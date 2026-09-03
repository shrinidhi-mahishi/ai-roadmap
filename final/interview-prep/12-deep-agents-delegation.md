# Deep Agents: Delegation, Task Planning & Subagents

**Consolidated Study Module for Director/VP AI Interviews**
**Date**: 2026-09-02 | **Package pin**: `deepagents==0.7.12`

---

## What Is This?

Imagine a senior executive who never does research herself. Instead, she gives clear
assignments to specialists, waits for their one-page summaries, and makes decisions based
on those summaries. She never sees the 50 emails each specialist sent, the 20 documents
they read, or the dead ends they explored. Her desk stays clean. Her decisions stay sharp.

That is exactly how LangChain Deep Agents handles delegation. The main agent (the executive)
delegates via a `task` tool to subagents (the specialists). Each subagent gets a fresh,
clean workspace (context window). It does its work -- sometimes dozens of tool calls -- and
returns a single summary to the parent. The parent never sees the mess. This is called
**context quarantine**, and it is the primary mechanism for keeping long-running agents
from degrading.

**Subagents quarantine context.** A sync `task(description, subagent_type)` starts a **fresh child instance**. The child's `messages` is **not** the parent transcript. The child runs until it stops calling tools. The parent gets **one** `ToolMessage` (JSON if `structured_response` / `response_format` is set; else last non-empty `AIMessage` text). Intermediate tool dumps stay in the child window. The `task` description tells the parent: the report is **not** shown to the user; relay a summary yourself.

**Default GP is on.** Bare `create_deep_agent(model=...)` auto-adds a sync spec named `general-purpose` unless you already registered that name, and therefore attaches `SubAgentMiddleware` + `task`. Disable only via `HarnessProfile.general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)` **and** no synchronous `subagents=` -- not via `excluded_middleware={"SubAgentMiddleware"}` (`ValueError`).

**Todos are opt-in since v0.7.** `create_deep_agent` no longer ships `TodoListMiddleware`. Combined with the empty base prompt + 43% shorter tool descriptions, **base input tokens on a default-agent turn dropped 65% (~6k to ~2k)**. LangChain's evals: **no** statistically significant accuracy gain; token usage **up** on two of three models.

**`recursion_limit` is not a hop cap.** `create_deep_agent` binds **9,999** super-steps. That is a LangGraph fuse (and a 10,000-sentinel dodge), **not** `max_task_calls`. Product caps belong in `ToolCallLimitMiddleware(tool_name="task")`, `ModelCallLimitMiddleware` on the child spec, or application state.

Think sealed rooms, not extra CPUs. Isolation trades parent-context tokens for a **second full prefix**. Parallelism is **model-issued parallel tool calls** in one parent message, not a harness thread pool.

## Why It Matters

Almost every "multi-agent platform" interview now forks here: is Deep Agents a new orchestrator, or nested graphs with a single handoff? Trap answers: "`excluded_middleware` drops `task`," "empty `subagents=` disables GP," "9,999 is max hops," "todos make capable models more accurate," "compiled/async children inherit parent HITL," "`interrupt_on={"task": True}` catches interpreter `task()`."

Anthropic's production Research system is the same topology with published economics: subagents are **compression**. Multi-agent beat single-agent Opus 4 by **90.2%** on their internal research eval; token usage explained **80%** of BrowseComp variance; agents are approximately **4x** chat tokens, multi-agent approximately **15x** chat; a lead spawning **3-5** subagents (each **3+** tools in parallel) cut research time by up to **90%**. They also warn: coding has fewer truly parallelizable tasks; agents that must share one context are a poor fit.

Multi-agent systems are the default architecture for production AI in 2026, but most teams add agent hierarchy too early and too deep. Understanding delegation patterns, their failure modes, and when NOT to use them separates architects who ship from architects who prototype.

---

## Architecture / System Design

### System Topology

```
                         TELEMETRY / OBSERVABILITY SINKS
         +------------------------------------------------------------------+
         |  LangSmith: metadata.lc_agent_name=<spec name>                   |
         |             tracing-context ls_agent_type="subagent"             |
         |  Async: supervisor launch/check/update/cancel/list; child run    |
         |         linked by thread ID                                      |
         |  stream.subagents (product UI) != stream.subgraphs (Pregel)      |
         |  WORM you build: (cid, thread_id, spec, desc_digest, spawn_ts)  |
         +----------^---------------------^------------------^-------------+
                    | child spans         | stream handles    | spawn audit
+-------------------+---------------------+-------------------+-------------+
| CONTROL PLANE  (assembly -- LLM-free; disable-GP / HITL / caps live here) |
|                                                                           |
|  create_deep_agent(subagents=...)                                         |
|  HarnessProfile.general_purpose_subagent  (enabled / rename / re-prompt)  |
|  TodoListMiddleware() opt-in since v0.7                                   |
|  CodeInterpreterMiddleware(subagents=...)   (dynamic on if both present)  |
|  interrupt_on / permissions on specs      recursion_limit bind (9,999)    |
|  ToolCallLimitMiddleware(tool_name="task")  ModelCallLimitMiddleware      |
|  langgraph dev --n-jobs-per-worker  (async slot pool)                     |
|  SubAgentMiddleware attached IFF >=1 sync spec (incl. auto-GP)            |
|  AsyncSubAgentMiddleware attached IFF async specs present                 |
|  excluded_middleware={"SubAgentMiddleware"} -> ValueError (not a toggle)  |
+----------------------------------+----------------------------------------+
                                   | CompiledStateGraph
                                   v
+-----------------------------------------------------------------------+
| DATA PLANE  (untrusted -- parent proposes task; child loop disposes)   |
|                                                                       |
|  Parent: task(description, subagent_type) | start_async_task | eval JS|
|  Child:  HumanMessage(description) only; tools until stop             |
|  Handoff: ONE ToolMessage (JSON or last AI text). Not user-visible    |
|                                                                       |
|  +--- TOOL PROXIES (delegation surface) ----------------------------+ |
|  | Sync:  task  (SubAgentMiddleware)                                 | |
|  | Async: start_async_task / check / update / cancel / list          | |
|  | Dyn:   JS task({description, subagentType, responseSchema?})      | |
|  | Plan:  write_todos (only if TodoListMiddleware restored)          | |
|  | Child tools: inherited or replaced BaseTool list                  | |
|  +-------------------------------------------------------------------+|
+---+---------------+------------------+------------------+-------------+
    |               |                  |                  |
    v               v                  v                  v
+-----------------------------------------------------------------------+
| PERSISTENCE LAYER  (parent thread != child invoke != async thread)     |
|  +-------------+ +-------------+ +-------------+ +------------------+ |
|  | Parent       | | Sync child  | | Async child | | Interpreter      | |
|  | checkpointer | | compile():  | | own AP      | | mode=thread      | |
|  | messages,    | |  None=inherit| | thread +   | | (default): JS    | |
|  | interrupts,  | |  True/thread| | run;        | | vars persist     | |
|  | async_tasks  | |  False=none | | durability  | | across turns     | |
|  | channel      | |             | | on server   | | turn/call reset  | |
|  +-------------+ +-------------+ +-------------+ +------------------+ |
|  StateBackend files SHARED with sync children (backend, not messages)  |
|  todos / structured_response / PrivateStateAttr stripped both ways     |
+-----------------------------------------------------------------------+
```

### Request-Flow Narrative (Parent task -> Child Graph -> Single Handoff)

1. **Control/construction.** Application calls `create_deep_agent`. If GP is enabled or any sync spec is passed, `SubAgentMiddleware` registers `task`. Async specs register the five async tools on a separate middleware slot. Interpreter + subagents together expose JS `task()` unless `CodeInterpreterMiddleware(subagents=False)`.

