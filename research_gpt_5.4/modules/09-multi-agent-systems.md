# Multi-Agent Systems — Supervisor, Worker, Collaboration, Delegation

## 1. System Topology & Data Flow

Production multi-agent systems are best treated as constrained distributed orchestration, not as "several chatbots talking to each other." The dominant enterprise pattern is still a centralized `supervisor` that owns policy, deadline, and final-answer synthesis, while bounded `workers` own narrow specialist tasks such as retrieval, calculation, compliance review, or remote delegation. `Handoffs` and peer collaboration are useful, but they widen the failure surface and should be reserved for cases where central control materially limits accuracy or ownership.

```text
┌────────────────────────────── Control Plane ───────────────────────────────┐
│ API Gateway -> AuthN/Z -> Policy Router -> Supervisor Runtime              │
│      │             │              │                  │                     │
│      │             │              │                  ├─ Task Planner       │
│      │             │              │                  ├─ Delegation Policy  │
│      │             │              │                  ├─ Approval Gate      │
│      │             │              │                  └─ Response Synth     │
│      └──────────────────────────> Correlation ID / Deadline / Tenant       │
└─────────────────────────────────────────────────────────────────────────────┘
                                 │
                                 v
┌────────────────────────────── Data Plane ──────────────────────────────────┐
│  Worker Pool                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐ │
│  │ Search       │  │ Planner      │  │ Compliance   │  │ Remote A2A     │ │
│  │ Worker       │  │ Worker       │  │ Worker       │  │ Worker / Mesh   │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────────┬───────┘ │
│         │                 │                 │                    │         │
│         └────────────┬────┴────────────┬────┴────────────┬──────┘         │
│                      v                 v                 v                  │
│               Tool Proxies / MCP Gateway / A2A Transport / Cache           │
└─────────────────────────────────────────────────────────────────────────────┘
            │                        │                          │
            v                        v                          v
┌────────────────────┐   ┌──────────────────────┐   ┌────────────────────────┐
│ Persistence Layer  │   │ Tool Boundary        │   │ Telemetry / Audit      │
│ workflow state     │   │ RBAC / allowlists    │   │ traces / metrics       │
│ task events        │   │ PII redact / scan    │   │ token ledger           │
│ checkpoints        │   │ approval policies    │   │ immutable decision log │
│ result cache       │   │ rate limits          │   │ SIEM / alerting        │
└────────────────────┘   └──────────────────────┘   └────────────────────────┘
```

### Request-flow narrative

1. `API Gateway` authenticates the tenant, stamps a `correlation_id`, and assigns a hard end-to-end deadline.
2. `Policy Router` classifies the request into one of three paths:
   - `supervisor + local workers` for bounded internal tasks
   - `handoff to specialist` when one agent must own the rest of the user turn
   - `remote delegation` when another team, vendor, or trust domain owns the capability
3. `Supervisor Runtime` creates a task graph with `max_depth`, `max_turns`, and `max_parallel_workers` limits so the run is convergent by construction.
4. Local workers receive only the minimal task payload rather than the full conversation transcript; this is the practical token and safety advantage of the supervisor-worker model.
5. Requests crossing a tool or remote-agent boundary pass through `Tool Proxies / MCP Gateway / A2A Transport`, where auth, approval, schema validation, and rate limits are enforced.
6. Every worker result is written to the `Persistence Layer` as an append-only task event with `task_id`, `parent_task_id`, `worker_id`, status, latency, and retry count.
7. `Response Synth` merges successful worker outputs, marks degraded branches explicitly, and returns either a full answer, a partial answer, or a safe escalation.
8. `Telemetry / Audit` captures delegation decisions, approval pauses, token usage, cache hits, tool invocations, and policy denials for both observability and chain-of-custody.

The main topology decision is where authority lives. In a supervisor-worker system, the supervisor owns policy and the final answer, and workers behave like tool-like bounded components. In a collaboration or A2A topology, authority becomes more distributed, which can improve specialization or organizational autonomy, but it also creates more retry surfaces, more auth edges, and more state-reconciliation work.

## 2. Core Mechanics & Algorithms

### Multi-agent orchestration as a guarded state machine

The operationally safe way to model delegation is as a bounded state machine:

```text
ACCEPT
  -> CLASSIFY
  -> PLAN
  -> DISPATCH_WORKERS
  -> COLLECT_RESULTS
     -> SYNTHESIZE            if required evidence is present
     -> RETRY_BRANCH          if failure is transient and retry budget remains
     -> FALLBACK_BRANCH       if worker/model/tool is unavailable
     -> ESCALATE_OR_APPROVE   if policy or side effects require review
     -> FAIL_CLOSED           if required controls cannot be satisfied
```

This model is more important than any specific framework because it encodes convergence. Without explicit transition guards, collaboration degenerates into delegation drift, supervisor-worker ping-pong, or hidden recursive routing.

### Delegation models

- `Supervisor as manager`: the parent agent calls workers as tools, owns the final answer, and centralizes policy. This is the most mature production pattern.
- `Handoff`: the current branch of the conversation transfers to a specialist that owns the rest of the turn. This improves specialist autonomy but weakens one-place policy enforcement.
- `Collaborative return`: a coordinator sends a bounded task to a specialist that automatically returns control when the task ends. This is safer than unconstrained peer chat because return semantics are part of the runtime.
- `Remote A2A mesh`: delegation crosses process or trust boundaries. At that point the system behaves like a microservice graph with LLM-driven routing rather than a nested function call tree.

### Routing, scheduling, and synthesis algorithms

For `n` candidate workers, a simple semantic or rules-plus-semantic router has a first-order routing cost of:

```text
routing_cost ~= O(n)
```

if the supervisor scores each worker description once. If each worker is further filtered by tenant policy, tool scope, or region, the real routing pipeline is:

```text
eligible_workers
  = workers
    filtered_by(tenant_policy, tool_scope, compliance_region)

route_decision_cost ~= O(|eligible_workers|)
```

Parallel delegation improves latency only when the worker branches are independent. The critical path is:

```text
critical_path_latency
  ~= routing
   + max(parallel_worker_branch_latency)
   + synthesis
   + approval_and_network_overhead
```

Token cost grows with every extra branch even if latency does not:

```text
total_tokens
  ~= supervisor_tokens
   + sum(worker_prompt_tokens + worker_output_tokens)
   + synthesis_tokens
   + duplicated_context_tokens
```

### Complexity and failure-relevant invariants

- `Bounded depth invariant`: every run must have `max_depth` and `max_turns`; otherwise recursive delegation can become an unbounded cost amplifier.
- `Single state owner invariant`: either the supervisor owns shared memory or each worker owns explicit scoped memory. Hidden shared state causes reconciliation bugs and broken replay.
- `Stable identity invariant`: every branch needs `run_id`, `task_id`, `parent_task_id`, `attempt`, and `worker_id` so retries and audits are deterministic.
- `Idempotency invariant`: tool calls and remote delegations must carry idempotency keys because workflow replay, queue redelivery, and manual resume are normal operating conditions.
- `Deadline invariant`: every child branch inherits a smaller deadline than its parent; no worker may outlive the parent request budget.

### Convergence properties

Well-behaved multi-agent systems converge when three limits are explicit:

```text
converges if:
  max_turns is finite
  max_parallel_workers is finite
  every retry path burns budget and decreases remaining deadline
```

In practice, the safest architecture is to keep workers mostly stateless and allow only one layer of meaningful delegation unless a second layer adds a clear coordination benefit. Nested hierarchies are usually an operations tax disguised as flexibility.

## 3. Token Economics & NFR Analysis

> ⚠️ Gap: The source set contains strong design guidance and some runtime limits, but it does not provide a stable vendor-independent price sheet or broadly reusable production percentile benchmarks. The formulas below are explicit and implementation-ready, while the `p50/p95/p99` numbers are recommended SLO targets rather than framework guarantees.

### Cost formulas

Assumptions:

- `runs = 1000`
- `S_u` = uncached supervisor input tokens per run
- `S_c` = cache-eligible supervisor prefix tokens per run
- `h` = prompt-cache hit rate for `S_c`
- `P_sup_in`, `P_sup_cache_in`, `P_sup_out` = supervisor model prices per 1M tokens
- `W_f`, `W_d` = number of fast-tier and deep-tier worker calls per run
- `F_in`, `F_out`, `D_in`, `D_out` = average tokens per fast/deep worker call
- `P_fast_in`, `P_fast_out`, `P_deep_in`, `P_deep_out` = worker model prices per 1M tokens
- `T_req` = average tool or remote-agent request charge per branch, if applicable
- `A` = average approval or human-review cost per run, if modeled financially

