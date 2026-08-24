# Module 09: Multi-Agent Systems — Topologies, Coordination, Frameworks, A2A, and Production Orchestration

**Scope**: Multi-agent architectures (supervisor, hierarchical, peer-to-peer, swarm, debate), communication protocols (message passing, shared state, blackboard, event-driven), coordination patterns (handoffs, delegation, consensus), A2A protocol, framework comparison (LangGraph, CrewAI, AutoGen/AG2, OpenAI Agents SDK, Google ADK), scaling, fault tolerance, evaluation, and Anthropic's multi-agent behavioral research.
**Prerequisite**: Module 04 (Agent Architecture), Module 05 (Agent Frameworks).
**Last updated**: 2026-08-21 | **Sources consulted**: 67

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                    CONTROL PLANE                                           │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
 │  │  Topology        │  │  Task Allocator  │  │  Budget &        │  │  HITL Gate       │  │
 │  │  Selector        │  │  - Decompose to  │  │  Admission Ctrl  │  │  - Approval for  │  │
 │  │  - Star/Chain/   │  │    subtasks      │  │  - Per-agent     │  │    high-stakes   │  │
 │  │    Mesh/Tree/    │  │  - Match to agent│  │    token cap     │  │  - Risk-tiered   │  │
 │  │    Swarm/Dynamic │  │    capabilities  │  │  - Model cascade │  │    escalation    │  │
 │  │  - Static or     │  │  - DAG with deps │  │  - Cost anomaly  │  │  - Override      │  │
 │  │    runtime adapt │  │  - Parallel/seq  │  │    detection     │  │    mechanism     │  │
 │  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
 └───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┘
             │                    │                    │                    │
 ┌───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┐
 │           ▼                    ▼                    ▼                    ▼                  │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                       DATA PLANE: MULTI-AGENT EXECUTION ENGINE                     │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  ORCHESTRATION LAYER                                                     │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Supervisor / │  │ Handoff      │  │ Consensus    │  │ Conflict   │  │      │    │
 │  │  │  │ Orchestrator │  │ Manager      │  │ Protocol     │  │ Resolver   │  │      │    │
 │  │  │  │ - Dispatches │  │ - Transfer   │  │ - Voting     │  │ - Semantic │  │      │    │
 │  │  │  │   subtasks   │  │   ownership  │  │ - Debate     │  │   diverge  │  │      │    │
 │  │  │  │ - Synthesizes│  │ - Context    │  │ - DCBFT for  │  │   detect   │  │      │    │
 │  │  │  │   results    │  │   propagation│  │   BFT        │  │ - A-HMAD   │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  AGENT POOL                                                              │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Agent A      │  │ Agent B      │  │ Agent C      │  │ Agent N    │  │      │    │
 │  │  │  │ (Researcher) │  │ (Coder)      │  │ (Reviewer)   │  │ (Custom)   │  │      │    │
 │  │  │  │ - Own context│  │ - Own context│  │ - Own context│  │ - Own ctx  │  │      │    │
 │  │  │  │ - Own tools  │  │ - Own tools  │  │ - Own tools  │  │ - Own tools│  │      │    │
 │  │  │  │ - Own model  │  │ - Own model  │  │ - Own model  │  │ - Own model│  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  COMMUNICATION LAYER                                                     │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Message Bus  │  │ Shared State │  │ Blackboard   │  │ A2A        │  │      │    │
 │  │  │  │ - Async      │  │ - LangGraph  │  │ - Centralized│  │ Protocol   │  │      │    │
 │  │  │  │   message    │  │   checkpoint │  │   mediated   │  │ - Cross-   │  │      │    │
 │  │  │  │   passing    │  │ - Explicit   │  │   comm       │  │   vendor   │  │      │    │
 │  │  │  │ - Event      │  │   state mgmt │  │ - 5% fewer   │  │   agent    │  │      │    │
 │  │  │  │   driven     │  │              │  │   tokens     │  │   interop  │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                         TOOL PROXY LAYER                                           │    │
 │  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐       │    │
 │  │  │ MCP Gateway   │  │ Tool Auth     │  │ Injection     │  │ Agent Card    │       │    │
 │  │  │ - Agent-to-   │  │ - Per-agent   │  │ Filter        │  │ Registry      │       │    │
 │  │  │   tool routing│  │   tool scope  │  │ - Cross-agent │  │ - /.well-     │       │    │
 │  │  │ - Schema val  │  │ - Least       │  │   injection   │  │   known/      │       │    │
 │  │  │ - Rate limit  │  │   privilege   │  │   cascade     │  │   agent.json  │       │    │
 │  │  │ - Circuit brk │  │ - Audit log   │  │ - Mind virus  │  │ - Capability  │       │    │
 │  │  │               │  │               │  │   detection   │  │   advertise   │       │    │
 │  │  └───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘       │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  PERSISTENCE LAYER                                         │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Agent State Store │  │ Checkpoint Store  │  │ Message History   │  │ WORM Audit Log │  │
 │  │ - Per-agent       │  │ - LangGraph       │  │ - Inter-agent     │  │ - All agent    │  │
 │  │   context window  │  │   PostgresSaver   │  │   messages        │  │   actions      │  │
 │  │ - Tool outputs    │  │ - Per-superstep   │  │ - Handoff records │  │ - Tool calls   │  │
 │  │ - Session state   │  │   persistence     │  │ - Debate rounds   │  │ - Immutable    │  │
 │  │                   │  │ - Resume-ready    │  │ - Context snapshots│  │ - Chain-of-    │  │
 │  │                   │  │                   │  │                   │  │   custody      │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  TELEMETRY PLANE                                           │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Distributed       │  │ Per-Agent Cost    │  │ Coordination      │  │ Behavioral     │  │
 │  │ Tracing           │  │ Attribution       │  │ Metrics           │  │ Anomaly Alert  │  │
 │  │ - OTel GenAI      │  │ - Token spend per │  │ - Handoff count   │  │ - Loop detect  │  │
 │  │   semantic conv.  │  │   agent           │  │ - Consensus rounds│  │ - Cost spike   │  │
 │  │ - Cross-agent     │  │ - Model tier      │  │ - Conflict rate   │  │ - Context rot  │  │
 │  │   trace IDs       │  │   breakdown       │  │ - Task completion │  │ - Collusion    │  │
 │  │ - Waterfall views │  │ - Anomaly detect  │  │   rate            │  │   patterns     │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

