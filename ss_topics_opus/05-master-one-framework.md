# Module 05: Master One Framework -- LangGraph, CrewAI, OpenAI Agents SDK

> **Audience**: Principal Data Scientist (12+ YOE) preparing for Director/VP-level AI roles.
> **Scope**: Deep comparative analysis of the three leading multi-agent orchestration frameworks -- LangGraph, CrewAI, and OpenAI Agents SDK -- covering execution models, state management, token economics, enterprise resilience, security posture, production code, and system design scenarios. Google ADK included as a brief reference point.
> **Pricing assumptions**: GPT-4o input $2.50/1M, output $10/1M; GPT-4o-mini input $0.15/1M, output $0.60/1M; Claude Sonnet 4 input $3/1M, output $15/1M. All as of mid-2026.
> **Framework versions**: LangGraph 1.0+ (GA Oct 2025), CrewAI 1.10.x, OpenAI Agents SDK 0.2.x (Apr 2026).

---

## 1. System Topology & Data Flow

### 1.1 LangGraph: StateGraph Architecture

LangGraph models workflows as directed graphs built from three primitives: **State**, **Nodes**, and **Edges**. Execution follows a Pregel-style message-passing model. No LangChain dependency required since 1.0.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           CONTROL PLANE                                      │
│                                                                              │
│  ┌──────────────────┐   ┌───────────────────┐   ┌────────────────────────┐  │
│  │  StateGraph       │   │  Edge Router       │   │  Recursion Limiter     │  │
│  │  Builder          │   │                    │   │                        │  │
│  │                  │   │  - Fixed edges     │   │  - Default: 1000 steps │  │
│  │  - .add_node()   │   │    (A -> B)        │   │    (v1.0.6+)           │  │
│  │  - .add_edge()   │   │  - Conditional     │   │  - Configurable per    │  │
│  │  - .compile()    │   │    edges (fn ->    │   │    invocation           │  │
│  │  - Type checking │   │    {node | END})   │   │  - Safety net, not     │  │
│  │  - Edge validate │   │  - START/END       │   │    control flow         │  │
│  └────────┬─────────┘   └───────────────────┘   └────────────────────────┘  │
│           │                                                                  │
├───────────┼──────────────────────────────────────────────────────────────────┤
│           │              DATA PLANE                                           │
│           v                                                                  │
│  ┌──────────────────┐   ┌───────────────────┐   ┌────────────────────────┐  │
│  │  Node Executor    │   │  State Container   │   │  Reducer Engine        │  │
│  │                  │   │  (TypedDict or     │   │                        │  │
│  │  - Python fn     │   │   Pydantic)        │   │  - Overwrite (default) │  │
│  │    (sync/async)  │   │                    │   │  - Append (lists)      │  │
│  │  - Receives full │   │  - Partial updates │   │  - Custom merge fn     │  │
│  │    state         │   │    per node        │   │  - Parallel-safe when  │  │
│  │  - Returns dict  │   │  - O(1) delta      │   │    fields are disjoint │  │
│  │    (partial)     │   │    passing         │   │  - DANGER: same-field  │  │
│  │  - No control    │   │  - Schema-typed    │   │    parallel = lost     │  │
│  │    flow logic    │   │                    │   │    updates             │  │
│  └────────┬─────────┘   └───────────────────┘   └────────────────────────┘  │
│           │                                                                  │
├───────────┼──────────────────────────────────────────────────────────────────┤
│           │              PERSISTENCE LAYER                                    │
│           v                                                                  │
│  ┌──────────────────┐   ┌───────────────────┐   ┌────────────────────────┐  │
│  │  Checkpointer     │   │  Store (cross-     │   │  Thread Manager        │  │
│  │                  │   │  thread memory)    │   │                        │  │
│  │  - InMemorySaver │   │                    │   │  - Thread ID scoping   │  │
│  │    (dev only)    │   │  - Long-term KV    │   │  - Multi-process share │  │
│  │  - SqliteSaver   │   │    store           │   │    via Postgres        │  │
│  │    (single node) │   │  - Shared across   │   │  - Any worker can      │  │
│  │  - PostgresSaver │   │    all threads     │   │    resume any thread   │  │
│  │    (production)  │   │  - User profiles,  │   │  - Stateless workers   │  │
│  │  - RedisSaver    │   │    learned prefs   │   │                        │  │
│  └──────────────────┘   └───────────────────┘   └────────────────────────┘  │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│                      TELEMETRY                                               │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────────┐│
│  │  LangSmith (full decision traces, node-by-node)                          ││
│  │  - SOC 2 Type II certified    - HIPAA BAA on Enterprise                 ││
│  │  - Per-step token/cost tracking    - Time-travel debug replay           ││
│  └──────────────────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────────────┘
```

**Request flow narrative (single invocation)**:

1. Client calls `graph.invoke(initial_state, config={"thread_id": "t-123"})`.
2. StateGraph identifies the START node and dispatches to the first node function.
3. Node executes (LLM call, tool call, computation) and returns a partial state dict.
4. Reducer engine merges the partial dict into the full state. If the field has an `Annotated[list, operator.add]` reducer, the value is appended; otherwise it overwrites.
5. Checkpointer persists the new state snapshot (full state + metadata) to the configured backend.
6. Edge router evaluates: fixed edges proceed unconditionally; conditional edges call a routing function that inspects state and returns the next node name or `END`.
7. Steps 3-6 repeat until an edge routes to `END` or the recursion limit is hit.
8. If `interrupt()` is called inside any node, execution halts, the checkpoint is saved, and any worker can later resume with `Command(resume=...)`.

**Subgraph composition**: A node in one graph can be another compiled graph. The parent graph passes a subset of its state as input; the subgraph runs to completion and returns its output, which the parent merges via its own reducer.

### 1.2 CrewAI: Flows / Crews / Agents / Tasks

CrewAI models multi-agent collaboration as a team of role-playing agents organized in a four-layer hierarchy. Built independently (not wrapping LangChain).

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           ORCHESTRATION LAYER (Flows)                        │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────────┐│
│  │  Flow                                                                    ││
│  │  - Top-level event-driven orchestration                                  ││
│  │  - Routes data between steps (Crews or plain functions)                  ││
│  │  - Conditional branching on step outputs                                 ││
│  │  - Decides which Crew runs when and what happens with results            ││
│  └────────┬──────────────────────────────┬──────────────────────────────────┘│
│           │                              │                                   │
├───────────┼──────────────────────────────┼───────────────────────────────────┤
│           v                              v                                   │
│  ┌──────────────────┐           ┌──────────────────┐    CREW LAYER           │
│  │  Crew A           │           │  Crew B           │                        │
│  │  (Research)       │           │  (Writing)        │                        │
│  │                  │           │                  │                        │
│  │  Process:        │           │  Process:        │                        │
│  │   sequential     │           │   hierarchical   │                        │
│  │                  │           │   (manager LLM)  │                        │
│  │  ┌────────────┐  │           │  ┌────────────┐  │                        │
│  │  │ Agent 1    │  │           │  │ Agent 3    │  │                        │
│  │  │ role:      │  │           │  │ role:      │  │                        │
│  │  │ "Analyst"  │  │           │  │ "Editor"   │  │                        │
│  │  │ goal: ...  │  │           │  │ goal: ...  │  │                        │
│  │  │ backstory: │  │           │  │ tools: []  │  │                        │
│  │  │  ...       │  │           │  └────────────┘  │                        │
│  │  │ tools: [T1]│  │           │  ┌────────────┐  │                        │
│  │  └────────────┘  │           │  │ Agent 4    │  │                        │
│  │  ┌────────────┐  │           │  │ role:      │  │                        │
│  │  │ Agent 2    │  │           │  │ "Writer"   │  │                        │
│  │  │ role:      │  │           │  └────────────┘  │                        │
│  │  │ "Gatherer" │  │           │                  │                        │
│  │  └────────────┘  │           │                  │                        │
│  │                  │           │                  │                        │
│  │  ┌────────────┐  │           │  ┌────────────┐  │    TASK LAYER          │
│  │  │ Task 1     │──┼──output──>│  │ Task 3     │  │                        │
│  │  │ agent: A1  │  │   feeds   │  │ agent: A3  │  │                        │
│  │  │ expected:  │  │   into    │  │ expected:  │  │                        │
│  │  │  JSON      │  │           │  │  markdown  │  │                        │
│  │  └────────────┘  │           │  └────────────┘  │                        │
│  │  ┌────────────┐  │           │  ┌────────────┐  │                        │
│  │  │ Task 2     │  │           │  │ Task 4     │  │                        │
│  │  │ agent: A2  │  │           │  │ agent: A4  │  │                        │
│  │  └────────────┘  │           │  └────────────┘  │                        │
│  └──────────────────┘           └──────────────────┘                        │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│  PERSISTENCE: Ephemeral by default (LanceDB/SQLite, lost on container       │
│  restart). Production fix: Mem0 (Cloud or self-hosted w/ Qdrant/pgvector).  │
│  Memory types: short-term, long-term, entity. Scoped to single Crew only.  │
├──────────────────────────────────────────────────────────────────────────────┤
│  TELEMETRY: Console output only (open-source). Enterprise AMP adds          │
│  immutable audit trails, runtime hooks for PII redaction.                   │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Process types**:
- **Sequential**: Tasks execute one after another; each agent's output feeds into the next. Most predictable for debugging.
- **Hierarchical**: A manager agent (separate LLM call) dynamically delegates tasks to specialists. Known bug: manager sometimes runs tasks in sequence instead of routing to the best agent (GitHub issue #4783, March 2026).
- **Consensual** (planned, not yet released): Agents negotiate and vote before executing.

**Request flow narrative**:

1. A Flow starts, evaluating step decorators (`@start`, `@listen`, `@router`) to determine initial execution.
2. The first Crew receives structured input from the Flow.
3. The Crew's process type (sequential or hierarchical) determines task execution order.
4. In sequential mode: Task 1 runs with Agent 1; its output feeds as context into Task 2 with Agent 2; and so on.
5. In hierarchical mode: A manager LLM reviews all tasks, selects which agent handles each, may re-delegate if the agent's output is insufficient.
6. Each agent internally runs a ReAct-style loop: reason about the task, optionally call tools, produce output matching `expected_output` format.
7. The Crew returns its final output to the Flow, which routes to the next step or terminates.

### 1.3 OpenAI Agents SDK: Handoff-Based Architecture

Released March 2025 as the successor to experimental Swarm. Six core primitives: Agent, Runner, Tools, Handoffs, Guardrails, Sessions.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           CONTROL PLANE                                      │
│                                                                              │
│  ┌──────────────────┐   ┌───────────────────┐   ┌────────────────────────┐  │
│  │  Runner            │   │  Guardrails        │   │  Session Manager      │  │
│  │                  │   │                    │   │                        │  │
│  │  - Executes agent│   │  - Input guards:   │   │  - Pluggable backends: │  │
│  │    loop          │   │    intercept user  │   │    SQLite, Redis,      │  │
│  │  - Call model    │   │    input before    │   │    SQLAlchemy          │  │
│  │  - Run tools     │   │    processing      │   │  - Cross-run convo    │  │
│  │  - Feed result   │   │  - Output guards:  │   │    persistence         │  │
│  │    back          │   │    validate final  │   │  - Thread-scoped       │  │
│  │  - Manage turns  │   │    response        │   │    history             │  │
│  │  - Handle        │   │  - Tripwire        │   │                        │  │
│  │    handoffs      │   │    exceptions      │   │                        │  │
│  │  - Uses Responses│   │  - Run alongside   │   │                        │  │
│  │    API by default│   │    agent (async)   │   │                        │  │
│  └────────┬─────────┘   └───────────────────┘   └────────────────────────┘  │
│           │                                                                  │
├───────────┼──────────────────────────────────────────────────────────────────┤
│           │              DATA PLANE                                           │
│           v                                                                  │
│                                                                              │
│  ┌─────────────┐  handoff   ┌─────────────┐  handoff   ┌─────────────┐     │
│  │  Triage      │──────────>│  Specialist  │──────────>│  Specialist  │     │
│  │  Agent       │           │  Agent A     │           │  Agent B     │     │
│  │             │           │             │           │             │     │
│  │  instruc-   │           │  instruc-   │           │  instruc-   │     │
│  │  tions:     │           │  tions:     │           │  tions:     │     │
│  │  "Route to  │           │  "Handle    │           │  "Handle    │     │
│  │   correct   │           │   billing   │           │   tech      │     │
│  │   agent"    │           │   queries"  │           │   support"  │     │
│  │             │           │             │           │             │     │
│  │  handoffs:  │           │  tools:     │           │  tools:     │     │
│  │  [A, B]     │           │  [lookup_   │           │  [search_   │     │
│  │             │           │   invoice]  │           │   kb]       │     │
│  └─────────────┘           └─────────────┘           └─────────────┘     │
│                                                                              │
│  Handoff = tool call (transfer_to_X). Runner switches active agent,         │
│  passes full conversation history. New agent fully takes over.              │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│  PERSISTENCE: Session backends (SQLite/Redis) for conversation state.       │
│  No workflow-level checkpointing. Long-horizon harness (Apr 2026) adds      │
│  cross-turn state for tasks spanning hours/days (details emerging).         │
├──────────────────────────────────────────────────────────────────────────────┤
│  TELEMETRY: Built-in tracing (auto per Runner.run). Visual DAGs.            │
│  Uploaded to OpenAI dashboard by default. OpenTelemetry export supported.   │
│  Sensitive data control: trace_include_sensitive_data flag.                  │
├──────────────────────────────────────────────────────────────────────────────┤
│  SANDBOX: Native (Apr 2026) -- E2B/Modal/Daytona for untrusted code.       │
│  Agents work in siloed workspaces accessing only explicitly relevant files. │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Request flow narrative**:

1. `Runner.run(triage_agent, input="I need a refund")` starts the loop.
2. Runner calls the model with the triage agent's instructions, tools, and handoff definitions.
3. Model returns a tool call: `transfer_to_billing_agent`.
4. Runner executes the handoff: switches active agent to `billing_agent`, passes full conversation history.
5. `billing_agent` now runs its own loop: reason, call tools (e.g., `lookup_invoice`), produce response.
6. If `billing_agent` needs tech support context, it can handoff to `tech_agent`.
7. Output guardrails validate the final agent's response. If a tripwire fires, the response is blocked.
8. Input guardrails only apply to the first agent. Intermediate agents are unchecked unless tool-specific guards are added.

**Critical design implication**: Control flow lives in prompts, not code. A router that misroutes in code is a unit-testable bug. A handoff that misroutes is caught in evals -- if caught at all.

### 1.4 Google ADK (Brief Reference)

Google Agent Development Kit uses an event-driven runtime with a hierarchical agent tree.

```
┌──────────────────────────────────────────┐
│  Root Agent                              │
│  (delegates to sub-agents)               │
│                                          │
│  ┌────────────┐  ┌────────────────────┐  │
│  │ Sequential │  │ ParallelAgent      │  │
│  │ Agent      │  │ (fan-out/fan-in)   │  │
│  │ (ordered   │  │                    │  │
│  │  steps)    │  │  ┌──────┐ ┌──────┐│  │
│  └────────────┘  │  │Sub-A │ │Sub-B ││  │
│  ┌────────────┐  │  └──────┘ └──────┘│  │
│  │ LoopAgent  │  └────────────────────┘  │
│  │ (repeat    │                          │
│  │  until     │  Protocols:              │
│  │  condition)│  - MCP (vertical tools)  │
│  └────────────┘  - A2A (horizontal agent)│
│                  Only framework with     │
│                  first-class both.       │
└──────────────────────────────────────────┘
Deploy: Vertex AI, Cloud Run, or GKE.
Languages: Python, TS, Go, Java.
```

---

## 2. Core Mechanics & Algorithms

### 2.1 LangGraph: Pregel-Style Execution

**Execution model**: Each "super-step" processes all nodes whose inputs are ready, applies reducers, and writes a checkpoint. This is modeled after Google's Pregel graph-processing system.

**State channels and reducers**: State is a typed container (TypedDict or Pydantic). Each node returns a partial update. The reducer determines how the partial merges into the full state:

| Reducer | Behavior | Use Case |
|---------|----------|----------|
| Default (overwrite) | New value replaces old | Single-writer fields (current step, status) |
| `operator.add` | Append to list | Message history, accumulated observations |
| Custom function | `fn(old, new) -> merged` | Conflict resolution, dedup, capped lists |

**Conditional edges**: A function inspects state and returns a string (node name or `END`). This is deterministic, testable code -- not prompt-driven routing.

**Streaming modes**: LangGraph supports streaming state updates (incremental state deltas), events (node entry/exit), and custom data (arbitrary payloads emitted mid-node).

**Interrupt/resume**: Inside any node, `interrupt(payload)` saves a checkpoint and returns control to the caller. Any worker can later call `Command(resume=user_input)` on the same thread ID. This is the foundation of human-in-the-loop patterns.

**Subgraphs**: A compiled graph can be used as a node in a parent graph. The parent maps a subset of its state to the subgraph's input schema; the subgraph runs to completion and returns output that the parent merges. This enables modular, testable graph composition.

### 2.2 CrewAI: Role-Based Design with Process Control

**Agent design**: Each agent gets a `role`, `goal`, and `backstory` that collectively shape its LLM behavior. The backstory is the key differentiator from simple system prompts -- it provides context that grounds the agent's reasoning in a persona.

**Task delegation**: In sequential mode, delegation is implicit (output of Task N feeds into Task N+1). In hierarchical mode, a manager agent (instantiated as a separate LLM call) reads all task descriptions and agent capabilities, then assigns tasks dynamically. The manager can re-assign if output quality is insufficient.

**Memory system**:
- **Short-term**: Within a single crew execution, agents share context about what has been done.
- **Long-term**: Persisted across executions. Stores task results, agent observations.
- **Entity**: Named-entity tracking across interactions (e.g., remembering a specific customer across runs).

**Tool ecosystem**: 60+ built-in tools (web search, file I/O, code execution, database queries, API integrations). Native MCP and A2A protocol support.

**Key limitation**: Memory stays scoped to a single Crew. No federation, conflict resolution, or ownership metadata across Crews.

### 2.3 OpenAI Agents SDK: Handoff Chains and Guardrails

**Handoff mechanics**: A handoff is implemented as a tool call (`transfer_to_X`). When the model invokes it, the Runner:
1. Switches the active agent to the target.
2. Passes the full conversation history to the new agent.
3. The new agent fully takes over (it does not return a result to an orchestrator).

This means handoffs are flat, not hierarchical. There is no "manager" that collects results. Each agent is autonomous once it receives control.

**Guardrail system**:
- **Input guardrails**: Run on the initial user input before any agent processes it. Can use a cheap, fast model for classification.
- **Output guardrails**: Validate the final agent's response. Use Pydantic models for structured validation.
- **Tripwire pattern**: If a guardrail detects a violation, it raises a `tripwire` exception that halts the agent.
- **Scope limitation**: Input guardrails apply only to the first agent. Output guardrails apply only to the final agent. Intermediate agents in a handoff chain are unchecked.

**Context variables**: Typed, dependency-injection-style values passed through the agent chain. Allow sharing of request-scoped data (user ID, session metadata) without polluting conversation history.

**Model-agnostic support**: Despite "OpenAI" in the name, the SDK supports any OpenAI-compatible API provider. The Responses API is used by default for OpenAI models; Chat Completions API works for others.

### 2.4 Cross-Framework Comparison

| Dimension | LangGraph | CrewAI | OpenAI Agents SDK | Google ADK |
|-----------|-----------|--------|-------------------|------------|
| **Execution model** | Directed graph (Pregel-style) | Role-based crews with process types | Handoff chains (flat) | Agent tree + workflow agents |
| **Primary abstraction** | State + Nodes + Edges | Agents + Tasks + Crews + Flows | Agent + Runner + Handoffs | Agents + Workflow agents |
| **Control flow** | Explicit (you draw every edge) | Implicit (process type + delegation) | Prompt-driven (handoff routing) | Mixed (declarative + LLM delegation) |
| **State management** | First-class typed state with reducers | Task output passing; ephemeral memory | Session-based conversation history | Session state + pluggable backends |
| **Human-in-the-loop** | Native (`interrupt()` / `Command(resume=...)`) | Not built-in | Not built-in (sessions only) | Supported via callbacks |
| **Extensibility** | Highest (arbitrary graph topology) | Medium (3 process types) | Low-medium (linear handoff chains) | High (composable agent types) |
| **Testability** | High (edges are deterministic code) | Medium (LLM-driven delegation) | Low (routing in prompts) | Medium |
| **Learning curve** | 1-2 weeks | 3-5 days | 2-3 days | ~1 week |
| **Languages** | Python, JS/TS | Python | Python, TypeScript | Python, TS, Go, Java |

### 2.5 Decision Matrix: When to Choose Which

| Scenario | Recommended | Rationale |
|----------|-------------|-----------|
| Regulated industry (finance, healthcare) requiring audit trails and human approval gates | **LangGraph** | Native HITL, durable checkpointing, LangSmith traces for compliance auditors, HIPAA BAA available |
| Rapid prototype of multi-agent system for concept validation | **CrewAI** | Working two-agent crew in ~30 lines. Fastest time-to-first-working-agent |
| Simple router + specialists pattern (customer support, triage) | **OpenAI Agents SDK** | Handoff pattern maps directly. Minimal boilerplate. Built-in tracing |
| Complex non-linear workflow with loops, parallel branches, conditional routing | **LangGraph** | Explicit graph topology. Conditional edges. Fan-out/fan-in |
| Content operations (research, writing, review pipelines) | **CrewAI** | Role-based design maps naturally to editorial workflows |
| Google Cloud shop with Gemini-first strategy | **Google ADK** | Native Vertex AI deployment. A2A + MCP support. GKE scaling |
| Long-running autonomous tasks (hours/days) | **LangGraph** (or OpenAI SDK + Temporal) | Durable execution with checkpoint-based resume |
| Single agent, 1-2 tools | **No framework** | Plain SDK loop with `max_steps` cap. Framework adds friction, not value |

**The hybrid path** (most common enterprise pattern): Prototype in CrewAI (validate the multi-agent concept in days), productionize in LangGraph (durability, control, HITL). This is a feature, not indecision.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Formulas

**Framework licensing costs**:

| Component | LangGraph | CrewAI | OpenAI Agents SDK |
|-----------|-----------|--------|-------------------|
| Core library | Free (MIT) | Free (open-source) | Free (open-source) |
| Managed platform | $39/user/month (100K node executions included; $0.001/additional execution) | Enterprise AMP ~$2,000/month | N/A (use OpenAI API directly) |
| Self-hosted | Infrastructure only (compute + Postgres + monitoring) | Infrastructure only | Infrastructure only |

**Per-run model costs** (3-agent sequential workflow, GPT-4o):

```
LangGraph cost per run:
  System prompts:    3 agents x ~500 tokens          =   1,500 input tokens
  State overhead:    ~530 tokens (framework routing)  =     530 input tokens
  Agent reasoning:   3 agents x ~800 tokens output    =   2,400 output tokens
  Total: ~2,030 input + 2,400 output
  Cost:  (2,030 / 1M x $2.50) + (2,400 / 1M x $10) = $0.005 + $0.024 = ~$0.029/run

