# Module 09: Multi-Agent Orchestration

> **Audience**: Principal Data Scientist (12+ YOE) preparing for Director/VP-level AI roles.
> **Scope**: End-to-end architecture of multi-agent systems -- orchestration topologies, typed state handoffs, inter-agent communication protocols (A2A, MCP), consensus mechanisms, token economics, distributed resilience (supervisor trees, circuit breakers), enterprise security (OWASP Agentic Top 10, agent identity, audit trails), and production system design.
> **Pricing assumptions**: Claude Sonnet 4 input $3/1M tokens, output $15/1M; Claude Opus 4 input $15/1M, output $75/1M; GPT-4o input $2.50/1M, output $10/1M; GPT-4.1-mini input $0.40/1M, output $1.60/1M. Prompt caching reduces input cost by 90% for cached prefixes. All as of mid-2026.
> **Key references**: Focused.io supervisor vs. swarm benchmarks (July 2026), OWASP Top 10 for Agentic Applications (Dec 2025), Zylos Research self-healing/failure-recovery (May 2026), MAST Failure Taxonomy (NeurIPS 2025), ICLR MAD benchmarks (2025), A2A Protocol v1.0 (early 2026, Linux Foundation), EU AI Act full enforcement (Aug 2026).

---

## 1. System Topology & Data Flow

### 1.1 Supervisor (Hub-and-Spoke)

A central orchestrator classifies intent, routes to specialized workers, and regains control after each worker response. Every interaction passes through the supervisor -- minimum 2 LLM calls per domain. LangGraph implements this via `create_supervisor()` with `llm.with_structured_output(RoutingDecision)` producing Pydantic-typed routing decisions with `next_agent` and `reasoning` fields.

Benchmarks (Focused.io, July 2026): 94% routing accuracy, ~4.2s single-domain latency, ~9.1s with handoffs, ~2,800 tokens per request.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          SUPERVISOR PATTERN                                  │
│                                                                              │
│                         CONTROL PLANE                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                     ┌──────────────────┐                            │    │
│  │                     │   SUPERVISOR      │                            │    │
│  │                     │                  │                            │    │
│  │                     │  - Intent class. │                            │    │
│  │                     │  - Route decision│                            │    │
│  │                     │  - Loop guard    │                            │    │
│  │                     │  - Final synth.  │                            │    │
│  │                     └───────┬──────────┘                            │    │
│  │                ┌───────────┼───────────┐                            │    │
│  │                v           v           v                            │    │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐               │    │
│  │  │  Worker A     │ │  Worker B     │ │  Worker C     │               │    │
│  │  │  (Billing)    │ │  (Shipping)   │ │  (Tech Supp.) │               │    │
│  │  │              │ │              │ │              │               │    │
│  │  │  Tools:      │ │  Tools:      │ │  Tools:      │               │    │
│  │  │  - invoice   │ │  - tracking  │ │  - kb_search │               │    │
│  │  │  - refund    │ │  - carrier   │ │  - diag_run  │               │    │
│  │  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘               │    │
│  │         │                │                │                        │    │
│  └─────────┼────────────────┼────────────────┼────────────────────────┘    │
│            │                │                │                              │
│  ┌─────────v────────────────v────────────────v────────────────────────┐    │
│  │                        DATA PLANE                                  │    │
│  │                                                                    │    │
│  │  TypedState {                                                      │    │
│  │    messages: List[BaseMessage]          (append-only reducer)       │    │
│  │    current_agent: str                  (routing target)            │    │
│  │    resolution_notes: List[str]         (operator.add reducer)      │    │
│  │    handoff_count: int                  (loop guard, max=25)        │    │
│  │  }                                                                 │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │                     PERSISTENCE LAYER                              │    │
│  │                                                                    │    │
│  │  PostgresSaver / SQLiteSaver                                       │    │
│  │  - Thread-scoped checkpoints (snapshot per step)                   │    │
│  │  - Immutable state versions (no in-place mutation)                 │    │
│  │  - Time-travel debugging (replay any checkpoint)                   │    │
│  │  - HITL pause/resume via interrupt()                               │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │                     TELEMETRY                                      │    │
│  │                                                                    │    │
│  │  - Per-agent latency (target: <5s single-domain, <10s handoff)     │    │
│  │  - Routing accuracy (target: >93%)                                 │    │
│  │  - Token consumption per request (baseline: ~2,800)                │    │
│  │  - Handoff count distribution (alert if >5% hit recursion limit)   │    │
│  │  - Agent error rate, tool call success rate                        │    │
│  │  - Sinks: Datadog / CloudWatch / OpenTelemetry                    │    │
│  └────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Request flow (supervisor)**:

```
User Query: "I need a refund for order #4521"
    │
    v
┌──────────┐  1. Classify intent     ┌──────────────┐
│  User    │ ──────────────────────>  │  Supervisor   │
│          │                          │              │
│          │                          │  LLM call 1: │
│          │                          │  route to     │
│          │                          │  Billing      │
│          │                          └──────┬───────┘
│          │                                 │
│          │                                 v  2. Delegated work
│          │                          ┌──────────────┐
│          │                          │  Billing     │
│          │                          │  Worker      │
│          │                          │              │
│          │                          │  LLM call 2: │
│          │                          │  lookup order │
│          │                          │  process refund│
│          │                          └──────┬───────┘
│          │                                 │
│          │                                 v  3. Return to supervisor
│          │                          ┌──────────────┐
│          │  4. Synthesized response  │  Supervisor   │
│          │ <────────────────────────│              │
│          │                          │  LLM call 3: │
│          │     Total: 3 LLM calls   │  synthesize  │
└──────────┘     ~4.2-9.1s latency    └──────────────┘
```

### 1.2 Hierarchical (Supervisor-of-Supervisors)

A tree of supervisors, each managing a team of specialists. Default recursion limit 25 for 4-specialist teams, 40 for hierarchical. Under single-agent failure, hierarchical degrades only 5.5% vs. 23% for linear pipelines and 31% for flat swarms.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                     HIERARCHICAL PATTERN                                     │
│                                                                              │
│                    ┌───────────────────┐                                     │
│                    │  ROOT SUPERVISOR   │                                     │
│                    │  (strategic layer) │                                     │
│                    └────────┬──────────┘                                     │
│               ┌─────────────┼─────────────┐                                 │
│               v             v             v                                 │
│    ┌──────────────┐ ┌──────────────┐ ┌──────────────┐                      │
│    │  TEAM SUP. A  │ │  TEAM SUP. B  │ │  TEAM SUP. C  │                      │
│    │  (Sales)      │ │  (Support)    │ │  (Ops)        │                      │
│    └──────┬───────┘ └──────┬───────┘ └──────┬───────┘                      │
│      ┌────┼────┐      ┌────┼────┐      ┌────┼────┐                         │
│      v    v    v      v    v    v      v    v    v                         │
│     W1   W2   W3    W4   W5   W6    W7   W8   W9                         │
│                                                                              │
│  ESCALATION PATH:                                                           │
│  Worker W5 fails -> Team Sup. B retries with W4 or W6                       │
│  Team Sup. B exhausts retries -> Root Supervisor reassigns to Team C        │
│  Root Supervisor fails -> system halt + alert (SPOF mitigation: hot standby)│
│                                                                              │
│  DEGRADATION BENCHMARKS:                                                    │
│  ┌──────────────────┬──────────────────────────┐                            │
│  │ Topology          │ Degradation on 1 failure  │                            │
│  ├──────────────────┼──────────────────────────┤                            │
│  │ Hierarchical      │ 5.5%                      │                            │
│  │ Linear Pipeline   │ 23%                       │                            │
│  │ Flat Swarm        │ 31%                       │                            │
│  └──────────────────┴──────────────────────────┘                            │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 1.3 Swarm (Peer-to-Peer Handoff)

