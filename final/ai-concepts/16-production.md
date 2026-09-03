# Module 16: Production -- Docker, Kubernetes, APIs, Queues, Scaling, and Reliability

## What Is This?

Deploying LLM applications to production is fundamentally different from deploying traditional web applications, and understanding these differences is the key to this module.

**Traditional web apps** are stateless (any server can handle any request), CPU-bound (compute is cheap), and fast (response in milliseconds). **LLM apps** are stateful (the KV cache ties a request to a specific GPU), GPU-bound (GPUs are 10-100x more expensive than CPUs), and slow (responses take seconds, sometimes minutes for complex agents).

These differences change everything about how you deploy:
- **Scaling**: You can't just add more servers — you need more GPUs, which cost $2-8/hour each and take minutes to provision.
- **Load balancing**: You can't round-robin requests because of KV cache affinity — switching a conversation to a different GPU means rebuilding the cache from scratch.
- **Fault tolerance**: If a GPU dies mid-generation, you lose the KV cache and must restart. For a 10-minute agent task, that's expensive.
- **Cost**: A traditional API might cost $0.001 per request. An LLM API might cost $0.10-$1.00 per request. Cost management is a first-class concern, not an afterthought.

The core deployment stack: **Docker containers** with GPU drivers package the model, **Kubernetes** orchestrates them across GPU nodes, **autoscalers** (KEDA, HPA) add/remove GPU pods based on queue depth, and **inference servers** (vLLM, TensorRT-LLM, NVIDIA NIM) optimize the actual model execution.

## Why It Matters

The gap between "works on my laptop" and "works in production at scale" is larger for LLM apps than for any other type of software. Models are expensive, GPUs are scarce, and failures are costly. This module covers the patterns and tools that make production LLM deployments reliable and cost-effective.

---

## 2. Core Concepts

### The Seven Production Contracts

Before any technology choice, define these contracts:

- **Artifact contract**: Exactly what code, model adapter, libraries, configuration, provenance, and vulnerabilities are being deployed. Every running pod should map to image/model/prompt/tool/source digests, provenance, SBOM, scan, signature, and approval.
- **Admission contract**: Whether a request may consume tokens, tools, money, data, and scarce accelerator time. Reject early -- before expensive execution begins.
- **Execution contract**: Distinguishes synchronous inference (deadline-bound, may stream) from durable asynchronous work (outlives a connection) and offline batch (throughput-optimized).
- **Ownership contract**: Who owns a queued job, when ownership expires, and what makes retry safe. A broker transfer and an application commit are different events.
- **Capacity contract**: Connects demand and service objectives to pod, node, accelerator, model, and provider capacity. Measure demand by work class, not request count.
- **Release contract**: Defines health, progressive exposure, automated gates, rollback, and schema compatibility. A "routine deploy" that drops 2% of streams is not zero downtime.
- **Recovery contract**: Defines SLO, RPO (Recovery Point Objective -- max tolerable data loss), RTO (Recovery Time Objective -- max tolerable restore time), backups, failover, and tested restoration.

### Two Planes, Three Clocks

| Plane | What it is | Clock | Typical store | Failure if mixed |
|---|---|---|---|---|
| **Control** | GPU Operator, Gateway/HTTPRoute/InferencePool, KEDA ScaledObject, Karpenter NodePool, Temporal server, admission (Kyverno/Binary Authorization) | kube-apiserver + scaler poll (KEDA default 15s; HPA ~15s) | etcd, Helm values, GitOps | App code that "picks a GPU" by inspecting prompts |
| **Data (tokens)** | Prefill/decode kernels, KV cache, prefix blocks, SSE/HTTP2 streams | User SLO clock: TTFT / TPOT / e2e | HBM on the replica; optional CPU/disk offload | Round-robin L4 load balancing across replicas causing prefix-cache miss storm |
| **Data (side effects)** | Tool calls, MCP sessions, agent workflow history, queue offsets | Durable-execution clock (Temporal event history; Kafka offset; SQS visibility timeout) | Temporal persistence / Kafka log / SQS / Redis PEL | Retrying a chat completion as if it were a Stripe POST *and* retrying a `payments.charge` tool without an idempotency key |

**Key insight**: The GPU is not a pod; the KV cache is. Any topology that lets the scheduler kill a replica without draining in-flight decode is treating state as cattle.

### Three Workload Paths

```text
                                    control plane
                    artifact registry, policy, releases, schemas,
                    model routing, autoscaling, SLOs, audit, DR
                                          |
 client / agent -- edge/WAF -- API gateway -- authn/authz/quota/admission
                                          |
                  +-----------------------+------------------------+
                  |                        |                        |
          synchronous path         durable agent path        offline batch
         deadline + streaming      accept -> job ID/event      manifest/shards
                  |                        |                        |
         inference gateway           durable queue/log          job controller
          + model workers          workflow/orchestrator         batch workers
                  |                        |                        |
          bounded tool calls       checkpoint + activities       output commit
                  +-----------------------+------------------------+
                                          |
                    DB/object store/vector store/event history
```

**Synchronous inference** is deadline-bound and may stream. Return a bounded error (`429` or `503` plus retry advice) instead of accumulating an unbounded in-memory queue. An HTTP disconnect does not prove the provider stopped billing or the model stopped generating; cancellation must be propagated explicitly.

**Durable agent work** can outlive a connection, pod, deployment, or region. An accept operation returns a stable operation/job ID. The execution record stores input version, policy, model/tool versions, state/checkpoints, attempts, budget consumed, output, and terminal reason. Temporal documents replay-based durable execution across process and infrastructure failure.

**Offline batch** optimizes throughput, determinism, resumability, and cost. A manifest defines immutable inputs, shard identity, artifact/model/prompt versions, output schema, retry policy, and commit protocol.

Never silently convert one path into another. A synchronous request that times out while a server continues processing creates an invisible expensive job.

### SLI/SLO/Error Budget

An **SLI** (Service Level Indicator) measures an outcome -- the ratio of "good events" to "total events." An **SLO** (Service Level Objective) sets the target for that SLI. An **SLA** (Service Level Agreement) is the external agreement with consequences. **Error budget** = 100% - SLO. Google's example: a 99.9% target over 3M requests permits 3,000 bad requests per measurement window.

For inference, **HTTP 200 is not "good"** if TTFT blew the chat UX. Do not use pod uptime as the primary user SLI.

---

## 3. How It Works

### 3.1 Docker and OCI: Build Immutable, Attestable Artifacts

The OCI image specification packages content-addressed manifests, configuration, and filesystem layers. Content addressing answers "which bytes?" but not "were these bytes built from the reviewed source by the approved process?" Add provenance, signature verification, vulnerability policy, and deployment admission.

**GPU images require three layers** (not just "CUDA + app"):

1. **Host kernel driver** -- NVIDIA GPU Operator deploys driver, Container Toolkit, Device Plugin, DCGM Exporter, MIG Manager as DaemonSet pods. The `gpu-operator` namespace must be `pod-security.kubernetes.io/enforce=privileged` because driver containers load kernel modules.
2. **Container Toolkit / CDI** -- Injects devices and CUDA userspace into the pod. The app image should *not* ship a second driver. Toolkit/operator mismatch is a classic `nvidia.com/gpu: 0` failure.
3. **App image** -- vLLM (`vllm/vllm-openai`), NVIDIA NIM, or a distroless agent worker. Distroless is for the control plane and CPU agent workers, not for the CUDA runtime (CUDA needs `libcuda`/cuDNN/NCCL).

