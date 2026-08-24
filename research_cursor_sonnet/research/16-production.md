# Research: Production — Docker, Kubernetes, APIs, Queues, Scaling, Reliability

**Date researched**: 2026-08-22
**Sources consulted**: 41

## 1. System Topology & Mechanics

### Control plane / data plane separation
- The control plane is the management layer (config, policy, service discovery, RBAC, audit logging) and **never sits in the direct hot path of live traffic**; the data plane is the forwarding layer that executes decisions on every request [26]. This mirrors Kubernetes itself: the control plane (API server, scheduler, controller manager) decides where workloads run; the data plane (kubelet, kube-proxy on worker nodes) executes and routes traffic [26].
- Control plane → data plane synchronization uses three models: **push** (real-time updates, e.g., Envoy xDS), **pull** (periodic fetch), or **hybrid** [27]. Data planes should cache last-known-good config so a control-plane outage doesn't halt traffic forwarding [27].
- For AI-specific stacks, an **AI control plane** additionally governs RBAC/auth, token-cost tracking, PII redaction, and prompt-level guardrails *before* a request reaches the AI data plane (model inference, MCP tool invocation) [29][30]. Policy enforcement happens pre-execution, analogous to network-layer traffic filtering [29].
- **Agent Gateway** pattern: a reverse-proxy control layer purpose-built for agent-to-agent (A2A) and agent-to-tool (MCP) traffic — distinct from a classic API gateway (HTTP) or LLM gateway (model endpoints) — natively understanding MCP/A2A protocols and multi-step workflows, applying auth/rate-limit/cost-attribution/audit at every hop [30].

### Kubernetes pod/deployment topology for agent & inference workloads
- **GPU scheduling stack (2026 state of the art)**: NVIDIA donated Dynamic Resource Allocation (DRA) driver to CNCF at KubeCon EU 2026; DRA APIs reached GA in Kubernetes 1.34 (`resource.k8s.io/v1`, stable, default-enabled) [1]. DRA replaces the decade-old device-plugin model with an API-driven device allocation framework, but is *allocation-only* — you still need Kueue for batch-fairness quota admission and an inference runtime (vLLM/KServe) above it [1].
- **KAI Scheduler** (NVIDIA, CNCF Sandbox) runs as a secondary scheduler alongside `kube-scheduler` specifically for GPU AI workloads, providing: gang scheduling (all pods of a multi-GPU job start together or none start), fair-share queuing (per-team GPU quota), priority-based preemption, and bin-packing (consolidate onto fewest nodes to preserve contiguous GPU blocks) [1].
- **Grove** (NVIDIA) manages lifecycle of multi-pod GPU serving deployments (prefill / decode / router role separation), handling startup ordering and gang-scheduling constraints for disaggregated inference topologies [1].
- **HAMi-core** closes the "governance gap": KAI Scheduler's GPU sharing does cooperative accounting only by default (does not enforce memory limits or isolate processes); HAMi-core intercepts CUDA calls at runtime to enforce memory quotas as a hard, verifiable contract rather than a trust-based agreement [1].
- Layered ownership model: GPU Operator owns node software lifecycle → KAI owns scheduling/accounting → HAMi Core owns runtime enforcement [1].
- Even with gang scheduling solved, **clusters commonly stall at 20–30% GPU utilization** because placement ≠ right-sizing: most inference pods reserve far more GPU fraction than they use, and no scheduler continuously re-sizes fractional allocations [1][4].
- Disaggregated serving topology (prefill/decode/router pods co-located in the same NVLink domain) is now standard for large-scale LLM serving via Grove + KAI [1][4].

### API layer topology for agent services (sync / async / streaming)
- Treat inference/agent execution as a **resource lifecycle**, not a single long-running HTTP call: API accepts + records work (job resource) → a worker performs execution → client reads the result via a stable status URL [7]. This decouples request-response semantics from the actual (potentially minutes-long) agent run.
- Standard production pattern for streaming: **two decoupled SSE connections** — one server-to-server (backend to LLM/agent runtime) and one client-to-server (browser to API) — bridged through a durable store (Postgres/Redis) rather than piping bytes directly [6]. This turns "pipe bytes to a socket" into "write rows to a table, let clients poll/subscribe," enabling reconnection without gaps.
- Batch DB writes from streaming token deltas (e.g., flush every 100ms) — a write-per-token is prohibitively expensive at LLM token rates (dozens of events/sec) [6].
- Resumable streams require a **monotonically increasing event ID** per stream; clients track last-received ID and reconnect via `GET /job/{id}/stream/{last_event_id}` [6][10]. Producer execution is decoupled from the HTTP connection lifecycle — it keeps running as a background task/process even if the client disconnects [10].
- NVIDIA's AI-Q Blueprint reference architecture: synchronous requests flow directly through a FastAPI frontend to the agent workflow; asynchronous/long-running requests (deep research) use a distributed execution cluster (Dask) with SSE progress streaming and a `stream.mode` announcement (`polling`, `live`, or `pubsub` via Postgres LISTEN/NOTIFY) [10].

### Queue-based task distribution topology
- Canonical topology: producer → durable queue/log → independent consumer pool, decoupling agent orchestrators from worker execution so failure isolation and independent scaling are possible [20][21].
- Agent-specific guidance (AWS Well-Architected Agentic AI Lens): keep message payloads compact — pass S3 URIs / DB keys / document IDs, not inline data; let the consumer fetch bytes on demand [21].
- Consumer-group pattern (Kafka, Redis Streams): each agent *type* gets its own consumer group on a shared stream/topic; multiple workers within a group auto-balance load; failed tasks after retry exhaustion move to a dead-letter stream [23].

