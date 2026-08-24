# 11 - Specialized Agents

**Scope:** Coding, browser, research, and data agents as distinct production workloads.
**Study goal:** Choose an agent architecture from the environment's observation, action, verification, state, and permission boundaries rather than from a role prompt.

A specialized agent is the tuple `(observation interface, action space, verifier, durable state, permission boundary)`. The common loop is `scope -> observe -> plan -> act -> verify -> checkpoint -> terminate/escalate`; specialization changes what each verb means. A plausible transcript is never proof of success.

## 1. System Topology & Data Flow

### Shared control plane and specialized execution planes

```text
                                      CONTROL PLANE
┌──────────────────────────────────────────────────────────────────────────────┐
│ Identity/tenant │ risk/budget/deadline │ model route │ tool RBAC │ approval │
│ policy versions │ environment images  │ source/data licences │ kill switch  │
└───────────────┬──────────────────────────────────────────────────┬───────────┘
                │ signed run envelope                              │ policy
                ▼                                                  ▼
┌───────────────┐       ┌──────────────────────────────────────────────────────┐
│ User / API    ├──────►│ Durable orchestrator                                │
│ objective     │◄──────┤ scope → observe → plan → act → verify → checkpoint  │
└───────────────┘       └──────┬──────────────┬──────────────┬───────────────┘
                               │              │              │
               DATA PLANE      │              │              │
        ┌───────────────────────┘              │              └────────────────┐
        ▼                                      ▼                               ▼
┌──────────────┐  ┌──────────────┐    ┌──────────────┐                ┌──────────────┐
│ Coding plane │  │ Browser plane│    │ Research     │                │ Data plane   │
│ repo/AST     │  │ DOM/a11y/pix │    │ search/open  │                │ catalog/SQL  │
│ patch/shell  │  │ click/type   │    │ extract/calc │                │ Python/chart │
│ build/tests  │  │ receipt/state│    │ cite/entail  │                │ reconcile    │
└──────┬───────┘  └──────┬───────┘    └──────┬───────┘                └──────┬───────┘
       │ sandbox/ACI             │ origin/action policy │ source policy        │ RLS/masks
       ▼                         ▼                      ▼                      ▼
┌──────────────────────────────── TOOL PROXY LAYER ────────────────────────────┐
│ repo proxy │ container runner │ browser broker │ search/parser │ query proxy │
│ secret broker │ egress filter │ schema/size/deadline validation │ approvals   │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
                             PERSISTENCE AND EVIDENCE
┌──────────────────────────────────────────────────────────────────────────────┐
│ Event log │ checkpoints/leases │ worktree/commit │ browser receipt/context  │
│ source snapshots/claim ledger │ query job/snapshot/result │ artifact hashes  │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
                                  TELEMETRY
┌──────────────────────────────────────────────────────────────────────────────┐
│ OTel traces │ tool/model/cost/SLO │ verifier outcomes │ SIEM │ immutable audit│
└──────────────────────────────────────────────────────────────────────────────┘
```

The control plane is shared; execution identities and workers are not. A repository process must not inherit a browser session, a browser must not receive confidential research documents, and a Python visualization sandbox must not receive warehouse credentials.

### End-to-end request flow

1. Intake authenticates user, tenant, purpose, target resources, deadline, monetary/token/runtime budgets, and desired artifact. Policy classifies risk and selects one specialization or an explicit composition.
2. The orchestrator writes an immutable run envelope and provisions a domain environment: pinned worktree container, isolated browser context, source trust zone, or scoped warehouse session.
3. An observer retrieves the smallest useful state: symbols and failing tests; DOM/accessibility nodes plus pixels; opened source passages; or metric definitions, schemas, and approved samples.
4. The planner proposes a typed action. The proxy validates tool schema, resource, origin, arguments, bytes, cost, and side-effect class. High-risk actions require a fresh trusted approval bound to the exact effect.
5. The executor acts under a short-lived identity. Read failures may receive bounded retry; ambiguous writes never receive blind retry. External operation IDs are persisted before interpretation.
6. A domain verifier inspects environment state independently of the model's completion claim: clean tests and diff, business receipt/state, claim-to-evidence entailment, or query/cardinality/reconciliation invariants.
7. The orchestrator checkpoints the authoritative domain artifact and an event-log cursor. It repeats only while state changes, budgets remain, and a valid next action exists.
8. Termination returns verified artifacts and limits. Failure returns a typed reason; an unknown side effect returns `reconciliation_required`; insufficient evidence or semantic ambiguity escalates.
9. Traces record operational metadata. The immutable audit stores objective, policy, authorization, tools, protected argument hashes, approvals, artifacts, external IDs, verifier result, costs, and terminal state.

### Workload boundary comparison

| Agent | Grounded observation | Action surface | Authoritative verifier | Durable checkpoint | Default side-effect posture |
|---|---|---|---|---|---|
| Coding | repository, symbols, targeted files, command/test output, diff | search/read/patch/build/test; bounded shell | fail-to-pass + pass-to-pass tests, type/lint/SAST, diff policy, reviewer | base commit, patch/commit, image and test manifest | draft change; no autonomous merge/deploy |
| Browser | URL, DOM, accessibility tree, pixels, account/session, network/download events | navigate/click/type/select/upload/download/submit | resulting business state, receipt, downloaded hash, account/origin | encrypted context reference, last verified state, receipt/operation ID | read/fill first; consequential submit requires approval |
| Research | opened pages/PDFs, passages, source metadata, calculations, contradictions | search/open/extract/parse/calculate/synthesize | claim coverage, source quality, entailment, contradiction and citation checks | source snapshot/hash, evidence and claim ledgers, synthesis version | no external publication without review |
| Data | catalog, lineage, metrics, schemas, samples, query plan/job/result | explain/dry-run/read SQL, bounded Python/chart | grain/cardinality/null/total/freshness/policy/statistical checks | query text/hash, job and snapshot IDs, result/code/report hashes | read-only; DML outside autonomous scope |

## 2. Core Mechanics & Algorithms

### 2.1 Specialization model and state machine

Prompt specialization changes vocabulary; systems specialization changes authority and evidence. Define a workload as:

```text
W = (O, A, V, S, P, B)
O = observation encoder       A = typed action set
V = independent verifier      S = durable state/checkpoint
P = authorization policy      B = token/time/runtime/effect budgets
```

