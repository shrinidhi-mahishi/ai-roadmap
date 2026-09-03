"""
Module 02: Fine-Tuning LLMs (SFT / PEFT / Preference / RLVR) -- Code Examples

Extracted from 02-fine-tuning.md. Contains two complete production fine-tuning
pipeline implementations:

  A. Adapter Registry, Canary Controller, and Fallback Chain (Opus-style)
     - Evaluation gating (4-gate: task, forgetting, safety, serve-dtype)
     - Adapter version management with immutable registry
     - Canary deployment controller with auto-rollback
     - Fallback inference chain with circuit breakers
     - Structured logging with structlog

  B. Control Plane + Serve Runtime with Full Jitter, Authz, Lineage (Grok-style)
     - Idempotent job submission keyed by dataset+base+peft+seed+code SHA
     - TransientError/PermanentError distinction
     - Protocol-based generator swapping
     - Fallback: FT adapter -> base model -> deterministic schema
     - Per-dependency circuit breakers (train API, adapter serve, base serve)
     - JSON logs with correlation_id + tenant + job on every line

Key patterns demonstrated across both implementations:
  - Training is a write/control plane; serving is a read/data plane
  - Eval gate is a hard block before promotion
  - Adapter rollback is a pointer flip (tens of MB)
  - Circuit breakers are independent per dependency
  - Identity from verified token, NEVER from model JSON
"""


# --- Section: Production Code A: Adapter Registry, Canary Controller, and Fallback Chain (Opus) ---

"""
Production fine-tuning pipeline with resilience patterns.
Demonstrates: training with checkpointing and spot resilience,
evaluation gating, adapter versioning, canary deployment with
rollback, circuit breakers, and structured logging.
"""

import hashlib
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger()


def correlation_id() -> str:
    return str(uuid.uuid4())[:12]


# --- Circuit Breaker ---

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    half_open_max_calls: int = 2
    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    failure_count: int = field(default=0, init=False)
    last_failure_time: float = field(default=0.0, init=False)
    half_open_calls: int = field(default=0, init=False)

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
                return True
            return False
        return self.half_open_calls < self.half_open_max_calls

    def record_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            self.half_open_calls += 1
            if self.half_open_calls >= self.half_open_max_calls:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
        else:
            self.failure_count = 0

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
        elif self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN


# --- Retry with Backoff + Jitter ---

def retry_with_backoff(
    func,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable: tuple = (ConnectionError, TimeoutError, OSError),
    cid: str = "",
):
    for attempt in range(max_retries + 1):
        try:
            result = func()
            if attempt > 0:
                logger.info("retry.succeeded", attempt=attempt, cid=cid)
            return result
        except retryable as e:
            if attempt == max_retries:
                logger.error("retry.exhausted", attempts=max_retries + 1, error=str(e), cid=cid)
                raise
            delay = random.uniform(0, min(base_delay * (2 ** attempt), max_delay))
            logger.warning("retry.backoff", attempt=attempt + 1, delay_s=round(delay, 2), cid=cid)
            time.sleep(delay)


# --- Evaluation Gate ---

@dataclass
class EvalThresholds:
    """Minimum scores to pass the evaluation gate."""
    task_accuracy: float = 0.85
    mmlu_floor: float = 0.60       # general capability floor
    hellaswag_floor: float = 0.70  # commonsense floor
    safety_pass_rate: float = 0.95  # red-team resistance


@dataclass
class EvalResult:
    task_accuracy: float
    mmlu_score: float
    hellaswag_score: float
    safety_pass_rate: float
    forgetting_delta_mmlu: float   # change from base model score
    forgetting_delta_hellaswag: float

    def passes(self, thresholds: EvalThresholds) -> bool:
        return (
            self.task_accuracy >= thresholds.task_accuracy
            and self.mmlu_score >= thresholds.mmlu_floor
            and self.hellaswag_score >= thresholds.hellaswag_floor
            and self.safety_pass_rate >= thresholds.safety_pass_rate
        )

    @property
    def forgetting_alert(self) -> bool:
        """Alert if general capability dropped significantly."""
        return (
            self.forgetting_delta_mmlu < -0.05
            or self.forgetting_delta_hellaswag < -0.05
        )


# --- Adapter Version Management ---

@dataclass
class AdapterVersion:
    version_id: str
    base_model: str
    adapter_path: str
    training_config: dict
    eval_result: EvalResult
    signature: str   # SHA-256 of adapter weights
    created_at: float
    promoted: bool = False


