# 16. Production

**Sub-areas covered**: control-plane/data-plane separation for AI stacks · Kubernetes GPU scheduling (DRA, KAI Scheduler, Grove, HAMi-core) · queue-based task distribution & backpressure · KEDA/HPA autoscaling · Temporal durable execution · Kubernetes-native resilience (probes, PDBs) · multi-region/multi-AZ failover · Zero-Trust MCP on Istio Ambient · Kubernetes + tool-level RBAC · PII redaction pipelines · gVisor/Kata sandbox isolation · immutable audit logging · production failure taxonomy (OOM, autoscaler thrashing, cascading failures)

---

## 1. System Topology & Data Flow

A production agent deployment on Kubernetes is not "a chatbot behind a load balancer" — it is a resource-lifecycle system in which a request is *accepted and recorded* by a thin API tier, *executed* by an independently-scaled worker/inference tier (often minutes later, often on a different node), and *observed* through a status channel decoupled from the original HTTP connection. The topology below separates control plane (decides placement, policy, scaling), data plane (executes and forwards), persistence (durable job/workflow state), tool-proxy/mesh layer (zero-trust MCP + external tool egress), and telemetry (cost, health, audit) as five independently-failing planes.

```
                        ┌──────────────────────────────────────────────────────────────────┐
                        │                          CONTROL PLANE                             │
                        │                                                                    │
 ┌───────────┐  HTTPS   │ ┌──────────────┐   ┌────────────────┐   ┌────────────────────────┐│
 │  Client /  │─────────┼▶│ API Gateway / │──▶│ K8s API Server  │──▶│ kube-scheduler + KAI    ││
 │  Upstream  │◀─────────┼─│ Ingress       │   │ (admission,     │   │ Scheduler (secondary,   ││
 │  Service   │  job_id  │ │ (authN/authZ, │   │  RBAC check,    │   │  GPU gang-scheduling,   ││
 └───────────┘  + status │ │  rate limit)  │   │  writes to etcd)│   │  fair-share queue,      ││
                  URL    │ └──────┬────────┘   └────────┬────────┘   │  bin-packing preempt)   ││
                        │        │                     │            └────────────┬────────────┘│
                        │        │           ┌─────────▼─────────┐               │              │
                        │        │           │ Controller Manager │  ┌────────────▼────────────┐│
                        │        │           │ (Deployment/Replica│  │ HPA / KEDA ScaledObject   ││
                        │        │           │  Set reconcile loop│  │ (queue-depth + KV-cache   ││
                        │        │           │  + PDB enforcement)│  │  metrics → 0..N replicas) ││
                        │        │           └────────────────────┘  └────────────┬────────────┘│
                        └────────┼─────────────────────────────────────────────────┼─────────────┘
                                 │                                                  │ scales pods
                        ┌────────▼──────────────────────────────────────────────────▼─────────────┐
                        │                              DATA PLANE                                   │
                        │                                                                            │
                        │  ┌─────────────────────┐   enqueue job     ┌──────────────────────────┐   │
                        │  │ Agent API Pods       │──────────────────▶│  Durable Queue / Log      │   │
                        │  │ (stateless FastAPI:  │                   │  (Kafka / Redis Streams / │   │
                        │  │  POST /jobs, GET     │◀──────────────────│  SQS; bounded, DLQ after  │   │
                        │  │  /jobs/{id}/stream)  │  status/SSE read   │  retry exhaustion)        │   │
                        │  └──────────┬───────────┘                   └────────────┬─────────────┘   │
                        │             │                                            │ dequeue           │
                        │             │                                 ┌──────────▼─────────────┐    │
                        │             │                                 │ Agent Worker Pods        │    │
                        │             │                                 │ (consumer group per      │    │
                        │             │                                 │  agent type; Temporal     │    │
                        │             │                                 │  Workflow drives Activity │    │
                        │             │                                 │  calls; hard iteration cap│    │
                        │             │                                 └──────────┬─────────────┘    │
                        │             │                                            │ inference calls    │
                        │             │                                 ┌──────────▼─────────────┐    │
                        │             │                                 │ GPU Inference Pods       │    │
                        │             │                                 │ (vLLM/KServe; DRA-       │    │
                        │             │                                 │  allocated GPU; prefill/ │    │
                        │             │                                 │  decode disaggregated    │    │
                        │             │                                 │  via Grove; HAMi-core     │    │
                        │             │                                 │  VRAM quota enforcement) │    │
                        │             │                                 └──────────┬─────────────┘    │
                        └─────────────┼────────────────────────────────────────────┼───────────────────┘
                                      │                                            │
                        ┌─────────────▼────────────────────────────────────────────▼───────────────────┐
                        │                     TOOL PROXY / SERVICE MESH LAYER                             │
                        │  ┌────────────────────┐   ┌───────────────────────┐   ┌─────────────────────┐ │
                        │  │ ztunnel (per-node,  │──▶│ Waypoint Proxy         │──▶│ MCP Servers / Ext.   │ │
                        │  │  Istio Ambient; L4  │   │ (agentgateway; parses  │   │ Tool APIs (each in a  │ │
                        │  │  mTLS STRICT, SPIFFE│   │  MCP natively, CEL-    │   │ gVisor/Kata sandbox;  │ │
                        │  │  workload identity) │   │  evaluates tool-call   │   │ reaches only allow-   │ │
                        │  └────────────────────┘   │  authz per tools/call) │   │ listed data sources)  │ │
                        │                            └───────────────────────┘   └─────────────────────┘ │
                        └─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                        ┌─────────────▼─────────────────────────────────────────────────────────────────┐
                        │                              PERSISTENCE LAYER                                   │
                        │  ┌────────────────────┐  ┌───────────────────────┐  ┌─────────────────────────┐│
                        │  │ Temporal Event      │  │ Postgres / Redis       │  │ Dead-Letter Stream +      ││
                        │  │ History (durable    │  │ (job status rows, SSE  │  │ Object-Lock Audit Log     ││
                        │  │  workflow state,     │  │  event log, resumable  │  │ (WORM, hash-chained,      ││
                        │  │  idempotency keys)   │  │  via last_event_id)    │  │  append-only)             ││
                        │  └────────────────────┘  └───────────────────────┘  └─────────────────────────┘│
                        └─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                        ┌─────────────▼─────────────────────────────────────────────────────────────────┐
                        │                        TELEMETRY / OBSERVABILITY SINKS                            │
                        │  ┌────────────────────┐  ┌───────────────────────┐  ┌─────────────────────────┐│
                        │  │ OTel Collector      │  │ Prometheus (queue      │  │ PII-Scrubbed Trace Store ││
                        │  │ (gen_ai.*, mcp.*    │  │  depth, KV-cache-fill, │  │ (SPIFFE-identity-tagged   ││
                        │  │  semantic-conv attrs│  │  GPU util, HPA events) │  │  spans; tokens/masks only,││
                        │  │  on every span)     │  │  → Alertmanager        │  │  never raw PII values)    ││
                        │  └────────────────────┘  └───────────────────────┘  └─────────────────────────┘│
                        └─────────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A client request hits the **API Gateway/Ingress**, which terminates TLS, authenticates the caller, and applies a coarse per-tenant rate limit before the request ever reaches the cluster's data plane. (2) The **Agent API Pod** treats the request as a *resource creation*, not a synchronous computation: it writes a job record and immediately returns `202 Accepted` with a `job_id` and a stable status URL — this decouples the HTTP connection's lifetime from the agent run's (potentially minutes-long) lifetime. (3) The job is pushed onto a **durable queue** (Kafka/Redis Streams/SQS) with a compact payload (job ID, S3 URI to input, not inline data); an independent pool of **Agent Worker Pods**, grouped by consumer group per agent type, dequeues and executes it, giving failure isolation and independent scaling between the thin API tier and the heavier worker tier. (4) The worker's control logic runs as a **Temporal Workflow**; every LLM call and tool invocation is a Temporal **Activity**, so a worker crash mid-run replays from Event History rather than restarting or double-executing side effects. (5) Activities needing GPU inference call **GPU Inference Pods**, which the **KAI Scheduler** gang-schedules (all prefill/decode/router pods of a disaggregated serving group start together or not at all) onto GPUs allocated via the **DRA** (Dynamic Resource Allocation) API, with **HAMi-core** enforcing hard VRAM quotas at the CUDA-call level so GPU sharing is a verifiable contract, not a trust agreement. (6) The **HPA/KEDA autoscaler** watches queue depth (`num_requests_waiting`, the scale-*out* trigger) and per-replica KV-cache fill (a saturation guard feeding concurrency limits, never the scale trigger itself) to add or remove worker/inference replicas, including scaling GPU inference to **zero** replicas during idle windows — something native HPA cannot do (its floor is 1) but KEDA can. (7) Any call to an external system or MCP server crosses the **service-mesh/tool-proxy layer**: the per-node `ztunnel` enforces L4 mTLS and SPIFFE identity for every hop with zero sidecar injected into the agent pod itself, and an **agentgateway waypoint** parses MCP traffic natively, evaluating CEL policy against the *specific tool being called* — unauthorized tools are invisible to `tools/list` and rejected at `tools/call`, not merely logged after the fact. (8) Job status, streamed token deltas (batched, not per-token), and final results land in **Postgres/Redis** as rows a client can poll or subscribe to via `GET /jobs/{id}/stream/{last_event_id}`, enabling gap-free reconnection after a client disconnect; failed jobs that exhaust retries land in a **dead-letter stream** rather than being silently dropped. (9) Every hop emits an OTel span carrying `gen_ai.*` and `mcp.*` semantic-convention attributes plus the caller's SPIFFE identity into the **telemetry layer**, where Prometheus drives autoscaling/alerting decisions and a separate PII-scrubbed trace store guarantees raw sensitive values never reach observability infrastructure.

---

## 2. Core Mechanics & Algorithms

### 2.1 Kubernetes pod lifecycle (state machine)

```
   ┌─────────┐  scheduled   ┌───────────┐  all containers  ┌─────────┐  container exit  ┌───────────┐
   │ Pending │─────────────▶│ ContainerCreating│───────────▶│ Running │─────────────────▶│ Succeeded/│
   │ (unsched-│              │ (image pull,│    running     │ (probes │   (0 = Succeeded, │ Failed    │
   │  uled or │              │  volume     │                │  active)│    non-0 = Failed)│           │
   │  waiting │              │  mount)     │                └────┬────┘                  └───────────┘
   │  on DRA/ │              └───────────┘                       │
   │  GPU     │                                     liveness probe fails
   │  claim)  │                                                  │
   └─────────┘                                                   ▼
                                                          ┌───────────────┐
                                                          │ CrashLoopBackOff│
                                                          │ (kubelet restarts│
                                                          │  w/ exponential  │
                                                          │  backoff, capped │
                                                          │  at 5 min)       │
                                                          └───────────────┘
