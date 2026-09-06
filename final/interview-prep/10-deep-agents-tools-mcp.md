# Module 10: Deep Agents Tools & MCP Integration

## What Is This?

This module covers how Deep Agents connects to the outside world: the three tool sources (custom, built-in, MCP), the Model Context Protocol itself, and the broader ecosystem adapters (ACP for editors, A2A for agent-to-agent). MCP is the "USB-C for AI" -- a standardized JSON-RPC 2.0 protocol that replaced the chaos of bespoke API integrations between AI models and external tools. Introduced by Anthropic in November 2024 and donated to the Linux Foundation's Agentic AI Foundation in December 2025, MCP is now adopted by Anthropic, OpenAI, Google, Microsoft, and AWS. Monthly SDK downloads: **97M+** by mid-2026.

In Deep Agents, MCP tools land on the same `tools=` surface as ordinary callables, but the operational concerns are different: local tools run in your process, built-in tools are injected by the harness, and MCP tools come from separate processes or remote servers needing transport, auth, and session strategy. The critical security fact is that `permissions=` does **not** cover MCP tools -- Zero-Trust MCP requires a **gateway PEP** (Policy Enforcement Point) that you build.

The ecosystem adapters (ACP, A2A, `dcode`) are **I/O adapters** around the same `create_deep_agent` compiled graph -- they are not new runtimes. ACP is JSON-RPC over stdio to editors. A2A is Agent Server `POST /a2a/{assistant_id}`. `dcode` is the CLI product. All consume the same `CompiledStateGraph`.

**Package pins**: `deepagents==0.7.12`, `deepagents-code==0.1.65` (pins `deepagents==0.7.10`), `deepagents-acp==0.0.11` (Alpha).

---

## Part 1: System Topology & Data Flow

### Three Tool Sources

| Source | Runs Where | Injected How | Governed By |
|--------|-----------|-------------|-------------|
| **Custom callables** | Your process | `tools=` in `create_deep_agent` | Your code; `interrupt_on` can name them |
| **Built-in harness tools** | Middleware + backend | Auto-injected (ls, read_file, write_file, edit_file, delete, glob, grep, execute, task) | `permissions=` (FS only); `excluded_tools`; `FilesystemMiddleware(tools=)` |
| **MCP-loaded tools** | Separate process or remote server | `MultiServerMCPClient` -> `get_tools()` -> `tools=` (additive) | **Gateway PEP** (not `permissions=`); `tool_interceptors`; `tool_name_prefix` |

`tools=` is **additive**: it never removes a built-in. To the model, all three sources look the same -- they are all tools in one selection loop. To you, they are operationally very different.

### MCP Architecture Diagram

```
+-----------------------------------------------------------------------+
|                           HOST APPLICATION                              |
|  (Deep Agents agent, Claude Desktop, Cursor, Custom App)               |
|                                                                         |
|  +------------------+  +------------------+  +------------------+       |
|  |   MCP Client 1   |  |   MCP Client 2   |  |   MCP Client 3   |      |
|  |  (1:1 connection) |  |  (1:1 connection) |  |  (1:1 connection) |     |
|  +--------+---------+  +--------+---------+  +--------+---------+       |
|           |                      |                      |                |
+-----------+----------------------+----------------------+----------------+
            | stdio                | stdio                | Streamable HTTP
            | (JSON-RPC 2.0)       | (JSON-RPC 2.0)       | (JSON-RPC 2.0)
            v                      v                      v
     +-------------+        +-------------+       +--------------------+
     | MCP Server  |        | MCP Server  |       | MCP Server (Remote)|
     | (Local)     |        | (Local)     |       |                    |
     | Tools:      |        | Tools:      |       | OAuth 2.1 + PKCE   |
     |  db_query   |        |  git_log    |       | Tools:             |
     |  db_write   |        |  git_diff   |       |  jira_create       |
     | Resources:  |        | Resources:  |       |  jira_search       |
     |  schema     |        |  repo tree  |       | Resources:         |
     | Prompts:    |        | Prompts:    |       |  sprint_board      |
     |  sql_help   |        |  review     |       +--------------------+
     +-------------+        +-------------+
```

### Three MCP Roles

**Host**: The LLM application (Deep Agents agent). Owns conversation, model invocation, user consent. Controls what tools the model sees and what credentials each server gets.

**Client**: A per-server connection living inside the host. One client per server (strict 1:1). Handles wire-level protocol -- connection management, capability negotiation, message framing.

**Server**: Exposes tools (callable functions), resources (read-only data), and prompts (reusable templates). Thin translation layers wrapping existing services in the MCP protocol.

### Protocol Triangle (Stackable)

| Protocol | Direction | Purpose |
|----------|-----------|---------|
| **MCP** | Agent <-> Tools/Data | Standardized tool/data access for one agent |
| **ACP** | Editor <-> Coding Agent | Agent-editor communication (JSON-RPC over stdio) |
| **A2A** | Agent <-> Agent | Peer-to-peer agent collaboration between deployments |

These stack: Zed ACP session -> Deep Agent -> MCP tools; a second fleet agent calls the same graph over A2A. MCP ingress (`POST /mcp`) exposes the agent as a stateless tool. A2A (`POST /a2a/{assistant_id}`) exposes it as a conversational peer. Mixing MCP ingress with A2A is a common design error: MCP = "this agent is a tool"; A2A = "this agent is a conversational peer."

