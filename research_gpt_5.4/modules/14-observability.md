# Observability — Tracing, Logging, Monitoring, Agent Trajectories

## 1. System Topology & Data Flow

Observability for agent systems is not one logging library bolted onto a chatbot. The production shape is a split control plane and data plane where trajectory capture, cost telemetry, artifact lineage, and policy evidence are explicit subsystems. The core architectural requirement is to preserve `how the run progressed`, `what evidence justified it`, and `what external effect actually happened` as separate but correlated records.

```text
┌────────────────────────────── Control Plane ──────────────────────────────┐
│ API Gateway -> AuthN/Z -> Policy Engine -> Run Coordinator -> SLO Rules   │
│      │               │              │                   │                  │
│      │               │              │                   ├─ Trace Sampling  │
│      │               │              │                   ├─ Redaction       │
│      │               │              │                   └─ Alert Routing   │
│      └────────────────────────────> Correlation ID / Deadline / Tenant     │
└─────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  v
┌─────────────────────────────── Data Plane ────────────────────────────────┐
│ User Turn -> Planner -> Model Turn -> Tool Proxy -> Verifier -> Response  │
│               │            │             │              │                  │
│               │            │             │              └─ effect check    │
│               │            │             ├─ MCP / API / Browser tools      │
│               │            └─ span events / usage / retries               │
│               └─ branch plan / checkpoints / resume lineage               │
└─────────────────────────────────────────────────────────────────────────────┘
          │                     │                     │
          v                     v                     v
┌──────────────────┐   ┌────────────────────┐   ┌──────────────────────────┐
│ Persistence      │   │ Tool Proxies       │   │ Telemetry / Audit Sinks  │
│ run store        │   │ Zero-Trust MCP     │   │ traces / metrics / logs  │
│ checkpoints      │   │ RBAC / approvals   │   │ cost ledger / SIEM       │
│ artifact hashes  │   │ PII filters        │   │ immutable event journal   │
└──────────────────┘   └────────────────────┘   └──────────────────────────┘
```

### Request-flow narrative

1. `API Gateway` authenticates the caller, assigns `run_id`, `correlation_id`, `tenant_id`, and an end-to-end deadline.
2. `Run Coordinator` creates the first trajectory event before the first model token is spent. This matters because admission, policy routing, and queue delay are part of user-visible latency.
3. `Planner` or the first model turn emits a span with `attempt`, `parent_span_id`, token usage, and the selected workflow path such as direct answer, multi-step plan, or specialist delegation.
4. `Tool Proxy` executes external reads or writes behind `MCP` or API boundaries. Observability records both the `attempted action` and the `confirmed external effect` so retries do not masquerade as exactly-once behavior.
5. `Verifier` or post-tool validator classifies the outcome as success, transient failure, permanent failure, or degraded success. This is where replay risk, wrong-record selection, and authorization failures become distinct.
6. `Persistence` stores checkpoints, trace spans, tool artifacts, and evidence references keyed to the same run lineage. Final transcripts alone are not enough for trajectory reconstruction.
7. `Telemetry / Audit Sinks` export logs, metrics, traces, costs, approvals, and redaction decisions to operational and compliance systems. Security-relevant records must survive even when optional debugging detail is sampled down.

The practical split is `trajectory observability` versus `resource observability` versus `evidence observability`. Trajectory observability explains the control flow. Resource observability explains tokens, latency, and retries. Evidence observability explains what retrieval results, tool payloads, or references justified the answer. Production outages often happen when teams instrument only one of the three.

## 2. Core Mechanics & Algorithms

### Agent trajectory as a state machine

Agent observability is strongest when the run is modeled as explicit state transitions instead of a single transcript blob.

```text
ACCEPT
  -> START_RUN
  -> PLAN_OR_ROUTE
  -> EXECUTE_STEP
       -> TOOL_CALL
       -> MODEL_TURN
       -> CHECKPOINT_WRITE
       -> VERIFY_EFFECT
  -> BRANCH_CONTINUE   if confidence is low and budget remains
  -> DEGRADED_COMPLETE if dependency or deadline policy trips
  -> COMPLETE
  -> FAIL
```

This representation exposes the events that matter operationally:

- `steps`: how many bounded decisions the run took
- `branches`: where alternate plans or retries were explored
- `tool calls`: what external capabilities were used
- `resume points`: where durable state allows replay or continuation
- `verification outcomes`: whether an action was merely attempted or actually confirmed

### Core observability units

The cleanest unit hierarchy is:

- `run`: the user-visible request
- `span`: a bounded sub-operation such as planner, tool call, handoff, verifier, or summary model
- `artifact`: any durable evidence object such as tool output, retrieval references, screenshot hash, or checkpoint snapshot
- `effect`: the confirmed external result, distinct from the command that tried to create it

Key invariant:

```text
attempted_action != confirmed_effect
```

If the system records only logical intent, retry and replay analysis becomes misleading.

### Algorithms and complexity

#### Trace reconstruction

Given a run represented as a directed acyclic execution graph:

```text
trace_graph = (V_spans, E_parent_child_edges)
```

Reconstructing the lineage of a completed run is:

- `O(|V| + |E|)` to traverse the full trajectory
- `O(depth)` to rebuild one user-visible branch
- `O(k)` to compute aggregate metrics over `k` selected spans such as tool latency or retry counts

This is why explicit parent-child IDs scale better than trying to infer lineage from free-form logs after the fact.

#### Critical-path latency

Users experience the critical path, not the sum of all branch times:

```text
critical_path_latency
  = queue_wait
  + planning
  + max(parallel_branch_durations)
  + verification
  + checkpoint_persistence
  + response_render
```

This formula is important because parallel branches can increase system work while leaving user-visible latency almost unchanged until a saturated dependency shifts the maximum branch duration upward.

#### Trajectory efficiency

A useful runtime health score is:

```text
trajectory_efficiency
  = useful_spans / total_spans
```

Where `useful_spans` are the spans that contributed evidence or necessary control decisions. A run can be correct but inefficient if it hides repeated retries, useless rewrites, or dead-end branches.

#### Evidence completeness

For retrieval- or tool-backed agents:

```text
evidence_completeness
  = verified_supporting_artifacts / required_supporting_artifacts
```

This is not a formal proof of correctness, but it is a powerful operational invariant. If the model answered without enough evidence artifacts, the run should be flagged even if the prose looks fluent.

### Convergence and correctness invariants

- `max_retries`, `max_branches`, and overall deadline must be explicit.
- Every span needs `run_id`, `span_id`, `parent_span_id`, `attempt`, and `tenant_id`.
- Every mutation-capable tool call needs an `idempotency_key`.
- Every checkpoint must preserve both workflow state and the policy version that governed the step.
- Every external artifact must carry a `trust_level`; low-trust tool or browser output cannot be silently promoted into high-trust instruction space.
- Every run must track both `task_success` and `trajectory_efficiency`; success alone hides thrash.

## 3. Token Economics & NFR Analysis

> ⚠️ Gap: The local `research_cursor` set is strong on token counters, cache multipliers, tool overheads, checkpoint semantics, and structural latency trade-offs, but weak on universal public percentile benchmarks for observability-heavy agent stacks. The `p50/p95/p99` targets below are engineering SLO envelopes, not vendor guarantees.

### Cost formulas

Assumptions used below:

- `runs = 1000`
- `U` = uncached input tokens for dynamic trajectory context per run
- `C` = stable cache-eligible prefix tokens for schemas, policy pack, and trace headers
- `h` = cache hit rate on `C`
- `O` = output tokens per run
- `P_in_fast`, `P_out_fast` = fast-tier model prices per `1M` tokens
- `P_in_deep`, `P_out_deep` = deep-tier model prices per `1M` tokens
- cache read cost is approximated as `0.1x` input-token price where supported
- cache write cost is approximated as `1.25x` input-token price where supported
- `E` = trace events persisted per run
- `P_evt` = persistence + index cost per trace event
- `A` = artifact writes per run
- `P_art` = storage/index cost per artifact write
- browser-tool declarations can add roughly `6,610-6,670` input tokens before screenshots in the local corpus
- computer-tool declarations can add roughly `4,520-4,590` input tokens before screenshots in the local corpus

Reusable cached-input primitive:

```text
effective_input_cost(model)
  = (
      U * P_in_model
      + C * (1 - h) * 1.25 * P_in_model
      + C * h * 0.10 * P_in_model
    ) / 1_000_000
```