```

**Invariant**: a pod only receives traffic from a Service while its **readiness** probe passes — this is independent of the **liveness** probe, which only controls restarts. Conflating the two (e.g., checking a downstream DB in a liveness probe) is a self-inflicted-cascading-failure bug: a DB outage would then cause kubelet to restart otherwise-healthy pods, adding restart-storm load on top of the original outage. The **startup** probe exists to gate both other probes until slow initialization (e.g., vLLM loading a large model's weights, which can take on the order of tens of minutes) completes — `failureThreshold: 120 × periodSeconds: 10s` gives a 1,260s startup budget before the pod is even liveness/readiness-checked.

### 2.2 GPU scheduling stack — layered ownership model

GPU allocation on Kubernetes is not a single algorithm but four layers, each closing a gap the layer below leaves open:

1. **DRA (Dynamic Resource Allocation)** — GA in Kubernetes 1.34 (`resource.k8s.io/v1`) — replaces the old device-plugin model with an API-driven `ResourceClaim`/`ResourceClass` allocation framework. **Allocation-only**: it answers "which GPU does this pod get" but has no concept of fairness across teams or of gang semantics across a multi-pod job.
2. **KAI Scheduler** (secondary scheduler, runs alongside `kube-scheduler`) adds: **gang scheduling** (all pods of a multi-GPU job admitted atomically — either the whole cohort starts or none does, avoiding a deadlock where half a distributed job holds GPUs while waiting for the other half), **fair-share queuing** (per-team quota), **priority preemption**, and **bin-packing** (consolidate onto the fewest nodes to preserve contiguous multi-GPU NVLink domains for disaggregated serving).
3. **Grove** manages multi-pod *lifecycle* for disaggregated inference (prefill/decode/router role separation) — startup ordering and gang-scheduling constraints specific to serving topologies, sitting above KAI's raw scheduling.
4. **HAMi-core** closes the "governance gap" KAI leaves: KAI's GPU sharing is cooperative accounting only (no memory-limit enforcement); HAMi-core intercepts CUDA calls at runtime to make VRAM quotas a hard, verifiable contract.

**Algorithmic complexity**: gang scheduling is fundamentally an *all-or-nothing* admission-control problem — a naive scheduler that admits pods one at a time can deadlock (`O(n)` pods each holding one of `n` required GPUs, none able to proceed); KAI's gang scheduler instead evaluates admission for the whole pod-group as a single atomic decision, `O(g)` where `g` is group size, and either admits or defers the entire group. **Key invariant**: even with gang scheduling and DRA solved, placement ≠ right-sizing — clusters commonly plateau at 20–30% GPU utilization because no scheduler in this stack continuously *re-sizes* fractional allocations after initial placement; closing that gap requires a separate continuous bin-packing controller layered on top.

### 2.3 HPA/KEDA scaling decision algorithm (state machine)

```
                    metric > threshold                          metric < threshold
                    for stabilizationWindow                     for stabilizationWindow
                    (scale-up default: 0s)                      (scale-down default: 300s)
   ┌──────────┐ ─────────────────────────▶  ┌──────────┐  ◀───────────────────────────
   │  STABLE  │                              │  SCALING  │
   │ (current  │ ◀─────────────────────────  │ (compute  │
   │  replica  │      new steady state        │  desired  │
   │  count)   │                              │  replicas,│
   └──────────┘                              │  apply    │
        ▲                                     │  policy   │
        │                                     │  limits)  │
        │ replicas == 0 (KEDA only;           └──────────┘
        │ HPA floor is 1)                          │
        └────────────────── scale-to-zero ─────────┘
              (activationThreshold crossed
               downward, cooldownPeriod elapsed)
```

`desired_replicas = ceil(current_replicas × (current_metric_value / target_metric_value))` is the core HPA formula; KEDA wraps this with two additional thresholds — `activationThreshold` (governs the 0→1 transition a plain HPA cannot make) and the standard `threshold` (handed to HPA for the 1→N range). **Two signals must not be conflated**: queue depth (`vllm:num_requests_waiting`) is the correct scale-*out* trigger because it is a leading, workload-driven indicator computable in closed form (`pods_needed = queue_length / per_pod_throughput`); KV-cache fill (`vllm:kv_cache_usage_perc`) is a *saturation guard*, not a scaling signal — wiring it into KEDA instead of into per-replica concurrency limits produces oscillation because cache fill is a lagging, per-replica-state indicator, not a fleet-wide demand signal. **Root cause of thrashing**: the *asymmetric* default stabilization windows (0s up, 300s down) mean a transient cold-start dip in average CPU (new pods report artificially low utilization) can trigger an immediate scale-down, which then spikes remaining pods' load and triggers scale-up — a feedback loop that can repeat every 10–15 seconds. The documented fix is explicit `behavior.scaleUp.stabilizationWindowSeconds` (60–120s) paired with proportional (`percent`) rather than fixed-count scale-up policy.

### 2.4 Queue-based task distribution

Consumer-group semantics (Kafka, Redis Streams): each **agent type** owns a distinct consumer group on a shared topic/stream; within a group, workers auto-balance partition/entry ownership, giving `O(1)` amortized dequeue per worker and horizontal scalability bounded only by partition count. **Backpressure invariant**: "a queue does not absorb overload — it converts a fast failure into a slow one, and if unbounded, converts a slow failure into a total one." A bounded queue forces a producer to block (or receive `429`/`503` with `Retry-After`) at capacity rather than accumulating unbounded latency; for LLM-backed queues this matters more than for generic backlogs because every queued item is *billed* whether or not it is eventually discarded after a client-side timeout, so an unbounded queue is a direct financial amplifier, not just a latency one.

> ⚠️ Gap: no ratified CNCF/Kubernetes reference architecture standardizes end-to-end "agent mesh" topology (control plane + queue + inference + gateway) as of Aug 2026 — the topology in §1 synthesizes vendor-specific patterns (Kagenti, Solo.io, TrueFoundry, NVIDIA AI-Q) rather than a single ratified spec.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost formulas — infrastructure vs. token cost trade-offs

```
cost_per_run = infra_cost_per_run + token_cost_per_run

