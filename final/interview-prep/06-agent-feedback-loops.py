"""
Agent Feedback Loops - Code Examples

Extracted from 06-agent-feedback-loops.md. Covers:
- Production agent loop harness with hop caps, same_action_k detection,
  circuit breaker on the critic, PII detect->redact->audit pipeline,
  origin-tagged untrusted hints, idempotent tool keys, and structured
  logging with correlation IDs.
- Complete feedback loop pipeline (training side) including signal
  capture, preference pair construction, DPO fine-tuning with
  checkpointing, 4-set eval gate, self-reflection loop, training
  circuit breaker, and staged rollout controller.
"""

import hashlib
import json
import logging
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional


# --- Section: Production Agent Loop with Hop Caps, Circuit Breaker, PII Pipeline ---

#!/usr/bin/env python3
"""Agent feedback-loop harness: hop caps, same_action_k, critic fallback.

Stdlib only. Swap FakeOracle / FakeLlm for pytest and a provider SDK.
# Optional: from langgraph.checkpoint.postgres import PostgresSaver
# Optional: from temporalio import activity, workflow
Run: python agent_feedback_loop.py
"""
from __future__ import annotations

import hashlib, json, logging, random, re, threading, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

MAX_TURNS = 10
MAX_REPLANS = 2
SAME_ACTION_WARN = 3
SAME_ACTION_HARD = 5
MEMORY_CAP = 3


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for k, d in (("correlation_id", "-"), ("tenant_id", "-"),
                     ("trial_id", "-"), ("turn", "-")):
            setattr(record, k, getattr(record, k, d))
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("loop")
    if logger.handlers:
        return logger
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter(
        '{"ts":"%(asctime)s","level":"%(levelname)s","cid":"%(correlation_id)s",'
        '"tenant":"%(tenant_id)s","trial":"%(trial_id)s","turn":"%(turn)s",'
        '"msg":"%(message)s"}'
    ))
    h.addFilter(CorrelationFilter())
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    return logger


LOG = configure_logging()


def slog(level: int, msg: str, *, cid: str, tenant: str, trial: str = "-",
         turn: int | str = "-", **fields: object) -> None:
    extra = {"correlation_id": cid, "tenant_id": tenant,
             "trial_id": trial, "turn": str(turn)}
    LOG.log(level, "%s %s", msg, json.dumps(fields, default=str), extra=extra)


class TransientError(Exception):
    """429, 5xx, timeout, circuit open -- retry idempotent tools / critic."""


class PermanentError(Exception):
    """4xx auth, policy deny, hop cap -- do not retry."""


class CircuitOpenError(TransientError):
    pass


def retry_with_jitter(
    fn: Callable[[], object], *, cid: str, tenant: str, trial: str, op: str,
    attempts: int = 4, base_s: float = 0.05, cap_s: float = 1.0,
) -> object:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except PermanentError:
            raise
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            sleep = random.uniform(0, min(cap_s, base_s * (2 ** i)))
            slog(logging.WARNING, "retry", cid=cid, tenant=tenant, trial=trial,
                 op=op, attempt=i + 1, sleep_s=round(sleep, 3), err=str(exc))
            time.sleep(sleep)
    assert last is not None
    raise last


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    cooldown_s: float = 15.0
    half_open_probes: int = 1
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _probes_used: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def state(self) -> CircuitState:
        return self._state

    def allow(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._state is CircuitState.OPEN:
                if now - self._opened_at >= self.cooldown_s:
                    self._state = CircuitState.HALF_OPEN
                    self._probes_used = 0
                else:
                    raise CircuitOpenError(f"circuit_open:{self.name}")
            if self._state is CircuitState.HALF_OPEN:
                if self._probes_used >= self.half_open_probes:
                    raise CircuitOpenError(f"circuit_half_open_busy:{self.name}")
                self._probes_used += 1

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = CircuitState.CLOSED
            self._probes_used = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is CircuitState.HALF_OPEN or \
               self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()


EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9.-]+")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
PHONE_RE = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


