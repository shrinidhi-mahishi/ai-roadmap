# Module 10: Context Engineering, Memory & Skills in LangChain Deep Agents

**Study Module for Director/VP AI Interviews**
**Date**: 2026-09-02 | **Sources**: 18 primary sources

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

The skills system uses **progressive disclosure** (now a "Trial" technique on the ThoughtWorks
Technology Radar): load skill names at startup, full instructions only when needed. The
AGENTS.md memory system provides persistent cross-session state. Prompt caching cuts costs
by up to 86% over extended sessions.

## Why It Matters

Context engineering is the single biggest lever for agent reliability in production.
Anthropic's engineering team reports that "context rot" -- gradual degradation as irrelevant
content accumulates -- caused nearly 65% of enterprise AI failures in 2025. Mastering these
patterns separates architects who deploy reliable agents from those who build demos.

---

## Part 1: System Topology & Data Flow

### Architecture Diagram

```
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                         CONTROL PLANE                                       │
 │  ┌───────────────┐  ┌──────────────────┐  ┌──────────────────────────────┐  │
 │  │ Agent Config   │  │ Middleware Stack  │  │ Model Profile                │  │
 │  │ - model        │  │ 1. Filesystem    │  │ - max_input_tokens: 200K    │  │
 │  │ - system_prompt│  │ 2. SubAgent      │  │ - cache_threshold: 1024/4096│  │
 │  │ - tools        │  │ 3. Summarization │  │ - offload_limit: 20K        │  │
 │  │ - permissions  │  │ 4. PatchToolCalls│  │                              │  │
 │  └───────────────┘  │ 5. Profile Extras│  └──────────────────────────────┘  │
 │                      │ 6. Prompt Caching│                                   │
 │                      │ 7. Memory        │                                   │
 │                      └──────────────────┘                                   │
 ├─────────────────────────────────────────────────────────────────────────────┤
 │                         DATA PLANE                                          │
 │                                                                             │
 │  ┌──────────────────── System Prompt Assembly ──────────────────────────┐   │
 │  │ 1. Custom system_prompt           (user-defined)                     │   │
 │  │ 2. Base agent prompt              (harness defaults)                 │   │
 │  │ 3. Memory prompt                  (AGENTS.md -- always loaded)       │   │
 │  │ 4. Skills prompt                  (name+desc only -- ~100 tok each)  │   │
 │  │ 5. Virtual filesystem prompt      (built-in tool docs)               │   │
 │  │ 6. Subagent prompt                (task tool instructions)           │   │
 │  │ 7. Custom middleware + HITL prompt (optional)                        │   │
 │  └─────────────────────────────────────────────────────────────────────┘   │
 │                           │                                                 │
 │                           v                                                 │
 │  ┌────────────────── Runtime Context ──────────────────┐                   │
 │  │ ToolRuntime + typed context_schema                   │                   │
 │  │ (user_id, api_key, org_id -- propagates to subagents)│                   │
 │  └──────────────────────┬───────────────────────────────┘                   │
 │                          │                                                   │
 │                          v                                                   │
 │  ┌──── Context Window (Working Memory) ─────────────────────────────────┐   │
 │  │                                                                       │   │
 │  │  [System Prompt] + [Message History] + [Tool Results]                │   │
 │  │                                                                       │   │
 │  │  Monitors:                                                            │   │
 │  │  - Tool result > 20K tokens? --> Offload to filesystem + 10-line stub│   │
 │  │  - Window > 85% full?       --> Summarize history                    │   │
 │  │  - ContextOverflowError?    --> Emergency summarize + retry          │   │
 │  └───────────────────────────────────────────────────────────────────────┘   │
 ├─────────────────────────────────────────────────────────────────────────────┤
 │                       PERSISTENCE LAYER                                     │
 │                                                                             │
 │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌─────────────────┐   │
 │  │ StateBackend  │ │ StoreBackend  │ │ Filesystem   │ │ ContextHub      │   │
 │  │ Thread-scoped │ │ Cross-thread  │ │ Backend      │ │ Backend         │   │
 │  │ via           │ │ via LangGraph │ │ Local disk   │ │ Versioned in    │   │
 │  │ checkpointer  │ │ BaseStore     │ │ or sandbox   │ │ LangSmith Hub   │   │
 │  └──────────────┘ └──────────────┘ └──────────────┘ └─────────────────┘   │
 ├─────────────────────────────────────────────────────────────────────────────┤
 │                       TELEMETRY                                             │
 │  ┌──────────────────────────────────────────────────────────────────────┐   │
 │  │ LangSmith: run tags {'lc_agent_name': '...'} + ContextHub versions  │   │
 │  │ Metrics: token burn rate, cache hit ratio, compression frequency     │   │
 │  └──────────────────────────────────────────────────────────────────────┘   │
 └─────────────────────────────────────────────────────────────────────────────┘
```

