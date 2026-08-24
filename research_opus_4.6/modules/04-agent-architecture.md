# Module 04: Agent Architecture -- Execution Patterns, Multi-Agent Orchestration, Resilience, and Enterprise Governance

**Scope**: Agent execution patterns (ReAct, Plan-and-Execute, Reflexion, LATS), agent loop architectures (single-agent through cyclic graphs), state management and checkpointing, durable execution, distributed resilience, failure taxonomy, enterprise security and compliance, production code patterns, and system design scenarios.
**Prerequisite**: Module 01 (LLM Foundations), Module 02 (Context Engineering), Module 03 (Tool Use), familiarity with Python async, state machines, distributed systems basics.
**Last updated**: 2026-08-21 | **Sources consulted**: 48

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
 ┌─────────────────────────────────────────────────────────────────────────────────────────┐
 │                              CONTROL PLANE                                              │
 │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────────┐  │
 │  │  Agent Gateway   │  │  Agent Registry  │  │  Policy Engine  │  │  Tenant Mgr      │  │
 │  │  - OAuth2/mTLS   │  │  - Pattern type  │  │  - HITL rules   │  │  - Namespace     │  │
 │  │  - Rate limits   │  │  - Capability    │  │  - Budget caps   │  │  - Credentials   │  │
 │  │  - Correlation   │  │    manifests     │  │  - Escalation    │  │  - Quota enforce │  │
 │  │    ID injection  │  │  - Health / ver. │  │    thresholds   │  │  - Isolation     │  │
 │  └──────┬──────────┘  └──────┬──────────┘  └──────┬──────────┘  └──────┬───────────┘  │
 │         │                    │                     │                     │               │
 └─────────┼────────────────────┼─────────────────────┼─────────────────────┼───────────────┘
           │                    │                     │                     │
 ┌─────────┼────────────────────┼─────────────────────┼─────────────────────┼───────────────┐
 │         ▼                    ▼                     ▼                     ▼               │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐  │
 │  │                     AGENT EXECUTION ENGINE                          DATA PLANE     │  │
 │  │                                                                                    │  │
 │  │  ┌──────────────────────────────────────────────────────────────────────────────┐  │  │
 │  │  │  PATTERN SELECTOR (routes to appropriate execution pattern)                 │  │  │
 │  │  │  ┌────────────────┐ ┌────────────────┐ ┌──────────────┐ ┌───────────────┐  │  │  │
 │  │  │  │ ReAct          │ │ Plan-and-      │ │ Reflexion    │ │ LATS          │  │  │  │
 │  │  │  │ Thought→       │ │ Execute        │ │ Actor→       │ │ Select→       │  │  │  │
 │  │  │  │ Action→        │ │ Planner→       │ │ Evaluator→   │ │ Expand→       │  │  │  │
 │  │  │  │ Observation    │ │ Executor→      │ │ Self-Reflect→│ │ Evaluate→     │  │  │  │
 │  │  │  │ (loop until    │ │ (Re-)Planner   │ │ Episodic Mem │ │ Simulate→     │  │  │  │
 │  │  │  │  done/cap)     │ │                │ │              │ │ Backpropagate │  │  │  │
 │  │  │  └────────────────┘ └────────────────┘ └──────────────┘ └───────────────┘  │  │  │
 │  │  └──────────────────────────────┬───────────────────────────────────────────────┘  │  │
 │  │                                 ▼                                                  │  │
 │  │  ┌──────────────────────────────────────────────────────────────────────────────┐  │  │
 │  │  │  ORCHESTRATION LAYER (multi-agent topology)                                 │  │  │
 │  │  │  ┌────────────────┐ ┌────────────────┐ ┌──────────────┐ ┌───────────────┐  │  │  │
 │  │  │  │ Single-Agent   │ │ Router-Based   │ │ DAG Workflow │ │ Cyclic Graph  │  │  │  │
 │  │  │  │ Loop           │ │ Branching      │ │ (parallel    │ │ (LangGraph    │  │  │  │
 │  │  │  │ (ReAct while-  │ │ (intent →      │ │  fan-out/in, │ │  StateGraph,  │  │  │  │
 │  │  │  │  loop, OpenAI  │ │  specialist    │ │  LLMCompiler │ │  conditional  │  │  │  │
 │  │  │  │  Runner.run()) │ │  dispatch)     │ │  3.6x speed) │ │  edges, sub-  │  │  │  │
 │  │  │  │                │ │                │ │              │ │  graphs)      │  │  │  │
 │  │  │  └────────────────┘ └────────────────┘ └──────────────┘ └───────────────┘  │  │  │
 │  │  │                                                                             │  │  │
 │  │  │  Multi-Agent Patterns:                                                      │  │  │
 │  │  │  ┌────────────────┐ ┌────────────────┐ ┌──────────────┐ ┌───────────────┐  │  │  │
 │  │  │  │ Supervisor/    │ │ Sequential     │ │ Parallel     │ │ Evaluator-    │  │  │  │
 │  │  │  │ Worker         │ │ Pipeline       │ │ Fan-Out/In   │ │ Optimizer     │  │  │  │
 │  │  │  │ (decompose →   │ │ (A → B → C →   │ │ (N workers,  │ │ Loop          │  │  │  │
 │  │  │  │  dispatch →    │ │  state pass)   │ │  reducer fn, │ │ (produce →    │  │  │  │
 │  │  │  │  synthesize)   │ │                │ │  ~75% time ↓)│ │  critique →   │  │  │  │
 │  │  │  │                │ │                │ │              │ │  iterate)     │  │  │  │
 │  │  │  └────────────────┘ └────────────────┘ └──────────────┘ └───────────────┘  │  │  │
 │  │  └──────────────────────────────┬───────────────────────────────────────────────┘  │  │
 │  │                                 ▼                                                  │  │
 │  │  ┌──────────────────────────────────────────────────────────────────────────────┐  │  │
 │  │  │  STATE MANAGEMENT LAYER                                                     │  │  │
 │  │  │  ┌────────────────┐ ┌────────────────┐ ┌──────────────┐ ┌───────────────┐  │  │  │
 │  │  │  │ Conversation   │ │ Tool State     │ │ Planning     │ │ Memory State  │  │  │  │
 │  │  │  │ State          │ │                │ │ State        │ │               │  │  │  │
 │  │  │  │ - Message      │ │ - Intermediate │ │ - Current    │ │ - Short-term  │  │  │  │
 │  │  │  │   history      │ │   results      │ │   plan       │ │   (checkpoint │  │  │  │
 │  │  │  │ - Grows O(N)   │ │ - Data         │ │ - Completed/ │ │    per run)   │  │  │  │
 │  │  │  │   per turn     │ │   products     │ │   pending    │ │ - Long-term   │  │  │  │
 │  │  │  │ - Context      │ │ - Replay       │ │   steps      │ │   (cross-run  │  │  │  │
 │  │  │  │   exhaustion   │ │   artifacts    │ │ - Replan     │ │    store)     │  │  │  │
 │  │  │  │   driver       │ │                │ │   history    │ │               │  │  │  │
 │  │  │  └────────────────┘ └────────────────┘ └──────────────┘ └───────────────┘  │  │  │
 │  │  └──────────────────────────────┬───────────────────────────────────────────────┘  │  │
 │  │                                 ▼                                                  │  │
 │  │  ┌──────────────────────────────────────────────────────────────────────────────┐  │  │
 │  │  │  CONTROL FLOW PRIMITIVES                                                    │  │  │
 │  │  │  ┌────────────────┐ ┌────────────────┐ ┌──────────────┐ ┌───────────────┐  │  │  │
 │  │  │  │ Conditional    │ │ Parallel       │ │ HITL         │ │ Sub-Agent     │  │  │  │
 │  │  │  │ Branching      │ │ Fan-Out/In     │ │ Interrupts   │ │ Delegation    │  │  │  │
 │  │  │  │ - State-based  │ │ - LangGraph    │ │ - Pause/save │ │ - Clean ctx   │  │  │  │
 │  │  │  │   edge routing │ │   Send API     │ │ - Non-block  │ │   windows     │  │  │  │
 │  │  │  │ - OpenAI       │ │ - Worker-per-  │ │   wait       │ │ - 1-2K token  │  │  │  │
 │  │  │  │   handoffs     │ │   input state  │ │ - Resume     │ │   summaries   │  │  │  │
 │  │  │  │ - ADK dynamic  │ │ - Race cond.   │ │   from exact │ │ - Parallel    │  │  │  │
 │  │  │  │   delegation   │ │   scale N(N-1) │ │   pause pt   │ │   exploration │  │  │  │
 │  │  │  │                │ │   /2           │ │              │ │               │  │  │  │
 │  │  │  └────────────────┘ └────────────────┘ └──────────────┘ └───────────────┘  │  │  │
 │  │  └──────────────────────────────────────────────────────────────────────────────┘  │  │
 │  │                                                                                    │  │
 │  └────────────────────────────────────────────────────────────────────────────────────┘  │
 │                                                                                          │
 └──────────────────────────────────────────────────────────────────────────────────────────┘

 ┌──────────────────────────────────────────────────────────────────────────────────────────┐
 │                         PERSISTENCE LAYER                                                │
 │  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  ┌────────────────────────┐ │
 │  │ Checkpointers  │  │ Durable Exec   │  │ Event Store    │  │ Long-Term Memory       │ │
 │  │ - MemorySaver  │  │ - Temporal     │  │ - Kafka/NATS   │  │ - PostgreSQL           │ │
 │  │   (dev)        │  │   Workflows +  │  │ - Append-only  │  │ - Vector store         │ │
 │  │ - SqliteSaver  │  │   Activities   │  │   immutable    │  │ - Cross-run knowledge  │ │
 │  │   (single-node)│  │ - Restate      │  │   log          │  │ - LangGraph Store API  │ │
 │  │ - PostgresSaver│  │ - Inngest      │  │ - Event        │  │                        │ │
 │  │   (multi-node) │  │ - DBOS         │  │   sourcing +   │  │ Object Store (S3)      │ │
 │  │ - DynamoDB +S3 │  │ - Saga/Comp.   │  │   rehydration  │  │ - WORM audit logs      │ │
 │  │   (AWS scale)  │  │   patterns     │  │ - Replay       │  │ - Agent trajectories   │ │
 │  │                │  │ - Idempotency  │  │   debugging    │  │ - 6-24 month retention │ │
 │  └────────────────┘  └────────────────┘  └────────────────┘  └────────────────────────┘ │
 └──────────────────────────────────────────────────────────────────────────────────────────┘

 ┌──────────────────────────────────────────────────────────────────────────────────────────┐
 │                         TELEMETRY & OBSERVABILITY                                        │
 │  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  ┌────────────────────────┐ │
 │  │ OpenTelemetry  │  │ Trajectory     │  │ Cost Tracker   │  │ Circuit Breaker        │ │
 │  │ - W3C trace-   │  │ Audit Log      │  │ - Per-agent    │  │ Monitor                │ │
 │  │   context      │  │ - Decision     │  │ - Per-task     │  │ - Closed/Open/         │ │
 │  │ - Distributed  │  │   chain record │  │ - Per-tenant   │  │   Half-Open states     │ │
 │  │   tracing      │  │ - Tool I/O     │  │ - Token        │  │ - Iteration cap        │ │
 │  │ - Arize Phoenix│  │ - State trans. │  │   attribution  │  │   enforcement          │ │
 │  │ - LangSmith    │  │ - EU AI Act    │  │ - Cost/outcome │  │ - No-progress          │ │
 │  │ - p50/p95/p99  │  │   Art.12 compl │  │   regression   │  │   detection            │ │
 │  └────────────────┘  └────────────────┘  └────────────────┘  └────────────────────────┘ │
 └──────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

