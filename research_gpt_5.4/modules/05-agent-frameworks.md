# Agent Frameworks — LangGraph, OpenAI Agents SDK, Google ADK, CrewAI

## 1. System Topology & Data Flow

These four frameworks solve the same business problem, but they place orchestration responsibility in different layers:

- `LangGraph` is graph-runtime first: state transitions, reducers, checkpoints, and super-step execution are the center of gravity.
- `OpenAI Agents SDK` is runner-loop first: the core abstraction is a turn-based agent run that may call tools, hand off to another agent, or pause for approval.
- `Google ADK` is workflow-platform first: sessions, memory, artifacts, compaction, and multiple workflow families are treated as one operational system.
- `CrewAI` is flow-application first: evented `Flow` state drives the app, while `Crew`s perform focused units of work.

```text
┌────────────────────────────── Control Plane ──────────────────────────────┐
│  API Gateway -> AuthZ/RBAC -> Policy Router -> Orchestrator Runtime      │
│       │             │                │                 │                  │
│       │             │                │                 ├─ LangGraph DAG   │
│       │             │                │                 ├─ OpenAI Runner   │
│       │             │                │                 ├─ ADK Workflow    │
│       │             │                │                 └─ CrewAI Flow     │
│       │             │                │                                    │
│       └────────────> Correlation ID / Tenant Context / Deadline Budget    │
└────────────────────────────────────────────────────────────────────────────┘
                                │
                                v
┌────────────────────────────── Data Plane ─────────────────────────────────┐
│  Model Providers  <->  Tool Proxy / MCP Gateway  <->  Enterprise Systems │
│  planner/executor        schema validation            CRM / ERP / RAG     │
│  primary/fallback        approval hooks               search / queue / DB  │
└────────────────────────────────────────────────────────────────────────────┘
          │                            │                             │
          v                            v                             v
┌──────────────────┐       ┌────────────────────┐       ┌──────────────────┐
│ Persistence      │       │ Durable Workflow   │       │ Telemetry Sinks  │
│ checkpoints      │       │ Temporal / Kafka   │       │ logs / traces    │
│ sessions         │       │ retries / DLQ      │       │ metrics / audits │
│ memory/artifacts │       │ outbox / locks     │       │ SIEM / alerting  │
└──────────────────┘       └────────────────────┘       └──────────────────┘
```

### Request-flow narrative

1. The `API Gateway` authenticates the tenant, assigns a `correlation_id`, and starts an end-to-end deadline budget.
2. `Policy Router` classifies the request as interactive, approval-gated, or durable async work.
3. The chosen runtime starts execution:
   - `LangGraph`: activates graph nodes for the current super-step.
   - `OpenAI Agents SDK`: enters the runner loop and inspects each model output.
   - `Google ADK`: runs a graph, dynamic workflow, collaborative agent set, or deterministic template.
   - `CrewAI`: starts a `Flow`, triggers `@start()` nodes, and routes events through listeners.
4. The runtime calls model providers and tool proxies. Tool proxies enforce schema validation, least-privilege credentials, and approval policies before touching downstream systems.
5. State changes land in framework-native persistence:
   - `LangGraph`: checkpoint store keyed by `thread_id`, with pending writes across siblings.
   - `OpenAI Agents SDK`: session storage plus serialized `RunState` on approval pauses.
   - `Google ADK`: session/state services, optional memory service, and artifact storage for large payloads.
   - `CrewAI`: persisted flow state and lineage for resume or fork.
6. Telemetry is emitted on every transition: structured logs, traces, cost counters, tool outcomes, approval decisions, and policy denials.
7. If the run exceeds its budget or hits a protected action, control returns to the policy plane for retry, fallback, approval, or graceful degradation.

The main architectural distinction is where determinism lives. `LangGraph` externalizes it into explicit graph structure and reducers. `OpenAI Agents SDK` keeps a lightweight control loop and expects durable workflow concerns to be added around it for long-running jobs. `Google ADK` exposes the largest first-party runtime surface for state and compaction. `CrewAI` optimizes for rapid composition of evented automation with typed flow state and remote-agent/tool integration.

## 2. Core Mechanics & Algorithms

