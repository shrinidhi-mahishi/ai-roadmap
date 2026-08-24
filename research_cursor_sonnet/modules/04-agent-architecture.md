# 04. Agent Architecture

**Sub-areas covered**: ReAct interleaved reasoning/acting · agent-loop reference implementations (OpenAI Agents SDK, Google ADK, Claude Code) · graph-based orchestration (LangGraph StateGraph/Pregel model) · plan-and-execute / ReWOO / LLMCompiler / trained planners · composable deterministic termination contracts · durable execution (Temporal, LangGraph checkpointers) · distributed locking & concurrency control for shared agent state · Zero-Trust agent security & audit trajectories · production failure taxonomy (the $47K infinite-loop incident, state-tracking failures, binding drift)

---

## 1. System Topology & Data Flow

An agent-loop system differs from a request/response LLM API in exactly one structural way that dominates every downstream design decision: **the number of model calls per task is not known in advance**. A ReAct or graph-based agent may take one step or two hundred, so every layer of the architecture — control plane, execution plane, persistence, telemetry — has to be built around *variable-length, self-directed* iteration rather than a fixed pipeline. The control plane's job is specifically to bound that variability (termination, budget, RBAC) *before* each step is allowed to execute, not to observe it after the fact.

```
                                   ┌────────────────────────────────────────────────────────┐
                                   │                     CONTROL PLANE                        │
                                   │                                                           │
  ┌──────────┐   user goal /       │  ┌──────────────┐   ┌────────────────┐   ┌─────────────┐  │
  │  Client  │────────────────────▶│  │ Identity &   │──▶│  Termination    │──▶│ Cost/Budget │  │
  │ (caller) │                     │  │ RBAC Gateway │   │  Supervisor     │   │ Meter       │  │
  │          │◀────────────────────│  │ (per-step    │   │  (composable    │   │ (token/$/   │  │
  │          │  final answer /     │  │  policy check│   │  stop-hook:     │   │  wall-clock │  │
  │          │  run_id (async)     │  │  before exec)│   │  budget+        │   │  velocity,  │  │
  └──────────┘                     │  └──────┬───────┘   │  stagnation)    │   │  §3.1)      │  │
                                   │         │            └────────┬────────┘   └──────┬──────┘  │
                                   │         ▼                     ▼                    ▼         │
                                   │  ┌───────────────────────────────────────────────────────┐ │
                                   │  │  Kill-Switch / Circuit-Breaker Registry -- per-run AND  │ │
                                   │  │  per-(tool,dependency); CLOSED / OPEN / HALF_OPEN (§4.4)│ │
                                   │  └──────────────────────────┬────────────────────────────┘ │
                                   └─────────────────────────────┼──────────────────────────────┘
                                                                  │  allow-next-step? (pre-call, §2.6)
                                   ┌──────────────────────────────▼──────────────────────────────┐
                                   │                          DATA PLANE                          │
                                   │                                                               │
                                   │  ┌────────────────────────────────────────────────────────┐  │
                                   │  │  Agent Loop Controller (§2): ReAct Thought→Action→        │
                                   │  │  Observation, OR StateGraph superstep executor            │
                                   │  │  (Planner → Executor(s) → Router → repeat / finish)       │
                                   │  └───────┬───────────────────┬───────────────────┬─────────┘  │
                                   │          ▼                   ▼                   ▼            │
                                   │  ┌───────────────┐  ┌────────────────┐  ┌──────────────────┐  │
                                   │  │ Planner Node   │  │ Executor Node(s)│  │ Verifier / Router │  │
                                   │  │ (decompose goal│  │ (map plan step  │  │ Node (progress    │  │
                                   │  │  → plan / DAG, │  │  → tool call;   │  │  check, conditional│  │
                                   │  │  re-plan on    │  │  parallel fan-  │  │  edge, loop-back  │  │
                                   │  │  failure, §2.5)│  │  out via Send)  │  │  or finish, §2.3) │  │
                                   │  └───────┬────────┘  └───────┬────────┘  └────────┬─────────┘  │
                                   └──────────┼───────────────────┼────────────────────┼────────────┘
                                              │                   │                    │
                                   ┌──────────▼───────────────────▼────────────────────▼────────────┐
                                   │                       TOOL PROXY LAYER                           │
                                   │  ┌────────────────┐  ┌─────────────────┐  ┌────────────────────┐│
                                   │  │ External Tool / │  │ Sub-agent /      │  │ Human-Approval      ││
                                   │  │ MCP Server      │  │ Handoff Target   │  │ Gate (durable wait, ││
                                   │  │ (idempotency-   │  │ (scoped, fresh   │  │  workflow.wait_     ││
                                   │  │  keyed calls,   │  │  context per     │  │  condition; zero    ││
                                   │  │  §4.1)          │  │  sub-agent)      │  │  compute while open)││
                                   │  └────────────────┘  └─────────────────┘  └────────────────────┘│
                                   └───────────────────────────────┬──────────────────────────────────┘
                                                                   │
                                   ┌───────────────────────────────▼──────────────────────────────────┐
                                   │                         PERSISTENCE LAYER                          │
                                   │  ┌───────────────┐  ┌────────────────┐  ┌─────────────────────────┐│
                                   │  │ Checkpointer   │  │ Event/Activity  │  │ Immutable Audit Log      ││
                                   │  │ (LangGraph     │  │ History         │  │ (WORM; trajectory hash,  ││
                                   │  │  Postgres:     │  │ (Temporal:      │  │  decision + redaction    ││
                                   │  │  thread_id +   │  │  replay-on-     │  │  status per step, §4.5)  ││
                                   │  │  checkpoint_id,│  │  crash, no      │  │                          ││
                                   │  │  §4.2)         │  │  re-billing,    │  │                          ││
                                   │  │                │  │  §4.1)          │  │                          ││
                                   │  └───────────────┘  └────────────────┘  └─────────────────────────┘│
                                   └─────────────────────────────────────────────────────────────────────┘
                                                                   │
                                   ┌───────────────────────────────▼──────────────────────────────────┐
                                   │                    TELEMETRY / OBSERVABILITY SINKS                  │
                                   │  ┌───────────────┐  ┌────────────────┐  ┌─────────────────────────┐ │
                                   │  │ OTel GenAI     │  │ Loop/Stagnation │  │ Cost-Velocity &          │ │
                                   │  │ Tracing        │  │ Detector (hash  │  │ Rollback-Rate Dashboards │ │
                                   │  │ (invoke_agent →│  │  window over    │  │ (per-run $/step/wall-    │ │
                                   │  │  plan →        │  │  state deltas,  │  │  clock; alert at 2x      │ │
                                   │  │  execute_tool, │  │  §2.6)          │  │  baseline, §3.2)         │ │
                                   │  │  §4.5)         │  │                 │  │                          │ │
                                   │  └───────────────┘  └────────────────┘  └─────────────────────────┘ │
                                   └──────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) The **Client** submits a goal, either synchronously (short interactive tasks) or asynchronously (long-running, tool-heavy, or human-in-the-loop tasks receive a `run_id` immediately and poll or subscribe to streamed events). (2) Before the first model call, the **Identity & RBAC Gateway** resolves the caller's scoped identity — trust does not transfer between agents, so a sub-agent spawned mid-run must earn its own scoped permissions rather than inheriting the parent's (§4.5). (3) The **Termination Supervisor** evaluates the run's composable stop conditions (`MaxMessages`, `TokenBudget`, `Timeout`, `TextMention`, stagnation) *before* the next step is permitted to execute — this pre-call placement, not a post-hoc log review, is the single design decision that separates a contained run from an incident like §5.1's $47,000 loop. (4) The **Cost/Budget Meter** tracks per-run spend velocity; if the run is burning tokens faster than its historical baseline, the **Kill-Switch/Circuit-Breaker Registry** can halt the run independent of step count. (5) Once cleared, control passes to the **Agent Loop Controller** in the data plane — either a ReAct-style `while(true)` (assemble context → call model → dispatch action → observe → repeat) or a compiled `StateGraph` executing supersteps (§2.1–§2.3). (6) For plan-and-execute architectures, a **Planner Node** decomposes the goal into an ordered plan or DAG once, up front, isolating the expensive planning call from the cheaper per-step **Executor Node(s)**, which map each plan step to a tool call and can fan out in parallel when dependencies allow (§2.5's LLMCompiler pattern). (7) A **Verifier/Router Node** inspects the updated state after each step and returns the name of the next node — a deterministic Python/TypeScript function, never a further LLM call, so every transition is testable (§2.7) — routing either forward, back to the Planner for re-planning, or to a terminal node. (8) Actions dispatch through the **Tool Proxy Layer**: external tools/MCP servers (idempotency-keyed, since Activity-boundary retries must not double-execute a side effect, §4.1), a handoff target (a distinct sub-agent that gets fresh, narrow context rather than the full accumulated transcript, mitigating the O(N²) growth in §3.2), or a durable **Human-Approval Gate** that suspends the workflow without consuming compute. (9) Every step's state delta is written to the **Checkpointer** (LangGraph's Postgres-backed saver or Temporal's Event History) before the loop advances — this is what makes a crash-and-resume operation replay rather than restart, and what makes the audit log's trajectory reconstruction possible after the fact. (10) The full trajectory streams to **Telemetry**: an OTel GenAI `invoke_agent` span wraps the run, a `plan` span wraps the Planner's decomposition phase, `execute_tool` spans record each tool call as a sibling — and, running continuously alongside, the **Loop/Stagnation Detector** hashes state deltas over a sliding window to catch an agent oscillating between the same few states before the Cost Meter's budget alarm would otherwise be the only thing that eventually stops it.

---

## 2. Core Mechanics & Algorithms

### 2.1 The ReAct interleaving pattern

ReAct (Yao et al., 2022/2023) interleaves verbal reasoning with task-specific actions in a single loop — **Thought → Action → Observation**, repeated until termination — rather than treating reasoning (chain-of-thought) and acting (action generation) as separate phases. Reasoning traces induce, track, and revise action plans and handle exceptions ("reason to act"); actions pull fresh information from the environment into the reasoning context ("act to reason"), which is precisely what grounds the loop against hallucination relative to pure chain-of-thought — on HotpotQA/FEVER, ReAct reduces error propagation by checking its own reasoning against real observations rather than an internally-generated, unverified chain. Critically, reasoning traces are **sparse**, emitted only where most useful (exception handling, plan revision) rather than on every step — this keeps token overhead bounded relative to always-verbose CoT, a design choice that directly determines the cost profile in §3.1.

```
        ┌──────────┐  emit reasoning   ┌──────────┐   select action   ┌─────────────┐
        │ THOUGHT  │──────────────────▶│  (implicit;│───────────────▶│    ACTION    │
        │ (sparse, │                   │  same LLM  │                 │ (tool call / │
        │  exception│                  │  turn)     │                 │  handoff /   │
        │  -driven) │                  └──────────┘                  │  final answer)│
        └──────────┘                                                  └──────┬───────┘
             ▲                                                                │
             │                                                                ▼
             │                                                        ┌──────────────┐
             └────────────────────────────────────────────────────────│ OBSERVATION   │
                        re-inject result into context, loop            │ (tool result / │
                                                                        │  environment   │
                                                                        │  state)        │
                                                                        └──────────────┘
