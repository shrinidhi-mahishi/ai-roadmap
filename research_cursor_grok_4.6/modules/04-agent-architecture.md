# Module 04 — Agent Architecture

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `research_cursor_grok/research/04-agent-architecture.md` (researched 2026-08-21, 81 sources).
**Mandatory topics**: ReAct · Loops · Planning · State · Workflows.

The unit of production is not “the model thought and then called a tool.” It is a **control plane** that owns the loop budget, legal tools this turn, checkpoint key, and stop condition, wrapping a **data plane** that actually mutates the world (tool adapters, MCP `tools/call`, A2A tasks, sandboxes). Across OpenAI Agents SDK, Anthropic, Google ADK, LangGraph, CrewAI, and Bedrock AgentCore the invariant is the same: **the model does not execute tools or handoffs**. It emits a structured action; the runtime dispatches; an observation is injected; the loop continues. Anthropic’s 2024 split still holds: **workflows** = LLMs and tools on predefined code paths; **agents** = the LLM dynamically directs process and tool use. Production stacks mix both: a deterministic outer graph (control) wrapping ReAct inner loops (data-plane I/O). Interview answers that skip this split fail when the follow-up is “who stops the loop, and where does `thread_id` live?”

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns identity, policy, routing, **loop fuses** (`max_turns` / `recursion_limit` / `RemainingSteps`), graph compile (which nodes and tools exist), checkpoint dispatch, stream mux, and HITL pause. Data plane owns LLM I/O (the untrusted planner), tool HTTP, MCP servers, A2A peers, and sandboxes. Persistence is three different stores: **thread checkpointer** (HITL, time travel, crash resume), **cross-thread Store** (prefs, Reflexion episodic memory), **durable workflow history** (Temporal events / Inngest step memo) — plus a **blob store** for tool payloads that must not land in Temporal history. Tool proxies never share IAM with the planner. Telemetry is the only place turn count, `cached_tokens`, cache-write tokens, and policy decisions are authoritative.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS  (SSE / sync HTTP / Temporal Signal / Inngest waitForEvent)             │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │  TLS + correlation-id
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE                                                                   │
│                                                                                 │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ API Gateway│─▶│ Policy       │─▶│ Loop budget  │─▶│ Graph compiler        │  │
│  │ auth,quota │  │ PII redact   │  │ max_turns=10 │  │ nodes, edges, tools   │  │
│  │ RPM/TPM    │  │ tool RBAC    │  │ RemainingStp │  │ tools_condition / Send│  │
│  │ breaker    │  │ allowlist    │  │ TAO hash fuse│  │ supervisor vs ReAct   │  │
│  └────────────┘  └──────┬───────┘  └──────┬───────┘  └──────────┬────────────┘  │
│                         │                 │                     │               │
│                         │                 ▼                     │               │
│                         │          ┌────────────────┐           │               │
│                         │          │ Orchestrator   │◀──────────┘               │
│                         │          │ topology pick  │  ReAct | plan-exec | DAG  │
│                         │          │ Pregel step    │  interrupt / stream mux   │
│                         │          └───────┬────────┘                           │
└─────────────────────────┼──────────────────┼────────────────────────────────────┘
                          │                  │
                          │                  ▼
┌─────────────────────────┼───────────────────────────────────────────────────────┐
│ DATA PLANE              │  (model = planner only; side effects live here)       │
│                         │                                                       │
│  ┌────────────┐  ┌──────┴───────┐  ┌─────────────┐  ┌────────────┐  ┌────────┐  │
│  │ LLM actor  │─▶│ Action parse │─▶│ Tool proxy  │─▶│ MCP server │─▶│Sandbox │  │
│  │ thought    │  │ schema+RBAC  │  │ idempotency │  │ tools/call │  │ E2B /  │  │
│  │ ≠ env      │  │ limit caps   │  │ dup circuit │  │ audience   │  │ Firecr.│  │
│  └────────────┘  └──────────────┘  └──────┬──────┘  └────────────┘  └────────┘  │
│                                           │  observation injected               │
│  ┌────────────┐                           │                                     │
│  │ A2A peer   │◀── task lifecycle ────────┘  (other trust domain only)          │
│  │ Agent Card │                                                                 │
│  └────────────┘                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE                                                                     │
│  ┌─────────────────────┐ ┌──────────────────┐ ┌───────────────┐ ┌─────────────┐ │
│  │ Checkpointer        │ │ Store            │ │ Durable engine│ │ Blob / WORM │ │
│  │ thread_id PK        │ │ cross-thread KV  │ │ Temporal hist.│ │ tool bytes  │ │
│  │ super-step snapshot │ │ prefs, Reflexion │ │ Inngest memo  │ │ not in hist.│ │
│  │ pending writes      │ │                  │ │ Prefect tasks │ │             │ │
│  │ sync|async|exit     │ │                  │ │ Continue-As-  │ │             │ │
│  └─────────────────────┘ └──────────────────┘ │ New @ 10k evt │ └─────────────┘ │
│                                               └───────────────┘                 │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────┴──────────────────────────────────────────┐
│ TELEMETRY / OBSERVABILITY SINKS                                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌────────────────────────┐ │
│  │ Audit log   │  │ Metrics      │  │ Trace spans │  │ Usage (authoritative)  │ │
│  │ tenant,user,│  │ turns, fuse  │  │ gateway →   │  │ cached_tokens,         │ │
│  │ agent,thread│  │ hits, tool   │  │ model →     │  │ cache_write_tokens,    │ │
│  │ hashed args,│  │ latency hist │  │ tool proxy  │  │ web_search call count  │ │
│  │ decision    │  │ breaker,TPM  │  │ interrupt   │  │                        │ │
│  └─────────────┘  └──────────────┘  └─────────────┘  └────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 End-to-end request flow

1. **Ingress.** Client opens SSE (chat), sync HTTP (script), or a durable wait (Temporal Signal / Inngest `waitForEvent` resume). Gateway stamps a correlation id, authenticates, checks org+project RPM/TPM. A closed circuit on the primary provider is already a routing input. Cached tokens still count toward OpenAI TPM.
2. **Policy.** Control plane redacts PII **before** the first model call. Tool RBAC attaches only the tools this agent/task is allowed (`send_email` stays off the supervisor). Scope is **tool grain** (`mcp:tool:{name}:{read|execute}`), not server-wide admin. Separate read-MCP from write-MCP so retrieved tickets cannot instruct mail.
3. **Loop budget.** Soft fuse in state (`RemainingSteps` / `max_iterations`) routes to `END` *before* the hard error. OpenAI Agents SDK default `max_turns=10` (a **turn** = one model invocation including any tool calls that occur with it). LangGraph `recursion_limit` default **25 supersteps** — one ReAct tool cycle is typically **2 supersteps** (model node + tool node) → default ≈ **12 tool rounds**. Never `max_turns=None` in prod.
4. **Compile / pick topology.** Fixed 4-step pipeline → prompt chain / ADK `SequentialAgent` / Prefect DAG. Unknown subtasks → orchestrator-workers with hard `max_workers`. Chat + tools <10 hops → ReAct graph. Days-long approval → do not hold a request worker; park on Temporal/Inngest.
5. **Orchestrator super-step (LangGraph Pregel).** All scheduled nodes run (possibly in parallel via `Send`); reducers merge channels; then checkpoint. Concurrent writes to a key **without** a reducer → `InvalidUpdateError`. Stream tokens **and** interrupt events; do not execute on partial JSON.
6. **Model turn (data plane, no side effects).** The actor emits Thought (language, \(\mathcal{L}\), does not touch the environment) and/or Action (domain \(\mathcal{A}\)). Parser yields typed tool calls. The model never holds IAM.
7. **Dispatch.** Adapter validates schema, caps `limit`, refuses POST without an idempotency key, hashes `(tool, canonical_args)`. Identical hash N times is a **tool-loop circuit** (ReAct paper: repetitive TAO is 47% of labeled failures). MCP `tools/call` uses an audience-bound token; no passthrough.
8. **Observe and re-enter.** Observation is injected. Control plane hashes last-K `(thought, action, args)` and breaks on repeat. If `interrupt` (refund > $X, destructive tool), checkpointer must already be on; resume restarts the **node from the top** (LangGraph). Inngest HITL is `step.waitForEvent` — **zero compute** while paused.
9. **Persist.** `thread_id = f"{tenant}:{user}:{session}"` — a constant string shares history across users (documented failure). Durability `sync` before irreversible tools; `async` (LangGraph default) persists while the next step runs; `exit` loses mid-run on pod kill. Temporal: LLM tokens belong in **Activity results** so replay does not re-bill; tool bytes go to blob + handle.
10. **Emit.** Terminal event carries usage. Audit tuple: `{tenant, user, agent, thread_id, turn, tool, args hash, decision, model, cache_hit}` — hashes + redacted preview in SIEM; full payload in a customer-managed bucket with TTL.

**Interview talking point:** “The model is an untrusted planner. Loop fuses, IAM, and checkpoint keys live on the control plane. A ReAct loop is a cyclic graph; an Airflow DAG cannot express retry-until without an outer scheduler.”

### 1.3 ReAct loop vs planner vs workflow graph

Do not draw one box labeled “agent.” These three topologies have different clocks, cost functions, and fuse placement.

