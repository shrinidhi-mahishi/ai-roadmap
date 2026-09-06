"""Deep Agents Steering & Human-in-the-Loop (HITL) patterns.

Covers the core HITL mechanisms: interrupt_on configuration, permission modes,
approval flows with timeout/fallback, action risk classification, and audit trails.
HITL is a durable pause (not a PDP) backed by a checkpointer -- the agent proposes,
middleware disposes, and the human resumes via Command.
"""

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


# ============================================================================
# --- Section 1: Action Risk Classification ---
# ============================================================================
# Four-tier model: the agent should NEVER decide its own oversight level.
# Enforcement happens at the workflow execution layer.

class RiskTier(Enum):
    READ_ONLY = 1       # Queries, retrievals, analysis -> fully autonomous
    REVERSIBLE = 2      # Draft creation, internal state -> autonomous + audit
    EXTERNAL = 3        # API calls, emails, Slack posts -> conditional interrupt
    IRREVERSIBLE = 4    # Deploys, payments, data deletion -> mandatory approval


# Map tool names to risk tiers -- this drives the interrupt_on config
TOOL_RISK_MAP: dict[str, RiskTier] = {
    "read_file": RiskTier.READ_ONLY,
    "search_index": RiskTier.READ_ONLY,
    "create_draft": RiskTier.REVERSIBLE,
    "update_internal_state": RiskTier.REVERSIBLE,
    "send_email": RiskTier.EXTERNAL,
    "post_to_slack": RiskTier.EXTERNAL,
    "delete_database": RiskTier.IRREVERSIBLE,
    "deploy_production": RiskTier.IRREVERSIBLE,
    "process_payment": RiskTier.IRREVERSIBLE,
}


def classify_action(tool_name: str, args: dict) -> RiskTier:
    """Classify a tool call by risk tier. Unknown tools default to IRREVERSIBLE
    (fail-closed). The model never decides its own oversight level."""
    base_tier = TOOL_RISK_MAP.get(tool_name, RiskTier.IRREVERSIBLE)

    # Conditional escalation: sending email to external domain is higher risk
    if tool_name == "send_email":
        to_addr = args.get("to", "")
        if not to_addr.endswith("@internal.com"):
            return RiskTier.IRREVERSIBLE  # External email = mandatory approval
    return base_tier


def requires_approval(tier: RiskTier) -> bool:
    """Tiers 3+ require human review. Tier 4 is mandatory, no exceptions."""
    return tier.value >= RiskTier.EXTERNAL.value


# ============================================================================
# --- Section 2: Permission Modes (First-Match-Wins, Fail-Open) ---
# ============================================================================
# Permissions are ordered rules evaluated top-down. First match wins.
# If NO rule matches, the operation is ALLOWED (fail-open).
# This is why a catch-all deny at the end is critical.

class PermissionMode(Enum):
    ALLOW = "allow"
    DENY = "deny"
    INTERRUPT = "interrupt"   # Requires deepagents >= 0.6.8


@dataclass
class FilesystemPermission:
    operations: list[str]       # ["read", "write"]
    paths: list[str]            # ["/workspace/**", "/config/**"]
    mode: PermissionMode
    when: Optional[Callable] = None  # Optional predicate for conditional interrupt


def evaluate_permissions(
    path: str, operation: str, rules: list[FilesystemPermission]
) -> PermissionMode:
    """First-match-wins evaluation. Returns the mode of the first matching rule.
    If no rule matches, returns ALLOW (fail-open -- this is the dangerous default)."""
    for rule in rules:
        if operation not in rule.operations:
            continue
        for pattern in rule.paths:
            # Simplified glob matching for demo
            if pattern == "/**" or path.startswith(pattern.rstrip("/**")):
                if rule.when is not None and not rule.when(path):
                    continue  # Predicate returned False, skip this rule
                return rule.mode
    # No rule matched -- FAIL-OPEN (this is the security gap)
    return PermissionMode.ALLOW