A user request enters the **Agent Gateway**, which authenticates via OAuth2/mTLS, attaches a correlation ID, resolves the tenant namespace, and enforces rate limits. The **Policy Engine** determines iteration caps, token budgets, and HITL escalation thresholds for this request class.

The **Pattern Selector** routes to the appropriate execution pattern. For a straightforward tool-assisted query, it selects **ReAct** (single-agent while-loop). For a multi-step structured task (e.g., 8-step financial analysis), it selects **Plan-and-Execute**, where a frontier model generates the full plan and a cheaper executor model handles each step. For tasks requiring quality guarantees over speed, **Reflexion** wraps the base pattern with an evaluator-critic cycle. For problems with large solution spaces (code generation with multiple valid approaches), **LATS** explores candidate paths via tree search.

Within the **Orchestration Layer**, the request maps to a topology. A single-agent loop handles the common case. A **Router** dispatches to specialists for multi-domain queries. A **DAG Workflow** parallelizes independent subtasks (LLMCompiler-style, 3.6x speedup via parallel execution). A **Cyclic Graph** (LangGraph StateGraph) handles workflows requiring loops, conditional branching, and sub-agent delegation within a single compiled graph.

At each step, the **State Management Layer** maintains four state categories: conversation state (message history, grows linearly per turn), tool state (intermediate results and data products), planning state (current plan, completed/pending steps, replan history), and memory state (short-term checkpoints within this run, long-term cross-run knowledge). State is defined as a TypedDict or Pydantic model; updates are incremental (not full overwrites), enabling safe parallel node execution.

**Control flow primitives** govern execution: conditional edges route based on state (LangGraph) or handoffs (OpenAI Agents SDK). The **Send API** (LangGraph) dynamically creates parallel workers with specific inputs, each writing to shared state via reducer functions. **HITL interrupts** pause execution, persist state without blocking threads, and resume from the exact pause point after human input. **Sub-agent delegation** spawns focused agents with clean context windows that return condensed summaries (1,000-2,000 tokens) to the parent orchestrator.

The **Persistence Layer** checkpoints state after every node transition (keyed by thread ID). For infrastructure-level durability, Temporal wraps the agent chain in a workflow where LLM calls and tool invocations are Activities with automatic retry. The **Event Store** records every state transition as an immutable event for replay debugging and regulatory audit. The **Telemetry** layer captures distributed traces (OpenTelemetry, W3C trace-context), per-agent cost attribution, and circuit breaker state.

The loop continues until one of: the model produces final output (no more tool calls), `max_turns` is reached, a circuit breaker opens, the token budget is exhausted, or HITL escalation is triggered. The full agent trajectory -- every thought, action, observation, state transition, and cost metric -- is recorded for observability, debugging, and compliance.

---

## 2. Core Mechanics & Algorithms

### 2.1 ReAct Loop Mechanics

ReAct (Reason + Act, Yao et al., 2022) is the canonical agent pattern and the default starting point for general-purpose agents in 2026.

```
ReAct Cycle:

  ┌──────────────────────────────────────────────────────┐
  │                   AGENT LOOP                          │
  │                                                       │
  │  ┌─────────┐    ┌─────────┐    ┌─────────────────┐  │
  │  │ THOUGHT │───▶│ ACTION  │───▶│  OBSERVATION     │  │
  │  │ (reason │    │ (select │    │  (execute tool,  │  │
  │  │  about  │    │  tool + │    │   read result)   │  │
  │  │  state) │    │  args)  │    │                  │  │
  │  └─────────┘    └─────────┘    └────────┬─────────┘  │
  │       ▲                                  │            │
  │       │                                  │            │
  │       └──────────────────────────────────┘            │
  │                                                       │
  │  Termination conditions:                              │
  │  1. Model emits final answer (no tool_use blocks)     │
  │  2. max_turns reached (hard cap, typically 5-25)      │
  │  3. Token budget exhausted                            │
  │  4. Circuit breaker opens (repeated failures)         │
  │  5. No-progress detected (same action repeated)       │
  └──────────────────────────────────────────────────────┘
```

**Action space definition**: Each tool (name, description, JSON schema) constitutes one possible action. The model selects from this space at each step. The action space is static per run unless tools are dynamically registered/deregistered. Tool descriptions drive selection accuracy -- a poorly described tool will be called in wrong contexts or not called when needed.

**Token cost**: Each iteration requires a full LLM inference pass over the accumulated conversation history. With N iterations, total tokens consumed grow as O(N^2) in the naive case (each turn re-reads all prior turns). Typical completion: 3-7 loops, 10,000-25,000 total tokens for complex tasks.

**Variants**: RP-ReAct (Molinari et al., Dec 2025) decouples strategic planning from execution -- a Reasoner-Planner decomposes goals into sub-questions while Proxy Execution Agents handle standard ReAct loops per sub-task. Focused ReAct reiterates the original question at each step and early-stops on repetitive actions, yielding up to 530% relative accuracy gains on targeted benchmarks.

### 2.2 Plan-and-Execute

Separates plan generation from step-by-step execution, optimizing for cost and multi-step reliability.

```
Plan-and-Execute Architecture:

  ┌──────────────────────────────────────────────────────────┐
  │ PLANNER (frontier model, e.g., Opus/GPT-5)               │
  │ - Receives full task description                          │
  │ - Generates ordered step list with dependencies           │
  │ - Outputs structured plan (1,000-2,000 tokens)            │
  │ - Consumes ~15% of total token budget                     │
  └──────────────────────┬───────────────────────────────────┘
                         ▼
  ┌──────────────────────────────────────────────────────────┐
  │ EXECUTOR (smaller/cheaper model, e.g., Haiku/GPT-4.1)    │
  │ Step 1: [action] ──▶ [result] ──▶ state update            │
  │ Step 2: [action] ──▶ [result] ──▶ state update            │
  │ Step N: [action] ──▶ [result] ──▶ state update            │
  │ - Consumes ~85% of total token budget at much lower rate  │
  └──────────────────────┬───────────────────────────────────┘
                         ▼
  ┌──────────────────────────────────────────────────────────┐
  │ RE-PLANNER (optional, triggered by execution deviation)   │
  │ - Evaluates: did step result match expected outcome?      │
  │ - If deviation detected: regenerate remaining plan        │
  │ - If on track: continue to next executor step             │
  └──────────────────────────────────────────────────────────┘
```

**Plan representations**: Ordered list of steps with descriptions and expected outputs (simplest), dependency DAG with explicit preconditions (for parallel execution), or hierarchical plan with sub-task decomposition (for complex workflows).

**Re-planning strategies**: (1) No replanning -- execute plan as-is, fail on deviation (cheapest, most brittle). (2) Step-level replanning -- after each step, compare actual vs. expected result, regenerate remaining steps if mismatch (balanced). (3) Continuous replanning -- re-evaluate full plan after every step (most adaptive, highest cost).

**Cost advantage**: CLEAR Framework data shows Plan-Execute costs $1.24/task vs. $5.12 for Reflexion at the same accuracy class -- 4.4x lower cost. Best for tasks with 5+ interdependent steps in stable environments.

### 2.3 Reflexion

A self-improvement loop using linguistic feedback rather than weight updates.

```
Reflexion Loop:

  ┌───────────┐     ┌─────────────┐     ┌──────────────────┐     ┌──────────────────┐
  │   ACTOR   │────▶│  EVALUATOR  │────▶│  SELF-REFLECTION │────▶│  EPISODIC MEMORY │
  │ (generate │     │ (assess     │     │  (generate       │     │  (store reflect- │
  │  output   │     │  quality,   │     │   specific       │     │   ions for next  │
  │  using    │     │  identify   │     │   feedback on    │     │   attempt)       │
  │  current  │     │  failures)  │     │   what went      │     │                  │
  │  memory)  │     │             │     │   wrong and how  │     │                  │
  │           │     │             │     │   to improve)    │     │                  │
  └───────────┘     └─────────────┘     └──────────────────┘     └────────┬─────────┘
       ▲                                                                   │
       └───────────────────────────────────────────────────────────────────┘
                              Next attempt uses stored reflections
```

**Convergence properties**: Typically improves quality 10-30% on failure-mode subsets. However, a 2025 replication study found single-agent Reflexion consistently repeats earlier misconceptions because the same model generates both output and critique, reinforcing its own blind spots (ICLR 2024, Huang et al.). Adds ~30% latency per iteration. Reflection improvements vary: +7-18% for reasoning tasks, but can decrease performance when initial accuracy is already high. Prompts soliciting mistakes induce up to 40.4% false positive correction rates.

**Mitigation**: Self-correction requires external verification (tool outputs, test results, separate critic models) to be reliable. PreFlect (prospective reflection) outperforms classic Reflexion by 10-15% with 15-20% additional token overhead. GSAR framework (2026) extends hallucination detection to multi-agent settings with typed grounding.

### 2.4 LATS (Language Agent Tree Search)

Combines reflection/evaluation with Monte-Carlo Tree Search to explore multiple reasoning paths.

```
LATS MCTS Operations:

  ┌─────────────┐
  │  SELECTION   │  UCB1 formula: UCB(s,a) = V(s,a) + c * sqrt(ln(N(s)) / N(s,a))
  │  Choose most │  where V = value estimate, N = visit count, c = exploration constant
  │  promising   │  Balances exploitation (high V) vs exploration (low visit count)
  │  node via UCB│
  └──────┬──────┘
         ▼
  ┌─────────────┐
  │  EXPANSION   │  Generate K candidate next actions from selected node
  │  Create new  │  Each becomes a child node in the search tree
  │  child nodes │
  └──────┬──────┘
         ▼
  ┌─────────────┐
  │  EVALUATION  │  LLM-as-judge scores each candidate (0-1)
  │  Score each  │  Alternatively: external verifier (test suite, type checker)
  │  candidate   │
  └──────┬──────┘
         ▼
  ┌─────────────┐
  │  SIMULATION  │  Execute most promising path forward (rollout)
  │  Rollout     │  Observe outcome: success, partial success, or failure
  │  selected    │
  │  path        │
  └──────┬──────┘
         ▼
  ┌─────────────┐
  │  BACKPROP    │  Update value estimates along the path from leaf to root
  │  Update      │  Good outcomes increase parent values; bad outcomes decrease
  │  ancestors   │
  └──────┬──────┘
         ▼
  ┌─────────────┐
  │  REFLECTION  │  Generate linguistic feedback on failed paths
  │  Learn from  │  Store in episodic memory for future selections
  │  failures    │  Prunes search space for subsequent iterations
  └─────────────┘
```

**Computational trade-offs**: Full LATS is 5-20x more expensive than baseline ReAct (each candidate path requires multiple LLM calls). Production usage is rare. Most teams use a lighter variant: generate 2-3 candidate plans, evaluate them, pick the best one without deep tree search. This achieves 60-80% of LATS quality improvement at 2-3x ReAct cost rather than 5-20x.

