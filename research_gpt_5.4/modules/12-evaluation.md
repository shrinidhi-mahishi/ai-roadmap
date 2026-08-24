# Evaluation — Task success, trajectory, tool accuracy, quality, cost, latency

## 1. System Topology & Data Flow

`Evaluation` in production agent systems is a parallel control surface, not a single score. The runtime must observe six distinct dimensions at once: `task success`, `trajectory efficiency`, `tool accuracy`, `answer quality`, `cost`, and `latency`. The architecture that matters is the split between `inline controls` that shape execution before damage is done and `post-run evaluators` that score what happened after the run finishes (`research_cursor/research/03-tool-use.md`, `research_cursor/research/04-agent-architecture.md`, `research_cursor/research/05-agent-frameworks.md`, `research_cursor/research/06-rag.md`, `research_cursor/research/08-planning-reasoning.md`, `research_cursor/research/10-mcp-interoperability.md`).

```text
┌────────────────────────────── Control Plane ───────────────────────────────┐
│ User / API -> AuthN/Z -> Policy Router -> Agent Runtime                    │
│      │             │              │                  │                      │
│      │             │              │                  ├─ Task judge          │
│      │             │              │                  ├─ Trajectory judge    │
│      │             │              │                  ├─ Tool validator      │
│      │             │              │                  ├─ Cost / SLA budgeter │
│      │             │              │                  └─ Approval gate       │
│      └────────────────────────────────────────────> correlation_id / budget │
└─────────────────────────────────────────────────────────────────────────────┘
                                 │
                                 v
┌────────────────────────────── Data Plane ──────────────────────────────────┐
│ plan -> reason -> tool_call -> observe -> retry/replan -> synthesize       │
│   │         │           │              │                 │                 │
│   │         │           │              │                 │                 └─ final answer
│   │         │           │              │                 └─ checkpoints
│   │         │           │              └─ tool results / references
│   │         │           └─ schema, args, side-effect intent
│   │         └─ branch depth, loop count, retry count
│   └─ acceptance criteria / target state
└─────────────────────────────────────────────────────────────────────────────┘
          │                         │                          │
          v                         v                          v
┌──────────────────────┐  ┌──────────────────────┐  ┌────────────────────────┐
│ Persistence Layer    │  │ Tool Boundary        │  │ Telemetry / Audit      │
│ workflow history     │  │ MCP gateway          │  │ traces / metrics       │
│ checkpoints          │  │ RBAC / approvals     │  │ token ledger           │
│ tool artifacts       │  │ PII redaction        │  │ immutable eval events  │
│ retrieval evidence   │  │ idempotency keys     │  │ SIEM / alerts          │
└──────────────────────┘  └──────────────────────┘  └────────────────────────┘
```

### Request-flow narrative

1. `AuthN/Z` attaches `tenant_id`, `correlation_id`, deadline, and policy scope before any planning starts.
2. `Policy Router` determines whether the run is allowed to mutate state, which tools can be called, and which evaluation dimensions are mandatory for this request class.
3. `Agent Runtime` executes the normal loop: `plan -> tool_call -> observe -> replan -> synthesize`.
4. During execution, `inline evaluators` intercept risky edges:
   - `tool validator` checks schema validity and business-rule validity
   - `approval gate` pauses high-risk side effects
   - `cost / SLA budgeter` cuts off fan-out when the remaining budget is too small
5. `Persistence Layer` stores checkpoints, tool arguments, tool outputs, retrieval candidates, and final answer lineage so post-run scoring can reconstruct the real path rather than only the final text.
6. `post-run evaluators` compute:
   - `task success`: did the workflow reach an allowed terminal state?
   - `trajectory`: how much branching, retrying, or replanning was needed?
   - `tool accuracy`: were calls structurally valid and semantically correct?
   - `quality`: was the answer grounded, sufficient, and reference-faithful?
   - `cost`: what did tokens, tools, caches, and sandboxes cost?
   - `latency`: what was the critical path and which percentile bucket did it land in?
7. `Telemetry / Audit` emits immutable evaluator decisions and degradation reasons for later audit, tuning, and incident review.

The key design rule is that `evaluation consumes the same first-class runtime artifacts that operations uses to recover the workflow`. If the system stores only the final answer, it cannot reliably measure trajectory quality, tool correctness, or policy compliance.

## 2. Core Mechanics & Algorithms

### Evaluation as a bounded state machine

