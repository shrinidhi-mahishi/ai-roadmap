# Specialized Agents — Coding, browser, research, data agents

## 1. System Topology & Data Flow

`Specialized agents` are role-bounded execution surfaces. The practical boundary is not "different prompts" but `different authority, tool scopes, context slices, and completion contracts`. A coordinator owns routing, deadlines, approvals, and final synthesis; each specialist owns one narrow loop: `coding`, `browser`, `research`, or `data` (`04-agent-architecture.md`, `05-agent-frameworks.md`, `09-multi-agent-systems.md`, `11-specialized-agents.md`).

```text
┌────────────────────────────── Control Plane ───────────────────────────────┐
│ User / API -> AuthN/Z -> Policy Router -> Coordinator Runtime              │
│      │             │              │                  │                     │
│      │             │              │                  ├─ Task Classifier    │
│      │             │              │                  ├─ Specialist Router   │
│      │             │              │                  ├─ Approval Gate       │
│      │             │              │                  └─ Final Synthesizer   │
│      └────────────────────────────────────────────> Correlation / Deadline  │
└─────────────────────────────────────────────────────────────────────────────┘
                                 │
                                 v
┌────────────────────────────── Data Plane ──────────────────────────────────┐
│  Specialist Pool                                                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐   │
│  │ Coding Agent │  │ Browser      │  │ Research     │  │ Data Agent    │   │
│  │ repo/shell   │  │ Agent        │  │ Agent        │  │ compute/ETL   │   │
│  │ tests/lint   │  │ DOM/screen   │  │ plan/retrieve│  │ files/query   │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬────────┘   │
│         │                 │                 │                  │            │
│         └────────────┬────┴────────────┬────┴────────────┬─────┘            │
│                      v                 v                 v                   │
│               Tool Proxies / MCP Gateway / Sandboxes / Cache                │
└─────────────────────────────────────────────────────────────────────────────┘
            │                        │                         │
            v                        v                         v
┌──────────────────────┐  ┌──────────────────────┐  ┌────────────────────────┐
│ Persistence Layer    │  │ Tool Boundary        │  │ Telemetry / Audit      │
│ workflow events      │  │ RBAC / allowlists    │  │ traces / metrics       │
│ checkpoints          │  │ approvals            │  │ token ledger           │
│ result cache         │  │ PII redact / scan    │  │ immutable decision log │
│ artifact store       │  │ rate limits          │  │ alerting / SIEM        │
└──────────────────────┘  └──────────────────────┘  └────────────────────────┘
```

### Request-flow narrative

1. `AuthN/Z` validates the caller, attaches `tenant_id`, `correlation_id`, and an end-to-end deadline.
2. `Policy Router` decides whether the request needs mutation, UI interaction, evidence synthesis, or bounded computation.
3. `Coordinator Runtime` narrows the candidate specialists to the smallest valid set:
   - `coding agent` for repository mutation, shell commands, tests, and refactors
   - `browser agent` for UI-only workflows, visual verification, or SaaS paths without usable APIs
   - `research agent` for decomposable evidence gathering, verification, and citation-heavy synthesis
   - `data agent` for computation, file analysis, transforms, and corpus-backed numerical work
4. The chosen specialist receives only a role-scoped payload rather than the full transcript. This is the primary token and safety advantage of specialization (`09-multi-agent-systems.md`, `11-specialized-agents.md`).
5. Each specialist executes through `Tool Proxies / MCP Gateway / Sandboxes / Cache`:
   - `coding` uses repo tools, shell, test runners, and hosted code execution
   - `browser` uses an `observe -> act -> observe` page loop with screenshots or DOM actions
   - `research` uses a `plan -> retrieve -> verify -> answer` loop
   - `data` uses code execution, artifacts, indexes, and retrieval instead of transcript replay
6. The specialist writes intermediate state to `Persistence Layer` as task events, checkpoints, and artifacts.
7. `Final Synthesizer` merges successful outputs, annotates degraded branches, and returns either a complete answer or a safe partial answer.
8. `Telemetry / Audit` records router decisions, tool calls, cache hits, approval pauses, redaction actions, and final output hashes.

