# 16 - Production

**Scope:** Docker, Kubernetes, APIs, queues, scaling, and reliability for agent and inference workloads.
**Study goal:** Turn code and models into an operated service with explicit artifact, admission, execution, ownership, capacity, release, and recovery contracts.

A container is not a production system. Kubernetes can reconcile pods while accepted jobs disappear. A queue can redeliver a payment after it committed. Autoscaling can respond after every deadline has expired. Production quality comes from connecting each platform mechanism to a user-visible guarantee and a tested failure boundary.

## 1. System Topology & Data Flow

### Three-path production topology

```text
                                        CONTROL PLANE
┌──────────────────────────────────────────────────────────────────────────────┐
│ Git/review -> CI/evals -> SBOM/scan/provenance/sign -> OCI/model registry    │
│ OpenAPI/event/schema registry │ policy/IAM │ GitOps/release/canary/rollback │
│ HPA/KEDA/node scaling policy │ SLO/error budget │ backup/DR/fencing/runbooks │
└──────────────┬─────────────────────┬──────────────────────┬──────────────────┘
               │ signed digests      │ desired state        │ policy/limits
               ▼                     ▼                      ▼
┌──────────────┐ TLS/OAuth  ┌─────────────────────────────────────────────────┐
│ client/agent ├───────────►│ edge/WAF -> API gateway -> admission/PEP        │
└──────────────┘            │ authn/z │ schema │ idempotency │ quota/deadline │
                            └───────┬──────────────┬───────────────┬──────────┘
                                    │              │               │
                         synchronous│       durable│agent   offline│batch
                                    ▼              ▼               ▼
                            ┌────────────┐  ┌────────────┐  ┌──────────────┐
                            │ inference  │  │ operation  │  │ manifest/job │
                            │ gateway    │  │ DB+outbox  │  │ controller   │
                            └─────┬──────┘  └─────┬──────┘  └──────┬───────┘
                                  │               │                 │ shards
                                  ▼               ▼                 ▼
                           ┌────────────┐  ┌────────────┐    ┌────────────┐
                           │ model pool │  │ queue/log  │    │ batch pool │
                           │ stream/cxl │  │ lease/DLQ  │    │ Jobs       │
                           └─────┬──────┘  └─────┬──────┘    └─────┬──────┘
                                 │               ▼                 │
                                 │       ┌───────────────┐          │
                                 │       │ Temporal/     │          │
                                 │       │ agent workers │          │
                                 │       └──────┬────────┘          │
                                 └──────────────┼───────────────────┘
                                                ▼
                                      ┌────────────────────┐
                                      │ tool/MCP PEP and   │
                                      │ sandbox executors  │
                                      └─────────┬──────────┘
                                                ▼
                      ┌─────────────────────────────────────────────────────┐
                      │ DB/object/vector/effect ledger/workflow history    │
                      │ immutable audit │ backups/PITR │ regional replicas │
                      └─────────────────────────────────────────────────────┘

 KUBERNETES DATA PLANE: Deployments │ Jobs │ node pools │ Services/Gateway
 TELEMETRY: API/queue/model/tool/workflow SLIs │ cost/quality │ audit/canaries
```

The control plane publishes signed desired state and can be temporarily unavailable without immediately stopping an already-running data plane. It should not perform a remote decision in every hot request when a signed local policy/config can suffice. The three workload paths share identity, policy, artifact, data and telemetry contracts, but have different queue, latency and completion semantics.

### End-to-end flows

**Synchronous inference**

1. Edge terminates TLS, applies coarse WAF/size controls and forwards authenticated context. The gateway obtains tenant identity from the credential, not request body.
2. API admission parses minimal bytes, authenticates, authorizes object/action, validates schema/content, resolves idempotency, reserves request/token/spend/concurrency budget and checks deadline feasibility.
3. The inference gateway selects an eligible model/pool and a ready replica. Short bounded queueing is part of TTFT. When completion cannot fit the deadline, return `429` or `503` with retry guidance rather than queue without bound.
4. An absolute deadline and cancellation propagate to model and bounded tools. Disconnect triggers explicit cancellation; it does not prove provider computation or billing stopped.
5. A structured response or RFC 9457 problem is returned. Token/cost/quality and terminal status are recorded without raw sensitive content.

**Durable agent operation**

1. `POST /runs` requires a tenant-scoped idempotency key. One transaction stores canonical request hash, operation ID/status and outbox event before returning `202 Accepted` with status/event URLs.
2. The outbox relay publishes to an at-least-once queue. A workflow/worker claims with lease and fencing attempt, validates envelope/schema/policy, checkpoints semantic boundaries and heartbeats long work.
3. Tools execute through a Zero Trust PEP and sandbox with domain idempotency keys. Unknown external outcomes reconcile before retry. Result/effect and terminal operation state commit before input acknowledgement.
4. Duplicate delivery reads committed state and acknowledges without repeating the effect. Poison work exhausts a bounded attempt policy and enters a protected DLQ. Cancellation reports whether an issued effect still completed.

**Offline batch**

1. An immutable manifest binds item/shard IDs, source hashes, image/model/prompt/schema versions, region, deadline, retry and output-commit protocol.
2. Separate lower-priority queues and Kubernetes Jobs process shards without consuming interactive headroom. Each item commits to a versioned staging key and conditionally marks completion.
3. Finalization verifies counts/hashes/schema/quality and atomically publishes the dataset manifest. Restart processes only absent/retryable items.

Do not convert paths silently. A timed-out synchronous call cannot become an invisible background expense. A multi-hour loop cannot exist only in pod RAM. Offline jobs cannot borrow unlimited interactive quota.

## 2. Core Mechanics & Algorithms

### 2.1 Docker, OCI, and artifact contract

OCI image manifests/config/layers identify exact bytes; OCI runtime defines executing an unpacked bundle. Content addressability does not prove reviewed source, trusted builder, vulnerability status or model quality. A release tuple binds:

```text
image digest + source commit + lockfile digest + build provenance
+ SBOM + scan result + signer/issuer + admission policy
+ model/adapter/tokenizer digest + prompt/tool/schema versions
+ migration compatibility + deployment manifest digest
```

Conservative Dockerfile pattern:

```dockerfile
# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.13-slim@sha256:REVIEWED_DIGEST
FROM ${PYTHON_IMAGE} AS build
WORKDIR /build
COPY requirements.lock .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip wheel --require-hashes --wheel-dir=/wheels -r requirements.lock

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN addgroup --system app && adduser --system --ingroup app app
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
WORKDIR /app
COPY --chown=app:app src/ ./src/
USER app
ENTRYPOINT ["python", "-m", "src.service"]
```

In a real build, replace `REVIEWED_DIGEST` through reviewed automation; never use a placeholder digest in a release. Multi-stage builds and `.dockerignore` reduce attack/transfer surface. Lock dependencies with hashes. Pinning deliberately stops automatic patch movement, so automation must propose, test, scan, sign and roll out new base digests.

Images contain no provider keys, cluster credentials or tenant data. Runtime uses non-root, read-only root, dropped capabilities, default seccomp, bounded CPU/memory/PID/ephemeral storage and workload identity. Containers share the node kernel; generated hostile code belongs in a stronger sandbox runtime/node pool with no ambient credentials and explicit egress.