```text
ACCEPT
  -> LOAD_POLICY
  -> EXECUTE_STEP
  -> CAPTURE_ARTIFACTS
  -> SCORE_INLINE
     -> CONTINUE             if step is valid and budget remains
     -> RETRY_TRANSIENT      if failure is transient and retry budget remains
     -> REPLAN               if evidence is weak but the task is still feasible
     -> APPROVAL_WAIT        if side effect requires human consent
     -> FAIL_CLOSED          if policy, schema, or deadline is violated
  -> SCORE_POST_RUN
  -> COMPLETE
```

This state machine matters because it creates measurable transitions. `Task success` is computed from terminal state, not narrative confidence. `Trajectory quality` is computed from transitions such as `retry`, `replan`, `approval_wait`, and `fail_closed`, not only from how polished the final answer sounds.

### Metric families

#### Task success

For operational agents, `task success` should be a state predicate:

```text
task_success(run)
  = 1 if terminal_state in allowed_success_states
      and acceptance_checks_passed
    else 0
```

Examples:

- ticket-routing agent: ticket moved to the correct queue and audit note persisted
- retrieval assistant: answer returned with minimum evidence count and no unsupported claims
- browser agent: target page reached and intended form submission confirmed

The invariant is that `success criteria must be machine-checkable`. Free-form self-grading is weaker than state- or artifact-based confirmation.

#### Trajectory efficiency

Trajectory quality should penalize hidden thrash:

```text
trajectory_efficiency
  = successful_runs / total_steps

trajectory_penalty
  = w1 * retry_count
  + w2 * replan_count
  + w3 * branch_count
  + w4 * loop_depth
```

The runtime signal comes from checkpoints, retries, and branch structure. A correct final answer can still be operationally poor if it required too many replays or dead branches.

Complexity:

- trace aggregation over `n` events is `O(n)`
- branch-depth analysis on a DAG trace is `O(V + E)`
- percentile computation over streaming windows is `O(log n)` with heap-based or sketch-based summarization

#### Tool accuracy

Tool correctness is a conjunction, not a single check:

```text
tool_accuracy
  = schema_valid_rate * semantic_correct_rate * replay_safe_rate
```

Where:

- `schema_valid_rate`: valid tool name and JSON arguments
- `semantic_correct_rate`: right business object, right fields, right action
- `replay_safe_rate`: repeated execution does not produce duplicate side effects

This distinction comes directly from the local research: strict schemas reduce malformed calls, but they do not guarantee that the chosen record, amount, recipient, or action is correct.

#### Quality

In retrieval-heavy systems, the evaluator should separate evidence quality from answer quality:

```text
quality_score
  = groundedness * evidence_sufficiency * citation_fidelity
```

Useful sub-checks:

- `groundedness`: claims supported by retrieved evidence
- `evidence_sufficiency`: enough relevant evidence reached the model
- `citation_fidelity`: citations point to the actual supporting artifacts
- `memory_hygiene`: stale cache or stale memory did not dominate fresher evidence

#### Cost and latency

Cost and latency are first-class outputs, not afterthoughts:

```text
critical_path_latency
  = planning
  + max(parallel_branch_durations)
  + verification
  + synthesis
```

The critical path is what matters to users. Summing every branch duration overstates perceived latency when the branches ran concurrently.

### Convergence invariants

- `max_retries`, `max_replans`, and overall deadline must be explicit.
- Every tool mutation needs an `idempotency_key`.
- Every branch inherits a smaller deadline than its parent.
- Every judged artifact needs a stable `artifact_id`.
- Evaluation input channels must preserve source boundaries so retrieved text or tool output is not silently promoted into instruction space.

## 3. Token Economics & NFR Analysis

> ⚠️ Gap: The local `research_cursor` set is strong on tool pricing primitives, cache multipliers, and workload-shape trade-offs, but weak on universal public percentile benchmarks for long-horizon agent runs. The `p50/p95/p99` targets below are engineering envelopes, not vendor guarantees.

### Cost formulas

Assumptions used below:

- `runs = 1000`
- `U` = uncached input tokens per run
- `C` = cache-eligible prefix tokens per run
- `h` = cache hit rate on `C`
- `O` = output tokens per run
- `P_in_fast`, `P_out_fast` = fast-tier model prices per `1M` tokens
- `P_in_deep`, `P_out_deep` = deep-tier model prices per `1M` tokens
- cache read cost is approximated as `0.1x` input-token price where supported
- cache write cost is approximated as `1.25x` input-token price where supported
- `S_web = $10 / 1k calls`
- `S_file = $2.50 / 1k calls`
- browser/computer tool declarations add roughly `4,520-6,670` input tokens before screenshots in the cited local material