### MCP Request Flow (End-to-End Tool Call)

1. **Discovery**: Client calls `tools/list`. Server returns catalog with names, descriptions, input schemas. Response includes `ttlMs` and `cacheScope` for caching (2026-07-28 spec).
2. **Schema injection**: Host injects tool definitions into the LLM's context window. Each tool costs **200-500 tokens**.
3. **Model decision**: LLM reasons and decides to call a tool.
4. **Host routing**: Host identifies which client owns the tool, constructs JSON-RPC `tools/call` with `_meta` (protocol version, capabilities).
5. **Wire transport**: Client sends over stdio (local) or Streamable HTTP (remote). HTTP includes `Mcp-Method` and `Mcp-Name` headers for gateway routing without body parsing.
6. **Server execution**: Server validates input against schema, executes, returns result.
7. **Response delivery**: Result flows back through client to host. Host feeds into LLM context.
8. **Multi-round (MRTR)**: If server needs more info, returns `resultType: "input_required"` with opaque `requestState`. Client collects answers, retries with `inputResponses`.

### Deep Agents MCP Integration Pattern

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
from deepagents import create_deep_agent

async with MultiServerMCPClient({
    "github": {"transport": "http", "url": "http://localhost:8001/mcp"},
    "jira":   {"transport": "http", "url": "http://localhost:8002/mcp"},
}) as client:
    tools = await client.get_tools()
    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        tools=tools,  # additive: MCP tools join built-in FS tools
    )
