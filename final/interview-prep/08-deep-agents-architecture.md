# Deep Agents Architecture (`create_deep_agent`)

## What Is This?

Imagine you hire a brilliant new employee (an LLM) but they show up on Day 1 with no desk, no laptop, no access badge, no file cabinet, and no idea who to ask for help. They can think -- but they cannot *do* anything.

A "harness" is everything you give that employee so they can actually work: a workspace (filesystem), tools (APIs), a task list (planning), colleagues to delegate to (sub-agents), rules about what they can and cannot touch (permissions), and a memory system so they do not forget what happened yesterday.

**Deep Agents** is LangChain's factory for assembling that entire harness in a single function call -- `create_deep_agent()`. Under the hood, it configures LangGraph (the graph execution engine) with a layered middleware stack. The output is a standard `CompiledStateGraph` -- not a proprietary black box. You can inspect it, extend it, or drop down to raw LangGraph whenever the harness does not fit.

**Deep Agents is not a new runtime.** It is LangChain's opinionated **harness** on top of `langchain.agents.create_agent`, compiled onto the **LangGraph** runtime. Same `CompiledStateGraph` object. Same ReAct-style loop. The product is the **defaults already wired**: a virtual filesystem (`ls` / `read_file` / `write_file` / `edit_file` / `glob` / `grep` / `delete`), a `task` tool plus auto **general-purpose** subagent, summarization + large-result offload, prompt-caching middleware, optional skills/memory/HITL/permissions.

LangChain's own equation: **Agent = Model + Harness**. A harness is "everything around that loop: the prompt, the tools, and any middleware that shapes the model's behavior."

Think of a kitchen. **LangGraph** is the stove (durable heat, timers, interrupts). **`create_agent`** is a burner and a pan -- you bring every ingredient. **Deep Agents** is the *mise en place*: cutting board (VFS), prep cooks (subagents), a rule that leftover stock goes in the walk-in (offload/summarize). You can still cook on a bare burner. You cannot pretend the *mise* is a second stove.

The project was directly inspired by Claude Code: an attempt to understand what makes Claude Code effective and make those patterns model-agnostic.

**Package pin:** `deepagents==0.7.12` (PyPI 2026-09-01; Development Status 4 -- Beta; Python `>=3.11,<4.0`; MIT).

## Why It Matters

The 2026 industry consensus is that **the model is commodity; the harness is moat**. Two teams using the identical model can see a 40-point difference in task completion rates based purely on harness design. If you are interviewing for a Director/VP role, you need to articulate *why* the orchestration layer matters more than the model choice -- and exactly how to design one for production.

Almost every "long-horizon agent platform" interview now forks here: do you own the **graph shape** (custom LangGraph), the **loop with your middleware** (`create_agent`), or the **assembled context machine** (Deep Agents)? The trap answers are "Deep Agents is a new orchestrator," "`tools=` removes built-ins," and "`excluded_middleware={"FilesystemMiddleware"}` is how I drop the VFS."

v0.7 (2026-07-29) is the cost story: hidden SDK base prompt gone, builtin tool descriptions **-43%**, todos **opt-in**, base input tokens **~6k -> ~2k (-65%)**. LangChain's own GTM agent on this stack: ~**10k requests/week**, **150** active users, **26%** interactive / **74%** ambient. PyPI last-month downloads **5,646,660**.

---

## Architecture / System Design

### Three-Layer Hierarchy

The architecture is a strict three-layer stack. Each layer adds capability:

```
+--------------------------------------------------+
|  create_deep_agent()                             |
|  Full harness: filesystem, sub-agents, skills,   |
|  context management, planning, memory            |
+--------------------------------------------------+
|  create_agent()                                  |
|  Minimal harness: agent loop + tool interface    |
+--------------------------------------------------+
|  LangGraph                                       |
|  Graph runtime: nodes, edges, state,             |
|  checkpointing, streaming, interrupts            |
+--------------------------------------------------+
```

| Layer | Package / entry | Job | Control vs opinion |
| --- | --- | --- | --- |
| **Runtime** | LangGraph | Durable execution, streaming, interrupts, checkpoints, stores | Most control, least opinion |
| **Framework** | `create_agent` | Model + tools + middleware -> ReAct-style loop | Minimal harness |
| **Harness** | `create_deep_agent` | VFS, subagents, summarization/offload, skills, memory, profiles, HITL wiring | Least control of loop shape, most context-engineering defaults |

LangChain's published rule of thumb: **start with Deep Agents**; drop to `create_agent` or LangGraph when you need to own the harness or the graph shape.

### System Topology Diagram

Three stacked layers, **not** three competing products. Construction is the control plane; the token stream is the data plane.

```
                         TELEMETRY / OBSERVABILITY SINKS
         +----------------------------------------------------------------------+
         |  LangSmith traces  metadata.ls_integration=deepagents                 |
         |  stream.subagents (nested message/tool/task handles)                  |
         |  summarization spans  metadata.lc_source=summarization                |
         |  cache_creation / cache_read tokens   GraphRecursionError             |
         |  LANGSMITH_HIDE_INPUTS|OUTPUTS  Client(hide_inputs, anonymizer)       |
         |  0.7.9: tracing inputs disabled on middleware                          |
         |  WORM audit: (cid, thread_id, tool, arg_digest, perm) not bytes       |
         +--------------^---------------------^------------------^---------------+
                        | spans               | metrics           | audit events
+-----------------------+---------------------+------------------+-------------+
| CONTROL PLANE  (construction -- LLM-free; allow/deny lives in middleware)    |
|                                                                              |
|  +-------------+ +--------------+ +-------------+ +-------------------+     |
|  | create_deep_| | Harness/     | | permissions | | interrupt_on      |     |
|  | agent kwargs| | Provider     | | first-match | | HITL + checkpoint |     |
|  | model/tools | | Profile      | | fail-open   | | context_schema    |     |
|  +------+------+ +------+-------+ +------+------+ +--------+----------+     |
|         |               | overlays       | FS PDP          | pause           |
|         v               v                v                 v                 |
|  +--------------------------------------------------------------------+     |
|  | graph.py: resolve model+profile -> backend -> middleware DAG ->      |     |
|  |   GP subagent -> USER|BASE|SUFFIX prompt -> create_agent(...) ->    |     |
|  |   .with_config({recursion_limit: 9999, ls_integration:deepagents})  |     |
|  +--------------------------------------------------------------------+     |
+-------------------------------+----------------------------------------------+
                                | CompiledStateGraph (same type as LangGraph)
                                v
+----------------------------------------------------------------------+
| DATA PLANE  (untrusted token stream -- model proposes, tools dispose) |
|                                                                       |
|  messages + assembled system + tool schemas -> model -> final | tools |
|  Middleware may add/remove tools, inject prompt sections, compact      |
|  history, write typed state, enforce FS perms *before* a built-in     |
|  FS tool runs. A callable in tools= cannot rewrite the tool list or   |
|  prompt *before* the model call.                                      |
|                                                                       |
|  +------------ TOOL PROXIES (least privilege) ----------------------+ |
|  | FS: ls read_file write_file edit_file glob grep delete           | |
|  | execute (sandbox protocol only; else error string)  eval (QuickJS)| |
|  | task (GP + declarative SubAgent)   MCP/custom on tools= (additive)| |
|  | permissions= covers built-in FS only -- NOT MCP, NOT execute,    | |
|  |   NOT direct backend.*                                            | |
|  +------------------------------------------------------------------+ |
+---------+---------------+------------------+------------------+-------+
          |               |                  |                  |
          v               v                  v                  v
+----------------------------------------------------------------------+
| PERSISTENCE LAYER  (two LangGraph systems + VFS lifetimes)            |
|                                                                       |
|  +--------------+ +--------------+ +-------------+ +----------------+ |
|  | Checkpointer | | Store        | | VFS backends| | Sandbox / Hub  | |
|  | thread_id    | | cross-thread | | State (dflt)| | remote container|
|  | messages,    | | required for | | StoreBackend| | idle_ttl e.g.  | |
|  | interrupts,  | | StoreBackend | | Filesystem* | | 3600s          | |
|  | time-travel  | |              | | Composite   | | ContextHub     | |
|  +--------------+ +--------------+ +-------------+ +----------------+ |
|  InMemorySaver = RAM. PostgresSaver: thread_id < 255 chars.           |
|  StateBackend files checkpointed every step -- do not write large     |
|  blobs. *FilesystemBackend / LocalShellBackend: forbidden in deployed.|
+----------------------------------------------------------------------+
```

### Planes (Do Not Couple)

| Plane | Lives here | LLM-free? | Failure if coupled |
| --- | --- | --- | --- |
| **Control** | `create_deep_agent` kwargs, `HarnessProfile` / `ProviderProfile`, middleware graph, `permissions`, `interrupt_on`, checkpointer/store handles, `context_schema` | Yes for assembly. PDP-style allow/deny is in middleware/backends, **not** in the model | Putting allow/deny in the system prompt; treating `context_schema` as authn |
| **Data** | User messages, tool results, VFS bytes, memory files, skill bodies, model completions, traces | No -- untrusted token stream | Letting the model pick `user_id` or a store namespace |

### Request Flow Narrative

