# Module 04: How MCP Works

### What Is This?

The Model Context Protocol (MCP) is a standard way for AI applications (like Claude, Cursor, or custom agents) to connect to external tools and data sources -- databases, SaaS APIs, file systems, code repositories. Think of it as USB-C for AI: before USB-C, every device had its own charger; now one cable fits everything. Without MCP, N AI apps times M data sources need N*M custom integrations. With MCP, you build N clients and M servers, and any client can talk to any server through one shared protocol. Launched by Anthropic in November 2024 and inspired by the Language Server Protocol (LSP), MCP defines a JSON-RPC 2.0 wire format, three primitives (Tools, Resources, Prompts), and two standard transports (stdio for local, Streamable HTTP for remote). It was donated to the Agentic AI Foundation (Linux Foundation) in December 2025, co-founded by Anthropic, Block, and OpenAI, with backing from Google, Microsoft, AWS, Cloudflare, and Bloomberg.

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
+---------------------------------------------------------------------------+
|                            CONTROL PLANE                                   |
|                                                                            |
|  +------------------+  +-----------------+  +---------------------------+ |
|  | Host              |  | Registry /       |  | OAuth 2.1 AuthZ Server   | |
|  | (Claude Desktop,  |->| Namespace        |->| (mints tokens; MCP      | |
|  |  Cursor, VS Code, |  | (verified server |  |  server validates,      | |
|  |  custom agent)    |  |  names; does NOT |  |  never issues)          | |
|  |                   |  |  code-scan)      |  |                         | |
|  | Spawns one Client |  +--------+---------+  +----------+--------------+ |
|  | per Server (1:1)  |           | discovery              | token issue   |
|  +--------+----------+           v                        |               |
|           |              +-----------------+              |               |
|           | tools/call   | Policy Engine    |<------------+               |
|           | resources/   | (OPA/Cedar PDP:  |                             |
|           | read         |  tool+argument   |                             |
|           |              |  RBAC per call)  |                             |
|           |              +--------+---------+                             |
+-----------+-----------------------+---------------------------------------+
            | authorized request    | scope-narrowed (RFC 8693 OBO)
+-----------v-----------------------v---------------------------------------+
|                    DATA PLANE  (MCP GATEWAY)                               |
|                                                                            |
|  +------------+  +--------------+  +-------------+  +------------------+  |
|  | Mcp-Method  |  | Per-Backend   |  | PII Detect  |  | Tool-Catalog    |  |
|  | /Mcp-Name   |  | Circuit       |  | -> Redact   |  | Cache           |  |
|  | Header      |->| Breaker       |->| -> Audit    |->| (ttlMs/         |  |
|  | Router      |  | (CLOSED ->    |  | (before     |  |  cacheScope,    |  |
|  | (no JSON-   |  |  OPEN ->      |  |  response   |  |  deterministic  |  |
|  |  RPC body   |  |  HALF-OPEN)   |  |  enters     |  |  order for      |  |
|  |  parse      |  |               |  |  model ctx) |  |  prompt cache)  |  |
|  |  needed)    |  |               |  |             |  |                 |  |
|  +------+------+  +------+-------+  +------+------+  +--------+--------+  |
+---------+----------------+----------------+-----------------------+--------+
          | stdio          | Streamable HTTP| Streamable HTTP       |
          | (local IPC,    | (~10ms/call,   | (SSE upgrade,         |
          |  ~0ms)         |  ~300 RPS)     |  resumable via        |
          |                |                |  Last-Event-ID)       |
+---------v----------------v----------------v-----------------------v--------+
|                       TOOL PROXIES (per MCP server)                        |
|                                                                            |
|  +-------------------+  +-------------------+  +------------------------+ |
|  | Sandbox Tier       |  | Server A: exposes  |  | Server B: different   | |
|  | (OS-level <10ms /  |  | Tools/Resources/   |  | vendor, different     | |
|  |  gVisor ~500ms /   |  | Prompts; may issue |  | sandbox tier,         | |
|  |  Firecracker       |  | reverse Sampling   |  | isolated blast        | |
|  |  ~125ms startup)   |  | calls              |  | radius from Server A  | |
|  +-------------------+  +---------+----------+  +-----------+-----------+ |
+----------------------------+------+--------------------------+-----------+
                             | backend I/O                     |
+----------------------------v---------------------------------v-----------+
|                         PERSISTENCE LAYER                                 |
|                                                                           |
|  +------------------+  +---------------------+  +---------+ +-----------+|
|  | Externalized      |  | Durable Workflow     |  | Token   | | Immutable ||
|  | EventStore        |  | Store (Temporal /    |  | Vault   | | Audit Log ||
|  | (Redis; SDK       |  | Dapr -- redelivers  |  | (short- | | (hash-    ||
|  |  default is in-   |  | pending activities   |  |  lived, | |  chained, ||
|  |  memory, 404s     |  | on restart)          |  |  scoped)| |  tool-call||
|  |  on restart)      |  |                      |  |         | |  level)   ||
|  +------------------+  +---------------------+  +---------+ +-----------+|
+------------------------------------+-------------------------------------|
                                     |
+------------------------------------v-------------------------------------+
|                    TELEMETRY / OBSERVABILITY                               |
|  Per-(client,server) P50/P95/P99 round-trip latency                      |
|  Circuit-breaker state dashboard | Token-tax meter per server             |
|  Shadow-MCP alerts | CVE/dependency-risk feed                            |
|  Chain-of-custody audit trail                                             |
|  W3C Trace Context (traceparent, tracestate, baggage in _meta)           |
+--------------------------------------------------------------------------+
```

### 1.2 Request-Flow Narrative

**Step 1 -- Host bootstraps control plane.** User connects a server (local binary or remote URL). The Host creates a dedicated MCP Client for that Server. **This 1:1 client-server pairing is the foundational isolation invariant.**

**Step 2 -- Capability negotiation.**
- **Pre-2026-07-28 (stateful)**: Client sends `initialize` with `protocolVersion`, client capabilities (`roots` / `sampling` / `elicitation` / `tasks`), and `clientInfo`. Server returns negotiated version, server capabilities (`tools` / `resources` / `prompts` / ...), `serverInfo`, optional `instructions`. Client sends `notifications/initialized`. Only negotiated capabilities may be used.
- **2026-07-28 (stateless)**: No handshake. Every request carries version + client capabilities in `_meta`. Any server instance behind a round-robin load balancer can handle any request. Optional `server/discover` RPC replaces init-time capability exchange.

**Step 3 -- Control plane authorization.** The registry confirms the target server's namespace-verified identity. The policy engine (OPA/Cedar) evaluates tool-and-argument-level RBAC for this specific call -- not just at connect time. An On-Behalf-Of token exchange (RFC 8693) narrows the caller's scope to exactly what this hop needs.

**Step 4 -- Gateway routing.** `Mcp-Method`/`Mcp-Name` HTTP headers let the router dispatch, authorize, and rate-limit without parsing the JSON-RPC body -- a deliberate performance optimization added in 2026-07-28.

**Step 5 -- Tool discovery (`tools/list`).** Client requests the tool catalog (JSON Schema `inputSchema` per tool). Dynamic discovery means adding a server tool does not require host code changes. Host merges schemas into the model's tool prompt. Prefer `listChanged` notifications over blind re-polling.

**Step 6 -- Model turn (outside MCP framing).** Host builds context with tool schemas. LLM emits a tool call. Spec requires human consent before invocation; annotations are untrusted unless the server is trusted.

**Step 7 -- Tool execution (`tools/call`).** Client issues `tools/call` with name + arguments through the gateway. Gateway checks a per-backend circuit breaker (not per-tool -- multiple tools share one backend API), then applies PII detect-redact-audit to the tool response before it enters the model's context window. Server validates schema; unknown/malformed calls return JSON-RPC error; business failures return `isError: true` so the model can self-correct.

**Step 8 -- Observe and loop.** Result (`content` / `structuredContent`) returns to host, appends to model context, next turn. Timeouts + `CancelledNotification` on expiry; progress MAY soft-extend but a max timeout SHOULD remain.

**Step 9 -- Shutdown.** Transport-specific: stdio closes stdin / SIGTERM / SIGKILL; HTTP closes connections or sends DELETE (pre-2026-07-28). No dedicated shutdown RPC.

**Step 10 -- Audit.** Regardless of outcome, the gateway writes an immutable, tool-call-level audit record before considering the request complete.

### 1.3 Participants and Two Layers

| Role | Responsibility |
| --- | --- |
| **Host** | User-facing LLM app (Claude Desktop/Code, VS Code, Cursor, custom agents). Orchestrates UX, consent, creates one client per server. Is the trust boundary. |
| **Client** | Protocol connector inside the host. Maintains a dedicated 1:1 session with exactly one server. Handles capability negotiation, session lifecycle, reconnections. |
| **Server** | Bridges MCP protocol to real-world systems (databases, SaaS APIs, file systems). Translates `resources/read` into SQL `SELECT`, etc. Can run locally (subprocess) or remotely (cloud service). |

**Two layers:**
1. **Data layer** -- JSON-RPC 2.0 semantics: lifecycle, tools/resources/prompts, client features (sampling/elicitation/roots in `2025-11-25`), notifications, utilities.
2. **Transport layer** -- Connection framing + auth: stdio vs Streamable HTTP. Custom transports are allowed if they preserve JSON-RPC format and lifecycle.

---

## 2. Core Mechanics & Algorithms

### 2.1 JSON-RPC 2.0 Message Types

All messages MUST be JSON-RPC 2.0 UTF-8.

| Type | Key Fields | Notes |
| --- | --- | --- |
| **Request** | `jsonrpc`, `id` (string or number, not null), `method`, optional `params` | Bidirectional; IDs MUST NOT reuse within a session |
| **Result** | matching `id`, `result` | Success path |
| **Error** | matching `id`, `error.{code,message,data?}` | Protocol failures |
| **Notification** | `method`, optional `params`, **no** `id` | One-way; no response expected |

**Error codes:**

| Code | Source | Meaning |
| --- | --- | --- |
| `-32602` | JSON-RPC | Invalid params (unknown tool, missing args) |
| `-32603` | JSON-RPC | Internal error |
| `-32002` | MCP (2025-11-25) | Resource not found (renumbered `-32602` in 2026-07-28) |
| `-1` | MCP | User rejected request (sampling) |
| `isError: true` | Tool result | Tool execution error (actionable by LLM for self-correction) |

**Critical distinction**: Protocol errors (JSON-RPC `error` field) indicate malformed requests or server failures. Tool execution errors (`isError: true` in `result`) indicate the tool ran but failed -- the LLM can read these and self-correct. This two-tier error model is fundamental to how agents recover from tool failures without human intervention.

### 2.2 Lifecycle State Machine (2025-11-25)

```
     +------------+
     | DISCONNECTED|
     +------+-----+
            | open transport
            v
     +------------+     version mismatch -> disconnect / error
     | INITIALIZE |------------------------------------------+
     | client->srv|                                          |
     +------+-----+                                          |
            | InitializeResult                               |
            v                                                |
     +------------+                                          |
     | INITIALIZED|  notifications/initialized               |
     +------+-----+                                          |
            |                                                |
            v                                                v
     +------------+                                   +----------+
     | OPERATION  |<-- tools/list, tools/call, ... ---| FAILED   |
     | (negotiated|    progress / cancel / errors     | / CLOSE  |
     |  caps only)|---------------------------------->+----------+
     +------+-----+
            | transport shutdown
            v
     +------------+
     |  SHUTDOWN  |
     +------------+
