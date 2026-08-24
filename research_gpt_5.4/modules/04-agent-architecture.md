# Agent Architecture — ReAct, Loops, Planning, State, Workflows

## 1. System Topology & Data Flow

Agent architecture is easiest to reason about when split into a control plane and a data plane. The control plane decides what should happen next, how state is persisted, whether a tool call is permitted, and how failures are handled. The data plane performs the expensive work: model inference, retrieval, tool I/O, and response synthesis.

```text
┌────────────────────────── Control Plane ──────────────────────────┐
│  API Gateway  ->  Policy/Router  ->  Planner/Loop Controller     │
│       │                 │                    │                    │
│       │                 │                    ├─> Approval Service │
│       │                 │                    ├─> Retry Policy     │
│       │                 │                    ├─> Circuit Breakers │
│       │                 │                    └─> Deadline Budget  │
│       │                 │                                         │
│       └────────────────> Correlation-ID / Trace Context           │
└────────────────────────────────────────────────────────────────────┘
                              │
                              v
┌──────────────────────────── Data Plane ───────────────────────────┐
│  Model Runtime  <->  Tool Proxy / MCP Gateway  <->  Business APIs │
│       │                         │                    CRM / ERP     │
│       │                         │                    Search / DB   │
│       │                         └────────────────> File / Queue    │
└────────────────────────────────────────────────────────────────────┘
            │                         │                        │
            v                         v                        v
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│ Checkpoint Store │      │ Event / State DB │      │ Telemetry Sinks  │
│ thread_id/run_id │      │ sessions/memory  │      │ logs/traces/SLOs │
│ pending writes   │      │ idempotency keys │      │ alerts/audits    │
└──────────────────┘      └──────────────────┘      └──────────────────┘
```

### Request-flow narrative

1. `API Gateway` receives a request, assigns a `correlation_id`, enforces tenant auth, and starts an end-to-end deadline.
2. `Policy/Router` classifies the work: plain ReAct, planner/executor, deterministic workflow, or async durable job.
3. `Planner/Loop Controller` initializes run state, tool budgets, recursion limits, and checkpoint boundaries.
4. `Model Runtime` produces either a final answer, a structured plan, or a tool invocation request.
5. `Tool Proxy / MCP Gateway` validates schema, checks RBAC, injects least-privilege credentials, and calls downstream systems.
6. Tool observations flow back into the controller, which either continues the loop, parallelizes child tasks, requests human approval, or terminates.
7. Every transition writes structured telemetry and state snapshots so the run can be resumed without duplicating external side effects.

The key architectural point is that the model should not own control flow by itself. Production systems externalize limits, retries, persistence, approvals, and auditability into explicit runtime components.

## 2. Core Mechanics & Algorithms

### ReAct as a state machine

ReAct is not just a prompt pattern; in production it is a bounded state machine:

```text
RECEIVE
  -> PLAN
  -> ACT
  -> OBSERVE
  -> EVALUATE
     -> PLAN       if more work remains and turn budget is available
     -> HANDOFF    if a specialist agent or human must take over
     -> COMPLETE   if success criteria are met
     -> FAIL       if policy, deadline, or safety guard trips
```

Useful invariants:

- Every run has a stable `run_id`, `tenant_id`, and monotonic `step_index`.
- Every side-effecting tool call carries an idempotency key derived from `(run_id, step_index, tool_name)`.
- Every loop has explicit termination guards: `max_turns`, deadline budget, cost budget, and recursion depth.
- Every persisted checkpoint represents a semantically valid resume point.

### Topology choices

#### ReAct loop

Best when the task is open-ended and tool choices depend heavily on intermediate observations. The downside is serial latency: each tool result usually triggers another model round-trip.

Approximate behavior:

- Latency: `O(k * (L_model + L_tool))`
- Token growth: `O(sum(history_t))`, unless history is compacted or cached
- Operational risk: high exposure to infinite loops, context bloat, and cascading retries

#### Planner/executor

