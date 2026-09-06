"""
Deep Agents Context Management, Memory & Skills -- context window management,
short-term and long-term memory, skills with progressive disclosure,
summarization for context offloading, and prompt caching strategy.

Context engineering is the single biggest lever for agent quality: most agent
failures are CONTEXT failures before they are reasoning failures. Deep Agents
treats context as a systems problem with five layers: input, runtime,
compression, isolation, and long-term memory.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


# =============================================================================
# --- Section 1: Context Window Management (Trim, Summarize) ------------------
# =============================================================================
# Deep Agents uses a two-stage strategy for context window pressure:
#   Stage 1 -- Offload at 20,000 tokens (single tool result -> VFS)
#   Stage 2 -- Summarize at 85% of max_input_tokens (compress history)
#
# Five context layers:
#   1. Input context (prompt-visible: system prompt, memory, skills, tools)
#   2. Runtime context (hidden: context_schema values like user_id, API keys)
#   3. Context compression (summarization + offloading)
#   4. Context isolation (subagent work in separate windows)
#   5. Long-term memory (AGENTS.md, durable files across threads)


@dataclass
class Message:
    """A single message in the conversation history."""

    role: str           # "system", "user", "assistant", "tool"
    content: str
    token_count: int = 0  # Estimated token count
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class ContextBudget:
    """Tracks token usage across context components.

    Typical 200K-token window breakdown:
      System prompt (custom):  200-1,000 tokens  (0.1-0.5%)
      DA base instructions:    ~800 tokens        (0.4%)
      Memory (AGENTS.md):      1,000-5,000 tokens (0.5-2.5%)
      Tool schemas:            2,000-4,000 tokens (1-2%)
      Skill metadata:          ~2,000 tokens      (1%)
      Stable prefix (cached):  ~6,000-12,000      (3-6%)
      Conversation history:    up to 85%
      Active skill body:       <5,000 tokens      (<2.5%)
    """

    max_tokens: int = 200_000
    system_prompt_tokens: int = 0
    tool_schema_tokens: int = 0
    skill_metadata_tokens: int = 0
    memory_tokens: int = 0
    conversation_tokens: int = 0

    @property
    def total_used(self) -> int:
        return (self.system_prompt_tokens + self.tool_schema_tokens
                + self.skill_metadata_tokens + self.memory_tokens
                + self.conversation_tokens)

    @property
    def available(self) -> int:
        return self.max_tokens - self.total_used

    @property
    def utilization(self) -> float:
        return self.total_used / self.max_tokens

    @property
    def stable_prefix_tokens(self) -> int:
        """The cacheable prefix: system prompt + tools + skill metadata."""
        return (self.system_prompt_tokens + self.tool_schema_tokens
                + self.skill_metadata_tokens)


class ContextWindowManager:
    """Manages the agent's context window with automatic offloading and summarization.

    Two triggers:
    1. Tool result > 20,000 tokens -> offload to VFS (replace with path + 10 lines)
    2. Total context > 85% of max -> summarize old history (keep ~10%)

    Fallback if summarization fails: truncate to last 170K tokens or 6 messages.
    ContextOverflowError if still exceeds after summarization -> re-summarize.
    """

    OFFLOAD_THRESHOLD = 20_000       # Tokens: offload single tool results
    SUMMARIZE_TRIGGER = 0.85         # Fraction: trigger summarization
    SUMMARIZE_RETENTION = 0.10       # Fraction: keep ~10% of summarized content
    FALLBACK_TOKEN_LIMIT = 170_000   # Tokens: emergency truncation
    FALLBACK_MESSAGE_LIMIT = 6       # Messages: emergency truncation

    def __init__(self, max_tokens: int = 200_000):
        self.max_tokens = max_tokens
        self.messages: list[Message] = []
        self.budget = ContextBudget(max_tokens=max_tokens)
        self._vfs: dict[str, str] = {}  # Offloaded content storage
        self._summarize_count = 0

    def add_message(self, message: Message) -> Message | None:
        """Add a message and handle context pressure.

        Returns: the (possibly modified) message, or None if offloaded.
        """
        # Stage 1: Offload large tool results to VFS
        if (message.role == "tool"
                and message.token_count > self.OFFLOAD_THRESHOLD):
            return self._offload_to_vfs(message)

        self.messages.append(message)
        self.budget.conversation_tokens += message.token_count

        # Stage 2: Summarize if over 85% capacity
        if self.budget.utilization >= self.SUMMARIZE_TRIGGER:
            self._summarize_history()

        return message

    def _offload_to_vfs(self, message: Message) -> Message:
        """Offload a large tool result to the virtual filesystem.

        Replace inline content with a file path + first 10 lines preview.
        The agent can later read_file to retrieve specific portions.

        Interview point: 20K threshold, replaced with path + 10-line preview.
        """
        vfs_path = f"/large_tool_results/{int(time.time())}_{id(message)}.txt"
        self._vfs[vfs_path] = message.content

        # Create preview: first 10 lines
        lines = message.content.splitlines()
        preview = "\n".join(lines[:10])
        remaining = len(lines) - 10

        offloaded = Message(
            role="tool",
            content=(
                f"[Result saved to {vfs_path}]\n"
                f"Preview (first 10 of {len(lines)} lines):\n{preview}\n"
                f"... ({remaining} more lines. Use read_file to view.)"
            ),
            token_count=200,  # Estimate for path + preview
            metadata={"offloaded_from": vfs_path, "original_tokens": message.token_count},
        )
        self.messages.append(offloaded)
        self.budget.conversation_tokens += offloaded.token_count
        return offloaded

    def _summarize_history(self) -> None:
        """Compress older conversation history.

        Keeps recent messages intact. Replaces older messages with a summary
        targeting ~10% retention of original content.

        If summarization fails: truncate to last 170K tokens or 6 messages.
        If still exceeds after summarization: raise ContextOverflowError.
        """
        self._summarize_count += 1

        # Split: keep last N messages, summarize the rest
        # In production, ContextSummarizerMiddleware does this with a model call
        keep_recent = max(6, len(self.messages) // 5)
        old_messages = self.messages[:-keep_recent]
        recent_messages = self.messages[-keep_recent:]

        if not old_messages:
            return

        # Simulate summarization (in production, this is a model call)
        old_tokens = sum(m.token_count for m in old_messages)
        summary_tokens = int(old_tokens * self.SUMMARIZE_RETENTION)

        summary_text = (
            f"[Summary of {len(old_messages)} earlier messages "
            f"({old_tokens} tokens compressed to ~{summary_tokens})]\n"
            f"Key points discussed:\n"
        )
        for msg in old_messages[:5]:  # Sample first few for the summary
            summary_text += f"- [{msg.role}] {msg.content[:100]}...\n"

        summary = Message(
            role="system",
            content=summary_text,
            token_count=summary_tokens,
            metadata={"is_summary": True, "original_count": len(old_messages)},
        )

        self.messages = [summary] + recent_messages
        self.budget.conversation_tokens = sum(m.token_count for m in self.messages)

    def get_context_for_model(self) -> list[dict]:
        """Assemble the full context for a model call.

        Prompt assembly order (what the model sees):
        1. Custom system_prompt (static) or @dynamic_prompt
        2. Built-in Deep Agents base instructions
        3. Memory files (AGENTS.md)
        4. OpenWiki pointers (on-demand, not auto-injected)
        5. Skill metadata (name + description, ~100 tok/skill)
        6. Tool schemas (every turn, even unused tools)
        7. Subagent/task guidance
        8. User middleware prompt additions
        9. HITL prompt when configured
        10. Conversation history (messages + tool results)
        """
        return [{"role": m.role, "content": m.content} for m in self.messages]

    def get_stats(self) -> dict:
        return {
            "messages": len(self.messages),
            "total_tokens": self.budget.total_used,
            "utilization": f"{self.budget.utilization:.1%}",
            "summarizations": self._summarize_count,
            "offloaded_files": len(self._vfs),
        }


# =============================================================================
# --- Section 2: Short-Term Memory (Conversation Buffer) ----------------------
# =============================================================================
# Short-term memory is the conversation history within a single thread.
# Managed by the context window manager above. Checkpointed for persistence.


@dataclass
class ConversationBuffer:
    """In-memory conversation buffer for a single thread.

    This is "short-term memory" -- the conversation the model sees.
    Persistence depends on the checkpointer:
    - MemorySaver: ephemeral (dies with process)
    - PostgresSaver: durable across restarts

    context_schema vs state_schema (common interview confusion):
    - context_schema: immutable per-run config (user_id, flags). NOT checkpointed.
    - state_schema: mutable graph state (messages, results). IS checkpointed.
    """

    thread_id: str
    messages: list[Message] = field(default_factory=list)
    _window_manager: ContextWindowManager = field(
        default_factory=lambda: ContextWindowManager(max_tokens=200_000)
    )

    def add_user_message(self, content: str, token_count: int = 0) -> None:
        """Add a user message to the conversation."""
        msg = Message(role="user", content=content,
                      token_count=token_count or len(content) // 4)
        self._window_manager.add_message(msg)
        self.messages.append(msg)

    def add_assistant_message(self, content: str, token_count: int = 0) -> None:
        """Add an assistant response."""
        msg = Message(role="assistant", content=content,
                      token_count=token_count or len(content) // 4)
        self._window_manager.add_message(msg)
        self.messages.append(msg)

    def add_tool_result(self, content: str, token_count: int = 0) -> None:
        """Add a tool result, possibly triggering offloading."""
        msg = Message(role="tool", content=content,
                      token_count=token_count or len(content) // 4)
        result = self._window_manager.add_message(msg)
        self.messages.append(result or msg)

    def get_history(self) -> list[dict]:
        """Get the conversation history for model context."""
        return self._window_manager.get_context_for_model()

    def get_stats(self) -> dict:
        return self._window_manager.get_stats()


# =============================================================================
# --- Section 3: Long-Term Memory (Store/Retrieve Facts) ----------------------
# =============================================================================
# Memory is durable state that survives across threads. Deep Agents treats
# memory as FILES on a backend (AGENTS.md), not vague "the model remembers."
#
# Two modes:
#   Semantic (hot-path): agent calls edit_file during conversation
#   Semantic (background): separate consolidation agent merges later
#
# Scoping patterns (namespace functions):
#   Agent:      (assistant_id,)           -- shared across users (injection risk)
#   User:       (user.identity,)          -- private
#   Agent+User: (assistant_id, identity)  -- recommended multi-tenant default
#   Org:        (org_id,)                 -- MUST be read-only


@dataclass
class MemoryEntry:
    """A single fact or preference stored in long-term memory."""

    key: str
    value: str
    source: str = "conversation"  # "conversation" | "consolidation" | "manual"
    updated_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


class LongTermMemoryStore:
    """Persistent memory store scoped by namespace.

    In production, this maps to StoreBackend with a namespace factory:
        namespace = lambda rt: (rt.server_info.assistant_id,
                                rt.server_info.user.identity)

    Security invariants:
    - Organization memory MUST be read-only (deny-write via permissions)
    - Concurrent writes degrade to last-write-wins
    - Partition by user namespace to prevent cross-user injection
    """

    def __init__(self):
        self._stores: dict[tuple, dict[str, MemoryEntry]] = {}

    def _get_store(self, namespace: tuple[str, ...]) -> dict[str, MemoryEntry]:
        return self._stores.setdefault(namespace, {})

    def store(self, namespace: tuple[str, ...], key: str, value: str,
              source: str = "conversation") -> None:
        """Store a fact in long-term memory.

        Hot-path: agent writes during conversation (higher token cost).
        Background: consolidation agent merges later (staleness risk).
        """
        store = self._get_store(namespace)
        store[key] = MemoryEntry(key=key, value=value, source=source)

    def retrieve(self, namespace: tuple[str, ...], key: str) -> str | None:
        """Retrieve a fact from memory."""
        store = self._get_store(namespace)
        entry = store.get(key)
        return entry.value if entry else None

    def list_facts(self, namespace: tuple[str, ...]) -> list[dict]:
        """List all facts in a namespace."""
        store = self._get_store(namespace)
        return [{"key": e.key, "value": e.value, "source": e.source}
                for e in store.values()]

    def to_agents_md(self, namespace: tuple[str, ...]) -> str:
        """Export memory as AGENTS.md format (canonical memory file).

        AGENTS.md is always loaded into the prompt when configured via
        memory=["/memories/AGENTS.md"]. It is the durable memory format.
        """
        store = self._get_store(namespace)
        lines = ["# AGENTS.md\n", "## Stored Facts\n"]
        for entry in sorted(store.values(), key=lambda e: e.key):
            lines.append(f"- **{entry.key}**: {entry.value}")
        return "\n".join(lines)

    def merge_from_conversation(
        self, namespace: tuple[str, ...],
        new_facts: list[dict[str, str]],
    ) -> int:
        """Background consolidation: merge new facts from recent threads.

        Scheduling rule: cron interval ~= lookback window.
        If you consolidate every 6 hours, look back 6 hours.

        Concurrent write hazard: parallel writes to the same memory file
        degrade into last-write-wins. Mitigate by partitioning by user
        namespace or serializing through a background consolidation agent.
        """
        merged = 0
        for fact in new_facts:
            key = fact.get("key", "")
            value = fact.get("value", "")
            if key and value:
                self.store(namespace, key, value, source="consolidation")
                merged += 1
        return merged


# =============================================================================
# --- Section 4: Skills (Reusable Prompt Templates) ---------------------------
# =============================================================================
# Skills solve the "important but not always relevant" problem. Progressive
# disclosure: only metadata loads at startup; full body loads on demand.
#
# Three levels:
#   1. Metadata: name + description (~100 tokens/skill). Always at startup.
#   2. Instructions: full SKILL.md body (<5,000 tokens). On activation.
#   3. Resources: scripts/, references/, assets/. On demand after activation.
#
# Subagent inheritance (ASYMMETRIC):
#   GP subagent: inherits parent skills
#   Custom/declarative subagents: do NOT inherit (need own skills=)
#
# Security: 36% prompt injection rate in public skills (agentskills.io).


@dataclass
class SkillMetadata:
    """Level 1: Metadata loaded at startup. ~100 tokens per skill."""

    name: str             # lowercase alphanum + hyphens, 1-64 chars
    description: str      # max 1,024 chars
    allowed_tools: list[str] = field(default_factory=list)
    compatibility: str = ""


@dataclass
class SkillBody:
    """Level 2: Full instructions loaded on activation. <5,000 tokens."""

    metadata: SkillMetadata
    instructions: str     # The full SKILL.md body (<500 lines)
    token_count: int = 0


@dataclass
class SkillResources:
    """Level 3: Supporting files loaded on demand after activation."""

    scripts: dict[str, str] = field(default_factory=dict)
    references: dict[str, str] = field(default_factory=dict)
    assets: dict[str, str] = field(default_factory=dict)


class SkillRegistry:
    """Registry for skills with progressive disclosure.

    Interview points:
    - skills= must point to the PARENT of skill directories (not the skill dir)
    - SKILL.md name must match parent directory name
    - Files > 10 MB are skipped entirely
    - Only GP subagent inherits parent skills
    - 36% of public skills contain prompt injection
    """

    MAX_BODY_TOKENS = 5_000
    MAX_BODY_LINES = 500
    MAX_FILE_SIZE_MB = 10
    MAX_DESCRIPTION_CHARS = 1_024

    def __init__(self):
        self._skills: dict[str, SkillBody] = {}
        self._resources: dict[str, SkillResources] = {}
        self._active: set[str] = set()

    def register(self, body: SkillBody,
                 resources: SkillResources | None = None) -> None:
        """Register a skill. Validates constraints from the spec."""
        meta = body.metadata
        # Validate name: lowercase alphanum + hyphens, 1-64 chars
        if not meta.name or len(meta.name) > 64:
            raise ValueError(f"Skill name must be 1-64 chars: '{meta.name}'")
        if len(meta.description) > self.MAX_DESCRIPTION_CHARS:
            raise ValueError(f"Description exceeds {self.MAX_DESCRIPTION_CHARS} chars")
        if body.token_count > self.MAX_BODY_TOKENS:
            raise ValueError(f"Body exceeds {self.MAX_BODY_TOKENS} tokens")

        self._skills[meta.name] = body
        if resources:
            self._resources[meta.name] = resources

    def get_metadata_for_prompt(self) -> list[dict]:
        """Return all skill metadata for injection into the prompt.

        Level 1: always loaded at startup. ~100 tokens per skill.
        20 skills = ~2,000 tokens in the stable prefix.

        This is the progressive disclosure tradeoff: metadata is cheap
        enough to always include, body is loaded only when needed.
        """
        return [
            {"name": s.metadata.name, "description": s.metadata.description}
            for s in self._skills.values()
        ]

    def activate_skill(self, name: str) -> str | None:
        """Level 2: Load full skill instructions on demand.

        The agent activates a skill by calling read_file on the SKILL.md.
        This loads the full body (<5,000 tokens) into context.
        """
        skill = self._skills.get(name)
        if not skill:
            return None
        self._active.add(name)
        return skill.instructions

    def get_resources(self, name: str) -> SkillResources | None:
        """Level 3: Load supporting resources after activation."""
        if name not in self._active:
            return None  # Must activate before accessing resources
        return self._resources.get(name)

    def get_token_overhead(self) -> dict:
        """Calculate token overhead from skills."""
        metadata_tokens = len(self._skills) * 100  # ~100 tokens per skill
        active_body_tokens = sum(
            self._skills[name].token_count for name in self._active
            if name in self._skills
        )
        return {
            "metadata_tokens": metadata_tokens,
            "active_body_tokens": active_body_tokens,
            "total": metadata_tokens + active_body_tokens,
        }


# =============================================================================
# --- Section 5: Summarization for Context Offloading -------------------------
# =============================================================================
# ContextSummarizerMiddleware compresses old history when context hits 85%.
# Separate from the ContextWindowManager above (which handles the mechanics),
# this section focuses on the summarization strategy and tradeoffs.


class SummarizationStrategy:
    """Configurable summarization for context offloading.

    Interview points:
    - Trigger: 85% of max_input_tokens
    - Retention: ~10% of original content
    - Fallback: truncate to last 170K tokens or 6 messages
    - ContextOverflowError if still exceeds -> re-summarize more aggressively
    - Multimodal limitation: images/audio/video counted but cannot be summarized
    - compact_conversation: model-callable tool to manually trigger summary

    SECURITY: Summarization is LOSSY. Critical instructions from early in
    the conversation may be lost. Put critical instructions in system_prompt
    or memory, NOT in conversation history.
    """

    def __init__(
        self,
        trigger_threshold: float = 0.85,
        retention_ratio: float = 0.10,
        max_tokens: int = 200_000,
        retention_messages: int = 15,
    ):
        self.trigger_threshold = trigger_threshold
        self.retention_ratio = retention_ratio
        self.max_tokens = max_tokens
        self.retention_messages = retention_messages

    def should_summarize(self, current_tokens: int) -> bool:
        """Check if summarization should trigger."""
        return current_tokens >= self.max_tokens * self.trigger_threshold

    def compute_summary(self, messages: list[Message]) -> tuple[Message, list[Message]]:
        """Compute a summary of older messages, preserving recent ones.

        Returns (summary_message, messages_to_keep).

        In production, this sends the old messages to the model for
        summarization. Here we simulate the output.
        """
        # Keep the most recent messages intact
        keep_count = min(self.retention_messages, len(messages))
        old = messages[:-keep_count] if keep_count < len(messages) else []
        recent = messages[-keep_count:]

        if not old:
            return None, messages

        old_tokens = sum(m.token_count for m in old)
        target_tokens = int(old_tokens * self.retention_ratio)

        # Simulate summarization (production uses a model call)
        key_points = []
        for msg in old:
            if msg.content.strip():
                key_points.append(f"- [{msg.role}] {msg.content[:80]}...")

        summary_text = (
            f"[Conversation summary: {len(old)} messages, "
            f"{old_tokens} tokens -> ~{target_tokens} tokens]\n"
            + "\n".join(key_points[:10])
        )

        summary = Message(
            role="system",
            content=summary_text,
            token_count=target_tokens,
            metadata={"is_summary": True, "original_messages": len(old)},
        )

        return summary, recent

    def emergency_truncate(self, messages: list[Message]) -> list[Message]:
        """Fallback when summarization fails: truncate aggressively.

        Keep last 170K tokens or 6 messages, whichever is smaller.
        This is a safety net, not the normal path.
        """
        # By message count
        by_count = messages[-self.FALLBACK_MESSAGE_LIMIT:]

        # By token count
        token_total = 0
        by_tokens = []
        for msg in reversed(messages):
            token_total += msg.token_count
            if token_total > 170_000:
                break
            by_tokens.insert(0, msg)

        # Use whichever is smaller
        return by_count if len(by_count) < len(by_tokens) else by_tokens

    FALLBACK_MESSAGE_LIMIT = 6


# =============================================================================
# --- Section 6: Prompt Caching Strategy --------------------------------------
# =============================================================================
# Deep Agents auto-wires prompt caching. No extra config for the default case.
# The stable prefix (system prompt + tools + skill metadata) is cached.
#
# CRITICAL ordering rule (GitHub issue #1356):
#   Memory middleware MUST go AFTER cache middleware.
#   If memory goes before cache, every memory update invalidates the entire
#   cached prefix, destroying the cost savings.
#
# Middleware wrap order (inside-out):
#   Skills -> Patch -> Profile -> User -> CACHE -> ... -> Summarizer -> HITL -> MEMORY


@dataclass
class CacheConfig:
    """Prompt caching configuration.

    Anthropic cache economics:
      Cache write (first turn):  1.25x base input = $3.75/MTok (Sonnet 4.6)
      Cache read (subsequent):   0.1x base input  = $0.30/MTok
      Default TTL:               5 minutes
      Extended TTL:              up to 1 hour

    Override TTL for slow HITL workflows:
      AnthropicPromptCachingMiddleware(ttl="1h")
    """

    ttl_seconds: int = 300       # Default: 5 minutes
    max_ttl_seconds: int = 3600  # Max: 1 hour
    write_multiplier: float = 1.25
    read_multiplier: float = 0.10
    base_price_per_mtok: float = 3.0  # Sonnet 4.6 input price


@dataclass
class CacheEntry:
    """A cached prompt prefix."""

    prefix_hash: str
    token_count: int
    created_at: float = field(default_factory=time.time)
    hits: int = 0


class PromptCacheManager:
    """Manages prompt caching for the stable prefix.

    What gets cached (the stable prefix):
    - System prompt (custom or @dynamic_prompt output)
    - Built-in Deep Agents base instructions
    - Tool schemas
    - Skill metadata (names + descriptions)
    - Memory (ONLY if placed AFTER cache middleware)

    What invalidates the cache:
    - Prompt text changes
    - Tool schema changes (adding/removing tools)
    - Skill changes (loading a new skill body)
    - Memory changes (if memory is BEFORE cache middleware -- bug #1356)
    - Profile suffix changes
    """

    def __init__(self, config: CacheConfig | None = None):
        self.config = config or CacheConfig()
        self._cache: dict[str, CacheEntry] = {}

    def compute_prefix_hash(self, prefix_components: list[str]) -> str:
        """Hash the stable prefix to detect changes."""
        combined = "\n---\n".join(prefix_components)
        return hashlib.sha256(combined.encode()).hexdigest()[:16]

    def check_cache(self, prefix_hash: str) -> CacheEntry | None:
        """Check if a cached prefix exists and is still valid."""
        entry = self._cache.get(prefix_hash)
        if not entry:
            return None
        # Check TTL
        age = time.time() - entry.created_at
        if age > self.config.ttl_seconds:
            del self._cache[prefix_hash]
            return None
        entry.hits += 1
        return entry

    def write_cache(self, prefix_hash: str, token_count: int) -> CacheEntry:
        """Cache a new prefix (costs 1.25x base price)."""
        entry = CacheEntry(prefix_hash=prefix_hash, token_count=token_count)
        self._cache[prefix_hash] = entry
        return entry

    def estimate_cost(self, prefix_tokens: int, num_turns: int) -> dict:
        """Estimate caching cost for a conversation.

        Worked example from the module (10-call run, Sonnet 4.6):
          Cached:    $0.2229/run ($223/1k)   -- 1 write + 9 reads of 2k prefix
          Uncached:  $0.2700/run ($270/1k)
          Savings:   ~$47/1k (~17%)
        """
        cfg = self.config

        # First turn: cache write
        write_cost = prefix_tokens * cfg.base_price_per_mtok * cfg.write_multiplier / 1_000_000

        # Subsequent turns: cache reads
        read_cost_per_turn = (prefix_tokens * cfg.base_price_per_mtok
                              * cfg.read_multiplier / 1_000_000)
        total_read_cost = read_cost_per_turn * (num_turns - 1)

        # Without caching
        uncached_cost = prefix_tokens * cfg.base_price_per_mtok * num_turns / 1_000_000

        cached_total = write_cost + total_read_cost
        savings = uncached_cost - cached_total

        return {
            "cached_cost": round(cached_total, 6),
            "uncached_cost": round(uncached_cost, 6),
            "savings": round(savings, 6),
            "savings_pct": f"{(savings / uncached_cost * 100):.1f}%" if uncached_cost else "0%",
        }


# =============================================================================
# --- Section 7: @dynamic_prompt (Runtime-Dependent Instructions) -------------
# =============================================================================
# Use @dynamic_prompt when system instructions depend on runtime context
# (user role, access level, feature flags, stored preferences).


@dataclass
class RuntimeContext:
    """Simulates the runtime context available to @dynamic_prompt.

    In Deep Agents, this is accessed via ToolRuntime:
    - runtime.server_info.user -> user identity
    - runtime.context -> context_schema values
    - runtime.execution_info -> run metadata

    context_schema is immutable per-run, not checkpointed, and NOT
    automatically visible to the model.
    """

    user_id: str
    role: str = "viewer"
    org_id: str = ""
    feature_flags: dict = field(default_factory=dict)
    preferences: dict = field(default_factory=dict)


def dynamic_prompt(runtime: RuntimeContext) -> str:
    """Build system prompt based on runtime context.

    This replaces the static system_prompt= parameter when instructions
    must vary by user role, permissions, or feature flags.

    IMPORTANT: @dynamic_prompt output is part of the cached prefix.
    If it returns different text per user, every user gets a separate
    cache entry -- potentially expensive at scale.
    """
    base = "You are an enterprise assistant."

    if runtime.role == "admin":
        base += " You have full administrative access. You may modify settings."
    elif runtime.role == "engineer":
        base += " You may read and write code. Do not modify production configs."
    else:
        base += " You may only read data. Do not attempt any writes."

    # Feature flags
    if runtime.feature_flags.get("beta_search"):
        base += "\nBeta: You have access to the advanced search tool."

    # User preferences
    if runtime.preferences.get("concise"):
        base += "\nThe user prefers concise responses."

    return base


# =============================================================================
# --- Section 8: Putting It All Together -- Context Engineering Pipeline ------
# =============================================================================


class ContextEngineeringPipeline:
    """Full context engineering pipeline combining all components.

    The pipeline demonstrates the five context layers:
    1. Input context: system prompt + memory + skill metadata + tools
    2. Runtime context: hidden config (user_id, flags)
    3. Compression: summarization + offloading
    4. Isolation: subagent delegation (see Module 12)
    5. Long-term memory: AGENTS.md, user preferences
    """

    def __init__(
        self,
        max_tokens: int = 200_000,
        system_prompt: str = "You are a helpful assistant.",
    ):
        self.window_manager = ContextWindowManager(max_tokens=max_tokens)
        self.memory = LongTermMemoryStore()
        self.skills = SkillRegistry()
        self.cache_manager = PromptCacheManager()
        self.summarizer = SummarizationStrategy(max_tokens=max_tokens)
        self.system_prompt = system_prompt

    def build_prompt(self, runtime: RuntimeContext) -> dict:
        """Assemble the full prompt for a model call.

        Prompt assembly order:
        1. System prompt (or @dynamic_prompt)
        2. Memory (AGENTS.md)
        3. Skill metadata (progressive disclosure Level 1)
        4. Tool schemas (every turn)
        5. Conversation history

        Middleware wrap order for caching:
        Skills -> Profile -> User -> CACHE -> Summarizer -> HITL -> MEMORY
        (Memory AFTER cache to prevent invalidation on memory updates)
        """
        # 1. System prompt
        prompt = dynamic_prompt(runtime)

        # 2. Memory
        user_ns = ("default_assistant", runtime.user_id)
        memory_text = self.memory.to_agents_md(user_ns)

        # 3. Skill metadata (Level 1: always loaded)
        skill_meta = self.skills.get_metadata_for_prompt()

        # 4. Stable prefix for caching
        prefix_components = [prompt, memory_text, json.dumps(skill_meta)]
        prefix_hash = self.cache_manager.compute_prefix_hash(prefix_components)
        cache_hit = self.cache_manager.check_cache(prefix_hash)

        if not cache_hit:
            # Cache miss: write new cache entry
            prefix_tokens = len(prompt) // 4 + len(memory_text) // 4 + len(skill_meta) * 100
            self.cache_manager.write_cache(prefix_hash, prefix_tokens)

        # 5. Conversation history
        history = self.window_manager.get_context_for_model()

        return {
            "system_prompt": prompt,
            "memory": memory_text,
            "skill_metadata": skill_meta,
            "history": history,
            "cache_hit": cache_hit is not None,
            "stats": self.window_manager.get_stats(),
        }


# =============================================================================
# --- Demo / Self-Test --------------------------------------------------------
# =============================================================================


def demo():
    """Demonstrate context management, memory, and skills."""
    print("=" * 60)
    print("DEMO: Deep Agents Context, Memory & Skills")
    print("=" * 60)

    # 1. Context window management
    print("\n--- Context Window Management ---")
    cwm = ContextWindowManager(max_tokens=1000)  # Small window for demo

    for i in range(5):
        cwm.add_message(Message(role="user", content=f"Question {i}", token_count=50))
        cwm.add_message(Message(role="assistant", content=f"Answer {i}" * 20,
                                token_count=150))
    print(f"Stats after 5 exchanges: {cwm.get_stats()}")

    # Trigger offloading with a large tool result
    large_result = "Row data\n" * 5000  # Simulate large DB dump
    cwm.add_message(Message(role="tool", content=large_result, token_count=25_000))
    print(f"Stats after large tool result: {cwm.get_stats()}")

    # 2. Conversation buffer (short-term memory)
    print("\n--- Short-Term Memory (Conversation Buffer) ---")
    buffer = ConversationBuffer(thread_id="thread-001")
    buffer.add_user_message("What is our Q3 revenue?", token_count=10)
    buffer.add_assistant_message("Let me look that up for you.", token_count=8)
    buffer.add_tool_result('{"q3_revenue": 2500000}', token_count=15)
    print(f"Buffer stats: {buffer.get_stats()}")

    # 3. Long-term memory
    print("\n--- Long-Term Memory ---")
    memory = LongTermMemoryStore()
    user_ns = ("assistant-1", "user-42")

    memory.store(user_ns, "preferred_format", "concise bullet points")
    memory.store(user_ns, "timezone", "IST (Asia/Kolkata)")
    memory.store(user_ns, "role", "Senior Engineer")

    print(f"Facts stored: {memory.list_facts(user_ns)}")
    print(f"Retrieve timezone: {memory.retrieve(user_ns, 'timezone')}")

    # Background consolidation
    new_facts = [
        {"key": "project", "value": "ML pipeline v2"},
        {"key": "preferred_model", "value": "Sonnet 4.6"},
    ]
    merged = memory.merge_from_conversation(user_ns, new_facts)
    print(f"Consolidated {merged} facts from conversation")

    # Export as AGENTS.md
    agents_md = memory.to_agents_md(user_ns)
    print(f"\nAGENTS.md:\n{agents_md}")

    # 4. Skills (progressive disclosure)
    print("\n--- Skills (Progressive Disclosure) ---")
    skills = SkillRegistry()

    # Register a skill
    deploy_skill = SkillBody(
        metadata=SkillMetadata(
            name="deploy-to-staging",
            description="Step-by-step staging deployment procedure",
            allowed_tools=["execute", "read_file", "write_file"],
        ),
        instructions=(
            "# Deploy to Staging\n\n"
            "1. Verify Dockerfile builds cleanly\n"
            "2. Run integration tests\n"
            "3. Push to staging registry\n"
            "4. Apply k8s manifests\n"
            "5. Verify health checks pass\n"
        ),
        token_count=200,
    )
    skills.register(deploy_skill)

    review_skill = SkillBody(
        metadata=SkillMetadata(
            name="code-review",
            description="Structured code review checklist for PR reviews",
            allowed_tools=["read_file", "grep"],
        ),
        instructions="# Code Review\n1. Check for bugs...\n2. Check style...\n",
        token_count=150,
    )
    skills.register(review_skill)

    # Level 1: Metadata always loaded
    print(f"Skill metadata (always loaded): {skills.get_metadata_for_prompt()}")
    print(f"Token overhead: {skills.get_token_overhead()}")

    # Level 2: Activate on demand
    instructions = skills.activate_skill("deploy-to-staging")
    print(f"\nActivated skill body:\n{instructions}")
    print(f"Token overhead after activation: {skills.get_token_overhead()}")

    # 5. Prompt caching
    print("\n--- Prompt Caching ---")
    cache_mgr = PromptCacheManager()
    cost = cache_mgr.estimate_cost(prefix_tokens=2000, num_turns=10)
    print(f"Cache cost estimate (2K prefix, 10 turns):")
    print(f"  Cached:   ${cost['cached_cost']:.4f}/run")
    print(f"  Uncached: ${cost['uncached_cost']:.4f}/run")
    print(f"  Savings:  ${cost['savings']:.4f} ({cost['savings_pct']})")

    # 6. @dynamic_prompt
    print("\n--- Dynamic Prompt ---")
    admin_ctx = RuntimeContext(user_id="admin-1", role="admin",
                               feature_flags={"beta_search": True})
    viewer_ctx = RuntimeContext(user_id="viewer-1", role="viewer",
                                preferences={"concise": True})
    print(f"Admin prompt:\n  {dynamic_prompt(admin_ctx)}")
    print(f"Viewer prompt:\n  {dynamic_prompt(viewer_ctx)}")

    # 7. Full pipeline
    print("\n--- Full Context Engineering Pipeline ---")
    pipeline = ContextEngineeringPipeline(max_tokens=200_000)
    pipeline.memory.store(("default_assistant", "user-42"),
                          "team", "ML Platform")
    pipeline.skills.register(deploy_skill)

    result = pipeline.build_prompt(admin_ctx)
    print(f"Prompt assembled:")
    print(f"  Cache hit: {result['cache_hit']}")
    print(f"  Skills loaded: {len(result['skill_metadata'])}")
    print(f"  History messages: {len(result['history'])}")

    print("\n" + "=" * 60)
    print("Key interview numbers:")
    print("  5 context layers: input, runtime, compression, isolation, memory")
    print("  20,000 tokens: offload threshold (tool result -> VFS)")
    print("  85%: summarization trigger (% of max_input_tokens)")
    print("  10%: target retention ratio after summarization")
    print("  ~100 tokens/skill: metadata overhead")
    print("  <5,000 tokens / <500 lines: skill body limits")
    print("  36%: prompt injection rate in public skills")
    print("  5 min default / 1 hour max: Anthropic cache TTL")
    print("  1.25x write / 0.1x read: cache price multipliers")
    print("  $223/1k (cached) vs $270/1k (uncached): 10-call run")
    print("  #1356: Memory middleware AFTER cache middleware")
    print("=" * 60)


if __name__ == "__main__":
    demo()
