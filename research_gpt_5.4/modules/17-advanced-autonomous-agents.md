# Advanced - Autonomous Agents, Long-Horizon Tasks, Agent Environments

## 1. System Topology & Data Flow

Advanced autonomous agents are not "one smarter loop." The durable enterprise shape is a control plane that owns planning, policy, replay, approvals, and checkpointing, plus a data plane that owns model inference and environment interaction. Long-horizon reliability comes from keeping workflow continuity outside volatile environments such as browsers, containers, and remote tool servers.

```text
┌──────────────────────────────── Control Plane ────────────────────────────────┐
│ API Edge -> AuthN/Z -> Policy Router -> Planner / Supervisor                 │
│    │            │            │                    │                           │
│    │            │            │                    ├─> Verifier / Replanner   │
│    │            │            │                    ├─> Approval Service        │
│    │            │            │                    ├─> Retry / Deadline Engine │
│    │            │            │                    └─> Cost / Token Governor   │
│    └──────────────────────────────> Correlation ID / Tenant / Run Budget     │
└───────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      v
┌────────────────────────────── Durable Workflow ───────────────────────────────┐
│ Checkpoint Store | Run State | Idempotency Keys | Pending Writes | DLQ       │
│ Temporal / queue / DB history keeps workflow state independent of env state  │
└───────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      v
┌───────────────────────────────── Data Plane ──────────────────────────────────┐
│ Model Runtime <-> Tool Proxy / MCP Gateway <-> API Workers / Retrieval /     │
│      │                          │                     Code Sandbox / Browser  │
│      │                          ├─> RBAC / PII Filter / Secrets Broker       │
│      └─> Planner tier / executor tier / fallback tier                        │
└───────────────────────────────────────────────────────────────────────────────┘
        │                           │                              │
        v                           v                              v
┌──────────────────┐     ┌────────────────────────┐     ┌───────────────────────┐
│ Persistence      │     │ Environment State      │     │ Telemetry / Audit     │
│ sessions/memory  │     │ tabs/files/cache/docs  │     │ logs/traces/metrics   │
│ object store     │     │ remote server metadata │     │ immutable event chain │
└──────────────────┘     └────────────────────────┘     └───────────────────────┘
```

### Request-flow narrative

1. `API Edge` authenticates the tenant, issues a `correlation_id`, and starts deadline plus cost budgets.
2. `Policy Router` classifies the request into a bounded topology: direct answer, planner/executor, verifier/replanner, or async durable job.
3. `Planner / Supervisor` creates a run graph with explicit step boundaries, environment permissions, fallback policy, and human-approval checkpoints.
4. `Durable Workflow` persists the initial plan before side effects begin. This is the authority for resume, replay, and branch lineage.
5. `Tool Proxy / MCP Gateway` validates schemas, enforces least privilege, injects short-lived credentials, and records every environment hop.
6. `API workers`, `retrieval services`, `sandboxes`, or `browser executors` perform the actual work. Their outputs return as low-trust observations, not as trusted policy.
7. `Verifier / Replanner` decides whether the run should continue, branch, degrade, request approval, or terminate. Each transition writes a checkpoint and an audit event.

The non-obvious boundary is `workflow state` versus `environment state`. A browser tab, container filesystem, or MCP server session can disappear or drift. The run must still be recoverable because the durable workflow remembers what was attempted, what external effects were confirmed, and what remains safe to retry.

## 2. Core Mechanics & Algorithms

### Long-horizon autonomy as a guarded state machine

```text
ACCEPT
  -> CLASSIFY
  -> PLAN
  -> SCHEDULE
  -> EXECUTE
  -> VERIFY
     -> COMPLETE   if success criteria are satisfied
     -> REPLAN     if evidence is weak and budget remains
     -> APPROVAL   if policy requires human confirmation
     -> DEGRADE    if a non-critical environment is unhealthy
     -> FAIL       if deadline, safety, or correctness invariants break
```

This state machine is stronger than a naive ReAct loop because it externalizes stop conditions and replay boundaries. A pure serial loop often hides whether the system is still progressing, merely thinking, or silently duplicating work.

### Topology patterns

#### Planner / executor

