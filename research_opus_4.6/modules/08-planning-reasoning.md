# Module 08: Planning & Reasoning -- CoT, ToT, GoT, ReAct, Reflexion, LATS, Reasoning Models, and Production Planning Systems

**Scope**: Reasoning paradigms (CoT, self-consistency, ToT, GoT), agentic patterns (ReAct, Reflexion, LATS, plan-and-execute), reasoning models (o1/o3/o4-mini, DeepSeek-R1, QwQ, Claude extended thinking), structured planning (DAG decomposition, GNNVerifier), dynamic replanning, reasoning transparency, and production planning architectures.
**Prerequisite**: Module 04 (Agent Architecture), Module 05 (Agent Frameworks).
**Last updated**: 2026-08-21 | **Sources consulted**: 60

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                    CONTROL PLANE                                           │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
 │  │  Complexity       │  │  Model Router    │  │  Budget Enforcer │  │  HITL Gate       │  │
 │  │  Classifier       │  │  - Standard for  │  │  - Per-task token│  │  - Plan approval │  │
 │  │  - Simple: direct │  │    simple tasks  │  │    cap           │  │  - Risk-tiered   │  │
 │  │  - Medium: CoT    │  │  - Reasoning for │  │  - Per-tenant $  │  │    classification│  │
 │  │  - Complex: o3/   │  │    complex tasks │  │    budget        │  │  - Maker-checker │  │
 │  │    extended think  │  │  - Hybrid routing│  │  - Loop detection│  │  - Async audit   │  │
 │  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
 └───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┘
             │                    │                    │                    │
 ┌───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┐
 │           ▼                    ▼                    ▼                    ▼                  │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                     DATA PLANE: REASONING & PLANNING ENGINE                        │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  PLANNING LAYER                                                          │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Task Decomp. │  │ DAG Builder  │  │ Dependency   │  │ Plan       │  │      │    │
 │  │  │  │ - Goal → sub-│  │ - Parallel   │  │ Resolver     │  │ Validator  │  │      │    │
 │  │  │  │   tasks      │  │   branches   │  │ - Data flow  │  │ - GNN      │  │      │    │
 │  │  │  │ - Recursive  │  │ - Sequential │  │ - Ordering   │  │   Verifier │  │      │    │
 │  │  │  │   hierarchy  │  │   chains     │  │ - Context    │  │ - Schema   │  │      │    │
 │  │  │  │              │  │ - Hybrid     │  │   propagation│  │   checks   │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  REASONING LAYER                                                         │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ CoT / Zero-  │  │ Self-        │  │ Reasoning    │  │ ReAct Loop │  │      │    │
 │  │  │  │ shot CoT     │  │ Consistency  │  │ Models       │  │ - Thought  │  │      │    │
 │  │  │  │ - Linear     │  │ - N=5-10     │  │ - o3/o4-mini │  │ - Action   │  │      │    │
 │  │  │  │   reasoning  │  │   samples    │  │ - DeepSeek-R1│  │ - Observe  │  │      │    │
 │  │  │  │ - 1.5-3x     │  │ - Majority   │  │ - Claude ext │  │ - Iterate  │  │      │    │
 │  │  │  │   tokens     │  │   vote       │  │ - 3-10x      │  │            │  │      │    │
 │  │  │  │              │  │ - RASC/CISC  │  │   tokens     │  │            │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  │                                                                          │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                   │      │    │
 │  │  │  │ ToT / GoT    │  │ Reflexion    │  │ LATS         │                   │      │    │
 │  │  │  │ - Tree/Graph │  │ - Episodic   │  │ - MCTS       │                   │      │    │
 │  │  │  │   search     │  │   memory     │  │ - Selection  │                   │      │    │
 │  │  │  │ - BFS/DFS    │  │ - Self-      │  │ - Expansion  │                   │      │    │
 │  │  │  │ - Aggregation│  │   critique   │  │ - Simulation │                   │      │    │
 │  │  │  │ - 20-50x tok │  │ - Retry      │  │ - Backprop   │                   │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘                   │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  EXECUTION LAYER                                                         │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Step Executor│  │ Tool Dispatch │  │ Replan Check │  │ Verify &   │  │      │    │
 │  │  │  │ - Run subtask│  │ - MCP gateway │  │ - On failure │  │ Checkpoint │  │      │    │
 │  │  │  │ - Capture    │  │ - Schema val. │  │ - On surprise│  │ - Per-step │  │      │    │
 │  │  │  │   output     │  │ - Idempotency │  │ - Local/hier │  │   state    │  │      │    │
 │  │  │  │ - Timeout    │  │   guard       │  │   /complete  │  │ - Durability│  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                         TOOL PROXY LAYER                                           │    │
 │  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐       │    │
 │  │  │ MCP Gateway   │  │ Output Valid. │  │ Injection     │  │ Sandbox       │       │    │
 │  │  │ - Tool routing│  │ - Schema check│  │ Detector      │  │ - Code exec   │       │    │
 │  │  │ - Rate limit  │  │ - Null/empty  │  │ - Reasoning   │  │ - gVisor/WASM │       │    │
 │  │  │ - Timeout     │  │   rejection   │  │   chain audit │  │ - Snapshot    │       │    │
 │  │  │ - Circuit brk │  │ - Type coerce │  │ - Guardian    │  │   restore     │       │    │
 │  │  └───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘       │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  PERSISTENCE LAYER                                         │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Plan Store        │  │ Checkpoint Store  │  │ Reasoning Trace   │  │ Temporal /      │  │
 │  │ - DAG structure   │  │ - Per-step state  │  │ Store             │  │ Durable Exec    │  │
 │  │ - Dependencies    │  │ - Tool outputs    │  │ - Full CoT logs   │  │ - Workflow      │  │
 │  │ - Status per node │  │ - Agent context   │  │ - Hidden token    │  │   history       │  │
 │  │ - Version history │  │ - Resume-ready    │  │   counts          │  │ - Idempotency   │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  TELEMETRY PLANE                                           │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Reasoning Metrics │  │ Planning Metrics  │  │ Cost Tracker      │  │ Alerting       │  │
 │  │ - Steps/task      │  │ - Plan adherence  │  │ - Reasoning token │  │ - Loop detect  │  │
 │  │ - Reasoning token │  │ - Replan count    │  │   cost per task   │  │ - Budget exceed│  │
 │  │   count (hidden)  │  │ - Step efficiency │  │ - Hidden vs       │  │ - Context rot  │  │
 │  │ - CoT faithfulness│  │ - DAG parallelism │  │   visible ratio   │  │ - Plan-reality │  │
 │  │ - Self-consistency│  │ - Task completion │  │ - Total TCO       │  │   mismatch     │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

