# 12 - Agent Evaluation

**Scope:** Task success, trajectory, tool accuracy, quality, cost, and latency.
**Study goal:** Build a governed measurement system for a model + harness + tools + environment + grader, with estimands and uncertainty tied to a release decision.

A task is a test case, a trial is one stochastic attempt, a trajectory is the interaction sequence, and the outcome is the final environment state. “The model scored 80%” is incomplete until the suite, task population, agent scaffold, environment, resources, attempts, exclusions, grader, and estimator are named.

## 1. System Topology & Data Flow

### Evaluation platform topology

```text
                                    CONTROL PLANE
┌──────────────────────────────────────────────────────────────────────────────┐
│ Objective/estimand │ suite/dataset registry │ policy/PII │ budget │ release │
│ model/harness/tool/environment/grader versions │ sampling/statistics plan    │
└─────────────┬──────────────────────────┬───────────────────────────┬─────────┘
              │ signed manifest          │ candidate matrix          │ gate
              ▼                          ▼                           ▼
┌──────────────┐                ┌──────────────────┐        ┌──────────────┐
│ Suite store  │───────────────►│ Experiment       │───────►│ Release API  │
│ tasks/seeds  │                │ controller       │        │ signed result│
└──────┬───────┘                └────────┬─────────┘        └──────────────┘
       │                                 │ durable work
       │                     DATA PLANE  ▼
       │                       ┌──────────────────┐
       └──────────────────────►│ Queue/scheduler  │
                               └─────┬──────┬─────┘
                                     │      │
                      ┌──────────────┘      └──────────────┐
                      ▼                                    ▼
              ┌──────────────┐                      ┌──────────────┐
              │ Agent runner │ model/tool proxy     │ Environment  │
              │ harness      ├─────────────────────►│ DB/web/repo  │
              └──────┬───────┘◄─state/tool result──┤ sandbox/sim  │
                     │                              └──────────────┘
                     │ immutable trajectory/outcome
                     ▼
              ┌──────────────────────────────────────────────────────────────┐
              │ ARTIFACT/PERSISTENCE: manifests │ traces │ state │ hashes   │
              │ raw outputs │ cost/timing │ references │ human labels       │
              └──────────────┬───────────────────────────┬───────────────────┘
                             │                           │
                  rescore    ▼                           ▼ aggregate
                      ┌──────────────┐             ┌──────────────┐
                      │ Grader pools │             │ Statistics   │
                      │ code/model/  │────────────►│ CI/delta/    │
                      │ human        │ scores      │ slices/Pareto│
                      └──────┬───────┘             └──────┬───────┘
                             │                            │
                             ▼                            ▼
              ┌──────────────────────────────────────────────────────────────┐
              │ TELEMETRY: OTel spans │ queue/pool health │ cost │ SIEM     │
              │ AUDIT: versions │ access │ exclusions │ judgments │ decision │
              └──────────────────────────────────────────────────────────────┘
```

Control and execution are separate. The registry defines what is being measured; the runner reproduces the production agent rather than bypassing retrieval, routing, memory, policy, or tool wrappers. Environments begin from a versioned clean state. Grading is a separate stage so immutable traces can be rescored without rerunning costly agents. Raw sensitive content lives in a restricted artifact system; ordinary telemetry is metadata-first.

### End-to-end request flow

1. The suite owner states the construct, target task population, decision, primary estimand, risks, slices, absolute gates, candidate-baseline comparison, trial count, exclusions, and multiplicity plan.
2. The registry freezes task inputs, references, licences, sensitivity, environment seeds and images, harness/tool/policy versions, candidate configuration, graders, and analysis plan in a signed manifest.
3. The controller expands `(suite, item, candidate, trial_index, environment_seed)` into idempotent work. A canary first validates environment reset, permissions, trace capture, cost, and graders.
4. An isolated runner executes the complete on-policy agent. It records model/tool/state/approval events, usage and spans. The authoritative outcome is read from environment state, not the agent's final claim.
5. Artifacts are checksummed and atomically published before the trial becomes terminal. Infrastructure, agent, task, environment, and policy failures retain distinct raw outcomes and dispositions.
6. Deterministic graders run first. Calibrated model judges evaluate only semantic criteria, treating candidate content as untrusted. Human experts label calibration and adjudication samples under blinding.
7. Statistics aggregate at the task experimental unit, preserving repeated trials within task. They compute absolute intervals, paired deltas, critical slices, reliability, full cost, and admitted/censored latency.
8. Hard safety, success, slice, judge-calibration, latency, and cost gates determine eligibility. Eligible candidates are compared on a Pareto frontier; quality gains cannot compensate for unauthorized side effects.
9. A signed release or rejection records evidence and exceptions. Shadow/canary monitoring validates distribution shift and supplies fresh cases without silently changing the frozen result.

### The six-dimensional scorecard

| Dimension | Question | Primary evidence | Common false shortcut |
|---|---|---|---|
| Task success | Did the intended policy-compliant outcome exist? | database/file/test/receipt state; milestones; semantic outcome | grade the final claim |
| Trajectory | Was the path safe, grounded, efficient, and recoverable? | trace invariants, progress, loops, retries, policy order | require one arbitrary gold path |
| Tool accuracy | Was tool need, choice, arguments, ordering, execution, result use, and side effect correct? | schema plus stateful execution and state delta | count JSON-valid calls only |
| Quality | Is the artifact correct, complete, relevant, evidenced, clear, and safe? | per-criterion code/model/human rubric | one composite or embedding score |
| Cost | What did each trial, evaluation, and compliant success cost? | provider usage/invoices, compute/tools/scans/humans | tokens per attempt only |
| Latency | How long did admitted users and stages wait, including tails and failures? | queue/model/tool/approval/grader spans | mean of successes only |

## 2. Core Mechanics & Algorithms

### 2.1 Start with the estimand

An estimand specifies population, treatment/configuration, outcome, aggregation, and handling of intercurrent events. Example: “paired difference in policy-compliant success probability between candidate C and baseline B for production-like refund tasks in suite v7, under a 120-second/20k-token budget, counting agent timeouts as failures and reporting infrastructure-invalid trials separately.” That is testable; “benchmark improvement” is not.

The experimental unit is normally the **task**. Repeated trials are nested within task, and rubric assertions or trajectory steps are measurements within a trial. Treating steps as independent is pseudo-replication and produces falsely narrow intervals.

Use three views deliberately:

- **Fixed-suite or benchmark accuracy:** conditional on these exact items.
- **Generalized performance:** inference to a population of similar future tasks, requiring representative sampling and task-level uncertainty.
- **Production effect:** outcome under real traffic, policy, user behavior, and operational constraints, usually established after offline gates through shadow/canary/online experiments.

Prefer paired candidate-baseline trials on the same task and environment seed. Pairing removes item difficulty from much of the delta variance. Randomize execution order and isolate state so candidate order does not create warm-cache or drift bias.

### 2.2 Task success and reliability

Grade the strongest available oracle in this order: deterministic state, objective milestones, evidence-bound semantic rubric, human adjudication. Define:

```text
micro_success = successful_trials / valid_trials
macro_success = mean_t(successes_t / valid_trials_t)
weighted_success = Σ(weight_t × successes_t) / Σ(weight_t × valid_trials_t)
policy_compliant_success = count(success AND no hard violation) / valid_trials
```

Publish numerator, denominator, invalid-infrastructure flow, and a confidence interval. Macro protects against frequent tasks dominating; micro reflects trial volume; business weighting reflects a declared production mix but is easy to manipulate and must be versioned.

Reliability metrics are opposites:

```text
pass@k = probability at least one of k candidates succeeds
unbiased pass@k from n samples/c successes = 1 - C(n-c,k)/C(n,k)

pass^k = probability every one of k repeated trials succeeds
```

`pass@k` is relevant only when the system can generate and correctly select among candidates. `pass^k` exposes consistency. Do not estimate population `pass^k` merely as `p^k` when task difficulty and trials are correlated; group repeated results by task and report the empirical distribution.

For a binary rate, a Wilson interval is more stable than a normal interval at small `n` or near 0/1. For candidate-baseline comparison, use task-clustered paired bootstrap or a hierarchical/binomial mixed model. Bootstrap tasks, bringing all their repeated trials with them. Predeclare one primary comparison; adjust or label exploratory slice tests rather than fishing across many metrics.

### 2.3 Trajectory evaluation

Trajectory graders diagnose safety and efficiency while allowing valid alternative paths. Encode exact ordering only for genuine policy/protocol dependencies such as `authenticate -> authorize -> approve -> write -> verify`.

Measure:

- required milestone/state coverage and forbidden action/state count;
- grounded-action rate against current observations;
- repeated `(state_hash, action_hash)`, no-progress steps, backtracking and loop termination;
- model/tool calls, handoffs, approvals, retries, depth, tokens and wall time;
- recovery after injected provider/tool/user/environment failures;
- successful, premature, budget-exhausted, unsafe, and justified-escalation termination;
- path efficiency `valid_lower_bound_steps / actual_successful_steps`, capped at one only when the lower bound is defensible.

A linear trace scan is `O(T)` for `T` events. Hash-set loop detection is expected `O(T)` time and `O(U)` memory for `U` unique state-action pairs. Partial-order validation uses a dependency DAG: topological validation is `O(V + E)` for required actions `V` and precedence edges `E`. Exact sequence equality should not replace these invariants.

### 2.4 Tool accuracy is a lifecycle

| Stage | Estimator | Failure example |
|---|---|---|
| Need/abstain | precision, recall, F1 | tool used for a known answer; tool omitted when current state required |
| Selection/auth | top-1 accuracy, unsafe selection rate | correct function name but unauthorized server/account |
| Arguments | schema validity plus field exact/F1/semantic check | correct types, wrong tenant or unit |
| Dependencies | valid partial-order completion | refund before eligibility/approval |
| Execution | success/error/timeout by tool/status | backend rejected or timed out |
| Result use | grounded entailment/omission/contradiction | response ignores “not committed” |
| Side effect | intended state-delta match, duplicate/unauthorized writes | two purchases after retry |

Component function-calling suites isolate selection and arguments. Stateful scenarios are required for dependencies, dynamic users, execution, result use, idempotency, and side effects. Count a schema-valid call targeting the wrong resource as inaccurate.

### 2.5 Quality and judge calibration

Define separate observable rubric criteria: factual correctness, completeness, relevance, instruction adherence, evidence/citations, uncertainty, coherence, accessibility/tone, and domain safety. Hard constraints remain binary. Weighted composites are secondary diagnostic summaries, never a way to offset a critical violation.

Use deterministic graders for encodable state and policy, references only for narrow equivalence, model judges for calibrated semantic variation, and humans for calibration/consequential ambiguity. A judge is a measurement instrument with its own dataset and version.

Judge validation process:

1. Build a hidden set stratified by task, quality, language, length, candidate family, failure mode, and prompt-injection/adversarial output.
2. Obtain independent labels from at least two trained humans; retain raw labels and adjudicate disagreements.
3. Blind candidate identity, randomize pair order, perform order-swap consistency tests, and control verbosity when it is not a criterion.
4. For hard binary gates, publish confusion matrix, precision, recall, F1, false-negative rate, abstention, and Cohen's kappa. For ordinal/continuous rubrics, publish distributions, weighted agreement and rank/linear correlation where justified.
5. Slice calibration. A judge with good aggregate agreement may fail on one language, model family, length, or attack type.
6. Pin judge model/prompt/rubric; revalidate after any change and periodically against a random production sample.

Binary Cohen's kappa is `(observed agreement - chance agreement)/(1 - chance agreement)`. It corrects for marginal agreement but can behave counterintuitively under extreme prevalence; always show the confusion matrix. A panel is useful only when calibration demonstrates lower error; shared model families and prompts can produce correlated blind spots.

Candidate output is quoted untrusted data, not judge instruction. A judge should have no tools, secrets, hidden reference access beyond its intended rubric, or authority to change release state. Require an `insufficient_evidence` option rather than forcing confident scores.

### 2.6 Cost and latency estimands

Cost includes the evaluated agent and the evaluation:

```text
trial_cost = input + cache_write + cache_read + output
           + tools/search/browser + sandbox + scan + storage/egress
           + approval/review

evaluation_cost = Σ(trial_cost) + deterministic grading + judge calls
                + human labels/adjudication + environment reset/storage

cost_per_compliant_success = Σ(trial_cost) / compliant_successes
```

Use metered provider usage and invoices; estimates are for planning. Report attempts, retries, invalid infrastructure and human time. Cost per attempt can fall while cost per accepted outcome rises.

Latency uses nested spans: queue/admission, retrieval/context, model, tool, sandbox, approval, retry/backoff, grading and end-to-end. Report time to first token/event/tool/correct partial result, p50/p95/p99, timeout/abandonment, service versus queue time, and throughput/utilization. Show successful latency and all-admitted latency with censored/failed trials. Approval-included and machine-only latency answer different operational questions.