@dataclass
class RedactionResult:
    text: str
    types: dict[str, int]
    pre_sha: str
    post_sha: str

    @property
    def hit(self) -> bool:
        return bool(self.types)


class AuditSink:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._lock = threading.Lock()

    def write(self, row: dict) -> None:
        with self._lock:
            self.rows.append(dict(row))


class PiiPipeline:
    """Detect -> redact -> audit. Never logs raw values."""

    def __init__(self, audit: AuditSink) -> None:
        self.audit = audit

    def redact(self, text: str) -> RedactionResult:
        pre = hashlib.sha256(text.encode()).hexdigest()
        types = {n: len(rx.findall(text)) for n, rx in
                 (("EMAIL", EMAIL_RE), ("SSN", SSN_RE),
                  ("PHONE", PHONE_RE), ("PAN", PAN_RE))}
        types = {k: v for k, v in types.items() if v}

        def tok(prefix: str, m: re.Match[str]) -> str:
            return f"[{prefix}_{hashlib.sha256(m.group(0).encode()).hexdigest()[:12]}]"

        out = EMAIL_RE.sub(lambda m: tok("EMAIL", m), text)
        out = SSN_RE.sub(lambda m: tok("SSN", m), out)
        out = PHONE_RE.sub(lambda m: tok("PHONE", m), out)
        out = PAN_RE.sub(lambda m: tok("PAN", m), out)
        return RedactionResult(out, types, pre,
                               hashlib.sha256(out.encode()).hexdigest())

    def apply(self, text: str, **meta: str) -> RedactionResult:
        result = self.redact(text)
        self.audit.write({
            "type": "pii_decision", "ts": time.time(), **meta,
            "pre_sha": result.pre_sha, "post_sha": result.post_sha,
            "types": result.types,
            "action": "tokenize" if result.hit else "none",
            "detector": "regex",
        })
        return result


@dataclass
class Hint:
    text: str
    origin: str
    oracle_hash: str
    untrusted: bool
    actor: str


@dataclass
class LoopState:
    goal: str
    allowlist: frozenset[str]
    s_ref: frozenset[str]
    plan: list[str] = field(default_factory=list)
    past_steps: list[str] = field(default_factory=list)
    memory: list[Hint] = field(default_factory=list)
    turns: int = 0
    replans: int = 0
    action_counts: dict[str, int] = field(default_factory=dict)
    last_obs_hash: str = ""
    status: str = "running"


class FakeOracle:
    """Deterministic verifier. $0 model. Prefer this over any critic."""

    def __init__(self, pass_on_turn: int = 2) -> None:
        self.pass_on_turn = pass_on_turn

    def verdict(self, state: LoopState) -> tuple[bool, str]:
        logs = (f"tests={'PASS' if state.turns >= self.pass_on_turn else 'FAIL'}"
                f" turn={state.turns}")
        return state.turns >= self.pass_on_turn, logs


class FakeCritic:
    """Oracle-log critic. Raises TransientError to exercise the breaker."""

    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def reflect(self, logs: str) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TransientError("critic_429")
        return f"hint: retry with a different tool; logs={logs[:80]}"


def action_hash(tool: str, args: str) -> str:
    return hashlib.sha256(f"{tool}|{args}".encode()).hexdigest()[:16]


def pep_allows(state: LoopState, tool: str) -> bool:
    return tool in state.allowlist and tool in state.s_ref


