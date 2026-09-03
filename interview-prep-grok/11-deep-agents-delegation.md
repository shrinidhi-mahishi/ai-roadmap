# Module 11: Deep Agents Delegation (`task`, GP, specialists, async, dynamic)

**Study + interview prep.** Grounded in research dated 2026-09-02 (50 sources). Package pin **`deepagents==0.7.12`** (PyPI 2026-09-01). This file is the **delegation plane**: breaking work into isolated child windows via the `task` tool, the default **general-purpose** (GP) subagent, declarative `SubAgent` vs `CompiledSubAgent` vs `AsyncSubAgent`, interpreter `task()`, and opt-in todos. Deep Agents does **not** add a scheduler. Sync children are nested LangGraph graphs; async children are Agent Protocol runs on their own threads; dynamic children are the same `task()` capability inside a QuickJS `eval`.

Harness assembly, middleware order, `excluded_middleware` rejection, and the GP-as-second-prefix cost model live in [08-deep-agents-harness](08-deep-agents-harness.md) — **cite, do not recopy**. `stream.subagents` product vs Pregel `subgraphs` is [09-deep-agents-execution](09-deep-agents-execution.md) §2.9. Hop caps / oracles as loop stoppers are [06-agent-feedback-loops](06-agent-feedback-loops.md). Identity-not-from-JSON and the gateway PEP pattern are [07-guardrails](07-guardrails.md). `$ per 1k runs` is **[inferred]** from published unit prices × a stated run shape, not a SKU. LangChain publishes **no** p50/p95/p99 of parent-wait vs async — missing percentiles are architecture-derived **[inferred]** and marked.

Delegation gates: async preview `>=0.5.0`; filesystem `permissions` on children `>=0.5.2`; structured output on subagents `>=0.5.3`; todos **opt-in since v0.7**; dynamic subagents **beta** (`pip install -U "deepagents[quickjs]"`; `langchain-quickjs>=0.2.0`; Python `>=3.11`).

---

## What Is This?

**Subagents quarantine context.** A sync `task(description, subagent_type)` starts a **fresh child instance**. The child’s `messages` is **not** the parent transcript. The child runs until it stops calling tools. The parent gets **one** `ToolMessage` (JSON if `structured_response` / `response_format` is set; else last non-empty `AIMessage` text). Intermediate tool dumps stay in the child window. The `task` description tells the parent: the report is **not** shown to the user; relay a summary yourself.

**Default GP is on.** Bare `create_deep_agent(model=...)` auto-adds a sync spec named `general-purpose` unless you already registered that name, and therefore attaches `SubAgentMiddleware` + `task`. GP has filesystem tools by default and the same tools/model as the parent unless overridden. It inherits parent **skills**. Disable only via `HarnessProfile.general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)` **and** no synchronous `subagents=` — not via `excluded_middleware={"SubAgentMiddleware"}` (`ValueError`).

**Todos are opt-in since v0.7.** `create_deep_agent` no longer ships `TodoListMiddleware`. Restore with `middleware=[TodoListMiddleware()]`. Codex harness profile still injects it. LangChain’s own evals: **no** statistically significant accuracy gain; token usage **up** on two of three models in PR #4929.

**`recursion_limit` is not a hop cap.** `create_deep_agent` binds **9,999** super-steps ([08](08-deep-agents-harness.md)). That is a LangGraph fuse (and a 10,000-sentinel dodge), **not** `max_task_calls`. Product caps belong in `ToolCallLimitMiddleware(tool_name="task")`, `ModelCallLimitMiddleware` on the child spec, or application state.

Think sealed rooms, not extra CPUs. Isolation trades parent-context tokens for a **second full prefix**. Parallelism is **model-issued parallel tool calls** in one parent message, not a harness thread pool.

## Why It Matters

Almost every “multi-agent platform” interview now forks here: is Deep Agents a new orchestrator, or nested graphs with a single handoff? Trap answers: “`excluded_middleware` drops `task`,” “empty `subagents=` disables GP,” “9,999 is max hops,” “todos make capable models more accurate,” “compiled/async children inherit parent HITL,” “`interrupt_on={"task": True}` catches interpreter `task()`.”

Anthropic’s production Research system is the same topology with published economics: subagents are **compression**. Multi-agent beat single-agent Opus 4 by **90.2%** on their internal research eval; token usage explained **80%** of BrowseComp variance; agents ≈ **4×** chat tokens, multi-agent ≈ **15×** chat; a lead spawning **3–5** subagents (each **3+** tools in parallel) cut research time by up to **90%**. They also warn: coding has fewer truly parallelizable tasks; agents that must share one context are a poor fit. Deep Agents maps that idea onto `task`, not a proprietary Research product.

---

### 1. System Topology & Data Flow

Two stacked planes. Construction / profile / HITL wiring / worker-pool sizing are control (LLM-free). `task` args, child messages, `write_todos` payloads, interpreter JS, and async prompts are data (untrusted). Persistence is the **parent** checkpointer **plus** (optionally) a child compile flag **plus** async Agent Protocol threads — they are **not** one transaction. Tool proxies are `task`, the five async tools, interpreter `task()`, and opt-in `write_todos`. Telemetry is LangSmith (`lc_agent_name`, `ls_agent_type="subagent"`) plus `stream.subagents` (cite [09](09-deep-agents-execution.md); do not recopy the field table). Deep Agents does not ship a second APM or WORM of spawn events — you add that.

