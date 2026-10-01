# Module 16: How A2A Protocol Works

### What Is This?

A2A (Agent-to-Agent) is an open interoperability protocol that lets AI agents built on different frameworks, by different vendors, discover each other, delegate stateful tasks, exchange messages, and coordinate work -- all without exposing their internal reasoning or tools. Think of it like HTTP for the web: before HTTP, every computer-to-computer handshake was custom glue; A2A does the same thing for AI agents. Google launched A2A on 9 April 2025 with 50+ partners, donated it to the Linux Foundation on 23 June 2025 with 100+ companies, and v1.0 (March 2026) is the first stable, production-ready release with 150+ supporting organizations, 22K GitHub stars, and SDKs in 5 languages. IBM's competing ACP merged into A2A in August 2025 -- there is effectively no alternative standard as of mid-2026. The cardinal rule: **MCP inside agents (for tools), A2A between agents (for coordination)**.

---

## 1. System Topology & Data Flow

### 1.1 Five-Plane Architecture

A production A2A deployment spans five cooperating planes: a **control plane** handling agent discovery, identity verification, and authorization before any task is accepted; a **data plane** routing task messages through load balancers, API gateways, and circuit breakers; **agent runtimes** encapsulating each agent's internal reasoning, MCP tool access, and LLM inference; a **persistence layer** maintaining task state, context continuity, and push notification configurations; and a **telemetry layer** providing distributed tracing across agent boundaries.

```
+------------------------------------------------------------------------------+
|                              CONTROL PLANE                                    |
|                                                                              |
|  +-------------------+   +------------------+   +------------------------+  |
|  | Client Agent       |   | Agent Card        |   | Auth Server            |  |
|  | (Orchestrator,     |-->| Registry          |-->| (OAuth 2.1 / mTLS /   |  |
|  |  LangGraph,        |   | (/.well-known/    |   |  OIDC discovery;      |  |
|  |  CrewAI, ADK,      |   |  agent-card.json  |   |  scoped per caller    |  |
|  |  custom runtime)   |   |  per RFC 8615;    |   |  per skill -- never   |  |
|  |                    |   |  signed via JWS   |   |  blanket access)      |  |
|  | Discovers remote   |   |  / RFC 7515)      |   |                       |  |
|  | agents, sends      |   +--------+---------+   +-----------+-----------+  |
|  | tasks, coordinates |            | discovery                | token issue  |
|  +--------+----------+            | + verification            |              |
|           | SendMessage    +------v-----------+               |              |
|           | GetTask        | Extended Agent    |<--------------+              |
|           | CancelTask     | Card Endpoint     |                              |
|           | Subscribe      | (auth-gated;      |                              |
|           |                | reveals hidden     |                              |
|           |                | skills / caps)     |                              |
|           |                +--------+----------+                              |
+-----------+-------------------------+----------------------------------------+
            | authorized request      | extended capabilities
+-----------v-------------------------v----------------------------------------+
|                     DATA PLANE  (API GATEWAY / MESH)                          |
|                                                                              |
|  +------------+  +---------------+  +-------------+  +--------------------+  |
|  | Protocol    |  | Per-Agent      |  | Rate        |  | A2A-Version        |  |
|  | Binding     |  | Circuit        |  | Limiter     |  | Negotiation        |  |
|  | Router      |->| Breaker        |->| + Quota     |->| (header-based;     |  |
|  | (JSON-RPC   |  | (per remote    |  | Manager     |  |  empty = v0.3      |  |
|  |  2.0 /      |  |  agent, not    |  | (prevents   |  |  fallback)         |  |
|  |  gRPC /     |  |  per task)     |  |  cascade)   |  |                    |  |
|  |  REST)      |  |               |  |             |  |                    |  |
|  +------+------+  +------+-------+  +------+------+  +--------+-----------+  |
+---------+----------------+----------------+--------------------+-------------+
          | JSON-RPC/HTTPS  | gRPC/Proto     | HTTP/JSON/REST
+---------v----------------v----------------v--------------------v-------------+
|                     AGENT RUNTIMES  (per remote agent)                        |
|                                                                               |
|  +------------------+  +------------------+  +--------------------------+    |
|  | Agent A           |  | Agent B           |  | Agent C                  |    |
|  | (Vertex AI Agent  |  | (Bedrock Agent-   |  | (Custom Python agent,    |    |
|  |  Builder; exposes |  |  Core; different  |  |  Google ADK; exposes     |    |
|  |  skills via Agent |  |  vendor, own LLM, |  |  skills, uses MCP        |    |
|  |  Card; internal   |  |  own MCP tools)   |  |  tools internally)       |    |
|  |  MCP tool access) |  |                   |  |                          |    |
|  +--------+---------+  +----------+--------+  +------------+-------------+    |
+-----------+------------------------+------------------------+----------------+
            | task state             | artifacts              | push notifs
+-----------v------------------------v------------------------v----------------+
|                           PERSISTENCE LAYER                                   |
|                                                                               |
|  +------------------+  +-------------------+  +----------+  +-------------+  |
|  | Task State Store  |  | Context Registry   |  | Push     |  | Artifact    |  |
|  | (9-state machine; |  | (contextId groups  |  | Notif    |  | Store       |  |
|  |  server-generated |  |  related tasks;    |  | Config   |  | (Parts:     |  |
|  |  taskId; survives |  |  referenceTaskIds  |  | (webhook |  |  text,      |  |
|  |  reconnection)    |  |  for cross-task    |  |  URLs;   |  |  file,      |  |
|  |                   |  |  dependencies)     |  |  persists|  |  data)      |  |
|  +------------------+  +-------------------+  |  until   |  |             |  |
|                                                |  done)   |  |             |  |
|                                                +----------+  +-------------+  |
+----------------------------------+--------------------------------------------+
                                   |
+----------------------------------v--------------------------------------------+
|                    TELEMETRY / OBSERVABILITY SINKS                             |
|  OpenTelemetry with W3C Trace Context (traceparent/tracestate) across all     |
|  agent hops  |  taskId + contextId + messageId correlation  |  per-agent      |
|  latency breakdown  |  circuit-breaker state dashboard  |  push notification  |
|  delivery tracking  |  authz deny / SSRF deny logging  |  cross-boundary     |
|  error attribution  |  API gateway centralized policy enforcement             |
+-----------------------------------------------------------------------------------+
```

**Plane responsibilities**

| Plane | What Lives Here | Truth Source |
|---|---|---|
| **CONTROL PLANE** | Client/orchestrator: Card discovery + JWS verification, auth, skill routing, version/tenant headers, breaker + fallback policy, HITL on interrupted states | A2A Spec SS2.2, SS8 |
| **DATA PLANE** | Wire ops over JSON-RPC / gRPC / HTTP+JSON; per-agent circuit breakers; rate limiters; A2A-Version negotiation; protocol binding routing | A2A Spec SS3, SS5 |
| **AGENT RUNTIMES** | Each agent's internal reasoning, MCP tool access, LLM inference; opaque to callers | A2A Spec SS1.2 |
| **PERSISTENCE** | Server-side Task state (9-state machine), history, artifacts; context registry; push notification config; webhook outbox | A2A Spec SS4.1 |
| **TELEMETRY** | `taskId`, `contextId`, `messageId` correlation; W3C Trace Context across hops; breaker state; authz/SSRF events | Spec SS1.2, SS13.4; layered via OpenTelemetry |

### 1.2 Three-Layer Protocol Stack

The v1.0 spec is organized into three layers, each decoupled from the others. The canonical data model is the single source of truth; operations are binding-agnostic; wire format is a deployment decision.

```
+-----------------------------------------------------------------+
| Layer | Purpose                | Normative Source / Details       |
|-------+------------------------+----------------------------------|
| L1    | Canonical Data Model   | Protocol Buffers (a2a.proto).    |
|       | (core structures)      | All SDK bindings regenerated     |
|       |                        | from proto. Defines: Task,       |
|       |                        | Message, Artifact, Part,         |
|       |                        | Context, AgentCard, Extension    |
|-------+------------------------+----------------------------------|
| L2    | Abstract Operations    | 11 operations:                   |
|       | (capabilities)         | SendMessage, SendStreamingMsg,   |
|       |                        | GetTask, ListTasks, CancelTask,  |
|       |                        | SubscribeToTask, 4x push notif   |
|       |                        | CRUD, GetExtendedAgentCard       |
|-------+------------------------+----------------------------------|
| L3    | Protocol Bindings      | JSON-RPC 2.0 / HTTPS (primary)  |
|       | (wire protocols)       | gRPC with protobuf (high-perf)   |
|       |                        | HTTP/JSON/REST (simple fallback) |
|       |                        | Choice is deployment, not design |
+-----------------------------------------------------------------+
```

**`spec/a2a.proto` is the single authoritative normative definition; generated JSON Schema is non-normative.**

### 1.3 End-to-End Request-Flow Narrative

1. **Discover Agent Card (CONTROL PLANE)** -- Client fetches `https://{server}/.well-known/agent-card.json` (or registry / preconfigured URL). Reads `skills[]`, `supportedInterfaces[]` (`protocolBinding` is one of {`JSONRPC`, `GRPC`, `HTTP+JSON`}, `protocolVersion`, optional `tenant`), `capabilities` (`streaming`, `pushNotifications`, `extendedAgentCard`), `securitySchemes` / `securityRequirements`, optional JWS `signatures[]`. If `extendedAgentCard: true`, authenticated `GetExtendedAgentCard` SHOULD replace the public cache for the session. **Verifies JWS signature against declared key using JSON canonicalization (RFC 8785) before trusting claims.**

2. **Authenticate** -- Acquire credentials out-of-band per Card schemes (API key, HTTP Bearer, OAuth 2.0, OIDC, mTLS). Attach on every request; echo `AgentInterface.tenant` when set. Send `A2A-Version: 1.0` header.

3. **Send Message / Task (DATA PLANE)** -- `SendMessage` / `POST /message:send` with `Message` (`messageId`, `role`, `parts[]`, optional `contextId`). Server MAY return a **Task** (async work; server-generated `taskId`) or a direct **Message**. Default blocking waits until terminal or interrupted (`INPUT_REQUIRED` / `AUTH_REQUIRED`) unless `return_immediately: true`.

4. **Stream, Poll, or Push (DATA PLANE updates)** -- If `capabilities.streaming`: SSE via `SendStreamingMessage` / `SubscribeToTask` for `Task` then `TaskStatusUpdateEvent` / `TaskArtifactUpdateEvent`. Else **poll** `GetTask`. If `pushNotifications`: register push-config; server webhooks client (timeout guidance **10-30 s**; at-least-once delivery; client MUST HTTP 2xx and SHOULD be idempotent).

5. **Artifact delivery (PERSISTENCE -> client)** -- Terminal success yields `artifacts[]` (output deliverables). **Prefer Artifacts for results; Messages are dialogue/HITL, not the primary result channel.** `ListTasks` defaults `includeArtifacts: false` to shrink payloads.

6. **Tool work (AGENT RUNTIMES, opaque)** -- Remote agent may call MCP tools / SaaS behind its boundary; A2A never exposes that surface to the client.

7. **Telemetry** -- Propagate `traceparent`/`tracestate` (W3C Trace Context) and log `taskId` / `contextId` / `messageId` / correlation ID every hop; breaker and authz outcomes on the orchestrator.

### 1.4 Orchestration Topologies

A2A is a **wire protocol**, not an orchestration model. Common deployment patterns:

- **Centralized Orchestrator** -- One lead agent discovers specialists via Agent Cards, delegates tasks via A2A, stitches results. Easier to reason about, debug, and audit. **Default for most enterprise workflows.**
- **Decentralized Swarm** -- Peers discover and hand off without a single controller. More flexible; harder tracing and trust management. Most real systems start centralized and evolve toward swarm as trust systems mature.

### 1.5 MCP-A2A Complementary Relationship

```
+-------------+--------------------------+---------------------------+
| Dimension    | MCP                       | A2A                        |
|-------------+--------------------------+---------------------------|
| Connects     | Agent to tools/data       | Agent to agent             |
|              | (vertical)                | (horizontal/peer)          |
| Analogy      | USB port for peripherals  | HTTP for web services      |
| Launched by  | Anthropic (Nov 2024)      | Google (Apr 2025)          |
| Governance   | Linux Foundation (AAIF)   | Linux Foundation (AAIF)    |
| 2026 scale   | 97M monthly SDK downloads | 150+ orgs, 22K GH stars   |
| When to use  | Agent needs tools, data,  | Multiple agents crossing   |
|              | context                   | ownership boundaries       |
+-------------+--------------------------+---------------------------+
```

**The reference architecture**: MCP inside agents (for tool and data access), A2A between agents (for coordination). Google ADK, Salesforce Agentforce, and ServiceNow Now Assist all implement both. The AAIF (Agentic AI Foundation, Dec 2025) -- co-founded by OpenAI, Anthropic, Google, Microsoft, AWS, Block, 190 member orgs as of May 2026 -- governs both protocols under neutral governance.

**Decision rule**: Start with one agent + MCP tools. Add A2A only when you have genuine reasons for agent autonomy and specialization across organizational boundaries. Multi-agent systems are harder to debug, more expensive to run, and slower to respond.

### 1.6 Five Design Principles (v1.0)

1. **Simple** -- Reuses HTTP, JSON-RPC 2.0, SSE. No new transport layer.
2. **Enterprise Ready** -- Built-in auth, authorization, security, privacy, tracing, monitoring.
3. **Async First** -- Built for long-running tasks and human-in-the-loop. From quick queries to deep research taking hours/days.
4. **Modality Agnostic** -- Text, audio, video, structured data, forms, embedded UI components.
5. **Opaque Execution** -- Agents collaborate by sharing skills and outputs without revealing internal reasoning, plans, or tool implementations.

### 1.7 Stewardship and Ecosystem

| Milestone | Date | Detail |
|---|---|---|
| Google launches A2A | 9 Apr 2025 | 50+ technology partners (Atlassian, Box, Cohere, Intuit, LangChain, MongoDB, PayPal, Salesforce, SAP, ServiceNow, UKG, Workday, plus major GSIs) |
| A2A donated to Linux Foundation | 23 Jun 2025 | 100+ supporting companies |
| IBM ACP merges into A2A | Aug 2025 | Eliminates competing standard |
| AAIF founded | Dec 2025 | Co-founded by OpenAI, Anthropic, Google, Microsoft, AWS, Block |
| A2A v1.0 stable release | Mar 2026 | First production-ready release; 150+ orgs |
| Technical Steering Committee | Current | AWS, Cisco, Google, IBM Research, Microsoft, Salesforce, SAP, ServiceNow |

**Framework compatibility (2026)**: LangGraph, CrewAI, Vertex AI Agent Builder, AutoGen, Google ADK, Microsoft Agent Framework, Amazon Bedrock AgentCore, Salesforce Agentforce, ServiceNow Now Assist -- existing agents can be A2A-enabled with minimal code changes.

---

## 2. Core Mechanics & Algorithms

### 2.1 Core Data Objects

| Concept | Role |
|---|---|
| **Task** | Stateful unit of work; server-generated `id`; `status`, optional `artifacts[]`, `history[]`, `contextId`, `metadata` |
| **Message** | Communication turn: `messageId`, `role` (`ROLE_USER` or `ROLE_AGENT`), `parts[]`, optional `taskId` / `contextId` / `referenceTaskIds`. **NOT for task outputs. NOT guaranteed persisted.** |
| **Part** | Smallest content unit -- exactly one of `text` / `raw` (bytes/base64) / `url` / `data` (JSON); optional `mediaType`, `filename` |
| **Artifact** | Task **output** deliverable: `artifactId`, `parts[]`. **THE reliable delivery mechanism for results.** Can be streamed. |
| **contextId** | Groups related tasks/messages (session continuity). Server-generated, opaque to clients. |
| **referenceTaskIds** | Explicit cross-task dependencies for complex workflows |

### 2.2 Data Model Hierarchy

```
                          +-------------+
                          |   Context    |
                          | (contextId)  |
                          |  groups      |
                          |  related     |
                          |  tasks       |
                          +------+------+
                                 | 1:N
                          +------v------+
                          |    Task      |
                          | (taskId)     |<-- referenceTaskIds
                          |  server-gen  |    (cross-task deps)
                          |  stateful    |
                          +--+-------+--+
                             |       |
                    1:N      |       |   1:N
               +-------------+       +-------------+
               |                                     |
        +------v------+                      +-------v------+
        |   Message    |                      |   Artifact    |
        | role: USER   |                      | (task output; |
        |   or AGENT   |                      |  the reliable |
        |              |                      |  delivery     |
        | NOT for task |                      |  mechanism)   |
        | outputs.     |                      |               |
        | NOT reliably |                      | CAN be        |
        | persisted.   |                      | streamed.     |
        +------+------+                      +-------+------+
               | 1:N                                  | 1:N
        +------v------+                      +-------v------+
        |    Part      |                      |    Part       |
        | - TextPart   |                      | - TextPart    |
        | - FilePart   |                      | - FilePart    |
        |   (URI or    |                      |   (URI or     |
        |    inline)   |                      |    inline)    |
        | - DataPart   |                      | - DataPart    |
        |   (JSON)     |                      |   (JSON)      |
        +-------------+                      +--------------+
```

**Critical distinction**: Messages are for communication (initiating tasks, clarification, status updates). Artifacts are for task outputs (documents, data, results). Using Messages to deliver task results is a spec violation.

### 2.3 Agent Card Structure

```
+---------------------------------------------------------------------+
|                        AGENT CARD STRUCTURE                          |
|---------------------------------------------------------------------|
| Field                    | Purpose                                   |
|--------------------------|-------------------------------------------|
| name, description        | Human-readable identity                   |
| version                  | Card version for cache invalidation       |
| provider                 | Organization info                         |
| serviceEndpoint          | URL where this agent accepts tasks        |
| supportedInterfaces[]    | url + protocolBinding + protocolVersion    |
|                          | + optional tenant                         |
| skills[]                 | ID, description, tags, examples per skill |
| defaultInput/OutputModes | MIME types (text, audio, video, JSON)     |
| securitySchemes          | Auth requirements (OpenAPI-aligned)       |
| capabilities             | streaming, pushNotifications,             |
|                          | extendedAgentCard, extensions flags       |
| agentInterface.tenant    | Multi-tenancy routing string              |
| agentCardSignature       | JWS (RFC 7515) over JSON-canonical       |
|                          | (RFC 8785) card body                      |
| extensions[]             | Extension URIs                            |
+---------------------------------------------------------------------+
```

**Extended Agent Cards** are served behind authentication via `GetExtendedAgentCard` (`GET /extendedAgentCard`). They allow agents to advertise sensitive or internal-only skills exclusively to authorized callers. When fetched, the extended card replaces the cached public card for the duration of the authenticated session.

**Signed Agent Cards** solve the trust-at-discovery problem. Without signatures, a malicious agent can serve a card claiming skills it does not have or misrepresenting its identity. The verification flow:
1. Client fetches card from `/.well-known/agent-card.json`
2. Client extracts the `agentCardSignature` (JWS per RFC 7515)
3. Client canonicalizes the card body per RFC 8785 (stripping the signature field)
4. Client verifies the signature against the declared key (via `jku` JWKS endpoint)
5. Verification failure = card rejected, agent untrusted

### 2.4 Task Lifecycle State Machine

Nine states with strict transition constraints. Terminal states are irreversible. Interrupted states enable human-in-the-loop workflows.

```
                               +-------------------------------+
                               |         UNSPECIFIED            |
                               |  (unknown / recovery state)   |
                               +-------------------------------+


  +-----------+     +----------+     +--------------+
  | SUBMITTED  |---->| WORKING  |---->|  COMPLETED   |  (terminal)
  +-----+-----+     +----+-----+     +--------------+
        |                 |
        |                 +---------->+--------------+
        |                 |           |   FAILED     |  (terminal)
        |                 |           +--------------+
        |                 |
        |                 +---------->+--------------+
        |                 |           |  CANCELED    |  (terminal)
        |                 |           +--------------+
        |                 |
        +---------------->+---------->+--------------+
        | (at submission   |           |  REJECTED    |  (terminal)
        |  or later)       |           +--------------+
        |                  |
        |                  +---------->+--------------------+
        |                  |           | INPUT_REQUIRED     | (interrupted)
        |                  |           |  client sends new  |--+
        |                  |           |  message to resume |  |
        |                  |           +--------------------+  |
        |                  |                                    |
        |                  +---------->+--------------------+  |
        |                  |           | AUTH_REQUIRED      |  |
        |                  |           |  client provides   |--+
        |                  |           |  credentials       |  |
        |                  |           +--------------------+  |
        |                  |                                    |
        |                  |<-----------------------------------+
        |                  |  (resume: new message with same
        |                  |   taskId and contextId)
        +------------------+
```

**Transition rules**:
- **Terminal states** (COMPLETED, FAILED, CANCELED, REJECTED) cannot accept further messages -- server returns `UnsupportedOperationError`
- **Interrupted states** (INPUT_REQUIRED, AUTH_REQUIRED) resume via new message with same `taskId` and `contextId`
- REJECTED can fire during initial creation OR after the agent determines it cannot proceed
- Streams MUST close when task reaches terminal state
- `SubscribeToTask` on a terminal-state task returns `UnsupportedOperationError`
- `CancelTask` on a terminal task returns `TaskNotCancelableError`
- `SubscribeToTask` MUST return current state as the first event (for late-joining observers)
- `contextId` + `taskId` mismatch MUST reject

### 2.5 Communication Patterns

```
+-------------------+--------------------------------------------------------+
| Pattern            | Mechanics                                              |
|-------------------+--------------------------------------------------------|
| 1. Synchronous    | returnImmediately: false (default). Client blocks      |
|    request-       | until terminal or interrupted state. Best for quick    |
|    response       | tasks under ~5s.                                       |
|-------------------+--------------------------------------------------------|
| 2. SSE Streaming  | message/stream or tasks/subscribe. Server streams      |
|                   | StreamResponse objects (exactly one of: task,          |
|                   | message, statusUpdate, artifactUpdate). Events MUST    |
|                   | be delivered in generation order, MUST NOT reorder.    |
|                   | Broadcast to all active streams. Stream MUST close     |
|                   | at terminal state. Closing one stream MUST NOT         |
|                   | affect others.                                         |
|-------------------+--------------------------------------------------------|
| 3. Push Notifs    | Async HTTP POST to client-provided webhook. Best for   |
|    (webhooks)     | long-running tasks (hours/days). Payloads are          |
|                   | StreamResponse as JSON regardless of binding.          |
|                   | Config persists until task completion or explicit      |
|                   | deletion. Webhook endpoints MUST require auth.         |
|                   | Timeout guidance: 10-30 s. At-least-once delivery.    |
|-------------------+--------------------------------------------------------|
| 4. Non-blocking   | returnImmediately: true. Returns instantly. Caller     |
|    poll           | follows up via GetTask, SubscribeToTask, or push.     |
+-------------------+--------------------------------------------------------+
```