**Conservative multi-stage Dockerfile**:

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.13-slim@sha256:<reviewed-digest> AS build
WORKDIR /build
COPY requirements.lock .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip wheel --require-hashes --wheel-dir=/wheels -r requirements.lock

FROM python:3.13-slim@sha256:<same-or-reviewed-runtime-digest>
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN addgroup --system app && adduser --system --ingroup app app
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
WORKDIR /app
COPY --chown=app:app src/ ./src/
USER app
ENTRYPOINT ["python", "-m", "src.service"]
```

**Key practices**:
- Pin base images by digest for auditability (but dependency automation must propose, test, scan, sign, and roll out updated digests)
- Never bake provider keys, cluster credentials, or tenant data into layers or build args
- Use read-only root filesystem, non-root user, dropped Linux capabilities, bounded PID/memory/CPU/ephemeral storage, default seccomp profile
- Docker rootless mode runs both daemon and containers without root in a user namespace
- Docker's default seccomp denies ~44 of 300+ syscalls
- A container is process isolation sharing a kernel, not a security boundary equivalent to a dedicated machine

**Image layout for production** -- four artifacts, four TTLs:

1. **Engine image** (vLLM/NIM digest, rebuilt on CVE)
2. **Weights** (HF/S3/FSx, checksummed, RWX for LWS)
3. **Tokenizer/config** (small ConfigMap or sidecar -- pinning tokenizer drift is a silent quality incident)
4. **LoRA adapters** (hot-loaded, versioned independently)

**Supply chain**:
- SLSA v1.2 defines source and build tracks for supply-chain assurance
- Cosign signs container images by digest; verify identity/issuer and attestation predicates at admission
- An SBOM attached as a signed Cosign attestation on the image digest is evidence; an SBOM on a GitHub Release page is documentation
- Keyless Cosign (Fulcio + Rekor) + Kyverno/`policy-controller` `verifyImages` at admission is the portable pattern

**The release unit should identify**:

```text
image digest + source commit + lockfile digest + build provenance
+ SBOM + scan result + signature + policy version
+ model/adapter/tokenizer digest + prompt/tool/schema versions
+ database migration compatibility + deployment manifest digest
```

### 3.2 Kubernetes: Reconciliation, Scheduling, Health, and Rollout

**Workload controllers by semantics**:

| Primitive | Use | Avoid assuming |
|---|---|---|
| `Deployment` | Stateless API, router, consumer, inference replica | Pod identity or durable local state |
| `StatefulSet` | Stable identity/storage/order is intrinsic | Storage replication or consistency is automatic |
| `Job` / `CronJob` | Bounded, retryable completion / scheduled work | Side effects are exactly once |
| DaemonSet | Node-local agent/device/log/network component | Ordinary horizontally scaled service |
| Managed external service | DB, broker, object store, model provider | Provider outage disappears |

**Resource management**: Kubernetes schedules from declared resource requests, not real usage; limits are enforced differently by resource. Under-requesting produces contention; over-requesting prevents consolidation. Keep model-serving pools separate from general agent/API pools so CPU scale-out cannot consume reserved GPU nodes.

**GPU-specific topology**:
- Pods request `nvidia.com/gpu` (integer, not millicores). GPU Feature Discovery labels product, VRAM, CUDA.
- **MIG** (Multi-Instance GPU): hardware isolation, fewer concurrent contexts. Changing `nvidia.com/mig.config` stops all GPU pods on that node.
- **Time-slicing**: noisy neighbor on HBM. Full GPU is default for vLLM because KV cache wants contiguous HBM.
- **LeaderWorkerSet (LWS)**: unit of replication for TP/DP/EP -- leader + workers, stable DNS, RDMA. Scale by LWS replicas, not HPA on a single leader.

**Health probes** have distinct meanings:
- **Startup probe**: Suppresses liveness and readiness until initialization succeeds. NIM: `failureThreshold x periodSeconds` can be minutes for 70B models.
- **Readiness**: Controls traffic eligibility. Should require model/adapter loaded, but failing readiness during dependency outage can remove all endpoints.
- **Liveness**: Restarts unrecoverably stuck containers. A bad liveness probe (failing because a remote provider is slow) can cause cascading failure by restarting containers under load.

**Critical rule**: Readiness on `/v1/health/ready` (weights in HBM). Liveness on `/v1/health/live` (process up). Inverting these sends traffic to a loading GPU and then OOM-kills it.

**Graceful termination sequence**:

1. Mark the pod not ready and stop new admission
2. Stop pulling new queue items; extend leases for accepted work
3. Drain HTTP/gRPC streams within a bounded grace period
4. Checkpoint or relinquish unfinished durable work
5. Flush bounded telemetry, then exit before `terminationGracePeriodSeconds`

Default `terminationGracePeriodSeconds=30`. A 28-minute decode is not 30 seconds. Pattern: readiness fail on SIGTERM (remove from Service/InferencePool) -> finish or cancel in-flight -> exit.

**PodDisruptionBudget (PDB)**: Limits approved voluntary evictions using `minAvailable` or `maxUnavailable`. Does NOT prevent involuntary node failures. `maxUnavailable: 0` or `minAvailable: 100%` means drain never completes. `unhealthyPodEvictionPolicy: AlwaysAllow` so CrashLoop pods don't deadlock Karpenter. GPU inference PDB must leave enough Ready+warm replicas to absorb one drain; cold start is not "available."

**Rolling updates**: Deployment `RollingUpdate` defaults to 25% `maxUnavailable` and 25% `maxSurge`. For an expensive GPU model, `maxSurge` may be impossible without reserved capacity. For a one-replica API, nonzero unavailability creates downtime. Database, queue-event, API, prompt, tool, and output-schema changes need backward/forward compatibility across the mixed-version window.

### 3.3 Inference Gateway Stack

An inference gateway is NOT a standard service mesh. A service mesh (Istio mTLS, DestinationRules) is the east-west control plane for workers. An inference gateway is the north-south control plane that must parse the OpenAI body (`model` field), not just `:path`.

| Layer | Job | Products |
|---|---|---|
| Edge / Tier-1 | AuthN, RPM/TPM quotas, model alias, canary split, PII filter | Envoy AI Gateway, Apigee, LiteLLM |
| Inference / Tier-2 | Endpoint pick on KV/queue/LoRA; P/D routing | GIE InferencePool + EPP / llm-d-router; GKE Inference Gateway |
| Engine | OpenAI `/v1/chat/completions`, `/v1/models` | vLLM, NIM |

**GIE request flow**: Gateway matches HTTPRoute -> if backend is an `InferencePool`, forward to EPP -> EPP scores endpoints (KV / queue / LoRA) -> Gateway sends to that Pod IP. Do not put Istio's default round-robin in front of vLLM and call it "done." AWS sample: precise KV-aware routing vs round-robin reduced p90 TTFT by up to 69% under Poisson multi-turn load.

**KServe dual-track (v0.20)**: `InferenceService` for predictive ML. `LLMInferenceService` for GenAI on llm-d with KV-aware scheduling, P/D disaggregation, and LWS multi-node.

**vLLM Production Stack**: Helm-based serving engines + router (session/prefix-aware/disaggregated_prefill) + Prometheus/Grafana. Disaggregated prefill: separate Deployments, router `enablePD: true`, NIXL KV transfer; same-AZ RDMA/EFA required.

### 3.4 API Contracts: Admission Before Expensive Execution

For every public operation define:
- Authentication and tenant identity
- Object-, field-, and function-level authorization
- Input/output schema, size, token, attachment, URL, tool, and model constraints
- Idempotency and deduplication behavior
- Absolute deadline and server-side work budget
- Streaming/event resume and cancellation behavior
- Rate, concurrency, spend, and provider-quota admission
- Retryable/non-retryable errors and `Retry-After`
- Privacy, retention, residency, and audit semantics

**Admission order** (reject early):

```text
parse/minimal size checks -> authenticate -> authorize tenant/object/action
-> validate schema/content -> policy/tool/URL allowlists
-> idempotency lookup -> rate/concurrency/spend/provider quota
-> estimate work + deadline feasibility -> enqueue/execute
```

**Idempotency**: HTTP defines PUT, DELETE, and safe methods as idempotent. A `POST /agent-runs` with side effects requires an idempotency key scoped to `tenant + operation + canonical request hash`. Stripe pattern: `Idempotency-Key` (<=255 chars, UUID v4); store first status+body >=24h including 500s; param mismatch returns error.

**Critical distinction**: Chat completions are NOT Stripe-idempotent. Correct split: idempotency on side-effecting tools and workflow start; at-most-once or explicit "resume stream" on token generation.

**Quotas (RPM/TPM)**: OpenAI enforces independent RPM, RPD, TPM, TPD, IPM; first ceiling hit wins. Streaming does NOT get a cheaper pool; TPM is counted when the request completes. Self-hosted gateways should mirror this: token-bucket per `(tenant, model)` in Redis, plus concurrency (in-flight streams) because TPM is lagging.

**Streaming SSE**: Chat Completions `stream=true` returns data-only SSE chunks terminated with `[DONE]`. Production implications:
- LBs must not buffer the whole body
- Idle timeouts must exceed `TPOT x max_tokens`, not "60s API timeout"
- No retry of a failed stream -- client must reconnect with a new request
- Moderation scores arrive after full output, not on deltas

**Machine-readable failures**: RFC 9457 defines `application/problem+json` with `type`, `title`, `status`, `detail`, `instance`. Return 429 with Retry-After for overload vs 402/403 for tenant budget vs 503 for no Ready endpoints -- clients and KEDA must not treat those as the same signal.

**Deadlines must be end-to-end**: gRPC has no deadline by default. Allocate the outer deadline across queue, model, tools, verification, response, and cancellation cleanup; each child receives the smaller of its budget and remaining parent time.

### 3.5 Queue Mechanics: Delivery is Not Processing

| Contract | Mechanism | Consequence |
|---|---|---|
| At-most-once | Remove/ack before or without durable processing | Work may be lost; no broker duplicate |
| At-least-once | Ack only after durable result; lease/visibility expiry redelivers | No loss under stated durability; duplicates expected |
| Ordered | Partition/message-group key plus serialized ownership | Throughput limited per key; cross-key order absent |
| Effectively once | At-least-once + idempotent effect/dedupe/transaction | Guarantee scoped to a defined state transition |

**System comparison**:

| System | Ordering | Back-pressure | DLQ | Fit |
|---|---|---|---|---|
| **Kafka** | Per partition | Passive: lag. `pause()`/`resume()` | App-level retry topic | High-throughput event log; replay; multi-consumer |
| **SQS** | Standard: none. FIFO: group | Visibility timeout + queue depth | Native redrive `maxReceiveCount` | Simple workers; KEDA SQS scaler |
| **Redis Streams** | Stream ID order | `MAXLEN` trim; PEL via `XREADGROUP`/`XACK`/`XCLAIM` | DIY | Hours-days retention; low ops |
| **Temporal** | Workflow history | Task-queue backlog; Worker slots | Failed activities retry by policy | Agents, HITL, multi-step tools |

**Kafka head-of-line blocking**: One slow message stalls a partition; adding consumers does NOT help; exceeding `max.poll.interval.ms` (default 5 min) evicts the member causing a rebalance storm. Fix: pause partition + worker thread, or timeout to DLQ.

**SQS**: Standard queues provide at-least-once delivery and can duplicate or reorder. Visibility is a renewable ownership lease. Set DLQ retention longer than source. `maxReceiveCount=1` is not resilience -- it is a panic button.

**Redis Streams**: Consumer groups share a `last-delivered-id`; each consumer has a PEL (Pending Entries List). Forgotten `XACK` = unbounded PEL = a leak disguised as back-pressure. `XCLAIM` min-idle-time reclaims after a crashed worker.

**Temporal**: Workflow = deterministic orchestration; every LLM call and tool is an Activity. Disable SDK-internal retries so Temporal owns backoff. Always set `Start-To-Close` timeout (server cannot detect a dead worker otherwise). Worker Controller supports rainbow deploys with progressive ramp + gate Workflow.

**Robust worker pattern**:

```text
receive message + attempt + lease
 -> validate envelope/schema/tenant/version
 -> acquire idempotency/operation record
 -> if committed: acknowledge duplicate
 -> mark attempt running; heartbeat/extend lease
 -> perform restartable steps; checkpoint after durable boundaries
 -> commit output/effect + operation terminal state atomically
 -> publish resulting event through transactional outbox/CDC
 -> acknowledge input
