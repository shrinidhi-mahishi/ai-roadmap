# 17 - Advanced Agent Systems

**Scope:** Autonomous agents, long-horizon tasks, and agent environments.
**Study goal:** Design bounded autonomy that makes verified progress across context/process failures in a versioned environment and stops for the correct reason.

Autonomy is delegated authority, not absence of boundaries. Long-horizon work is not a larger prompt. As duration grows, goals drift, observations expire, external actors change state, retries duplicate effects, context turns over, and activity can masquerade as progress. The production design makes objective, authority, environment, evidence, budgets, approvals, recovery and terminal predicates explicit.

## 1. System Topology & Data Flow

### Bounded-autonomy topology

```text
                                       CONTROL PLANE
┌──────────────────────────────────────────────────────────────────────────────┐
│ autonomy tiers/policy │ capability templates/revocation │ model/tool routes │
│ environment/harness/grader/artifact registry │ quota/SLO │ eval/release gate│
│ owners/approvers │ incident/rollback/redrive │ retention/residency/audit    │
└────────────┬───────────────────────┬──────────────────────┬─────────────────┘
             │ signed goal/envelope  │ versioned releases   │ policy/budgets
             ▼                       ▼                      ▼
┌──────────────┐ objective  ┌───────────────────────────────────────────────┐
│ user/workflow├───────────►│ durable run coordinator                      │
└──────────────┘            │ admission │ lease/fence │ checkpoint │ cancel │
                            └───────┬───────────┬───────────┬───────────────┘
                                    │           │           │
                                    ▼           ▼           ▼
                              ┌─────────┐  ┌─────────┐ ┌─────────┐
                              │ planner │  │ observer│ │ verifier│
                              │ replan  │  │ state   │ │ evidence│
                              └────┬────┘  └────┬────┘ └────┬────┘
                                   └─────────────┼────────────┘
                                                 │ typed proposal
                                                 ▼
                                      ┌─────────────────────┐
                                      │ action broker / PEP │
                                      │ schema/policy/budget│
                                      │ approval/idempotency│
                                      └──────────┬──────────┘
                                                 │ bound capability
                    ┌────────────────────────────┼──────────────────────────┐
                    ▼                            ▼                          ▼
            ┌──────────────┐             ┌──────────────┐           ┌──────────────┐
            │ code sandbox │             │ browser/VM   │           │ enterprise   │
            │ gVisor/uVM   │             │ web/desktop  │           │ APIs/tools   │
            └──────┬───────┘             └──────┬───────┘           └──────┬───────┘
                   └─────────────── versioned environment ──────────────────┘
                                                 │ observation/receipt/delta
                                                 ▼
                    ┌───────────────────────────────────────────────────────┐
                    │ workflow history + semantic checkpoints + effect log │
                    │ content-addressed artifacts/snapshots + WORM evidence│
                    └───────────────────────────────────────────────────────┘

 TOOL/MCP PROXY: credentials remain outside model/environment; per-action authority
 TELEMETRY: run/attempt/checkpoint/action correlation │ progress/drift/cost/safety
```

The control plane owns autonomy tiers, policy and releases. One execution owns observations, action receipts, checkpoint, environment lease and terminal state. Policy remains outside model text. A model can propose, but the broker authenticates the current fenced run, validates a typed action, checks cumulative sequence/budget policy, binds exact approval, issues a narrow capability and records the effect.

### End-to-end long-horizon flow

1. Admission authenticates principal/tenant and signs a goal contract: objective, invariant constraints, environment digest, action/data/destination scope, budgets, deadline, approval rules and success/failure predicates.
2. The coordinator durably creates the run, reserves capacity/budget, leases a compatible environment and verifies its reset manifest. Environment identity includes image/data/task/grader/harness digests, logical clock and isolation class.
3. On first execution or resume, the worker obtains a fencing token, re-authenticates, re-evaluates policy/capability expiry and reconciles every pending/ambiguous effect. It compares authoritative environment state with the checkpoint digest.
4. The observer produces typed, source-labelled, timestamped observations. Untrusted files/pages/messages/tool outputs are evidence, never instruction authority.
5. A receding-horizon planner retains a coarse dependency graph but commits only the next verifiable milestone. It proposes a small typed action batch.
6. The action broker enforces current authority, exact arguments, preconditions, destination, action/tool/spend/concurrency budgets and approval. The environment executes with an idempotency key and returns receipt plus state delta.
7. A deterministic or independent verifier checks local postcondition, global invariants and progress. Model self-reflection may suggest a hypothesis but cannot mark a milestone complete.
8. The coordinator appends evidence, cost, progress and action state, then writes a semantic checkpoint at a durable boundary. Large observations remain content-addressed artifacts, not repeated prompt history.
9. New evidence or a failed assumption triggers bounded replan. No-progress, repeated-state/action, plan churn, scope expansion, budget/deadline and risk monitors may wait, pause, escalate, truncate or terminate.
10. Success requires the signed predicate and independent verification. Failure, cancellation and truncation remain distinct, with committed/ambiguous effect status. Capabilities revoke and environments produce destruction/retention receipts.

## 2. Core Mechanics & Algorithms

### 2.1 Autonomy envelope and least agency

Authority is structured data:

```text
E = (principal, tenant, objective_hash, environment_digest,
     allowed_actions/resources/destinations, explicit_denies,
     token/call/compute/storage/spend/concurrency budgets,
     start/expiry/deadline, approval policy, stop predicates)

A_effective = A_principal ∩ A_run ∩ A_milestone ∩ A_environment
            ∩ A_tool ∩ A_resource_state ∩ A_remaining_budget
```

An explicit deny overrides permits. An authority increase creates a new signed envelope and audit event; the agent cannot expand scope by revising its plan. Independent dimensions allow broad sandbox edits but no network, or broad read access but only one exact external write.

Least agency is stricter than least privilege: grant the minimum tool methods, autonomy duration and action sequence for the current milestone. Separate read, draft, approve and commit capabilities. Revoke on cancel, fence loss, anomaly, horizon drift, deadline or owner action. Evaluate cumulative effects because individually permitted reads/writes can form prohibited exfiltration or spend.

For `P` indexed policies, candidate selection is near `O(log P + k)` plus `O(k)` predicates for `k` applicable policies; naive evaluation is `O(P)`. Budget reservation is an atomic compare-and-swap. Policy and budget decisions use authenticated state, not model narration.

Autonomy tiers:

| Tier | Authority | Evidence before promotion |
|---|---|---|
| 0 advise | no tool effects | quality/privacy evaluation |
| 1 observe | scoped reads | access/injection/privacy tests |
| 2 sandbox act | reversible isolated mutations | correctness/resource/escape tests |
| 3 external draft | reviewable artifact/request | provenance, exact diff/effect preview |
| 4 bounded commit | narrow reversible transaction | policy/idempotency/reconciliation/override |
| 5 high impact | exceptional multi-party authorization | formal risk acceptance and recovery proof |

### 2.2 Receding-horizon control loop

