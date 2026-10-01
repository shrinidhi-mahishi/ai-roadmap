# 04. How MCP (Model Context Protocol) Works

**Sub-areas covered**: Host/Client/Server architecture with 1:1 client-server invariant, the three primitives (Tools/Resources/Prompts) plus reverse-direction Sampling/Elicitation, transport mechanics (stdio, Streamable HTTP, deprecated HTTP+SSE) with latency/throughput benchmarks, JSON-RPC 2.0 wire format and error codes, capability negotiation lifecycle through the 2025-11-25 and 2026-07-28 spec transitions (stateful to stateless), the token tax problem (2x-30x overhead, $0.04-$0.15/conversation), optimization strategies (context trimming, Code Mode, tiered schema detail, cacheable list results), connection lifecycle management (stateful sessions vs. self-describing stateless requests), Streamable HTTP resumability via SSE event IDs, MCP gateway/proxy patterns (42 gateways on the market), OAuth 2.1 with mandatory PKCE, Zero-Trust transport security, tool-level RBAC, human-in-the-loop mandates, audit/governance (W3C Trace Context, NSA/DoD guidance, OWASP MCP Top 10), threat landscape (tool poisoning, confused deputy, supply-chain attacks, prompt injection via tool results), production failure taxonomy (833 fault threads across 473 repos), five repeatable failure modes with fixes, enterprise topologies (single-tenant, multi-tenant row-isolated, federated gateway, edge-cached read-only), production Python code with retries/circuit-breakers/PII-redaction/structured-logging, and two enterprise system-design scenarios with trade-off matrices

---

## 1. System Topology & Data Flow

A production MCP deployment spans five cooperating planes: a **control plane** handling identity, authorization, and policy before any tool executes; a **data plane** (the MCP gateway) routing every live tool call through circuit breakers and PII filters; a set of **tool proxies** wrapping each backend MCP server in isolation and sandbox enforcement; a **persistence layer** surviving process restarts independently of the protocol's stateless core; and a **telemetry layer** making every invocation auditable after the fact.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              CONTROL PLANE                                   │
│                                                                              │
│  ┌───────────────────┐   ┌──────────────────┐   ┌────────────────────────┐  │
│  │ Host               │   │ Registry /        │   │ OAuth 2.1 AuthZ Server │  │
│  │ (Claude Desktop,   │──▶│ Namespace         │──▶│ (mints tokens; MCP     │  │
│  │  Cursor, custom    │   │ (verified server  │   │  server validates,     │  │
│  │  agent runtime)    │   │  names; does NOT  │   │  never issues)         │  │
│  │                    │   │  code-scan)       │   │                        │  │
│  │ Spawns one Client  │   └────────┬─────────┘   └───────────┬────────────┘  │
│  │ per Server (1:1)   │            │ discovery                │ token issue   │
│  └────────┬──────────┘            ▼                          │              │
│           │ tools/call    ┌──────────────────┐               │              │
│           │ resources/read│ Policy Engine     │◀──────────────┘              │
│           │ prompts/get   │ (OPA/Cedar PDP:   │                              │
│           │               │  tool+argument    │                              │
│           │               │  RBAC per call)   │                              │
│           │               └────────┬─────────┘                              │
└───────────┼────────────────────────┼────────────────────────────────────────┘
            │ authorized request     │ scope-narrowed (RFC 8693 OBO)
┌───────────▼────────────────────────▼────────────────────────────────────────┐
│                     DATA PLANE  (MCP GATEWAY)                                │
│                                                                              │
│  ┌────────────┐  ┌──────────────┐  ┌─────────────┐  ┌────────────────────┐  │
│  │ Mcp-Method  │  │ Per-Backend   │  │ PII Detect  │  │ Tool-Catalog Cache │  │
│  │ /Mcp-Name   │  │ Circuit       │  │ -> Redact   │  │ (ttlMs/cacheScope, │  │
│  │ Header      │──▶│ Breaker      │──▶│ -> Audit    │──▶│  deterministic     │  │
│  │ Router      │  │ (CLOSED ->   │  │ (before     │  │  ordering for      │  │
│  │ (no JSON-   │  │  OPEN ->     │  │  response   │  │  stable prompt     │  │
│  │  RPC body   │  │  HALF-OPEN)  │  │  enters     │  │  caches)           │  │
│  │  parse)     │  │              │  │  model ctx) │  │                    │  │
│  └──────┬─────┘  └──────┬──────┘  └──────┬──────┘  └────────┬───────────┘  │
└─────────┼───────────────┼────────────────┼────────────────────┼──────────────┘
          │ stdio         │ Streamable HTTP │ Streamable HTTP    │
          │ (local IPC,   │ (shared session │ (SSE upgrade,      │
          │  ~0ms)        │  ~10ms/call,    │  resumable via     │
          │               │  ~300 RPS)      │  Last-Event-ID)    │
┌─────────▼───────────────▼────────────────▼────────────────────▼──────────────┐
│                        TOOL PROXIES  (per MCP server)                         │
│                                                                               │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐    │
│  │ Sandbox Tier      │  │ Server A: exposes │  │ Server B: different      │    │
│  │ (OS-level <10ms / │  │ Tools/Resources/  │  │ vendor, different        │    │
│  │  gVisor ~500ms /  │  │ Prompts; may      │  │ sandbox tier, isolated   │    │
│  │  Firecracker      │  │ issue reverse     │  │ blast radius from        │    │
│  │  ~125ms startup)  │  │ Sampling calls    │  │ Server A                 │    │
│  └──────────────────┘  └────────┬─────────┘  └──────────┬───────────────┘    │
└──────────────────────────────────┼─────────────────────────┼─────────────────┘
                                   │ backend I/O              │ backend I/O
┌──────────────────────────────────▼─────────────────────────▼─────────────────┐
│                           PERSISTENCE LAYER                                   │
│                                                                               │
│  ┌──────────────────┐  ┌───────────────────┐  ┌──────────┐  ┌─────────────┐ │
│  │ Externalized      │  │ Durable Workflow   │  │ Token    │  │ Immutable   │ │
│  │ EventStore        │  │ Store (Temporal /  │  │ Vault    │  │ Audit Log   │ │
│  │ (Redis; SDK       │  │ Dapr — redelivers │  │ (short-  │  │ (hash-      │ │
│  │  default is in-   │  │ pending activities │  │  lived,  │  │  chained,   │ │
│  │  memory, 404s     │  │ on restart)        │  │  scoped) │  │  tool-call  │ │
│  │  on restart)      │  │                    │  │          │  │  level)     │ │
│  └──────────────────┘  └───────────────────┘  └──────────┘  └─────────────┘ │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼───────────────────────────────────────────┐
│                    TELEMETRY / OBSERVABILITY SINKS                            │
│  Per-(client,server) P50/P95/P99 round-trip latency  |  circuit-breaker     │
│  state dashboard  |  token-tax meter per server  |  Shadow-MCP alerts  |    │
│  CVE/dependency-risk feed  |  chain-of-custody audit trail  |               │
│  W3C Trace Context (traceparent, tracestate, baggage in _meta)              │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A Host application maintains one Client per Server connection -- this 1:1 pairing is the foundational isolation invariant. (2) Under the 2026-07-28 spec, every request (`tools/call`, `resources/read`, `prompts/get`) is self-describing: protocol version, client identity, and capabilities travel in `_meta` within the JSON-RPC body. No `initialize`/`initialized` handshake is required. (3) The request crosses the control plane: the registry confirms the target server's namespace-verified identity, the policy engine evaluates tool-and-argument-level RBAC for this specific call (never just at connect time), and an On-Behalf-Of token exchange (RFC 8693) narrows the caller's scope to exactly what this hop needs. (4) The authorized request enters the MCP gateway data plane, where `Mcp-Method`/`Mcp-Name` HTTP headers let the router dispatch, authorize, and rate-limit without parsing the JSON-RPC body -- a deliberate performance optimization. (5) The gateway checks a per-backend circuit breaker (not per-tool, since multiple tools share one backend API), then applies PII detect-redact-audit to the tool response before it reaches the model's context window. (6) The call is framed over stdio (local subprocess, zero network overhead) or Streamable HTTP (networked, horizontally scaled, OAuth-fronted). (7) Tool-schema catalogs are cached client-side using `ttlMs`/`cacheScope` fields, mitigating re-fetch cost the stateless redesign would otherwise impose. (8) If the server needs mid-stream resumability (SSE `Last-Event-ID` replay), the event log must be externalized to a shared durable store -- the SDK-default in-memory EventStore 404s on restart. (9) Regardless of outcome, the gateway writes an immutable, tool-call-level audit record before considering the request complete.