Build/admission invariant: only an approved registry digest whose provenance identity, attestation predicate, SBOM/vulnerability policy and release tuple match may become a pod. Verification is against signer identity/issuer and expected claims, not merely “has a signature.”

### 2.2 Kubernetes reconciliation, health, and releases

| Primitive | Correct fit | Not automatically provided |
|---|---|---|
| Deployment | stateless API/router/consumer/inference replicas | session/job durability or stable identity |
| StatefulSet | identity/storage/order intrinsic to member | database replication/consistency |
| Job/CronJob | bounded retryable completion/schedule | exactly-once side effects |
| DaemonSet | node-local telemetry/network/device service | general business scaling |
| Managed external service | DB/broker/object/model when ownership favors provider | disappearance of quota/outage/DR |

Scheduling uses declared resource **requests**, not hoped-for usage. Specify CPU, memory, ephemeral storage, accelerators, topology, affinity and taints. Under-requesting creates contention; over-requesting blocks scheduling/consolidation. Keep API/agent/model/sandbox pools separate so CPU scale-out cannot occupy reserved accelerator or hostile-code nodes.

Probe semantics:

- startup: allow image/model/runtime initialization before liveness/readiness;
- readiness: this pod can serve its assigned route now; removes endpoint before drain;
- liveness: local process cannot make progress and restart is useful, not “a dependency is slow.”

A dependency outage should not make every liveness probe restart, causing a cascade. Model readiness requires artifact/runtime load and golden health, but liveness remains local.

Graceful termination state machine:

```text
READY -> DRAINING(not ready; no new HTTP/queue claims)
      -> STREAM_DRAIN/CHECKPOINT/LEASE_RELEASE
      -> TELEMETRY_FLUSH -> EXIT before grace deadline
```

PDB limits approved voluntary evictions; it does not prevent node failure, zone loss or every rollout reduction. Use zone/node topology spread and N+1 capacity. Deployment defaults of 25% `maxUnavailable`, 25% `maxSurge`, and 600-second progress detection are mechanics, not safe universal settings. GPU surge needs real reserved nodes. Mixed versions require backward/forward API, event, database, prompt/tool and output-schema compatibility.

Release flow is `build -> test/eval -> scan/sign/attest -> policy admission -> dark/shadow -> startup/warm -> canary -> SLO/quality/cost gates -> staged expansion/bake -> complete`. Keep known-good image/model/config rollback. Stop ordinary releases when error-budget policy says reliability work takes precedence.

### 2.3 API admission and idempotency

OpenAPI `3.2.0` is the current published language-independent HTTP interface specification on the research date. The reviewed contract defines authentication, object/field/function authorization, input/output schemas and bounds, idempotency, deadline/cancellation/stream resume, quotas/spend, retry errors, privacy/residency, and async status/terminal reasons.

Admission is ordered to reject cheap:

```text
minimal parse/size -> authenticate -> tenant/object/action authorize
-> schema/content + URL/tool/model policy -> idempotency lookup
-> rate/concurrency/spend/provider quota reservation
-> work estimate + deadline feasibility -> execute/enqueue
```

For `POST /runs`, persist this mapping atomically:

```text
(tenant, idempotency_key) -> canonical_request_hash, operation_id,
                             status, result_hash, expiry
```

Same key/same hash returns the existing operation; same key/different hash is `409 Conflict`. Retention exceeds maximum client retry horizon. Side effects also require domain/provider idempotency; API dedupe alone does not protect an activity after queue redelivery.

Deadline uses an absolute monotonic/wall representation and each child gets `min(child_budget, remaining_parent)`. gRPC has no default deadline. Transport retry does not make effects idempotent. Machine-readable RFC 9457 failures distinguish invalid, denied, quota/concurrency saturated, unavailable, deadline and internal states with appropriate `Retry-After`.

Authorization and complexity are `O(1)` expected for indexed identity/quota/idempotency records plus `O(B)` validation in input bytes. Tokenization/work estimation is `O(T)` in tokens. Enforce bytes before expensive tokenization to resist resource exhaustion.

### 2.4 Queue ownership, state, and effectively-once effects

| Delivery claim | Mechanism | Actual consequence |
|---|---|---|
| At-most-once | ack/remove before durable processing | work can be lost |
| At-least-once | ack after durable commit; lease expiry redelivers | duplicates expected |
| Ordered | partition/group key with serialized ownership | order only per key; lower hot-key throughput |
| Effectively once | at-least-once + scoped idempotent transaction/effect | applies only to named state transition |
| Kafka exactly-once | transactionally read/write Kafka with committed reads | external HTTP/payment is outside transaction |

SQS standard can duplicate/reorder even within visibility expectations. RabbitMQ publisher confirm is broker acceptance, while consumer ack transfers processing responsibility; prefetch bounds in-flight work. Kafka `read_committed` and transactional producer can atomically process Kafka records, not arbitrary external systems.

Worker lifecycle:

```text
READY -> LEASED(attempt,fence,expiry) -> VALIDATED -> RUNNING
      -> CHECKPOINTED -> EFFECT_INTENT -> COMMITTED -> ACKED
                     └-> UNKNOWN -> RECONCILING -> COMMITTED/FAILED
any retryable -> BACKOFF -> READY (bounded attempts/age/deadline)
permanent/poison -> QUARANTINED/DLQ
```

Lease is renewable ownership, not proof against duplicate. Fence stale owners at commit. Commit output/effect and operation terminal state before ack, atomically where possible. For external effects, persist intent, pass an idempotency key, store receipt and reconcile timeout before retry.

Transactional outbox writes business state and event row in one DB transaction. Relay/broker may still duplicate, so stable event IDs and idempotent consumers remain necessary. Preserve per-entity sequence if order matters. Claim/index operations are expected `O(log n)` in durable stores; partitioning provides parallelism but one ordered key remains serial.

Every queue has maximum attempts/age, exponential full jitter, adaptive lease heartbeat, poison classification, a protected dead-letter queue (DLQ) and governed redrive. Monitor oldest ready age, lease expiry, attempts and estimated tokens/tool-time by tenant/class, not depth alone.

### 2.5 Scaling as delayed feedback control

HPA approximates:

```text
desired = ceil(current_replicas × current_metric / desired_metric)
```

The documented default sync interval is 15 seconds and scale-down stabilization is 300 seconds. Multiple metrics take the largest recommendation. A separate node autoscaler must then obtain and initialize machines:

```text
metric -> HPA decision -> pending pod -> node/device provision
-> image/model pull -> runtime/model warmup -> readiness -> useful capacity
```

Scale on user pain/work:

- API: admitted concurrency, queue delay, active streams, deadline slack;
- durable consumers: oldest age and estimated remaining token/tool work;
- inference: queued tokens, decode/KV pressure and SLO goodput;
- tools: downstream concurrency/quota/latency, not worker CPU;
- batch: work remaining versus deadline and budget.

KEDA handles external event activation including zero-to-one; current defaults documented in the research are 30-second polling and 300-second cooldown to zero. Scale-to-zero is invalid when node/image/model startup exceeds acceptable queue age. Keep warm minimum, predictive/scheduled headroom or honest asynchronous admission.

Bound maximum replicas by DB connections, broker partitions, provider quota, accelerators and spend. More workers can amplify an outage. Stabilization, rate limits and multiple signals reduce oscillation. Backpressure and early admission shedding protect the service during the control-loop delay. Weighted tenant/class queues provide business fairness; Kubernetes pod priority only influences scheduling/preemption.