### Execution models

#### `LangGraph`

`LangGraph` uses a Pregel-style super-step model. Nodes that receive messages in the same step can execute in parallel, reducers merge state updates, and checkpoints are taken at super-step boundaries. The scheduler overhead is roughly `O(|V| + |E|)` per graph activation, but wall-clock latency is driven by the graph critical path rather than the total number of nodes.

Approximate makespan:

```text
graph_latency
  ~= Σ(super_step_coordination_overhead)
   + Σ(serial critical-path node latencies)
   + checkpoint_write_overhead
```

Key invariant: a resumed node restarts from the beginning of its function, so every external side effect must be idempotent or isolated behind a durable activity boundary.

#### `OpenAI Agents SDK`

The SDK is a turn-driven loop:

```text
RECEIVE_INPUT
  -> CALL_MODEL
  -> INSPECT_OUTPUT
     -> FINAL_OUTPUT
     -> TOOL_CALL -> CALL_MODEL
     -> HANDOFF   -> CALL_MODEL
     -> APPROVAL_PAUSE -> RESUME
     -> MAX_TURNS_EXCEEDED
```

This is operationally simple, but serial latency is the main trade-off:

```text
runner_latency ~= O(turns * (L_model + L_tool + L_transport))
```

Key invariant: every run must have explicit `max_turns`, deadline budget, and tool budget, because the orchestration loop is naturally open-ended.

#### `Google ADK`

ADK supports deterministic workflows (`SequentialAgent`, `ParallelAgent`, `LoopAgent`), graph-based workflows, dynamic orchestration, and collaborative multi-agent compositions. Deterministic template workflows converge because the scheduler does not ask the model to decide orchestration order.

Approximate behavior:

```text
workflow_latency
  ~= critical_path(agent/tool steps)
   + compaction_overhead
   + session_store_overhead
```

Key invariant: state domains are separated into `Session`, `State`, and `Memory`, reducing ambiguity about what should be replayed, compacted, or queried semantically.

#### `CrewAI`

CrewAI `Flow`s are event-driven state machines. `@start`, `@listen`, and `@router` define transitions, and multiple start conditions may run in parallel when satisfied.

Approximate behavior:

```text
flow_latency
  ~= event_dispatch_overhead
   + critical_path(LLM/tool tasks)
   + persistence_overhead
```

Key invariant: termination conditions are an application responsibility. The framework permits self-loops and revision loops, so bounded retries and escalation rules must be explicit.

### Comparative state machine

Across all four, a production-safe execution loop looks like this:

```text
ACCEPT
  -> CLASSIFY
  -> EXECUTE
  -> OBSERVE
  -> EVALUATE
     -> COMPLETE
     -> RETRY_TRANSIENT
     -> FALLBACK_PROVIDER
     -> HUMAN_APPROVAL
     -> DEAD_LETTER
```

### Complexity and convergence

- `LangGraph`: best when work can be exposed as a DAG or bounded state machine; convergence depends on no unbounded cycles and stable reducers.
- `OpenAI Agents SDK`: simplest mental model, but cost and latency grow linearly with turns unless history is pruned or continuation is compacted server-side.
- `Google ADK`: strongest documented context-management story because compaction and artifact offload reduce replay growth.
- `CrewAI`: high developer throughput for workflow apps, but correctness depends heavily on disciplined flow-state design and explicit exit conditions.

### Production invariants

- Every run needs a stable `run_id`, `tenant_id`, and `correlation_id`.
- Every side-effecting tool call needs an idempotency key such as `hash(run_id, step_index, tool_name, semantic_payload)`.
- Every framework needs bounded recursion or loop control:
  - `LangGraph`: `recursion_limit`
  - `OpenAI Agents SDK`: `max_turns`
  - `Google ADK`: `LoopAgent.maxIterations`
  - `CrewAI`: flow-defined exit rules
- Every persisted resume point must represent a semantically valid state, not just a raw transcript fragment.

## 3. Token Economics & NFR Analysis

> ⚠️ Gap: The public docs for these frameworks do not publish a stable, official apples-to-apples benchmark suite for identical multi-step workloads. The latency targets below are recommended internal SLOs and budgeting heuristics, not framework guarantees.