### 2.5 State Reducers and Concurrent Update Handling

LangGraph's StateGraph uses **state reducers** (also called annotation reducers) to handle concurrent updates when multiple nodes modify the same state field.

```
State Reducer Mechanics:

  State Definition (TypedDict with Annotated reducers):

  ┌────────────────────────────────────────────────┐
  │  class AgentState(TypedDict):                  │
  │      messages: Annotated[list, add_messages]   │  ← reducer: append, don't overwrite
  │      plan: str                                 │  ← no reducer: last-write-wins
  │      results: Annotated[list, operator.add]    │  ← reducer: concatenate lists
  │      scores: Annotated[dict, merge_dicts]      │  ← custom reducer: deep merge
  └────────────────────────────────────────────────┘

  Parallel Execution with Reducers:

  Node A writes: results = ["doc_1_summary"]  ──┐
                                                 ├──▶ Reducer: results = ["doc_1_summary", "doc_2_summary"]
  Node B writes: results = ["doc_2_summary"]  ──┘

  Without reducer:
  Node A writes: results = ["doc_1_summary"]  ──┐
                                                 ├──▶ ERROR or last-write-wins (data loss)
  Node B writes: results = ["doc_2_summary"]  ──┘
```

Reducers are critical for correctness in parallel fan-out patterns. Without them, concurrent updates to the same field cause race conditions or silent data loss. The `add_messages` built-in reducer handles the common case of appending to message history.

### 2.6 Framework Comparison (2026)

```
 ┌──────────────┬──────────────┬──────────────┬──────────────┬──────────────┬──────────────┐
 │              │  LangGraph   │ OpenAI       │ Google ADK   │ CrewAI       │ AutoGen      │
 │              │              │ Agents SDK   │              │              │              │
 ├──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
 │ Core         │ StateGraph   │ Runner loop  │ Agent-as-    │ Role-based   │ Actor model  │
 │ abstraction  │ (compiled    │ with handoff │ class with   │ crew/task    │ with typed   │
 │              │ graph)       │ delegation   │ workflow     │ composition  │ message      │
 │              │              │              │ composition  │              │ passing      │
 ├──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
 │ Graph        │ DAG + cyclic │ Linear chain │ DAG (Seq,    │ Sequential   │ DAG via      │
 │ topology     │ (key differ- │ + handoffs   │ Parallel,    │ + hierarchi- │ conversation │
 │              │ entiator)    │              │ Loop agents) │ cal          │ patterns     │
 ├──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
 │ State mgmt   │ TypedDict/   │ RunState +   │ SessionServ- │ @persist     │ Agent        │
 │              │ Pydantic,    │ to_state()/  │ ice,         │ decorator,   │ runtime      │
 │              │ reducers,    │ resume       │ task API     │ memory       │ context      │
 │              │ checkpoints  │              │              │ stores       │              │
 ├──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
 │ Persistence  │ MemorySaver, │ RunState     │ SessionServ- │ @persist     │ In-memory    │
 │              │ Sqlite/PG/   │ serializ.    │ ice (in-mem, │              │ or custom    │
 │              │ DynamoDB     │              │ Firestore)   │              │              │
 ├──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
 │ HITL         │ Native       │ to_state()/  │ Native       │ Human tool   │ Human-in-    │
 │              │ interrupt +  │ resume       │ resumable    │ proxy        │ loop agent   │
 │              │ resume       │ pattern      │ execution    │              │              │
 ├──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
 │ Model        │ Model-       │ OpenAI only  │ Gemini-opt., │ Model-       │ Model-       │
 │ support      │ agnostic     │              │ multi-model  │ agnostic     │ agnostic     │
 │              │              │              │ via LiteLLM  │              │              │
 ├──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
 │ Enterprise   │ 43% of ent.  │ Strong in    │ GCP-native,  │ Rapid proto- │ Research-    │
 │ adoption     │ agent deploy.│ OpenAI-first │ A2A protocol │ typing, role │ oriented,    │
 │              │ (2026)       │ shops        │ for interop  │ abstraction  │ v0.4 actor   │
 │              │              │              │              │              │ rewrite      │
 ├──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
 │ Durable      │ Checkpoints  │ State serial │ Session      │ @persist     │ No native    │
 │ execution    │ (app-level); │ (app-level); │ (app-level); │ (app-level); │ support;     │
 │              │ needs        │ Temporal GA  │ needs        │ needs        │ needs        │
 │              │ Temporal for │ integration  │ external     │ external     │ external     │
 │              │ infra-level  │ (Mar 2026)   │              │              │              │
 └──────────────┴──────────────┴──────────────┴──────────────┴──────────────┴──────────────┘

 Additional framework (2026):
 Microsoft Agent Framework RC -- compile-time type-safe DAGs with WorkflowBuilder.
 Cloud-native: Amazon Bedrock AgentCore, Azure AI Foundry Agents, Databricks Agent Bricks.
```

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Formulas by Architecture

```
 ┌────────────────────────────────────────────────────────────────────────────────┐
 │  COST MODEL                                                                    │
 │                                                                                │
 │  ReAct (baseline):                                                             │
 │    C_react = Sum(i=1..N) [ (S + H_i) * P_input + R_i * P_output ]             │
 │    where S = system prompt tokens (constant)                                   │
 │          H_i = accumulated history at step i (grows ~linearly)                 │
 │          R_i = response tokens at step i                                       │
 │          P_input, P_output = price per token (input/output)                    │
 │          N = number of iterations (typically 3-7)                               │
 │                                                                                │
 │    Naive growth: O(N^2) total input tokens (each step re-reads all history)    │
 │    Typical: 10,000-25,000 tokens/task, $0.06-0.09/task (simple)                │
 │                                                                                │
 │  Plan-and-Execute (moderate):                                                  │
 │    C_pe = C_plan(frontier) + Sum(i=1..K) C_step_i(cheap_model)                 │
 │    where C_plan uses frontier model for ~15% of total tokens                   │
 │          C_step uses cheaper model for ~85% of total tokens                    │
 │    Typical: $1.24/task (CLEAR Framework), 4.4x cheaper than Reflexion          │
 │                                                                                │
 │  Reflexion (higher):                                                           │
 │    C_refl = Sum(j=1..M) [ C_react_j + C_eval_j + C_reflect_j ]                │
 │    where M = number of reflection iterations (typically 2-3)                   │
 │    Typical: $5.12/task (CLEAR Framework), +30% latency per iteration           │
 │                                                                                │
 │  LATS (5-20x baseline):                                                        │
 │    C_lats = Sum(nodes in tree) [ C_expand + C_evaluate + C_simulate ]          │
 │    Each candidate path = multiple LLM calls                                    │
 │    Production variant (2-3 candidates): 2-3x ReAct cost                        │
 │                                                                                │
 │  Enterprise multiplier:                                                        │
 │    Agentic workflows consume 5-30x more tokens than standard chat              │
 │    Multi-agent systems ~15x single chat interaction                             │
 │    Enterprise AI inference = 85% of total AI budgets                            │
 │    Token usage explains 80% of performance variance (Anthropic, BrowseComp)    │
 └────────────────────────────────────────────────────────────────────────────────┘
```

**Cost optimization levers** (quantified):

1. **Plan caching**: 50.31% cost reduction, 96.61% performance retention, 27.28% latency reduction (NeurIPS 2025).
2. **Model routing**: Cheap model for easy 70% of queries, frontier for hard 30%. 40-70% cost reduction with no measurable quality loss.
3. **Prompt caching**: 50-90% reduction in prompt token costs. When prompt tokens are 90% of total, per-task cost drops 40-80%. All major providers support natively in 2026.
4. **Hybrid model pairing**: DeepSeek R1 (reasoning/planning) + Claude Sonnet (code editing) hit SOTA on Aider polyglot benchmark at 14x less cost than OpenAI o1 alone (Gauthier, Jan 2025).

### 3.2 Benchmark Results (2026)

```
 ┌────────────────────────────────────────────────────────────────────────────────┐
 │  BENCHMARK SCORECARD                                                           │
 │                                                                                │
 │  SWE-bench Verified (code repair):                                             │
 │    Claude Opus 4.7 .............. 87.6%  (SOTA)                                │
 │    GPT-5.3 Codex ............... 85.0%                                          │
 │    Claude Opus 4.5 ............. 80.9%                                          │
 │    Baseline (Claude 2, 2023) ...  1.96%                                         │
 │    Signal: top-3 gap compressed to <5pp -- saturation approaching              │
 │                                                                                │
 │  WebArena (web agent tasks):                                                   │
 │    Claude Mythos Preview ....... 68.7%  (SOTA)                                 │
 │    GPT-5.4 Pro ................. 65.8%                                          │
 │    Claude Opus 4.6 ............. 64.5%                                          │
 │    Human baseline .............. ~78%                                           │
 │    Original GPT-4 agent ........ 14.41%                                         │
 │    Signal: hybrid (computer-use + API) outperforms pure-pixel agents           │
 │                                                                                │
 │  GAIA (general AI assistant):                                                  │
 │    Claude Sonnet 4.5 ........... 74.6% (Princeton HAL)                         │
 │    Agentic-search specialist ... 92.36% (domain-optimized)                     │
 │    Signal: Anthropic sweeps top 6 HAL spots; GAIA2 succeeds original           │
 │                                                                                │
 │  TAU-bench (tool-augmented, reliability):                                      │
 │    Claude 3.5 Sonnet (frozen) .. 69.2% retail / 46.0% airline                  │
 │    pass^k reliability decay: pass^1 "good" can drop below 25% at pass^8       │
 │    ICML 2026: capability gains yield only small reliability improvements       │
 │    Successors: tau2-bench (dual-control), tau3-bench (audited tasks)           │
 │                                                                                │
 │  CAVEATS:                                                                      │
 │  (1) 0 of 15 major benchmarks integrate cost-efficiency into scoring           │
 │  (2) Scaffold dependency: same model posts different scores under              │
 │      different harnesses (agent framework matters as much as model)             │
 │  (3) UC Berkeley RDI (April 2026): automated scanning agent broke all          │
 │      8 major benchmarks by reward hacking -- near-perfect scores               │
 │      without solving a single task                                              │
 └────────────────────────────────────────────────────────────────────────────────┘
```

### 3.3 Latency Profiles by Architecture

```
 ┌───────────────────┬──────────────────┬──────────────────┬──────────────────────┐
 │ Pattern           │ First-token      │ Task completion  │ Key characteristic   │
 │                   │ latency          │ (wall-clock)     │                      │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ ReAct             │ ~250ms (single   │ Sequential,      │ Lowest per-step      │
 │                   │ LLM call)        │ accumulates      │ latency; highest     │
 │                   │                  │ over many steps   │ total for long tasks │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ Plan-and-Execute  │ Higher (plan     │ Fewer round-     │ Front-loaded latency │
 │                   │ generation       │ trips total;     │ but fewer total LLM  │
 │                   │ first)           │ executor fast    │ calls                │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ Reflexion         │ Same as base     │ +30% per         │ Quality/latency      │
 │                   │ pattern          │ reflection       │ trade-off; 2-3       │
 │                   │                  │ iteration        │ reflection rounds    │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ LATS              │ Same as base     │ 5-20x baseline   │ Highest latency;     │
 │                   │                  │ (full); 2-3x     │ highest quality      │
 │                   │                  │ (lite variant)   │ for large search     │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ DAG/Graph         │ Depends on       │ Lowest wall-     │ ~75% time reduction  │
 │ (parallel)        │ critical path    │ clock for        │ vs sequential on     │
 │                   │                  │ parallelizable   │ parallelizable work  │
 │                   │                  │ workloads        │                      │
 └───────────────────┴──────────────────┴──────────────────┴──────────────────────┘
```

