# Research: How MCP Works

**Date researched**: 2026-09-29
**Sources consulted**: 18
**Spec versions used**: Primary analysis against **MCP `2025-11-25`** (full primitives: tools/resources/prompts; client features: sampling, roots, elicitation; stateful `initialize` + Streamable HTTP sessions). Cross-checked **`2026-07-28` changelog** (stateless per-request `_meta`, deprecate Roots/Sampling/Logging, Multi Round-Trip Requests, remove `Mcp-Session-Id` / SSE redelivery). Site docs index lists both eras; treat `2025-11-25` as the widely deployed contract and `2026-07-28` as the forward-looking revision.

## 1. System Topology & Mechanics

### Problem framing (N×M integrations)

Anthropic launched MCP in **November 2024** to replace per-application custom integrations between AI apps and data sources ([System Design Newsletter #110](https://newsletter.systemdesign.one/p/how-mcp-works); [MCP intro](https://modelcontextprotocol.io)). Without a shared protocol, **N** AI assistants × **M** data sources require **N×M** bespoke connectors; MCP reduces that to **N clients + M servers** ([System Design Newsletter #110](https://newsletter.systemdesign.one/p/how-mcp-works)). The official site uses the same USB-C analogy: one standard port for AI apps ↔ external systems ([MCP intro](https://modelcontextprotocol.io)).

### Participants: Host / Client / Server

| Role | Responsibility |
| --- | --- |
| **Host** | User-facing LLM application (Claude Desktop/Code, VS Code, Cursor, custom agents). Orchestrates UX, consent, and creates **one MCP client per connected server** ([Architecture overview](https://modelcontextprotocol.io/docs/2025-11-25/learn/architecture); [Spec overview](https://modelcontextprotocol.io/specification/2025-11-25)). |
| **Client** | Protocol connector inside the host. Maintains a **dedicated 1:1 session** with exactly one server; translates host intents into MCP methods (`tools/call`, `resources/read`, etc.) ([Architecture overview](https://modelcontextprotocol.io/docs/2025-11-25/learn/architecture); [System Design Newsletter #110](https://newsletter.systemdesign.one/p/how-mcp-works)). |
| **Server** | Exposes context and capabilities (local filesystem, DB, remote SaaS). Translates MCP into native ops (e.g. `resources/read` → SQL `SELECT`) ([Architecture overview](https://modelcontextprotocol.io/docs/2025-11-25/learn/architecture); [System Design Newsletter #110](https://newsletter.systemdesign.one/p/how-mcp-works)). |

Local **stdio** servers typically serve one client; remote **Streamable HTTP** servers typically serve many clients ([Architecture overview](https://modelcontextprotocol.io/docs/2025-11-25/learn/architecture)).

### Two layers

1. **Data layer**: JSON-RPC 2.0 message semantics — lifecycle, tools/resources/prompts, client features (sampling/elicitation/roots in `2025-11-25`), notifications, utilities ([Architecture overview](https://modelcontextprotocol.io/docs/2025-11-25/learn/architecture); [Base protocol](https://modelcontextprotocol.io/specification/2025-11-25/basic)).
2. **Transport layer**: Connection framing + auth — stdio vs Streamable HTTP (plus optional custom transports) ([Architecture overview](https://modelcontextprotocol.io/docs/2025-11-25/learn/architecture); [Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)).

MCP is inspired by the **Language Server Protocol**: standardize the integration surface so one capability server works across many hosts ([Spec overview](https://modelcontextprotocol.io/specification/2025-11-25)).

### JSON-RPC 2.0 message types

All client↔server messages MUST be JSON-RPC 2.0 and UTF-8 ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports); [Base protocol](https://modelcontextprotocol.io/specification/2025-11-25/basic)):

| Type | Key fields | Notes |
| --- | --- | --- |
| **Request** | `jsonrpc`, `id` (string\|number, **not** null), `method`, optional `params` | Bidirectional (client→server or server→client). Request IDs MUST NOT reuse within a session ([Base protocol](https://modelcontextprotocol.io/specification/2025-11-25/basic)). |
| **Result response** | matching `id`, `result` | Success path |
| **Error response** | matching `id` (when known), `error.{code,message,data?}` | Protocol failures |
| **Notification** | `method`, optional `params`, **no** `id` | One-way; no response ([Base protocol](https://modelcontextprotocol.io/specification/2025-11-25/basic)) |

### Capability negotiation & lifecycle (`2025-11-25`)

Three phases ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)):

1. **Initialization**: Client sends `initialize` with `protocolVersion`, client capabilities, `clientInfo` → server returns negotiated version, server capabilities, `serverInfo` (optional `instructions`) → client sends `notifications/initialized`.
2. **Operation**: Only negotiated capabilities may be used.
3. **Shutdown**: Transport-specific (stdio: close stdin / SIGTERM / SIGKILL; HTTP: close connections). No dedicated shutdown RPC ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)).

**Version negotiation**: Client proposes a version (SHOULD be latest it supports). Server echoes it if supported, else returns another version it supports; client SHOULD disconnect on mismatch ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)). Over HTTP, subsequent requests MUST carry `MCP-Protocol-Version` (e.g. `2025-11-25`); missing header SHOULD be treated as `2025-03-26` for backwards compatibility ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)).

**Capability table (`2025-11-25`)** ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)):

| Side | Capability | Purpose |
| --- | --- | --- |
| Client | `roots` | Expose filesystem boundaries (`roots/list`) |
| Client | `sampling` | Server-requested LLM completions (`sampling/createMessage`) |
| Client | `elicitation` | Server-requested user input (`elicitation/create`; form and/or url) |
| Client | `tasks` | Task-augmented client requests |
| Server | `tools` / `resources` / `prompts` | Core server primitives |
| Server | `logging`, `completions`, `tasks` | Logs, argument autocomplete, tasks |
| Either | `experimental` | Non-standard features |

Sub-flags include `listChanged` (tools/resources/prompts/roots) and `subscribe` (resources) ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)).

**`2026-07-28` shift**: Removes `initialize`/`notifications/initialized`; every request carries version + client capabilities in `_meta`; adds mandatory `server/discover`; sessions/`Mcp-Session-Id` removed ([2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).

### Transports: stdio vs Streamable HTTP

**stdio** ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)):

- Client launches server as a **subprocess**.
- Newline-delimited JSON-RPC on stdin/stdout; messages MUST NOT contain embedded newlines.
- Logging MAY go to stderr; stdout MUST be MCP-only.
- Clients SHOULD support stdio whenever possible (lowest overhead, local-only).

**Streamable HTTP** (replaces deprecated HTTP+SSE from `2024-11-05`) ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)):

- Independent server process; single MCP endpoint supporting **POST** (and optional **GET** for SSE listen).
- Client POST MUST `Accept: application/json, text/event-stream`.
- Request → either one JSON response or SSE stream (may include server-initiated requests/notifications before the final response).
- Optional session: server MAY return `MCP-Session-Id` on `InitializeResult`; client MUST echo it on subsequent requests; DELETE ends session ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)).
- Security: validate `Origin` (403 on invalid); bind local servers to `127.0.0.1`; authenticate connections ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)).

Custom transports are allowed if they preserve JSON-RPC format and lifecycle ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)).

