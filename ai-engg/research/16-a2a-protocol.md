# Research: How A2A (Agent-to-Agent) Protocol Works
**Date researched**: 2026-09-29
**Sources consulted**: 14

---

## 1. System Topology & Mechanics

### What A2A Is

A2A is an open protocol (Apache 2.0) for AI agent interoperability -- enabling agents built on different frameworks, by different vendors, to discover each other, delegate tasks, exchange messages, and coordinate work. Launched by Google in April 2025, donated to the Linux Foundation in June 2025, and reaching v1.0 in March 2026. As of April 2026: 150+ supporting organizations, 22,000+ GitHub stars, SDKs in 5 languages (Python, JS, Java, Go, .NET), and production deployments at Microsoft (Azure AI Foundry, Copilot Studio), AWS (Bedrock AgentCore), Salesforce, SAP, and ServiceNow. IBM's competing ACP merged into A2A in August 2025 -- there is effectively no alternative standard as of mid-2026.

The analogy: TCP/IP for machines, HTTP for documents, APIs for services, MCP for agent-tool connections, **A2A for agent-to-agent coordination**.

### Three-Layer Architecture

The v1.0 spec is organized into three layers:

| Layer | Purpose | Details |
|-------|---------|---------|
| **L1: Canonical Data Model** | Core data structures | Defined in Protocol Buffers (`a2a.proto` is the single normative source). All SDK bindings regenerated from proto. |
| **L2: Abstract Operations** | Fundamental capabilities | 11 operations: SendMessage, SendStreamingMessage, GetTask, ListTasks, CancelTask, SubscribeToTask, 4x push notification CRUD, GetExtendedAgentCard |
| **L3: Protocol Bindings** | Wire protocols | JSON-RPC 2.0 over HTTPS (primary), gRPC with protobuf, HTTP/JSON/REST. Choice is a deployment decision, not a design one. |

### Five Design Principles (v1.0)

1. **Simple** -- Reuses HTTP, JSON-RPC 2.0, SSE. No new transport layer.
2. **Enterprise Ready** -- Built-in auth, authorization, security, privacy, tracing, monitoring.
3. **Async First** -- Built for long-running tasks and human-in-the-loop. From quick queries to deep research taking hours/days.
4. **Modality Agnostic** -- Text, audio, video, structured data, forms, embedded UI components.
5. **Opaque Execution** -- Agents collaborate by sharing skills and outputs without revealing internal reasoning, plans, or tool implementations.

### Core Components

**Agent Cards** -- JSON metadata at `/.well-known/agent.json` (IANA-registered per RFC 8615). Contains:
- Name, description, version, provider info
- Service endpoint URL
- Supported input/output modalities (MIME types)
- Skills (with IDs, descriptions, tags, examples)
- Authentication requirements (aligned with OpenAPI security schemes)
- Capability flags: streaming, pushNotifications, extendedAgentCard
- AgentInterface with `tenant` field for multi-tenancy
- AgentCardSignature for cryptographic verification (JWS per RFC 7515, after JSON-canonicalization per RFC 8785)
- Extension declarations (URIs)

**Extended Agent Cards** -- Served behind authentication. Allows hiding sensitive skills or internal capabilities from unauthenticated discovery. Clients replace their cached public card with the extended version for the authenticated session.

**Tasks** -- Fundamental unit of work. Server-generated ID (clients cannot set). Stateful, can span multiple message exchanges, can pause for input or authorization.

**Messages** -- Units of communication between agents. Each has a `role` (USER or AGENT) and contains one or more Parts. Messages are for task initiation, clarification, status updates, and ongoing interaction. Messages MUST NOT be used to deliver task outputs (use Artifacts). Messages are NOT guaranteed to be persisted in task history.

**Parts** -- Smallest content unit within Messages or Artifacts:
- **Text parts**: Plain text
- **File parts**: Files/images via URI or inline bytes
- **Data parts**: Structured JSON

**Artifacts** -- Task outputs (documents, images, structured data). Composed of Parts. Can be streamed as they are created. Artifacts ARE the reliable delivery mechanism for task results.

**Contexts** -- Group related tasks via `contextId` for continuity across interactions (like a conversation thread). Server-generated; clients treat as opaque.