---

## 2. Core Mechanics & Algorithms

### 2.1 Roles and the Three Primitives

MCP is a JSON-RPC 2.0 protocol connecting three roles:

- **Host**: LLM application (Claude Desktop, IDE, custom agent). Spawns one Client per Server. Is the trust boundary -- must obtain user consent before exposing data or invoking tools.
- **Client**: Protocol-speaking connection manager living inside the host. Maintains a dedicated 1:1 connection with a single Server. Handles capability negotiation, session lifecycle, reconnections.
- **Server**: Bridges MCP protocol to real-world systems (databases, SaaS APIs, file systems). Translates `resources/read` into SQL `SELECT`, etc. Can run locally (subprocess) or remotely (cloud service).

```
┌────────────────────────────────────────────────────────────────┐
│ Primitive   │ Direction       │ Discovery        │ Controller  │
├─────────────┼─────────────────┼──────────────────┼─────────────┤
│ Tools       │ Server->Client  │ tools/list       │ Model       │
│ Resources   │ Server->Client  │ resources/list   │ Application │
│ Prompts     │ Server->Client  │ prompts/list     │ User        │
├─────────────┼─────────────────┼──────────────────┼─────────────┤
│ Sampling*   │ Client<-Server  │ sampling/create  │ Server      │
│ Elicitation │ Client<-Server  │ elicitation/req  │ Server      │
└────────────────────────────────────────────────────────────────┘
  * Sampling deprecated in 2026-07-28, replaced by MRTR pattern
```

**Key invariant**: Clients and servers have a 1:1 relationship. A single host runs N clients, each connected to one server. This enables clear security boundaries and isolation between integrations. Tool inputs (and optionally outputs) are validated against JSON Schema 2020-12 -- this makes tool calls machine-checkable independent of model behavior.

### 2.2 Transport Layer State Machine

MCP defines two standard transports. The wire format is always JSON-RPC 2.0, UTF-8 encoded. The protocol is transport-agnostic -- custom transports are permitted if they preserve JSON-RPC message format and lifecycle requirements.

**stdio transport:**
```
┌───────────┐   spawn    ┌───────────┐
│   Client   │──────────▶│  Server   │
│            │  stdin     │ (subprocess)
│            │◀──────────│           │
│            │  stdout    │ stderr->log
└───────────┘            └───────────┘

- Newline-delimited JSON-RPC (embedded newlines forbidden)
- Network overhead: ~0ms (pure IPC)
- One client per process; no built-in auth (OS process isolation)
- Cold start: TypeScript ~80ms faster than Python
- No built-in reconnection; subprocess death = client must relaunch
```

**Streamable HTTP transport** (spec 2025-03-26, replaces deprecated HTTP+SSE):
```
                                 ┌──────────────────────────┐
                                 │  https://example.com/mcp  │
                                 └──────────┬───────────────┘
                                            │
              ┌─────────────────────────────┼─────────────────────┐
              │                             │                     │
        ┌─────▼─────┐              ┌───────▼───────┐      ┌──────▼──────┐
        │   POST     │              │    GET         │      │   DELETE    │
        │ JSON-RPC   │              │ SSE stream     │      │ Terminate   │
        │ req/notif  │              │ (server-init)  │      │ session     │
        └─────┬─────┘              └───────────────┘      └─────────────┘
              │
     ┌────────┴────────┐
     │                 │
┌────▼────┐     ┌──────▼──────┐
│ JSON    │     │ SSE stream  │
│ response│     │ (streaming) │
│ (short) │     │ (long ops)  │
└─────────┘     └─────────────┘

Session: Mcp-Session-Id header (pre-2026-07-28)
         Self-describing _meta (2026-07-28+)
Resumability: SSE event IDs + Last-Event-ID on reconnect
Performance: ~10ms/call, ~290-300 RPS sustained, 100% success rate
```

**Deprecated HTTP+SSE transport comparison:**
```
┌──────────────────┬──────────────────────┬──────────────────────┐
│ Metric            │ Streamable HTTP       │ HTTP+SSE (deprecated) │
├──────────────────┼──────────────────────┼──────────────────────┤
│ Endpoints         │ Single (/mcp)         │ Two (GET SSE + POST)  │
│ Throughput         │ ~290-300 RPS          │ 7-30 RPS              │
│ Resumability       │ Last-Event-ID replay  │ None                  │
│ Infrastructure     │ Stateless-compatible  │ Long-lived per-client │
│ Latency            │ ~10ms/call            │ 100s of ms under load │
└──────────────────┴──────────────────────┴──────────────────────┘
```

### 2.3 Capability Negotiation State Machine

**Pre-2026-07-28 (stateful):**
```
                  Client                         Server
                    │                               │
                    │──── initialize ───────────────▶│
                    │     (client capabilities,      │
                    │      protocol version)         │
                    │                               │
                    │◀─── InitializeResult ─────────│
                    │     (server capabilities,      │
                    │      agreed protocol version,  │
                    │      Mcp-Session-Id header)    │
                    │                               │
                    │──── notifications/initialized ▶│
                    │     (fire-and-forget)          │
                    │                               │
                    │◀──▶ ACTIVE SESSION ◀──────────▶│
                    │     (bidirectional requests,    │
                    │      notifications)            │
                    │                               │
                    │──── HTTP DELETE ──────────────▶│
                    │     (terminate session)        │
                    │                               │

  Server capabilities: tools, resources, prompts (each with listChanged)
  Client capabilities: sampling, roots, elicitation
```

**2026-07-28 (stateless):**
```
                  Client                         Server
                    │                               │
                    │──── Any RPC (tools/call, etc) ▶│
                    │     _meta: {                   │
                    │       protocolVersion,          │
                    │       clientId,                │
                    │       capabilities              │
                    │     }                          │
                    │                               │
                    │◀─── Result ───────────────────│
                    │                               │

  No handshake. No session ID. Any server instance behind
  a round-robin load balancer can handle any request.
  Optional: server/discover RPC for capability info.
  Cross-call state: server mints handle, model passes it back.
```

### 2.4 JSON-RPC 2.0 Message Types and Error Codes

Three message types:
1. **Requests**: Include `jsonrpc`, `id`, `method`, optional `params`. Expect a response.
2. **Responses**: Match request via `id`. Contain `result` or `error`.
3. **Notifications**: Fire-and-forget, no `id`, no response expected.

```
┌──────────────┬────────┬──────────────────────────────────────────┐
│ Error Code    │ Source  │ Meaning                                  │
├──────────────┼────────┼──────────────────────────────────────────┤
│ -32602        │ JSON-RPC│ Invalid params (unknown tool, missing arg)│
│ -32603        │ JSON-RPC│ Internal error                            │
│ -32002        │ MCP     │ Resource not found                        │
│ -1            │ MCP     │ User rejected request (sampling)          │
├──────────────┼────────┼──────────────────────────────────────────┤
│ isError: true │ Tool    │ Tool execution error (actionable by LLM)  │
└──────────────┴────────┴──────────────────────────────────────────┘
```

**Critical distinction**: Protocol errors (JSON-RPC `error` field) indicate malformed requests or server failures. Tool execution errors (`isError: true` in `result`) indicate the tool ran but failed -- the LLM can read these and self-correct.

### 2.5 Tool Annotations and Trust Model

Tool annotations provide metadata but are untrusted unless from a verified server:

```
┌───────────────────┬────────────────────────────────────────────┐
│ Annotation         │ Semantics                                  │
├───────────────────┼────────────────────────────────────────────┤
│ readOnlyHint       │ Tool does not modify state                 │
│ destructiveHint    │ Tool may irreversibly modify state         │
│ idempotentHint     │ Safe to retry without side effects         │
│ openWorldHint      │ Tool interacts with external/open systems  │
└───────────────────┴────────────────────────────────────────────┘
```

These annotations inform host-level UX decisions (e.g., skip confirmation for read-only tools from trusted servers) but must never be trusted blindly -- they are self-reported by the server.

