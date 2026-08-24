# Planning & Reasoning — Decomposition, Reflection, Verification, Replanning

## 1. System Topology & Data Flow

Production planning agents work best when planning, execution, verification, and replanning are explicit runtime components instead of an opaque "reason harder" prompt. The control plane owns decomposition policy, retry budgets, approvals, and checkpoint boundaries. The data plane executes tools, retrieval, model inference, and evidence persistence.

```text
┌────────────────────────────── Control Plane ───────────────────────────────┐
│ API Gateway -> AuthN/Z -> Policy Router -> Planner / DAG Scheduler         │
│      │              │               │                    │                  │
│      │              │               │                    ├─ Verifier Node   │
│      │              │               │                    ├─ Replanner       │
│      │              │               │                    ├─ Approval Gate   │
│      │              │               │                    └─ Deadline / Cost │
│      └─────────────────────────────> Correlation ID / Tenant / Run Budget   │
└──────────────────────────────────────────────────────────────────────────────┘
                                 │
                                 v
┌──────────────────────────────── Data Plane ────────────────────────────────┐
│ Executor Pool <-> Tool Proxy / MCP Gateway <-> Search / DB / CRM / Queue  │
│      │                    │                                                 │
│      │                    ├─ Schema Validation                              │
│      │                    ├─ RBAC / Approval Hooks                          │
│      │                    └─ PII Filter / Redaction                         │
│      │
│      └───────────────> Answer Synthesizer / Deterministic Fallback         │
└──────────────────────────────────────────────────────────────────────────────┘
         │                          │                           │
         v                          v                           v
┌──────────────────┐      ┌────────────────────┐      ┌──────────────────────┐
│ Persistence      │      │ Tool Result Store  │      │ Telemetry / Audit    │
│ checkpoints      │      │ idempotency keys   │      │ traces / metrics     │
│ thread_id/run_id │      │ cached observations│      │ immutable decisions  │
│ replay journal   │      │ verifier evidence  │      │ SIEM / cost ledger   │
└──────────────────┘      └────────────────────┘      └──────────────────────┘
```

### Request-flow narrative

1. `API Gateway` authenticates the tenant, assigns a `correlation_id`, and starts a hard deadline budget.
2. `Policy Router` decides whether the request should remain a bounded `ReAct` loop, a `planner/executor` workflow, or a dependency-aware `DAG` plan.
3. `Planner / DAG Scheduler` emits either a serial step list or a graph of independent subtasks with explicit dependencies.
4. `Executor Pool` runs bounded tool or retrieval steps through a policy-enforcing `MCP Gateway`, never with raw credentials exposed to the model.
5. `Verifier Node` grades evidence sufficiency, schema correctness, business-rule compliance, and whether remaining uncertainty justifies another attempt.
6. `Replanner` rewrites only the unfinished portion of the plan using persisted state, rather than rerunning the entire workflow from scratch.
7. `Answer Synthesizer` produces the final response if confidence is sufficient; otherwise it triggers a degraded but truthful fallback.
8. `Telemetry / Audit` records plan versions, tool observations, verifier verdicts, retries, fallback reasons, and user-visible degradations.

The key production boundary is `plan state` versus `execution state`. The plan can be revised many times, but side effects must stay replay-safe. That is why every external action needs idempotency keys and every verifier decision needs durable evidence.

## 2. Core Mechanics & Algorithms

### Planning topologies

`ReAct` remains the baseline topology:

```text
RECEIVE
  -> REASON
  -> ACT
  -> OBSERVE
  -> DECIDE
     -> REASON    if more work remains and budget allows
     -> COMPLETE  if success criteria are met
     -> FAIL      if policy, cost, or deadline guard trips
```

It is simple but serial. Every observation usually triggers another expensive model turn.

`Planner/executor` separates global reasoning from local execution:

```text
RECEIVE
  -> PLAN
  -> EXECUTE_STEP_SET
  -> VERIFY
     -> REPLAN_REMAINDER  if evidence is weak and retry budget remains
     -> COMPLETE          if evidence and policy checks pass
     -> ESCALATE          if side effects or risk require approval
```

This architecture amortizes the strongest reasoning model across the full task instead of paying for it after every tool result.

`Parallel DAG planning` goes further by identifying independence between steps:

```text
Task graph G = (V, E)
ready(V) = { v in V | indegree(v) = 0 and unfinished(v) }
execute ready(V) in parallel
commit successful outputs
release dependent nodes
stop when terminal node or failure policy triggers
```

Useful complexity approximations:

- Serial `ReAct` makespan: `O(k * (L_model + L_tool))`
- Planner/executor makespan: `O(L_plan + critical_path(executors) + L_verify)`
- DAG planner makespan: `O(L_plan + max_path_latency + scheduler_overhead)`
- Verifier grading work: typically `O(n)` in number of candidate observations, though pairwise consistency checks can approach `O(n^2)`

### Reflection and verification as control nodes

The strongest production pattern in the source set is not unconstrained self-critique. It is explicit verification:

- relevance grading before answer generation
- schema validation before tool execution
- approval gating before side effects
- stop or rewrite logic after bounded retries

This matters because freeform reflection is easy to overuse. A verifier should consume structured evidence and emit a finite decision set such as `accept`, `rewrite`, `retry`, `approve`, or `escalate`.

### Invariants and convergence rules

Planning systems stay stable only if these invariants hold:

- every run has a stable `run_id`, `tenant_id`, and monotonic `step_index`
- every plan revision increments `plan_version`
- every side-effecting tool call uses an idempotency key derived from `(run_id, plan_version, step_id)`
- every loop has hard bounds on `max_turns`, `max_replans`, and total deadline
- every checkpoint captures enough state to continue with the same remaining plan and verifier context

Convergence rule of thumb:

```text
bounded_progress =
  remaining_steps decreases
  OR verifier_confidence increases
  OR uncertainty mass shrinks
```

If none of those move for two consecutive cycles, the agent is in a replanning storm and should degrade or escalate instead of trying again.

## 3. Token Economics & NFR Analysis

> ⚠️ Gap: Public sources in `research_cursor` are strong on structural trade-offs, reasoning-token billing, and benchmark deltas, but thin on vendor-published end-to-end `p50/p95/p99` latency percentiles for full planner-verifier workloads. The percentile targets below are recommended internal SLO envelopes, not provider guarantees.

### Cost formulas

Assumptions:

- `runs = 1000`
- `react_turns = 6`
- `planner_calls = 1`
- `executor_steps = 4`
- `verifier_calls = 1`
- `replan_calls = 0.3` average per run
- `cached_prefix_tokens` are reused between turns where the framework supports prompt caching
- `reasoning_tokens` are billable output tokens

#### ReAct loop

```text
$ per 1k runs =
1000 * (
  ((react_turns * fresh_input_tokens) / 1_000_000) * P_input +
  (cached_prefix_tokens / 1_000_000) * P_cache_write +
  (((react_turns - 1) * cached_prefix_tokens) / 1_000_000) * P_cache_read +
  ((react_turns * visible_output_tokens) / 1_000_000) * P_output +
  ((react_turns * reasoning_tokens) / 1_000_000) * P_output +
  tool_surcharge_per_run
)
```

Without caching:

```text
$ per 1k runs =
1000 * (
  ((react_turns * (fresh_input_tokens + cached_prefix_tokens)) / 1_000_000) * P_input +
  ((react_turns * visible_output_tokens) / 1_000_000) * P_output +
  ((react_turns * reasoning_tokens) / 1_000_000) * P_output +
  tool_surcharge_per_run
)
```

The economic penalty is obvious: the expensive reasoning tier is paid on nearly every turn, and history replay keeps growing.

#### Planner/executor with cheaper bounded executors

```text
$ per 1k runs =
1000 * (
  (planner_input_tokens / 1_000_000) * P_plan_in +
  ((planner_output_tokens + planner_reasoning_tokens) / 1_000_000) * P_plan_out +
  (executor_fresh_input_tokens / 1_000_000) * P_exec_in +
  (executor_cache_write_tokens / 1_000_000) * P_exec_cache_write +
  (executor_cache_read_tokens / 1_000_000) * P_exec_cache_read +
  (executor_output_tokens / 1_000_000) * P_exec_out +
  (verifier_input_tokens / 1_000_000) * P_verify_in +
  ((verifier_output_tokens + verifier_reasoning_tokens) / 1_000_000) * P_verify_out +
  (replan_calls * (
      (replan_input_tokens / 1_000_000) * P_plan_in +
      ((replan_output_tokens + replan_reasoning_tokens) / 1_000_000) * P_plan_out
    )) +
  tool_surcharge_per_run
)
```