ReAct interleaves observation and action; production adds milestones, durable state, external verification and stop rules:

```text
LOAD -> RECONCILE -> OBSERVE -> SELECT_MILESTONE -> PROPOSE
     -> AUTHORIZE -> EXECUTE -> VERIFY -> CHECKPOINT
     -> CONTINUE / REPLAN / WAIT / APPROVE / TERMINATE
```

Maintain immutable objective/constraints separately from mutable tactics. The plan is a DAG of coarse milestones with dependencies and evidence predicates. Topological selection is `O(V+E)` for `V` milestones and `E` dependencies; in practice maintain a ready set for near `O(log V)` selection. Commit only the next milestone so wrong distant assumptions do not generate large abandoned branches.

Define verified progress rather than activity:

```text
progress_i = Σ(weight_m × newly_verified_milestone_m)
           + invariant_improvement - regression_penalty - rework_penalty

velocity = (progress_now - progress_checkpoint) /
           max(tokens, actions, wall_time in window)
```

Monitor repeated action/state fingerprints, edit-revert cycles, revisited sources, plan churn without new evidence, verifier delta, contradiction with authoritative state, recovery-from-own-change ratio and widening permission requests. Hash-set detection is expected `O(1)` per fingerprint; similarity search is approximate/index dependent.

Stopping rules are deterministic policy:

- `SUCCEEDED_VERIFIED` only if every required predicate and invariant passes;
- `WAITING_EXTERNAL` when a registered event/timer, not active polling, is appropriate;
- `REPLAN` after material state/assumption change, bounded by replan budget;
- `TRUNCATED_BUDGET/DEADLINE` when external limits end an otherwise nonterminal episode;
- `FAILED_TERMINAL` for impossible/unsafe/incompatible state;
- escalate after `K` no-progress windows or ambiguity exceeds autonomy tier;
- cancel speculative parallel branches when marginal expected value falls below cost/risk.

Reflection is a hypothesis. Compiler/test/database/environment predicates, receipts and independent review are evidence. Never persist unverified self-critique as a fact or procedure.

### 2.3 Horizon reliability, drift, and consistency

If `n` independent irreversible steps each have correctness `p`, naive success is `p^n`: `0.99^100 ≈ 36.6%`, while `0.999^100 ≈ 90.5%`. Steps are not independent and repair/verification changes the process, so this is an intuition, not a forecast. Reduce irreversible steps, verify milestones, stage changes and reconcile effects.

`pass@k` asks whether any of `k` attempts succeeds; `pass^k` asks whether all `k` succeed and captures consistency. Production automation often values repeatability because failed attempts cannot be silently discarded if they mutate state. Report strict success, progress, policy-clean success, pass consistency, recovery after injected fault and success by task length.

METR's 50%-time horizon means the **human-expert completion time** of tasks at which fitted agent success is 50%, not agent runtime. The historical trend, suite and uncertainty do not imply that all jobs of that length are automatable. Domain performance varies substantially; task cleanliness, grader integrity, scaffold, model snapshot and benchmark exploit classification matter. Benchmark scores never widen production authority automatically.

Horizon-drift invariant: the active checkpoint repeats the signed objective hash and invariant block byte-for-byte; only tactics and evidence evolve. A verifier structurally compares goal/resource scope after every compaction and replan. Facts have source, valid time, confidence and revocation; stale observations are re-read before effects.

### 2.4 Semantic checkpoints and durable resume

Conversation compaction, semantic checkpoint, workflow history, environment snapshot and artifact commit solve different problems:

| Mechanism | Preserves | Does not prove |
|---|---|---|
| Conversation/compaction | model-relevant context | external effects or omitted constraints |
| Semantic checkpoint | inspectable plan/facts/evidence/budgets/pending work | environment still matches |
| Workflow history | durable decisions/results/timers | Activities executed externally once |
| Environment snapshot | local filesystem/VM/app state | SaaS/API/database current state |
| Artifact commit | durable versioned output | success/policy compliance |

A checkpoint includes run/attempt/principal, signed objective/invariants, environment/policy/model/tool/schema versions, state digest/logical clock, milestones/evidence/verifier status, plan/rejected hypotheses, artifact/snapshot refs, every issued action/idempotency/receipt/postcondition, ambiguous effects/reconciliation, remaining budgets/deadline, capabilities/approval/revocation and provenance-bearing memory.

Checkpoint after environment reset, milestone acceptance, durable tool effect/postcondition, artifact/test, approval transition, event subscription/timer, validated compaction and terminal verification. Too frequent means serialization/contention; too sparse means rework and ambiguity. Measure RPO in verified actions and RTO to productive work.

Replayable workflow orchestration must be deterministic. Time, randomness, filesystem and network I/O occur only through recorded Activities/steps. Resume algorithm:

1. acquire a new environment/run fence and revoke old capabilities;
2. re-authenticate and re-evaluate policy, approval, deadline and budget;
3. query receipts/state for every pending or ambiguous effect;
4. observe authoritative environment and compare version/state digest;
5. invalidate stale facts/plan branches and reconcile/compensate only through authorized domain operations;
6. migrate/validate checkpoint schema and harness versions;
7. continue, replan, pause or require repair.

Resume is `O(A + D)` over ambiguous actions `A` and changed state elements `D`, plus environment queries. Never blindly continue from prose.

### 2.5 Agent environment contract

Adopt Gymnasium's conceptual separation:

```text
reset(seed, options) -> observation, info
step(action) -> observation, reward, terminated, truncated, info
```

`terminated` means a task end state; `truncated` means an external budget/time/infrastructure limit. Production adds:

| Contract | Required fields |
|---|---|
| Identity | environment/image/data/task/grader/harness digest, provenance, region |
| Reset | seed/fixture/snapshot, clocks, accounts, cleanup assertion, unique secrets |
| Observation | typed schema, partiality, source, freshness/version, trust/classification |
| Action | schema, precondition, authority, idempotency, effect, timeout, receipt/postcondition |
| Time | logical/wall clocks, independent events, timer/wait, lease/deadline |
| Concurrency | other actors, ordering, isolation/conflict/fencing rules |
| Lifecycle | running/waiting/verified success/failure/truncated/cancelled/corrupted |
| Grading | hidden/public predicates, partial credit, violations, nondeterminism/CI |
| Snapshot | included/excluded state, secret/identity reset, restore compatibility |
| Network/data | egress, credentials, retention/destruction |

Environment reset validation checks fixture hashes/counts, no prior-run files/browser/memory/credentials, correct clock/seed/locale/stubs, task/grader separation, dependencies, canary uniqueness, absence of solution artifacts and destruction receipt. Reset cost is `O(size of fixture/snapshot)` unless copy-on-write; verification cost depends on asserted state.

For multi-agent environments, explicitly choose turn-based Agent Environment Cycle or parallel action semantics. Define who advances logical time, observation visibility, write conflicts and join. A common harness does not make different benchmark graders or environment versions comparable.

### 2.6 Parallelism, waiting, and environment classes