```

**Invariant**: the loop terminates only when the model emits a terminal action (final-answer classification) or an external stop condition trips (§2.6) — ReAct itself defines no termination guarantee; that guarantee must be supplied by the surrounding controller, which is exactly the gap every 2026 production framework closes with an explicit, deterministic stop-hook layered on top (§2.6, §5.1).

### 2.2 Agent execution loop — reference implementations (state machines)

Three production reference implementations converge on the same shape while differing in persistence strategy:

- **OpenAI Agents SDK `Runner` loop**: (1) call the current agent's model with prepared input; (2) if output matches `agent.output_type` with no tool calls, terminate; (3) if the model requests a handoff, swap the current agent and re-loop; (4) if the model emits tool calls, execute them, append results, and re-loop; (5) if `max_turns` is exceeded, raise `MaxTurnsExceeded` (an explicit, deterministic budget check, not a model self-assessment). A single `Runner.run()` call is one logical conversation turn but may span many LLM calls across handoffs/tools. Interrupted runs (e.g., pending tool approval) serialize to a `RunState` via `to_state()` for exact resumption — the SDK's own checkpoint primitive.
- **Google ADK**: an event-driven `Runner` ↔ execution-logic loop using an ask-yield pattern. Every time the agent yields an `Event` (partial output, tool call, tool result), the Runner persists it via `SessionService`/`ArtifactService`/`MemoryService` and applies its `state_delta` before forwarding upstream. Because every event is appended to an **append-only session log**, durability is structural, not a bolted-on checkpoint mechanism — a resumed run simply replays the event log. ADK 2.0 added native `Workflow` graph/DAG support, letting teams compose deterministic tool/HITL steps with open-ended LLM-driven steps in one graph — a direct hybridization of §2.3's graph model with §2.1's ReAct model.
- **Claude Code**: the entire loop is one long-running asynchronous generator implementing ReAct's `while(true)` — assemble context → call model → dispatch tools → check permissions → execute → repeat — via a 9-step per-turn pipeline (settings resolution → state init → context assembly → context-compaction shapers → model call → tool dispatch → permission gate → tool execution → stop-condition check). Reverse-engineering estimates only ~1.6% of the codebase is AI decision logic; the remaining ~98.4% is deterministic infrastructure — permission gates, context management, tool routing, recovery — which is the concrete illustration of this module's central thesis: **the loop's reliability comes almost entirely from the deterministic scaffolding around the model call, not from the model call itself.**

```
     ┌───────────┐  tools/handoffs sent  ┌─────────────┐
     │  IDLE /   │──────────────────────▶│ MODEL_CALL  │
     │  compose  │                       └──────┬──────┘
     └───────────┘                              │
           ▲                     output classified as?
           │             ┌──────────────┼───────────────┬───────────────┐
           │             ▼              ▼                ▼               ▼
           │      final_output    tool_call(s)      handoff        max_turns
           │      (matches         │                requested      exceeded
           │      output_type)     ▼                    │               │
           │             │   ┌───────────┐              │               ▼
           │             │   │ EXECUTE +  │              │        ┌────────────┐
           │             │   │ append     │              │        │ RAISE       │
           │             │   │ tool_result│              │        │ MaxTurns    │
           │             │   └─────┬──────┘              │        │ Exceeded    │
           │             │         │                     ▼        └────────────┘
           │             │         │              ┌─────────────┐
           │             │         │              │ swap current │
           │             │         │              │ agent, reset │
           │             │         │              │ scoped state │
           │             │         │              └──────┬──────┘
           │             ▼         ▼                      │
           │      ┌────────────┐  └──────────────────────┘
           └──────│ TERMINATE  │
                  │ (return    │
                  │  final     │
                  │  output)   │
                  └────────────┘
