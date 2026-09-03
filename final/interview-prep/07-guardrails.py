"""
Guardrails (Runtime PDP / Sandbox / Egress) -- Code Examples

Covers:
  - Production guardrail harness with retries, circuit breaker, PII detect/redact/audit,
    hash-pin verify, egress allowlist, sandbox pool, HITL TOCTOU binding, structured logs
  - Layered pipeline with parallel input validation, PII redaction, tool-call gating,
    and audit trail

Source: 07-guardrails.md
"""

# --- Section: Production Guardrail Harness (stdlib, runnable) ---

#!/usr/bin/env python3
"""Runtime guardrails: PDP, sandbox, egress, HITL, PII detect->redact->audit.

Stdlib only. Swap FakePdp / FakeClassifier for Cedar AVP / Bedrock ApplyGuardrail.
Run: python guardrails_harness.py
"""
from __future__ import annotations

import hashlib, json, logging, random, re, threading, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

PDP_FAIL_CLOSED = {"send_email", "shell", "crm.export"}
NICENESS_RAILS = {"topic_brand"}


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for k, d in (("correlation_id", "-"), ("tenant_id", "-"),
                     ("decision", "-"), ("bundle_hash", "-")):
            setattr(record, k, getattr(record, k, d) or d)
        return True


def _log() -> logging.Logger:
    log = logging.getLogger("guardrails")
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"cid":"%(correlation_id)s","tenant":"%(tenant_id)s",'
            '"decision":"%(decision)s","bundle":"%(bundle_hash)s",'
            '"msg":"%(message)s"}'
        ))
        h.addFilter(CorrelationFilter())
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log


LOG = _log()


def retry_with_jitter(fn: Callable, *, attempts: int = 4, base: float = 0.05,
                      cap: float = 1.0):
    """Exponential backoff + full jitter (AWS-style). Raises last error."""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except TransientError as e:
            last = e
            time.sleep(random.uniform(0, min(cap, base * (2 ** i))))
    raise last