### 2.6 Dynamic Discovery and Subscription

```
  Server tool set changes
           │
           ▼
  notifications/tools/list_changed ──────▶ Client
                                            │
                                            ▼
                                     Client re-fetches
                                     tools/list (paginated)
                                            │
                                            ▼
                                     Updates internal
                                     tool registry
                                            │
                                            ▼
                                     Host re-injects
                                     updated schemas
                                     into LLM context

  Resource subscriptions:
  Client ──── resources/subscribe(uri) ────▶ Server
  Server ──── notifications/resources/updated ──▶ Client (on change)
```

### 2.7 Spec Evolution Timeline

```
┌────────────┬─────────────┬──────────────────────────────────────────────────┐
│ Date        │ Version      │ Key Change                                       │
├────────────┼─────────────┼──────────────────────────────────────────────────┤
│ Nov 2024    │ 2024-11-05   │ Initial release. HTTP+SSE transport.             │
│ Mar 2025    │ 2025-03-26   │ Streamable HTTP replaces SSE. stdio formalized. │
│ Nov 2025    │ 2025-11-25   │ OAuth 2.1. Tasks. Elicitation. Structured       │
│             │              │ output. Audio. Tool annotations.                │
│ Jul 2026    │ 2026-07-28   │ STATELESS CORE. Sessions removed. MRTR replaces │
│             │              │ Sampling/Elicitation/Roots. Header-based routing │
│             │              │ (Mcp-Method/Mcp-Name). Cacheable lists.         │
│             │              │ Extensions framework. CIMD replaces DCR.        │
└────────────┴─────────────┴──────────────────────────────────────────────────┘
```

---

## 3. Token Economics & NFR Analysis

### 3.1 The Token Tax

The most significant production cost of MCP: tool schemas are injected into the LLM's context window, consuming tokens before any user interaction.

**Measured overhead from production data:**
```
┌────────────────────────────────┬──────────────────────────────────┐
│ Metric                          │ Value                             │
├────────────────────────────────┼──────────────────────────────────┤
│ Token inflation vs. baseline    │ 2x - 30x (across 9 LLMs)         │
│ Simple directory listing        │ 12x more tokens than hard-coded   │
│ MCP tool schema vs. minimal     │ 5-15x more tokens per tool        │
│ Per-tool context consumption    │ ~1,000 tokens/session             │
│ 7 MCP servers                   │ 67,300 tokens (33.7% of 200k ctx) │
│ Full MCP setup (many servers)   │ 143k of 200k tokens (72% usage)   │
│ 20-30 registered tools          │ 15-30 KB of context (schemas only)│
│ Reasoning capacity cost (20 tools)│ ~10,000 fewer tokens for reasoning│
└────────────────────────────────┴──────────────────────────────────┘
```

### 3.2 Cost Formula: $ per 1k Conversations

```
Variables:
  S = number of tool schemas injected (e.g., 20)
  T_s = avg tokens per schema (~1,000)
  P_input = input token price ($/1M tokens)
  C_hit = prompt cache hit rate (0.0 - 1.0)
  P_cached = cached input price (typically 0.1x of P_input)
  R = avg tool calls per conversation
  T_r = avg tokens per tool result

Per-conversation schema cost (first turn):
  cost_schema = S * T_s * ((1 - C_hit) * P_input + C_hit * P_cached) / 1,000,000

Per-conversation tool-result cost:
  cost_results = R * T_r * P_input / 1,000,000

Total cost per conversation:
  cost_conv = cost_schema + cost_results

Cost per 1k conversations:
  cost_1k = cost_conv * 1,000
```

**Worked example** (Claude Opus, 20 tools, 75% cache hit):
```
  S = 20, T_s = 1,000, P_input = $15/1M, C_hit = 0.75, P_cached = $1.50/1M

  cost_schema = 20 * 1,000 * ((0.25 * 15) + (0.75 * 1.50)) / 1,000,000
              = 20,000 * (3.75 + 1.125) / 1,000,000
              = 20,000 * 4.875 / 1,000,000
              = $0.0975 per conversation (~$0.10)

  Without cache (C_hit = 0):
  cost_schema = 20,000 * 15 / 1,000,000 = $0.30 per conversation

  Per 1k conversations: $97.50 (cached) vs. $300 (uncached)
```

**Production validation** (22 days, 2,600 conversations):
```
  First-turn schema cost: ~$390 without cache, ~$100 with 75% cache hit
  Per conversation: ~$0.15 uncached, ~$0.04 cached
```

### 3.3 Latency SLA Targets

```
┌─────────────────────────────┬──────────┬──────────┬──────────┐
│ Component                    │ P50       │ P95       │ P99       │
├─────────────────────────────┼──────────┼──────────┼──────────┤
│ stdio round-trip             │ <1ms      │ <2ms      │ <5ms      │
│ Streamable HTTP round-trip   │ ~10ms     │ ~25ms     │ ~50ms     │
│ Gateway overhead (Bifrost)   │ 11us      │ 15us      │ 20us      │
│ Per tool call (production)   │ 50ms      │ 150ms     │ 200ms     │
│ 10-tool agentic workflow     │ 0.5s      │ 1.5s      │ 2.0s      │
│ Full task (5-15 tool calls)  │ 1.5s      │ 3.5s      │ 4.5s      │
├─────────────────────────────┼──────────┴──────────┴──────────┤
│ RECOMMENDED SLA TARGET       │ P99 < 5s per agentic task      │
│                              │ P99 < 300ms per tool call      │
│                              │ Circuit breaker open < 30s     │
└─────────────────────────────┴────────────────────────────────┘
```

### 3.4 Throughput Capacity Planning

```
┌──────────────────────────┬──────────────────────────────────┐
│ Transport                 │ Sustained throughput              │
├──────────────────────────┼──────────────────────────────────┤
│ Streamable HTTP (shared)  │ ~290-300 RPS at 100% success     │
│ HTTP+SSE (deprecated)     │ 7-30 RPS (collapses under load)  │
│ stdio                     │ Bound by subprocess throughput   │
│ Gateway (Bifrost, 5k RPS) │ 11us overhead per request        │
└──────────────────────────┴──────────────────────────────────┘

Capacity formula:
  max_concurrent_agents = gateway_rps / (avg_tools_per_task * tasks_per_second)

  Example: 300 RPS gateway, 10 tools/task, 1 task/agent/s
  max_concurrent_agents = 300 / (10 * 1) = 30 concurrent agents
```

### 3.5 MCP vs. Direct API: When NOT to Use MCP

```
┌──────────────────┬────────────────────────┬────────────────────────┐
│ Dimension         │ MCP                     │ Direct API/CLI          │
├──────────────────┼────────────────────────┼────────────────────────┤
│ Discovery         │ Dynamic at runtime      │ Static, hard-coded      │
│ Token cost        │ 2-30x overhead          │ Minimal (hand-picked)   │
│ Latency           │ +50-200ms/call          │ Direct HTTP, no framing │
│ Interoperability  │ Write once, any host    │ Bespoke per integration │
│ Security          │ Standardized OAuth 2.1  │ Per-app, inconsistent   │
│ Maintenance       │ Protocol handles        │ Every integration       │
│                   │ versioning              │ reinvents this          │
│ Scalability       │ Gateway centralized     │ Scaled independently    │
├──────────────────┼────────────────────────┼────────────────────────┤
│ BEST FOR          │ Dynamic tool sets,      │ Known fixed tools,      │
│                   │ multi-agent, platform   │ latency-critical,       │
│                   │ teams, audit needs      │ simple architectures    │
└──────────────────┴────────────────────────┴────────────────────────┘

Benchmark: CLI achieved 33% better token efficiency and 77 vs. 60 task
completion score compared to MCP for browser automation tasks.
```

**Decision heuristic**: Use MCP when tool sets are dynamic, multiple AI hosts need the same integrations, or governance/audit requirements exist. Use direct APIs for known, fixed tool sets where latency and token efficiency matter.

### 3.6 Optimization Strategies