> ⚠️ Data gap: No official CNCF/Kubernetes reference architecture diagram was found that standardizes "agent mesh" topology end-to-end (control plane + queue + inference + gateway) — the space is fragmented across vendor blogs (Kagenti, Solo.io, TrueFoundry) rather than a ratified spec as of Aug 2026.

## 2. Token Economics & NFR Metrics

### Infrastructure cost vs. token cost trade-offs
- **Serverless vs. Kubernetes crossover point**: multiple sources converge on **50–100K requests/day** or **~$5,000–$10,000/month** steady-state spend as the point where Kubernetes/dedicated capacity becomes cheaper than serverless per-invocation pricing [17][18][19]. Below that, serverless (Modal, Cloud Run) wins on zero idle cost [17][18].
- Concurrent-agent-run framing: **<10 concurrent agent runs** → serverless wins (zero idle, fast scale-up); **10–50 mixed** → managed durable platforms (Temporal/Prefect) win on state durability; **50+ steady** → Kubernetes wins on $/GPU-hour; **50+ bursty** → hybrid (managed orchestration + K8s workers) [17].
- Concrete GPU pricing (2026): Modal H100/A100/T4/L4 at $0.59–$3.95/hr with <1s cold start; Cloud Run L4 ~$0.67/hr with <5s GPU cold start; Lambda Cloud A100 80GB at $1.29/hr (~3x cheaper than AWS on-demand, $929/mo vs. ~$2,470/mo for sustained 24/7 inference; self-hosting break-even in 3–6 months) [18].
- At >1,000 inferences/minute, reserved-instance Kubernetes drops to **$0.0003/inference**, matching or beating serverless; steady 24/7 workloads on right-sized K8s nodes achieve 70–85% utilization [19].
- Serverless GPU offerings cost **1.5x–3x more per second** than on-demand instances; cold starts for large models range 20–60s (weight loading + CUDA init + compilation) — sub-second-response requirements effectively force a "warm" (always-on) replica, eliminating the serverless cost advantage [17].
- Scale-to-zero economics: an idle H100 bills the same as a busy one, so KEDA's ability to scale inference deployments to **zero replicas** (which native Kubernetes HPA cannot do — HPA floor is 1) is the entire economic case for bursty inference autoscaling [2].
- Prompt/tool-result caching is frequently a **larger cost lever than model choice**: Clay (350M agent executions/month) reports up to **70% cost savings** from strategic prompt caching [25]; a separate production case study reports **35%+** compute reduction from prompt + tool-result caching [25].
- Model cascading/routing (cheap model for simple tasks, frontier model reserved for complex reasoning) reported to yield **~60% cost savings** in production agentic pipelines [25].

### Latency SLAs for production agent APIs
- AWS EKS reference config for vLLM autoscaling: scale up when **p95 end-to-end latency exceeds 5 seconds**, used as an SLO guardrail trigger *in addition to* queue depth (>25 waiting requests/pod) [2].
- Google SRE latency SLO pattern: define **multiple percentile thresholds** to capture both typical and tail experience, e.g., "90% of requests < 400ms AND 99% of requests < 850ms" — a single average masks unhappy tail users [12][13].
- Multi-region reference dashboard thresholds: p95 request latency alert at >3s; queue depth alert at >100 pending; error rate alert at >1%; vector-DB replication lag alert at >30s [24].

### Autoscaling cost/latency trade-offs
- KEDA's core trade-off knobs: `activationThreshold` (0→1 transition, tolerates brief lulls before scale-to-zero) vs. `threshold` (1→N, handed to standard HPA) [2][3]. `cooldownPeriod` (default 300s) prevents a transient lull from prematurely killing the whole fleet [2].
- Two distinct autoscaling signals must **not** be conflated: queue depth (`vllm:num_requests_waiting`) is the scale-*out* trigger (add replicas); KV-cache fill (`vllm:kv_cache_usage_perc`) is a per-replica *saturation guard* feeding concurrency limits/alerts, not a scale trigger — "wire the first to KEDA and the second to your concurrency limits, don't cross the streams" [2].
- Queue-based (vs. live-metric) scaling is more predictable for async workloads: pods-needed = queue_length / per-pod-throughput, computable in closed form [3].

### Queue throughput / backpressure
- RabbitMQ throughput ceiling: tens of thousands of msgs/sec per queue; degrades under sustained high-throughput before Kafka does [21]. SQS: scales via API-call billing (pull-based; polling costs accrue even when idle — a workload-mismatch trap [20]). Kafka: designed for very high throughput + long retention + replay, at the cost of operational complexity [20][21].
- Backpressure design principle: "a queue does not absorb overload — it converts a fast failure into a slow one, and if unbounded, converts a slow failure into a total one" [23]. Bound queues so producers block naturally; return `429`/`503` with `Retry-After` to remote producers rather than silently dropping; expose estimated wait time so well-behaved clients can self-throttle [23].
- Production default retry/DLQ policy: **retry 3 times with backoff, then park in DLQ** — more retries delays recovery detection during genuine outages and stacks up wasted (billed) LLM-call cost [22][23].

## 3. Distributed Resilience & State