CrewAI cost per run:
  System prompts:    3 agents x ~500 tokens           =   1,500 input tokens
  Role/backstory:    3 agents x ~300 tokens            =     900 input tokens
  Manager overhead:  ~2,100 tokens (hierarchical mode) =   2,100 input tokens
  Agent reasoning:   3 agents x ~800 tokens output     =   2,400 output tokens
  Total: ~4,500 input + 2,400 output
  Cost:  (4,500 / 1M x $2.50) + (2,400 / 1M x $10) = $0.011 + $0.024 = ~$0.035/run

OpenAI Agents SDK cost per run:
  Triage agent:      ~300 tokens in + ~100 out (cheap routing)
  Specialist agent:  ~800 tokens in + ~800 out
  Total: ~1,100 input + 900 output (assuming 1 handoff, 1 specialist)
  Cost:  (1,100 / 1M x $2.50) + (900 / 1M x $10) = $0.003 + $0.009 = ~$0.012/run
```

**Key insight**: LangGraph achieves 47% lower token costs than CrewAI due to explicit edge transitions instead of LLM-driven task routing. In hierarchical mode, CrewAI's manager agent consumes ~30% of total token budget just for coordination.

**At scale** (1,000 runs/month, GPT-4o):

| Framework | Model Cost | Platform Cost | Total |
|-----------|-----------|---------------|-------|
| LangGraph | ~$29 | $0 (self-hosted) or $39 (managed) | $29-$68 |
| CrewAI | ~$35 | $0 (self-hosted) or ~$2,000 (Enterprise) | $35-$2,035 |
| OpenAI SDK | ~$12 | $0 | $12 |

**Cost optimization strategies**:
- **OpenAI SDK**: The handoff pattern naturally optimizes costs -- a cheap triage agent (GPT-4o-mini at $0.15/1M input) handles classification, routing to expensive specialists only when needed.
- **LangGraph**: State deltas (not full conversation replay) keep per-step cost constant regardless of conversation length.
- **CrewAI**: Use sequential mode to avoid manager overhead. Set `max_iterations` and `max_tokens` per agent.

### 3.2 Latency SLA Targets

**Framework orchestration overhead** (AIMultiple benchmark, 100 queries x 100 runs, identical models/tools):

| Framework | Orchestration Latency | Token Overhead/Query |
|-----------|----------------------|---------------------|
| DSPy | ~3.5ms | ~2,030 tokens |
| Haystack | ~5.9ms | ~1,570 tokens |
| LlamaIndex | ~6.0ms | ~1,600 tokens |
| LangChain | ~10ms | ~2,400 tokens |
| LangGraph | ~14ms | ~2,030 tokens |

Framework overhead is measurable but dwarfed by LLM inference time (typically 1-3 seconds per call). The framework choice does not materially impact end-to-end latency.

**Multi-agent workflow latency**: LangGraph measured at ~10,155ms in multi-agent benchmarks (dominated by LLM calls, not framework overhead). A Rust-based alternative (AutoAgents) beat LangGraph by 43.7% on pure framework latency -- but the gains are negligible when LLM inference is 99% of wall-clock time.

**Streaming characteristics**:
- **LangGraph**: Streams state updates, events, and custom data. Essential for long-running graphs where users need progress indicators.
- **CrewAI**: Streaming available. Executes tasks 5.76x faster than LangGraph in simple QA scenarios (JetThoughts benchmark), but LangGraph achieves 62% success rate vs CrewAI's 54% on complex tasks requiring deep reasoning.
- **OpenAI Agents SDK**: Native streaming. Added streaming speech-to-text in April 2026.
- **Google ADK**: Bidirectional audio and video streaming (unique capability).

### 3.3 Throughput and Scalability Limits

| Constraint | LangGraph | CrewAI | OpenAI Agents SDK |
|-----------|-----------|--------|-------------------|
| **Primary bottleneck** | Checkpointer state bloat | Context window overflow | No built-in scaling |
| **Scaling math** | 50MB state x 10 steps = 500MB written to Postgres per thread | >200 tasks per crew: context overflow, accuracy <60% | Relies on external infra |
| **Mitigation** | Store references to large artifacts, not artifacts themselves. PgBouncer for connection pooling | Keep crews small (<10 tasks). Split into multiple crews via Flows | Redis sessions for distributed. Pair with Temporal for durability |
| **Horizontal scaling** | PostgresSaver enables multi-process. Platform handles auto-scaling | Single-process per crew (open-source). Factory for containers | Application responsibility |

### 3.4 Framework Maturity Indicators (September 2026)

| Metric | LangGraph | CrewAI | OpenAI Agents SDK |
|--------|-----------|--------|-------------------|
| GitHub stars | ~42K | ~58K | ~29K |
| Monthly PyPI downloads | 38.8M | 27M+ | Not reported |
| GA version | 1.0 (Oct 2025) | 1.10.x | 0.2.x (Apr 2026) |
| Enterprise adopters | Klarna, Replit, Elastic, Lyft, Uber, Coinbase, NVIDIA | 450M monthly workflows, 2B agent runs/year, 12M+ daily executions | Growing, primarily OpenAI ecosystem |
| Learning curve | 1-2 weeks | 3-5 days | 2-3 days |

**Reading the signals**: CrewAI has the most stars (community enthusiasm) but LangGraph has the most PyPI downloads (production adoption). OpenAI SDK is youngest but growing fastest. Stars measure hype; downloads measure integration into CI pipelines.

---

## 4. Distributed Resilience & Security

### 4.1 Checkpointing and State Persistence

**LangGraph checkpointers** (thread-scoped, short-term):

| Checkpointer | Durability | Concurrency | Production Use |
|--------------|-----------|-------------|----------------|
| `InMemorySaver` | None (lost on restart) | Single process | Dev/test only |
| `SqliteSaver` | Single machine | Single process | Small-scale, zero-setup |
| `PostgresSaver` | Full (transactional) | Multi-process, concurrent | Production standard |
| `RedisSaver` | Full | Multi-process | Use with caution (CVE history) |
| Custom (`BaseCheckpointSaver`) | Depends | Depends | Any backend |

**Rule of thumb**: InMemorySaver for throwaway runs, SqliteSaver for single-machine persistence, PostgresSaver once multiple processes share threads.

**LangGraph Stores** (cross-thread, long-term): A separate KV store for data that persists across threads -- user profiles, learned preferences, organizational knowledge. This is distinct from checkpointers and solves a different problem.

**CrewAI state persistence**: Default LanceDB/SQLite storage is lost on every container restart. This is the most commonly reported production issue. Fix: configure Mem0 (Cloud or self-hosted with Qdrant/pgvector) as an external memory provider via `memory_config`. Critical warning: without explicit `user_id` scoping via the ExternalMemory API, context bleeds between users in multi-tenant deployments.

**OpenAI Agents SDK sessions**: Conversation state persists across runs via pluggable backends. But sessions handle conversational context only -- there is no workflow-level checkpointing. If the process dies mid-loop, work is lost. The April 2026 long-horizon harness partially addresses this for tasks spanning hours or days.

### 4.2 Fault Tolerance and Recovery

| Capability | LangGraph | CrewAI | OpenAI Agents SDK |
|-----------|-----------|--------|-------------------|
| Crash recovery | Automatic via checkpointing -- resumes exactly where left off | No built-in mechanism | No built-in; long-horizon harness (Apr 2026) emerging |
| Mid-execution pause/resume | Native (`interrupt()` + `Command(resume=...)`) | Not supported | Sessions: conversational continuity only |
| State durability | Postgres/SQLite/Redis checkpointers | Ephemeral; Mem0 for external | Session backends for conversation; no workflow state |
| Side-effect safety | Checkpoint replay can re-trigger side effects (user must handle idempotency) | Same issue with memory replay | Not addressed |
| Time-travel debugging | Built-in: rewind to any checkpoint, inspect state, modify logic, replay | Not available. Enterprise: execution traces for post-hoc inspection, but no replay | Not available |

**LangGraph time-travel debugging** deserves emphasis: you can rewind to a previous checkpoint, inspect the exact state at any step, modify the graph logic, and replay execution from that point. This is the only framework with this capability and it is critical for debugging complex multi-agent loops in production.

**Horizontal scaling patterns**:
- **LangGraph**: PostgresSaver enables multiple stateless workers to share threads. The `interrupt()`/resume pattern is inherently distributed -- any worker can resume a paused thread. PgBouncer in transaction pooling mode is essential between app and Postgres.
- **CrewAI**: Single-process per crew (open-source). CrewAI Factory provides containerized deployment for horizontal scaling.
- **OpenAI SDK**: Teams pair the SDK with **Temporal** for durable, horizontally-scaled workflows. Temporal child workflows handle multi-agent handoffs with configurable timeout and retry policies.

### 4.3 Enterprise Security

**Authentication, authorization, and compliance**:

| Capability | LangGraph | CrewAI | OpenAI Agents SDK |
|-----------|-----------|--------|-------------------|
| AuthN/AuthZ | Agent Authorization (beta, all tiers); SSO + RBAC on Enterprise | None (open-source); RBAC + SSO in Enterprise AMP | No built-in; application-level |
| SOC 2 Type II | Yes (via LangSmith) | Enterprise only | Via OpenAI platform |
| HIPAA | Yes (BAA on Enterprise tier) | Not documented | Via OpenAI platform (BAA available) |
| GDPR | Yes | Enterprise only | Via OpenAI platform |
| Audit logging | LangSmith: full decision traces, node-by-node. HIPAA auditors have cited these as the artifact they needed | Console output (open-source). Enterprise AMP: immutable audit trails, PII redaction hooks | Built-in tracing with visual DAGs, OpenTelemetry export, `trace_include_sensitive_data` flag |

**Multi-tenancy**:
- **LangGraph**: Not natively advertised. Implement via infrastructure isolation (VPCs, namespace separation) and RBAC. Pinecone namespaces for vector isolation alongside LangGraph.
- **CrewAI**: Open-source has no concept of teams or scoped API keys. Enterprise AMP adds identity, role-based filtering, and cost attribution per principal.
- **OpenAI SDK**: Application responsibility entirely.

**Deployment options**:

| Option | LangGraph | CrewAI | OpenAI Agents SDK |
|--------|-----------|--------|-------------------|
| Self-hosted | Yes (Enterprise) | Yes (Factory) | Yes (any infra) |
| Managed cloud | LangGraph Cloud SaaS | CrewAI AMP Cloud | OpenAI platform |
| BYOC | Yes (AWS) | Hybrid (Factory for sensitive, Cloud for non-sensitive) | No managed BYOC |
| VPC deployment | Enterprise plan | Factory (containerized) | Application responsibility |

### 4.4 Tool Sandboxing

- **LangGraph**: No built-in sandboxing. Tools run in-process. Isolation via Docker, E2B, or similar is the user's responsibility.
- **CrewAI**: Code Interpreter tool's Docker sandbox and SandboxPython fallback were both removed after CVE-2026-2275/2287. Current guidance points to external sandboxes (E2B, Daytona).
- **OpenAI Agents SDK**: Native sandbox execution added April 2026 (E2B/Modal/Daytona). Agents work within siloed workspaces accessing only explicitly relevant files.

### 4.5 Known Security Vulnerabilities (2025-2026)

**LangGraph/LangChain** ("LangDrained" coordinated disclosure, March 2026, Cyera Research):

| CVE | Vulnerability | Patch Version |
|-----|--------------|---------------|
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

**OpenAI Agents SDK**: No publicly disclosed CVEs as of September 2026.

---

## 5. Production Enterprise Code

### 5.1 Scenario: Customer Support Workflow

The same workflow implemented in all three frameworks: a customer message is triaged, routed to either a billing specialist or a technical support specialist, and a structured response is returned.

#### LangGraph Implementation

```python
"""
Customer support workflow with conditional routing, tool nodes, and checkpointing.
Requires: pip install langgraph langgraph-checkpoint-postgres psycopg2-binary
"""
from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.prebuilt import ToolNode