```
 ReAct (cyclic graph)                         Plan-and-execute
 ┌──────────┐   tools_condition               ┌──────────┐  plan is data
 │  MODEL   │────────┬──────────┐             │ PLANNER  │──────┐
 │ thought  │        │ action   │             │ 1 LLM    │      │
 │ + action │        ▼          │             └──────────┘      ▼
 └────▲─────┘   ┌─────────┐     │             ┌─────────────────────────────┐
      │         │  TOOLS  │     │             │ EXECUTOR  (optional inner   │
      │         │  MCP    │     │             │ ReAct per step; replan on   │
      │         └───┬─────┘     │             │ tool error / every K steps) │
      │  observation│           │ finish      └──────────────┬──────────────┘
      └─────────────┘           ▼                            ▼
                           ┌─────────┐                  ┌─────────┐
                           │  END    │                  │  JOIN   │
                           └─────────┘                  └─────────┘

 Workflow DAG (no back-edges)                 Orchestrator-workers (runtime fan-out)
 ┌────┐   ┌────┐   ┌────┐                     ┌──────────────┐
 │ A  │──▶│ B  │──▶│ C  │──▶ END              │ ORCHESTRATOR │ cap N (hard-code 8)
 └────┘   └──┬─┘   └────┘                     └──────┬───────┘
             │ fan-in reducer                        │ Send / ParallelAgent
             ▼                                  ┌────┼────┐
           ┌────┐                               ▼    ▼    ▼
           │ D  │                             W1   W2   W8   (narrow Haiku)
           └────┘                               │    │    │
                                                └──┬─┴─┬──┘
                                                   ▼   ▼
                                                JOIN LLM (Sonnet)
```

| Topology | Cycle? | Who picks next hop | Typical runtime |
| --- | --- | --- | --- |
| DAG (Prefect `.map`, Airflow, static LangGraph without back-edges) | No | Engineer | ETL, fixed RAG pipelines |
| Cyclic ReAct | Yes | Model + `tools_condition` | Tool-using assistants |
| Supervisor / hierarchical | Yes at manager; workers often DAGs or ReAct | Manager LLM | CrewAI `Process.hierarchical`, LangGraph supervisor node |
| Orchestrator-workers | Fan-out DAG per plan, then join | Orchestrator **at runtime** | Anthropic coding/search; ADK `ParallelAgent` |
| Plan-and-execute | Outer DAG of steps; inner ReAct optional | Planner then executor | LangChain planning agents; HuggingGPT four-stage |

LangGraph exists because **a ReAct loop is not a DAG**. Nodes compute; conditional edges (and `Send`) decide next; typed state carries memory across cycles. OpenAI two multi-agent contracts: **handoffs** (specialist owns the next user-facing reply; runner swaps agent) vs **agents-as-tools** (manager keeps the reply; specialist is a bounded capability). CrewAI: start production as a **Flow** (state, loops, conditionals); delegate islands of autonomy to Crews. ADK: `SequentialAgent` / `ParallelAgent` (same `session.state` — **distinct keys** required) / `LoopAgent`. Bedrock Classic supervisor+collaborators is **closed to new customers**; greenfield is **AgentCore** hosting your graph.

---

## 2. Core Mechanics & Algorithms

### 2.1 ReAct: Thought / Action / Observation

Yao et al. (ICLR 2023) augment the action space to \(\hat{\mathcal{A}} = \mathcal{A} \cup \mathcal{L}\): language **thoughts** do not touch the environment; domain **actions** do. Trajectory is interleaved `Thought → Action → Observation`. Thoughts decompose goals, extract from observations, inject commonsense, reformulate search, synthesize answers. QA used **dense** TAO; ALFWorld/WebShop used **sparse** thoughts (the LM decides when to think). HotpotQA/FEVER action space: `search[entity]`, `lookup[string]`, `finish[answer]` against a weak Wikipedia API — not a neural retriever.

**PaLM-540B (paper Table 1):** ReAct alone is **not** the accuracy winner. CoT-SC → ReAct / reverse is.

| Method | HotpotQA EM | FEVER Acc |
| --- | --- | --- |
| Standard | 28.7 | 57.1 |
| CoT | 29.4 | 56.3 |
| CoT-SC (21 samples, T=0.7) | 33.4 | 60.4 |
| Act-only | 25.7 | 58.9 |
| ReAct | 27.4 | 60.9 |
| CoT-SC → ReAct | 34.2 | 64.6 |
| ReAct → CoT-SC | **35.1** | 62.0 |
| Supervised SoTA (then) | 67.5 | 89.5 |

ALFWorld / WebShop: 1–2-shot ReAct beat IL/RL trained on \(10^3\)–\(10^5\) instances by **+34 pp** and **+10 pp**. Authors capped ReAct at **7 steps (HotpotQA)** and **5 (FEVER)**; extra steps recovered only 0.84% / 1.33% of already-correct trajectories — extra turns are a **cost knob**, not a quality monotone.

**When ReAct fails (human labels, 200 HotpotQA trajectories, Table 2):**

| Mode | ReAct | CoT |
| --- | --- | --- |
| Success: true positive | 94% | 86% |
| Success: false positive (hallucinated facts) | 6% | 14% |
| Failure: reasoning error (incl. **repetitive TAO loops**) | **47%** | 16% |
| Failure: empty/useless search | **23%** | n/a |
| Failure: hallucination | **0%** | **56%** |
| Failure: label ambiguity | 29% | 28% |

Grounding kills hallucination; the same interleaving **reduces reasoning flexibility** and creates the signature failure: greedy decode repeats the previous thought+action. Production implication: ReAct needs an **external** loop breaker; the model will not reliably stop itself. Empty search (23%) derails later thoughts — backoff ReAct → CoT-SC when the adapter returns empty, rather than paging forever.

**Production mapping:** ReAct ≈ LangGraph `bind_tools` + `ToolNode` + `tools_condition`; OpenAI Agents SDK `Runner` loop; Anthropic autonomous agent (tools in a loop with max iterations).

### 2.2 Loop fuses — five distinct clocks

Do not collapse these. They have different meters, stop conditions, and cost functions.

**A. Max-iteration / recursion (control-plane fuse).**

| Runtime | Unit | Default | Error | Conversion trap |
| --- | --- | --- | --- | --- |
| OpenAI Agents SDK | **turn** = 1 model invocation (incl. its tool calls) | `max_turns=10` | `MaxTurnsExceeded` | “Turn” ≠ LangGraph super-step |
| LangGraph | **superstep** | `recursion_limit=25` | `GraphRecursionError` | 1 ReAct cycle ≈ 2 supersteps → ~12 tool rounds |
| ADK `LoopAgent` | sequential sub-agent runs | docs example `max_iterations=5` | `escalate=True` from any sub-agent | Escalate is the *intended* stop, not an error |
| CrewAI hierarchical | manager↔worker messages | none unless `allow_delegation=False` on workers | ping-pong unbounded | Both-ways delegation is a fuse bug |

Raise `recursion_limit` only when the work is genuinely long (`graph.invoke(..., {"recursion_limit": 100})`). Pair with `RemainingSteps` that routes to `END` **before** the hard error — the hard error is an incident, not a product path.

**B. Tool loops (data-plane inner ReAct).** Same tool + same canonical args, or pagination-by-LLM (`page=1` forever). Adapter must: cap `limit`, return `is_error` on 4xx except 429, refuse POST without idempotency key, treat identical `(tool, canonical_args)` N times as a circuit. Hash last K TAO triples in the control plane as a second fuse.

**C. Human-in-the-loop.** Pause without burning a GPU/worker. LangGraph `interrupt(value)` **requires** a checkpointer; resume `Command(resume=...)`; node restarts from the top; multiple interrupts match resume values **by order**. OpenAI Agents SDK: first-class HITL + `AbortSignal`; Temporal TS waits on Signal/Update then Continue-As-New. Inngest AgentKit: `step.waitForEvent` (example 4h, `match: "data.ticketId"`) — **zero compute**. Losing `waitForEvent` inside `group.parallel()` is **not cancelled** until timeout — keep timeouts tight. CrewAI: `@human_feedback` + `@persist`.

**D. Event loops (runtime, not ReAct).** ADK Runner is ask–yield: user message + session id → internal events → streamed events. Inngest functions are event-triggered; Temporal workflows are event-sourced (history replay). These loops outlive a single LLM call.

**E. Streaming loops.** Mux tokens **and** tool/interrupt events. LangGraph modes: `values`, `updates`, `messages`, `custom`, `checkpoints`, `tasks`, `debug`; v1.2+ independent iterators (`stream.messages`, `stream.interrupts`). OpenAI `Runner.run_streamed()`; handoff `input_filter` **does not stream**; server-managed conversations **do not support** handoff input filters. Temporal TS `{ stream: true }` is labeled **experimental**. Do not execute tools on partial JSON.

### 2.3 Planning: plan-and-execute, hierarchical, dynamic replan

Same family as ReAct, different **control graphs**:

