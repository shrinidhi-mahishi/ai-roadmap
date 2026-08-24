# 05. Agent Frameworks

**Sub-areas covered**: LangGraph graph/state execution (Pregel supersteps, `Send`/`Command` APIs, checkpointers/stores) · OpenAI Agents SDK Agent/Handoff/Guardrail runtime loop · Google ADK Session/State/Runner event loop and ADK 2.0's graph-based Workflow Runtime · CrewAI Agent/Task/Crew/Process model and the Crews-vs-Flows production bifurcation · cross-framework token economics and latency benchmarks · durable execution (LangGraph checkpointers, Temporal integration, ADK SessionService, OpenAI SDK pluggable sessions, CrewAI unified Memory) · Zero-Trust MCP, RBAC/guardrails, PII redaction, and auditability per framework · production failure taxonomy (checkpoint bloat, handoff loops, migration breakage, role confusion) · two enterprise system-design scenarios with framework trade-off matrices

---

## 1. System Topology & Data Flow

All four frameworks solve the same generic deployment problem — route a goal through one or more LLM-driven decision points, dispatch tools/sub-agents, persist state across turns, and expose the trajectory for audit — but they place the control-flow decision (*what happens next?*) at different layers. LangGraph compiles that decision into code (conditional edges); the OpenAI Agents SDK and ADK's `AutoFlow` let the model itself emit the routing decision as a tool call; ADK's workflow agents and CrewAI's `Process` sit in between (fixed order, or a manager LLM choosing from a bounded set). The topology diagram below is deployment-shape-agnostic; each box is annotated with the abstraction each framework maps onto it.

```
                    ┌──────────────────────────────────────────────────────────────────────┐
                    │                            CONTROL PLANE                               │
                    │  LangGraph:  compiled StateGraph -- conditional edges / Command API     │
                    │  OpenAI SDK: Runner -- turn loop, handoff dispatch, guardrail eval       │
                    │  ADK:        Runner -- ask-yield event loop; AutoFlow delegation         │
                    │  CrewAI:     Crew -- Process (sequential | hierarchical); Flow (@router)  │
                    │                                                                          │
                    │  ┌────────────────┐   ┌─────────────────┐   ┌──────────────────────┐    │
                    │  │ Router /        │──▶│ Guardrail /      │──▶│ Termination / Budget  │    │
                    │  │ Orchestrator    │   │ Policy Gate      │   │ Supervisor             │    │
                    │  │ (edge fn /      │   │ (OpenAI SDK      │   │ (max_turns; ADK        │    │
                    │  │  transfer_to_X /│   │  Guardrail       │   │  invocation limits;    │    │
                    │  │  manager LLM /  │   │  tripwire; ADK   │   │  LangGraph recursion   │    │
                    │  │  AutoFlow)      │   │  before_model_   │   │  limit; cost/wall-     │    │
                    │  │                 │   │  callback)       │   │  clock caps)           │    │
                    │  └────────┬────────┘   └────────┬─────────┘   └───────────┬────────────┘    │
                    └───────────┼──────────────────────┼─────────────────────────┼─────────────────┘
                                │  allow-next-step?     │  tripwire clear?        │
                    ┌───────────▼──────────────────────▼─────────────────────────▼─────────────────┐
                    │                               DATA PLANE                                        │
                    │  LangGraph:  Nodes (State -> Partial<State>) run in Pregel supersteps;           │
                    │              per-key reducers merge concurrent writes (§2.1)                     │
                    │  OpenAI SDK: Agent (LLM + instructions + tools); one turn = call model ->         │
                    │              tool-call(s) or handoff -> re-loop (§2.2)                            │
                    │  ADK:        Agent / sub_agents tree, or a 2.0 Workflow graph node; state          │
                    │              mutated via CallbackContext/ToolContext, durable only once            │
                    │              yield-ed as an Event (§2.3)                                           │
                    │  CrewAI:     Task bound to an Agent (role/goal/backstory prepended every call)     │
                    │              executed per the Crew's Process (§2.4)                                │
                    │                                                                                    │
                    │  ┌────────────┐   ┌─────────────┐   ┌──────────────┐   ┌──────────────────────┐   │
                    │  │ Agent /     │──▶│ Sub-agent /  │──▶│ Tool Call     │──▶│ Aggregator / Reducer  │   │
                    │  │ Node        │   │ Handoff      │   │ Dispatch      │   │ (Send/map-reduce fan- │   │
                    │  │ (LLM call)  │   │ Target       │   │               │   │  in; manager synth)   │   │
                    │  └────────────┘   └─────────────┘   └──────┬───────┘   └──────────────────────┘   │
                    └──────────────────────────────────────────────┼──────────────────────────────────────┘
                                                                     │
                    ┌────────────────────────────────────────────────▼──────────────────────────────────┐
                    │                               TOOL PROXY LAYER                                       │
                    │  ┌───────────────────┐   ┌────────────────────┐   ┌────────────────────────────┐   │
                    │  │ MCP Server /       │   │ Function / Hosted /  │   │ Human-Approval / HITL Gate  │   │
                    │  │ External API       │   │ Computer / Agent-    │   │ (ADK before_tool_callback;   │   │
                    │  │ (Zero-Trust        │   │ as-Tool (OpenAI SDK  │   │  OpenAI SDK approvals;       │   │
                    │  │  gateway, deny-by- │   │ Tool taxonomy; ADK   │   │  LangGraph interrupt();      │   │
                    │  │  default, §4.3)    │   │ before/after_tool_   │   │  CrewAI human_input=True)    │   │
                    │  │                    │   │ callback)            │   │                              │   │
                    │  └───────────────────┘   └────────────────────┘   └────────────────────────────┘   │
                    └────────────────────────────────────────┬──────────────────────────────────────────────┘
                                                                │
                    ┌─────────────────────────────────────────▼────────────────────────────────────────────┐
                    │                               PERSISTENCE LAYER                                          │
                    │  LangGraph: Checkpointer (thread-scoped: Postgres/Redis/Memory) + Store (cross-thread,   │
                    │             long-term KV) -- durability modes exit/async/sync (§4.1)                     │
                    │  OpenAI SDK: pluggable Session backends -- SQLite/AsyncSQLite/Redis/SQLAlchemy/MongoDB/   │
                    │             Dapr/OpenAIConversations/AdvancedSQLite/Encrypted (§4.3)                      │
                    │  ADK:        SessionService (event log, sole durable-write path) + MemoryService          │
                    │             (cross-session recall); Firestore/Postgres/Vertex Agent Engine in prod (§4.2) │
                    │  CrewAI:     unified Memory (encode/consolidate/recall/extract/forget) on LanceDBStorage  │
                    │             (§4.4)                                                                        │
                    └─────────────────────────────────────────┬────────────────────────────────────────────────┘
                                                                 │
                    ┌──────────────────────────────────────────▼─────────────────────────────────────────────┐
                    │                          TELEMETRY / OBSERVABILITY SINKS                                   │
                    │  LangGraph: LangSmith traces + get_state_history() -- queryable, replayable audit ledger   │
                    │  OpenAI SDK: built-in OTel-compatible Tracing wrapping every Runner span                   │
                    │  ADK:       Vertex AI Agent Engine monitoring; before/after_tool_callback audit log        │
                    │             (sanitized params, allow/block decision, latency)                              │
                    │  CrewAI:    run/tool/duration monitoring feeding AMP/"Agent OS" ROI dashboards              │
                    └──────────────────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A caller submits a goal; every framework treats this as the seed of a session (LangGraph `thread_id`, OpenAI SDK `session_id`, ADK `Session`, CrewAI `Crew.kickoff()` inputs). (2) The **Router/Orchestrator** decides which agent/node executes first — in LangGraph this is a compiled entry edge; in the OpenAI SDK it's simply the `starting_agent` passed to `Runner.run()`; in ADK it's the root agent (or the `AutoFlow` coordinator resolving which `sub_agent` should own the turn); in CrewAI it's the first `Task` in a sequential list, or the `manager_agent` in hierarchical mode. (3) Before — or immediately after — the model call, a **Guardrail/Policy Gate** may intervene: OpenAI SDK input guardrails run in blocking or parallel mode and raise a typed tripwire exception; ADK's `before_model_callback` can short-circuit the call entirely by returning a synthetic `LlmResponse`; LangGraph and CrewAI have no native equivalent and rely on custom nodes/tool wrappers (§4.3). (4) A **Termination/Budget Supervisor** bounds how many more steps are allowed — LangGraph's recursion limit, the OpenAI SDK's `max_turns` (which counts LLM calls, not logical cycles — a documented gap, §4.2), ADK's invocation/quota ceilings, CrewAI's implicit reliance on the underlying LLM provider's own limits. (5) Control enters the **data plane**: a LangGraph node executes as part of a Pregel superstep and returns a partial state update merged via reducers; an OpenAI SDK `Agent` calls its model, and the model's own output — a final answer, a tool call, or a `transfer_to_<agent>` call — determines the next state transition; an ADK agent's execution logic runs inside an `InvocationContext` and *yields* `Event`s that the `Runner` forwards to `SessionService`; a CrewAI `Task` executes with its assigned `Agent`'s role/goal/backstory persona re-injected on every call. (6) Actions requiring external effects flow through the **Tool Proxy Layer** — MCP servers, function/hosted tools, or a durable human-approval gate; ADK's `after_tool_callback` can route large tool payloads to session state instead of back into the context window (the "store-and-slim" pattern), directly mitigating the context-bloat failure mode common to all four frameworks. (7) Every state transition is written to the **Persistence Layer** before the loop advances — this is the layer with the widest divergence across frameworks: LangGraph's checkpoint is a versioned graph-state snapshot; ADK's is an append-only event log where durability is structural, not a bolted-on checkpoint; the OpenAI SDK's session backend is an explicit pluggable interface; CrewAI's unified `Memory` is the only one of the four designed to compound *across* runs (via composite semantic/recency/importance scoring) rather than just within one. (8) The full trajectory streams to **Telemetry** — LangSmith, OTel Tracing, Vertex AI Agent Engine monitoring, or CrewAI's AMP dashboards — closing the loop that makes "why did it decide that" a queryable question rather than a guess.

---

## 2. Core Mechanics & Algorithms

### 2.1 LangGraph — Pregel-based graph execution

**Primitives.** `State` — a `TypedDict`/Pydantic schema shared by every node; each key can carry an independent **reducer** `(Value, Value) -> Value` controlling how concurrent updates merge (default: overwrite; e.g. `add_messages` appends). `Nodes` — functions `State -> Partial<State>`. `Edges` — normal (`A -> B`), conditional (a deterministic function returning the next node name), or the `Send` API (dynamic map-reduce fan-out: one node emits N `Send` objects, each spawning an independent parallel invocation of a target node with its own input). The `Command` API lets a node return both a state update *and* a routing decision in a single object, collapsing "update state" and "decide next node" into one atomic return value.

**Execution engine.** LangGraph borrows Google's **Pregel** message-passing model: execution proceeds in discrete, transactional **supersteps**. Within a superstep, every node scheduled to run executes (conceptually) in parallel; each emits messages (state updates) to its successors; the next superstep begins only once all of the current superstep's nodes complete and their writes are merged via reducers. This is what makes native fan-out/fan-in cheap (`Send`) and what makes cycles first-class — a conditional edge can route back to an earlier node, and each pass through the cycle is simply another superstep, not a special control construct. `StateGraph(Schema).add_node(...).add_edge(...)/.add_conditional_edges(...).compile()` produces a runnable graph supporting `.invoke()`, `.stream()`, `.ainvoke()`.

**State machine view.**

```
        ┌─────────────┐   superstep N: nodes scheduled by     ┌─────────────┐
        │  SUPERSTEP   │   superstep N-1's outgoing edges run  │  MERGE via   │
        │  DISPATCH    │───────────────────────────────────────▶│  REDUCERS    │
        │  (parallel   │                                        │  (per-key    │
        │  node set)   │                                        │  update fn)  │
        └─────────────┘                                        └──────┬──────┘
              ▲                                                        │
              │              route: conditional edge / Command         │
              └────────────────────────────────────────────────────────┘
                         (cycle) or -> END (no outgoing edges fire)
