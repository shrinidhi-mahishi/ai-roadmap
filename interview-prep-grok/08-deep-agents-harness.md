# Module 08: Deep Agents Harness (`create_deep_agent`)

**Study + interview prep.** Grounded in research dated 2026-09-02 (50 sources). Package pin **`deepagents==0.7.12`** (PyPI 2026-09-01; Development Status 4 – Beta; Python `>=3.11,<4.0`; MIT). `$ per 1k runs` is **[inferred]** from published Anthropic list prices × a stated run shape, not a SKU. LangChain publishes **no** p50/p95/p99 of the harness — missing percentiles are architecture-derived **[inferred] policy targets** and are marked. Sandbox internals, skills/memory deep-dives, and the full MCP tool catalog (topic 09) belong in later modules; guardrail taxonomies live in [07-guardrails](07-guardrails.md). This file is the **control-plane assembler**; Zero-Trust MCP (OAuth 2.1 / RFC 8707 / hash-pins / gateway PEP) is in §4.4, not deferred.

---

## What Is This?

**Deep Agents is not a new runtime.** It is LangChain’s opinionated **harness** on top of `langchain.agents.create_agent`, compiled onto the **LangGraph** runtime. Same `CompiledStateGraph` object. Same ReAct-style loop. The product is the **defaults already wired**: a virtual filesystem (`ls` / `read_file` / `write_file` / `edit_file` / `glob` / `grep` / `delete`), a `task` tool plus auto **general-purpose** subagent, summarization + large-result offload, prompt-caching middleware, optional skills/memory/HITL/permissions.

LangChain’s own equation: **Agent = Model + Harness**. A harness is “everything around that loop: the prompt, the tools, and any middleware that shapes the model’s behavior.” `create_agent` is a highly configurable harness with empty defaults. `create_deep_agent` is that same harness with commonly useful context-engineering already assembled. Sibling harnesses named in the concepts page: Claude Agent SDK, Manus, coding CLIs. Sibling runtimes: Temporal, Inngest. Sibling frameworks: Vercel AI SDK, CrewAI, OpenAI Agents SDK, Google ADK, LlamaIndex.

Think of a kitchen. **LangGraph** is the stove (durable heat, timers, interrupts). **`create_agent`** is a burner and a pan — you bring every ingredient. **Deep Agents** is the *mise en place*: cutting board (VFS), prep cooks (subagents), a rule that leftover stock goes in the walk-in (offload/summarize). You can still cook on a bare burner. You cannot pretend the *mise* is a second stove.

## Why It Matters

Almost every “long-horizon agent platform” interview now forks here: do you own the **graph shape** (custom LangGraph), the **loop with your middleware** (`create_agent`), or the **assembled context machine** (Deep Agents)? The trap answers are “Deep Agents is a new orchestrator,” “`tools=` removes built-ins,” and “`excluded_middleware={"FilesystemMiddleware"}` is how I drop the VFS.”

v0.7 (2026-07-29) is the cost story: hidden SDK base prompt gone, builtin tool descriptions **−43%**, todos **opt-in**, base input tokens **~6k → ~2k (−65%)**. LangChain’s own GTM agent on this stack: ~**10k requests/week**, **150** active users, **26%** interactive / **74%** ambient. PyPI last-month downloads **5,646,660** — ecosystem gravity, not your cluster. Interviews test whether you can split **control plane vs data plane**, refuse to strip scaffolding, pin `>=0.7.9` if `excluded_tools` is a control, and budget the **default GP subagent** as a second full tool-schema prefix, not a free abstraction.

---

### 1. System Topology & Data Flow

Three stacked layers, **not** three competing products. Construction is the control plane; the token stream is the data plane. Persistence is LangGraph’s checkpointer + store **plus** whichever VFS backend you bound. Tool proxies are the built-in FS/`task`/`execute` surface and anything you put on `tools=` (including MCP — additive; Zero-Trust in §4.4; catalog in topic 09). Telemetry is LangSmith (and whatever you hang on callbacks); Deep Agents does not ship a second APM.

```
                         TELEMETRY / OBSERVABILITY SINKS
         ┌──────────────────────────────────────────────────────────────────┐
         │  LangSmith traces  metadata.ls_integration=deepagents            │
         │  stream.subagents (nested message/tool/task handles)             │
         │  summarization spans  metadata.lc_source=summarization           │
         │  cache_creation / cache_read tokens   GraphRecursionError        │
         │  LANGSMITH_HIDE_INPUTS|OUTPUTS  Client(hide_inputs, anonymizer)  │
         │  0.7.9: tracing inputs disabled on middleware                    │
         │  WORM audit: (cid, thread_id, tool, arg_digest, perm) not bytes  │
         └────────────▲─────────────────────▲──────────────────▲────────────┘
                      │ spans               │ metrics           │ audit events
                      │                     │                   │
┌─────────────────────┴─────────────────────┴───────────────────┴───────────┐
│ CONTROL PLANE  (construction — LLM-free; allow/deny lives in middleware)  │
│                                                                           │
│  ┌─────────────┐ ┌──────────────┐ ┌─────────────┐ ┌───────────────────┐  │
│  │ create_deep_│ │ Harness/     │ │ permissions │ │ interrupt_on      │  │
│  │ agent kwargs│ │ Provider     │ │ first-match │ │ HITL + checkpointer│ │
│  │ model/tools │ │ Profile      │ │ fail-open   │ │ context_schema    │  │
│  └──────┬──────┘ └──────┬───────┘ └──────┬──────┘ └─────────┬─────────┘  │
│         │               │ overlays       │ FS PDP           │ pause      │
│         ▼               ▼                ▼                  ▼            │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ graph.py: resolve model+profile → backend → middleware DAG →       │  │
│  │   GP subagent → USER|BASE|SUFFIX prompt → create_agent(...) →      │  │
│  │   .with_config({recursion_limit: 9999, ls_integration:deepagents}) │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────┬──────────────────────────────────────────┘
                                 │ CompiledStateGraph  (same type as LangGraph)
                                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ DATA PLANE  (untrusted token stream — model proposes, tools/VFS dispose)  │
│                                                                           │
│  messages + assembled system + tool schemas → model → final | tool_calls  │
│  Middleware (not a custom Pregel scheduler) may add/remove tools, inject  │
│  prompt sections, compact history, write typed state, enforce FS perms    │
│  *before* a built-in FS tool runs. A callable in tools= cannot rewrite    │
│  the tool list or prompt *before* the model call.                         │
│                                                                           │
│  ┌────────────── TOOL PROXIES (least privilege — not an omnibus shell) ─┐ │
│  │ FS: ls read_file write_file edit_file glob grep delete               │ │
│  │ execute (sandbox protocol only; else error string)  eval (QuickJS)   │ │
│  │ task (GP + declarative SubAgent)   MCP/custom on tools= (additive)   │ │
│  │ permissions= covers built-in FS only — NOT MCP, NOT execute, NOT     │ │
│  │   direct backend.*   Identity from verified auth / context_schema    │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└─────────┬───────────────┬─────────────────┬─────────────────┬─────────────┘
          │               │                 │                 │
          ▼               ▼                 ▼                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE LAYER  (two LangGraph systems + VFS lifetimes)                │
│                                                                           │
│  ┌──────────────┐ ┌──────────────┐ ┌─────────────┐ ┌──────────────────┐  │
│  │ Checkpointer │ │ Store        │ │ VFS backends│ │ Sandbox / Hub    │  │
│  │ thread_id    │ │ cross-thread │ │ State (dflt)│ │ remote container │  │
│  │ messages,    │ │ required for │ │ StoreBackend│ │ idle_ttl e.g.    │  │
│  │ interrupts,  │ │ StoreBackend │ │ Filesystem* │ │ 3600s (docs ex.) │  │
│  │ time-travel  │ │              │ │ Composite   │ │ ContextHubBackend│  │
│  └──────────────┘ └──────────────┘ └─────────────┘ └──────────────────┘  │
│  InMemorySaver = RAM. PostgresSaver: thread_id < 255 chars.               │
│  StateBackend files checkpointed every step — do not write large blobs.   │
│  *FilesystemBackend / LocalShellBackend: forbidden in deployed agents.    │
│  LangSmith Agent Server / Managed Deep Agents provision checkpointer+store│
└───────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not couple):**

| Plane | Lives here | LLM-free? | Failure if coupled |
| --- | --- | --- | --- |
| **Control** | `create_deep_agent` kwargs, `HarnessProfile` / `ProviderProfile`, middleware graph, `permissions`, `interrupt_on`, checkpointer/store handles, `context_schema` | Yes for assembly. PDP-style allow/deny is in middleware/backends, **not** in the model | Putting allow/deny in the system prompt; treating `context_schema` as authn |
| **Data** | User messages, tool results, VFS bytes, memory files, skill bodies, model completions, traces | No — untrusted token stream | Letting the model pick `user_id` or a store namespace |

`create_deep_agent(...)` returns a `CompiledStateGraph[...]` — composition is first-class: drop a deep agent into a larger `StateGraph` as a node, or pass a compiled LangGraph as a `CompiledSubAgent`.

**Request-flow narrative (`create_deep_agent` → middleware stack → LangGraph invoke/stream):**

1. **Control / construction.** Application calls `create_deep_agent`. `graph.py` in order: (1) resolve chat model + `ProviderProfile` / `HarnessProfile`; (2) resolve backend (`StateBackend()` if omitted); (3) assemble the main-agent middleware stack; (4) build the default `general-purpose` subagent and any caller `subagents`; (5) compose the authored system prompt `USER` → `BASE` → `SUFFIX`; (6) call `langchain.agents.create_agent(...)`; (7) bind `.with_config({recursion_limit: 9_999, metadata: {ls_integration: "deepagents", ...}})`.
2. **Invoke contract.** Production always passes `thread_id` in `config["configurable"]` **and** a `context_schema` instance (`user_id`, flags). They are independent. SDK `client.threads.create()` owns `thread_id` on managed deployments. Self-hosted without checkpointer/store is **ephemeral**: one `invoke`, no resume, no HITL.
3. **Data plane loop.** LangGraph drives turns. Each turn the model sees message history + assembled system + the current tool surface. It returns a final message or tool calls. Tool results append to state. Middleware may compact, offload, patch dangling `tool_calls`, or interrupt **before** the next model call. The loop is not a custom Pregel scheduler — Deep Agents changes the loop **through middleware**.
4. **Tool proxy.** Built-in FS tools hit the backend; `permissions` first-match inside `FilesystemMiddleware`. `task` fans out to a nested graph (isolated conversation unless experimental `mode="fork"` on 0.7.12). `execute` is real only if the backend implements `SandboxBackendProtocol`; otherwise an error string. MCP/custom tools on `tools=` are **additive** and **not** covered by `permissions=`.
5. **Persistence.** Each super-step checkpoints (plus per-task `checkpoint_writes` so succeeded siblings are not re-run). `DeepAgentState.messages` uses a **`DeltaChannel`** reducer (`langgraph>=1.2`, beta) so growth stays linear. 0.7.8: `files` channel only for state backends. Store namespaces are a **prompt-injection vector** if user A can write what user B reads.
6. **Stream / observe.** Typed projections for messages, tools, values, output; Deep Agents adds `stream.subagents` so each `task` has its own handle. `useStream` locally targets `http://localhost:2024`; production points at the LangSmith deployment with reconnection. Extra summarization LLM calls appear with `metadata.lc_source == "summarization"`.
7. **Stop.** Model stops calling tools, or `GraphRecursionError` at the bound 9,999 super-steps (hard error, not a graceful NFR degrade), or HITL interrupt (requires checkpointer).

**Capability buckets (topology only — later modules hang here):**