### Client-Server Model

Every interaction has two roles:
- **Client agent**: Discovers remote agents, sends tasks, coordinates workflow
- **Remote agent**: Receives tasks, processes them, returns results

Any agent can play both roles. A coding agent is a client when delegating to QA, but a remote agent when a PM agent requests code.

### Communication Patterns

**1. Synchronous request-response** (`returnImmediately: false`, the default):
- Client sends message, blocks until terminal or interrupted state
- Best for quick tasks

**2. Streaming via SSE** (`message/stream` or `tasks/subscribe`):
- Server streams incremental updates in real-time
- `StreamResponse` contains exactly one of: task, message, statusUpdate, artifactUpdate
- Events MUST be delivered in generation order, MUST NOT be reordered
- Events broadcast to all active streams for a task
- Stream MUST close when task reaches terminal state
- Closing one stream MUST NOT affect other active streams

**3. Push notifications** (webhooks):
- Server sends async HTTP POST to client-provided webhook URL
- Best for long-running tasks where client shouldn't hold connection
- Payloads are StreamResponse objects as JSON, regardless of protocol binding
- Config persists until task completion or explicit deletion
- Webhook endpoints MUST require authentication from sending server

**4. Non-blocking poll** (`returnImmediately: true`):
- Returns immediately; caller polls via GetTask, subscribes, or uses push

### Orchestration Topologies

**Centralized Orchestrator**: One lead agent owns the plan, discovers specialists, delegates tasks, stitches results. Easier to reason about and debug. Default for most enterprise workflows.

**Decentralized Swarm**: No single orchestrator. Agents find each other directly, share tasks. More flexible and resilient, harder to trace. Most real systems start centralized, evolve to swarm when trust systems mature.

### Relationship with MCP (Complementary, Not Competing)

| Dimension | MCP | A2A |
|-----------|-----|-----|
| **What it connects** | Agent to tools/data (vertical) | Agent to agent (horizontal/peer) |
| **Analogy** | USB port for peripherals | HTTP for web services |
| **Launched by** | Anthropic (Nov 2024) | Google (Apr 2025) |
| **Governance** | Linux Foundation (AAIF) | Linux Foundation (AAIF) |
| **2026 adoption** | 97M monthly SDK downloads, 17K+ servers | 150+ orgs, 22K GitHub stars |
| **When to use** | Agent needs tools, data access, context | Multiple agents coordinating across boundaries |

The reference architecture: **MCP inside agents, A2A between agents**. Google ADK, Salesforce Agentforce, and ServiceNow Now Assist all implement both. The AAIF (Agentic AI Foundation, Dec 2025) -- co-founded by OpenAI, Anthropic, Google, Microsoft, AWS, Block -- governs both protocols under neutral governance.

Rule of thumb: Start with one agent + MCP tools. Add A2A only when you have genuine reasons for agent autonomy and specialization across organizational boundaries. Multi-agent systems are harder to debug, more expensive to run, and slower to respond.

---

## 2. Token Economics & NFR Metrics

### Protocol Overhead Costs

A2A introduces **significantly more latency than direct API calls** because the remote agent must:
1. Parse natural language instructions
2. Formulate a plan
3. Execute reasoning and tool calls
4. Formulate a structured response

This is not protocol overhead (the wire format is lightweight HTTP/JSON-RPC) -- it is **agent reasoning overhead**. The protocol itself adds negligible bytes compared to the LLM inference cost on both sides.