infra_cost_per_run  = (gpu_hourly_rate × avg_run_duration_hours) / concurrent_runs_per_gpu
token_cost_per_run  = (input_tokens × price_in_per_mtok + output_tokens × price_out_per_mtok) / 1e6
```

**Assumptions used below**: GPU pricing per public 2026 rate cards — Modal A100 ≈ $1.76/hr (stated range $0.59–$3.95/hr across T4/L4/A100/H100), Lambda Cloud A100-80GB $1.29/hr; token pricing at a mid-tier frontier model, $3/MTok input, $15/MTok output; a representative agent turn = 2,000 input tokens (with tool-schema tax), 500 output tokens.

| Scenario | Assumptions | Infra $/run | Token $/run | **$ per 1k runs** |
|---|---|---|---|---|
| Serverless GPU, bursty (<10 concurrent runs) | Modal A100 $1.76/hr, 8s avg run, cold start <1s (not billed) | $0.0039 | $0.0135 | **~$17.4 per 1k runs** |
| Kubernetes reserved GPU, steady (>1,000 inferences/min, 70–85% utilization) | Reserved-instance floor of $0.0003/inference per published 2026 benchmarks | $0.0003 | $0.0135 | **~$13.8 per 1k runs** (~21% cheaper than serverless at this volume) |
| Self-hosted dedicated GPU, 24/7, open-weight model (no per-token API cost) | Lambda A100 $1.29/hr → $929/mo; 500K runs/mo | $0.00186 | $0 (self-hosted inference) | **~$1.9 per 1k runs** (infra-only; requires 3–6 month payback horizon vs. on-demand at ~$2,470/mo) |
| Same self-hosted GPU, at <50K runs/mo (under-utilized) | Same $929/mo fixed floor, low volume | $0.0186 | $0 | **~$18.6 per 1k runs** (10x worse — fixed GPU cost isn't amortized at low volume) |

**The crossover is a volume/utilization function, not a fixed dollar answer.** Independent sources converge on **50–100K requests/day** or **~$5,000–$10,000/month** steady-state spend, or equivalently **>40–60% daily GPU utilization**, as the point where reserved Kubernetes capacity becomes cheaper than serverless per-invocation pricing; below that line, serverless wins purely on zero idle cost. Concurrency framing sharpens this: **<10 concurrent agent runs** → serverless; **10–50 mixed** → managed durable platforms (Temporal/Prefect) for state durability; **50+ steady** → Kubernetes for $/GPU-hour; **50+ bursty** → hybrid (managed orchestration control plane + Kubernetes worker pool). Serverless GPU offerings additionally cost **1.5×–3× more per second** than on-demand instances, and cold starts of 20–60s (weight loading + CUDA init) effectively force an always-on "warm" replica for any sub-second-response requirement — which erases the serverless cost advantage entirely for latency-sensitive tiers, independent of raw volume.

**Cost levers beyond infrastructure choice**: prompt/tool-result caching is frequently a *larger* lever than model or infra choice — Clay (350M agent executions/month) reports up to 70% cost savings from strategic prompt caching; a separate production case study reports 35%+ compute reduction from prompt + tool-result caching. Model cascading (cheap model for simple tasks, frontier model reserved for complex reasoning) is reported to yield ~60% cost savings in production agentic pipelines. `[vendor-reported, illustrative]`

### 3.2 Latency SLA targets — explicit P50/P95/P99

| Endpoint / tier | P50 | P95 | P99 | Timeout | Mitigation |
|---|---|---|---|---|---|
| Control-plane health/readiness check | 5–15ms | ≤40ms | ≤80ms | 2s | Liveness probe checks internal state only, never a downstream dependency |
| Job-submission endpoint (`POST /jobs`, accept + enqueue) | 20–60ms | ≤150ms | ≤300ms | 3s | Async accept pattern (§1) decouples this from actual agent execution time |
| Queue wait time (time in queue before a worker picks up the job) | 100ms–1s | ≤5s | ≤15s | n/a (bounded queue) | KEDA queue-depth scaling; shed load early via `429`/`503` + `Retry-After` rather than let wait time grow unbounded |
| GPU inference — time-to-first-token (TTFT) | 200–500ms | ≤1.5s | ≤3s | 10s | Warm-pool minimum replicas; avoid scale-to-zero on latency-critical inference tiers |
| GPU inference — end-to-end agent-turn latency (vLLM) | 1–3s | **≤5s** (AWS EKS scale-up trigger threshold) | ≤10s | 30s | Scale up on p95 > 5s *and* queue depth > 25 waiting/pod, not either alone |
| Full async agent run (multi-step, tool calls, streamed via SSE) | 5–30s | ≤60s | ≤120s | 300s (background task) | Resumable SSE via `last_event_id`; producer keeps running as a background task independent of client connection |
| Multi-region cross-AZ replication lag (vector DB) | 2–8s | ≤30s (alert threshold) | ≤60s | n/a | Active-active requires conflict resolution; active-passive tolerates higher lag since it's not serving live reads |

Google SRE's percentile-threshold discipline applies directly here: a single average latency figure masks unhappy tail users, so every production agent SLA should be stated as a **joint** condition (e.g., "P90 < 400ms **AND** P99 < 850ms" for control-plane/status endpoints) rather than a single number — the two thresholds catch different failure modes (typical-case regression vs. tail blowup from GC pauses, cold starts, or GPU contention).

### 3.3 Throughput and backpressure design

Capacity planning starts from `pods_needed = queue_length / per_pod_throughput`, a closed-form formula that queue-based (vs. live-metric) scaling makes tractable because queue length is directly observable and per-pod throughput is measurable offline. RabbitMQ sustains tens of thousands of msgs/sec per queue before degrading; Kafka is designed for materially higher throughput plus long retention/replay at the cost of operational complexity; SQS's pull-based polling model means cost accrues even during idle polling — a workload-mismatch trap for bursty agent traffic. The standing backpressure policy converged on across sources: **retry 3 times with exponential backoff, then park in a dead-letter queue** — more retries delays outage detection and stacks up wasted, already-billed LLM-call cost on work that will ultimately be discarded.

### 3.4 Non-functional requirements — availability, RPO/RTO, and trade-offs

**Availability / error-budget targets** (illustrative, tiered by blast radius):

| Tier | Target availability | Monthly error budget | Notes |
|---|---|---|---|
| Control plane (API server, scheduler, gateway) | 99.95% | ~21.9 min/month | Never sits in the hot path of a single request; an outage delays *new* scheduling decisions, not in-flight traffic |
| Agent API / data plane (job accept, status) | 99.9% | ~43.2 min/month | Standard SLO tier for a stateless, horizontally-scaled tier behind a load balancer |
| GPU inference tier (single region) | 99.5% | ~3.6 hr/month | Wider budget accounts for cold-start windows on scale-from-zero and gang-scheduling admission delays |
| Multi-region active-active | 99.99% | ~4.3 min/month | Requires conflict resolution + full model replication; justified only when the workload's blast-radius cost exceeds the added operational complexity |

**RPO/RTO figures**:
- **Temporal-durable Activities**: RPO ≈ 0 for any completed Activity (its result is durably recorded in Event History and never re-executed on replay); RTO ≈ time to replay Event History to the last checkpoint, typically seconds.
- **Multi-AZ failover** (single-datacenter/hardware failure): RTO 1–2 minutes, low added latency — but explicitly does **not** protect against a full regional outage (the October 2025 AWS regional outage is the cited cautionary precedent).
- **Multi-region active-passive**: RTO ≈ health-check-interval × failure-threshold (e.g., 30s interval × 3 failures ≈ 90s) — acceptable for DR but too slow for an active-active-grade SLA; RPO bounded by cross-region replication lag (≤30s per the alerting threshold in §3.2).
- **Model-weight cache (PVC)**: RPO/RTO for cold-start recovery is bounded by whether a `ReadWriteOnce` PVC (sized 2× model file size) is mounted — without it, every pod restart re-downloads full model weights (e.g., a 140GB re-download), turning what should be a seconds-scale RTO into a many-minutes one.

**Named trade-off discussions**:
1. **Autoscaling cost vs. latency**: KEDA's ability to scale GPU inference to zero replicas is the entire economic case for bursty workloads (an idle H100 bills the same as a busy one), but scale-from-zero reintroduces a 20–60s cold start that will blow through the P95 ≤ 5s / P99 ≤ 10s targets in §3.2 the moment a request lands on a cold replica. The resolution is *tiered*: latency-critical inference paths keep a minimum warm-replica floor (`activationThreshold` never reaches zero) and eat the idle cost; bursty/non-interactive batch paths scale to zero and accept cold-start latency as the cost of near-zero idle spend.
2. **Serverless vs. Kubernetes cost crossover**: below the ~50–100K req/day line, serverless wins outright on zero idle cost; above it, reserved Kubernetes capacity wins on $/GPU-hour — but this trade is not cost-only. Kubernetes reintroduces multi-minute pod-startup windows for large-model loading (up to ~21 minutes with a generous startup-probe budget) that serverless's per-request cold start does not have at the same magnitude, so the "crossover" decision also trades away serverless's finer-grained elasticity for Kubernetes's better steady-state unit economics.
3. **Multi-region active-active vs. active-passive**: active-active buys the highest availability (99.99%+) and lowest latency (every region serves live traffic) but requires conflict resolution for stateful data and full model replication — real, ongoing operational cost. Active-passive is the more common "multi-region" deployment in practice specifically because it avoids that conflict-resolution cost, accepting a ~90s RTO in exchange. A hard compliance constraint can override either choice: GDPR-scoped EU traffic must **never** route to US regions even during failover, meaning routing logic must encode geographic/regulatory constraints *ahead of* pure availability optimization — a compliant system may deliberately choose *not* to fail over to the fastest-recovering healthy region if that region is in the wrong jurisdiction.

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution in production (Temporal)

The load-bearing rule: **all non-deterministic I/O — LLM calls, tool invocations, external API/DB calls — must live in Activities, never in Workflow code.** Workflows are replayed from Event History on recovery; a direct LLM call inside Workflow code would produce a different result on replay and trigger a non-determinism error that fails the entire workflow. The Workflow is the durable brain (holds conversation state, schedules Activities, waits on results); it never calls external services directly. **Heartbeats** let long-running Activities (multi-minute tool calls, batch processing) report progress and resume from the last checkpoint after a Worker crash rather than re-running from scratch, and detect a stuck ("zombie") Activity faster than a bare execution timeout would. **Continue-As-New** is mandatory for long-running agent loops to keep Event History from growing unboundedly; every agent loop additionally needs a **hard iteration cap enforced in code**, not left to model judgment — production teams report failures from agent loops running 500+ iterations with no cap. Disable client-library retries for LLM SDKs entirely and let Temporal's Activity Retry Policy own all retry/backoff — a single configuration point, durable across crashes, with consistent observability. For multi-agent fan-out, run parallel Activities with `return_exceptions=True` (or equivalent) so a partial-failure fan-out still returns usable partial results instead of failing the whole batch.

### 4.2 Kubernetes-native resilience

**Three-probe model**, reiterating §2.1's invariant with production configuration specifics: **startup probe** delays liveness/readiness checks until slow init (model weight loading) completes — a 21-minute startup window is not unusual for large-model vLLM pods, requiring `failureThreshold: 120 × periodSeconds: 10s`; **liveness probe** must check *only* internal process health, never an external dependency, or a downstream outage causes kubelet to restart otherwise-healthy pods (self-inflicted cascading failure); **readiness probe** controls Service-endpoint traffic eligibility and *can* check dependency availability — its failure removes the pod from the load balancer without restarting it, the correct response to a transient dependency issue.

**Pod Disruption Budgets (PDBs)**: set for any Deployment with >1 replica; use `maxUnavailable: 1` or `minAvailable: 50%`; never `maxUnavailable: 0` (blocks all cluster maintenance/node drains indefinitely); set `unhealthyPodEvictionPolicy: AlwaysAllow` so a misbehaving `CrashLoopBackOff` pod can still be evicted during a node drain instead of blocking it. Layer on topology spread across ≥2 zones, pod anti-affinity, a `preStop` sleep of ≥5s (survives the Service endpoint-propagation race during rolling termination), and resource requests/limits on every container.

### 4.3 Circuit breakers, rate limiting, and fallback chains

Layered resilience stack for production LLM/agent gateways, in strict order: **rate limiting → circuit breaker → fallback chain**. Circuit-breaker sliding-window defaults converging across production sources: **failure-rate threshold 25–50%** over a **10–20 call sliding window** opens the circuit; **30–60s cooldown** before probing HALF_OPEN; **2–3 permitted probe calls** in HALF_OPEN before fully closing. Circuit state must be shared via a **distributed store (Redis)** across all gateway instances/workers/pods — e.g., 8 Gunicorn workers × 3 pods sharing one Redis-backed circuit state — so independent nodes don't each independently keep hammering a failing provider. A fallback chain should skip providers whose *own* circuit is already open rather than attempting and re-failing them (e.g., `[OpenAI, Anthropic, Gemini]` where Anthropic's circuit is open routes an OpenAI failure straight to Gemini). Only transient errors (429, 5xx) are retried with exponential backoff; permanent errors (auth/billing) are never retried. A layered rollout of circuit breakers + timeout budgets + backoff-with-jitter is reported (vendor case study) to have taken cascade failures from 847/month to 0, uptime from 94.2% to 99.97%, and MTTR from 12 minutes (manual) to 45 seconds (automatic). `[vendor-reported, treat as illustrative, not a verified industry benchmark]`

### 4.4 Failure taxonomy and idempotency

| Class | Examples | Policy |
|---|---|---|
| Transient | 429, 5xx, timeouts, DNS failures, brief GPU-scheduler admission delays | Retry with exponential backoff + jitter (3 attempts is the converged practical ceiling) |
| Permanent | 400/401/403, malformed schema, invalid tool name, auth/billing failure | Never retry — fail fast to fallback tier; retrying wastes budget with no chance of success |
| Poison-pill | A specific input that deterministically crashes the same worker/tool on every retry (e.g., a payload that always triggers the same OOM) | Detect via repeated-failure-on-identical-input hashing; quarantine to DLQ rather than retry indefinitely |

**Idempotency keys are mandatory, not optional**, on every mutating Activity (payment, notification, deletion, infra mutation) wrapped in a retryable call — without one, a network-level retry after an ambiguous timeout (the call may have actually succeeded server-side) causes double-execution, which for an irreversible action is exactly the failure class behind real-world incidents where an agent's retried call caused duplicated destructive side effects.

### 4.5 Distributed locking, multi-region/multi-AZ, and dead-letter handling

Multi-AZ handles single-datacenter/hardware failure with fast failover (1–2 min) and low added latency but does **not** protect against a full regional outage. Multi-region patterns: **active-active** (every region serves live traffic — lowest latency, highest availability, requires conflict resolution + full model replication); **active-passive** (secondary region as warm/cold standby — the most common real-world "multi-region" deployment); **hub-and-spoke** (central control plane/model registry in one region, lightweight inference spokes elsewhere — introduces a single point of failure at the hub unless the hub itself is multi-AZ). Failover engineering discipline: define RTO/RPO **before** setting the health-check interval, since the interval directly determines achievable RTO (§3.4). Dead-letter handling closes the loop on §4.4's failure taxonomy: after retry exhaustion, a failed task moves to a dead-letter stream/queue rather than being dropped, preserving it for replay once the root cause is fixed — critical for LLM-backed queues where a discarded task also represents already-spent token cost.

### 4.6 Zero-Trust MCP in production Kubernetes — protocol-specific enforcement

Generic "zero trust" name-dropping is insufficient at this layer; the 2026 production pattern is concrete and protocol-specific, built on **Istio Ambient Mode**:

- **ztunnel** (a shared, per-node Rust proxy) replaces per-pod sidecars, enforcing **L4 mTLS + SPIFFE workload identity** on every connection **without injecting anything into the agent pod itself** — this matters specifically for AI workloads because sidecar memory overhead compounds badly next to an already resource-heavy LLM inference container.
- ztunnel enforces **L4 only**. Any L7-level control — HTTP method restriction, and critically, **MCP tool-level authorization** — requires an additional **waypoint proxy** sitting at the same HBONE enforcement hop, inheriting the already-established SPIFFE identity chain rather than re-authenticating from scratch.
- The **AI-native waypoint** for this purpose is **agentgateway**: it parses the MCP protocol natively and evaluates **CEL expressions** against the *specific tool being called* — an unauthorized tool is hidden from the `tools/list` response entirely and rejected on `tools/call`, meaning the agent's own model never even observes that the tool exists, not merely that it was denied after asking.
- **Concrete enforcement recipe** applied to a namespace running agent workloads:
  1. Label the namespace `istio.io/dataplane-mode=ambient`.
  2. Apply `PeerAuthentication` in `STRICT` mTLS mode for the namespace (rejects any plaintext connection outright).
  3. Apply an L4 `AuthorizationPolicy` allow-listing traffic by workload identity (SPIFFE `spiffe://cluster.local/ns/agents/sa/worker` → `spiffe://cluster.local/ns/tools/sa/mcp-crm`, for example) — deny-by-default for everything not explicitly listed.
  4. Route all MCP traffic through an `AgentgatewayBackend` (`kind: mcp`) plus an `HTTPRoute` carrying the L7, per-tool CEL policy (e.g., `request.tool.name in ["search_docs", "get_ticket"] && request.tenant == jwt.tenant_id`).