### Server primitives: Tools / Resources / Prompts

Control model ([Architecture overview](https://modelcontextprotocol.io/docs/2025-11-25/learn/architecture); [System Design Newsletter #110](https://newsletter.systemdesign.one/p/how-mcp-works)):

| Primitive | Who drives | Discovery / use | Role |
| --- | --- | --- | --- |
| **Tools** | Model-controlled | `tools/list` → `tools/call` | Side-effecting functions with JSON Schema `inputSchema` (optional `outputSchema`, annotations, `execution.taskSupport`) ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)). |
| **Resources** | Application-controlled | `resources/list`, `resources/templates/list`, `resources/read`; optional subscribe | URI-addressed readable context (text or base64 blob); not for arbitrary mutation ([Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)). |
| **Prompts** | User-controlled | `prompts/list` → `prompts/get` | Parameterized message templates (often slash-command UX) ([Prompts](https://modelcontextprotocol.io/specification/2025-11-25/server/prompts)). |

Dynamic discovery: after connect, the agent learns capabilities at runtime—adding a server tool does not require host code changes ([System Design Newsletter #110](https://newsletter.systemdesign.one/p/how-mcp-works)).

**Tool errors**: JSON-RPC errors for unknown/malformed calls; execution failures return `result` with `isError: true` so the model can self-correct ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)). Tool names SHOULD be 1–128 chars, case-sensitive, `[A-Za-z0-9_.-]`, unique **per server** ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)).

