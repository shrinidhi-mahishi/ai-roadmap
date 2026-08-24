# Production — Docker, Kubernetes, APIs, Queues, Scaling, Reliability

## 1. System Topology & Data Flow

Production agent systems are strongest when `workflow control`, `tool/API execution`, `durable state`, and `observability` are separated into explicit planes. In the local `research_cursor` corpus, the recurring lesson is not "put everything inside one container," but rather "keep the control plane durable and policy-aware while keeping the execution plane replaceable." `Docker` and `Kubernetes` fit here as packaging and orchestration substrates for the data plane, while workflow engines, checkpoint stores, and audit systems remain separate persistence concerns.

```text
┌────────────────────────────── Control Plane ──────────────────────────────┐
│ API Gateway -> AuthN/Z -> Policy Engine -> Workflow Orchestrator          │
│      │               │              │                 │                    │
│      │               │              │                 ├─ SLA tier         │
│      │               │              │                 ├─ retry budget     │
│      │               │              │                 ├─ approval state   │
│      │               │              │                 └─ queue selection  │
│      └────────────────────────────> Correlation ID / Tenant / Deadline     │
└────────────────────────────────────────────────────────────────────────────┘
                                 │
                                 v
┌─────────────────────────────── Data Plane ────────────────────────────────┐
│ Sync API lane -> Router -> Model/Tool Executor -> Verifier -> Responder   │
│ Async API lane -> Admission Queue -> Worker Pool -> Verifier -> Callback   │
│                 │          │                │                 │             │
│                 │          │                ├─ strict APIs    │             │
│                 │          │                ├─ Zero-Trust MCP │             │
│                 │          │                ├─ sandboxed code │             │
│                 │          │                └─ browser last   │             │
│                 │          └─ Docker images on Kubernetes / Cloud runtime   │
└────────────────────────────────────────────────────────────────────────────┘
         │                         │                          │
         v                         v                          v
┌──────────────────┐   ┌────────────────────┐   ┌──────────────────────────┐
│ Persistence      │   │ Tool Proxies       │   │ Telemetry / Audit Sinks  │
│ workflow history │   │ schema validation  │   │ traces / logs / metrics  │
│ checkpoints      │   │ RBAC / approvals   │   │ queue lag / cost ledger  │
│ idempotency keys │   │ PII filters        │   │ immutable event journal  │
│ run store        │   │ auth propagation   │   │ SIEM / alerting          │
└──────────────────┘   └────────────────────┘   └──────────────────────────┘
```

### Request-flow narrative

1. `API Gateway` accepts the request, assigns `correlation_id`, `tenant_id`, and an end-to-end deadline, then classifies the request into an interactive or asynchronous service lane.
2. `Policy Engine` decides whether the request is read-only, mutation-capable, approval-gated, or disallowed. This is where schema validity and authorization are kept separate.
3. `Workflow Orchestrator` records the first durable event before expensive model or tool work begins. This preserves continuity across retries, pauses, and worker replacement.
4. `Router` chooses the cheapest safe execution surface in this order: `strict API/function tool`, `MCP capability`, `sandboxed code execution`, then `browser/computer automation` only when no narrower surface exists.
5. Interactive work stays on the synchronous path; long-running or fan-out work enters an `Admission Queue` so back-pressure can be applied without collapsing the entire service.
6. `Worker Pool` runs in containers, commonly on `Kubernetes` or equivalent managed compute. The containers are replaceable; they do not own business-critical workflow state.
7. `Verifier` records `attempted action`, `confirmed external effect`, and `degraded outcome` separately so retries do not create replay ambiguity.
8. `Persistence` stores workflow history, checkpoint data, idempotency keys, and audit events. `Telemetry / Audit Sinks` receive latency, queue depth, token usage, failure class, and redaction decisions for operational and compliance review.

The practical production lesson is that `Kubernetes` scales the worker substrate, but `reliability` comes from durable workflow state, queue discipline, idempotency, and observability rather than from replica count alone.

> ⚠️ Gap: The local `research_cursor` set is materially stronger on workflow durability, tool/API boundaries, caching, and remote-failure handling than on low-level `Dockerfile` tuning, `Kubernetes` controller internals, service-mesh behavior, or HPA/VPA benchmark data. This module therefore treats containers and clusters as production substrates, not as a place to invent unsupported operational detail.

## 2. Core Mechanics & Algorithms

### Production execution state machine

```text
ACCEPT
  -> AUTHENTICATE
  -> AUTHORIZE
  -> CLASSIFY_SYNC_OR_ASYNC
       -> SYNC_API_PATH
       -> QUEUED_WORKFLOW_PATH
  -> CHECK_IDEMPOTENCY
  -> ROUTE_EXECUTION_SURFACE
       -> STRICT_API
       -> MCP_TOOL
       -> SANDBOXED_CODE
       -> BROWSER_LAST_RESORT
  -> EXECUTE
  -> VERIFY_EFFECT
  -> CHECKPOINT
  -> COMPLETE
  -> DEGRADED_COMPLETE
  -> FAIL
```