```text
             ┌─────────┐
             │ ADMITTED│
             └────┬────┘
                  ▼
             ┌─────────┐  insufficient scope  ┌──────────┐
        ┌───►│ OBSERVED├─────────────────────►│ESCALATED │
        │    └────┬────┘                      └──────────┘
        │         ▼
        │    ┌─────────┐  denied/budget        ┌──────────┐
        │    │ PLANNED ├─────────────────────►│TERMINATED│
        │    └────┬────┘                      └──────────┘
        │         ▼
        │    ┌─────────┐  unknown write        ┌──────────────┐
        │    │EXECUTING├─────────────────────►│RECONCILIATION│
        │    └────┬────┘                      └──────────────┘
        │         ▼
        │    ┌─────────┐  verified             ┌──────────┐
        │    │VERIFYING├─────────────────────►│COMPLETED │
        │    └────┬────┘                      └──────────┘
        │         │ correctable + progress
        └─────────┴── checkpoint/re-observe
```

The controller stops on verified success, permanent denial, hard budget/deadline, repeated `(observation hash, action hash)` with no verifier delta, maximum corrections, user cancellation, or required human judgment. It converges operationally because every loop consumes a finite budget and must improve a monotone verification measure such as failing-test count, unresolved required claims, or unmet result invariants. Open-ended prose quality is not monotone, so it needs explicit coverage and review thresholds.

### 2.2 Coding agents: repository state is ground truth

The Agent-Computer Interface (ACI) should expose absolute worktree paths and typed `search`, `read`, `patch`, `test`, `lint`, `build`, `diff`, and `submit` operations. A general shell remains useful for heterogeneous builds, but it belongs inside a non-root ephemeral container with CPU, RAM, PID, disk, time, syscall, mount, secret, and egress limits. A Git worktree isolates checked-out state; it is not a process security sandbox.

A productive loop is `map -> reproduce -> localize -> patch minimally -> targeted test -> regression/security suite -> clean-room verify -> draft PR`. Repository mapping builds a symbol/reference index in `O(F + R)` over file bytes `F` and references `R`; a symbol lookup is approximately `O(log S + k)` for `S` indexed symbols and `k` matches. Repeatedly sending the whole repository is both more expensive and less precise than retrieving symbols, callers, tests, and changed hunks.

Verification layers catch different failure modes:

1. Reproduction establishes a failing baseline and rejects already-fixed or environment-broken tasks.
2. Fail-to-pass tests establish the requested behavior; pass-to-pass tests detect regression.
3. Type, lint, SAST, dependency, generated-file, migration, and protected-path policies constrain the patch.
4. A clean worktree/container reruns the full declared suite, proving the result does not depend on untracked conversational state.
5. Human review owns architectural intent, auth/crypto/permissions, migrations, dependencies, production configuration, and merge.

Historical SWE-bench, SWE-agent, and OpenHands results establish that repository tasks and interface design matter; they do not provide a current production acceptance rate. Evaluate the model, ACI, image, resources, retry policy, and grader as one system on private temporal tasks.

### 2.3 Browser agents: pixels, structure, session, and business state

Browser observation is multimodal. DOM/accessibility locators are compact and stable for accessible controls; screenshots reveal canvas, visual ordering, occlusion, and rendering state; URL/title, network, download, and session metadata reveal context. The agent should reconcile them rather than assume pixels and DOM agree. A DOM traversal is `O(D)` nodes, screenshot processing is `O(P)` pixels, and repeated full observations make a trajectory `O(T(D + P))`; crop, diff, and retain semantic element references to bound context.

Each task gets an isolated BrowserContext and task/tenant-scoped encrypted authentication reference. Contexts isolate cookies and local storage, not the target application's account state; two contexts editing the same account can still conflict. Use an account/workflow lease for writes.

The safe loop is `assert origin/account -> observe -> act -> wait for event/state -> re-observe -> verify`. Never blind-click stale coordinates. Navigation/read, form-fill, upload, message, purchase, delete, credential change, and publish are distinct permissions. Immediately before a consequential submit, recompute origin, account, recipients, SKU/quantity, currency/amount, destination, and approval. CAPTCHA or anti-bot controls terminate in human handoff; the agent must not evade them.

Success is a business-state predicate, not a visible success string: `receipt.order_id exists`, `ticket.status == closed`, or `sha256(download) == expected`. A timeout after submit is an unknown effect. Look up receipt/status by idempotency or business key before considering resubmission.

### 2.4 Research agents: evidence graph before narrative

Research separates discovery from evidence. A search result is a lead; an opened primary source passage is evidence. Decompose the question into non-overlapping required facets, diversify queries, open sources, snapshot relevant content, extract evidence, identify contradictions, calculate in a retained code environment, synthesize only from the ledger, then verify every material claim.

Represent provenance as a bipartite graph `G = (C ∪ E, L)`, where claims `C` link to evidence records `E`. Each evidence record stores canonical source/document ID, content hash, publication/access dates, source type, locator, bounded passage, licence/trust zone, and contradiction status. Building coverage is `O(|C| + |E| + |L|)`; canonical-URL/content-hash deduplication is expected `O(U)` for `U` retrieved items. Source independence needs a provenance graph because ten articles derived from one filing are one origin, not ten confirmations.

A stopping score can be explicit:

```text
coverage = supported_required_claims / required_claims
stop when coverage >= threshold
     and no unresolved high-severity contradiction
     and independent evidence minima are met
     and marginal_supported_claims(last k searches) < epsilon
or deadline/cost cap is reached
```

The report labels sourced fact, attributed source claim, inference, calculation, uncertainty, and recommendation. A citation verifier checks that the opened source entails the adjacent claim and that the locator resolves in the frozen snapshot. Live-web evaluation also measures freshness, while frozen corpora measure reproducibility; neither substitutes for the other.

### 2.5 Data agents: semantic contract before SQL

The data agent first writes an analysis contract: metric definition, grain, dimensions, filters, eligible population, time zone/window, currency, missing-value rule, snapshot/freshness, output columns, and reconciliation checks. It retrieves only relevant governed catalog/lineage/semantic metadata and approved samples. Schema dumping is not semantic grounding.

The execution path is `contract -> catalog -> plan -> compile -> policy lint -> explain/dry-run -> cost gate -> read-only execute -> persist job/result -> validate -> bounded Python/chart -> report`. A SQL `LIMIT` bounds returned rows, not necessarily columnar scan cost; enforce warehouse-native maximum bytes, wall time, rows, and concurrency. Persist the job ID before model interpretation because a response timeout can hide a completed query.

Query parsing is `O(Q)` in query length; result checks are `O(N)` rows or `O(G)` aggregates. Join-order search is combinatorial in the number of relations, so rely on the warehouse optimizer and restrict the agent to approved join paths/semantic models. Detect join explosion with pre/post cardinality and key-grain assertions. SQL success proves syntax and execution, not business correctness.

Warehouse-enforced RLS, column masking, tenant/purpose policy, and a non-owner/non-`BYPASSRLS` identity are authoritative. Python/R visualization runs in a separate no-network sandbox over a bounded result artifact and never receives warehouse credentials.

### 2.6 Cross-domain decision rules and invariants