This structure is usually cheaper than pure `ReAct` because the strongest model is called fewer times while cheaper executors handle bounded substeps.

#### Parallel decomposition budget

```text
planning_run_cost
  ~= planner_cost
   + Σ(executor_cost_i)
   + Σ(verifier_cost_i)
   + replanning_cost
   + tool_or_retrieval_surcharges
```

```text
critical_path_latency
  ~= planning_latency
   + max(parallel_step_durations)
   + verification_latency
   + answer_synthesis_latency
```

Published numeric anchor from the source set:

- Azure's agentic retrieval example totals `$4.32` for `2,000` retrievals with `3` subqueries and reranking assumptions.
- Equivalent retrieval-side planning budget: `$2.16 per 1k runs` before final answer synthesis.

That number is useful because it shows decomposition overhead is real even before generation cost is added.

### Latency targets

Recommended synchronous targets for enterprise planner/verifier systems:

- bounded `planner/executor`: `p50 <= 1.8s`, `p95 <= 5.0s`, `p99 <= 8.0s`
- verifier-heavy grounded workflows: `p50 <= 2.5s`, `p95 <= 7.0s`, `p99 <= 12.0s`
- anything beyond those budgets should acknowledge quickly and continue as durable async work

Mitigations by percentile:

- `p50`: reuse cached prefixes, colocate planners with tool gateways, stream first token or first progress event early
- `p95`: cap fan-out width, parallelize independent steps, precompute tool auth context, compact replay history
- `p99`: enforce admission control, per-step deadlines, breaker-open fallbacks, and downgrade from replan to deterministic completion once retry budget is spent

### Throughput and back-pressure

Capacity is bounded by both request rate and token rate:

```text
max_completed_runs_per_second
  ~= min(
       provider_rpm / 60 / avg_model_turns_per_run,
       provider_tpm / 60 / avg_total_tokens_per_run
     )
```

For DAG planners, executor concurrency is the next bottleneck:

```text
required_executor_slots
  ~= qps * avg_parallel_steps_per_run * avg_step_duration_seconds
```

Back-pressure policy should be explicit:

1. below `70%` of quota, accept normally
2. from `70%` to `85%`, reduce planner depth and cap speculative branches
3. from `85%` to `95%`, queue low-priority traffic and disable optional verification passes
4. above `95%`, reject non-critical traffic and force read-only deterministic fallbacks

This is the operational lesson from planning systems: the queue grows on branch fan-out, not only on raw request count.

### Availability, RPO, RTO, and compliance

Recommended enterprise targets:

- `Availability`: `99.9%` for synchronous planning APIs and `99.95%` for durable workflow control planes
- `RPO`: `<= 5 min` for checkpoints and session state; effectively `0` for immutable audit events once acknowledged
- `RTO`: `<= 30 min` for regional failover of planner state stores; `<= 2 hr` for rehydrating paused workflows from workflow history

Compliance design points:

- use region-bound execution for regulated data and keep planner/tool providers within approved residency boundaries
- treat every external tool as a separate compliance hop with explicit access logging
- persist redacted observations in telemetry whenever raw tool output may contain PII
- require human approval for side effects that affect money movement, identity, or production configuration

## 4. Distributed Resilience & Security

### Durable execution

The safest enterprise pattern is to keep user-facing orchestration synchronous only for short control decisions and move long-running execution into a durable workflow engine.

Practical pattern:

- `Kafka` or queue topics carry step-completion and replay events
- `Temporal` or equivalent workflow engine stores workflow history, retry state, cooldown timers, and approval pauses
- planner output is checkpointed after every meaningful state transition
- executor outputs are persisted before dependent steps are released
- dead-letter queues capture poison-pill runs with step payload, tenant, tool, and failure fingerprint

This matters because replanning is not just another prompt. It is a state transition that must be replay-safe under process crashes, duplicate deliveries, and partial success.

### Failure taxonomy

`Transient failures`

- provider `429` or timeout
- temporary search or database unavailability
- network partition between runtime and tool proxy

`Permanent failures`

- schema-invalid tool arguments
- RBAC denial
- unsupported downstream action
- query or task outside policy scope

`Poison-pill failures`