### Durable execution (Temporal)
- Core rule: **all non-deterministic I/O (LLM calls, tool invocations, external API/db calls) must live in Activities, never in Workflow code** — Workflows are replayed from event history on recovery, and a direct LLM call inside a Workflow would produce a different result on replay, triggering a non-determinism error that fails the workflow [8][9].
- The Workflow is the "durable brain": holds conversation state, schedules Activities, waits for results; it never calls external services directly [8].
- **Heartbeats** enable long-running Activities (batch processing, multi-minute tool calls) to report progress and resume from last checkpoint after a Worker crash, rather than re-running from scratch — detects stuck ("zombie") activities faster than a bare execution timeout [11].
- **Continue-As-New** is required for long-running agent loops to prevent Event History from growing unboundedly; every agent loop must have a **hard iteration cap enforced in code** (not left to model judgment) — production teams report failures from agent loops running 500+ iterations with no cap [9].
- Disable client-library retries for LLM SDKs; let Temporal's Activity Retry Policy own all retry/backoff — single configuration point, durable across crashes, consistent behavior, better observability [8].
- Multi-agent fan-out pattern: run parallel Activities with `return_exceptions=True` (or equivalent) to continue with partial results when some parallel searches/subagents fail, rather than failing the whole fan-out [8].

### Kubernetes-native resilience
- Three-probe model: **startup probe** (delays liveness/readiness checks until slow init — e.g., model weight loading — completes; critical for vLLM/LLM-serving pods where a 21-minute startup window is not unusual: `failureThreshold: 120 × periodSeconds: 10` = 1260s), **liveness probe** (detects deadlock/unrecoverable state → restarts container; must check *only* internal process health, never external dependencies — checking a DB in a liveness probe causes healthy pods to be restarted during a DB outage, i.e., self-inflicted cascading failure), **readiness probe** (controls Service-endpoint traffic eligibility, can check dependency availability, failure removes pod from load balancer without restarting it) [14][15].
- **Pod Disruption Budgets (PDBs)**: set for any Deployment with >1 replica; use `maxUnavailable: 1` or `minAvailable: 50%`; avoid `maxUnavailable: 0` (blocks cluster maintenance entirely); set `unhealthyPodEvictionPolicy: AlwaysAllow` so misbehaving (CrashLoopBackOff) pods can still be evicted during node drains rather than blocking them indefinitely [14][15].
- Additional resiliency patterns bundled together: topology spread across ≥2 zones, pod anti-affinity, `preStop` sleep ≥5s (survives Service endpoint-propagation race during rolling termination), resource requests/limits on every container [15].

### Circuit breakers, rate limiting, and provider fallback
- Layered resilience stack for LLM gateways: **rate limiting → circuit breaker → fallback chain** [16].
- Circuit breaker sliding-window defaults converging across sources: **failure-rate threshold 25–50%** over a **10–20 call sliding window** opens the circuit; **wait/cooldown 30–60s** before probing HALF_OPEN; **2–3 permitted probe calls** in HALF_OPEN before fully closing [16].
- Circuit state must be shared via a **distributed store (Redis)** across all gateway instances/workers/pods so independent nodes don't each independently hammer a failing provider — e.g., 8 Gunicorn workers × 3 pods sharing one Redis-backed circuit state [16].
- Fallback chain skips providers whose *own* circuit is already open rather than attempting and re-failing them — e.g., `[OpenAI, Anthropic, Gemini]` where Anthropic's circuit is open routes an OpenAI failure straight to Gemini [16].
- Only retry **transient** errors (429, 5xx) with exponential backoff; never retry permanent errors (auth/billing) — wastes budget and adds latency without recovery chance [16].
- Reported production outcome from layering circuit breakers + timeout budgets + backoff: cascade failures **847/month → 0**; uptime **94.2% → 99.97%**; MTTR **12 min (manual) → 45 sec (automatic)**; resource waste during outages **~$2,400/mo → ~$50/mo** [22] `[reported by vendor blog, treat as illustrative not verified benchmark]`.

### Distributed locking / multi-region / multi-AZ
- Multi-AZ handles single-datacenter/hardware failure with fast failover (1–2 min) and low latency; it does **not** protect against a full regional outage (explicitly cited: October 2025 AWS regional outage as the cautionary example) [39].
- Multi-region patterns: **active-active** (every region serves live traffic, lowest latency/highest availability, requires conflict resolution for stateful data + full model replication); **active-passive** (secondary region as warm/cold standby, most common "multi-region" usage in practice); **hub-and-spoke** (central control plane/model registry in one region, lightweight inference spokes elsewhere — introduces a single point of failure at the hub unless the hub itself is multi-AZ) [37][38].
- Failover engineering: define RTO/RPO **before** setting health-check interval; a 30s check interval with a 3-failure threshold ≈ 90s to failover — acceptable for active-passive DR, too slow for active-active SLA compliance [37].
- Regulatory routing constraint pattern (GDPR): EU user requests must **never** route to US regions even during failover — routing logic must encode geographic/compliance hard constraints ahead of pure availability optimization [24].

## 4. Enterprise Security & Governance