A stronger model plans once, then a cheaper executor carries out bounded steps. This reduces both serial latency and token spend because the planner is not invoked on every tool result.

Approximate behavior:

- Latency: `O(L_plan + critical_path(executor_steps))`
- Cost: lower when executor turns can use a cheaper model tier
- Operational fit: strong for enterprise actions because approvals and policy checks can sit at executor boundaries

#### Parallel DAG planning

Independent subtasks are decomposed into a dependency graph and executed concurrently. This is the highest-performance pattern when work fans out across retrieval, search, or analysis tasks.

Approximate behavior:

- Makespan: `O(max_path_latency + scheduler_overhead)`
- Cost: lower than serial ReAct when the graph exposes parallel work and avoids repeated planner calls
- Operational risk: higher orchestration complexity, especially for replay, joins, and partial failure recovery

### State model

A practical state model separates three scopes:

- `session state`: request-scoped scratchpad, plan, tool observations, current turn count
- `workflow state`: durable checkpoints, pending writes, approval pauses, retry metadata
- `long-term memory`: user profile, historical facts, embeddings, or domain memory across sessions

The main correctness constraint is deterministic recovery. If a process crashes after a tool succeeds but before the next model call, the runtime must resume from a persisted observation instead of reissuing the tool call blindly.

## 3. Token Economics & NFR Analysis

> ⚠️ Gap: Public sources document pricing and rate-limit mechanics well, but they do not publish stable cross-framework p50/p95/p99 latency distributions for complete enterprise agent runs. The targets below are recommended internal SLOs, not vendor guarantees.

### Cost formulas

Assumptions for the worked formulas below:

- `turns = 8`
- Stable prompt/tool prefix per turn: `prefix_tokens = 3000`
- Fresh user/tool observation input per turn: `fresh_input_tokens = 500`
- Model output per turn: `output_tokens = 300`
- Tool surcharge: `$0.00` per run unless noted separately
- Pricing snapshot from the research note's cited Aug 2026 public pricing pages

#### ReAct with prompt caching

```text
$ per 1k runs =
1000 * (
  (turns * fresh_input_tokens / 1_000_000) * input_price_per_million +
  (prefix_tokens / 1_000_000) * cache_write_price_per_million +
  (((turns - 1) * prefix_tokens) / 1_000_000) * cache_read_price_per_million +
  (turns * output_tokens / 1_000_000) * output_price_per_million +
  tool_surcharge_per_run
)
```

For `gpt-5.6-terra` with `input=$2.00/M`, `cache_write=$2.50/M`, `cache_read=$0.20/M`, `output=$12.00/M`:

```text
$ per 1k runs =
1000 * (
  (4000 / 1_000_000) * 2.00 +
  (3000 / 1_000_000) * 2.50 +
  (21000 / 1_000_000) * 0.20 +
  (2400 / 1_000_000) * 12.00
)
= 1000 * 0.0485
= $48.50 per 1k runs
```

Without caching:

```text
$ per 1k runs =
1000 * (
  (((prefix_tokens + fresh_input_tokens) * turns) / 1_000_000) * input_price_per_million +
  (turns * output_tokens / 1_000_000) * output_price_per_million
)

= 1000 * (
  (28000 / 1_000_000) * 2.00 +
  (2400 / 1_000_000) * 12.00
)
= $84.80 per 1k runs
```

#### Planner/executor split

Assumptions:

- Planner: one `gpt-5.6-sol` call with `4000` input and `600` output tokens
- Executor: six `gpt-5.6-luna` turns with a `2000` token cached prefix, `300` fresh input tokens per turn, and `200` output tokens per turn

```text
$ per 1k runs =
1000 * (
  planner_input_tokens / 1_000_000 * planner_input_price +
  planner_output_tokens / 1_000_000 * planner_output_price +
  executor_fresh_input_tokens / 1_000_000 * executor_input_price +
  executor_cache_write_tokens / 1_000_000 * executor_cache_write_price +
  executor_cache_read_tokens / 1_000_000 * executor_cache_read_price +
  executor_output_tokens / 1_000_000 * executor_output_price
)
```