```

**Invariant.** A compiled graph terminates when a superstep produces no further outgoing messages (no node schedules a successor) — analogous to Pregel's "vote to halt." Unlike a ReAct `while(true)`, the *shape* of possible transitions is fixed at compile time even though the *number* of supersteps taken is not — this is the core "flexibility with a knowable ceiling" trade LangGraph makes relative to fully model-driven routing (§2.2, §2.3's `AutoFlow`).

**Complexity.** A graph with a critical-path depth of `d` (the longest chain of *sequentially dependent* nodes) takes `O(d)` supersteps regardless of total node count — independent branches collapse into the same superstep. Because most backends re-serialize and persist the *entire* state at each superstep, per-step I/O cost is `O(|State|)`, and if `State` accumulates monotonically (e.g., an ever-growing `messages` list with no pruning), cumulative token cost across `d` supersteps is `O(d²)` — the same quadratic-replay pathology every framework in this module shares, quantified concretely in §3.

### 2.2 OpenAI Agents SDK — Agent / Handoff / Guardrail runtime loop

**Primitives.** `Agent` (LLM + instructions + tools, provider-agnostic), `Handoff` (agent-to-agent delegation — implemented as a `transfer_to_<agent_name>` tool the model discovers and invokes itself), `Guardrail` (input/output/tool safety checks exposing a `tripwire_triggered` boolean), `Tool` (function/hosted/computer/MCP/agent-as-tool), `Tracing` (OTel-compatible spans). The `Runner` is the sole orchestrator.

**The turn loop (`Runner.run()`).**

```
        ┌───────────┐   agent + input     ┌─────────────┐
        │  IDLE /    │────────────────────▶│ MODEL_CALL   │
        │  compose   │                     └──────┬──────┘
        └───────────┘                             │
              ▲                    output classified as?
              │            ┌───────────────┼────────────────┬─────────────┐
              │            ▼               ▼                 ▼             ▼
              │      final_output     tool_call(s)       handoff       max_turns
              │      (no tool calls)      │             requested      exceeded
              │            │              ▼                 │             │
              │            │       ┌────────────┐           │             ▼
              │            │       │ EXECUTE +   │           │      ┌─────────────┐
              │            │       │ append      │           │      │ raise Max-   │
              │            │       │ tool_result │           │      │ TurnsExceeded│
              │            │       └─────┬──────┘           │      │ (or invoke   │
              │            │             │                   │      │ error_handler│
              │            │             │                   ▼      │ ["max_turns"])│
              │            │             │            ┌─────────────┐└─────────────┘
              │            │             │            │ swap current │
              │            │             │            │ agent; inherit│
              │            │             │            │ full history  │
              │            │             │            │ (or filtered  │
              │            │             │            │ via input_    │
              │            │             │            │ filter)       │
              │            │             │            └──────┬───────┘
              │            ▼             ▼                    │
              │      ┌───────────┐  └────────────────────────┘
              └──────│ TERMINATE  │
                     │ (return    │
                     │  final     │
                     │  output)   │
                     └───────────┘