| Variant | Control topology | What changes vs interleaved ReAct | Named numbers (paper, not prod SLOs) |
| --- | --- | --- | --- |
| Act-only | Action→Obs | No thoughts | HotpotQA 25.7 vs ReAct 27.4 EM |
| ReAct → CoT-SC / reverse | Sequential backoff | External KB **or** internal majority vote | Best HotpotQA **35.1 EM** |
| Plan-and-Solve / PS+ | Plan then execute in **one** generation | PS+ adds variable extraction + intermediate calc | PS+ vs Zero-shot-CoT: MultiArith **91.8**, GSM8K **59.3** (+2.9 pp); CSQA 71.9 vs 65.2 |
| Plan-and-Execute (LangChain) | Planner LLM + executor ReAct | Plan is **data**; executor walks steps; replanner optional | Architectural, not a single bench |
| ReWOO | Planner → Worker(s) → Solver | Thoughts **decoupled** from observations; blueprint then tool burst | **5×** token efficiency, **+4 pp** HotpotQA vs interleaved ALMs |
| HuggingGPT | Plan → select HF models → execute → summarize | LLM as controller over modality-specific models | NeurIPS 2023 four-stage pipeline |
| LLMCompiler | Streamed DAG + task-fetch + joiner/replan | Parallel function DAG; args can be `$1` from prior tasks | Up to **3.7×** latency, **6.7×** cost, **~9 pp** accuracy vs ReAct; **1.35×** vs OpenAI parallel FC |
| Tree of Thoughts | BFS/DFS over thought nodes | Lookahead + backtrack; not tool-centric | Game of 24: GPT-4 CoT **4%** vs ToT **74%** |
| LATS | MCTS over ReAct steps | LM as actor, value, reflection | HumanEval GPT-4 pass@1 **92.7%**; WebShop GPT-3.5 avg **75.9** |
| Reflexion | Actor + Evaluator + Self-Reflection + episodic buffer | Verbal RL across **trials**, not within one trajectory | HumanEval pass@1 **91%** vs GPT-4 **80%** |

**Plan hallucination** is the planner-family failure mode: orchestrator emits 40 useless workers; frozen plan contradicts new observations; PS+ still has **27%** semantic-error share on GSM8K misses (a plan does not fix misunderstanding); HuggingGPT selects the wrong HF model; XML/JSON parse fail (Anthropic cookbook). Mitigations: **dynamic replanning** every K steps or on tool error (LLMCompiler’s Joiner exists for this); structured plan schema with **max N subtasks and a cost cap**; evaluator-optimizer with a **grounded** stop (unit tests, not another LLM vibe); gate on the evaluator **before** fan-out. Reflexion helps **across trials** (coding with tests). It does **not** stop a single bad plan from spending N workers **this** request unless you gate first.

Anthropic workflow patterns (plus agents): (1) prompt chaining, (2) routing, (3) parallelization — **sectioning** or **voting**, (4) orchestrator-workers (subtasks **not** known a priori), (5) evaluator-optimizer. Google Cloud architecture center: single-agent first; multi-agent when one model’s context/tools degrade. Anthropic: add agents only when evals show workflows fail.

### 2.4 State: checkpointing, reducers, threads

**LangGraph state** = `TypedDict` / Pydantic. Channels default to **LastValue** (overwrite). `Annotated[list, operator.add]` (or a custom reducer) **merges** — required for messages and for parallel fan-in. `Send(node, state)` from a conditional edge is **dynamic fan-out** with per-child state (map-reduce); fan-in is a reducer on a shared channel.

**Threads:** `configurable.thread_id` is the primary key. No `thread_id` ⇒ no save, no interrupt resume. Production: `thread_id = f"{tenant}:{user}:{session}"`.

**Checkpoint grain:** full `StateSnapshot` at each **super-step**; **task-level writes** as nodes finish so a sibling failure does not re-run successful parallel nodes (**pending writes**). Time travel resumes at super-step boundaries, not mid-node. `update_state` creates a **new** checkpoint; reducers still apply.

**Durability modes** (`sync` | `async` | `exit`):

| Mode | When persist | Failure |
| --- | --- | --- |
| `sync` | **before** next step | Slowest; required for payment / irreversible tools |
| `async` (**default**) | while next step runs | Kill -9 in the window can lose the last snapshot |
| `exit` | only when the graph exits | Less duplication; **lose** mid-run on pod kill |

**Checkpointer vs Store:** checkpointer = short-term **thread** memory (HITL, time travel, crash). Store = long-term **cross-thread** KV (prefs, facts, Reflexion buffer). Subgraphs do not automatically share parent checkpoints.

**DeltaChannel (beta):** sentinel in checkpoint blobs; reconstruct by replaying ancestor writes through a **deterministic, batching-invariant** reducer. Snapshot when update count hits `snapshot_frequency` **or** supersteps since snapshot hit `DELTA_MAX_SUPERSTEPS_SINCE_SNAPSHOT` (**default 5000**). Makes accumulating `messages` O(1) blob size per step instead of O(N). On-disk format **not stable**. Non-associative reducers ⇒ replay ≠ live state.

**Backends:** MemorySaver (dev; dies with process), SqliteSaver (single box; write lock), PostgresSaver (prod). Field reports: Postgres write **~5–15 ms**, **~3–8 ms** with asyncpg pool — ⚠️ not a vendor SLO. Raw `Connection` holds the connection for the **entire run** → timeouts; use `ConnectionPool`; TTL + `exit` to control disk.

**ADK `session.state` prefixes:** (none)=this session; `user:`=all sessions for `user_id` in `app_name`; `app:`=all users; `temp:`=this invocation (shared down the tree). Mutate via `CallbackContext` / `ToolContext` / `output_key` / `EventActions.state_delta` so `append_event` persists. Direct `Session.state[...] =` **bypasses** the event log → lost updates. `DatabaseSessionService`: **per-session lock**; `InMemorySessionService` **not** multi-thread safe.

**OpenAI Agents SDK Sessions:** default `Runner.run` is **stateless** across calls; attach a Session (Redis in production guides). `trace_include_sensitive_data=False` strips LLM I/O and tool args from traces.

**CrewAI `@persist`:** resume `kickoff(inputs={"id": ...})`; `restore_from_state_id` forks a new `state.id`.

**Reducer invariants (interview):** (1) parallel `Send`s must merge with an associative, commutative reducer or use distinct keys; (2) LastValue + two writers in one super-step is a bug (`InvalidUpdateError`); (3) schema version lives in checkpoint metadata; Store holds facts that must outlive a thread.

### 2.5 DAG vs cyclic, state machines, complexity

**State machine (single ReAct thread):**

```
                    ┌─────────────┐
         resume     │ IDLE / HITL │  waitForEvent / interrupt (zero GPU)
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
              ┌────▶│ MODEL_TURN  │  1 OpenAI "turn" / 1 LangGraph model node
              │     └──────┬──────┘
              │      thought│ / action / finish
              │            ▼
              │     ┌─────────────┐     finish / remaining=0 / hash-repeat
              │     │  DISPATCH   │────────────────────────────▶ TERMINAL
              │     └──────┬──────┘                              done|fused|
              │       tool │                                      degraded
              │            ▼
              │     ┌─────────────┐     destructive?
              │     │ TOOL_PROXY  │──── yes ──▶ INTERRUPT (checkpointer required)
              │     │ idempotent  │
              │     └──────┬──────┘
              │            ▼
              │     ┌─────────────┐
              └─────│  OBSERVE    │  inject; TAO hash; remaining_steps -= 1
                    └─────────────┘
```

**Complexity.**

- Sequential ReAct: \(\Theta(T)\) serial LLM calls. Prompt tokens without prefix cache \(\approx T \cdot P + \sum_{t} (o_t + \mathrm{obs}_t)\) — **quadratic-ish cost in turns**. With a frozen prefix, uncached growth is \(\Theta(\sum \mathrm{new}_t)\); cache writes 1.25× input on GPT-5.6+.
- LangGraph super-step: \(\Theta(N_{\mathrm{scheduled}})\) parallel node work, then \(O(C)\) reducer merges, then 1 checkpoint write (~5–15 ms field). Fan-out `Send` is map-reduce; join cost is the reducer, not another LLM, until a join node exists.
- Plan-and-execute / ReWOO: 1 planner + tool burst **outside** the LLM + 1 solver. Paper claim **5×** tokens vs interleaved ALMs.
- LLMCompiler DAG: wall-clock up to **3.7×** vs sequential ReAct on their benches; cost **6.7×** vs ReAct by not re-sending the full trajectory every hop.
- ToT: \(O(b^{d})\) LLM calls (thought samples × depth × value). Research spend, not a chat SKU.
- Reflexion: \(N_{\mathrm{trials}} \times\) ReAct cost + reflection tokens in the next context.
- Temporal history: warn **10,240 events / 10 MB**; terminate **51,200 / 50 MB**. 500 KB tool result × 100 tools ≈ 50 MB — **blob offload is an algorithm**, not an ops afterthought.

**Invariants worth stating in an interview.**

1. Model never executes tools; it emits actions.
2. `max_turns=10` and `recursion_limit=25` are **different units**; converting requires nodes per tool cycle.
3. Checkpointer ≠ Store ≠ Temporal history ≠ MCP session ≠ prompt cache.
4. No `thread_id` ⇒ no checkpoint, no HITL resume.
5. Reducers for fan-in; distinct keys under ADK `ParallelAgent`.
6. Honor 429; break on 5xx; retry **exactly one** layer (SDK **or** gateway). Nested 3×3×3 = 27 upstream calls.
7. Extra ReAct turns are the dominant cost knob; a mutating prefix (shuffled tools, timestamp in system) zeros cache hit rate.
8. Dynamic replanning is the difference between LLMCompiler/HuggingGPT and a stale plan that walks off a cliff.

---

## 3. Token Economics & NFR Analysis

