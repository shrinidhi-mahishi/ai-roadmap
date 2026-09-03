"""
Deep Agents: Delegation, Task Planning & Subagents
===================================================

Code examples from the consolidated study module covering:
- Production multi-agent system with hierarchical delegation, async subagents,
  event streaming, and structured output validation
- Delegation decision framework codifying when to delegate vs handle directly
- Restricted subagent permission patterns
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field
from deepagents import create_deep_agent
from deepagents.permissions import FilesystemPermission
from langgraph.checkpoint.memory import MemorySaver


# --- Section: Production Multi-Agent System with Delegation ---

"""
Production multi-agent system with hierarchical delegation, async subagents,
event streaming, and structured output validation.
Requires: pip install langchain-deepagents pydantic
"""


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


# --- Section: Delegation Decision Framework ---

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


# --- Section: Restricted Subagent Permissions ---

# Restricted subagent: read everywhere, write only to /output/
{
    "name": "restricted-writer",
    "permissions": [
        FilesystemPermission(operations=["read"], paths=["/**"]),
        FilesystemPermission(operations=["write"], paths=["/output/**"]),
    ],
}