Reusable effective-input formula:

```text
effective_input_cost(model)
  = (
      U * P_in_model +
      C * (1 - h) * 1.25 * P_in_model +
      C * h * 0.10 * P_in_model
    ) / 1_000_000
```

#### Evaluator-only scoring run

This is the post-run judge that consumes an existing trace plus artifacts without making new external calls.

```text
$ cost per 1k evaluation runs
  = 1000 * (
      effective_input_cost(judge_model)
      + (O_judge * P_out_judge) / 1_000_000
    )
```

This is usually the cheapest tier because it does not include tool surcharges. It is the right place for batch regression scoring and nightly benchmark sweeps.

#### Inline tool-using evaluation run

This path verifies tool outputs, fetches references, or uses hosted search while the user is waiting.

```text
$ cost per 1k runs
  = 1000 * (
      effective_input_cost(model)
      + (O * P_out_model) / 1_000_000
    )
    + web_calls_per_1k * 10
    + file_calls_per_1k * 2.5
    + sandbox_runs_per_1k * sandbox_cost_per_run
```

Worked example using only values present in the local corpus:

- `web_calls_per_1k = 150`
- `file_calls_per_1k = 300`
- `sandbox_cost_per_run = $0.0075`
- `sandbox_runs_per_1k = 100`

```text
tool_and_sandbox_spend_per_1k
  = 150 * $10 / 1000
  + 300 * $2.50 / 1000
  + 100 * $0.0075
  = $1.50 + $0.75 + $0.75
  = $3.00 per 1k runs
```

That `$3.00 / 1k runs` is only the non-model portion. The model spend still scales with uncached trace size, cache hit rate, and judge verbosity.

#### Browser-backed evaluation path

If the evaluator needs browser or computer-use verification, fixed prompt overhead becomes visible:

```text
$ browser-backed eval per 1k runs
  = 1000 * (
      effective_input_cost(model)
      + ((T_browser_overhead + T_screenshots + T_tool_results) * P_in_model) / 1_000_000
      + (O * P_out_model) / 1_000_000
    )
```

The main lesson is that browser verification can be operationally correct but economically expensive even before the first screenshot arrives.

### Latency targets

Recommended user-facing targets by workload shape:

- `inline policy + tool validation`: `p50 <= 250ms`, `p95 <= 800ms`, `p99 <= 1.5s`
- `single-run post-run evaluation`: `p50 <= 1.2s`, `p95 <= 3.0s`, `p99 <= 5.0s`
- `trajectory-heavy or browser-backed evaluation`: `p50 <= 2.5s`, `p95 <= 6.0s`, `p99 <= 10.0s`

Mitigation strategy by percentile:

- `p50`: warm connections, cached prompt prefixes, colocated trace store and judge service, streaming first token for user-visible explanations
- `p95`: bound branch fan-out, cap trace size before judging, parallelize independent validators, downgrade from browser verification to API verification when policy allows
- `p99`: admission control, per-branch deadlines, fail-open only for non-mutating telemetry, serve a partial scorecard with explicit `degraded=true` instead of waiting indefinitely

### Throughput and back-pressure

Useful sizing heuristic:

```text
judge_tokens_per_second
  = qps * (U + C + O)
```

If the judge service has capacity `J_tps`, then:

```text
safe_qps
  = J_tps / (U + C + O)
```

Back-pressure order should be deliberate:

1. drop optional explanation text before dropping core metrics
2. switch from deep-tier judge to fast-tier judge for low-risk traffic
3. sample trajectory scoring before sampling task-success scoring
4. disable expensive browser verification before disabling schema validation

### Non-functional requirements

- `availability`: `99.9%` for inline evaluators; `99.95%` for audit/event persistence
- `RPO`: `<= 5 minutes` for trace and metric buffers; `0` for immutable audit log if it is the compliance system of record
- `RTO`: `<= 30 minutes` for judge service failover; `<= 4 hours` for backfill of non-critical batch evaluation jobs
- `residency`: keep evaluator artifacts in the same regional boundary as the run that generated them
- `compliance`: preserve source lineage, approval records, and mutation intent separately so later audit can prove who authorized what and on which evidence

## 4. Distributed Resilience & Security

Evaluation is only trustworthy if it survives retries, replays, and partial outages without silently changing the meaning of the run.

### Durable execution

Recommended pattern:

- use `Temporal` or an equivalent workflow engine for long-running evaluation pipelines
- publish raw run events to `Kafka` or an append-only event bus
- checkpoint after each major phase: `capture_trace`, `validate_tools`, `score_quality`, `emit_audit`
- dead-letter malformed or poison-pill traces instead of blocking the whole scoring fleet

Durable flow:

```text
agent_run_completed
  -> append trace event
  -> workflow resumes evaluator
  -> load checkpoint
  -> score next phase
  -> persist result
  -> ack event
```

This design prevents a judge timeout from forcing the entire agent run to be repeated.

### Failure taxonomy

- `transient`: network timeout, rate limit, temporary vector-store timeout, judge-model transport error
- `permanent`: malformed artifact schema, missing approval record, invalid tool manifest, unsupported tenant policy
- `poison pill`: trace event that always crashes parsing or always violates an invariant

Required controls:

- `idempotency_key` on every replayable side effect
- `correlation_id` and `run_id` on every metric event
- `dedupe window` for event consumers
- `quarantine queue` for poison-pill traces

### Enterprise security

#### Zero-Trust MCP architecture

- expose tools through an authenticated MCP boundary
- scope each evaluator to read-only access unless mutation is explicitly required
- isolate credentials by tool and tenant
- require OAuth, PKCE, and resource scoping for external capability access where supported by the local MCP model

#### Tool-level RBAC

- `task_success_judge`: read run state, read artifacts, no mutation rights
- `trajectory_judge`: read checkpoints and trace edges, no business-record access unless necessary
- `tool_accuracy_judge`: read tool definitions, read tool outputs, mutation disabled by default
- `human_review_console`: can release approval holds, cannot rewrite stored evidence

#### PII filtering pipeline

```text
detect -> classify -> redact/tokenize -> persist original in vault -> judge redacted copy -> audit access
```

The judge should usually see redacted artifacts, while privileged investigators can retrieve originals under audit.

#### Auditability

- write append-only evaluator decisions with hashes of the judged artifacts
- store `who approved`, `what policy fired`, and `why fallback was selected`
- preserve chain-of-custody from tool result to final scorecard
- sign or hash large trace bundles so later investigations can prove they were not altered

## 5. Production Enterprise Code