Prices as of **2026-08-21** from vendor docs in the research file. `$ per 1k executions` figures are **[inferred]** from published token rates × stated loop depths, not vendor SKUs. ⚠️ OpenAI/Anthropic/Google do **not** publish p50/p95/p99 for *agent loops*; percentiles below are **[inferred]** from the compositional model \(T_{\mathrm{loop}} \approx \sum_i (\mathrm{TTFT}_i + T_{\mathrm{decode},i} + T_{\mathrm{tool},i})\) plus paper speedups. Do not put an unlabeled p95 in an architecture review.

### 3.1 Cost per 1k runs

**OpenAI** per 1M tokens, short context: gpt-5.6-sol $5 / $0.50 cached-in / $6.25 cache-write / $30 out; terra $2 / $0.20 / $2.50 / $12; luna $0.20 / $0.02 / $0.25 / $1.20. Cache writes **1.25×** uncached input on GPT-5.6+; cached input still counts toward **TPM**. Web search **$10 / 1k calls**; file search **$2.50 / 1k** + $0.10/GB-day. Regional processing **+10%** on models released on/after 2026-03-05.

**Anthropic:** Sonnet 5 $2 / $10 out / $2.50 5-min cache write / $0.20 cache read per MTok. Haiku 4.5 $1 / $5 / $1.25 / $0.10. Prompt caching: 5-min write **1.25×**, 1-hour write **2×**, read **0.10×**. Batch **−50%**. Managed Agents: **$0.08 per session-hour** active runtime + tokens. Web search **$10 / 1k**.

**Support-agent assumptions [inferred]:** 8k-token frozen prefix (system + tools), 400 output tokens/turn, 600 new input tokens/turn, `gpt-5.6-terra`, cache hit on prefix from turn 2, short context. Formula: \(\$ = (\mathrm{uncached}_M \times 2) + (\mathrm{cached}_M \times 0.20) + (\mathrm{out}_M \times 12)\). First turn writes cache at $2.50/M: **8k × $2.50/M = $0.020** once per cold prefix (**+$20 / 1k** cold starts).

| Turns | Uncached in | Cached in | Out | **$ / run [inferred]** | **$ / 1k [inferred]** |
| --- | --- | --- | --- | --- | --- |
| 1 (no tools) | 8.0k | 0 | 0.4k | 0.0208 | **21** |
| 3 (2 tools) | 9.2k | 16k | 1.2k | 0.0356 | **36** |
| 10 (SDK default cap) | 13.4k | 72k | 4.0k | 0.0872 | **87** |
| 25 (LangGraph default fuse counted as 25 model calls) | 22.4k | 192k | 10.0k | 0.203 | **203** |

If the prefix mutates (tool-list shuffle, timestamp in system prompt), cache hit rate → 0 and the 10-turn row jumps to **[inferred] ~$0.22/run ($220/1k)** because all 8k×10 are billed at $2.

**Anthropic Sonnet 5, 5-min cache, 10-turn:** **[inferred] ~$0.074/run ($74/1k)** plus one write $0.020. Orchestrator-workers: 1 Sonnet plan + N Haiku workers + 1 Sonnet synthesize. For N=8, 2k in / 800 out each: **[inferred] \(2\times(2\mathrm{k}\times\$2 + 0.8\mathrm{k}\times\$10) + 8\times(2\mathrm{k}\times\$1 + 0.8\mathrm{k}\times\$5) = \$0.040 + \$0.048 = \$0.088\)/run** — cheaper than 10-turn ReAct **if** workers stay narrow. Cost multiplier vs one Sonnet call is why Anthropic says start with workflows.

**ReWOO / LLMCompiler vs ReAct:** papers claim **5× tokens** (ReWOO) and **6.7× cost** (LLMCompiler) by not re-sending the full trajectory to the planner every tool hop. Production: planner on a cached prefix; execute tools **outside** the LLM; join once.

**ToT / LATS:** many LLM calls per puzzle. Treat as research spend. Reflexion = **N trials × ReAct** plus reflection tokens.

**Capacity sketch [inferred].** Support bot, 1k conversations/day, mix 70% 1-turn luna, 25% 3-turn terra, 5% 10-turn terra, 80% prefix cache hit, ignore tools’ own SaaS fees:

`0.7×1000×$0.0025 [inferred luna 8k+400] + 0.25×1000×$0.036 + 0.05×1000×$0.087 ≈ $1.75 + $9 + $4.4 ≈ **$15/day model**`.

Add Anthropic Managed Agents if used: 1k sessions × 2 min active = 33.3 session-hours × $0.08 = **$2.67/day**. Tools: 200 web searches × $10/1k = **$2**. Order-of-magnitude **~$20/day** before retries, evals, and 5% sol escalations. A runaway **25-turn terra** fleet at 1k/day is **~$203/day** — **10×** — which is why max-turns is a **financial** control, not just a correctness fuse.

**Routing (cost control plane):** Haiku/luna for easy inner workers, Sonnet/terra for supervisor, Opus/sol for the 5% hard tail. Each hop is a full request against **that model’s** RPM/TPM. OpenAI org T5: **$200k/mo** usage cap; unofficial secondary compilation lists GPT-5.6 sol/terra T5 **15k RPM / 40M TPM**, luna T5 **30k / 180M** — console wins. Cache is a **capacity** feature: 1k concurrent agents × 8k prefix/turn × 4 turns/min = **32M TPM** uncached — over T5 sol 40M with no headroom. 90% hit → ~3.2M uncached + 28.8M cached (still counts toward TPM; **cost** drops ~10× on input).

### 3.2 Latency SLA targets and mitigations

⚠️ No vendor agent-loop SLO. Compositional **[inferred]** table uses explicit assumptions: TTFT p50 0.4 s / p95 1.2 s / p99 3 s; 400-token decode p50 1.6 s; tool adapter p50 0.15 s / p95 0.8 s / p99 5 s. These are **not** OpenAI/Anthropic/Google numbers.

| Path | p50 **[inferred]** | p95 **[inferred]** | p99 **[inferred]** | Mitigation |
| --- | --- | --- | --- | --- |
| 1-turn, no tools | ~2.0 s (TTFT+decode) | ~2.8 s | dominated by TTFT p99 (~3 s + decode) | Frozen prefix cache; luna/Haiku for extract |
| 3-turn, 2 tools (serial ReAct) | ~3×2.0 + 2×0.15 ≈ **6.3 s** | slowest tool p95 + longest decode | **slowest tool p99 + longest decode**, not mean TTFT | Cap tools; parallel DAG (LLMCompiler **3.7×** wall-clock on paper benches) |
| 10-turn SDK cap, serial | ~linear in tools ≈ **20 s+** | one hung tool | one hung tool or long decode | `max_turns=8`; RemainingSteps; tool deadline < parent |
| Fan-out N=8 + join | ~max(worker) + join LLM | max(worker p95) + join | **max(worker p99) + join** | Hard `max_workers=8`; per-child deadline |
| Voting ×N | ~one call if parallel | ~one call | ~one call | Cost ×N linear; do not vote on the hot path |
| HITL refund approval | n/a (paused) | n/a | **human SLA** (Inngest example **4 h**; support ref **24 h**) | `waitForEvent` / Temporal Signal; zero GPU while paused |
| Checkpoint write | +5–15 ms/step ⚠️ field | — | — | Pool; TTL; do not put blobs in the snapshot |
| Streaming TTFT vs final | first token ≠ loop done | clients that treat TTFT as SLO will page | partial tokens then failover = duplicate speech | Buffer vs commit policy explicit |

LLMCompiler: up to **3.7×** wall-clock vs sequential ReAct on their benches (parallel function DAG). Sequential ReAct p99 is **not** average TTFT. Fan-out p99 ≈ max(worker p99) + join. Measure in LangSmith / Agents SDK traces / ADK traces: turn count, cached_tokens, cache_write_tokens, tool latency histogram.

### 3.3 Throughput and back-pressure

| Signal | Why it is the limiter | Back-pressure action |
| --- | --- | --- |
| Org/project TPM (cached tokens **count**) | 1k agents × 8k × 4 turns/min = 32M TPM uncached vs T5 sol 40M | Admit on **uncached + cached TPM**, not QPS; freeze prefix; luna inner workers |
| RPM per model | Each ReAct hop is a full request against **that** SKU | Supervisor on terra, workers on luna; do not stampede sol |
| 429 / `Retry-After` | **Your quota**, not provider outage | Honor `Retry-After` / `x-ratelimit-*` / `anthropic-ratelimit-*-reset`; **do not** trip the provider circuit (you replicate the spike). Billing 429 → halt spend |
| Checkpoint IOPS | 1k concurrent ReAct × 2 supersteps/turn × 4 turns/min ≈ **8k writes/min** | PostgresSaver + pool + TTL; SqliteSaver will lock |
| Temporal history growth | warn 10,240 events / 10 MB; kill 51,200 / 50 MB | Continue-As-New before 10k events; blob offload; `useLocalActivity: true` keeps history smaller |
| Inngest parallel wait losers | not cancelled until timeout | Tight `waitForEvent` timeouts |
| Gateway in-flight | 10-turn × 2 s hold vs 1-turn | Durable execution for long loops; sync HTTP only for short DAGs |
| TrueFoundry 3-layer gateway | token bucket per `(user, repo, model)` | Pattern breaker: identical-prompt loop, cost velocity, consecutive 429s, >50% errors/60s |

Prefect example: LLM retries 3× backoff 1s/2s/4s, tools 2×, 60s timeout — **cap** LLM Activities; unbounded retry × $30/M output = bill shock. LangGraph `RetryPolicy` defaults: `initial_interval=0.5`, `backoff_factor=2`, `max_attempts=3`, `jitter=True`; does **not** parse `Retry-After`.

