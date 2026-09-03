"""
Deep Agents Steering, Human-in-the-Loop & Production

Code examples extracted from 13-deep-agents-steering-and-production.md.
Covers: HITL interrupt configuration, tiered permissions, approval handlers
with escalation/stale-execution guards, a steering runtime with circuit
breaker / PII / CAS, and checkpoint cleanup.

Package pin: deepagents==0.7.12 (PyPI 2026-09-01). Research frozen 2026-09-02.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable
from uuid import uuid4


# --- Section: interrupt_on Mapping ---

# Official Deep Agents example:

interrupt_on_example = {
    "remove_file": True,  # all four decisions
    "fetch_file": False,  # never pause
    "notify_email": {"allowed_decisions": ["approve", "reject"]},  # no edit, no respond
}


# --- Section: Four Decision Types ---

# Resume payload example:

# Command(resume={"decisions": [
#     {"type": "approve"},
#     {"type": "edit", "edited_action": {"name": "...", "args": {...}}},
#     {"type": "reject", "message": "..."},   # message optional
#     {"type": "respond", "message": "..."},  # message required
# ]})


# --- Section: when Predicate ---

def writes_outside_workspace(request) -> bool:
    path = request.tool_call["args"].get("file_path", "")
    return not path.startswith("/workspace/")

interrupt_on_with_when = {
    "write_file": {
        "allowed_decisions": ["approve", "edit", "reject"],
        "when": writes_outside_workspace,
    },
}


# --- Section: Permission Model (First-Match-Wins) ---

# Critical ordering requirement -- specific denies must precede general allows:

# permissions = [
#     # Rule 1: Block secrets (matches first)
#     FilesystemPermission(operations=["read", "write"], paths=["/workspace/.env"], mode="deny"),
#     # Rule 2: Allow workspace (matches second)
#     FilesystemPermission(operations=["read", "write"], paths=["/workspace/**"], mode="allow"),
#     # Rule 3: Block everything else
#     FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="deny"),
# ]


# --- Section: Complete HITL Agent with Interrupt Configuration ---

"""
Production HITL agent with tiered interrupt configuration,
conditional gating, and permission-based filesystem access.
"""

from uuid import uuid4
from dataclasses import dataclass
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import interrupt, Command
from langchain_core.tools import tool


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def read_file(file_path: str) -> str:
    """Read a file from the workspace."""
    with open(file_path, "r") as f:
        return f.read()

@tool
def write_file(file_path: str, content: str) -> str:
    """Write content to a file in the workspace."""
    with open(file_path, "w") as f:
        f.write(content)
    return f"Wrote {len(content)} bytes to {file_path}"

@tool
def delete_file(file_path: str) -> str:
    """Delete a file from the workspace. Irreversible."""
    import os
    os.remove(file_path)
    return f"Deleted {file_path}"

@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email to a recipient."""
    return f"Email sent to {to}: {subject}"

@tool
def request_human_input(question: str) -> str:
    """Ask the human operator a question and wait for their response."""
    response = interrupt({
        "type": "question",
        "question": question,
    })
    return response.get("answer", "No answer provided")


# ---------------------------------------------------------------------------
# Conditional interrupt predicate
# ---------------------------------------------------------------------------

def writes_outside_workspace(request) -> bool:
    """Only interrupt file writes that target paths outside /workspace/."""
    path = request.tool_call["args"].get("file_path", "")
    return not path.startswith("/workspace/")


# ---------------------------------------------------------------------------
# Agent configuration
# ---------------------------------------------------------------------------

@dataclass
class AgentContext:
    user_id: str
    org_id: str
    environment: str  # "staging" or "production"


DB_URI = "postgresql://user:pass@localhost:5432/langgraph_prod"

