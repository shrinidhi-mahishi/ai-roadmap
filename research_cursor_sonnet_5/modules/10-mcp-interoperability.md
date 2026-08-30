# 10. MCP & Interoperability

**Sub-areas covered**: Host/Client/Server architecture and the three model-context primitives (Tools/Resources/Prompts) plus reverse-direction Sampling/Elicitation · the 2026-07-28 spec's removal of the `initialize`/`Mcp-Session-Id` handshake in favor of self-describing stateless requests, and the resulting `Mcp-Method`/`Mcp-Name` header-based gateway routing (SEP-2243) · transport mechanics (stdio, Streamable HTTP, deprecated HTTP+SSE) with complexity/latency trade-offs · tool-discovery caching via `ttlMs`/`cacheScope` (SEP-2549) · the "Tools Tax"/"MCP Tax" token-overhead economics (Scalekit's 4×-32× benchmark, real multi-server context-window consumption, Anthropic Tool Search/Cloudflare Code Mode/Block's layered-tool-pattern mitigations) · a full P50/P95/P99 latency table for stdio vs. Streamable HTTP round-trips · MCP gateway capacity planning and back-pressure design · explicit availability/RPO/RTO targets tied to session/EventStore persistence granularity, with stdio-simplicity-vs-HTTP-scalability and caching-vs-freshness trade-off discussion · durable execution across MCP tool calls (Dapr `MCPServer`, Temporal), the statelessness-vs-resumability trade-off, a transient/permanent/poison-pill failure taxonomy, and dead-letter handling · thorough Zero-Trust MCP coverage (NIST SP 800-207, OBO/confused-deputy defense, SPIFFE/SPIRE workload identity, RFC 8693 token exchange), OAuth 2.1 for MCP (RFC 9728/8414/8707, mandatory PKCE), gateway-centralized tool-level RBAC, PII detect→redact→audit at the MCP boundary, immutable chain-of-custody auditability, sandbox isolation tiers, and the live threat landscape (Tool Poisoning Attacks, MCPTox, named CVEs, supply-chain risk, schema drift) · a hardened Python MCP-gateway client wrapping multiple backend servers with retries, per-server circuit breakers, fallback chains, PII redaction, idempotency keys, and correlation-ID logging · two enterprise system-design scenarios with trade-off matrices

---

## 1. System Topology & Data Flow

A production MCP deployment is not a single client-server pipe but five cooperating planes: a **control plane** that decides *who* may call *what* before any tool executes, a **data plane** (the MCP gateway) that routes and mediates every live tool call, a set of **tool proxies** wrapping each backend MCP server in isolation and policy enforcement, a **persistence layer** that survives process restarts and reconnects independently of the protocol's own (now largely stateless) core, and a **telemetry layer** making every invocation auditable after the fact. The diagram below places the Host/Client/Server roles, the 2026-07-28 spec's header-based routing, Zero-Trust enforcement, and PII redaction into the generic planes they occupy.

```
                    ┌──────────────────────────────────────────────────────────────────────────────────────┐
                    │                                    CONTROL PLANE                                        │
                    │                                                                                          │
                    │  ┌─────────────────────┐   ┌──────────────────────┐   ┌───────────────────────────────┐│
                    │  │ Host (LLM app: Claude │──▶│ Registry / Namespace   │──▶│ OAuth 2.1 Authorization Server  ││
                    │  │ Desktop, Cursor, ...)  │   │ (verified server names,│   │ (external or co-hosted; mints   ││
                    │  │ spawns one Client per  │   │ Official Registry does │   │ tokens — the MCP server ITSELF  ││
                    │  │ Server session, §2.1   │   │ NOT code-scan, §4.11)  │   │ only validates them, never      ││
                    │  └──────────┬────────────┘   └───────────┬───────────┘   │ issues, §4.6)                    ││
                    │             │ tools/call,                 │ Shadow-MCP    └────────────────┬────────────────┘│
                    │             │ resources/read,              │ discovery                       │ token issuance  │
                    │             │ prompts/get                  ▼                                 │                 │
                    │  ┌─────────────────────┐   ┌──────────────────────┐                         │                 │
                    │  │ Policy Engine (OPA/    │   │ Two-Axis Auth Model    │◀────────────────────────┘                 │
                    │  │ Cedar PDP: tool- AND   │   │ (persona × credential  │                                           │
                    │  │ argument-level RBAC,   │   │ type; OBO / RFC 8693   │                                           │
                    │  │ evaluated on every     │   │ token exchange narrows │                                           │
                    │  │ tools/call, §4.5/4.7)  │   │ scope per hop, §4.5)   │                                           │
                    │  └──────────┬──────────┘   └───────────┬───────────┘                                          │
                    └─────────────┼───────────────────────────┼──────────────────────────────────────────────────────┘
                                     │ authorized, scope-narrowed request
                    ┌─────────────▼───────────────────────────▼──────────────────────────────────────────────────────┐
                    │                                  DATA PLANE  (MCP GATEWAY)                                         │
                    │  ┌───────────┐   ┌────────────┐   ┌────────────┐   ┌─────────────┐                             │
                    │  │ Mcp-Method │──▶│ Per-Server  │──▶│ PII Detect  │──▶│ Tool-Catalog │                             │
                    │  │ /Mcp-Name  │   │ Circuit     │   │ →Redact→    │   │ Cache        │                             │
                    │  │ Header     │   │ Breaker     │   │ Audit       │   │ (ttlMs /     │                             │
                    │  │ Router     │   │ (CLOSED→    │   │ (BEFORE     │   │ cacheScope,  │                             │
                    │  │ (SEP-2243, │   │ OPEN→HALF,  │   │ response    │   │ SEP-2549,    │                             │
                    │  │ no JSON-   │   │ per backend │   │ enters model│   │ §2.5/3.2)    │                             │
                    │  │ RPC body   │   │ dependency, │   │ context,    │   │              │                             │
                    │  │ parse,§2.4)│   │ never per-  │   │ §4.8)       │   │              │                             │
                    │  │            │   │ tool, §4.3) │   │             │   │              │                             │
                    │  └─────┬─────┘   └─────┬──────┘   └─────┬──────┘   └─────┬───────┘                             │
                    └────────┼───────────────┼─────────────────┼─────────────────┼─────────────────────────────────────┘
                               │ stdio          │ Streamable HTTP  │ Streamable HTTP  │
                               │ (local sub-    │ (session-pooled, │ (SSE upgrade,    │
                               │ process pipe,  │ shared session   │ resumable via     │
                               │ §2.3)          │ ~10× throughput  │ Last-Event-ID,    │
                               │                │ vs unique-per-   │ §3.3/4.2)         │
                               │                │ request, §3.3)   │                   │
                    ┌───────────▼──────────────▼──────────────────▼───────────────────▼──────────────────────────────┐
                    │                                 TOOL PROXIES  (per MCP server)                                     │
                    │  ┌──────────────────────────┐  ┌────────────────────────────┐  ┌────────────────────────────┐   │
                    │  │ Sandbox Tier               │  │ Server N: Tools/Resources/   │  │ Server N+1: different vendor,│   │
                    │  │ (OS-level <10ms / gVisor   │  │ Prompts; may itself issue     │  │ different sandbox tier,      │   │
                    │  │ ~500ms / Firecracker       │  │ Sampling / Elicitation        │  │ isolated blast radius from   │   │
                    │  │ ~125ms — startup vs.       │  │ reverse-calls back to the     │  │ Server N, §4.10)             │   │
                    │  │ isolation dial, §4.10)     │  │ client, §2.1)                 │  │                              │   │
                    │  └──────────────────────────┘  └──────────────┬─────────────┘  └───────────────┬──────────────┘   │
                    └──────────────────────────────────────────────┼──────────────────────────────────┼──────────────────┘
                                                                          │ backend I/O (DB, SaaS API)      │ backend I/O
                    ┌────────────────────────────────────────────────▼──────────────────────────────────▼────────────────┐
                    │                                     PERSISTENCE LAYER                                                  │
                    │  ┌────────────────────┐  ┌───────────────────────┐  ┌─────────────────────┐  ┌───────────────────┐│
                    │  │ Externalized Event    │  │ Durable Workflow Store  │  │ Token Vault          │  │ Immutable Audit     ││
                    │  │ Store (Redis, keyed    │  │ (Temporal Event Hist.   │  │ (short-lived, scope- │  │ Log (hash-chained,  ││
                    │  │ session+stream — SDK   │  │ per Activity / Dapr     │  │ limited creds; never │  │ tool-call-level:     ││
                    │  │ default is single-     │  │ Scheduler redelivers    │  │ handed to the agent  │  │ who, what tool,      ││
                    │  │ process in-memory and  │  │ pending activities on   │  │ itself, §3.5/4.8)    │  │ params, policy       ││
                    │  │ 404s on restart, §3.5) │  │ daprd restart, §4.1)    │  │                      │  │ decision, §4.9)      ││
                    │  └────────────────────┘  └───────────────────────┘  └─────────────────────┘  └───────────────────┘│
                    └────────────────────────────────────────────────┬───────────────────────────────────────────────────────────┘
                                                                          │
                    ┌────────────────────────────────────────────────▼───────────────────────────────────────────────────────────┐
                    │                            TELEMETRY / OBSERVABILITY SINKS                                                     │
                    │  Per-(client,server) P50/P95/P99 round-trip latency (§3.3) · circuit-breaker state dashboard (§4.3) ·          │
                    │  token-tax meter per connected server (§3.1-3.2) · Shadow-MCP / unsanctioned-connection alerts (§4.11) ·        │
                    │  CVE / dependency-risk feed for installed servers (§4.11) · chain-of-custody audit trail (§4.9)                 │
                    └──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A **Host** application (Claude Desktop, Cursor, an internal agent runtime) maintains one **Client** per **Server** connection — this 1:1 client-server pairing is what lets one host manage many isolated sessions with independent capability sets. (2) As of the **2026-07-28 spec**, the request carries its own protocol-version metadata and needs no prior `initialize`/`initialized` handshake — every `tools/call`, `resources/read`, or `prompts/get` is self-describing and independent (§2.2); a client that wants capability info up front may call the optional `server/discover` RPC, but nothing blocks on it. (3) The request first crosses the **control plane**: the registry confirms the target server's namespace-verified identity (distinct from any code-security guarantee, §4.11), the policy engine evaluates **tool- and argument-level RBAC** for *this specific call* (never just at connect time), and an On-Behalf-Of token exchange (RFC 8693) narrows the caller's scope to exactly what this hop needs (§4.5). (4) The authorized, scope-narrowed request enters the **MCP gateway data plane**, where the 2026-07-28 `Mcp-Method`/`Mcp-Name` HTTP headers (SEP-2243) let the router dispatch, authorize, and rate-limit **without parsing the JSON-RPC body at all** — a deliberate performance and security-boundary optimization (§2.4). (5) The gateway checks a **per-backend-dependency circuit breaker** (never per-tool, since multiple tools commonly share one backend API, §4.3) before dispatch, and applies **PII detect→redact→audit** to the eventual tool *response* before it ever reaches the model's context window (§4.8). (6) The call is framed over whichever transport that server uses — **stdio** for a local subprocess (zero network overhead, no built-in auth) or **Streamable HTTP** for a networked, horizontally-scaled, OAuth-fronted server (§2.3) — and executes inside a **sandbox tier** chosen for that server's trust level (OS-level, gVisor, or Firecracker, §4.10). (7) The tool-schema catalog for each connected server is cached client-side using the `ttlMs`/`cacheScope` fields introduced by SEP-2549, directly mitigating the re-fetch cost the stateless redesign would otherwise impose on every reconnect (§2.5/3.2). (8) If the server genuinely needs mid-stream resumability (SSE `Last-Event-ID` replay), the event log must be externalized to a shared, durable store (Redis-backed `EventStore` keyed by session+stream) — the SDK-default in-memory `EventStore` lives in a single process and either 404s on restart or fails to share across replicas, a real, easy-to-miss availability gap (§3.5). (9) Regardless of outcome — success, policy denial, or failure — the gateway writes an **immutable, tool-call-level audit record** (§4.9) before considering the request complete, because per §4.11's threat landscape, a compromised or merely instruction-following model's own account of "what it did" must never be the only evidence available to an auditor.

---

## 2. Core Mechanics & Algorithms

### 2.1 Roles and the three primitives

MCP is a JSON-RPC 2.0 protocol connecting three roles: **Hosts** (the LLM application initiating connections), **Clients** (one connector per server, embedded in the host), and **Servers** (processes exposing context/capabilities). This split is what lets a single host multiplex many independently capability-negotiated sessions.

| Primitive | Direction | Discovery | Invocation | Controller |
|---|---|---|---|---|
| **Tools** | Server → Client | `tools/list` | `tools/call` | Model decides |
| **Resources** | Server → Client | `resources/list`, `resources/templates/list` | `resources/read` | Application decides |
| **Prompts** | Server → Client | `prompts/list` | `prompts/get` | User decides |

Two reverse-direction primitives exist: **Sampling** (`sampling/createMessage` — the *server* asks the client's own LLM for a completion) and **Elicitation** (`elicitation/request` — the server asks the client for structured user input). Tool inputs (and optionally outputs) are validated against JSON Schema 2020-12 — this is the invariant that makes a tool call machine-checkable independent of whether the model "reads the docs correctly."

**Invariant**: the *controller* column is not a convention, it is the security-relevant fact that governs where a defense must live — a Tool can be silently invoked by the model, so any tool with a side effect must be gated by deterministic policy (§4.5/4.7), not by hoping the model only calls it when a human would approve.

### 2.2 Capability negotiation: from stateful handshake to stateless self-description

**Legacy model (spec ≤ 2025-11-25)**: every session began with a blocking `initialize` → `initialized` round trip exchanging supported capabilities before any request could proceed — an `O(2)` extra round trip cost paid once per session, but one that pins the session to whichever server process/pod answered the `initialize` call.

**2026-07-28 model**: the handshake and session IDs are removed entirely. Every request is self-describing, carrying its own `io.modelcontextprotocol/protocolVersion` in `_meta`.

```
Legacy (≤2025-11-25):                         Current (2026-07-28):