```
                         TELEMETRY / OBSERVABILITY SINKS
         ┌──────────────────────────────────────────────────────────────────┐
         │  LangSmith: metadata.lc_agent_name=<spec name>                   │
         │             tracing-context ls_agent_type="subagent"             │
         │  Async: supervisor launch/check/update/cancel/list; child run    │
         │         linked by thread ID                                      │
         │  stream.subagents (product UI) ≠ stream.subgraphs (Pregel) [09]  │
         │  WORM you build: (cid, thread_id, spec, desc_digest, spawn_ts)   │
         │  Filter: has(metadata, '{"lc_agent_name": "research-agent"}')     │
         └────────────▲─────────────────────▲──────────────────▲────────────┘
                      │ child spans         │ stream handles    │ spawn audit
                      │                     │                   │
┌─────────────────────┴─────────────────────┴───────────────────┴───────────┐
│ CONTROL PLANE  (assembly — LLM-free; disable-GP / HITL / caps live here)  │
│                                                                           │
│  create_deep_agent(subagents=…)                                           │
│  HarnessProfile.general_purpose_subagent  (enabled / rename / re-prompt)  │
│  TodoListMiddleware() opt-in since v0.7                                   │
│  CodeInterpreterMiddleware(subagents=…)   (dynamic on if both present)    │
│  interrupt_on / permissions on specs      recursion_limit bind (9,999)    │
│  ToolCallLimitMiddleware(tool_name="task")  ModelCallLimitMiddleware      │
│  langgraph dev --n-jobs-per-worker  (async slot pool)                     │
│  SubAgentMiddleware attached IFF ≥1 sync spec (incl. auto-GP)             │
│  AsyncSubAgentMiddleware attached IFF async specs present                 │
│  excluded_middleware={"SubAgentMiddleware"} → ValueError (not a toggle)   │
└────────────────────────────────┬──────────────────────────────────────────┘
                                 │ CompiledStateGraph (same type as 08)
                                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ DATA PLANE  (untrusted — parent proposes task; child loop disposes)       │
│                                                                           │
│  Parent: task(description, subagent_type) | start_async_task | eval JS    │
│  Child:  HumanMessage(description) only; tools until stop                 │
│  Handoff: ONE ToolMessage (JSON or last AI text). Not a user-visible turn │
│                                                                           │
│  ┌────────────── TOOL PROXIES (delegation surface) ─────────────────────┐ │
│  │ Sync:  task  (SubAgentMiddleware)                                    │ │
│  │ Async: start_async_task / check / update / cancel / list             │ │
│  │ Dyn:   JS task({description, subagentType, responseSchema?})         │ │
│  │ Plan:  write_todos (only if TodoListMiddleware restored)             │ │
│  │ Child tools: inherited or replaced BaseTool list (MCP is just tools) │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└─────────┬───────────────┬─────────────────┬─────────────────┬─────────────┘
          │               │                 │                 │
          ▼               ▼                 ▼                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE LAYER  (parent thread ≠ child invoke ≠ async thread)          │
│                                                                           │
│  ┌──────────────┐ ┌──────────────┐ ┌─────────────┐ ┌──────────────────┐  │
│  │ Parent       │ │ Sync child   │ │ Async child │ │ Interpreter      │  │
│  │ checkpointer │ │ compile():   │ │ own AP      │ │ mode=thread      │  │
│  │ messages,    │ │  None inherit│ │ thread +    │ │ (default): JS    │  │
│  │ interrupts,  │ │  True/thread │ │ run;        │ │ vars persist     │  │
│  │ async_tasks  │ │  False=none  │ │ durability  │ │ across turns     │  │
│  │ channel      │ │  parallel    │ │ on server   │ │ turn/call reset  │  │
│  └──────────────┘ │  OK only None│ └─────────────┘ └──────────────────┘  │
│                   └──────────────┘                                        │
│  StateBackend files SHARED with sync children (backend, not messages) [09]│
│  todos / structured_response / PrivateStateAttr stripped both directions  │
└───────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not couple):**

| Plane | Lives here | LLM-free? |
| --- | --- | --- |
| **Control** | `subagents=`, `HarnessProfile.general_purpose_subagent`, `TodoListMiddleware` opt-in, `CodeInterpreterMiddleware(subagents=…)`, `interrupt_on` / `permissions` on specs, `recursion_limit` bind, async worker-pool sizing | Yes for assembly. Disable-GP, attach-`task`, and HITL wiring are deterministic |
| **Data** | `task(description, subagent_type)` args, child messages, child tool results, `write_todos` payloads, interpreter `task({…})` JS, async `start_async_task` prompts | No — untrusted token/JS stream |

`create_deep_agent` control-plane assembly (from [08](08-deep-agents-harness.md), cite not recopy): resolve model/profile → backend → middleware stack → **build default GP + caller subagents** → system prompt → `create_agent` → `.with_config({recursion_limit: 9_999, ...})`. `SubAgentMiddleware` (and therefore `task`) is attached **only when ≥1 synchronous subagent exists**, including auto-GP.

**Data-plane isolation contract** for a sync `task` call:

1. Fresh child instance; child `messages` is **not** the parent transcript.
2. Child runs autonomously until it stops calling tools.
3. **Single handoff**: parent receives one `ToolMessage`. If `structured_response` is set, that object is JSON-serialized; else last non-empty `AIMessage` text. The extractor **walks backward** (Anthropic may emit a trailing empty `end_turn` `AIMessage`).
4. **Stateless messaging**: the child cannot stream multiple user-visible turns back.
5. Heavy intermediate tool calls stay in the **child** window.

Source (`subagents.py`) copies parent state **minus** `_EXCLUDED_STATE_KEYS = {messages, todos, structured_response}` and minus `PrivateStateAttr` keys, then overwrites `messages` with a single `HumanMessage(description)`. `todos` and `structured_response` have no defined reducer for parent←child, so they are stripped on the way back. Middleware-private fields are excluded **both** directions.

**Request-flow narrative (parent `task` → child graph → single handoff):**

1. **Control / construction.** Application calls `create_deep_agent`. If GP is enabled or any sync spec is passed, `SubAgentMiddleware` registers `task`. Async specs register the five async tools on a **separate** middleware slot. Interpreter + subagents together expose JS `task()` unless `CodeInterpreterMiddleware(subagents=False)`.
2. **Parent turn.** The coordinator model emits one or more `task` tool calls. Official teaching: launch independent work **in a single message with multiple tool calls**. Unknown `subagent_type` returns a **string error** listing allowed types (does not raise). Missing `tool_call_id` raises `ValueError`.
3. **Sync child (default).** Nested graph under the tools node. Parent **blocks** until that child (or, for parallel calls in one super-step, until the gather) finishes. Child sees only the description plus inherited/replaced tools. `runtime.context` (`context_schema`) **does** propagate to all subagents and their tools. Parent `callbacks` / `tags` / `configurable` reach the child via LangGraph `ensure_config` ambient merge; child’s **bound** `recursion_limit` / `metadata` **wins**. The task tool stamps `configurable.ls_agent_type = "subagent"` plus a tracing tag. Explicitly forwarding parent keys would double-count tags.
4. **Handoff.** One `ToolMessage` on the parent. Files on a shared **StateBackend** remain (backend sharing, not message sharing — [09](09-deep-agents-execution.md)). Parent relays a user-visible summary.
5. **Parallel vs async vs dynamic.**
   - **Parallel sync:** model-issued parallel `task` calls in one super-step. LangGraph runs sibling tool nodes together. Parent wait ≈ **max**(children) + synthesis, not a thread pool. Historical `asyncio.gather` without `return_exceptions=True` cancelled siblings on one child’s `GraphRecursionError` (#1698 / related #694).
   - **Async:** `start_async_task` returns a job ID immediately. Child is an Agent Protocol **thread**. Supervisor must **not** poll immediately. Completion is off the chat turn until the user asks; status lives on the dedicated `async_tasks` channel (survives summarization).
   - **Dynamic:** model writes JS (`Promise.all`, `while`, branches). One `eval` **blocks that parent turn** until the promise settles. Word **“workflow”** in the user request is a documented trigger to orchestrate from code. `task()` inside `eval` **does not** go through the normal tool-calling path — parent `interrupt_on` is **not** enforced per dispatch.
6. **Stream / observe.** Root `stream.messages` = coordinator only. `stream.subagents` = one handle per delegated `task` (`.name`, `.path`, `.status`, `.messages`, `.tool_calls`, nested `.subagents`, `.output`). Lifecycle-only UIs await `subagent.output` without subscribing to tokens. Index cards by the coordinator tool-call id. Collapse completed cards at **5+** children. Handle errors **per card**. Protocol details: [09](09-deep-agents-execution.md) §2.9 — not recopied. Frontend `useStream` example uses `{ config: { recursion_limit: 100 } }`; the same page claims a Deep Agents default of **10,000**, which disagrees with `graph.py`’s **9,999** bind.
7. **Stop.** Child stops tools; or `GraphRecursionError` at the child’s bound ceiling; or HITL `Command(resume=...)` (requires checkpointer); or async `cancel` / terminal cache; or parent `ToolCallLimitMiddleware` refuses further `task`.

**Official when / when-not ([2] condensed):**

| Use subagents | Do not |
| --- | --- |
| Multi-step work that would clutter the parent window (search, file reads, DB dumps) | Simple single-step tasks |
| Specialized domains (different instructions/tools/models) | When the parent **needs** intermediate evidence (quarantine would hide it) |
| Keep the parent on high-level coordination | When overhead outweighs the isolation win |

**Running without subagents (no `task` tool)** — **two** conditions, both required:

1. `general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)` on the active harness profile (Python `register_harness_profile` or YAML `general_purpose_subagent.enabled: false`).
2. Pass **no** synchronous specs via `subagents=`.

Then `SubAgentMiddleware` is never attached. Async specs are **unaffected**. Do **not** list `SubAgentMiddleware` in `excluded_middleware`. Empty `subagents=` **alone does not** drop GP — GP auto-adds.

**Escape hatch:** `create_agent` + `SubAgentMiddleware` + `FilesystemMiddleware` + optional `TodoListMiddleware` by hand — `task` isolation without GP auto-add, skills, or profiles. You own the stack; the disable path is “don’t attach the middleware.”

---

### 2. Core Mechanics & Algorithms

#### 2.1 Invariants (delegation, not a scheduler)

**I1.** Deep Agents introduces **no** new fan-out scheduler. Sync = nested graph from `task`. Async = Agent Protocol run on its own thread. Dynamic = `task()` bridged into QuickJS `eval`.

**I2.** Default GP is **on**. `task` exists whenever ≥1 sync spec exists, including auto-GP. You cannot `excluded_middleware` that scaffolding (`ValueError`). `_REQUIRED_MIDDLEWARE` pair is Filesystem + SubAgent ([08](08-deep-agents-harness.md)); `AsyncSubAgentMiddleware` is **not** in that pair — omit async specs and the slot stays empty.

**I3.** Isolation is a **context** contract, not a compliance contract. Child traces hold the dumps the parent never sees. A fat final report (`raw_dump: str`, “paste the search results”) **undoes** quarantine.

**I4.** Sync children **cannot spawn grandchildren via `task`**. The sync child stack has no nested `SubAgentMiddleware`. Dynamic dispatch is the supported fan-out-from-code path. Smuggling `task` through a `CompiledSubAgent` is the unsupported back door.

**I5.** `recursion_limit` is a **super-step ceiling**, not `max_task_calls`. Putting it inside `configurable` is a silent no-op.

**I6.** Parallel sync `task` is documented **only** for ephemeral children (`checkpointer=None` inherit). Per-thread compiled subgraphs (`checkpointer=True`) **do not** support parallel tool calls — sibling writes collide on the same `checkpoint_ns`.

**I7.** Identity for AuthZ is `runtime.context` / `rt.server_info.user.identity`, never `task(description=...)` JSON. `lc_agent_name` is **harness metadata** (safe to branch tool policy on); it is not an end-user principal.

#### 2.2 Five forms (GP vs declarative vs compiled vs async vs dynamic)

`subagents=` accepts dictionaries (`SubAgent`) or `CompiledSubAgent` objects. Docs historically said “two types”; 0.5+ also ships `AsyncSubAgent` on the same kwarg, routed to different middleware. Dynamic is **not** a spec type — it is interpreter dispatch onto configured specs.

| Form | What it is | How parent calls it | When to pick |
| --- | --- | --- | --- |
| **Default GP** | Auto-added sync spec `general-purpose` unless that name is already registered | `task(..., subagent_type="general-purpose")` | Context isolation **without** a specialist. Same tools/model as parent unless overridden. Inherits parent **skills** |
| **Declarative `SubAgent`** | Dict: `name`, `description`, `system_prompt` **required**; optional `tools`, `model`, `middleware`, `interrupt_on`, `skills`, `response_format`, `permissions` | `task` with that `name` | Most production specialists. Harness compiles via `create_agent` and injects the **sync subagent middleware stack** |
| **`CompiledSubAgent`** | `{name, description, runnable}` — precompiled LangGraph / `create_agent` graph. State **must** include `"messages"` | Same `task` surface | Custom graphs, DAGs, or an agent that already has its own HITL/structured-output wiring. Does **not** inherit parent `state_schema`, `interrupt_on`, or the Deep Agents subagent stack |
| **`AsyncSubAgent`** | `{name, description, graph_id, url?, headers?}` — Agent Protocol worker. Preview `>=0.5.0` | `start_async_task` / `check` / `update` / `cancel` / `list` — **not** `task` | Long-running, mid-flight steering, cancellation, independent scale |
| **Dynamic** | JS global `task({ description, subagentType, responseSchema? })` whenever subagents **and** `CodeInterpreterMiddleware` are both present. **Beta**. On by default in that combo | Model writes loops / `Promise.all` / branches inside `eval` | Classify-and-act, batch fan-out, adversarial verify, tournament, loop-until-done. Disable with `CodeInterpreterMiddleware(subagents=False)` |

**GP auto-add rule:** Deep Agents adds sync `general-purpose` unless you already pass a sync subagent with that name. Replace by passing `name="general-purpose"`. Rename/re-prompt via `HarnessProfile.general_purpose_subagent=GeneralPurposeSubagentProfile(...)`. Disable via `enabled=False` **and** no other sync specs.

GP default prompt (`DEFAULT_SUBAGENT_PROMPT`) tells the child the parent only sees the final assistant message. GP default description (`DEFAULT_GENERAL_PURPOSE_DESCRIPTION`) is action-oriented: use GP when keyword/file search is unlikely to hit on the first tries; GP has “all tools as the main agent.”

**`mode="fork"`** on `SubAgent` is **experimental** in 0.7.12: continues the parent conversation instead of isolating. `system_prompt` is appended; cannot define `skills`. Documented anti-pattern unless you explicitly want a shared transcript ([08](08-deep-agents-harness.md)).

**`TaskToolSchema`:**

| Arg | Meaning |
| --- | --- |
| `description` | Detailed autonomous work order. Include all necessary context and expected output format. The child cannot see user intent |
| `subagent_type` | Must be a registered name |

**Complexity [architecture, not a paper]:** one parent turn that emits \(N\) parallel `task` calls runs \(N\) isolated ReAct loops. Token cost ≈ parent prefix + \(\sum_i\) child prefixes (child cache keys **differ**). Wall-clock for sync parallel ≈ \(\max_i T_i\) + synthesis **if** gather isolation holds. Serial `task` waves are \(\sum T_i\). No harness cap on \(N\).

#### 2.3 Inheritance matrix (HITL / permissions / tools / skills)

| Field | Declarative `SubAgent` | GP (auto) | `CompiledSubAgent` | `AsyncSubAgent` |
| --- | --- | --- | --- | --- |
| `system_prompt` | **Required**; does **not** inherit | SDK default + profile overlay; GP-specific prompt **wins** over `base_system_prompt` (orchestrator prompt must not leak into researcher GP) | Owned by the runnable | Owned by the remote graph |
| `tools` | Inherits parent; a provided list **replaces entirely** (empty list = no inherited tools) | Same tools as parent (FS + caller `tools=`, including MCP) | Owned by the runnable | Owned by the remote graph |
| `model` | Inherits; override with `provider:model` or `BaseChatModel` | Same as parent unless replaced | Owned by the runnable | Owned by the remote graph |
| `middleware` | Does **not** inherit parent extras. Merged into the **sync subagent stack** by `.name`. No nested `SubAgentMiddleware` | Inherits main-agent **default-middleware overrides** (same `.name` replacements), not main-only extras | None from parent | N/A — remote |
| `interrupt_on` | Inherits parent; spec overrides. Requires checkpointer. Values: `True` / `False` / `InterruptOnConfig` (`allowed_decisions` e.g. `["approve", "edit", "reject"]`) | Inherits | **Does not inherit** — wire HITL inside the runnable | **Does not inherit** — configure on the remote agent |
| `skills` | Does **not** inherit. Own `SkillsMiddleware` if `skills=` set. Skill state isolated both directions | **Does** inherit parent skills | Owned by the runnable | Remote |
| `permissions` | Inherits; spec **replaces entirely** | Inherits parent | Owned by the runnable | Remote |
| `response_format` | Optional; parent then gets JSON `ToolMessage` (`>=0.5.3`) | Optional on replace-GP spec | If runnable writes `structured_response`, parent JSON-serializes it; else last AI text | Remote output extracted from thread state on `check` |
| `state_schema` | Forwarded from parent `DeepAgentState` subclass | Same | **Not** inherited — compile with a compatible schema yourself | Remote thread state |
| FS tool allowlist | Put `FilesystemMiddleware(tools=...)` on **this** spec. Parent override does **not** apply | Inherits a parent-replaced `FilesystemMiddleware` | Owned by the runnable | Remote |

Sync **child stack** vs parent ([08](08-deep-agents-harness.md), do not recopy order): (a) no nested `SubAgentMiddleware`; (b) skills run **after** `PatchToolCallsMiddleware` on inner agents.

**HITL extra:** `mode="interrupt"` filesystem rules auto-install HITL and merge with `interrupt_on` (user entries win per tool name). Child interrupt → same `interrupts` on the parent result → `Command(resume=...)`. Dynamic `task()` from `eval`: parent `interrupt_on` **not** enforced per dispatch — **gate `eval`**.

**Who called this tool:** when a `BaseTool` is shared, read `runtime.config["metadata"]["lc_agent_name"]` (same key used in streaming). Branch strict vs general lookup on that name; combine with `runtime.context` for per-agent limits. Per-subagent knobs: extra dataclass fields (`researcher_max_depth`) or namespaced keys (`researcher:max_depth`) — not stuffing knobs into `task(description=...)`. All children see the **same** parent `context` object.

Compiled runnables are `.with_config({metadata: {lc_agent_name: spec["name"]}, run_name: spec["name"]})` so a shared runnable registered under two names keeps distinct metadata.

**`CompiledSubAgent` contract:** returned state **must** have a `messages` key or the task tool raises `ValueError` (“Custom StateGraphs used with CompiledSubAgent should include `messages`…”). Writing only `structured_response` is not enough. Dynamic `response_schema` **cannot** target a compiled child — `ValueError` (`dynamic schemas require a raw SubAgent spec`).

#### 2.4 Structured output from subagents (`>=0.5.3`)

Pass `response_format` on the declarative spec: Pydantic model, `ToolStrategy(...)`, `ProviderStrategy(...)`, `AutoStrategy(...)`, bare type, or JSON schema dict — same surface as `create_agent`. Parent `ToolMessage.content` is JSON (`model_dump_json` / dataclass `asdict` / `json.dumps`). Docs example: `ResearchFindings` with `summary: str`, `confidence: float` (0–1), `sources: list[str]`. Keep the schema **small**. A `raw_dump: str` field defeats quarantine.

Configurable key `__deepagents_subagent_response_format` lets a task-tool caller request a dynamic response format per invocation.

Interpreter path: `task({..., responseSchema})` resolves to a **typed JS object** — do not `JSON.parse` unless the child intentionally returned a JSON string.

#### 2.5 Dynamic subagents (interpreter dispatch)

On by default whenever the agent has **both** subagents and `CodeInterpreterMiddleware`. Interpreter runtime is **beta**. PTC (`CodeInterpreterMiddleware(ptc=["glob"])`) is **allowlist-off by default**; dynamic subagents are **on by default** if both exist — the model can `Promise.all` many reviewers from JS without a new tool call the HITL layer sees.

This is the Deep Agents encoding of **Recursive Language Models** (Zhang, Kraska, Khattab; arXiv:2512.24601, 2025-12-31): keep the working set in interpreter variables, select slices, call sub-LMs, synthesize in code. RLMs handle inputs **two orders of magnitude** beyond the model window and, on the authors’ suite, beat compaction / retrieval-agent / code-agent scaffolds at **10M+** tokens with comparable or cheaper cost per query. Deep Agents maps `llm_query`/`sub_rlm` onto `task()` against **configured** subagents, not unbounded self-recursion.

Documented patterns: classify-and-act (`Promise.all` after a specialist map); fan-out-and-synthesize (directory review — discovering files from JS needs PTC `glob`); adversarial verification (independent reviewers + judge); generate-and-filter; tournament; **loop-until-done** (`while (true)` until a round adds nothing) — **poison-pill** if items are never stable.

Anthropic’s Research system still waits **synchronously** for each wave; they flag async as the next complexity jump. Deep Agents splits that choice: default `task` **is** the sync wave; `AsyncSubAgent` **is** the async jump (preview).

Multi-turn interpreter state: `mode="thread"` (default) persists JS variables across agent turns, so a later `eval` can see previous `task()` results. `mode="turn"` / `"call"` reset sooner. That persistence is **why** a poisoned `found` set in loop-until-done can survive into the next user message. `dcode` ships with the interpreter on; phrasing a request as a “workflow” is the live demo.

#### 2.6 Async subagents (preview `>=0.5.0`)

| Dimension | Sync `task` | Async |
| --- | --- | --- |
| Execution | Parent blocks | Returns task ID; parent continues |
| Concurrency | Parallel **but blocking** (parent waits for the gather) | Parallel **and non-blocking** |
| Mid-task updates | Not possible | `update_async_task` (interrupt + new run on same thread) |
| Cancellation | Not possible (except killing the parent run) | `cancel_async_task` → `runs.cancel()` |
| State | Stateless per invocation | **Stateful** on its own Agent Protocol thread |
| Best for | Parent must have the result before the next thought | Long-running work managed in chat |

Five tools (`AsyncSubAgentMiddleware`; current docs names): `start_async_task`, `check_async_task`, `update_async_task`, `cancel_async_task`, `list_async_tasks`. Reference-implementation repos may still say `launch_async_subagent` / `check_async_subagent` — pin the SDK.

Lifecycle: **Launch** creates a thread, starts a run with the description as input, returns thread ID as task ID. **Check** fetches run status; on success, reads thread state for final output. **Update** = new run on the same thread, `multitask_strategy=interrupt`; task ID unchanged. The example Agent Protocol server **clears thread state** before the new run — confirm on your server pin before assuming mid-flight memory survives. **Cancel** marks `"cancelled"`. **List**: live status for non-terminal tasks (fetched in parallel); terminal (`success` / `error` / `cancelled`) from cache.

**`async_tasks` channel** is dedicated on the supervisor graph, **separate from messages**, because summarization would otherwise drop task IDs stored only in tool messages. Each record: task ID, agent name, thread ID, run ID, status, timestamps (`created_at`, `last_checked_at`, `last_updated_at`).

Transport: omit `url` → **ASGI** (in-process). Both graphs in the same `langgraph.json`. Zero extra auth. Subagent is still a **separate thread**. Local ASGI requires an **async** parent entrypoint (`ainvoke`); sync `invoke` needs a reachable HTTP URL. Set `url` → **HTTP** to a remote Agent Protocol server. LangSmith Deployments auth via `LANGSMITH_API_KEY` / `LANGGRAPH_API_KEY` / `LANGCHAIN_API_KEY`. Self-hosted: `headers`. Topologies: single deployment (all ASGI), split (supervisor HTTP to workers), hybrid.

Worker pool: `langgraph dev --n-jobs-per-worker 10`. Each active run occupies a slot. Supervisor + 3 concurrent children = **4** slots. Under-provisioning **queues** launches (symptoms look like hung `start_async_task`).

Protocol resources: Threads, Runs, Store. Example server: `POST /threads`, `POST /threads/{id}/runs`, `GET .../runs/{run_id}`, `GET /threads/{id}`, `POST .../cancel`, `GET /ok`. Launch is fire-and-forget at the protocol layer (`Create Background Run` returns the run ID immediately). Durability mode on that run (`sync` / `async` / `exit`) is the Agent Server’s checkpoint durability, not Deep Agents-specific ([08](08-deep-agents-harness.md) §3).

Prompt rules (middleware injects; reinforce if the model still fails): do not poll immediately after launch; history is always stale — `check`/`list` before reporting; full `task_id`, never abbreviate.

#### 2.7 `TodoListMiddleware` (not a subagent)

v0.7 breaking change: **no longer** default on main, GP, or declarative children. Combined with empty base prompt + 43% shorter tool descriptions, **base input tokens on a default-agent turn dropped 65% (~6k → ~2k)**. Reward CIs spanned **zero** for every model in the v0.7 harness comparison vs 0.6.12. Token/cost drops were statistically clear for Luna (and Opus on tokens); Luna also a clear cost drop. `claude-sonnet-4-6` was the exception: a cost **increase** traced to two hard autonomous tasks, not to todos alone. Blog: slightly **better rewards and lower cost with todos disabled**.

When LangChain still recommends enabling: (1) long multi-step tasks that benefit from an explicit plan; (2) less capable models that drop steps; (3) UIs that stream progress from `stream.values.todos`.

Middleware behavior (LangChain, not Deep Agents-specific): adds `write_todos` + a planning system-prompt section (`WRITE_TODOS_SYSTEM_PROMPT` / `WRITE_TODOS_TOOL_DESCRIPTION` overridable). State: `todos` array; each item `{content, status}` with `pending` | `in_progress` | `completed`. **At most one `write_todos` per model turn.** The tool **replaces the entire list**; `after_model` rejects parallel calls. **Not inherited** by declarative subagents unless they opt in. GP mirrors the caller’s instance **by identity** when present ([08](08-deep-agents-harness.md)). Without the middleware, `stream.values.todos` is **absent** — do not render an empty list. Frontend: show the panel only when `todos.length > 0`; pulse a single `in_progress` item.

#### 2.8 `ToolCallLimitMiddleware` on `task` (the product cap)

No harness cap on number of `task` calls. Anthropic’s early Research agents spawned **50** subagents for simple queries. Their prompt-level scaling rules (Deep Agents does **not** encode these): simple fact → **1** agent, **3–10** tool calls; comparisons → **2–4** subagents, **10–15** calls each; complex → **>10** with divided labor.

`ToolCallLimitMiddleware` / `ModelCallLimitMiddleware` are LangChain prebuilts. For `task` on the **parent**:

| Knob | Meaning |
| --- | --- |
| `tool_name="task"` | Limit only delegation, not `read_file` |
| `run_limit` | Max calls per user turn (resets each message) |
| `thread_limit` | Max across the conversation; **requires checkpointer** |
| `exit_behavior` | `"continue"` (default): exceeded calls get an error string, other tools proceed; `"error"`: raise `ToolCallLimitExceededError`; `"end"`: stop with ToolMessage+AI — **single-tool only**, else `NotImplementedError` |

At least one of `thread_limit` / `run_limit` is required. `ModelCallLimitMiddleware(run_limit=N, thread_limit=M, exit_behavior="end"|"error")` on the **child spec** caps that child’s ReAct. `thread_limit` needs a checkpointer; `run_limit` resets each user message. These finish **cleanly** (or throw) instead of `GraphRecursionError`. Forum recipe: parent state `subagent_calls` / `max_subagent_calls` when you want a counter the graph can branch on.

Raising the child ceiling to 9,999 **without** `ModelCallLimitMiddleware` converts a 25-step bump into an unbounded bill ([06](06-agent-feedback-loops.md), [08](08-deep-agents-harness.md)).

#### 2.9 `recursion_limit` 9,999 — cite 08, keep the delegation bug

LangGraph raises `GraphRecursionError` (`GRAPH_RECURSION_LIMIT`) when super-steps exceed `recursion_limit`. Default since LangGraph **1.0.6** is **1000**. Historical Pregel/SDK schema still mentions **25**. One ReAct model+tool cycle ≈ **2** super-steps, so 25 ≈ 12 cycles. Deep Agents binds **9,999** ([08](08-deep-agents-harness.md) §2.7). Frontend copy still says **10,000**.

**Why 9,999 not 10,000:** LangGraph `merge_configs` historically **dropped** `recursion_limit` when it equaled `DEFAULT_RECURSION_LIMIT` (10000), so nested graphs fell back to **25**. Issue #7314 (filed 2026-03-27, closed 2026-03-30); PRs #7322 / #7334 attempted to preserve explicit 10000. Do not assume a given LangGraph pin is fixed — Deep Agents still binds 9,999 as of 0.7.x.

**Historical production bug #1698** (`deepagents==0.4.4`, filed 2026-03-07, closed 2026-04-17): `SubAgentMiddleware` invoked children **without** `config`, so children ran at **25**. A legal-doc child hit **exactly 25** steps (10 model + 15 tool / MCP calls), `GraphRecursionError` → `asyncio.Task.cancel()` → `CancelledError` on the parent, and **`asyncio.gather` cancelled the sibling** (`analista`) mid-flight. Parent had `recursion_limit=300`; it had **zero** effect on the child.

Current 0.7.x comment: ambient parent config is merged; child **bound** `recursion_limit` wins; do not double-forward. Re-verify on the pin — #2315 (closed as duplicate of #2362) documented a later regression where `_build_task_tool` again omitted `runtime.config`. Isolation of failures is **not** guaranteed on versions that gather children without `return_exceptions=True`. Frontend per-card errors are a **UI** isolation story, not a runtime guarantee. Sync has **no** mid-flight cancel API.

Proactive: `RemainingSteps` / `config["metadata"]["langgraph_step"]` so the graph can wind down before the wall. Reactive: `except GraphRecursionError` — execution **terminated**, last checkpoint is the failed super-step. `checkpoint_ns`: `""` = parent; `"node_name:uuid"` = subgraph; nested joined with `|`. Deep Agents sync `task` runs as a nested graph under the tools node; 09’s namespace `("tools:<tool_call_id>",)` is the stream handle. If you call subgraphs **inside a node** yourself, LangGraph assigns namespaces by **call order** — wrap each subagent in its own `StateGraph` with a **unique node name**.

#### 2.10 Control-plane checklist (interview fork)

| Goal | Do this | Do not |
| --- | --- | --- |
| No `task` tool | Profile `enabled=False` **and** no sync `subagents=` | `excluded_middleware={"SubAgentMiddleware"}` |
| Keep specialists, drop GP | `enabled=False` **or** replace `name="general-purpose"`; pass only your specs | Assume empty `subagents=` drops GP |
| No dynamic fan-out | `CodeInterpreterMiddleware(subagents=False)` or omit interpreter | Assume `interrupt_on={"task": True}` catches JS `task()` |
| No async | Omit `AsyncSubAgent` specs | Strip `AsyncSubAgentMiddleware` via excluded_middleware unless docs say it is optional (it is **not** in `_REQUIRED_MIDDLEWARE`) |
| Planning UI | `middleware=[TodoListMiddleware()]` | Expect `stream.values.todos` on a v0.7 default agent |
| Structured child → parent | `response_format=` on declarative spec `>=0.5.3` | `responseSchema` on interpreter `task()` targeting a `CompiledSubAgent` |

Best-practice router (canonical page, condensed): action-oriented `description` (the **router**); `task(description=...)` is the **work order** — both must be specific. Child system prompts: tool usage + output format + length cap (docs examples **<500 words**, or **<300 words** and “Do NOT include raw data”). Minimize tool sets. Choose models by task. Large data via VFS (`/data/raw_results.txt`), return analysis only. Instruct the parent to delegate if it otherwise does the work itself. Differentiate overlapping specialists (`quick-researcher` 1–2 searches vs `deep-researcher` multi-search). Anthropic: short orders like “research the semiconductor shortage” caused duplicated searches (one child on 2021 auto chips, two on 2025 supply).

---

### 3. Token Economics & NFR Analysis

> ⚠️ Gap: **No vendor publishes p50/p95/p99** of Deep Agents `task` wall-clock, async `check` RTT, or interpreter `Promise.all` fan-out, nor a LangChain “GP multiplier,” nor harness RPM. Numbers below are unit prices, Anthropic’s published 4×/15× chat multipliers, v0.7 prefix measurements, and **[inferred]** `$ / 1k` / ms from those units and the ReAct-cycle class in [08](08-deep-agents-harness.md) §3.4. Do not treat inferred rows as SKUs or vendor SLOs.

#### 3.1 GP on ≈ +0.8–1.0× (from 08, still valid)

[08](08-deep-agents-harness.md) §2.4 / §3.3: default GP on means one extra isolated tool-calling loop that **re-pays the tool-schema prefix**. For a medium research run (Claude Sonnet 4.6, 10 parent calls, 2k v0.7 cached prefix, GP **disabled**) they inferred **$0.223 / run → $223 / 1k**. One extra isolated **8-call** GP child with the same 2k prefix is roughly **+0.8–1.0×** the main-agent bill on that run **[inferred]**. LangChain does not publish a GP multiplier. Disable GP for short bots.

That multiplier is still the right order of magnitude on 2026-09-02: Sonnet 4.6 list prices are unchanged at **$3 / MTok in**, **$15 / MTok out**, **$3.75 / MTok 5-minute cache write**, **$0.30 / MTok cache read**. Isolation trades parent-context tokens for a **second full prefix**. Prompt caching on the child is a **separate** cache key (different system prompt + often different tools) — do not assume the parent’s cached prefix hits on the child **[inferred]**.

#### 3.2 `$ cost per 1k` — 0 vs 1 vs N children **[inferred]**

Assumptions (same as 08 unless noted):

- Model: `anthropic:claude-sonnet-4-6` at the unit prices above.
- Parent: 10 calls, 2k cached prefix (1 write + 9 reads), 3k uncached in / 800 out per call → **$0.223 / run**.
- Each sync child: 8 calls, own 2k prefix (1 write + 7 reads), 3k uncached in / 800 out per call.
- Child bill: cache write $0.0075 + 7×2k×$0.30/1e6 = $0.0042 + 8×3k×$3/1e6 = $0.072 + 8×800×$15/1e6 = $0.096 → **$0.180 / child**.
- Parent still pays its $0.223 even when it only coordinates (it still emits `task` calls and a synthesis turn). Extra parent synthesis tokens ignored (small vs prefix).

| Topology | Children | USD / run **[inferred]** | USD / 1k runs **[inferred]** | vs 0-child |
| --- | --- | --- | --- | --- |
| GP disabled, no specialists | 0 | $0.223 | **$223** | 1.0× |
| Default GP, one 8-call hop | 1 | $0.223 + $0.180 = $0.403 | **$403** | **1.81×** |
| Parent + 3 parallel specialists | 3 | $0.223 + 3×$0.180 = $0.763 | **$763** | **3.42×** |
| Parent + 5 parallel (Anthropic-like wave) | 5 | $0.223 + 5×$0.180 = $1.123 | **$1,123** | **5.04×** |
| Same 5 without prompt caching on children | 5 | $0.223 + 5×$0.216 = $1.303 | **$1,303** | **5.84×** |

Route a **reviewer** child to Haiku 4.5 ($1 / $5 / MTok in/out; 5-minute cache write $1.25 / MTok; cache read $0.10 / MTok) with the same 8-call shape **[inferred from published unit prices]**:

| Component | Math | USD |
| --- | --- | --- |
| Cache write | 2,000 × $1.25 / 1e6 | $0.0025 |
| Cache reads | 7 × 2,000 × $0.10 / 1e6 | $0.0014 |
| Uncached input | 8 × 3,000 × $1 / 1e6 | $0.0240 |
| Output | 8 × 800 × $5 / 1e6 | $0.0320 |
| **Haiku child / run** | | **$0.060** |

Sonnet parent + 3 Haiku children: $0.223 + 3 × $0.060 = **$0.403 / run → $403 / 1k**, versus **$763 / 1k** if those three children stay on Sonnet. That is the documented “choose models by task” lever.

**Anthropic calibration (not a Deep Agents SKU):** standalone agents ≈ **4×** chat; multi-agent ≈ **15×** chat; a research session can use **millions** of tokens. Moving 0→N Deep Agents children will not magically stay at +0.8×; a 3–5 wave of search-heavy children is the 15× regime. Use 08’s **+0.8–1.0× per isolated 8-call child**, and Anthropic’s **15× as the ceiling when the product is “research everything.”**

#### 3.3 Todo opt-in token tax

Re-enabling `TodoListMiddleware` adds: (1) a system-prompt section, (2) the `write_todos` tool schema on **every** parent (and GP, if the instance is shared) model call, (3) extra model turns to write/update the list, (4) the `todos` channel in checkpoints. LangChain did **not** publish a todo-only token delta.

**[inferred]** If the planning prompt + tool schema are ~**400–800 tokens** of extra prefix (order-of-magnitude) on 10 Sonnet calls with cache: 1× write of +600 at $3.75/MTok + 9× read at $0.30/MTok ≈ $0.00225 + $0.00162 = **$0.0039 / run → $3.90 / 1k** for prefix alone, **plus** 1–3 extra `write_todos` turns (say 500 in + 80 out) ≈ 3×($0.0015+$0.0012) = **$0.008 / run**. Prefix tax is cheap; **the extra turns dominate**. Enable todos for UI progress, not for “completeness,” on Sonnet-class models.

#### 3.4 Latency SLA — p50 / p95 / p99 numeric ms

> ⚠️ Gap: **Deep Agents publishes no p50/p95/p99** of `task`, async `check`, or `eval` fan-out. HITL on a sync child **dominates p99** (human think-time) with no published histogram. ASGI “eliminates network latency” vs HTTP — **no ms figure**.

Clock-split: (a) parent model turn that emits `task` / `start_async_task` / `eval`; (b) child ReAct loops; (c) handoff + parent synthesis; (d) HITL — a **different clock**; (e) async worker-queue delay if `--n-jobs-per-worker` is too small. Middleware assembly is construction-time (**0 ms** on the request path).

Architectural unit: one ReAct cycle (model + tool) **2,000 / 8,000 / 20,000 ms** **[inferred]** — same class as [08](08-deep-agents-harness.md) §3.4. An isolated 8-call child is **8 ×** that cycle if serial inside the child.

| Path | **p50** | **p95** | **p99** | Grounding / mitigation |
| --- | --- | --- | --- | --- |
| **Sync `task`, 8-call child, parent waits** **[inferred]** | **16,000 ms** | **64,000 ms** | **160,000 ms** | 8 × ReAct-cycle. Sync `task` **is** the p99 path: a slow child (MCP, web, 25-step loop) is a slow parent |
| **Sync 3 parallel equal 8-call children** **[inferred]** | **16,000 ms** | **64,000 ms** | **160,000 ms** | Wait ≈ **max** of children + synthesis, **if** gather isolation holds. Serial waves would be ~**sum**. Anthropic: 3–5 parallel + 3+ tools cut research time **up to 90%** vs sequential. LLMCompiler (cite [06](06-agent-feedback-loops.md)): up to **3.7×** latency vs ReAct on ParallelQA — same structural point |
| **Async ASGI launch+ack (child OFF user path)** **[inferred]** | **2,000 ms** | **8,000 ms** | **20,000 ms** | One parent ReAct to emit `start_async_task` and tell the user. Child p99 is a **different clock**. Supervisor polling immediately after launch turns async into blocking |
| **Async HTTP extra vs ASGI on launch/check** **[inferred policy]** | **50 ms** | **200 ms** | **1,000 ms** | Unpublished. Same-region HTTP class on Agent Protocol `POST/GET`. ASGI is the documented way to drop this term. Not a child-completion SLO |
| **Async `check` RTT, ASGI** **[inferred policy]** | **10 ms** | **50 ms** | **200 ms** | Unpublished in-process status fetch. Staleness is usually the **model reporting history** instead of calling `check`, not this RTT |
| **Dynamic `Promise.all` of 3 equal children** **[inferred]** | **16,000 ms** | **64,000 ms** | **160,000 ms** | One `eval` waits on the JS promise — still **blocking that parent turn**. Same child math as sync parallel |
| **Dynamic `while (true)` uncapped** | — | — | **no SLA** | Interpreter has **no hop cap**. Product: put a JS `round` cap. A 10-round serial 8-call loop would be **160,000 / 640,000 / 1,600,000 ms [inferred if capped at 10]** |
| **HITL on a sync child** **[inferred policy from human think-time]** | **30,000 ms** | **180,000 ms** | **600,000 ms** | Seconds–minutes. `interrupt_on` requires a checkpointer; parent run paused until `Command(resume=...)`. p99 = expire → **deny**, not auto-approve. Dominates any model percentile |
| **`GraphRecursionError` / 9,999** | — | — | **hard error** | Fuse, not a degrade. Product cap must fire **earlier** |
| **Construction / `enabled=False` extra vs thin parent** **[inferred]** | **0 ms** | **0 ms** | **0 ms** | Disable path is assembly. Absolute TTFT is still the parent model |

**Mitigations mapped to percentiles:**

- **p50 (user):** disable GP for L1; stream the parent; prefer Haiku (or skip children) when isolation is optional; ASGI for async so launch is not an extra WAN hop; `async` checkpointer on the parent when HITL is off.
- **p95:** parallel `task` in **one** parent message (max not sum); timeout/cancel **async** children independently; cheaper child `model=`; `response_format` so the parent does not re-ask.
- **p99:** HITL off the request thread; never wait on a sync child for “keep chatting”; worker slots ≥ **1 + concurrent children**; JS round cap; `ToolCallLimitMiddleware` on `task`; do not treat 9,999 as an SLO.

#### 3.5 Throughput / back-pressure

Deep Agents publishes **no** harness RPM. Provider TPM/RPM apply **per child model** independently — N parallel Sonnet children are N concurrent streams against the same API key **[inferred]**. Anthropic parallel tool calling is the intended back-pressure valve (the model chooses N); it is **not** a token-bucket in the harness.

| Ceiling | Number | Effect |
| --- | --- | --- |
| Compiled `recursion_limit` | **9,999** | Super-step fuse. Hitting it is `GraphRecursionError` |
| Frontend claimed default | **10,000** | Sentinel risk (`merge_configs` drop) |
| LangGraph default ≥1.0.6 | **1000** | Today’s runtime default |
| Nested-graph ghost / #1698 | **25** | Historical child footgun |
| Async worker example | `--n-jobs-per-worker 10` | Slots; supervisor + 3 children = **4** occupied |
| `write_todos` | **≤1 per model turn** | Parallel calls rejected |
| UI collapse | **5+** subagent cards | Frontend, not a runtime cap |
| Anthropic parallel wave | **3–5** subagents; **3+** tools each | Prompt-level; not encoded |
| Anthropic spawn footgun | **50** children on simple queries | Prompt-level |
| `ToolCallLimitMiddleware` on `task` | **you set** `run_limit` / `thread_limit` | The actual product cap |
| Provider TPM/RPM | account limits | **The** throughput ceiling. N children = N streams **[inferred]** |
| Parallel `task` + `checkpointer=True` child | **unsupported** | Checkpoint namespace collision |

**Back-pressure design:** (1) `ToolCallLimitMiddleware(tool_name="task", run_limit=...)` on the parent — do not ship 9,999 as policy; (2) `ModelCallLimitMiddleware` on each child spec; (3) async **worker slots** ≥ 1 + expected concurrent children; exhausted pool **queues** rather than errors; (4) JS `tickets.slice(0, 50)` (or similar) on dynamic fan-out — models will not reliably emit 200 parallel `task` calls in one turn; (5) circuit on provider 429 so child retries do not amplify tokens; (6) disable GP + interpreter `subagents=False` for L1; (7) bulkhead **parent model** vs **child fleet** vs **async workers**.

#### 3.6 NFRs and explicit trade-offs

| NFR | Production stance | Competes with |
| --- | --- | --- |
| **Availability of chat vs of children** | Product SLO is the parent loop. Sync `task` **couples** child availability to chat p99. Async moves child failure off the turn but adds worker-queue and stale-`check` failure modes. Circuit-open on the child model → **GP off (specialists only) → parent-only** — never unbounded spawn, never 9,999-step retry | Research quality vs user p99 |
| **RPO of parent checkpointer** | Last super-step that committed the `task` `ToolMessage` (and `async_tasks` records). `InMemorySaver` RPO = **empty on restart** | Crash-consistency vs p50 |
| **RPO of sync child** | `checkpointer=None` (default): per-invocation; inherits parent checkpointer so **interrupts work inside one parent call**; fresh messages each `task`. Child channel writes are **not** automatically visible on the parent except the filtered handoff. Crash mid-child: parent sees a failed tool super-step; retry **re-runs** the child (stateless) | Isolation vs resume fidelity |
| **RPO of per-thread child (`True`)** | Accumulates on that thread — **and forbids parallel `task` to the same graph** | Multi-turn child memory vs fan-out |
| **RPO of async child** | Own Agent Protocol thread. Durability = server mode (`sync` / `async` / `exit`). Supervisor `async_tasks` survives parent summarization; the handle is not the child’s bytes | Independent scale vs split-brain on rainbow deploys (supervisor mid-`check` and child mid-run may sit on different graph versions — pin `assistant_id` / graph ids; Anthropic: keep old and new versions running) |
| **RTO of parent** | Resume `thread_id`. Replay re-executes nodes after that checkpoint | Time-to-resume vs forensic truth |
| **RTO of sync child** | Re-invoke `task` (stateless). HITL: `Command(resume=...)` on the **parent** result |
| **RTO of async child** | `check` the thread; `update` interrupts; `cancel` is `runs.cancel()`. Example server **clears state** on update — RTO may be “new run,” not “continue” | Mid-flight steering vs memory |
| **RPO of traces** | Child traces are **longer-lived evidence** than the parent `ToolMessage`. Sampled LangSmith is lossy by policy | Debug vs PII store (a 30-day window on a copilot that fetched HR tickets **is** a PII store) |
| **Compliance** | **Not provided by `deepagents`.** No SOC2/HIPAA certification is claimed. Quarantine ≠ redaction. GDPR erasure of a delegated turn is parent checkpoint **+** child traces **+** async thread **+** shared VFS, not `thread_id` TTL | Time-to-debug vs residency |
| **Correctness vs $** | Anthropic paid **15×** chat for **90.2%** research lift and still said don’t multi-agent when work isn’t parallelizable. Todos **on**: higher cost, no accuracy on capable models. GP on is +0.8–1.0× per 8-call child **[inferred]** | Schema/prefix tax vs isolation |

**RPO/RTO [inferred from architecture, not vendor RPO SKUs]:** RPO_parent = last durable super-step. RPO_sync_child_default = none independent of parent (ephemeral invoke). RPO_async = last durable run persist on that thread. RTO_sync_child = re-run. RTO_async = check/cancel/update on thread ID. A `GraphRecursionError` is a **completed refuse**, not an RPO hole — log it. Files on shared StateBackend **outlive** the child handoff ([09](09-deep-agents-execution.md)).

---

### 4. Distributed Resilience & Security

#### 4.1 Durable execution: child vs parent checkpointer; sibling cancel (#1698)

| `checkpointer=` on child `.compile()` | Behavior | Parallel `task`? |
| --- | --- | --- |
| `None` (default) | Per-invocation. Inherits parent checkpointer so **interrupts and durable execution work inside one parent call**. Fresh messages each `task` (Deep Agents also overwrites `messages` with the description) | Yes — namespaces are per call |
| `True` | Per-thread. State accumulates across calls on the same thread | **No** — parallel tool calls conflict on the same `checkpoint_ns` |
| `False` | No child checkpoints. Interrupts inside the child will not resume cleanly | N/A |

Parent state does **not** automatically see child channel writes. Deep Agents copies a **filtered** snapshot in and a **filtered** snapshot out. HITL: **always** pass a checkpointer on the parent if any `interrupt_on` is set (parent or declarative child).

Async children **are** their own threads. Cancel is `runs.cancel()` on that thread, not a parent super-step abort. Sync has no mid-flight cancel.

**Sibling cancel chain (#1698):** child `GraphRecursionError` → task cancel → parent `CancelledError` → `asyncio.gather` tears down siblings. Verify gather semantics on the pinned version; cap `task` fan-out in application state. Frontend: show the error **on that subagent card** while others continue — UI, not runtime.

Rainbow deploys: you cannot update every in-flight agent at once. Same constraint for Deep Agents on LangSmith Deployments.

#### 4.2 Failure taxonomy

| Class | Examples | Detection | Handling |
| --- | --- | --- | --- |
| **Transient** | Provider 429/5xx on parent or child; async worker-queue delay; HTTP Agent Protocol blip; checkpointer blip during child interrupt | Error rate; hung `start_async_task`; p99 wait on sync `task` | Full-jitter retries on **idempotent** reads / `check`. **Do not** blindly retry `task` (each call is a new child bill). Do not retry `update` without knowing the server clears state |
| **Permanent** | `ValueError` at construction (`excluded_middleware` SubAgent; compiled graph missing `messages`; `response_schema` on compiled); unknown `subagent_type` (string error); `GraphRecursionError`; 4xx auth on remote `url` | Non-retryable / construction exception | Fail to next fallback (GP off → parent-only). Never “strip SubAgentMiddleware and retry” |
| **Poison-pill infinite `task` spawn** | Default GP always-on; Anthropic-style 50 children; dynamic `Promise.all` over `glob` of a large repo; async `start_async_task` in a loop (queues, does not error) | Token ledger; `lc_agent_name` cardinality; worker-slot exhaustion | `ToolCallLimitMiddleware` on `task`; JS N cap; disable GP; `CodeInterpreterMiddleware(subagents=False)`; HITL on `eval`; application `max_subagent_calls` |
| **Poison-pill child ReAct** | Child never stops tools; MCP-heavy legal child (#1698: `read_documento ×5`, greps, stats) until the wall | Child step count; `GraphRecursionError` | `ModelCallLimitMiddleware` on the **child spec**. 9,999 without this cap is a **token bomb** |
| **Poison-pill interpreter loop** | `while (true)` until `fresh` is empty; `responseSchema` items always get a new `id`; `mode="thread"` persists the poisoned set | Eval wall-clock; repeated identical `task()` | JS round cap; gate `eval`; `mode="turn"` if you do not want cross-turn JS memory |
| **Poison-pill async restart** | Supervisor keeps `update`ing; terminal cache on `list` does not stop **new** launches | Interrupt-restart loop on one thread ID | Cap updates; prompt against busy-loop `update`; cancel |
| **Poison-pill context leak** | Child returns raw dumps; `raw: str` schema; experimental `fork` | Parent window bloat; PII in parent checkpoint | Length cap in child prompt; VFS for blobs; small `response_format`; do not use `fork` as default |
| **Idempotency** | Two `task` calls on resume; async `update` after a success the parent did not `check`; HITL approve then mutated args | Duplicate child bills; lost mid-flight memory on servers that clear state | Idempotency key in application state (`spawn_id`); `check` before `update`; re-hash HITL args at execute (app-level; **gap** in harness). `task` itself is **not** idempotent |
| **Fan-out cancellation** | #1698 gather cancels siblings | Sibling `CancelledError` | Pin + verify `return_exceptions`; async cancel is per-task |
| **Denial of wallet** | Accidental GP; todos on; 9,999-step child; dynamic 200 reviewers | $ / 1k vs budget | Fallback chain in §4.4; product caps |

#### 4.3 Circuit breaker closed → open → half-open (task tool / child model)

> ⚠️ Gap: **`deepagents` does not ship circuit breakers, leader election, or a token-bucket around `task`.** What exists: `recursion_limit` / `GraphRecursionError` (hard stop); `ModelCallLimitMiddleware` / `ToolCallLimitMiddleware`; async worker pool as an implicit queue; provider RPM/TPM **[inferred]**; Anthropic: “the published architecture has no circuit breakers or per-run caps” for Research — same honesty applies to default Deep Agents.

Independent breakers: **parent model**, **`task` tool / child fleet**, **child model tier** (Sonnet gatherer vs Haiku reviewer), **async worker pool**, **interpreter `eval`**. A child 429 must not stall a short L1 chat (**bulkhead**) **and** must not `excluded_middleware` SubAgent.

```
        child 429/5xx | task error-rate window | worker pool 503 | GraphRecursionError storm
  ┌──────────┐  ─────────────────────────────────────────────────►  ┌──────────┐
  │  CLOSED  │                                                       │   OPEN   │
  │  task()  │  success resets consecutive count                     │ FAIL FAST│
  └────┬─────┘                                                       │ fallback │
       ▲                                                             │ chain    │
       │ probe OK                                                    └────┬─────┘
       │                                                                  │ cooldown
       │                                                            ┌─────▼──────┐
       └──────────── probe allow ───────────────────────────────────│ HALF-OPEN  │
                    probe fail → stay OPEN                          │ 1 specialist│
                                                                    │ probe; stay│
                                                                    │ OPEN if fail│
                                                                    └────────────┘