```

### 2.3 Graph-based orchestration (LangGraph / Pregel model)

Three primitives compose a graph: **State** — a shared `TypedDict`/Pydantic schema updated per-key via *reducer* functions (default: overwrite; e.g., `add_messages` appends rather than replaces); **Nodes** — sync/async functions receiving the full state and returning a partial update; **Edges** — normal (unconditional `A → B`), conditional (a deterministic routing function inspects state and returns the next node name), or the `Send` API for map-reduce fan-out. A node with multiple outgoing edges executes **all destinations in parallel** within the same "superstep" — this is a message-passing/Pregel-inspired execution model, not a naive DFS traversal, which is why graph-based agents achieve native parallelism that a ReAct loop structurally cannot (§2.1's loop is strictly sequential; there is no concept of "two Thoughts in the same step"). `StateGraph(Schema) → add_node → add_edge/add_conditional_edges → .compile()` produces a runnable graph. **Loops are first-class**: a conditional edge can route back to an earlier node (e.g., `validator → analyst` on low quality), making cyclic graphs the natural representation of iterative refinement — something a plain DAG cannot express, and the reason graph-based agents subsume both pure-DAG plan-and-execute (§2.5) and ReAct (§2.1) as special-case topologies rather than competing with them.

### 2.4 Workflows vs. agents — the governing architectural distinction

Anthropic's framing is the load-bearing distinction for every design decision in this module: **workflows** orchestrate LLMs and tools through *predefined code paths* (predictable, bounded cost/latency, reproducible); **agents** let the LLM *dynamically direct its own process and tool use* (flexible, but trading latency and cost for task performance and enabling compounding errors). The recommended agent loop is: (1) begin with user input; (2) the agent plans and executes autonomously; (3) it requests human feedback at checkpoints; (4) at each step it obtains ground truth from the environment (tool results, code execution, test suites) to assess progress — never trusting its own unverified self-assessment; (5) it terminates on completion or an explicit stop condition. The practical guidance is to start with the simplest workflow pattern (prompt chaining, routing, parallelization, orchestrator-workers, evaluator-optimizer) and reach for a full autonomous agent only when the step count and order are genuinely unknowable in advance — a coding agent is the canonical example where the search space cannot be pre-enumerated.

### 2.5 Planning architectures — decoupling strategy from tactics

- **Plan-and-Execute (P-t-E)**: a **Planner** (LLM) decomposes a goal into an ordered multi-step plan; **Executor(s)** map each step to tool calls; after execution the agent re-invokes with a re-planning prompt to decide whether to finish or generate a follow-up plan. Architectural advantage over ReAct: the expensive planner LLM is not re-queried for every tool invocation, improving cost-efficiency and predictability (§3.1's per-call-cost comparison quantifies this). Disadvantage: rigidity if an early step's premise turns out false — this requires an explicit re-planning loop, without which P-t-E degenerates into "execute a plan that no longer matches reality."
- **ReWOO (Reasoning WithOut Observation)**: a Planner generates the *full* task plan upfront without waiting for intermediate results; a Worker executes tasks; a Solver synthesizes the final answer from all worker outputs at once — avoiding the token cost of re-reading every intermediate observation back into the planner's context, at the cost of being unable to adapt the plan mid-execution.
- **LLMCompiler**: the planner streams a **DAG of tasks** (each with a tool, arguments, and a dependency list); tasks with satisfied dependencies execute in parallel rather than serially. Reported **3.6x speedup** over sequential plan-and-execute/ReWOO by exploiting that most tool calls (search, sub-LLM calls) are I/O-bound and mutually independent — this is the concrete mechanism behind LangGraph's `Send`-API parallel fan-out in §2.3.
- **Plan-and-Act (trained, not merely prompted)**: a trained Planner + Executor pair, where a synthetic-data generation method (annotating ground-truth trajectories with feasible plans) trains the Planner without manual annotation. Achieves state-of-the-art **57.58%** on WebArena-Lite and **81.36%** on WebVoyager (text-only) — evidence that explicit, *trainable* planning outperforms single-model ReAct-style mapping specifically on long-horizon web tasks, where a prompted planner's plan quality is bounded by in-context reasoning alone.

**Security framing**: Plan-then-Execute's separation of planning from execution establishes *control-flow integrity* — a compromised tool output cannot as easily redirect the overall plan mid-stream, because the plan was fixed before any untrusted observation entered context. This makes P-t-E inherently more resilient to indirect prompt injection than a reactive ReAct loop, where every observation feeds directly into the next action decision. Recommended defense-in-depth complements: least-privilege tool scoping (§4.3), sandboxed execution, task-scoped credentials.

### 2.6 Loop termination — the 2026 consensus pattern (deterministic, pre-call, composable)

Industry practice has moved decisively away from "the model decides when it's done" toward composable, deterministic, pre-call termination contracts — this is the single highest-leverage architectural decision in this entire module, evidenced concretely by §5.1's incident.

- **Composable Termination Conditions**: independent primitives (`MaxMessages`, `TokenBudget`, `TextMention`, `FunctionCall`, `Handoff`, `Timeout`, `ExternalSignal`, `Cancellation`) evaluated by a single supervisor each step, combined via AND/OR, with the tripped condition logged for postmortem.
- **Stop Hook**: a programmatic predicate run after every step, returning `continue | stop-success | stop-failure`, based on target-reached, step-budget, error-class, or stagnation signals — explicitly forbidding any other loop-exit path. A second LLM call deciding whether the first LLM call is finished is *not* a control; it is "a second thing that can fail" in the exact same way.
- **Bounded Agentic Loop**: wraps `send → check stop_reason → run tool → feed result back → repeat` in hard budgets (turns, tokens, cost) plus a wall-clock timeout, progress detection, and an out-of-band kill switch — the budget guard sits specifically on the "run another tool" edge, where autonomy compounds fastest.
- **Stagnation/no-progress detection**: compare state across a window (e.g., last 4+ steps) and halt on identical or oscillating actions. This is the condition every pure budget check catches only *after* wasting the budget — a step-count cap alone will happily run to 100% of budget on a stuck agent, while a stagnation detector can catch it at step 5.
- **Practical calibration**: run representative tasks with a generous budget, take the step count at the 95th percentile of *successful* runs, and set the hard cap slightly above it — a cap set by guesswork either kills good runs prematurely or fails to catch bad ones early enough to matter.

**Convergence property (formal statement)**: a loop with only a budget cap is guaranteed to terminate (trivially, at the cap) but is not guaranteed to terminate *usefully* — it may exhaust its entire budget in a stagnant or oscillating state. A loop with budget + stagnation detection is guaranteed to terminate at `min(budget, stagnation_window × detection_latency)`, which is the practically meaningful termination guarantee production systems actually rely on.

### 2.7 Typed agent state schemas and deterministic routing

Best-practice state design splits the schema into three layers: **(1) Input** — user-provided data, set once, never mutated; **(2) Pipeline state** — accumulated results (intermediate findings, tool outputs) written incrementally by nodes; **(3) Control/debug state** — iteration counters, routing decisions, error logs, trace metadata, used both for loop-safety (e.g., an `analyze_attempts` counter preventing infinite loops, directly implementing §2.6's budget primitive at the schema level) and observability. The schema — Pydantic, Zod, or TypeBox — is a versioned, immutable *data contract*; state transitions are named and validated rather than free-text status fields, and routing between states uses deterministic functions, never further LLM calls, so every transition is unit-testable and reproducible independent of model sampling variance.

### 2.8 Complexity and algorithmic notes

- **ReAct loop**: `O(k)` sequential model round trips for `k` steps; no pipelining is possible within a single trajectory since step `i+1` depends on step `i`'s observation. Wall-clock latency scales linearly with step count (§3.2).
- **Plan-and-Execute**: `O(1)` planner calls + `O(k)` executor calls, where executor calls can often route to smaller/cheaper models — the cost asymmetry directly drives §3.1's per-task cost advantage over pure ReAct on long tasks.
- **LLMCompiler / graph fan-out**: `O(depth)` sequential rounds where `depth` is the DAG's critical-path length, not its total task count — independent branches collapse into the same round via parallel execution, which is why the empirical 3.6x speedup tracks the ratio of total tasks to critical-path depth on I/O-bound workloads.
- **Naive context replay**: because most APIs bill the full conversation history on every call, a `k`-step loop that never trims context incurs `O(k²)` cumulative token cost, not `O(k)` — this is a distinct axis from round-trip count and is the primary driver of §3.1's economics.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost per completed task, not per token

The relevant unit of agent economics is **cost per completed task** (including cleanup/retry cost for failed trajectories), not cost per token or per API call. Agentic workflows consume an estimated **5-30x more tokens per task** than a single chatbot query, because a task triggers 10-20 model calls (reasoning, tool calls, verification, self-correction) instead of one. A cross-model analysis of agentic coding trajectories found agentic tasks can consume **up to 1,000x more tokens** than simple code chat, with **up to 30x run-to-run variance** on the same task — variance that a fixed per-task price estimate cannot capture, and that is itself an argument for tracking cost distributions (p50/p95 of $-per-task), not just a mean.

**Reliability-adjusted cost dominates list price.** Per-token price and per-attempt cost can favor one model while the *success-and-reliability-adjusted cost per correct task* favors another by a wide margin, driven by the "cleanup" term for failed trajectories — a model holding consistent success across repeated runs is what buys safe unattended operation, since inconsistency compounds specifically in multi-step loops (a single flaky step can invalidate an entire trajectory). **Decision rule**: if cleanup cost exceeds ~5-10x a single attempt's cost, pay the premium for the more reliable model; otherwise the token-efficient model wins on pure unit economics.

### 3.2 Quadratic token growth in naive loops

Because LLM APIs bill the entire conversation history on every call, a naive agent loop incurs **O(N²)** token cost: turn *k* re-sends all *k-1* prior turns. A 20-step loop can consume **>10x** the tokens a naive linear per-step estimate would suggest. Mitigations: scope-limited context per step (§2.7's typed-state layering enables this directly — pipeline state can be summarized/pruned independent of control state), state resets between phases, and coordinator-specialist patterns where sub-agents receive fresh, narrow context rather than the accumulated transcript (§1's handoff-target proxy).

| Metric | Alert Threshold |
|---|---|
| Token-per-task | >2x established baseline |
| Cost-per-completion | Daily spend exceeds historical baseline |
| Loop iterations per task | >2x baseline (signals retry loops / plan staleness) |
| Context utilization ratio | >85% of max context window |
| Per-subagent cost share | Orchestrator consuming >10-15% of total run cost |

### 3.3 Prompt caching economics across loop iterations

An agent's system prompt, tool definitions, and few-shot examples are byte-identical every turn, so marking that stable prefix cacheable is the single highest-leverage cost lever available in a loop: cache reads cost roughly **0.1x** fresh input-token price (~90% discount), though cache *writes* carry a ~1.25x premium and entries expire on a short TTL. Caching wins precisely for high-frequency, same-prefix loops — the more steps a trajectory has, the more turns amortize the one-time write premium. Caching fails to help on the first turn of every conversation, whenever any tool is added/removed/updated (invalidating the whole cached prefix, which matters for handoff-heavy multi-agent loops where the active tool set changes across steps), and on short sessions (<~5 turns) that don't amortize the write cost.

**Output-to-input tax.** Output tokens typically cost 3-5x input tokens. A ReAct loop that narrates its reasoning verbosely on every Thought, echoes whole tool results back into its own output, or returns unbounded observations has a bill dominated by what it *writes* at every single step — this compounds specifically in loops because it recurs `k` times, unlike a single-call system where it's a one-time tax. Constraining output format (structured diffs over full-file echoes, capped list lengths in observations) often saves more than switching models.

**Full per-task cost formula**, applied across every turn in a variable-length loop:

```
cost_per_task = Σ over all k turns of:
    (uncached_input_tokens_k  × input_rate  × (1 − cache_hit_rate_k))
  + (cached_input_tokens_k    × input_rate  × 0.1)
  + (output_tokens_k          × output_rate)
  + (thinking_tokens_k        × output_rate)
  ÷ 1,000,000   → dollar figure