Parallel branches reduce wall time only for independent milestones. Each receives disjoint ownership, budget, environment and output contract. Join verifies all artifacts and conflicts; parallel writes use optimistic versions/fences. Cost grows approximately with branch count, while elapsed time approaches the slowest critical branch plus merge/verification. Cancel branches whose expected contribution no longer justifies spend.

Long-lived monitoring should subscribe to trustworthy events or schedule wake-ups. Continuous model polling converts waiting into tool churn and failure opportunity. If polling is unavoidable, calculate cadence from event detection SLO, rate/spend limit and provider reliability, add jitter, persist last event/version and make duplicate/out-of-order events idempotent.

Choose environments by claim: deterministic API/state machine for exact actions; self-hosted web replica for resettable browser behavior; desktop/VM for OS interaction; pinned repository for code; time-evolving sentinel for independent events; live production shadow only after sandbox gates and read-only/tightly contained authority. Published benchmark percentages are scoped to their model, harness, version, budget and grader and are not production rates.

## 3. Token Economics & NFR Analysis

### 3.1 Explicit cost per 1,000 long-horizon runs

Scoped planning assumptions: 1,000 runs collectively use 50M uncached input, 120M prompt-cache reads, 0.5M cache writes and 20M output. Planning rates per million input/output are `sol $5/$30`, `terra $2/$12`, `luna $0.20/$1.20`; reads cost `0.1x` input and writes `1.25x`. These are internal dated assumptions, not universal provider prices.

| Tier | Without cache (`170.5M` input + `20M` output) | With write/read split |
|---|---:|---:|
| `sol` | `170.5×$5 + 20×$30` = **$1,452.50** | `50×$5 + 120×$.50 + .5×$6.25 + 20×$30` = **$913.13** |
| `terra` | `170.5×$2 + 20×$12` = **$581.00** | `50×$2 + 120×$.20 + .5×$2.50 + 20×$12` = **$365.25** |
| `luna` | `170.5×$.20 + 20×$1.20` = **$58.10** | `50×$.20 + 120×$.02 + .5×$.25 + 20×$1.20` = **$36.53** |

Phase routing uses 50% `luna` observation/classification, 35% `terra` execution/planning and 15% `sol` hard planning/verification: `.50×$36.525 + .35×$365.25 + .15×$913.125 = $283.07`. Per 1K, environment leases/compute cost `$120`, checkpoints/object storage `$12`, tools/network `$40`, independent verification `$35`, 100 three-minute human reviews at `$60/hour` cost `$300`, telemetry/audit `$8`, and retry/recovery reserve `$60`: platform/operations **$575**. Total is **$858.07/1K runs**. If 700 runs are policy-compliant accepted successes, cost per 1,000 successes is `$858.07×1000/700 = $1,225.81`.

```text
run_cost = Σ(model input/output/cache) + Σ(tool/provider/compute/network)
         + environment idle/snapshot + checkpoint/storage
         + verification/human review + recovery/failure + audit

cost_per_accepted_success = all success + failed/truncated run cost
                            / policy-compliant accepted outcomes
```

Without retrieval/compaction, if each turn adds `d` tokens and resends history, cumulative input is approximately `base×n + d×n(n+1)/2`. Cache stable goal/instructions/tool schemas, retrieve only milestone evidence, compact after semantic boundaries, and store large observations by reference. Mutable environment observations require freshness revalidation, not cache trust.

### 3.2 Latency and horizon SLOs

Internal starting targets, not public benchmarks:

| Operation | p50 | p95 | p99 | Tail mitigation |
|---|---:|---:|---:|---|
| Policy/action admission | 4 ms | 20 ms | 60 ms | local signed policy, indexed budget |
| Checkpoint durable write | 20 ms | 100 ms | 300 ms | bounded state, artifact references, batch metadata |
| Environment allocation/reset | 3 s | 20 s | 60 s | warm pool, CoW snapshot, reset assertions |
| Resume to productive action | 5 s | 45 s | 3 min | compact checkpoint, receipt indexes, warm capacity |
| Local milestone verification | 2 s | 30 s | 3 min | incremental tests, verifier pool, bounded artifacts |
| Approval wait | 20 s | 5 min | 30 min | async workflow, exact preview, staffing/backpressure |
| Time-to-first-useful artifact | 2 min | 15 min | 45 min | receding horizon, early milestone, limit exploration |
| Multi-day completion | 45 min | 8 h | 3 d | report by task class; checkpoint/wait rather than hold context |

Report task/horizon/environment/autonomy tier, success/failure/truncation/wait, resumed/fresh and release cohorts separately. Completion percentile among successes alone hides failed runs; publish terminal-state proportions and time-to-terminal.

### 3.3 Capacity, storage, and backpressure

Assume 50 admitted runs/hour with four-hour mean lifecycle: Little's Law gives **200 active runs**. If only 20% are simultaneously using a model, mean model concurrency is 40; provision 60 slots for tail/burst. Environment leases support 250, workers 220 and downstream tool slots 80. Approval demand is 10/hour against 15/hour reviewed capacity. The active-run limit is the minimum budget-adjusted inventory, with duty cycle accounted per resource, so admit no more than 200 without measured headroom.

At one checkpoint per active run per 15 minutes, `200×4 = 800 checkpoints/hour` (`0.22/s`). If each mutable environment averages 2 GB, active mutable state is about 400 GB before snapshots/artifacts/replication. Measure tail sizes and write amplification.

Backpressure reserves the full/predicted run budget at admission, caps tenant active/queued environments and approval demand, and pauses durable runs at verified checkpoints. Shed speculative branches and low-priority new work first. Never autoscale model workers beyond environment, tool, database, spend or reviewer capacity. Waiting tasks release model slots and use persisted timers/subscriptions.

For polling cadence `c` seconds over duration `D`, calls are about `D/c`; halve cadence and roughly double calls/cost/failure exposure. Choose event subscription when reliable. Otherwise select cadence from reaction SLO and rate budget, add jitter, and back off during outages.

### 3.4 NFR and quality targets

| NFR | Target/evidence | Trade-off |
|---|---|---|
| Verified success | per task/tier; no completion from self-report | Verifier cost/latency |
| Authority | zero effects outside signed envelope; 100% PEP mediation | Less flexible recovery |
| Consistency | report pass^k and horizon slices; bounded regression | Repeated trials cost |
| Availability | 99.9% coordinator/checkpoint/action broker | Durable control-plane expense |
| RPO | 0 accepted goal/effect/approval/audit; <=1 verified milestone | More checkpoint writes |
| RTO | <=15 min coordinator; <=3 min resume p99; <=60 min env rebuild | Warm pools/snapshots cost |
| Environment | >=99.9% clean reset; zero known cross-run contamination | Strong isolation and reset verification |
| Cancellation | revoke/fence <=30 s; explicit effect terminal state | Downstream integration work |
| Privacy | no ambient secret/raw trace export; deletion/destruction receipt | Less forensic convenience |
| Fairness | tenant/tier queue-age and approval SLOs | Reserved capacity lowers utilization |
| Compliance | residency, provenance, retention, human responsibility and WORM evidence | Governance overhead |

