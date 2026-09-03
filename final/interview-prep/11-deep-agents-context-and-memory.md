# Deep Agents: Context Engineering, Memory, Skills & Prompt Caching

**Consolidated Study Module for Director/VP AI Interviews**
**Date**: 2026-09-02 | **Package pin**: `deepagents==0.7.12`

---

## What Is This?

Think of an LLM agent like a surgeon in an operating room. The surgeon can only see what
is on the table in front of them (the context window). Context engineering is the discipline
of deciding what goes on that table, when it gets placed there, and what gets cleared away
as the operation proceeds. Put too much on the table, and the surgeon fumbles. Remove the
wrong item, and they lose critical information mid-procedure.

LangChain Deep Agents treats context as a **managed, layered resource** -- not a passive
input buffer. It uses four mechanisms: input assembly (what starts on the table), runtime
injection (what gets handed in during the operation), compression/offloading (clearing away
used items), and subagent isolation (sending a nurse to do a sub-procedure in a separate
room and report back with a summary).

Most agent failures are context failures before they are reasoning failures. The model either sees too much, too little, or the wrong slice at the wrong time. Deep Agents treats this as a systems problem: prompt assembly, hidden runtime state, compression, delegation, and long-term persistence are all part of the context design.

**Invariant**: Deep Agents does **not** invent a second context window. The model still sees one assembled system prompt + one message list per call. The harness only **shapes** that payload.

Think desk, not second brain: skills are labeled drawers; memory is the sticky note always in view; offload/summarize is the filing cabinet; prompt cache remembers the letterhead; OpenWiki is the team wiki on disk -- pull a page, do not glue it to the forehead.

Context-relevant gates: `memory=` / `skills=` **opt-in**; summarization + offload **always on**; `AnthropicPromptCachingMiddleware` **always registered** (`unsupported_model_behavior="ignore"`); `BedrockPromptCachingMiddleware` / `FireworksPromptCachingMiddleware` lazy extras; `create_summarization_tool_middleware` **opt-in**; `add_cache_control=True` on Memory when `memory=` is set.

## Why It Matters

Context engineering is the single biggest lever for agent reliability in production.
Anthropic's engineering team reports that "context rot" -- gradual degradation as irrelevant
content accumulates -- caused nearly 65% of enterprise AI failures in 2025. Mastering these
patterns separates architects who deploy reliable agents from those who build demos.

In interviews, a strong answer separates what the model sees from what the runtime knows. Trap answers: "OpenWiki is injected like memory," "skills auto-activate without `read_file`," "prompt cache *is* long-term memory," "disabling eviction is how I keep fidelity," "`allowed-tools` in SKILL.md is a security boundary."

LangChain's published skill pack moved Claude Code on *their* LangChain/LangGraph/Deep Agents eval from **25% to 95%** pass (blog intro also says 29% to 95%; **use the table**; benchmark not open-sourced). v0.7 cut the default harness prefix **~6k to ~2k (-65%)** -- the skinny cached prefix when `memory=`/`skills=` are empty.

---

## Architecture / System Design

### The Five-Layer Mental Model

| Layer | Load policy | Model sees |
| --- | --- | --- |
| **Input context** | Static prompt assembly | System prompt, memory, skill metadata, tool schemas, subagent instructions |
| **Runtime context** | Per-run injection | `context_schema` values hidden from model unless code injects them |
| **Context compression** | Automatic at limits | Offloaded tool blobs as VFS pointers; summarized older history |
| **Context isolation** | Subagent delegation | Heavy work in child window; parent gets compact result |
| **Long-term memory** | Filesystem-backed persistence | `AGENTS.md` and Store/FS files across threads |

### Prompt Assembly Order

```text
prompt assembly
  -> custom system_prompt
  -> built-in Deep Agents instructions (base + suffix)
  -> memory files (AGENTS.md -- always loaded)
  -> skill metadata (name + description, ~100 tok each)
  -> tool prompts and tool descriptions
  -> VFS prompt (built-in tool docs)
  -> subagent/task guidance
  -> user middleware prompts
  -> HITL prompt when configured
```

### Full System Topology

```
                         TELEMETRY / OBSERVABILITY SINKS
         +------------------------------------------------------------------+
         |  LangSmith  ls_integration=deepagents                            |
         |  summarization spans  metadata.lc_source=summarization           |
         |  cache_creation_input_tokens / cache_read_input_tokens           |
         |  Memory/Skills hooks: trace_policy omit_payload (load, not bytes)|
         |  OpenWiki CI: .last-update.json  OPENWIKI_TELEMETRY_DISABLED     |
         |  WORM you build: (cid, tenant, path, pre/post sha, pii action)  |
         +----------^---------------------^------------------^-------------+
                    | spans               | cache metrics     | memory audit
+-------------------+---------------------+-------------------+-------------+
| CONTROL PLANE  (LLM-free load policy; identity NEVER from model JSON)     |
|  create_deep_agent kwargs: skills=  memory=  (opt-in gates)               |
|  Always: FilesystemMiddleware offload  create_summarization_middleware     |
|  Always: AnthropicPromptCachingMiddleware(ignore)  + lazy Bedrock/FW      |
|  MemoryMiddleware LAST after cache  add_cache_control=True (ChatAnthropic)|
|  SkillsMiddleware FIRST (catalog into stable prefix)                      |
|  context_schema / invoke(context=) -- tools read it; do NOT dump to prompt|
|  PII detect->redact->audit BEFORE AGENTS.md / SKILL.md persist           |
|  IdP token -> tenant/user namespace  (StoreBackend ns, not LLM-chosen)   |
+----------------------------------+----------------------------------------+
                                   | one system + one message list / call
                                   v
+-----------------------------------------------------------------------+
| DATA PLANE  (untrusted tokens -- SKILL.md / AGENTS.md / MCP files)    |
|                                                                       |
|  assembled system + tool schemas + messages -> model -> final | calls |
|  wrap_model_call may compact the VIEW; graph messages still grow      |
|                                                                       |
|  +--- TOOL PROXIES (context I/O, not an omnibus shell) --------------+|
|  | read_file / grep  -> skill L2, wiki pages, offloaded blobs        ||
|  | edit_file / write_file -> memory hot-path; deny /skills/** writes ||
|  | execute -> skill scripts IFF SandboxBackendProtocol               ||
|  | compact_conversation (opt-in tool; does NOT disable 85% auto)     ||
|  | MCP/custom on tools= -- permissions= DOES NOT COVER (gateway PEP) ||
|  +-------------------------------------------------------------------+|
+---+------------------+------------------+------------------+----------+
    |                  |                  |                  |
    v                  v                  v                  v
+-----------------------------------------------------------------------+
| PERSISTENCE LAYER  (independent lifetimes -- cache is NOT memory)      |
|  +------------+ +-------------+ +--------------+ +------------------+ |
|  | Store / FS | | Checkpointer| | VFS offload  | | Provider KV cache| |
|  | memory=    | | thread_id   | | /large_tool_ | | 5m default (1h   | |
|  | skills=    | | messages    | |  results/    | |  HITL override)  | |
|  | OpenWiki   | | memory skip | | /conversation| | TTL then GONE    | |
|  |  on disk   | |  -load      | |  _history/   | | not in VFS       | |
|  +------------+ +-------------+ +--------------+ +------------------+ |
|  StateBackend scratch = thread only. Cross-thread -> Composite.        |
|  Default StateBackend does not survive a new thread_id.                |
+-----------------------------------------------------------------------+
```

### What the Model Always Knows vs On Demand vs What Survives

