# 08 - Planning and Reasoning

**Scope:** Decomposition, reflection, verification, and replanning.  
**Study goal:** Turn model reasoning into a bounded, observable control process whose plans, effects, and stopping decisions can be independently validated.

Reasoning is a capability that proposes conclusions or actions. Planning is a public control artifact: steps, dependencies, preconditions, expected effects, budgets, checks, and stop conditions. Hidden chain-of-thought is neither an execution contract nor an audit record.

## 1. System Topology & Data Flow

### Reference topology

```text
                                   CONTROL PLANE
┌──────────────────────────────────────────────────────────────────────────────┐
│ Identity/RBAC │ goal/constraints │ tool/verifier registry │ budgets/policy  │
│ model/prompt/plan schema versions │ approval rules │ deployment/eval gates │
└──────────────────┬───────────────────────────────────────┬───────────────────┘
                   │ immutable run envelope                │ policy decisions
                   ▼                                       ▼
                              ORCHESTRATION DATA PLANE
┌──────────────┐ admit      ┌──────────────┐ propose     ┌──────────────────┐
│ API/user     ├───────────►│ Durable run  ├────────────►│ Decomposer /    │
│ approval     │◄─status────┤ controller   │             │ planner         │
└──────────────┘            └──────┬───────┘             └────────┬─────────┘
                                   │ validate/install              │ candidate plan
                                   ▼                               ▼
                            ┌──────────────────────────────────────────┐
                            │ Schema │ DAG/coverage │ policy │ budgets │
                            └───────────────────┬──────────────────────┘
                                                │ ready leased steps
                                                ▼
                                   EXECUTION / TOOL PLANE
┌──────────────────────────────────────────────────────────────────────────────┐
│ Tool/MCP gateway │ API/browser/code/solver workers │ approval │ sandbox     │
└────────────────────────────────────┬─────────────────────────────────────────┘
                                     │ observations/effect receipts
                                     ▼
                           ┌──────────────────────┐
                           │ Verifier registry   │
                           │ policy/test/readback│
                           └───────┬──────────────┘
                       pass ───────┤──────── fail/material change
                                  │              ▼
                                  │      ┌──────────────────┐
                                  │      │ Evidence-grounded│
                                  │      │ reflection       │
                                  │      └────────┬─────────┘
                                  │               │ bounded repair request
                                  └───────────────▼
                                           replan generation g+1

                                PERSISTENCE LAYER
┌──────────────────────────────────────────────────────────────────────────────┐
│ Run/plan/step DB │ append-only events │ effect ledger │ approvals/artifacts │
│ checkpoint/outbox/queue │ verifier evidence │ DLQ/repair │ immutable audit  │
└────────────────────────────────────┬─────────────────────────────────────────┘
                                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ TELEMETRY: OTel │ plan/step trajectories │ quality/cost/tails │ WORM/SIEM   │
└──────────────────────────────────────────────────────────────────────────────┘
```

Models may propose transitions; ordinary code owns identity, goal, immutable constraints, state versions, budgets, permissions, approvals, terminal states, and external effects. The verifier need not share the executor's privileges.

### End-to-end control flow

1. The gateway authenticates tenant and principal, normalizes the goal and acceptance criteria, captures immutable constraints, assigns budgets, and pins policy, tool, model, prompt, plan-schema, and verifier versions.
2. A transactional run record and outbox event commit before scheduling. The planner receives a minimum public state projection, not credentials or unrestricted transcripts.
3. Decomposition returns a typed DAG. Deterministic validation checks schema, goal/criterion coverage, unique step IDs, acyclicity, dependency dataflow, known executable leaves, risk class, compensation, and global limits.
4. The controller installs generation `g` with compare-and-swap. Workers lease only ready steps whose dependencies completed and whose current preconditions still hold.
5. Before each action, the tool/MCP gateway validates arguments, authorizes the concrete resource under current policy, obtains exact-command approval when required, records effect intent/idempotency key, and dispatches into an appropriate sandbox.
6. The result and provider receipt commit. An independent verifier reads authoritative state, runs policy/tests/solver/sensor checks, and determines whether expected effects occurred.
7. A passing step becomes complete. A transient dependency failure retries within the shared deadline. A material verifier failure produces an evidence-linked structured reflection; repeated or non-improving critique stops.
8. Replanning fences affected old workers, reconciles in-flight outcomes, preserves valid completed artifacts, repairs the smallest invalid suffix/subgraph, validates it like the original plan, and atomically publishes `g+1` without resetting global budgets.
9. Final success requires the original goal and every immutable constraint to pass an authoritative oracle. Otherwise the run ends `BLOCKED`, `BUDGET_EXCEEDED`, `POLICY_DENIED`, `FAILED`, or `CANCELLED` with evidence.
10. OTel and immutable audit retain public plans, concise decisions, approvals, effects, verifier evidence, plan diffs, versions, cost, and terminal reason. Private reasoning is not required.

## 2. Core Mechanics & Algorithms

### 2.1 Decomposition

Decomposition creates units that can be scheduled, verified, retried, delegated, or compensated. Select the simplest structure that matches the task:

| Pattern | Control shape | Best fit | Main failure |
|---|---|---|---|
| Prompt-local steps | one response | bounded analysis without effects | missing dependencies remain implicit |
| Least-to-most | sequential subproblems | compositional dependency chains | early error propagation |
| Plan then execute | reviewed list/DAG | known business constraints | stale plan after observations |
| Orchestrator-workers | dynamic DAG/fan-out/join | unknown independent subtasks | duplicate/conflicting work |
| Tree/search | generate, score, prune, backtrack | ambiguous tasks with cheap oracle | exponential candidate cost/evaluator bias |
| Formal planner/solver | model translates, solver owns feasibility | scheduling/routing/constraints | wrong or incomplete formalization |