class TransientError(Exception):
    pass


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Fail-CLOSED for tools: OPEN denies; it never skips the PEP."""

    def __init__(self, name: str, fail_max: int = 5, cooldown_s: float = 2.0):
        self.name, self.fail_max, self.cooldown_s = name, fail_max, cooldown_s
        self.state = CircuitState.CLOSED
        self.fails = 0
        self.opened_at = 0.0
        self._lock = threading.Lock()

    def allow_probe(self) -> bool:
        with self._lock:
            if self.state is CircuitState.CLOSED:
                return True
            if self.state is CircuitState.OPEN:
                if time.time() - self.opened_at >= self.cooldown_s:
                    self.state = CircuitState.HALF_OPEN
                    return True
                return False
            return True  # half-open: one probe

    def record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self.fails = 0
                self.state = CircuitState.CLOSED
                return
            self.fails += 1
            if self.state is CircuitState.HALF_OPEN or self.fails >= self.fail_max:
                self.state = CircuitState.OPEN
                self.opened_at = time.time()


class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    HITL = "hitl"


@dataclass
class AuditRow:
    correlation_id: str
    tenant_id: str
    action: str
    decision: str
    arg_digest: str
    bundle_hash: str
    pii_types: list
    pii_action: str
    classifier_score: float | None
    sandbox_id: str | None
    human: str | None


AUDIT: list[AuditRow] = []

PII_RE = [
    ("EMAIL", re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")),
    ("PAN", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
]
INVISIBLE = dict.fromkeys(
    list(range(0xE0000, 0xE007F + 1)) + list(range(0xFE00, 0xFE0F + 1))
    + [0x200B, 0x200C, 0x200D, 0x2060],
    None,
)


def strip_invisible(text: str) -> str:
    return text.translate(INVISIBLE)


def pii_detect_redact_audit(text: str, *, cid: str, tenant: str,
                            dest: str) -> tuple[str, list[str], str]:
    """Detect -> redact -> audit. Fail-closed block on tool egress if PAN/SSN."""
    raw_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    types: list[str] = []
    out = text
    for name, rx in PII_RE:
        if rx.search(out):
            types.append(name)
            if name in {"PAN", "SSN"} and dest == "external_mcp":
                raise PermissionError("PII DLP fail-closed on external tool args")
            out = rx.sub(f"[{name}]", out)
    action = "tokenize" if types else "none"
    if dest == "user_chat" and types:
        action = "mask"
    return out, types, action


def tool_surface_hash(tool: dict) -> str:
    canonical = json.dumps(
        {k: tool[k] for k in ("name", "description", "inputSchema", "outputSchema")
         if k in tool},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def approval_binding(principal: str, action: str, args: dict,
                     dest: str, bundle: str, exp: float) -> str:
    body = json.dumps(
        {"p": principal, "a": action, "args": args, "d": dest,
         "b": bundle, "e": exp},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(body.encode()).hexdigest()


class SandboxPool:
    def __init__(self, size: int = 2):
        self._free = list(range(size))
        self._lock = threading.Lock()

    def lease(self) -> int:
        with self._lock:
            if not self._free:
                raise TransientError("sandbox_pool_empty")  # caller -> 503, never host exec
            return self._free.pop()

    def recycle(self, sid: int) -> None:
        with self._lock:
            self._free.append(sid)


EGRESS_ALLOW = {"crm.example.internal", "mail.example.internal"}


def egress_ok(host: str) -> bool:
    return host in EGRESS_ALLOW  # default-deny


@dataclass
class GuardrailHarness:
    bundle_hash: str = "policy-v3"
    pins: dict = field(default_factory=dict)
    pdp_breaker: CircuitBreaker = field(
        default_factory=lambda: CircuitBreaker("pdp"))
    clf_breaker: CircuitBreaker = field(
        default_factory=lambda: CircuitBreaker("classifier"))
    pool: SandboxPool = field(default_factory=SandboxPool)
    hitl_q: dict = field(default_factory=dict)

    def classify(self, text: str) -> float:
        if not self.clf_breaker.allow_probe():
            raise TransientError("classifier_circuit_open")
        def _call():
            if "IGNORE PREVIOUS" in text.upper():
                return 0.92
            return 0.04
        try:
            score = retry_with_jitter(_call)
            self.clf_breaker.record(True)
            return score
        except TransientError:
            self.clf_breaker.record(False)
            raise

    def pdp(self, principal: str, action: str, args: dict,
            score: float) -> Decision:
        if not self.pdp_breaker.allow_probe():
            return Decision.DENY  # stale-deny; NEVER allow-on-open
        def _eval():
            if action in PDP_FAIL_CLOSED and score >= 0.8:
                return Decision.DENY
            if action == "send_email":
                dest = (args.get("to") or "")
                if dest.endswith("@example.internal"):
                    return Decision.HITL
                return Decision.DENY
            if action == "shell":
                return Decision.HITL
            return Decision.ALLOW
        try:
            d = retry_with_jitter(_eval)
            self.pdp_breaker.record(True)
            return d
        except TransientError:
            self.pdp_breaker.record(False)
            return Decision.DENY

    def handle(self, *, tenant, principal, action, args, tool, user_text,
               next_hop_effectful) -> dict:
        cid = str(uuid.uuid4())
        text = strip_invisible(user_text)
        dest = "external_mcp" if action in PDP_FAIL_CLOSED else "user_chat"
        try:
            text, pii_types, pii_act = pii_detect_redact_audit(
                text, cid=cid, tenant=tenant, dest=dest)
        except PermissionError as e:
            return {"status": "refuse", "reason": str(e), "cid": cid}

        pin = tool_surface_hash(tool)
        if self.pins.get(tool["name"]) and self.pins[tool["name"]] != pin:
            return {"status": "refuse", "reason": "tool_hash_mismatch", "cid": cid}
        self.pins.setdefault(tool["name"], pin)

        try:
            score = self.classify(text)
        except TransientError:
            if next_hop_effectful:
                return {"status": "refuse", "reason": "classifier_open_fail_closed"}
            score = 0.0  # niceness fail-open + alert

        decision = self.pdp(principal, action, args, score)

        if decision is Decision.DENY:
            return {"status": "refuse", "reason": "pdp_deny", "cid": cid}

        if decision is Decision.HITL:
            exp = time.time() + 600
            token = approval_binding(principal, action, args,
                                     args.get("to", ""), self.bundle_hash, exp)
            self.hitl_q[token] = {**args, "exp": exp, "principal": principal,
                                  "action": action, "cid": cid}
            return {"status": "input_required", "approval_token": token, "cid": cid}

        host = args.get("host", "crm.example.internal")
        if not egress_ok(host):
            return {"status": "refuse", "reason": "egress_deny", "cid": cid}

        try:
            sid = self.pool.lease()
        except TransientError:
            return {"status": "unavailable", "reason": "sandbox_pool_empty", "cid": cid}
        try:
            result = {"ok": True, "sandbox_id": sid, "echo": text[:80]}
        finally:
            self.pool.recycle(sid)
        return {"status": "ok", "result": result, "cid": cid}

    def resume(self, token: str, *, args_now: dict) -> dict:
        item = self.hitl_q.get(token)
        if not item:
            return {"status": "refuse", "reason": "unknown_token"}
        if time.time() > item["exp"]:
            return {"status": "refuse", "reason": "hitl_expired_fail_closed"}
        expected = approval_binding(item["principal"], item["action"], args_now,
                                    args_now.get("to", ""), self.bundle_hash,
                                    item["exp"])
        if expected != token:
            return {"status": "refuse", "reason": "toctou_hash_mismatch"}
        return self.handle(
            tenant="t1", principal=item["principal"], action=item["action"],
            args=args_now, tool={"name": item["action"], "description": "x",
                                 "inputSchema": {}, "outputSchema": {}},
            user_text="approved", next_hop_effectful=True,
        )


# --- Section: Layered Pipeline with Parallel Execution (Opus-style) ---

"""
Guardrail pipeline: parallel input validation, PII redaction,
tool-call gating, and audit trail.
"""
import re, time, json, hashlib, logging
from enum import Enum
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

class Decision(Enum):
    PASS = "pass"
    BLOCK = "block"
    FLAG = "flag"

@dataclass
class CheckResult:
    layer: str
    decision: Decision
    confidence: float
    latency_ms: float
    details: str = ""

class PIIRedactor:
    PATTERNS = {
        "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "CREDIT_CARD": re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
        "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
        "PHONE": re.compile(r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
        "API_KEY": re.compile(r"\b(?:sk|pk|api|key|token)[-_]?[A-Za-z0-9]{20,}\b", re.I),
    }
    def redact(self, text: str) -> tuple[str, list[str]]:
        found = []
        result = text
        for pii_type, pattern in self.PATTERNS.items():
            for m in reversed(list(pattern.finditer(result))):
                found.append(pii_type)
                result = result[:m.start()] + f"[{pii_type}]" + result[m.end():]
        return result, list(set(found))

class ToolCallGate:
    def __init__(self, allowed: list[str], need_approval: list[str] = None):
        self._allowed = set(allowed)
        self._approval = set(need_approval or [])
        self._pii = PIIRedactor()

    def validate(self, tool: str, args: dict) -> CheckResult:
        start = time.monotonic()
        if tool not in self._allowed:
            return CheckResult("tool_gate", Decision.BLOCK, 1.0,
                               (time.monotonic()-start)*1000, f"Not in allowlist")
        _, pii_types = self._pii.redact(json.dumps(args))
        if pii_types:
            return CheckResult("tool_gate", Decision.BLOCK, 0.95,
                               (time.monotonic()-start)*1000, f"PII: {pii_types}")
        if tool in self._approval:
            return CheckResult("tool_gate", Decision.FLAG, 1.0,
                               (time.monotonic()-start)*1000, "Requires approval")
        return CheckResult("tool_gate", Decision.PASS, 1.0,
                           (time.monotonic()-start)*1000)