| Bucket | What ships | Default on? |
| --- | --- | --- |
| **Execution environment** | Custom tools + MCP (additive; catalog topic 09; Zero-Trust §4.4); VFS tools; `delete` (`>=0.7`); `execute` (sandbox only); optional QuickJS `eval` (`deepagents[quickjs]` → `langchain-quickjs>=0.3.5`) | FS yes; `execute` only if sandbox protocol; interpreter opt-in |
| **Context management** | Skills (progressive disclosure), memory (`AGENTS.md` always loaded), summarization, large-result offload (**20,000** tokens), prompt caching | Summarization + offload always; skills/memory if kwargs set; Anthropic cache middleware always registered (no-op off-provider) |
| **Delegation** | `task` + auto GP subagent; opt-in `TodoListMiddleware` / `write_todos` | GP **on** unless profile `enabled=False` + no sync `subagents`. Todos **off** since 0.7 |
| **Steering** | `interrupt_on` → `HumanInTheLoopMiddleware`; filesystem `permissions` | Off unless configured. Permission `mode="interrupt"` auto-installs HITL (`>=0.6.8`) |

`read_file` is **mandatory** in any `FilesystemMiddleware(tools=...)` allowlist — omitting it raises `ValueError` (`>=0.7`). `delete` is auto-hidden when the backend cannot delete. Skills: frontmatter at startup, body on demand. Memory: always in the system prompt. Last skill source wins on **name** collision.

---

### 2. Core Mechanics & Algorithms

#### 2.1 Invariants (harness, not a scheduler)

**I1.** Deep Agents introduces **no** new runtime. Durable execution, streaming, interrupts, checkpoints, stores = LangGraph. The loop shape is `create_agent`’s ReAct-style graph.

**I2.** `tools=` is **additive**. It never removes a built-in. Hide with `HarnessProfile.excluded_tools` or `FilesystemMiddleware(tools=[...])` (must keep `read_file`).

**I3.** `_REQUIRED_MIDDLEWARE = (FilesystemMiddleware, SubAgentMiddleware)`. `excluded_middleware` on either → **`ValueError`**. Filesystem “backs every built-in file tool and now also enforces `permissions`”; SubAgent “backs the `task` tool handler.” Profiles docs still mention a third “internal permission middleware”; in 0.7.x source, permissions are folded into `FilesystemMiddleware`. Invariant: **cannot strip permissions scaffolding**.

**I4.** `permissions=` is a **path PDP for built-in FS tools only**, first-match-wins, **no match → allow** (fail-open). Does not cover MCP, custom tools, `execute`/shell, or direct `backend.*`.

**I5.** Subagent `permissions` **replace** the parent list. Declarative `SubAgent.system_prompt` is **required** and does **not** inherit. `CompiledSubAgent` / `AsyncSubAgent` do **not** inherit `interrupt_on`.

#### 2.2 Layer choice (complexity of the *product*, not of Pregel)

| Layer | Package / entry | Job | Control vs opinion |
| --- | --- | --- | --- |
| **Runtime** | LangGraph | Durable execution, streaming, interrupts, checkpoints, stores | Most control, least opinion |
| **Framework** | `create_agent` | Model + tools + middleware → ReAct-style loop | Minimal harness |
| **Harness** | `create_deep_agent` | VFS, subagents, summarization/offload, skills, memory, profiles, HITL wiring | Least control of loop shape, most context-engineering defaults |

LangChain’s published rule of thumb: **start with Deep Agents**; drop to `create_agent` or LangGraph when you need to own the harness or the graph shape. Agency vs determinism: LangGraph maximal determinism (policy in **edges**); `create_agent` non-deterministic loop + middleware hooks; Deep Agents maximal agency (long run + fan-out because summarization/subagents exist). Middleware injects the few deterministic gates without leaving the loop.

**Complexity [architecture, not a paper]:** one turn is one model call + \(k\) tool executions. Super-step count is unbounded except `recursion_limit`. Unused built-in tools still send **full JSON schemas every turn** — \(O(\text{schema tokens})\) per call, paid even when unused. Offload is \(O(1)\) path swap above 20k tokens. Summarization is an extra LLM call at 85% of window. GP isolation trades main-context tokens for a **second full prefix**.

#### 2.3 Default middleware order (API reference is source of truth)

Customization docs have a numbering glitch (`HumanInTheLoopMiddleware` listed as a restarted “1.” after Memory). Trust the `create_deep_agent` `middleware=` docstring.

**Bare stack** (only `model=`; GP still auto-added): `FilesystemMiddleware` → `SubAgentMiddleware` → `SummarizationMiddleware` → `PatchToolCallsMiddleware` → prompt-caching middleware → profile extras / `_ToolExclusionMiddleware`.

**Full stack**, first → last:

| # | Slot | When present |
| --- | --- | --- |
| 1 | `SkillsMiddleware` | `skills=` set. **Before** filesystem so skill metadata exists before file tools |
| 2 | `FilesystemMiddleware` | Always. Permissions live here |
| 3 | `SubAgentMiddleware` | ≥1 synchronous subagent (including auto GP) |
| 4 | `SummarizationMiddleware` | Always (`create_summarization_middleware`) |
| 5 | `PatchToolCallsMiddleware` | Always. Repairs dangling tool calls after interrupt/cancel/malformed args. **Before** caching so the cached prefix matches repaired history |
| 6 | `AsyncSubAgentMiddleware` | Async `subagents` present |
| 7 | **Caller `middleware=`** | After Patch. `.name` match **replaces in place** (`>=0.7`); else appends here |
| 8 | Profile `extra_middleware` | Resolved `HarnessProfile` |
| 9 | `_ToolExclusionMiddleware` | Profile `excluded_tools` |
| 10 | `AnthropicPromptCachingMiddleware` | **Unconditional**; `unsupported_model_behavior="ignore"` |
| 11 | `BedrockPromptCachingMiddleware` | If `langchain-aws` installed; no-op off-Bedrock |
| 12 | `FireworksPromptCachingMiddleware` | If `langchain-fireworks` installed; session affinity from `thread_id` |
| 13 | `MemoryMiddleware` | `memory=` set. **After** caching so AGENTS.md updates are less likely to bust the prefix |
| 14 | `HumanInTheLoopMiddleware` | `interrupt_on=` set, or any permission `mode="interrupt"` (user `interrupt_on` wins per tool name) |

After assembly, `excluded_middleware` is filtered. Unmatched name → `ValueError`. Scaffolding exclusion → `ValueError`. String matching **multiple distinct classes** → `ValueError` (force class-form). `module:Class` import refs resolve lazily and **import Python code** (trusted config only).

**Synchronous subagent stack:** (a) no nested `SubAgentMiddleware` (only the parent exposes `task`); (b) skills run **after** `PatchToolCallsMiddleware` on inner agents. GP inherits main-agent **default-middleware overrides** (same `.name` replacements) but not main-only extras. Declarative `subagents=` do **not** inherit main middleware customization — pass overrides on the spec.

`TodoListMiddleware` is **not** in this stack as of 0.7. Restore with `middleware=[TodoListMiddleware()]`. Not inherited by declarative subagents unless they opt in. GP mirrors the caller’s exact instance **by identity** when present.

#### 2.4 `HarnessProfile` / `ProviderProfile`

Profiles package **per-provider or per-model** overlays so call sites stay stable. Built-ins ship for OpenAI and Anthropic/Claude. YAML/JSON: `HarnessProfileConfig`. Runtime-only fields (middleware instances, factories, class-form `excluded_middleware`) stay on `HarnessProfile`.

| Field | Effect |
| --- | --- |
| `base_system_prompt` | Replaces BASE in `USER → BASE → SUFFIX` |
| `system_prompt_suffix` | Always last; applied to main, declarative subagents, and auto GP |
| `tool_description_overrides` | Per-tool description map |
| `excluded_tools` | Post-injection **name** filter; can drop user tools **and** harness tools. As of **0.7.9** also blocks **execution** |
| `excluded_middleware` | Classes (exact type) or strings (`AgentMiddleware.name`) or `module:Class` |
| `extra_middleware` | Appended at slot 8 |
| `general_purpose_subagent` | `enabled` / rename / re-prompt. GP-specific `system_prompt` **wins** over profile `base_system_prompt` so an orchestrator prompt does not leak into the researcher GP |

**Lookup** for a preconfigured model instance: (1) exact `provider:identifier`; (2) identifier-only if it already contains `:`; (3) provider-only fallback. Keys: `"openai"` vs `"openai:gpt-5.5"`. Model-level merges onto provider-level; unset fields inherit; explicit model-level wins. Re-registering **merges**, does not replace. No wildcard-all-providers key.

**Merge:** prompt fields last-write-wins if set; tool-description maps merge per key; `excluded_tools` / `excluded_middleware` are **set union**; `extra_middleware` merge-by-name; GP profile field-wise.

**Load order:** built-ins → `importlib.metadata` entry points (`deepagents.harness_profiles` / `deepagents.provider_profiles`) → user `register_*_profile`. All three additive.

`ProviderProfile` is narrower: `init_kwargs` / `pre_init` / `init_kwargs_factory` for `init_chat_model`. Applies only to `provider:model` **strings**, not to a prebuilt `BaseChatModel`.

`HarnessProfileConfig.from_harness_profile` → `ValueError` if `extra_middleware` is non-empty or excluded classes live in `__main__` / a function scope.

#### 2.5 `excluded_tools` vs `excluded_middleware` (interview fork)

| Intent | Correct knob | Wrong knob |
| --- | --- | --- |
| Hide FS tools from the **model** but keep scaffolding (offload, permissions, skills/memory still need a VFS) | `excluded_tools={"ls", "read_file", ...}` **or** `FilesystemMiddleware(tools=["read_file", "ls", ...])` | `excluded_middleware={"FilesystemMiddleware"}` → **`ValueError`** |
| Run with **no** `task` tool | Profile `general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)` **and** `subagents=` empty of sync specs. Then `SubAgentMiddleware` is not attached. Async subagents unaffected | `excluded_middleware={"SubAgentMiddleware"}` → **`ValueError`** |
| Drop summarization | `excluded_middleware={"SummarizationMiddleware"}` (public alias) | — |
| Stop offering `execute` | `excluded_tools={"execute"}` (and/or non-sandbox backend, which already errors) | Removing FS middleware |

Pre-0.7.9, hiding a tool from the model while leaving it callable was a realistic footgun. **Pin `>=0.7.9`** if exclusion is a control. `tools=` expecting to **remove** a built-in is always wrong. Overriding `FilesystemMiddleware` without passing `backend=` / `permissions=` is **not merged** — you drop the outer wiring.

#### 2.6 Prompt assembly after v0.7