Effective cached supervisor input cost:

```text
supervisor_input_cost
  = (
      S_u * P_sup_in +
      S_c * ((1 - h) * P_sup_in + h * P_sup_cache_in)
    ) / 1_000_000
```

Supervisor-worker run cost:

```text
$ cost per 1k runs =
1000 * (
  supervisor_input_cost +
  (supervisor_output_tokens * P_sup_out) / 1_000_000 +
  W_f * ((F_in * P_fast_in + F_out * P_fast_out) / 1_000_000) +
  W_d * ((D_in * P_deep_in + D_out * P_deep_out) / 1_000_000) +
  (W_f + W_d) * T_req +
  A
)
```

Remote delegation introduces transport and duplicate-context overhead:

```text
$ remote cost per 1k runs =
1000 * (
  base_run_cost +
  remote_branches * transport_fee +
  remote_branches * (
    duplicated_remote_context_tokens * P_remote_in +
    remote_result_tokens * P_remote_out
  ) / 1_000_000
)
```

Interpretation:

- Increasing `W_f + W_d` raises cost almost linearly.
- High `h` makes the supervisor pattern materially cheaper because the router and policy prefix are often stable across runs.
- Remote delegation is usually justified by organizational or trust-boundary reasons, not by raw token efficiency.

### Worked numeric examples

To make the formulas auditable, use the following explicit example assumption set for `1000` runs:

- `S_u = 1500`
- `S_c = 6000`
- `supervisor_output_tokens = 350`
- `h = 0.80` for the cached case and `h = 0.00` for the uncached case
- `W_f = 2`, `F_in = 900`, `F_out = 180`
- `W_d = 1`, `D_in = 2400`, `D_out = 450`
- `P_sup_in = $3.00 / 1M`, `P_sup_cache_in = $0.75 / 1M`, `P_sup_out = $12.00 / 1M`
- `P_fast_in = $0.30 / 1M`, `P_fast_out = $1.20 / 1M`
- `P_deep_in = $3.00 / 1M`, `P_deep_out = $12.00 / 1M`
- `T_req = $0.0005` per worker branch
- `A = $0`

Cached supervisor input:

```text
supervisor_input_cost
  = (
      1500 * 3.00 +
      6000 * ((1 - 0.80) * 3.00 + 0.80 * 0.75)
    ) / 1_000_000
  = 11_700 / 1_000_000
  = $0.0117 per run
```

Cached local supervisor-worker total:

```text
$ cost per 1k runs
  = 1000 * (
      0.0117 +
      (350 * 12.00) / 1_000_000 +
      2 * ((900 * 0.30 + 180 * 1.20) / 1_000_000) +
      1 * ((2400 * 3.00 + 450 * 12.00) / 1_000_000) +
      3 * 0.0005
    )
  = 1000 * 0.030972
  = $30.97 per 1k runs
```

Uncached local supervisor-worker total:

```text
$ cost per 1k runs
  = 1000 * (
      ((1500 + 6000) * 3.00) / 1_000_000 +
      (350 * 12.00) / 1_000_000 +
      2 * ((900 * 0.30 + 180 * 1.20) / 1_000_000) +
      1 * ((2400 * 3.00 + 450 * 12.00) / 1_000_000) +
      3 * 0.0005
    )
  = 1000 * 0.041772
  = $41.77 per 1k runs
```

That means prompt caching saves:

```text
$41.77 - $30.97 = $10.80 per 1k runs
```

Remote delegation example with one additional remote branch:

- `remote_branches = 1`
- `transport_fee = $0.0015`
- `duplicated_remote_context_tokens = 1800`
- `remote_result_tokens = 350`
- `P_remote_in = $3.00 / 1M`
- `P_remote_out = $12.00 / 1M`

```text
$ remote cost per 1k runs
  = 1000 * (
      0.030972 +
      1 * 0.0015 +
      1 * ((1800 * 3.00 + 350 * 12.00) / 1_000_000)
    )
  = 1000 * 0.042072
  = $42.07 per 1k runs
```