This state machine matters because production systems fail most often at boundary transitions:

- `sync -> async`: admission control and queue lag determine whether the service degrades gracefully or melts down
- `planned action -> external side effect`: retries must not duplicate mutations
- `temporary outage -> degraded mode`: the system needs explicit fallback behavior instead of timing out everywhere
- `worker replacement -> resume`: replay safety depends on deterministic workflow state and persisted effect records

### Queueing and routing mechanics

The local corpus implies a consistent production preference:

```text
preferred_surface
  = argmin_surface risk(surface)
    subject to capability(surface) >= task_requirement
```

Operationally, the order is:

1. `strict API/function tool`
2. `MCP capability server`
3. `sandboxed server-side code execution`
4. `browser/computer automation`

This is not just an elegance preference. Each step downward increases ambiguity, input overhead, and attack surface.

### Capacity and scaling formulas

Interactive systems are constrained by both queueing and provider limits:

```text
max_completed_runs_per_minute
  = min(
      provider_rpm / avg_model_turns_per_run,
      provider_tpm / avg_total_tokens_per_run,
      worker_concurrency * 60 / avg_service_time_s
    )
```

For queued workflows:

```text
queue_drain_time_s
  = backlog_items / effective_worker_throughput_items_per_s
```

Little's Law gives a useful first-order planning rule:

```text
concurrency_needed
  = arrival_rate_per_s * avg_service_time_s
```

When fan-out is present, user-visible latency follows the critical path rather than the sum of all work:

```text
critical_path_latency
  = admission_delay
  + planning_latency
  + max(parallel_branch_durations)
  + effect_verification
  + checkpoint_persistence
```

### Reliability algorithms and invariants

#### Idempotency

Every mutation-capable step needs a stable key:

```text
idempotency_key
  = hash(tenant_id, workflow_id, step_id, normalized_arguments)
```

Lookup is effectively `O(1)` with a hash index, but the invariant is more important than complexity:

```text
attempted_action != confirmed_effect
```

Without that invariant, retries produce duplicate writes or ambiguous incident forensics.

#### Durable workflow replay

Temporal-style history or equivalent checkpoint replay changes reliability from "best effort" to "resumable":

```text
replay_safe if
  workflow_decisions are deterministic
  and external side effects are isolated in activities
  and activity results are persisted before reuse
```

Replay traversal is `O(events)` for a workflow history, which is acceptable because recovery correctness matters more than raw replay speed on rare failure paths.

#### Bulkhead isolation

Treat every remote dependency as a separate failure domain:

```text
dependency_domains = {
  model_provider,
  tool_proxy,
  queue_backend,
  checkpoint_store,
  auth_service
}
```

Each domain should have its own timeout, retry budget, and circuit breaker. If all domains share one failure budget, one slow tool can consume the latency envelope of the entire request.

### Convergence and correctness invariants

- `max_retries`, `queue_ttl`, and end-to-end deadline must be explicit.
- Queue admission must be bounded by tenant, priority class, and workload type.
- Workflow state must live outside ephemeral containers.
- All privileged tool calls must carry `idempotency_key`, `correlation_id`, and `tenant_id`.
- Checkpoints must record policy version and approval state, not only conversational context.
- Browser or untrusted tool output must never be promoted into high-trust policy instructions without validation.

## 3. Token Economics & NFR Analysis

> ⚠️ Gap: The local `research_cursor` corpus supports cost structure, cache multipliers, queue/back-pressure reasoning, and throughput formulas more strongly than universal public percentile benchmarks for production agent platforms. The `p50/p95/p99` numbers below are engineering targets for system design, not vendor guarantees.

### Cost formulas

Assumptions:

- `runs = 1000`
- `U` = uncached dynamic input tokens per run
- `C` = stable cacheable prefix tokens per run
- `h` = prompt-cache hit rate on `C`
- `O` = output tokens per run
- `S` = fraction of runs deflected to deterministic or cache-only fast paths
- `R_fast`, `R_deep`, `R_local` = route fractions across served runs, where `R_fast + R_deep + R_local = 1`
- `P_in_fast`, `P_out_fast` = fast-tier model prices per `1M` tokens
- `P_in_deep`, `P_out_deep` = premium-tier model prices per `1M` tokens
- `P_in_local`, `P_out_local` = effective self-hosted/local serving prices per `1M` tokens
- cache writes are approximated at `1.25x` input price where supported by the local corpus
- cache reads are approximated at `0.10x` input price where supported by the local corpus
- `A` = average API or MCP tool calls per run
- `P_api` = average non-token cost per tool/API call
- `Q` = queue/checkpoint cost per run
- `K` = container-runtime cost per run, after container reuse