**Streaming sub-patterns**:
- **Message-only stream**: One `Message`, then close
- **Task lifecycle stream**: Initial `Task`, then status/artifact events (`append` / `lastChunk` for chunked artifacts); close on terminal
- **Multi-stream fan-out**: Agent MAY serve multiple concurrent streams per task; events MUST broadcast in order; closing one MUST NOT affect others

**Complexity**: Poll interval p over duration T produces O(T/p) GetTask calls; SSE is O(E) events. Nested A2A depth d multiplies coordination RTTs approximately O(d) and risks context duplication.

### 2.6 Method Mapping Across Bindings

| Operation | JSON-RPC / gRPC | REST |
|---|---|---|
| Send | `SendMessage` | `POST /message:send` |
| Stream | `SendStreamingMessage` | `POST /message:stream` |
| Get / List / Cancel | `GetTask` / `ListTasks` / `CancelTask` | `GET /tasks/{id}`, `GET /tasks`, `POST /tasks/{id}:cancel` |
| Subscribe | `SubscribeToTask` | `POST /tasks/{id}:subscribe` |
| Push config | 4x CRUD operations | Standard REST CRUD |
| Extended card | `GetExtendedAgentCard` | `GET /extendedAgentCard` |

**Version header**: Clients MUST send `A2A-Version: 1.0`. Empty header is interpreted as **0.3**. Agent Cards can advertise both `0.3` and `1.0` interfaces for progressive migration.

### 2.7 Client-Server Role Duality

Any agent can play both roles simultaneously. A coding agent is a **client** when delegating review to a QA agent, but a **remote (server)** agent when a PM agent requests code generation. This duality means the same agent runtime must handle both outbound discovery/delegation and inbound task acceptance.

### 2.8 Key Invariants

1. **Server owns `taskId` and `contextId`** -- clients MUST NOT invent IDs for new tasks
2. **Opaque execution** -- clients reason over skills + exchanged Parts/Artifacts, never remote prompts/tools
3. **Modality agnostic** -- text, audio, video, structured data, all via Parts with MIME types
4. **Context consistency** -- Agent MUST reject messages where `contextId` and `taskId` mismatch
5. **Artifact-only outputs** -- Task results go in Artifacts, not Messages
6. **Capability honesty** -- stream/push without Card flags produces `UnsupportedOperationError` / `PushNotificationNotSupportedError`
7. **Messages are not durable** -- persist important content in Task history; SSE drops can lose transient status Messages

---

## 3. Token Economics & NFR Analysis

> **Gap**: A2A is an interoperability protocol, not a model API. The v1.0 specification publishes **no official p50/p95/p99 latency SLAs**, **no** token-cost formulas, **no** RPM/TPM quotas, and **no** `$ per 1k tasks` meter. A2A traffic is **unmetered** by the protocol. Numbers below are architectural inferences with labeled assumptions -- not vendor benchmarks.

### 3.1 Where the Cost Actually Lives

A2A introduces latency and cost, but the wire protocol itself is not the bottleneck. The dominant cost is **agent reasoning overhead**: parsing natural language instructions, formulating a plan, executing LLM inference and tool calls, and formulating a structured response.

```
+----------------------------------+-----------------------------------+
| Component                         | Relative Cost                     |
|----------------------------------+-----------------------------------|
| LLM inference (remote agent)      | 85-95% of total hop cost          |
|   - Input tokens (instructions)   |   ~40% of inference cost          |
|   - Output tokens (response)      |   ~50% of inference cost          |
|   - Reasoning tokens (if o-series)|   variable (3-10x multiplier)     |
| MCP tool calls within agent       | 3-10% (API/DB costs)             |
| A2A wire format overhead          | < 1% (HTTP headers, JSON-RPC     |
|   - A2A-Version header            |  envelope, Agent Card caching)    |
|   - application/a2a+json body     |                                   |
|   - A2A-Extensions header         |                                   |
| Network/infrastructure            | < 1%                              |
+----------------------------------+-----------------------------------+
```

### 3.2 Cost Formula -- Two Perspectives

**Both formulas below are valid** -- they use different assumptions. Choose the one matching your deployment profile.

**Formula A: Conservative (single-hop, with prompt cache + fallback)**

Assumptions: mid-tier model ($3/$15 per M in/out tokens), 4K input / 800 output tokens per remote task, 60% prompt-cache hit rate (0.1x price on cached portion), 85% remote / 15% local fallback split, small-tier local model ($0.40/$1.60).

Per-task remote model cost:

```
c_R = [(1-H) * T_in * P_in + H * T_in * P_in * D_cache] / 10^6
      + [T_out * P_out] / 10^6

With numbers:
  Effective input = 0.4 * 4000 + 0.6 * 4000 * 0.1 = 1840 tokens
  c_R = (1840 * 3 + 800 * 15) / 10^6 = $0.01752

Local fallback:
  c_L = (2500 * 0.40 + 400 * 1.60) / 10^6 = $0.00164

$ per 1K tasks = 1000 * (0.85 * 0.01752 + 0.15 * 0.00164) = ~$15.14
```

**Formula B: Realistic multi-hop (2-hop chain, no cache, conservative model)**

Assumptions: 2 hops per task (orchestrator -> specialist), 5K combined tokens per hop, Claude Sonnet ($3/M input, $15/M output), 40/60 input/output split (blended ~$10.20/M), $0.005/hop MCP tool cost.

```
Cost per task = 2 hops * 5000 tokens * $10.20/1M + $0.01 MCP = ~$0.112
$ per 1K tasks = ~$112
```

**Comparison table**:

| Parameter | Formula A ($15.14/1K) | Formula B ($112/1K) |
|---|---|---|
| Hops per task | 1 | 2 |
| Tokens per hop | 4,800 (in+out) | 5,000 (combined) |
| Cache hit rate | 60% at 0.1x price | None |
| Fallback rate | 15% to cheap local | None |
| Model tier | Mid ($3/$15) | Claude Sonnet ($3/$15) |
| MCP tools | Not counted | $0.005/hop |
| **Best represents** | **Mature deployment with cache/fallback** | **Greenfield multi-hop without optimization** |

**Key cost drivers**: (1) Hop count -- each additional hop roughly doubles LLM cost; (2) Reasoning-heavy models (o-series) can 3-10x the token count per hop; (3) Retry storms under transient failures multiply cost without delivering value; (4) Prompt caching at 60% hit rate cuts effective input cost by ~50%.

### 3.3 Latency -- Official Gap + Inferred Budget

> **Gap**: No official published p50/p95/p99 for A2A Card fetch, task RTT, or SSE. Push webhook **10-30 s** is a **delivery timeout budget** for the webhook HTTP call -- **not** an end-to-end task latency SLA.

**Inferred latency budget (short specialist task, same-region HTTP+JSON)**

| Stage | p50 (ms) | p95 (ms) | p99 (ms) | Notes |
|---|---|---|---|---|
| Agent Card fetch (cold) | 40 | 120 | 300 | Well-known GET; cacheable |
| Agent Card fetch (warm cache) | 1 | 2 | 5 | Local TTL cache |
| Auth token attach / validate | 5 | 20 | 80 | JWKS/introspection cached |
| Task RTT -- SendMessage framing | 30 | 80 | 180 | Network RTT-dominated |
| Remote agent LLM + tools | 800 | 3,000 | 12,000 | **Dominates e2e** |
| SSE first event after accept | 50 | 150 | 400 | Perceived latency win vs blocking |
| Poll GetTask (one cycle) | 30 | 80 | 180 | Plus poll interval waste |
| **E2E blocking short task (warm card)** | **~866** | **~3,182** | **~12,445** | Sum of warm path components |

**Arithmetic**: p50 warm path: 1 + 5 + 30 + 800 + 30 = 866 ms. p95: 2 + 20 + 80 + 3000 = 3,102 ms. p99: 5 + 80 + 180 + 12,000 = 12,265 ms.

**Multi-hop latency grows additively**: 2-hop sync = 4-60s, 3-hop sync = 6-90s. Beyond 3 hops, synchronous patterns produce unacceptable user-facing latency. **Design A2A communications asynchronously for 3+ hops using push notifications and long-running task management. Parallel fan-out to independent agents reduces wall-clock time.**

**Mitigation by tier**:

| Tier | Target | Mitigations |
|---|---|---|
| **p50** | Card + framing much less than model time | Aggressive Card cache; gRPC for internal mesh; `return_immediately` + SSE for perceived latency |
| **p95** | Cap remote tool tails | Per-task deadline; skill-scoped specialists; limit hop depth; prefer SSE over chatty poll |
| **p99** | Prevent cascade | Circuit breaker on remote agent; fallback local -> queue; CancelTask; stop webhook after consecutive failures then poll |

### 3.4 Throughput & Back-Pressure

- Agents SHOULD rate-limit all ops; MAY tier by user; extended cards MAY advertise quotas -- **no numeric defaults in spec**
- ListTasks: cursor pagination (`pageToken` / `nextPageToken`); default page size 50, min 1, max 100
- System overload MAY map to HTTP **503** + `Retry-After`
- **Inferred capacity knobs**: concurrent Tasks per tenant, SSE connection count, webhook fan-out QPS, orchestrator bulkhead per remote agent URL
- **Back-pressure chain**: reject new SendMessage at gateway -> open circuit -> fallback local/queue -> CancelTask on deadline burn
- Prefer gRPC for high-throughput internal meshes; JSON-RPC/HTTP+JSON for gateway familiarity

### 3.5 NFR Trade-Off Tables

**Trade-off A -- Opaque Remote A2A Agent vs In-Process Call**

| Dimension | Opaque Remote A2A Agent | In-Process / Same-Runtime Call |
|---|---|---|
| **Cost** | Specialist tokens + RTTs; smaller context per agent | Shared mega-context; often higher tokens |
| **Latency** | +1 RTT (or more) + remote queue; 2-30s per hop | Sub-ms IPC; no Card/auth overhead |
| **Ops** | Card, versioning, auth, breakers, distributed tracing | Deploy monolith / library |
| **Security** | Trust boundary; OAuth/mTLS; opaque IP protection | Single trust domain; tools visible |
| **Scalability** | Horizontal specialists across vendors | Vertical / same cluster only |
| **HITL** | Native via INPUT_REQUIRED | Manual implementation |
| **Long-running** | Native async state machine | Polling/callbacks |

**Decision rule**: Use A2A when the peer is another team/vendor/framework or must stay opaque. Use in-process for hot-path micro-skills inside one trust domain.

**Trade-off B -- Poll vs SSE vs Push**

| Dimension | Poll `GetTask` | SSE Stream | Push Webhook |
|---|---|---|---|
| **Cost** | Wasted polls (O(T/p) calls) | Long-lived connection | Notify + optional GetTask |
| **Latency** | Poll-interval lag | Best perceived (first token 1-5s) | Good for long tasks; notify is not SLA |
| **Ops** | Firewall-friendly | LB/proxy SSE support needed | Public callback URL, SSRF hardening |
| **Security** | Client-initiated only | Client-initiated | Server->client; auth webhook; SSRF risk |
| **Scalability** | High poll load at scale | Connection count limits | Fan-out; 10-30 s webhook timeout budget |

**Decision rule**: Short interactive -> SSE (if Card allows). Enterprise DMZ without inbound -> poll. Minutes-days -> push + poll fallback.

**Trade-off C -- A2A vs Direct API vs MCP Only**

