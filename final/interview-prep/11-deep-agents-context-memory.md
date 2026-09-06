# Module 11: Deep Agents -- Context Management, Memory & Skills

## What Is This?

Context engineering is the single biggest lever for agent quality: most agent failures are context failures before they are reasoning failures. Deep Agents treats context as a systems problem with five discrete layers -- input context (what the model sees in the prompt), runtime context (hidden configuration tools and middleware read), context compression (summarization and offloading to manage window pressure), context isolation (subagents carrying heavy work in a separate window), and long-term memory (durable state that survives across threads). Skills add a sixth dimension: progressive-disclosure procedural knowledge that loads only its metadata at startup and reveals full instructions on demand, keeping prompt budgets tight until the agent actually needs a capability. Prompt caching sits underneath all of this, automatically reducing cost and latency for the stable prefix that Deep Agents re-sends every turn.

---

## 1. System Topology & Data Flow

### 1.1 Prompt Assembly Pipeline

Every model call in Deep Agents assembles the prompt from multiple sources in a fixed order. Understanding this order is critical because it determines what the model sees, what stays hidden, what gets cached, and where prompt-injection vectors live.

```
  PROMPT ASSEMBLY ORDER (what the model sees)
  ┌──────────────────────────────────────────────────────────────────────┐
  │  1. Custom system_prompt= (static)                                  │
  │     OR @dynamic_prompt (runtime-dependent instructions)             │
  │  2. Built-in Deep Agents base instructions                          │
  │  3. Memory files (AGENTS.md and other memory= paths)                │
  │  4. OpenWiki pointers (agent reads via read_file, not auto-injected)│
  │  5. Skill metadata (name + description only, ~100 tok/skill)        │
  │  6. Tool descriptions and schemas (every turn, even unused tools)   │
  │  7. Subagent/task guidance                                          │
  │  8. User middleware prompt additions                                │
  │  9. HITL prompt when configured                                     │
  │ 10. Conversation history (messages + tool results)                  │
  └──────────────────────────────────────────────────────────────────────┘

  RUNTIME STATE (hidden from model unless explicitly injected)
  ┌──────────────────────────────────────────────────────────────────────┐
  │  context_schema-defined invoke-time data (user_id, org_id, flags)   │
  │  ToolRuntime access inside tools and middleware                     │
  │  rt.server_info / rt.execution_info (>=0.5.0)                      │
  └──────────────────────────────────────────────────────────────────────┘

  WINDOW MANAGEMENT (automatic + manual)
  ┌──────────────────────────────────────────────────────────────────────┐
  │  Offload: large tool I/O to VFS at 20k token threshold             │
  │  Summarize: old history at 85% of max_input_tokens                 │
  │  Delegate: heavy work to subagents (context isolation)             │
  │  compact_conversation: model tool to manually trigger summary      │
  │  Reload: only what is needed from VFS on demand                    │
  └──────────────────────────────────────────────────────────────────────┘
```

### 1.2 Middleware Stack and Context Flow

The middleware wrap order determines how context is assembled and cached. Getting this wrong breaks prompt caching or corrupts memory.

```
  MIDDLEWARE WRAP ORDER (inside-out: first listed = closest to model)
  ┌──────────────────────────────────────────────────────────────────────┐
  │  Slot  1: SkillsMiddleware          (skill metadata injection)      │
  │  Slot  2: PatchToolCallsMiddleware  (repair dangling tool_calls)    │
  │  Slot  3: ProfileMiddleware         (system_prompt + suffix)        │
  │  Slot  4: User middleware / profile extras                          │
  │  Slot  5: AnthropicPromptCachingMiddleware (cache markers)          │
  │           BedrockPromptCachingMiddleware                             │
  │  ...                                                                │
  │  Slot 13: ContextSummarizerMiddleware (compression triggers)        │
  │  Slot 14: HumanInTheLoopMiddleware   (HITL gates)                   │
  │  Slot 15: MemoryMiddleware           (MUST be AFTER cache)          │
  └──────────────────────────────────────────────────────────────────────┘
```