```

**Transactional outbox**: Writes business state and an event row in one database transaction; a relay later publishes committed rows. Consumers remain idempotent because the relay/broker can duplicate.

**Every queue needs**: Bounded attempts, exponential backoff with jitter, lease heartbeats, maximum age, poison classification, dead-letter quarantine, redrive authorization, and replay tooling. Alert on **oldest ready age**, not only depth.

**Back-pressure across the stack**: Gateway concurrency limit (fail 429) -> vLLM waiting queue (KEDA) -> Kafka/SQS lag (KEDA workers) -> Temporal Schedule-To-Start (worker deficit). If you only watch GPU util, you will scale the wrong layer.

### 3.6 Autoscaling: A Two-Loop, Delayed Control System

**Two loops**:

| Loop | Tool | Signal | Scale-to-zero? |
|---|---|---|---|
| Pod | KEDA `ScaledObject` -> managed HPA | Prometheus `vllm:num_requests_waiting`, p95 `vllm:e2e_request_latency_seconds`; Kafka lag; SQS depth | Yes (`minReplicaCount: 0`); HPA alone cannot |
| Node | Karpenter NodePool | Unschedulable pods | Yes (empty node consolidation) |

**HPA formula**: `desiredReplicas = ceil(currentReplicas * currentMetric / desiredMetric)` with 10% tolerance. Default sync interval 15s; multiple metrics choose the largest recommendation; default scale-down stabilization 300s.

**KEDA**: Activation (0<->1, `activationThreshold`) vs scaling (1<->N, HPA target). `threshold: 10` + `activationThreshold: 50` with 40 messages means stay at 0. Pause via `autoscaling.keda.sh/paused`.

**Why CPU HPA lies for inference**: A saturated decode replica can show low CPU and pinned SM util while the queue is the demand signal. Scale on the bottleneck closest to user pain:

- APIs: admitted concurrency, request queue time, active streams, deadline slack
- Queue consumers: oldest-message age and estimated remaining work
- Inference: queued tokens/prefill work, active decode sequences/KV pressure, SLO goodput
- Tools: provider concurrency/quota and latency
- Batch: work remaining versus completion deadline and budget

**Delayed stages** (each adds latency):

```text
metric collection -> pod decision -> pending pod -> node/accelerator provision
-> image/model download -> runtime/model warmup -> readiness -> useful capacity
```

Scale-up must be faster than model-load time or you add replicas that are NotReady while the queue is already the SLO violation. Scale-down stabilization (300s) exists because a GPU that took 3-8 minutes to become Ready should not be killed by a 30s lull.

**Karpenter GPU NodePools**: Separate pools: (a) on-demand decode (`p5`/`a3`/`ND`), `expireAfter` for CIS node recycle; (b) spot prefill/batch with interruption draining; (c) CPU for gateways/EPP/Temporal. Consolidation `WhenEmpty` is safer than aggressive bin-pack on GPU.

**Scale-to-zero traps**: Unsuitable when model/node startup exceeds acceptable queue age. Keep warm minimums, scheduled/predictive headroom, pre-pulled artifacts, or admission capable of returning an honest asynchronous/retry response. Bound `maxReplicas` by downstream/provider quota, database connections, broker partitions, GPU supply, and spend.

### 3.7 Distributed Resilience and State

**Classify state and assign recovery ownership**:

| State | Source of truth | Key hazard |
|---|---|---|
| Container filesystem | None; disposable | Accidental hidden state |
| Request/session | DB or signed client state | Sticky pod fate-sharing |
| Operation/workflow | Durable DB/event history | Duplicate side effects |
| Queue/log | Broker replicas | Loss, duplicate, poison, order |
| Relational/vector data | Managed DB/object store | Corrupt or stale index |
| Model/prompt/tool artifact | Immutable registry | Silent version drift |
| Cache/KV | Reconstructable | Treating cache as authoritative |
| Secrets/config/policy | Secret/config authority | Region drift/stale credentials |
| Audit/telemetry | Append-oriented store | Privacy leak or missing evidence |

**Sticky vs durable state**:

| State | Sticky (session affinity / prefix routing) | Durable (survive process death) |
|---|---|---|
| KV / prefix blocks | Yes (EPP / session-id router) | Optional (LMCache CPU/disk) |
| In-flight SSE | Yes (that TCP connection) | No -- reconnect = new request |
| Agent plan + tool results | No | Temporal event history |
| Queue messages | Partition/group | Kafka log / SQS / Redis PEL |
| Model weights | PVC / image / NIM cache | Object storage |

**Retry budget hierarchy**: Client, gateway, service, queue, workflow, SDK, and provider retries can multiply. With 3 attempts at 4 layers, one user action can produce `3^4 = 81` downstream attempts. Permit one layer to own retry for each failure class, propagate attempt/deadline/idempotency context, and cap total elapsed time.

**Overload and degradation**: Overload policy precedes autoscaling because scaling reacts after observation. Degradation ladder:
1. Preserve auth, policy, tenant isolation, idempotency, and audit
2. Reject new low-priority batch work or defer it durably
3. Reduce optional tools/verifiers or switch to pre-evaluated fallback model
4. Cap output/steps only where the API contract permits
5. Shed new interactive work before accepted work misses all deadlines
6. Never bypass authorization or safety to improve availability

**Circuit breakers**: Envoy per-cluster max connections, pending requests, concurrent requests, max retries (use retry budgets so retries cannot explode). Outlier detection ejects 5xx hosts. Apply at gateway->vLLM cluster AND agent->MCP cluster. Retry non-streaming 503/429 with jitter; do NOT blindly retry streaming or non-idempotent tools.

### 3.8 Multi-Zone and Multi-Region

Multi-zone is the normal HA baseline. Cross-zone replication does not address region-wide failure.

| Pattern | Data plane | Control plane | When |
|---|---|---|---|
| **Single-region multi-AZ** | Decode replicas per AZ; no TP across AZ | Gateway regional; Temporal workers spread | Default for chat |
| **Active-passive DR** | Warm (or cold) GPU pool in region B; weights in dual-region bucket | DNS failover | Compliance DR |
| **Active-active** | Independent InferencePools per region; sticky region by user/session | Global gateway with model+region routing | Capacity shopping + near-zero recovery |

**DR strategy comparison** (AWS Well-Architected illustrative ranges):

| Pattern | RPO | RTO | Cost |
|---|---|---|---|
| Backup/restore | Hours | Up to 24h | Lowest |
| Pilot light | Minutes | Tens of minutes | Low |
| Warm standby | Seconds | Minutes | Moderate |
| Active-active | Near-zero | Near-zero | Highest |

**Key rules**:
- Place stateless gateways and Temporal workers multi-AZ
- Place P/D KV transfer and TP ranks in one AZ (or one NVLink domain)
- Multi-region active-active inference: replicate *weights*, not KV
- Failover = cold cache -> TTFT SLO burn is expected; budget it
- Pre-provision accelerator/provider quota; a YAML copy in another region is not recoverable capacity
- Chaos: kill an AZ's GPU nodes and measure TTFT and 429 rate -- that IS the multi-AZ SLO

**Failover runbook**:

```text
declare incident and authority -> freeze unsafe writes/releases
-> establish source-of-truth region and queue ownership
-> verify data/artifact/config/secret recovery point
-> promote/scale dependencies and capacity
-> route a controlled cohort -> validate auth, correctness, policy, SLO
-> expand traffic -> reconcile ambiguous/in-flight operations
-> preserve evidence -> plan safe failback without split brain
```

---

## 4. Key Patterns & Best Practices

### Release Flow

```text
build -> tests/evals -> scan -> sign/attest -> policy admission
 -> deploy dark/shadow -> readiness/model warmup
 -> canary tenant/traffic cohort -> SLO + quality + cost gates
 -> staged expansion -> bake -> complete -> retain known-good rollback