```

---

## Part 2: Core Mechanics & Algorithms

### Three MCP Server Primitives

| Primitive | Controlled By | Discovery | Execution | Purpose |
|-----------|--------------|-----------|-----------|---------|
| **Tools** | Model | `tools/list` | `tools/call` | Executable functions (the workhorse) |
| **Resources** | Application | `resources/list` | `resources/read` | Read-only data addressed by URI |
| **Prompts** | User | `prompts/list` | `prompts/get` | Parameterized instruction templates |

Resources and prompts are usually **not** auto-bound as agent tools. Elicitation (`accept`/`decline`/`cancel`) must come from a **human UI** -- auto-accept forges content.

### Transport Layers

| Transport | How | When |
|-----------|-----|------|
| **Stdio** | Host spawns server as child process. JSON-RPC over stdin/stdout | Local servers. No network overhead. stdout exclusively for protocol messages |
| **Streamable HTTP** | HTTP POST + optional SSE. `Mcp-Method`/`Mcp-Name` headers | Remote servers. OAuth 2.1 auth. Spec 2025-03-26, retained 2025-11-25 |
| **SSE (deprecated)** | Original transport from 2024-11-05 | Formally deprecated 2026-07-28 with 12-month removal window |

### Capability Negotiation -- Stateful vs Stateless

**Pre-2026-07-28 (Stateful)**: `initialize` -> server responds with capabilities -> `initialized` notification -> session begins. State tied to connection; sticky sessions required.

**2026-07-28+ (Stateless)**: No handshake. Each request carries everything in `_meta` (protocolVersion, clientInfo, clientCapabilities). Any server instance behind round-robin. Serverless deployments viable.

### MultiServerMCPClient Session Model

| Setting | Default | What Happens |
|---------|---------|-------------|
| Stateless (default) | Fresh `ClientSession` per `tools/call` | Easy but breaks stateful servers. `handle_tool_errors=True` maps `isError=True` -> `ToolMessage(status="error")`, not TCP errors |
| Stateful | `async with client.session("server") as session` + `load_mcp_tools(session)` | Persistent session for servers that keep context. Scoped to the run |

**Transport/session/content-conversion failures always raise.** `handle_tool_errors=True` only maps semantic `isError=True` -> `ToolMessage(status="error")` so the model can inspect and recover. It does **not** retry TCP errors unless an interceptor catches them.

| Constructor Arg | Default | Role |
|----------------|---------|------|
| `connections` | -- | `dict[name, Connection]` with transport config |
| `tool_interceptors` | `None` | Onion around `tools/call`; first interceptor is outermost |
| `tool_name_prefix` | `False` | `"server_tool"` against collisions (`"math_add"`) |
| `handle_tool_errors` | `True` | `isError=True` -> `ToolMessage(status="error")` instead of raise |
| `callbacks` | `None` | Progress, logging, elicitation |

### Tool Interceptors -- The MCP PDP Bridge

MCP servers cannot see LangGraph runtime context. Interceptors bridge that gap:

```python
# Interceptor receives request with runtime context
# request.runtime.context -> user_id, tenant
# request.runtime.store -> LangGraph store
# request.runtime.state -> graph state
# request.override(args=..., headers=...) -> modify before send
# return Command(update=..., goto=...) -> modify graph state
```

Use cases: inject user IDs / API keys from runtime context, add rate limiting, DLP on args, retry/short-circuit, read from store. **Do not pass the user OAuth token through** -- mint an MCP-audience token. The official `headers={"Authorization": "Bearer ..."}` example is a static bearer anti-pattern.

### MCP vs Function Calling vs A2A

These are complementary layers, not competitors:

| Layer | Protocol | Direction | Purpose |
|-------|----------|-----------|---------|
| Foundation | Function Calling | LLM -> Host code | LLM emits structured args; host executes |
| Vertical | MCP | Agent -> Tools/Data | Standardized tool/data access |
| Horizontal | A2A | Agent <-> Agent | Peer-to-peer collaboration between deployments |

MCP does not replace function calling. Under the hood, MCP tools are converted into function-calling schemas. A2A standardizes agent-to-agent collaboration. Production stacks run both: A2A between agents, MCP from each agent to its tools.

### A2A JSON-RPC (Agent Server)

A2A is Google's (now Linux Foundation) protocol. Every LangSmith Deployment auto-exposes MCP + A2A. Disable via `langgraph.json` -> `"http": { "disable_a2a": true }`.

**Key identity mapping**:

| A2A | LangGraph |
|-----|-----------|
| `contextId` | **`thread_id`** -- must be a UUID. Server mints on first message; echo forever |
| `taskId` | One run inside the thread. New user turn = new task |
| Client `metadata.thread_id` | **Ignored** |

**Non-UUID `contextId` (e.g., `session-42`) returns `-32602`.** Completed `taskId` returns `-32004`. Foreign `taskId` returns `-32001`.

**Wire**: JSON-RPC **only** (gRPC and HTTP+JSON not implemented on Agent Server). Accepts v1.0 and v0.3 method names. `historyScope=context` (default) replays whole context; set `historyScope=task` to limit disclosure. `historyLength` max **10** (`-32602` if larger). Streaming ignores both with no error.

### ACP v1 (Agent Client Protocol)

ACP "standardizes communication between coding agents and code editors or IDEs" -- analogous to LSP. Protocol version: integer **1** (v2 is a draft).

| Property | Detail |
|----------|--------|
| Transport | Stdio JSON-RPC (remote HTTP = WIP) |
| Session methods | `session/new`, `session/list`, `session/resume`, `session/close`, `session/prompt`, `session/cancel` |
| Clients | Zed, JetBrains, VS Code, Neovim, Toad, Emacs, Obsidian, Cursor/Windsurf |
| Demo | `MemorySaver()` + `LocalShellBackend` on editor `cwd` -- **dies with subprocess, host blast radius** |
| Known bugs | **#5084**: process-wide cancel flag (cancelling A cancels B). **#4254**: no selectors without factory+modes/models |

**ACP is for agent-editor. MCP is for agent-tools. A2A is for agent-agent.** The editor is the TCB (Trusted Computing Base), **not** a Zero-Trust PEP.

### Claude Agent SDK vs Deep Agents Code (Sandbox Patterns)

| Axis | Deep Agents Code | Claude Agent SDK |
|------|-----------------|------------------|
| Loop location | Inside sandbox **or** outside using sandbox as tool | **Inside** sandbox only |
| Model | Any LangChain tool-calling provider (100+) | Claude only (Anthropic, Bedrock, Vertex) |
| Credential pattern | Auth proxy injects headers outside guest | Keys typically **in** the guest |
| Deployment | MDA on LangSmith or `langgraph build` image | Self-host HTTP/auth/streaming. Managed Agents is separate SKU |
| Multi-tenancy | Scoped threads, per-user sandboxes, RBAC | Build it yourself (`cwd` + `CLAUDE_CONFIG_DIR`) |

Comparison drafted **2026-04-16**. Named production users: OpenSWE, LangSmith Fleet. **Never nest both harnesses on one repo** -- use one inner harness and wrap the other as MCP/A2A peer.

### RAG on Deep Agents (Retrieve -> VFS -> Analysts)

Deep Agents does **not** ship a retriever or index. RAG is orchestration:

1. Your `@tool` searches a vector store (k=4)
2. `upload_files()` to `/retrieved/{batch_id}/chunk_{i}.md`
3. Tool returns **paths** (not full content)
4. Up to **3** `chunk-analyst` sub-agents `read_file` the chunks (max 300 words each)
5. Parent synthesizes

Tutorial numbers: 14 pages, 589,579 chars, 782 chunks, 20s/page fetch. Skipping `upload_files` reintroduces ~150k tokens into the parent -- the tutorial exists to avoid this. `permissions=` does not cover your custom search tool.

---

## Part 3: Token Economics & NFR Analysis

### Tool Definition Overhead

Each MCP tool definition consumes **200-500 tokens** in the context window. This is a fixed cost per request that never disappears.

| Setup | Token Overhead | Context Impact |
|-------|---------------|----------------|
| GitHub MCP server (93 tools) | 55,000 tokens | 28% of 200K window gone before user speaks |
| GitHub + Slack + Sentry (3 servers) | 143,000 tokens | 72% consumed idle |
| Typical production (5 servers x 30 tools) | 30,000-60,000 tokens | 15-30% permanently unavailable |
| 508-tool benchmark | 1,150,000 tokens | $377 per round in pure metadata |

Microsoft Research: large tool spaces lower agent performance by up to **85%**. Every 10K tokens of schemas removes ~5 pages of reasoning capacity.

### Optimization Strategies

| Strategy | Token Reduction | How |
|----------|----------------|-----|
| **Search-First Discovery** | 94% | Agent starts with 2-3 meta-tools; full schemas on-demand |
| **Code Execution Mode** | 98.7% | Single `execute_code` replaces full catalog (150K -> 2K) |
| **Tiered Schema Discovery** | 80-90% | Discovery tier (names only) vs invocation tier (full schema) |
| **Gateway Aggregation** | 94% | Cloudflare: 52 tools collapsed into 2 portal tools (~600 tokens) |
| **Cacheable List Results** (2026-07-28) | Variable | `tools/list` with `ttlMs` and `cacheScope` |

### Cost Per 1k Runs (Inferred)

Assumptions: Sonnet 4.6, 10 model calls in 5-minute window, GP off, 2k cached prefix (v0.7), 3k uncached/call, 800 output/call.

| Session Type | Token Sketch | USD/run | USD/1k |
|-------------|-------------|---------|--------|
| Chat harness 10-call, 5m cache | 2k prefix + 30k uncached + 8k out | $0.2229 | **$223** |
| Same + MCP schemas on prefix (~5k total) | 5k prefix, same uncached/out | $0.2422 | **$242** |
| Same + editor @file +2k uncached/turn | 5k prefix + 50k uncached + 8k out | $0.3022 | **$302** |
| `dcode` coding hour (`/cost` docs example) | Sonnet 4.5; $0.87+$0.16 subagents | $1.03 | **$1,030** |
| A2A 3-round ping-pong (6 hops) | 2 agents x 3 rounds = 6 full runs | $1.34 cached | **$1,337** |

Adapter overhead vs chat harness is ~$19/1k at a 3k MCP prefix -- not a protocol fee.

### MCP Financial Cost at Scale

At Sonnet pricing ($3/M input tokens), with 90K total schema overhead (3 servers):

| Daily Volume | Schema Overhead/Request | Monthly Overhead Cost |
|-------------|------------------------|----------------------|
| 100/day | $0.27 | $810 |
| 1,000/day | $0.27 | $8,100 |
| 10,000/day | $0.27 | $81,000 |

These are pure overhead -- query and response tokens are additional.

### Latency SLA Targets

| Path | p50 | p95 | p99 |
|------|-----|-----|-----|
| MCP tool call (local stdio) | <50ms | 300ms | 1,000ms |
| MCP Streamable HTTP (inferred) | 80ms | 400ms | 2,000ms |
| ACP stdio extra hop (inferred) | 80ms | 320ms | 1,280ms |
| ACP first model token (inferred) | 720ms | 2,880ms | 6,400ms |
| A2A `SendMessage` first-event (inferred) | 800ms | 3,200ms | 6,400ms |
| A2A one round, 2 deploys (inferred) | 40,000ms | 160,000ms | 400,000ms |

Bifrost gateway overhead: **11 microseconds** (Go-based, sub-3ms MCP latency, 5,000+ RPS).

### Availability & RPO/RTO

- **RPO stateless MCP (2026-07-28)**: Zero -- no protocol state to lose
- **RTO stateless MCP**: Next-request -- client retries, next server instance handles it
- **RPO A2A**: Last Agent Server checkpoint for that UUID `thread_id`
- **RPO ACP demo**: **Empty on subprocess death** (MemorySaver)
- 73% of production outages start at the transport layer
- 20-30% recovery failure rate without explicit retry handling

---

## Part 4: Distributed Resilience & Security

### Zero-Trust MCP

NSA advisory (May 2026) and industry guidance converge on treating every tool call as potentially unauthorized. `permissions=` **will not** save you -- MCP tools are additive `tools=` items outside the FS PDP.

| Control | Spec / Standard | On Deep Agents |
|---------|----------------|----------------|
| **Transport auth** | OAuth 2.1 + PKCE S256. RFC 8707 `resource` = canonical MCP server URI. Servers MUST accept only own-audience tokens. MUST NOT passthrough client token (mint via RFC 8693) | `headers=` static Bearer in docs is anti-pattern. Use `OAuthClientProvider` / gateway |
| **Server allowlist** | Only approved connections | Only put approved entries in `MultiServerMCPClient` |
| **Tool allowlist / prefix** | Least privilege | Filter `get_tools()`; `tool_name_prefix=True` against shadowing |
| **Hash-pin descriptions** | `toolSurfaceHash` over canonical JSON of name+description+inputSchema. Re-verify every `tools/call`. CVE-2025-54136 (MCPoison) CVSS 8.8 | Not in adapters. Pin in the **gateway**. Name filter is not hash pin |
| **Interceptor PDP** | Model proposes; PEP disposes | `runtime.context` user id; deny list; rate limit; short-circuit |
| **Identity** | Verified access token. Never the LLM | Bind from IdP into RunContext. `user_id` in model JSON is a proposal |

### Authorization Evolution

| Spec | Auth Model |
|------|-----------|
| 2024-11-05 | No standardized auth |
| 2025-03-26 | OAuth 2.1 + PKCE. Dynamic Client Registration |
| 2025-06-18 | Servers as OAuth Resource Servers. RFC 8707 + RFC 9728 PRM mandatory |
| 2026-07-28 | DCR deprecated for CIMD. RFC 9207 issuer validation. Credential binding. Stateless core |

**Enterprise-Managed Authorization (EMA)**: Employees access all MCP servers through single SSO (first: Okta). Centralized audit trail and policy enforcement.

### OWASP MCP Top 10

| ID | Risk | Mitigation |
|----|------|-----------|
| MCP01 | Token Mismanagement & Secret Exposure | Vault-backed rotation; never embed secrets in tool args |
| MCP02 | Excessive Permissions | Per-tool scope tokens; least privilege |
| MCP03 | Command Injection | Input validation + parameterized queries |
| MCP04 | Supply Chain Compromise | Pin versions; signed provenance; AIBOM |
| MCP05 | Tool Poisoning | Verify definitions against known-good manifests |
| MCP06 | Prompt Injection & Intent Flow | Separate system/user/tool context; output validation |
| MCP07 | Insufficient Authentication | OAuth 2.1 + PKCE mandatory; EMA |
| MCP08 | Audit & Logging Gaps | Log every tool call with correlation IDs |
| MCP09 | Shadow MCP Servers | Inventory all deployed servers; network controls |
| MCP10 | Context Over-Sharing | Gateway response filtering; output schema validation |

### CVE Landscape (Real Vulnerabilities)

| CVE | CVSS | Impact |
|-----|------|--------|
| CVE-2025-6514 (mcp-remote) | **9.6** | First real-world RCE on client OS via untrusted remote MCP server |
| CVE-2026-33032 (MCPwn) | **9.8** | Missing auth for command execution; 2,600+ exposed instances |
| CVE-2026-26118 (Azure MCP) | **8.8** | SSRF stealing managed identity tokens |
| CVE-2025-54136 (MCPoison) | **8.8** | Hallucinated/altered `tools/list` via poisoned tool definitions |

Broader: 40+ CVEs against MCP SDKs (Jan-Apr 2026). VIPER-MCP scanned ~40,000 repos, found 106 zero-days. Censys: 12,520 publicly exposed MCP services with ~40% unauthenticated. Only **8.5%** of remote servers implement mandatory OAuth 2.1 + PKCE.

### Tool-Level RBAC (What Exists vs What You Build)

| Control | What It Is | What It Is Not |
|---------|-----------|----------------|
| `permissions=` | Path glob PDP, fail-open, FS-tools-only | MCP, `execute`, `task`, PTC, `backend.*` |
| `excluded_tools` | Blunt name allowlist (+ execution block >=0.7.9) | Per-user roles |
| `interrupt_on` | Review queue on named tools (including MCP names) | Authorization PDP. PTC skips it |
| `A2A_ALLOWED_TOOL_CALL_RESULTS` | Which tool names become A2A DataParts | Authn |
| `@auth` (Agent Server) | Owner filter / 403 on threads | `contextId` format check |
| Gateway / Cedar / OPA | **The** MCP PEP | Not in `deepagents` |

### Circuit Breaker for MCP Servers

Track failures **per server** independently. One failing Jira should not block Salesforce.

| Trip Condition | Closed -> Open | Half-Open Probe | Fallback |
|----------------|---------------|-----------------|----------|
| MCP transport raise or hash drift | consecutive >= 3 in 60s window | One `tools/list` re-hash | **Disable those tools** -- agent continues on VFS and other MCP servers |
| A2A JSON-RPC 5xx / timeout | consecutive >= 5 | One `GetTask` or tiny `SendMessage` | **A2A -> direct invoke (same graph) -> refuse** |
| ACP stdio EOF / subprocess death | consecutive >= 3 | One `initialize` | **ACP -> HTTP/SDK invoke -> refuse** |

**Fallback chain**: ACP/A2A adapter -> direct `create_deep_agent` invoke (same graph) -> deterministic refuse. A2A peer down -> parent-only (subagents in-process). Never: circuit open -> LocalShell. Never: HITL timeout -> auto-approve. Never: A2A down -> skip `@auth`.

### PII Pipeline for MCP Args and A2A Payloads

MCP tool **arguments** are the exfil channel. A2A default history replays tool results. Scan sinks: MCP args, A2A payloads (including history replays), editor ACP logs, sandbox env.

1. **Detect**: Regex (email, PAN, SSN) + optional ML NER. Scan MCP args, A2A `SendMessage`/`GetTask` results, ACP `session/prompt` bodies. ML down: **block PAN into MCP args** and A2A publication.
2. **Redact**: Stable token hashes; block when field must not exist. `historyScope=task` reduces disclosure but is not redaction.
3. **Audit WORM**: Decisions, not values. `content_sha256` pre/post, entity types, action, detector, `correlation_id`, `contextId`=`thread_id`, surface.

---

## Part 5: Production Enterprise Code

### MCP Server with Tools, Resources, Validation

```python
"""Production MCP server for a customer data platform.
Tools + resources with input validation and audit logging.
"""
import json
import logging
from mcp.server.fastmcp import FastMCP

