# 16. A2A (Agent-to-Agent) Protocol

**Sub-areas covered**: Three-layer architecture (canonical data model in Protocol Buffers, 11 abstract operations, three wire bindings), Agent Card discovery with IANA-registered well-known URI and cryptographic signing (JWS/RFC 7515), task lifecycle state machine (9 states, strict transition rules, interrupted vs. terminal semantics), four communication patterns (sync, SSE streaming, push notifications, non-blocking poll), client-server role duality, orchestration topologies (centralized orchestrator vs. decentralized swarm), MCP-A2A complementary relationship ("MCP inside agents, A2A between agents"), protocol overhead dominated by agent reasoning not wire format, cost implications of multi-hop chains, task state management with context grouping and cross-task references, authentication surface (API key, HTTP Bearer, OAuth 2.0, OIDC, mTLS), Signed Agent Cards for cryptographic identity verification, Extended Agent Cards for authenticated-only capability disclosure, replay protection strategies, cross-agent prompt injection gaps, OpenTelemetry integration for distributed tracing, production failure modes (silent delegation failure as dominant 2026 risk, cross-boundary error smearing, observability gaps), enterprise deployment patterns (orchestrator-worker, cross-org federated, hierarchical delegation, human-in-the-loop, Kubernetes agent mesh), framework compatibility across 9+ platforms, Agent Payments Protocol (AP2), and two enterprise system-design scenarios with trade-off matrices

---

## 1. System Topology & Data Flow

A production A2A deployment spans five cooperating planes: a **control plane** handling agent discovery, identity verification, and authorization before any task is accepted; a **data plane** routing task messages between agents through load balancers, API gateways, and circuit breakers; **agent runtimes** encapsulating each agent's internal reasoning, MCP tool access, and LLM inference; a **persistence layer** maintaining task state, context continuity, and push notification configurations; and a **telemetry layer** providing distributed tracing across agent boundaries via OpenTelemetry and W3C Trace Context.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              CONTROL PLANE                                   │
│                                                                              │
│  ┌───────────────────┐   ┌──────────────────┐   ┌────────────────────────┐  │
│  │ Client Agent       │   │ Agent Card        │   │ Auth Server            │  │
│  │ (Orchestrator,     │──▶│ Registry          │──▶│ (OAuth 2.1 / mTLS /   │  │
│  │  LangGraph,        │   │ (/.well-known/    │   │  OIDC discovery;      │  │
│  │  CrewAI, ADK,      │   │  agent.json per   │   │  scoped per caller    │  │
│  │  custom runtime)   │   │  RFC 8615; signed │   │  per skill -- never   │  │
│  │                    │   │  via JWS/RFC 7515)│   │  blanket access)      │  │
│  │ Discovers remote   │   └────────┬─────────┘   └───────────┬────────────┘  │
│  │ agents, sends      │            │ discovery                │ token issue   │
│  │ tasks, coordinates │            │ + verification            │              │
│  └────────┬──────────┘            ▼                          │              │
│           │ SendMessage   ┌──────────────────┐               │              │
│           │ GetTask       │ Extended Agent    │◀──────────────┘              │
│           │ CancelTask    │ Card Endpoint     │                              │
│           │ Subscribe     │ (auth-gated;      │                              │
│           │               │  reveals hidden   │                              │
│           │               │  skills/caps)     │                              │
│           │               └────────┬─────────┘                              │
└───────────┼────────────────────────┼────────────────────────────────────────┘
            │ authorized request     │ extended capabilities
┌───────────▼────────────────────────▼────────────────────────────────────────┐
│                     DATA PLANE  (API GATEWAY / MESH)                         │
│                                                                              │
│  ┌────────────┐  ┌──────────────┐  ┌─────────────┐  ┌────────────────────┐  │
│  │ Protocol    │  │ Per-Agent     │  │ Rate        │  │ A2A-Version        │  │
│  │ Binding     │  │ Circuit       │  │ Limiter     │  │ Negotiation        │  │
│  │ Router      │  │ Breaker       │  │ + Quota     │  │ (header-based;     │  │
│  │ (JSON-RPC   │──▶│ (per remote  │──▶│ Manager    │──▶│  empty = v0.3      │  │
│  │  2.0 /      │  │  agent, not   │  │ (prevents  │  │  fallback)         │  │
│  │  gRPC /     │  │  per task)    │  │  cascade)  │  │                    │  │
│  │  REST)      │  │              │  │            │  │                    │  │
│  └──────┬─────┘  └──────┬──────┘  └──────┬──────┘  └────────┬───────────┘  │
└─────────┼───────────────┼────────────────┼────────────────────┼──────────────┘
          │ JSON-RPC/HTTPS │ gRPC/Proto    │ HTTP/JSON/REST     │
          │ (primary       │ (high-perf    │ (simple REST       │
          │  binding)      │  scenarios)   │  fallback)         │
┌─────────▼───────────────▼────────────────▼────────────────────▼──────────────┐
│                     AGENT RUNTIMES  (per remote agent)                        │
│                                                                               │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐    │
│  │ Agent A           │  │ Agent B           │  │ Agent C                  │    │
│  │ (Vertex AI Agent  │  │ (Bedrock Agent-   │  │ (Custom Python agent,    │    │
│  │  Builder; exposes │  │  Core; different  │  │  Google ADK; exposes     │    │
│  │  skills via Agent │  │  vendor, own LLM, │  │  skills, uses MCP        │    │
│  │  Card; internal   │  │  own MCP tools)   │  │  tools internally)       │    │
│  │  MCP tool access) │  │                   │  │                          │    │
│  └────────┬─────────┘  └──────────┬────────┘  └──────────┬───────────────┘    │
└───────────┼────────────────────────┼────────────────────────┼─────────────────┘
            │ task state             │ artifacts              │ push notifications
┌───────────▼────────────────────────▼────────────────────────▼─────────────────┐
│                           PERSISTENCE LAYER                                   │
│                                                                               │
│  ┌──────────────────┐  ┌───────────────────┐  ┌──────────┐  ┌─────────────┐ │
│  │ Task State Store  │  │ Context Registry   │  │ Push     │  │ Artifact    │ │
│  │ (9-state machine; │  │ (contextId groups  │  │ Notif    │  │ Store       │ │
│  │  server-generated │  │  related tasks;    │  │ Config   │  │ (Parts:     │ │
│  │  taskId; survives │  │  server-generated, │  │ (webhook │  │  text,      │ │
│  │  reconnection)    │  │  opaque to client) │  │  URLs;   │  │  file,      │ │
│  │                   │  │                    │  │  persists │  │  data)      │ │
│  │                   │  │  referenceTaskIds  │  │  until   │  │             │ │
│  │                   │  │  for cross-task    │  │  task     │  │             │ │
│  │                   │  │  dependencies      │  │  done)   │  │             │ │
│  └──────────────────┘  └───────────────────┘  └──────────┘  └─────────────┘ │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼───────────────────────────────────────────┐
│                    TELEMETRY / OBSERVABILITY SINKS                            │
│  OpenTelemetry with W3C Trace Context (traceparent/tracestate) across all   │
│  agent hops  |  taskId + contextId correlation  |  per-agent latency        │
│  breakdown  |  circuit-breaker state dashboard  |  push notification        │
│  delivery tracking  |  cross-boundary error attribution  |  API gateway     │
│  centralized policy enforcement (auth, rate limiting, quotas, logging)      │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A client agent needs a capability it does not have -- say, financial compliance review. It fetches the remote agent's Agent Card from `/.well-known/agent.json` (IANA-registered per RFC 8615), verifying the JWS signature against the card's declared signing key using JSON canonicalization (RFC 8785) before trusting the card's claims. (2) The client inspects the card's `skills` array, `supportedInputModes`/`supportedOutputModes` (MIME types), and `securitySchemes` to confirm the remote agent can handle the task and to determine authentication requirements. If the card declares `extendedAgentCard: true`, the client authenticates and fetches the extended card to discover hidden skills available only to authorized callers. (3) The client issues `SendMessage` via the chosen protocol binding (JSON-RPC 2.0 over HTTPS is the primary binding; gRPC with Protocol Buffers for high-throughput scenarios; HTTP/JSON/REST as a simple fallback). The `A2A-Version` header declares the requested protocol version. (4) The request crosses the data plane: an API gateway or service mesh routes based on the target agent's service endpoint, a per-agent circuit breaker prevents cascade failures, and rate limiters enforce quotas. (5) The remote agent creates a server-generated `taskId` (clients cannot set this), transitions the task to `SUBMITTED`, then to `WORKING` as it begins processing. The agent's internal reasoning -- LLM inference, MCP tool calls, plan formulation -- is opaque to the caller. (6) Results flow back as `Artifacts` (the reliable delivery mechanism for task outputs), not as Messages. The response pattern depends on configuration: synchronous block until terminal state, SSE streaming of incremental updates, push notification via webhook, or non-blocking return with subsequent polling via `GetTask`. (7) Every hop carries `traceparent`/`tracestate` headers, enabling a single trace ID across all agent boundaries for end-to-end distributed tracing.

---

## 2. Core Mechanics & Algorithms

### 2.1 Three-Layer Protocol Architecture

The v1.0 spec (March 2026) is organized into three layers, each decoupled from the others. The canonical data model is the single source of truth; operations are binding-agnostic; wire format is a deployment decision.