Capacity algorithm:

```text
required_replicas = ceil(peak_work_rate / measured_goodput_per_replica_at_SLO)
                    + zone/failure/rollout headroom

concurrency ~= arrival_rate × mean_time_in_system              (Little's Law)
drain_time ~= backlog_work/(service_rate-arrival_rate), only if service>arrival
```

Goodput must satisfy latency, quality and policy. Replay joint prompt/output, tool latency, prefix, model, tenant, retry and burst distributions. No portable requests-per-GPU/pod number exists.

### 2.6 Reliability, recovery, and invariants

SLO is the normal service target; error budget is `1-SLO`; SLA is an external consequence. RPO bounds recoverable data loss/age; RTO bounds time to restore the required level. Define them per state, not “the platform.”

| State | Durable authority | Recovery rule |
|---|---|---|
| Container filesystem/cache/KV | none/reconstructable | recreate/recompute |
| Session/idempotency | durable DB or signed client state | replicate/expire |
| Workflow/effect | workflow history + operation/effect ledger | checkpoint/replay/reconcile |
| Queue/log | broker replicas/retention | ack/offset/lease and regional plan |
| User/source/vector | DB/object source plus PITR; vector rebuildable | restore/rebuild/repoint |
| Artifact/config/policy | immutable replicated registry/authority | exact digest deploy |
| Audit | append-only/WORM where required | restore/query with custody |

Reliability invariants:

- no `202` before durable operation plus publish intent;
- accepted work has one durable owner record and a terminal or detectably nonterminal state;
- effect retries use idempotency/reconciliation; “exactly once” scope is stated;
- retry ownership is singular per failure class; `3` attempts at four layers must not become `3^4=81` downstream calls;
- overload preserves auth, policy, isolation, idempotency and audit before feature availability;
- data plane serves through temporary control-plane outage using already-verified artifacts/config;
- backups are not trusted until isolated full restore and application consistency pass;
- regional failover establishes one fenced write/queue owner before routing writes.

## 3. Token Economics & NFR Analysis

### 3.1 Explicit cost per 1,000 accepted executions

Scoped planning assumptions: 1,000 executions use 5M uncached input, 8M prompt-cache reads, 0.05M cache writes and 1.5M output. Planning rates per million input/output are `sol $5/$30`, `terra $2/$12`, `luna $0.20/$1.20`; reads cost `0.1x` input and writes `1.25x`. These are dated internal assumptions, not public universal pricing.

| Tier | Cached model cost per 1K executions |
|---|---:|
| `sol` | `5×$5 + 8×$.50 + .05×$6.25 + 1.5×$30` = **$74.31** |
| `terra` | `5×$2 + 8×$.20 + .05×$2.50 + 1.5×$12` = **$29.73** |
| `luna` | `5×$.20 + 8×$.02 + .05×$.25 + 1.5×$1.20` = **$2.97** |

A gated route mix of 60% `luna`, 30% `terra`, 10% `sol` costs `.6×$2.9725 + .3×$29.725 + .1×$74.3125 = $18.13`. Platform allocation per 1K is API/CPU `$0.40`, Kubernetes/node idle and rollout headroom `$2.50`, DB/queue/object `$1.20`, network `$0.60`, telemetry `$0.90`, sandbox/tool compute `$4.00`, and retry/failure reserve `$0.54`: **$10.14**. Total is **$28.27/1K accepted executions**. If 940 are policy-compliant successes, cost per 1,000 successes is `$28.27×1000/940 = $30.07`.

```text
effective_cost/success = provider tokens + accelerator/CPU + idle headroom
                       + queue/storage/network + tools/sandbox + telemetry
                       + retries/failed work + DR/release allocation
                       ---------------------------------------------------
                              policy-compliant successful outcomes
```

Track cache reads/writes, reasoning/output, actual tool/provider charges, accelerator hours, failed and post-cancel work. Do not count a fallback as successful unless it meets the minimum contract. Offline provider/batch discounts, cloud prices and quotas are region/date/contract dependent and remain separate scenarios.

### 3.2 Latency and freshness SLOs

Internal starting targets, not industry benchmarks:

| Path/stage | p50 | p95 | p99 | Tail mitigation |
|---|---:|---:|---:|---|
| API auth/admission | 4 ms | 15 ms | 40 ms | local policy/cache, indexed quota/idempotency |
| Sync queue | 15 ms | 100 ms | 300 ms | bounded queue, warm headroom, early shed |
| Sync TTFT | 300 ms | 1.5 s | 3 s | ready model pool, route/cache, admission |
| Sync E2E | 3 s | 10 s | 20 s | output/deadline cap, cancellation, bulkheads |
| Durable queue age | 2 s | 20 s | 60 s | age/work scaling, tenant fairness |
| Durable run completion | 45 s | 8 min | 30 min | checkpoint, async status, priority/budget |
| API rollout readiness | 20 s | 60 s | 3 min | pre-pull image, startup probe, warmup |
| Error alert delivery | 30 s | 2 min | 5 min | multi-window SLO alerts, alternate path |

Measure accepted/rejected, cached/miss, success/failure/cancel, model/tool/path and release cohorts separately. Queue delay consumes the caller deadline. A provider-only latency chart is not the end-to-end promise.

### 3.3 Throughput and autoscaling calculation

Synchronous peak is 100 requests/s with measured complete-SLO goodput of 18 requests/s/pod. Arithmetic needs six pods; add one-zone loss plus rollout/burst headroom for **eight ready pods**. At six-second mean E2E, Little's Law estimates `100×6=600` active/in-system requests; enforce lower concurrency if the measured pods cannot sustain this mix.

Durable arrival is 5 jobs/s with mean 40-second time in system, implying about 200 jobs in system. Measured worker goodput is 0.4 job/s, so 13 workers cover arrivals; four additional workers give failure/burst headroom: **17**. If backlog is 10,000 jobs, service is 8 jobs/s and arrivals remain 5/s, approximate drain time is `10,000/(8-5)=3,333s`, about 56 minutes. If service falls to 5/s, it never drains.

Autoscaling is not overload control. Admission bounds concurrency, queued tokens/jobs, tenant budget and deadline before scaling reacts. HPA/KEDA queue-age/work signals scale workers; node provisioning, image pull and warmup are included in required lead time. `maxReplicas` respects provider/DB/broker/spend ceilings. On saturation, defer durable work, shed low-priority sync requests with retry advice, and never bypass authorization.

### 3.4 NFRs and recovery objectives

| NFR | Target/evidence | Trade-off |
|---|---|---|
| Sync availability | 99.95% valid eligible completions/month | About 21.6 min/month budget; headroom costs |
| Durable acceptance | 99.99%; zero acknowledged-operation loss | Transaction/outbox adds latency |
| Quality/safety | route/task/policy gates; fallback counted only if compliant | Lower apparent availability |
| Queue | p99 oldest ready <=60 s; bounded attempts/age/DLQ | More rejection and operational redrive |
| RPO | 0 operation/effect/audit; <=5 min user data; cache 0 required | Synchronous durability and replication cost |
| RTO | <=15 min service failover; <=30 min operation recovery; <=4 h full batch recovery | Warm capacity and rehearsals |
| Zone | meet minimum SLO after one-zone loss | N+1 idle capacity |
| Region | warm standby, fenced single writer, tested failover/failback | Higher steady cost/complexity |
| Compliance | residency, retention/deletion, immutable audit, restore evidence | Region-specific stores/queues |
| Supply chain | 100% release digest/provenance/signature/SBOM policy | Patch/release friction |