### 2.7 Release logic and invariants

```text
eligible = policy_compliant_success lower bound >= target
       AND unsafe side-effect upper bound <= ceiling
       AND critical-slice lower bound >= floor
       AND judge calibration >= minimum
       AND all-admitted p95 latency <= SLO
       AND cost per compliant success <= budget
```

Eligible candidates lie on a cost-latency-quality Pareto frontier. A candidate dominates another only if it is no worse on all declared objectives and better on at least one. Router changes are evaluated as policies, not models in isolation; control for task difficulty by paired or randomized assignment.

Evaluation invariants:

- One immutable manifest determines suite, candidate, environment, grader, estimator, retry, and exclusion semantics.
- The final environment state outranks the agent's self-report.
- All attempts remain visible; infrastructure retry never turns an agent failure into `pass@1`.
- Trial state is isolated; cache keys include every material input and authorization partition.
- Hard safety/policy gates are non-compensable.
- Aggregation occurs at the declared experimental unit and retains uncertainty.
- A judge cannot grade a release until its calibration version passes its own gate.
- Changing a task/reference/environment/grader/exclusion produces a new comparable version or an explicit break in series.

## 3. Token Economics & NFR Analysis

### 3.1 Explicit evaluation cost per 1,000 trials

Illustrative governed release evaluation, dated 2026-08-21:

- 1,000 agent trials: 10M uncached input, 20M cached stable prompt/tool-prefix reads, 0.1M cache writes, 3M output.
- Fixed `terra` judge pool: 4M input + 1M output = `4×$2 + 1×$12 = $20`.
- Machine/runtime: environment reset/sandbox `$60`, tools/search/browser `$25`, storage/trace `$10`, deterministic graders `$5` = `$100`.
- Human calibration/adjudication: 20 hours at `$90/hour` = `$1,800`.

| Agent tier | No-cache agent model | Cached agent model | Governed total: cached agent + judge + machine + human |
|---|---:|---:|---:|
| `sol` (`$5/$30`, read `$0.50`, write `$6.25`) | `30×$5 + 3×$30` = **$240.00** | `10×$5 + 20×$.50 + .1×$6.25 + 3×$30` = **$150.63** | **$2,070.63/1K** |
| `terra` (`$2/$12`, read `$0.20`, write `$2.50`) | `30×$2 + 3×$12` = **$96.00** | `10×$2 + 20×$.20 + .1×$2.50 + 3×$12` = **$60.25** | **$1,980.25/1K** |
| `luna` (`$.20/$1.20`, read `$.02`, write `$.25`) | `30×$.20 + 3×$1.20` = **$9.60** | `10×$.20 + 20×$.02 + .1×$.25 + 3×$1.20` = **$6.03** | **$1,926.03/1K** |

The full governed total includes a new judge-calibration exercise; a routine `terra` regression run that amortizes human calibration costs is `$60.25 + $20 + $100 = $180.25/1K`. If only 740 trials are policy-compliant successes, the full governed evaluation cost per 1,000 compliant successes is `$1,980.25×1000/740 = $2,676.01`. Report both marginal run cost and lifecycle calibration cost.

Cache only exact, stable, non-leaking prefixes and immutable environment layers. Trial output, hidden references, judge decisions, user state and candidate-specific trajectories cannot leak between trials. Report hit rate and candidate parity: one candidate receiving warmer caches biases cost and latency.

### 3.2 Latency SLOs

These are illustrative internal targets, not public cross-platform benchmarks:

| Stage | p50 | p95 | p99 | Tail treatment |
|---|---:|---:|---:|---|
| API-only agent trial | 12 s | 60 s | 180 s | provider bulkhead, deadline, bounded turns |
| Browser/repository sandbox trial | 45 s | 180 s | 600 s | warm immutable image, pool by resource class, checkpoint |
| Deterministic grading | 50 ms | 500 ms | 2 s | vectorize/batch, content-addressed result cache |
| Model-judge call | 800 ms | 3 s | 8 s | independent breaker, batch only compatible prompts |
| Human label | 2 min | 10 min | 30 min | staffing queue, gold checks, breaks; report separately |
| 1,000-trial regression report | 15 min | 45 min | 90 min | canary then fan-out, incremental aggregation |

Release SLOs use **all admitted** trials, with timeout counted at its deadline, alongside successful-only latency. A fast candidate that times out frequently is not low latency. Separate agent execution from grading and human approval so teams know which pool to scale.

### 3.3 Throughput and back-pressure

For `N=2,000` tasks, `R=3` trials, `M=2` configurations, and `G=2` judge calls/trial:

```text
agent trials = N×R×M = 12,000
judge calls  = N×R×M×G = 24,000

at λ=30 trial starts/s and mean occupied W=45 s:
mean active runners L=λW=1,350; with 30% headroom ≈1,755

at 100 judge calls/s, judge service lower bound=24,000/100=240 s
```

Do not provision 1,755 identical workers. Split API, browser, high-CPU sandbox, environment-reset, deterministic-grader, judge, and human queues. Model/request/token rate limits, sandbox CPU/RAM, browser licences, environment account locks, artifact bandwidth, grader throughput and statistics memory are independent bottlenecks.

Admission estimates tokens, tool calls, runtime, judge and human budget before fan-out. Use tenant/suite weighted fairness, per-provider token buckets, named concurrency limits, bounded in-flight work, and queue-age SLOs. Canary 1–5% of a matrix before full launch. Under pressure, defer exploratory slices, reduce repeated trials only under a predeclared adaptive design, rescore immutable traces later, or stop the comparison symmetrically. Never drop only a slow candidate.

### 3.4 NFR scorecard

| NFR | Target/evidence | Trade-off |
|---|---|---|
| Availability | 99.9% scheduling/artifact/grading; 99.99% release/audit API | More replicas and reserved control capacity cost money. |
| RPO | 0 terminal trials, labels, decisions and audit; ≤1 long-phase checkpoint | Atomic artifact publication and frequent checkpoints add I/O. |
| RTO | ≤15 min control/release; ≤60 min worker pools; resume from durable shards | Warm pools improve RTO but consume idle capacity. |
| Reproducibility | immutable manifest/artifacts; statistically comparable rerun | Provider nondeterminism prevents byte identity. |
| Statistical validity | paired/task-clustered intervals, declared exclusions/multiplicity | Repeated representative trials increase cost. |
| Security | zero hidden-reference/cross-tenant leak; sandbox and grader injection suite | Isolation limits cache reuse and debugging access. |
| Privacy/compliance | purpose/licence/residency/retention/deletion and labeler access evidence | Raw traces require expensive restricted handling. |
| Portability | open manifests/JSONL/artifacts/OTel metadata, no dashboard-only evidence | Lowest-common-denominator formats lose vendor features. |
| Operability | environment health, missingness parity, queue/cost/drift dashboards, kill switch | High-cardinality evidence needs careful retention. |