### 3.4 Availability, RPO/RTO, compliance, explicit NFR trade-offs

| NFR | Target / meaning | Trade-off |
| --- | --- | --- |
| Availability | 99.9% **control plane** (gateway, orchestrator, checkpointer). Model provider is a **dependency** unless multi-vendor fallback | Multi-vendor: cost + output-distribution drift; fallback chain must still emit schema-valid output |
| RPO | Irreversible tools: **0** — `sync` durability / Activity complete **before** next side effect. Prompt cache: minutes (OpenAI GPT-5.6 TTL 30 min; Anthropic 5 min refresh-on-hit) | Treating prompt cache as RPO=0 over-provisions nothing useful; treating MemorySaver as RPO=0 **re-bills tools** after pod kill |
| RTO | Interactive: fail over < 1 s to secondary model (half-open probe). Long jobs: resume from checkpointer / Temporal history; **do not** re-run tools | Fast failover vs identical answers (temperature>0). `exit` durability: RTO looks fast and is wrong |
| Consistency | Tool side effects: **exactly-once via idempotency keys**. Model text: at-least-once retry may change tokens — cache the sampled `ModelTurn` | Cannot have bit-identical retry on T>0 |
| Durability | LangGraph super-step + pending writes; Temporal Activities for LLM/tools (replay does not re-call the API); Inngest `step.run` memo | Compose: **LangGraph (cognition) inside Temporal/Inngest/Prefect (durability)** |
| Compliance | Regional +10%; trajectory is a **regulated artifact** (prompts, thoughts, tool args, observations, plan text). Thoughts copy PII from observations — GDPR/HIPAA like prompts | Seat-based Anthropic Enterprise (audit, SCIM, HIPAA-ready) is **not** a substitute for your trajectory store |
| Cost vs latency | 10-turn terra **[inferred] $87/1k** vs 1-turn **$21/1k** vs cache-broken 10-turn **$220/1k** vs runaway 25-turn **$203/1k** | Extra turns are the knob; ToT/LATS on the hot path fails both |
| Control vs autonomy | Workflows for known steps (SLO < 3 s); agents when evals show workflows fail | Unbounded ReAct = infinite spend + 47% loop failure mode |
| Consistency vs availability | Sticky `thread_id` + session lock vs multi-replica Sqlite | Sqlite under multi-worker = lost checkpoints |

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution — Temporal / Inngest / Kafka vs LLM graphs

| System | Abstraction | Checkpoint grain | Pause/HITL | Replay | Best at |
| --- | --- | --- | --- | --- | --- |
| LangGraph | Cyclic typed `StateGraph` | Super-step snapshot + per-task writes | `interrupt` | Time travel / fork | Agent reasoning + mixed deterministic nodes |
| Temporal | Workflow + Activities | Event history | Signal / Update | Deterministic workflow replay; Activities **not** re-executed | Months-long agents; exactly-once side effects |
| Inngest | Functions + `step.run` | Per-step memo | `waitForEvent` / `sleep` | Replay function; completed steps cached | Serverless; HITL days; wrap LangGraph inside a step |
| Prefect 3 | Flows + tasks | Task run state | UI retry / pause | Skip completed tasks on retry | Data + `PrefectAgent` wrapping pydantic-ai |
| CrewAI Flow | Event-driven methods | `@persist` / `flow_uuid` | `@human_feedback` | Resume or `restore_from_state_id` fork | Productized Crews with an outer Flow |
| ADK SessionService | Event log + `state` prefixes | Event + `state_delta` | LongRunning tools / Go 2.0 HITL | Reconstruct from session history | Cloud Run / Agent Runtime |

**Compose:** LangGraph (cognition) inside Temporal/Inngest/Prefect (durability). Temporal × OpenAI Agents SDK **GA 2026-03-23**: orchestration in Workflow, model calls as Activities so replay does not re-bill tokens. `useLocalActivity: true` keeps history smaller. Continue-As-New passes latest state into a new RunId with empty history. **Do not** put full tool payloads / screenshots in Activity **return values**.

**Kafka (data-plane bus, not an agent runtime).** Commit the offset **after** the tool/LLM Activity succeeds; otherwise crash-retry duplicates. Combine with idempotency keys. Produce **intent** (`tool_call` + key) **before** the side effect (outbox). Poison messages → DLQ after N; do not block the partition. Many teams Temporal/Inngest already replace this bus.

**Locking:** ADK DB session lock; LangGraph Postgres row-level; Sqlite file lock. ParallelAgent / LangGraph fan-out: **reducer or distinct keys**.

### 4.2 Failure taxonomy

| Class | Examples | Handler |
| --- | --- | --- |
| Transient | HTTP 429, 503, TLS reset, 5xx/529, timeout, mid-stream drop | 429: honor `Retry-After`; **do not** fail over (quota). 5xx/timeout: breaker + jittered retry **one layer**. Retry idempotent model reads; do not retry unknown POSTs |
| Permanent | HTTP 400 illegal schema, mapping `"done"` vs `"end"`, unsupported graph compile, billing 429 | Fail the turn; fix schema/route. Halt spend on billing 429 |
| Poison pill | Identical TAO hash N times; same payload crashes parser every time; pagination-by-LLM; empty tool error string so the model retries; recursive manager↔worker delegation | Hash last-K TAO; adapter duplicate circuit; DLQ; never auto-replay; never `max_turns=None` |
| Semantic / plan hallucination | Schema-valid 40-worker fan-out; frozen plan vs new obs; empty search 23% derails thoughts | Cap N; replan on tool error; ReAct→CoT-SC on empty search; evaluator gate **before** fan-out |
| Idempotency miss | Crash after tool success, before checkpoint; Kafka commit before Activity; `exit` durability + OOM | `key = hash(tenant, thread_id, tool, canonical_args, turn)`; store result; pending writes so siblings are not re-run |

**Layer × failure (research §5.6):**

| Failure | ReAct loop | LangGraph | Temporal/Inngest | MCP/tools |
| --- | --- | --- | --- | --- |
| Infinite loop | Repeat TAO (47% of labeled ReAct failures) | `recursion_limit` | Workflow loop without Continue-As-New | Duplicate `tools/call` |
| State drift | Context overflow | Missing reducer; shared `thread_id` | History vs blob split | Session vs token identity |
| Lost checkpoint | Process death | MemorySaver / `exit` | History 50 MB **terminates** | MCP session hijack |
| Plan hallucination | Bad thought | Supervisor node | Activity input **is** the bad plan | Wrong tool selected |
| Timeout cascade | N sequential tools | Super-step wait | Activity retry × children | Downstream 504 |

**Cascading timeouts.** Gateway p99 explosion; Temporal `start_to_close` 60 s vs 120 s LLM; nested retries; streaming already flushed then failover duplicates speech. Mitigations: one retry layer; child deadline < parent; hedge only on **idempotent** GETs; streaming failover policy explicit (buffer vs commit); Inngest tight waits; LangGraph pending writes so a timed-out sibling does not redo the whole super-step.

### 4.3 Circuit breaker and fallback chain

Per downstream (primary LLM, secondary LLM, each MCP server):

- **Closed:** traffic flows; consecutive 5xx/timeout or error-rate window trips to **open**. 429 does **not** trip the **provider** circuit (exception: billing 429 → halt).
- **Open:** fail fast vs waiting full LLM timeout; start a timer (e.g. 30 s). Interactive traffic routes to fallback.
- **Half-open:** allow a probe (one request or a small percentage). Success → closed; fail → open.

TrueFoundry 3-layer: (1) token bucket per `(user, repo, model)`; (2) pattern breaker (identical-prompt loop, cost velocity, consecutive 429s, >50% errors/60s); (3) fallback chain **primary → cheaper model → semantic cache → 503**.

Deterministic / degraded fallback must still emit **valid structured output** (status=`degraded`, last checkpoint attached) so downstream parsers do not crash. Do not fall back from a typed agent result to free-form text on a parser path. Inngest/Temporal retries must **cap** LLM Activities.

LangGraph on node failure with checkpointer: pending writes kept; checkpoint does not advance.

### 4.4 Enterprise security

**Zero-Trust MCP (spec 2025-11-25).** MCP server = OAuth 2.1 **resource server**; clients use RFC 9728 Protected Resource Metadata; AS metadata RFC 8414 or OIDC; **RFC 8707 resource indicators**; **PKCE mandatory**; no implicit/password grants.

Hard rules:

- **No token passthrough** to downstream APIs (confused deputy). Token-exchange / separate client-credentials per hop.
- Audience-validate: token for `mcp.other.com` must fail even if the signature is valid.
- Scopes at **tool** grain (`mcp:tool:{name}:{read|execute}`), not server-wide admin (CSA agentic MCP; OWASP Agentic **ASI01 Agent Goal Hijack** via poisoned tool descriptions).
- DPoP (RFC 9449) for write/admin-class tokens where SDKs allow.
- Separate **read MCP** from **write MCP**.
- AgentCore: platform-layer auth for MCP + Lambda + KB in one turn; policies checked with automated reasoning (IAM/S3 lineage).

MCP = agent → tools (`tools/list`, `tools/call`). A2A = agent → agent: Agent Card, **task lifecycle**, streaming, push notifications (spec 1.0.0; Linux Foundation). A2A is **not** a replacement for MCP. In-process dispatch is cheaper; use A2A when the peer is a **different trust domain / vendor / language**. Mixing MCP tokens with A2A task identity is how you get confused deputies.