### Zero-Trust MCP in Kubernetes (network policy, service mesh, mTLS)
- **Istio Ambient Mode** is the emerging 2026 default for zero-trust AI agent/MCP meshes: replaces per-pod sidecars with a shared per-node `ztunnel` (Rust) proxy, enforcing L4 mTLS + SPIFFE workload identity **without injecting anything into the agent pod** — important because sidecar memory overhead compounds badly next to resource-heavy LLM inference containers [31][32].
- ztunnel enforces **L4 only**; any L7-level control (HTTP method restriction, MCP tool-level authorization) requires an additional **waypoint proxy** at the same HBONE enforcement hop, inheriting the already-proven SPIFFE identity chain [32][33].
- **AI-native waypoint (agentgateway)** parses MCP natively and evaluates CEL expressions against the specific tool being called — unauthorized tools are hidden from `tools/list` and rejected on `tools/call`, meaning the agent never even sees tools it isn't authorized to use [33].
- Enforcement recipe: label namespaces `istio.io/dataplane-mode=ambient` → enforce `PeerAuthentication` in `STRICT` mTLS mode → apply L4 `AuthorizationPolicy` for workload-identity-based allow-lists → route MCP traffic through an `AgentgatewayBackend` (kind: mcp) + `HTTPRoute` for L7 tool-level policy [31][32][33].
- OTel GenAI semantic-convention attributes now standardized for this layer: `gen_ai.provider.name`, `gen_ai.usage.input_tokens`/`output_tokens`, `mcp.method`, `mcp.session.id`, `mcp.tool.name` — every log entry carries the caller's SPIFFE identity [33].
- This architecture converts the cluster from an "open network" into a "policy-defined execution graph": agents reach only approved LLM providers and required MCP servers; MCP servers reach only designated data sources; all else is blocked by default [31].

### Kubernetes RBAC + application-level RBAC for agents
- Canonical binding chain: `ServiceAccount → Role → RoleBinding → Namespace` — **never** `ClusterRoleBinding` for agent workloads, and never rely on the `default` service account [34][35].
- Two-identity separation pattern: a **diagnostic** identity (get/list/watch only, explicitly excludes `secrets` and `pods/exec`) and a separate, narrowly-scoped **remediation** identity gated by human approval + audit logging + rollback path — never combine the two [34].
- Hardening checklist: `automountServiceAccountToken: false` on pods that don't need API access; generate policy from **observed runtime calls** (`audit2rbac`, `rakkess`) rather than guessing; enforce Pod Security Admission in `restricted` mode; verify with `kubectl auth can-i` after every change; treat even *read-only* Secrets access as a compromise of least-privilege because Secrets often contain DB creds/API keys/signing keys [34][35].
- 2026 shift beyond static RBAC: because agents dynamically chain tools, static manifests can't capture actual runtime risk — emerging guidance (CSA MAESTRO, CNCF March 2026 agentic standards) points to **SPIFFE/SVID short-lived credentials** + **just-in-time scoped permission grants** tied to a specific task/execution context, replacing "just-in-case" standing access [34][36]. Cited stat: Teleport survey found **67% static-credential reliance** among orgs running agents [36]; NHIMG research found **80% of organizations report agents already acted beyond intended scope** [34].

### PII redaction pipelines in production
- Redaction must be a **mandatory, centralized, non-optional** layer at every trust boundary, not per-agent middleware someone can forget to enable [40][41].
- Four canonical interception points in an agent loop: (A) ingress user prompt, (B) outgoing tool-call arguments, (C) incoming tool results, (D) outgoing final response [40][41]. A single "redact only the first user message" pass is insufficient — every subsequent hop (tool selection, tool output re-injected as context, retrieval) is a fresh chance to leak or ingest new PII [40].
- Preferred technique: **reversible pseudonymization** over blunt redaction/suppression — replace PII with realistic, contextually coherent pseudonyms scoped to a session entity map, then revert to real values only in the final response at boundary D — this preserves multi-turn utility better than suppression [40][41].
- Detection stack combines regex/Luhn (structured data: SSNs, card numbers) with NER models (spaCy/BERT/Presidio) for unstructured text [40].
- Observability must also be scrubbed: OTel span processors should store only tokens/masks in traces, never raw PII values [40][41].
- Reported production result after redaction-pipeline hardening: regulated-field leak rate fell to **0.2%**, false-positive blocks on legitimate checks dropped from **8% → 1.1%**, incident-packet generation time dropped from days to **18 minutes** [41] `[vendor-reported, not independently verified]`.

### Sandbox isolation (gVisor / Kata in Kubernetes)
- Three isolation tiers via Kubernetes `RuntimeClass`: **runc** (standard containers, shared host kernel, trusted workloads only) → **gVisor** (`runsc`; user-space kernel "Sentry" intercepts syscalls before host kernel, ~20–100%+ overhead on syscall-heavy workloads, moderate-trust) → **Kata Containers** (each pod gets a dedicated microVM + guest kernel via KVM, 5–15% overhead, hardware-enforced isolation for genuinely adversarial/untrusted LLM-generated code) [5][28].
- Startup latency trade-off: gVisor adds no VM boot time (<1s, same as runc); Kata adds 100–500ms VM startup, offset by using **Firecracker** as the VMM backend (fastest of QEMU/Cloud Hypervisor/Firecracker options, ~125ms boot, ~5MB memory overhead — the backend powering AWS Lambda, E2B, Vercel Sandbox) [5][28].
- Kubernetes-native standardization: Google's **Agent Sandbox** (CRD + Operator, backend-agnostic, supports both gVisor and Kata) provides a declarative API specifically for stateful, isolated AI agent code-execution workloads, avoiding vendor lock-in to a proprietary sandboxing SaaS [5].
- Recommendation convergence: gVisor when isolation-stronger-than-container-but-VM-overhead-unacceptable is the requirement; Kata/Firecracker as the default for multi-tenant agents executing arbitrary LLM-generated code [5][28].
- Direct pod port-forwarding is incompatible with these secure runtimes in production — a **Sandbox Router** is required to handle traffic into gVisor/Kata-isolated pods [5].