```

**2026-07-28 stateless lifecycle:**

```
     Client                         Server
       |                               |
       |---- Any RPC (tools/call, etc)->|
       |     _meta: {                   |
       |       protocolVersion,         |
       |       clientId,                |
       |       capabilities             |
       |     }                          |
       |                               |
       |<--- Result -------------------|
       |                               |

  No handshake. No session ID. Any server instance behind
  a round-robin load balancer can handle any request.
  Optional: server/discover RPC for capability info.
  Cross-call state: server mints handle, model passes it back.
```

### 2.3 Version Negotiation and Capability Table

**Version negotiation**: Client proposes (SHOULD be latest supported). Server echoes if supported, else returns another it supports; client SHOULD disconnect on mismatch. Over HTTP, subsequent requests MUST carry `MCP-Protocol-Version`; missing header SHOULD be treated as `2025-03-26`.

**Capability table (2025-11-25):**

| Side | Capability | Purpose |
| --- | --- | --- |
| Client | `roots` | Expose filesystem boundaries (`roots/list`) |
| Client | `sampling` | Server-requested LLM completions (`sampling/createMessage`) |
| Client | `elicitation` | Server-requested user input (`elicitation/create`; form/url) |
| Client | `tasks` | Task-augmented client requests |
| Server | `tools` / `resources` / `prompts` | Core server primitives |
| Server | `logging` / `completions` / `tasks` | Logs, autocomplete, tasks |
| Either | `experimental` | Non-standard features |

Sub-flags: `listChanged`, `subscribe`.

### 2.4 The Three Primitives

```
+-------------+------------------+------------------+--------------+
| Primitive   | Direction        | Discovery        | Controller   |
+-------------+------------------+------------------+--------------+
| Tools       | Server -> Client | tools/list       | Model        |
| Resources   | Server -> Client | resources/list   | Application  |
| Prompts     | Server -> Client | prompts/list     | User         |
+-------------+------------------+------------------+--------------+
| Sampling*   | Client <- Server | sampling/create  | Server       |
| Elicitation | Client <- Server | elicitation/req  | Server       |
+-------------+------------------+------------------+--------------+
  * Sampling deprecated in 2026-07-28, replaced by MRTR pattern
```

**Tools (model-controlled):** Side-effecting functions. `tools/list` returns catalog with JSON Schema `inputSchema` (JSON Schema 2020-12). Optional `outputSchema` for `structuredContent` validation. Tool names SHOULD be 1-128 chars, case-sensitive, `[A-Za-z0-9_.-]`, unique per server. Tool results can contain text, images (base64), audio (base64), resource links, embedded resources, or structured JSON. Dynamic discovery via `notifications/tools/list_changed`.

**Tool annotations (untrusted unless from verified server):**

| Annotation | Semantics |
| --- | --- |
| `readOnlyHint` | Tool does not modify state |
| `destructiveHint` | Tool may irreversibly modify state |
| `idempotentHint` | Safe to retry without side effects |
| `openWorldHint` | Tool interacts with external/open systems |

These inform host UX decisions (e.g., skip confirmation for read-only from trusted servers) but must never be trusted blindly -- they are self-reported.

**Resources (application-controlled):** URI-addressed readable context (text or base64 blob). URIs follow RFC 3986 (`file://`, `https://`, `git://`, custom schemes). Support URI templates (RFC 6570). Pagination via `cursor`/`nextCursor`. Subscriptions: `resources/subscribe` -> `notifications/resources/updated` on change. Annotations: `audience`, `priority` (0.0-1.0), `lastModified`. Not for arbitrary mutation.

**Prompts (user-controlled):** Parameterized message templates, often exposed as slash commands in UX. Each has `name`, `description`, `arguments` (with required flag). Returns array of `PromptMessage` with role and content.

### 2.5 Client Features

**Sampling** (`sampling/createMessage`): Server asks the host LLM for a completion without embedding provider API keys in the server. Supports text/image/audio; model preferences via `costPriority`/`speedPriority`/`intelligencePriority` (0-1) plus advisory `hints` (substring-matched model names). Supports multi-turn tool loops within sampling. HITL SHOULD review prompts/responses. **Deprecated in 2026-07-28** (SEP-2577, 12-month minimum support window); replaced by Multi Round-Trip Requests (MRTR).

**Roots** (`roots/list`): `file://` workspace boundaries. **Deprecated in 2026-07-28** -- pass paths via tool args / resource URIs / config instead.

**Elicitation** (`elicitation/create`): Server requests user input mid-flow.
- **Form mode**: Flat JSON Schema (string/number/boolean/enum); MUST NOT request passwords/API keys/tokens/payment credentials.
- **URL mode**: Out-of-band navigation for sensitive flows; only the URL is exposed to the MCP client.
In 2026-07-28, delivery moves to MRTR: server returns `resultType: "input_required"` + `inputRequests`; client retries with `inputResponses`.

### 2.6 Transports

**stdio:**
- Client launches server as subprocess; newline-delimited JSON-RPC on stdin/stdout (no embedded newlines).
- Logging MAY go to stderr; stdout MUST be MCP-only.
- Network overhead: ~0ms (pure IPC). Cold start: TypeScript ~80ms faster than Python.
- One client per process; no built-in auth (OS process isolation). No built-in reconnection -- subprocess death requires client relaunch.
- Clients SHOULD support stdio whenever possible (lowest overhead, local-only).

**Streamable HTTP (replaces deprecated HTTP+SSE from 2024-11-05):**
- Single MCP endpoint: POST (and optional GET for SSE listen).
- Client POST MUST `Accept: application/json, text/event-stream`.
- Response: one JSON object or SSE stream (may include server-initiated messages before final response).
- Optional session: server MAY return `Mcp-Session-Id` on `InitializeResult`; client MUST echo. DELETE ends session; 404 -> re-`initialize`.
- SSE resumability: event `id` + `Last-Event-ID` on GET for message replay. `retry` field (ms) before closing.
- Security: validate `Origin` (403 on invalid); bind local servers to `127.0.0.1`; authenticate connections.

**Deprecated HTTP+SSE comparison:**

| Metric | Streamable HTTP | HTTP+SSE (deprecated) |
| --- | --- | --- |
| Endpoints | Single (/mcp) | Two (GET SSE + POST) |
| Throughput | ~290-300 RPS | 7-30 RPS |
| Resumability | Last-Event-ID replay | None |
| Infrastructure | Stateless-compatible | Long-lived per-client SSE |
| Latency | ~10ms/call | Hundreds of ms under load |

### 2.7 Dynamic Discovery and Subscription

```
  Server tool set changes
           |
           v
  notifications/tools/list_changed -------> Client
                                              |
                                              v
                                       Client re-fetches
                                       tools/list (paginated)
                                              |
                                              v
                                       Host re-injects updated
                                       schemas into LLM context

  Resource subscriptions:
  Client --- resources/subscribe(uri) -----> Server
  Server --- notifications/resources/updated --> Client (on change)
```

### 2.8 Spec Evolution Timeline

| Date | Version | Key Change |
| --- | --- | --- |
| Nov 2024 | `2024-11-05` | Initial release. HTTP+SSE transport. |
| Mar 2025 | `2025-03-26` | Streamable HTTP replaces SSE. stdio formalized. |
| Nov 2025 | `2025-11-25` | OAuth 2.1. Tasks. Elicitation. Structured output. Audio. Tool annotations. |
| Jul 2026 | `2026-07-28` | **Stateless core.** Sessions removed. MRTR replaces Sampling/Elicitation/Roots. `Mcp-Method`/`Mcp-Name` headers. Cacheable lists (`ttlMs`/`cacheScope` + deterministic order). Extensions framework. CIMD replaces DCR. |

**2026-07-28 key changes in detail:**
- Removes `initialize`/`notifications/initialized`; every request self-describing via `_meta`
- Mandatory `server/discover`
- Sessions/`Mcp-Session-Id` removed
- Roots/Sampling/Logging deprecated
- MRTR for elicitation
- Cacheable list results for prompt-cache optimization
- RFC 9207 issuer validation; CIMD replaces DCR
- Extensions identified by reverse-DNS IDs, negotiated through capabilities, versioned independently
- 12-month minimum deprecation window for removed features

### 2.9 Invariants and Complexity

| Invariant | Binding |
| --- | --- |
| 1:1 client-server | Foundational isolation; one client per connected server |
| Capability gate | Only negotiated features usable after initialize |
| Request ID uniqueness | No reuse within session |
| Two-tier errors | Protocol error vs `isError: true` for model self-correction |
| Consent | Explicit user consent before tool invoke |
| Disconnect != cancel | Client MUST send explicit `CancelledNotification` |

**Tool selection complexity**: Catalog exposure of T tools costs O(T) schema tokens per turn. Selection accuracy degrades past ~10-15 visible tools (Haiku drops below 90% at 15). At thousands of tools, retrieval + Top-k is mandatory.

| Visible Tools | Haiku Accuracy | Sonnet Accuracy | Notes |
| --- | --- | --- | --- |
| 10 | **91%** @ 245 ms median | **95%** @ 410 ms median | Recommended max per context |
| 15 | **87%** (below 90%) | >= 90% | Haiku degrades significantly |
| 20-30 | -- | >= 90% until ~20; drops by 30 | Prefer scoped aggregation |

At cloud scale (Alibaba study, 3,616 tools): naive inlining infeasible (~506k tokens avg); Top-15 retrieval cuts to ~21k tokens while holding ~81.6% accuracy.

---

## 3. Token Economics & NFR Analysis

### 3.1 The Token Tax

The single most significant production cost of MCP. Tool schemas are injected into the LLM's context window, consuming tokens before any user interaction.

**Measured overhead from production data:**