```
┌─────────────────────────────────────────────────────────────────────┐
│ Layer │ Purpose               │ Normative Source / Details           │
├───────┼───────────────────────┼──────────────────────────────────────┤
│ L1    │ Canonical Data Model  │ Protocol Buffers (a2a.proto). All   │
│       │ (core structures)     │ SDK bindings regenerated from proto. │
│       │                       │ Defines: Task, Message, Artifact,   │
│       │                       │ Part, Context, AgentCard, etc.      │
├───────┼───────────────────────┼──────────────────────────────────────┤
│ L2    │ Abstract Operations   │ 11 operations:                       │
│       │ (capabilities)        │ SendMessage, SendStreamingMessage,  │
│       │                       │ GetTask, ListTasks, CancelTask,     │
│       │                       │ SubscribeToTask, 4x push notif      │
│       │                       │ CRUD, GetExtendedAgentCard          │
├───────┼───────────────────────┼──────────────────────────────────────┤
│ L3    │ Protocol Bindings     │ JSON-RPC 2.0 / HTTPS (primary)     │
│       │ (wire protocols)      │ gRPC with protobuf (high-perf)     │
│       │                       │ HTTP/JSON/REST (simple fallback)    │
│       │                       │ Choice is deployment, not design.   │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Agent Cards -- Discovery and Identity

Agent Cards are the protocol's discovery mechanism: JSON metadata served at `/.well-known/agent.json` that tells the world what an agent can do, how to authenticate, and where to send requests.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        AGENT CARD STRUCTURE                          │
├─────────────────────────────────────────────────────────────────────┤
│ Field                    │ Purpose                                   │
├──────────────────────────┼───────────────────────────────────────────┤
│ name, description        │ Human-readable identity                   │
│ version                  │ Card version for cache invalidation       │
│ provider                 │ Organization info                         │
│ serviceEndpoint          │ URL where this agent accepts tasks        │
│ skills[]                 │ ID, description, tags, examples per skill │
│ supportedInput/Output    │ MIME types (text, audio, video, JSON)     │
│ securitySchemes          │ Auth requirements (OpenAPI-aligned)       │
│ capabilities             │ streaming, pushNotifications,             │
│                          │ extendedAgentCard flags                   │
│ agentInterface.tenant    │ Multi-tenancy support                     │
│ agentCardSignature       │ JWS (RFC 7515) over JSON-canonical       │
│                          │ (RFC 8785) card body                      │
│ extensions[]             │ Extension URIs                            │
└─────────────────────────────────────────────────────────────────────┘
```

**Extended Agent Cards** are served behind authentication. They allow agents to advertise sensitive or internal-only skills exclusively to authorized callers. When a client fetches an extended card, it replaces the cached public card for the duration of the authenticated session.

**Signed Agent Cards** solve the trust-at-discovery problem. Without signatures, a malicious agent can serve a card claiming skills it does not have or misrepresenting its identity. The verification flow:
1. Client fetches card from `/.well-known/agent.json`
2. Client extracts the `agentCardSignature` (JWS per RFC 7515)
3. Client canonicalizes the card body per RFC 8785
4. Client verifies the signature against the declared key
5. Verification failure = card rejected, agent untrusted

### 2.3 Data Model Hierarchy

```
                          ┌─────────────┐
                          │   Context    │
                          │ (contextId)  │
                          │  groups      │
                          │  related     │
                          │  tasks       │
                          └──────┬──────┘
                                 │ 1:N
                          ┌──────▼──────┐
                          │    Task      │
                          │ (taskId)     │◀── referenceTaskIds
                          │  server-gen  │    (cross-task deps)
                          │  stateful    │
                          └──┬───────┬──┘
                             │       │
                    1:N      │       │   1:N
               ┌─────────────┘       └─────────────┐
               │                                     │
        ┌──────▼──────┐                      ┌───────▼──────┐
        │   Message    │                      │   Artifact    │
        │ role: USER   │                      │ (task output; │
        │   or AGENT   │                      │  the reliable │
        │              │                      │  delivery     │
        │ NOT for task │                      │  mechanism)   │
        │ outputs.     │                      │               │
        │ NOT reliably │                      │ CAN be        │
        │ persisted.   │                      │ streamed.     │
        └──────┬──────┘                      └───────┬──────┘
               │ 1:N                                  │ 1:N
        ┌──────▼──────┐                      ┌───────▼──────┐
        │    Part      │                      │    Part       │
        │ - TextPart   │                      │ - TextPart    │
        │ - FilePart   │                      │ - FilePart    │
        │   (URI or    │                      │   (URI or     │
        │    inline)   │                      │    inline)    │
        │ - DataPart   │                      │ - DataPart    │
        │   (JSON)     │                      │   (JSON)      │
        └─────────────┘                      └──────────────┘
```

**Critical distinction**: Messages are for communication (initiating tasks, clarification, status updates). Artifacts are for task outputs (documents, data, results). Messages are NOT guaranteed to be persisted in task history. Artifacts ARE the reliable delivery mechanism. Using Messages to deliver task results is a spec violation.

### 2.4 Task Lifecycle State Machine

Nine states with strict transition constraints. Terminal states are irreversible. Interrupted states enable human-in-the-loop workflows.

```
                               ┌─────────────────────────────────┐
                               │         UNSPECIFIED              │
                               │  (unknown / recovery state)     │
                               └─────────────────────────────────┘


  ┌───────────┐     ┌──────────┐     ┌──────────────┐
  │ SUBMITTED  │────▶│ WORKING  │────▶│  COMPLETED   │  (terminal)
  └─────┬─────┘     └────┬─────┘     └──────────────┘
        │                 │
        │                 ├──────────▶┌──────────────┐
        │                 │           │   FAILED     │  (terminal)
        │                 │           └──────────────┘
        │                 │
        │                 ├──────────▶┌──────────────┐
        │                 │           │  CANCELED    │  (terminal)
        │                 │           └──────────────┘
        │                 │
        ├────────────────▶├──────────▶┌──────────────┐
        │ (at submission  │           │  REJECTED    │  (terminal)
        │  or later)      │           └──────────────┘
        │                 │
        │                 ├──────────▶┌────────────────────┐
        │                 │           │ INPUT_REQUIRED     │ (interrupted)
        │                 │           │  client sends new  │──┐
        │                 │           │  message to resume │  │
        │                 │           └────────────────────┘  │
        │                 │                                    │
        │                 ├──────────▶┌────────────────────┐  │
        │                 │           │ AUTH_REQUIRED      │  │
        │                 │           │  client provides   │──┤
        │                 │           │  credentials       │  │
        │                 │           └────────────────────┘  │
        │                 │                                    │
        │                 │◀───────────────────────────────────┘
        │                 │  (resume: new message with same
        │                 │   taskId and contextId)
        └─────────────────┘
```

**Transition rules**:
- Terminal states (COMPLETED, FAILED, CANCELED, REJECTED) cannot accept further messages -- server returns `UnsupportedOperationError`
- Interrupted states (INPUT_REQUIRED, AUTH_REQUIRED) resume via new message with same `taskId` and `contextId`
- REJECTED can fire during initial creation OR after the agent determines it cannot proceed
- Streams MUST close when task reaches terminal state
- `SubscribeToTask` on a terminal-state task returns `UnsupportedOperationError`
- `CancelTask` on a terminal task returns `TaskNotCancelableError`
- `SubscribeToTask` MUST return current state as the first event (for late-joining observers)

### 2.5 Communication Patterns

```
┌───────────────────┬────────────────────────────────────────────────────────┐
│ Pattern            │ Mechanics                                              │
├───────────────────┼────────────────────────────────────────────────────────┤
│ 1. Synchronous    │ returnImmediately: false (default). Client blocks     │
│    request-       │ until terminal or interrupted state. Best for quick   │
│    response       │ tasks under ~5s.                                      │
├───────────────────┼────────────────────────────────────────────────────────┤
│ 2. SSE Streaming  │ message/stream or tasks/subscribe. Server streams     │
│                   │ StreamResponse objects (exactly one of: task,         │
│                   │ message, statusUpdate, artifactUpdate). Events MUST   │
│                   │ be delivered in generation order, MUST NOT reorder.   │
│                   │ Broadcast to all active streams. Stream MUST close    │
│                   │ at terminal state. Closing one stream MUST NOT        │
│                   │ affect others.                                        │
├───────────────────┼────────────────────────────────────────────────────────┤
│ 3. Push Notifs    │ Async HTTP POST to client-provided webhook. Best for │
│    (webhooks)     │ long-running tasks (hours/days). Payloads are         │
│                   │ StreamResponse as JSON regardless of binding.         │
│                   │ Config persists until task completion or explicit     │
│                   │ deletion. Webhook endpoints MUST require auth.       │
├───────────────────┼────────────────────────────────────────────────────────┤
│ 4. Non-blocking   │ returnImmediately: true. Returns instantly. Caller   │
│    poll           │ follows up via GetTask, SubscribeToTask, or push.    │
└───────────────────┴────────────────────────────────────────────────────────┘
```

### 2.6 Client-Server Role Duality

Any agent can play both roles. A coding agent is a **client** when delegating a review to a QA agent, but a **remote (server)** agent when a PM agent requests code generation. This duality means the same agent runtime must handle both outbound discovery/delegation and inbound task acceptance.

### 2.7 Orchestration Topologies

**Centralized Orchestrator**: One lead agent owns the plan, discovers specialists via Agent Cards, delegates tasks via A2A, stitches results. Easier to reason about, debug, and audit. Default for most enterprise workflows.

**Decentralized Swarm**: No single orchestrator. Agents discover each other directly, share tasks peer-to-peer. More flexible and resilient; harder to trace and debug. Most real systems start centralized and evolve toward swarm as trust systems mature.

### 2.8 MCP-A2A Complementary Relationship

