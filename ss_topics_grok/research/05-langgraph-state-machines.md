# Research: Master One Framework — LangGraph State Machines

**Date researched**: 2026-09-23
**Sources consulted**: 98

Scope: **LangGraph as the production agent state machine** — `StateGraph` / Pregel super-steps, typed channels and reducers, `Send` / `Command`, checkpointers vs Store, interrupts / HITL, subgraphs, streaming, `create_agent` vs raw graphs, `recursion_limit` / `RemainingSteps`, durability modes, and LangSmith Deployment (formerly LangGraph Platform) vs self-host. CrewAI roles and OpenAI Agents SDK handoffs appear in **§1.13 only**. ReAct thought–action–observation math, paper tables, and loop-fuse comparisons live in [`04-agent-loop-patterns.md`](04-agent-loop-patterns.md); this file does **not** recopy them. Vendor list prices used in §2 are from [`01-python-llm-foundations.md`](01-python-llm-foundations.md) (2026-09-23). ⚠️ No unpublished p50/p95/p99 for checkpoint writes or `invoke` overhead is invented. `$ / 1k graph runs` figures are **[inferred]** from a stated node/LLM-call skeleton × list prices, plus published LCU/LSU SKUs — LangGraph OSS has **no** per-run SKU.

Invariant: **the model never executes tools, edges, interrupts, or checkpoints**. It emits structured actions or text; Pregel schedules actors; `ToolNode` (or your node) runs side effects; the checkpointer commits at super-step (and optionally task) boundaries ([Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api); [Pregel runtime](https://docs.langchain.com/oss/python/langgraph/pregel); [ToolNode](https://reference.langchain.com/python/langgraph.prebuilt/prebuilt/tool_node/ToolNode)).

LangGraph **1.0 GA** was announced **2025-10-22** (GitHub tag `1.0.0` **2025-10-17**). It is **MIT**, requires **Python ≥3.10**, and “can be used without LangChain” as a framework while still depending on `langchain-core` (PyPI `langgraph` 1.2.x: `langchain-core>=1.4.7,<2`) ([1.0 blog](https://www.langchain.com/blog/langchain-langgraph-1dot0); [GitHub README](https://github.com/langchain-ai/langgraph); [PyPI](https://pypi.org/project/langgraph/)).

---

## 1. System Topology & Mechanics

### 1.1 Pregel control plane vs node data plane

LangGraph’s runtime is named after Google Pregel (SIGMOD 2010): computation proceeds in **bulk-synchronous super-steps**. During super-step \(S\), actors read messages from \(S-1\), run in parallel, and send messages that become visible only at \(S+1\) ([Malewicz et al.](https://doi.org/10.1145/1807167.1807184); [LangGraph Pregel](https://docs.langchain.com/oss/python/langgraph/pregel)).

| Plane | Owns | Does not own |
| --- | --- | --- |
| **Control (Pregel)** | Plan / execute / update loop; which nodes are active; `recursion_limit`; `thread_id` / `checkpoint_ns` / `checkpoint_id`; durability mode; interrupt matching; `Send` fan-out; stream mux | Transformer weights, tool HTTP |
| **Data (nodes / ToolNode)** | LLM calls, tools, MCP `tools/call`, custom Python | Super-step commit, edge routing (except via returned `Command`) |
| **Persistence control** | Checkpointer `put` / `put_writes` at super-step and task boundaries; Store `put`/`search` across threads | Model KV cache |

Each Pregel step ([runtime docs](https://docs.langchain.com/oss/python/langgraph/pregel)):

1. **Plan** — select actors that subscribe to channels updated last step (first step: input channels).
2. **Execution** — run selected actors **in parallel** until all complete, one fails, or a timeout. Channel updates are **invisible** to other actors until the next step.
3. **Update** — apply writes through each channel’s reducer.

Repeat until no actors are selected or the step cap fires (`GraphRecursionError`). Compiling a `StateGraph` or decorating `@entrypoint` **produces a `Pregel` instance**. Nodes in the compiled graph are `PregelNode` actors; state keys and branch signals are **channels** (`LastValue`, `Topic`, `BinaryOperatorAggregate`, `EphemeralValue`, and — `langgraph>=1.2`, beta — `DeltaChannel`) ([Pregel](https://docs.langchain.com/oss/python/langgraph/pregel); [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)).

A super-step is one “tick”: parallel nodes share a tick; a linear `START → A → B → END` is **separate** ticks for input, A, and B, and therefore **separate checkpoints** ([Checkpointers — super-steps](https://docs.langchain.com/oss/python/langgraph/checkpointers)). Graph API wording: nodes start **inactive**; a node becomes **active** when a message arrives on an incoming channel; nodes with no incoming messages vote to **halt**; execution ends when all are inactive and nothing is in transit ([Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)).

### 1.2 StateGraph, MessageGraph, typed state, reducers, `add_messages`, channels

**Unit of composition:** `StateGraph[State]`. `State` is a TypedDict, dataclass, or Pydantic model. Each key is a **channel**. Default channel is `LastValue` — last write wins. Annotate with a reducer `(Value, Value) -> Value` so parallel writes **merge** instead of clobber ([StateGraph](https://reference.langchain.com/python/langgraph/graph/state/StateGraph); [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)).

| Channel / annotation | Semantics | Typical use |
| --- | --- | --- |
| `LastValue` (default) | Overwrite | Scalars, latest draft |
| `Annotated[list, operator.add]` | Concatenate | Fan-in lists from `Send` |
| `Annotated[list[AnyMessage], add_messages]` | Append by default; **replace by message `id`**; honor `RemoveMessage` | Transcript |
| `BinaryOperatorAggregate` | Running fold | Counters |
| `Topic(..., accumulate=True)` | PubSub accumulate | Low-level Pregel |
| `DeltaChannel(bulk_reducer, snapshot_frequency=K)` | Persist **deltas**, reconstruct on read; snapshot every \(K\) steps | Long `messages` threads (`>=1.2`, beta) |
| `RemainingSteps` (managed) | Runtime-owned int; not user-initialized | Proactive fuse |
| `UntrackedValue` | Not checkpointed | Secrets that must not land in snapshots (forum pattern; not a substitute for auth) |

**Without a reducer, a node that returns `{"messages": [new]}` wipes prior history.** That is the dominant “lost messages” bug. `operator.add` appends but **cannot** overwrite an edited HITL message (it would duplicate). `add_messages` appends new IDs and replaces matching IDs — required for human edits ([Graph API — messages](https://docs.langchain.com/oss/python/langgraph/graph-api); [`add_messages`](https://reference.langchain.com/python/langgraph/graph/message/add_messages)).

**Parallel `LastValue` writes in one super-step raise `InvalidUpdateError`** unless that key has a reducer. Two `Send` workers writing `foo: str` (no reducer) is a crash, not last-write-wins in production graphs. Time-travel `update_state` with parallel predecessors also needs explicit `as_node=` or you get the same error ([Use time-travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel); [StateGraph source](https://github.com/langchain-ai/langgraph/blob/a6b8098548ec2c28ca58307782845e91465328e3/libs/langgraph/langgraph/graph/state.py)).

Compiled graphs expose `graph.nodes` (`PregelNode`s including `__start__`) and `graph.channels` (your keys **plus** ephemeral branch channels like `branch:write_essay:__self__:score_essay` and `start:write_essay`). Those extra channels are the control plane’s edge bookkeeping, not user state ([Pregel — high-level API](https://docs.langchain.com/oss/python/langgraph/pregel)).

`MessagesState` is the prebuilt TypedDict with a single `messages` key using `add_messages`. Subclass it for extra fields. Delete: `RemoveMessage(id=...)` or `RemoveMessage(id=REMOVE_ALL_MESSAGES)` where `REMOVE_ALL_MESSAGES == "__remove_all__"` ([Add memory](https://docs.langchain.com/oss/python/langgraph/add-memory); [`REMOVE_ALL_MESSAGES`](https://reference.langchain.com/python/langgraph/graph/message/REMOVE_ALL_MESSAGES)).

**`MessageGraph`** is a `StateGraph` whose entire state is `Annotated[list[AnyMessage], add_messages]`. **Deprecated in LangGraph 1.0, removal in 2.0.** Migrate to `StateGraph` + a `messages` key ([v1 migration](https://docs.langchain.com/oss/python/migrate/langgraph-v1); [commit #5843](https://github.com/langchain-ai/langgraph/commit/0bd7dd2c52c80c431ce72ab647daa548e019cba0)).

`DeltaChannel` (`>=1.2`, beta): without it, the **full** accumulated list is re-serialized into every checkpoint. With it, only the step’s writes are stored; the bulk reducer must be **associative**. `snapshot_frequency=K` bounds read replay to \(K\) steps. Changing a live thread from delta to non-delta (or downgrading below 1.2) **cannot reconstruct** those checkpoints ([Pregel — DeltaChannel](https://docs.langchain.com/oss/python/langgraph/pregel)).

Runtime **context** (`context_schema` + `invoke(..., context=...)`) is **not** graph state and is the documented place for per-run tokens / model names ([Graph API — runtime context](https://docs.langchain.com/oss/python/langgraph/graph-api)). Forum consensus: do **not** put bearer tokens in `state` or `config` (both can be checkpoint-associated); use `Runtime[Context].context` or `UntrackedValue` ([forum #2416](https://forum.langchain.com/t/how-to-send-sensitive-data-like-auth-tokens-across-nodes-without-getting-stored-in-the-checkpoint/2416)).

### 1.3 Nodes, edges, conditional edges, `Command`, `Send`

**Nodes** are Python callables `State -> Partial[State]` (sync or async). They do not own control flow unless they return `Command`. Signatures may also take `config: RunnableConfig` and `runtime: Runtime[Context]` (`store`, `stream_writer`, `execution_info`, `heartbeat`, `control` for drain). Compilation validates connectivity (orphaned nodes). `.compile(checkpointer=..., store=..., interrupt_before=..., interrupt_after=...)` injects persistence and static breakpoints; the same interrupt lists can be overridden **per invoke** ([Use the graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api); [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts); [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)).

**Edges:** `add_edge(src, dst)` is static. `add_conditional_edges(src, router)` — router returns a node name, `END`, a list of names (all run next super-step **in parallel**), or `list[Send]` ([Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)).

**`Command`** from a node (or a tool) combines a state write with a hop ([`Command`](https://reference.langchain.com/python/langgraph/types/Command); [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)):

| Field | Role |
| --- | --- |
| `update` | Partial state (same as returning a dict) |
| `goto` | Node name, sequence of names, `Send`, or sequence of `Send` |
| `resume` | Value for `interrupt()` — **the only `Command` field meant as `invoke`/`stream` input** |
| `graph` | `None` = current; `Command.PARENT` = closest parent (subgraph handoff) |

Return-type annotation `Command[Literal["next_node"]]` is required so Studio/rendering knows the dest. **`goto` augments routing; it does not suppress `add_edge`.** If both exist, **both destinations run** (static first, then `goto` in the documented regression test / PR) ([issue #5829](https://github.com/langchain-ai/langgraph/issues/5829); [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)). For parent updates of a **shared** key from a subgraph via `Command.PARENT`, the **parent** channel must have a reducer ([Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)).

**`Send` (map-reduce):** return `[Send("worker", {**slice})]` from a conditional edge (or `Command.goto`). Worker count is **data-dependent**. Each `Send` may carry a **different** state shape than the parent. Fan-in **requires** a reducer on the shared channel (usually `operator.add` on a list). Optional third argument: per-push `timeout` / `TimeoutPolicy` (`>=1.2`) ([Graph API — Send](https://docs.langchain.com/oss/python/langgraph/graph-api); [`Send`](https://reference.langchain.com/python/langgraph/types/Send); [Fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)).

### 1.4 Tool data plane: `ToolNode`

`langgraph.prebuilt.ToolNode` executes `AIMessage.tool_calls` in **parallel**, injects `InjectedState` / `InjectedStore` / `ToolRuntime`, and can return `Command` from tools. Default `handle_tool_errors` catches **invocation** (bad args) errors into a `ToolMessage` and **re-raises execution** errors. For a stock ReAct loop, LangChain’s `create_agent` already wires `ToolNode` + `tools_condition` (`"tools"` vs `"__end__"`). Use a raw `ToolNode` when you need custom routing or error policy ([ToolNode](https://reference.langchain.com/python/langgraph.prebuilt/prebuilt/tool_node/ToolNode); [`tools_condition`](https://reference.langchain.com/python/langgraph.prebuilt/tool_node/tools_condition); [prebuilt README](https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/README.md)). In-process parallelism is **not** a distributed lock around Stripe — see 03 for dispatcher IDs.

### 1.5 Checkpointers, `thread_id`, `checkpoint_ns`, time-travel

A **checkpoint** is a `StateSnapshot` at a super-step: `values`, `next`, `config` (`thread_id`, `checkpoint_ns`, `checkpoint_id`), `metadata` (`source` ∈ `{input, loop, update, fork}`, `writes`, `step`), `created_at`, `parent_config`, `tasks`. `checkpoint_id` is a monotonically increasing **ULID** (lexicographic newest-last) ([Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers); [Checkpointing reference](https://reference.langchain.com/python/langgraph/checkpoints)).

**`thread_id`** is the primary key. Without it the saver cannot load/resume. Docs: keep Postgres `thread_id` **under 255 characters** ([Persistence troubleshooting](https://docs.langchain.com/oss/python/langgraph/persistence)). GitHub: composite btree keys can still fail earlier — `ProgramLimitExceeded: Index row size … exceeds btree version 4 maximum 2704` on `checkpoint_blobs_pkey` ([issue #6239](https://github.com/langchain-ai/langgraph/issues/6239)). Use UUID / sha256, not concatenated user+session+url.

**`checkpoint_ns`:** `""` = root graph; `"node_name:uuid"` = subgraph invoked as that node; nested joined with `|` (`"outer:uuid|inner:uuid"`) ([Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).

Implementations ([reference table](https://reference.langchain.com/python/langgraph/checkpoints)):

| Saver | Package | Use |
| --- | --- | --- |
| `InMemorySaver` / `MemorySaver` | `langgraph-checkpoint` (bundled) | Tests only; **RAM; lost on restart** |
| `SqliteSaver` / `AsyncSqliteSaver` | `langgraph-checkpoint-sqlite` | Local demos; **not** multi-writer prod |
| `PostgresSaver` / `AsyncPostgresSaver` | `langgraph-checkpoint-postgres` | Production; LangSmith default |
| Agent Server injected saver | (you do not compile one) | Cloud / self-host runtime |

Postgres: call `.setup()` once; if you pass a raw connection, **`autocommit=True`** and **`row_factory=dict_row`**. Schema (simplified): `checkpoints` PK `(thread_id, checkpoint_ns, checkpoint_id)` plus a **writes** table for per-task blobs. Checkpoint dict version **`v: 4`** in library examples. Default serde: `JsonPlusSerializer` (ormsgpack + extended JSON). Optional `EncryptedSerializer.from_pycryptodome_aes()` reads **`LANGGRAPH_AES_KEY`** ([checkpoint-postgres README](https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres); [Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).

**Pending writes:** if one node in a parallel super-step fails, successful siblings’ writes are already in `checkpoint_writes` and **are not re-run** on resume. Time travel still resumes from **full** super-step snapshots, not mid-node ([Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).

**Time travel** ([Use time-travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel)):

- **Replay:** `get_state_history` (newest first) → `invoke(None, checkpoint.config)`. Nodes **before** the checkpoint are skipped; nodes **after** **re-execute** (LLM, HTTP, `interrupt()` all fire again). Replay of a terminal checkpoint is a no-op.
- **Fork:** `update_state(checkpoint.config, values=...)` creates a **new** checkpoint (`source="update"` / `"fork"`); original history stays. Then `invoke(None, fork_config)`.
- Interrupts **always re-trigger** on replay/fork of that node.
- Default subgraph = **one parent super-step** — you cannot time-travel *inside* it unless the subgraph was compiled with `checkpointer=True`.

### 1.6 Store vs checkpointer (cross-thread memory)

| | Checkpointer | Store (`BaseStore`) |
| --- | --- | --- |
| Persists | Full graph snapshots | Application key-value |
| Scope | One `thread_id` | Across threads |
| Access | `configurable.thread_id` | `store.put/get/search` from nodes / `Runtime.store` |
| Use | Continuity, HITL, time travel, crash resume | User prefs, facts, RAG-ish memories |

Compile with both: `builder.compile(checkpointer=..., store=...)`. Namespaces are `tuple[str, ...]`, e.g. `(user_id, "memories")`. `asearch` default **`limit=10`**; overflow is **silent**. Prefix search: `("alice",)` also matches `("alice", "memories")`. Semantic search needs `index={embed, dims, fields}` at construction; otherwise `query=` is a no-op or `NotImplementedError`. Production backends: `PostgresStore`, `MongoDBStore`, `RedisStore`, `UpstashStore`. Agent Server provides a store; enable embeddings in `langgraph.json` ([Stores](https://docs.langchain.com/oss/python/langgraph/stores); [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence); [Add memory](https://docs.langchain.com/oss/python/langgraph/add-memory)). Parent graphs may **not** see subgraph channel writes immediately because of `checkpoint_ns` isolation — **use Store for facts that must cross graphs** ([Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)).

### 1.7 `interrupt` / HITL, `interrupt_before`, `Command(resume=)`

Two pause APIs, both **require a checkpointer + `thread_id`**. The graph waits **indefinitely** ([Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)).

| Mechanism | Where it pauses | Resume |
| --- | --- | --- |
| `interrupt(value)` inside a node | Dynamic, conditional | `Command(resume=...)` becomes the **return value** of `interrupt()` |
| `interrupt_before` / `interrupt_after` at compile **or** invoke | Static, between nodes | `invoke(None, config)` (no resume payload) |

Docs recommend **`interrupt()`** for production HITL; static breakpoints are for stepping/debug. On resume, the **whole node restarts**. Code **before** `interrupt()` re-runs — side effects must be idempotent, or wrap them in Functional API `@task` (completed task results restore from the checkpointer) ([`interrupt`](https://reference.langchain.com/python/langgraph/types/interrupt); [Functional API](https://docs.langchain.com/oss/python/langgraph/functional-api)). Multiple `interrupt()` calls in one node match resume values **by order** (per task). Parallel interrupts: map **interrupt id → resume value** in `Command(resume={id: value})` ([Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)).

Surfacing: `invoke` → `result["__interrupt__"]`; event streaming `stream_events(..., version="v3")` → `stream.interrupted` / `stream.interrupts`. `Command(update=...)` as **invoke input** is the wrong API for multi-turn chat — pass a plain dict ([Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)).

`create_agent` HITL: `HumanInTheLoopMiddleware(interrupt_on={tool: True|{allowed_decisions}})` plus a checkpointer. Resume shape is a **decisions** list, not a free-form string ([LangChain HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop); [built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)).

### 1.8 Subgraphs

A subgraph is a compiled graph used as a node, or invoked inside a node with a state mapper ([Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)).

| `checkpointer=` on subgraph | Behavior |
| --- | --- |
| `None` (default) | Per-invocation; inherits parent saver; HITL works; memory does **not** accumulate across parent calls |
| `True` | Per-thread; own `checkpoint_ns` history; **do not** call the same subgraph in parallel (checkpoint collisions) |
| `False` | Stateless function; **no** interrupts |

Interrupts still propagate to the top-level graph. Viewing nested state (`get_state(..., subgraphs=True)` / `stream.subgraphs`) requires the subgraph to be **statically discoverable** (added as a node or called inside a node), not hidden inside a tool with extra indirection ([Subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)). There is **no documented nesting-depth cap of 25** (see §5.6).

### 1.9 Streaming modes

Two surfaces ([Streaming](https://docs.langchain.com/oss/python/langgraph/streaming); [`StreamMode`](https://reference.langchain.com/python/langgraph/types/StreamMode)):

1. **Stream-mode API** — `stream` / `astream`. Modes (combinable): `values`, `updates`, `messages`, `custom`, `checkpoints`, `tasks`, `debug`.
2. **Event streaming v3** (`>=1.2` recommended) — typed projections (`messages`, `values`, `subgraphs`, `output`, `interrupts`) as independent iterators.

| Mode | Payload |
| --- | --- |
| `values` | Full state after each step (incl. interrupts) |
| `updates` | Per-node / per-task deltas (parallel updates emitted **separately**) |
| `messages` | `(token, metadata)` from any LLM inside nodes |
| `custom` | `get_stream_writer()` payloads |
| `checkpoints` | Same shape as `get_state()`; needs checkpointer |
| `tasks` | Task start/finish + errors; needs checkpointer |
| `debug` | checkpoints + tasks + extra metadata |

`version="v2"` unifies chunks as `{type, ns, data}` (`>=1.1`). v1: single mode = raw; multi-mode = `(mode, data)`; subgraphs = `(namespace, data)`. Custom requires at least one mode to be `"custom"`. Token UX is `messages`, not `values` ([Streaming](https://docs.langchain.com/oss/python/langgraph/streaming); [LangChain streaming](https://docs.langchain.com/oss/python/langchain/streaming)).

### 1.10 `create_agent` vs raw `StateGraph`

LangGraph v1 **deprecates** `langgraph.prebuilt.create_react_agent` in favor of `langchain.agents.create_agent`, which **returns a `CompiledStateGraph`** (still Pregel) plus middleware ([v1 migration](https://docs.langchain.com/oss/python/migrate/langgraph-v1); [what's new](https://docs.langchain.com/oss/python/releases/langgraph-v1); [`create_agent`](https://reference.langchain.com/python/langchain/agents/create_agent)).

`create_agent` loop: model node → if `tool_calls` then tools node → model … until no tool calls. Parameters include `middleware`, `response_format`, `state_schema`, `context_schema`, `checkpointer`, `store`, `interrupt_before` / `interrupt_after`. Built-in middleware that matters in production: `HumanInTheLoopMiddleware`, `SummarizationMiddleware` (example trigger **4000** tokens / keep **20** messages), `ModelCallLimitMiddleware`, `ToolCallLimitMiddleware`, `PIIMiddleware`, model/tool retry ([built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)). **Drop to raw `StateGraph`** when you need cycles that are not “model⇄tools”, `Send` map-reduce, `Command.PARENT` handoffs, custom join nodes, or mixed deterministic/LLM branches. Official pattern: call `create_agent` **inside** a StateGraph node ([Custom workflow](https://docs.langchain.com/oss/python/langchain/multi-agent/custom-workflow)).

Example inner+outer: a 3-node parent `rewrite → retrieve → agent` where `agent` is `create_agent(...).invoke({"messages": ...})` and only the parent is compiled with the production checkpointer — the inner agent’s tool loop still burns parent `recursion_limit` super-steps if you add it as a compiled subgraph rather than a plain function call. Count ticks before you ship.

Prebuilt ReAct historically **did not raise** `GraphRecursionError` when tools were still pending: `_are_more_steps_needed` emits **“Sorry, need more steps to process this request.”** when `remaining_steps < 2` and tool calls remain ([issue #5548](https://github.com/langchain-ai/langgraph/issues/5548); see 04 for the ReAct fuse). Prefer `RemainingSteps` routing or `ModelCallLimitMiddleware` / `ToolCallLimitMiddleware` on `create_agent` ([built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)).

### 1.11 `recursion_limit` and `RemainingSteps`

`recursion_limit` is a **top-level** invoke config key, **not** inside `configurable`. It counts **super-steps**, not Python stack frames and not “number of tools.” Parallel nodes in one tick count as **one**. Starting **langgraph 1.0.6**, default is **1000** (PR `#6676`). Older blogs and `langchain_core.RunnableConfig` still mention **25** — that is the historical LangChain runnable default, **not** current LangGraph ([Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api); [GRAPH_RECURSION_LIMIT](https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT); [1.0.4…1.0.6](https://github.com/langchain-ai/langgraph/compare/1.0.4...1.0.6)). Exceeding it raises `GraphRecursionError` (subclass of `RecursionError`) ([`GraphRecursionError`](https://reference.langchain.com/python/langgraph/errors/GraphRecursionError)).

**Proactive fuse:** add `remaining_steps: RemainingSteps` to state (managed channel — do not initialize or decrement). Docs’ example bails when `remaining_steps <= 2` ([Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api); [`RemainingSteps`](https://reference.langchain.com/python/langgraph/managed/is_last_step/RemainingSteps)). Step counter is also on `config["metadata"]["langgraph_step"]`.

### 1.12 LangGraph Platform / Cloud vs self-host

**LangGraph Platform was renamed LangSmith Deployment** (GA blog still uses the old name). Same Agent Server runtime; what changes is **who hosts control plane vs data plane** ([Deployment](https://docs.langchain.com/langsmith/deployment); [Platform GA](https://www.langchain.com/blog/langgraph-platform-ga)).

| Topology | Control plane | Data plane (Agent Servers + Postgres + Redis) | Plan |
| --- | --- | --- | --- |
| **Cloud** | LangChain (AWS/GCP) | LangChain | Plus+ |
| **Hybrid / BYOC** | LangChain | Your VPC | Enterprise |
| **Self-hosted full** | You (K8s + Helm) | You | Enterprise |
| **Standalone Agent Server** | None | You (Docker/Compose/K8s); BYO Postgres+Redis | License |

**The control plane never connects to the data plane.** A **listener** **polls** control-plane APIs ([Control plane](https://docs.langchain.com/langsmith/control-plane); [hybrid-legacy](https://docs.langchain.com/langsmith/hybrid-legacy)).

Agent Server resources: **assistants** (compiled graph + config), **threads** (checkpointed state), **runs** (invocations), **crons**. Persistence: core rows + checkpoints + store in **Postgres** (Mongo/custom checkpointer optional); **Redis is ephemeral** pub/sub + queue signaling — **no user data** ([Agent Server](https://docs.langchain.com/langsmith/agent-server)). Queue: **at most one run per `thread_id` at a time**. `N_JOBS_PER_WORKER` default **10** concurrent runs **per worker** (different threads). Split API vs queue with `queue.enabled: true`. Durability on runs: `async` (default), `sync`, `exit`; `checkpoint_during` is **deprecated** ([create-run API](https://docs.langchain.com/langsmith/agent-server-api/thread-runs/create-run-wait-for-output)). Double-texting: `multitask_strategy` `enqueue` (docs: default), `reject` (HTTP **409**), `interrupt`, `rollback` ([reject-concurrent](https://docs.langchain.com/langsmith/reject-concurrent); [enqueue-concurrent](https://docs.langchain.com/langsmith/enqueue-concurrent)).

Export compiled graphs (loaded **once** at container start) unless you need a **factory** per run. Server **replaces** any checkpointer you compiled ([Agent Server](https://docs.langchain.com/langsmith/agent-server); [Cloud features](https://docs.langchain.com/langsmith/cloud-platform-features)). Cloud payload max **25 MB** → **413**. Cloud regions **US / EU** (implied by org; **not migratable**). Serverless S/M/L = **1/2/4** vCPU, **2/5/9** GiB, **shared DB**, scale-to-zero **beta** (new pricing). Dedicated S/M/L = **3/5/10** vCPU, **6/12/24** GiB, dedicated DB **1/2/4** vCPU + **4/8/16** GiB ([Cloud features](https://docs.langchain.com/langsmith/cloud-platform-features)). `/mcp` Streamable HTTP requires `langgraph-api>=0.2.3`; **stateless per request** ([server-mcp](https://docs.langchain.com/langsmith/server-mcp)).

**Temporal plugin (Python, public preview):** graph as Workflow; nodes as Activities (`execute_in: "activity"`) or in-Workflow (must be deterministic). `interrupt()` only in Activity nodes; wait is a Temporal signal — **no worker CPU while HITL blocks**. Activity retry **re-runs the whole node**. Python **3.11+** for interrupt/contextvars ([Temporal LangGraph](https://docs.temporal.io/develop/python/integrations/langgraph)).

### 1.13 Contrast (only here): CrewAI roles vs OpenAI Agents SDK handoffs vs LangGraph graphs

| Metaphor | Primitive | Typed state | Durability / HITL | Fuse | Why **this module** is LangGraph |
| --- | --- | --- | --- | --- | --- |
| **LangGraph** | Pregel graph: nodes, channels, `Send`, subgraphs | Reducers + checkpoints | Checkpointer + `interrupt` + Platform/Temporal | `recursion_limit` default **1000** super-steps | The product **is** a state machine |
| **OpenAI Agents SDK** | `Agent` + `Runner` loop; **handoffs** transfer the user-facing reply; `as_tool` nests | `output_type` / `RunState` / sessions — not a reducer graph | Serialize `RunState`; you host the wait | `max_turns` default **10** (`None` disables) | Fast OpenAI-hosted tools; **not** cyclic map-reduce |
| **CrewAI** | **Crew** of role/goal/backstory agents inside a **Flow** event graph | Flow state + pydantic tasks | `@persist`, `@human_feedback`, AMP webhooks | Process sequential (default) vs hierarchical (`manager_llm` required) | Role-play teams; Flow is the outer app, not Pregel |

Handoffs: the specialist **takes over** the transcript ([Running agents](https://openai.github.io/openai-agents-python/running_agents/)). Crew hierarchical: a manager delegates ([Hierarchical process](https://docs.crewai.com/edge/en/learn/hierarchical-process)). LangGraph: **you** draw the edges; the LLM does not silently become the orchestrator unless you put a router node there. Pick LangGraph for this module because checkpoints, `Send`, time-travel, and durable `interrupt()` are first-class — the skills 04’s loops need when they leave the whiteboard. Do **not** restack all three runtimes in one HTTP handler.

---

## 2. Token Economics & NFR Metrics

### 2.1 Latency: what is (not) published

⚠️ **LangGraph does not publish p50/p95/p99 for `invoke`, checkpoint `put`, or Agent Server API.** LangSmith exposes Agent Server latency as a **metric you monitor**, not a contractual SLO ([Agent Server](https://docs.langchain.com/langsmith/agent-server)). End-to-end latency ≈ model TTFT + tool RTT × turns + queue wait + (optional) serverless cold start + checkpoint I/O.

Order of magnitude **[inferred, not a vendor SLO]**: a local Postgres `put` is typically **milliseconds**; a frontier model call is **hundreds of ms to seconds**. Checkpoint I/O is **not** the token bill, but `durability="sync"` **adds that write to the critical path** before the next super-step ([Durability](https://docs.langchain.com/oss/python/langgraph/checkpointers); [Thinking in LangGraph](https://docs.langchain.com/oss/javascript/langgraph/thinking-in-langgraph)). `durability="async"` (default) overlaps write with the next node — a crash can lose the in-flight checkpoint and **replay the last committed super-step** (at-least-once nodes).

Streaming: `messages` mode is what users feel as TTFT; `values` waits for the whole node. Event v3 lets you drain `stream.messages` while HITL is pending ([Streaming](https://docs.langchain.com/oss/python/langgraph/streaming); [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)).

### 2.2 Checkpoint size vs tokens

Checkpoints are **storage + CPU**, not LLM tokens. They become a **token** problem when every model node resubmits the full `messages` channel. Without `DeltaChannel`, docs: the full list is **re-serialized into every checkpoint**, so checkpoint bytes grow ~linearly with thread length ([Pregel — DeltaChannel](https://docs.langchain.com/oss/python/langgraph/pregel)). `add_messages` does not truncate; use `RemoveMessage`, summarization middleware, or Store offload (see 02 for cache breakpoints).

**[inferred] size sketch** (not measured here): 50 turns × ~2 KB/message ≈ 100 KB channel value × (super-steps ≈ turns) if you snapshot fully each tick → **megabytes per busy thread** before blobs/indexes. Prune or set retention; docs warn checkpoints **grow unboundedly** ([Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)). Semantic Store search adds **embedding** tokens (`text-embedding-3-small` **$0.02 / 1M** per 01) — typically << chat.

Cloud request cap **25 MB** ([Cloud features](https://docs.langchain.com/langsmith/cloud-platform-features)) — a bloated `invoke` payload or checkpoint patch can 413 **before** the model runs.

### 2.3 Reference graphs for `$ / 1k` **[inferred]**

Prices from 01 (standard, short context, 2026-09-23): GPT-4.1 **$2 / $8** per 1M in/out; GPT-6 Sol **$2 / $10**; GPT-6 Luna **$0.10 / $0.50**; GPT-5.4 **$2.50 / $15**.

**Definition of one ticket-workflow execution (scenario A, §6.1):** **6 nodes**, **3 LLM nodes** (classify, draft, post-HITL send). Non-LLM: retrieve, `interrupt` review, persist. Tokens **per LLM call:** 3,000 input + 800 output (growing history; conservative for support, light for RAG). HITL wait is **$0 model** and **$0 worker** on Agent Server/Temporal while parked.

Per execution token $ = `3 × (3000 × P_in + 800 × P_out) / 1e6`.

| Model | $ / execution **[inferred]** | $ / 1k **[inferred]** |
| --- | --- | --- |
| GPT-4.1 | $0.0372 | **$37.20** |
| GPT-6 Sol | $0.0420 | **$42.00** |
| GPT-6 Luna | $0.0021 | **$2.10** |
| GPT-5.4 | $0.0585 | **$58.50** |

**Definition of one Send research execution (scenario B, §6.2):** 1 planner LLM + **8** worker LLMs (`Send` fan-out, **one super-step**) + 1 synthesizer = **10** LLM calls, same 3k/800 skeleton.

| Model | $ / execution **[inferred]** | $ / 1k **[inferred]** |
| --- | --- | --- |
| GPT-4.1 | $0.124 | **$124** |
| GPT-6 Luna | $0.007 | **$7** |
| GPT-5.4 | $0.195 | **$195** |

Fan-out **multiplies dollars by N** and **does not** multiply super-step count (parallel workers = one tick). Latency can drop toward `max(worker_p95)` plus join. `recursion_limit` does **not** cap `Send` width — cap `len(subjects)` in the router.

LangGraph adds **0 extra LLM calls** vs a hand-rolled loop on the same skeleton. Checkpointer I/O is infra. `create_agent` middleware (summarization, extra judge) **does** add calls — budget from traces.

### 2.4 Platform SKUs (published) vs `$ / 1k` **[inferred]**

[langchain.com/pricing](https://www.langchain.com/pricing) (fetched 2026-09-23): Developer **$0**/seat, **5k** base traces/mo, 1 seat. Plus **$39**/seat/mo, **10k** traces, Deployment + Engine, **1 free Serverless Small**. LCU **$1.50**; LSU **$1.00**. Runtime **0.045 LCU / vCPU-hr**, **0.006 LCU / GiB-hr** → **$0.0675 / vCPU-hr** and **$0.009 / GiB-hr [inferred]**. Database **0.177 LSU / vCPU-hr**, **0.025 LSU / GiB-hr**. Existing customers stay on old per-run/uptime pricing until **2026-10-01**, then move to LCU/LSU; scale-to-zero is **new pricing only** ([Billing](https://docs.langchain.com/langsmith/billing)). Do **not** mix superseded **$0.001/node** blog numbers with LCU.

**Agent Server compute $ / 1k [inferred]:** assume ⚠️ **2 vCPU-seconds** of runtime per ticket execution (not published): \(1000 × 2/3600 × 0.045 × 1.50 ≈ \$0.038\). **Tokens dominate** at GPT-4.1 $37/1k.

**Dedicated Small always-on [inferred] from published sizes + rates:** runtime \(3×0.045×1.50 + 6×0.006×1.50 = \$0.2565/\mathrm{h}\); DB \(1×0.177 + 4×0.025 = \$0.277/\mathrm{h}\); total **≈ $0.534/h ≈ $390/mo** if 24×7. At **1k** runs/mo that is **~$390 / 1k platform**; at **100k** runs/mo **~$3.90 / 1k**. Serverless S has **no dedicated DB SKU** (shared) and can scale to zero (beta).

### 2.5 Documented throughput knobs

| Knob | Value | $ / latency effect |
| --- | --- | --- |
| `recursion_limit` | Default **1000** since 1.0.6 | Cap spend; `GraphRecursionError` |
| `RemainingSteps` bail | Docs example `<= 2` | Graceful END vs exception |
| `RetryPolicy` | `max_attempts=3`, `0.5s` × `2.0` backoff, cap **128s**, jitter on | Extra model/tool $ on 5xx |
| `N_JOBS_PER_WORKER` | Default **10** | Bounds concurrent **runs**, not HTTP |
| 1 run / thread | Enforced on Agent Server | Same-thread double-text must `enqueue`/`reject` |
| `Send` width | Data-dependent | $ × N; latency ~ max worker |
| `durability` | `exit` / `async` / `sync` | Write on critical path only for `sync` |
| Payload | Cloud **25 MB** | 413 |

---

## 3. Distributed Resilience & State

### 3.1 Durability modes

Least → most durable ([Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers); [`Durability`](https://reference.langchain.com/python/langgraph/types/Durability); Agent Server default **`async`**):

| Mode | When it writes | Crash semantics |
| --- | --- | --- |
| `"exit"` | Only on success, error, or HITL interrupt | Fast; **no** mid-graph recovery |
| `"async"` | Checkpoint **while** next step runs (default) | Small window of lost last tick |
| `"sync"` | Checkpoint **before** next step | Highest durability; extra latency |

`checkpoint_during` is deprecated in favor of `durability` ([Run API](https://docs.langchain.com/langsmith/agent-server-api/thread-runs/create-run-wait-for-output)).

### 3.2 Exactly-once super-steps vs at-least-once nodes and tools

Mental model ([Fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance); time-travel docs):

- **Committed super-step + pending writes of successful tasks** = the unit you resume from **without** re-running those tasks.
- **Node body** = at-least-once. Retry, timeout (`NodeTimeoutError` is retryable), `interrupt()` resume, and time-travel **all restart the node from the top**. Tools that charged a card twice are **your** bug unless idempotent keys exist (see 03).
- Functional API: `@task` results restore; the `@entrypoint` **replays from line 1**. Changing `task` / `interrupt` **order** before the resume point mismatches cached values ([Functional API](https://docs.langchain.com/oss/python/langgraph/functional-api)).
- `RetryPolicy` defaults: **3** attempts including the first; skip `ValueError`/`TypeError`/`RuntimeError`/`OSError`/…; HTTP retries **5xx only**. Order: attempt → retry → `error_handler` (`>=1.2`) → bubble. Timeouts: **async nodes only**; sync + `timeout` **rejected at compile**. `TimeoutPolicy(run_timeout=..., idle_timeout=...)` — run is a hard wall clock; idle resets on stream/callback/`runtime.heartbeat()` (`refresh_on="auto"` or `"heartbeat"`). Timed-out writes are **cleared** before retry. `set_node_defaults` does **not** inherit into subgraphs ([Fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)).
- **Graceful shutdown (`>=1.2`):** pass `control=RunControl()` into `invoke`/`stream`; any thread may call `request_drain()`. The loop finishes the **current super-step**, writes a resumable checkpoint, then stops — the documented answer to SIGTERM without losing the tick ([Fault tolerance — Graceful shutdown](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)). Agent Server workers already drain at super-step boundaries.

**Functional API vs Graph API checkpoint grain.** Graph API: one checkpoint **per super-step** (time-travel to any node). Functional API (`@entrypoint` / `@task`): one checkpoint **per entrypoint invocation**; completed tasks restore by deterministic `task_id`. There is no “replay from task 2” without dropping to StateGraph ([forum #3991](https://forum.langchain.com/t/re-execute-restart-from-a-specific-task-in-a-functional-api/3991); [Functional API](https://docs.langchain.com/oss/python/langgraph/functional-api)).

### 3.3 Locking on `thread_id`

OSS in-process: two concurrent `invoke`s on the same `thread_id` race the checkpointer (last writer wins / corrupt pending writes). Agent Server: **lease** — **≤1 executing run per thread**. `reject` → **409**; `enqueue` queues FIFO; `interrupt` cancels current; `rollback` reverts the interrupted run’s checkpoint ([Agent Server](https://docs.langchain.com/langsmith/agent-server); [reject-concurrent](https://docs.langchain.com/langsmith/reject-concurrent)). PostgresSaver uses a process `threading.Lock` around cursors in the sync class — that is **not** a distributed thread lease.

At least **one queue worker** must listen or runs are orphaned. Containers are stateless; durability is Postgres. Graceful shutdown: finish the current **super-step** (see fault-tolerance “Graceful shutdown”).

### 3.4 Replay vs resume vs Temporal

| Event | Re-executes |
| --- | --- |
| `Command(resume=)` after `interrupt()` | **Entire node** (tasks restored in Functional API) |
| Agent Server worker crash, `durability=async/sync` | From last checkpoint; successful sibling tasks skipped via pending writes |
| Time-travel replay | All nodes **after** the chosen checkpoint, including interrupts |
| Temporal Activity retry | Whole node/Activity |

Temporal: HITL is a **signal**; you do not hold a FastAPI worker for days. OSS `graph.invoke` **does** hold the process unless you persist and return ([Temporal](https://docs.temporal.io/develop/python/integrations/langgraph); [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)).

### 3.5 Version skew

Supported: add/remove state keys; change topology on **finished** threads. **Interrupted** threads: do not **rename/remove** the node they are about to enter. Renamed keys **lose** saved values ([Graph API — migrations](https://docs.langchain.com/oss/python/langgraph/graph-api)). Agent Server **revisions** vs in-flight threads: drain or reject old schemas. `DeltaChannel` checkpoints are unreadable on `<1.2`.

---

## 4. Enterprise Security & Governance

### 4.1 HITL as policy

Treat mutating tools (refund, send-email, `execute_sql`) as **policy gates**, not UX niceties. Patterns: `interrupt()` before `ToolNode`; `HumanInTheLoopMiddleware`; static `interrupt_before=["tools"]`. Resume must be **authenticated** (same user / role) — OSS will happily `Command(resume=)` if the caller has the `thread_id`. On Platform, wrap with custom auth ([custom-auth](https://docs.langchain.com/langsmith/custom-auth)). Time-travel **re-fires** interrupts — an auditor replay is a **new** approval, not a cached yes ([Time-travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel)).

### 4.2 Secrets and PII in state / checkpoints

**Do not store secrets in graph state.** Official custom-auth guide: fetch tokens from a secret store; populate `config["configurable"]["langgraph_auth_user"]` in `@auth.authenticate`; “Storing secrets in graph state is not recommended” ([custom-auth](https://docs.langchain.com/langsmith/custom-auth)). Prefer `context=` / `Runtime.context` (not checkpointed as state) ([Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)).

Defense in depth: `EncryptedSerializer` + `LANGGRAPH_AES_KEY` (auto on LangSmith when the env var is set). Encryption **does not change what is persisted**. Platform JSON handlers **skip** encrypting identifiers (`thread_id`, `checkpoint_id`, …) and most `langgraph_*` fields; `langgraph_auth_user` is **not** in the never-encrypt system-field list but **is** in some `SKIP_FIELDS` examples — design as if auth metadata may persist in plaintext metadata ([encryption](https://docs.langchain.com/langsmith/encryption); [Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).

PII in `messages` **is** the checkpoint. Controls: `RemoveMessage`, Store with tighter TTL, LangChain `PIIMiddleware` on `create_agent`, LangSmith LLM Gateway redaction (Plus+), region (US/EU). LangSmith **does not train** on traces (pricing ToS statement — verify current ToS). Traces without **identity** (`langgraph_auth_user` / IAM) are useless for SOX.

### 4.3 RBAC on graphs / tools

OSS: DIY. LangSmith Plus: org User/Admin. Enterprise: custom RBAC/ABAC, custom SSO ([pricing](https://www.langchain.com/pricing)). Agent Server `@auth.authenticate` + `@auth.authorize` on assistants/threads/runs. Tool RBAC: allowlist per assistant config; do not bind a shared PAT into the graph. `ToolNode` is the wrapper: check `runtime.context` / `langgraph_auth_user` **before** HTTP.

### 4.4 Zero-Trust MCP via `ToolNode` wrappers

Agent Server **serves** graphs at `/mcp` (Streamable HTTP, `langgraph-api>=0.2.3`). **Each `/mcp` request is stateless** — conversational memory must live in the checkpointer/store, not the MCP session. Same auth as the rest of the API ([server-mcp](https://docs.langchain.com/langsmith/server-mcp)). Consume MCP with `langchain-mcp-adapters.MultiServerMCPClient`; pass **per-user** `Authorization` from `langgraph_auth_user`, not a compile-time secret. Stdio MCP is for laptops; docs warn against stdio in a web server ([support article](https://support.langchain.com/articles/8743137454-how-do-i-deploy-an-mcp-server-on-langsmith-deployments-with-my-agent)). Pattern: no unauthenticated Streamable HTTP; per-principal tokens; `tool_filter`; HITL on mutating tools; hosted MCP egress is **outside** your VPC.

Hybrid: data plane stays in-VPC; control plane poll is HTTPS + API key ([hybrid-legacy](https://docs.langchain.com/langsmith/hybrid-legacy)). Cloud NAT IPs are published for allowlists (post **2025-01-06** deployments) ([Cloud features](https://docs.langchain.com/langsmith/cloud-platform-features)).

---

## 5. Production Failure Modes

### 5.1 `GraphRecursionError`

**Symptom:** `GraphRecursionError` after `recursion_limit` super-steps (default **1000** since 1.0.6). Linear 30-node chain = 30 ticks; a 2-node cycle can burn 1000 quickly.

**Causes:** conditional edge never returns `END`; ReAct tool loop; `Send` writing into a cycle; putting `recursion_limit` **inside** `configurable` (ignored).

**Mitigations:** terminate edges; `RemainingSteps`; `ModelCallLimitMiddleware`; do not “set 10,000”; idempotent tools because replay duplicates side effects. `create_react_agent` / some ReAct graphs return **“Sorry, need more steps…”** instead of raising when tools are pending ([#5548](https://github.com/langchain-ai/langgraph/issues/5548)).

### 5.2 Reducer bugs (lost messages)

**Symptom:** transcript shrinks to the last node’s return; or HITL edits **append** a duplicate instead of replacing.

**Cause:** missing reducer (`LastValue` overwrite); `operator.add` instead of `add_messages`; returning a full list from one parallel worker without `operator.add` on the fan-in key (last-write-wins among `Send`s).

**Fix:** `Annotated[..., add_messages]` or `operator.add`; never return the whole list unless you intend overwrite; test a 2-way `Send`.

### 5.3 Stale checkpoint / version skew

**Symptom:** resume lands on a renamed node; `DeltaChannel` thread unreadable after downgrade; factory graph compiled with a new tool set but old `thread_id`.

**Fix:** new `thread_id` after incompatible schema change; pin behavioral version in state ([Graph API — migrations](https://docs.langchain.com/oss/python/langgraph/graph-api)); don’t change delta channel types in place.

### 5.4 Interrupt lost

**Symptom:** HITL never resumes; or resume re-runs a refund.

**Causes:** `InMemorySaver` + process restart; missing `thread_id`; `durability="exit"` crash **mid-node before** interrupt commit; resume with a **new** thread id; OSS `invoke` dropped when the HTTP worker died (no Agent Server/Temporal); Functional API interrupt **order** changed so index-based resume attaches to the wrong `interrupt()`.

**Fix:** Postgres/Agent Server; stable `thread_id`; `durability="sync"` for payment graphs; idempotent tools; don’t reorder interrupts.

### 5.5 Large state bloat

**Symptom:** slow `get_state_history`, fat Postgres, 413 on Cloud (**25 MB**), quadratic model $ from resubmitting `messages`.

**Fix:** `RemoveMessage` / summarization; `DeltaChannel` + `snapshot_frequency`; Store for blobs; prune checkpoints; don’t put PDFs in state.

### 5.6 “Subgraph limit 25” — what that number actually is

There is **no official subgraph nesting limit of 25**. Two real “25”s bite interviews:

1. **Historical `recursion_limit` default 25** (LangChain `RunnableConfig` / pre-1.0.6 LangGraph). Nested subgraphs **consume parent super-steps**. A parent `A → sub(D→E→F) → C` is **multiple** ticks, not one. People hitting 25 on “deep” graphs were hitting the **step fuse**, not a nesting cap ([forum #559](https://forum.langchain.com/t/how-do-subgraphs-and-recursion-limit-interact/559); [1.0.6](https://github.com/langchain-ai/langgraph/compare/1.0.4...1.0.6)). Current default **1000**.
2. **Cloud payload 25 MB** ([Cloud features](https://docs.langchain.com/langsmith/cloud-platform-features)).

Visualization: Mermaid/`get_graph(xray=...)` historically struggled past **~3** nested levels ([issue #2607](https://github.com/langchain-ai/langgraph/issues/2607)) — a **render** bug, not an executor cap. Real subgraph hazards: `checkpointer=True` **plus parallel** calls to the same subagent; call-order namespaces when invoking subgraphs inside a node (reorder → wrong state); parent not seeing child writes (use Store).

### 5.7 `Command` + static edge double fire

`Command(goto=X)` **and** `add_edge(node, Y)` run **both** X and Y ([#5829](https://github.com/langchain-ai/langgraph/issues/5829)). Symptom: duplicate tools / skipped `END`. Use **either** `Command` **or** static/conditional edges per node.

### 5.8 Shared modes

Non-idempotent tools + retry/resume; unbounded context (see 02); eval traces with PII; subgraph `checkpoint_ns` hiding parent updates.

---

## 6. Enterprise System Design Scenarios

### 6.1 Ticket workflow with HITL (days-long wait, audit)

**Need:** classify → retrieve → draft reply → **adjuster approval** → send. Pause **days**, no GPU/worker burn, replayable, SSO.

**Graph (6 nodes):** `START → classify → retrieve → draft → review (interrupt) → send → END`. Three LLM nodes. Checkpointer **Postgres** (or Agent Server). `durability="sync"` on the send path. `thread_id` = ticket UUID (**≤255 chars**). Store: customer prefs under `(org, user, "prefs")`. Auth: `@auth.authenticate`; tools read `langgraph_auth_user`; **no** API keys in state. Resume: `Command(resume={"decision": "approve"|"edit"|"reject", "edits": ...})` with the **same** thread. Platform: Dedicated if customer-facing p99 matters; Temporal if HITL is weeks and you don’t want to pay always-on workers. Budget **[inferred]:** GPT-4.1 **~$37 / 1k** tickets + **~$0.04 / 1k** serverless compute at 2 vCPU-s; Dedicated S ~**$390/mo** floor.

**Anti-pattern:** `InMemorySaver` behind FastAPI; interrupt after a non-idempotent `send_email` in the **same** node as the LLM draft (resume resends).

### 6.2 Fan-out research graph with `Send`

**Need:** planner emits \(N\) research questions; workers retrieve+summarize **in parallel**; reducer ranks.

```
START → plan → [Send("research", {q}) × N] → join → synthesize → END
```

`subjects: list[str]`; `findings: Annotated[list[str], operator.add]`. Router: `[Send("research", {"q": s, ...}) for s in state["subjects"]]`. Cap \(N\) (e.g. 8). Optional per-`Send` `timeout`. Join node reads the reduced list — **one super-step** for all workers. Budget **[inferred]:** GPT-4.1 **~$124 / 1k** at \(N=8\) + 2 LLM bookends. Latency ≈ planner + max(worker) + synthesizer, not \(N×\) serial.

**Anti-pattern:** no reducer on `findings` (7 of 8 results dropped); unbounded `Send` from an LLM-invented list; `recursion_limit=25` leftover in `configurable`.

### 6.3 Decision rule (one paragraph)

Use **raw `StateGraph`** when the product *is* a cyclic, checkpointed, fan-out, HITL state machine. Use **`create_agent`** as a node inside that graph for the inner ReAct loop (04), with middleware for HITL/PII/call limits. Use **LangSmith Deployment** when you want thread leases, `/mcp`, and durable waits without writing a queue. Use **Temporal** when HITL is long enough that even a dedicated worker pool is the wrong wait fabric. Do **not** replace this module with CrewAI roles or Agents SDK handoffs — those are different control planes (§1.13).

---

## Interview bullets (numbers)

1. **`recursion_limit` default is 1000 super-steps since langgraph 1.0.6** (PR `#6676`); the **25** you remember is the old LangChain runnable default — it is **not** a subgraph nesting cap.
2. **`RetryPolicy(max_attempts=3, initial_interval=0.5, backoff_factor=2.0, max_interval=128, jitter=True)`**; `NodeTimeoutError` is retryable; HTTP retries **5xx only**; timeouts are **async-only**.
3. **Agent Server: ≤1 run per `thread_id`; `N_JOBS_PER_WORKER` default 10; `reject` → HTTP 409.** Redis holds **no** user data.
4. **Postgres `thread_id`: docs say <255 chars; btree index max ~2704 bytes** (`checkpoint_blobs_pkey`) — use UUIDs.
5. **Plus = $39/seat + 10k traces + 1 free Serverless Small; LCU $1.50, LSU $1.00; runtime 0.045 LCU/vCPU-hr → $0.0675/vCPU-hr [inferred].** Old per-run prices die **2026-10-01**.
6. **Durability `"exit" | "async" | "sync"`; Agent Server default `async`.** `sync` puts the checkpoint on the critical path; `exit` cannot recover mid-graph.
7. **Seven stream modes:** `values`, `updates`, `messages`, `custom`, `checkpoints`, `tasks`, `debug` (+ v2 `{type,ns,data}` and v3 event projections).
8. **Ticket skeleton [inferred]:** 6 nodes / 3 LLM calls × (3k in + 800 out) → GPT-4.1 **$37.20 / 1k**; Luna **$2.10 / 1k**; platform compute **~$0.04 / 1k** at 2 vCPU-s. Dedicated S always-on **~$390/mo [inferred]**.
9. **Send research [inferred]:** 1+8+1 = 10 LLM calls → GPT-4.1 **$124 / 1k**; workers share **one** super-step.
10. **`checkpoint_ns`:** `""` root; `"node:uuid"` subgraph; nested `"a:uuid|b:uuid"`. Time-travel **inside** a subgraph needs `checkpointer=True` on that subgraph.
11. **LangGraph 1.0 GA 2025-10-22; MIT; Python ≥3.10; `create_react_agent` / `MessageGraph` deprecated** (remove in 2.0). `create_agent` still **is** a compiled StateGraph.
12. **Cloud payload 25 MB → 413.** HITL: node **restarts from the top** on `Command(resume=)`; graph waits **indefinitely**. OpenAI Agents SDK fuse is **`max_turns` default 10**, not 1000 super-steps.

---

## Gaps (do not fabricate)

- No vendor p50/p95/p99 for checkpoint `put` vs model RTT; treat ms-vs-seconds as **engineering judgment**, not a citation.
- No published per-execution LangSmith SKU; 2 vCPU-s / run is an **assumption**.
- No documented maximum subgraph **nesting depth**; “25” is a fuse / payload number, not a depth cap.
- Scale-to-zero **inactivity window** is explicitly unstable (beta).
- `HumanInTheLoopMiddleware` **ainvoke** `get_config` bug ([langchain#34974](https://github.com/langchain-ai/langchain/issues/34974)) — confirm the fix version before relying on async HITL middleware.
- Docs **255-char** `thread_id` vs Postgres **2704-byte** composite index — both real; UUID satisfies both.
- `DeltaChannel` is **beta** (`>=1.2`); associative-reducer mistakes are on you.
- Engine “5–30 LCU per run” appears on some pricing explainers — treat as vendor range, not this module’s ticket math.

---

## Sources

1. https://docs.langchain.com/oss/python/langgraph/graph-api
2. https://docs.langchain.com/oss/python/langgraph/graph-api.md
3. https://docs.langchain.com/oss/python/langgraph/pregel
4. https://docs.langchain.com/oss/javascript/langgraph/pregel
5. https://docs.langchain.com/oss/python/langgraph/use-graph-api
6. https://docs.langchain.com/oss/python/langgraph/checkpointers
7. https://docs.langchain.com/oss/javascript/langgraph/checkpointers
8. https://docs.langchain.com/oss/python/langgraph/persistence
9. https://docs.langchain.com/oss/python/langgraph/durable-execution
10. https://docs.langchain.com/oss/python/langgraph/stores
11. https://docs.langchain.com/oss/python/langgraph/add-memory
12. https://docs.langchain.com/oss/python/langgraph/interrupts
13. https://docs.langchain.com/oss/python/langgraph/streaming
14. https://docs.langchain.com/oss/python/langgraph/streaming.md
15. https://docs.langchain.com/oss/python/langchain/streaming
16. https://docs.langchain.com/oss/python/langgraph/use-subgraphs
17. https://docs.langchain.com/oss/python/langgraph/use-time-travel
18. https://docs.langchain.com/oss/python/langgraph/fault-tolerance
19. https://docs.langchain.com/oss/python/langgraph/functional-api
20. https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT
21. https://docs.langchain.com/oss/python/migrate/langgraph-v1
22. https://docs.langchain.com/oss/python/releases/langgraph-v1
23. https://docs.langchain.com/oss/python/langchain/multi-agent/custom-workflow
24. https://docs.langchain.com/oss/python/langchain/human-in-the-loop
25. https://docs.langchain.com/oss/python/langchain/middleware/built-in
26. https://docs.langchain.com/oss/javascript/langgraph/thinking-in-langgraph
27. https://reference.langchain.com/python/langgraph/types/Send
28. https://reference.langchain.com/python/langgraph/types/Command
29. https://reference.langchain.com/python/langgraph/types/interrupt
30. https://reference.langchain.com/python/langgraph/types/StreamMode
31. https://reference.langchain.com/python/langgraph/types/Durability
32. https://reference.langchain.com/python/langgraph/checkpoints
33. https://reference.langchain.com/python/langgraph/graph/state/StateGraph
34. https://reference.langchain.com/python/langgraph/graph/message/add_messages
35. https://reference.langchain.com/python/langgraph/graph/message/REMOVE_ALL_MESSAGES
36. https://reference.langchain.com/python/langgraph/errors/GraphRecursionError
37. https://reference.langchain.com/python/langgraph/managed/is_last_step/RemainingSteps
38. https://reference.langchain.com/python/langgraph/pregel/main/Pregel/stream
39. https://reference.langchain.com/python/langgraph/pregel/main/Pregel/invoke
40. https://reference.langchain.com/python/langgraph.prebuilt/prebuilt/tool_node/ToolNode
41. https://reference.langchain.com/python/langgraph.prebuilt/tool_node/tools_condition
42. https://reference.langchain.com/python/langgraph.prebuilt/tool_node
43. https://reference.langchain.com/python/langchain/agents/create_agent
44. https://reference.langchain.com/python/langgraph.checkpoint/serde/encrypted/EncryptedSerializer
45. https://reference.langchain.com/python/langgraph.store/memory/InMemoryStore
46. https://reference.langchain.com/python/langgraph/graph/state/StateGraph/add_node
47. https://reference.langchain.com/python/langgraph/graph/state/StateGraph/set_node_defaults
48. https://docs.langchain.com/langsmith/deployment
49. https://docs.langchain.com/langsmith/deployment.md
50. https://docs.langchain.com/langsmith/platform-setup
51. https://docs.langchain.com/langsmith/deploy-to-self-hosted-overview
52. https://docs.langchain.com/langsmith/agent-server
53. https://docs.langchain.com/langsmith/control-plane
54. https://docs.langchain.com/langsmith/hybrid-legacy
55. https://docs.langchain.com/langsmith/deploy-self-hosted-full-platform
56. https://docs.langchain.com/langsmith/cloud-platform-features
57. https://docs.langchain.com/langsmith/billing
58. https://docs.langchain.com/langsmith/custom-auth
59. https://docs.langchain.com/langsmith/encryption
60. https://docs.langchain.com/langsmith/server-mcp
61. https://docs.langchain.com/langsmith/reject-concurrent
62. https://docs.langchain.com/langsmith/enqueue-concurrent
63. https://docs.langchain.com/langsmith/agent-server-api/thread-runs/create-run-wait-for-output
64. https://docs.langchain.com/langsmith/agent-server-api/mcp/mcp-post.md
65. https://www.langchain.com/pricing
66. https://www.langchain.com/blog/langchain-langgraph-1dot0
67. https://www.langchain.com/blog/langgraph-platform-ga
68. https://github.com/langchain-ai/langgraph
69. https://github.com/langchain-ai/langgraph/releases/tag/1.0.0
70. https://github.com/langchain-ai/langgraph/compare/1.0.4...1.0.6
71. https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres
72. https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/__init__.py
73. https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/langgraph/graph/message.py
74. https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/README.md
75. https://github.com/langchain-ai/langgraph/commit/0bd7dd2c52c80c431ce72ab647daa548e019cba0
76. https://github.com/langchain-ai/langgraph/issues/5548
77. https://github.com/langchain-ai/langgraph/issues/5829
78. https://github.com/langchain-ai/langgraph/issues/6239
79. https://github.com/langchain-ai/langgraph/issues/2607
80. https://github.com/langchain-ai/langgraph/pull/5432
81. https://github.com/langchain-ai/langgraph/pull/7498
82. https://github.com/langchain-ai/langchain/issues/34974
83. https://pypi.org/project/langgraph/
84. https://forum.langchain.com/t/how-do-subgraphs-and-recursion-limit-interact/559
85. https://forum.langchain.com/t/how-to-send-sensitive-data-like-auth-tokens-across-nodes-without-getting-stored-in-the-checkpoint/2416
86. https://forum.langchain.com/t/re-execute-restart-from-a-specific-task-in-a-functional-api/3991
87. https://docs.temporal.io/develop/python/integrations/langgraph
88. https://python.temporal.io/temporalio.contrib.langgraph.LangGraphPlugin.html
89. https://doi.org/10.1145/1807167.1807184
90. https://research.google/pubs/pregel-a-system-for-large-scale-graph-processing/
91. https://openai.github.io/openai-agents-python/running_agents/
92. https://docs.crewai.com/edge/en/learn/hierarchical-process
93. https://support.langchain.com/articles/8743137454-how-do-i-deploy-an-mcp-server-on-langsmith-deployments-with-my-agent
94. https://langchain-ai-langgraph-40.mintlify.app/api/checkpointing/postgres
95. https://reference.langchain.com/python/langgraph-sdk/schema/Command
96. https://docs.langchain.com/langsmith/diagnostics-self-hosted
97. https://github.com/langchain-ai/langgraph/blob/a6b8098548ec2c28ca58307782845e91465328e3/libs/langgraph/langgraph/graph/state.py
98. https://reference.langchain.com/python/langgraph/func/entrypoint

---

*End of research. No unpublished latency SLOs. Token and platform `$ / 1k` tables are **[inferred]** from the stated 6-node / 3-LLM and 10-LLM Send skeletons and list prices dated 2026-09-23. ReAct paper math is in 04, not here.*
