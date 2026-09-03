"""
Deep Agents: Context Engineering, Memory, Skills & Prompt Caching
=================================================================

Code examples from the consolidated study module covering:
- Production context-managed agent with memory, skills, prompt caching,
  and multi-tenant isolation
- SKILL.md frontmatter structure for incident triage skill
- Context budget monitor for tracking window utilization and cost
"""
from dataclasses import dataclass
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.permissions import FilesystemPermission
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore


# --- Section: Production Context-Managed Agent ---

"""
Production context-engineered agent with memory, skills, prompt caching,
and multi-tenant isolation. Requires: pip install langchain-deepagents
"""


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


# --- Section: SKILL.md Example with Frontmatter ---

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


# --- Section: Context Budget Monitor ---

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