1. **Control / construction.** Application calls `create_deep_agent`. `graph.py` in order: (1) resolve chat model + `ProviderProfile` / `HarnessProfile`; (2) resolve backend (`StateBackend()` if omitted); (3) assemble the main-agent middleware stack; (4) build the default `general-purpose` subagent and any caller `subagents`; (5) compose the authored system prompt `USER` -> `BASE` -> `SUFFIX`; (6) call `langchain.agents.create_agent(...)`; (7) bind `.with_config({recursion_limit: 9_999, metadata: {ls_integration: "deepagents", ...}})`.

2. **Invoke contract.** Production always passes `thread_id` in `config["configurable"]` **and** a `context_schema` instance (`user_id`, flags). They are independent. SDK `client.threads.create()` owns `thread_id` on managed deployments. Self-hosted without checkpointer/store is **ephemeral**: one `invoke`, no resume, no HITL.

3. **Data plane loop.** LangGraph drives turns. Each turn the model sees message history + assembled system + the current tool surface. It returns a final message or tool calls. Tool results append to state. Middleware may compact, offload, patch dangling `tool_calls`, or interrupt **before** the next model call. The loop is not a custom Pregel scheduler -- Deep Agents changes the loop **through middleware**.

4. **Tool proxy.** Built-in FS tools hit the backend; `permissions` first-match inside `FilesystemMiddleware`. `task` fans out to a nested graph (isolated conversation unless experimental `mode="fork"` on 0.7.12). `execute` is real only if the backend implements `SandboxBackendProtocol`; otherwise an error string. MCP/custom tools on `tools=` are **additive** and **not** covered by `permissions=`.

5. **Persistence.** Each super-step checkpoints (plus per-task `checkpoint_writes` so succeeded siblings are not re-run). `DeepAgentState.messages` uses a **`DeltaChannel`** reducer (`langgraph>=1.2`, beta) so growth stays linear. Store namespaces are a **prompt-injection vector** if user A can write what user B reads.

6. **Stream / observe.** Typed projections for messages, tools, values, output; Deep Agents adds `stream.subagents` so each `task` has its own handle. Extra summarization LLM calls appear with `metadata.lc_source == "summarization"`.

7. **Stop.** Model stops calling tools, or `GraphRecursionError` at the bound 9,999 super-steps (hard error, not a graceful NFR degrade), or HITL interrupt (requires checkpointer).

### Four-Layer Capability Model

| Layer | Purpose | Components | Default on? |
|-------|---------|------------|-------------|
| 1 -- Execution | What the agent can *do* | Custom tools + MCP (additive); VFS tools; `delete` (>=0.7); `execute` (sandbox only); optional QuickJS `eval` | FS yes; `execute` only if sandbox protocol; interpreter opt-in |
| 2 -- Context | What the agent *knows* | Skills (progressive disclosure), memory (`AGENTS.md` always loaded), summarization, large-result offload (**20,000** tokens), prompt caching | Summarization + offload always; skills/memory if kwargs set; Anthropic cache always registered |
| 3 -- Delegation | How the agent *scales* | `task` + auto GP subagent; opt-in `TodoListMiddleware` / `write_todos` | GP **on** unless profile `enabled=False` + no sync `subagents`. Todos **off** since 0.7 |
| 4 -- Steering | How humans *control* it | `interrupt_on` -> `HumanInTheLoopMiddleware`; filesystem `permissions`; double-texting strategies | Off unless configured |

---

## Core Concepts & Algorithms

### Invariants (Harness, Not a Scheduler)

**I1.** Deep Agents introduces **no** new runtime. Durable execution, streaming, interrupts, checkpoints, stores = LangGraph. The loop shape is `create_agent`'s ReAct-style graph.

**I2.** `tools=` is **additive**. It never removes a built-in. Hide with `HarnessProfile.excluded_tools` or `FilesystemMiddleware(tools=[...])` (must keep `read_file`).

**I3.** `_REQUIRED_MIDDLEWARE = (FilesystemMiddleware, SubAgentMiddleware)`. `excluded_middleware` on either -> **`ValueError`**. Cannot strip permissions scaffolding.

**I4.** `permissions=` is a **path PDP for built-in FS tools only**, first-match-wins, **no match -> allow** (fail-open). Does not cover MCP, custom tools, `execute`/shell, or direct `backend.*`.

**I5.** Subagent `permissions` **replace** the parent list. Declarative `SubAgent.system_prompt` is **required** and does **not** inherit. `CompiledSubAgent` / `AsyncSubAgent` do **not** inherit `interrupt_on`.

### Middleware Stack -- Deterministic Assembly

The middleware stack is the core extension mechanism. It replaces subclassing. Each middleware can hook into six lifecycle points:

```
before_agent --> before_model --> wrap_model_call --> after_model --> after_agent
                                       |
                                  wrap_tool_call
                                  (per tool invocation)
```

**Full stack**, first to last:

| # | Slot | When present |
| --- | --- | --- |
| 1 | `SkillsMiddleware` | `skills=` set. **Before** filesystem so skill metadata exists before file tools |
| 2 | `FilesystemMiddleware` | Always. Permissions live here |
| 3 | `SubAgentMiddleware` | >=1 synchronous subagent (including auto GP) |
| 4 | `SummarizationMiddleware` | Always (`create_summarization_middleware`) |
| 5 | `PatchToolCallsMiddleware` | Always. Repairs dangling tool calls after interrupt/cancel/malformed args. **Before** caching so the cached prefix matches repaired history |
| 6 | `AsyncSubAgentMiddleware` | Async `subagents` present |
| 7 | **Caller `middleware=`** | After Patch. `.name` match **replaces in place** (>=0.7); else appends here |
| 8 | Profile `extra_middleware` | Resolved `HarnessProfile` |
| 9 | `_ToolExclusionMiddleware` | Profile `excluded_tools` |
| 10 | `AnthropicPromptCachingMiddleware` | **Unconditional**; `unsupported_model_behavior="ignore"` |
| 11 | `BedrockPromptCachingMiddleware` | If `langchain-aws` installed; no-op off-Bedrock |
| 12 | `FireworksPromptCachingMiddleware` | If `langchain-fireworks` installed; session affinity from `thread_id` |
| 13 | `MemoryMiddleware` | `memory=` set. **After** caching so AGENTS.md updates are less likely to bust the prefix |
| 14 | `HumanInTheLoopMiddleware` | `interrupt_on=` set, or any permission `mode="interrupt"` |

**Merging rules for custom middleware:**
- Custom middleware is matched by `.name` attribute against built-in middleware.
- If names match: the custom instance *replaces* the built-in, keeping its position in the stack.
- If names do not match: the custom instance inserts after `PatchToolCallsMiddleware` (position 5), before profile extras (position 8).

**What you cannot exclude**: `FilesystemMiddleware`, `SubAgentMiddleware`, and the permission middleware raise `ValueError` if you try to remove them. Use `excluded_tools` to hide their tools instead.

**Critical concurrency rule**: Never mutate `self` attributes inside hooks. Concurrent operations (sub-agents, parallel tool calls) will race. Use graph state for shared mutable data.

After assembly, `excluded_middleware` is filtered. Unmatched name -> `ValueError`. String matching **multiple distinct classes** -> `ValueError` (force class-form). `module:Class` import refs resolve lazily and **import Python code** (trusted config only).

### `excluded_tools` vs `excluded_middleware` (Interview Fork)

| Intent | Correct knob | Wrong knob |
| --- | --- | --- |
| Hide FS tools from the **model** but keep scaffolding (offload, permissions, skills/memory still need a VFS) | `excluded_tools={"ls", "read_file", ...}` **or** `FilesystemMiddleware(tools=["read_file", "ls", ...])` | `excluded_middleware={"FilesystemMiddleware"}` -> **`ValueError`** |
| Run with **no** `task` tool | Profile `general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)` **and** `subagents=` empty of sync specs | `excluded_middleware={"SubAgentMiddleware"}` -> **`ValueError`** |
| Drop summarization | `excluded_middleware={"SummarizationMiddleware"}` (public alias) | -- |
| Stop offering `execute` | `excluded_tools={"execute"}` (and/or non-sandbox backend) | Removing FS middleware |

Pre-0.7.9, hiding a tool from the model while leaving it callable was a realistic footgun. **Pin `>=0.7.9`** if exclusion is a control.

### Multi-Provider Model Interface

The `model=` parameter accepts `"provider:model"` strings:

```
model="anthropic:claude-sonnet-4-6"     # Anthropic direct
model="openai:gpt-5.5"                  # OpenAI
model="google_genai:gemini-3.6-flash"   # Google
model="ollama:north-mini-code-1.0"      # Local via Ollama
model="azure_openai:gpt-5.5"            # Azure
```

Any model supporting tool calling works. This is the key differentiator versus single-vendor SDKs (Claude Agent SDK = Claude only, OpenAI Agents SDK = OpenAI only).

`openai:` strings use the **Responses API by default**. Chat Completions: `init_chat_model("openai:...", use_responses_api=False)`. Disable Responses data retention: `use_responses_api=True, store=False, include=["reasoning.encrypted_content"]`.

**Published eval suite (not a production SLA):**

| Model | Overall | File Ops | Retrieval | Tool Use | Memory | Conversation | Summarization |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `google_genai:gemini-3.6-flash` | 82% | 100% | 100% | 90% | 54% | 38% | 80% |
| `openai:gpt-5.5` | 80% | 92% | 100% | 84% | 64% | 52% | 80% |
| `openai:gpt-5.4` | 18% | 100% | 100% | 18% | 51% | 38% | 100% |
| `anthropic:claude-opus-4-7` | 80% | 100% | 100% | 82% | -- | 48% | 100% |
| `anthropic:claude-opus-4-6` | 26% | 92% | 100% | 26% | 69% | 22% | 100% |
| `openrouter:z-ai/glm-5.1` | 89% | 92% | 100% | 89% | -- | 33% | 80% |
| `fireworks:.../glm-5p1` | 81% | 100% | 100% | 87% | -- | 33% | 80% |