**Step 1 — Complexity Classification**: An incoming task hits the **Complexity Classifier**. Simple tasks (code completion, summarization) route to standard models with direct prompting. Medium tasks (analysis, multi-step reasoning) use CoT or self-consistency. Complex tasks (architectural design, mathematical proofs) route to reasoning models (o3, Claude extended thinking) or structured planning.

**Step 2 — Planning**: For multi-step tasks, the **Planning Layer** decomposes the goal into subtasks, builds a DAG with dependency edges, and resolves execution order. Independent branches are marked for parallel execution. The **Plan Validator** (GNNVerifier) checks structural integrity before execution begins.

**Step 3 — HITL Gate**: For high-stakes tasks, the complete plan is presented to a human for approval before execution begins. Risk-tiered classification determines which plans need approval: Tier 1 (full automation) for read-only operations, Tier 4 (full approval) for irreversible actions with high blast radius.

**Step 4 — Step Execution**: Each subtask enters the **Execution Layer**. The Step Executor runs the subtask (possibly via a ReAct loop for adaptive steps). Tool calls pass through the **Tool Proxy Layer** for schema validation, injection detection, and sandboxing. Each step's output is checkpointed for crash recovery.

**Step 5 — Replan Check**: After each step, the system evaluates whether the plan needs revision. Three levels: local adjustment (modify current step), hierarchical replanning (re-decompose a higher-level goal), or complete replanning (discard remaining plan and start fresh from current state). Replanning triggers on step failure or unexpected output.

**Step 6 — Verify & Complete**: On plan completion, the system verifies that the original goal's success criteria are met. Reasoning traces, tool outputs, and plan execution history are persisted for audit.

---

## 2. Core Mechanics & Algorithms

### 2.1 Reasoning Paradigm Comparison

| Paradigm | Structure | Token Amplification | Backtracking | Aggregation | Best For |
|----------|-----------|--------------------:|:------------:|:-----------:|----------|
| **Direct** | Single call | 1× | No | No | Simple pattern-matching tasks |
| **Zero-shot CoT** | Linear chain | 1.5–3× | No | No | Medium reasoning; zero setup cost |
| **Few-shot CoT** | Linear chain | 2–4× | No | No | Controlled output format |
| **Self-consistency** | N parallel chains | N× | No | Vote | High-stakes single-answer tasks |
| **ToT** | Tree (BFS/DFS) | 20–50× | Yes | No | Exploration problems (Game of 24) |
| **GoT** | Arbitrary DAG | 10–30× | Yes | Yes | Complex multi-factor synthesis |
| **ReAct** | Loop (T→A→O) | Variable | No | No | Tool-using tasks needing feedback |
| **Reflexion** | ReAct + episodic memory | 2–3× per attempt | Retry | No | Tasks with verifiable success criteria |
| **LATS** | MCTS tree | 10–100× | Yes | Select | Hard tasks; performance > efficiency |
| **Reasoning model** | Internal (hidden) | 3–10× (hidden tokens) | Internal | Internal | Multi-step logic, math, analysis |

### 2.2 Chain-of-Thought and Self-Consistency

**Zero-shot CoT**: Append "Let's think step by step" to the prompt. For modern frontier models (Claude Opus, GPT-5, Qwen3), zero-shot CoT matches or exceeds few-shot CoT — model attention prioritizes instructions over demonstration tokens. Few-shot exemplars primarily enforce output format, not reasoning quality.

**Self-consistency**: Sample N independent chains at temperature ~0.7, majority-vote the final answer. Adds 12–18% accuracy on top of CoT. Practical: N=5–10 samples. Stronger models need fewer samples.

**RASC** (NAACL 2025): Dynamic stopping and weighted voting. Reduces sample usage by ~70% while maintaining accuracy.

**2026 finding** (Wharton): CoT's value is decreasing for reasoning models — minimal accuracy gains for o3-mini (+2.9%) and o4-mini (+3.1%) at 20–80% more latency. The reasoning is already built into the model; explicit CoT is redundant.

### 2.3 Tree of Thoughts and Graph of Thoughts

**ToT** maintains a tree of reasoning steps. The LLM generates candidate next-thoughts, self-evaluates each for progress, and a search algorithm (BFS/DFS/beam) explores with lookahead and backtracking. Achieves better solutions on exploration problems but at 20–50× token cost.

**GoT** generalizes beyond trees by enabling arbitrary directed graphs:
- **Aggregation**: Combining multiple thoughts into synergistic outcomes (impossible in a tree).
- **Refinement loops**: Feedback edges that improve earlier thoughts.
- **Distillation**: Condensing thought networks into essential conclusions.

GoT increases sorting quality by 62% over ToT while reducing costs by >31%.

**Adaptive GoT (AGoT)** (2025): Recursively decomposes only subproblems judged sufficiently complex, yielding dynamic DAGs per-instance.

### 2.4 Agentic Reasoning: ReAct, Reflexion, LATS

**ReAct** (Thought → Action → Observation loop):
- Excels when tasks require real-world interaction and feedback.
- One LLM call per step (gets expensive on long chains).
- No backtracking — once a step is taken, the agent cannot undo it.
- Prone to looping on ambiguous tasks.