v0.7 cut the hidden base system prompt (#4859) and trimmed builtin tool descriptions **43%** (#5009). Combined with opt-in todos, **base input tokens** on a default-agent turn dropped **65% (~6k → ~2k)**. “Base input tokens” = builtin prompt + tools + middleware, **not** the user task.

Assembly: caller `system_prompt` **first**; profile BASE only if set; suffix **last**. Empty caller + empty profile → **empty authored system prompt**; the model still receives tool schemas and middleware-injected sections. Passing `SystemMessage` preserves Anthropic `cache_control` markers.

> ⚠️ Gap: the Context engineering page still says “Your custom system prompt is prepended to the built-in system prompt, which includes guidance for filesystem tools and subagents.” That sentence is **stale relative to v0.7 + the API reference**. Middleware still injects tool-usage text; there is no longer a large hidden SDK preamble.

| Stack | Prompt resolution |
| --- | --- |
| Main | `USER` (`system_prompt=`) → profile `base_system_prompt` → profile suffix |
| Declarative subagent | Authored `system_prompt` → that subagent’s resolved profile BASE/suffix. Suffix-only profile **appends**. A `base_system_prompt` **replaces** the subagent authored base. Each subagent **re-resolves** the profile against **its own** model |
| Auto GP | `general_purpose_subagent.system_prompt` if set → else profile `base_system_prompt` → else SDK GP default; then profile suffix |

Dynamic per-run instructions belong in `@dynamic_prompt` middleware, not in a rebuilt graph. Runtime model swap without rebuild: `context_schema` + `@wrap_model_call` calling `init_chat_model` per request. Deep Agents does **not** ship a complexity router — that is application middleware.

#### 2.7 `recursion_limit`: 9,999 is a sentinel dodge

`create_deep_agent` binds `recursion_limit: 9_999` on the returned graph. Bare LangGraph default in the #1698 report is **25** super-steps. Frontend docs still say set `recursionLimit: 10000` for heavy subagent UIs.

**Why 9,999 not 10,000:** LangGraph `merge_configs` has historically **dropped** `recursion_limit` when it equals `DEFAULT_RECURSION_LIMIT` (10000), so `with_config({"recursion_limit": 10000})` was a no-op and nested graphs fell back to 25 **[inferred from graph.py bind + LangGraph issue #7314]**.

Issue #1698 (`deepagents==0.4.4`, closed 2026-03): subagents invoked **without** parent `config`, ran at 25, hit `GraphRecursionError` at exactly 25 steps, surfaced upstream as `CancelledError`, cancelled sibling `asyncio.gather` tasks. Later comments: parent `callbacks`/`tags`/`configurable` are forwarded; `recursion_limit` and `metadata` are **intentionally not** copied so the subagent’s bound config wins. Do not assume a parent `invoke(..., config={recursion_limit: 300})` still raises the subagent ceiling — verify on the pinned version.

`recursion_limit` is a **graph super-step ceiling**, not “max `task` calls.” Product hop caps belong in application state / `ModelCallLimitMiddleware`. Hitting 9,999 is a **hard error**.

#### 2.8 Version gates that change harness shape

| Version | Behavioral gate |
| --- | --- |
| `>=0.5.0` | `rt.server_info` / `rt.execution_info` namespace factories for StoreBackend |
| `>=0.5.2` | `permissions=` |
| `>=0.5.3` | `model=None` (implicit `claude-sonnet-4-6`) **deprecated**; removed in `deepagents==1.0.0` |
| `>=0.6.8` | Permission `mode="interrupt"` |
| **`>=0.7.0`** | Todos opt-in; `delete` tool; `FilesystemMiddleware(tools=...)` allowlist; `.name` middleware override; backend factories removed |
| `>=0.7.3` | Exact-match `delete` first-match-wins |
| `>=0.7.6` | Offload conversation history to a distinct session ID when summarizing |
| `>=0.7.8` | `files` state channel only for state backends |
| `>=0.7.9` | `excluded_tools` also blocks **execution**; tracing inputs disabled on middleware |
| `0.7.12` | Subagent conversation forking (experimental). `system_prompt` appended; cannot define `skills`. Undoes isolation if used casually |

**Todos rationale:** across three eval categories × three models, slightly **better reward and lower cost with todos off**. Do not enable “for completeness” on capable models.

#### 2.9 Offload / summarization state machine

| Knob | Default |
| --- | --- |
| Tool-result / large-arg offload | **20,000** tokens; substitution = path + preview of **first 10 lines** |
| Summarization trigger (profile with `max_input_tokens`) | `("fraction", 0.85)` |
| Keep after summarize | `("fraction", 0.10)` |
| Fallback if no profile | trigger `("tokens", 170000)`, keep `("messages", 6)` |
| Bare LangChain `SummarizationMiddleware` (not the DA factory) | `trigger=None`, `keep=("messages", 20)` |
| Immediate fallback | `ContextOverflowError` → summarize + retry |
| History offload path | `/conversation_history` |
| `grep` match cap (0.7 FS perf) | **1,000** matches; `truncated` flag |

Offload of **write/edit inputs** is delayed until the session crosses 85% of window; results over 20k offload **immediately**. Summarization writes a filesystem canonical record plus an in-context summary. `create_summarization_tool_middleware` adds on-demand `compact_conversation` without disabling the 85% auto path. The Deep Agents factory picks fraction defaults from `model.profile["max_input_tokens"]`; stock LangChain summarization does not.

v0.7 `write_file` **overwrites** instead of erroring — a confused agent can clobber artifacts. `grep`/`glob` return **partial** results with `truncated`; agents that treat “no more matches” as “complete search” **under-recall** on large trees **[inferred]**.

#### 2.10 Models the docs actually show

Quickstart strings: `google_genai:gemini-3.6-flash`, `openai:gpt-5.5`, `anthropic:claude-sonnet-4-6`, `openrouter:z-ai/glm-5.2`, `fireworks:accounts/fireworks/models/glm-5p2`, `baseten:zai-org/GLM-5.2`, `ollama:north-mini-code-1.0`. Customization also: Azure `azure_openai:gpt-5.5`; Bedrock `anthropic.claude-sonnet-4-6` + `model_provider="bedrock_converse"`. Any tool-calling LangChain chat model, including vLLM / llama.cpp.

`openai:` strings use the **Responses API by default**. Chat Completions: `init_chat_model("openai:...", use_responses_api=False)`. Disable Responses data retention: `use_responses_api=True, store=False, include=["reasoning.encrypted_content"]`.

Published eval suite (**not** a production SLA):

| Model | Overall | File Ops | Retrieval | Tool Use | Memory | Conversation | Summarization |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `google_genai:gemini-3.6-flash` | 82% | 100% | 100% | 90% | 54% | 38% | 80% |
| `openai:gpt-5.5` | 80% | 92% | 100% | 84% | 64% | 52% | 80% |
| `openai:gpt-5.4` | 18% | 100% | 100% | 18% | 51% | 38% | 100% |
| `anthropic:claude-opus-4-7` | 80% | 100% | 100% | 82% | — | 48% | 100% |
| `anthropic:claude-opus-4-6` | 26% | 92% | 100% | 26% | 69% | 22% | 100% |
| `openrouter:z-ai/glm-5.1` | 89% | 92% | 100% | 89% | — | 33% | 80% |
| `fireworks:.../glm-5p1` | 81% | 100% | 100% | 87% | — | 33% | 80% |

`gpt-5.4` at **18%** overall with **100%** file ops is the interview trap: a model can ace FS and still fail tool-use. Suggested-models list is “eval-suite necessary-but-not-sufficient.”

---

### 3. Token Economics & NFR Analysis

> ⚠️ Gap: LangChain does **not** publish p50/p95/p99 of `create_deep_agent` end-to-end, nor a tokens/sec harness benchmark, nor concurrent agents per replica, nor sandbox cold-start, nor p99 of `task` fan-out. Caching **prices** and **token-footprint** numbers below are from named vendor/docs pages. `$ per 1k runs` is **[inferred]**. Latency percentiles are architecture-derived **[inferred] policy targets**. Measure with LangSmith traces on the target model; do not cite this table as a vendor SLO.

#### 3.1 Harness token tax (measured)

v0.7 eval vs `0.6.12`:

- Base input tokens **~6k → ~2k** (−65%) on a default-agent turn.
- Matrix: three categories (autonomous, conversational, long-context) × four models (`gpt-5.6-luna`, `gemini-3.6-flash`, `claude-sonnet-4-6`, `claude-opus-4-8`).
- Reward CIs span zero for every model (no statistically significant quality drop). Luna: **−34% tokens, −15% cost, +4% reward** (statistically clear token and cost reductions). `claude-sonnet-4-6` was the exception: cost **increased** on two hard autonomous tasks (trace analysis, not a harness-token regression).

Unused built-in tools still send full JSON schemas every turn. `excluded_tools` shrinks baseline prompt size without waiting for offload/summarization.

#### 3.2 Prompt-caching meters (published unit prices)

Deep Agents **auto-applies** prompt caching to static system sections for Anthropic and Amazon Bedrock. “No configuration is required.” Implementation: tail-stack middleware, after Patch and caller middleware, so the cached prefix matches the bytes actually sent. Default Anthropic TTL in Deep Agents override docs: **`5m`**. Replace in place with `AnthropicPromptCachingMiddleware(ttl="1h")` for long gaps.

Multipliers (current Claude models except Fable/Mythos 5.1 footnote): **5m write = 1.25×** base input; **1h write = 2×**; **read = 0.1×**. Below minimum prefix length, cache markers are silently ignored.

| Model (docs string) | Input | 5m write | 1h write | Cache read | Output |
| --- | --- | --- | --- | --- | --- |
| Claude Sonnet 4.6 | $3 | $3.75 | $6 | $0.30 | $15 |
| Claude Sonnet 5 | $2 | $2.50 | $4 | $0.20 | $10 |
| Claude Opus 4.6 / 4.7 / 4.8 / 5 | $5 | $6.25 | $10 | $0.50 | $25 |
| Claude Haiku 4.5 | $1 | $1.25 | $2 | $0.10 | $5 |

USD per million tokens. US-only inference **1.1×**. Batch API **50%** off. Sonnet 5: the $2/$10 “introductory through 2026-08-31” price is now **standard**; the planned 2026-09-01 bump to $3/$15 **did not occur**. Fable/Mythos 5.1 cache **reads** are 0.025× input, not 0.1×.

Bedrock: explicit checkpoints; TTL **5 minutes** (many models) or **5m and 1h** for listed Claude IDs; Nova often **5m only**. Min tokens examples: Claude Sonnet 4.6 **1,024**; Opus 4.6 **4,096**; Opus 5 **512**; Haiku 4.5 **4,096**. Max **4** checkpoints/request on listed Claude. Not supported on Bedrock **batch**. AWS: “Support for prompt caching doesn't guarantee a cache hit.” Automatic top-level `cache_control` is **not** supported on legacy Amazon Bedrock (Opus 4.6 and earlier): API returns 400.

Fireworks: session affinity from `config.configurable.thread_id` (`prompt_cache_key` / `x-session-affinity`). Cross-provider `ModelFallbackMiddleware` **strips** Fireworks-only cache headers before a non-Fireworks fallback.

#### 3.3 `$ cost per 1k runs` **[inferred]**

Assumptions (not a vendor SKU):

- Model: `anthropic:claude-sonnet-4-6` at list prices above.
- Task: medium research run, **10** model calls, all inside one 5-minute window (cache stays warm).
- GP subagent **disabled**.
- v0.7 harness prefix: **2,000** tokens cached (tools + empty authored prompt).
- Dynamic uncached tokens per call: **3,000**.
- Output: **800** tokens/call.
- Cache: 1× 5m write of the 2k prefix + 9× reads of the same 2k. Dynamic 3k never cached.

| Component | Tokens × unit | USD / run |
| --- | --- | --- |
| Cache write | 2,000 × $3.75 / 1e6 | $0.00750 |
| Cache reads | 9 × 2,000 × $0.30 / 1e6 | $0.00540 |
| Uncached input | 10 × 3,000 × $3 / 1e6 | $0.09000 |
| Output | 10 × 800 × $15 / 1e6 | $0.12000 |
| **Total / run** | | **$0.2229** |
| **Total / 1,000 runs** | | **$223** |

Same run **without** prompt caching (5,000 input × 10 × $3/MTok + same output) = **$0.270 / run → $270 / 1k**. Caching saves ~**$47 / 1k** at a 2k prefix because v0.7 already shrank the static tax.

If `memory=` + skills inflate the cached prefix to **20,000** tokens (still 10 calls, 5m TTL) **[inferred]:**

| Path | / run | / 1k runs |
| --- | --- | --- |
| Cached (1 write + 9 reads of 20k + 30k uncached + 8k out) | $0.339 | **$339** |
| Uncached (10 × 23k in + 8k out) | $0.810 | **$810** |

**Default GP subagent on:** one extra isolated 8-call subagent with the same 2k prefix roughly **+0.8–1.0×** the main-agent bill on that run **[inferred]** — isolation trades main-context tokens for a second full tool-schema prefix. LangChain does not publish a “GP subagent multiplier.” Disable it for short tool-calling bots.

Todos: LangChain’s own evals found **higher cost** with `write_todos` on, for no significant accuracy gain.

#### 3.4 Latency SLA — p50 / p95 / p99 numeric ms

> ⚠️ Gap: **Deep Agents publishes no p50/p95/p99, no harness RPM, and no tokens/sec.** Provider TPM/RPM are model-account limits, not harness limits. Summarization adds a **synchronous extra LLM call** at 85% window — tail spike, unpublished magnitude. Checkpointer durability modes trade latency vs crash-consistency — **no numbers**. GTM “~10k req/week” is a traffic shape, not a latency SLA.

Clock-split: (a) streaming TTFT of the **parent** model; (b) one ReAct cycle (model + local FS tool); (c) summarizer tax; (d) GP/`task` isolation (serial wait for the report); (e) checkpointer durability tax; (f) HITL — a **different clock**. Middleware assembly is construction-time (**0 ms** on the request path). FS permission checks and `PatchToolCalls` are local CPU, dominated by the model RTT.

**[inferred] policy targets — numeric ms.** Anchors: inner-chat TTFT histogram buckets used in the observability module (0.64 / 2.56 / 5.12 s); ReAct cycle class used in the feedback-loops module (2 s / 8 s / 20 s); extra LLM hop class (2 s / 6 s / 15 s). These are **not** Deep Agents measurements.

| Path | **p50** | **p95** | **p99** | Grounding / mitigation |
| --- | --- | --- | --- | --- |
| **Streaming TTFT, parent, no summarizer** **[inferred policy]** | **640 ms** | **2,560 ms** | **5,120 ms** | Stream; cache-warm prefix (5m TTL). Middleware hops are not a second LLM |
| **One ReAct cycle (model + StateBackend FS tool)** **[inferred]** | **2,000 ms** | **8,000 ms** | **20,000 ms** | Local VFS is not the tail; the model + provider queue is. Timeout the provider independently of the checkpointer |
| **Summarization extra LLM ON path** (85% window; `lc_source=summarization`) **[inferred]** | **2,000 ms** | **6,000 ms** | **15,000 ms** | Extra hop class. Exclude `SummarizationMiddleware` only for short bots that **cannot** fill the window. Override `.name` with a cheaper summarizer (Fireworks example in the v0.7 blog) |
| **GP subagent isolated 8-call, parent waits for report** **[inferred]** | **16,000 ms** | **64,000 ms** | **160,000 ms** | 8 × ReAct-cycle p50/p95/p99. Parallel `task` is `max()` of children, not `sum()`, **if** the runtime actually fans out — verify gather semantics (historical #694: one failure cancelled siblings) |
| **10-call research run, GP off, no summarize** **[inferred]** | **20,000 ms** | **80,000 ms** | **200,000 ms** | 10 × cycle. This is the cost-section shape, not a vendor SLO. Do not put it on a chat HTTP timeout |
| **Checkpointer `sync` extra per super-step** **[inferred policy]** | **10 ms** | **50 ms** | **200 ms** | Unpublished. Postgres fsync-class tax. Prefer `async` for p50; `sync` when HITL/crash-consistency is the product; `exit` only for scripts (intermediate state **lost**) |
| **Checkpointer `exit` extra on the hot path** **[inferred policy]** | **0 ms** | **0 ms** | **0 ms** | Persist only on graph exit/interrupt — not an SLO win if you needed resume |
| **HITL interrupt clock** **[inferred policy]** | **30,000 ms** | **180,000 ms** | **600,000 ms** | Seconds–minutes; durable queue; p99 = expire → **deny**, not auto-approve. Not a Chat Completions SLO |
| **Happy-path extra-tax if GP disabled + no summarize** **[inferred policy]** | **0 ms** | **0 ms** | **0 ms** | vs a `create_agent` 1–2 tool hop the *added* tax is schema tokens, not a second model. The **absolute** TTFT is still the parent model row |
| **`GraphRecursionError`** | — | — | **hard error** | 9,999 is a fuse, not a degrade. Product cap must fire **earlier** |

**Mitigations mapped to percentiles:**

- **p50 (user):** stream; Anthropic 5m cache on the 2k prefix; disable GP for short bots; `async` checkpointer; StateBackend only for tiny scratch (large files blow checkpoint I/O).
- **p95:** cheaper summarizer via `.name` replace; `compact_conversation` before you hit 85% if the UX is interactive; timeout `task` independently of the parent.
- **p99:** HITL off the request thread; summarizer + GP fan-out **are** the tail — measure with the **root** histogram plus `stream.subagents`; never wait on LangSmith export; product hop cap ≪ 9,999.

#### 3.5 Throughput / back-pressure

| Ceiling | Number | Effect |
| --- | --- | --- |
| Compiled `recursion_limit` | **9,999** | Super-step fuse. Hitting it is `GraphRecursionError`, not graceful degrade |
| Frontend `recursionLimit` copy | **10,000** | Sentinel risk if it equals LangGraph `DEFAULT_RECURSION_LIMIT` |
| Bare LangGraph default (issue #1698 era) | **25** | Historical subagent footgun |
| Offload threshold | **20,000** tokens | Immediate for large results |
| Summarize trigger / keep | **0.85 / 0.10** of window | Extra LLM on the tail |
| `grep` cap | **1,000** matches | Partial recall |
| GTM internal agent | ~**10k req/week**, **150** users, **26/74** interactive/ambient | Traffic shape on LangSmith Deployments — not your RPS |
| PyPI downloads | **5,646,660** / last month | Ecosystem, not capacity |
| Sandbox idle TTL (docs example) | **3,600 s** | Fault domain recycle |
| `LocalShellBackend.max_file_size_mb` | **10** | Dev-only backend |
| `ModelRetryMiddleware` | default `max_retries=2` (3 attempts) | Exponential backoff; `langchain>=1.3.16` skips non-retryable |
| Provider TPM/RPM | account limits | **The** throughput ceiling. Deep Agents does not wrap 429s |
| Concurrent `invoke` on one `thread_id` | unpublished | Treat as undefined unless the checkpointer documents optimistic CAS **[inferred]** |

**Back-pressure design:** (1) admit with a **product** hop/`task` cap and a $ budget — do not ship 9,999 as policy; (2) bulkhead **parent model** vs **summarizer** vs **subagent fleet** vs **sandbox pool** vs **checkpointer writes**; (3) disable GP + exclude `execute` for short tool-calling bots; (4) circuit on provider 429 so retries do not become a token amplifier; (5) sandbox OOM kills the **sandbox**, not the agent server (bulkhead-by-process); (6) StateBackend files are checkpointed every step — capacity-plan **checkpoint storage**, not just tokens; (7) `grep`/`glob` `truncated` is back-pressure from the VFS — do not loop until “complete.”

#### 3.6 NFRs and explicit trade-offs

| NFR | Production stance | Competes with |
| --- | --- | --- |
| **Availability of chat vs of the harness extras** | Product SLO is one parent loop. Summarizer / GP / HITL are **best-effort or a different clock**. Circuit-open on the frontier model → fallback `create_agent` or deterministic refuse — not a 500 with a 9,999-step retry | Quality of long-horizon work vs user p99 |
| **RPO of checkpointer** | Last super-step (`sync`) / last successful async persist / **exit-only** (intermediate **lost**). `InMemorySaver` RPO = **empty on restart**. LangSmith managed: they provision Postgres-class storage | Crash-consistency vs p50 (`sync` extra **10 / 50 / 200 ms [inferred]**) |
| **RTO of checkpointer** | Resume `thread_id` (+ optional `checkpoint_id` time travel). Replay **re-executes** nodes after that checkpoint — debugger, not audit. `update_state` creates a **new** checkpoint; reducers still apply. HITL requires a checkpointer or you cannot pause | Time-to-resume vs forensic truth |
| **RPO of store / memories** | Last Store put. Namespace by `(assistant_id, user.identity)` (recommended). Org namespace: keep **read-only** | Lifelong memory vs prompt-injection |
| **RTO of store** | Re-point Store; you cannot reconstruct a dropped namespace. Do not restore untrusted `/memories/` into the system prompt | Velocity of “lessons” vs safety |
| **RPO of VFS** | StateBackend = checkpointer. StoreBackend = store. Sandbox = provider TTL / snapshots (docs example `idle_ttl_seconds=3600`). Host `FilesystemBackend` survives process death **and is forbidden in deployed agents** | Scratch convenience vs tenancy |
| **RPO of traces** | Sampled LangSmith is lossy by policy. 0.7.9 disabled tracing **inputs** on middleware — you already lost that tape | Debug vs PII |
| **Compliance** | **Not provided by `deepagents`.** LangSmith Enterprise + your IdP. SOC2/HIPAA: traces, checkpoints, and VFS bytes are subprocessors if they hold prompts. OpenAI Responses **stores by default** — `store=False` + encrypted reasoning. GDPR erasure of a thread is checkpointer+store+sandbox+trace purge, not `thread_id` TTL | Time-to-debug (content-on) vs residency |
| **Correctness vs $** | v0.7 −65% base tokens with reward CIs spanning zero. Todos **on** cost more. GP on is a second prefix. `gpt-5.4` 18% overall on the DA eval suite is a wrong default despite 100% file ops | Schema tax vs agency |

**RPO/RTO [inferred from architecture, not vendor RPO SKUs]:** RPO_checkpoint = last durable super-step (`sync`: before next step; `async`: small loss window; `exit`: empty if crash mid-run; InMemory: empty). RTO_checkpoint = resume `thread_id` (seconds) vs “we used InMemory in prod” (**cannot restore**). RPO_sandbox = last snapshot / TTL expiry. RTO_sandbox = new container; agent server must survive guest OOM. A `GraphRecursionError` is a **completed refuse**, not an RPO hole — log it.

---

### 4. Distributed Resilience & Security

#### 4.1 Durable execution: LangGraph checkpointer / store (no Temporal wrapper)

Deep Agents does **not** wrap a second workflow engine. “Durable execution” **is** LangGraph + a real checkpointer.

LangGraph writes a checkpoint at each super-step; additionally persists **per-task writes** so sibling nodes in the same super-step that already succeeded are not re-run (`checkpoint_writes`). `thread_id` is the primary key; without it the checkpointer cannot save or resume after an interrupt. Optional `checkpoint_id` selects a historical snapshot. Subgraphs have their own checkpoint **namespace** — delegated files in a **StateBackend** are not automatically cross-thread memory. Share via Store or configure the subgraph to write to the parent checkpoint.

| Mode | Persist when | Crash mid-run |
| --- | --- | --- |
| `exit` | Only on graph exit / interrupt | Intermediate state **lost** |
| `async` | Async while next step runs | Small window of loss |
| `sync` | Before next step | Highest durability, extra latency |

HITL **requires a checkpointer**; permission-interrupt examples use `InMemorySaver()` (tests, not prod). Cancel-before-tool-return is repaired by `PatchToolCallsMiddleware` so resumed graphs do not have dangling `tool_calls`.

Libraries: `langgraph-checkpoint` (`InMemorySaver`); `langgraph-checkpoint-sqlite`; `langgraph-checkpoint-postgres` (what LangSmith uses). Async: `AsyncSqliteSaver` / `AsyncPostgresSaver`.

**CompositeBackend (canonical production pattern):** default route = thread `StateBackend`; named prefixes map elsewhere. `/memories/` → `StoreBackend(namespace=lambda rt: (rt.server_info.assistant_id, rt.server_info.user.identity))`. `rt.server_info` factories require `deepagents>=0.5.0`. Graph factories for per-thread sandboxes **do not** receive full `Runtime` — read `thread_id` / `assistant_id` from `config["configurable"]`.

| Need | Minimum wiring |
| --- | --- |
| Script / unit test | Default `StateBackend`, no checkpointer |
| Multi-turn chat, restart-safe | `checkpointer=PostgresSaver` + `thread_id` |
| HITL | Checkpointer **required** |
| Cross-thread memory | `store=` + `CompositeBackend` `/memories/` route |
| `StoreBackend` without `store=` | Construction/runtime failure |
| Multi-tenant isolation | Namespace factory on `(assistant_id, user.identity)` plus LangSmith authz filters |

#### 4.2 Failure taxonomy

| Class | Examples | Detection | Handling |
| --- | --- | --- | --- |
| **Transient** | Provider 429/5xx, TPM, checkpointer blip, sandbox allocate queue, summarizer 5xx | Error rate; p99 latency window | Full-jitter retries on **idempotent** reads; `ModelRetryMiddleware` `max_retries=2`; **do not** retry `write_file`/`edit_file`/`delete`/`execute` without an idempotency key (v0.7 `write_file` **overwrites**) |
| **Permanent** | `ValueError` at construction (excluded scaffolding / unmatched name / `read_file` omitted / `StoreBackend` without `store`); `GraphRecursionError`; 4xx auth; schema mismatch | Non-retryable | Fail closed to `create_agent` or deterministic refuse. Never “strip FilesystemMiddleware and retry” |
| **Poison-pill tools** | Hallucinated FS paths (fail-open if no rule); hallucinated tool args; MCP tools that touch disk outside `permissions=`; `execute` advertised on a sandbox; `LocalShellBackend` in prod; experimental `fork` undoing isolation | Permission denials; backend errors; MCP `toolSurfaceHash` drift (§4.4) | deny-by-rule **before** allow `/workspace/**`; pin `>=0.7.9` for `excluded_tools`; never host shell; gateway hash-pin MCP |
| **Poison-pill memory / store** | Shared assistant/org namespace; skill name collision (last source wins); sleeper writes into `/memories/` | Unexpected cross-user reads | User namespace; org policies **read-only**; deny-write `/memories/**` from the agent |
| **Poison-pill traces** | PII in LangSmith when `LANGSMITH_TRACING=true`; checkpoints holding `.env` bytes | DLP on the three sinks | detect → redact → audit **before** persist (4.5) |
| **Idempotency** | Two `write_file` on resume; HITL approve then mutated args (TOCTOU) | Duplicate side effects; hash mismatch | Idempotency keys on mutating tools; re-hash at execute. Deep Agents does **not** publish a binding/hash of approved HITL args — treat as **[inferred] gap** |
| **Fan-out cancellation** | Historical: 25-step subagent → `CancelledError` → `asyncio.gather` siblings die; related #694 | Sibling tasks cancelled | Verify gather semantics on the pinned version; cap `task` fan-out in application state |
| **Denial of wallet** | Accidental GP spawn; todos on; 9,999-step loop; summarizer storm | Token ledger; step count | Disable GP; todos off; product cap; breaker on retry loops |

#### 4.3 Circuit breaker closed → open → half-open

> ⚠️ Gap: **`deepagents` does not ship circuit breakers, leader election, or a token-bucket.** Those are LangGraph/provider/application concerns. Put breakers in caller `middleware=` (slot 7) or around `graph.invoke`. Sandbox isolation is a **fault domain** (guest OOM), not a circuit breaker.

Independent breakers: **parent model**, **summarizer model**, **subagent model**, **checkpointer**, **store**, **sandbox pool**. A summarizer 429 must not stall a short chat (**bulkhead**) **and** must not strip `FilesystemMiddleware`.

```
        provider 429/5xx | checkpointer timeout | sandbox pool empty | error-rate window
  ┌──────────┐  ─────────────────────────────────────────────────►  ┌──────────┐
  │  CLOSED  │                                                       │   OPEN   │
  │  invoke  │  success resets consecutive count                     │ FAIL FAST│
  └────┬─────┘                                                       │ fallback │
       ▲                                                             │ chain    │
       │ probe OK                                                    └────┬─────┘
       │                                                                  │ cooldown
       │                                                            ┌─────▼──────┐
       └──────────── probe allow ───────────────────────────────────│ HALF-OPEN  │
                    probe fail → stay OPEN                          │ 1 synthetic│
                                                                    │ probe; stay│
                                                                    │ OPEN if fail│
                                                                    └────────────┘
```

**Thresholds [policy, not vendor SLO]:**

| Trip condition | Closed → open | Half-open probe | Fallback (**not** “strip the harness”) |
| --- | --- | --- | --- |
| Parent model 429/5xx | consecutive ≥ **5** or error-rate window | One tiny `invoke` with GP off | **Deep Agents → `create_agent` → deterministic refuse** |
| Summarizer 5xx | error-rate + p99 | Synthetic compact | Skip summarizer for this turn if window < 85%; **never** drop FS scaffolding |
| Checkpointer timeout | consecutive ≥ **3** | One checkpoint write | Fail closed for HITL; ephemeral refuse for “must resume” products; scripts may continue with `exit` durability |
| Sandbox pool empty | allocate 503 | One allocate | Queue or 503 — **never** `LocalShellBackend` |
| Store down | put/get errors | One KV get | Disable `/memories/` writes; keep thread StateBackend |

**Fallback chain (required interview answer):** **Deep Agents (full harness) → `create_agent` (thin harness, your tools only) → deterministic refuse.** Cross-provider model fallback must strip Fireworks cache headers. Never: model 429 → unsandboxed `execute`. Never: HITL timeout → auto-approve. Never: circuit open → `excluded_middleware` the filesystem.

#### 4.4 Zero-Trust MCP, tool-level RBAC, HITL vs PDP

Deep Agents’ own security policy is **“trust the LLM.”** The agent can do anything its tools allow. Boundaries belong at tool/sandbox/permission/**gateway** code, **not** in the prompt. The full MCP tool catalog is **topic 09**; Zero-Trust architecture is **this subsection** (Deep Agents tools/MCP docs + facts reused from [07-guardrails](07-guardrails.md)).

**Harness placement (why glob rules are not enough):** `tools=` is **additive**. Official path: `langchain-mcp-adapters` `MultiServerMCPClient.get_tools()` → `create_deep_agent(..., tools=...)`. Tools page HTTP example: `transport="http"`, `url=.../mcp`; the MCP guide also names stdio, OAuth, tool filtering, stateful sessions. `MultiServerMCPClient` is **stateless by default** (fresh session per call). `permissions=` is first-match, **fail-open**, **built-in FS tools only** — it does **not** cover MCP, custom tools, `execute`/shell, or `backend.*`. An MCP tool that writes disk is outside the FS PDP. Therefore Zero-Trust requires a **gateway PEP in front of MCP**, not `permissions=` globs. `interrupt_on` can pause a **named** MCP tool (review queue, not authz). Adapter interceptors may short-circuit using `ToolRuntime` — still **your** PEP, not a harness PDP.

| Zero-Trust control | Spec / 07 | On this harness |
| --- | --- | --- |
| **Transport** | Authenticated channel. OAuth 2.1 + PKCE `S256`. Clients **MUST** send RFC **8707** `resource` = **canonical MCP server URI** on authorize *and* token. Servers **MUST** accept only tokens whose audience is themselves. **MUST NOT** passthrough the client token to upstream APIs; obtain a new token (typically RFC **8693** exchange) scoped to the upstream resource. Spec-named risks: control circumvention, broken audit, stolen-token exfil proxy, trust-boundary collapse. stdio is **outside** this OAuth profile (host-env secrets) | Client `headers=` / `auth=` (httpx.Auth, built-in OAuth). A static `Authorization: Bearer` reused across upstreams is still passthrough **[inferred]** |
| **Capability negotiation** | 2025-11-25: `initialize` + `Mcp-Session-Id`, capabilities once (`tools`/`resources`/`prompts`, `listChanged`). 2026-07-28: those **removed**; version/identity/caps in `_meta`; optional `server/discover`; `ttlMs`/`cacheScope` on `tools/list`; Streamable HTTP `Mcp-Method`/`Mcp-Name` so a gateway can authz/rate-limit per tool without parsing JSON-RPC | MCP client lists tools → additive `tools=`. **Model proposes**; **PEP disposes** (gateway / interceptor / `interrupt_on`). FS `permissions` dispose **only** built-in file tools |
| **Hash-pin / allowlist** | `toolSurfaceHash` over canonical JSON of **name + description + inputSchema (+ outputSchema)**. Re-verify on every `tools/call`. Mismatch → session pause / re-consent. CVE-2025-54136 (MCPoison) CVSS **8.8**. Tool-description poisoning is in-context even if the user never “calls” the tool. 2026-07-28 `ttlMs` without re-hash = rug-pull window | Adapter **name** filter ≠ hash pin. Pin in the gateway; do not trust `tools/list` blindly |
| **Identity** | Verified access token / originating-user HMAC (Cedar L3). **Never** the LLM | Bind from IdP into RunContext: `context_schema`, `runtime.server_info.user.identity`, interceptor `ToolRuntime`. `user_id` in model JSON is a **proposal**, not a principal. `context_schema` is **not** authn — it is whatever the caller put in |

**Three trust boundaries:** (1) model ↔ host — model cannot verify tool descriptions; (2) client ↔ MCP server — authN/Z + integrity of `tools/list` and results; (3) MCP server ↔ upstream API — the server is a deputy with a token. CVE-2025-6514 CVSS **9.6**: **connecting** to hostile `authorization_endpoint` metadata can be RCE before any tool call.

**Tool-level RBAC (what exists vs what you build):**

| Control | What it is | What it is not |
| --- | --- | --- |
| `permissions=` | Path glob PDP, `read`/`write` ops, `allow\|deny\|interrupt`, first-match, **fail-open** | Per-principal RBAC; shell/`execute`; MCP; `backend.*` |
| `excluded_tools` | Blunt name allowlist (+ execution block `>=0.7.9`) | Per-user roles |
| `interrupt_on` | Review queue (approve/edit/reject/respond). `when` predicate skips the batch | An authorization PDP. Reviewer fatigue + TOCTOU (args change after approve) are unmitigated in the overview |
| LangSmith Deployments authz | Custom auth establishes the user; handlers tag `owner: user_id`, filter lists, 403 | `context_schema` is **not** authn — it is whatever the caller put in. Binding `user_id` from **verified** auth into `runtime.server_info.user.identity` is a platform concern |
| Gateway / Cedar / OPA | **The** MCP PEP (audience-bound tokens, hash-pin, no passthrough) | Not in `deepagents`. `permissions=` is not a substitute |

Correct permission ordering: deny `.env` **before** allow `/workspace/**`. Unanchored globs like `/**/secrets` **over-fire** on bulk `ls`/`glob`/`grep`/`delete` — anchor (`/secrets/**`). `delete` on a directory is all-or-nothing: write permission on target **and every descendant**. Sandbox + CompositeBackend: permission paths must sit on a **known route prefix** or construction raises `NotImplementedError` (including `/**`).

| Backend | Isolation | Production docs |
| --- | --- | --- |
| `StateBackend` / `StoreBackend` | No host FS, no shell | Default / durable memory |
| `FilesystemBackend` | Host paths; `root_dir` **absolute** | “Don’t use in deployed agents” |
| `LocalShellBackend` | Host FS **and** unrestricted shell; `virtual_mode=True` (default) jails **filesystem tools**, **not** `execute()` | “No isolation — use only in controlled development.” HITL on **all** ops; dedicated hosts; never untrusted users |
| LangSmith / Daytona / etc. sandboxes | Isolated container + `execute` | Recommended when code must run. Auth **proxy** for keys (`${OPENAI_API_KEY}`); never env-vars/files inside the sandbox |

Audit logs / SOC2 / HIPAA: **not** provided by `deepagents`. Do not claim harness-level immutability.

#### 4.5 PII pipeline — detect → redact → audit

Three sinks, three controls — plus an application pipeline that must run **before** any of them persist raw values.

| Sink | Default | Control |
| --- | --- | --- |
| **LangSmith traces** | Inputs/outputs logged when `LANGSMITH_TRACING=true` | `LANGSMITH_HIDE_INPUTS` / `LANGSMITH_HIDE_OUTPUTS`; `Client(hide_inputs=..., anonymizer=...)`; request-scoped `tracing_context` replicas `updates`; 0.7.9 tracing inputs disabled on **middleware**. LLM Gateway PII/secrets policies are **fail-close** if the scanner errors — they do **not** cover traces that bypass the gateway or model **outputs** in all cases |
| **Checkpoints / `StateBackend` files** | Full messages + file bytes in thread state | Don’t put secrets in VFS; deny `/workspace/.env`; prune checkpoints |
| **Model context** | Unredacted unless middleware | `PIIMiddleware("email", strategy="redact", apply_to_input=True)` etc. Strategies: `redact`, `mask`, `hash`, `block`. `apply_to_output=True` redacts streamed wire output (`langchain>=1.3.2`) |

`PIIMiddleware` is **not** in the default Deep Agents stack — you append it (slot 7).

**Pipeline (explicit):**

1. **Detection (control plane, before bytes leave the trust boundary).** Dual-gate: **regex** (email, PAN, SSN, phones) + **ML NER** if you have a scanner (Bedrock/Presidio/gateway). Scan: user input, model output, tool args/results, VFS writes, memory-write candidates, log/trace payloads, HITL UI. If ML is down: **fail closed to mask** on user-facing chat; **fail closed (block)** on tool args to external MCP / sandbox env — do not send raw PAN to a third-party server or into a checkpoint.
2. **Redaction.** `redact` / `mask` / `hash` to stable tokens (`[EMAIL_<hash12>]`) so the task can continue; `block` when the field must not exist (secrets paths, MCP args). Strip the value from VFS **and** from the message channel. Do **not** persist raw PAN in traces (sampled APM is not this step).
3. **Audit trail (WORM, immutable logs).** Log **decisions**, not values: `content_sha256` pre- and post-redact, entity **types** + counts, action (`redact` / `mask` / `hash` / `block-from-fs` / `block-from-trace`), detector (`regex` | `pii-middleware` | `gateway`), `correlation_id`, `tenant`, `thread_id`, permission decision, tool **arg digest**. A tool call without an audit row is a control-plane bug. Retention: security evidence *and* a sensitive-data asset — GDPR erasure vs legal hold is digest-level. Chain-of-custody for agent decisions: checkpointer `checkpoint_id` + arg digest + `ls_integration` metadata — **not** “LangSmith has the prompt so we are SOX-ready.”

---

### 5. Production Enterprise Code

Self-contained. Optional `deepagents` / `langchain` imports. Stdlib path runs the same control flow (retries + full jitter, circuit breaker, fallback **Deep Agents → create_agent → refuse**, PII detect→redact→audit, structured logs with correlation IDs, graceful degradation). Run: `python deep_agents_harness.py`.

```python
#!/usr/bin/env python3
"""Harness runtime: create_deep_agent + checkpointer patterns, stdlib fallbacks.

Fallback chain: Deep Agents → create_agent → deterministic refuse.
Run: python deep_agents_harness.py
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

# Optional (not required to run this file):
#   from deepagents import create_deep_agent
#   from langchain.agents import create_agent
#   from langgraph.checkpoint.postgres import PostgresSaver


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for k, d in (
            ("correlation_id", "-"),
            ("tenant_id", "-"),
            ("thread_id", "-"),
            ("harness", "-"),
        ):
            setattr(record, k, getattr(record, k, d))
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("da_harness")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"cid":"%(correlation_id)s","tenant":"%(tenant_id)s",'
            '"thread":"%(thread_id)s","harness":"%(harness)s",'
            '"msg":"%(message)s"}'
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
            sleep_s = min(cap_s, base_s * (2**i))
            sleep_s = random.random() * sleep_s  # full jitter
            slog(logging.WARNING, f"retry_backoff attempt={i+1} sleep_s={sleep_s:.3f}", harness="retry")
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
    block_on_pan: bool = True,
) -> str:
    kinds: list[str] = []
    if EMAIL_RE.search(text):
        kinds.append("email")
    if PAN_RE.search(text):
        kinds.append("pan")
    pre = _sha(text)
    if "pan" in kinds and block_on_pan and sink in {"mcp_args", "sandbox_env", "vfs_write"}:
        post = _sha("")
        audit.append({"cid": correlation_id, "tenant": tenant_id, "sink": sink, "kinds": kinds, "action": "block", "pre": pre, "post": post, "detector": "regex"})
        raise PermissionError(f"pii_block:{sink}:pan")
    redacted = EMAIL_RE.sub(
        lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]",
        text,
    )
    redacted = PAN_RE.sub("[PAN]", redacted)
    action = "redact" if redacted != text else "allow"
    audit.append({"cid": correlation_id, "tenant": tenant_id, "sink": sink, "kinds": kinds, "action": action, "pre": pre, "post": _sha(redacted), "detector": "regex"})
    return redacted


# --- compiled-graph ports --------------------------------------------------

class InvokeError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind  # "transient" | "permanent"


@dataclass
class RunResult:
    text: str
    harness: str
    degraded: bool


class CompiledPort:
    name: str

    def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> str: ...


def _last_text(result: dict[str, Any]) -> str:
    messages = result.get("messages") or []
    if not messages:
        return ""
    last = messages[-1]
    return getattr(last, "content", str(last))


def try_build_deep_agent() -> CompiledPort | None:
    """Illustrative create_deep_agent + checkpointer wiring when the lib is present."""
    try:
        from deepagents import create_deep_agent  # type: ignore
        from langgraph.checkpoint.memory import InMemorySaver  # type: ignore

        checkpointer = InMemorySaver()  # prod: PostgresSaver; HITL requires a checkpointer
        graph = create_deep_agent(
            model="anthropic:claude-sonnet-4-6",
            tools=[],
            checkpointer=checkpointer,
            interrupt_on={"write_file": True},
            name="research-copilot",
        )
    except Exception:
        return None

    class _G(CompiledPort):
        name = "deep_agents"

        def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> str:
            return _last_text(graph.invoke(payload, config=config))

    return _G()


def try_build_create_agent() -> CompiledPort | None:
    try:
        from langchain.agents import create_agent  # type: ignore
    except Exception:
        return None

    class _G(CompiledPort):
        name = "create_agent"

        def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> str:
            return _last_text(
                create_agent(model="anthropic:claude-sonnet-4-6", tools=[]).invoke(
                    payload, config=config
                )
            )

    return _G()


@dataclass
class ScriptedPort(CompiledPort):
    """Stdlib stand-in so this file runs without deepagents installed."""

    name: str = "scripted"
    fail_kind: str | None = None

    def invoke(self, payload: dict[str, Any], config: dict[str, Any]) -> str:
        if self.fail_kind == "transient":
            raise InvokeError("transient", "provider_429")
        if self.fail_kind == "permanent":
            raise InvokeError("permanent", "graph_construction")
        user = payload.get("user") or ""
        return f"ok:{self.name}:{user[:80]}"


def deterministic_refuse(reason: str) -> str:
    return json.dumps({"status": "refused", "reason": reason})


# --- runtime: breaker + fallback + PII + degradation -----------------------

@dataclass
class HarnessRuntime:
    deep: CompiledPort
    thin: CompiledPort
    deep_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("deep_agents"))
    thin_breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker("create_agent"))
    audit: list[dict[str, Any]] = field(default_factory=list)

    def run(
        self,
        user_text: str,
        *,
        tenant_id: str,
        thread_id: str,
        correlation_id: str | None = None,
    ) -> RunResult:
        cid = correlation_id or str(uuid.uuid4())
        extra = {"correlation_id": cid, "tenant_id": tenant_id, "thread_id": thread_id}
        safe = pii_detect_redact_audit(
            user_text, audit=self.audit, correlation_id=cid, tenant_id=tenant_id,
            sink="model_input", block_on_pan=False,
        )
        payload = {"messages": [{"role": "user", "content": safe}], "user": safe}
        config = {"configurable": {"thread_id": thread_id}, "metadata": {"cid": cid, "tenant_id": tenant_id}}

        def _call(port: CompiledPort, breaker: CircuitBreaker) -> str:
            extra["harness"] = port.name
            slog(logging.INFO, "invoke_start", **extra)

            def _once() -> str:
                return port.invoke(payload, config)

            try:
                breaker.allow()
                text = retry_call(_once)
                breaker.record_success()
                out = pii_detect_redact_audit(
                    text, audit=self.audit, correlation_id=cid, tenant_id=tenant_id,
                    sink="model_output", block_on_pan=False,
                )
                slog(logging.INFO, "invoke_ok", **extra)
                return out
            except CircuitOpenError:
                raise
            except InvokeError as exc:
                if exc.kind == "transient":
                    breaker.record_failure()
                slog(logging.ERROR, f"invoke_fail:{exc.kind}", **extra)
                raise
            except Exception as exc:
                breaker.record_failure()
                slog(logging.ERROR, "invoke_fail:unexpected", **extra)
                raise InvokeError("transient", type(exc).__name__) from exc

        try:
            return RunResult(
                _call(self.deep, self.deep_breaker), self.deep.name, degraded=False
            )
        except (CircuitOpenError, InvokeError, TimeoutError, ConnectionError) as exc:
            slog(logging.WARNING, "fallback_create_agent", **{**extra, "harness": "create_agent"})
            try:
                text = _call(self.thin, self.thin_breaker)
                return RunResult(text, self.thin.name, degraded=True)
            except (CircuitOpenError, InvokeError, TimeoutError, ConnectionError):
                slog(logging.ERROR, "fallback_refuse", **{**extra, "harness": "refuse"})
                return RunResult(
                    deterministic_refuse(type(exc).__name__),
                    "refuse",
                    degraded=True,
                )


def build_runtime() -> HarnessRuntime:
    """Stdlib ports so this file runs without model keys. Swap in try_build_* when live."""
    return HarnessRuntime(
        deep=ScriptedPort(name="deep_agents"),
        thin=ScriptedPort(name="create_agent"),
    )


if __name__ == "__main__":
    rt = build_runtime()
    r1 = rt.run(
        "Summarize ticket 55 for ada@example.com",
        tenant_id="acme",
        thread_id="t-1",
        correlation_id="cid-1",
    )
    print(r1)
    assert "[EMAIL_" in r1.text
    assert any(row["action"] in {"redact", "allow"} for row in rt.audit)

    rt.deep = ScriptedPort(name="deep_agents", fail_kind="transient")
    rt.deep_breaker = CircuitBreaker("deep_agents", failure_threshold=1, cooldown_s=60)
    r2 = rt.run("hello", tenant_id="acme", thread_id="t-2", correlation_id="cid-2")
    print(r2)
    assert r2.degraded is True
    assert r2.harness in {"create_agent", "refuse"}

    rt.thin = ScriptedPort(name="create_agent", fail_kind="permanent")
    rt.thin_breaker = CircuitBreaker("create_agent", failure_threshold=1, cooldown_s=60)
    r3 = rt.run("hello", tenant_id="acme", thread_id="t-3", correlation_id="cid-3")
    print(r3)
    assert r3.harness == "refuse"
    print("ok", len(rt.audit), "audit rows")
```

**Wiring notes (not in the script):** production `create_deep_agent` should pass `PostgresSaver`, `store=` when using `StoreBackend`, `CompositeBackend` `/memories/` namespaced by verified identity, `excluded_tools={"execute"}` unless a sandbox is bound, `GeneralPurposeSubagentProfile(enabled=False)` for short bots, `PIIMiddleware` in `middleware=`, `AnthropicPromptCachingMiddleware(ttl="1h")` only if turn gaps exceed 5m. Durability: `sync` when HITL is on. Pin `deepagents>=0.7.9`. `thread_id` **< 255** chars.

---

### 6. Architectural System Design Scenarios

#### Scenario A — Internal research copilot (docs, tickets, web)

**Problem.** A 2,000-person company wants an internal copilot over Confluence, Jira, and the public web. Work is long-horizon and artifact-heavy (briefs, comparison tables, source dumps). ~LangChain-GTM-shaped traffic is plausible later (~10k req/week, mostly ambient). Security wants no shell, per-user memory, HITL on writes to shared knowledge, and no second orchestration runtime. Platform team is split: “just `create_agent` + a retriever tool,” “custom LangGraph rental-pipeline style,” or Deep Agents.

**Proposed architecture:**

```
  ┌─────────┐   ┌─────────────────────────────────────────────────────────┐
  │ IdP/PEP │──▶│ CONTROL: create_deep_agent + HarnessProfile             │
  │ JWT →   │   │   excluded_tools={execute}   GP ON for source gathering │
  │ user_id │   │   interrupt_on writes to shared knowledge               │
  │         │   │   permissions: deny /memories/** write from the agent;  │
  │         │   │     deny **/.env before allow /workspace/**             │
  │         │   │   thread_id + context_schema(user_id)  PostgresSaver    │
  │         │   │   PII detect→redact→audit before traces/FS/memory       │
  └─────────┘   └──────────────────────────┬──────────────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────────────┐
                    │ DATA: parent Sonnet/Opus synthesize                  │
                    │   declarative research-agent (cheaper model) via task│
                    │   StateBackend scratch + CompositeBackend            │
                    │     /memories/ → StoreBackend                        │
                    │     ns=(assistant_id, user.identity)                 │
                    │   summarization ON (85/10)  offload 20k              │
                    │   stream.subagents  checkpointer durability=sync     │
                    │   web_search as tools= dict (provider-native)        │
                    └──────────────────────────────────────────────────────┘
```

**Technology choices:** Deep Agents because the job is non-deterministic, artifact-heavy, and already matches LangChain’s published copilot examples (GTM agent: per-rep memory, QBR skills, research subagents). Retriever stays a **tool**, not a hidden preamble. GP on for parallel source gathering; specialized `research-agent` spec with a cheaper model for fetch. Org store namespace **read-only**. No `LocalShellBackend`. Pin `>=0.7.9`. Todos **off**.

**Trade-off matrix:**

| Axis | **A1 Deep Agents (recommended)** | **A2 `create_agent` + retriever tool only** | **A3 Custom LangGraph (extract → score → HITL edges)** |
| --- | --- | --- | --- |
| **Cost** | **[inferred] ~$223 / 1k** 10-call Sonnet 4.6 cached 2k prefix, GP **off**; GP on **~1.8–2.0×** that run. Skills/memory at 20k prefix **$339 / 1k** cached | Near-zero harness tax until you add tools; you will re-implement offload/summarize/subagents or blow the window | Deterministic nodes are cheap; every agentic node still pays a model. Extra graph-authoring time |
| **Latency** | 10-call run **20,000 / 80,000 / 200,000 ms [inferred]**; GP 8-call child **16,000 / 64,000 / 160,000 ms [inferred]** if parent waits; summarizer **+2,000 / +6,000 / +15,000 ms [inferred]** | Lowest for 1–2 tool hops (TTFT **640 / 2,560 / 5,120 ms [inferred]**). Dies on long-horizon context | Deterministic steps can beat a long agent loop; HITL is still a **gap** clock |
| **Ops complexity** | Pin `deepagents` (beta, fast-moving); LangSmith deploy optional; middleware order is a footgun | Pin `langchain`; you own every middleware | You own the graph, edges, and every context-engineering piece |
| **Security posture** | Trust-LLM + fail-open FS PDP + sandbox-not-used; MCP uncovered by `permissions=` (needs gateway PEP, §4.4); Store namespaces are injection if mis-scoped | Smallest tool surface if you keep one retriever | Policy in **edges** (non-LLM PDP) — strongest for claims-like workflows, overkill for research prose |
| **Scalability ceiling** | Checkpoint storage (StateBackend every step) + provider TPM; product hop cap ≪ 9,999 | Scales with one hop; not a research agent | Fan-out/join you encode; not “model decides `task`” |

**Decision.** **A1 wins** for this problem statement. A2 is the right answer for RAG Q&A (LangChain’s own contrast). A3 is the rental-application pattern: LLM extract → code score → auto approve/reject/HITL — use it when **policy is an edge**, not when a researcher should spawn `task`. If the org already standardized on `create_deep_agent`, do not fork for this copilot — disable `execute`, keep summarization, namespace memory.

#### Scenario B — Multi-tenant coding agent (Deep Agents vs Claude Agent SDK)

**Problem.** A platform team must ship a coding assistant: repo checkout, tests, patches, optional `execute`. Multi-tenant. Model portability (Gemini / GPT / GLM / Bedrock) is a board requirement *or* the org is already all-in on Claude. Comparison page drafted **2026-04-16** — revisit before a 2026-H2 review; products move. Managed Deep Agents (MDA) is CLI-first hosted runtime, **private preview** at research time. Named production users on the comparison page: **OpenSWE**, **LangSmith Fleet**.

**Proposed architecture (recommended when portability + LangSmith tenancy matter):**

```
  ┌─────────┐   ┌─────────────────────────────────────────────────────────┐
  │ IdP/PEP │──▶│ CONTROL: create_deep_agent  sandbox backend (not local) │
  │ JWT     │   │   thread-scoped sandbox + idle_ttl_seconds (ex. 3600)   │
  │ tenant  │   │   permissions on non-sandbox routes only                │
  │         │   │   HITL on execute / prod-mutating tools                 │
  │         │   │   auth proxy for provider keys — never sandbox env/files│
  │         │   │   LangSmith scoped threads, per-user sandboxes, RBAC    │
  └─────────┘   └──────────────────────────┬──────────────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────────────┐
                    │ DATA: agent may run *outside* sandbox using remote   │
                    │   sandbox as a tool  OR inside a sandbox             │
                    │   State/Store for artifacts; execute only in guest   │
                    │   HarnessProfile per model (beta)                    │
                    │   PII detect→redact→audit; excluded_tools as blunt   │
                    │   pack; real MCP RBAC at gateway PEP (§4.4)          │
                    └──────────────────────────────────────────────────────┘
```

**Technology choices:** Deep Agents + **sandbox backend**, never `LocalShellBackend` (`virtual_mode` does not jail `execute()`). Comparison-page claim of “built-in multi-tenancy” means **LangSmith Deployment primitives**, not an in-library PDP. Claude Agent SDK if the org has already standardized on Claude + Anthropic managed agents and will staff a custom gateway/server/auth/tenancy. SDK: agent runs **inside a sandbox only**; model = Claude / Bedrock / Vertex / Azure Claude; license = SDK MIT, Claude Code proprietary.

**Trade-off matrix:**

| Axis | **B1 Deep Agents + remote sandbox (recommended if model-portable)** | **B2 Claude Agent SDK (agent inside sandbox only)** | **B3 Deep Agents + `LocalShellBackend` on the agent server** |
| --- | --- | --- | --- |
| **Cost** | Same token math as A; plus sandbox TTL/compute (unpublished here). GP/todos tax still applies — disable if the ACI is custom tools | Vendor prompt (Anthropic cut Claude Code system prompt >80% on new models; cited as inspiration for v0.7). Model-locked list prices | Cheap until the first cross-tenant incident |
| **Latency** | Sandbox cold-start **unpublished** — treat as p99 of `execute`, not TTFT. Parent TTFT still **640 / 2,560 / 5,120 ms [inferred]** | Unpublished here. Inside-sandbox only may skip a hop vs remote-as-tool | Fastest `execute` — that is the incident |
| **Ops complexity** | Pin `deepagents` beta; LangSmith or `langgraph build` image; pluggable backends | You own HTTP/SSE, auth, tenancy. Claude managed agents is a **separate** product | Looks simple; production docs forbid it |
| **Security posture** | Pluggable isolation; FS rules ≠ shell; auth proxy for keys; permissions do not constrain `execute` | Sandbox-local FS; model lock-in is also a blast-radius limit | **No isolation.** Unrestricted shell. `virtual_mode` jails FS tools only |
| **Scalability ceiling** | Per-user sandboxes + scoped threads (LangSmith). Assistant-scoped sandbox unbounded disk → TTL/snapshots | You build the ceiling | One host FS for all tenants — not a ceiling, a CVE |

**Decision.** **B1 wins** when Gemini/GPT/GLM/Bedrock and LangSmith Fleet/OpenSWE-like tenancy matter. **B2 wins** when the org is already Claude-standardized and will staff the server. **B3 never wins** in production. Revisit the April 2026 comparison before locking 2026-H2. “Already on LangGraph? Stay if the value is the graph’s shape. If the flow is highly agentic, consider migrating to Deep Agents.”

---

## Common Failure Modes

| Failure | Cause | Detection | Mitigation |
| --- | --- | --- | --- |
| `ValueError` at `create_deep_agent` | `excluded_middleware` names `FilesystemMiddleware` / `SubAgentMiddleware`; unmatched name; string matches multiple classes; `read_file` omitted from FS allowlist | Construction exception (good) | Hide tools or disable GP; never strip scaffolding |
| Tool hidden but still executable | `excluded_tools` on **<0.7.9** | Model cannot see schema; another path still calls it | Pin `>=0.7.9` |
| Replacement drops `backend`/`permissions` | Override `FilesystemMiddleware` without passing outer kwargs (not merged) | Permissions never evaluate; offload/skills lose VFS | Pass `backend=` and `permissions=` into the replacement |
| Accidental GP spend / loops | Default GP auto-added; `task` on the bare stack | Second full prefix in traces; `stream.subagents` surprise | `GeneralPurposeSubagentProfile(enabled=False)` + no sync `subagents` |
| Subagent 25-step `CancelledError` | Historical: child invoked without parent config (`#1698`) | `GraphRecursionError` at 25; siblings cancelled | Pin current bind 9,999; verify gather; product cap still required |
| Context overflow | Summarization excluded **and** offload path gone | Provider 400; checkpoint bloat (unless `DeltaChannel`) | Keep factory summarizer for research/coding; `ContextOverflowError` → summarize + retry |
| Under-recall on large trees | `grep` cap **1,000** + `truncated` | Agent stops after first page | Teach the agent to treat `truncated` as incomplete **[inferred]** |
| Artifact clobber | v0.7 `write_file` overwrites | Silent last-write-wins | Permissions deny on `/memories/**`; HITL on writes |
| HITL TOCTOU / fatigue | Review queue ≠ PDP; args change after approve; unanchored `/**/secrets` globs | Wrong tool ran; bulk `ls` interrupts | Anchor globs; re-hash args at execute (app-level; **gap** in overview); checkpointer required |
| Fail-open FS leak | No matching `permissions` rule → **allow** | `.env` in VFS/checkpoints | Deny secrets **before** allow workspace |
| MCP/custom FS bypass | `permissions=` FS-tools-only | Writes via MCP not in the PDP | Gateway PEP in front of MCP (OAuth 2.1 + RFC 8707 + hash-pin; §4.4) |
| Prompt-cache miss after memory write | Memory in the cached segment | `cache_read_input_tokens` = 0 | Keep memory small; MemoryMiddleware is **after** cache on purpose; 5m TTL |
| OpenAI retention | Responses API stores by default | Data-residency review fail | `store=False` + encrypted reasoning |
| Host shell in prod | `LocalShellBackend` / `FilesystemBackend` | Incident | Sandbox backend; 503 if pool empty |
| Stale docs | Context-engineering “built-in system preamble” | Inflated prompt-size estimates | Trust API reference + v0.7 blog |
| `fork` mode (0.7.12) | Experimental; continues parent conversation | Isolation gone; no skills on fork | Do not use as default subagent |

No public Deep Agents post-mortem corpus beyond GitHub issues (#1698, #5643, #5809). Do not invent incidents.

---

## Key Takeaways

- Deep Agents is the **harness**, `create_agent` is a **thin harness**, LangGraph is the **runtime**. Same loop; different defaults. `create_deep_agent` returns a `CompiledStateGraph`.
- Construction is the **control plane**; model + tools + VFS are the **data plane**. Middleware can rewrite the tool list and prompt *before* the model call; `tools=` cannot, and is additive.
- v0.7: todos **opt-in**, hidden prompt **gone**, **~6k→~2k** base tokens (−65%), `.name` override, `delete` tool. Pin **`>=0.7.9`** so `excluded_tools` also blocks execution.
- You cannot `excluded_middleware` the filesystem or subagent scaffolding (`ValueError`). Hide tools or disable GP. Permissions are **fail-open**, **FS-tools-only**, and do **not** apply to `execute` or MCP. Zero-Trust MCP is a **gateway PEP**: OAuth 2.1 + RFC 8707 audience, **no** token passthrough, hash-pinned tool JSON, identity from verified token / RunContext — never model JSON. Full MCP catalog is topic 09.
- Default GP subagent is **on** — a cost and recursion footgun, not a free abstraction. Prompt cache is on for Anthropic/Bedrock with **5m** TTL; MemoryMiddleware sits **after** cache middleware on purpose.
- **9,999** `recursion_limit` is a LangGraph sentinel dodge (10,000 dropped by `merge_configs`), not a product max-hops feature. Durable execution is LangGraph checkpointer/store — Deep Agents does not wrap Temporal.
- Fallback: **Deep Agents → `create_agent` → deterministic refuse**. PII is **detect → redact → audit** on traces, checkpoints/VFS, and model context. Circuit breakers are **yours**; the library does not ship them.
- Layer pick: long-horizon artifact work → Deep Agents; short latency-sensitive loop → `create_agent`; policy in edges → custom LangGraph; Claude-in-sandbox + you own the server → Claude Agent SDK (comparison drafted 2026-04-16).

---

## Interview Q&A

**Q1. What is Deep Agents, in one minute?**  
I treat it as an opinionated harness, not a runtime. LangGraph is durable execution. `create_agent` is model + tools + middleware — a thin harness. `create_deep_agent` calls that, then binds `recursion_limit` 9,999 and `ls_integration=deepagents`. What I paid for is VFS, subagents, summarization/offload, profiles, optional skills/memory/HITL. Same `CompiledStateGraph`. I can drop it in a larger graph as a node.

**Q2. Walk `create_deep_agent` → invoke.**  
Control plane: resolve model and `HarnessProfile`, resolve backend (`StateBackend` default), assemble middleware, add GP unless disabled, compose `USER → BASE → SUFFIX`, `create_agent`, `with_config`. Data plane: LangGraph loop; middleware shapes tools and prompt *before* the model; tools= callables run only after the model chooses them. I always pass `thread_id` and a `context_schema` instance. No checkpointer means no resume and no HITL.

**Q3. `excluded_tools` vs `excluded_middleware`.**  
I hide FS tools with `excluded_tools` or an allowlist that still includes `read_file`, because offload, permissions, skills, and memory still need a VFS. I disable `task` by turning off the GP profile and passing no sync subagents — then `SubAgentMiddleware` is not attached. If I `excluded_middleware` Filesystem or SubAgent I get `ValueError` at construction. Pre-0.7.9 exclusion was visibility-only; I pin `>=0.7.9` so it also blocks execution.

**Q4. Give me `$ per 1k` for a default-ish research run.**  
Inferred, not a SKU: Claude Sonnet 4.6, 10 calls in a 5-minute window, GP off, 2k cached prefix after v0.7, 3k uncached in, 800 out. One 5m write + nine reads + uncached + output ≈ $0.223/run → **$223 / 1k**. Uncached same shape **$270 / 1k**. A 20k prefix with memory/skills is **$339 / 1k** cached vs **$810** uncached. GP on is roughly **+0.8–1.0×** the main bill. Todos cost more in LangChain’s own evals — I leave them off.

**Q5. What p50/p95/p99 do you put on Deep Agents?**  
Nobody publishes harness percentiles. I contract streaming TTFT at **640 / 2,560 / 5,120 ms** inferred from the same inner-chat class I use elsewhere. One ReAct cycle **2,000 / 8,000 / 20,000 ms**. Summarizer extra hop **2,000 / 6,000 / 15,000 ms**. A parent-wait 8-call GP child **16,000 / 64,000 / 160,000 ms**. A 10-call research run **20,000 / 80,000 / 200,000 ms**. Sync checkpointer tax **10 / 50 / 200 ms**. HITL is a different clock: **30,000 / 180,000 / 600,000 ms**, expire-deny. I measure on LangSmith; I do not pretend 9,999 is an SLO.

**Q6. Why is `recursion_limit` 9,999?**  
LangGraph `merge_configs` historically dropped `recursion_limit` when it equaled the default 10,000, so nested graphs fell back to 25. Binding 9,999 dodges that sentinel. Issue #1698: 0.4.4 subagents ran at 25, `GraphRecursionError` became `CancelledError` and cancelled `asyncio.gather` siblings. It is a super-step fuse, not max `task` calls. I still set a product hop cap.

**Q7. Permissions and HITL — is that Zero Trust?**  
No. `permissions=` is a fail-open path PDP for built-in FS tools. No match allows. MCP, custom tools, `execute`, and `backend.*` are uncovered. `interrupt_on` is a review queue, not a PDP — TOCTOU on args is an inferred gap. MCP tools ride on additive `tools=` (`MultiServerMCPClient.get_tools()`); `permissions=` never sees them, so Zero-Trust is a **gateway PEP in front of MCP**, not FS globs. Transport: OAuth 2.1, RFC 8707 audience = canonical MCP server URI, **no** client-token passthrough (RFC 8693 exchange). Hash-pin name+description+schemas and re-verify every `tools/call` (CVE-2025-54136). Identity from the verified token / RunContext (`context_schema`, `runtime.server_info.user.identity`) — **never** from model JSON. The full MCP tool catalog is topic 09. The README security model is still trust-the-LLM.

**Q8. PII — detect → redact → audit.**  
Three sinks: LangSmith traces, checkpoints/StateBackend files, model context. I detect with regex plus optional ML before persist; redact/mask/hash/block via `PIIMiddleware` (not in the default stack) and env hide/anonymizer; audit WORM of decisions — pre/post hashes, entity types, counts, detector, cid, thread — not raw PAN. Gateway scanners fail-closed but miss bypass traces and some outputs. If ML is down I still regex-mask chat and I block PAN into MCP args, sandbox env, and VFS writes.

**Q9. Circuit breaker and fallback.**  
The library does not ship a breaker. I wrap `invoke`: closed → open → half-open with one probe. Independent breakers for parent, summarizer, checkpointer, sandbox. Fallback is Deep Agents → `create_agent` → deterministic refuse. I strip Fireworks cache headers on cross-provider fallback. I never fail open to `LocalShellBackend`. HITL timeout does not auto-approve.

**Q10. Deep Agents vs `create_agent` vs custom LangGraph vs Claude Agent SDK.**  
Research copilot, artifacts, subagents, summarization: Deep Agents. Short RAG Q&A, 1–2 tools, latency-sensitive: `create_agent` — the ~2k schema tax is dead weight. Claims workflow with code scoring and policy in edges: custom LangGraph, maybe a deep agent as one node. Coding assistant: Deep Agents plus a **remote sandbox** if I need model portability and LangSmith tenancy; Claude Agent SDK if we are already Claude-in-sandbox and will own HTTP/auth/tenancy. Comparison drafted 2026-04-16. Never LocalShell in prod.

**Q11. Prompt cache and MemoryMiddleware order — why?**  
Anthropic/Bedrock cache middleware is unconditional (no-op off-provider), TTL 5m default, after Patch so the cached prefix matches repaired history. MemoryMiddleware is **after** the cache middleware so AGENTS.md updates are less likely to bust the whole prefix. I still keep memory small. SystemMessage is how I preserve explicit `cache_control` breakpoints through assembly.

**Q12. What did v0.7 actually change, and what is still a footgun?**  
Todos opt-in, hidden base prompt removed, tool descriptions −43%, base tokens ~6k→~2k, `.name` replace, `delete`, backend factories gone. Reward CIs spanned zero; Luna cost −15%. Footguns that remain: default GP on, permissions fail-open, `write_file` overwrites, `grep` 1,000-match truncate, 9,999 is not a product cap, beta + fast-moving pin, experimental `fork` in 0.7.12 undoes isolation.

---

## Key Numbers to Memorize

### Package / layers / versions
| Number | What |
| --- | --- |
| **0.7.12** | Research pin (uploaded 2026-09-01); beta; Python `>=3.11,<4.0`; MIT |
| **5,646,660** | PyPI last-month downloads |
| **2025-07-29** | First public `0.0.1` |
| **2026-07-29** | v0.7 blog (token cut, todos opt-in) |
| **2026-04-16** | Deep Agents vs Claude Agent SDK comparison drafted |
| **2026-08-06** | Deep Agents vs LangChain vs LangGraph blog (GTM numbers) |
| **1.0.0** | `model=None` removed |
| **`>=0.7.9`** | `excluded_tools` blocks **execution**; middleware tracing inputs off |
| **0.7.12 fork** | Experimental subagent conversation fork |

### Tokens / cache / eval
| Number | What |
| --- | --- |
| **~6k → ~2k / −65%** | v0.7 base input tokens on a default-agent turn |
| **−43%** | Builtin tool description trim |
| **−34% / −15% / +4%** | Luna tokens / cost / reward in the v0.7 matrix |
| **20,000 / 10 lines** | Offload threshold / preview |
| **0.85 / 0.10** | Summarize trigger / keep fractions |
| **170000 / 6** | No-profile summarize trigger tokens / keep messages |
| **1,000** | `grep` match cap (`truncated`) |
| **1.25× / 2× / 0.1×** | Anthropic 5m write / 1h write / read |
| **5m** | Default cache TTL in DA override docs |
| **1,024 / 4,096 / 512 / 4** | Bedrock min tokens Sonnet 4.6 / Opus 4.6 & Haiku 4.5 / Opus 5; max checkpoints |
| **82% / 80% / 18% / 80% / 26% / 89% / 81%** | Eval overall: gemini-3.6-flash / gpt-5.5 / gpt-5.4 / opus-4-7 / opus-4-6 / glm-5.1 / glm-5p1 |

### $ / SKUs **[inferred]** where marked
| Number | What |
| --- | --- |
| **$3 / $15** | Sonnet 4.6 input / output per MTok |
| **$3.75 / $0.30** | Sonnet 4.6 5m write / cache read per MTok |
| **$2 / $10** | Sonnet 5 (planned 2026-09-01 bump to $3/$15 **did not occur**) |
| **$5 / $25** | Opus 4.6+ input / output |
| **$1 / $5** | Haiku 4.5 input / output |
| **1.1× / 50%** | US-only inference / Batch off |
| **[inferred] $223 / 1k** | 10-call cached 2k prefix, GP off |
| **[inferred] $270 / 1k** | Same run uncached |
| **[inferred] $47 / 1k** | Cache savings at 2k prefix |
| **[inferred] $339 / $810 per 1k** | 20k prefix cached / uncached |
| **[inferred] +0.8–1.0×** | Default GP isolated 8-call extra bill |

### Recursion / production / GTM
| Number | What |
| --- | --- |
| **9,999** | Bound `recursion_limit` (sentinel dodge vs 10,000) |
| **10,000** | Frontend `recursionLimit` copy; LangGraph `DEFAULT_RECURSION_LIMIT` historically dropped |
| **25** | Bare LangGraph default in #1698; subagent footgun |
| **255** | PostgresSaver `thread_id` max chars |
| **3,600 s** | Docs example sandbox `idle_ttl_seconds` |
| **10** | `LocalShellBackend.max_file_size_mb` |
| **2** | `ModelRetryMiddleware` default `max_retries` (3 attempts) |
| **~10k / week, 150 users, 26% / 74%** | LangChain GTM agent on Deep Agents |
| **`(FilesystemMiddleware, SubAgentMiddleware)`** | `_REQUIRED_MIDDLEWARE` |

### Latency / security (numeric ms)
| Number | What |
| --- | --- |
| **640 / 2,560 / 5,120 ms** | **[inferred policy]** streaming TTFT p50/p95/p99 |
| **2,000 / 8,000 / 20,000 ms** | **[inferred]** one ReAct cycle (model + local FS) |
| **2,000 / 6,000 / 15,000 ms** | **[inferred]** summarization extra LLM ON path |
| **16,000 / 64,000 / 160,000 ms** | **[inferred]** GP 8-call child, parent waits |
| **20,000 / 80,000 / 200,000 ms** | **[inferred]** 10-call research run, GP off, no summarize |
| **10 / 50 / 200 ms** | **[inferred policy]** checkpointer `sync` extra per super-step |
| **0 / 0 / 0 ms** | **[inferred policy]** `exit` durability hot-path tax; GP-off extra-tax vs thin harness *shape* (absolute TTFT is still the model) |
| **30,000 / 180,000 / 600,000 ms** | **[inferred policy]** HITL clock; p99 expire-deny |
| **detect → redact → audit** | PII on traces, checkpoints/VFS, model I/O **before** persist |
| **fail-open** | `permissions=` default when no rule matches |
| **RFC 8707** | MCP clients MUST send `resource` = canonical server URI on authorize *and* token |
| **RFC 8693** | Token *exchange* to upstream — spec **MUST NOT** passthrough the client token |
| **8.8 / 9.6** | CVE-2025-54136 MCPoison (no re-validate) / CVE-2025-6514 connect-time RCE |
| **localhost:2024** | Local `useStream` target |

**Dates:** research frozen **2026-09-02**. Do not treat inferred `$` or ms as list prices or vendor SLOs.