| Question | Coding | Browser | Research | Data |
|---|---|---|---|---|
| Best deterministic signal | tests + diff | backend business state | citation locator/coverage; semantics remain graded | query/result invariants |
| Safe retry unit | idempotent read/build/test | reads; never ambiguous submit | search/open by content key | catalog/dry-run; job status before resubmit |
| Parallelism boundary | disjoint worktrees/files, later merge | independent accounts/read tabs | independent facets, one synthesis owner | independent reads under byte/concurrency quota |
| Human boundary | merge and sensitive changes | consequential external action | publication/material uncertainty | high-impact conclusion or any mutation |
| Simpler workflow wins when | files/steps known | stable deterministic form/API | bounded known corpus/template | approved metric/query template exists |

System invariants:

- The verifier reads environment outcomes independently of the generation claim.
- One writer owns a worktree, account workflow, synthesis version, or mutable dataset at a time.
- Hostile repository/page/source/data content cannot expand tools, identity, egress, or output destination.
- Every external effect has authorization, exact approval where required, idempotency/business key, operation ID, and reconciliation path.
- Every artifact binds to input snapshot, environment/tool/model/policy versions, and a content hash.
- Context compression occurs only after a structured checkpoint; verbose reproducible output may be dropped after secure storage and hashing.
- Budget is multidimensional: tokens, steps, wall time, sandbox CPU, browser slots, search calls, bytes scanned, output bytes, effects, and human review.

## 3. Token Economics & NFR Analysis

### 3.1 Explicit cost per 1,000 runs

There is no protocol-level price or comparable public cost benchmark for these complete systems. Measure:

```text
C_1K = model tokens + sandbox/browser/search/warehouse + storage/egress/trace
     + retries + human review

model = (U·P_input + H·P_cache_read + W·P_cache_write + O·P_output) / 1,000,000
cost_per_1K_verified = C_1K × 1,000 / verified_runs_in_batch
```

**Illustrative assumptions as of 2026-08-21:** 1,000 runs, evenly split across four workloads, use 12M uncached input, 28M cached stable prefix/index/schema reads, 0.1M cache writes, and 4M output tokens. External costs are `$48` sandbox/build, `$28` browser/search, `$7` document parsing, `$20` warehouse/Python, and `$12` storage/trace: **$115/1K runs**. Human review is excluded and must be added from measured minutes. Rates are illustrative point-in-time model tiers, not a promise.

| Tier | No cache model math | Cached model math | Cached total with $115 runtime |
|---|---:|---:|---:|
| `sol` (`$5/$30`, read `$0.50`, write `$6.25`) | `40M×$5 + 4M×$30` = **$320.00** | `12×$5 + 28×$0.50 + .1×$6.25 + 4×$30` = **$194.63** | **$309.63/1K** |
| `terra` (`$2/$12`, read `$0.20`, write `$2.50`) | `40M×$2 + 4M×$12` = **$128.00** | `12×$2 + 28×$0.20 + .1×$2.50 + 4×$12` = **$77.85** | **$192.85/1K** |
| `luna` (`$0.20/$1.20`, read `$0.02`, write `$0.25`) | `40M×$.20 + 4M×$1.20` = **$12.80** | `12×$.20 + 28×$.02 + .1×$.25 + 4×$1.20` = **$7.79** | **$122.79/1K** |

Cache immutable system policy, tool schemas, dependency/index fragments keyed by base commit, source content keyed by hash, and catalog/metric metadata keyed by policy and schema version. Never share worktree state, browser auth, private evidence, raw query results, approval, or tenant-specific content across authorization partitions. If only 780 of the `terra` runs pass independent verification, cost per 1,000 verified outcomes is `$192.85×1000/780 = $247.24`, before review. Optimize that denominator, not token cost per attempt.

Workload attribution must include the external driver: CPU-minutes per accepted patch, browser-seconds and actions per completed workflow, searches/pages/parser time per supported report, and bytes scanned plus Python-seconds per reconciled analysis.

### 3.2 Latency SLOs

These are starting targets for an internal service, not public benchmark claims:

| Workload/operation | p50 | p95 | p99 | Tail mitigation |
|---|---:|---:|---:|---|
| Coding search/read/patch step | 150 ms | 1 s | 3 s | local index, bounded output, nearby sandbox |
| Coding verified draft PR | 6 min | 20 min | 45 min | dependency cache, targeted then full tests, large-build pool |
| Browser action-to-observation | 400 ms | 2 s | 6 s | event waits, stable locators, per-origin breaker |
| Browser verified workflow | 20 s | 90 s | 4 min | bounded replans, session warm-up, human handoff |
| Research first sufficient evidence | 20 s | 2 min | 5 min | parallel independent facets, source cache, parser bulkhead |
| Research verified report | 2 min | 8 min | 20 min | coverage stop rule, citation stage, deadline-aware synthesis |
| Data dry-run/read query | 1 s | 8 s | 30 s | catalog cache, warehouse slots, byte gate |
| Data verified report | 8 s | 45 s | 2 min | materialized aggregates, bounded result, async job lookup |

Report queue, model, tool, environment, verification, approval, and reconciliation separately. Human wait is not machine latency. For long tasks, expose time to first evidence/progress, checkpoint freshness, and final verified artifact.

### 3.3 Capacity and back-pressure

Use Little's Law `L = λW` on occupied scarce resources. For illustrative peak arrivals and mean occupancy:

```text
coding:   1 task/min × 12 min = 12 sandboxes; +50% headroom = 18
browser: 10 tasks/min × 1.5 min = 15 contexts; +30% headroom = 20
research: 2 tasks/min × 6 min = 12 run slots; +50% headroom = 18
data:    12 tasks/min × .75 min = 9 query slots; +50% headroom = 14
```

These are separate pools, not 70 interchangeable workers. Also size model concurrency, build CPU/RAM, browser processes and target-account leases, search/parser quotas, warehouse slots/bytes, event-log writes, artifact bandwidth, and trace ingestion. A research fan-out of five makes `2 tasks/min` become `10 worker starts/min`; a coding full suite can occupy much more CPU than its loop.

Admission uses tenant/risk/resource weighted fair queues. Reserve capacity for cancel, status, approval, and unknown-effect reconciliation. Bound per run and globally: steps, repeated-state hashes, child fan-out, command output, screenshots, open tabs, searches, pages, parser bytes, query bytes/rows, artifact size, and retries. On saturation, defer low priority work, reduce research fan-out, route to fixed workflows, return read-only evidence, or escalate. Never allow every layer to retry independently.

### 3.4 NFR scorecard and trade-offs

