# Module 16 — How A2A Protocol Works

**Audience**: Principal AI Architect interview prep · personal deep study  
**Sequence**: 16 (cross-agent wire protocol after multi-agent topology + MCP tool layer)  
**Grounded in**: `research/16-how-a2a-protocol-works.md` (14 sources, 2026-09-30; primary contract **A2A Protocol `1.0.0`**)

Agent2Agent (A2A) is the **agent ↔ agent** interoperability contract: discover peers via Agent Cards, delegate stateful **Tasks**, exchange **Messages** / **Parts**, and deliver **Artifacts** across frameworks and vendors ([A2A Spec](https://a2a-protocol.org/latest/specification/); [Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/)). Google launched A2A on **9 April 2025** (>50 partners), donated it to the **Linux Foundation** on **23 June 2025** (>100 companies), and **v1.0** is the first stable release ([Google launch](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/); [LF press](https://www.linuxfoundation.org/press/linux-foundation-launches-the-agent2agent-protocol-project-to-enable-secure-intelligent-communication-between-ai-agents)). Normative source: `spec/a2a.proto`. Protocol negotiation uses `Major.Minor` only (e.g. `"1.0"`); clients MUST send `A2A-Version: 1.0` ([A2A Spec §3.6](https://a2a-protocol.org/latest/specification/)).

**Boundary (do not re-teach MCP):** MCP = agent ↔ tools/context inside one agent. A2A = agent ↔ agent across trust/framework boundaries. Use MCP *inside* a specialist; use A2A *between* specialists ([A2A home](https://a2a-protocol.org/latest/); [Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/)). Remote agents are **opaque** collaborators (skills + exchanged information), not tool internals ([A2A Spec §1.2 Opaque Execution](https://a2a-protocol.org/latest/specification/)).

---

## Part 1 — System Topology & Data Flow

### Architecture map

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            CONTROL PLANE                                    │
│  Orchestrator / A2A Client · Agent Card cache · skill/agent routing         │
│  Auth session (OAuth/OIDC/mTLS) · A2A-Version · tenant echo · hop budgets   │
│  Circuit breakers · fallback policy · push-config registration              │
│                                                                             │
│  ┌──────────────┐  ┌────────────────┐  ┌─────────────────────────────────┐  │
│  │ Card fetch   │  │ Task router    │  │ HITL / AUTH_REQUIRED           │  │
│  │ .well-known  │  │ skill → agent  │  │ INPUT_REQUIRED gates           │  │
│  └──────┬───────┘  └───────┬────────┘  └────────────────┬────────────────┘  │
└─────────┼──────────────────┼────────────────────────────┼───────────────────┘
          │ discover         │ SendMessage / stream       │
          ▼                  ▼                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                             DATA PLANE                                      │
│  Bindings: JSON-RPC | gRPC | HTTP+JSON/REST                                 │
│  Ops: SendMessage · SendStreamingMessage · GetTask · ListTasks · Cancel     │
│       SubscribeToTask · push-config CRUD · GetExtendedAgentCard             │
│  Objects: Task · Message · Part · Artifact · contextId · Extension          │
│  Updates: Poll GetTask | SSE stream | Push webhook (at-least-once)          │
└───┬───────────────────────────────┬───────────────────────────────┬─────────┘
    │                               │                               │
    ▼                               ▼                               ▼
┌───────────────────┐   ┌───────────────────────┐   ┌─────────────────────────┐
│   TOOL PROXIES    │   │     PERSISTENCE       │   │      TELEMETRY          │
├───────────────────┤   ├───────────────────────┤   ├─────────────────────────┤
│ MCP *inside* each │   │ Task rows (taskId)    │   │ taskId · contextId      │
│ remote agent      │   │ status machine        │   │ messageId · corr IDs    │
│ (not A2A wire)    │   │ history[] · artifacts │   │ binding latency         │
│ SaaS / DB / files │   │ push delivery outbox  │   │ breaker state · hops    │
│ per-agent RBAC    │   │ contextId TTL/cleanup │   │ authz deny / SSRF deny  │
└───────────────────┘   └───────────────────────┘   └─────────────────────────┘
  * A2A carries opaque Parts/Artifacts; tool calls stay behind the remote agent
```

**Plane responsibilities**

| Plane | What lives here | Truth source |
| --- | --- | --- |
| **CONTROL PLANE** | Client/orchestrator: Card discovery, auth, skill routing, version/tenant headers, breaker + fallback, HITL on interrupted states | [A2A Spec §2.2, §8](https://a2a-protocol.org/latest/specification/); [System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol) |
| **DATA PLANE** | Wire ops over JSON-RPC / gRPC / HTTP+JSON; Task/Message/Part/Artifact; poll / SSE / push | [A2A Spec §3, §5](https://a2a-protocol.org/latest/specification/); [Streaming guide](https://a2a-protocol.org/dev/topics/streaming-and-async/) |
| **PERSISTENCE** | Server-side Task state, history, artifacts; client checkpoints of `taskId`/`contextId`; webhook outbox | [A2A Spec §4.1](https://a2a-protocol.org/latest/specification/) |
| **TOOL PROXIES** | **MCP (or equivalent) inside each opaque agent** — not peer A2A messaging | [A2A home](https://a2a-protocol.org/latest/); [Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/) |
| **TELEMETRY** | `taskId`, `contextId`, `messageId`, hop depth, breaker, authz/SSRF events; OpenTelemetry not mandated by spec | [A2A Spec §1.2, §13.4](https://a2a-protocol.org/latest/specification/) [inferred] correlation via metadata/headers |

### Three-layer protocol stack

```
Layer 1 — Data model (proto): Task, Message, AgentCard, Part, Artifact, Extension
Layer 2 — Abstract ops: SendMessage, SendStreamingMessage, GetTask, ListTasks,
                        CancelTask, SubscribeToTask, push-config CRUD, GetExtendedAgentCard
Layer 3 — Bindings: JSON-RPC | gRPC | HTTP+JSON/REST | custom
```

`spec/a2a.proto` is normative; generated JSON Schema is non-normative ([A2A Spec §1.4](https://a2a-protocol.org/latest/specification/)).

### End-to-end request-flow narrative

1. **Discover Agent Card (CONTROL PLANE)** — Client fetches `https://{server}/.well-known/agent-card.json` (or registry / preconfigured URL). Reads `skills[]`, `supportedInterfaces[]` (`protocolBinding` ∈ {`JSONRPC`,`GRPC`,`HTTP+JSON`}, `protocolVersion`, optional `tenant`), `capabilities` (`streaming`, `pushNotifications`, `extendedAgentCard`), `securitySchemes` / `securityRequirements`, optional JWS `signatures[]` ([A2A Spec §4.4.1, §8.2](https://a2a-protocol.org/latest/specification/)). Cache the card; if `extendedAgentCard: true`, authenticated `GetExtendedAgentCard` SHOULD replace the public cache for the session ([A2A Spec §3.1.11, §13.3](https://a2a-protocol.org/latest/specification/)).
2. **Authenticate** — Acquire credentials out-of-band per Card schemes (API key, HTTP Bearer, OAuth 2.0, OIDC, mTLS). Attach on every request; echo `AgentInterface.tenant` when set ([A2A Spec §4.4.6, §7.3](https://a2a-protocol.org/latest/specification/)). Send `A2A-Version: 1.0` ([A2A Spec §3.6](https://a2a-protocol.org/latest/specification/)).
3. **Send message / task (DATA PLANE)** — `SendMessage` / `POST /message:send` with `Message` (`messageId`, `role`, `parts[]`, optional `contextId`). Server MAY return a **Task** (async work; server-generated `taskId`) or a direct **Message**. Default blocking waits until terminal or interrupted (`INPUT_REQUIRED` / `AUTH_REQUIRED`) unless `return_immediately: true` ([A2A Spec §3.1.1, §3.2.2, §4.1](https://a2a-protocol.org/latest/specification/)).
4. **Stream or poll (DATA PLANE updates)** — If `capabilities.streaming`: `SendStreamingMessage` / `SubscribeToTask` (SSE) for `Task` then `TaskStatusUpdateEvent` / `TaskArtifactUpdateEvent`. Else **poll** `GetTask`. If `pushNotifications`: register push-config; server webhooks client (timeout guidance **10–30 s**; at-least-once; client MUST HTTP 2xx and SHOULD be idempotent) ([A2A Spec §3.5, §4.3.3](https://a2a-protocol.org/latest/specification/); [Streaming guide](https://a2a-protocol.org/dev/topics/streaming-and-async/)).
5. **Artifact (PERSISTENCE → client)** — Terminal success yields `artifacts[]` (output deliverables). Prefer Artifacts for results; Messages are dialogue/HITL, not the primary result channel ([A2A Spec §3.7, §4.1.7](https://a2a-protocol.org/latest/specification/)). `ListTasks` defaults `includeArtifacts: false` to shrink payloads ([A2A Spec §3.1.4](https://a2a-protocol.org/latest/specification/)).
6. **Tool work (TOOL PROXIES, opaque)** — Remote agent may call MCP tools / SaaS behind its boundary; A2A never exposes that surface to the client ([A2A Spec §1.2](https://a2a-protocol.org/latest/specification/)).
7. **Telemetry** — Log `taskId` / `contextId` / `messageId` / correlation ID every hop; breaker and authz outcomes on the orchestrator ([inferred] from enterprise principles in [A2A Spec §1.2, §13.4](https://a2a-protocol.org/latest/specification/)).

**Orchestration topologies** (protocol-agnostic): centralized orchestrator (default enterprise) vs decentralized swarm ([System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol)). A2A is the wire; topology is your control plane.

---

## Part 2 — Core Mechanics & Algorithms

### Theoretical fundamentals

A2A separates **capability advertisement** (Agent Card), **durable work** (Task state machine), and **communication turns** (Messages with typed Parts). Opaque Execution is the key invariant: clients reason over skills + exchanged Parts/Artifacts, never remote prompts/tools ([A2A Spec §1.2](https://a2a-protocol.org/latest/specification/)). Five design principles: Simple (HTTP/JSON-RPC/SSE), Enterprise Ready, Async First, Modality Agnostic, Opaque Execution ([Google launch](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/); [A2A Spec §1.2](https://a2a-protocol.org/latest/specification/)).

### Core objects

| Concept | Role |
| --- | --- |
| **Task** | Stateful unit of work; server-generated `id`; `status`, optional `artifacts[]`, `history[]`, `contextId`, `metadata` |
| **Message** | Turn: `messageId`, `role` (`ROLE_USER` \| `ROLE_AGENT`), `parts[]`, optional `taskId` / `contextId` / `referenceTaskIds` |
| **Part** | Exactly one of `text` \| `raw` \| `url` \| `data`; optional `mediaType`, `filename` |
| **Artifact** | Task **output**: `artifactId`, `parts[]` |
| **contextId** | Groups related tasks/messages (session continuity) |

([A2A Spec §4.1](https://a2a-protocol.org/latest/specification/))

### Task state machine

```
                         ┌──────────────────┐
              submit     │ TASK_STATE_      │
           ─────────────►│ SUBMITTED        │
                         └────────┬─────────┘
                                  │
                                  ▼
                         ┌──────────────────┐
              ┌─────────►│ TASK_STATE_      │◄────────────┐
              │          │ WORKING          │             │
              │          └────┬───────┬─────┘             │
              │               │       │                   │
              │    interrupt  │       │  interrupt        │
              │               ▼       ▼                   │
              │  ┌────────────────┐ ┌──────────────────┐  │
              │  │ INPUT_REQUIRED │ │ AUTH_REQUIRED    │──┘ resume
              │  └───────┬────────┘ └────────┬─────────┘  (new Message)
              │          │                   │
              │          └─────────┬─────────┘
              │                    │ success / fail / cancel / reject
              │                    ▼
              │     ┌──────────────────────────────────────────┐
              │     │ TERMINAL: COMPLETED | FAILED | CANCELED  │
              └─────│            | REJECTED                    │
   (illegal)        └──────────────────────────────────────────┘
   further msgs → UnsupportedOperationError

   TASK_STATE_UNSPECIFIED = unknown / recovery
```

Nine states including `UNSPECIFIED` ([A2A Spec §4.1.3](https://a2a-protocol.org/latest/specification/)). Terminal states reject further messages (`UnsupportedOperationError`) ([A2A Spec §3.1.1](https://a2a-protocol.org/latest/specification/)). Interrupted states enable HITL / credential acquisition without killing the task ([A2A Spec §7.6](https://a2a-protocol.org/latest/specification/)).

### Streaming algorithms

| Pattern | Behavior |
| --- | --- |
| Message-only stream | One `Message`, then close |
| Task lifecycle stream | Initial `Task`, then status/artifact events (`append` / `lastChunk`); close on terminal |
| Multi-stream fan-out | Agent MAY serve multiple concurrent streams per task; events MUST broadcast in order; closing one MUST NOT affect others |

([A2A Spec §3.1.2, §3.5.2](https://a2a-protocol.org/latest/specification/))

**Complexity [inferred]**: poll interval \(p\) over duration \(T\) → \(O(T/p)\) GetTask calls; SSE is \(O(E)\) events. Nested A2A depth \(d\) multiplies coordination RTTs \(\approx O(d)\) and risks context duplication ([System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol)).

### Key invariants

1. **Server owns `taskId`** — clients MUST NOT invent IDs for new tasks ([A2A Spec §3.4.2](https://a2a-protocol.org/latest/specification/)).
2. **`contextId` + `taskId` consistency** — mismatch MUST reject ([A2A Spec §3.4.3](https://a2a-protocol.org/latest/specification/)).
3. **Capability honesty** — stream/push without Card flags → protocol errors (`UnsupportedOperationError`, `PushNotificationNotSupportedError`) ([A2A Spec §5.4](https://a2a-protocol.org/latest/specification/)).
4. **Messages ≠ durable critical channel** — persist important content in Task history; SSE drops can lose transient status Messages ([A2A Spec §3.7](https://a2a-protocol.org/latest/specification/)).
5. **Version wire** — empty `A2A-Version` interpreted as **0.3**; Cards may advertise both `0.3` and `1.0` during migration ([A2A Spec §3.6](https://a2a-protocol.org/latest/specification/); [Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/)).

---

## Part 3 — Token Economics & NFR Analysis

> ⚠️ **Gap**: A2A is an interoperability protocol, not a model API. The v1.0 specification and steward announcements publish **no official p50/p95/p99 latency SLAs**, **no** token-cost formulas, **no** RPM/TPM quotas, and **no** `$ per 1k tasks` meter for A2A framing itself. A2A traffic is **unmetered** by the protocol. Numbers below are architectural **[inferred]** budgets or labeled model-price assumptions — not vendor benchmarks ([research §2](../research/16-how-a2a-protocol-works.md)).

### Cost formula — `$` per 1k tasks

A2A framing (Card GET, SendMessage, GetTask/SSE events) is coordination overhead. Dollars land in **each agent's LLM + tools**.

**Assumptions (state explicitly; substitute contract rates):**

| Symbol | Meaning | Example assumption |
| --- | --- | --- |
| \(P_{in}, P_{out}\) | $/1M input / output tokens on **remote specialist** model | mid-tier: **$3 / $15** |
| \(P^{L}_{in}, P^{L}_{out}\) | Local fallback agent prices | small tier: **$0.40 / $1.60** |
| \(T^{R}_{in}, T^{R}_{out}\) | Avg tokens per remote specialist task | **4,000 / 800** |
| \(T^{L}_{in}, T^{L}_{out}\) | Avg tokens if fallback local agent runs | **2,500 / 400** |
| \(f\) | Fraction of tasks that hit remote (rest local/queued) | **0.85** |
| \(H\) | Prompt-cache hit rate on stable specialist system+skill prefix | **0.6** |
| \(D_{cache}\) | Cached-input price multiplier | **0.1×** list input (illustrative) |
| \(C_{coord}\) | Fixed $/task for egress/LB (A2A bytes) | **~$0** at protocol layer (treat as infra opex) |

**Per-task remote model cost [inferred]:**

\[
c_R = \frac{(1-H)\,T^{R}_{in}\,P_{in} + H\,T^{R}_{in}\,P_{in}\,D_{cache}}{10^{6}}
      + \frac{T^{R}_{out}\,P_{out}}{10^{6}}
\]

With assumptions: effective input \(= 0.4\cdot4000 + 0.6\cdot4000\cdot0.1 = 1840\) tok →  
\(c_R = (1840\cdot3 + 800\cdot15)/10^6 = \$0.01752\).

Local fallback: \(c_L = (2500\cdot0.40 + 400\cdot1.60)/10^6 = \$0.00164\).

**`$` per 1k tasks (model only, A2A unmetered):**

\[
\$_{1k} = 1000 \times \big(f\cdot c_R + (1-f)\cdot c_L\big)
       = 1000 \times (0.85\cdot0.01752 + 0.15\cdot0.00164)
       \approx \$15.14
\]

Nested A2A (A→B→C) multiplies specialist \(c_R\) per hop — impose max hop depth and spend caps in CONTROL PLANE ([System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol)). Specialization argument: mega-agent = large context; A2A specialists shrink per-agent tool/context surface ([System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol)).

### Latency — official gap + [inferred] budget

> ⚠️ **Gap**: No official published p50/p95/p99 for A2A Card fetch, task RTT, or SSE. Push webhook **10–30 s** is a **delivery timeout budget** for the webhook HTTP call ([A2A Spec §4.3.3](https://a2a-protocol.org/latest/specification/)) — **not** an end-to-end task latency SLA.

**[inferred] Latency budget (short specialist task, same-region HTTP+JSON)**

| Stage | p50 (ms) | p95 (ms) | p99 (ms) | Notes |
| --- | --- | --- | --- | --- |
| Agent Card fetch (cold) | 40 | 120 | 300 | Well-known GET; cacheable |
| Agent Card fetch (warm cache) | 1 | 2 | 5 | Local TTL cache |
| Auth token attach / validate | 5 | 20 | 80 | JWKS/introspection cached |
| Task RTT — SendMessage framing | 30 | 80 | 180 | Network RTT-dominated |
| Remote agent LLM + tools | 800 | 3,000 | 12,000 | Dominates e2e |
| SSE first event after accept | 50 | 150 | 400 | Perceived latency win vs blocking wait |
| Poll GetTask (one cycle) | 30 | 80 | 180 | Plus poll interval waste |
| **E2E blocking short task (warm card)** | **~866** | **~3,182** | **~12,445** | \(1+5+30+800\) … \(5+80+180+12000\) |

**Arithmetic (p50 warm path):** \(1 + 5 + 30 + 800 = 836\) ms framing+model; add ~30 ms artifact finalize → **≈866 ms**.  
**p95:** \(2 + 20 + 80 + 3000 = 3102\) → **≈3.2 s**.  
**p99:** \(5 + 80 + 180 + 12000 = 12265\) → **≈12.3 s**.

Long research tasks shift entirely to remote LLM time; push+GetTask is the right pattern (minutes–days), with webhook **10–30 s** only bounding the notify HTTP call ([A2A Spec §3.2.2, §4.3.3](https://a2a-protocol.org/latest/specification/)).

**Mitigations by tier**

| Tier | Target | Mitigations |
| --- | --- | --- |
| **p50** | Card + framing ≪ model time | Aggressive Card cache; gRPC for internal mesh; `return_immediately` + SSE for perceived latency |
| **p95** | Cap remote tool tails | Per-task deadline; skill-scoped specialists; limit hop depth; prefer SSE over chatty poll |
| **p99** | Prevent cascade | Circuit breaker on remote agent; fallback local → queue; CancelTask; stop webhook after consecutive failures then poll |

### Throughput & back-pressure

- Agents SHOULD rate-limit all ops; MAY tier by user; extended cards MAY advertise quotas — **no numeric defaults** ([A2A Spec §13.3, §13.4](https://a2a-protocol.org/latest/specification/)).
- List Tasks: cursor pagination (`pageToken` / `nextPageToken`) ([A2A Spec §3.1.4](https://a2a-protocol.org/latest/specification/)).
- System overload MAY map to HTTP **503** + `Retry-After` ([A2A Spec §3.3.2](https://a2a-protocol.org/latest/specification/)).

**[inferred] Capacity knobs**: concurrent Tasks per tenant, SSE connection count, webhook fan-out QPS, orchestrator bulkhead per remote agent URL. **Back-pressure**: reject new SendMessage at gateway → open circuit → fallback local/queue → CancelTask on deadline burn. Prefer gRPC for high-throughput internal meshes; JSON-RPC/HTTP+JSON for gateway familiarity ([Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/)).

### NFR trade-offs

| NFR | Guidance |
| --- | --- |
| **Availability** | Multi-instance A2A servers need shared Task store (or sticky routing). Client orchestrator bulkheads per remote agent. Dual `0.3`/`1.0` interfaces during migration ([Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/)). |
| **RPO / RTO of task state** | Protocol does not prescribe backup SLOs. **[inferred]** RPO = Task DB WAL/checkpoint interval (lose in-flight WORKING progress if only memory). RTO = restore Task store + re-`GetTask`/`SubscribeToTask`; client recovers via poll/subscribe/push ([A2A Spec §3.5](https://a2a-protocol.org/latest/specification/)). Context TTL is implementation-defined ([A2A Spec §3.4.1](https://a2a-protocol.org/latest/specification/)). |
| **Compliance** | Spec: MUST NOT log credentials/PII unprotected; provide deletion/retention; TLS 1.3+ recommended ([A2A Spec §7.1, §13.4](https://a2a-protocol.org/latest/specification/)). No SOC2/HIPAA schema in-protocol — implementor responsibility. Opaque execution helps IP isolation of tools/prompts ([A2A Spec §1.2](https://a2a-protocol.org/latest/specification/)). |

### Explicit trade-off A — opaque remote agent vs in-process call

| Dimension | Opaque remote A2A agent | In-process / same-runtime call |
| --- | --- | --- |
| **Cost** | Specialist tokens + RTTs; smaller context per agent | Shared mega-context; often higher tokens |
| **Latency** | +1 RTT (or more) + remote queue | Sub-ms IPC; no Card/auth |
| **Ops** | Card, versioning, auth, breakers | Deploy monolith / library |
| **Security** | Trust boundary; OAuth/mTLS; opaque IP | Single trust domain; tools visible |
| **Scalability** | Horizontal specialists across vendors | Vertical / same cluster only |

**Decision rule**: use **A2A** when the peer is another team/vendor/framework or must stay opaque; use **in-process** for hot-path micro-skills inside one trust domain ([A2A Spec §1.2](https://a2a-protocol.org/latest/specification/); [System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol)).

### Explicit trade-off B — poll vs SSE vs push

| Dimension | Poll `GetTask` | SSE stream | Push webhook |
| --- | --- | --- | --- |
| **Cost** | Wasted polls | Long-lived conn | Notify + optional GetTask |
| **Latency** | Poll-interval lag | Best perceived | Good for long tasks; notify ≠ SLA |
| **Ops** | Firewall-friendly | LB/proxy SSE support | Public callback URL, SSRF hardening |
| **Security** | Client-initiated only | Client-initiated | Server→client; auth webhook; SSRF risk |
| **Scalability** | High poll load | Conn count limits | Fan-out; 10–30 s webhook timeout budget |

**Decision rule**: short interactive → SSE (if Card allows); enterprise DMZ without inbound → poll; minutes–days → push + poll fallback ([A2A Spec §3.5, §4.3.3](https://a2a-protocol.org/latest/specification/); [Streaming guide](https://a2a-protocol.org/dev/topics/streaming-and-async/)).

---

## Part 4 — Distributed Resilience & Security

### Durable execution — Task as the durability unit

Unlike pure RPC, A2A **Tasks** are first-class durable work: unique server `id`, lifecycle, history, artifacts ([A2A Spec §2.2, §4.1.1](https://a2a-protocol.org/latest/specification/)). Clients recover via `GetTask`, `SubscribeToTask` after SSE drop, or push payloads ([Streaming guide](https://a2a-protocol.org/dev/topics/streaming-and-async/); [A2A Spec §4.3.3](https://a2a-protocol.org/latest/specification/)).

> ⚠️ **Gap**: Spec does **not** prescribe Temporal/Kafka engines, distributed locks, or breaker thresholds. Those are implementation choices behind the opaque agent / client orchestrator ([research §3](../research/16-how-a2a-protocol-works.md)).

**[inferred] Production pattern**: persist Task rows keyed by `taskId`/`contextId` matching `TaskState`; webhook outbox with retry then client poll; circuit breakers **at the client orchestrator** around remote endpoints; document context TTL.

Integration sketch: Temporal/Kafka workflow holds orchestration steps; each activity is an A2A `SendMessage` + wait-for-terminal; checkpoint `taskId` for replay-safe resume; DLQ on permanent `FAILED`/`REJECTED`.

### Failure taxonomy

| Class | Examples | Handling |
| --- | --- | --- |
| **Transient** | 503 + `Retry-After`, SSE disconnect, webhook timeout | Retry + jitter; resubscribe; poll after push stop |
| **Permanent / protocol** | `VersionNotSupportedError` `-32009`, `ContentTypeNotSupportedError`, `UnsupportedOperationError` | Fail fast; fix Card/client; no blind retry |
| **AuthZ / not-found** | Scoped List/Get; not-found vs unauthorized MUST NOT be distinguishable | Deny; audit; do not probe existence |
| **Poison / cascade** | Recursive A→B→C, runaway spend | Max hop depth, deadline, CancelTask |
| **Capability mismatch** | Stream/push without Card flags | `UnsupportedOperationError` / `PushNotificationNotSupportedError` |

([A2A Spec §3.3, §5.4, §13.1](https://a2a-protocol.org/latest/specification/))

### IDEMPOTENCY of task / message identifiers

| Op | Idempotency |
| --- | --- |
| `GetTask` / List / Cancel | Naturally / Cancel idempotent |
| `SendMessage` | MAY use `messageId` for duplicate detection ([A2A Spec §3.3.1](https://a2a-protocol.org/latest/specification/)) |
| Push receiver | SHOULD process idempotently (at-least-once) ([A2A Spec §4.3.3](https://a2a-protocol.org/latest/specification/)) |
| New `taskId` | **Server-generated**; client stores mapping `messageId → taskId` for safe retry |

**Invariant**: retries MUST reuse the same `messageId` so the server can return the existing Task instead of creating duplicates ([A2A Spec §3.3.1, §3.4.2](https://a2a-protocol.org/latest/specification/)).

### Circuit breaker (client → remote agent): closed → open → half-open

```
  success                 cooldown elapsed
┌─────────┐  failures≥N  ┌──────┐  probe   ┌───────────┐
│ CLOSED  │─────────────►│ OPEN │─────────►│ HALF_OPEN │
└────▲────┘              └──────┘          └─────┬─────┘
     │ success                                   │
     └───────────────────────────────────────────┘
              failure in HALF_OPEN → OPEN
```

Apply breakers **per remote agent base URL / skill**, not inside the A2A proto ([inferred] production pattern).

### Fallback chain

**Remote specialist → local agent → queued task**

1. Primary: A2A to remote specialist (Card skill match).  
2. On OPEN breaker / permanent remote failure: run **local** agent (same trust domain, reduced skill).  
3. If local also refuses or budget exhausted: **enqueue** durable task for later (outbox / work queue); surface `queued` to user; drain when breaker half-opens.

### Enterprise security

#### Agent Card auth (OAuth / OIDC / mTLS)

`SecurityScheme` one-of: API key, HTTP auth, **OAuth 2.0** (incl. device-code), **OpenID Connect** (`openIdConnectUrl`), **mutual TLS** ([A2A Spec §4.5.1](https://a2a-protocol.org/latest/specification/)). Production MUST use HTTPS/TLS; TLS **1.3+** recommended; verify server certs ([A2A Spec §7.1, §7.2](https://a2a-protocol.org/latest/specification/)). Cards MAY be **JWS-signed** (RFC 7515) after JCS (RFC 8785); JWKS via `jku` ([A2A Spec §8.4](https://a2a-protocol.org/latest/specification/); [Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/)).

#### Zero-Trust — do not trust Card claims without verification

Treat Card `skills`, `capabilities`, and URLs as **untrusted advertisements** until: (1) TLS identity matches expected host, (2) optional JWS verifies against known JWKS, (3) OAuth audience/scopes authorize the skill, (4) runtime probes only negotiated capabilities. State transition alone is **not** authorization ([A2A Spec §7.6.4](https://a2a-protocol.org/latest/specification/)). Extended cards after auth replace public cards for the session ([A2A Spec §13.3](https://a2a-protocol.org/latest/specification/)).

#### Tool RBAC stays on the MCP side (boundary)

A2A authorizes **which caller may create/get/cancel which Tasks** (identity, skill, tenant, OAuth scopes) ([A2A Spec §7.5, §13.1](https://a2a-protocol.org/latest/specification/)). **Tool-level least privilege** remains inside each agent's MCP/tool gateway — A2A does not carry tool RBAC. Do not conflate peer-agent authz with tool allowlists ([A2A home](https://a2a-protocol.org/latest/)).

#### PII in Message Parts: detect → redact → audit

Spec: MUST NOT log credentials/PII unprotected; provide deletion/retention ([A2A Spec §13.4](https://a2a-protocol.org/latest/specification/)). **[inferred]** At trust-domain egress: **detect** PII in Parts → **redact/tokenize** before SendMessage → **audit** redaction event with `messageId`/`taskId` (no raw PII). Apply again on inbound Artifacts before merging into user-visible context.

#### Immutable task log

**[inferred]** Append-only audit of Task status transitions + Message envelopes (hashes of Parts, not secrets): `taskId`, `contextId`, `messageId`, actor, from→to state, timestamp. Supports chain-of-custody for agent decisions across opaque hops ([A2A Spec §13.4](https://a2a-protocol.org/latest/specification/) logging guidance).

#### SSRF on push webhooks

Webhook callers SHOULD reject private ranges (`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), localhost, link-local; prefer allowlists ([A2A Spec §13.2](https://a2a-protocol.org/latest/specification/); [Streaming guide — SSRF](https://a2a-protocol.org/dev/topics/streaming-and-async/)). Receivers MUST authenticate the server; SHOULD use timestamps/nonces against replay; rate-limit floods ([A2A Spec §13.2](https://a2a-protocol.org/latest/specification/)).

---

## Part 5 — Production Enterprise Code

Runnable, self-contained Python: tiny **A2A-shaped client** (Agent Card fetch + Task state machine) with **idempotent `messageId` → task mapping**, retries + full jitter, circuit breaker (**closed → open → half-open**) on a remote agent, fallback **remote → local → queued**, and structured logs with **correlation IDs**. Deterministic fake remote agent — **no API keys, no TODOs**.

```python
#!/usr/bin/env python3
"""A2A-shaped client: Agent Card + Task FSM + resilience (no API keys)."""

from __future__ import annotations

import json
import logging
import random
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Structured logging with correlation IDs
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "msg": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", None),
            "message_id": getattr(record, "message_id", None),
            "task_id": getattr(record, "task_id", None),
            "context_id": getattr(record, "context_id", None),
            "attempt": getattr(record, "attempt", None),
            "breaker_state": getattr(record, "breaker_state", None),
            "agent": getattr(record, "agent", None),
            "fallback": getattr(record, "fallback", None),
        }
        return json.dumps({k: v for k, v in payload.items() if v is not None}, sort_keys=True)


def build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


LOG = build_logger("a2a.client")


# ---------------------------------------------------------------------------
# Task state machine (A2A-shaped subset)
# ---------------------------------------------------------------------------

class TaskState(str, Enum):
    SUBMITTED = "TASK_STATE_SUBMITTED"
    WORKING = "TASK_STATE_WORKING"
    INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
    AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"
    COMPLETED = "TASK_STATE_COMPLETED"
    FAILED = "TASK_STATE_FAILED"
    CANCELED = "TASK_STATE_CANCELED"
    REJECTED = "TASK_STATE_REJECTED"
    UNSPECIFIED = "TASK_STATE_UNSPECIFIED"


TERMINAL = {
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELED,
    TaskState.REJECTED,
}


@dataclass
class Part:
    text: str


@dataclass
class Artifact:
    artifact_id: str
    parts: list[Part]


@dataclass
class Task:
    task_id: str
    context_id: str
    status: TaskState
    artifacts: list[Artifact] = field(default_factory=list)
    history_message_ids: list[str] = field(default_factory=list)


@dataclass
class AgentCard:
    name: str
    url: str
    protocol_version: str
    skills: list[str]
    streaming: bool
    push_notifications: bool
    signature_ok: bool


# ---------------------------------------------------------------------------
# Circuit breaker: closed → open → half-open
# ---------------------------------------------------------------------------

class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 2
    recovery_timeout_ticks: int = 2
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    opened_at_tick: int | None = None
    clock_tick: int = 0

    def tick(self) -> None:
        self.clock_tick += 1

    def allow(self) -> bool:
        if self.state is BreakerState.OPEN:
            assert self.opened_at_tick is not None
            if self.clock_tick - self.opened_at_tick >= self.recovery_timeout_ticks:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.state = BreakerState.CLOSED
        self.opened_at_tick = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.state is BreakerState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at_tick = self.clock_tick


# ---------------------------------------------------------------------------
# Retries: exponential backoff + full jitter (deterministic RNG)
# ---------------------------------------------------------------------------

@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay_ms: int = 10
    max_delay_ms: int = 80
    rng: random.Random = field(default_factory=lambda: random.Random(0))

    def delay_ms(self, attempt: int) -> int:
        ceiling = min(self.max_delay_ms, self.base_delay_ms * (2**attempt))
        return self.rng.randint(0, ceiling)


class TransientAgentError(Exception):
    pass


class PermanentAgentError(Exception):
    pass


# ---------------------------------------------------------------------------
# Deterministic fake remote A2A server
# ---------------------------------------------------------------------------

@dataclass
class FakeRemoteAgent:
    """In-memory A2A server: Card + SendMessage with messageId idempotency."""

    name: str = "specialist-remote"
    fail_times: int = 0
    _calls: int = 0
    _tasks: dict[str, Task] = field(default_factory=dict)
    _by_message_id: dict[str, str] = field(default_factory=dict)
    _id_seq: int = 0

    def agent_card(self) -> AgentCard:
        return AgentCard(
            name=self.name,
            url=f"https://agents.example/{self.name}",
            protocol_version="1.0",
            skills=["sourcing", "summarize"],
            streaming=True,
            push_notifications=True,
            signature_ok=True,
        )

    def send_message(
        self,
        *,
        message_id: str,
        context_id: str,
        text: str,
        skill: str,
    ) -> Task:
        if message_id in self._by_message_id:
            return self._tasks[self._by_message_id[message_id]]

        self._calls += 1
        if self._calls <= self.fail_times:
            raise TransientAgentError(f"{self.name}:transient")

        if skill not in ("sourcing", "summarize"):
            raise PermanentAgentError(f"{self.name}:unknown_skill")

        self._id_seq += 1
        task_id = f"task-{self.name}-{self._id_seq}"
        task = Task(
            task_id=task_id,
            context_id=context_id,
            status=TaskState.SUBMITTED,
            history_message_ids=[message_id],
        )
        task.status = TaskState.WORKING
        task.status = TaskState.COMPLETED
        task.artifacts = [
            Artifact(
                artifact_id=f"art-{self._id_seq}",
                parts=[Part(text=f"{self.name}:ok:{skill}:{text}")],
            )
        ]
        self._tasks[task_id] = task
        self._by_message_id[message_id] = task_id
        return task

    def get_task(self, task_id: str) -> Task:
        if task_id not in self._tasks:
            raise PermanentAgentError("TaskNotFoundError")
        return self._tasks[task_id]


@dataclass
class FakeLocalAgent:
    name: str = "local-fallback"

    def run(self, *, text: str, skill: str) -> Artifact:
        return Artifact(
            artifact_id="art-local-1",
            parts=[Part(text=f"{self.name}:ok:{skill}:{text}")],
        )


# ---------------------------------------------------------------------------
# Zero-Trust Card gate + PII redact (boundary helpers)
# ---------------------------------------------------------------------------

PRIVATE_PREFIXES = ("127.", "10.", "192.168.", "172.16.", "localhost")


def verify_agent_card(card: AgentCard, *, expected_host_substring: str) -> None:
    """Do not trust Card claims without verification."""
    if not card.signature_ok:
        raise PermanentAgentError("card_signature_untrusted")
    if expected_host_substring not in card.url:
        raise PermanentAgentError("card_url_untrusted")
    if card.protocol_version.split(".")[0] != "1":
        raise PermanentAgentError("VersionNotSupportedError")


def redact_parts(text: str) -> tuple[str, bool]:
    """detect → redact (simple email pattern) → caller audits."""
    if "@" in text and "." in text.split("@")[-1]:
        return "[REDACTED_EMAIL]", True
    return text, False


def ssrf_safe_webhook(url: str) -> bool:
    host = url.split("://")[-1].split("/")[0].split(":")[0].lower()
    return not any(host.startswith(p) or host == p.rstrip(".") for p in PRIVATE_PREFIXES)


# ---------------------------------------------------------------------------
# A2A client orchestrator
# ---------------------------------------------------------------------------

@dataclass
class A2AClient:
    remote: FakeRemoteAgent
    local: FakeLocalAgent
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    card_cache: AgentCard | None = None
    # Idempotency: message_id → task_id (client-side mirror of server map)
    message_to_task: dict[str, str] = field(default_factory=dict)
    immutable_log: list[dict[str, Any]] = field(default_factory=list)
    queue: list[dict[str, Any]] = field(default_factory=list)

    def discover_card(self, *, correlation_id: str) -> AgentCard:
        if self.card_cache is not None:
            return self.card_cache
        card = self.remote.agent_card()
        verify_agent_card(card, expected_host_substring="agents.example")
        self.card_cache = card
        LOG.info(
            "card_fetched",
            extra={"correlation_id": correlation_id, "agent": card.name},
        )
        return card

    def _audit(self, event: str, **fields: Any) -> None:
        entry = {"event": event, **fields}
        self.immutable_log.append(entry)

    def send_task(
        self,
        *,
        text: str,
        skill: str,
        correlation_id: str,
        context_id: str | None = None,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Flow: discover Card → send message/task → poll GetTask → artifact.
        Idempotent on message_id. Fallback: remote → local → queued.
        """
        ctx = context_id or f"ctx-{uuid.uuid5(uuid.NAMESPACE_URL, correlation_id).hex[:8]}"
        mid = message_id or str(uuid.uuid5(uuid.NAMESPACE_OID, f"{correlation_id}:{text}:{skill}"))

        if mid in self.message_to_task:
            tid = self.message_to_task[mid]
            task = self.remote.get_task(tid)
            LOG.info(
                "idempotent_hit",
                extra={
                    "correlation_id": correlation_id,
                    "message_id": mid,
                    "task_id": tid,
                    "context_id": ctx,
                },
            )
            return self._result(task, path="remote_idempotent", correlation_id=correlation_id)

        card = self.discover_card(correlation_id=correlation_id)
        if skill not in card.skills:
            raise PermanentAgentError("skill_not_on_card")

        clean, redacted = redact_parts(text)
        if redacted:
            self._audit(
                "pii_redacted",
                correlation_id=correlation_id,
                message_id=mid,
                context_id=ctx,
            )

        self.breaker.tick()
        errors: list[str] = []

        # --- remote specialist with retries ---
        if self.breaker.allow():
            for attempt in range(self.retry.max_attempts):
                try:
                    task = self.remote.send_message(
                        message_id=mid,
                        context_id=ctx,
                        text=clean,
                        skill=skill,
                    )
                    # stream-or-poll simulation: poll until terminal
                    while task.status not in TERMINAL:
                        task = self.remote.get_task(task.task_id)
                    self.breaker.record_success()
                    self.message_to_task[mid] = task.task_id
                    self._audit(
                        "task_transition",
                        task_id=task.task_id,
                        to=task.status.value,
                        message_id=mid,
                        correlation_id=correlation_id,
                    )
                    LOG.info(
                        "remote_ok",
                        extra={
                            "correlation_id": correlation_id,
                            "message_id": mid,
                            "task_id": task.task_id,
                            "context_id": ctx,
                            "attempt": attempt,
                            "breaker_state": self.breaker.state.value,
                            "agent": card.name,
                        },
                    )
                    return self._result(task, path="remote", correlation_id=correlation_id)
                except TransientAgentError as exc:
                    self.breaker.record_failure()
                    delay = self.retry.delay_ms(attempt)
                    errors.append(f"remote:a{attempt}:{exc}:sleep{delay}ms")
                    LOG.info(
                        "remote_fail",
                        extra={
                            "correlation_id": correlation_id,
                            "message_id": mid,
                            "attempt": attempt,
                            "breaker_state": self.breaker.state.value,
                            "agent": card.name,
                        },
                    )
                    if self.breaker.state is BreakerState.OPEN:
                        break
                except PermanentAgentError as exc:
                    self.breaker.record_failure()
                    errors.append(f"remote:permanent:{exc}")
                    break
        else:
            errors.append(f"remote:breaker_{self.breaker.state.value}")
            LOG.info(
                "breaker_open_skip",
                extra={
                    "correlation_id": correlation_id,
                    "breaker_state": self.breaker.state.value,
                    "agent": card.name,
                },
            )

        # --- fallback: local agent ---
        try:
            art = self.local.run(text=clean, skill=skill)
            LOG.info(
                "local_fallback_ok",
                extra={
                    "correlation_id": correlation_id,
                    "message_id": mid,
                    "fallback": "local",
                    "context_id": ctx,
                },
            )
            self._audit(
                "fallback_local",
                correlation_id=correlation_id,
                message_id=mid,
                errors=errors,
            )
            return {
                "path": "local",
                "correlation_id": correlation_id,
                "message_id": mid,
                "context_id": ctx,
                "status": TaskState.COMPLETED.value,
                "artifact": art.parts[0].text,
                "errors": errors,
            }
        except Exception as exc:  # local hard-fail → queue
            errors.append(f"local:{exc}")

        # --- fallback: queued task ---
        queued = {
            "message_id": mid,
            "context_id": ctx,
            "text": clean,
            "skill": skill,
            "correlation_id": correlation_id,
        }
        self.queue.append(queued)
        self._audit("fallback_queued", **queued, errors=errors)
        LOG.info(
            "queued_fallback",
            extra={
                "correlation_id": correlation_id,
                "message_id": mid,
                "fallback": "queued",
                "context_id": ctx,
            },
        )
        return {
            "path": "queued",
            "correlation_id": correlation_id,
            "message_id": mid,
            "context_id": ctx,
            "status": TaskState.SUBMITTED.value,
            "artifact": None,
            "errors": errors,
        }

    def _result(self, task: Task, *, path: str, correlation_id: str) -> dict[str, Any]:
        art = task.artifacts[0].parts[0].text if task.artifacts else None
        return {
            "path": path,
            "correlation_id": correlation_id,
            "task_id": task.task_id,
            "context_id": task.context_id,
            "status": task.status.value,
            "artifact": art,
        }


# ---------------------------------------------------------------------------
# Demo (deterministic)
# ---------------------------------------------------------------------------

def main() -> None:
    remote = FakeRemoteAgent(fail_times=2)
    client = A2AClient(remote=remote, local=FakeLocalAgent())
    corr = "corr-demo-001"

    # 1) First call: remote fails twice → breaker may open → local fallback
    r1 = client.send_task(text="find candidates for role X", skill="sourcing", correlation_id=corr)
    assert r1["path"] in {"remote", "local", "queued"}
    print("run1", json.dumps(r1, sort_keys=True))

    # 2) Same message_id → idempotent hit if remote eventually stored; else stable mid
    mid = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{corr}:find candidates for role X:sourcing"))
    # Advance breaker clock toward half-open / closed recovery
    client.breaker.tick()
    client.breaker.tick()
    remote.fail_times = 0  # remote healthy again
    remote._calls = 0

    r2 = client.send_task(
        text="find candidates for role X",
        skill="sourcing",
        correlation_id=corr,
        message_id=mid,
    )
    print("run2", json.dumps(r2, sort_keys=True))

    # 3) Idempotent replay
    r3 = client.send_task(
        text="find candidates for role X",
        skill="sourcing",
        correlation_id=corr,
        message_id=mid,
    )
    print("run3", json.dumps(r3, sort_keys=True))
    if r2.get("task_id") and r3.get("task_id"):
        assert r2["task_id"] == r3["task_id"]

    # 4) SSRF guard example
    assert ssrf_safe_webhook("https://hooks.example.com/a2a") is True
    assert ssrf_safe_webhook("http://127.0.0.1/steal") is False

    print("audit_entries", len(client.immutable_log))
    print("queue_depth", len(client.queue))
    print("ok")


if __name__ == "__main__":
    main()
```

Run: `python3 modules/16-how-a2a-protocol-works.md` is not valid — extract the code block or save as `a2a_client_demo.py` and run `python3 a2a_client_demo.py`. Expected: JSON logs, `run1`/`run2`/`run3`, `ok`.

---

## Part 6 — Architectural System Design Scenarios

### Scenario A — Cross-vendor hiring pipeline (orchestrator + specialists)

**Problem**: Design a hiring orchestrator that delegates **candidate sourcing**, **interview scheduling**, and **background-check** to agents owned by different vendors/frameworks. Volume: ~5k hiring workflows/day; each workflow 3–6 A2A tasks; HITL on offer approval; must not expose vendor tool internals; p95 interactive status < 5 s perceived for short steps; long background checks run hours–days ([Google A2A launch — candidate sourcing](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/)).

**Proposed architecture**

```
┌─────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE — Hiring Orchestrator (A2A Client)                │
│ Card cache · OAuth per vendor · hop budget=3 · spend caps       │
│ breakers per specialist · HITL on AUTH/INPUT_REQUIRED           │
└───────────────┬─────────────────────┬─────────────┬─────────────┘
                │ A2A                 │ A2A         │ A2A
                ▼                     ▼             ▼
        ┌───────────────┐   ┌────────────────┐ ┌──────────────────┐
        │ Sourcing Agent│   │ Scheduling Agt │ │ Background-check │
        │ Card+skills   │   │ Card+skills    │ │ Agent            │
        └───────┬───────┘   └───────┬────────┘ └────────┬─────────┘
                │ MCP               │ MCP               │ MCP
                ▼                   ▼                   ▼
           ATS/search           Calendar API         Vendor BGC API
                │                     │                   │
                └─────────────────────┴───────────────────┘
                          PERSISTENCE: Task store + audit log
                          TELEMETRY: taskId/contextId/corr → SIEM
                          Updates: SSE short steps | push+poll BGC
```

Technology: A2A **1.0** HTTP+JSON at edge, gRPC optional internal; signed Agent Cards; OIDC; Artifacts for candidate packets; push for BGC with SSRF allowlist; MCP only inside each specialist.

**Trade-off matrix**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **A1 Mega-agent (no A2A)** | High context/tools | High on broad workflows | Low integration count | Single domain; IP leak risk | Vertical only |
| **A2 Custom REST per vendor** | High eng N×M | Tunable | High maintenance | Per-integration auth | Poor reuse |
| **A3 A2A centralized orchestrator (recommended)** | Medium coord + specialist tokens | Predictable; SSE/push by stage | Medium (Cards, auth, breakers) | Opaque + OAuth/mTLS + scoped Get | Horizontal specialists |

**Decision rationale**: **A3** wins — Google’s own launch narrative is multi-agent hiring across systems; A2A removes N×M glue while preserving opaque vendor IP. Mega-agent fails compliance/tool sprawl; custom REST does not amortize across 100+ ecosystem partners ([Google launch](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/); [Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/)).

---

### Scenario B — Multi-tenant SaaS agent marketplace endpoint

**Problem**: Host many customer-owned specialist agents behind **one** platform endpoint with tenant isolation. Customers publish Cards (skills, bindings, rate tiers). Callers from other tenants must not learn task existence via Get/List. Mix of JSON-RPC and HTTP+JSON clients during **0.3→1.0** migration. Push webhooks to customer URLs require SSRF hardening. Target: thousands of Tasks/hour/tenant bursts without cross-tenant noisy-neighbor collapse ([Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/); [A2A Spec §13](https://a2a-protocol.org/latest/specification/)).

**Proposed architecture**

```
                    ┌────────────────────────────────────────┐
                    │ CONTROL PLANE — Platform Gateway       │
                    │ A2A-Version · tenant echo · OAuth/mTLS │
                    │ Card registry · JWS verify · quotas    │
                    │ SSRF allowlist for push URLs           │
                    └──────────────────┬─────────────────────┘
                                       │ route by tenant+skill
                 ┌─────────────────────┼─────────────────────┐
                 ▼                     ▼                     ▼
        ┌────────────────┐   ┌────────────────┐   ┌────────────────┐
        │ Tenant A cell  │   │ Tenant B cell  │   │ Tenant C cell  │
        │ A2A Server     │   │ A2A Server     │   │ A2A Server     │
        │ Task DB shard  │   │ Task DB shard  │   │ Task DB shard  │
        │ MCP tools      │   │ MCP tools      │   │ MCP tools      │
        └───────┬────────┘   └───────┬────────┘   └───────┬────────┘
                │                    │                    │
                └────────────────────┴────────────────────┘
                     TELEMETRY: per-tenant RPS, 503+Retry-After
                     PERSISTENCE: sharded tasks; immutable audit
                     Dual interfaces: 0.3 + 1.0 on Card
```

Technology: `AgentInterface.tenant` opaque routing string echoed on every request ([A2A Spec §4.4.6](https://a2a-protocol.org/latest/specification/)); authorize **before** DB lookup ([A2A Spec §13.1](https://a2a-protocol.org/latest/specification/)); signed Cards; push SSRF guards ([A2A Spec §13.2](https://a2a-protocol.org/latest/specification/)); extended Card for authenticated rate-limit disclosure ([A2A Spec §13.3](https://a2a-protocol.org/latest/specification/)).

**Trade-off matrix**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **B1 Shared DB, app-level tenant filter** | Lowest infra | Low | Easy to get wrong | IDOR / existence leaks | Scales until blast radius |
| **B2 Decentralized swarm of customer agents (no gateway)** | Variable | Variable | Hard tracing/trust | End-to-end authz weak | Flexible, opaque failures |
| **B3 Gateway + per-tenant cell (recommended)** | Medium (cells) | +1 hop at edge | Higher (mesh, quotas) | Strong isolation; SSRF/Card verify | Horizontal per tenant |

**Decision rationale**: **B3** matches v1.0 multi-tenancy + heterogeneous bindings goals. B1 fails the spec’s “authorize before query / indistinguishability” bar under real IDOR pressure. B2 swarm lacks marketplace governance and SSRF/audit centralization ([Announcing v1.0](https://a2a-protocol.org/v1.0.0/announcing-1.0/); [A2A Spec §13.1–13.2](https://a2a-protocol.org/latest/specification/); [System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol)).

---

### Interview prompts (after both scenarios)

1. Walk Card discovery → auth → `SendMessage` → SSE vs poll vs push → Artifact; where does MCP sit?
2. Why is push **10–30 s** not a latency SLA? What is your p99 budget when the remote LLM dominates?
3. Design `messageId` idempotency when the client retries after a timeout and the server already created a Task.
4. Place the circuit breaker: inside the remote agent or on the orchestrator? Why?
5. How do you enforce Zero-Trust on a JWS-signed Card that advertises `pushNotifications` to a URL you do not control?
6. Compare opaque A2A specialist vs in-process tool for a PII-heavy HR workflow — which NFR flips the decision?

---

## Sources

- [A2A Spec 1.0.0](https://a2a-protocol.org/latest/specification/) · [v1.0 announcement](https://a2a-protocol.org/v1.0.0/announcing-1.0/) · [A2A home](https://a2a-protocol.org/latest/)
- [Streaming & async](https://a2a-protocol.org/dev/topics/streaming-and-async/) · [GitHub a2aproject/A2A](https://github.com/a2aproject/A2A)
- [Google A2A launch](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/) · [LF donation](https://www.linuxfoundation.org/press/linux-foundation-launches-the-agent2agent-protocol-project-to-enable-secure-intelligent-communication-between-ai-agents)
- [System Design One #158](https://newsletter.systemdesign.one/p/agent-to-agent-protocol) · [0.3 upgrade](https://cloud.google.com/blog/products/ai-machine-learning/agent2agent-protocol-is-getting-an-upgrade)
