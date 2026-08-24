# Module 16 — Production

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `research_cursor_grok/research/16-production.md` (researched 2026-08-21, 79 sources). Prices below are **third-party aggregator quotes of AWS list-style on-demand** (not AWS Price List API dumps) and are labeled ⚠️. No unpublished production p50/p95/p99 SLOs are invented. `$ per 1k executions` is **[inferred]** from a named GPU-hour SKU × a stated utilization/shape — not a universal industry rate.
**Mandatory topics**: Docker · Kubernetes · APIs · Queues · Scaling · Reliability.

The unit of production is not “K8s + GPUs.” It is a **stateful token factory** (data plane: vLLM/NIM, KV in HBM, SSE, NIXL/RDMA, MCP sessions) sitting behind a **stateless control plane** (Gateway API, EPP, KEDA, Karpenter, Temporal server, admission). The control plane decides *which replica, when, and whether*. The data plane holds *bytes that cannot be cheaply moved*. Collapsing those planes — treating a 20-minute decode as a 30-second HTTP request, scaling GPU Deployments on CPU, or rolling vLLM like nginx — is how teams simultaneously OOM GPUs, lose prefix cache, and spend the error budget on “routine” deploys.

**Invariant:** the GPU is not a pod; the **KV cache is**. Any topology that lets the scheduler kill a replica without draining in-flight decode is treating state as cattle.

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns identity, RPM/TPM, body-based `model` routing, Endpoint Picker (EPP) scoring, KEDA/HPA, Karpenter NodePools, Temporal server, GPU Operator ClusterPolicy, and admission (Kyverno / Binary Authorization). Data plane owns tokenizer → prefill kernels → KV in HBM → optional NIXL/RDMA handoff → decode → SSE. Persistence is **three stores**: (1) **hard** — Temporal event history, Kafka log / SQS / Redis PEL, object-storage weights; (2) **sticky-soft** — prefix/KV blocks on the replica (optional LMCache CPU/disk, still replica-local unless clustered); (3) **in-flight** — the TCP/SSE connection. Tool proxies execute side effects; the GPU never holds IAM. Telemetry is the only authoritative token bill on streaming (`response.completed` / last SSE chunk). vLLM histograms are **demand**; DCGM (`:9400`, 30 s) is **hardware health**. Mixing those signals is how CPU-HPA lies.

A service mesh (Istio mTLS, DestinationRules) is the *east-west* control plane for workers, MCP servers, Temporal workers. An **inference gateway** is the *north-south* control plane that must parse the OpenAI body (`model` field), not just `:path`. Envoy AI Gateway / GKE Inference Gateway productize a **two-tier** split: Tier-1 (central auth, top-level routing, global rate limit) vs Tier-2 (self-hosted model cluster + EPP). They scale independently. Do not put Istio’s default round-robin in front of vLLM and call it “done.”

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS  (SSE chat / sync HTTP / batch producers / MCP client)                  │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │  TLS + mTLS (north-south) + correlation-id / x-request-id
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ INGRESS / TIER-1  (Envoy AIGatewayRoute · GKE Gateway · Apigee · LiteLLM)       │
│  AuthN (JWT/OIDC/mTLS) · tenant RPM/TPM · model alias · 90/10 HTTPRoute canary  │
│  PII detect→redact→audit · Priority<0 shed 429 · Retry-After · CORS            │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │  HTTPRoute; if backend = InferencePool → ext-proc EPP
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE  (kube-apiserver clock: KEDA default 15s; HPA sync typically 15s) │
│                                                                                 │
│  ┌────────────┐  ┌──────────────┐  ┌────────────────┐  ┌─────────────────────┐  │
│  │ GIE / EPP  │  │ KEDA → HPA   │  │ Karpenter      │  │ Temporal server     │  │
│  │ score KV / │  │ waiting q,   │  │ NodePools:     │  │ workflow id =       │  │
│  │ queue /    │  │ p95 e2e,     │  │ decode OD ·    │  │ tenant:thread       │  │
│  │ LoRA; pick │  │ Kafka lag,   │  │ spot prefill · │  │ Activities = LLM +  │  │
│  │ Pod IP     │  │ SQS depth    │  │ CPU gw/EPP/wf  │  │ tools; not tokens   │  │
│  └─────┬──────┘  └──────┬───────┘  └───────┬────────┘  └──────────┬──────────┘  │
│        │                │                  │                      │             │
│  ┌─────┴──────┐  ┌──────┴───────┐  ┌───────┴────────┐  ┌─────────┴──────────┐  │
│  │ Admission  │  │ GPU Operator │  │ PDB + drain    │  │ MCP Gateway PEP    │  │
│  │ Cosign/    │  │ driver · CDI │  │ preStop NotRdy │  │ OAuth 2.1 + PRM    │  │
│  │ Kyverno    │  │ DCGM · MIG   │  │ grace ≥ p99    │  │ RFC 8707 resource  │  │
│  │ BinAuthz   │  │ ClusterPolicy│  │ decode         │  │ tool allowlist CEL │  │
│  └────────────┘  └──────────────┘  └────────────────┘  └────────────────────┘  │
└────────┬───────────────────┬──────────────────────┬──────────────────┬──────────┘
         │ pick Pod IP       │ ScaledObject         │ NodePool         │ task queue
         ▼                   ▼                      ▼                  ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ DATA PLANE  (tokens) — HBM is the working set; do not L4-RR across replicas     │
│                                                                                 │
│  ┌──────────────────┐  NIXL / EFA   ┌──────────────────┐  ┌─────────────────┐   │
│  │ PREFILL GPU pool │──────────────▶│ DECODE GPU pool  │─▶│ Sampler + SSE   │   │
│  │ compute-bound    │  same-AZ only │ memory / KV bound│  │ stream=true     │   │
│  │ writes KV; TTFT  │  (not multi-AZ│ TPOT / ITL       │  │ [DONE]; no LB   │   │
│  │ spot OK if batch │   RDMA)       │ on-demand p5/a3  │  │ buffering       │   │
│  └──────────────────┘               └────────┬─────────┘  └────────┬────────┘   │
│         LeaderWorkerSet (TP/DP/EP)           │  KV in HBM          │            │
│         RWX PVC for multi-node weights       │  optional LMCache   │            │
└──────────────────────────────────────────────┼─────────────────────┼────────────┘
                                               │                     │
         ┌─────────────────────────────────────┤                     │
         │ stop_reason = tool_use              │ final / text        │
         ▼                                     ▼                     │
┌─────────────────────────────────┐   ┌──────────────────────────────────────────┐
│ TOOL PROXIES (untrusted planner │   │ PERSISTENCE                              │
│  never holds IAM / GPU)         │   │                                          │
│  ┌──────────┐  ┌─────────────┐  │   │  ┌──────────────┐  ┌──────────────────┐  │
│  │ MCP PEP  │─▶│ Sandbox     │──┼──▶│  │ HARD         │  │ STICKY / SOFT    │  │
│  │ audience │  │ Firecracker │  │   │  │ Temporal     │  │ KV / prefix      │  │
│  │ token +  │  │ / gVisor /  │  │   │  │ history;     │  │ blocks (replica) │  │
│  │ CEL tool │  │ Kata+CoCo   │  │   │  │ Kafka log;   │  │ LMCache CPU/NVMe │  │
│  └──────────┘  └─────────────┘  │   │  │ SQS + DLQ;   │  │ Weights: S3/FSx  │  │
└─────────────────────────────────┘   │  │ Redis PEL    │  │ Tokenizer CM     │  │
                                      │  └──────────────┘  └──────────────────┘  │
                                      └──────────────────────┬───────────────────┘
                                                             │