## 4. Distributed Resilience & Security

### 4.1 Durable execution and regional ownership

```text
┌──────────────┐ transaction ┌──────────────┐ outbox/CDC ┌──────────────┐
│ API admission├────────────►│ operation DB ├───────────►│ Kafka/broker │
│ idempotency  │             │ intent/state │            │ lease + DLQ  │
└──────────────┘             └──────┬───────┘            └──────┬───────┘
                                    │ checkpoint/effect          ▼
                             ┌──────▼───────┐             ┌──────────────┐
                             │ Temporal     │◄────────────┤ workers      │
                             │ history/timer│             │ fenced claim │
                             └──────┬───────┘             └──────┬───────┘
                                    │                             │ idempotent tool
                                    ▼                             ▼
                             ┌──────────────┐             ┌──────────────┐
                             │ effect ledger│◄──receipt───┤ MCP/tool PEP │
                             └──────────────┘             └──────────────┘

 region A owner epoch 41 ──replication/backups──► region B warm epoch 40
 failover: freeze -> verify RPO -> fence epoch 42 -> promote -> canary -> expand
```

Workflow replay reconstructs deterministic control flow; Activities may run again and must be idempotent/reconcilable. Checkpoints follow durable reads, surround effects and precede ownership transfer. Store schema/workflow versions and serializable facts, never live sockets/SDK clients. `UNKNOWN` external timeout becomes reconciliation, not blind retry.

Multi-zone replicas spread by failure domain with N+1 capacity. Multi-region warm standby replicates database, operation/event history, registry artifacts, policy/config, secrets/identity trust, observability and actual provider/accelerator quota. A YAML copy is not capacity. One fencing epoch owns writes and queue consumption. Failback is a controlled migration with the same evidence as failover.

Backups are encrypted, separately credentialed, immutable/versioned and restore-tested. Replication can copy corruption. etcd snapshots recover Kubernetes API state, not external DB, broker, object store, provider-side jobs or lost RAM.

### 4.2 Retry, overload, and failure classification

| Class | Examples | Handling |
|---|---|---|
| Transient | 429/503, timeout before commit, temporary node/provider loss | one retry owner, full jitter, breaker, remaining deadline |
| Permanent | invalid schema, denied policy, incompatible artifact/event | terminal problem/DLQ; no blind retry |
| Poison | deterministic crash or unsupported version each attempt | quarantine with original hash/reason |
| Ambiguous effect | timeout after payment/email/deploy | freeze repeat, provider receipt/state reconciliation |
| Overload | queue/deadline/capacity budget exhausted | early shed/defer, Retry-After, scale signal |
| Regional partition | ownership/replication uncertainty | freeze unsafe writes, fence, prove recovery point |

Breakers are dependency/route specific with closed/open/half-open probes. Bulkheads isolate tenant, interactive/batch, model/tool and sandbox pools. Graceful degradation preserves auth, authorization, tenant isolation, idempotency and audit; it may defer batch, remove optional tools, use a pre-evaluated fallback or cap output only when the API contract permits.

### 4.3 Layered security and Zero Trust MCP

1. Source/build: protected review, lock/hash, isolated builder, secret/license/vulnerability scan, SBOM and SLSA provenance.
2. Registry/admission: allowed registry, immutable digest, signer/issuer/attestation and expiring exception policy.
3. Kubernetes: short-lived identity, least-privilege RBAC, admission/audit, encrypted etcd/secrets, Restricted pod policy and patching.
4. Runtime: non-root, no privilege escalation, read-only FS, capabilities dropped, seccomp, resource bounds; stronger sandbox for untrusted code.
5. Network: default-deny ingress/egress enforced by the installed CNI, workload mTLS, metadata block, private endpoint/egress proxy.
6. API/data: server-bound tenant, object/tool authorization, schema/idempotency/quota/deadline, SSRF redirect/DNS/IP defense, encryption/residency/deletion.

An MCP server and its descriptions/results are untrusted. The host authenticates user and workload. A tool PEP evaluates RBAC baseline plus tenant/task/resource/action ABAC, exact arguments, destination and current state. It brokers a short-lived audience-bound capability; the model/sandbox never gets provider or cluster credentials. Every effect uses idempotency and immutable audit.

PII pipeline is `classify -> minimize -> redact/tokenize -> authorize destination -> execute -> controlled rehydrate -> retain/delete -> audit`. Queue envelopes carry opaque references rather than prompts. DLQs are sensitive stores: separate produce/consume/redrive/purge/view permissions, encryption, retention and audited access.

### 4.4 Release, redrive, and incident governance

Canary a representative traffic/tenant/risk cohort against control, then gate HTTP/TTFT/E2E plus task quality, schema validity, tool denial/duplicates, loops, checkpoint errors, cost/success and policy. Do not push the same unproven artifact to all regions. Mixed-version contract tests cover old/new API, event, DB, prompt/tool and output formats.

Redrive is a privileged release: identify/fix cause, select messages, pin corrected code/schema/policy, dry-run, preserve original identity/idempotency, throttle to downstream capacity, and audit operator/reason/result. Queue purge requires explicit data-loss authority and evidence preservation.

Incident response freezes unsafe releases/writes, establishes operation/queue ownership, checks telemetry completeness, reconciles ambiguous effects, preserves WORM evidence and follows measured restore/failover. Governance maintains service/data owners, threat/dependency/API inventories, SLO/capacity policy, provenance, retention/DR mapping, runbooks and expiring exceptions.

### 4.5 Failure modes and proof tests

| Failure | Prevention/containment | Required proof |
|---|---|---|
| Unsigned/mutable image | digest/provenance admission | altered image rejected |
| Bad readiness/liveness | semantic probes, local liveness | cold model and dependency outage tests |
| Rolling capacity collapse | surge/headroom/compatibility/canary | peak rollout plus zone loss |
| HPA/node lag/oscillation | warm min, stabilization, work metrics | zero-to-peak and burst-decay replay |
| Lost accepted job | transaction + outbox before 202 | kill each accept/publish boundary |
| Duplicate effect | ledger/provider key/reconciliation | kill before/after effect/ack |
| Poison/lease failure | bounded attempts, heartbeat, fence, DLQ | slow job and deterministic bad input |
| Retry storm | singular ownership/budget/jitter/deadline | inject timeout/429 and count calls |
| Tenant starvation | quotas/fair queues/bulkheads | adversarial heavy tenant |
| Split brain | epoch/single owner/freeze | isolate region control links |
| Corrupt backup/state | immutable PITR and restore validation | isolated full restore/reconcile |
| Bad global release | staged region/cohort and rollback | intentional canary failure |
| Control-plane outage | running data-plane independence | block registry/Kubernetes API while serving |

## 5. Production Enterprise Code

This Python 3.11 standard-library program implements the durable API/queue path. SQLite stands in for a transactional managed database; `MemoryQueue` stands in for an at-least-once broker. The code atomically creates an operation plus outbox, enforces tenant-scoped idempotency, publishes with stable event IDs, claims through expiring lease/fencing attempts, deduplicates an external effect, quarantines poison work, records a hash-chained audit, and supports worker drain. Its dependency chain uses exponential full jitter, closed/open/half-open breakers, primary and secondary models, then a deterministic manual-review fallback.

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
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class TransientFailure(RuntimeError):
    """A retryable dependency or capacity failure."""