`gpt-5.4` at **18%** overall with **100%** file ops is the interview trap: a model can ace FS and still fail tool-use.

### Harness Profiles -- Declarative Tuning

Profiles let you change harness behavior per provider or model *without* modifying `create_deep_agent` call sites. Two types:

**HarnessProfile** (agent-level tuning):

| Field | Effect |
| --- | --- |
| `base_system_prompt` | Replaces BASE in `USER -> BASE -> SUFFIX` |
| `system_prompt_suffix` | Always last; applied to main, declarative subagents, and auto GP |
| `tool_description_overrides` | Per-tool description map |
| `excluded_tools` | Post-injection name filter; can drop user tools **and** harness tools. As of **0.7.9** also blocks **execution** |
| `excluded_middleware` | Classes or strings or `module:Class` |
| `extra_middleware` | Appended at slot 8 |
| `general_purpose_subagent` | `enabled` / rename / re-prompt. GP-specific `system_prompt` **wins** over profile `base_system_prompt` |

**ProviderProfile** (model construction):
- `init_kwargs` -- passed to chat model constructor
- `pre_init` hooks -- credential validation, env checks
- `runtime_kwargs_factory` -- dynamic kwargs per invocation

**Resolution order:** Registration keys work at provider level (`"openai"`) or model level (`"openai:gpt-5.5"`). Model-level merges onto provider-level; unset fields inherit; explicit model-level wins. Load order: built-ins, then entry-point plugins, then direct `register()` calls.

**Merge:** prompt fields last-write-wins if set; tool-description maps merge per key; `excluded_tools` / `excluded_middleware` are **set union**; `extra_middleware` merge-by-name; GP profile field-wise.

### Prompt Assembly After v0.7

v0.7 cut the hidden base system prompt and trimmed builtin tool descriptions **43%**. Combined with opt-in todos, **base input tokens** on a default-agent turn dropped **65% (~6k -> ~2k)**.

Assembly: caller `system_prompt` **first**; profile BASE only if set; suffix **last**. Empty caller + empty profile -> **empty authored system prompt**; the model still receives tool schemas and middleware-injected sections.

| Stack | Prompt resolution |
| --- | --- |
| Main | `USER` (`system_prompt=`) -> profile `base_system_prompt` -> profile suffix |
| Declarative subagent | Authored `system_prompt` -> that subagent's resolved profile BASE/suffix. Each subagent **re-resolves** the profile against **its own** model |
| Auto GP | `general_purpose_subagent.system_prompt` if set -> else profile `base_system_prompt` -> else SDK GP default; then profile suffix |

### Sub-Agent Architecture

The `task` tool spawns ephemeral sub-agents with:
- **Fresh context**: No conversation history from parent. Prevents context pollution.
- **Autonomous execution**: Runs to completion without parent interaction.
- **Single handoff**: Returns only the final result to parent (typically ~200 tokens).
- **Context isolation**: Heavy outputs stored in virtual filesystem; parent sees summaries only.
- **Permission inheritance**: Sub-agents inherit parent permissions by default. Explicit `permissions` in the sub-agent spec *replaces* parent rules entirely.

Custom `CompiledStateGraph` instances can be passed as sub-agents, so raw LangGraph orchestration plugs in alongside the harness's defaults.

### `recursion_limit`: 9,999 Is a Sentinel Dodge

`create_deep_agent` binds `recursion_limit: 9_999` on the returned graph. Bare LangGraph default is **25** super-steps.

**Why 9,999 not 10,000:** LangGraph `merge_configs` has historically **dropped** `recursion_limit` when it equals `DEFAULT_RECURSION_LIMIT` (10000), so `with_config({"recursion_limit": 10000})` was a no-op and nested graphs fell back to 25.

Issue #1698 (`deepagents==0.4.4`): subagents invoked **without** parent `config`, ran at 25, hit `GraphRecursionError` at exactly 25 steps, surfaced upstream as `CancelledError`, cancelled sibling `asyncio.gather` tasks.

`recursion_limit` is a **graph super-step ceiling**, not "max `task` calls." Product hop caps belong in application state / `ModelCallLimitMiddleware`. Hitting 9,999 is a **hard error**.

### Offload / Summarization State Machine

| Knob | Default |
| --- | --- |
| Tool-result / large-arg offload | **20,000** tokens; substitution = path + preview of **first 10 lines** |
| Summarization trigger (profile with `max_input_tokens`) | `("fraction", 0.85)` |
| Keep after summarize | `("fraction", 0.10)` |
| Fallback if no profile | trigger `("tokens", 170000)`, keep `("messages", 6)` |
| Immediate fallback | `ContextOverflowError` -> summarize + retry |
| History offload path | `/conversation_history` |
| `grep` match cap (0.7 FS perf) | **1,000** matches; `truncated` flag |

Offload of **write/edit inputs** is delayed until the session crosses 85% of window; results over 20k offload **immediately**. Summarization writes a filesystem canonical record plus an in-context summary. `create_summarization_tool_middleware` adds on-demand `compact_conversation` without disabling the 85% auto path.

v0.7 `write_file` **overwrites** instead of erroring -- a confused agent can clobber artifacts. `grep`/`glob` return **partial** results with `truncated`.

### Double-Texting Strategies

When a user sends a new message while the agent is processing:

| Strategy | Behavior | Use Case |
|----------|----------|----------|
| `enqueue` (default) | Queue new input, process after current run | Chat UIs, sequential workflows |
| `reject` | Refuse new input until current run completes | Critical operations |
| `interrupt` | Halt current run, preserve progress, process new input from preserved state | Interactive editing |
| `rollback` | Halt current run, revert all progress, process from scratch | Fresh-start preference |

### Streaming Architecture

There are two streaming surfaces:

**Raw stream:** `agent.stream(..., stream_mode=..., subgraphs=True, version="v2")`

**Typed event stream (recommended):** `agent.stream_events(..., version="v3")`

Typed projections let you consume different surfaces independently:
- `stream.messages` -- model token/message flow
- `stream.tool_calls` -- tool invocations
- `stream.values` -- state snapshots and progress
- `stream.subagents` -- Deep Agents-specific: each delegated task gets its own handle with `.name`, `.path`, `.status`, `.messages`, `.tool_calls`, `.values`, `.subagents`, and `.output`
- `stream.output` -- final result

`stream.subagents` is the Deep Agents-specific feature. Each delegated task gets its own handle. `stream.subgraphs` exposes graph structure; `stream.subagents` exposes product-level delegated tasks and is the better UI surface for humans.

`subgraphs=True` is how LangGraph surfaces subagent events into the raw stream. Without it, coordinator-only streaming can make delegated work invisible.

Namespaces matter. In the raw stream, `chunk["ns"]` or event metadata tells you whether an event came from the main agent or a subagent.

Use `get_stream_writer()` inside a tool or subagent node when you want custom progress events that are not just tokens or tool messages.

Typed streams support controlled interleaving: `stream.interleave("messages", "subagents")` when you want a single read loop.

### Version Gates That Change Harness Shape

| Version | Behavioral gate |
| --- | --- |
| `>=0.5.2` | `permissions=` |
| `>=0.5.3` | `model=None` (implicit `claude-sonnet-4-6`) **deprecated** |
| `>=0.6.8` | Permission `mode="interrupt"` |
| **`>=0.7.0`** | Todos opt-in; `delete` tool; `FilesystemMiddleware(tools=...)` allowlist; `.name` middleware override; backend factories removed |
| `>=0.7.3` | Exact-match `delete` first-match-wins |
| `>=0.7.8` | `files` state channel only for state backends |
| `>=0.7.9` | `excluded_tools` also blocks **execution**; tracing inputs disabled on middleware |
| `0.7.12` | Subagent conversation forking (experimental). Undoes isolation if used casually |
| `1.0.0` | `model=None` removed |

**Todos rationale:** across three eval categories x three models, slightly **better reward and lower cost with todos off**. Do not enable "for completeness" on capable models.

---

## Token Economics & Cost Analysis

### Harness Token Tax (Measured)

v0.7 eval vs `0.6.12`:

- Base input tokens **~6k -> ~2k** (-65%) on a default-agent turn.
- Matrix: three categories (autonomous, conversational, long-context) x four models.
- Reward CIs span zero for every model (no statistically significant quality drop). Luna: **-34% tokens, -15% cost, +4% reward**. `claude-sonnet-4-6` was the exception: cost **increased** on two hard autonomous tasks.

Unused built-in tools still send full JSON schemas every turn. `excluded_tools` shrinks baseline prompt size without waiting for offload/summarization.

### Prompt-Caching Meters (Published Unit Prices)

Deep Agents **auto-applies** prompt caching to static system sections for Anthropic and Amazon Bedrock. "No configuration is required." Default Anthropic TTL: **`5m`**. Replace in place with `AnthropicPromptCachingMiddleware(ttl="1h")` for long gaps.

Multipliers: **5m write = 1.25x** base input; **1h write = 2x**; **read = 0.1x**. Below minimum prefix length, cache markers are silently ignored.

