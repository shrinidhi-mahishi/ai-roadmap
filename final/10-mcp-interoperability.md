# Module 10: MCP & Interoperability

## What Is This?

**MCP (Model Context Protocol)** is an open standard for connecting AI applications to external tools and data sources. Think of it as **USB-C for AI** -- before MCP, every AI app needed custom integration code for every tool (like the old days of different phone chargers). MCP provides one standard connector that works everywhere.

MCP has three core building blocks:
- **Tools**: Actions the model can invoke -- like "search the web," "query a database," or "send an email." The model decides when to call them.
- **Resources**: Data the model can read -- like files, database records, or API responses. Think of them as "read-only data sources" the model can access.
- **Prompts**: Pre-built prompt templates that the user (not the model) selects -- like "summarize this document" or "review this code."

The architecture has three roles:
- **Host**: The AI application (e.g., Claude Desktop, Cursor, your custom app)
- **Client**: A connector inside the host that speaks the MCP protocol
- **Server**: An external process that exposes tools/resources (e.g., a GitHub MCP server, a Postgres MCP server)

The model never speaks MCP directly -- it generates a regular function call, and the client translates that into an MCP request to the right server.

## Why It Matters

MCP is rapidly becoming the standard for tool integration. Instead of building custom integrations for every data source your agent needs, you connect to existing MCP servers. The ecosystem already has servers for GitHub, Slack, databases, file systems, and hundreds more.

---

## 2. Core Concepts

### Host, Client, Server -- Three Roles, Not Two

MCP is a **three-role** topology, not a simple RPC. Understanding this distinction is critical for interviews.

| Role | What It Is | Cardinality |
|------|-----------|-------------|
| **Host** | The AI application the user sees (Claude Desktop, Cursor, VS Code, ChatGPT, a custom agent runtime). Owns UX, consent, tool-approval policy, multi-server orchestration, and the LLM conversation. | 1 per user session |
| **Client** | A protocol connector **inside** the host. One client maps to one server. Translates `tools/list` into the model's native tool schema. | N per host |
| **Server** | A process exposing tools, resources, and prompts. Local (stdio) or remote (Streamable HTTP). | 1 per connection from a given client; remote servers multiplex many clients |

**The model never speaks MCP.** It emits native function/tool calls; the host's client translates to `tools/call` JSON-RPC. This is the invariant that makes the whole system work -- the protocol is between infrastructure components, not between the model and the tool.

```
  +---------------------------+
  |          HOST              |
  |  (Claude / Cursor / etc)   |
  |                            |
  |  +--------+  +--------+   |
  |  | Client |  | Client |   |     Each client <-> one server
  |  +---+----+  +---+----+   |
  +------|-----------|--------+
         |           |
    +----v----+  +---v-----+
    | Server  |  | Server  |      Local (stdio) or Remote (HTTP)
    | (GitHub)|  | (Slack) |
    +---------+  +---------+
```

**Provider-hosted MCP inversion**: Anthropic's Messages API MCP connector and OpenAI's Responses `type: "mcp"` invert the topology -- the provider becomes the MCP client, and your app never opens a socket to the MCP server.

### The Three Primitives

Who decides invocation is the key differentiator:

| Primitive | Discovery | Invocation | Who Decides |
|-----------|-----------|------------|-------------|
| **Tools** | `tools/list` | `tools/call` | **Model** decides (function calling) |
| **Resources** | `resources/list`, `resources/templates/list` | `resources/read` | **Application** decides (host picks what to attach) |
| **Prompts** | `prompts/list` | `prompts/get` | **User** decides (slash commands, templates) |