- Every log entry at this layer carries **OTel GenAI semantic-convention attributes** — `gen_ai.provider.name`, `gen_ai.usage.input_tokens`/`output_tokens`, `mcp.method`, `mcp.session.id`, `mcp.tool.name` — plus the caller's SPIFFE identity, so an auditor can answer "which workload identity called which MCP tool, with what token cost, when" without correlating across separate logging systems.
- Net effect: the cluster network model changes from "open, then filtered at the application layer" to a **policy-defined execution graph** — agents can reach only approved LLM providers and required MCP servers; MCP servers can reach only designated data sources; everything else is blocked by default at the mesh layer, before any application code runs.

### 4.7 Kubernetes RBAC + application-level tool RBAC

**Kubernetes RBAC** binding chain: `ServiceAccount → Role → RoleBinding → Namespace` — never `ClusterRoleBinding` for agent workloads, and never rely on the `default` ServiceAccount. A two-identity separation pattern is standard: a **diagnostic** identity (`get`/`list`/`watch` only, explicitly excluding `secrets` and `pods/exec`) and a separate, narrowly-scoped **remediation** identity gated by human approval, audit logging, and a rollback path — the two are never combined into one identity. Hardening checklist: `automountServiceAccountToken: false` on pods that don't need API access; generate policy from *observed* runtime calls (`audit2rbac`, `rakkess`) rather than guessing scope up front; enforce Pod Security Admission in `restricted` mode; verify with `kubectl auth can-i` after every RBAC change; treat even read-only Secrets access as a compromise of least-privilege, since Secrets typically hold DB creds, API keys, and signing keys.