```
┌──────────────────────────────────────┬───────────┬─────────────────────┐
│ Strategy                              │ Savings    │ Mechanism            │
├──────────────────────────────────────┼───────────┼─────────────────────┤
│ Context trimming + KV-cache +         │ Up to 91%  │ Aggressive pruning   │
│ parallel exec + progressive disclosure│            │ of schema metadata   │
│                                       │            │                      │
│ Bifrost Code Mode                     │ 92.8%      │ 3-4x fewer LLM      │
│                                       │ input      │ round trips at 500+  │
│                                       │ tokens     │ tools; 40% faster    │
│                                       │            │                      │
│ Tiered schema detail (proposed)       │ ~60%       │ Discovery tier: name │
│                                       │            │ + one-liner only;    │
│                                       │            │ full schema on-demand│
│                                       │            │                      │
│ Just-in-time MCP discovery            │ ~60%       │ Fetch only schemas   │
│                                       │            │ relevant to task     │
│                                       │            │                      │
│ Cacheable list results (2026-07-28)   │ Variable   │ ttlMs + cacheScope   │
│                                       │            │ + deterministic order│
│                                       │            │ for prompt cache     │
└──────────────────────────────────────┴───────────┴─────────────────────┘
```

### 3.7 NFR Targets

```
┌──────────────────┬──────────────────────────────────────────────┐
│ NFR               │ Target                                       │
├──────────────────┼──────────────────────────────────────────────┤
│ Availability      │ 99.9% for gateway; 99.5% per MCP server     │
│ RPO               │ 0 (immutable audit log, no data loss)        │
│ RTO               │ <60s (gateway failover); <5s (circuit open)  │
│ Compliance        │ NSA/DoD MCP Security Design guidance         │
│                   │ OWASP MCP Top 10                             │
│                   │ CSA Agentic MCP Security Best Practices      │
│ Audit             │ Tool-call-level: who, what tool, params,     │
│                   │ policy decision, W3C Trace Context            │
│ Token budget      │ <20% of context window for tool schemas      │
│ Session recovery  │ Externalized EventStore (not in-memory SDK   │
│                   │ default) with Last-Event-ID replay            │
└──────────────────┴──────────────────────────────────────────────┘
```

---

## 4. Distributed Resilience & Security

### 4.1 Failure Taxonomy

Academic research analyzed 833 confirmed runtime fault threads from 473 actively maintained MCP server repositories, producing 11 top-level categories, 27 subcategories, and 73 leaf fault types. In a survey of 55 MCP server developers, respondents experienced an average of 20 of 27 fault subcategories.

**Five repeatable production failure modes with fixes:**

```
┌────┬────────────────────────────┬───────────────────────────────────┐
│ #   │ Failure Mode                │ Fix                                │
├────┼────────────────────────────┼───────────────────────────────────┤
│ 1   │ Transport flakiness under   │ Externalize session state; health  │
│    │ load: LBs terminate at 60s, │ checks must validate active SSE    │
│    │ agent sessions silently break│ streams, not just HTTP 200         │
│    │                              │                                    │
│ 2   │ Tool description drift:     │ notifications/tools/list_changed   │
│    │ schemas change server-side,  │ + client-side schema version       │
│    │ silent argument mismatches   │ tracking + validation on every call│
│    │                              │                                    │
│ 3   │ Schema mismatches across    │ Pin SDK versions in lockfile;      │
│    │ SDK versions: subtle JSON    │ contract tests between client      │
│    │ Schema interpretation diffs  │ and server SDK versions            │
│    │                              │                                    │
│ 4   │ OAuth refresh storms:       │ Jittered refresh at 80% of token   │
│    │ hundreds of sessions hit     │ lifetime with 10% random offset +  │
│    │ token expiry simultaneously  │ single-flight lock on refresh      │
│    │                              │                                    │
│ 5   │ Silent JSON-RPC hangs:      │ Per-call timeout on every JSON-RPC │
│    │ server crashes mid-handler,  │ request ID + circuit breaker per   │
│    │ no response, client blocks   │ tool + child-process supervisor    │
└────┴────────────────────────────┴───────────────────────────────────┘
```

**Cascading failure pattern**: LLM agents retry with slightly different parameters (unlike traditional APIs that fail fast), creating cascading failures that appear as success until downstream data is checked. Each retry hits the MCP server, which propagates to the already-struggling tool provider.

**Fix**: Per-session retry budgets with proper backoff. After N failures of the same capability within a time window, circuit-break and surface failure to the orchestration layer. Never implement retry logic in the model prompt.

### 4.2 Durable Execution Patterns

```
  MCP Tool Call (may fail mid-execution)
           │
           ▼
  ┌──────────────────────────────────────────────┐
  │ Durable Execution Layer (Temporal / Dapr)     │
  │                                                │
  │  ┌───────────┐    ┌────────────┐    ┌───────┐ │
  │  │ Workflow   │───▶│ Activity   │───▶│ Event │ │
  │  │ (idempotent│    │ (tool call,│    │ Store │ │
  │  │  retry     │    │ retried on │    │ (WAL, │ │
  │  │  envelope) │    │ failure)   │    │ crash │ │
  │  │            │◀───│            │◀───│ safe) │ │
  │  └───────────┘    └────────────┘    └───────┘ │
  │                                                │
  │  On restart: replay event history,             │
  │  resume from last completed activity           │
  └──────────────────────────────────────────────┘

  Key insight: The MCP protocol itself is now stateless
  (2026-07-28). Durability lives OUTSIDE the protocol,
  in the orchestration layer wrapping tool calls.
```

**Failure classification for retry policy:**
```
┌──────────────────┬───────────────────┬─────────────────────────┐
│ Category          │ Examples           │ Action                   │
├──────────────────┼───────────────────┼─────────────────────────┤
│ Transient         │ Network timeout,   │ Retry with exponential   │
│                   │ 503, rate limit    │ backoff + jitter         │
│                   │                    │                          │
│ Permanent         │ 400 bad request,   │ Fail immediately,        │
│                   │ 404 not found,     │ surface to orchestrator  │
│                   │ auth denied        │                          │
│                   │                    │                          │
│ Poison pill       │ Valid request that  │ Dead-letter after N      │
│                   │ always crashes the │ attempts, alert ops      │
│                   │ server (bug)       │                          │
└──────────────────┴───────────────────┴─────────────────────────┘
```

### 4.3 Circuit Breaker State Machine

```
                     success
              ┌──────────────────┐
              │                  │
              ▼                  │
         ┌─────────┐      ┌─────┴──────┐      ┌──────────┐
    ────▶│ CLOSED   │─────▶│  OPEN       │─────▶│ HALF-OPEN │
         │ (normal) │ fail │ (reject all)│ timer│ (probe 1) │
         └─────────┘ thresh└────────────┘ expiry└─────┬─────┘
              ▲                  ▲                     │
              │                  │                     │
              │    success       │      failure        │
              └──────────────────┴─────────────────────┘

  CRITICAL: Circuit breakers are per-backend-dependency,
  NOT per-tool. Multiple tools share one backend API.
  Opening per-tool would leave other tools hitting the
  same failing backend.
```

### 4.4 Zero-Trust MCP Security

**OAuth 2.1 requirements (formalized Nov 2025):**
- Mandatory PKCE S256 for authorization code flow
- RFC 9728 for metadata discovery
- RFC 8707 for resource indicators
- Token passthrough is an explicit anti-pattern: MCP servers MUST NOT accept tokens not issued for that specific MCP server
- 2026-07-28: RFC 9207 issuer validation, `application_type` in DCR for desktop/CLI, credential binding to issuer (reuse across authorization servers forbidden)
- Dynamic Client Registration deprecated in favor of Client ID Metadata Documents (CIMD)

**Transport security:**
- Servers MUST validate `Origin` header (prevent DNS rebinding); invalid = 403
- Local servers SHOULD bind only to localhost (127.0.0.1), not 0.0.0.0
- Session IDs (pre-2026-07-28) MUST be globally unique and cryptographically secure

**Human-in-the-loop mandates (protocol-level):**
1. Users must explicitly consent to data access and tool invocation
2. Hosts must prompt for confirmation before invoking tools (especially destructive ones)
3. Sampling requests presented for user approval
4. Clear UI showing exposed tools with visual indicators on invocation