| NFR | Target/evidence | Trade-off |
|---|---|---|
| Availability | 99.9% admission/read/verify; 99.99% effect status/idempotency/audit | More durable state and reserved capacity cost money. |
| RPO | 0 approvals/effects/audit; ≤1 checkpoint step; ≤5 min aggregate telemetry | Frequent checkpoints increase storage and latency. |
| RTO | ≤15 min control/status; ≤60 min worker pools; rebuild from immutable artifacts | Warm sandboxes/browsers improve RTO but consume capacity. |
| Correctness | domain verifier and private temporal eval confidence intervals | Strong gates lower apparent success and increase review. |
| Security | zero cross-tenant/origin/repository/dataset escape in adversarial suite | Least privilege reduces generic autonomy. |
| Reproducibility | pinned commit/image/source snapshot/query snapshot and grader | Live sites and sources cannot be perfectly replayed. |
| Compliance | data classification, licence/residency/retention/deletion, review evidence | Research/browser vendors expand processors and data flows. |
| Operability | trace replay, kill switch, DLQ/repair, version rollback, capacity dashboard | More observability can capture sensitive data unless minimized. |

Benchmark scores are harness results, not model constants. Record dataset/version/date, model, prompts, ACI/tools, image/resources, attempts/hints, environment, and grader. Do not compare original, verified, live, frozen, pass@1, and pass@k scores as if they measured the same system.

## 4. Distributed Resilience & Security

### 4.1 Durable execution and explicit state ownership

```text
┌──────────────┐ start/signal ┌──────────────┐ lease/task ┌──────────────┐
│ API / UI     ├─────────────►│ Temporal     ├───────────►│ Domain worker│
│ approval     │◄─status──────┤ workflow     │◄─result────┤ sandbox      │
└──────────────┘              └──────┬───────┘            └──────┬───────┘
                                    │ events/checkpoint          │ tool call
                                    ▼                            ▼
                             ┌──────────────┐             ┌──────────────┐
                             │ DB/artifacts │             │ MCP/tool     │
                             │ effect ledger│             │ proxy        │
                             └──────┬───────┘             └──────┬───────┘
                                    │ outbox                     │
                                    ▼                            ▼
                             ┌──────────────┐             ┌──────────────┐
                             │ Kafka/DLQ    │             │ Repo/browser│
                             │ audit/OTel   │             │ search/data │
                             └──────────────┘             └──────────────┘
```

Temporal or an equivalent durable workflow engine owns deadlines, retries, timers, approvals, cancellation, and replay. Workflow code records decisions deterministically; side effects occur in activities with idempotency keys. A transactional outbox publishes state changes to Kafka. Consumers deduplicate by event ID. Poison events go to a DLQ with artifact references and policy-safe diagnostics; they are never endlessly replayed.

| Domain | Authoritative checkpoint | Recovery | Lock/lease |
|---|---|---|---|
| Coding | base commit, image/lockfile, patch/commit, test manifest/log hashes | recreate clean worktree; restart from last green commit | one writer/worktree or declared file ownership; merge normally |
| Browser | encrypted auth reference, URL, account/origin, last verified business state, receipt ID | reopen and re-observe; reconcile backend before submit | one write workflow per account/scarce object |
| Research | question/facet plan, canonical URL + source hash/snapshot, evidence/claim ledgers | reuse snapshot; refresh only under explicit freshness SLA | workers append evidence; one synthesis version owner |
| Data | analysis contract, catalog/policy/metric versions, SQL/hash, job/snapshot/result IDs | query job status/result before resubmit | warehouse quotas for reads; transaction lock for exceptional writes |

Workers carry `run_id`, `step_id`, `attempt`, `tenant`, `deadline`, and effect idempotency key. Leases expire and are fenced by monotonic token so a late worker cannot overwrite a newer checkpoint. Cancellation propagates to process groups, downloads, searches/parsers, and warehouse jobs. After cancellation, an external effect can still require reconciliation.

### 4.2 Retry, breaker, and failure taxonomy

Use exponential full jitter `sleep ~ U(0, min(cap, base·2^attempt))` only for known transient, idempotent operations and inside an aggregate deadline. Breakers are keyed by dependency and operation class; a failed public search provider may fall back, while repository publish, browser purchase, and data mutation fail closed. Breaker transitions are `closed -> open` after threshold, `open -> half_open` after cooldown, and `half_open -> closed` on a successful probe.

| Failure | Class/detection | Required response |
|---|---|---|
| Model/provider timeout | transient read/plan | bounded primary retry, secondary, deterministic safe fallback |
| Repeated state/action hash | permanent for current plan | checkpoint and replan once; then terminate/escalate |
| Coding build infra failure | transient if reproducible infra signal | retry clean worker; keep code failure distinct |
| Coding regression/security gate | domain permanent/correctable | return evidence to loop; never publish until clean |
| Browser stale locator | transient observation drift | re-observe and re-resolve; never blind-click |
| Browser submit timeout | unknown effect | freeze action; receipt/status reconciliation; no blind retry |
| Research parser/search outage | transient read | alternate approved source/parser; record coverage gap |
| Unsupported/contradictory claim | semantic permanent until evidence changes | revise/qualify/remove or human review |
| Data query timeout | ambiguous job state | lookup job ID/result before resubmit |
| Query grain/cardinality failure | domain permanent/correctable | revise plan under same snapshot and budget |
| Policy/auth/schema denial | permanent | no retry; surface policy ID and safe remediation |
| Poison task/event | repeated deterministic failure | DLQ, quarantine artifacts, operator repair |

### 4.3 Zero Trust MCP and least privilege

Treat MCP/tool interoperability as transport and discovery, not trust. The host authenticates the user/workload, filters the catalog, namespaces servers, and enforces data-flow policy. Each server/tool uses its own audience-bound short-lived identity. Never pass a broad host token through to a repository, browser account, search vendor, or warehouse.

Example tool RBAC:

| Role | Coding | Browser | Research | Data |
|---|---|---|---|---|
| `observer` | search/read/diff | navigate/read/extract | search/open | catalog/explain |
| `proposer` | patch/test | fill/cart/preview | extract/calculate/draft | dry-run/read query |
| `approver` | draft PR approval | message/purchase/publish | external publication | high-impact report/export |
| `operator` | image/policy/admin | credential/session admin | source/licence admin | policy/metric/admin |

Tool descriptions, repository text, pages, sources, query results, and model output are untrusted content. They cannot change system instructions, allowed origins, mounted paths, dataset policy, credentials, approval, or output destination. Validate paths after canonicalization, commands without shell interpolation where possible, URLs against scheme/host/IP policy, SQL through parser/policy plus warehouse enforcement, and outputs by type/size/provenance.

### 4.4 Domain containment and PII