Worked example:

```text
$ per 1k runs =
1000 * (
  (4000 / 1_000_000) * 5.00 +
  (600 / 1_000_000) * 30.00 +
  (1800 / 1_000_000) * 0.20 +
  (2000 / 1_000_000) * 0.25 +
  (10000 / 1_000_000) * 0.02 +
  (1200 / 1_000_000) * 1.20
)
= 1000 * 0.0405
= $40.50 per 1k runs
```

The planner/executor split is cheaper here because the expensive model is amortized across the entire task while the executor runs on a low-cost tier.

### Latency targets

For a user-facing enterprise copilot with moderate tool use, a reasonable target envelope is:

- `p50 <= 1.5s` for simple answer-only requests and `<= 3.0s` for tool-using requests
- `p95 <= 4.0s` for simple requests and `<= 8.0s` for tool-using requests
- `p99 <= 8.0s` for simple requests and `<= 15.0s` for tool-using requests

Mitigations by percentile:

- `p50`: stable prompt prefixes, warm HTTP pools, cheap executor model, streaming first token in under `600ms`
- `p95`: parallel retrieval fan-out, bounded tool deadlines, transcript compaction, cache-aware routing
- `p99`: circuit breakers, deadline propagation, fallback model chain, graceful removal of optional enrichments, queue shedding

### Throughput and back-pressure

Capacity planning starts with the tighter of request and token budgets:

```text
max_runs_per_minute ~= min(
  provider_rpm / avg_model_turns_per_run,
  provider_tpm / avg_total_tokens_per_run
)
```

For cache-aware limits where cached reads are discounted or excluded:

```text
effective_runs_per_minute ~= min(
  rpm / turns_per_run,
  itpm / (uncached_input_tokens_per_run + cache_write_tokens_per_run)
)
```

Recommended back-pressure policy:

- Below `70%` of provider quota: accept normally
- `70%` to `85%`: reduce retrieval fan-out, lower planner depth, batch low-priority requests
- `85%` to `95%`: queue non-interactive jobs, disable optional tools, enforce per-tenant concurrency limits
- Above `95%` sustained for `30s`: reject low-priority traffic with retry hints and open read-only fallback mode

### Availability, RPO/RTO, and compliance

Recommended enterprise targets:

- Availability: `99.9%` for synchronous copilot APIs; `99.95%` for durable async workflow control planes
- `RPO <= 5 minutes` for conversational state on standard replicated stores; effectively near-zero when all critical events land in append-only workflow history before acknowledgment
- `RTO <= 30 minutes` for regional failover of session and checkpoint services

Compliance design points:

- `SOC 2`: immutable audit logs, least-privilege credentials, traceable approvals
- `GDPR`: data residency controls, deletion workflow for long-term memory, prompt/log minimization
- `HIPAA` or `PCI` when applicable: PII/PHI tokenization before model ingress, encrypted storage, segregated tool credentials, explicit access audit trails

### Explicit NFR trade-off analysis

The main production trade-offs are not whether to pursue these NFRs, but how much cost and complexity to accept in order to tighten them:

- `Availability vs. cost`: a single-region `99.9%` copilot stack is materially cheaper and simpler than multi-region active-active, but active-active buys better blast-radius isolation and faster failover. The trade-off is duplicated warm capacity, more cross-region telemetry, and harder debugging of partial regional faults.
- `RPO/RTO vs. consistency and latency`: pushing `RPO` from minutes toward near-zero usually means append-only event history, synchronous replication for critical writes, or workflow acknowledgment only after durable persistence. That improves recovery but adds write latency, increases storage/replication spend, and can reduce peak throughput during dependency pressure.
- `Fast failover vs. operational complexity`: a tighter `RTO` often requires pre-provisioned standby services, tested failover automation, and dependency health orchestration. That lowers recovery time but increases runbook complexity, change-management burden, and the odds of automation-induced incidents if failover paths are not exercised.
- `Compliance boundaries vs. latency`: residency controls, PII tokenization, policy gateways, and approval checkpoints improve legal defensibility, but they insert extra network hops and synchronous policy decisions into the request path. The common result is higher `p95/p99` latency unless those checks are cached, colocated, or moved off the interactive path.
- `Compliance scope vs. ops burden`: stricter segmentation for `HIPAA` or `PCI` reduces audit risk, but it usually means separate storage domains, more key-management overhead, narrower tool allowlists, and slower developer iteration. Teams often reserve the strictest controls for write actions and sensitive tenants rather than every low-risk read path.
- `Throughput vs. graceful degradation`: aggressive queue shedding and optional-feature disablement protect system availability during rate-limit events, but they deliberately reduce answer richness. This is usually the right trade in enterprise systems because partial, policy-safe output is preferable to broad timeout cascades.

## 4. Distributed Resilience & Security

### Durable execution

Use durable execution when a run can outlive a single process, require human approval, or survive external outages. The cleanest separation is:

- Durable workflow engine for orchestration, replay, timers, and human pauses
- Agent runtime for bounded reasoning, tool selection, and synthesis
- External tools isolated as idempotent activities

A strong pattern is `Temporal workflow -> agent task/activity -> MCP tool calls`. The workflow owns event history and retries. The embedded agent stays short-lived and deterministic from the workflow's perspective. A lighter pattern is `LangGraph checkpointer -> StateGraph super-steps -> pending writes`, which is simpler but less authoritative than a full event-history engine for multi-hour jobs.

Durability checklist for production workflows:

- `workflow replay`: rebuild control state from event history or checkpoints rather than trusting process memory
- `distributed locking`: serialize multi-writer session updates with workflow ownership, row-level DB locks, or lease-based task claims
- `checkpointing`: persist plan state, tool observations, approval pauses, and retry counters at explicit step boundaries
- `dead-letter handling`: move poison messages and permanently failed tool jobs to a review queue with the full `correlation_id`, payload hash, and failure classification

### Failure taxonomy

1. `Transient dependency failure`: timeout, 429, connection reset, short-lived 5xx. Retry with bounded exponential backoff and jitter.
2. `Persistent dependency failure`: sustained outage, bad credentials, revoked permission. Open circuit, alert, and route to fallback.
3. `Semantic failure`: invalid tool arguments, schema mismatch, hallucinated parameters. Fail fast, surface a model-visible validation error, retry only after correction.
4. `Poison-pill input`: a request that deterministically triggers runaway loops, memory blowup, or policy violations. Quarantine by input signature and require operator review.
5. `Replay divergence`: tool side effect already happened, but state did not advance. Prevent with idempotency keys and persisted observations.
6. `Human stall`: approval or external review never returns. Use explicit SLA timers, escalation, and compensating actions.

### Retry, circuit breaker, and fallback policies

- Retries apply only to transient failures and must honor the remaining deadline budget.
- Circuit breaker states:
  - `closed`: normal traffic, failures counted against threshold
  - `open`: fail fast, skip costly calls, immediately try fallback path
  - `half-open`: allow a small number of probe requests to test recovery
- Fallback chain:
  - `primary model` for full-quality answers
  - `secondary model` for degraded but useful answers
  - `deterministic fallback` for safe summary, cached answer, or "action queued" response

Graceful degradation should preserve correctness before completeness. It is better to return a limited but trustworthy answer than a partially failed action masked as success.

### Zero-Trust MCP and enterprise security

Zero-Trust MCP architecture should treat every tool boundary as hostile until proven otherwise:

- Tool servers get short-lived credentials scoped to a single resource audience
- Tool calls pass through an authorization-aware proxy that enforces tenant, role, and purpose
- High-risk tools require human approval or policy attestation before execution
- Tool schemas are strict, versioned, and validated before the request reaches the downstream system