**Reflexion** (ReAct + self-critique + episodic memory):
- After each task attempt, generates verbal critique and stores it for future trials.
- On next attempt, reads prior reflections and adjusts approach.
- Best for tasks with clear, automated success criteria (run tests, validate schema, check answer).
- Limitation: single-agent Reflexion reinforces its own blind spots — the same model generates both output and critique.

**LATS** (Monte Carlo Tree Search for agents):
- The LLM serves simultaneously as agent (generating actions), value function (evaluating states), and optimizer (selecting branches to explore).
- HumanEval: 94.4% pass@1 (GPT-4). HotPotQA: 0.61 EM. WebShop: 75.9 avg.
- Higher computational cost than ReAct or Reflexion. Requires ability to revert to earlier states.

### 2.5 Plan-and-Execute with Dynamic Replanning

**Hybrid skeleton + ReAct** (dominant production pattern): A coarse plan committed up front, with each step expanded just-in-time via a ReAct loop. The skeleton provides structure and inspectability; per-step ReAct provides adaptability.

**Replanning levels**:
- **Local adjustment**: Modify tactical steps within the current phase.
- **Hierarchical replanning**: Escalate to re-decompose a higher-level goal.
- **Complete replanning**: Discard remaining plan and re-plan from current state.

**Cost mitigation**: Replan only when a step fails or returns unexpected output, not after every step.

**Task decomposition insight**: Decomposition is a verifiability strategy before it is a planning strategy — the reason to split a task is that each subtask has a checkable definition of done, enabling independent verification, retry, or delegation.

### 2.6 Structured Planning: DAG Decomposition

**ATG** (Atomic Task Graph, Jul 2026): Organizes planning as an explicit DAG. Independent branches run in parallel. On failure, leverages graph evolution history to localize errors and repair only affected regions. Improved best baseline from 25.5 to 56.1 on Mistral-7B.

**VMAO** (Verified Multi-Agent Orchestration, Mar 2026): Plan-Execute-Verify-Replan framework with dependency-aware parallel execution and automatic context propagation from upstream results.

**GNNVerifier** (Mar 2026): Graph neural network that represents a plan as a directed graph with enriched attributes, generating node-, edge-, and graph-level scores for structural validation.

### 2.7 Reasoning Models

| Model | Architecture | Key Benchmarks | Pricing (Input/Output $/1M) | CoT Visibility |
|-------|-------------|----------------|:---------------------------:|:--------------:|
| **o3** | Hidden CoT via RL | AIME 96.7%, GPQA 87.7%, SWE-bench 69.1% | $2 / $8 | Hidden |
| **o4-mini** | Hidden CoT via RL | AIME 92.7%, Codeforces 2719 Elo | $0.55 / $2.20 | Hidden |
| **o3-pro** | Hidden CoT (max quality) | AIME 98%, GPQA 86% | $20 / $80 | Hidden |
| **DeepSeek-R1** | 671B MoE, pure RL training | MATH 97.3%, MMLU 90.8% | $0.55 / $2.19 | **Visible** |
| **QwQ-32B** | 32B dense, matches R1 671B | AIME 79.8%, MATH 94.3% | Self-hosted | **Visible** |
| **Claude ext. thinking** | Adaptive effort levels | GPQA 84.8% (at 64K budget) | Standard pricing + thinking tokens | Summarized |
| **Qwen3** | Hybrid `/think` toggle | Competitive across benchmarks | Self-hosted or API | **Visible** |

**Test-time compute scaling**: Rather than scaling model size and training data, scale inference-time compute. A smaller model with more thinking time can outperform a larger model with no thinking.

**Hidden reasoning tokens**: o-series models generate internal tokens billed at output rate but never returned. A 300-token visible answer can carry 2,000+ hidden reasoning tokens. Monitor the `reasoning_tokens` field in the API usage object.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Model: Reasoning Overhead per 1K Tasks

**Assumptions**: Average task requires 2K input tokens, 500-token answer. Reasoning models generate additional hidden tokens.

| Approach | Tokens/Task (total) | Cost/1K Tasks (Sonnet 4 $3/$15) | Cost/1K Tasks (o3 $2/$8) |
|----------|--------------------:|-------------------------------:|------------------------:|
| Direct prompting | 2,500 | **$13.50** | N/A |
| Zero-shot CoT | 5,000 (2× output) | **$21.00** | N/A |
| Self-consistency (N=5) | 12,500 (5× output) | **$43.50** | N/A |
| Reasoning model (o3) | 2,500 visible + 10K hidden reasoning | N/A | **$84.00** |
| Reasoning model (o4-mini) | 2,500 visible + 10K hidden | N/A | **$23.65** |

> Hidden reasoning tokens billed at output rate. o3 at $8/M output × 10K hidden tokens = $0.08/task. o4-mini at $2.20/M × 10K = $0.022/task. Actual hidden token count varies 2K–50K+ by task complexity.

**Cost control mechanisms**:
- `max_completion_tokens` caps worst-case spend per request.
- Batch API provides 50% discount on non-urgent workloads.
- Prompt caching reduces input costs by up to 90% for repeated prefixes.
- Hybrid routing: standard model first, escalate to reasoning model only on low confidence.

### 3.2 Latency SLA Targets

| Reasoning Approach | p50 | p95 | p99 | Mitigation |
|-------------------|-----|-----|-----|------------|
| Direct prompting | 300ms | 1.2s | 3s | Streaming; model routing |
| Zero-shot CoT | 600ms | 2.5s | 6s | Streaming; truncate if budget exceeded |
| Self-consistency (N=5) | 600ms (parallel) | 2.5s | 6s | Parallel sampling; early stopping (RASC) |
| Reasoning model (o3) | 5s | 30s | 120s | Set `max_completion_tokens`; use o4-mini for time-sensitive |
| ReAct (3 steps avg) | 2s | 8s | 20s | Limit max steps; parallel tool calls |
| Plan-and-execute (5 steps) | 5s | 20s | 60s | Parallel DAG branches; checkpoint per step |
| LATS (MCTS) | 30s | 120s | 300s | Limit tree depth and breadth; timeout budget |