### 3.4 Latency SLA Targets (p50 / p95 / p99)

Assumptions: Claude Sonnet 4.6 or GPT-4o class model, 1-2K input tokens per step, prompt caching enabled, warm sandbox pool.

```
 ┌───────────────────┬────────┬─────────┬─────────┬─────────────────────────────────┐
 │ Pattern           │ p50    │ p95     │ p99     │ Mitigation for tail latency     │
 ├───────────────────┼────────┼─────────┼─────────┼─────────────────────────────────┤
 │ ReAct (per step)  │ 300ms  │ 1.2s    │ 3.5s    │ Prompt caching (60-80% TTFT     │
 │                   │        │         │         │ reduction); model fallback to   │
 │                   │        │         │         │ faster tier on timeout           │
 ├───────────────────┼────────┼─────────┼─────────┼─────────────────────────────────┤
 │ ReAct (end-to-end │ 2.5s   │ 8s      │ 25s     │ No-progress guard (cap wasted   │
 │ 3-5 step task)    │        │         │         │ iterations); iteration limit    │
 ├───────────────────┼────────┼─────────┼─────────┼─────────────────────────────────┤
 │ Plan-and-Execute  │ 1.5s   │ 5s      │ 12s     │ Planning call cached for repeat │
 │ (3-step task)     │        │         │         │ patterns; executor uses smaller │
 │                   │        │         │         │ model (Haiku-class)             │
 ├───────────────────┼────────┼─────────┼─────────┼─────────────────────────────────┤
 │ Reflexion         │ 3.5s   │ 12s     │ 35s     │ Cap reflection rounds to 2-3;  │
 │ (1 retry cycle)   │        │         │         │ skip reflection on pass@1       │
 ├───────────────────┼────────┼─────────┼─────────┼─────────────────────────────────┤
 │ LATS              │ 15s    │ 60s     │ 180s    │ Use lite variant (2-3x, not     │
 │ (full search)     │        │         │         │ 20x); beam width cap; early     │
 │                   │        │         │         │ termination on high-confidence  │
 ├───────────────────┼────────┼─────────┼─────────┼─────────────────────────────────┤
 │ DAG (parallel,    │ 800ms  │ 2.5s    │ 6s      │ Timeout per branch with partial │
 │ 3 branches)       │        │         │         │ result aggregation; warm worker │
 │                   │        │         │         │ pool for branch execution       │
 └───────────────────┴────────┴─────────┴─────────┴─────────────────────────────────┘
```

### 3.5 Throughput Capacity Planning & Back-Pressure

**Capacity model**: Agent throughput is bounded by three resources: LLM API rate limits (TPM/RPM), tool execution concurrency, and state store write throughput.

```
 max_concurrent_agents = min(
     llm_rpm / avg_llm_calls_per_agent_step,
     tool_pool_size,
     state_store_write_iops / checkpoints_per_step
 )
```

**Back-pressure mechanisms**:
- **Queue-based admission**: Agents pull work from queues (SQS, RabbitMQ). Queue depth signals overload — stop accepting new tasks when depth exceeds 2x drain rate.
- **Per-tenant rate limiting**: Cap concurrent agents per tenant (e.g., 10 concurrent for standard, 50 for enterprise). Prevents noisy-neighbor saturation.
- **Token budget enforcement**: Pre-execution budget check — reject tasks that would exceed remaining tenant budget. Enforce in the request path, not in billing.
- **Circuit breaker on LLM provider**: When provider returns 429s, open circuit → queue all agent steps → probe with single request → resume on success.
- **Horizontal scaling signal**: Auto-scale agent workers on queue depth (HPA with custom metric). Target: queue wait time < 5s at p95.

**Benchmark reference**: A well-tuned LangGraph deployment on 8-node Kubernetes cluster handles ~500 concurrent agent sessions with PostgresSaver checkpointing (< 15ms write latency at < 10KB state per checkpoint). Beyond 500, checkpoint write contention becomes the bottleneck — shard by tenant or switch to Temporal for infrastructure-grade durability.

### 3.6 Iteration Caps and Cost-Quality Trade-offs

Without `max_turns`, misbehaving agents loop indefinitely. Recommended caps:

- **OpenAI Agents SDK**: 5-10 for most use cases (OpenAI recommendation).
- **LangGraph**: `recursion_limit` default is 25 (hard ceiling).
- **Production rule**: Per-task AND per-tenant token budgets that halt execution, not just warn.
- **Cost compounding**: Multi-turn agent cost compounds 3-5x faster than naive "turns x avg_cost" models predict (Cloudzy, 2026).
- **Cost monitoring signal**: Track cost per *successful outcome*, not total spend. Total spend rising with volume is fine; cost per outcome rising is a regression.

### 3.7 Non-Functional Requirements

```
 ┌────────────────┬─────────────────────────────────────────────────────────────┐
 │ NFR            │ Agent-specific considerations                               │
 ├────────────────┼─────────────────────────────────────────────────────────────┤
 │ Availability   │ Agent loops are long-running (minutes to hours). Standard   │
 │                │ HTTP request availability (99.9%) is insufficient. Require  │
 │                │ durable execution (Temporal/equivalent) for any agent       │
 │                │ touching external systems. Checkpoint-based resume after    │
 │                │ infrastructure failure.                                     │
 ├────────────────┼─────────────────────────────────────────────────────────────┤
 │ RPO            │ State loss = lost work. Sync checkpointing (LangGraph)     │
 │                │ gives RPO = 0 at node boundaries (state saved before next  │
 │                │ step begins). Without sync checkpointing, RPO = last       │
 │                │ checkpoint interval.                                        │
 ├────────────────┼─────────────────────────────────────────────────────────────┤
 │ RTO            │ Checkpoint-based resume: RTO = crash detection + state     │
 │                │ reload + context reconstruction. LangGraph alone has no     │
 │                │ crash detection (no supervisor/watchdog/heartbeat).         │
 │                │ Temporal provides infrastructure-level RTO via automatic   │
 │                │ failure detection and task rescheduling.                    │
 ├────────────────┼─────────────────────────────────────────────────────────────┤
 │ Compliance     │ EU AI Act Art.12: automatic event logging, 6-24 month      │
 │                │ retention, tamper-evident, regulator-exportable. Full       │
 │                │ high-risk mandates enforceable Aug 2, 2026 (possible       │
 │                │ extension to Dec 2027). Penalties: up to 35M EUR or 7%     │
 │                │ worldwide annual turnover.                                  │
 ├────────────────┼─────────────────────────────────────────────────────────────┤
 │ Scalability    │ Only 1.6% of Claude Code codebase is AI decision logic;    │
 │                │ 98.4% is operational infrastructure. Agents consume 5-30x  │
 │                │ tokens of standard chat. Infrastructure cost dominates     │
 │                │ model cost at scale.                                        │
 └────────────────┴─────────────────────────────────────────────────────────────┘
```

---

## 4. Distributed Resilience & Security

### 4.1 Durable Execution

**The operational wall**: Agent frameworks solved the planning loop by 2025. The remaining challenge is operational: agent dies mid-run, approval lands a day late, upstream API rate-limits, partial side-effects leave audit logs inconsistent. Durable execution puts a primitive under all these failures.

```
Durable Execution Stack:

 ┌─────────────────────────────────────────────────────────────────────────┐
 │  TEMPORAL WORKFLOW (deterministic orchestration blueprint)               │
 │                                                                         │
 │  @workflow.defn                                                         │
 │  class AgentWorkflow:                                                   │
 │      @workflow.run                                                      │
 │      async def run(self, task):                                         │
 │          plan = await workflow.execute_activity(                         │
 │              generate_plan, task, ...)          ← Activity (LLM call)   │
 │          for step in plan.steps:                                        │
 │              result = await workflow.execute_activity(                   │
 │                  execute_step, step, ...)       ← Activity (tool call)  │
 │              if result.needs_approval:                                   │
 │                  await workflow.wait_condition(  ← HITL: can wait days  │
 │                      lambda: self.approved)                              │
 │                                                                         │
 │  Key capabilities:                                                      │
 │  - Automatic retry on activity failure (configurable backoff)           │
 │  - State held over long periods (days/years) without state machines     │
 │  - Self-healing: automatic retries for probabilistic LLM outputs       │
 │  - Deterministic replay for debugging (time-travel)                     │
 └─────────────────────────────────────────────────────────────────────────┘

 Real-world adoption:
 - OpenAI uses Temporal for Codex in production (millions of requests)
 - Official OpenAI Agents SDK integration reached GA on March 23, 2026
 - Temporal Serverless Workers + Google ADK integration (Replay 2026)
```

**Framework persistence comparison**:

```
 ┌───────────────┬──────────────────┬────────────────────────────────────────────┐
 │ Framework     │ Mechanism        │ Durability level                            │
 ├───────────────┼──────────────────┼────────────────────────────────────────────┤
 │ LangGraph     │ Checkpointers    │ App-level: MemorySaver (dev), Sqlite       │
 │               │                  │ (single-node), PostgresSaver (multi-node), │
 │               │                  │ DynamoDB+S3 (AWS). State after every node.  │
 │               │                  │ No crash detection -- external needed.      │
 ├───────────────┼──────────────────┼────────────────────────────────────────────┤
 │ OpenAI SDK    │ RunState serial. │ App-level: to_state()/resume. Temporal     │
 │               │                  │ integration GA for infra-level durability.  │
 ├───────────────┼──────────────────┼────────────────────────────────────────────┤
 │ Google ADK    │ SessionService   │ App-level: in-memory, Firestore, custom.   │
 │               │                  │ Task API for agent-to-agent delegation.    │
 ├───────────────┼──────────────────┼────────────────────────────────────────────┤
 │ CrewAI        │ @persist         │ App-level: decorator-based persistence.    │
 ├───────────────┼──────────────────┼────────────────────────────────────────────┤
 │ Temporal      │ Workflow+        │ Infra-level: automatic failure detection,  │
 │               │ Activities       │ task rescheduling, cross-node durability,  │
 │               │                  │ idempotency keys, saga/compensation.       │
 └───────────────┴──────────────────┴────────────────────────────────────────────┘
```

**The checkpointing gap**: Checkpointing alone is not full durable execution. LangGraph saves state but provides no automatic failure detection -- no supervisor, no watchdog, no heartbeat. If the process crashes, the workflow is dead until something external notices. LangGraph protects against *application-level* failures (bad reasoning, incorrect branches, HITL pauses). Temporal protects against *infrastructure-level* failures (container crashes, network partitions, host preemptions). Production deployments often need both layers.