| Model (docs string) | Input | 5m write | 1h write | Cache read | Output |
| --- | --- | --- | --- | --- | --- |
| Claude Sonnet 4.6 | $3 | $3.75 | $6 | $0.30 | $15 |
| Claude Sonnet 5 | $2 | $2.50 | $4 | $0.20 | $10 |
| Claude Opus 4.6 / 4.7 / 4.8 / 5 | $5 | $6.25 | $10 | $0.50 | $25 |
| Claude Haiku 4.5 | $1 | $1.25 | $2 | $0.10 | $5 |

USD per million tokens. US-only inference **1.1x**. Batch API **50%** off. Sonnet 5: the $2/$10 "introductory through 2026-08-31" price is now **standard**; the planned 2026-09-01 bump to $3/$15 **did not occur**. Fable/Mythos 5.1 cache **reads** are 0.025x input, not 0.1x.

Bedrock: explicit checkpoints; TTL **5 minutes** (many models) or **5m and 1h** for listed Claude IDs. Min tokens examples: Claude Sonnet 4.6 **1,024**; Opus 4.6 **4,096**; Opus 5 **512**; Haiku 4.5 **4,096**. Max **4** checkpoints/request on listed Claude.

Fireworks: session affinity from `config.configurable.thread_id`. Cross-provider `ModelFallbackMiddleware` **strips** Fireworks-only cache headers before a non-Fireworks fallback.

### `$ Cost per 1k Runs` [inferred]

Assumptions: Model `anthropic:claude-sonnet-4-6` at list prices. Task: medium research run, **10** model calls, all inside one 5-minute window. GP subagent **disabled**. v0.7 harness prefix: **2,000** tokens cached. Dynamic uncached tokens per call: **3,000**. Output: **800** tokens/call.

| Component | Tokens x unit | USD / run |
| --- | --- | --- |
| Cache write | 2,000 x $3.75 / 1e6 | $0.00750 |
| Cache reads | 9 x 2,000 x $0.30 / 1e6 | $0.00540 |
| Uncached input | 10 x 3,000 x $3 / 1e6 | $0.09000 |
| Output | 10 x 800 x $15 / 1e6 | $0.12000 |
| **Total / run** | | **$0.2229** |
| **Total / 1,000 runs** | | **$223** |

Same run **without** prompt caching = **$0.270 / run -> $270 / 1k**. Caching saves ~**$47 / 1k** at a 2k prefix.

If `memory=` + skills inflate the cached prefix to **20,000** tokens [inferred]:

| Path | / run | / 1k runs |
| --- | --- | --- |
| Cached (1 write + 9 reads of 20k + 30k uncached + 8k out) | $0.339 | **$339** |
| Uncached (10 x 23k in + 8k out) | $0.810 | **$810** |

**Default GP subagent on:** one extra isolated 8-call subagent with the same 2k prefix roughly **+0.8-1.0x** the main-agent bill [inferred]. Disable it for short tool-calling bots.

### Cost Formulas

```
Single-turn cost:
  C_turn = (input_tokens * P_input) + (output_tokens * P_output)
  With caching: C_turn = (cached_tokens * P_cache) + (uncached_tokens * P_input) + (output_tokens * P_output)

Multi-turn session cost:
  C_session = SUM(C_turn_i) for i = 1..N
  Without summarization: input_tokens_i ~ i * avg_turn_size  (linear growth)
  With summarization: input_tokens_i ~ min(i * avg_turn_size, threshold + K)  (capped)

Model tiering savings:
  C_tiered = C_supervisor(frontier) + SUM(C_worker_j(cheap))
  Reported: ~60% total spend reduction with ~4-point drop in routing accuracy
```

### Cost Control Mechanisms

**SummarizationMiddleware** -- the primary cost lever:
- Trigger: configurable threshold, e.g., `("tokens", 100000)`.
- Retention: `("messages", 20)` -- keeps N most recent messages verbatim.
- Older messages are summarized via an internal LLM call and offloaded to the virtual filesystem.

**Sub-Agent Context Isolation**:
- Each sub-agent gets fresh context, preventing token accumulation.
- Heavy subtask outputs stored in virtual filesystem; parent sees ~200-token summaries.

**Retrieval-Based Memory** (StoreBackend with semantic search):
- 72% token savings versus naive context injection from a 24-entry store.
- Savings grow with store size: naive injection scales linearly, retrieval holds flat at top-K.

**The #1 cost risk is runaway agent loops.**
- Documented case: 47-iteration supervisor loop burned $180 on a single request.
- Rate limit errors account for 60% of LLM call failures (Datadog 2026).
- Mitigations: `ToolCallLimitMiddleware`, max iteration counts, convergence detection, per-trace cost visibility in LangSmith.

### Latency SLA Targets

LangChain publishes **no** p50/p95/p99 of the harness. All values below are [inferred] policy targets.

| Path | **p50** | **p95** | **p99** | Grounding / mitigation |
| --- | --- | --- | --- | --- |
| **Streaming TTFT, parent, no summarizer** | **640 ms** | **2,560 ms** | **5,120 ms** | Stream; cache-warm prefix (5m TTL) |
| **One ReAct cycle (model + StateBackend FS tool)** | **2,000 ms** | **8,000 ms** | **20,000 ms** | Local VFS is not the tail; the model is |
| **Summarization extra LLM ON path** (85% window) | **2,000 ms** | **6,000 ms** | **15,000 ms** | Override `.name` with a cheaper summarizer |
| **GP subagent isolated 8-call, parent waits** | **16,000 ms** | **64,000 ms** | **160,000 ms** | Parallel `task` is `max()` of children, not `sum()` |
| **10-call research run, GP off, no summarize** | **20,000 ms** | **80,000 ms** | **200,000 ms** | Do not put on a chat HTTP timeout |
| **Checkpointer `sync` extra per super-step** | **10 ms** | **50 ms** | **200 ms** | Postgres fsync-class tax |
| **HITL interrupt clock** | **30,000 ms** | **180,000 ms** | **600,000 ms** | Durable queue; p99 = expire then deny |
| **Simple tool call (single turn)** | **1-3s** | **5s** | **8s** | Model inference + tool execution |
| **Sub-agent spawn + completion** | **5-15s** | **30s** | **60s** | Fresh context assembly + execution |
| **Checkpoint write (Postgres)** | **5-20ms** | **50ms** | **100ms** | Per super-step |
| **Checkpoint write (DynamoDB)** | **10-30ms** | **80ms** | **200ms** | <350KB direct, larger via S3 |
| **Summarization trigger** | **2-5s** | **10s** | **15s** | Internal LLM call for compression |

**Mitigations mapped to percentiles:**

- **p50 (user):** stream; Anthropic 5m cache on the 2k prefix; disable GP for short bots; `async` checkpointer; StateBackend only for tiny scratch.
- **p95:** cheaper summarizer via `.name` replace; `compact_conversation` before you hit 85% if the UX is interactive; timeout `task` independently of the parent.
- **p99:** HITL off the request thread; summarizer + GP fan-out **are** the tail -- measure with the root histogram plus `stream.subagents`; product hop cap much less than 9,999.

### Throughput / Back-Pressure

| Ceiling | Number | Effect |
| --- | --- | --- |
| Compiled `recursion_limit` | **9,999** | Super-step fuse. Hitting it is `GraphRecursionError` |
| Bare LangGraph default | **25** | Historical subagent footgun |
| Offload threshold | **20,000** tokens | Immediate for large results |
| Summarize trigger / keep | **0.85 / 0.10** of window | Extra LLM on the tail |
| `grep` cap | **1,000** matches | Partial recall |
| GTM internal agent | ~**10k req/week**, **150** users | Traffic shape, not RPS |
| Sandbox idle TTL (docs example) | **3,600 s** | Fault domain recycle |
| `ModelRetryMiddleware` | default `max_retries=2` (3 attempts) | Exponential backoff |
| Provider TPM/RPM | account limits | **The** throughput ceiling |

**Back-pressure design:** (1) admit with a **product** hop/`task` cap and a $ budget -- do not ship 9,999 as policy; (2) bulkhead parent model vs summarizer vs subagent fleet vs sandbox pool vs checkpointer writes; (3) disable GP + exclude `execute` for short tool-calling bots; (4) circuit on provider 429 so retries do not become a token amplifier; (5) sandbox OOM kills the **sandbox**, not the agent server; (6) StateBackend files are checkpointed every step -- capacity-plan checkpoint storage; (7) `grep`/`glob` `truncated` is back-pressure from the VFS.

### Availability, RPO & RTO Targets

| Target | Value | Notes |
|--------|-------|-------|
| **Availability** | 99.9% (3-nines) | Production agent harness uptime target |
| **RPO** | 0 (zero checkpoint data loss) | With PostgresSaver; MemorySaver = total loss on crash |
| **RTO** | <5 min | Restart container + replay from last checkpoint |

---

## Trade-offs & Failure Modes

### NFRs and Explicit Trade-offs