class AdapterRegistry:
    """Immutable adapter registry with promotion and rollback."""

    def __init__(self):
        self._versions: dict[str, AdapterVersion] = {}
        self._production_version: Optional[str] = None
        self._previous_production: Optional[str] = None

    def register(self, version: AdapterVersion, cid: str = "") -> None:
        if version.version_id in self._versions:
            raise ValueError(f"Version {version.version_id} already exists (immutable)")
        self._versions[version.version_id] = version
        logger.info(
            "adapter.registered",
            version=version.version_id,
            task_acc=version.eval_result.task_accuracy,
            mmlu=version.eval_result.mmlu_score,
            cid=cid,
        )

    def promote(
        self,
        version_id: str,
        thresholds: EvalThresholds,
        cid: str = "",
    ) -> bool:
        version = self._versions.get(version_id)
        if not version:
            raise KeyError(f"Version {version_id} not found")

        if not version.eval_result.passes(thresholds):
            logger.warning(
                "adapter.promotion_rejected",
                version=version_id,
                reason="eval_below_threshold",
                cid=cid,
            )
            return False

        if version.eval_result.forgetting_alert:
            logger.warning(
                "adapter.forgetting_detected",
                version=version_id,
                mmlu_delta=version.eval_result.forgetting_delta_mmlu,
                hellaswag_delta=version.eval_result.forgetting_delta_hellaswag,
                cid=cid,
            )

        self._previous_production = self._production_version
        self._production_version = version_id
        version.promoted = True
        logger.info("adapter.promoted", version=version_id, cid=cid)
        return True

    def rollback(self, cid: str = "") -> Optional[str]:
        if not self._previous_production:
            logger.error("adapter.rollback_failed", reason="no_previous_version", cid=cid)
            return None
        rolled_back_from = self._production_version
        self._production_version = self._previous_production
        self._previous_production = None
        logger.info(
            "adapter.rolled_back",
            from_version=rolled_back_from,
            to_version=self._production_version,
            cid=cid,
        )
        return self._production_version

    @property
    def production_version(self) -> Optional[str]:
        return self._production_version


# --- Canary Deployment Controller ---

@dataclass
class CanaryConfig:
    initial_traffic_pct: float = 5.0
    ramp_step_pct: float = 10.0
    ramp_interval_seconds: float = 3600.0  # 1 hour between ramps
    quality_threshold: float = 0.85
    rollback_on_degradation: bool = True


class CanaryController:
    """
    Routes traffic between production and canary adapter.
    Monitors quality. Auto-rollback on degradation.
    """

    def __init__(
        self,
        registry: AdapterRegistry,
        config: CanaryConfig,
        quality_monitor,  # callable(version_id) -> float
    ):
        self.registry = registry
        self.config = config
        self.quality_monitor = quality_monitor
        self.canary_version: Optional[str] = None
        self.canary_traffic_pct: float = 0.0
        self.last_ramp_time: float = 0.0

    def start_canary(self, version_id: str, cid: str = "") -> None:
        self.canary_version = version_id
        self.canary_traffic_pct = self.config.initial_traffic_pct
        self.last_ramp_time = time.time()
        logger.info(
            "canary.started",
            version=version_id,
            traffic_pct=self.canary_traffic_pct,
            cid=cid,
        )

    def route_request(self) -> str:
        """Returns version_id to serve this request."""
        if self.canary_version and random.random() * 100 < self.canary_traffic_pct:
            return self.canary_version
        return self.registry.production_version or "base"

    def check_and_ramp(self, cid: str = "") -> None:
        if not self.canary_version:
            return

        quality = self.quality_monitor(self.canary_version)

        if quality < self.config.quality_threshold:
            logger.warning(
                "canary.quality_degraded",
                version=self.canary_version,
                quality=quality,
                threshold=self.config.quality_threshold,
                cid=cid,
            )
            if self.config.rollback_on_degradation:
                self.canary_version = None
                self.canary_traffic_pct = 0.0
                logger.info("canary.rolled_back", cid=cid)
            return

        elapsed = time.time() - self.last_ramp_time
        if elapsed >= self.config.ramp_interval_seconds:
            self.canary_traffic_pct = min(
                100.0, self.canary_traffic_pct + self.config.ramp_step_pct
            )
            self.last_ramp_time = time.time()
            logger.info(
                "canary.ramped",
                version=self.canary_version,
                traffic_pct=self.canary_traffic_pct,
                cid=cid,
            )

            if self.canary_traffic_pct >= 100.0:
                self.registry.promote(
                    self.canary_version, EvalThresholds(), cid=cid
                )
                self.canary_version = None
                logger.info("canary.completed_full_rollout", cid=cid)