class AgentLoop:
    def __init__(self, oracle: FakeOracle, critic: FakeCritic,
                 pii: PiiPipeline, critic_breaker: CircuitBreaker,
                 audit: AuditSink) -> None:
        self.oracle = oracle
        self.critic = critic
        self.pii = pii
        self.critic_breaker = critic_breaker
        self.audit = audit

    def run(self, *, goal: str, tenant: str, cid: str,
            allowlist: frozenset[str],
            s_ref: frozenset[str]) -> LoopState:
        trial = uuid.uuid4().hex[:12]
        state = LoopState(goal=goal, allowlist=allowlist, s_ref=s_ref,
                          plan=["lookup", "act"])
        slog(logging.INFO, "trial_start", cid=cid, tenant=tenant,
             trial=trial, max_turns=MAX_TURNS, max_replans=MAX_REPLANS)
        while state.status == "running":
            if state.turns >= MAX_TURNS:
                state.status = "refuse_max_turns"
                slog(logging.ERROR, "max_turns", cid=cid, tenant=tenant,
                     trial=trial, turn=state.turns)
                break
            state.turns += 1
            tool = state.plan[0] if state.plan else "lookup"
            if not pep_allows(state, tool):
                state.status = "refuse_pep"
                slog(logging.ERROR, "pep_block", cid=cid, tenant=tenant,
                     trial=trial, turn=state.turns, tool=tool)
                break
            key = action_hash(tool, f"trial={trial}")
            state.action_counts[key] = state.action_counts.get(key, 0) + 1
            n = state.action_counts[key]
            if n >= SAME_ACTION_HARD:
                state.status = "refuse_same_action"
                slog(logging.ERROR, "same_action_hard", cid=cid,
                     tenant=tenant, trial=trial, turn=state.turns, n=n)
                break
            if n >= SAME_ACTION_WARN:
                slog(logging.WARNING, "same_action_warn", cid=cid,
                     tenant=tenant, trial=trial, turn=state.turns, n=n)

            def _exec() -> str:
                return f"obs:{tool}:ok:{key}"

            obs = retry_with_jitter(_exec, cid=cid, tenant=tenant,
                                    trial=trial, op=f"tool:{tool}")
            state.past_steps.append(str(obs))
            ok, logs = self.oracle.verdict(state)
            self.audit.write({
                "type": "oracle_verdict", "ts": time.time(), "cid": cid,
                "tenant": tenant, "trial": trial, "ok": ok,
                "logs_sha": hashlib.sha256(logs.encode()).hexdigest(),
            })
            if ok:
                state.status = "pass"
                slog(logging.INFO, "oracle_pass", cid=cid, tenant=tenant,
                     trial=trial, turn=state.turns)
                break
            hint = self._critic_fallback(
                logs, cid=cid, tenant=tenant, trial=trial, turn=state.turns)
            if hint is None:
                state.status = "refuse_skip_critic"
                slog(logging.ERROR, "refuse_after_skip_critic", cid=cid,
                     tenant=tenant, trial=trial, turn=state.turns)
                break
            redacted = self.pii.apply(
                hint, cid=cid, tenant=tenant, trial=trial,
                origin="critic", actor="orchestrator",
            )
            if redacted.types.get("PAN"):
                state.status = "refuse_pii"
                slog(logging.ERROR, "pii_block_from_store", cid=cid,
                     tenant=tenant, trial=trial, turn=state.turns)
                break
            state.memory.append(Hint(
                text=redacted.text, origin="critic",
                oracle_hash=hashlib.sha256(
                    logs.encode()).hexdigest()[:16],
                untrusted=True, actor="orchestrator",
            ))
            state.memory = state.memory[-MEMORY_CAP:]
            self.audit.write({
                "type": "lesson_write", "ts": time.time(), "cid": cid,
                "tenant": tenant, "trial": trial, "origin": "critic",
                "actor": "orchestrator", "untrusted": True,
                "oracle_hash": state.memory[-1].oracle_hash,
                "post_sha": redacted.post_sha,
            })
            state.replans += 1
            if state.replans > MAX_REPLANS:
                state.status = "refuse_max_replans"
                slog(logging.ERROR, "max_replans", cid=cid,
                     tenant=tenant, trial=trial, turn=state.turns,
                     replans=state.replans)
                break
            if len(state.plan) > 1:
                state.plan = state.plan[1:] + state.plan[:1]
        slog(logging.INFO, "trial_end", cid=cid, tenant=tenant,
             trial=trial, status=state.status, turns=state.turns,
             replans=state.replans, hints=len(state.memory),
             breaker=self.critic_breaker.state.value)
        return state

    def _critic_fallback(self, logs: str, *, cid: str, tenant: str,
                         trial: str, turn: int) -> str | None:
        """oracle critic -> skip critic -> caller refuses."""
        try:
            self.critic_breaker.allow()

            def _call() -> str:
                return self.critic.reflect(logs)

            text = str(retry_with_jitter(
                _call, cid=cid, tenant=tenant, trial=trial, op="critic"))
            self.critic_breaker.record_success()
            slog(logging.INFO, "critic_ok", cid=cid, tenant=tenant,
                 trial=trial, turn=turn)
            return text
        except (TransientError, PermanentError) as exc:
            self.critic_breaker.record_failure()
            slog(logging.WARNING, "critic_skip", cid=cid, tenant=tenant,
                 trial=trial, turn=turn, err=str(exc),
                 breaker=self.critic_breaker.state.value)
            return None