Under this assumption set, the practical budget takeaway is concrete: a cached local supervisor-worker path is about `$30.97 / 1k runs`, the same path without cache reuse is about `$41.77 / 1k runs`, and introducing one remote delegated branch raises the cached case to about `$42.07 / 1k runs`.

### Latency targets

Recommended user-facing SLO envelopes:

- `Local supervisor + bounded workers`: `p50 <= 1.2s`, `p95 <= 3.5s`, `p99 <= 7.0s`
- `Deep reasoning with parallel workers`: `p50 <= 2.5s`, `p95 <= 8.0s`, `p99 <= 15.0s`
- `Remote A2A collaboration`: `p50 <= 4.0s`, `p95 <= 12.0s`, `p99 <= 25.0s`

Mitigations by percentile:

- `p50`: warm model sessions, cache stable supervisor prefixes, keep workers context-minimal, colocate tool proxies with the supervisor runtime
- `p95`: cap fan-out, run independent workers concurrently, pre-authorize low-risk tools, return on first sufficient evidence instead of waiting for all noncritical branches
- `p99`: per-branch deadlines, bulkheads for remote workers, queue admission control, degrade to a reduced-worker plan, and fail closed for blocked privileged actions

### Throughput and back-pressure

A multi-agent service does not scale on `requests/sec` alone. The first hard limit is usually branch fan-out against worker/model concurrency, tool concurrency, or approval bandwidth.

Useful planning heuristics:

```text
effective_qps
  <= min(
       supervisor_qps,
       worker_pool_qps / avg_worker_calls_per_run,
       tool_qps / avg_tool_calls_per_run,
       approval_qps / avg_approvals_per_run
     )
```

```text
branch_arrival_rate
  = ingress_qps * avg_worker_calls_per_run
```

```text
queue_pressure
  = branch_arrival_rate / branch_service_rate
```

If `queue_pressure > 1`, latency will explode before the system looks "down." Production back-pressure policy should therefore be explicit:

1. Reject or defer new low-priority runs when worker queue depth exceeds a threshold.
2. Reduce `max_parallel_workers` under sustained saturation.
3. Drop optional workers such as style review, enrichment, or secondary validation before dropping required workers.
4. Convert remote delegation to local deterministic fallback when approval or transport queues breach the deadline budget.

### Availability, RPO, RTO, and compliance

Recommended enterprise targets:

- `Overall service availability`: `99.9%` minimum, `99.95%` for tier-1 internal copilot paths
- `Workflow event store`: `99.99%` because replay, audit, and resume depend on it
- `RPO`: `<= 1 minute` for workflow events and approvals; `<= 15 minutes` for derived analytics
- `RTO`: `<= 15 minutes` for same-region recovery; `<= 60 minutes` for cross-region failover

Compliance posture should be designed per boundary:

- `SOC 2` / `ISO 27001`: control baseline for logging, change control, secrets, and least privilege
- `GDPR` / `CCPA`: data minimization, deletion workflows, residency-aware routing, and auditable lawful-purpose tagging for delegation payloads
- `HIPAA` or equivalent sector controls`: only if PHI enters prompts or tool outputs; requires stronger segmentation, BAA-covered vendors, and redaction before non-covered model calls
- `Financial or regulated ops`: dual approval for side effects, immutable decision logs, and explicit separation between recommendation and execution agents

## 4. Distributed Resilience & Security

### Durable execution patterns

Local supervisor-worker flows can survive with resumable run state alone, but enterprise deployments become much safer when the orchestration is externalized into a durable workflow engine or event log.

Recommended pattern:

```text
User Request
  -> Workflow Engine (Temporal or equivalent)
  -> Task Queue / Kafka Topic
  -> Worker Executors
  -> Checkpoint after each branch result
  -> Synthesizer
  -> Final response or resumable pause