The main architecture rule is simple: `specialization is enforced by narrowing tools, context, and authority`. If all four specialists see the same tools and same full transcript, the system is only cosmetically specialized (`04-agent-architecture.md`, `09-multi-agent-systems.md`, `11-specialized-agents.md`).

## 2. Core Mechanics & Algorithms

### Specialist routing as a guarded state machine

```text
ACCEPT
  -> CLASSIFY_TASK
  -> FILTER_BY_POLICY
  -> SELECT_SPECIALIST
  -> PREPARE_SCOPED_CONTEXT
  -> EXECUTE_SPECIALIST
  -> OBSERVE_RESULT
     -> COMPLETE              if required evidence or mutation succeeded
     -> RETRY_TRANSIENT       if failure is transient and retry budget remains
     -> FALLBACK_SPECIALIST   if adjacent capability can satisfy intent safely
     -> APPROVAL_WAIT         if side effects need human approval
     -> FAIL_CLOSED           if required controls cannot be satisfied
```

This state machine matters more than any framework because it encodes convergence. Without explicit transitions, specialists drift into unbounded replanning, browser thrash, code replay, or retrieval fan-out storms (`04-agent-architecture.md`, `08-planning-reasoning.md`, `11-specialized-agents.md`).

### Per-specialist execution loops

- `Coding agent`: `plan -> edit -> validate -> retry/fallback -> return`. The loop is tool-centric and mutation-sensitive. Tool-schema size, container reuse, and idempotent validation dominate both economics and safety (`03-tool-use.md`, `05-agent-frameworks.md`).
- `Browser agent`: `observe -> act -> observe`. The loop is sequential because each action depends on the latest visual state. This makes browser specialists the slowest and most brittle on the critical path (`03-tool-use.md`, `11-specialized-agents.md`).
- `Research agent`: `decompose -> retrieve -> rerank -> verify -> synthesize`. Quality depends on decomposition quality, first-stage recall, and verifier discipline rather than on one big final generation (`06-rag.md`, `08-planning-reasoning.md`).
- `Data agent`: `classify workload -> execute computation -> persist artifact -> summarize`. The optimization goal is to move bulky data from prompt space into code execution, caches, and retrieval surfaces (`03-tool-use.md`, `07-memory.md`, `11-specialized-agents.md`).

### Routing and complexity

If there are `n` specialists, a rules-plus-semantic router has first-order complexity:

```text
route_cost ~= O(n)
```

In production, routing is performed over the eligible subset:

```text
eligible_specialists
  = specialists
    filtered_by(tool_scope, tenant_policy, side_effect_risk, data_residency)

effective_route_cost ~= O(|eligible_specialists|)
```

The critical-path latency differs by workload shape:

```text
coding_latency
  ~= planning + edit + validate + retry_overhead

browser_latency
  ~= sum(observe_i + act_i) for i in 1..steps

research_latency
  ~= planning + max(parallel_subquery_latency) + rerank + synthesis

data_latency
  ~= planning + compute_or_retrieve + artifact_summary
```

### Failure-relevant invariants

- `Bounded authority invariant`: each specialist receives only the tools required for its role.
- `Stable identity invariant`: every branch carries `run_id`, `task_id`, `attempt`, `tenant_id`, and `correlation_id`.
- `Replay-safe invariant`: all side effects require idempotency keys because resumable workflows and retries are expected, not exceptional.
- `Deadline inheritance invariant`: each specialist gets a smaller deadline than the parent request budget.
- `Evidence invariant`: research and data specialists must expose citations, artifacts, or intermediate traces; otherwise failures are not debuggable.
- `Observation freshness invariant`: browser actions are valid only against the latest screenshot or DOM snapshot.

### Convergence properties

```text
converges if:
  max_turns is finite
  max_subqueries is finite
  max_browser_steps is finite
  each retry burns remaining deadline
  each fallback is narrower or cheaper than the primary path
```