2. **Parent turn.** The coordinator model emits one or more `task` tool calls. Official teaching: launch independent work **in a single message with multiple tool calls**. Unknown `subagent_type` returns a **string error** listing allowed types (does not raise). Missing `tool_call_id` raises `ValueError`.

3. **Sync child (default).** Nested graph under the tools node. Parent **blocks** until that child finishes. Child sees only the description plus inherited/replaced tools. `runtime.context` (`context_schema`) **does** propagate to all subagents and their tools.

4. **Handoff.** One `ToolMessage` on the parent. If `structured_response` is set, that object is JSON-serialized; else last non-empty `AIMessage` text. The extractor **walks backward** (Anthropic may emit a trailing empty `end_turn` `AIMessage`). Files on a shared **StateBackend** remain (backend sharing, not message sharing). Parent relays a user-visible summary.

5. **Parallel vs async vs dynamic.**
   - **Parallel sync:** model-issued parallel `task` calls in one super-step. LangGraph runs sibling tool nodes together. Parent wait is approximately **max**(children) + synthesis, not a thread pool.
   - **Async:** `start_async_task` returns a job ID immediately. Child is an Agent Protocol **thread**. Supervisor must **not** poll immediately. Status lives on the dedicated `async_tasks` channel (survives summarization).
   - **Dynamic:** model writes JS (`Promise.all`, `while`, branches). One `eval` **blocks that parent turn** until the promise settles. `task()` inside `eval` **does not** go through the normal tool-calling path -- parent `interrupt_on` is **not** enforced per dispatch.

6. **Stream/observe.** Root `stream.messages` = coordinator only. `stream.subagents` = one handle per delegated `task` (`.name`, `.path`, `.status`, `.messages`, `.tool_calls`, nested `.subagents`, `.output`). Collapse completed cards at **5+** children.

7. **Stop.** Child stops tools; or `GraphRecursionError` at the child's bound ceiling; or HITL `Command(resume=...)`; or async `cancel`; or parent `ToolCallLimitMiddleware` refuses further `task`.

### Data-Plane Isolation Contract for Sync `task`

Source (`subagents.py`) copies parent state **minus** `_EXCLUDED_STATE_KEYS = {messages, todos, structured_response}` and minus `PrivateStateAttr` keys, then overwrites `messages` with a single `HumanMessage(description)`. `todos` and `structured_response` have no defined reducer for parent<-child, so they are stripped on the way back. Middleware-private fields are excluded **both** directions.

### Official When/When-Not

| Use subagents | Do not |
| --- | --- |
| Multi-step work that would clutter the parent window (search, file reads, DB dumps) | Simple single-step tasks |
| Specialized domains (different instructions/tools/models) | When the parent **needs** intermediate evidence (quarantine would hide it) |
| Keep the parent on high-level coordination | When overhead outweighs the isolation win |
| Different subtasks benefit from different models (cost optimization) | Fewer than 3 tool calls needed |
| Need to parallelize work | Task requires tight sequential reasoning across all context |

---

## Core Concepts & Algorithms

### Invariants (Delegation, Not a Scheduler)

**I1.** Deep Agents introduces **no** new fan-out scheduler. Sync = nested graph from `task`. Async = Agent Protocol run on its own thread. Dynamic = `task()` bridged into QuickJS `eval`.

**I2.** Default GP is **on**. `task` exists whenever >=1 sync spec exists, including auto-GP. You cannot `excluded_middleware` that scaffolding (`ValueError`). `_REQUIRED_MIDDLEWARE` pair is Filesystem + SubAgent.

**I3.** Isolation is a **context** contract, not a compliance contract. A fat final report (`raw_dump: str`, "paste the search results") **undoes** quarantine.

**I4.** Sync children **cannot spawn grandchildren via `task`**. The sync child stack has no nested `SubAgentMiddleware`. Dynamic dispatch is the supported fan-out-from-code path.

**I5.** `recursion_limit` is a **super-step ceiling**, not `max_task_calls`. Putting it inside `configurable` is a silent no-op.

**I6.** Parallel sync `task` is documented **only** for ephemeral children (`checkpointer=None` inherit). Per-thread compiled subgraphs (`checkpointer=True`) **do not** support parallel tool calls -- sibling writes collide on the same `checkpoint_ns`.

**I7.** Identity for AuthZ is `runtime.context` / `rt.server_info.user.identity`, never `task(description=...)` JSON.

### Five Subagent Forms

| Form | What it is | How parent calls it | When to pick |
| --- | --- | --- | --- |
| **Default GP** | Auto-added sync spec `general-purpose` | `task(..., subagent_type="general-purpose")` | Context isolation **without** a specialist. Same tools/model as parent. Inherits parent **skills** |
| **Declarative SubAgent** | Dict: `name`, `description`, `system_prompt` **required**; optional `tools`, `model`, `middleware`, `interrupt_on`, `skills`, `response_format`, `permissions` | `task` with that `name` | Most production specialists |
| **CompiledSubAgent** | `{name, description, runnable}` -- prebuilt LangGraph graph. State **must** include `"messages"` | Same `task` surface | Custom graphs, DAGs, or agent with own HITL/structured-output wiring. Does **not** inherit parent `state_schema`, `interrupt_on`, or the Deep Agents subagent stack |
| **AsyncSubAgent** | `{name, description, graph_id, url?, headers?}` -- Agent Protocol worker. Preview `>=0.5.0` | `start_async_task` / `check` / `update` / `cancel` / `list` -- **not** `task` | Long-running, mid-flight steering, cancellation, independent scale |
| **Dynamic** | JS global `task({description, subagentType, responseSchema?})` whenever subagents **and** `CodeInterpreterMiddleware` are both present. **Beta** | Model writes loops / `Promise.all` / branches inside `eval` | Classify-and-act, batch fan-out, adversarial verify, tournament, loop-until-done |

**GP auto-add rule:** Deep Agents adds sync `general-purpose` unless you already pass a sync subagent with that name. Replace by passing `name="general-purpose"`. Disable via `enabled=False` **and** no other sync specs.

**Running without subagents (no `task` tool)** -- **two** conditions, both required:
1. `general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)` on the active harness profile
2. Pass **no** synchronous specs via `subagents=`

### Inheritance Matrix (Critical Interview Detail)

| Field | Declarative SubAgent | GP (auto) | CompiledSubAgent | AsyncSubAgent |
| --- | --- | --- | --- | --- |
| `system_prompt` | **Required**; does **not** inherit | SDK default GP-specific prompt | Owned by the runnable | Owned by the remote graph |
| `tools` | Inherits parent; a provided list **replaces entirely** (empty list = no inherited tools) | Same tools as parent (FS + caller tools, including MCP) | Owned by the runnable | Owned by the remote graph |
| `model` | Inherits; override per subagent | Same as parent unless replaced | Owned by the runnable | Owned by the remote graph |
| `middleware` | Does **not** inherit parent extras. No nested SubAgentMiddleware | Inherits default-middleware overrides (same `.name` replacements) | None from parent | N/A -- remote |
| `interrupt_on` | Inherits parent; spec overrides. Requires checkpointer | Inherits | **Does not inherit** | **Does not inherit** |
| `skills` | Does **not** inherit. Own SkillsMiddleware if `skills=` set | **Does** inherit parent skills | Owned by the runnable | Remote |
| `permissions` | Inherits; spec **replaces entirely** (no merge) | Inherits parent | Owned by the runnable | Remote |
| `response_format` | Optional; parent gets JSON ToolMessage (`>=0.5.3`) | Optional on replace-GP spec | If runnable writes `structured_response`, parent serializes | Remote output from thread state |
| Runtime context | **Forwarded** to every subagent tool automatically | Forwarded | Forwarded | Remote |

**Key design decision**: Permissions **replace**, they do not merge. If you specify permissions on a custom subagent, it gets exactly those permissions and nothing from the parent. This prevents privilege escalation through additive merging.