- **Coding:** non-root ephemeral container/VM; pinned image/dependencies; worktree-only mount; default-deny egress; no host Docker socket; bounded resources; no secrets for untrusted PR code; minimal CI token; protected branches and environments.
- **Browser:** task/tenant context; encrypted auth state outside model context/traces; origin and account assertions; cross-origin information-flow policy; downloads quarantined; fresh exact-effect approval; anti-bot handoff.
- **Research:** public and confidential trust zones; licensed/internal source ACLs; no confidential upload to public search/model; opened-source provenance; calculation sandbox without network; publication scan.
- **Data:** warehouse-enforced RLS/masking/purpose/tenant; read-only non-owner identity without bypass roles; query/byte/row/time/export limits; separate visualization sandbox; policy-compatible result cache only.

The PII pipeline is `classify purpose -> detect -> minimize -> redact/tokenize -> authorize destination/tool -> execute -> rehydrate only at approved boundary -> retain/delete -> audit`. Apply it to objectives, repository files, screenshots, browser forms, downloaded documents, sources, SQL literals, query results, prompts, caches, artifacts, traces, eval fixtures, and backups. Hashing is not anonymization when inputs are enumerable.

### 4.5 Immutable audit and incident evidence

Audit: tenant/user/workload, run/step/attempt, objective hash and permitted fields, model/prompt/tool/schema/image/policy versions, repository/origin/source/dataset, authorization and approval actor, normalized protected arguments, observation/artifact hashes, external operation/job/receipt, result/error class, verifier inputs/outcome, token/CPU/browser/search/scan usage, cache, retry, cancellation, redaction, and terminal state. Record observable decisions and effects, not hidden reasoning text.

Write security audit through a transactional outbox to append-only WORM storage; sign/hash-chain batches and audit access to the audit. Keep operational traces shorter-lived and redacted. For incident reconstruction, preserve chain of custody from input snapshot through every authorized tool call to final verified artifact, while respecting source licences and data deletion obligations.

## 5. Production Enterprise Code

This Python 3.11 standard-library program implements the control boundary, not a fake autonomous shell. It produces typed plans for all four domains, authorizes them, runs a deterministic environment adapter, applies domain-specific verification, records events, retries only safe operations with exponential full jitter, opens and half-opens breakers, and uses primary -> secondary -> deterministic escalation. Replace `SpecializedBackend` and `DemoModel` with real sandbox/browser/search/warehouse and model adapters behind the same contracts.