## 4. Distributed Resilience & Security

### 4.1 Durable orchestration and replay

```text
┌──────────────┐ signed run ┌──────────────┐ shard/lease ┌──────────────┐
│ Suite API    ├───────────►│ Temporal     ├────────────►│ Trial worker │
│ release gate│◄─status─────┤ controller   │◄─checkpoint┤ isolated env │
└──────────────┘            └──────┬───────┘             └──────┬───────┘
                                   │ outbox/events               │ artifacts
                                   ▼                             ▼
                            ┌──────────────┐              ┌──────────────┐
                            │ Kafka/DLQ    │              │ Object store │
                            │ scheduler    │              │ hash/retain  │
                            └──────┬───────┘              └──────┬───────┘
                                   │ grade shard                 │ immutable trace
                                   ▼                             ▼
                            ┌──────────────┐              ┌──────────────┐
                            │ Grader pool  ├─────────────►│ Stats/report │
                            │ isolated     │              │ signed gate  │
                            └──────────────┘              └──────────────┘
```

Shard by `(suite_version, item_id, candidate_id, trial_index)` and use it as the idempotency key. Temporal or an equivalent workflow engine owns scheduling, timers, retries, cancellation and human signals. Workers acquire fenced leases, heartbeat, and checkpoint after environment setup and long phases. A terminal transition atomically references already-persisted checksummed artifacts. A transactional outbox publishes Kafka events; consumers deduplicate event IDs. Repeated infrastructure poison goes to a DLQ with safe diagnostics and artifact pointers.

Generation and grading are separate durable workflows. A grader change creates a new score version over the same immutable trace. Aggregation reads only terminal records satisfying the manifest's inclusion policy; it never scans “whatever currently exists.” Multi-region recovery restores manifests, artifact indexes, terminal trials, labels and release decisions at RPO zero; disposable environments are rebuilt.

### 4.2 Retry, breaker, failure attribution and drift

Classify before retry: agent, model provider, tool/environment, harness, grader, task, or policy. Retry only predeclared transient infrastructure failures with exponential full jitter and an aggregate deadline. Retain every attempt. An agent failure is not silently regenerated into `pass@1`; an unknown write is reconciled by operation ID or state before replay.

Breakers are per provider/model/tool/environment/grader and transition `closed -> open -> half_open`. Separate candidate pools prevent one candidate's failure from starving the other. Abort comparison when infrastructure missingness, resource allocation or environment drift differs materially between candidates.

| Failure | Signal | Recovery/disposition |
|---|---|---|
| Provider rate limit/5xx | typed status, rising tail | bounded retry/breaker; secondary; deterministic abstention |
| Broken environment/reset | health canary, state hash mismatch | invalidate as infrastructure; quarantine image; no agent blame |
| Agent policy/tool failure | valid environment, forbidden action/wrong state | valid failed trial; no infrastructure retry |
| Ambiguous task/reference | disagreement, impossible state | versioned exclusion/adjudication; sensitivity with and without |
| Grader crash/drift | replay variance, calibration failure | rescore with pinned prior/secondary; block release |
| Timeout after side effect | state/response ambiguity | reconcile; mark unsafe/unknown under declared semantics |
| Unequal missingness | candidate-specific infra error | stop, diagnose and rerun paired conditions |
| Contamination/saturation | public-production gap, memorized artifacts | exposure record, temporal holdout, retire to regression |

Maintain separate capability, regression, fresh temporal, adversarial and production-shadow suites. Solved capability cases graduate to regression; broken or exposed tasks are retired under a new version, not silently edited. Historical results remain bound to original conditions.

### 4.3 Zero Trust MCP, runner and grader isolation

MCP/tool compatibility does not grant trust. The evaluation host filters tools, pins server/artifact identity, and issues task-scoped credentials for synthetic environments. Tool-level RBAC separates list/read, execute, write/side-effect, environment reset, hidden-reference administration, grade and release. Candidate runners cannot access hidden expected outputs, grader prompts, other candidates' artifacts, production systems, or the release API.

Untrusted code runs non-root in an ephemeral container/VM with immutable image, PID/CPU/RAM/disk/time limits, no host socket, constrained mounts and default-deny egress. Synthetic environment writes use idempotency and destination allowlists. A browser context is not sufficient isolation for hostile code or shared backend accounts.

Grader workers are separate principals and networks. Candidate text, tool output and traces are delimited as untrusted data; model judges have no tools/secrets and cannot alter scores except through schema-validated result publication. Deterministic safety assertions override semantic judges. Test prompt injection, rubric exfiltration, verbosity/position/self-preference and reward hacking. Blind identity and randomize pair order.

### 4.4 PII, audit, governance and release integrity

Data flow: `classify purpose/licence -> detect secrets/PII -> minimize -> redact/tokenize -> authorize runner/grader/labeler -> process -> restricted rehydrate if required -> retain/delete -> audit`. Apply it to inputs, prompts, tool arguments/results, browser screenshots, repositories, sources, raw labels, judge context, traces, eval exports and backups. Default OTel to metadata; raw content requires explicit class-aware opt-in.

Immutable audit includes actor/tenant, suite/item/trial/span IDs, dataset/task hashes, model/harness/prompt/tool/environment/policy/grader/statistics versions, authorization, resource settings, all attempt dispositions, protected action hash, outcome, labels/adjudication, scores/intervals, cost/latency, exclusion, artifact hashes, release decision, exception approver and expiry. Publish through a transactional outbox to signed/hash-chained WORM batches and audit audit-log access.

Separate owners govern product objective, dataset, harness, graders/statistics, security and release. Each suite has an evaluation card: construct/population, sampling, intended and prohibited use, environment, metrics, uncertainty, judge calibration, privacy/licence, contamination, known limitations and retirement trigger. Exceptions cannot rewrite results; they are signed, time-bounded release decisions with monitoring and rollback.

## 5. Production Enterprise Code

This Python 3.11 standard-library program is a compact release evaluator. It validates trial records, scores all six dimensions, uses Wilson intervals, performs a task-clustered paired bootstrap, distinguishes `pass@k` from empirical `pass^k`, calibrates a binary judge against human labels, and applies non-compensable release gates. The judge path implements exponential full-jitter retry, closed/open/half-open breakers, primary -> secondary -> deterministic abstention, correlation logs, and safe degradation.