```
┌─────────────┬──────────────────────────┬───────────────────────────┐
│ Dimension    │ MCP                       │ A2A                        │
├─────────────┼──────────────────────────┼───────────────────────────┤
│ Connects     │ Agent to tools/data       │ Agent to agent             │
│              │ (vertical)                │ (horizontal/peer)          │
├─────────────┼──────────────────────────┼───────────────────────────┤
│ Analogy      │ USB port for peripherals  │ HTTP for web services      │
├─────────────┼──────────────────────────┼───────────────────────────┤
│ Launched by  │ Anthropic (Nov 2024)      │ Google (Apr 2025)          │
├─────────────┼──────────────────────────┼───────────────────────────┤
│ Governance   │ Linux Foundation (AAIF)   │ Linux Foundation (AAIF)    │
├─────────────┼──────────────────────────┼───────────────────────────┤
│ 2026 scale   │ 97M monthly SDK downloads │ 150+ orgs, 22K GH stars   │
├─────────────┼──────────────────────────┼───────────────────────────┤
│ When to use  │ Agent needs tools, data,  │ Multiple agents crossing   │
│              │ context                   │ ownership boundaries       │
└─────────────┴──────────────────────────┴───────────────────────────┘
```

**The reference architecture**: MCP inside agents (for tool and data access), A2A between agents (for coordination). Google ADK, Salesforce Agentforce, and ServiceNow Now Assist all implement both. The AAIF (Agentic AI Foundation, Dec 2025) -- co-founded by OpenAI, Anthropic, Google, Microsoft, AWS, Block -- governs both protocols under neutral governance.

**Decision rule**: Start with one agent + MCP tools. Add A2A only when you have genuine reasons for agent autonomy and specialization across organizational boundaries. Multi-agent systems are harder to debug, more expensive to run, and slower to respond.

### 2.9 Key Invariants

1. **Server-generated IDs**: Both `taskId` and `contextId` are server-generated. Clients cannot set them. Client-provided `taskId` for creation is NOT supported.
2. **Opaque execution**: Agents share skills and outputs without revealing internal reasoning, plans, or tool implementations. The remote agent is a black box.
3. **Modality agnostic**: Text, audio, video, structured data, forms, embedded UI components -- all conveyed via Parts with MIME types.
4. **Context consistency**: Agent MUST reject messages where `contextId` and `taskId` mismatch.
5. **Artifact-only outputs**: Task results go in Artifacts, not Messages. Messages are not guaranteed to persist.

---

## 3. Token Economics & NFR Analysis

### 3.1 Where the Cost Actually Lives

A2A introduces latency and cost, but the wire protocol itself is not the bottleneck. The dominant cost is **agent reasoning overhead**: parsing natural language instructions, formulating a plan, executing LLM inference and tool calls, and formulating a structured response. The JSON-RPC/gRPC wire format adds negligible bytes compared to the LLM inference cost on both sides.

```
┌──────────────────────────────────────────────────────────────────────┐
│                    COST BREAKDOWN PER A2A HOP                        │
├──────────────────────────────────┬───────────────────────────────────┤
│ Component                         │ Relative Cost                     │
├──────────────────────────────────┼───────────────────────────────────┤
│ LLM inference (remote agent)      │ 85-95% of total hop cost          │
│   - Input tokens (instructions)   │   ~40% of inference cost          │
│   - Output tokens (response)      │   ~50% of inference cost          │
│   - Reasoning tokens (if o-series)│   variable                       │
├──────────────────────────────────┼───────────────────────────────────┤
│ MCP tool calls within agent       │ 3-10% (API/DB costs)             │
├──────────────────────────────────┼───────────────────────────────────┤
│ A2A wire format overhead          │ < 1% (HTTP headers, JSON-RPC     │
│   - A2A-Version header            │  envelope, Agent Card caching)   │
│   - application/a2a+json body     │                                  │
│   - A2A-Extensions header         │                                  │
├──────────────────────────────────┼───────────────────────────────────┤
│ Network/infrastructure            │ < 1%                             │
└──────────────────────────────────┴───────────────────────────────────┘
```

**Estimated cost formula** [inferred -- no published benchmarks as of September 2026]:

```
Cost_per_1K_A2A_tasks ≈ (1000 × avg_hops × avg_tokens_per_hop × $/token) + (1000 × wire_overhead)
```

Worked example using conservative assumptions:

```
┌──────────────────────────────┬──────────────────────────────────────────┐
│ Assumption                    │ Value                                     │
├──────────────────────────────┼──────────────────────────────────────────┤
│ Average hops per task         │ 2 (orchestrator → specialist)             │
│ Average tokens per hop        │ 5,000 (input + output combined)           │
│ Model                         │ Claude Sonnet ($3/M input, $15/M output)  │
│   blended rate (40/60 split)  │ ~$10.20/M tokens                          │
│ Wire overhead per task        │ ~$0.0001 (HTTP, DNS, TLS, serialization)  │
│ MCP tool calls per hop        │ ~$0.005 (1-2 API/DB calls avg)            │
├──────────────────────────────┼──────────────────────────────────────────┤
│ Cost per task                 │ 2 × 5,000 × $10.20/1M + $0.0001 + $0.01 │
│                               │ ≈ $0.102 + $0.01 ≈ $0.112                │
├──────────────────────────────┼──────────────────────────────────────────┤
│ Cost per 1,000 tasks          │ ≈ $112                                    │
└──────────────────────────────┴──────────────────────────────────────────┘
```

Key cost drivers to watch: (1) hop count -- each additional hop roughly doubles LLM cost; (2) reasoning-heavy models (o-series) can 3-10x the token count per hop; (3) retry storms under transient failures multiply cost without delivering value. The wire overhead is negligible at any scale -- optimization effort belongs on reducing hops, caching agent reasoning, and right-sizing models per skill.

### 3.2 Latency Profiles

> **Gap**: No published p50/p99 latency benchmarks exist as of September 2026. All assessments below are qualitative estimates.

```
┌───────────────────────────┬───────────────────────────────────────────┐
│ Pattern                    │ Expected Latency                           │
├───────────────────────────┼───────────────────────────────────────────┤
│ Single-hop sync            │ 2-30s (dominated by remote agent LLM      │
│                            │ inference time and task complexity)        │
├───────────────────────────┼───────────────────────────────────────────┤
│ Direct API call (baseline) │ Sub-100ms                                  │
├───────────────────────────┼───────────────────────────────────────────┤
│ Two-hop chain              │ 4-60s (additive; each hop incurs full     │
│                            │ agent reasoning cycle)                    │
├───────────────────────────┼───────────────────────────────────────────┤
│ Three-hop chain            │ 6-90s (user-facing latency unacceptable  │
│                            │ for synchronous patterns)                 │
├───────────────────────────┼───────────────────────────────────────────┤
│ SSE streaming              │ First token: 1-5s. Perceived latency     │
│                            │ much lower than full sync.                │
├───────────────────────────┼───────────────────────────────────────────┤
│ Push notification          │ Decoupled entirely. Client receives       │
│                            │ webhook when done. Minutes to days.       │
└───────────────────────────┴───────────────────────────────────────────┘
```

**Design implication**: If you chain 3+ agents synchronously, user-facing latency becomes unacceptable. Design A2A communications asynchronously using push notifications and long-running task management. Parallel fan-out to independent agents (instead of serial chaining) reduces wall-clock time.

### 3.3 Throughput Characteristics

```
┌───────────────────┬──────────────────────────────────────────────────┐
│ Binding            │ Throughput Characteristics                        │
├───────────────────┼──────────────────────────────────────────────────┤
│ JSON-RPC/HTTPS    │ Standard HTTP request/response throughput.        │
│                   │ Bottleneck is agent processing speed, not wire.  │
├───────────────────┼──────────────────────────────────────────────────┤
│ gRPC/Protobuf     │ Lower serialization overhead vs JSON. Added for │
│                   │ high-performance scenarios. Multiplexed streams. │
├───────────────────┼──────────────────────────────────────────────────┤
│ SSE streaming     │ Incremental delivery reduces perceived latency.  │
│                   │ Server pushes as tokens generate.                │
├───────────────────┼──────────────────────────────────────────────────┤
│ Push notifications│ Fully decoupled. No connection held. Client      │
│                   │ receives POST at webhook when task state changes.│
└───────────────────┴──────────────────────────────────────────────────┘
```

### 3.4 Wire Format Overhead

- IANA-registered media type: `application/a2a+json`
- Custom HTTP headers: `A2A-Version`, `A2A-Extensions` (prefixed with `a2a-`)
- Protocol version negotiation adds one header per request
- Agent Card caching (standard HTTP caching directives) amortizes discovery cost across many requests
- gRPC binding uses Protocol Buffers regenerated from `a2a.proto` -- tighter serialization for high-volume internal traffic

### 3.5 NFR Trade-Off Matrix

```
┌─────────────────────┬──────────────────┬──────────────────────────────┐
│ NFR                  │ Direct API Call   │ A2A Protocol                  │
├─────────────────────┼──────────────────┼──────────────────────────────┤
│ Latency              │ Sub-100ms         │ 2-30s per hop (agent reason) │
│ Complexity           │ Minimal            │ Discovery, lifecycle, auth   │
│ Interoperability     │ Point-to-point,   │ Vendor-neutral, linear       │
│                      │ N^2 integrations  │ scaling                      │
│ Debugging            │ Standard HTTP      │ Distributed tracing across   │
│                      │ tracing           │ agent boundaries required    │
│ Security surface     │ Single endpoint    │ Each agent is attack surface │
│ Flexibility          │ Rigid API          │ Skill-based dynamic          │
│                      │ contracts         │ delegation                   │
│ Human-in-the-loop    │ Manual             │ Native via INPUT_REQUIRED    │
│ Long-running tasks   │ Polling/callbacks  │ Native async state machine   │
│ Cost per request     │ One API call       │ Full LLM inference per hop   │
└─────────────────────┴──────────────────┴──────────────────────────────┘
```

### 3.6 NFR Targets for Production A2A Deployments

**Availability**: Composite availability for an N-agent chain follows the weakest-link rule: `A_chain = A_1 × A_2 × ... × A_N`. For a 3-agent chain where each agent delivers 99.9% availability, the chain availability is 99.9%^3 = 99.7% -- roughly 26 hours of downtime per year instead of 8.7 hours. Implication: minimize chain depth, and front critical paths with fallback chains (section 5.1) to recover from individual agent outages.