**Application-level tool RBAC** is a distinct layer sitting above Kubernetes RBAC: it governs which *tools* (not which Kubernetes API resources) an agent identity may invoke, enforced at the agentgateway waypoint (§4.6). The 2026 shift beyond static manifests for both layers: because agents dynamically chain tools, a static RBAC manifest can't capture actual runtime risk — emerging guidance (CSA MAESTRO, CNCF's March 2026 agentic standards work) points toward **SPIFFE/SVID short-lived credentials** plus **just-in-time, task-scoped permission grants**, replacing "just-in-case" standing access. This is not a hypothetical concern: one industry survey found 67% static-credential reliance among organizations running agents, and separate research found 80% of organizations report agents already acting beyond their intended scope.

### 4.8 PII filtering — detect → redact → audit

Redaction must be a **mandatory, centralized, non-optional** layer at every trust boundary, never per-agent middleware someone can forget to enable. Four canonical interception points in an agent loop: (A) ingress user prompt, (B) outgoing tool-call arguments, (C) incoming tool results, (D) outgoing final response — a single "redact only the first user message" pass is insufficient, since every subsequent hop (tool selection, tool output re-injected as context, retrieval) is a fresh chance to leak or ingest new PII. The preferred technique is **reversible pseudonymization** over blunt suppression: replace PII with realistic, contextually coherent pseudonyms scoped to a session entity map, reverting to real values only at the final response boundary (D) — this preserves multi-turn utility that suppression destroys. Detection combines regex/Luhn (structured data: SSNs, card numbers) with NER models (spaCy/BERT/Presidio) for unstructured text. Observability must be scrubbed identically: OTel span processors should persist only tokens/masks in traces, never raw PII values — this is the same discipline §1's telemetry-layer "PII-Scrubbed Trace Store" box enforces architecturally.

### 4.9 Sandbox isolation (gVisor / Kata) in Kubernetes

Three isolation tiers via Kubernetes `RuntimeClass`: **runc** (standard containers, shared host kernel — trusted workloads only) → **gVisor** (`runsc`; a user-space kernel "Sentry" intercepts syscalls before the host kernel — 20–100%+ overhead on syscall-heavy workloads, moderate trust, no added VM boot time) → **Kata Containers** (each pod gets a dedicated microVM + guest kernel via KVM — 5–15% overhead, hardware-enforced isolation, the correct default for genuinely adversarial or untrusted LLM-generated code). Kata's VM startup overhead (100–500ms) is minimized by using **Firecracker** as the VMM backend — the fastest of the QEMU/Cloud Hypervisor/Firecracker options at ~125ms boot and ~5MB memory overhead, the same backend powering AWS Lambda, E2B, and Vercel Sandbox. Google's **Agent Sandbox** (a Kubernetes CRD + Operator, backend-agnostic across gVisor and Kata) provides a declarative API purpose-built for stateful, isolated agent code-execution workloads, avoiding lock-in to a proprietary sandboxing SaaS. A practical production constraint: direct pod port-forwarding is incompatible with these secure runtimes — a dedicated **Sandbox Router** is required to route traffic into gVisor/Kata-isolated pods.

### 4.10 Auditability — immutable logs and chain-of-custody

SOC 2 (CC6.1, CC7.2, CC8.1) now explicitly extends to AI agents: an agent must be treated as a **managed identity** with a unique, non-repudiable ID bound to an owner — never a shared service account. Required audit-trail fields, converging across sources: identity binding, verbatim intent/prompt capture with timestamp, the full tool-call sequence (parameters + return values), decision rationale/reasoning chain, affected-data lineage, output sensitivity classification, and cryptographic tamper-evidence (hash-chained, append-only — e.g., S3 Object Lock in Compliance mode). PII redaction must happen **at write time**, before logs reach storage, since once PII is in a log, every downstream access to that log becomes its own data-protection concern. A tiered retention policy is standard: operational logs 30–90 days, compliance logs 1–7 years (regulatory-driven), debug logs 7–14 days. Cited compliance gap: only 38% of organizations monitor AI activity end-to-end, and just 17% track agent-to-agent interactions — a majority of 2026 enterprise AI deployments reportedly ship without complete audit trails.

---

## 5. Production Enterprise Code

The module below implements a runnable resilience layer for a FastAPI-based agent API with queue-based task dispatch: retries with exponential backoff + jitter, a circuit breaker (closed→open→half-open) guarding the LLM/inference call, a fallback chain (primary inference → secondary provider → cached/degraded response), structured JSON logging with correlation IDs, and graceful degradation on total failure. The core resilience primitives are standard-library-only and directly runnable via `python production_agent_api.py`; a FastAPI wrapper below shows how the same primitives back a real HTTP endpoint with queue-based dispatch.