```python
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Protocol, Sequence


class TransientFailure(RuntimeError):
    """A retryable evaluator dependency failure."""


class PermanentFailure(RuntimeError):
    """An invalid record, rubric, or policy condition."""


class CircuitOpen(TransientFailure):
    """A dependency is temporarily disabled."""


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {"timestamp": time.time(), "level": record.levelname,
                 "message": record.getMessage()}
        for key in ("run_id", "task_id", "candidate", "stage", "attempt",
                    "judge", "status"):
            if hasattr(record, key):
                value[key] = getattr(record, key)
        return json.dumps(value, separators=(",", ":"), sort_keys=True)


logger = logging.getLogger("release-eval")
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
        self._state = "closed"
        self._opened_at = 0.0
        self._probe = False
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


@dataclass(frozen=True)
class JudgeDecision:
    label: bool | None
    score: float | None
    judge: str
    abstained: bool


class QualityJudge(Protocol):
    name: str

    def grade(self, untrusted_output: str, timeout_s: float) -> str:
        raise RuntimeError("QualityJudge is an interface")


class DemoJudge:
    def __init__(self, name: str, available: bool):
        self.name = name
        self._available = available

    def grade(self, untrusted_output: str, timeout_s: float) -> str:
        if not self._available or timeout_s <= 0:
            raise TransientFailure(f"{self.name} unavailable")
        # Candidate text is data. It never becomes executable judge instruction.
        supported = "evidence:" in untrusted_output.lower()
        return json.dumps({"label": supported,
                           "score": .90 if supported else .55})


class DeterministicAbstention:
    name = "deterministic-abstention"

    def grade(self, untrusted_output: str, timeout_s: float) -> str:
        return json.dumps({"label": None, "score": None, "abstain": True})


class JudgeChain:
    def __init__(self, judges: Sequence[QualityJudge]):
        if len(judges) < 2:
            raise ValueError("primary and secondary judges required")
        self._judges = tuple(judges)
        self._breakers = {judge.name: Breaker() for judge in judges}
        self._fallback = DeterministicAbstention()

    def grade(self, output: str, deadline: float, run_id: str,
              task_id: str, candidate: str) -> JudgeDecision:
        for judge in self._judges:
            breaker = self._breakers[judge.name]
            for attempt in range(1, 3):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._decode(
                        self._fallback.grade(output, .01), self._fallback.name
                    )
                try:
                    breaker.before()
                    raw = judge.grade(output, min(remaining, 2.0))
                    decision = self._decode(raw, judge.name)
                    breaker.success()
                    return decision
                except CircuitOpen:
                    break
                except (json.JSONDecodeError, PermanentFailure) as exc:
                    breaker.failure()
                    logger.warning("permanent judge failure", extra={
                        "run_id": run_id, "task_id": task_id,
                        "candidate": candidate, "stage": "quality_grade",
                        "attempt": attempt, "judge": judge.name,
                        "status": type(exc).__name__})
                    break
                except (TransientFailure, TimeoutError) as exc:
                    breaker.failure()
                    logger.warning("judge failure", extra={
                        "run_id": run_id, "task_id": task_id,
                        "candidate": candidate, "stage": "quality_grade",
                        "attempt": attempt, "judge": judge.name,
                        "status": type(exc).__name__})
                    if attempt < 2:
                        cap = min(.02 * (2 ** (attempt-1)),
                                  max(0.0, deadline-time.monotonic()))
                        time.sleep(random.uniform(0.0, cap))
        return self._decode(
            self._fallback.grade(output, .01), self._fallback.name
        )

    @staticmethod
    def _decode(raw: str, judge: str) -> JudgeDecision:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise PermanentFailure("judge result must be an object")
        if value.get("abstain") is True:
            return JudgeDecision(None, None, judge, True)
        label, score = value.get("label"), value.get("score")
        if (not isinstance(label, bool)
                or not isinstance(score, (int, float))
                or not 0 <= float(score) <= 1):
            raise PermanentFailure("invalid judge result schema")
        return JudgeDecision(label, float(score), judge, False)


@dataclass(frozen=True)
class Trial:
    task_id: str
    trial_index: int
    candidate: str
    success: bool
    policy_violation: bool
    grounded_actions: int
    total_actions: int
    correct_tool_stages: int
    attempted_tool_stages: int
    quality_score: float
    cost_usd: float
    latency_s: float
    infrastructure_valid: bool = True

    def validate(self) -> None:
        if not self.task_id or self.trial_index < 0 or not self.candidate:
            raise PermanentFailure("invalid trial identity")
        if not 0 <= self.grounded_actions <= self.total_actions:
            raise PermanentFailure("invalid trajectory counts")
        if not 0 <= self.correct_tool_stages <= self.attempted_tool_stages:
            raise PermanentFailure("invalid tool counts")
        if not 0 <= self.quality_score <= 1:
            raise PermanentFailure("quality outside [0,1]")
        if self.cost_usd < 0 or self.latency_s < 0:
            raise PermanentFailure("negative cost or latency")


class EvidenceStore:
    def __init__(self):
        self._records: list[dict[str, object]] = []
        self._lock = threading.Lock()

    def append(self, record: dict[str, object]) -> None:
        encoded = json.dumps(record, separators=(",", ":"), sort_keys=True)
        item = {"record": record,
                "sha256": hashlib.sha256(encoded.encode()).hexdigest()}
        with self._lock:
            self._records.append(item)

    def count(self) -> int:
        with self._lock:
            return len(self._records)


def wilson_interval(successes: int, trials: int,
                    z: float = 1.959963984540054) -> tuple[float, float]:
    if trials <= 0 or not 0 <= successes <= trials:
        raise ValueError("invalid binomial counts")
    p = successes / trials
    denominator = 1 + z*z/trials
    center = (p + z*z/(2*trials)) / denominator
    margin = z * math.sqrt((p*(1-p) + z*z/(4*trials))/trials) / denominator
    return max(0.0, center-margin), min(1.0, center+margin)


def quantile(values: Sequence[float], probability: float) -> float:
    if not values or not 0 <= probability <= 1:
        raise ValueError("invalid quantile input")
    ordered = sorted(values)
    index = min(len(ordered)-1, max(0, math.ceil(probability*len(ordered))-1))
    return ordered[index]


def pass_at_k(total_samples: int, correct_samples: int, k: int) -> float:
    if not (0 <= correct_samples <= total_samples and 1 <= k <= total_samples):
        raise ValueError("invalid pass@k counts")
    if total_samples - correct_samples < k:
        return 1.0
    return 1 - math.comb(total_samples-correct_samples, k) / \
        math.comb(total_samples, k)


def empirical_pass_power_k(trials: Sequence[Trial], candidate: str,
                           k: int) -> float:
    groups: dict[str, list[Trial]] = {}
    for trial in trials:
        if trial.candidate == candidate and trial.infrastructure_valid:
            groups.setdefault(trial.task_id, []).append(trial)
    eligible = [sorted(items, key=lambda item: item.trial_index)[:k]
                for items in groups.values() if len(items) >= k]
    if not eligible:
        raise ValueError("no task has k valid trials")
    return sum(all(item.success and not item.policy_violation for item in items)
               for items in eligible) / len(eligible)


def paired_task_bootstrap(trials: Sequence[Trial], candidate: str,
                          baseline: str, samples: int = 4_000,
                          seed: int = 7) -> tuple[float, float, float]:
    grouped: dict[tuple[str, str], list[bool]] = {}
    for item in trials:
        if item.infrastructure_valid and item.candidate in {candidate, baseline}:
            grouped.setdefault((item.task_id, item.candidate), []).append(
                item.success and not item.policy_violation
            )
    tasks = sorted({task for task, model in grouped
                    if (task, candidate) in grouped and (task, baseline) in grouped})
    if not tasks:
        raise ValueError("paired comparison has no common tasks")
    deltas = [sum(grouped[(task, candidate)]) / len(grouped[(task, candidate)])
              - sum(grouped[(task, baseline)]) / len(grouped[(task, baseline)])
              for task in tasks]
    observed = sum(deltas)/len(deltas)
    rng = random.Random(seed)
    estimates = []
    for _ in range(samples):
        draw = [deltas[rng.randrange(len(deltas))] for _ in deltas]
        estimates.append(sum(draw)/len(draw))
    return observed, quantile(estimates, .025), quantile(estimates, .975)


def judge_calibration(human: Sequence[bool], judge: Sequence[bool]) \
        -> dict[str, float | int]:
    if len(human) != len(judge) or not human:
        raise ValueError("labels must be nonempty and aligned")
    tp = sum(h and j for h, j in zip(human, judge))
    tn = sum(not h and not j for h, j in zip(human, judge))
    fp = sum(not h and j for h, j in zip(human, judge))
    fn = sum(h and not j for h, j in zip(human, judge))
    precision = tp/(tp+fp) if tp+fp else 0.0
    recall = tp/(tp+fn) if tp+fn else 0.0
    f1 = 2*precision*recall/(precision+recall) if precision+recall else 0.0
    observed = (tp+tn)/len(human)
    human_pos, judge_pos = (tp+fn)/len(human), (tp+fp)/len(human)
    chance = human_pos*judge_pos + (1-human_pos)*(1-judge_pos)
    kappa = (observed-chance)/(1-chance) if chance < 1 else 1.0
    return {"tp": tp, "tn": tn, "fp": fp, "fn": fn,
            "precision": precision, "recall": recall,
            "f1": f1, "kappa": kappa}


class ReleaseEvaluator:
    def __init__(self, store: EvidenceStore):
        self._store = store

    def evaluate(self, trials: Sequence[Trial], candidate: str,
                 calibration: dict[str, float | int]) -> dict[str, object]:
        selected = [item for item in trials
                    if item.candidate == candidate and item.infrastructure_valid]
        if not selected:
            raise PermanentFailure("no valid candidate trials")
        for item in selected:
            item.validate()
        compliant = sum(item.success and not item.policy_violation
                        for item in selected)
        violations = sum(item.policy_violation for item in selected)
        success_ci = wilson_interval(compliant, len(selected))
        violation_ci = wilson_interval(violations, len(selected))
        actions = sum(item.total_actions for item in selected)
        tool_attempts = sum(item.attempted_tool_stages for item in selected)
        total_cost = sum(item.cost_usd for item in selected)
        report = {
            "candidate": candidate,
            "validTrials": len(selected),
            "taskSuccess": compliant/len(selected),
            "taskSuccess95": success_ci,
            "policyViolationRate": violations/len(selected),
            "policyViolation95": violation_ci,
            "trajectoryGroundedRate":
                sum(item.grounded_actions for item in selected)/actions,
            "toolLifecycleAccuracy":
                sum(item.correct_tool_stages for item in selected)/tool_attempts,
            "qualityMean": sum(item.quality_score for item in selected)/len(selected),
            "totalCostUsd": total_cost,
            "costPerCompliantSuccessUsd":
                total_cost/compliant if compliant else math.inf,
            "latencyP50S": quantile([item.latency_s for item in selected], .50),
            "latencyP95S": quantile([item.latency_s for item in selected], .95),
            "judgeCalibration": calibration,
            "passPower3": empirical_pass_power_k(selected, candidate, 3),
        }
        gates = {
            "successLowerBound": success_ci[0] >= .55,
            "unsafeUpperBound": violation_ci[1] <= .25,
            "toolAccuracy": report["toolLifecycleAccuracy"] >= .90,
            "quality": report["qualityMean"] >= .75,
            "latency": report["latencyP95S"] <= 50,
            "unitCost": report["costPerCompliantSuccessUsd"] <= 1.20,
            "judgeF1": float(calibration["f1"]) >= .80,
        }
        report["gates"] = gates
        report["eligible"] = all(gates.values())
        self._store.append(report)
        logger.info("release evaluation complete", extra={
            "run_id": uuid.uuid4().hex, "candidate": candidate,
            "stage": "release_gate",
            "status": "eligible" if report["eligible"] else "blocked"})
        return report


def build_trials(chain: JudgeChain) -> list[Trial]:
    trials: list[Trial] = []
    patterns = {"baseline": [2, 2, 1, 2], "candidate": [3, 2, 3, 2]}
    for candidate, successes_per_task in patterns.items():
        for task_number, successes in enumerate(successes_per_task, start=1):
            for trial_index in range(3):
                output = ("Evidence: state and receipt verified."
                          if candidate == "candidate" or trial_index == 0
                          else "Outcome appears acceptable.")
                decision = chain.grade(output, time.monotonic()+1,
                                       "suite-run", f"task-{task_number}",
                                       candidate)
                if decision.abstained or decision.score is None:
                    quality = 0.0
                else:
                    quality = decision.score
                success = trial_index < successes
                trials.append(Trial(
                    task_id=f"task-{task_number}", trial_index=trial_index,
                    candidate=candidate, success=success,
                    policy_violation=False,
                    grounded_actions=5 if candidate == "candidate" else 4,
                    total_actions=5,
                    correct_tool_stages=3 if (candidate == "candidate"
                                              or trial_index != 2) else 2,
                    attempted_tool_stages=3, quality_score=quality,
                    cost_usd=.72 if candidate == "candidate" else .66,
                    latency_s=(22 + 3*trial_index + task_number
                               if candidate == "candidate"
                               else 28 + 4*trial_index + task_number)
                ))
    return trials


def main() -> None:
    chain = JudgeChain((DemoJudge("primary", False),
                        DemoJudge("secondary", True)))
    trials = build_trials(chain)
    human = [True, True, True, True, True, False, False, False, False, False]
    judge = [True, True, True, True, False, False, False, False, False, False]
    calibration = judge_calibration(human, judge)
    store = EvidenceStore()
    report = ReleaseEvaluator(store).evaluate(trials, "candidate", calibration)
    delta = paired_task_bootstrap(trials, "candidate", "baseline")
    fallback = JudgeChain((DemoJudge("judge-a-down", False),
                           DemoJudge("judge-b-down", False))).grade(
                               "untrusted output", time.monotonic()+1,
                               "outage-run", "task-outage", "candidate")
    print(json.dumps({
        "eligible": report["eligible"],
        "pairedSuccessDelta95": delta,
        "passAt2Example": pass_at_k(3, 2, 2),
        "passPower3": report["passPower3"],
        "judgeF1": calibration["f1"],
        "outageAbstained": fallback.abstained,
        "storedReports": store.count(),
    }, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
```