```
┌──────────────────────────┬──────────────────────────────────────────────┐
│ NFR Target                │ A2A Deployment Guidance                       │
├──────────────────────────┼──────────────────────────────────────────────┤
│ Availability              │ 99.9%^N for N agents in chain. Budget for    │
│                           │ weakest agent. Use fallback chains and       │
│                           │ circuit breakers to compensate.              │
├──────────────────────────┼──────────────────────────────────────────────┤
│ RPO (Recovery Point       │ Task state is server-owned and persisted.    │
│ Objective)                │ RPO = last persisted task state update.      │
│                           │ In-flight LLM reasoning between state       │
│                           │ transitions is lost on crash -- acceptable   │
│                           │ because the task can be re-submitted.        │
├──────────────────────────┼──────────────────────────────────────────────┤
│ RTO (Recovery Time        │ Agent rediscovery (re-fetch Agent Card) +    │
│ Objective)                │ task resume via GetTask with stored taskId.  │
│                           │ Typical RTO: seconds for warm agents, 1-5   │
│                           │ minutes if agent must cold-start + re-auth. │
├──────────────────────────┼──────────────────────────────────────────────┤
│ Compliance                │ EU AI Act: cross-org agent chains may        │
│                           │ constitute "AI systems" requiring risk       │
│                           │ classification, human oversight mandates,    │
│                           │ and transparency logging for high-risk use   │
│                           │ cases. GDPR: data parts flowing between     │
│                           │ agents across organizational boundaries      │
│                           │ are data transfers -- require lawful basis,  │
│                           │ data processing agreements, and potentially  │
│                           │ SCCs for cross-border flows. Log every       │
│                           │ cross-org data part transmission with        │
│                           │ purpose and legal basis.                     │
└──────────────────────────┴──────────────────────────────────────────────┘
```

---

## 4. Distributed Resilience & Security

### 4.1 Task State Management

Task state is server-owned. Clients reconnect via `GetTask` with a previously received `taskId` to recover current state. This makes the protocol resilient to client-side crashes -- the server's task store is the source of truth.

**Idempotency guarantees by operation:**

```
┌──────────────────────────────┬──────────────────────────────────────┐
│ Operation                     │ Idempotency                           │
├──────────────────────────────┼──────────────────────────────────────┤
│ GetTask, ListTasks            │ Naturally idempotent (read-only)     │
│ SendMessage                   │ MAY be idempotent (agents may dedup │
│                               │ on messageId, not guaranteed)        │
│ CancelTask                    │ Idempotent (repeated cancel = noop) │
│ DeletePushNotificationConfig  │ Idempotent                           │
└──────────────────────────────┴──────────────────────────────────────┘
```

**Context management**: `contextId` groups related tasks into a conversation-like thread. Server-generated, opaque to clients. If a client provides a `contextId` the agent cannot accept, the agent MUST reject with an error. `referenceTaskIds` enable explicit dependencies between tasks for complex workflows.

**Pagination** (ListTasks): Cursor-based with `pageToken`/`nextPageToken`. Default page size 50, min 1, max 100. Sorted by status timestamp descending. `nextPageToken` is always present (empty string = no more pages).

### 4.2 Authentication Schemes

Authentication is handled at the HTTP transport layer, not within A2A payloads. Identity flows through HTTP headers via standard mechanisms declared in Agent Cards.

```
┌───────────────────────────┬──────────────────────────────────────────┐
│ Scheme                     │ Use Case                                  │
├───────────────────────────┼──────────────────────────────────────────┤
│ APIKeySecurityScheme       │ API key in header or query param.         │
│                            │ Simplest. Internal/dev only.              │
├───────────────────────────┼──────────────────────────────────────────┤
│ HTTPAuthSecurityScheme     │ HTTP Bearer tokens. Common for service-  │
│                            │ to-service within same org.              │
├───────────────────────────┼──────────────────────────────────────────┤
│ OAuth2SecurityScheme       │ OAuth 2.0 (authorization code, client   │
│                            │ credentials, device code). Cross-org.   │
├───────────────────────────┼──────────────────────────────────────────┤
│ OpenIdConnectSecurityScheme│ OIDC discovery. Enterprise SSO.          │
├───────────────────────────┼──────────────────────────────────────────┤
│ MutualTlsSecurityScheme   │ mTLS with X.509 certs. Strongest        │
│                            │ machine-to-machine identity.            │
└───────────────────────────┴──────────────────────────────────────────┘
```

**Production recommendation**: mTLS between agents inside the same trust boundary; OAuth 2.1 with scoped tokens across organizational boundaries. Credentials MUST NOT share a root key -- scope per caller per skill.

**In-task authorization**: Agents can request credentials mid-task via `AUTH_REQUIRED` state, enabling just-in-time authorization for sensitive operations without front-loading all permissions at task start.

### 4.3 Authorization and Least Privilege

- OAuth scopes grant access to specific skills, not blanket agent access
- Servers MUST NOT reveal existence of resources the client is not authorized to access
- Servers SHOULD NOT distinguish between "does not exist" and "not authorized" (prevents information leakage)
- ListTasks MUST return only tasks visible to the authenticated client
- **Authorization creep anti-pattern**: Do not pass broad bearer tokens from orchestrator to sub-agents. Each delegation hop should scope down credentials.

### 4.4 Transport Security

- TLS required for all production communication
- HTTPS with TLS 1.3+ and strong cipher suites
- Post-quantum cryptography (PQC) cipher suites recommended as they become available
- All webhook endpoints for push notifications MUST use HTTPS
- Webhook endpoints MUST require authentication from the sending server

### 4.5 Replay Protection

The spec does not mandate replay protection by default. Recommended controls (ideally combined):

```
┌───────────────────────────────────────────────────────────────────────┐
│                     REPLAY PROTECTION STRATEGIES                      │
├───────────────────────────────────────────────────────────────────────┤
│ 1. UUIDv7 request ID as nonce (sorts by time, detects replayed IDs)  │
│ 2. Date header validated within 5-minute clock skew window           │
│ 3. Message Authentication Codes (MAC) on request body               │
├───────────────────────────────────────────────────────────────────────┤
│ For high-value operations:                                           │
│ 4. Sender-constrained tokens via mTLS-bound tokens (RFC 8705)       │
│ 5. DPoP (Demonstrating Proof of Possession, RFC 9449)               │
└───────────────────────────────────────────────────────────────────────┘
```

### 4.6 Cross-Agent Prompt Injection

A2A provides **no protocol-level controls** against prompt injection. This is a deliberate design boundary: the protocol handles transport and coordination, not content validation. Mitigation relies entirely on defense-in-depth within each agent:
- TLS for transport integrity
- Signed Agent Cards for identity verification at discovery
- Authentication and authorization for access control
- Least privilege scoping on every delegation hop
- Input validation and sanitization within each agent's runtime
- Isolation of untrusted content from system prompts

### 4.7 Error Propagation

**A2A-specific error types:**

```
┌──────────────────────────────────────┬───────────────────────────────┐
│ Error                                 │ When                           │
├──────────────────────────────────────┼───────────────────────────────┤
│ TaskNotFoundError                     │ ID doesn't exist / no access  │
│ TaskNotCancelableError                │ Already in terminal state     │
│ PushNotificationNotSupportedError     │ Agent lacks push capability   │
│ UnsupportedOperationError             │ Operation not supported       │
│ ContentTypeNotSupportedError          │ Unsupported MIME in parts     │
│ InvalidAgentResponseError             │ Response violates spec        │
│ ExtendedAgentCardNotConfiguredError   │ Declared but not configured   │
│ ExtensionSupportRequiredError         │ Required extension missing    │
│ VersionNotSupportedError              │ Requested A2A-Version bad     │
└──────────────────────────────────────┴───────────────────────────────┘
```

**Transport-level error mapping:**

```
┌─────────────────┬───────────────┬──────────────────┬─────────────────┐
│ Condition        │ HTTP           │ gRPC              │ JSON-RPC         │
├─────────────────┼───────────────┼──────────────────┼─────────────────┤
│ Authentication   │ 401            │ UNAUTHENTICATED   │ -                │
│ Authorization    │ 403            │ PERMISSION_DENIED │ -                │
│ Validation       │ 400            │ INVALID_ARGUMENT  │ -32602           │
│ Not found        │ 404            │ NOT_FOUND         │ -                │
│ System error     │ 500-503        │ INTERNAL-         │ -32603           │
│                  │               │ UNAVAILABLE       │                  │
└─────────────────┴───────────────┴──────────────────┴─────────────────┘
```

All errors MUST include error code + human-readable message. Optional error details array uses ProtoJSON `Any` representation (google.rpc error model recommended).

**Retry logic and circuit-breaking are deliberately left to client implementation** -- the protocol specifies state communication, not resilience policy.

### 4.8 Observability

OpenTelemetry with W3C Trace Context headers (`traceparent`/`tracestate`) on every A2A call enables end-to-end distributed tracing. A single trace ID across all agent hops eliminates a class of debugging pain -- but must be manually layered in; it is not built into the protocol.

**Known observability gaps (as of Sept 2026):**
- "delegated to research_agent" in logs is NOT observability -- need structured traces surviving handoffs
- No built-in distributed tracing (must layer OpenTelemetry manually)
- SREs report inability to localize latency spikes across agent hops
- No standardized metrics format for agent-to-agent interactions
- The reliability engineering discipline for A2A is not mature -- the protocol is production-grade, the operational practice is not

### 4.9 Production Failure Modes

**Silent delegation failure** is the dominant failure mode in 2026. The orchestrator sends a task to a specialist, receives a plausible "completed" event, the workflow continues -- but the specialist used wrong data, hit stale state, or returned incorrect results. No protocol-level mechanism catches semantic errors. This is fundamentally harder than catching transport failures.