# --- State schema ---

class SupportState(TypedDict):
    messages: Annotated[list[dict], operator.add]
    category: str  # "billing" | "technical" | "unknown"
    resolution: str
    customer_id: str


# --- Tools ---

def lookup_invoice(customer_id: str, invoice_id: str) -> dict:
    """Look up invoice details for a customer."""
    # Production: call billing API
    return {
        "invoice_id": invoice_id,
        "amount": 149.99,
        "status": "paid",
        "date": "2026-09-15",
    }


def search_knowledge_base(query: str) -> str:
    """Search the technical support knowledge base."""
    # Production: call vector search API
    return f"KB result for '{query}': Check firmware version >= 3.2.1. If issue persists, escalate to L2."


# --- Node functions ---

def triage(state: SupportState) -> dict:
    """Classify the customer message into billing or technical."""
    last_message = state["messages"][-1]["content"].lower()

    # Production: replace with LLM classification call
    if any(word in last_message for word in ["invoice", "charge", "refund", "bill", "payment"]):
        return {"category": "billing"}
    elif any(word in last_message for word in ["error", "bug", "crash", "not working", "broken"]):
        return {"category": "technical"}
    return {"category": "unknown"}


def billing_agent(state: SupportState) -> dict:
    """Handle billing inquiries."""
    customer_id = state["customer_id"]
    # Production: extract invoice_id from messages via LLM
    invoice = lookup_invoice(customer_id, "INV-2026-0042")
    resolution = (
        f"Invoice {invoice['invoice_id']}: ${invoice['amount']}, "
        f"status={invoice['status']}, date={invoice['date']}. "
        f"How can I help with this invoice?"
    )
    return {
        "resolution": resolution,
        "messages": [{"role": "assistant", "content": resolution}],
    }