- a specific payload repeatedly triggers the same parser or verifier exception
- a tool result repeatedly causes runaway replanning
- a replayed step cannot converge because state is already externally mutated

`Correctness failures`

- schema-valid but business-wrong action
- verifier accepts low-quality evidence
- missing reasoning or thinking artifacts during replay
- over-decomposition that creates noise instead of better evidence

### Retry, locking, and idempotency

Retries belong only on transient boundaries and only for idempotent operations:

- planner/model transport: `2-3` retries with exponential backoff and jitter
- executor tool reads: `2-3` retries with per-call deadline
- side-effecting writes: retry only with stable idempotency keys
- verifier failures: one retry at most, then degrade or escalate

Distributed locking should be narrow. Lock workflow records or approval tokens, not the whole tenant. The goal is preventing duplicate side effects, not serializing all thought.

Recommended idempotency key:

```text
idempotency_key = hash(run_id || plan_version || step_id || tool_name || semantic_input_digest)
```

### Circuit breakers and graceful degradation

State model:

```text
CLOSED
  -> OPEN       after timeout or error threshold breach
  -> HALF_OPEN  after cooldown
  -> CLOSED     after healthy probes
  -> OPEN       if probes fail
```

Graceful degradation order:

1. `planner + parallel executors + verifier + replanner`
2. `planner + bounded serial executors + verifier`
3. `single-pass bounded ReAct`
4. `deterministic rules-based fallback with clear degradation flag`

The user should receive a truthful result sooner rather than an "intelligent" loop that keeps burning budget without progress.

### Enterprise security controls

Zero-Trust `MCP` architecture:

- all tools terminate at a policy proxy, never directly from model to production system
- proxies exchange OAuth `2.1` tokens with PKCE and resource indicators where applicable
- planner and executor identities are separated so the planner cannot silently inherit write privileges

Tool-level `RBAC`:

- role maps evaluate `(user_role, tenant, tool_name, resource_scope, action)`
- approval service adds step-up authorization for privileged actions
- deny decisions include machine-readable reason codes for audit and analytics

PII filtering pipeline:

1. detect sensitive spans before tool execution and again before prompt construction
2. redact, tokenize, or mask fields according to policy
3. preserve reversible mappings only in protected audit stores when business process requires it
4. emit every redaction decision to an immutable audit ledger

Auditability requirements:

- append-only event log for plan versions, verifier verdicts, approvals, and fallbacks
- full chain of custody from user request -> plan -> tool call -> observation -> decision -> output
- replay journal sufficient to explain why a replan occurred and what evidence changed

## 5. Production Enterprise Code

The example below is a runnable Python service skeleton for bounded planning and replanning. It demonstrates retries with exponential backoff and jitter, a circuit breaker, a fallback model chain, structured logging with correlation IDs, verifier-driven replanning, and graceful degradation when planning or tools are partially unavailable.