```python
"""
production_agent_api.py

Runnable production resilience layer for a Kubernetes-deployed agent API:
retries w/ backoff+jitter, a circuit breaker (closed/open/half-open),
a fallback chain (primary inference -> secondary provider -> cached/
degraded response), correlation-ID structured logging, and a queue-based
job dispatcher standing in for a real Kafka/Redis Streams consumer.

Standard library only. Run directly: `python production_agent_api.py`
"""

from __future__ import annotations

import json
import logging
import queue
import random
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from collections import deque
from enum import Enum
from typing import Any, Callable, Deque, Optional


# --------------------------------------------------------------------------
# 1. Structured logging with correlation IDs
# --------------------------------------------------------------------------

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get() or "-"
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("agent_api")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"correlation_id":"%(correlation_id)s","msg":%(message)s}'
        )
    )
    handler.addFilter(CorrelationIdFilter())
    logger.handlers = [handler]
    logger.propagate = False
    return logger


log = configure_logging()


class correlation_scope:
    """Binds one correlation ID (== job_id in production) to every log
    line emitted while a job is being dispatched through the resilience
    stack -- required to trace a single agent run across queue pickup,
    retries, circuit-breaker trips, and fallback tiers (Sec 1, 4.3)."""

    def __init__(self, correlation_id: Optional[str] = None):
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self._token = None

    def __enter__(self) -> str:
        self._token = _correlation_id.set(self.correlation_id)
        return self.correlation_id

    def __exit__(self, *exc_info) -> None:
        _correlation_id.reset(self._token)


# --------------------------------------------------------------------------
# 2. Failure taxonomy (Sec 4.4)
# --------------------------------------------------------------------------

class InferenceError(Exception):
    """`transient=False` marks permanent errors (auth, malformed request,
    quota exhaustion) that must never be retried."""

    def __init__(self, message: str, transient: bool = True):
        super().__init__(message)
        self.transient = transient


# --------------------------------------------------------------------------
# 3. Exponential backoff with full jitter (Sec 4.3/4.4, layer 1)
# --------------------------------------------------------------------------

def backoff_with_full_jitter(attempt: int, base_s: float = 0.2, cap_s: float = 8.0) -> float:
    """AWS-style full jitter: sleep(random(0, min(cap, base * 2^attempt))).
    Avoids a thundering herd of simultaneously-retrying worker pods
    resynchronizing against an already-degraded provider."""
    upper_bound = min(cap_s, base_s * (2 ** attempt))
    return random.uniform(0, upper_bound)


def call_with_retry(fn: Callable[[], Any], max_attempts: int = 3,
                     base_s: float = 0.2, cap_s: float = 8.0):
    last_error: Optional[InferenceError] = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except InferenceError as exc:
            last_error = exc
            if not exc.transient:
                log.info(json.dumps({"event": "retry_aborted_permanent_error", "reason": str(exc)}))
                raise
            if attempt < max_attempts - 1:
                delay = backoff_with_full_jitter(attempt, base_s, cap_s)
                log.info(json.dumps({"event": "retry_backoff", "attempt": attempt + 1,
                                      "delay_s": round(delay, 3)}))
                time.sleep(delay)
    assert last_error is not None
    raise last_error


# --------------------------------------------------------------------------
# 4. Circuit breaker: CLOSED -> OPEN -> HALF_OPEN (Sec 4.3, layer 2)
#    In production this state is shared via Redis across gateway pods;
#    here it's process-local for a runnable, dependency-free demo.
# --------------------------------------------------------------------------

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str  # e.g. "inference:primary-vllm-cluster"
    failure_threshold_ratio: float = 0.5
    window_size: int = 10
    cooldown_s: float = 5.0
    half_open_max_probes: int = 2

    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _outcomes: Deque[bool] = field(default_factory=lambda: deque(maxlen=10), init=False)
    _opened_at: float = field(default=0.0, init=False)
    _half_open_probes_used: int = field(default=0, init=False)

    def _failure_ratio(self) -> float:
        if not self._outcomes:
            return 0.0
        return sum(1 for ok in self._outcomes if not ok) / len(self._outcomes)

    def allow_request(self) -> bool:
        if self._state == BreakerState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = BreakerState.HALF_OPEN
                self._half_open_probes_used = 0
                log.info(json.dumps({"event": "breaker_half_open", "dependency": self.name}))
            else:
                return False
        if self._state == BreakerState.HALF_OPEN:
            if self._half_open_probes_used >= self.half_open_max_probes:
                return False
            self._half_open_probes_used += 1
        return True

    def record_success(self) -> None:
        self._outcomes.append(True)
        if self._state == BreakerState.HALF_OPEN:
            self._state = BreakerState.CLOSED
            self._outcomes.clear()
            log.info(json.dumps({"event": "breaker_closed", "dependency": self.name}))

    def record_failure(self) -> None:
        self._outcomes.append(False)
        if self._state == BreakerState.HALF_OPEN:
            self._trip("half_open_probe_failed")
            return
        if len(self._outcomes) >= self.window_size and self._failure_ratio() >= self.failure_threshold_ratio:
            self._trip("failure_ratio_exceeded")

    def _trip(self, reason: str) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = time.monotonic()
        log.info(json.dumps({"event": "breaker_open", "dependency": self.name, "reason": reason,
                              "failure_ratio": round(self._failure_ratio(), 3)}))

    @property
    def state(self) -> BreakerState:
        return self._state


# --------------------------------------------------------------------------
# 5. Fallback chain: primary inference cluster -> secondary provider ->
#    cached/degraded response (Sec 4.3, layer 3)
# --------------------------------------------------------------------------

@dataclass
class InferenceDispatcher:
    primary_fn: Callable[[dict], dict]     # primary K8s-hosted vLLM cluster
    secondary_fn: Callable[[dict], dict]   # secondary region / provider
    cached_fn: Callable[[dict], Optional[dict]]  # last-known-good cache
    primary_breaker: CircuitBreaker
    secondary_breaker: CircuitBreaker

    def dispatch(self, request: dict) -> tuple[str, dict]:
        """Returns (source_tier, result). source_tier in
        {'primary','secondary','cached','degraded'} -- always logged so
        degraded-mode traffic is observable, never silently
        indistinguishable from the happy path."""

        if self.primary_breaker.allow_request():
            try:
                result = call_with_retry(lambda: self.primary_fn(request))
                self.primary_breaker.record_success()
                log.info(json.dumps({"event": "tier_success", "tier": "primary"}))
                return "primary", result
            except InferenceError:
                self.primary_breaker.record_failure()
                log.info(json.dumps({"event": "tier_failed", "tier": "primary"}))
        else:
            log.info(json.dumps({"event": "tier_skipped_breaker_open", "dependency": "primary"}))

        if self.secondary_breaker.allow_request():
            try:
                result = call_with_retry(lambda: self.secondary_fn(request), max_attempts=2)
                self.secondary_breaker.record_success()
                log.info(json.dumps({"event": "tier_success", "tier": "secondary"}))
                return "secondary", result
            except InferenceError:
                self.secondary_breaker.record_failure()
                log.info(json.dumps({"event": "tier_failed", "tier": "secondary"}))
        else:
            log.info(json.dumps({"event": "tier_skipped_breaker_open", "dependency": "secondary"}))

        cached = self.cached_fn(request)
        if cached is not None:
            log.info(json.dumps({"event": "tier_success", "tier": "cached"}))
            return "cached", cached

        log.info(json.dumps({"event": "fallback_to_degraded"}))
        return "degraded", {
            "status": "unavailable",
            "message": "Agent inference is temporarily unavailable across all regions; "
                       "please retry shortly.",
        }


# --------------------------------------------------------------------------
# 6. Queue-based task dispatch (Sec 1, 2.4) -- stands in for a real
#    Kafka/Redis Streams consumer group; same job-lifecycle semantics.
# --------------------------------------------------------------------------

class JobStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass
class AgentJobQueue:
    """Bounded queue enforcing the backpressure invariant from Sec 2.4/3.3:
    producers block (or should be told to retry) at capacity rather than
    growing an unbounded backlog of work that will be billed regardless
    of whether it's eventually consumed."""

    dispatcher: InferenceDispatcher
    max_queue_size: int = 100
    _queue: "queue.Queue[dict]" = field(init=False)
    _statuses: dict = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self):
        self._queue = queue.Queue(maxsize=self.max_queue_size)

    def submit(self, payload: dict) -> str:
        job_id = str(uuid.uuid4())
        with self._lock:
            self._statuses[job_id] = {"status": JobStatus.QUEUED.value, "result": None}
        try:
            self._queue.put_nowait({"job_id": job_id, "payload": payload})
        except queue.Full:
            with self._lock:
                self._statuses[job_id] = {"status": JobStatus.FAILED.value,
                                           "result": {"error": "queue_at_capacity"}}
            log.info(json.dumps({"event": "queue_full_reject", "job_id": job_id}))
        return job_id

    def status(self, job_id: str) -> Optional[dict]:
        with self._lock:
            return self._statuses.get(job_id)

    def worker_loop(self, poll_timeout_s: float = 0.5) -> None:
        """A single consumer; production replaces this with a
        consumer-group pool auto-balancing partitions (Sec 2.4)."""
        while True:
            try:
                job = self._queue.get(timeout=poll_timeout_s)
            except queue.Empty:
                continue
            if job is None:
                return
            job_id, payload = job["job_id"], job["payload"]
            with correlation_scope(job_id):
                with self._lock:
                    self._statuses[job_id]["status"] = JobStatus.RUNNING.value
                tier, result = self.dispatcher.dispatch(payload)
                final_status = (JobStatus.SUCCEEDED.value if tier in ("primary", "secondary", "cached")
                                 else JobStatus.DEGRADED.value)
                with self._lock:
                    self._statuses[job_id] = {"status": final_status, "tier": tier, "result": result}
                log.info(json.dumps({"event": "job_complete", "job_id": job_id,
                                      "status": final_status, "tier": tier}))
            self._queue.task_done()


# --------------------------------------------------------------------------
# Example wiring (graceful degradation end-to-end)
# --------------------------------------------------------------------------

if __name__ == "__main__":
    def flaky_primary_cluster(payload: dict) -> dict:
        if random.random() < 0.7:
            raise InferenceError("vLLM cluster 503 (GPU pool saturated)", transient=True)
        return {"answer": "primary-region response", "tokens": 128}

    def flaky_secondary_region(payload: dict) -> dict:
        if random.random() < 0.3:
            raise InferenceError("secondary region cold-starting", transient=True)
        return {"answer": "secondary-region response", "tokens": 128}

    def cached_last_known_good(payload: dict) -> Optional[dict]:
        return {"answer": "cached response (stale)", "tokens": 0}

    dispatcher = InferenceDispatcher(
        primary_fn=flaky_primary_cluster,
        secondary_fn=flaky_secondary_region,
        cached_fn=cached_last_known_good,
        primary_breaker=CircuitBreaker(name="inference:primary", window_size=5,
                                        failure_threshold_ratio=0.6, cooldown_s=2),
        secondary_breaker=CircuitBreaker(name="inference:secondary", window_size=5,
                                          failure_threshold_ratio=0.6, cooldown_s=2),
    )

    job_queue = AgentJobQueue(dispatcher=dispatcher, max_queue_size=50)
    worker_thread = threading.Thread(target=job_queue.worker_loop, daemon=True)
    worker_thread.start()

    submitted_ids = [job_queue.submit({"prompt": f"analyze ticket #{i}"}) for i in range(5)]
    time.sleep(3.0)  # let the background worker drain the queue

    for job_id in submitted_ids:
        print(json.dumps({"job_id": job_id, **job_queue.status(job_id)}))
```

