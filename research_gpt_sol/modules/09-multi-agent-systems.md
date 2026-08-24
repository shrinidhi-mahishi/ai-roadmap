# 09 - Multi-Agent Systems

**Scope:** Supervisor, worker, collaboration, and delegation.  
**Study goal:** Use multiple agents only when specialization, parallelism, context isolation, or independent review improves verified success enough to pay the coordination cost.

A multi-agent system contains separately configured agent instances with distinct task state, context, tools, or authority. Repeated samples are an ensemble; several prompt calls are a workflow. Multi-agent architecture adds value only when it also defines control ownership, delegation contracts, evidence exchange, conflict handling, durable task accounting, and termination.

## 1. System Topology & Data Flow

### Reference topology

```text
                                  CONTROL PLANE
┌──────────────────────────────────────────────────────────────────────────────┐
│ Identity/RBAC │ authenticated goal/constraints │ agent/tool/verifier registry│
│ global budgets/depth/concurrency │ policy/approval │ model/prompt versions   │
└────────────────────┬──────────────────────────────────────┬───────────────────┘
                     │ trusted run envelope                 │ capability policy
                     ▼                                      ▼
                            SUPERVISOR / CONTROL DATA PLANE
┌──────────────┐ admit       ┌──────────────┐ typed tasks ┌───────────────────┐
│ API/user     ├────────────►│ Supervisor   ├────────────►│ Durable scheduler │
│ approval     │◄─status─────┤ plan/gather  │◄─events─────┤ queue/leases      │
└──────────────┘             └──────┬───────┘             └─────────┬─────────┘
                                    │                               │ assignments
                            ┌───────▼────────────────────────────────▼───────┐
                            │ WORKER DATA PLANE                              │
                            │ researcher │ coder │ reviewer │ domain worker │
                            │ isolated context/workspace + scoped identity  │
                            └───────┬────────────────────────────────┬───────┘
                                    │ typed artifacts/status         │ tool proposals
                                    ▼                                ▼
                            ┌──────────────┐                  ┌────────────────┐
                            │ Artifact/    │                  │ Policy/tool/MCP│
                            │ blackboard  │                  │ gateway        │
                            └──────┬───────┘                  └───────┬────────┘
                                   │ claims/dissent/evidence          │ scoped effect
                                   ▼                                  ▼
                            ┌──────────────┐                  ┌────────────────┐
                            │ Conflict +  │                  │ APIs/browser/  │
                            │ final verify│                  │ code/data      │
                            └──────────────┘                  └────────────────┘

                                 PERSISTENCE LAYER
┌──────────────────────────────────────────────────────────────────────────────┐
│ Run/task DAG │ inbox/outbox │ leases/heartbeats │ budgets │ approvals       │
│ artifacts/provenance │ effect ledger/receipts │ checkpoints │ DLQ/repair    │
└───────────────────────────────────┬──────────────────────────────────────────┘
                                    ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ TELEMETRY: OTel trajectories │ coordination/quality/cost │ immutable audit │
└──────────────────────────────────────────────────────────────────────────────┘
```

The supervisor is a control-plane role, not the agent with every credential. Workers receive smaller typed assignments and attenuated capabilities. Domain systems remain authoritative; a blackboard, group chat, or worker report cannot authorize an external effect.

### End-to-end request flow

1. The API authenticates user, tenant, purpose, goal, constraints, and acceptance criteria. It selects a topology and pins agent, prompt, model, tool, policy, verifier, schema, and plan versions plus global call/token/tool/time/depth/concurrency budgets.
2. The supervisor decomposes only where task structure justifies it. Deterministic code validates criterion coverage, dependencies, duplicate-task fingerprints, worker capabilities, output schemas, and total descendant budget.
3. Each delegation envelope commits to the task ledger and outbox before publication. It binds task/parent/run/generation, named worker version, objective, inputs by digest, acceptance criteria, constraints, evidence policy, capability grant, budget, deadline, depth, idempotency key, and reply location.
4. A worker claims a fenced lease, validates the assignment, loads only task-specific context, and either accepts or returns a typed `blocked` result. It cannot expand the goal, grant, deadline, or child budget.
5. Tool proposals pass through a zero-trust MCP gateway that re-authorizes the concrete resource under the task-scoped identity. Side effects use durable intent, idempotency, receipts, approval, and authoritative readback.
6. Workers checkpoint and return bounded artifacts with claims, evidence, uncertainty, usage, and terminal status. The supervisor accepts at most one current-generation terminal result per logical task; late/stale attempts remain audit events.
7. A deterministic gather applies the declared join policy: all, quorum, best effort, first valid, or deadline. It preserves missing branches and dissent rather than converting partial fan-out into full success.
8. Fact conflicts go to independent evidence verification; artifact conflicts use ownership/CAS/three-way merge; plan conflicts use explicit objective/risk/cost criteria; authority conflicts are denied deterministically.
9. Synthesis consumes typed verified artifacts, not raw chat. Semantic success requires every acceptance criterion to pass an independent final verifier. Computational termination also requires zero unaccounted queued/running child tasks and no required approval.
10. OTel and immutable audit record task lineage, grants, versions, artifacts, evidence genealogy, conflicts, effects, approvals, usage, cancellations, and terminal reason without storing unnecessary private reasoning.

## 2. Core Mechanics & Algorithms

### 2.1 Topology selection