```python
from __future__ import annotations

import json
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


def log_event(event: str, **fields: object) -> None:
    payload = {"ts": round(time.time(), 3), "event": event, **fields}
    print(json.dumps(payload, sort_keys=True))


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 0.05
    max_delay_s: float = 0.25
    jitter_s: float = 0.03


def with_retries(
    fn: Callable[[], Dict[str, object]],
    retry_policy: RetryPolicy,
    correlation_id: str,
    component: str,
) -> Dict[str, object]:
    for attempt in range(1, retry_policy.max_attempts + 1):
        try:
            return fn()
        except PermanentError:
            raise
        except TransientError as exc:
            log_event(
                "retry",
                correlation_id=correlation_id,
                component=component,
                attempt=attempt,
                error=str(exc),
            )
            if attempt == retry_policy.max_attempts:
                raise
            delay = min(retry_policy.max_delay_s, retry_policy.base_delay_s * (2 ** (attempt - 1)))
            time.sleep(delay + random.uniform(0.0, retry_policy.jitter_s))
    raise RuntimeError("unreachable")


@dataclass
class CircuitBreaker:
    failure_threshold: int = 2
    reset_timeout_s: float = 0.30
    state: CircuitState = CircuitState.CLOSED
    failures: int = 0
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
        self.failures = 0
        self.opened_at = 0.0

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = time.monotonic()


@dataclass
class Trace:
    run_id: str
    final_state: str
    acceptance_checks_passed: bool
    retries: int
    replans: int
    branches: int
    loop_depth: int
    tool_name: str
    tool_args_valid: bool
    tool_target_correct: bool
    replay_safe: bool
    evidence_count: int
    grounded_claims: int
    total_claims: int
    latency_ms: int
    input_tokens: int
    cached_tokens: int
    output_tokens: int


class Judge:
    def evaluate(self, trace: Trace) -> Dict[str, object]:
        raise NotImplementedError


@dataclass
class PrimaryJudge(Judge):
    failure_rate: float = 0.35

    def evaluate(self, trace: Trace) -> Dict[str, object]:
        if random.random() < self.failure_rate:
            raise TransientError("primary_judge_timeout")
        return build_scorecard(trace, judge_name="primary-judge")


@dataclass
class SecondaryJudge(Judge):
    failure_rate: float = 0.10

    def evaluate(self, trace: Trace) -> Dict[str, object]:
        if random.random() < self.failure_rate:
            raise TransientError("secondary_judge_rate_limited")
        return build_scorecard(trace, judge_name="secondary-judge")


def deterministic_fallback(trace: Trace) -> Dict[str, object]:
    task_success = int(trace.final_state == "completed" and trace.acceptance_checks_passed)
    return {
        "judge": "deterministic-fallback",
        "task_success": task_success,
        "trajectory_efficiency": round(1.0 / max(1, 1 + trace.retries + trace.replans + trace.branches), 3),
        "tool_accuracy": float(trace.tool_args_valid and trace.tool_target_correct and trace.replay_safe),
        "quality_score": round(trace.grounded_claims / max(1, trace.total_claims), 3),
        "latency_ms": trace.latency_ms,
        "degraded": True,
        "reason": "judge_unavailable",
    }


def build_scorecard(trace: Trace, judge_name: str) -> Dict[str, object]:
    task_success = int(trace.final_state == "completed" and trace.acceptance_checks_passed)
    trajectory_penalty = (
        0.15 * trace.retries
        + 0.20 * trace.replans
        + 0.10 * max(0, trace.branches - 1)
        + 0.05 * max(0, trace.loop_depth - 1)
    )
    trajectory_efficiency = max(0.0, round(1.0 - trajectory_penalty, 3))
    tool_accuracy = round(
        float(trace.tool_args_valid) * float(trace.tool_target_correct) * float(trace.replay_safe),
        3,
    )
    groundedness = trace.grounded_claims / max(1, trace.total_claims)
    evidence_sufficiency = min(1.0, trace.evidence_count / 3.0)
    quality_score = round(groundedness * evidence_sufficiency, 3)
    estimated_cost_usd = round(
        ((trace.input_tokens - trace.cached_tokens) * 0.50 + trace.cached_tokens * 0.05 + trace.output_tokens * 1.50)
        / 1_000_000,
        6,
    )
    return {
        "judge": judge_name,
        "task_success": task_success,
        "trajectory_efficiency": trajectory_efficiency,
        "tool_accuracy": tool_accuracy,
        "quality_score": quality_score,
        "latency_ms": trace.latency_ms,
        "estimated_cost_usd": estimated_cost_usd,
        "degraded": False,
    }


@dataclass
class EvaluationService:
    primary: Judge
    secondary: Judge
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    def evaluate(self, trace: Trace) -> Dict[str, object]:
        correlation_id = str(uuid.uuid4())
        log_event("evaluation_started", correlation_id=correlation_id, run_id=trace.run_id)

        if self.breaker.state != CircuitState.OPEN:
            try:
                self.breaker.before_call()
                result = with_retries(
                    fn=lambda: self.primary.evaluate(trace),
                    retry_policy=self.retry_policy,
                    correlation_id=correlation_id,
                    component="primary_judge",
                )
                self.breaker.record_success()
                log_event("evaluation_finished", correlation_id=correlation_id, judge=result["judge"], degraded=False)
                return result
            except TransientError as exc:
                self.breaker.record_failure()
                log_event(
                    "primary_degraded",
                    correlation_id=correlation_id,
                    breaker_state=self.breaker.state.value,
                    error=str(exc),
                )

        try:
            result = with_retries(
                fn=lambda: self.secondary.evaluate(trace),
                retry_policy=self.retry_policy,
                correlation_id=correlation_id,
                component="secondary_judge",
            )
            result["degraded"] = True
            result["reason"] = "secondary_judge_used"
            log_event("evaluation_finished", correlation_id=correlation_id, judge=result["judge"], degraded=True)
            return result
        except TransientError:
            result = deterministic_fallback(trace)
            log_event("evaluation_finished", correlation_id=correlation_id, judge=result["judge"], degraded=True)
            return result


def main() -> None:
    random.seed(7)
    trace = Trace(
        run_id="run-42",
        final_state="completed",
        acceptance_checks_passed=True,
        retries=1,
        replans=0,
        branches=2,
        loop_depth=2,
        tool_name="crm.update_account",
        tool_args_valid=True,
        tool_target_correct=True,
        replay_safe=True,
        evidence_count=4,
        grounded_claims=5,
        total_claims=5,
        latency_ms=1480,
        input_tokens=6200,
        cached_tokens=1800,
        output_tokens=220,
    )
    service = EvaluationService(primary=PrimaryJudge(), secondary=SecondaryJudge())
    scorecard = service.evaluate(trace)
    print(json.dumps(scorecard, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
```