**No published p50/p99 latency benchmarks exist as of Sept 2026.** This is a notable gap -- all assessments are qualitative. [Inferred: typical single-hop A2A latency is dominated by the remote agent's LLM inference time, likely 2-30s depending on model and task complexity, vs. sub-100ms for a direct API call.]

### Throughput Characteristics

- JSON-RPC binding: standard HTTP request/response throughput, limited by agent processing speed not protocol
- gRPC binding: added for high-performance scenarios with Protocol Buffers serialization (lower serialization overhead vs JSON)
- Streaming via SSE: enables incremental delivery, reducing perceived latency for long tasks
- Push notifications: decouple client from server processing entirely

### Cost Implications

Each A2A hop that involves an LLM agent incurs:
- LLM inference costs on the remote agent side (input + output tokens)
- Potential tool-call costs within the remote agent (via MCP)
- Network/infrastructure costs (negligible relative to inference)

**Design implication**: If you chain 3+ agents synchronously, user-facing latency becomes unacceptable. Design A2A communications asynchronously using push notifications and long-running task management.

### Wire Format Overhead

- IANA-registered media type: `application/a2a+json`
- Custom HTTP headers: `A2A-Version`, `A2A-Extensions` (prefixed with `a2a-`)
- Protocol version negotiation adds one header per request
- Agent Card caching (via standard HTTP caching directives) amortizes discovery cost

---

## 3. Distributed Resilience & State

### Task Lifecycle State Machine

9 states with strict transition constraints:

```
                    +---> COMPLETED (terminal)
                    |
SUBMITTED --> WORKING --+---> FAILED (terminal)
    |           ^  |    |
    |           |  |    +---> CANCELED (terminal)
    |           |  |
    |           |  +---> INPUT_REQUIRED (interrupted) --+
    |           |  |                                     |
    |           |  +---> AUTH_REQUIRED (interrupted)  ---+
    |           |                                        |
    |           +--- (client sends new message) --------+
    |
    +---> REJECTED (terminal, can occur at submission or later)

UNSPECIFIED -- unknown/recovery state
```

**Transition Rules**:
- Terminal states (COMPLETED, FAILED, CANCELED, REJECTED) cannot accept further messages -- returns `UnsupportedOperationError`
- Interrupted states (INPUT_REQUIRED, AUTH_REQUIRED) resume via new message with same `taskId` and `contextId`
- REJECTED can fire during initial creation or after the agent determines it cannot proceed
- Streams MUST close at terminal state
- SubscribeToTask on terminal-state task returns `UnsupportedOperationError`
- Cancel on terminal task returns `TaskNotCancelableError`

### Long-Running Task Handling

A2A is async-first by design:
- `returnImmediately: true` for fire-and-forget submission
- Push notifications for webhook-based async updates (persists until task completion or explicit deletion)
- SSE streaming for real-time progress without polling
- SubscribeToTask for late-joining observers (MUST return current state as first event)
- Task state persists server-side; clients can reconnect and poll via GetTask

### Context Management & Multi-Turn

- **contextId**: Groups related tasks. Server-generated, opaque to clients. If agent cannot accept client-provided contextId, MUST reject with error.
- **taskId**: Server-generated only. Client-provided taskId for creation is NOT supported. Invalid taskId returns TaskNotFoundError.
- **Multi-turn**: Client uses taskId to continue a specific task, or contextId without taskId for new task in existing context.
- **Cross-task references**: `referenceTaskIds` field enables explicit dependencies between tasks.
- **Consistency**: Agent MUST reject messages with mismatching contextId and taskId.

### Error Propagation

**A2A-specific error types**:

| Error | When |
|-------|------|
| `TaskNotFoundError` | Task ID doesn't exist or isn't accessible |
| `TaskNotCancelableError` | Task already terminal |
| `PushNotificationNotSupportedError` | Agent lacks push capability |
| `UnsupportedOperationError` | Operation not supported |
| `ContentTypeNotSupportedError` | Unsupported media type in parts |
| `InvalidAgentResponseError` | Response violates spec |
| `ExtendedAgentCardNotConfiguredError` | Declared but not configured |
| `ExtensionSupportRequiredError` | Required extension missing |
| `VersionNotSupportedError` | Requested A2A-Version unsupported |

**Transport-level errors** map to standard codes:
- Auth: HTTP 401 / gRPC UNAUTHENTICATED
- Authz: HTTP 403 / gRPC PERMISSION_DENIED
- Validation: HTTP 400 / gRPC INVALID_ARGUMENT / JSON-RPC -32602
- Not found: HTTP 404 / gRPC NOT_FOUND
- System: HTTP 500-503 / gRPC INTERNAL-UNAVAILABLE / JSON-RPC -32603

All errors MUST include error code + human-readable message. Optional error details array uses ProtoJSON `Any` representation (google.rpc error model recommended).

**Retry logic and circuit-breaking are deliberately left to client implementation** -- the protocol focuses on state communication, not resilience policy.

### Idempotency Guarantees

| Operation | Idempotency |
|-----------|-------------|
| GetTask, ListTasks | Naturally idempotent |
| SendMessage | MAY be idempotent (agents may dedup on messageId) |
| CancelTask | Idempotent |
| DeletePushNotificationConfig | Idempotent |

### Pagination (ListTasks)

Cursor-based: `pageToken`/`nextPageToken`. Default page size 50, min 1, max 100. Sorted by status timestamp descending. `nextPageToken` MUST always be present (empty string = no more pages).

---

## 4. Enterprise Security & Governance

### Authentication Between Agents

Authentication is handled at the **HTTP transport layer, not within A2A payloads**. Identity flows through HTTP headers via standard mechanisms declared in Agent Cards.

**Supported security schemes** (exactly one per SecurityScheme object):

| Scheme | Use Case |
|--------|----------|
| `APIKeySecurityScheme` | API key in header/query |
| `HTTPAuthSecurityScheme` | HTTP Bearer tokens |
| `OAuth2SecurityScheme` | OAuth 2.0 (authorization code, client credentials, device code flows) |
| `OpenIdConnectSecurityScheme` | OIDC discovery |
| `MutualTlsSecurityScheme` | mTLS with X.509 certificates |

**Production recommendation**: mTLS between agents inside the same trust boundary; OAuth 2.1 with scoped tokens across boundaries. Credentials MUST NOT share a root key -- scope per caller per skill.

**In-task authorization**: Agents can request credentials mid-task via `TASK_STATE_AUTH_REQUIRED`, enabling just-in-time authorization for sensitive operations.

### Signed Agent Cards

Cryptographic identity verification at discovery time:
- JWS signature per RFC 7515
- JSON canonicalization per RFC 8785 before signing
- Verification process defined in spec section 8.4.3
- Prevents malicious agents from misrepresenting capabilities

### Authorization & Capability Discovery

- Agent Cards declare skills, each with scoped permissions
- OAuth scopes grant access to specific skills, not blanket agent access
- Servers MUST NOT reveal existence of resources the client isn't authorized to access
- Servers SHOULD NOT distinguish between "does not exist" and "not authorized" (prevents information leakage)
- ListTasks MUST return only tasks visible to the authenticated client
- Authorization creep is a known anti-pattern: don't pass broad bearer tokens from orchestrator to sub-agents

### Transport Security

- TLS implied for all communication channels
- Production: HTTPS with TLS 1.3+ and strong cipher suites
- Post-quantum cryptography (PQC) cipher suites recommended as they become available
- All webhook communications MUST use HTTPS

### Replay Protection

The spec does not mandate replay protection by default. Recommended controls (ideally combined):
1. UUIDv7 request ID (sorts by time) as nonce per request
2. Date header validated within 5-minute clock skew window
3. Message Authentication Codes (MAC)

For high-value operations: sender-constrained tokens via mTLS-bound tokens (RFC 8705) or DPoP (RFC 9449).

### Cross-Agent Prompt Injection

A2A provides **no protocol-level controls** against prompt injection. Mitigation relies entirely on defense-in-depth: TLS, trusted agents, authentication, authorization, least privilege, and input validation within each agent.

### Audit Trails

- OpenTelemetry with W3C Trace Context headers (`traceparent`/`tracestate`) on every A2A call enables end-to-end distributed tracing
- Single trace ID across all agent hops eliminates a huge class of debugging pain
- For external exposure: API Management integration recommended for centralized policy enforcement (auth, rate limiting, quotas, logging)
- taskId and contextId provide stable identifiers for audit correlation

### Governance Structure

- Linux Foundation governs the spec (Apache 2.0 license)
- Technical Steering Committee: AWS, Cisco, Google, IBM Research, Microsoft, Salesforce, SAP, ServiceNow
- AAIF (Agentic AI Foundation, Dec 2025): 190 member orgs as of May 2026 -- co-founded by OpenAI, Anthropic, Google, Microsoft, AWS, Block
- No single company controls either MCP or A2A

---

## 5. Production Failure Modes

### Discovery Failures

- Agent Card endpoint unreachable (standard HTTP failure -- easiest to handle)
- Agent Card served from non-default URI (not a security measure; may cause discovery misses)
- Stale cached Agent Card (spec requires caching directives from server, clients must respect)
- Malicious Agent Cards misrepresenting capabilities (mitigated by Signed Agent Cards in v1.0)
- Extended Agent Card not configured despite capability flag (returns `ExtendedAgentCardNotConfiguredError`)

### Message Format Incompatibilities

- **Schema gap**: Spec doesn't standardize per-skill JSON Schema for Part body content. A client knows a skill accepts `application/json` but not the exact object structure. Mitigations: embed OpenAPI fragment or JSON Schema in skill description, or use Extensions.
- `ContentTypeNotSupportedError`: Remote agent rejects unsupported MIME types
- `VersionNotSupportedError`: Protocol version mismatch (empty version header interpreted as v0.3)
- `InvalidAgentResponseError`: Response doesn't conform to spec
- Vendor behavioral differences: two agents both implementing A2A may interpret edge cases differently

### Timeout and Coordination Failures

- **Silent delegation failure**: The dominant failure mode in 2026. Orchestrator sends task to specialist, receives plausible "completed" event, workflow continues -- but the specialist used wrong data, hit stale state, or returned incorrect results. No protocol-level mechanism catches semantic errors.
- **Hard transport failures**: 503, connection timeout -- standard HTTP failure, caught by existing circuit breakers
- **Cross-boundary failure smearing**: When orchestrator delegates to sub-agent and sub-agent fails silently, who carries the error budget? How do you instrument the boundary? These questions have no consensus answers yet.
- **Cascading latency**: p99 grows when a called agent throttles, but dashboards cannot localize which hop caused the spike
- **Streaming failures**: Connection drops mid-stream, event ordering violations, multiple concurrent streams to same task

### Observability Gaps

A2A solves interoperability but NOT observability. Key gaps:
- "delegated to research_agent" in logs is NOT observability -- need structured traces surviving handoffs
- No built-in distributed tracing (must layer OpenTelemetry manually)
- SREs report inability to localize latency spikes across agent hops
- No standardized metrics format for agent-to-agent interactions
- The reliability engineering discipline for A2A is not mature -- the protocol is production-grade, the operational practice is not

---

## 6. Enterprise System Design Scenarios

### Multi-Vendor Agent Interoperability

**Primary use case**: Agents owned by different organizations interoperate -- a supplier's agent serving a buyer's agent, or Salesforce agents coordinating with ServiceNow agents. This is where A2A's vendor-neutrality pays off most.

**Deployment pattern**: Treat cross-org A2A boundaries like any external integration: contracts, rate limits, egress policy, zero implicit trust, mTLS, signed Agent Cards.

### Enterprise A2A Deployment Patterns

**Pattern 1: Orchestrator-Worker** (most common)
- Central orchestrator discovers specialists via Agent Cards, delegates via A2A
- Works across frameworks (LangGraph orchestrator delegating to CrewAI specialist)
- Clear responsibility chain for debugging

**Pattern 2: Cross-Organization Federated**
- Agents across org boundaries, strongest security requirements
- Signed Agent Cards + mTLS + OAuth scoped tokens
- Data residency and egress controls critical

**Pattern 3: Hierarchical Delegation**
- Orchestrators delegate to sub-orchestrators forming a tree
- Every layer adds latency and cost
- Rarely more than 2 levels justified in practice

**Pattern 4: Human-in-the-Loop**
- A2A tasks pause at defined points for human approval
- Not optional for high-risk EU AI Act use cases
- Model human approver as a step in the A2A task flow

**Pattern 5: Kubernetes Agent Mesh**
- Secure with NetworkPolicy, service mesh (Istio/Linkerd), OIDC-based Agent Card auth
- Compatible with existing load balancers, API gateways, observability tools

### Framework Compatibility (2026)

LangGraph, CrewAI, Vertex AI Agent Builder, AutoGen, Google ADK, Microsoft Agent Framework, Amazon Bedrock AgentCore, Salesforce Agentforce, ServiceNow Now Assist all support A2A -- existing agents can be A2A-enabled with minimal code changes.

### Agent Payments Protocol (AP2)

Extension of A2A into economic coordination: secure agent-driven transactions. 60+ organizations behind it across payments and financial services. [Inferred: enables agents to negotiate and execute payments as part of task completion, critical for agent marketplaces.]

### Trade-Off Matrix

| Dimension | Direct API Call | A2A Protocol |
|-----------|----------------|--------------|
| **Latency** | Sub-100ms | 2-30s per hop (agent reasoning) |
| **Complexity** | Minimal | Discovery, lifecycle, auth overhead |
| **Interoperability** | Point-to-point, N^2 integrations | Vendor-neutral, linear scaling |
| **Debugging** | Standard HTTP tracing | Requires distributed tracing across agent boundaries |
| **Security surface** | Single endpoint | Each agent is an attack surface |
| **Flexibility** | Rigid API contracts | Skill-based dynamic delegation |
| **Human-in-the-loop** | Manual implementation | Native via INPUT_REQUIRED state |
| **Long-running tasks** | Polling/callbacks | Native async with state machine |

| Dimension | MCP Only | MCP + A2A |
|-----------|----------|-----------|
| **When** | Single agent + tools | Multiple agents crossing ownership boundaries |
| **Complexity** | Lower | Higher (adds protocol, discovery, state) |
| **Cost** | One agent's inference | Multiple agents' inference per workflow |
| **Failure modes** | Tool failures (well-understood) | + silent delegation, cross-boundary smearing |
| **Recommended for** | Most projects in 2026 | Cross-vendor/cross-org multi-agent systems |

### When NOT to Use A2A

- Single agent systems (no peer communication needed)
- Single codebase (all components share deployment)
- Short synchronous workflows (no long-running task lifecycle)
- No discovery requirements (agents are hardcoded)
- No independent task state (stateless function calls)
- No external agent providers (all internal to one team)
- When an API or queue would be simpler
- When the team can't operate the complexity

**Start with MCP. Design clean agent boundaries from the beginning. Add A2A only when those boundaries become real.**

### The 90-Day Retention Test

The real measure of A2A adoption is not "supported by" (logo adoption) but **production retention after the first operational incident**. "Developer hype is cheap, production retention is expensive." The worst sign would be if developers only use it in demos while production systems fall back to custom APIs and queues.

---

## Sources

1. [System Design Newsletter - A2A Protocol Deep Dive](https://newsletter.systemdesign.one/p/agent-to-agent-protocol)
2. [Google Developers Blog - Announcing A2A](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/)
3. [A2A Protocol Official Specification](https://a2a-protocol.org/latest/specification/)
4. [A2A Protocol v1.0 Release Blog](https://a2a-protocol.org/latest/blog/2026/03/12/a2a-protocol-ships-v10-production-ready-standard-for-agent-to-agent-communication/)
5. [GitHub - a2aproject/A2A](https://github.com/a2aproject/A2A)
6. [Linux Foundation - A2A Surpasses 150 Organizations](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year)
7. [Red Hat Developer - How to Enhance A2A Security](https://developers.redhat.com/articles/2025/08/19/how-enhance-agent2agent-security)
8. [Glukhov - A2A Protocol 2026: Adoption, Hype, and Reality](https://www.glukhov.org/ai-systems/comparisons/a2a-protocol-2026-adoption/)
9. [Zylos Research - Agent Interoperability Protocols 2026](https://zylos.ai/research/2026-03-26-agent-interoperability-protocols-mcp-a2a-acp-convergence/)
10. [DEV Community - A2A + MCP: The SRE Reliability Framework](https://dev.to/ajaydevineni/a2a-mcp-in-production-the-sre-reliability-framework-nobody-has-written-yet-2hf2)
11. [Tyk - A2A Protocol Architecture and Technical Specification](https://tyk.io/learning-center/a2a-protocol-architecture-and-technical-specification/)
12. [SecureW2 - A2A Protocol Security](https://securew2.com/blog/a2a-protocol-security)
13. [Atlan - MCP vs A2A Protocol](https://atlan.com/know/mcp/mcp-vs-a2a-protocol/)
14. [Conceptualise - A2A Protocol Patterns for Multi-Agent Systems](https://www.conceptualise.de/en/blog/agent-to-agent-a2a-protocol-patterns)