**Resources**: pagination via `cursor`/`nextCursor`; `file://`, `https://`, `git://`, custom URI schemes; resource-not-found error code **`-32002`** in `2025-11-25` (renumbered to **`-32602`** in `2026-07-28`) ([Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources); [2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).

### Client features: Sampling, Roots, Elicitation

**Sampling** (`sampling/createMessage`): Server asks the host LLM for a completion without embedding provider SDKs/API keys in the server. Supports text/image/audio; optional tools + multi-turn tool loop; model preferences via `costPriority` / `speedPriority` / `intelligencePriority` (0–1) plus advisory `hints` ([Sampling](https://modelcontextprotocol.io/specification/2025-11-25/client/sampling)). Human-in-the-loop SHOULD review prompts/responses ([Sampling](https://modelcontextprotocol.io/specification/2025-11-25/client/sampling)). **Deprecated in `2026-07-28`**—migrate to direct LLM provider APIs ([2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).

**Roots** (`roots/list`): Client exposes `file://` workspace boundaries; optional `notifications/roots/list_changed` ([Roots](https://modelcontextprotocol.io/specification/2025-11-25/client/roots)). **Deprecated in `2026-07-28`**—pass paths via tool args / resource URIs / config instead ([2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).

**Elicitation** (`elicitation/create`): Server requests user input mid-flow ([Elicitation](https://modelcontextprotocol.io/specification/2025-11-25/client/elicitation)):

- **Form mode**: flat JSON Schema of primitives (string/number/boolean/enum); MUST NOT request passwords/API keys/tokens/payment credentials.
- **URL mode**: out-of-band navigation for sensitive flows; only the URL is exposed to the MCP client.

Remains in current and `2026-07-28` specs, but delivery moves to **Multi Round-Trip Requests**: server returns `resultType: "input_required"` + `inputRequests`; client retries with `inputResponses` ([2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog); [Spec latest overview](https://modelcontextprotocol.io/specification/latest)).

### Tool dispatch path (host → model → MCP)

[inferred] Host builds the turn → model emits tool call → host MCP client issues `tools/call` → server executes → result (`content` / `structuredContent`) returns to host → appended to model context. Spec mandates human consent for tool invocation and treats tool annotations as untrusted unless the server is trusted ([Spec overview](https://modelcontextprotocol.io/specification/2025-11-25); [Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)).

## 2. Token Economics & NFR Metrics

> ⚠️ Limited public data available for this dimension. MCP is a **protocol**, not a hosted inference product: the official spec publishes **no** RPM/TPM quotas, **no** `$/1k` pricing, and **no** Anthropic-owned latency SLAs for `tools/call`. Numbers below are either protocol-overhead measurements from third-party papers, model-side tool-selection costs, or `[inferred]` engineering bounds—not official MCP product benchmarks.

### Where tokens are spent

MCP itself does **not** bill tokens. Cost lives in the host’s LLM usage:

- Tool/resource/prompt **schemas and descriptions** inflate the system/tool prompt on every turn that exposes them ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools); [inferred] from host tool-calling practice).
- Tool **results** re-enter context as observations; large `resources/read` blobs dominate input tokens ([Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)).
- **Sampling** (when used) doubles model spend: host LLM call nested inside a server-driven flow ([Sampling](https://modelcontextprotocol.io/specification/2025-11-25/client/sampling)).

**Tool-catalog scale vs accuracy/latency** (ANSYR production logs, N_b=200 per bucket; not an official MCP spec number) ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)):

| Visible tools | Haiku accuracy | Sonnet accuracy | Notes |
| --- | --- | --- | --- |
| 10 | **91%** @ median **245 ms** | **95%** @ median **410 ms** | Recommended ≤ ~10 tools per context |
| 15 | **87%** (below 90% threshold) | ≥90% | Haiku degrades 10→15 |
| 20–30 | — | ≥90% until ~20; drops by 30 | Prefer scoped aggregation |

Cloud-scale tool access study (Alibaba catalog up to **3,616** tools): naive “put all tools in prompt” becomes infeasible (≥752 tools exceeds context); hybrid retrieval + Top-15 selection keeps accuracy (e.g. **81.6%** at 3,616) while cutting tokens vs baseline inlining (baseline ~**506k** tokens avg vs Top-15 ~**21k**) ([arXiv:2607.15593](https://arxiv.org/pdf/2607.15593.pdf)).

### Protocol / transport latency (overhead only)

Minimal JSON-RPC echo, N=100 + 10 warm-up, isolating framing from LLM/tool work ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)):

| Transport | Method | p50 | p95 | p99 |
| --- | --- | --- | --- | --- |
| stdio (local) | measured | **0.01 ms** | **0.02 ms** | **0.02 ms** |
| Streamable HTTP (loopback) | measured | **0.39 ms** | **0.45 ms** | **0.48 ms** |
| Streamable HTTP (same-region remote) | modeled = loopback + RTT | **~30.4 ms** | **~80.4 ms** | **~180.4 ms** |

Finding: **network RTT dominates**; stdio vs HTTP encoding is irrelevant once a host boundary is crossed ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)).

[inferred] End-to-end agent latency is typically **model inference (hundreds of ms–seconds) + downstream tool I/O**, not MCP framing. Practitioner guides suggest targeting **&lt;100 ms** server processing for local ops and **&lt;500 ms** when calling external services—these are UX heuristics, not published MCP SLAs ([readfa MCP performance guide](https://readfa.com/blog/mcp-server-performance/) — secondary; treat as aspirational).

### Caching & prompt-cache interaction

- `2025-11-25`: `listChanged` notifications reduce blind re-polling of `tools/list` / `resources/list` / `prompts/list` ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools); [Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)).
- `2026-07-28`: list/read results SHOULD include `ttlMs` + `cacheScope` (`public`|`private`); servers SHOULD return tools in **deterministic order** to improve LLM prompt-cache hit rates ([2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).

No published official hit-rate or TTL defaults.

### Throughput / back-pressure

Spec requires **per-request timeouts** and `CancelledNotification` on expiry; progress notifications MAY reset timers but a maximum timeout SHOULD remain ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)). Tools spec: servers MUST rate-limit tool invocations ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)). No official concurrent-connection or RPS ceiling.

## 3. Distributed Resilience & State

> ⚠️ Limited public data available for this dimension. MCP does **not** prescribe Temporal/Kafka/event-sourced orchestrators. Resilience is defined at **transport + session + cancellation** layers; durable agent workflows remain a host concern. Mark production orchestration patterns as `[inferred]` unless cited from the protocol.

### Stateful session model (`2025-11-25`)

- Spec describes **stateful connections** with initialize handshake ([Spec overview](https://modelcontextprotocol.io/specification/2025-11-25)).
- Streamable HTTP MAY mint cryptographically strong `MCP-Session-Id` (visible ASCII 0x21–0x7E); missing/expired session → **400** / **404**; client MUST re-`initialize` after 404 ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)).
- SSE streams MAY be **resumable** via event `id` + client `Last-Event-ID` on GET ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)).
- Disconnect ≠ cancel; clients MUST send explicit cancellation ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)).