**Tool RBAC and sandbox.**

| Control | Where enforced | Notes |
| --- | --- | --- |
| Tool allowlist per agent role | Control plane (graph compile / `Agent.tools`) | Supervisor must not inherit worker destructive tools |
| Argument schema + adapter caps | Data plane | Model cannot pass `limit=10e9` |
| Human approval | HITL interrupt / Inngest wait | Destructive tools only |
| Sandbox | E2B / Firecracker / Bedrock code interpreter / Anthropic $0.05/h container | CPU/mem/egress limits |
| Network egress allowlist | Sidecar / AgentCore | SSRF from tool URLs |

CrewAI / ADK: tools at **agent** and **task** level — prefer **task-level** for least privilege.

**PII: detect → redact → audit.** A trajectory is a regulated artifact. LangSmith: `create_anonymizer` regex; `LANGSMITH_HIDE_INPUTS/OUTPUTS`; OTEL collector transform to strip `gen_ai.prompt` / `gen_ai.completion` **before** SaaS. OpenAI Agents SDK: `trace_include_sensitive_data=False` keeps span topology, drops I/O. Audit tuple in SIEM: hashes + redacted preview; full payload in customer-managed bucket with TTL. **Thoughts are not safe to log unredacted** — they copy PII from observations.

**Immutable logs / chain of custody.** Persist `{tenant, user, agent, thread_id, turn, tool, schema-valid args hash, decision, model, cache_hit}` to WORM object store or Kafka compacted by `thread_id`. Workflow event history is a second copy. Reconstruct: policy snapshot + model id + sampled turn + tool results + human interrupt. Anthropic Enterprise audit/SCIM/Compliance API is seat+usage, not your store.

---

## 5. Production Enterprise Code

Stdlib-only runtime: full-jitter retries, circuit breaker (closed → open → half-open), primary → cheaper → cache → degraded fallback, correlation-id JSON logs, PII detect→redact→audit, ReAct/graph loop with `max_iter`, checkpoint dict, message reducer, TAO-hash fuse, duplicate-tool circuit, RemainingSteps route to END. Run: `python agent_runtime.py`.