```python
from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from typing import Callable, Iterable, Sequence


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


@dataclass(frozen=True)
class Step:
    step_id: str
    description: str
    tool_name: str
    requires_write: bool = False


@dataclass(frozen=True)
class Observation:
    step_id: str
    tool_name: str
    content: str
    sufficient: bool


@dataclass(frozen=True)
class Plan:
    plan_version: int
    steps: list[Step]
    final_goal: str


@dataclass(frozen=True)
class Decision:
    accepted: bool
    needs_replan: bool
    reason: str


@dataclass(frozen=True)
class Response:
    text: str
    degraded: bool
    reason: str | None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time": int(record.created * 1000),
        }
        for key in ("event", "correlation_id", "tenant_id", "degraded"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, separators=(",", ":"))


def build_logger() -> logging.Logger:
    logger = logging.getLogger("planning_runtime")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOGGER = build_logger()


def log(event: str, message: str, correlation_id: str, tenant_id: str, **extra: object) -> None:
    LOGGER.info(
        message,
        extra={
            "event": event,
            "correlation_id": correlation_id,
            "tenant_id": tenant_id,
            **extra,
        },
    )


def retry(fn: Callable[[], object], retries: int, base_delay_s: float, max_delay_s: float) -> object:
    attempt = 0
    while True:
        try:
            return fn()
        except TransientError:
            if attempt >= retries:
                raise
            delay = min(max_delay_s, base_delay_s * (2 ** attempt))
            jitter = random.uniform(0.0, delay * 0.25)
            time.sleep(delay + jitter)
            attempt += 1


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_timeout_s: float) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.failures = 0
        self.state = CircuitState.CLOSED
        self.opened_at = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self.state == CircuitState.OPEN:
                if (time.time() - self.opened_at) >= self.recovery_timeout_s:
                    self.state = CircuitState.HALF_OPEN
                    return True
                return False
            return True

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.failure_threshold:
                self.state = CircuitState.OPEN
                self.opened_at = time.time()


class ApprovalService:
    def authorize(self, step: Step) -> None:
        if step.requires_write and "delete" in step.description.lower():
            raise PermanentError("destructive write requires human approval")


class ToolGateway:
    def __init__(self, knowledge_base: dict[str, str]) -> None:
        self.knowledge_base = knowledge_base

    def execute(self, step: Step, request: str) -> Observation:
        key = step.description.lower()
        if "timeout" in request.lower() and step.tool_name == "crm_lookup":
            raise TransientError("temporary CRM timeout")
        content = self.knowledge_base.get(key, f"No direct evidence for: {step.description}")
        sufficient = not content.startswith("No direct evidence")
        return Observation(step_id=step.step_id, tool_name=step.tool_name, content=content, sufficient=sufficient)


class PrimaryPlanner:
    def create_plan(self, request: str, plan_version: int) -> Plan:
        if "planner_fail" in request.lower():
            raise TransientError("primary planner unavailable")
        steps = [
            Step("step-1", "lookup customer contract", "crm_lookup"),
            Step("step-2", "check compliance policy", "policy_lookup"),
            Step("step-3", "summarize action path", "synthesizer"),
        ]
        return Plan(plan_version=plan_version, steps=steps, final_goal=request)


class SecondaryPlanner:
    def create_plan(self, request: str, plan_version: int) -> Plan:
        steps = [
            Step("step-1", "lookup customer contract", "crm_lookup"),
            Step("step-2", "summarize action path", "synthesizer"),
        ]
        return Plan(plan_version=plan_version, steps=steps, final_goal=request)


def deterministic_plan(request: str, plan_version: int) -> Plan:
    return Plan(
        plan_version=plan_version,
        steps=[Step("step-1", "summarize available evidence", "synthesizer")],
        final_goal=request,
    )


class Verifier:
    def evaluate(self, plan: Plan, observations: Sequence[Observation]) -> Decision:
        if any("override" in observation.content.lower() for observation in observations):
            return Decision(accepted=False, needs_replan=False, reason="policy_override_detected")
        if len(observations) < len(plan.steps) - 1:
            return Decision(accepted=False, needs_replan=True, reason="missing_evidence")
        if any(not observation.sufficient for observation in observations[:-1]):
            return Decision(accepted=False, needs_replan=True, reason="weak_grounding")
        return Decision(accepted=True, needs_replan=False, reason="verified")


def make_idempotency_key(run_id: str, plan_version: int, step: Step, request: str) -> str:
    digest = sha256(request.encode("utf-8")).hexdigest()
    payload = f"{run_id}|{plan_version}|{step.step_id}|{step.tool_name}|{digest}"
    return sha256(payload.encode("utf-8")).hexdigest()


class PlanningRuntime:
    def __init__(
        self,
        tool_gateway: ToolGateway,
        approval_service: ApprovalService,
        primary_planner: PrimaryPlanner,
        secondary_planner: SecondaryPlanner,
        verifier: Verifier,
        max_replans: int = 1,
    ) -> None:
        self.tool_gateway = tool_gateway
        self.approval_service = approval_service
        self.primary_planner = primary_planner
        self.secondary_planner = secondary_planner
        self.verifier = verifier
        self.max_replans = max_replans
        self.planner_breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_s=5.0)

    def run(self, request: str, tenant_id: str) -> Response:
        correlation_id = str(uuid.uuid4())
        run_id = correlation_id
        degraded = False
        degrade_reason: str | None = None

        log("request_start", "starting planning request", correlation_id, tenant_id)
        plan = self._plan_with_fallback(request, correlation_id, tenant_id, plan_version=1)
        if plan.plan_version != 1 or len(plan.steps) < 3:
            degraded = True
            degrade_reason = "planner_fallback_used"

        for attempt in range(self.max_replans + 1):
            observations = self._execute_plan(plan, request, run_id, correlation_id, tenant_id)
            decision = self.verifier.evaluate(plan, observations)
            log("verification_complete", decision.reason, correlation_id, tenant_id)

            if decision.accepted:
                text = self._compose_answer(plan, observations)
                log("request_complete", "completed planning request", correlation_id, tenant_id, degraded=degraded)
                return Response(text=text, degraded=degraded, reason=degrade_reason)

            if decision.needs_replan and attempt < self.max_replans:
                degraded = True
                degrade_reason = decision.reason
                log("replanning", "verifier requested replanning", correlation_id, tenant_id, degraded=True)
                plan = self._replan(plan, observations, request, correlation_id, tenant_id)
                continue

            degraded = True
            degrade_reason = decision.reason
            fallback_text = self._deterministic_answer(observations, request)
            log("request_degraded", "returning deterministic fallback", correlation_id, tenant_id, degraded=True)
            return Response(text=fallback_text, degraded=True, reason=degrade_reason)

        fallback_text = self._deterministic_answer([], request)
        return Response(text=fallback_text, degraded=True, reason="unexpected_flow")

    def _plan_with_fallback(self, request: str, correlation_id: str, tenant_id: str, plan_version: int) -> Plan:
        if self.planner_breaker.allow():
            try:
                plan = retry(
                    lambda: self.primary_planner.create_plan(request, plan_version),
                    retries=2,
                    base_delay_s=0.05,
                    max_delay_s=0.20,
                )
                assert isinstance(plan, Plan)
                self.planner_breaker.record_success()
                return plan
            except TransientError:
                self.planner_breaker.record_failure()
                log("primary_planner_failed", "primary planner failed", correlation_id, tenant_id, degraded=True)

        try:
            plan = self.secondary_planner.create_plan(request, plan_version)
            log("secondary_planner_used", "secondary planner used", correlation_id, tenant_id, degraded=True)
            return plan
        except Exception:
            log("deterministic_plan_used", "using deterministic fallback plan", correlation_id, tenant_id, degraded=True)
            return deterministic_plan(request, plan_version)

    def _execute_plan(
        self,
        plan: Plan,
        request: str,
        run_id: str,
        correlation_id: str,
        tenant_id: str,
    ) -> list[Observation]:
        results: list[Observation] = []

        def run_step(step: Step) -> Observation:
            self.approval_service.authorize(step)
            idempotency_key = make_idempotency_key(run_id, plan.plan_version, step, request)
            log("step_start", f"executing {step.step_id}", correlation_id, tenant_id)
            if step.tool_name == "synthesizer":
                content = f"Synthesized action summary for '{request}' with idempotency_key={idempotency_key[:8]}"
                return Observation(step_id=step.step_id, tool_name=step.tool_name, content=content, sufficient=True)
            observation = retry(
                lambda: self.tool_gateway.execute(step, request),
                retries=2,
                base_delay_s=0.05,
                max_delay_s=0.20,
            )
            assert isinstance(observation, Observation)
            return observation

        with ThreadPoolExecutor(max_workers=min(4, len(plan.steps))) as pool:
            future_map = {pool.submit(run_step, step): step for step in plan.steps}
            for future, step in list(future_map.items()):
                try:
                    results.append(future.result())
                except PermanentError as exc:
                    raise exc
                except TransientError:
                    log("step_degraded", f"{step.step_id} exhausted retries", correlation_id, tenant_id, degraded=True)

        results.sort(key=lambda item: item.step_id)
        return results

    def _replan(
        self,
        plan: Plan,
        observations: Sequence[Observation],
        request: str,
        correlation_id: str,
        tenant_id: str,
    ) -> Plan:
        missing_steps = [
            Step(step.step_id, step.description + " via fallback source", step.tool_name)
            for step in plan.steps
            if step.step_id not in {observation.step_id for observation in observations}
        ]
        if not missing_steps:
            log("replan_short_circuit", "no missing steps, using deterministic plan", correlation_id, tenant_id, degraded=True)
            return deterministic_plan(request, plan.plan_version + 1)
        return Plan(plan_version=plan.plan_version + 1, steps=missing_steps + [Step("step-z", "summarize action path", "synthesizer")], final_goal=request)

    def _compose_answer(self, plan: Plan, observations: Sequence[Observation]) -> str:
        evidence = " | ".join(f"{obs.tool_name}: {obs.content}" for obs in observations)
        return f"Plan v{plan.plan_version} verified for '{plan.final_goal}'. Evidence: {evidence}"

    def _deterministic_answer(self, observations: Sequence[Observation], request: str) -> str:
        if not observations:
            return f"Degraded response for '{request}': no verified evidence available."
        evidence = "; ".join(f"{obs.tool_name}={obs.content}" for obs in observations)
        return f"Degraded response for '{request}': {evidence}"


if __name__ == "__main__":
    knowledge_base = {
        "lookup customer contract": "Customer contract allows address updates but not plan downgrades during active disputes.",
        "check compliance policy": "Compliance policy requires manual review for changes affecting regulated accounts.",
    }
    runtime = PlanningRuntime(
        tool_gateway=ToolGateway(knowledge_base),
        approval_service=ApprovalService(),
        primary_planner=PrimaryPlanner(),
        secondary_planner=SecondaryPlanner(),
        verifier=Verifier(),
        max_replans=1,
    )

    response = runtime.run(
        request="Can we update the customer address for account A-19 without changing the billing plan?",
        tenant_id="acme",
    )
    print(response)
```