**Cross-boundary error smearing**: When an orchestrator delegates to a sub-agent and the sub-agent fails silently, who carries the error budget? How do you instrument the boundary? These questions have no consensus answers yet.

**Schema gap**: The spec does not standardize per-skill JSON Schema for Part body content. A client knows a skill accepts `application/json` but not the exact object structure. Mitigation: embed OpenAPI fragment or JSON Schema in skill description, or use Extensions.

### 4.10 Failure Taxonomy

Classifying failures by recoverability determines the correct response strategy. Conflating transient and permanent failures (e.g., retrying an auth failure) wastes cost and delays detection. Ignoring poison-pill failures (semantically wrong but structurally valid responses) is the dominant production risk.

```
┌───────────────┬─────────────────────────────────┬──────────────────────────────┐
│ Category       │ Examples                         │ Response Strategy              │
├───────────────┼─────────────────────────────────┼──────────────────────────────┤
│ Transient      │ Network timeouts, 429 (rate      │ Retry with exponential         │
│                │ limited), 503 (agent overloaded), │ backoff + jitter. Circuit      │
│                │ DNS resolution blips, TLS         │ breaker trips after N          │
│                │ handshake timeout                 │ consecutive transient          │
│                │                                   │ failures. Resume via GetTask   │
│                │                                   │ after reconnection.            │
├───────────────┼─────────────────────────────────┼──────────────────────────────┤
│ Permanent      │ Agent Card not found (404),       │ Fail fast. Do NOT retry.       │
│                │ unsupported skill requested,      │ Surface error to orchestrator  │
│                │ authentication failure (401),     │ immediately. Log with error    │
│                │ authorization denied (403),       │ code for operator alerting.    │
│                │ VersionNotSupportedError,         │ Orchestrator should attempt    │
│                │ ContentTypeNotSupportedError      │ fallback agent or degrade      │
│                │                                   │ gracefully.                    │
├───────────────┼─────────────────────────────────┼──────────────────────────────┤
│ Poison-pill    │ Silent delegation failure:        │ Detect via output validation   │
│                │ semantically wrong results that   │ (dedicated validator agent or  │
│                │ pass structural validation (e.g., │ deterministic assertion checks │
│                │ agent returns plausible but       │ on artifacts). Quarantine      │
│                │ factually incorrect analysis).    │ suspect agent: circuit-break   │
│                │ Cross-agent prompt injection:     │ it, alert operator, exclude    │
│                │ malicious content in one agent's  │ from future delegation until   │
│                │ output hijacks the receiving      │ manual review. Log full        │
│                │ agent's reasoning.                │ request-response chain with    │
│                │                                   │ trace context for forensics.   │
└───────────────┴─────────────────────────────────┴──────────────────────────────┘
```

**Idempotency guarantees by operation** (summary for failure-handling decisions):

```
┌──────────────────────────┬─────────────────────────────────────────────────┐
│ Operation                 │ Idempotency Guarantee                            │
├──────────────────────────┼─────────────────────────────────────────────────┤
│ GetTask, ListTasks        │ Idempotent (read-only, safe to retry freely)    │
│ SendMessage               │ MAY be idempotent (dedup on messageId is agent- │
│                           │ specific, not guaranteed by protocol)            │
│ CancelTask                │ Idempotent (repeated cancel on terminal = noop) │
│ DeletePushNotifConfig     │ Idempotent (delete of absent config = noop)     │
│ SetPushNotifConfig        │ Idempotent (upsert semantics)                   │
│ GetPushNotifConfig        │ Idempotent (read-only)                          │
└──────────────────────────┴─────────────────────────────────────────────────┘
```

The critical gap: `SendMessage` is not guaranteed idempotent. In transient-failure retry scenarios, the same message may create duplicate tasks. Mitigation: include a client-generated `messageId` and implement server-side deduplication on that ID, or accept at-least-once semantics and make downstream processing idempotent.

### 4.11 Enterprise Security Posture

**1. Zero-Trust Agent-to-Agent Communication**

Every agent-to-agent message is treated as untrusted regardless of network position. Trust is established per-request, not per-connection:
- **Identity verification**: Signed Agent Cards (JWS/RFC 7515) verify the remote agent's identity at discovery. The client rejects any card with an invalid or missing signature before sending any task.
- **Per-skill authorization**: OAuth 2.1 scopes are granted per caller per skill, not blanket agent access. An agent authorized for `check_stock` cannot invoke `reserve_inventory` without a separate scope grant.
- **Credential scoping on delegation**: When an orchestrator delegates to a sub-agent, it issues a down-scoped token for only the skills needed for that specific task. The sub-agent cannot use the orchestrator's broader credentials (authorization creep prevention).
- **mTLS for transport identity**: Within the same trust boundary, mutual TLS provides machine-to-machine identity verification at the transport layer, independent of application-level auth.

**2. PII Filtering in Cross-Agent Data Flows**

Data parts (text, file, structured data) flowing between agents must be treated as potential PII carriers, especially across organizational boundaries:
- **Pre-transmission PII detection**: The sending agent runs PII detection (named entity recognition for names, addresses, financial identifiers, health data) on outbound data parts before transmission. Detected PII is either redacted, tokenized, or flagged for explicit consent verification depending on the data classification policy.
- **Receiving agent validation**: The receiving agent independently validates that inbound data parts comply with its own data handling policy. An agent operating under HIPAA constraints rejects data parts containing unencrypted PHI regardless of what the sender claims.
- **Data minimization**: Agents should transmit the minimum data parts necessary for the requested skill. A compliance review agent does not need raw customer PII -- it needs anonymized transaction patterns.

**3. Immutable Audit Logs for Cross-Agent Decision Chains**

Every task state transition across agent boundaries is logged to an append-only audit store:
- **What is logged**: Task ID, context ID, source agent identity, target agent identity, operation (SendMessage/GetTask/CancelTask), state transition (e.g., SUBMITTED -> WORKING), timestamp, W3C trace context (traceparent/tracestate), and a hash of the data parts exchanged (not the content itself, to avoid logging PII).
- **Chain-of-custody**: The trace context links every log entry across all agent hops into a single auditable decision chain. Given a final output artifact, an auditor can reconstruct which agents contributed, in what order, and what state transitions occurred.
- **Immutability**: Logs are written to an append-only store (e.g., AWS QLDB, Azure Immutable Blob, or a Merkle-tree-based log). No agent or operator can retroactively alter the audit trail.
- **Retention**: Log retention aligns with the regulatory regime governing the workflow (e.g., 7 years for financial services under SOX, 6 years under GDPR for exercised data subject rights).

---

## 5. Production Enterprise Code

### 5.1 A2A Client with Retries, Circuit Breaker, and Structured Logging