def technical_agent(state: SupportState) -> dict:
    """Handle technical support inquiries."""
    last_message = state["messages"][-1]["content"]
    kb_result = search_knowledge_base(last_message)
    resolution = f"Technical support: {kb_result}"
    return {
        "resolution": resolution,
        "messages": [{"role": "assistant", "content": resolution}],
    }


def fallback_agent(state: SupportState) -> dict:
    """Handle unclassified inquiries."""
    resolution = "I'll connect you with a human agent who can help with your request."
    return {
        "resolution": resolution,
        "messages": [{"role": "assistant", "content": resolution}],
    }


# --- Routing function ---

def route_by_category(state: SupportState) -> Literal["billing_agent", "technical_agent", "fallback_agent"]:
    """Route to the appropriate specialist based on triage classification."""
    category = state.get("category", "unknown")
    if category == "billing":
        return "billing_agent"
    elif category == "technical":
        return "technical_agent"
    return "fallback_agent"


# --- Graph construction ---

def build_support_graph(checkpointer=None):
    graph = StateGraph(SupportState)

    # Register nodes
    graph.add_node("triage", triage)
    graph.add_node("billing_agent", billing_agent)
    graph.add_node("technical_agent", technical_agent)
    graph.add_node("fallback_agent", fallback_agent)

    # Wire edges
    graph.add_edge(START, "triage")
    graph.add_conditional_edges("triage", route_by_category)
    graph.add_edge("billing_agent", END)
    graph.add_edge("technical_agent", END)
    graph.add_edge("fallback_agent", END)

    return graph.compile(checkpointer=checkpointer)