```python
#!/usr/bin/env python3
"""Agent control-plane primitives (stdlib only). Run: python agent_runtime.py"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol

# --- correlation-id JSON logs -------------------------------------------------

class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "thread_id": getattr(record, "thread_id", None),
            "superstep": getattr(record, "superstep", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class CorrelationAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs


def build_logger(correlation_id: str, tenant: str, thread_id: str) -> CorrelationAdapter:
    base = logging.getLogger("agent.runtime")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    return CorrelationAdapter(
        base, {"correlation_id": correlation_id, "tenant": tenant, "thread_id": thread_id}
    )


# --- PII detect → redact → audit ---------------------------------------------

_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
)


def redact_pii(text: str) -> tuple[str, list[dict[str, str]]]:
    audit: list[dict[str, str]] = []
    out = text
    for label, pat in _PII_PATTERNS:
        def _sub(m: re.Match[str], _label: str = label) -> str:
            digest = hashlib.sha256(m.group(0).encode()).hexdigest()[:12]
            token = f"<{_label}:{digest}>"
            audit.append({"type": _label, "placeholder": token})
            return token
        out = pat.sub(_sub, out)
    return out, audit


# --- failures, breaker, jittered retries -------------------------------------

class TransientError(Exception):
    def __init__(self, msg: str, retry_after: float | None = None, quota: bool = False) -> None:
        super().__init__(msg)
        self.retry_after = retry_after
        self.quota = quota


class PermanentError(Exception):
    pass


class PoisonPillError(Exception):
    pass


class CircuitOpenError(Exception):
    pass


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
        half_open_max: int = 1,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.half_open_max = half_open_max
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def _maybe_half_open(self) -> None:
        if self._state is BreakerState.OPEN and (time.monotonic() - self._opened_at) >= self.recovery_seconds:
            self._state = BreakerState.HALF_OPEN
            self._half_open_inflight = 0

    def allow(self) -> None:
        with self._lock:
            self._maybe_half_open()
            if self._state is BreakerState.OPEN:
                raise CircuitOpenError("circuit open")
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_inflight >= self.half_open_max:
                    raise CircuitOpenError("half-open probe in flight")
                self._half_open_inflight += 1

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._half_open_inflight = 0
            self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = 0


def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    base_seconds: float = 0.5,
    max_seconds: float = 8.0,
) -> Any:
    """LangGraph-shaped policy: initial 0.5s, backoff 2, max_attempts 3, full jitter.
    Honors Retry-After. Caller must not nest another retry layer."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            cap = min(max_seconds, base_seconds * (2**i))
            sleep_s = max(cap, exc.retry_after or 0.0)
            time.sleep(random.random() * sleep_s)
    assert last is not None
    raise last


# --- reducers + checkpoint dict ----------------------------------------------

def reduce_concat(left: list[Any], right: list[Any]) -> list[Any]:
    return list(left) + list(right)


def reduce_last(left: Any, right: Any) -> Any:
    return right


REDUCERS: dict[str, Callable[[Any, Any], Any]] = {
    "messages": reduce_concat,
    "tao_hashes": reduce_concat,
    "pii_audit": reduce_concat,
    "remaining_steps": reduce_last,
    "status": reduce_last,
    "plan": reduce_last,
}


def merge_writes(base: dict[str, Any], writes: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in writes.items():
        reducer = REDUCERS.get(key, reduce_last)
        out[key] = reducer(out.get(key, [] if reducer is reduce_concat else None), value)
    return out


@dataclass
class Checkpoint:
    thread_id: str
    superstep: int
    state: dict[str, Any]
    pending_writes: dict[str, Any] = field(default_factory=dict)


class Checkpointer:
    def __init__(self) -> None:
        self._snaps: dict[str, list[Checkpoint]] = {}
        self._lock = threading.Lock()

    def put(self, cp: Checkpoint) -> None:
        with self._lock:
            self._snaps.setdefault(cp.thread_id, []).append(cp)

    def latest(self, thread_id: str) -> Checkpoint | None:
        with self._lock:
            seq = self._snaps.get(thread_id) or []
            return seq[-1] if seq else None


def empty_state(max_iter: int) -> dict[str, Any]:
    return {
        "messages": [],
        "tao_hashes": [],
        "pii_audit": [],
        "remaining_steps": max_iter,
        "status": "running",
        "plan": None,
    }


# --- model / tools ------------------------------------------------------------

@dataclass
class FunctionCall:
    id: str
    name: str
    arguments: dict[str, Any]
    thought: str


@dataclass
class ModelTurn:
    text: str | None
    tool_calls: list[FunctionCall]
    finish: bool
    cached_hit: bool = False


class ModelClient(Protocol):
    name: str

    def complete(self, messages: list[dict[str, str]]) -> ModelTurn:
        ...


class ToolProxy:
    def __init__(self, executors: dict[str, Callable[[dict[str, Any]], Any]], dup_limit: int = 2) -> None:
        self._executors = executors
        self._done: dict[str, Any] = {}
        self._dup_counts: dict[str, int] = {}
        self._dup_limit = dup_limit
        self._lock = threading.Lock()

    def execute(
        self,
        call: FunctionCall,
        *,
        tenant: str,
        thread_id: str,
        turn_index: int,
        allowed: set[str],
        method: str = "GET",
    ) -> dict[str, Any]:
        if call.name not in allowed:
            raise PermanentError(f"rbac deny {call.name}")
        if method == "POST" and "idempotency_key" not in call.arguments:
            raise PermanentError("POST refused without idempotency_key")
        limit = call.arguments.get("limit")
        if isinstance(limit, (int, float)) and limit > 100:
            raise PermanentError("adapter cap: limit>100")
        canonical = json.dumps(call.arguments, sort_keys=True, separators=(",", ":"))
        dup_key = f"{call.name}|{canonical}"
        idemp = hashlib.sha256(
            f"{tenant}|{thread_id}|{call.name}|{canonical}|{turn_index}".encode()
        ).hexdigest()
        with self._lock:
            if idemp in self._done:
                return self._done[idemp]
            self._dup_counts[dup_key] = self._dup_counts.get(dup_key, 0) + 1
            if self._dup_counts[dup_key] > self._dup_limit:
                raise PoisonPillError(f"duplicate tool circuit {dup_key}")
        raw = self._executors[call.name](call.arguments)
        result = {"call_id": call.id, "name": call.name, "payload": raw, "idempotency_key": idemp}
        with self._lock:
            self._done[idemp] = result
        return result


# --- fallback chain ----------------------------------------------------------

@dataclass
class SemanticCache:
    _store: dict[str, ModelTurn] = field(default_factory=dict)

    def get(self, key: str) -> ModelTurn | None:
        return self._store.get(key)

    def put(self, key: str, turn: ModelTurn) -> None:
        self._store[key] = turn


class FallbackChain:
    def __init__(
        self,
        primary: ModelClient,
        secondary: ModelClient,
        cache: SemanticCache,
        breaker: CircuitBreaker,
        log: CorrelationAdapter,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.cache = cache
        self.breaker = breaker
        self.log = log

    def complete(self, messages: list[dict[str, str]]) -> ModelTurn:
        key = hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()
        hit = self.cache.get(key)
        if hit is not None:
            self.log.info("semantic_cache_hit")
            return hit

        def _primary() -> ModelTurn:
            self.breaker.allow()
            try:
                turn = self.primary.complete(messages)
            except TransientError as exc:
                if exc.quota:
                    self.log.info("quota_429_no_breaker")
                    raise
                self.breaker.record_failure()
                raise
            except CircuitOpenError:
                raise
            except Exception:
                self.breaker.record_failure()
                raise
            self.breaker.record_success()
            return turn

        try:
            turn = retry_call(_primary)
            self.cache.put(key, turn)
            return turn
        except (TransientError, CircuitOpenError) as exc:
            self.log.info("fallback_secondary", extra={"reason": type(exc).__name__})
            try:
                turn = self.secondary.complete(messages)
                self.cache.put(key, turn)
                return turn
            except Exception as inner:
                self.log.info("fallback_degraded", extra={"reason": type(inner).__name__})
                raise PermanentError("fallback_exhausted") from inner


# --- ReAct / graph loop ------------------------------------------------------

def tao_fingerprint(thought: str, name: str, arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{thought}|{name}|{canonical}".encode()).hexdigest()


class GraphRunner:
    """Pregel-shaped: model node + tool node, 2 supersteps per ReAct cycle.
    Soft fuse RemainingSteps routes to END before a hard max_iter error."""

    def __init__(
        self,
        llm: FallbackChain,
        tools: ToolProxy,
        checkpointer: Checkpointer,
        allowed: set[str],
        log: CorrelationAdapter,
        tenant: str,
        thread_id: str,
        max_iter: int = 8,
        hash_repeat: int = 2,
        durability: str = "sync",
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.checkpointer = checkpointer
        self.allowed = allowed
        self.log = log
        self.tenant = tenant
        self.thread_id = thread_id
        self.max_iter = max_iter
        self.hash_repeat = hash_repeat
        self.durability = durability

    def _checkpoint(self, superstep: int, state: dict[str, Any], pending: dict[str, Any]) -> None:
        if self.durability == "exit":
            return
        self.checkpointer.put(Checkpoint(self.thread_id, superstep, dict(state), dict(pending)))

    def invoke(self, user_text: str) -> dict[str, Any]:
        prior = self.checkpointer.latest(self.thread_id)
        state = dict(prior.state) if prior else empty_state(self.max_iter)
        redacted, audit = redact_pii(user_text)
        state = merge_writes(state, {"messages": [{"role": "user", "content": redacted}], "pii_audit": audit})
        superstep = prior.superstep + 1 if prior else 0
        turn_index = 0

        while state["status"] == "running":
            if state["remaining_steps"] <= 0:
                state = merge_writes(state, {"status": "fused"})
                self.log.info("soft_fuse_remaining_steps")
                break

            # model node
            self.log.info("model_node", extra={"superstep": superstep})
            try:
                turn = self.llm.complete(state["messages"])
            except PermanentError:
                state = merge_writes(state, {"status": "degraded"})
                self.log.info("graceful_degradation")
                break

            pending: dict[str, Any] = {
                "remaining_steps": state["remaining_steps"] - 1,
                "messages": [{"role": "assistant", "content": turn.text or ""}],
            }
            if turn.finish or not turn.tool_calls:
                pending["status"] = "done"
                state = merge_writes(state, pending)
                self._checkpoint(superstep, state, pending)
                break

            state = merge_writes(state, pending)
            self._checkpoint(superstep, state, pending)
            superstep += 1

            # tool node (data plane)
            observations: list[dict[str, str]] = []
            hashes: list[str] = []
            try:
                for call in turn.tool_calls:
                    fp = tao_fingerprint(call.thought, call.name, call.arguments)
                    hashes.append(fp)
                    if state["tao_hashes"].count(fp) + hashes.count(fp) >= self.hash_repeat:
                        raise PoisonPillError("repetitive TAO")
                    result = self.tools.execute(
                        call,
                        tenant=self.tenant,
                        thread_id=self.thread_id,
                        turn_index=turn_index,
                        allowed=self.allowed,
                    )
                    payload = json.dumps(result["payload"], default=str)
                    payload, pii = redact_pii(payload)
                    observations.append({"role": "tool", "content": payload, "name": call.name})
                    state = merge_writes(state, {"pii_audit": pii})
            except PoisonPillError as exc:
                self.log.info("poison_pill", extra={"reason": str(exc)})
                state = merge_writes(state, {"status": "fused", "tao_hashes": hashes})
                self._checkpoint(superstep, state, {"status": "fused"})
                break

            state = merge_writes(state, {"messages": observations, "tao_hashes": hashes})
            self._checkpoint(superstep, state, {"messages": observations})
            superstep += 1
            turn_index += 1

        if self.durability == "exit":
            self.checkpointer.put(Checkpoint(self.thread_id, superstep, dict(state), {}))
        return state


# --- demo clients -------------------------------------------------------------

class ScriptedClient:
    def __init__(self, name: str, script: list[ModelTurn], fail_first: int = 0) -> None:
        self.name = name
        self._script = list(script)
        self._fail_first = fail_first
        self._i = 0

    def complete(self, messages: list[dict[str, str]]) -> ModelTurn:
        if self._fail_first > 0:
            self._fail_first -= 1
            raise TransientError("simulated 503", quota=False)
        if self._i >= len(self._script):
            return ModelTurn("done", [], True)
        turn = self._script[self._i]
        self._i += 1
        return turn


def _search(args: dict[str, Any]) -> dict[str, Any]:
    return {"sentences": [f"wiki:{args.get('entity', '')}", "contact jane@example.com"]}


FINISH = ModelTurn("answer", [], True)
SEARCH = ModelTurn(
    None,
    [FunctionCall("c1", "search", {"entity": "Acme"}, thought="need wiki")],
    False,
)
SEARCH_REPEAT = ModelTurn(
    None,
    [FunctionCall("c2", "search", {"entity": "Acme"}, thought="need wiki")],
    False,
)


def main() -> None:
    tenant = "acme"
    correlation_id = str(uuid.uuid4())
    thread_id = f"{tenant}:user1:sess1"
    log = build_logger(correlation_id, tenant, thread_id)
    tools = ToolProxy({"search": _search})
    saver = Checkpointer()
    breaker = CircuitBreaker(failure_threshold=2, recovery_seconds=0.05)

    primary = ScriptedClient("terra", [SEARCH, FINISH], fail_first=1)
    secondary = ScriptedClient("luna", [SEARCH, FINISH])
    chain = FallbackChain(primary, secondary, SemanticCache(), breaker, log)
    runner = GraphRunner(chain, tools, saver, {"search"}, log, tenant, thread_id, max_iter=6)
    state = runner.invoke("Refund for 123-45-6789 please")
    log.info("run_complete", extra={"superstep": saver.latest(thread_id).superstep if saver.latest(thread_id) else 0})
    assert state["status"] in {"done", "fused", "degraded"}
    assert any(a["type"] == "ssn" for a in state["pii_audit"])
    assert saver.latest(thread_id) is not None

    poison_thread = f"{tenant}:user1:sess-poison"
    poison_log = build_logger(str(uuid.uuid4()), tenant, poison_thread)
    poison_primary = ScriptedClient("terra", [SEARCH, SEARCH_REPEAT, SEARCH_REPEAT])
    poison_chain = FallbackChain(
        poison_primary, ScriptedClient("luna", [FINISH]), SemanticCache(),
        CircuitBreaker(), poison_log,
    )
    poison_state = GraphRunner(
        poison_chain, ToolProxy({"search": _search}), Checkpointer(),
        {"search"}, poison_log, tenant, poison_thread, max_iter=8, hash_repeat=2,
    ).invoke("loop me")
    assert poison_state["status"] == "fused"
    print(json.dumps({"ok": True, "status": state["status"], "poison": poison_state["status"]}))


if __name__ == "__main__":
    main()
```

**What the runtime encodes (map to research).**

| Primitive | Research rule |
| --- | --- |
| Full jitter, `attempts=3`, `base=0.5` | LangGraph `RetryPolicy`; **one** layer |
| 429/`quota=True` does not `record_failure` | Honor quota; do not trip provider breaker |
| Closed → open → half-open | 5xx/timeout fail-fast |
| Primary → secondary → degraded status | TrueFoundry chain; schema-valid degrade |
| `thread_id` checkpoint list | Super-step snapshots + pending writes |
| `reduce_concat` on `messages` / `tao_hashes` | Fan-in reducer; LastValue elsewhere |
| `remaining_steps` → `fused` | Soft fuse before `GraphRecursionError` |
| TAO hash + duplicate `(tool, args)` | 47% repetitive-TAO failure mode |
| PII placeholders in state, not raw SSN | Thoughts/obs copy PII; audit placeholders |
| `durability=sync` default in this demo | Irreversible tools; `exit` skips mid-run puts |

**Interview talking point:** retries with jitter handle 503; they do not make a repeated `search[Acme]` safe. Idempotency + TAO-hash fuse + RemainingSteps are three different failure classes. Correlation-id JSON is the reconstructable chain of custody; the checkpoint dict is the resume key.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers and component choices are from the research file.

### Scenario 1 — Multi-tenant customer support (router + policy DAG + capped ReAct + HITL)