Reusable cached-input primitive:

```text
effective_input_cost(model)
  = (
      U * P_in_model
      + C * (1 - h) * 1.25 * P_in_model
      + C * h * 0.10 * P_in_model
    ) / 1_000_000
```

Weighted served-run model cost:

```text
served_fraction = 1 - S
```

```text
weighted_model_cost_per_run
  = served_fraction * (
      R_fast * (
        effective_input_cost(fast)
        + (O * P_out_fast) / 1_000_000
      )
      + R_deep * (
        effective_input_cost(deep)
        + (O * P_out_deep) / 1_000_000
      )
      + R_local * (
        effective_input_cost(local)
        + (O * P_out_local) / 1_000_000
      )
    )
```

Full production blended cost:

```text
$ cost per 1k runs
  = 1000 * weighted_model_cost_per_run
    + 1000 * A * P_api
    + 1000 * Q
    + 1000 * K
```

Container reuse matters because the local corpus reports hosted execution-container sessions in a range that can dominate total cost if every request cold-starts a fresh session. A useful approximation is:

```text
K
  = container_session_price / average_runs_per_reused_container
```

### Worked numeric example

Assume:

- `U = 1800`
- `C = 2500`
- `h = 0.70`
- `O = 250`
- `S = 0.10`
- `R_fast = 0.60`
- `R_deep = 0.25`
- `R_local = 0.15`
- `P_in_fast = $0.30 / 1M`
- `P_out_fast = $1.20 / 1M`
- `P_in_deep = $3.00 / 1M`
- `P_out_deep = $12.00 / 1M`
- `P_in_local = $0.12 / 1M`
- `P_out_local = $0.48 / 1M`
- `A = 1.4`
- `P_api = $0.002`
- `Q = $0.0004`
- `K = $0.0012`

Fast-tier effective input cost:

```text
effective_input_cost(fast)
  = (
      1800 * 0.30
      + 2500 * (1 - 0.70) * 1.25 * 0.30
      + 2500 * 0.70 * 0.10 * 0.30
    ) / 1_000_000
  = (540 + 281.25 + 52.5) / 1_000_000
  = $0.00087375
```

Deep-tier effective input cost:

```text
effective_input_cost(deep)
  = (
      1800 * 3.00
      + 2500 * (1 - 0.70) * 1.25 * 3.00
      + 2500 * 0.70 * 0.10 * 3.00
    ) / 1_000_000
  = (5400 + 2812.5 + 525) / 1_000_000
  = $0.0087375
```

Local effective input cost:

```text
effective_input_cost(local)
  = (
      1800 * 0.12
      + 2500 * (1 - 0.70) * 1.25 * 0.12
      + 2500 * 0.70 * 0.10 * 0.12
    ) / 1_000_000
  = (216 + 112.5 + 21) / 1_000_000
  = $0.0003495
```

Weighted served-run model cost:

```text
weighted_model_cost_per_run
  = 0.90 * (
      0.60 * (0.00087375 + 0.0003)
      + 0.25 * (0.0087375 + 0.003)
      + 0.15 * (0.0003495 + 0.00012)
    )
  = 0.90 * (
      0.60 * 0.00117375
      + 0.25 * 0.0117375
      + 0.15 * 0.0004695
    )
  = 0.90 * (0.00070425 + 0.002934375 + 0.000070425)
  = 0.90 * 0.00370905
  = $0.003338145
```

Final blended cost:

```text
$ cost per 1k runs
  = 1000 * 0.003338145
    + 1000 * 1.4 * 0.002
    + 1000 * 0.0004
    + 1000 * 0.0012
  = 3.338145 + 2.8 + 0.4 + 1.2
  = $7.738145
```

Interpretation:

- model spend is not the only driver; tool/API traffic and container cold-start avoidance materially change the budget
- prompt-cache hits shrink repeated policy and schema overhead
- queue/checkpoint cost is usually small per run, but it buys much stronger replay and operability
- local serving is financially attractive only if evaluation quality and governance parity remain acceptable

### Latency targets

Recommended service envelopes:

- `read-only interactive API lane`: `p50 <= 800ms`, `p95 <= 2.5s`, `p99 <= 5.0s`
- `tool-backed interactive lane`: `p50 <= 1.5s`, `p95 <= 4.0s`, `p99 <= 8.0s`
- `async queued acknowledgement`: `p50 <= 120ms`, `p95 <= 300ms`, `p99 <= 750ms`
- `background queued workflow completion`: `p50 <= 45s`, `p95 <= 3m`, `p99 <= 10m`

Mitigations by percentile:

- `p50`: stable prompt prefixes, hot container reuse, colocated queue consumers, low-cost routing features
- `p95`: cap branch fan-out, flush queues on deadline, reuse checkpoints, degrade optional enrichments before core execution
- `p99`: open dependency-specific circuit breakers, stop routing to sick tool domains, shed low-priority traffic, fall back to deterministic read-only responses, preserve mutation paths as fail-closed

### Throughput and back-pressure

Useful planning formulas:

```text
safe_qps
  = min(
      gateway_qps_limit,
      model_tokens_per_second / avg_tokens_per_request,
      queue_backend_ops_per_second / queue_ops_per_request,
      checkpoint_writes_per_second / checkpoints_per_run
    )
```

```text
queue_growth_per_second
  = arrival_rate_per_second - completion_rate_per_second
```

If `queue_growth_per_second > 0` for sustained periods, the system is already in a latent incident even if request acceptance still looks healthy.

Back-pressure order:

1. reject or defer low-priority async work
2. disable optional secondary analysis and enrichments
3. shrink planner breadth or fan-out depth
4. route safe read-only traffic to cheaper bounded paths
5. fail closed for privileged writes rather than masking authorization or replay risk

### Non-functional requirements

- `availability`: `99.9%` for synchronous read paths, `99.95%` for workflow state and idempotency store, `99.99%` for audit evidence on privileged mutations
- `RPO`: `0` for approvals, idempotency keys, and mutation audit logs; `<= 1 minute` for checkpoint mirrors; `<= 5 minutes` for performance telemetry replicas
- `RTO`: `<= 15 minutes` for worker-pool replacement, `<= 30 minutes` for queue-consumer failover, `<= 60 minutes` for checkpoint-store restoration, `<= 4 hours` for full observability backfill
- `compliance`: tenant-scoped cache keys, request lineage, least-privilege tool identities, and explicit data-retention classes
- `privacy`: redact prompts, tool results, and artifacts before cache or trace persistence; retain reversible mappings only in isolated vault systems when required

## 4. Distributed Resilience & Security

Production reliability is mostly the art of surviving partial failure without corrupting workflow state or authority boundaries. The local corpus repeatedly supports one design rule: keep `durable control state` above `replaceable execution surfaces`.

### Durable execution

Recommended pattern:

- use `Temporal`, `LangGraph` checkpoints, or an equivalent durable workflow engine for long-running tasks
- emit append-only route and effect events to `Kafka` or an equivalent event stream for audit and replay diagnostics
- checkpoint after `request_admitted`, `approval_resolved`, `route_selected`, `activity_started`, `activity_result_received`, `effect_verified`, and `response_committed`
- keep queue leases short and renew them explicitly so dead workers do not hold work indefinitely
- send exhausted retries, malformed payloads, and repeated idempotency conflicts to a dead-letter stream

Replay-safe flow:

```text
request_received
  -> durable_admission_record
  -> route_selected
  -> activity_invoked
  -> external_effect_verified
  -> checkpoint_written
  -> audit_journal_committed
  -> complete
```

The order matters. `audit_journal_committed` cannot depend on best-effort in-memory state, and an `external_effect_verified` record must exist before a replay can safely skip a side effect.

### Failure taxonomy

`Transient failures`

- `429` provider throttling
- queue backend timeout
- checkpoint-store latency spike
- temporary `MCP` auth refresh failure
- Kubernetes node eviction or container restart

`Permanent failures`

- invalid tool schema version
- revoked credentials or RBAC denial
- tenant mismatch on replayed request
- malformed container image or unsupported runtime contract
- deterministic output validation failure

`Poison-pill failures`

- one bad queue message that crashes every worker replay
- one malformed tool result that repeatedly fails parser validation
- one oversize workflow payload that always exceeds token or queue limits

`Semantic failures`

- policy-valid request routed to the wrong tenant resource
- replay attempts a mutation whose prior side effect was never recorded correctly
- browser output contains prompt injection that bypasses trust filtering
- autoscaled workers increase throughput but overload the checkpoint store, creating hidden recovery debt

Required controls:

- strict idempotency keys on every mutation-capable activity
- dead-letter retention with route lineage, payload hash, and failure class
- queue retry ceilings that distinguish transient from permanent failure
- per-dependency circuit breakers so one remote domain does not exhaust the entire request budget
- workflow-level compensation or manual-review paths for partially completed mutation sequences

### Circuit breakers and graceful degradation

Breakers should exist independently for:

- model provider
- `MCP` or API tool proxy
- queue backend
- checkpoint store
- auth service
- browser automation substrate

```text
CLOSED
  -> OPEN       after thresholded timeout / throttle / invalid-response failures
  -> HALF_OPEN  after cooldown
  -> CLOSED     after bounded successful probes
  -> OPEN       if probes fail
```

Graceful degradation order:

1. full workflow with premium model, queueing, and rich tools
2. disable optional enrichments and secondary fetches
3. bypass unstable remote tools and serve read-only model-only responses where safe
4. move non-urgent jobs to background queues and return asynchronous handles
5. deterministic fallback for safe read-only traffic; fail closed for privileged writes

### Enterprise security controls

Zero-Trust `MCP` architecture:

- every tool or resource request terminates at a policy-enforcing proxy
- OAuth/PKCE-style capability auth stays at the protocol boundary, not buried in prompt text
- tool discovery does not imply tool authorization; execution-time checks remain mandatory
- resource-bound tokens are scoped to the narrowest capability set possible

Tool-level RBAC:

- split identities for `read_only_lookup`, `analytics_export`, and `mutation_capable_actions`
- map workflow classes to capability bundles rather than raw credentials
- preserve the same approval and RBAC rules across remote hosted models, local containers, and queued workers

PII filtering pipeline:

1. detect sensitive fields in prompt input, retrieved content, tool output, and workflow artifacts
2. redact or tokenize before trace write, cache write, and artifact persistence
3. store any reversible mapping only in isolated secrets or vault systems
4. emit immutable audit records for disclosure, redaction, replay, and deletion events

Auditability:

- immutable journal for `request_admitted`, `route_selected`, `tool_called`, `effect_verified`, `fallback_used`, `queue_retried`, and `response_sent`
- answer lineage from response to workflow history, tool artifacts, and confirmed external record versions
- separate performance telemetry from compliance-grade audit evidence so sampling decisions do not erase security history

## 5. Production Enterprise Code

The runnable Python example below models a production service with synchronous and queued lanes, retries with exponential backoff and jitter, dependency-specific circuit breakers, idempotency enforcement, structured logging, a primary-to-secondary-to-deterministic fallback chain, and graceful degradation under partial outages.