#### Metrics-only observability path

This is the lowest-latency path: counters, spans, and logs are emitted without LLM-based post-processing.

```text
$ cost per 1k runs
  = 1000 * (
      effective_input_cost(fast_model)
      + (O * P_out_fast) / 1_000_000
    )
    + (runs * E * P_evt)
    + (runs * A * P_art)
```

Use this for inline usage telemetry, cheap run summaries, and basic alerting. It is the right default for high-volume copilot traffic.

#### Trajectory-judge path

This path evaluates or summarizes the run after execution, often with a deeper model because it needs to classify retries, evidence quality, or policy drift.

```text
$ cost per 1k runs
  = 1000 * (
      effective_input_cost(deep_model)
      + (O * P_out_deep) / 1_000_000
    )
    + (runs * E * P_evt)
    + (runs * A * P_art)
```

The deeper model spend is often justified only for sampled or high-risk runs. Applying it to every request usually makes observability more expensive than the business action being observed.

#### Browser-backed trajectory path

When the agent uses browser or computer tools, observability has a large fixed token floor before screenshots and tool results are even included.

```text
$ browser-backed observability per 1k runs
  = 1000 * (
      effective_input_cost(model)
      + ((T_browser_overhead + T_tool_results + T_screenshots) * P_in_model) / 1_000_000
      + (O * P_out_model) / 1_000_000
    )
    + (runs * E * P_evt)
    + (runs * A * P_art)
```

The fixed overhead explains why UI-driven observability should be sampled, bounded, or downgraded to API-level checks when possible.

### Worked numeric example

Use the following explicit example assumption set for `1000` runs:

- `U = 1800`
- `C = 3200`
- `h = 0.80`
- `O = 250`
- `P_in_fast = $0.30 / 1M`
- `P_out_fast = $1.20 / 1M`
- `P_in_deep = $3.00 / 1M`
- `P_out_deep = $12.00 / 1M`
- persistence and artifact costs are left as `P_evt` and `P_art` because the local corpus is strong on token economics and weaker on universal telemetry vendor pricing

Fast-path model cost:

```text
effective_input_cost(fast)
  = (
      1800 * 0.30
      + 3200 * (1 - 0.80) * 1.25 * 0.30
      + 3200 * 0.80 * 0.10 * 0.30
    ) / 1_000_000
  = (540 + 240 + 76.8) / 1_000_000
  = $0.0008568 per run
```

Metrics-only model total:

```text
$ model cost per 1k runs
  = 1000 * (
      0.0008568
      + (250 * 1.20) / 1_000_000
    )
  = 1000 * (0.0008568 + 0.0003)
  = $1.1568 per 1k runs
```

Deep trajectory-judge model total:

```text
effective_input_cost(deep)
  = (
      1800 * 3.00
      + 3200 * (1 - 0.80) * 1.25 * 3.00
      + 3200 * 0.80 * 0.10 * 3.00
    ) / 1_000_000
  = (5400 + 2400 + 768) / 1_000_000
  = $0.008568 per run
```

```text
$ model cost per 1k trajectory-judge runs
  = 1000 * (
      0.008568
      + (250 * 12.00) / 1_000_000
    )
  = 1000 * (0.008568 + 0.003)
  = $11.568 per 1k runs
```

This makes the design rule concrete: full trajectory judging is about `10x` the model cost of the fast metrics-only path under the same token shape, so it should be targeted at sampled, high-value, or high-risk traffic.

### Latency targets

Recommended user-facing SLO envelopes by workload shape:

- `metrics-only inline instrumentation`: `p50 <= 150ms`, `p95 <= 500ms`, `p99 <= 1.0s`
- `interactive trace summary or evaluator`: `p50 <= 1.2s`, `p95 <= 3.5s`, `p99 <= 6.0s`
- `browser-backed or trajectory-heavy reconstruction`: `p50 <= 3.0s`, `p95 <= 8.0s`, `p99 <= 12.0s`

Mitigations by percentile:

- `p50`: warm trace exporters, cache stable prompt prefixes, colocate trace store and run coordinator, stream lightweight status first
- `p95`: cap branch fan-out, sample bulky artifacts, parallelize independent validators, truncate trace payloads before judge-model calls
- `p99`: propagate deadlines, bulkhead slow exporters, open circuit breakers on degraded telemetry sinks, downgrade to counters-only mode rather than stalling the user path