| NFR | Production stance | Competes with |
| --- | --- | --- |
| **Availability of chat vs of the harness extras** | Product SLO is one parent loop. Summarizer / GP / HITL are **best-effort or a different clock** | Quality of long-horizon work vs user p99 |
| **RPO of checkpointer** | Last super-step (`sync`) / last successful async persist / **exit-only** (intermediate **lost**). `InMemorySaver` RPO = **empty on restart** | Crash-consistency vs p50 |
| **RTO of checkpointer** | Resume `thread_id` (+ optional `checkpoint_id` time travel). Replay **re-executes** nodes after that checkpoint | Time-to-resume vs forensic truth |
| **RPO of store / memories** | Last Store put. Namespace by `(assistant_id, user.identity)` (recommended). Org namespace: keep **read-only** | Lifelong memory vs prompt-injection |
| **RPO of VFS** | StateBackend = checkpointer. StoreBackend = store. Sandbox = provider TTL / snapshots | Scratch convenience vs tenancy |
| **Correctness vs $** | v0.7 -65% base tokens with reward CIs spanning zero. Todos **on** cost more. GP on is a second prefix | Schema tax vs agency |
| **Compliance** | **Not provided by `deepagents`.** LangSmith Enterprise + your IdP. GDPR erasure of a thread is checkpointer+store+sandbox+trace purge | Time-to-debug vs residency |

### Checkpointing vs True Durable Execution

This is a critical architectural distinction for VP-level interviews:

- **Checkpointing** (LangGraph): Saves state. Developer is responsible for detecting the need to restore, triggering it, and coordinating at scale.
- **Durable execution** (Temporal, AWS Step Functions, Restate, DBOS, Inngest): The runtime itself guarantees exactly-once semantics, automatic recovery, and side-effect deduplication.

Session memory is not durable execution. Saving chat history helps an agent remember, but does not prove which shell command ran, which email was sent, or whether a retry would duplicate a side effect.

Production reference architectures in 2026 combine both: durable execution primitives for side-effect guarantees + LangGraph checkpointing for conversation state.

### Checkpoint Granularity -- The Waste Problem

The 2026 "Crab" checkpoint/restore study found:
- Over 75% of agent turns produce no recovery-relevant state.
- Blanket checkpointing is mostly waste.
- Semantics-aware checkpointing (only at phase boundaries) raised recovery correctness from 8% to 100% while cutting checkpoint traffic by up to 87%.

**Production recommendation**: Checkpoint at major phase boundaries, not at every micro-step.

### Checkpointer Backends

| Backend | Durability | Cost | Use Case |
|---------|-----------|------|----------|
| MemorySaver | Zero -- total state loss on crash | $0 | Dev/test only |
| SqliteSaver | Single-process, file-backed | Minimal | Local dev with persistence |
| PostgresSaver | ACID-compliant, point-in-time recovery | ~$50/mo managed RDS | Production default |
| RedisSaver | High-throughput, low-latency | Higher | High-frequency checkpoints |
| DynamoDBSaver | Auto-scaling, multi-region replication | Pay-per-request | AWS-native, global deployments |

### Failure Taxonomy

| Class | Examples | Detection | Handling |
| --- | --- | --- | --- |
| **Transient** | Provider 429/5xx, TPM, checkpointer blip, sandbox allocate queue, summarizer 5xx | Error rate; p99 latency window | Full-jitter retries on **idempotent** reads; `ModelRetryMiddleware` `max_retries=2`; **do not** retry `write_file`/`edit_file`/`delete`/`execute` without an idempotency key |
| **Permanent** | `ValueError` at construction (excluded scaffolding / unmatched name / `read_file` omitted / `StoreBackend` without `store`); `GraphRecursionError`; 4xx auth | Non-retryable | Fail closed to `create_agent` or deterministic refuse |
| **Poison-pill tools** | Hallucinated FS paths (fail-open if no rule); MCP tools that touch disk outside `permissions=`; `execute` on sandbox; `LocalShellBackend` in prod; experimental `fork` undoing isolation | Permission denials; backend errors; MCP hash drift | deny-by-rule before allow; pin `>=0.7.9`; never host shell; gateway hash-pin MCP |
| **Poison-pill memory / store** | Shared assistant/org namespace; skill name collision (last source wins); sleeper writes into `/memories/` | Unexpected cross-user reads | User namespace; org policies read-only; deny-write `/memories/**` from the agent |
| **Silent** | Non-deterministic output; tool call fails silently | Trace comparison, output validation | Schema validation per node boundary; structured error results |
| **Idempotency** | Two `write_file` on resume; HITL approve then mutated args (TOCTOU) | Duplicate side effects; hash mismatch | Idempotency keys on mutating tools; re-hash at execute |
| **Fan-out cancellation** | Historical: 25-step subagent -> `CancelledError` -> siblings die | Sibling tasks cancelled | Verify gather semantics; cap `task` fan-out |
| **Denial of wallet** | Accidental GP spawn; todos on; 9,999-step loop; summarizer storm | Token ledger; step count | Disable GP; todos off; product cap; breaker on retry loops |
| **Artifact clobber** | v0.7 `write_file` overwrites | Silent last-write-wins | Permissions deny on `/memories/**`; HITL on writes |
| **Fail-open FS leak** | No matching `permissions` rule -> allow | `.env` in VFS/checkpoints | Deny secrets before allow workspace |
| **Prompt-cache miss** | Memory update in cached segment | `cache_read_input_tokens` = 0 | Keep memory small; MemoryMiddleware is after cache on purpose |

### Common Failure Modes Quick Reference

| Failure | Cause | Mitigation |
| --- | --- | --- |
| `ValueError` at `create_deep_agent` | `excluded_middleware` names scaffolding | Hide tools or disable GP; never strip scaffolding |
| Tool hidden but still executable | `excluded_tools` on <0.7.9 | Pin `>=0.7.9` |
| Replacement drops `backend`/`permissions` | Override `FilesystemMiddleware` without passing outer kwargs | Pass `backend=` and `permissions=` into replacement |
| Accidental GP spend/loops | Default GP auto-added | `GeneralPurposeSubagentProfile(enabled=False)` |
| Context overflow | Summarization excluded and offload path gone | Keep factory summarizer |
| Under-recall on large trees | `grep` cap 1,000 + `truncated` | Teach agent to treat `truncated` as incomplete |
| Host shell in prod | `LocalShellBackend` / `FilesystemBackend` | Sandbox backend; 503 if pool empty |
| OpenAI retention | Responses API stores by default | `store=False` + encrypted reasoning |
| Stale docs | Context-engineering "built-in system preamble" | Trust API reference + v0.7 blog |

---

## Production Patterns & Best Practices

### Circuit Breaker (Closed -> Open -> Half-Open)

`deepagents` does **not** ship circuit breakers, leader election, or a token-bucket. Those are LangGraph/provider/application concerns. Put breakers in caller `middleware=` or around `graph.invoke`.

```
        provider 429/5xx | checkpointer timeout | sandbox pool empty
  +----------+  ------------------------------------------------>  +----------+
  |  CLOSED  |                                                      |   OPEN   |
  |  invoke  |  success resets consecutive count                    | FAIL FAST|
  +----+-----+                                                      | fallback |
       ^                                                            | chain    |
       | probe OK                                                   +----+-----+
       |                                                                 | cooldown
       |                                                           +-----v------+
       +------------ probe allow -----------------------------------| HALF-OPEN |
                    probe fail -> stay OPEN                          | 1 synthetic|
                                                                    | probe     |
                                                                    +------------+
```

**Fallback chain (required interview answer):** **Deep Agents (full harness) -> `create_agent` (thin harness, your tools only) -> deterministic refuse.** Cross-provider model fallback must strip Fireworks cache headers. Never: model 429 -> unsandboxed `execute`. Never: HITL timeout -> auto-approve. Never: circuit open -> `excluded_middleware` the filesystem.

**Independent breakers:** parent model, summarizer model, subagent model, checkpointer, store, sandbox pool. A summarizer 429 must not stall a short chat (**bulkhead**) **and** must not strip `FilesystemMiddleware`.

### Zero-Trust MCP, Tool-Level RBAC, HITL vs PDP

Deep Agents' own security policy is **"trust the LLM."** The agent can do anything its tools allow. Boundaries belong at tool/sandbox/permission/**gateway** code, **not** in the prompt.

**Harness placement:** `tools=` is **additive**. `permissions=` is first-match, **fail-open**, **built-in FS tools only** -- it does **not** cover MCP, custom tools, `execute`/shell, or `backend.*`. An MCP tool that writes disk is outside the FS PDP. Therefore Zero-Trust requires a **gateway PEP in front of MCP**, not `permissions=` globs.

| Zero-Trust control | On this harness |
| --- | --- |
| **Transport** | OAuth 2.1 + PKCE S256. RFC 8707 audience = canonical MCP server URI. No client-token passthrough (RFC 8693 exchange). Client `headers=` / `auth=` |
| **Capability negotiation** | MCP client lists tools -> additive `tools=`. Model proposes; PEP disposes (gateway / interceptor / `interrupt_on`). FS `permissions` dispose only built-in file tools |
| **Hash-pin / allowlist** | `toolSurfaceHash` over canonical JSON. Re-verify on every `tools/call`. CVE-2025-54136 CVSS 8.8. Adapter name filter is not hash pin |
| **Identity** | Bind from IdP into RunContext: `context_schema`, `runtime.server_info.user.identity`. `user_id` in model JSON is a proposal, not a principal |

**Three trust boundaries:** (1) model <-> host -- model cannot verify tool descriptions; (2) client <-> MCP server -- authN/Z + integrity; (3) MCP server <-> upstream API -- the server is a deputy with a token. CVE-2025-6514 CVSS 9.6: connecting to hostile metadata can be RCE before any tool call.

### Filesystem Permissions

Path-based allow/deny rules. First-match-wins evaluation. Three modes: `allow`, `deny`, `interrupt` (pauses for human approval; requires checkpointer). Default when no rule matches: operations allowed (permissive).

