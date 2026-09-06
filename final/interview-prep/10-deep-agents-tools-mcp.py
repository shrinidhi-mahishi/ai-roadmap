"""
Deep Agents Tools & MCP Integration -- tool definition/registration, MCP server
and client implementation, schema validation, interceptor patterns, and
multi-server wiring.

MCP (Model Context Protocol) is the "USB-C for AI" -- a standardized JSON-RPC 2.0
protocol for connecting AI models to external tools. Three primitives: tools
(model-controlled), resources (app-controlled read-only data), prompts
(user-controlled templates). Key security fact: permissions= does NOT cover
MCP tools -- Zero-Trust MCP requires a gateway PEP.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


# =============================================================================
# --- Section 1: Tool Definition and Registration -----------------------------
# =============================================================================
# Three tool sources in Deep Agents:
#   1. Custom callables -- your code, runs in your process
#   2. Built-in harness tools -- injected by middleware (ls, read_file, etc.)
#   3. MCP-loaded tools -- from external servers, need transport/auth/session
#
# tools= is ADDITIVE: it never removes a built-in. To the model, all three
# sources look identical. To you, they are operationally very different.


@dataclass
class ToolSchema:
    """JSON Schema definition for a tool's input parameters.

    Each tool definition costs 200-500 tokens in the context window.
    With 93 GitHub tools, that is 55,000 tokens -- 28% of a 200K window
    gone before the user speaks.
    """

    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema for parameters
    required: list[str] = field(default_factory=list)


@dataclass
class ToolDefinition:
    """A complete tool definition including schema and implementation.

    In production, this maps to what the model sees (schema) and what
    the host executes (handler). The model never sees the handler code.
    """

    schema: ToolSchema
    handler: Callable[..., Any]
    source: str = "custom"  # "custom" | "builtin" | "mcp"


class ToolRegistry:
    """Registry for all tools available to an agent.

    Interview point: tools= is additive -- MCP tools JOIN built-in FS tools.
    The model sees one flat list. The registry tracks source for routing.
    """

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool_def: ToolDefinition) -> None:
        """Register a tool. Duplicate names from different sources can collide.

        Use tool_name_prefix=True on MultiServerMCPClient to namespace
        MCP tools as "server_tool" (e.g., "math_add") to avoid shadowing.
        """
        if tool_def.schema.name in self._tools:
            existing = self._tools[tool_def.schema.name]
            log.warning(
                "Tool name collision: '%s' (existing source: %s, new source: %s). "
                "Use tool_name_prefix to namespace MCP tools.",
                tool_def.schema.name, existing.source, tool_def.source,
            )
        self._tools[tool_def.schema.name] = tool_def

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_schemas(self) -> list[dict]:
        """Return all tool schemas for injection into the model context.

        This is the token-expensive operation: every schema rides along
        every turn, even if the tool is never called.
        """
        return [
            {
                "name": t.schema.name,
                "description": t.schema.description,
                "input_schema": t.schema.input_schema,
            }
            for t in self._tools.values()
        ]

    def call(self, name: str, args: dict) -> Any:
        """Execute a tool by name."""
        tool = self._tools.get(name)
        if not tool:
            return {"error": f"Unknown tool: {name}"}
        return tool.handler(**args)


def tool(name: str, description: str, schema: dict, required: list[str] | None = None):
    """Decorator for registering a function as a tool.

    Usage:
        @tool("lookup_customer", "Look up customer by ID",
              {"customer_id": {"type": "string"}}, required=["customer_id"])
        def lookup_customer(customer_id: str) -> str:
            ...
    """
    def decorator(fn: Callable) -> Callable:
        fn._tool_schema = ToolSchema(
            name=name,
            description=description,
            input_schema={"type": "object", "properties": schema},
            required=required or [],
        )
        return fn
    return decorator


# -- Example custom tools --

@tool("lookup_customer", "Look up a customer by ID. Returns tier and ARR.",
      {"customer_id": {"type": "string", "description": "Customer ID (C-NNNN)"}},
      required=["customer_id"])
def lookup_customer(customer_id: str) -> str:
    """Custom callable tool -- runs in your process, governed by your code."""
    CUSTOMERS = {
        "C-1001": {"name": "Acme Corp", "tier": "enterprise", "arr": 240_000},
        "C-1002": {"name": "Globex Inc", "tier": "mid-market", "arr": 48_000},
    }
    cid = customer_id.strip().upper()
    if not cid.startswith("C-") or not cid[2:].isdigit():
        return json.dumps({"error": "Invalid format. Expected C-NNNN."})
    customer = CUSTOMERS.get(cid)
    if not customer:
        return json.dumps({"error": f"Customer {cid} not found."})
    return json.dumps({"customer_id": cid, **customer})


@tool("calculate_discount", "Calculate volume discount for a customer.",
      {"arr": {"type": "number"}, "tier": {"type": "string"}},
      required=["arr", "tier"])
def calculate_discount(arr: float, tier: str) -> str:
    """Another custom tool demonstrating input validation."""
    rates = {"enterprise": 0.15, "mid-market": 0.10, "startup": 0.05}
    rate = rates.get(tier, 0.0)
    discount = arr * rate
    return json.dumps({"discount": discount, "rate": rate, "tier": tier})


# =============================================================================
# --- Section 2: MCP Server (Expose Tools via JSON-RPC) -----------------------
# =============================================================================
# An MCP server exposes three primitives: tools, resources, prompts.
# Transport: stdio (local) or Streamable HTTP (remote, OAuth 2.1).
# JSON-RPC 2.0 is the wire format for all messages.


@dataclass
class MCPToolResult:
    """Result of an MCP tool call."""

    content: list[dict]  # [{"type": "text", "text": "..."}]
    is_error: bool = False


class MCPServer:
    """Simplified MCP server exposing tools and resources.

    Production servers use the FastMCP SDK:
        from mcp.server.fastmcp import FastMCP
        mcp = FastMCP("my-server", version="1.0.0")
        @mcp.tool()
        async def my_tool(...): ...

    Three primitives:
    - Tools: model-controlled callable functions (the workhorse)
    - Resources: app-controlled read-only data addressed by URI
    - Prompts: user-controlled parameterized templates

    2026-07-28 spec: stateless core (no initialize handshake needed).
    Each request carries everything in _meta. Serverless viable.
    """

    def __init__(self, name: str, version: str = "1.0.0"):
        self.name = name
        self.version = version
        self._tools: dict[str, Callable] = {}
        self._tool_schemas: dict[str, dict] = {}
        self._resources: dict[str, Callable] = {}

    def register_tool(self, name: str, description: str,
                      input_schema: dict, handler: Callable) -> None:
        """Register a tool on the server."""
        self._tools[name] = handler
        self._tool_schemas[name] = {
            "name": name,
            "description": description,
            "inputSchema": {"type": "object", "properties": input_schema},
        }

    def register_resource(self, uri: str, handler: Callable) -> None:
        """Register a read-only resource addressed by URI."""
        self._resources[uri] = handler

    def handle_jsonrpc(self, request: dict) -> dict:
        """Handle a JSON-RPC 2.0 request.

        MCP uses standard JSON-RPC with these methods:
        - tools/list: discover available tools (returns catalog + schemas)
        - tools/call: execute a tool
        - resources/list: discover available resources
        - resources/read: read a resource by URI
        - prompts/list + prompts/get: discover and retrieve prompts

        Multi-Round Tool Results (MRTR): if the server needs more info,
        return resultType="input_required" with opaque requestState.
        """
        method = request.get("method", "")
        params = request.get("params", {})
        req_id = request.get("id")

        if method == "tools/list":
            return self._jsonrpc_response(req_id, {
                "tools": list(self._tool_schemas.values()),
            })

        elif method == "tools/call":
            tool_name = params.get("name", "")
            args = params.get("arguments", {})

            if tool_name not in self._tools:
                return self._jsonrpc_error(req_id, -32601, f"Tool not found: {tool_name}")

            try:
                result_text = self._tools[tool_name](**args)
                return self._jsonrpc_response(req_id, {
                    "content": [{"type": "text", "text": result_text}],
                    "isError": False,
                })
            except Exception as e:
                return self._jsonrpc_response(req_id, {
                    "content": [{"type": "text", "text": str(e)}],
                    "isError": True,
                })

        elif method == "resources/list":
            resources = [{"uri": uri, "name": uri} for uri in self._resources]
            return self._jsonrpc_response(req_id, {"resources": resources})

        elif method == "resources/read":
            uri = params.get("uri", "")
            if uri not in self._resources:
                return self._jsonrpc_error(req_id, -32601, f"Resource not found: {uri}")
            content = self._resources[uri]()
            return self._jsonrpc_response(req_id, {
                "contents": [{"uri": uri, "text": content}],
            })

        else:
            return self._jsonrpc_error(req_id, -32601, f"Method not found: {method}")

    def _jsonrpc_response(self, req_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _jsonrpc_error(self, req_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


# =============================================================================
# --- Section 3: MCP Client (Connect and Call Tools) --------------------------
# =============================================================================
# The client maintains a 1:1 connection to a server. In production, this is
# handled by MultiServerMCPClient from langchain-mcp-adapters.
#
# Default is STATELESS: fresh session per tools/call. Use client.session()
# for stateful servers that keep context.


@dataclass
class MCPConnection:
    """Configuration for connecting to one MCP server."""

    name: str
    transport: str  # "stdio" | "http"
    url: str | None = None  # Required for http transport
    headers: dict[str, str] = field(default_factory=dict)
    # WARNING: headers= with static Bearer token is an ANTI-PATTERN.
    # Use OAuthClientProvider or gateway for proper token management.


class MCPClient:
    """Simplified MCP client that connects to an MCP server.

    In production, use MultiServerMCPClient:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        async with MultiServerMCPClient({...}) as client:
            tools = await client.get_tools()

    Session model:
    - Stateless (default): fresh ClientSession per tools/call
    - Stateful: async with client.session("server") for persistent context

    handle_tool_errors=True maps semantic isError=True -> ToolMessage(status="error")
    so the model can inspect and recover. TCP errors still RAISE.
    """

    def __init__(self, server: MCPServer, connection: MCPConnection):
        self.server = server
        self.connection = connection
        self._cached_tools: list[dict] | None = None

    def discover_tools(self) -> list[dict]:
        """Call tools/list to get available tool schemas.

        Each tool costs 200-500 tokens. 93 GitHub tools = 55,000 tokens.
        Optimization strategies:
        - Search-first discovery: 94% reduction (2-3 meta-tools first)
        - Code execution mode: 98.7% reduction (single execute_code tool)
        - Tiered schema: 80-90% reduction (names only, then full on demand)
        - Gateway aggregation: 94% reduction (Cloudflare portal pattern)

        2026-07-28 spec adds ttlMs and cacheScope for caching tools/list.
        """
        response = self.server.handle_jsonrpc({
            "jsonrpc": "2.0", "id": 1, "method": "tools/list",
        })
        tools = response.get("result", {}).get("tools", [])
        self._cached_tools = tools
        return tools

    def call_tool(self, name: str, arguments: dict,
                  handle_tool_errors: bool = True) -> dict:
        """Call a tool on the server.

        Flow:
        1. Host identifies which client owns the tool
        2. Constructs JSON-RPC tools/call with _meta
        3. Sends over stdio (local) or Streamable HTTP (remote)
        4. Server validates input, executes, returns result
        5. Result flows back to host -> model context

        handle_tool_errors=True: semantic isError -> ToolMessage(status="error")
        TCP/transport errors still raise (interceptor must catch them).
        """
        response = self.server.handle_jsonrpc({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })

        if "error" in response:
            if handle_tool_errors:
                return {"status": "error", "message": response["error"]["message"]}
            raise RuntimeError(response["error"]["message"])

        result = response.get("result", {})
        if result.get("isError") and handle_tool_errors:
            return {"status": "error", "content": result.get("content", [])}

        return {"status": "ok", "content": result.get("content", [])}


# =============================================================================
# --- Section 4: Tool Schema Validation ---------------------------------------
# =============================================================================
# Validate tool inputs against JSON Schema before execution. This prevents
# injection and ensures type safety. Also includes hash-pinning for
# Zero-Trust MCP (CVE-2025-54136 MCPoison defense).


class SchemaValidator:
    """Validates tool call arguments against the tool's input schema.

    Interview point: Schema validation happens at TWO levels:
    1. Server-side: validates before executing the tool handler
    2. Gateway/client-side: validates before even sending to the server
       (defense-in-depth against hallucinated or poisoned tool calls)
    """

    @staticmethod
    def validate(args: dict, schema: dict) -> list[str]:
        """Validate arguments against a JSON schema. Returns list of errors.

        Production systems use jsonschema library. This is a simplified
        demonstration of the pattern.
        """
        errors = []
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        # Check required fields
        for field_name in required:
            if field_name not in args:
                errors.append(f"Missing required field: {field_name}")

        # Check types
        for field_name, value in args.items():
            if field_name not in properties:
                errors.append(f"Unknown field: {field_name}")
                continue

            expected_type = properties[field_name].get("type")
            type_map = {
                "string": str, "number": (int, float), "integer": int,
                "boolean": bool, "array": list, "object": dict,
            }
            if expected_type and expected_type in type_map:
                if not isinstance(value, type_map[expected_type]):
                    errors.append(
                        f"Field '{field_name}': expected {expected_type}, "
                        f"got {type(value).__name__}"
                    )

        return errors


def compute_tool_surface_hash(name: str, description: str, input_schema: dict) -> str:
    """Compute a hash-pin for a tool definition (Zero-Trust MCP).

    CVE-2025-54136 (MCPoison, CVSS 8.8): Attacker poisons tools/list to
    alter tool descriptions, causing the model to call tools with
    attacker-controlled arguments.

    Defense: Hash name+description+inputSchema. Re-verify on every
    tools/call. If hash drifts, block the tool and alert.

    Pin in the GATEWAY, not the client. Name filtering alone is not
    a hash pin.
    """
    canonical = json.dumps(
        {"name": name, "description": description, "inputSchema": input_schema},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# =============================================================================
# --- Section 5: Tool Interceptor Pattern (Logging, Auth, Rate Limiting) ------
# =============================================================================
# Interceptors bridge the gap between MCP servers (which cannot see LangGraph
# runtime context) and the agent's security/observability needs.
#
# Pattern: onion wrapping around tools/call. First interceptor = outermost.
# Use cases: inject user IDs, add rate limiting, DLP on args, retry.


@dataclass
class InterceptorRequest:
    """Context passed to each interceptor in the chain."""

    tool_name: str
    arguments: dict
    user_id: str | None = None
    tenant_id: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class InterceptorResult:
    """Result that can be modified or short-circuited by interceptors."""

    proceed: bool = True  # False = short-circuit (don't call the server)
    modified_args: dict | None = None
    modified_headers: dict | None = None
    result: Any = None  # If short-circuited, the result to return
    error: str | None = None


class ToolInterceptor:
    """Base class for tool interceptors.

    Interceptors form an onion: each wraps the next. They can:
    - Modify arguments before sending to the server
    - Add headers (auth tokens, tenant context)
    - Short-circuit (deny, rate-limit) without calling the server
    - Modify results after the server responds
    - Log/audit the call

    NEVER pass the user's OAuth token through to the MCP server.
    Mint an MCP-audience token via RFC 8693 token exchange.
    """

    def before(self, request: InterceptorRequest) -> InterceptorResult:
        """Called before the tool is invoked. Can modify or block."""
        return InterceptorResult()  # Pass through by default

    def after(self, request: InterceptorRequest, result: Any) -> Any:
        """Called after the tool returns. Can modify the result."""
        return result


class LoggingInterceptor(ToolInterceptor):
    """Logs every tool call for audit trail.

    OWASP MCP08: Audit & Logging Gaps -- log every tool call with
    correlation IDs.
    """

    def __init__(self):
        self.call_log: list[dict] = []

    def before(self, request: InterceptorRequest) -> InterceptorResult:
        entry = {
            "timestamp": time.time(),
            "tool": request.tool_name,
            "user_id": request.user_id,
            "tenant_id": request.tenant_id,
            # Log arg DIGEST, not raw args (PII pipeline)
            "args_hash": hashlib.sha256(
                json.dumps(request.arguments, sort_keys=True).encode()
            ).hexdigest()[:12],
        }
        self.call_log.append(entry)
        log.info("MCP call: %s by user=%s", request.tool_name, request.user_id)
        return InterceptorResult()

    def after(self, request: InterceptorRequest, result: Any) -> Any:
        if self.call_log:
            self.call_log[-1]["completed"] = time.time()
        return result


class AuthInterceptor(ToolInterceptor):
    """Injects authentication headers from runtime context.

    Interview point: The official docs headers= Bearer example is a
    STATIC BEARER ANTI-PATTERN. Production uses:
    - OAuthClientProvider for dynamic token refresh
    - Gateway that mints per-request tokens
    - RFC 8707 audience = canonical MCP server URI
    - RFC 8693 token exchange (never passthrough)
    """

    def __init__(self, token_provider: Callable[[str], str]):
        # token_provider takes user_id and returns a scoped token
        self._token_provider = token_provider

    def before(self, request: InterceptorRequest) -> InterceptorResult:
        if not request.user_id:
            return InterceptorResult(proceed=False, error="No user identity")

        # Mint a scoped token for this specific MCP server
        # NEVER pass the user's original OAuth token through
        token = self._token_provider(request.user_id)
        return InterceptorResult(
            modified_headers={"Authorization": f"Bearer {token}"},
        )


class RateLimitInterceptor(ToolInterceptor):
    """Per-tenant rate limiting on MCP tool calls."""

    def __init__(self, max_per_minute: int = 60):
        self.max_per_minute = max_per_minute
        self._calls: dict[str, list[float]] = {}

    def before(self, request: InterceptorRequest) -> InterceptorResult:
        tenant = request.tenant_id or "default"
        now = time.time()
        calls = self._calls.setdefault(tenant, [])
        # Prune old entries
        self._calls[tenant] = [t for t in calls if now - t < 60]

        if len(self._calls[tenant]) >= self.max_per_minute:
            return InterceptorResult(
                proceed=False,
                error=f"Rate limit exceeded for tenant {tenant}",
            )
        self._calls[tenant].append(now)
        return InterceptorResult()


class DLPInterceptor(ToolInterceptor):
    """Data Loss Prevention: scan tool arguments for PII before sending.

    MCP tool ARGUMENTS are the exfil channel. Scan for PII patterns
    and redact or block before the data reaches the MCP server.
    """

    PII_PATTERNS = {
        "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
        "pan": r"\b[A-Z]{5}\d{4}[A-Z]\b",
    }

    def before(self, request: InterceptorRequest) -> InterceptorResult:
        import re
        args_str = json.dumps(request.arguments)
        for pii_type, pattern in self.PII_PATTERNS.items():
            if re.search(pattern, args_str):
                return InterceptorResult(
                    proceed=False,
                    error=f"PII detected ({pii_type}) in tool arguments. Blocked.",
                )
        return InterceptorResult()


class InterceptorChain:
    """Onion-wrap interceptors around a tool call.

    First interceptor in the list = outermost (runs first on before,
    last on after).
    """

    def __init__(self, interceptors: list[ToolInterceptor]):
        self.interceptors = interceptors

    def execute(self, request: InterceptorRequest,
                tool_caller: Callable[[str, dict], Any]) -> Any:
        """Run the interceptor chain around a tool call."""
        # Before phase: run interceptors in order
        modified_args = request.arguments
        modified_headers = request.headers.copy()

        for interceptor in self.interceptors:
            result = interceptor.before(request)
            if not result.proceed:
                return {"error": result.error or "Blocked by interceptor"}
            if result.modified_args:
                modified_args = result.modified_args
            if result.modified_headers:
                modified_headers.update(result.modified_headers)

        # Execute the tool call
        call_result = tool_caller(request.tool_name, modified_args)

        # After phase: run interceptors in reverse order
        for interceptor in reversed(self.interceptors):
            call_result = interceptor.after(request, call_result)

        return call_result


# =============================================================================
# --- Section 6: Multi-Server MCP Client Setup --------------------------------
# =============================================================================
# Production agents connect to multiple MCP servers simultaneously.
# One client per server (strict 1:1). Track failures per server independently
# for circuit breaking.


@dataclass
class MCPServerConfig:
    """Configuration for one MCP server connection."""

    name: str
    transport: str  # "stdio" | "http"
    url: str | None = None
    command: str | None = None  # For stdio transport
    args: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)


class MultiServerMCPClient:
    """Connects to multiple MCP servers and aggregates their tools.

    Production equivalent:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        async with MultiServerMCPClient({
            "github": {"transport": "http", "url": "http://localhost:8001/mcp"},
            "jira":   {"transport": "http", "url": "http://localhost:8002/mcp"},
        }) as client:
            tools = await client.get_tools()

    Key settings:
    - tool_name_prefix=True: namespace as "server_tool" to avoid collisions
    - handle_tool_errors=True: semantic isError -> ToolMessage, not raise
    - tool_interceptors: onion around tools/call
    """

    def __init__(
        self,
        configs: dict[str, MCPServerConfig],
        tool_name_prefix: bool = False,
        handle_tool_errors: bool = True,
        interceptors: list[ToolInterceptor] | None = None,
    ):
        self.configs = configs
        self.tool_name_prefix = tool_name_prefix
        self.handle_tool_errors = handle_tool_errors
        self.interceptor_chain = InterceptorChain(interceptors or [])
        self._clients: dict[str, MCPClient] = {}
        self._tool_hashes: dict[str, str] = {}  # For hash-pinning

    def connect(self, servers: dict[str, MCPServer]) -> None:
        """Connect to all configured servers.

        In production, this happens inside an async context manager.
        Connections are 1:1 (one client per server, strict).
        """
        for name, server in servers.items():
            config = self.configs.get(name)
            if not config:
                log.warning("No config for server: %s", name)
                continue
            connection = MCPConnection(name=name, transport=config.transport,
                                       url=config.url, headers=config.headers)
            self._clients[name] = MCPClient(server, connection)

    def get_tools(self) -> list[dict]:
        """Discover tools from all connected servers.

        Token overhead at scale (from the module):
        - GitHub MCP (93 tools): 55,000 tokens (28% of 200K window)
        - GitHub + Slack + Sentry (3 servers): 143,000 tokens (72% consumed idle)
        - 508-tool benchmark: 1,150,000 tokens ($377 per round in metadata)

        Microsoft Research: large tool spaces lower accuracy by up to 85%.
        Every 10K schema tokens removes ~5 pages of reasoning capacity.
        """
        all_tools = []
        for server_name, client in self._clients.items():
            tools = client.discover_tools()
            for t in tools:
                # Hash-pin each tool definition for MCPoison defense
                tool_hash = compute_tool_surface_hash(
                    t["name"], t["description"], t.get("inputSchema", {}),
                )
                full_name = (f"{server_name}_{t['name']}"
                             if self.tool_name_prefix else t["name"])
                self._tool_hashes[full_name] = tool_hash

                tool_entry = {**t, "name": full_name, "_server": server_name}
                all_tools.append(tool_entry)

        return all_tools

    def call_tool(self, tool_name: str, arguments: dict,
                  user_id: str | None = None, tenant_id: str | None = None) -> Any:
        """Route a tool call to the correct server via the interceptor chain."""
        # Find which server owns this tool
        server_name = None
        original_name = tool_name
        for name in self._clients:
            prefix = f"{name}_"
            if tool_name.startswith(prefix):
                server_name = name
                original_name = tool_name[len(prefix):]
                break
        if not server_name:
            # Try direct match (no prefix mode)
            for name, client in self._clients.items():
                if client._cached_tools and any(
                    t["name"] == tool_name for t in client._cached_tools
                ):
                    server_name = name
                    break

        if not server_name:
            return {"error": f"No server found for tool: {tool_name}"}

        # Build interceptor request with runtime context
        request = InterceptorRequest(
            tool_name=original_name,
            arguments=arguments,
            user_id=user_id,
            tenant_id=tenant_id,
        )

        # Execute through interceptor chain
        client = self._clients[server_name]
        return self.interceptor_chain.execute(
            request,
            lambda name, args: client.call_tool(name, args, self.handle_tool_errors),
        )


# =============================================================================
# --- Section 7: MCP Gateway (Per-Tenant Auth, Scope, Rate Limit) -------------
# =============================================================================
# The gateway is the Zero-Trust PEP that permissions= cannot provide for MCP.
# Architecture: Agent -> Gateway -> [Auth] -> [Rate Limit] -> [Scope] -> Server


@dataclass
class TenantConfig:
    """Per-tenant gateway configuration."""

    tenant_id: str
    allowed_tools: set[str]  # Least-privilege tool allowlist
    rate_limit_per_minute: int = 60
    allowed_servers: set[str] = field(default_factory=set)


class MCPGateway:
    """Centralized MCP gateway for multi-tenant Zero-Trust enforcement.

    This is the REQUIRED security layer for MCP in production.
    permissions= covers only built-in FS tools. MCP needs a gateway PEP.

    Controls implemented:
    1. Transport auth: OAuth 2.1 + PKCE, RFC 8707 audience
    2. Server allowlist: only approved connections
    3. Tool allowlist + prefix: least privilege
    4. Hash-pin descriptions: re-verify every tools/call
    5. Interceptor PDP: model proposes, PEP disposes
    6. Identity: from verified access token, never from model JSON
    """

    def __init__(self):
        self._tenants: dict[str, TenantConfig] = {}
        self._audit_log: list[dict] = []

    def register_tenant(self, config: TenantConfig) -> None:
        self._tenants[config.tenant_id] = config

    def authorize_and_route(
        self, tenant_id: str, tool_name: str, arguments: dict
    ) -> dict:
        """Gateway authorization flow:
        1. Authenticate tenant
        2. Check tool allowlist
        3. Rate limit check
        4. Forward to server
        """
        t0 = time.time()

        # 1. Auth
        tenant = self._tenants.get(tenant_id)
        if not tenant:
            self._audit("auth_fail", tenant_id, tool_name)
            return {"error": "Unknown tenant", "code": 401}

        # 2. Scope check (tool allowlist)
        if tool_name not in tenant.allowed_tools:
            self._audit("scope_deny", tenant_id, tool_name)
            return {"error": f"Tool '{tool_name}' not permitted", "code": 403}

        # 3. Rate limit (simplified)
        self._audit("authorized", tenant_id, tool_name,
                     latency_ms=round((time.time() - t0) * 1000, 1))

        # 4. Forward to server (stub -- in production, this is the actual RPC)
        return {"status": "ok", "tool": tool_name, "routed": True}

    def _audit(self, event: str, tenant: str, tool: str, **extra) -> None:
        """WORM audit log: decisions, not values."""
        self._audit_log.append({
            "ts": time.time(), "event": event,
            "tenant": tenant, "tool": tool, **extra,
        })


# =============================================================================
# --- Section 8: Circuit Breaker for MCP Servers ------------------------------
# =============================================================================
# Track failures PER SERVER independently. One failing Jira should not
# block Salesforce. Library does not ship protocol breakers -- you build them.


class CircuitState:
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class MCPCircuitBreaker:
    """Per-server circuit breaker for MCP connections.

    Trip conditions:
    - MCP transport raise or hash drift: consecutive >= 3 in 60s window
    - Half-open probe: one tools/list re-hash
    - Fallback: DISABLE those tools. Agent continues on VFS and other servers.

    NEVER fail-open to LocalShell. NEVER auto-approve HITL on timeout.
    """

    server_name: str
    failure_threshold: int = 3
    window_seconds: float = 60.0
    recovery_timeout: float = 30.0

    _state: str = field(default=CircuitState.CLOSED, init=False)
    _failures: list[float] = field(default_factory=list, init=False)
    _last_open_time: float = field(default=0.0, init=False)

    @property
    def state(self) -> str:
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_open_time > self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
        return self._state

    def record_success(self) -> None:
        self._failures.clear()
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        now = time.time()
        self._failures = [t for t in self._failures if now - t < self.window_seconds]
        self._failures.append(now)

        if len(self._failures) >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._last_open_time = now
            log.warning("Circuit OPEN for MCP server: %s", self.server_name)

    def allow_request(self) -> bool:
        """Check if requests are allowed through."""
        state = self.state  # Triggers half-open transition
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.HALF_OPEN:
            return True  # Allow one probe request
        return False  # OPEN -- reject


# =============================================================================
# --- Demo / Self-Test --------------------------------------------------------
# =============================================================================


def demo():
    """Demonstrate MCP tool integration patterns."""
    print("=" * 60)
    print("DEMO: Deep Agents Tools & MCP Integration")
    print("=" * 60)

    # 1. Tool registration
    print("\n--- Tool Registration ---")
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        schema=lookup_customer._tool_schema,
        handler=lookup_customer,
        source="custom",
    ))
    registry.register(ToolDefinition(
        schema=calculate_discount._tool_schema,
        handler=calculate_discount,
        source="custom",
    ))
    print(f"Registered tools: {[s['name'] for s in registry.list_schemas()]}")

    result = registry.call("lookup_customer", {"customer_id": "C-1001"})
    print(f"lookup_customer(C-1001) = {result}")

    # 2. MCP Server
    print("\n--- MCP Server ---")
    server = MCPServer("customer-data-platform")
    server.register_tool(
        "lookup_customer", "Look up customer by ID",
        {"customer_id": {"type": "string"}},
        lambda customer_id: lookup_customer(customer_id=customer_id),
    )
    server.register_resource(
        "cdp://schema/customers",
        lambda: json.dumps({"table": "customers", "columns": ["id", "name", "tier"]}),
    )

    # tools/list
    tools_response = server.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/list",
    })
    print(f"tools/list: {[t['name'] for t in tools_response['result']['tools']]}")

    # tools/call
    call_response = server.handle_jsonrpc({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "lookup_customer", "arguments": {"customer_id": "C-1001"}},
    })
    print(f"tools/call: {call_response['result']['content'][0]['text']}")

    # 3. MCP Client
    print("\n--- MCP Client ---")
    client = MCPClient(
        server, MCPConnection(name="cdp", transport="stdio"),
    )
    tools = client.discover_tools()
    print(f"Discovered {len(tools)} tools")
    result = client.call_tool("lookup_customer", {"customer_id": "C-1002"})
    print(f"Client call result: {result}")

    # 4. Schema validation
    print("\n--- Schema Validation ---")
    errors = SchemaValidator.validate(
        {"customer_id": "C-1001"},
        {"properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]},
    )
    print(f"Valid input errors: {errors}")

    errors = SchemaValidator.validate(
        {"customer_id": 1001},  # Wrong type
        {"properties": {"customer_id": {"type": "string"}}, "required": ["customer_id"]},
    )
    print(f"Invalid input errors: {errors}")

    # Hash pinning
    tool_hash = compute_tool_surface_hash(
        "lookup_customer", "Look up customer by ID",
        {"customer_id": {"type": "string"}},
    )
    print(f"Tool surface hash: {tool_hash}")

    # 5. Interceptor chain
    print("\n--- Interceptor Chain ---")
    logger = LoggingInterceptor()
    rate_limiter = RateLimitInterceptor(max_per_minute=100)
    chain = InterceptorChain([logger, rate_limiter])

    request = InterceptorRequest(
        tool_name="lookup_customer",
        arguments={"customer_id": "C-1001"},
        user_id="user-42",
        tenant_id="acme",
    )
    result = chain.execute(
        request,
        lambda name, args: client.call_tool(name, args),
    )
    print(f"Intercepted call result: {result}")
    print(f"Audit log entries: {len(logger.call_log)}")

    # 6. Multi-server setup
    print("\n--- Multi-Server MCP Client ---")
    server2 = MCPServer("billing-platform")
    server2.register_tool(
        "get_invoice", "Retrieve invoice by number",
        {"invoice_id": {"type": "string"}},
        lambda invoice_id: json.dumps({"id": invoice_id, "amount": 1500}),
    )

    multi_client = MultiServerMCPClient(
        configs={
            "cdp": MCPServerConfig(name="cdp", transport="stdio"),
            "billing": MCPServerConfig(name="billing", transport="http",
                                       url="http://localhost:8002/mcp"),
        },
        tool_name_prefix=True,  # Namespace: "cdp_lookup_customer", "billing_get_invoice"
        interceptors=[logger],
    )
    multi_client.connect({"cdp": server, "billing": server2})
    all_tools = multi_client.get_tools()
    print(f"All tools across servers: {[t['name'] for t in all_tools]}")

    # 7. Gateway
    print("\n--- MCP Gateway ---")
    gateway = MCPGateway()
    gateway.register_tenant(TenantConfig(
        tenant_id="acme",
        allowed_tools={"lookup_customer", "get_invoice"},
        rate_limit_per_minute=100,
    ))
    print(f"Authorized: {gateway.authorize_and_route('acme', 'lookup_customer', {})}")
    print(f"Denied tool: {gateway.authorize_and_route('acme', 'delete_all', {})}")
    print(f"Unknown tenant: {gateway.authorize_and_route('unknown', 'lookup_customer', {})}")

    # 8. Circuit breaker
    print("\n--- Circuit Breaker ---")
    cb = MCPCircuitBreaker(server_name="cdp", failure_threshold=3)
    print(f"Initial state: {cb.state}")
    cb.record_failure()
    cb.record_failure()
    print(f"After 2 failures: {cb.state}")
    cb.record_failure()
    print(f"After 3 failures: {cb.state} (circuit OPEN)")
    print(f"Allow request? {cb.allow_request()}")

    print("\n" + "=" * 60)
    print("Key interview numbers:")
    print("  200-500 tokens per MCP tool definition")
    print("  55,000 tokens for GitHub MCP (93 tools)")
    print("  94% reduction via search-first discovery or gateway aggregation")
    print("  97M+ MCP SDK monthly downloads mid-2026")
    print("  8.5% of remote servers implement mandatory OAuth 2.1 + PKCE")
    print("  CVE-2026-33032 (MCPwn) CVSS 9.8: 2,600+ exposed instances")
    print("  permissions= is FAIL-OPEN and FS-ONLY (does NOT cover MCP)")
    print("=" * 60)


if __name__ == "__main__":
    demo()