class PermanentFailure(RuntimeError):
    """A non-retryable schema, policy, or idempotency failure."""


class CircuitOpen(TransientFailure):
    """A dependency is isolated until a recovery probe."""


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {"timestamp": time.time(), "severity": record.levelname,
                 "message": record.getMessage()}
        for key in ("operation_id", "tenant_ref", "worker", "attempt",
                    "dependency", "status", "event_id"):
            if hasattr(record, key):
                value[key] = getattr(record, key)
        return json.dumps(value, separators=(",", ":"), sort_keys=True)


logger = logging.getLogger("durable-production")
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
class RunRequest:
    tenant: str
    idempotency_key: str
    payload: dict[str, object]
    max_steps: int
    deadline_epoch_s: float

    def canonical_hash(self) -> str:
        body = {"payload": self.payload, "maxSteps": self.max_steps}
        return hashlib.sha256(json.dumps(
            body, separators=(",", ":"), sort_keys=True
        ).encode()).hexdigest()


@dataclass(frozen=True)
class Message:
    event_id: str
    operation_id: str
    tenant: str


class Admission:
    def __init__(self, max_input_bytes: int = 8_192,
                 max_steps: int = 20, min_deadline_s: float = 1.0):
        self._max_input_bytes = max_input_bytes
        self._max_steps = max_steps
        self._min_deadline_s = min_deadline_s

    def validate(self, request: RunRequest) -> None:
        encoded = json.dumps(request.payload, separators=(",", ":")).encode()
        if not request.tenant or not request.idempotency_key:
            raise PermanentFailure("authenticated tenant and idempotency required")
        if len(encoded) > self._max_input_bytes:
            raise PermanentFailure("payload too large")
        if not 0 < request.max_steps <= self._max_steps:
            raise PermanentFailure("step budget outside policy")
        if request.deadline_epoch_s - time.time() < self._min_deadline_s:
            raise PermanentFailure("deadline cannot fit minimum work")


class MemoryQueue:
    """At-least-once transport: duplicate publish is intentionally possible."""

    def __init__(self):
        self._messages: deque[Message] = deque()
        self._lock = threading.Lock()

    def publish(self, message: Message) -> None:
        with self._lock:
            self._messages.append(message)

    def receive(self) -> Message | None:
        with self._lock:
            return self._messages.popleft() if self._messages else None