The strongest practical distinction is that `browser` specialists pay in sequential observation, `research` specialists pay in fan-out and verification, `coding` specialists pay in mutation safety and validation loops, and `data` specialists pay in artifact and memory design.

## 3. Token Economics & NFR Analysis

> ⚠️ Gap: The local `research_cursor` set contains stronger evidence for token overheads, tool charges, cache mechanics, and workload-shape trade-offs than for universal vendor-neutral percentile benchmarks. The `p50/p95/p99` targets below are recommended SLO envelopes to engineer toward, not provider guarantees.

### Cost formulas

Assumptions shared across formulas:

- `runs = 1000`
- `P_fast_in`, `P_fast_cache`, `P_fast_out` = fast-tier model prices per `1M` tokens
- `P_deep_in`, `P_deep_cache`, `P_deep_out` = deep-tier model prices per `1M` tokens
- `h` = exact-prefix cache hit rate on cacheable prompt prefixes
- `U` = uncached input tokens
- `C` = cache-eligible prefix tokens
- `O` = output tokens

Effective input cost for any specialist:

```text
effective_input_cost(model)
  = (
      U * P_model_in +
      C * ((1 - h) * P_model_in + h * P_model_cache)
    ) / 1_000_000
```

#### Coding agent

Variables:

- `T_schema` = tool-definition tokens
- `T_result` = tool-result reinjection tokens
- `E_container` = hosted code-execution charge per run
- `V` = validation-loop turns

```text
$ coding cost per 1k runs
  = 1000 * (
      effective_input_cost(model) +
      ((T_schema + T_result) * P_model_in) / 1_000_000 +
      (O * P_model_out) / 1_000_000 +
      E_container +
      V * retry_surcharge
    )
```

Worked floor using local evidence:

- OpenAI hosted `1 GB` code execution has a `5` minute minimum at `$0.03 / 20 minutes`
- therefore `E_container_floor = 5 * ($0.03 / 20) = $0.0075 per run = $7.50 / 1k runs`

```text
$ coding execution floor per 1k runs = $7.50
```

This is before model tokens, tool schemas, or validation replays are added (`03-tool-use.md`, `11-specialized-agents.md`).

#### Browser agent

Variables:

- `B_overhead` = browser/computer tool declaration tokens
- `S_obs` = screenshot or page-observation tokens across all steps
- `A_steps` = action count
- `R_tool` = tool-result reinjection tokens

```text
$ browser cost per 1k runs
  = 1000 * (
      effective_input_cost(model) +
      ((B_overhead + S_obs + R_tool) * P_model_in) / 1_000_000 +
      (O * P_model_out) / 1_000_000
    )
```

Local published overhead floors:

- `browser_toolset_20260801`: about `6,610-6,670` input tokens before screenshots, user prompt, or results
- `computer_toolset_20260801`: about `4,520-4,590` input tokens before screenshots, user prompt, or results

Using the local Sonnet-style assumption of `$2 / 1M input tokens` from `03-tool-use.md`:

```text
$ browser toolset floor per 1k runs
  = 6,670 * 1000 * ($2 / 1_000_000)
  = $13.34 per 1k runs

$ computer toolset floor per 1k runs
  = 4,590 * 1000 * ($2 / 1_000_000)
  = $9.18 per 1k runs
```

Those are only declaration floors; screenshots and iterative observation materially increase the real total (`03-tool-use.md`, `11-specialized-agents.md`).

#### Research agent

Variables:

- `Q` = planned subqueries per run
- `K` = reranked chunks per subquery
- `T_chunk` = average tokens per reranked chunk
- `P_plan` = planning-token cost per run
- `P_rerank` = reranking-token cost rate

```text
rerank_tokens_per_run = Q * K * T_chunk

$ research cost per 1k runs
  = 1000 * (
      effective_input_cost(model) +
      (O * P_model_out) / 1_000_000 +
      P_plan +
      (rerank_tokens_per_run * P_rerank) / 1_000_000
    )
```