**PII filtering pipeline:**
```
  Tool response from MCP server
           │
           ▼
  ┌─────────────────┐
  │ PII DETECT       │ (NER / regex / Presidio)
  │ (SSN, email,     │
  │  phone, address) │
  └────────┬────────┘
           │ detected entities
           ▼
  ┌─────────────────┐
  │ REDACT           │ (replace with [REDACTED_SSN], etc.)
  └────────┬────────┘
           │ redacted response
           ▼
  ┌─────────────────┐
  │ AUDIT LOG        │ (log what was redacted, correlation ID,
  │                  │  original hash for forensics)
  └────────┬────────┘
           │ clean response
           ▼
  Model context window (no PII enters LLM)
```

**RBAC model:**
```
  ┌────────────────────────────────────────────────────────┐
  │ Gateway Policy Engine (OPA / Cedar)                     │
  │                                                         │
  │  Rule: allow(principal, action, resource)                │
  │                                                         │
  │  Example:                                               │
  │  permit(                                                │
  │    principal in Role::"analyst",                        │
  │    action == "tools/call",                              │
  │    resource.tool_name == "query_db"                     │
  │  ) when {                                               │
  │    resource.args.table in ["sales", "products"]  AND    │
  │    resource.args.operation == "SELECT"                   │
  │  };                                                     │
  │                                                         │
  │  Evaluated on EVERY tools/call, not just at connect.    │
  └────────────────────────────────────────────────────────┘
```

### 4.5 Known Threat Landscape

```
┌────┬──────────────────────┬──────────────────────────────────────┐
│ #   │ Threat                │ Description                           │
├────┼──────────────────────┼──────────────────────────────────────┤
│ 1   │ Tool Poisoning        │ Malicious instructions in tool        │
│    │                       │ metadata/descriptions. Appear benign  │
│    │                       │ to users, execute when agent reads    │
│    │                       │ metadata. Persist across sessions.    │
│    │                       │                                       │
│ 2   │ Confused Deputy       │ Proxy server attack: malicious client │
│    │                       │ obtains victim's auth through what    │
│    │                       │ appears as legitimate consent screen. │
│    │                       │                                       │
│ 3   │ Supply Chain          │ Compromised npm packages. Documented: │
│    │                       │ fake Postmark MCP server, 15 clean    │
│    │                       │ releases before adding exfiltration.  │
│    │                       │                                       │
│ 4   │ Prompt Injection via  │ Tool results injected back into LLM   │
│    │ Tool Results          │ context can manipulate agent behavior │
│    │                       │ if not sanitized.                     │
└────┴──────────────────────┴──────────────────────────────────────┘
```

### 4.6 Audit and Governance

- W3C Trace Context propagation: `traceparent`, `tracestate`, `baggage` in `_meta` for distributed trace correlation
- NSA/DoD joint MCP Security Design guidance (May 2026)
- OWASP MCP Top 10 project
- Cloud Security Alliance Agentic MCP Security Best Practices Guide
- MCP donated to Agentic AI Foundation (Linux Foundation, Dec 2025), co-founded by Anthropic, Block, OpenAI, with Google, Microsoft, AWS, Cloudflare, Bloomberg
- Specification Enhancement Proposals (SEPs) for protocol changes
- 12-month minimum deprecation window

**Phased enterprise implementation timeline:**
```
  Day 0-30:   Inventory + basic audit logging + secret management
  Day 30-90:  Gateway architecture + approval processes
  Day 90-180: Complete identification pipelines + governance frameworks
```

---

## 5. Production Enterprise Code

### 5.1 MCP Gateway Client with Circuit Breakers, Retries, PII Redaction, and Structured Logging