# --- Usage ---

if __name__ == "__main__":
    # Development: in-memory checkpointer
    from langgraph.checkpoint.memory import InMemorySaver

    app = build_support_graph(checkpointer=InMemorySaver())

    result = app.invoke(
        {
            "messages": [{"role": "user", "content": "I was overcharged on my last invoice"}],
            "customer_id": "CUST-12345",
            "category": "",
            "resolution": "",
        },
        config={"configurable": {"thread_id": "support-001"}},
    )
    print(f"Category: {result['category']}")
    print(f"Resolution: {result['resolution']}")

    # Production: swap to PostgresSaver
    # DB_URI = "postgresql://user:pass@localhost:5432/langgraph_checkpoints"
    # with PostgresSaver.from_conn_string(DB_URI) as checkpointer:
    #     checkpointer.setup()  # Creates tables on first run
    #     app = build_support_graph(checkpointer=checkpointer)
    #     result = app.invoke(...)
```

#### CrewAI Implementation

```python
"""
Customer support workflow with role-based agents and task delegation.
Requires: pip install crewai crewai-tools
"""
from crewai import Agent, Crew, Task, Process
from crewai.tools import tool


# --- Tools ---

@tool("Invoice Lookup")
def lookup_invoice(customer_id: str, invoice_id: str) -> str:
    """Look up invoice details for a customer. Returns invoice summary."""
    # Production: call billing API
    return (
        f"Invoice {invoice_id}: $149.99, status=paid, date=2026-09-15, "
        f"customer={customer_id}"
    )