## 4. Distributed Resilience & Security

### 4.1 Durable resumability and action state

```text
┌──────────────┐ goal/envelope ┌──────────────┐ history/timer ┌──────────────┐
│ API/scheduler├──────────────►│ Temporal     ├──────────────►│ run worker   │
└──────────────┘               │ coordinator  │◄─checkpoint───┤ lease/fence  │
                               └──────┬───────┘                └──────┬───────┘
                                      │ outbox/action                 │ proposal
                                      ▼                               ▼
                               ┌──────────────┐                ┌──────────────┐
                               │ Kafka/DLQ   │                │ broker/PEP   │
                               │ event/audit │                │ capability   │
                               └──────┬───────┘                └──────┬───────┘
                                      │                                ▼
                               ┌──────▼───────┐                ┌──────────────┐
                               │ WORM/artifact│◄──receipt──────┤ environment  │
                               │ effect ledger│                │ sandbox/API  │
                               └──────────────┘                └──────────────┘
```

Run lifecycle:

```text
CREATED -> ADMITTED -> RUNNING <-> WAITING_EXTERNAL/WAITING_APPROVAL
                    <-> PAUSED_QUOTA/OPERATOR
                    <-> RECONCILING_AMBIGUOUS_EFFECT
                    -> SUCCEEDED_VERIFIED / FAILED_TERMINAL
                    -> TRUNCATED_BUDGET/DEADLINE / CANCELLED(effect status)
```

Transitions use compare-and-swap run version/event sequence. Worker holds renewable lease/fence; stale workers cannot checkpoint or act. Every action stores `(run, action, attempt, idempotency, fence)`, canonical request hash, policy/approval, receipt and postcondition. Queue delivery is at least once. Stable event/action IDs deduplicate; poison schema/checkpoint enters a protected dead-letter queue. Temporal replay reconstructs orchestration but Activities remain idempotent/reconcilable.

Retries have one owner per failure class, remaining deadline, attempt/cost budget and exponential full jitter. Breakers isolate models, browsers, tools and environment pools with closed/open/half-open probes. Half-open actions are read-only/low impact. Degradation preserves identity, policy, audit, checkpoint and cancellation; it pauses work or requests human takeover rather than removing verification.

### 4.2 State drift, ambiguous effects, and recovery

On state mismatch or resume: fence executors; read authoritative environment and receipts; classify intended actions committed/absent/partial/duplicate/unknown; re-establish invariants; invalidate stale dependent observations/memory; re-evaluate policy/deadline/remaining value; write a reconciled checkpoint or terminate for repair.

External compensation is a new domain action with authorization and failure modes, never “ask the model to undo.” Environment snapshots exclude live SaaS/database state. Workflow/harness/checkpoint upgrades require historical replay and migration tests. Artifacts, task fixtures, grader and environment are pinned by digest/provenance; a changed component creates a new evaluation/release series.

Failure drills kill before/after action, receipt, checkpoint and ack; mutate environment during pause; expire capability/approval; restore snapshots; corrupt compaction; replay old histories; inject provider outage; and verify RPO/RTO from productive execution, not status load.

### 4.3 Least agency, approval, and Zero Trust MCP

MCP servers, tool descriptions/results, webpages, files, emails, memory and agent handoffs are untrusted observations. The host authenticates principal/workload. A tool PEP applies RBAC baseline plus run/milestone/resource/action/destination/current-state ABAC and cumulative sequence policy. It issues a short-lived run/action/audience-bound capability; model and sandbox have no ambient enterprise/cloud credential.

Approval binds exact `(principal, tenant, run, action, canonical arguments, target version/state digest, expected delta, maximum scope/amount, policy version, expiry, nonce)`. State or argument change invalidates it. Approval text in an observation has no authority. High impact uses separation of duties.

Prompt injection defense keeps instruction/control labels distinct from observations and reauthorizes every effect. Memory writes need schema, provenance, subject/tenant authorization, confidence and validity. Facts, preferences, skills and reflection remain distinct; retrieved memory never grants capability. Support correction/deletion and derived-summary rebuild.

### 4.4 Environment isolation and supply chain

Standard containers share a kernel. Use gVisor/application-kernel isolation for common untrusted code and microVM/jailer or dedicated boundaries for hostile/high-risk multi-tenant work, based on compatibility and measured overhead. Every runtime has ephemeral per-run storage, read-only base, no host socket/mount, non-root, seccomp, CPU/RAM/PID/disk/time/output limits, no metadata credential, default-deny egress, destination proxy, per-run secrets and destruction receipt.

Pin OCI image/data/setup/grader/model/tool/harness digests and verify provenance/signatures. The grader/hidden tests use separate identity/network and are never visible to the agent. Snapshot restore rotates uniqueness, credentials, identity and clock as required. Artifacts exiting a sandbox are untrusted and scanned/verified before use.

PII pipeline is `classify -> minimize -> redact/tokenize -> authorize environment/destination -> execute -> controlled rehydrate -> retain/delete -> audit`. Raw screenshots/prompts/files stay in governed artifact storage; operational traces carry references/hashes.

### 4.5 Monitoring, sabotage, and governance

Monitor typed state deltas, data flow, cumulative spend/access/destination, progress velocity, loop fingerprints, plan/scope drift, approval patterns, post-cancel activity, effect ambiguity and environment contamination. Deterministic policy remains authoritative. Separate verifier/monitor sees only needed evidence and cannot be persuaded by the acting agent. Canary secrets/resources and sequence-level red-team tasks detect hidden harmful side objectives.

Correlate bounded traces with run/attempt/checkpoint/action IDs rather than one multi-day trace. Immutable audit records goal/envelope, policy revision/decision, proposal, approval, capability, environment/artifact versions, receipt, state delta, cost and terminal predicate. Restrict raw evidence access, retention and export.

Govern through named system/data/risk owners, autonomy tier, system card, threat/data/environment inventory, evaluation suite, release approval, incident/revoke/redrive/rollback runbooks, exception expiry and periodic recertification. Increase authority only from monitored evidence at the previous tier. NIST AI RMF Govern/Map/Measure/Manage supplies a lifecycle frame, not an automatic approval.

### 4.6 Representative failure matrix

| Failure | Detection | Containment/recovery |
|---|---|---|
| Goal/scope drift | structural goal/envelope comparison | freeze tactics; replan or new signed authority |
| Premature victory | end-state predicate/test mismatch | reject completion; independent verification |
| Activity/no progress | repeated fingerprint, zero verifier delta | wait/replan/escalate/truncate |
| Compaction/memory poison | missing invariant/provenance conflict | restore checkpoint; quarantine/rebuild memory |
| Stale resume/concurrent mutation | state digest/version mismatch | fence, reconcile, invalidate observations |
| Duplicate/ambiguous effect | receipt/state/idempotency lookup | freeze retry; reconcile then commit/fail |
| Approval TOCTOU/laundering | args/state/identity mismatch | invalidate; require exact fresh approval |
| Sandbox/secret escape | runtime/egress/canary alert | kill/quarantine, rotate, preserve evidence |
| Environment/grader leakage | reset/canary/exploit signal | invalidate run/series; rebuild environment |
| Retry/tool cascade | attempts/cost/queue surge | breaker, durable wait, one retry owner |
| Unbounded spend/branches | reservation/cap breach | cancel branches, pause/fence run |
| Cancellation illusion | effects after fence/cancel | revoke, query effect terminal state, reconcile |
| Wait churn | calls without state change | event subscription/scheduled wake |
| Bad reflection | self-claim contradicts evidence | discard reflection; independent state check |