**Inheritance shorthand to memorize:**
- Inherits: `tools`, `model`, `interrupt_on`, `permissions`, runtime context
- Does not inherit: `system_prompt`, `middleware`, custom `skills`
- Special case: only `general-purpose` inherits parent skills

### Structured Output from Subagents (`>=0.5.3`)

Pass `response_format` on the declarative spec: Pydantic model, `ToolStrategy(...)`, `ProviderStrategy(...)`, `AutoStrategy(...)`, bare type, or JSON schema dict. Parent `ToolMessage.content` is JSON. Keep the schema **small**. A `raw_dump: str` field defeats quarantine.

Interpreter path: `task({..., responseSchema})` resolves to a **typed JS object** -- do not `JSON.parse` unless the child intentionally returned a JSON string. Dynamic response schema **cannot** target a compiled child -- `ValueError`.

### Async Subagents (Preview `>=0.5.0`)

| Dimension | Sync task | Async |
| --- | --- | --- |
| Execution | Parent blocks | Returns task ID; parent continues |
| Concurrency | Parallel **but blocking** (parent waits for the gather) | Parallel **and non-blocking** |
| Mid-task updates | Not possible | `update_async_task` (interrupt + new run on same thread) |
| Cancellation | Not possible (except killing the parent run) | `cancel_async_task` -> `runs.cancel()` |
| State | Stateless per invocation | **Stateful** on its own Agent Protocol thread |
| Best for | Parent must have result before next thought | Long-running work managed in chat |

Five tools (`AsyncSubAgentMiddleware`): `start_async_task`, `check_async_task`, `update_async_task`, `cancel_async_task`, `list_async_tasks`.

**Transport:** Omit `url` -> **ASGI** (in-process, zero extra auth). Set `url` -> **HTTP** to a remote Agent Protocol server. Local ASGI requires an **async** parent entrypoint (`ainvoke`); sync `invoke` needs a reachable HTTP URL.

**Worker pool:** `langgraph dev --n-jobs-per-worker 10`. Each active run occupies a slot. Supervisor + 3 concurrent children = **4** slots. Under-provisioning **queues** launches (symptoms look like hung `start_async_task`).

**`async_tasks` channel** is dedicated on the supervisor graph, **separate from messages**, because summarization would otherwise drop task IDs stored only in tool messages.

### Dynamic Subagents (Interpreter Dispatch, Beta)

On by default whenever the agent has **both** subagents and `CodeInterpreterMiddleware`. This is the Deep Agents encoding of **Recursive Language Models** (Zhang, Kraska, Khattab; arXiv:2512.24601, 2025-12-31): keep the working set in interpreter variables, select slices, call sub-LMs, synthesize in code. RLMs handle inputs **two orders of magnitude** beyond the model window.

Documented patterns: classify-and-act (`Promise.all` after a specialist map); fan-out-and-synthesize (directory review); adversarial verification (independent reviewers + judge); generate-and-filter; tournament; **loop-until-done** (`while (true)` until a round adds nothing) -- **poison-pill** if items are never stable.

Multi-turn interpreter state: `mode="thread"` (default) persists JS variables across agent turns. `mode="turn"` / `"call"` reset sooner. That persistence is **why** a poisoned set in loop-until-done can survive into the next user message.

**Critical**: `task()` inside `eval` **does not** go through the normal tool-calling path -- parent `interrupt_on` is **not** enforced per dispatch. Gate `eval`.

### Task Planning (TodoListMiddleware)

v0.7 breaking change: **no longer** default on main, GP, or declarative children. Combined with empty base prompt + 43% shorter tool descriptions, **base input tokens dropped 65% (~6k to ~2k)**.

**When to restore:** (1) long multi-step tasks that benefit from an explicit plan; (2) less capable models that drop steps; (3) UIs that stream progress from `stream.values.todos`.

Middleware adds `write_todos` tool + planning system-prompt section. State: `todos` array; each item `{content, status}` with `pending` | `in_progress` | `completed`. **At most one `write_todos` per model turn.** The tool **replaces the entire list**; `after_model` rejects parallel calls.

**Not inherited** by declarative subagents unless they opt in. GP mirrors the caller's instance **by identity** when present. Without the middleware, `stream.values.todos` is **absent** -- do not render an empty list.

**Progress formula from the frontend docs:**
```
percentage = round(completed / total * 100)
```

**Streaming surface to memorize:** `stream.values.todos`

### Event Streaming Architecture

Deep Agents extends LangGraph streaming with typed projections via `stream_events(version="v3")`.

| Projection | Source | Content | UI Use |
|-----------|--------|---------|--------|
| `stream.messages` | Coordinator | Main agent text output | Primary chat display |
| `stream.subagents` | SubAgentMiddleware | Subagent lifecycle + content | Status indicators, nested views |
| `stream.tool_calls` | ToolRuntime | Tool invocations and results | Debug panels, progress indicators |
| `subagent.messages` | Individual subagent | Subagent-scoped text | Expandable detail views |
| `subagent.subagents` | Nested delegation | Recursive subagent events | Deep hierarchy views |

**Content-block-centric streaming**: Events are typed (text, reasoning, media, tool-call data). UIs no longer guess the chunk type. Each event has a lifecycle: Start (create placeholder, show spinner) -> Data (stream content) -> End (finalize rendering).

**Why streaming is architectural, not a feature**: Without streaming, users see blank screens during 15-second agent computations. This is the single biggest driver of abandonment rates on AI products.

### Delegation Patterns (Production 2026)

**Pattern 1: Supervisor (default choice)**
Central orchestrator delegates to specialized workers. Widest framework support. Best-understood failure mode (over-delegation, bounded by iteration ceilings).

**Pattern 2: Fan-Out (parallel independent tasks)**
Parallel independent subtasks with results aggregated. Effective for 3-10 parallel tasks. Deep Agents supports via multiple subagent calls or dynamic subagents.

**Pattern 3: Pipeline (sequential stages)**
Each stage transforms output for the next. Linear workflows with clear boundaries.

**Pattern 4: Debate (adversarial validation)**
Multiple agents argue, a critic selects the best output. Reserved for quality-critical decisions. Higher cost but catches hallucination cascading.

**Pattern 5: Swarm (genuine scale)**
Dynamic peer agents with shared memory/message bus. Practical at 100 agents (Kimi K2.5), demonstrated at 300 (K2.6). Experimental; not recommended for most production systems.

**Industry consensus**: Start with supervisor. Add fan-out for parallelizable subtasks. Pipeline for linear workflows. Debate only when quality justifies 2-3x cost. Swarm almost never.

### Protocol Stack (2026 Consensus)

| Layer | Protocol | Purpose | Status |
|-------|----------|---------|--------|
| Agent-to-Tool | **MCP** | Tool access, server discovery | Most mature, broadest adoption |
| Agent-to-Agent | **A2A** | Cross-vendor agent coordination | 150+ org support; IBM ACP merged in |
| Agent-to-Frontend | **AG-UI** | Real-time event streaming to UIs | 40+ framework integrations |

A2A discovery uses **Agent Cards** -- JSON documents at well-known URLs describing identity, skills, API endpoint, supported modalities, authentication requirements, and streaming capabilities.

### ToolCallLimitMiddleware on `task` (The Product Cap)

No harness cap on number of `task` calls. Anthropic's early Research agents spawned **50** subagents for simple queries. Their prompt-level scaling rules:
- Simple fact -> **1** agent, **3-10** tool calls
- Comparisons -> **2-4** subagents, **10-15** calls each
- Complex -> **>10** with divided labor

`ToolCallLimitMiddleware` / `ModelCallLimitMiddleware` are LangChain prebuilts:

| Knob | Meaning |
| --- | --- |
| `tool_name="task"` | Limit only delegation, not `read_file` |
| `run_limit` | Max calls per user turn (resets each message) |
| `thread_limit` | Max across the conversation; requires checkpointer |
| `exit_behavior` | `"continue"` (default): exceeded calls get error string; `"error"`: raise; `"end"`: stop cleanly |

### `recursion_limit` 9,999 -- Why Not 10,000

LangGraph `merge_configs` historically **dropped** `recursion_limit` when it equaled `DEFAULT_RECURSION_LIMIT` (10000), so nested graphs fell back to **25**. Issue #7314 (filed 2026-03-27). Deep Agents still binds 9,999 as of 0.7.x.

**Historical production bug #1698** (`deepagents==0.4.4`): `SubAgentMiddleware` invoked children **without** `config`, so children ran at **25**. A legal-doc child hit **exactly 25** steps, `GraphRecursionError` -> `asyncio.Task.cancel()` -> `CancelledError` on the parent, and **`asyncio.gather` cancelled the sibling** mid-flight. Parent `recursion_limit=300` had zero effect.

**Control-plane checklist:**

| Goal | Do this | Do not |
| --- | --- | --- |
| No `task` tool | Profile `enabled=False` **and** no sync `subagents=` | `excluded_middleware={"SubAgentMiddleware"}` |
| Keep specialists, drop GP | `enabled=False` or replace `name="general-purpose"` | Assume empty `subagents=` drops GP |
| No dynamic fan-out | `CodeInterpreterMiddleware(subagents=False)` or omit interpreter | Assume `interrupt_on={"task": True}` catches JS `task()` |
| No async | Omit `AsyncSubAgent` specs | Strip `AsyncSubAgentMiddleware` |
| Planning UI | `middleware=[TodoListMiddleware()]` | Expect `stream.values.todos` on a v0.7 default agent |
| Structured child -> parent | `response_format=` on declarative spec `>=0.5.3` | `responseSchema` on interpreter targeting a `CompiledSubAgent` |

---

## Token Economics & Cost Analysis

### Context Quarantine ROI

| Scenario | Without Subagents | With Subagents | Savings |
|----------|-------------------|----------------|---------|
| 10 web searches | 10 full results in context (~50K tokens) | 1 summary (~2K tokens) | ~96% |
| File analysis (20 files) | All file contents loaded (~100K tokens) | Condensed findings (~3K tokens) | ~97% |
| Multi-step research | Cumulative tool outputs | Per-topic subagent, summary only | 10:1 to 50:1 |

**Key insight**: Total token usage is often **higher** with subagents (each gets a fresh system prompt). But parent context pressure is dramatically lower, sustaining many more turns before hitting compression. The ROI is in agent longevity, not per-turn cost.

### Cost Formulas

**Per-delegation cost:**
```
C_delegation = C_subagent_system_prompt_assembly
             + C_subagent_inference (all turns within subagent)
             + C_parent_result_ingestion (ToolMessage, typically 1-2K tokens)
```

**Complexity:** One parent turn that emits N parallel `task` calls runs N isolated ReAct loops. Token cost is approximately parent prefix + sum of child prefixes (child cache keys **differ**). Wall-clock for sync parallel is approximately max(children) + synthesis.

### GP On Cost: +0.8-1.0x

Default GP on means one extra isolated tool-calling loop that **re-pays the tool-schema prefix**. For a medium research run (Claude Sonnet 4.6, 10 parent calls, 2k v0.7 cached prefix, GP disabled) the inferred cost is **$0.223 / run -> $223 / 1k**. One extra isolated **8-call** GP child with the same 2k prefix is roughly **+0.8-1.0x** the main-agent bill. Disable GP for short bots.

### $ Cost Per 1k -- 0 vs 1 vs N Children [inferred]

Assumptions: Sonnet 4.6, parent 10 calls, 2k cached prefix (1 write + 9 reads), 3k uncached in / 800 out per call -> **$0.223 / run**. Each sync child: 8 calls, own 2k prefix (1 write + 7 reads), 3k uncached in / 800 out per call -> **$0.180 / child**.

| Topology | Children | USD / run | USD / 1k runs | vs 0-child |
| --- | --- | --- | --- | --- |
| GP disabled, no specialists | 0 | $0.223 | **$223** | 1.0x |
| Default GP, one 8-call hop | 1 | $0.403 | **$403** | 1.81x |
| Parent + 3 parallel specialists | 3 | $0.763 | **$763** | 3.42x |
| Parent + 5 parallel (Anthropic-like wave) | 5 | $1.123 | **$1,123** | 5.04x |
| Same 5 without prompt caching on children | 5 | $1.303 | **$1,303** | 5.84x |

### Model-Tier Optimization: Haiku Children

Route a **reviewer** child to Haiku 4.5 ($1 / $5 / MTok in/out) with the same 8-call shape:

| Component | Math | USD |
| --- | --- | --- |
| Cache write | 2,000 x $1.25 / 1e6 | $0.0025 |
| Cache reads | 7 x 2,000 x $0.10 / 1e6 | $0.0014 |
| Uncached input | 8 x 3,000 x $1 / 1e6 | $0.0240 |
| Output | 8 x 800 x $5 / 1e6 | $0.0320 |
| **Haiku child / run** | | **$0.060** |

Sonnet parent + 3 Haiku children: $0.223 + 3 x $0.060 = **$0.403 / run -> $403 / 1k**, versus **$763 / 1k** if those three children stay on Sonnet.

| Subagent Role | Recommended Model | Rationale |
|---------------|-------------------|-----------|
| Web search summarization | Haiku | 90% cheaper; summarization is low-complexity |
| Data analysis | Sonnet | Needs reasoning for correct calculations |
| Code generation | Sonnet/Opus | Needs strong coding capability |
| Report writing | Sonnet | Balance of quality and cost |

### Todo Opt-In Token Tax

Re-enabling `TodoListMiddleware` adds prefix + tool schema of approximately **400-800 tokens** on 10 Sonnet calls with cache. Prefix tax is cheap (**$3.90 / 1k** [inferred]); the 1-3 extra `write_todos` turns dominate (**$0.008 / run**). Enable todos for UI progress, not for "completeness."

### Latency Analysis

| Path | **p50** | **p95** | **p99** | Grounding |
| --- | --- | --- | --- | --- |
| **Sync task, 8-call child, parent waits** [inferred] | **16,000 ms** | **64,000 ms** | **160,000 ms** | 8 x ReAct-cycle. Sync task **is** the p99 path |
| **Sync 3 parallel equal 8-call children** [inferred] | **16,000 ms** | **64,000 ms** | **160,000 ms** | Wait = **max** of children + synthesis. Anthropic: 3-5 parallel cut research time **up to 90%** vs sequential |
| **Async ASGI launch+ack** [inferred] | **2,000 ms** | **8,000 ms** | **20,000 ms** | Child p99 is a **different clock** |
| **Async HTTP extra vs ASGI** [inferred] | **50 ms** | **200 ms** | **1,000 ms** | Same-region HTTP class |
| **Async check RTT, ASGI** [inferred] | **10 ms** | **50 ms** | **200 ms** | In-process status fetch |
| **Dynamic Promise.all of 3 children** [inferred] | **16,000 ms** | **64,000 ms** | **160,000 ms** | One eval blocks that parent turn |
| **Dynamic while(true) uncapped** | -- | -- | **no SLA** | Product: put a JS round cap |
| **HITL on a sync child** [inferred] | **30,000 ms** | **180,000 ms** | **600,000 ms** | Seconds-minutes. Dominates any model percentile |
| **GraphRecursionError / 9,999** | -- | -- | **hard error** | Fuse, not a degrade. Product cap must fire earlier |

**Latency SLA targets** (production guidance):