Client            Server                      Client                    Server
  │──initialize──────▶│                          │──tools/call (protocolVersion   │
  │◀─capabilities──────│                          │   in _meta, self-describing)──▶│
  │──initialized──────▶│                          │◀────────────result─────────────│
  │──tools/call───────▶│                          (no prior round trip; a version
  │◀────result─────────│                           mismatch surfaces as a per-
  (session pinned to                               request error, not a session-
   this server instance                            level failure)
   via Mcp-Session-Id)
```

- **Complexity**: legacy = `O(1)` fixed handshake cost amortized over the session's lifetime, but `O(session)` coupling to one server instance. Current = `O(1)` per request with **zero** cross-request coupling — any healthy replica can answer any request, which is precisely why Google and Cloudflare cite this change as what let MCP scale behind an ordinary round-robin load balancer (§3.5).
- **Fallback probing**: a client uncertain of a server's era calls `server/discover` (stdio) or sends-and-inspects-the-400-body (Streamable HTTP) rather than hard-failing; a well-behaved legacy server should raise `UnsupportedProtocolVersionError` listing what it *does* support, not silently misbehave.
- **Invariant**: statelessness at the protocol layer does not imply statelessness at the *application* layer — a tool call can still mutate durable backend state; what's removed is the *transport session* pinning, not the backend's own consistency requirements (§4.2).

> ⚠️ Gap: the 2026-07-28 redesign is very recent (as of this research's date); ecosystem gateways and third-party servers still assuming the older handshake/`Mcp-Session-Id` model constitute a live, unquantified compatibility surface — no adoption-rate figure exists for how much of the installed base has migrated.

### 2.3 Transport mechanics

The spec defines exactly two current standard transports, plus one deprecated one:

| Transport | Mechanism | Scaling | Auth | Status |
|---|---|---|---|---|
| **stdio** | Client launches server as local subprocess; newline-delimited JSON-RPC over stdin/stdout | Single client per process; no horizontal scale | None built in — relies on OS process isolation | Current, local-only |
| **Streamable HTTP** | Single endpoint (e.g. `/mcp`); POST replies as plain JSON or an upgraded request-scoped SSE stream | Horizontal — stateless core means any replica answers any request | OAuth 2.1, multi-tenant | Current, the only current networked transport |
| **HTTP+SSE** (two endpoints: GET stream, POST messages) | Legacy dual-endpoint model | Poor — requires sticky routing to the GET-stream-holding instance | Ad hoc | **Deprecated since spec 2025-03-26**; must not be used in new builds |

A binding is purely a **framing/delivery** contract — message patterns and cancellation semantics (`notifications/cancelled`, or simply closing the stream) are transport-agnostic, so switching stdio↔HTTP does not change application-level protocol logic, only deployment topology.

**Invariant**: never write application logs to **stdout** on a stdio-transport server — stdout *is* the JSON-RPC wire; any stray `print()` corrupts the stream. Route all logging to stderr.

### 2.4 Gateway header-based routing (SEP-2243)

The `Mcp-Method` and `Mcp-Name` HTTP headers let a gateway route, authorize, and rate-limit **purely on headers**, without deserializing and parsing the JSON-RPC body.

```
def route_request(headers: dict, body_bytes: bytes) -> BackendServer:
    # O(1) header lookup — the JSON-RPC body is NEVER parsed at this layer.
    method = headers["Mcp-Method"]          # e.g. "tools/call"
    tool_name = headers.get("Mcp-Name")     # e.g. "github.create_issue"
    server = registry.resolve(tool_name)    # namespace lookup, O(1) amortized (hash map)
    if not policy_engine.allow(method, tool_name, caller_identity(headers)):
        raise Forbidden()                   # denial logged BEFORE body is ever touched, §4.9
    return server
```

- **Complexity**: `O(1)` amortized routing decision vs. `O(n)` (full JSON-RPC deserialize + schema walk) for body-based routing — this matters at gateway scale because a rejected/misrouted call now costs a header parse, not a full body parse, directly bounding the blast radius of a malformed or oversized payload.
- **Invariant**: because routing never inspects the body, the policy engine's decision must be made from headers + caller identity alone — any policy that needs to inspect *tool arguments* (argument-level RBAC, §4.5) necessarily happens one layer deeper, after routing, not at this hop.

### 2.5 Tool-discovery caching (SEP-2549)

`tools/list`, `prompts/list`, `resources/list`, and `resources/read` responses as of 2026-07-28 carry `ttlMs` and `cacheScope` fields, letting a client cache the catalog deterministically.

```
def get_tool_catalog(server_id: str, cache: dict) -> ToolCatalog:
    entry = cache.get(server_id)
    if entry and (now_ms() - entry.fetched_at_ms) < entry.ttl_ms:
        return entry.catalog                 # O(1), no round trip, no upstream cache invalidation
    catalog = server.tools_list()            # cold fetch
    cache[server_id] = CacheEntry(catalog, entry.ttl_ms if entry else catalog.ttl_ms, now_ms())
    return catalog
```

- **Invariant**: a cached catalog is *authoritative until `ttlMs` expires* — a server that changes a tool's schema mid-window has no push-invalidation channel in the base spec, so `ttlMs` **is** the staleness bound, not an optimization hint (§3.5's freshness-vs-cost trade-off).
- This directly counteracts the re-fetch cost the stateless redesign (§2.2) would otherwise impose — without session pinning, a naive client would re-fetch `tools/list` on every reconnect; `ttlMs` amortizes that cost and keeps upstream LLM-provider prompt caches stable across reconnects.

### 2.6 The Tasks primitive (async long-running operations)

2026-07-28 promotes **Tasks** out of experimental status: a long-running tool operation returns a durable handle immediately rather than blocking the request, and the client polls for completion. This is the MCP-level analog of a job queue, letting a tool call whose backend work takes minutes (a large data export, a multi-step provisioning operation) avoid holding a synchronous connection open for the duration.

> ⚠️ Gap: the source material confirms Tasks exist and are now non-experimental but does not enumerate the specific polling state names/schema; treat the exact wire-level state machine as an implementation detail to verify against the live 2026-07-28 spec text before building against it, not as settled here `[inferred generalization]`.

---

## 3. Token Economics & NFR Analysis

### 3.1 Tool-schema overhead vs. native function calling — cost formula

Every MCP tool definition ships name, description, full JSON Schema, field descriptions, and enums **on every turn** — there is no persistent server-side tool registration the model can rely on instead. Measured overhead: **550–1,400 tokens per tool**. A controlled Scalekit benchmark (Claude Sonnet 4, 75 head-to-head MCP-vs-CLI comparisons) found MCP cost **4×–32× more tokens** than an equivalent CLI operation; the simplest task tested (checking a repo's language) cost 1,365 tokens via CLI vs. **44,026 tokens via MCP**, because 43 tool definitions were injected up front for a task that used 1–2 of them.

```
Cost_MCP(1k runs) = 1000 × [ Σ(tools connected) tool_schema_tokens_i × price_in
                              + used_tool_call_tokens × price_in
                              + response_tokens × price_out ]

Cost_native_fn(1k runs) = 1000 × [ inlined_fn_schema_tokens (typically 1 app-owned,
                                     pre-trimmed set, not a full server catalog)
                                     × price_in
                              + response_tokens × price_out ]