Local Azure-style worked example:

- `Q = 3`
- `K = 50`
- `T_chunk = 500`

```text
rerank_tokens_per_run
  = 3 * 50 * 500
  = 75,000 tokens

rerank_tokens_per 1k runs
  = 75,000,000 tokens
```

The local notes also cite `2,000` retrievals producing `150M` reranking tokens plus `$3.30` reranking and `$1.02` planning under Azure's hypothetical example, which is a direct warning that fan-out can dominate economics if decomposition quality is poor (`06-rag.md`, `08-planning-reasoning.md`, `11-specialized-agents.md`).

#### Data agent

Variables:

- `A_in` = artifact or retrieval tokens reinjected into the model
- `X_compute` = code-execution charge per run
- `M_cache` = savings from caches, compaction, or artifact reuse

```text
$ data cost per 1k runs
  = 1000 * (
      effective_input_cost(model) +
      (A_in * P_model_in) / 1_000_000 +
      (O * P_model_out) / 1_000_000 +
      X_compute
      - M_cache
    )
```

The economic goal is not just "use a cheaper model." It is to convert repeated transcript reasoning into `artifact reuse`, `retrieval reuse`, and `cached prefixes` so the model sees smaller working sets (`03-tool-use.md`, `05-agent-frameworks.md`, `07-memory.md`, `11-specialized-agents.md`).

### Latency targets

Recommended SLO envelopes by specialist type:

- `Coding agent`: `p50 <= 1.8s`, `p95 <= 6.0s`, `p99 <= 12.0s`
- `Browser agent`: `p50 <= 4.0s`, `p95 <= 12.0s`, `p99 <= 25.0s`
- `Research agent`: `p50 <= 2.5s`, `p95 <= 8.0s`, `p99 <= 15.0s`
- `Data agent`: `p50 <= 2.0s`, `p95 <= 7.0s`, `p99 <= 14.0s`

Mitigations by percentile:

- `p50`: cache stable prefixes, minimize tool schemas, warm containers, and keep specialist context narrow.
- `p95`: parallelize independent research subqueries, bound validation loops, pre-authorize low-risk tools, and reuse artifacts instead of replaying raw inputs.
- `p99`: impose per-branch deadlines, circuit breakers, degraded-mode fallbacks, queue admission control, and fail-closed policies for privileged mutations.

### Throughput and back-pressure

Useful capacity formulas:

```text
effective_qps
  <= min(
       coordinator_qps,
       coding_pool_qps / avg_coding_calls_per_run,
       browser_pool_qps / avg_browser_calls_per_run,
       research_pool_qps / avg_research_calls_per_run,
       data_pool_qps / avg_data_calls_per_run
     )
```

```text
queue_pressure
  = arrival_rate / specialist_service_rate
```

```text
browser_capacity_penalty
  ~= sequential_steps_per_run
```

Back-pressure policy should be explicit:

1. Reject or defer low-priority requests when `queue_pressure > 1`.
2. Drop optional specialists before required ones.
3. Route browser tasks to APIs when a safe API path exists.
4. Reduce research fan-out and rerank depth under saturation.
5. Fall back from deep-tier to fast-tier or deterministic checks when the deadline budget is nearly exhausted.

### Availability, RPO, RTO, compliance

Recommended enterprise targets:

- `overall availability`: `99.9%` minimum, `99.95%` for tier-1 internal copilots
- `workflow event store`: `99.99%`
- `RPO`: `<= 1 minute` for workflow events, approvals, and audit trails
- `RTO`: `<= 15 minutes` same-region, `<= 60 minutes` cross-region recovery

Compliance implications:

- `browser` and `coding` paths need stronger approval, segregation, and audit controls because they can mutate external state
- `research` and `data` paths need stronger provenance, evidence retention, and permission-aware retrieval because their main risk is poisoned or overexposed context
- `GDPR` / `CCPA`: data minimization, deletion workflows, and residency-aware routing
- `SOC 2` / `ISO 27001`: baseline for secrets, RBAC, logging, change control, and incident handling