log = logging.getLogger("cdp-mcp")
mcp = FastMCP("customer-data-platform", version="1.0.0")

CUSTOMERS = {
    "C-1001": {"name": "Acme Corp", "tier": "enterprise", "arr": 240000},
    "C-1002": {"name": "Globex Inc", "tier": "mid-market", "arr": 48000},
}

@mcp.tool()
async def lookup_customer(customer_id: str) -> str:
    """Look up a customer by ID. Returns tier and ARR."""
    cid = customer_id.strip().upper()
    if not cid.startswith("C-") or not cid[2:].isdigit():
        return json.dumps({"error": "Invalid format. Expected C-NNNN."})
    customer = CUSTOMERS.get(cid)
    if not customer:
        return json.dumps({"error": f"Customer {cid} not found."})
    log.info("lookup_customer customer_id=%s found", cid)
    return json.dumps({"customer_id": cid, **customer})

@mcp.resource("cdp://schema/customers")
async def customer_schema() -> str:
    """Return customer data schema for LLM context grounding."""
    return json.dumps({"table": "customers", "columns": {
        "customer_id": "string (C-NNNN)",
        "name": "string", "tier": "enterprise|mid-market|startup",
        "arr": "integer (USD annual recurring revenue)",
    }})

if __name__ == "__main__":
    import sys
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if transport == "http":
        mcp.run(transport="streamable-http", host="0.0.0.0", port=8080)
    else:
        mcp.run(transport="stdio")
