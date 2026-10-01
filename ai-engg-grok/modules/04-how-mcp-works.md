# Module 04 — How MCP Works

**Audience**: Principal AI Architect interview prep · personal deep study  
**Sequence**: 04 (protocol integration layer after agent runtime + IR patterns)  
**Grounded in**: `research/04-how-mcp-works.md` (18 sources, 2026-09-29; primary contract **MCP `2025-11-25`**, forward-checked against **`2026-07-28`**)

Anthropic launched MCP in November 2024 to collapse the **N×M** connector problem: without a shared protocol, **N** AI apps × **M** data sources need bespoke integrations; MCP reduces that to **N clients + M servers** ([System Design Newsletter #110](https://newsletter.systemdesign.one/p/how-mcp-works); [MCP intro](https://modelcontextprotocol.io)). USB-C analogy: one standard port for hosts ↔ external systems. Spec inspiration is LSP—standardize the capability surface so one server works across many hosts ([Spec overview](https://modelcontextprotocol.io/specification/2025-11-25)). Sibling modules cover agent loops (02) and IR agents (03); this module is the **tool/context protocol** those runtimes speak.

---

## Part 1 — System Topology & Data Flow

### Architecture map

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            CONTROL PLANE                                    │
│  Host (Claude / VS Code / Cursor / custom agent)                            │
│  UX · consent gates · one MCP Client per connected Server                   │
│  initialize handshake · capability negotiation · session / deadline policy  │
│  tool allow/deny · HITL before tools/call · max-turns / cancellation        │
│                                                                             │
│  ┌──────────────┐  ┌────────────────┐  ┌─────────────────────────────────┐  │
│  │ Client A     │  │ Client B       │  │ Client C                        │  │
│  │ ↔ Server A   │  │ ↔ Server B     │  │ ↔ Server C                      │  │
│  │ (1:1 session)│  │ (1:1 session)  │  │ (1:1 session)                   │  │
│  └──────┬───────┘  └───────┬────────┘  └────────────────┬────────────────┘  │
└─────────┼──────────────────┼────────────────────────────┼───────────────────┘
          │ JSON-RPC 2.0     │                            │
          ▼                  ▼                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                             DATA PLANE                                      │
│  Transport: stdio (subprocess)  |  Streamable HTTP (POST ± SSE)             │
│  Lifecycle: initialize → notifications/initialized → operation → shutdown   │
│  Methods: tools/list · tools/call · resources/* · prompts/* · sampling/*    │
│  Message types: Request / Result / Error / Notification (no id)             │
└───┬───────────────────────────────┬───────────────────────────────┬─────────┘
    │                               │                               │
    ▼                               ▼                               ▼
┌───────────────────┐   ┌───────────────────────┐   ┌─────────────────────────┐
│   TOOL PROXIES    │   │     PERSISTENCE       │   │      TELEMETRY          │
├───────────────────┤   ├───────────────────────┤   ├─────────────────────────┤
│ MCP Server adapters│  │ MCP-Session-Id store  │   │ correlation / request id│
│ native ops (SQL,  │   │ (HTTP 2025-11-25)     │   │ tools/call audit trail  │
│  SaaS, fs, git)   │   │ SSE Last-Event-Id     │   │ progress / cancel       │
│ schema validate   │   │ resource subscribe    │   │ Origin / OAuth metrics  │
│ rate-limit tools  │   │ task handles (ext)    │   │ transport p50/p95/p99   │
│ isError → model   │   │ host checkpoints*     │   │ PII redact events       │
└───────────────────┘   └───────────────────────┘   └─────────────────────────┘
  * MCP is not a workflow engine — host adds Temporal/LangGraph checkpoints
```

**Plane responsibilities**

| Plane | What lives here | Truth source |
| --- | --- | --- |
| **CONTROL PLANE** | Host app; creates **one client per server**; consent; capability policy; turn caps | [Architecture overview](https://modelcontextprotocol.io/docs/2025-11-25/learn/architecture); [Spec overview](https://modelcontextprotocol.io/specification/2025-11-25) |
| **DATA PLANE** | JSON-RPC 2.0 methods over stdio or Streamable HTTP; negotiated features only | [Base protocol](https://modelcontextprotocol.io/specification/2025-11-25/basic); [Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) |
| **PERSISTENCE** | Optional `MCP-Session-Id`; SSE resume; resource subscriptions; **host-owned** agent checkpoints | [Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports); [Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources) |
| **TOOL PROXIES** | Servers translate `tools/call` / `resources/read` into native ops; schemas + rate limits | [Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools); [Newsletter #110](https://newsletter.systemdesign.one/p/how-mcp-works) |
| **TELEMETRY** | Request IDs, audit of tool usage, progress/cancel, transport latency, security events | [Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools); [Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle) |

### End-to-end request-flow narrative (`initialize` → `tools/list` → `tools/call`)

1. **Host bootstraps control plane** — User connects a server (local binary or remote URL). Host creates a dedicated MCP **Client** for that **Server** (1:1 session) ([Architecture overview](https://modelcontextprotocol.io/docs/2025-11-25/learn/architecture)).
2. **`initialize` (data plane handshake)** — Client sends JSON-RPC `initialize` with `protocolVersion`, client capabilities (`roots` / `sampling` / `elicitation` / `tasks` as applicable), and `clientInfo`. Server returns negotiated version, server capabilities (`tools` / `resources` / `prompts` / …), `serverInfo`, optional `instructions` ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)).
3. **`notifications/initialized`** — Client notifies readiness. Only **negotiated** capabilities may be used in the Operation phase ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)). Over HTTP, subsequent requests MUST carry `MCP-Protocol-Version` (e.g. `2025-11-25`); missing header SHOULD be treated as `2025-03-26` ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)).
4. **Optional HTTP session mint** — Server MAY return `MCP-Session-Id` on `InitializeResult`; client MUST echo it; DELETE ends session; 404 → re-`initialize` ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)).
5. **`tools/list` (discovery)** — Client requests the tool catalog (JSON Schema `inputSchema` per tool). Dynamic discovery means adding a server tool does not require host code changes ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools); [Newsletter #110](https://newsletter.systemdesign.one/p/how-mcp-works)). Host merges schemas into the model’s tool prompt (control plane). Prefer `listChanged` notifications over blind re-polling ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)).
6. **Model turn (host, outside MCP framing)** — Host builds context → LLM emits a tool call. Spec requires human consent before invocation; annotations are untrusted unless the server is trusted ([Spec overview](https://modelcontextprotocol.io/specification/2025-11-25); [Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)).
7. **`tools/call` (tool proxy)** — Client issues `tools/call` with name + arguments. Server validates schema; unknown/malformed → JSON-RPC error (e.g. `-32602`); business failures return `result` with `isError: true` so the model can self-correct ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)).
8. **Observe → loop** — `content` / `structuredContent` returns to host, appends to model context, next turn. Timeouts + `CancelledNotification` on expiry; progress MAY soft-extend but a max timeout SHOULD remain ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)).
9. **Shutdown** — Transport-specific (stdio: close stdin / SIGTERM / SIGKILL; HTTP: close connections). No dedicated shutdown RPC ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)).

[inferred] Host path: turn build → model tool call → MCP `tools/call` → server native op → observation → context append.

---

## Part 2 — Core Mechanics & Algorithms

### Participants & two layers

| Role | Responsibility |
| --- | --- |
| **Host** | User-facing LLM app; orchestrates UX/consent; one client per server |
| **Client** | Protocol connector; dedicated 1:1 session; translates host intents → MCP methods |
| **Server** | Exposes tools/resources/prompts; translates MCP → native ops |

Local **stdio** servers typically serve one client; remote **Streamable HTTP** servers typically serve many ([Architecture overview](https://modelcontextprotocol.io/docs/2025-11-25/learn/architecture)).

1. **Data layer** — JSON-RPC 2.0 semantics (lifecycle, primitives, client features, notifications).
2. **Transport layer** — Framing + auth (stdio vs Streamable HTTP; custom allowed if JSON-RPC + lifecycle preserved) ([Architecture overview](https://modelcontextprotocol.io/docs/2025-11-25/learn/architecture); [Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)).

### JSON-RPC 2.0 message types

All messages MUST be JSON-RPC 2.0 UTF-8 ([Base protocol](https://modelcontextprotocol.io/specification/2025-11-25/basic)):

| Type | Key fields | Notes |
| --- | --- | --- |
| **Request** | `jsonrpc`, `id` (string\|number, **not** null), `method`, optional `params` | Bidirectional; IDs MUST NOT reuse within a session |
| **Result** | matching `id`, `result` | Success |
| **Error** | matching `id`, `error.{code,message,data?}` | Protocol failures |
| **Notification** | `method`, optional `params`, **no** `id` | One-way; no response |

### Lifecycle state machine (`2025-11-25`)

```
     ┌────────────┐
     │ DISCONNECTED│
     └──────┬─────┘
            │ open transport
            ▼
     ┌────────────┐     version mismatch → disconnect / error
     │ INITIALIZE │────────────────────────────────────────────┐
     │ client→srv │                                            │
     └──────┬─────┘                                            │
            │ InitializeResult                                 │
            ▼                                                  │
     ┌────────────┐                                            │
     │ INITIALIZED│  notifications/initialized                 │
     └──────┬─────┘                                            │
            │                                                  │
            ▼                                                  ▼
     ┌────────────┐                                     ┌────────────┐
     │ OPERATION  │◄── tools/list, tools/call, … ───────│  FAILED    │
     │ (negotiated│     progress / cancel / errors      │  / CLOSE   │
     │  caps only)│─────────────────────────────────────►└────────────┘
     └──────┬─────┘
            │ transport shutdown
            ▼
     ┌────────────┐
     │  SHUTDOWN  │
     └────────────┘
```

**Version negotiation**: Client proposes (SHOULD be latest it supports). Server echoes if supported, else returns another it supports; client SHOULD disconnect on mismatch ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)).

**Capability table (`2025-11-25`)** ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)):

| Side | Capability | Purpose |
| --- | --- | --- |
| Client | `roots` / `sampling` / `elicitation` / `tasks` | Workspace boundaries; server-requested LLM; user input; task-augmented requests |
| Server | `tools` / `resources` / `prompts` | Core primitives |
| Server | `logging` / `completions` / `tasks` | Logs, autocomplete, tasks |
| Either | `experimental` | Non-standard |

Sub-flags: `listChanged`, `subscribe` ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)).

**`2026-07-28` shift**: Removes `initialize`/`notifications/initialized`; every request carries version + client capabilities in `_meta`; mandatory `server/discover`; sessions/`Mcp-Session-Id` removed; Roots/Sampling/Logging deprecated ([2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).

### Transports

**stdio** ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)):

- Client launches server as subprocess; newline-delimited JSON-RPC on stdin/stdout (no embedded newlines).
- Logging MAY go to stderr; stdout MUST be MCP-only.
- Clients SHOULD support stdio whenever possible (lowest overhead, local-only).

**Streamable HTTP** (replaces deprecated HTTP+SSE from `2024-11-05`) ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)):

- Single MCP endpoint: POST (optional GET for SSE listen).
- Client POST MUST `Accept: application/json, text/event-stream`.
- Response: one JSON object or SSE stream (may include server-initiated messages before final response).
- Security: validate `Origin` (403 on invalid); bind local servers to `127.0.0.1`; authenticate connections.

### Server primitives

| Primitive | Who drives | Discovery / use | Role |
| --- | --- | --- | --- |
| **Tools** | Model-controlled | `tools/list` → `tools/call` | Side-effecting; JSON Schema; optional `outputSchema` / annotations / `execution.taskSupport` |
| **Resources** | Application-controlled | `resources/list`, templates, `resources/read`; optional subscribe | URI-addressed readable context (text/blob); not arbitrary mutation |
| **Prompts** | User-controlled | `prompts/list` → `prompts/get` | Parameterized templates (often slash-command UX) |

Tool names SHOULD be 1–128 chars, case-sensitive, `[A-Za-z0-9_.-]`, unique **per server** ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)). Resource-not-found: **`-32002`** in `2025-11-25` (renumbered **`-32602`** in `2026-07-28`) ([Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)).

### Client features (brief)

- **Sampling** (`sampling/createMessage`): server asks host LLM without embedding provider keys in the server; HITL SHOULD review; **deprecated in `2026-07-28`** ([Sampling](https://modelcontextprotocol.io/specification/2025-11-25/client/sampling); [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).
- **Roots** (`roots/list`): `file://` boundaries; **deprecated in `2026-07-28`**.
- **Elicitation** (`elicitation/create`): form mode (no secrets) vs URL mode (sensitive out-of-band); MRTR in `2026-07-28` (`resultType: "input_required"`) ([Elicitation](https://modelcontextprotocol.io/specification/2025-11-25/client/elicitation)).

### Invariants & complexity

| Invariant | Binding |
| --- | --- |
| Capability gate | Only negotiated features usable after initialize |
| Request ID uniqueness | No reuse within session |
| Tool → model errors | Protocol error vs `isError: true` for recoverable business failure |
| Consent | Explicit user consent before tool invoke |

**Complexity [inferred]**: catalog exposure of \(T\) tools costs \(O(T)\) schema tokens per turn that lists them. Selection accuracy degrades past ~10–15 visible tools (Haiku drops below 90% at 15) ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)). At thousands of tools, retrieval + Top-k is mandatory—naive inlining of 3,616 tools is infeasible; Top-15 cut avg tokens from ~**506k** → ~**21k** while holding ~**81.6%** accuracy ([arXiv:2607.15593](https://arxiv.org/pdf/2607.15593.pdf)).

---

## Part 3 — Token Economics & NFR Analysis

> ⚠️ Gap: MCP is a **protocol**, not a hosted inference product. The official spec publishes **no** RPM/TPM quotas, **no** `$/1k` pricing, and **no** Anthropic-owned end-to-end latency SLAs for `tools/call`. Cost lives in the host LLM + downstream tool I/O. Transport percentiles below are from third-party measurements ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)); e2e agent budgets are labeled **[inferred]**.

### Where tokens (and dollars) are spent

1. Tool/resource/prompt **schemas** inflate every turn that exposes them.
2. Tool **results** / large `resources/read` blobs re-enter context as input tokens.
3. **Sampling** (when used) doubles model spend (nested host LLM call).

**Tool-catalog accuracy vs latency** (ANSYR production logs, N_b=200; not an official MCP SLA) ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)):

| Visible tools | Haiku accuracy | Sonnet accuracy | Notes |
| --- | --- | --- | --- |
| 10 | **91%** @ median **245 ms** | **95%** @ median **410 ms** | Recommend ≤ ~10 tools/context |
| 15 | **87%** (below 90%) | ≥90% | Haiku degrades 10→15 |
| 20–30 | — | ≥90% until ~20; drops by 30 | Prefer scoped aggregation |

### Cost formula — `$` per 1k tool-calls / agent-runs

MCP framing itself is free. Bill the **host model** + optional retrieval gate.

**Assumptions (state explicitly; substitute contract rates):**

| Symbol | Meaning | Example assumption |
| --- | --- | --- |
| \(P_{in}, P_{out}\) | $/1M input / output tokens (primary model) | e.g. mid-tier: $3 / $15 |
| \(S\) | Tool schema tokens exposed per turn | 800 (10 tools × ~80 tok) |
| \(R\) | Avg tool-result tokens appended | 1,200 |
| \(C_{base}\) | Fixed system/prompt tokens (excl. tools) | 2,000 |
| \(N\) | Tool calls per agent run | 4 |
| \(H\) | Prompt-cache hit rate on stable schema prefix | 0.7 |
| \(D_{cache}\) | Discount on cached input (vendor-specific) | 0.1× list input price (illustrative) |
| \(K_{ret}\) | Optional retrieval gate tokens (query+hits) | 500 when \(T \gg 15\) |

**Uncached per-run model token estimate [inferred]:**

\[
T_{in} \approx C_{base} + S + N\cdot\frac{R}{2}\quad\text{(growing context; midpoint approx)}
,\qquad
T_{out} \approx N\cdot 80 + 200
\]

**`$` per 1k agent-runs (primary model only):**

\[
\$_{1k} = 1000 \times \Big(
  \frac{(1-H)\,T_{in}\,P_{in} + H\,T_{in}\,P_{in}\,D_{cache}}{10^{6}}
  + \frac{T_{out}\,P_{out}}{10^{6}}
  + \frac{K_{ret}\,P_{in}}{10^{6}}\Big)
\]

**Worked example [inferred]** with assumptions above (\(T_{in}\approx 2000+800+4\cdot600=5200\), \(T_{out}\approx 520\)):

- Uncached: \(1000\times[(5200\cdot3 + 520\cdot15)/10^6] \approx \$23.40\)
- At \(H=0.7\), \(D_{cache}=0.1\): effective input ≈ \(0.3\cdot5200 + 0.7\cdot5200\cdot0.1 = 1924\) tok → \(1000\times[(1924\cdot3 + 520\cdot15)/10^6] \approx \$13.57\)

Keep catalogs ≤ ~10–15 tools or use retrieval (Alibaba study: Top-15 vs full catalog) to prevent schema explosion ([arXiv:2607.15593](https://arxiv.org/pdf/2607.15593.pdf)). `2026-07-28` adds `ttlMs` / `cacheScope` and deterministic tool order to improve prompt-cache hits ([changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).

### Latency — published transport p50 / p95 / p99

Minimal JSON-RPC echo, N=100 + 10 warm-up (framing only; **not** LLM/tool work) ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)):

| Transport | Method | p50 | p95 | p99 |
| --- | --- | --- | --- | --- |
| stdio (local) | measured | **0.01 ms** | **0.02 ms** | **0.02 ms** |
| Streamable HTTP (loopback) | measured | **0.39 ms** | **0.45 ms** | **0.48 ms** |
| Streamable HTTP (same-region remote) | modeled = loopback + RTT | **~30.4 ms** | **~80.4 ms** | **~180.4 ms** |

Finding: **network RTT dominates**; stdio vs HTTP encoding is irrelevant once a host boundary is crossed.

> ⚠️ Gap: No official Anthropic/OpenAI vendor percentiles for full `tools/call` → downstream SaaS. Below is a labeled **[inferred]** e2e latency budget with arithmetic.

**[inferred] End-to-end `tools/call` latency budget (remote HTTP + SaaS tool)**

| Stage | p50 | p95 | p99 | Notes |
| --- | --- | --- | --- | --- |
| MCP framing (remote) | 30 ms | 80 ms | 180 ms | From table above |
| Host consent / RBAC check | 5 ms | 15 ms | 40 ms | Local policy |
| Downstream tool I/O | 80 ms | 250 ms | 800 ms | Typical SaaS API |
| Result serialize + PII filter | 5 ms | 20 ms | 50 ms | Redaction pass |
| **Sum (budget)** | **120 ms** | **365 ms** | **1070 ms** | Additive worst-case stack |

**Mitigations by tier**

| Tier | Target | Mitigations |
| --- | --- | --- |
| p50 | Keep framing ≪ model time; local ops **&lt;100 ms** server processing heuristic | stdio for desktop; cache `tools/list`; truncate resource reads |
| p95 | Cap remote tool I/O; parallel independent calls | deadlines per call; connection reuse; Top-k tool retrieval (fewer schema tokens → faster model) |
| p99 | Prevent long-tail cascades | hard max timeout + cancel; circuit breaker; degrade to cached/deterministic fallback |

Practitioner UX heuristics (**&lt;100 ms** local / **&lt;500 ms** external) are aspirational, not MCP SLAs ([readfa performance guide](https://readfa.com/blog/mcp-server-performance/) — secondary).

### Throughput & back-pressure

- Spec: **per-request timeouts** + `CancelledNotification`; progress MAY reset soft timers but a **maximum timeout SHOULD remain** ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)).
- Servers MUST **rate-limit** tool invocations ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)).
- No official concurrent-connection or RPS ceiling.

**[inferred] Capacity planning knobs**: concurrent sessions (or RPS after `2026-07-28` stateless), OAuth token-validation QPS (cache introspection), downstream dependency latency—not MCP framing. Back-pressure: reject/queue at host bulkhead → cancel in-flight → open circuit on error budget burn.

### NFR trade-offs

| NFR | Guidance |
| --- | --- |
| **Availability** | Multi-instance HTTP needs session affinity or shared session store under `2025-11-25`; `2026-07-28` stateless favors horizontal scale ([changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)). Bulkhead per upstream server. |
| **RPO / RTO** | MCP does not define backup SLOs. [inferred] RPO = host checkpoint interval for agent state; RTO = re-`initialize` + restore host workflow (Temporal/LangGraph). SSE `Last-Event-ID` resumes streams only—not tool-call history ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)). |
| **Compliance** | No SOC2/HIPAA schema in the protocol; implementor responsibility ([Spec overview](https://modelcontextprotocol.io/specification/2025-11-25)). Sanitize outputs; log tool usage; secrets only via elicitation URL mode ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools); [Elicitation](https://modelcontextprotocol.io/specification/2025-11-25/client/elicitation)). |

### Explicit trade-off: stdio isolation vs remote HTTP reach

| Dimension | stdio (subprocess) | Streamable HTTP (remote) |
| --- | --- | --- |
| Isolation | Strong OS process boundary; local-only blast radius | Shared network surface; DNS rebinding / Origin risks |
| Reach | Same machine / IDE sidecar | Multi-tenant SaaS, cross-VPC tools |
| Latency overhead | ~0.01 ms p50 framing | Remote ≈ RTT (tens–hundreds ms) |
| Auth | Env credentials; SHOULD NOT use OAuth HTTP auth | OAuth 2.1 / Bearer recommended |
| Ops | Process lifecycle + sandbox | LB, TLS, sessions/`subscriptions`, AS discovery |
| Scale-out | Vertical / many local processes | Horizontal (stronger after stateless revision) |

**Decision rule**: default **stdio** for desktop/IDE secrets and lowest latency; **HTTP** when the tool must be shared across hosts/tenants—then Zero-Trust + OAuth audience binding is mandatory ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports); [Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)).

---

## Part 4 — Distributed Resilience & Security

### Durable execution: MCP is not Temporal

MCP defines **transport + session + cancellation** resilience—not workflow replay. Resource subscriptions notify URI updates but do **not** event-source tool calls ([Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)). Tasks (experimental → extension `io.modelcontextprotocol/tasks`) add polling wrappers for long work, still not a full orchestrator ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle); [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).

**How a host adds durability [inferred]:**

| Concern | Host pattern above MCP |
| --- | --- |
| Checkpoints | LangGraph / Temporal / ADK session snapshots after each successful `tools/call` observation |
| Idempotency | Client-generated idempotency key in tool args / `_meta`; server dedupe store; retries only on idempotent reads |
| Replay | Workflow history replays host decisions; re-issues MCP calls with same key; do not assume server session memory survives |
| Dead-letter | Permanent `isError` / poison args → DLQ + human review; do not infinite-retry side effects |
| Locking | Distributed lock around non-idempotent mutations (book/purchase) before `tools/call` |

Disconnect ≠ cancel—clients MUST send explicit cancellation ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)). Prefer explicit handles over implicit session maps (aligned with `2026-07-28`) ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)).

### Failure taxonomy

| Class | Examples | Handling |
| --- | --- | --- |
| **Transient** | Network blip, 429/503, SSE drop | Retry with jitter; SSE `Last-Event-ID` resume (`2025-11-25`); re-issue (`2026-07-28`) |
| **Permanent** | Unsupported protocol version (`-32602` / `-32022`), auth audience fail | Fail closed; do not retry blindly |
| **Business / model-recoverable** | Schema-ok but domain reject → `isError: true` | Return text to model; budgeted model retry |
| **Poison pill** | Repeated identical failing args; hallucinated tool name | Cap retries; quarantine tool; require HITL |
| **Security** | Confused deputy, token passthrough, unsanitized resource → prompt injection | Deny; audit; revoke tokens ([Security best practices](https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices); [arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)) |
| **Cascade** | Nested sampling tool loops; cascading timeouts | Iteration limits; propagate remaining deadline host→MCP→downstream |

### Circuit breaker: closed → open → half-open

Not specified by MCP—implement in the **host** (or gateway) **per server / per tool bulkhead** [inferred]:

```
     success / below threshold
  ┌──────────────────────────────┐
  │                              │
  ▼                              │
┌────────┐  error budget burn  ┌──────┐  probe success  ┌───────────┐
│ CLOSED │────────────────────►│ OPEN │──cooldown──────►│ HALF-OPEN │
└────────┘                     └──────┘                 └─────┬─────┘
  ▲                              ▲                            │
  │                              │         probe failure      │
  └──────────────────────────────┴────────────────────────────┘
         (reject fast while OPEN; limited probes in HALF-OPEN)
```

While OPEN: skip primary MCP server → **fallback chain** (below). Half-open allows a single probe `tools/call` (idempotent preferred).

### Fallback chains

1. **Primary** remote MCP server (full tool set).  
2. **Secondary** regional replica / read-only Resource Gateway.  
3. **Deterministic fallback** — cached last-good `tools/list` + stub “degraded” tool results or human escalation (no invented side effects).

Field patterns: Stateful Session Server, Proxy Aggregator, Tool Orchestrator, Resource Gateway, Domain Adapter—avoid God Tool / Unsanitized Resource Content ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)).

### Zero-Trust MCP

| Control | Requirement |
| --- | --- |
| Consent & privacy | Hosts MUST NOT exfiltrate resource data without consent; tools = arbitrary code ([Spec overview](https://modelcontextprotocol.io/specification/2025-11-25)) |
| Transport auth | stdio: env credentials, no OAuth HTTP; HTTP: OAuth 2.1 resource server + Bearer; RFC 8707 Resource Indicators (`resource`) audience-bind tokens ([Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)) |
| Origin | Streamable HTTP MUST validate `Origin`; prefer `127.0.0.1` for local ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)) |
| Confused deputy | Avoid static shared third-party client IDs + consent cookies on proxies ([Security best practices](https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices)) |
| Token hygiene | No tokens in query strings; validate audience; no token passthrough to upstreams |

`2026-07-28`: validate `iss` (RFC 9207) when present; prefer Client ID Metadata Documents over DCR ([changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).

### Tool RBAC — least privilege

Fine-grained tool RBAC is **not** a first-class MCP object. [inferred] Enforce via:

1. Capability negotiation (coarse feature gate).  
2. OAuth scopes / step-up for dangerous tools.  
3. Host allowlists/denylists per tenant/role.  
4. UI consent before each side-effecting `tools/call`.  
5. Prefer named tools over God Tool `do_anything` ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)).

### PII pipeline: detect → redact → audit

1. **Detect** — scan tool args and results for email/phone/PAN/SSN patterns (and vendor classifiers) before model append or log.  
2. **Redact** — replace with stable tokens (`[PII:email:3f2a]`) so the model retains referential structure without raw secrets.  
3. **Audit** — write immutable event: `{correlation_id, tool, decision: redact|allow|deny, hash(raw), actor, ts}` without storing cleartext when policy forbids.

Elicitation: never request passwords/API keys in form mode—use URL mode only ([Elicitation](https://modelcontextprotocol.io/specification/2025-11-25/client/elicitation)). Spec: sanitize tool outputs; validate inputs; log tool usage ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)).

### Immutable audit logs

Append-only store (WORM object lock / hash-chained log) for: initialize outcomes, capability sets, every `tools/call` (name, arg hash, result hash/`isError`, latency), consent decisions, circuit state transitions, PII redactions. Chain-of-custody for agent decisions sits in the **host** audit stream keyed by correlation ID—not inside MCP framing.

---

## Part 5 — Production Enterprise Code

Runnable Python (stdlib only). No API keys. JSON-RPC-style dispatch with retries + jitter, circuit breaker, fallback, correlation IDs, graceful degradation.

```python
#!/usr/bin/env python3
"""MCP-host-style JSON-RPC tool dispatch with resilience primitives.

Run: python3 this_file.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Structured logging + correlation
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s correlation_id=%(correlation_id)s %(message)s",
)
BASE_LOGGER = logging.getLogger("mcp.host")


def log(correlation_id: str, level: int, msg: str, **extra: Any) -> None:
    BASE_LOGGER.log(
        level,
        "%s | %s",
        msg,
        json.dumps(extra, default=str),
        extra={"correlation_id": correlation_id},
    )


# ---------------------------------------------------------------------------
# Circuit breaker: closed → open → half-open
# ---------------------------------------------------------------------------

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    recovery_timeout_s: float = 2.0
    half_open_successes: int = 1
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    successes_in_half_open: int = 0
    opened_at: float = 0.0

    def allow(self) -> bool:
        if self.state is BreakerState.CLOSED:
            return True
        if self.state is BreakerState.OPEN:
            if time.monotonic() - self.opened_at >= self.recovery_timeout_s:
                self.state = BreakerState.HALF_OPEN
                self.successes_in_half_open = 0
                return True
            return False
        return True  # HALF_OPEN: single probe path controlled by caller

    def on_success(self) -> None:
        if self.state is BreakerState.HALF_OPEN:
            self.successes_in_half_open += 1
            if self.successes_in_half_open >= self.half_open_successes:
                self.state = BreakerState.CLOSED
                self.failures = 0
        else:
            self.failures = 0
            self.state = BreakerState.CLOSED

    def on_failure(self) -> None:
        self.failures += 1
        if self.state is BreakerState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = time.monotonic()


# ---------------------------------------------------------------------------
# PII detect → redact → audit
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
AUDIT_LOG: List[Dict[str, Any]] = []  # append-only in-process stand-in for WORM


def redact_pii(payload: Any, correlation_id: str) -> Any:
    raw = json.dumps(payload)
    redacted = EMAIL_RE.sub(
        lambda m: f"[PII:email:{hashlib.sha256(m.group(0).encode()).hexdigest()[:8]}]",
        raw,
    )
    if redacted != raw:
        AUDIT_LOG.append(
            {
                "correlation_id": correlation_id,
                "event": "pii_redact",
                "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "ts": time.time(),
            }
        )
    return json.loads(redacted)


# ---------------------------------------------------------------------------
# JSON-RPC envelopes
# ---------------------------------------------------------------------------

def rpc_request(method: str, params: Dict[str, Any], req_id: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}


def rpc_result(req_id: str, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def rpc_error(req_id: str, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


# ---------------------------------------------------------------------------
# Fake MCP servers (primary + fallback) — no network, no API keys
# ---------------------------------------------------------------------------

class TransientToolError(Exception):
    pass


class PermanentToolError(Exception):
    pass


class FakeMcpServer:
    """Minimal in-process MCP server: initialize / tools/list / tools/call."""

    def __init__(self, name: str, fail_times: int = 0) -> None:
        self.name = name
        self._remaining_failures = fail_times
        self._initialized = False
        self._tools = {
            "echo": {
                "name": "echo",
                "description": "Echo text",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
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
            },
        }

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        req_id = message.get("id", "unknown")
        method = message.get("method")
        params = message.get("params") or {}

        if method == "initialize":
            self._initialized = True
            return rpc_result(
                req_id,
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": self.name, "version": "0.1.0"},
                },
            )

        if not self._initialized and method != "initialize":
            return rpc_error(req_id, -32000, "not initialized")

        if method == "tools/list":
            return rpc_result(req_id, {"tools": list(self._tools.values())})

        if method == "tools/call":
            return self._tools_call(req_id, params)

        return rpc_error(req_id, -32601, f"Method not found: {method}")

    def _tools_call(self, req_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in self._tools:
            return rpc_error(req_id, -32602, f"Unknown tool: {name}")

        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise TransientToolError(f"{self.name} simulated outage")

        if name == "echo":
            text = args.get("text")
            if not isinstance(text, str):
                return rpc_result(
                    req_id,
                    {"content": [{"type": "text", "text": "text must be string"}], "isError": True},
                )
            return rpc_result(
                req_id,
                {"content": [{"type": "text", "text": text}], "isError": False},
            )

        if name == "add":
            try:
                total = float(args["a"]) + float(args["b"])
            except (KeyError, TypeError, ValueError):
                return rpc_result(
                    req_id,
                    {"content": [{"type": "text", "text": "a and b required numbers"}], "isError": True},
                )
            return rpc_result(
                req_id,
                {
                    "content": [{"type": "text", "text": str(total)}],
                    "structuredContent": {"sum": total},
                    "isError": False,
                },
            )

        raise PermanentToolError(f"unhandled tool {name}")


# ---------------------------------------------------------------------------
# Host dispatcher: retries + jitter, breaker, fallback, degradation
# ---------------------------------------------------------------------------

@dataclass
class HostDispatcher:
    primary: FakeMcpServer
    secondary: FakeMcpServer
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    max_retries: int = 3
    base_backoff_s: float = 0.05
    idempotency_seen: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    cached_tools: Optional[List[Dict[str, Any]]] = None

    def initialize(self, correlation_id: str) -> Dict[str, Any]:
        req_id = str(uuid.uuid4())
        msg = rpc_request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "demo-host", "version": "1.0.0"},
            },
            req_id,
        )
        result = self._send_with_resilience(msg, correlation_id, idempotent=True)
        # Secondary also initialized for failover path
        self.secondary.handle(msg)
        log(correlation_id, logging.INFO, "initialized", server=self.primary.name)
        return result

    def tools_list(self, correlation_id: str) -> List[Dict[str, Any]]:
        req_id = str(uuid.uuid4())
        msg = rpc_request("tools/list", {}, req_id)
        result = self._send_with_resilience(msg, correlation_id, idempotent=True)
        tools = result.get("result", {}).get("tools", [])
        if tools:
            self.cached_tools = tools
        elif self.cached_tools is not None:
            log(correlation_id, logging.WARNING, "tools/list degraded to cache")
            return self.cached_tools
        return tools

    def tools_call(
        self,
        name: str,
        arguments: Dict[str, Any],
        correlation_id: str,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        arguments = redact_pii(arguments, correlation_id)
        if idempotency_key and idempotency_key in self.idempotency_seen:
            log(correlation_id, logging.INFO, "idempotent replay", key=idempotency_key)
            return self.idempotency_seen[idempotency_key]

        req_id = str(uuid.uuid4())
        msg = rpc_request(
            "tools/call",
            {"name": name, "arguments": arguments},
            req_id,
        )
        # reads are idempotent; treat echo/add as safe to retry in this demo
        result = self._send_with_resilience(msg, correlation_id, idempotent=True)
        result = redact_pii(result, correlation_id)

        AUDIT_LOG.append(
            {
                "correlation_id": correlation_id,
                "event": "tools/call",
                "tool": name,
                "args_sha256": hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest(),
                "ok": "error" not in result and not (result.get("result") or {}).get("isError"),
                "breaker": self.breaker.state.value,
                "ts": time.time(),
            }
        )
        if idempotency_key:
            self.idempotency_seen[idempotency_key] = result
        return result

    def _send_with_resilience(
        self,
        message: Dict[str, Any],
        correlation_id: str,
        idempotent: bool,
    ) -> Dict[str, Any]:
        last_exc: Optional[Exception] = None
        attempts = self.max_retries if idempotent else 1

        for attempt in range(attempts):
            if not self.breaker.allow():
                log(
                    correlation_id,
                    logging.WARNING,
                    "circuit open — fallback chain",
                    state=self.breaker.state.value,
                )
                return self._fallback(message, correlation_id)

            server = self.primary
            try:
                resp = server.handle(message)
                if "error" in resp and resp["error"].get("code") == -32000:
                    raise TransientToolError(resp["error"]["message"])
                self.breaker.on_success()
                log(
                    correlation_id,
                    logging.INFO,
                    "rpc ok",
                    method=message["method"],
                    attempt=attempt,
                    server=server.name,
                    breaker=self.breaker.state.value,
                )
                return resp
            except TransientToolError as exc:
                last_exc = exc
                self.breaker.on_failure()
                sleep_s = self.base_backoff_s * (2**attempt) + random.uniform(0, self.base_backoff_s)
                log(
                    correlation_id,
                    logging.WARNING,
                    "transient failure — retry with jitter",
                    attempt=attempt,
                    sleep_s=round(sleep_s, 4),
                    error=str(exc),
                    breaker=self.breaker.state.value,
                )
                time.sleep(sleep_s)
            except PermanentToolError as exc:
                self.breaker.on_failure()
                log(correlation_id, logging.ERROR, "permanent failure", error=str(exc))
                return rpc_error(message["id"], -32603, str(exc))

        log(correlation_id, logging.ERROR, "retries exhausted — fallback", error=str(last_exc))
        return self._fallback(message, correlation_id)

    def _fallback(self, message: Dict[str, Any], correlation_id: str) -> Dict[str, Any]:
        """Fallback chain: secondary server → deterministic degraded result."""
        try:
            # Ensure secondary initialized for tools/call path
            if message["method"] != "initialize":
                init = rpc_request(
                    "initialize",
                    {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "demo-host", "version": "1.0.0"},
                    },
                    str(uuid.uuid4()),
                )
                self.secondary.handle(init)
            resp = self.secondary.handle(message)
            log(correlation_id, logging.INFO, "fallback secondary ok", server=self.secondary.name)
            self.breaker.on_success()
            return resp
        except Exception as exc:  # noqa: BLE001 — deliberate degrade
            log(correlation_id, logging.ERROR, "secondary failed — deterministic degrade", error=str(exc))
            return self._deterministic_degrade(message, correlation_id)

    def _deterministic_degrade(self, message: Dict[str, Any], correlation_id: str) -> Dict[str, Any]:
        method = message["method"]
        req_id = message["id"]
        if method == "tools/list" and self.cached_tools is not None:
            log(correlation_id, logging.WARNING, "graceful degradation: cached tools/list")
            return rpc_result(req_id, {"tools": self.cached_tools, "degraded": True})
        if method == "tools/call":
            log(correlation_id, logging.WARNING, "graceful degradation: stub tool result")
            return rpc_result(
                req_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": "Service degraded. Tool not executed. Escalate to human.",
                        }
                    ],
                    "isError": True,
                    "degraded": True,
                },
            )
        return rpc_error(req_id, -32603, "degraded: no fallback available")


def demo() -> None:
    correlation_id = str(uuid.uuid4())
    primary = FakeMcpServer("primary", fail_times=2)  # first 2 tools/call raise transient
    secondary = FakeMcpServer("secondary", fail_times=0)
    host = HostDispatcher(primary=primary, secondary=secondary)

    host.initialize(correlation_id)
    tools = host.tools_list(correlation_id)
    assert any(t["name"] == "echo" for t in tools)

    # PII in args → redacted before call + audit
    r1 = host.tools_call(
        "echo",
        {"text": "reach me at alice@example.com"},
        correlation_id,
        idempotency_key="echo-1",
    )
    assert "alice@example.com" not in json.dumps(r1)
    assert "[PII:email:" in json.dumps(r1)

    # Retries absorb primary transient failures
    r2 = host.tools_call("add", {"a": 2, "b": 3}, correlation_id, idempotency_key="add-1")
    assert r2["result"]["structuredContent"]["sum"] == 5.0

    # Idempotent replay
    r2b = host.tools_call("add", {"a": 2, "b": 3}, correlation_id, idempotency_key="add-1")
    assert r2b == r2

    print("OK — tools:", [t["name"] for t in tools])
    print("OK — echo result:", r1["result"]["content"][0]["text"])
    print("OK — add sum:", r2["result"]["structuredContent"]["sum"])
    print("OK — audit events:", len(AUDIT_LOG), "breaker:", host.breaker.state.value)


if __name__ == "__main__":
    demo()
```

---

## Part 6 — Architectural System Design Scenarios

### Scenario A — Multi-tenant SaaS “enterprise tool mesh” (3,000+ tools)

**Problem statement**  
Design an MCP-facing layer for a B2B agent platform: **50 tenants**, peak **2k agent runs/min**, each run 2–6 `tools/call`s, catalog of **~3,000** enterprise tools (CRM, ERP, ITSM). Requirement: ≥90% tool-selection accuracy on mid-tier models, p95 e2e tool latency **&lt;500 ms** for read tools in-region, OAuth per tenant, no cross-tenant data bleed. Desktop IDEs and cloud hosts must both connect.

**Proposed architecture**

```
  IDE/Cloud Hosts ──OAuth──► MCP Edge Gateway (Streamable HTTP)
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
              Retrieval gate   Session/AS      Audit WORM
              (Top-15 tools)   token cache     + PII filter
                    │
                    ▼
              Tenant Proxy Aggregator (ns__tool names)
                    │
         ┌──────────┼──────────┐
         ▼          ▼          ▼
      CRM MCP    ERP MCP    ITSM MCP   (Domain Adapters)
```

Technology: Streamable HTTP + OAuth 2.1 (RFC 8707 audience), Redis session/token cache (`2025-11-25`) or prefer stateless handles toward `2026-07-28`, vector/BM25 tool retrieval, per-tenant bulkheads + circuit breakers, host Temporal checkpoints for multi-step mutations.

**Trade-off matrix**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **A1 Inline all tools in prompt** | Very high tokens (~100k–500k+/turn) | Model p50/p95 explode | Simple | Large attack surface in context | Fails past context limits |
| **A2 Retrieval gateway + Top-15 (recommended)** | Low schema tokens (~21k class) | Tool-select median ~hundreds ms; framing ≪ RTT | Medium (index freshness) | Smaller prompt; scope by tenant | Scales to 3k+ tools ([arXiv:2607.15593](https://arxiv.org/pdf/2607.15593.pdf)) |
| **A3 Per-tenant stdio sidecars only** | Low cloud $; high desktop ops | Best framing (~0.01 ms) | Poor multi-tenant | Strong isolation | Cannot share SaaS tools |

**Decision rationale**  
Choose **A2**. Published cloud-scale evidence shows naive inlining is infeasible at thousands of tools while Top-15 preserves accuracy and cuts tokens ([arXiv:2607.15593](https://arxiv.org/pdf/2607.15593.pdf); ≤10–15 visible tools for ≥90% on mid models ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf))). HTTP is required for reach; stdio cannot serve multi-tenant SaaS. Pair with Zero-Trust OAuth and immutable audit.

---

### Scenario B — Regulated desktop coding agent with local secrets

**Problem statement**  
Banking engineering org wants Cursor/VS Code agents to call **local** code-intel + **tickets** without sending source or API keys to a multi-tenant MCP cloud. Peak 200 concurrent developers, local tool p99 **&lt;100 ms** for fs/git reads, SOC2 evidence for every `tools/call`, HITL on writes to prod trackers.

**Proposed architecture**

```
  IDE Host (control plane)
       │ stdio 1:1
       ▼
  Sandboxed local MCP servers (fs, git, linter)
       │
       ├── optional HTTPS MCP (tickets) with user OAuth + always_ask writes
       ├── PII/secret redact before model append
       └── append-only audit log → SIEM
  Host Temporal/LangGraph: checkpoint + idempotency on ticket mutations
```

**Trade-off matrix**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **B1 All-remote HTTP MCP mesh** | Centralized infra $ | RTT-bound (p99 framing alone ~180 ms modeled) | Central ops easier | Larger blast radius; confused-deputy risk | Horizontal scale |
| **B2 stdio-local + selective remote HTTP (recommended)** | Mostly local CPU | Framing ~0.01–0.02 ms p99 local ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)) | Per-machine sandbox updates | Process isolation + env secrets; OAuth only for remote | Per-developer vertical |
| **B3 Single God Tool over SSH** | Cheap to build | Variable | Brittle | Worst least-privilege | Illusion of scale |

**Decision rationale**  
Choose **B2**. Spec and measurements favor stdio for local-only, lowest overhead, env-based credentials ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports); [arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)). Remote HTTP only for systems that must be shared, with consent on writes. Host—not MCP—owns durable checkpoints and idempotency for ticket mutations. Avoid God Tool anti-pattern ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)).

---

### Interview prompts (after both scenarios)

1. Walk `initialize` → `tools/list` → `tools/call` on the Part 1 diagram; where do consent and RBAC sit?
2. Why can MCP framing be 0.01 ms p50 yet your agent still miss a 500 ms p95 SLO?
3. Derive `$` per 1k runs when tool catalog grows from 10 → 100 without retrieval.
4. “MCP is not Temporal”—where do you put checkpoints and idempotency keys?
5. Draw closed → open → half-open for a flapping CRM MCP server; what is the fallback chain?
6. Design Zero-Trust for Streamable HTTP: Origin, audience-bound tokens, confused-deputy mitigations.
7. Spec migration: what breaks when you move a sticky-session fleet from `2025-11-25` to `2026-07-28`?
8. Given Scenario A vs B constraints, when would you deliberately pick the non-recommended row in each matrix?

---

## Source anchors (non-exhaustive)

Research file: `research/04-how-mcp-works.md`. Primary: [MCP architecture](https://modelcontextprotocol.io/docs/2025-11-25/learn/architecture), [Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle), [Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports), [Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools), [Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization), [Security best practices](https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices), [2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog), [Newsletter #110](https://newsletter.systemdesign.one/p/how-mcp-works), [arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf), [arXiv:2607.15593](https://arxiv.org/pdf/2607.15593.pdf).