| Question | Answer |
| --- | --- |
| Always in the prompt | System prompt + `AGENTS.md` + skill **names/descriptions** + tool schemas |
| On demand | Skill bodies, `references/`, script **stdout**, offloaded blobs, OpenWiki pages, `read_file` of the repo |
| How long one thread | Until **20k offload + 85% summarize** recycle the **model view**; graph `messages` still grow (`DeltaChannel`) |
| New `thread_id` | Store/FS `memory=` and `skills=` survive. `StateBackend` scratch does **not**. Provider cache does **not** after TTL |
| New process, same thread | Checkpointer blob + store |
| Forgotten on summarize | Pixel/audio/video blocks in the compacted range; clipped tool args |

### Request-Flow Narrative (Startup to Turn to Offload/Summarize to Cache)

1. **Construction (control).** Application calls `create_deep_agent`. `skills=` attaches `SkillsMiddleware` **first** so the catalog lands in the stable prefix. `memory=` attaches `MemoryMiddleware` **last**, after `append_prompt_caching_middleware`, with `add_cache_control=True`. Summarization + `FilesystemMiddleware` offload are already in the bare stack. Anthropic cache middleware is unconditional (no-op off-provider).

2. **Startup `before_agent`.** Memory: `backend.download_files(sources)`; missing files skipped; other errors raise `ValueError`. Contents sit in private `memory_contents`. **Skip reload** if already in state (prior turn / checkpoint). Skills: scan **containers of skill directories** (a path that *is* the skill dir is **not** loaded); parse YAML frontmatter (`yaml.safe_load`); skip `SKILL.md` **> 10 MB**; inject name + description + `-> Read {path}`. **Skip** if `skills_metadata` already present (checkpointed session -- new skills mid-thread are invisible). HTML comments stripped from memory so OpenWiki's managed markers do not reach the model.

3. **First model call (data).** Logical system composition: custom `system_prompt` -> base/suffix -> memory block -> skills catalog -> VFS prompt -> subagent/`task` -> user middleware -> HITL. **Wrap order** stamps cache: Skills injects **before** cache middleware; Memory injects **after**, then tags the last system block as a second Anthropic breakpoint. Result: tools + skill fronts + FS docs = stable prefix; `AGENTS.md` = suffix that can change without rewriting the prefix. GitHub **#1356** was the inverse (volatile prepended -> one memory edit missed the entire suffix).

4. **Per-turn.** User message arrives. ToolRuntime injects typed runtime context. Model may `read_file` a skill path printed in the catalog (**not** a special skill tool). Prompt tells it to pass **`limit=1000`** because default `read_file` is **100 lines**. That is L2. L3 (`scripts/` / `references/` / `assets/`) is the model following the body; script **source** stays on disk if `execute`d -- only stdout enters context. If it skips `read_file`, it improvises from the ~100-token description.

5. **Offload.** Custom-tool / `execute` **result** > **20,000** tokens (tokenization = **4 chars/token** -> **80k characters**) -> write `/large_tool_results/<tool_call_id>`; replacement is preview (docs: first **10 lines**; source: **head and tail**) + `read_file`/`grep` instructions. Human message > **50,000** tokens (**200k chars**) uses the same machinery. **FS tools are excluded from immediate result eviction** (`ls`, `glob`, `grep`, `read_file`, `edit_file`, `write_file`). Offload measures **text tokens only** -- a screenshot-only message is **not** evicted by image size.

6. **Summarize.** Trigger `("fraction", 0.85)` of `model.profile["max_input_tokens"]`, keep `("fraction", 0.10)`; no profile -> **170,000** tokens trigger / keep **6** messages. Dual write: in-context structured summary **and** `/conversation_history/{session_id}.md`. Raw `messages` are **not** deleted (unlike stock LangChain `RemoveMessage`). `ContextOverflowError` -> summarize + retry. Stream tokens from the extra call have `metadata.lc_source == "summarization"` -- filter them or the user sees the agent "talking to itself."

7. **Cache hit/miss.** Anthropic conversational cache marks stable system + tools and caches **through the latest message**. Hit = **0.1x** input (Fable/Mythos 5.1: **0.025x**); 5m write **1.25x**; 1h write **2x**. TTL **5m** sliding on hit (override `ttl="1h"` for human gaps). Below provider min prefix (**1,024** Sonnet 4.6; **4,096** Haiku 4.5 / Opus 4.6) the marker is a **silent no-op**. After 5m idle, next turn is a **write**, not a read. Cache does not equal memory; checkpointer persists history.

8. **Optional skill body / wiki / blob.** `read_file` pulls L2, an OpenWiki page after the `AGENTS.md` pointer, or a slice of `/large_tool_results/`. Those bytes join **messages** (usually **not** the cached system prefix). Activating one skill adds **<5k** once, then it can sit in the conversational tail.

Runtime `context=` (`user_id`, API keys) is **not** in the prompt unless a tool or `@dynamic_prompt` copies it. Tools should read `runtime.context`; dumping `user_id` into the system prompt **per user** fragments the cache; dumping `api_key` caches a secret for TTL.

---

## Core Concepts & Algorithms

### 1. Context Schema vs State Schema

- `context_schema=` defines per-run runtime context (immutable). Use a `dataclass` or `TypedDict` shape, then pass actual values at invoke time with `context=...`. Values are hidden from the model unless a tool, middleware, or prompt builder reads and injects them.

- `state_schema=` defines mutable graph state that should be checkpointed and updated during execution.

- `@dynamic_prompt` is the right primitive when instructions depend on runtime context such as user role, access level, feature flags, or stored preferences.

- Runtime context propagates to subagents. If one subagent needs special settings, use namespaced keys such as `researcher:max_depth`.

### 2. Skills: Progressive Disclosure

Skills are Deep Agents' reusable workflow and domain-knowledge primitive. They solve a common context problem: some instructions are important, but not important enough to preload into every prompt forever. A skill is "progressive-disclosure procedural memory" -- a package of instructions that the agent can discover, open, and then follow when relevant.

**The Skill Identity Pattern**: rather than routing to specialized sub-agents, a single agent assumes different identities on demand. At rest it has a base identity. When a skill activates, it adopts that skill's instructions, constraints, tone, and behavioral patterns. When the task completes, it returns to base. This avoids the overhead of subagent context assembly for lightweight specialization.

**Three-tier loading strategy:**

| Level | What Loads | When | Token Cost |
|-------|-----------|------|------------|
| **1 Metadata** | `name` + `description` from frontmatter | Startup, every configured skill | **~100 tokens/skill** |
| **2 Instructions** | Full SKILL.md body | Agent `read_file`s the path | **<5,000 tokens** recommended; spec also **<500 lines** |
| **3 Resources** | `scripts/`, `references/`, `assets/` | Instructions reference them | **0 until accessed**; scripts: stdout only if executed |

```text
skills=["/skills/"]
  -> SkillsMiddleware scans each source directory
  -> parse SKILL.md frontmatter
  -> inject skill name + description into startup prompt
  -> model decides a skill is relevant
  -> read full SKILL.md through read_file
  -> follow instructions
  -> optionally open scripts/, references/, assets/
```

**SKILL.md frontmatter fields:**
- `name` (required): lowercase alphanumeric plus hyphens, 1-64 chars, must match parent directory name
- `description` (required): max 1024 chars (truncated to 1024 for the model)
- `license` (optional)
- `compatibility` (optional, max 500 chars)
- `metadata` (optional key-value mapping)
- `allowed-tools` (experimental): space-separated list of pre-approved tools the skill can use. **Not enforced** -- experimental, not a security boundary.

**Source path rule:** Source paths in `skills=` must point to directories that **contain** skill directories. Pointing directly at the skill directory itself does not load it. Last source wins on `name` collision.

**Subagent inheritance is asymmetric:**
- The auto-added `general-purpose` subagent inherits the main agent's skills
- Custom subagents do **not** inherit them by default and need their own `skills=` paths
- Skill state is isolated between parent and child

**Deep Agents allows unicode lowercase (`cafe`) beyond Claude API's ASCII + reserved-word rules** -- a portable skill may load here and fail on Claude's Skills API.