with PostgresSaver.from_conn_string(DB_URI) as checkpointer:
    from langchain_deepagents import create_deep_agent, FilesystemPermission

    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        tools=[read_file, write_file, delete_file, send_email, request_human_input],

        # Tiered interrupt configuration
        interrupt_on={
            "read_file": False,                           # Tier 1: autonomous
            "write_file": {                               # Tier 2/3: conditional
                "allowed_decisions": ["approve", "edit", "reject"],
                "when": writes_outside_workspace,
            },
            "delete_file": True,                          # Tier 4: always interrupt
            "send_email": {                               # Tier 3: external side effect
                "allowed_decisions": ["approve", "edit", "reject"],
            },
        },

        # Filesystem permissions (first-match-wins)
        permissions=[
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/workspace/.env", "/workspace/.secrets/**"],
                mode="deny",
            ),
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/workspace/**"],
                mode="allow",
            ),
            FilesystemPermission(
                operations=["write"],
                paths=["/config/**"],
                mode="interrupt",
            ),
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/**"],
                mode="deny",
            ),
        ],

        checkpointer=checkpointer,
        context_schema=AgentContext,
    )


# --- Section: Approval Handler with Escalation and Stale-Execution Guard ---

"""
Production approval handler: processes interrupts, applies tiered
escalation, enforces SLAs, prevents stale execution.
"""

import hashlib
import json
import time
from datetime import datetime, timedelta


def compute_action_hash(action_requests: list[dict]) -> str:
    """Deterministic hash of proposed actions for stale-execution detection."""
    canonical = json.dumps(action_requests, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def classify_escalation_tier(action_requests: list[dict]) -> int:
    """Map action requests to escalation tier based on tool risk."""
    tier_map = {"delete_file": 3, "send_email": 2, "write_file": 1, "read_file": 0}
    return max(tier_map.get(ar["tool_name"], 1) for ar in action_requests)


ESCALATION_SLAS = {
    1: timedelta(hours=4),      # Moderate-confidence actions
    2: timedelta(hours=1),      # Low-confidence / high-blast-radius
    3: timedelta(minutes=15),   # Compliance-sensitive
}


def handle_interrupt(result, config: dict, agent, stored_hash: str | None = None):
    """
    Process an interrupt result. Returns the resumed agent result.
    In production, the human decision step is an async queue (Slack, email, web UI).
    """
    if not result.interrupts:
        return result

    action_requests = result.interrupts[0].value["action_requests"]
    current_hash = compute_action_hash(action_requests)

    # Stale execution guard
    if stored_hash and current_hash != stored_hash:
        decisions = [{
            "type": "reject",
            "message": "Action context has changed since original proposal. Re-evaluate.",
        }]
    else:
        tier = classify_escalation_tier(action_requests)
        if tier <= 1:
            decisions = [{"type": "approve"} for _ in action_requests]
        elif tier >= 3:
            decisions = [{
                "type": "reject",
                "message": "Tier 3 action requires explicit human review. "
                           "Queued for on-call approval.",
            } for _ in action_requests]
        else:
            decisions = [{"type": "approve"} for _ in action_requests]

    return agent.invoke(
        Command(resume={"decisions": decisions}),
        config=config,
        version="v2",
    )


# --- Section: Steering Runtime with Circuit Breaker, PII, and CAS ---

#!/usr/bin/env python3
"""HITL steering: pause is not a PDP. Expire-deny, never expire-approve.
Fallback: HITL (human Command) -> deny (reject / TTL) -> refuse (no tool run).
"""
from __future__ import annotations

import hashlib, json, logging, random, re, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
_log = logging.getLogger("steering")

def full_jitter_sleep(attempt: int, base_s: float = 0.05, cap_s: float = 1.0) -> None:
    time.sleep(random.uniform(0, min(cap_s, base_s * (2**attempt))))

def retry_call(fn: Callable[[], Any], *, attempts: int = 3) -> Any:
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except (TimeoutError, ConnectionError, OSError) as exc:
            last = exc
            if i == attempts - 1:
                raise
            full_jitter_sleep(i)
    raise last


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitOpenError(RuntimeError):
    pass

@dataclass
class CircuitBreaker:
    """HITL-service breaker. Half-open probe MUST be deny/health, never approve."""
    name: str
    failure_threshold: int = 5
    cooldown_s: float = 30.0
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0

    def allow(self) -> None:
        if self._state is CircuitState.CLOSED:
            return
        if self._state is CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = CircuitState.HALF_OPEN
                return
            raise CircuitOpenError(self.name)

    def record_success(self) -> None:
        self._failures = 0
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self._failures += 1
        if self._state is CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
ALLOWED = frozenset({"approve", "edit", "reject", "respond"})
SIDE_EFFECT = frozenset({"notify_email", "delete", "refund", "execute"})

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()

def args_digest(args: dict[str, Any]) -> str:
    return _sha(json.dumps(args, sort_keys=True, default=str))

def pii_detect_redact_audit(
    text: str, *, audit: list[dict[str, Any]], correlation_id: str,
    tenant_id: str, sink: str, block_on_pan: bool = True,
) -> str:
    kinds = [n for n, rx in (("email", EMAIL_RE), ("pan", PAN_RE)) if rx.search(text)]
    pre = _sha(text)
    if "pan" in kinds and block_on_pan and sink in {"hitl_ui", "mcp_args", "email_body"}:
        audit.append({"cid": correlation_id, "tenant": tenant_id, "sink": sink,
                      "kinds": kinds, "action": "block", "pre": pre, "post": _sha(""),
                      "detector": "regex"})
        raise PermissionError(f"pii_block:{sink}:pan")
    redacted = EMAIL_RE.sub(
        lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]", text)
    redacted = PAN_RE.sub("[PAN]", redacted)
    audit.append({"cid": correlation_id, "tenant": tenant_id, "sink": sink,
                  "kinds": kinds, "action": "redact" if redacted != text else "allow",
                  "pre": pre, "post": _sha(redacted), "detector": "regex"})
    return redacted


@dataclass
class ActionRequest:
    name: str
    args: dict[str, Any]
    allowed: tuple[str, ...] = ("approve", "edit", "reject", "respond")

@dataclass
class Ticket:
    thread_id: str
    interrupt_id: str
    actions: list[ActionRequest]
    display_digests: list[str]
    status: str = "pending"  # pending | consumed | expired
    created_at: float = field(default_factory=time.monotonic)
    ttl_s: float = 600.0  # 10 min expire-deny

@dataclass
class ResumeResult:
    status: str  # approved | denied | refused
    decisions: list[dict[str, Any]]
    degraded: bool


class SteeringRuntime:
    def __init__(self, *, ttl_s: float = 600.0, now: Callable[[], float] | None = None):
        self.ttl_s = ttl_s
        self._now = now or time.monotonic
        self.tickets: dict[str, Ticket] = {}
        self.breaker = CircuitBreaker("hitl_queue", failure_threshold=3, cooldown_s=30.0)
        self.audit: list[dict[str, Any]] = []
        self.decision_log: list[dict[str, Any]] = []

    def enqueue(self, actions: list[ActionRequest], *, thread_id: str,
                correlation_id: str, tenant_id: str) -> Ticket:
        cards, digests = [], []
        for a in actions:
            raw = json.dumps(a.args, sort_keys=True, default=str)
            cards.append(pii_detect_redact_audit(
                raw, audit=self.audit, correlation_id=correlation_id,
                tenant_id=tenant_id, sink="hitl_ui"))
            digests.append(args_digest(a.args))
        t = Ticket(thread_id=thread_id, interrupt_id=str(uuid.uuid4()),
                   actions=actions, display_digests=digests,
                   ttl_s=self.ttl_s, created_at=self._now())
        self.tickets[t.interrupt_id] = t
        return t

    def _cas(self, ticket: Ticket, to: str) -> bool:
        if ticket.status != "pending":
            return False
        ticket.status = to
        return True

    def deny_decisions(self, n: int, message: str) -> list[dict[str, Any]]:
        return [{"type": "reject", "message": message} for _ in range(n)]

    def resume(self, interrupt_id: str, decisions: list[dict[str, Any]] | None, *,
               approver_id: str, correlation_id: str, tenant_id: str,
               role_can_approve: bool = True) -> ResumeResult:
        ticket = self.tickets.get(interrupt_id)
        if ticket is None:
            return ResumeResult("refused", [], True)
        n = len(ticket.actions)

        def _deny(reason: str, msg: str) -> ResumeResult:
            dec = self.deny_decisions(n, msg)
            if not self._cas(ticket, "expired" if "timeout" in reason else "consumed"):
                return ResumeResult("refused", [], True)
            self.decision_log.append({"reason": reason, "approver_id": approver_id,
                                       "thread_id": ticket.thread_id, "ts": time.time()})
            return ResumeResult("denied", dec, True)

        try:
            self.breaker.allow()
        except CircuitOpenError:
            return _deny("circuit_open", "HITL service unavailable. Do not retry.")

        if self._now() - ticket.created_at >= ticket.ttl_s:
            self.breaker.record_success()
            return _deny("ttl_timeout", "Approval timed out. Do not retry.")
        if not role_can_approve:
            self.breaker.record_success()
            return _deny("rbac_deny", "Approver not authorized for this tool.")
        if not decisions or len(decisions) != n:
            self.breaker.record_success()
            return _deny("bad_decisions", "Decision count mismatch. Do not retry.")

        for i, (d, action) in enumerate(zip(decisions, ticket.actions)):
            dtype = d.get("type")
            if dtype not in ALLOWED or dtype not in action.allowed:
                return _deny("illegal_type", f"Decision {dtype!r} not allowed.")
            if dtype == "respond" and action.name in SIDE_EFFECT:
                return _deny("respond_forbidden", "respond is not a deny on side-effecting tools.")
            if dtype == "edit" and (d.get("edited_action") or {}).get("name") != action.name:
                return _deny("rename_forbidden", "edited_action.name must match original.")
            if dtype == "approve" and args_digest(action.args) != ticket.display_digests[i]:
                return _deny("toctou_hash", "Args changed since display. Do not execute.")

        if any(d.get("type") == "approve" for d in decisions) \
                and self.breaker._state is CircuitState.HALF_OPEN:
            self.breaker.record_failure()
            return _deny("half_open_no_approve", "Circuit half-open: deny-only probe.")

        if not self._cas(ticket, "consumed"):
            return ResumeResult("refused", [], True)
        self.breaker.record_success()
        self.decision_log.append({"reason": "human", "approver_id": approver_id,
                                   "thread_id": ticket.thread_id, "ts": time.time()})
        if all(d.get("type") in {"reject", "respond"} for d in decisions):
            return ResumeResult("denied", decisions, False)
        return ResumeResult("approved", decisions, False)


# --- Section: Checkpoint Cleanup Job ---

"""Background job to purge terminal-state checkpoints on a retention schedule."""

import psycopg2
from datetime import datetime, timedelta

DB_URI = "postgresql://lguser:lgpass@localhost:5432/langgraph"
RETENTION_DAYS = 30
TERMINAL_STATUSES = ("completed", "failed", "cancelled")

def cleanup_stale_checkpoints():
    """Delete checkpoints for threads that reached terminal state
    more than RETENTION_DAYS ago. MUST NOT delete threads with pending interrupts."""
    cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
    conn = psycopg2.connect(DB_URI)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM checkpoints
                WHERE thread_id IN (
                    SELECT thread_id FROM threads
                    WHERE status IN %s
                      AND updated_at < %s
                )
            """, (TERMINAL_STATUSES, cutoff))
            deleted = cur.rowcount
            conn.commit()
            print(f"Purged {deleted} checkpoints older than {cutoff.isoformat()}")
    finally:
        conn.close()