| Metric | Value |
| --- | --- |
| Token inflation vs baseline chat | **2x - 30x** (across 9 LLMs) |
| Simple directory listing via MCP | **12x** more tokens than hard-coded function |
| MCP tool schema vs minimal schema | **5-15x** more tokens per tool |
| Per-tool context consumption | **~1,000 tokens/session** |
| 7 MCP servers | **67,300 tokens** (33.7% of 200k context) |
| Full MCP setup (many servers) | **143k of 200k tokens** (72% usage) |
| 20-30 registered tools | **15-30 KB** of context (schemas only) |
| Reasoning capacity cost (20 tools) | **~10,000 fewer tokens** for reasoning |

**Real-world example**: With 7 servers using 67,300 tokens for tool schemas alone, the model has lost a third of its context window before a single user message arrives. At 20 tools, the model has ~10,000 fewer tokens available for actual reasoning -- the equivalent of removing several pages of working memory.

### 3.2 Cost Formulas -- $ per 1k Conversations

MCP framing itself is free. The cost is the host LLM processing tool schemas and results.

**General formula:**

```
Per-conversation schema cost (first turn):
  cost_schema = S * T_s * ((1 - C_hit) * P_input + C_hit * P_cached) / 1,000,000

Per-conversation tool-result cost:
  cost_results = R * T_r * P_input / 1,000,000

Total:
  cost_conv = cost_schema + cost_results

Variables:
  S     = number of tool schemas injected (e.g., 20)
  T_s   = avg tokens per schema (~1,000)
  P_input = input token price ($/1M tokens)
  C_hit = prompt cache hit rate (0.0 - 1.0)
  P_cached = cached input price (typically 0.1x of P_input)
  R     = avg tool calls per conversation
  T_r   = avg tokens per tool result
```

**Worked Example A -- Mid-tier model, 10 tools, 4 calls/run (Grok model):**

```
S=10, T_s=80, C_base=2000, R=4, P_in=$3/1M, P_out=$15/1M
T_in ~ 2000 + 800 + 4*600 = 5,200 tokens
T_out ~ 4*80 + 200 = 520 tokens

Uncached: 1000 * [(5200*3 + 520*15) / 10^6] = $23.40 / 1k runs
With 70% cache (D_cache=0.1x): $13.57 / 1k runs
```

**Worked Example B -- Claude Opus, 20 tools, 75% cache hit (Opus model):**

```
S=20, T_s=1000, P_input=$15/1M, C_hit=0.75, P_cached=$1.50/1M

Uncached: 20,000 * 15 / 1,000,000 = $0.30/conversation = $300 / 1k
Cached:   20,000 * ((0.25 * 15) + (0.75 * 1.50)) / 1,000,000
        = 20,000 * 4.875 / 1,000,000 = $0.0975/conversation
        = $97.50 / 1k conversations
```

**Production validation** (22 days, 2,600 conversations):
- Per conversation: ~$0.15 uncached, ~$0.04 cached
- Total first-turn schema cost: ~$390 without cache, ~$100 with cache

### 3.3 Latency -- Transport and Production

**Transport overhead only** (minimal JSON-RPC echo, N=100 + 10 warm-up, framing only -- NOT LLM/tool work):

| Transport | Method | p50 | p95 | p99 |
| --- | --- | --- | --- | --- |
| stdio (local) | measured | **0.01 ms** | **0.02 ms** | **0.02 ms** |
| Streamable HTTP (loopback) | measured | **0.39 ms** | **0.45 ms** | **0.48 ms** |
| Streamable HTTP (same-region remote) | modeled = loopback + RTT | **~30.4 ms** | **~80.4 ms** | **~180.4 ms** |

**Finding: Network RTT dominates.** stdio vs HTTP encoding is irrelevant once a host boundary is crossed.

**Production latency targets:**

| Component | p50 | p95 | p99 |
| --- | --- | --- | --- |
| stdio round-trip | <1 ms | <2 ms | <5 ms |
| Streamable HTTP round-trip | ~10 ms | ~25 ms | ~50 ms |
| Gateway overhead (Bifrost) | **11 us** | 15 us | 20 us |
| Per tool call (production) | **50 ms** | 150 ms | **200 ms** |
| 10-tool agentic workflow | 0.5 s | 1.5 s | 2.0 s |
| Full task (5-15 tool calls) | 1.5 s | 3.5 s | 4.5 s |

**Inferred e2e `tools/call` latency budget (remote HTTP + SaaS tool):**

| Stage | p50 | p95 | p99 | Notes |
| --- | --- | --- | --- | --- |
| MCP framing (remote) | 30 ms | 80 ms | 180 ms | Transport measurement |
| Host consent / RBAC check | 5 ms | 15 ms | 40 ms | Local policy engine |
| Downstream tool I/O | 80 ms | 250 ms | 800 ms | Typical SaaS API |
| Result serialize + PII filter | 5 ms | 20 ms | 50 ms | Redaction pass |
| **Sum (budget)** | **120 ms** | **365 ms** | **1,070 ms** | Additive worst-case |

**Latency mitigations by tier:**

| Tier | Target | Mitigations |
| --- | --- | --- |
| **p50** | Keep framing far below model time | stdio for desktop; cache `tools/list`; truncate resource reads |
| **p95** | Cap remote tool I/O; parallelize | Deadlines per call; connection reuse; Top-k retrieval (fewer schema tokens -> faster model) |
| **p99** | Prevent long-tail cascades | Hard max timeout + cancel; circuit breaker; degrade to cached/deterministic fallback |

### 3.4 Throughput and Back-Pressure

| Transport | Sustained Throughput |
| --- | --- |
| Streamable HTTP (shared) | **~290-300 RPS** at 100% success |
| HTTP+SSE (deprecated) | 7-30 RPS (collapses under load) |
| stdio | Bound by subprocess throughput |
| Gateway (Bifrost) | **5k RPS** at 11 us overhead |

**Capacity formula:**

```
max_concurrent_agents = gateway_rps / (avg_tools_per_task * tasks_per_second)

Example: 300 RPS gateway, 10 tools/task, 1 task/agent/s
max_concurrent_agents = 300 / (10 * 1) = 30 concurrent agents
```

Spec requires per-request timeouts + `CancelledNotification`; progress MAY reset soft timers but max timeout SHOULD remain. Servers MUST rate-limit tool invocations.

**Back-pressure flow**: Reject/queue at host bulkhead -> cancel in-flight -> open circuit on error budget burn.

### 3.5 MCP vs Direct API/CLI

| Dimension | MCP | Direct API/CLI |
| --- | --- | --- |
| Discovery | Dynamic at runtime | Static, hard-coded |
| Token cost | **2-30x overhead** | Minimal (hand-picked) |
| Latency | +50-200 ms/call + protocol framing | Direct HTTP, no intermediary |
| Interoperability | Write once, any host | Bespoke per integration |
| Security | Standardized OAuth 2.1 | Per-app, inconsistent |
| Maintenance | Protocol handles versioning | Every integration reinvents |
| Scalability | Gateway centralized | Scaled independently |
| **Best for** | Dynamic tool sets, multi-agent, platform teams, audit needs | Known fixed tools, latency-critical, simple architectures |

**Benchmark**: CLI achieved **33% better token efficiency** and a 77 vs 60 task completion score compared to MCP for browser automation tasks. The gap was largest in multi-step debugging workflows where context budget ran out mid-task with MCP but not with CLI. Perplexity CTO publicly moved away from MCP (March 2026).

**Decision heuristic**: Use MCP when tool sets are dynamic, multiple AI hosts need the same integrations, or governance/audit requirements exist. Use direct APIs for known, fixed tool sets where latency and token efficiency matter.

### 3.6 Optimization Strategies

| Strategy | Savings | Mechanism |
| --- | --- | --- |
| Context trimming + KV-cache + parallel exec + progressive disclosure | Up to **91%** | Aggressive pruning of schema metadata |
| Bifrost Code Mode | **92.8%** input tokens | 3-4x fewer LLM round trips at 500+ tools; 40% faster |
| Tiered schema detail (proposed) | **~60%** | Discovery tier: name + one-liner only; full schema on-demand |
| Just-in-time MCP discovery | **~60%** | Fetch only schemas relevant to current task |
| Cacheable list results (2026-07-28) | Variable | `ttlMs` + `cacheScope` + deterministic order for prompt cache |

### 3.7 NFR Targets

| NFR | Target | Rationale |
| --- | --- | --- |
| **Availability** | 99.9% gateway / 99.5% per server | Multi-instance HTTP needs session affinity (2025-11-25) or stateless (2026-07-28). Bulkhead per upstream server. |
| **RPO** | 0 (immutable audit log) | Every tool call audit record persisted before completion |
| **RTO** | <60 s gateway failover / <5 s circuit open | SSE `Last-Event-ID` resumes streams only -- not tool-call history |
| **Compliance** | NSA/DoD MCP Security Design, OWASP MCP Top 10, CSA Best Practices | Protocol-level: no SOC2/HIPAA schema; implementor responsibility |
| **Audit** | Tool-call-level: who, what tool, params, policy decision, W3C Trace Context | Chain of custody keyed by correlation ID |
| **Token budget** | <20% of context window for tool schemas | Beyond 20%, reasoning quality degrades measurably |
| **Session recovery** | Externalized EventStore (not in-memory SDK default) with `Last-Event-ID` replay | SDK default in-memory store 404s on restart |
| **Recommended SLA** | P99 <5 s per agentic task / P99 <300 ms per tool call / Circuit breaker open <30 s | Production targets from gateway benchmarks |

### 3.8 Explicit Trade-Off: stdio vs Streamable HTTP

| Dimension | stdio (subprocess) | Streamable HTTP (remote) |
| --- | --- | --- |
| Isolation | Strong OS process boundary; local-only blast radius | Shared network surface; DNS rebinding / Origin risks |
| Reach | Same machine / IDE sidecar | Multi-tenant SaaS, cross-VPC tools |
| Latency overhead | ~0.01 ms p50 framing | Remote = RTT (tens-hundreds ms) |
| Auth | Env credentials; SHOULD NOT use OAuth | OAuth 2.1 / Bearer recommended |
| Ops | Process lifecycle + sandbox | LB, TLS, sessions/subscriptions, AS discovery |
| Scale-out | Vertical / many local processes | Horizontal (stronger after stateless revision) |

**Decision rule**: Default stdio for desktop/IDE secrets and lowest latency; HTTP when the tool must be shared across hosts/tenants -- then Zero-Trust + OAuth audience binding is mandatory.

---

## 4. Distributed Resilience & Security

### 4.1 Durable Execution: MCP is NOT Temporal

MCP defines transport + session + cancellation resilience -- not workflow replay. Resource subscriptions notify URI updates but do NOT event-source tool calls. Tasks (experimental -> extension `io.modelcontextprotocol/tasks`) add polling wrappers for long work, still not a full orchestrator.