### Throughput and back-pressure

Observability capacity planning should be expressed in both event volume and token volume.

Useful sizing heuristics:

```text
trace_events_per_second
  = qps * E
```

```text
judge_tokens_per_second
  = qps * (U + C + O)
```

```text
safe_qps
  = min(
      trace_ingest_capacity / E,
      judge_model_tps / (U + C + O),
      artifact_store_writes_per_second / A
    )
```

Back-pressure order:

1. drop optional explanation text before dropping core metrics
2. reduce trajectory-judge sampling before reducing raw audit capture
3. skip screenshots or large tool payload reinjection before skipping effect verification
4. fail closed for privileged writes, but allow counters-only degraded telemetry for read-only traffic

### Non-functional requirements

- `availability`: `99.9%` for inline metrics/logging, `99.95%` for trace and artifact persistence, `99.99%` for immutable audit journal of privileged actions
- `RPO`: `0` for approved mutation events and idempotency ledger, `<= 1 minute` for checkpoints, `<= 5 minutes` for mirrored trace indexes
- `RTO`: `<= 15 minutes` for regional trace-ingest failover, `<= 30 minutes` for checkpoint-store recovery, `<= 4 hours` for backfilling sampled analytics indexes
- `compliance`: keep actor identity, tenant identity, policy version, approval outcome, and artifact lineage separable
- `privacy`: traces must support field-level redaction or tokenization because tool outputs, retrieved records, screenshots, and raw prompts often contain sensitive data

## 4. Distributed Resilience & Security

Observability is trustworthy only if it survives retries, replay, partial outages, and policy boundaries without widening privilege or corrupting lineage.

### Durable execution

Recommended pattern:

- use `Temporal`, `LangGraph` durable checkpoints, or an equivalent workflow engine for long-running agents
- publish run events and tool outcomes to `Kafka` or an append-only event bus
- checkpoint after `plan_created`, `tool_called`, `tool_result_received`, `verification_complete`, and `response_sent`
- store large artifacts by reference plus hash, not by repeatedly copying them into prompt history
- route exhausted or poison events to a dead-letter stream with preserved `run_id`, `tenant_id`, `action_hash`, and failure class

Durable flow:

```text
request_received
  -> checkpoint_written
  -> model_or_tool_step
  -> attempted_action_recorded
  -> effect_verified
  -> audit_persisted
  -> complete
```

This ordering matters because a replay-safe system must be able to prove whether the world changed, not merely whether the model intended it to change.

### Failure taxonomy

`Transient failures`

- trace exporter timeout
- temporary checkpoint-store unavailability
- `429` or transport failure from a telemetry sink
- network flaps between coordinator and remote tool server

`Permanent failures`

- invalid schema in trace payload
- revoked credentials for sink or tool proxy
- RBAC denial for artifact access
- unsupported or malformed event version

`Poison-pill failures`

- one artifact repeatedly breaks indexing or serialization
- one screenshot or tool payload exceeds allowed size and replays forever
- one corrupted checkpoint causes deterministic resume failure

`Semantic failures`

- correct final answer with hidden retry storm
- tool side effect succeeded but effect verification never persisted
- answer cites evidence that is missing from retained artifacts
- sampled traces systematically exclude the failing branch class

Required controls:

- idempotency keys on every write-capable tool call
- checkpoint versioning so replay uses the same policy pack as the original run
- effect-verification records separate from attempted-action records
- dead-letter retention long enough for forensic review

### Circuit breakers and graceful degradation

Telemetry dependencies must not be allowed to take down the primary user path.

```text
CLOSED
  -> OPEN       after sustained exporter failure or timeout breach
  -> HALF_OPEN  after cooldown
  -> CLOSED     after successful probe window
  -> OPEN       if probes fail
```

Independent breakers should exist for:

- trace exporter
- checkpoint store
- artifact store
- primary model
- privileged tool proxy

Graceful degradation order:

1. full trace + artifact persistence
2. counters + critical spans + sampled artifacts
3. counters + immutable audit for privileged events only
4. deterministic degraded response with explicit `degraded=true`