### Audit logs
- SOC 2 (CC6.1, CC7.2, CC8.1) now explicitly extends to AI agents: agents must be treated as **managed identities** with unique, non-repudiable IDs bound to an owner — not shared service accounts [40][42][43].
- Required audit-trail fields converge across sources: identity binding, verbatim intent/prompt capture with timestamp, full tool-call sequence (parameters + return values), decision rationale/reasoning chain, affected-data lineage, output sensitivity classification, and cryptographic tamper-evidence (hash-chained, append-only, e.g., S3 Object Lock in Compliance mode) [42][43][44].
- PII redaction must happen **at write time**, before logs reach storage — "once PII is in the log, every downstream access becomes a data-protection concern" [44].
- Cited compliance gap stat: only **38% of organizations monitor AI activity end-to-end**, and just **17% track agent-to-agent interactions** (EY/AIUC-1 Consortium, 2026); **7 in 10 enterprise AI deployments in 2026 ship without complete audit trails** [42][44].
- Tiered log retention pattern: operational logs 30–90 days, compliance logs 1–7 years (regulatory-driven), debug logs 7–14 days [44].

## 5. Production Failure Modes

### Pod crash loops & OOM kills (vLLM/inference-specific)
- **Two distinct OOM failure classes, both surfacing as exit code 137, requiring opposite fixes**: (1) **Host RAM OOM** — kernel cgroup OOM killer fires because container RSS exceeds `resources.limits.memory`; kubelet event shows "Memory cgroup out of memory"; fix = raise the memory limit. (2) **GPU VRAM OOM** — `torch.cuda.OutOfMemoryError` in application logs (KV cache exceeds available VRAM); fix = lower `--gpu-memory-utilization`, lower `--max-model-len`, or lower `--max-num-seqs` [PagedAttention KV-cache pre-allocation is the root cause — vLLM reserves the entire KV cache on startup with no graceful degradation] [46][47][48].
- Diagnostic sequence: `kubectl describe pod` (check "Last State: Terminated, Reason: OOMKilled") → `kubectl logs --previous --tail=200` to distinguish which OOM class occurred [46][47].
- Common misconfiguration: `--gpu-memory-utilization 0.95` leaves no headroom for the CUDA allocator itself, causing OOM even at idle; recommended production value ~0.85–0.9 [48][47].
- Readiness-probe race condition: pod passes readiness before KV cache is fully allocated, then OOMs under the first real concurrent load — fix via `startupProbe` with generous `failureThreshold` (observed real config: `initialDelaySeconds: 60, periodSeconds: 10, failureThreshold: 120` = 21 minutes total budget for large-model load) rather than inflating the liveness probe's `initialDelaySeconds` [46][49].
- Missing PVC for model cache is a recurring "production incident waiting to happen": without a `ReadWriteOnce` PVC (sized 2x model file size) mounted at the HF cache path, every pod restart re-downloads full model weights (e.g., a 140GB re-download) [49].
- Prefix-caching interaction: turning on KV prefix caching can *itself* cause OOM if prompts don't share prefixes — cached blocks sit idle and starve the live KV cache; only enable when workload genuinely has stable shared prefixes (system prompts, RAG persona blocks) [48].