### Cost formulas

Assumptions for the worked formulas below:

- `runs = 1000`
- Interactive enterprise copilot workload
- `planner_turns = 1`
- `executor_turns = 3`
- `fresh_input_tokens_per_turn = 450`
- `cached_prefix_tokens_per_turn = 1600`
- `output_tokens_per_turn = 220`
- `tool_calls_per_run = 2`
- `primary_model_input_price = $3.00 / 1M`
- `primary_model_output_price = $12.00 / 1M`
- `executor_model_input_price = $0.50 / 1M`
- `executor_model_output_price = $2.00 / 1M`
- `cache_write_price = $0.625 / 1M`
- `cache_read_price = $0.050 / 1M`
- External tool/API surcharge is accounted separately as `tool_cost_per_run`

Generic orchestration formula:

```text
$ per 1k runs =
1000 * (
  uncached_input_tokens / 1_000_000 * P_in +
  cache_write_tokens    / 1_000_000 * P_cache_write +
  cache_read_tokens     / 1_000_000 * P_cache_read +
  output_tokens         / 1_000_000 * P_out +
  tool_cost_per_run
)
```

#### `OpenAI Agents SDK` style turn-runner

If each run has one high-capability planning turn and three cheaper execution turns:

```text
$ per 1k runs =
1000 * (
  (2250 / 1_000_000) * 3.00 +          # planner input
  (220  / 1_000_000) * 12.00 +         # planner output
  (1350 / 1_000_000) * 0.50 +          # executor fresh input
  (1600 / 1_000_000) * 0.625 +         # cache write
  (3200 / 1_000_000) * 0.050 +         # cache read for later turns
  (660  / 1_000_000) * 2.00            # executor output
)
= $11.57 per 1k runs + (1000 * tool_cost_per_run)
```

The main cost risk in this model is transcript replay. Sessions are convenient, but if history is not compacted or server-managed continuation is not used, `uncached_input_tokens` grows with every turn.

#### `LangGraph` style planner plus parallel workers

LangGraph does not change token prices by itself, but it can reduce redundant turns when the graph structure is explicit and cacheable nodes repeat. Let `h` be repeatable-node cache hit rate.

```text
$ per 1k runs =
1000 * (
  planner_input / 1_000_000 * P_plan_in +
  planner_output / 1_000_000 * P_plan_out +
  fresh_worker_input / 1_000_000 * P_exec_in +
  ((1 - h) * repeatable_worker_input) / 1_000_000 * P_exec_in +
  cached_worker_reads / 1_000_000 * P_cache_read +
  worker_output / 1_000_000 * P_exec_out
)
```

Illustrative example with `h = 0.40`, `repeatable_worker_input = 2400`, `fresh_worker_input = 1200`, and `worker_output = 600`:

```text
$ per 1k runs
~= 1000 * (
  (2250 / 1_000_000) * 3.00 +
  (220  / 1_000_000) * 12.00 +
  (1200 / 1_000_000) * 0.50 +
  (1440 / 1_000_000) * 0.50 +
  (960  / 1_000_000) * 0.050 +
  (600  / 1_000_000) * 2.00
)
= $11.32 per 1k runs + tool costs
```

The economic win is not cheaper inference pricing; it is fewer repeated reasoning turns and better reuse of cacheable branches.

#### `Google ADK` style compaction-aware workflow

ADK's compaction and artifact offload effectively reduce replay. Let `c` be the compaction retention ratio and `a` the artifact offload ratio.

```text
$ per 1k runs =
1000 * (
  (fresh_input + (replayed_history * c) - (artifact_tokens * a)) / 1_000_000 * P_in +
  output_tokens / 1_000_000 * P_out
)
```

If compaction keeps only `35%` of prior replay and artifact offload removes `20%` of large attachment tokens from prompt context, end-to-end cost drops without changing the model tier. That is especially important for long-lived workflows where replay growth dominates the raw prompt.

#### `CrewAI` style flow accounting

CrewAI exposes whole-flow usage metrics, so the correct cost model is end-to-end rather than per-agent:

```text
$ per 1k runs =
1000 * (
  flow_prompt_tokens / 1_000_000 * P_in +
  flow_completion_tokens / 1_000_000 * P_out +
  flow_cached_prompt_tokens / 1_000_000 * P_cache_read +
  flow_cache_creation_tokens / 1_000_000 * P_cache_write +
  tool_cost_per_run
)
```

The operational warning is that role scaffolding and nested crew/task exchanges can inflate prompt volume even when business logic looks simple.

### Latency targets

For a user-facing enterprise copilot:

- `p50 <= 1.5s` for answer-only requests; `<= 3.0s` when tool calls are required
- `p95 <= 4.0s` for answer-only requests; `<= 8.0s` for tool-using requests
- `p99 <= 8.0s` for answer-only requests; `<= 15.0s` for tool-using requests

Mitigation by percentile:

- `p50`: warm HTTP pools, prompt prefix caching, executor model tiers, streaming first token in under `600ms`
- `p95`: parallel graph branches, ADK compaction thresholds, bounded tool deadlines, session trimming, smaller crew/task fan-out
- `p99`: circuit breakers, deadline propagation, fallback model chains, approval queue isolation, removal of optional enrichments, read-only degradation mode

### Throughput and back-pressure

Capacity must be bounded by both model and tool infrastructure:

```text
runs_per_second ~= min(
  provider_rpm / (60 * avg_model_turns_per_run),
  provider_tpm / (60 * avg_total_tokens_per_run),
  downstream_tool_qps / avg_tool_calls_per_run
)
```

Practical back-pressure policy:

- Below `70%` utilization: accept normally
- `70%` to `85%`: reduce retrieval fan-out, lower planner depth, batch non-urgent background jobs
- `85%` to `95%`: queue low-priority work, disable optional enrichment tools, cap per-tenant concurrency
- Above `95%` sustained for `30s`: shed non-critical traffic, return cached or deterministic fallbacks, and switch sensitive write tools to approval-only mode

Framework-specific throughput notes:

- `LangGraph`: can shrink critical-path latency with parallel nodes, but persistence throughput must keep up with graph execution.
- `OpenAI Agents SDK`: websocket mode processes one response at a time per connection, so horizontal connection management matters under concurrency.
- `Google ADK`: parallel agents and row-level session locking help concurrency, but database lock contention becomes a real design constraint at scale.
- `CrewAI`: parallel `@start()` methods improve concurrency, but unbounded flow fan-out can create queue pressure if downstream APIs are slower than the flow engine.

### Availability, RPO, RTO, and compliance

Recommended targets:

- Availability: `99.9%` for synchronous agent APIs; `99.95%` for durable async control planes
- `RPO <= 5 minutes` for session/checkpoint data on replicated storage; near-zero for write-ahead workflow histories persisted before acknowledgment
- `RTO <= 30 minutes` for regional failover of session stores, checkpoint DBs, and approval services

Compliance discussion:

- `SOC 2` and `ISO 27001`: require immutable audit logs, role separation, secret rotation, and controlled production access.
- `GDPR` and regional residency: require clear data retention windows, transcript deletion paths, and location-aware state stores.
- `HIPAA` or other regulated PII workloads: require data minimization, tool-level masking, approval gates on outbound actions, and trace redaction.

> ⚠️ Gap: Public framework docs are much stronger on state, approvals, and tracing than on built-in compliance controls such as immutable audit-log schemas, sandbox isolation guarantees, or deep PII-redaction pipelines. Those controls usually live in the surrounding platform, not the framework core.

## 4. Distributed Resilience & Security

### Durable execution patterns

- `LangGraph`: use a durable checkpointer such as PostgreSQL for graph state; push non-idempotent external side effects into separately tracked tool boundaries; for multi-hour workflows, pair the graph with Temporal, Kafka consumers, or an outbox processor.
- `OpenAI Agents SDK`: persist sessions and serialize `RunState` whenever approval pauses or operator handoff is possible; for durable jobs, wrap the SDK inside Temporal, Dapr, Restate, or DBOS because the runner itself is not the full workflow engine.
- `Google ADK`: lean on `DatabaseSessionService` for concurrency control, keep artifacts outside the prompt transcript, and use Pub/Sub or Kafka for long-running external work that resumes the session after completion.
- `CrewAI`: persist flow state aggressively, treat remote tools and A2A steps as replayable activities, and isolate irreversible actions behind outbox records because low-level exactly-once guarantees are not strongly documented.