```

Google defines canarying as a partial, time-limited deployment evaluated against a control. Example: 20% failure affecting a 5% canary population = 1% overall error rate. For agents, gate not only HTTP errors and latency but task success, tool denials, side-effect duplicates, tokens/cost per accepted result, loop terminations, queue age, checkpoint errors, and policy violations.

### Canary Surfaces (Use All Three)

| Surface | What you canary | Rollback |
|---|---|---|
| HTTPRoute / model name | App/model weights | Weight -> 0 |
| Argo Rollouts | Gateway binary / agent | RS revert; keep stable at 100% capacity |
| GPU Operator nodeSelector | Driver/toolkit | Git revert; 48h bake recommended |

### Gateway Quota Implementation

- Estimate TPM on request using tokenizer of the served model (not cl100k on a Llama backend)
- Decrement remaining tokens on `response.completed` / last SSE chunk
- If client aborts, still count generated tokens (vLLM kept running)
- Concurrency limit = `max_num_seqs x replicas x safety_factor < 1`
- Envoy cluster circuit breaker `max_pending_requests` is the last bulkhead before the GPU

### Prefill vs Decode Pools

Prefill is compute-bound (want high SM clocks, fewer high-end GPUs). Decode is memory-bandwidth/KV-capacity-bound (want HBM and stable TPOT). Disaggregation lets you right-size independently:
- Prefiller pushes KV via LMCache/NIXL/EFA layer-by-layer
- Decoder reserves `PD_BUFFER_SIZE`
- Same AZ for RDMA -- do not multi-AZ a NIXL path and expect training-cluster bandwidth

### Capacity Planning Checklist

1. Measure tokens/s and concurrent seqs at SLO TTFT/TPOT on one replica
2. KV bytes ~ 2 x layers x kv_heads x head_dim x dtype x seq x batch (order-of-magnitude)
3. Headroom: vLLM `--gpu-memory-utilization` default 0.9 OOMs when CUDA graphs + fragmentation eat the 10%; production recommends 0.75-0.85 on shared nodes
4. Spare replicas >= PDB `maxUnavailable` + 1 AZ failure
5. Karpenter CapacityBuffers for GPU if TTFT SLO cannot wait node spin-up

### NFR Percentile Contract

Specify shape, not just percentile: e.g., "p95 TTFT for prompts <= 2k tokens, decode <= 512 tokens, cache-hit ratio unstated." Mixing RAG 32k prompts into the same histogram as "hi" makes p95 a fiction. Separate SLIs: interactive vs batch vs agent-tool.

---

## 5. System Design Considerations

### 5.1 Scenario A: Multi-Tenant Interactive Inference API

**Requirements**: Streaming chat, strict tenant isolation, variable prompts, p99 latency SLO, one-zone loss, provider/self-hosted fallback.

| Decision | Choice | Reject | Why |
|---|---|---|---|
| Serving | vLLM + GIE/llm-d prefix routing; minReplicas >= 2 per AZ | Scale-to-zero | Cold start > TTFT budget |
| Ingress | GKE/Envoy Inference Gateway Tier-1+2; Model Armor | L4 NLB only | Body-based model routing + cache-aware pick |
| State | Sticky via EPP; no KV multi-AZ | Global anycast to random replica | KV is not in the session cookie |
| Agents | Temporal for tools; HTTP for tokens | Kafka for every token | Tokens need SSE; tools need durability |
| Security | mTLS + tenant RPM/TPM; distroless workers; signed GPU images | Shared API key to vLLM | Noisy neighbor + audit |

**Trade-off**: CPU utilization is a weak sole signal; `maxSurge` requires costly GPU headroom; fallback success must be part of the API/quality contract, not an availability trick.

### 5.2 Scenario B: Durable Research/Coding Agent

**Requirements**: 30-minute to multi-hour runs, browser/code tools, restarts and deployments, cancellation, no duplicate external writes.

**Design**: `POST /runs` with tenant-scoped idempotency key -> atomically create operation -> return `202` with status/event URLs. Workflow history holds state and timers; activities use idempotency keys, explicit timeouts, heartbeats, and bounded retries. Sandboxed tool workers have per-run filesystem, no ambient credentials, allowlisted egress.

**Trade-off**: A queue alone does not persist branching/timers/checkpoints; workflow replay does not make external activities exactly once. Combine durable orchestration with idempotent or reconcilable effects.

### 5.3 Scenario C: High-Volume Offline Evaluation/Enrichment

**Requirements**: Millions of records, completion deadline, restartability, schema-versioned output, low cost, no impact on online traffic.

| Decision | Choice | Reject | Why |
|---|---|---|---|
| API | SQS or Kafka + KEDA `minReplicaCount: 0` | Always-on H100s | Hours of idle |
| GPU | Spot + interruption drain; on-demand overflow | 100% spot | Involuntary eviction |
| SLO | Throughput + eventual completeness; DLQ for poison | Chat TTFT | Different user clock |
| Drain | Job or long `terminationGracePeriodSeconds` | 30s | KEDA long-running warning |

**Trade-off**: FIFO ordering is normally unnecessary and constrains throughput; determinism and idempotent per-item commit matter more. Batch SLO is a completion deadline, not p99 request latency. This is where scale-to-zero + Karpenter earns back GPU-hours.

### 5.4 Scenario D: Regulated Warm-Standby Multi-Region

**Requirements**: Residency controls, audited access, regional failure, defined RPO/RTO, controlled failback.

**Design**: Each permitted region has independent network, Kubernetes cluster, identities, artifact replica, policy/config, observability, database replica, and minimum operational capacity. One region owns writes and queue consumption using a fencing epoch; warm region runs synthetic validation. Failback is another controlled migration, not an automatic DNS reversal.

**Trade-off**: Warm standby costs more than backup/restore but reduces dependence on creating scarce capacity during disaster. Active-active rejected unless near-zero recovery justifies write-conflict, routing, quota, and consistency complexity.

### 5.5 Scenario E: Multi-Model LoRA Factory

GKE/GIE: many LoRAs on one base + adapter-aware scoring. Trade-off: density vs noisy neighbor (HBM for adapters + KV). Quota per `(tenant, adapter)`. Canary: new adapter = new InferenceObjective/model name, 10% split, not a fleet restart.

### 5.6 Scenario F: Regulated Agent with MCP Tools

| Layer | Control |
|---|---|
| Identity | User JWT -> gateway; MCP OAuth PRM; RFC 8707 resource |
| Tools | Envoy `MCPRoute` allowlist; CEL on params; no raw shell MCP in prod |
| Execution | Temporal activities + idempotency keys; sandbox (Kata/gVisor) for untrusted code |
| GPU | Isolated NodePool; optional Confidential Containers on Hopper |
| Audit | OTel + MCP method/tool; no prompt in Prometheus labels |

---

## 6. Code Examples

### Kubernetes Deployment with Proper Probes (vLLM Inference)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-inference
spec:
  replicas: 2
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 1
      maxSurge: 1  # Requires spare GPU capacity
  template:
    spec:
      terminationGracePeriodSeconds: 300  # Match p99 decode time
      containers:
      - name: vllm
        image: vllm/vllm-openai@sha256:<reviewed-digest>
        args:
          - --gpu-memory-utilization=0.85
          - --max-num-seqs=256
        resources:
          requests:
            nvidia.com/gpu: 1
            cpu: "4"
            memory: "32Gi"
          limits:
            nvidia.com/gpu: 1
        ports:
        - containerPort: 8000
        startupProbe:
          httpGet:
            path: /v1/health/ready
            port: 8000
          failureThreshold: 60    # 60 x 10s = 10 min for model load
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /v1/health/ready  # Model loaded and serving
            port: 8000
          periodSeconds: 5
          failureThreshold: 3
        livenessProbe:
          httpGet:
            path: /v1/health/live   # Process alive, NOT dependency check
            port: 8000
          periodSeconds: 15
          failureThreshold: 5
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: vllm-pdb
spec:
  maxUnavailable: 1
  unhealthyPodEvictionPolicy: AlwaysAllow  # Prevent CrashLoop deadlock
  selector:
    matchLabels:
      app: vllm-inference
```