@tool("Knowledge Base Search")
def search_knowledge_base(query: str) -> str:
    """Search the technical support knowledge base for solutions."""
    # Production: call vector search API
    return f"KB result for '{query}': Check firmware version >= 3.2.1. If issue persists, escalate to L2."


# --- Agents ---

triage_agent = Agent(
    role="Customer Support Triage Specialist",
    goal="Accurately classify customer inquiries into billing or technical categories and provide initial context for the specialist.",
    backstory=(
        "You are a senior triage specialist with 10 years of experience in customer support. "
        "You can quickly identify whether a customer issue relates to billing/payments or "
        "technical problems. You always classify before routing."
    ),
    verbose=False,
    allow_delegation=False,
    max_iterations=3,
)

billing_agent = Agent(
    role="Billing Support Specialist",
    goal="Resolve billing inquiries by looking up invoices and providing clear, actionable responses.",
    backstory=(
        "You are a billing specialist who handles invoice disputes, refund requests, "
        "and payment issues. You always look up the relevant invoice before responding."
    ),
    tools=[lookup_invoice],
    verbose=False,
    allow_delegation=False,
    max_iterations=5,
)

technical_agent = Agent(
    role="Technical Support Engineer",
    goal="Resolve technical issues by searching the knowledge base and providing step-by-step solutions.",
    backstory=(
        "You are a L1 technical support engineer. You search the knowledge base for "
        "solutions and provide clear troubleshooting steps. You escalate to L2 when "
        "the KB does not have a definitive answer."
    ),
    tools=[search_knowledge_base],
    verbose=False,
    allow_delegation=False,
    max_iterations=5,
)


# --- Tasks ---

def build_support_crew(customer_message: str, customer_id: str) -> Crew:
    triage_task = Task(
        description=(
            f"Classify this customer message into 'billing' or 'technical':\n\n"
            f"Customer ID: {customer_id}\n"
            f"Message: {customer_message}\n\n"
            f"Respond with ONLY the category: 'billing' or 'technical'."
        ),
        expected_output="A single word: 'billing' or 'technical'",
        agent=triage_agent,
    )

    billing_task = Task(
        description=(
            f"Handle this billing inquiry from customer {customer_id}:\n\n"
            f"Message: {customer_message}\n\n"
            f"Look up the customer's most recent invoice and provide a helpful response."
        ),
        expected_output="A clear, professional response addressing the billing concern with invoice details.",
        agent=billing_agent,
    )

    technical_task = Task(
        description=(
            f"Handle this technical support request from customer {customer_id}:\n\n"
            f"Message: {customer_message}\n\n"
            f"Search the knowledge base and provide troubleshooting steps."
        ),
        expected_output="Step-by-step troubleshooting instructions based on knowledge base results.",
        agent=technical_agent,
    )

    # Sequential: triage first, then both specialists run (only one produces
    # the relevant output). In production, use a Flow with conditional routing
    # to avoid running the irrelevant specialist.
    return Crew(
        agents=[triage_agent, billing_agent, technical_agent],
        tasks=[triage_task, billing_task, technical_task],
        process=Process.sequential,
        verbose=False,
    )


# --- Usage ---

if __name__ == "__main__":
    crew = build_support_crew(
        customer_message="I was overcharged on my last invoice",
        customer_id="CUST-12345",
    )
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

from pydantic import BaseModel
from agents import (
    Agent,
    GuardrailFunctionOutput,
    InputGuardrail,
    OutputGuardrail,
    Runner,
    function_tool,
    handoff,
)


# --- Tools ---

@function_tool
def lookup_invoice(customer_id: str, invoice_id: str) -> str:
    """Look up invoice details for a customer."""
    # Production: call billing API
    return (
        f"Invoice {invoice_id}: $149.99, status=paid, date=2026-09-15, "
        f"customer={customer_id}"
    )


@function_tool
def search_knowledge_base(query: str) -> str:
    """Search the technical support knowledge base for solutions."""
    # Production: call vector search API
    return (
        f"KB result for '{query}': Check firmware version >= 3.2.1. "
        f"If issue persists, escalate to L2."
    )


# --- Output schema ---

class SupportResponse(BaseModel):
    category: str
    resolution: str
    escalation_needed: bool


# --- Guardrails ---

async def check_pii_in_input(ctx, agent, input_text: str) -> GuardrailFunctionOutput:
    """Block requests containing sensitive PII patterns."""
    import re
    # Check for SSN, credit card patterns
    pii_patterns = [
        r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
        r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",  # Credit card
    ]
    for pattern in pii_patterns:
        if re.search(pattern, input_text):
            return GuardrailFunctionOutput(
                output_info={"reason": "PII detected in input"},
                tripwire_triggered=True,
            )
    return GuardrailFunctionOutput(
        output_info={"reason": "No PII detected"},
        tripwire_triggered=False,
    )


# --- Agents ---

billing_agent = Agent(
    name="Billing Specialist",
    instructions=(
        "You are a billing support specialist. Look up the customer's invoice "
        "and resolve billing inquiries. Always provide invoice details in your response. "
        "Use the lookup_invoice tool with the customer ID from the conversation."
    ),
    tools=[lookup_invoice],
    output_type=SupportResponse,
)

technical_agent = Agent(
    name="Technical Support",
    instructions=(
        "You are a technical support engineer. Search the knowledge base for solutions "
        "and provide step-by-step troubleshooting instructions. Set escalation_needed=true "
        "if the KB result suggests L2 escalation."
    ),
    tools=[search_knowledge_base],
    output_type=SupportResponse,
)

triage_agent = Agent(
    name="Support Triage",
    instructions=(
        "You are a customer support triage agent. Analyze the customer's message and "
        "hand off to the appropriate specialist:\n"
        "- Billing issues (invoices, charges, refunds, payments) -> Billing Specialist\n"
        "- Technical issues (errors, bugs, crashes, not working) -> Technical Support\n"
        "Do NOT try to resolve the issue yourself. Hand off immediately."
    ),
    handoffs=[
        handoff(agent=billing_agent),
        handoff(agent=technical_agent),
    ],
    input_guardrails=[
        InputGuardrail(guardrail_function=check_pii_in_input),
    ],
)


# --- Usage ---

async def handle_support_request(message: str) -> SupportResponse:
    result = await Runner.run(
        triage_agent,
        input=message,
    )
    return result.final_output


if __name__ == "__main__":
    import asyncio

    response = asyncio.run(
        handle_support_request("I was overcharged on my last invoice")
    )
    print(f"Category: {response.category}")
    print(f"Resolution: {response.resolution}")
    print(f"Escalation needed: {response.escalation_needed}")