### Failure taxonomy

#### Transient failures

- Provider `429` rate limits
- Network resets and transport timeouts
- Temporary lock contention on session/checkpoint rows
- Short-lived MCP or downstream API outages

Mitigation: bounded retries with exponential backoff and jitter, deadline propagation, and circuit-breaker protection.

#### Permanent failures

- Schema validation errors
- RBAC denials
- Unsupported tool arguments
- Non-recoverable business rule violations
- Policy rejections such as "human approval required"

Mitigation: fail fast, emit structured audit records, and do not retry automatically.

#### Replay hazards

- Duplicate ticket creation after node replay
- Double-charge or double-email after retry
- Divergent state when session history replays but external systems already committed

Mitigation: idempotency keys, outbox pattern, semantic dedupe keys, and side-effect checkpoints.

#### Poison-pill work items

- Malformed tasks that always exceed context
- Flows that recurse without meeting an exit condition
- Inputs that always trigger unsafe tool parameters

Mitigation: dead-letter queues, operator review lanes, max-attempt counters, and quarantine tags.

### Retries, circuit breakers, and fallback chains

Recommended retry policy:

- Retry only transient failures
- Exponential backoff: `base_delay * 2^attempt + jitter`
- Max attempts: `3` to `5` for interactive paths, `6` to `8` for durable async jobs
- Carry `correlation_id` and `idempotency_key` across every retry

Circuit-breaker states:

- `closed`: normal traffic, error rate below threshold
- `open`: fail fast for a cool-down period after repeated transient failures
- `half-open`: allow a limited probe volume to determine recovery

Fallback chain:

```text
primary model/provider
  -> cheaper or alternate region/model
  -> deterministic rules/template response
  -> cached last-known-good answer or human escalation
```

Graceful degradation policy:

- Drop optional retrieval enrichments before core answer generation
- Disable write tools before disabling read tools
- Switch to async acknowledgment for long-running jobs before rejecting work entirely
- Prefer "read-only plus human follow-up" over silent failure for regulated actions

### Zero-Trust MCP and enterprise security

Zero-Trust MCP/tool architecture should enforce:

- mTLS or equivalent trusted transport between runtime and tool gateway
- short-lived tool credentials minted per run
- allow-listed tool schemas and argument validation
- tenant-aware RBAC checks before every side effect
- approval policies for shell, patch, finance, HR, CRM, or data-export tools

PII protection pipeline:

```text
detect -> classify -> redact/tokenize -> route -> audit -> retention policy
```

Minimum controls:

- Structured detection for SSNs, PANs, MRNs, and secrets
- Redaction before tracing or analytics export
- Immutable audit trail with actor, tool, payload hash, policy decision, and timestamp
- Chain-of-custody linking `correlation_id`, `run_id`, approval record, and downstream change record

> ⚠️ Gap: The source note is strongest on approvals, checkpointing, sessions, compaction, and protocol integration. It is thinner on first-party evidence for built-in sandbox isolation, immutable audit schemas, and exactly-once tool execution guarantees, especially for `CrewAI` and OSS `LangGraph`.

## 5. Production Enterprise Code

The snippets below are framework-agnostic control-plane components. In production, they wrap `LangGraph`, `OpenAI Agents SDK`, `Google ADK`, or `CrewAI` rather than replacing them.

### Resilient model invocation shell