```python
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import uuid
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Deque, Dict, List, Optional


class FailureCategory(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class ServiceError(Exception):
    def __init__(self, message: str, category: FailureCategory) -> None:
        super().__init__(message)
        self.category = category


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class Request:
    tenant_id: str
    prompt: str
    requires_mutation: bool
    allow_async: bool
    priority: str


@dataclass
class Response:
    status: str
    answer: str
    route: str
    degraded: bool
    correlation_id: str


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts_ms": int(record.created * 1000),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for key in ("correlation_id", "tenant_id", "route", "degraded"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, separators=(",", ":"))


def build_logger() -> logging.Logger:
    logger = logging.getLogger("production_module")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOGGER = build_logger()


def log(message: str, correlation_id: str, tenant_id: str, route: str, degraded: bool) -> None:
    LOGGER.info(
        message,
        extra={
            "correlation_id": correlation_id,
            "tenant_id": tenant_id,
            "route": route,
            "degraded": degraded,
        },
    )


def retry_with_backoff(
    fn: Callable[[], str],
    max_attempts: int,
    base_delay_s: float,
    max_delay_s: float,
) -> str:
    last_error: Optional[ServiceError] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except ServiceError as exc:
            last_error = exc
            if exc.category == FailureCategory.PERMANENT or attempt == max_attempts:
                raise
            delay = min(max_delay_s, base_delay_s * (2 ** (attempt - 1)))
            jitter = random.uniform(0.0, delay * 0.25)
            time.sleep(delay + jitter)
    raise last_error or ServiceError("retry state invalid", FailureCategory.PERMANENT)


class CircuitBreaker:
    def __init__(self, failure_threshold: int, recovery_timeout_s: float) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.failure_count = 0
        self.state = BreakerState.CLOSED
        self.opened_at = 0.0

    def before_call(self) -> None:
        if self.state == BreakerState.OPEN:
            if time.monotonic() - self.opened_at >= self.recovery_timeout_s:
                self.state = BreakerState.HALF_OPEN
                return
            raise ServiceError("circuit open", FailureCategory.TRANSIENT)

    def on_success(self) -> None:
        self.failure_count = 0
        self.state = BreakerState.CLOSED
        self.opened_at = 0.0

    def on_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = time.monotonic()


class IdempotencyStore:
    def __init__(self) -> None:
        self.completed: Dict[str, Response] = {}

    def get(self, key: str) -> Optional[Response]:
        return self.completed.get(key)

    def put(self, key: str, response: Response) -> None:
        self.completed[key] = response


class WorkflowStore:
    def __init__(self) -> None:
        self.events: Dict[str, List[str]] = {}

    def append(self, workflow_id: str, event: str) -> None:
        self.events.setdefault(workflow_id, []).append(event)


class SimpleQueue:
    def __init__(self, max_depth: int) -> None:
        self.max_depth = max_depth
        self.items: Deque[tuple[str, Request, str]] = deque()

    def enqueue(self, correlation_id: str, request: Request, workflow_id: str) -> None:
        if len(self.items) >= self.max_depth:
            raise ServiceError("queue saturated", FailureCategory.TRANSIENT)
        self.items.append((correlation_id, request, workflow_id))

    def dequeue(self) -> Optional[tuple[str, Request, str]]:
        if not self.items:
            return None
        return self.items.popleft()

    def depth(self) -> int:
        return len(self.items)


class ModelBackend:
    def __init__(self, name: str, fail_word: str = "") -> None:
        self.name = name
        self.fail_word = fail_word

    def infer(self, prompt: str) -> str:
        if self.fail_word and self.fail_word in prompt:
            raise ServiceError(f"{self.name} temporarily unavailable", FailureCategory.TRANSIENT)
        return f"{self.name} answered: {prompt[:100]}"


class ToolProxy:
    def __init__(self, breaker: CircuitBreaker) -> None:
        self.breaker = breaker

    def execute(self, request: Request) -> str:
        self.breaker.before_call()
        if "rbac_deny" in request.prompt:
            self.breaker.on_failure()
            raise ServiceError("rbac denied", FailureCategory.PERMANENT)
        if "tool_flaky" in request.prompt:
            self.breaker.on_failure()
            raise ServiceError("tool transient failure", FailureCategory.TRANSIENT)
        self.breaker.on_success()
        if request.requires_mutation:
            return "mutation confirmed"
        return "read-only lookup confirmed"


class ProductionService:
    def __init__(self) -> None:
        self.idempotency_store = IdempotencyStore()
        self.workflow_store = WorkflowStore()
        self.queue = SimpleQueue(max_depth=3)
        self.model_breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_s=1.5)
        self.tool_breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_s=1.5)
        self.primary_model = ModelBackend(name="premium-primary", fail_word="primary_down")
        self.secondary_model = ModelBackend(name="fast-secondary", fail_word="secondary_down")
        self.tool_proxy = ToolProxy(self.tool_breaker)

    def handle(self, request: Request) -> Response:
        correlation_id = str(uuid.uuid4())
        workflow_id = str(uuid.uuid4())
        route = "sync_api"
        degraded = False

        self.workflow_store.append(workflow_id, "request_admitted")
        idempotency_key = self._idempotency_key(request)
        cached = self.idempotency_store.get(idempotency_key)
        if cached is not None:
            log("idempotent replay prevented", correlation_id, request.tenant_id, "idempotent_replay", False)
            return cached

        if request.allow_async and self._should_queue(request):
            self.workflow_store.append(workflow_id, "queued")
            self.queue.enqueue(correlation_id, request, workflow_id)
            response = Response(
                status="accepted",
                answer="workflow accepted for background processing",
                route="async_queue",
                degraded=False,
                correlation_id=correlation_id,
            )
            self.idempotency_store.put(idempotency_key, response)
            log("queued workflow", correlation_id, request.tenant_id, "async_queue", False)
            return response

        self.workflow_store.append(workflow_id, "sync_started")
        try:
            answer = self._execute_request(request, workflow_id)
        except ServiceError:
            degraded = True
            route = "secondary_fallback"
            try:
                answer = retry_with_backoff(
                    lambda: self.secondary_model.infer(request.prompt),
                    max_attempts=3,
                    base_delay_s=0.05,
                    max_delay_s=0.2,
                )
            except ServiceError:
                route = "deterministic_fallback"
                answer = self._deterministic_answer(request)

        response = Response(
            status="ok",
            answer=answer,
            route=route if degraded else "sync_api",
            degraded=degraded,
            correlation_id=correlation_id,
        )
        self.idempotency_store.put(idempotency_key, response)
        self.workflow_store.append(workflow_id, "response_committed")
        log("sync request served", correlation_id, request.tenant_id, response.route, degraded)
        return response

    def drain_queue(self) -> List[Response]:
        results: List[Response] = []
        while True:
            item = self.queue.dequeue()
            if item is None:
                return results
            correlation_id, request, workflow_id = item
            degraded = False
            route = "queued_worker"
            self.workflow_store.append(workflow_id, "worker_started")
            try:
                answer = self._execute_request(request, workflow_id)
            except ServiceError:
                degraded = True
                route = "queued_deterministic_fallback"
                answer = self._deterministic_answer(request)
            self.workflow_store.append(workflow_id, "worker_completed")
            response = Response(
                status="completed",
                answer=answer,
                route=route,
                degraded=degraded,
                correlation_id=correlation_id,
            )
            log("queued workflow completed", correlation_id, request.tenant_id, route, degraded)
            results.append(response)

    def _execute_request(self, request: Request, workflow_id: str) -> str:
        self.workflow_store.append(workflow_id, "route_selected")
        effect = retry_with_backoff(
            lambda: self.tool_proxy.execute(request),
            max_attempts=3,
            base_delay_s=0.05,
            max_delay_s=0.2,
        )
        self.workflow_store.append(workflow_id, "effect_verified")
        self.model_breaker.before_call()
        try:
            answer = retry_with_backoff(
                lambda: self.primary_model.infer(f"{request.prompt} | {effect}"),
                max_attempts=3,
                base_delay_s=0.05,
                max_delay_s=0.2,
            )
            self.model_breaker.on_success()
        except ServiceError:
            self.model_breaker.on_failure()
            raise
        self.workflow_store.append(workflow_id, "checkpoint_written")
        return answer

    def _should_queue(self, request: Request) -> bool:
        token_estimate = len(request.prompt.split())
        return request.priority == "bulk" or token_estimate > 18 or self.queue.depth() > 1

    def _idempotency_key(self, request: Request) -> str:
        payload = (
            f"{request.tenant_id}|{request.prompt}|"
            f"{request.requires_mutation}|{request.allow_async}|{request.priority}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _deterministic_answer(self, request: Request) -> str:
        mode = "write deferred" if request.requires_mutation else "read-only degraded response"
        return f"{mode}: {request.prompt[:100]}"


if __name__ == "__main__":
    service = ProductionService()
    requests = [
        Request(
            tenant_id="acme",
            prompt="Summarize the invoice status for customer 42",
            requires_mutation=False,
            allow_async=False,
            priority="interactive",
        ),
        Request(
            tenant_id="acme",
            prompt="Run overnight account reconciliation primary_down",
            requires_mutation=True,
            allow_async=True,
            priority="bulk",
        ),
        Request(
            tenant_id="globex",
            prompt="Generate a read-only risk digest tool_flaky",
            requires_mutation=False,
            allow_async=True,
            priority="bulk",
        ),
    ]

    for req in requests:
        print(service.handle(req))

    for result in service.drain_queue():
        print(result)
```