For a DAG `G=(V,E)`, cycle detection and topological scheduling are `O(|V|+|E|)`. Critical-path length, not step count, sets ideal parallel latency. Naive tree search with branching `b` and depth `d` evaluates `(b^(d+1)-1)/(b-1)` nodes; beam width, depth, cost, and oracle thresholds must prune it.

**Decomposition invariants**

- Every acceptance criterion maps to a step and final oracle.
- Every dependency exists; the graph is acyclic; required artifacts precede consumers.
- Every leaf maps to a registered tool, service, human, model, or solver.
- Effect boundaries align with verification, approval, retry, and compensation boundaries.
- Depth, width, candidates, model/tool calls, time, currency, and no-progress are globally bounded.

Avoid planning when one deterministic API or one bounded generation suffices. Decomposition has coordination cost and creates more failure/recovery state.

### 2.2 Reflection

Reflection is feedback-driven revision, not a generic request to “think again.”

- **Candidate critique** checks a draft against stable criteria before execution.
- **Execution reflection** diagnoses a failed verifier or changed environment and proposes a local repair.
- **Episodic reflection** records a post-run lesson for later work, with lower authority than raw outcomes.

A safe critique contract contains `failed_criteria`, trusted `evidence_refs`, `proposed_changes`, `unresolved_questions`, confidence, and a fingerprint. The controller preserves the best independently scored candidate, limits iterations, requires minimum measurable oracle improvement, and stops on a repeated fingerprint. Reflection cannot redefine success, overwrite tool output, change budgets, or become policy.

Self-critique without external feedback can regress a correct result or repeat the generator's blind spot. Prefer compiler/test output, search evidence, solver unsat cores, policy violations, sensors, or human feedback. Independence may come from mechanism, evidence, model, prompt, or owner; repeated same-model text is not proof.

### 2.3 Verification

Verification asks whether a specific property holds:

| Layer | Property | Strongest practical oracle |
|---|---|---|
| Request | identity, goal, constraints, ambiguity | authenticated request, policy, clarification |
| Plan | coverage, acyclicity, executability, feasibility | schema/DAG checks, registry, solver, reviewer |
| Pre-action | current preconditions and authority | current state, policy engine, exact approval |
| Step result | expected state transition | authoritative API/DB readback, tests, sensor, receipt |
| Final | original goal and all constraints | independent end-state evaluator |

Agreement and self-consistency rank candidates but do not prove correctness because samples can share one false premise. A learned verifier can help triage, but deterministic business rules and executable checks precede it for high-impact work. A formal proof applies only to correctly formalized properties.

Maintain a verifier registry with owner, version, checked properties, inputs, calibration, false-accept/reject limits, side effects, and blind spots. Bind verification to current state and plan generation; a check against a stale snapshot is not evidence for the present action.

### 2.4 Replanning

Replan on a material event, not every token or successful step:

- a precondition becomes false or a required input disappears;
- observed effects differ from expected effects;
- a non-transient business error or verifier rejection occurs;
- an authenticated goal/constraint changes;
- a dependency/policy changes or assumption expires;
- time, cost, risk, or depth crosses a threshold.

Classify the event as transient, plan defect, changed goal, policy block, irreversible conflict, or unknown. Retry a transient error; repair the affected suffix/subgraph for a plan defect; create a new run/goal lineage for a changed goal; pause/escalate for policy or irreversible conflict. Preserve completed work only when artifact hash, preconditions, authorization, and semantics remain valid.

```text
OBSERVE ─► AUTHENTICATE/NORMALIZE ─► CLASSIFY
                                         │
             ┌───────────────┬───────────┼──────────────┐
             ▼               ▼           ▼              ▼
          RETRY         LOCAL REPAIR   NEW GOAL      BLOCK/ESCALATE
             │               │
             └───────────────┴──► VERIFY PLAN g+1 ─► RESUME
```

Replan convergence requires monotonic global budget consumption, a maximum generation count, materiality/hysteresis, repeated-plan fingerprints, and terminal states. Without those, the controller can thrash between equally plausible plans or reset its budget each generation.

### 2.5 Controller state machine and stopping rules

```text
CREATED ─► PLANNING ─► PLAN_VALIDATION ─► EXECUTING ─► VERIFYING
              ▲               │                  │            │
              │               └─reject───────────┤            ├─pass─► next/final
              │                                  │            │
              └──────── REFLECTING ◄─────────────┴─fail───────┘
                           │
                           ├─repairable/budget─► REPLANNING
                           └─no repair/budget─► BLOCKED

Any active state ─► SUCCEEDED | FAILED | POLICY_DENIED | BUDGET_EXCEEDED | CANCELLED
```

Stop when the final oracle passes, an immutable constraint makes the goal infeasible, approval is rejected/expired, the same plan/critique repeats, improvement is below threshold, or any call/tool/time/cost/depth/generation budget is exhausted. A model's “done” text is not a terminal condition.

**Global invariants**

- Authenticated goal, tenant, constraints, policy, and cumulative budgets cannot be changed by plan or reflection text.
- Only one fenced current plan generation schedules work; late old-generation results are recorded and reconciled, never silently applied.
- Every effect has intent, stable logical idempotency key, outcome/unknown state, receipt, and verification.
- Completed steps never rerun unless explicit replay policy proves idempotence or compensation/re-execution is authorized.
- Success means the independent final oracle passes; a fluent answer or exhausted loop does not count.
- Replanning preserves lineage and cannot reset attempts, currency, approvals, or points of no return.

## 3. Token Economics & NFR Analysis

### 3.1 Cost per 1,000 runs

```text
C_1000 = Σ(U·P_in + H·P_cache + W·P_write + O·P_out)/1,000,000
       + tools/solvers/sandboxes + durable state/trace + human review

cost_per_verified_success = total end-to-end cost /
                            runs passing an independent final oracle
```