```python
"""
Production MCP gateway client.
Wraps multiple backend MCP servers with:
  - Per-backend circuit breakers (not per-tool)
  - Exponential backoff with jitter
  - PII detection and redaction
  - Structured JSON logging with correlation IDs
  - Graceful degradation via fallback model chains
  - Per-session retry budgets
"""

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
from typing import Any


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------

class StructuredLogger:
    """JSON-structured logger with correlation ID propagation."""

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(handler)
        self._logger.setLevel(logging.INFO)

    def log(
        self,
        level: str,
        event: str,
        correlation_id: str,
        **kwargs: Any,
    ) -> None:
        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "level": level,
            "event": event,
            "correlation_id": correlation_id,
            **kwargs,
        }
        getattr(self._logger, level.lower())(json.dumps(record))


logger = StructuredLogger("mcp_gateway")


# ---------------------------------------------------------------------------
# PII redaction
# ---------------------------------------------------------------------------

_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")),
    ("PHONE_US", re.compile(r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    ("CREDIT_CARD", re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")),
]


@dataclass
class RedactionResult:
    cleaned_text: str
    redacted_count: int
    original_hash: str  # SHA-256 of original for forensic correlation


def redact_pii(text: str) -> RedactionResult:
    """Detect and redact PII before tool results enter model context."""
    original_hash = hashlib.sha256(text.encode()).hexdigest()
    redacted_count = 0
    cleaned = text
    for label, pattern in _PII_PATTERNS:
        matches = pattern.findall(cleaned)
        redacted_count += len(matches)
        cleaned = pattern.sub(f"[REDACTED_{label}]", cleaned)
    return RedactionResult(
        cleaned_text=cleaned,
        redacted_count=redacted_count,
        original_hash=original_hash,
    )


# ---------------------------------------------------------------------------
# Circuit breaker (per-backend, not per-tool)
# ---------------------------------------------------------------------------

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """
    Per-backend circuit breaker.

    CLOSED: normal operation. Failures increment counter.
    OPEN:   all calls rejected immediately. Timer starts.
    HALF_OPEN: one probe call allowed. Success -> CLOSED, failure -> OPEN.
    """

    backend_name: str
    failure_threshold: int = 5
    recovery_timeout_s: float = 30.0
    _state: CircuitState = CircuitState.CLOSED
    _failure_count: int = 0
    _last_failure_time: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._last_failure_time >= self.recovery_timeout_s:
                return CircuitState.HALF_OPEN
        return self._state

    async def call(self, coro):
        """Execute coro through the circuit breaker."""
        async with self._lock:
            current = self.state
            if current == CircuitState.OPEN:
                raise CircuitOpenError(
                    f"Circuit OPEN for backend '{self.backend_name}'. "
                    f"Recovery in {self.recovery_timeout_s - (time.monotonic() - self._last_failure_time):.1f}s"
                )

        try:
            result = await coro
        except Exception as exc:
            async with self._lock:
                self._failure_count += 1
                self._last_failure_time = time.monotonic()
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitState.OPEN
            raise
        else:
            async with self._lock:
                self._failure_count = 0
                self._state = CircuitState.CLOSED
            return result


class CircuitOpenError(Exception):
    pass


# ---------------------------------------------------------------------------
# Retry with exponential backoff and jitter
# ---------------------------------------------------------------------------

_TRANSIENT_CODES = {-32603, 503, 429}  # internal error, service unavailable, rate limit


async def retry_with_backoff(
    coro_factory,
    *,
    max_retries: int = 3,
    base_delay_s: float = 0.5,
    max_delay_s: float = 10.0,
    correlation_id: str,
) -> Any:
    """
    Retry a coroutine with exponential backoff and full jitter.

    Jitter formula: delay = random(0, min(max_delay, base * 2^attempt))
    This is the "full jitter" strategy from AWS architecture blog --
    it decorrelates competing retries better than equal jitter.
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except CircuitOpenError:
            raise  # never retry a circuit-open rejection
        except Exception as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            # Only retry transient errors
            error_code = getattr(exc, "code", None)
            if error_code is not None and error_code not in _TRANSIENT_CODES:
                break

            cap = min(max_delay_s, base_delay_s * (2 ** attempt))
            delay = random.uniform(0, cap)  # full jitter
            logger.log(
                "warning",
                "retry_scheduled",
                correlation_id,
                attempt=attempt + 1,
                delay_s=round(delay, 3),
                error=str(exc),
            )
            await asyncio.sleep(delay)

    raise last_exc


# ---------------------------------------------------------------------------
# Per-session retry budget
# ---------------------------------------------------------------------------

@dataclass
class RetryBudget:
    """
    Caps total retries per session to prevent cascading failures.
    After budget exhaustion, all subsequent calls fail fast.
    """

    max_retries_per_session: int = 20
    _used: int = 0

    def consume(self) -> bool:
        if self._used >= self.max_retries_per_session:
            return False
        self._used += 1
        return True

    @property
    def remaining(self) -> int:
        return max(0, self.max_retries_per_session - self._used)


# ---------------------------------------------------------------------------
# Fallback model chain
# ---------------------------------------------------------------------------

@dataclass
class ModelTier:
    name: str
    cost_per_1m_input: float
    max_context: int
    latency_budget_ms: int


FALLBACK_CHAIN: list[ModelTier] = [
    ModelTier("claude-opus-4", 15.0, 200_000, 30_000),
    ModelTier("claude-sonnet-4", 3.0, 200_000, 15_000),
    ModelTier("claude-haiku-3.5", 0.80, 200_000, 5_000),
]


async def call_with_fallback(
    messages: list[dict],
    llm_call_fn,  # async (model_name, messages) -> response
    correlation_id: str,
) -> Any:
    """
    Try models in descending capability order.
    On timeout or rate limit, fall back to next tier.
    """
    for i, tier in enumerate(FALLBACK_CHAIN):
        try:
            logger.log(
                "info", "llm_attempt", correlation_id,
                model=tier.name, tier=i,
            )
            result = await asyncio.wait_for(
                llm_call_fn(tier.name, messages),
                timeout=tier.latency_budget_ms / 1000,
            )
            logger.log(
                "info", "llm_success", correlation_id,
                model=tier.name,
            )
            return result
        except (asyncio.TimeoutError, Exception) as exc:
            logger.log(
                "warning", "llm_fallback", correlation_id,
                model=tier.name, error=str(exc),
                next_model=FALLBACK_CHAIN[i + 1].name if i + 1 < len(FALLBACK_CHAIN) else None,
            )
            if i == len(FALLBACK_CHAIN) - 1:
                raise

    raise RuntimeError("All model tiers exhausted")


# ---------------------------------------------------------------------------
# MCP Gateway Client
# ---------------------------------------------------------------------------

@dataclass
class MCPServerConfig:
    name: str
    url: str  # e.g., "https://mcp-server.internal/mcp"
    backend_name: str  # for circuit breaker grouping
    tools: list[str] = field(default_factory=list)


class MCPGatewayClient:
    """
    Production MCP gateway client managing multiple backend servers.

    Key design decisions:
    - Circuit breakers are per-backend, not per-tool (multiple tools share a backend).
    - PII redaction happens BEFORE tool results enter model context.
    - Every call gets a correlation ID for distributed tracing.
    - Retry budget is per-session to prevent cascading failures.
    """

    def __init__(self, servers: list[MCPServerConfig]):
        self._servers = {s.name: s for s in servers}
        self._tool_to_server: dict[str, str] = {}
        for server in servers:
            for tool in server.tools:
                self._tool_to_server[tool] = server.name

        # One circuit breaker per backend dependency
        backend_names = {s.backend_name for s in servers}
        self._breakers = {
            name: CircuitBreaker(backend_name=name) for name in backend_names
        }
        self._retry_budget = RetryBudget()

    def _get_breaker(self, tool_name: str) -> CircuitBreaker:
        server_name = self._tool_to_server.get(tool_name)
        if not server_name:
            raise ValueError(f"No server registered for tool '{tool_name}'")
        backend = self._servers[server_name].backend_name
        return self._breakers[backend]

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_s: float = 30.0,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Execute a tool call through the gateway with full resilience stack.

        Flow:
        1. Assign correlation ID
        2. Check retry budget
        3. Retry with backoff (through circuit breaker)
        4. Apply per-call timeout
        5. Redact PII from response
        6. Log audit record
        """
        cid = correlation_id or str(uuid.uuid4())
        breaker = self._get_breaker(tool_name)

        logger.log(
            "info", "tool_call_start", cid,
            tool=tool_name, backend=breaker.backend_name,
            circuit_state=breaker.state.value,
            retry_budget_remaining=self._retry_budget.remaining,
        )

        async def _execute():
            if not self._retry_budget.consume():
                raise RuntimeError(
                    f"Retry budget exhausted ({self._retry_budget.max_retries_per_session} retries). "
                    "Failing fast to prevent cascade."
                )
            return await breaker.call(
                asyncio.wait_for(
                    self._raw_tool_call(tool_name, arguments, cid),
                    timeout=timeout_s,
                )
            )

        try:
            raw_result = await retry_with_backoff(
                _execute, max_retries=3, correlation_id=cid,
            )
        except CircuitOpenError as exc:
            logger.log("error", "circuit_open_rejection", cid, tool=tool_name, error=str(exc))
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Service temporarily unavailable: {exc}"}],
            }
        except Exception as exc:
            logger.log("error", "tool_call_failed", cid, tool=tool_name, error=str(exc))
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Tool execution failed after retries: {exc}"}],
            }

        # PII redaction on response text before it enters model context
        if "content" in raw_result:
            for item in raw_result["content"]:
                if item.get("type") == "text" and "text" in item:
                    redaction = redact_pii(item["text"])
                    item["text"] = redaction.cleaned_text
                    if redaction.redacted_count > 0:
                        logger.log(
                            "warning", "pii_redacted", cid,
                            tool=tool_name,
                            redacted_count=redaction.redacted_count,
                            original_hash=redaction.original_hash,
                        )

        # Immutable audit record
        logger.log(
            "info", "tool_call_complete", cid,
            tool=tool_name,
            backend=breaker.backend_name,
            circuit_state=breaker.state.value,
        )

        return raw_result

    async def _raw_tool_call(
        self, tool_name: str, arguments: dict[str, Any], correlation_id: str,
    ) -> dict[str, Any]:
        """
        Send a JSON-RPC 2.0 tools/call request to the appropriate MCP server.

        In production, this would use httpx/aiohttp to POST to the server URL.
        The _meta block carries protocol version and client identity per 2026-07-28 spec.
        """
        server = self._servers[self._tool_to_server[tool_name]]
        request_body = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
                "_meta": {
                    "protocolVersion": "2026-07-28",
                    "clientId": "mcp-gateway-client-v1",
                    # W3C Trace Context for distributed tracing
                    "traceparent": f"00-{correlation_id.replace('-', '')[:32].ljust(32, '0')}-{uuid.uuid4().hex[:16]}-01",
                },
            },
        }

        # In production: response = await httpx_client.post(server.url, json=request_body)
        # Simulated for demonstration:
        logger.log(
            "debug", "json_rpc_request", correlation_id,
            server=server.name, url=server.url,
            method="tools/call", tool=tool_name,
        )

        # Placeholder -- replace with actual HTTP call
        return {
            "content": [{"type": "text", "text": f"Result from {tool_name}"}],
            "isError": False,
        }


# ---------------------------------------------------------------------------
# OAuth token refresh with jitter (prevents refresh storms)
# ---------------------------------------------------------------------------

@dataclass
class TokenManager:
    """
    Manages OAuth token lifecycle with jittered refresh to prevent
    refresh storms when hundreds of agent sessions hit expiry simultaneously.

    Refresh at 80% of token lifetime with up to 10% random offset.
    Single-flight lock ensures only one refresh per backend at a time.
    """

    token: str = ""
    expires_at: float = 0.0
    token_lifetime_s: float = 3600.0
    _refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def _refresh_threshold(self) -> float:
        """80% of lifetime + up to 10% jitter."""
        base = self.expires_at - (self.token_lifetime_s * 0.20)
        jitter = random.uniform(0, self.token_lifetime_s * 0.10)
        return base - jitter

    async def get_token(self, refresh_fn) -> str:
        """Return current token, refreshing if within jittered threshold."""
        if time.monotonic() < self._refresh_threshold():
            return self.token

        async with self._refresh_lock:
            # Double-check after acquiring lock (another coroutine may have refreshed)
            if time.monotonic() < self._refresh_threshold():
                return self.token

            new_token, new_lifetime = await refresh_fn()
            self.token = new_token
            self.token_lifetime_s = new_lifetime
            self.expires_at = time.monotonic() + new_lifetime
            return self.token


# ---------------------------------------------------------------------------
# Usage example
# ---------------------------------------------------------------------------

async def main():
    servers = [
        MCPServerConfig(
            name="github-mcp",
            url="https://mcp-github.internal/mcp",
            backend_name="github-api",
            tools=["list_repos", "create_issue", "get_pr"],
        ),
        MCPServerConfig(
            name="postgres-mcp",
            url="https://mcp-pg.internal/mcp",
            backend_name="postgres-primary",
            tools=["query_db", "list_tables"],
        ),
        MCPServerConfig(
            name="slack-mcp",
            url="https://mcp-slack.internal/mcp",
            backend_name="slack-api",
            tools=["send_message", "list_channels"],
        ),
    ]

    client = MCPGatewayClient(servers)

    result = await client.call_tool(
        "query_db",
        {"sql": "SELECT name, email FROM users WHERE active = true LIMIT 10"},
        timeout_s=15.0,
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
```