## 5. Production Enterprise Code

This Python 3.11 standard-library program implements a bounded long-horizon control loop. It persists inspectable semantic checkpoints and a hash-chained audit in SQLite, increments a fencing token on resume, restores a versioned deterministic environment, enforces action/budget/deadline policy outside the planner, uses idempotent action receipts, verifies progress and terminal state, and distinguishes pause, wait, verified success and truncation. Planner calls use exponential full jitter, closed/open/half-open breakers, primary then secondary, and a deterministic `WAIT` fallback.

```python
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import sqlite3
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol, Sequence


class TransientFailure(RuntimeError):
    """A retryable planner or environment dependency failure."""


class PermanentFailure(RuntimeError):
    """A policy, schema, fence, or environment contract failure."""


class CircuitOpen(TransientFailure):
    """A dependency is isolated pending a recovery probe."""


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {"timestamp": time.time(), "severity": record.levelname,
                 "message": record.getMessage()}
        for key in ("run_id", "fence", "step", "action", "dependency",
                    "attempt", "status"):
            if hasattr(record, key):
                value[key] = getattr(record, key)
        return json.dumps(value, separators=(",", ":"), sort_keys=True)


logger = logging.getLogger("bounded-autonomy")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


class Breaker:
    def __init__(self, threshold: int = 2, recovery_s: float = 2.0):
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
                    raise CircuitOpen("half-open probe active")
                self._probe = True

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = "closed"
            self._probe = False

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._probe = False
            if self._state == "half_open" or self._failures >= self._threshold:
                self._state = "open"
                self._opened_at = time.monotonic()


@dataclass(frozen=True)
class Envelope:
    principal: str
    tenant: str
    objective: str
    environment_version: str
    allowed_actions: Sequence[str]
    denied_actions: Sequence[str]
    max_actions: int
    max_replans: int
    max_no_progress: int
    deadline_epoch_s: float
    policy_version: str

    def objective_hash(self) -> str:
        return hashlib.sha256(json.dumps(
            asdict(self), separators=(",", ":"), sort_keys=True
        ).encode()).hexdigest()


@dataclass(frozen=True)
class Action:
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class StepResult:
    observation: dict[str, object]
    reward: float
    terminated: bool
    truncated: bool
    info: dict[str, object]


class MigrationEnvironment:
    VERSION = "migration-env@sha256:demo-v1"
    ACTIONS = ("inspect", "edit", "test")

    def __init__(self):
        self._stage = 0
        self._receipts: dict[str, StepResult] = {}

    def reset(self, seed: int) -> tuple[dict[str, object], dict[str, object]]:
        self._stage = 0
        self._receipts.clear()
        return self.observe(), {"version": self.VERSION, "seed": seed,
                                "resetVerified": True}

    def observe(self) -> dict[str, object]:
        return {"stage": self._stage,
                "nextExpected": self.ACTIONS[self._stage]
                if self._stage < len(self.ACTIONS) else None,
                "stateDigest": self.state_digest(),
                "source": "authoritative-environment"}

    def step(self, action: Action, action_id: str) -> StepResult:
        prior = self._receipts.get(action_id)
        if prior:
            return prior
        if action.name not in self.ACTIONS:
            raise PermanentFailure("environment action is not registered")
        expected = self.ACTIONS[self._stage] if self._stage < 3 else None
        reward = 1.0 if action.name == expected else 0.0
        if reward:
            self._stage += 1
        result = StepResult(
            observation=self.observe(), reward=reward,
            terminated=self._stage == len(self.ACTIONS), truncated=False,
            info={"receipt": hashlib.sha256(
                f"{action_id}:{action.name}:{self._stage}".encode()
            ).hexdigest()[:24], "postconditionVerified": bool(reward)},
        )
        self._receipts[action_id] = result
        return result

    def snapshot(self) -> dict[str, object]:
        return {"version": self.VERSION, "stage": self._stage,
                "receipts": {key: asdict(value)
                             for key, value in self._receipts.items()}}

    def restore(self, snapshot: dict[str, object]) -> None:
        if snapshot.get("version") != self.VERSION:
            raise PermanentFailure("environment snapshot version mismatch")
        stage = snapshot.get("stage")
        if not isinstance(stage, int) or not 0 <= stage <= len(self.ACTIONS):
            raise PermanentFailure("invalid environment snapshot")
        self._stage = stage
        self._receipts = {}
        raw_receipts = snapshot.get("receipts", {})
        if not isinstance(raw_receipts, dict):
            raise PermanentFailure("invalid receipt snapshot")
        for key, raw in raw_receipts.items():
            if not isinstance(key, str) or not isinstance(raw, dict):
                raise PermanentFailure("invalid receipt record")
            self._receipts[key] = StepResult(**raw)

    def state_digest(self) -> str:
        return hashlib.sha256(
            f"{self.VERSION}:stage:{self._stage}".encode()
        ).hexdigest()

    def success_verified(self) -> bool:
        return self._stage == len(self.ACTIONS)


class CheckpointStore:
    def __init__(self, path: Path):
        self._db = sqlite3.connect(path, check_same_thread=False,
                                   isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS runs(
                  run_id TEXT PRIMARY KEY,
                  objective_hash TEXT NOT NULL,
                  envelope_json TEXT NOT NULL,
                  fence INTEGER NOT NULL DEFAULT 0,
                  status TEXT NOT NULL,
                  checkpoint_json TEXT NOT NULL,
                  version INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS audit(
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                  run_id TEXT NOT NULL,
                  event_json TEXT NOT NULL,
                  previous_hash TEXT NOT NULL,
                  event_hash TEXT NOT NULL
                );
            """)

    def create(self, run_id: str, envelope: Envelope,
               checkpoint: dict[str, object]) -> None:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    "INSERT INTO runs(run_id,objective_hash,envelope_json,"
                    "status,checkpoint_json) VALUES(?,?,?,?,?)",
                    (run_id, envelope.objective_hash(), json.dumps(
                        asdict(envelope), separators=(",", ":"), sort_keys=True
                    ), "ADMITTED", json.dumps(
                        checkpoint, separators=(",", ":"), sort_keys=True
                    )),
                )
                self._audit_locked(run_id, {"event": "run.created"})
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def acquire(self, run_id: str, envelope: Envelope) -> tuple[int, dict[str, object]]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT objective_hash,fence,checkpoint_json,status FROM runs "
                    "WHERE run_id=?", (run_id,)
                ).fetchone()
                if not row or not hmac.compare_digest(
                        row["objective_hash"], envelope.objective_hash()):
                    raise PermanentFailure("run goal/envelope mismatch")
                if row["status"] in {
                    "SUCCEEDED_VERIFIED", "FAILED_NO_PROGRESS",
                    "FAILED_TERMINAL", "TRUNCATED_BUDGET",
                    "TRUNCATED_DEADLINE", "TRUNCATED_ENVIRONMENT",
                    "CANCELLED",
                }:
                    raise PermanentFailure("terminal run cannot be reacquired")
                fence = int(row["fence"]) + 1
                self._db.execute(
                    "UPDATE runs SET fence=?,status='RUNNING' WHERE run_id=?",
                    (fence, run_id),
                )
                self._audit_locked(run_id, {
                    "event": "run.acquired", "fence": fence
                })
                self._db.execute("COMMIT")
                return fence, json.loads(row["checkpoint_json"])
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def save(self, run_id: str, fence: int, status: str,
             checkpoint: dict[str, object]) -> None:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                changed = self._db.execute(
                    "UPDATE runs SET checkpoint_json=?,status=?,version=version+1 "
                    "WHERE run_id=? AND fence=?",
                    (json.dumps(checkpoint, separators=(",", ":"),
                                sort_keys=True), status, run_id, fence),
                ).rowcount
                if changed != 1:
                    raise PermanentFailure("stale run fence")
                self._audit_locked(run_id, {
                    "event": "checkpoint.saved", "fence": fence,
                    "status": status, "step": checkpoint["step"],
                    "stateDigest": checkpoint["stateDigest"],
                })
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def status(self, run_id: str) -> str:
        with self._lock:
            row = self._db.execute(
                "SELECT status FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            return str(row["status"]) if row else "MISSING"

    def audit_count(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) n FROM audit").fetchone()
            return int(row["n"])

    def _audit_locked(self, run_id: str, event: dict[str, object]) -> None:
        row = self._db.execute(
            "SELECT event_hash FROM audit ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous = row["event_hash"] if row else "0" * 64
        encoded = json.dumps(event, separators=(",", ":"), sort_keys=True)
        digest = hashlib.sha256((previous + encoded).encode()).hexdigest()
        self._db.execute(
            "INSERT INTO audit(run_id,event_json,previous_hash,event_hash) "
            "VALUES(?,?,?,?)", (run_id, encoded, previous, digest)
        )


class PlannerBackend(Protocol):
    name: str

    def plan(self, observation: dict[str, object], timeout_s: float) -> Action:
        """Return one bounded action or raise a classified failure."""


class DemoPlanner:
    def __init__(self, name: str, failures_before_success: int):
        self.name = name
        self._failures = failures_before_success

    def plan(self, observation: dict[str, object], timeout_s: float) -> Action:
        if timeout_s <= 0:
            raise TransientFailure("planner deadline exhausted")
        if self._failures > 0:
            self._failures -= 1
            raise TransientFailure(f"{self.name} unavailable")
        expected = observation.get("nextExpected")
        if not isinstance(expected, str):
            return Action("WAIT", {"reason": "no next milestone"})
        return Action(expected, {"expectedState": observation["stateDigest"]})


class PlannerChain:
    def __init__(self, primary: PlannerBackend, secondary: PlannerBackend):
        self._planners = (primary, secondary)
        self._breakers = {planner.name: Breaker() for planner in self._planners}

    def choose(self, observation: dict[str, object], deadline: float,
               run_id: str, fence: int, step: int) -> Action:
        for planner in self._planners:
            breaker = self._breakers[planner.name]
            for attempt in range(1, 3):
                if time.monotonic() >= deadline:
                    return Action("WAIT", {"reason": "deadline"})
                try:
                    breaker.before()
                    action = planner.plan(
                        observation, deadline - time.monotonic()
                    )
                    breaker.success()
                    return action
                except CircuitOpen:
                    break
                except PermanentFailure:
                    break
                except (TransientFailure, TimeoutError) as exc:
                    breaker.failure()
                    logger.warning("planner retryable failure", extra={
                        "run_id": run_id, "fence": fence, "step": step,
                        "action": "plan", "dependency": planner.name,
                        "attempt": attempt, "status": type(exc).__name__,
                    })
                    if attempt < 2:
                        cap = min(.02 * (2 ** (attempt - 1)),
                                  max(0.0, deadline - time.monotonic()))
                        time.sleep(random.uniform(0.0, cap))
        return Action("WAIT", {"reason": "planners unavailable"})


class ActionBroker:
    @staticmethod
    def authorize(envelope: Envelope, action: Action, actions_used: int,
                  current_digest: str) -> None:
        if action.name in envelope.denied_actions:
            raise PermanentFailure("action explicitly denied")
        if action.name not in envelope.allowed_actions:
            raise PermanentFailure("action outside autonomy envelope")
        if actions_used >= envelope.max_actions:
            raise PermanentFailure("action budget exhausted")
        if envelope.deadline_epoch_s <= time.time():
            raise PermanentFailure("run deadline exhausted")
        if action.arguments.get("expectedState") != current_digest:
            raise PermanentFailure("stale action precondition")


class Coordinator:
    def __init__(self, run_id: str, envelope: Envelope,
                 store: CheckpointStore, environment: MigrationEnvironment,
                 planners: PlannerChain):
        self._run_id, self._envelope, self._store = run_id, envelope, store
        self._environment, self._planners = environment, planners

    @staticmethod
    def initial_checkpoint(environment: MigrationEnvironment) -> dict[str, object]:
        observation, info = environment.reset(seed=7)
        if not info["resetVerified"]:
            raise PermanentFailure("environment reset was not verified")
        return {"step": 0, "actionsUsed": 0, "replans": 0,
                "noProgress": 0, "verifiedProgress": 0,
                "stateDigest": observation["stateDigest"],
                "environment": environment.snapshot(),
                "pendingEffects": [], "lastReceipt": None}

    def run(self, iteration_limit: int) -> str:
        fence, checkpoint = self._store.acquire(self._run_id, self._envelope)
        self._environment.restore(checkpoint["environment"])
        if checkpoint["stateDigest"] != self._environment.state_digest():
            raise PermanentFailure("checkpoint/environment drift")

        for _ in range(iteration_limit):
            if time.time() >= self._envelope.deadline_epoch_s:
                return self._terminal(fence, checkpoint,
                                      "TRUNCATED_DEADLINE")
            observation = self._environment.observe()
            action = self._planners.choose(
                observation, time.monotonic() + 1.0,
                self._run_id, fence, checkpoint["step"] + 1,
            )
            if action.name == "WAIT":
                return self._terminal(fence, checkpoint, "WAITING_EXTERNAL")
            try:
                ActionBroker.authorize(
                    self._envelope, action, checkpoint["actionsUsed"],
                    observation["stateDigest"],
                )
            except PermanentFailure as exc:
                if "budget" in str(exc) or "deadline" in str(exc):
                    return self._terminal(fence, checkpoint,
                                          "TRUNCATED_BUDGET")
                raise

            step = checkpoint["step"] + 1
            action_id = f"{self._run_id}:step:{step}"
            result = self._environment.step(action, action_id)
            progressed = bool(result.reward > 0
                              and result.info["postconditionVerified"])
            checkpoint.update({
                "step": step,
                "actionsUsed": checkpoint["actionsUsed"] + 1,
                "verifiedProgress": checkpoint["verifiedProgress"]
                                    + (1 if progressed else 0),
                "noProgress": 0 if progressed else checkpoint["noProgress"] + 1,
                "stateDigest": result.observation["stateDigest"],
                "environment": self._environment.snapshot(),
                "lastReceipt": result.info["receipt"],
            })
            logger.info("action verified", extra={
                "run_id": self._run_id, "fence": fence, "step": step,
                "action": action.name, "dependency": "environment",
                "attempt": 1, "status": "progress" if progressed else "stalled",
            })
            if result.truncated:
                return self._terminal(fence, checkpoint,
                                      "TRUNCATED_ENVIRONMENT")
            if result.terminated:
                if not self._environment.success_verified():
                    raise PermanentFailure("terminal claim failed verifier")
                return self._terminal(fence, checkpoint,
                                      "SUCCEEDED_VERIFIED")
            if checkpoint["noProgress"] >= self._envelope.max_no_progress:
                checkpoint["replans"] += 1
                checkpoint["noProgress"] = 0
                if checkpoint["replans"] > self._envelope.max_replans:
                    return self._terminal(fence, checkpoint,
                                          "FAILED_NO_PROGRESS")
            self._store.save(self._run_id, fence, "RUNNING", checkpoint)

        return self._terminal(fence, checkpoint, "PAUSED_CHECKPOINT")

    def _terminal(self, fence: int, checkpoint: dict[str, object],
                  status: str) -> str:
        checkpoint["environment"] = self._environment.snapshot()
        checkpoint["stateDigest"] = self._environment.state_digest()
        self._store.save(self._run_id, fence, status, checkpoint)
        return status


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = CheckpointStore(Path(directory) / "runs.db")
        envelope = Envelope(
            principal="user-42", tenant="tenant-a",
            objective="Inspect, edit, and test the isolated migration",
            environment_version=MigrationEnvironment.VERSION,
            allowed_actions=("inspect", "edit", "test"),
            denied_actions=("publish", "merge", "deploy", "secret.read"),
            max_actions=8, max_replans=1, max_no_progress=2,
            deadline_epoch_s=time.time() + 60,
            policy_version="autonomy-policy-9",
        )
        run_id = uuid.uuid4().hex
        initial_env = MigrationEnvironment()
        store.create(run_id, envelope,
                     Coordinator.initial_checkpoint(initial_env))
        planners = PlannerChain(DemoPlanner("primary", 3),
                                DemoPlanner("secondary", 0))
        first = Coordinator(run_id, envelope, store,
                            MigrationEnvironment(), planners).run(2)
        resumed = Coordinator(run_id, envelope, store,
                              MigrationEnvironment(), planners).run(2)

        publish_denied = False
        try:
            ActionBroker.authorize(
                envelope, Action("publish", {"expectedState": "x"}), 0, "x"
            )
        except PermanentFailure:
            publish_denied = True

        wait_run = uuid.uuid4().hex
        wait_env = MigrationEnvironment()
        store.create(wait_run, envelope,
                     Coordinator.initial_checkpoint(wait_env))
        waiting = Coordinator(
            wait_run, envelope, store, MigrationEnvironment(),
            PlannerChain(DemoPlanner("planner-a-down", 3),
                         DemoPlanner("planner-b-down", 3)),
        ).run(1)
        print(json.dumps({
            "firstSession": first,
            "resumedSession": resumed,
            "finalStatus": store.status(run_id),
            "publishDenied": publish_denied,
            "allPlannerOutage": waiting,
            "auditEvents": store.audit_count(),
        }, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
```