**p50 mitigation**: Hybrid routing — classify task complexity, route simple tasks to direct prompting (300ms), complex to reasoning models.
**p95 mitigation**: Set hard token budgets per reasoning step. Use streaming to reduce perceived latency. RASC reduces self-consistency samples by ~70%.
**p99 mitigation**: Circuit breaker on reasoning models (Section 4.2). Timeout per step with graceful degradation — if reasoning exceeds budget, return best partial result.

### 3.3 Throughput & Back-Pressure

**Reasoning model rate limits**: o3/o4-mini have lower RPM limits than standard models due to higher compute per request. At 100K tasks/day with reasoning models, plan for:

```
max_concurrent_reasoning = min(
    api_rpm_limit / avg_reasoning_duration_minutes,
    budget_per_hour / avg_cost_per_reasoning_call,
    connection_pool_size
)
```

**Back-pressure mechanisms**:
- Token budget enforcement per task: halt execution if cumulative tokens exceed cap.
- Loop detection: if agent re-invokes same tool with semantically identical inputs, halt and escalate.
- Reasoning depth limit: cap MCTS tree depth and ToT breadth to bound compute.
- Queue-based admission control: if reasoning queue depth exceeds threshold, reject new complex tasks or downgrade to CoT.

### 3.4 NFR Trade-offs

| NFR | Standard + CoT | Reasoning Models | Plan-and-Execute |
|-----|---------------|-----------------|-----------------|
| **Availability** | High (single-call, fast failover) | Lower (long-running, hard to preempt) | Medium (multi-step, checkpoint-dependent) |
| **RPO** | N/A (stateless) | N/A (stateless per call) | Per-step checkpoint (Section 3.5) |
| **RTO** | <1s (retry with different model) | <1s (retry or route to fallback) | Seconds to minutes (resume from checkpoint) |
| **Compliance** | CoT visible but unfaithful (<20%) | Hidden CoT = audit gap (o3); visible = auditable (R1, Claude) | Full plan + execution trace = best auditability |

### 3.5 RPO/RTO for Multi-Step Plans

| Component | RPO | RTO | Mechanism |
|-----------|-----|-----|-----------|
| **Plan structure** | 0 (persisted on creation) | <1s (reload from plan store) | Stored in PostgreSQL / plan store |
| **Per-step checkpoint** | Per-step (0 data loss within completed steps) | <5s (resume from last checkpoint) | LangGraph checkpointer / Temporal history |
| **Reasoning trace** | Per-step | <1s (read from trace store) | Append-only log |
| **Tool outputs** | Per-call (logged before processing) | <1s | Idempotency key + cached result |

**Disaster recovery for interrupted plans**: On crash, the system reads the plan DAG and checkpoint store. Completed steps are skipped (their outputs are cached). The current in-progress step is retried from the beginning (idempotent tools) or from the last checkpoint (for multi-call steps). Remaining steps execute normally.

**Trade-off — checkpoint granularity**:
- **Node-level** (LangGraph): Every graph node writes a checkpoint. Finer = less re-work on recovery. But 50-step workflow generates 50 persisted states.
- **Explicit commit points**: Developer inserts saves at "safe" boundaries. Coarser = potentially more re-work. Easier to reason about.

**Key trade-offs**:

- **Accuracy vs. Cost**: Reasoning models improve accuracy by 15–30% on complex tasks but cost 3–10× more. For high-stakes, low-volume tasks (100 calls/day), the cost is justified. For bulk processing (100K calls/day), a 20–80% token increase is prohibitive.

- **Transparency vs. Performance**: Hidden CoT (o3) achieves the best benchmark scores but cannot be audited. Visible CoT (DeepSeek-R1, Claude) enables audit but may underperform. For regulated industries, transparency is a hard constraint — choose visible-CoT models even at accuracy cost.

- **Planning depth vs. Adaptability**: Deep up-front planning (full DAG decomposition) provides inspectability and parallel execution but is brittle when assumptions change. Shallow planning with per-step ReAct adaptation is more robust but harder to audit and parallelize.

- **Reasoning depth vs. Latency**: LATS achieves 94.4% on HumanEval but takes 30s+ per task. ReAct achieves lower accuracy in 2s. Production systems must choose based on latency SLAs and error cost.

---

## 4. Distributed Resilience & Security

### 4.1 Plan Persistence and Crash Recovery

**LangGraph**: Saves graph state at each superstep. Checkpoints capture message history, current node, tool outputs, metadata. PostgresSaver recommended for production.

**Temporal**: Each Activity is recorded in Event History. Workflow code is deterministic — on recovery, it replays against history, skipping completed Activities. Append-only, compacted history.

**Idempotency requirement**: A checkpoint tells the runtime where execution stopped. It does not know whether a side effect (email sent, payment charged) was completed. If the agent crashes after a tool call but before checkpointing, it retries on recovery. Safe only if tool calls are idempotent. For non-idempotent operations: log the call ID before execution, check on retry, skip if already executed.

### 4.2 Circuit Breaker Pattern for Reasoning Systems

#### 4.2.1 State Machine

```
                    success
              ┌───────────────┐
              │               │
              ▼               │
         ┌─────────┐    ┌────┴─────┐    ┌─────────────┐
         │ CLOSED  │───▶│  OPEN    │───▶│ HALF-OPEN   │
         │         │    │          │    │             │
         │ Normal  │    │ Downgrade│    │ Probe with  │
         │ reasoning│    │ to CoT / │    │ 2 test      │
         │         │    │ standard │    │ tasks       │
         └─────────┘    └──────────┘    └─────────────┘
              ▲          │       ▲            │
              │          │       │            │
              │          │       └────────────┘
              │          │        probe fails
              │     after 60s
              │     recovery timeout
              │     (60s → 120s → 240s exponential)
              │
              └──────────────────────────────┘
                    2/2 probes succeed
```