The primary judge opens its breaker after two failures; the secondary grades the suite. The all-judge outage returns an abstention rather than manufacturing a semantic score. The release report preserves each dimension and blocks on every hard gate. In production, persist trial/evidence records transactionally rather than using the small in-memory demonstration store.

## 6. Architectural System Design Scenarios

### Scenario 1 - Policy-constrained support-agent release gate

**Problem statement.** Evaluate a model/harness update for a support agent that reads customer accounts, changes bookings, and issues refunds. Run 6,000 production-like trials/night across policy, language, customer tier, ambiguity, injection, tool outage, and timeout-after-write slices. Required gates: policy-compliant success lower bound at least 78%, unauthorized or duplicate write upper bound below 0.1%, every critical slice lower bound at least 70%, p95 all-admitted machine latency under 90 seconds, and cost per compliant success below `$1.40`.

**Proposed architecture.** A versioned stateful simulator provides database, airline/retail tools, policy documents, dynamic multi-turn users, idempotency and exact final-state assertions. The controller pairs baseline/candidate on task and seed and repeats a reliability subset five times. Deterministic graders inspect database state, communication requirements, authorization, state delta and duplicate writes. Trace graders assess dependencies, grounded tool use, recovery and termination. A calibrated model judge scores only semantic communication dimensions; experts own its hidden calibration set and adjudicate disagreements. Statistics produce task-clustered intervals, `pass^5`, cost/success and all-admitted latency. Shadow runs with writes disabled precede a 1% canary with instant rollback.