```

Why this matters:

- `Workflow replay`: deterministic orchestration logic can recover after process crashes without rerunning already-completed side effects.
- `Distributed locking`: a workflow or lease key prevents duplicate processing when the same business object is retried.
- `Checkpointing`: branch completion is persisted incrementally, so partial progress is not lost when one worker fails.
- `Dead-letter handling`: poison tasks move to a DLQ with the full delegation trail instead of disappearing in ad hoc logs.

### Failure taxonomy

Transient failures:

- model rate limits
- network timeouts
- temporary MCP server unavailability
- short-lived remote A2A endpoint failures

Permanent failures:

- schema mismatch between supervisor and worker contract
- policy denial on a required privileged tool
- invalid tenant entitlements
- deleted or forbidden downstream resource

Poison-pill signals:

- same task fails with the same normalized error across `N` retries
- supervisor keeps routing to the same worker despite unchanged denial reasons
- replay reproduces the same invalid payload without state change

Required controls:

- idempotency keys on tool calls and remote delegations
- exponential backoff with jitter for transient faults
- circuit breakers per worker endpoint
- dead-letter promotion after retry budget exhaustion
- branch-level fallback so one failed specialist does not necessarily take down the whole run

### Zero-Trust MCP and delegation security

The correct security model is zero-trust at every tool and agent boundary:

1. Treat every MCP server or remote agent endpoint as an independent protected resource.
2. Bind tokens to the specific server or agent audience; never let one broad token authorize the whole mesh.
3. Apply least-privilege RBAC at the tool level, not only at the user or agent level.
4. Require explicit approval for side effects such as ticket creation, database mutation, message sending, or privileged retrieval.
5. Separate `recommendation agents` from `execution agents` so reasoning and actuation do not share the same blast radius.

### PII filtering and auditability

A compliance-grade delegation pipeline should be:

```text
ingress
  -> classify sensitive fields
  -> redact or tokenize
  -> route only allowed fields to workers/tools
  -> persist original-to-redacted mapping in secure audit store
  -> emit immutable decision event
```

Minimum enterprise audit record per branch:

- `correlation_id`
- `tenant_id`
- `actor` and `delegating_agent`
- `target_worker` or `remote_agent`
- prompt hash and policy version
- tool name, auth scope, and approval outcome
- redaction actions taken
- model/provider chosen
- output hash and user-visible degradation flags

> ⚠️ Gap: The source material is much stronger on authorization, approval, and delegation mechanics than on first-party immutable audit-log schemas or built-in PII-redaction internals, so teams should expect to implement those controls in their own platform layer.

## 5. Production Enterprise Code

The example below is a runnable Python supervisor-worker skeleton with:

- retries with exponential backoff and jitter
- a circuit breaker with `closed -> open -> half-open`
- fallback model chain `primary -> secondary -> deterministic fallback`
- structured JSON logging with correlation IDs
- graceful degradation when one worker path is unavailable

```python
from __future__ import annotations

import concurrent.futures
import json
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


def log_event(event: str, **fields: object) -> None:
    payload = {"ts": round(time.time(), 3), "event": event, **fields}
    print(json.dumps(payload, sort_keys=True))


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 0.2
    max_delay_s: float = 2.0
    jitter_s: float = 0.1


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    reset_timeout_s: float = 5.0
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    opened_at: float = 0.0

    def before_call(self) -> None:
        now = time.monotonic()
        if self.state == CircuitState.OPEN:
            if now - self.opened_at >= self.reset_timeout_s:
                self.state = CircuitState.HALF_OPEN
            else:
                raise TransientError("circuit_open")

    def record_success(self) -> None:
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.opened_at = 0.0

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = time.monotonic()


@dataclass
class ModelEndpoint:
    name: str
    failure_rate: float
    latency_s: float
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    def infer(self, prompt: str) -> str:
        self.breaker.before_call()
        time.sleep(self.latency_s)
        if random.random() < self.failure_rate:
            self.breaker.record_failure()
            raise TransientError(f"{self.name}_temporary_failure")
        self.breaker.record_success()
        return f"{self.name} handled: {prompt}"


def call_with_retries(
    fn: Callable[[], str],
    retry_policy: RetryPolicy,
    correlation_id: str,
    worker_name: str,
) -> str:
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except PermanentError:
            raise
        except TransientError as exc:
            log_event(
                "worker_retry",
                correlation_id=correlation_id,
                worker=worker_name,
                attempt=attempt,
                error=str(exc),
            )
            if attempt >= retry_policy.max_attempts:
                raise
            backoff = min(
                retry_policy.max_delay_s,
                retry_policy.base_delay_s * (2 ** (attempt - 1)),
            )
            sleep_s = backoff + random.uniform(0.0, retry_policy.jitter_s)
            time.sleep(sleep_s)


@dataclass
class WorkerResult:
    worker: str
    ok: bool
    output: str
    degraded: bool = False