```python
from __future__ import annotations

import json
import logging
import random
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Protocol


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class CircuitOpenError(Exception):
    pass


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "timestamp": int(record.created * 1000),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, sort_keys=True)


def build_logger() -> logging.Logger:
    logger = logging.getLogger("agent-runtime")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger


LOGGER = build_logger()


@dataclass(frozen=True)
class RequestContext:
    tenant_id: str
    run_id: str
    correlation_id: str
    deadline_epoch_ms: int

    @staticmethod
    def create(tenant_id: str, timeout_ms: int) -> "RequestContext":
        now_ms = int(time.time() * 1000)
        return RequestContext(
            tenant_id=tenant_id,
            run_id=str(uuid.uuid4()),
            correlation_id=str(uuid.uuid4()),
            deadline_epoch_ms=now_ms + timeout_ms,
        )


class ModelBackend(Protocol):
    name: str

    def invoke(self, prompt: str, ctx: RequestContext) -> str:
        ...


@dataclass(frozen=True)
class StaticBackend:
    name: str
    response: str
    mode: str = "ok"

    def invoke(self, prompt: str, ctx: RequestContext) -> str:
        if self.mode == "transient":
            raise TransientError(f"{self.name} temporary outage")
        if self.mode == "permanent":
            raise PermanentError(f"{self.name} rejected request")
        return f"{self.response} [tenant={ctx.tenant_id}]"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    reset_timeout_s: float = 20.0
    half_open_max_probes: int = 1
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float | None = None
    half_open_inflight: int = 0

    def before_call(self) -> None:
        now = time.time()
        if self.state == CircuitState.OPEN:
            if self.opened_at is None or now - self.opened_at < self.reset_timeout_s:
                raise CircuitOpenError("circuit is open")
            self.state = CircuitState.HALF_OPEN
            self.half_open_inflight = 0
        if self.state == CircuitState.HALF_OPEN:
            if self.half_open_inflight >= self.half_open_max_probes:
                raise CircuitOpenError("half-open probe budget exhausted")
            self.half_open_inflight += 1

    def record_success(self) -> None:
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.opened_at = None
        self.half_open_inflight = 0

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.half_open_inflight = 0
        if self.consecutive_failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = time.time()


def log_event(message: str, ctx: RequestContext, **fields: object) -> None:
    LOGGER.info(
        message,
        extra={
            "extra_fields": {
                "tenant_id": ctx.tenant_id,
                "run_id": ctx.run_id,
                "correlation_id": ctx.correlation_id,
                **fields,
            }
        },
    )


def retry_invoke(
    backend: ModelBackend,
    prompt: str,
    ctx: RequestContext,
    breaker: CircuitBreaker,
    max_attempts: int = 4,
    base_delay_s: float = 0.25,
) -> str:
    for attempt in range(1, max_attempts + 1):
        if int(time.time() * 1000) >= ctx.deadline_epoch_ms:
            raise TransientError("deadline exceeded before invoke")
        breaker.before_call()
        try:
            result = backend.invoke(prompt, ctx)
            breaker.record_success()
            log_event(
                "backend_invoke_success",
                ctx,
                backend=backend.name,
                attempt=attempt,
                circuit_state=breaker.state.value,
            )
            return result
        except PermanentError:
            breaker.record_success()
            raise
        except (TransientError, CircuitOpenError) as exc:
            breaker.record_failure()
            log_event(
                "backend_invoke_retry",
                ctx,
                backend=backend.name,
                attempt=attempt,
                error=str(exc),
                circuit_state=breaker.state.value,
            )
            if attempt == max_attempts:
                raise
            sleep_s = base_delay_s * (2 ** (attempt - 1)) + random.uniform(0.0, 0.15)
            time.sleep(sleep_s)
    raise TransientError("exhausted retries")


def deterministic_fallback(prompt: str) -> str:
    normalized = " ".join(prompt.strip().split())
    return (
        "Service is temporarily degraded. "
        f"Received request summary: {normalized[:180]}. "
        "A human-review workflow has been queued."
    )


def invoke_with_fallback_chain(
    prompt: str,
    ctx: RequestContext,
    backends: Iterable[tuple[ModelBackend, CircuitBreaker]],
    cached_answer_lookup: Callable[[str], str | None],
) -> str:
    for backend, breaker in backends:
        try:
            return retry_invoke(backend=backend, prompt=prompt, ctx=ctx, breaker=breaker)
        except PermanentError as exc:
            log_event("backend_permanent_failure", ctx, backend=backend.name, error=str(exc))
            raise
        except (TransientError, CircuitOpenError) as exc:
            log_event("backend_failed_over", ctx, backend=backend.name, error=str(exc))

    cached = cached_answer_lookup(prompt)
    if cached:
        log_event("graceful_degradation_cached_answer", ctx, mode="cached_answer")
        return cached

    log_event("graceful_degradation_deterministic_fallback", ctx, mode="template_response")
    return deterministic_fallback(prompt)


def demo() -> str:
    ctx = RequestContext.create(tenant_id="acme", timeout_ms=2_000)
    primary = StaticBackend(name="primary", response="primary answer", mode="transient")
    secondary = StaticBackend(name="secondary", response="secondary answer", mode="ok")
    result = invoke_with_fallback_chain(
        prompt="Summarize the latest support incident.",
        ctx=ctx,
        backends=[
            (primary, CircuitBreaker(failure_threshold=2, reset_timeout_s=5.0)),
            (secondary, CircuitBreaker(failure_threshold=2, reset_timeout_s=5.0)),
        ],
        cached_answer_lookup=lambda _: None,
    )
    return result


if __name__ == "__main__":
    print(demo())
```