## 4. Distributed Resilience & Security

### Durable execution patterns

Specialists should be treated as distributed-system components, not as prompt variants. The clean durability split is:

```text
User Request
  -> Durable Workflow Engine
  -> Specialist Task Queue
  -> Specialist Executor
  -> Checkpoint / Artifact Write
  -> Final Synthesizer
  -> Response or resumable pause
```

Recommended platform patterns:

- `Temporal` or equivalent for long-running specialist orchestration, replay, and resumable approvals
- `Kafka` or equivalent for branch events, DLQ handling, and async specialist backlogs
- checkpoint after each major specialist milestone:
  - `coding`: after plan, after edit set, after validation
  - `browser`: after each stable observation/action pair
  - `research`: after plan, retrieval set, rerank set, verifier output
  - `data`: after artifact creation and summary generation

### Failure taxonomy

Transient failures:

- model rate limits
- network timeouts
- short-lived MCP or tool outages
- ephemeral browser-environment flakiness
- temporary sandbox or container cold-start delays

Permanent failures:

- schema mismatch between coordinator and specialist
- missing tenant entitlements
- forbidden mutation path
- deleted or inaccessible downstream resource
- invalid retrieval corpus or unsupported file type

Poison-pill signals:

- repeated identical browser failure on the same stale DOM state
- repeated code replay against the same non-idempotent change set
- research loop keeps rewriting queries without increasing evidence quality
- data job keeps loading oversized artifacts that breach deadline or token budgets

Required controls:

- idempotency keys on every side effect
- exponential backoff with jitter for transient faults
- circuit breakers per specialist endpoint and per downstream tool
- dead-letter promotion after retry-budget exhaustion
- degraded-mode synthesis so one failed noncritical specialist does not necessarily fail the whole run

### Zero-Trust MCP and tool security

The safe enterprise pattern is `Zero-Trust MCP above specialist execution`:

1. Treat every tool server, browser sandbox, code-execution container, and retrieval system as an independent protected resource.
2. Bind credentials to the specific server or action scope; never grant one broad token to all specialists.
3. Apply tool-level `RBAC` and least privilege:
   - `coding` can read repo metadata broadly, but write or deploy only through approved paths
   - `browser` can navigate approved domains, but high-impact clicks require confirmation
   - `research` can read approved corpora only
   - `data` can read or transform permitted datasets, but not exfiltrate raw sensitive extracts
4. Separate `recommendation` from `execution` for high-impact actions such as deploys, payments, ticket mutations, or production access.
5. Keep governance in the coordinator or workflow layer, not inside every specialist prompt (`10-mcp-interoperability.md`, `11-specialized-agents.md`).

### PII filtering and auditability

A compliance-grade path should be:

```text
ingress
  -> detect sensitive fields
  -> redact or tokenize
  -> route only allowed fields to specialist
  -> persist secure audit mapping
  -> emit immutable decision event
```

Minimum audit record:

- `correlation_id`
- `tenant_id`
- `user_id` or service principal
- `selected_specialist`
- `tool_scope`
- `approval_outcome`
- `policy_version`
- `redaction_actions`
- `model_or_runtime`
- `artifact_hash` or `output_hash`
- `degraded_mode_flag`

The source set is especially clear that `browser` specialists are the highest direct-action risk and that `research` or `data` specialists can still be compromised by prompt injection or poisoned retrieved context. Specialization reduces blast radius only when policy enforcement is external and auditable (`03-tool-use.md`, `07-memory.md`, `08-planning-reasoning.md`, `10-mcp-interoperability.md`, `11-specialized-agents.md`).

## 5. Production Enterprise Code

The example below is a runnable Python coordinator for four specialist types. It demonstrates:

- retries with exponential backoff and jitter
- a circuit breaker with `closed -> open -> half_open`
- fallback model chain `primary -> secondary -> deterministic fallback`
- structured logging with correlation IDs
- graceful degradation when a noncritical specialist is impaired

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
    base_delay_s: float = 0.15
    max_delay_s: float = 1.5
    jitter_s: float = 0.05