**The MCP protocol itself is now stateless (2026-07-28). Durability lives OUTSIDE the protocol, in the orchestration layer wrapping tool calls.**

```
  MCP Tool Call (may fail mid-execution)
           |
           v
  +----------------------------------------------+
  | Durable Execution Layer (Temporal / Dapr)     |
  |                                                |
  |  +----------+    +-----------+    +----------+ |
  |  | Workflow  |--->| Activity  |--->| Event    | |
  |  | (idempot  |    | (tool call|    | Store    | |
  |  |  retry    |    | retried on|    | (WAL,    | |
  |  |  envelope)|    | failure)  |    | crash    | |
  |  |           |<---|           |<---| safe)    | |
  |  +----------+    +-----------+    +----------+ |
  |                                                |
  |  On restart: replay event history,             |
  |  resume from last completed activity           |
  +----------------------------------------------+
```

**How a host adds durability:**

| Concern | Host Pattern Above MCP |
| --- | --- |
| **Checkpoints** | LangGraph / Temporal / ADK session snapshots after each successful `tools/call` observation |
| **Idempotency** | Client-generated idempotency key in tool args / `_meta`; server dedupe store; retries only on idempotent reads |
| **Replay** | Workflow history replays host decisions; re-issues MCP calls with same key; do not assume server session memory survives |
| **Dead-letter** | Permanent `isError` / poison args -> DLQ + human review; do not infinite-retry side effects |
| **Locking** | Distributed lock around non-idempotent mutations (book/purchase) before `tools/call` |

### 4.2 Failure Taxonomy

Academic research analyzed **833 confirmed runtime fault threads** from 473 actively maintained MCP server repositories, producing **11 top-level categories, 27 subcategories, and 73 leaf fault types**. In a survey of 55 MCP server developers, respondents experienced an average of **20 of 27** fault subcategories.

**Five repeatable production failure modes:**

| # | Failure Mode | Symptom | Fix |
| --- | --- | --- | --- |
| 1 | **Transport flakiness under load** | LBs terminate SSE at 60s; agent sessions silently break; health checks pass | Externalize session state; health checks must validate active SSE streams, not just HTTP 200 |
| 2 | **Tool description drift** | Schemas change server-side; silent argument mismatches | `notifications/tools/list_changed` + client-side schema version tracking + validation on every call |
| 3 | **Schema mismatches across SDK versions** | Subtle JSON Schema interpretation differences cause validation failures | Pin SDK versions in lockfile; contract tests between client and server SDK versions |
| 4 | **OAuth refresh storms** | Hundreds of sessions hit token expiry simultaneously, overwhelm IdP | Jittered refresh at 80% of token lifetime with 10% random offset + single-flight lock on refresh |
| 5 | **Silent JSON-RPC hangs** | Server crashes mid-handler; no response; client blocks forever | Per-call timeout on every JSON-RPC request ID + circuit breaker per backend + child-process supervisor |

**Cascading failure pattern unique to AI agents**: LLM agents retry with slightly different parameters (unlike traditional APIs that fail fast), creating cascading failures that appear as success until downstream data is checked. Each retry hits the MCP server, which propagates to the already-struggling tool provider. **Fix**: Per-session retry budgets with proper backoff. After N failures of the same capability within a time window, circuit-break and surface failure to the orchestration layer. Never implement retry logic in the model prompt.

**Context overflow**: Every MCP tool call adds to prompt context. With 10 servers each returning 500-2,000 tokens, 20,000 tokens consumed by tool context alone. LLMs exhibit attention decay on longer contexts.

**Missing protocol-level primitives** (force every deployment to reinvent): identity propagation, adaptive tool budgeting, structured error semantics.

**Failure classification for retry policy:**

| Category | Examples | Action |
| --- | --- | --- |
| **Transient** | Network blip, 429/503, SSE drop | Retry with exponential backoff + jitter; SSE `Last-Event-ID` resume |
| **Permanent** | Unsupported version (`-32602`), auth audience fail, 400 bad request | Fail closed; do not retry |
| **Business/model-recoverable** | Schema-ok but domain reject -> `isError: true` | Return text to model; budgeted model retry |
| **Poison pill** | Repeated identical failing args; hallucinated tool name | Cap retries; quarantine tool; DLQ after N attempts |
| **Security** | Confused deputy, token passthrough, prompt injection via results | Deny; audit; revoke tokens |
| **Cascade** | Nested sampling tool loops; cascading timeouts | Iteration limits; propagate remaining deadline host->MCP->downstream |

### 4.3 Circuit Breaker State Machine

```
                   success
            +-----------------+
            |                 |
            v                 |
       +---------+      +-----+------+      +----------+
  ---->| CLOSED  |----->|   OPEN     |----->| HALF-OPEN |
       | (normal)| fail | (reject    | timer| (probe 1) |
       +---------+ thr  | all)       | exp  +-----+-----+
            ^            +-----+-----+            |
            |                  ^                   |
            |    success       |      failure      |
            +------------------+-------------------+

  **CRITICAL**: Circuit breakers are per-backend-dependency,
  NOT per-tool. Multiple tools share one backend API.
  Opening per-tool would leave other tools hitting the
  same failing backend.
```

**Fallback chains:**
1. **Primary** remote MCP server (full tool set)
2. **Secondary** regional replica / read-only Resource Gateway
3. **Deterministic fallback** -- cached last-good `tools/list` + stub "degraded" tool results or human escalation (no invented side effects)

**Architectural patterns (field catalog):**

| Pattern | Best When | Risk |
| --- | --- | --- |
| Stateful Session Server | Multi-step edits needing server memory | Drift / affinity; conflicts with stateless revision |
| Proxy Aggregator | Many upstream MCP servers | Tool-name collisions -> namespace (`ns__tool`); static merge blows tool budget |
| Tool Orchestrator | Multi-system workflows as one tool | Less reusable sub-steps; partial failure inside composite |
| Resource Gateway | Read-heavy context (docs, schemas) | Extra hop |
| Domain Adapter | Ugly third-party APIs | Adapter churn on API changes |

**Anti-patterns**: God Tool (`do_anything(action, params)` -- collapses selection accuracy); Unsanitized Resource Content (enables prompt injection).

### 4.4 Zero-Trust MCP Security

**OAuth 2.1 requirements (formalized Nov 2025):**
- Mandatory PKCE S256 for authorization code flow
- RFC 9728 for metadata discovery
- RFC 8707 Resource Indicators (`resource` parameter) -- audience-bind tokens to the specific MCP server URI
- Token passthrough is an explicit anti-pattern: MCP servers MUST NOT accept tokens not issued for that specific server
- MUST NOT place tokens in query strings; servers MUST validate audience
- Scope strategy: prefer challenged `scope`, else `scopes_supported`, least privilege / step-up for increments

**2026-07-28 auth hardening:**
- RFC 9207 issuer validation (clients MUST validate `iss` before redeeming codes)
- `application_type` in DCR for desktop/CLI (prevents rejection of localhost redirects)
- Credential binding to issuer (reuse across authorization servers forbidden)
- DCR deprecated in favor of Client ID Metadata Documents (CIMD)

**Transport security:**
- Servers MUST validate `Origin` header (prevent DNS rebinding); invalid = 403
- Local servers SHOULD bind only to `127.0.0.1`, not `0.0.0.0`
- Session IDs (pre-2026-07-28) MUST be globally unique and cryptographically secure
- Clients must handle session IDs securely to prevent session hijacking

**HITL mandates (protocol-level):**
1. Users must explicitly consent to data access and tool invocation
2. Hosts must prompt for confirmation before invoking tools (especially destructive ones)
3. Sampling requests presented for user approval
4. Clear UI showing exposed tools with visual indicators on invocation

### 4.5 Known Threat Landscape

| # | Threat | Description |
| --- | --- | --- |
| 1 | **Tool Poisoning** | Malicious instructions embedded in tool metadata/descriptions. Appear benign to users, execute when agent reads metadata. Persist across sessions. |
| 2 | **Confused Deputy** | Proxy server attack: malicious client obtains victim's auth through what appears as legitimate consent screen (MCP proxy + static shared client ID + consent cookies). |
| 3 | **Supply Chain** | Compromised npm packages. Documented: fake Postmark MCP server -- **15 clean releases before adding exfiltration code** (September 2025). |
| 4 | **Prompt Injection via Tool Results** | Tool results injected back into LLM context can manipulate agent behavior if not sanitized. |

### 4.6 RBAC Model

Fine-grained tool RBAC is NOT a first-class MCP object. Enforce via layered approach:

1. **Capability negotiation** (coarse feature gate)
2. **OAuth scopes / step-up** for dangerous tools
3. **Host allowlists/denylists** per tenant/role
4. **UI consent** before each side-effecting `tools/call`
5. **OPA/Cedar policy engine** for per-call evaluation

```
  Gateway Policy Engine (OPA / Cedar)

  Rule: allow(principal, action, resource)

  Example:
  permit(
    principal in Role::"analyst",
    action == "tools/call",
    resource.tool_name == "query_db"
  ) when {
    resource.args.table in ["sales", "products"]  AND
    resource.args.operation == "SELECT"
  };

  Evaluated on EVERY tools/call, not just at connect.
```

### 4.7 PII Pipeline: Detect -> Redact -> Audit

```
  Tool response from MCP server
           |
           v
  +------------------+
  | PII DETECT        | (NER / regex / Presidio)
  | (SSN, email,      |
  |  phone, CC, etc.) |
  +--------+---------+
           | detected entities
           v
  +------------------+
  | REDACT            | (replace with [PII:email:3f2a], etc.)
  +--------+---------+
           | redacted response
           v
  +------------------+
  | AUDIT LOG         | (log what was redacted, correlation ID,
  |                   |  original hash for forensics, immutable)
  +--------+---------+
           | clean response
           v
  Model context window (no PII enters LLM)
```

Elicitation: never request passwords/API keys in form mode -- use URL mode only.

### 4.8 Audit and Governance

- **W3C Trace Context**: `traceparent`, `tracestate`, `baggage` in `_meta` (SEP-414 in 2026-07-28) for distributed trace correlation across SDKs and gateways
- **NSA/DoD** joint MCP Security Design guidance (May 2026)
- **OWASP MCP Top 10** project -- first industry-standard framework for classifying MCP risks
- **Cloud Security Alliance** Agentic MCP Security Best Practices Guide
- **Agentic AI Foundation** (Linux Foundation, Dec 2025) -- Anthropic, Block, OpenAI co-founders + Google, Microsoft, AWS, Cloudflare, Bloomberg
- **SEPs** (Specification Enhancement Proposals) for protocol changes
- **12-month minimum** deprecation window for removed features
- **Extensions framework** (2026-07-28): extensions identified by reverse-DNS IDs, negotiated through capabilities, versioned independently