Reverse-direction primitives: **Sampling** (`sampling/createMessage` -- server asks client's LLM for a completion; deprecated) and **Elicitation** (server asks client for structured user input via forms or URLs).

### Stateless Core (2026-07-28 Redesign)

The `initialize`/`initialized` handshake and `Mcp-Session-Id` are retired. Every request is self-describing. This was not a minor cleanup -- Google and Cloudflare both published detailed accounts of hitting a "hard wall" scaling MCP because the old protocol pinned clients to specific pods via session IDs, forcing sticky-session load balancing, complex drain-on-deploy logic, and broken sessions on autoscale/restart.

Now: any request can land on any replica behind round-robin. No shared session store required. Cross-call state = explicit handles in tool arguments (cart id, browser context id).

---

## 3. How It Works

### 3.1 Transports

**stdio**: Host launches server as a subprocess. Newline-delimited JSON-RPC on stdin/stdout (MUST NOT embed newlines in a message). stderr is logging only. No HTTP headers -- `_meta` lives in the JSON body. Shutdown: close stdin, wait, then SIGTERM->SIGKILL (POSIX). Near-zero network overhead but single-client-per-process, no built-in auth. Best for local IDE tools.

**Streamable HTTP**: One MCP endpoint (e.g. `https://example.com/mcp`) accepting POST. Client sends `Accept: application/json, text/event-stream`. Server replies with either a single JSON object or an SSE stream scoped to that request (progress notifications, then result). Required headers: `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name` (SEP-2243) -- these let gateways route without parsing the body. Cancellation = close the SSE stream. Supports horizontal scaling, OAuth, multi-tenancy.

**Deprecated HTTP+SSE (2024-11-05)**: Two separate endpoints (GET for SSE stream, POST for messages). 12-month minimum offramp. Cursor still documents it; OpenAI still accepts it. New builds must not use it.

**Key transport rules:**
- Validate `Origin` header or return 403 (DNS rebinding defense)
- Local servers SHOULD bind `127.0.0.1`, not `0.0.0.0`
- Send `X-Accel-Buffering: no` and SSE comment keep-alives so nginx/CDNs don't buffer or idle-timeout
- `Last-Event-ID` / resumable SSE is NOT supported in this revision

**Throughput benchmarks (ToolHive on Kubernetes):** Streamable HTTP with shared sessions sustains **290-300 req/s** vs only **30-36 req/s with unique sessions per request** -- a ~10x difference purely from session-pooling strategy.

### 3.2 JSON-RPC 2.0 Message Contract

Requests: `id` + `method` + `params`. Notifications: no `id`. Protocol errors use JSON-RPC codes (`-32602` for invalid params / unknown tool / resource not found; `-32603` internal). Tool **business** failures are NOT JSON-RPC errors -- they are successful results with `isError: true` so the model can self-correct.

### 3.3 Capability Negotiation

Every request MUST carry `_meta.io.modelcontextprotocol/protocolVersion` and SHOULD carry `clientInfo` + `clientCapabilities`. Servers advertise via `server/discover` (cacheable with `ttlMs` and `cacheScope`). Version probing: send `server/discover`; on `DiscoverResult` stay modern; on `UnsupportedProtocolVersionError` pick from `supported`; on timeout/error try legacy `initialize`.

Key client capabilities:
- `elicitation.form` / `elicitation.url` -- empty `elicitation: {}` = form-only (compat)
- `sampling` / `sampling.tools` -- **deprecated** but still on the 12-month clock
- `extensions`: Tasks, Enterprise Managed Authorization, MCP Apps

Server capabilities: `tools.listChanged`, `resources.listChanged` / `resources.subscribe`, `prompts`, extensions. Tool lists MUST NOT vary as a side effect of other requests; they MAY vary by authorization presented.

### 3.4 Tools (Model-Controlled)

**Schema.** `inputSchema` MUST be a JSON Schema object; default dialect **JSON Schema 2020-12** if `$schema` is omitted. Optional `outputSchema` -- if present, `structuredContent` MUST conform; clients SHOULD validate. Dual-write: structured results SHOULD also appear as serialized JSON in a `text` content block for older hosts. Parameterless tools: `{ "type": "object", "additionalProperties": false }`.

**Names.** 1-128 chars; `[A-Za-z0-9_.-]`; case-sensitive; unique per server. Aggregators SHOULD prefix with a client-assigned server id (not `serverInfo.name`, which isn't globally unique).

**Call result types.** `resultType: "complete"` (normal / `isError`); `"input_required"` (MRTR); `"task"` (Tasks extension). Content types: `text`, `image`, `audio`, `resource_link`, embedded `resource`.

**`x-mcp-header`.** Primitive params (string/integer/boolean) mirrored to `Mcp-Param-{name}` HTTP headers for WAF/LB routing. MUST NOT put secrets/PII there.

**Annotations.** Optional behavior hints. Clients MUST treat annotations as untrusted unless the server is trusted. HITL: hosts SHOULD confirm invocations -- tools are arbitrary code execution.

**Tool list requirements:** Lists are paginated, cacheable, SHOULD be deterministically ordered to stabilize LLM prompt caches (SEP-2549).

### 3.5 Resources (Application-Driven)

Resources are URI-identified context (RFC 3986), not actions. Hosts choose UX: picker, search, auto-attach. Contents: `text` or base64 `blob`. `resources/read` MAY return multiple contents (directory listing). Missing resource: JSON-RPC `-32602` (not empty `contents[]`). `https://` URIs SHOULD be fetchable by the client directly.

**Templates** use RFC 6570 URI templates; arguments can use the completion utility. **Annotations:** `audience` (`user`|`assistant`), `priority` 0.0-1.0, `lastModified` ISO-8601.

**Subscriptions.** Client opens `subscriptions/listen` with `resourceSubscriptions` URIs; server emits `notifications/resources/updated` correlated by subscription ID. Change notifications moved from GET SSE onto this opt-in listen stream in 2026-07-28.

### 3.6 Elicitation and MRTR (Multi Round-Trip Requests)

MRTR (SEP-2322) is the ONLY legal way for a server to ask the client for elicitation, sampling, or roots in 2026-07-28 -- a breaking change from bidirectional SSE requests.

**Flow:**
1. Client sends `tools/call` (or `resources/read` / `prompts/get`)
2. Server returns `resultType: "input_required"` with `inputRequests{}` + optional opaque `requestState`
3. Client gathers input from user
4. Client retries the same method with a new JSON-RPC id, echoing `requestState` and attaching `inputResponses`

**Elicitation modes:**
- **form:** restricted flat JSON Schema (string/number/boolean/enum/multi-select). Data IS visible to the client. MUST NOT collect passwords, API keys, tokens.
- **url:** out-of-band navigation; secrets never transit MCP. Use for OAuth-to-third-party and credential collection.

**`requestState` security:** Servers MUST treat it as attacker-controlled. Apply HMAC/AEAD, bind to principal, set TTL, bind to originating method/args. Single-use requires server-side nonce enforcement.

### 3.7 Tasks Extension (Durable Work)

`io.modelcontextprotocol/tasks` (contributed with AWS): server returns `resultType: "task"` + `taskId`, `ttlMs`, `pollIntervalMs`. Client polls `tasks/get` until `completed|failed|cancelled`. Mid-flight elicitation via `input_required` + `tasks/update`. Optional `notifications/tasks` on listen stream. Crash resilience: persist `taskId`; poll after reconnect. Cancellation is cooperative. Don't hold HTTP for CI/batch.

### 3.8 OAuth 2.1 Profile (HTTP Only)

STDIO SHOULD NOT use this profile -- credentials come from the environment. HTTP SHOULD. As of 2025-11-25, any internet-reachable MCP server MUST implement OAuth 2.1 with PKCE. Static "paste an API key" auth is explicitly non-compliant for public endpoints.

**Standards stack:** OAuth 2.1 draft-13, RFC 6750 Bearer, RFC 8414 AS metadata, RFC 9728 Protected Resource Metadata (MUST on MCP servers), RFC 8707 Resource Indicators (MUST on clients), RFC 9207 `iss` on auth response, CIMD (Client ID Metadata Document) SHOULD, DCR deprecated but retained for compat.

**Flow (compressed):**
1. Unauth MCP request -> `401 WWW-Authenticate: Bearer resource_metadata=..., scope=...`
2. Fetch Protected Resource Metadata
3. AS metadata discovery
4. CIMD (HTTPS `client_id` URL) or static/DCR
5. PKCE S256 (refuse if `code_challenge_methods_supported` absent)
6. Authorize with `resource` = MCP server URI
7. Validate `iss` (RFC 9207)
8. Token with `resource` parameter again
9. Bearer token to MCP

**Scope strategy:** Challenge `scope` is authoritative for this operation; step-up rather than asking `scopes_supported` maximally. Least privilege is a spec SHOULD.

### 3.9 Enterprise-Managed Authorization (EMA)

Extension for workforce SSO: employee SSO to the host; IdP issues ID-JAG; MCP AS exchanges ID-JAG for an MCP access token. Policy (group, CA, device) lives in Okta/Entra, not per-server consent screens. Revoke at the IdP once. Machine-to-machine: OAuth client-credentials extension.

### 3.10 MCP Clients in the Market

| Host | Transport | Notes |
|------|-----------|-------|
| **Cursor** | stdio, Streamable HTTP, legacy SSE | OAuth loopback; static `auth.CLIENT_ID`; enterprise allowlist + per-server network sandbox |
| **Claude.ai / Desktop / Cowork** | Remote: Anthropic-brokered HTTP; Desktop: local stdio | Remote connectors egress from Anthropic IPs, not the laptop |
| **Claude API MCP connector** | Streamable HTTP/SSE; tools only | Anthropic is the MCP client. Not on Bedrock/Vertex |
| **OpenAI Responses** | Streamable HTTP/SSE | `type: "mcp"` with `server_url` or `connector_id` or `tunnel_id`. No extra $ per MCP call. `require_approval` default-on |
| **VS Code Copilot** | stdio / `type: http` | MCP gallery + `.vscode/mcp.json`; macOS/Linux stdio sandbox |

Multi-server: hosts instantiate independent clients. One server crash does not take down others. Tool-name collisions are the host's problem (prefix). But cross-server prompt context is shared -- that is the shadowing attack surface.

### 3.11 Interoperability: A2A, OpenAPI, Gateways, Registries

**A2A vs MCP:**

| | MCP | A2A |
|---|-----|-----|
| Scope | Agent -> tool/resource | Agent -> agent (opaque peers) |
| Discovery | `tools/list` | Agent Card (skills, caps, security) |
| Unit of work | `tools/call` | Task + Message + Artifact |
| Auth | OAuth 2.1 | `securitySchemes` (OAuth2, mTLS, etc.) |
| Pattern | MCP inside the agent | A2A between agents |

**OpenAPI.** Native LLM tools are JSON Schema; MCP `inputSchema` is JSON Schema 2020-12. Production adapters: OpenAPI 3.0/3.1 operation -> one MCP tool. Consensus: OpenAPI remains the source-of-truth contract; MCP servers are generated/wrapped on top for agent connectivity; function calling is reserved for narrow, latency-critical paths.

**Tool gateways:** Microsoft MCP Gateway (K8s reverse proxy + tool registry + `POST /mcp` router), Envoy AI Gateway 1.0 `MCPRoute` (include/exclude tool filters), Cloudflare (stateless MCP on Workers). SEP-2243 exists so these boxes never parse JSON bodies.

**Registries.** Official: registry.modelcontextprotocol.io (reverse-DNS names, GitHub OAuth/OIDC proof). NOT a malware scanner -- scanning delegated to npm/PyPI/Docker. Anthropic Connectors Directory and Cursor Marketplace are aggregators, not the protocol registry. CVE-2026-44427: open redirect in trailing-slash middleware (fixed >=1.7.5).

**MCP vs Native Function Calling vs OpenAPI:**

| Dimension | Native Function Calling | OpenAPI | MCP |
|-----------|------------------------|---------|-----|
| Best fit | Latency-sensitive, small stable toolset, single agent | Enterprise HTTP services needing contracts | Cross-runtime portability, multi-host reuse |
| Governance | App-owned, ad hoc | Mature (security schemes, gateways) | Emerging (gateways layer RBAC on top) |
| Latency | Shortest (single LLM turn) | +1 network hop | Session/schema layer; stdio fast, HTTP varies |
| Portability | Vendor-specific | Vendor-neutral, mature | Vendor-neutral, purpose-built for multi-host |
| Discovery | Static, developer-declared | Static spec | Dynamic (`tools/list`, `listChanged`) |
| Reliability | Most battle-tested | Inherits REST API reliability | Newer, carries significant CVE surface |

---

## 4. Key Patterns & Best Practices

### Pattern 1: The "Tools Tax" and How to Manage It

MCP tool definitions cost **550-1,400 tokens per tool**. Real-world measurements:

| Configuration | Tool-Schema Tokens | % of 200K Window |
|--------------|-------------------|------------------|
| GitHub MCP alone (35 tools) | ~26,000 | 13% |
| Slack MCP alone (11 tools) | ~21,000 | 10.5% |
| GitHub + Playwright + IDE (3 servers) | ~143,000 | 72% |
| 5-server config | ~55,000 | 27.5% |
| 10-server config | ~75,000 | 37.5% |

Context utilization above a **~70% fracture point** is associated with measurable reasoning degradation.

**Mitigations and measured effects:**
- **Tool Search** (subagent-gated loading): preserved **85% of context**
- **Code Mode** (sandboxed code execution surface): **99.9% reduction** (1.17M -> ~1,000 tokens)
- **Tool Attention middleware** (Intent-Schema Overlap gating): **95% reduction** (47.3K -> 2.4K tokens)
- **Layered tool pattern** (Block/Square): 200+ endpoints -> **3 conceptual tools** (discover/plan/execute)
- Practical ceiling: **~30-40 always-loaded tools**, defer the rest via search/lazy-loading

**Prompt-cache interaction:** Adding/removing tools mid-conversation invalidates the prefix cache; a miss can cost more than the tools you dropped. Mitigations: deterministic `tools/list` order; append new defs after the cache breakpoint; or a single stable `call_tool({name,args})` meta-tool.

### Pattern 2: Stateless by Default, Stateful by Exception

Cross-call state = handles in tool arguments. Handle rules:
- Authenticated: handle is a name, not a capability -- re-check authz every call
- Unauthenticated: handle IS a bearer token -> UUIDv4-class entropy + TTL
- Opaque to the caller; document lifetime in the create tool description (model-visible)
- Unknown/expired -> `isError: true` with recoverable message (create a new handle)

Use the Tasks extension only when you genuinely need durable work across disconnects.

### Pattern 3: Progressive Discovery

If tool definitions exceed ~1-5% of the context window, switch from eager loading to:
1. `search_tools` -- model asks for tools matching an intent
2. `get_tool_details` -- model gets the full schema for selected tools
3. Execute -- model calls the tool

This keeps context lean. OpenAI and Anthropic both offer built-in tool search. Or implement a `call_tool({name,args})` meta-tool.

### Pattern 4: Zero-Trust Token Rules (Non-Negotiable)

**Token passthrough is forbidden.** MCP servers MUST accept only tokens audienced to themselves and MUST NOT forward the inbound access token upstream. Upstream = a new token from the upstream AS (on-behalf-of / client-credentials / workload identity).

Why:
- Passthrough bypasses MCP-layer rate limits, schema validation, and audit
- A token stolen for Service A becomes a confused-deputy key for Service B if B doesn't check `aud`
- Future controls (step-up, tool-level RBAC) are unimplementable if the server is a dumb pipe

### Pattern 5: Gateway as Enterprise Control Plane

```
  +------------+
  |    Host    |
  |  (Claude)  |
  +-----+------+
        |
  +-----v------+
  | MCP Gateway|  <-- Auth, RBAC, audit, rate limit, tool-hash pin
  +--+---+---+-+
     |   |   |
  +--v-+ +-v-+ +-v--+
  |Srv1| |Srv2| |Srv3|
  +----+ +----+ +----+
```

Gateway centralizes: auth (OAuth 2.1/SSO termination), RBAC (per-user/per-role allowed server+tool combos), audit (tool-call-level structured logs), rate limiting, policy (reject poisoned descriptions, redact PII). Deploy in logging-only mode for weeks before enabling enforcement.

Vendor landscape: Kong AI MCP Proxy, Azure API Management, Cloudflare AI Gateway, MintMCP (SCIM-driven RBAC), Operant (SPIFFE/SPIRE), Microsoft MCP Gateway (K8s).

### Pattern 6: Sandbox Isolation Tiers

| Approach | Startup Latency | Isolation Level | When to Use |
|----------|----------------|-----------------|-------------|
| OS-level (bubblewrap/seatbelt) | <10ms | Process-level | Trusted local CLI tools |
| gVisor (userspace kernel) | ~500ms | Container+ | Multi-tenant cloud MCP servers |
| Firecracker microVM | ~125ms | Hardware/VM-level | Highest-assurance managed platforms |

A documented gVisor test running Anthropic's reference filesystem MCP server under 60+ adversarial inputs (`--network none`, `--cap-drop ALL`, `--read-only`) blocked all network calls, sensitive-path writes, and process spawning.

---

## 5. System Design Considerations

### 5.1 Transport & State Decision Matrix

| | stdio | Streamable HTTP + JSON | Streamable HTTP + SSE | Tasks Extension |
|---|-------|----------------------|----------------------|-----------------|
| Fan-out | 1 client | Many | Many | Many |
| Load balancing | n/a | Round-robin OK (stateless) | Same; don't buffer | Poll any replica if task store shared |
| HITL mid-call | MRTR | MRTR | MRTR | `input_required` on task |
| Long job | Blocks process | Proxy timeout | Proxy timeout | **Designed for this** |
| Secrets | env vars | OAuth | OAuth | OAuth + task ACL |
| Cancel | `notifications/cancelled` | Close stream | Close stream | `tasks/cancel` cooperative |

### 5.2 When to Use What (Protocol Choice)

| Need | Choose | Don't |
|------|--------|-------|
| CRUD / query / one-shot action | **MCP tool** or OpenAPI->MCP | A2A task for a calculator |
| Multi-turn negotiation with another org's agent | **A2A** (Agent Card, artifacts) | Flatten multi-turn agent work into `tools/call` |
| Existing REST estate, no agent team | **OpenAPI tool** (Foundry) or generated MCP wrapper | Hand-write 200 MCP tools on day 1 |
| Mix Search + MCP + OpenAPI under one policy | **Toolbox / MCP gateway** | N direct connections |
| IDE local files / secrets on laptop | **stdio** + OS sandbox | Remote MCP with env API keys in cloud |
| SaaS consumed by Claude/ChatGPT/Cursor | **Streamable HTTP** + OAuth 2.1 + CIMD | STDIO-only (Claude.ai can't reach it) |

### 5.3 Architecture Trade-Off Matrix

| Architecture | Strength | Cost / Risk | Fit |
|-------------|----------|-------------|-----|
| Direct host -> N servers | Simple; Cursor/VS Code native | Catalog explosion; shadowing; N OAuth dances | <15 trusted servers |
| Progressive discovery host | Token + accuracy | Extra meta-tools; cache discipline | 50-1000 tools |
| Enterprise MCP gateway | One `aud`, RBAC, audit, circuit breaking | Extra hop; gateway is a deputy | Regulated; many teams |
| Provider-hosted MCP (OpenAI/Anthropic) | No local client; multi-server | Egress to vendor; tool subset; approval UX is theirs | Product agents |
| Secure tunnel / Workers | Private MCP without public IP | Vendor trust; tunnel ops | On-prem tools for ChatGPT |
| EMA + IdP | Central joiner/leaver | Client + AS must implement ID-JAG | Workforce Claude/Cursor at scale |
| A2A mesh + MCP leaves | Org boundaries, long tasks | Two auth stacks; Agent Card sprawl | Supplier/partner agents |

### 5.4 Resilience Stack

Implement in this order around every external call:

1. **Rate limiter** (admission control)
2. **Bulkhead** (isolate resource pools per dependency)
3. **Circuit breaker** (Closed -> Open -> Half-Open; ~5 consecutive failures, ~60s cooldown)
4. **Retry with exponential backoff + jitter** (avoid thundering herd)
5. **Timeout** (bounds latency per attempt, not whole retry budget)
6. **Fallback** (cached/partial/default response)

Key MCP-specific guidance: implement circuit breakers **per external dependency, not per tool** (multiple tools often share one backend). Surface breaker state in the tool-error message text so the LLM can reason about it. Route logs to stderr, never stdout (corrupts stdio JSON-RPC).

### 5.5 Reconnect, Cancellation, Subscriptions

| Event | stdio | Streamable HTTP |
|-------|-------|-----------------|
| Cancel in-flight | `notifications/cancelled` | Close SSE stream |
| Process death | Restart subprocess; retry; re-listen | Retry POST on any instance |
| Catalog/resource change | `subscriptions/listen` | Same; keep-alives required |
| SSE disconnect | n/a | Re-listen; NO `Last-Event-ID` replay |

### 5.6 Durable Execution for MCP Tools

For multi-step tool logic that must survive crashes, use external workflow engines:
- **Dapr MCPServer**: auto-registers a durable workflow per discovered tool; a tool call becomes "start a workflow"
- **Temporal**: wraps each MCP tool as a thin invoker of a Temporal Workflow; all business logic as Activities with automatic retry policies

### 5.7 Capacity Planning

- Per-agent context budget must reserve headroom for tool schemas: a naive 3-server/40-tool deployment can consume >70% of a 200K-token window before any user content
- Session-affinity strategy is a throughput lever: shared-session pools measured ~10x the throughput of unique-session-per-request pools
- Sandbox choice is a latency/security trade: <10ms (OS-level) for trusted local, ~500ms (gVisor) for multi-tenant, ~125ms (Firecracker) for highest-assurance

---

## 6. Code Examples

### Basic MCP Tool Server (Python)

```python
from mcp.server import Server
from mcp.types import Tool, TextContent
import json

server = Server("weather-service")

@server.list_tools()
async def list_tools():
    """Return tool catalog. MUST be deterministically ordered for prompt cache."""
    return [
        Tool(
            name="get_weather",
            description="Get current weather for a city. Returns temperature and conditions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"},
                    "units": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "default": "celsius"
                    }
                },
                "required": ["city"],
                "additionalProperties": False  # Recommended for parameterized tools
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "get_weather":
        try:
            weather = await fetch_weather(arguments["city"], arguments.get("units", "celsius"))
            # Return structured content + text fallback for older hosts
            return [TextContent(type="text", text=json.dumps(weather))]
        except CityNotFoundError:
            # Business failure: isError=True so model can self-correct
            # NOT a JSON-RPC error
            return [TextContent(
                type="text",
                text=f"City '{arguments['city']}' not found. Try a different spelling.",
                isError=True
            )]
    raise ValueError(f"Unknown tool: {name}")  # This becomes JSON-RPC -32602
```

### MCP Client with Progressive Discovery

```python
from mcp.client import Client

async def smart_tool_usage(client: Client, user_query: str):
    """
    Progressive discovery: don't load all tools into context.
    Step 1: Search for relevant tools
    Step 2: Get details for selected tools
    Step 3: Execute
    """
    # Instead of dumping all tools/list into the model:
    all_tools = await client.list_tools()

    if len(all_tools) > 40:
        # Too many tools -- use search/filter approach
        # Only load tools matching the user's intent
        relevant = [t for t in all_tools if matches_intent(t, user_query)]
        return relevant[:10]  # Cap at 10 for context efficiency
    else:
        return all_tools
```

### OAuth 2.1 Flow for Remote MCP Server

```python
# Simplified OAuth 2.1 flow for MCP (conceptual)
import httpx

async def authenticate_mcp_server(server_url: str):
    """
    1. Unauth request -> 401 with resource_metadata URL
    2. Fetch Protected Resource Metadata (RFC 9728)
    3. Discover Authorization Server (RFC 8414)
    4. PKCE S256 flow with resource parameter (RFC 8707)
    """
    # Step 1: Get 401 with metadata pointer
    resp = await httpx.post(f"{server_url}/mcp", json={"method": "tools/list"})
    assert resp.status_code == 401
    resource_metadata_url = parse_www_authenticate(resp.headers["www-authenticate"])

    # Step 2: Fetch PRM
    prm = await httpx.get(resource_metadata_url)
    as_url = prm.json()["authorization_servers"][0]

    # Step 3: AS metadata
    as_metadata = await httpx.get(f"{as_url}/.well-known/oauth-authorization-server")

    # Step 4: PKCE S256 -- MUST refuse if code_challenge_methods_supported absent
    if "S256" not in as_metadata.json().get("code_challenge_methods_supported", []):
        raise SecurityError("AS doesn't support PKCE S256 -- abort")

    # Step 5: Authorize with resource parameter (audience binding)
    # resource = server_url ensures token is ONLY valid for this MCP server
    auth_url = build_auth_url(
        as_metadata.json()["authorization_endpoint"],
        resource=server_url,        # RFC 8707 -- critical for zero-trust
        code_challenge=pkce_challenge,
        code_challenge_method="S256",
    )

    # ... redirect user, get code, exchange for token ...
    # Token's aud claim MUST match server_url
```

### Handle-Based State Management (Stateless Protocol)

```python
@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "create_shopping_cart":
        # Create state, return an opaque handle
        cart_id = str(uuid.uuid4())  # UUIDv4 entropy for unauthenticated handles
        await store.create(cart_id, {"items": [], "created": now(), "ttl": 3600})
        return [TextContent(
            type="text",
            text=f"Cart created. Use cart_id='{cart_id}' in subsequent calls. "
                 f"Expires in 1 hour."  # Document lifetime -- model-visible
        )]

    elif name == "add_to_cart":
        cart_id = arguments["cart_id"]
        cart = await store.get(cart_id)
        if cart is None:
            # Unknown/expired handle -> isError with recovery instruction
            return [TextContent(
                type="text",
                text="Cart expired or invalid. Create a new cart first.",
                isError=True
            )]
        # Re-check authz even though handle is valid (authenticated case)
        if not await check_user_owns_cart(current_user, cart_id):
            return [TextContent(type="text", text="Access denied.", isError=True)]

        cart["items"].append(arguments["item"])
        await store.update(cart_id, cart)
        return [TextContent(type="text", text=f"Added. Cart now has {len(cart['items'])} items.")]
```

---

## 7. Common Pitfalls & Failure Modes

### 7.1 Tool Poisoning Attacks (TPA)

Malicious instructions embedded in tool metadata (descriptions, parameter docs) -- invisible to the user's UI but fully visible to the LLM. The poisoned tool doesn't even need to be executed; its description alone can steer the model to exfiltrate data or take unauthorized actions.

**MCPTox benchmark (45 live servers, 353 tools, 1,312 adversarial test cases, 20 LLM agents):**
- Average attack success rate: **36.5%**
- Highest: **72.8%** (OpenAI o1-mini)
- Counterintuitive: more capable models are often MORE susceptible (exploits superior instruction-following)
- Best refusal rate: Claude 3.7 Sonnet refused less than 3% of attacks, still complied in ~34%
- **MCPLib** catalogs **31 distinct attack methods** across 4 classes

**MCP-ITP (arXiv 2601.07395):** Implicit poisoning -- the malicious tool is never called; metadata steers the agent to a privileged tool. Reported up to **84.2% ASR** and 0.3% miss detection rate.

**Mitigations:** Render full description + schema in HITL; hash-pin catalogs (MCP-Scan "tool pinning"); isolate high-privilege servers into separate hosts/conversations; never mix unvetted marketplace servers with secrets-bearing servers.

### 7.2 Rug-Pull Servers

Server returns benign `tools/list` at install approval, then `list_changed` injects poisoning. Same class as a PyPI package growing malware post-review. `ttlMs` + listen notifications make detection easier if the host hashes the list and re-prompts on diff. Clients that cache forever without invalidation can miss a rug-pull or stick to a stale good list -- both are failure modes.

### 7.3 Confused Deputy (Two Species)

**A. OAuth proxy deputy.** MCP proxy uses static third-party `client_id` + DCR + consent cookie. Attacker registers `redirect_uri=attacker.com`, rides the cookie, skips consent, steals a code. Fix: per-client consent, exact redirect match, single-use `state` after consent.

**B. Tool-authority deputy.** MCP server holds GitHub/Slack credentials; the model is induced (via issue text, email, or another tool result) to misuse them. Invariant Labs: official GitHub MCP + public issue -> agent dumps private-repo PII into a public PR. Not a bug in GitHub's MCP code -- any client with that server is exposed. Fix: one repo per session, least-privilege PATs, runtime dataflow policy.

### 7.4 Schema Drift

- Server tightens `inputSchema` -> model keeps old cached schema -> validation `isError` or `-32602`
- Server adds required fields -> same
- `outputSchema` added later -> non-validating clients silently accept garbage
- JSON Schema dialect mismatch (draft-07 vs 2020-12) across SDK eras
- Gemini/OpenAI subset conversion strips `$defs` / `additionalProperties`
- Aggregator name prefixing changes mid-prompt-cache

Fix: honor `ttlMs` AND `list_changed`; bust LLM tool cache when hash changes; contract tests in CI.

### 7.5 Prompt Injection via Resources

Resources are application-driven and often auto-attached. A `resources/read` of a ticket, email, or wiki is untrusted text that can become a tool-use script. The GitHub-issue pattern generalized: any retrieved document can manipulate the model into calling tools.

Fix: delimit untrusted resource bytes; sanitize tool results; policy: "no `tools/call` that writes public artifacts in the same turn as a read from untrusted URI"; human approval on destructive tools.

### 7.6 Named CVEs and Incidents

| Date | Incident | CVSS | Description |
|------|----------|------|-------------|
| Jun 2025 | CVE-2025-49596 (MCP Inspector) | 9.4 | Unauth RCE via DNS rebinding + 0.0.0.0 binding |
| Jul 2025 | CVE-2025-6514 (`mcp-remote` npm) | 9.6 | OS command injection via malicious OAuth endpoint; 437K+ downloads |
| Jul 2025 | CVE-2025-54136 "MCPoison" (Cursor) | 7.2-8.8 | Trust bound to server name not contents; team-wide compromise from one file |
| Aug 2025 | CVE-2025-54135 "CurXecute" (Cursor) | 9.8 | Workspace-file write via prompt injection -> RCE through MCP auto-start |
| Aug 2025 | CVE-2025-53109/53110 (Anthropic Filesystem MCP) | 7.3/8.4 | Symlink and path-prefix containment bypass |
| Sep 2025 | Postmark MCP (npm) supply-chain trojan | n/a | BCC-based data exfiltration hidden in package update |
| Sep 2025 | npm worm "Shai-Hulud" | n/a | Harvested npm/GitHub/AWS/GCP tokens from infected machines, ~500 packages |
| Jan 2026 | CVE-2025-68143/44/45 (mcp-server-git) | up to 9.1 | Path traversal + argument injection, 3 chained flaws |
| Mar 2026 | CVE-2026-33032 "MCPwn" (nginx-ui) | 9.8 | Auth bypass -> RCE, actively exploited |
| Jan-Apr 2026 | OX Security: systemic STDIO command injection | Critical | 10 CVEs spanning Python/TS/Java/Rust SDKs; est. 200,000 vulnerable servers |

**Aggregate:** 313 CVEs indexed touching MCP ecosystem. Independent scans: **30-82% of public MCP servers carry exploitable flaws**; only **8.5% use OAuth**. In Jan-Apr 2026: shell/exec injection 43%, tooling infra 20%, auth bypass 13%, path traversal + other ~24%.

### 7.7 Supply-Chain Risk

The dominant install pattern (`npx -y some-mcp-server` or `uvx some-mcp-server`) resolves the full transitive dependency tree from a public registry and executes with host privileges -- filesystem, env vars, network -- BEFORE any MCP handshake begins. `postinstall` scripts run at install time; MCP-layer policy enforcement cannot intercept. Unpinned `@latest` installs bet against a compromise window measured in hours. Container isolation with restricted egress (ToolHive) was confirmed effective against the Sept 2025 npm attack.

---

## 8. Interview Questions & Answers

**Q1: What is MCP and why does it exist?**

MCP is the Model Context Protocol -- an open standard for connecting AI applications to external tools and data. Before MCP, every AI app needed custom integrations for every tool. MCP standardizes this with a JSON-RPC 2.0 protocol connecting hosts (AI apps), clients (protocol connectors inside hosts), and servers (tool/data providers). It defines three primitives: tools (model-invoked actions), resources (URI-addressed context), and prompts (templated workflows). The key insight is the model never speaks MCP -- it emits native tool calls, and the host's client translates them to JSON-RPC. Think of it as USB-C for AI tools.

**Q2: Explain the 2026-07-28 stateless redesign. Why was it done?**

The old protocol required an `initialize`/`initialized` handshake and tracked sessions via `Mcp-Session-Id`. Google and Cloudflare both published detailed accounts of this breaking cloud-native scaling: session IDs forced sticky-session load balancing, complex drain-on-deploy logic, and broken sessions on autoscale/restart. The 2026-07-28 revision removed sessions entirely. Every request is self-describing via `_meta` and HTTP headers (`Mcp-Method`, `Mcp-Name`, `MCP-Protocol-Version`). Any request can hit any replica behind round-robin. Cross-call state is now explicit handles in tool arguments. The trade-off is you lose mid-stream resume (dropped connection = retry from scratch), so idempotent tools are essential.

**Q3: What is the "Tools Tax" and how do you mitigate it?**

The Tools Tax is the context-window cost of MCP tool schemas. Each tool definition costs 550-1,400 tokens. In practice, a 3-server / 40-tool deployment can consume 70%+ of a 200K-token window before any user content. Past the ~70% fracture point, reasoning degrades. Four mitigations: (1) Tool Search / progressive discovery -- only load tools matching the current intent, preserving ~85% of context. (2) Code Mode -- expose a sandboxed code surface instead of per-tool schemas, achieving 99.9% reduction. (3) Layered tool pattern (Block's approach) -- collapse 200+ endpoints into 3 conceptual tools (discover/plan/execute). (4) Keep always-loaded tools to ~30-40 max, defer the rest via lazy-loading. Also, deterministic `tools/list` ordering stabilizes prompt caches for a 10x input discount.

**Q4: Walk me through MCP's OAuth 2.1 security model.**

An internet-reachable MCP server MUST implement OAuth 2.1 with PKCE. The server is strictly a resource server (validates tokens, never issues them). Flow: unauthenticated request gets a 401 with a `resource_metadata` URL pointing to RFC 9728 Protected Resource Metadata. Client discovers the Authorization Server via RFC 8414. PKCE S256 is mandatory (refuse if not supported). The critical zero-trust control is RFC 8707 Resource Indicators: the client includes a `resource` parameter (the MCP server's URL) in auth and token requests, and the server validates that the token's `aud` claim matches. This prevents a token for Server A from being replayed against Server B. Token passthrough is explicitly forbidden -- each hop gets its own credential.

**Q5: What is MRTR and why does it matter?**

MRTR (Multi Round-Trip Requests) is the only legal way for a server to ask the client for additional input in the 2026-07-28 spec. It replaced the old bidirectional SSE approach. When a server needs user input (e.g., OAuth consent, form data), it returns `resultType: "input_required"` with `inputRequests` and an opaque `requestState`. The client gathers input and retries the same method with a new JSON-RPC id, echoing `requestState`. The `requestState` must be cryptographically protected (HMAC/AEAD, bound to principal, with TTL) because the server must treat it as attacker-controlled. Two elicitation modes: form (flat JSON Schema, data visible to client, no secrets) and url (out-of-band navigation, secrets never transit MCP).

**Q6: What are tool poisoning attacks and how do you defend against them?**

Tool poisoning embeds malicious instructions in tool metadata (descriptions, parameter docs) that are invisible to the user but visible to the LLM. The poisoned tool doesn't need to be called -- its description alone can steer the model. MCPTox benchmark showed a 36.5% average attack success rate across 20 LLM agents, with more capable models often more susceptible. An implicit variant (MCP-ITP) achieved 84.2% success without the malicious tool ever being invoked. Defense: render full descriptions in HITL UI; hash-pin tool catalogs (MCP-Scan); isolate high-privilege servers in separate conversations; never mix unvetted marketplace servers with secrets-bearing servers; treat all descriptions and annotations as untrusted.

**Q7: How does A2A differ from MCP and when would you use each?**

MCP is agent-to-tool (vertical integration): your agent calls tools and reads resources. A2A is agent-to-agent (horizontal integration): one agent delegates to another opaque agent. MCP discovery is `tools/list`; A2A discovery is Agent Cards describing capabilities, skills, and security. MCP's unit of work is a stateless `tools/call`; A2A's is a stateful Task with lifecycle states (SUBMITTED -> WORKING -> COMPLETED/FAILED). Use MCP inside each agent for its tools; use A2A between agents, especially across organizations where you need opacity. The emerging pattern: A2A between specialist agents, MCP between each specialist and its tools.

**Q8: How would you design an enterprise MCP deployment for 1000+ tools?**

Don't connect 1000 tools directly. Layer it: (1) MCP gateway as the control plane -- one `aud`, centralized auth (SSO/EMA), RBAC, audit, rate limiting, circuit breakers. Use `Mcp-Method`/`Mcp-Name` headers so the gateway never parses JSON bodies. (2) Progressive discovery behind the gateway -- model sees search/detail/execute meta-tools, not 1000 tool schemas. (3) Server portals that front multiple backend MCP servers with default-deny write controls. (4) Code Mode at the portal level to keep token cost flat as servers are added (Cloudflare achieved 94% reduction). Deploy gateway in logging-only mode first. Pin tool-catalog hashes. Re-approve on `list_changed`. Budget token cost per connected server.

**Q9: What's the confused deputy problem in MCP?**

Two species. (A) OAuth proxy deputy: an MCP proxy uses a static third-party `client_id` with Dynamic Client Registration and consent cookies. An attacker registers `redirect_uri=attacker.com`, rides the cookie, skips consent, and steals an auth code. Fix: per-client consent before redirect, exact `redirect_uri` match, single-use `state` after consent. (B) Tool-authority deputy: the MCP server holds powerful credentials (GitHub admin, Stripe), and the model is prompt-injected (via issue text, email, resource content) into misusing those credentials. This is not a bug in any specific MCP server -- any client with that server is exposed. Fix: least-privilege PATs, one resource scope per session, runtime dataflow policy, separate high-privilege servers into isolated conversations.

**Q10: How do you handle MCP server failures in production?**

Multi-server hosts isolate failures -- one server crash doesn't take down others (Cursor documents this). For individual server resilience, implement a per-dependency (not per-tool) circuit breaker stack: rate limiter -> bulkhead -> circuit breaker (5 consecutive failures, 60s cooldown) -> retry with exponential backoff + jitter -> timeout -> fallback. Surface breaker state in the error message text so the LLM can reason about it. For durable work, use the Tasks extension (persist `taskId`, poll after reconnect) or external workflow engines (Temporal, Dapr). Stateless 2026-07-28 helps: pod restarts are invisible; requests hit any healthy replica.

**Q11: What supply-chain risks are specific to MCP?**

The dominant install pattern (`npx -y some-mcp-server`) resolves and executes the full transitive dependency tree with host privileges before any MCP handshake begins. `postinstall` scripts run at install time, so MCP-layer enforcement can't intercept. Real incidents: the Sept 2025 npm worm "Shai-Hulud" harvested credentials from ~500 packages. A dependency confusion campaign in May 2026 hit 33 packages impersonating internal scopes. As of Aug 2026, 313 CVEs touch the MCP ecosystem, 30-82% of public servers carry exploitable flaws, and only 8.5% use OAuth. Mitigations: pin versions (never `@latest`), container isolation with restricted egress (ToolHive), prefer first-party hosted servers (`mcp.stripe.com` not a third-party proxy), hash-pin tool catalogs, namespace verification via the official registry.

**Q12: How does the Block (Square) case study demonstrate enterprise MCP at scale?**

Block rewrote their internal agent "Goose" as an MCP client and scaled to 12,000 employees across 15 job functions in 8 weeks. They built 100+ pre-approved internal MCP servers bundled by default. Key architectural decisions: (1) Replaced API keys with OAuth + SSO. (2) Used a layered tool pattern -- collapsed Square's 200+ endpoints into 3 conceptual tools (discover/plan/execute) instead of 1:1 endpoint-to-tool mapping that caused context blowup and errors. (3) Added dynamic context management (auto enable/disable servers based on query). Reported outcome: 75% of engineers saving 8-10 hours/week; company-wide 50-75% time savings.

---

## 9. Key Numbers to Memorize

| Metric | Value | Source |
|--------|-------|--------|
| MCP protocol fee per call | **$0** | OpenAI explicit statement |
| Tool schema overhead per tool | **550-1,400 tokens** | Scalekit benchmark |
| MCP vs CLI token cost ratio | **4x-32x** more tokens | Scalekit benchmark |
| GitHub MCP alone (35 tools) context | **~26,000 tokens (13% of 200K)** | AgentPMT measurement |
| 3-server config context | **~143,000 tokens (72% of 200K)** | AgentPMT measurement |
| Context fracture point | **~70% utilization** | Academic literature |
| Tool Search context preservation | **85%** | Anthropic |
| Code Mode token reduction | **99.9%** (1.17M -> ~1K) | Cloudflare |
| Tool Attention token reduction | **95%** (47.3K -> 2.4K) | arXiv 2604.21816 |
| Practical always-loaded tool ceiling | **~30-40 tools** | Multiple sources |
| Prompt cache discount (Sonnet 5) | **10x** ($2 -> $0.20/MTok) | Anthropic pricing |
| Claude web search / OpenAI web search | **$10 / 1K searches** | Vendor pricing |
| Streamable HTTP shared-session throughput | **290-300 req/s** | ToolHive benchmark |
| Unique-session throughput | **30-36 req/s** (~10x worse) | ToolHive benchmark |
| OpenAI MCP RPM limits | **200 (Tier 1) to 2000 (Tier 5)** | OpenAI docs |
| MCP CVEs (Aug 2026) | **313** | mcp-cve-project |
| Public servers with exploitable flaws | **30-82%** | Independent scans |
| Public servers using OAuth | **8.5%** | Independent scans |
| MCPTox avg attack success rate | **36.5%** | Academic benchmark |
| Block/Goose rollout scale | **12,000 employees, 8 weeks** | Block case study |
| Block time savings | **8-10 hours/week for 75% of engineers** | Block case study |

---

## 10. Quick Reference

### MCP Architecture at a Glance

```
HOST (Claude/Cursor/ChatGPT)
  |-- Client 1 <-> Server 1 (GitHub, stdio)
  |-- Client 2 <-> Server 2 (Slack, Streamable HTTP)
  |-- Client N <-> Server N (Custom, Streamable HTTP + OAuth)

Model emits native tool calls -> Client translates to JSON-RPC tools/call
Model NEVER speaks JSON-RPC directly
```

### Three Primitives

| Primitive | Who Decides | Discovery | Invocation |
|-----------|------------|-----------|------------|
| Tools | Model | `tools/list` | `tools/call` |
| Resources | Application | `resources/list` | `resources/read` |
| Prompts | User | `prompts/list` | `prompts/get` |

### Transport Decision

| | stdio | Streamable HTTP |
|---|-------|-----------------|
| Use when | Local IDE tools, secrets on laptop | SaaS products, multi-tenant, cloud-hosted |
| Auth | OS-level process isolation | OAuth 2.1 + PKCE S256 (MUST for public endpoints) |
| Scaling | Single client | Round-robin any replica (stateless) |
| Latency | Near-zero network overhead | ~10ms under load + upstream API |

### Zero-Trust Checklist

| Control | Implementation |
|---------|----------------|
| Strong identity | EMA or CIMD+PKCE; no long-lived tokens in git |
| Per-request authz | Gateway on `Mcp-Name` + server-side check |
| Audience-bound tokens | RFC 8707 `resource`; reject wrong `aud` |
| No token passthrough | New upstream credential every hop |
| Least-privilege catalogs | `allowed_tools`; progressive discovery |
| Network egress policy | Cursor/VS Code sandbox; SSRF allowlist for OAuth URLs |
| Supply-chain pin | Hash tool descriptors; registry namespace proof; prefer first-party servers |
| Assume poisoned catalog | Show full descriptions in HITL; pin versions; `list_changed` = re-review |

### Cost Quick Math

- **MCP protocol fee: $0** -- you pay only tokens
- Tool schemas at 80 tools x 350 tokens: **28K tokens/turn** = $0.14 uncached, $0.014 cached (Sol/Opus 5)
- 1K tool calls at 800 tokens each: **$4.00** input (but Code Mode filters this out of context)
- Web search: **$10/1K** -- often larger than MCP token cost
- Prompt cache stable tools: **10x cheaper** than uncached

### Interview-Ready Invariants

1. Host != client != server; one client per server; the LLM never speaks JSON-RPC
2. 2026-07-28 is stateless HTTP: `_meta` + `Mcp-Method`/`Mcp-Name`; sessions are handles or Tasks
3. MRTR replaced bidirectional sampling/elicitation; `requestState` must be AEAD
4. Sampling/roots/logging/HTTP+SSE are deprecated (12-month floor), not gone today
5. MCP $/1k calls = $0 protocol + token economics; Web Search is a $10/1k SKU
6. Token passthrough is a spec violation; `aud` + RFC 8707 are Zero-Trust MCP
7. Tool text is an instruction channel; poisoning, shadowing, rug-pull are production threats
8. A2A is the peer plane; MCP is the tool plane; gateways/registries are the enterprise control plane
9. Annotations, `cacheScope`, and UIs that hide arguments will lie to humans
10. Multi-server isolation of process != isolation of prompt