This service demonstrates the operational pattern the module argues for: retries use exponential backoff plus jitter, the primary judge sits behind a circuit breaker, the system falls back to a secondary judge and then to a deterministic scorecard, every event carries a `correlation_id`, and degraded operation is explicit rather than hidden.

## 6. Architectural System Design Scenarios

### Scenario 1: Enterprise support agent scorecard for grounded answers

**Problem statement**

Design an evaluation stack for a multi-tenant support agent serving product manuals, ticket history, and billing policies. The business wants `task success`, `groundedness`, `cost`, and `latency` scored on every user-visible run at `25k` requests/min while keeping inline evaluation at `p99 <= 1.5s`.

**Proposed architecture**

```text
┌──────────────┐    ┌────────────────────┐    ┌─────────────────────────┐
│ Support API  │ -> │ Agent Runtime      │ -> │ Inline Validators       │
└──────────────┘    │ retrieve / answer  │    │ schema / budget / PII   │
                    └─────────┬──────────┘    └──────────┬──────────────┘
                              │                          │
                              v                          v
                    ┌────────────────────┐    ┌─────────────────────────┐
                    │ Trace / Artifact   │ -> │ Post-run Judge Service  │
                    │ Store              │    │ quality / cost / SLA    │
                    └────────────────────┘    └──────────┬──────────────┘
                                                         v
                                              ┌─────────────────────────┐
                                              │ Audit + BI + Alerting   │
                                              └─────────────────────────┘
```

Technology choices:

- workflow state in `LangGraph`-style checkpoints or equivalent
- trace and usage ingestion through an append-only event bus
- fast-tier judge inline, deeper judge asynchronously for audit backfill
- immutable audit stream for degraded decisions and policy overrides

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Inline-only deep judge | High | Worst | Medium | Strong | Moderate |
| Inline fast judge + async deep backfill | Medium | Best | Medium-high | Strong | High |
| Nightly batch-only evaluation | Lowest | Best for users | Low | Weak for real-time controls | High |

**Decision rationale**

`Inline fast judge + async deep backfill` wins because it keeps real-time safety and SLA visibility while avoiding the tail-latency penalty of running the most expensive evaluator on the user path. Batch-only scoring is too slow for live guardrails, while deep-judge-only inline scoring burns both cost and latency budget.

### Scenario 2: Finance operations agent with tool-accuracy and replay-safety guarantees

**Problem statement**

Design an evaluation system for a finance operations agent that updates invoices, refunds, and credit holds across ERP and CRM tools. The system must prove `tool accuracy`, `replay safety`, and `policy compliance` for every mutating action, and it must surface degradations within `p95 <= 3.0s` even during partial downstream outages.

**Proposed architecture**

```text
┌──────────────┐    ┌────────────────────┐    ┌─────────────────────────┐
│ Finance API  │ -> │ Policy / Approval  │ -> │ Agent + MCP Tool Proxy  │
└──────────────┘    └────────────────────┘    │ RBAC / idempotency      │
                                              └──────────┬──────────────┘
                                                         │
                                                         v
                                              ┌─────────────────────────┐
                                              │ Mutation Event Log       │
                                              │ before/after snapshots   │
                                              └──────────┬──────────────┘
                                                         v
                                              ┌─────────────────────────┐
                                              │ Tool-Accuracy Evaluator  │
                                              │ semantic + replay checks │
                                              └──────────┬──────────────┘
                                                         v
                                              ┌─────────────────────────┐
                                              │ Compliance Archive       │
                                              └─────────────────────────┘
```

Technology choices:

- MCP gateway with least-privilege tokens per downstream system
- idempotency keys on every mutation request
- workflow replay handled by durable execution rather than ad hoc retries
- before/after snapshots captured for semantic correctness review

**Trade-off evaluation matrix**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Human approval on every mutation | Highest | Worst | Medium | Strongest | Low |
| Policy-gated automation + sampled human review | Medium | Best | High | Strong | High |
| Unreviewed full automation | Lowest | Best | Low | Weak | High until first major incident |

**Decision rationale**

`Policy-gated automation + sampled human review` is the correct enterprise default. It preserves throughput while still proving replay safety, least privilege, and semantic correctness on the highest-risk edges. Mandatory human approval for every action does not scale, and unreviewed automation collapses the separation between model confidence and authorized business correctness.