Why this code matters:

- retries use exponential backoff with jitter and stop after bounded retry limits
- circuit breakers explicitly model `closed -> open -> half-open`
- the fallback chain is `primary model -> secondary model -> deterministic fallback`
- synchronous and queued paths degrade differently instead of sharing one fragile behavior
- workflow events, correlation IDs, and idempotency keys make post-incident reasoning much easier
- the service fails closed for high-authority mutation ambiguity and degrades open only for safe read-oriented behavior

## 6. Architectural System Design Scenarios

### Scenario 1: Multi-tenant customer-support API with queued escalation lane

**Problem statement**

Design a multi-tenant support copilot that handles `75k requests/min` across chat, email, and CRM surfaces. The product needs `p99 <= 5.0s` for read-only interactive responses, safe escalation to background workflows for complex multi-system actions, and strict tenant isolation for tool calls and cached context.

**Proposed architecture**

```text
┌──────────────────── Scenario 1 ────────────────────┐
│ Web Chat / CRM UI / Ticketing API                    │
└──────────────┬──────────────────────────────────────┘
               v
      ┌──────────────────────┐
      │ API Gateway + Auth   │
      └──────────┬───────────┘
                 v
      ┌─────────────────────────────┐
      │ Policy Engine + Router      │
      └──────────┬───────────┬──────┘
                 │           │
                 v           v
      ┌────────────────┐  ┌─────────────────────┐
      │ Sync API Lane  │  │ Queue + Workflow    │
      │ Fast/Deep LLM  │  │ Orchestrator        │
      └───────┬────────┘  └──────────┬──────────┘
              │                      v
              v             ┌─────────────────────┐
      ┌────────────────┐    │ Worker Pool on K8s  │
      │ MCP / API      │    │ + sandboxed actions │
      │ Tool Proxies   │    └──────────┬──────────┘
      └───────┬────────┘               v
              └───────────────┬────────────────────
                              v
                   ┌─────────────────────┐
                   │ Audit + Traces      │
                   │ + Idempotency Store │
                   └─────────────────────┘
```

Technology choices:

- synchronous read-only responses via routed fast/deep model path
- queue-backed escalations for multi-step CRM mutations or approval-gated actions
- `MCP` or strict API proxies with tenant-scoped RBAC and explicit schema validation
- stateless workers packaged in `Docker` containers and scaled on `Kubernetes`, with workflow state externalized to durable stores

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Synchronous premium-model + direct tool calls only | Highest | Strong p50, weak p95/p99 under dependency drag | Low | Medium to strong, but fragile under retries | Medium |
| Routed sync lane + queued escalation workflow | Medium | Best p95/p99 balance across mixed traffic | Medium to high | Strong because mutations are isolated behind workflow + approval controls | High |
| Browser-first automation for all unsupported tasks | Highest blended cost | Weakest p95/p99 | High | Weakest because visual surfaces are highest risk | Low to medium |

**Decision rationale**