### 5.2 Per-Call Timeout Wrapper for JSON-RPC Hang Prevention

```python
"""
Prevents silent JSON-RPC hangs (failure mode #5).
The MCP protocol has no built-in timeout. This wraps every
outbound JSON-RPC request ID with an asyncio timeout.
"""

import asyncio
from typing import Any


class JSONRPCTimeoutError(Exception):
    def __init__(self, method: str, request_id: str, timeout_s: float):
        self.method = method
        self.request_id = request_id
        self.timeout_s = timeout_s
        super().__init__(
            f"JSON-RPC request {request_id} ({method}) timed out after {timeout_s}s. "
            "Server may have crashed mid-handler."
        )


async def json_rpc_call_with_timeout(
    send_fn,       # async (request_body) -> response
    method: str,
    params: dict[str, Any],
    *,
    request_id: str,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """
    Wrap a JSON-RPC call with a per-request timeout.

    Without this, a server crash mid-handler means the client
    waits forever -- the JSON-RPC id never gets a response.
    """
    request_body = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }

    try:
        response = await asyncio.wait_for(
            send_fn(request_body),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        raise JSONRPCTimeoutError(method, request_id, timeout_s)

    if "error" in response:
        error = response["error"]
        raise JSONRPCError(error.get("code", -1), error.get("message", "Unknown"))

    return response.get("result", {})


class JSONRPCError(Exception):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"JSON-RPC error {code}: {message}")
```

### 5.3 Token Tax Calculator

```python
"""
Calculate the token cost of an MCP tool setup.
Use this to right-size your tool catalog before deployment.
"""

from dataclasses import dataclass


@dataclass
class TokenTaxReport:
    total_schema_tokens: int
    context_pct: float           # % of context window consumed by schemas
    cost_per_conv_uncached: float # $ per conversation, no cache
    cost_per_conv_cached: float   # $ per conversation, with cache
    cost_per_1k_uncached: float
    cost_per_1k_cached: float
    reasoning_tokens_lost: int   # tokens unavailable for reasoning


def calculate_token_tax(
    num_tools: int,
    tokens_per_tool: int = 1_000,
    context_window: int = 200_000,
    input_price_per_1m: float = 15.0,   # $/1M tokens (e.g., Claude Opus)
    cache_hit_rate: float = 0.75,
    cached_price_ratio: float = 0.10,   # cached tokens cost 10% of full price
) -> TokenTaxReport:
    total_tokens = num_tools * tokens_per_tool
    context_pct = (total_tokens / context_window) * 100

    uncached_cost = total_tokens * input_price_per_1m / 1_000_000
    effective_price = (
        (1 - cache_hit_rate) * input_price_per_1m
        + cache_hit_rate * (input_price_per_1m * cached_price_ratio)
    )
    cached_cost = total_tokens * effective_price / 1_000_000

    return TokenTaxReport(
        total_schema_tokens=total_tokens,
        context_pct=round(context_pct, 1),
        cost_per_conv_uncached=round(uncached_cost, 4),
        cost_per_conv_cached=round(cached_cost, 4),
        cost_per_1k_uncached=round(uncached_cost * 1000, 2),
        cost_per_1k_cached=round(cached_cost * 1000, 2),
        reasoning_tokens_lost=total_tokens,
    )


# Example usage:
if __name__ == "__main__":
    report = calculate_token_tax(num_tools=20)
    print(f"Schema tokens:        {report.total_schema_tokens:,}")
    print(f"Context consumed:     {report.context_pct}%")
    print(f"$/conv (no cache):    ${report.cost_per_conv_uncached}")
    print(f"$/conv (75% cache):   ${report.cost_per_conv_cached}")
    print(f"$/1k conv (no cache): ${report.cost_per_1k_uncached}")
    print(f"$/1k conv (cached):   ${report.cost_per_1k_cached}")
    print(f"Reasoning tokens lost:{report.reasoning_tokens_lost:,}")
```

---

## 6. Architectural System Design Scenarios

### Scenario A: Multi-Tenant SaaS AI Assistant with MCP Gateway

**Problem statement**: A B2B SaaS company serves 500 enterprise customers. Each customer's AI assistant needs access to customer-specific data (Postgres, S3, Salesforce) through MCP tools, but no customer should see another's data. The system must handle 1,000 concurrent agent sessions, comply with SOC 2, and keep per-conversation cost under $0.20.

**Architecture:**

```
  ┌─────────────────────────────────────────────────────────────────┐
  │                     CUSTOMER AI ASSISTANTS                       │
  │  ┌─────────┐  ┌─────────┐  ┌─────────┐       ┌─────────┐      │
  │  │ Tenant A │  │ Tenant B │  │ Tenant C │  ...  │ Tenant N │      │
  │  │ Agent    │  │ Agent    │  │ Agent    │       │ Agent    │      │
  │  └────┬────┘  └────┬────┘  └────┬────┘       └────┬────┘      │
  └───────┼────────────┼────────────┼──────────────────┼───────────┘
          │            │            │                  │
          ▼            ▼            ▼                  ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │                    MCP GATEWAY (Envoy AI Gateway)                 │
  │                                                                   │
  │  ┌────────────────┐  ┌──────────────────┐  ┌─────────────────┐  │
  │  │ OAuth 2.1 +     │  │ Tenant-Scoped    │  │ Tool Catalog    │  │
  │  │ PKCE AuthN      │──▶│ Tool Filter      │──▶│ Cache (ttlMs)   │  │
  │  │ (per-tenant     │  │ (OPA policy:     │  │ (per-tenant     │  │
  │  │  credentials)   │  │  tenant_id in    │  │  scope, 5min    │  │
  │  │                 │  │  JWT -> allow    │  │  TTL)           │  │
  │  │                 │  │  only that       │  │                 │  │
  │  │                 │  │  tenant's tools) │  │                 │  │
  │  └────────────────┘  └────────┬─────────┘  └─────────────────┘  │
  │                               │                                   │
  │  ┌────────────────┐  ┌───────▼──────────┐  ┌─────────────────┐  │
  │  │ Per-Backend     │  │ PII Redaction    │  │ Audit Logger    │  │
  │  │ Circuit Breaker │──▶│ (Presidio, runs  │──▶│ (immutable,     │  │
  │  │ (5 failures ->  │  │  before model    │  │  hash-chained,  │  │
  │  │  30s cooldown)  │  │  context)        │  │  SOC 2 ready)   │  │
  │  └────────────────┘  └────────┬─────────┘  └─────────────────┘  │
  └───────────────────────────────┼──────────────────────────────────┘
                                  │
          ┌───────────────────────┼─────────────────────┐
          │                       │                     │
  ┌───────▼────────┐  ┌──────────▼─────────┐  ┌───────▼────────┐
  │ Postgres MCP    │  │ S3 Documents MCP    │  │ Salesforce MCP  │
  │ Server          │  │ Server              │  │ Server          │
  │                 │  │                     │  │                 │
  │ Row-level       │  │ Bucket prefix       │  │ Org-scoped      │
  │ security via    │  │ isolation via       │  │ API tokens via  │
  │ SET ROLE +      │  │ IAM session         │  │ per-tenant      │
  │ RLS policies    │  │ policies            │  │ connected app   │
  └────────────────┘  └─────────────────────┘  └────────────────┘
```