**Why MemoryMiddleware goes AFTER cache middleware (GitHub issue #1356):** If memory is injected before the cache middleware marks the prefix, any memory update invalidates the entire cache. By placing memory after caching, the stable prefix (system prompt + tools + skills metadata) stays cached even when memory changes between turns.

### 1.3 Five-Layer Context Model

| Layer | What It Contains | Prompt-Visible? | Persistence |
|-------|-----------------|-----------------|-------------|
| **1. Input Context** | system_prompt, memory files, skill metadata, tool schemas, conversation history | Yes | Per-turn assembly |
| **2. Runtime Context** | context_schema values (user_id, API keys, feature flags) | **No** -- must be explicitly injected by tools/middleware | Per-run immutable |
| **3. Context Compression** | Summarized history, offloaded tool I/O | Indirectly (summary replaces originals) | Checkpointed |
| **4. Context Isolation** | Subagent work (heavy tool results stay in child window) | Only the compact result returned to parent | Child's checkpoint |
| **5. Long-Term Memory** | AGENTS.md, user preferences, org policies | Yes (always loaded) | Filesystem/Store across threads |

**Real-world example:** A customer support agent has a 200k token context window. The system prompt + tools consume 5k tokens. Memory (AGENTS.md with company policies) takes 3k. Skill metadata for 20 skills takes 2k (100 tok/skill). That leaves ~190k for conversation. After 50 back-and-forth messages with tool calls, the conversation history might reach 170k tokens (85% of 200k). The summarizer fires, compresses old messages down to ~17k (10% of 170k), freeing 153k tokens for new work. If a tool returns a 30k-token database dump, the offloader kicks in at the 20k threshold and writes it to VFS instead, replacing the inline result with a file path.

---

## 2. Core Mechanics & Algorithms

### 2.1 `context_schema` vs `state_schema`

These are commonly confused in interviews. They serve fundamentally different purposes:

| Aspect | `context_schema` | `state_schema` |
|--------|-----------------|----------------|
| **Purpose** | Per-run immutable configuration | Mutable graph state |
| **Mutability** | Frozen after invoke | Updated during execution |
| **Checkpointed** | No | Yes |
| **Prompt visibility** | Hidden unless explicitly injected | Hidden unless explicitly injected |
| **Typical contents** | user_id, org_id, API keys, feature flags | Tool results, intermediate reasoning |
| **Subagent access** | Propagated via runtime | Isolated per subagent |

```python
from dataclasses import dataclass

# context_schema: immutable per-run configuration
@dataclass
class AgentContext:
    user_id: str
    org_id: str
    environment: str  # "staging" | "production"
    feature_flags: dict  # e.g., {"beta_search": True}

# state_schema: mutable checkpointed state
@dataclass
class AgentState:
    messages: list  # conversation history
    todo_items: list  # task plan
    retrieved_docs: list  # search results
```

**Key rule:** Runtime context is NOT automatically included in the prompt. The model only sees it if a tool, middleware, or `@dynamic_prompt` reads it and injects it.

### 2.2 `@dynamic_prompt` -- Runtime-Dependent Instructions

Use `@dynamic_prompt` when system instructions depend on runtime context such as user role, access level, feature flags, or stored preferences. This replaces the static `system_prompt=` parameter.

```python
@dynamic_prompt
def build_prompt(runtime: ToolRuntime) -> str:
    user = runtime.server_info.user
    role = user.metadata.get("role", "viewer")
    base = "You are an enterprise assistant."
    if role == "admin":
        base += " You may modify system settings."
    else:
        base += " You may only read data. Do not attempt writes."
    return base
```

### 2.3 Skills -- Progressive-Disclosure Procedural Memory

Skills solve the "important but not always relevant" problem. They are reusable workflows and domain knowledge that the agent discovers on demand rather than carrying in every prompt.

**Three progressive-disclosure levels:**

| Level | What Loads | Token Cost | When |
|-------|-----------|------------|------|
| **1. Metadata** | name + description from SKILL.md frontmatter | ~100 tokens/skill | Always at startup |
| **2. Instructions** | Full SKILL.md body | <5,000 tokens / <500 lines | When agent activates via read_file |
| **3. Resources** | scripts/, references/, assets/ | Variable | On demand after activation |

**SKILL.md structure:**

```markdown
---
name: deploy-to-staging
description: "Step-by-step procedure for deploying a service to the staging environment"
license: MIT
compatibility: "kubernetes >= 1.28"
allowed-tools: "execute read_file write_file"
---

# Deploy to Staging

1. Verify the Dockerfile builds cleanly...
2. Run integration tests...
```

**Frontmatter constraints:**
- `name`: lowercase alphanumeric + hyphens, 1-64 chars, must match parent directory name
- `description`: max 1,024 chars
- Body: <5,000 tokens, <500 lines
- Files >10 MB are skipped entirely

**Subagent skill inheritance (asymmetric):**
- Auto-added general-purpose (GP) subagent: **inherits** parent skills
- Custom/declarative subagents: do **NOT** inherit -- need their own `skills=`
- Skill state is isolated between parent and child

**Agent Skills Specification (agentskills.io):** Public registry with ~1.9M skills. However, **36% prompt injection rate** in public skills -- treat any third-party skill as untrusted input. Validate, sandbox, and scope permissions.

### 2.4 Memory -- Filesystem-Backed Persistence

Memory is durable state that survives across threads. Deep Agents treats memory as files on a backend, not as vague "the model remembers."

**Canonical format:** `AGENTS.md` -- always loaded into the prompt when configured via `memory=["/memories/AGENTS.md"]`.

**Two memory modes:**

| Mode | Mechanism | Latency | Staleness |
|------|-----------|---------|-----------|
| **Semantic (hot-path)** | Agent calls `edit_file` during conversation to update AGENTS.md | Higher per-turn (agent spends tokens on memory maintenance) | None -- immediate |
| **Semantic (background)** | Separate consolidation agent merges recent threads into memory | None during conversation | Present -- new facts available only after next consolidation |
| **Episodic** | Thread checkpoints preserve conversation history | None (automatic) | N/A -- search over past threads requires wrapping thread-history APIs in a tool |

**Scoping patterns (namespace functions):**

| Scope | Namespace | Use Case | Risk |
|-------|-----------|----------|------|
| **Agent** | `(assistant_id,)` | Shared behavior/knowledge across users | Prompt-injection channel -- any user can write |
| **User** | `(user.identity,)` | Private preferences, context | Only that user can poison |
| **Agent+User** | `(assistant_id, user.identity)` | Per-user per-agent preferences | Recommended default for multi-tenant |
| **Organization** | `(org_id,)` | Company policies, knowledge base | **Must be read-only** via `permissions` deny-write |

```python
# Namespace factory requires deepagents >= 0.5.0
namespace = lambda rt: (rt.server_info.assistant_id, rt.server_info.user.identity)
```

**Concurrent write hazard:** Parallel writes to the same memory file degrade into **last-write-wins**. Mitigation: partition memory by user namespace, or serialize updates through a background consolidation agent.

**Background consolidation scheduling rule (from docs):** `cron interval ~= lookback window`. If you consolidate every 6 hours, look back 6 hours.

### 2.5 Summarization & Context Offloading

Deep Agents uses a two-stage strategy to manage context window pressure:

**Stage 1 -- Offload (20k token threshold):** When a single tool result exceeds ~20,000 tokens, the harness writes it to the virtual filesystem (VFS) and replaces the inline content with a file path reference. The agent can later `read_file` to retrieve specific portions.

**Stage 2 -- Summarize (85% of max_input_tokens):** When total context reaches 85% of the model's maximum input tokens, `ContextSummarizerMiddleware` compresses older conversation history down to approximately 10% of the original content. The summary replaces the original messages in the conversation.

```
  Context Window Lifecycle:
  
  0%    ───────────────── conversation grows ──────────────────── 85%
                                                                   │
                          SUMMARIZE TRIGGER ◄───────────────────────┘
                          │
                          ▼
                     Keep ~10% (summary of older messages)
                     + keep recent messages intact
                     ────────────────────────────────── ~25%
                     
                     Conversation continues growing...
```

**Fallback (if summarization model fails):** Truncate to last 170k tokens or 6 messages, whichever is smaller. This is a safety net, not the normal path.

**`compact_conversation` tool:** A model-callable tool that manually triggers summarization. Useful when the agent knows it has accumulated a lot of intermediate reasoning that is no longer needed.

**ContextOverflowError:** If the prompt still exceeds the model's context after summarization, the harness raises this error. The retry path re-summarizes with more aggressive compression.

**Multimodal limitation:** Deep Agents does not compress images, audio, or video. These tokens are counted but cannot be summarized. Claude visual token formula: `tokens = (width * height) / 750`, minimum 1 token per tile.

### 2.6 Prompt Caching

Deep Agents automatically wires prompt caching for supported providers. No extra configuration is required for the default case.

**How it works:**
1. `create_deep_agent` auto-registers both `AnthropicPromptCachingMiddleware` and `BedrockPromptCachingMiddleware`
2. Each middleware no-ops on unsupported models with `unsupported_model_behavior="ignore"`
3. The stable prefix (system prompt + tools + skills metadata + memory) is marked for caching
4. On subsequent turns, if the prefix hasn't changed, the provider returns a cache hit

**Anthropic cache economics:**

| Operation | Price Multiplier | Absolute (Sonnet 4.6) |
|-----------|-----------------|----------------------|
| Cache write (first turn) | 1.25x base input | $3.75 / MTok |
| Cache read (subsequent turns) | 0.1x base input | $0.30 / MTok |
| Default TTL | 5 minutes | -- |
| Extended TTL (configurable) | Up to 1 hour | -- |

```python
# Override the default 5m TTL to 1h for slow HITL workflows
from langchain_deepagents import AnthropicPromptCachingMiddleware

agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",
    middleware=[
        AnthropicPromptCachingMiddleware(ttl="1h"),
    ],
)
```

**What invalidates the cache:**
- Prompt text changes (system prompt edits, `@dynamic_prompt` output changes)
- Tool schema changes (adding/removing tools)
- Skill changes (loading a new skill body)
- Memory changes (if memory is placed before cache middleware -- see ordering rule)
- Profile suffix changes

### 2.7 OpenWiki

OpenWiki is a **separate CLI tool** (not middleware) that writes and maintains a Markdown wiki. Install via `npm install -g openwiki` then `openwiki --init`.

**Key properties:**
- Produces `openwiki/` (code mode) or `~/.openwiki/wiki` (personal mode)
- Does **NOT** inject pages into the system prompt (unlike memory)
- Progressive disclosure: agent discovers wiki via pointers in AGENTS.md, then `read_file`
- Claims-driven updates: grounded claims stored under `openwiki/.claims/`
- Emits OKF v0.2 format
- Visualizer binds **127.0.0.1** only
- Git-resident -- treat as world-readable to anyone with repo access
- CI integration: `openwiki --update` for freshness

**What OpenWiki is NOT:** It is not a second system prompt. It is not a substitute for skills/memory/offload. It is not a RAG index (wiki is architecture narrative, not a corpus).

---

## 3. Token Economics & NFR Analysis

### 3.1 Context Budget Breakdown

For a typical 200k-token context window (Claude Sonnet 4.6):

| Component | Tokens | % of Window |
|-----------|--------|-------------|
| System prompt (custom) | 200-1,000 | 0.1-0.5% |
| Deep Agents base instructions | ~800 | 0.4% |
| Memory (AGENTS.md) | 1,000-5,000 | 0.5-2.5% |
| Tool schemas (10 built-in + 5 custom) | 2,000-4,000 | 1-2% |
| Skill metadata (20 skills @ ~100 tok) | ~2,000 | 1% |
| **Stable prefix (cacheable)** | **~6,000-12,000** | **3-6%** |
| Conversation history | Variable | Up to 85% |
| Active skill body (when loaded) | <5,000 | <2.5% |

**Prompt-size rule:** Unused built-in tools still send schemas unless removed with `excluded_tools`. Each tool schema costs ~100-200 tokens per turn.

### 3.2 Cost Impact of Prompt Caching

Using the standard 10-call research run with Sonnet 4.6 ($3/$15 per MTok):

| Scenario | Per Run | Per 1k Runs | Savings |
|----------|---------|-------------|---------|
| **Cached** (1 write + 9 reads of 2k prefix + 30k uncached + 8k out) | $0.2229 | **$223** | Baseline |
| **Uncached** (10 x 5k in + 8k out) | $0.2700 | **$270** | -- |
| **Savings from caching** | $0.0471 | **$47** | ~17% |

Cache savings scale with prefix size. A 5k prefix saves proportionally more than a 2k prefix.

### 3.3 Summarization Cost

Summarization adds a model call when triggered. The summarizer call processes the full conversation being compressed:

| Event | Extra Cost | Frequency |
|-------|-----------|-----------|
| Summarization trigger (at 85%) | ~$0.01-0.05 (varies with conversation length) | Every ~50-100 turns for a typical chat |
| Offload to VFS (at 20k) | Negligible (filesystem write) | Per large tool result |
| compact_conversation (manual) | Same as summarization trigger | On demand |

### 3.4 Latency SLA Targets

| Path | p50 | p95 | p99 | Notes |
|------|-----|-----|-----|-------|
| **Cache-hit first token** | **640 ms** | **2,560 ms** | **5,120 ms** | [inferred] Anthropic 5m cache warm |
| **Cache-miss first token** | **1,200 ms** | **4,800 ms** | **9,600 ms** | [inferred] Full prefix re-processed |
| **Offload write (VFS)** | **1 ms** | **5 ms** | **20 ms** | [inferred] Local filesystem |
| **Summarization call** | **2,000 ms** | **8,000 ms** | **20,000 ms** | [inferred] One model call on history |
| **Skill body load (read_file)** | **1 ms** | **5 ms** | **20 ms** | [inferred] VFS read |
| **Memory hot-path write** | **10 ms** | **50 ms** | **200 ms** | [inferred] Store backend write |

All latency figures are [inferred policy targets], not vendor SLOs.

### 3.5 Availability & Recovery Targets

| Metric | Target | Rationale |
|--------|--------|-----------|
| **Availability** | 99.9% (3-nines) | Standard for internal enterprise tooling |
| **RPO (checkpoints)** | Last durable super-step | `sync` vs `async` vs `exit` durability modes |
| **RTO (checkpoints)** | <5 minutes | Resume from last checkpoint on restart |
| **RPO (memory/store)** | Last Store put | Namespace scoping prevents cross-user corruption |
| **RPO (traces)** | 14 days (base) / 400 days (extended) | LangSmith retention tiers |

---

## 4. Distributed Resilience & Security

### 4.1 Memory Security Architecture

| Threat | Attack Vector | Mitigation |
|--------|--------------|------------|
| **Cross-user memory poisoning** | User A writes malicious instructions to agent-scoped memory, User B reads them | User-scoped namespace `(assistant_id, user.identity)` |
| **Org memory injection** | Any user writes to org-scoped policies | Org namespace **read-only** via `permissions` deny-write on `/memories/org/` |
| **Skill poisoning** | Shared writable skill directory | Read-only permissions on shared skills; `mode="interrupt"` for changes |
| **Public skill injection** | 36% of agentskills.io skills contain prompt injection | Validate, sandbox, never auto-load from public registry without review |
| **Memory via tool results** | Tool returns instruction-like content that persists to memory | Separate memory-write candidates from tool results; PII pipeline |
| **Last-write-wins corruption** | Two concurrent agents update the same AGENTS.md | Partition by user namespace; serialize via background consolidation |

### 4.2 Context Compression Security

**Summarization is lossy.** The 10% retention ratio means 90% of conversation detail is discarded. This creates:
- **Information loss risk:** Critical instructions from early in the conversation may be lost
- **Replay non-determinism:** Resuming from a post-summarization checkpoint produces different behavior than the original
- **Audit gap:** The pre-summarization content is gone from the conversation (though traces may retain it)

**Mitigation:** Critical instructions belong in the system prompt or memory (always loaded), not in conversation history where they can be summarized away.

### 4.3 PII Pipeline for Context

Detect, redact, and audit PII before it enters any persistence layer:

| Sink | PII Risk | Control |
|------|----------|---------|
| **Memory files** | User data persisted across threads | detect -> redact -> audit before memory write |
| **Skill bodies** | Instructions may reference real data | Scan on skill creation/update |
| **VFS (offloaded content)** | Large tool results with customer data | PIIMiddleware before model, but does NOT scan VFS files the model never re-reads |
| **Checkpoints** | Full conversation state | Encrypted if `LANGGRAPH_AES_KEY` present |
| **Traces** | Prompts and tool results | `LANGSMITH_HIDE_INPUTS` / `LANGSMITH_HIDE_OUTPUTS` |
| **Cache** | Stable prefix cached at provider | Provider-managed; cannot redact from provider cache |

### 4.4 GDPR and EU AI Act

- **GDPR erasure:** Requires purging memory + checkpoints + traces + VFS for a given user. `thread_id` TTL alone is not sufficient.
- **EU AI Act Article 14 (effective August 2, 2026):** Mandates human ability to intervene, stop, or override high-risk AI. Context management must support this by maintaining interpretable state.

---

## 5. Common Failure Modes

| Failure | Cause | Detection | Mitigation |
|---------|-------|-----------|------------|
| Prompt bloat from unused tools | Tool schemas sent every turn even when unused | Token count grows without corresponding tool use | `excluded_tools` to remove tools the agent should never call |
| Summarizer destroys critical instructions | Important early instructions land in conversation history instead of system prompt | Agent "forgets" instructions after summarization | Put critical instructions in system_prompt or memory, not conversation |
| Memory placed before cache middleware | Memory updates invalidate the entire cached prefix | Cache hit rate drops to near zero; costs increase ~17% | Memory middleware AFTER cache middleware (issue #1356) |
| context_schema treated as state_schema | Immutable config becomes mutable state, or durable state becomes invisible runtime | Config changes mid-run or state disappears between runs | context_schema = immutable per-run; state_schema = mutable checkpointed |
| Shared writable memory (cross-tenant injection) | One user writes memory that another user reads | Unexpected behavior changes across users | User-scoped namespace; org memory read-only |
| Skill directory path misconfiguration | Pointing `skills=` directly at a skill directory instead of its parent | Skills silently fail to load | `skills=` must point to the parent of skill directories |
| Supporting files never used | SKILL.md never references scripts/references/assets | Agent doesn't know supporting files exist | SKILL.md must explicitly describe when to open supporting files |
| Custom subagents "lose" skills | Assumption that all subagents inherit parent skills | Subagent lacks expected capabilities | Only GP subagent inherits; custom subagents need their own `skills=` |
| Offload threshold too low/high | Custom offload threshold doesn't match workload | Either too many VFS writes (overhead) or context overflow | Default 20k threshold works for most workloads; tune based on typical tool output sizes |
| Cache miss after HITL wait >5m | Human review takes longer than default 5m Anthropic cache TTL | Cache write cost ($3.75/MTok) instead of read ($0.30/MTok) on resume | `AnthropicPromptCachingMiddleware(ttl="1h")` for HITL-heavy workflows |
| OpenWiki stuffed into system prompt | Treating OpenWiki as always-loaded context instead of on-demand | Prompt bloat; stale wiki content in every turn | OpenWiki is progressive-disclosure via read_file, not auto-injected |
| Multimodal content not compressible | Images/video counted but cannot be summarized | Context overflow with visual content | Budget visual tokens separately; use image URLs instead of inline when possible |
| ContextOverflowError after summarization | Context still exceeds model max even after compression | Hard error on model call | Re-summarize with more aggressive compression; split into subagents |

---

## 6. Architectural System Design Scenarios

### Scenario A -- Multi-Tenant Research Agent with Per-User Memory

**Problem.** A SaaS platform serves 500 analysts who each have personal research preferences, citation styles, and domain expertise stored across sessions. The system must prevent cross-user memory contamination, handle 50-page research reports (large tool outputs), and keep per-run costs under $0.30.

**Architecture (recommended):**

```
  ┌─────────────┐   ┌──────────────────────────────────────────────────┐
  │ Auth / IdP  │──▶│ CONTROL: create_deep_agent                       │
  │ JWT → user  │   │   context_schema: user_id, org_id, preferences  │
  │ identity    │   │   @dynamic_prompt reads context → role-based     │
  │             │   │   memory: /memories/AGENTS.md                     │
  │             │   │     namespace: (assistant_id, user.identity)      │
  │             │   │   skills: /skills/ (20 research skills)          │
  │             │   │   org policies: /memories/org/ (deny-write)      │
  │             │   │   cache TTL 5m (default, research sessions <5m)  │
  │             │   │   excluded_tools: unused built-ins               │
  └─────────────┘   └──────────────────────────┬───────────────────────┘
                                               ▼
                    ┌──────────────────────────────────────────────────┐
                    │ DATA: model proposes / tools dispose              │
                    │   Large reports (>20k tok) → VFS offload          │
                    │   Summarize at 85% → keep 10%                    │
                    │   Subagent for each major section (isolation)     │
                    │   Background consolidation every 6h               │
                    │   PII detect→redact→audit on memory writes       │
                    └──────────────────────────────────────────────────┘
```

**Trade-off matrix:**

| Axis | User-scoped memory + offload (recommended) | Single shared memory | No memory (stateless) |
|------|---------------------------------------------|---------------------|----------------------|
| **Cost** | ~$0.22/run (cached); +$0.01 per summarization; negligible memory I/O | Same model cost; risk of poisoned shared context | Same model cost; user re-explains preferences every session |
| **Latency** | Cache hit: 640ms TTFT; background consolidation: no user-facing impact | Same | Same; no memory load latency |
| **Security** | Per-user isolation; org read-only; PII pipeline | Cross-user injection risk; compliance failure | No persistence risk; but poor UX |
| **Scalability** | Linear in users (one namespace per user); Store backend handles thousands | One file with race conditions | Stateless = easy to scale |

### Scenario B -- Multi-Tenant SaaS with Skills and Context Engineering

**Problem.** A customer support platform must handle billing, technical, and compliance queries. Each domain has specialized procedures (skills) that should load only when relevant. The system serves 100k daily queries across 50 enterprise tenants with strict data isolation requirements.

**Architecture (recommended):**

```
  ┌──────────────────────────────────────────────────────────────────┐
  │ CONTROL: Orchestrator Deep Agent                                 │
  │   skills: /skills/billing/, /skills/technical/, /skills/compliance│
  │   memory: /memories/AGENTS.md (tenant-scoped)                    │
  │   context_schema: tenant_id, user_id, plan_tier                 │
  │   @dynamic_prompt: tier-based instructions                       │
  │   excluded_tools: remove unused built-ins per tenant             │
  └──────────────────────────────────┬───────────────────────────────┘
                                     │
        ┌────────────────────────────┼─────────────────────┐
        ▼                            ▼                     ▼
  ┌────────────┐           ┌────────────────┐     ┌──────────────┐
  │ Billing    │           │ Technical      │     │ Compliance   │
  │ Subagent   │           │ Subagent       │     │ Subagent     │
  │ own skills │           │ own skills     │     │ own skills   │
  │ context    │           │ context        │     │ mandatory    │
  │ isolation  │           │ isolation      │     │ HITL         │
  └────────────┘           └────────────────┘     └──────────────┘
```

**Key design decisions:**
1. **Skills over memory for procedures:** Domain-specific procedures live in skills (progressive disclosure) rather than memory (always loaded). A billing query loads only billing skills, saving ~4k tokens per turn on technical/compliance skill bodies.
2. **Subagents for isolation:** Each domain runs in a child window. A billing subagent's 30k-token CRM dump stays in the child; the parent sees only a 200-token summary.
3. **Tenant-scoped memory:** `namespace=(tenant_id, assistant_id, user.identity)` prevents cross-tenant data leakage.
4. **Cache optimization:** Static prefix (system prompt + skill metadata + tools) is ~8k tokens. At 100k daily queries, caching saves ~$470/day (vs uncached).

---

## Interview Q&A

**Q1. What are the five context layers in Deep Agents, and why does the split matter?**
I organize context into input (prompt-visible material like system prompt, memory, skills, tools), runtime (hidden per-run config via context_schema that tools and middleware read but the model never sees), compression (summarization at 85% capacity and offloading at 20k tokens), isolation (subagents carrying heavy work in separate windows, returning only compact results), and long-term memory (durable files like AGENTS.md that persist across threads). The split matters because confusing them -- for example, treating runtime context as prompt content or putting critical instructions in conversation history instead of system prompt -- causes either token waste, security leaks, or information loss when the summarizer fires.

**Q2. What is the difference between `context_schema` and `state_schema`?**
`context_schema` defines immutable per-run configuration -- things like user_id, API keys, feature flags that stay constant for the entire run. It is not checkpointed and not automatically visible to the model. `state_schema` defines mutable graph state that gets updated during execution and checkpointed at each super-step. The classic design bug is mixing them up: putting durable state into context_schema (where it disappears between runs) or putting one-time config into state_schema (where it gets checkpointed unnecessarily and can be mutated).

**Q3. How does prompt caching work in Deep Agents, and what breaks it?**
Deep Agents auto-registers Anthropic and Bedrock caching middleware. The stable prefix -- system prompt, tool schemas, skill metadata -- gets cached for 5 minutes by default (configurable up to 1 hour). Subsequent turns that match the same prefix get 0.1x input pricing instead of full price. What breaks it: any change to the prompt prefix invalidates the cache. This includes prompt edits, tool schema changes, skill body changes, and -- critically -- memory updates if MemoryMiddleware is placed before the cache middleware. That is why issue #1356 established that memory goes AFTER cache in the middleware stack.

**Q4. How do skills differ from memory, and when do you use each?**
Memory is always loaded into the prompt because it is assumed to be always relevant -- things like AGENTS.md with company policies and user preferences. Skills use progressive disclosure: only metadata (name + description, ~100 tokens) loads at startup; the full body (<5,000 tokens) loads only when the agent activates the skill via read_file. I use memory for things the agent needs every turn (identity, policies, preferences). I use skills for domain procedures that are only relevant for specific query types. Overusing always-loaded memory for task-specific procedures bloats every prompt unnecessarily.

**Q5. Walk me through what happens when context hits 85% capacity.**
At 85% of max_input_tokens, ContextSummarizerMiddleware fires. It takes the older portion of conversation history and sends it to the model for summarization, targeting approximately 10% retention of the original content. The summary replaces the original messages. If the summarizer model fails, the fallback truncates to the last 170k tokens or 6 messages, whichever is smaller. If context still exceeds capacity after summarization, a ContextOverflowError is raised and the retry path re-summarizes with more aggressive compression. Before that threshold, individual tool results exceeding 20k tokens are offloaded to VFS at the time they are produced, replaced with file path references.

**Q6. How do you handle memory in a multi-tenant environment?**
I scope memory with namespace functions: `(assistant_id, user.identity)` for per-user-per-agent memory (recommended default), `(user.identity,)` for cross-agent user preferences, `(org_id,)` for organizational policies. The critical rule is that organization-scoped memory must be read-only via permissions deny-write -- otherwise any user of that assistant becomes a prompt-injection channel for all other users. Concurrent writes to the same memory file create last-write-wins races, so I partition by user namespace and use background consolidation (cron interval equals lookback window) rather than hot-path writes when multiple agents might contend on the same file.

**Q7. What is the prompt caching cost math for a typical run?**
For a 10-call run on Sonnet 4.6 with a 2k-token cached prefix: one cache write at $3.75/MTok (2k x $3.75/1M = $0.0075), nine cache reads at $0.30/MTok (9 x 2k x $0.30/1M = $0.0054), uncached dynamic input at $3/MTok (10 x 3k x $3/1M = $0.09), output at $15/MTok (10 x 800 x $15/1M = $0.12). Total: $0.2229/run or $223/1k runs. Without caching the same run costs $0.27/run or $270/1k. That is a ~$47/1k savings at a 2k prefix. The savings scale with prefix size.

**Q8. What is OpenWiki, and how does it interact with the agent context?**
OpenWiki is a separate CLI (npm, not pip) that writes and maintains a Markdown wiki on the filesystem. It does NOT inject wiki pages into the system prompt -- that is a common misconception. Instead, the agent discovers wiki content through pointers in AGENTS.md and reads specific pages via read_file (progressive disclosure). OpenWiki uses claims-driven updates with grounded claims stored under openwiki/.claims/. It produces git-resident Markdown, so it is world-readable to anyone with repo access -- never put secrets in wiki pages. It is not a substitute for skills, memory, or the RAG index.

**Q9. What are the biggest failure modes in context engineering?**
The top three I watch for: First, shared writable memory without user-scoped namespaces -- this is a cross-tenant prompt-injection channel where one user poisons instructions for another. Second, putting critical instructions in conversation history instead of the system prompt -- the summarizer will compress them away at 85% capacity. Third, placing MemoryMiddleware before the cache middleware -- every memory update invalidates the entire cached prefix, destroying the 17% cost savings from caching. A fourth one for production: unused tool schemas riding along every turn without excluded_tools, silently bloating the prefix by hundreds of tokens per unnecessary tool.

**Q10. How do subagents fit into context management?**
Subagents are primarily a context isolation mechanism, not just a parallelism feature. When a tool returns a 50k-token database dump, keeping it in the parent window wastes context budget and risks summarization loss. Instead, I delegate the analysis to a subagent: the child gets a fresh context window, processes the dump, and returns a 200-token summary to the parent. The parent never sees the 50k tokens. This is the "context quarantine" pattern. The GP subagent inherits parent skills and tools; custom subagents need explicit configuration. Runtime context propagates to all subagents via namespaced keys.

**Q11. How do you handle the 36% injection rate in public skills?**
I treat any third-party skill from agentskills.io as untrusted input. The mitigation is layered: validate SKILL.md frontmatter constraints (name 1-64 chars, description max 1024 chars, body <5000 tokens, file <10MB), sandbox execution of any scripts in supporting resources, scope permissions so skills can only use their declared `allowed-tools`, make shared skills read-only via permissions, and use interrupt mode for any skill modifications. I never auto-load from the public registry without human review. The 36% figure means more than a third of public skills contain prompt injection attempts.

**Q12. Walk me through the middleware stack ordering and why it matters.**
The middleware wraps the model in an inside-out order. Skills middleware goes first (closest to model) to inject skill metadata into the prompt. PatchToolCallsMiddleware repairs dangling tool_calls from interrupted turns. Profile middleware adds the system prompt and suffix. User middleware adds custom prompt material. Then caching middleware marks the stable prefix for provider caching. Context summarizer monitors window size. HITL middleware gates tool calls. Memory middleware goes last -- after caching -- so that memory updates do not invalidate the cached prefix. Getting this wrong, especially the cache-before-memory ordering, can turn a 17% cost savings into zero savings because every memory update forces a full cache rewrite at 1.25x pricing.

---

## Key Numbers to Memorize

### Context Layers & Thresholds
| Number | What |
|--------|------|
| **5** | Context layers: input, runtime, compression, isolation, long-term memory |
| **3** | Skill progressive-disclosure levels: metadata, instructions, resources |
| **~100 tok/skill** | Metadata cost (name + description) at startup |
| **<5,000 tokens** | Skill body max recommended size |
| **<500 lines** | Skill body max recommended lines |
| **10 MB** | Skill file skip limit |
| **1-64 chars** | Skill name constraint (lowercase alphanum + hyphens) |
| **1,024 chars** | Skill description max |
| **~1.9M** | Public skills on agentskills.io |
| **36%** | Prompt injection rate in public skills |

### Compression & Caching
| Number | What |
|--------|------|
| **20,000 tokens** | Offload threshold (tool result to VFS) |
| **85%** | Summarization trigger (% of max_input_tokens) |
| **10%** | Target retention ratio after summarization |
| **170k / 6** | Fallback truncation: tokens or messages |
| **5 min** | Default Anthropic cache TTL |
| **1 hour** | Max configurable cache TTL |
| **1.25x / 0.1x** | Cache write / read price multipliers |
| **$3.75 / $0.30** | Cache write / read per MTok (Sonnet 4.6) |
| **$223 / 1k** | Cached 10-call run cost |
| **$270 / 1k** | Uncached 10-call run cost |
| **~$47 / 1k** | Cache savings at 2k prefix |

### Memory & Scoping
| Number | What |
|--------|------|
| **AGENTS.md** | Canonical memory file format |
| **`(assistant_id, user.identity)`** | Recommended namespace for multi-tenant |
| **last-write-wins** | Concurrent memory write behavior |
| **>=0.5.0** | `rt.server_info` namespace factories |
| **#1356** | GitHub issue: Memory middleware AFTER cache middleware |

### Latency [inferred policy targets]
| Number | What |
|--------|------|
| **640 / 2,560 / 5,120 ms** | Cache-hit first token p50/p95/p99 |
| **2,000 / 8,000 / 20,000 ms** | One ReAct cycle (model + tool) |
| **20,000 / 80,000 / 200,000 ms** | 10-call research run |

---

## Quick Reference

```
CONTEXT SCHEMA vs STATE SCHEMA
  context_schema = immutable per-run config (user_id, flags) -- not in prompt
  state_schema   = mutable checkpointed state (messages, results) -- not in prompt

SKILLS vs MEMORY
  Skills  = progressive disclosure (metadata → instructions → resources)
  Memory  = always loaded (AGENTS.md, preferences, policies)

SUMMARIZATION PIPELINE
  Tool output > 20k tokens  → offload to VFS (replace with path)
  Total context > 85%       → summarize older history (keep ~10%)
  Still exceeds max         → ContextOverflowError → re-summarize
  Fallback                  → truncate to 170k tokens or 6 messages

CACHE MIDDLEWARE ORDER
  Skills → Patch → Profile → User → CACHE → ... → Summarizer → HITL → MEMORY
  Memory AFTER cache = cache stays valid when memory updates
  Memory BEFORE cache = every memory write invalidates the cache

MEMORY SCOPING
  Agent:      (assistant_id,)           -- shared, writable = injection risk
  User:       (user.identity,)          -- private, safe
  Agent+User: (assistant_id, identity)  -- recommended default
  Org:        (org_id,)                 -- MUST be read-only
```