| Dimension | Direct API Call | MCP Only | A2A Protocol |
|---|---|---|---|
| **Latency** | Sub-100ms | Single agent LLM | 2-30s per hop (agent reasoning) |
| **Complexity** | Minimal | Lower | Discovery, lifecycle, auth overhead |
| **Interoperability** | N^2 integrations | Single agent + tools | Vendor-neutral, linear scaling |
| **Cost** | One API call | One agent's inference | Multiple agents' inference per workflow |
| **Failure modes** | Standard HTTP | Tool failures (well-understood) | + silent delegation, cross-boundary smearing |
| **Human-in-the-loop** | Manual | Manual | Native via INPUT_REQUIRED state |
| **Long-running** | Polling/callbacks | N/A | Native async state machine |

### 3.6 NFR Targets for Production Deployments

**Availability chain rule**: Composite availability for an N-agent chain follows: **A_chain = A_1 x A_2 x ... x A_N**. For a 3-agent chain where each agent delivers 99.9% availability: 99.9%^3 = 99.7% -- roughly 26 hours of downtime per year instead of 8.7 hours. **Implication: minimize chain depth, front critical paths with fallback chains.**

| NFR Target | A2A Deployment Guidance |
|---|---|
| **Availability** | 99.9%^N for N agents in chain. Budget for weakest agent. Dual `0.3`/`1.0` interfaces during migration. Multi-instance A2A servers need shared Task store (or sticky routing). |
| **RPO** | Task state is server-owned and persisted. RPO = last persisted task state update. In-flight LLM reasoning between state transitions is lost on crash -- acceptable because the task can be re-submitted. Context TTL is implementation-defined. |
| **RTO** | Agent rediscovery (re-fetch Agent Card) + task resume via GetTask with stored taskId. Typical: seconds for warm agents, 1-5 minutes if cold-start + re-auth. |
| **Compliance** | MUST NOT log credentials/PII unprotected; provide deletion/retention; TLS 1.3+. EU AI Act: cross-org agent chains may constitute "AI systems" requiring risk classification. GDPR: data parts across org boundaries are data transfers requiring lawful basis + DPAs + potentially SCCs. Opaque execution helps IP isolation. |

---

## 4. Distributed Resilience & Security

### 4.1 Durable Execution -- Task as the Durability Unit

Unlike pure RPC, A2A **Tasks** are first-class durable work items: unique server `id`, lifecycle states, history, artifacts. Clients recover via:
- `GetTask` (poll / post-webhook)
- `SubscribeToTask` after SSE disconnect
- Push notifications with `StreamResponse` payloads

**Production pattern**: Persist Task rows keyed by `taskId`/`contextId` matching `TaskState`; webhook outbox with retry then client poll; circuit breakers **at the client orchestrator** around remote endpoints; document context TTL.

**Integration sketch**: Temporal/Kafka workflow holds orchestration steps; each activity is an A2A `SendMessage` + wait-for-terminal; checkpoint `taskId` for replay-safe resume; DLQ on permanent `FAILED`/`REJECTED`.

### 4.2 Idempotency of Task / Message Identifiers

| Operation | Idempotency Guarantee |
|---|---|
| `GetTask`, `ListTasks` | Naturally idempotent (read-only, safe to retry freely) |
| `SendMessage` | **MAY** be idempotent (dedup on `messageId` is agent-specific, not guaranteed by protocol). **This is the critical gap.** |
| `CancelTask` | Idempotent (repeated cancel on terminal = noop) |
| `DeletePushNotifConfig` | Idempotent (delete of absent config = noop) |
| `SetPushNotifConfig` | Idempotent (upsert semantics) |
| `GetPushNotifConfig` | Idempotent (read-only) |

**Invariant**: Retries MUST reuse the same `messageId` so the server can return the existing Task instead of creating duplicates. Client-side: maintain `messageId -> taskId` mapping for safe retry.

### 4.3 Circuit Breaker (Client -> Remote Agent)

```
  success                 cooldown elapsed
+--------+  failures>=N  +------+  probe   +-----------+
| CLOSED |--------------->| OPEN |---------->| HALF_OPEN |
+----^---+               +------+          +-----+-----+
     | success                                    |
     +--------------------------------------------+
              failure in HALF_OPEN -> OPEN
```

Apply breakers **per remote agent base URL / skill**, not inside the A2A protocol. This is a client orchestrator implementation choice.

### 4.4 Fallback Chain

**Remote specialist -> local agent -> queued task**

1. **Primary**: A2A to remote specialist (Card skill match)
2. **On OPEN breaker / permanent remote failure**: Run **local** agent (same trust domain, reduced skill)
3. **If local also fails or budget exhausted**: **Enqueue** durable task for later (outbox / work queue); surface `queued` to user; drain when breaker half-opens

### 4.5 Enterprise Authentication

Authentication is handled at the HTTP transport layer, not within A2A payloads. Identity flows through HTTP headers via standard mechanisms declared in Agent Cards.

| Scheme | Use Case |
|---|---|
| `APIKeySecurityScheme` | API key in header/query. Simplest. Internal/dev only. |
| `HTTPAuthSecurityScheme` | HTTP Bearer tokens. Service-to-service within same org. |
| `OAuth2SecurityScheme` | OAuth 2.0 (authorization code, client credentials, device code). Cross-org. |
| `OpenIdConnectSecurityScheme` | OIDC discovery. Enterprise SSO. |
| `MutualTlsSecurityScheme` | mTLS with X.509 certs. Strongest machine-to-machine identity. |

**Production recommendation**: mTLS between agents inside the same trust boundary; OAuth 2.1 with scoped tokens across organizational boundaries. Credentials MUST NOT share a root key -- scope per caller per skill.

**In-task authorization**: Agents can request credentials mid-task via `AUTH_REQUIRED` state, enabling just-in-time authorization for sensitive operations. Credentials SHOULD arrive **out-of-band** over HTTPS. In-band credential relay across multi-agent chains increases exposure -- if used, bind/encrypt credentials to the originating agent.

### 4.6 Zero-Trust Agent-to-Agent Communication

Every agent-to-agent message is treated as untrusted regardless of network position. Trust is established per-request, not per-connection:

1. **Identity verification**: Signed Agent Cards (JWS/RFC 7515) verify the remote agent's identity at discovery. Reject any card with invalid or missing signature before sending any task.
2. **Per-skill authorization**: OAuth 2.1 scopes are granted per caller per skill, not blanket agent access. An agent authorized for `check_stock` cannot invoke `reserve_inventory` without a separate scope grant.
3. **Credential scoping on delegation**: When an orchestrator delegates to a sub-agent, it issues a down-scoped token for only the skills needed. The sub-agent cannot use the orchestrator's broader credentials (**authorization creep prevention**).
4. **mTLS for transport identity**: Within the same trust boundary, mutual TLS provides machine-to-machine identity verification at the transport layer.
5. **Extended cards over public**: After auth, extended cards replace public cards for the session, revealing hidden capabilities only to verified callers.
6. **State transition is NOT authorization**: A task entering AUTH_REQUIRED does not authorize any operation.

### 4.7 Transport Security

- Production MUST use HTTPS/TLS; TLS **1.3+** recommended; HSTS SHOULD be enabled; disable SSLv3/TLS1.0/1.1
- Clients SHOULD verify server TLS certs against trusted CAs
- **Post-quantum cryptography (PQC) cipher suites recommended as they become available**
- All webhook endpoints for push notifications MUST use HTTPS
- IANA-registered media type: `application/a2a+json`

### 4.8 Replay Protection

The spec does not mandate replay protection by default. Recommended controls (ideally combined):

| Strategy | Mechanism |
|---|---|
| **Nonce** | UUIDv7 request ID (sorts by time, detects replayed IDs) |
| **Timestamp** | Date header validated within 5-minute clock skew window |
| **Integrity** | Message Authentication Codes (MAC) on request body |
| **High-value** | Sender-constrained tokens via mTLS-bound tokens (RFC 8705) |
| **Proof-of-possession** | DPoP (Demonstrating Proof of Possession, RFC 9449) |

### 4.9 SSRF Hardening on Push Webhooks

Webhook callers SHOULD reject private ranges, localhost, link-local; prefer allowlists:

Blocked ranges: `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16` (link-local), `localhost`, `::1`

Receivers MUST authenticate the server; SHOULD use timestamps/nonces against replay; rate-limit webhook flood.

### 4.10 PII in Message Parts

Spec: MUST NOT log credentials/PII unprotected; provide deletion/retention.

**Production pattern**: At trust-domain egress: **detect** PII in Parts (NER for names, addresses, financial identifiers, health data) -> **redact/tokenize** before SendMessage -> **audit** redaction event with `messageId`/`taskId` (no raw PII). Apply again on inbound Artifacts before merging into user-visible context. Data minimization: transmit minimum data parts necessary for the requested skill.

### 4.11 Immutable Audit Logs for Cross-Agent Decision Chains

Every task state transition across agent boundaries is logged to an append-only audit store:
- **What is logged**: Task ID, context ID, source agent identity, target agent identity, operation, state transition, timestamp, W3C trace context (`traceparent`/`tracestate`), and a hash of the data parts exchanged (not the content itself)
- **Chain-of-custody**: Trace context links every log entry across all agent hops into a single auditable decision chain
- **Immutability**: Append-only store (AWS QLDB, Azure Immutable Blob, or Merkle-tree-based log)
- **Retention**: Aligns with regulatory regime (7 years SOX, 6 years GDPR for data subject rights)

### 4.12 Cross-Agent Prompt Injection

A2A provides **no protocol-level controls** against prompt injection. This is a deliberate design boundary: the protocol handles transport and coordination, not content validation. Mitigation relies entirely on defense-in-depth within each agent: TLS, signed Agent Cards, authentication, authorization, least privilege, input validation and sanitization, isolation of untrusted content from system prompts.

### 4.13 Tool RBAC Stays on the MCP Side

A2A authorizes **which caller may create/get/cancel which Tasks** (identity, skill, tenant, OAuth scopes). **Tool-level least privilege** remains inside each agent's MCP/tool gateway. Do not conflate peer-agent authz with tool allowlists.

### 4.14 Observability Gaps (Sept 2026)

- "delegated to research_agent" in logs is NOT observability -- need structured traces surviving handoffs
- No built-in distributed tracing (must layer OpenTelemetry manually via W3C Trace Context)
- SREs report inability to localize latency spikes across agent hops
- No standardized metrics format for agent-to-agent interactions
- The reliability engineering discipline for A2A is not mature -- **the protocol is production-grade, the operational practice is not**

---

## 5. Failure Modes

### 5.1 Failure Taxonomy

Classifying failures by recoverability determines the correct response strategy. **Conflating transient and permanent failures (e.g., retrying an auth failure) wastes cost and delays detection. Ignoring poison-pill failures (semantically wrong but structurally valid responses) is the dominant production risk.**

| Category | Examples | Response Strategy |
|---|---|---|
| **Transient** | 503 + `Retry-After`, SSE disconnect, webhook timeout, DNS blips, TLS handshake timeout, 429 rate limit | Retry with exponential backoff + jitter. Circuit breaker trips after N consecutive failures. Resume via GetTask / SubscribeToTask after reconnection. |
| **Permanent / Protocol** | `VersionNotSupportedError` -32009, `ContentTypeNotSupportedError`, `UnsupportedOperationError`, 401 auth failure, 403 authz denied, Agent Card not found (404) | **Fail fast. Do NOT retry.** Surface to orchestrator immediately. Attempt fallback agent or degrade gracefully. |
| **Poison-pill / Semantic** | **Silent delegation failure**: semantically wrong results that pass structural validation (agent returns plausible but factually incorrect analysis). Cross-agent prompt injection: malicious content in one agent's output hijacks the receiving agent's reasoning. | Detect via output validation (dedicated validator agent or deterministic assertion checks on artifacts). Quarantine suspect agent: circuit-break it, alert operator, exclude from future delegation until manual review. Log full request-response chain with trace context. |
| **Capability mismatch** | Stream/push without Card flags | `UnsupportedOperationError` / `PushNotificationNotSupportedError` |
| **Cascade / runaway** | Recursive A->B->C delegation, runaway spend, cascading timeouts | Max hop depth, deadline budgets, per-task spend caps, CancelTask |