### KEDA ScaledObject for Queue-Based Inference Scaling

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: vllm-scaler
spec:
  scaleTargetRef:
    name: vllm-inference
  minReplicaCount: 2       # Never zero for interactive
  maxReplicaCount: 8       # Bound by GPU supply + downstream quota
  cooldownPeriod: 300      # Don't kill a GPU that took 5 min to warm
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleUp:
          stabilizationWindowSeconds: 30
          policies:
          - type: Pods
            value: 2
            periodSeconds: 60
        scaleDown:
          stabilizationWindowSeconds: 300
          policies:
          - type: Pods
            value: 1
            periodSeconds: 120
  triggers:
  - type: prometheus
    metadata:
      serverAddress: http://prometheus:9090
      metricName: vllm_waiting_requests
      # Per-pod average waiting requests
      query: sum(vllm:num_requests_waiting) / count(vllm:num_requests_waiting)
      threshold: "25"
      activationThreshold: "5"  # Don't wake GPUs for noise
  - type: prometheus
    metadata:
      serverAddress: http://prometheus:9090
      metricName: vllm_e2e_p95
      query: histogram_quantile(0.95, sum(rate(vllm:e2e_request_latency_seconds_bucket[1m])) by (le))
      threshold: "5"            # 5-second p95 e2e