| Metric | Sync Delegation | Async Delegation | Streaming TTFB |
|--------|----------------|-----------------|----------------|
| p50 | <10s | <500ms (launch) | <200ms |
| p95 | <30s | <2s (launch) | <500ms |
| p99 | <60s | <5s (launch) | <1s |
| p50 result ready | N/A | <30s | N/A |
| p95 result ready | N/A | <120s | N/A |

### Availability, RPO/RTO

| NFR | Target | Rationale |
|-----|--------|-----------|
| Availability (sync delegation) | 99.9% | Blocking path; sync failure = parent failure for that turn |
| Availability (async delegation) | 99.5% | Depends on task queue durability and remote platform uptime |
| RPO (platform-backed async tasks) | 0 | Every async task is checkpointed |
| RPO (in-memory async) | 1 task (lost on crash) | MemorySaver: dev/test only |
| RTO (sync subagent restart) | <10s | Fresh context assembly + first LLM call |
| RTO (async task recovery) | <2 min | Reload task state from checkpoint |

| NFR | Production stance | Competes with |
| --- | --- | --- |
| **Availability of chat vs of children** | Product SLO is the parent loop. Sync `task` **couples** child availability to chat p99. Circuit-open on child model -> **GP off -> parent-only** | Research quality vs user p99 |
| **RPO of sync child** | `checkpointer=None` (default): per-invocation; crash mid-child: parent sees failed tool super-step; retry re-runs the child | Isolation vs resume fidelity |
| **RPO of async child** | Own Agent Protocol thread. Supervisor `async_tasks` survives parent summarization | Independent scale vs split-brain on rainbow deploys |
| **Compliance** | **Not provided by `deepagents`.** Quarantine does not equal redaction. GDPR erasure = parent checkpoint + child traces + async thread + shared VFS | Time-to-debug vs residency |
| **Correctness vs $** | Anthropic paid **15x** chat for **90.2%** research lift and still said don't multi-agent when work isn't parallelizable | Schema/prefix tax vs isolation |

---

## Trade-offs & Failure Modes

### Core Trade-off

**Subagents trade total tokens for bounded context pressure.** Total usage may increase, but parent agent sustains many more turns. The ROI is longevity, not per-turn cost.

### Failure Taxonomy

**Transient failures (automatic recovery):**

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Subagent execution failure | Stream status = `failed` | Parent receives error as ToolMessage; can retry |
| Network timeout (async) | `check_async_task` returns timeout | Retry with backoff; task state persisted |
| Provider 429/5xx on child | Error rate; p99 wait spike | Full-jitter retries on idempotent reads. Do **not** blindly retry `task` (each call is a new child bill) |

**Permanent failures (require architectural mitigation):**

| Failure | Description | Severity | Mitigation |
|---------|-------------|----------|------------|
| Over-delegation | Parent delegates too aggressively | Medium | Clear descriptions; "3+ tool calls" heuristic |
| Delegation loops | Infinite re-dispatch cycles | High | Subagents do not inherit `task` tool; explicit inclusion required |
| Supervisor saturation | Routing accuracy drops after 8-12 round trips | Medium | Summarization at 85%; subagent results as single ToolMessages |
| Hallucination cascading | One agent's hallucinated output treated as ground truth | Critical | Structured output (Pydantic), tool evidence verification, HITL |
| Premature hierarchy | Adding 3+ layers when 2 suffice | Medium | Two layers handle vast majority; third rarely justified |

### Common Failure Modes (Production Reference)

| Failure | Cause | Detection | Mitigation |
| --- | --- | --- | --- |
| Accidental GP spend | Bare `create_deep_agent` auto-adds GP + task | Second prefix in traces; hidden intermediates | `enabled=False` + no sync specs |
| `ValueError` stripping task | `excluded_middleware={"SubAgentMiddleware"}` | Construction exception | Disable GP; never strip scaffolding |
| Nested recursion 25 / sibling cancel | Historical #1698: child without config; gather cancels siblings | `GraphRecursionError` at 25; CancelledError | Pin 0.7.x; ModelCallLimitMiddleware on children |
| Todos missing after upgrade | v0.7 opt-in; UI assumed `stream.values.todos` | Empty panel | `middleware=[TodoListMiddleware()]` only if needed |
| Context / PII leak on handoff | Child returns raw dumps; `raw` schema; `fork` mode | Parent window bloat; PII in checkpoint | "Do NOT include raw data"; VFS for blobs; small response_format |
| Infinite task spawn | No harness N cap; dynamic Promise.all; async start loop | 50-child traces; queued launches | ToolCallLimitMiddleware on task; JS cap; disable GP |
| Wrong specialist / parent won't delegate | Vague description; overlapping specs | Parent does the work; duplicated searches | Action-oriented router + differentiated descriptions |
| Immediate async check loop | Model treats async as sync | Wasted turns | System prompt: after launch, return control |
| Per-thread compile + parallel task | checkpointer=True on compiled child | Checkpoint corruption | Ephemeral default; or disable parallel tool calling |
| Dynamic HITL bypass | eval task() skips interrupt_on; no max-iterations | Unbounded eval; unapproved MCP | Gate eval; JS round cap; mode="turn" |
| Child MCP loop amplifier | Inherited full parent MCP | Step count; token burn | Replace tools on the spec; ModelCallLimitMiddleware |

**Drew Breunig's Four Failure Modes applied to delegation:**

| Mode | Attack Vector | Mitigation |
|------|--------------|------------|
| Context Poisoning | One agent's hallucinated output treated as ground truth downstream | Structured output, tool evidence verification |
| Distraction | Over-delegation creates noise | Clear descriptions, 3+ tool calls heuristic |
| Confusion | Too many tools or conflicting subagent instructions | Minimal tool sets per specialist |
| Clash | Multi-agent results contradict each other | Adversarial verification pattern |

---

## Production Patterns & Best Practices

### Circuit Breaker for Delegation

`deepagents` does **not** ship circuit breakers, leader election, or a token-bucket around `task`. Build your own.

```
        child 429/5xx | task error-rate window | GraphRecursionError storm
  +----------+  -------------------------------------------------------->  +----------+
  |  CLOSED  |                                                               |   OPEN   |
  |  task()  |  success resets consecutive count                             | FAIL FAST|
  +----+-----+                                                               | fallback |
       ^                                                                     | chain    |
       | probe OK                                                            +----+-----+
       |                                                                          | cooldown
       |                                                                    +-----v------+
       +------------ probe allow -----------------------------------------------| HALF-OPEN|
                    probe fail -> stay OPEN                                  | 1 specialist|
                                                                            | probe      |
                                                                            +------------+
```

**Fallback chain (required interview answer):** **GP on (default isolation) -> GP off / specialists only -> parent-only (enabled=False + no sync specs).** Never: child 429 -> unbounded Promise.all. Never: HITL timeout -> auto-approve. Never: circuit open -> excluded_middleware SubAgent.

### Back-Pressure Design

| Ceiling | Number | Effect |
| --- | --- | --- |
| Compiled recursion_limit | **9,999** | Super-step fuse. Hitting it is GraphRecursionError |
| LangGraph default >=1.0.6 | **1000** | Today's runtime default |
| Nested-graph ghost / #1698 | **25** | Historical child footgun |
| Async worker example | `--n-jobs-per-worker 10` | Supervisor + 3 children = 4 occupied |
| write_todos | **<=1 per model turn** | Parallel calls rejected |
| Anthropic parallel wave | **3-5** subagents; **3+** tools each | Prompt-level; not encoded |
| Anthropic spawn footgun | **50** children on simple queries | Prompt-level |
| ToolCallLimitMiddleware on task | **you set** run_limit / thread_limit | The actual product cap |
| Provider TPM/RPM | account limits | **The** throughput ceiling. N children = N streams |
| UI collapse | **5+** subagent cards | Frontend, not a runtime cap |