Correct permission ordering: deny `.env` **before** allow `/workspace/**`. Unanchored globs like `/**/secrets` **over-fire** on bulk `ls`/`glob`/`grep`/`delete` -- anchor (`/secrets/**`). `delete` on a directory is all-or-nothing.

### RBAC with Least-Privilege

| Role | Allowed Tools | Filesystem Access | Notes |
|------|--------------|-------------------|-------|
| **Viewer** | `read_file`, `ls`, `glob` | Read-only, scoped paths | Dashboards, reporting |
| **Developer** | `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep` | Read/write workspace | No `execute`, no sandbox |
| **Admin** | All tools including `execute` | Full access + sandbox | Sandbox access for testing |
| **Auditor** | `read_file`, `grep`, `glob` | Read-only + trace access | Compliance checks |

### PII Pipeline -- Detect, Redact, Audit

Three sinks, three controls:

| Sink | Default | Control |
| --- | --- | --- |
| **LangSmith traces** | Inputs/outputs logged when `LANGSMITH_TRACING=true` | `LANGSMITH_HIDE_INPUTS` / `LANGSMITH_HIDE_OUTPUTS`; `Client(hide_inputs=..., anonymizer=...)`; 0.7.9 tracing inputs disabled on middleware |
| **Checkpoints / StateBackend files** | Full messages + file bytes in thread state | Don't put secrets in VFS; deny `/workspace/.env`; prune checkpoints |
| **Model context** | Unredacted unless middleware | `PIIMiddleware("email", strategy="redact", apply_to_input=True)`. Strategies: `redact`, `mask`, `hash`, `block` |

`PIIMiddleware` is **not** in the default Deep Agents stack -- you append it (slot 7).

### Multi-Tenancy -- Three Auth Layers

1. **Custom Authentication**: `@auth.authenticate` handler validates credentials, returns user identity and permissions.
2. **Agent Auth**: Handles OAuth for third-party services, manages token refresh automatically.
3. **RBAC**: Controls operator-level access for team members.

Built-in: scoped threads, per-user sandboxes, run history.

### Dual-Layer Memory Model

**Short-term (thread-scoped)**: Lives in checkpoint state tied to `thread_id`. Provides conversation continuity, HITL workflow state, fault tolerance, and time-travel debugging.

**Long-term (cross-conversation)**: Key-value store organized by namespace tuples (e.g., `(user_id, "memories")`). Backed by PostgreSQL with semantic search. Queryable via API.

### CompositeBackend (Canonical Production Pattern)

Default route = thread `StateBackend`; named prefixes map elsewhere. `/memories/` -> `StoreBackend(namespace=lambda rt: (rt.server_info.assistant_id, rt.server_info.user.identity))`.

| Need | Minimum wiring |
| --- | --- |
| Script / unit test | Default `StateBackend`, no checkpointer |
| Multi-turn chat, restart-safe | `checkpointer=PostgresSaver` + `thread_id` |
| HITL | Checkpointer **required** |
| Cross-thread memory | `store=` + `CompositeBackend` `/memories/` route |
| Multi-tenant isolation | Namespace factory on `(assistant_id, user.identity)` plus LangSmith authz filters |

### Backend Isolation Levels

| Backend | Isolation | Production docs |
| --- | --- | --- |
| `StateBackend` / `StoreBackend` | No host FS, no shell | Default / durable memory |
| `FilesystemBackend` | Host paths; `root_dir` **absolute** | "Don't use in deployed agents" |
| `LocalShellBackend` | Host FS **and** unrestricted shell; `virtual_mode=True` jails **filesystem tools**, **not** `execute()` | "No isolation -- use only in controlled development." Never untrusted users |
| LangSmith / Daytona / etc. sandboxes | Isolated container + `execute` | Recommended when code must run |

### Production Deployment Checklist

1. Replace `MemorySaver` with durable checkpointer (PostgresSaver or DynamoDBSaver)
2. Configure `SummarizationMiddleware` trigger threshold per workload
3. Set filesystem permissions (default is permissive -- everything allowed)
4. Enable HITL for high-stakes tool calls (writes, deletes, execute)
5. Configure retry policies per-node
6. Set up LangSmith tracing on day one
7. Build evaluators in parallel with first agent, not after first production incident
8. Implement model tiering (frontier for supervisor, cheaper for workers)
9. Set `ToolCallLimitMiddleware` to prevent cost explosions
10. Configure double-texting strategy appropriate to UX
11. Pin `deepagents>=0.7.9` so `excluded_tools` also blocks execution
12. Disable GP subagent for short tool-calling bots
13. Set `excluded_tools={"execute"}` unless a sandbox is bound
14. Never use `LocalShellBackend` in production

---

## Code Examples

### Production Deep Agent Setup

```python
"""
Production Deep Agent with full middleware stack, model tiering,
durable checkpointing, permissions, HITL, and observability.

Requirements:
  pip install deepagents langgraph-checkpoint-postgres langchain-anthropic
"""

import asyncio
import logging
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend
from deepagents.middleware import (
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
)
from deepagents.permissions import FilesystemPermission
from deepagents.profiles import HarnessProfile, register_harness_profile
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -- 1. Persistence layer
DB_URI = "postgresql://agent_user:secure_pass@db-host:5432/agent_state"
checkpointer = PostgresSaver.from_conn_string(DB_URI)
checkpointer.setup()
store = PostgresStore.from_conn_string(DB_URI)
store.setup()

# -- 2. Backend -- composite routing
backend = CompositeBackend(
    default=StateBackend(),
    routes={
        "/workspace/": FilesystemBackend(root_dir="./workspace", virtual_mode=True),
        "/memories/": StoreBackend(
            store=store,
            namespace=lambda rt: (rt.server_info.user.identity, "memories"),
        ),
    },
)

# -- 3. Permissions -- first-match-wins, most specific first
permissions = [
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/**/.env", "/**/credentials*", "/**/*.key"],
        mode="deny",
    ),
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/workspace/**"],
        mode="allow",
    ),
    FilesystemPermission(
        operations=["read"],
        paths=["/memories/**"],
        mode="allow",
    ),
    FilesystemPermission(
        operations=["write"],
        paths=["/memories/**"],
        mode="interrupt",  # human approval for memory writes
    ),
    FilesystemPermission(
        operations=["read", "write"],
        paths=["/**"],
        mode="deny",  # deny-all catch-all
    ),
]

# -- 4. Custom middleware
summarization_mw = SummarizationMiddleware(
    trigger=("tokens", 80_000),
    retention=("messages", 15),
)
tool_limit_mw = ToolCallLimitMiddleware(max_calls=200)

# -- 5. Harness profile
production_profile = HarnessProfile(
    system_prompt_suffix=(
        "You are a production research assistant. "
        "Always cite sources. Never fabricate data."
    ),
    excluded_tools=frozenset(["execute"]),
)
register_harness_profile("anthropic:claude-sonnet-4-6", production_profile)

# -- 6. Custom tools
def search_knowledge_base(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the internal knowledge base for relevant documents."""
    return {"results": [{"title": f"Doc about {query}", "relevance": 0.92}]}

def create_support_ticket(title: str, description: str, priority: str = "medium") -> dict:
    """Create a support ticket in the ticketing system."""
    return {"ticket_id": "SUPP-1234", "status": "created", "priority": priority}

# -- 7. Assemble the agent
agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=[search_knowledge_base, create_support_ticket],
    system_prompt="You are a senior support engineer.",
    middleware=[summarization_mw, tool_limit_mw],
    backend=backend,
    permissions=permissions,
    memory="./AGENTS.md",
    interrupt_on={"tools": ["create_support_ticket"]},
    checkpointer=checkpointer,
    store=store,
)

# -- 8. Invoke with thread tracking
def handle_user_request(user_id: str, thread_id: str, message: str) -> str:
    config = {"configurable": {"thread_id": thread_id, "user_id": user_id}}
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": message}]},
            config=config,
        )
        return result["messages"][-1].content
    except Exception as e:
        logger.error("Agent invocation failed: %s", e, exc_info=True)
        return f"Error: {e}"

# -- 9. Model tiering
tiered_agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",  # frontier supervisor
    tools=[search_knowledge_base],
    system_prompt="You are a research coordinator. Delegate subtasks to workers.",
    subagents=[{
        "name": "data_gatherer",
        "model": "anthropic:claude-haiku-4",  # cheap worker
        "instructions": "Gather and summarize data. Return concise findings.",
        "tools": [search_knowledge_base],
    }],
    middleware=[summarization_mw, tool_limit_mw],
    checkpointer=checkpointer,
    store=store,
)
```

### Custom Middleware Example

```python
"""Custom middleware for per-request cost tracking and alerting."""

from deepagents.middleware import AgentMiddleware


class CostTrackingMiddleware(AgentMiddleware):
    name = "cost_tracking"

    def __init__(self, alert_threshold_usd: float = 1.0):
        self.alert_threshold = alert_threshold_usd

    def before_agent(self, state, config):
        """Initialize cost accumulator in graph state at run start."""
        state["accumulated_cost_usd"] = 0.0
        return state

    def after_model(self, response, state, config):
        """Track token usage after each model call."""
        usage = getattr(response, "usage_metadata", None)
        if usage:
            input_cost = (usage.get("input_tokens", 0) / 1_000_000) * 3.00
            output_cost = (usage.get("output_tokens", 0) / 1_000_000) * 15.00
            state["accumulated_cost_usd"] += input_cost + output_cost
            if state["accumulated_cost_usd"] > self.alert_threshold:
                print(f"COST ALERT: ${state['accumulated_cost_usd']:.4f}")
        return response

    def wrap_tool_call(self, tool_call, handler, state, config):
        """Log every tool call for audit trail."""
        print(f"AUDIT: Tool call -> {tool_call.get('name', 'unknown')}")
        return handler(tool_call)
```