One higher-capability model plans a bounded set of steps, and cheaper workers execute them. This reduces expensive serial replanning and gives policy enforcement a clean place to sit.

Approximate behavior:

- latency: `O(L_plan + critical_path(executor_steps))`
- token cost: planner cost plus executor turns, not planner cost times every turn
- fit: best for deterministic back-office workflows with stable APIs or MCP tools

#### Verifier / replanner

The system alternates between execution and explicit quality checks. This is useful when the agent can gather evidence but must prove sufficiency before moving on.

Approximate behavior:

- latency: `O(L_plan + sum(L_exec_i + L_verify_i))`
- correctness: higher than unconstrained loops when acceptance tests are strict
- risk: replanning storms unless `max_replans`, deadlines, and confidence thresholds are explicit

#### Parallel DAG scheduling

Independent subtasks become a dependency graph rather than a serial conversation.

Approximate behavior:

```text
makespan ~= planning_time + max(parallel_branch_durations) + join_overhead
```

This is the core reason long-horizon systems can outperform classic ReAct. When evidence collection, search, or API reads are independent, parallelism reduces both latency and repeated context growth.

#### Supervisor with bounded specialists

A supervisor assigns scoped work to retrieval, coding, browser, or analytics specialists. This helps when different environments need different prompts, policies, and failure handling.

Operational rule: the supervisor should own orchestration, but not become a hidden second workflow engine. The durable workflow remains the source of truth for retries and run lineage.

### Environment model

Enterprise agent environments form a capability hierarchy:

1. `API/function tools`: narrowest authority, easiest validation, cheapest observation footprint
2. `MCP servers`: structured interoperability with explicit auth and resource boundaries
3. `Retrieval environments`: strong for evidence gathering, but require permission-aware grounding
4. `Code sandboxes / containers`: useful for bounded computation and transformation
5. `Browser / computer environments`: most flexible, but highest token overhead and highest drift risk

The strategic rule is simple: prefer the narrowest environment that can complete the step.

### Complexity and convergence

For a run with `s` serial steps, `b` parallel branches, and `r` replans:

```text
serial_react_latency ~= s * (L_model + L_env)

dag_latency ~= L_plan + max(branch_i_latency) + L_join

token_growth ~= plan_tokens
              + executor_tokens
              + verifier_tokens
              + checkpoint_context
              + environment_observation_tokens
```

Key invariants:

- every side-effecting step carries an idempotency key derived from `run_id + step_id`
- workflow checkpoints occur only at semantically valid resume boundaries
- environment observations remain low-trust evidence, not silent policy updates
- memory writes require validation and scope controls
- long-horizon runs must have explicit caps on `max_steps`, `max_replans`, deadline, and spend

Convergence properties:

- planner/executor converges when the plan is bounded and step completion is externally testable
- verifier/replanner converges when success criteria tighten faster than evidence ambiguity grows
- DAG systems converge when fan-out is bounded and joins do not reintroduce unbounded serial debate
- browser-heavy systems converge least reliably because the environment itself is a mutable state machine

## 3. Token Economics & NFR Analysis

> ⚠️ Gap: The local `research_cursor` corpus is strong on relative cost drivers and topology trade-offs, but thin on stable published end-to-end SLA distributions for long-horizon autonomous runs. The targets below are recommended internal SLOs, not vendor guarantees.

### Cost formulas

Assumptions:

- `runs = 1000`
- `steps = average executable steps per run`
- `plan_in`, `plan_out`, `exec_in`, `exec_out`, `verify_in`, `verify_out` are token counts per stage
- `cache_prefix` is the stable prompt/tool prefix reused across executor turns
- `env_fixed_tokens` is environment overhead per step
- `P_*` terms are unit prices for the selected model tiers

#### Planner / executor with prompt caching

```text
$ per 1k runs =
1000 * (
  (plan_in / 1_000_000) * P_plan_in +
  (plan_out / 1_000_000) * P_plan_out +
  (cache_prefix / 1_000_000) * P_exec_cache_write +
  (((steps - 1) * cache_prefix) / 1_000_000) * P_exec_cache_read +
  ((steps * exec_in) / 1_000_000) * P_exec_in +
  ((steps * exec_out) / 1_000_000) * P_exec_out +
  tool_fees_per_run
)
```

