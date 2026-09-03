"""
Deep Agents Architecture (create_deep_agent) -- Code Examples

Covers:
  - Production Deep Agent setup with full middleware stack, model tiering,
    durable checkpointing, permissions, HITL, and observability
  - Custom middleware for per-request cost tracking and alerting
  - Harness runtime with fallback chain (Deep Agents -> create_agent -> deterministic refuse),
    circuit breaker, PII detect/redact/audit

Source: 08-deep-agents-architecture.md
"""

# --- Section: Production Deep Agent Setup ---

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


# --- Section: Custom Middleware Example ---

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


# --- Section: Harness Runtime with Fallback Chain (stdlib) ---

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