**Design:** (1) ToolCallLimitMiddleware on task; (2) ModelCallLimitMiddleware on each child spec; (3) async worker slots >= 1 + concurrent children; (4) JS slice cap on dynamic fan-out; (5) circuit on provider 429; (6) disable GP + interpreter subagents=False for L1; (7) bulkhead parent model vs child fleet vs async workers.

### Zero-Trust MCP on Children

MCP tools are ordinary `BaseTool` objects on `tools=`. There is **no** MCP-specific subagent middleware. `permissions=` is first-match-wins, **no match -> allow** (fail-open), and applies only to **built-in FS tools** -- not MCP, not custom tools, not sandbox `execute`.

| Child form | MCP / tool surface | What Zero-Trust must do |
| --- | --- | --- |
| **Declarative, tools omitted** | **Inherits** parent BaseTool objects | Parent PEP is the child PEP. Minimize by replacing tools |
| **Declarative, tools set** | **Replace entirely** -- narrower RBAC | Re-list only needed MCP tools |
| **GP (auto)** | Same tools as parent (widest child) | Disable GP on L1 |
| **Compiled / async** | Do **not** inherit parent tools, HITL, or permissions | Configure on the runnable/remote graph |
| **Dynamic task()** | Uses the configured spec's tools | Promise.all N children = N times that spec's surface **without** per-dispatch parent HITL |

### PII Pipeline for Children

Quarantine is a **context** win, not a **compliance** win. Child traces contain full intermediate tool dumps. PII in a web page the child fetched is in LangSmith unless you redact.

1. **Detection (control plane).** Dual-gate: regex + ML NER. Scan: `task(description=...)` (user-influenced), child tool args/results, child final report / structured_response JSON, parent ToolMessage, todos titles.

2. **Redaction.** Redact/mask/hash via `PIIMiddleware` on the **child** list (not inherited) and on the parent handoff; block PAN into child MCP args / VFS.

3. **Audit trail (WORM).** Log decisions: pre/post hashes, entity types + counts, action, detector, cid, lc_agent_name, ls_agent_type=subagent, async thread id.

### RBAC for Delegation

| Role | Delegation Authority | Subagent Access |
|------|---------------------|-----------------|
| **Analyst** | Research subagents only | Read-only tools; no code execution |
| **Engineer** | Coding + research subagents | Read-write tools; sandboxed code execution |
| **Lead** | Any subagent type + approve async tasks | Full tool access; async task management |
| **Admin** | Configure subagent definitions + modify delegation policies | Full access; subagent CRUD |

### Durable Execution: Child vs Parent Checkpointer

| checkpointer= on child | Behavior | Parallel task? |
| --- | --- | --- |
| `None` (default) | Per-invocation. Inherits parent checkpointer so interrupts work. Fresh messages each task | Yes |
| `True` | Per-thread. State accumulates | **No** -- parallel tool calls conflict |
| `False` | No child checkpoints. Interrupts will not resume cleanly | N/A |

---

## Code Examples

### Production Multi-Agent System with Delegation

```python
"""
Production multi-agent system with hierarchical delegation, async subagents,
event streaming, and structured output validation.
Requires: pip install langchain-deepagents pydantic
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field
from deepagents import create_deep_agent
from deepagents.permissions import FilesystemPermission
from langgraph.checkpoint.memory import MemorySaver


class ResearchFinding(BaseModel):
    """Structured output from the research subagent."""
    title: str = Field(description="One-line summary of finding")
    evidence: list[str] = Field(description="Supporting evidence from tools")
    confidence: float = Field(ge=0.0, le=1.0)
    sources: list[str] = Field(description="URLs or document references")


class AnalysisReport(BaseModel):
    """Structured output from the analysis subagent."""
    summary: str = Field(description="Executive summary")
    findings: list[ResearchFinding]
    severity: str
    recommended_actions: list[str]


@dataclass
class AgentContext:
    user_id: str
    org_id: str
    session_id: str


RESEARCHER_SUBAGENT = {
    "name": "researcher",
    "description": (
        "Use for tasks requiring web search, document retrieval, or "
        "gathering information from external sources."
    ),
    "system_prompt": (
        "You are a research specialist. Find accurate, well-sourced "
        "information. Always cite sources. Return structured findings."
    ),
    "model": "anthropic:claude-haiku-4.5",  # Cost optimization
    "response_format": ResearchFinding,
    "permissions": [
        FilesystemPermission(operations=["read"], paths=["/**"]),
    ],
}

ANALYST_SUBAGENT = {
    "name": "analyst",
    "description": (
        "Use for data analysis, calculations, or synthesizing "
        "research findings into a coherent report."
    ),
    "system_prompt": (
        "You are a data analyst. Synthesize research findings into "
        "actionable analysis. Every claim must trace to evidence."
    ),
    "model": "anthropic:claude-sonnet-4-6",
    "response_format": AnalysisReport,
    "permissions": [
        FilesystemPermission(operations=["read"], paths=["/**"]),
        FilesystemPermission(operations=["write"], paths=["/output/**"]),
    ],
}


def create_orchestrator(tools: list[Any] | None = None) -> Any:
    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        system_prompt=(
            "You are a senior technical coordinator. Decompose complex "
            "requests and delegate to specialists.\n\n"
            "DELEGATION GUIDELINES:\n"
            "- Use 'researcher' for information gathering.\n"
            "- Use 'analyst' for data synthesis.\n"
            "- Handle simple questions directly -- do NOT over-delegate.\n"
            "- Never delegate a task that would take fewer than 3 tool calls."
        ),
        tools=tools or [],
        subagents=[RESEARCHER_SUBAGENT, ANALYST_SUBAGENT],
        checkpointer=MemorySaver(),
        interrupt_on={"delete_file": True},
    )
    return agent
```

### Delegation Decision Framework

```python
class DelegationDecisionFramework:
    """Codifies when to delegate vs. handle directly."""

    DELEGATE_WHEN = [
        "Single agent context regularly approaches 65% of window capacity",
        "Tasks naturally decompose into independent subtopics",
        "Different subtasks benefit from different models (cost optimization)",
        "Need to parallelize work (async subagents)",
        "Task requires 5+ distinct tool calls in a focused area",
    ]

    DO_NOT_DELEGATE_WHEN = [
        "Task requires tight sequential reasoning across all context",
        "Subagent overhead exceeds benefit (fewer than 3 tool calls)",
        "Fewer than 3-5 distinct subtask types in the workload",
        "Task is a simple question answerable from current context",
        "Flow-control overhead often exceeds benefits for <5 responsibilities",
    ]

    SCALING_LIMITS = {
        "supervisor_degradation": "Noticeable after 8-12 subagent round trips",
        "practical_swarm_limit": "100 agents (Kimi K2.5)",
        "demonstrated_swarm_limit": "300 agents (Kimi K2.6)",
        "recommended_max_layers": 2,
    }

    @classmethod
    def should_delegate(cls, tool_calls_needed: int, context_utilization: float) -> bool:
        return tool_calls_needed >= 3 and context_utilization < 0.65
```

### Restricted Subagent Permissions

```python
# Restricted subagent: read everywhere, write only to /output/
{
    "name": "restricted-writer",
    "permissions": [
        FilesystemPermission(operations=["read"], paths=["/**"]),
        FilesystemPermission(operations=["write"], paths=["/output/**"]),
    ],
}
```

---

## Interview Q&A

**Q1. What is Deep Agents delegation, in one minute?**
I treat subagents as context quarantine, not a scheduler. A sync `task` is a nested LangGraph graph with a fresh `messages` list and a single `ToolMessage` handoff. Async is Agent Protocol on another thread. Dynamic is `task()` inside QuickJS `eval`. Default GP is on, so a bare `create_deep_agent` already has `task`. Todos are opt-in since v0.7. `recursion_limit` 9,999 is a fuse, not max hops.