**Agent Skills Specification (agentskills.io):** Released by Anthropic (October 2025), governance moved to AAIF under the Linux Foundation. As of mid-2026: ~40 products support it, ~60,000 repos use it, ~1.9M public skills indexed. Licensed Apache 2.0 (code) / CC-BY-4.0 (docs). A 2026 audit found **prompt injection in 36% of tested public skills**. Treat community skills like open-source dependencies: review before install.

### 3. Memory: AGENTS.md

Deep Agents treats memory as a filesystem-backed persistence layer, not as vague "the model remembers things." Memory matters because project conventions, user preferences, and durable agent instructions should survive across threads without being manually re-pasted into every run.

**Two memory modes to keep distinct:**
- **Semantic memory**: durable files such as `AGENTS.md`, preferences, and policies -- always loaded
- **Episodic memory**: past conversation history preserved as checkpointed threads -- accessible via search tools

**Key difference from skills:** Memory is always loaded because it is assumed to be always relevant. Skills are loaded progressively only when needed.

Community convention: freeform Markdown, **no required schema**. Typical sections: overview, build/test, style, security. Wrapped as `<agent_memory>` plus `<memory_guidelines>`: update when the user says "remember," role/behavior, feedback, durable IDs, conventions; **do not** update for transient status, one-shot tasks, small talk, stale facts; **never** store credentials (instruction, **not** enforcement).

**Scoping is a backend design question:**

| Scope | Namespace example | Write policy |
| --- | --- | --- |
| Agent | `(assistant_id,)` | Shared persona; last-write-wins races |
| User | `(user_id,)` | Isolated preferences (default for tenancy) |
| Org | `(org_id,)` | Policies; **read-only** recommended |
| Agent+user | `(assistant_id, user_id)` | Multi-agent deploy |

**Update strategies:**
- **Hot path**: agent `edit_file`s during the conversation. Immediate but adds latency and increases chance the agent spends too much effort maintaining memory mid-task.
- **Background consolidation ("sleep time")**: a separate agent + cron (`0 */6 * * *` example) consolidates threads. **Cron interval must match the lookback window** or you reprocess / drop. Reduces user-facing latency but introduces staleness.

**Episodic memory** already exists because Deep Agents use checkpointers. What is missing by default is search over past threads, which you add by wrapping thread-history APIs in a tool. Do not dump full history into the system prompt.

**`MemoryMiddleware` does NOT re-read after `edit_file` in the same session** (`skip-if-loaded`). Next **new** agent run sees the file. Checkpointed `memory_contents` can serve **stale memory across invokes** until that private field is cleared.

**Scalability constraint**: Memory files grow the system prompt linearly. For applications needing to remember many past interactions, RAG with a vector store is the correct pattern.

### 4. Summarization and Context Offloading

Long-running agents fail because tool outputs, file contents, and conversation history keep piling up until the model can no longer see the right context. Deep Agents addresses this with a two-step strategy: offload large tool I/O first, then summarize history when the context window is pressured.

**Key design decision**: The 85% threshold is deliberately conservative. Setting it at 90% creates the "compressor overflow trap" -- by the time compression fires, the middle region may be 180K tokens, which exceeds the summarizer model's own context limit.

**Two-phase compression with distinct mechanisms:**

```
Tool-Level Offloading (per result):
  IF tool_result_tokens > 20,000:
    full_text --> filesystem
    context <-- file_path + first_10_lines

Window-Level Summarization (global):
  IF total_context > 0.85 * max_input_tokens:
    old_messages --> LLM summarizer
    context <-- structured_summary + recent_10%
    filesystem <-- full_text_rendering

Emergency Recovery:
  IF ContextOverflowError:
    immediate_summarization() + retry()
```

| Event | When | Replacement |
| --- | --- | --- |
| Tool **result** > 20k tokens | Immediate | `/large_tool_results/<tool_call_id>` + preview + page-back instructions. **Not** FS built-in results |
| Human message > 50k tokens | Immediate | Same eviction |
| Tool **args** on write/edit | Delayed until session >= **85%** | Truncate older args (files already on disk); default clip = first 20 chars + suffix |
| Window >= 85% (or 170k / 6 fallback) | Summarizer hop | Model view = summary + keep 10% (or 6 msgs); markdown history on VFS |

**Deep Agents factory vs stock LangChain `SummarizationMiddleware`**: raw `messages` kept vs `RemoveMessage`; recovery path `/conversation_history/{id}.md` vs none; `ContextOverflowError` retry; defaults 0.85/0.10 vs `trigger=None`, `keep=("messages", 20)`.

**Multimodal context limitation**: Context compression is text-oriented. During summarization, image, audio, video, and file blocks are **discarded** -- only text descriptions survive. The offloading mechanism measures text tokens only; non-text blocks are preserved but not compressed by size. Production mitigation: delegate multimodal-heavy inspection to subagents that return compact text results before media enters the main agent's compaction cycle.

**Claude visual tokens**: `ceil(width/28) x ceil(height/28)`. Standard-tier cap **1,568** visual tokens / **1,568 px** long edge; high-res **4,784 / 2,576 px**. Above 20 images/request, a stricter per-image pixel cap applies. API: up to **600** images/request (100 on 200k-context models); claude.ai **20 / turn**.

### 5. Prompt Caching

Deep Agents repeatedly resends large static prefixes: system instructions, tool schemas, memory, and skill-related prompt material. Prompt caching is the harness-level optimization that targets exactly this problem. Deep Agents does not ask you to wire caching -- it auto-registers provider-specific caching middleware.

**Cache key derivation**: exact bytes in fixed order (tools -> system -> messages). One changed character at position N invalidates everything after position N.

**Middleware placement matters**: The caching middleware runs after `PatchToolCallsMiddleware` and after your own middleware so the cached prefix matches what is actually sent to the model.

```
  Skills (slot 1) ----> FS / SubAgent / Summarization / Patch ----> caller mw
         |                                                      |
         | catalog in STABLE prefix                             |
         v                                                      v
  AnthropicPromptCachingMiddleware  (+ Bedrock / Fireworks extras)
         |  stamps cache_control on bytes about to be sent
         v
  MemoryMiddleware (LAST if memory=) ----> AGENTS.md SUFFIX + 2nd breakpoint
```

**Why Memory goes after cache**: Because `AGENTS.md` is volatile. If Memory runs first, a single `edit_file` invalidates tools + skill catalog + FS docs -- GitHub #1356. The second breakpoint **no-ops** on Bedrock/Vertex wrappers (`isinstance(request.model, ChatAnthropic)` only) -- Bedrock agents may bust more prefix on a memory edit.

**Token thresholds for cache checkpoints:**
- Claude Sonnet 4.6: **1,024** tokens (lower threshold)
- Claude Opus 4.6/4.5, Sonnet 4.5, Haiku 4.5: **4,096** tokens per checkpoint
- Bedrock: max **4** checkpoints; not on batch; Nova often **5m only**

**Provider coverage:**
- `create_deep_agent` automatically wires prompt caching for supported Anthropic and Bedrock models
- Both `AnthropicPromptCachingMiddleware` and `BedrockPromptCachingMiddleware` are always registered with `unsupported_model_behavior="ignore"`
- Fireworks: replica-local prefix cache; middleware maps `thread_id` to session affinity; `ModelFallbackMiddleware` strips Fireworks headers before a non-Fireworks fallback
- Gemini implicit cache and OpenAI are **not** wired in `_prompt_caching.py`

**TTL behavior:**
- Default: **5 minutes** (sliding on hit)
- Override for HITL: `AnthropicPromptCachingMiddleware(ttl="1h")` (2x write cost)
- Warm-keeping: a request at least every 5 minutes keeps the cache alive indefinitely
- Override TTL **in place** by `.name == "AnthropicPromptCachingMiddleware"`; do **not** append a second cache middleware

**Lookback**: **20 content blocks** from the breakpoint (consecutive `tool_use` count as one position; consecutive `tool_result` as one). Growing tool loop can walk the breakpoint off the last write, causing every turn to **write** 1.25x.