The first session completes two verified milestones and checkpoints. A new coordinator/environment restores that snapshot under a higher fence and completes the final test. `publish` remains impossible under the envelope. Loss of both planners transitions to durable `WAITING_EXTERNAL` instead of inventing an action or weakening verification.

## 6. Architectural System Design Scenarios

### Scenario 1 - Multi-day codebase migration

**Problem statement.** Migrate a service spanning 6,000 files to a new runtime over up to seven days. The system must survive context, pod and zone restarts; parallelize independent packages; preserve behavior; and produce a reviewable pull request. It may never merge or deploy. Requirements are RPO at most one verified milestone, p99 resume-to-productive-action under three minutes, zero production credentials, a `$500` run cap, and all required/hidden tests passing before success.

**Proposed architecture.** A signed goal pins base commit, target runtime, in-scope paths, invariant tests, banned dependencies/actions and budgets. Temporal owns milestone DAG, leases, approvals and checkpoints. Each branch receives a disjoint package set and a gVisor/microVM worktree pinned to OCI/base/dependency digests. The model can read/edit/test/commit inside that branch only. Network reaches approved registries through a proxy; no main/merge/deploy credential exists. Every session restores a semantic checkpoint, verifies worktree commit/test state, completes one coherent milestone, creates a commit and exits. A merge worker checks ownership/conflicts; independent hidden tests and diff/policy verifier gate exact-PR human approval.