**Thresholds**:
- **Closed → Open**: 5 reasoning timeouts or API errors within 120s window.
- **Open duration**: 60s recovery timeout with exponential backoff (longer than standard because reasoning APIs recover slower).
- **Half-Open → Closed**: 2 consecutive successful probe tasks.

#### 4.2.2 Per-Component Breaker Applications

| Component | Failure Type | Class | Fallback Strategy |
|-----------|-------------|-------|-------------------|
| Reasoning model API (o3) | Timeout / 429 / 500 | **Transient** | Downgrade to o4-mini → standard model + CoT |
| Extended thinking (Claude) | Budget exhaustion / timeout | **Transient** | Reduce effort level (max → high → standard) |
| Plan decomposition | LLM generates invalid DAG | **Transient** | Retry with simplified prompt; fall back to linear plan |
| ReAct step | Loop detected (same tool call 3×) | **Permanent** (design) | Halt step; escalate to human; log for plan revision |
| Tool execution | Tool returns null/malformed | **Transient** | Schema-check; retry once; if still bad, mark step failed and replan |
| MCTS expansion | Tree depth exceeds budget | **Transient** | Return best node found so far; truncate search |

### 4.3 Failure Taxonomy

| Failure | Class | Detection | Mitigation |
|---------|-------|-----------|------------|
| Reasoning loop ($47K incident) | **Permanent** (design) | Step count + output similarity monitoring | Hard iteration limit; per-task token budget; loop detection |
| Plan-reality mismatch | **Transient** | Tool not found; capability manifest check | Validate plan against tool manifest before execution |
| Token budget exhaustion | **Transient** | Cumulative token counter ≥ cap | Per-step and per-task caps; context compression |
| Context rot (65% of enterprise failures) | **Transient** | Quality degradation before hard limit | Summarize intermediates; sliding window; re-inject goal |
| Self-consistency failure | **Transient** | Different decompositions on same input | Temperature 0 for planning; structural validation |
| Reasoning hallucination | **Transient** | Faithfulness judge; counterfactual testing | Independent verification; functional attention rescaling |
| Silent tool failure | **Transient** | Schema-check on tool returns | Reject null/empty; retry once |
| Goal drift | **Transient** | Original goal divergence metric | Periodically re-inject goal into context |
| CoT unfaithfulness (<20%) | **Permanent** (architecture) | Faithfulness scoring (Anthropic research) | Treat CoT as hypothesis; independent verification |
| Ceremonialization ("verified" but didn't) | **Permanent** (architecture) | Functional validation (run the tests, not claim to) | Require executable verification, not verbal claims |

### 4.3.1 Idempotency in Plan Execution

For durable plan execution (Temporal, LangGraph checkpoints), steps may be replayed after crashes. Steps with side effects must be idempotent:

```
Step execution:  send_email(to="user@co.com", subject="Report")
                                    │
                          ┌─────────▼──────────┐
                          │ Idempotency Guard   │
                          │ key = hash(plan_id  │
                          │   + step_index      │
                          │   + tool + args)    │
                          └─────────┬──────────┘
                                    │
                     ┌──────────────▼──────────────┐
                     │ IF key in executed_steps:    │
                     │   RETURN cached_result       │
                     │ ELSE:                        │
                     │   execute + store result     │
                     └─────────────────────────────┘
```

### 4.3.2 Poison-Pill Detection in Reasoning Chains

A poison pill in reasoning is an input that causes the agent to reason indefinitely, hallucinate, or execute harmful actions — e.g., a task description containing hidden prompt injection that hijacks the planning process.

**Detection heuristics**:
- Step count exceeds 3× the expected maximum for the task type.
- Token consumption exceeds 5× the budget without progress toward completion.
- Same tool invoked 3+ times with semantically identical arguments.
- Plan revision count exceeds threshold (agent keeps replanning without executing).

**Quarantine**: Halt execution. Persist current state for forensic analysis. Alert ops. Route task to human reviewer.

### 4.4 Enterprise Security Boundaries

#### 4.4.1 Reasoning Transparency and the Audit Gap

CoT faithfulness scores are below 20% (Anthropic research). Three mechanistically distinct behaviors are externally indistinguishable:
1. **Genuine reasoning**: Stated steps causally connected to the conclusion.
2. **Confabulation**: Answer determined first; reasoning constructed afterward to justify it.
3. **Sycophantic backward reasoning**: Model works backward from a human's preferred answer.

**Reasoning model audit matrix**:

| Model | CoT Visibility | Auditability | Compliance Risk |
|-------|---------------|:------------:|:---------------:|
| o3/o4-mini | Hidden | None — reasoning is invisible | **High** — conflicts with EU AI Act Art. 14 |
| DeepSeek-R1 | Fully visible | High — full trace debuggable | Low |
| Claude extended thinking | Summarized | Medium — key steps visible | Medium |
| Qwen3 (with `/think`) | Fully visible | High | Low |
| Standard model + CoT | Visible but unfaithful | Low — visible ≠ faithful | Medium |

**Recommendation**: For compliance-critical applications, prefer models with visible reasoning (DeepSeek-R1, Qwen3, Claude extended thinking) over hidden CoT (o3). Treat any visible CoT as a hypothesis, not evidence of actual computation.

#### 4.4.2 Zero-Trust Plan Execution

1. **Plan approval gates**: High-stakes plans presented to human reviewers before execution. Risk-tiered: Tier 1 (auto — read-only ops), Tier 3 (exception — flag on low confidence), Tier 4 (full gate — every instance reviewed for payments, legal, regulatory submissions).

2. **Injection detection in reasoning chains**: Prompt injection (OWASP LLM01:2025, 50–84% success rate) can hijack the planning process. Real-world: EchoLeak (CVE-2025-32711, CVSS 9.3) — zero-click injection in M365 Copilot exfiltrated files. Defense: handle privileged functions in code, not via the model; use a guardian validation model; require human approval for irreversible actions.

3. **Least privilege per plan step**: Each step receives only the tools and permissions it needs. A "research" step gets read-only tools; a "publish" step gets write access with human approval.

4. **PII filtering**: Reasoning traces may contain PII from tool outputs. Filter reasoning trace logs before storage. Redact PII in persisted plans and checkpoints.

5. **Immutable execution audit**: Every plan step, tool call, reasoning trace, and replan decision logged to WORM storage with timestamps, model IDs, and user identity. Chain-of-custody for SOC2/HIPAA/GDPR.

---

## 5. Production Enterprise Code

### 5.1 Plan-and-Execute with DAG Decomposition and Replanning

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class PlanStep:
    id: str
    description: str
    tool: str
    args: dict
    depends_on: list[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    output: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ExecutionPlan:
    goal: str
    steps: list[PlanStep]
    replan_count: int = 0
    max_replans: int = 3


class PlanAndExecuteAgent:
    def __init__(self, llm_client, tool_registry, checkpointer,
                 max_steps: int = 20, max_tokens_per_step: int = 4000):
        self.llm = llm_client
        self.tools = tool_registry
        self.checkpointer = checkpointer
        self.max_steps = max_steps
        self.max_tokens_per_step = max_tokens_per_step

    async def run(self, goal: str, session_id: str) -> dict:
        existing = await self.checkpointer.load(session_id)
        if existing:
            plan = existing
        else:
            plan = await self._decompose(goal)
            await self.checkpointer.save(session_id, plan)

        while True:
            ready_steps = self._get_ready_steps(plan)
            if not ready_steps:
                break

            for step in ready_steps:
                step.status = StepStatus.RUNNING
                try:
                    result = await self._execute_step(step, plan)
                    step.output = result
                    step.status = StepStatus.COMPLETED
                except Exception as e:
                    step.error = str(e)
                    step.status = StepStatus.FAILED
                    if await self._should_replan(plan, step):
                        plan = await self._replan(plan, step)
                await self.checkpointer.save(session_id, plan)

        return {
            "goal": plan.goal,
            "completed": all(s.status == StepStatus.COMPLETED for s in plan.steps),
            "steps": [
                {"id": s.id, "status": s.status.value, "output": s.output}
                for s in plan.steps
            ],
        }

    async def _decompose(self, goal: str) -> ExecutionPlan:
        available_tools = self.tools.list_tools()
        response = self.llm.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": (
                f"Decompose this goal into a plan of concrete steps.\n"
                f"Available tools: {available_tools}\n"
                f"Goal: {goal}\n\n"
                f"Return a JSON array of steps, each with: id, description, "
                f"tool, args, depends_on (list of step IDs that must complete first).\n"
                f"Maximize parallelism — only add dependencies where data flow requires it."
            )}],
        )
        import json
        steps_data = json.loads(response.content[0].text)
        steps = [PlanStep(**s) for s in steps_data]
        if len(steps) > self.max_steps:
            raise ValueError(f"Plan has {len(steps)} steps, max is {self.max_steps}")
        return ExecutionPlan(goal=goal, steps=steps)

    def _get_ready_steps(self, plan: ExecutionPlan) -> list[PlanStep]:
        ready = []
        for step in plan.steps:
            if step.status != StepStatus.PENDING:
                continue
            deps_met = all(
                self._get_step(plan, dep_id).status == StepStatus.COMPLETED
                for dep_id in step.depends_on
            )
            if deps_met:
                ready.append(step)
        return ready

    async def _execute_step(self, step: PlanStep, plan: ExecutionPlan) -> str:
        dep_context = "\n".join(
            f"Step {dep_id} output: {self._get_step(plan, dep_id).output}"
            for dep_id in step.depends_on
        )
        tool = self.tools.get(step.tool)
        result = await tool.execute(
            **step.args,
            context=dep_context,
            max_tokens=self.max_tokens_per_step,
        )
        if result is None or result == "":
            raise ValueError(f"Tool {step.tool} returned empty result")
        return result

    async def _should_replan(self, plan: ExecutionPlan, failed_step: PlanStep) -> bool:
        if plan.replan_count >= plan.max_replans:
            return False
        response = self.llm.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": (
                f"A plan step failed. Should we replan?\n"
                f"Goal: {plan.goal}\n"
                f"Failed step: {failed_step.description}\n"
                f"Error: {failed_step.error}\n"
                f"Answer 'yes' or 'no'."
            )}],
        )
        return response.content[0].text.strip().lower() == "yes"

    async def _replan(self, plan: ExecutionPlan, failed_step: PlanStep) -> ExecutionPlan:
        completed = [s for s in plan.steps if s.status == StepStatus.COMPLETED]
        completed_summary = "\n".join(
            f"- {s.description}: {s.output[:200]}" for s in completed
        )
        new_plan = await self._decompose(
            f"{plan.goal}\n\nAlready completed:\n{completed_summary}\n\n"
            f"Failed step: {failed_step.description} — Error: {failed_step.error}\n"
            f"Create a revised plan starting from the current state."
        )
        new_plan.replan_count = plan.replan_count + 1
        for completed_step in completed:
            for new_step in new_plan.steps:
                if new_step.description == completed_step.description:
                    new_step.status = StepStatus.COMPLETED
                    new_step.output = completed_step.output
        return new_plan

    def _get_step(self, plan: ExecutionPlan, step_id: str) -> PlanStep:
        return next(s for s in plan.steps if s.id == step_id)