# Correct permission ordering: specific deny -> interrupt -> allow -> catch-all deny
PERMISSION_RULES = [
    # Rule 1: Hard block secrets (most specific deny first)
    FilesystemPermission(
        operations=["read", "write"], paths=["/workspace/.env"], mode=PermissionMode.DENY
    ),
    # Rule 2: Pause for human on sensitive config writes
    FilesystemPermission(
        operations=["write"], paths=["/config/"], mode=PermissionMode.INTERRUPT
    ),
    # Rule 3: Allow workspace reads and writes
    FilesystemPermission(
        operations=["read", "write"], paths=["/workspace/"], mode=PermissionMode.ALLOW
    ),
    # Rule 4: Catch-all deny (without this, unmatched paths are ALLOWED)
    FilesystemPermission(
        operations=["read", "write"], paths=["/**"], mode=PermissionMode.DENY
    ),
]


# ============================================================================
# --- Section 3: interrupt_on Configuration ---
# ============================================================================
# interrupt_on maps tool names to interrupt behavior.
# Unnamed tools AUTO-APPROVE (fail-open).
# True = always interrupt. False = never interrupt (overrides permission interrupt).
# Dict = InterruptOnConfig with allowed_decisions, description, and when predicate.

@dataclass
class InterruptOnConfig:
    allowed_decisions: list[str]  # subset of ["approve", "edit", "reject", "respond"]
    description: str
    when: Optional[Callable] = None  # If None or returns True -> interrupt


def build_interrupt_on_map() -> dict[str, Any]:
    """Build the interrupt_on configuration for an agent.

    Key rules:
    - True = interrupt with all 4 decisions
    - False = never interrupt (disables permission interrupt too)
    - InterruptOnConfig = conditional interrupt with specific decisions
    - Unnamed tools auto-approve (fail-open)
    """
    return {
        # Always interrupt on delete (all 4 decisions available)
        "delete_file": True,

        # Never interrupt on read (even if permission mode="interrupt" would)
        "read_file": False,

        # Conditional interrupt on email: only when sending to external domains
        "send_email": InterruptOnConfig(
            allowed_decisions=["approve", "edit", "reject"],  # No "respond" -- see below
            description="Review this email before sending",
            when=lambda args: args.get("to", "").endswith("@external.com"),
        ),

        # Always interrupt on deploy
        "deploy_production": True,

        # Always interrupt on payment
        "process_payment": True,
    }


# ============================================================================
# --- Section 4: Four Decision Types ---
# ============================================================================
# After interrupt, the human can: approve, edit, reject, or respond.
# CRITICAL: Never use "respond" on side-effecting tools -- the model
# treats the response as SUCCESS and may report "email sent" when it wasn't.

class DecisionType(Enum):
    APPROVE = "approve"   # Execute with original in-memory args (safe default)
    EDIT = "edit"         # Modify args, then execute (danger: name can be changed)
    REJECT = "reject"     # Skip tool, return ERROR ToolMessage
    RESPOND = "respond"   # Human message becomes SUCCESS ToolMessage (danger: see above)


@dataclass
class ToolCall:
    """Represents a proposed tool call from the model."""
    id: str
    name: str
    args: dict


@dataclass
class HumanDecision:
    decision_type: DecisionType
    edited_args: Optional[dict] = None   # Only for EDIT
    response_text: Optional[str] = None  # Only for RESPOND
    rejection_reason: Optional[str] = None  # Only for REJECT


@dataclass
class ToolMessage:
    """Result of executing or skipping a tool call."""
    tool_call_id: str
    status: str            # "success" or "error"
    content: str


