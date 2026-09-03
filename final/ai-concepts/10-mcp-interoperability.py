"""
MCP (Model Context Protocol) & Interoperability: the open standard for
connecting AI apps to external tools and data. Covers tool/resource
definitions in JSON-RPC format, server/client skeletons, and transport framing.
"""

from __future__ import annotations
import json
import uuid
from dataclasses import dataclass
from typing import Any


# ═══ Section 1: MCP Tool Definition (JSON-RPC Format) ═══

def create_tool_definition(name: str, description: str,
                           properties: dict, required: list[str]) -> dict:
    """Build an MCP tool definition.
    inputSchema MUST be JSON Schema 2020-12. Names: 1-128 chars, [A-Za-z0-9_.-].
    Each tool costs 550-1,400 tokens in context (the 'Tools Tax')."""
    return {
        "name": name, "description": description,
        "inputSchema": {"type": "object", "properties": properties,
                        "required": required, "additionalProperties": False},
    }


def demo_tool_definitions():
    weather = create_tool_definition(
        "get_weather", "Get current weather for a city.",
        {"city": {"type": "string", "description": "City name"},
         "units": {"type": "string", "enum": ["celsius", "fahrenheit"]}},
        ["city"],
    )
    print("Weather tool:", json.dumps(weather, indent=2))


# ═══ Section 2: MCP Resource Definition (URI-Based) ═══

@dataclass
class MCPResource:
    """Resources are URI-identified context (RFC 3986), not actions.
    Key: the APPLICATION decides when to attach resources, not the model.
    Discovery: resources/list. Invocation: resources/read (text or base64)."""
    uri: str
    name: str
    description: str
    mime_type: str = "text/plain"

    def to_dict(self) -> dict:
        return {"uri": self.uri, "name": self.name,
                "description": self.description, "mimeType": self.mime_type}


def demo_resources():
    readme = MCPResource("file:///workspace/README.md", "Project README",
                         "Main docs", "text/markdown")
    template = {"uriTemplate": "db://users/{user_id}/profile",
                "name": "User Profile", "description": "Per-user profile data"}
    print("Static resource:", json.dumps(readme.to_dict()))
    print("URI template:", json.dumps(template))


# ═══ Section 3: MCP Server Skeleton ═══

class MCPServer:
    """Minimal MCP server handling core JSON-RPC methods.
    2026-07-28 redesign: stateless (no initialize handshake, no session ID).
    Every request self-describes. Cross-call state = explicit handles."""

    def __init__(self, name: str, version: str = "1.0.0"):
        self.name, self.version = name, version
        self.tools: list[dict] = []
        self.resources: list[MCPResource] = []
        self._handlers: dict[str, Any] = {}

    def register_tool(self, tool_def: dict, handler: Any):
        self.tools.append(tool_def)
        self._handlers[tool_def["name"]] = handler

    def register_resource(self, resource: MCPResource):
        self.resources.append(resource)

    def handle_request(self, request: dict) -> dict:
        """Route JSON-RPC 2.0 request. Protocol errors use -32602.
        Business failures return isError:true (model can self-correct)."""
        method, req_id = request.get("method", ""), request.get("id")
        params = request.get("params", {})

        if method == "server/discover":
            return self._ok(req_id, {"serverInfo": {"name": self.name},
                                      "capabilities": {"tools": {"listChanged": True}}})
        elif method == "tools/list":
            return self._ok(req_id, {"tools": self.tools})
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments", {})
            if name not in self._handlers:
                return self._err(req_id, -32602, f"Unknown tool: {name}")
            try:
                result = self._handlers[name](**args)
                return self._ok(req_id, {"content": [{"type": "text", "text": str(result)}]})
            except Exception as e:
                return self._ok(req_id, {"content": [{"type": "text", "text": str(e)}],
                                         "isError": True})
        elif method == "resources/list":
            return self._ok(req_id, {"resources": [r.to_dict() for r in self.resources]})
        return self._err(req_id, -32601, f"Method not found: {method}")

    def _ok(self, rid, result):
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def _err(self, rid, code, msg):
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}


