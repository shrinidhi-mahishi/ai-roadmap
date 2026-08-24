# 04 — Agent Architecture

**Scope:** ReAct, bounded loops, planning, authoritative state, and durable workflows.  
**Study goal:** Decide where model autonomy creates value, then constrain it with deterministic transitions, budgets, evidence, and recovery.

An agent is two coupled systems: a **probabilistic control policy** that proposes actions and a **deterministic runtime** that decides whether those proposals may change state. Put known business control flow in code. Spend model autonomy only where fresh observations make the path genuinely uncertain.

## 1. System Topology & Data Flow

### Reference topology

```text
                                      CONTROL PLANE
┌──────────────────────────────────────────────────────────────────────────────┐
│ Model/prompt registry │ workflow/graph versions │ tool schemas │ eval gates │
│ policy/RBAC/approvals │ budgets/quotas          │ rollout      │ kill switch│
└───────────────┬──────────────────────┬──────────────────────┬───────────────┘
                │ pinned version set   │ transition policy    │ limits
                ▼                      ▼                      ▼
                                       DATA PLANE
┌───────────┐  ┌──────────────┐  ┌──────────────────┐  ┌────────────────────┐
│ API/WAF   ├─►│ Admission +  ├─►│ Run coordinator  ├─►│ Planner / ReAct    │
│ identity  │  │ run creation │  │ state machine    │  │ decision proposal │
└───────────┘  └──────┬───────┘  └───┬──────────┬───┘  └─────────┬──────────┘
                      │              │          │                │
                      │      ┌───────▼──────┐   │        ┌───────▼────────┐
                      │      │ Postcondition│   │        │ Policy binder  │
                      │      │ verifier     │   │        │ plan/state ver │
                      │      └───────┬──────┘   │        └───────┬────────┘
                      │              │          │                │ command
                      │              │   ┌──────▼────────────────▼──────┐
                      │              │   │ Tool/MCP gateway             │
                      │              │   │ authz/approve/idempotency    │
                      │              │   └──────┬───────────────┬───────┘
                      │              │          │               │
                      │              │    ┌─────▼─────┐   ┌─────▼────────┐
                      │              │    │ API/search│   │ Browser/code │
                      │              │    │ workers   │   │ sandboxes    │
                      │              │    └─────┬─────┘   └─────┬────────┘
                      │              └──────────┴───────┬───────┘
                      │                  verified observation/evidence
                      │                                 │
                      └──────────────────── next loop ◄──┘
                                PERSISTENCE LAYER
┌──────────────────────────────────────────────────────────────────────────────┐
│ Run/event ledger │ state checkpoints │ plan/step versions │ intent/outbox    │
│ effect receipts  │ approvals         │ artifacts/provenance│ long-term store │
└───────────────────────────────┬──────────────────────────────────────────────┘
                                │ spans, events, budgets, digests
                                ▼
                        TELEMETRY / OBSERVABILITY
┌──────────────────────────────────────────────────────────────────────────────┐
│ OTel traces │ structured logs │ trajectory/quality/cost metrics │ WORM/SIEM │
└──────────────────────────────────────────────────────────────────────────────┘
```

The model can propose what to do. Only the coordinator can advance the run state machine. Identity, authorization, budget, approval, intent persistence, effect execution, postcondition verification, and terminal status therefore remain outside model text.

### Request flow

1. Admission authenticates the actor, applies tenant/risk quotas, creates `run_id/trace_id`, and pins model, prompt, policy, tool, reducer, and workflow graph versions.
2. The event store commits the immutable goal, actor, purpose, initial budget, deadline, and `RUN_CREATED` before work is accepted.
3. A deterministic router chooses the simplest adequate architecture: single call, fixed workflow with LLM nodes, bounded ReAct, planner-executor DAG, or approval workflow.
4. The coordinator first checks cancellation, deadline, every budget dimension, waiting conditions, and externally verifiable completion. Provider stop reasons never override these checks.
5. The context projector creates a bounded view of authoritative state: current plan, completed-step receipts, recent observations with provenance, unresolved work, and eligible tools. It cannot rewrite the ledger.
6. A planner or ReAct model proposes a typed action, plan update, wait, or completion claim. The binder validates it against the current `state_version`, `plan_version`, workflow node, permissions, and allowed transition.
7. Effectful intent and idempotency identity are committed before dispatch. The tool gateway re-authorizes the concrete resource, obtains point-of-action approval, executes under a short-lived credential, verifies/sanitizes the result, and persists the receipt.
8. The coordinator applies a deterministic reducer to the observation, increments monotonic budgets, checkpoints state, and loops. Parallel branches write namespaced outputs and join through conflict-aware reducers.
9. A completion claim becomes `SUCCEEDED` only after the postcondition validator records evidence. Otherwise the run continues or ends in a typed state such as `INCOMPLETE`, `STALLED`, `BUDGET_EXHAUSTED`, `WAITING_INPUT`, or `FAILED_DEPENDENCY`.
10. A worker crash acquires a new fenced lease, loads the checkpoint, reconciles any dispatched-without-result effect, reuses committed observations, and resumes the next admissible transition.

## 2. Core Mechanics & Algorithms

### 2.1 ReAct and bounded loops

