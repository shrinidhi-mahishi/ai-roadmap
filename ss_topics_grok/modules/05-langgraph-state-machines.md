# Module 05 — Master One Framework: LangGraph State Machines

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `ss_topics_grok/research/05-langgraph-state-machines.md` (researched 2026-09-23, 98 sources). Vendor list prices used in §3 are from [`01-python-llm-foundations.md`](01-python-llm-foundations.md) (2026-09-23). ReAct thought–action–observation math, paper tables, and loop-fuse comparisons live in [`04-agent-loop-patterns.md`](04-agent-loop-patterns.md) — **do not recopy them**. JSON Schema / dispatcher IDs / Stripe idempotency live in [`03-tool-calling.md`](03-tool-calling.md). Prefix-stability and the tool-schema tax live in [`02-context-engineering.md`](02-context-engineering.md).
**Mandatory topics**: StateGraph · Pregel super-steps · typed channels / reducers / `add_messages` · `Command` / `Send` · checkpointer vs Store · `interrupt` HITL · streaming · `create_agent` vs raw graph · durability · LangSmith Deployment (Agent Server).

The model **never executes tools, edges, interrupts, or checkpoints**. It emits structured actions or text. **Pregel** schedules actors in bulk-synchronous super-steps. **`ToolNode`** (or your node) runs side effects. The **checkpointer** commits at super-step (and optionally task) boundaries. Collapsing `recursion_limit` (super-steps), OpenAI `max_turns` (model invocations), and CrewAI process type into one “framework iteration cap” is how teams ship unbounded `Send` width or a silent “Sorry, need more steps…” on HTTP 200.

LangGraph **1.0 GA** was announced **2025-10-22** (GitHub tag `1.0.0` **2025-10-17**). **MIT**, **Python ≥3.10**, usable without the rest of LangChain as a *framework* while still depending on `langchain-core` (PyPI `langgraph` 1.2.x: `langchain-core>=1.4.7,<2`). `MessageGraph` and `create_react_agent` are **deprecated in 1.0, removal in 2.0**.

---

## What Is This?

**LangGraph** is a **Pregel state machine** for agents: compile a `StateGraph[State]` into a `Pregel` instance whose nodes are actors, whose keys are **channels** with reducers, and whose ticks are **super-steps** with optional Postgres checkpoints, cross-thread Store, `interrupt()` HITL, `Send` map-reduce, and subgraphs. **LangSmith Deployment** (formerly LangGraph Platform) is the Agent Server that leases **one run per `thread_id`**, injects the production checkpointer, and serves `/mcp`. CrewAI **roles** and OpenAI Agents SDK **handoffs** are different control planes — brief contrast only in §1.3.

## Why It Matters

A 6-node ticket workflow with **3** LLM calls (3k in / 800 out) is **[inferred] $37.20 / 1k** on GPT-4.1 list ($2 / $8 per 1M). The same skeleton with `Send` **N=8** workers plus planner and synthesizer (**10** LLM calls, **one** fan-out super-step) is **[inferred] $124 / 1k**. Platform compute at an assumed 2 vCPU-s/run is **[inferred] ~$0.04 / 1k** — **tokens dominate**. Agent Server default **`N_JOBS_PER_WORKER=10`**; a second invoke on the same `thread_id` with `reject` is **HTTP 409**. `recursion_limit` default is **1000 super-steps since 1.0.6** (the **25** you remember is the old LangChain runnable default — **not** a subgraph nesting cap). Dedicated Small always-on is **[inferred] ~$390/mo**. Get the reducer, the thread lease, and `interrupt()` restart semantics wrong and you drop 7 of 8 `Send` results, double-send email on resume, or lose days of HITL in `InMemorySaver`.

## Interview traps (fail these, fail the round)

