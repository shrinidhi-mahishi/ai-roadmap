# Topic 5: Master One Framework
> Consolidated from multi-model research (Grok + Opus) | Study & Interview Prep

---

## Introduction

### What This Topic Covers

This topic is a **deep comparative analysis** of the three leading agent frameworks — LangGraph (state machines), CrewAI (role-based crews), and OpenAI Agents SDK (handoff chains) — with Google ADK as a brief reference. The goal is to give you enough depth to **master one framework** while being able to articulate the trade-offs of the others in an interview. It covers execution models, state management, checkpointing, token economics, enterprise security (SOC 2, HIPAA), known vulnerabilities, production code implementing the same workflow in all three frameworks, and migration patterns between them.

### Why Study This

- **The framework question is inevitable**: In any Director/VP AI interview, you will be asked "What framework would you use and why?" This topic gives you a decision matrix to answer with precision.
- **Architectural depth**: Knowing that LangGraph uses Pregel-style execution with 47% lower token costs than CrewAI, or that OpenAI SDK's handoffs are non-deterministic and compound failure rates (85% per step = 27% over 8 steps), shows you've operated these systems — not just read the docs.
- **Production readiness**: LangGraph is the only framework with built-in durable checkpointing and time-travel debugging. CrewAI loses state on container restart by default. These facts drive real architectural decisions.
- **Career positioning**: The common pattern is "prototype in CrewAI, productionize in LangGraph." Knowing when to recommend each shows executive-level judgment.

### What Details Are Included

- Architecture diagrams for all three frameworks with control/data/persistence planes
- Pregel execution model, state channels, reducers, conditional edges (LangGraph deep dive)
- CrewAI Flows/Crews/Agents/Tasks architecture with process types
- OpenAI Agents SDK handoff mechanics, guardrails, context variables
- Cross-framework comparison table (10 dimensions) and decision matrix (8 scenarios)
- Same customer support workflow implemented in all three frameworks
- Per-framework cost comparison, maturity indicators (GitHub stars, PyPI downloads)
- CVE listings and security comparison across frameworks
- Migration patterns (CrewAI → LangGraph, OpenAI SDK → LangGraph)
- Two enterprise system design scenarios with framework selection rationale

### How to Approach This Document

> **First pass (2-3 hours)**: Sections 1-4. Study the architecture diagrams for all three frameworks. Focus on the comparison table and decision matrix — these are interview gold.
>
> **Second pass (2-3 hours)**: Section 6. Read through the same workflow in all three frameworks. Pick one framework and implement it yourself. Understand the migration patterns.
>
> **Deep dive (2+ hours)**: Pick the framework you want to master (LangGraph recommended for production roles) and study its internals — Pregel execution, checkpointers, state channels.
>
> **Interview prep (1 hour)**: Section 10. Practice the "which framework" answer: state the decision matrix, recommend one, articulate why the others lose for this specific use case.
>
> **Before an interview (30 min)**: Re-read section 10 only.

### How This Document Is Structured

This guide follows a **10-section progressive learning flow** — each section builds on the previous:

| # | Section | What It Covers | Study Approach |
|---|---------|---------------|----------------|
| 1 | Concept Overview | What and why | Read first for orientation |
| 2 | Core Concepts | Fundamental building blocks | Study deeply, take notes |
| 3 | Architecture & System Design | ASCII diagrams, topology, data flow | Draw diagrams from memory |
| 4 | Key Algorithms & Mechanics | Technical depth, complexity analysis | Understand the "why" |
| 5 | Token Economics & Cost Analysis | Pricing, cost formulas, optimization | Memorize key numbers |
| 6 | Production Patterns & Code | Runnable Python implementations | Run, modify, and break the code |
| 7 | Failure Modes & Mitigations | What goes wrong, how to handle it | Practice explaining failure scenarios |
| 8 | Security & Governance | Enterprise security considerations | Know compliance frameworks by name |
| 9 | System Design Scenarios | Real-world problems with trade-offs | Practice whiteboarding these |
| 10 | Interview Quick Reference | Key numbers, frameworks, talking points | Review 30 min before interviews |

---

> **Scope**: Deep comparative analysis of LangGraph, CrewAI, and OpenAI Agents SDK -- execution models, state management, token economics, enterprise resilience, security, production code, and system design scenarios. Google ADK included as brief reference.
> **Framework versions**: LangGraph 1.0+ (GA 2025-10-22, MIT, Python >=3.10), CrewAI 1.10.x, OpenAI Agents SDK 0.2.x (Apr 2026).
> **Pricing assumptions**: GPT-4.1 $2/$8 per 1M in/out; GPT-6 Sol $2/$10; GPT-6 Luna $0.10/$0.50; GPT-5.4 $2.50/$15; GPT-4o $2.50/$10; GPT-4o-mini $0.15/$0.60; Claude Sonnet 4 $3/$15. All as of mid-2026.

---

## 1. Concept Overview

### What Is This Topic?

"Master one framework" means choosing -- and deeply understanding -- the orchestration layer that schedules LLM calls, manages state, handles failures, and enforces control flow in production agent systems. The three leading frameworks each embody a different execution philosophy:

| Framework | Core Metaphor | One-line Mental Model |
|-----------|---------------|----------------------|
| **LangGraph** | Pregel state machine | You draw every edge; the model is an untrusted planner inside your graph |
| **CrewAI** | Role-playing team | Agents with personas collaborate on tasks; a manager may delegate |
| **OpenAI Agents SDK** | Handoff chain | Specialists pass the conversation baton; control flow lives in prompts |

### Why It Matters

The framework choice determines your **control surface** over cost, durability, testability, and compliance:

- **Cost control**: A 6-node ticket workflow with 3 LLM calls is **[inferred] $37.20/1k** on GPT-4.1 list. The same skeleton with `Send` N=8 workers is **$124/1k**. CrewAI's hierarchical manager consumes ~30% of total token budget just for coordination. OpenAI SDK's handoff pattern naturally skips unnecessary agents. The framework's execution model directly drives token spend.
- **Durability**: LangGraph checkpoints let you park a HITL approval for days at $0 worker cost. CrewAI's default storage is lost on container restart. OpenAI SDK has no workflow-level checkpointing -- process crash = lost work.
- **Testability**: LangGraph edges are deterministic Python functions you can unit test. OpenAI SDK handoff routing lives in prompts and is caught only by evals -- if caught at all. CrewAI hierarchical delegation is LLM-driven and non-deterministic.
- **Compliance**: Only LangGraph offers native durable checkpoints with time-travel replay for dispute resolution, HIPAA BAA, and node-level audit trails via LangSmith.

**The model never executes tools, edges, interrupts, or checkpoints.** It emits structured actions or text. The framework runtime handles everything else. Collapsing LangGraph `recursion_limit` (super-steps), OpenAI `max_turns` (model invocations), and CrewAI process type into one "framework iteration cap" is how teams ship unbounded fan-out or silent "Sorry, need more steps..." on HTTP 200.

### Interview Traps (Fail These, Fail the Round)

- `{"configurable": {"recursion_limit": 50}}` -- `recursion_limit` is a **top-level** invoke key. Putting it inside `configurable` is **ignored**.
- Treating **25** as a subgraph **nesting** cap. Nested subgraphs consume parent super-steps; people hitting 25 hit the old **step fuse**. Current default **1000**. Cloud **25 MB** payload -> **413** is the other "25."
- `MessageGraph` / `create_react_agent` as the 2026 answer (deprecated in 1.0; `create_agent` is the replacement and still returns a `CompiledStateGraph`).
- Missing reducer: `{"messages": [new]}` **wipes** history (`LastValue`). Parallel `LastValue` writes in one super-step -> `InvalidUpdateError`. `operator.add` cannot overwrite an edited HITL message (`add_messages` replaces by `id`).
- `Command(goto=X)` **and** `add_edge(node, Y)` run **both** X and Y.
- `InMemorySaver` behind FastAPI; HITL wait holds a worker; process restart drops the interrupt.
- Secrets in `state` or `config` (both checkpoint-associated). Use `Runtime.context` / `UntrackedValue`.
- `durability="exit"` on a payment graph; side effects before `interrupt()` re-run on resume.
- Unbounded `Send` from an LLM-invented list. `recursion_limit` does **not** cap fan-out width.
- Prebuilt ReAct `_are_more_steps_needed` -> "Sorry, need more steps..." instead of `GraphRecursionError`.
- Mixing CrewAI hierarchical manager, OpenAI handoffs, and a LangGraph Pregel in **one** HTTP handler.

---

## 2. Core Concepts

This section builds from the ground up: LangGraph's Pregel execution model first (the deepest), then CrewAI's role-based design, then OpenAI Agents SDK's handoff chains. Each subsection explains the "why" behind the design, not just the "how."

### 2.1 LangGraph: Pregel-Style State Machines

LangGraph is a **Pregel state machine** for agents. You compile a `StateGraph[State]` into a `Pregel` instance whose nodes are actors, whose keys are **channels** with reducers, and whose ticks are **super-steps** with optional Postgres checkpoints, cross-thread Store, `interrupt()` HITL, `Send` map-reduce, and subgraphs. LangGraph 1.0 GA was announced 2025-10-22 (MIT license). It is usable without the rest of LangChain as a framework, while still depending on `langchain-core` (PyPI `langgraph` 1.2.x).

**Pregel super-steps.** LangGraph's runtime is named after Google Pregel (SIGMOD 2010): computation proceeds in bulk-synchronous super-steps. During super-step S, actors read messages from S-1, run in parallel, and send messages that become visible only at S+1.

```
  super-step S
  +----------+   actors with mail from S-1      +----------+
  |  PLAN    | --------------------------------> | EXECUTE  |  parallel
  +----------+                                   +----+-----+
       ^  writes invisible to siblings until barrier  |
       |                                              v
       |                                         +----------+
       |                                         |  UPDATE  |  reducers + checkpoint
       |                                         +----+-----+
       |  actors remain / mail in transit             |
       +----------------------------------------------+
            no actors selected, or recursion_limit -> HALT (GraphRecursionError)
```

A super-step is one "tick": parallel nodes share a tick. A linear `START -> A -> B -> END` uses **separate** ticks for input, A, and B -- and therefore separate checkpoints.

**Unit of composition:** `StateGraph[State]`. `State` is a TypedDict, dataclass, or Pydantic model. Each key is a **channel**. Default channel is `LastValue` -- last write wins. Annotate with a reducer `(Value, Value) -> Value` so parallel writes **merge** instead of clobber.

**State channels and reducers:**

| Channel / Annotation | Semantics | Typical Use |
|----------------------|-----------|-------------|
| `LastValue` (default) | Overwrite | Scalars, latest draft |
| `Annotated[list, operator.add]` | Concatenate | Fan-in lists from `Send` |
| `Annotated[list[AnyMessage], add_messages]` | Append by default; **replace by message `id`**; honor `RemoveMessage` | Transcript |
| `BinaryOperatorAggregate` | Running fold | Counters |
| `Topic(..., accumulate=True)` | PubSub accumulate | Low-level Pregel |
| `DeltaChannel(bulk_reducer, snapshot_frequency=K)` | Persist **deltas**, reconstruct on read; snapshot every K steps | Long `messages` (>=1.2, beta) |
| `RemainingSteps` (managed) | Runtime-owned int; **not** user-initialized | Proactive fuse |
| `UntrackedValue` | Not checkpointed | Secrets that must not land in snapshots |

**Critical bug pattern:** Without a reducer, a node that returns `{"messages": [new]}` wipes prior history. That is the dominant "lost messages" bug. `operator.add` appends but cannot overwrite an edited HITL message (it would duplicate). `add_messages` appends new IDs and replaces matching IDs. Parallel `LastValue` writes in one super-step raise `InvalidUpdateError` -- two `Send` workers writing `foo: str` (no reducer) is a crash, not last-write-wins.

**Nodes, edges, `Command`, `Send`:**