class OperationStore:
    def __init__(self, path: Path):
        self._db = sqlite3.connect(path, check_same_thread=False,
                                   isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._setup()

    def _setup(self) -> None:
        with self._lock:
            self._db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS operations(
                  operation_id TEXT PRIMARY KEY,
                  tenant TEXT NOT NULL,
                  idem_key TEXT NOT NULL,
                  request_hash TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  max_steps INTEGER NOT NULL,
                  deadline_epoch REAL NOT NULL,
                  status TEXT NOT NULL,
                  attempt INTEGER NOT NULL DEFAULT 0,
                  fence INTEGER NOT NULL DEFAULT 0,
                  lease_owner TEXT,
                  lease_until REAL,
                  result_json TEXT,
                  created_at REAL NOT NULL,
                  UNIQUE(tenant, idem_key)
                );
                CREATE TABLE IF NOT EXISTS outbox(
                  event_id TEXT PRIMARY KEY,
                  operation_id TEXT NOT NULL,
                  tenant TEXT NOT NULL,
                  published INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS effects(
                  effect_key TEXT PRIMARY KEY,
                  request_hash TEXT NOT NULL,
                  result_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dlq(
                  operation_id TEXT PRIMARY KEY,
                  reason TEXT NOT NULL,
                  payload_hash TEXT NOT NULL,
                  created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit(
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                  operation_id TEXT NOT NULL,
                  event_json TEXT NOT NULL,
                  previous_hash TEXT NOT NULL,
                  event_hash TEXT NOT NULL
                );
            """)

    def create(self, request: RunRequest) -> tuple[str, bool]:
        request_hash = request.canonical_hash()
        operation_id = uuid.uuid4().hex
        event_id = hashlib.sha256(
            f"operation.accepted:{operation_id}".encode()).hexdigest()[:24]
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                existing = self._db.execute(
                    "SELECT operation_id,request_hash FROM operations "
                    "WHERE tenant=? AND idem_key=?",
                    (request.tenant, request.idempotency_key),
                ).fetchone()
                if existing:
                    if not hmac.compare_digest(existing["request_hash"],
                                               request_hash):
                        raise PermanentFailure(
                            "idempotency key reused with different request")
                    self._db.execute("COMMIT")
                    return str(existing["operation_id"]), False
                self._db.execute(
                    "INSERT INTO operations(operation_id,tenant,idem_key,"
                    "request_hash,payload_json,max_steps,deadline_epoch,"
                    "status,created_at) VALUES(?,?,?,?,?,?,?,'PENDING',?)",
                    (operation_id, request.tenant, request.idempotency_key,
                     request_hash, json.dumps(request.payload,
                                              separators=(",", ":"),
                                              sort_keys=True), request.max_steps,
                     request.deadline_epoch_s, time.time()),
                )
                self._db.execute(
                    "INSERT INTO outbox(event_id,operation_id,tenant) "
                    "VALUES(?,?,?)", (event_id, operation_id, request.tenant)
                )
                self._append_audit_locked(operation_id, {
                    "event": "operation.accepted", "requestHash": request_hash
                })
                self._db.execute("COMMIT")
                return operation_id, True
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def publish_outbox(self, queue: MemoryQueue) -> int:
        with self._lock:
            rows = self._db.execute(
                "SELECT event_id,operation_id,tenant FROM outbox "
                "WHERE published=0 ORDER BY rowid"
            ).fetchall()
            for row in rows:
                queue.publish(Message(row["event_id"], row["operation_id"],
                                      row["tenant"]))
                self._db.execute("UPDATE outbox SET published=1 WHERE event_id=?",
                                 (row["event_id"],))
            return len(rows)

    def claim(self, message: Message, worker: str,
              lease_s: float = 5.0) -> tuple[
                  str, int, dict[str, object] | None, float | None
              ]:
        now = time.time()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT status,attempt,fence,lease_owner,lease_until,"
                    "payload_json,deadline_epoch FROM operations "
                    "WHERE operation_id=? "
                    "AND tenant=?", (message.operation_id, message.tenant)
                ).fetchone()
                if not row:
                    raise PermanentFailure("operation envelope mismatch")
                if row["status"] in {"COMMITTED", "DLQ"}:
                    self._db.execute("COMMIT")
                    return str(row["status"]), int(row["fence"]), None, None
                if (row["status"] == "RUNNING" and row["lease_until"]
                        and float(row["lease_until"]) > now
                        and row["lease_owner"] != worker):
                    self._db.execute("COMMIT")
                    return "LEASED_ELSEWHERE", int(row["fence"]), None, None
                attempt, fence = int(row["attempt"]) + 1, int(row["fence"]) + 1
                self._db.execute(
                    "UPDATE operations SET status='RUNNING',attempt=?,fence=?,"
                    "lease_owner=?,lease_until=? WHERE operation_id=?",
                    (attempt, fence, worker, now + lease_s,
                     message.operation_id),
                )
                self._append_audit_locked(message.operation_id, {
                    "event": "operation.claimed", "attempt": attempt,
                    "fence": fence, "worker": worker,
                })
                self._db.execute("COMMIT")
                return ("RUNNING", fence, json.loads(row["payload_json"]),
                        float(row["deadline_epoch"]))
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def effect_once(self, effect_key: str, request_hash: str,
                    value: dict[str, object]) -> dict[str, object]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT request_hash,result_json FROM effects "
                    "WHERE effect_key=?", (effect_key,)
                ).fetchone()
                if row:
                    if not hmac.compare_digest(row["request_hash"], request_hash):
                        raise PermanentFailure(
                            "effect key reused with changed arguments")
                    self._db.execute("COMMIT")
                    return json.loads(row["result_json"])
                encoded = json.dumps(value, separators=(",", ":"),
                                     sort_keys=True)
                self._db.execute(
                    "INSERT INTO effects(effect_key,request_hash,result_json) "
                    "VALUES(?,?,?)", (effect_key, request_hash, encoded)
                )
                self._db.execute("COMMIT")
                return dict(value)
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def complete(self, operation_id: str, fence: int,
                 result: dict[str, object]) -> None:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                changed = self._db.execute(
                    "UPDATE operations SET status='COMMITTED',result_json=?,"
                    "lease_owner=NULL,lease_until=NULL WHERE operation_id=? "
                    "AND status='RUNNING' AND fence=?",
                    (json.dumps(result, separators=(",", ":"), sort_keys=True),
                     operation_id, fence),
                ).rowcount
                if changed != 1:
                    raise PermanentFailure("stale worker fence at commit")
                self._append_audit_locked(operation_id, {
                    "event": "operation.committed", "fence": fence,
                    "resultHash": hashlib.sha256(json.dumps(
                        result, separators=(",", ":"), sort_keys=True
                    ).encode()).hexdigest(),
                })
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def fail(self, operation_id: str, fence: int, reason: str,
             permanent: bool, max_attempts: int = 3) -> str:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    "SELECT attempt,request_hash FROM operations "
                    "WHERE operation_id=? AND fence=?",
                    (operation_id, fence),
                ).fetchone()
                if not row:
                    raise PermanentFailure("stale worker fence at failure")
                terminal = permanent or int(row["attempt"]) >= max_attempts
                status = "DLQ" if terminal else "PENDING"
                self._db.execute(
                    "UPDATE operations SET status=?,lease_owner=NULL,"
                    "lease_until=NULL WHERE operation_id=? AND fence=?",
                    (status, operation_id, fence),
                )
                if terminal:
                    self._db.execute(
                        "INSERT OR REPLACE INTO dlq(operation_id,reason,"
                        "payload_hash,created_at) VALUES(?,?,?,?)",
                        (operation_id, reason[:200], row["request_hash"],
                         time.time()),
                    )
                self._append_audit_locked(operation_id, {
                    "event": "operation.failed", "status": status,
                    "reason": reason[:100], "fence": fence,
                })
                self._db.execute("COMMIT")
                return status
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def _append_audit_locked(self, operation_id: str,
                             event: dict[str, object]) -> None:
        previous_row = self._db.execute(
            "SELECT event_hash FROM audit ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous = previous_row["event_hash"] if previous_row else "0" * 64
        encoded = json.dumps(event, separators=(",", ":"), sort_keys=True)
        digest = hashlib.sha256((previous + encoded).encode()).hexdigest()
        self._db.execute(
            "INSERT INTO audit(operation_id,event_json,previous_hash,event_hash)"
            " VALUES(?,?,?,?)", (operation_id, encoded, previous, digest)
        )

    def status(self, operation_id: str) -> str:
        with self._lock:
            row = self._db.execute(
                "SELECT status FROM operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            return str(row["status"]) if row else "MISSING"

    def result(self, operation_id: str) -> dict[str, object]:
        with self._lock:
            row = self._db.execute(
                "SELECT result_json FROM operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if not row or not row["result_json"]:
                return {}
            return json.loads(row["result_json"])

    def counts(self) -> dict[str, int]:
        with self._lock:
            effects = self._db.execute("SELECT COUNT(*) n FROM effects").fetchone()
            dlq = self._db.execute("SELECT COUNT(*) n FROM dlq").fetchone()
            audit = self._db.execute("SELECT COUNT(*) n FROM audit").fetchone()
            return {"effects": int(effects["n"]), "dlq": int(dlq["n"]),
                    "audit": int(audit["n"])}


class ModelBackend(Protocol):
    name: str

    def run(self, payload: dict[str, object], timeout_s: float) -> str:
        """Return a model result or raise a classified failure."""


class DemoModel:
    def __init__(self, name: str, failures_before_success: int):
        self.name = name
        self._failures = failures_before_success

    def run(self, payload: dict[str, object], timeout_s: float) -> str:
        if timeout_s <= 0:
            raise TransientFailure("model deadline exhausted")
        if self._failures > 0:
            self._failures -= 1
            raise TransientFailure(f"{self.name} unavailable")
        return f"processed-by:{self.name}"


class ModelChain:
    def __init__(self, primary: ModelBackend, secondary: ModelBackend):
        self._models = (primary, secondary)
        self._breakers = {model.name: Breaker() for model in self._models}

    def execute(self, payload: dict[str, object], deadline: float,
                operation_id: str, tenant_ref: str) -> str:
        for model in self._models:
            breaker = self._breakers[model.name]
            for attempt in range(1, 3):
                if time.monotonic() >= deadline:
                    return "manual-review:deadline"
                try:
                    breaker.before()
                    result = model.run(payload, deadline - time.monotonic())
                    breaker.success()
                    return result
                except CircuitOpen:
                    break
                except PermanentFailure:
                    break
                except (TransientFailure, TimeoutError) as exc:
                    breaker.failure()
                    logger.warning("model retryable failure", extra={
                        "operation_id": operation_id,
                        "tenant_ref": tenant_ref, "worker": "model-chain",
                        "attempt": attempt, "dependency": model.name,
                        "status": type(exc).__name__, "event_id": "none",
                    })
                    if attempt < 2:
                        cap = min(.02 * (2 ** (attempt - 1)),
                                  max(0.0, deadline - time.monotonic()))
                        time.sleep(random.uniform(0.0, cap))
        return "manual-review:models-unavailable"


class Worker:
    def __init__(self, name: str, store: OperationStore, queue: MemoryQueue,
                 models: ModelChain, tenant_secret: bytes):
        self._name, self._store, self._queue = name, store, queue
        self._models, self._tenant_secret = models, tenant_secret
        self._draining = False

    def begin_drain(self) -> None:
        self._draining = True

    def process_one(self) -> str:
        if self._draining:
            return "DRAINING"
        message = self._queue.receive()
        if not message:
            return "EMPTY"
        tenant_ref = hmac.new(self._tenant_secret, message.tenant.encode(),
                              hashlib.sha256).hexdigest()[:16]
        state, fence, payload, deadline_epoch = self._store.claim(
            message, self._name
        )
        if state in {"COMMITTED", "DLQ", "LEASED_ELSEWHERE"}:
            return state
        assert payload is not None and deadline_epoch is not None
        try:
            if payload.get("poison") is True:
                raise PermanentFailure("unsupported poison payload")
            model_result = self._models.execute(
                payload,
                time.monotonic() + max(0.0, deadline_epoch - time.time()),
                message.operation_id, tenant_ref,
            )
            result: dict[str, object] = {"model": model_result}
            if payload.get("effect"):
                arguments = {"effect": payload["effect"],
                             "target": payload.get("target")}
                request_hash = hashlib.sha256(json.dumps(
                    arguments, separators=(",", ":"), sort_keys=True
                ).encode()).hexdigest()
                receipt = self._store.effect_once(
                    f"{message.operation_id}:effect-1", request_hash,
                    {"receipt": "effect-" + message.operation_id[:10],
                     "status": "committed"},
                )
                result["effect"] = receipt
            self._store.complete(message.operation_id, fence, result)
            logger.info("operation committed", extra={
                "operation_id": message.operation_id,
                "tenant_ref": tenant_ref, "worker": self._name,
                "attempt": fence, "dependency": "operation-store",
                "status": "COMMITTED", "event_id": message.event_id,
            })
            return "COMMITTED"
        except PermanentFailure as exc:
            return self._store.fail(message.operation_id, fence, str(exc), True)
        except (TransientFailure, TimeoutError) as exc:
            status = self._store.fail(message.operation_id, fence,
                                      str(exc), False)
            if status == "PENDING":
                self._queue.publish(message)
            return status


class RunAPI:
    def __init__(self, admission: Admission, store: OperationStore):
        self._admission, self._store = admission, store

    def accept(self, request: RunRequest) -> tuple[str, bool]:
        self._admission.validate(request)
        return self._store.create(request)


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = OperationStore(Path(directory) / "operations.db")
        queue, api = MemoryQueue(), RunAPI(Admission(), store)
        request = RunRequest(
            tenant="tenant-a", idempotency_key="invoice-8421",
            payload={"task": "review", "effect": "notify",
                     "target": "case-44"},
            max_steps=5, deadline_epoch_s=time.time() + 30,
        )
        operation_id, created = api.accept(request)
        duplicate_id, duplicate_created = api.accept(request)
        conflict_rejected = False
        try:
            api.accept(RunRequest(
                tenant="tenant-a", idempotency_key="invoice-8421",
                payload={"task": "changed"}, max_steps=5,
                deadline_epoch_s=time.time() + 30,
            ))
        except PermanentFailure:
            conflict_rejected = True

        store.publish_outbox(queue)
        worker = Worker(
            "worker-7", store, queue,
            ModelChain(DemoModel("primary", 3), DemoModel("secondary", 0)),
            b"tenant-reference-secret",
        )
        first_status = worker.process_one()
        queue.publish(Message("duplicate-delivery", operation_id, "tenant-a"))
        duplicate_status = worker.process_one()

        poison = RunRequest(
            tenant="tenant-a", idempotency_key="poison-1",
            payload={"poison": True}, max_steps=1,
            deadline_epoch_s=time.time() + 30,
        )
        poison_id, _ = api.accept(poison)
        store.publish_outbox(queue)
        poison_status = worker.process_one()

        fallback_request = RunRequest(
            tenant="tenant-b", idempotency_key="fallback-1",
            payload={"task": "summarize"}, max_steps=2,
            deadline_epoch_s=time.time() + 30,
        )
        fallback_id, _ = api.accept(fallback_request)
        store.publish_outbox(queue)
        outage_worker = Worker(
            "worker-outage", store, queue,
            ModelChain(DemoModel("primary-down", 3),
                       DemoModel("secondary-down", 3)),
            b"tenant-reference-secret",
        )
        fallback_status = outage_worker.process_one()
        worker.begin_drain()
        print(json.dumps({
            "created": created,
            "duplicateSameOperation": operation_id == duplicate_id
                                      and not duplicate_created,
            "conflictRejected": conflict_rejected,
            "firstStatus": first_status,
            "duplicateDeliveryStatus": duplicate_status,
            "finalStatus": store.status(operation_id),
            "poisonStatus": poison_status,
            "poisonFinalStatus": store.status(poison_id),
            "fallbackStatus": fallback_status,
            "fallbackModel": store.result(fallback_id).get("model"),
            "counts": store.counts(),
            "drainStatus": worker.process_one(),
        }, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
```

Expected terminal properties: duplicate API submission returns one operation; changed input with the same key is rejected; duplicate broker delivery observes `COMMITTED` and does not repeat the single effect; the primary model's breaker leads to the secondary; loss of both models records the explicit `manual-review` fallback; poison work enters the DLQ; and drain stops new claims. The SQLite file is deleted only by the demonstration's temporary directory; a deployment uses replicated durable storage and an independently immutable audit sink.

## 6. Architectural System Design Scenarios

### Scenario 1 - Multi-tenant interactive inference API

**Problem statement.** Design a streaming inference API for 100 requests/s across 3,000 tenants and three zones. Prompts and outputs vary widely; the API uses self-hosted and approved provider routes. Requirements are p95 TTFT under 1.5 seconds, p99 E2E under 20 seconds, 99.95% valid-completion availability, strict tenant/spend isolation, cancellation, one-zone tolerance, and progressive model/image releases without losing more than the monthly error budget.

**Proposed architecture.** A WAF/API gateway validates OpenAPI schemas, OAuth identity, tenant/object policy, token/output/tool bounds, absolute deadline, concurrency/spend reservation and RFC 9457 failures. Stateless API Deployments span zones behind a Gateway; model pools use dedicated accelerator nodes and readiness only after model/kernel golden health. An inference-aware endpoint picker routes by compatible model, queue, queued tokens and KV pressure. HPA uses admitted concurrency, queue delay and goodput; node autoscaling obtains devices. Eight ready API/model serving units cover the scoped six-unit arithmetic need plus zone/rollout headroom. Cancellation propagates to model/provider and post-disconnect tokens are reconciled.

```text
┌──────────────┐ TLS/OAuth ┌──────────────┐ admitted ┌────────────────┐
│ clients      ├──────────►│ WAF/API      ├─────────►│ model gateway  │
│ 100 req/s    │           │ quota/deadline│         │ pool picker    │
└──────────────┘           └──────┬───────┘         └───┬────────┬───┘
                                  │ problems/cancel      ▼        ▼
                                  │                ┌──────────┐┌──────────┐
                                  │                │ zone A/B ││ zone C   │
                                  │                │ model    ││ model    │
                                  │                └────┬─────┘└────┬─────┘
                                  │                     │ provider fallback
                                  ▼                     ▼
                            ┌────────────┐        ┌──────────────┐
                            │ SLO/cost/  │        │ approved     │
                            │ canary gate│        │ provider     │
                            └────────────┘        └──────────────┘
```

Rollout uses signed digest admission, startup/readiness warmup, 1% shadow/canary, then staged cohorts gated on TTFT/E2E, valid output, quality, policy, tokens/cost, OOM and fallback rate. `maxSurge` is chosen from real spare accelerator capacity; PDB and topology spread are tested with zone loss. Scale never exceeds provider/DB/device/spend ceilings.

| Approach | Cost | Latency | Operations | Security/reliability | Scalability ceiling |
|---|---|---|---|---|---|
| Provider-only synchronous API | Variable, low idle | Provider-dependent | Lowest platform work | Data/region/quota dependency; easy fallback illusion | Provider quota |
| **Kubernetes model pools + policy-approved provider fallback** | Higher headroom, optimized route cost | Low warm latency; bounded fallback | High dual-stack/canary ownership | Zone tolerance, control and tested minimum fallback | Device plus provider capacity |
| Serverless/scale-to-zero model service | Low idle | Cold model/node violates tail target | Medium | Simple isolation, weak ordinary-latency fit | Cold-start and concurrency quota |

**Decision rationale.** Warm Kubernetes pools satisfy the tail and zone objectives while an eligible provider route absorbs specific capacity/dependency failures. Admission and goodput scaling prevent the fallback from becoming an unbounded retry amplifier. The provider response counts as good only where its data and quality contract matches. Scale-to-zero is rejected because its cold path exceeds the normal TTFT SLO.

### Scenario 2 - Durable research and coding agent

**Problem statement.** Design a multi-tenant research/coding service running 5 jobs/s with durations from 30 seconds to four hours. Runs use browser, repository, shell and publishing tools, survive deployment/node/region failure, support cancellation and human approval, and must not duplicate emails, commits or deployments. Accepted-operation/effect/audit RPO is zero; p99 ready queue age is 60 seconds and warm-region operation recovery is 30 minutes.

**Proposed architecture.** `POST /runs` authenticates, authorizes tools/repositories, reserves budgets and atomically writes tenant-scoped idempotency plus outbox before `202`. Kafka partitions work by tenant/run while Temporal owns branches, timers, approval and checkpoints. Workers claim Activities with heartbeat/lease and versioned workflow state. Browser/code tools execute in disposable sandbox-runtime pods on separate nodes with no ambient credential, default-deny egress and PEP-brokered one-operation capability. Effect ledger records intent/idempotency/receipt; ambiguous results reconcile. Separate queues and quotas isolate interactive from scheduled research. Warm region receives state/artifact/policy replication but cannot consume writes until a higher fencing epoch.

```text
┌──────────────┐ idempotent ┌──────────────┐ outbox ┌──────────────┐
│ client       ├───────────►│ run DB       ├───────►│ Kafka/DLQ    │
│ status/events│◄───────────┤ RPO 0        │        │ tenant/run   │
└──────────────┘            └──────────────┘        └──────┬───────┘
                                                           ▼
                                                 ┌──────────────────┐
                                                 │ Temporal/workers │
                                                 │ timer/checkpoint │
                                                 └───────┬──────────┘
                                                         ▼ exact capability
                                                 ┌──────────────────┐
                                                 │ tool PEP +       │
                                                 │ sandbox runtime  │
                                                 └───────┬──────────┘
                                                         ▼
                                                 ┌──────────────────┐
                                                 │ effect/audit     │
                                                 │ ledger + receipt │
                                                 └──────────────────┘
```

The scoped 5 jobs/s and measured 0.4 jobs/s/worker require 13 workers for arrivals and 17 with failure/burst headroom. Autoscaling uses oldest age and estimated remaining token/tool work, never CPU alone. Max workers respects repository/provider/DB limits. Shutdown marks not ready, stops claims, checkpoints/releases leases, drains bounded activity and flushes telemetry.

| Approach | Cost | Latency/recovery | Operations | Security/reliability | Scalability ceiling |
|---|---|---|---|---|---|
| Long HTTP request with pod-local loop | Low initial | Disconnect/deploy loses progress | Low | Unacceptable durability/effect ambiguity | Connection and pod lifetime |
| Queue plus stateless consumer/DB status | Medium | Restartable simple jobs | Medium | Duplicates manageable; branches/timers bespoke | State-machine complexity |
| **Temporal + Kafka/outbox + sandboxed idempotent Activities** | Highest platform investment | Fast resume; explicit wait/cancel | High workflow/version/runbook work | Strong history, fencing, effect reconciliation | High with partition/pool limits |

**Decision rationale.** A queue transports ownership but does not durably model a multi-hour branch, approval timer or compensation. Temporal supplies replayable control history while Activities remain explicitly idempotent or reconcilable. Sandbox and tool PEP contain generated code and credentials. The added platform cost is justified by zero-loss acceptance, bounded recovery and non-duplicated effects; it would be excessive for independent short batch items.

## Interview Review

1. **Why is a container not production?** It packages runtime bytes; admission, durable state, work ownership, scaling, release and recovery remain separate contracts.
2. **Readiness versus liveness?** Readiness controls traffic capability; liveness restarts locally unrecoverable process failure. Downstream slowness should not restart every pod.
3. **Does a PDB guarantee availability?** No. It limits voluntary disruptions, not node/AZ failure or every rollout setting; topology and spare capacity still matter.
4. **What does at-least-once mean?** Broker work can redeliver. Commit before ack, fence owners and make each named effect idempotent/reconcilable.
5. **What does Kafka exactly-once not cover?** Arbitrary external databases, payments, emails or tools outside the Kafka transaction.
6. **Why not scale on CPU?** Agent/model work can queue on tokens, GPU KV, provider quota or slow tools while CPU is moderate. Scale on SLO pain and remaining work.
7. **How do retries avoid multiplication?** One owner per failure class, propagated deadline/attempt/idempotency, full jitter and global attempt/cost budget.
8. **How is DR proved?** State-specific RPO/RTO plus isolated restore, capacity, ownership fencing, controlled cohort, reconciliation and failback exercises.

## Primary References

- [OCI specifications](https://opencontainers.org/about/overview/)
- [Docker build best practices](https://docs.docker.com/build/building/best-practices/)
- [SLSA 1.2](https://slsa.dev/spec/v1.2/)
- [Sigstore Cosign container signing](https://docs.sigstore.dev/cosign/signing/signing_with_containers/)
- [Kubernetes Pods](https://kubernetes.io/docs/concepts/workloads/pods/)
- [Kubernetes rolling updates](https://kubernetes.io/docs/tasks/run-application/update-deployment-rolling/)
- [Kubernetes probes](https://kubernetes.io/docs/concepts/workloads/pods/probes/)
- [Kubernetes disruptions and PDB](https://kubernetes.io/docs/concepts/workloads/pods/disruptions/)
- [Kubernetes HPA](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/)
- [Kubernetes node autoscaling](https://kubernetes.io/docs/concepts/cluster-administration/node-autoscaling/)
- [KEDA ScaledObject](https://keda.sh/docs/2.21/reference/scaledobject-spec/)
- [OpenAPI 3.2.0](https://spec.openapis.org/oas/v3.2.0.html)
- [HTTP semantics](https://www.rfc-editor.org/rfc/rfc9110.html)
- [HTTP Problem Details](https://www.rfc-editor.org/rfc/rfc9457.html)
- [OAuth 2.0 Security BCP](https://www.rfc-editor.org/rfc/rfc9700.html)
- [SQS delivery and visibility](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html)
- [RabbitMQ reliability](https://www.rabbitmq.com/docs/reliability)
- [Kafka design and delivery](https://kafka.apache.org/43/design/design/)
- [Temporal durable execution](https://docs.temporal.io/)
- [Transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html)
- [Google SRE SLOs](https://sre.google/workbook/implementing-slos/)
- [Google SRE cascading failures](https://sre.google/sre-book/addressing-cascading-failures/)
- [NIST contingency planning](https://csrc.nist.gov/pubs/sp/800/34/r1/upd1/final)
- [Kubernetes Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/)
- [OWASP API Security Top 10](https://owasp.org/API-Security/editions/2023/en/0x11-t10/)