### Stateless revision (`2026-07-28`)

Removes protocol sessions, GET listen endpoint, and SSE redelivery. Cross-call state → **server-minted handles** as ordinary tool arguments. Broken response stream → client **re-issues** with a new request id. Server→client change fan-out moves to `subscriptions/listen` ([2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).

[inferred] Stateless HTTP favors horizontal scale (no sticky sessions); stateful `2025-11-25` sessions need affinity or shared session store if multi-instance.

### Progress, cancellation, tasks

- Utilities: progress tracking, cancellation, error reporting ([Spec overview](https://modelcontextprotocol.io/specification/2025-11-25)).
- **Tasks** (experimental in `2025-11-25`): durable wrappers for long-running work; moved to official extension `io.modelcontextprotocol/tasks` in `2026-07-28` with polling via `tasks/get` ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle); [2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).

### Circuit breakers / distributed locking

Not specified by MCP. [inferred] Hosts should apply bulkheads per MCP server (isolate slow remote tools), deadlines per `tools/call`, and retries only on idempotent reads—tool side effects are not automatically idempotent.

### Checkpointing / replay

Resource subscriptions notify URI updates (`notifications/resources/updated`) but do not provide event-sourcing replay of tool calls ([Resources](https://modelcontextprotocol.io/specification/2025-11-25/server/resources)). [inferred] Agent checkpointing (LangGraph/Temporal) sits **above** MCP in the host.

### Architectural patterns for resilience (field)

Industry pattern catalog (15-server derivation corpus) names **Stateful Session Server**, **Proxy Aggregator**, **Tool Orchestrator**, **Resource Gateway**, **Domain-Specific Adapter**—and anti-patterns like God Tool / Unsanitized Resource Content ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)).

## 4. Enterprise Security & Governance

### Trust principles (protocol-level)

Spec mandates user **consent and control**, **data privacy** (hosts MUST NOT exfiltrate resource data without consent), **tool safety** (tools = arbitrary code execution; annotations untrusted unless server trusted; explicit user consent before invoke), and (in `2025-11-25`) **LLM sampling controls** (approve sampling, edit prompts, limit what servers see) ([Spec overview](https://modelcontextprotocol.io/specification/2025-11-25)).

### Transport security

| Transport | Auth guidance |
| --- | --- |
| **stdio** | SHOULD **NOT** use OAuth HTTP auth; pull credentials from the **environment** ([Base protocol — Auth](https://modelcontextprotocol.io/specification/2025-11-25/basic); [Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)). |
| **HTTP** | SHOULD conform to MCP Authorization (OAuth 2.1); Bearer tokens, API keys, custom headers possible; OAuth recommended ([Architecture overview](https://modelcontextprotocol.io/docs/2025-11-25/learn/architecture); [Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)). |

Streamable HTTP MUST validate `Origin` against DNS rebinding; prefer localhost bind for local servers ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)).

### OAuth 2.1 authorization (HTTP)

Authorization is **OPTIONAL** but when used on HTTP ([Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)):

- MCP server = OAuth **resource server**; MCP client = OAuth **client**; separate or colocated **authorization server**.
- Based on OAuth 2.1 draft, RFC8414 (AS metadata), RFC7591 (DCR), RFC9728 (Protected Resource Metadata), Client ID Metadata Documents.
- Discovery: `401` + `WWW-Authenticate` with `resource_metadata` and optional `scope`, or `/.well-known/oauth-protected-resource[...]`.
- Clients MUST use **RFC 8707 Resource Indicators** (`resource` parameter) so tokens are audience-bound to the MCP server URI.
- Access tokens via `Authorization: Bearer`; MUST NOT place tokens in query strings; servers MUST validate audience.
- PKCE in the authorization code flow (diagrammed in the authorization sequence).
- Scope strategy: prefer challenged `scope`, else `scopes_supported`, least privilege / step-up for increments.

`2026-07-28` hardens auth further: clients MUST validate `iss` (RFC 9207) when present; DCR deprecated in favor of Client ID Metadata Documents; credentials keyed by issuer ([2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).

### Capability negotiation as policy surface

Only features advertised in `initialize` (or per-request `_meta` in `2026-07-28`) are usable—capability negotiation is the coarse RBAC/feature gate ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)). Fine-grained tool RBAC is **not** a first-class MCP object; [inferred] hosts/servers enforce via OAuth scopes, allowlists, and UI consent.

### Attack catalog (official)

Security best practices cover **confused deputy** (MCP proxy + static third-party client ID + consent cookies), token passthrough, session hijacking, and related mitigations ([Security best practices](https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices)).

### PII / sandbox / audit

- Spec: sanitize tool outputs; validate inputs; log tool usage for audit ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)).
- Elicitation: secrets only via URL mode ([Elicitation](https://modelcontextprotocol.io/specification/2025-11-25/client/elicitation)).
- Icon fetching: no credentials, HTTPS/`data:` only, same-origin preference ([Base protocol](https://modelcontextprotocol.io/specification/2025-11-25/basic)).
- No mandated WASM/container sandbox in the protocol; [inferred] hosts sandbox stdio servers via OS process isolation or containers.
- No SOC2/HIPAA schema prescribed; compliance is implementor responsibility ([Spec overview](https://modelcontextprotocol.io/specification/2025-11-25)).

## 5. Production Failure Modes

### Capability / version mismatch

Unsupported protocol version → JSON-RPC error (example `code: -32602`, `data.supported` / `data.requested`) or disconnect ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)). `2026-07-28` adds `UnsupportedProtocolVersionError` (`-32022` after renumber) ([2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).

### Hallucinated / invalid tool parameters

- Unknown tool / schema-invalid call → protocol error (e.g. `-32602`) ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)).
- Business validation failures → `isError: true` with actionable text for model retry ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)).
- Clients SHOULD show args to users before call to prevent exfiltration ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)).

### Infinite / nested loops

Sampling with tools: every `tool_use` MUST be matched by `tool_result`; parties SHOULD enforce **iteration limits** on tool loops ([Sampling](https://modelcontextprotocol.io/specification/2025-11-25/client/sampling)). [inferred] Host max-turn caps remain necessary for outer agent loops that keep calling MCP tools.

### Context window degradation

Large tool catalogs and resource blobs consume context; accuracy drops as tool count rises past ~10–15 visible tools ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)). Mitigation: scoped Proxy Aggregator / retrieval-over-tools ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf); [arXiv:2607.15593](https://arxiv.org/pdf/2607.15593.pdf)).

### Cascading timeouts

Spec: timeouts + cancellation notifications; progress may extend soft deadlines but max timeout SHOULD apply ([Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)). Clients SHOULD timeout tool calls ([Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)). [inferred] Propagate remaining deadline from host→MCP→downstream API.

### Session / stream loss

`2025-11-25`: 404 on dead session → full re-init; SSE may resume with `Last-Event-ID` ([Transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)). `2026-07-28`: no resume—re-issue request ([2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).

### State drift

Stateful Session anti-pattern / pattern: implicit server-side session maps can diverge from LLM beliefs after retries ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)). Prefer explicit handles (aligned with `2026-07-28` guidance).

### Security incidents / confused deputy

Documented attack class when MCP proxies third-party OAuth with shared static client IDs ([Security best practices](https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices)). Unsanitized resource content enables prompt injection into the host model ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)).

