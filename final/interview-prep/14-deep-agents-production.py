"""Deep Agents Production & Ecosystem patterns.

Covers the production deployment story: Agent Server architecture (API replicas +
queue workers + Postgres + Redis), durable execution with checkpointing, health/readiness
probes, circuit breakers for external services, cost monitoring, and the queue worker pattern.
The key insight is that production ships Agent Server AROUND the same compiled graph --
no new runtime, no Temporal wrapper.
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ============================================================================
# --- Section 1: Agent Server Setup (FastAPI + LangGraph) ---
# ============================================================================
# Agent Server has two roles: API replicas ROUTE, queue workers EXECUTE.
# Coupling them produces "the HTTP timeout is my agent SLO" -- false for
# a 20-minute research run.

# This demonstrates the key concepts. In production, you'd use the actual
# langgraph CLI / Agent Server, not a custom FastAPI wrapper.

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse, StreamingResponse
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


def create_agent_server_app() -> "FastAPI":
    """Create a FastAPI app that mimics Agent Server's split architecture.

    Key invariants:
    - API replicas persist pending runs and stream SSE. They do NOT execute the graph.
    - Queue workers acquire a lease, run LangGraph super-steps, write checkpoints.
    - At most one run per thread_id at a time.
    - Redis is signaling only -- no user/run payloads flow through it.
    """
    if not HAS_FASTAPI:
        raise ImportError("pip install fastapi uvicorn")

    app = FastAPI(title="Agent Server (demo)")

    # In-memory stores for demo (production: Postgres + Redis)
    threads: dict[str, dict] = {}
    runs: dict[str, dict] = {}
    checkpoints: dict[str, list[dict]] = defaultdict(list)

    @app.post("/threads")
    async def create_thread():
        """Create a conversation thread. thread_id < 255 chars (Postgres limit)."""
        thread_id = str(uuid.uuid4())
        threads[thread_id] = {"id": thread_id, "created_at": time.time(), "metadata": {}}
        return threads[thread_id]

    @app.post("/threads/{thread_id}/runs")
    async def create_run(thread_id: str, request: Request):
        """Submit a run. API replica persists it; does NOT execute the graph.
        Redis sentinel wakes a worker. Creating a run is a fast write.

        Payload cap: 25 MB -> HTTP 413.
        If all job slots busy, runs queue (back-pressure, not HTTP 429).
        """
        if thread_id not in threads:
            raise HTTPException(404, f"Thread {thread_id} not found")

        body = await request.json()
        run_id = str(uuid.uuid4())
        runs[run_id] = {
            "id": run_id,
            "thread_id": thread_id,
            "status": "pending",
            "input": body.get("input"),
            "created_at": time.time(),
        }
        # In production: Redis sentinel wakes a worker here
        return {"run_id": run_id, "status": "pending"}

    @app.get("/threads/{thread_id}/runs/{run_id}")
    async def get_run(thread_id: str, run_id: str):
        if run_id not in runs:
            raise HTTPException(404, "Run not found")
        return runs[run_id]

    return app


# ============================================================================
# --- Section 2: Health Check and Readiness Probes ---
# ============================================================================
# Both Postgres AND Redis must be up for Agent Server to be available.
# Prolonged outage of either = server unavailable.

class ServiceStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class HealthCheck:
    """Production health check covering all critical dependencies.

    Agent Server requires:
    - Postgres: checkpoints, threads, runs, store (always required)
    - Redis: wake sentinel, cancel/stream pub/sub (always required)
    - Embedding/LLM API: model calls (degraded without)
    """
    postgres_ok: bool = True
    redis_ok: bool = True
    model_api_ok: bool = True
    last_checkpoint_age_s: float = 0.0
    worker_count: int = 1
    pending_runs: int = 0

    @property
    def status(self) -> ServiceStatus:
        # Both Postgres AND Redis are hard requirements
        if not self.postgres_ok or not self.redis_ok:
            return ServiceStatus.UNHEALTHY
        if not self.model_api_ok:
            return ServiceStatus.DEGRADED
        if self.worker_count == 0:
            return ServiceStatus.UNHEALTHY
        return ServiceStatus.HEALTHY

    @property
    def ready(self) -> bool:
        """Readiness probe: can accept new runs?"""
        return (
            self.status != ServiceStatus.UNHEALTHY
            and self.pending_runs < 1000  # Back-pressure threshold
        )

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "ready": self.ready,
            "postgres": self.postgres_ok,
            "redis": self.redis_ok,
            "model_api": self.model_api_ok,
            "workers": self.worker_count,
            "pending_runs": self.pending_runs,
            "last_checkpoint_age_s": self.last_checkpoint_age_s,
        }


# For the FastAPI app:
def add_health_endpoints(app: "FastAPI", health: HealthCheck):
    """Add /healthz (liveness) and /readyz (readiness) endpoints."""
    if not HAS_FASTAPI:
        return

    @app.get("/healthz")
    async def liveness():
        """Liveness: is the process alive? Kubernetes restarts on failure."""
        return {"status": "alive"}

    @app.get("/readyz")
    async def readiness():
        """Readiness: can we accept traffic? Kubernetes removes from LB on failure."""
        if not health.ready:
            raise HTTPException(503, health.to_dict())
        return health.to_dict()


# ============================================================================
# --- Section 3: Durable Execution with Checkpointing ---
# ============================================================================
# Agent Server IS the Temporal equivalent. You do NOT add Temporal.
#
# Temporal concept         -> Agent Server equivalent
# Workflow ID              -> thread_id (< 255 chars)
# Event history            -> Postgres checkpoints (async/sync/exit)
# Activity worker          -> Queue worker (N_JOBS_PER_WORKER=10)
# Task queue               -> Postgres pending runs + Redis sentinel
# Sticky execution         -> None (stateless replicas)
# Signal / query           -> Redis cancel/stream pub/sub; Command(resume=...)
# Retry policy             -> Four layers (not one Temporal retry)

class DurabilityMode(Enum):
    ASYNC = "async"   # After each step, async -- small crash window (Server default)
    SYNC = "sync"     # Before next step -- highest durability, extra latency
    EXIT = "exit"     # Only on graph exit -- no mid-run crash recovery


@dataclass
class Checkpoint:
    """Represents a durable checkpoint of graph state."""
    checkpoint_id: str
    thread_id: str
    step: int
    state: dict       # Serialized graph state
    created_at: float
    durability: DurabilityMode


class CheckpointStore:
    """Simulates Postgres checkpoint storage. In production, Agent Server
    injects its own checkpointer -- do NOT pass checkpointer= in graph code."""

    def __init__(self, durability: DurabilityMode = DurabilityMode.ASYNC):
        self.durability = durability
        self._store: dict[str, list[Checkpoint]] = defaultdict(list)

    def save(self, thread_id: str, step: int, state: dict) -> Checkpoint:
        cp = Checkpoint(
            checkpoint_id=str(uuid.uuid4()),
            thread_id=thread_id,
            step=step,
            state=state,
            created_at=time.time(),
            durability=self.durability,
        )
        self._store[thread_id].append(cp)
        logger.info(
            "Checkpoint saved: thread=%s step=%d mode=%s",
            thread_id, step, self.durability.value,
        )
        return cp

    def load_latest(self, thread_id: str) -> Optional[Checkpoint]:
        """Load the latest checkpoint for resume. Any replica can do this."""
        checkpoints = self._store.get(thread_id, [])
        return checkpoints[-1] if checkpoints else None

    def list_checkpoints(self, thread_id: str) -> list[Checkpoint]:
        return list(self._store.get(thread_id, []))


# ============================================================================
# --- Section 4: Queue Worker Pattern ---
# ============================================================================
# Workers claim a lease from the queue, run graph super-steps, checkpoint,
# and release. HITL interrupt releases the slot -- a 48-hour Slack approval
# does not hold a worker.
#
# N_JOBS_PER_WORKER default = 10 concurrent runs per worker.
# Workers write heartbeat timestamps to Redis. Sweeper interval: 2 minutes.
# On hard crash, sweeper re-enqueues; another instance resumes from checkpoint.

@dataclass
class RunTask:
    run_id: str
    thread_id: str
    input_data: dict
    status: str = "pending"  # pending -> running -> completed / interrupted / failed
    worker_id: Optional[str] = None
    started_at: Optional[float] = None


class QueueWorker:
    """Simulates Agent Server queue worker behavior.

    Key behaviors:
    - At most one run per thread_id at a time
    - Worker heartbeats to Redis (sweeper reclaims after 2 min silence)
    - HITL interrupt releases the worker slot
    - Stateless: any worker can resume any thread from checkpoint
    """

    N_JOBS_PER_WORKER = 10  # Default concurrent runs per worker
    SWEEPER_INTERVAL_S = 120  # 2-minute sweeper checks for dead workers

    def __init__(self, worker_id: str, checkpoint_store: CheckpointStore):
        self.worker_id = worker_id
        self.checkpoint_store = checkpoint_store
        self._active_runs: dict[str, RunTask] = {}
        self._last_heartbeat = time.monotonic()

    @property
    def available_slots(self) -> int:
        return self.N_JOBS_PER_WORKER - len(self._active_runs)

    def claim(self, task: RunTask) -> bool:
        """Claim a run task. Enforces one run per thread_id."""
        if self.available_slots <= 0:
            return False
        # Check: no other active run on this thread
        for active in self._active_runs.values():
            if active.thread_id == task.thread_id:
                return False
        task.status = "running"
        task.worker_id = self.worker_id
        task.started_at = time.monotonic()
        self._active_runs[task.run_id] = task
        return True

    def execute_step(self, run_id: str, step: int, node_result: dict) -> str:
        """Execute one super-step. Checkpoint at durability cadence.
        Returns new status: 'running', 'completed', or 'interrupted'."""
        task = self._active_runs.get(run_id)
        if not task:
            raise ValueError(f"Run {run_id} not claimed by this worker")

        # Checkpoint after step (async mode)
        self.checkpoint_store.save(task.thread_id, step, node_result)
        self._heartbeat()
        return "running"

    def release(self, run_id: str, status: str = "completed"):
        """Release worker slot. HITL interrupt calls this with 'interrupted'."""
        if run_id in self._active_runs:
            self._active_runs[run_id].status = status
            del self._active_runs[run_id]

    def _heartbeat(self):
        """Write heartbeat. In production, this goes to Redis."""
        self._last_heartbeat = time.monotonic()


# ============================================================================
# --- Section 5: Circuit Breaker for External Services ---
# ============================================================================
# The product does NOT ship a circuit breaker. You build independent breakers
# for: model, sandbox, checkpointer, MCP gateway.
#
# Fallback chain: Hosted Agent Server -> self-host same graph -> deterministic refuse
# NEVER: circuit open -> LocalShellBackend
# NEVER: HITL timeout -> auto-approve
# NEVER: model 429 -> unsandboxed execute

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Independent circuit breaker for a single external service.

    Build separate breakers for each dependency:
    - Model API (LLM provider)
    - Sandbox (code execution)
    - Checkpointer (Postgres)
    - MCP Gateway (tool proxy)
    """
    name: str
    failure_threshold: int = 5
    cooldown_seconds: float = 30.0
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _total_trips: int = 0

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute through the breaker. Raises on open circuit."""
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_seconds:
                self._state = CircuitState.HALF_OPEN
                logger.info("Circuit %s: half-open, probing", self.name)
            else:
                raise RuntimeError(f"circuit_open:{self.name}")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure(e)
            raise

    def _on_success(self):
        self._failures = 0
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
            logger.info("Circuit %s: closed (recovered)", self.name)

    def _on_failure(self, error: Exception):
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            self._total_trips += 1
            logger.warning(
                "Circuit %s: OPEN (trip #%d, error: %s)",
                self.name, self._total_trips, error,
            )

    @property
    def state(self) -> CircuitState:
        return self._state


def build_service_breakers() -> dict[str, CircuitBreaker]:
    """Create independent breakers for all external dependencies."""
    return {
        "model": CircuitBreaker("model", failure_threshold=3, cooldown_seconds=10),
        "sandbox": CircuitBreaker("sandbox", failure_threshold=5, cooldown_seconds=30),
        "checkpointer": CircuitBreaker("checkpointer", failure_threshold=3, cooldown_seconds=15),
        "mcp_gateway": CircuitBreaker("mcp_gateway", failure_threshold=5, cooldown_seconds=30),
    }


# ============================================================================
# --- Section 6: Four Retry Layers ---
# ============================================================================
# Deep Agents does NOT install retry middleware by default.
# These four layers are commonly confused in interviews.

RETRY_LAYERS = {
    "Layer 1 - Chat Model HTTP": {
        "default": "max_retries=6",
        "retries": "429 / 5xx / network errors",
        "skips": "401 / 404 (permanent errors)",
    },
    "Layer 2 - LangGraph Node RetryPolicy": {
        "default": "max_attempts=3 (includes first try)",
        "retries": "Exceptions matching retry_on predicate",
        "skips": "ValueError, TypeError, non-retryable types",
        "note": "jitter=True by default",
    },
    "Layer 3 - Agent Server Run Attempt": {
        "default": "3 attempts",
        "retries": "Transient Postgres errors during the run",
        "skips": "Model 429s (this is NOT a model retry layer)",
    },
    "Layer 4 - Middleware (if installed)": {
        "default": "max_retries=2 (3 total)",
        "retries": "Model: retryable + unclassified; Tool: optional allowlist",
        "skips": "Tools not in the allowlist",
        "note": "NOT installed by default -- you must append it",
    },
}


@dataclass
class RetryPolicy:
    """LangGraph node retry policy (Layer 2).
    This is per-node, not per-request."""
    max_attempts: int = 3        # Includes the first attempt
    initial_interval: float = 1.0
    backoff_factor: float = 2.0
    jitter: bool = True
    retry_on: tuple = (ConnectionError, TimeoutError)  # NOT ValueError

    def should_retry(self, attempt: int, error: Exception) -> bool:
        if attempt >= self.max_attempts:
            return False
        return isinstance(error, self.retry_on)

    def delay(self, attempt: int) -> float:
        """Exponential backoff with optional jitter."""
        base = self.initial_interval * (self.backoff_factor ** (attempt - 1))
        if self.jitter:
            import random
            base *= (0.5 + random.random())
        return base


# ============================================================================
# --- Section 7: Cost Monitoring and Alerts ---
# ============================================================================
# The all-in cost per 1k runs: model ($223) + traces ($0.50-$5) + infra (~$10).
# Key cost traps: extended trace retention, zombie crons, dual-instrument OTel.

@dataclass
class CostTracker:
    """Track per-run costs across model, traces, and infra.

    Key numbers:
    - Model (Sonnet 4.6, 10-call cached): $223 / 1k runs
    - LangSmith traces (base 14d): $0.50 / 1k
    - LangSmith traces (extended 400d): $5.00 / 1k (auto-upgrade trap)
    - Dedicated Small infra: ~$384/mo (~$10/1k at 10k runs/week)
    """
    model_cost_per_run: float = 0.223        # $223 / 1k
    trace_cost_per_run: float = 0.0005       # $0.50 / 1k (base)
    infra_cost_per_run: float = 0.01         # ~$10/1k at GTM volume
    _total_runs: int = 0
    _total_cost: float = 0.0
    _cost_by_component: dict = field(default_factory=lambda: defaultdict(float))
    _alerts: list = field(default_factory=list)
    budget_per_day: float = 500.0            # Daily budget alert threshold

    def record_run(
        self,
        model_tokens_in: int,
        model_tokens_out: int,
        cached_tokens: int = 0,
        trace_retention: str = "base",
    ):
        """Record costs for a single run."""
        # Model cost (simplified: $3/M input, $15/M output for Sonnet 4.6)
        fresh_input = model_tokens_in - cached_tokens
        cached_cost = cached_tokens * 0.3 / 1_000_000   # 0.1x of $3/M
        fresh_cost = fresh_input * 3.0 / 1_000_000
        output_cost = model_tokens_out * 15.0 / 1_000_000
        model_cost = cached_cost + fresh_cost + output_cost

        # Trace cost
        trace_cost = 0.0005 if trace_retention == "base" else 0.005

        total = model_cost + trace_cost + self.infra_cost_per_run
        self._total_runs += 1
        self._total_cost += total
        self._cost_by_component["model"] += model_cost
        self._cost_by_component["traces"] += trace_cost
        self._cost_by_component["infra"] += self.infra_cost_per_run

        return {
            "run_cost": round(total, 4),
            "model": round(model_cost, 4),
            "traces": round(trace_cost, 4),
            "infra": round(self.infra_cost_per_run, 4),
        }

    def check_alerts(self) -> list[str]:
        """Check for cost anomalies."""
        alerts = []
        if self._total_cost > self.budget_per_day:
            alerts.append(
                f"BUDGET_EXCEEDED: ${self._total_cost:.2f} > ${self.budget_per_day:.2f}/day"
            )
        # Check for extended trace cost trap
        if self._cost_by_component["traces"] > self._cost_by_component["model"] * 0.05:
            alerts.append(
                "TRACE_COST_HIGH: traces > 5% of model cost. "
                "Check for auto-upgraded extended retention."
            )
        return alerts

    @property
    def summary(self) -> dict:
        return {
            "total_runs": self._total_runs,
            "total_cost": round(self._total_cost, 2),
            "cost_per_1k": round(self._total_cost / max(1, self._total_runs) * 1000, 2),
            "by_component": {k: round(v, 4) for k, v in self._cost_by_component.items()},
        }


# ============================================================================
# --- Section 8: Streaming and Disconnect Behavior ---
# ============================================================================
# Protocol v2: POST-only SSE, resume via body field "since" (NOT Last-Event-ID).
# Default disconnect: run keeps going. HITL is NOT cancel.

class DoubleTextStrategy(Enum):
    """What happens when a new input arrives while a run is running."""
    ENQUEUE = "enqueue"       # Queue the new run (default, no state corruption)
    REJECT = "reject"         # 409 until current run ends
    INTERRUPT = "interrupt"   # Halt current, keep checkpoints, start from that state
    ROLLBACK = "rollback"     # Halt and DELETE the in-flight run's checkpoints


@dataclass
class StreamSession:
    """Manages SSE streaming with proper disconnect semantics.

    Key rules:
    - Protocol v2: resume via body field 'since', NOT Last-Event-ID header
    - Browser EventSource does NOT apply
    - on_disconnect default: run continues (not cancel)
    - HITL interrupt != cancel: worker releases slot, sleeps unbounded
    """
    thread_id: str
    run_id: str
    on_disconnect: str = "continue"  # "continue" or "cancel"
    double_text: DoubleTextStrategy = DoubleTextStrategy.ENQUEUE
    _events: list[dict] = field(default_factory=list)
    _last_seq: int = 0

    def emit(self, event_type: str, data: dict) -> int:
        self._last_seq += 1
        event = {"seq": self._last_seq, "type": event_type, "data": data}
        self._events.append(event)
        return self._last_seq

    def replay_since(self, since: int) -> list[dict]:
        """Resume from a sequence number. Protocol v2: POST body, not header."""
        return [e for e in self._events if e["seq"] > since]


# ============================================================================
# --- Section 9: Failure Taxonomy ---
# ============================================================================
# Common production failures and their handling patterns.

FAILURE_TAXONOMY = {
    "transient": {
        "examples": ["Model 429/5xx", "network blip", "Postgres blip"],
        "handling": "Layer 1-4 retries with backoff and jitter",
    },
    "permanent": {
        "examples": ["ValueError", "401/404", "GraphRecursionError", "@auth 403"],
        "handling": "Fail closed. Do NOT bump recursion_limit to 10000",
    },
    "poison_pill": {
        "examples": [
            "Non-retryable crash loop (sweeper burns 3 PG attempts)",
            "HITL never resumed",
            "Recursion ceiling hit",
        ],
        "handling": "Shrink state; cancel/resume from any replica; product cap",
    },
    "idempotency": {
        "examples": ["Resume restarts node from line 1", "Duplicate Slack/CRM write"],
        "handling": "Upserts; idempotency keys; HITL after draft",
    },
    "zombie_spend": {
        "examples": [
            "SSE drop while worker continues",
            "Cron never deleted",
            "Serverless idle-before-scale-down",
        ],
        "handling": "on_disconnect='cancel' for chats; cron lifecycle in destroy",
    },
}


# ============================================================================
# --- Demo ---
# ============================================================================

if __name__ == "__main__":
    # 1. Health check
    health = HealthCheck(postgres_ok=True, redis_ok=True, model_api_ok=True, worker_count=3)
    assert health.status == ServiceStatus.HEALTHY
    assert health.ready

    health_degraded = HealthCheck(postgres_ok=True, redis_ok=True, model_api_ok=False)
    assert health_degraded.status == ServiceStatus.DEGRADED

    health_down = HealthCheck(postgres_ok=False, redis_ok=True)
    assert health_down.status == ServiceStatus.UNHEALTHY
    assert not health_down.ready

    # 2. Durable execution with checkpointing
    store = CheckpointStore(durability=DurabilityMode.ASYNC)
    thread_id = "thread-prod-001"
    store.save(thread_id, step=1, state={"node": "extract", "docs": 5})
    store.save(thread_id, step=2, state={"node": "validate", "valid": 4})
    latest = store.load_latest(thread_id)
    assert latest is not None
    assert latest.step == 2

    # 3. Queue worker pattern
    worker = QueueWorker("worker-1", store)
    task = RunTask(run_id="run-001", thread_id=thread_id, input_data={"query": "test"})
    assert worker.claim(task)
    assert worker.available_slots == 9  # 10 - 1
    worker.execute_step("run-001", step=3, node_result={"node": "score", "result": 0.85})
    worker.release("run-001", status="completed")
    assert worker.available_slots == 10

    # 4. Circuit breaker
    breakers = build_service_breakers()
    model_breaker = breakers["model"]

    # Simulate successful calls
    for _ in range(3):
        model_breaker.call(lambda: "ok")
    assert model_breaker.state == CircuitState.CLOSED

    # Simulate failures to trip the breaker
    for _ in range(3):
        try:
            model_breaker.call(lambda: (_ for _ in ()).throw(ConnectionError("timeout")))
        except ConnectionError:
            pass
    assert model_breaker.state == CircuitState.OPEN

    # Verify open circuit rejects calls
    try:
        model_breaker.call(lambda: "should fail")
        assert False, "Should have raised"
    except RuntimeError as e:
        assert "circuit_open" in str(e)

    # 5. Cost monitoring
    tracker = CostTracker(budget_per_day=1.0)
    for _ in range(5):
        tracker.record_run(
            model_tokens_in=30000,
            model_tokens_out=8000,
            cached_tokens=2000,
            trace_retention="base",
        )
    summary = tracker.summary
    assert summary["total_runs"] == 5

    # 6. Retry policy
    policy = RetryPolicy(max_attempts=3)
    assert policy.should_retry(1, ConnectionError("timeout"))
    assert not policy.should_retry(1, ValueError("bad input"))  # Not retryable
    assert not policy.should_retry(3, ConnectionError("timeout"))  # Max attempts

    # 7. Streaming
    session = StreamSession(thread_id="t1", run_id="r1")
    seq1 = session.emit("token", {"text": "Hello"})
    seq2 = session.emit("token", {"text": " world"})
    replayed = session.replay_since(seq1)
    assert len(replayed) == 1  # Only events after seq1

    print("All production patterns validated successfully.")
    print(f"  - Health check: {health.status.value}")
    print(f"  - Checkpoints: {len(store.list_checkpoints(thread_id))} saved")
    print(f"  - Worker slots: {worker.available_slots}/{QueueWorker.N_JOBS_PER_WORKER}")
    print(f"  - Circuit breakers: {len(breakers)} services monitored")
    print(f"  - Cost: ${summary['total_cost']:.2f} for {summary['total_runs']} runs")
    print(f"  - Retry layers: {len(RETRY_LAYERS)}")
    print(f"  - Failure classes: {len(FAILURE_TAXONOMY)}")