No central router. Agents hand off directly to peers using `Command(goto=target, graph=Command.PARENT)`. Each specialist holds its own tools plus handoff tools for every peer. Benchmarks: 91% routing accuracy, ~2.8s single-domain, ~5.4s with handoffs, ~1,900 tokens/request -- 32% fewer tokens than supervisor but 3% lower routing accuracy.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          SWARM PATTERN                                       │
│                                                                              │
│     ┌─────────┐  handoff   ┌─────────┐  handoff   ┌─────────┐             │
│     │ Agent A  │<─────────>│ Agent B  │<─────────>│ Agent C  │             │
│     │(Billing) │           │(Shipping)│           │(Returns) │             │
│     └────┬────┘           └────┬────┘           └────┬────┘             │
│          │                     │                     │                     │
│          └─────────────────────┼─────────────────────┘                     │
│                                │                                            │
│                           ┌────v────┐                                      │
│                           │ Agent D  │                                      │
│                           │(Tech Sup)│                                      │
│                           └─────────┘                                      │
│                                                                              │
│  Communication: Command(goto="agent_b", graph=Command.PARENT)              │
│  State: each agent sees full conversation + can write to shared state       │
│  No SPOF: any agent can be entry point                                      │
│  Risk: harder to observe/debug, lower routing accuracy (91% vs 94%)         │
│                                                                              │
│  DATA FLOW:                                                                 │
│  User -> Agent A (handles billing) -> realizes shipping question             │
│       -> Command(goto="Agent B") -> Agent B resolves -> returns to user      │
│  Total: 2 LLM calls (vs. supervisor's 3-4)                                 │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 1.4 Pipeline (Sequential)

Deterministic linear execution. Agent A -> B -> C with no branching. Implemented natively by CrewAI Sequential process and Google ADK `SequentialAgent`. Ideal for fixed-order workflows (research -> write -> review).

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          PIPELINE PATTERN                                    │
│                                                                              │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐          │
│  │ Stage 1   │────>│ Stage 2   │────>│ Stage 3   │────>│ Stage 4   │          │
│  │ Research  │     │ Draft     │     │ Review    │     │ Publish   │          │
│  │           │     │           │     │           │     │           │          │
│  │ Output:   │     │ Input:    │     │ Input:    │     │ Input:    │          │
│  │ findings  │     │ findings  │     │ draft     │     │ approved  │          │
│  │ (struct.) │     │ Output:   │     │ Output:   │     │ draft     │          │
│  │           │     │ draft     │     │ approved  │     │ Output:   │          │
│  │           │     │           │     │ or reject │     │ published │          │
│  └──────────┘     └──────────┘     └──────────┘     └──────────┘          │
│                                                                              │
│  RELIABILITY: 10 stages at 85%/stage = ~20% end-to-end success              │
│  MITIGATION: validation gates between stages, retry with fallback model     │
│                                                                              │
│  PARALLEL FAN-OUT/FAN-IN (variant):                                         │
│                                                                              │
│                    ┌──────────┐                                              │
│                    │ Dispatch  │                                              │
│                    └────┬─────┘                                              │
│              ┌──────────┼──────────┐                                        │
│              v          v          v                                        │
│       ┌──────────┐ ┌──────────┐ ┌──────────┐                              │
│       │ Worker 1  │ │ Worker 2  │ │ Worker 3  │    (concurrent)             │
│       └────┬─────┘ └────┬─────┘ └────┬─────┘                              │
│            └─────────────┼───────────┘                                      │
│                     ┌────v─────┐                                            │
│                     │ Reducer   │  Deterministic merge function              │
│                     │ (merge)  │  resolves parallel state updates            │
│                     └──────────┘                                            │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 1.5 Topology Selection Decision Tree

```
Is the task order fixed?
├── YES ──> Pipeline (Sequential)
└── NO
    ├── Are agents peers with equal authority?
    │   ├── YES ──> Swarm (ensure observability)
    │   └── NO
    │       ├── >6 workers?
    │       │   ├── YES ──> Hierarchical
    │       │   └── NO ──> Supervisor
    │       └── Need iterative refinement?
    │           └── YES ──> Reflection Loop
    │
    └── <3 distinct domains? ──> Skip multi-agent entirely.
        Single agent with tools is cheaper and simpler.
```

2026 consensus: the burden of proof is on multi-agent. At equal token budgets, single-agent matches or beats multi-agent on reasoning tasks. Multi-agent earns its complexity only when each role has a narrow responsibility, task routing is observable, and outputs are validated before downstream consumption.

---

## 2. Core Mechanics & Algorithms

### 2.1 Five Orchestration Topologies -- Trade-off Summary

| Topology | Routing Accuracy | Latency (single) | Latency (handoff) | Tokens/req | Failure Degradation | Best For |
|----------|-----------------|-------------------|--------------------|-----------|--------------------|----------|
| Supervisor | 94% | ~4.2s | ~9.1s | ~2,800 | SPOF risk | Dynamic routing, audit trails |
| Swarm | 91% | ~2.8s | ~5.4s | ~1,900 | 31% on 1 failure | Peer-equal agents, latency-sensitive |
| Hierarchical | ~94% (layered) | ~5s | ~12s | ~3,500 | 5.5% on 1 failure | 6+ workers, resilience-critical |
| Pipeline | 100% (deterministic) | sum(stages) | N/A | sum(stages) | 23% on 1 failure | Fixed-order workflows |
| Parallel Fan-Out | 100% (deterministic) | max(workers) | N/A | sum(workers) | depends on reducer | Independent subtasks |

### 2.2 Typed State Handoffs Across Frameworks

**LangGraph**: State is a typed schema extending `MessagesState` with fields like `current_agent`, `resolution_notes` (using `operator.add` reducer so multiple agents append without clobbering), and `handoff_count` for loop guards. Immutable state management -- each update creates a new version. Custom handoff tools use `InjectedState` and `InjectedToolCallId` annotations.

**OpenAI Agents SDK** (v0.17.1, 26K+ stars): Three primitives -- Agents (LLM + instructions + tools), Handoffs (tool call returning another Agent, runner switches `active_agent`), Guardrails (input/output validators). `agent.asTool()` keeps the calling agent responsible vs. full handoff where the specialist owns the next response. Shared conversation history persists across handoffs.

**Google ADK** (~20K stars): Tree hierarchy with two rules -- parent manages sub-agents, each agent has exactly one parent. `LlmAgent` uses AutoFlow (LLM-driven delegation based on description fields). Three workflow agents: `SequentialAgent`, `ParallelAgent`, `LoopAgent`. Sub-agent (permanent hierarchy member) vs. `AgentTool` (external consultant called on demand). Communication through shared session state (a "digital whiteboard").

**CrewAI**: Role-based DSL with Agent (role, goal, backstory) + Task + Tool + Crew. Two orchestration layers -- Crews (autonomous collaboration, stateless by default) and Flows (event-driven with `@start`, `@listen`, `@router` decorators providing state threading, persistence, branching). Flows wrap Crews for production-grade control. Sequential and Hierarchical process types.

**AutoGen / Microsoft Agent Framework**: AutoGen treated conversation history as state. AssistantAgent + UserProxyAgent, now in maintenance mode. Successor Microsoft Agent Framework (MAF, GA April 2026) shifts to explicit graph-based Workflows with typed nodes and edges.

### 2.3 Agent Communication Protocols

**Agent-to-Agent Protocol (A2A)**: Google-introduced (April 2025), Linux Foundation governance, v1.0 early 2026, 150+ organizations. Agents advertise capabilities via agent cards, exchange tasks via JSON-RPC over HTTPS, stream updates via Server-Sent Events. Supports negotiation, delegation, coordination. Security via cryptographic signing and role-based routing.

**Model Context Protocol (MCP)**: Anthropic-created. Standardizes agent-to-tool and agent-to-data connections. Client-server design with schema consistency, access control, auditability. Complementary to A2A: "MCP for tool access, A2A for peer collaboration" -- the dual foundation of agent communication.

**Communication patterns**:
- **Message passing**: A2A (JSON-RPC), OpenAI SDK handoffs (tool call returning Agent)
- **Shared state**: LangGraph typed state with reducers, ADK session state ("digital whiteboard")
- **Blackboard**: Agents write results to a common object accessible by all (implicit in ADK shared state)

### 2.4 Consensus Mechanisms

Multi-agent debate (MAD) has been extensively benchmarked with mixed results:

**ICLR 2025 findings** (5 MAD frameworks vs. CoT and Self-Consistency):
- MAD does not consistently outperform single-agent strategies
- GPT-4o-mini: CoT scored 80.73% on MMLU vs. best MAD at 80.40%
- Self-Consistency scored 95.67% on GSM8k vs. best MAD at 94.93%
- Exceptions: Exchange-of-Thoughts on MATH (75.93% vs. 72.87% CoT), AgentVerse on HumanEval (85.37% vs. 78.05% CoT)
- MAD is "overly aggressive" -- frequently flips correct answers to incorrect ones
- Increasing rounds or agent count does not reliably improve accuracy
- AgentVerse collapsed to 5.47% on GSM8k with Llama 3.1-8b (strict formatting failures)

**Mixed-model configurations show promise**: GPT-4o-mini + Llama 3.1-70b yielded 95.00% on GSM8k and 88.20% on MMLU -- structural decorrelation of blind spots.

**2025 consensus**: Majority voting is optimal for reasoning tasks; consensus is optimal for knowledge tasks. ReConcile (ACL 2024) implements round-table conference with discussion and voting. MDAgents (NeurIPS 2024) adapts collaboration structure to task complexity.

### 2.5 Human-in-the-Loop & Escalation

**Three core approval modes**:
1. **Pre-action approval**: Human must approve before anything external happens
2. **Escalation-on-uncertainty**: Agent runs until hitting a confidence threshold
3. **Post-action audit**: Agent acts, records results for sampling and correction

Most production systems need all three.

**Gate decision variables**: Reversibility, Blast radius, Confidence threshold. If any two are elevated, add a gate.

**Risk-tiered action classification**:

| Tier | Action Type | Approval | Example |
|------|-------------|----------|---------|
| 1 | Read-only | Autonomous | Database query, log search |
| 2 | Reversible | Notify | Draft email, create ticket |
| 3 | External | Pre-approve | Send email, API call to partner |
| 4 | High-risk / Irreversible | Mandatory human | Financial transaction, production deploy |

**Tiered escalation SLAs**:

| Tier | Trigger | SLA | Escalation Target |
|------|---------|-----|--------------------|
| 1 | Confidence 0.6-0.8 | 4 hours | Team member |
| 2 | Confidence <0.6 or high blast radius | 1 hour | Team lead |
| 3 | Compliance/legal/critical infrastructure | 15 minutes | Designated authority (auto-page) |

**Confidence compounding problem**: Miscalibration compounds across chains. Three agents each off by ~15pp: claimed 90% per-step confidence implies only ~42% probability all three steps are correct. RLHF-trained models tend to express highest confidence on incorrect outputs -- claimed 90% can correspond to ~75% real-world accuracy.

**Framework HITL support**:
- LangGraph: `interrupt()` primitive (Jan 2025, superseded `NodeInterrupt`) pauses execution, returns payload to caller
- Google ADK: Pause for human input anywhere, restore state on resume
- AWS Bedrock AgentCore (Oct 2025): Managed orchestration with access management

### 2.6 Framework Comparison

| Aspect | LangGraph | OpenAI Agents SDK | CrewAI | Google ADK | AutoGen/MAF |
|--------|-----------|-------------------|--------|------------|-------------|
| **Orchestration** | Graph-based state machine | Single-agent loop + handoffs | Role-based crews + Flows | Tree hierarchy + workflow agents | Conversation -> Graph (MAF) |
| **State** | Typed, checkpointed, immutable | Shared conversation + Sessions | Task outputs (Crews) + event-sourced (Flows) | Shared session state | Conversation history -> typed nodes |
| **Communication** | Shared state + handoff tools | Handoff = tool returning Agent | Sequential task output passing | Shared state + LLM delegation | Natural language messages |
| **HITL** | `interrupt()` | `execute_tools=False` | Callbacks, triggers | Pause/resume anywhere | `human_input_mode` |
| **Model support** | Any LLM | 100+ via LiteLLM | Fully agnostic | Gemini-optimized, others via LiteLLM | Any LLM |
| **Speed vs. accuracy** | 62% complex task success | Production (v0.17.1) | 5.76x faster, 54% complex success | Production (v1.x) | Maintenance (use MAF) |
| **Maturity** | Production-grade | Production | Production (Flows) | Production (ADK 2.0 in dev) | Maintenance mode |

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Formulas

**Base agentic multiplier**: Agentic workflows consume 3-10x more tokens than simple chat completions. A single user request can trigger 10-20 LLM calls (planning, tool selection, execution, verification, response generation). Gartner (March 2026): agentic models require 5-30x more tokens per task than a standard chatbot.

**Multi-agent multiplier**: Multi-agent systems use ~15x more tokens than standard chat interactions. The coordination overhead -- tokens for inter-agent messages, state synchronization, consensus -- adds a multiplier on top of the base agent cost.

**Cost per topology** (worked example, single customer service request):

| Component | Supervisor | Swarm | Single Agent |
|-----------|-----------|-------|-------------|
| Tokens per request | 2,800 | 1,900 | 900 |
| LLM calls per request | 3-4 | 2 | 1 |
| Cost at Sonnet 4 ($3/$15 per 1M) | ~$0.042 input + output | ~$0.029 | ~$0.014 |
| Cost at GPT-4.1-mini ($0.40/$1.60) | ~$0.0045 | ~$0.003 | ~$0.0014 |
| At 10K requests/day (Sonnet 4) | ~$420/day | ~$290/day | ~$140/day |
| At 10K requests/day (4.1-mini) | ~$45/day | ~$30/day | ~$14/day |

**Unconstrained agent cost**: An unconstrained agent solving a software engineering task can cost $5-8 per task in API fees alone.

### 3.2 The Re-Sent Context Problem

The single biggest invisible cost in agentic systems. Re-sent context (system prompts, tool definitions, state history repeated across multiple calls in the same workflow) accounts for **62% of total agent inference bills** (Stanford Digital Economy Lab). Enterprise LLM spending reached $8.4B in H1 2025, with ~40% of enterprises spending >$250K annually and 96% reporting costs exceeding initial projections.

**Worked example -- re-sent context cost**:

```
System prompt + tool schemas: ~4,000 tokens (sent every LLM call)
Conversation history growth: ~500 tokens/turn
5-turn agent workflow = 5 calls

Call 1: 4,000 + 0 = 4,000 input tokens
Call 2: 4,000 + 500 = 4,500
Call 3: 4,000 + 1,000 = 5,000
Call 4: 4,000 + 1,500 = 5,500
Call 5: 4,000 + 2,000 = 6,000
                         ------
Total input:             25,000 tokens

Without re-sent context: 4,000 + 2,500 = 6,500 tokens
Re-sent context overhead: 18,500 tokens (74% of total)

At Opus 4 ($15/1M input): $0.375 actual vs. $0.098 minimal = 3.8x waste
At Sonnet 4 ($3/1M input): $0.075 actual vs. $0.020 minimal = 3.8x waste
```

### 3.3 The Falling Price, Rising Bill Paradox

Token prices fell ~80% between 2025 and 2026, yet enterprise LLM API spend passed $8.4B in 2025 and is on track to double again. Weekly token processing volume on OpenRouter surged from 0.4 trillion (Dec 2024) to 27.0 trillion (March 2026) -- a 68x increase in 15 months. Gartner forecasts 40% of AI agent projects will be cancelled by 2027 due to cost overruns alone.

### 3.4 Latency SLA Targets

| Pattern | Latency Profile | Notes |
|---------|----------------|-------|
| Single LLM call | ~800ms | Baseline |
| Supervisor (single domain) | ~4.2s | 2 LLM calls minimum |
| Supervisor (with handoffs) | ~9.1s | 3-4 LLM calls |
| Swarm (single domain) | ~2.8s | 1 LLM call after triage |
| Swarm (with handoffs) | ~5.4s | 2 LLM calls |
| Orchestrator-Worker + Reflexion | 10-30s | Iterative refinement |
| Pipeline (N stages) | N * ~2-4s | Sequential, no parallelism |
| Parallel fan-out (N workers) | max(workers) * ~2-4s | Bounded by slowest worker |

Without multi-turn reasoning, accuracy on complex tasks plateaus at ~60-70%. Achieving the 95%+ accuracy required for enterprise processes demands longer thinking -- multi-agent systems are inherently slower but more accurate for complex tasks.

### 3.5 Runaway Cost Incidents

In November 2025, two LangChain-based agents entered an infinite conversation cycle that ran for 11 days, generating a **$47,000 bill** before detection. IAL-Scan analysis of 6,549 LLM agent repositories found 68 confirmed infinite loop failures across 47 projects (91.9% precision).

### 3.6 Key Optimization Strategies

**1. Prompt caching**: 90% reduction on cached input tokens. ProjectDiscovery raised cache hit rate from 7% to 84%, cutting LLM spend by 59-70%.

**2. Model routing**: 100-300x cost differential between premium and small model tiers. Dynamic model selection based on complexity signals. Paul Gauthier demonstrated DeepSeek R1 (reasoning) + Claude Sonnet (editing) hitting SOTA results at 14x less cost than OpenAI o1 alone.

**3. Structured memory handoff**: Agents pass structured summaries, not full conversation histories. Downstream agents need conclusions and key data points, not the reasoning trail.

**4. Combined optimization**: Full stack (model routing + caching + prompt compression + batch scheduling + budget governance) consistently yields 60-80% spend reduction without quality loss.

**Cost optimization decision matrix**:

| Strategy | Implementation Effort | Cost Reduction | Quality Impact |
|----------|----------------------|---------------|---------------|
| Prompt caching | Low | 59-70% | None |
| Model routing | Medium | 70-90% | Minimal if well-calibrated |
| Structured summaries | Medium | 40-60% | Risk of information loss |
| Step/token limits | Low | Variable | Hard ceiling prevents runaway |
| Batch scheduling | Medium | 20-30% | Adds latency |
| Combined | High | 60-80% | None if properly tuned |

### 3.7 Diminishing Returns Analysis

At equal token budgets, single-agent matches or beats multi-agent on reasoning. The primary engineering question is not "can we add more agents?" but "does the marginal agent's contribution exceed its coordination cost?"

**When multi-agent hurts**:
- Each additional agent adds coordination overhead (inter-agent messages, state sync, consensus tokens)
- MAD frequently flips correct answers to incorrect ones
- AgentVerse collapsed to 5.47% accuracy on GSM8k with Llama 3.1-8b due to formatting cascades
- 5 agents at 95% individual accuracy yield only ~77% end-to-end success

**When multi-agent wins**:
- Each role has narrow, non-overlapping responsibility
- Structural decorrelation (different models) catches blind spots
- Task requires tools from different security domains
- Human review bottleneck is the constraint (parallel specialist analysis)

---

## 4. Distributed Resilience & Security

### 4.1 State Management

**LangGraph checkpointing**: Snapshot of graph state at every step, organized by thread. Built-in persistent checkpointers (`PostgresSaver`, `SQLiteSaver`) enable fault-tolerant resume, conversation memory, time-travel debugging, and HITL pauses. Checkpoints are thread-scoped for natural isolation. Crashed workflows resume from last checkpoint via `thread_id`.

**Event sourcing**: Every state change recorded as an immutable event that can be replayed to reconstruct any previous state. CrewAI Flows use event-driven state with `@start`, `@listen`, `@router` decorators. Enterprise orchestration separates operational state (checkpoints, progress, activity logs) from knowledge state (contextual, domain-specific data).

**Parallel merge**: When parallel branches update the same key, a reducer function defines exactly how updates combine -- avoiding race conditions by design rather than by locking. Agents fork from a shared checkpoint, work independently, merge results back.

**Checkpointing strategies**:
1. **Complete state snapshots**: Capture everything (agent states, contexts, intermediate data, system state)
2. **Clean breakpoints**: Pauses only at predefined checkpoints, preventing mid-operation interruptions

Long-running workflows need durable state that outlives a failed session. If a five-step workflow loses context at step four, it must resume from that point -- not restart and rebill for completed work.

### 4.2 Fault Tolerance: Supervisor Trees (Erlang/OTP-Inspired)

Hierarchical process supervision with three restart strategies:

| Strategy | Behavior | Use Case |
|----------|----------|----------|
| **one-for-one** | Only the failed child restarts | Independent children |
| **one-for-all** | All children restart when any fails | Interdependent children |
| **rest-for-one** | Failed child + all subsequently-started children restart | Ordered pipelines |

Supervisors enforce restart tolerance via `MaxRestarts` within `MaxTime` seconds. Exceeding this causes the supervisor to terminate and escalate upward -- the "let it crash" philosophy isolates failure at the correct hierarchy level.

```
┌────────────────────────────────────────────────────────────┐
│                    SUPERVISOR TREE                          │
│                                                            │
│              ┌──────────────┐                              │
│              │  Root Sup.    │                              │
│              │  max: 5/60s  │  (5 restarts per 60s max)   │
│              └──────┬───────┘                              │
│           ┌─────────┼─────────┐                            │
│           v         v         v                            │
│    ┌──────────┐ ┌──────────┐ ┌──────────┐                │
│    │ Team Sup. │ │ Team Sup. │ │ Team Sup. │                │
│    │ one-for-  │ │ one-for-  │ │ rest-for- │                │
│    │ one       │ │ all       │ │ one       │                │
│    └────┬─────┘ └────┬─────┘ └────┬─────┘                │
│     ┌───┼───┐    ┌───┼───┐    ┌───┼───┐                  │
│     v   v   v    v   v   v    v   v   v                  │
│    A1  A2  A3   B1  B2  B3   C1  C2  C3                  │
│                                                            │
│  A2 fails -> only A2 restarts (one-for-one)               │
│  B2 fails -> B1, B2, B3 all restart (one-for-all)         │
│  C2 fails -> C2, C3 restart; C1 untouched (rest-for-one) │
│  Team Sup exceeds 5/60s -> terminates, Root Sup decides   │
└────────────────────────────────────────────────────────────┘
```

### 4.3 Circuit Breaker Pattern

Three-state machine for protecting against cascading LLM API failures:

```
                ┌──────────────────────────────┐
                │         CLOSED               │
                │  (normal operation)           │
                │  failure_count < threshold    │
                │                              │
                │  On failure: failure_count++ │
                │  On success: reset count     │
                └──────────┬───────────────────┘
                           │ failure_count >= 5
                           v
                ┌──────────────────────────────┐
                │          OPEN                │
                │  (rejecting calls)           │
                │  Route to fallback model     │
                │                              │
                │  Wait: timeout (60s)         │
                └──────────┬───────────────────┘
                           │ timeout expires
                           v
                ┌──────────────────────────────┐
                │       HALF-OPEN              │
                │  (probing)                   │
                │  Allow 1 test request        │
                │                              │
                │  Success x2 -> CLOSED        │
                │  Failure -> OPEN             │
                └──────────────────────────────┘

Default: failureThreshold=5, successThreshold=2, timeout=60,000ms
```

### 4.4 Deadlock Prevention

**Resource ordering**: Agents acquire shared resources in a globally agreed order (always memory lock before file lock), making circular wait impossible.

**Mediator pattern**: Dedicated orchestrator brokers all resource requests with enforced timeouts (e.g., 30s). Agents that cannot acquire a resource receive `ResourceTimeout` rather than waiting indefinitely.

**Idempotency guards**: Before spawning any subagent, verify the same logical task is not already running. O(1) overhead. Production incident: context-monitor firing `new-session` events every 6 minutes, each spawning a memory-sync subagent without checking if sync was already running. Multiple subagents contended for same memory files, token budgets, and session state -- deadlock resolution took 20-45 minutes without watchdog.

### 4.5 Graceful Degradation

Five-level hierarchy, each response tagged with degradation level for observability:

| Level | Strategy | Description |
|-------|----------|-------------|
| 1 | Full capability | Primary model, all tools, real-time data |
| 2 | Reduced model | Fallback to smaller/cheaper model (e.g., Haiku instead of Opus) |
| 3 | Cached responses | Semantically similar cached results (threshold >= 0.92) |
| 4 | Static fallback | Pre-defined error response with actionable guidance |
| 5 | Queue for later | Accept and acknowledge, process when capacity restores |

### 4.6 Concurrency Control

**Semaphores** cap concurrent operations (e.g., `asyncio.Semaphore(20)` for LLM API calls). **Token buckets** cap throughput rate independently of concurrency. Combined: semaphore limiting to 20 concurrent calls + exponential backoff reduces 429 errors by ~90% in batch workloads. Exponential backoff with jitter reduces retry storms by 60-80% vs. fixed-interval retries (AWS research).

### 4.7 Context Compaction

Without active compaction, agents lose coherent access to original task objectives by approximately the 60% context mark (Factory AI research). Strategies: anchored iterative summarization, dropping old tool outputs, offloading intermediate findings to external storage. Trigger compaction at 75% of context limit.

### 4.8 Enterprise Security

#### 4.8.1 OWASP Top 10 for Agentic Applications (2026)

Published December 9, 2025, 100+ contributors. Key distinction from LLM Top 10: LLM Top 10 governs model-level risks (what a model says). Agentic Top 10 governs system-level risks (what an agent does). Prompt injection in a chatbot is a content problem; in an agent it becomes a control problem.

| ID | Vulnerability | Core Risk | Priority |
|----|--------------|-----------|----------|
| ASI01 | Agent Goal Hijack | Manipulating objectives via prompt injection | P0 (load-bearing) |
| ASI02 | Tool Misuse & Exploitation | Incorrect or malicious tool arguments | P1 |
| ASI03 | Agent Identity & Privilege Abuse | Missing per-agent credentials, shared sessions | P0 (load-bearing) |
| ASI04 | Agentic Supply Chain Compromise | Compromised third-party frameworks or MCP servers | P1 |
| ASI05 | Unexpected Code Execution | Agents generating and running unvalidated code | P1 |
| ASI06 | Memory & Context Poisoning | Hallucinated data stored in shared memory as fact | P2 |
| ASI07 | Insecure Inter-Agent Communication | Spoofed identities, replayed messages, forged consensus | P1 |
| ASI08 | Cascading Agent Failures | Error propagation across agent chains | P2 |
| ASI09 | Human-Agent Trust Exploitation | Social engineering through agent interfaces | P2 |
| ASI10 | Rogue Agents | Agents acting outside intended scope undetected | P2 |

Priority sequence: ASI01 + ASI03 first (load-bearing risks under every other category), then ASI04 + ASI07 as agent fleets grow.

#### 4.8.2 Agent Identity & Trust Boundaries

Every agent must have a distinct, managed identity -- not inherited from a user session or shared across instances. Agent identity must be credentialed, rotated, and audited independently of user identity. Per ISACA (2025), every AI agent must be provisioned as a named service account; shared credentials are an audit finding.

GS Consulting trust boundary scoring (weighted 0-100): identity ambiguity (20%), delegated authority (20%), action propagation (20%), data reach (15%), recovery coupling (15%), state persistence (10%).

#### 4.8.3 Inter-Agent Authorization

ASI07 mitigations:
- Mutual TLS and signed payloads for all inter-agent communication
- Authenticate every message, not just the initial handshake
- Message integrity verification and replay protection
- Zero-trust: verify every agent interaction before execution
- Unauthorized operations halt instantly; lateral movement becomes impossible

#### 4.8.4 Information Compartmentalization

Standard single-agent guardrails (input/output filters, system prompt hardening) do not address propagation pathways, trust inheritance, and shared context in multi-agent architectures. A single compromised agent poisoned 87% of downstream decision-making within 4 hours in simulated systems (Galileo, December 2025). System-level circuit breakers and quarantine mechanisms are required.

#### 4.8.5 Audit Trails

An AI agent audit trail is a chronological, tamper-resistant record of every input, internal chain-of-thought, LLM call, tool execution, and final output. Each event records the local agent and the initiating subject without losing actor history. Supports investigation and live containment: find active descendants, revoke credentials, cancel callbacks, quarantine artifacts, block downstream action.

#### 4.8.6 The Governance Gap

- 82% of enterprises already have AI agents their security teams did not know existed
- Only 7.2% of organizations have a named individual with formal accountability for agent behavior
- 88% of organizations deploying agents reported at least one security incident in 2025
- Only 38% monitor AI traffic end-to-end (prompts, tool calls, outputs)
- Only 17% continuously monitor agent-to-agent interactions
- 48% of all AI agents in production are running unsecured

#### 4.8.7 Regulatory Compliance

| Regulation | Key Requirement | Timeline |
|-----------|----------------|----------|
| **EU AI Act** | Model cards, data lineage, continuous quality monitoring, demonstrable human oversight (Article 14) | Full enforcement Aug 2, 2026 |
| **California SB-833** | State-level AI agent requirements | July 1, 2026 |
| **ISO/IEC 42001** | AI Management System standard; AWS, Microsoft, SAP certified | Dec 2023 (published), required by enterprise procurement 2026+ |
| **NIST AI Agent Standards** | Federal standards initiative | Feb 2026 |

Complete governance framework requires six control layers: identity/auth, least-privilege access, behavioral monitoring, human oversight checkpoints, audit logging, and supply chain security.

---

## 5. Production Enterprise Code

### 5.1 Supervisor Pattern with Agent Routing and Typed State

```python
"""
Supervisor pattern with typed state handoffs using LangGraph.
Demonstrates: intent classification, agent routing, loop guards,
structured state management with reducers.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Annotated, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


# --- Typed State ---

class AgentState:
    """Typed state shared across all agents in the supervisor graph."""
    messages: Annotated[list[BaseMessage], add_messages]
    current_agent: str
    resolution_notes: Annotated[list[str], operator.add]  # append-only
    handoff_count: int
    confidence: float


# --- Routing Schema ---

class RoutingDecision(BaseModel):
    """Supervisor's structured routing output."""
    next_agent: Literal["billing", "shipping", "technical", "FINISH"]
    reasoning: str = Field(description="Why this agent was selected")
    confidence: float = Field(ge=0.0, le=1.0)


# --- Agent Definitions ---

AGENT_CONFIGS = {
    "billing": {
        "system_prompt": (
            "You are a billing specialist. Handle subscription, refund, "
            "and payment inquiries. Return a structured resolution."
        ),
        "tools": ["lookup_invoice", "process_refund", "check_subscription"],
    },
    "shipping": {
        "system_prompt": (
            "You are a shipping specialist. Handle order tracking and "
            "logistics inquiries. Return tracking status and ETA."
        ),
        "tools": ["track_order", "carrier_status", "estimate_delivery"],
    },
    "technical": {
        "system_prompt": (
            "You are a technical support specialist. Diagnose issues "
            "and provide troubleshooting steps."
        ),
        "tools": ["search_knowledge_base", "run_diagnostics", "check_status"],
    },
}

MAX_HANDOFFS = 10  # loop guard


def create_supervisor_graph() -> StateGraph:
    """Build the supervisor orchestration graph."""
    llm = ChatOpenAI(model="gpt-4o", temperature=0)
    routing_llm = llm.with_structured_output(RoutingDecision)

    def supervisor_node(state: dict) -> dict:
        """Classify intent and route to the appropriate specialist."""
        if state.get("handoff_count", 0) >= MAX_HANDOFFS:
            return {
                "current_agent": "FINISH",
                "resolution_notes": [
                    f"Loop guard triggered after {MAX_HANDOFFS} handoffs"
                ],
            }

        decision: RoutingDecision = routing_llm.invoke(
            [
                {"role": "system", "content": (
                    "You are a supervisor routing customer requests to "
                    "specialists. Classify the intent and select the best "
                    "agent. Set next_agent to FINISH if the query is resolved."
                )},
                *state["messages"],
            ]
        )

        return {
            "current_agent": decision.next_agent,
            "handoff_count": state.get("handoff_count", 0) + 1,
            "confidence": decision.confidence,
            "resolution_notes": [
                f"Routed to {decision.next_agent}: {decision.reasoning}"
            ],
        }

    def make_worker_node(agent_name: str):
        """Factory for specialist worker nodes."""
        config = AGENT_CONFIGS[agent_name]

        def worker_node(state: dict) -> dict:
            response = llm.invoke(
                [
                    {"role": "system", "content": config["system_prompt"]},
                    *state["messages"],
                ]
            )
            return {
                "messages": [response],
                "current_agent": "supervisor",  # return control
                "resolution_notes": [
                    f"{agent_name} responded: {response.content[:200]}"
                ],
            }

        worker_node.__name__ = agent_name
        return worker_node

    def route_after_supervisor(state: dict) -> str:
        """Conditional edge: route to the selected agent or finish."""
        agent = state.get("current_agent", "FINISH")
        if agent == "FINISH" or state.get("handoff_count", 0) >= MAX_HANDOFFS:
            return END
        return agent

    # --- Build Graph ---
    graph = StateGraph(dict)
    graph.add_node("supervisor", supervisor_node)
    for name in AGENT_CONFIGS:
        graph.add_node(name, make_worker_node(name))

    graph.set_entry_point("supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {name: name for name in AGENT_CONFIGS} | {END: END},
    )
    for name in AGENT_CONFIGS:
        graph.add_edge(name, "supervisor")

    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


# --- Run ---

def run_supervisor(user_query: str, thread_id: str = "default") -> dict:
    """Execute a supervised multi-agent workflow with fault-tolerant state."""
    app = create_supervisor_graph()
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "messages": [HumanMessage(content=user_query)],
        "current_agent": "supervisor",
        "resolution_notes": [],
        "handoff_count": 0,
        "confidence": 0.0,
    }

    result = app.invoke(initial_state, config)
    return {
        "response": result["messages"][-1].content,
        "handoffs": result.get("handoff_count", 0),
        "confidence": result.get("confidence", 0.0),
        "audit_trail": result.get("resolution_notes", []),
    }
```

### 5.2 Hierarchical Multi-Agent System with Escalation

```python
"""
Hierarchical supervisor-of-supervisors pattern.
Demonstrates: tiered escalation, team-level isolation,
root supervisor fallback when team supervisors exhaust retries.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class EscalationTier(Enum):
    TEAM_WORKER = 1
    TEAM_SUPERVISOR = 2
    ROOT_SUPERVISOR = 3
    HUMAN = 4


@dataclass
class TaskResult:
    success: bool
    output: Any
    agent_id: str
    tier: EscalationTier
    attempts: int = 1
    escalation_reason: str | None = None


@dataclass
class AgentSpec:
    agent_id: str
    team: str
    capabilities: list[str]
    max_retries: int = 2

    async def execute(self, task: dict) -> TaskResult:
        """Execute a task. Replace with actual LLM call in production."""
        raise NotImplementedError("Subclass with actual LLM invocation")


@dataclass
class TeamSupervisor:
    """Manages a team of specialist agents with retry and escalation."""
    team_name: str
    workers: list[AgentSpec]
    max_retries: int = 3
    _retry_count: int = field(default=0, init=False)

    async def handle(self, task: dict) -> TaskResult:
        """Attempt task with workers, escalate on exhaustion."""
        for worker in self.workers:
            for attempt in range(1, worker.max_retries + 1):
                try:
                    result = await asyncio.wait_for(
                        worker.execute(task), timeout=30.0
                    )
                    if result.success:
                        logger.info(
                            "Worker %s resolved task on attempt %d",
                            worker.agent_id, attempt,
                        )
                        return result
                except asyncio.TimeoutError:
                    logger.warning(
                        "Worker %s timed out on attempt %d",
                        worker.agent_id, attempt,
                    )
                except Exception:
                    logger.exception(
                        "Worker %s failed on attempt %d",
                        worker.agent_id, attempt,
                    )

        # All workers exhausted -- escalate to root
        return TaskResult(
            success=False,
            output=None,
            agent_id=self.team_name,
            tier=EscalationTier.TEAM_SUPERVISOR,
            attempts=sum(w.max_retries for w in self.workers),
            escalation_reason=(
                f"Team {self.team_name} exhausted all {len(self.workers)} "
                f"workers with retries"
            ),
        )


@dataclass
class RootSupervisor:
    """Root-level supervisor managing multiple team supervisors."""
    teams: dict[str, TeamSupervisor]
    confidence_threshold: float = 0.6
    escalation_sla_seconds: float = 900.0  # 15-minute SLA for tier 3

    async def route_and_execute(self, task: dict) -> TaskResult:
        """Route task to appropriate team, handle cross-team escalation."""
        target_team = self._classify_team(task)

        if target_team not in self.teams:
            return TaskResult(
                success=False,
                output=None,
                agent_id="root_supervisor",
                tier=EscalationTier.ROOT_SUPERVISOR,
                escalation_reason=f"No team found for classification: {target_team}",
            )

        result = await self.teams[target_team].handle(task)

        if result.success:
            return result

        # Team failed -- try alternate teams before human escalation
        for alt_name, alt_team in self.teams.items():
            if alt_name == target_team:
                continue
            logger.info("Cross-team escalation: %s -> %s", target_team, alt_name)
            alt_result = await alt_team.handle(task)
            if alt_result.success:
                return alt_result

        # All teams exhausted -- escalate to human
        logger.critical(
            "All teams exhausted for task. Escalating to human. SLA: %.0fs",
            self.escalation_sla_seconds,
        )
        return TaskResult(
            success=False,
            output=None,
            agent_id="root_supervisor",
            tier=EscalationTier.HUMAN,
            escalation_reason="All automated teams exhausted. Human required.",
        )

    def _classify_team(self, task: dict) -> str:
        """Classify which team should handle the task.
        Replace with LLM-based classification in production.
        """
        # Placeholder: use keyword matching; production would use
        # llm.with_structured_output(TeamClassification)
        query = task.get("query", "").lower()
        if any(w in query for w in ("refund", "bill", "payment", "charge")):
            return "billing"
        if any(w in query for w in ("track", "ship", "deliver", "order")):
            return "shipping"
        return "technical"
```

### 5.3 Consensus Mechanism (Debate/Voting Between Agents)

```python
"""
Multi-agent consensus via majority voting with structural decorrelation.
Uses different models for each reviewer to decorrelate blind spots.
Includes confidence weighting and disagreement detection.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


class ReviewVerdict(BaseModel):
    """Structured output from each reviewer agent."""
    severity: Literal["critical", "major", "minor", "informational"]
    category: str = Field(description="e.g., security, correctness, performance")
    description: str
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_fix: str | None = None


class ReviewerVote(BaseModel):
    """A single reviewer's aggregated vote on a code change."""
    approve: bool
    findings: list[ReviewVerdict]
    reviewer_model: str
    overall_confidence: float


@dataclass
class ConsensusResult:
    approved: bool
    vote_count: dict[str, int]  # {"approve": N, "reject": M}
    agreement_ratio: float  # 0.0-1.0
    merged_findings: list[ReviewVerdict]
    requires_human: bool
    human_reason: str | None = None


async def run_reviewer(
    model_name: str, diff: str, system_prompt: str
) -> ReviewerVote:
    """Run a single reviewer agent. Each uses a different model
    for structural decorrelation of blind spots.

    In production, replace with actual LLM calls:
      llm = ChatOpenAI(model=model_name, temperature=0)
      structured_llm = llm.with_structured_output(ReviewerVote)
      return structured_llm.invoke([...])
    """
    # Production implementation would call the actual LLM here.
    # This stub exists only to show the interface contract.
    raise NotImplementedError("Wire to actual LLM in production")


async def consensus_review(
    diff: str,
    reviewer_configs: list[dict[str, str]] | None = None,
    approval_threshold: float = 0.6,
    confidence_floor: float = 0.5,
) -> ConsensusResult:
    """Run parallel reviewers, aggregate via weighted majority vote.

    Args:
        diff: The code diff to review.
        reviewer_configs: List of {"model": "...", "focus": "..."} dicts.
            Defaults to three structurally decorrelated reviewers.
        approval_threshold: Fraction of votes needed to approve.
        confidence_floor: Findings below this confidence are discarded.
    """
    if reviewer_configs is None:
        reviewer_configs = [
            {"model": "claude-sonnet-4-20250514", "focus": "security and correctness"},
            {"model": "gpt-4o", "focus": "architecture and performance"},
            {"model": "claude-opus-4-20250514", "focus": "edge cases and error handling"},
        ]

    # Fan-out: run all reviewers concurrently
    tasks = [
        run_reviewer(
            model_name=cfg["model"],
            diff=diff,
            system_prompt=(
                f"You are a code reviewer specializing in {cfg['focus']}. "
                f"Review the following diff and produce findings."
            ),
        )
        for cfg in reviewer_configs
    ]
    votes: list[ReviewerVote] = await asyncio.gather(*tasks)

    # Tally votes
    approve_count = sum(1 for v in votes if v.approve)
    reject_count = len(votes) - approve_count
    agreement_ratio = max(approve_count, reject_count) / len(votes)

    # Merge findings: deduplicate by category+description, keep highest confidence
    all_findings = [
        f for v in votes for f in v.findings if f.confidence >= confidence_floor
    ]
    merged = _deduplicate_findings(all_findings)

    # Any critical finding from any reviewer blocks approval regardless of vote
    has_critical = any(f.severity == "critical" for f in merged)
    approved = (
        (approve_count / len(votes)) >= approval_threshold and not has_critical
    )

    # Low agreement -> flag for human review
    requires_human = agreement_ratio < 0.6 or has_critical
    human_reason = None
    if requires_human:
        if has_critical:
            human_reason = "Critical finding detected -- human review required"
        elif agreement_ratio < 0.6:
            human_reason = (
                f"Low reviewer agreement ({agreement_ratio:.0%}) -- "
                f"disagreement may indicate novel issue"
            )

    return ConsensusResult(
        approved=approved,
        vote_count={"approve": approve_count, "reject": reject_count},
        agreement_ratio=agreement_ratio,
        merged_findings=merged,
        requires_human=requires_human,
        human_reason=human_reason,
    )


def _deduplicate_findings(findings: list[ReviewVerdict]) -> list[ReviewVerdict]:
    """Deduplicate findings by category. Keep the highest-confidence version."""
    best: dict[str, ReviewVerdict] = {}
    for f in findings:
        key = f"{f.category}:{f.severity}"
        if key not in best or f.confidence > best[key].confidence:
            best[key] = f
    # Sort: critical > major > minor > informational
    severity_order = {"critical": 0, "major": 1, "minor": 2, "informational": 3}
    return sorted(best.values(), key=lambda f: severity_order.get(f.severity, 99))
```

### 5.4 Human-in-the-Loop with Approval Gates

```python
"""
Human-in-the-loop approval gate with risk-tiered action classification.
Integrates with LangGraph's interrupt() pattern.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

logger = logging.getLogger(__name__)


class RiskTier(IntEnum):
    """Risk tiers for action classification."""
    READ_ONLY = 1        # autonomous
    REVERSIBLE = 2       # notify
    EXTERNAL = 3         # pre-approve
    IRREVERSIBLE = 4     # mandatory human approval


@dataclass
class ActionRequest:
    """An action an agent wants to perform."""
    action_type: str          # e.g., "send_email", "refund", "query_db"
    parameters: dict[str, Any]
    agent_id: str
    confidence: float
    risk_tier: RiskTier
    blast_radius: str         # "single_user", "team", "org", "public"
    reversible: bool


@dataclass
class ApprovalDecision:
    approved: bool
    approver: str             # "auto" | human identifier
    reason: str
    timestamp: float
    sla_met: bool


# Escalation SLA by tier (seconds)
ESCALATION_SLA = {
    RiskTier.READ_ONLY: float("inf"),    # no SLA, auto-approved
    RiskTier.REVERSIBLE: 14400.0,        # 4 hours
    RiskTier.EXTERNAL: 3600.0,           # 1 hour
    RiskTier.IRREVERSIBLE: 900.0,        # 15 minutes
}


def classify_risk(action: ActionRequest) -> RiskTier:
    """Classify an action's risk tier based on gate decision variables.

    Rule: if any two of (not reversible, high blast radius, low confidence)
    are true, escalate by one tier.
    """
    risk_signals = sum([
        not action.reversible,
        action.blast_radius in ("org", "public"),
        action.confidence < 0.7,
    ])

    base_tier = action.risk_tier
    if risk_signals >= 2 and base_tier < RiskTier.IRREVERSIBLE:
        return RiskTier(base_tier + 1)
    return base_tier


def evaluate_approval(action: ActionRequest) -> ApprovalDecision:
    """Evaluate whether an action can proceed autonomously or needs human approval."""
    effective_tier = classify_risk(action)
    sla_seconds = ESCALATION_SLA[effective_tier]
    now = time.time()

    if effective_tier == RiskTier.READ_ONLY:
        return ApprovalDecision(
            approved=True,
            approver="auto",
            reason="Read-only action -- autonomous execution",
            timestamp=now,
            sla_met=True,
        )

    if effective_tier == RiskTier.REVERSIBLE:
        logger.info(
            "Reversible action by %s: %s -- auto-approved with notification",
            action.agent_id, action.action_type,
        )
        # In production: send async notification to team channel
        return ApprovalDecision(
            approved=True,
            approver="auto_with_notify",
            reason="Reversible action -- approved with notification",
            timestamp=now,
            sla_met=True,
        )

    # EXTERNAL and IRREVERSIBLE require human approval.
    # In production, this would integrate with LangGraph's interrupt():
    #
    #   from langgraph.types import interrupt
    #   approval = interrupt({
    #       "action": action.action_type,
    #       "parameters": action.parameters,
    #       "risk_tier": effective_tier.name,
    #       "sla_seconds": sla_seconds,
    #       "agent_id": action.agent_id,
    #       "confidence": action.confidence,
    #   })
    #
    # The graph pauses here and returns the payload to the caller.
    # When the human responds, the graph resumes with their decision.

    logger.warning(
        "Action %s by %s requires human approval (tier: %s, SLA: %.0fs)",
        action.action_type, action.agent_id, effective_tier.name, sla_seconds,
    )
    return ApprovalDecision(
        approved=False,
        approver="pending_human",
        reason=f"Risk tier {effective_tier.name} requires human approval",
        timestamp=now,
        sla_met=False,  # will be updated when human responds
    )
```

### 5.5 Circuit Breaker and Graceful Degradation

```python
"""
Circuit breaker for LLM API calls with five-level graceful degradation.
Production-grade: thread-safe, configurable thresholds, structured logging.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class DegradationLevel(Enum):
    FULL = 1
    REDUCED_MODEL = 2
    CACHED = 3
    STATIC_FALLBACK = 4
    QUEUED = 5


@dataclass
class CircuitBreaker:
    """Three-state circuit breaker for LLM API calls.

    Closed  -> Open:      failure_count >= failure_threshold
    Open    -> Half-Open:  timeout_seconds elapsed
    Half-Open -> Closed:   success_count >= success_threshold
    Half-Open -> Open:     any failure
    """
    failure_threshold: int = 5
    success_threshold: int = 2
    timeout_seconds: float = 60.0

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def state(self) -> CircuitState:
        return self._state

    async def call(
        self,
        primary_fn: Callable[..., Awaitable[Any]],
        fallback_fn: Callable[..., Awaitable[Any]] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Any, DegradationLevel]:
        """Execute primary_fn through the circuit breaker.

        Returns (result, degradation_level) so callers can tag responses.
        """
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.timeout_seconds:
                    logger.info("Circuit transitioning OPEN -> HALF_OPEN")
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                else:
                    # Still open -- use fallback
                    if fallback_fn is not None:
                        result = await fallback_fn(*args, **kwargs)
                        return result, DegradationLevel.REDUCED_MODEL
                    raise CircuitOpenError(
                        f"Circuit open, no fallback. Retry in "
                        f"{self.timeout_seconds - (time.time() - self._last_failure_time):.0f}s"
                    )

        # CLOSED or HALF_OPEN: attempt the primary call
        try:
            result = await primary_fn(*args, **kwargs)
            await self._record_success()
            return result, DegradationLevel.FULL
        except Exception as exc:
            await self._record_failure()
            if fallback_fn is not None:
                logger.warning(
                    "Primary failed (%s), using fallback. State: %s",
                    exc, self._state.value,
                )
                result = await fallback_fn(*args, **kwargs)
                return result, DegradationLevel.REDUCED_MODEL
            raise

    async def _record_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    logger.info("Circuit transitioning HALF_OPEN -> CLOSED")
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
            else:
                self._failure_count = 0

    async def _record_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._state == CircuitState.HALF_OPEN:
                logger.warning("Circuit transitioning HALF_OPEN -> OPEN")
                self._state = CircuitState.OPEN
            elif self._failure_count >= self.failure_threshold:
                logger.warning(
                    "Circuit transitioning CLOSED -> OPEN after %d failures",
                    self._failure_count,
                )
                self._state = CircuitState.OPEN


class CircuitOpenError(Exception):
    """Raised when the circuit is open and no fallback is available."""


# --- Graceful Degradation Cascade ---

@dataclass
class DegradationCascade:
    """Five-level graceful degradation for multi-agent systems.

    Each level tries the next fallback strategy. Every response is
    tagged with its degradation level for observability.
    """
    primary_model: str = "claude-opus-4-20250514"
    fallback_model: str = "claude-haiku-4-20250514"
    cache_similarity_threshold: float = 0.92
    queue_timeout_seconds: float = 300.0

    async def execute(
        self,
        query: str,
        primary_fn: Callable[..., Awaitable[str]],
        fallback_fn: Callable[..., Awaitable[str]],
        cache_fn: Callable[[str, float], Awaitable[str | None]],
        static_response: str = "We're experiencing high demand. A team member will follow up shortly.",
    ) -> tuple[str, DegradationLevel]:
        """Try each degradation level in sequence until one succeeds."""

        # Level 1: Full capability
        try:
            result = await asyncio.wait_for(primary_fn(query), timeout=30.0)
            return result, DegradationLevel.FULL
        except Exception as exc:
            logger.warning("Level 1 (primary) failed: %s", exc)

        # Level 2: Reduced model
        try:
            result = await asyncio.wait_for(fallback_fn(query), timeout=15.0)
            return result, DegradationLevel.REDUCED_MODEL
        except Exception as exc:
            logger.warning("Level 2 (fallback model) failed: %s", exc)

        # Level 3: Cached response
        try:
            cached = await cache_fn(query, self.cache_similarity_threshold)
            if cached is not None:
                return cached, DegradationLevel.CACHED
            logger.info("Level 3: no cache hit above threshold %.2f",
                       self.cache_similarity_threshold)
        except Exception as exc:
            logger.warning("Level 3 (cache) failed: %s", exc)

        # Level 4: Static fallback
        logger.warning("Level 4: returning static fallback response")
        return static_response, DegradationLevel.STATIC_FALLBACK

        # Level 5 (queue for later) would be implemented as:
        # await queue_service.enqueue(query)
        # return ack_message, DegradationLevel.QUEUED
```

### 5.6 Structured Logging with Distributed Tracing

```python
"""
Distributed tracing across multi-agent systems.
Propagates trace_id and span_id through agent handoffs for
end-to-end observability. Compatible with OpenTelemetry export.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Generator


@dataclass
class Span:
    """A single span in a distributed trace."""
    trace_id: str
    span_id: str
    parent_span_id: str | None
    agent_id: str
    operation: str
    start_time: float
    end_time: float | None = None
    status: str = "in_progress"
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration_ms(self) -> float | None:
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000


class AgentTracer:
    """Distributed tracer for multi-agent orchestration.

    Maintains a trace context that propagates through agent handoffs.
    Each agent creates child spans under the shared trace_id.
    """

    def __init__(self, service_name: str = "multi-agent-system"):
        self.service_name = service_name
        self._spans: list[Span] = []
        self._logger = logging.getLogger(f"tracer.{service_name}")

    def new_trace(self) -> str:
        """Start a new trace. Returns the trace_id."""
        return uuid.uuid4().hex[:16]

    @contextmanager
    def span(
        self,
        trace_id: str,
        agent_id: str,
        operation: str,
        parent_span_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Generator[Span, None, None]:
        """Context manager that creates, yields, and finalizes a span."""
        span = Span(
            trace_id=trace_id,
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=parent_span_id,
            agent_id=agent_id,
            operation=operation,
            start_time=time.time(),
            attributes=attributes or {},
        )

        self._logger.info(
            json.dumps({
                "event": "span_start",
                "trace_id": trace_id,
                "span_id": span.span_id,
                "parent_span_id": parent_span_id,
                "agent_id": agent_id,
                "operation": operation,
                "service": self.service_name,
            })
        )

        try:
            yield span
            span.status = "ok"
        except Exception as exc:
            span.status = "error"
            span.events.append({
                "name": "exception",
                "timestamp": time.time(),
                "attributes": {
                    "exception.type": type(exc).__name__,
                    "exception.message": str(exc),
                },
            })
            raise
        finally:
            span.end_time = time.time()
            self._spans.append(span)

            self._logger.info(
                json.dumps({
                    "event": "span_end",
                    "trace_id": trace_id,
                    "span_id": span.span_id,
                    "agent_id": agent_id,
                    "operation": operation,
                    "status": span.status,
                    "duration_ms": span.duration_ms,
                    "service": self.service_name,
                    "attributes": span.attributes,
                })
            )

    def add_event(self, span: Span, name: str, attributes: dict | None = None) -> None:
        """Add a timestamped event to a span (e.g., tool call, handoff)."""
        span.events.append({
            "name": name,
            "timestamp": time.time(),
            "attributes": attributes or {},
        })

    def get_trace(self, trace_id: str) -> list[dict]:
        """Export all spans for a trace in OpenTelemetry-compatible format."""
        return [asdict(s) for s in self._spans if s.trace_id == trace_id]


# --- Usage Example ---

def traced_supervisor_flow(user_query: str) -> dict:
    """Demonstrates trace propagation through a supervisor -> worker flow."""
    tracer = AgentTracer(service_name="customer-support")
    trace_id = tracer.new_trace()

    with tracer.span(trace_id, "supervisor", "classify_intent") as sup_span:
        sup_span.attributes["user_query_length"] = len(user_query)
        sup_span.attributes["routing_model"] = "gpt-4o"
        tracer.add_event(sup_span, "routing_decision", {"target": "billing"})

        # Worker span is a child of the supervisor span
        with tracer.span(
            trace_id, "billing_agent", "process_refund",
            parent_span_id=sup_span.span_id,
        ) as worker_span:
            worker_span.attributes["tools_used"] = ["lookup_invoice", "process_refund"]
            worker_span.attributes["tokens_consumed"] = 1200
            tracer.add_event(worker_span, "tool_call", {"tool": "lookup_invoice"})
            tracer.add_event(worker_span, "tool_call", {"tool": "process_refund"})

        # Back to supervisor for synthesis
        with tracer.span(
            trace_id, "supervisor", "synthesize_response",
            parent_span_id=sup_span.span_id,
        ) as synth_span:
            synth_span.attributes["tokens_consumed"] = 450

    # Export full trace
    trace = tracer.get_trace(trace_id)
    total_duration = sum(s.get("duration_ms", 0) or 0 for s in trace)
    total_tokens = sum(
        s.get("attributes", {}).get("tokens_consumed", 0) for s in trace
    )
    return {
        "trace_id": trace_id,
        "spans": len(trace),
        "total_duration_ms": total_duration,
        "total_tokens": total_tokens,
        "trace": trace,
    }
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Multi-Agent Customer Service Platform

**Problem statement**: A B2C SaaS company processes 50,000 support tickets/day across billing, shipping, technical support, and returns. Current single-agent chatbot suffers from context drift on multi-turn conversations, inconsistent tool selection across domains (31% tool misuse rate), and 40% human escalation rate. Target: reduce human escalation to <15%, maintain <10s P95 response time, and achieve >90% first-contact resolution.

**Proposed architecture**:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                   MULTI-AGENT CUSTOMER SERVICE PLATFORM                      │
│                                                                              │
│  User Message ──> API Gateway (rate limit, auth)                            │
│                        │                                                     │
│                   ┌────v────────────┐                                       │
│                   │ Triage Agent     │  Intent + urgency + sentiment         │
│                   │ (GPT-4.1-mini)  │  Structured output: {domain, tier,    │
│                   │ Cost: ~$0.0004  │   sentiment_score, entities}          │
│                   └────┬────────────┘                                       │
│                        │                                                     │
│                   ┌────v────────────┐                                       │
│                   │ Supervisor       │  Routes based on triage output        │
│                   │ (Sonnet 4)      │  Loop guard: max 10 handoffs          │
│                   └────┬────────────┘                                       │
│          ┌─────────────┼──────────────┬──────────────┐                      │
│          v             v              v              v                      │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐      │
│  │ Billing Agent │ │ Shipping     │ │ Tech Support │ │ Returns      │      │
│  │ (Sonnet 4)   │ │ Agent        │ │ Agent        │ │ Agent        │      │
│  │              │ │ (Sonnet 4)   │ │ (Sonnet 4)   │ │ (Sonnet 4)   │      │
│  │ Tools:       │ │ Tools:       │ │ Tools:       │ │ Tools:       │      │
│  │ - invoice_db │ │ - tracking   │ │ - kb_search  │ │ - return_rma │      │
│  │ - refund_api │ │ - carrier_api│ │ - diag_run   │ │ - label_gen  │      │
│  │ - sub_mgmt   │ │ - est_eta    │ │ - log_search │ │ - refund_api │      │
│  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘ └──────┬───────┘      │
│         └────────────────┼────────────────┼────────────────┘               │
│                     ┌────v────────────────v──┐                              │
│                     │ Quality Check Agent     │  Reviews before delivery    │
│                     │ (GPT-4.1-mini)          │  Checks: tone, accuracy,   │
│                     │                        │  completeness, PII redaction│
│                     └────┬───────────────────┘                              │
│                          │                                                   │
│                     ┌────v───────────────────┐                              │
│                     │ Escalation Handler      │  Triggers:                  │
│                     │                        │  - confidence < 0.6         │
│                     │  Tier 1: team (4h SLA) │  - 2 failed resolutions     │
│                     │  Tier 2: lead (1h SLA) │  - negative sentiment       │
│                     │  Tier 3: auth (15m SLA)│  - compliance keywords      │
│                     └────────────────────────┘                              │
│                                                                              │
│  PERSISTENCE: LangGraph PostgresSaver (thread-scoped checkpoints)           │
│  TELEMETRY: OpenTelemetry traces, per-agent latency, routing accuracy       │
│  SECURITY: per-agent service accounts, mTLS inter-agent, PII filters       │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix**:

| Dimension | A: Supervisor (recommended) | B: Swarm | C: Single Agent + Tools |
|-----------|-----------------------------|----------|------------------------|
| **Routing accuracy** | 94% | 91% (-3%) | N/A (one agent) |
| **Latency (P95)** | ~9s (handoff case) | ~5.4s (faster) | ~3s (fastest) |
| **Token cost / request** | ~2,800 | ~1,900 (-32%) | ~900 (-68%) |
| **Daily cost at 50K req (Sonnet)** | ~$2,100 | ~$1,450 | ~$700 |
| **Context drift resilience** | High (typed state, reducers) | Medium (shared context) | Low (single long context) |
| **Failure degradation** | SPOF risk (mitigate w/ standby) | 31% on 1 agent failure | N/A |
| **Observability** | Excellent (centralized routing) | Poor (peer-to-peer) | Simple |
| **Ops complexity** | Medium | High (debug difficulty) | Low |
| **Scalability ceiling** | High (add teams via hierarchy) | Medium (O(n^2) handoff tools) | Low (context window bound) |
| **Compliance audit** | Strong (supervisor audit trail) | Weak (distributed decisions) | Medium |

**Decision rationale**: Supervisor wins despite higher cost and latency because (1) 94% routing accuracy reduces misrouted tickets (each misroute costs ~$8 in human agent time), (2) centralized routing produces complete audit trails required for financial services compliance (EU AI Act Article 14), (3) typed state with reducers prevents the context drift that causes the existing single-agent's 40% escalation rate, and (4) the SPOF risk is mitigated by hot-standby supervisor and the hierarchical extension path for future scaling beyond 4 domains. The 32% token savings of swarm does not justify the 31% failure degradation and poor observability in a 50K req/day environment.

### 6.2 Scenario: Multi-Agent Code Review Pipeline

**Problem statement**: An engineering org (200 developers, ~400 PRs/day) finds that AI-generated code (adopted by 85% of developers) has increased PR size by 154% and PR review time by 91%. Bug rate is up 9%. Single-model review tools miss domain-specific issues (security reviewer misses performance; performance reviewer misses edge cases). Target: catch 40%+ more bugs than single-model review, reduce human review time by 50%, and keep review cost under $0.50/PR.

**Proposed architecture**:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                  MULTI-AGENT CODE REVIEW PIPELINE                            │
│                                                                              │
│  PR Webhook ──> Version Pin (lock model + tool versions for reproducibility)│
│                        │                                                     │
│                   ┌────v────────────────────┐                               │
│                   │ Diff Preprocessor        │  Split into reviewable chunks │
│                   │ (deterministic, no LLM)  │  Extract: files, functions,  │
│                   │                          │  imports, test coverage delta │
│                   └────┬───────────────────┘                               │
│                        │                                                     │
│         ┌──────────────┼──────────────┬──────────────┐                      │
│         v              v              v              v                      │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐      │
│  │ Architect     │ │ Security     │ │ QA Agent     │ │ Performance  │      │
│  │ Agent         │ │ Agent        │ │              │ │ Agent        │      │
│  │ (Sonnet 4)   │ │ (Opus 4)     │ │ (Sonnet 4)   │ │ (GPT-4o)    │      │
│  │              │ │              │ │              │ │              │      │
│  │ Focus:       │ │ Focus:       │ │ Focus:       │ │ Focus:       │      │
│  │ - patterns   │ │ - OWASP      │ │ - test gaps  │ │ - O(n) vs   │      │
│  │ - structure  │ │ - input val  │ │ - edge cases │ │   O(n^2)     │      │
│  │ - coupling   │ │ - auth/authz │ │ - coverage   │ │ - resources  │      │
│  │ - SOLID      │ │ - injection  │ │ - assertion  │ │ - memory     │      │
│  │              │ │              │ │   quality    │ │ - concurrency│      │
│  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘ └──────┬───────┘      │
│         └────────────────┼────────────────┼────────────────┘               │
│                     ┌────v────────────────v──┐                              │
│                     │ Adversarial Critic      │  Different model from       │
│                     │ (Claude Opus 4)         │  the reviewer that          │
│                     │                        │  produced the finding       │
│                     │ - Challenges each finding│                            │
│                     │ - Discards < threshold  │                              │
│                     │ - Decorrelates blind    │                              │
│                     │   spots across models   │                              │
│                     └────┬───────────────────┘                              │
│                          │                                                   │
│                     ┌────v───────────────────┐                              │
│                     │ Consensus Aggregator    │  Priority: security >       │
│                     │ (deterministic logic)   │  correctness > perf > style │
│                     │                        │                              │
│                     │ - Deduplicate findings  │  Severity: block /          │
│                     │ - Resolve conflicts     │  require-discussion /       │
│                     │ - Apply org policy      │  advisory / informational   │
│                     └────┬───────────────────┘                              │
│                          │                                                   │
│                     ┌────v───────────────────┐                              │
│                     │ Output Formatter        │                              │
│                     │ (no LLM)               │                              │
│                     │                        │                              │
│                     │ - Inline PR comments   │                              │
│                     │ - Summary with scores  │                              │
│                     │ - Status check (CI)    │                              │
│                     │ - Feedback loop to     │                              │
│                     │   adjust thresholds    │                              │
│                     └────────────────────────┘                              │
│                                                                              │
│  COST BUDGET: $0.50/PR max (enforced via token ceiling per agent)           │
│  TELEMETRY: false positive rate, findings addressed vs dismissed            │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix**:

| Dimension | A: Parallel Specialists + Critic (recommended) | B: Single Premium Model | C: Sequential Pipeline |
|-----------|-------------------------------------------------|------------------------|----------------------|
| **Bug detection** | +40% vs. single model | Baseline | +25% (no decorrelation) |
| **False positive rate** | Low (adversarial critic filters) | Medium | High (no critic stage) |
| **Latency** | ~15s (parallel + critic) | ~8s | ~30s (sequential 4 stages) |
| **Cost per PR** | ~$0.35-0.45 | ~$0.15 | ~$0.40 |
| **Blind spot coverage** | High (structural decorrelation) | Low (single model bias) | Medium (same model reused) |
| **Ops complexity** | High (4 agents + critic + aggregator) | Low | Medium (4 sequential stages) |
| **Scalability** | High (add specialist agents) | Low (context window bound) | Medium (add stages) |
| **Feedback loop** | Rich (per-agent metrics) | Single signal | Per-stage metrics |

**Cost breakdown per PR** (recommended architecture):

```
4 parallel reviewers (Sonnet 4 x 2, Opus 4 x 1, GPT-4o x 1):
  - Sonnet 4: ~2,000 tokens in + ~800 out = $0.006 + $0.012 = $0.018 x 2 = $0.036
  - Opus 4:   ~2,000 tokens in + ~800 out = $0.030 + $0.060 = $0.090
  - GPT-4o:   ~2,000 tokens in + ~800 out = $0.005 + $0.008 = $0.013

Adversarial critic (Opus 4, reviewing 4 agents' findings):
  - ~3,000 tokens in + ~1,500 out = $0.045 + $0.113 = $0.158

Subtotal per PR: ~$0.30
With prompt caching (84% hit rate on system prompts): ~$0.22
At 400 PRs/day: ~$88/day, ~$2,640/month

Savings vs. human review time (200 devs x 30 min saved/day x $80/hr):
  ~$4,000/day saved, 66:1 ROI
```

**Decision rationale**: Parallel specialists with adversarial critic wins because (1) structural decorrelation across four different models/specializations catches blind spots that any single model misses (ICLR 2025 data: mixed-model configurations yield the highest accuracy), (2) the adversarial critic stage filters false positives before they reach developers (critical for adoption -- developers abandon tools with >20% false positive rates), (3) parallel execution keeps latency at ~15s vs. ~30s for sequential (acceptable for async PR review), and (4) the $0.22-0.45/PR cost is 66:1 ROI against human review time saved. The single premium model option is cheaper but misses the +40% bug detection improvement that justifies the system.

---

## Appendix: Production Failure Modes Reference

This section consolidates failure mode data referenced throughout the module.

**Overall failure rates**: Multi-agent LLM systems fail 41-86% of the time depending on task complexity. Five agents at 95% individual accuracy yield ~77% end-to-end success. 40% of multi-agent pilots fail within 6 months. Gartner predicts >40% of agentic AI projects cancelled by 2027.

**MAST Failure Taxonomy (NeurIPS 2025)** -- analyzed 1,600+ execution traces (kappa = 0.88):
- Specification Problems: 41.77% (role ambiguity, unclear tasks, missing constraints)
- Coordination Failures: 36.94% (communication breakdowns, state sync, conflicting objectives)
- Verification Gaps: 21.30% (inadequate testing, missing validation)

**Seven critical failure patterns**:

| Pattern | Root Cause | Detection Signal | Mitigation |
|---------|-----------|-----------------|-----------|
| Cascading errors | Agent treats flawed upstream output as ground truth | Monotonic quality degradation | Schema validation at every agent boundary |
| Coordination deadlock | Circular dependencies (A waits B, B waits C, C waits A) | No explicit error, just timeout | Enforce explicit topology + hard timeouts |
| Context drift | Shared goal degrades through free-text handoffs | Goal divergence over turns | Immutable objective field in typed task object |
| Infinite loops | Unbounded refinement cycle between agents | Token/cost spike, no convergence | Step limits + token caps + diminishing-returns detection (<5% improvement x3 -> terminate) |
| Silent partial failure | Intermediate agent hits tool error, pipeline reports success | Missing data in final output | Output evaluation gates at every handoff |
| Inter-agent misalignment | Conflicting implicit role assumptions | Format mismatches downstream | Explicit output contracts + alignment tests before integration |
| Tool/data corruption | External tools return stale or poisoned data | Stale timestamps, data anomalies | MCP with strict validation, least-privilege scoping |

**Notable incidents**:
- November 2025: Two LangChain agents in infinite conversation cycle -- **$47,000 bill** over 11 days
- December 2025: Amazon Kiro agent with operator-level permissions deleted and rebuilt AWS Cost Explorer to "fix" a minor issue -- **13-hour outage** in mainland China
- Galileo simulation: Single compromised agent poisoned **87% of downstream decisions** within 4 hours
- Tool misuse is the most common proximate cause of production failures: **31% of incidents** (2024-2025)