```

*Stated assumptions* (illustrative calibration, not vendor-audited telemetry `[inferred]`): a 2026 mid-tier reasoning model at **$3/1M input, $15/1M output**; a modest 5-server MCP configuration injecting **~55,000 tokens** of tool schema per turn (measured, §3.2 below) vs. a hand-curated native function-calling set of **~3,000 tokens** for the same task set:

```
MCP (5-server config):    1000 × (55,000 × $3/1M + 800 × $15/1M)  = 1000 × $0.177  = $177.00 / 1k runs
Native function calling:  1000 × ( 3,000 × $3/1M + 800 × $15/1M)  = 1000 × $0.021  = $21.00  / 1k runs
```

This is a **~8.4× cost multiplier purely from schema overhead**, before accounting for any difference in output quality or latency — consistent with the independently corroborated academic figure of **~10k–60k tokens/turn "Tools Tax"** in typical multi-server deployments.

### 3.2 Real-world multi-server measurements and mitigations

| Configuration | Tool-schema tokens | % of 200K window |
|---|---|---|
| GitHub MCP alone (35 tools) | ~26,000 | 13% |
| Slack MCP alone (11 tools) | ~21,000 | 10.5% |
| GitHub + Playwright + IDE (3 servers) | ~143,000 | 72% |
| 5-server modest config | ~55,000 | 27.5% |
| 10-server power-user config | ~75,000 | 37.5% |
| Cloudflare, full native MCP (pre-Code Mode) | ~1,170,000 | exceeds any window |

Context utilization above a published **~70% fracture point** is associated with measurable reasoning degradation — meaning the 3-server row above is not just expensive, it is already past the point where the token spend itself degrades output quality.

**Measured mitigations:**

| Mitigation | Mechanism | Measured effect |
|---|---|---|
| **Anthropic Tool Search** (GA Feb 2026) | Subagent-gated tool loading instead of eager injection | Preserves **85% of context** vs. eager loading |
| **Cloudflare Code Mode** | Expose a sandboxed code-execution surface instead of per-tool schemas | **1.17M → ~1,000 tokens** (~99.9% reduction); Cloudflare's own 52-tool/4-server deployment: 9,400 → ~600 tokens (**94% reduction**), cost stays **flat** as more servers are added behind the portal |
| **"Tool Attention" middleware** (arXiv 2604.21816) | Intent–Schema Overlap gating + lazy schema loading | **95.0%** simulated per-turn token reduction (47.3k→2.4k); context utilization 24%→91% in a 120-tool/6-server benchmark — `[projected, not measured on live agents, per the paper's own framing]` |
| **Block's layered tool pattern** | Collapse N REST endpoints into 3 conceptual tools (discover/plan/execute) instead of 1-tool-per-endpoint | Square's 200+ endpoints → 3 tools; cited as the fix for "1:1 endpoint-to-tool doesn't scale" (frequent errors + context blowup) |

Practical guidance converges on a ceiling of **~30–40 always-loaded tools**, deferring the rest via search/lazy-loading.

### 3.3 Latency: P50/P95/P99 for MCP round-trips

No public source discloses one formal, composed P99 SLA spanning client → gateway → server → backend for MCP as a unit. The table below anchors every **measured** row to the benchmarks cited in the research and derives **inferred** rows using standard tail-compounding (an add-on network hop rarely moves P50 much but reliably fattens P99, because the hop's own tail latency stacks on top of the backend's):

| Stage | P50 | P95 | P99 | Dominant tail cause | Mitigation |
|---|---|---|---|---|---|
| stdio round trip (local pipe, simple tool) | **~1–2ms** `[inferred — local IPC, no network stack]` | ~3ms `[inferred]` | ~8ms `[inferred]` | Process scheduling jitter under host CPU contention | None needed at this layer; the bottleneck is never stdio itself |
| Streamable HTTP, simple/cached tool (echo-class) | **~10ms** (measured, load test) | ~25ms `[inferred]` | ~50ms `[inferred]` | Network hop + JSON-RPC (de)serialization | Keep-alive connection pooling, session-affinity avoidance (stateless core makes any replica valid) |
| Streamable HTTP, cached/simple production server (Microsoft Learn-class, Locust/Azure test) | Sub-second (**measured**, "sub-second p90" reported) | **sub-second (measured p90)** | Not disclosed | Backend cache hit path | N/A — already the good case |
| Streamable HTTP, synchronous embedding-backed server (Context7-class, Locust/Azure test) | **>1s on every call (measured)** | >1s (measured) | Higher — compounds with any queueing | Synchronous embedding computation on the critical path | Precompute/cache embeddings; move embedding generation off the tool-call hot path |
| Streamable HTTP, filesystem-style server (mcpbench reference run) | ~35ms `[inferred, backed out from p99]` | ~60ms `[inferred]` | **88ms @ ~98 RPS (measured)** | Local filesystem I/O + schema validation | CI regression gate flags >20% p95 degradation as a build failure — treat this as an enforced NFR, not just a dashboard number |
| GitHub-backed search tool (Locust/Azure test) | baseline | **2.8× avg-to-p90 ratio (measured)** | Higher still, unbounded by MCP layer | Upstream GitHub API rate limiting bleeding through the tool layer | Tool-layer caching + client-side backoff cannot fix an upstream-imposed ceiling; must be budgeted for explicitly in SLA design |
| Gateway header-routing overhead (SEP-2243, no body parse) | **+<1ms** `[inferred from O(1) header lookup vs O(n) body parse]` | +2ms `[inferred]` | +5ms `[inferred]` | Negligible vs. backend I/O in nearly all cases | N/A — this is the reason header-based routing was added |
| **Composed: client → gateway → networked server → backend (cached case)** | **~15ms** `[derived: HTTP hop + gateway overhead + cache hit]` | **~35ms** `[derived]` | **~70ms** `[derived, tail-compounded]` | Gateway + network hop stack on top of an already-fast backend | Session-pooling (below), tool-catalog caching (§2.5) |
| **Composed: client → gateway → networked server → backend (I/O-heavy/uncached case)** | **~1.1s** `[derived: dominated by backend I/O]` | **~2.5s** `[inferred, tail-compounded]` | **~4s+** `[inferred]` | Backend synchronous work dominates every other stage combined | This is the row that actually determines user-perceived latency; optimizing gateway overhead here is a rounding error |

**Session-pooling as a throughput/latency lever, not just a scaling concern.** ToolHive's Kubernetes benchmark found Streamable HTTP with **shared sessions sustaining 290–300 req/s**, vs. only **30–36 req/s with a unique session per request** — a **~10× difference** from session-pooling strategy alone. Legacy stdio-over-container-attachment architectures scaled far worse: one test recorded only **2 of 50 requests succeeding** under concurrency, because per-connection container spin-up cost dominated.

**Mitigation summary**: (1) prefer Streamable HTTP with pooled/shared sessions over stdio-per-request or unique-session-per-request patterns whenever horizontal scale matters; (2) treat upstream-API rate limits (GitHub-class) as a hard SLA input the MCP layer cannot paper over, not a tuning problem; (3) enforce p95 regression gates in CI the way mcpbench does, since MCP servers regress silently otherwise; (4) use `ttlMs` tool-catalog caching (§2.5) to remove the re-fetch round trip the stateless redesign would otherwise add on every reconnect.

### 3.4 Throughput: MCP gateway capacity planning and back-pressure

```
Sustained_gateway_throughput = min(
    Session_pool_capacity,                       # shared-session HTTP: ~290-300 req/s per ToolHive benchmark node
    Backend_API_rate_limit_per_dependency,        # e.g. GitHub API ceiling — bleeds through regardless of gateway tuning
    Sandbox_provisioning_rate,                    # gVisor ~500ms / Firecracker ~125ms cold-start bounds new-session rate
    Gateway_CPU_for_header_routing_and_PII_scan   # header-only routing is cheap; PII regex/NLP scanning is not
)
```

- **Back-pressure design**: (1) a **token bucket per (caller, server)** for admission control, independent of any single tool's own limits; (2) when a backend circuit breaker opens (§4.3), the gateway should return a structured `429`/breaker-open signal upstream rather than queuing indefinitely, so callers reduce dispatch rate proportionally instead of retrying into a known-degraded dependency; (3) **shared-session pooling is itself a back-pressure lever** — the ToolHive measurement shows pool strategy alone is a ~10× throughput swing, meaning capacity planning must budget per-session-strategy, not just per-hardware-unit.
- **Sandbox tier is a capacity constraint, not just a security dial**: a gVisor server costing ~500ms to cold-start bounds how fast the gateway can spin up fresh isolated sessions under a traffic spike; Firecracker's ~125ms start is faster but requires KVM/bare-metal, which itself constrains where the gateway can autoscale to (§4.10).
- **Tool-schema token cost is a per-connected-server capacity input**, not just a conversation-level cost: a naive 3-server/40-tool deployment can consume **>70% of a 200K-token window before any user content is processed** (§3.2), so capacity planning for multi-tool agents must budget token cost **per connected server**, not just per turn.

> ⚠️ Gap: no purpose-built "MCP gateway throughput" industry-standard benchmark spans the full admission-control → routing → backend chain as one number; the formula above composes independently measured sub-benchmarks (ToolHive, mcpbench, ToolHive) rather than a single validated end-to-end figure.

### 3.5 NFR analysis: availability, RPO/RTO tied to session/state persistence, and compliance trade-offs

No vendor publishes a composed availability SLA scoped to "one MCP client→gateway→server call" as a unit; every figure below beyond the topology-level statelessness claim is an **`[inferred/recommended]`** design target — stated explicitly because this is exactly the section most commonly audited for these numbers.

**Availability targets by deployment pattern:**

| Deployment pattern | Availability target | Basis |
|---|---|---|
| stdio, single local subprocess, no supervision | **~99%** (~87.6h/year downtime) `[inferred]` | Process death = total failure for that session; no HA concept applies to a local subprocess |
| Legacy HTTP+SSE, sticky session pinned via `Mcp-Session-Id` | **~99.5%** `[inferred]` | Documented "hard wall" (Google/Cloudflare accounts): pod restarts/autoscale/deploys break pinned sessions; drain-on-deploy logic is complex and imperfect |
| Streamable HTTP, 2026-07-28 stateless core, round-robin LB across replicas | **99.9%** `[inferred, directly enabled by the spec change]` | Any healthy replica can serve any request — pod restarts and rollouts become invisible to the client, per Google's and Cloudflare's own published rationale for the redesign |
| + per-backend circuit breakers and fallback chains at the gateway (§4.3) | **99.95%** `[inferred]` | A single degraded backend dependency degrades that capability, not the whole gateway's availability |
| + multi-region gateway with externalized (Redis-backed) EventStore for genuine SSE resumability | **99.99%** (~52min/year) `[inferred]` | Removes single-region infra as a common-mode failure; residual risk is a correlated multi-provider outage across regions simultaneously |

**RPO/RTO tied to session/state persistence granularity:**

| Persistence tier | Mechanism | RPO | RTO |
|---|---|---|---|
| Stateless core request (2026-07-28, no session ID) | Nothing persisted by the protocol itself | **N/A by design** — but a dropped connection must be **retried from scratch**, which is only safe if the tool call is idempotent (§4.4) | **Near-zero** — any healthy replica answers the retry immediately |
| SDK-default in-memory `EventStore` (SSE resumability) | Lives in a single process | **Total loss of unflushed session state on restart** `> ⚠️ Gap: a real, easy-to-miss production risk, not a theoretical one` | **Not recoverable** — returns 404 to a `Last-Event-ID` replay attempt; client must start over |
| Redis-backed externalized `EventStore`, keyed session+stream, with eviction cap | Shared, durable store outside any single process | **Near-zero** for events written before the crash | **Seconds** — client resumes via `Last-Event-ID` replay against the shared store |
| Tool-catalog cache (`ttlMs`/`cacheScope`, SEP-2549) | Client-local cache, authoritative until TTL expiry | **Up to `ttlMs` staleness window** if the server changes a tool's schema mid-window | **Zero** — cache serves (possibly stale) data with no blocking revalidation |
| Durable workflow wrapper (Dapr `MCPServer` / Temporal Activities) | Tool call becomes a workflow/Activity; result journaled independently | **Near-zero** — a completed Activity's result is durably recorded before the caller proceeds | **Seconds–minutes** — Dapr Scheduler redelivers pending activities on `daprd` restart; Temporal replays Event History without re-issuing already-completed side effects |

**Trade-off 1 — stdio simplicity vs. HTTP scalability.** stdio has near-zero network overhead and no auth infrastructure to operate, making it the correct default for local, single-user, trusted-process tools (a CLI coding assistant's filesystem server). But it structurally cannot scale past one client per process, has no session-independent failover, and has no built-in authentication — appropriate only when OS-level process isolation is an acceptable trust boundary. Streamable HTTP trades that simplicity for OAuth support, multi-tenancy, and horizontal scale, at the cost of operating an authorization server, TLS termination, and discovery-metadata endpoints (§4.6) that a stdio deployment never needs. The 2025-11-25 spec revision makes this trade-off non-optional for public servers: **any internet-reachable MCP server MUST implement OAuth 2.1 with PKCE** — stdio/localhost servers are explicitly exempt, formalizing "local-trust stdio, internet-facing OAuth-HTTP" as the sanctioned split rather than a matter of taste.

**Trade-off 2 — tool-discovery caching vs. freshness.** `ttlMs` caching (§2.5) is what makes the stateless redesign's per-reconnect re-fetch cost tolerable and keeps upstream LLM-provider prompt caches stable — but it is also a **hard staleness bound**, not a soft hint: a server that changes a tool's argument contract mid-TTL-window has no push-invalidation path in the base spec, so a client can call a tool with a schema the server has already moved past. The correct dial is picking `ttlMs` short enough to bound this staleness blast radius (seconds-to-low-minutes for frequently-changing internal tools) while long enough to actually amortize the refetch cost (longer for stable, versioned public servers) — there is no single correct value, only a per-server risk/cost calibration.

**Compliance mapping.** RBAC enforcement at the gateway maps to **EU AI Act** and **HIPAA** obligations; the immutable audit trail maps to **SOC 2** and **GDPR Article 30** records-of-processing requirements; the token vault (short-lived, scope-limited credentials never exposed to the agent) maps to **GDPR** and **SOC 2** as well. Critically, a **Zero Data Retention gateway architecture** is required to keep this mapping intact: if the gateway persists raw tool-response bodies to disk (even for debugging), it legally becomes a **sub-processor**, expanding SOC 2/HIPAA/GDPR audit scope beyond what a pure pass-through proxy would carry (§4.8).

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution across MCP calls

For multi-step tool logic that must survive process crashes, teams offload to external workflow engines rather than reimplementing retry/resume inside the MCP server itself:

- **Dapr `MCPServer` resource**: auto-registers a durable workflow orchestration per discovered tool — a tool call *becomes* "start a workflow." The Dapr Scheduler re-delivers pending activities to a new instance on `daprd` restart, and Dapr keeps one warm session per backend MCP server with automatic reconnect-once-on-`ErrConnectionClosed`.
- **Temporal**: wraps each MCP tool as a thin invoker of a Temporal **Workflow**; all business logic and external API calls execute as **Activities** with configurable automatic retry policies, guaranteeing eventual completion despite process restarts or network failures. The critical constraint carried over from durable-execution systems generally: never call an LLM or a non-deterministic tool directly inside the Workflow function itself — wrap it in an Activity, or replay will re-issue the call and can diverge from what actually happened.

### 4.2 Statefulness, session management, and the resumability trade-off

Google and Cloudflare both published detailed accounts of hitting a "hard wall" scaling MCP on cloud-native infrastructure because the pre-2026 protocol pinned clients to specific pods via `Mcp-Session-Id`, forcing sticky-session load balancing, complex drain-on-deploy logic, and broken sessions on autoscale/restart. The **2026-07-28 spec removes the handshake and session ID entirely** (SEP-2575, SEP-2567), making the protocol core stateless: pod restarts, rollouts, and autoscaling become invisible to the client, and requests can hit any healthy replica behind an ordinary round-robin load balancer.

**The trade-off is not free**: stateless mode survives process restarts trivially (there is nothing to lose) but gives up mid-stream resume — a dropped connection must be **retried from scratch**, which is only safe if the underlying tool call is idempotent (§4.4). An application that genuinely needs SSE resumability must externalize the event log to a shared, durable store, because SDK-default `EventStore` implementations live in single-process memory (§3.5).

### 4.3 Circuit breakers and the resilience onion

Convergent industry guidance recommends a fixed layering, executed in this order around every external call an MCP tool makes:

1. **Rate limiter** (admission control)
2. **Bulkhead** (isolate resource pools per dependency, preventing cascading exhaustion)
3. **Circuit breaker** — Closed→Open→Half-Open, typical threshold ~5 consecutive failures, ~60s cooldown before a Half-Open probe
4. **Retry with exponential backoff + jitter** (avoids thundering herd)
5. **Timeout** (bounds latency per attempt, not the whole retry budget)
6. **Fallback** (cached/partial/default response)

**MCP-specific guidance**: implement circuit breakers **per external dependency, not per tool**, since multiple tools frequently share one backend API — a breaker per tool would let a still-healthy tool on a *broken* dependency keep hammering it. Surface breaker state directly in the **tool-error message text** (e.g., "circuit breaker is open — will retry automatically at 14:32:00") so the calling LLM can reason about whether to retry, fall back, or alert the user, rather than blindly re-invoking a call it has no way to know is doomed. And always route logs to **stderr**, never stdout, on stdio-transport servers — stdout corruption of the JSON-RPC stream is a silent, hard-to-diagnose failure mode.

> ⚠️ Gap: no vendor-neutral, production-scale benchmark quantifies MTBF/error-budget improvement from these patterns specifically for MCP (as opposed to microservices generally) — the prescriptions above are pattern guidance, not measured outcome deltas.

### 4.4 Failure taxonomy, poison-pill detection, and idempotency

| Class | Definition | MCP-specific example | Mitigation |
|---|---|---|---|
| **Transient** | Resolves on retry without intervention | Backend 5xx/timeout, rate-limit 429 from an upstream API bleeding through the tool | Retry with jittered exponential backoff; honor `Retry-After`; never re-delegate to a different server for this class |
| **Permanent** | Fails identically on every retry | A tool's declared schema no longer matches the server's actual handler (schema drift, §4.11); a required scope was revoked mid-session | Never retry blind — surface the structured error and require an explicit re-authorization or re-discovery step |
| **Poison-pill** | A specific (server, call) pair deterministically breaks every attempt | A malformed argument set that crashes a specific tool's parser every time; a duplicate/replayed call under at-least-once delivery from infrastructure above the MCP layer | Idempotency-keyed claim-before-execute + dead-letter after N attempts, surfaced structurally rather than looping indefinitely |

**Idempotency is not optional under the 2026-07-28 stateless model**: because a dropped connection is retried **from scratch** rather than resumed, any tool with a side effect (a write, a charge, an email send) must be safe to invoke twice with the same arguments — an idempotency key derived from the call's content hash is the standard mechanism, checked-and-claimed atomically before the side effect executes.

### 4.5 Zero-Trust MCP architecture

This is the security core of the topic. Applying **NIST SP 800-207** to MCP: no user, client, server, token, tool, workload, package, or network location receives automatic trust — every request must be authenticated, authorized against a *specific* resource server, scoped to least privilege, evaluated against live context, executed inside an isolated boundary, and continuously monitored. This decomposes into four concrete, enforceable mechanisms:

1. **On-Behalf-Of (OBO) token flows to prevent Confused Deputy attacks.** An agent acting on a user's behalf must exercise *only the calling user's* permissions, never the agent's own standing service-account power. Without OBO, a compromised or merely over-eager agent inherits whatever broad service-account scope the *agent process itself* was granted — the classic confused-deputy pattern, now with an LLM in the loop deciding what to do with that inherited power.

2. **Workload identity, layered with user identity, not substituting for it.** SPIFFE/SPIRE, AWS IRSA, or Azure Managed Identity issue cryptographic identity to *ephemeral* MCP server processes/containers (SPIFFE IDs take the form `spiffe://<trust-domain>/mcp-server/<name>/<instance-id>`). This is deliberately combined with the caller's OBO user identity for **defense-in-depth**: a valid call requires *both* correct user permission *and* a verified workload origin — a stolen user token alone cannot authorize a call from an unverified workload, and a compromised workload alone cannot exercise permissions beyond whatever user token it's currently forwarding.

3. **OAuth 2.0 Token Exchange (RFC 8693) to narrow scope at every hop.** In a multi-server call chain, passing one all-powerful user token end-to-end means every downstream server sees the same broad scope the *first* hop had. Token Exchange lets each hop mint a **narrower, audience-scoped** token for the next hop — the structural enforcement of "trust narrows, never widens, across a delegation chain."

4. **Tool-level and argument-level authorization on every `tools/call`, never just at connect time.** Deterministic policy enforcement (an OPA/Cedar-style Policy Decision Point) evaluates each individual call — not "is this client allowed to talk to this server," but "is this specific tool call, with these specific arguments, allowed for this specific identity right now." This is explicitly **not** dependent on the model "following instructions" — a prompt that tells the model not to delete production data is advisory; a PDP that denies the `delete_production_table` tool call for this identity is enforcement.

**Why this must live below the model, not inside the prompt**: the entire Tool Poisoning Attack class (§4.11) works *because* it exploits superior instruction-following in more capable models — a defense that depends on the model correctly interpreting adversarial input is defending against the exact mechanism the attack uses. Zero-Trust MCP's core claim is that authorization must be a property of the infrastructure the call passes through, verifiable independent of what the model "decided" to do.

### 4.6 OAuth for MCP

The MCP server acts strictly as an **OAuth 2.1 resource server** — it validates tokens but never issues them; a separate authorization server (co-hosted or fully external) mints tokens. Mandatory mechanisms as of spec 2025-06-18/2025-11-25:

- **RFC 9728** (Protected Resource Metadata): servers **MUST** serve `/.well-known/oauth-protected-resource`; on a `401`, the `WWW-Authenticate` header **MUST** point to this metadata plus a required `scope`.
- **RFC 8414** (Authorization Server Metadata): the AS **MUST** publish its own discovery document so clients can locate authorization/token endpoints without hardcoding them.
- **RFC 8707** (Resource Indicators): clients **MUST** include a `resource` parameter (the MCP server's canonical URI) in both authorization and token requests, and servers **MUST** validate the returned token's `aud` claim matches — this is the **primary defense against token replay across MCP servers**: a token minted for server A cannot be replayed against server B even if both trust the same AS.
- **PKCE with S256 is mandatory**; the `plain` challenge method is forbidden, and a client must refuse to proceed if the AS doesn't advertise `code_challenge_methods_supported`.
- As of **2025-11-25**, any **internet-reachable MCP server MUST implement OAuth 2.1 with PKCE** — a static "paste an API key" auth model is explicitly non-compliant for public endpoints (stdio/localhost servers remain exempt, §3.5's stdio-vs-HTTP trade-off).

### 4.7 Tool-level RBAC and gateway governance

MCP has no built-in enterprise RBAC — organizations must centralize it at a gateway/control-plane layer fronting every downstream server: auth (OAuth 2.1/SSO termination via Entra ID/Okta), RBAC (per-user/per-role allowed server+tool+scope combinations), audit (atomic tool-call-level structured logs — timestamp, user identity, agent identity, tool name, parameters, response summary, policy decision), rate limiting, and policy (reject poisoned tool descriptions, enforce least privilege, redact PII).

A documented industry deployment pattern uses a **two-axis auth model** — persona (interactive user vs. automated non-user) × credential type (no-auth, static/dynamic API key, PKCE-authcode, client-credentials, platform app-context) — served through one MCP endpoint, plus three enterprise SSO grant types (Auth Code+PKCE, Device Code, ROPC) and three token-provisioning models (Bring-Your-Own-Token, Generate-Your-Own-Token, delegated OAuth via RFC 8693). **Recommended rollout**: deploy the gateway in **logging-only mode** for several weeks to baseline traffic before enabling active enforcement, rate limiting, and tool-hash pinning — flipping straight to enforce-mode against unknown traffic patterns is a documented anti-pattern. Vendor/pattern landscape spans Kong AI MCP Proxy, Azure API Management MCP (Entra ID, policy expressions), Cloudflare AI Gateway/MCP portals, MintMCP (SCIM-driven RBAC + Virtual MCP Bundles), and Operant MCP Gateway (SPIFFE/SPIRE-based).

### 4.8 PII filtering at the MCP boundary (detect → redact → audit)

Standard endpoint DLP tooling cannot see MCP tool-call payloads, so redaction must move to an **inline gateway/proxy layer**:

- **Classification**: regex for structured PII (SSNs, keys, card numbers) plus NLP models (e.g., Microsoft Presidio) for unstructured PII (names, addresses).
- **Redaction before context injection**: mask/tokenize/hash sensitive fields in the tool *response* **before it ever reaches the model's context window** — redacting after the fact is not redaction, it's a log entry about a leak that already happened.
- **Zero Data Retention architecture**: the gateway must act as a pure pass-through proxy, resolving OAuth tokens and applying redaction transforms **in memory only**; persisting raw tool-response bodies to disk turns the platform into a legal **sub-processor**, expanding SOC 2/HIPAA/GDPR audit scope (§3.5).
- Regulatory mapping: RBAC → EU AI Act, HIPAA; audit trail → SOC 2, GDPR; token vault → GDPR, SOC 2.

### 4.9 Auditability: immutable logs and chain-of-custody

Every tool call — success, denial, or failure — should generate an **atomic, tool-call-level audit record**: timestamp, user identity, agent identity, tool name, parameters, response summary, and the policy decision that permitted or denied it. **Denied calls are auditable events, not silent drops** — a denial with no record is indistinguishable, after the fact, from a call that was never attempted. This record must be written by the **enforcement layer** (the gateway), not derived from the agent's own self-reported account of what it did — an agent's narration of its actions is not evidence, because a poisoned or manipulated agent can narrate falsely while the underlying infrastructure log cannot.

### 4.10 Sandbox isolation for MCP servers

Three tiers, trading startup latency for isolation strength:

| Approach | Startup | Isolation | Example use |
|---|---|---|---|
| OS-level (bubblewrap/seatbelt) | <10ms | Process-level | Anthropic Claude Code CLI (local) |
| gVisor (userspace kernel intercepting syscalls) | ~500ms | Container+ | Anthropic Claude web, multi-tenant cloud |
| Firecracker microVM | ~125ms | Hardware/VM-level (dedicated kernel) | Vercel Sandbox, "paranoid" managed platforms |

A documented gVisor test running Anthropic's own reference filesystem MCP server under 60+ adversarial inputs (`--network none`, `--cap-drop ALL`, `--read-only`) blocked all network calls, sensitive-path writes, process spawning, and `/proc`/`/etc/shadow` access attempts — demonstrating syscall-level containment even against a compromised or malicious server binary.

### 4.11 The live threat landscape: TPA, CVEs, supply chain, and schema drift

**Tool Poisoning Attacks (TPA).** Disclosed by Invariant Labs (April 2025): malicious instructions embedded in tool *metadata* (descriptions, parameter docs) are invisible in the user's UI but fully visible to the LLM, and can manipulate the agent into exfiltrating data or taking unauthorized actions **without the poisoned tool ever needing to be executed**. When multiple MCP servers share a client context, a single malicious server can poison descriptions to hijack credentials meant for a *different*, trusted server.

The **MCPTox benchmark** (45 live real-world MCP servers, 353 authentic tools, 1,312 adversarial test cases, 20 LLM agents) quantifies severity: average attack success rate **36.5%** across all 20 models, highest **72.8%** (OpenAI o1-mini). The counterintuitive finding — **more capable models are often more susceptible**, because the attack exploits superior instruction-following, and the best-performing refuser (Claude 3.7 Sonnet) still refused less than 3% of attacks while complying in ~34% of poisoned-tool cases. A companion STRIDE/DREAD study across 5 MCP components identified **57 distinct threats**, rating tool poisoning the most prevalent client-side vulnerability across all 7 major MCP clients tested; the **MCPLib** taxonomy catalogs **31 distinct attack methods** across direct/indirect tool injection, malicious user attacks, and LLM-inherent attacks.

**Named CVEs (selected, by severity):**

| Date | Incident/CVE | CVSS | Description |
|---|---|---|---|
| Jun 2025 | CVE-2025-49596 (Anthropic MCP Inspector) | 9.4 | Unauthenticated RCE via browser/DNS rebinding + `0.0.0.0` binding |
| Jul 2025 | CVE-2025-6514 (`mcp-remote`, 437K+ downloads) | 9.6 | OS command injection via malicious OAuth `authorization_endpoint` — first documented full RCE on client OS from an untrusted remote MCP server |
| Jul 2025 | CVE-2025-54136 "MCPoison" (Cursor) | 7.2–8.8 | Trust bound to server *name* not contents — editing an approved shared `.cursor/mcp.json` silently swapped in a malicious command, enabling team-wide compromise from one committed file |
| Aug 2025 | CVE-2025-54135 "CurXecute" (Cursor) | 9.8 | Workspace-file write via prompt injection → RCE through MCP auto-start |
| Sep 2025 | Flowise CustomMCP node | 10.0 | STDIO transport → RCE |
| Jan 2026 | CVE-2025-68143/68144/68145 (`mcp-server-git`) | up to 9.1 | 3 chained flaws incl. path traversal, argument injection |
| Mar 2026 | CVE-2026-33032 "MCPwn" (nginx-ui) | 9.8 | Auth bypass → RCE, actively exploited |
| Jan–Apr 2026 | OX Security: systemic STDIO command injection across official SDKs | Critical (by-design) | 10 CVEs spanning Python/TypeScript/Java/Rust SDK consumers; est. **200,000 vulnerable servers, 150M+ combined downloads**; Anthropic has declined to patch some as "by design" |

As of August 2026 the community-maintained `mcp-cve-project` indexes **313 CVEs** across the MCP ecosystem. Independent scans found **30–82% of public MCP servers carry exploitable flaws**, and only **8.5% use OAuth**. In a single 60-day window in early 2026, **30+ CVEs** were filed, of which **43% were command-injection patterns**.

**Supply-chain risk mechanics.** The dominant install pattern — `npx -y some-mcp-server` or `uvx some-mcp-server` — resolves the full transitive dependency tree from a public registry and executes it with the **full privileges of the host** *before any MCP handshake even begins*; `postinstall`/`preinstall` lifecycle scripts run at install time, meaning runtime MCP-layer policy enforcement (allowlists, gateway auth) **cannot intercept the payload** — the compromise happens upstream of anything Zero-Trust MCP (§4.5) is positioned to defend. Unpinned `@latest` installs bet against a compromise window measured in **hours**. Container isolation with restricted egress (e.g., Stacklok's ToolHive) was independently confirmed effective against the Sept 2025 npm attack that hit foundational JS/TS packages indirectly depended on by the official MCP TypeScript SDK. The **Official MCP Registry** (Anthropic + GitHub + PulseMCP + Microsoft) provides namespace-verified metadata but explicitly does **not** perform code security scanning — that responsibility is delegated to npm/PyPI/Docker Hub and downstream marketplace aggregators, a division of responsibility that directly contributes to this risk profile.

**Schema drift** is a distinct, quieter failure class: the published `tools/list` JSON Schema can diverge from what the handler code actually expects (a field marked optional but dereferenced unconditionally; `userId` vs. `user_id` casing mismatches) — invisible to protocol-level validation and only caught by integration tests exercising the real handler against the declared schema, not just schema-conformance tests against a mock.

---

## 5. Production Enterprise Code

The implementation below is an MCP-gateway client dispatching `tools/call` requests to multiple backend MCP servers, wiring together every pattern from §3–§4: retries with exponential backoff + full jitter, a per-backend-dependency circuit breaker (CLOSED→OPEN→HALF_OPEN, never per-tool), a fallback chain (alternate server exposing the same capability → last-known-good cached response → structured degraded error), content-hash idempotency keys (required because the 2026-07-28 stateless core retries from scratch, §4.4), PII detect→redact→audit before any response re-enters the caller's context (§4.8), RFC 8707-style audience validation on inbound bearer tokens (§4.6), and structured JSON logging correlated by a delegation identity that survives thread-pool worker boundaries (§4.9). Standard library only.

```python
"""
mcp_gateway_client.py

A hardened MCP-gateway client dispatching tools/call requests across
multiple backend MCP servers, demonstrating every pattern from Module 10
(MCP & Interoperability) Sec 3-4:

  - retries with exponential backoff + full jitter for transient
    backend failures (Sec 4.4 transient/permanent/poison-pill taxonomy)
  - a per-backend-dependency circuit breaker: CLOSED -> OPEN -> HALF_OPEN,
    scoped per backend server, never per tool (Sec 4.3)
  - a fallback chain: alternate server exposing the same capability ->
    last-known-good cached response -> structured degraded error (Sec 4.3)
  - content-hash idempotency keys per dispatched call, required because
    the 2026-07-28 stateless core retries a dropped call from scratch,
    never resumes it (Sec 2.2/4.4)
  - PII detect -> redact -> audit BEFORE a tool response re-enters the
    caller's context (Sec 4.8)
  - RFC 8707-style audience ("aud") validation on the bearer token
    presented for each backend, defending against cross-server token
    replay (Sec 4.6)
  - structured JSON logging correlated by delegation identity
    (origin_sub + request_id) that survives ThreadPoolExecutor workers,
    where Python's contextvars do NOT auto-propagate (Sec 4.9)
  - graceful degradation: the gateway returns a "partial_degraded"
    result with an explicit list of which calls fell back, rather than
    failing the whole batch outright

Install:  no dependencies (stdlib only; swap Mock* server functions for
          a real MCP client transport -- stdio subprocess or Streamable
          HTTP session -- in production)
Run:      python mcp_gateway_client.py
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import hashlib
import json
import logging
import random
import re
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


# --------------------------------------------------------------------------
# 1. Structured logging correlated by delegation identity (Sec 4.9)
# --------------------------------------------------------------------------

_origin_sub: ContextVar[str] = ContextVar("origin_sub", default="-")
_request_id: ContextVar[str] = ContextVar("request_id", default="-")


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.origin_sub = _origin_sub.get()
        record.request_id = _request_id.get()
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("mcp_gateway")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"origin_sub":"%(origin_sub)s","request_id":"%(request_id)s",'
            '"msg":%(message)s}'
        )
    )
    handler.addFilter(CorrelationFilter())
    logger.handlers = [handler]
    logger.propagate = False
    return logger


log = configure_logging()


def bind_correlation_context(origin_sub: str, request_id: str) -> None:
    """contextvars are per-OS-thread and do NOT propagate automatically
    into ThreadPoolExecutor worker threads (unlike asyncio's
    run_in_executor, which copies the caller's Context). Every dispatch
    function below re-binds explicitly at entry so audit log lines
    emitted deep inside a pooled worker thread still carry the correct
    origin_sub/request_id -- the chain-of-custody invariant from Sec 4.9."""
    _origin_sub.set(origin_sub)
    _request_id.set(request_id)


# --------------------------------------------------------------------------
# 2. Failure taxonomy (Sec 4.4): transient vs. permanent vs. poison-pill
# --------------------------------------------------------------------------

class McpError(Exception):
    """`transient=False` marks permanent errors (schema drift, revoked
    scope) that must never be retried against the same backend -- these
    route straight to the fallback chain instead."""

    def __init__(self, message: str, transient: bool = True):
        super().__init__(message)
        self.transient = transient


class TokenAudienceError(Exception):
    """Raised when a bearer token's aud claim does not match the target
    server's canonical URI -- the RFC 8707 anti-replay check (Sec 4.6).
    This is a hard authorization failure, never retried."""


# --------------------------------------------------------------------------
# 3. RFC 8707-style audience validation (Sec 4.6) -- Zero-Trust boundary
# --------------------------------------------------------------------------

def validate_token_audience(token: dict, server_canonical_uri: str) -> None:
    """A token minted for server A must never be accepted by server B,
    even if both trust the same authorization server -- this is the
    primary defense against cross-server token replay."""
    if token.get("aud") != server_canonical_uri:
        raise TokenAudienceError(
            f"token aud={token.get('aud')!r} does not match "
            f"target server {server_canonical_uri!r}"
        )


# --------------------------------------------------------------------------
# 4. PII detect -> redact -> audit at the MCP boundary (Sec 4.8)
# --------------------------------------------------------------------------

_PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "card": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
}


def redact_pii(response_text: str, request_id: str) -> tuple[str, list[str]]:
    """Redacts BEFORE the response ever reaches the caller's context --
    redacting after logging/forwarding is not redaction (Sec 4.8). Returns
    the redacted text plus which PII classes were found, for the audit
    record -- detection is itself an auditable fact, not just the redaction."""
    found: list[str] = []
    redacted = response_text
    for label, pattern in _PII_PATTERNS.items():
        if pattern.search(redacted):
            found.append(label)
            redacted = pattern.sub(f"[REDACTED_{label.upper()}]", redacted)
    if found:
        log.info(json.dumps({"event": "pii_redacted", "request_id": request_id,
                              "classes": found}))
    return redacted, found


# --------------------------------------------------------------------------
# 5. Retry with exponential backoff + full jitter (Sec 4.3/4.4)
# --------------------------------------------------------------------------

def backoff_with_full_jitter(attempt: int, base_s: float = 0.05, cap_s: float = 1.5) -> float:
    upper_bound = min(cap_s, base_s * (2 ** attempt))
    return random.uniform(0, upper_bound)


def call_with_retry(fn: Callable[[], dict], server_name: str,
                     max_attempts: int = 3, base_s: float = 0.05, cap_s: float = 1.5) -> dict:
    last_error: Optional[McpError] = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except McpError as exc:
            last_error = exc
            if not exc.transient:
                log.info(json.dumps({"event": "retry_aborted_permanent_error",
                                      "server": server_name, "reason": str(exc)}))
                raise
            if attempt < max_attempts - 1:
                delay = backoff_with_full_jitter(attempt, base_s, cap_s)
                log.info(json.dumps({"event": "retry_backoff", "server": server_name,
                                      "attempt": attempt + 1, "delay_s": round(delay, 3)}))
                time.sleep(delay)
    assert last_error is not None
    raise last_error


# --------------------------------------------------------------------------
# 6. Circuit breaker: CLOSED -> OPEN -> HALF_OPEN, per BACKEND (Sec 4.3)
# --------------------------------------------------------------------------

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold_ratio: float = 0.6
    window_size: int = 5
    cooldown_s: float = 6.0
    half_open_max_probes: int = 1

    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _outcomes: list = field(default_factory=list, init=False)
    _opened_at: float = field(default=0.0, init=False)
    _half_open_probes_used: int = field(default=0, init=False)

    def _failure_ratio(self) -> float:
        if not self._outcomes:
            return 0.0
        return sum(1 for ok in self._outcomes if not ok) / len(self._outcomes)

    def allow_request(self) -> bool:
        if self._state == BreakerState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = BreakerState.HALF_OPEN
                self._half_open_probes_used = 0
                log.info(json.dumps({"event": "breaker_half_open", "server": self.name}))
            else:
                return False
        if self._state == BreakerState.HALF_OPEN:
            if self._half_open_probes_used >= self.half_open_max_probes:
                return False
            self._half_open_probes_used += 1
        return True

    def record_success(self) -> None:
        self._outcomes.append(True)
        self._outcomes = self._outcomes[-self.window_size:]
        if self._state == BreakerState.HALF_OPEN:
            self._state = BreakerState.CLOSED
            self._outcomes.clear()
            log.info(json.dumps({"event": "breaker_closed", "server": self.name}))

    def record_failure(self) -> None:
        self._outcomes.append(False)
        self._outcomes = self._outcomes[-self.window_size:]
        if self._state == BreakerState.HALF_OPEN:
            self._trip("half_open_probe_failed")
            return
        if len(self._outcomes) >= self.window_size and self._failure_ratio() >= self.failure_threshold_ratio:
            self._trip("failure_ratio_exceeded")

    def _trip(self, reason: str) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = time.monotonic()
        log.info(json.dumps({"event": "breaker_open", "server": self.name, "reason": reason,
                              "failure_ratio": round(self._failure_ratio(), 3)}))


_BREAKERS: dict[str, CircuitBreaker] = {}


def get_breaker(server_name: str) -> CircuitBreaker:
    if server_name not in _BREAKERS:
        _BREAKERS[server_name] = CircuitBreaker(name=server_name)
    return _BREAKERS[server_name]


# --------------------------------------------------------------------------
# 7. Idempotency keys per dispatched call (Sec 2.2/4.4) -- required
#    because a dropped stateless-core call is retried FROM SCRATCH
# --------------------------------------------------------------------------

def dispatch_idempotency_key(tool_name: str, server_name: str, args: dict) -> str:
    payload = f"{tool_name}:{server_name}:{json.dumps(args, sort_keys=True)}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# 8. Backend server registry + capability-based routing (Sec 4.7 gateway)
# --------------------------------------------------------------------------

@dataclass
class McpServerProfile:
    name: str
    canonical_uri: str          # RFC 8707 audience for this server
    capability: str
    fn: Callable[[dict], dict]  # stand-in for a real stdio/HTTP tools/call


class GatewayRegistry:
    """Routes a capability request to a healthy backend server, skipping
    any server whose breaker is OPEN -- routing and circuit-breaking
    share one source of truth (Sec 4.3)."""

    def __init__(self, servers: list[McpServerProfile]):
        self._by_capability: dict[str, list[McpServerProfile]] = {}
        for s in servers:
            self._by_capability.setdefault(s.capability, []).append(s)

    def route(self, capability: str, exclude: Optional[set] = None) -> Optional[McpServerProfile]:
        exclude = exclude or set()
        candidates = [
            s for s in self._by_capability.get(capability, [])
            if s.name not in exclude and get_breaker(s.name).allow_request()
        ]
        return candidates[0] if candidates else None


# --------------------------------------------------------------------------
# 9. Mock backend MCP servers (specialists + last-known-good cache)
# --------------------------------------------------------------------------

_LAST_KNOWN_GOOD: dict[str, dict] = {}


def make_backend(name: str, fail_rate: float) -> Callable[[dict], dict]:
    def _run(args: dict) -> dict:
        if random.random() < fail_rate:
            raise McpError(f"{name} tools/call failed (backend 5xx)", transient=True)
        return {"server": name,
                "text": f"result for {args.get('query', '?')} -- contact: user@example.com"}
    return _run


# --------------------------------------------------------------------------
# 10. Gateway dispatch: one tool call, with fallback chain (Sec 4.3/4.8)
# --------------------------------------------------------------------------

@dataclass
class GatewayResult:
    call_id: str
    server: str
    status: str
    text: Optional[str] = None
    pii_redacted: list = field(default_factory=list)
    degraded: bool = False


def dispatch_tool_call(registry: GatewayRegistry, capability: str, call_id: str,
                        args: dict, bearer_token: dict, origin_sub: str,
                        request_id: str) -> GatewayResult:
    bind_correlation_context(origin_sub, request_id)  # re-bind: runs in a pool thread

    server = registry.route(capability)
    if server is None:
        cached = _LAST_KNOWN_GOOD.get(capability)
        if cached is not None:
            log.info(json.dumps({"event": "audit", "call_id": call_id, "capability": capability,
                                  "outcome": "no_healthy_backend_served_stale_cache"}))
            return GatewayResult(call_id, "cache", "degraded", cached["text"], degraded=True)
        log.info(json.dumps({"event": "audit", "call_id": call_id, "capability": capability,
                              "outcome": "no_healthy_backend_no_cache"}))
        return GatewayResult(call_id, "none", "failed", None, degraded=True)

    try:
        validate_token_audience(bearer_token, server.canonical_uri)  # Sec 4.6, hard fail, no retry
    except TokenAudienceError as exc:
        log.info(json.dumps({"event": "audit", "call_id": call_id, "server": server.name,
                              "outcome": "token_audience_rejected", "reason": str(exc)}))
        return GatewayResult(call_id, server.name, "denied", None, degraded=True)

    idem_key = dispatch_idempotency_key(capability, server.name, args)
    breaker = get_breaker(server.name)
    try:
        raw = call_with_retry(lambda: server.fn(args), server.name, max_attempts=3)
        breaker.record_success()
        redacted_text, pii_found = redact_pii(raw["text"], request_id)
        _LAST_KNOWN_GOOD[capability] = {"text": redacted_text}
        log.info(json.dumps({"event": "audit", "call_id": call_id, "server": server.name,
                              "idempotency_key": idem_key, "outcome": "success",
                              "pii_classes": pii_found}))
        return GatewayResult(call_id, server.name, "success", redacted_text, pii_found)
    except McpError as exc:
        breaker.record_failure()
        log.info(json.dumps({"event": "audit", "call_id": call_id, "server": server.name,
                              "idempotency_key": idem_key, "outcome": "failed", "reason": str(exc)}))

        # Fallback chain: alternate server -> last-known-good cache -> degraded failure (Sec 4.3)
        alt = registry.route(capability, exclude={server.name})
        if alt is not None:
            try:
                validate_token_audience(bearer_token, alt.canonical_uri)
                alt_raw = call_with_retry(lambda: alt.fn(args), alt.name, max_attempts=2)
                get_breaker(alt.name).record_success()
                redacted_text, pii_found = redact_pii(alt_raw["text"], request_id)
                log.info(json.dumps({"event": "audit", "call_id": call_id, "server": alt.name,
                                      "outcome": "success_on_fallback_server"}))
                return GatewayResult(call_id, alt.name, "success", redacted_text, pii_found)
            except (McpError, TokenAudienceError):
                get_breaker(alt.name).record_failure()

        cached = _LAST_KNOWN_GOOD.get(capability)
        if cached is not None:
            log.info(json.dumps({"event": "audit", "call_id": call_id,
                                  "outcome": "degraded_served_stale_cache"}))
            return GatewayResult(call_id, "cache", "degraded", cached["text"], degraded=True)

        log.info(json.dumps({"event": "audit", "call_id": call_id,
                              "outcome": "degraded_no_fallback_available"}))
        return GatewayResult(call_id, "none", "degraded", None, degraded=True)


# --------------------------------------------------------------------------
# 11. Gateway entrypoint: fan out a batch of tool calls in parallel
# --------------------------------------------------------------------------

def run_gateway_batch(query: str, origin_sub: str = "user-42") -> dict:
    servers = [
        McpServerProfile("search_primary", "https://mcp.internal/search-a", "search",
                          make_backend("search_primary", 0.20)),
        McpServerProfile("search_secondary", "https://mcp.internal/search-b", "search",
                          make_backend("search_secondary", 0.15)),
        McpServerProfile("crm_lookup", "https://mcp.internal/crm", "crm",
                          make_backend("crm_lookup", 0.10)),
    ]
    registry = GatewayRegistry(servers)
    request_id = str(uuid.uuid4())
    bind_correlation_context(origin_sub, request_id)
    bearer_token = {"aud": "https://mcp.internal/search-a"}  # simulates a scoped OBO token

    log.info(json.dumps({"event": "gateway_batch_start", "query": query, "request_id": request_id}))

    calls = [
        ("c1", "search", {"query": f"{query} :: A"}, bearer_token),
        ("c2", "search", {"query": f"{query} :: B"},
         {"aud": "https://mcp.internal/search-b"}),   # scoped for search-secondary, but the
                                                        # router picks the first healthy "search"
                                                        # candidate (search_primary) -- routing and
                                                        # authorization are independent concerns, so
                                                        # this is correctly DENIED, not silently
                                                        # routed to match the token (Sec 4.6)
        ("c3", "crm", {"query": query},
         {"aud": "https://mcp.internal/search-a"}),   # WRONG audience -> will be denied (Sec 4.6)
    ]

    results: list[GatewayResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [
            pool.submit(dispatch_tool_call, registry, cap, cid, args, token, origin_sub, request_id)
            for cid, cap, args, token in calls
        ]
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())

    degraded = [r for r in results if r.degraded]
    status = "complete" if not degraded else (
        "partial_degraded" if len(degraded) < len(results) else "degraded_total"
    )
    log.info(json.dumps({"event": "gateway_batch_complete", "status": status,
                          "degraded_calls": [r.call_id for r in degraded], "request_id": request_id}))
    return {"status": status, "request_id": request_id,
            "results": [dataclasses.asdict(r) for r in results]}


# --------------------------------------------------------------------------
# 12. Example run
# --------------------------------------------------------------------------

if __name__ == "__main__":
    random.seed(7)
    output = run_gateway_batch("Q3 pipeline review for account Acme Corp")
    print(json.dumps(output, indent=2))
```

**What each pattern buys, mapped back to §2–§4.** `validate_token_audience()` is the concrete, runnable form of §4.6's RFC 8707 requirement — in the example run, the `crm` call is deliberately dispatched with a token scoped to `search-a`'s audience and is **denied before any network call happens**, exactly the cross-server token-replay defense the spec mandates. `redact_pii()` runs on every successful backend response before it is cached or returned, implementing §4.8's "redact before context injection, not after" invariant — note that the mock backend's response text deliberately embeds an email address, so a real run of this script will show a `pii_redacted` audit event. `GatewayRegistry.route()` shares one source of truth with `CircuitBreaker` — a server whose breaker is `OPEN` is never a routing candidate — which is what makes the fallback chain in `dispatch_tool_call()` (alternate server → last-known-good cache → structured degraded result) actually work: a caller never sees a hard failure because one of three backends had a bad run, they see a `partial_degraded` status with an explicit, auditable list of which calls fell back. `dispatch_idempotency_key()` exists specifically because §2.2/4.4 established that the 2026-07-28 stateless core retries a dropped call from scratch rather than resuming it — any infrastructure above this code (a message queue, a client-side retry loop) could redeliver the same call, and the content-hash key is what lets a downstream system detect and discard the duplicate. Finally, `bind_correlation_context()` solves the same easy-to-miss production bug as in multi-agent systems generally: Python's `contextvars` do not propagate into `ThreadPoolExecutor` worker threads, so every dispatch function re-binds explicitly at entry — without this, the audit log's chain-of-custody (§4.9) would silently lose its `origin_sub`/`request_id` correlation on every parallel-dispatched call.

---

## 6. Architectural System Design Scenarios

### Scenario A — Enterprise-wide internal-tool MCP rollout for an AI coding/ops assistant

**Problem statement.** A large financial-services engineering organization (modeled on Block's Goose rollout) wants to scale an internal AI agent from an engineering-only tool to **12,000 employees across 15 job functions**, giving each employee's agent governed access to Slack, calendar, the data warehouse, and 30+ internal API platforms (Square-scale, 200+ REST endpoints) — without either (a) hand-building N custom integrations per app, or (b) exposing raw API-key access that bypasses any central governance. The MCP-specific risk to design against: naively exposing 200+ endpoints as 200+ MCP tools would consume the majority of every agent's context window before a single user query is processed (§3.2), and would make the "1 tool per endpoint doesn't scale" failure Block explicitly documented.

**Proposed architecture.**

```
Employee query → Host agent (one MCP Client per connected internal server)
                                                    │
                                                    ▼
        MCP Gateway: OAuth 2.1 + enterprise SSO (Entra ID/Okta) terminates
        auth (Sec 4.6-4.7); replaces manual API-key management entirely
                                                    │
                                                    ▼
        Layered-tool pattern (Sec 3.2): Square's 200+ endpoints collapse
        into 3 conceptual tools (discover / plan / execute) instead of
        200+ individual tool schemas -- directly bounds context-window
        cost regardless of how many backend endpoints exist
                                                    │
                                                    ▼
        Tool-level RBAC per job function (Sec 4.5/4.7): a "sales" agent
        role cannot reach HR-data tools or destructive-write endpoints,
        enforced at the gateway PDP, independent of agent prompt content
                                                    │
                                                    ▼
        Dynamic context management: servers auto enable/disable per
        query (only load the 3-5 servers this specific task needs,
        Sec 3.2's ~30-40-tool ceiling), plus a context summarizer for
        long-running conversations
                                                    │
                                                    ▼
        Per-backend circuit breakers (Sec 4.3) + immutable audit log
        (Sec 4.9) covering every tool call across all 15 job functions
```

Tech choices: an OAuth 2.1 resource-server model per §4.6 replacing all standing API keys; a gateway-enforced two-axis auth model (§4.7) so interactive employees and automated batch jobs share one governed endpoint; the layered-tool pattern (§3.2) generalized beyond Square specifically to every high-endpoint-count internal platform onboarded; `ttlMs`-based tool-catalog caching (§2.5) since a 12,000-employee fleet reconnecting constantly would otherwise pay the stateless-core re-fetch cost on every session.

**Trade-off matrix:**

| Dimension | Proposed: MCP gateway + layered-tool pattern | 1-tool-per-endpoint MCP (no layering) | Native function calling per app (no MCP) |
|---|---|---|---|
| Cost / 1k runs | Bounded and roughly flat as more internal platforms are onboarded (Cloudflare's measured property, §3.2) — layering absorbs endpoint growth | Grows **linearly with endpoint count**; a 200+-endpoint platform alone can approach the 70% context-fracture point (§3.2) before any other server is even connected | Lowest per-call token cost (no schema injection at all, §3.1), but every app-integration is bespoke, non-portable code |
| Latency | Streamable HTTP + session pooling gives the ~10× throughput of shared vs. unique sessions (§3.3); layering adds one extra "discover/plan" round trip before "execute" | Comparable per-call latency to the proposed design, but the token overhead itself increases model *reasoning* latency at longer contexts | Fastest per call — single LLM turn, local exec, no protocol/schema layer at all |
| Ops complexity | Moderate-high — requires gateway operation, OAuth/SSO integration, RBAC policy per job function (§4.7), but this is a **one-time** governance investment amortized across all 15 job functions | High — the tool-explosion problem forces ongoing per-endpoint schema maintenance and re-tuning as APIs change, with no natural place to add cross-cutting policy | Highest **long-term** — N apps × M integrations, each independently authenticated, each independently RBAC'd, with no shared audit trail across apps |
| Security | Strong — one enforced OAuth/RBAC/audit boundary for every connected internal platform (§4.5-4.9); a single gateway policy change propagates everywhere | Same theoretical security ceiling as the proposed design, but the practical reality of maintaining RBAC across 200+ individually-schemad tools materially increases the odds of a missed scope | Weakest at fleet scale — each app's own ad hoc auth means no single place enforces "sales agents cannot reach HR data" across the whole organization |
| Scalability | Scales to arbitrarily many internal platforms — Block's documented outcome (100+ pre-approved internal MCP servers, ~60 in week one to 100+ by week eight) validates this at real enterprise scale | Does not scale past a handful of high-endpoint-count platforms before hitting the context ceiling described in §3.2 | Scales in raw call-volume terms but not in **governance** terms — adding the 16th app means building the 16th bespoke integration and RBAC surface from scratch |

**Decision rationale.** The layered-tool-pattern MCP gateway is selected because it is the only approach shown, at Block's documented 12,000-employee/8-week scale, to solve both problems simultaneously: bounding the tool-schema token tax regardless of backend endpoint count, and centralizing OAuth/RBAC/audit so a "sales agent cannot see HR data" rule is enforced once, at the gateway, rather than 15 times, once per job function's bespoke integration. The 1-tool-per-endpoint alternative is rejected not on security grounds — its security *ceiling* is comparable — but because it fails the token-economics test at exactly this scale (§3.2), turning every broad internal platform into a context-window liability before the agent processes any actual user content. Native function calling per app is rejected specifically at fleet scale: its per-call cost and latency advantages (§3.1) are real but do not compound favorably when multiplied across 15 job functions each needing independently-built, independently-secured integrations — the governance cost dominates the token-cost savings at this organizational size, which is precisely the trade-off §3.1's OpenAPI/native-function-calling/MCP comparison predicts favors MCP for "cross-runtime portability, shared tool infra across many agents."

### Scenario B — Regulated multi-tenant MCP portal for external partner/agent access

**Problem statement.** A healthcare-adjacent SaaS platform (modeled on the Cloudflare enterprise reference architecture) needs to expose internal servers — customer records, scheduling, billing — to both internal agents and external partner agents, under HIPAA/SOC 2 obligations, with **default-deny write access**, PII that must never leave the trust boundary unredacted, and a requirement that unsanctioned ("shadow") MCP connections be detected, not merely disallowed by policy document. The specific MCP-scale risk: as more internal servers are added behind the portal over time, naive native-MCP tool exposure grows the token tax linearly (§3.2's measured 52-tool/4-server/9,400-token baseline), directly working against the goal of onboarding more internal systems over time without degrading every connected agent's effective context budget.

**Proposed architecture.**

```
External/internal agent → Portal boundary (default-deny writes, Sec 4.7)
                                                    │
                                                    ▼
        Code Mode portal (Sec 3.2): 4 internal servers / 52 tools /
        ~9,400 tokens of schema collapse into 2 portal tools / ~600
        tokens (94% reduction) -- and critically, token cost stays FLAT
        as more internal servers are added behind the portal over time
                                                    │
                                                    ▼
        Zero-Trust enforcement at the portal (Sec 4.5-4.6): OAuth 2.1
        resource-server validation, SPIFFE-issued workload identity for
        each internal server process, OBO scope narrowing per external
        partner's actual grant -- never the portal's own standing power
                                                    │
                                                    ▼
        PII detect->redact->audit (Sec 4.8) applied to every record
        BEFORE it crosses the portal boundary outward to any agent,
        internal or external -- Zero Data Retention: transforms happen
        in memory only, so the portal itself never becomes a sub-
        processor under HIPAA/SOC 2 (Sec 3.5/4.8)
                                                    │
                                                    ▼
        Stateless Workers for the compute/routing layer (Sec 4.2's
        "stateless by default, stateful by exception"), Durable Objects
        only where genuine session coordination is required
                                                    │
                                                    ▼
        Shadow-MCP detection via network-layer visibility (Sec 4.11):
        any connection attempt to an internal server NOT proxied
        through the portal is flagged, not just disallowed by policy
```

Tech choices: Code Mode-style portal tools (§3.2) instead of native per-server tool exposure, specifically because the flat-token-cost-as-servers-grow property directly serves a platform that expects to keep onboarding internal systems; SPIFFE/SPIRE workload identity per internal server (§4.5) combined with per-partner OAuth grants (§4.6) for the dual-verification Zero-Trust pattern; a gVisor- or Firecracker-tier sandbox (§4.10) per external-partner session given the lower trust level of external callers vs. internal employees in Scenario A; network-layer shadow-MCP detection (§4.11) as a structural backstop to the RBAC policy layer, since policy alone cannot catch a connection that was never routed through the policy engine at all.

**Trade-off matrix:**

| Dimension | Proposed: Code Mode portal + Zero-Trust + shadow detection | Native per-server MCP exposure (no portal abstraction) | OpenAPI gateway (REST contracts, no MCP layer) |
|---|---|---|---|
| Cost / 1k runs | Flat as internal servers are added (measured: 94% reduction on the 52-tool baseline, §3.2) — the portal absorbs growth instead of passing it to every connected agent | Grows linearly with each new internal server onboarded — the exact 1.17M-token pathology Cloudflare's own pre-Code-Mode deployment hit (§3.2) | Lowest raw token cost (no MCP schema layer at all), but requires each partner integration to hand-write and maintain its own REST client code — a different cost category, not a lower one at scale |
| Latency | Code Mode's sandboxed execution surface adds a small fixed overhead vs. a raw tool call, offset by never paying the multi-tool-schema parsing cost native exposure would impose at scale | Comparable per-call latency to the proposed design at low server counts; degrades as more servers are added and each call competes with a larger injected context (§3.2's fracture-point effect) | Fastest per call — REST + gateway retries/caching only, no agent-specific tool-schema abstraction layer |
| Ops complexity | Moderate — requires building/maintaining the Code Mode portal abstraction itself, but this is a one-time investment that then absorbs all future server onboarding | Low initially, but ops burden **grows with every new server** as tool counts and context-budget conflicts compound (§3.2) | Moderate — mature REST/OpenAPI tooling (security schemes, API gateways) but no native agent-facing dynamic tool discovery (`tools/list`, `listChanged`) |
| Security | Strong — default-deny writes, PII redaction before the portal boundary, Zero Data Retention keeping the platform out of sub-processor scope, shadow-MCP network-layer detection as a structural backstop (§4.5-4.8/4.11) | Comparable security *ceiling* to the proposed design if implemented equally rigorously per-server, but 52 individually-secured tool surfaces are harder to audit uniformly than 2 portal tools with one enforcement point | Mature but generic — OpenAPI security schemes are not agent-context-aware (no notion of "this response contains PII that must be redacted before an LLM sees it"), requiring a bolt-on layer regardless |
| Scalability | Scales cleanly as internal servers are added — the flat-token-cost property is the direct enabler, matching Cloudflare's own production reference architecture | Does not scale past a handful of internal servers before the token tax makes every connected agent's context budget the binding constraint | Scales in API-surface terms (REST scales well), but does not natively solve agent-specific dynamic tool discovery or portability across multiple agent hosts (§3.1's function-calling/OpenAPI/MCP comparison) |
| Compliance | Strong — Zero Data Retention keeps the portal a pure pass-through, avoiding sub-processor status under HIPAA/SOC 2; redaction-before-crossing-boundary satisfies both internal-employee and external-partner data-handling obligations identically (§3.5/4.8) | Same theoretical compliance ceiling, but redaction/audit must be replicated correctly across every one of 52+ tool surfaces rather than enforced once at a single boundary | Requires a separate compliance layer bolted onto REST responses; OpenAPI itself has no PII-awareness primitive |

**Decision rationale.** The Code Mode portal is selected specifically because its **flat-token-cost-as-servers-grow** property (measured, not projected) directly solves the platform's stated future-onboarding requirement in a way neither alternative does: native per-server exposure repeats Cloudflare's own documented 1.17M-token failure mode as more servers are added, and an OpenAPI gateway alone has no agent-facing dynamic-discovery or context-awareness primitive at all, pushing PII-redaction and tool-selection logic into ad hoc bolt-ons per integration. The Zero-Trust layer (SPIFFE workload identity + per-partner OAuth + PII redaction before the portal boundary) is non-negotiable given the regulated data class in play — the source material is explicit that policy-document-only controls (RBAC as an org chart rather than an enforced PDP decision) have already failed in comparable real incidents (§4.11's CVE and TPA record). Shadow-MCP network-layer detection is retained as a structural backstop precisely because RBAC policy can only govern connections that are actually routed through it — a connection that bypasses the portal entirely is invisible to any policy engine, which is why detection must happen at the network layer, independent of the policy layer it's meant to backstop.

---

> ⚠️ Data gaps carried over from the primary source, stated explicitly rather than silently smoothed over: no vendor publishes a composed availability SLA spanning client→gateway→server→backend as a single unit (§3.5), so every figure beyond the topology-level statelessness claim is an inferred design target; no purpose-built "MCP gateway throughput" benchmark spans the full admission-control→routing→backend chain as one number (§3.4); the exact Tasks-primitive polling state machine is confirmed to exist and be non-experimental as of 2026-07-28 but not enumerated in detail in the source material (§2.6); no vendor-neutral benchmark quantifies MTBF/error-budget improvement from MCP-specific resilience patterns as opposed to microservices generally (§4.3); and the 2026-07-28 stateless redesign is recent enough that ecosystem-wide migration/compatibility coverage (§2.2) has no published adoption-rate figure.