```

### 5.2 Hybrid Complexity Router

```python
from enum import Enum
from dataclasses import dataclass


class Complexity(Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


@dataclass
class RoutingDecision:
    complexity: Complexity
    model: str
    reasoning_strategy: str
    max_tokens: int
    estimated_cost: float


class ComplexityRouter:
    ROUTES = {
        Complexity.SIMPLE: {
            "model": "claude-haiku-4-5-20251001",
            "strategy": "direct",
            "max_tokens": 1000,
            "cost_per_1k": 0.50,
        },
        Complexity.MEDIUM: {
            "model": "claude-sonnet-4-20250514",
            "strategy": "zero_shot_cot",
            "max_tokens": 4000,
            "cost_per_1k": 21.00,
        },
        Complexity.COMPLEX: {
            "model": "claude-opus-4-20250918",
            "strategy": "extended_thinking",
            "max_tokens": 16000,
            "cost_per_1k": 84.00,
        },
    }

    def __init__(self, classifier_client):
        self.classifier = classifier_client

    async def route(self, task: str) -> RoutingDecision:
        response = self.classifier.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            messages=[{"role": "user", "content": (
                f"Classify this task's reasoning complexity as "
                f"'simple', 'medium', or 'complex'.\n"
                f"Simple: pattern-matching, lookup, summarization.\n"
                f"Medium: multi-step analysis, comparison, structured output.\n"
                f"Complex: mathematical proof, architectural design, "
                f"multi-constraint optimization.\n\nTask: {task}"
            )}],
        )
        complexity = Complexity(response.content[0].text.strip().lower())
        route = self.ROUTES[complexity]
        return RoutingDecision(
            complexity=complexity,
            model=route["model"],
            reasoning_strategy=route["strategy"],
            max_tokens=route["max_tokens"],
            estimated_cost=route["cost_per_1k"],
        )