**Illustrative point-in-time assumptions, 2026-08-21:** across 1,000 production-shaped runs, decomposition, execution, reflection, verification, and replanning consume 15M uncached input tokens, 20M cached stable-prefix reads, 100,000 cache-write tokens, and 4M output tokens. Tools, solvers, sandboxes, checkpoints, and allocated control-plane infrastructure cost `$8/1K runs`; human approval is excluded. Rates use the [current pricing reference](https://developers.openai.com/api/docs/pricing).

| Model tier | No prompt cache / 1K | Cached model cost / 1K | Total with $8 external |
|---|---:|---:|---:|
| `gpt-5.6-sol` | `(35M×$5)+(4M×$30)` = **$295.00** | `$75+$10+$0.63+$120` = **$205.63** | **$213.63** |
| `gpt-5.6-terra` | `(35M×$2)+(4M×$12)` = **$118.00** | `$30+$4+$0.25+$48` = **$82.25** | **$90.25** |
| `gpt-5.6-luna` | `(35M×$0.20)+(4M×$1.20)` = **$11.80** | `$3+$0.40+$0.03+$4.80` = **$8.23** | **$16.23** |

Cache only stable instructions, schemas, tool definitions, and verifier criteria that satisfy the provider's exact-prefix contract. Current observations, approval, tenant state, and plan generation remain uncached request data. Key result caches by goal/constraint digest, tool/verifier versions, state snapshot, and policy; an environment change invalidates it.

A direct `terra` call with 3,000 input and 600 output tokens costs `(3M×$2)+(0.6M×$12) = $13.20/1K`. One extra reflection call with 2,000 input and 400 output costs `$8.80/1K`. Generate five self-consistency candidates and generation cost is roughly 5x before batching/caching. A full tree at `b=3,d=4` contains `121` nodes; at `$0.0088` per candidate call it costs about `$1.0648` per run or `$1,064.80/1K` before tools. Search must buy measurable oracle improvement.

If the verified-success rate is 80%, the illustrative cached `terra` system's `$90.25/1K attempted` becomes `$90.25/800×1,000 = $112.81/1K verified successes`, before human repair. Report failed branches, compensations, and approvals; cost per successful final text hides unsafe false success.

### 3.2 Latency SLOs

```text
T_success = T_plan + Σcritical_path(T_step + T_verify)
          + Σretry_backoff + Σreflection/replan + T_approval
```

The following are internal design targets, not public model benchmarks:

| Workload | p50 | p95 | p99 | Tail mitigation |
|---|---:|---:|---:|---|
| One bounded plan + deterministic verify | ≤ 1.5 s | ≤ 5 s | ≤ 10 s | schema cache, bounded output, warm verifier |
| Eight-step DAG, four-way parallel | ≤ 5 s | ≤ 20 s | ≤ 45 s | ready-step parallelism, deadline propagation, artifact reuse |
| One reflection + local suffix replan | ≤ 8 s | ≤ 35 s | ≤ 75 s | external feedback, one repair, no-progress stop |
| Formal solver-backed plan | ≤ 3 s | ≤ 15 s | ≤ 40 s | model only translates; solver timeout/unsat evidence |
| Human-approved pivot | machine ≤ 4 s | machine ≤ 15 s | machine ≤ 30 s | report human wait separately; expiry/status webhook |

Measure time to first useful artifact and independently verified terminal state, plus queue, planner, tool, verifier, solver, approval, reflection, and recovery spans. Track plan depth/width/critical path, work reused after replan, cancellations, and budget exhaustion. Streaming improves perceived latency but not verified completion.

### 3.3 Throughput and back-pressure

At 100 admitted runs/s with 2.5 mean model calls, six tool calls, eight verifier executions, 12 checkpoints, and 10 seconds mean machine duration:

```text
model calls/s       = 100×2.5 = 250
tool calls/s        = 100×6 = 600
verifier runs/s     = 100×8 = 800
checkpoint writes/s = 100×12 = 1,200
active runs         = 100×10 = 1,000
```

A four-way DAG stage can create 400 branch starts/s even at 100 runs/s. Size by critical dependency, p99 payload, retries, and replan fan-out. Use weighted admission by predicted model calls, tree/DAG width, tool risk/cost, verifier/sandbox CPU, checkpoints, and human-review load.

Separate interactive, batch/search, mutation, solver, verifier, approval, compensation, and telemetry queues. Reserve capacity for status/cancel/approval/compensation. Cap per-tenant parallelism and global search nodes; propagate cancellation/deadlines and use bounded stream buffers. Shed extra candidates, optional reflection, speculative branches, and verbose traces before effect reconciliation or verification. Never let each orchestration layer retry independently.

### 3.4 NFR scorecard

| NFR | Target/evidence | Trade-off |
|---|---|---|
| Availability | 99.9% run plane; 99.99% status/cancel/approval/effect ledger | Durable control services cost more than request-local loops. |
| RPO | 0 goal/constraints, plan generations, effects, approvals, budget, audit; ≤ 5 min aggregate telemetry | More checkpoints add write latency/storage. |
| RTO | ≤ 15 min controller/effect plane; ≤ 60 min analytics | Version pinning, replay tests, and repair UI are required. |
| Reliability | verified success, false-success, `pass^k`, duplicate-effect rate, recovery success | More verification may reject valid unusual solutions. |
| Performance | p50/p95/p99 verified terminal, queue/recovery/approval spans | Parallelism lowers critical path but raises tokens/quota. |
| Security | zero unauthorized effects/goal escalation in adversarial suite | Least privilege and exact approval constrain autonomy. |
| Compliance | explainable public plan, evidence/decision lineage, retention/residency, human accountability | Raw chain-of-thought is neither required nor desirable. |
| Operability | stuck-run/DLQ repair, generation rollback, verifier registry, failure injection | Rich state increases schema/migration burden. |

No vendor-neutral benchmark jointly normalizes constraint satisfaction, reflection improvement, verifier false accepts, replan recovery, token/tool cost, p50/p95/p99, and human minutes. Use formal, interactive, coding, research, and domain-specific test families plus production-shaped perturbations.

## 4. Distributed Resilience & Security

### 4.1 Durable workflow and plan generations

```text
┌──────────────┐ tx/outbox  ┌──────────────┐ schedule  ┌──────────────┐
│ Run/plan DB  ├───────────►│ Kafka/Temporal├─────────►│ Fenced worker│
│ generation g │◄─events────┤ queues        │          │ ready step   │
└──────┬───────┘            └──────────────┘          └──────┬───────┘
       │                                                      │ intent/key
       ▼                                                      ▼
┌──────────────┐ receipt    ┌──────────────┐ execute   ┌──────────────┐
│ Effect ledger│◄───────────┤ Tool gateway ├──────────►│ External API │
│ unknown/result│           │ auth/approval│           │ idempotent   │
└──────┬───────┘            └──────────────┘           └──────┬───────┘
       │ authoritative verification                           │ readback
       └──────────────────────────────────────────────────────┘
```

Persist run, plan generation/parent/reason, steps/dependencies/status/lease/key, append-only events, effects, approvals, artifacts, cumulative budget, and verifier evidence. Temporal, Kafka consumers, or equivalent may redeliver; deterministic event/effect IDs and monotonic transitions turn replay into deduplicated upsert. Poison events go to a DLQ after bounded attempts with operator repair state.

Install replan `g+1` by CAS only if `g` is current. Stop leasing affected old steps, fence workers by run/generation lease epoch, request cancellation, classify in-flight outcomes, snapshot current external state, validate the repair, and publish atomically. Late results remain audit events and require reconciliation; they cannot advance `g+1` silently.

### 4.2 Effects, Saga, and recovery

A timeout means outcome unknown. Before dispatch, commit canonical command/actor/policy/plan generation and a stable idempotency key based on logical step, not attempt. After timeout, query the provider by operation/key before retrying. Commit result and receipt, then verify authoritative postconditions.

Classify each effect as read-only, idempotent write, compensable write, irreversible pivot, or manual recovery. A Saga persists compensation as a workflow that can itself fail. Approval precedes the pivot and is bound to exact arguments, state version, policy, and plan generation. Replanning never pretends an irreversible effect disappeared.

Use per-dependency breakers, full-jitter exponential backoff, aggregate deadlines, and one retry owner. Transient failures retry; permanent schema/business errors repair or block; policy failures fail closed; ambiguous effects reconcile. Checkpoint after plan install, effect intent/result, verification, approval, and replan publication.

Recovery drills crash before/after every tool, during approval, during generation CAS, and during compensation. Verify no duplicate effect, unchanged goal/policy/budget, proper old-worker fencing, and a terminal outcome. Back up run/effect/approval/event state, version registry, artifacts, and audit; export queue high-water marks needed for replay.

### 4.3 Zero-Trust MCP and plan hijacking

```text
┌──────────────┐ proposal   ┌────────────────┐ mTLS/OAuth ┌──────────────┐
│ Planner/step ├───────────►│ Policy/tool/MCP├───────────►│ Tool server  │
│ untrusted I/O│            │ gateway        │            │ /sandbox     │
└──────┬───────┘            └───────┬────────┘            └──────┬───────┘
       │ observation data            │ trusted identity            │ scoped token
       ▼                             ▼                             ▼
┌──────────────┐             ┌──────────────┐              ┌──────────────┐
│ Origin labels│             │ Goal/policy  │              │ API/code/web │
│ + delimiter  │             │ approval log │              │ data plane   │
└──────────────┘             └──────────────┘              └──────────────┘
```

Authenticate caller and MCP server, allowlist tools per controller state, authorize `(actor, action, resource, arguments, conditions)` at dispatch, mint short-lived audience credentials, and sandbox code/browser with bounded filesystem/network. Tool-level RBAC separates read, draft, mutate, approve, verify, and compensate; the verifier does not inherit mutation permission.

Keep goal, tenant, immutable constraints, budgets, and policy in server-owned fields. Treat user, retrieved, tool, memory, peer-agent, and reflection content as untrusted observations. Diff every replan against goal, destinations, resources, privileges, approvals, risk, and budget. Fresh authorization is mandatory for changed arguments.

Prompt injection cannot add a tool, elevate scope, waive an approval, increase a budget, redefine the verifier, or convert evidence into instruction. Alert on goal reversal, abnormal depth/fan-out, repeated verifier bypass, unusual destinations, and privilege escalation.

### 4.4 PII, verification governance, and audit

The PII path is `classify -> detect -> redact/tokenize -> authorize purpose -> plan/model/tool -> rehydrate only at authorized boundary -> audit/delete`. Apply it to goals, observations, artifacts, plans, critiques, tool arguments/results, verifier evidence, traces, and eval datasets. Secrets never enter model-visible plans; sensitive artifact references are capability-bound.

Maintain a verifier registry with owner, checked properties, mechanism, version, evidence source, calibration, blind spots, and false-accept/reject thresholds. Validate both formal problem translation and solution. Sandbox executable tests, bind evidence to current state/generation, keep hidden/adversarial tests where appropriate, and require deterministic policy plus accountable human review for high-impact decisions.

An approval UI shows exact action/target, evidence, expected effect, risk, alternatives, compensation, uncertainty, and plan diff. Approval expires when arguments, state, policy, or plan generation changes.

Immutable audit records actor/tenant, goal/constraint digest, public plan/step generations, model/prompt/tool/policy/verifier versions, concise critique and evidence refs, authorization/approval, idempotency key, provider receipt, postcondition, compensation, tokens/cost/timing, and terminal reason. Redact content, hash-chain/sign WORM batches, and log access. Do not store unrestricted hidden reasoning as explanation.

## 5. Production Enterprise Code

This Python 3.11 standard-library example implements a versioned incident-remediation controller. It validates criterion coverage and DAG acyclicity, enforces global budgets and tool RBAC, executes idempotently, verifies against authoritative state, reflects only on external test evidence, repairs the invalid suffix while preserving a completed diagnostic, uses structured logs, full-jitter retries, closed/open/half-open breakers, and primary -> secondary -> deterministic `BLOCKED` planner fallback. Run with `python planning_controller.py`.

```python
from __future__ import annotations

import json
import hashlib
import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, Sequence


class TransientError(RuntimeError):
    """A retryable dependency failure."""


class PermanentError(RuntimeError):
    """A plan, policy, or state failure."""


class CircuitOpen(TransientError):
    """A dependency is temporarily disabled."""


class StepStatus(Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class RunStatus(Enum):
    ACTIVE = "active"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    BUDGET_EXCEEDED = "budget_exceeded"


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    roles: frozenset[str]


@dataclass
class Budget:
    max_model_calls: int = 8
    max_tool_calls: int = 8
    max_generations: int = 2
    model_calls: int = 0
    tool_calls: int = 0


@dataclass
class Step:
    step_id: str
    tool: str
    args: dict[str, str]
    depends_on: tuple[str, ...]
    covers: tuple[str, ...]
    expected: str
    status: StepStatus = StepStatus.PENDING


@dataclass
class Plan:
    generation: int
    goal: str
    steps: list[Step]
    reason: str


@dataclass
class Run:
    run_id: str
    trace_id: str
    goal: str
    required_criteria: frozenset[str]
    principal: Principal
    budget: Budget
    status: RunStatus = RunStatus.ACTIVE
    generation: int = 0
    plan: Plan | None = None
    replan_reason: str | None = None
    critique_fingerprints: set[str] = field(default_factory=set)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {"timestamp": time.time(), "level": record.levelname,
                 "message": record.getMessage()}
        for key in ("trace_id", "run_id", "generation", "step_id", "stage", "attempt"):
            if hasattr(record, key):
                value[key] = getattr(record, key)
        return json.dumps(value, separators=(",", ":"), sort_keys=True)


logger = logging.getLogger("planning")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


class Breaker:
    def __init__(self, threshold: int = 2, recovery_s: float = 5.0):
        self._threshold = threshold
        self._recovery_s = recovery_s
        self._failures = 0
        self._opened_at = 0.0
        self._probe = False
        self._state = "closed"
        self._lock = threading.Lock()

    def before(self) -> None:
        with self._lock:
            if self._state == "open":
                if time.monotonic() - self._opened_at < self._recovery_s:
                    raise CircuitOpen("circuit open")
                self._state = "half_open"
            if self._state == "half_open":
                if self._probe:
                    raise CircuitOpen("half-open probe busy")
                self._probe = True

    def success(self) -> None:
        with self._lock:
            self._state = "closed"
            self._failures = 0
            self._probe = False

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._probe = False
            if self._state == "half_open" or self._failures >= self._threshold:
                self._state = "open"
                self._opened_at = time.monotonic()


class PlannerModel(Protocol):
    name: str

    def propose(self, goal: str, generation: int, feedback: str | None,
                timeout_s: float) -> str: ...


class PlannerChain:
    def __init__(self, models: Sequence[PlannerModel]):
        if len(models) < 2:
            raise ValueError("primary and secondary planners required")
        self._models = tuple(models)
        self._breakers = {model.name: Breaker() for model in models}

    @staticmethod
    def parse(raw: str) -> Plan:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PermanentError("planner returned invalid JSON") from exc
        if not isinstance(value, dict) or set(value) != {
            "generation", "goal", "reason", "steps"
        }:
            raise PermanentError("plan violates exact schema")
        if (not isinstance(value["generation"], int)
                or not isinstance(value["goal"], str) or not value["goal"].strip()
                or not isinstance(value["reason"], str)):
            raise PermanentError("plan header has invalid types")
        if not isinstance(value["steps"], list) or not value["steps"]:
            raise PermanentError("plan needs steps")
        steps = []
        for item in value["steps"]:
            required = {"id", "tool", "args", "depends_on", "covers", "expected"}
            if not isinstance(item, dict) or set(item) != required:
                raise PermanentError("step violates exact schema")
            if (any(not isinstance(item[key], str) or not item[key]
                    for key in ("id", "tool", "expected"))
                    or not isinstance(item["args"], dict)
                    or not all(isinstance(k, str) and isinstance(v, str)
                               for k, v in item["args"].items())
                    or not isinstance(item["depends_on"], list)
                    or not all(isinstance(x, str) for x in item["depends_on"])
                    or not isinstance(item["covers"], list) or not item["covers"]
                    or not all(isinstance(x, str) for x in item["covers"])):
                raise PermanentError("step fields have invalid types")
            steps.append(Step(item["id"], item["tool"], item["args"],
                              tuple(item["depends_on"]), tuple(item["covers"]),
                              item["expected"]))
        return Plan(value["generation"], value["goal"], steps, value["reason"])

    def propose(self, run: Run, deadline: float) -> Plan | None:
        next_generation = run.generation + 1
        for model in self._models:
            breaker = self._breakers[model.name]
            for attempt in range(1, 3):
                if run.budget.model_calls >= run.budget.max_model_calls:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                try:
                    breaker.before()
                    run.budget.model_calls += 1
                    raw = model.propose(run.goal, next_generation,
                                        run.replan_reason, min(remaining, 3.0))
                    plan = self.parse(raw)
                    breaker.success()
                    return plan
                except CircuitOpen:
                    break
                except PermanentError:
                    breaker.failure()
                    break
                except (TimeoutError, ConnectionError, TransientError):
                    breaker.failure()
                    logger.warning("planner failure", extra={
                        "trace_id": run.trace_id, "run_id": run.run_id,
                        "generation": next_generation, "stage": model.name,
                        "attempt": attempt})
                    if attempt == 2:
                        break
                    delay = random.uniform(0.0, 0.02 * (2 ** (attempt - 1)))
                    if time.monotonic() + delay >= deadline:
                        return None
                    time.sleep(delay)
        return None


def validate_plan(run: Run, plan: Plan, allowed_tools: frozenset[str]) -> None:
    if plan.goal != run.goal or plan.generation != run.generation + 1:
        raise PermanentError("goal or generation changed")
    if len(plan.steps) > 10:
        raise PermanentError("plan exceeds step budget")
    ids = [step.step_id for step in plan.steps]
    if len(ids) != len(set(ids)):
        raise PermanentError("duplicate step ID")
    by_id = {step.step_id: step for step in plan.steps}
    for step in plan.steps:
        if step.tool not in allowed_tools or any(dep not in by_id for dep in step.depends_on):
            raise PermanentError("unknown tool or dependency")
    indegree = {step_id: 0 for step_id in ids}
    children = {step_id: [] for step_id in ids}
    for step in plan.steps:
        for dep in step.depends_on:
            indegree[step.step_id] += 1
            children[dep].append(step.step_id)
    ready = [step_id for step_id, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        current = ready.pop()
        visited += 1
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if visited != len(ids):
        raise PermanentError("plan contains a cycle")
    covered = frozenset(item for step in plan.steps for item in step.covers)
    if not run.required_criteria.issubset(covered):
        raise PermanentError("plan omits acceptance criteria")


class ToolGateway:
    def __init__(self):
        self.world = {"diagnosed": False, "patched": False, "tests_pass": False}
        self._ledger: dict[tuple[str, str], dict[str, object]] = {}
        self._breakers = {name: Breaker() for name in ("inspect", "patch", "test")}

    def execute(self, run: Run, step: Step, deadline: float) -> dict[str, object]:
        if run.budget.tool_calls >= run.budget.max_tool_calls:
            raise PermanentError("tool budget exhausted")
        if step.tool == "patch" and "deployer" not in run.principal.roles:
            raise PermanentError("RBAC denied patch")
        key = (run.run_id, step.step_id)
        if key in self._ledger:
            return self._ledger[key]
        breaker = self._breakers[step.tool]
        for attempt in range(1, 3):
            if time.monotonic() >= deadline:
                raise TimeoutError("tool deadline")
            try:
                breaker.before()
                run.budget.tool_calls += 1
                if step.tool == "inspect":
                    self.world["diagnosed"] = True
                    result = {"diagnosed": True}
                elif step.tool == "patch":
                    self.world["patched"] = step.args.get("variant") == "correct"
                    result = {"applied": True, "variant": step.args.get("variant")}
                else:
                    self.world["tests_pass"] = bool(self.world["patched"])
                    result = {"pass": self.world["tests_pass"]}
                breaker.success()
                self._ledger[key] = result
                return result
            except CircuitOpen:
                raise
            except (TimeoutError, ConnectionError, TransientError):
                breaker.failure()
                logger.warning("tool failure", extra={
                    "trace_id": run.trace_id, "run_id": run.run_id,
                    "generation": run.generation, "step_id": step.step_id,
                    "stage": step.tool, "attempt": attempt})
                if attempt == 2:
                    raise
                time.sleep(random.uniform(0.0, 0.02 * (2 ** (attempt - 1))))
        raise AssertionError("unreachable")


def verify(step: Step, result: dict[str, object], world: dict[str, bool]) -> bool:
    checks = {"diagnosed": bool(result.get("diagnosed") and world["diagnosed"]),
              "applied": bool(result.get("applied")),
              "pass": bool(result.get("pass") and world["tests_pass"])}
    return checks.get(step.expected, False)


def reflect(run: Run, step: Step, result: dict[str, object]) -> str | None:
    evidence = json.dumps({"step": step.step_id, "expected": step.expected,
                           "observed": result}, sort_keys=True)
    fingerprint = hashlib.sha256(evidence.encode()).hexdigest()
    if fingerprint in run.critique_fingerprints:
        return None
    run.critique_fingerprints.add(fingerprint)
    if step.tool == "test" and result.get("pass") is False:
        return "candidate_fix_failed: tests returned false; repair patch/test suffix"
    return None


def install_plan(run: Run, candidate: Plan) -> None:
    old = {step.step_id: step for step in run.plan.steps} if run.plan else {}
    for step in candidate.steps:
        prior = old.get(step.step_id)
        if (prior and prior.status is StepStatus.COMPLETED
                and prior.tool == step.tool and prior.args == step.args):
            step.status = StepStatus.COMPLETED
    run.plan = candidate
    run.generation = candidate.generation
    run.replan_reason = None


class Controller:
    ALLOWED_TOOLS = frozenset({"inspect", "patch", "test"})

    def __init__(self, planners: PlannerChain, tools: ToolGateway):
        self._planners = planners
        self._tools = tools

    def run(self, run: Run, timeout_s: float = 5.0) -> Run:
        deadline = time.monotonic() + timeout_s
        while run.status is RunStatus.ACTIVE:
            if (run.budget.model_calls >= run.budget.max_model_calls
                    or run.budget.tool_calls >= run.budget.max_tool_calls):
                run.status = RunStatus.BUDGET_EXCEEDED
                break
            if run.plan is None or run.replan_reason:
                if run.generation >= run.budget.max_generations:
                    run.status = RunStatus.BLOCKED
                    break
                candidate = self._planners.propose(run, deadline)
                if candidate is None:
                    run.status = RunStatus.BLOCKED
                    break
                try:
                    validate_plan(run, candidate, self.ALLOWED_TOOLS)
                except PermanentError:
                    run.status = RunStatus.BLOCKED
                    break
                install_plan(run, candidate)
                logger.info("plan installed", extra={
                    "trace_id": run.trace_id, "run_id": run.run_id,
                    "generation": run.generation, "stage": "plan"})

            assert run.plan is not None
            ready = [step for step in run.plan.steps
                     if step.status is StepStatus.PENDING
                     and all(next(item for item in run.plan.steps
                                  if item.step_id == dep).status is StepStatus.COMPLETED
                             for dep in step.depends_on)]
            if not ready:
                if all(step.status is StepStatus.COMPLETED for step in run.plan.steps):
                    run.status = (RunStatus.SUCCEEDED if self._tools.world["tests_pass"]
                                  else RunStatus.BLOCKED)
                else:
                    run.status = RunStatus.BLOCKED
                break

            step = ready[0]
            try:
                result = self._tools.execute(run, step, deadline)
            except (PermanentError, CircuitOpen, TimeoutError):
                run.status = RunStatus.BLOCKED
                break
            if verify(step, result, self._tools.world):
                step.status = StepStatus.COMPLETED
                continue
            step.status = StepStatus.FAILED
            critique = reflect(run, step, result)
            if critique is None:
                run.status = RunStatus.BLOCKED
            else:
                run.replan_reason = critique

        logger.info("run terminal", extra={
            "trace_id": run.trace_id, "run_id": run.run_id,
            "generation": run.generation, "stage": run.status.value})
        return run


class DemoPlanner:
    def __init__(self, name: str, available: bool):
        self.name = name
        self._available = available

    def propose(self, goal: str, generation: int, feedback: str | None,
                timeout_s: float) -> str:
        if not self._available or timeout_s <= 0:
            raise TimeoutError("planner unavailable")
        if feedback is None:
            steps = [
                {"id": "s1", "tool": "inspect", "args": {}, "depends_on": [],
                 "covers": ["diagnose"], "expected": "diagnosed"},
                {"id": "s2", "tool": "patch", "args": {"variant": "initial"},
                 "depends_on": ["s1"], "covers": ["change"], "expected": "applied"},
                {"id": "s3", "tool": "test", "args": {}, "depends_on": ["s2"],
                 "covers": ["verify"], "expected": "pass"},
            ]
            reason = "initial decomposition"
        else:
            steps = [
                {"id": "s1", "tool": "inspect", "args": {}, "depends_on": [],
                 "covers": ["diagnose"], "expected": "diagnosed"},
                {"id": "s2b", "tool": "patch", "args": {"variant": "correct"},
                 "depends_on": ["s1"], "covers": ["change"], "expected": "applied"},
                {"id": "s3b", "tool": "test", "args": {}, "depends_on": ["s2b"],
                 "covers": ["verify"], "expected": "pass"},
            ]
            reason = feedback
        return json.dumps({"generation": generation, "goal": goal,
                           "reason": reason, "steps": steps})


def main() -> None:
    run = Run(str(uuid.uuid4()), str(uuid.uuid4()), "make checkout tests pass",
              frozenset({"diagnose", "change", "verify"}),
              Principal("tenant-a", frozenset({"deployer"})), Budget())
    planners = PlannerChain((DemoPlanner("primary", False),
                             DemoPlanner("secondary", True)))
    result = Controller(planners, ToolGateway()).run(run)
    print(json.dumps({"status": result.status.value,
                      "generation": result.generation,
                      "model_calls": result.budget.model_calls,
                      "tool_calls": result.budget.tool_calls,
                      "steps": [{"id": step.step_id, "status": step.status.value}
                                for step in result.plan.steps] if result.plan else []},
                     separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
```

The demo's primary planner opens its breaker after bounded failures. The secondary decomposes an initial plan; authoritative tests reject the first patch, structured reflection cites that evidence, and generation 2 reuses completed `s1` while repairing only the patch/test suffix. If both planners fail, the deterministic fallback is `BLOCKED`; no unvalidated action is synthesized. Changing the principal role makes the patch fail closed at the tool gateway.

## 6. Architectural System Design Scenarios

### Scenario 1 - Regulated claims recommendation

**Problem statement.** Design a claims workflow for 500 cases/s. It must collect required evidence, evaluate versioned eligibility rules, prepare a recommendation, obtain accountable review for adverse/high-value outcomes, preserve seven-year lineage, maintain RPO 0 for case/effect/approval state, and keep machine p99 under 25 seconds excluding human wait.

**Proposed architecture.** Use a deterministic Temporal workflow for identity, document checklist, deadlines, calculations, policy rules, approvals, and terminal states. A bounded model node decomposes variable document review into parallel extraction tasks and reflects only on missing/contradictory evidence. PostgreSQL stores case/run/effect state; object storage holds evidence by digest; a versioned rules engine and authoritative claims system verify every recommendation. New authenticated evidence triggers repair of the affected review suffix. The model cannot alter eligibility rules or issue payment.

```text
┌──────────────┐ auth/evidence ┌──────────────┐ schedule ┌──────────────┐
│ Claimant/ops ├──────────────►│ Case API +   ├────────►│ Temporal     │
│ reviewer     │◄─status───────┤ run/effect DB│         │ deterministic│
└──────────────┘               └──────┬───────┘         └──────┬───────┘
                                      │                         │ variable review
                                      ▼                         ▼
                               ┌──────────────┐          ┌──────────────┐
                               │ Evidence     │          │ Model DAG    │
                               │ object/WORM  │          │ extract/check│
                               └──────────────┘          └──────┬───────┘
                                                               │ recommendation
                                                               ▼
                                                        ┌──────────────┐
                                                        │ Rules/readback│
                                                        │ + approval   │
                                                        └──────────────┘
```

At 500 cases/s, if 30% require four parallel document tasks, that path adds 600 task starts/s; six deterministic steps/case add 3,000 step executions/s before retries. Separate review, rule, approval, and payment queues. Missing model capacity delays variable extraction but never bypasses the document checklist or rules.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security/compliance | Scalability ceiling |
|---|---|---|---|---|---|
| **Deterministic workflow + bounded model review DAG** | Medium | Predictable rules; parallel document tail | High two-layer design | Strong explicit rules, approval, lineage | High with partitioned workers |
| Fully dynamic reasoning agent | Variable/high retries | Unpredictable p99 | Medium demo, high production repair | Weak rule/goal-drift boundary | Quota and trajectory limited |
| Formal rules only, manual document review | High human cost | Slow review queue | Low model complexity | Strongest deterministic boundary | Human throughput limited |

**Decision rationale.** The known regulated process belongs in deterministic workflow and rules; only document interpretation benefits from dynamic decomposition. Verification is authoritative and human accountability remains at decision boundaries. This minimizes model authority while gaining parallel review throughput.

### Scenario 2 - Production incident remediation

**Problem statement.** Design an incident agent for 50 simultaneous priority incidents across 2,000 services. It must gather read-only diagnostics in parallel, propose/test a patch, stage a 10% canary, recover from changed symptoms, keep machine p95 under 10 minutes, achieve RPO 0 for changes/approvals, and never mutate production without exact approval and rollback evidence.

**Proposed architecture.** Use a durable DAG controller with read-only diagnostic workers, a bounded planner, evidence-grounded reflection, and suffix replanning. Code runs in ephemeral egress-restricted sandboxes. Verification layers include type/unit/integration/regression/security tests, diff policy, signed artifacts, canary telemetry, and authoritative deployment readback. PostgreSQL/effect ledger and Temporal preserve plan generations, commands, approvals, receipts, and compensation. A failed test repairs the code subgraph; materially changed production symptoms replan diagnosis; canary failure executes tested rollback.

```text
┌──────────────┐ incident/auth ┌──────────────┐ fan-out   ┌──────────────┐
│ Engineer/oncall├────────────►│ Durable DAG  ├──────────►│ Read-only    │
│ exact approval│◄─status/diff─┤ controller   │◄─evidence┤ diagnostics  │
└──────────────┘               └──────┬───────┘          └──────────────┘
                                      │ patch candidate
                                      ▼
                               ┌──────────────┐ verify   ┌──────────────┐
                               │ Code sandbox├─────────►│ Tests/scans  │
                               └──────┬───────┘          └──────┬───────┘
                                      │ approved artifact        │ feedback/replan
                                      ▼                          │
                               ┌──────────────┐ canary/readback   │
                               │ Deploy proxy├───────────────────┘
                               │ + rollback  │
                               └──────────────┘
```

With 50 incidents, eight read-only branches each create up to 400 diagnostic tasks. Cap per-service concurrency and reserve deploy/rollback/verifier capacity. Old-generation workers are fenced after replan. A planner outage preserves diagnostics and returns an operator-ready evidence bundle; a telemetry or policy outage blocks canary progression.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
|---|---|---|---|---|---|
| Static runbook workflow | Low model cost | Fast for known failure | Medium runbook upkeep | Strong bounded actions | Limited novel diagnosis |
| ReAct-style single loop | Medium-variable | Serial and variable tail | Low initial, hard replay | Broad loop authority risk | Limited by serial context/tool loop |
| **DAG + verified suffix replanning** | Higher control/state cost | Parallel diagnosis, bounded repair | High | Strong fencing, approvals, oracles | High with queue/worker partitions |

**Decision rationale.** Incidents require variable evidence acquisition and repair, so a static workflow alone is insufficient. The DAG exposes parallelism and explicit effects; suffix replanning retains useful diagnostics and avoids repeated changes. Independent tests, canary readback, approval, and rollback remain stronger than model reflection.

## Interview Review

1. **Planning versus reasoning?** Planning is an executable public artifact; reasoning is a capability that may propose it.
2. **When should work be decomposed?** When separate scheduling, verification, approval, recovery, or parallelism is valuable enough to pay coordination cost.
3. **What makes reflection useful?** Stable criteria plus external evidence and measurable oracle improvement, with iteration/no-progress limits.
4. **What verifies an agent?** The strongest independent mechanism available: policy, solver, tests, authoritative readback, sensor, or accountable human.
5. **When should the system replan?** On material failed preconditions/effects, changed authenticated constraints, policy, or budget—not every successful step.
6. **How does replanning remain safe?** Fence old work, reconcile effects, preserve only valid artifacts, CAS generation `g+1`, revalidate, and retain global budgets.

## Primary References

- [Chain-of-Thought Prompting](https://arxiv.org/abs/2201.11903)
- [Least-to-Most Prompting](https://arxiv.org/abs/2205.10625)
- [Plan-and-Solve](https://arxiv.org/abs/2305.04091)
- [Tree of Thoughts](https://arxiv.org/abs/2305.10601)
- [LLM+P](https://arxiv.org/abs/2304.11477)
- [LLM-Modulo](https://arxiv.org/abs/2402.01817)
- [Self-Refine](https://arxiv.org/abs/2303.17651)
- [Reflexion](https://arxiv.org/abs/2303.11366)
- [CRITIC](https://arxiv.org/abs/2305.11738)
- [Training Verifiers](https://arxiv.org/abs/2110.14168)
- [Self-Consistency](https://arxiv.org/abs/2203.11171)
- [Program-Aided Language Models](https://arxiv.org/abs/2211.10435)
- [Chain-of-Verification](https://arxiv.org/abs/2309.11495)
- [ReAct](https://arxiv.org/abs/2210.03629)
- [PlanBench](https://arxiv.org/abs/2206.10498)
- [Tau-bench](https://arxiv.org/abs/2406.12045)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [Saga pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/saga)
- [OWASP excessive agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)
- [Indirect prompt injection](https://arxiv.org/abs/2302.12173)
- [NIST AI RMF](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10)