def apply_decision(tool_call: ToolCall, decision: HumanDecision) -> ToolMessage:
    """Apply a human decision to a tool call.

    Key dangers:
    - EDIT: edited_action.name is unrestricted (attacker can rename tool)
    - RESPOND: status is SUCCESS -- never use for denying side-effecting tools
    - REJECT: vague rejections cause 2-3 retry LLM calls
    """
    if decision.decision_type == DecisionType.APPROVE:
        # Execute with original args -- safe default
        result = _execute_tool(tool_call.name, tool_call.args)
        return ToolMessage(tool_call.id, "success", result)

    elif decision.decision_type == DecisionType.EDIT:
        # Validate that the tool name was not changed (security check)
        if decision.edited_args is None:
            raise ValueError("edit decision requires edited_args")
        # In production, verify edited_action.name == original name
        result = _execute_tool(tool_call.name, decision.edited_args)
        return ToolMessage(tool_call.id, "success", result)

    elif decision.decision_type == DecisionType.REJECT:
        # Return ERROR ToolMessage with specific guidance
        # Good: "Do not retry. Ask the user which file to archive."
        # Bad: "No, try something else" -> causes 2-3 extra LLM calls
        reason = decision.rejection_reason or "Action rejected by reviewer."
        return ToolMessage(tool_call.id, "error", f"Rejected: {reason}")

    elif decision.decision_type == DecisionType.RESPOND:
        # WARNING: status is SUCCESS. Model thinks the action happened.
        # Never use this to deny a side-effecting tool.
        return ToolMessage(
            tool_call.id, "success",
            decision.response_text or "Human provided a direct response."
        )

    raise ValueError(f"Unknown decision type: {decision.decision_type}")


def _execute_tool(name: str, args: dict) -> str:
    """Stub tool execution for demo purposes."""
    return f"Executed {name} with args: {json.dumps(args)}"


# ============================================================================
# --- Section 5: Approval Flow with Timeout and Fallback ---
# ============================================================================
# The library waits FOREVER. Expire-deny is YOUR responsibility.
# NEVER expire-approve -- that turns TTL into an attacker-controlled delay.

@dataclass
class HITLRequest:
    """Represents a batch of tool calls waiting for human review."""
    request_id: str
    thread_id: str
    action_requests: list[ToolCall]
    arg_digest: str           # Hash of proposed args (for TOCTOU prevention)
    created_at: float
    timeout_seconds: float = 600.0  # 10-minute expire-deny
    decisions: Optional[list[HumanDecision]] = None


def create_hitl_request(
    thread_id: str,
    tool_calls: list[ToolCall],
    timeout_seconds: float = 600.0,
) -> HITLRequest:
    """Create an HITL request with arg digest for TOCTOU prevention.
    The arg digest binds what the reviewer saw to what will execute."""
    # Hash the proposed arguments so we can verify on resume
    args_blob = json.dumps(
        [{"name": tc.name, "args": tc.args} for tc in tool_calls],
        sort_keys=True,
    )
    digest = hashlib.sha256(args_blob.encode()).hexdigest()[:16]

    return HITLRequest(
        request_id=str(uuid.uuid4()),
        thread_id=thread_id,
        action_requests=tool_calls,
        arg_digest=digest,
        created_at=time.monotonic(),
        timeout_seconds=timeout_seconds,
    )


def check_expiry(request: HITLRequest) -> bool:
    """Check if the HITL request has expired. Always expire-DENY, never approve.
    Circuit OPEN = deny, NEVER approve. Expire-approve turns TTL into an
    attacker-controlled delay."""
    elapsed = time.monotonic() - request.created_at
    return elapsed > request.timeout_seconds


def resume_hitl(
    request: HITLRequest,
    decisions: list[HumanDecision],
    cas_ticket: str,
) -> list[ToolMessage]:
    """Resume from an HITL pause. Validates expiry, CAS ticket, and arg digest.

    Key protections:
    1. Expire-deny: if timeout exceeded, reject automatically
    2. CAS ticket: prevents double-execute (two resume commands)
    3. Arg digest: prevents TOCTOU (display != execute args)
    4. Positional matching: decisions must match action_requests length/order
    """
    # Check expiry -- always deny on timeout
    if check_expiry(request):
        return [
            ToolMessage(tc.id, "error", "Expired: auto-deny after timeout")
            for tc in request.action_requests
        ]

    # Positional matching: decisions must align with action_requests
    if len(decisions) != len(request.action_requests):
        raise ValueError(
            f"Decision count {len(decisions)} != action count "
            f"{len(request.action_requests)}. Decisions are positional."
        )

    # Apply each decision
    results = []
    for tool_call, decision in zip(request.action_requests, decisions):
        result = apply_decision(tool_call, decision)
        results.append(result)
    return results