```

### Idempotent Queue Worker Pattern (Python pseudocode)

```python
async def process_message(msg: QueueMessage) -> None:
    """At-least-once queue consumer with idempotency."""
    # 1. Validate envelope
    if not validate_schema(msg.body, msg.version):
        await dead_letter(msg, reason="schema_invalid")
        return

    # 2. Check idempotency (atomic read-or-create)
    op = await operations_db.get_or_create(
        idempotency_key=msg.idempotency_key,
        request_hash=hash(msg.body),
        tenant=msg.tenant_id,
    )
    if op.status == "COMPLETED":
        await msg.ack()  # Duplicate -- safe to skip
        return
    if op.request_hash != hash(msg.body):
        await dead_letter(msg, reason="idempotency_key_reuse_different_input")
        return

    # 3. Mark running + heartbeat
    await op.mark_running(attempt=msg.receive_count)
    heartbeat_task = asyncio.create_task(
        heartbeat_loop(msg, interval_seconds=30)
    )

    try:
        # 4. Execute with deadline
        result = await asyncio.wait_for(
            execute_with_tools(op, msg.body),
            timeout=remaining_deadline(msg),
        )

        # 5. Commit result + mark terminal atomically
        async with db.transaction():
            await op.commit_result(result)
            await outbox.publish(op.completion_event())

        await msg.ack()
    except RetryableError:
        if msg.receive_count >= MAX_ATTEMPTS:
            await op.mark_failed_terminal()
            await dead_letter(msg, reason="max_attempts")
        # Else: visibility timeout will redeliver
    except NonRetryableError as e:
        await op.mark_failed_terminal(reason=str(e))
        await dead_letter(msg, reason="non_retryable")
    finally:
        heartbeat_task.cancel()