### Request-Flow Narrative

1. **Startup**: The middleware stack assembles the system prompt in fixed order. Memory
   (AGENTS.md) loads unconditionally. Skills load metadata only (~100 tokens each). Prompt
   caching middleware marks the static prefix boundary.

2. **Per-Turn**: User message arrives. ToolRuntime injects typed runtime context. The LLM
   sees the full assembled prompt (cached prefix + new messages). If a skill is relevant, the
   LLM loads its full instructions (progressive disclosure level 2).

3. **Tool Execution**: Tool results flow back. If a single result exceeds 20,000 tokens, it
   is offloaded to the filesystem and replaced with a path reference plus a 10-line preview.

4. **Compression Check**: After each turn, if context window utilization exceeds 85% of
   `max_input_tokens`, the SummarizationMiddleware triggers. It produces a structured summary
   (intent, artifacts, next steps) and writes the full original text to disk. The 10% most
   recent tokens are preserved verbatim.

5. **Subagent Delegation**: If the agent delegates via the `task` tool, a subagent runs with
   a completely fresh context window. The parent receives only the final summary (10:1 to
   50:1 compression ratio). Parent context stays clean.

6. **Cache Economics**: On subsequent turns, the stable system prompt prefix hits the cache
   (0.1x read cost). Memory middleware is placed after prompt caching middleware so that
   memory updates do not invalidate the cache prefix.

---

## Part 2: Core Mechanics & Algorithms

### Context Flow Architecture

The context management system operates across four distinct phases:

**Phase 1: Static Assembly (Startup)**

Seven ordered components are concatenated into the system prompt. The order is not arbitrary --
it is optimized for prompt cache stability. Components that change rarely (base prompt, tool
docs) are placed before components that change more often (memory, middleware prompts).

**Phase 2: Progressive Disclosure (Runtime)**

Skills implement a three-tier loading strategy:

| Tier | What Loads | When | Token Cost |
|------|-----------|------|------------|
| Metadata | `name` + `description` from frontmatter | Agent startup (all skills) | ~100 tokens/skill |
| Instructions | Full SKILL.md body | Agent determines relevance | <5,000 tokens recommended |
| Resources | scripts/, references/, assets/ | Instructions reference them | Variable |

The Skill Identity Pattern: rather than routing to specialized sub-agents, a single agent
assumes different identities on demand. At rest it has a base identity. When a skill
activates, it adopts that skill's instructions, constraints, tone, and behavioral patterns.
When the task completes, it returns to base. This avoids the overhead of subagent context
assembly for lightweight specialization.

**Phase 3: Compression Triggers**

Two-phase compression with distinct mechanisms:

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

**Critical design decision**: The 85% threshold is deliberately conservative. Setting it at
90% creates the "compressor overflow trap" -- by the time compression fires, the middle
region may be 180K tokens, which exceeds the summarizer model's own context limit.

**Phase 4: Context Isolation via Subagents**