**Immutable audit logs**: Append-only store (WORM object lock / hash-chained log) for: initialize outcomes, capability sets, every `tools/call` (name, arg hash, result hash/`isError`, latency), consent decisions, circuit state transitions, PII redactions. Chain-of-custody for agent decisions sits in the host audit stream keyed by correlation ID -- not inside MCP framing.

**Phased enterprise implementation:**

| Phase | Timeline | Activities |
| --- | --- | --- |
| Day 0-30 | Foundation | Inventory + basic audit logging + secret management |
| Day 30-90 | Gateway | Gateway architecture + approval processes |
| Day 90-180 | Governance | Complete identification pipelines + governance frameworks |

### 4.9 Enterprise Adoption and Gateway Landscape

**Adoption numbers (2026):**
- **78%** of production AI teams use MCP
- **9,400+** servers in public registry
- **1B+** total SDK downloads (TypeScript + Python)
- **~500M/month** combined downloads

**42 gateways on the market** in three categories:

| Category | Examples | Key Feature |
| --- | --- | --- |
| Cloud-Native / K8s | Microsoft MCP Gateway, Envoy AI Gateway | Session-aware K8s (MS); token-encoding, no Redis (Envoy) |
| Developer-First | MCPProxy, Bifrost | Open-source; Code Mode 92.8% token savings; 11 us overhead; 6 auth types (Bifrost) |
| Commercial SaaS | AWS AgentCore, Cloudflare | Edge-native (CF); fully managed (AWS) |
| Protocol Bridge | Kong AI Gateway | Converts API schemas into MCP tool definitions |

**Four enterprise topologies:**

| Topology | Use Case |
| --- | --- |
| Single-tenant | Isolated internal teams, one gateway per team |
| Multi-tenant row-isolated | SaaS-style multi-customer MCPs with data isolation |
| Federated gateway | Large estates with central audit, multiple regional gateways |
| Edge-cached read-only | High-RPS tool discovery where `tools/list` is stable and cacheable |

---

## 5. Production Enterprise Code

Runnable Python (stdlib + asyncio). No API keys. JSON-RPC-style dispatch with per-backend circuit breakers, exponential backoff + full jitter, PII redaction, fallback chain, correlation IDs, per-session retry budget, OAuth token refresh with jitter, idempotency, and token tax calculator.

```python
#!/usr/bin/env python3
"""MCP gateway client with full resilience stack.

Combines the best patterns from both source implementations:
- Grok: sync FakeMcpServer with initialize/tools/list/tools/call,
        deterministic fallback chain, idempotency replay
- Opus: async per-backend circuit breaker (NOT per-tool), per-session
        retry budget, OAuth token manager with jittered refresh,
        token tax calculator

Run: python3 this_file.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Structured logging with correlation IDs
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s cid=%(correlation_id)s %(message)s",
)
BASE_LOGGER = logging.getLogger("mcp.host")


class CidFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = "-"
        return True


BASE_LOGGER.addFilter(CidFilter())


def log(cid: str, level: int, msg: str, **kw: Any) -> None:
    extra = " ".join(f"{k}={v}" for k, v in kw.items())
    BASE_LOGGER.log(level, f"{msg} {extra}".rstrip(),
                    extra={"correlation_id": cid})


# ---------------------------------------------------------------------------
# PII detect -> redact -> audit
# ---------------------------------------------------------------------------

_PII = [
    ("email", re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("phone", re.compile(
        r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    ("cc", re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")),
]
AUDIT_LOG: list[dict[str, Any]] = []   # append-only WORM stand-in


def redact_pii(cid: str, text: str) -> str:
    """Detect and redact PII. Log audit record. Return clean text."""
    original_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    count = 0
    for label, pattern in _PII:
        found = pattern.findall(text)
        if found:
            count += len(found)
            text = pattern.sub(f"[PII:{label}:{original_hash[:6]}]", text)
    if count:
        AUDIT_LOG.append({
            "cid": cid, "event": "pii_redact",
            "count": count, "hash": original_hash,
        })
        log(cid, logging.INFO, "pii_redacted", count=count)
    return text


# ---------------------------------------------------------------------------
# Circuit breaker: per-backend, NOT per-tool
# ---------------------------------------------------------------------------

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    pass


@dataclass
class CircuitBreaker:
    """Per-backend circuit breaker. Multiple tools share one backend API --
    opening per-tool would leave other tools hitting the same failing backend.
    """
    name: str
    failure_threshold: int = 3
    recovery_timeout_s: float = 2.0
    half_open_successes: int = 1
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    successes_in_half_open: int = 0
    opened_at: float = 0.0

    def before_call(self) -> None:
        if self.state is BreakerState.OPEN:
            if time.monotonic() - self.opened_at >= self.recovery_timeout_s:
                self.state = BreakerState.HALF_OPEN
                self.successes_in_half_open = 0
            else:
                raise CircuitOpenError(
                    f"breaker={self.name} state=open "
                    f"retry_in={self.recovery_timeout_s - (time.monotonic() - self.opened_at):.1f}s"
                )

    def record_success(self) -> None:
        if self.state is BreakerState.HALF_OPEN:
            self.successes_in_half_open += 1
            if self.successes_in_half_open >= self.half_open_successes:
                self.state = BreakerState.CLOSED
                self.failures = 0
        else:
            self.failures = 0
            self.state = BreakerState.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        if (self.state is BreakerState.HALF_OPEN
                or self.failures >= self.failure_threshold):
            self.state = BreakerState.OPEN
            self.opened_at = time.monotonic()


# ---------------------------------------------------------------------------
# Per-session retry budget (prevents cascading failures)
# ---------------------------------------------------------------------------

@dataclass
class RetryBudget:
    """After budget exhaustion, all subsequent calls fail fast."""
    max_retries: int = 20
    _used: int = 0

    def consume(self) -> bool:
        if self._used >= self.max_retries:
            return False
        self._used += 1
        return True

    @property
    def remaining(self) -> int:
        return max(0, self.max_retries - self._used)


# ---------------------------------------------------------------------------
# Retry with exponential backoff + full jitter
# ---------------------------------------------------------------------------

class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


def retry_with_backoff(
    cid: str,
    op: str,
    fn: Callable[[], Any],
    *,
    breaker: CircuitBreaker,
    budget: RetryBudget,
    max_attempts: int = 4,
    base_s: float = 0.05,
    max_s: float = 0.8,
) -> Any:
    """Full jitter: delay = random(0, min(max, base * 2^attempt)).
    AWS architecture blog shows this decorrelates competing retries
    better than equal jitter."""
    last: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        if not budget.consume():
            raise TransientError(f"retry budget exhausted ({budget.max_retries})")
        try:
            breaker.before_call()
            result = fn()
            breaker.record_success()
            log(cid, logging.INFO, "op_ok", op=op, attempt=attempt,
                breaker=breaker.state.value)
            return result
        except CircuitOpenError:
            raise
        except PermanentError as exc:
            breaker.record_failure()
            log(cid, logging.ERROR, "op_permanent", op=op, err=str(exc))
            raise
        except TransientError as exc:
            last = exc
            breaker.record_failure()
            if attempt == max_attempts:
                break
            cap = min(max_s, base_s * (2 ** (attempt - 1)))
            delay = random.uniform(0.0, cap)
            log(cid, logging.WARNING, "op_retry", op=op, attempt=attempt,
                delay_ms=int(delay * 1000), breaker=breaker.state.value)
            time.sleep(delay)
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 envelope helpers
# ---------------------------------------------------------------------------

def rpc_request(method: str, params: dict, req_id: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "method": method,
            "params": params}


def rpc_result(req_id: str, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def rpc_error(req_id: str, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# Fake MCP server (no network, no API keys)
# ---------------------------------------------------------------------------

class FakeMcpServer:
    """In-process MCP server: initialize / tools/list / tools/call."""

    def __init__(self, name: str, fail_times: int = 0) -> None:
        self.name = name
        self._remaining = fail_times
        self._initialized = False
        self._tools = {
            "echo": {
                "name": "echo",
                "description": "Echo text back",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                "annotations": {"readOnlyHint": True},
            },
            "add": {
                "name": "add",
                "description": "Add two numbers",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "number"},
                        "b": {"type": "number"},
                    },
                    "required": ["a", "b"],
                },
                "annotations": {"readOnlyHint": True, "idempotentHint": True},
            },
        }

    def handle(self, msg: dict) -> dict:
        req_id = msg.get("id", "?")
        method = msg.get("method")
        params = msg.get("params") or {}

        if method == "initialize":
            self._initialized = True
            return rpc_result(req_id, {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": self.name, "version": "0.1.0"},
            })

        if not self._initialized:
            return rpc_error(req_id, -32000, "not initialized")

        if method == "tools/list":
            return rpc_result(req_id, {"tools": list(self._tools.values())})

        if method == "tools/call":
            return self._call(req_id, params)

        return rpc_error(req_id, -32601, f"Method not found: {method}")

    def _call(self, req_id: str, params: dict) -> dict:
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in self._tools:
            return rpc_error(req_id, -32602, f"Unknown tool: {name}")

        if self._remaining > 0:
            self._remaining -= 1
            raise TransientError(f"{self.name}: simulated 503")

        if name == "echo":
            text = args.get("text")
            if not isinstance(text, str):
                return rpc_result(req_id, {
                    "content": [{"type": "text",
                                 "text": "text must be string"}],
                    "isError": True,
                })
            return rpc_result(req_id, {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            })

        if name == "add":
            try:
                total = float(args["a"]) + float(args["b"])
            except (KeyError, TypeError, ValueError):
                return rpc_result(req_id, {
                    "content": [{"type": "text",
                                 "text": "a and b must be numbers"}],
                    "isError": True,
                })
            return rpc_result(req_id, {
                "content": [{"type": "text", "text": str(total)}],
                "structuredContent": {"sum": total},
                "isError": False,
            })
        raise PermanentError(f"unhandled tool: {name}")


# ---------------------------------------------------------------------------
# Host dispatcher: resilience stack
# ---------------------------------------------------------------------------

@dataclass
class HostDispatcher:
    primary: FakeMcpServer
    secondary: FakeMcpServer        # fallback server
    breaker: CircuitBreaker = field(
        default_factory=lambda: CircuitBreaker("primary-backend"))
    budget: RetryBudget = field(default_factory=RetryBudget)
    idempotency_cache: dict[str, dict] = field(default_factory=dict)
    cached_tools: list[dict] | None = None

    def initialize(self, cid: str) -> dict:
        req_id = str(uuid.uuid4())
        msg = rpc_request("initialize", {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "demo-host", "version": "1.0.0"},
        }, req_id)
        result = self._send(msg, cid, idempotent=True)
        self.secondary.handle(msg)   # keep fallback initialized
        log(cid, logging.INFO, "initialized", server=self.primary.name)
        return result

    def tools_list(self, cid: str) -> list[dict]:
        msg = rpc_request("tools/list", {}, str(uuid.uuid4()))
        result = self._send(msg, cid, idempotent=True)
        tools = result.get("result", {}).get("tools", [])
        if tools:
            self.cached_tools = tools   # for deterministic fallback
        elif self.cached_tools is not None:
            log(cid, logging.WARNING, "tools/list degraded to cache")
            return self.cached_tools
        return tools

    def tools_call(
        self,
        name: str,
        arguments: dict,
        cid: str,
        idempotency_key: str | None = None,
    ) -> dict:
        # 1. PII redaction on args before call
        arguments = json.loads(
            redact_pii(cid, json.dumps(arguments)))

        # 2. Idempotent replay
        if idempotency_key and idempotency_key in self.idempotency_cache:
            log(cid, logging.INFO, "idempotent_replay", key=idempotency_key)
            return self.idempotency_cache[idempotency_key]

        # 3. Execute with resilience
        msg = rpc_request("tools/call",
                          {"name": name, "arguments": arguments},
                          str(uuid.uuid4()))
        result = self._send(msg, cid, idempotent=True)

        # 4. PII redaction on result
        result_str = json.dumps(result)
        result_str = redact_pii(cid, result_str)
        result = json.loads(result_str)

        # 5. Audit record
        AUDIT_LOG.append({
            "cid": cid, "event": "tools/call", "tool": name,
            "args_hash": hashlib.sha256(
                json.dumps(arguments, sort_keys=True).encode()
            ).hexdigest()[:16],
            "ok": "error" not in result and not (
                result.get("result") or {}).get("isError"),
            "breaker": self.breaker.state.value,
        })

        if idempotency_key:
            self.idempotency_cache[idempotency_key] = result
        return result

    def _send(self, msg: dict, cid: str, idempotent: bool) -> dict:
        try:
            return retry_with_backoff(
                cid, msg["method"],
                lambda: self._try_primary(msg),
                breaker=self.breaker, budget=self.budget,
                max_attempts=3 if idempotent else 1,
            )
        except (TransientError, CircuitOpenError) as exc:
            log(cid, logging.ERROR, "primary_exhausted fallback",
                err=str(exc))
            return self._fallback(msg, cid)

    def _try_primary(self, msg: dict) -> dict:
        resp = self.primary.handle(msg)
        if "error" in resp and resp["error"].get("code") == -32000:
            raise TransientError(resp["error"]["message"])
        return resp

    def _fallback(self, msg: dict, cid: str) -> dict:
        """Fallback chain: secondary server -> deterministic degrade."""
        try:
            # Ensure secondary is initialized
            if msg["method"] != "initialize":
                init = rpc_request("initialize", {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "demo-host", "version": "1.0.0"},
                }, str(uuid.uuid4()))
                self.secondary.handle(init)
            resp = self.secondary.handle(msg)
            log(cid, logging.INFO, "fallback_secondary_ok",
                server=self.secondary.name)
            self.breaker.record_success()
            return resp
        except Exception as exc:
            log(cid, logging.ERROR, "secondary_failed degrade",
                err=str(exc))
            return self._degrade(msg, cid)

    def _degrade(self, msg: dict, cid: str) -> dict:
        """Deterministic degradation -- no invented side effects."""
        method = msg["method"]
        req_id = msg["id"]
        if method == "tools/list" and self.cached_tools is not None:
            return rpc_result(req_id,
                              {"tools": self.cached_tools, "degraded": True})
        if method == "tools/call":
            return rpc_result(req_id, {
                "content": [{"type": "text",
                             "text": "Service degraded. Tool not executed. "
                                     "Escalate to human."}],
                "isError": True, "degraded": True,
            })
        return rpc_error(req_id, -32603, "degraded: no fallback")


# ---------------------------------------------------------------------------
# Token tax calculator (right-size your catalog before deployment)
# ---------------------------------------------------------------------------

def token_tax_report(
    num_tools: int,
    tokens_per_tool: int = 1_000,
    context_window: int = 200_000,
    input_price_per_1m: float = 15.0,    # e.g. Claude Opus
    cache_hit_rate: float = 0.75,
    cached_price_ratio: float = 0.10,
) -> dict[str, Any]:
    total = num_tools * tokens_per_tool
    pct = (total / context_window) * 100
    uncached = total * input_price_per_1m / 1_000_000
    effective = ((1 - cache_hit_rate) * input_price_per_1m
                 + cache_hit_rate * input_price_per_1m * cached_price_ratio)
    cached = total * effective / 1_000_000
    return {
        "schema_tokens": total,
        "context_pct": round(pct, 1),
        "cost_per_conv_uncached": round(uncached, 4),
        "cost_per_conv_cached": round(cached, 4),
        "cost_per_1k_uncached": round(uncached * 1000, 2),
        "cost_per_1k_cached": round(cached * 1000, 2),
        "reasoning_tokens_lost": total,
    }


# ---------------------------------------------------------------------------
# OAuth token refresh with jitter (prevents refresh storms)
# ---------------------------------------------------------------------------

class TokenManager:
    """Jittered refresh at 80% of token lifetime with up to 10% random
    offset. Single-flight lock ensures only one refresh per backend."""

    def __init__(self, lifetime_s: float = 3600.0) -> None:
        self.token = "initial-token"
        self.lifetime_s = lifetime_s
        self.expires_at = time.monotonic() + lifetime_s
        self._lock = False   # simplified; use asyncio.Lock in production

    def _threshold(self) -> float:
        base = self.expires_at - (self.lifetime_s * 0.20)
        jitter = random.uniform(0, self.lifetime_s * 0.10)
        return base - jitter

    def get_token(self) -> str:
        if time.monotonic() < self._threshold():
            return self.token
        # Single-flight refresh (simplified)
        if not self._lock:
            self._lock = True
            self.token = f"refreshed-{uuid.uuid4().hex[:8]}"
            self.expires_at = time.monotonic() + self.lifetime_s
            self._lock = False
        return self.token


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def demo() -> None:
    cid = str(uuid.uuid4())
    primary = FakeMcpServer("primary", fail_times=2)
    secondary = FakeMcpServer("secondary", fail_times=0)
    host = HostDispatcher(primary=primary, secondary=secondary)

    host.initialize(cid)
    tools = host.tools_list(cid)
    assert any(t["name"] == "echo" for t in tools)

    # PII in args -> redacted before call + in audit
    r1 = host.tools_call("echo",
                         {"text": "email alice@example.com SSN 123-45-6789"},
                         cid, idempotency_key="echo-1")
    r1_text = json.dumps(r1)
    assert "alice@example.com" not in r1_text
    assert "123-45-6789" not in r1_text
    assert "[PII:" in r1_text

    # Retries absorb primary transient failures -> eventual success
    r2 = host.tools_call("add", {"a": 2, "b": 3}, cid,
                         idempotency_key="add-1")
    assert r2["result"]["structuredContent"]["sum"] == 5.0

    # Idempotent replay (no re-execution)
    r2b = host.tools_call("add", {"a": 2, "b": 3}, cid,
                          idempotency_key="add-1")
    assert r2b == r2

    # Token tax report
    tax = token_tax_report(num_tools=20)

    print("=== Results ===")
    print(f"tools: {[t['name'] for t in tools]}")
    print(f"echo: {r1['result']['content'][0]['text']}")
    print(f"add:  {r2['result']['structuredContent']['sum']}")
    print(f"breaker: {host.breaker.state.value}")
    print(f"audit events: {len(AUDIT_LOG)}")
    print(f"retry budget remaining: {host.budget.remaining}")
    print(f"=== Token Tax (20 tools, Opus) ===")
    for k, v in tax.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    demo()
```