**FastAPI integration** (illustrative; requires `pip install fastapi uvicorn` to actually serve — the resilience primitives above are exercised identically, just invoked from HTTP handlers instead of `__main__`):

```python
"""
main.py -- FastAPI wrapper around the resilience primitives above,
implementing the job-resource lifecycle pattern from Sec 1: accept +
enqueue, return a job_id immediately, let the client poll for status.
"""

from fastapi import FastAPI, HTTPException
from production_agent_api import (
    AgentJobQueue, InferenceDispatcher, CircuitBreaker, correlation_scope,
)
import threading

app = FastAPI(title="Agent API")

dispatcher = InferenceDispatcher(
    primary_fn=lambda p: {"answer": "ok"},        # replace with real vLLM client
    secondary_fn=lambda p: {"answer": "ok (secondary)"},
    cached_fn=lambda p: None,
    primary_breaker=CircuitBreaker(name="inference:primary"),
    secondary_breaker=CircuitBreaker(name="inference:secondary"),
)
job_queue = AgentJobQueue(dispatcher=dispatcher, max_queue_size=200)
threading.Thread(target=job_queue.worker_loop, daemon=True).start()


@app.post("/jobs", status_code=202)
def submit_job(payload: dict):
    job_id = job_queue.submit(payload)
    return {"job_id": job_id, "status_url": f"/jobs/{job_id}"}


@app.get("/jobs/{job_id}")
def get_job_status(job_id: str):
    status = job_queue.status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="job not found")
    return status


@app.get("/healthz/liveness")
def liveness():
    # Internal process health ONLY -- never check a downstream dependency
    # here (Sec 4.2's self-inflicted-cascading-failure warning).
    return {"status": "alive"}


@app.get("/healthz/readiness")
def readiness():
    # Readiness MAY check dependency availability; failure removes this
    # pod from Service routing without triggering a restart.
    breaker_ok = dispatcher.primary_breaker.state.value != "open"
    return {"status": "ready" if breaker_ok else "degraded", "breaker": dispatcher.primary_breaker.state.value}
```

**Illustrative Kubernetes manifests** implementing §4.2's probe/PDB discipline and §2.3's autoscaling behavior for this service:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agent-api
spec:
  # spec.replicas intentionally omitted -- HPA/KEDA owns replica count
  # once enabled (Sec 2.3); leaving it set fights the controller on
  # every `kubectl apply`.
  selector:
    matchLabels:
      app: agent-api
  template:
    metadata:
      labels:
        app: agent-api
    spec:
      automountServiceAccountToken: false
      serviceAccountName: agent-api-sa
      containers:
        - name: agent-api
          image: registry.internal/agent-api:1.4.0
          ports:
            - containerPort: 8000
          startupProbe:
            httpGet: { path: /healthz/liveness, port: 8000 }
            initialDelaySeconds: 5
            periodSeconds: 10
            failureThreshold: 12          # 120s budget for a lightweight API pod
          livenessProbe:
            httpGet: { path: /healthz/liveness, port: 8000 }
            periodSeconds: 10
            failureThreshold: 3
          readinessProbe:
            httpGet: { path: /healthz/readiness, port: 8000 }
            periodSeconds: 5
            failureThreshold: 2
          resources:
            requests: { cpu: "250m", memory: "256Mi" }
            limits: { cpu: "1", memory: "512Mi" }
          lifecycle:
            preStop:
              exec: { command: ["sh", "-c", "sleep 5"] }
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: DoNotSchedule
          labelSelector: { matchLabels: { app: agent-api } }
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: agent-api-pdb
spec:
  minAvailable: 50%
  unhealthyPodEvictionPolicy: AlwaysAllow
  selector:
    matchLabels: { app: agent-api }
---
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: agent-worker-scaler
spec:
  scaleTargetRef:
    name: agent-worker
  minReplicaCount: 0            # scale-to-zero (Sec 2.3, 3.4 trade-off)
  maxReplicaCount: 50
  cooldownPeriod: 300
  triggers:
    - type: redis-streams
      metadata:
        stream: agent-jobs
        consumerGroup: agent-workers
        activationLagThreshold: "1"
        lagThreshold: "5"        # ~= pods_needed = queue_length / per_pod_throughput
```

This demonstrates every required pattern together: a bounded, backpressure-respecting job queue rejects work at capacity rather than growing an unbounded backlog (§2.4/§3.3); the dispatcher's per-tier circuit breakers isolate the primary-region and secondary-region failure domains from each other, falling through to a cached/degraded response only once both are unavailable; every log line carries the job's correlation ID end-to-end from submission to completion; and the accompanying Kubernetes manifests encode the probe/PDB/topology-spread discipline and KEDA scale-to-zero configuration described in §4.2 and §2.3/§3.4 directly as infrastructure rather than leaving them as prose recommendations.

---

## 6. Architectural System Design Scenarios

### Scenario A — Regulated fintech agent platform requiring GDPR-compliant multi-region deployment

**Problem statement.** A fintech company runs a customer-facing support/underwriting agent for EU and US customers. Regulatory constraints require EU customer data and requests to never be processed or routed through US infrastructure — including during a failover — while the business simultaneously demands high availability (customer-facing, revenue-linked) and low latency (agent responses feel synchronous to the end user even though they run through the async job-resource pattern of §1). A single-region deployment was ruled out after a regional outage caused a multi-hour customer-facing incident.

**Proposed architecture.**

```
EU customer request ──▶ Global Gateway (geo-aware routing,
                          checks jurisdiction claim in JWT)
                                    │
                    ┌───────────────┴────────────────┐
                    ▼                                 ▼
        EU region (eu-west-1)                US region (us-east-1)
        - Active: full agent stack            - Active: full agent stack
          (API pods, workers, GPU              (independent copy, own
           inference, Kafka, Temporal)          Kafka/Temporal/GPU pool)
        - Multi-AZ within region for            - Multi-AZ within region
          hardware-failure resilience            for hardware-failure
                    │                            resilience
                    └───────────┬────────────────────┘
                                ▼
                   Hub: central model registry +
                   control-plane config (multi-AZ
                   itself, to avoid single point
                   of failure at the hub)
                   -- NOT in the data path for
                   live requests, only for policy/
                   model version propagation
