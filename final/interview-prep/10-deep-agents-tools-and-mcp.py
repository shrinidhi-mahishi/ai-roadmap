"""
Deep Agents Tools, MCP & Ecosystem — Interview Prep Code Examples

Covers tools, MCP integration, and the ecosystem surface of LangChain
Deep Agents >= 0.7.x:
- MCP integration with MultiServerMCPClient (stateless and stateful sessions)
- MCP interceptors for audit logging and runtime context injection
- Production adapter runtime with circuit breakers and fallback chains
  (ACP/A2A -> direct invoke -> refuse)
- MCP gateway PEP with hash-pinned tool verification (CVE-2025-54136)

Source: 10-deep-agents-tools-and-mcp.md
"""

from __future__ import annotations
import hashlib, json, logging, random, re, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


# --- Section: Core Concepts & Algorithms > MCP Integration ---

from langchain_mcp_adapters.client import MultiServerMCPClient

async with MultiServerMCPClient({
    "github": {"transport": "http", "url": "http://localhost:8001/mcp"},
    "jira":   {"transport": "http", "url": "http://localhost:8002/mcp"},
}) as client:
    tools = await client.get_tools()
    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        tools=tools,
    )


# --- Section: Core Concepts & Algorithms > MCP Interceptors ---

@wrap_tool_call
def audit_all_tools(request, handler):
    log.info("Tool: %s, Args: %s", request["name"], request["args"])
    result = handler(request)
    log.info("Result size: %d bytes", len(str(result)))
    return result


# --- Section: Code Examples > MCP Integration with Interceptors ---

from langchain_mcp_adapters.client import MultiServerMCPClient

async with MultiServerMCPClient({
    "github": {"transport": "http", "url": "http://localhost:8001/mcp"},
    "jira":   {"transport": "http", "url": "http://localhost:8002/mcp"},
}) as client:
    tools = await client.get_tools()
    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        tools=tools,
    )


# --- Section: Code Examples > Stateful MCP Session ---

# For servers that maintain state across calls:
async with client.session("github") as session:
    tools = await load_mcp_tools(session)
    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        tools=tools,
    )


# --- Section: Code Examples > Production Adapter Runtime with Circuit Breakers ---

#!/usr/bin/env python3
"""Ecosystem adapters around one compiled Deep Agents graph.

Fallback: ACP/A2A surface -> direct graph.invoke -> deterministic refuse.
contextId is thread_id and MUST be a UUID. Never LocalShellBackend.
"""
from __future__ import annotations
import hashlib, json, logging, random, re, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

# --- UUID validation for A2A contextId ---
def require_thread_uuid(context_id: str) -> str:
    """A2A contextId == LangGraph thread_id. Non-UUID -> -32602 class error."""
    try:
        return str(uuid.UUID(context_id))
    except ValueError as exc:
        raise InvokeError("permanent", "a2a_-32602_invalid_thread_id") from exc

# --- Circuit breaker ---
class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    cooldown_s: float = 30.0
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0

    def allow(self):
        if self._state is CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = CircuitState.HALF_OPEN
            else:
                raise RuntimeError(f"circuit_open:{self.name}")

    def record_success(self):
        self._failures = 0; self._state = CircuitState.CLOSED

    def record_failure(self):
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

# --- Adapter fallback chain ---
class InvokeError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # "transient" | "permanent"

@dataclass
class AdapterRuntime:
    """Fallback: ACP/A2A -> direct invoke -> refuse."""
    a2a_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("a2a"))
    acp_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("acp"))
    direct_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("direct"))

    def run(self, user_text, *, context_id, tenant_id):
        thread_id = require_thread_uuid(context_id)
        # Try A2A first, then direct invoke, then refuse
        for breaker, surface in [
            (self.a2a_breaker, "a2a"),
            (self.direct_breaker, "direct"),
        ]:
            try:
                breaker.allow()
                result = self._invoke(surface, user_text, thread_id)
                breaker.record_success()
                return result
            except Exception:
                breaker.record_failure()
                continue
        return {"status": "refused", "reason": "all_surfaces_failed"}


# --- Section: Code Examples > MCP Gateway PEP (Hash-Pinned Tools) ---

@dataclass
class McpGateway:
    allowed_tools: set[str]
    surface_hash: dict[str, str]
    disabled: set[str] = field(default_factory=set)

    def _pin(self, name, description, schema):
        blob = json.dumps(
            {"name": name, "description": description, "inputSchema": schema},
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def call(self, name, args, *, description, schema):
        if name in self.disabled or name not in self.allowed_tools:
            return {"status": "error", "reason": "mcp_tool_disabled"}
        expected = self.surface_hash.get(name)
        got = self._pin(name, description, schema)
        if expected and got != expected:
            self.disabled.add(name)
            return {"status": "error", "reason": "mcp_hash_drift"}
        return self._execute_tool(name, args)