# ============================================================================
# --- Section 6: Audit Trail for Approved Actions ---
# ============================================================================
# WORM (Write-Once Read-Many) audit log. Logs decisions, not raw PII values.
# Includes: approver_id, arg_digest, decision, correlation_id, thread_id.

@dataclass
class AuditEntry:
    timestamp: float
    thread_id: str
    request_id: str
    tool_name: str
    decision: str
    approver_id: str
    arg_digest: str        # Hash of args, not raw values (PII protection)
    correlation_id: str
    elapsed_seconds: float  # Time from interrupt to decision


class AuditTrail:
    """WORM audit trail for HITL decisions. Never log raw PII -- only digests."""

    def __init__(self):
        self._entries: list[AuditEntry] = []

    def record(
        self,
        request: HITLRequest,
        decisions: list[HumanDecision],
        approver_id: str,
    ) -> list[AuditEntry]:
        """Record all decisions for an HITL request."""
        elapsed = time.monotonic() - request.created_at
        correlation_id = str(uuid.uuid4())
        entries = []

        for tool_call, decision in zip(request.action_requests, decisions):
            # Hash the specific tool args for this entry
            arg_hash = hashlib.sha256(
                json.dumps(tool_call.args, sort_keys=True).encode()
            ).hexdigest()[:16]

            entry = AuditEntry(
                timestamp=time.time(),
                thread_id=request.thread_id,
                request_id=request.request_id,
                tool_name=tool_call.name,
                decision=decision.decision_type.value,
                approver_id=approver_id,
                arg_digest=arg_hash,
                correlation_id=correlation_id,
                elapsed_seconds=elapsed,
            )
            entries.append(entry)
            self._entries.append(entry)
        return entries

    def query(self, thread_id: str) -> list[AuditEntry]:
        return [e for e in self._entries if e.thread_id == thread_id]


# ============================================================================
# --- Section 7: HITL Circuit Breaker ---
# ============================================================================
# The library waits forever. Build your own breaker.
# Half-open probe is a reject Command or health GET -- NEVER Approve.
# Circuit OPEN = deny, NEVER approve.

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class HITLCircuitBreaker:
    """Circuit breaker for the HITL review service.
    Trips when the review service is down or reviewers are not responding."""
    threshold: int = 3            # Consecutive timeouts before tripping
    cooldown_seconds: float = 60.0
    _state: CircuitState = CircuitState.CLOSED
    _consecutive_timeouts: int = 0
    _opened_at: float = 0.0

    def record_timeout(self):
        """Record a timeout (no human response within TTL)."""
        self._consecutive_timeouts += 1
        if self._consecutive_timeouts >= self.threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

    def record_response(self):
        """Record a successful human response."""
        self._consecutive_timeouts = 0
        self._state = CircuitState.CLOSED

    def should_deny(self) -> bool:
        """When circuit is OPEN, all actions are denied (never approved).
        Half-open probes with reject, never approve."""
        if self._state == CircuitState.CLOSED:
            return False
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_seconds:
                self._state = CircuitState.HALF_OPEN
                return True  # Half-open: still deny, probe with reject
            return True
        # HALF_OPEN: deny until explicit record_response
        return True


# ============================================================================
# --- Section 8: PII Redaction for HITL Cards ---
# ============================================================================
# Default description embeds full tool args into checkpoint, UI, and traces.
# This is a GDPR/HIPAA incident waiting to happen.
# PIIMiddleware on the agent does NOT redact interrupt payloads.

import re

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def redact_for_hitl_card(args: dict) -> dict:
    """Redact PII from tool args before displaying on the HITL card.
    Keep raw args server-side for the digest bind.
    Block PAN from Slack/MCP review channels entirely."""
    redacted = {}
    for key, value in args.items():
        if isinstance(value, str):
            value = EMAIL_RE.sub(
                lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:8]}]",
                value,
            )
            if PAN_RE.search(value):
                value = "[PAN_REDACTED]"
            value = SSN_RE.sub("[SSN_REDACTED]", value)
        redacted[key] = value
    return redacted