@dataclass
class SpecialistWorker:
    name: str
    primary: ModelEndpoint
    secondary: ModelEndpoint
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    def deterministic_fallback(self, task: str) -> str:
        if self.name == "compliance":
            forbidden = {"ssn", "passport", "credit_card"}
            flagged = [token for token in forbidden if token in task.lower()]
            if flagged:
                return f"BLOCK: contains sensitive fields {flagged}"
            return "ALLOW: no blocked sensitive keywords detected"
        if self.name == "search":
            keywords = [word for word in task.split() if len(word) > 4][:6]
            return f"Fallback keyword bundle: {', '.join(keywords) or 'none'}"
        return "Fallback summary: partial service available"

    def run(self, task: str, correlation_id: str) -> WorkerResult:
        for endpoint in (self.primary, self.secondary):
            try:
                output = call_with_retries(
                    fn=lambda endpoint=endpoint: endpoint.infer(task),
                    retry_policy=self.retry_policy,
                    correlation_id=correlation_id,
                    worker_name=self.name,
                )
                log_event(
                    "worker_success",
                    correlation_id=correlation_id,
                    worker=self.name,
                    endpoint=endpoint.name,
                )
                return WorkerResult(worker=self.name, ok=True, output=output)
            except TransientError as exc:
                log_event(
                    "worker_endpoint_failed",
                    correlation_id=correlation_id,
                    worker=self.name,
                    endpoint=endpoint.name,
                    breaker_state=endpoint.breaker.state.value,
                    error=str(exc),
                )

        fallback = self.deterministic_fallback(task)
        log_event(
            "worker_degraded",
            correlation_id=correlation_id,
            worker=self.name,
            reason="all_model_endpoints_failed",
        )
        return WorkerResult(worker=self.name, ok=True, output=fallback, degraded=True)