### 6. OpenWiki (Durable Wiki, Not a Prompt)

CLI **built on Deep Agents**. Agents are the primary audience; humans get a visualizer on **127.0.0.1** (or static export). GitHub `langchain-ai/openwiki`: MIT, created **2026-06-22**, ~**16k** stars at research time, Node **22+**, `npm i -g openwiki`.

| Path | Role |
| --- | --- |
| `openwiki/` | Generated Markdown (OKF v0.2) |
| `openwiki/INSTRUCTIONS.md` | User brief; `--init`/`--update` do **not** rewrite it |
| `openwiki/.claims/` | Versioned Grounded Claims sidecars |
| `openwiki/.page-manifest.json` | Per-page checkpoints |
| `openwiki/.last-update.json` | Last successful check, including no-ops |

OpenWiki is a wiki on the VFS, not a second system prompt. It writes Markdown under `openwiki/` and maintains a managed snippet in root `AGENTS.md` / `CLAUDE.md` pointing at `openwiki/quickstart.md`. Pages enter the window only if the agent `read_file`s them.

**Wrong defaults:** 50 overlapping skills; 50k `AGENTS.md`; wiki regen every user turn; disabling eviction.

---

## Token Economics & Cost Analysis

Prices: Claude Sonnet 4.6 input **$3 / MTok**, 5m write **$3.75**, 1h write **$6**, read **$0.30**, output **$15**. Haiku 4.5 **$1 / $5**.

### Cache Multipliers

| Op | Multiplier vs base input | TTL |
| --- | --- | --- |
| 5m write | **1.25x** | 5 min, sliding on hit |
| 1h write | **2x** | 1 hour |
| Read | **0.1x** (Fable/Mythos 5.1: **0.025x**) | Same as write |

10-call stable prefix vs 10x uncached: Anthropic 5m **0.215x**; Anthropic 1h **0.29x**; Fireworks serverless default 50% cached input **0.55x**; uncached **1.0x**.

A **2k** Deep Agents prefix caches on Sonnet 4.6 (**1,024** min) and **misses on Haiku 4.5** (**4,096**) unless you pad.

### Per-Turn Cost Formulas

**Without caching:**
```
C_turn = (input_tokens * P_input) + (output_tokens * P_output)
```

**With prompt caching:**
```
C_turn = (cache_write_tokens * P_write)          # First turn only (for new prefix)
       + (cache_read_tokens * 0.1 * P_input)     # Subsequent turns
       + (new_tokens * P_input)                   # Non-cached portion
       + (output_tokens * P_output)
```

**Worked example** (Claude Sonnet 4.6, 8K stable prefix, 2K new history/turn):
- No caching: ~$0.51 over 10 turns
- With caching: ~$0.14 over 10 turns (72% savings)
- With caching: ~$0.07/turn at 50 turns (86% savings, amortized write cost)

### Skill Fronts vs Stuffed Bodies [inferred]

Sonnet 4.6 $3/MTok, **no cache**, one call:

| Catalog | Always-on tokens | Uncached $ / 1k calls |
| --- | --- | --- |
| 20 skills x 100 tok | 2,000 | **$6** |
| 20 skills x 4,000 tok stuffed | 80,000 | **$240** |
| 50 skills x 100 tok | 5,000 | **$15** |
| 50 skills x 4,000 stuffed | 200,000 | **$600** |

5m cache, 10 calls/run, 1 write + 9 reads of the catalog only:

| Catalog | $ / 1k runs |
| --- | --- |
| 2k frontmatter | **$13** |
| 80k stuffed | **$516** |

Disclosure saves **~40x** on a 20-skill library if bodies average 4k and would otherwise be stuffed.

### $ Cost Per 1k Runs -- Cache On/Off, Fat vs Thin Memory [inferred]

| Variant | Cached prefix | $ / run | $ / 1k |
| --- | --- | --- | --- |
| No cache, 2k prefix + 3k dynamic | -- | $0.270 | **$270** |
| Cache, 2k prefix (thin, no memory/skills) | 2k | $0.223 | **$223** |
| Cache, 2k + 20 skill fronts (4k total) | 4k | $0.236 | **$236** |
| Cache, **fat memory 20k** + 2k harness = 22k | 22k | $0.352 | **$352** |
| No cache, fat 22k + 3k dynamic | -- | $0.870 | **$870** |
| Cache, **fat 50k memory** + 2k = 52k | 52k | $0.545 | **$545** |
| No cache, 52k + 3k | -- | $1.770 | **$1,770** |

Fat memory still **wins vs uncached** (52k cached **$545/1k** vs **$1,770/1k**) **if** the file is stable inside TTL. If `edit_file` rewrites `AGENTS.md` **every turn**, memory writes alone cost **$1,875 / 1k** -- **worse than not caching that section**. Keep memory small; put workflows in skills.

### Offload vs Stuffing a 50k-Token Dump, 8 Later Turns [inferred]

| Path | $ / 1k |
| --- | --- |
| Stuff | **$1,200** |
| Offload (~400 tok preview x 8) | **$10** |
| Stuff + cache read 0.1x (usually does not apply -- blob sits in messages, not stable prefix) | **$120** |

### Summarization Extra Hop [inferred]

80k history in + 2k summary out. Sonnet 4.6: **$0.270 / hop -> $270 / 1k** runs that compact once. Haiku 4.5: **$0.090 / hop -> $90 / 1k**.

### Cost Reduction Mechanisms Summary

| Mechanism | Savings | Trade-off |
|-----------|---------|-----------|
| Prompt caching | 72-86% input cost | 5-min TTL requires warm-keeping |
| Progressive disclosure | ~100 tok/skill vs. 275-8,000 eager | Adds one LLM read decision per skill |
| Subagent isolation | 10:1 to 50:1 compression | Latency per delegation (fresh invocation) |
| Tool exclusion | Variable (per tool removed) | Reduces agent capability |
| Offloading | Prevents context overflow | Loses in-context searchability |

### Context Budget Parameters

| Parameter | Default Value | Tuning Guidance |
|-----------|---------------|-----------------|
| Working context budget | 200,000 tokens | Model-dependent |
| Offload threshold | 20,000 tokens/tool call | Lower for chatty tools |
| Summarization trigger | 85% of max_input_tokens | Never above 90% (compressor trap) |
| Recent context preserved | 10% of tokens | Increase for high-coherence tasks |
| Fallback trigger | 170K tokens / 6 messages | When model profile unavailable |
| Offloaded result preview | First 10 lines | Sufficient for most tool outputs |

### Latency Analysis

| Path | **p50** | **p95** | **p99** | Grounding |
| --- | --- | --- | --- | --- |
| **100k stuffed, cache HIT (published)** | **2,400 ms** | -- | -- | Anthropic book demo |
| **100k stuffed, cache MISS (published)** | **11,500 ms** | -- | -- | Same demo |
| **10k many-shot HIT / MISS (published)** | **1,100 / 1,600 ms** | -- | -- | Closest hosted pair to ~2-8k prefix |
| **10-turn conversational HIT / MISS (published)** | **~2,500 / ~10,000 ms** | -- | -- | Anthropic 10-turn example |
| **Parent TTFT, 2k prefix cache HIT** [inferred] | **1,000 ms** | **2,000 ms** | **3,500 ms** | Hosted prefix-hot |
| **Parent TTFT, 2k prefix cache MISS** [inferred] | **1,600 ms** | **5,000 ms** | **12,000 ms** | Hosted prefix-cold |
| **Parent TTFT, 100k stuffed HIT** [inferred] | **2,400 ms** | **5,000 ms** | **12,000 ms** | p50 = published 2.4s |
| **Parent TTFT, 100k stuffed MISS** [inferred] | **11,500 ms** | **20,000 ms** | **40,000 ms** | p50 = published 11.5s |
| **One ReAct cycle (model + local read_file)** [inferred] | **2,000 ms** | **8,000 ms** | **20,000 ms** | VFS extra is not the tail |
| **Skill L2 activation (read + next model call)** [inferred] | **2,000 ms** | **8,000 ms** | **20,000 ms** | Dominated by extra ReAct |
| **Summarizer extra hop** [inferred] | **2,000 ms** | **6,000 ms** | **15,000 ms** | Extra LLM over evicted prefix |
| **Hot-path memory edit_file extra turn** [inferred] | **2,000 ms** | **8,000 ms** | **20,000 ms** | Why docs recommend background consolidation |
| **HITL on /memories/ write** [inferred] | **30,000 ms** | **180,000 ms** | **600,000 ms** | Seconds-minutes; expire -> deny |