This code is intentionally service-shaped rather than notebook-shaped. It shows where retries belong, how verifier-driven replanning is bounded, how fallback planning works when the primary planner degrades, and how side effects are kept replay-safe with idempotency keys.

## 6. Architectural System Design Scenarios

### Scenario 1: Regulated operations copilot with approval-gated replanning

**Problem statement**

Design a multi-tenant operations copilot for a financial-services platform handling `25k` requests/min. It answers account-change questions, proposes next actions, and may trigger privileged updates after approval. The system must keep `p99 <= 8.0s`, preserve a full audit trail, and prevent unapproved side effects even when replanning occurs.

**Proposed architecture**

```text
┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐
│ Agent UI/API │-> │ Policy Edge  │-> │ Planner / Verifier   │
└──────────────┘   └──────────────┘   └──────────┬───────────┘
                                                 v
                                      ┌──────────────────────┐
                                      │ Executor Tool Proxy  │
                                      │ CRM / policy / KYC   │
                                      └──────────┬───────────┘
                                                 v
                                      ┌──────────────────────┐
                                      │ Approval Workflow    │
                                      │ Temporal + audit log │
                                      └──────────┬───────────┘
                                                 v
                                      ┌──────────────────────┐
                                      │ Write APIs / Ledger  │
                                      └──────────────────────┘
```