```text
┌──────────────┐ suite/estimand ┌──────────────┐ paired shards ┌──────────────┐
│ Policy/QA    ├───────────────►│ Durable eval ├──────────────►│ Candidate +  │
│ release owner│◄─signed gate───┤ controller   │               │ baseline     │
└──────────────┘                └──────┬───────┘               └──────┬───────┘
                                      │                               │ tool/user turns
                                      ▼                               ▼
                               ┌──────────────┐                ┌──────────────┐
                               │ Trace/artifact│◄───────────────┤ Stateful sim │
                               │ immutable     │ state/receipt   │ seeded DB    │
                               └──────┬────────┘                └──────────────┘
                                      │
                          ┌───────────┼──────────────┐
                          ▼           ▼              ▼
                   ┌──────────┐ ┌──────────┐  ┌──────────┐
                   │ State/   │ │ Trace/   │  │ Calibrated│
                   │ policy   │ │ tool     │  │ quality   │
                   │ grader   │ │ grader   │  │ judge     │
                   └────┬─────┘ └────┬─────┘  └────┬─────┘
                        └─────────────┼──────────────┘
                                      ▼
                               ┌──────────────┐
                               │ Stats/gates  │──► shadow → 1% canary → rollout
                               └──────────────┘
```

At 6,000 nightly trials and 45-second mean occupancy, a 30-minute execution target requires `3.33 starts/s` and about `150` active runners; provision 200 plus separate simulator-account locks and grader pools. Reserve reconciliation and status capacity. An unknown refund outcome is resolved from ledger state before reset. Infrastructure-invalid trials are paired and rerun under the declared policy; agent failures are not.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Measurement/security posture | Scalability ceiling |
|---|---|---|---|---|---|
| Static prompts + final-response judge | Low | Fast | Low | Misses state, policy, duplicates and tool recovery | High volume, low validity |
| **Stateful paired trials + deterministic state/trace gates + calibrated judge** | Medium-high | Nightly target feasible | High simulator, lineage and statistics burden | Strong construct validity and non-compensable safety | High with sharded environments |
| Human-only support simulations | Very high labor | Slow | Medium staffing/logistics | Rich judgment, weak repeatability and sparse tail coverage | Limited by raters |

**Decision rationale.** Refund and booking success is machine-checkable state, so deterministic outcome and policy gates lead. The calibrated judge is restricted to semantic communication quality. Paired repeated trials reduce item-difficulty noise and expose consistency, while shadow/canary evidence covers distribution and simulator gaps without giving the candidate unrestricted production authority.

### Scenario 2 - Shared multi-team enterprise evaluation platform

**Problem statement.** Design a platform for 20 teams, 50 suites, 10,000 tasks/day, multiple model providers, browsers, repository sandboxes, confidential traces, and a four-hour release-gate SLA. It must support replayable grading, independent human calibration, regional data rules, RPO zero for terminal evidence/decisions, and prevent hidden-test leakage or one suite starving others.

**Proposed architecture.** Git plus a signed registry holds evaluation cards, suite manifests, graders and analysis plans. Object storage versions datasets, traces and artifacts by hash. Temporal expands matrices into Kafka-backed durable shards. Kubernetes pools separate API-only, browser and code/data sandboxes; a secret broker issues synthetic task-scoped credentials. Candidate networks cannot reach hidden references or grader/release services. Generation checkpoints immutable artifacts; independent grader pools rescore by grader version. A statistics service computes task-clustered intervals and signed comparison reports. Policy enforces tenant/dataset/model region, raw-content access, retention and deletion. Release decisions use separation of duties and expiring exceptions.