**Latency SLA targets** (production guidance):

| Metric | Interactive Agent | Background Agent |
|--------|------------------|-----------------|
| p50 TTFT | <2s | N/A |
| p95 TTFT | <5s | <10s |
| p99 TTFT | <10s | <30s |
| p50 full response | <15s | <60s |
| p95 full response | <45s | <180s |

**Effective context capacity**: Research shows performance degrades starting at ~65% of advertised window (effective capacity ~130K for a 200K model). Plan for 60-70% utilization as the usable ceiling.

### Availability, RPO/RTO

| NFR | Target | Rationale |
|-----|--------|-----------|
| Availability (context assembly) | 99.9% | Core path for every agent turn |
| Availability (compression) | 99.5% | LLM-dependent; summarizer outage degrades but does not block |
| RPO (checkpoint-backed memory, PostgresSaver) | 0 | Every checkpoint is durable |
| RPO (MemorySaver / in-memory) | 1 conversation (total loss) | Never use in production |
| RTO (context restoration from checkpoint) | <30s | Reload thread state + reassemble prompt |
| RTO (full memory rehydration from Store) | <5 min | Cross-thread retrieval |
| RPO of provider cache | **None after TTL** | 5m/1h then gone. Ephemeral. Not a checkpointer |

---

## Trade-offs & Failure Modes

### Core Design Trade-offs

| Axis | Skills (progressive) | Fat AGENTS.md | Offload 20k | Summarize 85% | Prompt cache 5m | OpenWiki |
| --- | --- | --- | --- | --- | --- | --- |
| **Cost** | Best if many procedures | Worst if large/churn | Best vs stuffing blobs | Extra hop $ | 0.1x reads if stable | CI tokens, then cheap reads |
| **Latency** | Extra read_file | Prefill tax | Cheap after eviction | Extra LLM | TTFT down on hit; 5m miss = write | N/A at query time |
| **Freshness** | Reload skipped if checkpointed | Skip-if-loaded | Immediate for results | Drops media | TTL | Claims-driven update |
| **Security** | Untrusted body; deny-write org | Poisoning; PII always on | Blobs on backend ACL | Summary may leak PII | Secrets in KV TTL | Ignore file does not equal redaction |

### NFR Trade-offs

| NFR | Production stance | Competes with |
| --- | --- | --- |
| **Availability of chat vs summarizer/cache** | Product SLO is the parent loop + thin prefix. Summarizer is **best-effort** (skip compact). Cache miss is a **cost/TTFT degrade**, not a 500. Store outage on `memory=` download is **hard** (`ValueError` on non-missing) | Long-horizon fidelity vs user p99 |
| **RPO of memory** | Last successful `edit_file` / Store put. Last-write-wins -- **not** CAS | Lifelong persona vs poisoning |
| **RPO of skills** | Last file on backend. Checkpointed `skills_metadata` hides new files until new session | Hot-reload vs cache stability |
| **Compliance** | **Not provided by `deepagents`.** Always-on memory + cached KV are **processing** of whatever users saved. No Deep Agents DPA. GDPR erasure = Store/FS memory + skills + offloads + wiki + traces + cache expiry | Debug vs residency |

### Common Failure Modes