Choose `routed sync lane + queued escalation workflow`. It preserves the fast path for common support questions while isolating slow, high-authority actions behind durable workflow state, queue back-pressure, and audit controls. An all-sync design looks simpler at first, but it collapses under remote dependency variance and replay risk.

### Scenario 2: Enterprise document-processing platform with Kubernetes worker autoscaling

**Problem statement**

Design a document-processing platform that ingests `12 million` documents per day, exposes a public status API, and runs extraction, classification, and enrichment jobs with `99.95%` completion durability. The business wants cheap high-throughput background execution, `p95 <= 3 minutes` for standard jobs, and deterministic recovery from worker/node failures.

**Proposed architecture**

```text
┌──────────────────── Scenario 2 ────────────────────┐
│ Upload API / Batch Import / Webhooks                 │
└──────────────┬──────────────────────────────────────┘
               v
      ┌──────────────────────────┐
      │ Admission API + Policy   │
      └──────────┬───────────────┘
                 v
      ┌──────────────────────────┐
      │ Durable Queue            │
      │ + Workflow History       │
      └──────────┬───────────────┘
                 v
      ┌──────────────────────────┐
      │ K8s Worker Pools         │
      │ parse / enrich / verify  │
      └───────┬─────────┬────────┘
              │         │
              v         v
      ┌─────────────┐  ┌────────────────┐
      │ MCP / APIs  │  │ Model Backends │
      │ DLP / OCR   │  │ fast + premium │
      └──────┬──────┘  └────────┬───────┘
             └──────────┬───────┘
                        v
             ┌────────────────────┐
             │ Checkpoints / DLQ   │
             │ Metrics / Audit     │
             └────────────────────┘
```

Technology choices:

- durable queue plus workflow history for resumable multi-step jobs
- horizontally scaled worker pools on `Kubernetes`, separated by workload class
- dead-letter queue for poison documents and repeated parser failures
- `MCP`/API boundaries for OCR, DLP, and downstream system writes so auth and retries stay explicit

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Monolithic worker service with local retries only | Medium initially | Degrades sharply under backlog | Low | Medium | Medium |
| Queue + workflow history + specialized Kubernetes worker pools | Medium to low at scale | Best durability and steady p95 | High | Strong because trust boundaries and retries are explicit | High |
| Fully serverless per-step execution without durable workflow control | Medium to high | Good p50, unstable long-tail recovery | Medium | Medium | Medium to high |

**Decision rationale**

Choose `queue + workflow history + specialized Kubernetes worker pools`. It creates the cleanest separation between admission, execution, and recovery, which is the dominant reliability theme in the local corpus. The monolith is simpler but brittle under replay and backlog pressure, while pure per-step serverless execution lacks a strong durable-control plane for long-running multi-stage processing.

## Sources

- [1] `research_cursor/research/01-llm-foundations.md` - Local note covering self-hosted/open-weight serving control surfaces such as `vLLM` and broader serving trade-offs.
- [2] `research_cursor/research/03-tool-use.md` - Local note covering API/function tools, hosted containers, browser/computer automation, tool-surface overhead, and production execution boundaries.
- [3] `research_cursor/research/04-agent-architecture.md` - Local note covering control-plane versus data-plane separation, durable workflow concepts, replay boundaries, and topology-driven scaling.
- [4] `research_cursor/research/05-agent-frameworks.md` - Local note covering runtime persistence, checkpoints, approvals, Cloud Run/GKE deployment posture, and multi-instance production considerations.
- [5] `research_cursor/research/07-memory.md` - Local note covering cache stability, replayed context risk, and memory-layer governance under production load.
- [6] `research_cursor/research/08-planning-reasoning.md` - Local note covering planner/executor economics, bounded reasoning, and workflow trade-offs relevant to production routing.
- [7] `research_cursor/research/09-multi-agent-systems.md` - Local note covering remote delegation, transport update modes, worker coordination, and distributed failure domains.
- [8] `research_cursor/research/10-mcp-interoperability.md` - Local note covering Zero-Trust `MCP`, OAuth-style authorization, resource-scoped capability access, and remote reliability boundaries.
- [9] `research_cursor/research/11-specialized-agents.md` - Local note covering containerized execution, sandbox reuse, browser isolation, and specialist runtime deployment patterns.
- [10] `research_cursor/research/12-evaluation.md` - Local note covering cost accounting, container-fee visibility, and evaluation of production trade-offs beyond final-answer quality.
- [11] `research_cursor/research/13-security-guardrails.md` - Local note covering trust boundaries, least-privilege tools, policy gates, and prompt-injection risk on high-authority surfaces.
- [12] `research_cursor/research/14-observability.md` - Local note covering traces, audit evidence, confirmed external effects, and production telemetry design.
- [13] `research_cursor/research/15-inference-optimization.md` - Local note covering cache economics, throughput ceilings, durability pressure, and scaling trade-offs under load.