```

### 5.3 Loop Detection Guard

```python
import hashlib
from collections import deque


class LoopDetector:
    def __init__(self, max_steps: int = 25, similarity_window: int = 5,
                 max_same_tool_calls: int = 3):
        self.max_steps = max_steps
        self.similarity_window = similarity_window
        self.max_same_tool = max_same_tool_calls
        self.step_count = 0
        self.recent_calls: deque[str] = deque(maxlen=similarity_window)
        self.tool_call_counts: dict[str, int] = {}

    def check(self, tool_name: str, tool_args: dict) -> bool:
        self.step_count += 1
        if self.step_count > self.max_steps:
            raise ReasoningLoopError(
                f"Max steps ({self.max_steps}) exceeded"
            )
        call_hash = hashlib.sha256(
            f"{tool_name}:{sorted(tool_args.items())}".encode()
        ).hexdigest()[:12]
        if call_hash in self.recent_calls:
            self.tool_call_counts[call_hash] = (
                self.tool_call_counts.get(call_hash, 1) + 1
            )
            if self.tool_call_counts[call_hash] >= self.max_same_tool:
                raise ReasoningLoopError(
                    f"Tool {tool_name} called {self.max_same_tool}× "
                    f"with identical args"
                )
        self.recent_calls.append(call_hash)
        return True


class ReasoningLoopError(Exception):
    pass
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Hybrid Reasoning System for a Financial Services Platform

**Business context**: A financial services company processes 50K daily analyst queries spanning simple lookups ("What's AAPL's P/E ratio?"), medium analysis ("Compare AAPL vs MSFT earnings trends"), and complex reasoning ("Design a hedging strategy for a $100M portfolio exposed to interest rate risk"). Requirements: sub-2s latency for simple queries, <30s for complex reasoning, $50K/month total AI budget, SOC2 audit trail, and visible reasoning traces for all investment recommendations.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                     QUERY PIPELINE                                       │
 │                                                                          │
 │  Query ──▶ ┌──────────────┐  ┌────────────────────────────────────┐     │
 │            │ Complexity   │  │         MODEL ROUTING              │     │
 │            │ Classifier   │  │                                    │     │
 │            │ (Haiku)      │──▶│  Simple ──▶ Haiku (direct)       │     │
 │            │              │  │  Medium ──▶ Sonnet (CoT)          │     │
 │            │              │  │  Complex ──▶ Opus (ext. thinking) │     │
 │            └──────────────┘  └────────────────────────────────────┘     │
 │                                         │                               │
 │                              ┌──────────▼──────────┐                    │
 │                              │ Reasoning Engine     │                    │
 │                              │ - Direct / CoT /     │                    │
 │                              │   Extended thinking  │                    │
 │                              │ - Tool dispatch      │                    │
 │                              │ - Loop detection     │                    │
 │                              └──────────┬──────────┘                    │
 │                                         │                               │
 │                              ┌──────────▼──────────┐                    │
 │                              │ Compliance Layer     │                    │
 │                              │ - Reasoning trace    │                    │
 │                              │   persistence        │                    │
 │                              │ - Source attribution  │                    │
 │                              │ - HITL for recs >$1M │                    │
 │                              └─────────────────────┘                    │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Single Reasoning Model for All | B: Tiered Complexity Routing (Recommended) | C: Always CoT + Self-Consistency |
|-----------|----------------------------------|--------------------------------------------|--------------------------------|
| **Cost at 50K queries/day** | ⬛⬜⬜ — All queries through Opus/o3 = ~$130K/month | ⬛⬛⬛ — 70% simple (Haiku), 25% medium (Sonnet), 5% complex (Opus) = ~$35K/month | ⬛⬛⬜ — CoT doubles token usage; self-consistency 5× = ~$65K/month |
| **Latency (simple queries)** | ⬛⬜⬜ — 5–30s even for P/E lookups | ⬛⬛⬛ — <2s for simple (Haiku direct) | ⬛⬛⬜ — 600ms–2.5s (CoT overhead on every query) |
| **Accuracy (complex reasoning)** | ⬛⬛⬛ — Best reasoning model on every query | ⬛⬛⬛ — Complex queries get full reasoning | ⬛⬛⬜ — CoT + SC helps but below reasoning model quality |
| **Audit compliance** | ⬛⬜⬜ — o3 hidden CoT = no audit trail | ⬛⬛⬛ — Claude extended thinking = summarized trace; visible | ⬛⬛⬛ — Full visible CoT for all queries |
| **Operational simplicity** | ⬛⬛⬛ — One model, one routing rule | ⬛⬛⬜ — Classifier + 3 model configs + routing logic | ⬛⬛⬛ — One strategy, no routing |