ReAct interleaves a decision with environmental action and observation ([ReAct](https://arxiv.org/abs/2210.03629)):

```text
goal/state → decision summary → validated action → environment observation
    ▲                                                    │
    └──────── update state / replan / prove finish ──────┘
```

The production audit record needs an inspectable action rationale, validated arguments, observation provenance, state delta, and completion evidence. It does not require storing private chain-of-thought.

ReAct is justified when later steps depend on live results. A known refund, approval, payment, or document pipeline should be a fixed workflow with optional model nodes; asking the model to rediscover mandatory control flow increases latency and variance.

The coordinator uses independent monotonic limits:

```text
turns_used      ≤ max_turns
tool_calls_used ≤ max_tool_calls
fanout_used     ≤ max_parallel_branches
input/output    ≤ token_budgets
cost_used       ≤ currency_budget
now             ≤ deadline
no_progress     ≤ max_no_progress
replans_used    ≤ max_replans
```

A single iteration cap cannot represent spend, risk, parallel fan-out, or elapsed time. Count attempted calls even when they fail. Hash `{authoritative state digest, plan version, proposed action, canonical arguments}`; repeated hashes or unchanged progress measures detect oscillation before a hard turn cap.

```text
┌─────────┐ admit ┌─────────┐ propose ┌──────────┐ execute ┌───────────┐
│ CREATED ├──────►│ RUNNING ├────────►│ VALIDATING├───────►│ OBSERVING │
└─────────┘       └────┬────┘         └────┬─────┘         └─────┬─────┘
                       │                   │ deny                  │ checkpoint
           ┌───────────┼───────────┐       ▼                       └──────┐
           │           │           │  ┌──────────────┐                   │
           ▼           ▼           ▼  │BLOCKED_POLICY│                   │
     ┌──────────┐ ┌─────────┐ ┌─────────────┐ └──────────────┘            │
     │WAIT_INPUT│ │STALLED  │ │BUDGET/TIME  │                             │
     └────┬─────┘ └─────────┘ └─────────────┘                             │
          │ resume                                                         │
          └─────────────────────────────► RUNNING ◄────────────────────────┘
                                                │ verifier passes
                                                ▼
                                         ┌────────────┐
                                         │ SUCCEEDED  │
                                         └────────────┘
```

**Loop invariants**

- Terminal states never return to active except through a new linked retry/child run.
- Only the coordinator commits transitions; a model’s “done” is merely a proposal.
- Monotonic counters never decrease after retry/replay.
- A `SUCCEEDED` transition contains immutable postcondition evidence.
- Every observation references a committed tool result or immutable artifact.
- Waiting for input/approval is durable state and consumes no worker slot.

### 2.2 Planning algorithms

| Pattern | Operation | Best fit | Main risk |
|---|---|---|---|
| Inline ReAct | choose one next action from current evidence | short uncertain paths | myopia/oscillation |
| Plan-then-execute | emit ordered steps, then run them | stable decomposable work | stale plan |
| Receding horizon | plan a few steps, execute one, replan on material delta | volatile environments | planning overhead |
| DAG planner | typed dependencies; execute ready nodes | independent branches | fan-out and merge conflicts |
| Tree/search | generate, score, prune, backtrack | high-value objective search | exponential calls |
| Evaluator-optimizer | generate, score criteria, revise | objectively judgeable artifacts | correlated evaluator bias |
| Reflection memory | store feedback lesson for a later attempt | repeated tasks with reliable feedback | false self-diagnosis |

A typed step contains `step_id`, dependencies, expected preconditions, allowed tool class, success predicate, risk/effect class, compensation metadata, and status. Replanning creates `plan_version + 1`, records the invalidating evidence, and carries forward a completed step only if its postcondition remains true.

For a DAG `G=(V,E)`, topological scheduling is `O(|V|+|E|)`. A node becomes ready only when every dependency is `SUCCEEDED`. Parallel branches write `branch_id`-scoped results; a deterministic reducer merges them. A write conflict or unsatisfied dependency is an explicit failure, not last-writer-wins prose.

Tree search with breadth `b` and depth `d` can expand `O(b^d)` candidates and memory without pruning. Bound depth, frontier width, retained candidates, evaluator calls, and total token/currency cost. Use objective environment scoring; model reflection alone is not an independent verifier.

Planning pays only when:

```text
planner_cost + planner_latency + merge/replan_cost
    < avoided_failure_cost + avoided_execution_cost + quality_value
```

Plan-and-Solve, Tree of Thoughts, and Reflexion show task-specific gains, not universal production multipliers ([Plan-and-Solve](https://aclanthology.org/2023.acl-long.147/), [Tree of Thoughts](https://proceedings.neurips.cc/paper/2023/hash/271db9922b8d1f4dd7aaef84ed5ac703-Abstract.html), [Reflexion](https://papers.nips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html)).

### 2.3 State is not context

| State plane | Examples | Consistency/authority |
|---|---|---|
| Run identity | tenant, actor, goal, pinned versions | immutable and durable |
| Workflow snapshot | node, plan/state version, budgets, pending work | strongly consistent per run |
| Effect ledger | intents, keys, attempts, receipts | append-only recovery/audit authority |
| Observation/artifact | docs, screenshots, outputs | immutable by digest with provenance/TTL |
| Context projection | selected events, summaries, active plan | disposable derivation |
| Long-term memory | approved preferences/facts/lessons | separate scope, retention, correction |
| Completion evidence | tests, state assertions, approvals | immutable and linked to terminal state |

Every run has optimistic `state_version`. A commit succeeds only from the version read. Exclusive execution uses an expiring lease plus monotonically increasing fencing token so a partitioned worker cannot later overwrite a new owner. Never hold a database/distributed lock over inference, tools, human wait, or network calls.

Event sourcing makes state reproducible:

```text
state_n = fold(reducer_version, initial_state, events_1..n)
```

The fold is `O(n)` from the origin; periodic snapshots reduce recovery to `O(events since snapshot)`. Reducers must be deterministic for the pinned version. Tool corrections append new events; they do not edit prior receipts. Context summaries are materialized projections and cannot alter identity, approval, budgets, or committed facts.

### 2.4 Workflows and graph composition

A workflow uses code-defined paths; an agent chooses some path dynamically. Useful compositions are prompt chaining with gates, deterministic routing, independent parallelization or voting, orchestrator-workers for discovered subtasks, and evaluator-optimizer cycles ([building effective agents](https://www.anthropic.com/engineering/building-effective-agents)).

Use a deterministic outer graph and model-directed inner nodes:

```text
authenticate → classify → [known workflow]
                           ├─► LLM fact-gathering/ReAct node
                           ├─► deterministic policy gate
                           ├─► durable approval interrupt
                           ├─► idempotent/compensable execution
                           └─► external postcondition verifier
```

Durable interrupts checkpoint and release the worker. Resume may restart node code, so every side effect before an interrupt must be idempotent. Graph replay semantics differ by framework: know whether nodes, pending parallel writes, model calls, or APIs re-execute. A workflow service’s “exactly once” execution label does not make arbitrary external effects exactly once.

Effects fall into four classes: read-only, idempotent mutation, compensable mutation, and irreversible/high-impact. Compensation is a durable domain workflow with its own idempotency, failure states, and human playbook; it is not a database rollback across independent systems.

## 3. Token Economics & NFR Analysis

### 3.1 Cost per 1,000 runs and per success

For model calls `i=1..n`:

```text
C_1000 = Σ_i(U_i·P_in + H_i·P_cache + W_i·P_write + O_i·P_out)/1,000,000
       + tools + workflow/storage + sandbox + observability
cost_per_success = total_run_cost / successful_runs
wasted_cost_rate = failed_cancelled_duplicate_cost / total_cost
```

**Assumptions as of 2026-08-21:** across 1,000 runs, variable turns produce 12M uncached input, 20M cached-prefix reads, and 3M output tokens. Ten distinct 8,000-token stable prefixes are each written once, so cache writes total 80,000 tokens. No tools or infrastructure are included. The no-cache baseline bills all 32M input tokens at the uncached rate. Point-in-time rates come from the [current pricing reference](https://developers.openai.com/api/docs/pricing).

| Tier | Input/cache read/write/output per 1M | No cache / 1K | Cached trajectory / 1K | Saving |
|---|---|---:|---:|---:|
| `gpt-5.6-sol` | $5 / $0.50 / $6.25 / $30 | `$160+$90` = **$250.00** | `$60+$10+$0.50+$90` = **$160.50** | 35.8% |
| `gpt-5.6-terra` | $2 / $0.20 / $2.50 / $12 | `$64+$36` = **$100.00** | `$24+$4+$0.20+$36` = **$64.20** | 35.8% |
| `gpt-5.6-luna` | $0.20 / $0.02 / $0.25 / $1.20 | `$6.40+$3.60` = **$10.00** | `$2.40+$0.40+$0.02+$3.60` = **$6.42** | 35.8% |

The research brief's separate `gpt-5.4` illustration excludes cache-write tokens: `(12M×$2.50)+(20M×$0.25)+(3M×$15) = $80/1K runs` ([model page](https://developers.openai.com/api/docs/models/gpt-5.4)). A real budget must add writes, tools, storage, and failed work using the selected endpoint's current prices. Stable system prompts, eligible tool schemas, and workflow instructions should precede volatile state. Cached tokens still occupy context and long trajectories continually add observations.

A `terra` planner call using 2,000 uncached input and 1,000 output tokens costs `$0.016/run`, or **$16/1K runs**. It is economically justified only if it reduces downstream retries, tools, human repair, or failure loss by more than $16/1K plus its latency. If 800 of 1,000 runs succeed, the cached `terra` model cost is `$64.20/800 = $0.08025` per successful run, versus `$0.06420` per submitted run.

### 3.2 Latency and reliability targets

```text
serial latency = Σ(model + queue + tool + checkpoint)
parallel stage = dispatch + max(branch latency) + deterministic join
end-to-end     = admission + Σ(critical-path stages) + human wait
```

| Architecture | p50 | p95 | p99 | Tail mitigation |
|---|---:|---:|---:|---|
| Fixed workflow, one LLM node | ≤ 1.5 s | ≤ 4 s | ≤ 8 s | Cached prompt, bounded tool result, pinned path. |
| Bounded ReAct, ≤ 4 turns | ≤ 4 s | ≤ 12 s | ≤ 25 s | Per-turn deadline, no-progress detector, checkpoint/partial result. |
| Planner + four-branch DAG | ≤ 6 s | ≤ 20 s | ≤ 45 s | Fan-out cap, branch bulkhead, early pruning, join timeout. |
| Approval workflow machine time | ≤ 3 s | ≤ 10 s | ≤ 20 s | Durable pause; measure human wait separately. |

Reliability compounds across required steps. In the simplified independent model, `p^n` with `p=0.98` and `n=20` is `0.98^20 ≈ 66.8%`. Real steps are correlated, but the example explains why unnecessary turns reduce whole-run reliability. Report repeated `pass^k`, verified final business state, false-success rate, recovery success, and duplicate effects rather than only average answer score.

### 3.3 Throughput and back-pressure

```text
model_calls/s = admitted_runs/s × mean_model_turns
tool_calls/s  = admitted_runs/s × mean_tool_calls
active_runs   = admitted_runs/s × mean_machine_duration_s
branch_calls  = DAG_runs/s × mean_parallel_branches
```

At `50 runs/s`, six model turns, four tools, and 12 seconds mean machine duration, capacity is `300 model calls/s`, `200 tool calls/s`, and about `600 active machine runs`. The Section 3.1 trajectory averages 32,000 input and 3,000 output tokens/run, so total observed input is `96M TPM`, uncached input is `36M TPM`, and output is `9M TPM`. If 5 runs/s fan out to four branches, reserve another 20 branch slots/calls per second plus join capacity.

Queue by weighted work, not request count: estimated remaining model calls, uncached tokens, tool operations, branch width, and sandbox time. Enforce tenant concurrency, global model/token rate, tool quotas, maximum queue age, and per-risk-class bulkheads. Reserve worst-case remaining budget before mutations. Under pressure, disable low-priority planning branches and evaluator passes before transactional tool/status/approval workers; then route recognized intents to a smaller fixed workflow, change mutations to draft-only, allow reads, queue, or fail closed.

### 3.4 NFR scorecard and trade-offs

| Requirement | Target | Consequence / trade-off |
|---|---|---|
| Availability | 99.9% run execution; 99.99% status/approval/cancel API | Read-only partials may degrade; policy, ledger, approval, and mutation verification fail closed. |
| Durability | 100% acknowledged transitions and effects are checkpointed | Per-step persistence adds latency but enables exact recovery position. |
| RPO | 0 for goal, state, budgets, approval, intent, receipt; ≤ 5 min metrics | Strong replicated authority; derived context/telemetry rebuildable. |
| RTO | ≤ 15 min coordinator/workflow; ≤ 60 min analytics | Warm control plane and version registry matter more than disposable workers. |
| Quality | False success < 0.1%; postcondition and task thresholds by risk; pass^k release gate | Strict verification can increase incomplete runs but prevents fluent false closure. |
| Security | No unauthorized effect; every effect complete-mediated at point of action | Narrow nodes/tools and blocking guards reduce autonomy and may add approval latency. |
| Audit | Reproducible transition/plan/effect chain with immutable evidence | Raw observations aid forensics but increase privacy risk; store digests plus governed evidence. |
| Compliance | Owner, purpose, impact, residency, retention, eval, incident, rollback per workflow | Version pinning/migrations add operations but prevent silent behavior skew. |

## 4. Distributed Resilience & Security

### 4.1 Durable workflow execution

Persist `run_created`, every proposed/accepted/rejected transition, plan version, budget debit, tool intent, approval, dispatch, receipt, observation, checkpoint, compensation, and terminal evidence. Replay folds pinned events; it reuses recorded model/tool results rather than invoking nondeterministic dependencies while reconstructing state.

Temporal, LangGraph, and Step Functions have different checkpoint/replay/interrupt semantics. LangGraph can persist step state and completed parallel writes, but replay after a selected checkpoint may re-execute downstream nodes. An interrupt can restart its node on resume, so pre-interrupt effects must be idempotent. Step Functions workflow execution guarantees vary between Standard and Express; none turn every downstream effect into exactly once.

For every mutation: persist actor, canonical command, plan/state version, authorization, deadline, and idempotency key; dispatch through outbox/durable task; make the destination deduplicate; store the receipt; reconcile an ambiguous crash before reissue. Partition events by `run_id`, use optimistic `state_version`, and lease exclusive executors with fencing tokens. Parallel branches write namespaces, and a deterministic reducer rejects conflicting mutations.

### 4.2 Failure, retry, and compensation

| Class | Examples | Response |
|---|---|---|
| Transient | 429/503, timeout before known effect, worker loss | One retry owner; exponential backoff/full jitter, `Retry-After`, deadline and aggregate retry budget. |
| Permanent | invalid plan/schema, policy denial, insufficient funds, bad transition | Do not network-retry; replan only if new admissible information exists. |
| Poison event/run | reducer crash, malformed checkpoint, repeated injected observation | Durable attempt count, DLQ/quarantine, preserve digest, human/version repair. |
| Ambiguous effect | dispatch without receipt after crash | Query destination by key/state; resume only after reconciliation. |
| Stalled loop | repeated action/state hash or zero progress | Typed `STALLED`, preserve evidence, request input or operator review. |
| Parallel conflict | state-version/reducer conflict | Keep branch outputs; deterministic retry/join or replan from new version. |
| Partial saga | compensation or irreversible step fails | Durable compensation state, alert, resumable manual playbook. |
| Version skew | old run resumes under incompatible graph/tool/reducer | Pin version; explicit migration adapter; replay/canary test before activation. |

Circuit breakers are separate by provider/model/region/tool. `CLOSED` records transient failure, `OPEN` fails fast, and limited `HALF_OPEN` probes test recovery. Model replanning is not a network retry and consumes its own budget. Compensation must be domain-specific, idempotent, resumable, and allowed to require human intervention ([compensating transaction](https://learn.microsoft.com/en-us/azure/architecture/patterns/compensating-transaction)).

Recovery order is: fenced lease, checkpoint, reconcile dispatched-without-result effects, reuse committed observations, then continue the next admissible transition. Degrade by removing branches/evaluators, selecting a pinned smaller fixed workflow, draft-only mutations, reads, durable queue, then resumable typed failure. Never silently swap a high-risk in-flight run to an unpinned graph/model/tool version.

### 4.3 Zero-Trust MCP, guardrails, and state poisoning

```text
┌──────────────┐ proposal ┌────────────────┐ allowed edge ┌──────────────┐
│ Model node   ├─────────►│ Coordinator +  ├─────────────►│ Tool/MCP     │
│ no authority │          │ policy binder  │              │ proxy        │
└──────┬───────┘          └───────┬────────┘              └──────┬───────┘
       │ untrusted plan            │ state/plan version           │ mTLS/token
       ▼                           ▼                              ▼
┌──────────────┐           ┌──────────────┐               ┌──────────────┐
│ Context      │◄──────────┤ Event ledger │               │ MCP server / │
│ projection   │           │ authority    │               │ sandbox/API  │
└──────────────┘           └──────┬───────┘               └──────┬───────┘
                                  │ hostile observation            │
                                  └──────────────┬─────────────────┘
                                                 ▼
                                          ┌──────────────┐
                                          │ Sanitize +   │
                                          │ verify       │
                                          └──────────────┘
```

- User goals are untrusted intent; retrieved pages, messages, errors, branch outputs, summaries, and peer messages are untrusted observations; model plans are proposals.
- Authenticate actor/workload and MCP server, encrypt transport, allowlist capabilities/egress, and expose only tools required at the active workflow node.
- Compute RBAC/ABAC from tenant, actor, purpose, node, resource, risk, plan/state version, approval, and budget. Re-authorize immediately before effect and mint a short-lived audience credential.
- Blocking admission checks run before an effectful loop. Tool guardrails run per call, result sanitization after every observation, and final quality/disclosure checks before response. A top-level input/output guardrail does not mediate internal tools automatically.
- External text cannot change system policy, identity, credentials, budget, approval, workflow state, or authoritative facts. Red-team poisoned documents, tool descriptions, memory, summaries, and branch joins with AgentDojo-style cases.

### 4.4 PII and immutable audit

```text
┌────────────┐   ┌────────────────┐   ┌──────────────┐   ┌──────────────┐
│ Input/state├──►│ regex + NER +  ├──►│ block/tokenize├──►│ context/tool │
│ observation│   │ schema detector│   │ /mask         │   │ scoped use   │
└────────────┘   └───────┬────────┘   └──────┬───────┘   └──────┬───────┘
                         │ detector/version   │ vault map          │
                         └────────────────────┴─────────────►┌─────▼────────┐
                                                            │ WORM audit  │
                                                            └──────────────┘
```

Propagate tenant, actor, purpose, and scopes outside model arguments. Encrypt events, snapshots, artifacts, and long-term memory with tenant keys and row-level controls. Govern checkpoint, trace, memory, and artifact retention separately. Bearer tokens never enter context or plaintext traces.

Audit events record `run_id/trace_id`, tenant pseudonym, node, state/plan/event versions, model/prompt/tool/graph/reducer versions, call ID, canonical command digest, policy, approval, idempotency key, receipt/result digest, parent event, token/cost counters, and timing. Sign/hash-chain batches to WORM storage and log access. General telemetry defaults to metadata and hashes; raw content lives in a separate shorter-retention evidence store. Traces are observability, not transaction authority ([OpenTelemetry GenAI](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)).

## 5. Production Enterprise Code

This executable Python 3.11 standard-library example implements a bounded ReAct coordinator with typed states, monotonic budgets, state versions, an append-only event store, repeated-action detection, external completion verification, full-jitter retries, closed/open/half-open breakers, primary-to-secondary model fallback, deterministic failure degradation, correlation-ID logs, and a retrying tool. Run with `python bounded_agent.py`.

```python
from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Protocol, Sequence


class TransientError(RuntimeError):
    """Retryable dependency failure."""


class PermanentError(RuntimeError):
    """Invalid decision, transition, or policy input."""


class CircuitOpen(TransientError):
    """Dependency is failing fast during recovery."""


class BudgetExceeded(PermanentError):
    """A monotonic run budget cannot admit another dependency attempt."""


class RunStatus(Enum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    INCOMPLETE = "incomplete"
    STALLED = "stalled"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TIMED_OUT = "timed_out"
    FAILED_DEPENDENCY = "failed_dependency"


TERMINAL = {
    RunStatus.SUCCEEDED, RunStatus.INCOMPLETE, RunStatus.STALLED,
    RunStatus.BUDGET_EXHAUSTED, RunStatus.TIMED_OUT,
    RunStatus.FAILED_DEPENDENCY,
}


@dataclass
class Budget:
    max_turns: int
    max_model_attempts: int
    max_tool_calls: int
    max_tokens: int
    max_cost_micros: int
    max_no_progress: int
    deadline: float
    turns: int = 0
    model_attempts: int = 0
    tool_calls: int = 0
    tokens: int = 0
    cost_micros: int = 0
    no_progress: int = 0

    def exhausted(self) -> bool:
        return (
            self.turns >= self.max_turns
            or self.model_attempts >= self.max_model_attempts
            or self.tool_calls >= self.max_tool_calls
            or self.tokens >= self.max_tokens
            or self.cost_micros >= self.max_cost_micros
        )


@dataclass(frozen=True)
class Observation:
    source_id: str
    data: dict[str, object]
    receipt: str


@dataclass
class RunState:
    run_id: str
    trace_id: str
    goal: str
    status: RunStatus
    state_version: int
    plan_version: int
    budget: Budget
    observations: dict[str, Observation] = field(default_factory=dict)
    action_hashes: set[str] = field(default_factory=set)
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelReply:
    raw: str
    input_tokens: int
    output_tokens: int
    cost_micros: int


@dataclass(frozen=True)
class Decision:
    kind: str
    tool: str | None
    arguments: dict[str, object]
    summary: str

    @classmethod
    def parse(cls, raw: str) -> "Decision":
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PermanentError("model returned invalid JSON") from exc
        if not isinstance(value, dict) or value.get("kind") not in {"act", "finish"}:
            raise PermanentError("decision violates schema")
        if value["kind"] == "finish":
            if set(value) != {"kind", "summary"} or not isinstance(value["summary"], str):
                raise PermanentError("finish decision violates exact schema")
            return cls("finish", None, {}, value["summary"])
        if set(value) != {"kind", "tool", "arguments", "summary"}:
            raise PermanentError("action decision violates exact schema")
        if not isinstance(value["tool"], str) or not isinstance(value["arguments"], dict):
            raise PermanentError("action fields have invalid types")
        if not isinstance(value["summary"], str):
            raise PermanentError("decision summary must be text")
        return cls("act", value["tool"], value["arguments"], value["summary"])


class Model(Protocol):
    name: str

    def propose(self, state_json: str, timeout_s: float) -> ModelReply: ...


class Tool(Protocol):
    name: str

    def execute(self, arguments: dict[str, object], timeout_s: float) -> Observation: ...


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {"timestamp": time.time(), "level": record.levelname,
                 "message": record.getMessage()}
        for item in ("trace_id", "run_id", "model", "tool", "status", "attempt"):
            if hasattr(record, item):
                value[item] = getattr(record, item)
        return json.dumps(value, separators=(",", ":"), sort_keys=True)


logger = logging.getLogger("bounded_agent")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, threshold: int = 3, recovery_s: float = 10.0):
        if threshold < 1 or recovery_s <= 0:
            raise ValueError("invalid breaker configuration")
        self._threshold = threshold
        self._recovery_s = recovery_s
        self._failures = 0
        self._opened_at = 0.0
        self._probe = False
        self._state = BreakerState.CLOSED
        self._lock = threading.Lock()

    def before(self) -> None:
        with self._lock:
            if self._state is BreakerState.OPEN:
                if time.monotonic() - self._opened_at < self._recovery_s:
                    raise CircuitOpen("circuit is open")
                self._state = BreakerState.HALF_OPEN
            if self._state is BreakerState.HALF_OPEN:
                if self._probe:
                    raise CircuitOpen("half-open probe already running")
                self._probe = True

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._probe = False
            self._state = BreakerState.CLOSED

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._probe = False
            if self._state is BreakerState.HALF_OPEN or self._failures >= self._threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()


class EventStore:
    def __init__(self):
        self._events: list[dict[str, object]] = []
        self._lock = threading.Lock()

    def append(self, state: RunState, event_type: str,
               payload: dict[str, object], expected_version: int) -> None:
        with self._lock:
            if state.state_version != expected_version:
                raise PermanentError("optimistic state-version conflict")
            self._events.append({"event_id": str(uuid.uuid4()),
                                 "run_id": state.run_id,
                                 "version": expected_version + 1,
                                 "type": event_type, "payload": payload})
            state.state_version += 1

    @property
    def events(self) -> tuple[dict[str, object], ...]:
        with self._lock:
            return tuple(self._events)


class ModelChain:
    def __init__(self, models: Sequence[Model]):
        if not models:
            raise ValueError("at least one model is required")
        self._models = tuple(models)
        self._breakers = {model.name: CircuitBreaker() for model in models}

    def propose(self, state_json: str, deadline: float,
                state: RunState) -> ModelReply | None:
        for model in self._models:
            breaker = self._breakers[model.name]
            for attempt in range(1, 4):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                if state.budget.model_attempts >= state.budget.max_model_attempts:
                    raise BudgetExceeded("model-attempt budget exhausted")
                state.budget.model_attempts += 1
                try:
                    breaker.before()
                    reply = model.propose(state_json, min(remaining, 5.0))
                    breaker.success()
                    Decision.parse(reply.raw)
                    return reply
                except PermanentError:
                    break
                except CircuitOpen:
                    break
                except (TimeoutError, ConnectionError, TransientError) as exc:
                    breaker.failure()
                    logger.warning("transient model failure",
                                   extra={"trace_id": state.trace_id,
                                          "run_id": state.run_id,
                                          "model": model.name,
                                          "attempt": attempt})
                    if attempt == 3:
                        break
                    delay = random.uniform(0.0, 0.1 * (2 ** (attempt - 1)))
                    if delay >= deadline - time.monotonic():
                        return None
                    time.sleep(delay)
        return None


class Coordinator:
    def __init__(self, store: EventStore, models: ModelChain, tools: Sequence[Tool]):
        self._store = store
        self._models = models
        self._tools = {tool.name: tool for tool in tools}
        self._tool_breakers = {tool.name: CircuitBreaker() for tool in tools}

    @staticmethod
    def _project(state: RunState) -> str:
        return json.dumps({"goal": state.goal,
                           "plan_version": state.plan_version,
                           "observations": {key: asdict(value)
                                            for key, value in state.observations.items()}},
                          separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _verified(state: RunState) -> tuple[bool, tuple[str, ...]]:
        order = state.observations.get("order")
        if order and order.data.get("status") in {"paid", "shipped"}:
            return True, (order.receipt,)
        return False, ()

    def _run_tool(self, state: RunState, decision: Decision) -> Observation:
        if decision.tool not in self._tools:
            raise PermanentError("tool is not allowed in this workflow node")
        if decision.tool != "lookup_order" or set(decision.arguments) != {"order_id"}:
            raise PermanentError("tool arguments violate the node contract")
        order_id = decision.arguments["order_id"]
        if not isinstance(order_id, str) or not order_id.startswith("ord_"):
            raise PermanentError("invalid order ID")
        tool = self._tools[decision.tool]
        breaker = self._tool_breakers[tool.name]
        for attempt in range(1, 4):
            remaining = state.budget.deadline - time.monotonic()
            if remaining <= 0:
                raise TransientError("tool deadline exhausted")
            if state.budget.tool_calls >= state.budget.max_tool_calls:
                raise BudgetExceeded("tool-attempt budget exhausted")
            state.budget.tool_calls += 1
            try:
                breaker.before()
                result = tool.execute(decision.arguments, min(remaining, 3.0))
                breaker.success()
                return result
            except CircuitOpen as exc:
                raise TransientError("tool circuit is open") from exc
            except (TimeoutError, ConnectionError, TransientError) as exc:
                breaker.failure()
                logger.warning("transient tool failure",
                               extra={"trace_id": state.trace_id,
                                      "run_id": state.run_id,
                                      "tool": tool.name, "attempt": attempt})
                if attempt == 3:
                    raise TransientError("tool retry budget exhausted") from exc
                delay = random.uniform(0.0, 0.1 * (2 ** (attempt - 1)))
                if delay >= state.budget.deadline - time.monotonic():
                    raise TransientError("insufficient tool retry deadline") from exc
                time.sleep(delay)
        raise AssertionError("bounded tool retry did not terminate")

    def run(self, state: RunState) -> RunState:
        if state.status is not RunStatus.CREATED:
            raise PermanentError("run must begin in CREATED")
        self._store.append(state, "RUN_STARTED", {}, state.state_version)
        state.status = RunStatus.RUNNING
        while state.status is RunStatus.RUNNING:
            if time.monotonic() >= state.budget.deadline:
                state.status = RunStatus.TIMED_OUT
                break
            verified, evidence = self._verified(state)
            if verified:
                state.status, state.evidence = RunStatus.SUCCEEDED, evidence
                break
            if state.budget.exhausted():
                state.status = RunStatus.BUDGET_EXHAUSTED
                break

            try:
                reply = self._models.propose(self._project(state),
                                             state.budget.deadline, state)
            except BudgetExceeded:
                state.status = RunStatus.BUDGET_EXHAUSTED
                break
            if reply is None:
                state.status = RunStatus.FAILED_DEPENDENCY
                break
            state.budget.turns += 1
            state.budget.tokens += reply.input_tokens + reply.output_tokens
            state.budget.cost_micros += reply.cost_micros
            decision = Decision.parse(reply.raw)

            if decision.kind == "finish":
                # Fluent completion without postcondition evidence is not success.
                state.budget.no_progress += 1
                if state.budget.no_progress >= state.budget.max_no_progress:
                    state.status = RunStatus.INCOMPLETE
                continue

            action_hash = hashlib.sha256(
                json.dumps({"plan": state.plan_version, "tool": decision.tool,
                            "args": decision.arguments,
                            "observations": sorted(state.observations)},
                           separators=(",", ":"), sort_keys=True).encode()
            ).hexdigest()
            if action_hash in state.action_hashes:
                state.budget.no_progress += 1
                if state.budget.no_progress >= state.budget.max_no_progress:
                    state.status = RunStatus.STALLED
                    break
            else:
                state.action_hashes.add(action_hash)

            version = state.state_version
            self._store.append(state, "TOOL_INTENT",
                               {"tool": decision.tool,
                                "args_digest": hashlib.sha256(
                                    json.dumps(decision.arguments, sort_keys=True).encode()
                                ).hexdigest()}, version)
            try:
                observation = self._run_tool(state, decision)
            except BudgetExceeded:
                state.status = RunStatus.BUDGET_EXHAUSTED
                break
            except PermanentError:
                state.status = RunStatus.INCOMPLETE
                break
            except TransientError:
                state.status = RunStatus.FAILED_DEPENDENCY
                break
            state.observations[observation.source_id] = observation
            state.budget.no_progress = 0
            self._store.append(state, "TOOL_RESULT",
                               {"source": observation.source_id,
                                "receipt": observation.receipt},
                               state.state_version)

        self._store.append(state, "RUN_TERMINAL",
                           {"status": state.status.value,
                            "evidence": list(state.evidence)},
                           state.state_version)
        logger.info("run terminal", extra={"trace_id": state.trace_id,
                                           "run_id": state.run_id,
                                           "status": state.status.value})
        return state


class DemoModel:
    def __init__(self, name: str, available: bool):
        self.name = name
        self._available = available

    def propose(self, state_json: str, timeout_s: float) -> ModelReply:
        if timeout_s <= 0 or not self._available:
            raise TimeoutError("model unavailable")
        state = json.loads(state_json)
        if "order" in state["observations"]:
            raw = json.dumps({"kind": "finish", "summary": "Order state verified."})
        else:
            raw = json.dumps({"kind": "act", "tool": "lookup_order",
                              "arguments": {"order_id": "ord_42"},
                              "summary": "Read authoritative order state."})
        return ModelReply(raw, input_tokens=300, output_tokens=80, cost_micros=900)


class DemoOrderTool:
    name = "lookup_order"

    def __init__(self):
        self._fail_once = True

    def execute(self, arguments: dict[str, object], timeout_s: float) -> Observation:
        if timeout_s <= 0:
            raise TimeoutError("tool deadline expired")
        if self._fail_once:
            self._fail_once = False
            raise TransientError("temporary order API failure")
        order_id = str(arguments["order_id"])
        return Observation("order", {"order_id": order_id, "status": "paid"},
                           receipt=f"orders-api:{order_id}:v7")


def main() -> None:
    budget = Budget(max_turns=5, max_model_attempts=8, max_tool_calls=3,
                    max_tokens=5_000,
                    max_cost_micros=20_000, max_no_progress=1,
                    deadline=time.monotonic() + 5.0)
    state = RunState(str(uuid.uuid4()), str(uuid.uuid4()),
                     "Verify order ord_42 status", RunStatus.CREATED,
                     state_version=0, plan_version=1, budget=budget)
    store = EventStore()
    coordinator = Coordinator(
        store,
        ModelChain([DemoModel("primary-region", False),
                    DemoModel("secondary-region", True)]),
        [DemoOrderTool()],
    )
    result = coordinator.run(state)
    print(json.dumps({"status": result.status.value,
                      "evidence": result.evidence,
                      "budget": asdict(result.budget),
                      "events": store.events},
                     separators=(",", ":"), sort_keys=True, default=str))


if __name__ == "__main__":
    main()
```

The in-memory event store is executable pedagogy, not a durable database. In production, store events and state versions transactionally, attach idempotency keys/outbox records to `TOOL_INTENT`, lease workers with fencing tokens, and persist model/tool results before advancing. The sample’s deterministic no-model fallback is `FAILED_DEPENDENCY`; it never invents an action or completion.

## 6. Architectural System Design Scenarios

### Scenario 1 — Customer-service resolution with transactional refunds

**Problem statement.** Design an agent handling 400 customer cases/second. It answers policy questions, diagnoses order exceptions, and may issue refunds up to $500. Read-only cases require p99 ≤ 8 seconds; machine time for refund cases p95 ≤ 15 seconds excluding human approval. Every refund needs policy validation, RPO 0, destination idempotency, verified final order/payment state, and zero model-controlled authorization.

**Proposed architecture and technologies.** Use a deterministic Temporal outer workflow: OIDC admission, intent classifier, authoritative order/payment reads, bounded ReAct only for exception diagnosis, deterministic refund-policy calculator, signed approval above risk thresholds, idempotent payment adapter, reconciliation, postcondition verifier, and customer communication. PostgreSQL stores run/state versions, intent/outbox, approvals, and receipts; Kafka distributes audit/read projections. The context projection carries order facts and observation provenance, never credentials or mutable authority.

```text
┌──────────────┐ OIDC       ┌──────────────┐ workflow   ┌──────────────┐
│ Customer/CSR ├───────────►│ API + policy ├───────────►│ Temporal     │
│ + approval   │◄──status───┤ admission    │            │ case graph   │
└──────────────┘            └──────────────┘            └───┬──────┬───┘
                                                            │      │
                                               uncertain fact│      │ fixed controls
                                                            ▼      ▼
                                                     ┌──────────┐ ┌──────────────┐
                                                     │ ReAct    │ │ Policy calc +│
                                                     │ diagnosis│ │ approval     │
                                                     └────┬─────┘ └──────┬───────┘
                                                          │              │ intent/outbox
                                                          ▼              ▼
                                                   ┌──────────────┐ ┌──────────────┐
                                                   │ Read tools   │ │ Payment API  │
                                                   │ order/search │ │ idempotent   │
                                                   └──────┬───────┘ └──────┬───────┘
                                                          │ facts           │ receipt
                                                          └────────┬─────────┘
                                                                   ▼
                                                            ┌──────────────┐
                                                            │ Postcondition│
                                                            │ + PG/Kafka   │
                                                            └──────────────┘
```

At 400 cases/s, if 70% fixed cases average two model calls and 30% exceptions average five, capacity is `400×(0.7×2+0.3×5)=1,160 model calls/s`. If average tools are 1.5/case, provision 600 tool calls/s plus failover headroom. Separate read, approval, and mutation bulkheads; diagnosis branching is the first feature disabled under pressure.

**Trade-off evaluation.**

| Architecture | Cost | Latency | Ops complexity | Security/control | Scalability ceiling |
|---|---|---|---|---|---|
| **Deterministic workflow + bounded ReAct diagnosis** | Medium; autonomy only on exceptions | Fast fixed path; variable diagnosis bounded | High: workflow, ledger, verifier | Strongest policy/approval/idempotency boundary | High with separate worker pools |
| ReAct for the entire case | High variable turns and repair | Unpredictable tails | Medium initially | Weak path predictability; greater injection/loop surface | Quota/cost constrained |
| Fixed workflow without model diagnosis | Lowest model cost | Fast and predictable | Medium rule maintenance | Strong controls, poor novel-exception coverage | High for known intents only |

**Decision rationale.** The hybrid wins because refund rules and execution are known controls, while exception diagnosis benefits from fresh evidence and flexible reads. It confines autonomy to the ambiguous node, preserves a fast majority path, and makes success a verified payment/order state rather than final prose.

### Scenario 2 — Bounded due-diligence planner-executor DAG

**Problem statement.** Design a due-diligence agent processing 20 company investigations/second across filings, litigation, product, market, and security evidence. Relevant branches emerge during research. Interactive reports require p95 ≤ 45 seconds and p99 ≤ 120 seconds, at most six branches and 30 model calls, explicit unresolved conflicts, source-level provenance, resumability after region loss, and a tenant spend cap.

**Proposed architecture and technologies.** A Temporal/LangGraph-style coordinator stores a typed versioned plan and bounded DAG in PostgreSQL. The planner creates questions/dependencies; policy intersects each node with read-only search/API tools. Ready branches execute in isolated worker pools and store documents by digest. A deterministic reducer deduplicates sources, preserves contradictory claims, and rejects branch write conflicts. A rubric evaluator scores required coverage; replanning occurs only on a material evidence delta and consumes its own budget. Kafka emits progress, OTel records trajectory metrics, and WORM retains manifests/digests.

```text
┌──────────────┐ request    ┌──────────────┐ plan/CAS  ┌──────────────┐
│ Analyst UI   ├───────────►│ Coordinator  ├──────────►│ Planner      │
│ partial view │◄─progress──┤ Temporal + PG│           │ typed DAG    │
└──────────────┘            └──────┬───────┘           └──────┬───────┘
                                    │ ready nodes               │ ≤6 branches
                  ┌─────────────────┼─────────────────┐         │
                  ▼                 ▼                 ▼         ▼
           ┌────────────┐    ┌────────────┐    ┌────────────┐ ┌────────────┐
           │ Filings    │    │ Litigation │    │ Market     │ │ Security   │
           │ worker     │    │ worker     │    │ worker     │ │ worker     │
           └─────┬──────┘    └─────┬──────┘    └─────┬──────┘ └─────┬──────┘
                 └─────────────────┬┴─────────────────┴───────────────┘
                                   ▼
                            ┌──────────────┐ rubric/contradiction ┌────────────┐
                            │ Deterministic├─────────────────────►│ Evaluator  │
                            │ reducer      │                      │ /replan    │
                            └──────┬───────┘                      └────────────┘
                                   ▼
                            ┌──────────────┐
                            │ Artifacts +  │
                            │ report/audit │
                            └──────────────┘
```

At 20 runs/s and six maximum concurrent branches, reserve up to 120 branch executions/s. If p95 branch duration is 15 seconds, worst admitted fan-out requires 1,800 branch slots; in practice weighted admission uses predicted branches and caps the fleet below that theoretical maximum, queuing lower priority investigations. A 30-call hard cap limits search/evaluator explosions, while join deadlines permit a provenance-rich partial report.

**Trade-off evaluation.**

| Architecture | Cost | Latency | Ops complexity | Security/control | Scalability ceiling |
|---|---|---|---|---|---|
| **Bounded typed DAG + deterministic reducer** | Medium-high parallel calls; explicit cap | Low critical path with parallel evidence | High: planner, scheduler, reducers, artifacts | Read-only capabilities, provenance, conflict visibility | High with admission/fan-out control |
| Serial ReAct research | Medium calls but long trajectory | High, roughly sum of branch work | Medium | Easier ordering, myopic coverage and long injection chain | Low at 20 runs/s |
| Fixed five-section workflow | Predictable and often cheaper | Predictable parallel stages | Medium | Strongest path control, misses emergent branches | High but lower investigation recall |

**Decision rationale.** The bounded DAG wins because the decomposition is genuinely unknown yet branches are mostly independent. Typed dependencies and reducers preserve state integrity; branch, call, replan, and spend caps prevent exponential search; exact source artifacts and unresolved-conflict reporting keep partial completion honest.

## Interview Review

1. **What separates an agent from its runtime?** The model proposes a control action; the runtime validates and commits state transitions, tools, budgets, and evidence.
2. **When should ReAct be used?** When each next action depends on a fresh observation, not when business steps are already known.
3. **Why is a stop reason not success?** It describes transport/model termination. Business success requires an external postcondition and immutable evidence.
4. **How is state different from context?** State is authoritative and durable; context is a bounded disposable projection for one model call.
5. **Why is workflow replay not exactly-once effect?** Replayed activities or redelivered tasks can repeat an external side effect unless the destination deduplicates and ambiguity is reconciled.
6. **What should be bounded independently?** Turns, tools, branches, tokens, currency, time, retries, replans, and no-progress transitions.

## Primary References

- [ReAct](https://arxiv.org/abs/2210.03629)
- [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [OpenAI Agents SDK runner](https://openai.github.io/openai-agents-python/running_agents/)
- [Anthropic stop reasons](https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons)
- [Plan-and-Solve](https://aclanthology.org/2023.acl-long.147/)
- [Tree of Thoughts](https://proceedings.neurips.cc/paper/2023/hash/271db9922b8d1f4dd7aaef84ed5ac703-Abstract.html)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [AWS Step Functions workflow types](https://docs.aws.amazon.com/step-functions/latest/dg/choosing-workflow-type.html)
- [Compensating transaction pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/compensating-transaction)
- [tau-bench](https://arxiv.org/abs/2406.12045)
- [AgentDojo](https://proceedings.nips.cc/paper_files/paper/2024/hash/97091a5177d8dc64b1da8bf3e1f6fb54-Abstract-Datasets_and_Benchmarks_Track.html)