### Back-pressure and graceful-degradation policy

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AdmissionMode(str, Enum):
    NORMAL = "normal"
    REDUCE_OPTIONAL = "reduce_optional"
    ASYNC_ONLY = "async_only"
    READ_ONLY = "read_only"
    SHED = "shed"


@dataclass(frozen=True)
class CapacitySnapshot:
    provider_utilization: float
    tool_utilization: float
    approval_queue_depth: int


def select_mode(snapshot: CapacitySnapshot) -> AdmissionMode:
    max_util = max(snapshot.provider_utilization, snapshot.tool_utilization)
    if max_util < 0.70:
        return AdmissionMode.NORMAL
    if max_util < 0.85:
        return AdmissionMode.REDUCE_OPTIONAL
    if max_util < 0.95:
        return AdmissionMode.ASYNC_ONLY
    if snapshot.approval_queue_depth < 50:
        return AdmissionMode.READ_ONLY
    return AdmissionMode.SHED


def execution_profile(mode: AdmissionMode) -> dict[str, object]:
    if mode == AdmissionMode.NORMAL:
        return {"retrieval_fanout": 4, "allow_write_tools": True, "response_mode": "full"}
    if mode == AdmissionMode.REDUCE_OPTIONAL:
        return {"retrieval_fanout": 2, "allow_write_tools": True, "response_mode": "compact"}
    if mode == AdmissionMode.ASYNC_ONLY:
        return {"retrieval_fanout": 1, "allow_write_tools": False, "response_mode": "async_ack"}
    if mode == AdmissionMode.READ_ONLY:
        return {"retrieval_fanout": 1, "allow_write_tools": False, "response_mode": "read_only"}
    return {"retrieval_fanout": 0, "allow_write_tools": False, "response_mode": "retry_later"}
```

These two snippets capture the minimum production shell the framework docs imply but do not fully provide by themselves: bounded retries, circuit breakers, model fallback, correlation-aware logging, and load-shedding before total outage.

## 6. Architectural System Design Scenarios

### Scenario 1: Multi-tenant action copilot for customer operations

**Problem statement**

Design a multi-tenant SaaS copilot that handles `12k` interactive requests per minute, supports tool-assisted case resolution, and requires human approval for sensitive CRM writes. Target `p95 <= 8s` for tool-using requests and a complete audit trail for every outbound action.

**Proposed architecture**

Recommended approach: `OpenAI Agents SDK` for the interactive control loop, wrapped in a durable workflow shell for approvals and long-running actions.

```text
┌──────────────────┐    ┌─────────────────────┐    ┌──────────────────────┐
│ Web / API Clients│ -> │ API Gateway + Auth  │ -> │ Policy / Approval Svc│
└──────────────────┘    └─────────────────────┘    └──────────┬───────────┘
                                                               │
                                                               v
┌────────────────────────────── Control Runtime ────────────────────────────┐
│ OpenAI Agents Runner -> Session Store -> Serialized RunState -> Temporal  │
│        │                    │                    │               │         │
│        └──────────────> MCP / Tool Gateway <────┘               │         │
└────────────────────────────────────────────────────────────────────────────┘
                                │                              │
                                v                              v
                    ┌────────────────────┐          ┌──────────────────────┐
                    │ CRM / Ticket APIs  │          │ Logs / Traces / SIEM │
                    │ Email / KB / Search│          │ Approval Audit Store │
                    └────────────────────┘          └──────────────────────┘