| Topology | Control | Best fit | Main weakness |
|---|---|---|---|
| Router -> specialist | code/one classifier | mutually exclusive domains | routing error, weak recovery |
| Supervisor -> workers | central coordinator | dynamic decomposition/synthesis | semantic bottleneck/single premise risk |
| Fan-out/gather | deterministic or supervisor | independent evidence/candidates | duplicate work and synthesis cost |
| Sequential pipeline | state machine | known SOP and review chain | accumulated error/latency |
| Handoff/swarm | active peer | conversational ownership changes | cycles, drift, unclear completion |
| Blackboard/group chat | selector/peer protocol | negotiation around shared artifacts | context growth/contention/poisoning |
| Debate/ensemble | moderator/vote | checkable ambiguous answer | correlated errors, token multiplication |
| Hierarchy | nested supervisors | large decomposable program | recursive explosion/cancellation difficulty |
| Event choreography | protocol/events | stable independent services | weak global progress visibility |

For `n` independent workers, fan-out/gather schedules in `O(n)` and ideal worker wall time is the maximum branch latency, while total model/tool cost remains additive. A full all-to-all collaboration sends `O(r n^2)` messages over `r` rounds. Shared-history prompt work can approach `O(r·n·history_size)`. Recursive branching factor `b` to depth `d` permits `(b^(d+1)-1)/(b-1)` descendants, so depth alone is insufficient; cap total descendants and live workers.

Use one strong agent plus deterministic tools as the baseline. Add workers only when an evaluation isolates value from parallel capacity, specialization, context isolation, or genuinely independent review. Sequential, tightly coupled work often pays coordination tax without critical-path reduction.

### 2.2 Supervisor and worker contracts

The supervisor owns authenticated goal/constraints, task DAG, registry selection, global budgets, leases/cancellation, join policy, conflict handling, synthesis, final verification, and escalation. An LLM may propose tasks; deterministic code owns their validity and scheduling.

A worker accepts one immutable contract:

```text
identity: run_id, task_id, parent_id, plan_generation, worker_id/version
work: objective, acceptance criteria, input artifact hashes, output schema
control: constraints, evidence policy, deadline, budget, depth remaining
authority: capability/resource grant, data classification, network policy
delivery: idempotency key, reply location, terminal status + actual usage
```

The worker operates only inside the grant, returns evidence-bearing artifacts or a structured failure, and does not redefine the parent goal. A child grant is a strict subset of the parent grant, and the sum of child reservations cannot exceed remaining parent budget. Invalid assignments are rejected; improvising missing permission or facts is not helpful behavior.

### 2.3 Collaboration and reducers

**Private context plus artifacts** is the default: workers receive a brief and immutable references, then return typed outputs. It minimizes token replay and blast radius. Supply explicit shared assumptions when branches depend on them.

**Shared conversation** is useful for ownership handoffs but requires filtered context, active-agent state, speaker/round bounds, and a rule preventing simultaneous contradictory handoffs.

**Blackboard collaboration** uses append-only namespaced claims/tasks/artifacts/reviews. Each claim has author/version/origin/evidence/confidence semantics and verification state. Several agents never overwrite one `answer` field.

**Pipeline collaboration** passes typed artifacts through a known SOP. It is preferable when roles are stable and dependencies dense. **Debate/ensemble** retains distinct candidates and evidence; role labels do not make errors independent.

Parallel reducers must be associative and commutative when arrival order is irrelevant, or sort by stable keys before a deterministic merge. Natural-language summarization is not a conflict-free replicated data type. Designate one merge owner for shared mutable code/data, or partition ownership so branches cannot collide.

### 2.4 Conflict resolution

| Conflict | Detection | Resolution |
|---|---|---|
| Fact | same predicate/scope, incompatible values | preserve claims and genealogy; retrieve primary evidence; independent verifier |
| Artifact | same base/version/path modified | owner partition, CAS, three-way merge, serial critical section |
| Plan | mutually exclusive actions/dependencies | supervisor scores explicit goal, constraints, cost, risk; reverify |
| Authority | requested action outside grant/policy | deterministic denial; votes and prose cannot override |

Voting is valid only for aggregable answers with sufficiently independent error sources. Weighting five agents that copied one source as five votes creates false consensus. Track evidence genealogy and group claims by independent source root before quorum. A verifier should be independent in mechanism or evidence and should not automatically possess mutation permissions.

### 2.5 Termination and convergence

Centralized success requires:

```text
all acceptance criteria independently verified
AND outstanding tasks = 0
AND active leases = 0
AND no required approval/conflict remains
```

Every task ends exactly once as `succeeded`, `failed`, `blocked`, `cancelled`, `expired`, or `budget_exhausted`. A model saying `DONE` is only a proposal. Hard ceilings cover elapsed time, messages/rounds, tokens, model/tool calls, live workers, descendants, depth, result bytes, repeated fingerprints, and rounds without verified progress.

For recursive delegation, creating a child atomically increments durable outstanding work; accepting exactly one terminal result atomically decrements it. Global budgets decrease monotonically across every child. A finite descendant cap plus deadlines and terminal transitions guarantees computational termination. Semantic completion remains separate and may end `blocked` even when all workers finish.

**System invariants**

- `accepted_terminal_results(task_id) <= 1` and only the current generation/fencing epoch is accepted.
- Child capabilities are subsets of the parent grant; child budgets sum within the parent remainder.
- A terminal parent has no unaccounted live/queued child lease; cancellation does not imply an external effect was undone.
- Verified completion maps every original criterion to independent evidence.
- Inter-agent prose cannot change control fields, approval, routing policy, identity, or budget.
- Shared-state reducers are deterministic under duplication and arrival-order changes.
- A failed/blocked/expired hard stop never becomes API success.