```python
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Protocol, Sequence


class Domain(str, Enum):
    CODING = "coding"
    BROWSER = "browser"
    RESEARCH = "research"
    DATA = "data"


class TransientFailure(RuntimeError):
    """A safe-to-retry dependency failure."""


class PermanentFailure(RuntimeError):
    """A policy, schema, or semantic failure."""


class CircuitOpen(TransientFailure):
    """A dependency is temporarily unavailable."""


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {"timestamp": time.time(), "level": record.levelname,
                 "message": record.getMessage()}
        for key in ("run_id", "step_id", "attempt", "domain", "stage",
                    "model", "status"):
            if hasattr(record, key):
                value[key] = getattr(record, key)
        return json.dumps(value, separators=(",", ":"), sort_keys=True)


logger = logging.getLogger("specialized-agent")
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
                    raise CircuitOpen("half-open probe already active")
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
class Plan:
    domain: Domain
    action: str
    arguments: dict[str, object]
    side_effect: bool


@dataclass(frozen=True)
class Outcome:
    run_id: str
    status: str
    artifact: dict[str, object]
    verifier: dict[str, object]


@dataclass(frozen=True)
class Approval:
    effect_digest: str
    expires_at: float


class PlannerModel(Protocol):
    name: str

    def plan(self, domain: Domain, objective: str, timeout_s: float) -> str:
        raise RuntimeError("PlannerModel is an interface")


class DemoModel:
    ACTIONS = {
        Domain.CODING: ("patch_and_test", False),
        Domain.BROWSER: ("purchase", True),
        Domain.RESEARCH: ("search_sources", False),
        Domain.DATA: ("read_query", False),
    }

    def __init__(self, name: str, available: bool):
        self.name = name
        self._available = available

    def plan(self, domain: Domain, objective: str, timeout_s: float) -> str:
        if not self._available or timeout_s <= 0:
            raise TransientFailure(f"{self.name} unavailable")
        action, side_effect = self.ACTIONS[domain]
        return json.dumps({"action": action, "sideEffect": side_effect,
                           "arguments": {"objective": objective[:160]}})


class DeterministicFallback:
    name = "deterministic"

    def plan(self, domain: Domain, objective: str, timeout_s: float) -> str:
        return json.dumps({"action": "escalate", "sideEffect": False,
                           "arguments": {"reason": "models_unavailable",
                                         "objectiveHash": hashlib.sha256(
                                             objective.encode()).hexdigest()}})


class PlannerChain:
    def __init__(self, models: Sequence[PlannerModel]):
        if len(models) < 2:
            raise ValueError("primary and secondary models required")
        self._models = tuple(models)
        self._fallback = DeterministicFallback()
        self._breakers = {model.name: Breaker() for model in models}

    def create(self, domain: Domain, objective: str, deadline: float,
               run_id: str, step_id: str) -> tuple[Plan, str]:
        for model in self._models:
            breaker = self._breakers[model.name]
            for attempt in range(1, 3):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._decode(self._fallback, domain, objective, .01), \
                           self._fallback.name
                try:
                    breaker.before()
                    raw = model.plan(domain, objective, min(remaining, 2.0))
                    plan = self._decode_raw(domain, raw)
                    breaker.success()
                    return plan, model.name
                except CircuitOpen:
                    break
                except (TransientFailure, TimeoutError, json.JSONDecodeError,
                        PermanentFailure) as exc:
                    breaker.failure()
                    logger.warning("planner failure", extra={
                        "run_id": run_id, "step_id": step_id,
                        "attempt": attempt, "domain": domain.value,
                        "stage": "planning", "model": model.name,
                        "status": type(exc).__name__})
                    if attempt < 2:
                        cap = min(.025, max(0.0, deadline-time.monotonic()))
                        time.sleep(random.uniform(0.0, cap))
        return self._decode(self._fallback, domain, objective, .01), \
               self._fallback.name

    @staticmethod
    def _decode(model: PlannerModel, domain: Domain, objective: str,
                timeout_s: float) -> Plan:
        return PlannerChain._decode_raw(
            domain, model.plan(domain, objective, timeout_s)
        )

    @staticmethod
    def _decode_raw(domain: Domain, raw: str) -> Plan:
        value = json.loads(raw)
        if (not isinstance(value, dict)
                or not isinstance(value.get("action"), str)
                or not isinstance(value.get("arguments"), dict)
                or not isinstance(value.get("sideEffect"), bool)):
            raise PermanentFailure("invalid plan schema")
        return Plan(domain, value["action"], value["arguments"],
                    value["sideEffect"])


class Policy:
    ALLOWED = {
        Domain.CODING: {"patch_and_test", "escalate"},
        Domain.BROWSER: {"purchase", "escalate"},
        Domain.RESEARCH: {"search_sources", "escalate"},
        Domain.DATA: {"read_query", "escalate"},
    }

    @staticmethod
    def effect_digest(plan: Plan) -> str:
        value = {"domain": plan.domain.value, "action": plan.action,
                 "arguments": plan.arguments, "sideEffect": plan.side_effect}
        return hashlib.sha256(json.dumps(
            value, separators=(",", ":"), sort_keys=True
        ).encode()).hexdigest()

    @classmethod
    def approve(cls, plan: Plan, lifetime_s: float = 60.0) -> Approval:
        return Approval(cls.effect_digest(plan), time.time()+lifetime_s)

    def authorize(self, plan: Plan, approval: Approval | None) -> None:
        if plan.action not in self.ALLOWED[plan.domain]:
            raise PermanentFailure("action outside domain allowlist")
        if plan.domain is Domain.BROWSER and plan.side_effect:
            if (approval is None or approval.expires_at < time.time()
                    or not hmac.compare_digest(
                        approval.effect_digest, self.effect_digest(plan))):
                raise PermanentFailure("fresh exact-effect approval required")
        if plan.domain is Domain.DATA and plan.side_effect:
            raise PermanentFailure("autonomous data mutation denied")


class EventStore:
    def __init__(self):
        self._events: list[dict[str, object]] = []
        self._lock = threading.Lock()

    def append(self, event: dict[str, object]) -> None:
        encoded = json.dumps(event, separators=(",", ":"), sort_keys=True)
        durable = {"payload": event,
                   "sha256": hashlib.sha256(encoded.encode()).hexdigest()}
        with self._lock:
            self._events.append(durable)

    def count(self) -> int:
        with self._lock:
            return len(self._events)


class SpecializedBackend:
    """Deterministic adapter illustrating four real environment contracts."""

    def __init__(self):
        self._transient_budget = {"research:search_sources": 1}
        self._effects: dict[str, tuple[str, dict[str, object]]] = {}
        self._lock = threading.Lock()

    def execute(self, plan: Plan, idempotency_key: str) -> dict[str, object]:
        operation = f"{plan.domain.value}:{plan.action}"
        if self._transient_budget.get(operation, 0) > 0:
            self._transient_budget[operation] -= 1
            raise TransientFailure("approved dependency temporarily unavailable")

        digest = hashlib.sha256(json.dumps(
            {"action": plan.action, "arguments": plan.arguments},
            separators=(",", ":"), sort_keys=True
        ).encode()).hexdigest()
        if plan.side_effect:
            with self._lock:
                prior = self._effects.get(idempotency_key)
                if prior:
                    if prior[0] != digest:
                        raise PermanentFailure(
                            "idempotency key reused with changed arguments")
                    return dict(prior[1])

        if plan.domain is Domain.CODING:
            result = {"baseCommit": "abc123", "changedFiles": ["src/tax.py"],
                      "failToPass": True, "passToPass": True,
                      "typeLintSast": True, "diffPolicy": True,
                      "artifact": "patch:sha256:2f4a"}
        elif plan.domain is Domain.BROWSER:
            result = {"origin": "https://approved.vendor.example",
                      "account": "procurement-bot", "orderId": "ord-2048",
                      "amount": 75, "currency": "USD", "status": "submitted"}
        elif plan.domain is Domain.RESEARCH:
            result = {"requiredClaims": 2, "supportedClaims": 2,
                      "unresolvedHighContradictions": 0,
                      "openedPrimarySources": 2,
                      "evidenceLedgerHash": "sha256:91ce"}
        elif plan.domain is Domain.DATA:
            result = {"readOnly": True, "rowPolicyApplied": True,
                      "bytesScanned": 8_000_000, "byteCap": 10_000_000,
                      "grainValid": True, "reconciled": True,
                      "jobId": "job-778", "snapshot": "2026-08-21T00:00Z"}
        else:
            raise PermanentFailure("unsupported domain")

        if plan.side_effect:
            with self._lock:
                self._effects[idempotency_key] = (digest, dict(result))
        return result


class DomainVerifier:
    @staticmethod
    def verify(domain: Domain, artifact: dict[str, object]) -> dict[str, object]:
        if domain is Domain.CODING:
            checks = (artifact.get("failToPass"), artifact.get("passToPass"),
                      artifact.get("typeLintSast"), artifact.get("diffPolicy"))
        elif domain is Domain.BROWSER:
            checks = (artifact.get("origin") == "https://approved.vendor.example",
                      isinstance(artifact.get("orderId"), str),
                      artifact.get("status") == "submitted")
        elif domain is Domain.RESEARCH:
            checks = (artifact.get("supportedClaims") ==
                      artifact.get("requiredClaims"),
                      artifact.get("unresolvedHighContradictions") == 0,
                      int(artifact.get("openedPrimarySources", 0)) >= 2)
        elif domain is Domain.DATA:
            checks = (artifact.get("readOnly"), artifact.get("rowPolicyApplied"),
                      int(artifact.get("bytesScanned", 1)) <=
                      int(artifact.get("byteCap", 0)),
                      artifact.get("grainValid"), artifact.get("reconciled"))
        else:
            checks = (False,)
        return {"passed": all(value is True for value in checks),
                "checkCount": len(checks)}


class AgentRunner:
    def __init__(self, planner: PlannerChain, backend: SpecializedBackend,
                 policy: Policy, store: EventStore):
        self._planner = planner
        self._backend = backend
        self._policy = policy
        self._store = store
        self._tool_breakers: dict[str, Breaker] = {}

    def run(self, domain: Domain, objective: str, *,
            approval: Approval | None = None,
            timeout_s: float = 3.0) -> Outcome:
        run_id, step_id = uuid.uuid4().hex, uuid.uuid4().hex
        deadline = time.monotonic() + timeout_s
        plan, model = self._planner.create(
            domain, objective, deadline, run_id, step_id
        )
        self._policy.authorize(plan, approval)
        self._store.append({"runId": run_id, "stepId": step_id,
                            "type": "plan", "model": model,
                            "plan": asdict(plan)})
        if plan.action == "escalate":
            outcome = Outcome(run_id, "degraded_human_required",
                              dict(plan.arguments), {"passed": False,
                                                     "checkCount": 0})
            self._log(outcome, domain, step_id, model)
            return outcome

        operation = f"{domain.value}:{plan.action}"
        breaker = self._tool_breakers.setdefault(operation, Breaker())
        artifact: dict[str, object] | None = None
        max_attempts = 1 if plan.side_effect else 3
        for attempt in range(1, max_attempts + 1):
            try:
                breaker.before()
                artifact = self._backend.execute(plan, f"{run_id}:{plan.action}")
                breaker.success()
                break
            except CircuitOpen:
                break
            except TransientFailure:
                breaker.failure()
                if plan.side_effect:
                    outcome = Outcome(run_id, "reconciliation_required", {},
                                      {"passed": False, "checkCount": 0})
                    self._log(outcome, domain, step_id, model)
                    return outcome
                if attempt < max_attempts and time.monotonic() < deadline:
                    cap = min(.05 * (2 ** (attempt-1)),
                              max(0.0, deadline-time.monotonic()))
                    time.sleep(random.uniform(0.0, cap))

        if artifact is None:
            outcome = Outcome(run_id, "degraded_dependency_unavailable", {},
                              {"passed": False, "checkCount": 0})
            self._log(outcome, domain, step_id, model)
            return outcome

        verifier = DomainVerifier.verify(domain, artifact)
        status = "verified" if verifier["passed"] else "verification_failed"
        self._store.append({"runId": run_id, "stepId": step_id,
                            "type": "outcome", "status": status,
                            "artifactHash": hashlib.sha256(json.dumps(
                                artifact, separators=(",", ":"),
                                sort_keys=True).encode()).hexdigest(),
                            "verifier": verifier})
        outcome = Outcome(run_id, status, artifact, verifier)
        self._log(outcome, domain, step_id, model)
        return outcome

    @staticmethod
    def _log(outcome: Outcome, domain: Domain, step_id: str,
             model: str) -> None:
        logger.info("run terminal", extra={
            "run_id": outcome.run_id, "step_id": step_id,
            "domain": domain.value, "stage": "terminal",
            "model": model, "status": outcome.status})


def main() -> None:
    store = EventStore()
    policy = Policy()
    runner = AgentRunner(
        PlannerChain((DemoModel("primary", False),
                      DemoModel("secondary", True))),
        SpecializedBackend(), policy, store
    )
    browser_objective = "Buy one approved keyboard for USD 75"
    browser_plan = Plan(Domain.BROWSER, "purchase",
                        {"objective": browser_objective}, True)
    outcomes = [
        runner.run(Domain.CODING, "Fix tax rounding and open a draft patch"),
        runner.run(Domain.BROWSER, browser_objective,
                   approval=policy.approve(browser_plan)),
        runner.run(Domain.RESEARCH, "Verify two material supplier claims"),
        runner.run(Domain.DATA, "Reconcile tenant revenue by month"),
    ]
    outage_runner = AgentRunner(
        PlannerChain((DemoModel("primary-down", False),
                      DemoModel("secondary-down", False))),
        SpecializedBackend(), policy, store
    )
    outcomes.append(outage_runner.run(
        Domain.RESEARCH, "Prepare evidence while model providers are down"
    ))
    print(json.dumps({"statuses": [item.status for item in outcomes],
                      "eventCount": store.count()},
                     separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
```