def main() -> None:
    audit = AuditSink()
    pii = PiiPipeline(audit)
    loop = AgentLoop(
        oracle=FakeOracle(pass_on_turn=2),
        critic=FakeCritic(fail_times=1),
        pii=pii,
        critic_breaker=CircuitBreaker("critic"),
        audit=audit,
    )
    cid = uuid.uuid4().hex
    state = loop.run(
        goal="resolve ticket", tenant="acme", cid=cid,
        allowlist=frozenset({"lookup", "act"}),
        s_ref=frozenset({"lookup", "act"}),
    )
    assert state.status == "pass", state.status
    assert state.turns == 2, state.turns
    assert any(r["type"] == "lesson_write" for r in audit.rows)
    assert any(r["type"] == "pii_decision" for r in audit.rows)
    refuse = AgentLoop(
        oracle=FakeOracle(pass_on_turn=99),
        critic=FakeCritic(fail_times=99),
        pii=pii,
        critic_breaker=CircuitBreaker(
            "critic", failure_threshold=1, cooldown_s=60),
        audit=audit,
    ).run(
        goal="resolve ticket", tenant="acme", cid=cid,
        allowlist=frozenset({"lookup"}),
        s_ref=frozenset({"lookup"}),
    )
    assert refuse.status in {"refuse_skip_critic", "refuse_max_turns",
                             "refuse_same_action", "refuse_max_replans"}
    print(json.dumps({"pass_status": state.status,
                      "refuse_status": refuse.status,
                      "audit_rows": len(audit.rows)}, indent=2))


# --- Section: Complete Feedback Loop Pipeline (Training Side) ---

"""
Production feedback loop: captures signals, constructs preference pairs,
runs DPO fine-tuning with checkpointing, and gates deployment with 4-set eval.
"""

import json
import time
import logging
import hashlib
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("feedback_loop")


# -- Signal types --

class SignalType(Enum):
    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"
    USER_EDIT = "user_edit"
    REGENERATION = "regeneration"
    SESSION_ABANDON = "session_abandon"
    TASK_COMPLETE = "task_complete"


@dataclass
class FeedbackSignal:
    trace_id: str
    signal_type: SignalType
    original_output: str
    corrected_output: Optional[str]  # present for USER_EDIT
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    user_id: str = ""
    metadata: dict = field(default_factory=dict)

    def to_preference_pair(self) -> Optional[dict]:
        """Convert feedback signal to a DPO preference pair."""
        if self.signal_type == SignalType.USER_EDIT and self.corrected_output:
            return {
                "prompt": self.metadata.get("prompt", ""),
                "chosen": self.corrected_output,
                "rejected": self.original_output,
                "source": "user_edit",
                "trace_id": self.trace_id,
                "timestamp": self.timestamp,
            }
        if self.signal_type == SignalType.REGENERATION and self.corrected_output:
            return {
                "prompt": self.metadata.get("prompt", ""),
                "chosen": self.corrected_output,
                "rejected": self.original_output,
                "source": "regeneration",
                "trace_id": self.trace_id,
                "timestamp": self.timestamp,
            }
        return None


# -- Preference pair store --