```python
"""
Production A2A client with exponential backoff, jitter, circuit breaker,
fallback chains, and structured logging. Runnable against any A2A-compliant
remote agent.

Requirements:
    pip install httpx tenacity structlog
"""

import enum
import time
import uuid
import random
import httpx
import structlog
from dataclasses import dataclass, field
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
    retry_if_exception_type,
    before_sleep_log,
)
from typing import Any

logger = structlog.get_logger()


# ─── Circuit Breaker ────────────────────────────────────────────────

class CircuitState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Per-agent circuit breaker. Trips after `failure_threshold` consecutive
    failures. Stays open for `recovery_timeout` seconds, then allows one
    probe request (half-open). If the probe succeeds, circuit closes.
    If it fails, circuit reopens."""

    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    _half_open_admitted: bool = False

    def allow_request(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.monotonic() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self._half_open_admitted = False
                logger.info("circuit_breaker_half_open")
                return True
            return False
        # HALF_OPEN: allow exactly one probe
        if not self._half_open_admitted:
            self._half_open_admitted = True
            return True
        return False

    def record_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            logger.info("circuit_breaker_closed", reason="probe_success")
        self.state = CircuitState.CLOSED
        self.failure_count = 0

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            logger.warning("circuit_breaker_reopened", reason="probe_failed")
        elif self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "circuit_breaker_opened",
                failure_count=self.failure_count,
                threshold=self.failure_threshold,
            )


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open and the request is rejected."""
    pass


# ─── A2A Client ─────────────────────────────────────────────────────

@dataclass
class A2AClient:
    """Production A2A client for a single remote agent. Handles:
    - Agent Card fetching and caching
    - SendMessage with exponential backoff + jitter
    - Per-agent circuit breaker
    - GetTask for polling
    - Structured logging with trace context
    """

    base_url: str
    auth_token: str | None = None
    a2a_version: str = "1.0"
    timeout: float = 60.0
    circuit_breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    _agent_card: dict[str, Any] | None = field(default=None, init=False)
    _client: httpx.Client = field(init=False)

    def __post_init__(self) -> None:
        headers = {
            "Content-Type": "application/a2a+json",
            "A2A-Version": self.a2a_version,
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=self.timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "A2AClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ── Agent Card Discovery ──

    def fetch_agent_card(self) -> dict[str, Any]:
        """Fetch and cache the remote agent's public Agent Card."""
        log = logger.bind(remote_agent=self.base_url)
        resp = self._client.get("/.well-known/agent.json")
        resp.raise_for_status()
        self._agent_card = resp.json()
        log.info(
            "agent_card_fetched",
            agent_name=self._agent_card.get("name"),
            skills_count=len(self._agent_card.get("skills", [])),
            capabilities=self._agent_card.get("capabilities", {}),
        )
        return self._agent_card

    # ── SendMessage with Retry + Circuit Breaker ──

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        wait=wait_exponential_jitter(initial=1, max=30, jitter=5),
        stop=stop_after_attempt(4),
        before_sleep=before_sleep_log(logger, "warning"),
        reraise=True,
    )
    def _send_message_with_retry(
        self, payload: dict[str, Any], trace_id: str
    ) -> dict[str, Any]:
        """Internal: send JSON-RPC request with tenacity retry policy.
        Retries on transport errors and 5xx responses.
        Does NOT retry on 4xx (client errors are not transient)."""
        resp = self._client.post(
            "/",
            json=payload,
            headers={"traceparent": f"00-{trace_id}-{uuid.uuid4().hex[:16]}-01"},
        )
        if resp.status_code >= 500:
            resp.raise_for_status()  # triggers retry
        resp.raise_for_status()
        return resp.json()

    def send_message(
        self,
        text: str,
        task_id: str | None = None,
        context_id: str | None = None,
        return_immediately: bool = False,
    ) -> dict[str, Any]:
        """Send a message to the remote agent. Creates a new task or
        continues an existing one.

        Returns the JSON-RPC response containing the Task object.
        Raises CircuitOpenError if the circuit breaker is open.
        """
        if not self.circuit_breaker.allow_request():
            raise CircuitOpenError(
                f"Circuit open for {self.base_url}; "
                f"retry after {self.circuit_breaker.recovery_timeout}s"
            )

        trace_id = uuid.uuid4().hex
        message_id = str(uuid.uuid4())
        log = logger.bind(
            trace_id=trace_id,
            message_id=message_id,
            remote_agent=self.base_url,
            task_id=task_id,
        )

        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": message_id,
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": message_id,
                    "role": "user",
                    "parts": [{"kind": "text", "text": text}],
                },
                "configuration": {
                    "returnImmediately": return_immediately,
                },
            },
        }
        if task_id:
            payload["params"]["taskId"] = task_id
        if context_id:
            payload["params"]["contextId"] = context_id

        log.info("a2a_send_message", text_length=len(text))
        start = time.monotonic()

        try:
            result = self._send_message_with_retry(payload, trace_id)
            elapsed = time.monotonic() - start
            self.circuit_breaker.record_success()

            task_data = result.get("result", {})
            log.info(
                "a2a_message_sent",
                elapsed_s=round(elapsed, 3),
                task_id=task_data.get("id"),
                task_status=task_data.get("status", {}).get("state"),
            )
            return result

        except Exception as exc:
            elapsed = time.monotonic() - start
            self.circuit_breaker.record_failure()
            log.error(
                "a2a_send_failed",
                elapsed_s=round(elapsed, 3),
                error=str(exc),
                circuit_state=self.circuit_breaker.state.value,
            )
            raise

    # ── GetTask (polling) ──

    def get_task(self, task_id: str) -> dict[str, Any]:
        """Poll for current task state. Naturally idempotent."""
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tasks/get",
            "params": {"id": task_id},
        }
        resp = self._client.post("/", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ── CancelTask ──

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        """Cancel a running task. Idempotent -- repeated cancel is a noop."""
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tasks/cancel",
            "params": {"id": task_id},
        }
        resp = self._client.post("/", json=payload)
        resp.raise_for_status()
        return resp.json()


# ─── Fallback Chain ─────────────────────────────────────────────────

@dataclass
class AgentFallbackChain:
    """Routes requests through a prioritized list of A2A agents.
    If the primary agent's circuit is open or the request fails after
    all retries, falls through to the next agent. Logs every fallback
    decision for audit.

    Usage:
        chain = AgentFallbackChain(agents=[
            A2AClient("https://primary-agent.corp.internal", auth_token="..."),
            A2AClient("https://secondary-agent.corp.internal", auth_token="..."),
            A2AClient("https://tertiary-agent.vendor.com", auth_token="..."),
        ])
        result = chain.send("Analyze Q3 revenue forecast")
    """

    agents: list[A2AClient]

    def send(
        self,
        text: str,
        return_immediately: bool = False,
    ) -> dict[str, Any]:
        """Try each agent in order. Returns the first successful response.
        Raises the last exception if all agents fail."""
        last_exc: Exception | None = None

        for i, agent in enumerate(self.agents):
            log = logger.bind(
                agent_index=i,
                agent_url=agent.base_url,
                total_agents=len(self.agents),
            )
            try:
                result = agent.send_message(
                    text=text,
                    return_immediately=return_immediately,
                )
                if i > 0:
                    log.warning("a2a_fallback_used", fallback_index=i)
                return result

            except CircuitOpenError:
                log.warning("a2a_agent_circuit_open", skipping=True)
                last_exc = CircuitOpenError(f"Circuit open: {agent.base_url}")
                continue

            except Exception as exc:
                log.error("a2a_agent_failed", error=str(exc))
                last_exc = exc
                continue

        raise RuntimeError(
            f"All {len(self.agents)} agents exhausted"
        ) from last_exc
```

### 5.2 A2A Task Poller with Graceful Degradation

```python
"""
Async task poller that monitors long-running A2A tasks with exponential
backoff polling intervals and graceful degradation when the remote
agent becomes unreachable.

Requirements:
    pip install httpx structlog
"""

import asyncio
import time
import uuid
import httpx
import structlog
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = structlog.get_logger()

TERMINAL_STATES = {"completed", "failed", "canceled", "rejected"}
INTERRUPTED_STATES = {"input_required", "auth_required"}


@dataclass
class TaskPoller:
    """Polls an A2A task until it reaches a terminal or interrupted state.

    Features:
    - Exponential backoff: starts at `initial_interval`, doubles up to `max_interval`
    - Timeout: gives up after `timeout_seconds` total elapsed time
    - Graceful degradation: if the remote agent is unreachable, continues
      polling (with logged warnings) rather than failing immediately,
      up to `max_consecutive_errors` before giving up
    - Callback: invokes `on_status_change` when task state changes
    """

    client: httpx.AsyncClient
    base_url: str
    task_id: str
    initial_interval: float = 2.0
    max_interval: float = 60.0
    timeout_seconds: float = 3600.0
    max_consecutive_errors: int = 10
    on_status_change: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None
    _last_state: str | None = field(default=None, init=False)

    async def poll_until_terminal(self) -> dict[str, Any]:
        """Poll GetTask until the task reaches a terminal or interrupted state.
        Returns the final task object."""
        log = logger.bind(task_id=self.task_id, remote=self.base_url)
        interval = self.initial_interval
        start = time.monotonic()
        consecutive_errors = 0

        while True:
            elapsed = time.monotonic() - start
            if elapsed > self.timeout_seconds:
                log.error("a2a_poll_timeout", elapsed_s=round(elapsed, 1))
                raise TimeoutError(
                    f"Task {self.task_id} did not complete within "
                    f"{self.timeout_seconds}s"
                )

            try:
                payload = {
                    "jsonrpc": "2.0",
                    "id": str(uuid.uuid4()),
                    "method": "tasks/get",
                    "params": {"id": self.task_id},
                }
                resp = await self.client.post(
                    f"{self.base_url}/",
                    json=payload,
                    headers={"Content-Type": "application/a2a+json"},
                )
                resp.raise_for_status()
                result = resp.json()
                consecutive_errors = 0

            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                consecutive_errors += 1
                log.warning(
                    "a2a_poll_error",
                    error=str(exc),
                    consecutive_errors=consecutive_errors,
                    max_errors=self.max_consecutive_errors,
                )
                if consecutive_errors >= self.max_consecutive_errors:
                    raise RuntimeError(
                        f"Task {self.task_id}: {consecutive_errors} consecutive "
                        f"poll failures, giving up"
                    ) from exc
                await asyncio.sleep(interval)
                interval = min(interval * 2, self.max_interval)
                continue

            task = result.get("result", {})
            state = task.get("status", {}).get("state", "unknown").lower()

            if state != self._last_state:
                log.info(
                    "a2a_task_state_change",
                    old_state=self._last_state,
                    new_state=state,
                    elapsed_s=round(elapsed, 1),
                )
                if self.on_status_change:
                    await self.on_status_change(state, task)
                self._last_state = state

            if state in TERMINAL_STATES:
                log.info(
                    "a2a_task_terminal",
                    final_state=state,
                    total_elapsed_s=round(time.monotonic() - start, 1),
                    artifacts_count=len(task.get("artifacts", [])),
                )
                return task

            if state in INTERRUPTED_STATES:
                log.info("a2a_task_interrupted", state=state)
                return task

            await asyncio.sleep(interval)
            interval = min(interval * 2, self.max_interval)


# ─── Usage Example ──────────────────────────────────────────────────

async def run_long_task() -> None:
    """Demonstrates: fire-and-forget submission, then poll with backoff."""
    async with httpx.AsyncClient() as http:
        # Step 1: Submit task (non-blocking)
        submit_payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "role": "user",
                    "parts": [{"kind": "text", "text": "Generate Q3 compliance report"}],
                },
                "configuration": {"returnImmediately": True},
            },
        }

        resp = await http.post(
            "https://compliance-agent.corp.internal/",
            json=submit_payload,
            headers={
                "Content-Type": "application/a2a+json",
                "A2A-Version": "1.0",
                "Authorization": "Bearer <scoped-token>",
            },
        )
        resp.raise_for_status()
        task = resp.json().get("result", {})
        task_id = task["id"]
        logger.info("a2a_task_submitted", task_id=task_id)

        # Step 2: Poll until terminal
        async def on_change(state: str, task_data: dict) -> None:
            logger.info("status_callback", state=state)

        poller = TaskPoller(
            client=http,
            base_url="https://compliance-agent.corp.internal",
            task_id=task_id,
            initial_interval=3.0,
            timeout_seconds=1800,
            on_status_change=on_change,
        )
        final_task = await poller.poll_until_terminal()

        # Step 3: Extract artifacts
        for artifact in final_task.get("artifacts", []):
            for part in artifact.get("parts", []):
                if part.get("kind") == "text":
                    logger.info("artifact_text", text=part["text"][:200])
                elif part.get("kind") == "file":
                    logger.info("artifact_file", uri=part.get("file", {}).get("uri"))
                elif part.get("kind") == "data":
                    logger.info("artifact_data", keys=list(part.get("data", {}).keys()))
```