The adapter intentionally makes the first research read fail transiently; the runner retries it with jitter and verifies the final evidence. The browser purchase requires an expiring exact-effect digest, has one execution attempt, and uses a domain idempotency ledger. The primary model opens its breaker after bounded failures, the secondary serves normal work, and the separate all-model outage returns deterministic `degraded_human_required` without tools or side effects.

## 6. Architectural System Design Scenarios

### Scenario 1 - Enterprise issue-to-draft-PR agent with UI verification

**Problem statement.** Design an agent for 500 repositories and 300 engineering tasks/day. Issue and PR text are untrusted; builds range from 2 to 40 minutes. The service may create reviewed draft PRs but cannot merge or deploy. Frontend changes require browser verification. Target p95 draft time is 25 minutes, RPO is zero for publication/audit, and sensitive paths require named reviewers.

**Proposed architecture.** Admission pins tenant, repository, base commit, issue, protected paths, budget, and acceptance criteria. A Temporal workflow provisions a non-root ephemeral container plus one worktree. A repository mapper supplies symbol-scoped context. The coding loop uses typed patch/test tools and no production credential. Targeted tests precede clean-room full tests, type/lint/SAST/dependency/diff policy. For UI changes, a separate Playwright worker starts the built artifact, observes DOM/accessibility and screenshots, executes acceptance steps, and verifies resulting state. A publisher with a narrow token creates a signed draft PR only after both verifier manifests pass; branch policy and human review own merge.

```text
┌──────────────┐  signed task  ┌──────────────┐  lease     ┌──────────────┐
│ Engineer/API ├──────────────►│ Temporal +   ├───────────►│ Code sandbox │
│ reviewer     │◄─draft/evidence┤ policy       │            │ worktree/ACI │
└──────────────┘               └──────┬───────┘            └──────┬───────┘
                                      │                            │ patch/tests
                                      │ UI artifact                ▼
                                      │                     ┌──────────────┐
                                      ├────────────────────►│ Browser      │
                                      │                     │ verifier     │
                                      ▼                     └──────┬───────┘
                               ┌──────────────┐                     │ state/pixels
                               │ Artifact DB  │◄────────────────────┘
                               │ audit/outbox │
                               └──────┬───────┘
                                      │ both manifests verified
                                      ▼
                               ┌──────────────┐
                               │ Draft PR     │──► human review; no auto-merge
                               └──────────────┘
```

At 300/day with a six-hour engineering peak, arrival is about `50/hour`. At a 12-minute mean occupied sandbox, `L = 50×0.2 = 10`; provision 15 ordinary sandboxes plus a separately limited large-build pool. Cache immutable dependency layers by image/lockfile and repository indexes by base commit. Never cache mutable worktrees or secrets. One writer owns each worktree; UI accounts are unique per run. A lost PR-create response is reconciled by run marker before retry.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security posture | Scalability ceiling |
|---|---|---|---|---|---|
| **Durable coding loop + independent browser/clean-room verifier** | Medium-high compute | Parallel UI/full verification meets tail goal | High: images, pools, workflow, graders | Strong separation and evidence before publish | High with repository/resource pools |
| One agent in a persistent shared runner | Low warm-start cost | Fast median, noisy tail | Medium initially | Weak tenant/state/secret isolation | Contention and contaminated state |
| Fixed codemod/CI workflow only | Lowest for known changes | Fast and predictable | Low-medium | Strong narrow authority | Low on novel multi-file issues |

**Decision rationale.** The durable specialized loop is warranted for novel repository tasks, while deterministic codemods remain the route for enumerable changes. Independent clean-room and browser verification prevent the coding transcript from grading itself. Draft-only publication, protected-path routing, and sandbox separation bound authority without pretending tests prove architectural correctness.

### Scenario 2 - Regulated due-diligence research and analytics agent

**Problem statement.** Design a multi-tenant service producing 400 supplier-risk reports/day from regulator sites, corporate filings, licensed research, confidential internal documents, and Snowflake/PostgreSQL metrics. Every material claim must be reviewable; confidential data cannot reach public search. The service permits read-only queries, targets p95 report time under 12 minutes, supported-material-claim rate above 98%, RPO zero for evidence/audit, and reproducible reports for seven years.