### 5.2 A2A-Specific Error Types

| Error | JSON-RPC Code | When |
|---|---|---|
| `TaskNotFoundError` | -32001 | Task ID does not exist or caller lacks access |
| `TaskNotCancelableError` | -32002 | Task already in terminal state |
| `PushNotificationNotSupportedError` | -32003 | Agent lacks push capability |
| `UnsupportedOperationError` | -32004 | Operation not supported (e.g., SendMessage on terminal task) |
| `ContentTypeNotSupportedError` | -32005 | Unsupported MIME type in parts |
| `VersionNotSupportedError` | -32009 | Requested A2A-Version unsupported |
| `InvalidAgentResponseError` | - | Response violates spec |
| `ExtendedAgentCardNotConfiguredError` | - | Declared but not configured |
| `ExtensionSupportRequiredError` | - | Required extension missing from client |

**Transport-level error mapping**:

| Condition | HTTP | gRPC | JSON-RPC |
|---|---|---|---|
| Authentication | 401 | UNAUTHENTICATED | - |
| Authorization | 403 | PERMISSION_DENIED | - |
| Validation | 400 | INVALID_ARGUMENT | -32602 |
| Not found | 404 | NOT_FOUND | - |
| System error | 500-503 | INTERNAL-UNAVAILABLE | -32603 |

### 5.3 Discovery Failures

- Agent Card endpoint unreachable (standard HTTP failure -- easiest to handle)
- Stale cached Agent Card (spec requires caching directives; clients must respect)
- Malicious Agent Cards misrepresenting capabilities (mitigated by Signed Agent Cards)
- Extended Agent Card not configured despite capability flag (`ExtendedAgentCardNotConfiguredError`)
- **Schema gap**: Spec does not standardize per-skill JSON Schema for Part body content. A client knows a skill accepts `application/json` but not the exact object structure. Mitigation: embed OpenAPI fragment or JSON Schema in skill description, or use Extensions.

### 5.4 Silent Delegation Failure -- The Dominant 2026 Risk

**Silent delegation failure is the dominant failure mode in 2026.** The orchestrator sends a task to a specialist, receives a plausible "completed" event, the workflow continues -- but the specialist used wrong data, hit stale state, or returned incorrect results. **No protocol-level mechanism catches semantic errors.** This is fundamentally harder than catching transport failures.

**Cross-boundary error smearing**: When an orchestrator delegates to a sub-agent and the sub-agent fails silently, who carries the error budget? How do you instrument the boundary? These questions have no consensus answers yet.

**Mitigation**: Add a validator agent that spot-checks specialist responses against known constraints before the orchestrator accepts them. Embed deterministic assertion checks on artifact content. Log full request-response chains with trace context for forensic analysis.

### 5.5 Long-Running / Disconnect / Webhook Failures

- SSE drop mid-task -> resubscribe via `SubscribeToTask`; may miss transient status Messages not persisted in history
- Webhook SSRF if URL not validated
- Webhook down -> retries then stop; client must poll or miss terminal state
- Auth-required without open stream/webhook/poll -> client misses continuation
- Partial writes / retry without `messageId` idempotency -> duplicate side effects

---

## 6. System Design Scenarios

### Scenario 1: Cross-Organization Procurement Workflow

**Problem**: A manufacturing company's procurement system must coordinate with three external vendor agents (inventory, pricing, logistics) owned by different organizations. Each vendor uses different AI frameworks and has different security requirements. The system must handle multi-day RFQ (Request for Quote) cycles with human approval gates, survive vendor agent outages, and maintain full audit trails for regulatory compliance.

```
+-----------------------------------------------------------------------------+
|                     BUYER ORGANIZATION (TRUST BOUNDARY)                       |
|                                                                              |
|  +--------------------+      +------------------------------------------+   |
|  | Procurement Portal |      | Orchestrator Agent (Google ADK)          |   |
|  | (human approvers   |<---->|  - Discovers vendors via Signed Cards   |   |
|  |  interact via      |      |  - Delegates RFQ subtasks via A2A       |   |
|  |  INPUT_REQUIRED    |      |  - Aggregates quotes, ranks by TCO      |   |
|  |  state)            |      |  - Pauses at INPUT_REQUIRED for         |   |
|  +--------------------+      |    human approval (> $50K threshold)    |   |
|                               |  - Internal tools via MCP               |   |
|                               |  - Circuit breaker per vendor           |   |
|                               +-------+----------+----------+----------+   |
|                                       |          |          |              |
|  +-------------------+                |          |          |              |
|  | API Gateway        |<--------------+          |          |              |
|  | (mTLS termination, |                          |          |              |
|  |  rate limiting,    |                          |          |              |
|  |  audit logging,    |                          |          |              |
|  |  egress policy)    |                          |          |              |
|  +-------+-----------+                           |          |              |
+----------+--------------------------------------|----------|------------- +
           | mTLS + OAuth 2.1                      |          |
           | scoped per vendor per skill           |          |
+----------v--------------+  +--------------------v+  +------v-------------+
| VENDOR A (Inventory)     |  | VENDOR B (Pricing)  |  | VENDOR C (Logistics)|
| Agent: CrewAI             |  | Agent: LangGraph    |  | Agent: Bedrock      |
| Skills: check_stock,     |  | Skills: quote,      |  | Skills: estimate_   |
|   reserve_inventory,     |  |   bulk_discount     |  |   shipping, track,  |
|   confirm_shipment       |  | Card: Signed, OAuth |  |   customs_clearance |
| Card: Signed, mTLS       |  | US region only      |  | Card: Signed, OAuth |
| Long-running: push       |  |                     |  | Push notifications  |
| Data residency: EU       |  |                     |  | Multi-region        |
+--------------------------+  +---------------------+  +--------------------+
```

**Task lifecycle for a procurement request**:

| Time | State | Action |
|---|---|---|
| T+0 | SUBMITTED | Orchestrator receives "procure 500 units of part X-7742" |
| T+1s | WORKING | Fan-out: 3 parallel A2A tasks to vendors (non-blocking, push notification config) |
| T+5m | WORKING | Vendor A: stock confirmed (push). Vendor B: quote returned (push). Vendor C: circuit open (3 timeouts). |
| T+35m | WORKING | Vendor C circuit half-open, probe succeeds, logistics estimate received |
| T+36m | INPUT_REQUIRED | Total > $50K. Orchestrator pauses for human approval via portal |
| T+4h | WORKING | Human approves. Orchestrator resumes, sends reserve_inventory to Vendor A |
| T+4h5m | COMPLETED | Reservation confirmed. Artifacts contain PO number, shipping ETA, cost breakdown |

**Trade-off matrix**:

| Dimension | Custom API/Queue | A2A Protocol |
|---|---|---|
| Vendor onboarding | Weeks per vendor (custom API per) | Hours (Agent Card + auth) |
| Adding new vendor | New integration code per vendor | Discover Agent Card, test skills, configure auth |
| Human-in-the-loop | Custom webhook + polling logic | Native INPUT_REQUIRED state + resume with same taskId |
| Multi-day workflow | Stateful queue + DB + polling | Native task state machine, push notifications, GetTask |
| Latency per hop | Sub-100ms API | 2-30s (agent reasoning). Acceptable for procurement. |
| Silent delegation risk | API contracts enforce schema | Vendor agent may return plausible but wrong results. Mitigation: validator agent. |
| Audit / traceability | Custom logging per vendor | W3C Trace Context across all hops. taskId correlation. |

**Decision rationale**: A2A wins because (1) vendors are separate organizations with different frameworks -- the N-vendor integration problem scales linearly with A2A instead of quadratically; (2) procurement workflows are inherently multi-day and human-in-the-loop, mapping directly to A2A's async-first design; (3) 2-30s latency is acceptable for workflows measured in hours/days; (4) Signed Agent Cards + mTLS provide cryptographic identity verification at cross-org boundaries.

---

### Scenario 2: Internal AI Platform with Shared Specialist Agents

**Problem**: An enterprise with 50+ product teams wants to offer shared AI specialist agents (code review, security scanning, documentation generation, test generation) as an internal platform. Each product team has its own orchestrator agent; specialists are maintained by the platform team. The system must enforce per-team quotas, prevent cross-team data leakage, scale horizontally, and support adding new specialists without redeploying any team's orchestrator.

```
+-----------------------------------------------------------------------------+
|                          KUBERNETES CLUSTER                                   |
|                                                                              |
|  +------------------------------------------------------------------------+  |
|  |               SERVICE MESH (Istio / Linkerd)                            |  |
|  |        mTLS between all pods    |    NetworkPolicy isolation            |  |
|  +-------------------------------------+----------------------------------+  |
|                                        |                                     |
|  +-------------------------------------v----------------------------------+  |
|  |                    API GATEWAY (Kong / Envoy)                            |  |
|  |  Per-team OAuth scopes  |  Rate limiting  |  Request logging            |  |
|  |  Agent Card registry    |  A2A-Version routing                          |  |
|  +---+--------+--------+--+----------+------------------------------------+  |
|      |        |        |             |                                       |
|  +---v--+ +--v---+ +--v---+    +---v-------------------------------+       |
|  |Team A| |Team B| |Team C|    | Agent Card Discovery Service       |       |
|  |Orch. | |Orch. | |Orch. |    | (/.well-known/agent.json           |       |
|  |Agent | |Agent | |Agent |    |  aggregator; filters by team's     |       |
|  | MCP  | | MCP  | | MCP  |    |  OAuth scope for Extended Cards)   |       |
|  |tools | |tools | |tools |    +--------------------------------------+      |
|  +--+---+ +--+---+ +--+---+                                                 |
|     |        |        |        A2A (JSON-RPC / HTTPS)                        |
|     |        |        |                                                      |
|  +--v--------v--------v--------------------------------------------+        |
|  |              SHARED SPECIALIST AGENT POOL                        |        |
|  |                                                                  |        |
|  |  +------------+  +------------+  +------------+  +-----------+  |        |
|  |  | Code Review |  | Security   |  | Doc Gen    |  | Test Gen  |  |        |
|  |  | Agent       |  | Scanner    |  | Agent      |  | Agent     |  |        |
|  |  | (HPA:3-20) |  | Agent      |  | (HPA:2-10) |  | (HPA:2-8)|  |        |
|  |  |             |  | (HPA:2-15) |  |            |  |           |  |        |
|  |  | Skills:     |  | Skills:    |  | Skills:    |  | Skills:   |  |        |
|  |  | -review_pr  |  | -scan_deps |  | -gen_api   |  | -gen_unit |  |        |
|  |  | -suggest_   |  | -audit_    |  |  _docs     |  |  _tests   |  |        |
|  |  |  refactor   |  |  iam_roles |  | -gen_      |  | -gen_integ|  |        |
|  |  |             |  | -pen_test  |  |  runbook   |  |  _tests   |  |        |
|  |  +------------+  +------------+  +------------+  +-----------+  |        |
|  |                                                                  |        |
|  |  Each specialist: own Agent Card, own HPA, own resource quota.  |        |
|  |  Platform team deploys new specialists without touching team code |        |
|  +------------------------------------------------------------------+        |
|                                                                              |
|  +----------------------------------------------------------------------+   |
|  |                    OBSERVABILITY STACK                                 |   |
|  |  OpenTelemetry Collector  |  Grafana dashboards  |  per-team cost    |   |
|  |  W3C Trace Context       |  per-agent latency    |  attribution      |   |
|  |  across all hops         |  P50/P95/P99          |  via OAuth scope  |   |
|  +----------------------------------------------------------------------+   |
+-----------------------------------------------------------------------------+
```