| Failure | Cause | Detection | Mitigation |
| --- | --- | --- | --- |
| Fat AGENTS.md busts cache | Always injected; edit_file rewrites suffix | `cache_read_input_tokens` collapse; 1.25x on 20-50k every turn | Keep memory minimal; workflows -> skills; ttl="1h" for sparse HITL |
| Memory **before** cache (#1356) | Fork of graph.py or volatile .name wrap prepends | Whole prefix misses on one edit | Memory **after** cache; override TTL in place |
| Bedrock no second breakpoint | `add_cache_control` is ChatAnthropic-only | Memory edits bust more prefix | Keep memory tiny |
| Summarizer drops media / clips args | Text-oriented compact | Model "remembers" a write it cannot see; screenshots gone | `read_file` VFS; media as paths |
| Skills not discovered | `skills=` points at the skill dir; >10 MB; checkpointed metadata | Empty catalog | Parent container path; fresh session for new skills |
| Description overlap | L1 is the **only** router | Wrong skill or hesitation | Action-oriented: "Use when drafting..." not "Helps with email" |
| Default read_file 100 lines | Agent "uses" a truncated body | Partial procedure | Prompt already says `limit=1000` |
| Multimodal blowup | Images skip 20k text offload; ~1.5k tok/image | 85% trips on pixels; then summarization deletes them | Paths/URLs; <=20 images; subagent for inspection |
| 20-block lookback miss | Conversational breakpoint walks >=20 blocks past last write | Every turn **writes** 1.25x | Extra breakpoint on the static block |
| Haiku 4.5 + 2k prefix | Min **4,096**; marker silent no-op | 1.0x forever, "cache is on" | Pad, different model, or accept uncached |
| compact_conversation <50% | Tool no-ops | Agent thinks it compacted | Rely on 85% auto |
| Cron/lookback skew | 6h cron != 6h lookback | Duplicate or dropped consolidations | Match the documented pair |
| Memory staleness | Outdated AGENTS.md silently biases behavior | Conflicting tool evidence | Version memory, validate against tool evidence |
| Over-compression | Agent erases task-critical evidence | Lost constraints | Lower threshold, use structured notes |

**Drew Breunig's Four Failure Modes Framework:**

| Mode | Attack Vector | Mitigation |
|------|--------------|------------|
| Context Poisoning | Incorrect info injected early persists | Input validation, source verification |
| Distraction | Irrelevant content dilutes attention | Compression, filtering |
| Confusion | Too many tools or conflicting instructions | Tool selection middleware, skill boundaries |
| Clash | Multi-turn info accumulates contradictions | Isolation, structured memory |

Compression alone handles Distraction but does nothing about Confusion or Clash.

---

## Production Patterns & Best Practices

### Durable Execution: Skills/Memory Persist vs 5m Cache

| Backend | Skill/memory durability | Failure mode for context |
| --- | --- | --- |
| `StateBackend` | Thread + checkpointer | Lost if no checkpointer; skills must be seeded via `invoke(files=)` |
| `StoreBackend` | Cross-thread; namespace = tenant | Store outage -> `ValueError` on non-missing |
| `FilesystemBackend` | Disk under `root_dir` | Offloads + history land on real disk unless composited |
| `ContextHubBackend` | Hub commits, optimistic parent_commit | Skills/AGENTS.md as versioned Hub repo |
| `CompositeBackend` | Route `/skills/`, `/memories/`, `/large_tool_results/` separately | Mis-route writes org policy into a user namespace |

| Need | Mechanism | Survives new thread_id? | Survives 5m idle? |
| --- | --- | --- | --- |
| Same-thread resume | Checkpointer | Yes (same id) | Yes |
| Preferences across threads | `memory=` on Store/FS | **Yes** | Yes |
| Skill catalog | `skills=` on Store/FS | **Yes** | Yes |
| Scratch files | `StateBackend` | **No** | Yes in-thread |
| Provider KV | Cache middleware | No | **No** (TTL) |

### Circuit Breaker: Summarizer

`deepagents` does **not** ship a summarizer circuit breaker. Build your own.

```
        summarizer 429/5xx | error-rate window | ContextOverflowError storm
  +----------+  -------------------------------------------------------->  +----------+
  |  CLOSED  |                                                               |   OPEN   |
  |  compact |  success resets consecutive count                             | SKIP     |
  +----+-----+                                                               | compact  |
       ^                                                                     | keep FS  |
       | probe OK                                                            +----+-----+
       |                                                                          | cooldown
       |                                                                    +-----v------+
       +------------ probe allow -----------------------------------------------| HALF-OPEN|
                    probe fail -> stay OPEN                                  | 1 synthetic|
                                                                            | compact    |
                                                                            +------------+
```

**Fallback chain (required interview answer):** **cached prefix (thin) -> uncached (same thin payload) -> refuse fat context.** Never: summarizer down -> stuff 20k tool dumps back into messages. Never: cache miss -> disable offload. Never: Store down -> concatenate wiki into `system_prompt`. Never: circuit open -> `excluded_middleware` filesystem.

### Zero-Trust Context Pipeline

Skill bodies, `AGENTS.md`, OpenWiki pages, and MCP-served files are **untrusted content**. Identity is the **verified access token** bound into `context_schema` / `runtime.server_info.user.identity` -- **never** a `user_id` the model emitted in JSON. Progressive disclosure means a human who "approved skills" blessed a **~100-token description**; the 5k body loads later.

| Zero-Trust control | On this context plane |
| --- | --- |
| **Transport** | OAuth 2.1 + PKCE S256. RFC **8707** resource = canonical MCP server URI. **MUST NOT** passthrough the client token |
| **Untrusted files** | `yaml.safe_load`; 10 MB cap; escaped load warnings; container-of-dirs. Audit all bundled scripts/references |
| **Hash-pin MCP tools** | `toolSurfaceHash` over name + description + schemas; re-verify every `tools/call` |
| **Identity** | IdP token -> namespace `(user_id,)` / `(org_id,)`. Model JSON is a **proposal** |

### PII Pipeline: Detect -> Redact -> Audit

Always-on memory is in **every** subsequent system prompt and, on Anthropic, in the **cached suffix**. User-scoped files (emails the model was told to save) are PII in GPU/cache for TTL.

1. **Detection (control plane, before bytes leave the trust boundary).** Dual-gate: **regex** (email, PAN, SSN, phones, `sk-`/`AKIA` credential shapes) + **ML NER** if you have a scanner. If ML is down: **fail closed to mask** on chat; **fail closed (block)** on memory/skill writes.

2. **Redaction.** `redact` / `mask` / `hash` to stable tokens (`[EMAIL_<hash12>]`) so preferences can continue; `block` when the field must not exist. Strip the value from the VFS file **and** from `memory_contents` **before** `modify_request` injects `<agent_memory>`.

3. **Audit trail (WORM, immutable).** Log **decisions**, not values: `content_sha256` pre- and post-redact, entity **types** + counts, action, detector, `correlation_id`, `tenant`, `thread_id`, path.

`PIIMiddleware` does **not** redact `AGENTS.md` once it is already in the system prompt. Redact **before** write, or deny the path.

### RBAC for Context Access

| Role | Context Scope | Memory Access | Skill Management |
|------|--------------|---------------|-----------------|
| **User** | Own conversation only | Read/write own namespace | Use skills; no install/modify |
| **Team Lead** | Team + shared memory | Read team; write own | Install org-approved skills |
| **Admin** | All contexts + skill mgmt | Full read/write across namespaces | Install/modify/remove any skill |
| **Auditor** | Read-only context logs | Read-only all namespaces | Read skill configs; no modification |

---

## Code Examples

### Production Context-Managed Agent

```python
"""
Production context-engineered agent with memory, skills, prompt caching,
and multi-tenant isolation. Requires: pip install langchain-deepagents
"""
from dataclasses import dataclass
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.permissions import FilesystemPermission
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore


@dataclass
class TenantContext:
    """Typed context propagated to all subagents automatically."""
    user_id: str
    org_id: str
    api_key: str
    tier: str  # "free" | "pro" | "enterprise"


def build_backend(store: Any) -> CompositeBackend:
    """Multi-tenant backend with namespace isolation."""
    return CompositeBackend(
        default=StateBackend(),
        routes={
            "/memories/": StoreBackend(
                namespace=lambda rt: ("memories", rt.context.user_id),
            ),
            "/skills/shared/": StoreBackend(
                namespace=lambda rt: ("org_skills", rt.context.org_id),
            ),
            "/skills/personal/": StoreBackend(
                namespace=lambda rt: ("user_skills", rt.context.user_id),
            ),
        },
    )


def create_context_managed_agent(
    model: str = "anthropic:claude-sonnet-4-6",
) -> Any:
    store = InMemoryStore()  # Replace with PostgresStore for production
    checkpointer = MemorySaver()  # Replace with PostgresSaver for production

    agent = create_deep_agent(
        model=model,
        system_prompt=(
            "You are a production support agent. Always verify information "
            "against tool evidence before responding. If memory (AGENTS.md) "
            "conflicts with current tool output, trust the tool output."
        ),
        memory=["./AGENTS.md"],
        skills=["/skills/"],
        store=store,
        checkpointer=checkpointer,
        backend=build_backend(store),
        permissions=[
            FilesystemPermission(
                operations=["write"], paths=["/skills/**"], mode="deny"
            ),
            FilesystemPermission(
                operations=["write"], paths=["/output/**"], mode="allow"
            ),
            FilesystemPermission(
                operations=["write"], paths=["/**"], mode="interrupt"
            ),
        ],
        interrupt_on={"delete_file": True},
    )
    return agent
```

### SKILL.md Example with Frontmatter

```python
SKILL_FRONTMATTER = {
    "name": "incident-triage",
    "description": (
        "Use when the user reports a production incident or outage. "
        "Guides structured incident triage: severity classification, "
        "blast radius assessment, and runbook execution."
    ),
    "license": "Apache-2.0",
    "compatibility": "Requires access to PagerDuty and Datadog MCP servers",
    "metadata": {
        "author": "platform-team",
        "version": "2.1.0",
    },
    "allowed-tools": "pagerduty_get_incident datadog_query_metrics",
}

SKILL_BODY = """
# Incident Triage Skill

## When Activated
You are now an incident triage specialist. Follow this protocol exactly.

## Severity Classification
1. Query current alerts via PagerDuty
2. Check error rates and latency via Datadog
3. Classify severity:
   - SEV1: Revenue impact or data loss. Page on-call immediately.
   - SEV2: Degraded experience for >10% users. Notify team channel.
   - SEV3: Minor issue, no user impact. Create ticket.

## Blast Radius Assessment
- Which services are affected? (check dependency graph)
- Which regions? (check regional metrics)
- How many users? (check active session counts)

## When Complete
Return to base identity. Do not retain incident-specific context
beyond the summary provided to the user.
"""
```

### Context Budget Monitor

```python
class ContextBudgetMonitor:
    """Track context window utilization across turns."""

    def __init__(self, max_tokens: int = 200_000, warn_at: float = 0.70):
        self.max_tokens = max_tokens
        self.warn_threshold = warn_at
        self.turn_history: list[dict] = []

    def record_turn(self, input_tokens: int, output_tokens: int, cached: bool) -> dict:
        utilization = input_tokens / self.max_tokens
        entry = {
            "turn": len(self.turn_history) + 1,
            "input_tokens": input_tokens,
            "utilization": round(utilization, 3),
            "cached": cached,
            "alert": utilization > self.warn_threshold,
        }
        self.turn_history.append(entry)
        return entry

    def cost_report(self, price_per_1k_input: float, price_per_1k_output: float) -> dict:
        total_input = sum(t["input_tokens"] for t in self.turn_history)
        cached_input = sum(t["input_tokens"] for t in self.turn_history if t["cached"])
        uncached_input = total_input - cached_input
        total_output = sum(t.get("output_tokens", 0) for t in self.turn_history)

        cost = (
            (uncached_input / 1000 * price_per_1k_input)
            + (cached_input / 1000 * price_per_1k_input * 0.1)
            + (total_output / 1000 * price_per_1k_output)
        )
        cost_without_cache = (
            (total_input / 1000 * price_per_1k_input)
            + (total_output / 1000 * price_per_1k_output)
        )
        return {
            "total_turns": len(self.turn_history),
            "cache_hit_rate": cached_input / total_input if total_input else 0,
            "estimated_cost": round(cost, 4),
            "savings_pct": round(
                (1 - cost / cost_without_cache) * 100 if cost_without_cache else 0, 1
            ),
        }
```

---

## Interview Q&A

**Q1. What is Deep Agents context, in one minute?**
I treat it as four layers around one model call, not a second window. Skills are progressive disclosure: ~100 tokens of name+description at startup, body on `read_file`, resources later. Memory is always-on `AGENTS.md` -- I keep it tiny and user-scoped. Offload at 20k tokens (4 chars/token -> 80k chars) then summarize at 85% keep 10%, with raw graph messages still growing. Prompt cache is provider KV, 5m default, 1.25x write / 0.1x read. OpenWiki is a durable wiki on the filesystem that only *points* from `AGENTS.md`; I do not stuff it.

**Q2. Walk a turn: startup -> cache -> optional skill body.**
`before_agent` downloads memory and skill frontmatter, skip-if-loaded. Skills middleware injects the catalog into the stable prefix; cache middleware stamps `cache_control`; Memory injects `AGENTS.md` as a suffix with a second Anthropic breakpoint. The model may `read_file` a skill path with `limit=1000`. Tool results over 20k go to `/large_tool_results/` unless they are built-in FS tools. At 85% I pay a summarizer hop and write `/conversation_history/{id}.md`. Cache hit if the prefix is exact and inside TTL; after 5m idle I pay a write.

**Q3. What is the difference between input context and runtime context?**
Input context is prompt-visible material such as system prompt, memory, skills, and tool prompts. Runtime context is invoke-time data hidden from the model unless code explicitly injects it. `context_schema` types the runtime context so tools and middleware can safely access values like user IDs, API keys, and feature flags.

**Q4. Why is Memory after cache?**
Because `AGENTS.md` is volatile. If Memory runs first, a single `edit_file` invalidates tools + skill catalog + FS docs -- GitHub #1356. Factory order: Skills first, Patch before cache, Memory last with `add_cache_control=True`. That split is ChatAnthropic-only; on Bedrock I assume more prefix busts and keep memory smaller. Override TTL in place; do not append a second cache middleware.

**Q5. When should I use `@dynamic_prompt`?**
When instructions depend on runtime context or stored data, such as role-based behavior or user-specific preferences. Use it instead of trying to hard-code every case into a static `system_prompt=`.

**Q6. What does summarization preserve?**
A structured in-context summary for working memory and a canonical text rendering of the original conversation in the filesystem. The recent 10% of tokens are preserved verbatim. Raw graph messages are **not** deleted (unlike stock LangChain `RemoveMessage`).

**Q7. Does this system handle multimodal compression automatically?**
No. Compression is text-oriented. Summarization **drops** image/audio/video/file blocks in the compacted range. Offloading measures text tokens only. Production mitigation: store media in backend/object store; pass paths/URLs; use subagents for image-heavy inspection.

**Q8. Give me $ per 1k: thin vs fat, cache on vs off.**
Inferred, Sonnet 4.6, 10 calls in 5m, GP off, 3k dynamic, 800 out. Thin 2k cached **$223 / 1k**, uncached **$270**. 20 skill fronts 4k cached **$236**. Fat 20k memory cached **$352** vs **$870** uncached. Fat 50k cached **$545** vs **$1,770** uncached. Rewrite 50k every turn: **$1,875 / 1k** in memory writes alone. Offload a 50k dump **$10 / 1k** vs stuff **$1,200**. One Sonnet summarizer hop **$270 / 1k**; Haiku **$90 / 1k**.

**Q9. Skills vs memory vs OpenWiki -- who goes where?**
Procedures and scripts -> skills (L1 catalog, L2 on read, L3 references). Identity, style, never-do, user prefs -> thin `AGENTS.md`. Durable repo architecture with evidence -> OpenWiki pages the agent `read_file`s after the pointer. Episodic threads stay in the checkpointer and a `threads.search` tool -- do not dump them into the system prompt. Org policy files are read-only and app-written.

**Q10. Do subagents inherit skills?**
Only the default `general-purpose` subagent does. Custom subagents need their own `skills=` configuration. Skill state is isolated between parent and child.

**Q11. What is `compact_conversation` for?**
It lets the agent trigger compaction on demand instead of waiting for the automatic threshold. But it is gated at ~50% of the auto trigger so the agent cannot compact a short chat -- below that the tool **no-ops**.

**Q12. How do tool descriptions affect context size?**
Unused tool schemas still consume prompt budget on every turn, which is why the docs recommend removing tools the agent should never call via `excluded_tools`.

**Q13. How does memory work in Deep Agents?**
You pass memory file paths with `memory=`, Deep Agents loads them into the prompt at startup, and the backend decides where they persist and how they are scoped. By default, the agent can update memory via `edit_file` (hot path). Alternatively, a background consolidation agent periodically synthesizes patterns across recent conversations.

**Q14. Why make shared memory read-only?**
To prevent prompt injection through shared durable state and keep policies under application control. If one user can write memory that another user later reads, you have a prompt-injection channel.

**Q15. Which providers get automatic prompt caching?**
Anthropic models and supported Amazon Bedrock models. Fireworks is a lazy extra. Gemini and OpenAI are **not** wired.

**Q16. What invalidates the cache?**
Any change in the repeated prefix: prompt edits, tool-schema changes, skill or memory changes, model/provider-specific prompt overlays. The cache keys off exact bytes in fixed order.

**Q17. Zero-Trust when skills and memory are just files?**
File content is untrusted, including SKILL.md and MCP-served docs. Identity comes from the verified token into the Store namespace, never from model JSON. `allowed-tools` is experimental and not enforced. `permissions=` fail-open, FS-tools-only -- an MCP writer is outside that PDP, so I need a gateway PEP: OAuth 2.1, RFC 8707 audience, no token passthrough, hash-pinned tool JSON. Progressive disclosure hides the body at approval time; I treat a third-party `skills=` dir like installing software. Org skills come from application code; deny-write `/skills/**`.

**Q18. PII -- detect -> redact -> audit on memory.**
Always-on memory is in every later prompt and in the cached suffix. I detect regex + optional ML before `edit_file` lands; redact emails to stable hashes; **block** PAN and API keys from memory and skill writes; audit WORM of pre/post hashes, kinds, action, detector, cid, tenant, path -- not the raw value. If ML is down I still regex-mask chat and fail-closed block credentials into memory.

**Q19. Circuit breaker and fallback on this plane.**
The library does not ship a summarizer breaker. I wrap the hop: closed -> open -> half-open, one probe. Open means **skip compact**, keep the filesystem, keep offload. Fallback for the payload is cached thin prefix -> uncached thin -> refuse fat context. Never re-inline a 20k dump because the summarizer is down.

**Q20. What survives a new thread_id vs a new process vs 5m idle?**
New thread: Store/FS memory and skills yes; StateBackend scratch no; provider cache no after TTL. New process, same thread: checkpointer + store. 5m idle: cache KV gone (next turn is a write); memory files still there.

**Q21. Name three silent cache misses.**
(1) Haiku 4.5 with a 2k prefix (4,096 min, marker ignored). (2) Lookback: the conversational breakpoint is >=20 content blocks past the last write, so every turn writes 1.25x. (3) Memory rewritten every turn so the suffix never reads. Bonus: Fireworks without `thread_id` affinity; N parallel cold prefixes = N writes because the entry exists only after the first response begins.

---

## System Design Scenarios

### Scenario 1: Long-Running Research Agent (100+ Turns, Multi-Session)

**Problem**: Design a research agent spanning 100+ turns across multiple sessions. Must retain findings, handle context exhaustion gracefully, and provide cost-predictable operation.

```
+------------------------------------------------------------------+
|                    RESEARCH ORCHESTRATOR                          |
|                                                                  |
|  System Prompt (cached):                                         |
|  - Base instructions + research protocol                         |
|  - AGENTS.md (compact user prefs + project context)              |
|  - Skill metadata (web-search, code-analysis, summarizer)        |
|                                                                  |
|  Compression Strategy:                                           |
|  - Agent-level: summarize at 50% (proactive)                     |
|  - Gateway safety net: summarize at 85% (defensive)              |
|  - Structured notes written to /memories/ (cross-session)        |
|                                                                  |
|  +-------------+  +-------------+  +-------------+              |
|  | Subagent:    |  | Subagent:    |  | Subagent:    |             |
|  | Topic A      |  | Topic B      |  | Topic C      |             |
|  | (fresh ctx)  |  | (fresh ctx)  |  | (fresh ctx)  |             |
|  | Returns 2K   |  | Returns 2K   |  | Returns 2K   |             |
|  +-------------+  +-------------+  +-------------+              |
|                                                                  |
|  Cross-Session Memory:                                           |
|  CompositeBackend -> StoreBackend(/memories/) -> PostgresStore   |
+------------------------------------------------------------------+
```

**Trade-off Matrix:**

| Decision | Option A | Option B | Choice | Rationale |
|----------|----------|----------|--------|-----------|
| Compression trigger | 85% (default) | 50% agent + 85% gateway | **Two-layer (B)** | Prevents compressor trap; proactive at 50% keeps context cleaner |
| Memory storage | AGENTS.md only | AGENTS.md + StoreBackend | **Composite (B)** | AGENTS.md for compact prefs; StoreBackend for growing research notes |
| Research subtopics | Single agent | Subagent per topic | **Subagents (B)** | Fresh context per topic prevents cross-topic interference; 10:1 compression |
| Model for subagents | Same as parent | Cheaper model (Haiku) | **Haiku for search, Sonnet for synthesis** | 80% of subagent work is summarizing search results |

### Scenario 2: Coding Agent: Skills + Memory + OpenWiki

**Problem**: Repo coding agent with PRs, tests, architecture questions. HITL for humans. Growing internal wiki. Security: no untrusted community skills with `execute`, per-user preferences, org procedures read-only.

**Proposed architecture (recommended):**

```
  +---------+   +-------------------------------------------------------------+
  | IdP/PEP |-->| CONTROL: create_deep_agent                                  |
  | JWT ->  |   |   memory=["/workspace/AGENTS.md"]  (pointer + style)        |
  | user_id |   |   skills=["/workspace/.agents/skills/", "/skills/org/"]     |
  |         |   |     deny-write /skills/org/**  and generated openwiki/      |
  |         |   |   AnthropicPromptCachingMiddleware(ttl="1h")                |
  |         |   |   summarization ON; offload 20k; Patch before cache         |
  |         |   |   PII detect->redact->audit before memory/skill persist     |
  +---------+   +------------------------------+------------------------------+
                                               v
                    +------------------------------------------------------+
                    | DATA: 2k harness + 1k AGENTS.md + 1.5k skill fronts  |
                    |   = 4.5k cached prefix                               |
                    |   OpenWiki pages via read_file after pointer          |
                    |   /memories/ -> StoreBackend ns=(assistant, user)     |
                    |   CI: openwiki code --update (empty = no model call)  |
                    +------------------------------------------------------+
```

**Cost [inferred]:** 4.5k prefix: 1h write $0.027 + 9 reads $0.012 = **$0.039** / 10-call vs **$0.135** uncached prefix.

### Scenario 3: Multi-Tenant SaaS Agent Platform with Skill Marketplace

**Problem**: Agent platform serving 500 enterprise tenants, each with custom skills and memory. Tenants must be isolated, skills auditable, prompt variations A/B-testable. Expected: 10,000 concurrent sessions.

**Key decisions:**
- **Namespace isolation via CompositeBackend**: Each tenant scoped by org_id and user_id. Prevents cross-tenant access without separate infrastructure.
- **Skill sandboxing non-negotiable**: 36% prompt injection rate in public skills.
- **Shared prompt caching**: Base prompt (tool definitions, harness defaults) identical across tenants; one cache write serves all. Per-tenant customization appended after cache boundary.
- **RAG for history**: Enterprise tenants accumulate interaction history; AGENTS.md does not scale for recall.

### Scenario 4: Support Bot: Thin Memory + Aggressive Offload

**Problem**: Ticket bot. Policy manual ~40k tokens. CRM tools dump 50k-token pages. Per-tenant macros. Security: do not put customer ticket text into a shared org cache prefix.

**Recommended (offload + thin memory):**

| Design | Prefix | Cache | $ / 1k tickets |
| --- | --- | --- | --- |
| Offload, 3k prefix, 2k dynamic | 3k | 5m | **~$170** |
| Stuffed 40k policy, cache hits | 40k | 5m | **~$300** |
| Stuffed 40k, no cache | 40k | -- | **~$1,020** |

**Skills vs RAG vs stuff (same bot):** If policy is <~200k and identical for all tenants: stuff + cache. If huge or tenant-sliced: RAG retrieval into **messages** (not `memory=`). If policy is a procedure: a skill with `references/policy.md` so L1 is small and L3 loads one file. Mixing all three in `AGENTS.md` is the failure mode.

---

## Key Numbers to Memorize

### Tokens / Compression / Skills
| Number | What |
| --- | --- |
| **~6k -> ~2k / -65%** | v0.7 default harness prefix |
| **~100 tok / skill** | L1 metadata |
| **<5,000 tok / <500 lines** | Recommended L2 SKILL.md body |
| **1,024 chars** | Spec description max |
| **10 MB** | SKILL.md DoS cap (skipped) |
| **4 chars/token** | Offload tokenizer |
| **20,000 / 50,000** | Tool-result / human-message eviction thresholds |
| **0.85 / 0.10** | Summarize trigger / keep (profile) |
| **170,000 / 6** | No-profile trigger / keep messages |
| **20 content blocks** | Anthropic cache lookback from breakpoint |
| **25% -> 95%** | LangChain skills eval on Claude Code |

### Cache / Price / Minima
| Number | What |
| --- | --- |
| **1.25x / 2x / 0.1x** | Anthropic 5m write / 1h write / read |
| **5m / 1h** | Default TTL / HITL override |
| **1,024 / 4,096** | Sonnet 4.6 min / Haiku 4.5 & Opus 4.6 min |
| **$3 / $15** | Sonnet 4.6 input / output per MTok |
| **$1 / $5** | Haiku 4.5 input / output |

### Latency (numeric ms)
| Number | What |
| --- | --- |
| **11,500 -> 2,400 ms** | Anthropic 100k book TTFT miss -> hit (published) |
| **1,000 / 2,000 / 3,500 ms** | [inferred] 2k prefix cache HIT p50/p95/p99 |
| **1,600 / 5,000 / 12,000 ms** | [inferred] 2k prefix cache MISS |
| **2,000 / 8,000 / 20,000 ms** | [inferred] ReAct + local read / skill L2 |
| **2,000 / 6,000 / 15,000 ms** | [inferred] summarizer extra hop |
| **30,000 / 180,000 / 600,000 ms** | [inferred] HITL memory-write clock |

**Dates:** research frozen **2026-09-02**. Do not treat inferred $ or ms as list prices or vendor SLOs.