# --- Fallback Inference Chain ---

class FallbackInferenceChain:
    """
    Try fine-tuned model -> base model with prompt -> cached response.
    Each provider protected by a circuit breaker.
    """

    def __init__(self, providers: list[tuple[str, Any, CircuitBreaker]]):
        self.providers = providers  # (name, inference_fn, circuit_breaker)

    def generate(self, prompt: str, cid: str = "") -> dict:
        errors = []
        for name, inference_fn, cb in self.providers:
            if not cb.can_execute():
                errors.append((name, "circuit_open"))
                continue
            try:
                def _call():
                    return inference_fn(prompt)

                result = retry_with_backoff(_call, max_retries=2, cid=cid)
                cb.record_success()
                return {
                    "text": result,
                    "provider": name,
                    "fallback": name != self.providers[0][0],
                }
            except Exception as e:
                cb.record_failure()
                errors.append((name, str(e)))
                logger.warning("fallback.failed", provider=name, error=str(e), cid=cid)

        raise RuntimeError(f"All inference providers failed: {errors}")


# --- Section: Production Code B: Control Plane + Serve Runtime with Full Jitter, Authz, Lineage (Grok) ---

#!/usr/bin/env python3
"""Fine-tune control+serve resilience: retries, breakers, adapter->base->deterministic.

Stdlib only. Swap Fake* ports for vendor HTTP (OpenAI jobs, vLLM /v1/load_lora_adapter).
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

# Optional deps (not required to run this file):
#   import httpx  # vendor job + vLLM client
#   from peft import PeftModel  # local adapter load; merge_and_unload() MUST be assigned


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = getattr(record, "correlation_id", "-")
        record.tenant_id = getattr(record, "tenant_id", "-")
        record.job_id = getattr(record, "job_id", "-")
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("ft")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"cid":"%(correlation_id)s","tenant":"%(tenant_id)s",'
            '"job":"%(job_id)s","msg":"%(message)s"}'
        )
    )
    handler.addFilter(CorrelationFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


LOG = configure_logging()


def slog(
    level: int,
    msg: str,
    *,
    cid: str,
    tenant: str,
    job: str = "-",
    **fields: object,
) -> None:
    extra = {"correlation_id": cid, "tenant_id": tenant, "job_id": job}
    LOG.log(level, "%s %s", msg, json.dumps(fields, default=str), extra=extra)


class TransientError(Exception):
    """429, 5xx, preemption, adapter swap timeout -- safe to retry idempotent ops."""


class PermanentError(Exception):
    """4xx auth, rank>max_lora_rank, cutoff org, poison config hash -- do not retry."""


def retry_with_jitter(
    fn: Callable[[], object],
    *,
    cid: str,
    tenant: str,
    op: str,
    job: str = "-",
    attempts: int = 4,
    base_s: float = 0.05,
    cap_s: float = 2.0,
) -> object:
    """Retry with full jitter (AWS-style). Distinguishes transient vs permanent errors."""
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
            sleep = min(cap_s, base_s * (2**i))
            sleep = random.uniform(0, sleep)  # full jitter
            slog(
                logging.WARNING, "retry",
                cid=cid, tenant=tenant, job=job, op=op,
                attempt=i + 1, sleep_s=round(sleep, 3), err=str(exc),
            )
            time.sleep(sleep)
    assert last is not None
    raise last


class CircuitState_B(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(TransientError):
    pass


@dataclass
class CircuitBreaker_B:
    """Independent circuit breaker for train API, adapter serve, and base serve."""
    name: str
    failure_threshold: int = 5
    cooldown_s: float = 15.0
    half_open_probes: int = 1
    _state: CircuitState_B = CircuitState_B.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _probes_used: int = 0

    def allow(self) -> None:
        now = time.monotonic()
        if self._state is CircuitState_B.OPEN:
            if now - self._opened_at >= self.cooldown_s:
                self._state = CircuitState_B.HALF_OPEN
                self._probes_used = 0
            else:
                raise CircuitOpenError(f"circuit_open:{self.name}")
        if self._state is CircuitState_B.HALF_OPEN:
            if self._probes_used >= self.half_open_probes:
                raise CircuitOpenError(f"circuit_half_open_busy:{self.name}")
            self._probes_used += 1

    def record_success(self) -> None:
        self._failures = 0
        self._state = CircuitState_B.CLOSED
        self._probes_used = 0

    def record_failure(self) -> None:
        self._failures += 1
        if self._state is CircuitState_B.HALF_OPEN:
            self._state = CircuitState_B.OPEN
            self._opened_at = time.monotonic()
            return
        if self._failures >= self.failure_threshold:
            self._state = CircuitState_B.OPEN
            self._opened_at = time.monotonic()


@dataclass(frozen=True)
class Authz_B:
    """Server-side authorization. adapter_id NEVER parsed from model JSON."""
    tenant_id: str
    actor: str
    allowed_adapter_id: str | None


@dataclass(frozen=True)
class Lineage:
    """Full lineage tuple for idempotent job submission."""
    dataset_hash: str
    base_rev: str
    peft_json: str
    seed: int
    code_sha: str

    def idempotency_key(self) -> str:
        raw = "|".join(
            [self.dataset_hash, self.base_rev, self.peft_json, str(self.seed), self.code_sha]
        )
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class EvalReport:
    """4-gate evaluation: task, forgetting, safety, serve-dtype."""
    task_ok: bool
    forgetting_ok: bool
    safety_ok: bool
    serve_dtype_ok: bool

    def promote_allowed(self) -> bool:
        return self.task_ok and self.forgetting_ok and self.safety_ok and self.serve_dtype_ok


class JobClient(Protocol):
    name: str
    def submit(self, lineage: Lineage, method: str) -> str: ...
    def status(self, job_id: str) -> str: ...


class Generator_B(Protocol):
    name: str
    def complete(self, prompt: str, adapter_id: str | None) -> str: ...


@dataclass
class JobRegistry:
    """Process-local stand-in; production: Postgres unique(idempotency_key)."""
    _jobs: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str | None:
        return self._jobs.get(key)

    def put(self, key: str, job_id: str) -> None:
        self._jobs[key] = job_id


class FtControlPlane:
    """Training-API breaker + idempotent submit. Eval gate is a hard block."""

    def __init__(self, jobs: JobClient, registry: JobRegistry | None = None) -> None:
        self.jobs = jobs
        self.registry = registry or JobRegistry()
        self.breaker = CircuitBreaker_B("train_api", cooldown_s=60.0)

    def submit_idempotent(
        self, lineage: Lineage, method: str, cid: str, tenant: str
    ) -> str:
        key = lineage.idempotency_key()
        existing = self.registry.get(key)
        if existing:
            slog(logging.INFO, "job_dedup", cid=cid, tenant=tenant, job=existing, key=key[:12])
            return existing

        def _op() -> str:
            self.breaker.allow()
            try:
                jid = self.jobs.submit(lineage, method)
            except PermanentError:
                self.breaker.record_failure()
                raise
            except Exception as exc:
                self.breaker.record_failure()
                raise TransientError(str(exc)) from exc
            self.breaker.record_success()
            return jid

        jid = retry_with_jitter(
            _op, cid=cid, tenant=tenant, op="train_submit", attempts=3, base_s=0.2, cap_s=5.0
        )
        assert isinstance(jid, str)
        self.registry.put(key, jid)
        slog(logging.INFO, "job_submitted", cid=cid, tenant=tenant, job=jid, method=method)
        return jid

    def promote(self, adapter_id: str, report: EvalReport, cid: str, tenant: str) -> str:
        if not report.promote_allowed():
            slog(
                logging.ERROR, "promote_blocked", cid=cid, tenant=tenant, job=adapter_id,
                task=report.task_ok, forget=report.forgetting_ok,
                safety=report.safety_ok, dtype=report.serve_dtype_ok,
            )
            raise PermanentError("eval_gate_failed")
        slog(logging.INFO, "promote_ok", cid=cid, tenant=tenant, job=adapter_id)
        return adapter_id


@dataclass
class DegradedResult:
    """Result with degradation metadata for observability."""
    text: str
    adapter_degraded: bool
    generation_degraded: bool
    served: str  # adapter | base | deterministic


class FtServeRuntime:
    """Serve fallback: FT adapter -> base -> deterministic. Independent breakers."""

    def __init__(
        self,
        adapter_gen: Generator_B,
        base_gen: Generator_B,
        adapter_timeout_s: float = 2.0,
    ) -> None:
        self.adapter_gen = adapter_gen
        self.base_gen = base_gen
        self.adapter_timeout_s = adapter_timeout_s
        self.breakers = {
            "adapter": CircuitBreaker_B("adapter_serve"),
            "base": CircuitBreaker_B("base_serve"),
        }

    def _call(self, gen: Generator_B, prompt: str, adapter_id: str | None,
              cid: str, tenant: str) -> str:
        br = self.breakers["adapter" if adapter_id else "base"]

        def _op() -> str:
            br.allow()
            t0 = time.monotonic()
            try:
                text = gen.complete(prompt, adapter_id)
            except PermanentError:
                br.record_failure()
                raise
            except Exception as exc:
                br.record_failure()
                raise TransientError(str(exc)) from exc
            if adapter_id and (time.monotonic() - t0) > self.adapter_timeout_s:
                br.record_failure()
                raise TransientError("adapter_ttft_timeout")
            br.record_success()
            return text

        label = f"generate:{gen.name}:{adapter_id or 'base'}"
        return retry_with_jitter(_op, cid=cid, tenant=tenant, op=label)

    def complete(self, prompt: str, authz: Authz_B, schema_fallback: str) -> DegradedResult:
        cid = str(uuid.uuid4())
        slog(logging.INFO, "serve_start", cid=cid, tenant=authz.tenant_id, q=prompt[:200])
        aid = authz.allowed_adapter_id

        # Level 1: Try fine-tuned adapter
        if aid:
            try:
                text = self._call(self.adapter_gen, prompt, aid, cid, authz.tenant_id)
                slog(logging.INFO, "serve_end", cid=cid, tenant=authz.tenant_id, served="adapter")
                return DegradedResult(text, False, False, "adapter")
            except (TransientError, PermanentError) as exc:
                slog(logging.ERROR, "adapter_failed", cid=cid,
                     tenant=authz.tenant_id, err=str(exc))

        # Level 2: Fall back to base model (longer prompt / RAG belongs here)
        try:
            slog(logging.WARNING, "fallback_base", cid=cid, tenant=authz.tenant_id)
            text = self._call(self.base_gen, prompt, None, cid, authz.tenant_id)
            slog(logging.INFO, "serve_end", cid=cid, tenant=authz.tenant_id, served="base")
            return DegradedResult(text, True, False, "base")
        except (TransientError, PermanentError) as exc:
            slog(logging.ERROR, "base_failed", cid=cid,
                 tenant=authz.tenant_id, err=str(exc))

        # Level 3: Deterministic fallback (regex/schema extract, canned response)
        slog(logging.ERROR, "serve_deterministic", cid=cid, tenant=authz.tenant_id)
        return DegradedResult(
            f"Generation unavailable. Deterministic fallback: {schema_fallback}",
            True,
            True,
            "deterministic",
        )


# --- Demo backends (swap for real vLLM / vendor HTTP clients) ---

class FakeJobClient:
    name = "train_api"

    def submit(self, lineage: Lineage, method: str) -> str:
        _ = method
        return f"job-{lineage.idempotency_key()[:8]}"

    def status(self, job_id: str) -> str:
        return f"succeeded:{job_id}"


class StaticGenerator:
    def __init__(self, name: str, fail: bool = False) -> None:
        self.name = name
        self.fail = fail

    def complete(self, prompt: str, adapter_id: str | None) -> str:
        if self.fail:
            raise TransientError("simulated_outage")
        tag = adapter_id or "base"
        return f"[{tag}] {prompt[:40]}"


if __name__ == "__main__":
    cid = str(uuid.uuid4())
    lineage = Lineage(
        dataset_hash="sha256:abc",
        base_rev="llama-3.1-8b@rev1",
        peft_json='{"r":32,"alpha":64,"target":"all-linear"}',
        seed=42,
        code_sha="deadbeef",
    )
    control = FtControlPlane(FakeJobClient())
    jid = control.submit_idempotent(lineage, "sft", cid, "acme")
    jid2 = control.submit_idempotent(lineage, "sft", cid, "acme")
    assert jid == jid2  # idempotent: same lineage -> same job
    control.promote(
        "adapter-v3",
        EvalReport(True, True, True, True),
        cid,
        "acme",
    )
    serve = FtServeRuntime(
        adapter_gen=StaticGenerator("adapter_gen", fail=True),
        base_gen=StaticGenerator("base_gen"),
    )
    authz = Authz_B(tenant_id="acme", actor="u1", allowed_adapter_id="adapter-v3")
    result = serve.complete("Emit the ticket JSON schema only.", authz, '{"status":"degraded"}')
    print(json.dumps({
        "job_id": jid,
        "dedup_ok": jid == jid2,
        "text": result.text,
        "served": result.served,
        "adapter_degraded": result.adapter_degraded,
        "generation_degraded": result.generation_degraded,
    }, indent=2))