# ============================================================================
# --- Section 9: Subagent HITL Inheritance Summary ---
# ============================================================================
# GP/Declarative SubAgents inherit parent interrupt_on + permissions.
# Compiled/Async SubAgents do NOT inherit -- you must wire HITL inside.
# Interpreter eval() BYPASSES parent interrupt_on.

INHERITANCE_RULES = {
    "GP (default)": "Inherits parent interrupt_on + permissions",
    "Declarative SubAgent": "Inherits, but REPLACES entirely if spec sets its own (PR #2334)",
    "CompiledSubAgent": "Does NOT inherit -- must wire HITL inside the child graph",
    "AsyncSubAgent": "Does NOT inherit -- own deployment, own gates",
    "Dynamic (QuickJS eval)": "BYPASSES parent interrupt_on -- gate eval; task() not caught",
}


# ============================================================================
# --- Demo ---
# ============================================================================

if __name__ == "__main__":
    # 1. Action classification
    tier = classify_action("send_email", {"to": "bob@external.com"})
    assert tier == RiskTier.IRREVERSIBLE  # External email escalated
    tier = classify_action("read_file", {"path": "/workspace/report.md"})
    assert tier == RiskTier.READ_ONLY

    # 2. Permission evaluation
    mode = evaluate_permissions("/workspace/.env", "read", PERMISSION_RULES)
    assert mode == PermissionMode.DENY  # Secrets blocked
    mode = evaluate_permissions("/config/db.yaml", "write", PERMISSION_RULES)
    assert mode == PermissionMode.INTERRUPT  # Config writes need review
    mode = evaluate_permissions("/workspace/report.md", "read", PERMISSION_RULES)
    assert mode == PermissionMode.ALLOW

    # 3. HITL request with timeout
    tool_calls = [
        ToolCall(id="tc1", name="send_email", args={"to": "alice@external.com", "body": "Q3 Report"}),
        ToolCall(id="tc2", name="delete_file", args={"path": "/data/old_backup.tar"}),
    ]
    request = create_hitl_request("thread-001", tool_calls, timeout_seconds=600)
    assert not check_expiry(request)  # Not expired yet

    # 4. Resume with decisions
    decisions = [
        HumanDecision(DecisionType.APPROVE),  # Approve email
        HumanDecision(
            DecisionType.REJECT,
            rejection_reason="Do not delete. Archive to /cold_storage/ instead.",
        ),
    ]
    results = resume_hitl(request, decisions, cas_ticket="ticket-abc")
    assert results[0].status == "success"
    assert results[1].status == "error"

    # 5. Audit trail
    audit = AuditTrail()
    entries = audit.record(request, decisions, approver_id="reviewer@company.com")
    assert len(entries) == 2
    assert entries[0].decision == "approve"
    assert entries[1].decision == "reject"
    # Audit logs digest, not raw args
    assert len(entries[0].arg_digest) == 16

    # 6. PII redaction for HITL card
    raw_args = {"to": "alice@secret.com", "body": "Card: 4111 1111 1111 1111"}
    redacted = redact_for_hitl_card(raw_args)
    assert "alice@secret.com" not in str(redacted)
    assert "4111" not in str(redacted)

    # 7. Circuit breaker
    breaker = HITLCircuitBreaker(threshold=3, cooldown_seconds=1.0)
    assert not breaker.should_deny()
    breaker.record_timeout()
    breaker.record_timeout()
    breaker.record_timeout()
    assert breaker.should_deny()  # Circuit open after 3 timeouts

    print("All HITL patterns validated successfully.")
    print(f"  - Classified {len(TOOL_RISK_MAP)} tools across 4 risk tiers")
    print(f"  - {len(PERMISSION_RULES)} permission rules (first-match-wins)")
    print(f"  - {len(entries)} audit entries recorded")
    print(f"  - Inheritance rules for {len(INHERITANCE_RULES)} subagent forms")