class PreferencePairStore:
    """In-memory store; swap for Argilla/database in production."""

    def __init__(self):
        self._pairs: list[dict] = []
        self._seen: set[str] = set()

    def add(self, pair: dict) -> bool:
        pair_hash = hashlib.sha256(
            json.dumps(pair, sort_keys=True).encode()
        ).hexdigest()
        if pair_hash in self._seen:
            logger.info("Duplicate pair skipped: %s", pair["trace_id"])
            return False
        self._seen.add(pair_hash)
        self._pairs.append(pair)
        logger.info(
            "Pair added: source=%s trace=%s total=%d",
            pair["source"], pair["trace_id"], len(self._pairs),
        )
        return True

    def export_for_training(self, min_pairs: int = 500) -> list[dict]:
        if len(self._pairs) < min_pairs:
            logger.warning(
                "Only %d pairs available (minimum %d). Skipping export.",
                len(self._pairs), min_pairs,
            )
            return []
        snapshot = list(self._pairs)
        logger.info("Exported %d preference pairs for training.", len(snapshot))
        return snapshot


# -- Self-reflection loop --

class SelfReflectionLoop:
    """Generate-critique-revise loop with bounded iterations."""

    def __init__(self, llm_call, evaluator, max_iterations: int = 3):
        self._llm_call = llm_call
        self._evaluator = evaluator
        self._max_iterations = max_iterations

    def run(self, prompt: str) -> dict:
        best_output = None
        best_score = -1.0

        for iteration in range(self._max_iterations):
            if iteration == 0:
                output = self._llm_call(prompt)
            else:
                critique_prompt = (
                    f"Original prompt: {prompt}\n"
                    f"Previous attempt: {output}\n"
                    f"Critique: {critique}\n"
                    f"Revise the response addressing the critique."
                )
                output = self._llm_call(critique_prompt)

            score = self._evaluator(prompt, output)
            logger.info(
                "Reflection iter=%d score=%.3f", iteration, score
            )

            if score > best_score:
                best_score = score
                best_output = output

            if score >= 0.9:
                logger.info("Score threshold met at iter=%d", iteration)
                return {
                    "output": best_output,
                    "score": best_score,
                    "iterations": iteration + 1,
                }

            critique = self._llm_call(
                f"Critique this response for accuracy and completeness:\n"
                f"Prompt: {prompt}\nResponse: {output}"
            )

        logger.info(
            "Max iterations reached. Returning best (score=%.3f)", best_score
        )
        return {
            "output": best_output,
            "score": best_score,
            "iterations": self._max_iterations,
        }


# -- Circuit breaker for training --

class TrainingCircuitBreaker:
    """Monitors training health and halts on anomalies."""

    def __init__(
        self,
        max_kl_divergence: float = 15.0,
        max_reward_zscore: float = 2.5,
        min_eval_score: float = 0.6,
    ):
        self._max_kl = max_kl_divergence
        self._max_reward_z = max_reward_zscore
        self._min_eval = min_eval_score
        self._reward_history: list[float] = []
        self._halted = False
        self._halt_reason = ""

    def check_step(self, kl_div: float, reward: float, step: int) -> bool:
        """Returns True if training should continue, False if halted."""
        if self._halted:
            return False

        if kl_div > self._max_kl:
            self._halt("KL divergence %.2f exceeds max %.2f at step %d"
                        % (kl_div, self._max_kl, step))
            return False

        self._reward_history.append(reward)
        if len(self._reward_history) >= 10:
            mean = sum(self._reward_history) / len(self._reward_history)
            variance = sum(
                (r - mean) ** 2 for r in self._reward_history
            ) / len(self._reward_history)
            std = variance ** 0.5
            if std > 0:
                z_score = (reward - mean) / std
                if abs(z_score) > self._max_reward_z:
                    self._halt(
                        "Reward z-score %.2f exceeds threshold at step %d "
                        "(possible reward hacking)" % (z_score, step)
                    )
                    return False
        return True

    def check_eval(self, eval_scores: dict) -> bool:
        """Check 4-set evaluation gate."""
        for eval_name, score in eval_scores.items():
            if score < self._min_eval:
                self._halt(
                    "Eval '%s' scored %.3f (below min %.3f)"
                    % (eval_name, score, self._min_eval)
                )
                return False
        logger.info("All eval sets passed: %s", eval_scores)
        return True

    def _halt(self, reason: str):
        self._halted = True
        self._halt_reason = reason
        logger.error("TRAINING HALTED: %s", reason)

    @property
    def status(self) -> dict:
        return {
            "halted": self._halted,
            "reason": self._halt_reason,
            "steps_monitored": len(self._reward_history),
        }