**Step 1 — Topology Selection**: An incoming task enters the **Control Plane**. The Topology Selector evaluates the task's structure: single-domain → single agent, multi-domain with clear stages → chain/pipeline, multi-domain needing parallel work → star/supervisor, 50+ independent sub-tasks → swarm. ~70% of enterprise deployments use star topology.

**Step 2 — Task Decomposition & Allocation**: The **Task Allocator** decomposes the goal into subtasks using AOP principles: solvability (each subtask within an agent's capability), completeness (subtasks cover the full query), non-redundancy (no duplicate work). Subtasks are organized as a DAG with dependency edges. Independent branches are marked for parallel execution.

**Step 3 — Budget & Admission Control**: Before execution, the **Budget Controller** checks per-agent token caps, per-task cost estimates, and model cascade rules. Multi-agent systems use 3–15× more tokens than single-agent equivalents — cost controls prevent runaway spending.

**Step 4 — Agent Execution**: Subtasks are dispatched to the **Agent Pool**. Each agent operates with its own context window, tools, and model. Agents communicate via the **Communication Layer**: message passing (direct exchange), shared state (LangGraph checkpointer), blackboard (centralized mediated comm), or A2A protocol (cross-vendor interop). Tool calls route through the **Tool Proxy Layer** with per-agent authorization scopes and injection filtering.

**Step 5 — Coordination**: For tasks requiring agreement, the **Consensus Protocol** runs: majority voting for reasoning tasks (13.2% better than consensus), consensus for knowledge retrieval (2.8% better than voting). The **Conflict Resolver** detects semantic intent divergence — cooperating agents developing inconsistent interpretations of shared objectives.

**Step 6 — Synthesis & Verification**: The orchestrator synthesizes agent outputs. A **Critic agent** reviews for errors, flags hallucinations, and triggers retries. Results pass through the **HITL Gate** for high-stakes decisions. All actions are logged to the **WORM Audit Log** with distributed trace IDs linking cross-agent execution paths.

---

## 2. Core Mechanics & Algorithms

### 2.1 Multi-Agent Topology Comparison

| Topology | Structure | Control | Communication Cost | Failure Mode | Best For |
|----------|-----------|---------|-------------------:|:------------:|----------|
| **Star / Supervisor** | Central orchestrator + workers | Centralized | O(N) — hub mediates all | Hub SPOF | ~70% of enterprise deployments; clear subtask delegation |
| **Chain / Pipeline** | Sequential stages | Linear | O(N) — pass-through | Early-stage cascade | Workflows with clear stage boundaries (MetaGPT) |
| **Mesh / Peer-to-Peer** | All-to-all | Distributed | O(N²) — quadratic | Complex coordination | Maximum information flow; rare in production |
| **Tree / Hierarchical** | Layered delegation | Hierarchical | O(N log N) | Mid-level bottleneck | Problems with natural recursive decomposition |
| **Swarm** | Autonomous peers, no orchestrator | Emergent | O(N) — local rules only | Unpredictable emergence | 50+ independent sub-tasks (Kimi K2.5: 100 sub-agents, 1,500 parallel tool calls) |
| **Dynamic / Adaptive** | Runtime reconfiguration | Adaptive | Variable | Topology instability | Academic frontier; tasks with unknown structure |

### 2.2 Communication Patterns

**Message Passing**: The most common pattern. Agents exchange natural language or structured data directly. Variants include turn-taking (sequential), parallel exchange (simultaneous), and summarizer-mediated (intermediate context aggregation).

**Shared State**: Agents read/write a common state store. LangGraph's core mechanism — explicit state persists across graph nodes. Risk: centralized shared memory becomes a throughput bottleneck. Distributed memory with synchronization adds latency but improves resilience.

**Blackboard Architecture**: Revival of the 1985 Hayes-Roth pattern for LLMs. A central blackboard serves as mediated communication — agents neither message each other directly nor maintain private histories. Recent research (bMAS, 2025) shows blackboard outperforms CoT and static MAS by ~5% while reducing token cost through centralized message buffering. Achieved 13–57% improvement over master-slave baselines in information discovery.

**Event-Driven / Asynchronous**: Agents react to event streams rather than being polled. AG-UI protocol standardizes event-driven agent-to-frontend communication using streaming JSON events over HTTP/SSE/WebSocket.

### 2.3 The Protocol Stack (MCP / A2A / AG-UI)

Three protocols form the complete agentic communication stack as of 2026:

| Protocol | Layer | Purpose | Creator | Status |
|----------|-------|---------|---------|--------|
| **MCP** | Agent → Tool | How an agent accesses external tools and data | Anthropic | Production, widely adopted |
| **A2A** | Agent → Agent | How agents delegate work across vendor boundaries | Google | v0.3+, 150+ orgs, Linux Foundation |
| **AG-UI** | Agent → User | How agents stream results to frontend UIs | CopilotKit | Production, 12K+ GitHub stars |

Complementary, not competing — analogous to TCP, HTTP, and HTML at different layers. IBM's ACP merged with A2A under the Linux Foundation in August 2025, consolidating agent-to-agent interop.

**A2A key mechanisms**: Agent Cards at `/.well-known/agent.json` for machine-readable capability advertisement. 11 JSON-RPC methods (SendMessage, SendStreamingMessage, GetTask, SubscribeToTask, etc.). gRPC support and signed security cards in v0.3. Agent Payments Protocol (AP2) extension announced September 2025.

### 2.4 Handoff Protocols

Handoffs transfer conversational ownership from one agent to another — the receiving agent owns the remainder of the current turn (unlike tool calls where the caller retains control).

**OpenAI Agents SDK**: The clearest implementation. Agents declare handoff targets in configuration. The `Runner` class's agent loop handles LLM calls → tool execution → handoff processing → guardrail checks until final output. Design rule: unwieldy beyond 8–10 agent types.

**LangGraph State-Driven**: Handoffs are state mutations. The `Command` primitive (late 2024) lets nodes dynamically decide which node executes next at runtime without pre-defined edges.

**CrewAI Delegation**: Hierarchical process mode auto-generates a manager agent. Strict hub-and-spoke communication avoids peer-to-peer traffic.

### 2.5 Consensus & Conflict Resolution

**Voting vs. Consensus** (ACL 2025): Majority voting outperforms consensus by 13.2% on reasoning tasks (diverse solution paths coexist), but underperforms by 2.8% on knowledge retrieval (agreement catches hallucinations). Optimal strategy is task-dependent.

**Multi-Agent Debate (MAD)**: Agents critique and refine each other's answers iteratively. However, ICLR 2025 found MAD fails to consistently outperform simpler single-agent strategies. Sycophancy causes collapse in 59% of evaluation runs — agents copy answers instead of genuinely deliberating.

**Adaptive Heterogeneous MAD (A-HMAD)**: Extends debate with diverse specialized agents and a consensus optimizer that learns to weight each agent's vote by reliability and argument confidence. 4–6% absolute accuracy gains over standard debate; 30%+ reduction in factual errors.

**Semantic Consensus Framework**: Enterprise multi-agent systems exhibit 41–87% failure rates, with 79% from specification and coordination issues, not model limitations. Root cause: "Semantic Intent Divergence" — cooperating agents develop inconsistent interpretations of shared objectives. Solution: process-aware middleware with conflict detection engine.

### 2.6 The Orchestrator-Worker-Critic Triad

The canonical production pattern (late 2025): Orchestrator breaks down goals → Workers (specialized agents) execute → Critic reviews outputs and triggers retries.

**Anthropic's multi-agent research system**: Claude Opus as lead agent spawns parallel Claude Sonnet subagents, each with own context window and tool access. Outperformed single-agent Opus by 90.2% on research evaluations — but uses ~15× more tokens. Three factors explain 95% of performance variance: token usage alone explains 80%, with tool calls and model choice as the other two. Multi-agent systems work mainly because they spend enough tokens to solve the problem.

### 2.7 Task Decomposition Principles

**AOP (Agent-Oriented Planning, ICLR 2025)**: Three critical design principles:
1. **Solvability**: Each subtask must be within an agent's capability.
2. **Completeness**: Subtasks must cover the full query.
3. **Non-redundancy**: No duplicate work across agents.

**LaMMA-P (ICRA 2025)**: Integrates LLM reasoning with PDDL heuristic search for long-horizon multi-agent tasks. Task Allocator matches actions to agent capabilities for parallel execution.

**Decentralized Two-Layer Architecture (Nature, Nov 2025)**: Heterogeneous LLM agent-executors with adaptive upper-layer controllers using SPSA with consensus under unknown-but-bounded noise. Convergence proof for two-layer decentralized settings.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Model: Multi-Agent Overhead per 1K Tasks

**Assumptions**: Average task at 2K input tokens, 500-token answer per agent. Multi-agent adds inter-agent communication and orchestrator overhead.

| Architecture | Agents/Task | Tokens/Task (total) | Cost/1K Tasks (Sonnet $3/$15) | Cost/1K Tasks (Mixed Cascade) |
|-------------|:-----------:|--------------------:|-----------------------------:|-----------------------------:|
| Single agent (baseline) | 1 | 2,500 | **$13.50** | N/A |
| Supervisor + 2 workers | 3 | 8,500 (3.4×) | **$46.50** | **$22.00** (Haiku workers) |
| Supervisor + 4 workers | 5 | 15,000 (6×) | **$82.50** | **$35.00** (Haiku workers) |
| Orchestrator-Worker-Critic | 3 | 10,000 (4×) | **$55.00** | **$28.00** |
| Anthropic multi-agent research | ~5 | 37,500 (15×) | **$206.25** | **$85.00** (Sonnet subagents) |

> **Model cascading** is the primary cost lever: frontier model for orchestration/planning, smaller models for worker execution, smallest for routing. The plan-and-execute pattern can reduce costs by 90% compared to frontier models everywhere.

**Cost anomaly detection**: Token spend anomalies function as behavioral anomaly detectors. A context window growing 40% over baseline or tool invocations tripling within an hour indicates a replanning loop. Real-time cost anomaly detection catches behavioral failures before users notice quality changes.

### 3.2 Latency SLA Targets

| Architecture | p50 | p95 | p99 | Mitigation |
|-------------|-----|-----|-----|------------|
| Single agent | 1s | 3s | 8s | Streaming; model routing |
| Supervisor + 2 workers (parallel) | 2s | 6s | 15s | Parallel worker dispatch; timeout per agent |
| Supervisor + 4 workers (parallel) | 2.5s | 8s | 20s | Parallel dispatch; cap worker count |
| Chain / Pipeline (3 stages) | 3s | 10s | 25s | Minimize stage count; parallel where possible |
| Debate (3 rounds, 3 agents) | 8s | 25s | 60s | Limit debate rounds; early stopping on consensus |
| Swarm (50+ agents) | 5s | 20s | 60s | Horizontal scaling; async execution |

**p50 mitigation**: Parallel dispatch — all independent agents start simultaneously. Star topology adds only one extra model call (orchestrator) vs. sequential chain adding N calls.
**p95 mitigation**: Per-agent timeout with graceful degradation. If a worker agent doesn't respond within 2× p50, return partial results and note the gap. Straggler agents are the dominant p95 driver.
**p99 mitigation**: Circuit breaker per downstream agent (Section 4.2). Budget enforcer kills tasks exceeding token cap. Hard wall-clock timeout per task.

### 3.3 Throughput & Back-Pressure

**Horizontal scaling**: Treat agents as stateless microservices. Statelessness eliminates synchronization and enables independent scaling per agent type.

**Back-pressure mechanisms**:
- Per-agent token cap: halt agent execution if cumulative tokens exceed budget.
- Per-task cost cap: kill orchestration if total cost exceeds threshold.
- Queue-based admission: if agent pool utilization exceeds 80%, queue new tasks or reject with backoff.
- Model cascade under load: under high throughput, automatically downgrade worker agents from Sonnet → Haiku to maintain throughput at acceptable quality.
- Loop detection: if an agent re-invokes the same tool 3× with identical args, halt and escalate.

### 3.4 RPO/RTO for Multi-Agent Systems

| Component | RPO | RTO | Mechanism |
|-----------|-----|-----|-----------|
| **Agent state (per-agent context)** | Per-checkpoint (every superstep) | <5s (reload from checkpoint store) | LangGraph PostgresSaver / Temporal history |
| **Inter-agent messages** | 0 (logged before delivery) | <1s (replay from message log) | Durable message queue (Redis Streams, Kafka) |
| **Orchestrator plan** | 0 (persisted on creation) | <1s (reload from plan store) | PostgreSQL with WAL replication |
| **Tool call results** | Per-call (logged before processing) | <1s (idempotency key lookup) | Idempotent tool execution + cached results |
| **WORM audit log** | 0 (append-only, replicated) | <1s | S3/GCS with cross-region replication |

**Disaster recovery**: On orchestrator crash, the system reads the plan DAG and checkpoint store. Completed agent tasks are skipped (outputs cached). In-progress agent tasks are retried (agents must be idempotent). The orchestrator reconstructs synthesis from completed outputs and continues with remaining tasks.

**Trade-off — checkpoint frequency**: LangGraph checkpoints every superstep (fine-grained, low RPO, higher I/O overhead). Temporal checkpoints on Activity boundaries (coarser, slightly higher RPO, lower overhead). For 5-agent systems with <50 steps total, the difference is negligible. For 50-agent swarms with 1,500+ tool calls, checkpoint overhead becomes material.

### 3.5 NFR Trade-offs

| NFR | Single Agent | Supervisor (Star) | Chain (Pipeline) | Swarm |
|-----|-------------|-------------------|-----------------|-------|
| **Cost** | 1× (baseline) | 3–6× (inter-agent overhead) | 2–4× (sequential accumulation) | 3–15× (many parallel agents) |
| **Latency** | Lowest | +1 orchestrator call | Linear with stage count | Lowest for parallel work |
| **Availability** | High (single point) | Medium (hub SPOF) | Low (cascade risk) | High (no single point) |
| **Auditability** | Simple trace | Good (orchestrator sees all) | Good (linear trace) | Poor (emergent behavior) |
| **Debuggability** | Trivial | Moderate (distributed traces) | Moderate (stage boundaries) | Difficult (emergent) |

**Key trade-off — when to go multi-agent**: Multi-agent adds 3–15× cost and significant complexity. Justified only when: (a) subtasks require genuinely different tools/models/contexts, (b) parallel execution matters for latency, (c) context isolation prevents contamination between domains, or (d) team-level development requires ownership boundaries. "Start with a single agent and good prompt engineering. Add tools before adding agents."

---

## 4. Distributed Resilience & Security

### 4.1 Common Failure Modes (Three Tiers)

**First-week failures**: Error propagation (one agent's hallucination becomes the next agent's ground truth), state corruption, context exhaustion.

**First-month failures**: Cost explosion, infinite loops, retry complexity under sustained load.

**Silent failures**: Valid HTTP 200 responses with nonsensical or hallucinated content passed between agents — the most common and hardest to detect. Surface-level metrics (CPU, API latency) cannot capture this; requires semantic quality monitoring.

### 4.2 Circuit Breaker Pattern for Multi-Agent Systems

#### 4.2.1 State Machine

```
                    success
              ┌───────────────┐
              │               │
              ▼               │
         ┌─────────┐    ┌────┴─────┐    ┌─────────────┐
         │ CLOSED  │───▶│  OPEN    │───▶│ HALF-OPEN   │
         │         │    │          │    │             │
         │ Normal  │    │ Skip     │    │ Route 2    │
         │ agent   │    │ agent;   │    │ test tasks │
         │ dispatch│    │ use      │    │ to agent   │
         │         │    │ fallback │    │             │
         └─────────┘    └──────────┘    └─────────────┘
              ▲          │       ▲            │
              │          │       │            │
              │          │       └────────────┘
              │          │        probe fails
              │     after 45s
              │     recovery timeout
              │     (45s → 90s → 180s exponential)
              │
              └──────────────────────────────┘
                    2/2 probes succeed
```

**Thresholds**:
- **Closed → Open**: 5 agent failures (timeout, crash, hallucination-detected) within 90s window.
- **Open duration**: 45s initial recovery timeout with exponential backoff (45s → 90s → 180s).
- **Half-Open → Closed**: 2 consecutive successful probe tasks (lightweight test queries).
- **Fallback strategies**: Skip the failed agent and route its subtask to a general-purpose agent; merge the subtask into the orchestrator's context; or return partial results noting the gap.

#### 4.2.2 Per-Component Breaker Applications

| Component | Failure Type | Class | Fallback Strategy |
|-----------|-------------|-------|-------------------|
| Worker agent (API timeout) | LLM provider down | **Transient** | Route to backup model provider; queue for retry |
| Worker agent (hallucination) | Semantic quality failure | **Transient** | Retry with Critic feedback; escalate to human |
| Orchestrator | Planning failure / loop | **Permanent** (design) | Fail entire task; escalate to human with context |
| Inter-agent message | Delivery failure | **Transient** | Retry from durable queue; at-least-once delivery |
| A2A remote agent | Cross-vendor timeout | **Transient** | Fallback to local agent; degrade gracefully |
| Consensus protocol | No convergence after N rounds | **Transient** | Use majority vote of available results; escalate |
| Tool execution | Tool returns null/malformed | **Transient** | Schema-check; retry once; mark step failed |

### 4.3 Failure Taxonomy

| Failure | Class | Detection | Mitigation |
|---------|-------|-----------|------------|
| Error propagation (hallucination cascade) | **Permanent** (design) | Critic agent; semantic quality checks between stages | Independent verification at each stage boundary |
| Silent error (valid response, wrong content) | **Transient** | LLM-judge on output quality; trajectory scoring | Per-agent output validation; Critic review |
| Cost explosion (runaway loop) | **Permanent** (design) | Per-task token counter; cost anomaly detection | Hard token budget; loop detection (3× identical calls) |
| Context exhaustion | **Transient** | Token count approaching context limit | Summarize intermediates; sliding window; context compression |
| Semantic intent divergence (79% of enterprise failures) | **Transient** | Conflict detection engine; divergence metric | Process-aware middleware; goal re-injection per agent |
| Sycophantic debate collapse (59% of MAD runs) | **Permanent** (architecture) | Answer-swap detection; diversity metrics | Heterogeneous agents; A-HMAD weighted voting |
| Agent collusion | **Permanent** (emergent) | Pricing pattern analysis; communication audit | Remove back-channels; randomize agent pairings |
| Mind virus (self-propagating payload) | **Permanent** (attack) | Persistent state audit; anomalous instruction detection | Ephemeral agent state; input sanitization; isolation |

### 4.3.1 Idempotency in Multi-Agent Execution

When an orchestrator crashes after dispatching a subtask but before recording completion, recovery replays the dispatch. Each worker agent's execution must be idempotent:

```
Orchestrator dispatches subtask to Agent B:
                                    │
                          ┌─────────▼──────────┐
                          │ Idempotency Guard   │
                          │ key = hash(task_id  │
                          │   + agent_id        │
                          │   + subtask_index   │
                          │   + tool + args)    │
                          └─────────┬──────────┘
                                    │
                     ┌──────────────▼──────────────┐
                     │ IF key in completed_tasks:   │
                     │   RETURN cached_result        │
                     │ ELSE:                         │
                     │   execute agent               │
                     │   store result with key       │
                     │   mark subtask complete       │
                     └──────────────────────────────┘
```

**Idempotency key formula**: `hash(task_id + agent_id + subtask_index + tool_name + canonical_args)`. The `subtask_index` prevents collisions when the same agent runs multiple subtasks. For non-idempotent side effects (emails, payments), log the call ID before execution and skip on replay.

### 4.3.2 Poison-Pill Detection in Multi-Agent Systems

A poison pill in multi-agent systems is an input that causes cascading failure across agents — a hallucinated intermediate result that corrupts downstream agents, or a prompt injection payload that propagates through handoffs.

**Detection heuristics**:
- An agent's output contains instructions directed at other agents (injection marker).
- Error rate spikes across multiple agents after a single agent's output is propagated.
- Token consumption across the agent pool exceeds 5× baseline without proportional task progress.
- An agent requests tools or permissions outside its declared scope.

**Quarantine flow**:
```
  Agent A output ──▶ ┌────────────────┐
                     │ Quality Gate    │
                     │ - Injection     │ ──(flagged)──▶ ┌──────────────┐
                     │   pattern scan  │                │ Dead Letter  │
                     │ - Schema check  │                │ Queue        │
                     │ - Scope check   │                │ - Persist    │
                     └────────┬───────┘                │ - Alert ops  │
                              │ (passed)                │ - Human      │
                              ▼                         │   review     │
                     Agent B receives                   └──────────────┘
                     sanitized input
```

### 4.4 Enterprise Security Boundaries

#### 4.4.1 Multi-Agent-Specific Threats

**Mind viruses**: Self-propagating payloads designed to spread between AI agents by exploiting persistent state mechanisms. Unlike traditional viruses targeting executable code, these target the "cognitive" instructions of agents, leveraging persistent state files that survive context resets. Defense: ephemeral agent state (no persistent memory between tasks), input sanitization at every agent boundary, isolation between agent instances.

**Prompt injection cascades**: One compromised agent poisoning the entire pipeline. In a star topology, a poisoned worker agent's output flows through the orchestrator to all other agents. Defense: per-agent output validation by an independent Critic agent; schema-check all inter-agent messages; never pass raw agent output as system instructions to another agent.

**Collusion**: In competitive settings, agents immediately collude when given back-channels. Even without communication, they price-match "to the penny via a public listings board" (Anthropic research). Defense: remove back-channels; randomize agent pairings; monitor for pricing pattern convergence.

#### 4.4.2 Zero-Trust Multi-Agent Security

1. **Per-agent tool authorization**: Each agent receives only the tools it needs. A "researcher" agent gets read-only search tools; a "coder" agent gets file-write tools; a "deployer" agent gets deployment tools with human approval gate. Tool scope enforced at the MCP gateway, not by the agent itself.

2. **Inter-agent message validation**: All messages between agents pass through a validation layer that checks for injection patterns, scope violations, and schema conformance. Messages are never passed as system prompts — always as user-role content with clear provenance marking.

3. **Agent identity and provenance**: Each agent has a cryptographic identity. All messages carry sender identity. The orchestrator verifies that responses come from the expected agent, preventing impersonation.

4. **Immutable execution audit**: Every agent action, tool call, inter-agent message, handoff, and consensus round logged to WORM storage with timestamps, agent IDs, and trace IDs. Enables forensic reconstruction of any multi-agent interaction.

5. **Blast radius containment**: Agent failures are isolated — a crashed or compromised agent cannot affect other agents' state. Agents run in separate processes/containers with no shared memory outside explicit communication channels.

---

## 5. Production Enterprise Code

### 5.1 Supervisor Orchestrator with Parallel Worker Dispatch

```python
import asyncio
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Subtask:
    id: str
    description: str
    assigned_agent: str
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    output: Optional[str] = None
    error: Optional[str] = None
    token_count: int = 0


@dataclass
class AgentConfig:
    name: str
    model: str
    system_prompt: str
    tools: list[str]
    max_tokens: int = 4000


class SupervisorOrchestrator:
    def __init__(self, llm_client, agent_configs: dict[str, AgentConfig],
                 checkpointer, max_total_tokens: int = 100_000):
        self.llm = llm_client
        self.agents = agent_configs
        self.checkpointer = checkpointer
        self.max_total_tokens = max_total_tokens
        self.total_tokens = 0
        self.completed_keys: set[str] = set()

    async def run(self, goal: str, session_id: str) -> dict:
        existing = await self.checkpointer.load(session_id)
        if existing:
            subtasks = existing["subtasks"]
            self.completed_keys = set(existing.get("completed_keys", []))
        else:
            subtasks = await self._decompose(goal)
            await self.checkpointer.save(session_id, {
                "subtasks": subtasks, "completed_keys": []
            })

        while True:
            ready = self._get_ready(subtasks)
            if not ready:
                break

            results = await asyncio.gather(
                *[self._execute_agent(st, subtasks) for st in ready],
                return_exceptions=True,
            )

            for subtask, result in zip(ready, results):
                if isinstance(result, Exception):
                    subtask.status = TaskStatus.FAILED
                    subtask.error = str(result)
                else:
                    subtask.output = result
                    subtask.status = TaskStatus.COMPLETED
                    idem_key = self._idempotency_key(subtask)
                    self.completed_keys.add(idem_key)

            await self.checkpointer.save(session_id, {
                "subtasks": subtasks,
                "completed_keys": list(self.completed_keys),
            })

        return await self._synthesize(goal, subtasks)

    async def _decompose(self, goal: str) -> list[Subtask]:
        agent_descriptions = "\n".join(
            f"- {name}: {cfg.system_prompt[:100]}..."
            for name, cfg in self.agents.items()
        )
        response = self.llm.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": (
                f"Decompose this goal into subtasks and assign each to an agent.\n"
                f"Available agents:\n{agent_descriptions}\n\n"
                f"Goal: {goal}\n\n"
                f"Return JSON array: [{{id, description, assigned_agent, depends_on}}]\n"
                f"Maximize parallelism — only add dependencies where data flow requires it."
            )}],
        )
        import json
        return [Subtask(**s) for s in json.loads(response.content[0].text)]

    async def _execute_agent(self, subtask: Subtask,
                             all_subtasks: list[Subtask]) -> str:
        idem_key = self._idempotency_key(subtask)
        if idem_key in self.completed_keys:
            return subtask.output

        if self.total_tokens >= self.max_total_tokens:
            raise RuntimeError(
                f"Token budget exhausted: {self.total_tokens}/{self.max_total_tokens}"
            )

        agent_cfg = self.agents[subtask.assigned_agent]
        dep_context = "\n".join(
            f"Result from '{s.id}': {s.output[:500]}"
            for s in all_subtasks
            if s.id in subtask.depends_on and s.status == TaskStatus.COMPLETED
        )

        response = self.llm.messages.create(
            model=agent_cfg.model,
            max_tokens=agent_cfg.max_tokens,
            system=agent_cfg.system_prompt,
            messages=[{"role": "user", "content": (
                f"Task: {subtask.description}\n\n"
                f"Context from prior steps:\n{dep_context}"
            )}],
        )

        self.total_tokens += response.usage.input_tokens + response.usage.output_tokens
        subtask.token_count = response.usage.input_tokens + response.usage.output_tokens
        return response.content[0].text

    async def _synthesize(self, goal: str, subtasks: list[Subtask]) -> dict:
        completed = [s for s in subtasks if s.status == TaskStatus.COMPLETED]
        failed = [s for s in subtasks if s.status == TaskStatus.FAILED]

        if not failed:
            results_summary = "\n".join(
                f"- {s.id} ({s.assigned_agent}): {s.output[:300]}"
                for s in completed
            )
            response = self.llm.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4000,
                messages=[{"role": "user", "content": (
                    f"Synthesize these agent results into a final answer.\n"
                    f"Original goal: {goal}\n\nResults:\n{results_summary}"
                )}],
            )
            return {
                "status": "completed",
                "answer": response.content[0].text,
                "total_tokens": self.total_tokens,
                "agents_used": list({s.assigned_agent for s in completed}),
            }

        return {
            "status": "partial",
            "completed": [s.id for s in completed],
            "failed": [(s.id, s.error) for s in failed],
            "total_tokens": self.total_tokens,
        }

    def _get_ready(self, subtasks: list[Subtask]) -> list[Subtask]:
        ready = []
        for st in subtasks:
            if st.status != TaskStatus.PENDING:
                continue
            deps_met = all(
                next(s for s in subtasks if s.id == dep).status == TaskStatus.COMPLETED
                for dep in st.depends_on
            )
            if deps_met:
                ready.append(st)
        return ready

    def _idempotency_key(self, subtask: Subtask) -> str:
        return hashlib.sha256(
            f"{subtask.id}:{subtask.assigned_agent}:{subtask.description}".encode()
        ).hexdigest()[:16]
```

### 5.2 Inter-Agent Quality Gate with Injection Detection

```python
import re
from dataclasses import dataclass


@dataclass
class GateResult:
    passed: bool
    sanitized_output: str
    flags: list[str]


INJECTION_PATTERNS = [
    r"(?i)ignore\s+(previous|all|prior)\s+(instructions|prompts)",
    r"(?i)you\s+are\s+now\s+a",
    r"(?i)system:\s*",
    r"(?i)act\s+as\s+(if|though)\s+you",
    r"(?i)disregard\s+(your|the)\s+(instructions|rules|guidelines)",
]


class InterAgentQualityGate:
    def __init__(self, llm_client, max_output_tokens: int = 8000):
        self.llm = llm_client
        self.max_output_tokens = max_output_tokens
        self._compiled_patterns = [re.compile(p) for p in INJECTION_PATTERNS]

    def validate(self, agent_name: str, output: str,
                 expected_schema: dict | None = None) -> GateResult:
        flags = []

        if not output or not output.strip():
            return GateResult(passed=False, sanitized_output="", flags=["empty_output"])

        if len(output.split()) > self.max_output_tokens:
            flags.append("output_exceeds_token_limit")
            output = " ".join(output.split()[:self.max_output_tokens])

        for pattern in self._compiled_patterns:
            if pattern.search(output):
                flags.append(f"injection_pattern_detected: {pattern.pattern[:50]}")

        if expected_schema:
            try:
                import json
                parsed = json.loads(output)
                missing_keys = set(expected_schema.get("required", [])) - set(parsed.keys())
                if missing_keys:
                    flags.append(f"schema_violation: missing {missing_keys}")
            except (json.JSONDecodeError, AttributeError):
                if expected_schema.get("type") == "object":
                    flags.append("schema_violation: expected JSON object")

        injection_detected = any("injection" in f for f in flags)
        if injection_detected:
            return GateResult(
                passed=False,
                sanitized_output="",
                flags=flags,
            )

        return GateResult(
            passed=len(flags) == 0,
            sanitized_output=output,
            flags=flags,
        )
```

### 5.3 A2A-Compatible Agent Card and Discovery

```python
from dataclasses import dataclass, field, asdict
import json


@dataclass
class AgentSkill:
    id: str
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)


@dataclass
class AgentCard:
    name: str
    description: str
    url: str
    version: str
    skills: list[AgentSkill]
    authentication: dict = field(default_factory=lambda: {"schemes": ["bearer"]})
    default_input_modes: list[str] = field(default_factory=lambda: ["text/plain"])
    default_output_modes: list[str] = field(default_factory=lambda: ["text/plain"])

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def matches_task(self, task_description: str) -> float:
        task_lower = task_description.lower()
        score = 0.0
        for skill in self.skills:
            if any(tag in task_lower for tag in skill.tags):
                score += 0.3
            if any(word in task_lower for word in skill.description.lower().split()):
                score += 0.1
        return min(score, 1.0)


class AgentCardRegistry:
    def __init__(self):
        self._cards: dict[str, AgentCard] = {}

    def register(self, card: AgentCard) -> None:
        self._cards[card.name] = card

    def discover(self, task_description: str,
                 min_score: float = 0.2) -> list[tuple[AgentCard, float]]:
        scored = [
            (card, card.matches_task(task_description))
            for card in self._cards.values()
        ]
        return sorted(
            [(card, score) for card, score in scored if score >= min_score],
            key=lambda x: x[1],
            reverse=True,
        )

    def get(self, name: str) -> AgentCard | None:
        return self._cards.get(name)
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Multi-Agent Customer Support Platform

**Business context**: A SaaS company handles 15K support tickets/day across billing, technical, and account management domains. Requirements: <30s first response, 70%+ autonomous resolution rate, seamless escalation to human agents, SOC2 audit trail, and $40K/month AI budget. The company currently has separate teams for each domain with different knowledge bases and tooling.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                     CUSTOMER SUPPORT PIPELINE                            │
 │                                                                          │
 │  Ticket ──▶ ┌──────────────┐  ┌────────────────────────────────────┐    │
 │             │ Triage Agent │  │         SPECIALIST AGENTS          │    │
 │             │ (Haiku)      │  │                                    │    │
 │             │ - Classify   │──▶│  Billing ──▶ Billing Agent        │    │
 │             │ - Route      │  │  Tech    ──▶ Technical Agent       │    │
 │             │ - Priority   │  │  Account ──▶ Account Agent         │    │
 │             │              │  │                                    │    │
 │             └──────────────┘  └────────────────────────────────────┘    │
 │                                         │                               │
 │                              ┌──────────▼──────────┐                    │
 │                              │ Critic Agent        │                    │
 │                              │ - Quality check     │                    │
 │                              │ - Tone validation   │                    │
 │                              │ - Policy compliance  │                    │
 │                              └──────────┬──────────┘                    │
 │                                         │                               │
 │                              ┌──────────▼──────────┐                    │
 │                              │ Response Delivery    │                    │
 │                              │ - Auto-send (Tier 1) │                    │
 │                              │ - Human review       │                    │
 │                              │   (Tier 2+)          │                    │
 │                              │ - Escalation (Tier 3)│                    │
 │                              └─────────────────────┘                    │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Single Agent with All Tools | B: Supervisor + Specialist Workers (Recommended) | C: Peer-to-Peer Agent Mesh |
|-----------|-------------------------------|------------------------------------------------|---------------------------|
| **Autonomous resolution rate** | ⬛⬛⬜ — 55% (context overload from all domains) | ⬛⬛⬛ — 73% (specialized agents with focused context) | ⬛⬛⬜ — 60% (coordination overhead reduces quality) |
| **First response latency** | ⬛⬛⬛ — <2s (single call) | ⬛⬛⬜ — <5s (triage + specialist + critic = 3 calls) | ⬛⬜⬜ — 8–15s (agents negotiate who handles) |
| **Cost at 15K tickets/day** | ⬛⬛⬛ — ~$18K/month (single Sonnet call) | ⬛⬛⬛ — ~$32K/month (Haiku triage + Sonnet specialists + Haiku critic) | ⬛⬜⬜ — ~$55K/month (inter-agent comm overhead) |
| **Auditability** | ⬛⬛⬜ — Single trace, hard to isolate domain reasoning | ⬛⬛⬛ — Per-domain traces; clear responsibility boundaries | ⬛⬜⬜ — Distributed traces across N agents |
| **Scalability per domain** | ⬛⬜⬜ — Must redeploy entire agent for any domain change | ⬛⬛⬛ — Update billing agent without touching others | ⬛⬛⬛ — Independent agent deployment |
| **Operational complexity** | ⬛⬛⬛ — One agent, one prompt, one model | ⬛⬛⬜ — 5 agents, distributed tracing, circuit breakers | ⬛⬜⬜ — N² communication paths, emergent behavior |

**Recommended approach**: **B (Supervisor + Specialist Workers)**.

**Decision rationale**: The 70%+ autonomous resolution requirement eliminates Option A — a single agent with all three domain tool sets suffers context overload, achieving only ~55% resolution in testing. The $40K/month budget eliminates Option C — peer-to-peer mesh inter-agent communication overhead pushes cost to ~$55K/month. Supervisor + specialists achieves 73% resolution by giving each specialist a focused context window with domain-specific tools and knowledge base access. The Haiku triage agent (classification at <$1K/month) routes to Sonnet specialists, keeping total cost at ~$32K. The Critic agent catches tone/policy violations before customer delivery. Per-domain traces satisfy SOC2 auditors who need to see exactly which agent made which decision.

### 6.2 Scenario: Multi-Agent Code Migration System

**Business context**: An enterprise is migrating 2,500 microservices from Java 11 to Java 21 over 12 months. Each migration involves dependency updates, API changes, build system modifications, test suite updates, and validation. Requirements: 80% automated migration rate, zero production regressions, human review for all PRs, $100K/month AI budget, and progress tracking across the fleet.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                     MIGRATION PIPELINE (per service)                     │
 │                                                                          │
 │  Repo ──▶ ┌──────────────┐ ──▶ ┌──────────────┐ ──▶ ┌────────────┐    │
 │           │ Analyzer     │     │ Migrator     │     │ Validator  │    │
 │           │ Agent        │     │ Agent        │     │ Agent      │    │
 │           │              │     │              │     │            │    │
 │           │ - Dependency │     │ - Apply code │     │ - Build    │    │
 │           │   scan       │     │   transforms │     │ - Test     │    │
 │           │ - API compat │     │ - Update     │     │ - Security │    │
 │           │   check      │     │   build files│     │   scan     │    │
 │           │ - Risk score │     │ - Fix        │     │ - Diff     │    │
 │           │              │     │   deprecations│     │   review   │    │
 │           └──────────────┘     └──────────────┘     └──────┬─────┘    │
 │                                                            │          │
 │                                                 ┌──────────▼───────┐  │
 │                                                 │ PR Agent         │  │
 │                                                 │ - Create PR      │  │
 │                                                 │ - Write summary  │  │
 │                                                 │ - Tag reviewers  │  │
 │                                                 │ - Track status   │  │
 │                                                 └──────────────────┘  │
 │                                                                        │
 │  ┌──────────────────────────────────────────────────────────────────┐  │
 │  │  FLEET ORCHESTRATOR                                              │  │
 │  │  - Prioritize services by risk score                             │  │
 │  │  - Parallel pipeline execution (10 services concurrently)        │  │
 │  │  - Progress dashboard (completed / in-progress / blocked)        │  │
 │  │  - Rollback coordination on regression                           │  │
 │  └──────────────────────────────────────────────────────────────────┘  │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Single Agent per Service (Full Pipeline) | B: Chain of Specialist Agents per Service (Recommended) | C: Swarm (All Services Simultaneously) |
|-----------|---------------------------------------------|--------------------------------------------------------|---------------------------------------|
| **Automated migration rate** | ⬛⬛⬜ — 65% (context overload on complex services) | ⬛⬛⬛ — 82% (specialists handle each phase optimally) | ⬛⬛⬜ — 70% (emergent coordination errors) |
| **Regression safety** | ⬛⬛⬜ — Single agent may skip validation | ⬛⬛⬛ — Dedicated Validator agent with independent build/test/scan | ⬛⬜⬜ — Validation bottleneck across 2,500 services |
| **Cost per service** | ⬛⬛⬛ — ~$15 (single long context call) | ⬛⬛⬜ — ~$35 (4 agent calls × ~$8.75 avg) | ⬛⬜⬜ — ~$60 (coordination overhead + redundant analysis) |
| **Total fleet cost (2,500 services)** | ⬛⬛⬛ — ~$37.5K | ⬛⬛⬛ — ~$87.5K (within $100K/month budget) | ⬛⬜⬜ — ~$150K (exceeds budget) |
| **Debuggability on failure** | ⬛⬜⬜ — One monolithic trace; hard to identify which phase failed | ⬛⬛⬛ — Per-phase traces; clear stage boundary for failure isolation | ⬛⬜⬜ — Emergent behavior difficult to trace |
| **Throughput** | ⬛⬛⬜ — 10 services/day (sequential phases per service) | ⬛⬛⬛ — 15 services/day (parallel pipeline stages across services) | ⬛⬛⬛ — 20+ services/day (massive parallelism) |

**Recommended approach**: **B (Chain of Specialist Agents per Service)**.

**Decision rationale**: The zero-regression requirement is the hard constraint. Option A's single agent handling the full pipeline lacks independent validation — it may skip or superficially run tests. The chain approach (Analyzer → Migrator → Validator → PR Agent) provides an independent Validator agent that builds, tests, and security-scans with no shared context with the Migrator — eliminating "ceremonialization" where the same agent claims to have validated its own work. At ~$87.5K for the full fleet, it stays within the $100K budget. Option C (swarm) exceeds budget and introduces emergent coordination errors that risk regressions. The Fleet Orchestrator processes 10 services concurrently through the 4-stage pipeline, completing ~15 services/day (all 2,500 in ~167 days, well within the 12-month window). Per-phase traces make it easy to identify why a specific migration failed — was it an unresolvable dependency (Analyzer), a code transform error (Migrator), a test failure (Validator), or a PR creation issue?

---

*Module 09 complete. Covers multi-agent topologies, communication patterns, the MCP/A2A/AG-UI protocol stack, coordination algorithms, framework comparison, and production deployment with fault tolerance, cost management, and security boundaries.*