**Q2. Walk parent task -> child -> handoff.**
The parent model emits `task(description, subagent_type)`, optionally several in one message for parallel fan-out. Middleware copies parent state minus `messages` / `todos` / `structured_response` / private keys, overwrites `messages` with the description, runs the child until it stops tools, and returns one ToolMessage -- JSON if `response_format` is set, else last non-empty AI text (walk backward past empty Anthropic `end_turn`). The parent relays a summary; the report is not shown to the user. `runtime.context` is forwarded. Child bound `recursion_limit` wins over the parent.

**Q3. What does delegation buy you?**
Context isolation, specialization, and parallelism. The biggest practical win is often isolating heavy tool use from the coordinator's context window. Compression ratios of 10:1 to 50:1 (Anthropic's engineering measurements).

**Q4. How do I run with no task tool?**
`GeneralPurposeSubagentProfile(enabled=False)` **and** no synchronous `subagents=`. Then SubAgentMiddleware is never attached. Async specs still get their five tools. `excluded_middleware={"SubAgentMiddleware"}` raises `ValueError`. Empty `subagents=` alone does **not** disable GP.

**Q5. What are the most important inheritance rules?**
Tools and model inherit by default (lists **replace** entirely). `system_prompt` and middleware do **not** inherit. `permissions` inherit unless replaced (no merging). Only the `general-purpose` child inherits parent skills. Compiled and async own their runnable/remote graph -- no parent HITL, permissions, or MCP. Interpreter `task()` skips parent `interrupt_on` -- gate `eval`.

**Q6. How is AsyncSubAgent different from a normal subagent?**
It launches non-blocking background work, maintains state on its own Agent Protocol thread, and can later be checked, updated, or cancelled. The supervisor gets a task ID immediately and can continue chatting. The `async_tasks` channel survives parent summarization.

**Q7. When should I use ASGI versus HTTP transport for async subagents?**
Use ASGI when the graphs are co-deployed and you want in-process calls (zero extra auth, but requires `ainvoke`). Use HTTP when subagents live on a remote Agent Protocol server or need independent scaling.

**Q8. Give me $ per 1k for 0 vs 1 vs N children.**
Inferred, Sonnet 4.6, 10 parent calls, 2k cached prefix -> **$223 / 1k** with zero children. One 8-call child is **$180** -> **$403 / 1k** (1.81x). Three Sonnet children **$763 / 1k**. Five **$1,123 / 1k**. Three Haiku children on a Sonnet parent land at **$403 / 1k** (same as 1 Sonnet child). Anthropic's 15x chat is the ceiling for "research everything."

**Q9. What p50/p95/p99 do you put on parent-wait vs async?**
Nobody publishes them. I contract an 8-call sync child the parent waits on at **16,000 / 64,000 / 160,000 ms**, 8x a 2s/8s/20s ReAct cycle. Three parallel equal children stay in that band if gather isolation holds (max, not sum). Async ASGI launch+ack is **2,000 / 8,000 / 20,000 ms** -- child work is off the user path. HITL on a sync child **30,000 / 180,000 / 600,000 ms**, expire-deny.

**Q10. Why is recursion_limit 9,999, and what was #1698?**
LangGraph `merge_configs` dropped 10000 as the default sentinel, so nested graphs fell back to 25. Binding 9,999 dodges that. #1698: 0.4.4 children ran at 25, `GraphRecursionError` became `CancelledError` and `asyncio.gather` cancelled the sibling. Parent `recursion_limit=300` did nothing. It is a super-step fuse, not max task calls. I still set `ToolCallLimitMiddleware` on task and `ModelCallLimitMiddleware` on the child.

**Q11. Is task planning built into Deep Agents by default?**
Not anymore. Starting in v0.7, it is opt-in through `TodoListMiddleware()`. LangChain's evals showed higher cost and no accuracy on capable models. Statuses: `pending`, `in_progress`, `completed`. Streaming via `stream.values.todos`. I enable it for long plans, weak models, or UI progress -- not for completeness.

**Q12. When should I use `CompiledSubAgent`?**
When the child needs a prebuilt LangGraph workflow instead of a simple declarative spec. State **must** include `messages`. It does not inherit parent state_schema, interrupt_on, or the Deep Agents subagent stack. Dynamic response schema cannot target a compiled child.

**Q13. How do dynamic subagents differ from normal task calls?**
The interpreter dispatches them from code using `task()`, which is useful for loops, filtering, batch orchestration, and adversarial verification. But `interrupt_on` is **not** enforced per dispatch. Disable with `CodeInterpreterMiddleware(subagents=False)`.

**Q14. Zero-Trust MCP on children -- inherit or not?**
MCP is just BaseTool objects on `tools=`. Declarative omit -> full parent MCP. Declarative specify -> only that list (specialist RBAC). GP inherits everything. Compiled/async do not inherit. `permissions=` is fail-open FS-only and never sees MCP. Identity from forwarded `runtime.context`, never from task JSON.

**Q15. PII -- detect -> redact -> audit for children.**
Child traces hold dumps the parent never sees, so quarantine is not DLP. I detect regex + optional ML on description, child tool I/O, handoff JSON, and todos titles. I use `PIIMiddleware` on the **child** list (not inherited) and on the parent handoff. I audit WORM of decisions: pre/post hashes, entity types, cid, lc_agent_name, ls_agent_type=subagent.

**Q16. Circuit breaker and fallback.**
The library does not ship a breaker. I wrap task/child-model: closed -> open -> half-open with one specialist probe. Fallback is **GP on -> GP off (specialists only) -> parent-only**. Never fail open to unbounded Promise.all, never auto-approve HITL, never excluded_middleware SubAgent.

**Q17. What is the most common async anti-pattern?**
Launching a background task and then immediately polling it in a loop, which turns async delegation back into blocking.

**Q18. How do you observe delegated work?**
For sync subagents, use `stream.subagents` (product-level, user-facing). For async work, use the async task tools and follow traces by thread ID. LangSmith tags with `lc_agent_name` enable per-subagent cost attribution. Collapse UI cards at 5+ children.

**Q19. Sync vs async vs dynamic -- how do you choose?**
Parent must have result before next thought: sync task, parallel in one message. User should keep chatting, cancel, or steer: async preview. Batch classify-and-act / adversarial verify: dynamic task() with JS N/round cap and eval gated. L1 support: no subagents. Shared HITL conversation: parent-only.

**Q20. What does the protocol stack look like in 2026?**
MCP for agent-to-tool (most mature). A2A for agent-to-agent (150+ orgs; IBM ACP merged in). AG-UI for agent-to-frontend (40+ framework integrations, SSE transport). Note: LangChain's ACP (Agent Client Protocol, stdio for editors) is different from IBM's ACP.

---

## System Design Scenarios

### Scenario 1: Enterprise Document Processing Pipeline

**Problem**: Process legal contracts (20-100 pages each), extract key terms, flag risks, generate compliance summaries. 200 contracts/day. 3 specialized analyses per contract. Latency: <10 minutes. Cost: <$2 per contract.

```
+------------------------------------------------------------------+
|                    COORDINATOR AGENT (Sonnet)                     |
|  Context: ~20K tokens (section metadata only, not full text)     |
|                                                                  |
|  +---------------+ +---------------+ +------------------+        |
|  | Financial      | | Legal Risk     | | Regulatory       |       |
|  | Terms Analyst  | | Analyst        | | Compliance       |       |
|  | (Haiku)        | | (Sonnet)       | | (Sonnet)         |       |
|  | Structured:    | | Structured:    | | Structured:      |       |
|  | FinancialTerms | | RiskFlags      | | ComplianceCheck  |       |
|  +---------------+ +---------------+ +------------------+        |
|                                                                  |
|  Fan-Out: All 3 subagents run concurrently                       |
|  Output: ComplianceReport Pydantic model                         |
+------------------------------------------------------------------+
```