```text
┌──────────────┐ signed goal ┌──────────────┐ milestones ┌──────────────┐
│ engineer     ├────────────►│ Temporal     ├───────────►│ branch queues│
│ exact PR ack │◄────────────┤ checkpoints  │            └──────┬───────┘
└──────────────┘             └──────────────┘                   │ disjoint scope
                                                      ┌─────────┼─────────┐
                                                      ▼         ▼         ▼
                                                ┌──────────┐┌──────────┐┌──────────┐
                                                │ uVM env A││ uVM env B││ uVM env C│
                                                │ edit/test││ edit/test││ edit/test│
                                                └────┬─────┘└────┬─────┘└────┬─────┘
                                                     └───────────┼───────────┘
                                                                 ▼
                                                        ┌────────────────┐
                                                        │ merge verifier │
                                                        │ hidden tests   │
                                                        └───────┬────────┘
                                                                ▼ exact diff
                                                        ┌────────────────┐
                                                        │ PR approval    │
                                                        │ no merge/deploy│
                                                        └────────────────┘
```

Capacity separates model concurrency, environment leases, test CPU/storage and reviewers. Under the Section 3 service shape, cap 200 active runs, but this class receives at most 80 environments and 30 simultaneous test slots. Branch budget is reserved; low-value branches cancel. Track verified checks/commit, regressions/reverts, merge conflicts, cost per accepted commit, checkpoint RTO and review minutes.