Technology choices:

- strong planner model for initial decomposition and exception handling
- bounded executors for CRM, policy, and KYC checks
- verifier node for evidence sufficiency and policy compliance
- durable approval workflow before any external write

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Pure ReAct with direct tools | Medium | High | Low-Medium | Low | Medium |
| Planner + verifier + approval gates | Medium-High | Medium | High | Very High | High |
| Deterministic workflow only | Low-Medium | Low | Medium | Very High | Medium |

**Decision rationale**

Choose `planner + verifier + approval gates`. Pure `ReAct` is too risky because it interleaves reasoning and action with weaker governance boundaries. A deterministic workflow is safer but too rigid for messy account exceptions. The recommended design keeps flexible planning while ensuring that writes happen only after verification, authorization, and durable approval logging.

### Scenario 2: Enterprise investigation engine with parallel decomposition and bounded replanning

**Problem statement**

Design an internal investigation system that analyzes incidents, tickets, and compliance notes across regions. Analysts ask multi-part questions such as "what combination of vendor outages and policy exceptions caused refund escalations last quarter?" The system must support parallel evidence gathering, tolerate tool outages, and keep `p95 <= 7.0s` for synchronous investigations.

**Proposed architecture**

```text
┌──────────────┐   ┌────────────────┐   ┌────────────────────────┐
│ Analyst UI    │-> │ Query Classifier│-> │ DAG Planner / Scheduler │
└──────────────┘   └────────────────┘   └──────────┬─────────────┘
                                                   v
                                 ┌────────────────────────────────────┐
                                 │ Parallel Executors                 │
                                 │ search / incident DB / ticket API  │
                                 └──────────┬───────────────┬─────────┘
                                            v               v
                                 ┌────────────────┐  ┌────────────────┐
                                 │ Verifier Node   │  │ Replay Store    │
                                 └────────┬───────┘  └────────────────┘
                                          v
                                 ┌────────────────────────────────────┐
                                 │ Replanner / Deterministic Fallback │
                                 └────────────────────────────────────┘
```