```

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| `OpenAI Agents SDK` + Temporal + MCP gateway | Medium | Low for interactive work; medium for approval-paused flows | Medium | Strong, because approvals, sessions, and MCP controls are first-class | High if connections, session store, and workflow workers scale horizontally |
| `LangGraph` + custom approval plane + Postgres checkpoints | Medium | Low to medium, especially when branches parallelize | High | Medium to strong, but governance must be assembled outside the core runtime | High for explicit workflows; more engineering required for approval UX |
| `CrewAI` Flow + A2A/MCP + persisted lineage | Medium | Medium | Medium | Medium, with good protocol flexibility but thinner low-level durability evidence | Medium to high, depending on downstream queue design |

**Decision rationale**

Choose `OpenAI Agents SDK` when the differentiator is approval-heavy interactive actions rather than graph complexity. The SDK's documented approval surface, session model, and resumable run state reduce time-to-production for user-facing assistants. Temporal absorbs the durable-workflow responsibility that the SDK intentionally leaves to external systems. `LangGraph` is stronger if the product later evolves into a branching case-resolution DAG with many parallel substeps, but it requires more custom governance plumbing up front.

### Scenario 2: Regulated underwriting workflow with long-lived state

**Problem statement**

Design a regulated underwriting workflow that processes `100k` application updates per day, may pause for hours waiting on documents or human review, and must preserve replay-safe state with data-minimized prompts. Target `99.95%` control-plane availability, `RPO <= 5 minutes`, and auditable PII handling.

**Proposed architecture**

Recommended approach: `Google ADK` for session/state/memory separation, compaction, and database-backed session coordination.

```text
┌────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
│ Intake APIs / ETL  │ -> │ Auth / Policy Layer │ -> │ ADK Workflow Runtime│
└────────────────────┘    └─────────────────────┘    └──────────┬──────────┘
                                                                 │
                  ┌──────────────────────────────────────────────┼──────────────────────────┐
                  │                                              │                          │
                  v                                              v                          v
        ┌────────────────────┐                      ┌────────────────────┐      ┌──────────────────┐
        │ Session DB + Locks │                      │ Memory / Artifacts │      │ Pub/Sub or Kafka │
        │ row-level control  │                      │ compaction / blobs │      │ async activities  │
        └────────────────────┘                      └────────────────────┘      └──────────────────┘
                  │                                              │                          │
                  └──────────────────────────────┬───────────────┴──────────────┬──────────┘
                                                 v                              v
                                     ┌────────────────────┐          ┌──────────────────────┐
                                     │ Underwriting Tools │          │ DLP / Audit / SIEM   │
                                     │ document services  │          │ chain of custody      │
                                     └────────────────────┘          └──────────────────────┘
```

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| `Google ADK` + DatabaseSessionService + artifact store + Pub/Sub | Medium | Medium for interactive steps; strong for long-lived workflows | Medium | Strong, because state domains and DB locking are documented clearly | High for session-heavy enterprise workflows with careful DB sizing |
| `LangGraph` + Postgres checkpoints + custom compaction/memory services | Medium | Medium; can be excellent when workflow structure is explicit | High | Medium, because security and memory separation must be built around the runtime | High, but replay safety depends on strong side-effect discipline |
| `CrewAI` persisted Flows + external queues + custom DLP pipeline | Medium | Medium | Medium | Medium, with good application composition but thinner evidence on low-level locking/exactly-once semantics | Medium to high, depending on queue and persistence strategy |

**Decision rationale**

Choose `Google ADK` when the dominant problem is long-lived, stateful workflow control rather than maximum orchestration flexibility. Its explicit `Session` / `State` / `Memory` split, documented row-level locking, context compaction, and artifact offload are directly aligned with regulated workflows where replay cost and state correctness matter more than raw developer convenience. `LangGraph` remains a strong alternative for teams that want a graph-native runtime and are prepared to build governance and compaction services around it, but ADK has the stronger first-party documentation for this specific operating model.