The `task` tool spawns subagents with completely fresh context windows. The parent receives
only the final ToolMessage. This is the most powerful compression mechanism: 10:1 to 50:1
ratio (Anthropic's engineering measurements). Subagent conversation history is discarded.

### Memory Lifecycle

**AGENTS.md Persistent Memory**:
- Always injected into the system prompt (no progressive disclosure)
- Suitable for compact, always-relevant info (preferences, conventions, standards)
- NOT suitable for large corpora (injected every invocation)
- Cross-session persistence requires CompositeBackend routing to LangGraph Store

**Backend Routing via CompositeBackend**:

```
CompositeBackend
  ├── default: StateBackend (thread-scoped, via checkpointer)
  ├── /memories/: StoreBackend (cross-thread, namespace factory)
  ├── /skills/shared/: StoreBackend (org-scoped namespace)
  └── /skills/personal/: StoreBackend (user-scoped namespace)
```

**Scalability constraint**: Memory files grow the system prompt linearly. For applications
needing to remember many past interactions, RAG with a vector store is the correct pattern.

### Prompt Caching Mechanics

Cache key derivation: exact bytes in fixed order (tools -> system -> messages). One changed
character at position N invalidates everything after position N.

Token thresholds for cache checkpoints:
- Claude Opus 4.6/4.5, Sonnet 4.5, Haiku 4.5: 4,096 tokens per checkpoint
- Claude Sonnet 4.6: 1,024 tokens (lower threshold)

TTL behavior:
- Default: 5 minutes
- Amazon Bedrock: 1-hour TTL (January 2026)
- Warm-keeping: a request at least every 5 minutes keeps the cache alive indefinitely

MemoryMiddleware placement after prompt caching middleware prevents memory updates from
invalidating the cached prefix.

### Multimodal Context Limitations

Deep Agents supports images, video, audio, and documents. Three integration paths: user
message content blocks, built-in `read_file` tool, and custom tool outputs.

**Critical limitation**: Context compression is text-oriented. During summarization, image,
audio, video, and file blocks are **discarded** -- only text descriptions survive. The
offloading mechanism measures text tokens only; non-text blocks are preserved but not
compressed by size.

Production mitigation: delegate multimodal-heavy inspection to subagents that return compact
text results before media enters the main agent's compaction cycle.

### Agent Skills Specification (agentskills.io)

Released by Anthropic (October 2025), governance moved to AAIF under the Linux Foundation.
As of mid-2026: ~40 products support it, ~60,000 repos use it, ~1.9M public skills indexed.
Licensed Apache 2.0 (code) / CC-BY-4.0 (docs).

A 2026 audit found **prompt injection in 36% of tested public skills**. Skills can execute
bundled scripts. Treat community skills like open-source dependencies: review before install.

---

## Part 3: Token Economics & NFR Analysis

### Context Budget Parameters

| Parameter | Default Value | Tuning Guidance |
|-----------|---------------|-----------------|
| Working context budget | 200,000 tokens | Model-dependent |
| Offload threshold | 20,000 tokens/tool call | Lower for chatty tools |
| Summarization trigger | 85% of max_input_tokens | Never above 90% (compressor trap) |
| Recent context preserved | 10% of tokens | Increase for high-coherence tasks |
| Fallback trigger | 170K tokens / 6 messages | When model profile unavailable |
| Offloaded result preview | First 10 lines | Sufficient for most tool outputs |

### Cost Formulas

**Per-turn cost without caching**:
```
C_turn = (input_tokens * P_input) + (output_tokens * P_output)
```

**Per-turn cost with prompt caching**:
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

### Cost Reduction Mechanisms

| Mechanism | Savings | Trade-off |
|-----------|---------|-----------|
| Prompt caching | 72-86% input cost | 5-min TTL requires warm-keeping |
| Progressive disclosure | ~100 tok/skill vs. 275-8,000 eager | Adds one LLM read decision per skill |
| Subagent isolation | 10:1 to 50:1 compression | Latency per delegation (fresh invocation) |
| Tool exclusion | Variable (per tool removed) | Reduces agent capability |
| Offloading | Prevents context overflow | Loses in-context searchability |

### Latency Analysis

| Operation | Latency Impact | When |
|-----------|---------------|------|
| Prompt cache hit | Reduces TTFT (skips reprocessing prefix) | Every turn after first |
| Summarization | +1 LLM call (seconds) | At 85% utilization |
| Subagent delegation | +1 fresh LLM invocation per delegation | Per `task` call |
| Skill level-2 load | +1 file read (<10ms) | When skill becomes relevant |

**Latency SLA targets** (production guidance):

| Metric | Interactive Agent | Background Agent |
|--------|------------------|-----------------|
| p50 TTFT | <2s | N/A |
| p95 TTFT | <5s | <10s |
| p99 TTFT | <10s | <30s |
| p50 full response | <15s | <60s |
| p95 full response | <45s | <180s |

### Capacity Planning

**Effective context capacity**: Research shows performance degrades starting at ~65% of
advertised window (effective capacity ~130K for a 200K model). Plan for 60-70% utilization
as the usable ceiling.

**TodoListMiddleware**: Benchmarking found no statistically significant accuracy improvement
and higher token usage on 2 of 3 models. Now opt-in (July 2026). Do not include in default
middleware unless measured improvement for your specific workload.

### Availability, RPO/RTO & Compliance Targets

| NFR | Target | Rationale |
|-----|--------|-----------|
| Availability (context assembly pipeline) | 99.9% | Core path for every agent turn; outage = total agent failure |
| Availability (compression operations) | 99.5% | LLM-dependent; summarizer model outage degrades but does not block (agent continues with larger context until overflow) |
| RPO (checkpoint-backed memory, PostgresSaver) | 0 (no data loss) | Every checkpoint is durable; crash between checkpoints loses only in-flight turn |
| RPO (MemorySaver / in-memory) | 1 conversation (total loss on crash) | Acceptable for dev/test; never use in production |
| RTO (context restoration from checkpoint) | <30s | Reload thread state from PostgresSaver + reassemble system prompt |
| RTO (full memory rehydration from LangGraph Store) | <5 min | Cross-thread memory retrieval from StoreBackend (depends on store size and query complexity) |

**Compliance**:
- **GDPR Article 17 (Right to Erasure)**: Applies to AGENTS.md content and LangGraph Store memory entries. Must support per-user deletion across all namespace-scoped backends (CompositeBackend routing by user_id). Deletion must propagate to checkpoints containing the user's data.
- **EU AI Act**: Context assembly must be auditable. ContextHubBackend versioning + LangSmith traces provide the required transparency into what context was presented to the model at each decision point.

**Key trade-off**: Aggressive compression (save tokens, risk losing constraints) vs conservative compression (expensive, preserve fidelity) vs subagent isolation (latency overhead, best fidelity). Production recommendation: pin critical constraints in AGENTS.md (never compressed), use subagent isolation for tool-heavy work, and apply summarization only to conversation history.

---

## Part 4: Distributed Resilience & Security

### Durable Execution & Checkpointing

Deep Agents builds on LangGraph's durable execution. State persists across turns within a
thread via the checkpointer. Custom state schemas must subclass `DeepAgentState` to preserve
the `DeltaChannel` reducer on messages.

**Checkpoint scope**:

| Backend | Scope | Durability | Use Case |
|---------|-------|------------|----------|
| StateBackend | Thread | Survives across turns | Default per-conversation state |
| StoreBackend | Cross-thread | Durable via BaseStore | Shared memory, user preferences |
| FilesystemBackend | Local disk | Until deleted | Sandboxed file operations |
| ContextHubBackend | LangSmith Hub | Versioned | A/B testing prompts, audit trail |

**Human-in-the-Loop**: When `interrupt_on` is configured, the agent pauses at critical
decision points. Requires a checkpointer for state persistence during the pause.

### Failure Taxonomy

**Transient failures (automatic recovery)**:

| Failure | Detection | Recovery |
|---------|-----------|----------|
| ContextOverflowError | Framework raises exception | Immediate summarization + retry |
| Tool result too large | Token count > 20K | Offload to filesystem + stub |
| Cache miss | TTL expired | Re-cache on next request (transparent) |

**Permanent failures (require architectural mitigation)**:

| Failure | Description | Mitigation |
|---------|-------------|------------|
| Context rot | Silent accuracy degradation as irrelevant content accumulates | Subagent isolation, compression, structured memory |
| Lossy compression | Summarizer discards critical constraints or exact values | Pin critical info in AGENTS.md (never summarized) |
| Multimodal content loss | Images/audio/video discarded during summarization | Delegate media processing to subagents first |
| Memory staleness | Outdated AGENTS.md silently biases behavior | Version memory, validate against tool evidence |
| Over-compression | Agent erases task-critical evidence that seemed low-priority | Lower summarization threshold, use structured notes |
| Under-compression | Agent fails to compress enough, overflows | Default 85% threshold handles this |

**The Four Failure Modes Framework (Drew Breunig)**:

| Mode | Attack Vector | Mitigation |
|------|--------------|------------|
| Context Poisoning | Incorrect info injected early persists | Input validation, source verification |
| Distraction | Irrelevant content dilutes attention | Compression, filtering |
| Confusion | Too many tools or conflicting instructions | Tool selection middleware, skill boundaries |
| Clash | Multi-turn info accumulates contradictions | Isolation, structured memory |

Compression alone handles Distraction but does nothing about Confusion or Clash.

### Zero-Trust & Security

**Trust model**: Deep Agents follows "trust the LLM" -- the agent can do anything its tools
allow. Security is enforced at the tool/sandbox level, not by expecting the model to
self-police.

**Filesystem permissions** (layered):

```python
# Read-only skills
FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny")

# Approval-required writes (requires checkpointer for pause)
FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="interrupt")
```

**Multi-tenant isolation**: CompositeBackend routes with namespace factories derived from
runtime context (org_id, user identity). Skills and memory are namespace-isolated per
organization and per user.

**Skill security**: 36% prompt injection rate in public skills (2026 audit). Mitigations:
- Review community skills before install (treat like open-source deps)
- Sandbox backends for script execution
- Filesystem permissions to restrict write access
- `allowed-tools` frontmatter (experimental) for pre-approved tools

**Audit trail**: All runs tagged with `{'lc_agent_name': 'subagent-name'}` in LangSmith.
ContextHubBackend provides versioned storage for audit of context changes across runs.

### Zero-Trust Context Pipeline

Every context source (skills, memory, tool results) is treated as potentially compromised:
- **Skill content sandboxed**: 36% prompt injection rate in public skills (2026 audit). Skill instructions execute in a restricted environment; filesystem permissions prevent write access to system paths.
- **Memory writes validated before persistence**: Content written to AGENTS.md or LangGraph Store is validated against schema expectations. Reject writes that contain prompt injection patterns or exceed size thresholds.
- **Tool results sanitized before context injection**: Tool outputs are scanned for injection attempts before being added to the context window. Offloaded results (>20K tokens) are sanitized at read-back time as well.

### RBAC for Context Access

| Role | Context Scope | Memory Access | Skill Management |
|------|--------------|---------------|-----------------|
| **User** | Own conversation context only | Read/write own namespace in StoreBackend | Use skills; no install/modify |
| **Team Lead** | Team conversations + shared memory | Read team namespaces; write own | Install org-approved skills |
| **Admin** | All contexts + skill management | Full read/write across namespaces | Install/modify/remove any skill; manage skill marketplace |
| **Auditor** | Read-only access to context logs + memory audit trail | Read-only across all namespaces | Read skill configs; no modification |

Enforcement: Namespace isolation in CompositeBackend (route by user_id/org_id from TenantContext) + LangGraph Store scoping. Role checked at runtime context injection; unauthorized access returns empty results, not errors (fail-closed for writes, fail-safe for reads).

### PII Filtering Pipeline

```
Incoming Message ──> [1. Detection] ──> [2. Redaction] ──> [3. Context Filtering] ──> LLM
                          │                    │                      │
                          v                    v                      v
                     Scan user messages,  Mask PII before        Strip PII from
                     tool results, and    storing in AGENTS.md   context window
                     memory retrievals    or LangGraph Store     before sending
                     for PII              (typed placeholders:   to model
                                          [EMAIL_1], [SSN_1],
                                          [PHONE_1])

[4. Audit Trail]: Log every PII detection with:
    - conversation_id
    - source (user_message | tool_result | memory_retrieval)
    - PII type (email | phone | ssn | address | name)
    - action taken (redacted | blocked | passed_with_consent)
```

Critical for multi-tenant deployments: PII filtering must apply at the CompositeBackend boundary to prevent cross-tenant PII leakage through shared memory namespaces.

---

## Part 5: Production Enterprise Code

### Complete Context-Managed Agent with Memory and Skills

```python
"""
Production context-engineered agent with memory, skills, prompt caching,
and multi-tenant isolation. Requires: pip install langchain-deepagents
"""
from dataclasses import dataclass
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.middleware import (
    SummarizationMiddleware,
    SkillsMiddleware,
    MemoryMiddleware,
)
from deepagents.permissions import FilesystemPermission
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore


# --- Runtime Context Schema ---

@dataclass
class TenantContext:
    """Typed context propagated to all subagents automatically."""
    user_id: str
    org_id: str
    api_key: str
    tier: str  # "free" | "pro" | "enterprise"


# --- Backend Configuration ---

def build_backend(store: Any) -> CompositeBackend:
    """Multi-tenant backend with namespace isolation."""
    return CompositeBackend(
        default=StateBackend(),  # Thread-scoped (per conversation)
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


# --- Agent Factory ---

def create_context_managed_agent(
    model: str = "anthropic:claude-sonnet-4-6",
    max_input_tokens: int = 200_000,
    summarization_trigger: float = 0.85,
    offload_threshold: int = 20_000,
) -> Any:
    """
    Create a production agent with full context engineering:
    - Prompt caching (automatic for Anthropic models)
    - Progressive skill disclosure
    - Memory persistence across sessions
    - Summarization with filesystem preservation
    - Multi-tenant namespace isolation
    """
    store = InMemoryStore()  # Replace with PostgresStore for production
    checkpointer = MemorySaver()  # Replace with PostgresSaver for production

    agent = create_deep_agent(
        model=model,
        system_prompt=(
            "You are a production support agent. You help enterprise customers "
            "diagnose and resolve technical issues. Always verify information "
            "against tool evidence before responding. If memory (AGENTS.md) "
            "conflicts with current tool output, trust the tool output."
        ),
        memory=["./AGENTS.md"],  # Always loaded, never progressively disclosed
        skills=["/skills/"],     # Progressive disclosure: metadata at startup
        store=store,
        checkpointer=checkpointer,
        backend=build_backend(store),
        permissions=[
            # Skills are read-only
            FilesystemPermission(
                operations=["write"], paths=["/skills/**"], mode="deny"
            ),
            # User output directory is writable
            FilesystemPermission(
                operations=["write"], paths=["/output/**"], mode="allow"
            ),
            # Everything else requires approval
            FilesystemPermission(
                operations=["write"], paths=["/**"], mode="interrupt"
            ),
        ],
        interrupt_on={"delete_file": True},  # HITL for destructive ops
        max_input_tokens=max_input_tokens,
    )

    return agent


# --- Invocation with Tenant Context ---

def handle_support_request(
    agent: Any,
    user_message: str,
    tenant: TenantContext,
    thread_id: str,
) -> str:
    """
    Invoke agent with tenant context and thread-scoped state.
    Context propagates automatically to any subagents spawned.
    """
    config = {"configurable": {"thread_id": thread_id}}

    result = agent.invoke(
        {"messages": [{"role": "user", "content": user_message}]},
        config=config,
        context=tenant,
    )

    return result["messages"][-1].content


# --- Monitoring: Token Budget Tracker ---

class ContextBudgetMonitor:
    """
    Track context window utilization across turns.
    Alert when approaching compression thresholds.
    """

    def __init__(self, max_tokens: int = 200_000, warn_at: float = 0.70):
        self.max_tokens = max_tokens
        self.warn_threshold = warn_at
        self.turn_history: list[dict] = []

    def record_turn(self, input_tokens: int, output_tokens: int, cached: bool) -> dict:
        utilization = input_tokens / self.max_tokens
        effective_cost = input_tokens * (0.1 if cached else 1.0)

        entry = {
            "turn": len(self.turn_history) + 1,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "utilization": round(utilization, 3),
            "cached": cached,
            "effective_cost_multiplier": 0.1 if cached else 1.0,
            "alert": utilization > self.warn_threshold,
        }
        self.turn_history.append(entry)

        if entry["alert"]:
            print(
                f"[WARN] Turn {entry['turn']}: context at {utilization:.1%} "
                f"(threshold: {self.warn_threshold:.0%}). "
                f"Summarization expected at 85%."
            )

        return entry

    def cost_report(self, price_per_1k_input: float, price_per_1k_output: float) -> dict:
        total_input = sum(t["input_tokens"] for t in self.turn_history)
        total_output = sum(t["output_tokens"] for t in self.turn_history)
        cached_input = sum(
            t["input_tokens"] for t in self.turn_history if t["cached"]
        )
        uncached_input = total_input - cached_input

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
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "cache_hit_rate": (
                cached_input / total_input if total_input > 0 else 0
            ),
            "estimated_cost": round(cost, 4),
            "cost_without_cache": round(cost_without_cache, 4),
            "savings_pct": round(
                (1 - cost / cost_without_cache) * 100 if cost_without_cache > 0 else 0, 1
            ),
        }


# --- Usage Example ---

if __name__ == "__main__":
    agent = create_context_managed_agent()

    tenant = TenantContext(
        user_id="user-42",
        org_id="acme-corp",
        api_key="sk-prod-xxx",
        tier="enterprise",
    )

    # First request in a new thread
    response = handle_support_request(
        agent=agent,
        user_message="Our API latency spiked to 5s p99 after yesterday's deploy.",
        tenant=tenant,
        thread_id="ticket-12345",
    )
    print(response)

    # Monitor context budget
    monitor = ContextBudgetMonitor(max_tokens=200_000, warn_at=0.70)
    monitor.record_turn(input_tokens=12_000, output_tokens=800, cached=False)
    monitor.record_turn(input_tokens=12_000, output_tokens=1_200, cached=True)
    monitor.record_turn(input_tokens=45_000, output_tokens=2_000, cached=True)

    report = monitor.cost_report(
        price_per_1k_input=0.003,  # Claude Sonnet 4.6 input
        price_per_1k_output=0.015,  # Claude Sonnet 4.6 output
    )
    print(f"\nCost report: {report}")
```

### SKILL.md Example with Frontmatter

```python
"""
Programmatic skill registration and validation.
Demonstrates the Agent Skills Specification (agentskills.io).
"""
import yaml
from pathlib import Path


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

## Output Format
Provide a structured incident summary with:
- Severity level and justification
- Affected services and regions
- Recommended immediate actions
- Suggested runbook (if applicable)

## When Complete
Return to base identity. Do not retain incident-specific context
beyond the summary provided to the user.
"""


def write_skill(skills_dir: str, name: str, frontmatter: dict, body: str) -> Path:
    """Write a SKILL.md file with proper frontmatter."""
    skill_path = Path(skills_dir) / name
    skill_path.mkdir(parents=True, exist_ok=True)

    skill_file = skill_path / "SKILL.md"
    frontmatter_yaml = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False)
    content = f"---\n{frontmatter_yaml}---\n\n{body}"

    skill_file.write_text(content)
    return skill_file


def validate_skill_frontmatter(frontmatter: dict) -> list[str]:
    """Validate skill frontmatter against the Agent Skills Specification."""
    errors = []

    # Required fields
    if "name" not in frontmatter:
        errors.append("Missing required field: name")
    elif not isinstance(frontmatter["name"], str):
        errors.append("name must be a string")
    elif len(frontmatter["name"]) > 64:
        errors.append("name must be 1-64 characters")
    elif not all(c.isalnum() or c == "-" for c in frontmatter["name"]):
        errors.append("name must be lowercase alphanumeric + hyphens only")

    if "description" not in frontmatter:
        errors.append("Missing required field: description")
    elif len(frontmatter.get("description", "")) > 1024:
        errors.append("description must be 1-1024 characters")

    # Optional field constraints
    if "compatibility" in frontmatter and len(frontmatter["compatibility"]) > 500:
        errors.append("compatibility must be max 500 characters")

    if "metadata" in frontmatter:
        meta = frontmatter["metadata"]
        if not isinstance(meta, dict):
            errors.append("metadata must be a key-value mapping")
        elif not all(isinstance(v, str) for v in meta.values()):
            errors.append("metadata values must be strings")

    return errors


if __name__ == "__main__":
    # Validate
    errors = validate_skill_frontmatter(SKILL_FRONTMATTER)
    if errors:
        print(f"Validation errors: {errors}")
    else:
        print("Skill frontmatter is valid.")

    # Write
    path = write_skill(
        skills_dir="/tmp/agent-skills",
        name="incident-triage",
        frontmatter=SKILL_FRONTMATTER,
        body=SKILL_BODY,
    )
    print(f"Skill written to: {path}")
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: Long-Running Research Agent (100+ Turns, Multi-Session)

**Problem Statement**: Design a research agent that conducts deep technical investigations
spanning 100+ turns across multiple sessions. The agent must retain findings across sessions,
handle context window exhaustion gracefully, and provide cost-predictable operation. Expected
workload: 4-hour research sessions with 15-20 tool calls per session.

**Architecture**:

```
┌──────────────────────────────────────────────────────────────────┐
│                    RESEARCH ORCHESTRATOR                          │
│                                                                  │
│  System Prompt (cached):                                         │
│  - Base instructions + research protocol                         │
│  - AGENTS.md (compact user prefs + project context)              │
│  - Skill metadata (web-search, code-analysis, summarizer)        │
│                                                                  │
│  Compression Strategy:                                           │
│  - Agent-level: summarize at 50% (proactive)                     │
│  - Gateway safety net: summarize at 85% (defensive)              │
│  - Structured notes written to /memories/ (cross-session)        │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │ Subagent:     │  │ Subagent:     │  │ Subagent:     │          │
│  │ Topic A       │  │ Topic B       │  │ Topic C       │          │
│  │ (fresh ctx)   │  │ (fresh ctx)   │  │ (fresh ctx)   │          │
│  │ Returns 2K    │  │ Returns 2K    │  │ Returns 2K    │          │
│  │ summary       │  │ summary       │  │ summary       │          │
│  └──────────────┘  └──────────────┘  └──────────────┘           │
│                                                                  │
│  Cross-Session Memory:                                           │
│  CompositeBackend -> StoreBackend(/memories/) -> PostgresStore   │
│                                                                  │
│  Telemetry:                                                      │
│  - Token burn rate ($/hour) alerts for runaway loops             │
│  - Compression frequency tracking                                │
│  - Cache hit ratio monitoring                                    │
└──────────────────────────────────────────────────────────────────┘
```

**Trade-off Matrix**:

| Decision | Option A | Option B | Choice | Rationale |
|----------|----------|----------|--------|-----------|
| Compression trigger | 85% (default) | 50% agent + 85% gateway | **Two-layer (B)** | Hermes-style offset prevents compressor trap; proactive at 50% keeps context cleaner |
| Memory storage | AGENTS.md only | AGENTS.md + StoreBackend | **Composite (B)** | AGENTS.md for compact prefs; StoreBackend for growing research notes |
| Research subtopics | Single agent, sequential | Subagent per topic | **Subagents (B)** | Fresh context per topic prevents cross-topic interference; 10:1 compression |
| Model for subagents | Same as parent (Sonnet) | Cheaper model (Haiku) | **Haiku for search, Sonnet for synthesis** | 80% of subagent work is summarizing search results; Haiku sufficient at 1/10 cost |

**Decision Rationale**: The two-layer compression strategy (50% proactive + 85% safety net)
is the key architectural choice. At 50%, the context is small enough that the summarizer
can read the full middle region without risk. The 85% gateway catches edge cases where the
agent generates large outputs between compression cycles. Cross-session memory uses
StoreBackend (not just AGENTS.md) because research notes grow beyond what should be in
every system prompt.

---

### Scenario 2: Multi-Tenant SaaS Agent Platform with Skill Marketplace

**Problem Statement**: Design an agent platform serving 500 enterprise tenants, each with
custom skills and memory. Tenants must be isolated (no data leakage), skills must be
auditable, and the platform must support A/B testing of prompt variations. Expected load:
10,000 concurrent agent sessions.

**Architecture**:

```
┌──────────────────────────────────────────────────────────────────┐
│                    TENANT GATEWAY                                │
│  - Authenticates tenant, extracts org_id + user_id              │
│  - Injects TenantContext into runtime                           │
│  - Routes to agent pool                                         │
├──────────────────────────────────────────────────────────────────┤
│                    AGENT POOL (per tenant config)                │
│                                                                  │
│  ┌─────────────────────── Per-Agent Instance ──────────────────┐│
│  │                                                              ││
│  │  CompositeBackend:                                           ││
│  │  ├── default: StateBackend (thread-scoped)                   ││
│  │  ├── /memories/: StoreBackend(ns=("memories", user_id))     ││
│  │  ├── /skills/shared/: StoreBackend(ns=("org", org_id))      ││
│  │  └── /skills/personal/: StoreBackend(ns=("user", user_id)) ││
│  │                                                              ││
│  │  Permissions:                                                ││
│  │  - Shared skills: read-only                                  ││
│  │  - Personal skills: read-write                               ││
│  │  - Shared resources: write requires HITL interrupt           ││
│  │                                                              ││
│  │  Prompt Caching:                                             ││
│  │  - 1-hour TTL on Bedrock (shared base prompt cached)         ││
│  │  - Per-tenant system prompt appended after cache boundary    ││
│  │                                                              ││
│  │  A/B Testing:                                                ││
│  │  - ContextHubBackend for versioned prompt variants           ││
│  │  - LangSmith traces tagged with variant ID                   ││
│  │                                                              ││
│  └──────────────────────────────────────────────────────────────┘│
├──────────────────────────────────────────────────────────────────┤
│                    SKILL MARKETPLACE                             │
│  - Community skills: sandboxed execution, reviewed before publish│
│  - Org skills: org-admin approved, namespace-isolated            │
│  - Validation: skills-ref validate on upload                     │
│  - Audit: ContextHubBackend versions + LangSmith trace tags      │
├──────────────────────────────────────────────────────────────────┤
│                    OBSERVABILITY                                 │
│  - LangSmith: per-tenant cost attribution, per-skill usage       │
│  - Alerts: token burn rate anomalies, cache hit ratio drops      │
│  - Audit: skill version history, permission change log           │
└──────────────────────────────────────────────────────────────────┘
```

**Trade-off Matrix**:

| Decision | Option A | Option B | Choice | Rationale |
|----------|----------|----------|--------|-----------|
| Tenant isolation | Namespace in shared store | Separate store per tenant | **Namespace (A)** | 500 tenants with separate stores is operationally untenable; namespace routing is proven at scale |
| Skill security | Trust marketplace review | Sandbox all skill scripts | **Sandbox all (B)** | 36% prompt injection rate in public skills; review is necessary but insufficient alone |
| Prompt caching | Per-tenant cache | Shared base + per-tenant suffix | **Shared base (B)** | Base prompt (tools, harness) is identical across tenants; amortize cache write across all sessions |
| Memory persistence | AGENTS.md only | AGENTS.md + RAG for history | **RAG for history (B)** | Enterprise tenants accumulate interaction history; AGENTS.md does not scale for recall |

**Decision Rationale**: Namespace isolation via CompositeBackend is the core architectural
pattern. Each tenant's data is scoped by org_id and user_id in the namespace factory,
preventing cross-tenant data access without the operational burden of separate infrastructure.
Skill sandboxing is non-negotiable given the 36% injection rate. Shared prompt caching
maximizes cost efficiency -- the base prompt (tool definitions, harness defaults) is identical
across tenants, so one cache write serves all. Per-tenant customization is appended after the
cache boundary, keeping the cached prefix stable.

---

## Key Interview Talking Points

1. **Context is not a buffer, it is a managed resource**. The four-phase lifecycle (assembly,
   injection, compression, isolation) is the architectural backbone.

2. **Progressive disclosure is a proven pattern** (ThoughtWorks Trial). Loading 50 skills
   eagerly wastes 250K tokens; loading metadata costs 5K.

3. **The 85% threshold is a safety margin**, not an optimization target. Going higher risks
   the compressor overflow trap.

4. **Prompt cache stability requires middleware ordering awareness**. Memory after caching
   middleware is a deliberate design choice.

5. **Subagent isolation is the most powerful compression mechanism** (10:1 to 50:1) but adds
   latency. Use it for naturally decomposable subtasks, not for everything.

6. **36% of public skills have prompt injection**. Treat skills like open-source dependencies:
   review, sandbox, permission-scope.

7. **Effective context capacity is 60-70% of advertised**. Plan for 130K usable on a 200K
   model.