**Problem statement.** Design a support copilot at **1k conversations/day**. Mix from the capacity sketch: 70% 1-turn, 25% 3-turn, 5% 10-turn. Policy: refund rules are **code**, not prompts. CRM is an MCP server. Refunds above threshold $X require a human who may take **hours** (Inngest example 4 h; reference topology 24 h). Success metric is resolution rate, not tokens. Must not become a 25-turn terra fleet (**[inferred] ~$203/day** vs ~$15/day model on the mixed path). Compliance: trajectory is a regulated artifact; thoughts copy PII.

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Zendesk /  │ SSE │ CONTROL PLANE                                             │
│ in-app chat│────▶│ Gateway: auth, tenant TPM, breaker, correlation-id        │
└────────────┘     │   ▼                                                       │
                   │ Router (Haiku): classify → extract | policy | specialist  │
                   │   ▼                                                       │
                   │ Policy DAG (code): refund rules, entitlements, no LLM     │
                   │   ▼                                                       │
                   │ ReAct specialist (terra/Sonnet): CRM MCP, max_turns=6     │
                   │ RemainingSteps in state; TAO-hash fuse; prefix cached     │
                   │   ▼  refund > $X                                          │
                   │ interrupt → Inngest waitForEvent 24h (zero compute)       │
                   └────┬─────────────────────────────┬────────────────────────┘
                        │                             │
                        ▼                             ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ DATA PLANE       │        │ TOOL PROXIES                 │
                   │ Haiku router     │        │ Read-MCP: tickets (audience) │
                   │ Sonnet/terra     │        │ Write-MCP: refund (HITL)     │
                   │ ReAct inner loop │        │ no token passthrough         │
                   └──────────────────┘        └──────────────────────────────┘
                        │
                        ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE / TELEMETRY                                   │
                   │ PostgresSaver thread_id=tenant:user:ticket                │
                   │ Inngest step memo for HITL; WORM audit hashes             │
                   │ metrics: turns, cache_hit, fuse_reason, refund_decision   │
                   └───────────────────────────────────────────────────────────┘
```

**Technology choices.** Anthropic-style start-with-workflows: Haiku router + deterministic policy DAG, then a **capped** ReAct specialist (max 6 turns, not SDK 10, not LangGraph 25). Frozen tool JSON + system **above** the cache breakpoint. HITL on Inngest `waitForEvent` (`match: data.ticketId`), not a held gunicorn worker. CRM via MCP resource-server OAuth (RFC 8707 audience, PKCE); separate read vs write MCP. Checkpointer Postgres + pool; `sync` durability on refund execution. Redact PII before tokenize; `trace_include_sensitive_data=False` / OTEL strip before SaaS. Fallback: terra → luna → degraded JSON `{status: degraded, last_policy_decision}`.

**Trade-off evaluation matrix.**

| Dimension | A. Unbounded ReAct (max_turns=None / 25 terra) | B. Recommended: Haiku router + policy DAG + ReAct max 6 + Inngest HITL | C. ToT/LATS or voting×5 on every ticket |
| --- | --- | --- | --- |
| Cost | 25-turn terra **[inferred] $203/1k** → **~$203/day** at 1k; cache-broken 10-turn **$220/1k** | Mix **[inferred] ~$15/day model** + $2.67 Managed Agents + $2 search ≈ **~$20/day** | ToT is research spend; voting ×5 multiplies cost linearly |
| Latency | Linear in tools; p99 = hung tool | 1-turn majority; HITL p99 = **human SLA 24 h** (not model) | Tree search worst; LLMCompiler 3.7× only helps if you actually DAG the tools |
| Ops | Fuse fires as `GraphRecursionError` in prod | Medium: two MCP servers, Inngest wait, RemainingSteps | Tree logs, no product SLO |
| Security | Supervisor inherits refund tool; thoughts logged raw | Task-level write-MCP; HITL; hashed audit; read/write split | More model power, same confused-deputy surface if MCP passthrough |
| Scalability | 32M TPM uncached at 1k concurrent × 8k × 4/min vs T5 40M | Cache + luna path; 8k checkpoint writes/min is fine on Postgres | TPM explodes with samples × depth |

**Decision rationale.** **B** is the only option that keeps the mixed 1k/day bill near **[inferred] ~$20/day**, puts refund rules in code (not a 47% loop-prone ReAct thought), and parks humans on `waitForEvent` instead of holding workers. A fails the financial fuse (10×) and the paper’s own extra-step result (0.84% recovery). C fails cost and latency; ToT’s Game-of-24 74% vs CoT 4% is not a support SKU. Outcome-based vendor pricing does not pay your tokens — max-turns is still your control.

### Scenario 2 — SWE-bench-class coding agent (inner ReAct + tests, outer Temporal, replan)

**Problem statement.** Design an enterprise coding agent: multi-file patches, **evaluator-optimizer** against unit tests, jobs that run **~40 minutes** and must survive deploys. Plan the file list up front; **replan** after a test fail. Reflexion-style memory **across attempts** must not live forever in the 128k window. Irreversible apply (open PR / merge) needs HITL. Inner loop can be 12 tool rounds; outer workflow can be hours. Do not greenfield on Bedrock Agents Classic (closed to new customers).

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ IDE / PR   │────▶│ CONTROL PLANE  Temporal workflow (GA w/ Agents SDK 2026)  │
│ bot        │     │ workflow-id = tenant:repo:pr                              │
└────────────┘     │   ▼                                                       │
                   │ PLANNER (Sonnet/terra, cached prefix): file list, max N=8 │
                   │   ▼  structured plan schema + cost cap                    │
                   │ Fan-out Activities (cap 8): per-file workers (luna/Haiku) │
                   │   ▼                                                       │
                   │ INNER ReAct + tests (evaluator-optimizer, max_turns=8)    │
                   │ ACI: absolute paths, diffs the model can write            │
                   │   ▼  tests fail → JOINER / replan (not frozen plan)       │
                   │ HITL Signal on apply_patch / open_PR                      │
                   │ Continue-As-New before 10k events; blobs in object store  │
                   └────┬─────────────────────────────┬────────────────────────┘
                        │ Activities: LLM + tools     │
                        ▼                             ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ DATA PLANE       │        │ TOOL PROXIES / SANDBOX       │
                   │ model Activities │        │ git MCP (short-lived PAT)    │
                   │ (result = tokens)│        │ pytest in Firecracker/E2B    │
                   │ replay-safe      │        │ egress allowlist; no SSRF    │
                   └──────────────────┘        └──────────────────────────────┘
                        │
                        ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE                                               │
                   │ Temporal history = control; LLM tokens in Activity result │
                   │ LangGraph PostgresSaver for inner graph time-travel       │
                   │ Store: Reflexion episodic notes across trials (not 128k)  │
                   │ Kafka optional: agent.turns / agent.dlq (offset after ok) │
                   └───────────────────────────────────────────────────────────┘
```

**Technology choices.** Anthropic ACI finding: absolute paths and diffs the model can write mattered more than the outer prompt — invest in the tool surface. Inner loop: ReAct + **tests as grounded stop** (evaluator-optimizer), not an LLM-vibe critic. Outer: Temporal so a 40-minute job survives deploys; model calls as Activities (replay does not re-bill). Plan-and-execute for the file list; **Joiner/replan** after test fail (LLMCompiler lesson; HuggingGPT without replan walks off a cliff). Reflexion memory in **Store**, keyed by `repo:test_id`, not stuffed into the graph blob. AgentCore Runtime is the AWS 2026 host if you want a managed runtime for **your** LangGraph/CrewAI graph; Classic MAC is maintenance. Fan-out `max_workers=8` hard-coded; do not let the LLM pick 200. History: blob handles only; Continue-As-New before 10,240 events.

**Trade-off evaluation matrix.**

| Dimension | A. Single-process LangGraph ReAct, MemorySaver, frozen plan | B. Recommended: Temporal outer + capped inner ReAct + tests + Store Reflexion + replan | C. Bedrock Agents Classic MAC or unbounded orchestrator-workers |
| --- | --- | --- | --- |
| Cost | Crash re-bills tools; frozen plan spends N workers; 10-turn **[inferred] $87/1k**, 25-turn **$203/1k** | Activities save re-billed tokens; ReWOO-like plan-once + tool burst; N=8 Haiku **[inferred] $0.088/run** vs 10-turn ReAct if workers stay narrow | Classic closed to new customers; unbounded N is plan hallucination |
| Latency | 40 min job dies on deploy → restart from turn 0 | Activity overhead; HITL Signal; inner p99 = slowest test + decode | Supervisor+collaborators extra hops; no months-long HITL story |
| Ops | Lowest until the first OOM (`exit` durability) | Temporal UI + Continue-As-New runbooks; two state stores (history vs checkpointer) | AWS-only; migration tax off Classic |
| Security | Broad git tools on one agent; apply without HITL | Task-level tools; sandbox; Signal on apply; audience-bound MCP | Platform auth (AgentCore) is better — use **Runtime**, not Classic |
| Scalability | Sqlite/memory dies; history unbounded in-process | 1k idle HITL workflows ≈ cheap (no worker CPU); history growth is the constraint | AgentCore Memory branching for parallel specialists is the 2026 scale path |

**Decision rationale.** **B** is the only option that simultaneously (1) survives a 40-minute deploy window without re-billing, (2) uses tests as a grounded evaluator so Reflexion does not fan-out a bad plan **this** request, and (3) keeps cross-trial memory in Store so the 128k window does not become the database. A fails lost-checkpoint (HITL never resumes; pod restart re-runs tools) and plan hallucination (no Joiner). C: do not design greenfield on Classic; if AWS is required, AgentCore Runtime hosts **your** graph with Gateway MCP and branching Memory. Dynamic replanning every test-fail is the interview sound-bite that separates LLMCompiler from a stale plan-and-execute.

---