**Data isolation model**:

| Layer | Isolation Mechanism |
|---|---|
| **Network** | Kubernetes NetworkPolicy: team namespaces can only reach specialist pods via service mesh, not each other's orchestrators |
| **Identity** | OIDC-based Agent Card auth. Each team has distinct OAuth client credentials. Specialist validates team identity on every request. |
| **Task visibility** | ListTasks returns ONLY tasks created by the authenticated team. Specialist stores tasks partitioned by team scope. |
| **Quota** | API gateway enforces per-team rate limits. Cost attributed via OAuth scope in telemetry. |
| **Agent Card access** | Extended Agent Cards show team-specific skills. `pen_test` skill visible only to security team. |

**Trade-off matrix**:

| Dimension | Embedded Per-Team Agents | Shared A2A Specialist Pool |
|---|---|---|
| Resource efficiency | 50 copies of each specialist, most idle | Single pool with HPA. ~5-10x better utilization. |
| Adding new specialist | Redeploy all 50 team stacks | Deploy once, update Agent Card registry. Zero team impact. |
| Upgrading specialist | Coordinate 50 team upgrades | Canary deploy behind service mesh. A2A version negotiation handles compatibility. |
| Cross-team data leak | No risk (isolated) | Requires task partitioning, ListTasks scoping, network policy. More attack surface. |
| Latency | In-process, fast | Network hop + agent reasoning. 2-30s per specialist call. |
| Team autonomy | Full control | Teams depend on platform for specialist availability. SLA contract needed. |
| Operational complexity | Simple per-team | Platform team operates mesh, gateway, HPA, tracing. Higher ops bar. |

**Decision rationale**: A2A specialist pool wins for 50+ teams because (1) operational leverage -- deploying or upgrading a specialist once serves all teams; (2) resource efficiency is 5-10x better with shared HPA; (3) main risks (cross-team data leakage, platform availability) are mitigated by strict ListTasks scoping, network isolation, HPA, circuit breakers, and graceful degradation to local MCP tools. Would NOT be justified for fewer than ~10 teams (ops complexity outweighs consolidation benefit) or if specialists needed sub-second response times.

---

### Scenario 3: Cross-Vendor Hiring Pipeline (Orchestrator + Specialists)

**Problem**: Design a hiring orchestrator that delegates **candidate sourcing**, **interview scheduling**, and **background-check** to agents owned by different vendors/frameworks. Volume: ~5K hiring workflows/day; each workflow 3-6 A2A tasks; HITL on offer approval; must not expose vendor tool internals; p95 interactive status < 5s perceived for short steps; long background checks run hours-days.

```
+-------------------------------------------------------------+
| CONTROL PLANE -- Hiring Orchestrator (A2A Client)            |
| Card cache | OAuth per vendor | hop budget=3 | spend caps    |
| breakers per specialist | HITL on AUTH/INPUT_REQUIRED        |
+---------------+---------------------+-----------+------------+
                | A2A                 | A2A       | A2A
                v                     v           v
        +---------------+   +---------------+ +------------------+
        | Sourcing Agent|   | Scheduling Agt| | Background-check |
        | Card+skills   |   | Card+skills   | | Agent            |
        +-------+-------+   +-------+-------+ +--------+---------+
                | MCP               | MCP               | MCP
                v                   v                   v
           ATS/search           Calendar API         Vendor BGC API
                |                   |                   |
                +-------------------+-------------------+
                    PERSISTENCE: Task store + audit log
                    TELEMETRY: taskId/contextId/corr --> SIEM
                    Updates: SSE short steps | push+poll BGC
```

**Trade-off matrix**:

| Approach | Cost | Latency | Ops | Security |
|---|---|---|---|---|
| **Mega-agent (no A2A)** | High context/tools | High on broad workflows | Low integration count | IP leak risk |
| **Custom REST per vendor** | High eng N x M | Tunable | High maintenance | Per-integration auth |
| **A2A centralized orchestrator (recommended)** | Medium coord + specialist tokens | Predictable; SSE/push by stage | Medium (Cards, auth, breakers) | Opaque + OAuth/mTLS + scoped Get |

**Decision rationale**: A2A removes N x M glue while preserving opaque vendor IP. Google's own launch narrative is multi-agent hiring across systems. Mega-agent fails compliance/tool sprawl; custom REST does not amortize across 100+ ecosystem partners.

---

### When NOT to Use A2A

- Single agent systems (no peer communication needed)
- Single codebase (all components share deployment)
- Short synchronous workflows (no long-running task lifecycle)
- No discovery requirements (agents are hardcoded)
- No independent task state (stateless function calls)
- No external agent providers (all internal to one team)
- When an API or queue would be simpler
- When the team cannot operate the complexity

**Start with MCP. Design clean agent boundaries from the beginning. Add A2A only when those boundaries become real.**

### Agent Payments Protocol (AP2)

Extension of A2A into economic coordination: secure agent-driven transactions. 60+ organizations behind it across payments and financial services. Enables agents to negotiate and execute payments as part of task completion -- critical for agent marketplaces.

### The 90-Day Retention Test

The real measure of A2A adoption is not "supported by" (logo adoption) but **production retention after the first operational incident**. "Developer hype is cheap, production retention is expensive." The worst sign would be if developers only use it in demos while production systems fall back to custom APIs and queues.

---

## Production Enterprise Code

**Combined A2A client with Agent Card verification, circuit breaker, retry with jitter, fallback chain (remote -> local -> queued), messageId idempotency, PII redaction, SSRF guard, immutable audit log, async task poller with backoff, and structured logging with correlation IDs. Deterministic fake remote agent -- no API keys, no TODOs.**