```

Routing logic enforces the compliance constraint *before* any availability optimization: an EU-jurisdiction request is only ever routed within `eu-west-1` (multi-AZ for hardware failure, never cross-region to `us-east-1`, even if `eu-west-1` is degraded) — the business accepts a regional-outage-driven EU service disruption over a compliance violation. US traffic gets the same treatment mirrored. The hub-and-spoke element is scoped narrowly to control-plane concerns (model registry, policy config) precisely because a hub failure in the data path would create the single point of failure the architecture is designed to avoid; the hub itself is multi-AZ to close that residual risk.

**Trade-off evaluation matrix.**

| Dimension | Single-region + multi-AZ only (rejected baseline) | Multi-region active-passive (US primary, EU as compliance-mandated separate active region) | Multi-region active-active per jurisdiction (proposed) |
|---|---|---|---|
| Cost / ops complexity | Lowest — one region, one control plane | Medium — two live regions but only one serves each jurisdiction's traffic at a time within that jurisdiction; still need per-jurisdiction infra since EU can't fail to US | Highest — two fully independent, always-active stacks, each needing its own on-call/monitoring/capacity planning |
| Availability | 99.9% at best; multi-AZ protects hardware failure but not a full regional outage (§3.4/§4.5) | 99.95%+ per jurisdiction, but "passive" framing is misleading here — each jurisdiction only has *one* legally-eligible region, so there is no real passive failover target once regional capacity is exhausted | 99.99%-class per jurisdiction is achievable *within* each region's own multi-AZ posture, since cross-region failover isn't legally available anyway for either side |
| Latency | Best when healthy (single region, no cross-region hops) | Same as proposed during normal operation; no material latency difference since neither model can route cross-jurisdiction anyway | Same as active-passive in the common case — the "active-active" distinction here is about both regions being independently fully staffed and monitored, not about a cross-region traffic-sharing latency benefit |
| Compliance / security | Fails the "EU data never touches US infra" requirement the moment a regional outage forces cross-region failover | Compliant by construction, but "passive" is a misnomer risk — engineers must not build a generic active-passive runbook that assumes cross-region failover is always safe | Compliant by construction; explicit per-jurisdiction independence in the architecture itself removes the risk of an engineer building a "helpful" cross-region failover path during an incident |
| Scalability | Bounded by one region's GPU capacity | Each jurisdiction scales independently within its own region | Same as active-passive column — scalability is identical; the meaningful difference vs. that column is organizational/architectural clarity, not a technical scaling advantage |

**Decision rationale.** The determining factor is not a general availability/latency/cost trade-off but the hard regulatory constraint: any design that presents US infrastructure as a *legitimate failover target* for EU traffic — even one framed as "active-passive" — carries residual risk that an incident responder or an automated failover system routes EU traffic to US infrastructure under pressure, since generic multi-region tooling defaults to "route to the nearest healthy region." The proposed architecture removes that risk structurally by treating the two jurisdictions as independent active deployments with **no cross-region failover path configured at all**, rather than relying on policy or runbook discipline to prevent an engineer or an automated system from taking the "helpful" cross-region action during a real outage — directly mirroring §3.4's principle that compliance routing constraints must be encoded ahead of pure availability optimization, not layered on as an exception a stressed on-call engineer has to remember.

### Scenario B — Customer-support agent platform scaling from startup to enterprise traffic

**Problem statement.** A SaaS company's AI customer-support agent launched on a fully serverless stack (Cloud Run + managed LLM APIs) at low traffic (~500 requests/day, a handful of concurrent runs). Eighteen months later traffic has grown to ~150,000 requests/day with a mix of steady baseline load and sharp intra-day bursts around business hours; the serverless bill has grown non-linearly (per-invocation pricing plus 1.5–3× GPU-second premiums on any self-hosted-model workloads) and cold starts are increasingly visible to customers during burst periods. Leadership wants a plan that doesn't require a disruptive big-bang rewrite.
 
**Proposed architecture.**

```
Phase 1 (current, <10 concurrent):      Phase 2 (50-100K req/day, mixed):
  Cloud Run (serverless)                  Managed durable orchestration
  - Zero idle cost                        (Temporal Cloud) + serverless
  - Fast scale-up                         workers
  - Cold starts tolerable                 - Durable state, retry-heavy
                                             logic now cost-justified
                                           - Still no cluster to operate

Phase 3 (>100K req/day, steady baseline
+ bursty peaks) -- target state:

  ┌─────────────────────────────────────────────────────────────┐
  │  Kubernetes cluster (steady baseline capacity, reserved      │
  │  GPU nodes sized to ~70-85% avg utilization) + KEDA           │
  │  (scale-to-zero burst capacity for peak-hour overflow)        │
  │                                                                │
  │  Baseline traffic → always-warm worker pool (avoids cold-     │
  │  start latency hit on typical-hour traffic, Sec 3.4 trade-off)│
  │  Burst overflow  → KEDA-scaled additional pods, accepting     │
  │  cold-start latency only on the traffic tail that needs it    │
  └─────────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix.**

| Dimension | Stay fully serverless, scale up | Migrate everything to always-on Kubernetes | Hybrid: reserved baseline + KEDA burst overflow (proposed) |
|---|---|---|---|
| Cost / 1k runs | Grows worse than linearly past the ~50-100K req/day crossover (§3.1) — serverless per-invocation pricing plus 1.5-3x GPU-second premium never gets a volume discount the way reserved capacity does | Cheapest at steady 24/7 high utilization (~$13.8/1k runs per §3.1's worked example) but wastes money if baseline capacity is sized for peak rather than average load | Near-Kubernetes unit economics on baseline traffic (the majority of volume) plus serverless-equivalent burst economics only on the minority tail that actually needs it |
| Latency P95 | Increasingly poor during bursts — serverless cold starts (20-60s for any self-hosted-model path) become visible exactly when traffic is highest | Best and most consistent if capacity is over-provisioned for peak, but that means paying for idle GPU most of the day | Meets the ≤5s P95 target from §3.2 on baseline traffic (always-warm pool); burst-overflow traffic accepts a bounded cold-start hit, which is an explicit, monitored trade-off rather than an unplanned one |
| Ops complexity | Lowest — no cluster to operate, but "scale up" here really means hitting provider rate-limit ceilings and per-invocation cost walls with no architectural lever to pull | Highest sustained complexity — full cluster operations (upgrades, node pools, RBAC, PDBs) for a workload whose peak-to-trough ratio may not justify always-on capacity everywhere | Moderate — same cluster-ops burden as full migration, but KEDA's scale-to-zero on the burst tier means less capacity to manage at the margin, and the migration itself is incremental (Phase 1→2→3) rather than a big-bang cutover |
| Security posture | Provider-managed isolation; less direct control over network policy, mTLS enforcement, tool-level RBAC | Full control — enables the Zero-Trust MCP mesh (§4.6) and K8s+tool RBAC (§4.7) the serverless path can't natively support | Same security posture as full migration for the Kubernetes-resident majority of traffic; the model provider APIs used at the edges (if any remain serverless) still sit behind the same API-gateway-level authN/authZ from §1 |
| Scalability | Ceiling is the provider's own rate limits/quotas, not infrastructure the company controls | Effectively unbounded, gated only by GPU procurement lead time and DRA/KAI scheduling capacity (§2.2) | Same ceiling as full migration for baseline; burst tier inherits KEDA's proven scale-to-zero elasticity for the traffic shape it's best suited to (§3.4) |

**Decision rationale.** The phased hybrid is chosen because the company's traffic profile is now explicitly bimodal — a steady, predictable baseline plus sharp, less-predictable bursts — and §3.1's crossover heuristics (50-100K req/day, $5-10K/month, >40-60% daily utilization) all independently point toward Kubernetes for the baseline majority of this traffic, while the bursty minority is exactly the shape KEDA's scale-to-zero elasticity (§2.3) was designed for. A full big-bang migration to always-on Kubernetes would size reserved capacity for peak load and waste money on idle GPU-hours during trough periods — the same "idle H100 bills the same as a busy one" problem in reverse. Staying fully serverless ignores that the crossover math has already been crossed at 150K req/day and locks the company into a cost curve that gets worse, not better, as volume keeps growing. The phased rollout (serverless → managed durable orchestration → hybrid Kubernetes+KEDA) additionally avoids a disruptive rewrite: each phase reuses the previous phase's Temporal-based durable-execution investment (§4.1), and the final architecture's baseline/burst split is a capacity-allocation decision layered on top of already-durable workflow logic, not a rearchitecture of the agent's control flow itself.