The point is not to hide the outage. The point is to preserve the minimum safe evidence set while protecting the user-facing workflow from total collapse.

### Enterprise security controls

Zero-Trust `MCP` and tool boundary:

- every tool request terminates at a policy-enforcing proxy
- proxies inject least-privilege credentials instead of handing raw secrets to the model
- trace readers and trace writers get separate RBAC policies
- approval events are first-class trace artifacts, not side notes in logs

PII filtering pipeline:

1. detect sensitive spans in prompts, tool results, retrieval artifacts, and screenshots
2. redact or tokenize before long-term trace persistence
3. preserve re-identification mappings only in isolated vault systems if business policy requires them
4. emit immutable audit events for every redaction, disclosure, and export decision

Auditability requirements:

- immutable event journal for `run_started`, `tool_called`, `approval_recorded`, `effect_verified`, `fallback_used`, and `response_sent`
- source lineage from answer -> span -> artifact hash -> external system record or document version
- separate storage classes for hot debugging traces and compliance-grade audit evidence

> ⚠️ Gap: The local `research_cursor` set is stronger on protocol boundaries, checkpointing, and trace payload sensitivity than on universal built-in provider guarantees for immutable storage, first-party PII redaction, or fine-grained RBAC over hosted trace systems. Enterprises should design those guarantees explicitly.

## 5. Production Enterprise Code

The runnable Python example below demonstrates a trajectory-aware service with structured logging, retries with exponential backoff and jitter, circuit breakers, a primary-to-secondary-to-deterministic fallback chain, checkpointed trace events, PII redaction, and graceful degradation when the telemetry sink is unavailable.