**Recommended approach**: **B (Tiered Complexity Routing)**.

**Decision rationale**: The $50K/month budget eliminates Option A (single reasoning model at $130K/month). Option C (always CoT + self-consistency) stays within budget at $65K/month but wastes reasoning on simple lookups that comprise 70% of volume. Tiered routing achieves the best cost-accuracy balance at ~$35K/month — simple queries get sub-2s responses via Haiku, while the 5% of complex queries that drive investment decisions get full extended thinking with visible reasoning traces. The audit requirement eliminates o3 (hidden CoT) — Claude extended thinking provides summarized traces that satisfy SOC2 reviewers.

### 6.2 Scenario: Autonomous Code Review Agent with Multi-Step Reasoning

**Business context**: An engineering platform runs an autonomous code review agent processing 2,000 PRs/day across 50 repositories. The agent must: analyze diffs for bugs, security issues, and style violations; generate fix suggestions with explanations; and escalate high-risk changes to senior engineers. Requirements: <5 min per PR, <3% false positive rate, reasoning trace for every finding, and human approval for any suggested auto-fix.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                     CODE REVIEW PIPELINE                                 │
 │                                                                          │
 │  PR Diff ──▶ ┌──────────────┐ ──▶ ┌──────────────┐ ──▶ ┌────────────┐ │
 │              │ Plan: Review │     │ Execute DAG  │     │ Synthesize │ │
 │              │ Decomposition│     │              │     │ + Report   │ │
 │              │              │     │  ┌─────────┐ │     │            │ │
 │              │ File-level   │     │  │Bugs     │ │     │ Findings   │ │
 │              │ DAG with     │     │  ├─────────┤ │     │ ranked by  │ │
 │              │ parallel     │     │  │Security │ │     │ severity   │ │
 │              │ branches     │     │  ├─────────┤ │     │            │ │
 │              │              │     │  │Style    │ │     │ Reasoning  │ │
 │              │              │     │  ├─────────┤ │     │ trace per  │ │
 │              │              │     │  │Perf     │ │     │ finding    │ │
 │              │              │     │  └─────────┘ │     │            │ │
 │              └──────────────┘     └──────────────┘     └──────┬─────┘ │
 │                                                               │       │
 │                                                    ┌──────────▼─────┐ │
 │                                                    │ Adversarial    │ │
 │                                                    │ Verification   │ │
 │                                                    │ - Refute each  │ │
 │                                                    │   finding      │ │
 │                                                    │ - Kill false   │ │
 │                                                    │   positives    │ │
 │                                                    └──────────┬─────┘ │
 │                                                               │       │
 │                                          ┌────────────────────▼─────┐ │
 │                                          │ HITL Gate               │ │
 │                                          │ - Auto-approve comments │ │
 │                                          │ - Human-approve fixes   │ │
 │                                          │ - Escalate high-risk    │ │
 │                                          └──────────────────────────┘ │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Single-Pass Review (Sonnet + CoT) | B: DAG Decomposition + Adversarial Verify (Recommended) | C: Reasoning Model per File (o3) |
|-----------|-------------------------------------|-------------------------------------------------------|--------------------------------|
| **False positive rate** | ⬛⬜⬜ — ~15% FP (no verification step) | ⬛⬛⬛ — <3% FP (adversarial refutation kills weak findings) | ⬛⬛⬜ — ~8% FP (better reasoning but still no verification) |
| **Latency per PR** | ⬛⬛⬛ — ~1 min (single pass) | ⬛⬛⬜ — ~3 min (parallel review + serial verification) | ⬛⬜⬜ — ~10 min (o3 reasoning time per file) |
| **Cost per PR** | ⬛⬛⬛ — ~$0.10 (single Sonnet call) | ⬛⬛⬜ — ~$0.50 (multiple review + verify calls) | ⬛⬜⬜ — ~$2.00 (o3 reasoning tokens per file) |
| **Reasoning quality** | ⬛⬛⬜ — Catches obvious bugs; misses subtle issues | ⬛⬛⬛ — Parallel dimensions catch more; verification confirms | ⬛⬛⬛ — Best per-file reasoning |
| **Audit trail** | ⬛⬛⬜ — Single CoT per review | ⬛⬛⬛ — Per-dimension reasoning + verification verdict | ⬛⬜⬜ — Hidden CoT; no audit |
| **Scalability (2K PRs/day)** | ⬛⬛⬛ — $200/day | ⬛⬛⬛ — $1,000/day (within budget) | ⬛⬜⬜ — $4,000/day |

**Recommended approach**: **B (DAG Decomposition + Adversarial Verification)**.

**Decision rationale**: The <3% false positive requirement is the critical constraint — developers ignore review bots with high FP rates. Single-pass review (Option A) at ~15% FP violates this hard requirement. The adversarial verification step (each finding is independently challenged by a skeptic agent prompted to refute it) is the key mechanism that kills weak findings and achieves <3% FP. The DAG decomposition enables parallel review across dimensions (bugs, security, style, performance), completing within the 5-min latency budget. Reasoning models (Option C) provide better per-file analysis but the hidden CoT prevents audit trail generation, and at $4K/day for 2K PRs, the cost is 4× higher than the DAG approach for marginal accuracy improvement.

---

*Module 08 complete. Covers reasoning paradigms from CoT to LATS, reasoning models, structured planning with DAG decomposition, and production deployment patterns for hybrid reasoning systems.*