| Approach | Cost | Completion time | Operations | Security/reliability | Scalability ceiling |
|---|---|---|---|---|---|
| One continuous agent/context/worktree | Lowest orchestration | Long critical path | Low initially | Drift/context/pod loss; coherent ownership | One model/env bottleneck |
| **Temporal milestones + semantic checkpoints + disjoint sandbox branches** | Higher model/env/verification cost | Parallel where dependencies permit | High workflow/merge/version work | Bounded scope, resumable, independently verified | Test/env/reviewer capacity |
| Human-led migration with agent suggestions only | Highest human time | Predictable team cadence | Medium | Strong direct oversight; lower autonomous risk | Engineering staffing |

**Decision rationale.** The task has enough duration and independent packages to justify durable orchestration and bounded parallelism. Disjoint ownership prevents agents overwriting one another; checkpoints preserve intent/evidence rather than entire context; hidden state tests prevent premature victory. Missing merge/deploy credentials makes the top-level prohibition structural. Human-led work remains the fallback for checkpoint migration, irreconcilable drift or verifier disagreement.

### Scenario 2 - Seven-day monitoring and conditional drafting

**Problem statement.** Monitor a procurement portal for seven days and, when a qualifying tender appears, produce a cited response draft within ten minutes. No autonomous submission or purchase is allowed. Requirements are event recall above the validated target, p95 reaction under five minutes, fewer than 200 portal calls/week when subscription is unavailable, RPO zero for last observed event/version, explicit expiry/no-event terminal state, and exact human approval before any external draft upload.

**Proposed architecture.** A durable workflow registers a signed webhook/event subscription where available; otherwise it schedules jittered polling no faster than the call budget permits. It stores logical time, last event/version and dedupe key, then releases model/environment capacity while waiting. A low-cost typed observer classifies each change; hard policy checks qualification and injection/data labels. A capable model prepares a draft from immutable evidence references, while an independent verifier checks citations and tender constraints. Human approval binds exact file hash, portal account/destination, tender version and expiry. The upload tool has a one-operation capability and idempotency key; purchase tools are absent.

```text
┌──────────────┐ webhook/timer ┌──────────────┐ event/version ┌──────────────┐
│ portal       ├──────────────►│ durable      ├──────────────►│ typed observer│
│ independent  │◄─bounded poll─┤ workflow     │               │ policy check │
└──────────────┘               └──────┬───────┘               └──────┬───────┘
                                      │ wait releases capacity       ▼
                                      │                       ┌──────────────┐
                                      │                       │ draft model  │
                                      │                       │ + verifier   │
                                      │                       └──────┬───────┘
                                      │                              ▼ exact hash
                                      │                       ┌──────────────┐
                                      └──────────────────────►│ human approve│
                                                              │ upload only │
                                                              └──────────────┘
```

With a seven-day window and 200-call maximum, fixed polling cannot average faster than `604,800/200 = 3,024 seconds`, about 50.4 minutes; that cannot meet a five-minute reaction SLO. Therefore the five-minute SLO requires a reliable subscription or a negotiated higher call budget. The system states this incompatibility rather than hiding it with busy polling. Evaluate through time-compressed scripted event timelines with duplicates, reordering, portal outage, clock jumps and injection.

| Approach | Cost | Reaction latency | Operations | Security/reliability | Scalability ceiling |
|---|---|---|---|---|---|
| Continuous browser/model polling | Highest idle/tool cost | Lowest when healthy | High session churn | More injection/failure exposure | Browser/provider limits |
| Fixed polling under 200 calls/week | Low | About 50-minute average interval | Low-medium | Simple and auditable; misses five-minute SLO | Portal call budget |
| **Webhook/subscription + durable timer fallback + exact upload approval** | Lowest idle, integration cost | Meets event-path target | Medium-high event validation/dedupe | Strong wait semantics and bounded effect | Event service/reviewer capacity |

**Decision rationale.** Productive waiting is a durable state, not continuous cognition. Subscription decouples reaction time from polling spend; persisted event versions handle duplicate/reordered delivery. The observer and draft model receive no submit/purchase authority. Exact hash-bound approval and one-operation upload capability prevent the model from reinterpreting “yes” after portal or draft state changes.

## Interview Review

1. **What makes autonomy bounded?** Signed objective, data/action/destination/resource/time/concurrency scope, approval rules, deterministic enforcement and revocation.
2. **Why is long horizon not long context?** External state changes, effects, failures and context turnover require checkpoints, environment observation, reconciliation and terminal proof.
3. **Checkpoint versus snapshot?** Checkpoint preserves semantic run state/evidence; snapshot preserves selected local environment bytes. Neither proves current external SaaS state.
4. **Terminated versus truncated?** Terminated is a task end state; truncated is an external budget/time/infrastructure limit on a nonterminal episode.
5. **How is progress measured?** Newly verified milestone/invariant delta minus regression/rework, normalized by tokens/actions/time; activity does not count.
6. **How is resume safe?** Fence old workers, reauthorize, reconcile ambiguous effects, re-observe authoritative state, invalidate stale facts and replan if needed.
7. **How do environment benchmarks transfer?** Only as scoped evidence for a pinned model, harness, environment, task, budget and grader; they do not set production authority.
8. **Why can approval fail?** Prose can be forged or become stale. Bind authenticated approval to canonical effect, target state/version, policy and expiry.

## Primary References

- [OpenAI model autonomy guidance](https://developers.openai.com/api/docs/guides/latest-model)
- [OpenAI Responses compaction](https://developers.openai.com/api/reference/java/resources/responses/methods/compact)
- [Temporal workflow replay](https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/workflow/workflow-execution/workflow-execution.mdx)
- [LangGraph checkpoint time travel](https://langchain-ai.github.io/langgraph/concepts/time-travel/)
- [Gymnasium environment API](https://gymnasium.farama.org/api/env/)
- [PettingZoo APIs](https://pettingzoo.farama.org/main/content/basic_usage/)
- [WebArena](https://proceedings.iclr.cc/paper_files/paper/2024/hash/4410c0711e9154a7a2d26f9b3816d1ef-Abstract-Conference.html)
- [OSWorld](https://papers.nips.cc/paper_files/paper/2024/hash/5d413e48f84dc61244b6be550f1cd8f5-Abstract-Datasets_and_Benchmarks_Track.html)
- [SWE-bench Verified](https://www.swebench.com/verified.html)
- [tau-bench](https://openreview.net/pdf?id=roNSXZpUDN)
- [METR time horizons](https://metr.org/time-horizons/)
- [RE-Bench](https://arxiv.org/abs/2411.15114)
- [SentinelBench](https://arxiv.org/abs/2606.05342)
- [UltraHorizon](https://arxiv.org/abs/2509.21766)
- [AgentDojo](https://proceedings.nips.cc/paper_files/paper/2024/hash/97091a5177d8dc64b1da8bf3e1f6fb54-Abstract-Datasets_and_Benchmarks_Track.html)
- [OWASP Agentic Applications Top 10 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
- [gVisor](https://gvisor.dev/docs/)
- [Firecracker production host setup](https://github.com/firecracker-microvm/firecracker/blob/main/docs/prod-host-setup.md)
- [OPA](https://www.openpolicyagent.org/docs)
- [NIST AI RMF](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10)