@dataclass
class CircuitBreaker:
    failure_threshold: int = 2
    reset_timeout_s: float = 1.0
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
class Endpoint:
    name: str
    latency_s: float
    failure_rate: float
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    def call(self, payload: str) -> str:
        self.breaker.before_call()
        time.sleep(self.latency_s)
        if random.random() < self.failure_rate:
            self.breaker.record_failure()
            raise TransientError(f"{self.name}_temporary_failure")
        self.breaker.record_success()
        return f"{self.name} handled: {payload}"


def with_retries(
    fn: Callable[[], str],
    retry_policy: RetryPolicy,
    correlation_id: str,
    specialist: str,
) -> str:
    for attempt in range(1, retry_policy.max_attempts + 1):
        try:
            return fn()
        except PermanentError:
            raise
        except TransientError as exc:
            log_event(
                "retry",
                correlation_id=correlation_id,
                specialist=specialist,
                attempt=attempt,
                error=str(exc),
            )
            if attempt == retry_policy.max_attempts:
                raise
            backoff = min(
                retry_policy.max_delay_s,
                retry_policy.base_delay_s * (2 ** (attempt - 1)),
            )
            time.sleep(backoff + random.uniform(0.0, retry_policy.jitter_s))


@dataclass
class SpecialistResult:
    specialist: str
    ok: bool
    output: str
    degraded: bool = False


@dataclass
class SpecialistAgent:
    name: str
    primary: Endpoint
    secondary: Endpoint
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    def deterministic_fallback(self, request: str) -> str:
        lowered = request.lower()
        if self.name == "coding":
            return "Static fallback: read-only review path selected; no mutations executed."
        if self.name == "browser":
            return "Static fallback: browser unavailable; return API-or-manual-verification recommendation."
        if self.name == "research":
            keywords = [token for token in lowered.split() if len(token) > 5][:5]
            return f"Static fallback: keyword bundle = {', '.join(keywords) or 'none'}."
        if self.name == "data":
            numbers = [token for token in lowered.split() if any(ch.isdigit() for ch in token)]
            return f"Static fallback: extracted numeric hints = {', '.join(numbers) or 'none'}."
        return "Static fallback: limited service available."

    def run(self, request: str, correlation_id: str) -> SpecialistResult:
        for endpoint in (self.primary, self.secondary):
            try:
                output = with_retries(
                    fn=lambda endpoint=endpoint: endpoint.call(request),
                    retry_policy=self.retry_policy,
                    correlation_id=correlation_id,
                    specialist=self.name,
                )
                log_event(
                    "specialist_success",
                    correlation_id=correlation_id,
                    specialist=self.name,
                    endpoint=endpoint.name,
                )
                return SpecialistResult(self.name, True, output, degraded=False)
            except TransientError as exc:
                log_event(
                    "endpoint_failed",
                    correlation_id=correlation_id,
                    specialist=self.name,
                    endpoint=endpoint.name,
                    breaker_state=endpoint.breaker.state.value,
                    error=str(exc),
                )

        fallback = self.deterministic_fallback(request)
        log_event(
            "specialist_degraded",
            correlation_id=correlation_id,
            specialist=self.name,
            reason="all_endpoints_failed",
        )
        return SpecialistResult(self.name, True, fallback, degraded=True)