**Trade-off Matrix:**

| Decision | Option A | Option B | Choice | Rationale |
|----------|----------|----------|--------|-----------|
| Execution model | Sequential | Parallel async fan-out | **Parallel (B)** | 3 independent analyses; cuts latency from 30min to 10min |
| Financial analyst | Sonnet | Haiku | **Haiku (B)** | Term extraction is pattern-matching; 1/10 cost |
| Legal/regulatory | Haiku | Sonnet | **Sonnet (B)** | Risk assessment requires nuanced reasoning |
| Output validation | Free text | Pydantic structured | **Pydantic (B)** | Prevents hallucinated risk flags; enables validation |
| Coordinator context | Full contract text | Section metadata only | **Metadata (A)** | Coordinator routes, not analyzes; stays at ~20K |

**Cost estimate** per contract: Coordinator ~$0.20 + Financial (Haiku) ~$0.01 + Legal (Sonnet) ~$0.20 + Regulatory (Sonnet) ~$0.17 = **~$0.58** (well under $2 target).

### Scenario 2: Research Copilot -- GP + Specialist Reviewer

**Problem**: Strategy team internal research copilot over Confluence, tickets, and public web. Work is breadth-first: gather sources, then verify citations/policy before a brief reaches a VP. Queries range from "what's the weather" (must stay cheap) to due-diligence (15x chat / millions of tokens regime).

**Recommended architecture:**

```
  +---------+   +-------------------------------------------------------------+
  | IdP/PEP |-->| CONTROL: GP ON (or cheap research-agent spec)               |
  | JWT ->  |   |   ToolCallLimitMiddleware(tool_name=task)                   |
  | user_id |   |   reviewer: tools={read_file,ls,glob,grep}+cite            |
  |         |   |   permissions REPLACE: deny writes; read /workspace/**      |
  |         |   |   response_format=Findings  interrupt_on /memories/**       |
  |         |   |   ModelCallLimitMiddleware on each child spec               |
  |         |   |   PII detect->redact->audit on child traces + handoff      |
  +---------+   +--------------------------------------------------------------+
```

**Cost envelope [inferred]:** 1 parent + 3 Sonnet gatherers + 1 Haiku reviewer approximately $0.82 / run -> ~$820 / 1k at 8-call-child toy size. Real research is dollars per query.

**Trade-off Matrix:**

| Axis | GP + Haiku reviewer (recommended) | Parent-only (no specialists) | Dual reviewers + judge (Promise.all) |
| --- | --- | --- | --- |
| **Cost** | ~$820 / 1k toy; real -> Anthropic 15x chat | $223 / 1k but window fills | +2x child bill on critical path |
| **Latency** | max gatherers + reviewer | Lowest p99 from one model | Same sync wait + HITL bypass from eval |
| **Security** | Best least-privilege on reviewer | Smallest tool surface | eval bypasses parent HITL |
| **Ops** | Medium: two specs, allowlists | Lowest | High: beta interpreter, PTC, eval HITL |

### Scenario 3: Customer Support -- L1/L2/L3 with Escalation

**Problem**: B2B SaaS, 50,000 active users, ~2,000 support conversations/day. L1 (FAQ), L2 (technical troubleshooting), L3 (escalation to human). Requirements: <5s TTFB, streaming, cross-session history, SOC2 audit trail.

**Key architectural decisions:**

- **L1 via Skill Identity Pattern** (60-70% of queries): Coordinator loads relevant skill instructions on demand (~5K tokens) and handles directly. No delegation overhead. Fastest path.
- **L2 via Diagnostic Subagent**: Diagnosis requires 5-15 tool calls (log search, metric query, config check). Subagent returns structured DiagnosticReport.
- **L3 via HITL Interrupt**: `interrupt_on={"escalate_to_human": True}`. Checkpointer preserves full conversation state during human review.
- **<5s TTFB**: Prompt caching (1-hour TTL on Bedrock) + SSE transport (<100ms event delivery).
- **Cross-session memory**: RAG for history (2,000 conversations/day exceeds AGENTS.md capacity).

### Scenario 4: Support Agent -- No-Subagents vs Fan-Out

**Problem**: L1 password resets + refund HITL must stay on one conversation state. Overnight batch of ~200 tickets needs classify-and-act.

**Recommended: L1 parent-only; L2 batch dynamic with hard N cap.**

| Axis | No subagents (L1) | Declarative specialists + parallel task | Dynamic classify-and-act (L2 batch) |
| --- | --- | --- | --- |
| **Cost** | 1.0x parent; GP never happens | ~1 + 0.8N similar-sized child | Unbounded N unless JS slice |
| **Latency** | Lowest p99 | Parent waits on max of parallel children | One eval waits on Promise.all |
| **Security** | Smallest surface; HITL on parent | Least-privilege per specialist if tools replace | interrupt_on bypass per JS task() |

**Decision:** L1 parent-only wins for interactive. Dynamic classify-and-act wins for overnight batch with JS cap, Haiku specialists, todos off.

**200-ticket fan-out cost [inferred]:** Haiku specialists: 200 x $0.060 = **$12** in child tokens. Sonnet children: 200 x $0.180 = **$36**.

---

## Key Numbers to Memorize

### Package / Forms / Versions
| Number | What |
| --- | --- |
| **0.7.12** | Research pin |
| **>=0.5.0** | Async subagents preview |
| **>=0.5.3** | Structured output on subagents |
| **v0.7** | Todos opt-in; ~6k->~2k base tokens (-65%) |
| **beta** | Dynamic interpreter (quickjs, Python >=3.11) |

### Tokens / Anthropic / Research
| Number | What |
| --- | --- |
| **90.2%** | Multi-agent vs single Opus 4 (internal research eval) |
| **80%** | BrowseComp variance explained by token usage |
| **4x / 15x** | Agents vs chat / multi-agent vs chat tokens |
| **3-5 / 3+ / <=90%** | Parallel subagents / tools each / wall-clock cut vs sequential |
| **50** | Early Research spawn footgun on simple queries |
| **10M+** | RLM paper context (Zhang/Kraska/Khattab) |

### $ / SKUs [inferred]
| Number | What |
| --- | --- |
| **$223 / 1k** | 0 children, 10-call cached 2k prefix |
| **$180** | One 8-call Sonnet child |
| **$403 / 1k** | 1 GP child or Sonnet parent + 3 Haiku children |
| **$763 / $1,123 / $1,303 per 1k** | 3 Sonnet / 5 cached / 5 uncached children |
| **$0.060** | Haiku 8-call child |
| **+0.8-1.0x** | Per isolated 8-call GP child vs parent bill |
| **$12 / $36** | 200-ticket Haiku vs Sonnet child batch |

### Recursion / Async / Caps
| Number | What |
| --- | --- |
| **9,999** | Deep Agents bound recursion_limit (sentinel dodge) |
| **1000** | LangGraph default since 1.0.6 |
| **25** | Nested-graph ghost; #1698 child wall |
| **~2** | Super-steps per ReAct cycle (25 = ~12 cycles) |
| **4** | Example async slots: supervisor + 3 children |

### Latency (numeric ms)
| Number | What |
| --- | --- |
| **16,000 / 64,000 / 160,000 ms** | [inferred] sync 8-call child, parent waits |
| **2,000 / 8,000 / 20,000 ms** | [inferred] async ASGI launch+ack; one ReAct cycle |
| **50 / 200 / 1,000 ms** | [inferred] async HTTP extra vs ASGI |
| **30,000 / 180,000 / 600,000 ms** | [inferred] HITL on sync child |
| **replace entirely** | Child tools / permissions lists (not merged) |
| **fail-open** | permissions= when no rule matches (FS tools only) |

**Dates:** research frozen **2026-09-02**. Do not treat inferred $ or ms as list prices or vendor SLOs.