**What this demonstrates:**

| Concern | Implementation |
| --- | --- |
| Per-backend circuit breaker | CLOSED -> OPEN -> HALF_OPEN; multiple tools share one backend |
| Retries + full jitter | U(0, min(max, base * 2^n)); AWS architecture blog pattern |
| Per-session retry budget | 20 max retries per session; fail fast after exhaustion |
| Fallback chain | Primary -> secondary server -> deterministic degraded result |
| PII redaction | Email/SSN/phone/CC detect -> stable tokens -> append-only audit |
| Idempotency | SHA-256 key on tool:args; cached replay avoids re-execution |
| JSON-RPC 2.0 | Correct envelopes: request/result/error with matching IDs |
| Correlation IDs | UUID per session; every log line carries `cid=` |
| OAuth jitter | 80% lifetime + 10% random offset; single-flight lock |
| Token tax calculator | Right-size catalog before deployment |
| Graceful degradation | Cached `tools/list` + "tool not executed, escalate to human" stubs |

---

## 6. Architectural System Design Scenarios

### Scenario A -- Multi-Tenant SaaS AI Assistant with MCP Gateway

**Problem statement.** A B2B SaaS company serves 500 enterprise customers. Each customer's AI assistant needs access to customer-specific data (Postgres, S3, Salesforce) through MCP tools, but no customer should see another's data. The system must handle 1,000 concurrent agent sessions, comply with SOC 2, keep per-conversation cost under $0.20, and maintain >=90% tool-selection accuracy on mid-tier models with a catalog of ~3,000+ enterprise tools across CRM, ERP, ITSM. Desktop IDEs and cloud hosts must both connect.

**Proposed architecture:**

```
  IDE/Cloud Hosts --OAuth--> MCP Edge Gateway (Streamable HTTP)
                                   |
                    +--------------+--------------+
                    v              v              v
              Retrieval gate   OPA Policy     Audit WORM
              (Top-15 tools)   (tenant_id in  + PII filter
              (ns__tool names) JWT -> allow
                    |          only that
                    |          tenant's tools)
                    v
              Tenant Proxy Aggregator
                    |
         +----------+----------+
         v          v          v
      CRM MCP    ERP MCP    ITSM MCP   (Domain Adapters)
      (RLS       (Bucket    (Org-scoped
       policies)  prefix     API tokens)
                  IAM)
```

**Trade-off matrix:**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **A1. Inline all tools in prompt** | Very high (~100k-500k+ tokens/turn) | Model p50/p95 explode | Simple | Large attack surface | Fails past context limits |
| **A2. Retrieval gateway + Top-15 (recommended)** | Low schema tokens (~21k class); $0.10/conv cached with 20 tools within $0.20 budget | Tool-select ~hundreds ms; framing far below RTT; P99 per tool ~50 ms | Medium (index freshness; health checks must validate SSE) | Smaller prompt; tenant-scoped RLS at DB level (unbypas\-sable even by prompt injection); PII redaction before model context | 300 RPS per gateway instance; scales to 3k+ tools |
| **A3. Per-tenant stdio sidecars only** | Low cloud $; high desktop ops | Best framing (~0.01 ms) | Poor multi-tenant management | Strong isolation | Cannot share SaaS tools across hosts |