### God tools

Undifferentiated `do_anything(action, params)` collapses selection accuracy—decompose into named tools ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)).

> ⚠️ Limited public data: no large Anthropic/OpenAI **post-mortems** exclusively about MCP outages were found in this research pass; failure modes above are derived from the specification’s MUST/SHOULD requirements and peer-reviewed field studies.

## 6. Enterprise System Design Scenarios

### Design goals

- **Build once, connect many**: one MCP server works across Claude, ChatGPT, VS Code, Cursor, and other compliant hosts ([MCP intro](https://modelcontextprotocol.io); [System Design Newsletter #110](https://newsletter.systemdesign.one/p/how-mcp-works)).
- Decouple **intelligence** (model/host) from **data/tools** (servers) so model swaps do not rewrite integrations ([System Design Newsletter #110](https://newsletter.systemdesign.one/p/how-mcp-works)).

### Transport choice matrix

| Criterion | stdio | Streamable HTTP |
| --- | --- | --- |
| Latency overhead | ~0.01 ms p50 framing ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)) | Loopback ~0.4 ms; remote ≈ network RTT |
| Multi-tenant / remote | Poor fit (subprocess per user/machine) | Required |
| Auth | Env credentials | OAuth 2.1 / Bearer |
| Ops complexity | Process lifecycle, sandboxing | LB, TLS, sessions/`subscriptions`, AS discovery |
| Scale-out | Vertical / many local processes | Horizontal (stronger after `2026-07-28` stateless) |

### Pattern trade-offs (field catalog)

| Pattern | Best when | Cost / risk |
| --- | --- | --- |
| Resource Gateway | Read-heavy context (docs, schemas) | Extra hop; join awkwardness ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)) |
| Tool Orchestrator | Multi-system workflows as one tool | Less reusable sub-steps; partial failure inside composite |
| Stateful Session Server | Multi-step edits needing server memory | Drift / affinity; conflicts with stateless revision |
| Proxy Aggregator | Many upstream MCP servers | Tool-name collisions → namespace (`ns__tool`); static merge blows tool budget |
| Domain Adapter | Ugly third-party APIs | Adapter churn on API changes |