This is the preferred enterprise baseline because it amortizes the expensive planner over the whole run and keeps executor turns on a cheaper tier.

#### Verifier / replanner loop

```text
$ per 1k runs =
1000 * (
  planner_executor_cost_per_run +
  ((replans * verify_in) / 1_000_000) * P_verify_in +
  ((replans * verify_out) / 1_000_000) * P_verify_out +
  ((replans * replan_in) / 1_000_000) * P_replan_in +
  ((replans * replan_out) / 1_000_000) * P_replan_out
)
```

The economic danger is not one expensive call. It is repeated verification and replanning on the same partially useful evidence.

#### Browser or computer-use overhead

The local notes make environment overhead explicit: browser tool declarations contribute about `6610-6670` input tokens, and computer tool declarations add about `4520-4590` tokens before screenshots or task-specific context.

```text
$ per 1k runs =
1000 * (
  planner_executor_cost_per_run +
  ((browser_steps * browser_decl_tokens) / 1_000_000) * P_exec_in +
  ((screenshot_tokens_total) / 1_000_000) * P_exec_in +
  browser_infra_fees_per_run
)
```

The practical implication is that browser-first autonomy starts with a materially higher cost floor than API-first autonomy even before action retries or visual drift are considered.

#### Capacity formulas

```text
completed_runs_per_second
  ~= min(
       model_tpm / avg_total_tokens_per_run,
       worker_pool_size / avg_critical_path_seconds,
       env_qps_limit / avg_env_calls_per_run
     )
```

```text
critical_path_latency
  ~= planning_time
   + max(parallel_branch_durations)
   + approval_wait
   + verification_time
   + persistence_and_trace_overhead
```

### Latency targets

Recommended run-level targets by environment class:

- `API / MCP-first operator`: `p50 <= 6s`, `p95 <= 20s`, `p99 <= 45s`
- `retrieval-heavy research agent`: `p50 <= 15s`, `p95 <= 60s`, `p99 <= 180s`
- `browser-last-resort operator`: `p50 <= 45s`, `p95 <= 240s`, `p99 <= 720s`

Mitigations by percentile:

- `p50`: stable prompt prefixes, warm worker pools, DAG parallelism, cached tool schemas, streaming first token
- `p95`: bounded fan-out, per-environment deadlines, transcript compaction, checkpoint reuse, queue-aware routing
- `p99`: admission control, breaker-driven degradation, skip non-critical steps, human takeover path, replay from last good checkpoint instead of whole-run restart

### Throughput and back-pressure

Long-horizon systems saturate first on orchestration shape, not just model QPS. The dominant bottlenecks are usually:

- worker concurrency for environment calls
- token throughput for repeated observations and verifier turns
- slowest remote boundary in the critical path

Back-pressure policy should be explicit:

1. cap concurrent durable runs per tenant
2. cap concurrent environment sessions such as browsers or sandboxes
3. shed low-priority work before high-priority incident or operations runs
4. degrade from browser to API/retrieval-only evidence collection when the browser pool is saturated
5. reject new replans when the run has already crossed cost or latency budgets

### Availability, RPO, RTO, and compliance

Recommended targets:

- `Availability`: `99.9%` for internal autonomy, `99.95%` for customer-facing assisted execution
- `RPO`: `<= 5 min` for run state, `0` for acknowledged audit events
- `RTO`: `<= 15 min` for workflow failover, `<= 60 min` for browser/sandbox fleet recovery

Compliance requirements:

- separate tenant identity from agent identity at the tool boundary
- store only redacted or tokenized sensitive spans in telemetry
- retain lineage for `plan -> action -> observation -> decision -> output`
- apply residency and vendor-boundary controls when a run crosses external model or tool providers

## 4. Distributed Resilience & Security

### Durable execution

Durability is the foundation of long-horizon autonomy. The safest pattern is:

- `Temporal` or equivalent workflow engine owns run history, checkpoint replay, timers, and approval pauses
- `Kafka` or a queue buffer absorbs external events, retries, and asynchronous tool completions
- each activity writes a checkpoint keyed by `run_id`, `step_id`, and external idempotency key
- side effects commit only after the workflow records both intent and confirmed external result
- poison steps move to a `DLQ` with enough metadata for targeted replay or operator triage

The control rule is `durable workflow continuity above replaceable environments`. If a browser tab disappears or an MCP server restarts, the workflow still knows what was done, what is safe to retry, and where manual intervention is required.

### Failure taxonomy

`Transient failures`

- network flaps
- rate limits and `429`
- auth token refresh races
- temporary browser pool exhaustion
- sandbox cold-start timeouts

`Permanent failures`

- schema mismatch
- missing entitlements
- forbidden actions under RBAC
- malformed inputs or unsupported file types
- impossible plans generated from stale system metadata

`Poison-pill failures`

- one task payload always crashes a worker
- one retrieved artifact repeatedly poisons the prompt or verifier
- one browser flow always lands on an anti-automation wall

`Correctness failures`

- replay duplicates a non-idempotent external action
- planner assumes environment state that no longer exists
- memory poisoning silently changes future behavior
- verifier accepts weak evidence and the run terminates incorrectly

### Retry, locking, and replay policy

Retries belong only on transient and idempotent boundaries.

- tool read operations: `2-3` retries with exponential backoff and jitter
- tool writes: retry only with idempotency keys and external effect confirmation
- planner/model transport errors: retry once, then fail over to a smaller or cheaper tier
- browser actions: retry only after a fresh observation; never blindly replay stale clicks

Distributed locking guidance:

- use workflow-level ownership for the run
- use row or lease locks for mutable shared session records
- never rely on a browser tab or container process itself as the lock authority

### Circuit breakers and graceful degradation

```text
CLOSED
  -> OPEN       after timeout or error threshold breach
  -> HALF_OPEN  after cooldown
  -> CLOSED     after healthy probe window
  -> OPEN       if probe fails
```

Graceful degradation chain:

1. `planner + executor + verification + primary environment`
2. `planner + executor + verification + secondary environment`
3. `planner + executor + deterministic evidence bundle`
4. `pause for human takeover` if the action surface is unsafe without the failed environment

This is the enterprise distinction between autonomy and automation theater: the system remains truthful and partially useful under partial outages instead of pretending success.

### Enterprise security controls

Zero-Trust `MCP` pattern:

- all environment calls terminate at a policy proxy, not directly from the model
- the proxy injects short-lived scoped credentials
- resource-bound tokens and server metadata remain outside the prompt
- approvals are enforced at the proxy or workflow boundary, not by prompt instructions alone

Tool-level `RBAC`:

- map `user role + tenant + run type + tool action` to a least-privilege policy
- split read, propose, approve, and commit privileges into separate actions
- record deny decisions with reason codes and correlation IDs

PII filtering pipeline:

1. detect sensitive fields before storage or retrieval
2. redact or tokenize high-risk spans before model exposure
3. re-check environment outputs before memory writes or prompt assembly
4. persist the redaction decision, actor, and policy version in an audit trail

Auditability and chain of custody:

- immutable event log for every plan, action, retry, fallback, and approval
- lineage from final output back to exact evidence and tool responses used
- signed or append-only storage for regulated investigations
- explicit branch lineage for replans and human overrides

> ⚠️ Gap: The local research set is prescriptive about Zero-Trust boundaries and memory poisoning, but does not provide a canonical enterprise-wide audit schema or a definitive comparison across container, VM, and WASM isolation for all agent environments.

## 5. Production Enterprise Code

The example below is a runnable Python skeleton for a durable autonomous runtime. It demonstrates retries with exponential backoff and jitter, circuit breakers, a primary-to-secondary-to-deterministic model fallback chain, structured logging with correlation IDs, checkpointing, and graceful degradation when a browser environment is unavailable.

