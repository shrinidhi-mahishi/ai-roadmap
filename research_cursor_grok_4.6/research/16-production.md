# Research: Production
**Date researched**: 2026-08-21
**Sources consulted**: 79

Scope: **Docker** (inference/agent images, GPU containers, distroless, supply chain, SBOM), **Kubernetes** (GPU Operator, KEDA, HPA, Karpenter, vLLM on K8s, inference gateways), **APIs** (OpenAI-compat gateways, auth, versioning, SSE, idempotency, quotas), **queues** (Kafka, SQS, Redis Streams, Temporal, back-pressure, DLQ), **scaling** (prefill/decode pools, scale-to-zero, multi-region, capacity planning), **reliability** (SLOs, error budgets, chaos, multi-AZ, graceful drain, canary). Primary vendors: Kubernetes, NVIDIA GPU Operator / DCGM / NIM / Confidential Containers, KServe, vLLM Production Stack / llm-d, Gateway API Inference Extension, Envoy AI Gateway, Temporal, AWS EKS/SageMaker HyperPod, GKE Inference Gateway, Azure Application Gateway for Containers, Google SRE Workbook, Stripe (idempotency pattern), Sigstore/SLSA. Prices below are **third-party aggregator quotes of AWS list-style on-demand** (not AWS Price List API dumps) and are labeled ⚠️. No unpublished p50/p95/p99 SLOs are invented. `$ per 1k executions` is **[inferred]** from a named GPU-hour SKU × a stated utilization/shape — not a universal industry rate.

Invariant: **an inference cluster is a stateful token factory sitting behind a stateless control plane.** The control plane (Gateway API, EPP, KEDA, Karpenter, Temporal server, admission) decides *which replica, when, and whether*. The data plane (vLLM, KV cache, NIXL/RDMA, SSE streams, MCP sessions) holds *bytes that cannot be cheaply moved*. Collapsing those planes — treating a 20-minute decode as a 30-second HTTP request, scaling GPU Deployments on CPU, or rolling vLLM like nginx — is how teams simultaneously OOM GPUs, lose prefix cache, and spend their error budget on “routine” deploys.

---

## 1. System Topology & Mechanics

### 1.1 Two planes, three clocks, one GPU

| Plane | What it is | Clock | Typical store | Failure if mixed |
| --- | --- | --- | --- | --- |
| **Control** | GPU Operator ClusterPolicy, Gateway/HTTPRoute/InferencePool, KEDA ScaledObject, Karpenter NodePool, Temporal server, admission (Kyverno / Binary Authorization) | kube-apiserver + scaler poll (KEDA default 15s; HPA `--horizontal-pod-autoscaler-sync-period` typically 15s) | etcd, Helm values, GitOps | App code that “picks a GPU” by inspecting prompts |
| **Data (tokens)** | Prefill/decode kernels, KV cache, prefix blocks, SSE/HTTP2 streams | User SLO clock: TTFT / TPOT / e2e | HBM on the replica; optional CPU/disk offload (LMCache) | Round-robin L4 load balancing across replicas → prefix-cache miss storm |
| **Data (side effects)** | Tool calls, MCP sessions, agent workflow history, queue offsets | Durable-execution clock (Temporal event history; Kafka offset; SQS visibility timeout) | Temporal persistence / Kafka log / SQS / Redis PEL | Retrying a chat completion as if it were a Stripe `POST` *and* retrying a `payments.charge` tool without an idempotency key |