### Harness Runtime with Fallback Chain (stdlib)

```python
#!/usr/bin/env python3
"""Harness runtime: create_deep_agent + fallback chain.

Fallback: Deep Agents -> create_agent -> deterministic refuse.
Run: python deep_agents_harness.py
"""
from __future__ import annotations
import hashlib, json, logging, random, re, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

# --- retries + full jitter ---
def retry_call(fn, *, attempts=3, base_s=0.2, cap_s=2.0, retryable=(TimeoutError,)):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except retryable as exc:
            last = exc
            time.sleep(random.random() * min(cap_s, base_s * (2**i)))
    raise last

# --- circuit breaker ---
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
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0

    def allow(self):
        if self._state is CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = CircuitState.HALF_OPEN
            else:
                raise CircuitOpenError(f"circuit_open:{self.name}")

    def record_success(self):
        self._failures = 0
        self._state = CircuitState.CLOSED

    def record_failure(self):
        self._failures += 1
        if self._state is CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

# --- PII: detect -> redact -> audit ---
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

def pii_detect_redact_audit(text, *, audit, cid, tenant, sink, block_pan=True):
    kinds = []
    if EMAIL_RE.search(text): kinds.append("email")
    if PAN_RE.search(text): kinds.append("pan")
    if "pan" in kinds and block_pan and sink in {"mcp_args", "sandbox_env"}:
        audit.append({"cid": cid, "sink": sink, "action": "block"})
        raise PermissionError(f"pii_block:{sink}:pan")
    redacted = EMAIL_RE.sub(lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]", text)
    redacted = PAN_RE.sub("[PAN]", redacted)
    audit.append({"cid": cid, "sink": sink, "action": "redact" if redacted != text else "allow"})
    return redacted

# --- runtime: breaker + fallback ---
@dataclass
class HarnessRuntime:
    deep_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("deep"))
    thin_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("thin"))
    audit: list = field(default_factory=list)

    def run(self, user_text, *, tenant_id, thread_id):
        cid = str(uuid.uuid4())
        safe = pii_detect_redact_audit(
            user_text, audit=self.audit, cid=cid, tenant=tenant_id,
            sink="model_input", block_pan=False)
        # Try Deep Agents -> create_agent -> refuse
        for breaker, name in [(self.deep_breaker, "deep"), (self.thin_breaker, "thin")]:
            try:
                breaker.allow()
                result = f"ok:{name}:{safe[:80]}"  # placeholder for graph.invoke
                breaker.record_success()
                return pii_detect_redact_audit(
                    result, audit=self.audit, cid=cid, tenant=tenant_id,
                    sink="model_output", block_pan=False)
            except (CircuitOpenError, Exception):
                breaker.record_failure()
        return json.dumps({"status": "refused", "reason": "all_circuits_open"})
```

---

## Interview Q&A

**Q: What is Deep Agents in one sentence?**
A: It is LangChain's opinionated agent harness on top of `create_agent` and LangGraph that bundles filesystem access, context management, delegation, streaming, and human approval into one constructor.

**Q: How is Deep Agents different from LangChain?**
A: LangChain is the framework with core building blocks (`create_agent`). Deep Agents is a higher-level harness that prepackages those blocks into a stronger default agent.

**Q: How is Deep Agents different from LangGraph?**
A: LangGraph is the runtime for durable execution, streaming, threads, and interrupts. Deep Agents uses that runtime but adds agent-specific conventions and middleware.

**Q: Walk `create_deep_agent` to invoke.**
A: Control plane: resolve model and `HarnessProfile`, resolve backend (`StateBackend` default), assemble middleware, add GP unless disabled, compose `USER -> BASE -> SUFFIX`, `create_agent`, `with_config`. Data plane: LangGraph loop; middleware shapes tools and prompt before the model; `tools=` callables run only after the model chooses them. Always pass `thread_id` and a `context_schema` instance. No checkpointer means no resume and no HITL.

**Q: `excluded_tools` vs `excluded_middleware`.**
A: Hide FS tools with `excluded_tools` or an allowlist that still includes `read_file`, because offload, permissions, skills, and memory still need a VFS. Disable `task` by turning off the GP profile and passing no sync subagents. If I `excluded_middleware` Filesystem or SubAgent I get `ValueError` at construction. Pre-0.7.9 exclusion was visibility-only; pin `>=0.7.9` so it also blocks execution.

**Q: When should I choose Deep Agents over `create_agent`?**
A: Choose it when you need filesystem access, long-running context compression, delegated subagents, or approval flows. Use `create_agent` when a minimal tool-calling loop is enough. Use custom LangGraph when policy belongs in edges (deterministic scoring/routing).

**Q: Is Deep Agents tied to one model provider?**
A: No. The docs explicitly position it as provider-flexible, unlike harnesses tightly coupled to a single model ecosystem. Quickstart shows Anthropic, OpenAI, Google, Ollama, Azure, Bedrock, Fireworks, and more.

**Q: Give me `$ per 1k` for a default-ish research run.**
A: [inferred] Claude Sonnet 4.6, 10 calls in a 5-minute window, GP off, 2k cached prefix after v0.7, 3k uncached in, 800 out. One 5m write + nine reads + uncached + output ~ $0.223/run -> **$223 / 1k**. Uncached same shape **$270 / 1k**. A 20k prefix with memory/skills is **$339 / 1k** cached vs **$810** uncached. GP on is roughly +0.8-1.0x the main bill. Todos cost more in LangChain's own evals.

**Q: What p50/p95/p99 do you put on Deep Agents?**
A: Nobody publishes harness percentiles. I contract streaming TTFT at **640 / 2,560 / 5,120 ms** [inferred]. One ReAct cycle **2,000 / 8,000 / 20,000 ms**. Summarizer extra hop **2,000 / 6,000 / 15,000 ms**. A parent-wait 8-call GP child **16,000 / 64,000 / 160,000 ms**. HITL is a different clock: **30,000 / 180,000 / 600,000 ms**, expire-deny.

**Q: Why is `recursion_limit` 9,999?**
A: LangGraph `merge_configs` historically dropped `recursion_limit` when it equaled the default 10,000, so nested graphs fell back to 25. Binding 9,999 dodges that sentinel. Issue #1698: subagents ran at 25, `GraphRecursionError` became `CancelledError`. It is a super-step fuse, not max `task` calls. Still set a product hop cap.

**Q: Permissions and HITL -- is that Zero Trust?**
A: No. `permissions=` is a fail-open path PDP for built-in FS tools. MCP, custom tools, `execute`, and `backend.*` are uncovered. `interrupt_on` is a review queue, not a PDP. MCP tools ride on additive `tools=`; `permissions=` never sees them. Zero-Trust is a **gateway PEP in front of MCP**: OAuth 2.1, RFC 8707 audience, no passthrough, hash-pin, identity from verified token.

**Q: What does Deep Agents add to LangGraph streaming?**
A: A first-class subagent projection, exposed as `stream.subagents`, so delegated work is observable as named child streams instead of only raw graph events. Typed projections for messages, tool calls, values. `subgraphs=True` is needed for raw streaming to surface subagent activity.

**Q: PII -- detect, redact, audit.**
A: Three sinks: LangSmith traces, checkpoints/StateBackend files, model context. Detect with regex plus optional ML before persist; redact/mask/hash/block via `PIIMiddleware` (not in the default stack). Audit WORM of decisions, not raw PAN. Gateway scanners fail-closed but miss bypass traces.

**Q: Circuit breaker and fallback.**
A: The library does not ship a breaker. Wrap `invoke`: closed -> open -> half-open with one probe. Independent breakers for parent, summarizer, checkpointer, sandbox. Fallback is Deep Agents -> `create_agent` -> deterministic refuse. Strip Fireworks cache headers on cross-provider fallback. Never fail open to `LocalShellBackend`.

**Q: Deep Agents vs `create_agent` vs custom LangGraph vs Claude Agent SDK.**
A: Research copilot, artifacts, subagents, summarization: Deep Agents. Short RAG Q&A, 1-2 tools, latency-sensitive: `create_agent`. Claims workflow with code scoring and policy in edges: custom LangGraph. Coding assistant: Deep Agents + remote sandbox if model portability matters; Claude Agent SDK if already Claude-standardized and will staff the server. Never LocalShell in prod.

**Q: Prompt cache and MemoryMiddleware order -- why?**
A: Anthropic/Bedrock cache middleware is unconditional, TTL 5m default, after Patch so the cached prefix matches repaired history. MemoryMiddleware is **after** the cache middleware so AGENTS.md updates are less likely to bust the whole prefix. Keep memory small.

**Q: What did v0.7 actually change, and what is still a footgun?**
A: Todos opt-in, hidden base prompt removed, tool descriptions -43%, base tokens ~6k->~2k, `.name` replace, `delete`, backend factories gone. Reward CIs spanned zero; Luna cost -15%. Footguns that remain: default GP on, permissions fail-open, `write_file` overwrites, `grep` 1,000-match truncate, 9,999 is not a product cap, beta + fast-moving pin, experimental `fork` in 0.7.12 undoes isolation.