### 5.3 Agent Card Verifier

```python
"""
Verifies Signed Agent Cards per the A2A v1.0 spec: JWS (RFC 7515)
over JSON-canonicalized (RFC 8785) card body.

Requirements:
    pip install cryptography pyjwt canonicaljson httpx structlog
"""

import json
import httpx
import jwt
import structlog
import canonicaljson
from dataclasses import dataclass
from cryptography.hazmat.primitives.asymmetric import ec, rsa, padding
from cryptography.hazmat.primitives import hashes, serialization

logger = structlog.get_logger()


@dataclass
class AgentCardVerifier:
    """Fetches and verifies a remote agent's Signed Agent Card.

    Flow:
    1. Fetch card from /.well-known/agent.json
    2. Extract agentCardSignature (JWS compact serialization)
    3. Canonicalize card body per RFC 8785
    4. Verify JWS signature against declared public key
    5. Return verified card or raise on failure
    """

    allowed_algorithms: tuple[str, ...] = ("ES256", "RS256", "EdDSA")

    def fetch_and_verify(
        self,
        agent_url: str,
        trusted_public_key_pem: str | None = None,
    ) -> dict:
        """Fetch agent card and verify its signature.

        Args:
            agent_url: Base URL of the remote agent.
            trusted_public_key_pem: If provided, verify against this key
                instead of the key declared in the card (trust-on-first-use
                is risky; prefer pre-registered keys).

        Returns:
            Verified agent card dict.

        Raises:
            ValueError: Signature missing, invalid, or algorithm disallowed.
            httpx.HTTPStatusError: Card endpoint unreachable.
        """
        log = logger.bind(agent_url=agent_url)

        resp = httpx.get(f"{agent_url}/.well-known/agent.json", timeout=10.0)
        resp.raise_for_status()
        card = resp.json()

        signature_jws = card.get("agentCardSignature")
        if not signature_jws:
            raise ValueError(
                f"Agent card from {agent_url} has no agentCardSignature. "
                f"Cannot verify identity."
            )

        # Strip signature from card body before canonicalization
        card_body = {k: v for k, v in card.items() if k != "agentCardSignature"}
        canonical_bytes = canonicaljson.encode_canonical_json(card_body)

        # Decode JWS header to check algorithm
        unverified_header = jwt.get_unverified_header(signature_jws)
        alg = unverified_header.get("alg", "")
        if alg not in self.allowed_algorithms:
            raise ValueError(
                f"Algorithm {alg} not in allowed list {self.allowed_algorithms}"
            )

        # Determine verification key
        if trusted_public_key_pem:
            public_key = serialization.load_pem_public_key(
                trusted_public_key_pem.encode()
            )
        else:
            # Fallback: extract from JWS header (TOFU -- log warning)
            jwk_data = unverified_header.get("jwk")
            if not jwk_data:
                raise ValueError("No trusted key provided and no JWK in JWS header")
            public_key = jwt.algorithms.ECAlgorithm.from_jwk(json.dumps(jwk_data))
            log.warning(
                "agent_card_tofu",
                msg="Using key from JWS header (trust-on-first-use). "
                    "Production systems should pre-register keys.",
            )

        try:
            jwt.decode(
                signature_jws,
                key=public_key,
                algorithms=list(self.allowed_algorithms),
                options={"verify_exp": False},
            )
        except jwt.InvalidSignatureError as exc:
            log.error("agent_card_signature_invalid", error=str(exc))
            raise ValueError(
                f"Agent card signature verification FAILED for {agent_url}"
            ) from exc

        log.info(
            "agent_card_verified",
            agent_name=card.get("name"),
            algorithm=alg,
            skills=[s.get("id") for s in card.get("skills", [])],
        )
        return card
```

---

## 6. Architectural System Design Scenarios

### Scenario 1: Cross-Organization Procurement Workflow

**Problem statement**: A manufacturing company's procurement system must coordinate with three external vendor agents (inventory, pricing, logistics) owned by different organizations. Each vendor exposes different capabilities, uses different AI frameworks, and has different security requirements. The system must handle multi-day RFQ (Request for Quote) cycles with human approval gates, survive vendor agent outages, and maintain full audit trails for regulatory compliance.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     BUYER ORGANIZATION (TRUST BOUNDARY)                      │
│                                                                              │
│  ┌────────────────────┐      ┌──────────────────────────────────────────┐   │
│  │ Procurement Portal │      │ Orchestrator Agent (Google ADK)          │   │
│  │ (human approvers   │◀────▶│  - Discovers vendors via Signed Cards   │   │
│  │  interact via      │      │  - Delegates RFQ subtasks via A2A       │   │
│  │  INPUT_REQUIRED    │      │  - Aggregates quotes, ranks by TCO      │   │
│  │  state)            │      │  - Pauses at INPUT_REQUIRED for         │   │
│  └────────────────────┘      │    human approval (> $50K threshold)    │   │
│                               │  - Internal tools via MCP               │   │
│                               │  - Circuit breaker per vendor           │   │
│                               └───────┬──────────┬──────────┬──────────┘   │
│                                       │          │          │              │
│  ┌───────────────────┐                │          │          │              │
│  │ API Gateway        │◀───────────────┤          │          │              │
│  │ (mTLS termination, │                │          │          │              │
│  │  rate limiting,    │                │          │          │              │
│  │  audit logging,    │                │          │          │              │
│  │  egress policy)    │                │          │          │              │
│  └───────┬────────────┘                │          │          │              │
└──────────┼─────────────────────────────┼──────────┼──────────┼──────────────┘
           │ mTLS + OAuth 2.1            │          │          │
           │ scoped per vendor per skill │          │          │