**Proposed architecture.** Intake classifies purpose, supplier, jurisdictions, source licences, datasets, freshness, and reviewer. A lead workflow decomposes legal, sanctions, financial, operational, and contradiction facets. Public-search workers run in a public trust zone; licensed/internal retrieval runs in a private zone. Both write immutable source snapshots and evidence records, never cross-zone raw documents. A synthesis worker reads only the claim ledger. Data work begins from an approved metric/grain contract; a policy proxy lints and dry-runs SQL, enforces RLS/masking and bytes, then persists job/snapshot/result before a no-network Python verifier reconciles totals. Citation and data verifiers produce signed manifests. DLP/licence policy scans the report and an analyst approves publication.

```text
┌──────────────┐ question/policy ┌──────────────┐ facets  ┌────────────────┐
│ Analyst      ├────────────────►│ Durable lead ├────────►│ Public research│
│ approval     │◄─report/evidence┤ workflow     │         │ untrusted web  │
└──────────────┘                 └──────┬───────┘         └───────┬────────┘
                                       │                          │ snapshots
                                       ├──────────────►┌──────────▼────────┐
                                       │               │ Private retrieval │
                                       │               │ licensed/internal │
                                       │               └──────────┬────────┘
                                       ▼                          ▼
                               ┌───────────────────────────────────────────┐
                               │ Evidence + claim ledger / immutable audit │
                               └──────────┬─────────────────────┬──────────┘
                                          │                     │ contract
                                          ▼                     ▼
                               ┌──────────────┐          ┌──────────────┐
                               │ Citation     │          │ Query proxy  │
                               │ verifier     │          │ RLS/dry-run  │
                               └──────┬───────┘          └──────┬───────┘
                                      │                         ▼
                                      │                  ┌──────────────┐
                                      │                  │ Result/Python│
                                      │                  │ verifier     │
                                      │                  └──────┬───────┘
                                      └──────────┬──────────────┘
                                                 ▼
                                          ┌──────────────┐
                                          │ DLP/licence  │──► analyst publication
                                          └──────────────┘
```

With 400/day over an eight-hour peak, arrival is `50/hour`. At six minutes average machine time, mean concurrency is five lead runs; provision eight, while independently sizing facet workers, parser CPU, search quotas, and warehouse slots. Cap five facets/run, 30 opened sources, 10M query bytes, 10k result rows, and two correction cycles. Cache public pages by content hash and private sources/results only within tenant, licence, policy, and snapshot partitions. Contradiction or insufficient evidence degrades to an explicit gap, never invented certainty.

**Trade-off evaluation.**

| Approach | Cost | Latency | Ops complexity | Security/governance | Scalability ceiling |
|---|---|---|---|---|---|
| Single model with web and database credentials | Lowest initial platform cost | Serial; variable | Low initially | Unacceptable data-flow and confused-deputy risk | Context and credential bottleneck |
| **Trust-zoned research + governed data workers + evidence synthesis** | Higher worker/storage cost | Parallel facets meet p95 with caps | High: ledgers, policy, snapshots, graders | Strong provenance, RLS, licence and DLP boundary | High with separate quota pools |
| Analyst-only manual process | High labor cost | Slow and queue-bound | Low platform complexity | Strong judgment, inconsistent evidence mechanics | Limited by specialist headcount |

**Decision rationale.** The recommended design keeps source discovery, confidential retrieval, governed computation, synthesis, and publication in separate authority zones. Parallelism is used only for independent facets; one synthesis owner resolves claims. Warehouse policy and result invariants provide deterministic evidence where possible, while an analyst retains accountability for material semantic conclusions.

## Interview Review

1. **What makes an agent specialized?** Its observation, action, verifier, durable state, and permission boundary, not its role prompt.
2. **Which has the strongest verifier?** Coding often has deterministic tests, but tests can be incomplete; browser and data can verify environment state; research depends most on provenance and calibrated judgment.
3. **Why not retry every tool?** A lost read is usually retryable; a lost purchase, publish, or mutation can already have committed and requires reconciliation.
4. **How do browser contexts help?** They isolate client storage; they do not isolate shared backend accounts or authorize cross-origin data flow.
5. **Why is valid SQL insufficient?** It can use the wrong grain, population, join, time window, policy, or statistical assumption.
6. **How should research stop?** Required-facet coverage, independent evidence minima, contradiction severity, marginal yield, deadline, and cost cap.
7. **What should be cached?** Immutable, authorization-compatible schemas/indexes/source content; never secrets, approvals, mutable state, or cross-tenant private artifacts.
8. **What is the primary production metric?** Cost and unsafe-action rate per independently verified accepted outcome, segmented by workload and risk.

## Primary References

- [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [SWE-agent and the Agent-Computer Interface](https://arxiv.org/abs/2405.15793)
- [OpenHands platform](https://arxiv.org/abs/2407.16741)
- [SWE-bench](https://proceedings.iclr.cc/paper_files/paper/2024/hash/edac78c3e300629acfe6cbe9ca88fb84-Abstract-Conference.html)
- [Agent evaluation and outcome grading](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- [Long-running agent harnesses](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [BrowserGym](https://arxiv.org/abs/2412.05467)
- [Playwright BrowserContext isolation](https://playwright.dev/docs/browser-contexts)
- [Playwright authentication-state warning](https://playwright.dev/docs/auth)
- [WebArena](https://proceedings.iclr.cc/paper_files/paper/2024/file/4410c0711e9154a7a2d26f9b3816d1ef-Paper-Conference.pdf)
- [OSWorld](https://arxiv.org/abs/2404.07972)
- [Production multi-agent research architecture](https://www.anthropic.com/engineering/multi-agent-research-system)
- [BrowseComp](https://openai.com/index/browsecomp/)
- [Frozen-corpus Deep Research Bench](https://arxiv.org/abs/2506.06287)
- [Data Agent Benchmark](https://arxiv.org/abs/2603.20576)
- [Spider 2.0](https://proceedings.iclr.cc/paper_files/paper/2025/hash/46c10f6c8ea5aa6f267bcdabcb123f97-Abstract-Conference.html)
- [BigQuery dry runs and maximum bytes](https://cloud.google.com/bigquery/docs/running-queries)
- [ScienceAgentBench](https://arxiv.org/abs/2410.05080)
- [Git worktree](https://git-scm.com/docs/git-worktree)
- [Agent containment](https://www.anthropic.com/engineering/how-we-contain-claude)
- [Kubernetes Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/)
- [AgentDojo](https://arxiv.org/abs/2406.13352)
- [Agentic browser cross-origin security](https://agent-security.cs.washington.edu/agentic_browsers_sop.html)
- [PostgreSQL row security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- [Snowflake row access policies](https://docs.snowflake.com/en/user-guide/security-row-using)
- [NIST Generative AI Profile](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)