### Autoscaling thrashing / flapping
- Root cause identified across every source: **default scale-up `stabilizationWindowSeconds` is 0** while scale-down defaults to 300s — this asymmetry means HPA reacts instantly to every metric blip on the way up [50][51][52].
- Classic feedback loop: new (cold-start) pods report artificially low CPU → average CPU drops below threshold → HPA scales down → remaining pods' CPU spikes → HPA scales up → repeat indefinitely, sometimes every 10–15 seconds [51][52].
- Fix pattern converged across sources: set explicit `behavior.scaleUp.stabilizationWindowSeconds` (60–120s) AND `behavior.scaleDown.stabilizationWindowSeconds` (300s, conservative); change scale-up policy from a fixed pod count (`pods: 4`) to `percent: 50` for proportional scaling; gate new pods out of the HPA metric average via a readiness probe with `initialDelaySeconds: 30` until fully warm [52].
- A second, distinct thrashing cause: leaving `spec.replicas` set in the Deployment/StatefulSet manifest fights the HPA controller on every `kubectl apply` — official Kubernetes docs explicitly recommend removing `spec.replicas` entirely once HPA is enabled [50].
- Conflicting autoscalers: running HPA and VPA on the *same metric* creates oscillation (VPA changes resource requests → HPA's per-pod math shifts → unnecessary rescale) [51].

### Queue backlog buildup
- Backlog death spiral, documented precisely: arrival rate exceeds service rate → queue grows → latency grows (latency = queue time + service time) → clients hit their own timeouts and retry → retry traffic *increases* arrival rate further → by the time anyone notices, the queue contains e.g. 20 minutes of already-expired work, and the system spends 100% of capacity producing answers that get discarded on arrival [23].
- For LLM-backed queues specifically, this is worse than generic backlogs because (a) service time is seconds not milliseconds so the queue drains slowly, (b) every queued item is **billed** even if discarded, and (c) retries cost real money, not just CPU — the amplification loop has a direct financial cost attached [23].
- Mitigation requires shedding **early**, not late: bound queue length so a synchronous producer blocks naturally at capacity; return `429`/`503` with `Retry-After` (never a bare rejection with no timing hint — that invites immediate retry, the opposite of the intended effect) [23].

### Cascading failures across microservices/multi-agent systems
- Documented real incident: a single authentication-service failure cascaded to **47 downstream agents** in one production multi-agent system — attributed to AI agents lacking the well-defined APIs and predictable failure modes of traditional microservices; a downstream agent doesn't fail gracefully when an upstream agent hallucinates or times out, it enters an *undefined* state that propagates unpredictably [43a/OWASP source: 45].
- OWASP ASI08 (Agentic AI Top 10, 2026) formalizes "Cascading Failures" with four named amplification mechanisms: **feedback loops** (agents whose outputs are each other's inputs create self-reinforcing error growth), **trust transitivity** (A trusts B trusts C; if C is compromised, A accepts corrupted output without independent verification), **memory persistence** (errors written to long-term memory/vector stores keep influencing reasoning even after the source is fixed), and **scope escalation** (excessive agency turns a localized hallucination — "delete all files" — into a system-wide action) [45].
- Mitigation validated in production case study (2.3M requests/month, 847 cascades/month before fix): per-agent circuit breakers + intelligent per-agent timeout budgets + exponential backoff with jitter as a three-layer standard resilience pattern, cutting cascades to zero and improving uptime 94.2%→99.97% [22].
- Recommended blast-radius success criterion from chaos-testing practice: **no single agent failure should affect more than 2 downstream agents** [43a].
- Health-check design lesson: a `/healthz/liveness` endpoint that doesn't verify LLM-provider reachability lets the UI keep accepting messages that will inevitably fail — frustrated users resubmit, doubling load during an outage ("amplification cascade" via user retry behavior, not just system retry behavior) [43b].

> ⚠️ Data gap: numeric outcomes in sections 2, 3, and 5 sourced from vendor/practitioner blog posts (Clay, LinkedIn case studies, dev.to) rather than peer-reviewed benchmarks — treat as illustrative order-of-magnitude, not verified industry-wide averages. Flagged inline as `[vendor-reported]` above.

## 6. Enterprise System Design Scenarios

### Published production deployment benchmarks
- **Clay (Claygent)**: 350M+ agent executions/month across 40M companies and 900M contacts, processing trillions of tokens/week [25]. Migrated from AWS Lambda to ECS with durable workflow execution patterns for reliability; implemented **adaptive rate limiting modeled on TCP/IP congestion control**, achieving 4–10x throughput improvement; prompt caching yielded up to 70% cost savings; bounded retries for cost control [25].
- Survey of 306 practitioners + 20 in-depth case studies (arXiv, 2026) on production agent deployments: 42.9% of deployments serve user bases in the *hundreds*; 25.7% are high-traffic (tens of thousands to 1M+ daily users) [25].
- Domain-specific ROI figures (self-reported, treat as illustrative): Siemens supply-chain agents — 25% reduction in disruptions, 15% lower procurement cost, 300% first-year ROI, 30% decrease in unplanned downtime; SuperAGI CI/CD agents — 75% reduction in deployment time, 90% increase in test coverage, 60% reduction in downtime [25] `[vendor-reported]`.

### Architecture case studies / trade-off matrices

**Serverless vs. Kubernetes vs. VMs decision matrix** [17][18][19]:

| Workload shape | Recommended | Why |
|---|---|---|
| 1–10 concurrent agent runs, spiky | Serverless (Modal, Cloud Run) | Zero idle cost, fast scale-up, cold starts tolerable |
| 10–50, mixed complexity | Managed durable (Temporal, Prefect, Step Functions) | Durable state, retry-heavy logic, audit trail, predictable cost |
| 50+, steady utilization | Kubernetes (+ KEDA, Argo) | Best $/GPU-hour once utilization is high; full control over scheduling/networking |
| 50+, bursty | Hybrid: managed orchestration + K8s worker pool | Durability + elastic compute together |
| GPU training/fine-tuning | Kubernetes (always) | Serverless platforms have limited/no GPU support; need node-pool + MIG/fractional GPU control |

- Break-even heuristics across sources: **~$5–10K/month steady spend**, **50–100K requests/day**, or **>40–60% daily utilization** all independently point to the same crossover from serverless-favored to Kubernetes-favored economics [17][18][19].
- Real-world pattern: **most mature 2026 AI platforms don't pick one** — Kubernetes for training + heavy/latency-critical inference, serverless for bursty/lightweight inference and event-driven orchestration/preprocessing [17][19].

**Message-queue selection decision tree** [20]:
- Need multi-tenancy + geo-replication → Apache Pulsar.
- Need complex routing (exchanges, RPC, per-message TTL, dead-letter topologies) at moderate throughput → RabbitMQ.
- Serverless + fully AWS-native, simple decoupling → SQS (+ SNS for fan-out).
- IoT/edge, sub-ms latency → NATS.
- Already on Redis, moderate scale → Redis Streams.
- Need event replay / long retention / multiple independent consumers of the same stream / very high throughput → Kafka (the only realistic option for replay-driven audit/debug use cases).

**Sandbox isolation selection matrix** [5][28]:

| Isolation need | Runtime | Overhead | Startup |
|---|---|---|---|
| Trusted internal workloads | runc (standard) | Baseline | <1s |
| Moderate-trust, cost-sensitive | gVisor (`runsc`) | 20–100%+ on syscall-heavy | <1s (no VM) |
| Untrusted/adversarial LLM-generated code | Kata + Firecracker | 5–15% | 100–500ms (Firecracker) |

### Capacity planning
- vLLM/GPU capacity math: right-size `--max-model-len` to your **measured p99 context length**, not the model's theoretical max — a 32K `max-model-len` setting pre-allocates KV cache for 32K-token sequences even if concurrent requests never approach that length, exhausting VRAM before serving a single real request [47][48].
- Load-test methodology: profile single-user session resource consumption first, then extrapolate to multi-tenant concurrent load — "a pod that survives at idle does not prove it survives at peak concurrency" [46][48][25].
- Capacity signal hierarchy for autoscaling admission control at agent-traffic scale: traditional CPU-based HPA is a *lagging* indicator for agentic traffic; production teams are shifting to metering by **token/resource cost** rather than raw request count, with TCP/IP-style congestion-control-inspired admission control to handle agent retry storms and prevent runaway loops from self-amplifying [25].
- GPU fleet utilization reality check: even with gang scheduling and DRA solved, published 2026 sources report clusters commonly plateau at **20–30% GPU utilization** due to over-reservation relative to actual per-pod usage — closing this gap requires continuous right-sizing tooling (e.g., ScaleOps-style continuous bin-packing), not just better initial placement [1][4].

## Sources
- [1] https://www.spheron.network/blog/kubernetes-gpu-orchestration-2026/ — Kubernetes GPU orchestration 2026: DRA, KAI Scheduler, Grove, HAMi-core
- [2] https://dreaming.press/posts/autoscaling-llm-inference-on-kubernetes.html — Autoscaling LLM inference on K8s via queue depth, not GPU metrics
- [3] https://agentbus.sh/posts/how-to-autoscale-llm-inference-on-kubernetes/ — KEDA queue-based scaling for LLM inference with Redis
- [4] LinkedIn (Nicolas Vermandé, ScaleOps) — GPU utilization wall at 20-30%, gang scheduling vs. sizing
- [5] https://northflank.com/blog/sandboxes-on-kubernetes — gVisor/Kata isolation options, Agent Sandbox, RuntimeClass
- [6] https://www.kitewing.ai/blog/stateless-agents-stateful-product/ — Resilient multi-user SSE streaming architecture via DB decoupling
- [7] https://blogs.oracle.com/developers/how-to-build-a-rest-api-for-an-ai-application — REST API design for AI: job-resource lifecycle pattern
- [8] https://go.temporal.io/platform-hub/ai-engineering/ai-reference-architecture — Temporal AI agent reference architecture
- [9] https://www.xgrid.co/resources/temporal-ai-agent-orchestration-failure-patterns/ — 11 production pitfalls for Temporal AI agent orchestration
- [10] https://docs.nvidia.com/aiq-blueprint/2.1.0/architecture/data-flow.html — NVIDIA AI-Q Blueprint data flow: sync/async job architecture
- [11] https://docs.temporal.io/design-patterns/long-running-activity — Temporal heartbeat pattern for long-running activities
- [12] https://sre.google/sre-book/service-level-objectives/ — Google SRE book: SLI/SLO/error budget definitions
- [13] https://sre.google/workbook/implementing-slos/ — Google SRE workbook: implementing SLOs, latency SLO examples
- [14] https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/ — Official K8s probe configuration docs
- [15] https://kubernetes.recipes/recipes/deployments/kubernetes-resiliency-patterns/ — K8s resiliency patterns: PDB, probes, topology spread
- [16] https://mdsanwarhossain.me/blog-llm-gateway-production.html — LLM gateway production: circuit breakers, fallback chains, caching
- [17] https://www.gmicloud.ai/en/blog/kubernetes-vs-serverless-vs-managed-ai-agent-scaling — K8s vs serverless vs managed platforms for AI agent scaling
- [18] https://blog.starmorph.com/blog/ai-agent-deployment-cloud-platforms-compared — Cloud platform comparison table for agent deployment, GPU pricing
- [19] https://cirocloud.com/artikel/kubernetes-vs-serverless-for-ai-the-real-cost-comparison — K8s vs serverless real cost comparison
- [20] https://www.youngju.dev/blog/culture/2026-03-22-message-queue-kafka-rabbitmq-sqs-comparison-2025.en — Kafka vs RabbitMQ vs SQS vs Pulsar vs NATS decision guide
- [21] https://letsbuildsolutions.com/blog/system-design/how-message-queues-work-kafka-rabbitmq-sqs-compared/ — How message queues work: throughput/retention comparison
- [22] LinkedIn (Afolabi) — Building production-ready AI agents: multi-layer resilience pattern, cascade metrics
- [23] https://multigrid.ai/learn/backpressure — Backpressure and queue depth in AI pipelines
- [24] https://jameshu.io/books/ai-engineering/reference/19a_multi_region_architecture.html — Multi-region inference architecture reference with routing logic
- [25] https://www.zenml.io/llmops-database/scaling-go-to-market-ai-agents-at-production-scale — Clay/Claygent production scale case study
- [26] https://www.truefoundry.com/blog/control-plane-vs-data-plane — Control plane vs data plane, AI-specific extension
- [27] https://api7.ai/learning-center/api-gateway-guide/api-gateway-control-plane-vs-data-plane — API gateway control/data plane sync models
- [28] https://www.systemshardening.com/articles/kubernetes/runtimeclass-gvisor-kata/ — gVisor vs Kata RuntimeClass isolation deep dive
- [29] https://www.truefoundry.com/blog/agent-gateway — Agent Gateway architecture and MCP/A2A handling
- [30] https://truto.one/blog/how-to-implement-pii-redaction-when-passing-saas-data-to-llms-via-mcp/ — PII redaction at MCP boundary, zero-retention proxy
- [31] https://t0.mirantis.com/agents-mcp-on-k8s-pt2/ — Zero Trust for agents/MCP on Kubernetes with Istio Ambient
- [32] https://next.redhat.com/2026/03/05/zero-trust-ai-agents-on-kubernetes-what-i-learned-deploying-multi-agent-systems-on-kagenti/ — Kagenti zero-trust multi-agent deployment learnings
- [33] https://www.solo.io/blog/from-service-mesh-to-agentic-mesh — Service mesh to agentic mesh, agentgateway MCP waypoint
- [34] https://dev.to/ajey_k_b3b392e7c4138059db/kubernetes-rbac-for-ai-agents-least-privilege-patterns-that-actually-matter-5aee — K8s RBAC least-privilege patterns for AI agents
- [35] https://dev.to/futhgar/kubernetes-rbac-building-least-privilege-service-accounts-27ca — Building least-privilege K8s service accounts
- [36] https://blog.devops.dev/your-kubernetes-rbac-was-not-built-for-ai-agents-heres-exactly-where-it-breaks-e94a47cc2eb6 — Where K8s RBAC breaks for AI agents, SPIFFE/JIT access
- [37] https://mlflow.org/articles/what-is-multi-region-ai-deployment/ — Multi-region AI deployment guide for cloud architects
- [38] https://aws.plainenglish.io/multi-az-vs-multi-region-choosing-your-aws-resilience-strategy-ha-vs-dr-bc59a795fd0a — Multi-AZ vs multi-region resilience strategy
- [39] https://garystafford.medium.com/considerations-when-architecting-resilient-multi-region-workloads-7eacbad71de9 — Considerations for resilient multi-region workloads
- [40] https://philterd.ai/blog/redact-pii-before-sending-to-an-llm/ — PII redaction architecture for chat/RAG/agents
- [41] https://solana.garden/guides/llm-agent-pii-detection-redaction-pipeline-systems-explained/ — PII detection/redaction pipeline: token vaults, audit-safe traces
- [42] https://www.miniorange.com/blog/ai-agent-compliance-challenges/ — AI agent compliance: GDPR, HIPAA, SOC2, EU AI Act
- [43] https://roval.ai/research/blog/soc-2-ai-agents/ — SOC 2 for AI agents: auditor expectations
- [44] https://docs.aws.amazon.com/es_es/wellarchitected/latest/agentic-ai-lens/agentops05-bp03.html — AWS Well-Architected: structured logging and audit trails for agents
- [45] https://adversa.ai/blog/cascading-failures-in-agentic-ai-complete-owasp-asi08-security-guide-2026/ — OWASP ASI08 cascading failures guide
- [46] https://www.kubenatives.com/p/vllm-oomkilled-recovery-kubernetes-runbook — vLLM OOMKilled production runbook
- [47] https://markaicode.com/errors/vllm-process-killed-kubernetes-oom-fix/ — vLLM OOMKilled root causes and production config
- [48] https://www.sector88.co/blog/how-to-fix-vllm-oom — Complete 2026 vLLM OOM fix checklist
- [49] https://www.kubenatives.com/p/how-vllm-serves-models-kubernetes — vLLM on Kubernetes: PagedAttention, OOM fixes, production config
- [50] https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/ — Official K8s HPA docs: thrashing/stabilization window
- [51] https://jasonbutz.info/2024/07/kubernetes-hpa/ — HPA pod creation thrashing root cause (spec.replicas conflict)
- [52] https://thecodeforge.io/devops/kubernetes-hpa-autoscaling/ — HPA flapping: cold-start pods trigger scale-down/up cycle
- Additional corroborating sources reviewed: dev.to (Preventing Cascading Failures in AI Agents, OWASP ASI08 walkthrough), Brandon Lincoln Hendricks (ADK cascading failure dependency management, 47-agent incident), Google Cloud Blog (SRE error budgets and maintenance windows; building good SLOs), arXiv 1702.05843 (Chaos Engineering principles paper), Netflix Tech Blog (Chaos Engineering Upgraded; ChAP platform), AWS (What is a Dead-Letter Queue), AWS Well-Architected Agentic AI Lens (agentperf04-bp01, queue-based agent coordination), niteagent.com (Agent Coordination Layer with Message Queues), antigravitylab.net (Flow Control for Autonomous Agents), Docker Blog (Secure AI Agents at Runtime), blaxel.ai (Container Escape / microVM isolation), soguru.in / jacar.es / undercodetesting.com (Docker containerization best practices for agentic workflows), manveerc.substack.com (AI agent sandboxing guide 2026), katacontainers.io (Kata + Agent Sandbox integration announcement), Red Hat Developer (KServe autoscaling with KEDA), keda.sh and AWS EKS docs (KEDA scalers reference), Berkeley EECS-2025-203 (Efficient Systems for LLM Agents at Scale — Agentix, Starburst), Towards Data Science (Three Generations of Autoscaling for Agentic Traffic), Medium/aimonks (7 Layers of Production-Grade Agentic AI System), arXiv 2512.04123 (Measuring Agents in Production survey), teamazing.com (AI Agent Audit Trail + RBAC 2026 requirements), mintmcp.com (AI agent governance framework).