┌──────────▼──────────────┐  ┌───────────▼────┐  ┌─▼──────────▼──────────────┐
│ VENDOR A (Inventory)     │  │ VENDOR B       │  │ VENDOR C (Logistics)      │
│                          │  │ (Pricing)      │  │                           │
│ ┌──────────────────────┐ │  │ ┌────────────┐ │  │ ┌───────────────────────┐ │
│ │ Agent: CrewAI         │ │  │ │ Agent:     │ │  │ │ Agent: Bedrock        │ │
│ │ Skills:               │ │  │ │ LangGraph  │ │  │ │ AgentCore             │ │
│ │  - check_stock        │ │  │ │ Skills:    │ │  │ │ Skills:               │ │
│ │  - reserve_inventory  │ │  │ │  - quote   │ │  │ │  - estimate_shipping  │ │
│ │  - confirm_shipment   │ │  │ │  - bulk_   │ │  │ │  - track_order        │ │
│ │ Card: Signed, mTLS    │ │  │ │    discount│ │  │ │  - customs_clearance  │ │
│ │ Long-running: push    │ │  │ │ Card:      │ │  │ │ Card: Signed, OAuth   │ │
│ │ notifications         │ │  │ │ Signed,    │ │  │ │ Push notifications    │ │
│ └──────────────────────┘ │  │ │ OAuth      │ │  │ └───────────────────────┘ │
│                          │  │ └────────────┘ │  │                           │
│ Data residency: EU       │  │ US region only │  │ Multi-region              │
└──────────────────────────┘  └────────────────┘  └───────────────────────────┘
```

**Task lifecycle for a procurement request:**

```
┌───────────────────────────────────────────────────────────────────────────┐
│  Time   │ State              │ Action                                     │
├─────────┼────────────────────┼────────────────────────────────────────────┤
│  T+0    │ SUBMITTED          │ Orchestrator receives "procure 500 units  │
│         │                    │ of part X-7742"                           │
├─────────┼────────────────────┼────────────────────────────────────────────┤
│  T+1s   │ WORKING            │ Fan-out: 3 parallel A2A tasks to vendors  │
│         │                    │ (non-blocking, push notification config)  │
├─────────┼────────────────────┼────────────────────────────────────────────┤
│  T+5m   │ WORKING            │ Vendor A: stock confirmed (push notif)    │
│         │                    │ Vendor B: quote returned (push notif)     │
│         │                    │ Vendor C: circuit open (3 timeouts)       │
├─────────┼────────────────────┼────────────────────────────────────────────┤
│  T+35m  │ WORKING            │ Vendor C circuit half-open, probe works,  │
│         │                    │ logistics estimate received               │
├─────────┼────────────────────┼────────────────────────────────────────────┤
│  T+36m  │ INPUT_REQUIRED     │ Total > $50K. Orchestrator pauses for    │
│         │                    │ human approval via portal                 │
├─────────┼────────────────────┼────────────────────────────────────────────┤
│  T+4h   │ WORKING            │ Human approves. Orchestrator resumes,    │
│         │                    │ sends reserve_inventory to Vendor A       │
├─────────┼────────────────────┼────────────────────────────────────────────┤
│  T+4h5m │ COMPLETED          │ Reservation confirmed. Artifacts contain │
│         │                    │ PO number, shipping ETA, cost breakdown   │
└─────────┴────────────────────┴────────────────────────────────────────────┘
```

**Trade-off matrix:**

```
┌───────────────────────┬──────────────────┬──────────────────────────────┐
│ Dimension              │ Custom API/Queue  │ A2A Protocol                  │
├───────────────────────┼──────────────────┼──────────────────────────────┤
│ Vendor onboarding      │ Weeks per vendor  │ Hours (Agent Card + auth)     │
│                        │ (custom API per)  │ (standard protocol)           │
├───────────────────────┼──────────────────┼──────────────────────────────┤
│ Adding new vendor      │ New integration   │ Discover Agent Card, test    │
│                        │ code per vendor   │ skills, configure auth       │
├───────────────────────┼──────────────────┼──────────────────────────────┤
│ Human-in-the-loop      │ Custom webhook    │ Native INPUT_REQUIRED state  │
│                        │ + polling logic   │ + resume with same taskId    │
├───────────────────────┼──────────────────┼──────────────────────────────┤
│ Multi-day workflow     │ Stateful queue +  │ Native task state machine,   │
│                        │ DB + polling      │ push notifications, GetTask  │
├───────────────────────┼──────────────────┼──────────────────────────────┤
│ Latency per vendor hop │ Sub-100ms API     │ 2-30s (agent reasoning)      │
│                        │                   │ Acceptable for procurement.  │
├───────────────────────┼──────────────────┼──────────────────────────────┤
│ Security surface       │ Per-vendor API    │ Each vendor agent = attack   │
│                        │ contracts         │ surface. Signed Cards + mTLS │
│                        │                   │ + OAuth mitigate.            │
├───────────────────────┼──────────────────┼──────────────────────────────┤
│ Silent delegation risk │ API contracts     │ Vendor agent may return      │
│                        │ enforce schema    │ plausible but wrong results. │
│                        │                   │ Mitigation: validator agent. │
├───────────────────────┼──────────────────┼──────────────────────────────┤
│ Audit / traceability   │ Custom logging    │ W3C Trace Context across all │
│                        │ per vendor        │ hops. taskId correlation.    │
├───────────────────────┼──────────────────┼──────────────────────────────┤
│ Data residency         │ Per-vendor config │ Per-vendor. Egress policy    │
│                        │                   │ at API gateway layer.        │
└───────────────────────┴──────────────────┴──────────────────────────────┘
```

**Decision rationale**: A2A is the right choice here because (1) vendors are separate organizations with different frameworks -- the N-vendor integration problem scales linearly with A2A instead of quadratically with custom APIs; (2) procurement workflows are inherently multi-day and human-in-the-loop, which maps directly to A2A's async-first design and interrupted states; (3) the 2-30s latency per hop is acceptable for a workflow measured in hours/days; (4) Signed Agent Cards + mTLS provide cryptographic identity verification at the cross-org boundary. The main risk is silent delegation failure -- mitigated by adding a validator agent that spot-checks vendor responses against known constraints before the orchestrator accepts them.

---

### Scenario 2: Internal AI Platform with Shared Specialist Agents

**Problem statement**: An enterprise with 50+ product teams wants to offer shared AI specialist agents (code review, security scanning, documentation generation, test generation) as an internal platform. Each product team has its own orchestrator agent; specialists are maintained by the platform team. The system must enforce per-team quotas, prevent cross-team data leakage, scale horizontally, and support rapid addition of new specialist agents without redeploying any team's orchestrator.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          KUBERNETES CLUSTER                                  │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │                    SERVICE MESH (Istio / Linkerd)                       │  │
│  │            mTLS between all pods    │    NetworkPolicy isolation        │  │
│  └────────────────────────────────────┼───────────────────────────────────┘  │
│                                        │                                     │
│  ┌─────────────────────────────────────▼──────────────────────────────────┐  │
│  │                        API GATEWAY (Kong / Envoy)                       │  │
│  │  Per-team OAuth scopes  │  Rate limiting  │  Request logging           │  │
│  │  Agent Card registry    │  A2A-Version routing                         │  │
│  └───┬────────┬────────┬───┴────────┬─────────────────────────────────────┘  │
│      │        │        │            │                                        │
│  ┌───▼──┐ ┌──▼───┐ ┌──▼───┐   ┌───▼──────────────────────────────────┐    │
│  │Team A│ │Team B│ │Team C│   │ Agent Card Discovery Service          │    │
│  │Orch. │ │Orch. │ │Orch. │   │ (/.well-known/agent.json aggregator; │    │
│  │Agent │ │Agent │ │Agent │   │  serves public + extended cards;      │    │
│  │      │ │      │ │      │   │  filters by team's OAuth scope)       │    │
│  │ MCP  │ │ MCP  │ │ MCP  │   └──────────────────────────────────────┘    │
│  │tools │ │tools │ │tools │                                               │
│  └──┬───┘ └──┬───┘ └──┬───┘                                               │
│     │        │        │        A2A (JSON-RPC / HTTPS)                      │
│     │        │        │                                                     │
│  ┌──▼────────▼────────▼────────────────────────────────────────────────┐   │
│  │              SHARED SPECIALIST AGENT POOL                            │   │
│  │                                                                      │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────────┐  │   │
│  │  │ Code Review │  │ Security   │  │ Doc Gen    │  │ Test Gen     │  │   │
│  │  │ Agent       │  │ Scanner    │  │ Agent      │  │ Agent        │  │   │
│  │  │ (HPA:3-20) │  │ Agent      │  │ (HPA:2-10) │  │ (HPA:2-8)   │  │   │
│  │  │             │  │ (HPA:2-15) │  │            │  │              │  │   │
│  │  │ Skills:     │  │ Skills:    │  │ Skills:    │  │ Skills:      │  │   │
│  │  │ -review_pr  │  │ -scan_deps │  │ -gen_api   │  │ -gen_unit    │  │   │
│  │  │ -suggest_   │  │ -audit_    │  │  _docs     │  │  _tests      │  │   │
│  │  │  refactor   │  │  iam_roles │  │ -gen_      │  │ -gen_integ   │  │   │
│  │  │             │  │ -pen_test  │  │  runbook   │  │  _tests      │  │   │
│  │  └─────────────┘  └───────────┘  └───────────┘  └──────────────┘  │   │
│  │                                                                      │   │
│  │  Each specialist: own Agent Card, own HPA, own resource quota.      │   │
│  │  Platform team deploys new specialists without touching team code.   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    OBSERVABILITY STACK                                │   │
│  │  OpenTelemetry Collector  │  Grafana dashboards  │  per-team cost   │   │
│  │  W3C Trace Context       │  per-agent latency    │  attribution     │   │
│  │  across all hops         │  P50/P95/P99          │  via OAuth scope │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Data isolation model:**

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Layer             │ Isolation Mechanism                                    │
├───────────────────┼──────────────────────────────────────────────────────┤
│ Network            │ Kubernetes NetworkPolicy: team namespaces can only   │
│                    │ reach specialist pods via service mesh, not each     │
│                    │ other's orchestrators                                │
├───────────────────┼──────────────────────────────────────────────────────┤
│ Identity           │ OIDC-based Agent Card auth. Each team has distinct  │
│                    │ OAuth client credentials. Specialist validates      │
│                    │ team identity on every request.                     │
├───────────────────┼──────────────────────────────────────────────────────┤
│ Task visibility    │ ListTasks returns ONLY tasks created by the         │
│                    │ authenticated team (A2A spec requirement).           │
│                    │ Specialist stores tasks partitioned by team scope.  │
├───────────────────┼──────────────────────────────────────────────────────┤
│ Quota              │ API gateway enforces per-team rate limits.          │
│                    │ Cost attributed via OAuth scope in telemetry.       │
├───────────────────┼──────────────────────────────────────────────────────┤
│ Agent Card access  │ Extended Agent Cards show team-specific skills.     │
│                    │ pen_test skill visible only to security team.       │
└───────────────────┴──────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

```
┌───────────────────────┬──────────────────┬──────────────────────────────┐
│ Dimension              │ Embedded per-team │ Shared A2A specialist pool    │
│                        │ agents            │                               │
├───────────────────────┼──────────────────┼──────────────────────────────┤
│ Resource efficiency    │ 50 copies of each │ Single pool with HPA. ~5-10x │
│                        │ specialist, most  │ better utilization.           │
│                        │ idle              │                               │
├───────────────────────┼──────────────────┼──────────────────────────────┤
│ Adding new specialist  │ Redeploy all 50   │ Deploy once, update Agent    │
│                        │ team stacks       │ Card registry. Zero team     │
│                        │                   │ impact.                      │
├───────────────────────┼──────────────────┼──────────────────────────────┤
│ Upgrading specialist   │ Coordinate 50     │ Canary deploy behind service │
│ model/logic            │ team upgrades     │ mesh. A2A version negotiation│
│                        │                   │ handles compatibility.       │
├───────────────────────┼──────────────────┼──────────────────────────────┤
│ Cross-team data leak   │ No risk (isolated) │ Requires task partitioning,  │
│                        │                   │ ListTasks scoping, network   │
│                        │                   │ policy. More attack surface. │
├───────────────────────┼──────────────────┼──────────────────────────────┤
│ Latency                │ In-process, fast   │ Network hop + agent reason.  │
│                        │                   │ 2-30s per specialist call.   │
├───────────────────────┼──────────────────┼──────────────────────────────┤
│ Debugging              │ Single-process    │ Distributed tracing required.│
│                        │ logs              │ Trace Context across mesh.   │
├───────────────────────┼──────────────────┼──────────────────────────────┤
│ Team autonomy          │ Full control      │ Teams depend on platform for │
│                        │                   │ specialist availability.     │
│                        │                   │ SLA contract needed.         │
├───────────────────────┼──────────────────┼──────────────────────────────┤
│ Operational complexity │ Simple per-team   │ Platform team operates mesh, │
│                        │                   │ gateway, HPA, tracing.       │
│                        │                   │ Higher ops bar.              │
└───────────────────────┴──────────────────┴──────────────────────────────┘
```

**Decision rationale**: A2A enables the platform team to offer specialist agents as internal services that any team's orchestrator can discover and consume via Agent Cards, without per-team integration code. The key value is operational leverage: deploying or upgrading a specialist once serves all 50+ teams. The main risks are (1) cross-team data leakage through shared task stores -- mitigated by strict ListTasks scoping and network isolation; (2) platform availability becoming a single point of failure -- mitigated by HPA, circuit breakers in each team's orchestrator, and graceful degradation (teams can fall back to local, simpler tools via MCP if the specialist pool is down); (3) the latency cost of a network hop per specialist call -- acceptable because code review and security scanning are not latency-sensitive. The approach would NOT be justified if the org had fewer than ~10 teams (the operational complexity outweighs the consolidation benefit) or if the specialists needed sub-second response times.