### Capacity planning signals (published / inferred)

- Keep **≤ ~10–15 tools** visible per LLM turn for ≥90% selection accuracy on mid/small models; use retrieval beyond that ([arXiv:2606.30317](https://arxiv.org/pdf/2606.30317v1.pdf)).
- At **thousands** of enterprise tools, retrieval gateways are mandatory ([arXiv:2607.15593](https://arxiv.org/pdf/2607.15593.pdf)).
- [inferred] Capacity plan on: concurrent MCP sessions (or RPS after stateless), AS token-validation QPS (cache introspection), and downstream tool dependency latency—not MCP framing.

### Spec migration planning

Enterprises on `2025-11-25` should track: session removal, MRTR for elicitation/sampling-like flows, Roots/Sampling/Logging deprecation (≥12-month window per lifecycle policy), and mandatory `Mcp-Method`/`Mcp-Name` headers ([2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)).

> ⚠️ Limited public data: few vendor-published multi-tenant “MCP cluster” capacity numbers (tokens/sec, concurrent agents, memory footprints) from Anthropic itself; design guidance leans on the protocol + independent papers cited above.

## Sources

- [1] https://newsletter.systemdesign.one/p/how-mcp-works — System Design Newsletter #110 “How MCP Works” (Eric Roby / Neo Kim, Dec 26, 2025); primary narrative on N×M, host/client/server, primitives (paid article; public excerpt used).
- [2] https://modelcontextprotocol.io — Official MCP homepage / USB-C framing / ecosystem.
- [3] https://modelcontextprotocol.io/specification/2025-11-25 — Spec index `2025-11-25` (stateful connections; sampling/roots/elicitation).
- [4] https://modelcontextprotocol.io/specification/latest — Spec “latest” landing (features reflect forward revision / elicitation emphasis).
- [5] https://modelcontextprotocol.io/docs/2025-11-25/learn/architecture — Architecture overview (participants, layers, primitives).
- [6] https://modelcontextprotocol.io/specification/2025-11-25/basic — Base protocol (JSON-RPC messages, auth split stdio vs HTTP, `_meta`, icons).
- [7] https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle — Initialize, capability negotiation, timeouts, shutdown.
- [8] https://modelcontextprotocol.io/specification/2025-11-25/basic/transports — stdio + Streamable HTTP, sessions, SSE, Origin checks.
- [9] https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization — OAuth 2.1, RFC9728 discovery, Resource Indicators, Bearer.
- [10] https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices — Confused deputy and related MCP attack mitigations.
- [11] https://modelcontextprotocol.io/specification/2025-11-25/server/tools — `tools/list`, `tools/call`, schemas, `isError`.
- [12] https://modelcontextprotocol.io/specification/2025-11-25/server/resources — Resources, templates, subscribe, URI schemes.
- [13] https://modelcontextprotocol.io/specification/2025-11-25/server/prompts — Prompts list/get, user-controlled templates.
- [14] https://modelcontextprotocol.io/specification/2025-11-25/client/sampling — `sampling/createMessage`, model preferences, tool loops.
- [15] https://modelcontextprotocol.io/specification/2025-11-25/client/roots — `roots/list`, `file://` boundaries.
- [16] https://modelcontextprotocol.io/specification/2025-11-25/client/elicitation — Form vs URL elicitation.
- [17] https://modelcontextprotocol.io/specification/2026-07-28/changelog — Stateless MCP, MRTR, deprecations, caching headers.
- [18] https://arxiv.org/pdf/2606.30317v1.pdf — MCP server architecture patterns + measured/modeled transport latency + tool-count accuracy (Celabe/ANSYR + registry corpus).

**Additional quantitative reference (not counted in primary 18 if deduped for interviews):** https://arxiv.org/pdf/2607.15593.pdf — Cloud-scale tool retrieval (3,616 tools, token/latency tables).