@dataclass
class Supervisor:
    workers: Dict[str, SpecialistWorker]
    max_parallel_workers: int = 4
    required_workers: Tuple[str, ...] = ("search", "compliance")

    def plan(self, user_request: str) -> List[Tuple[str, str]]:
        plan = [("search", user_request), ("compliance", user_request)]
        if "pricing" in user_request.lower() or "quote" in user_request.lower():
            plan.append(("finance", user_request))
        return plan

    def handle(self, user_request: str) -> Dict[str, object]:
        correlation_id = str(uuid.uuid4())
        started = time.monotonic()
        plan = self.plan(user_request)
        log_event(
            "run_started",
            correlation_id=correlation_id,
            worker_plan=[name for name, _ in plan],
        )

        results: Dict[str, WorkerResult] = {}
        degraded_workers: List[str] = []

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_parallel_workers
        ) as pool:
            future_map = {
                pool.submit(self.workers[name].run, task, correlation_id): name
                for name, task in plan
            }
            for future in concurrent.futures.as_completed(future_map):
                worker_name = future_map[future]
                try:
                    result = future.result(timeout=5)
                except Exception as exc:  # noqa: BLE001
                    log_event(
                        "worker_failed_hard",
                        correlation_id=correlation_id,
                        worker=worker_name,
                        error=str(exc),
                    )
                    results[worker_name] = WorkerResult(
                        worker=worker_name,
                        ok=False,
                        output=f"{worker_name} unavailable",
                        degraded=True,
                    )
                    degraded_workers.append(worker_name)
                    continue

                results[worker_name] = result
                if result.degraded:
                    degraded_workers.append(worker_name)

        missing_required = [
            worker
            for worker in self.required_workers
            if not results.get(worker) or not results[worker].ok
        ]
        if missing_required:
            raise PermanentError(f"required_workers_failed={missing_required}")

        response = {
            "correlation_id": correlation_id,
            "status": "degraded" if degraded_workers else "ok",
            "summary": self.synthesize(results),
            "degraded_workers": degraded_workers,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
        log_event("run_finished", **response)
        return response

    @staticmethod
    def synthesize(results: Dict[str, WorkerResult]) -> str:
        ordered = []
        for worker in sorted(results):
            suffix = " (degraded)" if results[worker].degraded else ""
            ordered.append(f"[{worker}{suffix}] {results[worker].output}")
        return " | ".join(ordered)


def build_demo_supervisor() -> Supervisor:
    return Supervisor(
        workers={
            "search": SpecialistWorker(
                name="search",
                primary=ModelEndpoint("search-primary", failure_rate=0.4, latency_s=0.1),
                secondary=ModelEndpoint("search-secondary", failure_rate=0.1, latency_s=0.15),
            ),
            "compliance": SpecialistWorker(
                name="compliance",
                primary=ModelEndpoint("compliance-primary", failure_rate=0.8, latency_s=0.1),
                secondary=ModelEndpoint("compliance-secondary", failure_rate=0.3, latency_s=0.1),
            ),
            "finance": SpecialistWorker(
                name="finance",
                primary=ModelEndpoint("finance-primary", failure_rate=0.2, latency_s=0.12),
                secondary=ModelEndpoint("finance-secondary", failure_rate=0.05, latency_s=0.12),
            ),
        }
    )


if __name__ == "__main__":
    random.seed(7)
    supervisor = build_demo_supervisor()
    result = supervisor.handle(
        "Generate a pricing-safe renewal summary without exposing ssn fields."
    )
    print(json.dumps(result, indent=2, sort_keys=True))
```

The key production idea is that degradation is explicit. If a noncritical worker falls back, the supervisor still responds, records the degraded branch, and keeps required control workers such as compliance in the critical path.

## 6. Architectural System Design Scenarios

### Scenario 1: Regulated customer-support copilot

**Problem statement**: Design a multi-tenant support copilot for a B2B SaaS platform handling `15k` requests/min during peak support events, with privileged actions such as refund approval, ticket mutation, and knowledge-base retrieval. The system must keep `p99 <= 7s` for bounded support workflows, maintain centralized policy control, and prevent PII leakage into downstream tools.

**Proposed architecture**:

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ User -> Support UI -> API Gateway -> Supervisor                            │
│                                 │                                          │
│                                 ├─ Search Worker -> RAG / KB               │
│                                 ├─ Policy Worker -> Entitlements / RBAC     │
│                                 ├─ Compliance Worker -> PII / action guard  │
│                                 └─ Action Worker -> MCP tool proxy          │
│                                                        │                   │
│                                                        v                   │
│                                          CRM / billing / ticket systems    │
│                                                                            │
│ Persistence: workflow events + approval log + immutable audit trail       │
│ Telemetry: traces, token ledger, redaction events, degraded-branch flags  │
└────────────────────────────────────────────────────────────────────────────┘
```

Technology choices:

- centralized supervisor runtime with bounded workers
- MCP gateway for enterprise tools with tool-level approval
- append-only workflow event store for replay and audit
- local deterministic fallback for enrichment workers; fail closed for action workers

**Trade-off evaluation matrix**:

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Single generalist agent with all tools | Lowest prompt overhead | Best p50, worst p99 under tool sprawl | Lowest initially | Weakest least-privilege boundary | Limited by context/tool explosion |
| Supervisor with bounded workers | Moderate | Strong p95/p99 due to parallelism and bounded tasks | Moderate | Strong central policy and approvals | High for internal multi-tenant use |
| Remote peer agents per business system | Highest | Slowest due to network and auth hops | Highest | Strong domain isolation, weaker central simplicity | Highest org-level autonomy, hardest to operate |

**Decision rationale**: Choose `supervisor with bounded workers`. It preserves one approval plane, makes PII filtering and RBAC enforceable before side effects, and supports graceful degradation when search or enrichment is impaired. A single generalist is cheaper at low scale but becomes unsafe and brittle as tool count and tenant policy diversity grow. A remote peer mesh adds too much latency and auth complexity for a support flow that benefits from central control.

### Scenario 2: Cross-organization vendor-risk review mesh

**Problem statement**: Design an enterprise workflow that coordinates internal security, legal, procurement, and an external due-diligence provider to review strategic vendors. Each domain owns its own systems and policies, some agents operate in separate trust domains, and the workflow must survive partial remote outages. Target `p95 <= 12s` for interactive status checks and `RTO <= 60 minutes` for regional recovery, with immutable auditability of who delegated what to whom.

**Proposed architecture**:

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Analyst UI -> Coordinator Agent -> Workflow Engine                         │
│                         │                                                  │
│                         ├─ Internal Security Agent -> vuln / asset tools    │
│                         ├─ Internal Legal Agent -> clause analyzer          │
│                         ├─ Procurement Agent -> ERP / contract systems      │
│                         └─ Remote Due-Diligence Agent -> A2A endpoint       │
│                                                      │                     │
│                                                      v                     │
│                                         External trust domain / agent card  │
│                                                                            │
│ Kafka / task bus stores branch events, retries, DLQ, and checkpoint state │
│ Audit trail stores approvals, auth scopes, redactions, and result hashes   │
└────────────────────────────────────────────────────────────────────────────┘
```

Technology choices:

- durable workflow engine plus Kafka-style event transport
- remote A2A delegation for external specialists
- branch-level bulkheads, circuit breakers, and resumable approvals
- immutable decision log with prompt hashes and auth scopes

**Trade-off evaluation matrix**:

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Centralize all tools under one internal supervisor | Lowest external overhead | Fastest | Moderate | Weak when external parties should not share tool access | Poor across trust boundaries |
| Hierarchical internal manager with manual external handoff | Moderate | Moderate to high | Moderate | Better separation, but weak automation continuity | Moderate |
| Coordinator plus remote A2A mesh | Highest | Highest | Highest | Best cross-domain isolation and ownership | Best for multi-organization collaboration |

**Decision rationale**: Choose `coordinator plus remote A2A mesh` despite the extra cost. The problem is fundamentally cross-domain and cross-trust-boundary, so clean ownership, scoped auth, and branch isolation matter more than token efficiency. A centralized tool model would be operationally simpler but would collapse legal, procurement, and external-provider boundaries into one blast radius and one policy surface.

## Sources

- [1] https://developers.openai.com/api/docs/guides/agents/orchestration - OpenAI manager-style orchestration, handoffs, and specialist design guidance.
- [2] https://openai.github.io/openai-agents-python/running_agents/ - OpenAI runner loop, `max_turns`, websocket limits, and run continuation behavior.
- [3] https://openai.github.io/openai-agents-python/human_in_the_loop/ - OpenAI approval pauses, resumable run state, and approval coverage for tools and agent-as-tool calls.
- [4] https://openai.github.io/openai-agents-python/mcp/ - OpenAI MCP integration, approval policies, and tool filtering.
- [5] https://docs.langchain.com/oss/python/langchain/multi-agent/subagents - LangChain supervisor/subagent pattern, stateless workers, context isolation, and parallel subagent execution.
- [6] https://docs.langchain.com/oss/python/migrate/langgraph-supervisor - Deprecation of `langgraph-supervisor` and migration to tool-wrapped subagents.
- [7] https://docs.langchain.com/oss/python/langgraph/use-subgraphs - LangGraph subgraph persistence modes, nested-state visibility, and multi-agent subgraph guidance.
- [8] https://adk.dev/workflows/collaboration/ - ADK coordinator/subagent collaboration, modes, and automatic return semantics.
- [9] https://adk.dev/agents/llm-agents/ - ADK agent descriptions, delegation-related fields, and mode semantics.
- [10] https://adk.dev/agents/custom-agents/ - ADK hierarchy primitives, `sub_agents`, transfer scope, and custom orchestration patterns.
- [11] https://docs.crewai.com/edge/en/concepts/collaboration - CrewAI delegation tools and agent-to-agent collaboration within a crew.
- [12] https://docs.crewai.com/en/concepts/processes - CrewAI sequential vs hierarchical execution and manager-agent behavior.
- [13] https://docs.crewai.com/v1.15.1/en/learn/a2a-agent-delegation - CrewAI outbound A2A delegation, auth options, timeouts, update modes, and transport choices.
- [14] https://docs.crewai.com/v1.15.4/en/enterprise/features/a2a - CrewAI enterprise A2A transport, agent-card, auth, TLS, and gRPC features.
- [15] https://www.anthropic.com/engineering/building-effective-agents - Anthropic orchestrator-workers pattern and trade-offs of multi-agent decomposition.
- [16] https://www.anthropic.com/research/multiagent-systems - Anthropic research on emerging multi-agent patterns, scaling experiments, and alignment risks.
- [17] https://modelcontextprotocol.io/specification/draft/basic/authorization - MCP authorization requirements, discovery, and OAuth-based protected resource model.
- [18] https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations - MCP security requirements including PKCE, HTTPS, and resource-bound tokens.