- **Nodes** are `State -> Partial[State]` (sync or async). They do not own control flow unless they return `Command`.
- **Edges:** `add_edge(src, dst)` is static. `add_conditional_edges(src, router)` -- router returns a node name, `END`, a list of names (all run next tick in parallel), or `list[Send]`.
- **`Command`** combines a state write with a hop. `goto` augments routing; it does **not** suppress `add_edge`. If both exist, both destinations run. `resume` is the only `Command` field meant as invoke/stream input.
- **`Send` (map-reduce):** return `[Send("worker", {**slice})]` from a conditional edge. Worker count is data-dependent. Cap `len(subjects)` in the router -- `recursion_limit` does **not** cap width.

**`ToolNode`:** `langgraph.prebuilt.ToolNode` executes `AIMessage.tool_calls` in parallel, injects `InjectedState`/`InjectedStore`/`ToolRuntime`, and can return `Command` from tools. Default `handle_tool_errors` catches invocation errors into a `ToolMessage` and re-raises execution errors. In-process parallelism is not a distributed lock around Stripe.

**Checkpointers and `thread_id`:**

| Saver | Use |
|-------|-----|
| `InMemorySaver` | Tests only; RAM; lost on restart |
| `SqliteSaver` | Local demos; not multi-writer prod |
| `PostgresSaver` | Production; LangSmith default |
| Agent Server injected saver | Cloud / self-host runtime |

A checkpoint is a `StateSnapshot` at a super-step: `values`, `next`, `config` (containing `thread_id`, `checkpoint_ns`, `checkpoint_id`), `metadata`, `parent_config`, `tasks`. Without `thread_id`, the saver cannot load/resume. `checkpoint_id` is a monotonically increasing ULID.

**Interrupt / HITL, resume:** Two pause APIs, both require a checkpointer + `thread_id`. The graph waits indefinitely. `interrupt(value)` inside a node (production); `interrupt_before`/`interrupt_after` at compile or invoke (debug/stepping). On resume, the **whole node restarts** -- side effects before the call re-run. Multiple `interrupt()` calls in one node match resume values by order.

**Subgraphs:** A compiled graph used as a node. `checkpointer=None` (default) inherits parent saver; `True` gives own `checkpoint_ns` history (do not call in parallel); `False` is stateless. There is no documented nesting-depth cap of 25.

**`recursion_limit` and durability:** `recursion_limit` counts super-steps, not Python stack frames. Default is **1000** since langgraph 1.0.6 (the old **25** was the LangChain runnable default, not LangGraph). Durability modes: `"exit"` (fast, no mid-graph recovery), `"async"` (Agent Server default, small window of lost last tick), `"sync"` (highest durability, extra latency).

**Streaming modes (7 combinable):**

| Mode | Payload |
|------|---------|
| `values` | Full state after each step |
| `updates` | Per-node deltas |
| `messages` | Token-by-token from any LLM -- **this is TTFT** |
| `custom` | `get_stream_writer()` payloads |
| `checkpoints` | Same shape as `get_state()` |
| `tasks` | Task start/finish + errors |
| `debug` | checkpoints + tasks + extra metadata |

**`create_agent` vs raw `StateGraph`:** LangGraph v1 deprecates `create_react_agent` in favor of `create_agent` (from `langchain.agents`), which returns a `CompiledStateGraph`. Drop to raw `StateGraph` when you need cycles beyond "model <-> tools", `Send` map-reduce, `Command.PARENT` handoffs, or mixed deterministic/LLM branches.

### 2.2 CrewAI: Role-Based Design with Process Control

CrewAI models multi-agent collaboration as a team of role-playing agents organized in a four-layer hierarchy: **Flows -> Crews -> Agents -> Tasks**. Built independently (not wrapping LangChain).

**Agent design:** Each agent gets a `role`, `goal`, and `backstory` that collectively shape its LLM behavior. The backstory is the key differentiator from simple system prompts -- it provides context that grounds the agent's reasoning in a persona.