```python
from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Sequence


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class CircuitOpenError(Exception):
    pass


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time_ms": int(record.created * 1000),
        }
        for key in ("event", "correlation_id", "tenant_id", "run_id", "degraded"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, separators=(",", ":"))


def build_logger() -> logging.Logger:
    logger = logging.getLogger("autonomous_runtime")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOGGER = build_logger()


def log(event: str, message: str, correlation_id: str, tenant_id: str, run_id: str, **extra: object) -> None:
    LOGGER.info(
        message,
        extra={
            "event": event,
            "correlation_id": correlation_id,
            "tenant_id": tenant_id,
            "run_id": run_id,
            **extra,
        },
    )


def retry(
    fn: Callable[[], str],
    retries: int,
    base_delay_s: float,
    max_delay_s: float,
) -> str:
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


@dataclass(frozen=True)
class Step:
    step_id: str
    env: str
    action: str
    critical: bool = True


@dataclass
class Checkpoint:
    status: str
    completed_steps: List[str] = field(default_factory=list)
    observations: List[str] = field(default_factory=list)


class CheckpointStore:
    def __init__(self) -> None:
        self._store: Dict[str, Checkpoint] = {}

    def load(self, run_id: str) -> Checkpoint:
        return self._store.setdefault(run_id, Checkpoint(status="accepted"))

    def save(self, run_id: str, checkpoint: Checkpoint) -> None:
        self._store[run_id] = checkpoint


class Environment:
    def execute(self, action: str) -> str:
        raise NotImplementedError


class ApiEnvironment(Environment):
    def execute(self, action: str) -> str:
        if "api_timeout" in action:
            raise TransientError("temporary API timeout")
        return f"api:{action}:ok"


class RetrievalEnvironment(Environment):
    def execute(self, action: str) -> str:
        return f"retrieval:{action}:evidence_bundle_ready"


class BrowserEnvironment(Environment):
    def execute(self, action: str) -> str:
        if "vendor_portal" in action:
            raise TransientError("browser session drift detected")
        return f"browser:{action}:ok"


class Model:
    def generate(self, prompt: str) -> str:
        raise NotImplementedError


class PrimaryModel(Model):
    def generate(self, prompt: str) -> str:
        if "force_primary_fail" in prompt:
            raise TransientError("primary model transport failure")
        return f"PRIMARY PLAN/REPORT: {prompt}"


class SecondaryModel(Model):
    def generate(self, prompt: str) -> str:
        if "force_secondary_fail" in prompt:
            raise TransientError("secondary model transport failure")
        return f"SECONDARY PLAN/REPORT: {prompt}"


def deterministic_fallback(prompt: str) -> str:
    return f"DETERMINISTIC FALLBACK: {prompt}"


class ModelChain:
    def __init__(self, primary: Model, secondary: Model) -> None:
        self.primary = primary
        self.secondary = secondary
        self.breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_s=10.0)

    def generate(self, prompt: str) -> tuple[str, bool]:
        if self.breaker.allow():
            try:
                result = self.primary.generate(prompt)
                self.breaker.record_success()
                return result, False
            except TransientError:
                self.breaker.record_failure()
        try:
            return self.secondary.generate(prompt), True
        except TransientError:
            return deterministic_fallback(prompt), True


class Planner:
    def __init__(self, model_chain: ModelChain) -> None:
        self.model_chain = model_chain

    def plan(self, goal: str) -> tuple[List[Step], bool]:
        _, degraded = self.model_chain.generate(f"plan for goal={goal}")
        if "vendor" in goal.lower():
            return [
                Step("s1", "retrieval", "load_vendor_policy", critical=True),
                Step("s2", "browser", "vendor_portal_collect_documents", critical=False),
                Step("s3", "api", "create_review_case", critical=True),
            ], degraded
        return [
            Step("s1", "retrieval", "load_account_context", critical=True),
            Step("s2", "api", "reconcile_open_exceptions", critical=True),
            Step("s3", "api", "write_resolution_notes", critical=True),
        ], degraded


class AutonomousRuntime:
    def __init__(self) -> None:
        self.checkpoints = CheckpointStore()
        self.environments = {
            "api": ApiEnvironment(),
            "retrieval": RetrievalEnvironment(),
            "browser": BrowserEnvironment(),
        }
        self.breakers = {
            "api": CircuitBreaker(failure_threshold=2, recovery_timeout_s=5.0),
            "retrieval": CircuitBreaker(failure_threshold=2, recovery_timeout_s=5.0),
            "browser": CircuitBreaker(failure_threshold=1, recovery_timeout_s=20.0),
        }
        self.model_chain = ModelChain(PrimaryModel(), SecondaryModel())
        self.planner = Planner(self.model_chain)

    def run(self, tenant_id: str, goal: str) -> dict[str, object]:
        correlation_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        checkpoint = self.checkpoints.load(run_id)
        degraded = False

        log("run_start", "starting autonomous run", correlation_id, tenant_id, run_id)
        plan, plan_degraded = self.planner.plan(goal)
        degraded = degraded or plan_degraded
        checkpoint.status = "planned"
        self.checkpoints.save(run_id, checkpoint)

        for step in plan:
            if step.step_id in checkpoint.completed_steps:
                continue
            result = self._execute_step(step, tenant_id, correlation_id, run_id)
            if result is None:
                if step.critical:
                    checkpoint.status = "paused_for_human"
                    self.checkpoints.save(run_id, checkpoint)
                    report, _ = self.model_chain.generate(
                        f"run paused; goal={goal}; observations={checkpoint.observations}"
                    )
                    return {
                        "run_id": run_id,
                        "status": checkpoint.status,
                        "degraded": True,
                        "report": report,
                        "observations": checkpoint.observations,
                    }
                degraded = True
                checkpoint.observations.append(f"{step.step_id}:degraded_skip")
                continue

            checkpoint.completed_steps.append(step.step_id)
            checkpoint.observations.append(result)
            checkpoint.status = "executing"
            self.checkpoints.save(run_id, checkpoint)

        checkpoint.status = "completed"
        self.checkpoints.save(run_id, checkpoint)
        report, report_degraded = self.model_chain.generate(
            f"goal={goal}; observations={checkpoint.observations}"
        )
        degraded = degraded or report_degraded
        log("run_complete", "completed autonomous run", correlation_id, tenant_id, run_id, degraded=degraded)
        return {
            "run_id": run_id,
            "status": checkpoint.status,
            "degraded": degraded,
            "report": report,
            "observations": checkpoint.observations,
        }

    def _execute_step(self, step: Step, tenant_id: str, correlation_id: str, run_id: str) -> str | None:
        breaker = self.breakers[step.env]
        if not breaker.allow():
            log(
                "step_circuit_open",
                f"circuit open for {step.env}",
                correlation_id,
                tenant_id,
                run_id,
                degraded=True,
            )
            return None

        try:
            result = retry(
                lambda: self.environments[step.env].execute(step.action),
                retries=2,
                base_delay_s=0.05,
                max_delay_s=0.25,
            )
            breaker.record_success()
            log("step_success", f"step {step.step_id} completed", correlation_id, tenant_id, run_id)
            return result
        except TransientError:
            breaker.record_failure()
            log(
                "step_transient_failure",
                f"step {step.step_id} degraded",
                correlation_id,
                tenant_id,
                run_id,
                degraded=True,
            )
            return None
        except PermanentError:
            log(
                "step_permanent_failure",
                f"step {step.step_id} failed permanently",
                correlation_id,
                tenant_id,
                run_id,
                degraded=True,
            )
            return None


if __name__ == "__main__":
    runtime = AutonomousRuntime()
    finance_result = runtime.run(
        tenant_id="acme",
        goal="Reconcile daily finance exceptions",
    )
    print(json.dumps(finance_result, indent=2))

    vendor_result = runtime.run(
        tenant_id="acme",
        goal="Collect vendor onboarding evidence from vendor portal",
    )
    print(json.dumps(vendor_result, indent=2))
```