```

**Guardrail placement is structural, not per-agent.** Input guardrails run only on the *first* agent in a chain; output guardrails only on the agent producing the *final* output; a handed-off agent mid-chain is not re-validated by the original input guardrail, and guardrails never wrap the handoff tool-call itself. This is documented behavior, not a bug — but it is the single most common source of production surprise for teams assuming guardrails apply uniformly (§4.3, §4.2's failure taxonomy). Guardrails can run `run_in_parallel=True` (default — lowest latency, but the agent may burn tokens/side effects before the tripwire fires) or `run_in_parallel=False` (blocking — completes before the agent starts, at added latency, preventing wasted spend).

**Invariant.** By default the next agent in a handoff inherits the *entire* conversation history — context is monotonically non-decreasing across handoffs unless an `input_filter` prunes it, which is the structural cause of the cost-amplification failure mode in §3 and §4.2 (a two-agent A→B→A cycle re-copies both agents' accumulated context at every hop).

**Complexity.** A linear `k`-turn run costs `O(k)` sequential model calls. A handoff cycle of length `c` (A→B→A→B...) costs `O(c²)` in cumulative context tokens under the default full-history-inheritance policy, since cycle `i` re-sends the context accumulated by cycles `1..i-1` — this is why `max_turns` (which counts calls, not cycles) systematically under-bounds cost for cyclic handoff topologies.

### 2.3 Google ADK — Session/State/Runner event loop

**Primitives.** `Session` (a single conversation's chronological `Events` + `State` scratchpad), `State` (`session.state`, current-conversation-only), `SessionService` (create/retrieve/update/delete; the **sole sanctioned path** for durable persistence, via `append_event()`), `Runner` (binds an agent to a session service, orchestrates the event loop).

**Event loop mechanics — the "ask-yield" invariant.** The Runner and the agent's execution logic communicate via a bidirectional channel: the agent code does its work and *yields* an `Event` back to the Runner; only once yielded does the Runner apply the event's `state_delta` through the `SessionService` and make it durable. State mutations made via `context.state[...]` inside a callback or tool are recorded locally in the `InvocationContext` but are **not durable until yielded** — direct writes to `session.state` outside `CallbackContext`/`ToolContext`-mediated flows (or `output_key`, or explicit `EventActions.state_delta`) are explicitly discouraged, because they bypass the only mechanism that makes state changes crash-safe and replayable.

```
   ┌─────────────┐  invoke   ┌──────────────────┐   yield Event    ┌───────────────┐
   │   Runner     │──────────▶│ Execution Logic   │─────────────────▶│ Runner applies  │
   │ (ask)        │           │ (Agent/LLM/       │   (state_delta,  │ state_delta via │
   │              │◀──────────│  Callback/Tool)   │◀─────────────────│ SessionService  │
   └─────────────┘  resume   └──────────────────┘   (yield)          │ (durable write) │
        ▲                                                             └───────┬────────┘
        │                                                                     │
        └─────────────────────────── next turn / next Event ─────────────────┘
```

**Multi-agent hierarchy.** A parent's `sub_agents` list forms a tree (ADK auto-sets `parent_agent`). Two orchestration layers compose on top: (1) **Workflow agents** — `SequentialAgent` (one shared `InvocationContext` across children, enabling state-passing), `ParallelAgent` (distinct `InvocationContext.branch` per child but a *shared* `session.state`, requiring distinct keys to avoid races), `LoopAgent`; (2) **LLM-driven delegation** — a `CoordinatorAgent`'s `AutoFlow` transfers execution to sub-agents based on their natural-language descriptions (the ADK analogue of OpenAI SDK handoffs), or a parent wraps a sub-agent as an `AgentTool` for explicit, synchronous invocation (the ADK analogue of a deterministic function call).

**ADK 2.0 — convergence toward the graph model.** ADK 2.0 (stable May 19, 2026) replaced the 1.x hierarchical executor with a graph-based Workflow Runtime where Agents, Tools, and Functions are individually evaluated **nodes** — explicitly framed as a control-plane hardening measure: "even if an LLM node is manipulated, the workflow runtime lacks the pathways (edges or nodes) to execute unauthorized actions." This is architecturally the same shift LangGraph made from the start (§2.1), motivated by the same failure class: pre-2.0 ADK agents could "get stuck in infinite loops, bypass key business logic due to hallucinations, or fail without raising clean exceptions."

**Cross-service orchestration (A2A).** An ADK agent can run as an independent `A2AServer` and consume remote agents via `RemoteA2aAgent`, handling Agent Card discovery, authentication, and JSON-RPC transparently — the closest of the four frameworks to a native microservices-for-agents pattern.

**Complexity.** A `SequentialAgent` of depth `n` costs `O(n)` sequential calls; a `ParallelAgent` of fan-out `n` costs `O(1)` wall-clock rounds but `O(n)` total calls, with the shared-`session.state` race window bounded by however the caller partitions keys. `AutoFlow` delegation adds one LLM-driven routing call per hop, structurally identical in cost profile to an OpenAI SDK handoff.

### 2.4 CrewAI — Crew/Process/Flow model

**Primitives.** `Agent` (role, goal, backstory — prepended to every LLM call for that agent), `Task` (description, expected output, assigned agent, optional tools/context), `Crew` (binds agents + tasks under a `Process`), `Process` (`sequential | hierarchical`, a type-safe enum).

**Sequential process.** Tasks execute in list order; each task's output becomes context for the next — a fixed-shape DAG with no dynamic branching unless the caller drops to a custom process or wraps the crew in a `Flow`.

**Hierarchical process.** Requires a `manager_agent` or `manager_llm`. The manager dynamically creates and delegates tasks to agents based on their declared capabilities and validates outcomes — an emergent, conversational orchestration model closer to a running negotiation than a compiled routing table. This is CrewAI's most expressive mode and, per §4.2, its most cited failure surface: the manager LLM can invent or mis-reference a coworker name that matches no agent in the crew.

**Crews vs. Flows — the production bifurcation.** CrewAI explicitly separates two orchestration philosophies: **Crews** optimize for autonomy and emergent collaboration (flexible, natural delegation, best for prototyping and open-ended problem-solving); **Flows** are event-driven, deterministic-state-management workflows with fine-grained control and conditional branching, and can embed one or more Crews as steps. The documented production pattern is explicit: *"prototype with Crews, harden with Flows around them."* This is CrewAI's analogue of LangGraph's node-vs-edge separation and ADK's AutoFlow-vs-Workflow-agent split — every framework in this module converges on the same underlying lesson: unconstrained LLM-driven routing is good for exploring the solution space, and a compiled/deterministic layer is what makes the result operable in production.

**State-machine view (hierarchical process).**

```
        ┌───────────────┐   task pool + agent capability descriptions   ┌───────────────┐
        │  Manager LLM   │───────────────────────────────────────────────▶│  Delegate to   │
        │  (creates/     │                                                │  Agent by name │
        │  assigns tasks)│◀───────────────────────────────────────────────│  (LLM-inferred, │
        └───────┬───────┘        validate outcome / re-delegate            │  not statically │
                │                                                          │  verified)      │
                │  all tasks validated                                    └───────────────┘
                ▼
        ┌───────────────┐
        │   Crew output   │
        └───────────────┘
```

**Invariant.** Sequential mode guarantees `O(tasks)` LLM calls with no manager overhead; hierarchical mode adds exactly one manager LLM call per delegation decision, and because the manager's coworker-name resolution is LLM-inferred rather than statically type-checked against the crew's registered agents, hierarchical mode carries a **non-zero, model-capability-dependent failure probability per delegation** that sequential mode structurally cannot exhibit (§4.2). CrewAI's documentation and maintainers explicitly recommend stronger frontier models specifically for hierarchical delegation as a mitigation.

**Complexity.** Sequential: `O(n)` calls for `n` tasks. Hierarchical: `O(n)` delegation calls + `O(n)` execution calls = `O(2n)`, plus retry cost proportional to the coworker-name mis-resolution rate — for `n` parallel tasks, this is `n` extra manager calls *before any work begins*, matching the ~30% cost premium over sequential mode measured in §3.

### 2.5 Cross-Framework Mechanics Summary

| Framework | Execution model | Termination mechanism | Routing decision cost |
|---|---|---|---|
| LangGraph | Pregel supersteps over a compiled graph | No outgoing messages ("vote to halt"); recursion limit as backstop | `O(1)` — a Python function, no LLM call |
| OpenAI Agents SDK | Turn loop: call → tool/handoff → re-loop | `final_output` with no tool calls, or `max_turns` exceeded | `O(1)` LLM call per handoff (model-emitted tool call) |
| Google ADK | Ask-yield event loop over an agent tree / 2.0 graph | Terminal event with no further yields; workflow-agent structural bound | `O(1)` LLM call per `AutoFlow` delegation; `O(1)` code path for Workflow agents |
| CrewAI | Sequential task list, or manager-directed hierarchical delegation | Last task in list completes, or manager marks all tasks validated | `O(1)` for sequential (fixed order); 1 manager LLM call per hierarchical delegation |

---

## 3. Token Economics & NFR Analysis

### 3.1 Cross-framework cost benchmarks

Two independently reported 2026 cross-framework benchmarks (six frameworks/2,000 runs, and a separate four-framework/2,000-run comparison, both pinning GPT-4o to isolate framework overhead from model variance) converge on the same ordering: **CrewAI consumes roughly 2–3× the tokens of the pack on simple, one-tool-call flows** and runs several times slower; LangChain (not LangGraph) was most token-efficient overall; **LangGraph was fastest on latency across all tested tasks**. LangGraph's overhead instead shows up on *deeply branching* tasks, where its explicit state machine folds growing tool-call history back into context at every node — one measurement spiked to **~15,010 prompt tokens in a single call** on the heaviest task.

**Root cause of CrewAI's overhead.** Every agent in a crew carries a `role`/`goal`/`backstory` scaffold prepended to *every* LLM turn — measured at ~150 tokens of overhead per agent per call, a **56% per-request overhead** baked into the architecture itself, not tunable away without abandoning the persona model. On a 3-agent pipeline at 10,000 requests/day (GPT-4o pricing): **LangGraph ≈ $32/day** (~800 tokens/request, developer-controlled prompts) vs. **CrewAI ≈ $50/day** (~1,250 tokens/request) — an $18/day gap compounding to **~$6,570/year** for one moderate-traffic pipeline.

**Hierarchical process tax.** CrewAI's `hierarchical` mode adds one manager LLM call per delegation decision — for 3 parallel tasks, 3 extra manager calls *before any work starts*, adding roughly **30% cost** vs. sequential mode. Measured latency on a 3-parallel-agent task (GPT-4o, ~500-token outputs): **LangGraph ~4.2s**, **CrewAI hierarchical ~7.8s**, **CrewAI sequential ~13.1s** (sequential is slower here specifically because it has no fan-out — three tasks execute one after another).

**Orchestration-only overhead.** On a standard 100-iteration research-and-summarize loop, LangGraph spent "near zero" tokens on routing (code-based `if`/`match`, no LLM call) using under 2,000 tokens/run total, while CrewAI spent **$4.10 in prompt tokens purely on orchestration** across the same 100 iterations, using ~4,500 tokens/run.

> ⚠️ These benchmark figures vary by source and task type, and none disclose full methodology/prompt templates — treat the *relative ordering* (CrewAI > LangGraph on simple-task token cost) as more reliable than the absolute percentages or dollar figures.

### 3.2 Framework-specific overhead deep dives

**LangGraph checkpoint serialization.** An independently reproducible measurement (16-turn ReAct agent, 65 messages) found default `MemorySaver` full-Pydantic serialization produces a **21,850-byte checkpoint** vs. **3,217 bytes** with a binary pooling serializer — an **85.3% storage overhead (6.79×)**. The same report claims a **37.8% token overhead paid every LLM call** (5,764 vs. 3,587 semantic-content tokens) from re-serializing accumulated state into the context path; a maintainer rebuttal in the same thread disputes this specific framing, noting checkpoint bytes never enter the LLM context directly since messages are rehydrated to provider wire format on resume — **treat the token-overhead claim as contested**, but the storage-overhead figure as independently reproducible. As of the report, LangGraph has **no pluggable serializer** at the checkpointer/store/stream/subgraph boundary.

**OpenAI Agents SDK handoff cycle amplification.** Handoff cycles compound cost **non-linearly**: if Agent A hands to B and B hands back to A, each cycle appends the accumulated context of every prior cycle, so cycle 3 costs **~3× cycle 1**. A two-agent cycle with 5 handoffs each uses 10 turns — comfortably under a `max_turns=20` cap that *looks* safe — while costing **3–6× more** than the equivalent linear path, precisely because `max_turns` counts LLM calls, not logical A→B→A cycles.

**Transport-layer latency (OpenAI SDK).** Default HTTP transport incurs **~200ms overhead per tool-call round-trip** (TCP/TLS handshake, header parsing) in multi-hop workflows; switching to a persistent WebSocket connection cuts per-message overhead to **~20ms**, yielding **30–50% total latency reduction** for agents with 3+ tool calls per run, and **40–60%** for tool-heavy workflows specifically.

**LangGraph persistence backend overhead.** `SqliteSaver` adds **~2–5ms I/O latency per checkpoint write**, compounding across 10+ tool calls per workflow. Checkpoint storage grows **~2–4MB per workflow instance** in tool-heavy runs; at 50 concurrent workflows this reaches **100–200MB in-memory** with `MemorySaver`/`SimpleCheckpoint`, which **will OOM under sustained load** — never ship `MemorySaver` to production.

### 3.3 Prompt caching support per framework

| Framework | Caching mechanism | Discount / overhead |
|---|---|---|
| LangGraph | Node-level `Caching` policies + provider prompt caching pass-through | Provider-dependent (see below) |
| OpenAI Agents SDK | Provider automatic prompt caching on any prefix ≥1,024 tokens; `AdvancedSQLiteSession` adds per-turn usage analytics | Cached input: **0.1× uncached rate**; cache write: **1.25× surcharge** (not stacked on full price); TTL: 30-min exact window (GPT-5.6+) or model-dependent best-effort retention (earlier models) |
| Google ADK | Provider prompt caching only; no framework-native semantic cache documented | Provider-dependent `[inferred]` |
| CrewAI | Tool-result caching (`cache=True` on `Crew`) | Avoids re-invoking identical tool calls; does not address the role/goal/backstory per-turn scaffold cost that dominates CrewAI's overhead |

Because agent instructions/system prompts are typically stable turn-over-turn, they form a natural cache-eligible prefix in every framework built on a provider API — the framework layer mostly determines *whether the stable prefix stays byte-identical across turns* (a framework that reorders or re-templates the system prompt on every call defeats caching regardless of provider support).

### 3.4 Latency SLA targets

No framework publishes an official p50/p95/p99 wall-clock SLA; the table below combines the two measured benchmark data points (§3.1) with a **1.5–2× multiplier over p50 for p95, and a further 1.5–1.8× over p95 for p99** — the same architect-constructed extrapolation convention used elsewhere in this roadmap for agent-loop tail latency, justified because by p95 most per-call decode variance is already absorbed and the remaining p95→p99 gap is dominated by rare whole-trajectory events (a retry storm, a stuck handoff cycle, a provider-side queueing spike) rather than routine jitter.

| Framework / scenario | p50 (measured) | p95 `[inferred]` | p99 `[inferred]` | Mitigation |
|---|---|---|---|---|
| LangGraph, 3-agent parallel task | ~4.2s | ~7–8s | ~11–14s | Native `Send`-API fan-out already used; p99 mitigation: cap recursion limit + `durability="sync"` to bound crash-replay tail |
| OpenAI Agents SDK, HTTP transport, 3+ tool calls | baseline + ~200ms/hop | baseline × 1.3–1.5 (network queueing) | baseline × 1.8–2.2 | WebSocket transport (30–60% reduction); blocking guardrails to fail fast instead of wasting a full turn |
| ADK, `SequentialAgent`/`ParallelAgent` | Comparable to LangGraph `[inferred, single source]` | Comparable `[inferred]` | Quota-gated: `429 RESOURCE_EXHAUSTED` spikes tail sharply past QPM ceiling (§3.5) | Capacity-plan to Google's 750-QPM-with-buffer formula *before* p99 becomes quota-bound rather than compute-bound |
| CrewAI, hierarchical, 3-agent parallel task | ~7.8s | ~13–16s | ~20–25s | Use frontier models for the manager to reduce coworker-mis-resolution retries; consider Flow-based deterministic routing for latency-sensitive paths |
| CrewAI, sequential, 3-agent | ~13.1s | ~20–26s | ~28–36s | Fan out independent tasks via a `Flow` instead of a flat sequential list |

> ⚠️ Cross-framework orchestration-latency-in-milliseconds figures (LangGraph ~120ms, CrewAI ~450ms per routing decision, cited in some secondary sources) come from a single vendor comparison without published methodology — treat as directional only.

### 3.5 Throughput / concurrency capacity planning

**Google ADK / Vertex AI Agent Engine is the only framework with a publicly documented, formula-driven capacity-planning methodology.** Per-project quotas gate `reasoning_engine_service_query_requests` (QPM) and hard-cap **10 concurrent `BidiStreamQuery` connections per minute**. Google's own sizing formula for a 250-peak-concurrent-user deployment at 2 requests/user/min:

```
Query QPM               = peak_users × avg_requests_per_user_per_min
                         = 250 × 2 = 500  →  recommended (+50% buffer): 750 QPM

Session-event-append/min = Query QPM × events_per_query   (≈12: LLM call → tool-use
                            decision → tool execution → tool-result LLM call →
                            final response)
                         = 500 × 12 = 6,000/min  →  buffered: 9,000/min

Session writes/min      ≈ Query QPM (typically ≤ query rate)  →  buffered: 750/min

Concurrent BidiStreamQuery connections: hard-capped at 10/min regardless of buffer
  -- architect around via connection pooling/multiplexing, not a quota request
```

Undersized quotas surface as `429 RESOURCE_EXHAUSTED`, sometimes mis-reported as a generic `500` during streaming — a documented operator-visibility gap.

**Other frameworks rely on generic LLM-provider rate limits plus self-managed infrastructure sizing**, with no equivalent published worksheet: LangGraph scales horizontally via LangGraph Platform or self-hosted workers (proven at 85M-user scale, §6); the OpenAI Agents SDK is bound by the chosen session backend (Redis scales horizontally; `SQLiteSession`'s file-locking becomes the bottleneck under concurrent writers); CrewAI has no native distributed executor — its throughput ceiling is purely a function of the underlying LLM provider's rate limits.

**Back-pressure design (cross-framework, none ship this natively).** Production guidance converges on an external **Rate Governor** pattern: a Redis-backed (or file-locked, single-node) global token bucket per identity, with **reservation semantics** — agents request quota *before* starting a task and reclaim unused reservations — and bucket keys scoped by `(tenant, workload, model)` so one tenant's runaway agent throttles only that tenant, with a platform-wide breaker as backstop. A "Predictive Circuit Breaker" pattern monitors cost/error *velocity* (e.g., request rate climbing from 1 req/s to 110 req/s in two minutes) and **gracefully downgrades** (switch to a cheaper model) rather than hard-failing after the fact.

### 3.6 NFR summary and explicit trade-offs

| NFR dimension | LangGraph | OpenAI Agents SDK | Google ADK | CrewAI |
|---|---|---|---|---|
| Token cost (simple task) | Baseline / lowest — code-routed, no LLM spent on routing | Low for linear flows; **3–6×** on handoff cycles | Comparable to LangGraph `[inferred]`; quota-gated at scale | Highest — **2–3×** pack median; +30% in hierarchical mode |
| Latency | Fastest measured (~4.2s/3-agent parallel) | Network-bound; WebSocket cuts 30–60% | Comparable to LangGraph `[inferred, single source]` | Slowest measured (7.8–13.1s on equivalent task) |
| Ops complexity | High — steep learning curve (1–2 weeks), richest tooling (LangSmith, time-travel, Temporal) | Low–medium — minimal primitives; session backend + cycle-detection are DIY | Medium–high — event-loop mental model, quota management, version-migration risk | Lowest — 20 lines to a working prototype, 2–4 hour ramp |
| Flexibility vs. control | Maximal flexibility (arbitrary cyclic graph) *traded against* the steepest ramp-up and heaviest per-run state footprint | Emergent, model-driven routing *traded against* no structural cycle limit (§4.2) | Hybrid — tree/graph gives more structure than pure LLM routing, *traded against* migration fragility across major versions | Maximal ease-of-prototyping *traded against* the least verifiable production behavior (manager-LLM-inferred delegation) |
| Best-fit NFR profile | Complex, stateful, auditable, long-running, or regulated workflows where the ops investment pays for itself | OpenAI-native stacks needing fast time-to-production with strong native safety primitives | Google Cloud/Gemini-native enterprises wanting unified search + agent orchestration (Agentspace) | Rapid multi-agent prototyping; role-clear business processes with a human review gate before anything expensive happens |

> Availability targets, RPO/RTO figures tied to each framework's checkpoint granularity, and the compliance-vs-cost/latency trade-off summarized only qualitatively above are made explicit with concrete numbers in §3.7.

### 3.7 Availability, RPO/RTO & Compliance Trade-offs

No framework or managed-service vendor publishes an availability SLA scoped specifically to "agent framework deployment" — every percentage below is an `[inferred/recommended]` design target, derived from the durability modes, checkpoint granularity, and persistence-backend behavior already documented in §4.1, §3.2, and §3.5, not a published agent-framework number.

**Availability targets per framework/deployment pattern `[inferred/recommended]`.**

| Deployment pattern | Availability target | Basis |
|---|---|---|
| LangGraph, single-region, managed Postgres checkpointer, `durability="sync"` | **99.9%** (~8.7h/year) | Bounded by the managed Postgres instance's own single-AZ SLA (typically 99.9–99.95%) plus the app tier; §4.1 |
| LangGraph + Temporal integration, multi-region, nodes as Activities | **99.95%+** (~4.4h/year) | Temporal replicates workflow history across zones/regions; `interrupt()` becomes a durable wait rather than a single-node blocking call at zero holding cost (§4.1) |
| OpenAI Agents SDK, `RedisSession` (Sentinel/Cluster HA) | **99.9–99.95%** | Multi-AZ Redis HA vs. `SQLiteSession`'s file-locking single point of failure (**<99%**, dev-only per §4.1) |
| Google ADK, Vertex AI Agent Engine (managed Sessions + Memory Bank) | **99.9%**, effectively lower under burst | Google Cloud managed-service tier, but capped by the hard 10-connection/min `BidiStreamQuery` ceiling (§3.5) which manifests as request failures even while the underlying service is technically up |
| CrewAI, self-hosted `LanceDBStorage`, no native distributed executor | **99–99.5%** `[inferred]` | No HA guidance shipped for the memory layer (§3.5); availability is purely a function of what the operator builds around it |

**RPO/RTO tied to checkpointing granularity (extends §4.1's durability-mode discussion into explicit numbers).**

| Framework | Checkpoint/persistence unit | RPO (data loss window) | RTO (recovery time) |
|---|---|---|---|
| LangGraph, `durability="sync"` | Per-superstep (§2.1, §4.1) | **~1 superstep** — a few to tens of seconds, using §3.1's ~4.2s/3-agent-parallel figure as a proxy for superstep duration; worst case is only the in-flight superstep | **Seconds to low minutes** — `PostgresSaver`'s `get_state_history()` resumes at the last committed row rather than replaying an external log |
| LangGraph, `durability="async"` (default) | Per-superstep + async flush lag | **~1 superstep plus a few hundred ms–low seconds of flush lag** (widened vs. sync mode) | Comparable to sync mode once the pending write lands; a crash before flush loses that increment |
| Google ADK, `SessionService.append_event()` | Per-event (one yielded `Event` — LLM call, tool-use decision, or tool-result step; §3.5 estimates ~12 events/query) | **~1 event** — finer-grained than a LangGraph superstep in principle, but any `context.state[...]` write made without a yield is unrecoverable, so effective RPO depends on callback discipline, not just the framework (§2.3) | **Seconds to low minutes**, contingent on the Firestore/Postgres/Agent Engine backend's own recovery time |
| OpenAI Agents SDK, session backends (Redis/SQLAlchemy/Mongo) | Per-turn (§4.1) | **~1 turn** — coarser than LangGraph's per-superstep granularity whenever a turn spans multiple tool calls | **Seconds** for `RedisSession`/`SQLAlchemySession`; materially worse for `SQLiteSession` under concurrent-writer file-locking failure |
| CrewAI, unified `Memory` | Per-task (§2.4, §4.1) | **~1 task** — the coarsest of the four; a task can span many internal LLM calls before its output is captured, so a mid-task crash can lose the entire task's partial work | **No first-class checkpoint-resume path documented** — closest analogue is re-running the Crew/Flow from its last completed task, plus manual-detection lag; the documented memory-degradation failure mode (§4.1) means recovery isn't always a clean rewind either |

**Trade-off 1 — tighter RPO/RTO costs storage and per-step latency (quantified directionally from §3.2/§4.2 figures).** Moving LangGraph from `durability="async"` to `"sync"` narrows RPO from "~1 superstep + flush lag" to "~1 superstep, no lag" for **~3–5ms round-trip cost per step** (§4.2) — negligible against multi-second LLM turns (§3.1), so tightening RPO here is nearly free in relative latency terms. Storage is the sharper trade: §3.2's measured **21,850-byte default checkpoint** (vs. 3,217 bytes with a binary pooling serializer) multiplied across every superstep, at 50 concurrent workflows in tool-heavy runs, already approaches the **100–200MB in-memory** range §3.2 flags as an OOM risk with `MemorySaver`. Checkpointing more frequently (e.g., per-node instead of per-superstep, or skipping the `_keep_last_n` trimming pattern from §5) directly multiplies that storage/write cost; checkpointing coarser (CrewAI's per-task model) cuts write overhead roughly in proportion to how many steps a task bundles, at the cost of the much wider RPO/RTO exposure quantified above. Net: LangGraph's per-superstep sync checkpointing buys ~1-superstep RPO for ~3–5ms/step and multi-MB/workflow storage; CrewAI's per-task persistence buys a fraction of that write cost but a proportionally wider, and less recoverable, RPO/RTO window.

**Trade-off 2 — compliance rigor vs. cost and latency.** §4.3's compliance drivers (GDPR Art. 30, EU AI Act Art. 12 record-keeping for high-risk systems, SOC 2 Type II, SEC disclosure rules) are cited there only as drivers, but the Agent Activity Log they motivate carries the same cost/latency shape as checkpointing. Writing that hash-chained log **synchronously before each tool call proceeds** gives the strongest compliance posture (no gap between "action taken" and "action logged," which is what Art. 12's record-keeping intent and a SOC 2 audit require) but adds a write round-trip to the critical path of every tool call — directionally the same **~2–5ms-per-write order of magnitude as LangGraph's `SqliteSaver`** figure (§3.2) if the audit log shares that storage tier, or more if it rides a separate compliance-grade store with its own durability guarantee. Writing the log **asynchronously** (fire-and-forget after the action proceeds) removes that latency tax but reopens the exact gap regulators target: an action can complete and only later fail to record — or be delayed during precisely the incident a regulator would want to review — which is a materially weaker audit-trail-completeness posture than synchronous writes. Storage compounds the same way as checkpoint bloat: every additional field in §4.3's minimum audit bar (sanitized args, allow/block decision, latency, downstream resource URI) is bytes written per tool call, per agent, per step, so at Scenario A's fintech scale (§6, tens of millions of conversations/year) this becomes a non-trivial storage/write-throughput line item rather than a single-run rounding error — the same volume-times-per-step-cost dynamic that makes CrewAI's persona-scaffold overhead (§3.1) or LangGraph's checkpoint-serialization overhead (§3.2) material in aggregate even though each individual write looks cheap in isolation.

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution per framework

**LangGraph.** Two-tier persistence: **Checkpointers** persist a thread's graph state as versioned checkpoints (short-term, thread-scoped — conversation continuity, HITL, time-travel debugging); **Stores** persist application-defined KV data outside graph state (long-term, cross-thread — user preferences, shared facts). Three durability modes trade latency for safety: `"exit"` (checkpoint only at graph completion — fastest, zero mid-run crash recovery), `"async"` (persisted asynchronously during the next step — **default**, small loss window on crash), `"sync"` (persisted before advancing — highest durability, the correct production default for any graph touching side-effecting tools). `PostgresSaver` is the first-party production backend (permanent rows, queryable via `get_state_history()`, enabling fork/replay "time travel"); `RedisSaver` trades permanence for speed and TTL-based auto-expiry, ideal for high-churn conversational threads with no audit requirement. **Critical distinction: checkpoints preserve data between node boundaries, not a journal of what happened inside a node** — LangGraph's own docs state plainly that nodes re-execute on resume; the `@task` primitive mitigates this for non-idempotent side effects by caching the task's result so a replay returns the cached value instead of re-invoking. The official **Temporal LangGraph plugin** runs the compiled graph as a Temporal Workflow, with nodes/tasks optionally executing as Temporal Activities (independently retried, timed-out, and durable across machine failure); `interrupt()` becomes a **durable wait** at zero holding cost. Caveat: the in-memory `Store` is unavailable inside Activity-wrapped nodes — memory needing survival across the Activity boundary must be modeled as durable state, not node-local storage.

**Google ADK.** `SessionService` (lifecycle: create/retrieve/append/delete — the only sanctioned durable-write path) and `MemoryService` (cross-session recall) are cleanly separated; in-memory implementations lose all data on process restart, so production deployments use Cloud Firestore, PostgreSQL, or the managed Vertex AI Agent Engine Sessions + Memory Bank. **Migration friction is severe**: ADK 1.x and 2.0 session schemas are structurally incompatible (2.0 reads legacy pickle-based v0 with deprecation warnings but requires migration to JSON-based v1); Google's explicit guidance is "back up production session data, run 1.x and 2.0 in completely separate storage environments, plan a migration window, not an in-place upgrade." An open, long-unaddressed RFC documents no automated migration path, no Kubernetes-native migration hooks (teams must SSH into pods), race conditions when multiple pods migrate concurrently, and no rollback capability across ADK 1.14.0/1.17.0/1.19.0 schema changes.

**OpenAI Agents SDK.** Sessions automatically persist and prepend conversation history keyed by `session_id`, eliminating manual `.to_input_list()` bookkeeping. Backend spectrum: `SQLiteSession`/`AsyncSQLiteSession` (local/simple, file-locking for concurrent access), `RedisSession` (shared across distributed workers, TTL support), `SQLAlchemySession` (any supported DB), `MongoDBSession` (atomic sequence counters for cross-process ordering), `DaprSession` (cloud-native sidecar), `OpenAIConversationsSession` (server-managed), `OpenAIResponsesCompactionSession` (auto-compaction for long conversations), `AdvancedSQLiteSession` (branching + per-turn usage analytics), `EncryptedSession` (encryption + TTL wrapper composable over any backend).

**CrewAI.** A single unified `Memory` class replaced four legacy types (`ShortTermMemory`, `LongTermMemory`, `EntityMemory`, `ExternalMemory`), built around five "cognitive operations": encode, consolidate, recall, extract, forget. On save, an LLM infers scope/categories/importance; on recall, a `RecallFlow` deepens search (`depth="deep"`, LLM-assessed) or falls back to fast vector-only search (`depth="shallow"`, ~200ms, no LLM call). Retrieval uses composite scoring: `semantic_weight × similarity + recency_weight × decay + importance_weight × importance`. Storage backend is `LanceDBStorage`; memory attaches to an `Agent`, a `Crew` (shared unless overridden), or a `Flow` (as `self.remember()`/`self.recall()`, explicitly distinct from `Flow` state, which is ephemeral-per-run). **Documented degenerative failure**: repeated re-runs after `429` rate-limit errors caused one production user's agent memory to progressively degrade output quality into hallucination, with no built-in mechanism to select a memory "start point" or partially reset — the only workaround found was deleting the entire local memory store.

### 4.2 Failure taxonomy

| Class | Definition | Cross-framework examples | Mitigation |
|---|---|---|---|
| **Transient** | Resolves on retry without intervention | 429s, 5xx, timeouts, provider-side queueing spikes | Retry with exponential backoff + jitter (never immediately re-trip a breaker on rate-limiting — honor `Retry-After` first) |
| **Permanent** | Will fail identically on every retry | Malformed schema, auth failure, invalid tool name, ADK version-incompatible session schema | Never retry — fail fast to a fallback tier or terminate the step |
| **Poison-pill** | A specific input deterministically fails the same step every time | LangGraph long tool call (>~180s) silently re-dispatched by a stale-run sweep while the original execution is still in flight, causing **2–3× redundant work**; OpenAI SDK `tool_choice="required"` + single available tool causing infinite repeat-calling until `max_turns` exhausts | Idempotency-keyed **claim-before-execute**: derive a stable `request_id` from tool arguments (not attempt metadata), atomically insert a `PENDING` record before execution (Redis `SET NX` / Postgres unique constraint), have redispatched retries resolve against the stored receipt instead of re-executing |

**Framework-specific signature failures (§5 depth in research):**

- **LangGraph**: async-durability memory leak — with default `durability="async"`, each superstep submits a checkpoint-write coroutine capturing a reference to the previous superstep's pending future, forming an unresolved chain that grows **O(N²)** in the worst case as state accumulates; confirmed in production on 50–300-superstep multi-tenant graphs with `AsyncPostgresSaver`. Workaround: `durability="sync"` (adds ~3–5ms round-trip cost per step, acceptable against multi-second LLM turns). Also: a `langgraph dev` flush-loop bug silently dropped its own registrations on the first tick, meaning a SIGKILL/crash/sleep mid-session could silently lose all state since the last clean shutdown while the run *appeared* healthy throughout.
- **OpenAI Agents SDK**: cycle-cost amplification invisible to `max_turns` (§3.2); the `tool_choice` dilemma — optional risks the model hallucinating an answer instead of calling a necessary tool, `"required"` risks looping until budget exhaustion, with no fully satisfying built-in resolution.
- **Google ADK**: broad `except Exception`/`except BaseException` blocks inside tools **silently disable ADK 2.0's native automatic-retry mechanism**, and catching `BaseException` specifically traps `NodeInterruptedError`, breaking the framework's ability to pause for human-in-the-loop input; certain built-in tools (Code Execution, Google Search, Vertex AI Search) cannot be combined with any other tool in the same agent (ADK Python ≤v1.15.0); ADK 2.0.0 stable explicitly pins around a compromised LiteLLM version range (1.82.7–1.82.8) following a supply-chain incident.
- **CrewAI**: "coworker not found" — the manager LLM invents or mis-references a coworker name not matching any registered agent (disproportionately a smaller-model problem); non-OpenAI models (Gemini, Claude) routed via LiteLLM show tool-output hallucination and malformed Thought/Action parsing not observed on GPT-4o/DeepSeek; `knowledge_sources` set at the `Task` level (unsupported — must be `Agent`/`Crew`-level) silently fails to load and the agent fabricates file contents rather than raising a configuration error.

### 4.3 Enterprise security and governance

**Zero-Trust MCP and agent identity (framework-agnostic).** 2026 enterprise practice converges on an Agent Access Model independent of any single framework: every agent gets a unique, cryptographically-rooted identity (no shared service accounts); short-lived, task-scoped, sender-constrained credentials issued by an Identity Broker; deny-by-default tool/API allowlisting enforced at a policy gateway sitting *ahead of* every tool call, not just the first; and an append-only, tamper-evident **Agent Activity Log** using a hash-chain (`hash_i = sha256(hash_{i-1} || event_i)`) that reconstructs "what did this agent do, and on whose authority" independent of the model's own self-report. Regulatory drivers cited: GDPR Art. 30 (processing register), EU AI Act Art. 12 (logging for high-risk systems), SOC 2 Type II, 2025 SEC cybersecurity disclosure rules.

> ⚠️ A frequently-cited "7 in 10 enterprise AI deployments ship without complete audit trails" statistic is a single-source vendor claim without disclosed survey methodology — treat as directional signal, not a validated industry figure.

**Per-framework tool-level RBAC/guardrails:**

- **OpenAI Agents SDK** — the strongest native guardrail system of the four. Guardrails are explicit programmatic checks (LLM-powered *or* deterministic rule-based/regex) setting `tripwire_triggered`; on trip, the SDK raises a typed exception (`InputGuardrailTripwireTriggered`, `OutputGuardrailTripwireTriggered`, `ToolInputGuardrailTripwireTriggered`, `ToolOutputGuardrailTripwireTriggered`) exposing every guardrail result accumulated before the trip for forensics. Separate from guardrails, **human-in-the-loop approvals** pause a run before side effects (cancellations, edits, shell commands, sensitive MCP actions) — a distinct decision point from automatic validation. Recommended pattern: run a cheap/fast model as the input guardrail in blocking mode to filter malicious/off-topic queries before invoking the expensive primary agent. Native sandboxed execution (isolated file systems, manifest-defined mounts) shipped April 2026.
- **Google ADK** — built entirely on **interception hooks** at four lifecycle points: `before_model_callback` (block/modify before the LLM sees the request — returning a non-`None` `LlmResponse` skips the call entirely, the mechanism for guardrails and caching), `after_model_callback` (sanitize raw output), `before_tool_callback` (validate args, authorize — returning a dict skips execution and uses the dict as the result), `after_tool_callback` (sanitize results before re-entering context, or route large payloads to session state — "store-and-slim"). **Critical, explicitly documented gap**: callbacks are opt-in and **not registered by default** — "teams that deploy ADK without callback-based controls are running agents without any runtime guardrail layer." **Plugins** (vs. per-agent callbacks) are the recommended mechanism for policies applied consistently across many agents, registered once at the `Runner` level. Recommended PII pattern: `before_tool_callback` runs detection (e.g., Microsoft Presidio) and blocks calls sending PII to unauthorized services; `before_model_callback` redacts detected PII (e.g., `[PERSON_NAME]`) so only the sanitized prompt reaches the LLM and is logged, tagged `pii_redacted: true`.
- **LangGraph** — no native RBAC/PII primitives; the Postgres checkpointer's `get_state_history()` functions as an incidental but powerful audit mechanism (full, queryable ledger of every state transition per thread, enabling forensic "why did it decide that" review and time-travel replay) — a first-class capability, not a bolt-on, but security controls proper are layered in as custom nodes/middleware around `interrupt()`.
- **CrewAI** — the enterprise "Agent OS" platform (e.g., the PwC partnership) provides RBAC and secure key management across frameworks/cloud providers, native MCP support for scoped corporate-data access, and native A2A protocol support (positioned in at least one analysis as "the only one with native A2A support" among the four). The **open-source core itself ships no built-in PII redaction or guardrail primitives** comparable to OpenAI SDK's guardrails or ADK's callbacks — security is delegated to the enterprise layer or custom tool wrappers.

**Audit logging minimum bar (cross-framework).** Sanitized input parameters, allow/block decision, status, downstream resource URI, latency per tool call — sufficient to reconstruct an agent's actions after a suspected compromise, independent of which framework produced the trajectory.

---

## 5. Production Enterprise Code

The implementation below is a production-hardened LangGraph agent chosen per the "most mature for this pattern" guidance in §2.1/§4.1. It wires together every resilience pattern from §3–§4: retries with exponential backoff + full jitter, a per-tool circuit breaker (CLOSED→OPEN→HALF_OPEN), a fallback chain (primary tool → degraded response), structured JSON logging with a correlation ID per thread, graceful degradation on guardrail/budget trip, and a checkpoint-aware state schema that never grows unbounded (state trimming mitigates §3.2's O(N²) replay cost and §2.1's checkpoint-bloat risk). It targets `langgraph>=0.2` and `langgraph-checkpoint-sqlite` (swap `SqliteSaver` for `PostgresSaver`/`RedisSaver` in production per §4.1's durability guidance) and otherwise uses only the standard library.

```python
"""
production_langgraph_agent.py

A production-hardened LangGraph agent demonstrating every pattern from
Module 05 (Agent Frameworks) Sec 3-4:

  - StateGraph with a bounded, trimmed message schema (avoids the O(N^2)
    context-replay / checkpoint-bloat pathology, Sec 2.1/3.2)
  - per-tool circuit breaker: CLOSED -> OPEN -> HALF_OPEN (Sec 4.2)
  - retry with exponential backoff + full jitter for transient tool
    errors (Sec 4.2's transient/permanent/poison-pill taxonomy)
  - a fallback chain: primary tool -> degraded response, never a bare
    exception surfaced to the caller
  - structured JSON logging with a per-thread correlation ID (Sec 4.3
    audit-logging minimum bar)
  - a Termination Supervisor enforcing max steps / cost / wall-clock
    budgets, evaluated BEFORE each step (never a post-hoc log review)
  - SqliteSaver checkpointing with durability="sync" (Sec 4.1 -- the
    correct default for any graph touching side-effecting tools)

Install:  pip install langgraph langgraph-checkpoint-sqlite
Run:      python production_langgraph_agent.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import sqlite3
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any, Callable, Optional, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver


# --------------------------------------------------------------------------
# 1. Structured logging with per-thread correlation IDs (Sec 4.3)
# --------------------------------------------------------------------------

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get()
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("langgraph_prod_agent")
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


class thread_scope:
    """Binds one correlation ID (the LangGraph thread_id) to every log
    line for a single run, so a full trajectory can be reconstructed
    for audit (Sec 4.3) independent of which node emitted the log."""

    def __init__(self, thread_id: Optional[str] = None):
        self.thread_id = thread_id or str(uuid.uuid4())
        self._token = None

    def __enter__(self) -> str:
        self._token = _correlation_id.set(self.thread_id)
        return self.thread_id

    def __exit__(self, *exc_info) -> None:
        _correlation_id.reset(self._token)


# --------------------------------------------------------------------------
# 2. Failure taxonomy (Sec 4.2): transient vs. permanent
# --------------------------------------------------------------------------

class ToolError(Exception):
    """`transient=False` marks permanent errors that must never be
    retried (auth failure, malformed args, unknown tool)."""

    def __init__(self, message: str, transient: bool = True):
        super().__init__(message)
        self.transient = transient


class BudgetExceededError(Exception):
    """Raised by the Termination Supervisor -- deterministic, evaluated
    BEFORE the next step is allowed to execute (Sec 3.5, 4.2)."""


# --------------------------------------------------------------------------
# 3. Retry with exponential backoff + full jitter (Sec 4.2)
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
# 4. Circuit breaker: CLOSED -> OPEN -> HALF_OPEN, per tool (Sec 4.2)
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
    _outcomes: list = field(default_factory=list, init=False)
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
        self._outcomes = self._outcomes[-self.window_size:]
        if self._state == BreakerState.HALF_OPEN:
            self._state = BreakerState.CLOSED
            self._outcomes.clear()
            log.info(json.dumps({"event": "breaker_closed", "tool": self.name}))

    def record_failure(self) -> None:
        self._outcomes.append(False)
        self._outcomes = self._outcomes[-self.window_size:]
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


_BREAKERS: dict[str, CircuitBreaker] = {}


def get_breaker(tool_name: str) -> CircuitBreaker:
    if tool_name not in _BREAKERS:
        _BREAKERS[tool_name] = CircuitBreaker(name=tool_name, window_size=5,
                                               failure_threshold_ratio=0.6, cooldown_s=10)
    return _BREAKERS[tool_name]


def dispatch_tool(tool_name: str, primary_fn: Callable[[dict], dict],
                   fallback_fn: Callable[[dict], dict], args: dict) -> tuple[str, dict]:
    """Circuit breaker + retry + fallback chain (Sec 4.2). Never lets a
    tool failure surface as a bare exception to the graph -- always
    degrades gracefully instead."""
    breaker = get_breaker(tool_name)
    if breaker.allow_request():
        try:
            result = call_with_retry(lambda: primary_fn(args))
            breaker.record_success()
            return "primary", result
        except ToolError:
            breaker.record_failure()
            log.info(json.dumps({"event": "tool_failed_falling_back", "tool": tool_name}))
    else:
        log.info(json.dumps({"event": "tool_skipped_breaker_open", "tool": tool_name}))
    return "degraded", fallback_fn(args)


# --------------------------------------------------------------------------
# 5. Termination Supervisor: pre-call, deterministic budget guard (Sec 3.5)
# --------------------------------------------------------------------------

@dataclass
class TerminationSupervisor:
    max_steps: int = 12
    max_cost_usd: float = 2.00
    wall_clock_timeout_s: float = 90.0
    _steps: int = field(default=0, init=False)
    _cost_usd: float = field(default=0.0, init=False)
    _started_at: float = field(default_factory=time.monotonic, init=False)

    def check_before_step(self) -> None:
        if self._steps >= self.max_steps:
            raise BudgetExceededError(f"max_steps ({self.max_steps}) reached")
        if self._cost_usd >= self.max_cost_usd:
            raise BudgetExceededError(f"max_cost_usd (${self.max_cost_usd:.2f}) reached")
        if time.monotonic() - self._started_at >= self.wall_clock_timeout_s:
            raise BudgetExceededError(f"wall_clock_timeout_s ({self.wall_clock_timeout_s}) reached")

    def record_step(self, step_cost_usd: float) -> None:
        self._steps += 1
        self._cost_usd += step_cost_usd


_SUPERVISORS: dict[str, TerminationSupervisor] = {}


def get_supervisor(thread_id: str) -> TerminationSupervisor:
    if thread_id not in _SUPERVISORS:
        _SUPERVISORS[thread_id] = TerminationSupervisor()
    return _SUPERVISORS[thread_id]


# --------------------------------------------------------------------------
# 6. State schema -- bounded/trimmed to avoid O(N^2) replay (Sec 2.1, 3.2)
# --------------------------------------------------------------------------

def _keep_last_n(existing: list, new: list, n: int = 20) -> list:
    """Reducer: append new messages, then trim to the last N -- this is
    the concrete mitigation for the checkpoint-bloat / quadratic-token
    pathology documented in Sec 2.1 and 3.2. Trades perfect long-range
    recall for bounded per-step cost, an explicit, logged trade-off."""
    merged = existing + new
    if len(merged) > n:
        log.info(json.dumps({"event": "state_trimmed", "dropped": len(merged) - n}))
    return merged[-n:]


class AgentState(TypedDict):
    messages: Annotated[list[dict], _keep_last_n]
    goal: str
    status: str
    tool_source: str


# --------------------------------------------------------------------------
# 7. Tool implementations (a flaky search backend for demonstration)
# --------------------------------------------------------------------------

def flaky_search_tool(args: dict) -> dict:
    if random.random() < 0.3:
        raise ToolError("search backend 503", transient=True)
    return {"results": [f"doc about {args.get('query', '?')}"]}


def degraded_search_fallback(args: dict) -> dict:
    return {"results": [], "note": "search unavailable, proceeding without fresh context"}


# --------------------------------------------------------------------------
# 8. Graph nodes
# --------------------------------------------------------------------------

def plan_node(state: AgentState) -> dict:
    thread_id = _correlation_id.get()
    supervisor = get_supervisor(thread_id)
    try:
        supervisor.check_before_step()
    except BudgetExceededError as exc:
        log.info(json.dumps({"event": "run_terminated", "reason": "budget_exceeded", "detail": str(exc)}))
        return {"status": "stopped_budget"}

    step_hint = "search" if len(state["messages"]) < 6 else "finish"
    log.info(json.dumps({"event": "plan_step", "decision": step_hint}))
    return {"messages": [{"role": "assistant", "content": f"planning: {step_hint}"}], "status": step_hint}


def tool_node(state: AgentState) -> dict:
    thread_id = _correlation_id.get()
    supervisor = get_supervisor(thread_id)
    source, observation = dispatch_tool(
        tool_name="search",
        primary_fn=flaky_search_tool,
        fallback_fn=degraded_search_fallback,
        args={"query": state["goal"]},
    )
    supervisor.record_step(step_cost_usd=0.02)
    return {
        "messages": [{"role": "tool", "content": json.dumps(observation)}],
        "tool_source": source,
        "status": "planning",
    }


def finish_node(state: AgentState) -> dict:
    log.info(json.dumps({"event": "run_terminated", "reason": "goal_reached"}))
    return {"status": "completed"}


def route_after_plan(state: AgentState) -> str:
    if state["status"] == "stopped_budget":
        return END
    if state["status"] == "finish":
        return "finish"
    return "tool"


# --------------------------------------------------------------------------
# 9. Graph assembly with sync-durability SQLite checkpointing (Sec 4.1)
# --------------------------------------------------------------------------

def build_graph():
    builder = StateGraph(AgentState)
    builder.add_node("plan", plan_node)
    builder.add_node("tool", tool_node)
    builder.add_node("finish", finish_node)

    builder.add_edge(START, "plan")
    builder.add_conditional_edges("plan", route_after_plan, {"tool": "tool", "finish": "finish", END: END})
    builder.add_edge("tool", "plan")
    builder.add_edge("finish", END)

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return builder.compile(checkpointer=checkpointer)


# --------------------------------------------------------------------------
# 10. Example run
# --------------------------------------------------------------------------

if __name__ == "__main__":
    graph = build_graph()

    with thread_scope() as thread_id:
        log.info(json.dumps({"event": "run_start", "goal": "Research topic X"}))
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}
        try:
            final_state = graph.invoke(
                {"messages": [], "goal": "Research topic X", "status": "planning", "tool_source": "none"},
                config=config,
            )
            log.info(json.dumps({"event": "final_result", "status": final_state["status"],
                                  "message_count": len(final_state["messages"])}))
        except Exception as exc:  # graceful degradation: never crash the caller
            log.info(json.dumps({"event": "run_failed_gracefully", "error": str(exc)}))
            print(json.dumps({"status": "degraded", "reason": str(exc)}))
```

**What each pattern buys, mapped back to §3–§4.** The `_keep_last_n` reducer bounds the state that gets re-serialized into every checkpoint and re-sent to the model on every turn, directly countering §2.1's `O(d²)` replay cost and §3.2's checkpoint-bloat/serialization-overhead findings — the trade-off (bounded recall) is made explicit in a log line rather than a silent truncation. The per-tool `CircuitBreaker` isolates a flaky search backend's failures from the rest of the graph and, on trip, routes to a structured degraded response instead of raising — exactly the "the agent's tool contract must define what to do on `CIRCUIT_OPEN`, or it will attempt unpredictable workarounds" guidance from cross-framework circuit-breaker practice. `TerminationSupervisor.check_before_step()` runs *before* every planning step, never after, which is the design decision that separates a bounded run from an unbounded handoff/tool-call loop (§4.2's `tool_choice="required"` and LangGraph long-tool-call failure modes). `thread_scope` binds one correlation ID to every log line for the run's lifetime, satisfying §4.3's audit-logging minimum bar. Swapping `SqliteSaver` for `PostgresSaver` and setting `durability="sync"` on `.compile()`/`.invoke()` is the only change needed to move this from a demo to the production-recommended durability mode described in §4.1.

---

## 6. Architectural System Design Scenarios

### Scenario A — Multi-channel customer-support triage platform at consumer-fintech scale

**Problem statement.** A consumer fintech (Klarna-scale: tens of millions of users, millions of daily support-adjacent interactions) needs an AI support layer that resolves routine queries (balance disputes, refund status, card issues) without human involvement, escalates ambiguous or high-risk cases (fraud claims, chargebacks) to a human with full context, and produces a regulator-reviewable audit trail for every automated decision — while keeping per-conversation cost low enough to be economically viable at tens-of-millions-of-conversations/year scale (the shape of the Klarna production deployment: 85M active users, 2.5M daily transactions, 80% resolution-time reduction, ~70% automation of repetitive tasks).

**Proposed architecture.**

```
User message → Identity/session resolution (thread_id keyed to the
               user's support session)
                              │
                              ▼
              Intake classifier node (fast/cheap model): routes into
              {routine_query | ambiguous | high_risk} -- a compiled
              conditional edge, not a further LLM-driven handoff, so
              routing is testable and audit-reproducible (Sec 2.1)
                              │
              ┌───────────────┼────────────────────┐
              ▼                ▼                     ▼
      routine_query      ambiguous                high_risk
      (single-node        (multi-step             (immediate
      tool-augmented       retrieval + verify      human handoff,
      response; cached      loop, bounded by        zero autonomous
      prompt prefix for     Termination Supervisor  action -- Sec 4.3
      the support persona) max_steps, Sec 3.5/5)    RBAC gate)
              │                │                     │
              └────────────────┴─────────────────────┘
                              ▼
              Postgres Checkpointer (thread-scoped) + Store (cross-
              thread: user preferences, prior resolved-ticket history)
              -- get_state_history() gives support-ops and compliance
              a full replayable trajectory per conversation (Sec 4.1)
                              ▼
              LangSmith / OTel trace per conversation; cost-per-
              resolution dashboard alerting at 2x baseline (Sec 3.6)
```

**Framework choice: LangGraph.** The routing decision (routine vs. ambiguous vs. high-risk) must be deterministic and auditable — a compiled conditional edge, not a model-emitted handoff — because a regulator reviewing a mishandled fraud case needs to see *why* the system routed it where it did, independent of whatever the model happened to output that day. LangGraph's `Store` also directly serves the cross-conversation memory requirement (a user's prior ticket history informing the current one) that none of the other three frameworks handle as a first-class, explicitly-separate-from-thread-state primitive.

**Trade-off matrix.**

| Dimension | LangGraph (proposed) | OpenAI Agents SDK | Google ADK | CrewAI |
|---|---|---|---|---|
| Cost / 1k conversations | Lowest at this volume — code-routed classification avoids paying an LLM call per routing decision (§3.1's "near-zero" orchestration tokens) | Handoff-based routing between a triage agent and a specialist agent risks §4.2's cycle-amplification if the specialist ever hands back for clarification | Comparable `[inferred]`, but quota-gated — 10M+ conversations/year at fintech scale requires careful QPM capacity planning against Vertex's hard ceilings (§3.5) | Highest — persona-scaffold overhead (§3.1) on every one of tens of millions of routine queries is the single largest avoidable cost at this scale |
| Latency | Fastest measured architecture (§3.1); matters directly for a live-chat support UX | Network-bound; WebSocket transport needed to hit interactive-latency targets | Comparable `[inferred]`; BidiStream cap (10/min) is a hard architectural constraint for a live-chat surface specifically | Slowest measured (§3.1) — a poor fit for live, latency-sensitive support chat |
| Ops complexity | Highest to build (steep ramp, §3.6) but justified by the multi-year production track record this exact shape has at Klarna's scale | Lower ramp, but the handoff-history-inheritance default (§2.2) needs an explicit `input_filter` to avoid context bloat across triage→specialist hops | Medium-high; ADK 2.0's migration fragility (§4.1) is a real operational risk for a system that cannot tolerate a botched version upgrade mid-production | Lowest ramp, but the least verifiable production behavior (manager-LLM-inferred delegation, §2.4) is a poor match for a regulator-facing system |
| Security/audit | `get_state_history()` gives a first-class, queryable, replayable ledger per conversation (§4.1) — directly satisfies the regulatory audit requirement | Strong native guardrails (§4.3) for filtering malicious input, but no equivalent first-class conversation-replay primitive | Callback/plugin hooks are powerful but **opt-in and unregistered by default** (§4.3) — a documented governance gap unacceptable for a regulated fintech without disciplined internal enforcement | Enterprise "Agent OS" layer can add RBAC, but OSS core has no built-in audit/PII primitives (§4.3) |
| Scalability | Proven at 85M-user scale via LangGraph Platform / self-hosted workers (§3.5, §6 production deployments) | Scales via chosen session backend; no native distributed executor | Hard QPM/BidiStream quota ceilings requiring proactive capacity planning (§3.5) | No native distributed executor; scale is a pure function of the underlying LLM provider |

**Decision rationale.** LangGraph is selected specifically because the triage decision must be a compiled, testable function rather than a model-emitted routing choice — the fintech's compliance requirement ("show us why this was routed this way") is structurally satisfied by a conditional edge and structurally *not* guaranteed by an LLM-driven handoff, where the routing rationale lives inside a non-deterministic model call. The `Store`/`Checkpointer` separation maps directly onto the two kinds of memory the product needs (this-conversation state vs. this-user's-history-across-conversations), and `get_state_history()` closes the audit-trail requirement without any custom infrastructure. The cost argument reinforces the same conclusion independently: at tens-of-millions-of-conversations/year volume, CrewAI's 2–3× persona-scaffold overhead alone would add a cost line large enough to threaten the product's unit economics, while LangGraph's near-zero orchestration-token routing keeps the *marginal* cost of triage close to the cost of the actual resolution work.

### Scenario B — Cross-system enterprise research assistant spanning CRM, document stores, and external web sources

**Problem statement.** An enterprise (PwC/DocuSign-shaped: a consulting or SaaS platform team) wants an agent that researches a target account across Salesforce, an internal document/knowledge base, and the public web, then drafts a structured briefing for a human reviewer — a role-clear, multi-step business process (identify → research → compose → validate) with a natural human-in-the-loop gate before anything is sent externally, and a requirement to reach a *working* prototype quickly because the business case must be proven before infrastructure investment is justified.

**Proposed architecture.**

```
Target account → Identifier Agent (selects target accounts from CRM
                  query results)
                              │
                              ▼
              Researcher Agent (gathers context: CRM history, internal
              docs via a knowledge-base tool, public web search) --
              sequential Process: each agent's output becomes the next
              agent's context, matching the natural information-
              dependency order of this workflow (Sec 2.4)
                              │
                              ▼
              Composer Agent (drafts the outreach/briefing document
              from gathered context)
                              │
                              ▼
              Validator Agent (gates on hallucination-risk and
              personalization-quality thresholds BEFORE human review --
              a deterministic check, not a further LLM's self-report)
                              │
                              ▼
              Human review + delivery (the durable HITL gate; nothing
              external-facing ships without this step, Sec 4.3)
                              │
                              ▼
              Run/tool/duration monitoring → ROI dashboard (Sec 4.3 --
              the enterprise "Agent OS" observability layer)
```

**Framework choice: CrewAI.** This workflow is exactly CrewAI's designed sweet spot: a small number of *role-clear* agents (Identifier, Researcher, Composer, Validator) with a naturally sequential information dependency, no need for arbitrary cyclic control flow, and a business requirement to prove the concept in days rather than the 1–2 weeks LangGraph's ramp-up would cost before a single production conversation happens. The documented "prototype with Crews, harden with Flows" pattern is followed directly: the initial four-agent sequential Crew is the MVP, with a `Flow` wrapper added once the pipeline needs conditional branching (e.g., skip the Composer step if the Researcher agent finds insufficient data) or needs to be triggered on a schedule/event rather than ad hoc.

**Trade-off matrix.**

| Dimension | CrewAI (proposed) | LangGraph | OpenAI Agents SDK | Google ADK |
|---|---|---|---|---|
| Cost / 1k briefings | Higher per-run cost (§3.1's persona-scaffold overhead) than LangGraph, but the *absolute* volume here is far lower than Scenario A's consumer-support case (dozens-to-hundreds of briefings/day, not millions) — the overhead is real but not the dominant business constraint at this scale | Lowest per-run cost, but the cost savings are marginal in absolute dollars at this volume and don't offset a materially longer build time | Handoff-based agent-to-agent delegation would work but adds the cycle-amplification risk (§4.2) if Validator ever needs to send work back to Composer for revision | Comparable `[inferred]`; Google Cloud-native tooling (Agentspace) is attractive only if the enterprise is already Gemini/Workspace-committed |
| Latency | Slower than LangGraph (§3.1), but this is an asynchronous, human-reviewed workflow (minutes-to-hours turnaround is acceptable), not a live-chat surface — latency is a non-issue at this scenario's actual UX requirement | Fastest, but the latency advantage is irrelevant to a workflow that already ends in a human review queue | Network-bound; the WebSocket optimization (§3.2) targets tool-call-heavy live interactions, not this batch-shaped workflow | Comparable `[inferred]`; irrelevant for the same reason as LangGraph |
| Ops complexity | Lowest (§3.6) — directly enables the "prove the business case fast" requirement; the documented DocuSign pattern of "expanded beyond the first use case into a repeatable operating loop" shows this scales organizationally once proven | Highest ramp-up cost, hard to justify before the business case is proven | Lower ramp than LangGraph, but session-backend selection and cycle-detection are DIY (§3.6) — extra work not needed for a sequential, non-cyclic pipeline | Medium-high; ADK's migration fragility (§4.1) is a real but secondary concern for a workflow with modest reliability requirements (human always reviews before anything ships) |
| Security/audit | OSS core has no built-in guardrails (§4.3) — acceptable specifically *because* the Validator + mandatory human-review gate already provides the hard stop before anything external-facing ships; enterprise "Agent OS" layer adds RBAC/MCP-scoped access if/when this graduates beyond prototype | Strongest audit primitive (`get_state_history()`) but this workflow's compliance bar (human always reviews) doesn't require conversation-level regulatory replay the way Scenario A's automated-decision fintech case does | Strong native guardrails, but they solve a problem (validating autonomous, unsupervised output) this workflow doesn't have, since a human reviews every output | Callback/plugin architecture is powerful but its opt-in-by-default gap (§4.3) matters less here given the human gate |
| Scalability | No native distributed executor (§3.5) — acceptable at dozens-to-hundreds of briefings/day; would need re-architecture (or a `Flow`-based rewrite) if volume grew to Scenario A's scale | Best scalability story of the four, but scalability is not this scenario's binding constraint | Scales via session backend choice; adequate for this volume | Hard quota ceilings are irrelevant at this volume |
| **Best-fit signal** | Role-clear, sequential, human-gated, low-to-moderate-volume business process where time-to-first-working-prototype dominates the decision | Regulated, high-volume, or long-running workflows where the audit/scale investment pays for itself (Scenario A) | OpenAI-native stacks with autonomous, unsupervised multi-agent delegation needing strong built-in safety rails | Google Cloud/Gemini-native enterprises wanting unified search + orchestration (Agentspace integration) |

**Decision rationale.** The deciding factor is volume and supervision shape, not raw per-run efficiency: this workflow produces a small number of human-reviewed artifacts per day, so CrewAI's 2–3× token overhead (§3.1) — the single most damning number against it in Scenario A — translates to a marginal absolute cost difference here, while its 2–4 hour ramp-up (§3.6) directly serves the business requirement to validate the use case before committing to LangGraph's steeper build. The mandatory Validator-then-human-review gate structurally absorbs the risk that CrewAI's OSS core lacks built-in guardrails (§4.3): the architecture doesn't need the framework to police the agent, because a human already does, downstream of a deterministic (non-LLM) hallucination-risk check. This is the mirror image of Scenario A's reasoning — there, audit-grade determinism and cost-at-scale forced LangGraph; here, prototyping speed and a human safety net make CrewAI's flexibility a net win rather than a liability, with an explicit, pre-planned escape hatch (`Flow`-wrapping the Crew) if the pipeline's branching complexity or volume later outgrows a flat sequential Process.