PII protection pipeline:

```text
detect -> classify -> redact/tokenize -> policy check -> model/tool call -> audit record
```

Each decision envelope should capture:

- `correlation_id`
- `tenant_id`
- `actor`
- `tool_name`
- `input_hash`
- `policy_decision`
- `output_classification`

Audit trails should land in append-only storage so operators can reconstruct chain-of-custody for agent decisions, tool invocations, approval events, and fallback transitions.

> ⚠️ Gap: Public framework docs are strong on approvals, tracing, and tool schemas, but much thinner on first-party PII classifiers, isolation guarantees, and turnkey redaction pipelines. Those controls usually need to be implemented at the platform layer, not delegated to the agent framework.

## 5. Production Enterprise Code

The snippet below is runnable Python that demonstrates bounded retries, jitter, circuit breakers, fallback model chains, structured logging with correlation IDs, and graceful degradation with a deterministic fallback path.

```python
from __future__ import annotations

import json
import logging
import random
import sys
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": int(time.time() * 1000),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for key in ("correlation_id", "provider", "event", "state", "attempt", "degraded"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, separators=(",", ":"))


def build_logger() -> logging.Logger:
    logger = logging.getLogger("agent_runtime")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


LOGGER = build_logger()


def log(level: int, message: str, **fields: object) -> None:
    LOGGER.log(level, message, extra=fields)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_delay_s: float = 0.25
    max_delay_s: float = 2.0
    jitter_ratio: float = 0.25


@dataclass(frozen=True)
class CircuitBreakerConfig:
    failure_threshold: int = 3
    open_interval_s: float = 5.0
    half_open_max_calls: int = 1


@dataclass(frozen=True)
class AgentResponse:
    text: str
    provider: str
    degraded: bool = False


class CircuitBreaker:
    def __init__(self, config: CircuitBreakerConfig) -> None:
        self.config = config
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.opened_at = 0.0
        self.half_open_calls = 0

    def before_call(self) -> None:
        now = time.monotonic()
        if self.state == CircuitState.OPEN:
            if now - self.opened_at >= self.config.open_interval_s:
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
            else:
                raise TransientError("circuit open")
        if self.state == CircuitState.HALF_OPEN:
            if self.half_open_calls >= self.config.half_open_max_calls:
                raise TransientError("half-open probe budget exhausted")
            self.half_open_calls += 1

    def record_success(self) -> None:
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.half_open_calls = 0

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= self.config.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = time.monotonic()
            self.half_open_calls = 0


class ModelEndpoint:
    def __init__(
        self,
        name: str,
        invoke_fn: Callable[[str], str],
        breaker: CircuitBreaker,
    ) -> None:
        self.name = name
        self.invoke_fn = invoke_fn
        self.breaker = breaker

    def invoke(self, prompt: str, correlation_id: str) -> AgentResponse:
        self.breaker.before_call()
        try:
            text = self.invoke_fn(prompt)
            self.breaker.record_success()
            log(
                logging.INFO,
                "provider call succeeded",
                correlation_id=correlation_id,
                provider=self.name,
                event="provider_success",
                state=self.breaker.state.value,
            )
            return AgentResponse(text=text, provider=self.name, degraded=False)
        except PermanentError:
            self.breaker.record_failure()
            raise
        except Exception as exc:
            self.breaker.record_failure()
            raise TransientError(str(exc)) from exc


def retry_call(
    operation: Callable[[], AgentResponse],
    *,
    policy: RetryPolicy,
    correlation_id: str,
    provider: str,
) -> AgentResponse:
    delay_s = policy.initial_delay_s
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return operation()
        except PermanentError:
            log(
                logging.ERROR,
                "permanent failure",
                correlation_id=correlation_id,
                provider=provider,
                event="permanent_failure",
                attempt=attempt,
            )
            raise
        except TransientError as exc:
            log(
                logging.WARNING,
                f"transient failure: {exc}",
                correlation_id=correlation_id,
                provider=provider,
                event="transient_failure",
                attempt=attempt,
            )
            if attempt == policy.max_attempts:
                raise
            jitter = 1.0 + random.uniform(-policy.jitter_ratio, policy.jitter_ratio)
            time.sleep(min(delay_s, policy.max_delay_s) * jitter)
            delay_s = min(delay_s * 2.0, policy.max_delay_s)
    raise RuntimeError("unreachable")


def deterministic_fallback(prompt: str) -> AgentResponse:
    safe_excerpt = prompt[:120].replace("\n", " ")
    text = (
        "The primary providers are temporarily unavailable. "
        f"Your request has been accepted and partially summarized: {safe_excerpt}"
    )
    return AgentResponse(text=text, provider="deterministic_fallback", degraded=True)


def invoke_with_fallback(
    prompt: str,
    endpoints: list[ModelEndpoint],
    *,
    correlation_id: str,
    retry_policy: RetryPolicy,
) -> AgentResponse:
    for endpoint in endpoints:
        try:
            return retry_call(
                lambda: endpoint.invoke(prompt, correlation_id),
                policy=retry_policy,
                correlation_id=correlation_id,
                provider=endpoint.name,
            )
        except (TransientError, PermanentError):
            log(
                logging.WARNING,
                "provider failed, trying next fallback",
                correlation_id=correlation_id,
                provider=endpoint.name,
                event="fallback_transition",
                state=endpoint.breaker.state.value,
            )
            continue

    response = deterministic_fallback(prompt)
    log(
        logging.ERROR,
        "all providers unavailable, degraded response returned",
        correlation_id=correlation_id,
        provider=response.provider,
        event="graceful_degradation",
        degraded=response.degraded,
    )
    return response


def flaky_primary(prompt: str) -> str:
    # Simulate a provider that often times out under pressure.
    if random.random() < 0.8:
        raise TimeoutError("primary timed out")
    return f"primary answer for: {prompt}"


def stable_secondary(prompt: str) -> str:
    return f"secondary answer for: {prompt}"


def main() -> None:
    random.seed(7)
    correlation_id = str(uuid.uuid4())

    primary = ModelEndpoint(
        name="gpt-primary",
        invoke_fn=flaky_primary,
        breaker=CircuitBreaker(CircuitBreakerConfig()),
    )
    secondary = ModelEndpoint(
        name="claude-secondary",
        invoke_fn=stable_secondary,
        breaker=CircuitBreaker(CircuitBreakerConfig(failure_threshold=2)),
    )

    response = invoke_with_fallback(
        "Summarize the current order status and next approved action.",
        [primary, secondary],
        correlation_id=correlation_id,
        retry_policy=RetryPolicy(),
    )

    print(
        json.dumps(
            {
                "correlation_id": correlation_id,
                "provider": response.provider,
                "degraded": response.degraded,
                "text": response.text,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
```