**Sharp edges on resume**: On resume, code before an interrupt may re-execute. Nondeterministic operations and side effects need idempotency. The node boundary must be engineered as a replay boundary. LangChain's 2026 State of Agent Engineering report: 60% of production incidents trace to state management.

### 4.2 Failure Taxonomy

```
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │  AGENT FAILURE TAXONOMY                                                     │
 │                                                                             │
 │  Industry failure rate in live environments: 70-95%                         │
 │  88% of failures trace to infrastructure gaps, not model quality (Arize)    │
 │  Average enterprise abandoned 2.3 AI initiatives in 2025 ($16.5M avg loss) │
 │                                                                             │
 │  ┌─────────────────────────────────────────────────────────────────────┐   │
 │  │  TRANSIENT FAILURES (retryable)                                     │   │
 │  │                                                                     │   │
 │  │  - LLM API rate limits / timeouts                                   │   │
 │  │  - Tool endpoint temporary unavailability                           │   │
 │  │  - Network partitions (recoverable via checkpoint resume)           │   │
 │  │  - Container preemption (Temporal auto-reschedules)                 │   │
 │  └─────────────────────────────────────────────────────────────────────┘   │
 │                                                                             │
 │  ┌─────────────────────────────────────────────────────────────────────┐   │
 │  │  PERMANENT FAILURES (require intervention)                          │   │
 │  │                                                                     │   │
 │  │  Infinite loops (31.6% -- context blindness):                       │   │
 │  │    LLMs lack internal "stop" signal on repetitive errors. Retry     │   │
 │  │    loop consumes context window, pushing original goal out of scope.│   │
 │  │    68 confirmed infinite loop incidents across 47 projects.         │   │
 │  │    Mitigation: hard iteration cap + hash(tool+args) repeat detect. │   │
 │  │                                                                     │   │
 │  │  Planning failures (30.3% -- rogue actions):                        │   │
 │  │    Wrong decomposition, goal drift, hallucinated sub-tasks.         │   │
 │  │    In multi-agent systems, one agent's hallucinated output becomes  │   │
 │  │    another agent's authoritative input (cascading errors).          │   │
 │  │    Mitigation: external verification, HITL for irreversible acts.  │   │
 │  │                                                                     │   │
 │  │  Context window exhaustion (24.9% -- silent degradation):           │   │
 │  │    Agent performs perfectly for first 5 steps, then degrades --     │   │
 │  │    repeating work, forgetting constraints, contradicting itself.    │   │
 │  │    Even 200K+ token windows suffer recall degradation as context   │   │
 │  │    fills. Mitigation: summarization at intervals, subagent model.  │   │
 │  │                                                                     │   │
 │  │  State corruption (8.1% -- memory corruption):                      │   │
 │  │    Race conditions in parallel execution (scale as N(N-1)/2).      │   │
 │  │    Aggregation hallucination: LLM synthesizes false consensus from │   │
 │  │    parallel results. Mitigation: state reducers, typed merging.    │   │
 │  │                                                                     │   │
 │  │  Hallucinated task completion (5.1% -- runaway execution):          │   │
 │  │    Agent reports success without completing work. High internal     │   │
 │  │    self-consistency defeats consistency-based detection (EMNLP '25).│   │
 │  │    Mitigation: external verification (tests, checksums, separate   │   │
 │  │    critic model). PreFlect outperforms classic Reflexion by 10-15%.│   │
 │  └─────────────────────────────────────────────────────────────────────┘   │
 └─────────────────────────────────────────────────────────────────────────────┘
```

### 4.3 Circuit Breaker for Agent Loops

```
Circuit Breaker State Machine:

  ┌──────────┐     failure_count >= threshold     ┌──────────┐
  │  CLOSED  │ ──────────────────────────────────▶ │   OPEN   │
  │ (normal  │                                     │ (reject  │
  │  exec.)  │                                     │  all     │
  │          │ ◀──── success in half-open ──────── │  calls,  │
  └──────────┘                                     │  return  │
       ▲                                           │  error)  │
       │                                           └────┬─────┘
       │                                                │
       │              timeout expires                   │
       │                                                ▼
       │                                          ┌───────────┐
       │                                          │ HALF-OPEN │
       └────────── success ◀───────────────────── │ (allow 1  │
                                                   │  trial    │
                   failure ──────────────────────▶ │  call)    │
                   (back to OPEN)                  └───────────┘

  Agent-specific adaptations:
  - Track per-tool AND per-agent-loop failure rates separately
  - "Failure" includes: tool error, timeout, no-progress (same action repeated),
    token budget exceeded, confidence below HITL threshold
  - On OPEN: return explicit error message to model (not silent drop)
    so the model can reason about the failure and try alternatives
  - Half-open trial: allow one iteration with reduced token budget
  - Metrics: failure_count, consecutive_failures, time_in_state
```

### 4.4 Enterprise Security

**Prompt injection in agentic contexts**: Prompt injection remains #1 on OWASP LLM Top 10 in 2026 -- an unsolved structural problem. LLMs treat system prompt, user request, and retrieved text as a single token stream with no reliable command/data boundary. OWASP maps prompt injection to 6 of 10 categories in its Top 10 for Agentic Applications.

Key statistics:
- Documented injection attempts against enterprise AI: ~340% YoY increase (late 2025).
- Indirect attacks (instructions hidden in email/document/web page): >55% of incidents.
- Current detection tools catch only 23% of sophisticated injection attempts.
- Average AI agent-related breach cost: ~$4.7M.

**Multi-hop attacks**: In multi-agent systems, injection in one data source propagates through agent chains. CVE-2026-22708 (Cursor): attacker poisons agent execution environment so allowlisted commands deliver arbitrary payloads -- the allowlist made the attack *easier* by auto-approving the needed commands.

**Supply chain attacks**: LiteLLM backdoor on PyPI (March 2026, ~47,000 downloads in 3 hours). First malicious MCP server in the wild: postmark-mcp shipped 15 clean versions before adding exfiltration code.

**Agent permission boundaries (OWASP Excessive Agency LLM06)**: Because no fully reliable injection defense exists, assume injection succeeds. The durable mitigation is ensuring a compromised agent cannot perform high-impact actions. Zero Trust for agents: all actions explicitly allowed rather than implicitly permitted.

**Execution sandboxing**:

```
 ┌───────────────┬─────────────────────────────────────────────────────────────┐
 │ Technology    │ Use case                                                     │
 ├───────────────┼─────────────────────────────────────────────────────────────┤
 │ Firecracker   │ Strongest isolation, regulated data, microVM per execution  │
 │ microVMs      │                                                             │
 ├───────────────┼─────────────────────────────────────────────────────────────┤
 │ gVisor        │ Syscall-level interception, compute-heavy multi-tenant      │
 ├───────────────┼─────────────────────────────────────────────────────────────┤
 │ V8 Isolates   │ JS-only workloads, latency-critical (<1ms startup)          │
 ├───────────────┼─────────────────────────────────────────────────────────────┤
 │ WebAssembly   │ Emerging: polyglot + fine-grained capability control        │
 ├───────────────┼─────────────────────────────────────────────────────────────┤
 │ Standard      │ NOT acceptable isolation boundary for agentic workloads     │
 │ containers    │                                                             │
 └───────────────┴─────────────────────────────────────────────────────────────┘
```

**Microsoft Agent Governance Toolkit (April 2026)**: Four execution rings (Ring 0 supervisor through Ring 3 untrusted sandbox), each with resource limits plus instant kill-switch. Maps controls to every OWASP agentic risk.

**HITL escalation policies**: Irreversible actions (delete, send, payment, permission change) require human approval. Confidence-based routing: agent confidence below threshold triggers escalation. Budget-based: cumulative cost above per-task threshold triggers review. All escalation decisions logged with reason codes for audit.

**Audit trails for agent decision chains**: EU AI Act Article 12 requires high-risk AI systems to enable automatic recording of events over the system lifetime. Requirements: structured complete records (timestamp, agent identity, action type, input, output, context), tamper-evident (cryptographic measures), retained 6-24 months, exportable for regulator review. Each agent in a multi-agent pipeline needs its own identity, scope constraints, and audit trail segment. Non-human identities already outnumber human identities in most enterprises.

**Industry readiness gap**: 85% of enterprise customers experimenting with agents, only 5% in production (Cisco, RSA Conference 2026). 61% of organizations have fragmented logs; 33% lack evidence-quality audit trails (Gravitee, 2026).

**Defensive architecture consensus (2026)**: Containment, not cure. Six control layers: identity, least-privilege access, runtime enforcement, behavioral monitoring, audit logging, supply chain security. Defense-in-depth pairs model-level resistance with architectural controls, assuming any single layer can fail.

---

## 5. Production Enterprise Code

### 5.1 Configurable Agent Loop with ReAct Pattern