```

---

## 7. Common Pitfalls & Failure Modes

| Failure | Mechanism / Symptom | Prevention & Recovery |
|---|---|---|
| **GPU OOM** | `gpu_memory_utilization` too high; long `max_model_len`; KV + CUDA graph > free HBM | Lower utilization to 0.75-0.85; `--kv-cache-dtype fp8`; cap `max_num_seqs`; do not liveness-restart every OOM |
| **Noisy neighbor** | Time-slicing / MPS; two vLLM on one GPU; CPU noisy on EPP | MIG or full GPU; QoS Guaranteed; separate NodePools for interactive vs batch |
| **Rolling-update KV loss** | New RS, old pods SIGTERM; prefix cache empty; EPP scores dying pods | maxUnavailable 1; surge with extra GPUs; session pin until drain; canary 1% model name split |
| **Bad readiness** | Pod receives traffic before model warmup | Readiness tied to serving capability; startup probe with sufficient timeout |
| **Bad liveness** | Dependency/load spike restarts all pods | Local-progress liveness, tolerant thresholds |
| **HPA oscillation** | Delayed/noisy metric and rapid scale-down | Stabilization windows, multiple signals, headroom |
| **Scale-to-zero mid-decode** | HPA/KEDA treats GPU like nginx; 30s grace | minReplicas >= 1 for interactive; grace >= p99 decode; preStop drain |
| **CPU metric blindness** | Queue/deadline misses while CPU moderate | Queue age/work/concurrency/SLO metrics |
| **Scale-out overloads dependency** | More workers exhaust DB/provider quota | Max replicas from downstream budget, bulkhead |
| **Unbounded retry storm** | Retries multiply across layers after timeout | Retry ownership, global budget, jitter, deadline, idempotency |
| **Lost acknowledged job** | API responds before durable commit | Transactional persistence before `202 Accepted` |
| **Duplicate side effect** | Lease expires after effect but before ack | Effect idempotency key, intent/result, reconciliation |
| **Poison-message loop** | Deterministic bad input repeatedly consumes workers | Max attempts, quarantine/DLQ, schema validation |
| **Visibility too short** | Live worker loses lease, concurrent duplicate runs | Heartbeat/extend, checkpoint, fenced ownership |
| **Queue metric lies** | Depth low but oldest item/partition stuck | Age/partition/tenant/work metrics |
| **Thundering herd** | Scale-from-zero; all clients retry after 429 | activationThreshold; jittered Retry-After; warm min replicas; admission concurrency limit at gateway |
| **EPP / ext-proc down** | Gateway cannot pick endpoints | EPP HA; timeout + fail-closed; Istio outlier |
| **MIG reconfig** | Label change stops all GPU pods on node | Maintenance window; PDB across nodes |
| **NCCL/NIXL AZ split** | P/D or TP across AZ | Topology spread within AZ for ranks |
| **Streaming disconnect leak** | Downstream work continues after client leaves | Cancellation propagation and billing/reconciliation |
| **Deadline mismatch** | Gateway times out while worker continues | Propagated absolute deadline and child budgets |
| **Tenant starvation** | One customer/batch monopolizes workers/GPU | Per-tenant admission, queues, weighted fairness |
| **Regional split brain** | Both regions own writes after partition | Fencing epoch, single ownership/conflict rule |
| **Schema/version skew** | Old consumer cannot parse new event/tool output | Additive evolution, version envelope, contract tests |
| **NEG / LB cap** | GKE 50 NEG limit | Fewer ports; fewer zones on that Gateway; split Gateways |
| **Image supply-chain skip** | `:latest` unsigned CUDA image | Cosign+Kyverno; pin digest |
| **Observability overload/leak** | High-cardinality/raw prompts raise cost/privacy risk | Sampling, cardinality budgets, redaction, access/retention |

**Error budget mapping**: A rolling deploy that drops 2% of streams for 15 min on a 99.9% monthly SLO is not "zero downtime." Google: 1,500-error incident on a 3,000-error budget = 50% of the budget. Freeze features when budget is burned; that includes model swaps.

**Failure handling must define a terminal state**: `FAILED_RETRYABLE` without a bounded next attempt is not terminal; `UNKNOWN` after an external timeout requires reconciliation; cancellation may be `REQUESTED`, `CANCELLED_BEFORE_EFFECT`, or `COMPLETED_DESPITE_CANCEL`.

---

## 8. Interview Questions & Answers

**Q1: "Design a production inference API. What signals do you scale on?"**

Never scale on CPU alone. CPU is weakly correlated with GPU inference demand -- a saturated decode replica can show low CPU while the queue grows. The primary signals should be: (1) `vllm:num_requests_waiting` as a per-pod average -- this directly measures demand the GPU cannot serve yet; (2) p95 end-to-end latency against the SLO; (3) KV cache utilization percentage. These feed a KEDA ScaledObject that manages HPA. The second loop is node autoscaling (Karpenter), which reacts to unschedulable pods. The critical insight is the delay chain: metric collection -> pod decision -> pending pod -> node provision -> image pull -> model load -> readiness. Scale-up must be faster than model-load time, and scale-down stabilization (300s) must exceed model warmup time so you don't kill a GPU that took 5 minutes to become Ready just because of a 30-second lull. Also bound maxReplicas by downstream quota -- more consumers can make a dependency outage worse.

**Q2: "Why is rolling a vLLM deployment different from rolling nginx?"**

The KV cache is the state you are killing. When a new ReplicaSet replaces old pods, the prefix cache on the dying replicas is lost. The new replicas start cold, so the entire fleet sees a TTFT spike during a "routine" deploy. With nginx, a new pod is stateless and ready in seconds. With vLLM, readiness requires loading a 70B model into HBM, which can take minutes. The fixes: maxUnavailable 1 with pre-provisioned surge GPU capacity, session pinning until drain completes, LMCache offload to CPU/disk for cache preservation, and a canary by model name (1% traffic split to the new version before any RS roll). GPU Operator upgrades are even riskier because the blast radius is cluster-wide -- canary on labeled nodes first.

**Q3: "Explain how you'd implement idempotency for an agentic system."**

Idempotency has different meanings for different workload paths. Chat completions are NOT Stripe-idempotent -- you can't replay a generation safely without either caching the completion or charging twice. The correct split is: idempotency on side-effecting tools and workflow start (using an `Idempotency-Key` -> Temporal `Workflow-Id` mapping), and at-most-once or explicit "resume stream" for token generation. For tool calls like `payments.charge`, the Stripe pattern applies: store the first status+body for 24h, reject key reuse with different input. In a queue worker, the idempotency check happens early: get-or-create the operation record atomically, and if it is already committed, ack the duplicate. For irreversible external effects, pass the idempotency key to the effect provider, record intent/result, and reconcile ambiguous timeouts instead of blindly retrying.

**Q4: "What's the difference between at-least-once and exactly-once, and why does it matter for agents?"**

At-least-once means the broker redelivers if it doesn't receive an ack, so you get durability at the cost of possible duplicates. Exactly-once is more nuanced: Kafka's transactional exactly-once covers atomic read-process-write within Kafka itself, but it does NOT make an arbitrary external API call exactly once. The practical answer for agents is "effectively once": at-least-once delivery combined with idempotent effects at the application level. For a tool that sends an email, you need an idempotency key on the email service, not a Kafka transaction. The danger is that teams hear "exactly once" and stop worrying about duplicates, then discover their agent sent the same payment twice because the visibility timeout expired after the payment succeeded but before the ack.

**Q5: "How do you choose between Kafka, SQS, Redis Streams, and Temporal for an agent system?"**

Each serves a different need. Kafka for high-throughput event logs with replay and multi-consumer-group fan-out (100k+ msg/s). SQS for least-ops AWS-native workers with built-in DLQ. Temporal for multi-step agents, HITL workflows, and exactly-once business logic through durable execution. Redis Streams for short-retention, low-ops when you already have Redis. The key distinctions: Kafka's head-of-line blocking can stall a partition on one slow message; SQS standard queues have no ordering guarantee; Temporal's workflow is deterministic orchestration where LLM calls must be Activities; Redis requires manual DLQ and PEL management. For an agent system: tokens stay on HTTP/SSE through the inference gateway; tool side effects go through Temporal; event distribution goes through Kafka; simple task queues use SQS.

**Q6: "Walk me through your SLI/SLO design for a multi-tenant inference platform."**

I'd define separate SLIs for each workload path. For sync inference: availability = completed streams with `finish_reason in {stop, tool_calls}` without 5xx/429-shed; TTFT = first SSE delta under threshold (separate from TPOT/ITL); correctness = schema-valid JSON output. For durable agents: accepted run reaches correct terminal state within deadline; no duplicate side effects. For batch: item produces committed output by business deadline. Critical: shape the SLI threshold -- "p95 TTFT for prompts <= 2k tokens, decode <= 512 tokens" rather than mixing RAG 32k prompts and "hi" into one histogram. The error budget = 100% - SLO. At 99.9% over 3M requests, that's 3,000 errors per month. Deploys, model swaps, and GPU Operator upgrades consume this budget -- Google's argument is that change IS the outage source. Burn-rate alerts: 1h fast burn + 6h slow burn on TTFT-good-ratio, not GPU util. Count overload rejections and fallback responses separately.

**Q7: "How do you handle a queue meltdown in an agent system?"**

First, diagnose the right meltdown. Kafka: exponential lag means a partition is stuck (head-of-line blocking, not consumer shortage). SQS: in-flight count hitting visibility timeout storm means messages are being received but not acknowledged fast enough. Redis: unbounded PEL means consumers are processing but forgetting `XACK`. For Kafka HOL: pause the stuck partition, move the slow message to a DLQ, resume. Do not add consumers -- they can't help with per-partition blocking. For SQS: ensure `maxReceiveCount >= 3` (not 1), verify visibility timeout exceeds p99 processing time, check for heartbeat/extend. For all: alert on oldest-message age, not depth. Depth may be stable while one tenant or ordered partition is stuck. And critically: never allow unbounded HTTP retry into vLLM -- a retry storm from queue workers can cascade through the GPU fleet.

**Q8: "Describe your Docker image strategy for a GPU inference service."**

The key insight is that GPU images are three layers, not one. The host kernel driver comes from the GPU Operator (DaemonSet). The Container Toolkit injects CUDA userspace. The app image runs vLLM or NIM. Don't bake the driver into the app image -- toolkit/operator mismatch causes the classic `nvidia.com/gpu: 0` failure. For the app image: multi-stage build, pin base by digest, non-root user, read-only filesystem. But separate inference images from agent worker images -- inference needs GPU layers and model weights on PVC/S3 (don't bake 70B into the image); agent workers should be distroless/nonroot with no GPU. The supply chain: Cosign + Kyverno at admission verifies signed digests; an SBOM attached as a signed attestation on the image digest is evidence (not just docs on GitHub). Four artifacts with four TTLs: engine image, weights, tokenizer/config, and LoRA adapters -- each versioned independently.

**Q9: "How would you implement graceful drain for a vLLM pod during deployment?"**

Default `terminationGracePeriodSeconds=30` is catastrophically wrong for inference -- a 28-minute decode is not 30 seconds. Pattern: (1) On SIGTERM, readiness probe fails, removing the pod from the Service/InferencePool so no new requests arrive. (2) The pod finishes or cancels all in-flight decode streams within a bounded grace period. (3) It checkpoints or relinquishes any durable work. (4) Flushes telemetry and exits. Set `terminationGracePeriodSeconds` to match at least p99 decode time. Pair with a PDB that leaves enough Ready+warm replicas to absorb the drain -- cold start is not "available." Caveat: vLLM has had issues honoring HTTP cancellation during streaming -- the engine can continue minutes after client disconnect. If unfixed on your version, drain is not cancel, so you need conservative `maxUnavailable` paired with the drain.

**Q10: "What's the difference between PDB and topology spread constraints?"**

PDB limits approved voluntary evictions (kubectl drain, Karpenter consolidation) using `minAvailable` or `maxUnavailable`. It does NOT prevent involuntary node failures. Topology spread constraints express how pods should be distributed across failure domains (zones, nodes). They work together: spread constraints ensure replicas are in different zones so a single-zone failure doesn't take everything down; PDB ensures rolling updates don't remove too many at once. Key pitfalls: `maxUnavailable: 0` means drain never completes (deadlocks Karpenter). `unhealthyPodEvictionPolicy: AlwaysAllow` prevents CrashLooping pods from blocking eviction. And topology constraints can become imbalanced after scale-down -- zero-sized domains may be invisible unless the node autoscaler understands them. For GPU inference, PDB must account for cold start time -- a pod being "scheduled" is not the same as "serving."

**Q11: "Walk me through a multi-region failover for an LLM platform."**

The honest answer is that multi-region for inference is about weights replication, not KV replication. KV cache is replica-local and should stay that way. Pattern: each region has independent InferencePools, Gateway, Temporal workers, database replicas, artifact copies, and minimum GPU capacity. One region owns writes using a fencing epoch. The warm region runs synthetic validation continuously. Failover: declare incident, freeze unsafe writes/releases, confirm recovery point, promote state, scale capacity, validate a controlled cohort (auth, correctness, policy, SLO), then expand traffic. Failover means cold cache, so expect TTFT SLO burn -- budget it in the error budget. Critical: pre-provision GPU quota in the DR region. A YAML manifest in another region is not recoverable capacity if the GPUs aren't allocated. Failback is another controlled migration, not automatic DNS reversal -- you need to prevent split brain on queues and databases.

**Q12: "How do you handle the security of MCP tools in production?"**

Zero-trust MCP starts with the gateway as the policy enforcement point. The Envoy MCP Gateway pattern: OAuth on the client-facing `/mcp`, tool names prefixed (`github__issue_read`), `toolSelector` include/regex, per-backend secrets, and scoped header forwarding so fan-out `tools/list` doesn't leak tokens to every backend. Authorization uses JWT scopes/claims + CEL on `request.mcp.tool` and `request.mcp.params`. The MCP spec requires OAuth 2.1, PRM (RFC 9728), and resource indicators (RFC 8707) bound to the canonical MCP URI. The confused deputy problem: a token minted for `https://api.example.com/mcp` must not be accepted by a raw GitHub MCP. Practically: do not let agents dial MCP servers with a shared PAT. Bind tenant identity server-side, authorize callback and tool destinations, resolve DNS and validate redirects at connection time for SSRF defense.