## 3. Token Economics & NFR Analysis

### 3.1 Cost per 1,000 runs

```text
C_1000 = Σ_agents(U·P_in + H·P_cache + W·P_write + O·P_out)/1,000,000
       + tools/sandboxes + state/artifacts/egress + verifier + human review

coordination_tax = supervisor + inter-agent messages + gather/synthesis
cost_per_verified_success = total MAS lifecycle cost / verified successes
```

**Illustrative point-in-time assumptions, 2026-08-21:** 1,000 supervisor-worker runs consume 10M uncached input tokens, 30M cached stable-role/schema/source-prefix reads, 120,000 cache-write tokens, and 5M output tokens across supervisor and workers. Tools, sandboxes, durable tasks/artifacts, and verification cost `$12/1K`; human review is excluded. Rates use the [current pricing reference](https://developers.openai.com/api/docs/pricing).

| Model tier | No prompt cache / 1K | Cached model cost / 1K | Total with $12 external |
|---|---:|---:|---:|
| `gpt-5.6-sol` | `(40M×$5)+(5M×$30)` = **$350.00** | `$50+$15+$0.75+$150` = **$215.75** | **$227.75** |
| `gpt-5.6-terra` | `(40M×$2)+(5M×$12)` = **$140.00** | `$20+$6+$0.30+$60` = **$86.30** | **$98.30** |
| `gpt-5.6-luna` | `(40M×$0.20)+(5M×$1.20)` = **$14.00** | `$2+$0.60+$0.03+$6` = **$8.63** | **$20.63** |

Cache only stable role instructions, schemas, registries, and shared source prefixes allowed by the provider contract. Tenant briefs, grants, private artifacts, current budgets, and task state stay outside shared prefixes. A result cache key includes tenant, task/constraint digest, worker/version, input artifact hashes, policy, and verifier; cancellation or artifact change invalidates it.

At `terra` rates, one worker using 3,000 input and 500 output tokens costs `$0.012`; four workers cost `$48/1K runs` before supervisor, tools, and synthesis. A single 8,000-input/1,200-output agent costs `$30.40/1K`. The extra `$17.60/1K` is justified only if parallel/specialized work raises verified value enough. A fan-out of eight doubles worker inference again even if wall time does not double.

If the illustrative cached system verifies 75% of attempted runs, `$98.30/1K attempted` becomes `$98.30/750×1,000 = $131.07/1K verified successes`. Include cancelled workers and discarded/duplicate artifacts. Vendor reports of multi-agent gains often spend different token budgets; compare strong single-agent, equal-budget single-agent, deterministic workflow, and MAS variants.

### 3.2 Latency and parallel efficiency

```text
T_run = T_plan + Σsequential stages + max(parallel worker critical paths)
      + T_queue/rate-limit + T_gather/conflict/synthesis/verify
      + T_retry/replan/approval

parallel_efficiency = equivalent_serial_work / (workers × parallel_wall_time)
```

These are internal design targets, not public framework benchmarks:

| Workload | p50 | p95 | p99 | Tail mitigation |
|---|---:|---:|---:|---|
| Router -> one specialist | ≤ 1.5 s | ≤ 5 s | ≤ 10 s | deterministic route fallback, small context |
| Four-worker fan-out/gather | ≤ 4 s | ≤ 15 s | ≤ 35 s | parallel leases, branch deadlines, bounded reports |
| Eight-worker research with conflict check | ≤ 7 s | ≤ 30 s | ≤ 60 s | staged gather, partial policy, source dedupe |
| Stateful handoff turn | ≤ 2 s | ≤ 8 s | ≤ 15 s | persisted active agent, filtered history |
| Human-approved multi-agent effect | machine ≤ 4 s | machine ≤ 15 s | machine ≤ 30 s | human wait separate; approval expiry/status |

Measure time to first useful artifact and verified final result. Break out supervisor queue/plan, worker queue/run, tool, gather, conflict, synthesis, verifier, and cancellation time. Track critical-path width, stragglers, wasted cancelled work, coordination tokens, messages per useful artifact, and time after last verified progress.

### 3.3 Capacity and back-pressure

At 100 runs/s with mean fan-out four, two model calls and three tools per worker, 20-KiB reports, and 12 seconds mean machine duration:

```text
worker tasks/s      = 100×4 = 400
worker model calls/s= 100×4×2 = 800
worker tool calls/s = 100×4×3 = 1,200
artifact ingress/s  = 100×4×20 KiB ≈ 7.8 MiB/s
active runs         = 100×12 = 1,200
```

Add supervisor plan/synthesis calls and verifier demand separately. Tail fan-out matters more than mean: one recursive run can exhaust shared quota. Use hierarchical quotas for tenant runs, per-run live/total descendants, model RPM/TPM, tool concurrency, queue age, artifact bytes, shared writes, retries, and human review.

Separate supervisor/synthesis capacity from workers so fan-out cannot starve control. Bulkhead worker types, providers, tenants, tools, and verifier pools. Back-pressure toward admission: shrink fan-out, choose a cheaper topology, delay batch work, return queued/partial status, or reject overload. Propagate parent deadlines and cancellation; do not let every worker retry a provider outage.

### 3.4 NFR scorecard

| NFR | Target/evidence | Trade-off |
|---|---|---|
| Availability | 99.9% run plane; 99.99% status/cancel/approval/task ledger | More durable control state increases cost/latency. |
| RPO | 0 run/task/effect/approval/budget/artifact metadata; ≤ 5 min aggregate telemetry | Artifact replication/storage can be large. |
| RTO | ≤ 15 min supervisor/task/effect plane; ≤ 60 min analytics | Registry/version recovery and repair tooling required. |
| Reliability | verified success, `pass^k`, false success, orphan/duplicate-effect rate | More agents add stochastic branch points. |
| Performance | p50/p95/p99, fan-out, queue/straggler/cancel spans | Parallel speed trades quota and total tokens. |
| Security | zero grant escalation/cross-tenant artifact/effect in adversarial suite | Context isolation and reference monitors reduce flexibility. |
| Compliance | agent/vendor inventory, lineage, residency, purpose, approval/accountability | Cross-provider workers expand subprocessors/data flows. |
| Operability | kill switch, registry rollback, DLQ/orphan/conflict repair, fault injection | More topologies multiply runbook/test surface. |

No vendor-neutral benchmark normalizes outcome, token/tool budget, p50/p95/p99, queues, security, and human review across supervisor, handoff, swarm, debate, and group chat. Production routing needs representative replay and repeated trials; HTTP/model-call success is not coordination health.

## 4. Distributed Resilience & Security

### 4.1 Durable supervisor-worker execution

```text
┌──────────────┐ tx/outbox  ┌──────────────┐ deliver   ┌──────────────┐
│ Run/task DB  ├───────────►│ Kafka/Temporal├─────────►│ Worker lease │
│ budget/join  │◄─events────┤ queue         │          │ + heartbeat  │
└──────┬───────┘            └──────────────┘          └──────┬───────┘
       │                                                      │ artifact/effect
       ▼                                                      ▼
┌──────────────┐ accept/CAS ┌──────────────┐          ┌──────────────┐
│ Inbox/result │◄───────────┤ Artifact store│          │ Tool gateway │
│ dedupe/fence │            │ digest/proven│          │ + ledger     │
└──────────────┘            └──────────────┘          └──────────────┘
```

Separate control state, message/artifact state, and domain state. Commit tasks and outbox transactionally. Temporal workflow code or an equivalent event-sourced controller remains deterministic; LLM/API calls run as retry-bounded activities. Workers claim `(task, attempt, fencing_epoch)`, heartbeat, checkpoint, and commit terminal result plus artifact through a durable protocol.

At-least-once delivery is expected. Deterministic task/event/artifact/effect IDs make replay idempotent. The supervisor accepts only the current generation/epoch and one terminal result. Poison assignments/messages enter a DLQ after bounded attempts. A reconciliation job finds task-counter leaks, orphan leases, missing artifacts, unknown effects, and children that outlive parents.

### 4.2 Partial failure, concurrency, and compensation

Declare joins before dispatch: all, quorum of independent valid sources, best effort with missing branches disclosed, first valid with cancellation, or deadline cutoff. A timeout is unknown, not failed. Cancellation does not prove a worker stopped or an effect reversed. Late results are audited and fenced from current synthesis.

Use disjoint artifact ownership or append-only records. Shared exclusive fields use CAS; unordered contributions use associative/commutative reducers; deterministic synthesis sorts stable IDs; code/data merges use one owner and three-way merge. Non-deterministic last-write summaries are prohibited.

Classify transient infrastructure/rate limits, invalid task/input, authorization, capability, policy, verifier rejection, conflict, and unknown effect. Retry only transient classes with full jitter, per-dependency breakers, global retry budget, and parent deadline. Apply bulkheads by provider/tool/worker/tenant. One layer owns retries.

For side effects, record intent and stable operation key before dispatch, reconcile timeout by provider readback, then commit receipt and verify state. Use Saga compensation for multi-service work; persist compensation and recognize irreversible pivots. Exact approval occurs before a pivot and expires on worker/args/generation/state change.

### 4.3 Failure taxonomy and containment

| Failure | Symptom | Containment |
|---|---|---|
| Supervisor omission | acceptance criterion has no task/evidence | coverage/DAG validation and final oracle |
| Wrong worker/routing | transfers, weak answers, capability mismatch | typed registry, routing eval, explicit `blocked`, baseline fallback |
| Recursive explosion | descendants grow faster than completions | inherited global depth/descendant/concurrency/cost limits |
| Synthesis loss | valid evidence or dissent disappears | bounded typed artifacts, genealogy-preserving reducer, final verifier |
| Echo chamber | repeated claim mistaken for independent support | group by source root; independent evidence/mechanism |
| Blackboard/edit collision | overwrite or nondeterministic final state | append-only claims, owner partition, CAS/three-way merge |
| Stale worker/late result | old generation mutates current output | fencing epoch, cancellation, inbox version check, reconciliation |
| Retry amplification | every child retries one provider outage | one retry owner, aggregate budget, breaker/bulkhead, DLQ |
| Task-counter leak | join waits forever for orphan work | atomic child increment/terminal decrement, lease sweeper, repair job |
| Context/economic blowout | chat replay or fan-out saturates quota | private briefs, report caps, topology downgrade, overload admission |

Detect progress from accepted artifacts, state changes, and verifier improvement, not message count. A hard ceiling produces a non-success terminal state; it never converts incomplete work into success.

### 4.4 Zero-Trust MCP/A2A and delegation security

```text
┌──────────────┐ typed task  ┌────────────────┐ mTLS/OAuth ┌──────────────┐
│ Supervisor   ├────────────►│ Agent gateway  ├───────────►│ Worker agent │
│ broad intent │             │ registry/policy│            │ scoped grant │
└──────┬───────┘             └───────┬────────┘            └──────┬───────┘
       │                              │                            │ tool proposal
       │                         signed task/card                   ▼
       │                              │                    ┌────────────────┐
       └──────────────────────────────┴───────────────────►│ Tool/MCP policy│
                                                           │ re-authorize   │
                                                           └────────────────┘
```

Agent identity, task identity, user identity, and service identity are distinct. Authenticate every request/server, validate certificate/audience, allowlist registry entries and pinned versions, and issue a short-lived task capability restricted by tenant, resources, operations, data class, deadline, and depth. Worker tools are a subset of the grant; re-authorization occurs at the concrete tool boundary.

Tool-level RBAC separates discover, read, propose, mutate, approve, verify, compensate, export, and delegate. A protocol `AUTH_REQUIRED` state is not enterprise authorization. Runtime discovery advertises capability but never authorizes it.

Validate inter-agent schema, size, sender, nonce/timestamp, task/run/generation binding, hashes, content type, and grant. Natural-language bodies cannot change control fields. Treat authenticated worker prose as untrusted: it may be compromised, hallucinated, or poisoned. Preserve origin/evidence genealogy through synthesis; supervisor agreement cannot launder a web instruction into policy.

### 4.5 Isolation, PII, supply chain, and audit

Give workers the minimum context and separate workspace, filesystem, network, secret, and memory scopes. Redact/tokenize PII before delegation; enforce tenant/purpose filters in retrieval and artifacts. Code/browser workers use ephemeral sandboxes and egress allowlists. Procedural prompts/policies are developer-controlled; worker lessons enter quarantine/review.

The PII path is `classify -> detect -> minimize/redact/tokenize -> authorize delegation -> worker/tool -> rehydrate only at allowed boundary -> audit/delete`. Apply it to briefs, messages, artifacts, blackboards, model/tool I/O, traces, evals, and backups. Cross-provider/vendor delegation requires residency, retention, training, encryption, subprocessors, deletion, incident, and contractual SLA review.

The agent registry binds owner, version, provider/model, approved prompt/tools, schemas, capability ceiling, data classes, network policy, deployment digest, SBOM/signature, eval status, and retirement. Changing descriptions can change model routing and requires regression/security evaluation.

Immutable audit includes run/task/parent/generation, principal, agent/prompt/model/tool versions, delegation grant, input/output hashes, evidence genealogy, tool/effects/receipts, approvals/policy, usage, retries, handoffs, conflicts, cancellations, and terminal reason. Hash-chain/sign WORM batches, restrict audit access, and omit secrets/private reasoning. Human approval shows exact action/resource, evidence/dissent, risk, compensation, and worker identity.

## 5. Production Enterprise Code

This Python 3.11 standard-library example implements a bounded two-worker research system. It validates attenuated capability grants, runs isolated assignments concurrently, uses typed evidence artifacts, accepts one current-generation result per task, detects fact conflicts without voting them away, verifies criterion coverage, and applies semantic plus computational termination. It includes structured logs, full-jitter retry, closed/open/half-open breakers, primary -> secondary -> deterministic `BLOCKED` worker fallback, global thread-safe call budgets, and cancellation. Run with `python multi_agent_supervisor.py`.

```python
from __future__ import annotations

import concurrent.futures
import json
import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence


class TransientError(RuntimeError):
    """A retryable worker-model failure."""


class PermanentError(RuntimeError):
    """A task, artifact, policy, or state failure."""


class CircuitOpen(TransientError):
    """A dependency is temporarily disabled."""


class TaskStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    EXPIRED = "expired"


class RunStatus(Enum):
    ACTIVE = "active"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Claim:
    predicate: str
    value: str
    source_id: str
    source_root: str


@dataclass(frozen=True)
class Artifact:
    task_id: str
    worker_id: str
    claims: tuple[Claim, ...]


@dataclass
class Task:
    task_id: str
    run_id: str
    generation: int
    assigned_worker: str
    objective: str
    criterion: str
    expected_predicate: str
    capability_grant: frozenset[str]
    deadline: float
    idempotency_key: str
    status: TaskStatus = TaskStatus.QUEUED
    artifact: Artifact | None = None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {"timestamp": time.time(), "level": record.levelname,
                 "message": record.getMessage()}
        for key in ("trace_id", "run_id", "task_id", "worker_id", "stage", "attempt"):
            if hasattr(record, key):
                value[key] = getattr(record, key)
        return json.dumps(value, separators=(",", ":"), sort_keys=True)


logger = logging.getLogger("multi_agent")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


class Budget:
    def __init__(self, max_model_calls: int):
        self._max = max_model_calls
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        with self._lock:
            if self._used >= self._max:
                return False
            self._used += 1
            return True

    @property
    def used(self) -> int:
        with self._lock:
            return self._used


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


class WorkerModel(Protocol):
    name: str

    def complete(self, task: Task, timeout_s: float) -> str: ...


class ModelChain:
    def __init__(self, models: Sequence[WorkerModel], budget: Budget):
        if len(models) < 2:
            raise ValueError("primary and secondary models required")
        self._models = tuple(models)
        self._budget = budget
        self._breakers = {model.name: Breaker() for model in models}

    @staticmethod
    def parse(raw: str, task: Task) -> Artifact:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PermanentError("worker returned invalid JSON") from exc
        if not isinstance(value, dict) or set(value) != {"task_id", "worker_id", "claims"}:
            raise PermanentError("artifact violates exact schema")
        if value["task_id"] != task.task_id or value["worker_id"] != task.assigned_worker:
            raise PermanentError("artifact identity mismatch")
        if not isinstance(value["claims"], list) or not value["claims"]:
            raise PermanentError("artifact has no claims")
        claims = []
        for item in value["claims"]:
            if not isinstance(item, dict) or set(item) != {
                "predicate", "value", "source_id", "source_root"
            } or any(not isinstance(v, str) or not v for v in item.values()):
                raise PermanentError("claim violates exact schema")
            claims.append(Claim(item["predicate"], item["value"],
                                item["source_id"], item["source_root"]))
        return Artifact(task.task_id, task.assigned_worker, tuple(claims))

    def run(self, task: Task, trace_id: str) -> Artifact | None:
        for model in self._models:
            breaker = self._breakers[model.name]
            for attempt in range(1, 3):
                remaining = task.deadline - time.monotonic()
                if remaining <= 0 or not self._budget.consume():
                    return None
                try:
                    breaker.before()
                    raw = model.complete(task, min(remaining, 3.0))
                    artifact = self.parse(raw, task)
                    breaker.success()
                    return artifact
                except CircuitOpen:
                    break
                except PermanentError:
                    breaker.failure()
                    break
                except (TimeoutError, ConnectionError, TransientError):
                    breaker.failure()
                    logger.warning("worker model failure", extra={
                        "trace_id": trace_id, "run_id": task.run_id,
                        "task_id": task.task_id, "worker_id": task.assigned_worker,
                        "stage": model.name, "attempt": attempt})
                    if attempt == 2:
                        break
                    delay = random.uniform(0.0, 0.02 * (2 ** (attempt - 1)))
                    if time.monotonic() + delay >= task.deadline:
                        return None
                    time.sleep(delay)
        return None


@dataclass(frozen=True)
class WorkerSpec:
    worker_id: str
    capabilities: frozenset[str]
    chain: ModelChain


class Supervisor:
    def __init__(self, registry: dict[str, WorkerSpec], budget: Budget,
                 parent_grant: frozenset[str], max_fanout: int = 4):
        self._registry = registry
        self._budget = budget
        self._parent_grant = parent_grant
        self._max_fanout = max_fanout
        self._accepted: dict[str, Artifact] = {}
        self._lock = threading.Lock()

    def _run_task(self, task: Task, trace_id: str) -> Artifact | None:
        spec = self._registry.get(task.assigned_worker)
        if (spec is None or not task.capability_grant.issubset(spec.capabilities)
                or not task.capability_grant.issubset(self._parent_grant)):
            task.status = TaskStatus.BLOCKED
            return None
        if time.monotonic() >= task.deadline:
            task.status = TaskStatus.EXPIRED
            return None
        task.status = TaskStatus.RUNNING
        artifact = spec.chain.run(task, trace_id)
        if artifact is None:
            task.status = TaskStatus.BLOCKED
            return None
        if not any(claim.predicate == task.expected_predicate
                   for claim in artifact.claims):
            task.status = TaskStatus.BLOCKED
            return None
        with self._lock:
            existing = self._accepted.get(task.task_id)
            if existing is not None:
                return existing
            self._accepted[task.task_id] = artifact
            task.artifact = artifact
            task.status = TaskStatus.SUCCEEDED
            return artifact

    @staticmethod
    def conflicts(artifacts: Sequence[Artifact]) -> dict[str, set[str]]:
        values: dict[str, set[str]] = {}
        for artifact in artifacts:
            for claim in artifact.claims:
                values.setdefault(claim.predicate, set()).add(claim.value)
        return {predicate: candidates for predicate, candidates in values.items()
                if len(candidates) > 1}

    def execute(self, run_id: str, trace_id: str, tasks: Sequence[Task],
                required_criteria: frozenset[str]) -> tuple[RunStatus, tuple[Artifact, ...]]:
        if not tasks or len(tasks) > self._max_fanout:
            return RunStatus.BLOCKED, ()
        if (len({task.task_id for task in tasks}) != len(tasks)
                or len({task.idempotency_key for task in tasks}) != len(tasks)
                or any(task.run_id != run_id for task in tasks)
                or len({task.generation for task in tasks}) != 1):
            return RunStatus.BLOCKED, ()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks))
        futures = {pool.submit(self._run_task, task, trace_id): task for task in tasks}
        artifacts = []
        try:
            for future, task in futures.items():
                try:
                    result = future.result(timeout=max(0.001, task.deadline-time.monotonic()))
                except (TimeoutError, concurrent.futures.TimeoutError):
                    task.status = TaskStatus.EXPIRED
                    result = None
                if result is not None:
                    artifacts.append(result)
        finally:
            for future in futures:
                future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)

        covered = frozenset(task.criterion for task in tasks
                            if task.status is TaskStatus.SUCCEEDED)
        terminal = all(task.status in {TaskStatus.SUCCEEDED, TaskStatus.BLOCKED,
                                       TaskStatus.EXPIRED} for task in tasks)
        status = (RunStatus.SUCCEEDED
                  if terminal and required_criteria.issubset(covered)
                  and not self.conflicts(artifacts) else RunStatus.BLOCKED)
        logger.info("run terminal", extra={
            "trace_id": trace_id, "run_id": run_id, "stage": status.value})
        return status, tuple(sorted(artifacts, key=lambda item: item.task_id))


class DemoModel:
    def __init__(self, name: str, available: bool, predicate: str,
                 value: str, source_id: str):
        self.name = name
        self._available = available
        self._predicate = predicate
        self._value = value
        self._source_id = source_id

    def complete(self, task: Task, timeout_s: float) -> str:
        if not self._available or timeout_s <= 0:
            raise TimeoutError("worker model unavailable")
        return json.dumps({"task_id": task.task_id,
                           "worker_id": task.assigned_worker,
                           "claims": [{"predicate": self._predicate,
                                       "value": self._value,
                                       "source_id": self._source_id,
                                       "source_root": self._source_id}]})


def worker(worker_id: str, predicate: str, value: str,
           source_id: str, budget: Budget) -> WorkerSpec:
    return WorkerSpec(worker_id, frozenset({"web.read:policy"}),
                      ModelChain((DemoModel(worker_id + ":primary", False,
                                            predicate, value, source_id),
                                  DemoModel(worker_id + ":secondary", True,
                                            predicate, value, source_id)), budget))


def main() -> None:
    run_id, trace_id = str(uuid.uuid4()), str(uuid.uuid4())
    deadline = time.monotonic() + 3.0
    budget = Budget(max_model_calls=8)
    registry = {
        "retention:v1": worker("retention:v1", "retention", "30_days",
                               "policy-7", budget),
        "security:v1": worker("security:v1", "encryption", "aes256",
                              "security-4", budget),
    }
    tasks = (
        Task("t1", run_id, 1, "retention:v1", "Find retention policy",
             "retention_verified", "retention",
             frozenset({"web.read:policy"}), deadline,
             run_id + ":t1"),
        Task("t2", run_id, 1, "security:v1", "Find encryption control",
             "encryption_verified", "encryption",
             frozenset({"web.read:policy"}), deadline,
             run_id + ":t2"),
    )
    supervisor = Supervisor(registry, budget, frozenset({"web.read:policy"}))
    status, artifacts = supervisor.execute(
        run_id, trace_id, tasks,
        frozenset({"retention_verified", "encryption_verified"})
    )
    print(json.dumps({"status": status.value, "model_calls": budget.used,
                      "tasks": {task.task_id: task.status.value for task in tasks},
                      "claims": [claim.__dict__ for artifact in artifacts
                                 for claim in artifact.claims]},
                     separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
```

The demo runs two private-context workers concurrently. Each primary model opens its own breaker after bounded failures, then a secondary returns a schema-validated artifact. The supervisor succeeds only after both criteria have terminal evidence and no conflicting predicate values. If both models for any worker fail, the deterministic result is `BLOCKED`; if a worker reports a different value for the same predicate, both claims remain and semantic success is denied. A grant outside either parent or worker capabilities also blocks before model content is exposed to tools.

## 6. Architectural System Design Scenarios

### Scenario 1 - Enterprise due diligence research

**Problem statement.** Design a due-diligence system processing 60 investigations/minute. Each investigation covers corporate identity, security, privacy, financial, sanctions, and jurisdictional evidence; requires primary-source citations and unresolved dissent; must return p95 within 3 minutes, retain seven-year lineage, isolate client matters, and cost no more than `$4` per verified report at normal depth.

**Proposed architecture.** Use a central supervisor with deterministic fan-out capped at six non-overlapping evidence dimensions. Workers receive client/matter-scoped read-only grants and private briefs, then return typed claims, dates, source URLs/digests, retrieval times, uncertainty, and gaps. Kafka/Temporal and PostgreSQL own tasks, leases, budgets, and joins; object storage owns artifacts. An independent citation/contradiction verifier resolves evidence genealogy; synthesis cannot discard dissent. Web/browser access passes through an allowlisted MCP gateway. Large fan-out is enabled only when marginal unique-source/verified-claim value justifies cost.

```text
┌──────────────┐ matter/auth ┌──────────────┐ fan-out≤6 ┌──────────────┐
│ Analyst      ├────────────►│ Supervisor + ├──────────►│ Private      │
│ review       │◄─report─────┤ task ledger  │           │ researchers  │
└──────────────┘             └──────┬───────┘           └──────┬───────┘
                                    │                          │ typed evidence
                                    ▼                          ▼
                             ┌──────────────┐           ┌──────────────┐
                             │ Temporal/   │           │ Artifact +   │
                             │ Kafka/DB    │           │ genealogy    │
                             └──────────────┘           └──────┬───────┘
                                                               │ verify/conflict
                                                               ▼
                                                        ┌──────────────┐
                                                        │ Citation +   │
                                                        │ synthesis    │
                                                        └──────────────┘
```

At one investigation/s and fan-out six, baseline is six worker tasks/s. If each performs eight browser/search calls, size 48 tool calls/s plus retries; cap result bytes and source count. Reserve supervisor/verifier pools so worker bursts cannot starve joins. A missing branch yields an explicit partial/blocked report according to investigation policy, never silent completeness.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security/quality | Scalability ceiling |
|---|---|---|---|---|---|
| Single strong research agent | Lowest coordination cost | Serial source exploration | Medium | One context/premise; easier isolation | Model context/tool serial limit |
| **Bounded supervisor fan-out + verifier** | Higher additive tokens | Parallel breadth; gather tail | High task/artifact runtime | Strong genealogy/dissent/tenant boundary | High with quota/bulkheads |
| Open group chat researchers | Highest context replay | Variable rounds/p99 | Medium demo, high tuning | Echo chamber/broadcast exposure | Poor under long shared history |

**Decision rationale.** The task has six independent evidence dimensions and valuable breadth, so bounded fan-out can reduce critical path and expand coverage. Private artifacts minimize cross-worker anchoring and data exposure; independent verification prevents consensus from becoming proof. The single-agent baseline remains the economic gate.

### Scenario 2 - Software change and incident response team

**Problem statement.** Design a system for 100 concurrent repository changes or incidents across 5,000 services. It must parallelize code mapping and diagnosis, avoid edit collisions, keep p95 diagnosis under 10 minutes, require executable tests and security review, preserve RPO 0 for approvals/deploy effects, and prohibit production credentials in coding workers.

**Proposed architecture.** Use a supervisor with read-only repository mapper/investigator workers, one branch-owning implementer, independent test/security reviewers, and a deployment verifier. Workers use isolated worktrees and egress-restricted sandboxes; one merge owner controls the mutable branch. Typed artifacts carry commit/tree hashes, commands, outputs, test evidence, and uncertainty. Temporal tracks tasks/generations/repairs; Git/object storage holds artifacts; an effect ledger and deployment MCP gateway bind approval, canary, receipt, and rollback. Failed verification creates one bounded repair assignment, not an unrestricted chat loop.

```text
┌──────────────┐ task/approval ┌──────────────┐ parallel read ┌──────────────┐
│ Developer/   ├──────────────►│ Supervisor   ├──────────────►│ Mapper +     │
│ oncall       │◄─status/diff──┤ task ledger  │◄─artifacts────┤ investigators│
└──────────────┘               └──────┬───────┘               └──────────────┘
                                      │ one merge owner
                                      ▼
                               ┌──────────────┐ artifact  ┌──────────────┐
                               │ Implementer │──────────►│ Test/security│
                               │ worktree    │◄─repair───┤ reviewers    │
                               └──────┬───────┘           └──────────────┘
                                      │ verified/approved
                                      ▼
                               ┌──────────────┐
                               │ Deploy MCP +│
                               │ canary/rollback│
                               └──────────────┘
```

At 100 concurrent runs with four read-only workers, expect up to 400 diagnostic leases before implement/review stages. Partition provider/model/tool quotas, cap per-repository work, and reserve test/deploy/rollback capacity. Stale-generation results cannot merge. A model outage preserves read-only artifacts for humans; test, policy, telemetry, or approval outage blocks deployment.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
|---|---|---|---|---|---|
| Single coding agent with all tools | Low coordination, high context | Serial diagnosis/change/test | Medium | Excess privilege/context coupling | Limited by one trajectory |
| **Supervisor + isolated specialists + merge owner** | Higher worker/control cost | Parallel diagnosis, staged critical path | High | Strong workspace/tool/approval separation | High with worker pools |
| Peer swarm on shared branch | Variable/high retries | Fast until collisions/thrashing | Medium | Weak ownership/termination | Low under write contention |

**Decision rationale.** Parallel read-only exploration and independent review justify specialists, while mutable code and deployment need single ownership and deterministic controls. The architecture gains breadth without allowing consensus, shared-branch races, or worker credentials to authorize production effects.

## Interview Review

1. **When are multiple agents justified?** Measured gain from parallelism, specialization, context isolation, or independent review over equal-budget baselines.
2. **What does a supervisor own?** Goal, task DAG, registry selection, global budgets, leases, joins, conflicts, synthesis, final verification, and termination.
3. **What crosses delegation?** A typed, versioned task with objective, criteria, artifacts, constraints, grant, budget, deadline, depth, schema, and idempotency.
4. **How are conflicts resolved?** Facts by evidence, artifacts by ownership/CAS/merge, plans by explicit objective/risk, authority by deterministic deny.
5. **Why is consensus insufficient?** Agents can share sources, models, prompts, and anchors; agreement may be correlated error.
6. **How does the system terminate?** Finite durable task accounting plus hard budgets, and a separate independent semantic completion oracle.

## Primary References

- [Anthropic multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Anthropic building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [LangChain multi-agent patterns](https://docs.langchain.com/oss/python/langchain/multi-agent/index)
- [LangChain subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)
- [LangChain handoffs](https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs)
- [Google ADK multi-agent patterns](https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/)
- [AutoGen teams](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/teams.html)
- [AutoGen termination](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/termination.html)
- [Magentic-One](https://arxiv.org/abs/2411.04468)
- [MAST failure taxonomy](https://arxiv.org/abs/2503.13657)
- [MultiAgentBench](https://arxiv.org/abs/2503.01935)
- [Scaling Agent Systems](https://arxiv.org/abs/2512.08296)
- [AgentDojo](https://arxiv.org/abs/2406.13352)
- [A2A specification](https://a2a-protocol.org/latest/specification/)
- [Temporal documentation](https://docs.temporal.io/)
- [Saga pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/saga)
- [OWASP Agentic Top 10](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/)
- [NIST AI RMF](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10)