- `{"configurable": {"recursion_limit": 50}}` — `recursion_limit` is a **top-level** invoke key. Putting it inside `configurable` is **ignored**.
- Treating **25** as a subgraph **nesting** cap. Nested subgraphs consume **parent super-steps**; people hitting 25 hit the **step fuse**. Current default **1000**. Cloud **25 MB** payload → **413** is the other “25”.
- `MessageGraph` / `create_react_agent` as the 2026 answer (deprecated; `create_agent` still **is** a `CompiledStateGraph`).
- Missing reducer: `{"messages": [new]}` **wipes** history (`LastValue`). Parallel `LastValue` writes in one super-step → **`InvalidUpdateError`**. `operator.add` **cannot** overwrite an edited HITL message (`add_messages` replaces by `id`).
- `Command(goto=X)` **and** `add_edge(node, Y)` run **both** X and Y ([#5829](https://github.com/langchain-ai/langgraph/issues/5829)).
- `InMemorySaver` behind FastAPI; HITL wait **holds** a worker; process restart **drops** the interrupt.
- Secrets in `state` or `config` (both checkpoint-associated). Use `invoke(..., context=...)` / `Runtime.context` or `UntrackedValue`.
- No `thread_id` ⇒ no checkpoint, no `interrupt()` resume. Concatenated `user+session+url` as `thread_id` → Postgres btree **`ProgramLimitExceeded`** (~2704 bytes) even if docs say **<255 chars**.
- `durability="exit"` on a payment graph; side effects **before** `interrupt()` in the same node (resume **restarts the node from the top**).
- Unbounded `Send` from an LLM-invented list. `recursion_limit` does **not** cap fan-out width.
- Prebuilt ReAct `_are_more_steps_needed` → **“Sorry, need more steps…”** instead of `GraphRecursionError` when tools are still pending ([#5548](https://github.com/langchain-ai/langgraph/issues/5548)).
- Two OSS `invoke`s on the same `thread_id` (last writer wins). Agent Server: **≤1 run / thread**.
- Redis as a user-data store (Agent Server Redis is **ephemeral pub/sub + queue signaling** — **no user data**).
- Time-travel replay treated as a cached HITL “yes” — interrupts **re-fire**.
- `checkpointer=True` subgraph invoked **in parallel** (checkpoint_ns collisions). `DeltaChannel` thread downgraded below 1.2.
- Mixing CrewAI hierarchical manager, OpenAI **handoffs**, and a LangGraph Pregel in **one** HTTP handler.

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns the **Pregel scheduler** (plan / execute / update), `recursion_limit`, `thread_id` / `checkpoint_ns` / `checkpoint_id`, durability mode, interrupt matching, `Send` fan-out, the Agent Server **thread lease**, and which tools are legal **this super-step**. It does **not** own transformer weights or tool HTTP. Data plane (generation) samples tokens. Data plane (`ToolNode` / your nodes) runs tools and MCP `tools/call`. Persistence is the **checkpointer** (one `thread_id`) plus the **Store** (cross-thread) — **not** the prompt-cache KV. Tool proxies never take IAM from model JSON. Telemetry is the only place super-step histograms, LCU, and HITL wait are authoritative.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS                                                                         │
│  SSE stream modes │ REST invoke │ HITL Command(resume=) │ /mcp Streamable HTTP │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │ TLS + session JWT + thread_id=UUID (≤255 chars) + correlation-id
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE  (Pregel — Agent Server worker / your process, not the GPU)       │
│  Compiled graph loaded once at container start. Server REPLACES your saver.     │
│                                                                                 │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌───────────┐  │
│  │ Edge       │─▶│ Policy     │─▶│ PREGEL     │─▶│ DURABILITY │─▶│ THREAD    │  │
│  │ auth, SSO  │  │ PII redact │  │ SCHEDULER  │  │ exit|async │  │ LEASE     │  │
│  │ 25 MB→413  │  │ tool RBAC  │  │ 1. Plan    │  │ |sync      │  │ ≤1 run /  │  │
│  │ 409 reject │  │ context=   │  │ 2. Execute │  │ checkpoint │  │ thread_id │  │
│  │ enqueue /  │  │ Runtime    │  │ 3. Update  │  │ put        │  │ N_JOBS=10 │  │
│  │ interrupt  │  │ NOT state  │  │ reducers   │  │            │  │ / worker  │  │
│  └────────────┘  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘  └─────┬─────┘  │
│                        │               │               │               │        │
│                        ▼               ▼               ▼               ▼        │
│                 ┌──────────────────────────────────────────────────────────┐    │
│                 │ GRAPH ORCHESTRATOR  StateGraph → compiled Pregel         │    │
│                 │  ├─ channels: LastValue / add_messages / operator.add    │    │
│                 │  ├─ Command(update, goto, resume) / Send fan-out         │    │
│                 │  ├─ HITL interrupt() + interrupt_before/after            │    │
│                 │  ├─ recursion_limit (super-steps; default 1000 @ 1.0.6)  │    │
│                 │  ├─ RemainingSteps managed channel (do not initialize)   │    │
│                 │  ├─ subgraphs (checkpointer None | True | False)         │    │
│                 │  └─ stream mux: values/updates/messages/custom/…         │    │
│                 └──────────────────────────┬───────────────────────────────┘    │
│  ┌────────────┐  ┌────────────┐            │            ┌──────────────────┐    │
│  │ Circuit    │  │ Fallback   │◀───────────┘───────────▶│ SIGTERM drain    │    │
│  │ breaker    │  │ primary →  │                         │ finish super-step│    │
│  │ LLM ≠ tool │  │ secondary  │                         │ then checkpoint  │    │
│  │ ≠ MCP      │  │ → degraded │                         │ RunControl       │    │
│  └────────────┘  └────────────┘                         └────────┬─────────┘    │
└──────────────────────────────────────────────────────────────────┼──────────────┘
                                                                   │
          ┌────────────────────────────────┬───────────────────────┘
          │ chat / agent SSE, REST         │
          ▼                                ▼
┌─────────────────────────────────┐  ┌────────────────────────────────────────────┐
│ DATA PLANE  GENERATION          │  │ DATA PLANE  EXECUTOR (ToolNode / nodes)    │
│ (provider-owned on hosted APIs) │  │ model NEVER holds IAM or Stripe sk         │
│                                 │  │                                            │
│  Tokenizer → Prefill → Decode   │  │  ToolNode: AIMessage.tool_calls in parallel│
│  tool JSON / draft / plan list  │  │  InjectedState / InjectedStore / Runtime   │
│  stop: end_turn / tool_use /    │  │  handle_tool_errors: invocation →          │
│    max_tokens / refusal         │  │  ToolMessage; execution errors re-raise    │
│                                 │  │  remaining_steps < 2 → wrap-up, no tools   │
└────────────┬────────────────────┘  └─────────────────────┬──────────────────────┘
             │                                             │
             │  untrusted planner (text / tool JSON)       │ side effects
             ▼                                             ▼
┌─────────────────────────────────┐  ┌────────────────────────────────────────────┐
│ TOOL PROXIES  (MCP / adapters)  │  │ PERSISTENCE LAYER                          │
│ Zero-Trust wrap around ToolNode │  │                                            │
│ RFC 8707 audience; NO passthrough│  │  ┌──────────────────┐  ┌─────────────────┐ │
│ identity = langgraph_auth_user  │  │  │ CHECKPOINTER     │  │ STORE           │ │
│ / Runtime.context — never JSON  │  │  │ thread_id PK     │  │ BaseStore KV    │ │
│  ┌──────────┐  ┌─────────────┐  │  │  │ checkpoint_ns    │  │ across threads  │ │
│  │ Stripe / │  │ CRM / MCP   │  │  │  │ pending writes   │  │ (user,memories) │ │
│  │ send     │  │ tools/call  │──┼──│  │ durability mode  │  │ asearch limit=10│ │
│  │ HITL     │  │ SSRF filter │  │  │  │ time-travel      │  │ silent overflow │ │
│  └──────────┘  └─────────────┘  │  │  └──────────────────┘  └─────────────────┘ │
│  /mcp is STATELESS per request  │  │  ┌──────────────────┐  ┌─────────────────┐ │
│  memory lives in checkpointer   │  │  │ Postgres         │  │ Redis (Platform)│ │
│  HITL on mutating tools         │  │  │ checkpoints+store│  │ pub/sub+queue   │ │
│                                 │  │  │ EncryptedSerializer│ │ NO user data    │ │
└─────────────────────────────────┘  │  └──────────────────┘  └─────────────────┘ │
                                     └────────────────────────────────────────────┘
                                                            │
┌───────────────────────────────────────────────────────────┴─────────────────────┐
│ TELEMETRY / OBSERVABILITY SINKS                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ Audit (WORM) │  │ Metrics      │  │ Traces       │  │ Usage (authoritative │ │
│  │ cid, tenant  │  │ super-step   │  │ gateway→LLM  │  │ on terminal event)   │ │
│  │ thread_id,   │  │ p50/p95,     │  │ →ToolNode→   │  │ input, cache_read,   │ │
│  │ checkpoint_id│  │ Send width,  │  │ HITL wait    │  │ output, LCU/LSU,     │ │
│  │ node, hashed │  │ 409 reject,  │  │ PII stripped │  │ total_cost_usd,      │ │
│  │ draft, HITL  │  │ GraphRecursion│ │ in prod      │  │ langgraph_step       │ │
│  │ decision     │  │ InvalidUpdate│  │              │  │                      │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not couple):**

| Plane | Owns | Failure if coupled |
| --- | --- | --- |
| **Control (Pregel)** | Super-step schedule, reducers, `Send`, interrupts, thread lease, `recursion_limit` | Model “goto send” trusted; Stripe in the same node as `interrupt()` |
| **Generation data** | Sample tool JSON / draft / plan | Executor runs on incomplete JSON |
| **Executor data (`ToolNode`)** | Tools, MCP, `Command` from tools | Silent empty success → ReAct-shaped loops (04) |
| **Tool proxies** | Audience-bound MCP / Stripe | Token passthrough; `/mcp` treated as a session store |
| **Persistence** | Checkpointer (`thread_id`) + Store (cross-thread) | Pod kill mid-HITL; secrets in snapshots |
| **Telemetry** | Super-step audit, LCU, HITL | Finance dashboards that ignore 1000-tick runaways |

Hosted **server tools** invert the executor (04). Your Pregel still owns the **outer** fuse. Compiling a `StateGraph` or decorating `@entrypoint` **produces a `Pregel` instance**.

### 1.2 End-to-end request flow

1. **Ingress.** SSE (`stream` / `astream` / event v3) or REST `invoke`. Gateway stamps `correlation_id`, binds `thread_id` = UUID (docs **<255 chars**; btree composite **≲2704 bytes** — do not concatenate user+url). Consult the per-(vendor, model) breaker **and** the Agent Server lease. Payload **>25 MB** on Cloud → **413** before the model runs.
2. **Policy + runtime context.** Detect → redact PII **before** the transcript is checkpointed or traced. Tool RBAC maps `(principal, tenant, tool, args_shape)` → allow / deny / HITL. Put bearer tokens in `context=` / `Runtime.context`, **not** graph state. `langgraph_auth_user` is populated by `@auth.authenticate` on Platform.
3. **Admit the run.** Agent Server: **at most one executing run per `thread_id`**. `multitask_strategy`: `enqueue` (docs default, FIFO), `reject` → **HTTP 409**, `interrupt` (cancel current), `rollback` (revert the interrupted run’s checkpoint). OSS in-process: two concurrent `invoke`s **race** the saver — that is not a lease. `N_JOBS_PER_WORKER` default **10** concurrent runs **per worker** (different threads).
4. **Pregel plan.** Select actors that subscribe to channels updated last step (first step: input channels). Nodes start **inactive**; a node becomes **active** when a message arrives; nodes with no mail vote to **halt**.
5. **Execute.** Run selected actors **in parallel** until all complete, one fails, or a timeout. Channel updates are **invisible** to siblings until the next super-step. LLM nodes call the model; `ToolNode` executes `tool_calls` in-process (not a distributed lock around Stripe — 03).
6. **Update + checkpoint.** Apply writes through each channel’s reducer. Checkpointer `put` / `put_writes` at the super-step (and task) boundary. `durability="sync"` **blocks** the next tick on the write; `"async"` (Agent Server default) overlaps write with the next node; `"exit"` writes only on success / error / HITL — **no** mid-graph recovery. Pending writes of **successful** siblings are kept if one parallel task fails.
7. **Route.** Static `add_edge`, conditional router (name / `END` / list of names / `list[Send]`), or `Command(goto=...)`. **`goto` does not suppress `add_edge`.** Fan-out: `Send("worker", slice)` — worker count is **data-dependent**; cap `len(subjects)` in the router, not with `recursion_limit`.
8. **HITL.** `interrupt(value)` inside a node (production) or static `interrupt_before` / `interrupt_after`. Graph waits **indefinitely**. Requires checkpointer + `thread_id`. Resume: `Command(resume=...)` becomes the **return value** of `interrupt()`. The **whole node restarts** — side effects before the call re-run. Parallel interrupts: map **interrupt id → resume value**.
9. **Stream.** `messages` mode is TTFT; `values` waits for the whole node. Event v3: drain `stream.messages` while HITL is pending. Seven stream-mode API modes (combinable): `values`, `updates`, `messages`, `custom`, `checkpoints`, `tasks`, `debug`.
10. **Store vs checkpoint.** Thread continuity, time-travel, crash resume → checkpointer. User prefs / facts that must cross graphs → Store `put`/`search` (parent may **not** see subgraph channel writes because of `checkpoint_ns` isolation). `asearch` default **limit=10**; overflow is **silent**.
11. **Halt.** No actors selected, or `GraphRecursionError` at `recursion_limit`, or `RemainingSteps` router jumps to END. Emit + WORM: super-step, node, hashed args, HITL decision, `thread_id`, `correlation_id`, `checkpoint_id` (ULID).
12. **Drain.** `RunControl.request_drain()` finishes the **current super-step**, writes a resumable checkpoint, then stops (SIGTERM). Agent Server workers already drain at super-step boundaries.

**Interview talking point:** “Pregel owns the tick; `ToolNode` owns the HTTP; the checkpointer owns the resume. I never let the model execute, and I never hold a FastAPI worker for a days-long `interrupt()`.”

### 1.3 Contrast only: CrewAI roles vs OpenAI Agents SDK handoffs vs LangGraph graphs

| Metaphor | Primitive | Typed state | Durability / HITL | Fuse | Why **this module** is LangGraph |
| --- | --- | --- | --- | --- | --- |
| **LangGraph** | Pregel graph: nodes, channels, `Send`, subgraphs | Reducers + checkpoints | Checkpointer + `interrupt` + Platform / Temporal plugin | `recursion_limit` default **1000** super-steps | The product **is** a state machine |
| **OpenAI Agents SDK** | `Agent` + `Runner`; **handoffs** transfer the user-facing reply; `as_tool` nests | `output_type` / `RunState` / sessions — **not** a reducer graph | Serialize `RunState`; **you** host the wait | `max_turns` default **10** (`None` disables) | Fast OpenAI-hosted tools; **not** cyclic map-reduce |
| **CrewAI** | **Crew** of role/goal/backstory agents inside a **Flow** event graph | Flow state + pydantic tasks | `@persist`, `@human_feedback`, AMP webhooks | Sequential (default) vs hierarchical (`manager_llm` required) | Role-play teams; Flow is the outer app, not Pregel |

Handoffs: the specialist **takes over** the transcript. Crew hierarchical: a manager **delegates**. LangGraph: **you** draw the edges; the LLM does not silently become the orchestrator unless you put a router node there. Pick LangGraph here because checkpoints, `Send`, time-travel, and durable `interrupt()` are first-class — the skills 04’s loops need when they leave the whiteboard. **Do not** restack all three runtimes in one HTTP handler.

---

## 2. Core Mechanics & Algorithms

### 2.1 Pregel super-steps

LangGraph’s runtime is named after Google Pregel (SIGMOD 2010): computation proceeds in **bulk-synchronous super-steps**. During super-step \(S\), actors read messages from \(S-1\), run in parallel, and send messages that become visible only at \(S+1\).

```
  super-step S
  ┌──────────┐   actors with mail from S-1      ┌──────────┐
  │  PLAN    │ ───────────────────────────────▶ │ EXECUTE  │  parallel
  └──────────┘                                  └────┬─────┘
       ▲  writes invisible to siblings until barrier │
       │                                             ▼
       │                                        ┌──────────┐
       │                                        │  UPDATE  │  reducers + checkpoint
       │                                        └────┬─────┘
       │  actors remain / mail in transit            │
       └─────────────────────────────────────────────┘
            no actors selected, or recursion_limit → HALT (GraphRecursionError)
```

A super-step is one “tick”: parallel nodes share a tick; a linear `START → A → B → END` is **separate** ticks for input, A, and B, and therefore **separate checkpoints**. Repeat until halt or the step cap fires.

### 2.2 StateGraph, reducers, `add_messages`, channels

**Unit of composition:** `StateGraph[State]`. `State` is a TypedDict, dataclass, or Pydantic model. Each key is a **channel**. Default channel is `LastValue` — last write wins. Annotate with a reducer `(Value, Value) -> Value` so parallel writes **merge** instead of clobber.

| Channel / annotation | Semantics | Typical use |
| --- | --- | --- |
| `LastValue` (default) | Overwrite | Scalars, latest draft |
| `Annotated[list, operator.add]` | Concatenate | Fan-in lists from `Send` |
| `Annotated[list[AnyMessage], add_messages]` | Append by default; **replace by message `id`**; honor `RemoveMessage` | Transcript |
| `BinaryOperatorAggregate` | Running fold | Counters |
| `Topic(..., accumulate=True)` | PubSub accumulate | Low-level Pregel |
| `DeltaChannel(bulk_reducer, snapshot_frequency=K)` | Persist **deltas**, reconstruct on read; snapshot every \(K\) steps | Long `messages` (`>=1.2`, **beta**) |
| `RemainingSteps` (managed) | Runtime-owned int; **not** user-initialized | Proactive fuse |
| `UntrackedValue` | Not checkpointed | Secrets that must not land in snapshots (not a substitute for auth) |

**Without a reducer, a node that returns `{"messages": [new]}` wipes prior history.** That is the dominant “lost messages” bug. `operator.add` appends but **cannot** overwrite an edited HITL message (it would duplicate). `add_messages` appends new IDs and replaces matching IDs.

**Parallel `LastValue` writes in one super-step raise `InvalidUpdateError`** unless that key has a reducer. Two `Send` workers writing `foo: str` (no reducer) is a **crash**, not last-write-wins. Time-travel `update_state` with parallel predecessors needs explicit `as_node=` or you get the same error.

`MessagesState` is the prebuilt TypedDict with a single `messages` key using `add_messages`. Subclass it for extra fields. Delete: `RemoveMessage(id=...)` or `RemoveMessage(id=REMOVE_ALL_MESSAGES)` (`"__remove_all__"`).

**`MessageGraph`** is a `StateGraph` whose entire state is `Annotated[list[AnyMessage], add_messages]`. **Deprecated in 1.0, removal in 2.0.**

`DeltaChannel` (`>=1.2`, beta): without it, the **full** accumulated list is re-serialized into every checkpoint. The bulk reducer must be **associative**. Changing a live thread from delta to non-delta **cannot reconstruct** those checkpoints.

Runtime **context** (`context_schema` + `invoke(..., context=...)`) is **not** graph state — documented place for per-run tokens / model names.

Compiled graphs expose `graph.nodes` (`PregelNode`s including `__start__`) and `graph.channels` (your keys **plus** ephemeral branch channels like `branch:write_essay:__self__:score_essay`). Those extra channels are edge bookkeeping, not user state.

### 2.3 Nodes, edges, `Command`, `Send`

**Nodes** are `State -> Partial[State]` (sync or async). They do not own control flow unless they return `Command`. Signatures may take `config: RunnableConfig` and `runtime: Runtime[Context]` (`store`, `stream_writer`, `execution_info`, `heartbeat`, `control` for drain). `.compile(checkpointer=..., store=..., interrupt_before=..., interrupt_after=...)` injects persistence and static breakpoints; the same interrupt lists can be overridden **per invoke**.

**Edges:** `add_edge(src, dst)` is static. `add_conditional_edges(src, router)` — router returns a node name, `END`, a list of names (all run next super-step **in parallel**), or `list[Send]`.

**`Command`** combines a state write with a hop:

| Field | Role |
| --- | --- |
| `update` | Partial state (same as returning a dict) |
| `goto` | Node name, sequence of names, `Send`, or sequence of `Send` |
| `resume` | Value for `interrupt()` — **the only `Command` field meant as `invoke`/`stream` input** |
| `graph` | `None` = current; `Command.PARENT` = closest parent (subgraph handoff) |

Return-type annotation `Command[Literal["next_node"]]` is required so Studio/rendering knows the dest. **`goto` augments routing; it does not suppress `add_edge`.** If both exist, **both destinations run**. For parent updates of a **shared** key from a subgraph via `Command.PARENT`, the **parent** channel must have a reducer.

**`Send` (map-reduce):** return `[Send("worker", {**slice})]` from a conditional edge (or `Command.goto`). Worker count is **data-dependent**. Each `Send` may carry a **different** state shape than the parent. Fan-in **requires** a reducer on the shared channel (usually `operator.add` on a list). Optional third argument (`>=1.2`): per-push `timeout` / `TimeoutPolicy`. Cap `len(subjects)` in the router — `recursion_limit` does **not** cap width.

### 2.4 `ToolNode`

`langgraph.prebuilt.ToolNode` executes `AIMessage.tool_calls` **in parallel**, injects `InjectedState` / `InjectedStore` / `ToolRuntime`, and can return `Command` from tools. Default `handle_tool_errors` catches **invocation** (bad args) errors into a `ToolMessage` and **re-raises execution** errors. For a stock ReAct loop, `create_agent` already wires `ToolNode` + `tools_condition` (`"tools"` vs `"__end__"`). Use a raw `ToolNode` when you need custom routing or error policy. In-process parallelism is **not** a distributed lock around Stripe.

### 2.5 Checkpointers, `thread_id`, Store

A **checkpoint** is a `StateSnapshot` at a super-step: `values`, `next`, `config` (`thread_id`, `checkpoint_ns`, `checkpoint_id`), `metadata` (`source` ∈ `{input, loop, update, fork}`, `writes`, `step`), `created_at`, `parent_config`, `tasks`. `checkpoint_id` is a monotonically increasing **ULID**.

**`thread_id`** is the primary key. Without it the saver cannot load/resume.

**`checkpoint_ns`:** `""` = root; `"node_name:uuid"` = subgraph invoked as that node; nested joined with `|`.

| Saver | Package | Use |
| --- | --- | --- |
| `InMemorySaver` / `MemorySaver` | `langgraph-checkpoint` (bundled) | Tests only; **RAM; lost on restart** |
| `SqliteSaver` / `AsyncSqliteSaver` | `langgraph-checkpoint-sqlite` | Local demos; **not** multi-writer prod |
| `PostgresSaver` / `AsyncPostgresSaver` | `langgraph-checkpoint-postgres` | Production; LangSmith default |
| Agent Server injected saver | (you do not compile one) | Cloud / self-host runtime |

Postgres: `.setup()` once; raw connection needs **`autocommit=True`** and **`row_factory=dict_row`**. PK `(thread_id, checkpoint_ns, checkpoint_id)` plus a **writes** table. Optional `EncryptedSerializer.from_pycryptodome_aes()` reads **`LANGGRAPH_AES_KEY`**.

**Pending writes:** if one node in a parallel super-step fails, successful siblings’ writes are already in `checkpoint_writes` and **are not re-run** on resume. Time travel still resumes from **full** super-step snapshots, not mid-node.

**Time travel:** Replay `get_state_history` → `invoke(None, checkpoint.config)` — nodes **before** are skipped; nodes **after** **re-execute** (LLM, HTTP, `interrupt()` all fire again). Fork: `update_state` creates a **new** checkpoint (`source="update"` / `"fork"`). Default subgraph = **one parent super-step** — you cannot time-travel *inside* it unless the subgraph was compiled with `checkpointer=True`.

| | Checkpointer | Store (`BaseStore`) |
| --- | --- | --- |
| Persists | Full graph snapshots | Application key-value |
| Scope | One `thread_id` | Across threads |
| Access | `configurable.thread_id` | `store.put/get/search` / `Runtime.store` |
| Use | Continuity, HITL, time travel, crash resume | User prefs, facts, RAG-ish memories |

### 2.6 `interrupt` / HITL, resume

Two pause APIs, both **require a checkpointer + `thread_id`**. The graph waits **indefinitely**.

| Mechanism | Where it pauses | Resume |
| --- | --- | --- |
| `interrupt(value)` inside a node | Dynamic, conditional | `Command(resume=...)` becomes the **return value** of `interrupt()` |
| `interrupt_before` / `interrupt_after` at compile **or** invoke | Static, between nodes | `invoke(None, config)` (no resume payload) |

Docs recommend **`interrupt()`** for production HITL; static breakpoints are for stepping/debug. On resume, the **whole node restarts**. Code **before** `interrupt()` re-runs — side effects must be idempotent, or wrap them in Functional API `@task` (completed task results restore from the checkpointer). Multiple `interrupt()` calls in one node match resume values **by order**. `Command(update=...)` as **invoke input** is the wrong API for multi-turn chat — pass a plain dict.

`create_agent` HITL: `HumanInTheLoopMiddleware(interrupt_on={tool: True|{allowed_decisions}})` plus a checkpointer. Resume shape is a **decisions** list, not a free-form string.

> ⚠️ Gap: `HumanInTheLoopMiddleware` **ainvoke** `get_config` bug ([langchain#34974](https://github.com/langchain-ai/langchain/issues/34974)) — confirm the fix version before relying on async HITL middleware.

### 2.7 Subgraphs

A subgraph is a compiled graph used as a node, or invoked inside a node with a state mapper.

| `checkpointer=` on subgraph | Behavior |
| --- | --- |
| `None` (default) | Per-invocation; inherits parent saver; HITL works; memory does **not** accumulate across parent calls |
| `True` | Per-thread; own `checkpoint_ns` history; **do not** call the same subgraph in parallel |
| `False` | Stateless function; **no** interrupts |

Interrupts still propagate to the top-level graph. Viewing nested state requires the subgraph to be **statically discoverable**. There is **no documented nesting-depth cap of 25**. Nested subgraphs **consume parent super-steps**. Visualization historically struggled past **~3** nested levels ([#2607](https://github.com/langchain-ai/langgraph/issues/2607)) — a **render** bug, not an executor cap.

### 2.8 Streaming modes

| Mode | Payload |
| --- | --- |
| `values` | Full state after each step (incl. interrupts) |
| `updates` | Per-node / per-task deltas (parallel updates emitted **separately**) |
| `messages` | `(token, metadata)` from any LLM inside nodes — **this** is TTFT |
| `custom` | `get_stream_writer()` payloads |
| `checkpoints` | Same shape as `get_state()`; needs checkpointer |
| `tasks` | Task start/finish + errors; needs checkpointer |
| `debug` | checkpoints + tasks + extra metadata |

`version="v2"` unifies chunks as `{type, ns, data}` (`>=1.1`). Event streaming v3 (`>=1.2` recommended): typed projections as independent iterators. Custom requires at least one mode to be `"custom"`. Token UX is `messages`, not `values`.

### 2.9 `create_agent` vs raw `StateGraph`

LangGraph v1 **deprecates** `langgraph.prebuilt.create_react_agent` in favor of `langchain.agents.create_agent`, which **returns a `CompiledStateGraph`** (still Pregel) plus middleware.

`create_agent` loop: model node → if `tool_calls` then tools node → model … until no tool calls. Production middleware: `HumanInTheLoopMiddleware`, `SummarizationMiddleware` (example trigger **4000** tokens / keep **20** messages), `ModelCallLimitMiddleware`, `ToolCallLimitMiddleware`, `PIIMiddleware`, model/tool retry.

**Drop to raw `StateGraph`** when you need cycles that are not “model⇄tools”, `Send` map-reduce, `Command.PARENT` handoffs, custom join nodes, or mixed deterministic/LLM branches. Official pattern: call `create_agent` **inside** a StateGraph node. If you add the inner agent as a **compiled subgraph**, its tool loop still burns parent `recursion_limit` super-steps — count ticks before you ship.

Prefer `RemainingSteps` routing or call-limit middleware over hoping `GraphRecursionError` is the UX.

### 2.10 `recursion_limit`, `RemainingSteps`, durability

`recursion_limit` counts **super-steps**, not Python stack frames and not “number of tools.” Parallel nodes in one tick count as **one**. Starting **langgraph 1.0.6**, default is **1000** (PR `#6676`). Older blogs still mention **25** — historical LangChain runnable default, **not** current LangGraph. Exceeding it raises `GraphRecursionError` (subclass of `RecursionError`).

**Proactive fuse:** add `remaining_steps: RemainingSteps` to state (managed — do not initialize or decrement). Docs’ example bails when `remaining_steps <= 2`. Step counter is also on `config["metadata"]["langgraph_step"]`.

| Mode | When it writes | Crash semantics |
| --- | --- | --- |
| `"exit"` | Only on success, error, or HITL interrupt | Fast; **no** mid-graph recovery |
| `"async"` | Checkpoint **while** next step runs (default) | Small window of lost last tick |
| `"sync"` | Checkpoint **before** next step | Highest durability; extra latency |

`checkpoint_during` is **deprecated** in favor of `durability`. Agent Server default **`async`**.

**Exactly-once super-steps vs at-least-once nodes:** committed super-step + pending writes of successful tasks = the unit you resume from **without** re-running those tasks. **Node body** = at-least-once. Retry, timeout (`NodeTimeoutError` is retryable), `interrupt()` resume, and time-travel **all restart the node from the top**. Functional API: `@task` results restore; `@entrypoint` **replays from line 1**. Graph API: one checkpoint **per super-step**. Functional API: one checkpoint **per entrypoint invocation**.

`RetryPolicy` defaults: **3** attempts including the first; skip `ValueError`/`TypeError`/`RuntimeError`/`OSError`/…; HTTP retries **5xx only**. Timeouts: **async nodes only**; sync + `timeout` **rejected at compile**. `set_node_defaults` does **not** inherit into subgraphs.

### 2.11 Complexity of super-steps

Let \(L\) be the length of the **critical sequential path** (nodes that cannot share a tick), \(W\) the `Send` width, \(M\) the number of LLM calls, \(T\) thread length in messages.

- **Time (control plane):** \(\Theta(L)\) super-steps. Fan-out of \(W\) workers is \(\Theta(1)\) tick, not \(\Theta(W)\). Classic ReAct ≈ **2** super-steps per tool round (model, then tools) — see 04.
- **Checkpoints:** \(\Theta(L)\) snapshots on a linear path; parallel workers share one snapshot plus **pending writes** per task.
- **Serialization (no `DeltaChannel`):** each snapshot re-writes the full `messages` channel → \(\Theta(T \cdot L)\) bytes. `DeltaChannel` stores the step’s writes; read replay bounded by `snapshot_frequency=K`.
- **Token bill:** \(\Theta(M)\), **independent** of super-step count when workers share a tick. `Send` **multiplies dollars by \(W\)** and does **not** multiply ticks.
- **`recursion_limit`:** a hard cap on \(L\), **not** on \(W\). A 2-node cycle can burn 1000 ticks quickly; a 30-node DAG cannot.
- **InvalidUpdateError:** \(O(1)\) crash at the barrier when two `LastValue` writes land in one tick.
- **Time-travel replay:** \(\Theta(\)nodes after the chosen checkpoint\()\) re-execution, including interrupts.

### 2.12 Invariants

1. The model never executes tools, edges, interrupts, or checkpoints.
2. Agent Server: **≤1 executing run per `thread_id`**. OSS in-process has **no** distributed lease.
3. No `thread_id` ⇒ no save, no `interrupt()` resume.
4. Parallel `LastValue` writes in one super-step ⇒ `InvalidUpdateError` (need a reducer).
5. `Command.goto` does **not** suppress `add_edge` — both fire.
6. `interrupt()` resume **restarts the whole node**; code before the call is at-least-once.
7. `recursion_limit` is a **top-level** invoke key and counts **super-steps** (default **1000** since 1.0.6).
8. `Send` width is data-dependent and **uncapped** by `recursion_limit` — cap in the router.
9. Secrets live in `Runtime.context` / `UntrackedValue`, never in checkpointed state.
10. Pending writes skip successful sibling tasks on resume; the failed node restarts from the top.
11. Redis on Agent Server holds **no** user data. Checkpointer vs Store: thread vs cross-thread.
12. Time-travel **re-fires** interrupts — an auditor replay is a **new** approval.

---

## 3. Token Economics & NFR Analysis

List prices: **see 01**. Hop-loop math: **see 04**. This section prices **graph skeletons** (LLM calls the Pregel actually issues) plus **published LCU/LSU**. LangGraph OSS has **no** per-run SKU. `$ per 1k graph runs` figures are **[inferred]** from a stated node/LLM-call skeleton × list prices.

Per LLM call (standard, short context, 2026-09-23): GPT-4.1 **$2 / $8** per 1M in/out; GPT-6 Sol **$2 / $10**; GPT-6 Luna **$0.10 / $0.50**; GPT-5.4 **$2.50 / $15**. Skeleton tokens **per LLM call:** 3,000 input + 800 output.

\[
C_{\mathrm{call}} = \frac{3000\,P_{\mathrm{in}} + 800\,P_{\mathrm{out}}}{10^{6}}
\]

GPT-4.1: \(C_{\mathrm{call}} = \$0.0124\). HITL wait is **$0 model** and **$0 worker** on Agent Server / Temporal while parked. LangGraph adds **0 extra LLM calls** vs a hand-rolled loop on the same skeleton. Checkpointer I/O is infra. `create_agent` middleware (summarization, extra judge) **does** add calls — budget from traces.

### 3.1 Cost per 1k **completed graph runs**

**Ticket workflow (scenario A, §6.1):** **6 nodes**, **3 LLM nodes** (classify, draft, post-HITL send). Non-LLM: retrieve, `interrupt` review, persist. \(3 \times \$0.0124 = \$0.0372\) / execution.

| Model | $ / execution **[inferred]** | **$ / 1k** **[inferred]** |
| --- | --- | --- |
| GPT-4.1 | $0.0372 | **$37.20** |
| GPT-6 Sol | $0.0420 | **$42.00** |
| GPT-6 Luna | $0.0021 | **$2.10** |
| GPT-5.4 | $0.0585 | **$58.50** |

**Send research (scenario B, §6.2):** 1 planner LLM + **8** worker LLMs (`Send` fan-out, **one super-step**) + 1 synthesizer = **10** LLM calls. \(10 \times \$0.0124 = \$0.124\) / execution.

| Model | $ / execution **[inferred]** | **$ / 1k** **[inferred]** |
| --- | --- | --- |
| GPT-4.1 | $0.124 | **$124** |
| GPT-6 Luna | $0.007 | **$7** |
| GPT-5.4 | $0.195 | **$195** |

Fan-out **multiplies dollars by N** and **does not** multiply super-step count. Latency can drop toward `max(worker_p95)` plus join.

Checkpoints are **storage + CPU**, not LLM tokens. They become a **token** problem when every model node resubmits the full `messages` channel. **[inferred]** size sketch: 50 turns × ~2 KB/message ≈ 100 KB channel value × (super-steps ≈ turns) if you snapshot fully each tick → **megabytes per busy thread**. Docs warn checkpoints **grow unboundedly**. Semantic Store search adds embedding tokens (`text-embedding-3-small` **$0.02 / 1M** per 01) — typically << chat. Cloud request cap **25 MB**.

### 3.2 Platform SKUs (published) vs `$ / 1k` **[inferred]**

[langchain.com/pricing](https://www.langchain.com/pricing) (fetched 2026-09-23): Developer **$0**/seat, **5k** base traces/mo, 1 seat. Plus **$39**/seat/mo, **10k** traces, Deployment + Engine, **1 free Serverless Small**. LCU **$1.50**; LSU **$1.00**. Runtime **0.045 LCU / vCPU-hr**, **0.006 LCU / GiB-hr** → **$0.0675 / vCPU-hr** and **$0.009 / GiB-hr [inferred]**. Database **0.177 LSU / vCPU-hr**, **0.025 LSU / GiB-hr**. Existing customers stay on old per-run/uptime pricing until **2026-10-01**, then move to LCU/LSU; scale-to-zero is **new pricing only**. Do **not** mix superseded **$0.001/node** blog numbers with LCU.

**Agent Server compute $ / 1k [inferred]:** assume ⚠️ **2 vCPU-seconds** of runtime per ticket execution (not published): \(1000 \times 2/3600 \times 0.045 \times 1.50 \approx \$0.038\). **Tokens dominate** at GPT-4.1 $37/1k.

**Dedicated Small always-on [inferred]** from published sizes + rates: runtime \(3\times0.045\times1.50 + 6\times0.006\times1.50 = \$0.2565/\mathrm{h}\); DB \(1\times0.177 + 4\times0.025 = \$0.277/\mathrm{h}\); total **≈ $0.534/h ≈ $390/mo** if 24×7. At **1k** runs/mo that is **~$390 / 1k platform**; at **100k** runs/mo **~$3.90 / 1k**. Serverless S has **no dedicated DB SKU** (shared) and can scale to zero (beta).

> ⚠️ Gap: Engine “5–30 LCU per run” appears on some pricing explainers — treat as vendor range, **not** this module’s ticket math. Scale-to-zero **inactivity window** is explicitly unstable (beta).

### 3.3 Latency SLA targets

> ⚠️ Limited public data available — **LangGraph does not publish p50/p95/p99** for `invoke`, checkpoint `put`, or Agent Server API. LangSmith exposes Agent Server latency as a **metric you monitor**, not a contractual SLO. Numbers below are **[inferred] policy** plus documented caps. Do not invent a p95 in an architecture review.

Order of magnitude **[inferred, not a vendor SLO]**: a local Postgres `put` is typically **milliseconds**; a frontier model call is **hundreds of ms to seconds**. Checkpoint I/O is **not** the token bill, but `durability="sync"` **adds that write to the critical path** before the next super-step.

**[inferred] sequential ticket path (HITL wait excluded):** \(T \approx T_{\mathrm{classify}} + T_{\mathrm{retrieve}} + T_{\mathrm{draft}} + T_{\mathrm{send}}\) after resume. p99 of the **machine** path is the slowest LLM + tool, not average TTFT. HITL p99 is the **human SLA**. **[inferred] Send research:** \(T \approx T_{\mathrm{plan}} + \max_i T_{\mathrm{worker},i} + T_{\mathrm{synth}}\), not \(N\times\) serial.

**[inferred] policy targets** (not vendor guarantees):

| Metric | Target | Mitigation |
| --- | --- | --- |
| **p50** ticket wall (pre-HITL) | **3 LLM sequential**; wall **< 8 s** if retrieve is local | Stream **`messages`** (not `values`); cache-stable tools prefix (02); `durability="async"` on non-money ticks |
| **p95** Send research | Planner + **max(worker)** + synth; wall **< 20 s** if workers are I/O | Cap `Send` width at 8; per-`Send` timeout (`>=1.2`); join on `operator.add`; do not serial-ReAct the N questions (04) |
| **p99** hang | Fail closed on node timeout / Activity timeout / 409-storm; HITL p99 = human SLA | `TimeoutPolicy` on **async** nodes; `RemainingSteps` wrap-up; `reject` vs `enqueue` for double-text; `durability="sync"` only around send/refund |

Measure yourself: `langgraph_step`, checkpoint `put` histogram, `Send` width, 409 rate, HITL wait, `total_cost_usd`. Streaming: `messages` mode is what users feel as TTFT; `values` waits for the whole node.

### 3.4 Throughput and back-pressure

| Knob | Value | $ / latency effect |
| --- | --- | --- |
| `recursion_limit` | Default **1000** since 1.0.6 | Cap spend; `GraphRecursionError` |
| `RemainingSteps` bail | Docs example `<= 2` | Graceful END vs exception |
| `RetryPolicy` | `max_attempts=3`, `0.5s` × `2.0` backoff, cap **128s**, jitter on | Extra model/tool $ on 5xx |
| `N_JOBS_PER_WORKER` | Default **10** | Bounds concurrent **runs**, not HTTP |
| 1 run / thread | Enforced on Agent Server | Same-thread double-text must `enqueue` / `reject` (**409**) |
| `Send` width | Data-dependent | $ × N; latency ~ max worker |
| `durability` | `exit` / `async` / `sync` | Write on critical path only for `sync` |
| Payload | Cloud **25 MB** | 413 |

**Back-pressure design:**

1. Admit iff LLM breaker ∈ {closed, half-open} **and** the thread has **no** executing run **and** `N_JOBS_PER_WORKER` has room.
2. Shed in order: `reject` (409) on hot threads → disable parallel **writes** → skip synthesizer extras → deterministic degraded JSON. Do not raise `recursion_limit` to 9999 as a “fix.”
3. Split API vs queue with `queue.enabled: true`. At least **one queue worker** must listen or runs are orphaned.
4. Cap `Send` width in the router. Inner `create_agent` `max_turns` / `ModelCallLimitMiddleware` so a poisoned worker wastes **3** hops, not **1000** super-steps.

LLM RPM/ITPM: **see 01**. 100 concurrent tickets × 3 LLM nodes = **300** model calls in flight if uncapped; `Send` N=8 on 20 jobs = **160** worker calls in **one** tick of each job.

### 3.5 Non-functional requirements

| NFR | Working target | Tension |
| --- | --- | --- |
| **Availability** | 99.9% **gateway**. Graph still ends on fuse (partial state + `GraphRecursionError` / wrap-up). Multi-vendor LLM fallback for 503/529. Agent Server `reject` → 409 is **correct** back-pressure, not an outage | Failover **busts** prefix cache (02); never failover a 400; 409 on the same `thread_id` is not a 500 |
| **RPO** | Checkpointer + pending writes: **0** for HITL-irreversible (`durability="sync"`). Store TTL hours. KV/prompt-cache: **minutes**, best-effort. `"exit"` RPO = **whole run**. Redis: **not** in the RPO story (no user data) | `"async"` races the next super-step; `InMemorySaver` RPO = process life |
| **RTO** | Interactive: LLM failover **< 1 s** (breaker already open). Resume HITL with **same** `thread_id` + `Command(resume=...)`. In-flight payment: **same** idempotency key (03). Temporal plugin: HITL is a **signal** — no worker CPU while parked | Fast failover vs bit-identical tokens (T>0); node restart **replays** side effects unless idempotent |
| **Consistency** | Channels + reducers are the DAG truth. Model text: at-least-once retry **changes tokens**. Exactly-once is a **lie** without the downstream store (03). One run / `thread_id` prevents split-brain writes | `Command` + static edge double-fire; parent not seeing subgraph writes (`checkpoint_ns`) |
| **Compliance** | Regional Cloud **US / EU** (org-implied; **not migratable**); ZDR / traces (01); checkpoints are **PII stores**; `EncryptedSerializer` + `LANGGRAPH_AES_KEY`; WORM HITL decisions | Residency vs latency vs DLP on unbounded `messages` channels; encryption **does not change what is persisted** |
| **Cost vs latency** | Ticket **[inferred] $37.20/1k** (GPT-4.1) vs Send N=8 **$124/1k** vs Luna ticket **$2.10/1k**. Dedicated S **~$390/mo** floor vs serverless ~**$0.04/1k** compute | `durability="sync"` on every tick; Dedicated always-on at 1k runs/mo (**~$390 / 1k platform**) |
| **Cache vs tenancy** | Frozen tools/system prefix (02). `thread_id` is tenant-scoped UUID. Store namespaces `(org, user, "prefs")` | Hit rate vs isolation; putting PDFs in state → 413 / btree blow-ups |

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution: checkpointer + Platform (Temporal / Kafka equivalent)

Application state ≠ KV cache. The LangGraph **equivalent** of a Temporal Workflow + Kafka compacted log is:

- **Checkpointer** = durable Workflow history at **super-step** grain (Graph API) or **entrypoint** grain (Functional API).
- **Agent Server thread lease** = `workflow-id` uniqueness (**≤1 run / `thread_id`**).
- **Redis queue + pub/sub** = the Kafka *topic for wakeups* — **not** the system of record (no user data).
- **Postgres** = the log (checkpoints + writes + store).
- **Temporal plugin** (Python public preview): graph as Workflow; nodes as Activities (`execute_in: "activity"`) or in-Workflow (must be deterministic). `interrupt()` only in Activity nodes; wait is a Temporal **signal** — **no worker CPU while HITL blocks**. Activity retry **re-runs the whole node**. Python **3.11+** for interrupt/contextvars. Disable nested SDK retries (`max_retries=0`) so **one** owner retries (03).

OSS `graph.invoke` **does** hold the process unless you persist and return. Do not park a FastAPI worker for days.

**Kafka / outbox mapping:** `graph.intents` (goal + `thread_id` **before** first node), `graph.supersteps`, `tool.results`, `hitl.decisions` → **Signal** the wait, `graph.dlq`. Compaction on `thread_id` keeps a snapshot; the full log is chain-of-custody. Poison: skip + alert after N handler crashes (`GraphRecursionError` storms, `InvalidUpdateError` from a bad reducer deploy).

**Locking:** PostgresSaver uses a process `threading.Lock` around cursors in the sync class — that is **not** a distributed thread lease. Agent Server: **lease**. `reject` → **409**; `enqueue` queues FIFO; `interrupt` cancels current; `rollback` reverts the interrupted run’s checkpoint. Containers are stateless; durability is Postgres. Graceful shutdown: finish the current **super-step**.

**Replay vs resume vs Temporal:**

| Event | Re-executes |
| --- | --- |
| `Command(resume=)` after `interrupt()` | **Entire node** (tasks restored in Functional API) |
| Agent Server worker crash, `durability=async/sync` | From last checkpoint; successful sibling tasks skipped via pending writes |
| Time-travel replay | All nodes **after** the chosen checkpoint, including interrupts |
| Temporal Activity retry | Whole node/Activity |

**Version skew:** add/remove state keys; change topology on **finished** threads. **Interrupted** threads: do not **rename/remove** the node they are about to enter. Renamed keys **lose** saved values. `DeltaChannel` checkpoints are unreadable on `<1.2`. Drain or reject old schemas on Agent Server revisions.

### 4.2 Failure taxonomy, poison-pill, circuit breaker, fallbacks

| Class | Examples | Handler |
| --- | --- | --- |
| **Transient** | 408/429/5xx/529, TLS reset, MCP disconnect, `NodeTimeoutError` | Full jitter; same idempotency key; last-good catalog; `RetryPolicy` 5xx only |
| **Permanent** | 400 schema, 401/403, `refusal`, spend-cap 429, `InvalidUpdateError` (missing reducer), RBAC deny | Fail the run; **do not** failover schema 400s; **do not** retry `InvalidUpdateError` |
| **Poison pill (graph)** | Conditional edge never returns `END`; ReAct tool loop; `Send` writing into a cycle; unbounded `Send`; `Command` + static edge double-fire; identical `(tool, args)` (04) | `RemainingSteps` / `ModelCallLimitMiddleware`; cap `Send` width; **either** `Command` **or** static edges per node; hash circuit N=3 inside `ToolNode` wrapper |
| **Poison pill (ops)** | `InMemorySaver` + restart; missing `thread_id`; `durability="exit"` crash mid-node; Functional API interrupt **order** changed; factory graph new tools + old `thread_id` | Postgres/Agent Server; stable UUID; `durability="sync"` for money; do not reorder interrupts; new `thread_id` after incompatible schema |
| **Semantic** | Goal hijack via tool output (04 PlanFlip); schema-valid unauthorized refund | Frozen goal + RBAC + HITL; not a retry |
| **Soft fuse as success** | “Sorry, need more steps…”; HTTP 200 with `__interrupt__` dropped | Metric + eval fail |
| **Fuse mismatch** | `recursion_limit` inside `configurable`; subgraph ticks vs parent 1000; Deep Agents subagent **25** (04) | Top-level invoke key; count ticks; propagate limits |
| **State bloat** | Fat `messages`; PDFs in state; 413 on Cloud | `RemoveMessage` / summarization; `DeltaChannel`; Store for blobs; prune |

**Poison repeating (inside `ToolNode` wrappers — control plane, same as 04):**

1. Canonicalize args (JSON key sort; strip ephemeral timestamps).
2. Key = `(tool_name, args_hash)`.
3. After **N=3** identical **failures**: hard observation and remove the tool from the allowlist.
4. After **N=6** empty calls: abort the **stream**.
5. A looping `create_charge` with a **new** model-invented UUID is a **duplicate-charge** bug (03) — hop hash ≠ Stripe idempotency key.

**Circuit breaker** (one per **(provider, model)** for LLM **and** one per **tool-class** / MCP server). Open on high **5xx/529/timeout** rate. **Do not** open solely on 429-with-Retry-After. Half-open: probe with a **cheap read**, not `send_email`.

```
           5xx/529/timeout rate ≥ threshold           probe success
  ┌────────┐  ──────────────────────────────────▶  ┌──────┐  ──────▶ CLOSED
  │ CLOSED │                                       │ OPEN │
  └───┬────┘  429 with Retry-After = throttle      └──┬───┘
      │       (stay CLOSED; sleep)                    │ timer (e.g. 30 s)
      │ success resets window                         ▼
      │                                          ┌──────────┐
      └──────────────────────────────────────────│ HALF_OPEN│── probe fail ──▶ OPEN
                                                 │ 1 cheap  │
                                                 │ read     │
                                                 └──────────┘
```

**Fallback chain:** primary (GPT-4.1 / Sol) → secondary vendor (same IR) → **deterministic** `{"status":"degraded"}` (no charge, no email). Tool-class open → queue / HITL, not a second processor. `PermanentError` on schema / RBAC / `InvalidUpdateError` **does not** failover. `GraphRecursionError` is a **fuse**, not a retry. Retry amplification: LLM timeout 60 s → tool 55 s → both retry. Fix: one retry owner; hop cap; nested timeouts strictly decreasing.

### 4.3 Zero-Trust MCP wrapping `ToolNode`

Agent Server **serves** graphs at `/mcp` (Streamable HTTP, `langgraph-api>=0.2.3`). **Each `/mcp` request is stateless** — conversational memory must live in the checkpointer/store, not the MCP session. Same auth as the rest of the API.

Consume MCP with `langchain-mcp-adapters.MultiServerMCPClient`; pass **per-user** `Authorization` from `langgraph_auth_user`, not a compile-time secret. Stdio MCP is for laptops; docs warn against stdio in a web server.

**Wrap `ToolNode` (or each tool):**

1. Remote MCP servers are OAuth 2.1 resource servers. RFC 9728 metadata; **RFC 8707** `resource` indicator; PKCE; MUST **validate audience**; MUST NOT **token-passthrough**.
2. Gateway: terminate OAuth, **RFC 8693** exchange to upstream. Pin manifests `hash(description+schema)` against rug-pulls.
3. Re-validate authorization **at execution**, not only at plan-approval. Check `runtime.context` / `langgraph_auth_user` **before** HTTP.
4. `tool_filter` / per-assistant allowlist. HITL on mutating tools. Hosted MCP egress is **outside** your VPC unless Hybrid (data plane in-VPC; control plane poll is HTTPS + API key).
5. Cloud NAT IPs are published for allowlists (post **2025-01-06** deployments). Dual-LLM: quarantined model reads untrusted `tool_result`; privileged model holds tools (04).

No unauthenticated Streamable HTTP. The planner is **untrusted** (OWASP LLM01 / LLM06 — 04).

### 4.4 Tool RBAC, PII in checkpoints, WORM

**Tool RBAC:** OSS is DIY. LangSmith Plus: org User/Admin. Enterprise: custom RBAC/ABAC, custom SSO. Agent Server `@auth.authenticate` + `@auth.authorize` on assistants/threads/runs. Allowlist per assistant config; do not bind a shared PAT into the graph. Map `(principal, tenant, tool, args_shape)` → allow / deny / HITL. Resume values must not concatenate into a new tool call without **re-RBAC**. OSS will happily `Command(resume=)` if the caller has the `thread_id` — wrap with custom auth on Platform.

**HITL as policy:** treat mutating tools (refund, send-email, `execute_sql`) as **policy gates**. Patterns: `interrupt()` **before** `ToolNode`; `HumanInTheLoopMiddleware`; static `interrupt_before=["tools"]`. Show an **uneditable, sanitized** preview of name+args in the interrupt payload. Split **draft** and **send** into different nodes so resume cannot resend.

**PII pipeline (detect → redact → audit):** DLP at ingress **and** executor **before** checkpoint/trace. PII in `messages` **is** the checkpoint. Controls: `RemoveMessage`, Store with tighter TTL, `PIIMiddleware` on `create_agent`, LangSmith LLM Gateway redaction (Plus+), region (US/EU). `EncryptedSerializer` + `LANGGRAPH_AES_KEY` (auto on LangSmith when the env var is set). Encryption **does not change what is persisted**. Platform JSON handlers **skip** encrypting identifiers (`thread_id`, `checkpoint_id`, …) and most `langgraph_*` fields — design as if auth metadata may persist in plaintext metadata. LangSmith **does not train** on traces (pricing ToS — verify current ToS). Traces without **identity** (`langgraph_auth_user` / IAM) are useless for SOX.

**Immutable WORM audit:** `correlation_id`, tenant, `thread_id`, `checkpoint_id`, `checkpoint_ns`, super-step (`langgraph_step`), node name, `Send` width, hashed draft / hashed tool args, policy/HITL decision + actor, breaker state, `total_cost_usd`, model id, durability mode. Reconstruct a send as: policy snapshot + sampled draft + hashed body + human interrupt + ToolNode result. Kafka full log is a second copy. Provider traces are not a SIEM. PostgresSaver **is** a PII store — retention/prune is a compliance control, not an ops nice-to-have.

---

## 5. Production Enterprise Code

Assumptions match research: HTTP `INITIAL_RETRY_DELAY=0.5`, `MAX_RETRY_DELAY=8.0`, `DEFAULT_MAX_RETRIES=2`; LangGraph `recursion_limit` on invoke (**not** inside `configurable`); `Send` width cap **8**; HITL via `interrupt()` on a **separate** node from `send_email`; MemorySaver for offline. PostgresSaver helper is complete and import-gated. Run: `python langgraph_runtime.py`.

```python
#!/usr/bin/env python3
"""LangGraph production control plane. Python 3.11+.

  python langgraph_runtime.py

Offline self-test uses MemorySaver (no network, no LLM). PostgresSaver is a
complete helper gated on a DSN so this file runs without Postgres.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import operator
import os
import random
import time
import uuid
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
            "node": getattr(record, "node", None),
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


def build_logger(
    correlation_id: str,
    tenant: str,
    thread_id: str | None = None,
    node: str | None = None,
) -> CorrelationAdapter:
    base = logging.getLogger("langgraph.runtime")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    extra: dict[str, Any] = {"correlation_id": correlation_id, "tenant": tenant}
    if thread_id:
        extra["thread_id"] = thread_id
    if node:
        extra["node"] = node
    return CorrelationAdapter(base, extra)


class TransientError(Exception):
    def __init__(self, msg: str, retry_after: float | None = None, status: int | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after
        self.status = status


class PermanentError(Exception):
    pass


class CircuitOpenError(TransientError):
    pass


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class BreakerStateMachine:
    """Per (provider, model) or per tool-class. Do not trip on 429-with-Retry-After."""

    def __init__(self, name: str, failure_threshold: int = 5, recovery_seconds: float = 30.0, half_open_max: int = 1) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.half_open_max = half_open_max
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = 0
        self._lock = asyncio.Lock()

    async def allow(self) -> None:
        async with self._lock:
            self._maybe_half_open()
            if self._state is BreakerState.OPEN:
                raise CircuitOpenError(f"circuit_open:{self.name}")
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_inflight >= self.half_open_max:
                    raise CircuitOpenError(f"circuit_half_open_busy:{self.name}")
                self._half_open_inflight += 1

    def _maybe_half_open(self) -> None:
        if self._state is BreakerState.OPEN and (time.monotonic() - self._opened_at) >= self.recovery_seconds:
            self._state = BreakerState.HALF_OPEN
            self._half_open_inflight = 0

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._half_open_inflight = 0
            self._state = BreakerState.CLOSED

    async def record_failure(self, *, trip: bool = True) -> None:
        async with self._lock:
            if not trip:
                return
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = 0

    @property
    def state(self) -> BreakerState:
        return self._state


T = TypeVar("T")


async def retry_with_jitter(
    fn: Callable[[], Awaitable[T]],
    *,
    log: CorrelationAdapter,
    attempts: int = SDK_DEFAULT_MAX_RETRIES + 1,
    base: float = INITIAL_RETRY_DELAY,
    cap: float = MAX_RETRY_DELAY,
) -> T:
    """HTTP/transport loop ONLY. Full jitter. Never wrap GraphRecursionError."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except PermanentError:
            raise
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            ra = exc.retry_after
            sleep_s = ra if ra is not None and 0 < ra <= 60 else random.random() * min(cap, base * (2**i))
            log.warning("http_retry attempt=%s sleep_s=%.3f err=%s", i + 1, sleep_s, exc)
            await asyncio.sleep(sleep_s)
    assert last is not None
    raise last


def deterministic_degraded(ticket_id: str) -> dict[str, Any]:
    return {"status": "degraded", "ticket_id": ticket_id, "answer": None}


class FallbackChain:
    def __init__(
        self,
        primary: Callable[[], Awaitable[str]],
        secondary: Callable[[], Awaitable[str]],
        breaker: BreakerStateMachine,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self.breaker = breaker

    async def invoke(self, log: CorrelationAdapter) -> str:
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


class ZeroTrustToolProxy:
    """Wraps ToolNode-equivalent side effects. Identity never comes from model JSON."""

    def __init__(self, allowed: frozenset[str], irreversible: frozenset[str]) -> None:
        self.allowed = allowed
        self.irreversible = irreversible
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, *, principal: str, tool: str, args: dict[str, Any], hitl_approved: bool) -> str:
        if not principal:
            raise PermanentError("missing_principal")
        if tool not in self.allowed:
            raise PermanentError(f"rbac_deny:{tool}")
        if tool in self.irreversible and not hitl_approved:
            raise PermanentError(f"hitl_required:{tool}")
        self.calls.append((tool, dict(args)))
        return f"ok:{tool}:{json.dumps(args, sort_keys=True)}"


def postgres_saver(dsn: str) -> Any:
    """Production checkpointer. Requires psycopg + langgraph-checkpoint-postgres.

    Docs: autocommit=True, row_factory=dict_row, call .setup() once.
    thread_id must be a UUID (docs <255 chars; btree composite ≲2704 bytes).
    Optional: EncryptedSerializer.from_pycryptodome_aes() + LANGGRAPH_AES_KEY.
    """
    from psycopg import Connection
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres import PostgresSaver

    conn = Connection.connect(dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row)
    saver = PostgresSaver(conn)
    saver.setup()
    return saver


class TicketState(TypedDict):
    messages: Annotated[list, add_messages]
    ticket_id: str
    draft: str
    decision: str
    sent: int


def classify_ticket(state: TicketState) -> dict[str, Any]:
    return {"messages": [{"role": "assistant", "content": "intent:billing"}]}


def retrieve_policy(state: TicketState) -> dict[str, Any]:
    return {"messages": [{"role": "assistant", "content": "kb:refund-window-30d"}]}


def draft_reply(state: TicketState) -> dict[str, Any]:
    return {
        "draft": f"ticket {state['ticket_id']}: approved-template",
        "messages": [{"role": "assistant", "content": "drafted"}],
    }


def review_hitl(state: TicketState) -> Command[Literal["send_email", "draft"]]:
    payload = interrupt({"kind": "review", "draft": state["draft"], "ticket_id": state["ticket_id"]})
    decision = payload["decision"] if isinstance(payload, dict) else str(payload)
    if decision == "edit":
        edits = payload.get("edits", state["draft"]) if isinstance(payload, dict) else state["draft"]
        return Command(update={"decision": "edit", "draft": edits}, goto="draft")
    if decision == "reject":
        return Command(update={"decision": "reject"}, goto=END)
    return Command(update={"decision": "approve"}, goto="send_email")


def send_email(state: TicketState) -> dict[str, Any]:
    if state.get("decision") != "approve":
        raise PermanentError("send_without_approve")
    digest = hashlib.sha256(state["draft"].encode()).hexdigest()[:16]
    return {
        "sent": state.get("sent", 0) + 1,
        "messages": [{"role": "assistant", "content": f"sent:{state['ticket_id']}:{digest}"}],
    }


def build_ticket_graph(checkpointer: MemorySaver | None = None):
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
    builder.add_edge("send_email", END)
    return builder.compile(checkpointer=checkpointer or MemorySaver())


class ResearchState(TypedDict):
    subjects: list[str]
    findings: Annotated[list[str], operator.add]
    report: str


class WorkerState(TypedDict):
    q: str


def plan_research(state: ResearchState) -> dict[str, Any]:
    seed = state.get("subjects") or ["alpha", "beta", "gamma"]
    return {"subjects": seed[:SEND_WIDTH_CAP]}


def fanout_research(state: ResearchState) -> list[Send]:
    subjects = state["subjects"][:SEND_WIDTH_CAP]
    return [Send("research", {"q": s}) for s in subjects]


def research_worker(state: WorkerState) -> dict[str, Any]:
    return {"findings": [f"finding:{state['q']}"]}


def synthesize(state: ResearchState) -> dict[str, Any]:
    return {"report": "|".join(sorted(state["findings"]))}


def build_research_graph(checkpointer: MemorySaver | None = None):
    builder = StateGraph(ResearchState)
    builder.add_node("plan", plan_research)
    builder.add_node("research", research_worker)
    builder.add_node("synthesize", synthesize)
    builder.add_edge(START, "plan")
    builder.add_conditional_edges("plan", fanout_research)
    builder.add_edge("research", "synthesize")
    builder.add_edge("synthesize", END)
    return builder.compile(checkpointer=checkpointer or MemorySaver())


class ScalarState(TypedDict):
    foo: str


def _fanout_clobber(_state: ScalarState) -> list[Send]:
    return [Send("worker", {"foo": "a"}), Send("worker", {"foo": "b"})]


def _worker(state: ScalarState) -> dict[str, Any]:
    return {"foo": state["foo"]}


def build_clobber_graph():
    builder = StateGraph(ScalarState)
    builder.add_node("split", lambda s: {})
    builder.add_node("worker", _worker)
    builder.add_edge(START, "split")
    builder.add_conditional_edges("split", _fanout_clobber)
    builder.add_edge("worker", END)
    return builder.compile()


class CycleState(TypedDict):
    n: int


def build_cycle_graph():
    builder = StateGraph(CycleState)
    builder.add_node("tick", lambda s: {"n": s["n"] + 1})
    builder.add_edge(START, "tick")
    builder.add_conditional_edges("tick", lambda s: "tick")
    return builder.compile()


def _offline() -> None:
    cid = str(uuid.uuid4())
    tenant = "acme"
    log = build_logger(cid, tenant, thread_id="acme:ticket-1")
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def _sleep(s: float) -> None:
        slept.append(s)

    asyncio.sleep = _sleep  # type: ignore[method-assign]
    try:
        async def once() -> int:
            raise TransientError("429", retry_after=0.4)

        async def _retry_case() -> None:
            try:
                await retry_with_jitter(once, log=log, attempts=2)
            except TransientError:
                pass

        asyncio.run(_retry_case())
        assert slept and abs(slept[0] - 0.4) < 1e-9
    finally:
        asyncio.sleep = real_sleep  # type: ignore[method-assign]

    async def _breaker_case() -> BreakerStateMachine:
        br = BreakerStateMachine("llm:gpt-4.1", failure_threshold=1, recovery_seconds=0.0)

        async def boom() -> str:
            raise TransientError("529")

        try:
            await FallbackChain(boom, boom, br).invoke(log)
            raise AssertionError("expected degraded")
        except PermanentError:
            pass
        assert br.state is BreakerState.OPEN
        try:
            await br.allow()
        except CircuitOpenError:
            raise AssertionError("should be half-open") from None
        await br.record_success()
        assert br.state is BreakerState.CLOSED
        return br

    br = asyncio.run(_breaker_case())
    assert deterministic_degraded("t")["status"] == "degraded"

    proxy = ZeroTrustToolProxy(frozenset({"send_email", "search_kb"}), frozenset({"send_email"}))
    try:
        proxy.execute(principal="user:1", tool="send_email", args={"to": "a"}, hitl_approved=False)
        raise AssertionError("hitl")
    except PermanentError as exc:
        assert "hitl_required" in str(exc)
    try:
        proxy.execute(principal="user:1", tool="shell", args={}, hitl_approved=True)
        raise AssertionError("rbac")
    except PermanentError as exc:
        assert "rbac_deny" in str(exc)
    assert proxy.execute(principal="user:1", tool="search_kb", args={"q": "x"}, hitl_approved=False).startswith("ok:")

    saver = MemorySaver()
    ticket = build_ticket_graph(saver)
    thread = str(uuid.uuid4())
    cfg: dict[str, Any] = {"configurable": {"thread_id": thread}, "recursion_limit": DEFAULT_RECURSION_LIMIT}
    paused = ticket.invoke(
        {
            "ticket_id": "T-1001",
            "messages": [{"role": "user", "content": "refund please"}],
            "draft": "",
            "decision": "",
            "sent": 0,
        },
        cfg,
    )
    assert paused["sent"] == 0 and paused["__interrupt__"]
    assert paused["draft"].startswith("ticket T-1001")
    resumed = ticket.invoke(Command(resume={"decision": "approve"}), cfg)
    assert resumed["sent"] == 1 and resumed["decision"] == "approve"
    assert ticket.get_state(cfg).next == ()

    reject_thread = str(uuid.uuid4())
    reject_cfg: dict[str, Any] = {
        "configurable": {"thread_id": reject_thread},
        "recursion_limit": DEFAULT_RECURSION_LIMIT,
    }
    ticket.invoke(
        {
            "ticket_id": "T-1002",
            "messages": [{"role": "user", "content": "no"}],
            "draft": "",
            "decision": "",
            "sent": 0,
        },
        reject_cfg,
    )
    rejected = ticket.invoke(Command(resume={"decision": "reject"}), reject_cfg)
    assert rejected["decision"] == "reject" and rejected["sent"] == 0

    research = build_research_graph(MemorySaver())
    fan = research.invoke(
        {"subjects": ["alpha", "beta", "gamma"], "findings": [], "report": ""},
        {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": DEFAULT_RECURSION_LIMIT},
    )
    assert sorted(fan["findings"]) == ["finding:alpha", "finding:beta", "finding:gamma"]
    assert "finding:alpha" in fan["report"]

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

    dsn = os.environ.get("LANGGRAPH_POSTGRES_DSN", "")
    postgres_configured = bool(dsn)
    if postgres_configured:
        postgres_saver(dsn)

    print(
        json.dumps(
            {
                "ok": True,
                "cid": cid,
                "breaker": br.state.value,
                "ticket_sent": resumed["sent"],
                "reject_sent": rejected["sent"],
                "findings": sorted(fan["findings"]),
                "degraded": deterministic_degraded("t")["status"],
                "postgres_configured": postgres_configured,
                "thread_id_uuid": thread,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    _offline()
```

**Behavior encoded (maps to §§1–4):**

- Full-jitter **HTTP** retries; `Retry-After` honored iff \(0 < t \leq 60\); `GraphRecursionError` / `InvalidUpdateError` / RBAC are **not** retried.
- Breaker closed → open → half-open (`recovery_seconds=0` in the test so the probe is immediate); 429-with-RA does not trip the policy in comments; fallback primary → secondary → `status: "degraded"`. **PermanentError does not failover.**
- JSON logs carry `correlation_id` + tenant + `thread_id` + node.
- **TypedDict** `TicketState` with `add_messages`; **no** static edge from `review` to `send_email` (only `Command.goto`) so `#5829` cannot double-fire.
- **HITL** `interrupt()` on `review`; resume `Command(resume={"decision": "approve"})` sends **once**; reject never sends. Draft and send are **different nodes**.
- **`Send` fan-out** with `operator.add` on `findings`; width capped at **8**.
- Missing reducer → `InvalidUpdateError`. Cycle + `recursion_limit=8` → `GraphRecursionError`.
- `ZeroTrustToolProxy` wraps ToolNode-equivalent side effects: principal required, allowlist, HITL on `send_email`.
- `postgres_saver(dsn)` is complete (`autocommit`, `dict_row`, `.setup()`); offline test uses **MemorySaver**.

**Interview talking point:** retries with jitter handle 529; they do not cap a cyclic graph. Reducers + `interrupt()` on a separate node from side effects + `recursion_limit` on the invoke dict are three different classes.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers from the research file; scale-up arithmetic marked **[inferred]**.

### Scenario 1 — Ticket HITL workflow (days-long wait, audit)

**Problem statement.** Multi-tenant support: classify → retrieve → draft reply → **adjuster approval** → send. Pause **days**, no GPU/worker burn, replayable, SSO. Volume **1k tickets/day**. Budget **[inferred]:** GPT-4.1 **~$37.20 / 1k** tickets + **~$0.04 / 1k** serverless compute at 2 vCPU-s; Dedicated S ~**$390/mo** floor. Constraint: `thread_id` = ticket UUID (**≤255 chars**); `durability="sync"` on the send path; tools read `langgraph_auth_user`; **no** API keys in state; resume `Command(resume={"decision": "approve"|"edit"|"reject", "edits": ...})` with the **same** thread. Eval success = correct send/no-send, **not** “the graph returned.”

**Proposed architecture.**

```
                    ┌──────────────────────────────────────────────────────────┐
                    │ EDGE  auth, tenant TPM, correlation-id, PII redact       │
                    │ thread_id = ticket UUID  (never user+url concatenation)  │
                    │ 25 MB cap; 409 if run already executing on this thread   │
                    └────────────────────────────┬─────────────────────────────┘
                                                 │
                    ┌────────────────────────────▼─────────────────────────────┐
                    │ CONTROL  Agent Server (or Temporal plugin for week waits)│
                    │  PREGEL: START→classify→retrieve→draft→review→send→END   │
                    │  review = interrupt(); NO add_edge(review, send)         │
                    │  create_agent MAY sit inside classify/draft nodes        │
                    │  recursion_limit=50 (ticket ≠ research); RemainingSteps  │
                    │  THREAD LEASE ≤1 run / ticket; N_JOBS_PER_WORKER=10      │
                    │  CHECKPOINTER Postgres durability=sync around send       │
                    │  STORE (org, user, "prefs") — not in messages channel    │
                    │  CircuitBreaker(llm) ≠ CircuitBreaker(email)             │
                    └─────┬───────────────────────────────┬────────────────────┘
                          │                               │
                          ▼                               ▼
                    ┌──────────────────┐            ┌─────────────────────────┐
                    │ DATA  Generation │            │ DATA  ToolNode          │
                    │ 3 LLM: classify, │            │ send_email only after   │
                    │ draft, post-HITL │            │ Command(resume=approve) │
                    │ send (optional)  │            │ retrieve is deterministic│
                    └────────┬─────────┘            └──────────┬──────────────┘
                             │                                 │
                    ┌────────▼─────────┐            ┌──────────▼──────────────┐
                    │ TOOL PROXIES     │            │ PERSIST  ckpt + WORM    │
                    │ Zero-Trust wrap  │            │ hashed draft, HITL      │
                    │ ticket body =    │            │ actor, checkpoint_id    │
                    │ untrusted obs    │            │ Kafka: send → Signal    │
                    └──────────────────┘            └─────────────────────────┘
```

**Technology choices.** Raw `StateGraph` for the outer machine; `create_agent` only **inside** classify/draft if you need a ReAct lookup (04). Agent Server Dedicated if customer-facing p99 matters; Temporal if HITL is **weeks** and you do not want always-on workers. Auth: `@auth.authenticate`; resume authenticated as the **same** role. Instrument `langgraph_step`, `__interrupt__` age, send count (**must be 0 or 1**), 409 rate. A 1% lost-interrupt rate at 1k tickets is **10** dropped approvals — incident, not a rounding error.

**Trade-off evaluation matrix.**

| Dimension | A. `InMemorySaver` + FastAPI `invoke` holding the worker for HITL; send in the same node as `interrupt()` | B. Recommended: Postgres / Agent Server + `interrupt()` on `review` + `durability="sync"` on send + UUID `thread_id` + ToolNode wrap | C. `create_agent` only (model⇄tools) with `HumanInTheLoopMiddleware`, no raw graph |
| --- | --- | --- | --- |
| **Cost / 1k** | Same **[inferred] $37.20** model until a restart **replays** classify+draft ($×2) or a runaway loop hits 1000 ticks | Mix **[inferred] $37.20** + ~$0.04 compute; HITL wait **$0**; Dedicated S **~$390/mo** if you buy the floor | Middleware summarization / judge **adds** calls; still 3 logical LLM hops if you keep the skeleton |
| **Latency** | Worker tied up for **days**; p99 = process lifetime | Machine p50 **[inferred] <8 s** pre-HITL; p99 = **human SLA**; Temporal signal = no CPU | Extra middleware hops; `ainvoke` HITL bug risk ([#34974](https://github.com/langchain-ai/langchain/issues/34974)) |
| **Ops complexity** | Looks simple until pod recycle drops every parked ticket | Medium (saver, lease, auth, WORM, drain) | Low graph, high “why didn’t we END” when the path is not ReAct |
| **Security posture** | Anyone with RAM dump has transcripts; resume is whoever hits the process; send **re-fires** on resume | SSO resume; secrets in `context=`; send is a **separate** node; traces stripped; EncryptedSerializer | HITL on tools is good; no `Send`/join; still need a checkpointer + `thread_id` |
| **Scalability ceiling** | 1k parked tickets = 1k stuck workers | `N_JOBS_PER_WORKER=10` on **running** ticks; parked HITL is a row in Postgres | Fine for 1–4 tool hops; cannot express days-long wait without the same Platform anyway |

**Decision rationale.** **B** is the only design that treats HITL as a **durable super-step**, not a blocked socket. A fails the money-and-audit exam (`InMemorySaver`, resend-on-resume). C is the right **inner** loop (04) and the wrong **outer** machine — a ticket is classify/retrieve/draft/review/send, not model⇄tools until silence. Quote: GPT-4.1 **$37.20 / 1k**; platform compute **~$0.04 / 1k**; split **draft** and **send**.

### Scenario 2 — `Send` research fan-out (N=8)

**Problem statement.** Overnight / hours-OK research: planner emits \(N\) questions; workers retrieve+summarize **in parallel**; reducer ranks; synthesizer writes the memo. Failure mode is **cost and dropped writes**, not 800 ms p95. Budget **[inferred]:** GPT-4.1 **~$124 / 1k** at \(N=8\) + 2 LLM bookends. Latency ≈ planner + max(worker) + synthesizer, **not** \(N\times\) serial. Constraint: `subjects: list[str]`; `findings: Annotated[list[str], operator.add]`; cap \(N\) at **8** in the router; optional per-`Send` timeout; parent `recursion_limit` 200–1000 **and** inner `create_agent` call limits if a worker is itself a ReAct loop; `max_budget_usd` **$2–5**/job (04).

**Proposed architecture.**

```
  ┌─────────────┐    ┌─────────────────────────────────────────────────────────┐
  │ Batch job / │───▶│ CONTROL  Agent Server queue (or Temporal Workflow)      │
  │ analyst UI  │    │  PREGEL: START → plan → [Send("research") × N]          │
  │             │    │          → synthesize → END                             │
  │             │    │  fanout router: cap N=8; NEVER trust LLM list unbounded │
  │             │    │  findings: Annotated[list[str], operator.add]           │
  │             │    │  workers MAY call create_agent inside the node          │
  │             │    │  HOP: recursion_limit on parent; inner max_turns=3      │
  │             │    │  CHECKPOINTER: plan + findings + report; Store blobs    │
  │             │    │  THREAD LEASE 1 run / job id; reject 409 on retry storm │
  └─────────────┘    └───────────┬───────────────────────────┬─────────────────┘
                                 │                           │
                                 ▼                           ▼
                     ┌─────────────────────┐     ┌─────────────────────────────┐
                     │ DATA  Generation    │     │ TOOL PROXIES  web_search    │
                     │ planner (Sol/4.1)   │     │ snippets = untrusted        │
                     │ workers Luna/Haiku  │     │ Zero-Trust MCP wrap         │
                     │ synthesizer Sol     │     │ HITL before send/write      │
                     └──────────┬──────────┘     └──────────────┬──────────────┘
                                ▼                               ▼
                     ┌─────────────────────────────────────────────────────────┐
                     │ PERSIST  findings list (reducer) + worker blobs + WORM  │
                     │          (cid, Send width, hashed queries, frozen_goal) │
                     └─────────────────────────────────────────────────────────┘
```

**Technology choices.** Raw `StateGraph` **because** of `Send`. Do not interleaved-ReAct the planner (04 ReWOO/LLMCompiler result). Persist the **plan object**; crash mid-worker: pending writes skip successful siblings. Workers on Luna (**[inferred] $7 / 1k** if the whole 10-call skeleton were Luna — mix Sol planner + Luna workers in production). When **not** to use `Send`: live incident troubleshooting where the next query cannot be foreseen — then ReAct with a tight hop cap (04 Scenario 1).

**Trade-off evaluation matrix.**

| Dimension | A. Serial ReAct / 8 sequential research hops (no `Send`, no reducer) | B. Recommended: `Send` fan-out N=8 + `operator.add` + width cap + inner worker fuses | C. Unbounded `Send` from an LLM-invented list; `foo: str` LastValue workers; `recursion_limit` stuffed in `configurable` |
| --- | --- | --- | --- |
| **Cost / 1k** | Still ~**10** LLM calls if you run 1+8+1, but **quadratic** prefix if interleaved (04); easy to loop past 8 | **[inferred] $124 / 1k** GPT-4.1; Luna **$7 / 1k**; dollars × N, ticks **≠** N | Unbounded N → unbounded $; `recursion_limit` **ignored** inside `configurable` → 1000-tick incident |
| **Latency** | \(N\times\) worker p95 (serial) | **[inferred]** planner + **max(worker)** + synth; one fan-out super-step | `InvalidUpdateError` crash at the barrier (7 of 8 dropped) **or** a 1000-tick cycle |
| **Ops complexity** | One graph until 10% HotpotQA-style divergence (04) | Medium (cap, reducer tests, per-Send timeout, blob store) | Looks like one router until production |
| **Security posture** | One injected snippet can steer **all** subsequent hops (04 PlanFlip) | Frozen goal; inner allowlist=1 tool; blast radius **3** inner turns × **8** workers | LLM-chosen width is an agency bug (LLM06); no HITL before write |
| **Scalability ceiling** | Prefix+observations quadratic; 8 serial model calls on the hot path | Worker pool × search RPM; `N_JOBS_PER_WORKER=10` jobs, each with 8 parallel LLM calls — **budget TPM** | Fan-out TPM spike with no cap; 409 storms if the client retries the same `thread_id` |

**Decision rationale.** **B** is the only design that keeps **fan-out on one super-step** (the Pregel result) while bounding **dollars × N** in the router (the economics result) and **not** clobbering `findings` (the reducer result). A is the 20k-input interview fail from 04 applied to a graph that already has `Send`. C is the production outage: missing reducer + unbounded width + ignored fuse. Quote: GPT-4.1 **$124 / 1k** at N=8; workers share **one** tick; cap `len(subjects)` **in the router**.

---

## Key numbers to memorize

| Number | What |
| --- | --- |
| **$37.20 / $124 per 1k** | GPT-4.1 ticket (3 LLM) / Send N=8 (10 LLM) **[inferred]** |
| **$2.10 / $7 per 1k** | Same skeletons on GPT-6 Luna **[inferred]** |
| **~$0.04 / 1k** | Platform compute at 2 vCPU-s **[inferred]** |
| **~$390/mo** | Dedicated Small always-on **[inferred]** |
| **$39 / $1.50 / $1.00** | Plus seat / LCU / LSU; runtime 0.045 LCU/vCPU-hr → **$0.0675/vCPU-hr [inferred]** |
| **2026-10-01** | Old per-run prices die; LCU/LSU thereafter |
| **1000** | `recursion_limit` default since **1.0.6** (the **25** is not a nesting cap) |
| **10 / 409** | `N_JOBS_PER_WORKER`; `reject` → HTTP **409** |
| **≤1 run / thread_id** | Agent Server invariant |
| **25 MB / 413** | Cloud payload cap |
| **<255 chars / ~2704 bytes** | Docs `thread_id` vs Postgres btree |
| **3 / 0.5s / 2.0 / 128s** | `RetryPolicy` attempts / initial / backoff / cap |
| **exit / async / sync** | Durability; Agent Server default **async** |
| **7 stream modes** | `values`, `updates`, `messages`, `custom`, `checkpoints`, `tasks`, `debug` |
| **1.0 GA 2025-10-22** | MIT; Python ≥3.10; `MessageGraph` / `create_react_agent` deprecated |
| **10** | OpenAI Agents SDK `max_turns` default — **not** 1000 super-steps |

**Interview closer:** “The model is an untrusted planner inside a Pregel graph I drew. I compile a `StateGraph` with reducers, cap `Send` width in the router, park HITL on `interrupt()` with Postgres and a thread lease, put `recursion_limit` on the invoke dict, and I do not confuse that fuse with OpenAI `max_turns` or a CrewAI role. Ticket skeleton **$37.20 / 1k**; Send N=8 **$124 / 1k**; one run per `thread_id` or you get 409.”