```python
#!/usr/bin/env python3
"""
A2A-shaped client: Agent Card discovery + JWS verification + Task FSM +
circuit breaker (closed->open->half-open) + retries with full jitter +
fallback chain (remote->local->queued) + messageId idempotency +
PII redaction + SSRF guard + immutable audit log + async task poller.

Run: python a2a_client_demo.py
Expected output: JSON logs, run1/run2/run3 results, poller demo, ok.
No API keys. No TODOs. Fully deterministic.

Requirements for production variant:
    pip install httpx tenacity structlog canonicaljson pyjwt cryptography
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable


# ---------------------------------------------------------------------------
# Structured logging with correlation IDs (JSON format for SIEM ingestion)
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    """Emits one JSON line per log event with standard correlation fields."""
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
            "trace_id": getattr(record, "trace_id", None),
        }
        return json.dumps(
            {k: v for k, v in payload.items() if v is not None}, sort_keys=True
        )


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
# Task state machine (A2A v1.0 -- all 9 states)
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
    TaskState.COMPLETED, TaskState.FAILED,
    TaskState.CANCELED, TaskState.REJECTED,
}
INTERRUPTED = {TaskState.INPUT_REQUIRED, TaskState.AUTH_REQUIRED}


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
    """Simplified Agent Card matching A2A v1.0 structure."""
    name: str
    url: str
    protocol_version: str
    skills: list[str]
    streaming: bool
    push_notifications: bool
    signature_ok: bool  # True if JWS verified


# ---------------------------------------------------------------------------
# Circuit breaker: closed -> open -> half-open (time-based recovery)
# ---------------------------------------------------------------------------

class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Per-agent circuit breaker. Trips after failure_threshold consecutive
    failures. Stays open for recovery_timeout_ticks, then allows one probe
    request (half-open). Probe success -> closed. Probe fail -> reopen."""

    failure_threshold: int = 2
    recovery_timeout_ticks: int = 2  # use real time in production
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
        if self.state is BreakerState.HALF_OPEN or \
           self.failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at_tick = self.clock_tick


# ---------------------------------------------------------------------------
# Retries: exponential backoff + full jitter (deterministic RNG for tests)
# ---------------------------------------------------------------------------

@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay_ms: int = 10
    max_delay_ms: int = 80
    rng: random.Random = field(default_factory=lambda: random.Random(0))

    def delay_ms(self, attempt: int) -> int:
        ceiling = min(self.max_delay_ms, self.base_delay_ms * (2 ** attempt))
        return self.rng.randint(0, ceiling)


class TransientAgentError(Exception):
    """503, timeout, DNS blip -- retryable."""
    pass


class PermanentAgentError(Exception):
    """401, 403, VersionNotSupported -- do NOT retry."""
    pass


# ---------------------------------------------------------------------------
# Deterministic fake remote A2A server (messageId idempotency)
# ---------------------------------------------------------------------------

@dataclass
class FakeRemoteAgent:
    """In-memory A2A server: Card + SendMessage with messageId dedup."""

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
        self, *, message_id: str, context_id: str, text: str, skill: str,
    ) -> Task:
        # Idempotency: same messageId -> return existing Task
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
            task_id=task_id, context_id=context_id,
            status=TaskState.COMPLETED,
            history_message_ids=[message_id],
            artifacts=[Artifact(
                artifact_id=f"art-{self._id_seq}",
                parts=[Part(text=f"{self.name}:ok:{skill}:{text}")],
            )],
        )
        self._tasks[task_id] = task
        self._by_message_id[message_id] = task_id
        return task

    def get_task(self, task_id: str) -> Task:
        if task_id not in self._tasks:
            raise PermanentAgentError("TaskNotFoundError")
        return self._tasks[task_id]


@dataclass
class FakeLocalAgent:
    """Fallback agent: same trust domain, reduced skill set."""
    name: str = "local-fallback"

    def run(self, *, text: str, skill: str) -> Artifact:
        return Artifact(
            artifact_id="art-local-1",
            parts=[Part(text=f"{self.name}:ok:{skill}:{text}")],
        )


# ---------------------------------------------------------------------------
# Zero-Trust Card gate + PII redact + SSRF guard (boundary helpers)
# ---------------------------------------------------------------------------

PRIVATE_PREFIXES = ("127.", "10.", "192.168.", "172.16.", "169.254.", "localhost")


def verify_agent_card(card: AgentCard, *, expected_host: str) -> None:
    """Zero-trust: do not trust Card claims without verification.
    In production, this calls JWS verification against known JWKS."""
    if not card.signature_ok:
        raise PermanentAgentError("card_signature_untrusted")
    if expected_host not in card.url:
        raise PermanentAgentError("card_url_untrusted")
    if card.protocol_version.split(".")[0] != "1":
        raise PermanentAgentError("VersionNotSupportedError")


def redact_parts(text: str) -> tuple[str, bool]:
    """Detect -> redact (simple email pattern) -> caller audits.
    Production: use NER for names, addresses, financial IDs, health data."""
    if "@" in text and "." in text.split("@")[-1]:
        return "[REDACTED_EMAIL]", True
    return text, False


def ssrf_safe_webhook(url: str) -> bool:
    """Reject private/localhost ranges for push webhook URLs."""
    host = url.split("://")[-1].split("/")[0].split(":")[0].lower()
    return not any(
        host.startswith(p) or host == p.rstrip(".")
        for p in PRIVATE_PREFIXES
    )


# ---------------------------------------------------------------------------
# A2A client orchestrator (remote -> local -> queued fallback chain)
# ---------------------------------------------------------------------------

@dataclass
class A2AClient:
    """Full A2A client with:
    - Agent Card discovery + verification (Zero-Trust)
    - SendMessage with retries + circuit breaker
    - messageId -> taskId idempotency mapping
    - Fallback chain: remote -> local -> queued
    - PII redaction at trust-domain egress
    - SSRF guard for push webhook URLs
    - Immutable audit log (append-only)
    - Structured JSON logging with correlation IDs
    """

    remote: FakeRemoteAgent
    local: FakeLocalAgent
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    card_cache: AgentCard | None = None
    message_to_task: dict[str, str] = field(default_factory=dict)
    immutable_log: list[dict[str, Any]] = field(default_factory=list)
    queue: list[dict[str, Any]] = field(default_factory=list)

    def discover_card(self, *, correlation_id: str) -> AgentCard:
        """Fetch + verify Agent Card. Cache on success."""
        if self.card_cache is not None:
            return self.card_cache
        card = self.remote.agent_card()
        verify_agent_card(card, expected_host="agents.example")
        self.card_cache = card
        LOG.info("card_fetched", extra={
            "correlation_id": correlation_id, "agent": card.name,
        })
        return card

    def _audit(self, event: str, **fields: Any) -> None:
        """Append-only audit log: taskId, state transitions, hashes."""
        entry = {"event": event, "ts": time.time(), **fields}
        self.immutable_log.append(entry)

    def send_task(
        self, *, text: str, skill: str, correlation_id: str,
        context_id: str | None = None, message_id: str | None = None,
    ) -> dict[str, Any]:
        """Full flow: Card -> PII redact -> retry with breaker ->
        fallback chain -> audit.  Idempotent on message_id."""

        ctx = context_id or f"ctx-{uuid.uuid5(uuid.NAMESPACE_URL, correlation_id).hex[:8]}"
        mid = message_id or str(uuid.uuid5(
            uuid.NAMESPACE_OID, f"{correlation_id}:{text}:{skill}"
        ))

        # --- Idempotency check: already processed this message? ---
        if mid in self.message_to_task:
            tid = self.message_to_task[mid]
            task = self.remote.get_task(tid)
            return self._result(task, path="remote_idempotent", cid=correlation_id)

        card = self.discover_card(correlation_id=correlation_id)
        if skill not in card.skills:
            raise PermanentAgentError("skill_not_on_card")

        # --- PII redaction at trust-domain egress ---
        clean, redacted = redact_parts(text)
        if redacted:
            self._audit("pii_redacted", correlation_id=correlation_id,
                        message_id=mid, context_id=ctx)

        self.breaker.tick()
        errors: list[str] = []

        # --- TIER 1: Remote specialist with retries ---
        if self.breaker.allow():
            for attempt in range(self.retry.max_attempts):
                try:
                    task = self.remote.send_message(
                        message_id=mid, context_id=ctx,
                        text=clean, skill=skill,
                    )
                    # Poll until terminal (SSE in production)
                    while task.status not in TERMINAL:
                        task = self.remote.get_task(task.task_id)

                    self.breaker.record_success()
                    self.message_to_task[mid] = task.task_id
                    self._audit("task_completed", task_id=task.task_id,
                                state=task.status.value, message_id=mid,
                                correlation_id=correlation_id)
                    return self._result(task, path="remote", cid=correlation_id)

                except TransientAgentError as exc:
                    self.breaker.record_failure()
                    delay = self.retry.delay_ms(attempt)
                    errors.append(f"remote:a{attempt}:{exc}:sleep{delay}ms")
                    if self.breaker.state is BreakerState.OPEN:
                        break
                except PermanentAgentError as exc:
                    self.breaker.record_failure()
                    errors.append(f"remote:permanent:{exc}")
                    break
        else:
            errors.append(f"remote:breaker_{self.breaker.state.value}")

        # --- TIER 2: Local fallback agent ---
        try:
            art = self.local.run(text=clean, skill=skill)
            self._audit("fallback_local", correlation_id=correlation_id,
                        message_id=mid, errors=errors)
            return {
                "path": "local", "correlation_id": correlation_id,
                "message_id": mid, "context_id": ctx,
                "status": TaskState.COMPLETED.value,
                "artifact": art.parts[0].text, "errors": errors,
            }
        except Exception as exc:
            errors.append(f"local:{exc}")

        # --- TIER 3: Queue for later drain ---
        queued = {"message_id": mid, "context_id": ctx, "text": clean,
                  "skill": skill, "correlation_id": correlation_id}
        self.queue.append(queued)
        self._audit("fallback_queued", **queued, errors=errors)
        return {
            "path": "queued", "correlation_id": correlation_id,
            "message_id": mid, "context_id": ctx,
            "status": TaskState.SUBMITTED.value,
            "artifact": None, "errors": errors,
        }

    def _result(self, task: Task, *, path: str, cid: str) -> dict[str, Any]:
        art = task.artifacts[0].parts[0].text if task.artifacts else None
        return {
            "path": path, "correlation_id": cid,
            "task_id": task.task_id, "context_id": task.context_id,
            "status": task.status.value, "artifact": art,
        }


# ---------------------------------------------------------------------------
# Async Task Poller with exponential backoff + graceful degradation
# ---------------------------------------------------------------------------

@dataclass
class AsyncTaskPoller:
    """Polls an A2A task until terminal or interrupted state.
    Features: exponential backoff, timeout, graceful degradation
    (continues polling on transient errors up to max_consecutive_errors),
    callback on state changes.  Production: swap fake for httpx."""

    remote: FakeRemoteAgent
    task_id: str
    initial_interval: float = 0.01  # seconds (fast for demo)
    max_interval: float = 1.0
    timeout_seconds: float = 10.0
    max_consecutive_errors: int = 5
    on_state_change: Callable[[str, Task], Awaitable[None]] | None = None
    _last_state: str | None = field(default=None, init=False)

    async def poll_until_terminal(self) -> Task:
        interval = self.initial_interval
        start = time.monotonic()
        consecutive_errors = 0

        while True:
            elapsed = time.monotonic() - start
            if elapsed > self.timeout_seconds:
                raise TimeoutError(
                    f"Task {self.task_id} not done in {self.timeout_seconds}s"
                )
            try:
                task = self.remote.get_task(self.task_id)
                consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                if consecutive_errors >= self.max_consecutive_errors:
                    raise RuntimeError(f"{consecutive_errors} consecutive errors")
                await asyncio.sleep(interval)
                interval = min(interval * 2, self.max_interval)
                continue

            state = task.status.value
            if state != self._last_state:
                if self.on_state_change:
                    await self.on_state_change(state, task)
                self._last_state = state

            if task.status in TERMINAL:
                return task
            if task.status in INTERRUPTED:
                return task  # caller decides how to resume

            await asyncio.sleep(interval)
            interval = min(interval * 2, self.max_interval)


# ---------------------------------------------------------------------------
# Demo (deterministic, no API keys)
# ---------------------------------------------------------------------------

def main() -> None:
    remote = FakeRemoteAgent(fail_times=2)
    client = A2AClient(remote=remote, local=FakeLocalAgent())
    corr = "corr-demo-001"

    # 1) First call: remote fails 2x -> breaker opens -> local fallback
    r1 = client.send_task(
        text="find candidates for role X", skill="sourcing",
        correlation_id=corr,
    )
    assert r1["path"] in {"remote", "local", "queued"}
    print("run1", json.dumps(r1, sort_keys=True))

    # 2) Advance clock for breaker recovery, remote now healthy
    mid = str(uuid.uuid5(
        uuid.NAMESPACE_OID, f"{corr}:find candidates for role X:sourcing"
    ))
    client.breaker.tick()
    client.breaker.tick()
    remote.fail_times = 0
    remote._calls = 0

    r2 = client.send_task(
        text="find candidates for role X", skill="sourcing",
        correlation_id=corr, message_id=mid,
    )
    print("run2", json.dumps(r2, sort_keys=True))

    # 3) Idempotent replay: same messageId -> same taskId
    r3 = client.send_task(
        text="find candidates for role X", skill="sourcing",
        correlation_id=corr, message_id=mid,
    )
    print("run3", json.dumps(r3, sort_keys=True))
    if r2.get("task_id") and r3.get("task_id"):
        assert r2["task_id"] == r3["task_id"], "Idempotency violated!"

    # 4) SSRF guard
    assert ssrf_safe_webhook("https://hooks.example.com/a2a") is True
    assert ssrf_safe_webhook("http://127.0.0.1/steal") is False
    assert ssrf_safe_webhook("http://169.254.169.254/metadata") is False

    # 5) Async poller demo
    async def poll_demo() -> None:
        poller = AsyncTaskPoller(
            remote=remote, task_id=r2["task_id"],
        )
        result = await poller.poll_until_terminal()
        print("poller_result", result.status.value)

    asyncio.run(poll_demo())

    print("audit_entries", len(client.immutable_log))
    print("queue_depth", len(client.queue))
    print("ok")


if __name__ == "__main__":
    main()
```

---

## Common Failure Modes Table

| # | Failure Mode | Symptom | Root Cause | Mitigation |
|---|---|---|---|---|
| 1 | **Silent delegation** | Plausible but wrong results pass through | Remote agent returns semantically incorrect output; no protocol-level semantic validation | Validator agent, deterministic assertions on artifacts, trace-context forensics |
| 2 | **Cross-boundary error smearing** | Cannot attribute error to specific agent in chain | Opaque execution hides internal failures; no standardized metrics format | W3C Trace Context on every hop, per-agent circuit breaker, structured logging |
| 3 | **Retry storm on non-idempotent SendMessage** | Duplicate tasks created during transient failure recovery | SendMessage idempotency not guaranteed by spec | Client-generated `messageId` + server-side dedup mapping |
| 4 | **Cascading timeout** | 3-hop chain blocks for 90s+ | Each hop adds full LLM reasoning time | Max hop depth, per-task deadline, async with push+poll |
| 5 | **Stale Agent Card** | Client sends tasks to agent with changed/removed skills | Card cache not invalidated | Respect HTTP cache headers, periodic re-fetch, Extended Card after auth |
| 6 | **Schema gap** | Client sends JSON Parts agent cannot parse | No per-skill JSON Schema standardized | Embed OpenAPI/JSON Schema in skill description or Extensions |
| 7 | **SSRF via push webhook** | Server POSTs to internal infrastructure | Webhook URL points to private range | Reject 127/10/172.16/192.168/169.254, allowlists only |
| 8 | **Version mismatch** | `VersionNotSupportedError` on every request | Missing `A2A-Version: 1.0` header (empty defaults to 0.3) | Always send version header; dual 0.3/1.0 Card during migration |
| 9 | **Authorization creep** | Sub-agent operates with orchestrator's broad token | Tokens not down-scoped on delegation | Issue per-hop, per-skill scoped tokens |
| 10 | **Observability gap** | Cannot localize which hop caused latency spike | No built-in distributed tracing in protocol | Layer OpenTelemetry manually; propagate traceparent/tracestate |

---

## Key Takeaways for Interviews

- **MCP inside agents, A2A between agents** -- MCP equips one agent with tools (vertical); A2A coordinates agents across trust/framework boundaries (horizontal). They are complementary, both governed by the Linux Foundation's AAIF.

- **Agent Cards are the discovery mechanism** -- JSON manifests at `/.well-known/agent-card.json` declare skills, auth requirements, capabilities, and supported modalities. Signed Cards (JWS/RFC 7515 over RFC 8785 canonicalized body) provide cryptographic identity verification. Extended Cards reveal hidden skills after authentication.