```python
from __future__ import annotations

import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional


class FailureCategory(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class ServiceError(Exception):
    def __init__(self, message: str, category: FailureCategory) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class TraceEvent:
    run_id: str
    correlation_id: str
    tenant_id: str
    span_id: str
    parent_span_id: Optional[str]
    name: str
    status: str
    attempt: int
    payload: str
    timestamp_ms: int


@dataclass(frozen=True)
class ToolResult:
    attempted_action: str
    confirmed_effect: str
    data: str


@dataclass(frozen=True)
class RunResponse:
    answer: str
    degraded: bool
    reason: Optional[str]
    trace_events_persisted: int


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "message": record.getMessage(),
            "time_ms": int(record.created * 1000),
        }
        for key in ("run_id", "correlation_id", "tenant_id", "event", "degraded"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, separators=(",", ":"))


def build_logger() -> logging.Logger:
    logger = logging.getLogger("observability_module")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOGGER = build_logger()


def log(message: str, run_id: str, correlation_id: str, tenant_id: str, event: str, degraded: bool = False) -> None:
    LOGGER.info(
        message,
        extra={
            "run_id": run_id,
            "correlation_id": correlation_id,
            "tenant_id": tenant_id,
            "event": event,
            "degraded": degraded,
        },
    )


def retry_with_backoff(
    fn: Callable[[], None],
    max_attempts: int,
    base_delay_s: float,
    max_delay_s: float,
) -> None:
    for attempt in range(1, max_attempts + 1):
        try:
            fn()
            return
        except ServiceError as exc:
            if exc.category == FailureCategory.PERMANENT or attempt == max_attempts:
                raise
            delay = min(max_delay_s, base_delay_s * (2 ** (attempt - 1)))
            jitter = random.uniform(0.0, delay * 0.25)
            time.sleep(delay + jitter)


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


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


class PiiRedactor:
    EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

    @classmethod
    def redact(cls, text: str) -> str:
        return cls.EMAIL_PATTERN.sub("[REDACTED_EMAIL]", text)


class TraceStore:
    def __init__(self) -> None:
        self.events: List[TraceEvent] = []

    def persist(self, event: TraceEvent) -> None:
        if "trace_fail" in event.payload:
            raise ServiceError("trace store temporarily unavailable", FailureCategory.TRANSIENT)
        self.events.append(event)


class AuditBuffer:
    def __init__(self) -> None:
        self.events: List[TraceEvent] = []

    def persist(self, event: TraceEvent) -> None:
        self.events.append(event)


class ToolGateway:
    def lookup(self, query: str) -> ToolResult:
        if "tool_fail" in query:
            raise ServiceError("tool gateway timeout", FailureCategory.TRANSIENT)
        attempted_action = f"lookup:{query}"
        confirmed_effect = "read_only_lookup_completed"
        data = f"customer_email=owner@example.com; summary=policy allows credit for service outage; query={query}"
        return ToolResult(
            attempted_action=attempted_action,
            confirmed_effect=confirmed_effect,
            data=data,
        )


class Model:
    def __init__(self, name: str, fail_on: str = "") -> None:
        self.name = name
        self.fail_on = fail_on

    def generate(self, prompt: str) -> str:
        if self.fail_on and self.fail_on in prompt:
            raise ServiceError(f"{self.name} transport error", FailureCategory.TRANSIENT)
        return f"{self.name} answer: {prompt[:120]}"


def deterministic_fallback(query: str, tool_result: ToolResult) -> str:
    return (
        f"Deterministic response for '{query}'. "
        f"Observed effect={tool_result.confirmed_effect}. "
        f"Evidence={tool_result.data[:80]}"
    )


class ObservabilityAwareService:
    def __init__(self, primary_model: Model, secondary_model: Model, tool_gateway: ToolGateway) -> None:
        self.primary_model = primary_model
        self.secondary_model = secondary_model
        self.tool_gateway = tool_gateway
        self.trace_store = TraceStore()
        self.audit_buffer = AuditBuffer()
        self.trace_breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_s=5.0)
        self.model_breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_s=5.0)

    def run(self, query: str, tenant_id: str) -> RunResponse:
        run_id = str(uuid.uuid4())
        correlation_id = str(uuid.uuid4())
        trace_count = 0
        degraded = False
        reason: Optional[str] = None

        log("starting run", run_id, correlation_id, tenant_id, "run_started")
        trace_count += self._persist_event(
            run_id=run_id,
            correlation_id=correlation_id,
            tenant_id=tenant_id,
            span_id="root",
            parent_span_id=None,
            name="run_started",
            status="ok",
            attempt=1,
            payload=f"query={query}",
        )

        tool_result = self.tool_gateway.lookup(query)
        redacted_data = PiiRedactor.redact(tool_result.data)
        trace_count += self._persist_event(
            run_id=run_id,
            correlation_id=correlation_id,
            tenant_id=tenant_id,
            span_id="tool_lookup",
            parent_span_id="root",
            name="tool_lookup",
            status="ok",
            attempt=1,
            payload=f"attempted={tool_result.attempted_action}; effect={tool_result.confirmed_effect}; data={redacted_data}",
        )

        prompt = (
            f"Question: {query}\n"
            f"Attempted action: {tool_result.attempted_action}\n"
            f"Confirmed effect: {tool_result.confirmed_effect}\n"
            f"Evidence: {redacted_data}\n"
            "Summarize the result for an enterprise operator."
        )

        answer: Optional[str] = None
        try:
            self.model_breaker.before_call()
            answer = self._generate_with_retry(self.primary_model, prompt)
            self.model_breaker.on_success()
        except ServiceError:
            self.model_breaker.on_failure()
            degraded = True
            reason = "primary_model_failed"
            log("primary model failed, escalating fallback", run_id, correlation_id, tenant_id, "primary_model_failed", degraded=True)

        if answer is None:
            try:
                answer = self._generate_with_retry(self.secondary_model, prompt)
                degraded = True
                reason = reason or "secondary_model_used"
            except ServiceError:
                answer = deterministic_fallback(query, tool_result)
                degraded = True
                reason = "deterministic_fallback"
                log("secondary model failed, using deterministic fallback", run_id, correlation_id, tenant_id, "secondary_model_failed", degraded=True)

        trace_count += self._persist_event(
            run_id=run_id,
            correlation_id=correlation_id,
            tenant_id=tenant_id,
            span_id="final_answer",
            parent_span_id="root",
            name="final_answer",
            status="degraded" if degraded else "ok",
            attempt=1,
            payload=PiiRedactor.redact(answer),
        )
        log("completed run", run_id, correlation_id, tenant_id, "run_completed", degraded=degraded)
        return RunResponse(answer=answer, degraded=degraded, reason=reason, trace_events_persisted=trace_count)

    def _generate_with_retry(self, model: Model, prompt: str) -> str:
        result: dict[str, str] = {}

        def call() -> None:
            result["answer"] = model.generate(prompt)

        retry_with_backoff(call, max_attempts=3, base_delay_s=0.05, max_delay_s=0.2)
        return result["answer"]

    def _persist_event(
        self,
        run_id: str,
        correlation_id: str,
        tenant_id: str,
        span_id: str,
        parent_span_id: Optional[str],
        name: str,
        status: str,
        attempt: int,
        payload: str,
    ) -> int:
        event = TraceEvent(
            run_id=run_id,
            correlation_id=correlation_id,
            tenant_id=tenant_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            name=name,
            status=status,
            attempt=attempt,
            payload=payload,
            timestamp_ms=int(time.time() * 1000),
        )

        try:
            self.trace_breaker.before_call()
            self.trace_store.persist(event)
            self.trace_breaker.on_success()
            return 1
        except ServiceError:
            self.trace_breaker.on_failure()
            self.audit_buffer.persist(event)
            return 0


if __name__ == "__main__":
    service = ObservabilityAwareService(
        primary_model=Model("primary-model", fail_on="primary_fail"),
        secondary_model=Model("secondary-model", fail_on="secondary_fail"),
        tool_gateway=ToolGateway(),
    )

    response = service.run(
        query="Explain the approved customer credit for outage case 4242 without exposing owner@example.com.",
        tenant_id="acme",
    )
    print(response)
```