Production takeaways from the code:

- Retries are bounded and apply only to transient failures.
- Breakers fail fast when a dependency is already unhealthy.
- Fallback order is explicit and audit-friendly.
- Correlation IDs are carried through every log event.
- Graceful degradation returns a safe partial answer instead of surfacing an opaque outage.

## 6. Architectural System Design Scenarios

### Scenario 1: Multi-tenant support copilot for a SaaS platform

**Problem statement**: Design a multi-tenant support copilot that serves `40,000` requests/minute during peak weekday load, keeps tool-using requests under `p95 8s`, enforces tenant isolation, and allows approved write actions into CRM and ticketing systems.

**Proposed architecture**:

```text
┌────────────┐    ┌──────────────┐    ┌─────────────────────┐
│ Web / API  │ -> │ Tenant Auth  │ -> │ Planner / Router    │
└────────────┘    └──────────────┘    └─────────┬───────────┘
                                                │
                           ┌────────────────────┼────────────────────┐
                           v                    v                    v
                  ┌────────────────┐   ┌────────────────┐   ┌────────────────┐
                  │ Fast Executor  │   │ Approval Gate  │   │ Cache / Memory │
                  │ cheap model    │   │ for write ops  │   │ tenant-scoped  │
                  └───────┬────────┘   └────────────────┘   └────────────────┘
                          │
                          v
                ┌──────────────────────┐
                │ MCP Tool Gateway     │
                │ schema + RBAC + OTel │
                └───────┬───────┬──────┘
                        │       │
                        v       v
                   ┌────────┐ ┌────────────┐
                   │ Search │ │ CRM/Ticket │
                   └────────┘ └────────────┘
```