This snippet deliberately separates checkpoint state from environment state. The browser step can fail and degrade without corrupting the workflow, the planner can fall back to smaller or deterministic behavior, and every major event is logged with a `correlation_id` and `run_id`.

## 6. Architectural System Design Scenarios

### Scenario 1: Multi-tenant finance exception operator

**Problem statement**

Design an internal autonomous operations system that clears finance exceptions across ERP, billing, and CRM systems for `25k` tasks per day. The system must preserve audit trails, tolerate multi-hour pauses for approvals, and keep `p95 <= 20s` for API-first runs with `p99 <= 45s`.

**Proposed architecture**

```text
+-------------+   +----------------+   +-----------------------------+
| Ops Console  |-> | Policy Router  |-> | Planner / Durable Workflow  |
+-------------+   +----------------+   +-------------+---------------+
                                                       |
                                                       v
                              +-----------------------------------------------+
                              | MCP / Tool Proxy                              |
                              | ERP API | Billing API | CRM API | Approval    |
                              +-------------------+---------------------------+
                                                  |
                                                  v
                              +-----------------------------------------------+
                              | Checkpoints | Audit Ledger | Metrics | DLQ    |
                              +-----------------------------------------------+
```

Technology choices:

- workflow engine for multi-hour durability and approval pauses
- API-first execution through MCP or typed tool proxies
- low-cost executor tier for deterministic step execution
- immutable event ledger for every decision and external write

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Serial ReAct over raw APIs | Medium | High | Medium | Medium | Medium |
| Planner/executor + durable workflow | Medium | Medium | High | High | High |
| Browser automation over admin UIs | High | Very High | High | Low-Medium | Low |