```

### Multi-Tenant MCP Gateway

```python
"""MCP gateway: per-tenant auth, tool scope, rate limiting, audit.
Architecture: Agent -> Gateway -> [Auth] -> [Rate Limit] -> [Scope] -> Server
"""
import json
import time
from dataclasses import dataclass, field
from typing import Any

@dataclass
class TenantConfig:
    tenant_id: str
    allowed_tools: set[str]
    rate_limit_per_minute: int
    server_url: str

@dataclass
class MCPGateway:
    tenants: dict[str, TenantConfig] = field(default_factory=dict)
    audit_log: list[dict[str, Any]] = field(default_factory=list)
    _calls: dict[str, list[float]] = field(default_factory=dict)

    def register(self, config: TenantConfig) -> None:
        self.tenants[config.tenant_id] = config

    async def route(self, tenant_id: str, tool: str, args: dict) -> dict:
        t0 = time.time()
        tenant = self.tenants.get(tenant_id)
        if not tenant:
            self._audit("auth_fail", tenant_id, tool)
            return {"error": "Unknown tenant", "code": 401}
        if tool not in tenant.allowed_tools:
            self._audit("scope_deny", tenant_id, tool)
            return {"error": f"Tool '{tool}' not permitted", "code": 403}
        # Rate limit check
        now = time.time()
        calls = self._calls.setdefault(tenant_id, [])
        self._calls[tenant_id] = [t for t in calls if now - t < 60]
        if len(self._calls[tenant_id]) >= tenant.rate_limit_per_minute:
            self._audit("rate_limited", tenant_id, tool)
            return {"error": "Rate limit exceeded", "code": 429}
        self._calls[tenant_id].append(now)
        # Forward to server (simplified)
        result = {"status": "ok", "tool": tool, "data": "executed"}
        self._audit("ok", tenant_id, tool, latency_ms=round((time.time()-t0)*1000, 1))
        return result

    def _audit(self, event: str, tenant: str, tool: str, **extra: Any) -> None:
        self.audit_log.append({"ts": time.time(), "event": event,
                               "tenant": tenant, "tool": tool, **extra})