**Trade-off evaluation matrix**:

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Pure ReAct with one strong model | High | Weak under serial tool use | Low | Medium; harder to isolate risky steps | Medium |
| Planner + cheap executor + approval gates | Medium | Strong | Medium | Strong; policy can wrap executor/tool boundaries | High |
| Fully deterministic workflow with no agent planning | Low to medium | Strong for fixed tasks | Medium | Strong | Medium; less adaptable to novel cases |

**Decision rationale**: Choose `planner + cheap executor + approval gates`. It preserves flexible reasoning for messy support questions, keeps repeated execution on a low-cost tier, and provides clean insertion points for tenant-aware policy enforcement before write actions. It also reflects the NFR trade-off profile of this use case: `99.9%` availability and strong tenant isolation matter, but near-zero `RPO` or active-active financial-grade recovery would add cost and latency that the support path usually does not justify. Pure ReAct spends too much on repeated strong-model turns, while a fully deterministic workflow is too rigid for ambiguous support conversations.

### Scenario 2: Durable claims-processing agent for an insurer

**Problem statement**: Design a claims-processing workflow that handles `100,000` claims/day, pauses for human approval or document arrival, survives multi-hour outages, keeps `RPO` near zero for financial actions, and produces regulator-friendly audit trails.

**Proposed architecture**:

```text
┌──────────────┐    ┌─────────────────┐    ┌────────────────────┐
│ Intake Queue │ -> │ Temporal        │ -> │ Agent Activity     │
│ docs/events  │    │ Workflow        │    │ plan/summarize     │
└──────────────┘    └───────┬─────────┘    └─────────┬──────────┘
                            │                        │
                            │                        v
                            │              ┌────────────────────┐
                            │              │ MCP Tool Gateway   │
                            │              │ policy + redaction │
                            │              └───────┬────────────┘
                            │                      │
         ┌──────────────────┼──────────────┐       v
         v                  v              v   ┌──────────────┐
┌──────────────┐   ┌──────────────┐  ┌──────────────┐
│ Event History│   │ Approval UI  │  │ Claims/ERP DB│
│ replay/RPO   │   │ human tasks  │  │ payments     │
└──────────────┘   └──────────────┘  └──────────────┘
                            │
                            v
                     ┌──────────────┐
                     │ Audit / SIEM │
                     └──────────────┘
```

**Trade-off evaluation matrix**:

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Long-lived ReAct session with DB checkpoints | Medium | Variable | Medium | Medium | Medium |
| Temporal workflow with embedded agent activities | Medium to high | Best for durable jobs, not instant UX | High | Very strong; event history plus approvals | High |
| Batch DAG planner without durable workflow engine | Low to medium | Strong for parallel analysis | Medium | Medium | High for compute, weaker for pause/resume |

**Decision rationale**: Choose `Temporal workflow with embedded agent activities`. Financial workflows need replay-safe execution, explicit timers, approval pauses, and near-zero-loss audit history more than they need conversational fluidity. This is an intentional NFR trade: higher ops complexity and slightly higher steady-state cost are acceptable because stronger `RPO/RTO`, auditability, and compliance controls outweigh raw latency for claims operations. A long-lived ReAct session can be made to work, but it is weaker on deterministic recovery and compliance evidence. A pure DAG planner is fast for parallel analysis but is the wrong primitive for human-in-the-loop claims operations that may pause for hours.