**Control vs data (cloud products).** Envoy AI Gateway documents a **two-tier** pattern: Tier-1 (central auth, top-level routing, global rate limit) vs Tier-2 (self-hosted model cluster + Endpoint Picker) ([Envoy AI Gateway GitHub](https://github.com/envoyproxy/ai-gateway/)). GKE Inference Gateway is the same split productized: GKE Gateway = L7 proxy (TLS, connection management, forwarding); llm-d EPP = routing intelligence over Envoy `ext-proc`; they scale independently ([About GKE Inference Gateway](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/about-gke-inference-gateway)). SageMaker HyperPod Inference Operator is a Kubernetes controller on EKS that owns model lifecycle while KEDA+Karpenter own elasticity ([HyperPod Inference Operator](https://aws.amazon.com/blogs/architecture/unlock-efficient-model-deployment-simplified-inference-operator-setup-on-amazon-sagemaker-hyperpod/); [HyperPod autoscaling](https://aws.amazon.com/blogs/machine-learning/introducing-auto-scaling-on-amazon-sagemaker-hyperpod/)). Interview move: **the GPU is not a pod; the KV cache is.** Any topology that lets the scheduler kill a replica without draining in-flight decode is treating state as cattle.

**Mesh vs inference gateway.** A service mesh (Istio mTLS, DestinationRules) is the *east-west* control plane for workers, MCP servers, Temporal workers. An **inference gateway** is the *north-south* control plane that must parse the OpenAI body (`model` field), not just `:path`. GIE’s request flow: Gateway matches HTTPRoute → if backend is an `InferencePool`, forward to EPP → EPP scores endpoints (KV / queue / LoRA) → Gateway sends to that Pod IP ([GIE intro](https://gateway-api-inference-extension.sigs.k8s.io/)). Istio’s GIE task requires a DestinationRule for TLS to the EPP ([Istio GIE task](https://preliminary.istio.io/latest/docs/tasks/traffic-management/ingress/gateway-api-inference-extension/)). Do not put Istio’s default round-robin in front of vLLM and call it “done.”

### 1.2 Docker: inference images, GPU runtime, distroless, supply chain

**GPU images are not “CUDA + app.”** Three layers:

1. **Host kernel driver** — NVIDIA GPU Operator default install deploys driver, Container Toolkit, Device Plugin, DCGM Exporter, MIG Manager as DaemonSet pods on every GPU node; current NVIDIA-documented patch is **v26.3.3** ([GPU Operator overview](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/overview.html); [install guide](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html)). Production clusters with a baked AMI often `--set driver.enabled=false` and let the Operator own only Kubernetes-facing components. PSA: the `gpu-operator` namespace must be `pod-security.kubernetes.io/enforce=privileged` because driver containers load kernel modules.
2. **Container Toolkit / CDI** — injects devices and CUDA userspace into the pod; the app image should *not* ship a second driver. Toolkit/operator mismatch is a classic “`nvidia.com/gpu: 0` after install” failure.
3. **App image** — vLLM (`vllm/vllm-openai`), NVIDIA NIM, or a distroless agent worker. NIM Operator owns NGC pull secrets, model cache Jobs (`backoffLimit` default 5), probes: `/v1/health/live` immediately, `/v1/health/ready` only after model load; startup probe failureThreshold × periodSeconds can be **minutes** for 70B ([NIM Operator](https://docs.nvidia.com/nim/large-language-models/2.0.4-pb6/deployment/kubernetes-deployment/nim-operator-deployment.html)).

**Distroless is for the control plane and CPU agent workers, not for the CUDA runtime.** Google distroless (`gcr.io/distroless/static-debian13`, `cc-debian13`, language images) ships no package manager and no shell; Kubernetes itself rebased to distroless (KEP-1729) ([distroless](https://github.com/GoogleContainerTools/distroless)). CUDA userspace still needs `libcuda` / cuDNN / NCCL. Pattern: **multi-stage** — build/compile in a fat NVIDIA CUDA image; copy the binary + only linked `.so` files onto `cc-debian13` **or** a continuously patched hardened base. ⚠️ NVIDIA CUDA base images lag distro OpenSSL patches; inheriting `nvidia/cuda:*-ubuntu*` as the *runtime* base is a CVE-lag decision, not a convenience.

**Supply chain that admission can consume.** An SBOM on a GitHub Release page is documentation. An SBOM **attached as a signed Cosign attestation on the image digest** is evidence ([Sigstore Cosign](https://github.com/sigstore/cosign); NVIDIA AICR: SPDX v2.3 JSON per-platform digest, not the multi-arch index — resolve with `crane digest --platform` before `cosign verify-attestation --type spdxjson` ([NVIDIA AICR supply-chain](https://github.com/NVIDIA/aicr/blob/main/docs/integrator/supply-chain-verification.md))). SLSA: Cloud Build trigger-produced provenance; Binary Authorization `built-by-cloud-build` attestor or CV SLSA check (trusted builder = `GOOGLE_CLOUD_BUILD` only) ([BinAuthz SLSA](https://cloud.google.com/binary-authorization/docs/cv-slsa-check); [deploy Cloud Build images](https://docs.cloud.google.com/binary-authorization/docs/deploy-cloud-build)). Keyless Cosign (Fulcio + Rekor) + Kyverno/`policy-controller` `verifyImages` at admission is the portable equivalent.

**Agent vs inference image split.** Inference: GPU, large layers, model weights on PVC/S3/FSx (don’t bake 70B into the image). Agent/Temporal worker: distroless/nonroot, read-only rootfs, dropped caps, no GPU. Mixing them (Python agent + vLLM in one container) couples blast radius and image size; Dragonfly/P2P pull helps the *inference* layer (EKS walkthroughs cite Dragonfly to avoid registry stampedes on GPU node scale-up) ([EKS Karpenter+KEDA+Dragonfly writeup](https://codingwithtaz.blog/2026/05/13/production-ready-gpu-inference-autoscaling-on-eks-with-karpenter-keda-and-dragonfly/)).

**Image layout that survives production.** Four artifacts, four TTLs: (1) **engine image** (vLLM/NIM digest, rebuilt on CVE); (2) **weights** (HF/S3/FSx, checksummed, RWX for LWS); (3) **tokenizer/config** (small ConfigMap or sidecar — pinning tokenizer drift is a silent quality incident); (4) **LoRA adapters** (hot-loaded, versioned independently). NIM cache Jobs retry (`backoffLimit` 5) so a flaky NGC pull does not leave a Ready=false replica in the InferencePool forever. Probes: liveness on `/v1/health/live` (process up); readiness on `/v1/health/ready` (weights in HBM). Inverting those two is how rolling updates send traffic to a loading GPU and then OOM-kill it. Resource requests: `nvidia.com/gpu` is unsplittable in the default device plugin — `cpu`/`memory` still matter because tokenizer + Python + Prometheus multiprocess dir (`PROMETHEUS_MULTIPROC_DIR` in Dynamo/vLLM) sit in DRAM, not HBM.

**CDI vs legacy nvidia-container-runtime.** GPU Operator ≥25.10 path: containerd CDI, optional NVIDIA DRA driver (`nvidia-dra-driver-gpu`) for attribute-based allocation and ComputeDomain (multi-node NVLink). DRA GPU allocation defaults **off** in Helm (`resources.gpus.enabled=false`); enabling it while leaving the Device Plugin on double-advertises GPUs. ⚠️ Treat DRA as K8s 1.34+ / driver 580+ only, with an explicit disable of the classic plugin ([GPU resource management playbook](https://devfloor9.github.io/engineering-playbook/en/docs/agentic-ai-platform/model-serving/gpu-infrastructure/gpu-resource-management)).

### 1.3 Kubernetes GPU topology

**Device plugin contract.** Pods request `nvidia.com/gpu` (integer; not millicores). GPU Feature Discovery labels product, VRAM, CUDA. MIG (Operator ≥ v26.3.0): MIG Manager generates per-node ConfigMaps from NVML; changing `nvidia.com/mig.config` **stops all GPU pods** (device plugin, GFD, DCGM), applies profiles, may reboot ([GPU Operator MIG](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-mig.html)). Time-slicing vs MIG vs full GPU: time-slicing = noisy neighbor on HBM; MIG = hardware isolation, fewer concurrent contexts; full GPU = default for vLLM because KV cache wants contiguous HBM.

**KServe dual-track (v0.20).** `InferenceService` = predictive ML. `LLMInferenceService` (`serving.kserve.io/v1alpha1`) = GenAI on llm-d: KV-aware scheduling, P/D, LWS multi-node ([LLMInferenceService overview](https://kserve.github.io/website/docs/model-serving/generative-inference/llmisvc/llmisvc-overview)). Config composition: well-known configs (lowest) ← user `baseRefs` ← LLMInferenceService spec (highest). Prefill/decode templates are first-class (`kserve-config-llm-prefill-template`, `kserve-config-llm-decode-template`). Admin install (0.19 docs): Kubernetes **1.32+**, cert-manager 1.18+, Gateway API **1.3.0+**, GIE **1.2.0**, Envoy Gateway **v1.5.0+**, LWS **0.6.2+** for multi-node ([KServe LLMIsvc install](https://kserve.github.io/website/docs/0.19/admin-guide/kubernetes-deployment-llmisvc)). **Install GIE CRDs before the Gateway provider** or the provider never learns `InferencePool` and you restart operators ([dependencies](https://kserve.github.io/website/docs/model-serving/generative-inference/llmisvc/llmisvc-dependencies)).

**LeaderWorkerSet.** Unit of replication for TP/DP/EP: leader + workers, stable DNS, RDMA. KServe: `LWS size = data / dataLocal` (example: data=8, dataLocal=2 → size 4 = 1 leader + 3 workers). Multi-node ServingRuntime still documents PVC **ReadWriteMany**, autoscaler **none** for that path, GPU types `nvidia.com/gpu` | `intel.com/gpu` | `amd.com/gpu` | `habana.ai/gaudi` ([KServe multi-node](https://kserve.github.io/website/docs/model-serving/generative-inference/multi-node); [LWS](https://github.com/kubernetes-sigs/lws)).

**vLLM Production Stack.** Helm: serving engines + router (session / prefix-aware / disaggregated_prefill) + Prometheus/Grafana. Disaggregated prefill: separate Deployments, router `enablePD: true`, NIXL KV transfer; same-AZ RDMA/EFA required on AWS P5/P6 ([vLLM production stack](https://docs.vllm.ai/en/stable/deployment/integrations/production-stack/); [disaggregated prefill](https://github.com/vllm-project/production-stack/blob/main/docs/source/use_cases/disaggregated-prefill.rst); [HyperPod DPD](https://aws.amazon.com/blogs/machine-learning/disaggregated-prefill-and-decode-for-llm-inference-on-sagemaker-hyperpod/)). llm-d entered CNCF Sandbox 2026-03-24; NIXL does peer discovery so you do not pin NCCL ranks in the connector config; ⚠️ scaling P/D replicas >1 still needs explicit pairing docs ([llm-d P/D guide via Spheron](https://www.spheron.network/blog/llm-d-kubernetes-disaggregated-inference-guide/); [llm-d](https://llm-d.ai/)).

**Autoscaling two loops.**

| Loop | Tool | Signal | Scale-to-zero? |
| --- | --- | --- | --- |
| Pod | KEDA `ScaledObject` → managed HPA | Prometheus `vllm:num_requests_waiting`, p95 `vllm:e2e_request_latency_seconds`; Kafka lag; SQS depth | Yes (`minReplicaCount: 0`); HPA alone cannot |
| Node | Karpenter NodePool | Unschedulable pods (`Unschedulable=True`) | Yes (empty node consolidation) |

KEDA v2.17: **activation** (0↔1, `activationThreshold`) vs **scaling** (1↔N, HPA target). Activation has priority: `threshold: 10` + `activationThreshold: 50` with 40 messages → stay at 0. `minReplicaCount >= 1` ignores activation. Pause via `autoscaling.keda.sh/paused`. Long-running: HPA may SIGTERM a replica 2.9h into a 3h job — handle with `terminationGracePeriodSeconds` / preStop, or run as Jobs ([KEDA scaling](https://keda.sh/docs/2.17/concepts/scaling-deployments/)). AWS EKS walkthrough (example, not a universal SLO): scale when **average queue depth > 25 waiting req/pod** or **p95 e2e > 5s**; scale-up stabilization 30s / +2 pods/min; scale-down 300s / −1 pod/120s ([EKS HPA+KEDA](https://docs.aws.amazon.com/eks/latest/userguide/ml-inference-autoscaling-hpa-keda.html)). **Do not scale vLLM on CPU or `DCGM_FI_DEV_GPU_UTIL` alone** — a saturated decode replica can show low CPU and pinned SM util while the *queue* is the demand signal.

**HPA math (why CPU HPA lies).** Kubernetes HPA uses `desiredReplicas = ceil(currentReplicas × (currentMetric / desiredMetric))` with a 10% tolerance and stabilization windows ([HPA docs](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)). For `metricType: AverageValue` on `sum(vllm:num_requests_waiting)`, KEDA divides by replica count so the threshold is **per pod**. Two triggers → HPA takes the **max** desired. Scale-up must be faster than model-load time or you add replicas that are NotReady while the queue is already the SLO violation. Scale-down stabilization (AWS example 300s) exists because a GPU that took 3–8 minutes to become Ready should not be killed by a 30s lull. `useCachedMetrics` in KEDA cuts scraper load (HPA polls ~15s; scaler poll may be slower) — disable for latency-guardrail metrics that must not be stale across a 5s p95 window.

**Karpenter GPU NodePools.** Separate pools: (a) on-demand decode (`p5`/`a3`/`ND`), expireAfter for CIS node recycle; (b) spot prefill/batch with interruption draining; (c) CPU for gateways/EPP/Temporal. Consolidation `WhenEmpty` is safer than aggressive bin-pack on GPU — packing two incompatible models onto one node after a shuffle destroys MIG/time-slice assumptions. Topology: `topology.kubernetes.io/zone` + `nvidia.com/gpu.product`. CapacityBuffers (Karpenter) pre-warm nodes when TTFT SLO cannot wait EC2 allocation + driver + weight load.

Karpenter: provisions without node groups; retries in milliseconds vs Cluster Autoscaler node-group minutes; disruption = Expiration / Consolidation / Drift / Interruption (spot); respects PDB; GPU NodePools constrain instance families (`g6`, `p5`, …) ([Karpenter concepts](https://karpenter.sh/docs/concepts/)). SageMaker HyperPod ships **managed Karpenter**.

### 1.4 APIs: OpenAI-compat gateways, auth, versioning, SSE, idempotency, quotas

**Gateway stack.**

| Layer | Job | Products |
| --- | --- | --- |
| Edge / Tier-1 | AuthN, RPM/TPM quotas, model alias, canary split, PII filter | Envoy AI Gateway (`AIGatewayRoute` + `AIServiceBackend`), Apigee on GKE, LiteLLM |
| Inference / Tier-2 | Endpoint pick on KV/queue/LoRA; P/D routing | GIE InferencePool + EPP / llm-d-router; GKE Inference Gateway; Azure Application Gateway for Containers (preview, Helm `--set albController.aiGateway=true`, GIE CRDs v1.3.1) ([Azure inference gateway](https://learn.microsoft.com/en-us/azure/application-gateway/for-containers/how-to-inference-gateway)) |
| Engine | OpenAI `/v1/chat/completions`, `/v1/completions`, `/v1/models` | vLLM, NIM (passes through vLLM metrics at `/v1/metrics`) ([NIM observability](https://docs.nvidia.com/nim/large-language-models/3.0.0/reference/logging-and-observability.html)) |

Envoy AI Gateway 1.0: committed-stable control-plane API, 16 providers, MCP gateway, multimodal ([Envoy AI Gateway](https://aigateway.envoyproxy.io/); [API](https://aigateway.envoyproxy.io/docs/api/)). GKE: body-based routing on OpenAI `model`; weighted HTTPRoute canary (docs example: 90/10 gemma vs gemma-new); **Priority < 0** shed first with **429**; streaming errors are **not retried**; CORS on HTTPRoute; **50 NEG per Backend Service** caps multi-port × zones × clusters ([GKE concepts](https://cloud.google.com/kubernetes-engine/docs/concepts/about-gke-inference-gateway)). AWS sample on EKS: precise KV-aware routing vs round-robin reduced **p90 TTFT by up to 69%** under their Poisson multi-turn load — sample, not a law ([aws-samples cache-aware routing](https://github.com/aws-samples/sample-eks-cache-aware-llm-routing)).

**Auth.** Edge: mTLS or JWT/OIDC (Istio PeerAuthentication; GKE BackendTLSPolicy on InferencePool). API keys for partners mapped to tenants. MCP: OAuth 2.1 + PRM (RFC 9728) + AS metadata (RFC 8414) + resource indicators (RFC 8707); 401 + `WWW-Authenticate` with `resource_metadata` ([MCP authorization](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/authorization); [spec 2025-11-25](https://mcp.mintlify.app/specification/2025-11-25/basic/authorization)). Envoy `MCPRoute`: OAuth on the gateway, per-backend API key injection, tool allowlists, JWT scope→tool CEL ([MCP Gateway](https://aigateway.envoyproxy.io/docs/capabilities/mcp/)).

**Versioning.** OpenAI documents `openai-version` (currently `2020-10-01` on REST) and treats additive fields/events as backwards-compatible ([API overview](https://developers.openai.com/api/reference/overview/)). Self-hosted: **model name in the JSON body** is the version. Pin `gpt-x-YYYY-MM-DD` or `llama-3-8b-v12` in the gateway; never let clients hit `latest`. GIE model rollouts = traffic split by model name / InferenceObjective.

**Streaming SSE.** Chat Completions: `stream=true` → data-only SSE chunks with `choices[0].delta`; terminate with `[DONE]`. Responses API: typed events (`response.created`, `response.output_text.delta`, `response.completed`, `error`) ([OpenAI streaming](https://developers.openai.com/api/docs/guides/streaming-responses)). Production implications: (1) LBs must not buffer the whole body (`proxy-buffering off` / HTTP/2 streaming). (2) Idle timeouts must exceed TPOT×max_tokens, not “60s API timeout.” (3) OpenAI: moderation scores arrive **after** full output, not on deltas — gateway output filters on partial tokens are weaker. (4) GKE: no retry of a failed stream — client must reconnect with a new request (or Temporal activity retry **once**, knowing tokens already billed). (5) Envoy MCP: `Last-Event-ID` for SSE reconnect.

**Idempotency.** Stripe: `Idempotency-Key` (≤255, UUID v4, not PII); store first status+body ≥24h including 500s; param mismatch → error; GET/DELETE ignore the header ([Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests); [design](https://stripe.com/blog/idempotency)). OpenAI **chat completions are not Stripe-idempotent**: the same key cannot replay a generation without either (a) returning a cached completion or (b) charging twice. Correct split: **idempotency on side-effecting tools and workflow start** (`Idempotency-Key` → Temporal `Workflow-Id` reuse); **at-most-once or explicit “resume stream”** on token generation. ⚠️ OpenAI has no public `Idempotency-Key` on Chat Completions; do not claim otherwise.

**Quotas (RPM/TPM).** OpenAI enforces independent RPM, RPD, TPM, TPD, IPM; first ceiling hit wins. Headers: `x-ratelimit-limit-requests`, `-tokens`, `-remaining-*`, `-reset-*`, project-scoped token variants; `Retry-After` on 429 ([rate limits](https://developers.openai.com/api/docs/guides/rate-limits); [error codes](https://developers.openai.com/api/docs/guides/error-codes)). Streaming **does not** get a cheaper RPM/TPM pool; TPM is counted when the request **completes**. Self-hosted gateways should mirror this: token-bucket per `(tenant, model)` in Redis, plus **concurrency** (in-flight streams) because TPM is lagging. Elastic Observability: alert at **80% of project/model peak-minute** vs ceiling, not 5-minute averages ([Elastic rate-limit monitoring](https://www.elastic.co/observability-labs/blog/openai-rate-limit-monitoring)).

**Gateway quota implementation notes.** Estimate TPM on *request* using tokenizer of the **served** model (not cl100k on a Llama backend — you will under-count). Decrement remaining tokens on `response.completed` / last SSE chunk; if the client aborts, still count generated tokens (vLLM kept running — see cancellation bug class). Concurrency limit = `max_num_seqs` × replicas × safety factor < 1. Return **429 with Retry-After** for overload vs **402/403** for tenant budget vs **503** for no Ready endpoints — clients and KEDA must not treat those as the same signal. Envoy cluster circuit breaker `max_pending_requests` is the last bulkhead before the GPU.

### 1.5 Queues: Kafka, SQS, Redis Streams, Temporal

**When HTTP is the wrong API.** Agent runs, batch summarization, tool fan-out, and anything with `Start-To-Close` minutes belong on a queue or a workflow. Online chat stays on the inference gateway with a **bounded** admission queue (GIE shedding / vLLM `max_num_seqs`).

| System | Ordering | Back-pressure | DLQ | Fit |
| --- | --- | --- | --- | --- |
| **Kafka** | Per partition | Passive: lag. `pause()`/`resume()`; `max.poll.records`; consumer count ≤ partitions | App-level retry topic; not native | High-throughput event log; replay; multi-consumer-group |
| **SQS** | Standard: none. FIFO: group | Visibility timeout + queue depth | Native redrive `maxReceiveCount`; same account/Region; FIFO DLQ **breaks** exact order ([SQS DLQ](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-dead-letter-queues.html)) | Simple workers; KEDA SQS scaler; HyperPod lists SQS as a KEDA trigger |
| **Redis Streams** | Stream ID order | `MAXLEN` trim (shed oldest); PEL via `XREADGROUP`/`XACK`/`XCLAIM` | DIY: delivery count → `XADD` dlq → `XACK` ([Redis streaming](https://redis.io/docs/latest/develop/use-cases/streaming/)) | Hours–days retention; low ops vs Kafka |
| **Temporal** | Workflow history | Task-queue backlog; Worker slots; Schedule-To-Start as *detection* not primary throttle | Failed activities retry by policy; “DLQ” = failed workflow / manual reset | Agents, HITL, multi-step tools |

Kafka head-of-line: one slow message stalls a partition; adding consumers **does not** help; exceeding `max.poll.interval.ms` (default 5 min) evicts the member → rebalance storm. Fix: pause partition + worker thread, or timeout → DLQ ([MSK HOL](https://repost.aws/articles/AR08CYM7xFQyqcUKH8Oxo6LA/why-adding-consumers-won-t-fix-your-consumer-lag-on-amazon-msk-head-of-line-blocking)). Production consumer knobs that actually bound memory: `max.poll.records` (e.g. 100), `max.partition.fetch.bytes`, manual commit **after** the tool Activity succeeds (at-least-once), and lag alerts that distinguish *spike-and-recover* from *exponential lag* (pipeline death). Partition count is a capacity plan: parallelism cannot exceed it; over-partitioning for a 2-worker agent team is operational noise.

SQS: set DLQ retention **longer** than source (standard queues keep original enqueue timestamp; FIFO DLQ **resets** enqueue time). `maxReceiveCount=1` is not resilience — it is a panic button. Standard queues with `maxReceiveCount > 3` move poison to the back after 3 receives so `ApproximateAgeOfOldestMessage` is not stuck on one zombie. Redrive allow policy: `byQueue` (≤10 source ARNs) or `denyAll` so a mis-set DLQ is not a write sink for the account.

Redis Streams: `XADD` + auto IDs; consumer groups share a `last-delivered-id`; each consumer has a PEL. Forgotten `XACK` = unbounded PEL = “back-pressure” that is actually a leak. `XCLAIM` min-idle-time is the reclaim path after a crashed agent worker. `MAXLEN ~` approximate trim is the shed-oldest valve — acceptable for telemetry, **not** for payment tools (those belong in Temporal).

Temporal: Workflow = deterministic orchestration; **every LLM call and tool is an Activity**; disable SDK-internal retries so Temporal owns backoff. Timeouts: **always set Start-To-Close** (server cannot detect a dead worker otherwise); Schedule-To-Close caps all retries; Heartbeat for long tools; Continue-As-New before history size limits ([Temporal activity failures](https://docs.temporal.io/encyclopedia/detecting-activity-failures); [timeouts](https://temporal.io/blog/activity-timeouts); [AI reference architecture](https://go.temporal.io/platform-hub/ai-engineering/ai-reference-architecture)). Worker Controller: rainbow deploys, `Progressive` ramp + **gate Workflow** before Current Version, HPA/KEDA per version, PDB templates ([Worker Controller](https://docs.temporal.io/production-deployment/worker-deployments/kubernetes-controller)). OpenAI Codex-on-the-web is a cited Temporal production agent ([Temporal agents blog](https://temporal.io/blog/of-course-you-can-build-dynamic-ai-agents-with-temporal)). Back-pressure across the stack: **gateway concurrency limit** (fail 429) → **vLLM waiting queue** (KEDA) → **Kafka/SQS lag** (KEDA workers) → **Temporal Schedule-To-Start** (worker deficit). If you only watch GPU util, you will scale the wrong layer.

---

## 2. Token Economics & NFR Metrics

### 2.1 SLIs that match user clocks (not GPU clocks)

Google SRE: SLI = good events / total events; SLO < 100%; error budget = 100% − SLO; 3M requests at 99.9% → **3,000** errors / 4 weeks ([SRE Workbook ch.2](https://sre.google/workbook/implementing-slos/)). For inference, **HTTP 200 is not “good”** if TTFT blew the chat UX. Split SLIs:

| SLI | Good event | Why not GPU util |
| --- | --- | --- |
| Availability | Completed streams with `finish_reason ∈ {stop, tool_calls}` and no gateway 5xx/429-shed of *priority ≥ 0* | 429 from quota is often *intended* (budget), 429 from overload is error |
| TTFT | First SSE delta < T_chat (interactive) | Prefill-bound; P/D and prefix routing exist for this |
| TPOT / ITL | Inter-token latency < T_decode | Decode-bound; HOL prefill on a monolith wrecks this |
| E2E | Full completion < T_job (batch) | Agents: per-turn vs per-workflow |
| Correctness | Tool success / schema-valid JSON | Separate error budget from infra |

**Published example thresholds (not your SLO).** AWS EKS docs use **25 waiting requests/pod** and **p95 e2e 5s** in a walkthrough — substitute measurements for *your* model/GPU/shape ([EKS KEDA](https://docs.aws.amazon.com/eks/latest/userguide/ml-inference-autoscaling-hpa-keda.html)). ⚠️ Vendor p50/p95/p99 for production vLLM fleets are almost never published as commitments. NIM/vLLM expose histograms: `vllm:e2e_request_latency_seconds`, `vllm:num_requests_waiting`, KV cache usage; Dynamo adds `dynamo_frontend_*` ITL when NIM runs in Dynamo mode (scrape `/v1/metrics` **and** worker `:9090/metrics`) ([NIM metrics](https://docs.nvidia.com/nim/large-language-models/3.0.0/reference/logging-and-observability.html); [Dynamo metrics](https://docs.nvidia.com/dynamo/dev/user-guides/observability-local/metrics)). DCGM Exporter default listen `:9400`, interval **30000 ms** ([DCGM Exporter](https://docs.nvidia.com/datacenter/cloud-native/gpu-telemetry/latest/dcgm-exporter.html)). Use DCGM for **hardware health** (XID, ECC, FB used) and vLLM for **demand**.

GKE predicted-latency routing (Preview): XGBoost on live traffic for per-request TTFT/TPOT — still a routing heuristic, not an SLO.

### 2.2 GPU-hour → `$ per 1k executions` (formula, not a fake benchmark)

⚠️ **On-demand SKU (aggregator, 2026-08).** Thunder Compute (reviewed 2026-08-14) and DevZero quote **p5.48xlarge $55.04/hr us-east-1** = **$6.88 / H100-hr** (8×H100); **p5.4xlarge $6.88/hr** (1×H100). Regional spread in the same tables: us-east-1 $55.04 vs sa-east-1 $92.47. These are **not** AWS Price List API exports — confirm in AWS Console / Price List before budgeting ([DevZero p5.48xlarge](https://www.devzero.io/instances/aws/p5.48xlarge); [Thunder Compute AWS P5 Aug 2026](https://www.thundercompute.com/blog/aws-p5-vs-thunder-compute)). Conflicting blog posts quoting ~$98/hr for the same shape appear to mix families/regions; do not average them.

**[inferred] unit economics** (illustrative arithmetic, not a measured benchmark):

```
$ / 1k executions = (GPU_hours_consumed / executions) × 1000 × $/GPU-hr
GPU_hours_consumed = replicas × wall_hours × (1 / utilization_fraction_of_billed_time)
```

Example **[inferred]**: one H100 billed 1 hour at $6.88, serving 3,440 completed requests → **$2.00 / 1k executions** infra-only. Change concurrency, prompt length, or idle time and the number moves by 10×. Add: EBS, egress ($0.09/GB after 100 GB in that Thunder Compute comparison — ⚠️ AWS egress SKUs vary), gateway CPU, Temporal Cloud, Kafka, model storage. **Token API cost** (if fronting OpenAI/Bedrock) is a *second* bill: TPM is not GPU-hour.

**Utilization traps.** `DCGM_FI_DEV_GPU_UTIL` high + `vllm:kv_cache_usage_perc` ~1.0 + growing `num_requests_waiting` = **memory-bound decode**, not “healthy busy.” High util + empty queue = batch/prefill saturation. Scale-to-zero saves the $6.88/hr but **cold start = image pull + model load into HBM**. AWS EKS: new replica Ready only after model load; KEDA scale-down is conservative for that reason. Coding-with-Taz EKS post claims **84s cold / 7s warm** with a *small* image — ⚠️ that is their demo shape, not 70B on p5.

**RPM vs TPM vs GPU concurrency.** RPM binds chatty small prompts. TPM binds RAG (10k-token prompts). GPU binds **in-flight sequences × KV bytes**. A tenant can be under RPM and still OOM the replica (`max_num_seqs` / `gpu_memory_utilization`). Gateway quotas must cap **all three**. OpenAI: hitting RPM with 20 tiny requests still 429s even if TPM is unused.

**Prefill vs decode pools (cost).** Prefill is compute-bound (want high SM clocks, can use fewer high-end GPUs); decode is memory-bandwidth / KV-capacity-bound (want HBM and stable TPOT). Disaggregation lets you **right-size independently** — AWS HyperPod DPD: prefiller pushes KV via LMCache/NIXL/EFA layer-by-layer; decoder reserves `PD_BUFFER_SIZE`; **same AZ** for EFA RDMA; G6 multi-GPU called out as PCIe-bottlenecked vs P5/P6 ([HyperPod DPD](https://aws.amazon.com/blogs/machine-learning/disaggregated-prefill-and-decode-for-llm-inference-on-sagemaker-hyperpod/)). ⚠️ Do not multi-AZ a NIXL path and expect training-cluster bandwidth.

**Capacity planning checklist.** (1) Measure tokens/s and concurrent seqs at SLO TTFT/TPOT on *one* replica. (2) KV bytes ≈ 2 × layers × kv_heads × head_dim × dtype × seq × batch (order-of-magnitude; engine-specific). (3) Headroom: vLLM `--gpu-memory-utilization` default **0.9** OOMs when CUDA graphs + fragmentation eat the 10%; production writeups recommend **0.75–0.85** on shared nodes ([vLLM tuning, Red Hat](https://developers.redhat.com/articles/2026/03/03/practical-strategies-vllm-performance-tuning)). (4) Spare replicas ≥ PDB `maxUnavailable` + 1 AZ failure. (5) Karpenter CapacityBuffers for GPU if TTFT SLO cannot wait node spin-up.

**NFR percentile contract (what to put in the SLO doc).** Specify **shape**, not just percentile: e.g. “p95 TTFT for prompts ≤ 2k tokens, decode ≤ 512 tokens, cache-hit ratio unstated.” Mixing RAG 32k prompts into the same histogram as “hi” makes p95 a fiction. Separate SLIs: interactive vs batch vs agent-tool. Error budget policy: deploys, model swaps, and GPU Operator upgrades **consume** the budget — Google’s 100% SLO argument is that change *is* the outage source ([SRE Workbook](https://sre.google/workbook/implementing-slos/)). Burn-rate alerts: 1h fast burn + 6h slow burn on TTFT-good-ratio, not on GPU util.

**`$ per 1k` worked example (all [inferred], labeled).** Assume aggregator **$6.88/H100-hr**, 1 replica, 40% of wall-clock actually serving (rest idle/NotReady), 2,000 completed chat turns/hour while serving. GPU cost = 6.88 / 0.4 = **$17.20** billed per serving-hour equivalent → **$8.60 / 1k turns** infra. If 30% of turns are gateway 429s that still hit prefill before shed, effective good-turn cost rises; measure **good completions** in the denominator. Add Temporal/Kafka CPU as a second line — usually << GPU unless you store full prompts in the workflow history (don’t; store blob refs).

---

## 3. Distributed Resilience & State

### 3.1 What must be durable vs what must be sticky

| State | Sticky (session affinity / prefix routing) | Durable (survive process death) |
| --- | --- | --- |
| KV / prefix blocks | Yes (EPP / session-id router) | Optional (LMCache CPU/disk; still replica-local unless clustered) |
| In-flight SSE | Yes (that TCP connection) | No — reconnect = new request unless app checkpoints partial output |
| Agent plan + tool results | No | **Temporal event history** |
| Queue messages | Partition/group | Kafka log / SQS / Redis PEL |
| Model weights | PVC / image / NIM cache | Object storage source of truth |

**Circuit breakers (bulkhead, not retry-storm).** Envoy: per-cluster max connections, pending requests, concurrent requests, **max retries** (recommend retry *budgets* so retries cannot explode) ([Envoy circuit breaking](https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/upstream/circuit_breaking.html)). Outlier detection ejects 5xx hosts. Apply at the **gateway → vLLM** cluster *and* **agent → MCP** cluster. Retry **non-streaming** 503/429 with jitter; **do not** blindly retry streaming or non-idempotent tools.

**Rate limits as resilience.** Tenant RPM/TPM protects *others* (noisy neighbor). Global concurrency protects *GPUs*. GIE Priority: drop `Priority < 0` with 429 under pressure. OpenAI-compat: return the same header set (`Retry-After`, remaining RPM/TPM) so official SDKs back off.

**PDBs.** Kubernetes: `minAvailable` / `maxUnavailable`; `maxUnavailable: 0` or `minAvailable: 100%` → **drain never completes** ([configure PDB](https://kubernetes.io/docs/tasks/run-application/configure-pdb/)). `unhealthyPodEvictionPolicy: AlwaysAllow` so CrashLoop pods don’t deadlock Karpenter. GPU inference: PDB must leave enough **Ready+warm** replicas to absorb one drain; cold start is not “available.” HPA/KEDA vs PDB: scale-down that violates PDB will stall node consolidation — that is a **cost** failure mode, not a free reliability win.

**Graceful drain.** Default `terminationGracePeriodSeconds=30`. A 28-minute decode is not 30 seconds. Pattern: readiness fail on SIGTERM (remove from Service / InferencePool) → finish or cancel in-flight → exit. ⚠️ vLLM has had issues honoring HTTP cancellation during streaming (community: issue #24584 — engine continues minutes after client disconnect). If unfixed on your version, drain ≠ cancel. GKE node upgrades may only honor PDB grace up to a platform cap (community: ~1 hour on GKE automatic upgrades) — ⚠️ verify current GKE docs for your cluster version. Pair drain with conservative `maxUnavailable`.

**Temporal as the agent control plane.** Workers on K8s; Temporal server (self-host Cassandra/Postgres or Temporal Cloud multi-region). KEDA on task-queue depth for workers; Worker Controller recommends HPA+Prometheus Adapter when many task queues (KEDA hits Temporal API rate limits). Progressive rollout: e.g. 1% 30s → 10% 1m + gate Workflow `HelloWorld` ([Worker Controller](https://docs.temporal.io/production-deployment/worker-deployments/kubernetes-controller)). Signals/Updates = HITL without burning GPU.

**Multi-AZ / multi-region.** Place **stateless gateways** and **Temporal workers** multi-AZ. Place **P/D KV transfer** and **TP ranks** in one AZ (or one NVLink domain). Multi-region active-active inference: replicate *weights* (object storage), not KV; DNS/geo or GKE multi-cluster Gateway subject to **NEG limits**. Failover = cold cache → TTFT SLO burn is expected; budget it.

**Region topology that does not lie.** Three patterns, increasing RPO/RTO honesty:

| Pattern | Data plane | Control plane | RPO on KV | When |
| --- | --- | --- | --- | --- |
| **Single-region multi-AZ** | Decode replicas per AZ; **no** TP across AZ | Gateway regional; Temporal workers spread | Replica-local; lose AZ → lose those prefixes | Default for chat |
| **Active-passive DR** | Warm (or cold) GPU pool in region B; weights in dual-region bucket | DNS failover; Temporal Cloud replication if used | Full miss on failover | Compliance DR, not “seamless” |
| **Active-active** | Independent InferencePools per region; sticky region by user or session | Global gateway (Apigee / Envoy Tier-1) with **model+region** routing | Never shared | Capacity shopping (GKE docs: pull GPU/TPU where it exists) |

HyperPod DPD and llm-d NIXL assume **same AZ**. GKE Inference Gateway can front **multiple clusters** until the 50-NEG math fails (8 ports × 3 zones × 2 clusters = 48). Azure Application Gateway for Containers inference path is **preview** (2026) — treat as non-GA for regulated go-live. Chaos: kill an AZ’s GPU nodes and measure TTFT and 429 rate — that *is* the multi-AZ SLO, not “we have 3 zones in the YAML.”

**Chaos.** Chaos Mesh / Litmus: pod kill, network latency, disk fill. GPU-specific: DCGM-fault injection is not the same as killing a Deployment. Test: (1) SIGTERM mid-stream, (2) Kafka rebalance during tool batch, (3) Karpenter interruption on spot GPU, (4) EPP unavailable → 503 not blackhole, (5) Temporal worker drain during activity. Argo Rollouts: keep stable RS at 100% when traffic-managed so canary weight ≠ overloaded canary pods ([Argo Rollouts traffic](https://argoproj.github.io/argo-rollouts/features/traffic-management/)).

---

## 4. Enterprise Security & Governance

### 4.1 Zero-Trust MCP (gateway as PEP)

Do not let agents dial MCP servers with a shared PAT. Envoy MCP Gateway is the policy enforcement point: OAuth on the *client-facing* `/mcp`, tool names prefixed (`github__issue_read`), `toolSelector` include/regex, per-backend secrets, optional header forwarding for user PATs (`forwardHeaders` scoped so fan-out `tools/list` doesn’t leak tokens to every backend) ([MCP Gateway](https://aigateway.envoyproxy.io/docs/capabilities/mcp/)). Authorization rules: JWT scopes/claims + CEL on `request.mcp.tool` / `request.mcp.params`. Spec: AS **MUST** be OAuth 2.1; servers **MUST** PRM (RFC 9728); clients **MUST** RFC 8707 `resource` ([MCP auth spec](https://mcp.mintlify.app/specification/2025-11-25/basic/authorization)). **Confused deputy**: a token minted for gateway resource `https://api.example.com/mcp` must not be accepted by a raw GitHub MCP.

### 4.2 mTLS, RBAC, network

- **North-south:** Gateway TLS; GKE BackendTLSPolicy verifies model-server identity ([deploy GKE Inference Gateway](https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway)).
- **East-west:** Istio STRICT mTLS for Temporal workers, MCP backends, EPP. Istio GIE example uses DestinationRule TLS to EPP (their snippet uses `insecureSkipVerify: true` — **do not copy that to prod**).
- **Kubernetes RBAC:** platform owns ClusterPolicy / NodePools / GatewayClasses; app teams own LLMInferenceService in a namespace. Bind `nvidia.com/gpu` via ResourceQuota so a tenant cannot schedule the fleet.
- **NetworkPolicy:** default deny; allow Gateway → InferencePool pods on targetPorts (GIE: up to 8 ports); allow EPP scrape of vLLM metrics; allow NIXL/RDMA only inside the P/D namespace/AZ. Multi-port DP-attention + 3 zones = 24 NEGs per pool — plan the 50-NEG cap.

### 4.3 PII, audit, content

Traces and prompts are PII stores (see observability research). Gateway: GKE Model Armor / NeMo Guardrails on request **and** response; Apigee for API product quotas. Audit: who called which `model`, tokens, tenant, `x-request-id` (OpenAI recommends logging it). MCP: log `tools/call` name + hashed args, not raw secrets. SSE: OpenAI warns partial completions are harder to moderate — enterprise gateways still run **output classifiers on rolling windows**, accepting residual risk or buffering (which kills TTFT).

### 4.4 Sandbox

| Isolation | Mechanism | GPU? |
| --- | --- | --- |
| Pod Security Restricted | nonroot, no privilege, seccomp | CPU agents |
| gVisor / gVisor-GPU | syscall intercept | Limited; ⚠️ always check current GPU support |
| Kata + Confidential Containers | UVM + TEE; GPU Operator deploys Kata Manager + CC Manager; Hopper + AMD SEV-SNP EA; containerd only; NFD labels `nvidia.com/cc` ([GPU Operator CoCo](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/25.10/confidential-containers.html)) | Passthrough `nvidia.com/pgpu`; multi-GPU on HGX = **all** GPUs+NVSwitches to one UVM ([CC workloads](https://docs.nvidia.com/datacenter/cloud-native/confidential-containers/latest/configure-workloads.html)) |
| Tool sandbox | Firecracker/WASM **without** GPU for untrusted code | N/A |

Admission: only signed+SBOM’d images; NIM from NGC with pull secrets; no `:latest`.

---

## 5. Production Failure Modes

| Failure | Mechanism | Blast radius | Mitigations |
| --- | --- | --- | --- |
| **GPU OOM** | `gpu_memory_utilization` too high; long `max_model_len`; activation + KV + CUDA graph > free HBM; fragmentation | Pod restart; all in-flight streams die; KEDA may **scale up** on error latency → more OOMs | Lower utilization to 0.75–0.85; `--kv-cache-dtype fp8`; cap `max_num_seqs`; classify OOM (input/output tokens, KV %, DCGM) — **do not liveness-restart every OOM**; canary recover after driver reset ([self-healing guide](https://oh-bug.com/posts/llm-gpu-self-healing-production-guide/)) |
| **Noisy neighbor** | Time-slicing / MPS; two vLLM on one GPU; CPU noisy on EPP | TPOT jitter; TTFT SLO burn | MIG or full GPU; QoS Guaranteed; separate NodePools for interactive vs batch; GIE Priority |
| **Rolling-update KV loss** | New RS, old pods SIGTERM; prefix cache empty; EPP still scores dying pods | Fleetwide TTFT spike during “routine” deploy | maxUnavailable 1; surge with extra GPUs; session pin until drain; LMCache offload; **canary 1% model name split** before RS roll; GPU Operator canary on **one labeled node** (driver/toolkit blast radius is cluster-wide if you don’t) ([GPU Operator canary recipe](https://kubernetes.recipes/recipes/deployments/gpu-operator-upgrade-canary/)) |
| **Queue meltdown** | Kafka lag exponential; SQS in-flight = visibility timeout storm; Redis PEL leak (`XACK` forgotten) | Duplicate tools; TPM burn; Temporal activity retries amplify LLM cost | Lag SLO + pause partitions; SQS DLQ `maxReceiveCount` ≥ 3 not 1; PEL reclaim `XCLAIM`; Temporal Start-To-Close; **never** unbounded HTTP retry into vLLM |
| **Thundering herd** | Scale-from-zero; all clients retry after 429; Karpenter + image pull + model load | Control-plane + GPU quota + registry | activationThreshold; jittered Retry-After; warm min replicas for SLO-critical models; Dragonfly; NIM cache PVC; admission concurrency limit at gateway **before** KEDA |
| **Scale-to-zero mid-decode** | HPA/KEDA treats GPU like nginx; 30s grace | Partial SSE; billed prefill wasted | minReplicas ≥ 1 for interactive; grace ≥ p99 decode; preStop drain; KEDA long-running warning ([tianpan mid-decode](https://tianpan.co/blog/2026-06-02-the-autoscaler-that-scaled-to-zero-mid-decode)) |
| **EPP / ext-proc down** | Gateway cannot pick | 503 or fallback to random (worse) | EPP HA; timeout + fail-closed for paid traffic; Istio outlier |
| **MIG reconfig** | Label change stops **all** GPU pods on node | Node-level outage | Maintenance window; PDB across nodes; never dual-wield MIG change + app deploy |
| **NCCL/NIXL AZ split** | P/D or TP across AZ | Hang / timeout / KV transfer fail | Topology spread *within* AZ for ranks; anti-affinity across AZ only for *replicas*, not *workers in one LWS* |
| **Spot interruption** | Karpenter interruption handler | Same as drain, shorter notice | On-demand for decode pool; spot for batch/prefill if restartable; PDB won’t help involuntary eviction |
| **Rebalance storm** | Kafka `max.poll.interval.ms` | Whole consumer group pause | Pause/resume; processing timeout+DLQ |
| **Idempotency hole** | Client retries chat *and* `payments.charge` | Double charge, double tokens | Workflow-Id + Stripe-style keys on tools only |
| **NEG / LB cap** | GKE 50 NEG | Cannot add cluster/port | Fewer ports; fewer zones on that Gateway; split Gateways |
| **Image supply-chain skip** | `:latest` unsigned CUDA image | Silent CVE / backdoor | Cosign+Kyverno; pin digest |

**Error budget mapping.** A rolling deploy that drops 2% of streams for 15 minutes on a 99.9% monthly SLO is not “zero downtime” — compute it. Google: a 1,500-error incident on a 3,000-error budget is **50%** of the budget. Freeze features when budget is burned; that includes **model swaps**.

---

## 6. Enterprise System Design Scenarios

### 6.1 Scenario A — Interactive chat, 99.9% availability, TTFT SLO

**Requirements:** multi-tenant SaaS chat; prefix-heavy; streaming; PII in EU.

| Decision | Choice | Reject | Why |
| --- | --- | --- | --- |
| Serving | vLLM + GIE/llm-d prefix routing; minReplicas ≥ 2 per AZ | Scale-to-zero | Cold start > TTFT budget |
| Ingress | GKE Inference Gateway or Envoy AI Gateway Tier-1+2; Model Armor | L4 NLB only | Body-based model routing + cache-aware pick |
| State | Sticky via EPP; no KV multi-AZ | Global anycast to random replica | KV is not in the session cookie |
| Agents | Temporal for tools; HTTP for tokens | Kafka for every token | Tokens need SSE; tools need durability |
| Security | mTLS + tenant RPM/TPM; distroless workers; signed GPU images | Shared API key to vLLM | Noisy neighbor + audit |

**NFR [inferred]:** size decode pool from measured TPOT at target concurrency; keep prefill on a separate pool if p95 TTFT fails while TPOT is fine (classic monolith symptom).

### 6.2 Scenario B — Burst batch / overnight summarization

| Decision | Choice | Reject | Why |
| --- | --- | --- | --- |
| API | SQS or Kafka + KEDA `minReplicaCount: 0` | Always-on H100s | Hours of idle |
| GPU | Spot + interruption drain; on-demand overflow | 100% spot | Involuntary eviction |
| SLO | Throughput + eventual completeness; DLQ for poison | Chat TTFT | Different user clock |
| Drain | Job or long `terminationGracePeriodSeconds` | 30s | KEDA long-running warning |

**Cost:** this is where scale-to-zero + Karpenter earns back GPU-hours. Keep **activationThreshold** so one probe message doesn’t wake a p5.

### 6.3 Scenario C — Multi-model LoRA factory

GKE/GIE: many LoRAs on one base + adapter-aware scoring. Trade-off: **density vs noisy neighbor** (HBM for adapters + KV). Quota per `(tenant, adapter)`. Canary: new adapter = new InferenceObjective / model name, 10% split, not a fleet restart.

### 6.4 Scenario D — Regulated agent with MCP tools

| Layer | Control |
| --- | --- |
| Identity | User JWT → gateway; MCP OAuth PRM; RFC 8707 resource |
| Tools | Envoy `MCPRoute` allowlist; CEL on params; no raw shell MCP in prod |
| Execution | Temporal activities + idempotency keys; sandbox (Kata/gVisor) for untrusted code |
| GPU | Isolated NodePool; optional Confidential Containers on Hopper (EA, SEV-SNP, all-GPUs-on-node constraint) |
| Audit | OTel + MCP method/tool; no prompt in Prometheus labels |

### 6.5 Scenario E — 70B+ multi-node TP/EP

LWS + RDMA + RWX PVC + **autoscaler none** on that ServingRuntime path (KServe). Scale by **LWS replicas**, not by HPA on a single leader. EPP still load-balances *across* LWS groups. Failure domain = the LWS (lose leader → lose the replica). PDB on LWS groups, not random pods.

### 6.6 Trade-off matrices

**HPA vs KEDA vs Knative**

| | HPA | KEDA | Knative Serving |
| --- | --- | --- | --- |
| Scale to 0 | No | Yes | Yes (HTTP) |
| Custom PromQL | Adapter required | Native | Activator metrics |
| Async queues | Awkward | Native (Kafka/SQS) | Poor fit |
| Interactive HTTP | OK if min≥1 | OK | Built for it; still GPU-cold-start bound |

**Monolith vLLM vs P/D**

| | Monolith | Disaggregated P/D |
| --- | --- | --- |
| Ops | One Deployment | Router + two pools + NIXL |
| TTFT vs TPOT isolation | HOL blocking | Independent scaling |
| Network | Intra-node | Same-AZ RDMA |
| When | <7–13B, one GPU | Long context + high concurrency (KServe: high throughput) |

**Kafka vs SQS vs Temporal vs Redis Streams**

| Need | Pick |
| --- | --- |
| Replay, fan-out, 100k+ msg/s | Kafka |
| Least ops, AWS-native workers | SQS + DLQ |
| Multi-step agent, HITL, exactly-once *business* logic | Temporal |
| Short retention, PEL, already on Redis | Streams |

**Canary surfaces (use all three, different blast radii)**

| Surface | What you canary | Rollback |
| --- | --- | --- |
| HTTPRoute / model name | App/model weights | Weight → 0 |
| Argo Rollouts | Gateway binary / agent | RS revert; keep stable at 100% capacity |
| GPU Operator nodeSelector | Driver/toolkit | Git revert; 48h bake recommended in operator-canary recipes |

### 6.7 Interview close

A Principal AI Architect drawing “K8s + GPUs” is junior. The production diagram is: **signed distroless/CUDA images → GPU Operator/MIG → Karpenter NodePools → vLLM/LWS with PDB and drain → GIE/Envoy picking on KV not RR → KEDA on queue/TTFT not CPU → Temporal/Kafka for side effects → OAuth MCP PEP → SLOs on TTFT/TPOT with an error budget that includes deploys.** The token factory is the data plane. Everything else exists to keep it from being scheduled like a stateless web app.

---

## Sources

1. https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/overview.html
2. https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html
3. https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-mig.html
4. https://docs.nvidia.com/datacenter/cloud-native/gpu-telemetry/latest/dcgm-exporter.html
5. https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/25.10/confidential-containers.html
6. https://docs.nvidia.com/datacenter/cloud-native/confidential-containers/latest/configure-workloads.html
7. https://docs.nvidia.com/nim/large-language-models/3.0.0/reference/logging-and-observability.html
8. https://docs.nvidia.com/nim/large-language-models/2.0.4-pb6/deployment/kubernetes-deployment/nim-operator-deployment.html
9. https://docs.nvidia.com/dynamo/dev/user-guides/observability-local/metrics
10. https://github.com/NVIDIA/aicr/blob/main/docs/integrator/supply-chain-verification.md
11. https://kserve.github.io/website/docs/model-serving/generative-inference/llmisvc/llmisvc-overview
12. https://kserve.github.io/website/docs/model-serving/generative-inference/llmisvc/llmisvc-config-composition
13. https://kserve.github.io/website/docs/model-serving/generative-inference/llmisvc/llmisvc-dependencies
14. https://kserve.github.io/website/docs/0.19/admin-guide/kubernetes-deployment-llmisvc
15. https://kserve.github.io/website/docs/model-serving/generative-inference/multi-node
16. https://docs.vllm.ai/en/stable/deployment/integrations/production-stack/
17. https://github.com/vllm-project/production-stack
18. https://github.com/vllm-project/production-stack/blob/main/docs/source/use_cases/disaggregated-prefill.rst
19. https://llm-d.ai/
20. https://github.com/llm-d/llm-d-router
21. https://gateway-api-inference-extension.sigs.k8s.io/
22. https://gateway-api-inference-extension.sigs.k8s.io/guides/implementers/
23. https://github.com/kubernetes-sigs/gateway-api-inference-extension
24. https://github.com/kubernetes-sigs/lws
25. https://aigateway.envoyproxy.io/
26. https://aigateway.envoyproxy.io/docs/api/
27. https://aigateway.envoyproxy.io/docs/capabilities/mcp/
28. https://aigateway.envoyproxy.io/blog/mcp-implementation/
29. https://github.com/envoyproxy/ai-gateway/
30. https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/upstream/circuit_breaking.html
31. https://keda.sh/docs/2.17/concepts/scaling-deployments/
32. https://karpenter.sh/docs/concepts/
33. https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/
34. https://kubernetes.io/docs/tasks/run-application/configure-pdb/
35. https://docs.aws.amazon.com/eks/latest/userguide/ml-inference-autoscaling-hpa-keda.html
36. https://aws.amazon.com/blogs/machine-learning/introducing-auto-scaling-on-amazon-sagemaker-hyperpod/
37. https://aws.amazon.com/blogs/machine-learning/best-practices-to-run-inference-on-amazon-sagemaker-hyperpod/
38. https://aws.amazon.com/blogs/machine-learning/disaggregated-prefill-and-decode-for-llm-inference-on-sagemaker-hyperpod/
39. https://aws.amazon.com/blogs/architecture/unlock-efficient-model-deployment-simplified-inference-operator-setup-on-amazon-sagemaker-hyperpod/
40. https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-dead-letter-queues.html
41. https://github.com/aws-samples/sample-eks-cache-aware-llm-routing
42. https://cloud.google.com/kubernetes-engine/docs/concepts/about-gke-inference-gateway
43. https://docs.cloud.google.com/kubernetes-engine/docs/concepts/about-gke-inference-gateway
44. https://cloud.google.com/kubernetes-engine/docs/how-to/deploy-gke-inference-gateway
45. https://cloud.google.com/binary-authorization/docs/cv-slsa-check
46. https://docs.cloud.google.com/binary-authorization/docs/deploy-cloud-build
47. https://github.com/GoogleContainerTools/distroless
48. https://learn.microsoft.com/en-us/azure/application-gateway/for-containers/how-to-inference-gateway
49. https://docs.temporal.io/production-deployment/worker-deployments/kubernetes-controller
50. https://docs.temporal.io/encyclopedia/detecting-activity-failures
51. https://temporal.io/blog/activity-timeouts
52. https://temporal.io/blog/of-course-you-can-build-dynamic-ai-agents-with-temporal
53. https://go.temporal.io/platform-hub/ai-engineering/ai-reference-architecture
54. https://temporal.io/blog/announcing-openai-agents-sdk-integration
55. https://developers.openai.com/api/docs/guides/rate-limits
56. https://developers.openai.com/api/docs/guides/error-codes
57. https://developers.openai.com/api/docs/guides/streaming-responses
58. https://developers.openai.com/api/reference/overview/
59. https://docs.stripe.com/api/idempotent_requests
60. https://stripe.com/blog/idempotency
61. https://sre.google/workbook/implementing-slos/
62. https://github.com/sigstore/cosign
63. https://redis.io/docs/latest/develop/use-cases/streaming/
64. https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/authorization
65. https://mcp.mintlify.app/specification/2025-11-25/basic/authorization
66. https://preliminary.istio.io/latest/docs/tasks/traffic-management/ingress/gateway-api-inference-extension/
67. https://argoproj.github.io/argo-rollouts/features/traffic-management/
68. https://www.thundercompute.com/blog/aws-p5-vs-thunder-compute
69. https://www.devzero.io/instances/aws/p5.48xlarge
70. https://developers.redhat.com/articles/2026/03/03/practical-strategies-vllm-performance-tuning
71. https://repost.aws/articles/AR08CYM7xFQyqcUKH8Oxo6LA/why-adding-consumers-won-t-fix-your-consumer-lag-on-amazon-msk-head-of-line-blocking
72. https://www.elastic.co/observability-labs/blog/openai-rate-limit-monitoring
73. https://www.spheron.network/blog/llm-d-kubernetes-disaggregated-inference-guide/
74. https://tianpan.co/blog/2026-06-02-the-autoscaler-that-scaled-to-zero-mid-decode
75. https://kubernetes.recipes/recipes/deployments/gpu-operator-upgrade-canary/
76. https://oh-bug.com/posts/llm-gpu-self-healing-production-guide/
77. https://codingwithtaz.blog/2026/05/13/production-ready-gpu-inference-autoscaling-on-eks-with-karpenter-keda-and-dragonfly/
78. https://cast.ai/blog/kubernetes-gpu-autoscaling/
79. https://devfloor9.github.io/engineering-playbook/en/docs/agentic-ai-platform/model-serving/gpu-infrastructure/gpu-resource-management