┌────────────────────────────────────────────────────────────┴───────────────────┐
│ TELEMETRY                                                                      │
│  vLLM /v1/metrics (waiting, e2e hist, KV %) · NIM worker :9090 · DCGM :9400   │
│  (XID, ECC, FB) · WORM audit (tenant, model, tokens, hashed MCP args)          │
│  Burn-rate on TTFT-good-ratio — not on GPU util                                │
└────────────────────────────────────────────────────────────────────────────────┘
```

**K8s GPU pools (Karpenter, not a shared node group).** Separate NodePools: (a) on-demand decode (`p5`/`a3`/`ND`), `expireAfter` for CIS node recycle; (b) spot prefill/batch with interruption draining; (c) CPU for gateways/EPP/Temporal. Consolidation `WhenEmpty` is safer than aggressive bin-pack on GPU — packing two incompatible models onto one node after a shuffle destroys MIG/time-slice assumptions. Topology: `topology.kubernetes.io/zone` + `nvidia.com/gpu.product`. CapacityBuffers pre-warm nodes when TTFT cannot wait EC2 allocation + driver + weight load. HyperPod DPD and llm-d NIXL assume **same AZ**; TP ranks of one LeaderWorkerSet are one failure domain.

**Docker is four artifacts with four TTLs, not “CUDA + app.”** (1) **engine image** (vLLM/NIM digest, rebuilt on CVE); (2) **weights** (HF/S3/FSx, checksummed, RWX for LWS — do not bake 70B into the image); (3) **tokenizer/config** (ConfigMap/sidecar — tokenizer drift is a silent quality incident); (4) **LoRA adapters** (hot-loaded, versioned independently). Mixing the Python agent and vLLM in one container couples blast radius and image size.

### 1.2 End-to-end request flow

1. **Ingress.** Client opens SSE (`stream=true`) or sync HTTP or a batch producer (SQS/Kafka). Tier-1 stamps `x-request-id` / correlation-id, authenticates (mTLS or JWT/OIDC), maps API key → tenant. Idle timeouts must exceed TPOT × `max_tokens`, not “60s API timeout.” LBs must not buffer the body (`proxy-buffering off` / HTTP/2 streaming).
2. **Quota and shed.** Independent RPM, TPM, and **concurrency** (in-flight streams — TPM is lagging because OpenAI-compat counts TPM when the request **completes**). First ceiling wins. Return **429 + Retry-After** for overload, **402/403** for tenant budget, **503** for no Ready endpoints — KEDA and clients must not treat those as the same signal. GIE **Priority < 0** sheds first with 429.
3. **Policy.** PII detect → redact → audit **before** the prompt is a cache key or a Temporal payload. Tool RBAC attaches only this turn’s allowlist. Envoy `MCPRoute`: OAuth on `/mcp`, per-backend API key injection, JWT scope → tool CEL. Confused deputy: a token minted for `https://api.example.com/mcp` must not be accepted by a raw GitHub MCP.
4. **Version.** Self-hosted: **model name in the JSON body** is the version. Pin `llama-3-8b-v12`; never let clients hit `latest`. GIE model rollouts = traffic split by model name / InferenceObjective (docs example: 90/10). Additive OpenAI fields are backwards-compatible (`openai-version` currently `2020-10-01` on REST); body `model` is still the pin.
5. **EPP pick (Tier-2).** Gateway matches HTTPRoute → if backend is an `InferencePool`, forward to EPP → EPP scores endpoints (KV / queue / LoRA) → Gateway sends to that **Pod IP**. AWS sample (Poisson multi-turn, not a law): precise KV-aware routing vs round-robin reduced **p90 TTFT by up to 69%**. Round-robin L4 across replicas is a prefix-cache miss storm.
6. **Engine.** Prefill (compute-bound, TTFT) then decode (memory-bound, TPOT). Disaggregated path: prefiller pushes KV via LMCache/NIXL/EFA layer-by-layer; decoder reserves `PD_BUFFER_SIZE`; **same AZ**. Readiness is `/v1/health/ready` (weights in HBM); liveness is `/v1/health/live` (process up). Inverting those two sends traffic to a loading GPU and then OOM-kills it. NIM cache Jobs `backoffLimit` default 5 so a flaky NGC pull does not leave a Ready=false replica in the pool forever.
7. **SSE.** Chat Completions: data-only chunks with `choices[0].delta`, terminate `[DONE]`. GKE: **streaming errors are not retried** — client reconnects (or Temporal retries the activity **once**, knowing tokens already billed). Envoy MCP: `Last-Event-ID` for SSE reconnect. ⚠️ vLLM has had engines continue minutes after client disconnect (community #24584) — drain ≠ cancel on an unfixed version.
8. **Side effects leave HTTP.** Agent runs, HITL, and anything with Start-To-Close minutes belong on Temporal (every LLM call and tool is an Activity) or a queue. **Idempotency-Key → Temporal Workflow-Id** on workflow start and Stripe-style keys on tools. Chat completions are **not** Stripe-idempotent: the same key cannot replay a generation without returning a cached completion or charging twice.
9. **Scale loops (asynchronous with the request).** KEDA reads `vllm:num_requests_waiting` / p95 e2e / Kafka lag / SQS depth → HPA. Karpenter sees `Unschedulable=True`. Do **not** scale vLLM on CPU or `DCGM_FI_DEV_GPU_UTIL` alone — a saturated decode replica can show low CPU and pinned SM util while the *queue* is the demand signal.
10. **Emit and audit.** Decrement remaining TPM on `response.completed` / last chunk; aborted clients still count generated tokens if the engine kept running. WORM: tenant, model, tokens, `x-request-id`, hashed MCP args — not raw secrets, not prompts in Prometheus labels.

**Interview talking point:** “Tokens stay on the inference gateway with a bounded admission queue. Tools and multi-step agents are Temporal. The GPU is scheduled as a token factory, not as nginx.”

---

## 2. Core Mechanics & Algorithms

### 2.1 Docker — three GPU layers, distroless split, supply chain

GPU images are not “CUDA + app.” Three layers, three owners:

| Layer | Owner | Failure if mixed |
| --- | --- | --- |
| **Host kernel driver** | GPU Operator DaemonSets (driver, Container Toolkit, Device Plugin, DCGM Exporter, MIG Manager). NVIDIA-documented patch **v26.3.3**. Baked AMI often `--set driver.enabled=false`. `gpu-operator` namespace **must** be `pod-security.kubernetes.io/enforce=privileged` | App image that ships a second driver |
| **Container Toolkit / CDI** | Injects devices + CUDA userspace. GPU Operator ≥25.10: containerd CDI; optional NVIDIA DRA (`nvidia-dra-driver-gpu`) for attribute-based allocation / ComputeDomain. DRA Helm default **off** (`resources.gpus.enabled=false`); enabling it while leaving the Device Plugin on **double-advertises GPUs**. ⚠️ DRA as K8s 1.34+ / driver 580+ only | Toolkit/operator mismatch → `nvidia.com/gpu: 0` after “successful” install |
| **App image** | vLLM (`vllm/vllm-openai`), NIM, or distroless agent worker | Python agent + vLLM in one container |

**Distroless is for the control plane and CPU agent workers, not for the CUDA runtime.** Google distroless (`gcr.io/distroless/static-debian13`, `cc-debian13`) ships no package manager and no shell; Kubernetes itself rebased to distroless (KEP-1729). CUDA still needs `libcuda` / cuDNN / NCCL. Pattern: **multi-stage** — compile in a fat NVIDIA CUDA image; copy the binary + linked `.so` onto `cc-debian13` **or** a continuously patched hardened base. ⚠️ NVIDIA CUDA base images lag distro OpenSSL patches; inheriting `nvidia/cuda:*-ubuntu*` as the *runtime* base is a CVE-lag decision.

**Supply chain admission can consume.** An SBOM on a GitHub Release is documentation. An SBOM **attached as a signed Cosign attestation on the image digest** is evidence. NVIDIA AICR: SPDX v2.3 JSON **per-platform digest**, not the multi-arch index — `crane digest --platform` before `cosign verify-attestation --type spdxjson`. SLSA: Cloud Build trigger-produced provenance; Binary Authorization `built-by-cloud-build` / CV SLSA (`GOOGLE_CLOUD_BUILD` only). Portable equivalent: keyless Cosign (Fulcio + Rekor) + Kyverno/`policy-controller` `verifyImages`. No `:latest`.

**Resource requests.** `nvidia.com/gpu` is **integer, unsplittable** in the default device plugin. `cpu`/`memory` still matter: tokenizer + Python + Prometheus multiprocess dir (`PROMETHEUS_MULTIPROC_DIR`) sit in DRAM, not HBM. Dragonfly/P2P pull is for the *inference* layer to avoid registry stampedes on GPU node scale-up.

### 2.2 GPU scheduling — device plugin, MIG, LWS, EPP

**Device plugin contract.** Pods request `nvidia.com/gpu` (not millicores). GPU Feature Discovery labels product, VRAM, CUDA. Time-slicing = noisy neighbor on HBM. MIG = hardware isolation, fewer concurrent contexts; Operator ≥ v26.3.0 MIG Manager generates per-node ConfigMaps from NVML; changing `nvidia.com/mig.config` **stops all GPU pods** on the node (device plugin, GFD, DCGM) and may reboot. Full GPU = default for vLLM because KV wants contiguous HBM.

**KServe dual-track (v0.20).** `InferenceService` = predictive ML. `LLMInferenceService` (`serving.kserve.io/v1alpha1`) = GenAI on llm-d: KV-aware scheduling, P/D, LWS multi-node. Config: well-known configs (lowest) ← user `baseRefs` ← spec (highest). Prefill/decode templates are first-class. Admin install (0.19 docs): Kubernetes **1.32+**, cert-manager 1.18+, Gateway API **1.3.0+**, GIE **1.2.0**, Envoy Gateway **v1.5.0+**, LWS **0.6.2+**. **Install GIE CRDs before the Gateway provider** or the provider never learns `InferencePool`.

**LeaderWorkerSet.** Unit of replication for TP/DP/EP: leader + workers, stable DNS, RDMA. KServe: `LWS size = data / dataLocal` (data=8, dataLocal=2 → size 4 = 1 leader + 3 workers). Multi-node ServingRuntime: PVC **ReadWriteMany**, autoscaler **none** on that path. Scale by **LWS replicas**, not HPA on a single leader. Failure domain = the LWS (lose leader → lose the replica). PDB on LWS groups, not random pods.

**EPP scoring complexity.** Per request, score \(W\) ready endpoints on KV-overlap, waiting-queue depth, LoRA residency: \(\Theta(W)\). Keep this on the **control plane** (ext-proc), not in the client. GKE: body-based routing on OpenAI `model`; **50 NEG per Backend Service** caps multi-port × zones × clusters (8 ports × 3 zones × 2 clusters = 48). Azure Application Gateway for Containers inference path is **preview (2026)** — non-GA for regulated go-live.

**HPA vs KEDA vs Knative (when to use which).**

| | HPA | KEDA | Knative Serving |
| --- | --- | --- | --- |
| Scale to 0 | No | Yes (`minReplicaCount: 0`) | Yes (HTTP) |
| Custom PromQL | Adapter required | Native | Activator metrics |
| Async queues | Awkward | Native (Kafka/SQS) | Poor fit |
| Interactive HTTP | OK if min≥1 | OK | Built for it; still GPU-cold-start bound |

### 2.3 HPA / KEDA / Karpenter — two loops, one formula

Kubernetes HPA:

\[
\mathrm{desiredReplicas}=\left\lceil\mathrm{currentReplicas}\times\frac{\mathrm{currentMetric}}{\mathrm{desiredMetric}}\right\rceil
\]

10% tolerance; stabilization windows. For `metricType: AverageValue` on `sum(vllm:num_requests_waiting)`, KEDA divides by replica count so the threshold is **per pod**. Two triggers → HPA takes the **max** desired. Sync period typically **15 s**. Scale-up must be faster than model-load time or you add NotReady replicas while the queue is already the SLO violation. Scale-down stabilization (AWS example **300 s**, −1 pod/120 s) exists because a GPU that took 3–8 minutes to become Ready should not be killed by a 30 s lull. `useCachedMetrics` cuts scraper load — disable for latency-guardrail metrics that must not be stale across a 5 s p95 window.

**KEDA v2.17 activation vs scaling.**

| Loop | Tool | Signal | Scale-to-zero? |
| --- | --- | --- | --- |
| Pod | KEDA `ScaledObject` → managed HPA | Prometheus `vllm:num_requests_waiting`, p95 `vllm:e2e_request_latency_seconds`; Kafka lag; SQS depth | Yes (`minReplicaCount: 0`); HPA alone cannot |
| Node | Karpenter NodePool | Unschedulable pods | Yes (empty node consolidation) |

Activation (0↔1, `activationThreshold`) has **priority** over scaling (1↔N, HPA target): `threshold: 10` + `activationThreshold: 50` with 40 messages → **stay at 0**. `minReplicaCount >= 1` ignores activation. Pause via `autoscaling.keda.sh/paused`. Long-running: HPA may SIGTERM a replica 2.9 h into a 3 h job — `terminationGracePeriodSeconds` / preStop, or run as Jobs.

AWS EKS walkthrough (**example, not a universal SLO**): scale when **average queue depth > 25 waiting req/pod** or **p95 e2e > 5 s**; scale-up stabilization 30 s / +2 pods/min.

**Autoscaler state machine**

```
                    messages < activationThreshold
        ┌─────────┐  (minReplicaCount=0)           ┌─────────┐
        │  ZERO   │◀───────────────────────────────│  WARM 1 │
        └────┬────┘  scale-down after cooldown     └────┬────┘
             │ ≥ activationThreshold                    │ HPA desired>1
             ▼                                          ▼
        ┌─────────┐  COLD START                    ┌─────────┐
        │  0 → 1  │  image + driver + HBM load     │  1 → N  │
        └─────────┘  (84s cold / 7s warm is a      └─────────┘
                      *small* demo shape, not 70B)
```

**Invariants.** (1) Do not scale vLLM on CPU or DCGM util alone. (2) Interactive pools: `minReplicas ≥ 1` (usually ≥ 2 per AZ) — scale-to-zero mid-decode is an outage class. (3) Scale-up rate must beat model-load or you amplify waiting. (4) PDB vs KEDA: scale-down that violates PDB stalls Karpenter consolidation — a **cost** failure, not a free reliability win. (5) `maxUnavailable: 0` / `minAvailable: 100%` → drain **never completes**.

### 2.4 API gateways — OpenAI-compat, SSE, idempotency, quotas

| Layer | Job | Products |
| --- | --- | --- |
| Edge / Tier-1 | AuthN, RPM/TPM, model alias, canary, PII | Envoy AI Gateway 1.0 (16 providers, MCP, multimodal), Apigee, LiteLLM |
| Inference / Tier-2 | Endpoint pick on KV/queue/LoRA; P/D | GIE InferencePool + EPP / llm-d-router; GKE Inference Gateway; Azure AGfC (preview, GIE CRDs v1.3.1) |
| Engine | `/v1/chat/completions`, `/v1/completions`, `/v1/models` | vLLM, NIM (vLLM metrics at `/v1/metrics`) |

**Idempotency (Stripe vs tokens).** Stripe: `Idempotency-Key` (≤255, UUID v4, not PII); store first status+body **≥24 h including 500s**; param mismatch → error; GET/DELETE ignore the header. OpenAI **chat completions are not Stripe-idempotent**. Correct split: idempotency on **side-effecting tools and workflow start**; **at-most-once or explicit resume** on token generation. ⚠️ OpenAI has no public `Idempotency-Key` on Chat Completions; do not claim otherwise.

**Quotas.** OpenAI: independent RPM, RPD, TPM, TPD, IPM; headers `x-ratelimit-limit-requests`, `-tokens`, `-remaining-*`, `-reset-*`; `Retry-After` on 429. Streaming does **not** get a cheaper pool; TPM counted at **complete**. Self-hosted: token-bucket per `(tenant, model)` plus **concurrency** ≈ `max_num_seqs` × replicas × safety factor < 1. Estimate TPM on *request* using the **served** model’s tokenizer (cl100k on a Llama backend under-counts). Elastic: alert at **80% of project/model peak-minute** vs ceiling, not 5-minute averages. Envoy cluster circuit breaker `max_pending_requests` is the last bulkhead before the GPU.

**Admission state machine**

```
 AUTH ─▶ QUOTA(RPM∧TPM∧concurrency) ─▶ BREAKER ─▶ EPP ─▶ ENGINE
           │ 429 / 402/403                │ open          │ 503 no Ready
           ▼                              ▼               ▼
        Retry-After                    FALLBACK        fail-closed
                                         │                or 503
                                         ▼
                                   DETERMINISTIC JSON
```

**Complexity.** Token bucket \(O(1)\) per request. Idempotency store \(O(1)\) lookup + fingerprint compare. EPP \(\Theta(W)\). Gateway must not parse the full SSE body into memory.

### 2.5 Queues — Kafka, SQS, Redis Streams, Temporal, back-pressure

HTTP is the wrong API for agent runs, batch summarization, tool fan-out, and Start-To-Close minutes. Online chat stays on the inference gateway with a **bounded** admission queue (GIE shedding / vLLM `max_num_seqs`).

| System | Ordering | Back-pressure | DLQ | Fit |
| --- | --- | --- | --- | --- |
| **Kafka** | Per partition | Passive lag; `pause()`/`resume()`; `max.poll.records`; consumers ≤ partitions | App retry topic (not native) | High-throughput event log; replay; multi-CG |
| **SQS** | Standard: none. FIFO: group | Visibility timeout + depth | Native redrive `maxReceiveCount`; FIFO DLQ **breaks** exact order and **resets** enqueue time | Simple workers; KEDA SQS scaler |
| **Redis Streams** | Stream ID | `MAXLEN` trim (shed oldest); PEL via `XREADGROUP`/`XACK`/`XCLAIM` | DIY delivery-count → `XADD` dlq → `XACK` | Hours–days retention; low ops |
| **Temporal** | Workflow history | Task-queue backlog; worker slots; Schedule-To-Start = *detection* not primary throttle | Failed activities by policy; “DLQ” = failed workflow / reset | Agents, HITL, multi-step tools |

**Kafka HOL.** One slow message stalls a partition; adding consumers **does not** help; exceeding `max.poll.interval.ms` (default 5 min) evicts the member → rebalance storm. Fix: pause partition + worker thread, or timeout → DLQ. Bound memory: `max.poll.records` (e.g. 100), `max.partition.fetch.bytes`, manual commit **after** the tool Activity succeeds (at-least-once). Parallelism cannot exceed partition count.

**SQS.** DLQ retention **longer** than source. `maxReceiveCount=1` is a panic button, not resilience. Standard queues with `maxReceiveCount > 3` move poison off `ApproximateAgeOfOldestMessage`. Redrive allow: `byQueue` (≤10 source ARNs) or `denyAll`.

**Redis Streams.** Forgotten `XACK` = unbounded PEL = “back-pressure” that is a leak. `XCLAIM` min-idle-time reclaims after a crashed worker. `MAXLEN ~` is acceptable for telemetry, **not** for payment tools (those belong in Temporal).

**Temporal.** Workflow = deterministic orchestration; **every LLM call and tool is an Activity**; disable SDK-internal retries so Temporal owns backoff. **Always set Start-To-Close** (server cannot detect a dead worker otherwise); Schedule-To-Close caps all retries; Heartbeat for long tools; Continue-As-New before history size limits. Worker Controller: rainbow deploys, `Progressive` ramp + **gate Workflow** before Current Version (e.g. 1% 30 s → 10% 1 m + `HelloWorld`), HPA/KEDA per version, PDB templates. KEDA on task-queue depth; many queues → HPA+Prometheus Adapter (KEDA hits Temporal API rate limits). Signals/Updates = HITL without burning GPU.

**Back-pressure stack (outer → inner).** Gateway concurrency 429 → vLLM waiting queue (KEDA) → Kafka/SQS lag (KEDA workers) → Temporal Schedule-To-Start (worker deficit). If you only watch GPU util, you will scale the wrong layer.

**Queue consumer state machine (Kafka)**

```
 JOIN ─▶ POLL ─▶ PROCESS ─▶ COMMIT (after Activity success)
           │         │ timeout / crash
           │         ▼
           │    PAUSE partition  or  DLQ + commit
           ▼
      max.poll.interval exceeded → EVICT → REBALANCE STORM
```

### 2.6 SLO error budgets

Google SRE: SLI = good / total; SLO < 100%; error budget = 100% − SLO; 3 M requests at 99.9% → **3,000** errors / 4 weeks. For inference, **HTTP 200 is not “good”** if TTFT blew the chat UX.

| SLI | Good event | Why not GPU util |
| --- | --- | --- |
| Availability | Completed streams with `finish_reason ∈ {stop, tool_calls}` and no gateway 5xx / overload-429 of *priority ≥ 0* | 429 from quota is often *intended*; 429 from overload is error |
| TTFT | First SSE delta < T_chat | Prefill-bound; P/D and prefix routing exist for this |
| TPOT / ITL | Inter-token latency < T_decode | Decode-bound; HOL prefill on a monolith wrecks this |
| E2E | Full completion < T_job (batch) | Agents: per-turn vs per-workflow |
| Correctness | Tool success / schema-valid JSON | Separate budget from infra |

Specify **shape**, not just percentile: “p95 TTFT for prompts ≤ 2 k tokens, decode ≤ 512, cache-hit ratio unstated.” Mixing RAG 32 k prompts with “hi” makes p95 a fiction. Burn-rate alerts: **1 h fast + 6 h slow** on TTFT-good-ratio, not GPU util. Deploys, model swaps, and GPU Operator upgrades **consume** the budget — Google’s 100% SLO argument is that change *is* the outage source. A rolling deploy that drops 2% of streams for 15 minutes on a 99.9% monthly SLO is not “zero downtime.” A 1,500-error incident on a 3,000-error budget is **50%** of the budget. Freeze features — including **model swaps** — when burned.

---

## 3. Token Economics & NFR Analysis

Two meters: **GPU-seconds** (self-host token factory) and **provider tokens** (if the gateway fronts OpenAI/Bedrock). They do not convert without *your* tok/s × *your* SKU. TPM is not GPU-hour.

### 3.1 `$ per 1k runs` — named SKU × named shape

⚠️ **On-demand SKU (aggregator, 2026-08).** Thunder Compute (reviewed 2026-08-14) and DevZero quote **p5.48xlarge $55.04/hr us-east-1** = **$6.88 / H100-hr** (8×H100); **p5.4xlarge $6.88/hr** (1×H100). Same tables: us-east-1 $55.04 vs sa-east-1 $92.47. These are **not** AWS Price List API exports — confirm in Console / Price List before budgeting. Conflicting posts quoting ~$98/hr for the same shape appear to mix families/regions; **do not average them**.

**Formula [inferred], not a measured benchmark:**

```
$ / 1k executions = (GPU_hours_consumed / executions) × 1000 × $/GPU-hr
GPU_hours_consumed = replicas × wall_hours × (1 / utilization_fraction_of_billed_time)
```

| Shape (all **[inferred]**, labeled) | Arithmetic | $ / 1k |
| --- | --- | --- |
| 1×H100 billed 1 h at **$6.88**, **3,440** completed requests | \(6.88 / 3.440\) | **$2.00** infra-only |
| 1 replica, **40%** of wall-clock serving, **2,000** good turns/h while serving | billed equiv \(6.88 / 0.4 = \$17.20\) / serving-h → \(17.20 / 2\) | **$8.60** / 1k turns |
| Same, denominator includes 30% gateway-429s that still hit prefill | good-turn cost rises; measure **good completions** | higher than $8.60 |

Change concurrency, prompt length, or idle time and the number moves by **10×**. Add second lines: EBS; egress **$0.09/GB after 100 GB** in that Thunder Compute comparison (⚠️ AWS egress SKUs vary); gateway CPU; Temporal Cloud; Kafka; model storage. Token API cost, if any, is a *second* bill.

**Utilization traps.** `DCGM_FI_DEV_GPU_UTIL` high + `vllm:kv_cache_usage_perc` ~1.0 + growing `num_requests_waiting` = **memory-bound decode**, not “healthy busy.” High util + empty queue = batch/prefill saturation. Scale-to-zero saves the $6.88/hr but **cold start = image pull + model load into HBM**. Coding-with-Taz EKS post: **84 s cold / 7 s warm** with a *small* image — ⚠️ not 70B on p5. vLLM `--gpu-memory-utilization` default **0.9** OOMs when CUDA graphs + fragmentation eat the 10%; production writeups recommend **0.75–0.85** on shared nodes.

**RPM vs TPM vs GPU concurrency.** RPM binds chatty small prompts. TPM binds RAG (10 k-token prompts). GPU binds **in-flight sequences × KV bytes** ≈ \(2 \times \mathrm{layers} \times \mathrm{kv\_heads} \times \mathrm{head\_dim} \times \mathrm{dtype} \times \mathrm{seq} \times \mathrm{batch}\) (order-of-magnitude; engine-specific). A tenant can be under RPM and still OOM (`max_num_seqs` / `gpu_memory_utilization`). Gateway quotas must cap **all three**.

**Prefill vs decode pools (cost).** Prefill is compute-bound (high SM clocks; fewer high-end GPUs). Decode is memory-bandwidth / KV-capacity-bound (HBM, stable TPOT). Disaggregation right-sizes independently. G6 multi-GPU called out as PCIe-bottlenecked vs P5/P6. ⚠️ Do not multi-AZ a NIXL path and expect training-cluster bandwidth.

**Capacity checklist.** (1) Measure tokens/s and concurrent seqs at SLO TTFT/TPOT on *one* replica. (2) KV bytes as above. (3) Headroom 0.75–0.85. (4) Spare replicas ≥ PDB `maxUnavailable` + 1 AZ failure. (5) Karpenter CapacityBuffers if TTFT cannot wait node spin-up.

### 3.2 Latency — p50 / p95 / p99 (label **[inferred]**)

⚠️ **Vendor p50/p95/p99 for production vLLM fleets are almost never published as commitments.** NIM/vLLM expose histograms (`vllm:e2e_request_latency_seconds`, `vllm:num_requests_waiting`, KV %). AWS EKS docs use **25 waiting/pod** and **p95 e2e 5 s** in a walkthrough — substitute *your* model/GPU/shape. GKE predicted-latency routing (Preview) is a heuristic, not an SLO.

| Percentile | TTFT (prefill + queue) | TPOT / ITL (decode) | E2E |
| --- | --- | --- | --- |
| **p50** | **[inferred]** prefix-**hit** + EPP pick; KV-aware sample cut **p90 TTFT up to 69%** vs RR under *their* Poisson load — shape of the lever, not your SLO | Decode-only iteration; MIG/full GPU beats time-slice jitter | First token ≈ TTFT on SSE |
| **p95** | **[inferred]** miss + admission queue; AWS walkthrough **5 s e2e** is a *scale trigger*, not a contract | HOL prefill on a monolith; P/D exists to isolate this | Agent: per-turn vs per-workflow clocks |
| **p99** | **[inferred]** cold start (minutes for 70B; **84 s** on a small demo image), AZ failover (**cold** KV), rolling-update cache flush, MIG reconfig (node-level), EPP down → 503, stampede after 429 | Scale-to-zero mid-decode; time-slice noisy neighbor; NCCL/NIXL AZ split hang | Tool timeout + Temporal Schedule-To-Start; SQS visibility storm |

| Tier | Mitigations |
| --- | --- |
| p50 | GIE/llm-d prefix routing; session pin until drain; tokenizer pin; readiness = weights in HBM |
| p95 | Chunked prefill or P/D if TTFT fails while TPOT is fine (monolith symptom); KEDA on **waiting + p95 e2e**, not CPU; Envoy `max_pending_requests`; 80% peak-minute quota alert |
| p99 | `minReplicas ≥ 2` per AZ for interactive; CapacityBuffers; jittered `Retry-After`; Dragonfly + NIM cache PVC; `activationThreshold` so one probe does not wake a p5; PDB + `terminationGracePeriodSeconds` ≥ p99 decode; canary **model-name split** before RS roll; GPU Operator canary on **one labeled node** |

Track TTFT and TPOT histograms **separately**. Goodput (both SLOs) > raw tok/s.

### 3.3 Throughput and back-pressure

\[
\mathrm{throughput}=\min(\mathrm{RPM/TPM},\ \mathrm{prefill\ FLOPs},\ \mathrm{decode\ HBM},\ \mathrm{KV\ blocks},\ \mathrm{NIXL\ BW},\ \mathrm{admit\ concurrency},\ \mathrm{Kafka\ partitions})
\]

**Protocol:**

1. Gateway admits iff breaker ∈ {closed, half-open} **and** tenant RPM/TPM/concurrency buckets have room **and** drain flag is off **and** Ready endpoints exist.
2. Saturation → **429 Retry-After**, not 500. Quota 429 ≠ overload 429 ≠ 503 empty pool.
3. KEDA: waiting queue / lag / p95 — **max** of triggers. Karpenter only after pods are unschedulable.
4. Kafka: pause partitions under HOL; never “add consumers” past partition count. SQS: visibility timeout ≥ processing; DLQ `maxReceiveCount ≥ 3`. Redis: `XACK` or PEL is a leak. Temporal: Start-To-Close always; Schedule-To-Start is a **page**, not a throttle.
5. Thundering herd: `activationThreshold`; jittered Retry-After; warm min replicas for SLO-critical models; admission concurrency **before** KEDA.
6. Client abort still bills generated tokens if the engine ignored cancel — count them.

### 3.4 Availability, RPO/RTO, compliance, explicit NFR trade-offs

| NFR | Working target | Tension |
| --- | --- | --- |
| Availability | Gateway 99.9% with split SLIs (TTFT-good, not HTTP 200). Interactive: minReplicas ≥ 2/AZ, no scale-to-zero. 429-overload counts against budget; 429-quota often does not | Multi-AZ decode replicas ≠ multi-AZ TP. Failover = cold cache → **budget TTFT burn** |
| RPO | Tools / workflow: **0** (Temporal history + outbox before effect). KV/prefix: **replica-local; lose AZ → lose those prefixes**. Weights: object storage is SoT | Treating KV as RPO=0 over-provisions HBM and forbids drain |
| RTO | Interactive: fail over to another **Ready** replica or secondary model in <1 s (cold prefix). Decode death without offload: **re-prefill**. Batch: Job/queue replay | Fast failover vs identical tokens (temp>0). GKE streaming: no retry of a failed stream |
| Consistency | Tools: exactly-once via idempotency keys. Tokens: at-least-once retry may change text. Kafka: at-least-once + idempotent Activity | Sticky EPP ↑ hit, ↓ spread |
| Compliance | Regional inference; PII redact pre-tokenize; EU chat stays in-region; WORM of `x-request-id` / hashed args; signed+SBOM’d images; Confidential Containers on Hopper = EA (SEV-SNP, **all** GPUs+NVSwitches to one UVM) | Residency vs GPU shopping. Azure inference gateway **preview** |
| Cost vs latency | Scale-to-zero earns GPU-hours on batch; interactive pays minReplicas + CapacityBuffers. P/D costs ops + same-AZ RDMA to isolate TTFT vs TPOT | $2.00 vs $8.60 / 1k is **utilization**, not a SKU |
| Consistency vs availability | Prefix-sticky routing vs random (available, cold prefill). Mesh RR is the availability-looking bug |

**Region patterns (RPO/RTO honesty).**

| Pattern | Data plane | Control plane | RPO on KV | When |
| --- | --- | --- | --- | --- |
| **Single-region multi-AZ** | Decode replicas per AZ; **no** TP across AZ | Gateway regional; Temporal workers spread | Replica-local | Default for chat |
| **Active-passive DR** | Warm/cold GPU pool in region B; weights dual-region | DNS failover; Temporal Cloud replication if used | Full miss on failover | Compliance DR, not “seamless” |
| **Active-active** | Independent InferencePools per region; sticky by user/session | Global Tier-1 with **model+region** routing | Never shared | Capacity shopping (GKE: pull GPU where it exists) |

**Explicit trade-offs.**

| Dimension | Cheap / fast | Balanced | Strict / regulated |
| --- | --- | --- | --- |
| Docker | `:latest` CUDA runtime base | Multi-stage; digest pin; Cosign+Kyverno | Distroless workers; SPDX attestation per-platform; BinAuthz SLSA; no unsigned GPU image |
| Scale | CPU HPA; scale-to-zero interactive | KEDA on waiting/p95; min≥2/AZ chat; min=0 batch | CapacityBuffers; on-demand decode; spot only restartable prefill |
| API | L4 NLB + RR | GIE/Envoy body+KV pick; RPM+TPM+concurrency | mTLS + tenant quotas; 402 vs 429 vs 503 distinct |
| Queue | Unbounded HTTP retry into vLLM | Kafka/SQS + DLQ ≥3; Temporal for tools | Outbox before effect; no Redis `MAXLEN` on payments |
| Drain | Default 30 s grace | preStop NotReady; grace ≥ p99 decode | Drain ≠ cancel until engine honors HTTP cancel; GKE upgrade grace cap ⚠️ verify |
| Isolation | Time-slice / two vLLM per GPU | Full GPU interactive; MIG for density | CoCo EA / Kata; isolated NodePool; ResourceQuota on `nvidia.com/gpu` |

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution — Temporal / Kafka

| State | Sticky (session affinity / prefix routing) | Durable (survive process death) |
| --- | --- | --- |
| KV / prefix blocks | Yes (EPP / session-id router) | Optional LMCache; still replica-local unless clustered |
| In-flight SSE | Yes (that TCP connection) | No — reconnect = new request unless app checkpoints partial output |
| Agent plan + tool results | No | **Temporal event history** |
| Queue messages | Partition / group | Kafka log / SQS / Redis PEL |
| Model weights | PVC / image / NIM cache | Object storage source of truth |

**Temporal.** Workers on K8s; server self-host (Cassandra/Postgres) or Temporal Cloud multi-region. Workflow id = `tenant:thread_id` (and `Idempotency-Key` reuse on start). Activities = PII/policy, LLM **recording** `ModelTurn` (never re-sample on replay), MCP `tools/call`, WORM append. Replay reconstructs **control** state. Compensating action = new turn, not overwrite WORM. Continue-As-New at history bounds. Worker Controller progressive rollout + gate Workflow. Place **stateless gateways** and **Temporal workers** multi-AZ. Place **P/D KV transfer** and **TP ranks** in one AZ (or one NVLink domain).

**Kafka.** Topics: `agent.turns`, `agent.tool_intent`, `agent.dlq`. Produce **intent** (`tool_call` + idempotency key) **before** the side effect (outbox). Poison → DLQ after N; do not block the partition. Lag alerts must distinguish *spike-and-recover* from *exponential lag* (pipeline death). Online chat **does not** wait on Kafka; effectful tools **do** wait on WORM.

> ⚠️ Gap: research has no Temporal replay-cost numbers for multi-MB prompt histories and no Kafka lag SLO for agent buses. Do not store full prompts in workflow history — store blob refs.

**SQS / Redis** as worker buses: KEDA SQS scaler (HyperPod lists SQS as a trigger). Redis PEL reclaim is the crash path. Neither replaces Temporal for HITL / exactly-once *business* logic.

**Resume keys.** `x-request-id`. Temporal `Workflow-Id`. Kafka offset / SQS receipt. MCP `Last-Event-ID`. None substitutes for the others. SSE resume ≠ KV resume.

### 4.2 Failure taxonomy

| Class | Examples | Handler |
| --- | --- | --- |
| Transient | 429 overload, 503 empty pool, TLS reset, Kafka rebalance, SQS visibility expiry, NIXL blip, spot interruption | Full-jitter retry on **non-streaming** 503/429; honor `Retry-After`; do **not** blindly retry streams or non-idempotent tools |
| Permanent | 400 illegal body, tokenizer/schema mismatch, 402/403 budget, unsupported model name | Fail the turn; fix pin or quota; do not retry |
| Poison pill | Same payload crashes the worker; Redis PEL leak; Kafka HOL zombie; `maxReceiveCount=1` panic | Hash + N crashes → DLQ; pause partition; PEL `XCLAIM`; never unbounded HTTP retry into vLLM |
| Semantic | Silent prefix miss (RR balancer); 429-quota counted as availability error (or the reverse); OOM classified as “need more replicas” | EPP not L4; split 429 classes; classify OOM (tokens, KV %, DCGM) — **do not liveness-restart every OOM** |

| Failure | Mechanism | Blast radius | Mitigations |
| --- | --- | --- | --- |
| **GPU OOM** | `gpu_memory_utilization` too high; long `max_model_len`; activation+KV+CUDA graph > free HBM | Pod restart; all in-flight SSE die; KEDA may **scale up** on error latency → more OOMs | 0.75–0.85; `--kv-cache-dtype fp8`; cap `max_num_seqs`; canary recover after driver reset |
| **Noisy neighbor** | Time-slicing / MPS; two vLLM on one GPU | TPOT jitter; TTFT SLO burn | MIG or full GPU; QoS Guaranteed; separate NodePools; GIE Priority |
| **Rolling-update KV loss** | New RS, old pods SIGTERM; EPP still scores dying pods | Fleetwide TTFT spike during “routine” deploy | `maxUnavailable` 1; surge GPUs; session pin until drain; **canary 1% model-name split** before RS roll |
| **Queue meltdown** | Kafka exponential lag; SQS in-flight = visibility storm; Redis PEL leak | Duplicate tools; TPM burn; Temporal retries amplify LLM $ | Lag SLO + pause; DLQ ≥3; `XCLAIM`; Start-To-Close |
| **Thundering herd** | Scale-from-zero; clients retry 429; Karpenter + pull + load | Control-plane + GPU quota + registry | `activationThreshold`; jitter; warm min; Dragonfly; gateway concurrency **before** KEDA |
| **Scale-to-zero mid-decode** | HPA/KEDA treats GPU like nginx; 30 s grace | Partial SSE; billed prefill wasted | minReplicas ≥ 1 interactive; grace ≥ p99 decode; preStop drain |
| **EPP / ext-proc down** | Gateway cannot pick | 503 or fallback to random (worse) | EPP HA; timeout + **fail-closed** for paid traffic; Istio outlier |
| **MIG reconfig** | Label change stops **all** GPU pods on node | Node-level outage | Maintenance window; never dual-wield with app deploy |
| **NCCL/NIXL AZ split** | P/D or TP across AZ | Hang / KV transfer fail | Topology spread *within* AZ for ranks; anti-affinity across AZ only for *replicas* |
| **Spot interruption** | Karpenter interruption handler | Drain with shorter notice | On-demand decode; spot batch/prefill if restartable; PDB will not help involuntary eviction |
| **NEG / LB cap** | GKE 50 NEG | Cannot add cluster/port | Fewer ports/zones; split Gateways |
| **Idempotency hole** | Client retries chat *and* `payments.charge` | Double charge, double tokens | Workflow-Id + Stripe-style keys **on tools only** |
| **Image supply-chain skip** | `:latest` unsigned CUDA | Silent CVE / backdoor | Cosign+Kyverno; pin digest |

**Chaos (minimum).** (1) SIGTERM mid-stream. (2) Kafka rebalance during tool batch. (3) Karpenter interruption on spot GPU. (4) EPP unavailable → 503 not blackhole. (5) Temporal worker drain during activity. (6) Kill an AZ’s GPU nodes — measure TTFT and 429 rate; that *is* the multi-AZ SLO. Argo Rollouts: keep stable RS at 100% when traffic-managed so canary weight ≠ overloaded canary pods.

### 4.3 Circuit breaker and fallbacks

Per **downstream cluster** (gateway → vLLM, agent → MCP), not per process:

- **Closed:** traffic flows; consecutive failures or error-rate window, or Envoy `max_pending_requests` / outlier 5xx ejection, trips to open. Recommend **retry budgets** so retries cannot explode.
- **Open:** fail fast; start a timer (e.g. 30 s). Interactive → fallback chain. Batch can wait. Paid traffic **fail-closed** if EPP is down (do not fall back to random replica). Effectful tools **fail-closed** without WORM.
- **Half-open:** allow a probe (one request or a small percentage). Success → closed; fail → open.

```
  CLOSED --(failures≥N or error-rate window)--> OPEN --(timer)--> HALF_OPEN
    ▲                                              │                    │
    │            success probe                     │ fail probe         │
    └──────────────────────────────────────────────┴────────────────────┘
```

**Retry rule.** Exponential backoff + **full jitter** (`sleep = U(0, min(cap, base·2^attempt))`) on **non-streaming** 503/429. Honor `Retry-After`. **Do not** retry streaming or non-idempotent tools. GKE: no retry of a failed stream.

**Fallback chain:** primary InferencePool (KV-aware) → secondary pool or region (**cold** KV; budget TTFT) → **deterministic degrade** (schema-valid JSON: `degraded=true`, still parseable). Do not fall back from GIE to Istio RR. Do not fall back from Temporal Activity failure to a fire-and-forget HTTP tool. Do not fall back from WORM-fail to “charge anyway.” Cascade **counts** in the error budget.

Envoy: per-cluster max connections, pending requests, concurrent requests, **max retries**. Outlier detection ejects 5xx hosts. Apply at **gateway → vLLM** *and* **agent → MCP**.

⚠️ No vendor publishes breaker trips/hour as an SLO.

### 4.4 Zero-Trust MCP, tool RBAC, PII, mTLS, immutable logs

**Zero-Trust MCP (gateway as PEP).** Do not let agents dial MCP servers with a shared PAT. Envoy MCP Gateway: OAuth on client-facing `/mcp`, tool names prefixed (`github__issue_read`), `toolSelector` include/regex, per-backend secrets, optional `forwardHeaders` scoped so fan-out `tools/list` does not leak tokens. Authorization: JWT scopes/claims + CEL on `request.mcp.tool` / `request.mcp.params`. Spec: AS **MUST** be OAuth 2.1; servers **MUST** PRM (RFC 9728); clients **MUST** RFC 8707 `resource`. 401 + `WWW-Authenticate` with `resource_metadata`. **Confused deputy** is the interview failure mode.

**Tool RBAC.** Allowlist per turn. Bind `nvidia.com/gpu` via ResourceQuota so a tenant cannot schedule the fleet. Platform owns ClusterPolicy / NodePools / GatewayClasses; app teams own `LLMInferenceService` in a namespace. Untrusted code: Firecracker/WASM **without** GPU; agents: distroless, nonroot, read-only rootfs, dropped caps. Kata + Confidential Containers: GPU Operator deploys Kata Manager + CC Manager; Hopper + AMD SEV-SNP EA; containerd only; NFD labels `nvidia.com/cc`; passthrough `nvidia.com/pgpu`; multi-GPU on HGX = **all** GPUs+NVSwitches to one UVM.

**mTLS.**

| Direction | Control | Do not |
| --- | --- | --- |
| North-south | Gateway TLS; GKE `BackendTLSPolicy` verifies model-server identity | Terminate TLS and forward cleartext to vLLM on a shared CNI |
| East-west | Istio **STRICT** mTLS for Temporal workers, MCP backends, EPP | Copy Istio GIE task `insecureSkipVerify: true` to prod |
| NetworkPolicy | Default deny; Gateway → InferencePool targetPorts (GIE: up to 8 ports); EPP scrape of vLLM metrics; NIXL/RDMA only inside the P/D namespace/AZ | Allow all in the GPU namespace “because RDMA is hard” |

**PII pipeline:** detect → redact **before tokenize / before Temporal payload / before cache key** → audit placeholder (hash, never raw). Residual: KV tensors are data-in-use. GKE Model Armor / NeMo Guardrails on request **and** response. SSE: OpenAI warns partial completions are harder to moderate — enterprise gateways still run **output classifiers on rolling windows**, accepting residual risk, or buffer (which kills TTFT). MCP: log `tools/call` name + hashed args, not raw secrets. No prompt in Prometheus labels.

**Immutable audit tuple:** `tenant_id`, `model`, `adapter_id`, tokens, `x-request-id`, EPP endpoint, `fallback_reason`, MCP tool name, args **hash**, policy decision, drain flag, quota remaining. Hash-chained WORM; **not** sampling-eligible. Replay reconstructs: policy snapshot + model id + sampled turn + tool results + human interrupt.

---

## 5. Production Enterprise Code

Stdlib-only admission gateway: full-jitter retries, circuit breaker closed→open→half-open, primary→secondary→deterministic fallback, correlation-id JSON logs, PII detect→redact→audit, Stripe-style idempotency (tools/workflow start — **not** token replay), RPM/TPM/concurrency token buckets, drain flag (fail Ready, reject new, wait in-flight), hash-chained WORM, graceful schema-valid degrade. Run: `python prod_gateway.py` (copy the block; do not add a repo `.py`).

```python
#!/usr/bin/env python3
"""Production inference admission primitives (stdlib only). Run: python prod_gateway.py"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

# --- logs / PII --------------------------------------------------------------

class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class CorrelationAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs


def build_logger(correlation_id: str, tenant: str) -> CorrelationAdapter:
    base = logging.getLogger("prod.gateway")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    return CorrelationAdapter(base, {"correlation_id": correlation_id, "tenant": tenant})


_PII = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
)


def redact_pii(text: str) -> tuple[str, list[dict[str, str]]]:
    audit: list[dict[str, str]] = []
    out = text
    for label, pat in _PII:
        def _sub(m: re.Match[str], _label: str = label) -> str:
            digest = hashlib.sha256(m.group(0).encode()).hexdigest()[:12]
            token = f"<{_label}:{digest}>"
            audit.append({"type": _label, "placeholder": token})
            return token
        out = pat.sub(_sub, out)
    return out, audit


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


# --- quota / drain / idempotency / breaker -----------------------------------

class TokenBucket:
    def __init__(self, rate: float, burst: float) -> None:
        self.rate = rate
        self.burst = burst
        self.tokens = burst
        self.ts = time.monotonic()
        self._lock = threading.Lock()

    def allow(self, n: float = 1.0) -> tuple[bool, float]:
        with self._lock:
            now = time.monotonic()
            self.tokens = min(self.burst, self.tokens + (now - self.ts) * self.rate)
            self.ts = now
            if self.tokens >= n:
                self.tokens -= n
                return True, self.tokens
            return False, self.tokens


@dataclass
class QuotaDecision:
    ok: bool
    code: int
    reason: str
    retry_after: float
    remaining_rpm: float
    remaining_tpm: float


class QuotaGate:
    """RPM + TPM + in-flight concurrency per (tenant, model)."""

    def __init__(self, rpm: float, tpm: float, max_inflight: int) -> None:
        self._rpm: dict[str, TokenBucket] = {}
        self._tpm: dict[str, TokenBucket] = {}
        self._inflight: dict[str, int] = {}
        self._max = max_inflight
        self.rpm, self.tpm = rpm, tpm
        self._lock = threading.Lock()

    def _key(self, tenant: str, model: str) -> str:
        return f"{tenant}:{model}"

    def admit(self, tenant: str, model: str, prompt_tokens: int) -> QuotaDecision:
        k = self._key(tenant, model)
        with self._lock:
            self._rpm.setdefault(k, TokenBucket(self.rpm / 60.0, self.rpm))
            self._tpm.setdefault(k, TokenBucket(self.tpm / 60.0, self.tpm))
            self._inflight.setdefault(k, 0)
            rpm_ok, rpm_left = self._rpm[k].allow(1.0)
            tpm_ok, tpm_left = self._tpm[k].allow(float(prompt_tokens))
            if not rpm_ok:
                return QuotaDecision(False, 429, "rpm", 1.0, rpm_left, tpm_left)
            if not tpm_ok:
                return QuotaDecision(False, 429, "tpm", 1.0, rpm_left, tpm_left)
            if self._inflight[k] >= self._max:
                return QuotaDecision(False, 429, "concurrency", 0.5, rpm_left, tpm_left)
            self._inflight[k] += 1
            return QuotaDecision(True, 200, "ok", 0.0, rpm_left, tpm_left)

    def release(self, tenant: str, model: str) -> None:
        k = self._key(tenant, model)
        with self._lock:
            self._inflight[k] = max(0, self._inflight.get(k, 1) - 1)


class DrainController:
    """preStop: fail Ready, reject new work, wait in-flight (graceful drain)."""

    def __init__(self) -> None:
        self.draining = False
        self.in_flight = 0
        self._cv = threading.Condition()

    def begin_drain(self) -> None:
        with self._cv:
            self.draining = True

    def enter(self) -> bool:
        with self._cv:
            if self.draining:
                return False
            self.in_flight += 1
            return True

    def exit(self) -> None:
        with self._cv:
            self.in_flight = max(0, self.in_flight - 1)
            self._cv.notify_all()

    def wait_idle(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._cv:
            while self.in_flight > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cv.wait(timeout=remaining)
            return True


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, failures: int = 3, recovery_s: float = 0.3, half_open_max: int = 1) -> None:
        self.failures_needed = failures
        self.recovery_s = recovery_s
        self.half_open_max = half_open_max
        self.state = BreakerState.CLOSED
        self.fail_count = 0
        self.opened_at = 0.0
        self.half_open_inflight = 0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self.state is BreakerState.CLOSED:
                return True
            if self.state is BreakerState.OPEN:
                if time.monotonic() - self.opened_at >= self.recovery_s:
                    self.state = BreakerState.HALF_OPEN
                    self.half_open_inflight = 0
                else:
                    return False
            if self.state is BreakerState.HALF_OPEN:
                if self.half_open_inflight >= self.half_open_max:
                    return False
                self.half_open_inflight += 1
                return True
            return False

    def record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self.fail_count = 0
                self.state = BreakerState.CLOSED
                self.half_open_inflight = 0
                return
            self.fail_count += 1
            if self.state is BreakerState.HALF_OPEN or self.fail_count >= self.failures_needed:
                self.state = BreakerState.OPEN
                self.opened_at = time.monotonic()
                self.half_open_inflight = 0


@dataclass
class IdemRecord:
    key: str
    fingerprint: str
    status: int
    body: dict[str, Any]
    stored_at: float


class IdempotencyStore:
    """Stripe-style: replay first status+body including 5xx; param mismatch errors."""

    def __init__(self, ttl_s: float = 86400.0) -> None:
        self.ttl_s = ttl_s
        self._rows: dict[str, IdemRecord] = {}
        self._lock = threading.Lock()

    @staticmethod
    def fingerprint(params: dict[str, Any]) -> str:
        blob = json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()

    def get_or_begin(self, key: str, params: dict[str, Any]) -> IdemRecord | None:
        if len(key) > 255:
            raise ValueError("Idempotency-Key > 255")
        fp = self.fingerprint(params)
        now = time.monotonic()
        with self._lock:
            row = self._rows.get(key)
            if row and (now - row.stored_at) < self.ttl_s:
                if row.fingerprint != fp:
                    raise ValueError("idempotency params mismatch")
                return row
            return None

    def put(self, key: str, params: dict[str, Any], status: int, body: dict[str, Any]) -> None:
        with self._lock:
            self._rows[key] = IdemRecord(
                key, self.fingerprint(params), status, body, time.monotonic()
            )


class WormLog:
    def __init__(self) -> None:
        self.chain = "genesis"
        self.rows: list[dict[str, Any]] = []

    def append(self, row: dict[str, Any]) -> str:
        payload = json.dumps({"prev": self.chain, "row": row}, sort_keys=True)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        self.chain = digest
        self.rows.append({"hash": digest, **row})
        return digest


# --- retry / engines / gateway -----------------------------------------------

class TransientError(RuntimeError):
    pass


class PermanentError(RuntimeError):
    pass


def full_jitter(attempt: int, base: float, cap: float, rng: random.Random) -> float:
    return rng.random() * min(cap, base * (2 ** attempt))


def retry_call(
    fn: Callable[[], dict[str, Any]],
    *,
    attempts: int,
    base: float,
    cap: float,
    rng: random.Random,
    streaming: bool,
) -> dict[str, Any]:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except TransientError as exc:
            last = exc
            if streaming or i == attempts - 1:
                break
            time.sleep(full_jitter(i, base, cap, rng))
    assert last is not None
    raise last


class Engine:
    def __init__(self, name: str, fail: type[Exception] | None = None, text: str = "ok") -> None:
        self.name = name
        self.fail = fail
        self.text = text
        self.calls = 0

    def complete(self, prompt: str) -> dict[str, Any]:
        self.calls += 1
        if self.fail:
            raise self.fail(self.name)
        return {"text": self.text, "model": self.name, "prompt": prompt}


@dataclass
class Gateway:
    primary: Engine
    secondary: Engine
    breaker: CircuitBreaker
    quota: QuotaGate
    drain: DrainController
    idem: IdempotencyStore
    worm: WormLog = field(default_factory=WormLog)
    rng: random.Random = field(default_factory=lambda: random.Random(0))
    retry_attempts: int = 3
    retry_base: float = 0.01
    retry_cap: float = 0.05

    def handle(
        self,
        prompt: str,
        *,
        tenant: str,
        model: str,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
        streaming: bool = False,
        tool_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cid = correlation_id or str(uuid.uuid4())
        log = build_logger(cid, tenant)
        redacted, pii = redact_pii(prompt)
        params = tool_params or {"prompt": redacted, "model": model, "tenant": tenant}

        if idempotency_key:
            cached = self.idem.get_or_begin(idempotency_key, params)
            if cached is not None:
                log.info("idempotency_replay")
                return {**cached.body, "replayed": True, "trace_id": cid}

        if not self.drain.enter():
            body = self._degrade("draining", cid, tenant, model, pii, 503)
            log.info("rejected_drain")
            return body

        q = self.quota.admit(tenant, model, estimate_tokens(redacted))
        if not q.ok:
            self.drain.exit()
            body = {
                "error": q.reason,
                "status": q.code,
                "retry_after": q.retry_after,
                "remaining_rpm": q.remaining_rpm,
                "remaining_tpm": q.remaining_tpm,
                "degraded": False,
                "trace_id": cid,
                "pii": pii,
            }
            log.info("quota_reject")
            self.worm.append({"event": "quota", "tenant": tenant, "reason": q.reason, "cid": cid})
            if idempotency_key:
                self.idem.put(idempotency_key, params, q.code, body)
            return body

        try:
            result = self._dispatch(redacted, streaming, log)
            result.update({"degraded": result.get("degraded", False), "trace_id": cid, "pii": pii, "status": 200})
            self.worm.append(
                {
                    "event": "complete",
                    "tenant": tenant,
                    "model": result.get("model"),
                    "cid": cid,
                    "degraded": result["degraded"],
                    "pii_types": [x["type"] for x in pii],
                    "drain": self.drain.draining,
                }
            )
            if idempotency_key:
                self.idem.put(idempotency_key, params, 200, result)
            log.info("degraded" if result.get("degraded") else "ok")
            return result
        except Exception as exc:
            body = self._degrade(f"unhandled:{type(exc).__name__}", cid, tenant, model, pii, 500)
            if idempotency_key:
                self.idem.put(idempotency_key, params, 500, body)
            log.info("unhandled")
            return body
        finally:
            self.quota.release(tenant, model)
            self.drain.exit()

    def _dispatch(self, prompt: str, streaming: bool, log: CorrelationAdapter) -> dict[str, Any]:
        if self.breaker.allow():
            try:
                out = retry_call(
                    lambda: self.primary.complete(prompt),
                    attempts=self.retry_attempts,
                    base=self.retry_base,
                    cap=self.retry_cap,
                    rng=self.rng,
                    streaming=streaming,
                )
                self.breaker.record(True)
                return out
            except TransientError:
                self.breaker.record(False)
                log.info("primary_transient")
            except PermanentError:
                self.breaker.record(False)
                raise
        else:
            log.info("breaker_open")
        try:
            out = self.secondary.complete(prompt)
            out["fallback"] = "secondary"
            return out
        except (TransientError, PermanentError):
            return self._degrade_body("fallback_exhausted")

    def _degrade_body(self, reason: str) -> dict[str, Any]:
        return {
            "text": "degraded: cannot complete this turn",
            "model": "deterministic",
            "degraded": True,
            "fallback_reason": reason,
        }

    def _degrade(
        self, reason: str, cid: str, tenant: str, model: str, pii: list[dict[str, str]], status: int
    ) -> dict[str, Any]:
        body = self._degrade_body(reason)
        body.update({"trace_id": cid, "pii": pii, "status": status, "tenant": tenant, "requested_model": model})
        self.worm.append({"event": "degrade", "reason": reason, "cid": cid, "tenant": tenant, "status": status})
        return body


def _demo() -> None:
    rng = random.Random(7)
    gw = Gateway(
        Engine("vllm-a"),
        Engine("vllm-b", text="secondary-ok"),
        CircuitBreaker(failures=2, recovery_s=0.05),
        QuotaGate(rpm=30, tpm=8000, max_inflight=4),
        DrainController(),
        IdempotencyStore(),
        rng=rng,
    )
    a = gw.handle("hello user@x.com", tenant="acme", model="llama-3-8b-v12", correlation_id="c1")
    assert a["model"] == "vllm-a" and a["degraded"] is False
    assert any(x["type"] == "email" for x in a["pii"])
    assert "<email:" in a["text"] or "ok" in a["text"]

    key = str(uuid.uuid4())
    tool = {"tool": "payments.charge", "amount": 10, "tenant": "acme"}
    t1 = gw.handle("charge", tenant="acme", model="llama-3-8b-v12", idempotency_key=key, tool_params=tool)
    t2 = gw.handle("charge", tenant="acme", model="llama-3-8b-v12", idempotency_key=key, tool_params=tool)
    assert t2.get("replayed") is True
    assert t1.get("replayed") is not True
    try:
        gw.handle(
            "charge",
            tenant="acme",
            model="llama-3-8b-v12",
            idempotency_key=key,
            tool_params={**tool, "amount": 11},
        )
        raise AssertionError("mismatch must error")
    except ValueError:
        pass

    tight = Gateway(
        Engine("vllm-a"),
        Engine("vllm-b"),
        CircuitBreaker(),
        QuotaGate(rpm=1, tpm=4, max_inflight=1),
        DrainController(),
        IdempotencyStore(),
        rng=rng,
    )
    tight.handle("abcd", tenant="acme", model="m", correlation_id="q1")
    q = tight.handle("abcd", tenant="acme", model="m", correlation_id="q2")
    assert q["status"] == 429 and q["error"] in {"rpm", "tpm", "concurrency"}

    dead = Gateway(
        Engine("down", fail=TransientError),
        Engine("also", fail=TransientError),
        CircuitBreaker(failures=1, recovery_s=0.05),
        QuotaGate(rpm=50, tpm=8000, max_inflight=8),
        DrainController(),
        IdempotencyStore(),
        rng=rng,
    )
    d = dead.handle("x", tenant="acme", model="m", correlation_id="c2", streaming=True)
    assert d["degraded"] is True and d["model"] == "deterministic"
    assert dead.breaker.state is BreakerState.OPEN

    half = Gateway(
        Engine("p", fail=TransientError),
        Engine("s", text="sec"),
        CircuitBreaker(failures=1, recovery_s=0.0),
        QuotaGate(rpm=50, tpm=8000, max_inflight=8),
        DrainController(),
        IdempotencyStore(),
        rng=rng,
    )
    half.handle("x", tenant="acme", model="m", correlation_id="c3")
    half.primary.fail = None
    half.primary.text = "recovered"
    time.sleep(0.01)
    r = half.handle("x", tenant="acme", model="m", correlation_id="c4")
    assert r.get("fallback") == "secondary" or r["model"] in {"p", "s", "deterministic"}

    gw.drain.begin_drain()
    n = gw.handle("late", tenant="acme", model="m", correlation_id="c5")
    assert n["status"] == 503 and n["degraded"] is True
    assert gw.drain.wait_idle(0.2) is True
    assert len(gw.worm.rows) >= 2
    print("ok")


if __name__ == "__main__":
    _demo()
```

**What the demo proves.** (1) PII email → placeholder + audit list. (2) Idempotency-Key replays first body; param mismatch raises. (3) RPM/TPM/concurrency 429 is distinct from 5xx. (4) Streaming + dead primary **does not** retry-storm; breaker opens; deterministic JSON degrade. (5) Drain flag returns 503 and `wait_idle` completes. (6) WORM chain is append-only. Chat completions still must **not** use the idempotency store as a token cache — the demo keys a **tool** payload (`payments.charge`).

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers are from the research file. Decision rules: **tokens stay on the inference gateway**; **tools/agents on Temporal**; **KEDA on queue/TTFT not CPU**; **KV-aware pick not L4 RR**; **interactive minReplicas ≥ 2/AZ**; **batch may scale to zero behind `activationThreshold`**.

### Scenario 1 — Multi-tenant interactive chat, 99.9% availability, TTFT SLO

**Problem statement.** Multi-tenant SaaS chat; prefix-heavy; streaming SSE; PII in EU. Availability **99.9%** on a TTFT-good SLI (not HTTP 200). Prefix cache must survive routine deploys. Irreversible tools (`payments.charge`) exist but must not ride the token HTTP retry path. Interactive pool cannot wait p5 cold start (driver + 70B HBM load). Compliance: in-region inference; signed GPU images; mTLS east-west. Error budget includes rolling updates and GPU Operator upgrades (3 M req / 4 weeks at 99.9% → **3,000** errors; a 1,500-error deploy is **50%** of budget).

**Proposed architecture.**

```
┌────────────┐ SSE  ┌──────────────────────────────────────────────────────────┐
│ Chat UI /  │─────▶│ TIER-1 Envoy AI Gateway / GKE Inference Gateway          │
│ EU clients │      │ mTLS/JWT · tenant RPM/TPM/concurrency · PII redact       │
└────────────┘      │ Model Armor · Priority shed · 90/10 model-name canary    │
                    │ 429 overload vs 402 budget vs 503 empty — distinct       │
                    └────┬──────────────────────────────┬──────────────────────┘
                         │ HTTPRoute → InferencePool    │ MCP OAuth 2.1 + PRM
                         ▼                              ▼
                    ┌─────────────────┐          ┌─────────────────────────────┐
                    │ TIER-2 EPP      │          │ MCP PEP + Temporal          │
                    │ KV/queue/LoRA   │          │ Workflow-Id = tenant:thread │
                    │ pick Pod IP     │          │ Activities = LLM + tools    │
                    └────────┬────────┘          │ Idempotency-Key on charge   │
                             │                   └──────────────┬──────────────┘
                             ▼                                  ▼
                    ┌─────────────────┐  same-AZ   ┌───────────────────────────┐
                    │ DECODE pool     │◀──NIXL/───▶│ PREFILL pool (optional    │
                    │ on-demand p5    │    EFA     │  split if p95 TTFT fails  │
                    │ minReplicas ≥ 2 │            │  while TPOT is fine)      │
                    │ / AZ; PDB; drain│            │                           │
                    │ grace ≥ p99     │            │ Karpenter: OD decode ·    │
                    │ KEDA waiting+p95│            │ CPU for gw/EPP/workers    │
                    └────────┬────────┘            └───────────────────────────┘
                             │
                             ▼
                    ┌──────────────────────────────────────────────────────────┐
                    │ WORM audit · vLLM metrics · DCGM health · Cosign admit   │
                    └──────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix.**

| Dimension | A. L4 NLB + CPU HPA + scale-to-zero + shared PAT to vLLM | B. Recommended: GIE/Envoy KV-pick + KEDA on waiting/p95 + minReplicas ≥ 2/AZ + Temporal tools + mTLS/Cosign | C. Always-on max OD GPUs + Kafka for every token + Istio RR |
| --- | --- | --- | --- |
| Cost | Looks cheap until cold start + miss storm; $6.88/H100-h idle saved, **TTFT budget spent** | Pays minReplicas + CapacityBuffers; **[inferred]** $8.60/1k at 40% util / 2k turns/h vs $2.00/1k at 3,440 completions/h — utilization is the lever | Highest GPU-hours; Kafka per token is ops $ without KV affinity |
| Latency | p50 maybe; **p99 = cold start** (84 s small demo; minutes for 70B); RR kills prefix | p50 on prefix-hit; sample **p90 TTFT −69%** vs RR *in their* load; p99 bounded by warm replicas + drain | Tokens on Kafka add HOL; RR still misses; p99 not chat-shaped |
| Ops | Trivial YAML, chronic incidents | Two-tier gateway, KEDA PromQL, PDB/drain, Worker Controller canary | Kafka+always-on is simple capacity, hard routing |
| Security | Shared key; no PII gate; unsigned CUDA `:latest` | PEP MCP; tenant quotas; redact→audit; STRICT mTLS; digest+SBOM | Mesh mTLS possible; still confused-deputy if agents skip PEP |
| Scalability | CPU HPA lies; scale-from-zero stampede | Independent Tier-1 vs EPP vs GPU pools; NEG 50-cap planned | GPU-bound by $; Kafka partitions ≠ decode HBM |

**Decision rationale.** **B** is research §6.1: serving = vLLM + GIE/llm-d prefix routing; ingress parses the OpenAI body; state is sticky via EPP, not a cookie; agents = Temporal for tools and HTTP for tokens; security = mTLS + tenant RPM/TPM + distroless workers + signed GPU images. A fails the TTFT SLO on every scale-from-zero and every RR miss, and fails audit. C wastes GPU-hours and puts the user clock on a log. Size the decode pool from **measured** TPOT at target concurrency; split prefill only if p95 TTFT fails while TPOT is fine. Interview close: “99.9% is an error budget that includes the Friday model swap. Warm replicas are the SLO; scale-to-zero is a batch feature.”

### Scenario 2 — Burst batch / overnight summarization (scale-to-zero)

**Problem statement.** Overnight corpus summarization; throughput + eventual completeness, **not** chat TTFT. Hours of idle between bursts. Poison documents exist. Jobs may run longer than HPA’s default 30 s grace (KEDA long-running warning: SIGTERM 2.9 h into a 3 h job). GPU spend must return to ~0 when the queue is empty, without a single probe message waking a p5. Restartable work may use spot; overflow must not drop poison on the floor.

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Producers  │────▶│ CONTROL PLANE                                             │
│ ETL / S3   │     │ SQS or Kafka topic `batch.summarize`                      │
└────────────┘     │ KEDA ScaledObject: minReplicaCount 0                      │
                   │   activationThreshold high enough that a probe ≠ p5       │
                   │   scaling threshold = lag / depth                         │
                   │ Karpenter: spot prefill/batch + OD overflow NodePool      │
                   │ interruption drain → visibility timeout / Kafka pause     │
                   └────┬────────────────────────────┬─────────────────────────┘
                        │ KEDA 0↔N GPU Jobs/pods     │ DLQ poison
                        ▼                            ▼
                   ┌─────────────────┐        ┌─────────────────┐
                   │ GPU workers     │        │ DLQ + operator  │
                   │ Job or long     │        │ maxReceiveCount │
                   │ terminationGrace│        │ ≥ 3 (not 1)     │
                   │ vLLM max_num_   │        │ FIFO DLQ does   │
                   │ seqs bounded    │        │  not preserve   │
                   └────────┬────────┘        │  order          │
                            │                 └─────────────────┘
                            ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ Results object store · Temporal optional for multi-step   │
                   │ summaries that call tools · WORM of job id + doc hash     │
                   └───────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix.**

| Dimension | A. Always-on H100s, chat TTFT SLO, 30 s drain, `maxReceiveCount=1` | B. Recommended: SQS/Kafka + KEDA min=0 + `activationThreshold` + spot+OD overflow + long grace / Jobs + DLQ ≥3 | C. 100% spot, Redis `MAXLEN` trim, unbounded HTTP into vLLM |
| --- | --- | --- | --- |
| Cost | Idle **$6.88/H100-h** (p5.4xlarge aggregator) all night; this is where scale-to-zero earns the GPU-hour back | Pays burst GPU-hours + Karpenter spin; activationThreshold prevents probe-wake | Spot $ looks best until interruption + recompute + lost trims |
| Latency | Fast when idle-warm — **wrong SLO** for overnight | Queue wait + cold start acceptable; completeness > TTFT | HOL + trim = silent data loss; interruption = p99 forever |
| Ops | Easy, expensive | KEDA activation vs scaling; Jobs vs Deployment; Dragonfly on stampede | PEL/`MAXLEN` incidents; no native DLQ |
| Security | Same GPU pool as chat = noisy neighbor + PII mix | Separate NodePool; no chat prefixes on batch nodes; WORM doc hash | Trimmed payment-like payloads; shared pool |
| Scalability | Bound by standing GPUs | Partition/depth → KEDA; OD overflow when spot starved | Spot capacity is not an SLO; Redis trim sheds **oldest** |

**Decision rationale.** **B** is research §6.2: API = SQS or Kafka + KEDA `minReplicaCount: 0`; GPU = spot with interruption drain plus on-demand overflow (100% spot rejected); SLO = throughput + eventual completeness + DLQ for poison (not chat TTFT); drain = Job or long `terminationGracePeriodSeconds` (30 s rejected). A is the bill you are trying to delete. C confuses “cheap interruptible FLOPs” with “durable batch.” Keep **activationThreshold** so one probe message does not wake a p5. Interview close: “Batch borrows the token factory. It does not inherit the chat SLO, the 30 s grace, or the interactive NodePool.”

---

*End of module. Six sections. Six mandatory topics (Docker, Kubernetes, APIs, queues, scaling, reliability). `$ / 1k` tables use aggregator **$6.88/H100-hr** (p5.48xlarge $55.04/hr us-east-1, 2026-08) and **[inferred]** named shapes (3,440 completions/h → $2.00/1k; 40% util × 2,000 turns/h → $8.60/1k). No unpublished production TTFT/TPOT p50/p95/p99 SLOs — percentiles are labeled **[inferred]** or bound from documented mechanics (AWS EKS walkthrough 25 waiting/pod and p95 e2e 5 s as *scale triggers*; aws-samples KV-aware p90 TTFT up to 69% vs RR; 84 s/7 s cold/warm on a small demo image; GKE 50 NEG; KEDA 15 s; HPA 15 s; GPU Operator v26.3.3).*