Technology choices:

- dependency-aware DAG planner for independent evidence branches
- parallel read-only executors with per-branch deadlines
- verifier node for source sufficiency and contradiction detection
- replay store for checkpointed observations and bounded replanning

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Serial ReAct | Medium | High | Low | High | Medium |
| Planner + serial executors | Medium | Medium-High | Medium | High | Medium-High |
| Parallel DAG planner + verifier | High | Medium | High | High | Very High |

**Decision rationale**

Choose `parallel DAG planner + verifier`. The workload is dominated by independent evidence lookups, so serial `ReAct` wastes time on repeated planner turns and a longer critical path. Planner-plus-serial execution improves structure but leaves too much latency on the table. The DAG design wins because it shortens makespan, preserves explicit verification, and still allows deterministic fallback when one evidence branch fails.

## Sources

- [1] https://arxiv.org/abs/2210.03629 - ReAct paper on interleaved reasoning and acting.
- [2] https://research.google/blog/react-synergizing-reasoning-and-acting-in-language-models/ - Google Research summary of ReAct.
- [3] https://developers.openai.com/api/docs/guides/agents/running-agents - Agent runtime loop and continuation model.
- [4] https://openai.github.io/openai-agents-python/running_agents/ - OpenAI Agents SDK loop semantics and durable-execution integrations.
- [5] https://docs.langchain.com/oss/python/langgraph/graph-api - LangGraph super-step execution, routing, and recursion limits.
- [6] https://www.langchain.com/blog/planning-agents - Planner/executor and replanner design.
- [7] https://doi.org/10.48550/arxiv.2312.04511 - LLMCompiler benchmark and DAG planning.
- [8] https://docs.langchain.com/oss/python/langgraph/agentic-rag - Retrieval grading, rewrite, and generation loop.
- [9] https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview - Query decomposition, activity log, and cost example.
- [10] https://developers.openai.com/api/docs/guides/reasoning - Reasoning tokens, effort controls, and incomplete-response behavior.
- [11] https://platform.claude.com/cookbook/extended-thinking-extended-thinking-with-tool-use - Thinking blocks and tool-loop state requirements.
- [12] https://openai.github.io/openai-agents-python/usage/ - Usage accounting and reasoning token fields.
- [13] https://docs.crewai.com/en/concepts/flows - Flow persistence, control primitives, and metrics.
- [14] https://docs.langchain.com/oss/python/langgraph/checkpointers - Checkpoints, pending writes, and replay semantics.
- [15] https://docs.langchain.com/oss/python/langgraph/persistence - Persistence model for graph state.
- [16] https://openai.github.io/openai-agents-python/sessions/ - Session persistence and history shaping.
- [17] https://openai.github.io/openai-agents-python/human_in_the_loop/ - Approval pause and resume semantics.
- [18] https://adk.dev/sessions/ - Session, state, and memory model.
- [19] https://adk.dev/sessions/session/ - Session-service locking and persistence.
- [20] https://developers.openai.com/api/docs/guides/function-calling - Function-calling state and strict-schema guidance.
- [21] https://developers.openai.com/api/docs/guides/structured-outputs - Structured outputs and refusal/schema behavior.
- [22] https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use - Strict schema-valid tool use.
- [23] https://developers.openai.com/api/docs/guides/agent-builder-safety - Prompt-injection and agent safety guidance.
- [24] https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks - Handling untrusted content in agent loops.
- [25] https://openai.github.io/openai-agents-python/mcp/ - MCP integration and approval support.
- [26] https://modelcontextprotocol.io/specification/draft/basic/authorization - MCP OAuth-based authorization profile.
- [27] https://modelcontextprotocol.io/specification/draft/basic/authorization/security-considerations - PKCE, issuer validation, and auth security requirements.
- [28] https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT - Runaway-loop troubleshooting.
- [29] https://proceedings.mlr.press/v267/patil25a.html - BFCL long-horizon decision-making benchmark.
- [30] https://aclanthology.org/2024.tacl-1.9/ - Long-context degradation benchmark.
- [31] https://arxiv.org/abs/2404.06654 - RULER long-context benchmark.