Why this code matters:

- retries use exponential backoff with jitter and stop only after the bounded retry budget is exhausted
- the circuit breakers explicitly model `closed -> open -> half-open`
- the fallback chain is `primary model -> secondary model -> deterministic fallback`
- all logs carry `run_id`, `correlation_id`, and `tenant_id`
- trace persistence degrades to a local audit buffer if the trace store fails, so the user path stays alive while minimum evidence is retained
- PII redaction happens before long-term persistence, not after an incident review request

## 6. Architectural System Design Scenarios

### Scenario 1: Multi-tenant support copilot observability plane

**Problem statement**

Design the observability plane for a customer-facing SaaS support copilot serving `40k requests/min`. The business wants per-run cost, latency, citations, tool-attempt lineage, and retry visibility, while keeping read-only observability overhead within `p99 <= 1.0s` and preserving tenant isolation.

**Proposed architecture**

```text
┌──────────────────── Scenario 1 ────────────────────┐
│ Web / Chat Clients                                  │
└──────────────┬──────────────────────────────────────┘
               v
      ┌─────────────────┐
      │ API + AuthN/Z   │
      └──────┬──────────┘
             v
      ┌──────────────────────────────┐
      │ Support Agent Runtime        │
      │ planner / model / tools      │
      └──────┬──────────┬────────────┘
             │          │
             v          v
      ┌────────────┐  ┌─────────────────┐
      │ Trace Bus  │  │ Zero-Trust MCP  │
      └──────┬─────┘  └────────┬────────┘
             v                 v
      ┌────────────┐    ┌───────────────┐
      │ Run Store  │    │ CRM / KB APIs │
      └──────┬─────┘    └───────────────┘
             v
      ┌──────────────────────────────┐
      │ Metrics / Logs / Cost Ledger │
      └──────────────────────────────┘
```

Technology choices:

- event bus for low-latency span export
- tenant-scoped run store with checkpoint history
- `MCP` or API proxies enforcing least privilege for ticket, CRM, and knowledge-base calls
- sampled deep trajectory judges only on retries, escalations, or degraded runs

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Final transcript logging only | Lowest | Best p50 | Lowest | Weakest lineage and replay visibility | Low once tools and retries grow |
| Metrics + traces + sampled trajectory judge | Medium | Strong p95/p99 with bounded overhead | Medium | Strong tenant lineage and evidence retention | High |
| Full trajectory judge on every run | Highest | Weakest p95/p99 | High | Strong debugging depth, but excessive data exposure and cost | Medium |

**Decision rationale**

Choose `metrics + traces + sampled trajectory judge`. Transcript-only logging is too weak because correct support answers can still hide retry storms, wrong-record lookups, or tool thrash. Full judging on every request is operationally expensive and increases sensitive-data surface area. Sampling deeper analysis only on risky runs preserves cost control while still capturing the failure modes support teams actually need to debug.