```

**Thresholds [policy, not vendor SLO]:**

| Trip condition | Closed → open | Half-open probe | Fallback (**never unbounded spawn**) |
| --- | --- | --- | --- |
| Child model 429/5xx | consecutive ≥ **5** or error-rate window | One tiny specialist `task` (not GP) | **GP on → GP off (specialists only) → parent-only** |
| `task` storm / spawn rate | `run_limit` exceeded or N > product cap | Refuse further `task` this turn | Parent-only for this turn; do not raise 9,999 |
| Async worker pool exhausted | allocate/queue timeout | One `start_async_task` | Stay on sync specialists or parent-only — **do not** grow `--n-jobs-per-worker` unbounded |
| Interpreter `eval` timeout / loop | wall-clock cap | One `eval` with `subagents=False` | Force normal `task` path or parent-only; HITL on `eval` |
| Parent model 429 | consecutive ≥ **5** | One parent invoke, GP off | Deterministic refuse (no children to spawn) |

**Fallback chain (required interview answer):** **GP on (default isolation) → GP off / specialists only → parent-only (`enabled=False` + no sync specs).** Never: child 429 → unbounded `Promise.all`. Never: HITL timeout → auto-approve. Never: circuit open → `excluded_middleware` SubAgent. Never: sandbox/MCP outage on a child → inherit parent `execute` + full MCP onto a reviewer.

#### 4.4 Zero-Trust MCP on children; tool-level RBAC; spawn audit

MCP tools are ordinary `BaseTool` objects on `tools=`. There is **no** MCP-specific subagent middleware. Inheritance is the declarative `tools` rule: omit ⇒ **full parent MCP set**; specify ⇒ **only** that list (MCP is gone unless re-listed). `permissions=` is first-match-wins, **no match ⇒ allow** (fail-open), and applies only to **built-in FS tools** — not MCP, not custom tools, **not sandbox `execute`**. Composite + sandbox: permission paths must sit on a **known route prefix** or construction raises `NotImplementedError`.

Zero-Trust MCP (mTLS, OAuth, per-tool gateway headers, audience-bound tokens, hash-pin of tool JSON) is **unchanged** from [07-guardrails](07-guardrails.md). Children do not get a second PEP for free:

| Child form | MCP / tool surface | What Zero-Trust must do |
| --- | --- | --- |
| **Declarative, `tools` omitted** | **Inherits** parent `BaseTool` objects — same gateway headers, same OAuth client, same mTLS | Parent PEP is the child PEP. Still fail-open FS globs do **not** constrain those MCP tools. Minimize by **replacing** `tools` |
| **Declarative, `tools` set** | **Replace entirely** — narrower RBAC. Empty list = no inherited tools | Re-list only the MCP tools that specialist needs (`send_email` + `validate_email`, not web + DB). Issue #1698: MCP on a child is a **privilege and a loop amplifier** |
| **GP (auto)** | Same tools as parent (FS + caller `tools=`, including MCP) | Widest child. Disable GP on L1. Do not give GP `execute` if the parent has a sandbox |
| **Compiled / async** | **Do not inherit** parent tools, HITL, or permissions | Configure MCP/OAuth/headers on **that** runnable / remote graph. Parent gateway does **not** automatically wrap a remote Agent Protocol worker. Async HTTP `headers` are **service** auth to the server, not end-user identity inside the prompt |
| **Dynamic `task()`** | Uses the **configured spec’s** tools | `Promise.all` N children ⇒ N times that spec’s MCP surface, **without** per-dispatch parent HITL. Gate `eval`. PTC off by default does **not** disable this |

**Tool-level RBAC (what exists):**

| Control | What it is | What it is not |
| --- | --- | --- |
| Child `tools=` replace | Least-privilege specialist allowlist | A gateway PEP |
| Child `permissions=` replace | Path glob PDP for **built-in FS only**; auditor pattern: parent allow `/workspace/**`; child deny all writes, allow read `/workspace/**`, deny other reads | MCP / `execute` / custom |
| `interrupt_on` inherit (declarative/GP only) | Review queue; child can require approval on `read_file` even if parent does not | AuthZ. Compiled/async/dynamic-eval do **not** inherit |
| `lc_agent_name` | Harness-stamped spec name; branch `strict_lookup` vs general | End-user identity |
| `runtime.context` / `context_schema` | **Forwarded** to every subagent tool — children **can** AuthZ correctly if tools read it | Authn. Do not let `task` JSON carry `user_id` as source of truth (same rule as [07-guardrails](07-guardrails.md); Deep Agents’ contribution is that **context is forwarded**) |
| `excluded_tools` / FS allowlist | Parent allowlist does **not** apply to a declarative spec — put `FilesystemMiddleware(tools=...)` on **this** spec | Per-user roles |
| Gateway PEP | **The** MCP control (07): authenticated transport, no token passthrough, hash-pin `tools/list`, identity from verified token | Not in `deepagents`. Inherited only insofar as you passed already-wrapped `BaseTool`s |

Sandbox `execute` on a child that inherited parent tools is a **privilege escalation** relative to a read-only auditor spec. Replace `tools` and `permissions` on that spec. Anthropic: bad MCP descriptions sent agents down wrong paths; a tool-testing agent rewriting descriptions cut later task time **40%**.

**Immutable logs of `task` spawn (you build):** Deep Agents stamps `lc_agent_name` / `ls_agent_type="subagent"` on LangSmith runs; it does **not** ship WORM. Log **decisions**, not prompts: `correlation_id`, `thread_id`, spec name, `tool_call_id`, `description` **digest**, `spawn_ts`, parent `checkpoint_id`, async `thread_id`/`run_id` if any. A `task` call without an audit row is a control-plane bug. Retention: security evidence **and** a sensitive-data asset.

#### 4.5 PII pipeline — detect → redact → audit (child traces **and** handoff reports)

Quarantine is a **context** win, not a **compliance** win. Child traces contain the **full** intermediate tool dumps the parent never sees. PII in a web page the child fetched is in LangSmith unless you redact. Returning raw dumps in the final `ToolMessage` **re-injects** PII into the parent transcript, parent checkpoint, parent LangSmith trace, and any downstream synthesizer. Anthropic Research aimed at high-level observability of decision patterns **without** monitoring conversation contents; Deep Agents’ default is the opposite (full child traces). Configure LangSmith data retention / PII settings at the workspace, **and** `PIIMiddleware` on the **child** `middleware` list — declarative children do **not** inherit parent extras. Strategies: `block` / `redact` / `mask` / `hash`; check human, AI, and tool-result messages; `langchain>=1.3.2` also redacts streamed wire output.

**Pipeline (explicit) — run before persist on child traces, VFS writes, and the parent handoff:**

1. **Detection (control plane, before bytes leave the trust boundary).** Dual-gate: **regex** (email, PAN, SSN, phones) + **ML NER** if you have a scanner. Scan: `task(description=...)` (user-influenced → attacker-influenced), child tool args/results, child final report / `structured_response` JSON, parent `ToolMessage`, LangSmith payloads, `todos` titles if planning is on, HITL UI. If ML is down: **fail closed to mask** on user-facing chat; **fail closed (block)** on child MCP args / sandbox env / VFS writes — do not send raw PAN to a third-party MCP the child inherited.
2. **Redaction.** `redact` / `mask` / `hash` to stable tokens (`[EMAIL_<hash12>]`) so the parent can still synthesize; `block` when the field must not exist (secrets paths, MCP args). Strip the value from the **child trace** **and** from the **handoff**. Do **not** persist raw PAN in sampled APM and call it this step. Keep `response_format` small so there is no `raw` kitchen sink to redact after the fact.
3. **Audit trail (WORM, immutable logs).** Log **decisions**, not values: `content_sha256` pre- and post-redact, entity **types** + counts, action (`redact` / `mask` / `hash` / `block-from-handoff` / `block-from-child-trace`), detector (`regex` | `pii-middleware` | `gateway`), `correlation_id`, `tenant`, parent `thread_id`, child `lc_agent_name`, `ls_agent_type=subagent`, async child `thread_id` if any. Chain-of-custody: parent `checkpoint_id` + child run id + arg digest — **not** “LangSmith has the child prompt so we are SOX-ready.” GDPR erasure vs legal hold is digest-level.

`todos` in checkpoints may contain PII from task titles — another reason todos are a UI feature, not a default.

---

### 5. Production Enterprise Code

Self-contained. Optional `deepagents` / `langchain` imports. Stdlib path runs the same control flow: retries + full jitter, circuit breaker on the **task/child** path, fallback **GP on → GP off (specialists only) → parent-only**, `task` call limits, spawn audit, PII detect→redact→audit, structured logs with correlation IDs. Run: `python deep_agents_delegation.py`.

```python
#!/usr/bin/env python3
"""Delegation runtime: task caps + GP-on → GP-off → parent-only fallback.

Optional (not required to run this file):
    from deepagents import create_deep_agent, GeneralPurposeSubagentProfile
    from langchain.agents.middleware import ToolCallLimitMiddleware, TodoListMiddleware
    from langgraph.checkpoint.memory import InMemorySaver

Run: python deep_agents_delegation.py
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

# --- structured logging ----------------------------------------------------

class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for k, d in (
            ("correlation_id", "-"),
            ("tenant_id", "-"),
            ("thread_id", "-"),
            ("mode", "-"),
            ("lc_agent_name", "-"),
        ):
            setattr(record, k, getattr(record, k, d))
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("da_delegation")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"cid":"%(correlation_id)s","tenant":"%(tenant_id)s",'
            '"thread":"%(thread_id)s","mode":"%(mode)s",'
            '"agent":"%(lc_agent_name)s","msg":"%(message)s"}'
        )
    )
    handler.addFilter(CorrelationFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


LOG = configure_logging()


def slog(level: int, msg: str, **extra: Any) -> None:
    LOG.log(level, msg, extra=extra)


# --- retries + full jitter -------------------------------------------------

def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    base_s: float = 0.2,
    cap_s: float = 2.0,
    retryable: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError),
) -> Any:
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except retryable as exc:
            last = exc
            if i == attempts - 1:
                break
            sleep_s = random.random() * min(cap_s, base_s * (2**i))
            slog(logging.WARNING, f"retry_backoff attempt={i+1} sleep_s={sleep_s:.3f}")
            time.sleep(sleep_s)
    assert last is not None
    raise last


# --- circuit breaker closed → open → half-open -----------------------------

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    pass


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    cooldown_s: float = 30.0
    half_open_probes: int = 1
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _probes_used: int = 0

    def allow(self) -> None:
        if self._state is CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = CircuitState.HALF_OPEN
                self._probes_used = 0
            else:
                raise CircuitOpenError(f"circuit_open:{self.name}")
        if self._state is CircuitState.HALF_OPEN:
            if self._probes_used >= self.half_open_probes:
                raise CircuitOpenError(f"circuit_half_open_busy:{self.name}")
            self._probes_used += 1

    def record_success(self) -> None:
        self._failures = 0
        self._state = CircuitState.CLOSED
        self._probes_used = 0

    def record_failure(self) -> None:
        self._failures += 1
        if self._state is CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            return
        if self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()


# --- PII: detect → redact → audit ------------------------------------------

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def pii_detect_redact_audit(
    text: str,
    *,
    audit: list[dict[str, Any]],
    correlation_id: str,
    tenant_id: str,
    sink: str,
    lc_agent_name: str,
    block_on_pan: bool = True,
) -> str:
    kinds: list[str] = []
    if EMAIL_RE.search(text):
        kinds.append("email")
    if PAN_RE.search(text):
        kinds.append("pan")
    pre = _sha(text)
    if "pan" in kinds and block_on_pan and sink in {"mcp_args", "child_vfs", "handoff_block"}:
        audit.append(
            {
                "cid": correlation_id, "tenant": tenant_id, "sink": sink,
                "agent": lc_agent_name, "kinds": kinds, "action": "block",
                "pre": pre, "post": _sha(""), "detector": "regex",
            }
        )
        raise PermissionError(f"pii_block:{sink}:pan")
    redacted = EMAIL_RE.sub(
        lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]",
        text,
    )
    redacted = PAN_RE.sub("[PAN]", redacted)
    action = "redact" if redacted != text else "allow"
    audit.append(
        {
            "cid": correlation_id, "tenant": tenant_id, "sink": sink,
            "agent": lc_agent_name, "kinds": kinds, "action": action,
            "pre": pre, "post": _sha(redacted), "detector": "regex",
        }
    )
    return redacted


# --- task-call limiter + spawn WORM ----------------------------------------

class TaskLimitExceeded(RuntimeError):
    pass


@dataclass
class TaskCallLimiter:
    run_limit: int
    thread_limit: int
    _run: int = 0
    _thread: int = 0

    def reset_run(self) -> None:
        self._run = 0

    def check_and_incr(self) -> None:
        if self._run >= self.run_limit or self._thread >= self.thread_limit:
            raise TaskLimitExceeded(
                f"task_cap run={self._run}/{self.run_limit} thread={self._thread}/{self.thread_limit}"
            )
        self._run += 1
        self._thread += 1


class InvokeError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class Mode(str, Enum):
    GP_ON = "gp_on"
    SPECIALISTS_ONLY = "specialists_only"
    PARENT_ONLY = "parent_only"


ALLOWED: dict[Mode, frozenset[str]] = {
    Mode.GP_ON: frozenset({"general-purpose", "reviewer"}),
    Mode.SPECIALISTS_ONLY: frozenset({"reviewer"}),
    Mode.PARENT_ONLY: frozenset(),
}


@dataclass
class ScriptedGraph:
    """Stdlib stand-in. fail_kind simulates child-model / task-tool faults."""

    fail_kind: str | None = None

    def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> str:
        if self.fail_kind == "transient":
            raise InvokeError("transient", "child_provider_429")
        if self.fail_kind == "permanent":
            raise InvokeError("permanent", "graph_recursion")
        user = payload.get("user") or ""
        children = payload.get("wanted_children") or []
        return f"ok:{config.get('mode')}:n={len(children)}:{user[:60]}"


@dataclass
class DelegationRuntime:
    graph: ScriptedGraph
    task_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("task_child"))
    limiter: TaskCallLimiter = field(default_factory=lambda: TaskCallLimiter(run_limit=8, thread_limit=24))
    audit: list[dict[str, Any]] = field(default_factory=list)
    spawn_log: list[dict[str, Any]] = field(default_factory=list)  # immutable append-only

    def _spawn_row(self, *, cid: str, tenant: str, thread_id: str, spec: str, description: str) -> None:
        self.spawn_log.append(
            {
                "cid": cid,
                "tenant": tenant,
                "thread_id": thread_id,
                "ls_agent_type": "subagent",
                "lc_agent_name": spec,
                "desc_digest": _sha(description),
                "ts": time.time(),
            }
        )

    def run(
        self,
        user_text: str,
        *,
        tenant_id: str,
        thread_id: str,
        wanted_children: list[str],
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        cid = correlation_id or str(uuid.uuid4())
        extra = {"correlation_id": cid, "tenant_id": tenant_id, "thread_id": thread_id}
        self.limiter.reset_run()
        safe_user = pii_detect_redact_audit(
            user_text, audit=self.audit, correlation_id=cid, tenant_id=tenant_id,
            sink="model_input", lc_agent_name="parent", block_on_pan=False,
        )

        last_exc: BaseException | None = None
        for mode in (Mode.GP_ON, Mode.SPECIALISTS_ONLY, Mode.PARENT_ONLY):
            extra["mode"] = mode.value
            allowed = ALLOWED[mode]
            planned = [n for n in wanted_children if n in allowed]
            slog(logging.INFO, f"try_mode planned={planned}", **extra)
            try:
                self.task_breaker.allow()
                if mode is Mode.PARENT_ONLY:
                    planned = []
                for spec in planned:
                    self.limiter.check_and_incr()
                    self._spawn_row(
                        cid=cid, tenant=tenant_id, thread_id=thread_id,
                        spec=spec, description=safe_user,
                    )
                    extra["lc_agent_name"] = spec
                    slog(logging.INFO, "task_spawn", **extra)

                def _once() -> str:
                    return self.graph.invoke(
                        {"user": safe_user, "wanted_children": planned},
                        {"configurable": {"thread_id": thread_id}, "mode": mode.value, "metadata": {"cid": cid}},
                    )

                text = retry_call(_once)
                text = pii_detect_redact_audit(
                    text, audit=self.audit, correlation_id=cid, tenant_id=tenant_id,
                    sink="handoff_report", lc_agent_name=planned[-1] if planned else "parent",
                    block_on_pan=False,
                )
                self.task_breaker.record_success()
                slog(logging.INFO, "invoke_ok", **extra)
                return {"text": text, "mode": mode.value, "task_calls": len(planned), "degraded": mode is not Mode.GP_ON}
            except TaskLimitExceeded as exc:
                slog(logging.ERROR, f"task_cap:{exc}", **extra)
                last_exc = exc
                break
            except CircuitOpenError as exc:
                slog(logging.WARNING, str(exc), **extra)
                last_exc = exc
                continue
            except InvokeError as exc:
                if exc.kind == "transient":
                    self.task_breaker.record_failure()
                slog(logging.ERROR, f"invoke_fail:{exc.kind}", **extra)
                last_exc = exc
                continue
            except (TimeoutError, ConnectionError) as exc:
                self.task_breaker.record_failure()
                last_exc = exc
                continue
        slog(logging.ERROR, "fallback_exhausted", **extra)
        return {
            "text": json.dumps({"status": "refused", "reason": type(last_exc).__name__ if last_exc else "unknown"}),
            "mode": "refuse",
            "task_calls": 0,
            "degraded": True,
        }


if __name__ == "__main__":
    rt = DelegationRuntime(graph=ScriptedGraph())
    r1 = rt.run(
        "Research ACME for ada@example.com",
        tenant_id="acme",
        thread_id="t-1",
        wanted_children=["general-purpose", "reviewer"],
        correlation_id="cid-1",
    )
    print(r1)
    assert r1["mode"] == "gp_on" and r1["task_calls"] == 2
    assert "[EMAIL_" in r1["text"] or "ada@" not in r1["text"]
    assert any(row["lc_agent_name"] == "general-purpose" for row in rt.spawn_log)
    assert any(row["action"] in {"redact", "allow"} for row in rt.audit)

    rt.graph = ScriptedGraph(fail_kind="transient")
    rt.task_breaker = CircuitBreaker("task_child", failure_threshold=1, cooldown_s=60)
    r2 = rt.run("hello", tenant_id="acme", thread_id="t-2", wanted_children=["general-purpose"], correlation_id="cid-2")
    print(r2)
    assert r2["degraded"] is True
    assert r2["mode"] in {"specialists_only", "parent_only", "refuse"}

    rt.graph = ScriptedGraph()
    rt.task_breaker = CircuitBreaker("task_child")  # reset so the cap, not the breaker, fires
    rt.limiter = TaskCallLimiter(run_limit=1, thread_limit=1)
    rt.limiter._thread = 1  # already at thread cap
    r3 = rt.run("hello", tenant_id="acme", thread_id="t-3", wanted_children=["reviewer"], correlation_id="cid-3")
    print(r3)
    assert r3["mode"] == "refuse"
    print("ok", len(rt.audit), "pii rows", len(rt.spawn_log), "spawn rows")
```

**Wiring notes (not in the script):** production `create_deep_agent` should pass a real checkpointer if any `interrupt_on` is set; `ToolCallLimitMiddleware(tool_name="task", run_limit=…)` on the **parent**; `ModelCallLimitMiddleware` on each child spec; `PIIMiddleware` on **child** `middleware=` (not inherited); reviewer `tools` **replace** (no MCP, no `execute`); `permissions` deny-writes; `GeneralPurposeSubagentProfile(enabled=False)` + empty sync specs for L1; `CodeInterpreterMiddleware(subagents=False)` unless you intend JS fan-out; async worker slots ≥ 1 + concurrent children; `ainvoke` for ASGI. Do not `excluded_middleware` SubAgent. Pin `deepagents==0.7.12` (or current) and re-verify config forwarding (#2315/#2362).

---

### 6. Architectural System Design Scenarios

#### Scenario A — Research copilot: GP + specialist reviewer

**Problem.** A strategy team wants an internal research copilot over Confluence, tickets, and the public web. Work is breadth-first: gather sources, then **verify citations / policy** before a brief reaches a VP. Traffic can grow toward LangChain-GTM-shaped load later; queries range from “what’s the weather” (must stay cheap) to due-diligence (Anthropic’s **15× chat / millions of tokens** regime). Security: no shell on the reviewer, no inherited MCP write tools, identity from `context_schema`, HITL on writes to `/memories/**`. Platform debate: default GP only; GP + read-only reviewer with `response_format=Findings`; dual adversarial reviewers + judge via dynamic `Promise.all`.

**Proposed architecture (recommended: parent coordinates, GP or cheap gatherers fan out, Haiku reviewer is a sync gate):**

```
  ┌─────────┐   ┌─────────────────────────────────────────────────────────┐
  │ IdP/PEP │──▶│ CONTROL: create_deep_agent  GP ON (or cheap research-   │
  │ JWT →   │   │   agent spec). ToolCallLimitMiddleware(tool_name=task)  │
  │ user_id │   │   reviewer: tools⊆{read_file,ls,glob,grep}+cite         │
  │ in      │   │   permissions REPLACE: deny writes; read /workspace/**  │
  │ context │   │   response_format=Findings  interrupt_on /memories/**   │
  │         │   │   ModelCallLimitMiddleware on each child spec           │
  │         │   │   PII detect→redact→audit on child traces + handoff     │
  │         │   │   todos ON only because the UI is a progress dashboard  │
  └─────────┘   └──────────────────────────┬──────────────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────────────┐
                    │ DATA: parent Sonnet/Opus synthesizes                 │
                    │   parallel task() gatherers (GP or research-agent)   │
                    │   sync task(reviewer) on the critical path           │
                    │   StateBackend scratch shared; reports are summaries │
                    │   stream.subagents cards; collapse at 5+     [09]    │
                    │   LangSmith filter lc_agent_name=reviewer            │
                    └──────────────────────────────────────────────────────┘
```

**Technology choices:** maps Anthropic’s 90.2% vs single Opus / 3–5 parallel workers / citation agent as a separate pass onto Deep Agents `task` + structured output — not a closed Research product. Reviewer stays **sync** so synthesis cannot proceed on partials. Optional async variant: gatherers as `AsyncSubAgent` so the user can keep chatting; worker pool ≥ 1 + N gatherers. Adversarial verification (two independent reviewer specs + judge `response_format=Verdict`) only when an oracle or citation checker exists — same rule as [06](06-agent-feedback-loops.md). Do **not** let the judge call `execute`. Instruct the orchestrator: objective, output format, tools/sources, stop boundary — `description` is the router; `task(description=...)` is the work order.

**Cost envelope [inferred from §3.2]:** 1 parent + 3 Sonnet gatherers + 1 Haiku reviewer ≈ $0.223 + 3×$0.180 + $0.060 ≈ **$0.82 / run → ~$820 / 1k** at the 8-call-child toy size. Real research is **dollars per query**, not cents. Not for “what’s the weather.”

**Trade-off matrix:**

| Axis | **A1 GP + Haiku reviewer (recommended)** | **A2 Parent-only (GP disabled, no specialists)** | **A3 Dual reviewers + judge (`Promise.all`)** |
| --- | --- | --- | --- |
| **Cost** | **[inferred] ~$820 / 1k** toy 8-call; real sessions → Anthropic **15×** chat | **[inferred] $223 / 1k** same parent shape; window fills; dumps stay on the parent | +2 × child bill on the critical path vs A1; JS N unbounded unless capped |
| **Latency** | Parent waits **max** gatherers then reviewer: **16,000 / 64,000 / 160,000 ms [inferred]** per 8-call wave + reviewer hop. HITL **30,000 / 180,000 / 600,000 ms [inferred]** if memories writes interrupt | Lowest p99 you can get from one model (**2,000 / 8,000 / 20,000 ms [inferred]** per cycle). Official when-not: overhead outweighs isolation | One `eval` blocks on `Promise.all` + judge; **same wait class** as parallel sync **plus** HITL hole (`interrupt_on` skipped per JS `task()`) |
| **Ops complexity** | Medium: two specs, allowlists, `response_format`, LangSmith views per `lc_agent_name` | Lowest | High: beta interpreter, PTC allowlist, `eval` HITL, round caps |
| **Security** | **Best** least-privilege on reviewer (`tools` replace + `permissions` replace + HITL inherit). GP still widest gatherer — constrain GP or replace it with `research-agent` | Smallest tool surface; no child MCP amplifier | Independent prompts help false-positive cost; **eval bypasses parent HITL**; do not give judge `execute` |
| **Scalability** | N concurrent streams on one API key; `run_limit` on `task`; no worker pool unless async gatherers | Vertical: one TPM pool | N limited by JS + TPM, not by model-issued tool arity — **cap in JS** |

**Decision.** **A1 wins** for due diligence / competitive intel. A2 is the correct answer for short Q&A (and for any ticket that needs the intermediate evidence in the parent window). A3 is a **higher-assurance add-on** when false positives are costly and an oracle exists — not the default copilot. If gatherers are long-running, move **only** gatherers to async; keep the reviewer sync.

#### Scenario B — Support agent: no-subagents vs fan-out

**Problem.** L1 password resets and refund HITL must stay on one conversation state. Overnight, a backoffice queue of ~**200** tickets needs classify-and-act (`bug-fixer` / `feature-analyst` / `support-agent`). Accidental default GP on “reset my password” doubles tokens and hides the tool trace from the agent-assist UI. Debate: no-subagents profile; three declarative specialists + parallel `task`; dynamic classify-and-act `Promise.all`.

**Proposed architecture (recommended default = L1 parent-only; L2 batch = dynamic with a hard N cap):**

```
  ┌─────────┐   ┌─────────────────────────────────────────────────────────┐
  │ IdP/PEP │──▶│ CONTROL L1: GeneralPurposeSubagentProfile(enabled=False)│
  │ JWT     │   │   no sync subagents=   no interpreter (or subagents=   │
  │         │   │   False)  HITL on refund / delete_customer              │
  │         │   │   PIIMiddleware on the PARENT (no child list to inherit)│
  │         │   │ CONTROL L2 batch (separate graph / profile):            │
  │         │   │   CodeInterpreterMiddleware + three Haiku specialists   │
  │         │   │   JS tickets.slice(0, 50) per eval   gate eval HITL     │
  └─────────┘   └──────────────────────────┬──────────────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────────────┐
                    │ L1 DATA: single ReAct; refund HITL stays on parent   │
                    │ L2 DATA: classify→Promise.all specialists→synthesize │
                    │   per-ticket child; parent only writes JS + summary  │
                    │ Do NOT compile per-thread children + parallel task   │
                    └──────────────────────────────────────────────────────┘
```

**Support fan-out numbers [inferred]:** 200-ticket overnight batch, three Haiku specialists, ~8 calls each, one specialist per ticket: 200 × $0.060 ≈ **$12** in child tokens, plus parent `eval` turns. Same batch on Sonnet children ≈ 200 × $0.180 = **$36**. Sync `task` in a loop (no interpreter) would serialize or depend on the parent emitting 200 parallel tool calls in one turn — models will not reliably do that; dynamic `Promise.all` is why this topology exists. Cap N or you will blow TPM.

**Trade-off matrix:**

| Axis | **B1 No subagents (recommended L1)** | **B2 Declarative specialists + parallel `task`** | **B3 Dynamic classify-and-act (L2 batch)** |
| --- | --- | --- | --- |
| **Cost** | **1.0×** parent; GP’s +0.8–1.0× **never happens** | **[inferred] ~1 + 0.8N ×** similar-sized child; Haiku drops that | Unbounded N × child unless `slice`; RLM paper: comparable/$ vs compaction at 10M+ tokens — not this ticket shape |
| **Latency** | One model chain — lowest p99 | Parent waits on **max** of parallel `task` (**16,000 / 64,000 / 160,000 ms [inferred]** per 8-call child). HITL refund on a **child** is the wrong clock | One `eval` waits on `Promise.all`; still blocking that turn; overnight batch can hide it |
| **Ops complexity** | Lowest | Medium: prompts, allowlists, parent “delegate via `task()`” | Medium-high: beta interpreter, PTC, `eval` HITL, mixed-profile fleets (Codex still injects todos) |
| **Security** | Smallest tool surface; HITL on refund stays on the parent; `PIIMiddleware` on parent | Least-privilege per specialist **if** `tools` replace. Default GP must stay **off** | **`interrupt_on` bypass** per JS `task()`; PTC off by default but dynamic subagents **on** by default if interpreter present |
| **Scalability** | Vertical: one TPM pool | N concurrent streams; models will not emit 200 parallel `task`s | Horizontal for **stateless** tickets; **wrong** when tickets share refund HITL / one conversation. Per-thread compiled children + parallel `task` **corrupt checkpoints** |

**Decision.** **B1 wins for L1** — official when-not: overhead outweighs benefits; this is the correct profile for short tool-calling bots. **B3 wins for overnight classify-and-act** with a JS cap and `eval` gated, Haiku specialists, todos **off** unless the customer should see a multi-step plan. **B2** is the right interactive L2 shape (a handful of specialists, one parent turn of parallel `task`), not a 200-ticket hammer. Keep shared-state refunds on the parent with no subagents.

---

## Common Failure Modes

| Failure | Cause | Detection | Mitigation |
| --- | --- | --- | --- |
| Accidental GP spend / hidden tools | Bare `create_deep_agent` auto-adds GP + `task` | Second prefix in traces; agent-assist UI missing intermediates | `enabled=False` + no sync specs; `stream.subagents` / subgraph streaming if GP stays on |
| `ValueError` stripping `task` | `excluded_middleware={"SubAgentMiddleware"}` | Construction exception (good) | Disable GP; never strip scaffolding |
| Nested recursion 25 / sibling cancel | Historical #1698: child without `config`; `gather` cancels siblings | `GraphRecursionError` at 25; sibling `CancelledError` | Pin 0.7.x bind 9,999; verify gather; **still** `ModelCallLimitMiddleware` so 9,999 is not a token bomb. Re-check #2315/#2362 |
| Todos missing after upgrade | v0.7 opt-in; UI assumed `stream.values.todos` | Empty panel | `middleware=[TodoListMiddleware()]` only if you need the UI. Codex profile still injects — mixed fleets disagree |
| Parallel `write_todos` | Tool replaces the entire list | `after_model` rejection | One call per turn |
| Context / PII leak on handoff | Child returns raw dumps; `raw` schema; `fork` mode | Parent window bloat; PII in parent checkpoint | “Do NOT include raw data”; VFS for blobs; small `response_format`; no `fork` |
| Infinite `task` spawn | No harness N cap; dynamic `Promise.all`; async start loop | 50-child traces; queued launches | `ToolCallLimitMiddleware` on `task`; JS cap; disable GP; `subagents=False` on interpreter |
| Wrong specialist / parent won’t delegate | Vague `description`; overlapping specs | Parent does the work; duplicated searches | Action-oriented router + parent “delegate via `task()`” + differentiate **when** |
| Immediate async `check` loop | Model treats async as sync | Wasted turns; user-path ≈ child p99 | System prompt: after launch, return control; history is stale |
| Truncated async task ID | Model abbreviates UUID | `check`/`cancel` miss | Full id; try another model if persistent |
| Launch queues | `--n-jobs-per-worker` too small | Hung `start_async_task` | Slots ≥ 1 + concurrent children |
| `invoke` + ASGI (no `url`) | Sync parent, in-process worker | Transport unavailable | `ainvoke` or set `url` |
| Compiled graph without `messages` | Custom StateGraph omitted the key | `ValueError` from task tool | Include `messages`; a final `AIMessage` is enough |
| Dynamic schema on compiled child | `responseSchema` requires a raw `SubAgent` spec | `ValueError` | Declarative spec only |
| Per-thread compile + parallel `task` | `checkpointer=True` on compiled child | Checkpoint corruption | Ephemeral default; or disable parallel tool calling |
| Dynamic HITL bypass / loop-until-done | `eval` `task()` skips `interrupt_on`; no max-iterations | Unbounded eval; unapproved MCP | Gate `eval`; JS round cap; `mode="turn"` if needed |
| Child MCP loop amplifier | Inherited full parent MCP (#1698 legal child) | Step count; token burn | Replace `tools` on the spec; `ModelCallLimitMiddleware` |

No public Deep Agents post-mortem corpus beyond GitHub issues (#1698, #2315/#2362). Do not invent incidents.

---

## Key Takeaways

- Subagents are **context quarantine with a single handoff**, not a free speedup and not a compliance boundary. Default **GP is on**; that is a cost and recursion footgun.
- You cannot `excluded_middleware` the `task` scaffolding. Disable GP on the harness profile **and** pass no sync specs. Empty `subagents=` alone does **not** drop GP.
- **9,999** is a LangGraph 10000-sentinel dodge, not a product max-hops feature. **25** is the nested-graph ghost from #1698 (sibling cancel via `gather`). **1000** is today’s LangGraph default. None of them is `max_task_calls` — use `ToolCallLimitMiddleware` on `task`.
- Declarative children inherit tools / HITL / permissions (lists **replace**); compiled and async **do not**. Dynamic `eval` `task()` **skips** parent HITL. Sync children cannot `task` grandchildren.
- Todos are **opt-in since v0.7** because evals showed higher cost and no accuracy. Turn them on for UI / weak models, not for completeness. GP mirrors the caller’s `TodoListMiddleware` **by identity** when present.
- Sync waits (p99 = child). Async returns a thread id on `async_tasks` so summarization cannot drop the handle — budget worker slots. Dynamic `Promise.all` still blocks the `eval` turn.
- Identity in `context_schema` / `runtime.context`, never in `task` JSON. `lc_agent_name` is harness metadata. Zero-Trust MCP on children = inherit-or-replace `BaseTool`s + gateway PEP; `permissions=` never covers MCP. PII is **detect → redact → audit** on child traces **and** the handoff.
- Anthropic paid **15×** chat tokens for **90.2%** research lift and still said don’t use multi-agent when work isn’t parallelizable. Fallback: **GP on → GP off → parent-only**, never unbounded spawn.

---

## Interview Q&A

**Q1. What is Deep Agents delegation, in one minute?**  
I treat subagents as context quarantine, not a scheduler. A sync `task` is a nested LangGraph graph with a fresh `messages` list and a single `ToolMessage` handoff. Async is Agent Protocol on another thread. Dynamic is `task()` inside QuickJS `eval`. Default GP is on, so a bare `create_deep_agent` already has `task`. Todos are opt-in since v0.7. `recursion_limit` 9,999 is a fuse, not max hops.

**Q2. Walk parent `task` → child → handoff.**  
The parent model emits `task(description, subagent_type)`, optionally several in one message for parallel fan-out. Middleware copies parent state minus `messages` / `todos` / `structured_response` / private keys, overwrites `messages` with the description, runs the child until it stops tools, and returns one ToolMessage — JSON if `response_format` is set, else last non-empty AI text (walk backward past empty Anthropic `end_turn`). The parent relays a summary; the report is not shown to the user. `runtime.context` is forwarded. Child bound `recursion_limit` wins over the parent.

**Q3. How do I run with no `task` tool?**  
`GeneralPurposeSubagentProfile(enabled=False)` **and** no synchronous `subagents=`. Then `SubAgentMiddleware` is never attached. Async specs still get their five tools. `excluded_middleware={"SubAgentMiddleware"}` raises `ValueError`. Empty `subagents=` alone does **not** disable GP.

**Q4. Inheritance: HITL, permissions, MCP, compiled vs async.**  
Declarative: tools inherit unless the list replaces entirely; `interrupt_on` inherits and can override; `permissions` inherit and replace entirely; skills do **not** inherit; middleware extras do **not**. GP inherits parent tools, skills, permissions, and default-middleware `.name` overrides. Compiled and async own their runnable/remote graph — no parent HITL, permissions, or MCP unless I wired them there. Interpreter `task()` skips parent `interrupt_on`; I gate `eval`.

**Q5. Give me `$ per 1k` for 0 vs 1 vs N children.**  
Inferred, same 08 parent shape: Sonnet 4.6, 10 parent calls, 2k cached prefix → **$223 / 1k** with zero children. One 8-call isolated child is **$180** → **$403 / 1k** (1.81×). Three Sonnet children **$763 / 1k**. Five **$1,123 / 1k**; five without child cache **$1,303 / 1k**. Three Haiku children on a Sonnet parent land back at **$403 / 1k**. Anthropic’s 15× chat is the ceiling for “research everything,” not this toy 8-call math. GP multiplier is **+0.8–1.0× per 8-call child**; LangChain does not publish one.

**Q6. What p50/p95/p99 do you put on parent-wait vs async?**  
Nobody publishes them. I contract an 8-call sync child the parent waits on at **16,000 / 64,000 / 160,000 ms**, inferred as 8× a 2s/8s/20s ReAct cycle. Three parallel equal children stay in that band if gather isolation holds (max, not sum). Async ASGI launch+ack is **2,000 / 8,000 / 20,000 ms** — child work is off the user path. HTTP extra vs ASGI **50 / 200 / 1,000 ms** inferred policy. HITL on a sync child **30,000 / 180,000 / 600,000 ms**, expire-deny. Dynamic `while (true)` has **no SLA** unless I cap rounds. I measure with `ls_agent_type=subagent`; I do not pretend 9,999 is an SLO.

**Q7. Why is `recursion_limit` 9,999, and what was #1698?**  
LangGraph `merge_configs` dropped 10000 as the default sentinel, so nested graphs fell back to 25. Binding 9,999 dodges that. #1698: 0.4.4 children ran at 25, `GraphRecursionError` became `CancelledError` and `asyncio.gather` cancelled the sibling. Parent `recursion_limit=300` did nothing. It is a super-step fuse, not max `task` calls. I still set `ToolCallLimitMiddleware` on `task` and `ModelCallLimitMiddleware` on the child. I re-verify config forwarding on the pin (#2315/#2362).

**Q8. Zero-Trust MCP on children — inherit or not?**  
MCP is just `BaseTool`s on `tools=`. Declarative omit ⇒ full parent MCP, including whatever gateway headers/mTLS/OAuth I wrapped. Declarative specify ⇒ that list only — that is my specialist RBAC. GP inherits everything. Compiled/async do **not** inherit; I configure PEP on the remote graph; async `headers` are service auth. `permissions=` is fail-open FS-only and never sees MCP. Identity from forwarded `runtime.context`, never from `task` JSON. `lc_agent_name` is safe for tool policy, not for “the model said it is admin.” Dynamic `Promise.all` multiplies that surface without per-call HITL.

**Q9. PII — detect → redact → audit for children.**  
Child traces hold dumps the parent never sees, so quarantine is not DLP. I detect regex + optional ML on description, child tool I/O, handoff JSON, and todos titles. I redact/mask/hash via `PIIMiddleware` on the **child** list (not inherited) and on the parent handoff; I block PAN into child MCP args / VFS. I audit WORM of decisions — pre/post hashes, entity types, counts, detector, cid, `lc_agent_name`, `ls_agent_type=subagent`, async thread id — not raw PAN. LangSmith retention on a research copilot that fetched HR tickets is a PII store; I set workspace retention independently.

**Q10. Circuit breaker and fallback.**  
The library does not ship a breaker. I wrap `task`/child-model: closed → open → half-open with one specialist probe. Fallback is **GP on → GP off (specialists only) → parent-only**. I never fail open to unbounded `Promise.all`, never auto-approve HITL, never `excluded_middleware` SubAgent. Async pool exhaustion queues — I size slots to 1 + concurrent children or I drop to parent-only.

**Q11. Todos since v0.7 — when do you turn them back on?**  
Default off. LangChain’s evals: no significant accuracy, tokens up on two of three models, reward CIs spanned zero, slightly better reward and lower cost with todos disabled. Combined harness trim ~6k→~2k base tokens (−65%). I enable `TodoListMiddleware()` for a progress UI, long plans, or weak models. GP shares that instance by identity; declarative children do not inherit. `write_todos` replaces the whole list, max one per turn. I do not render an empty todos panel when the channel is absent.

**Q12. Sync vs async vs dynamic — how do you choose?**  
Parent must have the result before the next thought: sync `task`, parallel in one message. User should keep chatting, cancel, or steer: async preview, `async_tasks` channel, ASGI unless I need a split deploy, `ainvoke`, don’t poll after launch. Batch classify-and-act / adversarial verify / RLM-style slice-and-call: dynamic `task()` with a JS N/round cap and `eval` gated; disable with `CodeInterpreterMiddleware(subagents=False)`. L1 support: no subagents. Shared HITL conversation: parent-only — per-thread compiled children plus parallel `task` corrupt checkpoints.

---

## Key Numbers to Memorize

### Package / forms / versions
| Number | What |
| --- | --- |
| **0.7.12** | Research pin (PyPI 2026-09-01) |
| **`>=0.5.0`** | Async subagents **preview** |
| **`>=0.5.2`** | Filesystem `permissions` on children |
| **`>=0.5.3`** | Structured output on subagents |
| **v0.7** | Todos **opt-in**; ~6k→~2k base tokens (−65%) |
| **beta** | Dynamic interpreter (`deepagents[quickjs]`, `langchain-quickjs>=0.2.0`, Python `>=3.11`) |
| **`_EXCLUDED_STATE_KEYS`** | `{messages, todos, structured_response}` |

### Tokens / Anthropic / todos
| Number | What |
| --- | --- |
| **~6k → ~2k / −65%** | v0.7 default-agent base input tokens |
| **90.2%** | Anthropic multi-agent vs single Opus 4 (internal research eval) |
| **80%** | BrowseComp variance explained by token usage |
| **4× / 15×** | Agents vs chat / multi-agent vs chat tokens |
| **3–5 / 3+ / ≤90%** | Parallel subagents / tools each / wall-clock cut vs sequential |
| **50** | Early Research spawn footgun on simple queries |
| **<500 / <300 words** | Docs child output caps |
| **5+** | Collapse UI subagent cards |
| **≤1** | `write_todos` per model turn |
| **10M+** | RLM paper context (Zhang/Kraska/Khattab, arXiv:2512.24601) |
| **40%** | Anthropic: rewritten MCP descriptions cut later task time |

### $ / SKUs **[inferred]** where marked
| Number | What |
| --- | --- |
| **$3 / $15 / $3.75 / $0.30** | Sonnet 4.6 in / out / 5m write / cache read per MTok |
| **$1 / $5 / $1.25 / $0.10** | Haiku 4.5 in / out / 5m write / cache read per MTok |
| **[inferred] $223 / 1k** | 0 children, 10-call cached 2k prefix |
| **[inferred] $180** | One 8-call Sonnet child |
| **[inferred] $403 / 1k** | 1 GP child **or** Sonnet parent + 3 Haiku children |
| **[inferred] $763 / $1,123 / $1,303 per 1k** | 3 Sonnet children / 5 cached / 5 uncached children |
| **[inferred] $0.060** | Haiku 8-call child |
| **[inferred] ~$820 / 1k** | 3 Sonnet gatherers + 1 Haiku reviewer (toy size) |
| **[inferred] +0.8–1.0×** | Per isolated 8-call GP child vs parent bill |
| **[inferred] $3.90 / 1k + $0.008 / run** | Todo prefix tax / extra `write_todos` turns (order-of-magnitude) |
| **[inferred] $12 / $36** | 200-ticket Haiku vs Sonnet child batch |

### Recursion / async / caps
| Number | What |
| --- | --- |
| **9,999** | Deep Agents bound `recursion_limit` (sentinel dodge vs 10,000) |
| **10,000** | Frontend claimed default; `merge_configs` historically dropped |
| **1000** | LangGraph default since 1.0.6 |
| **25** | Nested-graph ghost; #1698 child wall (10 model + 15 tool/MCP) |
| **~2** | Super-steps per ReAct cycle ⇒ 25 ≈ 12 cycles |
| **4** | Example async slots: supervisor + 3 children |
| **10** | Docs example `--n-jobs-per-worker` |
| **#1698** | Sibling cancel via `gather` (`deepagents==0.4.4`, closed 2026-04-17) |
| **#7314** | `recursion_limit==10000` dropped (filed 2026-03-27, closed 2026-03-30) |
| **#2315 / #2362** | Later task-tool config-forwarding regression |

### Latency / security (numeric ms)
| Number | What |
| --- | --- |
| **16,000 / 64,000 / 160,000 ms** | **[inferred]** sync 8-call child, parent waits; also 3-parallel max and dynamic `Promise.all` of 3 |
| **2,000 / 8,000 / 20,000 ms** | **[inferred]** async ASGI launch+ack (child off path); one ReAct cycle |
| **50 / 200 / 1,000 ms** | **[inferred policy]** async HTTP extra vs ASGI on launch/check |
| **10 / 50 / 200 ms** | **[inferred policy]** async `check` RTT, ASGI |
| **30,000 / 180,000 / 600,000 ms** | **[inferred policy]** HITL on sync child; p99 expire-deny |
| **160,000 / 640,000 / 1,600,000 ms** | **[inferred if JS-capped at 10 rounds]** serial loop-until-done; uncapped = no SLA |
| **0 / 0 / 0 ms** | Construction / disable-GP extra vs parent-only *shape* |
| **detect → redact → audit** | PII on child traces **and** handoff reports **before** persist |
| **fail-open** | `permissions=` when no rule matches (FS tools only) |
| **replace entirely** | Child `tools` / `permissions` lists (not merged) |

**Dates:** research frozen **2026-09-02**. Do not treat inferred `$` or ms as list prices or vendor SLOs.