**Decision rationale.** Choose **A2**. Published evidence shows naive inlining is infeasible at thousands of tools while Top-15 preserves accuracy and cuts tokens. HTTP is required for multi-tenant reach; stdio cannot serve SaaS. Envoy AI Gateway chosen over Microsoft MCP Gateway because its token-encoding architecture eliminates the Redis dependency for session management -- critical when 2026-07-28 makes the protocol stateless. OPA chosen over Cedar because existing SOC 2 audit tooling integrates with OPA decision logs. Row-level security in Postgres chosen because it cannot be bypassed by prompt injection -- even if the LLM crafts a malicious SQL query, RLS policies enforce tenant boundaries at the database level. Pair with Zero-Trust OAuth (RFC 8707 audience binding) and immutable audit.

---

### Scenario B -- Internal Developer Platform with Federated Bifrost Gateway

**Problem statement.** A 5,000-engineer enterprise has 3 regional engineering centers (US-West, EU-Frankfurt, APAC-Singapore). Each center runs AI-assisted developer tools (code review agents, incident responders, documentation generators). Central platform team needs unified audit, consistent tool governance, and <100 ms P95 tool call latency within each region. The tool catalog spans 200+ tools across 40 MCP servers. SOX compliance requires centralized audit.

**Proposed architecture:**

```
  +-------------------------------------------------------+
  |         CENTRAL GOVERNANCE PLANE (us-west)             |
  |  +---------------+  +---------------+  +------------+ |
  |  | Global Policy  |  | Audit         |  | Tool       | |
  |  | (Cedar rules,  |  | Aggregator    |  | Registry   | |
  |  |  versioned,    |  | (receives all |  | (canonical | |
  |  |  replicated)   |  |  regions;     |  |  schemas,  | |
  |  |                |  |  SOX ready)   |  |  versioned)| |
  |  +-------+--------+  +------^--------+  +------+-----+ |
  +---------+------------------+--------------------+-------+
            | policy sync      | audit events       | catalog sync
   +--------+--------+--------+--------+--------+--+------+
   |                  |                 |                   |
   v                  v                 v
+----------+   +----------+   +----------+
| US-West   |   | EU-Frank  |   | APAC-SG   |
| Regional  |   | Regional  |   | Regional  |
| Bifrost   |   | Bifrost   |   | Bifrost   |
| Gateway   |   | Gateway   |   | Gateway   |
| (Code Mode|   | (Code Mode|   | (Code Mode|
|  92.8%    |   |  92.8%    |   |  92.8%    |
|  token    |   |  token    |   |  token    |
|  savings) |   |  savings) |   |  savings) |
+-----+-----+   +-----+-----+   +-----+-----+
      |                |               |
   Local MCP        Local MCP       Local MCP
   Servers (15)     Servers (12)    Servers (13)
```

**Trade-off matrix:**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **B1. Single centralized gateway** | Simpler ops | Cross-region RTT (~150 ms US-APAC) blows <100 ms P95 | Easier to manage | Single point of control | Harder to scale globally |
| **B2. Federated regional gateways (recommended)** | ~$500/mo compute (3 instances); per-conv ~$0.02 (Code Mode) vs $3.00 (naive) | P95 <25 ms intra-region; tool calls never cross regions | 3 regional deploys + 1 central governance | Cedar formal verification; central policy, regional enforcement | 300 RPS per region; 10x headroom at ~28 RPS/region |
| **B3. God Tool over SSH** | Cheap to build | Variable | Brittle | Worst least-privilege; collapses selection accuracy | Illusion of scale |

**Decision rationale.** Choose **B2**. Cross-region latency eliminates a single centralized gateway. Bifrost chosen over Envoy because Code Mode is essential at 200+ tools -- without it, tool schemas alone consume the entire context window (200k+ tokens). Cedar chosen over OPA because Cedar's formal verification properties provide provable guarantees that no policy combination can grant unintended access -- critical when 3 regional teams request policy changes. Central audit aggregator is eventually consistent (not strongly consistent) because audit latency tolerance is minutes, not milliseconds, and strong consistency across 3 regions would add 100-200 ms to every tool call for quorum writes. Tool calls never cross regions. `ttlMs` caching prevents thundering herd on regional gateway restart.

---

### Scenario C -- Regulated Desktop Coding Agent with Local Secrets

**Problem statement.** Banking engineering org wants Cursor/VS Code agents to call local code-intel + ticket systems without sending source code or API keys to a multi-tenant MCP cloud. Peak 200 concurrent developers, local tool p99 <100 ms for fs/git reads, SOC 2 evidence for every `tools/call`, HITL on writes to prod trackers.

**Proposed architecture:**

```
  IDE Host (control plane)
       | stdio 1:1
       v
  Sandboxed local MCP servers (fs, git, linter)
       |
       +-- optional HTTPS MCP (tickets) with user OAuth + always_ask writes
       +-- PII/secret redact before model append
       +-- append-only audit log -> SIEM
  Host Temporal/LangGraph: checkpoint + idempotency on ticket mutations
```

**Trade-off matrix:**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **C1. All-remote HTTP mesh** | Central infra $ | RTT-bound (p99 ~180 ms framing alone) | Easier central ops | Larger blast radius; confused deputy risk | Horizontal |
| **C2. stdio-local + selective remote HTTP (recommended)** | Mostly local CPU | Framing ~0.01-0.02 ms p99 local | Per-machine sandbox updates | Process isolation + env secrets; OAuth only for remote | Per-developer vertical |
| **C3. Single God Tool over SSH** | Cheap | Variable | Brittle | Worst least-privilege | Illusion of scale |

**Decision rationale.** Choose **C2**. Spec and measurements favor stdio for local-only, lowest overhead, env-based credentials. Remote HTTP only for shared systems (tickets), with consent on writes. Host owns durable checkpoints and idempotency for ticket mutations. Avoid God Tool anti-pattern.

---

## Common Failure Modes

| # | Failure Mode | Symptom | Root Cause | Fix |
| --- | --- | --- | --- | --- |
| 1 | **Token tax explosion** | 72% of context consumed by schemas; reasoning quality drops | Too many tools inlined without retrieval | Retrieval gateway (Top-15); tiered schema; Code Mode (92.8% savings); <20% context budget |
| 2 | **Transport flakiness** | Agent sessions silently break under load | LBs terminate SSE at 60s; health checks pass but sessions dead | Externalize session state; validate active SSE streams |
| 3 | **Tool description drift** | Silent argument mismatches; wrong results | Server-side schema changes without client notification | `listChanged` notifications + client-side version tracking |
| 4 | **OAuth refresh storms** | IdP overwhelmed; mass auth failures | Hundreds of sessions expire simultaneously | Jittered refresh at 80% lifetime + 10% offset + single-flight lock |
| 5 | **Silent JSON-RPC hangs** | Client blocks forever; agent appears stuck | Server crash mid-handler; no response for request ID | Per-call timeout + circuit breaker per backend + process supervisor |
| 6 | **Cascading agent retries** | Downstream overload that appears as slow success | LLM agents retry with different params (unlike fail-fast APIs) | Per-session retry budget; circuit-break at capability level; never retry in prompt |
| 7 | **Confused deputy** | Victim's auth leaked via proxy | Static shared client ID + consent cookies on MCP proxy | Per-client credentials; CIMD; validate audience |
| 8 | **Supply chain poisoning** | Data exfiltration via compromised server | Fake MCP server with clean release history | Pin versions; CVE feed; namespace verification; no auto-update |

---

## Key Takeaways for Interviews

1. **MCP solves N*M -> N+M** -- Without a shared protocol, every AI app needs bespoke integrations to every tool. MCP reduces this to N clients + M servers, just like USB-C standardized device charging.

2. **The 1:1 client-server invariant is foundational** -- Each host spawns one dedicated client per connected server. This provides isolation, clear security boundaries, and independent lifecycle management.

3. **Two-tier error model is critical for agent recovery** -- Protocol errors (JSON-RPC `error`) indicate system failures; tool execution errors (`isError: true`) indicate the tool ran but failed, letting the LLM read the error and self-correct.

4. **The token tax is the biggest production cost** -- Tool schemas consume 2-30x more tokens than baseline. Seven servers can eat 33.7% of a 200k context window before any user message. Keep schemas under 20% of context; use retrieval at scale.

5. **Circuit breakers must be per-backend, not per-tool** -- Multiple tools share one backend API. Opening per-tool leaves other tools still hitting the same failing backend. This is the most common misconfiguration in production MCP gateways.

6. **MCP is NOT Temporal** -- The protocol handles transport + session + cancellation. Durable execution (checkpoints, idempotency, replay, dead-letter) lives in the host's orchestration layer above MCP (LangGraph, Temporal, ADK).

7. **Disconnect does not equal cancel** -- Explicit `CancelledNotification` is required. Treating disconnect as cancel would violate tool-side expectations for long-running operations.

8. **2026-07-28 made MCP stateless** -- Sessions removed; every request self-describing via `_meta`. Any server behind round-robin LB can handle any request. Cross-call state uses server-minted handles passed via tool arguments.

---

## Interview Q&A

**Q1: Walk through the MCP request flow from Host to tool result. Where do consent and RBAC sit?**
A: The Host creates one Client per Server (1:1 invariant). Pre-2026-07-28, the Client sends `initialize` with protocol version and capabilities; the Server returns its capabilities; the Client sends `notifications/initialized`. Post-2026-07-28, every request carries version and capabilities in `_meta` with no handshake. For a tool call: the Host builds context including tool schemas from `tools/list`, the LLM emits a tool call, and the Host presents it for user consent (spec mandates this). The request goes through the gateway where OPA/Cedar evaluates per-call RBAC -- not just at connect time but on every `tools/call` with tool name and argument inspection. The call then passes through a per-backend circuit breaker, PII redaction on the response, and immutable audit logging before the result enters the model context.

**Q2: Why can MCP framing be 0.01 ms p50 yet your agent still miss a 500 ms p95 SLA?**
A: Because MCP framing is pure protocol overhead (JSON-RPC serialization). The latency budget is dominated by downstream tool I/O (typical SaaS API: 80-800 ms p50-p99), model inference (hundreds of ms to seconds), and policy evaluation. For remote Streamable HTTP, network RTT adds 30-180 ms. Even locally, the tool's backend work dominates. A 10-tool agentic workflow has 0.5 s at p50 and 2.0 s at p99 in production -- almost none of that is MCP framing. The implication is that optimizing MCP transport is pointless; optimize the downstream tools and model inference.