### Scenario 2: Regulated finance operations agent with replay-safe audit lineage

**Problem statement**

Design an observability architecture for a finance operations agent that recommends or executes `refunds`, `credit holds`, and `invoice adjustments`. The business requires `99.99%` durable audit capture for privileged actions, `RPO = 0` for mutation approvals and idempotency evidence, and `p95 <= 4.0s` observability processing excluding human wait time.

**Proposed architecture**

```text
┌──────────────────── Scenario 2 ────────────────────┐
│ Analyst UI / Approval Console                       │
└──────────────┬──────────────────────────────────────┘
               v
      ┌──────────────────────┐
      │ Workflow Engine      │
      │ checkpoints / resume │
      └──────┬───────────────┘
             ├──────────────────────┐
             v                      v
      ┌──────────────┐       ┌───────────────┐
      │ Audit Journal│       │ Trace Index   │
      └──────┬───────┘       └──────┬────────┘
             v                      v
      ┌──────────────────────────────────────┐
      │ Policy Proxy + Effect Verifier       │
      └──────────────┬───────────────────────┘
                     v
      ┌──────────────────────────────────────┐
      │ ERP / Billing / Ledger APIs          │
      └──────────────────────────────────────┘
```

Technology choices:

- durable workflow engine with checkpointing before and after approval and side-effect boundaries
- append-only audit journal for approvals, idempotency keys, and confirmed effects
- separate hot trace index for debugging so compliance evidence is not coupled to the same retention path
- strict RBAC split between trace readers, approvers, and write-capable service identities

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Centralized immutable audit journal only | Medium | Strong | Medium | Strongest compliance core, weakest debugging detail | High |
| Audit journal + checkpointed trace index + effect verifier | High | Medium | High | Best replay safety, lineage, and RCA depth | High |
| Vendor-hosted traces only with minimal local audit | Low to medium | Strong p50 | Low to medium | Weakest control over retention, redaction, and exactly-once evidence | Medium |

**Decision rationale**

Choose `audit journal + checkpointed trace index + effect verifier`. Finance operations need more than debugging convenience. They need proof that an approval occurred, proof of the intended command, proof of the confirmed external effect, and proof that a replay did not duplicate the mutation. A local immutable journal paired with checkpointed traces preserves both compliance-grade evidence and operator-friendly diagnosis, while vendor-hosted traces alone leave too much control outside the enterprise boundary.

## Sources

- [1] `research_cursor/research/03-tool-use.md` - Local note covering tool-call traces, approval events, browser/computer overhead, usage signals, and replay-related failure modes.
- [2] `research_cursor/research/04-agent-architecture.md` - Local note covering control-plane versus data-plane boundaries, checkpoints, ReAct loops, and planner/executor trajectories.
- [3] `research_cursor/research/05-agent-frameworks.md` - Local note covering LangGraph, OpenAI Agents SDK, Google ADK, and CrewAI observability surfaces such as sessions, spans, checkpoints, and usage metrics.
- [4] `research_cursor/research/06-rag.md` - Local note covering references, activity logs, retrieval artifacts, and evidence-linked diagnosis.
- [5] `research_cursor/research/07-memory.md` - Local note covering memory-layer diagnostics, cache behavior, episodic logs, and observation drift from stale state.
- [6] `research_cursor/research/08-planning-reasoning.md` - Local note covering verifier loops, replanning storms, and the difference between correctness, authorization, and hidden trajectory thrash.
- [7] `research_cursor/research/09-multi-agent-systems.md` - Local note covering supervisor-worker lineage, hidden nested state, and remote delegation failure domains.
- [8] `research_cursor/research/10-mcp-interoperability.md` - Local note covering Zero-Trust `MCP`, auth boundaries, and protocol-level observability implications.
- [9] `research_cursor/research/11-specialized-agents.md` - Local note covering browser, research, coding, and data specialists plus their observability-specific failure modes.
- [10] `research_cursor/research/12-evaluation.md` - Local note covering trajectory scoring, tool-accuracy evaluation, runtime-artifact scoring, and cost/latency separation.
- [11] `research_cursor/research/13-security-guardrails.md` - Local note covering policy checks, approval flows, PII sensitivity of traces, and fail-closed control patterns.