def demo_server():
    server = MCPServer("weather-service")
    server.register_tool(
        create_tool_definition("get_weather", "Get weather.", {"city": {"type": "string"}}, ["city"]),
        lambda city: f"22C and sunny in {city}",
    )
    server.register_resource(MCPResource("file:///data/cities.json", "Cities", "All cities"))

    for req in [
        {"jsonrpc": "2.0", "id": 1, "method": "server/discover"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "get_weather", "arguments": {"city": "Tokyo"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "unknown_tool", "arguments": {}}},
    ]:
        resp = server.handle_request(req)
        err = resp.get("error")
        print(f"  {req['method']} -> {'ERROR: ' + err['message'] if err else json.dumps(resp['result'])[:90]}")


# ═══ Section 4: MCP Client (Discover and Call Tools) ═══

class MCPClient:
    """Client that connects to one MCP server (1:1 mapping).
    The host manages N clients. Model never speaks JSON-RPC -- client translates."""

    def __init__(self, server: MCPServer):
        self.server = server
        self.available_tools: list[dict] = []

    def discover(self) -> dict:
        return self.server.handle_request(
            {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "server/discover"}
        ).get("result", {})

    def list_tools(self) -> list[dict]:
        resp = self.server.handle_request(
            {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/list"})
        self.available_tools = resp.get("result", {}).get("tools", [])
        return self.available_tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.server.handle_request(
            {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "tools/call",
             "params": {"name": name, "arguments": arguments}}
        ).get("result", {})


def demo_client():
    server = MCPServer("calc")
    server.register_tool(
        create_tool_definition("add", "Add two numbers.",
                               {"a": {"type": "number"}, "b": {"type": "number"}}, ["a", "b"]),
        lambda a, b: a + b,
    )
    client = MCPClient(server)
    info = client.discover()
    print(f"Connected to: {info['serverInfo']['name']}")
    tools = client.list_tools()
    print(f"Tools: {[t['name'] for t in tools]}")
    result = client.call_tool("add", {"a": 17, "b": 25})
    print(f"add(17, 25) = {result['content'][0]['text']}")


# ═══ Section 5: Transport Layer (stdio vs Streamable HTTP) ═══

def format_stdio_message(msg: dict) -> str:
    """stdio: newline-delimited JSON-RPC on stdin/stdout. MUST NOT embed newlines.
    Near-zero overhead but single-client-per-process, no built-in auth."""
    return json.dumps(msg, separators=(",", ":")) + "\n"


def format_http_request(msg: dict) -> dict:
    """Streamable HTTP: POST to one endpoint. Required headers let gateways
    route without parsing the body. Supports OAuth, multi-tenancy, scaling."""
    return {
        "method": "POST", "url": "https://example.com/mcp",
        "headers": {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": msg.get("method", ""),
            "Mcp-Name": msg.get("params", {}).get("name", ""),
        },
        "body": json.dumps(msg),
    }


def demo_transports():
    msg = {"jsonrpc": "2.0", "id": "x", "method": "tools/call",
           "params": {"name": "get_weather", "arguments": {"city": "London"}}}
    print("STDIO:", format_stdio_message(msg).strip()[:80])
    http = format_http_request(msg)
    print(f"HTTP:  {http['method']} {http['url']}")
    for k, v in http["headers"].items():
        print(f"  {k}: {v}")
    print("\n  stdio  -> local, no auth, single client, near-zero latency")
    print("  HTTP   -> remote, OAuth 2.1, multi-tenant, round-robin replicas")


# ═══ Main ═══

if __name__ == "__main__":
    print("=" * 60)
    print("MCP & INTEROPERABILITY -- Interview Prep Demos")
    print("=" * 60)

    print("\n--- 1. MCP Tool Definition ---")
    demo_tool_definitions()

    print("\n--- 2. MCP Resource Definition ---")
    demo_resources()

    print("\n--- 3. MCP Server Skeleton ---")
    demo_server()

    print("\n--- 4. MCP Client (Discover + Call) ---")
    demo_client()

    print("\n--- 5. Transport Layer (stdio vs HTTP) ---")
    demo_transports()

    print("\n" + "=" * 60)
    print("Key takeaways:")
    print("  - Three primitives: Tools (model), Resources (app), Prompts (user)")
    print("  - Model NEVER speaks JSON-RPC -- client translates")
    print("  - 2026-07-28: stateless, no session ID, any replica")
    print("  - Tools Tax: 550-1,400 tokens/tool; ~30-40 always-loaded max")
    print("  - Business errors use isError:true, not JSON-RPC error codes")