@dataclass
class Coordinator:
    specialists: Dict[str, SpecialistAgent]
    required_specialists: List[str] = field(default_factory=lambda: ["research"])

    def route(self, request: str) -> List[str]:
        lowered = request.lower()
        selected: List[str] = []
        if any(token in lowered for token in ("code", "repo", "test", "refactor")):
            selected.append("coding")
        if any(token in lowered for token in ("browser", "ui", "click", "screen")):
            selected.append("browser")
        if any(token in lowered for token in ("research", "cite", "compare", "sources")):
            selected.append("research")
        if any(token in lowered for token in ("csv", "sql", "dataset", "analyze", "forecast")):
            selected.append("data")
        if not selected:
            selected.append("research")
        return selected

    def handle(self, request: str) -> Dict[str, object]:
        correlation_id = str(uuid.uuid4())
        chosen = self.route(request)
        log_event("run_started", correlation_id=correlation_id, specialists=chosen)

        results: Dict[str, SpecialistResult] = {}
        degraded: List[str] = []

        for name in chosen:
            result = self.specialists[name].run(request, correlation_id)
            results[name] = result
            if result.degraded:
                degraded.append(name)

        missing_required = [name for name in self.required_specialists if name in chosen and not results[name].ok]
        if missing_required:
            raise PermanentError(f"required_specialists_failed={missing_required}")

        response = {
            "correlation_id": correlation_id,
            "status": "degraded" if degraded else "ok",
            "specialists": chosen,
            "degraded_specialists": degraded,
            "summary": " | ".join(f"[{name}] {results[name].output}" for name in chosen),
        }
        log_event("run_finished", **response)
        return response


def build_demo() -> Coordinator:
    return Coordinator(
        specialists={
            "coding": SpecialistAgent(
                "coding",
                Endpoint("coding-primary", latency_s=0.05, failure_rate=0.15),
                Endpoint("coding-secondary", latency_s=0.07, failure_rate=0.05),
            ),
            "browser": SpecialistAgent(
                "browser",
                Endpoint("browser-primary", latency_s=0.09, failure_rate=0.60),
                Endpoint("browser-secondary", latency_s=0.08, failure_rate=0.20),
            ),
            "research": SpecialistAgent(
                "research",
                Endpoint("research-primary", latency_s=0.04, failure_rate=0.10),
                Endpoint("research-secondary", latency_s=0.05, failure_rate=0.03),
            ),
            "data": SpecialistAgent(
                "data",
                Endpoint("data-primary", latency_s=0.06, failure_rate=0.20),
                Endpoint("data-secondary", latency_s=0.06, failure_rate=0.05),
            ),
        }
    )


if __name__ == "__main__":
    random.seed(11)
    coordinator = build_demo()
    result = coordinator.handle(
        "Research pricing sources, analyze csv renewals, and verify the browser UI."
    )
    print(json.dumps(result, indent=2, sort_keys=True))
```

The operational point is that the coordinator does not hide degradation. A failed `browser` branch can fall back to a manual-or-API recommendation while the `research` branch still completes, and every retry and breaker transition remains auditable.

## 6. Architectural System Design Scenarios

### Scenario 1: Enterprise release engineer with coding and browser specialists

**Problem statement**: Design a release-management assistant for a multi-tenant SaaS platform that prepares pull requests, runs bounded validation, and verifies admin-console changes that do not have stable APIs. Peak load is `8k` requests/min during release windows. The platform needs `p99 <= 12s` for normal coding tasks, `p99 <= 25s` for UI verification branches, and fail-closed handling for deployment or production-setting mutations.

**Proposed architecture**:

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Developer -> Release UI -> API Gateway -> Coordinator                      │
│                                         │                                  │
│                                         ├─ Coding Agent -> repo/shell/tests │
│                                         ├─ Browser Agent -> admin UI        │
│                                         ├─ Policy Gate -> approvals/RBAC    │
│                                         └─ Synthesizer                      │
│                                                                            │
│ Persistence: workflow events + checkpoints + artifact store               │
│ Security: isolated browser VM + sandboxed code execution + deploy approval │
│ Telemetry: traces, token ledger, mutation audit, degraded-branch flags    │
└────────────────────────────────────────────────────────────────────────────┘
```

Technology choices:

- bounded `coding` specialist for repository mutation and tests
- isolated `browser` specialist only for API-less verification paths
- workflow engine for resumable validation and approval pauses
- immutable audit trail for every edit, click, and deploy decision