**Process types:**
- **Sequential:** Tasks execute one after another; each agent's output feeds into the next. Most predictable for debugging.
- **Hierarchical:** A manager agent (separate LLM call) dynamically delegates tasks to specialists. Known bug: manager sometimes runs tasks in sequence instead of routing to the best agent (GitHub issue #4783, March 2026). The manager consumes ~30% of total token budget just for coordination.
- **Consensual** (planned, not yet released): Agents negotiate and vote before executing.

**Memory system:**
- **Short-term:** Within a single crew execution, agents share context about what has been done.
- **Long-term:** Persisted across executions. Stores task results, agent observations.
- **Entity:** Named-entity tracking across interactions (e.g., remembering a specific customer across runs).
- **Key limitation:** Memory stays scoped to a single Crew. No federation, conflict resolution, or ownership metadata across Crews.

**Tool ecosystem:** 60+ built-in tools (web search, file I/O, code execution, database queries, API integrations). Native MCP and A2A protocol support.

**Request flow:**
1. A Flow starts, evaluating step decorators (`@start`, `@listen`, `@router`).
2. The first Crew receives structured input from the Flow.
3. The Crew's process type determines task execution order.
4. In sequential mode: Task 1 runs with Agent 1; output feeds as context into Task 2 with Agent 2; and so on.
5. In hierarchical mode: A manager LLM reviews all tasks, selects which agent handles each, may re-delegate.
6. Each agent internally runs a ReAct-style loop: reason, optionally call tools, produce output matching `expected_output` format.
7. The Crew returns its final output to the Flow for routing.

### 2.3 OpenAI Agents SDK: Handoff Chains and Guardrails

Released March 2025 as the successor to experimental Swarm. Six core primitives: **Agent, Runner, Tools, Handoffs, Guardrails, Sessions**.

**Handoff mechanics:** A handoff is implemented as a tool call (`transfer_to_X`). When the model invokes it, the Runner switches the active agent, passes full conversation history, and the new agent fully takes over. This means handoffs are **flat, not hierarchical** -- there is no "manager" that collects results.

**Critical design implication:** Control flow lives in prompts, not code. A router that misroutes in code is a unit-testable bug. A handoff that misroutes is caught in evals -- if caught at all.

**Guardrail system:**
- **Input guardrails:** Run on initial user input before processing. Can use a cheap, fast model for classification.
- **Output guardrails:** Validate the final agent's response using Pydantic models.
- **Tripwire pattern:** If a guardrail detects a violation, it raises a `tripwire` exception that halts the agent.
- **Scope limitation:** Input guardrails apply only to the first agent. Output guardrails apply only to the final agent. Intermediate agents in a handoff chain are unchecked.

**Context variables:** Typed, dependency-injection-style values passed through the agent chain for sharing request-scoped data without polluting conversation history.

**Model-agnostic support:** Despite "OpenAI" in the name, the SDK supports any OpenAI-compatible API provider. The Responses API is used by default; Chat Completions API works for others.

**Persistence:** Session backends (SQLite/Redis) for conversation state. No workflow-level checkpointing. The April 2026 long-horizon harness partially addresses tasks spanning hours/days.

### 2.4 Google ADK (Brief Reference)

Google Agent Development Kit uses an event-driven runtime with a hierarchical agent tree: Root Agent delegates to Sequential, Parallel, and Loop agents. Unique capability: first-class support for **both** MCP (vertical tools) and A2A (horizontal agent-to-agent) protocols. Bidirectional audio and video streaming. Deploy on Vertex AI, Cloud Run, or GKE. Languages: Python, TS, Go, Java.

### 2.5 Cross-Framework Comparison

| Dimension | LangGraph | CrewAI | OpenAI Agents SDK |
|-----------|-----------|--------|-------------------|
| **Execution model** | Directed graph (Pregel-style) | Role-based crews with process types | Handoff chains (flat) |
| **Primary abstraction** | State + Nodes + Edges | Agents + Tasks + Crews + Flows | Agent + Runner + Handoffs |
| **Control flow** | Explicit (you draw every edge) | Implicit (process type + delegation) | Prompt-driven (handoff routing) |
| **State management** | First-class typed state with reducers | Task output passing; ephemeral memory | Session-based conversation history |
| **Human-in-the-loop** | Native (`interrupt()` / `Command(resume=...)`) | Not built-in | Not built-in (sessions only) |
| **Extensibility** | Highest (arbitrary graph topology) | Medium (3 process types) | Low-medium (linear handoff chains) |
| **Testability** | High (edges are deterministic code) | Medium (LLM-driven delegation) | Low (routing in prompts) |
| **Learning curve** | 1-2 weeks | 3-5 days | 2-3 days |
| **Fuse mechanism** | `recursion_limit` default **1000** super-steps | Sequential (default) vs hierarchical (manager_llm required) | `max_turns` default **10** (None disables) |
| **Languages** | Python, JS/TS | Python | Python, TypeScript |

### 2.6 Decision Matrix: When to Choose Which

| Scenario | Recommended | Rationale |
|----------|-------------|-----------|
| Regulated industry (finance, healthcare) with audit trails and human approval gates | **LangGraph** | Native HITL, durable checkpointing, LangSmith traces, HIPAA BAA |
| Rapid prototype of multi-agent system for concept validation | **CrewAI** | Working two-agent crew in ~30 lines. Fastest time-to-first-working-agent |
| Simple router + specialists pattern (customer support, triage) | **OpenAI Agents SDK** | Handoff pattern maps directly. Minimal boilerplate. Built-in tracing |
| Complex non-linear workflow with loops, parallel branches, conditional routing | **LangGraph** | Explicit graph topology. Conditional edges. Fan-out/fan-in via `Send` |
| Content operations (research, writing, review pipelines) | **CrewAI** | Role-based design maps naturally to editorial workflows |
| Google Cloud shop with Gemini-first strategy | **Google ADK** | Native Vertex AI deployment. A2A + MCP support |
| Long-running autonomous tasks (hours/days) | **LangGraph** (or OpenAI SDK + Temporal) | Durable execution with checkpoint-based resume |
| Single agent, 1-2 tools | **No framework** | Plain SDK loop with `max_steps` cap. Framework adds friction, not value |

**The hybrid path** (most common enterprise pattern): Prototype in CrewAI (validate concept in days), productionize in LangGraph (durability, control, HITL). This is a feature, not indecision.

---

## 3. Architecture & System Design

### 3.1 LangGraph: Full System Topology

```
+---------------------------------------------------------------------------------+
| CLIENTS                                                                         |
|  SSE stream modes | REST invoke | HITL Command(resume=) | /mcp Streamable HTTP |
+------------+--------------------------------------------------------------------+
             | TLS + session JWT + thread_id=UUID (<=255 chars) + correlation-id
             v
+---------------------------------------------------------------------------------+
| CONTROL PLANE  (Pregel -- Agent Server worker / your process, not the GPU)      |
|  Compiled graph loaded once at container start. Server REPLACES your saver.     |
|                                                                                 |
|  +------------+  +------------+  +------------+  +------------+  +-----------+  |
|  | Edge       |->| Policy     |->| PREGEL     |->| DURABILITY |->| THREAD    |  |
|  | auth, SSO  |  | PII redact |  | SCHEDULER  |  | exit|async |  | LEASE     |  |
|  | 25 MB->413 |  | tool RBAC  |  | 1. Plan    |  | |sync      |  | <=1 run / |  |
|  | 409 reject |  | context=   |  | 2. Execute |  | checkpoint |  | thread_id |  |
|  | enqueue /  |  | Runtime    |  | 3. Update  |  | put        |  | N_JOBS=10 |  |
|  | interrupt  |  | NOT state  |  | reducers   |  |            |  | / worker  |  |
|  +------------+  +-----+------+  +-----+------+  +-----+------+  +-----+-----+  |
|                        |               |               |               |        |
|                        v               v               v               v        |
|                 +--------------------------------------------------------------+ |
|                 | GRAPH ORCHESTRATOR  StateGraph -> compiled Pregel             | |
|                 |  +- channels: LastValue / add_messages / operator.add        | |
|                 |  +- Command(update, goto, resume) / Send fan-out             | |
|                 |  +- HITL interrupt() + interrupt_before/after                | |
|                 |  +- recursion_limit (super-steps; default 1000 @ 1.0.6)      | |
|                 |  +- RemainingSteps managed channel (do not initialize)       | |
|                 |  +- subgraphs (checkpointer None | True | False)             | |
|                 |  +- stream mux: values/updates/messages/custom/...           | |
|                 +---------------------------+----------------------------------+ |
|  +------------+  +------------+             |            +------------------+    |
|  | Circuit    |  | Fallback   |<------------+----------->| SIGTERM drain    |    |
|  | breaker    |  | primary -> |                          | finish super-step|    |
|  | LLM != tool|  | secondary  |                          | then checkpoint  |    |
|  | != MCP     |  | -> degraded|                          | RunControl       |    |
|  +------------+  +------------+                          +--------+---------+    |
+--------------------------------------------------------------+---+--------------+
                                                               |
          +------------------------------------+---------------+
          | chat / agent SSE, REST             |
          v                                    v
+----------------------------------+  +--------------------------------------------+
| DATA PLANE  GENERATION           |  | DATA PLANE  EXECUTOR (ToolNode / nodes)    |
| (provider-owned on hosted APIs)  |  | model NEVER holds IAM or Stripe sk         |
|                                  |  |                                            |
|  Tokenizer -> Prefill -> Decode  |  |  ToolNode: AIMessage.tool_calls in parallel|
|  tool JSON / draft / plan list   |  |  InjectedState / InjectedStore / Runtime   |
|  stop: end_turn / tool_use /     |  |  handle_tool_errors: invocation ->         |
|    max_tokens / refusal          |  |  ToolMessage; execution errors re-raise    |
|                                  |  |  remaining_steps < 2 -> wrap-up, no tools  |
+------------+---------------------+  +-----------+--------------------------------+
             |                                    |
             |  untrusted planner (text/tool JSON)|  side effects
             v                                    v
+----------------------------------+  +--------------------------------------------+
| TOOL PROXIES  (MCP / adapters)   |  | PERSISTENCE LAYER                          |
| Zero-Trust wrap around ToolNode  |  |                                            |
| RFC 8707 audience; NO passthrough|  |  +------------------+  +-----------------+ |
| identity = langgraph_auth_user   |  |  | CHECKPOINTER     |  | STORE           | |
| / Runtime.context -- never JSON  |  |  | thread_id PK     |  | BaseStore KV    | |
|  +----------+  +-------------+   |  |  | checkpoint_ns    |  | across threads  | |
|  | Stripe / |  | CRM / MCP   |   |  |  | pending writes   |  | (user,memories) | |
|  | send     |  | tools/call  |---+--+  | durability mode  |  | asearch limit=10| |
|  | HITL     |  | SSRF filter |   |  |  | time-travel      |  | silent overflow | |
|  +----------+  +-------------+   |  |  +------------------+  +-----------------+ |
| /mcp is STATELESS per request    |  |  +------------------+  +-----------------+ |
| memory lives in checkpointer     |  |  | Postgres         |  | Redis (Platform)| |
| HITL on mutating tools           |  |  | checkpoints+store|  | pub/sub+queue   | |
|                                  |  |  | EncryptedSerial. |  | NO user data    | |
+----------------------------------+  |  +------------------+  +-----------------+ |
                                      +--------------------------------------------+
                                                            |
+-----------------------------------------------------------+---------------------+
| TELEMETRY / OBSERVABILITY SINKS                                                  |
|  +-------------+  +-------------+  +-------------+  +------------------------+  |
|  | Audit (WORM)|  | Metrics     |  | Traces      |  | Usage (authoritative   |  |
|  | cid, tenant |  | super-step  |  | gateway->LLM|  | on terminal event)     |  |
|  | thread_id,  |  | p50/p95,    |  | ->ToolNode-> |  | input, cache_read,     |  |
|  | checkpoint  |  | Send width, |  | HITL wait    |  | output, LCU/LSU,       |  |
|  | node, hashed|  | 409 reject, |  | PII stripped |  | total_cost_usd,        |  |
|  | draft, HITL |  | GraphRecurs |  | in prod      |  | langgraph_step         |  |
|  | decision    |  | InvalidUpdt |  |              |  |                        |  |
|  +-------------+  +-------------+  +-------------+  +------------------------+  |
+----------------------------------------------------------------------------------+
```

**Plane separation (do not couple):**

| Plane | Owns | Failure if Coupled |
|-------|------|--------------------|
| **Control (Pregel)** | Super-step schedule, reducers, `Send`, interrupts, thread lease, `recursion_limit` | Model "goto send" trusted; Stripe in same node as `interrupt()` |
| **Generation data** | Sample tool JSON / draft / plan | Executor runs on incomplete JSON |
| **Executor data (ToolNode)** | Tools, MCP, `Command` from tools | Silent empty success -> ReAct-shaped loops |
| **Tool proxies** | Audience-bound MCP / Stripe | Token passthrough; `/mcp` treated as session store |
| **Persistence** | Checkpointer (thread_id) + Store (cross-thread) | Pod kill mid-HITL; secrets in snapshots |
| **Telemetry** | Super-step audit, LCU, HITL | Finance dashboards that ignore 1000-tick runaways |

### 3.2 CrewAI: Flows / Crews / Agents / Tasks Topology

```
+------------------------------------------------------------------------------+
|                           ORCHESTRATION LAYER (Flows)                        |
|                                                                              |
|  +------------------------------------------------------------------------+ |
|  |  Flow                                                                  | |
|  |  - Top-level event-driven orchestration (@start, @listen, @router)     | |
|  |  - Routes data between steps (Crews or plain functions)                | |
|  |  - Conditional branching on step outputs                               | |
|  +--------+---------------------------------+-----------------------------+ |
|           |                                 |                               |
|           v                                 v                               |
|  +------------------+             +------------------+    CREW LAYER        |
|  |  Crew A           |             |  Crew B           |                    |
|  |  (Research)       |             |  (Writing)        |                    |
|  |  Process:         |             |  Process:         |                    |
|  |   sequential      |             |   hierarchical    |                    |
|  |                   |             |   (manager LLM)   |                    |
|  |  +------------+   |             |  +------------+   |                    |
|  |  | Agent 1    |   |             |  | Agent 3    |   |                    |
|  |  | role:      |   |             |  | role:      |   |                    |
|  |  | "Analyst"  |   |             |  | "Editor"   |   |                    |
|  |  | tools:[T1] |   |             |  | tools: []  |   |                    |
|  |  +------------+   |             |  +------------+   |                    |
|  |  +------------+   |             |  +------------+   |                    |
|  |  | Agent 2    |   |             |  | Agent 4    |   |                    |
|  |  | role:      |   |             |  | role:      |   |                    |
|  |  | "Gatherer" |   |             |  | "Writer"   |   |                    |
|  |  +------------+   |             |  +------------+   |                    |
|  |                   |             |                   |                    |
|  |  +------------+   |             |  +------------+   |    TASK LAYER     |
|  |  | Task 1     |---+--output---->  | Task 3     |   |                    |
|  |  | agent: A1  |   |   feeds     |  | agent: A3  |   |                    |
|  |  | expected:  |   |   into      |  | expected:  |   |                    |
|  |  |  JSON      |   |             |  |  markdown  |   |                    |
|  |  +------------+   |             |  +------------+   |                    |
|  +-------------------+             +-------------------+                    |
|                                                                              |
| PERSISTENCE: Ephemeral by default (LanceDB/SQLite, lost on container        |
| restart). Fix: Mem0 (Cloud or self-hosted w/ Qdrant/pgvector).              |
| Memory scoped to single Crew only. No cross-Crew federation.               |
|                                                                              |
| TELEMETRY: Console output only (open-source). Enterprise AMP adds           |
| immutable audit trails, runtime hooks for PII redaction.                    |
+------------------------------------------------------------------------------+
```

### 3.3 OpenAI Agents SDK: Handoff-Based Topology

```
+------------------------------------------------------------------------------+
|                           CONTROL PLANE                                      |
|                                                                              |
|  +------------------+   +-------------------+   +------------------------+   |
|  |  Runner           |   |  Guardrails        |   |  Session Manager      |   |
|  |  - Executes agent |   |  - Input guards    |   |  - SQLite, Redis,     |   |
|  |    loop           |   |  - Output guards   |   |    SQLAlchemy         |   |
|  |  - Call model     |   |  - Tripwire except.|   |  - Cross-run convo    |   |
|  |  - Run tools      |   |  - Run alongside   |   |    persistence        |   |
|  |  - Handle handoffs|   |    agent (async)    |   |  - Thread-scoped      |   |
|  |  - Uses Responses |   |  - Input: 1st agent|   |                       |   |
|  |    API by default |   |  - Output: last only|   |                       |   |
|  +--------+----------+   +-------------------+   +------------------------+   |
|           |                                                                   |
|           v              DATA PLANE                                           |
|                                                                              |
|  +-------------+  handoff   +-------------+  handoff   +-------------+       |
|  |  Triage      |---------->|  Specialist  |---------->|  Specialist  |       |
|  |  Agent       |           |  Agent A     |           |  Agent B     |       |
|  |  instruc-    |           |  instruc-    |           |  instruc-    |       |
|  |  tions:      |           |  tions:      |           |  tions:      |       |
|  |  "Route to   |           |  "Handle     |           |  "Handle     |       |
|  |   correct    |           |   billing"   |           |   tech       |       |
|  |   agent"     |           |  tools:      |           |   support"   |       |
|  |  handoffs:   |           |  [lookup_    |           |  tools:      |       |
|  |  [A, B]      |           |   invoice]   |           |  [search_kb] |       |
|  +-------------+           +-------------+           +-------------+       |
|                                                                              |
|  Handoff = tool call (transfer_to_X). Runner switches active agent,         |
|  passes full conversation history. New agent fully takes over.              |
|                                                                              |
| PERSISTENCE: Session backends for conversation state only. No workflow-     |
| level checkpointing. Long-horizon harness (Apr 2026) adds cross-turn       |
| state for tasks spanning hours/days.                                        |
|                                                                              |
| TELEMETRY: Built-in tracing. Visual DAGs. OpenTelemetry export.             |
| trace_include_sensitive_data flag.                                          |
| SANDBOX: Native (Apr 2026) -- E2B/Modal/Daytona for untrusted code.        |
+------------------------------------------------------------------------------+
```

### 3.4 End-to-End Request Flow (LangGraph)

1. **Ingress.** SSE or REST `invoke`. Gateway stamps `correlation_id`, binds `thread_id` = UUID (docs <255 chars; btree composite ~2704 bytes -- do not concatenate user+url). Check the per-(vendor, model) breaker and Agent Server lease. Payload >25 MB on Cloud -> 413.
2. **Policy + runtime context.** Detect -> redact PII before the transcript is checkpointed or traced. Tool RBAC maps `(principal, tenant, tool, args_shape)` -> allow / deny / HITL. Bearer tokens go in `context=` / `Runtime.context`, never graph state.
3. **Admit the run.** Agent Server: at most one executing run per `thread_id`. `multitask_strategy`: `enqueue` (FIFO), `reject` -> HTTP 409, `interrupt` (cancel current), `rollback`.
4. **Pregel plan.** Select actors that subscribe to channels updated last step.
5. **Execute.** Run selected actors in parallel. Channel updates invisible to siblings until the next super-step.
6. **Update + checkpoint.** Apply writes through each channel's reducer. Checkpointer `put`/`put_writes` at the super-step boundary. Pending writes of successful siblings are kept if one parallel task fails.
7. **Route.** Static `add_edge`, conditional router, or `Command(goto=...)`. Fan-out via `Send`.
8. **HITL.** `interrupt(value)` -- graph waits indefinitely. Resume: `Command(resume=...)` becomes the return value of `interrupt()`. The whole node restarts.
9. **Stream.** `messages` mode is TTFT; `values` waits for the whole node.
10. **Store vs checkpoint.** Thread continuity -> checkpointer. Cross-thread data -> Store.
11. **Halt.** No actors selected, or `GraphRecursionError` at `recursion_limit`, or `RemainingSteps` router jumps to END.
12. **Drain.** `RunControl.request_drain()` finishes the current super-step, writes a resumable checkpoint.

---

## 4. Key Algorithms & Mechanics

### 4.1 Pregel Super-Step Complexity

Let L = critical sequential path length, W = `Send` width, M = LLM call count, T = thread length in messages.

- **Time (control plane):** O(L) super-steps. Fan-out of W workers is O(1) tick, not O(W). Classic ReAct ~2 super-steps per tool round (model, then tools).
- **Checkpoints:** O(L) snapshots on a linear path; parallel workers share one snapshot plus pending writes per task.
- **Serialization (no DeltaChannel):** each snapshot re-writes the full `messages` channel -> O(T * L) bytes. DeltaChannel stores the step's writes; read replay bounded by `snapshot_frequency=K`.
- **Token bill:** O(M), independent of super-step count when workers share a tick. `Send` multiplies dollars by W and does not multiply ticks.
- **`recursion_limit`:** a hard cap on L, not on W. A 2-node cycle can burn 1000 ticks quickly; a 30-node DAG cannot.

### 4.2 Invariants (LangGraph)

1. The model never executes tools, edges, interrupts, or checkpoints.
2. Agent Server: <=1 executing run per `thread_id`. OSS in-process has no distributed lease.
3. No `thread_id` -> no save, no `interrupt()` resume.
4. Parallel `LastValue` writes in one super-step -> `InvalidUpdateError` (need a reducer).
5. `Command.goto` does not suppress `add_edge` -- both fire.
6. `interrupt()` resume restarts the whole node; code before the call is at-least-once.
7. `recursion_limit` is a top-level invoke key and counts super-steps (default 1000 since 1.0.6).
8. `Send` width is data-dependent and uncapped by `recursion_limit` -- cap in the router.
9. Secrets live in `Runtime.context` / `UntrackedValue`, never in checkpointed state.
10. Pending writes skip successful sibling tasks on resume; the failed node restarts from the top.
11. Redis on Agent Server holds no user data. Checkpointer vs Store: thread vs cross-thread.
12. Time-travel re-fires interrupts -- an auditor replay is a new approval.

### 4.3 CrewAI Task Delegation Algorithm

**Sequential mode:** Linear pipeline. Task N output -> Task N+1 context. Deterministic ordering. Each agent internally runs a ReAct loop until `expected_output` format is satisfied or `max_iterations` is hit.

**Hierarchical mode:** Manager agent receives all task descriptions + agent capabilities. Manager makes an LLM call to decide: (a) which agent handles each task, (b) whether to re-delegate if output quality is insufficient. This adds an extra LLM call per delegation decision.

**Real-world example:** A content pipeline with Researcher, Writer, and Editor agents. In sequential mode, the Researcher always runs first, then Writer, then Editor -- predictable but inflexible. In hierarchical mode, the manager might skip the Researcher if the task already has sufficient context, or re-delegate to the Writer if the Editor finds quality issues. But the manager sometimes runs tasks in sequence anyway (GitHub #4783).

### 4.4 OpenAI Agents SDK Handoff Algorithm

1. Runner sends conversation + instructions + tool definitions to the model.
2. Model returns either a final response OR a tool call.
3. If the tool call is `transfer_to_X`: Runner switches active agent, passes full conversation history. New agent fully takes over (no return to orchestrator).
4. If the tool call is a regular tool: Runner executes it, feeds result back to the model for the next turn.
5. `max_turns` (default 10) caps the total model invocations. When `None`, the loop is unbounded.
6. Output guardrails run only on the final agent's response. Input guardrails run only on the first agent's input.

**Key difference from LangGraph:** Handoffs are flat. Agent A hands off to Agent B, which hands off to Agent C. There is no fan-out, no parallel execution, no join. Each handoff is a full context transfer. Compounding step failure: 85% per-step success x 8 steps = 27% end-to-end.

### 4.5 Checkpointer vs Store vs Session (Cross-Framework)

| Capability | LangGraph Checkpointer | LangGraph Store | CrewAI Memory | OpenAI Sessions |
|-----------|------------------------|-----------------|---------------|-----------------|
| **Scope** | One `thread_id` | Across threads | Single Crew | Conversation thread |
| **Persists** | Full graph snapshots | Application KV | Task results, observations | Conversation history |
| **Crash recovery** | Automatic resume from checkpoint | N/A (not execution state) | None (ephemeral default) | None (conversation only) |
| **Time-travel** | Yes (rewind, inspect, replay) | No | No | No |
| **HITL support** | Native (interrupt/resume) | No | Not built-in | Not built-in |
| **Cross-process** | PostgresSaver: any worker can resume | Shared via Postgres | Mem0 for external | Redis sessions |

---

## 5. Token Economics & Cost Analysis

### 5.1 Per-Call Cost Formula

Per LLM call (standard, short context):

```
C_call = (input_tokens * P_in + output_tokens * P_out) / 1,000,000
```

For a skeleton of **3,000 input + 800 output** tokens per LLM call:

| Model | P_in / P_out (per 1M) | C_call |
|-------|----------------------|--------|
| GPT-4.1 | $2 / $8 | $0.0124 |
| GPT-6 Sol | $2 / $10 | $0.0140 |
| GPT-6 Luna | $0.10 / $0.50 | $0.0007 |
| GPT-5.4 | $2.50 / $15 | $0.0195 |
| GPT-4o | $2.50 / $10 | $0.0155 |

### 5.2 Cost per 1k Completed Graph Runs

**Scenario A: Ticket workflow (6 nodes, 3 LLM calls)** -- classify, draft, post-HITL send.

| Model | $ / execution [inferred] | $ / 1k [inferred] |
|-------|-------------------------|-------------------|
| GPT-4.1 | $0.0372 | **$37.20** |
| GPT-6 Sol | $0.0420 | **$42.00** |
| GPT-6 Luna | $0.0021 | **$2.10** |
| GPT-5.4 | $0.0585 | **$58.50** |

**Scenario B: Send research fan-out (N=8 workers + planner + synthesizer = 10 LLM calls).**

| Model | $ / execution [inferred] | $ / 1k [inferred] |
|-------|-------------------------|-------------------|
| GPT-4.1 | $0.124 | **$124** |
| GPT-6 Luna | $0.007 | **$7** |
| GPT-5.4 | $0.195 | **$195** |

Fan-out **multiplies dollars by N** and does **not** multiply super-step count.

### 5.3 Cross-Framework Cost Comparison (3-Agent Sequential Workflow, GPT-4o)

| Component | LangGraph | CrewAI | OpenAI Agents SDK |
|-----------|-----------|--------|-------------------|
| System prompts | 3 x 500 = 1,500 in | 3 x 500 = 1,500 in | 300 in (triage) + 800 in (specialist) |
| Framework overhead | ~530 in (routing) | ~2,100 in (manager, hierarchical) + ~900 (role/backstory) | ~0 (handoff is a tool call) |
| Agent reasoning | 3 x 800 = 2,400 out | 3 x 800 = 2,400 out | ~100 out (triage) + ~800 out (specialist) |
| **Total tokens** | ~2,030 in + 2,400 out | ~4,500 in + 2,400 out | ~1,100 in + 900 out |
| **Cost / run** | **~$0.029** | **~$0.035** | **~$0.012** |

**Key insight:** LangGraph achieves 47% lower token costs than CrewAI due to explicit edge transitions instead of LLM-driven task routing. In hierarchical mode, CrewAI's manager agent consumes ~30% of total token budget just for coordination. OpenAI SDK is cheapest when the handoff pattern naturally skips unnecessary agents.

**At scale (1k runs/month, GPT-4o):**

| Framework | Model Cost | Platform Cost | Total |
|-----------|-----------|---------------|-------|
| LangGraph | ~$29 | $0 (self-hosted) or $39 (managed) | $29-$68 |
| CrewAI | ~$35 | $0 (self-hosted) or ~$2,000 (Enterprise AMP) | $35-$2,035 |
| OpenAI SDK | ~$12 | $0 | $12 |

### 5.4 LangGraph Platform SKUs (Published)

[langchain.com/pricing](https://www.langchain.com/pricing) (fetched 2026-09-23):

| Tier | Price | Includes |
|------|-------|----------|
| Developer | $0/seat | 5k base traces/mo, 1 seat |
| Plus | $39/seat/mo | 10k traces, Deployment + Engine, 1 free Serverless Small |
| LCU (compute unit) | $1.50 | Runtime: 0.045 LCU/vCPU-hr -> **$0.0675/vCPU-hr** |
| LSU (storage unit) | $1.00 | Database: 0.177 LSU/vCPU-hr |

**Agent Server compute per 1k runs [inferred]:** At 2 vCPU-seconds per ticket execution: 1000 x 2/3600 x 0.045 x 1.50 = ~$0.038. **Tokens dominate.**

**Dedicated Small always-on [inferred]:** ~$0.534/h = **~$390/mo** if 24x7. At 1k runs/mo that is ~$390/1k platform; at 100k runs/mo ~$3.90/1k.

Existing customers stay on old per-run/uptime pricing until **2026-10-01**, then move to LCU/LSU. Scale-to-zero is new pricing only (beta).

**Framework licensing comparison:**

| Component | LangGraph | CrewAI | OpenAI Agents SDK |
|-----------|-----------|--------|-------------------|
| Core library | Free (MIT) | Free (open-source) | Free (open-source) |
| Managed platform | $39/user/month | Enterprise AMP ~$2,000/month | N/A (use OpenAI API directly) |
| Self-hosted | Infra only (compute + Postgres) | Infra only | Infra only |

### 5.5 Latency Analysis

**Framework orchestration overhead** (AIMultiple benchmark, 100 queries x 100 runs, identical models/tools):

| Framework | Orchestration Latency | Token Overhead/Query |
|-----------|----------------------|---------------------|
| DSPy | ~3.5ms | ~2,030 tokens |
| Haystack | ~5.9ms | ~1,570 tokens |
| LlamaIndex | ~6.0ms | ~1,600 tokens |
| LangChain | ~10ms | ~2,400 tokens |
| LangGraph | ~14ms | ~2,030 tokens |

Framework overhead is dwarfed by LLM inference time (1-3 seconds per call). The framework choice does not materially impact end-to-end latency.

**Multi-agent benchmark:** LangGraph measured at ~10,155ms in multi-agent benchmarks (dominated by LLM calls). A Rust-based alternative (AutoAgents) beat LangGraph by 43.7% on pure framework latency -- but negligible when LLM inference is 99% of wall-clock time.

**Streaming:** CrewAI executes tasks 5.76x faster than LangGraph in simple QA scenarios (JetThoughts benchmark), but LangGraph achieves **62% success rate** vs CrewAI's **54%** on complex tasks requiring deep reasoning.

**[Inferred] policy targets (not vendor guarantees):**

| Metric | Target | Mitigation |
|--------|--------|------------|
| p50 ticket wall (pre-HITL) | 3 LLM sequential; wall <8s | Stream `messages`; `durability="async"` |
| p95 Send research | planner + max(worker) + synth; wall <20s | Cap `Send` width at 8; per-`Send` timeout |
| p99 hang | Fail closed on timeout / 409-storm | `TimeoutPolicy` on async nodes; `RemainingSteps` wrap-up |

### 5.6 Framework Maturity Indicators (September 2026)

| Metric | LangGraph | CrewAI | OpenAI Agents SDK |
|--------|-----------|--------|-------------------|
| GitHub stars | ~42K | ~58K | ~29K |
| Monthly PyPI downloads | 38.8M | 27M+ | Not reported |
| GA version | 1.0 (Oct 2025) | 1.10.x | 0.2.x (Apr 2026) |
| Enterprise adopters | Klarna, Replit, Elastic, Lyft, Uber, Coinbase, NVIDIA | 450M monthly workflows, 2B agent runs/year | Growing, primarily OpenAI ecosystem |
| Learning curve | 1-2 weeks | 3-5 days | 2-3 days |

**Reading the signals:** CrewAI has the most stars (community enthusiasm) but LangGraph has the most PyPI downloads (production adoption). Stars measure hype; downloads measure integration into CI pipelines.

### 5.7 Throughput and Back-Pressure

| Knob | Value | Effect |
|------|-------|--------|
| `recursion_limit` | Default 1000 since 1.0.6 | Cap spend; `GraphRecursionError` |
| `RemainingSteps` bail | Docs example <= 2 | Graceful END vs exception |
| `RetryPolicy` | max_attempts=3, 0.5s x 2.0 backoff, cap 128s | Extra model/tool $ on 5xx |
| `N_JOBS_PER_WORKER` | Default 10 | Bounds concurrent runs, not HTTP |
| 1 run / thread | Enforced on Agent Server | Same-thread double-text: `enqueue`/`reject` (409) |
| `Send` width | Data-dependent | $ x N; latency ~ max worker |
| `durability` | exit/async/sync | Write on critical path only for sync |
| Payload | Cloud 25 MB | 413 |

**Back-pressure design:**
1. Admit iff LLM breaker is closed/half-open, thread has no executing run, and `N_JOBS_PER_WORKER` has room.
2. Shed in order: `reject` (409) on hot threads -> disable parallel writes -> skip extras -> deterministic degraded JSON.
3. Cap `Send` width in the router. Inner `create_agent` `max_turns` so a poisoned worker wastes 3 hops, not 1000 super-steps.

---

## 6. Production Patterns & Code

### 6.1 Same Workflow in All Three Frameworks: Customer Support

The same workflow implemented in all three frameworks: a customer message is triaged, routed to either a billing specialist or a technical support specialist, and a structured response is returned.

#### LangGraph Implementation

```python
"""
Customer support workflow with conditional routing, HITL, and checkpointing.
Requires: pip install langgraph langgraph-checkpoint-postgres psycopg2-binary
"""
from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import InMemorySaver


# --- State schema: Annotated fields prevent lost-message bugs ---

class SupportState(TypedDict):
    messages: Annotated[list[dict], operator.add]  # reducer = append, not overwrite
    category: str  # "billing" | "technical" | "unknown"
    resolution: str
    customer_id: str


# --- Tools ---

def lookup_invoice(customer_id: str, invoice_id: str) -> dict:
    """Look up invoice details. Production: call billing API."""
    return {"invoice_id": invoice_id, "amount": 149.99, "status": "paid", "date": "2026-09-15"}


def search_knowledge_base(query: str) -> str:
    """Search KB. Production: call vector search API."""
    return f"KB result for '{query}': Check firmware >= 3.2.1. If issue persists, escalate to L2."


# --- Node functions (State -> Partial[State]) ---

def triage(state: SupportState) -> dict:
    """Classify into billing or technical. Production: LLM classification call."""
    last_message = state["messages"][-1]["content"].lower()
    if any(w in last_message for w in ["invoice", "charge", "refund", "bill", "payment"]):
        return {"category": "billing"}
    elif any(w in last_message for w in ["error", "bug", "crash", "not working", "broken"]):
        return {"category": "technical"}
    return {"category": "unknown"}


def billing_agent(state: SupportState) -> dict:
    invoice = lookup_invoice(state["customer_id"], "INV-2026-0042")
    resolution = f"Invoice {invoice['invoice_id']}: ${invoice['amount']}, status={invoice['status']}."
    return {"resolution": resolution, "messages": [{"role": "assistant", "content": resolution}]}


def technical_agent(state: SupportState) -> dict:
    kb_result = search_knowledge_base(state["messages"][-1]["content"])
    resolution = f"Technical support: {kb_result}"
    return {"resolution": resolution, "messages": [{"role": "assistant", "content": resolution}]}


def fallback_agent(state: SupportState) -> dict:
    resolution = "I'll connect you with a human agent who can help."
    return {"resolution": resolution, "messages": [{"role": "assistant", "content": resolution}]}


# --- Routing: deterministic, unit-testable code, not a prompt ---

def route_by_category(state: SupportState) -> Literal["billing_agent", "technical_agent", "fallback_agent"]:
    category = state.get("category", "unknown")
    if category == "billing":
        return "billing_agent"
    elif category == "technical":
        return "technical_agent"
    return "fallback_agent"


# --- Graph construction ---

def build_support_graph(checkpointer=None):
    graph = StateGraph(SupportState)
    graph.add_node("triage", triage)
    graph.add_node("billing_agent", billing_agent)
    graph.add_node("technical_agent", technical_agent)
    graph.add_node("fallback_agent", fallback_agent)

    graph.add_edge(START, "triage")
    graph.add_conditional_edges("triage", route_by_category)
    graph.add_edge("billing_agent", END)
    graph.add_edge("technical_agent", END)
    graph.add_edge("fallback_agent", END)

    return graph.compile(checkpointer=checkpointer or InMemorySaver())


if __name__ == "__main__":
    app = build_support_graph()
    result = app.invoke(
        {
            "messages": [{"role": "user", "content": "I was overcharged on my last invoice"}],
            "customer_id": "CUST-12345", "category": "", "resolution": "",
        },
        # recursion_limit is a TOP-LEVEL invoke key, NOT inside configurable
        config={"configurable": {"thread_id": "support-001"}, "recursion_limit": 50},
    )
    print(f"Category: {result['category']}")
    print(f"Resolution: {result['resolution']}")
```

#### CrewAI Implementation

```python
"""
Customer support workflow with role-based agents and task delegation.
Requires: pip install crewai crewai-tools
"""
from crewai import Agent, Crew, Task, Process
from crewai.tools import tool


@tool("Invoice Lookup")
def lookup_invoice(customer_id: str, invoice_id: str) -> str:
    """Look up invoice details for a customer."""
    return f"Invoice {invoice_id}: $149.99, status=paid, date=2026-09-15, customer={customer_id}"


@tool("Knowledge Base Search")
def search_knowledge_base(query: str) -> str:
    """Search the technical support knowledge base."""
    return f"KB result for '{query}': Check firmware >= 3.2.1. Escalate to L2 if persists."


# Agents: role + goal + backstory ground the LLM persona
triage_agent = Agent(
    role="Customer Support Triage Specialist",
    goal="Accurately classify customer inquiries into billing or technical categories.",
    backstory=(
        "You are a senior triage specialist with 10 years of experience. "
        "You quickly identify whether issues relate to billing/payments or technical problems."
    ),
    verbose=False, allow_delegation=False, max_iterations=3,
)

billing_agent = Agent(
    role="Billing Support Specialist",
    goal="Resolve billing inquiries by looking up invoices and providing clear responses.",
    backstory="You handle invoice disputes, refund requests, and payment issues.",
    tools=[lookup_invoice], verbose=False, allow_delegation=False, max_iterations=5,
)

technical_agent = Agent(
    role="Technical Support Engineer",
    goal="Resolve technical issues via knowledge base search and troubleshooting steps.",
    backstory="You search the KB for solutions and provide clear troubleshooting steps.",
    tools=[search_knowledge_base], verbose=False, allow_delegation=False, max_iterations=5,
)


def build_support_crew(customer_message: str, customer_id: str) -> Crew:
    triage_task = Task(
        description=f"Classify: '{customer_message}' from {customer_id}. Reply 'billing' or 'technical'.",
        expected_output="A single word: 'billing' or 'technical'", agent=triage_agent,
    )
    billing_task = Task(
        description=f"Handle billing inquiry from {customer_id}: {customer_message}",
        expected_output="Professional response with invoice details.", agent=billing_agent,
    )
    technical_task = Task(
        description=f"Handle technical request from {customer_id}: {customer_message}",
        expected_output="Step-by-step troubleshooting instructions.", agent=technical_agent,
    )
    # Sequential: all agents run even when lead is rejected.
    # Use a Flow with conditional routing to avoid running the irrelevant specialist.
    return Crew(
        agents=[triage_agent, billing_agent, technical_agent],
        tasks=[triage_task, billing_task, technical_task],
        process=Process.sequential, verbose=False,
    )


if __name__ == "__main__":
    crew = build_support_crew("I was overcharged on my last invoice", "CUST-12345")
    result = crew.kickoff()
    print(f"Result: {result.raw}")
```

#### OpenAI Agents SDK Implementation

```python
"""
Customer support workflow with agent handoffs and guardrails.
Requires: pip install openai-agents
"""
from __future__ import annotations
import re, asyncio
from pydantic import BaseModel
from agents import Agent, GuardrailFunctionOutput, InputGuardrail, Runner, function_tool, handoff


@function_tool
def lookup_invoice(customer_id: str, invoice_id: str) -> str:
    """Look up invoice details for a customer."""
    return f"Invoice {invoice_id}: $149.99, status=paid, date=2026-09-15, customer={customer_id}"


@function_tool
def search_knowledge_base(query: str) -> str:
    """Search the technical support knowledge base."""
    return f"KB result for '{query}': Check firmware >= 3.2.1. Escalate to L2 if persists."


class SupportResponse(BaseModel):
    category: str
    resolution: str
    escalation_needed: bool


async def check_pii_in_input(ctx, agent, input_text: str) -> GuardrailFunctionOutput:
    """Block requests containing sensitive PII patterns."""
    pii_patterns = [r"\b\d{3}-\d{2}-\d{4}\b", r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"]
    for pattern in pii_patterns:
        if re.search(pattern, input_text):
            return GuardrailFunctionOutput(
                output_info={"reason": "PII detected"}, tripwire_triggered=True,
            )
    return GuardrailFunctionOutput(output_info={"reason": "No PII"}, tripwire_triggered=False)


# Handoff pattern: triage routes to specialist. No unnecessary agents run.
billing_agent = Agent(
    name="Billing Specialist",
    instructions="Look up the customer's invoice and resolve billing inquiries.",
    tools=[lookup_invoice], output_type=SupportResponse,
)

technical_agent = Agent(
    name="Technical Support",
    instructions="Search the KB for solutions. Set escalation_needed=true if L2 needed.",
    tools=[search_knowledge_base], output_type=SupportResponse,
)

triage_agent = Agent(
    name="Support Triage",
    instructions=(
        "Analyze the message and hand off immediately:\n"
        "- Billing issues -> Billing Specialist\n"
        "- Technical issues -> Technical Support\n"
        "Do NOT resolve yourself."
    ),
    handoffs=[handoff(agent=billing_agent), handoff(agent=technical_agent)],
    input_guardrails=[InputGuardrail(guardrail_function=check_pii_in_input)],
)


async def handle_support_request(message: str) -> SupportResponse:
    result = await Runner.run(triage_agent, input=message)
    return result.final_output


if __name__ == "__main__":
    response = asyncio.run(handle_support_request("I was overcharged on my last invoice"))
    print(f"Category: {response.category}")
    print(f"Resolution: {response.resolution}")
```

### 6.2 Production LangGraph Control Plane (HITL + Send + Circuit Breaker)

This is a comprehensive production-grade implementation featuring retry with jitter, circuit breakers, zero-trust tool proxies, ticket HITL workflow, Send fan-out research, and error-mode demonstrations.

```python
#!/usr/bin/env python3
"""LangGraph production control plane. Python 3.11+.
  python langgraph_runtime.py
Offline self-test uses MemorySaver (no network, no LLM). PostgresSaver is a
complete helper gated on a DSN so this file runs without Postgres.
"""
from __future__ import annotations

import asyncio, hashlib, json, logging, operator, os, random, time, uuid
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Annotated, Any, Literal, TypedDict, TypeVar

from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError, InvalidUpdateError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, Send, interrupt

INITIAL_RETRY_DELAY = 0.5
MAX_RETRY_DELAY = 8.0
SDK_DEFAULT_MAX_RETRIES = 2
DEFAULT_RECURSION_LIMIT = 50
SEND_WIDTH_CAP = 8


# ---- Structured JSON Logging ----

class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname, "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "thread_id": getattr(record, "thread_id", None),
            "node": getattr(record, "node", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class CorrelationAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs


def build_logger(correlation_id, tenant, thread_id=None, node=None):
    base = logging.getLogger("langgraph.runtime")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    extra = {"correlation_id": correlation_id, "tenant": tenant}
    if thread_id: extra["thread_id"] = thread_id
    if node: extra["node"] = node
    return CorrelationAdapter(base, extra)


# ---- Error Hierarchy ----

class TransientError(Exception):
    def __init__(self, msg, retry_after=None, status=None):
        super().__init__(msg)
        self.retry_after = retry_after
        self.status = status

class PermanentError(Exception):
    pass

class CircuitOpenError(TransientError):
    pass


# ---- Circuit Breaker (one per provider/model or tool-class) ----

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class BreakerStateMachine:
    """Do not trip on 429-with-Retry-After."""
    def __init__(self, name, failure_threshold=5, recovery_seconds=30.0, half_open_max=1):
        self.name, self.failure_threshold = name, failure_threshold
        self.recovery_seconds, self.half_open_max = recovery_seconds, half_open_max
        self._state, self._failures = BreakerState.CLOSED, 0
        self._opened_at, self._half_open_inflight = 0.0, 0
        self._lock = asyncio.Lock()

    async def allow(self):
        async with self._lock:
            self._maybe_half_open()
            if self._state is BreakerState.OPEN:
                raise CircuitOpenError(f"circuit_open:{self.name}")
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_inflight >= self.half_open_max:
                    raise CircuitOpenError(f"circuit_half_open_busy:{self.name}")
                self._half_open_inflight += 1

    def _maybe_half_open(self):
        if self._state is BreakerState.OPEN and (time.monotonic() - self._opened_at) >= self.recovery_seconds:
            self._state, self._half_open_inflight = BreakerState.HALF_OPEN, 0

    async def record_success(self):
        async with self._lock:
            self._failures, self._half_open_inflight = 0, 0
            self._state = BreakerState.CLOSED

    async def record_failure(self, *, trip=True):
        async with self._lock:
            if not trip: return
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state, self._opened_at = BreakerState.OPEN, time.monotonic()
                self._half_open_inflight = 0

    @property
    def state(self): return self._state


# ---- Retry with Full Jitter (HTTP/transport only) ----

T = TypeVar("T")

async def retry_with_jitter(fn, *, log, attempts=SDK_DEFAULT_MAX_RETRIES + 1,
                            base=INITIAL_RETRY_DELAY, cap=MAX_RETRY_DELAY):
    """Never wrap GraphRecursionError or InvalidUpdateError."""
    last = None
    for i in range(attempts):
        try:
            return await fn()
        except PermanentError:
            raise
        except TransientError as exc:
            last = exc
            if i == attempts - 1: break
            ra = exc.retry_after
            sleep_s = ra if ra is not None and 0 < ra <= 60 else random.random() * min(cap, base * (2**i))
            log.warning("http_retry attempt=%s sleep_s=%.3f err=%s", i + 1, sleep_s, exc)
            await asyncio.sleep(sleep_s)
    raise last


def deterministic_degraded(ticket_id):
    return {"status": "degraded", "ticket_id": ticket_id, "answer": None}


class FallbackChain:
    """Primary -> secondary -> deterministic degraded. PermanentError does not failover."""
    def __init__(self, primary, secondary, breaker):
        self.primary, self.secondary, self.breaker = primary, secondary, breaker

    async def invoke(self, log):
        try:
            await self.breaker.allow()
            result = await retry_with_jitter(self.primary, log=log)
            await self.breaker.record_success()
            return result
        except CircuitOpenError as exc:
            log.warning("llm_breaker_open err=%s", exc)
        except TransientError as exc:
            await self.breaker.record_failure(trip=True)
            log.warning("llm_primary_transient err=%s", exc)
        except PermanentError as exc:
            await self.breaker.record_failure(trip=False)
            log.error("llm_permanent_no_failover err=%s", exc)
            raise
        try:
            return await retry_with_jitter(self.secondary, log=log)
        except (TransientError, PermanentError) as exc:
            log.error("degraded_deterministic err=%s", exc)
            raise PermanentError("degraded") from exc


# ---- Zero-Trust Tool Proxy ----

class ZeroTrustToolProxy:
    """Identity never comes from model JSON. Wraps ToolNode-equivalent side effects."""
    def __init__(self, allowed: frozenset[str], irreversible: frozenset[str]):
        self.allowed, self.irreversible = allowed, irreversible
        self.calls: list[tuple[str, dict]] = []

    def execute(self, *, principal, tool, args, hitl_approved):
        if not principal: raise PermanentError("missing_principal")
        if tool not in self.allowed: raise PermanentError(f"rbac_deny:{tool}")
        if tool in self.irreversible and not hitl_approved: raise PermanentError(f"hitl_required:{tool}")
        self.calls.append((tool, dict(args)))
        return f"ok:{tool}:{json.dumps(args, sort_keys=True)}"


# ---- PostgresSaver Helper (import-gated) ----

def postgres_saver(dsn: str):
    """Production checkpointer. autocommit=True, dict_row, .setup() once.
    thread_id must be UUID (<255 chars; btree composite <=2704 bytes).
    Optional: EncryptedSerializer + LANGGRAPH_AES_KEY."""
    from psycopg import Connection
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres import PostgresSaver
    conn = Connection.connect(dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row)
    saver = PostgresSaver(conn)
    saver.setup()
    return saver


# ---- Ticket HITL Workflow (interrupt on SEPARATE node from send) ----

class TicketState(TypedDict):
    messages: Annotated[list, add_messages]
    ticket_id: str
    draft: str
    decision: str
    sent: int


def classify_ticket(state: TicketState) -> dict:
    return {"messages": [{"role": "assistant", "content": "intent:billing"}]}

def retrieve_policy(state: TicketState) -> dict:
    return {"messages": [{"role": "assistant", "content": "kb:refund-window-30d"}]}

def draft_reply(state: TicketState) -> dict:
    return {
        "draft": f"ticket {state['ticket_id']}: approved-template",
        "messages": [{"role": "assistant", "content": "drafted"}],
    }

def review_hitl(state: TicketState) -> Command[Literal["send_email", "draft"]]:
    # interrupt() on a SEPARATE node from send_email prevents double-send on resume
    payload = interrupt({"kind": "review", "draft": state["draft"], "ticket_id": state["ticket_id"]})
    decision = payload["decision"] if isinstance(payload, dict) else str(payload)
    if decision == "edit":
        edits = payload.get("edits", state["draft"]) if isinstance(payload, dict) else state["draft"]
        return Command(update={"decision": "edit", "draft": edits}, goto="draft")
    if decision == "reject":
        return Command(update={"decision": "reject"}, goto=END)
    return Command(update={"decision": "approve"}, goto="send_email")

def send_email(state: TicketState) -> dict:
    if state.get("decision") != "approve":
        raise PermanentError("send_without_approve")
    digest = hashlib.sha256(state["draft"].encode()).hexdigest()[:16]
    return {
        "sent": state.get("sent", 0) + 1,
        "messages": [{"role": "assistant", "content": f"sent:{state['ticket_id']}:{digest}"}],
    }


def build_ticket_graph(checkpointer=None):
    builder = StateGraph(TicketState)
    builder.add_node("classify", classify_ticket)
    builder.add_node("retrieve", retrieve_policy)
    builder.add_node("draft", draft_reply)
    builder.add_node("review", review_hitl)
    builder.add_node("send_email", send_email)
    builder.add_edge(START, "classify")
    builder.add_edge("classify", "retrieve")
    builder.add_edge("retrieve", "draft")
    builder.add_edge("draft", "review")
    # NO add_edge(review, send_email) -- only Command.goto. Prevents #5829 double-fire.
    builder.add_edge("send_email", END)
    return builder.compile(checkpointer=checkpointer or MemorySaver())


# ---- Send Fan-Out Research (width capped at 8) ----

class ResearchState(TypedDict):
    subjects: list[str]
    findings: Annotated[list[str], operator.add]  # reducer: parallel writes merge
    report: str

class WorkerState(TypedDict):
    q: str

def plan_research(state: ResearchState) -> dict:
    seed = state.get("subjects") or ["alpha", "beta", "gamma"]
    return {"subjects": seed[:SEND_WIDTH_CAP]}

def fanout_research(state: ResearchState) -> list[Send]:
    subjects = state["subjects"][:SEND_WIDTH_CAP]  # Cap width in the router
    return [Send("research", {"q": s}) for s in subjects]

def research_worker(state: WorkerState) -> dict:
    return {"findings": [f"finding:{state['q']}"]}

def synthesize(state: ResearchState) -> dict:
    return {"report": "|".join(sorted(state["findings"]))}

def build_research_graph(checkpointer=None):
    builder = StateGraph(ResearchState)
    builder.add_node("plan", plan_research)
    builder.add_node("research", research_worker)
    builder.add_node("synthesize", synthesize)
    builder.add_edge(START, "plan")
    builder.add_conditional_edges("plan", fanout_research)
    builder.add_edge("research", "synthesize")
    builder.add_edge("synthesize", END)
    return builder.compile(checkpointer=checkpointer or MemorySaver())


# ---- Error Demonstrations ----

class ScalarState(TypedDict):
    foo: str

def build_clobber_graph():
    """Demonstrates InvalidUpdateError: parallel LastValue writes with no reducer."""
    builder = StateGraph(ScalarState)
    builder.add_node("split", lambda s: {})
    builder.add_node("worker", lambda s: {"foo": s["foo"]})
    builder.add_edge(START, "split")
    builder.add_conditional_edges("split", lambda _: [Send("worker", {"foo": "a"}), Send("worker", {"foo": "b"})])
    builder.add_edge("worker", END)
    return builder.compile()

def build_cycle_graph():
    """Demonstrates GraphRecursionError: unbounded cycle hits recursion_limit."""
    class CycleState(TypedDict):
        n: int
    builder = StateGraph(CycleState)
    builder.add_node("tick", lambda s: {"n": s["n"] + 1})
    builder.add_edge(START, "tick")
    builder.add_conditional_edges("tick", lambda s: "tick")
    return builder.compile()


# ---- Offline Self-Test ----

def _offline():
    """Runs all patterns with MemorySaver, no network, no LLM."""
    cid, tenant = str(uuid.uuid4()), "acme"
    log = build_logger(cid, tenant, thread_id="acme:ticket-1")

    # Ticket HITL: interrupt -> approve -> send once
    saver = MemorySaver()
    ticket = build_ticket_graph(saver)
    thread = str(uuid.uuid4())
    cfg = {"configurable": {"thread_id": thread}, "recursion_limit": DEFAULT_RECURSION_LIMIT}
    paused = ticket.invoke(
        {"ticket_id": "T-1001", "messages": [{"role": "user", "content": "refund please"}],
         "draft": "", "decision": "", "sent": 0}, cfg)
    assert paused["sent"] == 0 and paused["__interrupt__"]
    resumed = ticket.invoke(Command(resume={"decision": "approve"}), cfg)
    assert resumed["sent"] == 1 and resumed["decision"] == "approve"

    # Ticket HITL: reject -> never sends
    reject_thread = str(uuid.uuid4())
    reject_cfg = {"configurable": {"thread_id": reject_thread}, "recursion_limit": DEFAULT_RECURSION_LIMIT}
    ticket.invoke({"ticket_id": "T-1002", "messages": [{"role": "user", "content": "no"}],
                   "draft": "", "decision": "", "sent": 0}, reject_cfg)
    rejected = ticket.invoke(Command(resume={"decision": "reject"}), reject_cfg)
    assert rejected["decision"] == "reject" and rejected["sent"] == 0

    # Send fan-out: parallel workers with operator.add reducer
    research = build_research_graph(MemorySaver())
    fan = research.invoke(
        {"subjects": ["alpha", "beta", "gamma"], "findings": [], "report": ""},
        {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": DEFAULT_RECURSION_LIMIT})
    assert sorted(fan["findings"]) == ["finding:alpha", "finding:beta", "finding:gamma"]

    # Error modes
    try:
        build_clobber_graph().invoke({"foo": ""})
        raise AssertionError("expected InvalidUpdateError")
    except InvalidUpdateError:
        pass

    try:
        build_cycle_graph().invoke({"n": 0}, {"recursion_limit": 8})
        raise AssertionError("expected GraphRecursionError")
    except GraphRecursionError:
        pass

    print(json.dumps({"ok": True, "ticket_sent": resumed["sent"], "reject_sent": rejected["sent"],
                       "findings": sorted(fan["findings"])}, indent=2))


if __name__ == "__main__":
    _offline()
```

**Key behaviors encoded:**
- Full-jitter HTTP retries; `Retry-After` honored iff 0 < t <= 60; `GraphRecursionError`/`InvalidUpdateError`/RBAC are never retried.
- Breaker: closed -> open -> half-open. 429-with-Retry-After does not trip. Fallback: primary -> secondary -> deterministic degraded. PermanentError does not failover.
- TypedDict `TicketState` with `add_messages`. No static edge from `review` to `send_email` (only `Command.goto`) preventing #5829 double-fire.
- HITL `interrupt()` on `review`; resume sends once; reject never sends. Draft and send are different nodes.
- `Send` fan-out with `operator.add` on `findings`; width capped at 8.
- Missing reducer -> `InvalidUpdateError`. Cycle + `recursion_limit=8` -> `GraphRecursionError`.
- `ZeroTrustToolProxy`: principal required, allowlist, HITL on `send_email`.

### 6.3 Migration Patterns

**CrewAI to LangGraph** (most common migration path):

| CrewAI Concept | LangGraph Equivalent |
|----------------|---------------------|
| Agent (role + goal + backstory) | Node function (with system prompt containing role/goal) |
| Task | Node function's logic + expected output validation |
| Crew (sequential) | Linear edge chain: A -> B -> C -> END |
| Crew (hierarchical) | Conditional edges from a router node |
| Flow | Parent StateGraph with subgraph nodes |
| Memory (short-term) | State fields with append reducers |
| Memory (long-term) | Store (cross-thread) |
| `allow_delegation=True` | Conditional edge routing based on state |

**OpenAI SDK to LangGraph:** Triggered when teams need model flexibility or advanced state management. Map each agent to a node, each handoff to a conditional edge, add explicit state typing.

---

## 7. Failure Modes & Mitigations

### 7.1 LangGraph Failure Taxonomy

| Class | Examples | Handler |
|-------|----------|---------|
| **Transient** | 408/429/5xx/529, TLS reset, MCP disconnect, `NodeTimeoutError` | Full jitter; same idempotency key; `RetryPolicy` 5xx only |
| **Permanent** | 400 schema, 401/403, refusal, spend-cap 429, `InvalidUpdateError`, RBAC deny | Fail the run; do not failover 400s; do not retry `InvalidUpdateError` |
| **Poison pill (graph)** | Conditional edge never returns END; ReAct tool loop; unbounded `Send`; `Command` + static edge double-fire | `RemainingSteps`; cap `Send` width; either `Command` or static edges per node |
| **Poison pill (ops)** | `InMemorySaver` + restart; missing `thread_id`; `durability="exit"` crash mid-node | Postgres/Agent Server; stable UUID; `durability="sync"` for money |
| **Semantic** | Goal hijack via tool output; schema-valid unauthorized refund | Frozen goal + RBAC + HITL |
| **Soft fuse as success** | "Sorry, need more steps..."; HTTP 200 with `__interrupt__` dropped | Metric + eval fail |
| **State bloat** | Fat `messages`; PDFs in state; 413 on Cloud | `RemoveMessage`/summarization; `DeltaChannel`; Store for blobs |

**Poison-pill detection inside ToolNode wrappers:**
1. Canonicalize args (JSON key sort; strip ephemeral timestamps).
2. Key = `(tool_name, args_hash)`.
3. After N=3 identical failures: hard observation, remove tool from allowlist.
4. After N=6 empty calls: abort the stream.

**Circuit breaker pattern** (one per (provider, model) for LLM, one per tool-class/MCP server):

```
           5xx/529/timeout rate >= threshold           probe success
  +--------+  ---------------------------------->  +------+  ------> CLOSED
  | CLOSED |                                       | OPEN |
  +---+----+  429 with Retry-After = throttle      +--+---+
      |       (stay CLOSED; sleep)                    | timer (30s)
      | success resets window                         v
      |                                          +----------+
      +------------------------------------------| HALF_OPEN|-- probe fail --> OPEN
                                                 | 1 cheap  |
                                                 | read     |
                                                 +----------+
```

**Fallback chain:** primary -> secondary vendor (same IR) -> deterministic `{"status":"degraded"}`. PermanentError does not failover. `GraphRecursionError` is a fuse, not a retry.

### 7.2 CrewAI Pitfalls

| Pitfall | Symptom | Solution |
|---------|---------|----------|
| Delegation loops (hierarchical) | Manager bounces tasks between agents with overlapping roles | Mutually exclusive roles. `max_iterations=7`. Disable delegation when not needed |
| Hallucination compounding | Error rate climbs beyond 4 sequential tools per agent | `temperature=0.1`, strict `expected_output` JSON schemas, `max_tokens`. Reduced failures 12% -> 3% across 1K runs |
| Context window overflow | Accuracy drops below 60% | Keep crews under 200 tasks. Split into multiple crews via Flows |
| Manager token overhead | 30% of budget consumed by coordination | Use sequential mode unless dynamic delegation is genuinely needed |
| State lost on restart | Production data disappears | Configure Mem0 with explicit `user_id` scoping |

### 7.3 OpenAI Agents SDK Pitfalls

| Pitfall | Symptom | Solution |
|---------|---------|----------|
| Non-deterministic handoff routing | Billing handoffs misfire on refund-policy questions | Structured classification step before handoff. Use evals to catch routing drift |
| Compounding step failure | 85% per-step success x 8 steps = 27% end-to-end | Minimize handoff chain depth. Add retry logic. Monitor per-step success rates |
| No mid-execution recovery | Work lost on process crash | Pair with Temporal for durable workflows |
| Guardrail scope gaps | Intermediate agents unchecked | Add tool-specific validation at each agent, not just input/output guards |
| Input guardrails first-agent-only | Policy violations pass through after handoff | Duplicate critical checks as tool-level guards on each specialist |

### 7.4 Durable Execution: Checkpointer + Platform

**LangGraph equivalent of Temporal Workflow + Kafka compacted log:**

| Component | LangGraph Equivalent |
|-----------|---------------------|
| Temporal Workflow history | Checkpointer at super-step grain |
| Workflow-id uniqueness | Agent Server thread lease (<=1 run/thread_id) |
| Kafka topic for wakeups | Redis queue + pub/sub (no user data) |
| Durable log | Postgres (checkpoints + writes + store) |
| Activity retry | Whole node restarts. Functional API @task results restore |

**Temporal plugin** (Python public preview): graph as Workflow; nodes as Activities. `interrupt()` only in Activity nodes; wait is a Temporal signal -- no worker CPU while HITL blocks. Python 3.11+ for interrupt/contextvars.

**Replay vs resume:**

| Event | Re-executes |
|-------|-------------|
| `Command(resume=)` after `interrupt()` | Entire node (tasks restored in Functional API) |
| Worker crash, `durability=async/sync` | From last checkpoint; successful sibling tasks skipped |
| Time-travel replay | All nodes after chosen checkpoint, including interrupts |

**Version skew:** Add/remove state keys; change topology on finished threads. Do not rename/remove the node an interrupted thread is about to enter. `DeltaChannel` checkpoints are unreadable on <1.2.

---

## 8. Security & Governance

### 8.1 Authentication, Authorization, and Compliance

| Capability | LangGraph | CrewAI | OpenAI Agents SDK |
|-----------|-----------|--------|-------------------|
| AuthN/AuthZ | Agent Authorization (beta, all tiers); SSO + RBAC on Enterprise | None (open-source); RBAC + SSO in Enterprise AMP | No built-in; application-level |
| SOC 2 Type II | Yes (via LangSmith) | Enterprise only | Via OpenAI platform |
| HIPAA | Yes (BAA on Enterprise tier) | Not documented | Via OpenAI platform |
| GDPR | Yes | Enterprise only | Via OpenAI platform |
| Audit logging | LangSmith: full decision traces, node-by-node. HIPAA auditors cited these as the artifact they needed | Console only (OSS). Enterprise AMP: immutable trails, PII hooks | Built-in tracing with visual DAGs, OpenTelemetry, `trace_include_sensitive_data` flag |

### 8.2 Zero-Trust MCP Wrapping

Agent Server serves graphs at `/mcp` (Streamable HTTP). Each `/mcp` request is stateless -- memory must live in the checkpointer/store.

**Wrap ToolNode:**
1. Remote MCP servers are OAuth 2.1 resource servers. RFC 9728 metadata; RFC 8707 `resource` indicator; PKCE; validate audience; never token-passthrough.
2. Gateway: terminate OAuth, RFC 8693 exchange to upstream. Pin manifests `hash(description+schema)` against rug-pulls.
3. Re-validate authorization at execution, not only at plan-approval.
4. `tool_filter` / per-assistant allowlist. HITL on mutating tools.
5. Cloud NAT IPs published for allowlists (post 2025-01-06 deployments).

### 8.3 Tool RBAC, PII, WORM Audit

**Tool RBAC:** Map `(principal, tenant, tool, args_shape)` -> allow / deny / HITL. Resume values must not concatenate into a new tool call without re-RBAC.

**PII pipeline:** Detect -> redact -> audit. DLP at ingress and executor before checkpoint/trace. PII in `messages` IS the checkpoint. Controls: `RemoveMessage`, Store with tighter TTL, `PIIMiddleware` on `create_agent`, LangSmith LLM Gateway redaction, `EncryptedSerializer` + `LANGGRAPH_AES_KEY`. Encryption does not change what is persisted -- design as if auth metadata may persist in plaintext.

**Immutable WORM audit fields:** `correlation_id`, tenant, `thread_id`, `checkpoint_id`, `checkpoint_ns`, super-step, node name, `Send` width, hashed draft/tool args, policy/HITL decision + actor, breaker state, `total_cost_usd`, model id, durability mode.

### 8.4 Known Security Vulnerabilities (2025-2026)

**LangGraph/LangChain** ("LangDrained" coordinated disclosure, March 2026, Cyera Research):

| CVE | Vulnerability | Patch |
|-----|--------------|-------|
| CVE-2025-67644 | SQL injection in SQLite checkpointer | `langgraph-checkpoint-sqlite >= 3.0.1` |
| CVE-2026-28277 | Unsafe msgpack deserialization -> RCE | `langgraph >= 1.0.10` |
| CVE-2026-27022 | SQL injection in Redis checkpointer | `langgraph-checkpoint-redis >= 1.0.2` |
| CVE-2025-64439 | Deserialization RCE in JsonPlusSerializer | `langgraph-checkpoint >= 3.0` |

Production mitigation: set `LANGGRAPH_STRICT_MSGPACK=true`.

**CrewAI** (disclosed by Yarden Porat, Cyata, 2026):

| CVE | Vulnerability |
|-----|--------------|
| CVE-2026-2275 | Sandbox escape in Code Interpreter |
| CVE-2026-2285 | Tool fallback to less-isolated execution without notification |
| CVE-2026-2286 | SSRF via unvalidated URLs in RAG search tools |
| CVE-2026-2287 | SandboxPython fallback bypass |

Pattern: tools falling back to less-isolated execution modes without notification, combined with prompt injection.

**OpenAI Agents SDK:** No publicly disclosed CVEs as of September 2026.

### 8.5 Tool Sandboxing

- **LangGraph:** No built-in sandboxing. Tools run in-process. Isolation via Docker/E2B is user's responsibility.
- **CrewAI:** Code Interpreter Docker sandbox and SandboxPython fallback both removed after CVE-2026-2275/2287. Current: external sandboxes (E2B, Daytona).
- **OpenAI Agents SDK:** Native sandbox execution added April 2026 (E2B/Modal/Daytona). Agents work in siloed workspaces.

### 8.6 Multi-Tenancy

- **LangGraph:** Not natively advertised. Implement via VPCs, namespace separation, RBAC. `thread_id` is tenant-scoped UUID. Store namespaces `(org, user, "prefs")`.
- **CrewAI:** OSS has no concept of teams or scoped API keys. Enterprise AMP adds identity, role-based filtering, cost attribution.
- **OpenAI SDK:** Application responsibility entirely.

---

## 9. System Design Scenarios

### Scenario 1: Insurance Claims Processing Pipeline

**Problem:** A mid-size insurance company (500K claims/year) must automate claims processing: extract structured data from PDFs/photos, classify claim type and severity, check policy coverage, flag fraud, route to human adjusters for approval above $10K, generate settlement letters. Regulatory: full audit trail, HIPAA compliance, dispute resolution replay.

```
+-------------------------------------------------------------------------+
|                      CLAIMS PROCESSING PIPELINE                          |
|                                                                          |
|  +-------------+    +--------------------------------------------------+|
|  | Document    |    |  LangGraph StateGraph                            ||
|  | Ingestion   |--->|                                                  ||
|  | (S3 + SQS)  |    |  State: ClaimState(TypedDict)                   ||
|  +-------------+    |    claim_id, documents, extracted_data,          ||
|                     |    classification, coverage_check,               ||
|                     |    fraud_score, adjuster_decision,               ||
|                     |    settlement_letter, audit_trail: list          ||
|                     |                                                  ||
|                     |  extract ---> classify                           ||
|                     |  (OCR+LLM)   (type, severity)                    ||
|                     |                    |                              ||
|                     |          +---------+---------+                    ||
|                     |          v                   v                    ||
|                     |  coverage_check       fraud_detect               ||
|                     |  (policy DB)          (ML + rules)               ||
|                     |          +--------+--------+                     ||
|                     |                   v                               ||
|                     |          route_decision (conditional edge)        ||
|                     |              |              |                     ||
|                     |     < $10K   |   >= $10K or fraud > 0.7          ||
|                     |     no fraud |              |                     ||
|                     |              v              v                     ||
|                     |    auto_approve     human_review                  ||
|                     |                    (interrupt() + resume)         ||
|                     |         +------+-------+                         ||
|                     |                v                                  ||
|                     |       generate_letter ---> END                    ||
|                     +--------------------------------------------------+|
|                                       |                                 |
|                     +-----------------+------------------+              |
|                     v                 v                   v              |
|          PostgreSQL        LangSmith           PgBouncer               |
|          (checkpoints,     (audit trail,       (connection pool,        |
|           claim state)      compliance)         transaction mode)       |
+-------------------------------------------------------------------------+
```

**Trade-off matrix:**

| Dimension | LangGraph | CrewAI | OpenAI Agents SDK |
|-----------|-----------|--------|-------------------|
| **Audit trail** | LangSmith node-by-node traces. HIPAA auditor-ready. Time-travel replay for disputes | Console only (OSS). Enterprise AMP adds audit at $2K/month | Tracing with visual DAGs. OpenTelemetry export. But no replay |
| **Human-in-the-loop** | Native `interrupt()`/`Command(resume=...)`. Any worker can resume | Not supported without external queue | Not built-in. Sessions handle conversation only |
| **Parallel execution** | Fan-out coverage_check + fraud_detect in parallel with merge reducer | Not supported in sequential. Hierarchical unpredictable | No native parallel execution |
| **State durability** | PostgresSaver with full checkpoint history. Crash recovery automatic | Ephemeral. Mem0 is per-agent, not per-claim | Sessions for conversation only. Crash = lost work |
| **Dispute resolution** | Time-travel: rewind to any step, inspect state, replay | Not possible | Not possible |
| **HIPAA** | BAA on Enterprise tier | Not documented | BAA via OpenAI platform but no workflow audit |
| **Scalability** | Stateless workers + PostgresSaver. PgBouncer. Platform auto-scales | Single-process per crew | Redis sessions + Temporal for durability adds complexity |

**Decision: LangGraph.** Regulatory requirements (HIPAA, full audit trail, dispute resolution replay) eliminate CrewAI OSS and make OpenAI SDK insufficient without heavy external infrastructure. Native `interrupt()` maps directly to ">$10K needs adjuster." Time-travel debugging uniquely addresses dispute resolution. The main cost is the 1-2 week learning curve and PostgresSaver operational overhead -- both acceptable for 500K claims/year.

### Scenario 2: AI-Powered Sales Development Representative (SDR)

**Problem:** A B2B SaaS startup (Series A, 15-person team) needs an AI SDR: qualify inbound leads, research prospect's company (LinkedIn, Crunchbase, news), personalize outreach email, send via email platform, handle replies. OpenAI models exclusively; ship in 2 weeks; founder reviews every email for month 1. Budget: minimal (pre-revenue).

```
+-------------------------------------------------------------------------+
|                         AI SDR SYSTEM                                    |
|  +-------------+                                                        |
|  | Inbound     |                                                        |
|  | Webhook     |                                                        |
|  | (HubSpot)   |                                                        |
|  +------+------+                                                        |
|         v                                                               |
|  +--------------------------------------------------------------+      |
|  |  OpenAI Agents SDK                                            |      |
|  |                                                               |      |
|  |  Lead Triage Agent (input guard: check_pii)                   |      |
|  |    handoffs: [research_agent, rejection_agent]                |      |
|  |         |                                                      |      |
|  |    +----+-------+                                              |      |
|  |    v            v                                              |      |
|  |  Research     Rejection                                       |      |
|  |  Agent        Agent                                           |      |
|  |  tools:       (polite decline;                                |      |
|  |  [linkedin,    output guard: tone_check)                      |      |
|  |   crunchbase]                                                 |      |
|  |  handoffs:                                                    |      |
|  |  [writer]                                                     |      |
|  |    v                                                          |      |
|  |  Writer Agent                                                 |      |
|  |  (output guard: brand_compliance)                             |      |
|  |    |                                                          |      |
|  +----+---> Founder Review Queue (Slack approve/reject)          |      |
|             |                                                    |      |
|             v (approved)                                         |      |
|  +------------------+                                            |      |
|  | Email Platform   |                                            |      |
|  | (SendGrid/Resend)|                                            |      |
|  +------------------+                                            |      |
|                                                                  |      |
|  Session: SQLite. Tracing: OpenAI dashboard (free).              |      |
|  Cost: ~$0.01-0.02/lead (4o-mini triage, 4o writing)            |      |
+------------------------------------------------------------------+------+
```

**Trade-off matrix:**

| Dimension | LangGraph | CrewAI | OpenAI Agents SDK |
|-----------|-----------|--------|-------------------|
| **Time to ship** | 1-2 weeks learning + implementation | 3-5 days prototype | **2-3 days**. Handoff maps directly |
| **Cost** | Free self-hosted + model. Over-engineered for this | Free + model. Reasonable fit | Free + model. Cheapest: triage on 4o-mini |
| **Complexity fit** | Over-engineered. Linear flow does not need a graph | Good conceptually but all agents run even for rejected leads | Best fit. Handoff skips unnecessary agents |
| **Human review** | Native `interrupt()` | Not supported | Slack webhook from output guardrail is trivial |
| **Observability** | LangSmith (overkill) | Console output | OpenAI dashboard (free, sufficient) |

**Decision: OpenAI Agents SDK.** Time-to-ship (2-3 days vs 1-2 weeks), cost efficiency (handoff skips unnecessary agents), and operational simplicity win for an early-stage startup. The founder review gate does not need `interrupt()` -- Slack webhook is simpler for a 15-person team. If the company scales to enterprise (regulated, complex workflows), plan a migration to LangGraph -- but that is a Series B problem.

### Scenario 3: Ticket HITL Workflow (Days-Long Wait, Audit)

**Problem:** Multi-tenant support: classify -> retrieve -> draft reply -> adjuster approval -> send. Pause days, no GPU/worker burn, replayable, SSO. Volume 1k tickets/day. Budget: GPT-4.1 ~$37.20/1k + ~$0.04/1k serverless compute.

**Trade-off matrix (LangGraph design variants):**

| Dimension | A. InMemorySaver + FastAPI holding worker for HITL | B. Postgres/Agent Server + interrupt() + durability=sync on send | C. create_agent only (model <-> tools) with HITL middleware |
|-----------|-----|-----|-----|
| **Cost/1k** | Same $37.20 until restart replays ($x2) | $37.20 + ~$0.04 compute; HITL wait $0 | Middleware adds calls |
| **Latency** | Worker tied up for days; p99 = process lifetime | p50 <8s pre-HITL; p99 = human SLA | Extra middleware hops |
| **Security** | RAM dump has transcripts; resume is whoever hits process | SSO resume; secrets in context; send is separate node | Still needs checkpointer |
| **Scalability** | 1k parked tickets = 1k stuck workers | N_JOBS=10 on running ticks; parked HITL is a Postgres row | Fine for 1-4 tool hops |

**Decision: B** is the only design that treats HITL as a durable super-step, not a blocked socket. A fails the money-and-audit exam. C is the right inner loop and the wrong outer machine -- a ticket is classify/retrieve/draft/review/send, not model <-> tools until silence.

---

## 10. Interview Quick Reference

### Framework Decision Matrix

| If the interviewer says... | Recommend | Key phrase |
|---------------------------|-----------|------------|
| "Regulated, HIPAA, audit trail, human approval gates" | LangGraph | "Durable checkpoint + interrupt() + LangSmith traces + HIPAA BAA" |
| "Prototype multi-agent in a week" | CrewAI | "Role-based agents, working crew in 30 lines, then migrate to LangGraph" |
| "Simple router + specialists, ship fast" | OpenAI Agents SDK | "Handoff pattern maps directly, 2-3 days, built-in tracing" |
| "Complex loops, parallel branches, fan-out/fan-in" | LangGraph | "Send map-reduce on one super-step, conditional edges, explicit graph" |
| "Long-running tasks (hours/days)" | LangGraph (or SDK + Temporal) | "Checkpoint-based resume, interrupt() costs $0 while parked" |
| "Single agent, 1-2 tools" | No framework | "Plain SDK loop with max_steps cap. Framework adds friction, not value" |

### Key Numbers to Memorize

| Number | What |
|--------|------|
| **$37.20 / $124 per 1k** | GPT-4.1 ticket (3 LLM) / Send N=8 (10 LLM) [inferred] |
| **$2.10 / $7 per 1k** | Same skeletons on GPT-6 Luna [inferred] |
| **~$0.04 / 1k** | Platform compute at 2 vCPU-s [inferred] |
| **~$390/mo** | Dedicated Small always-on [inferred] |
| **$39 / $1.50 / $1.00** | Plus seat / LCU / LSU |
| **2026-10-01** | Old per-run prices die; LCU/LSU thereafter |
| **1000** | `recursion_limit` default since 1.0.6 (the 25 is not a nesting cap) |
| **10 / 409** | `N_JOBS_PER_WORKER`; `reject` -> HTTP 409 |
| **10** | OpenAI Agents SDK `max_turns` default -- not 1000 super-steps |
| **<=1 run / thread_id** | Agent Server invariant |
| **25 MB / 413** | Cloud payload cap |
| **<255 chars / ~2704 bytes** | Docs `thread_id` vs Postgres btree |
| **3 / 0.5s / 2.0 / 128s** | RetryPolicy attempts / initial / backoff / cap |
| **exit / async / sync** | Durability modes; Agent Server default async |
| **7 stream modes** | values, updates, messages, custom, checkpoints, tasks, debug |
| **42K / 58K / 29K** | GitHub stars: LangGraph / CrewAI / OpenAI SDK |
| **38.8M / 27M+** | Monthly PyPI downloads: LangGraph / CrewAI |
| **~14ms** | LangGraph framework orchestration overhead (dwarfed by LLM ~1-3s) |
| **62% vs 54%** | LangGraph vs CrewAI success rate on complex reasoning tasks |

### Interview Closers

**LangGraph-focused:** "The model is an untrusted planner inside a Pregel graph I drew. I compile a StateGraph with reducers, cap Send width in the router, park HITL on interrupt() with Postgres and a thread lease, put recursion_limit on the invoke dict, and I do not confuse that fuse with OpenAI max_turns or a CrewAI role. Ticket skeleton $37.20/1k; Send N=8 $124/1k; one run per thread_id or you get 409."

**Framework comparison:** "The framework question that matters is not 'which is best' but 'which execution model -- graph vs role-based vs handoff -- fits the stated requirements, and what are the trade-offs given the team's constraints.' I prototype in CrewAI to validate the concept in days, then productionize in LangGraph for durability, control, and HITL."

**Architecture:** "Pregel owns the tick; ToolNode owns the HTTP; the checkpointer owns the resume. I never let the model execute, and I never hold a FastAPI worker for a days-long interrupt()."

### Emerging Convergence (2026)

With MCP standardizing how agents reach tools and A2A standardizing how agents talk to each other, the framework choice is shifting from "which library" to "which execution model matches your workload." Google ADK is the only framework with first-class support for both MCP and A2A. The protocol layer is becoming framework-agnostic -- tool integrations and agent-to-agent communication will survive a framework migration.