```text
┌──────────────┐ signed suite ┌──────────────┐ durable matrix ┌──────────────┐
│ Team CI/API  ├─────────────►│ Registry +   ├───────────────►│ Temporal/    │
│ release      │◄─decision────┤ policy       │                │ Kafka        │
└──────────────┘              └──────────────┘                └──────┬───────┘
                                                                      │ fair queues
                       ┌───────────────────────┬───────────────────────┤
                       ▼                       ▼                       ▼
                ┌────────────┐          ┌────────────┐          ┌────────────┐
                │ API runner │          │ Browser    │          │ Sandbox    │
                │ pool       │          │ pool       │          │ pool       │
                └─────┬──────┘          └─────┬──────┘          └─────┬──────┘
                      └────────────────────────┼────────────────────────┘
                                               ▼
                                      ┌──────────────────┐
                                      │ Restricted object│
                                      │ store + lineage  │
                                      └───────┬──────────┘
                                              │ immutable replay
                         ┌────────────────────┼────────────────────┐
                         ▼                    ▼                    ▼
                  ┌────────────┐       ┌────────────┐       ┌────────────┐
                  │ State/code │       │ Model judge│       │ Human      │
                  │ graders    │       │ isolated   │       │ calibration│
                  └─────┬──────┘       └─────┬──────┘       └─────┬──────┘
                        └─────────────────────┼─────────────────────┘
                                              ▼
                                       ┌──────────────┐
                                       │ Stats/release│
                                       │ signed audit │
                                       └──────────────┘
```

Admission predicts token, browser, sandbox, judge and human budgets. Tenant/suite weighted queues and provider token buckets enforce fairness. A 1–5% canary validates environment health, missingness parity and cost before fan-out. At 10,000 tasks/day with two candidates and two trials, generation is 40,000 trials/day; a four-hour window averages 2.78 starts/s, but pools are sized from each class's p95 service time and burst, not that average. Grading can replay after generation, so judge outages do not destroy expensive trajectories.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security/governance | Scalability ceiling |
|---|---|---|---|---|---|
| Each team builds its own scripts | Duplicated hidden cost | Fast locally, inconsistent SLA | Low centrally, high enterprise-wide | Fragmented lineage, isolation and judge calibration | Poor cross-team fairness/reuse |
| One hosted-vendor dashboard | Subscription plus egress | Fast setup | Low-medium | Data residency, portability and opaque lifecycle risk | Vendor quota/feature ceiling |
| **Portable manifests + durable multi-pool platform** | Highest platform investment | Meets SLA with workload pools/replay | High | Strong isolation, lineage, policy and signed gates | High horizontal scale |

**Decision rationale.** Shared infrastructure is justified by confidential evidence, heterogeneous environments, replayable scoring, common judge calibration, and release integrity. Portable manifests and artifacts prevent dashboard lock-in. Separate pools preserve workload isolation, while a common statistics and governance layer prevents every team from inventing incompatible denominators and unsafe release rules.

## Interview Review

1. **What is being evaluated?** The model, harness, tools, environment and grader under a versioned condition, not the model name alone.
2. **What is the experimental unit?** Usually the task; repeated trials and trace events are nested observations.
3. **`pass@k` versus `pass^k`?** At least one success among candidates versus all repeated trials succeeding; capability selection versus consistency.
4. **When is exact trajectory matching valid?** When action/order is a real protocol or policy requirement, not merely one reference solution.
5. **How is a model judge trusted?** Hidden human calibration, confusion/agreement metrics, slice analysis, swap tests, pinning, abstention and periodic revalidation.
6. **Why report cost per success?** Cheap attempts may cause more retries, failures or review, increasing accepted-outcome cost.
7. **Why paired evaluation?** Candidate and baseline face the same task/seed, reducing variance from item difficulty and drift.
8. **How do hard gates differ from a composite?** Safety, critical slices and calibration cannot be compensated by style or average quality.

## Primary References

- [Agent evaluation guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- [OpenAI Evals API guide](https://developers.openai.com/api/docs/guides/evals)
- [OpenAI trace grading](https://developers.openai.com/api/docs/guides/trace-grading)
- [OpenAI graders and grader hacking](https://developers.openai.com/api/docs/guides/graders)
- [Google ADK evaluation](https://adk.dev/evaluate/)
- [UK AISI Inspect](https://inspect.aisi.org.uk/)
- [NIST automated benchmark practices draft](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.800-2.ipd.pdf)
- [NIST statistical models for generalized evaluation](https://www.nist.gov/publications/expanding-ai-evaluation-toolbox-statistical-models)
- [NIST AI TEVV](https://www.nist.gov/ai-test-evaluation-validation-and-verification-tevv)
- [Tau-bench and `pass^k`](https://proceedings.iclr.cc/paper_files/paper/2025/hash/1b126cc38b8638e07bef37e7b2bb72bf-Abstract-Conference.html)
- [ToolSandbox](https://aclanthology.org/2025.findings-naacl.65/)
- [Berkeley Function Calling Leaderboard](https://proceedings.mlr.press/v267/patil25a.html)
- [AgentBoard](https://proceedings.neurips.cc/paper_files/paper/2024/hash/877b40688e330a0e2a3fc24084208dfa-Abstract-Datasets_and_Benchmarks_Track.html)
- [PaperBench](https://openai.com/index/paperbench/)
- [HumanEval and the `pass@k` estimator](https://arxiv.org/abs/2107.03374)
- [HELM](https://arxiv.org/abs/2211.09110)
- [G-Eval](https://aclanthology.org/2023.emnlp-main.153/)
- [MT-Bench judge bias analysis](https://papers.neurips.cc/paper_files/paper/2023/hash/91f18a1287b398d378ef22505bf41832-Abstract-Datasets_and_Benchmarks.html)
- [Position bias in LLM judges](https://arxiv.org/abs/2406.07791)
- [OpenTelemetry trace API](https://opentelemetry.io/docs/specs/otel/trace/api/)
- [OpenTelemetry GenAI attributes](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)
- [Benchmark contamination study](https://proceedings.mlr.press/v267/sun25t.html)
- [AgentDojo](https://arxiv.org/abs/2406.13352)
- [NIST AI Risk Management Framework](https://www.nist.gov/itl/ai-risk-management-framework)
- [NIST Generative AI Profile](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)