```

The provider's pricing-page number describes exactly one uncached, one-shot call — an agent loop is many turns over a growing transcript, so the naive "price × tokens" estimate systematically understates real cost unless it explicitly sums over the (variable, and often heavy-tailed) turn count.

**$ per 1,000 completed runs — architecture comparison** (illustrative, frontier-tier pricing ~$5/MTok input / $25/MTok output, assuming a moderate-complexity task requiring ~8 reasoning/tool steps; figures are directional, not vendor-quoted):

| Architecture | Assumptions | $/run | **$ per 1k runs** |
|---|---|---|---|
| ReAct, no caching, full context replay each step | 8 steps, context grows ~500 tokens/step, no cache | ~$0.19 | **~$190 per 1k runs** (O(N²) replay dominates) |
| ReAct, with prompt caching on stable prefix (80% hit rate) | Same 8 steps; tool-schema + system prompt cached | ~$0.09 | **~$90 per 1k runs** (~53% reduction) |
| Plan-and-Execute, 1 planner call + 8 cheap-model executor calls | Planner on frontier model, executors on a smaller/cheaper tier | ~$0.06 | **~$60 per 1k runs** (planner isolation avoids re-paying planning cost per step) |
| Graph/DAG (LLMCompiler-style), 8 tasks at critical-path depth 3 | Parallel fan-out collapses 8 sequential calls into 3 sequential rounds | ~$0.07 | **~$70 per 1k runs** `[cost per-call is similar to Plan-and-Execute; the win here is latency, not $, since parallel calls are still billed individually]` |

### 3.4 Latency SLA targets — why P50 lies for multi-step loops

Task completion time for a multi-step agent is the **sum across every sequential call in the chain**, and tail latency compounds *multiplicatively*, not additively. Classic distributed-systems tail-amplification (P99/P50 ratio of 5-10x) is worse for LLM-backed steps, which routinely show **20-50x** P99/P50 ratios due to streaming/decode variance, provider-side queueing, and variable output length.

**Tail-at-scale applied to agent loops**: for a 5-hop chain (plan → retrieve → tool call → tool call → synthesize) where each hop independently has a 5% chance of hitting its slow threshold, the probability *all five* stay fast is `0.95^5 ≈ 0.77` — meaning **23% of requests eat at least one tail event**, even though each individual hop looks fine 95% of the time. This ratio worsens geometrically with step count, which is a direct, quantifiable argument for keeping loops short (favoring Plan-and-Execute's bounded step count over an open-ended ReAct loop) whenever the task allows it.

**SLO design for agent loops**: (1) pick the end-to-end percentile the user actually feels — P95 for most interactive UX, P99 for stricter guarantees; (2) split time-to-first-token (target <2s for interactive surfaces) from end-to-end wall-clock (may reasonably sit at 45s P95 for ticket triage, 8 minutes for nightly reconciliation); (3) decompose the end-to-end budget into **per-hop budgets** worked backward from the target; (4) track per-hop latency histograms, not just end-to-end, because the end-to-end P99 tells you *that* you have a tail but not *where in the loop* it lives. A hard timeout below the point where partial work stops being useful should route to **escalation**, not a bare error — a run that gives up at 90s with a partial draft and its retrieved context beats one that burns 4 minutes and returns nothing.

**P99 targets `[inferred/recommended]`.** No vendor publishes P99 figures for agent-loop wall-clock time, so the P99 column below is an architect-constructed design target, not a measured benchmark. It is derived by applying a **1.5-2x multiplier over the table's own P95 figures** — narrower than the 20-50x P99/P50 ratio cited above (which describes *single-call* decode-time variance) because by P95 most of that per-call variance has already been absorbed; the remaining P95→P99 gap is dominated by *rare, whole-trajectory* failure modes (a retry storm on one hop, a provider-side queueing spike, a pathological tool-call retry) rather than routine decode jitter. Treat the multiplier itself as a starting assumption to recalibrate once real per-hop histograms (item 4 above) are available.

| Loop architecture | Step count (typical) | P50 total | P95 total `[inferred]` | P99 total `[inferred/recommended]` | Mitigation |
|---|---|---|---|---|---|
| ReAct, simple task | 2-4 | 4-12s | 20-30s | 32-60s (~1.6-2x P95) | P95: favor for short tasks — no planner-tax overhead. **P99**: speculative early-exit — return the best answer found so far if a cheap sufficiency check passes, rather than waiting out a stalled final step. |
| ReAct, complex task | 10-20 | 30-90s | 3-5 min | 5-9 min (~1.6-1.8x P95) | P95: stagnation detector to cut tail-heavy trajectories early. **P99**: hard step-count ceiling + fallback to partial result — once the ceiling is hit, return the last coherent intermediate state instead of continuing to retry. |
| Plan-and-Execute | 1 plan + 5-10 exec | 15-40s | 60-90s | 100-160s (~1.7x P95) | P95: parallel executor fan-out where dependencies allow. **P99**: parallel redundant execution of the historically-slowest executor step with first-to-finish semantics, so one straggler branch can't dominate the critical path. |
| Graph/DAG (LLMCompiler-style) | critical-path depth 2-4 | 8-20s | 30-45s | 50-80s (~1.7x P95) | P95: fastest for high-parallelism workloads; highest upfront design cost. **P99**: async/webhook pattern for outlier-long branches — detach the slow branch from the synchronous caller and notify on completion instead of holding the request open. |

### 3.5 Capacity planning — concurrency, not QPS

Because agent-loop latency is measured in tens of seconds to minutes (not the ~200ms of a classic web request), the correct capacity unit is **concurrency via Little's Law** (`L = λ × W`), not requests-per-second. Example: 10 requests/sec × 90s average residency = **900 concurrent in-flight runs** — the number that must be provisioned against provider rate limits, checkpoint-store connections, and internal API capacity.

**Fan-out multiplies real load**: if each in-flight run holds ~3 concurrent tool calls on average (a Plan-and-Execute run fanning out executors, or a graph superstep executing parallel branches), the 900 front-door concurrent runs become **2,700 simultaneous callers** at the downstream-tool tier — invisible on any request-rate graph, and the reason capacity planning that stops at the front door systematically undercounts real load.

**Binding-limit formula**: `Sustainable RPM = min(provider RPM limit, provider TPM limit ÷ avg tokens per request)`; `Required concurrency = ceil(sustainable throughput/sec × avg request duration)`. Apply 30-50% headroom for bursts/retries; monitor RPM/TPM utilization as a percentage of quota and alert at 70-80%, exactly as with a database connection pool. A documented real-world constraint: a large sales-recommendation pipeline hit a **300 requests/minute** platform ceiling that made naive per-task synchronous invocation infeasible at required scale (hundreds of thousands of tasks in a 9-hour nightly window), forcing a **message-queue-driven architecture** decoupling orchestration from execution — the queue absorbs burst-vs-limit mismatch that a synchronous per-run loop cannot.

> ⚠️ Gap: no public, verifiable benchmark quantifies agent-loop throughput ceilings specifically for LangGraph- or Temporal-hosted workflows at extreme scale (e.g., >1M concurrent runs); the figures above are drawn from single-company case studies and may not generalize. `[inferred]` for cross-framework applicability.

### 3.6 Non-functional requirements and trade-offs

- **Availability**: target 99.9% for the control plane (identity, termination supervisor, budget meter) specifically, since a control-plane outage either blocks all new runs (safe-fail) or — worse, if fail-open — removes the termination guarantee entirely for runs already in flight; fail-closed is the correct default for the termination supervisor even at the cost of availability.
- **RPO/RTO**: with Temporal-style event sourcing, RPO ≈ zero for completed Activities (a completed step's result is durably recorded and never re-executed on replay) and RTO ≈ time to replay Event History to the last checkpoint, typically seconds. For LangGraph's Postgres checkpointer, RPO is bounded by the checkpoint cadence (per-superstep by default) and RTO ≈ resume-from-last-checkpoint time.
- **Compliance**: agent trajectories often carry PII through multiple intermediate steps (not just a single request/response pair), which means redaction must run at *every* state-write boundary, not once at ingress — a single unredacted intermediate state write defeats the entire compliance posture regardless of how well the final output is scrubbed.
- **Central trade-off — autonomy vs. controllability**: the same "commitment dial" from §2.4 that trades cost/latency for task-generality also trades controllability for autonomy — a pure ReAct loop is maximally flexible but hardest to bound (§2.6), while a graph with pre-defined edges is easiest to bound but requires the branching structure to have been anticipated at design time (§2.3's "rigid unless designed for the actual branching that occurs" limitation). No architecture dominates on all axes simultaneously; the choice is a per-workload decision based on how well the task's step count and branching can be anticipated in advance.

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution for agent loops

Every agent loop built on a durable-execution substrate (e.g., a Temporal-style harness) is modeled as an event-sourced Workflow: outer loops, tool calls, waiting for human approval, and even sandboxed code execution are all durable end-to-end. The substrate durably records every Activity call/return in an **Event History**; on crash, the workflow deterministically **replays** that history to reconstruct in-memory state and resume exactly where it left off — without re-executing completed work or re-paying for already-made LLM calls (LLM call results are cached from first execution and reused verbatim on replay).

**Checkpoint boundary caveat.** Durable execution checkpoints at the **Activity boundary**, not inside an inference call — an in-flight LLM call that was mid-token when the worker died cannot resume mid-token; that Activity retries from scratch and is paid for again. This makes **idempotent tool calls** (via idempotency keys / dedup guards) mandatory for any side-effecting Activity, since a retry that recharges a card or double-books a resource is a correctness bug, not a performance issue.

**Human approval as a durable wait.** A `wait_condition()`-style primitive blocks without consuming worker compute; the pending decision lives in Event History (not RAM), so thousands of approvals can wait independently in an open state with zero worker CPU cost, and every decision produces a free audit trail. A bounded timeout (e.g., 24 hours) ensures the workflow doesn't hang forever if a human never responds — this is §2.6's termination contract extended to include "no human response" as a first-class stop condition, not an omission.

**Continue-As-New.** For very long-running loops, event history grows unbounded; a Continue-As-New pattern atomically completes the current run and starts a fresh run under the same workflow ID, carrying forward only essential state — the workflow appears continuous externally while internally resetting its history log, directly analogous to §3.2's context-compaction problem but expressed at the workflow-engine layer instead of the prompt layer.

**LangGraph checkpointer vs. Temporal — scope distinction.** A LangGraph-style checkpointer (paired with a persistent backend such as Postgres) provides durability *scoped to the graph* — an `interrupt()` suspends the graph, and the checkpointer makes that pause durable *if* backed by Postgres/SQLite/Redis (an in-memory checkpointer does not survive a crash). A general-purpose durable-execution engine persists the *whole system* — agent loops, external service calls, human waits, and timers — across multiple frameworks simultaneously. The two are complementary, not competing, at different scopes: use the graph checkpointer for intra-graph state, and the durable-execution engine when the workflow must survive process/infrastructure failures spanning multiple services.

### 4.2 Checkpointing granularity and pending-writes recovery

A Postgres-backed checkpointer persists checkpoints keyed by `(thread_id, checkpoint_ns, checkpoint_id)`. Beyond full super-step checkpoints, per-node (task) writes are persisted as each node in a super-step finishes — this is what enables "pending writes" recovery: if one node in a super-step fails, the already-successful nodes' writes are durable and are **not re-run on resume**, avoiding wasted re-computation and, more importantly, avoiding a duplicate side effect from a node that already succeeded. A `"sync"` durability mode persists every checkpoint synchronously before the next step starts, trading some latency for maximum durability — the correct default for any loop touching side-effecting tools, with async/eventual durability reserved for read-only or purely-advisory intermediate steps.

### 4.3 Distributed locking and race conditions on shared agent state

Concurrent multi-agent systems that touch shared mutable state are full distributed systems and inherit all the classic failure modes — race conditions, ordering violations, split-brain, partial-failure recovery. The presence of an LLM in the loop does not change whether the state layer is correct.

**Why classic DB locks fail for agents.** An agent's read-think-write cycle (read state → LLM reasons for 5-15 seconds → write result) is far longer than a typical database lock hold time; holding a pessimistic lock for the duration of an LLM call creates severe contention and connection-pool exhaustion. Distributed locks (Redlock, ZooKeeper) are appropriate only for **short-lived critical sections under ~100ms**; for the 5-15s agent read-think-write cycle, **optimistic concurrency control** is almost always preferable.

**Optimistic locking pattern.** Every state record carries a version number; reads capture the version; writes assert `version == captured_version` as a precondition. The first writer to commit succeeds and bumps the version; every subsequent writer with a stale version fails immediately (no silent overwrite) and must re-read + recompute + retry. This is the industry-standard fix for the "two agents both believe they booked the same resource" class of bug — one documented production incident described a 47-second full outage where two agents silently overwrote each other's writes in a shared cache, with no errors logged.

**Agentic Mutex (semantic lock).** For high-integrity or financial/compliance-sensitive actions, lock a *semantic token* representing the entity/workflow objective (e.g., `account:12345`) at the orchestration layer rather than a literal database row; add a lease TTL so a dead agent cannot hold the lock forever. For complex, hard-to-serialize work (e.g., software-engineering agents), avoid shared-state mutexes entirely by isolating each agent in its own ephemeral branch/container/DB clone, then resolving concurrent work deterministically at a final merge boundary via standard review.

**Formal results.** A 2026 concurrency-control benchmark for multi-agent LLM systems found: uncoordinated execution passes only **13%** of contended-workload trials; two-phase locking (2PL) deadlocks 0.81 times/trial with minimal speedup (1.04x); optimistic concurrency control (OCC) aborts 0.95 times/trial and is *slower* than serial execution (0.93x) at 1.83x token cost; a selective-recovery approach passes all 10 contended workloads within 5% of serial correctness at a 1.4x speedup and near-serial (1.15x) token cost — evidence that naive OCC, while conceptually correct, is not automatically cost-free in an LLM-agent context, since every abort-and-retry re-pays the LLM reasoning cost, not just a cheap database write retry.

### 4.4 Circuit breakers for loop-internal tool calls

A standard three-state circuit breaker (CLOSED → OPEN → HALF-OPEN) applied at the tool-dispatch boundary inside the loop: track per-dependency error rate over a rolling window; when it exceeds a threshold (e.g., 30% error rate over 60s, or 3 failures in 60s for external CRM/payment APIs), open the breaker and return a **structured error** (e.g., `CIRCUIT_OPEN`) to the agent instead of executing the call. Critically, the agent's system prompt/tool contract must define what to do on `CIRCUIT_OPEN`, or the agent will attempt unpredictable workarounds that bypass the breaker via a different code path — a breaker with no defined agent-facing contract is a breaker the agent can route around.

**2026 enhanced designs**: per-tool circuit isolation (independent state per tool/agent ID, not just hostname); confidence-aware tripping (trip when average response confidence drops below threshold, not just on hard errors); cost-aware tripping (trip on token-burn-rate/cost-budget breach — directly implementing §2.6's cost-velocity termination signal at the tool-dispatch layer); gradual HALF-OPEN recovery via exponential ramp-up (1, 2, 4, 8... test calls); a **DEGRADED** state between CLOSED and OPEN that disables risky tools, adds human review, or switches to a conservative model rather than going fully silent.

**Layered error-handling stack**: Layer 1 — retry with exponential backoff + jitter for transient errors; Layer 2 — multi-provider fallback chain, triggered only after Layer-1 retries are exhausted on the primary; Layer 3 — circuit breaker to stop hammering a persistently failing dependency. Rate-limit (429) handling is a **distinct mechanism** from circuit breaking: it should be handled by rate-aware retry honoring the `Retry-After` header, not by immediately tripping a failure-count-based breaker — sustained rate-limiting eventually exhausts retry budget and *then* counts as a breaker-relevant failure.

### 4.5 Failure taxonomy for loop-internal operations

| Class | Examples | Response |
|---|---|---|
| Transient | 429, 5xx, timeouts, DNS failures, flaky tool provisioning | Retry with exponential backoff + jitter |
| Permanent | 400/401/403, malformed schema, auth failure, invalid tool name | Never retry — fail fast to fallback tier or terminate the step |
| Poison-pill | A specific input that deterministically fails the same tool/step on every retry (e.g., a plan step whose premise is now false) | Detect via repeated-failure-on-identical-input hashing (§2.6's loop detector applies directly); quarantine and route to re-planning, don't retry indefinitely |

Idempotency keys on every mutating Activity, combined with this taxonomy, are what make retries safe by construction rather than merely "usually fine" — a retry on a *permanent* error is a wasted round trip at best and a masked bug at worst, since a permanent error retried enough times can look statistically like "eventually succeeded" when it actually succeeded via a side channel.

### 4.6 Enterprise security — Zero-Trust MCP, RBAC, PII, and auditability

**Zero Trust for agent loop actions.** No principal (human or machine) is implicitly trusted, and every access decision is evaluated **per-request**, not just at initial authentication — coarse-grained RBAC roles alone cannot deliver this granularity. The standard architecture: (1) every agent has a stable, unique cryptographic identity rather than shared/inherited credentials; (2) every tool call passes through an external policy-decision gateway *before* execution — deterministic and testable, external to the agent's own non-deterministic reasoning; (3) trust does not transfer between agents — sub-agents earn their own scoped permissions rather than inheriting a parent's (directly reflected in §1's Identity & RBAC Gateway sitting ahead of every step, not just the first one).

**Per-capability RBAC / fine-grained authorization progression.** The maturity ladder: **RBAC** (coarse roles) for simple single-tenant agents → **ABAC** (attribute-based, incorporating user context) for user-delegated agents acting on behalf of a specific person → **FGA** (Fine-Grained Authorization, Zanzibar-style relationship tuples) for multi-tenant enterprise agents needing per-resource, individually-revocable grants. **Per-tool scoping** is the single highest-leverage, lowest-effort investment: each tool gets the narrowest scope it can possibly need, enforced in three layers — the tool's own credential is provisioned with minimum scope; the orchestration layer attaches an authorization policy per tool asserting the originating user may act on the referenced resource *before* the call leaves the boundary; the tool itself performs a final per-call check using the carried identity. **Just-in-Time (JIT) elevation** keeps high-blast-radius tools (e.g., `email.send`) out of every role's standing scope, granting a single-resource, short-lived elevation on explicit request.

**PII filtering (detect → redact → audit).** The dominant architecture is a pass-through proxy with zero data retention: redaction happens *before* persistence, in-memory, between the moment an upstream system returns data and the moment it's written into agent state/audit storage — raw prompts, raw model outputs, and PII/PHI values are never written to durable storage in the first place; only safe derivatives are (decision outcome, reason codes, redaction-applied flag, risk scores). A **session-scoped token vault** — short-lived, in-memory-only, keyed to the conversation/session — maps stable tokens to real values for agents that need to reference the same PII across multiple loop steps, without ever persisting the real value to disk. A **hash-anchored audit proof** (e.g., SHA-256 computed before redaction) proves later that a specific invocation produced specific content without ever storing the disclosed content itself.

**Auditability of agent decision trajectories.** OpenTelemetry GenAI semantic conventions define standardized span shapes for model inference, tool execution, agent invocation, and — as of 2026 — **planning**. Key operation types: `invoke_agent` (splits `CLIENT` for remote agents vs. `INTERNAL` for in-process), `execute_tool` (span name includes the tool name), and the new `plan` operation — an `INTERNAL` span wrapping the agent's explicit planning/decomposition phase, with the planning LLM call as its child and resulting tool/task spans as siblings under the parent `invoke_agent` span. A single `trace_id` links the entire decision trajectory end-to-end. **Architectural rule**: tool spans are children of the *agent* span, not of the model-call span — the model span ends when the model emits a tool-call request, and tool execution happens afterward in application code; nesting the tool span inside the model span misrepresents the timeline and inflates apparent model latency. What to persist: data shape (token/byte counts, structural schema), sensitivity classification resolved at access time, semantic tags, and a content hash computed before redaction — never raw prompt/output content in the audit trail itself, giving regulators proof a disclosure occurred without a copy of what was disclosed.

---

## 5. Production Enterprise Code

The module below implements a runnable, self-contained **ReAct-style agent-loop controller** with every resilience pattern from §2 and §4 wired together: retries with exponential backoff + jitter, a per-tool circuit breaker (CLOSED→OPEN→HALF-OPEN), a fallback chain (primary tool → degraded response), structured logging with correlation IDs, a **max-iteration guard**, a **no-progress/stagnation detector** hashing state deltas over a sliding window, and a **checkpointer** persisting state after every step so a crashed run resumes instead of restarting. Standard library only.

```python
"""
react_loop_controller.py

Production-grade ReAct agent-loop controller implementing the
deterministic, pre-call termination contract from Sec 2.6/4 of
Module 04 (Agent Architecture):

  - max-iteration guard (hard step budget)
  - cost/token budget guard (cost-velocity termination)
  - no-progress / stagnation detector (state-delta hashing over a
    sliding window -- catches the failure mode that burned $47,000
    over 11 days in the canonical production incident, Sec 5.1)
  - per-tool circuit breaker (CLOSED -> OPEN -> HALF_OPEN)
  - retry with exponential backoff + jitter for transient tool errors
  - fallback to a degraded response when a tool is unavailable
  - structured JSON logging with a correlation ID per run
  - a checkpointer persisting state after every step, so a crashed
    run resumes from the last completed step instead of restarting

All external calls (model, tools) are injected as callables so this
module is fully testable without a live model API or tool backend.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import uuid
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Deque, Optional


# --------------------------------------------------------------------------
# 1. Structured logging with correlation IDs (Sec 4.6 auditability)
# --------------------------------------------------------------------------

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get() or "-"
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("react_loop")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"correlation_id":"%(correlation_id)s","msg":%(message)s}'
        )
    )
    handler.addFilter(CorrelationIdFilter())
    logger.handlers = [handler]
    logger.propagate = False
    return logger


log = configure_logging()


class run_scope:
    """Binds one correlation ID (the run_id) to every log line for a
    single agent trajectory -- required so the full Thought/Action/
    Observation sequence of one run can be reconstructed for audit
    (Sec 4.6)."""

    def __init__(self, run_id: Optional[str] = None):
        self.run_id = run_id or str(uuid.uuid4())
        self._token = None

    def __enter__(self) -> str:
        self._token = _correlation_id.set(self.run_id)
        return self.run_id

    def __exit__(self, *exc_info) -> None:
        _correlation_id.reset(self._token)


# --------------------------------------------------------------------------
# 2. Failure taxonomy (Sec 4.5)
# --------------------------------------------------------------------------

class ToolError(Exception):
    """`transient=False` marks permanent errors that must never be
    retried (auth failure, malformed args, unknown tool)."""

    def __init__(self, message: str, transient: bool = True):
        super().__init__(message)
        self.transient = transient


class BudgetExceededError(Exception):
    """Raised by the Termination Supervisor -- deterministic, pre-call,
    per-run (Sec 2.6). Never raised by the model deciding it is done;
    always raised by compiled code evaluating a hard limit."""


class StagnationDetectedError(Exception):
    """Raised when the state-delta hash repeats beyond threshold across
    a sliding window -- the control that would have stopped the
    canonical $47K/11-day incident (Sec 5.1) at the cost of a few
    dollars instead of tens of thousands."""


# --------------------------------------------------------------------------
# 3. Exponential backoff with full jitter (Sec 4.4, layer 1)
# --------------------------------------------------------------------------

def backoff_with_full_jitter(attempt: int, base_s: float = 0.2, cap_s: float = 8.0) -> float:
    upper_bound = min(cap_s, base_s * (2 ** attempt))
    return random.uniform(0, upper_bound)


def call_with_retry(fn: Callable[[], Any], max_attempts: int = 3,
                     base_s: float = 0.2, cap_s: float = 8.0) -> Any:
    last_error: Optional[ToolError] = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except ToolError as exc:
            last_error = exc
            if not exc.transient:
                log.info(json.dumps({"event": "retry_aborted_permanent_error", "reason": str(exc)}))
                raise
            if attempt < max_attempts - 1:
                delay = backoff_with_full_jitter(attempt, base_s, cap_s)
                log.info(json.dumps({"event": "retry_backoff", "attempt": attempt + 1,
                                      "delay_s": round(delay, 3)}))
                time.sleep(delay)
    assert last_error is not None
    raise last_error


# --------------------------------------------------------------------------
# 4. Circuit breaker: CLOSED -> OPEN -> HALF_OPEN, per tool (Sec 4.4)
# --------------------------------------------------------------------------

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold_ratio: float = 0.5
    window_size: int = 10
    cooldown_s: float = 15.0
    half_open_max_probes: int = 2

    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _outcomes: Deque[bool] = field(default_factory=lambda: deque(maxlen=10), init=False)
    _opened_at: float = field(default=0.0, init=False)
    _half_open_probes_used: int = field(default=0, init=False)

    def _failure_ratio(self) -> float:
        if not self._outcomes:
            return 0.0
        return sum(1 for ok in self._outcomes if not ok) / len(self._outcomes)

    def allow_request(self) -> bool:
        if self._state == BreakerState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = BreakerState.HALF_OPEN
                self._half_open_probes_used = 0
                log.info(json.dumps({"event": "breaker_half_open", "tool": self.name}))
            else:
                return False
        if self._state == BreakerState.HALF_OPEN:
            if self._half_open_probes_used >= self.half_open_max_probes:
                return False
            self._half_open_probes_used += 1
        return True

    def record_success(self) -> None:
        self._outcomes.append(True)
        if self._state == BreakerState.HALF_OPEN:
            self._state = BreakerState.CLOSED
            self._outcomes.clear()
            log.info(json.dumps({"event": "breaker_closed", "tool": self.name}))

    def record_failure(self) -> None:
        self._outcomes.append(False)
        if self._state == BreakerState.HALF_OPEN:
            self._trip("half_open_probe_failed")
            return
        if len(self._outcomes) >= self.window_size and self._failure_ratio() >= self.failure_threshold_ratio:
            self._trip("failure_ratio_exceeded")

    def _trip(self, reason: str) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = time.monotonic()
        log.info(json.dumps({"event": "breaker_open", "tool": self.name, "reason": reason,
                              "failure_ratio": round(self._failure_ratio(), 3)}))


# --------------------------------------------------------------------------
# 5. Termination Supervisor: max-iteration + cost-budget guard (Sec 2.6)
# --------------------------------------------------------------------------

@dataclass
class TerminationSupervisor:
    max_iterations: int = 25
    max_cost_usd: float = 5.00
    wall_clock_timeout_s: float = 300.0

    _iterations: int = field(default=0, init=False)
    _cost_usd: float = field(default=0.0, init=False)
    _started_at: float = field(default_factory=time.monotonic, init=False)

    def check_before_step(self) -> None:
        """Pre-call, deterministic, per-run -- evaluated in compiled
        code before the NEXT step is allowed to execute. This is the
        control that a post-hoc dashboard threshold can never be,
        because it runs before the spend happens, not after (Sec 5.1)."""
        if self._iterations >= self.max_iterations:
            raise BudgetExceededError(
                f"max_iterations ({self.max_iterations}) reached"
            )
        if self._cost_usd >= self.max_cost_usd:
            raise BudgetExceededError(
                f"max_cost_usd (${self.max_cost_usd:.2f}) reached at ${self._cost_usd:.2f}"
            )
        elapsed = time.monotonic() - self._started_at
        if elapsed >= self.wall_clock_timeout_s:
            raise BudgetExceededError(
                f"wall_clock_timeout_s ({self.wall_clock_timeout_s}s) reached"
            )

    def record_step(self, step_cost_usd: float) -> None:
        self._iterations += 1
        self._cost_usd += step_cost_usd
        log.info(json.dumps({
            "event": "step_recorded", "iteration": self._iterations,
            "cumulative_cost_usd": round(self._cost_usd, 4),
        }))


# --------------------------------------------------------------------------
# 6. No-progress / stagnation detector (Sec 2.6, 5.1)
# --------------------------------------------------------------------------

@dataclass
class StagnationDetector:
    """Hashes each step's (action, observation) pair into a sliding
    window and raises if the same state repeats beyond threshold --
    catches an agent oscillating between the same few states, which a
    pure step/cost budget only catches AFTER burning the full budget."""

    window_size: int = 6
    repeat_threshold: int = 3

    _history: Deque[str] = field(default_factory=lambda: deque(maxlen=6), init=False)

    def check(self, action: str, args: dict, observation: Any) -> None:
        payload = json.dumps({"action": action, "args": args, "observation": observation},
                              sort_keys=True, default=str)
        state_hash = hashlib.sha256(payload.encode()).hexdigest()
        repeats = sum(1 for h in self._history if h == state_hash)
        self._history.append(state_hash)
        if repeats + 1 >= self.repeat_threshold:
            log.info(json.dumps({"event": "stagnation_detected", "action": action,
                                  "repeats": repeats + 1}))
            raise StagnationDetectedError(
                f"action '{action}' produced an identical (args, observation) state "
                f"{repeats + 1} times within the last {self.window_size} steps"
            )


# --------------------------------------------------------------------------
# 7. Checkpointer -- persists state after every step (Sec 4.1, 4.2)
# --------------------------------------------------------------------------

@dataclass
class InMemoryCheckpointer:
    """Stand-in for a Postgres/Redis-backed checkpointer. In production
    this writes to durable storage keyed by (run_id, step_index) so a
    crashed run resumes from the last completed step rather than
    restarting -- never re-executing an already-committed side effect."""

    _checkpoints: dict[str, list[dict]] = field(default_factory=dict, init=False)

    def save(self, run_id: str, step_index: int, state: dict) -> None:
        self._checkpoints.setdefault(run_id, []).append({"step": step_index, "state": state})
        log.info(json.dumps({"event": "checkpoint_saved", "step": step_index}))

    def load_latest(self, run_id: str) -> Optional[dict]:
        history = self._checkpoints.get(run_id, [])
        return history[-1]["state"] if history else None


# --------------------------------------------------------------------------
# 8. Tool dispatch: circuit breaker + retry + fallback (Sec 4.4)
# --------------------------------------------------------------------------

@dataclass
class ToolDispatcher:
    tool_fn: Callable[[dict], dict]
    breaker: CircuitBreaker
    fallback_fn: Callable[[dict], dict]

    def call(self, args: dict) -> tuple[str, dict]:
        if self.breaker.allow_request():
            try:
                result = call_with_retry(lambda: self.tool_fn(args))
                self.breaker.record_success()
                return "primary", result
            except ToolError:
                self.breaker.record_failure()
                log.info(json.dumps({"event": "tool_failed_falling_back", "tool": self.breaker.name}))
        else:
            log.info(json.dumps({"event": "tool_skipped_breaker_open", "tool": self.breaker.name}))

        return "degraded", self.fallback_fn(args)


# --------------------------------------------------------------------------
# 9. ReAct Loop Controller -- ties everything together (Sec 2.1, 2.6)
# --------------------------------------------------------------------------

@dataclass
class ReActLoopController:
    """Implements Thought -> Action -> Observation, repeated until the
    model emits a final answer OR any external, deterministic stop
    condition trips first. The model NEVER decides termination alone --
    every exit path is enforced in compiled code (Sec 2.6)."""

    think_fn: Callable[[list[dict]], dict]   # returns {"thought": str, "action": str|None, "args": dict, "final_answer": str|None}
    dispatcher: ToolDispatcher
    supervisor: TerminationSupervisor
    stagnation: StagnationDetector
    checkpointer: InMemoryCheckpointer
    estimated_cost_per_step_usd: float = 0.02

    def run(self, goal: str) -> dict:
        with run_scope() as run_id:
            log.info(json.dumps({"event": "run_start", "goal": goal}))
            transcript: list[dict] = [{"role": "user", "content": goal}]
            step_index = 0

            while True:
                try:
                    self.supervisor.check_before_step()
                except BudgetExceededError as exc:
                    log.info(json.dumps({"event": "run_terminated", "reason": "budget_exceeded",
                                          "detail": str(exc)}))
                    return {"status": "stopped_budget", "run_id": run_id, "detail": str(exc),
                            "transcript": transcript}

                decision = self.think_fn(transcript)
                transcript.append({"role": "assistant", "thought": decision.get("thought")})

                if decision.get("final_answer") is not None:
                    log.info(json.dumps({"event": "run_terminated", "reason": "final_answer"}))
                    self.checkpointer.save(run_id, step_index, {"transcript": transcript, "done": True})
                    return {"status": "completed", "run_id": run_id,
                            "answer": decision["final_answer"], "transcript": transcript}

                action = decision["action"]
                args = decision.get("args", {})
                source, observation = self.dispatcher.call(args)
                transcript.append({"role": "tool", "action": action, "source": source,
                                    "observation": observation})

                try:
                    self.stagnation.check(action, args, observation)
                except StagnationDetectedError as exc:
                    log.info(json.dumps({"event": "run_terminated", "reason": "stagnation",
                                          "detail": str(exc)}))
                    return {"status": "stopped_stagnation", "run_id": run_id, "detail": str(exc),
                            "transcript": transcript}

                self.supervisor.record_step(self.estimated_cost_per_step_usd)
                step_index += 1
                self.checkpointer.save(run_id, step_index, {"transcript": transcript, "done": False})


# --------------------------------------------------------------------------
# Example wiring
# --------------------------------------------------------------------------

if __name__ == "__main__":
    def flaky_search_tool(args: dict) -> dict:
        if random.random() < 0.3:
            raise ToolError("search backend 503", transient=True)
        return {"results": [f"doc about {args.get('query', '?')}"]}

    def degraded_search_fallback(args: dict) -> dict:
        return {"results": [], "note": "search unavailable, proceeding without fresh context"}

    dispatcher = ToolDispatcher(
        tool_fn=flaky_search_tool,
        breaker=CircuitBreaker(name="search", window_size=5, failure_threshold_ratio=0.6, cooldown_s=2),
        fallback_fn=degraded_search_fallback,
    )

    call_count = {"n": 0}

    def scripted_think(transcript: list[dict]) -> dict:
        # A deliberately-looping "buggy" agent for the first 4 steps to
        # demonstrate the stagnation detector, then it "succeeds".
        call_count["n"] += 1
        if call_count["n"] <= 4:
            return {"thought": "let me search again", "action": "search",
                    "args": {"query": "same query"}, "final_answer": None}
        return {"thought": "I have enough information", "action": None, "args": {},
                "final_answer": "Task complete."}

    controller = ReActLoopController(
        think_fn=scripted_think,
        dispatcher=dispatcher,
        supervisor=TerminationSupervisor(max_iterations=25, max_cost_usd=5.0, wall_clock_timeout_s=60),
        stagnation=StagnationDetector(window_size=6, repeat_threshold=3),
        checkpointer=InMemoryCheckpointer(),
    )

    result = controller.run("Research topic X and summarize findings.")
    log.info(json.dumps({"event": "final_result", "status": result["status"]}))
```

This demonstrates every required pattern in one coherent ReAct trajectory: a flaky search tool (30% failure rate) exercises retry-with-jitter and its dedicated circuit breaker independently of the loop's own control flow; the `TerminationSupervisor` enforces a hard per-run iteration/cost/wall-clock budget evaluated *before* every step, never after; the `StagnationDetector` catches the scripted agent's identical `search("same query")` repetition by its third occurrence — stopping the run at step 3 instead of running to the 25-iteration cap, which is precisely the class of control that would have converted the §5.1 incident from an 11-day, $47,000 loop into a same-day, sub-dollar one; and the `InMemoryCheckpointer` persists transcript state after every step so a crashed process resumes from the last completed step rather than re-running the whole trajectory (and, in a production side-effecting version, would prevent a completed tool call from being re-executed on restart).

---

## 6. Architectural System Design Scenarios

### Scenario A — Long-running research/analysis agent for a regulated enterprise, hardened against the $47K infinite-loop failure class

**Problem statement.** A financial-services firm wants an agent that performs multi-hour due-diligence research (cross-referencing filings, news, internal risk data) with a human analyst reviewing intermediate findings at checkpoints, per compliance policy. An initial ReAct-style prototype worked on short tasks but on longer research tasks occasionally entered unbounded back-and-forth between a "gather more evidence" step and a "verify sufficiency" step, with no hard external stop — exactly the failure shape documented in §5.1's canonical incident (a Verifier and an Analyzer agent looping for 11 days at $47,000, surfaced only by a billing-dashboard threshold breach). The firm needs a design where a runaway trajectory is caught in minutes, not weeks, and where every step is auditable for regulatory review.

**Proposed architecture.**

```
User request → Identity & RBAC Gateway (analyst-scoped identity)
                              │
                              ▼
              Termination Supervisor (Sec 2.6, 5): composable stop
              conditions -- max_iterations=40, max_cost_usd=25,
              wall_clock_timeout=2h, stagnation window=6/threshold=3
              -- evaluated PRE-CALL on every step, never post-hoc
                              │
                              ▼
              Durable Workflow (Temporal-style): the entire research
              session is one event-sourced workflow; every tool call,
              every re-plan, every human-approval wait is a durable
              Activity/signal -- crash-safe, replay-not-restart
                              │
              ┌───────────────┴────────────────┐
              ▼                                 ▼
     Plan-and-Execute loop (Sec 2.5):    Human-Approval Gate (durable
     Planner decomposes "due diligence   wait_condition, zero compute
     on Company X" into a bounded plan;  while pending): triggered at
     re-plans only on Verifier failure,  each major finding and before
     never on open-ended "gather more"   the final report is finalized
     -- eliminates the unbounded
     Analyzer<->Verifier ping-pong by
     construction (the plan is fixed
     up front, not renegotiated per
     observation)
              │                                 │
              └───────────────┬────────────────┘
                               ▼
              Postgres Checkpointer: full trajectory persisted after
              every step; OTel GenAI trace (invoke_agent → plan →
              execute_tool) gives regulators a replayable audit trail
              of exactly what evidence supported each finding
```

**Trade-off evaluation matrix.**

| Dimension | Unbounded ReAct (baseline that produced the incident shape) | Plan-and-Execute with hard budgets, no durability layer | Plan-and-Execute + durable workflow + composable termination (proposed) |
|---|---|---|---|
| Cost / 1k runs | Unbounded tail risk — a single stuck trajectory can cost more than 1,000 well-behaved runs combined (§5.1's $47K/11-day case) | Bounded per-run ($25 cap), but a mid-run crash restarts the whole trajectory, re-paying already-completed reasoning cost | Bounded per-run AND crash-safe — a mid-run failure resumes from the last checkpoint rather than re-paying sunk cost |
| Latency / completion time | Highly variable, no upper bound without an external kill switch | Bounded by wall-clock timeout, but a crash adds a full restart's worth of latency | Bounded by wall-clock timeout; a crash adds only replay-to-checkpoint latency (seconds), not a full restart |
| Reliability / auditability | Low — an unbounded loop that "looks fine" on every health check (§5.1: status reports remained "technically correct" throughout) provides no early warning | Medium — budgets bound the damage but a plain retry-from-scratch on crash makes the audit trail non-contiguous | High — durable Event History plus OTel GenAI tracing gives a single, contiguous, replayable trajectory regardless of crashes, satisfying regulatory review requirements directly |
| Ops complexity | Lowest to build, highest incident risk | Medium — budget logic only, no durable-execution infrastructure to operate | Highest to build (durable-workflow engine, checkpointer, composable termination config) but this is the necessary cost of converting a possible $47K incident into a bounded, auditable, resumable one |
| Security posture | Prompt-level-only safety (the agent "should" stop) — exactly the control gap named in §5.1's root-cause analysis | Deterministic budget enforcement closes the runaway-cost gap but human-approval gating is still a synchronous blocking call, awkward for multi-hour waits | Durable human-approval gate consumes zero compute while pending and cannot be silently skipped by the agent, since it is an infrastructure-level suspend, not a prompt instruction |

**Decision rationale.** The proposed design is chosen because the incident this scenario is explicitly designed against (§5.1) demonstrates that dashboards and logging are observational, not enforcing — "we have observability" is not "we can stop a runaway agent." Plan-and-Execute is preferred over open-ended ReAct specifically because its plan is fixed up front and only re-negotiated on an explicit, bounded re-planning trigger (Verifier failure with a capped retry count), which structurally eliminates the "Verifier keeps asking for more, Analyzer keeps complying, forever" ping-pong pattern rather than merely bounding it after the fact. Wrapping the whole session in a durable workflow converts the human-approval requirement from a fragile synchronous wait into a zero-cost durable suspend, and gives the compliance team a single contiguous audit trail across however many crashes or restarts occur — a requirement no in-memory-only implementation can satisfy. The composable termination stack (iteration + cost + wall-clock + stagnation, all pre-call and deterministic) is the layer that, applied to §5.1's actual incident, would have stopped it at approximately step 3 in the ping-pong pattern rather than 11 days in.

### Scenario B — Repo-scale autonomous coding agent (Devin-class) balancing long-horizon planning against runaway cost and state drift

**Problem statement.** An engineering platform team wants to offer an autonomous coding agent that can take a multi-file feature request, plan the implementation, write and test code, and self-correct on failures — without human intervention at every step, but with bounded cost and no risk of the agent silently corrupting its own understanding of repository state over a long session (§5's finding that 61.3% of agent failures are state-tracking errors, not planning errors, and that performance degrades measurably after ~35 minutes of continuous work, with failure rate quadrupling as task duration doubles).

**Proposed architecture.**

```
Feature request → Planner (frontier model): decomposes into a DAG of
                   dependent steps, not a flat list -- enables explicit
                   identification of parallelizable work (independent
                   files/modules), mirroring the DAG-based planning
                   architecture in Sec 2.5
                              │
              ┌───────────────┴────────────────┐
              ▼                                 ▼
     Hybrid execution split:              Agentic MapReduce for
     - "brain" (stateless reasoning        whole-repo-context steps:
       coordinator) plans and decides       Plan (author deterministic
     - "devbox" (sandboxed execution        relevance selectors) →
       environment) runs shell/file/        Shard (run selectors, no
       test commands -- the agent's         model, over the full repo)
       own loop never touches customer      → Map (parallel child
       infrastructure directly              sessions investigate
                              │              batches) → Reduce (dedupe
                              │              + synthesize)
              └───────────────┬────────────────┘
                               ▼
              Self-correction loop (Sec 2.4's "obtain ground truth"
              principle): on non-zero shell exit or failed test, read
              traceback → match to file → edit → re-run, bounded by
              the Termination Supervisor's max_iterations -- never an
              unbounded "keep trying different things" loop
                               │
                               ▼
              Independent re-verification before high-risk actions
              (Sec 4, binding-drift mitigation): before merging or
              force-pushing, a SEPARATE cheap model call re-reads the
              original request and re-derives the target file/branch,
              rather than trusting a possibly-drifted entity binding
              carried since step 1
                               │
                               ▼
              Postgres/Temporal checkpoint after every plan-DAG node
              completion + fresh 35-minute sub-session boundaries: long
              tasks are split into fresh-context child sessions at
              natural DAG boundaries rather than one continuously-
              degrading multi-hour session
```

**Trade-off evaluation matrix.**

| Dimension | Single long-lived ReAct session (naive baseline) | Plan-and-Execute with a flat step list, single long session | DAG-based planning + Agentic MapReduce + fresh sub-sessions per boundary (proposed) |
|---|---|---|---|
| Cost / 1k completed tasks | Highest — O(N²) context replay compounds over a multi-hour single session (§3.2), and state-tracking drift (§4) forces expensive re-work | Lower than ReAct (planner isolated from executor cost) but a flat list still forces full-session context accumulation | Lowest — MapReduce's deterministic Shard phase means token cost scales with relevant code found, not total repo size or total elapsed session length; fresh sub-sessions reset the O(N²) accumulation at each boundary |
| Latency / completion time | Degrades measurably after ~35 minutes; doubling task duration quadruples failure rate, and failed attempts must restart expensively | Better, since independent plan steps can be identified, but a flat list under-exploits parallelism vs. an explicit DAG | Fastest for parallelizable repo-scale tasks — the DAG's critical-path depth, not its total node count, bounds wall-clock time, and Map-phase children run concurrently |
| Reliability (state tracking) | Worst — the dominant failure mode (61.3% of failures in a 50K-trajectory study) is exactly what a single long session without fresh-context resets is most exposed to | Improved by planner/executor isolation but still accumulates drift within each executor's own context over a long flat list | Best — fresh sub-sessions at DAG boundaries bound how long any single context can drift before being reset; independent re-verification before high-risk actions (merges, force-pushes) catches binding drift specifically, reducing wrong high-risk actions by a reported 79% relative to no re-verification |
| Ops complexity | Lowest to build | Medium | Highest — requires a DAG-planning model, a deterministic selector/sharding pipeline, parallel child-session orchestration, and a durable checkpoint per DAG node — justified specifically because repo-scale tasks are exactly the long-horizon, high-state-tracking-risk class this complexity targets |
| Security / blast radius | An unbounded session accumulating drift is more likely to eventually take a wrong high-risk action (e.g., committing to the wrong branch) with no independent check | Same risk, mitigated only by whatever the flat plan happens to check | Independent re-verification before merge/push is a structural control against binding drift specifically, not a hope that the single-pass reasoning got the entity binding right the first time and kept it right for the whole session |

**Decision rationale.** The proposed design is chosen because the two dominant failure modes for repo-scale autonomous coding — cost blowup from context accumulation and state-tracking drift over long sessions — are both addressed structurally rather than through better prompting. DAG-based planning (rather than a flat plan or open-ended ReAct) is selected because it is the only representation that lets independent work execute in parallel, directly reducing wall-clock time on the critical path; Agentic MapReduce's deterministic Shard phase moves the expensive "find the relevant code" search out of repeated LLM-driven exploration into a one-time deterministic pass, so cost scales with relevant-code volume rather than total repo size or session length. Resetting to fresh sub-sessions at natural DAG boundaries is a direct, structural response to the documented ~35-minute degradation curve — rather than hoping a single session's summarization/compaction keeps up with drift indefinitely, the architecture never lets a single context run long enough for that degradation to dominate. Independent re-verification before irreversible actions (merge, force-push) is included specifically because research shows the intuitive alternative — locking an entity binding once and persisting it — can *amplify* error propagation up to 8.5x when the initial binding was wrong, so a second, independent verification pass is the only mitigation that improves matters in both the drift and the propagation direction simultaneously.