**Trade-off evaluation matrix**:

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Single generalist with all tools | Lowest initial prompt overhead | Best p50, worst p99 under tool sprawl | Lowest initially | Weakest least-privilege boundary | Low once tool count grows |
| Coordinator with coding plus browser specialists | Moderate | Better p95/p99 via scoped context and bounded loops | Moderate | Strong approval and blast-radius control | High for internal engineering workflows |
| Browser-first automation for all release steps | Highest | Slowest due to sequential UI dependence | High | Weakest operational reliability despite isolation | Low because UI paths do not parallelize well |

**Decision rationale**: Choose `coordinator with coding plus browser specialists`. The coding path handles the common case cheaply and safely, while the browser path is invoked only for the minority of tasks that truly require visual verification. A generalist is cheaper at tiny scale but becomes unsafe as mutation authority and tool count expand. A browser-first design inherits the highest token overhead and the weakest resilience characteristics from the source set (`03-tool-use.md`, `09-multi-agent-systems.md`, `11-specialized-agents.md`).

### Scenario 2: Strategy research desk with research and data specialists

**Problem statement**: Design an enterprise analyst assistant that answers board-level market questions using internal documents, external research, spreadsheets, and forecasting models. The system must support `25k` analytical requests/day, keep `p95 <= 8s` for evidence synthesis, `p99 <= 14s` for data-backed summaries, preserve source citations, and prevent raw sensitive exports from leaking into model prompts.

**Proposed architecture**:

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Analyst -> Insights Portal -> Coordinator                                  │
│                                │                                           │
│                                ├─ Research Agent -> query planner/reranker  │
│                                ├─ Data Agent -> compute/artifacts/sql       │
│                                ├─ Policy Layer -> corpus ACLs/PII filters   │
│                                └─ Final Synthesizer                         │
│                                                                            │
│ Storage: vector index + artifact store + workflow checkpoints             │
│ Security: permission-aware retrieval + redaction pipeline + immutable log  │
│ Resilience: replayable workflow + branch deadlines + DLQ for failed jobs   │
└────────────────────────────────────────────────────────────────────────────┘
```

Technology choices:

- `research` specialist for decomposition, retrieval, reranking, and citation preservation
- `data` specialist for spreadsheet analysis, forecasts, and artifact generation outside the prompt
- permission-aware retrieval and artifact storage instead of large transcript replay
- workflow checkpoints between planning, retrieval, computation, and synthesis

**Trade-off evaluation matrix**:

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
| --- | --- | --- | --- | --- | --- |
| Single LLM with full document dumps | High variable token cost | Unstable due to long prompts | Low initially | Weak provenance and overexposure risk | Low once corpora and files grow |
| Research plus data specialists under one coordinator | Moderate and predictable | Strong p95 because retrieval and compute are bounded separately | Moderate | Strong provenance, ACLs, and artifact isolation | High for enterprise analysis workloads |
| Separate remote expert agents per source system | Highest | Highest due to transport and auth hops | Highest | Strong domain isolation, weaker operational simplicity | Highest organizational autonomy, hardest to run |

**Decision rationale**: Choose `research plus data specialists under one coordinator`. This design matches the actual workload split: one branch gathers and verifies evidence, and the other performs bounded computation over artifacts. A single long-context model can appear simpler but pays heavily in token cost, weaker provenance, and prompt-injection exposure. A remote mesh is justified only when organization or trust boundaries dominate over latency and ops simplicity (`06-rag.md`, `07-memory.md`, `08-planning-reasoning.md`, `11-specialized-agents.md`).

## Sources

- `research_cursor/research/03-tool-use.md`
- `research_cursor/research/04-agent-architecture.md`
- `research_cursor/research/05-agent-frameworks.md`
- `research_cursor/research/06-rag.md`
- `research_cursor/research/07-memory.md`
- `research_cursor/research/08-planning-reasoning.md`
- `research_cursor/research/09-multi-agent-systems.md`
- `research_cursor/research/10-mcp-interoperability.md`
- `research_cursor/research/11-specialized-agents.md`