**Decision rationale**

Choose `planner/executor + durable workflow`. The workload is structured, approval-heavy, and dominated by business APIs rather than visual interfaces. Serial ReAct wastes latency on repeated reasoning, while browser automation expands authority and failure surface for no benefit. The workflow-backed split gives clean checkpoints, replay safety, and lower cost variance.

### Scenario 2: Vendor due-diligence agent across retrieval and browser fallback

**Problem statement**

Design a regulated procurement assistant that gathers security questionnaires, sanctions checks, insurance documents, and vendor-portal evidence for enterprise onboarding. The system must support long-horizon runs that span hours, keep `p95 <= 60s` for retrieval-first cases, and degrade safely when a vendor portal changes unexpectedly.

**Proposed architecture**

```text
+----------------+   +----------------+   +----------------------------+
| Procurement UI |-> | Query Classify |-> | Supervisor / Workflow      |
+----------------+   +----------------+   +-------------+--------------+
                                                       |
                           +---------------------------+----------------------+
                           |                                                  |
                           v                                                  v
                +---------------------+                          +----------------------+
                | Retrieval / MCP     |                          | Browser Worker Pool  |
                | policy docs / lists |                          | isolated sessions    |
                +----------+----------+                          +----------+-----------+
                           |                                                |
                           +-------------------+----------------------------+
                                               v
                           +-----------------------------------------------+
                           | Evidence Store | PII Filter | Audit | Escalate |
                           +-----------------------------------------------+
```

Technology choices:

- retrieval-first evidence gathering for policy and sanctions sources
- browser pool only for API-less vendor portals
- verifier that checks evidence completeness before case closure
- escalation path when browser state drifts or a portal requires manual action

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Retrieval-only research agent | Low-Medium | Low-Medium | Medium | High | High |
| Retrieval-first + browser fallback | Medium-High | Medium-High | High | Medium-High | Medium-High |
| Browser-first operator | Very High | Very High | Very High | Low-Medium | Low |

**Decision rationale**

Choose `retrieval-first + browser fallback`. Most evidence is available through structured sources, so the browser should be reserved for the small portion of vendors that expose only a portal UI. This preserves the narrowest default authority, keeps token overhead lower, and gives the workflow a truthful degraded path when visual state becomes unreliable.

## Sources

- [1] `research_cursor/research/04-agent-architecture.md`
- [2] `research_cursor/research/05-agent-frameworks.md`
- [3] `research_cursor/research/06-rag.md`
- [4] `research_cursor/research/07-memory.md`
- [5] `research_cursor/research/08-planning-reasoning.md`
- [6] `research_cursor/research/09-multi-agent-systems.md`
- [7] `research_cursor/research/10-mcp-interoperability.md`
- [8] `research_cursor/research/11-specialized-agents.md`
- [9] `research_cursor/research/13-security-guardrails.md`
- [10] `research_cursor/research/14-observability.md`
- [11] `research_cursor/research/15-inference-optimization.md`
- [12] `research_cursor/research/16-production.md`