```python
"""
Production agent loop with ReAct pattern, circuit breaker, HITL escalation,
structured logging, checkpointing, and fallback model chain.

Requirements:
    pip install anthropic openai pydantic structlog
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import structlog
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Structured Logging with Correlation IDs
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(0),
)

logger = structlog.get_logger()


def bind_correlation_id(run_id: str) -> None:
    """Bind a correlation ID to all log entries in this context."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        correlation_id=run_id,
        trace_id=str(uuid.uuid4()),
    )


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Circuit breaker for agent loop failure detection.

    Tracks consecutive failures. Opens when threshold is reached,
    rejects all calls while open, allows one trial after cooldown.
    """

    failure_threshold: int = 3
    cooldown_seconds: float = 30.0
    state: BreakerState = BreakerState.CLOSED
    consecutive_failures: int = 0
    last_failure_time: float = 0.0

    def record_success(self) -> None:
        self.consecutive_failures = 0
        if self.state == BreakerState.HALF_OPEN:
            self.state = BreakerState.CLOSED
            logger.info("circuit_breaker_closed", reason="half_open_success")

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.last_failure_time = time.monotonic()
        if self.consecutive_failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            logger.warning(
                "circuit_breaker_opened",
                consecutive_failures=self.consecutive_failures,
            )

    def allow_request(self) -> bool:
        if self.state == BreakerState.CLOSED:
            return True
        if self.state == BreakerState.OPEN:
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed >= self.cooldown_seconds:
                self.state = BreakerState.HALF_OPEN
                logger.info("circuit_breaker_half_open", elapsed_s=round(elapsed, 1))
                return True
            return False
        # HALF_OPEN: allow exactly one trial
        return True


# ---------------------------------------------------------------------------
# No-Progress Detection
# ---------------------------------------------------------------------------

@dataclass
class ProgressDetector:
    """Detects agent loops making no forward progress.

    Hashes recent (tool_name, arguments) pairs and flags repeated actions.
    """

    window_size: int = 5
    recent_hashes: list[str] = field(default_factory=list)

    def check(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Return True if this action is a repeat within the recent window."""
        payload = json.dumps({"tool": tool_name, "args": arguments}, sort_keys=True)
        action_hash = hashlib.sha256(payload.encode()).hexdigest()[:16]
        is_repeat = action_hash in self.recent_hashes
        self.recent_hashes.append(action_hash)
        if len(self.recent_hashes) > self.window_size:
            self.recent_hashes.pop(0)
        return is_repeat


# ---------------------------------------------------------------------------
# Checkpoint Store (file-based for portability; swap for PostgresSaver/etc.)
# ---------------------------------------------------------------------------

class CheckpointStore:
    """Persists agent state to disk after each iteration for crash recovery.

    In production, replace with PostgresSaver, DynamoDB+S3, or equivalent.
    """

    def __init__(self, directory: str = "/tmp/agent_checkpoints") -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, run_id: str, iteration: int, state: dict[str, Any]) -> None:
        path = self.directory / f"{run_id}_iter{iteration}.json"
        path.write_text(json.dumps(state, default=str, indent=2))
        logger.debug("checkpoint_saved", run_id=run_id, iteration=iteration)

    def load_latest(self, run_id: str) -> tuple[int, dict[str, Any]] | None:
        pattern = f"{run_id}_iter*.json"
        files = sorted(self.directory.glob(pattern))
        if not files:
            return None
        latest = files[-1]
        iteration = int(latest.stem.split("_iter")[1])
        state = json.loads(latest.read_text())
        logger.info("checkpoint_restored", run_id=run_id, iteration=iteration)
        return iteration, state


# ---------------------------------------------------------------------------
# HITL Escalation
# ---------------------------------------------------------------------------

class EscalationReason(str, Enum):
    LOW_CONFIDENCE = "low_confidence"
    IRREVERSIBLE_ACTION = "irreversible_action"
    BUDGET_EXCEEDED = "budget_exceeded"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"


@dataclass
class EscalationPolicy:
    """Determines when agent execution should escalate to a human."""

    confidence_threshold: float = 0.7
    irreversible_actions: set[str] = field(
        default_factory=lambda: {"delete_record", "send_email", "execute_payment",
                                 "modify_permissions"}
    )
    max_cost_per_task_usd: float = 5.0

    def should_escalate(
        self,
        tool_name: str | None = None,
        confidence: float | None = None,
        cumulative_cost_usd: float = 0.0,
    ) -> EscalationReason | None:
        if confidence is not None and confidence < self.confidence_threshold:
            return EscalationReason.LOW_CONFIDENCE
        if tool_name and tool_name in self.irreversible_actions:
            return EscalationReason.IRREVERSIBLE_ACTION
        if cumulative_cost_usd > self.max_cost_per_task_usd:
            return EscalationReason.BUDGET_EXCEEDED
        return None


async def request_human_approval(
    run_id: str, reason: EscalationReason, context: dict[str, Any]
) -> bool:
    """Request human review. Replace with webhook/queue in production."""
    logger.warning(
        "hitl_escalation_requested",
        run_id=run_id,
        reason=reason.value,
        context_summary=str(context)[:500],
    )
    # In production: push to approval queue, await webhook callback.
    # Here we auto-approve for demonstration. Replace this with actual HITL.
    return True


# ---------------------------------------------------------------------------
# Fallback Model Chain
# ---------------------------------------------------------------------------

class ModelConfig(BaseModel):
    provider: str       # "anthropic" or "openai"
    model_id: str
    max_tokens: int = 4096
    temperature: float = 0.0


DEFAULT_MODEL_CHAIN: list[ModelConfig] = [
    ModelConfig(provider="anthropic", model_id="claude-sonnet-4-5-20241022"),
    ModelConfig(provider="openai", model_id="gpt-4.1-2025-04-14"),
    ModelConfig(provider="anthropic", model_id="claude-haiku-4-5-20241022"),
]


async def call_model_with_fallback(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    model_chain: list[ModelConfig],
) -> dict[str, Any]:
    """Try each model in the chain until one succeeds.

    Returns the parsed response dict with 'content' and optionally 'tool_calls'.
    Raises RuntimeError if all models in the chain fail.
    """
    last_error: Exception | None = None

    for model_cfg in model_chain:
        try:
            if model_cfg.provider == "anthropic":
                return await _call_anthropic(messages, tools, model_cfg)
            elif model_cfg.provider == "openai":
                return await _call_openai(messages, tools, model_cfg)
            else:
                raise ValueError(f"Unknown provider: {model_cfg.provider}")
        except Exception as exc:
            last_error = exc
            logger.warning(
                "model_fallback",
                failed_model=model_cfg.model_id,
                error=str(exc),
            )
            continue

    raise RuntimeError(
        f"All models in fallback chain failed. Last error: {last_error}"
    )


async def _call_anthropic(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    config: ModelConfig,
) -> dict[str, Any]:
    """Call Anthropic Messages API. Returns normalized response."""
    import anthropic

    client = anthropic.AsyncAnthropic()

    # Convert tools to Anthropic format
    anthropic_tools = [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["parameters"],
        }
        for t in tools
    ]

    response = await client.messages.create(
        model=config.model_id,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        messages=messages,
        tools=anthropic_tools if anthropic_tools else anthropic.NOT_GIVEN,
    )

    # Normalize to common format
    result: dict[str, Any] = {"content": None, "tool_calls": [], "raw": response}
    for block in response.content:
        if block.type == "text":
            result["content"] = block.text
        elif block.type == "tool_use":
            result["tool_calls"].append({
                "id": block.id,
                "name": block.name,
                "arguments": block.input,
            })
    return result


async def _call_openai(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    config: ModelConfig,
) -> dict[str, Any]:
    """Call OpenAI Chat Completions API. Returns normalized response."""
    import openai

    client = openai.AsyncOpenAI()

    # Convert messages from Anthropic to OpenAI format if needed
    oai_messages = []
    for msg in messages:
        if msg["role"] == "user" and isinstance(msg.get("content"), list):
            # Flatten content blocks for OpenAI
            text_parts = [
                b["text"] for b in msg["content"]
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            oai_messages.append({"role": "user", "content": " ".join(text_parts)})
        else:
            oai_messages.append(msg)

    oai_tools = [
        {"type": "function", "function": t}
        for t in tools
    ] if tools else None

    response = await client.chat.completions.create(
        model=config.model_id,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        messages=oai_messages,
        tools=oai_tools,
    )

    choice = response.choices[0]
    result: dict[str, Any] = {
        "content": choice.message.content,
        "tool_calls": [],
        "raw": response,
    }
    if choice.message.tool_calls:
        for tc in choice.message.tool_calls:
            result["tool_calls"].append({
                "id": tc.id,
                "name": tc.function.name,
                "arguments": json.loads(tc.function.arguments),
            })
    return result


# ---------------------------------------------------------------------------
# Tool Registry and Execution
# ---------------------------------------------------------------------------

ToolFunction = Callable[..., Any]

_TOOL_REGISTRY: dict[str, tuple[dict[str, Any], ToolFunction]] = {}


def register_tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
    func: ToolFunction,
) -> None:
    """Register a tool with its schema and implementation."""
    schema = {
        "name": name,
        "description": description,
        "parameters": parameters,
    }
    _TOOL_REGISTRY[name] = (schema, func)


async def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Execute a registered tool by name. Returns string result."""
    if name not in _TOOL_REGISTRY:
        return f"Error: Unknown tool '{name}'"
    _, func = _TOOL_REGISTRY[name]
    try:
        if asyncio.iscoroutinefunction(func):
            result = await func(**arguments)
        else:
            result = func(**arguments)
        return json.dumps(result, default=str) if not isinstance(result, str) else result
    except Exception as exc:
        return f"Error executing {name}: {exc}"


# ---------------------------------------------------------------------------
# Agent Loop
# ---------------------------------------------------------------------------

@dataclass
class AgentRunResult:
    run_id: str
    final_answer: str | None
    iterations: int
    total_tokens_estimate: int
    cost_estimate_usd: float
    termination_reason: str
    trajectory: list[dict[str, Any]]


async def run_agent(
    task: str,
    run_id: str | None = None,
    max_iterations: int = 15,
    model_chain: list[ModelConfig] | None = None,
    escalation_policy: EscalationPolicy | None = None,
    checkpoint_store: CheckpointStore | None = None,
    resume: bool = False,
) -> AgentRunResult:
    """Execute a ReAct agent loop with production safeguards.

    Args:
        task: The user's task description.
        run_id: Correlation ID for tracing. Auto-generated if not provided.
        max_iterations: Hard iteration cap (circuit breaker independent).
        model_chain: Ordered list of models to try (fallback chain).
        escalation_policy: HITL escalation configuration.
        checkpoint_store: Persistence backend for crash recovery.
        resume: If True, attempt to restore from last checkpoint.

    Returns:
        AgentRunResult with final answer, trajectory, and cost metrics.
    """
    run_id = run_id or str(uuid.uuid4())
    bind_correlation_id(run_id)

    model_chain = model_chain or DEFAULT_MODEL_CHAIN
    escalation_policy = escalation_policy or EscalationPolicy()
    checkpoint_store = checkpoint_store or CheckpointStore()

    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=30.0)
    progress = ProgressDetector(window_size=5)
    trajectory: list[dict[str, Any]] = []

    # Gather tool schemas for injection into LLM context
    tool_schemas = [schema for schema, _ in _TOOL_REGISTRY.values()]

    # Build initial message history
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": task,
        }
    ]

    start_iteration = 0
    total_tokens = 0

    # Crash recovery: restore from checkpoint if requested
    if resume:
        restored = checkpoint_store.load_latest(run_id)
        if restored:
            start_iteration, state = restored
            messages = state["messages"]
            trajectory = state["trajectory"]
            total_tokens = state["total_tokens"]
            logger.info(
                "agent_resumed",
                run_id=run_id,
                from_iteration=start_iteration,
            )

    logger.info(
        "agent_started",
        run_id=run_id,
        task_preview=task[:200],
        max_iterations=max_iterations,
        model_chain=[m.model_id for m in model_chain],
    )

    termination_reason = "max_iterations_reached"
    final_answer: str | None = None

    for iteration in range(start_iteration, max_iterations):
        iter_start = time.monotonic()

        # --- Circuit breaker check ---
        if not breaker.allow_request():
            escalation = escalation_policy.should_escalate(
                confidence=0.0,
            )
            if escalation:
                approved = await request_human_approval(
                    run_id, EscalationReason.CIRCUIT_BREAKER_OPEN,
                    {"iteration": iteration, "breaker_state": breaker.state.value},
                )
                if not approved:
                    termination_reason = "circuit_breaker_escalation_rejected"
                    break
                # Human approved: reset breaker for one more attempt
                breaker.state = BreakerState.HALF_OPEN
            else:
                termination_reason = "circuit_breaker_open"
                break

        # --- Call model with fallback chain ---
        try:
            response = await call_model_with_fallback(
                messages, tool_schemas, model_chain,
            )
        except RuntimeError as exc:
            logger.error("all_models_failed", error=str(exc))
            breaker.record_failure()
            termination_reason = "all_models_failed"
            break

        # --- Estimate tokens (approximate; use actual counts in production) ---
        iter_tokens = sum(len(json.dumps(m)) // 4 for m in messages) + 500
        total_tokens += iter_tokens

        # --- Check for final answer (no tool calls) ---
        if not response["tool_calls"]:
            final_answer = response.get("content", "")
            termination_reason = "final_answer"
            trajectory.append({
                "iteration": iteration,
                "type": "final_answer",
                "content": final_answer,
                "tokens_estimate": iter_tokens,
                "duration_ms": round((time.monotonic() - iter_start) * 1000),
            })
            logger.info(
                "agent_final_answer",
                iteration=iteration,
                answer_preview=str(final_answer)[:200],
            )
            break

        # --- Process tool calls ---
        assistant_content = response.get("content", "")
        messages.append({
            "role": "assistant",
            "content": assistant_content or "",
        })

        for tool_call in response["tool_calls"]:
            tool_name = tool_call["name"]
            tool_args = tool_call["arguments"]

            # No-progress detection
            if progress.check(tool_name, tool_args):
                logger.warning(
                    "no_progress_detected",
                    iteration=iteration,
                    repeated_tool=tool_name,
                )
                breaker.record_failure()

            # HITL escalation check
            cost_estimate = total_tokens * 0.000003  # rough $/token estimate
            escalation_reason = escalation_policy.should_escalate(
                tool_name=tool_name,
                cumulative_cost_usd=cost_estimate,
            )
            if escalation_reason:
                approved = await request_human_approval(
                    run_id, escalation_reason,
                    {"tool": tool_name, "args": tool_args, "iteration": iteration},
                )
                if not approved:
                    termination_reason = f"hitl_rejected_{escalation_reason.value}"
                    trajectory.append({
                        "iteration": iteration,
                        "type": "hitl_rejected",
                        "reason": escalation_reason.value,
                        "tool": tool_name,
                    })
                    break

            # Execute tool
            logger.info(
                "tool_execution_start",
                iteration=iteration,
                tool=tool_name,
                args_preview=str(tool_args)[:200],
            )
            tool_result = await execute_tool(tool_name, tool_args)

            if tool_result.startswith("Error"):
                breaker.record_failure()
            else:
                breaker.record_success()

            # Append tool result to conversation
            messages.append({
                "role": "user",
                "content": f"Tool '{tool_name}' returned: {tool_result}",
            })

            trajectory.append({
                "iteration": iteration,
                "type": "tool_call",
                "tool": tool_name,
                "arguments": tool_args,
                "result_preview": tool_result[:500],
                "tokens_estimate": iter_tokens,
                "duration_ms": round((time.monotonic() - iter_start) * 1000),
            })
        else:
            # Only checkpoint if the inner loop completed without break
            checkpoint_store.save(run_id, iteration, {
                "messages": messages,
                "trajectory": trajectory,
                "total_tokens": total_tokens,
            })
            continue

        # Inner loop was broken (HITL rejection)
        break

    cost_estimate_usd = total_tokens * 0.000003  # Replace with actual pricing

    result = AgentRunResult(
        run_id=run_id,
        final_answer=final_answer,
        iterations=iteration + 1 if 'iteration' in dir() else 0,
        total_tokens_estimate=total_tokens,
        cost_estimate_usd=cost_estimate_usd,
        termination_reason=termination_reason,
        trajectory=trajectory,
    )

    logger.info(
        "agent_completed",
        run_id=run_id,
        iterations=result.iterations,
        termination_reason=termination_reason,
        total_tokens=total_tokens,
        cost_usd=round(cost_estimate_usd, 4),
    )

    return result


# ---------------------------------------------------------------------------
# Example: Register tools and run
# ---------------------------------------------------------------------------

def lookup_customer(customer_id: str) -> dict[str, Any]:
    """Look up customer details by ID."""
    # Replace with actual database/API call
    return {
        "customer_id": customer_id,
        "name": "Acme Corp",
        "tier": "enterprise",
        "open_tickets": 3,
    }


def search_knowledge_base(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search internal knowledge base for relevant articles."""
    # Replace with actual search implementation
    return [
        {"title": "Billing FAQ", "relevance": "0.92", "snippet": "..."},
        {"title": "Account Setup Guide", "relevance": "0.87", "snippet": "..."},
    ]


# Register tools at import time
register_tool(
    name="lookup_customer",
    description="Look up customer details by their unique ID. Returns name, tier, and open ticket count.",
    parameters={
        "type": "object",
        "properties": {
            "customer_id": {"type": "string", "description": "The customer's unique identifier"},
        },
        "required": ["customer_id"],
    },
    func=lookup_customer,
)

register_tool(
    name="search_knowledge_base",
    description="Search the internal knowledge base for articles matching a query.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query text"},
            "max_results": {"type": "integer", "description": "Maximum results to return", "default": 5},
        },
        "required": ["query"],
    },
    func=search_knowledge_base,
)


async def main() -> None:
    """Example: run agent on a customer service task."""
    result = await run_agent(
        task="Customer C-12345 is asking about their billing cycle. "
             "Look up their account and find relevant knowledge base articles.",
        max_iterations=10,
        escalation_policy=EscalationPolicy(
            confidence_threshold=0.6,
            max_cost_per_task_usd=2.0,
        ),
    )
    print(f"\n--- Agent Result ---")
    print(f"Run ID: {result.run_id}")
    print(f"Answer: {result.final_answer}")
    print(f"Iterations: {result.iterations}")
    print(f"Tokens: {result.total_tokens_estimate}")
    print(f"Cost: ${result.cost_estimate_usd:.4f}")
    print(f"Termination: {result.termination_reason}")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario 1: Document Processing Pipeline

**Problem**: "Design a document processing pipeline that handles 50K documents/day with multi-step extraction, validation, and human review, achieving 99.5% accuracy."

**Component Diagram**:

```
 ┌─────────────────────────────────────────────────────────────────────────────────┐
 │                     DOCUMENT PROCESSING PIPELINE                                │
 │                                                                                 │
 │  ┌─────────────┐    ┌──────────────────────────────────────────────────────┐   │
 │  │ Ingestion   │    │              DAG WORKFLOW ENGINE                     │   │
 │  │ Gateway     │    │              (LangGraph StateGraph)                  │   │
 │  │             │    │                                                      │   │
 │  │ - S3 / SFTP │    │  ┌────────────┐  ┌────────────┐  ┌──────────────┐  │   │
 │  │ - 50K/day   │───▶│  │ CLASSIFY   │─▶│ EXTRACT    │─▶│ VALIDATE     │  │   │
 │  │ - Dedup     │    │  │ (router)   │  │ (fan-out   │  │ (rule engine │  │   │
 │  │ - Priority  │    │  │            │  │  by field   │  │  + LLM cross-│  │   │
 │  │   queue     │    │  │ Doc type → │  │  group,     │  │  check)      │  │   │
 │  │             │    │  │ specialist │  │  3-5 agents │  │              │  │   │
 │  └─────────────┘    │  │ agent      │  │  parallel)  │  │ Confidence < │  │   │
 │                     │  │            │  │            │  │ 0.95 → HITL  │  │   │
 │                     │  └────────────┘  └────────────┘  └──────┬───────┘  │   │
 │                     │                                          │          │   │
 │                     │                                          ▼          │   │
 │                     │                                   ┌──────────────┐  │   │
 │                     │                                   │ RECONCILE    │  │   │
 │                     │                                   │ (merge       │  │   │
 │                     │                                   │  extracted   │  │   │
 │                     │                                   │  fields,     │  │   │
 │                     │                                   │  reducer fn) │  │   │
 │                     │                                   └──────┬───────┘  │   │
 │                     └──────────────────────────────────────────┼──────────┘   │
 │                                                                │              │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────▼────────────┐  │
 │  │ HITL Review      │  │ Audit Store      │  │ Output Store               │  │
 │  │ Queue            │  │                  │  │                            │  │
 │  │ - Web UI for     │  │ - Every agent    │  │ - Structured JSON/DB      │  │
 │  │   reviewers      │  │   decision       │  │ - Versioned extractions   │  │
 │  │ - Confidence-    │  │   logged         │  │ - Downstream API/webhook  │  │
 │  │   ranked batch   │  │ - EU AI Act      │  │                            │  │
 │  │ - Corrections    │  │   Art.12 compl.  │  │                            │  │
 │  │   feed back to   │  │ - 24-month       │  │                            │  │
 │  │   fine-tuning    │  │   retention      │  │                            │  │
 │  └──────────────────┘  └──────────────────┘  └────────────────────────────┘  │
 │                                                                                 │
 │  ┌─────────────────────────────────────────────────────────────────────────┐   │
 │  │ INFRASTRUCTURE                                                          │   │
 │  │ - Temporal: durable workflow execution, retry, HITL waits              │   │
 │  │ - PostgresSaver: checkpoint state after every node                     │   │
 │  │ - Model routing: Haiku for classification, Sonnet for extraction       │   │
 │  │ - Prompt caching: shared system prompt + tool schemas across docs      │   │
 │  │ - Auto-scaler: workers scale 1-50 based on queue depth                │   │
 │  └─────────────────────────────────────────────────────────────────────────┘   │
 └─────────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off Matrix**:

```
 ┌───────────────────┬──────────────────┬──────────────────┬──────────────────────┐
 │ Criterion         │ A: Single ReAct  │ B: Plan-and-     │ C: DAG Workflow      │
 │                   │ Agent            │ Execute Pipeline │ with Specialist       │
 │                   │                  │                  │ Agents               │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ Accuracy (99.5%   │ 92-95%: single   │ 96-98%: planner  │ 99.5%+: specialist  │
 │ target)           │ agent drifts on  │ catches steps    │ agents with clean    │
 │                   │ complex docs     │ but executor     │ context per field    │
 │                   │                  │ shares context   │ group + HITL for     │
 │                   │                  │                  │ low confidence       │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ Throughput        │ Sequential: ~2K  │ Sequential:      │ Parallel: 50K+/day  │
 │ (50K/day target)  │ docs/day         │ ~8K docs/day     │ with fan-out and    │
 │                   │ (bottleneck:     │ (cheaper exec)   │ worker auto-scaling │
 │                   │ frontier model   │                  │                      │
 │                   │ per step)        │                  │                      │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ Cost per doc      │ $0.15-0.25       │ $0.06-0.10       │ $0.04-0.08          │
 │                   │ (frontier model  │ (frontier plans, │ (Haiku classifies,  │
 │                   │ every step)      │ Haiku executes)  │ Sonnet extracts,    │
 │                   │                  │                  │ prompt caching)     │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ Implementation    │ Low: one prompt, │ Medium: planner  │ High: graph design, │
 │ complexity        │ one loop         │ + executor +     │ reducers, HITL      │
 │                   │                  │ routing          │ queue, auto-scaler  │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ Failure           │ Entire doc fails │ Partial: failed  │ Granular: one field │
 │ isolation         │ on any step      │ step can be      │ group fails without │
 │                   │ failure          │ retried          │ affecting others    │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ HITL integration  │ All-or-nothing   │ Per-step review  │ Per-field-group     │
 │                   │ review           │ possible         │ confidence-routed   │
 │                   │                  │                  │ review batches      │
 └───────────────────┴──────────────────┴──────────────────┴──────────────────────┘
```

**Decision rationale**: Option C (DAG Workflow with Specialist Agents) is the correct choice. The 99.5% accuracy target and 50K/day throughput rule out single-agent approaches -- ReAct cannot parallelize, and its context window fills with extraction results from prior fields, degrading accuracy on later fields. Plan-and-Execute improves cost but remains sequential and shares context across steps. The DAG approach solves both problems: specialist agents per field group operate with clean context windows (eliminating context exhaustion), parallel fan-out achieves throughput through horizontal scaling, and confidence-based HITL routing targets reviewer effort at the ~5% of extractions that need it rather than reviewing everything. The cost premium of multiple agent calls is offset by using cheaper models (Haiku for classification, Sonnet for extraction) and aggressive prompt caching (identical system prompts across all 50K documents). Temporal provides infrastructure-level durability so that a worker crash does not lose progress on a partially processed document.