# -- Staged rollout controller --

class RolloutController:
    """Progressive traffic shifting with automatic rollback."""

    STAGES = [
        {"name": "canary", "percent": 1,
         "min_samples": 50, "auto_promote_hours": 24},
        {"name": "early", "percent": 5,
         "min_samples": 200, "auto_promote_hours": 48},
        {"name": "ramp", "percent": 25,
         "min_samples": 500, "auto_promote_hours": 72},
        {"name": "full", "percent": 50,
         "min_samples": 1000, "auto_promote_hours": 168},
    ]

    def __init__(self, quality_threshold: float = 0.85):
        self._stage_idx = 0
        self._quality_threshold = quality_threshold
        self._promoted_at: Optional[float] = None
        self._rolled_back = False

    @property
    def current_stage(self) -> dict:
        if self._rolled_back:
            return {"name": "rolled_back", "percent": 0}
        return self.STAGES[self._stage_idx]

    def record_quality(self, score: float, sample_count: int) -> str:
        stage = self.STAGES[self._stage_idx]

        if score < self._quality_threshold:
            self._rolled_back = True
            logger.error(
                "ROLLBACK at stage '%s': quality %.3f < threshold %.3f",
                stage["name"], score, self._quality_threshold,
            )
            return "rolled_back"

        if sample_count < stage["min_samples"]:
            return "collecting"

        hours_elapsed = 0.0
        if self._promoted_at:
            hours_elapsed = (time.time() - self._promoted_at) / 3600

        if (hours_elapsed >= stage["auto_promote_hours"]
                or self._promoted_at is None):
            if self._stage_idx < len(self.STAGES) - 1:
                self._stage_idx += 1
                self._promoted_at = time.time()
                new_stage = self.STAGES[self._stage_idx]
                logger.info(
                    "Promoted to stage '%s' (%d%% traffic)",
                    new_stage["name"], new_stage["percent"],
                )
                return f"promoted:{new_stage['name']}"
            return "fully_deployed"

        return "waiting"


# -- Full pipeline orchestrator --

class FeedbackLoopPipeline:
    """Orchestrates the complete feedback-to-improvement loop."""

    def __init__(self, llm_call, evaluator):
        self.pair_store = PreferencePairStore()
        self.circuit_breaker = TrainingCircuitBreaker()
        self.rollout = RolloutController()
        self.reflection = SelfReflectionLoop(llm_call, evaluator)

    def ingest_feedback(self, signal: FeedbackSignal):
        pair = signal.to_preference_pair()
        if pair:
            self.pair_store.add(pair)

    def trigger_training(self) -> dict:
        pairs = self.pair_store.export_for_training(min_pairs=500)
        if not pairs:
            return {"status": "insufficient_data"}

        logger.info("Starting DPO training with %d pairs", len(pairs))

        # Simulated training loop with circuit breaker monitoring
        for step in range(100):
            kl_div = 0.5 + step * 0.1   # simulated KL growth
            reward = 0.7 + step * 0.005  # simulated reward
            if not self.circuit_breaker.check_step(kl_div, reward, step):
                return {
                    "status": "halted",
                    "details": self.circuit_breaker.status,
                }

        # 4-set evaluation gate
        eval_scores = {
            "task_holdout": 0.88,
            "capability_drift": 0.92,
            "safety_refusal": 0.95,
            "production_arena": 0.86,
        }
        if not self.circuit_breaker.check_eval(eval_scores):
            return {
                "status": "eval_failed",
                "details": self.circuit_breaker.status,
            }

        logger.info("Training complete. Beginning staged rollout.")
        return {"status": "ready_for_rollout", "eval_scores": eval_scores}


if __name__ == "__main__":
    main()
