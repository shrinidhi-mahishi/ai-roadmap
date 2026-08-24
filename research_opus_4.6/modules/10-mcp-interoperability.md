# Module 10: MCP & Interoperability — Model Context Protocol, A2A, AG-UI, Security, and Production Deployment

**Scope**: MCP architecture (JSON-RPC 2.0, transports, primitives, lifecycle), MCP security (OAuth 2.1, OWASP MCP Top 10, tool poisoning, rug pulls, supply chain attacks), A2A protocol (Agent Cards, task lifecycle, governance), AG-UI protocol, ecosystem adoption, production deployment patterns, and the AAIF governance model.
**Prerequisite**: Module 04 (Agent Architecture), Module 09 (Multi-Agent Systems).
**Last updated**: 2026-08-21 | **Sources consulted**: 75

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                    CONTROL PLANE                                           │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
 │  │  MCP Gateway      │  │  Auth Server     │  │  Registry &      │  │  Policy Engine   │  │
 │  │  (Triple Gate)    │  │  (OAuth 2.1)     │  │  Discovery       │  │  - Per-tool      │  │
 │  │  - Gate 1: Client │  │  - PKCE (S256)   │  │  - Agent Cards   │  │    authorization │  │
 │  │    → LLM (inject  │  │  - Resource      │  │    at /.well-    │  │  - Mcp-Method +  │  │
 │  │    filter, PII)   │  │    Indicators    │  │    known/        │  │    Mcp-Name hdr  │  │
 │  │  - Gate 2: LLM →  │  │  - Confused      │  │  - Tool schemas  │  │  - Scope-based   │  │
 │  │    MCP (tool auth) │  │    deputy block  │  │  - Server health │  │    access ctrl   │  │
 │  │  - Gate 3: MCP →  │  │  - DCR → Client  │  │  - Version check │  │  - Rate limiting │  │
 │  │    downstream     │  │    ID Metadata   │  │                  │  │                  │  │
 │  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
 └───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┘
             │                    │                    │                    │
 ┌───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┐
 │           ▼                    ▼                    ▼                    ▼                  │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                       DATA PLANE: PROTOCOL EXECUTION ENGINE                        │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  MCP LAYER (Agent ↔ Tool)                                                │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Host         │  │ Client       │  │ Server       │  │ Transport  │  │      │    │
 │  │  │  │ - AI app     │  │ - 1:1 conn   │  │ - Exposes    │  │ - stdio    │  │      │    │
 │  │  │  │ - Manages N  │  │   to server  │  │   tools      │  │   (local)  │  │      │    │
 │  │  │  │   MCP clients│  │ - Capability │  │ - Resources  │  │ - Stream-  │  │      │    │
 │  │  │  │ - Mediates   │  │   negotiation│  │ - Prompts    │  │   able HTTP│  │      │    │
 │  │  │  │   user/model │  │ - JSON-RPC   │  │ - Sampling   │  │   (remote) │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  A2A LAYER (Agent ↔ Agent)                                               │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Client Agent │  │ Remote Agent │  │ Task Manager │  │ Agent Card │  │      │    │
 │  │  │  │ - Identifies │  │ - Receives   │  │ - 9 states   │  │ Registry   │  │      │    │
 │  │  │  │   delegation │  │   tasks      │  │ - Running/   │  │ - Discovery│  │      │    │
 │  │  │  │ - Sends via  │  │ - Returns    │  │   Paused/    │  │ - Signed   │  │      │    │
 │  │  │  │   JSON-RPC   │  │   artifacts  │  │   Finished   │  │   cards    │  │      │    │
 │  │  │  │ - Subscribes │  │ - Streaming  │  │ - Context    │  │ - Skill    │  │      │    │
 │  │  │  │   to updates │  │   via SSE    │  │   propagation│  │   matching │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  AG-UI LAYER (Agent ↔ User)                                              │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Agent Runtime│  │ SSE Stream   │  │ Frontend     │  │ State Sync │  │      │    │
 │  │  │  │ - Processes  │  │ - 17 event   │  │ - React hook │  │ - Snapshot │  │      │    │
 │  │  │  │   user input │  │   types      │  │   (useAgent) │  │ - Delta    │  │      │    │
 │  │  │  │ - Runs tools │  │ - Lifecycle  │  │ - Renders    │  │   (JSON    │  │      │    │
 │  │  │  │ - Generates  │  │ - Text/Tool  │  │   streaming  │  │   Patch)   │  │      │    │
 │  │  │  │   response   │  │ - State mgmt │  │   output     │  │ - Bi-dir   │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                         TOOL PROXY LAYER                                           │    │
 │  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐       │    │
 │  │  │ Tool Sandbox   │  │ Input Sanitiz.│  │ Tool Poison   │  │ Supply Chain  │       │    │
 │  │  │ - Docker/OCI   │  │ - Command inj │  │ Detector      │  │ Verifier      │       │    │
 │  │  │ - MicroVM      │  │ - Schema val  │  │ - Description │  │ - Package     │       │    │
 │  │  │ - gVisor       │  │ - PII filter  │  │   scan        │  │   integrity   │       │    │
 │  │  │ - Network      │  │ - Output      │  │ - Rug pull    │  │ - Version     │       │    │
 │  │  │   isolation    │  │   sanitize    │  │   monitor     │  │   pinning     │       │    │
 │  │  └───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘       │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  PERSISTENCE LAYER                                         │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Tool Schema Store │  │ Session / State   │  │ A2A Task Store   │  │ WORM Audit Log │  │
 │  │ - Tool defs       │  │ Store             │  │ - Task lifecycle │  │ - All tool      │  │
 │  │ - Version history │  │ - Checkpoints     │  │ - Artifacts      │  │   invocations  │  │
 │  │ - Description     │  │ - External state  │  │ - Messages       │  │ - Auth events  │  │
 │  │   hashes (rug     │  │   (Postgres/Redis)│  │ - Idempotency    │  │ - Immutable    │  │
 │  │   pull detection) │  │ - Stateless in RC │  │   keys           │  │ - W3C Trace    │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  TELEMETRY PLANE                                           │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Per-Tool Metrics  │  │ Protocol Health   │  │ Security Alerts  │  │ Cost & Usage   │  │
 │  │ - p50/p90/p99     │  │ - Transport errs  │  │ - Tool poison    │  │ - Tokens per   │  │
 │  │   per tool call   │  │ - Handshake fail  │  │   detection      │  │   tool call    │  │
 │  │ - Success/fail    │  │ - A2A task state  │  │ - Supply chain   │  │ - Schema       │  │
 │  │   rate            │  │   transitions     │  │   anomalies      │  │   overhead     │  │
 │  │ - Cache hit ratio │  │ - Endpoint health │  │ - Auth failures  │  │   (22K+ tokens │  │
 │  │ - W3C traceCtx   │  │   (9% healthy)    │  │ - Scope violate  │  │   for 100 tools│  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

**Step 1 — Discovery & Capability Negotiation**: The AI Host application connects to one or more MCP servers. Each Client performs the initialization handshake: sends `initialize` with supported protocol version and capabilities, receives server's capabilities (tools, resources, prompts), sends `initialized` notification. For A2A, the client fetches the remote agent's Agent Card from `/.well-known/agent-card.json` to discover skills and authentication requirements.

**Step 2 — Tool Schema Loading**: The Client calls `tools/list` to retrieve available tools with their JSON Schema definitions. With 5 servers and 100 tools, this injects 22,000+ tokens of schema overhead into the model's context window before any user prompt is processed.

**Step 3 — Authorization**: For remote MCP servers, OAuth 2.1 with PKCE (S256) authenticates the client. Resource Indicators (RFC 8707) bind tokens to specific servers, preventing confused deputy attacks. The Gateway applies per-tool authorization policies using `Mcp-Method` and `Mcp-Name` headers.

**Step 4 — Tool Execution**: The model selects a tool and the Client sends `tools/call` via JSON-RPC. The request passes through the **Triple Gate**: Gate 1 (prompt injection filter, PII detection), Gate 2 (tool authorization, parameter validation), Gate 3 (downstream resource access). The tool executes in an isolated sandbox (Docker/OCI, microVM, or gVisor). Output is sanitized before returning to the model.

**Step 5 — A2A Delegation (if cross-agent)**: If the task requires another agent, the Client sends a `tasks/send` JSON-RPC request to the remote agent via A2A. The task progresses through the 9-state lifecycle (submitted → working → completed/failed). Streaming responses arrive via SSE; async updates via webhook push notifications.

**Step 6 — AG-UI Streaming to Frontend**: Agent responses stream to the frontend via AG-UI's SSE protocol. The 17 event types cover lifecycle (RUN_STARTED/FINISHED), text streaming (TEXT_MESSAGE_START/CONTENT/END), tool calls, and state synchronization (STATE_SNAPSHOT/STATE_DELTA with JSON Patch). All tool invocations are logged to the **WORM Audit Log** with W3C Trace Context for distributed trace correlation.

---

## 2. Core Mechanics & Algorithms

### 2.1 MCP Protocol Specification

**Wire format**: JSON-RPC 2.0 with three message types:
- **Requests**: Include unique `id` and `method` name. Expect a response.
- **Responses**: Match by `id`, contain `result` or `error`.
- **Notifications**: One-way (no `id`). Used for lifecycle signals and dynamic updates.

**Three core primitives** (server-exposed):

| Primitive | Control Model | Description | Key Methods |
|-----------|:------------:|-------------|-------------|
| **Tools** | Model-controlled | Executable functions the AI invokes | `tools/list`, `tools/call` |
| **Resources** | App-controlled | Read-only data sources identified by URIs | `resources/list`, `resources/read`, `resources/subscribe` |
| **Prompts** | User-controlled | Reusable templates for LLM interactions | `prompts/list`, `prompts/get` |

**Client-side primitives**: Sampling (server requests model completions from host), Elicitation (server requests user input), Roots (client exposes filesystem paths).

### 2.2 Transport Layer Comparison

| Transport | Latency | Scaling | Auth | Use Case |
|-----------|---------|---------|------|----------|
| **stdio** | ~0ms transport overhead | One client per process | Process boundary | Local dev, desktop agents, personal utilities |
| **Streamable HTTP** | Network-dependent | Horizontal with LB | OAuth 2.1, TLS | Production, cloud, multi-client |
| **HTTP+SSE (deprecated)** | Network-dependent | Requires sticky sessions; 8–10× worse under load | Basic | Legacy only; removal deadlines Q2 2026 |

**Streamable HTTP mechanics**: Single endpoint (e.g., `https://example.com/mcp`). Client sends JSON-RPC via HTTP POST; server responds with `application/json` for short calls or upgrades to `text/event-stream` SSE for streaming. Client listens for server-initiated messages via HTTP GET to the same endpoint.

**2026-07-28 RC**: Removes protocol-level sessions (`Mcp-Session-Id` eliminated). The transport becomes fully stateless — any server instance behind a standard load balancer can handle any request. This resolves the primary horizontal scaling challenge.

### 2.3 Specification Version Evolution

| Version | Date | Key Change |
|---------|------|------------|
| Initial | 2024-11-05 | stdio + HTTP+SSE; Tools, Resources, Prompts |
| March rev | 2025-03-26 | Streamable HTTP replaces SSE; OAuth 2.1 baseline |
| June rev | 2025-06-18 | Auth server separated from MCP server; mandatory RFC 9728 |
| November rev | 2025-11-25 | Async operations, server identity verification, audit trails |
| Release Candidate | 2026-07-28 | Stateless core, Extensions framework (SEPs), Tasks → extension, MCP Apps, W3C Trace Context |

### 2.4 A2A Protocol Architecture

**Core model**: Client-Remote — a client agent delegates tasks to a remote agent via JSON-RPC 2.0 over HTTP(S).

**Agent Cards**: JSON metadata at `/.well-known/agent-card.json` declaring name, skills, supported modalities, authentication requirements, and capability flags. v1.0 adds **signed cards** using JWS (RFC 7515) over JSON Canonicalization Scheme (RFC 8785) — cryptographic proof the card was issued by the domain owner.

**Task lifecycle — 9 states in 3 categories**:

| Category | States |
|----------|--------|
| **Running** | `SUBMITTED`, `WORKING` |
| **Paused** | `INPUT_REQUIRED`, `AUTH_REQUIRED` |
| **Finished** | `COMPLETED`, `FAILED`, `CANCELED`, `REJECTED` |

**Core data objects**: Message (communication turn with role), Part (TextPart/FilePart/DataPart), Artifact (output generated by agent).

**Interaction patterns**: Synchronous (request-response with polling), Streaming (SSE stream of events), Push (webhook notifications for async updates).

**Key JSON-RPC methods**: `tasks/send`, `tasks/get`, `tasks/cancel`, `tasks/sendSubscribe`, `tasks/resubscribe`.

### 2.5 AG-UI Protocol

**Transport**: HTTP POST with SSE response stream. 17 event types across 5 categories:

| Category | Events | Purpose |
|----------|--------|---------|
| **Lifecycle** | RUN_STARTED, RUN_FINISHED, RUN_ERROR, STEP_STARTED, STEP_FINISHED | Run and step boundaries |
| **Text Message** | TEXT_MESSAGE_START, TEXT_MESSAGE_CONTENT, TEXT_MESSAGE_END | Streaming token delivery |
| **Tool Call** | TOOL_CALL_START, TOOL_CALL_ARGS, TOOL_CALL_END, TOOL_CALL_RESULT | Tool execution lifecycle |
| **State Mgmt** | STATE_SNAPSHOT, STATE_DELTA, ACTIVITY_SNAPSHOT | Bi-directional state sync (JSON Patch RFC 6902) |
| **Special** | RAW, CUSTOM | Extension points for application-specific events |

**Event ordering**: Events sharing a `messageId` or `toolCallId` must follow START → CONTENT/ARGS → END. Every run bracketed by RUN_STARTED and a terminal event.

### 2.6 The Three-Protocol Stack

| Protocol | Layer | Direction | Creator | Governance |
|----------|-------|-----------|---------|------------|
| **MCP** | Agent → Tool | Vertical integration | Anthropic | AAIF (Linux Foundation) |
| **A2A** | Agent → Agent | Horizontal coordination | Google | Linux Foundation |
| **AG-UI** | Agent → User | User interaction | CopilotKit | Open source |

Complementary, not competing — analogous to TCP (transport), HTTP (application), and HTML (presentation) at different layers. IBM's ACP merged into A2A under the Linux Foundation in August 2025, consolidating the agent-to-agent space.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Model: MCP Overhead per 1K Tool Calls

**Assumptions**: Average tool call generates 500 input tokens (tool schema + prompt) and 200 output tokens.

| Configuration | Tokens/Call (total) | Cost/1K Calls (Sonnet $3/$15) | Cost/1K Calls (Haiku $0.80/$4) |
|--------------|--------------------:|-----------------------------:|------------------------------:|
| Direct API call (no MCP) | 700 | **$5.10** | **$1.36** |
| MCP (5 tools, selective schema) | 1,200 | **$6.60** | **$1.76** |
| MCP (100 tools, full schema load) | 22,700 | **$71.10** | **$18.96** |
| MCP + A2A delegation | 2,500 | **$10.50** | **$2.80** |

> With 5 servers and 100 tools, injecting every tool schema into context adds ~22,000 tokens per request — a 32× overhead vs. the 700-token baseline. Selective schema loading (only include schemas for tools relevant to the current task) reduces this to ~1,200 tokens.

**Cost optimization mechanisms**:
- **Selective tool loading**: Only inject relevant tool schemas, not all available tools.
- **Caching**: Tool-call latency drops from ~2,485ms (cold) to ~0.01ms on cache hits (>99.99% reduction). Cache tool schemas aggressively.
- **MCP gateway shared caching**: Centralized cache for frequently-called tools across clients.
- **Stateless protocol (2026-07-28 RC)**: Eliminates session overhead and enables horizontal scaling without sticky sessions.

### 3.2 Latency SLA Targets

| Operation | p50 | p95 | p99 | Mitigation |
|-----------|-----|-----|-----|------------|
| stdio tool call (local) | <5ms | 15ms | 50ms | Process-local; no network |
| Streamable HTTP tool call | 50ms | 200ms | 500ms | Connection pooling; keep-alive |
| MCP handshake (initialization) | 100ms | 300ms | 800ms | Cache negotiation result; stateless RC eliminates handshake |
| A2A task delegation (sync) | 200ms | 1s | 3s | Timeout per task; fallback to local agent |
| A2A task delegation (streaming) | 200ms first event | 1s | 5s | SSE streaming reduces perceived latency |
| Gateway overhead (Bifrost) | 0.011ms | 0.05ms | 0.1ms | 11μs at 5,000 RPS; negligible |
| AG-UI first event | 50ms | 200ms | 500ms | SSE stream start; server-side buffering |

**p50 mitigation**: Connection pooling and keep-alive for Streamable HTTP. Selective tool loading to reduce schema parsing time.
**p95 mitigation**: Timeout per tool call (2s default). Circuit breaker on slow MCP servers. Cache tool schemas to avoid repeated discovery calls.
**p99 mitigation**: Hard timeout at 5s per tool call. Gateway-level rate limiting prevents server overload. Fallback to degraded mode (skip optional tools) if primary server is unresponsive.

### 3.3 Throughput & Back-Pressure

**MCP server performance by language** (TM Dev Lab benchmarks):

| Runtime | Tier | RPS | Notes |
|---------|------|----:|-------|
| Rust | 1 | Highest | Unmatched throughput and resource efficiency |
| Java (Quarkus) | 1 | High | Optimal for latency SLAs; sub-ms medians |
| Go | 1 | High | Best balance of performance, memory, simplicity |
| Bun | 2 | 2.2× Node.js | Same code, 2.2× the throughput |
| Node.js | 2 | Moderate | Suitable for low-moderate traffic |
| Python (4 workers + uvloop) | 3 | 259 | FastMCP session overhead is the bottleneck |

**Scaling patterns**:
- **Session pooling**: Shared pools of 10 sessions reach 293 RPS vs. 33–36 RPS with unique sessions per request.
- **Stateless (2026-07-28 RC)**: Removes the session bottleneck entirely. Standard load balancers work.
- **Gateway rate limiting**: Prevent a single client from saturating an MCP server.
- **Queue-based admission**: For high-volume tool calls, queue requests and process at sustainable throughput.

### 3.4 RPO/RTO for MCP Infrastructure

| Component | RPO | RTO | Mechanism |
|-----------|-----|-----|-----------|
| **Tool schemas** | 0 (versioned in schema store) | <1s (reload from store) | Version-controlled tool definitions |
| **Session state (pre-RC)** | Per-checkpoint | <5s (resume from external store) | PostgreSQL/Redis external state |
| **Session state (RC)** | N/A (stateless) | 0 (no state to recover) | Stateless protocol eliminates this |
| **A2A task state** | Per-state-transition | <1s (reload from task store) | Durable task persistence |
| **WORM audit log** | 0 (append-only) | <1s | S3/GCS with cross-region replication |
| **Tool description hashes** | 0 (computed on load) | <1s | Rug-pull detection via hash comparison |

**Disaster recovery**: With the stateless RC, MCP server recovery is trivial — spin up new instances behind a load balancer. No state to recover. For pre-RC stateful deployments, external state stores (PostgreSQL, Redis) hold session data with standard database replication for DR.

### 3.5 NFR Trade-offs

| NFR | stdio (local) | Streamable HTTP (remote) | With Gateway |
|-----|:-------------:|:------------------------:|:------------:|
| **Latency** | Lowest (<5ms) | Moderate (50–200ms) | +11μs overhead |
| **Availability** | Tied to host process | Independent scaling | HA via gateway + LB |
| **Security** | Process boundary only | OAuth 2.1, TLS, PKCE | Triple-gate defense |
| **Scalability** | Single client | Horizontal (stateless RC) | Centralized rate limit |
| **Auditability** | None (local) | W3C Trace Context (RC) | Centralized logging |
| **Compliance** | N/A | OAuth + audit trail | SOC2/HIPAA-ready |

---

## 4. Distributed Resilience & Security

### 4.1 OWASP MCP Top 10

| ID | Category | Core Risk | Prevalence |
|----|----------|-----------|:----------:|
| MCP01 | Token Mismanagement | Hard-coded credentials in config (53% of servers) | Very High |
| MCP02 | Privilege Escalation | Loosely defined permissions expanding over time (only 18% implement scoping) | High |
| MCP03 | Tool Poisoning | Hidden instructions in tool descriptions | High |
| MCP04 | Supply Chain Attacks | Compromised packages, typosquatted servers | High |
| MCP05 | Command Injection | Unsanitized input in shell commands (43% of 30+ CVEs) | Very High |
| MCP06 | Prompt Injection | Hidden instructions in tool responses | High |
| MCP07 | Insufficient AuthN/AuthZ | Only 8.5% OAuth adoption | Very High |
| MCP08 | Insufficient Telemetry | No standardized audit trail | High |
| MCP09 | Shadow MCP Servers | Unapproved deployments outside governance | Medium |
| MCP10 | Context Over-Sharing | Sensitive data leaking across sessions | Medium |

**Impact data**: Between January and February 2026, researchers filed 30+ CVEs targeting MCP. With 5 connected MCP servers, a single compromised server achieved 78.3% attack success rate (Palo Alto Unit 42). A June 2026 study found only a runtime enforcing scope as an explicit execution-time invariant blocked all 10 tested attack cases.

### 4.2 Circuit Breaker for MCP Servers

#### 4.2.1 State Machine

```
                    success
              ┌───────────────┐
              │               │
              ▼               │
         ┌─────────┐    ┌────┴─────┐    ┌─────────────┐
         │ CLOSED  │───▶│  OPEN    │───▶│ HALF-OPEN   │
         │         │    │          │    │             │
         │ Normal  │    │ Skip     │    │ Send 2     │
         │ tool    │    │ tool;    │    │ test calls │
         │ calls   │    │ return   │    │ (tools/list│
         │         │    │ fallback │    │  + 1 call) │
         └─────────┘    └──────────┘    └─────────────┘
              ▲          │       ▲            │
              │          │       │            │
              │          │       └────────────┘
              │          │        probe fails
              │     after 30s
              │     recovery timeout
              │     (30s → 60s → 120s exponential)
              │
              └──────────────────────────────┘
                    2/2 probes succeed
```

**Thresholds**:
- **Closed → Open**: 5 failures (timeout, 5xx, parse error) within 60s window.
- **Open duration**: 30s initial recovery timeout with exponential backoff (30s → 60s → 120s).
- **Half-Open → Closed**: 2 consecutive successful probes (`tools/list` + one lightweight `tools/call`).
- **Fallback**: Skip failed MCP server; if tool was critical, route to alternative server or return graceful degradation message to model.

#### 4.2.2 Per-Component Breaker Applications

| Component | Failure Type | Class | Fallback Strategy |
|-----------|-------------|-------|-------------------|
| MCP server (timeout/5xx) | Server unreachable | **Transient** | Retry with backoff; skip server if persistent |
| MCP server (malformed JSON-RPC) | Protocol error | **Transient** | Reject response; retry once; mark degraded |
| Tool execution (command injection detected) | Security violation | **Permanent** (attack) | Block tool permanently; alert security team |
| Tool poisoning (description change detected) | Rug pull | **Permanent** (attack) | Quarantine server; revert to last known-good description |
| A2A remote agent (timeout) | Network/agent issue | **Transient** | Retry; fallback to local agent execution |
| OAuth token refresh | Auth server down | **Transient** | Use cached token until expiry; queue requests |
| Transport (SSE stream drop) | Connection issue | **Transient** | Reconnect with exponential backoff; resume from last event |

### 4.3 Failure Taxonomy

| Failure | Class | Detection | Mitigation |
|---------|-------|-----------|------------|
| Tool poisoning (MCP03) | **Permanent** (attack) | Hash tool descriptions on first load; alert on change | ETDI — cryptographic signing of tool definitions |
| Rug pull (post-approval description change) | **Permanent** (attack) | Store description hash; compare on every `tools/list` | Version-pin descriptions; alert on hash mismatch |
| Supply chain compromise (MCP04) | **Permanent** (attack) | Package integrity checks; anomalous behavior detection | Pin versions; verify checksums; use Tier 1 servers only |
| Command injection (MCP05) | **Permanent** (attack) | Input validation; WAF rules | Parameterized execution; never pass raw input to shell |
| Prompt injection cascade (MCP06) | **Transient** | Output sanitization; injection pattern detection | Mark tool outputs as untrusted; strip HTML |
| Schema overhead (100 tools = 22K tokens) | **Transient** | Token count monitoring | Selective tool loading; schema summarization |
| Transport failure (connection drop) | **Transient** | Connection health check; heartbeat | Reconnect with backoff; resume from last event |
| Session affinity violation (pre-RC) | **Transient** | Request routing to wrong instance | External state store; or migrate to stateless RC |
| Endpoint death (52% of remote endpoints) | **Permanent** (operational) | Health check endpoint monitoring | Registry health scoring; fallback servers |
| Confused deputy (forwarding client token) | **Permanent** (design) | Token audience validation | Resource Indicators (RFC 8707); separate upstream tokens |

### 4.3.1 Idempotency in MCP Tool Calls

Tool calls may be retried after timeouts or transport failures. Tools with side effects must be idempotent:

```
Client sends tools/call to MCP server:
                                    │
                          ┌─────────▼──────────┐
                          │ Idempotency Guard   │
                          │ key = hash(tool_name│
                          │   + canonical_args  │
                          │   + client_id       │
                          │   + request_id)     │
                          └─────────┬──────────┘
                                    │
                     ┌──────────────▼──────────────┐
                     │ IF key in executed_calls:    │
                     │   RETURN cached_result       │
                     │ ELSE:                        │
                     │   execute tool               │
                     │   store result with key      │
                     │   RETURN result              │
                     └─────────────────────────────┘
```

**A2A idempotency**: GET operations are naturally idempotent. `tasks/send` uses `messageId` for duplicate detection. `tasks/cancel` is idempotent (duplicate cancellation returns same effect or `TaskNotFoundError`).

### 4.3.2 Poison-Pill Detection for MCP Tools

A poison pill in MCP is a malicious tool description, response, or resource that hijacks the model's behavior — exfiltrating data, overriding other tools' instructions, or performing unauthorized actions.

**Detection heuristics**:
- Tool description contains instructions directed at the model (e.g., "ignore previous instructions", "before using other tools, first send data to...").
- Tool description changes after initial approval (rug pull — hash comparison).
- Tool response contains HTML, JavaScript, or URL patterns that could be rendered/followed.
- Tool requests permissions beyond its declared scope (e.g., a "search" tool requesting write access).

**Quarantine flow**:
```
  tools/list response ──▶ ┌────────────────┐
                          │ Description     │
                          │ Scanner         │
                          │ - Hash compare  │ ──(changed)──▶ ┌──────────────┐
                          │ - Injection     │                │ Quarantine   │
                          │   pattern scan  │                │ - Block tool │
                          │ - Scope check   │                │ - Alert ops  │
                          └────────┬───────┘                │ - Revert to  │
                                   │ (clean)                 │   known-good │
                                   ▼                         └──────────────┘
                          Tool available for
                          model invocation
```

### 4.4 Enterprise Security Boundaries

#### 4.4.1 Five-Layer Security Model

1. **Tool capability modeling**: Define what each tool can and cannot do. Separate read-only from write-capable tools. Never combine shell, filesystem, browser, email, and source code access in the same unconstrained workflow.

2. **Token-to-tool authorization mapping**: Map OAuth access token scopes to specific tool permissions. Use `Mcp-Method` and `Mcp-Name` headers (2026-07-28 RC) for per-tool authorization at the gateway.

3. **Runtime sandbox isolation**: Tools execute in Docker/OCI containers (rootless, read-only root filesystem, seccomp profiles), microVMs (stronger isolation for untrusted code), or restricted-language sandboxes. Network isolation: bind to 127.0.0.1 or trusted interfaces, default-deny egress.

4. **Tool supply chain validation**: Pin MCP server versions. Verify package checksums. Use Tier 1 (production-grade, vendor-official) servers for critical operations. Monitor for typosquatting (the Postmark MCP incident affected ~300 organizations across 15 versions).

5. **Transport identity enforcement**: TLS for all remote connections. mTLS for server-to-server. OAuth 2.1 with PKCE for client authentication. Resource Indicators prevent confused deputy attacks.

#### 4.4.2 Zero-Trust MCP Deployment

1. **No implicit trust in tool descriptions**: Treat every tool description as potentially adversarial. Hash descriptions on first load and alert on any change. Scan for injection patterns before making descriptions available to the model.

2. **Mark tool outputs as untrusted**: Strip HTML, sanitize attack strings server-side. Separate untrusted content during context assembly. Never allow tool output to override system instructions.

3. **Per-tool authorization with least privilege**: Each tool gets only the permissions it needs. A database query tool gets SELECT-only access. A file tool gets access to specific directories only. Enforce at the gateway, not in the tool code.

4. **Audit everything**: Log every tool invocation, parameter, response, and authorization decision to WORM storage. W3C Trace Context (traceparent, tracestate, baggage) enables distributed trace correlation across MCP servers and gateways.

5. **Network segmentation**: MCP servers for different security tiers run in separate network segments. High-privilege tools (payment, deployment) in isolated VPC with human-in-the-loop approval.

---

## 5. Production Enterprise Code

### 5.1 MCP Server with Tool Registration and Sandbox Execution

```python
import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ToolRisk(Enum):
    READ_ONLY = "read_only"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict
    risk_level: ToolRisk = ToolRisk.READ_ONLY
    requires_approval: bool = False
    allowed_scopes: list[str] = field(default_factory=list)
    description_hash: str = ""

    def __post_init__(self):
        self.description_hash = hashlib.sha256(
            self.description.encode()
        ).hexdigest()[:16]


class MCPServer:
    def __init__(self, sandbox_executor, auth_validator):
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, callable] = {}
        self._sandbox = sandbox_executor
        self._auth = auth_validator
        self._call_log: list[dict] = []

    def register_tool(self, tool: ToolDefinition, handler: callable) -> None:
        self._scan_description(tool)
        self._tools[tool.name] = tool
        self._handlers[tool.name] = handler

    def handle_request(self, request: dict, auth_token: str) -> dict:
        method = request.get("method")
        request_id = request.get("id")

        if method == "tools/list":
            return self._tools_list(request_id)
        elif method == "tools/call":
            return self._tools_call(request, request_id, auth_token)
        else:
            return self._error(request_id, -32601, f"Unknown method: {method}")

    def _tools_list(self, request_id) -> dict:
        tools = [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema,
            }
            for t in self._tools.values()
        ]
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}

    def _tools_call(self, request: dict, request_id, auth_token: str) -> dict:
        params = request.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name not in self._tools:
            return self._error(request_id, -32602, f"Unknown tool: {tool_name}")

        tool = self._tools[tool_name]

        if not self._auth.validate(auth_token, tool.allowed_scopes):
            return self._error(request_id, -32603, "Insufficient scope")

        if tool.description_hash != hashlib.sha256(
            tool.description.encode()
        ).hexdigest()[:16]:
            return self._error(request_id, -32604, "Tool description integrity violation")

        idem_key = hashlib.sha256(
            json.dumps({"tool": tool_name, "args": arguments}, sort_keys=True).encode()
        ).hexdigest()[:16]

        for log_entry in reversed(self._call_log[-100:]):
            if log_entry.get("idem_key") == idem_key:
                return {"jsonrpc": "2.0", "id": request_id, "result": log_entry["result"]}

        handler = self._handlers[tool_name]
        result = self._sandbox.execute(handler, arguments, timeout_ms=5000)

        log_entry = {
            "tool": tool_name,
            "args": arguments,
            "idem_key": idem_key,
            "result": result,
        }
        self._call_log.append(log_entry)

        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _scan_description(self, tool: ToolDefinition) -> None:
        import re
        injection_patterns = [
            r"(?i)ignore\s+(previous|all|prior)",
            r"(?i)before\s+using\s+other\s+tools",
            r"(?i)send\s+(all|this|the)\s+data\s+to",
            r"(?i)override\s+(system|instructions)",
        ]
        for pattern in injection_patterns:
            if re.search(pattern, tool.description):
                raise ValueError(
                    f"Tool '{tool.name}' description contains suspicious pattern: "
                    f"{pattern}"
                )

    def _error(self, request_id, code: int, message: str) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
```

### 5.2 MCP Gateway with Triple-Gate Pattern

```python
import re
import time
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class GateResult:
    passed: bool
    blocked_by: str = ""
    reason: str = ""


class MCPGateway:
    def __init__(self, auth_validator, rate_limiter,
                 max_requests_per_minute: int = 100):
        self._auth = auth_validator
        self._rate_limiter = rate_limiter
        self._max_rpm = max_requests_per_minute
        self._request_counts: dict[str, list[float]] = defaultdict(list)

    def process(self, request: dict, auth_token: str,
                client_id: str) -> dict | GateResult:
        gate1 = self._gate1_client_to_llm(request)
        if not gate1.passed:
            return gate1

        gate2 = self._gate2_llm_to_mcp(request, auth_token, client_id)
        if not gate2.passed:
            return gate2

        return request

    def _gate1_client_to_llm(self, request: dict) -> GateResult:
        params = request.get("params", {})
        args = params.get("arguments", {})

        for key, value in args.items():
            if isinstance(value, str):
                if self._contains_injection(value):
                    return GateResult(
                        passed=False,
                        blocked_by="gate1",
                        reason=f"Prompt injection detected in argument '{key}'",
                    )
                if self._contains_pii(value):
                    return GateResult(
                        passed=False,
                        blocked_by="gate1",
                        reason=f"PII detected in argument '{key}'",
                    )

        return GateResult(passed=True)

    def _gate2_llm_to_mcp(self, request: dict, auth_token: str,
                           client_id: str) -> GateResult:
        tool_name = request.get("params", {}).get("name", "")

        if not self._auth.validate_tool_access(auth_token, tool_name):
            return GateResult(
                passed=False,
                blocked_by="gate2",
                reason=f"Token lacks scope for tool '{tool_name}'",
            )

        now = time.time()
        recent = [t for t in self._request_counts[client_id] if now - t < 60]
        self._request_counts[client_id] = recent

        if len(recent) >= self._max_rpm:
            return GateResult(
                passed=False,
                blocked_by="gate2",
                reason=f"Rate limit exceeded: {self._max_rpm} requests/minute",
            )

        self._request_counts[client_id].append(now)
        return GateResult(passed=True)

    def _contains_injection(self, text: str) -> bool:
        patterns = [
            r"(?i)ignore\s+(previous|all|prior)\s+(instructions|prompts)",
            r"(?i)you\s+are\s+now\s+a",
            r"(?i)system:\s*",
            r"(?i)disregard\s+(your|the)",
        ]
        return any(re.search(p, text) for p in patterns)

    def _contains_pii(self, text: str) -> bool:
        patterns = [
            r"\b\d{3}-\d{2}-\d{4}\b",
            r"\b\d{16}\b",
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        ]
        return any(re.search(p, text) for p in patterns)
```

### 5.3 A2A Agent Card Discovery Client

```python
import json
from dataclasses import dataclass, field


@dataclass
class A2ASkill:
    id: str
    name: str
    description: str
    tags: list[str] = field(default_factory=list)


@dataclass
class A2AAgentCard:
    name: str
    description: str
    url: str
    version: str
    skills: list[A2ASkill]
    supported_modes: list[str] = field(default_factory=lambda: ["text/plain"])
    auth_schemes: list[str] = field(default_factory=lambda: ["bearer"])
    supports_streaming: bool = False
    supports_push: bool = False


class A2AClient:
    def __init__(self, http_client):
        self._http = http_client
        self._card_cache: dict[str, A2AAgentCard] = {}

    async def discover(self, agent_url: str) -> A2AAgentCard:
        if agent_url in self._card_cache:
            return self._card_cache[agent_url]

        card_url = f"{agent_url.rstrip('/')}/.well-known/agent-card.json"
        response = await self._http.get(card_url)
        data = json.loads(response.text)

        card = A2AAgentCard(
            name=data["name"],
            description=data["description"],
            url=data["url"],
            version=data.get("version", "1.0"),
            skills=[A2ASkill(**s) for s in data.get("skills", [])],
            supported_modes=data.get("defaultInputModes", ["text/plain"]),
            auth_schemes=[
                s["scheme"] for s in data.get("authentication", {}).get("schemes", [])
            ],
            supports_streaming=data.get("capabilities", {}).get("streaming", False),
            supports_push=data.get("capabilities", {}).get("pushNotifications", False),
        )
        self._card_cache[agent_url] = card
        return card

    async def send_task(self, agent_url: str, task_description: str,
                        auth_token: str) -> dict:
        card = await self.discover(agent_url)

        request = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": task_description}],
                },
            },
        }

        response = await self._http.post(
            card.url,
            json=request,
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        return json.loads(response.text)

    def find_best_agent(self, task: str,
                        agents: list[A2AAgentCard]) -> A2AAgentCard | None:
        task_lower = task.lower()
        scored = []
        for agent in agents:
            score = sum(
                0.3 for skill in agent.skills
                if any(tag in task_lower for tag in skill.tags)
            )
            score += sum(
                0.1 for skill in agent.skills
                if any(word in task_lower for word in skill.name.lower().split())
            )
            scored.append((agent, min(score, 1.0)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0] if scored and scored[0][1] > 0.2 else None
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Enterprise MCP Gateway for a Multi-Tool AI Platform

**Business context**: A financial services company deploys AI agents that connect to 15 MCP servers (Bloomberg terminal, internal databases, CRM, email, document management, compliance tools, etc.). Requirements: SOC2 compliance with full audit trail, <200ms tool-call latency, prevention of cross-tool data exfiltration, support for 500 concurrent users, and $20K/month infrastructure budget. Currently, each AI agent connects directly to all MCP servers with shared credentials.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                     MCP GATEWAY ARCHITECTURE                             │
 │                                                                          │
 │  AI Agents ──▶ ┌──────────────┐  ┌────────────────────────────────────┐ │
 │  (500 users)   │ Gateway      │  │         MCP SERVER TIERS           │ │
 │                │              │  │                                    │ │
 │                │ Gate 1:      │  │  Tier 1 (Read-Only)               │ │
 │                │  Injection   │  │  ├─ Bloomberg MCP                 │ │
 │                │  + PII filter│  │  ├─ Internal KB MCP               │ │
 │                │              │──▶│  └─ CRM (read) MCP               │ │
 │                │ Gate 2:      │  │                                    │ │
 │                │  OAuth scope │  │  Tier 2 (Write, HITL)             │ │
 │                │  + tool auth │  │  ├─ Email MCP (approval required) │ │
 │                │              │  │  ├─ CRM (write) MCP               │ │
 │                │ Gate 3:      │  │  └─ Doc Management MCP            │ │
 │                │  Downstream  │  │                                    │ │
 │                │  rate limit  │  │  Tier 3 (Compliance, Isolated)    │ │
 │                │              │  │  ├─ Trading MCP (separate VPC)    │ │
 │                │              │  │  └─ Compliance MCP (audit-only)   │ │
 │                └──────────────┘  └────────────────────────────────────┘ │
 │                       │                                                  │
 │                ┌──────▼──────┐                                           │
 │                │ WORM Audit  │                                           │
 │                │ Log (S3)    │                                           │
 │                │ + W3C Trace │                                           │
 │                └─────────────┘                                           │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Direct Agent-to-Server (Current) | B: Centralized MCP Gateway (Recommended) | C: Per-Agent Sidecar Proxy |
|-----------|-------------------------------------|----------------------------------------|---------------------------|
| **Security** | ⬛⬜⬜ — Shared credentials; no cross-tool isolation; 78% attack success with 1 compromised server | ⬛⬛⬛ — Per-tool OAuth scopes; triple-gate defense; centralized injection detection | ⬛⬛⬜ — Per-agent isolation but duplicated security logic |
| **Audit compliance (SOC2)** | ⬛⬜⬜ — No centralized logging; agents log inconsistently | ⬛⬛⬛ — Single audit endpoint; W3C Trace Context; WORM storage | ⬛⬛⬜ — Distributed logs; aggregation complexity |
| **Latency overhead** | ⬛⬛⬛ — Zero gateway overhead | ⬛⬛⬛ — 11μs per request (Bifrost benchmark); negligible at <200ms target | ⬛⬛⬜ — Sidecar adds 1–5ms per call |
| **Operational complexity** | ⬛⬛⬛ — Simple direct connections | ⬛⬛⬜ — Gateway infrastructure + HA configuration | ⬛⬜⬜ — 500 sidecar instances to manage |
| **Cost** | ⬛⬛⬛ — No additional infrastructure | ⬛⬛⬛ — Gateway + S3 audit storage ≈ $3K/month | ⬛⬜⬜ — 500 sidecar instances ≈ $15K/month |
| **Cross-tool data exfiltration prevention** | ⬛⬜⬜ — No isolation between tools | ⬛⬛⬛ — Per-tool scopes; network segmentation by tier | ⬛⬛⬜ — Agent-level isolation but no cross-tool policy |

**Recommended approach**: **B (Centralized MCP Gateway)**.

**Decision rationale**: The SOC2 audit trail requirement demands centralized, immutable logging — impossible with Option A's direct connections where each agent logs independently. The cross-tool exfiltration risk is critical in financial services: with 15 MCP servers and shared credentials, a single compromised tool description can access Bloomberg data, CRM records, and email simultaneously. The gateway's triple-gate pattern with per-tool OAuth scopes prevents this at 11μs overhead — well within the 200ms latency budget. At ~$3K/month for gateway infrastructure + S3 audit storage, it's far cheaper than 500 sidecar instances ($15K/month) and delivers better security via centralized policy enforcement. The three-tier server classification (read-only, write with HITL, compliance in isolated VPC) maps to natural risk boundaries in financial services.

### 6.2 Scenario: Cross-Vendor Agent Interop Platform Using A2A

**Business context**: A manufacturing conglomerate runs AI agents from three vendors: Google ADK agents for supply chain optimization, LangGraph agents for engineering documentation, and custom Python agents for quality control. Currently, these operate in silos — a supply chain decision can't query engineering specs, and quality control findings don't feed into supply chain planning. Requirements: cross-vendor agent communication without exposing internal agent implementations, <5s delegation latency, support for structured data exchange (CAD files, sensor data), and vendor-neutral so no single vendor lock-in.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                     A2A INTEROP ARCHITECTURE                             │
 │                                                                          │
 │  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐            │
 │  │ Google ADK     │  │ LangGraph      │  │ Custom Python  │            │
 │  │ Agents         │  │ Agents         │  │ Agents         │            │
 │  │ (Supply Chain) │  │ (Engineering)  │  │ (Quality Ctrl) │            │
 │  └───────┬────────┘  └───────┬────────┘  └───────┬────────┘            │
 │          │                   │                    │                      │
 │          │         A2A Protocol (JSON-RPC/HTTPS)  │                      │
 │          │                   │                    │                      │
 │  ┌───────▼───────────────────▼────────────────────▼──────────────────┐  │
 │  │                    A2A COORDINATION LAYER                         │  │
 │  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │  │
 │  │  │ Agent Card  │  │ Task Router │  │ Auth Broker │              │  │
 │  │  │ Registry    │  │ - Skill     │  │ - Per-vendor│              │  │
 │  │  │ - All agent │  │   matching  │  │   OAuth     │              │  │
 │  │  │   cards     │  │ - Load      │  │ - Token     │              │  │
 │  │  │ - Signed    │  │   balance   │  │   exchange  │              │  │
 │  │  │   (JWS)     │  │ - Fallback  │  │ - Scope     │              │  │
 │  │  └─────────────┘  └─────────────┘  └─────────────┘              │  │
 │  └──────────────────────────────────────────────────────────────────┘  │
 │                                                                        │
 │  Each agent exposes /.well-known/agent-card.json                       │
 │  Each agent speaks A2A JSON-RPC over HTTPS                             │
 │  Internal implementations remain opaque (A2A opacity principle)        │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Custom REST API Integration | B: A2A Protocol with Agent Cards (Recommended) | C: Shared Database / Event Bus |
|-----------|-------------------------------|-----------------------------------------------|-------------------------------|
| **Vendor neutrality** | ⬛⬜⬜ — Custom adapters per vendor pair (N² integrations) | ⬛⬛⬛ — Open standard; all 3 frameworks support A2A natively | ⬛⬛⬜ — Neutral bus but requires custom adapters |
| **Implementation opacity** | ⬛⬜⬜ — Must expose internal APIs | ⬛⬛⬛ — Agents interact without sharing internal logic, memory, or tools | ⬛⬜⬜ — Shared schema couples implementations |
| **Structured data exchange** | ⬛⬛⬜ — Custom serialization per pair | ⬛⬛⬛ — DataPart (JSON), FilePart (binary), TextPart; native multi-modal | ⬛⬛⬜ — Schema-dependent |
| **Delegation latency** | ⬛⬛⬛ — Direct HTTP call | ⬛⬛⬛ — JSON-RPC over HTTPS; streaming via SSE | ⬛⬛⬜ — Event bus adds 50–200ms |
| **Discovery** | ⬛⬜⬜ — Hardcoded endpoints | ⬛⬛⬛ — Agent Cards at well-known path; skill-based matching | ⬛⬛⬜ — Service registry needed |
| **Maintenance (3 vendors × N agents)** | ⬛⬜⬜ — 9 custom integrations (3²) | ⬛⬛⬛ — Each agent implements 1 standard; 3 total | ⬛⬛⬜ — Shared schema drift across vendors |

**Recommended approach**: **B (A2A Protocol with Agent Cards)**.

**Decision rationale**: The cross-vendor requirement with 3 different frameworks makes Option A's custom REST approach untenable — 9 custom integrations (3² pairwise) that must be maintained independently. A2A's opacity principle is the key architectural benefit: supply chain agents don't need to know that engineering documentation runs on LangGraph, only that an agent at `engineering.internal` has skills for "spec lookup" and "CAD retrieval." All three frameworks (Google ADK, LangGraph, and Python) support A2A natively — no custom adapters needed. Signed Agent Cards (JWS) provide trust without a central authority. The structured data exchange (DataPart for sensor readings, FilePart for CAD files) handles the manufacturing use case natively. At <5s delegation latency, A2A over HTTPS with optional SSE streaming is well within budget. Option C (shared database) creates tight coupling through shared schemas that would drift across vendor teams.

---

*Module 10 complete. Covers MCP protocol architecture and evolution, OWASP MCP Top 10 security risks, A2A and AG-UI protocols, the three-protocol stack, production deployment patterns, and enterprise gateway design.*
