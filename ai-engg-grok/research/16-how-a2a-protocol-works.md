# Research: How A2A Protocol Works

**Date researched**: 2026-09-30
**Sources consulted**: 14
**Spec version used**: Primary analysis against **A2A Protocol `1.0.0`** (Latest Released Version on [a2a-protocol.org/latest/specification](https://a2a-protocol.org/latest/specification/); normative source of truth is `spec/a2a.proto`). Prior versions noted for history: `0.1.0`, `0.2.6`, `0.3.0`. Protocol version negotiation uses `Major.Minor` only (e.g. `"1.0"`); patch numbers do not affect wire compatibility ([A2A Spec §3.6](https://a2a-protocol.org/latest/specification/)).

## 1. System Topology & Mechanics

### Problem framing and stewardship

Without a shared agent-to-agent contract, every cross-framework or cross-vendor handoff is custom glue: capability discovery, auth, task handoff, status, context, and result delivery ([System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol)). Google launched Agent2Agent (A2A) on **9 April 2025** with **>50** technology partners (Atlassian, Box, Cohere, Intuit, LangChain, MongoDB, PayPal, Salesforce, SAP, ServiceNow, UKG, Workday, plus major GSIs) ([Google Developers Blog — Announcing A2A](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/); [Google Cloud Next partners blog](https://cloud.google.com/blog/topics/partners/best-agentic-ecosystem-helping-partners-build-ai-agents-next25)). Google donated A2A to the **Linux Foundation** on **23 June 2025**; the project reported **>100** supporting companies at donation time ([Linux Foundation press](https://www.linuxfoundation.org/press/linux-foundation-launches-the-agent2agent-protocol-project-to-enable-secure-intelligent-communication-between-ai-agents); [Google Cloud donation post](https://developers.googleblog.com/google-cloud-donates-a2a-to-linux-foundation/)). Stewardship today: Technical Steering Committee with AWS, Cisco, Google, IBM Research, Microsoft, Salesforce, SAP, and ServiceNow ([A2A Protocol home](https://a2a-protocol.org/latest/); [Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/)). **v1.0** is the first stable, production-ready release ([Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/)).

### Boundary with MCP (tools vs agents — not a re-teach)

| Layer | Standard | Role |
| --- | --- | --- |
| Agent ↔ tools / context | **MCP** | Equip one agent with tools, APIs, resources |
| Agent ↔ agent | **A2A** | Discover peers, delegate tasks, exchange results across frameworks/vendors |

Official framing: use MCP inside an agent; use A2A between agents ([A2A Protocol home](https://a2a-protocol.org/latest/); [Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/); [Google A2A launch](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/); [System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol)). A2A deliberately treats remote agents as **opaque** collaborators (skills + exchanged information), not as tools whose internals must be shared ([A2A Spec §1.2 Opaque Execution](https://a2a-protocol.org/latest/specification/); [Google A2A launch — design principles](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/)).

### Three-layer architecture

```
Layer 1 — Data model (proto): Task, Message, AgentCard, Part, Artifact, Extension
Layer 2 — Abstract ops: SendMessage, SendStreamingMessage, GetTask, ListTasks,
                        CancelTask, SubscribeToTask, push-config CRUD, GetExtendedAgentCard
Layer 3 — Bindings: JSON-RPC | gRPC | HTTP+JSON/REST | custom
```

`spec/a2a.proto` is the **single authoritative normative** definition; generated JSON Schema is non-normative ([A2A Spec §1.4](https://a2a-protocol.org/latest/specification/)).

### Roles and discovery

- **A2A Client**: initiates requests (user app or another agent).
- **A2A Server (remote agent)**: exposes an A2A-compliant endpoint, processes tasks, returns results.
- Any agent may play both roles in different workflows ([A2A Spec §2.2](https://a2a-protocol.org/latest/specification/); [System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol)).

**Agent Card** (JSON manifest) is published by every A2A server. Discovery mechanisms ([A2A Spec §8.2](https://a2a-protocol.org/latest/specification/)):

1. Well-known URI: `https://{server_domain}/.well-known/agent-card.json` (RFC 8615 pattern)
2. Registries / catalogs
3. Direct / preconfigured URL or content

Required / key Agent Card fields ([A2A Spec §4.4.1](https://a2a-protocol.org/latest/specification/)): `name`, `description`, `version`, `supportedInterfaces[]` (url + `protocolBinding` ∈ {`JSONRPC`,`GRPC`,`HTTP+JSON`} + `protocolVersion` + optional `tenant`), `capabilities` (`streaming`, `pushNotifications`, `extendedAgentCard`, `extensions`), `defaultInputModes` / `defaultOutputModes` (MIME types), `skills[]` (`id`, `name`, `description`, `tags`, optional `examples`, per-skill modes/security), optional `securitySchemes` / `securityRequirements`, optional JWS `signatures[]`.

**Extended Agent Card**: if `capabilities.extendedAgentCard: true`, authenticated clients call `GetExtendedAgentCard` (`GET /extendedAgentCard`) and SHOULD replace the cached public card for the session ([A2A Spec §3.1.11, §13.3](https://a2a-protocol.org/latest/specification/)).

**Multi-tenancy**: `AgentInterface.tenant` is an opaque routing string; when set, clients MUST echo it on every request ([A2A Spec §4.4.6](https://a2a-protocol.org/latest/specification/); [Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/)).

### Tasks, messages, parts, artifacts, contexts

| Concept | Role |
| --- | --- |
| **Task** | Stateful unit of work; server-generated `id`; `status`, optional `artifacts[]`, `history[]`, `contextId`, `metadata` ([A2A Spec §4.1.1](https://a2a-protocol.org/latest/specification/)) |
| **Message** | Communication turn: `messageId`, `role` (`ROLE_USER` \| `ROLE_AGENT`), `parts[]`, optional `taskId` / `contextId` / `referenceTaskIds` ([A2A Spec §4.1.4](https://a2a-protocol.org/latest/specification/)) |
| **Part** | Smallest content unit — exactly one of `text` \| `raw` (bytes/base64) \| `url` \| `data` (JSON); optional `mediaType`, `filename` ([A2A Spec §4.1.6](https://a2a-protocol.org/latest/specification/)) |
| **Artifact** | Task **output** deliverable: `artifactId`, `parts[]`, optional name/description ([A2A Spec §4.1.7](https://a2a-protocol.org/latest/specification/)) |
| **contextId** | Groups related tasks/messages (conversation/session continuity) ([A2A Spec §3.4.1](https://a2a-protocol.org/latest/specification/)) |

Spec guidance: Messages = communication; Artifacts = results. Messages SHOULD NOT be used as the primary result channel ([A2A Spec §3.7](https://a2a-protocol.org/latest/specification/)).

**TaskState lifecycle** ([A2A Spec §4.1.3](https://a2a-protocol.org/latest/specification/)):

| State | Class |
| --- | --- |
| `TASK_STATE_SUBMITTED` | Non-terminal — acknowledged |
| `TASK_STATE_WORKING` | Non-terminal — processing |
| `TASK_STATE_INPUT_REQUIRED` | Interrupted — needs client input |
| `TASK_STATE_AUTH_REQUIRED` | Interrupted — needs auth/credential |
| `TASK_STATE_COMPLETED` | Terminal — success |
| `TASK_STATE_FAILED` | Terminal — error |
| `TASK_STATE_CANCELED` | Terminal — client cancel |
| `TASK_STATE_REJECTED` | Terminal — agent declines |
| `TASK_STATE_UNSPECIFIED` | Unknown / recovery |

Terminal states reject further messages (`UnsupportedOperationError`) ([A2A Spec §3.1.1](https://a2a-protocol.org/latest/specification/)).

`SendMessage` may return a **Task** (async work) or a direct **Message** (simple interactions). Blocking default: wait until terminal or interrupted (`INPUT_REQUIRED` / `AUTH_REQUIRED`) unless `return_immediately: true` ([A2A Spec §3.2.2](https://a2a-protocol.org/latest/specification/)).

### Interaction patterns and bindings

Three update mechanisms ([A2A Spec §3.5](https://a2a-protocol.org/latest/specification/); [Streaming & async guide](https://a2a-protocol.org/dev/topics/streaming-and-async/)):

1. **Polling** — `GetTask`
2. **Streaming (SSE)** — `SendStreamingMessage` / `SubscribeToTask` when `capabilities.streaming: true`
3. **Push (webhooks)** — push-config CRUD when `capabilities.pushNotifications: true`; webhook timeout recommended **10–30 s**; at-least-once delivery; clients MUST ack with HTTP 2xx and SHOULD be idempotent

Streaming patterns ([A2A Spec §3.1.2](https://a2a-protocol.org/latest/specification/)):

- Message-only stream: one `Message`, then close
- Task lifecycle stream: initial `Task`, then `TaskStatusUpdateEvent` / `TaskArtifactUpdateEvent` (`append` / `lastChunk` for chunked artifacts); close on terminal state

Method mapping (same semantics across bindings) ([A2A Spec §5.3](https://a2a-protocol.org/latest/specification/)):

| Op | JSON-RPC / gRPC | REST |
| --- | --- | --- |
| Send | `SendMessage` | `POST /message:send` |
| Stream | `SendStreamingMessage` | `POST /message:stream` |
| Get / List / Cancel | `GetTask` / `ListTasks` / `CancelTask` | `GET /tasks/{id}`, `GET /tasks`, `POST /tasks/{id}:cancel` |
| Subscribe | `SubscribeToTask` | `POST /tasks/{id}:subscribe` |

Version header: clients MUST send `A2A-Version: 1.0` (empty header interpreted as **0.3**) ([A2A Spec §3.6](https://a2a-protocol.org/latest/specification/)). Agent Cards can advertise both `0.3` and `1.0` interfaces for progressive migration ([Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/)).

### Orchestration topology (protocol-agnostic)

A2A is a **wire protocol**, not an orchestration model. Common deployment patterns ([System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol/)):

- **Centralized orchestrator** — one lead agent discovers specialists, delegates, stitches results (default enterprise pattern).
- **Decentralized swarm** — peers discover and hand off without a single controller (higher flexibility, harder tracing).

Five guiding principles: Simple (HTTP/JSON-RPC/SSE), Enterprise Ready, Async First, Modality Agnostic, Opaque Execution ([A2A Spec §1.2](https://a2a-protocol.org/latest/specification/); [Google A2A launch](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/); [System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol)).

## 2. Token Economics & NFR Metrics

> ⚠️ Limited public data available for this dimension. A2A is an interoperability protocol, not a model API. There are **no official published p50/p95/p99 latency SLAs, token-cost formulas, RPM/TPM quotas, or prompt-cache hit rates** for A2A itself in the v1.0 specification or steward announcements. Numbers below are architectural inferences or secondary commentary — not vendor benchmarks.

### What the protocol costs (vs model tokens)

A2A traffic is **control/coordination overhead** on top of whatever LLMs each agent already runs. Per remote delegation, a client typically pays ([inferred] from Task + Message + optional stream events; [A2A Spec §3.1, §3.5](https://a2a-protocol.org/latest/specification/)):

1. Agent Card fetch (cacheable; well-known GET)
2. Authenticated `SendMessage` / stream open
3. Zero or more status/artifact events or poll cycles
4. Final artifact retrieval / history fetch (`historyLength` can shrink payloads; `includeArtifacts` defaults **false** on List Tasks to reduce size) ([A2A Spec §3.1.4, §3.2.4](https://a2a-protocol.org/latest/specification/))

`[inferred]` Relative overhead: sync short tasks ≈ 1–2 RTTs after discovery; long research tasks shift cost to **LLM time inside the remote agent**, not A2A framing. Nested A2A (agent A → B → C) multiplies coordination RTTs and context duplication risk ([System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol) production-breakdown framing).

### Latency shape (architectural, not measured)

| Path | Expected latency character | Source |
| --- | --- | --- |
| Blocking `SendMessage` (short task) | Dominated by remote agent LLM + tools; connection held until terminal/interrupted | [A2A Spec §3.2.2](https://a2a-protocol.org/latest/specification/) |
| Streaming SSE | Lower *perceived* latency; first `Task`/`Message` then incremental events | [Streaming guide](https://a2a-protocol.org/dev/topics/streaming-and-async/) |
| Push + GetTask | Best for minutes–days; webhook recommended timeout **10–30 s**; client may poll after notify | [A2A Spec §4.3.3](https://a2a-protocol.org/latest/specification/) |
| Polling | Higher latency + wasteful requests; firewall-friendly | [A2A Spec §3.5.1](https://a2a-protocol.org/latest/specification/) |

Binding choice: gRPC for high-throughput internal meshes; JSON-RPC/HTTP+JSON for gateway/LB familiarity ([Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/); [System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol); gRPC added in **0.3** era ([Google Cloud A2A upgrade](https://cloud.google.com/blog/products/ai-machine-learning/agent2agent-protocol-is-getting-an-upgrade))).

### Throughput, caching, routing

- **Rate limits**: Spec says agents SHOULD rate-limit all ops and MAY tier by user; no numeric defaults ([A2A Spec §13.4](https://a2a-protocol.org/latest/specification/)). Extended cards MAY advertise rate limits/quotas ([A2A Spec §13.3](https://a2a-protocol.org/latest/specification/)).
- **Agent Card caching**: clients SHOULD cache; extended cards SHOULD use cache headers; replace public with extended after auth ([A2A Spec §8, §13.3](https://a2a-protocol.org/latest/specification/)).
- **Model routing**: out of protocol scope — each remote agent chooses its own models/tools. A2A only selects **which agent/skill** via Card + Message content `[inferred]`.
- **Token economics of specialization**: newsletter argument — one mega-agent is slow/expensive/brittle; specialist agents via A2A reduce per-agent context and tool surface ([System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol)). No published `$/1k` A2A execution figures.

## 3. Distributed Resilience & State

### Stateful tasks as the durability unit

Unlike a pure RPC, A2A **Tasks** are first-class durable work items: unique server `id`, lifecycle states, history, artifacts ([A2A Spec §2.2, §4.1.1](https://a2a-protocol.org/latest/specification/)). Clients recover via:

- `GetTask` (poll / post-webhook)
- `SubscribeToTask` after SSE disconnect ([Streaming guide](https://a2a-protocol.org/dev/topics/streaming-and-async/))
- Push notifications with `StreamResponse` payloads ([A2A Spec §4.3.3](https://a2a-protocol.org/latest/specification/))

Multi-stream fan-out: an agent MAY serve **multiple concurrent streams** for one task; events MUST be broadcast in order; closing one stream MUST NOT affect others ([A2A Spec §3.5.2](https://a2a-protocol.org/latest/specification/)).

### Idempotency and cancellation

- Get ops are naturally idempotent; Cancel is idempotent; SendMessage MAY use `messageId` for duplicate detection ([A2A Spec §3.3.1](https://a2a-protocol.org/latest/specification/)).
- Push receivers SHOULD process idempotently (duplicate deliveries allowed) ([A2A Spec §4.3.3](https://a2a-protocol.org/latest/specification/)).
- Cancel on non-cancelable/terminal → `TaskNotCancelableError` (JSON-RPC `-32002`) ([A2A Spec §5.4](https://a2a-protocol.org/latest/specification/)).

### Async-first and HITL

Interrupted states (`INPUT_REQUIRED`, `AUTH_REQUIRED`) pause work for human-in-the-loop or credential acquisition without killing the task ([A2A Spec §4.1.3, §7.6](https://a2a-protocol.org/latest/specification/); [Google A2A launch — long-running tasks](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/)). Push delivery: MUST attempt ≥1; MAY exponential backoff; SHOULD timeout **10–30 s**; MAY stop after consecutive failures ([A2A Spec §4.3.3](https://a2a-protocol.org/latest/specification/)).

### Checkpointing / locking / circuit breakers

> ⚠️ Limited public data available for this dimension. The protocol defines task state and delivery semantics but **does not prescribe** Temporal/Kafka event-sourcing engines, distributed locks, or circuit-breaker thresholds. Those are implementation choices behind the opaque agent.

`[inferred]` Production patterns that fit A2A’s model:

- Persist Task rows keyed by `taskId`/`contextId` with status machine matching `TaskState`
- Treat webhook failures like outbox/retry; fall back to client poll
- Apply circuit breakers **at the client orchestrator** around remote agent endpoints (HTTP/gRPC), not inside the protocol
- Use `contextId` for session affinity; document context TTL/cleanup (spec allows implementation-defined expiration) ([A2A Spec §3.4.1](https://a2a-protocol.org/latest/specification/))

Messages are **not** a reliable critical-delivery channel if the client disconnects mid-stream; persist important content in Task history if needed ([A2A Spec §3.7](https://a2a-protocol.org/latest/specification/)).

### Error taxonomy (machine codes)

A2A-specific JSON-RPC codes include `TaskNotFoundError` `-32001`, `TaskNotCancelableError` `-32002`, `PushNotificationNotSupportedError` `-32003`, `UnsupportedOperationError` `-32004`, `ContentTypeNotSupportedError` `-32005`, `VersionNotSupportedError` `-32009`, etc. ([A2A Spec §5.4](https://a2a-protocol.org/latest/specification/)). System errors may map to HTTP 503 + `Retry-After` ([A2A Spec §3.3.2](https://a2a-protocol.org/latest/specification/)).

## 4. Enterprise Security & Governance

### Transport and identity

- Production MUST use HTTPS (HTTP bindings) / TLS (gRPC); TLS **1.3+** recommended; HSTS SHOULD; disable SSLv3/TLS1.0/1.1 ([A2A Spec §7.1, §13.4](https://a2a-protocol.org/latest/specification/)).
- Clients SHOULD verify server TLS certs against trusted CAs ([A2A Spec §7.2](https://a2a-protocol.org/latest/specification/)).
- Auth is **out-of-band credential acquisition** + credentials on every request; schemes declared on Agent Card, aligned with OpenAPI-style security ([A2A Spec §7.3](https://a2a-protocol.org/latest/specification/); [Google A2A launch — secure by default](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/)).

**SecurityScheme** one-of ([A2A Spec §4.5.1](https://a2a-protocol.org/latest/specification/)):

- API key (query/header/cookie)
- HTTP auth (Basic, Bearer, …)
- OAuth 2.0 (incl. device-code flow for constrained clients)
- OpenID Connect (`openIdConnectUrl`)
- Mutual TLS

### Authorization model

Protocol does **not** fix a global RBAC schema. Servers authorize by identity/policy; MAY consider skill, task actions, data policies, OAuth scopes ([A2A Spec §7.5](https://a2a-protocol.org/latest/specification/)). List/Get/Cancel/Subscribe/push-config MUST scope to caller’s authorized boundary (user, role, project, tenant, custom) **before** any DB query that could leak existence ([A2A Spec §13.1](https://a2a-protocol.org/latest/specification/)). Not-found vs unauthorized SHOULD NOT be distinguishable to clients ([A2A Spec §3.3.2](https://a2a-protocol.org/latest/specification/)).

### Signed Agent Cards

Cards MAY be JWS-signed (RFC 7515) after JCS canonicalization (RFC 8785); `signatures` excluded from signed content; JWKS via `jku` for key discovery ([A2A Spec §8.4](https://a2a-protocol.org/latest/specification/); Signed Cards highlighted in [Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/); signing also called out in [0.3 upgrade](https://cloud.google.com/blog/products/ai-machine-learning/agent2agent-protocol-is-getting-an-upgrade)).

### In-task authorization chains

`TASK_STATE_AUTH_REQUIRED` lets a remote agent pause and ask the client (or upstream agent) for credentials/approval. Credentials SHOULD arrive **out-of-band** over HTTPS. In-band credential relay across multi-agent chains increases exposure — if used, bind/encrypt credentials to the originating agent ([A2A Spec §7.6](https://a2a-protocol.org/latest/specification/)). State transition alone is **not** authorization for any operation ([A2A Spec §7.6.4](https://a2a-protocol.org/latest/specification/)).

### Push / SSRF / audit

Webhook callers SHOULD reject private IP ranges (`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), localhost, link-local; prefer allowlists ([A2A Spec §13.2](https://a2a-protocol.org/latest/specification/); [Streaming guide — SSRF](https://a2a-protocol.org/dev/topics/streaming-and-async/)). Receivers MUST authenticate the server; SHOULD use timestamps/nonces against replay; rate-limit webhook flood ([A2A Spec §13.2](https://a2a-protocol.org/latest/specification/)). Media type `application/a2a+json` for HTTP+JSON payloads ([A2A Spec §14.1](https://a2a-protocol.org/latest/specification/)).

Audit: SHOULD log auth failures, authz denials, suspicious patterns (rapid task creation/cancellations); MUST NOT log credentials/PII unprotected; comply with applicable data protection and provide deletion/retention mechanisms ([A2A Spec §13.4](https://a2a-protocol.org/latest/specification/)). Opaque execution preserves IP of internal tools/prompts ([A2A Spec §1.2](https://a2a-protocol.org/latest/specification/); [A2A home](https://a2a-protocol.org/latest/)).

**PII redaction / sandbox isolation**: not protocol-prescribed. `[inferred]` Apply at agent boundaries before Parts leave the trust domain; sandbox each agent’s tool runtime independently (A2A only carries opaque Parts/Artifacts).

## 5. Production Failure Modes

### Cross-framework / silo failure (pre-A2A)

LangGraph ↔ CrewAI ↔ ADK ↔ AutoGen integrations without A2A require bespoke glue; same-company agents can still fail to collaborate ([System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol)). A2A’s purpose is to eliminate that N×M glue at the **agent** layer.

### Capability / version mismatches

| Failure | Spec response |
| --- | --- |
| Stream without `capabilities.streaming` | `UnsupportedOperationError` |
| Push without `capabilities.pushNotifications` | `PushNotificationNotSupportedError` |
| Extended card unsupported / unconfigured | `UnsupportedOperationError` / `ExtendedAgentCardNotConfiguredError` |
| Wrong `A2A-Version` | `VersionNotSupportedError` (`-32009`) |
| Unsupported MIME in Parts | `ContentTypeNotSupportedError` |
| Required extension not declared by client | `ExtensionSupportRequiredError` |

([A2A Spec §3.3.4, §5.4](https://a2a-protocol.org/latest/specification/))

### Long-running / disconnect / webhook failures

- SSE drop mid-task → resubscribe via `SubscribeToTask`; may miss transient status Messages not persisted in history ([A2A Spec §3.5.2, §3.7](https://a2a-protocol.org/latest/specification/); [Streaming guide](https://a2a-protocol.org/dev/topics/streaming-and-async/)).
- Webhook SSRF if URL not validated ([A2A Spec §13.2](https://a2a-protocol.org/latest/specification/)).
- Webhook down → retries then stop; client must poll or miss terminal state `[inferred]` from §4.3.3 retry/stop language.
- Auth-required without open stream/webhook/poll → client misses continuation ([A2A Spec §7.6.2](https://a2a-protocol.org/latest/specification/)).

### Infinite / cascading work

Protocol has no global max-iteration or cost-cap primitive. `[inferred]` Orchestrators must impose deadline budgets, max hop depth (A→B→C…), and per-task spend caps; otherwise recursive delegation can cascade timeouts and token spend ([System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol) production tradeoff themes: latency, trust, auth, debugging, observability).

### State drift and hallucinated delegation

- Partial writes / retry without `messageId` idempotency → duplicate side effects `[inferred]` (spec allows but does not require SendMessage idempotency) ([A2A Spec §3.3.1](https://a2a-protocol.org/latest/specification/)).
- Mismatched `contextId` + `taskId` → MUST reject ([A2A Spec §3.4.3](https://a2a-protocol.org/latest/specification/)).
- Client invents `taskId` for new tasks → unsupported; server generates IDs ([A2A Spec §3.4.2](https://a2a-protocol.org/latest/specification/)).
- LLM picks wrong skill/agent from Card text → mitigate with skill tags/examples, authz on skills, and human approval via `AUTH_REQUIRED` / `INPUT_REQUIRED` `[inferred]`.

### Observability gaps

Opaque agents hide internal ReAct traces. Spec enterprise principles call for tracing/monitoring alignment ([A2A Spec §1.2](https://a2a-protocol.org/latest/specification/)) but do not mandate OpenTelemetry fields. `[inferred]` Propagate correlation IDs via Message/Task `metadata` and HTTP headers at the gateway; log `taskId`, `contextId`, `messageId` on every hop.

> ⚠️ Limited public data available for this dimension regarding **named production incident post-mortems** with quantified blast radius. Steward docs describe failure *classes* and mitigations; secondary “best practice” blogs exist but are not treated as authoritative benchmarks here.

## 6. Enterprise System Design Scenarios

### Canonical stack

```
[User / Orchestrator Agent]
        |  A2A (discover Card → auth → Task/Message → Artifact)
        v
[Specialist Agent] --MCP/tools--> [DB / SaaS / files]
        |
        +-- A2A --> [Another Specialist]
```

Official recommendation: MCP inside, A2A between ([A2A home](https://a2a-protocol.org/latest/); [Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/)).

### Case study pattern: candidate sourcing (Google launch)

Hiring manager agent → sourcing agents → interview scheduling agent → background-check agent, coordinated via A2A across enterprise systems ([Google A2A launch](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/)). Illustrates multi-vendor, multi-skill, multi-turn task lifecycles with human checkpoints.

### Marketplace / multi-tenant hosting

v1.0 emphasizes multi-tenancy at a single endpoint and heterogeneous bindings so enterprises are not locked to one stack ([Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/)). Google Cloud AI Agent Marketplace path for selling A2A-enabled agents noted in the **0.3** upgrade narrative ([Google Cloud A2A upgrade](https://cloud.google.com/blog/products/ai-machine-learning/agent2agent-protocol-is-getting-an-upgrade)).

### Trade-off matrix

| Approach | Cost | Latency | Ops complexity | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| Mega-agent (no A2A) | High context/tokens | High for broad tasks | Low integration, high brittleness | Single trust domain | Vertical scale only |
| Custom agent APIs | High eng N×M | Tunable | High maintenance | Per-integration | Limited reuse |
| A2A centralized orchestrator | Medium coordination + specialist tokens | Predictable control path | Medium (gateway, Cards, auth) | Card + OAuth/OIDC/mTLS + scoped List/Get | Horizontal specialists |
| A2A decentralized swarm | Variable (discovery/churn) | Variable | High (tracing, trust) | Harder end-to-end authz | Flexible but opaque |

(Centralized vs swarm from [System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol); cost/latency columns `[inferred]` from architecture — no published cluster benchmarks.)

### Capacity planning (what you can quantify today)

| Metric | Public data |
| --- | --- |
| Partner / ecosystem size | >50 at launch (Apr 2025); >100 at LF donation (Jun 2025) ([Google launch](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/); [LF press](https://www.linuxfoundation.org/press/linux-foundation-launches-the-agent2agent-protocol-project-to-enable-secure-intelligent-communication-between-ai-agents)); newsletter later cites **150+** organizations ([System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol)) |
| Concurrent agents / tokens/sec | **Not published** by steward |
| Webhook timeout guidance | **10–30 s** ([A2A Spec §4.3.3](https://a2a-protocol.org/latest/specification/)) |
| Pagination | Cursor-based `pageToken`/`nextPageToken` for List Tasks ([A2A Spec §3.1.4](https://a2a-protocol.org/latest/specification/)) |
| Protocol bindings | **3** core: JSONRPC, GRPC, HTTP+JSON ([A2A Spec §4.4.6](https://a2a-protocol.org/latest/specification/)) |
| Task lifecycle states | **9** including UNSPECIFIED ([A2A Spec §4.1.3](https://a2a-protocol.org/latest/specification/)) |

### Design checklist for Principal Architect interviews

1. Publish Card at `/.well-known/agent-card.json`; sign in cross-org settings.
2. Prefer orchestrator topology until trust + observability mature.
3. Declare `streaming` / `pushNotifications` honestly; clients validate before use.
4. Use Artifacts for outputs; keep Messages for dialogue/HITL.
5. Plan `AUTH_REQUIRED` / `INPUT_REQUIRED` UX and out-of-band credential vaults.
6. SSRF-harden webhooks; rate-limit; scope List/Get by tenant.
7. Version with `A2A-Version` + dual `0.3`/`1.0` interfaces during migration.
8. Keep MCP for tools; never confuse tool servers with peer agents.

## Sources

- [1] https://newsletter.systemdesign.one/p/agent-to-agent-protocol — System Design One #158 (Eric Roby & Neo Kim), primary explainer (26 Jun 2026)
- [2] https://a2a-protocol.org/latest/specification/ — A2A Protocol Specification, Latest Released Version **1.0.0**
- [3] https://a2a-protocol.org/v1.0.0/announcing-1.0/ — Official v1.0 announcement (TSC, multi-tenancy, signed cards, MCP complementarity)
- [4] https://a2a-protocol.org/latest/ — A2A Protocol home (stewardship, MCP vs A2A boundary)
- [5] https://a2a-protocol.org/dev/topics/streaming-and-async/ — Streaming SSE + push-notification security guide
- [6] https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/ — Google launch (9 Apr 2025), design principles, >50 partners, candidate-sourcing scenario
- [7] https://cloud.google.com/blog/topics/partners/best-agentic-ecosystem-helping-partners-build-ai-agents-next25 — Google Cloud Next partner announcement of A2A
- [8] https://www.linuxfoundation.org/press/linux-foundation-launches-the-agent2agent-protocol-project-to-enable-secure-intelligent-communication-between-ai-agents — Linux Foundation project launch (23 Jun 2025)
- [9] https://developers.googleblog.com/google-cloud-donates-a2a-to-linux-foundation/ — Google Cloud donation narrative (>100 companies)
- [10] https://cloud.google.com/blog/products/ai-machine-learning/agent2agent-protocol-is-getting-an-upgrade — A2A **0.3** upgrade (gRPC, signed cards, marketplace)
- [11] https://github.com/a2aproject/A2A — Canonical repo (`specification/a2a.proto`, SDKs)
- [12] https://a2a-protocol.org/v1.0.0/specification/ — Frozen v1.0.0 specification URL
- [13] https://github.com/a2aproject/A2A/blob/v1.0.1/docs/specification.md — Spec markdown at GitHub tag v1.0.1 (docs packaging; wire Major.Minor still `1.0`)
- [14] https://spec.openapis.org/oas/v3.2.0.html#security-scheme-object — OpenAPI 3.2 Security Scheme Object (referenced by A2A SecurityScheme)