**Framework selection**: LangGraph StateGraph (DAG with conditional edges) on top of Temporal for durable execution. Model routing via LiteLLM. PostgresSaver for checkpointing across multiple worker nodes.

### 6.2 Scenario 2: Enterprise Customer Service Agent System

**Problem**: "Design an enterprise customer service agent system handling 10K concurrent conversations with escalation policies, audit trails, and sub-200ms first response."

**Component Diagram**:

```
 ┌─────────────────────────────────────────────────────────────────────────────────┐
 │                  CUSTOMER SERVICE AGENT SYSTEM                                  │
 │                                                                                 │
 │  ┌──────────────┐                                                              │
 │  │ Channel      │    ┌──────────────────────────────────────────────────────┐  │
 │  │ Gateway      │    │             AGENT RUNTIME CLUSTER                    │  │
 │  │              │    │                                                      │  │
 │  │ - Chat / SMS │    │  ┌──────────────────────────────────────────────┐   │  │
 │  │ - Voice      │    │  │  INTENT ROUTER (cached classification)      │   │  │
 │  │ - Email      │    │  │  - Haiku w/ prefix caching (<80ms)          │   │  │
 │  │ - 10K conc.  │───▶│  │  - Route: billing / technical / account /  │   │  │
 │  │ - WebSocket  │    │  │    returns / general / escalate             │   │  │
 │  │ - Load bal.  │    │  └───────┬────────┬────────┬────────┬─────────┘   │  │
 │  │              │    │          │        │        │        │              │  │
 │  └──────────────┘    │          ▼        ▼        ▼        ▼              │  │
 │                      │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐     │  │
 │                      │  │Billing │ │Technic-│ │Account │ │Returns │     │  │
 │                      │  │Agent   │ │al Agent│ │Agent   │ │Agent   │     │  │
 │                      │  │        │ │        │ │        │ │        │     │  │
 │                      │  │Tools:  │ │Tools:  │ │Tools:  │ │Tools:  │     │  │
 │                      │  │-invoice│ │-diag-  │ │-profile│ │-order  │     │  │
 │                      │  │ lookup │ │ nostics│ │ update │ │ lookup │     │  │
 │                      │  │-payment│ │-ticket │ │-tier   │ │-refund │     │  │
 │                      │  │ adjust │ │ create │ │ change │ │ process│     │  │
 │                      │  │-plan   │ │-KB     │ │-pref   │ │-ship   │     │  │
 │                      │  │ change │ │ search │ │ mgmt   │ │ track  │     │  │
 │                      │  └───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘     │  │
 │                      │      │          │          │          │           │  │
 │                      │      └────────┬─┴──────────┴──────┬───┘           │  │
 │                      │               ▼                    ▼               │  │
 │                      │  ┌─────────────────┐  ┌────────────────────────┐  │  │
 │                      │  │ ESCALATION      │  │ RESPONSE CACHE         │  │  │
 │                      │  │ ENGINE          │  │                        │  │  │
 │                      │  │                 │  │ - Common Q&A: serve    │  │  │
 │                      │  │ Confidence <0.7 │  │   from cache (<50ms)  │  │  │
 │                      │  │ → human queue   │  │ - Semantic similarity │  │  │
 │                      │  │ Irreversible    │  │   matching on past    │  │  │
 │                      │  │ action → approve│  │   verified responses  │  │  │
 │                      │  │ 2+ SL breaches  │  │ - Invalidation on    │  │  │
 │                      │  │ → supervisor    │  │   policy changes      │  │  │
 │                      │  └────────┬────────┘  └────────────────────────┘  │  │
 │                      │           │                                        │  │
 │                      └───────────┼────────────────────────────────────────┘  │
 │                                  ▼                                           │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────────────────┐ │
 │  │ Human Agent      │  │ Audit Trail      │  │ Analytics                  │ │
 │  │ Console          │  │                  │  │                            │ │
 │  │                  │  │ - Per-agent       │  │ - CSAT by agent type      │ │
 │  │ - Full conv.     │  │   identity       │  │ - Resolution rate         │ │
 │  │   history        │  │ - Every decision │  │ - Escalation rate         │ │
 │  │ - Agent's draft  │  │   + reasoning    │  │ - Cost per resolution    │ │
 │  │   response       │  │ - Tamper-evident │  │ - Latency p50/p95/p99   │ │
 │  │ - Suggested      │  │ - 24-month       │  │ - Token usage trends    │ │
 │  │   actions        │  │   retention      │  │                            │ │
 │  │ - One-click      │  │ - Exportable for │  │                            │ │
 │  │   approve/edit   │  │   regulator      │  │                            │ │
 │  └──────────────────┘  └──────────────────┘  └────────────────────────────┘ │
 │                                                                                 │
 │  ┌─────────────────────────────────────────────────────────────────────────┐   │
 │  │ INFRASTRUCTURE                                                          │   │
 │  │ - K8s: auto-scale agent pods 10-200 based on concurrent conversations  │   │
 │  │ - Redis: session state, response cache, rate limits                     │   │
 │  │ - PostgreSQL: audit trail, conversation archive, analytics             │   │
 │  │ - Temporal: durable execution for multi-step resolutions               │   │
 │  │ - Model routing: Haiku (router, <80ms), Sonnet (specialist agents)     │   │
 │  └─────────────────────────────────────────────────────────────────────────┘   │
 └─────────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off Matrix**:

```
 ┌───────────────────┬──────────────────┬──────────────────┬──────────────────────┐
 │ Criterion         │ A: Single ReAct  │ B: Router +      │ C: Router +          │
 │                   │ Agent (all       │ Specialist       │ Specialist Agents    │
 │                   │ domains)         │ Agents (direct)  │ + Response Cache     │
 │                   │                  │                  │ + Escalation Engine  │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ First response    │ 300-500ms:       │ 250-400ms: two   │ <200ms: cache hits  │
 │ latency (<200ms   │ single large     │ LLM calls        │ (<50ms) for 40-60%  │
 │ target)           │ model call with  │ (router +        │ of queries; Haiku   │
 │                   │ all tool schemas │ specialist)      │ router (<80ms) for  │
 │                   │                  │                  │ rest                 │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ 10K concurrent    │ Fails: single    │ Manageable:      │ Achievable:          │
 │ conversations     │ model with 30+   │ specialists      │ cache offloads 40-  │
 │                   │ tool schemas per │ have 5-8 tools   │ 60%; remaining       │
 │                   │ call; context    │ each (less       │ conversations use    │
 │                   │ size explodes    │ context)         │ lightweight agents   │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ Accuracy          │ 85-90%: tool     │ 93-96%: domain   │ 95-98%: domain      │
 │                   │ selection errors │ focus improves   │ focus + verified     │
 │                   │ with 30+ tools   │ accuracy         │ response cache +    │
 │                   │                  │                  │ HITL for edge cases │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ Cost per          │ $0.08-0.15       │ $0.04-0.08       │ $0.02-0.05          │
 │ conversation      │ (frontier model  │ (Haiku routes,   │ (cache hits ~free,  │
 │                   │ every turn)      │ Sonnet handles)  │ Haiku routes,       │
 │                   │                  │                  │ Sonnet handles)     │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ Escalation        │ Coarse: whole    │ Per-domain       │ Granular: per-      │
 │ control           │ conversation     │ escalation       │ action confidence-  │
 │                   │ escalation only  │ thresholds       │ based + irreversible│
 │                   │                  │                  │ action gates        │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ Audit trail       │ Single agent ID: │ Per-agent ID:    │ Per-agent ID +      │
 │ granularity       │ undifferentiated │ domain-specific  │ per-action decision │
 │                   │ decision log     │ decision chains  │ chains with reason  │
 │                   │                  │                  │ codes               │
 ├───────────────────┼──────────────────┼──────────────────┼──────────────────────┤
 │ Implementation    │ Low              │ Medium           │ High                 │
 │ complexity        │                  │                  │                      │
 └───────────────────┴──────────────────┴──────────────────┴──────────────────────┘
```

**Decision rationale**: Option C (Router + Specialist Agents + Response Cache + Escalation Engine) is the correct choice. The sub-200ms first response requirement is the binding constraint -- neither pure ReAct nor direct routing achieves it consistently under load. The response cache solves this for the 40-60% of customer queries that are semantically similar to previously verified responses (common billing questions, standard return policies), serving them in <50ms without any LLM call. For novel queries, the Haiku-based intent router classifies in <80ms (achievable with prompt caching on the classification system prompt), then hands off to a domain-specialist agent with a focused tool set (5-8 tools instead of 30+), which reduces both context size and tool selection errors.

The 10K concurrent conversations requirement demands lightweight agents. Specialist agents with 5-8 tools use ~3x fewer context tokens than a single agent with all 30+ tools. Combined with cache offloading, peak LLM concurrency drops from 10K to ~4-6K simultaneous inference calls -- achievable with horizontal pod autoscaling.

The escalation engine provides the granular control enterprises require: confidence-based routing to human agents (threshold per domain), mandatory human approval for irreversible actions (refunds, account changes), and automatic supervisor notification on repeated SLA breaches. Each specialist agent has its own identity in the audit trail, with per-action decision chains and reason codes satisfying EU AI Act Article 12 requirements.

**Framework selection**: OpenAI Agents SDK or LangGraph for the router-specialist pattern (both support handoff/routing natively). Redis for session state and response cache. Temporal for multi-step resolutions that span multiple turns or require external approvals. PostgreSQL for the audit trail with tamper-evident logging.

> **Gap**: The research does not provide empirical data on response cache hit rates for customer service specifically. The 40-60% estimate is based on general customer service patterns (high frequency of common questions) and would need validation with actual conversation logs during a pilot phase.

---

## Sources

All claims sourced from 48 references consulted during research. Key sources by section:

- **Execution patterns**: Yao et al. 2022 (ReAct), Molinari et al. Dec 2025 (RP-ReAct), CLEAR Framework (Plan-Execute cost data)
- **Frameworks**: LangGraph docs, OpenAI Agents SDK docs, Google ADK 2.x, Microsoft Agent Framework RC
- **Benchmarks**: SWE-bench Verified, WebArena, GAIA/GAIA2, TAU-bench/tau2/tau3, UC Berkeley RDI reward hacking study
- **Durable execution**: Temporal (OpenAI Codex production usage), LangGraph checkpointing, Diagrid checkpointing gap analysis
- **Security**: OWASP LLM Top 10 (2026), OWASP Agentic AI Top 10, CVE-2026-22708, LiteLLM supply chain attack, Microsoft Agent Governance Toolkit
- **Compliance**: EU AI Act Article 12, CISA May 2026, Cisco RSA Conference 2026 readiness data
- **Failure modes**: Arize 2026 (88% infrastructure gap), Huang et al. ICLR 2024 (self-correction limits), EMNLP 2025 (hallucination detection)
- **Cost optimization**: NeurIPS 2025 (plan caching), Gauthier Jan 2025 (hybrid model pairing)