```

### Deep Agents + MCP + Gateway Wiring

```python
"""
Production wiring: MCP tools through gateway, not raw connections.
pip install deepagents langchain-mcp-adapters langgraph-checkpoint-postgres
"""
from langchain_mcp_adapters.client import MultiServerMCPClient
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.permissions import FilesystemPermission
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore

DB_URI = "postgresql://user:pass@db:5432/agent_state"
checkpointer = PostgresSaver.from_conn_string(DB_URI)
store = PostgresStore.from_conn_string(DB_URI)

async def build_agent():
    # MCP tools loaded through gateway (not direct server connections)
    async with MultiServerMCPClient({
        "crm": {
            "transport": "http",
            "url": "https://mcp-gateway.internal/crm/mcp",  # gateway endpoint
            "headers": {"X-Tenant": "acme"},  # gateway resolves to tenant server
        },
    }) as client:
        mcp_tools = await client.get_tools()
        # Filter to allowed tools only (gateway also enforces, defense in depth)
        safe_tools = [t for t in mcp_tools if t.name in {"lookup_customer", "search"}]

        agent = create_deep_agent(
            model="anthropic:claude-sonnet-4-6",
            tools=safe_tools,  # additive: joins built-in FS tools
            backend=CompositeBackend(
                default=StateBackend(),
                routes={"/memories/": StoreBackend(
                    store=store,
                    namespace=lambda rt: (rt.server_info.user.identity, "mem"),
                )},
            ),
            permissions=[
                FilesystemPermission(operations=["read","write"],
                    paths=["/**/.env"], mode="deny"),
                FilesystemPermission(operations=["read","write"],
                    paths=["/workspace/**"], mode="allow"),
                FilesystemPermission(operations=["read","write"],
                    paths=["/**"], mode="deny"),
            ],
            checkpointer=checkpointer,
            store=store,
        )
        return agent