```

### 5.2 Migration Patterns

**CrewAI to LangGraph** (most common migration path):

Teams prototype in CrewAI, validate the concept, then hit CrewAI's control-flow ceiling. The migration requires rethinking from role-based to graph-based:

| CrewAI Concept | LangGraph Equivalent |
|----------------|---------------------|
| Agent (role + goal + backstory) | Node function (with system prompt containing role/goal) |
| Task | Node function's logic + expected output validation |
| Crew (sequential) | Linear chain of edges: A -> B -> C -> END |
| Crew (hierarchical) | Conditional edges from a router node |
| Flow | Parent StateGraph with subgraph nodes |
| Memory (short-term) | State fields with append reducers |
| Memory (long-term) | Store (cross-thread) |
| `allow_delegation=True` | Conditional edge routing based on state |

**OpenAI SDK to LangGraph**: Triggered when teams need model flexibility (beyond OpenAI-compatible APIs) or advanced state management beyond sessions. Map each agent to a node, each handoff to a conditional edge, and add explicit state typing.

**Version upgrade notes**: LangGraph's pre-1.0 to 1.0 migration broke patterns like `AgentExecutor` and `compile(recursion_limit=...)`. Pin LangGraph and LangChain versions together; upgrade in lockstep.

### 5.3 Common Pitfalls and Solutions

**LangGraph pitfalls**:

| Pitfall | Symptom | Solution |
|---------|---------|----------|
| Parallel nodes updating the same state field | Disappearing messages, empty `tool_calls`, inconsistent state | Annotate with a merge reducer: `Annotated[list, operator.add]`. Add tests for fan-out branches |
| State corruption on non-head checkpoints | Updating old checkpoint inherits future values | Always `get_state()`, copy, modify, then submit the complete dictionary |
| Schema evolution breaking existing threads | Paused threads fail on resume after redeployment | Use `state.get("field_name", default)` for backward compatibility. Never rename/remove a node while threads are paused at it |
| Recursion limit confusion | Graph runs fewer steps than expected | Official docs conflict (1000 vs 25 default). Set explicitly per invocation |
| Checkpointer state bloat | Postgres disk usage grows rapidly | Store references to large artifacts, not artifacts themselves |

**CrewAI pitfalls**:

| Pitfall | Symptom | Solution |
|---------|---------|----------|
| Delegation loops (hierarchical mode) | Manager bounces tasks between agents with overlapping roles | Make agent roles mutually exclusive. Set `max_iterations=7`. Disable delegation when not needed |
| Hallucination compounding | Error rate climbs beyond 4 sequential tools per agent | `temperature=0.1`, strict `expected_output` JSON schemas, `max_tokens` per response. Reduced failures 12% -> 3% across 1K runs |
| Context window overflow | Accuracy drops below 60% | Keep crews under 200 tasks. Split into multiple crews via Flows |
| Manager token overhead | 30% of token budget consumed by coordination | Use sequential mode unless dynamic delegation is genuinely needed |
| State lost on restart | Production data disappears | Configure Mem0 with explicit `user_id` scoping |

**OpenAI Agents SDK pitfalls**:

| Pitfall | Symptom | Solution |
|---------|---------|----------|
| Non-deterministic handoff routing | Billing handoffs misfire on refund-policy questions | Add structured classification step before handoff. Use evals to catch routing drift |
| Compounding step failure | 85% per-step success x 8 steps = 27% end-to-end | Minimize handoff chain depth. Add retry logic. Monitor per-step success rates |
| No mid-execution recovery | Work lost on process crash | Pair with Temporal for durable workflows |
| Guardrail scope gaps | Intermediate agents unchecked | Add tool-specific validation at each agent, not just input/output guards |
| Input guardrails apply only to first agent | Policy violations pass through after handoff | Duplicate critical checks as tool-level guards on each specialist |

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Insurance Claims Processing Pipeline

**Problem statement**: A mid-size insurance company (500K claims/year) needs to automate their claims processing pipeline. Claims arrive as unstructured documents (PDFs, photos of damage, handwritten notes). The pipeline must: (1) extract structured data from documents, (2) classify claim type and severity, (3) check against policy coverage, (4) flag potential fraud, (5) route to human adjusters for approval above $10K, and (6) generate settlement letters. Regulatory requirement: full audit trail for every decision, HIPAA compliance for health-related claims, and ability to replay any claim's processing history for dispute resolution.

**Proposed architecture**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      CLAIMS PROCESSING PIPELINE                             │
│                                                                             │
│  ┌─────────────┐    ┌──────────────────────────────────────────────────┐   │
│  │  Document    │    │  LangGraph StateGraph                           │   │
│  │  Ingestion   │───>│                                                 │   │
│  │  (S3 + SQS)  │    │  State: ClaimState(TypedDict)                  │   │
│  └─────────────┘    │    - claim_id, documents, extracted_data,       │   │
│                     │      classification, coverage_check,            │   │
│                     │      fraud_score, adjuster_decision,            │   │
│                     │      settlement_letter, audit_trail: list       │   │
│                     │                                                 │   │
│                     │  ┌───────────┐   ┌──────────────┐              │   │
│                     │  │ extract   │──>│ classify     │              │   │
│                     │  │ (OCR +    │   │ (claim type, │              │   │
│                     │  │  LLM)     │   │  severity)   │              │   │
│                     │  └───────────┘   └──────┬───────┘              │   │
│                     │                         │                       │   │
│                     │               ┌─────────┴─────────┐            │   │
│                     │               v                   v            │   │
│                     │  ┌──────────────┐   ┌──────────────┐          │   │
│                     │  │ coverage_    │   │ fraud_detect │          │   │
│                     │  │ check        │   │ (ML model +  │          │   │
│                     │  │ (policy DB)  │   │  rules)      │          │   │
│                     │  └──────┬───────┘   └──────┬───────┘          │   │
│                     │         └─────────┬─────────┘                  │   │
│                     │                   v                            │   │
│                     │         ┌──────────────────┐                   │   │
│                     │         │ route_decision   │                   │   │
│                     │         │ (conditional     │                   │   │
│                     │         │  edge)           │                   │   │
│                     │         └────┬────────┬────┘                   │   │
│                     │              │        │                        │   │
│                     │     < $10K   │        │  >= $10K or            │   │
│                     │     no fraud │        │  fraud_score > 0.7     │   │
│                     │              v        v                        │   │
│                     │  ┌───────────┐  ┌──────────────┐              │   │
│                     │  │ auto_     │  │ human_review │              │   │
│                     │  │ approve   │  │ (interrupt() │              │   │
│                     │  │           │  │  + resume)   │              │   │
│                     │  └─────┬─────┘  └──────┬───────┘              │   │
│                     │        └────────┬───────┘                      │   │
│                     │                 v                               │   │
│                     │       ┌──────────────────┐                     │   │
│                     │       │ generate_letter  │──> END              │   │
│                     │       │ (LLM + template) │                     │   │
│                     │       └──────────────────┘                     │   │
│                     └──────────────────────────────────────────────────┘   │
│                                       │                                    │
│                     ┌─────────────────┼─────────────────┐                 │
│                     v                 v                  v                 │
│          ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐        │
│          │ PostgreSQL   │  │ LangSmith    │  │ PgBouncer        │        │
│          │ (checkpoints,│  │ (audit trail,│  │ (connection pool, │        │
│          │  claim state)│  │  compliance  │  │  transaction mode)│        │
│          │              │  │  traces)     │  │                  │        │
│          └──────────────┘  └──────────────┘  └──────────────────┘        │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix**:

| Dimension | LangGraph | CrewAI | OpenAI Agents SDK |
|-----------|-----------|--------|-------------------|
| **Audit trail** | LangSmith node-by-node traces. HIPAA auditor-ready. Time-travel replay for dispute resolution | Console only (open-source). Enterprise AMP adds audit, but $2K/month | Built-in tracing with visual DAGs. OpenTelemetry export. But no replay |
| **Human-in-the-loop** | Native `interrupt()` / `Command(resume=...)`. Any worker can resume. | Not supported. Would require external queue | Not built-in. Sessions only handle conversation continuity |
| **Parallel execution** | Fan-out coverage_check and fraud_detect in parallel, fan-in with merge reducer | Not supported in sequential mode. Hierarchical mode is unpredictable | No native parallel execution |
| **State durability** | PostgresSaver with full checkpoint history. Crash recovery automatic | Ephemeral. Mem0 external memory is per-agent, not per-claim | Sessions for conversation only. Process crash = lost work |
| **Dispute resolution** | Time-travel debugging: rewind to any step, inspect state, replay | Not possible | Not possible |
| **HIPAA compliance** | BAA available on Enterprise tier | Not documented | Via OpenAI platform (BAA available, but no workflow-level audit) |
| **Scalability** | Stateless workers + PostgresSaver. PgBouncer for connection pooling. Platform auto-scales | Single-process per crew. Would need one container per claim | Redis sessions for distributed. Temporal for durability adds complexity |

**Decision rationale**: **LangGraph** is the clear choice. The regulatory requirements (HIPAA, full audit trail, dispute resolution replay) eliminate CrewAI open-source and make OpenAI SDK insufficient without heavy external infrastructure. LangGraph's native `interrupt()` for human-in-the-loop approval gates maps directly to the ">$10K needs adjuster" requirement. Time-travel debugging is uniquely available in LangGraph and directly addresses the dispute resolution requirement. The parallel fan-out for coverage check + fraud detection is a natural graph pattern. The main cost is the 1-2 week learning curve and PostgresSaver operational overhead -- both acceptable for a system processing 500K claims/year.

### 6.2 Scenario: AI-Powered Sales Development Representative (SDR)

**Problem statement**: A B2B SaaS startup (Series A, 15-person team) needs an AI SDR to handle inbound leads. The workflow: (1) qualify inbound leads from website forms and email, (2) research the prospect's company (LinkedIn, Crunchbase, news), (3) personalize an outreach email based on the research, (4) send the email via the company's email platform, and (5) handle replies with follow-up sequences. The team uses OpenAI models exclusively, wants to ship in 2 weeks, and the founder will personally review every outbound email for the first month. Budget: minimal -- the startup is pre-revenue.

**Proposed architecture**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AI SDR SYSTEM                                       │
│                                                                             │
│  ┌─────────────┐                                                           │
│  │  Inbound     │                                                           │
│  │  Webhook     │                                                           │
│  │  (HubSpot/   │                                                           │
│  │   Typeform)  │                                                           │
│  └──────┬──────┘                                                           │
│         │                                                                   │
│         v                                                                   │
│  ┌──────────────────────────────────────────────────────────────────┐      │
│  │  OpenAI Agents SDK                                               │      │
│  │                                                                  │      │
│  │  ┌──────────────┐                                               │      │
│  │  │ Lead Triage   │  "Qualify this lead. If ICP match, hand off  │      │
│  │  │ Agent         │   to Research Agent. If not, hand off to     │      │
│  │  │               │   Rejection Agent."                          │      │
│  │  │  input guard: │                                              │      │
│  │  │  check_pii()  │                                              │      │
│  │  │  handoffs:    │                                              │      │
│  │  │  [research,   │                                              │      │
│  │  │   rejection]  │                                              │      │
│  │  └───────┬───────┘                                              │      │
│  │          │                                                       │      │
│  │    ┌─────┴──────┐                                               │      │
│  │    v            v                                                │      │
│  │  ┌────────────┐  ┌────────────┐                                 │      │
│  │  │ Research   │  │ Rejection  │                                 │      │
│  │  │ Agent      │  │ Agent      │                                 │      │
│  │  │            │  │            │                                 │      │
│  │  │ tools:     │  │ "Send a   │                                 │      │
│  │  │ [linkedin_ │  │  polite   │                                 │      │
│  │  │  lookup,   │  │  decline  │                                 │      │
│  │  │  crunchbase│  │  email"   │                                 │      │
│  │  │  _search,  │  │           │                                 │      │
│  │  │  news_     │  │ output    │                                 │      │
│  │  │  search]   │  │ guard:    │                                 │      │
│  │  │            │  │ tone_check│                                 │      │
│  │  │ handoffs:  │  └────────────┘                                 │      │
│  │  │ [writer]   │                                                 │      │
│  │  └──────┬─────┘                                                 │      │
│  │         v                                                        │      │
│  │  ┌────────────┐                                                 │      │
│  │  │ Writer     │                                                 │      │
│  │  │ Agent      │                                                 │      │
│  │  │            │                                                 │      │
│  │  │ "Write a   │                                                 │      │
│  │  │  personal- │                                                 │      │
│  │  │  ized      │                                                 │      │
│  │  │  outreach  │                                                 │      │
│  │  │  email"    │                                                 │      │
│  │  │            │                                                 │      │
│  │  │ output     │    ┌────────────────────────┐                   │      │
│  │  │ guard:     │───>│ Founder Review Queue   │                   │      │
│  │  │ brand_     │    │ (Slack notification +  │                   │      │
│  │  │ compliance │    │  approve/reject via    │                   │      │
│  │  │            │    │  reaction)             │                   │      │
│  │  └────────────┘    └───────────┬────────────┘                   │      │
│  │                                │ approved                        │      │
│  └────────────────────────────────┼─────────────────────────────────┘      │
│                                   v                                         │
│                        ┌──────────────────┐                                │
│                        │  Email Platform   │                                │
│                        │  (SendGrid /      │                                │
│                        │   Resend API)     │                                │
│                        └──────────────────┘                                │
│                                                                             │
│  Session store: SQLite (sufficient for startup scale)                       │
│  Tracing: OpenAI dashboard (free, built-in)                                │
│  Cost: ~$0.01-0.02/lead (GPT-4o-mini for triage, GPT-4o for writing)      │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix**:

| Dimension | LangGraph | CrewAI | OpenAI Agents SDK |
|-----------|-----------|--------|-------------------|
| **Time to ship** | 1-2 weeks learning curve + implementation | 3-5 days to working prototype | 2-3 days. Handoff pattern maps directly to triage -> research -> write |
| **Cost** | Free (self-hosted) + model costs. But over-engineered for this scale | Free + model costs. Reasonable fit | Free + model costs. Cheapest: triage on GPT-4o-mini, writing on GPT-4o |
| **Complexity fit** | Over-engineered. Linear triage -> research -> write does not need a graph | Good fit conceptually (role-based agents), but sequential mode means all agents run even when lead is rejected | Best fit. Handoff skips unnecessary agents. Rejection agent only runs for rejected leads |
| **Human review gate** | Native `interrupt()` | Not supported without external queue | Not built-in, but Slack webhook from output guardrail is trivial to implement |
| **Founder review UX** | Would need custom integration | Would need custom integration | Output guardrail can trigger Slack notification with approve/reject buttons |
| **Model lock-in** | Any model | Any model | Despite the name, supports any OpenAI-compatible API. But optimized for OpenAI |
| **Observability** | LangSmith (powerful but overkill here) | Console output | OpenAI dashboard (free, built-in, sufficient for startup) |
| **Scalability ceiling** | Unlimited (but startup does not need it) | 200 tasks/crew limit irrelevant at this scale | Sufficient. Redis sessions if they outgrow SQLite |

**Decision rationale**: **OpenAI Agents SDK** wins on three axes that matter most to an early-stage startup: (1) time-to-ship (2-3 days vs 1-2 weeks for LangGraph), (2) cost efficiency (handoff pattern naturally skips unnecessary agents -- rejected leads never hit the research or writer agents, saving model costs), and (3) operational simplicity (zero infrastructure beyond the application server, built-in tracing via OpenAI dashboard). The team already uses OpenAI models exclusively, removing the model-flexibility advantage of LangGraph. The founder review gate does not need LangGraph's `interrupt()` -- a Slack webhook triggered from the writer agent's output is simpler and more natural for a 15-person team. The main risk is handoff routing accuracy (the triage agent might misclassify leads), mitigated by output guardrails on the writer agent and the founder's manual review during the first month. If the company scales to enterprise (regulated industries, complex multi-step workflows), they should plan a migration to LangGraph -- but that is a Series B problem, not a Series A problem.

---

## Appendix: Emerging Convergence (2026)

With MCP standardizing how agents reach tools and A2A standardizing how agents talk to each other, the framework choice is shifting from "which library" to "which execution model matches your workload." Key signals:

- **Google ADK** is the only framework with first-class support for both MCP and A2A protocols.
- **LangGraph** and **CrewAI** are adding community integrations for both protocols.
- The protocol layer is becoming framework-agnostic -- your tool integrations and agent-to-agent communication will survive a framework migration.

The framework question that matters in an interview is not "which is best" but "which execution model (graph vs role-based vs handoff) fits the stated requirements, and what are the trade-offs of that choice given the team's constraints."