**Trade-off matrix:**
```
┌──────────────┬──────────────────────────────────────────────────────┐
│ Dimension     │ Analysis                                             │
├──────────────┼──────────────────────────────────────────────────────┤
│ Cost          │ Gateway overhead: 11us/req (Envoy). Tool schemas     │
│               │ cached per-tenant (5min TTL) -> 75%+ cache hits.     │
│               │ 20 tools * $0.10/conv cached = $0.10/conv.           │
│               │ Within $0.20 budget. Tiered schemas would cut to     │
│               │ ~$0.04 but adds implementation complexity.           │
│               │                                                      │
│ Latency       │ P99 per tool call: ~50ms (Streamable HTTP + local    │
│               │ gateway). 10-tool task: ~500ms. Acceptable for       │
│               │ conversational UX. Circuit breaker adds <1ms.        │
│               │                                                      │
│ Ops           │ Envoy AI Gateway: stateless, no Redis needed          │
│               │ (token-encoding architecture). Horizontal scaling    │
│               │ via K8s HPA. Health checks must validate SSE         │
│               │ streams, not just HTTP 200.                          │
│               │                                                      │
│ Security      │ Row-level security in Postgres (RLS) is the          │
│               │ strongest tenant isolation. S3 bucket-prefix with     │
│               │ IAM session policies is next best. PII redaction     │
│               │ before model context prevents data leakage.          │
│               │ SOC 2 covered by immutable audit log.                │
│               │                                                      │
│ Scalability   │ 300 RPS sustained per gateway instance.               │
│               │ 1,000 concurrent sessions * 10 tools/session =       │
│               │ 10,000 tool calls/session. At 1 task/min =           │
│               │ ~167 RPS. Single gateway instance sufficient.         │
│               │ Add replica at 250 RPS for headroom.                 │
└──────────────┴──────────────────────────────────────────────────────┘
```

**Decision rationale**: Envoy AI Gateway chosen over Microsoft MCP Gateway because its token-encoding architecture eliminates the Redis dependency for session management -- critical when the 2026-07-28 spec makes the protocol stateless and any server instance must handle any request. OPA chosen over Cedar for the policy engine because the team has existing OPA expertise and SOC 2 audit tooling already integrates with OPA decision logs. Row-level security in Postgres chosen over application-level filtering because it cannot be bypassed by prompt injection -- even if the LLM crafts a malicious SQL query, RLS policies enforce tenant boundaries at the database level.

---

### Scenario B: Internal Developer Platform with Federated MCP Gateway

**Problem statement**: A 5,000-engineer enterprise has 3 regional engineering centers (US-West, EU-Frankfurt, APAC-Singapore). Each center runs its own AI-assisted developer tools (code review agents, incident responders, documentation generators). Central platform team needs unified audit, consistent tool governance, and <100ms P95 tool call latency within each region. The tool catalog spans 200+ tools across 40 MCP servers.

**Architecture:**

```
  ┌─────────────────────────────────────────────────────────────┐
  │              CENTRAL GOVERNANCE PLANE (us-west)              │
  │                                                              │
  │  ┌───────────────┐  ┌─────────────────┐  ┌───────────────┐ │
  │  │ Global Policy  │  │ Audit Aggregator │  │ Tool Registry │ │
  │  │ (Cedar rules,  │  │ (receives audit  │  │ (canonical    │ │
  │  │  versioned,    │  │  streams from    │  │  tool catalog,│ │
  │  │  replicated    │  │  all regions;    │  │  versioned    │ │
  │  │  to regions)   │  │  SOX compliance) │  │  schemas)     │ │
  │  └───────┬───────┘  └────────▲────────┘  └───────┬───────┘ │
  └──────────┼───────────────────┼────────────────────┼─────────┘
             │ policy sync       │ audit events       │ catalog sync
    ┌────────┴───────┬───────────┴─────────┬──────────┴──────┐
    │                │                     │                  │
    ▼                ▼                     ▼                  ▼
┌──────────┐  ┌──────────────┐  ┌──────────────┐
│ US-West   │  │ EU-Frankfurt  │  │ APAC-Singapore│
│ Regional  │  │ Regional      │  │ Regional      │
│ Gateway   │  │ Gateway       │  │ Gateway       │
│           │  │               │  │               │
│ ┌───────┐ │  │ ┌───────────┐ │  │ ┌───────────┐ │
│ │Bifrost │ │  │ │Bifrost    │ │  │ │Bifrost    │ │
│ │Gateway │ │  │ │Gateway    │ │  │ │Gateway    │ │
│ │        │ │  │ │           │ │  │ │           │ │
│ │Code    │ │  │ │Code Mode  │ │  │ │Code Mode  │ │
│ │Mode    │ │  │ │(200+ tools│ │  │ │(200+ tools│ │
│ │(92.8%  │ │  │ │ -> 92.8%  │ │  │ │ -> 92.8%  │ │
│ │token   │ │  │ │ token     │ │  │ │ token     │ │
│ │savings)│ │  │ │ savings)  │ │  │ │ savings)  │ │
│ └───┬───┘ │  │ └─────┬─────┘ │  │ └─────┬─────┘ │
│     │     │  │       │       │  │       │       │
│  ┌──▼──┐  │  │  ┌────▼────┐  │  │  ┌────▼────┐  │
│  │Local │  │  │  │Local    │  │  │  │Local    │  │
│  │MCP   │  │  │  │MCP      │  │  │  │MCP      │  │
│  │Servers│  │  │  │Servers  │  │  │  │Servers  │  │
│  │(15)  │  │  │  │(12)     │  │  │  │(13)     │  │
│  └──────┘  │  │  └─────────┘  │  │  └─────────┘  │
└────────────┘  └───────────────┘  └───────────────┘
```

**Trade-off matrix:**
```
┌──────────────┬──────────────────────────────────────────────────────┐
│ Dimension     │ Analysis                                             │
├──────────────┼──────────────────────────────────────────────────────┤
│ Cost          │ 200+ tools with naive schema injection: ~200k tokens │
│               │ = entire context window consumed. Bifrost Code Mode  │
│               │ reduces to ~15k tokens (92.8% savings), making 200+ │
│               │ tools viable. 3 gateway instances: ~$500/mo compute.│
│               │ Per-conversation: ~$0.02 (Code Mode) vs. $3.00      │
│               │ (naive). Code Mode is non-negotiable at this scale.  │
│               │                                                      │
│ Latency       │ Regional gateways: P95 < 25ms (intra-region).        │
│               │ Cross-region policy sync: async, 30s propagation.    │
│               │ Tool calls never cross regions. Audit events are     │
│               │ async-shipped to central aggregator (eventual         │
│               │ consistency acceptable for audit, not for policy).   │
│               │                                                      │
│ Ops           │ Bifrost is open-source, runs as K8s deployment.      │
│               │ 6 upstream auth types cover all internal servers.     │
│               │ Per-virtual-key tool filtering enables per-team      │
│               │ tool access without per-team gateway instances.      │
│               │ Operational burden: 3 regional deployments + 1       │
│               │ central governance plane.                            │
│               │                                                      │
│ Security      │ Cedar policies versioned and replicated (not         │
│               │ authored locally). Central team controls what tools  │
│               │ are available globally. Regional gateways enforce    │
│               │ but cannot modify policy. Tool poisoning mitigated   │
│               │ by central registry verification (namespace trust,  │
│               │ not code scanning). Supply chain risk: pin server    │
│               │ versions, CVE feed per installed server.             │
│               │                                                      │
│ Scalability   │ Each regional gateway: 300 RPS. 5,000 engineers /    │
│               │ 3 regions = ~1,667 engineers/region. At 5 tool       │
│               │ calls/task, 1 task/engineer/5min = ~28 RPS/region.   │
│               │ 10x headroom per region. Horizontal scaling via      │
│               │ K8s HPA if needed. Catalog sync: ttlMs caching       │
│               │ prevents thundering herd on regional gateway restart.│
└──────────────┴──────────────────────────────────────────────────────┘
```

**Decision rationale**: Federated gateway chosen over single centralized gateway because cross-region latency (US-West to APAC: ~150ms RTT) would blow the <100ms P95 budget for every tool call. Bifrost chosen over Envoy AI Gateway because Code Mode is essential at 200+ tools -- without it, tool schemas alone consume the entire context window. Cedar chosen over OPA for policy because Cedar's formal verification properties provide provable guarantees that no policy combination can grant unintended access -- critical when 3 regional teams can request policy changes. Central audit aggregator is eventually consistent (not strongly consistent) because audit latency tolerance is minutes, not milliseconds, and strong consistency across 3 regions would add 100-200ms to every tool call for quorum writes.

---

*Module compiled from 22 sources. Research date: 2026-09-29. Protocol versions covered: 2024-11-05 through 2026-07-28.*