---

## System Design Scenarios

### Scenario 1: Internal Research Copilot (Docs, Tickets, Web)

**Problem.** A 2,000-person company wants an internal copilot over Confluence, Jira, and the public web. Work is long-horizon and artifact-heavy. ~10k req/week. Security wants no shell, per-user memory, HITL on writes to shared knowledge.

**Architecture:**

```
  +---------+   +---------------------------------------------------------+
  | IdP/PEP |-->| CONTROL: create_deep_agent + HarnessProfile              |
  | JWT ->  |   |   excluded_tools={execute}   GP ON for source gathering  |
  | user_id |   |   interrupt_on writes to shared knowledge                |
  |         |   |   permissions: deny /memories/** write from agent;       |
  |         |   |     deny **/.env before allow /workspace/**              |
  |         |   |   thread_id + context_schema(user_id)  PostgresSaver     |
  +---------+   +----------------------------+----------------------------+
                                             v
                  +------------------------------------------------------+
                  | DATA: parent Sonnet/Opus synthesize                    |
                  |   declarative research-agent (cheaper model) via task  |
                  |   StateBackend scratch + CompositeBackend              |
                  |     /memories/ -> StoreBackend ns=(asst_id, user_id)  |
                  |   summarization ON (85/10)  offload 20k               |
                  |   stream.subagents  checkpointer durability=sync       |
                  +------------------------------------------------------+
```

**Trade-off matrix:**

| Axis | Deep Agents (recommended) | `create_agent` + retriever | Custom LangGraph |
| --- | --- | --- | --- |
| **Cost** | [inferred] ~$223/1k cached; GP on ~1.8-2.0x | Near-zero harness tax; will re-implement offload | Deterministic nodes cheap; extra graph-authoring time |
| **Latency** | 10-call run 20,000/80,000/200,000 ms | Lowest for 1-2 tool hops; dies on long-horizon | Deterministic steps can beat a long agent loop |
| **Security** | Trust-LLM + fail-open FS PDP; MCP uncovered by `permissions` | Smallest tool surface | Policy in edges (non-LLM PDP) -- strongest |

**Decision:** Deep Agents wins for long-horizon, artifact-heavy, non-deterministic research. `create_agent` is the right answer for RAG Q&A. Custom LangGraph is for claims workflows with policy in edges.

### Scenario 2: Multi-Tenant Customer Support Platform

**Problem**: B2B SaaS with 500 enterprise customers. AI agents search private KB, create tickets, escalate. 10,000 concurrent conversations. SOC 2 required.

**Architecture:**

```
+--------------------------------------------------------------+
|                      API Gateway / Auth                       |
|              (JWT validation, tenant extraction)              |
+----------------------------+---------------------------------+
                             v
+--------------------------------------------------------------+
|                   LangGraph Cloud / K8s                       |
|  +--------------------------------------------------------+  |
|  |  create_deep_agent() per tenant config                  |  |
|  |  model="anthropic:claude-sonnet-4-6" (supervisor)       |  |
|  |  + model="anthropic:claude-haiku-4" (workers)           |  |
|  +--------------------------------------------------------+  |
|  |  CompositeBackend                                       |  |
|  |    /workspace/ -> StoreBackend(ns=tenant_id)            |  |
|  |    /shared/    -> StoreBackend(ns="global_kb")          |  |
|  +--------------------------------------------------------+  |
|  |  Permissions: deny /**/.env, allow /workspace/**,       |  |
|  |  read-only /shared/**, deny-all catch-all               |  |
|  +--------------------------------------------------------+  |
|  |  HITL: interrupt on create_ticket, escalate_to_human    |  |
|  +--------------------------------------------------------+  |
|  PostgresSaver (checkpoints)  |  PostgresStore (memories)     |
|  Per-tenant thread isolation  |  Per-tenant namespace         |
+--------------------------------------------------------------+
```

**Key decisions:**
- Namespace-based tenant isolation over separate agent instances (500 tenants x dedicated instances is operationally expensive)
- PostgresSaver for SOC 2-compliant audit trails via SQL queryability
- Model tiering (Sonnet supervisor + Haiku workers) controls cost: ~60% reduction, ~4-point accuracy loss acceptable for KB lookups
- HITL limited to ticket creation + escalation (approving every search would destroy UX)

### Scenario 3: Autonomous Code Review Pipeline

**Problem**: 200 developers, 50 repos. AI-assisted code review: clone PR branch, run static analysis, read docs, produce structured review, post to GitHub. <3 minutes. Code never leaves org infrastructure.

**Architecture:**

```
+--------------------------------------------------------------+
|                     GitHub Webhook Handler                     |
+----------------------------+---------------------------------+
                             v
+--------------------------------------------------------------+
|                   Self-Hosted LangGraph                        |
|  +--------------------------------------------------------+  |
|  |  Supervisor Agent (claude-sonnet-4-6)                   |  |
|  |  Plans review strategy, delegates to workers            |  |
|  +--------------------------------------------------------+  |
|  |  Worker: code_analyzer (claude-haiku-4)                 |  |
|  |    Sandbox: E2B (Firecracker) -- git clone, lint, test  |  |
|  |    Network: blocked except internal git + registry      |  |
|  |                                                         |  |
|  |  Worker: doc_reader (claude-haiku-4)                    |  |
|  |    Reads: repo docs, style guides, ADRs                 |  |
|  |                                                         |  |
|  |  Worker: review_writer (claude-sonnet-4-6)              |  |
|  |    Synthesizes findings into structured review           |  |
|  +--------------------------------------------------------+  |
+--------------------------------------------------------------+
```

**Key decisions:**
- Self-hosting mandatory (code residency)
- E2B Firecracker for strongest isolation of arbitrary PR code
- Multi-worker for 3-minute SLA: `code_analyzer` and `doc_reader` parallel, then `review_writer` synthesizes
- Auth proxy pattern keeps GitHub tokens outside the sandbox

---

## Quick Reference: Framework Comparison Matrix

| Dimension | Deep Agents | Claude Agent SDK | OpenAI Agents SDK | CrewAI |
|-----------|-------------|------------------|-------------------|--------|
| Model support | Any (100+ providers) | Claude only | OpenAI only | Any |
| Deployment | Managed or self-host | Self-host only | OpenAI platform | CrewAI cloud |
| Multi-tenancy | Built-in (RBAC) | Build yourself | Limited | Enterprise tier |
| Checkpointing | Postgres/Redis/DynamoDB | None built-in | None built-in | Limited |
| HITL | Dynamic interrupts anywhere | Manual | Limited | Manual |
| LOC (basic) | ~10-20 | ~10-20 | ~10 | ~30-60 |
| LOC (production) | ~50-100 | ~200+ | ~100+ | ~100-200 |
| License | MIT | MIT (SDK) | MIT | MIT |

**When to use what:**
- Maximum control, custom workflows: raw LangGraph
- Long-running agents with sub-agents: Deep Agents
- Anthropic-only, self-hosted: Claude Agent SDK
- Fast multi-agent prototyping: CrewAI
- Simplest single-agent path: OpenAI Agents SDK

---

## Key Numbers to Memorize

### Package / Layers / Versions
| Number | What |
| --- | --- |
| **0.7.12** | Research pin (2026-09-01); beta; MIT |
| **5,646,660** | PyPI last-month downloads |
| **>=0.7.9** | `excluded_tools` blocks execution |
| **9,999** | Bound `recursion_limit` (sentinel dodge vs 10,000) |
| **25** | Bare LangGraph default; subagent footgun |
| **255** | PostgresSaver `thread_id` max chars |

### Tokens / Cache / Eval
| Number | What |
| --- | --- |
| **~6k -> ~2k / -65%** | v0.7 base input tokens |
| **-43%** | Builtin tool description trim |
| **20,000 / 10 lines** | Offload threshold / preview |
| **0.85 / 0.10** | Summarize trigger / keep fractions |
| **1,000** | `grep` match cap |
| **1.25x / 2x / 0.1x** | Anthropic 5m write / 1h write / read |

### $ / SKUs [inferred]
| Number | What |
| --- | --- |
| **$3 / $15** | Sonnet 4.6 input / output per MTok |
| **$223 / 1k** | 10-call cached 2k prefix, GP off |
| **$270 / 1k** | Same run uncached |
| **$339 / $810 per 1k** | 20k prefix cached / uncached |
| **+0.8-1.0x** | Default GP isolated 8-call extra bill |

### Latency [inferred policy]
| Number | What |
| --- | --- |
| **640 / 2,560 / 5,120 ms** | Streaming TTFT p50/p95/p99 |
| **2,000 / 8,000 / 20,000 ms** | One ReAct cycle |
| **2,000 / 6,000 / 15,000 ms** | Summarization extra LLM |
| **16,000 / 64,000 / 160,000 ms** | GP 8-call child |
| **10 / 50 / 200 ms** | Checkpointer `sync` extra |
| **30,000 / 180,000 / 600,000 ms** | HITL clock; p99 expire-deny |

### Security
| Number | What |
| --- | --- |
| **fail-open** | `permissions=` default when no rule matches |
| **RFC 8707** | MCP clients MUST send `resource` = canonical server URI |
| **RFC 8693** | Token exchange -- MUST NOT passthrough client token |
| **8.8 / 9.6** | CVE-2025-54136 MCPoison / CVE-2025-6514 connect-time RCE |