- **Tasks are durable, Messages are not** -- Task state (9-state FSM with 4 terminal, 2 interrupted) persists server-side and survives client disconnects. Messages are for dialogue; Artifacts are the reliable output mechanism. Using Messages for task outputs violates the spec.

- **Availability degrades multiplicatively** -- A 3-agent chain with 99.9% per-agent availability delivers only 99.7% end-to-end. Minimize chain depth; front critical paths with fallback chains and circuit breakers.

- **Silent delegation failure is the dominant 2026 risk** -- No protocol-level mechanism catches semantic errors. A specialist can return plausible but incorrect results. Mitigate with validator agents, deterministic assertions on artifacts, and full trace-context forensics.

- **Cost is dominated by LLM reasoning, not wire format** -- A2A wire overhead is <1% of total hop cost. Each hop costs a full LLM inference cycle. Prompt caching (60% hit rate) can cut effective input cost ~50%. Multi-hop chains multiply cost linearly.

- **Three update patterns match three latency profiles** -- SSE for interactive (<5s perceived), polling for DMZ without inbound connectivity, push+poll for minutes-to-days workflows. Push webhook timeout (10-30s) is a delivery budget, not a task SLA.

- **Do not use A2A for everything** -- Single agent systems, single codebases, stateless function calls, and teams that cannot operate the complexity should stick with MCP or direct APIs. Start with MCP; add A2A when agent boundaries become real organizational boundaries.

---

## Interview Q&A

**Q1: Walk me through how an A2A client sends a task to a remote agent, from discovery to artifact retrieval.**

A1: First, I fetch the Agent Card from the remote agent's well-known URL, verify the JWS signature against the declared key using RFC 8785 canonicalization, and inspect skills, capabilities, and security requirements. Then I authenticate using the scheme declared in the card -- OAuth 2.1 with scoped tokens for cross-org, mTLS for same trust boundary. I attach the `A2A-Version: 1.0` header and send a `SendMessage` with a client-generated `messageId` for idempotency. The server creates a server-generated `taskId` and starts processing. Depending on the card's capabilities, I receive updates via SSE streaming, push notifications at my webhook, or I poll with GetTask. When the task reaches COMPLETED, I extract the Artifacts -- those are the reliable output mechanism, not Messages. Throughout, I propagate W3C Trace Context headers for distributed tracing.

**Q2: What is the difference between MCP and A2A, and when do you use each?**

A2: MCP connects an agent to its tools and data -- think of it as a USB port for peripherals. A2A connects agents to other agents -- think of it as HTTP for web services. The reference architecture is MCP inside agents for tool access, A2A between agents for coordination across trust and framework boundaries. I start with one agent plus MCP tools and add A2A only when I have genuine multi-agent, multi-vendor, or cross-org requirements. Both are now governed by the Linux Foundation's AAIF, and major platforms like Google ADK, Salesforce Agentforce, and Amazon Bedrock implement both.

**Q3: How does the A2A task state machine handle human-in-the-loop workflows?**

A3: The task FSM has two interrupted states: INPUT_REQUIRED and AUTH_REQUIRED. When a remote agent needs human input or credentials, it transitions to one of these states and pauses work without killing the task. The client detects this via SSE, push notification, or polling, surfaces the request to a human through its UI, and then sends a new message with the same taskId and contextId to resume. This is a first-class protocol feature, not a bolted-on pattern. It natively supports multi-day procurement, offer approvals, and EU AI Act-mandated human oversight checkpoints.

**Q4: How would you handle silent delegation failure in an A2A system?**

A4: Silent delegation failure is the dominant 2026 risk -- a specialist returns plausible but factually incorrect results, and the orchestrator accepts them because they pass structural validation. My defense-in-depth: first, I add a validator agent that spot-checks specialist outputs against known business constraints before the orchestrator merges them. Second, I embed deterministic assertion checks on artifact content -- numeric ranges, schema validation, cross-reference checks. Third, I maintain full trace-context forensic logs so I can retroactively audit the decision chain. The protocol itself provides no semantic validation -- this is entirely my responsibility.

**Q5: Why is the push webhook timeout of 10-30 seconds not a latency SLA?**

A5: The 10-30 second figure is a delivery timeout for the webhook HTTP POST call -- how long the server waits for the client's webhook endpoint to respond with an HTTP 2xx. It has nothing to do with how long the task takes. A background check task might run for days. The end-to-end latency is dominated by the remote agent's LLM inference and tool execution time. For a short specialist task, my p50 budget is about 866ms (warm card cache + auth + framing + LLM). For multi-hop chains, latency grows additively -- 3 hops can easily hit 90s synchronously, which is why I design anything beyond 2 hops as async with push+poll.

**Q6: Where do you place the circuit breaker -- inside the remote agent or on the orchestrator? Why?**

A6: On the orchestrator, per remote agent base URL. The circuit breaker is a client-side resilience pattern. The remote agent is opaque -- I cannot control or configure its internals. My orchestrator tracks consecutive failures per remote endpoint: after N failures, the circuit opens and I fail fast to the local fallback agent. After a recovery timeout, the circuit goes half-open and I send one probe request. If the probe succeeds, the circuit closes. This prevents cascade failures where a failing specialist drags down the entire workflow.

**Q7: How does A2A handle protocol version migration?**

A7: Clients MUST send the `A2A-Version: 1.0` header. If the header is missing or empty, the server interprets it as version 0.3 for backward compatibility. Agent Cards can advertise both 0.3 and 1.0 in their `supportedInterfaces` array, allowing progressive migration. The client selects the interface matching its version and routes accordingly. This means I can upgrade my orchestrator to 1.0 while some vendor agents still run 0.3, and vice versa.

**Q8: How do you ensure messageId idempotency when the spec only says servers MAY deduplicate?**

A8: I generate a deterministic messageId on the client side -- typically a UUID v5 from the correlation ID plus request content. I maintain a client-side `messageId -> taskId` mapping. On retry after a timeout, I reuse the same messageId. If the server already processed it and created a Task, it returns the existing Task rather than creating a duplicate. Even if the server does not implement dedup, my client-side mapping catches the duplicate before I process the result twice. For critical workflows, I also make downstream processing idempotent.

**Q9: How does the availability chain rule affect your A2A system design?**

A9: Composite availability for an N-agent chain is A_chain = A_1 times A_2 times ... times A_N. For a 3-agent chain where each delivers 99.9%, the chain delivers only 99.7% -- roughly 26 hours of downtime per year instead of 8.7 hours. This means I minimize chain depth, front critical paths with fallback chains so a single agent outage does not bring down the workflow, and use circuit breakers to quickly fail over to local alternatives. In practice, I rarely justify more than 2 levels of delegation.

**Q10: What is the Agent Payments Protocol (AP2) and why does it matter?**

A10: AP2 is an extension of A2A into economic coordination -- it enables agents to negotiate and execute secure payments as part of task completion. Over 60 organizations from payments and financial services back it. This is critical for agent marketplaces where one organization's agent consumes another's specialist and needs to pay per-task. It turns A2A from pure coordination into a full economic protocol, which is the next step for cross-org multi-agent commerce.

**Q11: When should you NOT use A2A?**

A11: I do not use A2A for single-agent systems, single-codebase deployments, short synchronous function calls, hardcoded agent topologies with no discovery needs, or when my team cannot operate the complexity. If an API call or message queue would be simpler, I use that. The real test is the 90-day retention test -- if I am only using A2A in demos but falling back to custom APIs in production after the first incident, then A2A was not the right choice. I start with MCP, design clean agent boundaries from the start, and add A2A only when those boundaries become real organizational boundaries.

**Q12: How do you design zero-trust agent-to-agent communication in a cross-org A2A deployment?**

A12: Every agent-to-agent message is treated as untrusted regardless of network position. I verify identity at discovery via Signed Agent Cards with JWS over canonicalized JSON. I use OAuth 2.1 with per-caller, per-skill scoped tokens -- never blanket agent access. When my orchestrator delegates to a sub-agent, it issues a down-scoped token with only the skills needed for that task, preventing authorization creep. At the transport layer, I use mTLS within trust boundaries and TLS 1.3+ across boundaries. I also harden push webhooks against SSRF by rejecting private IP ranges and requiring authentication from the sending server. Extended Agent Cards reveal sensitive skills only after authentication, replacing the public card for the session.

---

## Key Numbers to Memorize

| Metric | Value |
|---|---|
| A2A launch date | 9 April 2025 (Google); Linux Foundation donation 23 June 2025 |
| v1.0 stable release | March 2026 |
| Partners at launch / at LF donation / current | 50+ / 100+ / 150+ organizations |
| GitHub stars | 22,000+ |
| SDKs | 5 languages (Python, JS, Java, Go, .NET) |
| TSC members | 8 (AWS, Cisco, Google, IBM, Microsoft, Salesforce, SAP, ServiceNow) |
| AAIF member orgs | 190 (as of May 2026) |
| Task lifecycle states | 9 (including UNSPECIFIED) |
| Terminal states | 4 (COMPLETED, FAILED, CANCELED, REJECTED) |
| Interrupted states | 2 (INPUT_REQUIRED, AUTH_REQUIRED) |
| Abstract operations | 11 |
| Protocol bindings | 3 (JSON-RPC 2.0, gRPC, HTTP/JSON/REST) |
| Webhook timeout guidance | 10-30 seconds |
| ListTasks default page size | 50 (min 1, max 100) |
| MCP 2026 adoption | 97M monthly SDK downloads, 17K+ servers |
| AP2 backing | 60+ organizations |
| Cost per 1K tasks (optimized) | ~$15 (with cache + fallback) |
| Cost per 1K tasks (multi-hop) | ~$112 (2-hop, no cache) |
| E2E p50 latency (warm, single hop) | ~866 ms |
| Chain availability (3 agents at 99.9%) | 99.7% (26 hours downtime/year) |
| A2A wire overhead | <1% of total hop cost |

---

## Quick Reference

```
PROTOCOL STACK
  L1: Data Model (a2a.proto) -> Task, Message, Artifact, Part, AgentCard
  L2: Operations (11 total) -> SendMessage, GetTask, CancelTask, ...
  L3: Bindings -> JSON-RPC (primary) | gRPC (perf) | REST (simple)

BOUNDARY RULE
  MCP inside agents (tools/data) | A2A between agents (coordination)

DISCOVERY
  /.well-known/agent-card.json -> skills, auth, capabilities
  JWS/RFC 7515 signed over RFC 8785 canonical body
  Extended Card (auth-gated) replaces public card for session

TASK FSM (9 states)
  SUBMITTED -> WORKING -> COMPLETED | FAILED | CANCELED | REJECTED
                       -> INPUT_REQUIRED | AUTH_REQUIRED (interrupted, resume with same taskId)
  UNSPECIFIED (recovery)

UPDATE PATTERNS
  Sync (block)  |  SSE (stream)  |  Push (webhook)  |  Poll (GetTask)

COST FORMULA
  $/1K tasks = 1000 * hops * tokens_per_hop * $/token  (LLM dominates, wire < 1%)

RESILIENCE CHAIN
  Remote specialist (A2A) -> Local agent (MCP) -> Queue (drain later)
  Circuit breaker per remote agent: closed -> open -> half-open

AUTH
  API key | HTTP Bearer | OAuth 2.0/2.1 | OIDC | mTLS
  Cross-org: Signed Cards + mTLS + scoped OAuth tokens

MEDIA TYPE
  application/a2a+json

VERSION HEADER
  A2A-Version: 1.0 (empty = 0.3 fallback)

DOMINANT RISK (2026)
  Silent delegation failure: semantically wrong results pass structural validation
```