```

---

## Part 6: System Design Scenarios

### Scenario A: Enterprise Coding Assistant with MCP Governance

**Problem**: Fintech, 2,000 engineers, 15 teams. AI coding assistant needs code search, CI/CD, incident management, DB schemas. SOC 2: no cross-team data leakage, audit trail, tool revocation in <5 min. Target: <500ms p95, 99.9% availability, <$20K/month.

| Alternative | Verdict | Rationale |
|------------|---------|-----------|
| **Direct MCP connections (no gateway)** | Rejected | No centralized auth/audit. SOC 2 auditors reject. Revocation = config push to 2,000 machines |
| **Aggregation gateway with EMA** | **Selected** | Centralized Okta SSO. Per-team ACLs. Single audit stream. Tiered discovery: 2,400 tokens (vs 30K+). Revocation = ACL change in seconds |
| **Per-team dedicated servers** | Rejected | 15 teams x 5 servers = 75 deployments. $60K+/month. Cross-team tools require duplication |

**Decision**: Gateway with tiered schema discovery keeps token overhead under 3K tokens. EMA via Okta for single-pane access control. 3 gateway replicas for 99.9%. Total: ~$12K/month.

### Scenario B: Multi-Agent Fleet with A2A + Subagents

**Problem**: Research, billing, HR specialists. Research and coding in same trust domain. Billing in separate PCI zone. HR must not be a peer. Partners speak CrewAI/ADK A2A.

| Axis | Hybrid: subagents + A2A across zones (rec.) | A2A for everything | Subagents-only |
|------|---------------------------------------------|-------------------|----------------|
| **Cost** | In-process task + A2A only on billing hop | 2x tokens per ping-pong round | No extra HTTP; cannot reach other clusters |
| **Security** | Network + @auth only at zone boundary | Public cards; confused deputy risk | Same process, same credentials (wrong for PCI) |
| **Scale** | Parallel chunk analysis in-process; billing scales independently | One run per contextId per hop | Fan-out via task gather |

**Decision**: Subagents for private decomposition (RAG chunk-analysts); A2A only for billing in another compliance zone; `historyScope=task`; `disable_a2a` on HR.

---

## Common Failure Modes

| Failure | Cause | Mitigation |
|---------|-------|------------|
| Schema bloat kills accuracy | 93 GitHub tools = 55K tokens | Search-first discovery; tiered schemas; gateway aggregation |
| MCP token passthrough | Docs `headers` Bearer example | OAuth 2.1 + RFC 8707 audience; never copy user tokens |
| Tool poisoning / MCPoison | Altered `tools/list` (CVE-2025-54136) | Hash-pin tool definitions at gateway; re-verify every call |
| Stateless session breaks workflow | Default fresh session per call | `client.session()` for stateful servers |
| `permissions=` assumed to cover MCP | MCP tools are additive `tools=` | Gateway PEP is required; permissions is FS-only |
| A2A without identity | Missing `@auth`; public card | `@auth.authenticate` on both deploys; `@auth.on.threads` |
| Bad `contextId` | `session-42` instead of UUID | `-32602`; server-minted UUID; new taskId per turn |
| A2A history exfil | `historyScope=context` replays all tasks | `historyScope=task`; `A2A_ALLOWED_TOOL_CALL_RESULTS` |
| ACP MemorySaver in prod | Demo saver dies with subprocess | PostgresSaver; not MemorySaver |
| ACP cancel bleed | #5084: process-wide flag | One server per editor window until fix |
| Dual harness | Claude SDK `query()` + DA `execute` on one repo | One inner harness; other as MCP/A2A peer |
| MCP `handle_tool_errors` confusion | TCP still raises; `isError` does not hit exception interceptors | Interceptor retry on transport; semantic error goes to model |
| `structuredContent` invisible to model | Wrapped as artifact, not in context | Interceptor appends if needed; default keeps context smaller |

---

## Interview Q&A

**Q1: What are the three tool sources in Deep Agents?**
Custom callables and LangChain tools that I define in code; built-in harness tools like `read_file`, `glob`, `task`; and MCP-loaded tools fetched from external servers. They all land on the same `tools=` surface, but operationally they differ: local tools run in my process, built-ins are injected by middleware, MCP tools come from separate processes needing transport, auth, and session management.

**Q2: What is MCP in one sentence?**
A standardized JSON-RPC 2.0 protocol that lets any AI model talk to any external tool -- the USB-C for AI -- with three primitives: tools (model-controlled callable functions), resources (app-controlled read-only data), and prompts (user-controlled templates).

**Q3: How does `MultiServerMCPClient` work?**
It connects to one or more MCP servers, loads tool definitions, and returns LangChain-compatible tools. Default is **stateless** -- fresh `ClientSession` per `tools/call`. For stateful servers, use `client.session("server_name")` + `load_mcp_tools(session)`. `handle_tool_errors=True` maps semantic `isError=True` to `ToolMessage(status="error")` so the model can retry, but TCP errors still raise.

**Q4: Do permissions cover MCP tools?**
No. `permissions=` is a fail-open path PDP for built-in FS tools only. MCP tools are additive `tools=` items completely outside that PDP. Zero-Trust MCP is a gateway PEP: OAuth 2.1, RFC 8707 audience = canonical MCP server URI, no token passthrough, hash-pinned tool JSON. Identity from verified tokens, never from model JSON.

**Q5: Give me $ per 1k for MCP tool overhead.**
At 90K tokens of schema overhead (3 servers, typical): $0.27/request at Sonnet pricing. That is $8,100/month at 1,000 requests/day in pure schema cost. With search-first discovery, I cut to ~2,400 tokens -> $0.007/request -> $216/month. The 94% reduction is why gateway aggregation and tiered discovery are mandatory at scale.

**Q6: A2A `contextId` vs `taskId` vs ACP `session_id`.**
`contextId` is `thread_id` and must be a UUID -- `session-42` returns `-32602`. Server mints on first message; I echo it. `taskId` is one run inside that thread; new user turn = new task; completed IDs return `-32004`. ACP `session_id` is the editor session -- I map it to a UUID `thread_id` if I need a checkpointer. UUID is not authorization; `@auth.on.threads` is.

**Q7: Is editor ACP a Zero-Trust PEP?**
No. Stdio ACP makes the IDE the TCB -- it can log every frame. `permissions=` is still fail-open FS-only. MCP egress still needs a gateway. `--trust-project-mcp` and `class_path` are footguns. Ingress `/mcp` and `/a2a` need the same `@auth` as `/runs`. CLI allowlists do not replace that.

**Q8: Circuit breaker for MCP and A2A.**
Libraries do not ship protocol breakers. I wrap per-server: closed -> open -> half-open with one probe (`tools/list` re-hash for MCP, `GetTask` for A2A). MCP down: disable those tools, agent continues on VFS and healthy servers. A2A down: direct invoke of same graph, then refuse. Never fail-open to LocalShell. Never auto-approve HITL on timeout.

**Q9: Claude Agent SDK vs Deep Agents Code -- sandbox story?**
Claude SDK is agent-in-sandbox: loop inside the guest, keys usually there unless I add a proxy, N sessions = N processes. Deep Agents Code is sandbox-as-tool: loop on my machine, `read_file`/`execute` target remote sandbox, LangSmith auth proxy injects credentials outside guest. Comparison drafted 2026-04-16. I never nest both on one repo.

**Q10: When do I pick A2A vs subagents?**
Subagents for private decomposition in one process -- RAG chunk-analysts, coding subtasks, fresh child window, single handoff. A2A when the peer is another team, another framework, or another compliance zone. Hybrid is the enterprise default: subagents inside, A2A across zone boundaries, `historyScope=task`, tool-result allowlist, `disable_a2a` on HR.

**Q11: How does Deep Agents RAG work?**
It is orchestration, not a new index. My `@tool` searches my vector store (k=4). I `upload_files` chunks to `/retrieved/{id}/chunk_i.md` and return paths. Up to 3 chunk-analyst sub-agents `read_file` and summarize (<300 words each). Parent synthesizes. This avoids dumping ~150K tokens into the parent. Delimiters are not injection-proof. Permissions do not cover my custom search tool.

**Q12: PII pipeline for MCP and A2A.**
MCP tool arguments are the exfil channel. A2A default history replays tool results. I scan MCP args, A2A payloads, editor ACP logs, sandbox env. Detect with regex + ML; if ML down, block PAN into MCP args and A2A publication. Redact to stable hashes. Audit WORM of decisions -- pre/post sha256, entity types, action, cid, contextId=thread_id, surface. `historyScope=task` reduces disclosure but is not redaction.

---

## Key Numbers to Memorize

| Number | What |
|--------|------|
| **200-500 tokens** | Per MCP tool definition overhead |
| **55,000 tokens** | GitHub MCP server (93 tools) |
| **94%** | Token reduction via search-first discovery or gateway aggregation |
| **97M+** | MCP SDK monthly downloads mid-2026 |
| **8.5%** | Remote MCP servers implementing mandatory OAuth 2.1 + PKCE |
| **9.8 / 9.6 / 8.8** | CVE-2026-33032 / CVE-2025-6514 / CVE-2026-26118 CVSS scores |
| **$223 / 1k** | Chat harness 10-call cached (Sonnet 4.6) |
| **$242 / 1k** | Same + MCP schemas on prefix |
| **$1.03** | `dcode` /cost example (one thread) |
| **$8,100/mo** | Schema overhead at 1,000 requests/day with 90K tokens |
| **ACP protocol 1** | Wire `protocolVersion`; v2 is draft |
| **A2A v1.0 JSON-RPC** | + v0.3 method names. No gRPC/HTTP+JSON on Agent Server |
| **contextId = thread_id UUID** | `session-42` returns `-32602` |
| **historyLength max 10** | `-32602` if larger; streaming ignores |
| **#5084 / #4254** | ACP cancel bleed / missing selectors |
| **RFC 8707 / 8693** | MCP audience / no-passthrough exchange |
| **fail-open** | `permissions=` default (FS-only; does not cover MCP) |
| **2026-04-16** | Deep Agents vs Claude Agent SDK comparison drafted |

## Quick Reference

| When to use | Choose |
|-------------|--------|
| Agent -> external tools/data | MCP (vertical: tool access) |
| Agent <-> agent across deployments | A2A (horizontal: peer collaboration) |
| Editor <-> coding agent | ACP (stdio JSON-RPC, protocol v1) |
| LLM -> structured function execution | Function Calling (foundation layer) |
| Same trust domain decomposition | Subagents / `task` tool (in-process) |
| Cross-compliance-zone collaboration | A2A with `@auth` + `historyScope=task` |
| Multi-model portability needed | Deep Agents (any LangChain provider) |
| Claude-only, agent-in-sandbox | Claude Agent SDK |