**Q3: Derive the cost when a tool catalog grows from 10 to 100 tools without retrieval.**
A: At ~1,000 tokens per tool and $15/1M for Opus input: 10 tools cost 10,000 tokens = $0.15/conversation uncached. 100 tools cost 100,000 tokens = $1.50/conversation uncached -- a 10x increase. But more critically, 100 tools consume 50% of a 200k context window just for schemas, leaving only 100k tokens for reasoning and conversation. Tool selection accuracy also degrades: Haiku drops below 90% at 15 tools, Sonnet degrades past 20-30. At 100 tools without retrieval, both cost and accuracy are unacceptable. This is why retrieval gateways (Top-15 selection, ~21k tokens at 81.6% accuracy) or Code Mode (92.8% token savings) are non-negotiable at scale.

**Q4: "MCP is not Temporal" -- where do you put checkpoints and idempotency keys?**
A: Checkpoints go in the host's orchestration layer -- LangGraph or Temporal session snapshots after each successful `tools/call` observation. MCP's SSE `Last-Event-ID` only resumes streams; it does not event-source tool call history. Idempotency keys go in tool arguments or the `_meta` block of the JSON-RPC request. The server maintains a dedupe store and returns cached results for duplicate keys. For non-idempotent mutations (purchases, deployments), I add a distributed lock before calling `tools/call` and wrap the whole sequence in a Temporal Workflow where Activities correspond to individual tool calls. Permanent `isError` responses or poison pill arguments go to a dead-letter queue for human review.

**Q5: Draw the circuit breaker for a flapping CRM MCP server. What is the fallback chain?**
A: The circuit breaker is per-backend (CRM API), not per-tool (not per `create_contact` or `search_contacts`). CLOSED: normal operation, failures counted. After 3-5 failures in a window, transition to OPEN: all calls rejected immediately, timer starts. After 30 seconds, HALF_OPEN: one probe call (idempotent read preferred, like `search_contacts`). If probe succeeds, back to CLOSED; if fails, back to OPEN with doubled timeout. Fallback chain: (1) primary CRM MCP server, (2) secondary read-only replica or regional mirror, (3) deterministic degraded result -- return cached last-good `tools/list` and stub results ("CRM unavailable, escalate to human") with `isError: true`. Never invent data to fill the gap.

**Q6: How do you handle the 2025-11-25 to 2026-07-28 migration for an enterprise fleet?**
A: The key breaking changes: session removal (no more `Mcp-Session-Id`), Roots/Sampling/Logging deprecation (12-month window), MRTR for elicitation, mandatory `Mcp-Method`/`Mcp-Name` headers, and CIMD replacing DCR. I would migrate in phases: first add `Mcp-Method`/`Mcp-Name` headers to all HTTP requests while keeping session IDs (both specs tolerate this). Second, move cross-call state from implicit session maps to explicit server-minted handles passed as tool arguments. Third, replace `sampling/createMessage` calls with MRTR patterns (`resultType: "input_required"`). Fourth, externalize session state from in-memory SDK stores to Redis/Postgres. Finally, remove session affinity from load balancers -- with stateless requests, any server instance can handle any request. The 12-month deprecation window gives time, but the sticky-session fleet is the hardest part: any implicit server-side session state must become explicit handles.

**Q7: Design Zero-Trust for Streamable HTTP: Origin, audience-bound tokens, confused-deputy mitigations.**
A: First, validate `Origin` on every HTTP request and return 403 for invalid origins (prevents DNS rebinding). Second, use OAuth 2.1 with mandatory PKCE S256 and RFC 8707 Resource Indicators -- every token is audience-bound to the specific MCP server URI, so a token for `crm.example.com` cannot be used against `hr.example.com`. Third, for confused deputy: avoid static shared third-party client IDs on proxy servers; use per-client credentials via CIMD (Client ID Metadata Documents). Fourth, never pass tokens through to upstream systems -- each hop gets its own scoped token via RFC 8693 On-Behalf-Of exchange. Fifth, bind local servers to `127.0.0.1` only. Sixth, in 2026-07-28, validate `iss` (RFC 9207) before redeeming authorization codes, preventing code injection from rogue authorization servers.

**Q8: Your multi-tenant SaaS runs 3,000 tools. How do you keep tool-selection accuracy above 90%?**
A: The Alibaba study shows naive inlining of 3,616 tools is infeasible (~506k tokens). I use a retrieval gateway with Top-15 selection: for each user query, a retrieval model (BM25 + vector) selects the 15 most relevant tools, cutting tokens from 506k to ~21k while maintaining 81.6% accuracy. For the remaining accuracy gap, I namespace tools per tenant/domain (`crm__create_contact`) so the retrieval query has stronger discriminative signal. I cache `tools/list` results per tenant with `ttlMs` from the 2026-07-28 spec, and I return tools in deterministic order to maximize prompt cache hits. Bifrost Code Mode can achieve 92.8% token reduction at 500+ tools with 100% pass rate by using 3-4x fewer LLM round trips. The NFR target is <20% of context window consumed by tool schemas.

**Q9: How do OAuth refresh storms happen in MCP deployments and how do you prevent them?**
A: When hundreds of agent sessions start within the same minute (common at shift start or after a deployment), their OAuth tokens expire simultaneously. All sessions hit the IdP's token endpoint at once, overwhelming it. The fix has three parts: (1) jittered refresh at 80% of token lifetime with up to 10% random offset -- instead of all refreshing at exactly 3600s, they spread across 2880-3240s; (2) single-flight lock per backend -- only one refresh request per backend at a time, all other coroutines wait for the result; (3) client-side token caching with pre-emptive refresh so tokens never actually expire during a call. This pattern is documented in all production MCP gateway guides.

**Q10: When would you deliberately choose NOT to use MCP?**
A: When you have a known, fixed set of tools where latency and token efficiency matter more than dynamic discovery. CLI achieved 33% better token efficiency and 77 vs 60 task completion score compared to MCP in browser automation benchmarks. The gap was largest in multi-step workflows where context budget ran out mid-task with MCP. Perplexity's CTO moved away from MCP in March 2026 for this reason. I would skip MCP for: latency-critical pipelines where 50-200ms/call overhead is unacceptable, simple architectures with 1-3 fixed tools, internal scripts where governance/audit is not required, and any path where tool schemas would consume more than 20% of context. I would use MCP for: dynamic tool sets, multi-agent systems needing the same integrations, platform teams serving many consumers, and any deployment requiring standardized audit trails.

**Q11: What are the three missing protocol-level primitives and why do they matter?**
A: Identity propagation -- the protocol has no standard way to carry end-user identity through the Host -> Client -> Server chain, so every deployment reinvents this with custom headers or OAuth claim forwarding. Adaptive tool budgeting -- there is no protocol mechanism for a server or gateway to say "you have 15,000 tokens left for tool schemas; here are the top-k tools that fit." Each host implements this independently. Structured error semantics -- beyond `isError: true` and JSON-RPC error codes, there is no standard way to express "this error is retryable in 30 seconds" or "this error means your permissions are insufficient for this specific argument value." These three gaps force every production deployment to build custom solutions, reducing the interoperability benefit that is MCP's core value proposition.

**Q12: Compare Envoy AI Gateway vs Bifrost for an MCP gateway. When would you choose each?**
A: Envoy AI Gateway uses a token-encoding architecture that eliminates Redis/database dependency for session management -- ideal when the 2026-07-28 spec makes sessions stateless and you want any instance to handle any request without shared state. It integrates well with existing Envoy service mesh deployments. Bifrost is open-source with Code Mode that achieves 92.8% input token reduction at 500+ tools -- essential when your catalog is large enough that naive schema injection consumes the entire context window. It supports 6 upstream auth types and per-virtual-key tool filtering. Choose Envoy when you have fewer than ~100 tools, an existing Envoy deployment, and need zero external state dependencies. Choose Bifrost when you have 200+ tools and Code Mode is the difference between the catalog fitting in context versus not. Both handle 300+ RPS; Bifrost benchmarks 5k RPS at 11 microseconds overhead.

---

## Key Numbers to Memorize

| Metric | Value | Context |
| --- | --- | --- |
| Token inflation | **2x - 30x** | MCP vs baseline chat across 9 LLMs |
| Per-tool context cost | **~1,000 tokens** | Per tool per session |
| 7 servers context usage | **67,300 tokens (33.7%)** | Of 200k context window |
| Tool accuracy threshold | **10-15 tools** | Beyond this, mid-tier models drop below 90% |
| Top-15 retrieval tokens | **~21k** (vs ~506k naive) | Alibaba study at 3,616 tools |
| Code Mode savings | **92.8%** input tokens | Bifrost at 500+ tools |
| Context budget rule | **<20%** for schemas | Beyond this, reasoning quality degrades |
| stdio latency | **0.01 ms** p50 | Pure IPC, no network |
| HTTP remote latency | **~30 ms** p50 | Same-region, protocol only |
| Bifrost gateway overhead | **11 us** | At 5,000 RPS |
| Per tool call (production) | **50-200 ms** | Including downstream I/O |
| Streamable HTTP throughput | **~290-300 RPS** | At 100% success rate |
| HTTP+SSE throughput (deprecated) | **7-30 RPS** | Collapses under load |
| CLI vs MCP efficiency | **33% better** (CLI) | Browser automation benchmark |
| 833 fault threads | **73 leaf fault types** | From 473 MCP server repos |
| Enterprise adoption | **78%** production AI teams | 2026 |
| Public registry servers | **9,400+** | 2026 |
| SDK downloads | **1B+ total** | ~500M/month combined |
| MCP gateways on market | **42** | 2026 |

---

## Quick Reference

```
MCP = USB-C for AI: N*M integrations -> N clients + M servers

Three primitives:
  Tools     (model-controlled)   -> tools/list, tools/call
  Resources (app-controlled)     -> resources/list, resources/read
  Prompts   (user-controlled)    -> prompts/list, prompts/get

Two transports:
  stdio          -> local, ~0ms, subprocess, no auth needed
  Streamable HTTP -> remote, ~10ms/call, OAuth 2.1, horizontal scale

1:1 client-server invariant (foundational isolation)
Disconnect != cancel (explicit CancelledNotification required)
Circuit breakers: per-backend, NOT per-tool
MCP is NOT Temporal (durability lives in the host layer)
Token budget: <20% of context for schemas
isError: true = model can self-correct; JSON-RPC error = system failure

2026-07-28: stateless core, no sessions, _meta on every request
Retrieval gateway: mandatory above ~15 tools
Fallback: primary -> secondary -> deterministic degrade -> human
```