**Q13: "What's the cost model for a production inference service?"**

The unit economics formula: `$/1k executions = (GPU_hours / executions) x 1000 x $/GPU-hr`. But this is misleading without qualification. GPU cost alone at ~$6.88/H100-hr on-demand, serving 3,440 requests/hr = ~$2/1k executions infrastructure-only. But add utilization gaps (40% serving time means effective cost is $17.20/serving-hour), 429s that still hit prefill before shed (count good completions in the denominator), EBS, egress, gateway CPU, Temporal, Kafka, and model storage. The three quota dimensions that matter: RPM binds chatty small prompts, TPM binds RAG (10k-token prompts), GPU binds in-flight sequences x KV bytes. A tenant can be under RPM and still OOM the replica. Burn-rate alerts: watch good-completion cost, not GPU util. And remember: if you front OpenAI/Bedrock, the token API cost is a second bill independent of GPU-hours.

---

## 9. Key Numbers to Memorize

| Metric | Value | Context |
|---|---|---|
| HPA default sync interval | **15 seconds** | How often pod autoscaler recalculates |
| HPA scale-down stabilization | **300 seconds** (5 min) | Default window before removing pods |
| KEDA default polling interval | **30 seconds** (activation), **15s** (scaling via HPA) | External metric check frequency |
| KEDA cooldown to zero | **300 seconds** | Default before scaling to zero |
| K8s Deployment rolling update defaults | **25% maxUnavailable, 25% maxSurge** | Neither is a safety decision |
| K8s stalled rollout timeout | **600 seconds** (10 min) | Default progress deadline |
| Docker seccomp default | **~44 of 300+ syscalls** denied | Default allowlist scope |
| SQS standard queues | **At-least-once**, can duplicate and reorder | Consumers must be idempotent |
| Kafka `max.poll.interval.ms` default | **5 minutes** | Exceeding evicts consumer -> rebalance |
| Temporal history limits | **51,200 events / 50 MB** | Hard stop unless Continue-As-New |
| SLO math example | **99.9% over 3M requests = 3,000 errors** | Error budget calculation |
| vLLM `--gpu-memory-utilization` default | **0.9** | Production: use **0.75-0.85** |
| H100 on-demand (aggregator, us-east-1) | **~$6.88/GPU-hr** (p5.4xlarge) | Third-party quote, not AWS Price List |
| GKE NEG limit | **50 NEG per Backend Service** | Caps multi-port x zones x clusters |
| KV-aware routing TTFT improvement | **Up to 69% p90 reduction** | AWS sample, not universal |
| Canary math (Google example) | **20% failure x 5% canary = 1% overall** | Under uniform-load assumptions |
| Retry multiplication | **3 attempts x 4 layers = 81 possible calls** | Why retry ownership matters |
| E2E idle timeout for SSE | **TPOT x max_tokens** | Not "60s API timeout" |
| Model load cold start | **84s small demo / minutes for 70B** | Why scale-to-zero is risky for interactive |

---

## 10. Quick Reference

### Technology Decision Matrix

| Decision | Prefer | When not to |
|---|---|---|
| Kubernetes | Many services, mixed pools, policy, scheduling, controlled rollouts | Small product can use managed containers/functions |
| Managed broker | Durability/HA/patching outweigh customization | Compliance, protocol, or latency needs self-operated broker |
| Durable workflow engine (Temporal) | Long-lived branching/timers/retries/human waits | Simple independent jobs with idempotent DB state suffice |
| Active-active regions | Near-zero regional recovery justifies complexity | Single writer/warm standby meets objectives |
| Scale to zero | Async or latency-tolerant with acceptable cold start | Interactive model load exceeds SLO |
| Native rolling update | Stateless compatible change with enough surge | Risky model/policy changes need progressive controller |

### HPA vs KEDA vs Knative

| | HPA | KEDA | Knative Serving |
|---|---|---|---|
| Scale to 0 | No | Yes | Yes (HTTP) |
| Custom PromQL | Adapter required | Native | Activator metrics |
| Async queues | Awkward | Native (Kafka/SQS) | Poor fit |
| Interactive HTTP | OK if min>=1 | OK | Built for it; still GPU-cold-start bound |

### Monolith vLLM vs Disaggregated P/D

| | Monolith | Disaggregated P/D |
|---|---|---|
| Ops | One Deployment | Router + two pools + NIXL |
| TTFT vs TPOT isolation | HOL blocking | Independent scaling |
| Network | Intra-node | Same-AZ RDMA |
| When | <7-13B, one GPU | Long context + high concurrency |

### Production-Readiness Checklist

1. **Artifact**: Can you map a running pod to image/model/prompt/tool/source digests, provenance, SBOM, scan, signature, and approval?
2. **API**: Are authz, schema, deadline, idempotency, quota, cancellation, retry, and error contracts explicit?
3. **Queue**: Who owns work, when does ownership expire, when is it acknowledged, how are duplicates/poison/order handled?
4. **State**: What survives a pod, node, zone, region, and bad deploy? Where is every source of truth?
5. **Scale**: What metric represents work/SLO pain, what is each control-loop delay, what is the downstream cap?
6. **Release**: Is mixed-version compatibility tested? What canary cohort, gates, bake time, and automated rollback?
7. **Security**: Are build, admission, runtime, network, API, tool, tenant, secret, and data boundaries independently enforced?
8. **Reliability**: What are the user SLIs/SLOs and error-budget actions? What are RPO/RTO per state?
9. **Failure proof**: Have retries, kill points, poison work, provider throttling, zone loss, control-plane outage, bad release, restore, failover, and failback been exercised?
10. **Economics**: Is cost measured per compliant successful outcome, including idle headroom, retries, failures, queue/storage/network, and observability?

### The Interview Close

A Principal AI Architect drawing "K8s + GPUs" is junior. The production diagram is: **signed distroless/CUDA images -> GPU Operator/MIG -> Karpenter NodePools -> vLLM/LWS with PDB and drain -> GIE/Envoy picking on KV not RR -> KEDA on queue/TTFT not CPU -> Temporal/Kafka for side effects -> OAuth MCP PEP -> SLOs on TTFT/TPOT with an error budget that includes deploys.** The strongest production answer links every technology choice to a failure or service contract. "Use Docker, Kubernetes, and a queue" is incomplete; explain the immutable artifact, admission, work ownership, idempotency boundary, scaling signal, rollout gate, and measured recovery proof.
