# Research: Inference & Optimization — Caching, Routing, Batching, Quantization

**Date researched**: 2026-08-22
**Sources consulted**: 34

## 1. System Topology & Mechanics

### 1.1 Serving architecture: control plane vs. data plane
Modern LLM inference stacks split cleanly into a **control plane** (configuration, policy, secrets, routing rules) and a **data plane** (live request processing). This mirrors service-mesh design (Istio/Envoy) applied to AI traffic:

- **Control plane** (e.g., Envoy AI Gateway Controller): watches Custom Resources, expands them into proxy config, pushes via xDS. Owns policy definitions, backend security policies, token-based rate-limit metadata, and ExtProc sidecar lifecycle. [21]
- **Data plane**: Envoy Proxy + an **External Processor (ExtProc)** sidecar that does AI-specific work — model-name extraction, request transformation, token accounting — plus a Rate Limit Service for token-budget enforcement. [21]
- Commercial equivalents (Kong Konnect, LiteLLM, Bifrost, TrueFoundry AI Gateway) follow the same split: a managed/shared control plane for keys, budgets, and policy, with data-plane proxy nodes deployed close to traffic. [21][14]

### 1.2 Caching layer placement — three distinct cache types
1. **KV cache (GPU-resident, token-level)**: Managed inside the inference engine itself (vLLM/SGLang/TensorRT-LLM), not a separate service. Placement is layered:
   - **GPU HBM** — active working set (hot).
   - **CPU DRAM** — "warm" overflow tier, pinned memory for fast GPU↔CPU transfer (e.g., LMCache `local_cpu`). [17]
   - **Local disk/NVMe (GDS)** — large-capacity tier for long documents. [17]
   - **Remote/distributed** (Mooncake, Redis, InfiniStore, S3) — persistent, cross-node pool; Mooncake aggregates DRAM+SSD across a cluster into one addressable KV store. [18][17]
   - LMCache is the "glue" layer that lets vLLM/SGLang move KV chunks across these tiers asynchronously with LRU eviction, and survive process restarts when backed by disk/remote storage. [17]
2. **Prefix/PagedAttention cache (within-engine, block-based)**: vLLM's PagedAttention partitions KV cache into fixed-size blocks mapped via a block table (OS-paging analogy), enabling non-contiguous storage, near-zero fragmentation, and **block-level sharing within and across requests**. [1][2] SGLang's **RadixAttention** achieves the same goal at **token-level granularity** using a radix tree (trie with variable-length edges) as the cache-key structure, with LRU eviction of leaf nodes first (to preserve shared ancestors longest). [4][5]
3. **Prompt/semantic cache (application layer, response-level)**: Sits in front of the model call, outside the inference engine — e.g., GPTCache, LangChain/LlamaIndex integrations, cloud offerings (AWS Bedrock, Alibaba Higress, Azure). Stores full query→response pairs keyed by embedding similarity via a vector store + similarity evaluator. [8][9] Provider-side prompt caching (Anthropic, OpenAI, Google) is a third variant: a managed, provider-hosted cache of KV-cache-equivalent state keyed by exact prefix match, not semantic similarity.

### 1.3 Router architecture
Two dominant router designs:
- **Learned classifier routers** (RouteLLM): matrix-factorization or causal-LLM classifiers trained on human-preference data (e.g., Chatbot Arena) to predict whether a strong or weak model is needed per query, with a tunable **cost threshold**. [10][11]
- **Commercial routing services** (Martian, NotDiamond, OpenRouter): hosted endpoints exposing a `model: "router"` abstraction with a `max_cost_per_million_tokens` knob (Martian) or per-request judge/confidence scoring (NotDiamond). Added routing decision latency is typically **100–200 ms** per hop (embedding pass or small classifier call). [13]
- **Cascading routers**: start cheap, escalate to a stronger model when a lightweight judge's confidence drops below a threshold — "confidence-based escalation." [24][25]
- Routing decisions are logically **downstream of the AI-gateway policy/authorization decision** — the router only selects among an already-authorized endpoint set, and its choice is logged as an *operational* record correlated (via request ID) to a separate *compliance* audit record. [33]

### 1.4 Batching scheduler mechanics
- **Static/naive batching** (baseline): waits for a full batch before executing; ~30–40% GPU utilization. [12]
- **Continuous (iteration-level) batching** (vLLM default, SGLang, TensorRT-LLM "in-flight batching", LMDeploy "persistent batching", HF TGI): admits/evicts requests every iteration rather than waiting for the whole batch to finish, eliminating idle GPU slots from variable-length outputs. Reported **2–3x throughput** vs. static batching alone, up to **23–28x** when stacked with PagedAttention + kernel tuning. [3][12][22]
- **Chunked prefill / stall-free batching (Sarathi-Serve)**: splits long prefill computation into token-budget-sized chunks interleaved with ongoing decode steps so a long prompt's prefill never fully stalls in-flight decodes. Naive hybrid batching (prefill blocking decode) causes up to **28.3x** worse time-between-tokens (TBT) versus decode-only batches; stall-free/chunked batching bounds this tightly. [20][23]
- **Multi-step scheduling** (vLLM v0.6+): runs the scheduler once and executes N consecutive model steps without CPU round-trips per step, cutting CPU-overhead-induced GPU idle time — measured **28% throughput gain** on Llama 70B / 4×H100. [3]
- **SLO-aware / adaptive schedulers** (FlowPrefill, 2026): decouple *preemption granularity* from *scheduling frequency* using operator-level preemption instead of fixed chunk sizes, to hit TTFT SLOs without sacrificing throughput on heterogeneous request mixes. [19]

### 1.5 Quantization pipeline — offline vs. online
- **Offline (post-training quantization, PTQ)**: calibrate on a representative dataset, then bake weights (and optionally activations/KV cache) into a lower-precision checkpoint before deployment.
  - **GPTQ**: layer-by-layer, second-order-error-minimizing weight quantization; broad ecosystem support, huge number of pre-quantized community checkpoints. [15]
  - **AWQ**: activation-aware — profiles activation magnitudes during calibration to identify the ~1% of "salient" weight channels, protects them at higher precision, quantizes the rest aggressively; calibrates **5–10x faster** than GPTQ. [15][16]
  - **GGUF**: llama.cpp/Ollama format; supports Q2_K–Q8_0 mixed-precision "K-quants," hybrid CPU+GPU inference (splits layers across VRAM and system RAM). [15]
  - **NVIDIA Model Optimizer**: generates FP8/FP4/NVFP4/MXFP4 checkpoints for TensorRT-LLM; pre-quantized models can also be pulled directly from Hugging Face. [7]
- **Online / runtime**: FP8 KV cache can be toggled at serve time in TensorRT-LLM even for checkpoints not natively quantized (`kv_cache_dtype fp8`); vLLM similarly exposes KV cache dtype as a serve-time flag. [7][3]
- **Kernel dependency**: quantization format is coupled to GPU-generation-specific kernels — **Marlin** (Ampere/Ada, handles both GPTQ and AWQ INT4 at group_size 128) vs. **Machete** (Hopper-optimized successor, GPTQ-only as of 2026). Format choice without matching kernel support can leave 8x performance on the table (GGUF on vLLM/H200 measured at ~93 tok/s vs. ~741 tok/s for AWQ+Marlin). [16]

## 2. Token Economics & NFR Metrics

### 2.1 Caching cost savings (quantified)
| Cache type | Write cost multiplier | Read cost multiplier | Net savings | Source |
|---|---|---|---|---|
| Anthropic prompt cache (5-min TTL) | 1.25x base input | 0.1x base input | Up to 90% on cached portion | [6] |
| Anthropic prompt cache (1-hour TTL) | 2x base input | 0.1x base input | Same read discount, break-even ~6 reads/hr | [6] |
| OpenAI prompt cache (pre-GPT-5.6) | No extra fee | 0.5x (50% discount) | 50% on cached tokens | [7 disc.] |
| OpenAI prompt cache (GPT-5.6+) | 1.25x uncached rate | 0.1x uncached rate | Up to 90% on reads | [7 disc.] |
| Google Gemini cache | Standard input cost | 0.25x (75% discount) | 75% on cached tokens | [7 disc.] |
| Self-hosted vLLM prefix caching | N/A (compute only) | N/A | 30–60% throughput gain on shared-prompt workloads; "free 30% win" | [3] |

Real-world case: a developer's Claude bill dropped from **$720/month → $72/month (90% reduction)** by caching a stable system prompt across high-volume requests. [6]

Latency impact of prompt caching (Anthropic, measured): [6]
| Scenario | TTFT w/o cache | TTFT w/ cache | Reduction |
|---|---|---|---|
| Chat with a book (100K cached tokens) | 11.5s | 2.4s | −79% |
| Many-shot prompting (10K cached tokens) | 1.6s | 1.1s | −31% |
| 10-turn convo, long system prompt | ~10s | ~2.5s | −75% |

> ⚠️ Anthropic changed the *default* cache TTL from 1 hour to 5 minutes in early 2026; older articles/benchmarks citing 1-hour defaults are stale. [6]

### 2.2 Quantization cost/throughput gains (quantified)
- FP8 vs FP16 on H100 (TensorRT-LLM): **1.3–1.8x throughput**, ~40% memory reduction, <1% accuracy loss on standard benchmarks; with a first-token latency constraint (<500ms), FP8 + batch size 16 gives a **2.3x speedup** on Llama-2-7B. [7]
- FP8 KV cache vs FP16 KV cache: enables **2–3x larger batch size** on H100 for models like GPT-J, translating to ~**1.5x** additional performance benefit. [7]
- INT8 SmoothQuant increases throughput **30–50%** with minimal quality loss (TensorRT-LLM guidance: prioritize FP8 first, fall back to Int8 SQ, then AWQ/GPTQ). [7]
- Together AI production data: FP8/FP4 quantization delivers **20–40% throughput improvement** without harming output quality; kernel fusion + smarter MoE execution + scheduling nets **20–50% faster decoding**. [22]
- Quantized "distilled" reasoning models can achieve **2–5x lower cost** at similar quality bands for many tasks vs. full-precision frontier models. [22]
- Custom speculators (Together AI) reduce GPU-hours needed to generate 1B tokens by **23–26%** vs. base speculator, **49–61%** vs. no speculative decoding. [22]

### 2.3 Batching throughput/latency (quantified)
- Continuous batching alone: **2–3x** throughput over static batching; combined with PagedAttention + FP8 KV + chunked prefill + tuned `max-num-batched-tokens`: **~1.7x** cumulative gain in one documented progression (4,200 → 7,100 tok/s on vLLM 0.7 baseline). [3]
- Chunked prefill: **−50 to −70% p95 TTFT** on mixed workloads (documented case: 32K-token inputs, p95 TTFT 2,800ms → 890ms, a 68% reduction) at the cost of a slight p50 TTFT increase. [12][23]
- PagedAttention: **2–4x** more concurrent requests per GPU-memory budget; reduced KV cache waste from an estimated 60–80% (naive contiguous allocation) to **under 4%**. [1][12]
- MLPerf Inference v6.0 (April 2026) headline numbers: NVIDIA GB300 NVL72 — **2.49M tokens/sec** (Offline) / **1.56M tokens/sec** (Server) on DeepSeek-R1 across 72 GPUs, a **2.77x** improvement over its own v5.1 debut six months prior on identical hardware (software-only gains). AMD Instinct MI355X crossed **1M tokens/sec** on Llama 2 70B (Server + Offline) and GPT-OSS-120B (Offline) at multi-node scale, with 92–98% scale-out efficiency. [26][27][28]
- Speculative decoding (EAGLE-3): **3.0–6.5x** speedup over vanilla autoregressive decoding, ~1.4x over EAGLE-2; vLLM's EAGLE-3 integration delivers up to **2.5x** speedup in production; NVIDIA reports **3.6x** throughput improvement on H200. Effectiveness degrades at large batch sizes as the workload shifts from memory-bound to compute-bound (EAGLE's benefit crosses over below 1.0x speedup at batch size ≥48 in one benchmark; EAGLE-3 sustains benefit to batch ~56). [29][30][31][32]

### 2.4 Model routing cost savings (quantified)
- RouteLLM (LMSYS): **up to 85%** cost reduction on MT-Bench, **45%** on MMLU, **35%** on GSM8K vs. GPT-4-only, while retaining **95%** of GPT-4's quality score; best router needs only **~54% of GPT-4 calls** after data augmentation (down from a naive 50/50 split) to hit the 95% quality bar. [10][11]
- RouteLLM vs. commercial routers (Martian, Unify AI) at matched MT-Bench quality: RouteLLM requires **>40% fewer** calls to the strong model — i.e., it is >40% cheaper for equivalent output quality. [10][11]
- General economics: routing only pays off when (a) the strong/weak price gap is large and (b) a meaningful fraction of real traffic is genuinely answerable by the cheap model — measure your own "routable share" rather than trusting vendor headline numbers (some vendors claim up to 97% savings; realistic, workload-validated savings cluster **20–40%** for a mixed enterprise workload). [11][13]

### 2.5 Latency SLAs by technique (p50/p95/p99)
- Production SLA example (Baseten/vLLM-class serving, GLM-5.2 on B300s): target mean **TTFT ≤ 2.5s**, mean **TPOT ≤ 20ms**; disaggregated 4-Prefill+1-Decode topology took mean TPOT from **~40ms → ~17ms** through parallelism-strategy changes (not the max-raw-throughput config). [3 disc.]
- vLLM p99 TTFT failure mode: bursty/mixed-length traffic produces **bimodal** TTFT distributions — p50 fine, p95 **5–10x worse** — caused by long prefills blocking in-flight decodes; this is a *scheduling* problem, not a raw-compute ceiling. [12][19]
- Baseten's "fast" API variant trades throughput for latency (Tensor+Expert Parallelism only, vs. Attention Data Parallelism for the general API) at a **50% price premium** on input/output tokens on identical B200 hardware. [22 disc.]

## 3. Distributed Resilience & State

### 3.1 Durable execution for inference pipelines
- Standard pattern (Temporal-based, applicable to any durable-execution engine): wrap every LLM call and tool invocation in an **Activity**; the **Workflow** holds conversation state and never calls the LLM directly (direct in-workflow LLM calls break determinism on replay). [34]
- Activities get built-in configurable retry policies; best practice is to classify errors explicitly:
  - HTTP 429 → honor `Retry-After` header via `next_retry_delay`, don't blindly exponential-backoff.
  - HTTP 400/422 → `non_retryable=True`, fail fast.
  - HTTP 500/502/503/529 → transient, moderate exponential backoff with jitter. [34]
- Disable SDK-level auto-retries (`max_retries=0`) when a durable-execution layer owns retries, to avoid **retry amplification** (SRE book's classic 3-layer-retry example: 3×3×3 = 27 upstream attempts from one user action). [23 disc., resilience search]
- On worker crash, the workflow replays event history and re-hydrates completed LLM/tool call results from the log rather than re-invoking them — critical for cost control (no duplicate billed LLM calls) and idempotency. [34]

### 3.2 Distributed cache consistency
- **KV cache**: Mooncake/LMCache provide a distributed pool but explicitly favor throughput over strict consistency — remote backends (Redis, Mooncake, InfiniStore) are described as "reliable but not as performant" and used as an overflow/persistence tier, not a synchronously-consistent primary store. [17][18]
- **Semantic/prompt cache**: consistency is fundamentally probabilistic — cache hits are determined by embedding-similarity thresholds, not exact keys, so "consistency" here really means **collision risk management** (see §5.1).
- **Cache salting** (vLLM): to prevent timing-based side-channel attacks in multi-tenant KV-cache-sharing deployments, a secret per-user/per-team salt is mixed into cache keys so identical prompts from different tenants don't produce observably identical cache behavior. Recommended for any multi-tenant vLLM deployment regardless of confidential-computing usage. [PII/cache search result 5]

### 3.3 Circuit breakers for inference backend failures
Canonical three-layer resilience stack (documented consistently across TrueFoundry, BackendBytes, Inferbase, Maxim): [drop 9-13]
1. **Retry** (same candidate, transient blip assumption) — owned by exactly one layer (usually SDK) to avoid amplification.
2. **Circuit breaker** (Closed → Open → Half-Open state machine) — trips on 5xx/529/timeouts/mid-stream errors, explicitly **not** on the caller's own 429s; while open, requests fail fast instead of paying the full timeout cost.
3. **Fallback chain** — ordered list of alternates: same model via a different cloud/region first (preserves product behavior), then a different model (changes behavior, needs pre-approval), then a self-hosted last resort.
- Concrete breaker parameters: failure threshold (count or % over a rolling window), cooldown/timeout period before probing, success threshold to close, and single-probe half-open testing. [9][12]
- Documented amplification failure: **one user action → 27 upstream attempts** when handler (3x), SDK (3x), and gateway (3x) all retry independently. [10]

### 3.4 Rate-limiting fallbacks
- Distinguish **infrastructure failover** (outage-driven) from **cost-driven routing** (budget-driven) — conflating them causes fallback chains to blow past latency/cost budgets. [24]
- Health checks for failover routing should hit **liveliness** endpoints, not **readiness** endpoints — readiness typically returns 503 whenever a shared database is unreachable, which would incorrectly pull every region out of rotation simultaneously during a DB blip. [16 multi-region]

### 3.5 Multi-region inference failover
Two architectural paths: [15/16/17/18 multi-region search]
1. **Managed global routing** (AWS Bedrock Cross-Region Inference, Azure Global Standard): near-zero implementation effort, automatic capacity-based rerouting, but explicitly a **capacity mechanism, not a DR mechanism** — does not protect against model/provider disruptions, and **may route "beyond geographic boundaries"** to any supported region, creating data-residency exposure that shows up as a compliance finding, not an outage alert.
2. **Custom multi-region AI gateway** (LiteLLM pattern): active-active or active-passive DNS-routed gateway instances, one shared PostgreSQL for global config/budget/key state, one Redis **per region** (kept in-region to avoid cross-region round trips on every rate-limit check). Failure mode: if the shared Postgres goes down, **all regions** lose DB access simultaneously — mitigate with `allow_requests_on_db_unavailable: true` (serve from cache) and multi-AZ Postgres.
- Residency-vs-availability tension is a **policy decision, not an engineering default**: an EU-region outage cannot blindly fail over to US-East if GDPR/data-residency terms prohibit it; the routing/policy layer must evaluate residency rules **before** dispatching the failover.

## 4. Enterprise Security & Governance

### 4.1 Zero-Trust for model-serving infrastructure
- Traditional RBAC is considered **too coarse** for AI control planes; production zero-trust designs favor **ABAC/CapBAC** (attribute- or capability-based) policies evaluated per-request against caller identity, resource sensitivity, and action context (e.g., OPA/Rego sidecars). [drop 1-5 zero trust]
- Reference architecture layers: mTLS-authenticated identity (often SPIFFE-based) → API-gateway JWT validation → sidecar ABAC policy check → mTLS-encrypted micro-segmented network path (deny-all-by-default Kubernetes NetworkPolicies) → orchestrator with least-privilege downstream access.
- Non-human identities (agents, orchestrators, tool runners) get **short-lived, scoped, dynamically-issued credentials** rather than static API keys — tokens expire quickly and are unusable outside their intended invocation context.

### 4.2 RBAC for model/router access
- Enterprise AI-gateway RBAC (e.g., Bifrost) models permissions as (resource × operation) pairs across protected resources: virtual keys, model providers, guardrails, MCP gateways/tool groups, audit logs, adaptive routing config, user provisioning. [drop 3]
- **Access profiles** bundle allowed providers/models, budgets, rate limits, and MCP tool access; attaching a profile to a role auto-provisions every user in that role with independent budget counters — eliminates hand-issued raw credentials.
- RBAC answers "was this change **permitted**"; audit logs answer "**how** did the system get here" — these are distinct guarantees and an incident investigation needs both.

### 4.3 PII redaction before caching (cache-poisoning/leakage risk)
This is a documented, non-hypothetical gap area:
- **Retention mismatch**: prompt caches are engineered for hit-rate (LRU by memory budget), not for regulatory retention rules — "a hot key in a high-traffic shared system can sit in Redis for months," meaning the **user-message content (the part most likely to carry PII) is exactly the part determining cache-key retention**. [drop 1 PII]
- **GDPR Article 17 (right to erasure) conflict**: as of early 2026, **no major LLM provider offers a per-entry cache-eviction API** — you cannot selectively force-delete one user's cached prompt content on request. [drop 2 PII]
- **Cross-tenant cache-hit legal ambiguity**: if Provider X's cache serves User B a cached response originally generated from User A's prompt, it is an **open legal question** whether this constitutes the provider processing User A's data on User B's behalf under GDPR. [drop 2 PII]
- **Mitigation pattern**: redact PII **at the request boundary**, before the prompt reaches any caching or logging layer — not at query/read time (redacting only when displayed leaves the raw PII sitting in the warehouse/cache in the interim). [drop 3, 4 PII]
- **Timing side-channel**: vLLM's prefix cache shows a measurable response-time gap between cache hits and misses; an attacker with backend access can time responses to infer whether a specific (possibly sensitive) prompt was previously cached, then reconstruct it step-by-step. Mitigation: **cache salting** — mix a secret per-user/team salt into every cache-key block so identical prompts from different tenants don't collide observably. [drop 5 PII]

### 4.4 Semantic cache poisoning / key-collision (a governance and security issue)
- Formal 2026 finding: semantic caching's embedding-similarity key matching is a **locality-preserving fuzzy hash** that does not satisfy the avalanche property required for collision resistance — this is an **inherent design vulnerability**, not an implementation bug. [8][9]
- Attack mechanics: an attacker crafts an adversarial query `Q_adv` semantically similar (in embedding space) to a target query `Q_target` but designed to elicit a harmful/incorrect response; once `⟨Q_adv, R_poison⟩` is cached, any future user issuing `Q_target` receives `R_poison`. [8]
- Cache-related threats extend beyond text-to-text: a 2026 NDSS study (covering vLLM, SGLang, GPTCache, AIBrix, rtp-llm, LMDeploy) identified six attack-vector classes including **blockwise/multimodal collisions used to bypass content-moderation checks** — all vendors acknowledged the vulnerabilities after responsible disclosure. [8/9]
- Defenses: response-level validation before serving a cache hit, cluster-centroid indexing (raising the bar for adversarial collision crafting), namespace isolation per tenant, and treating semantic-cache hit rate improvements as a security/quality trade-off, not a free win.

### 4.5 Sandbox isolation for custom inference code
- **Root cause**: GPU device nodes (`/dev/nvidia0`, `/dev/nvidiactl`, `/dev/nvidia-uvm`) are mounted directly into containers by the standard `nvidia-device-plugin`; `ioctl()` calls pass straight through to the host kernel's NVKM driver — **no IOMMU isolation, no per-pod kernel context**. Container/namespace boundaries do **not** provide hardware-level isolation for GPU workloads. [drop 1 sandbox]
- **gVisor + nvproxy**: user-space kernel intercepts and proxies GPU-related syscalls, restricting `ioctl()` calls to an allowlisted, driver-version-specific set via seccomp filters — strong defense-in-depth against host-kernel exploits, but **does not protect against vulnerabilities inside the NVIDIA driver itself** for any `ioctl` gVisor passes through. [drop 3 sandbox]
- **MicroVMs (Kata Containers) with GPU passthrough (VFIO)**: hardware-enforced isolation boundary, but requires nested virtualization or physical device passthrough — heavier operationally.
- **MIG (Multi-Instance GPU)**: the primary mechanism for **hard** memory/compute partitioning on a single physical GPU; without MIG or dedicated per-tenant hardware, co-tenants risk memory-snooping/side-channel exposure and **55–145% latency degradation** from noisy-neighbor effects under shared scheduling. [drop 2 sandbox]
- Practical recommendation: dedicated node pools (K8s taints/tolerations) for untrusted/multi-tenant custom-code workloads, plus strict egress network policy (compute isolation alone doesn't block model-weight exfiltration over the network).

### 4.6 Audit logs of routing decisions
- Best-practice separation: the **audit record** (compliance-grade, hash-chained/HMAC-signed, append-only) captures identity, policy version, data-classification tag, and decision outcome — but explicitly **not raw prompt/response content** (only SHA-256 fingerprints of input/output in some implementations, to satisfy traceability without creating new PII exposure). The **router's operational log** separately records the actual model/endpoint chosen; the two are correlated via a shared request ID. [drop 1-5 audit]
- EU AI Act Article 12 compliance logging requires "automatic recording of events" for high-risk AI systems; documented implementations export tamper-evident JSONL with a verifiable hash chain (any modification/deletion/reorder breaks the chain from that point forward). [drop 4 audit]
- Design principle: policy evaluation happens **before** routing dispatch ("Budget → Policy → PII → Guardrails → Routing" filter chain), so a DENY decision means the prompt never reaches any model provider and never needs a routing-cost audit entry. [drop 3 audit]

## 5. Production Failure Modes

### 5.1 Cache staleness / poisoning
- **Semantic cache key-collision attacks** (see §4.4) — the most severe class, since it causes **response hijacking**, not just staleness: a malicious actor's cached response is served to a legitimate user querying something merely embedding-similar. [8][9]
- **Retention/compliance staleness**: caches persist far longer than teams assume because LRU-by-memory-budget optimizes for hit rate, not for regulatory TTL — a warm cache entry can survive **months**, invisibly violating data-minimization commitments. [PII search, drop 1]
- **Timing side-channel leakage**: cache hit/miss latency differential lets an attacker probabilistically reconstruct what prompts other tenants have sent, even without reading the cached content directly. [PII search, drop 5]

### 5.2 Quantization accuracy degradation (a "silent failure" class)
This is one of the most consequential 2026 findings for production quantization decisions:
- **Perplexity is not a reliable acceptance gate.** A documented case: INT4 GPTQ quantization moved held-out perplexity by only **1%** (3.81 → 3.85) while multi-step task completion dropped **7 points** (81.2% → 74.1%). Failures clustered specifically in **long sequences (6+ steps)** requiring holding a constraint from an early step to a later one — exactly the kind of degradation token-level perplexity cannot see, since it averages surprise across an entire corpus. [drop 2 quant incidents]
- **Fix applied in that case**: switched GPTQ → AWQ, increased calibration set to 1,024 samples weighted toward long sequences → recovered to 79.3% task completion (down from 81.2% FP16, but inside a 2-point tolerance). [drop 2]
- **"Flat Score, Amplified Failures" study (2026, arXiv 2607.27275)**: across 8 model/domain cells (456 episodes each) at 16/8/4-bit precision, the standard success-rate metric showed **no statistically significant change** at 4-bit — but process-level analysis showed quantization **amplifies pre-existing failure modes by up to 2.5x in volume** (e.g., tool-name hallucination rate in one model rose from 19.5% → 38.3% of tool calls, a 2.0x rate increase, and error-budget-exhausting episode terminations rose **9x**, from 5→46 of 456 episodes). Quantization creates **no new failure types** — it amplifies whichever failure channel is already open in that model/domain. Tightening the error-budget window (making the environment less forgiving) re-exposes the hidden gap: **1.3 points at budget K=10, 7.5 points at K=5, 16.7 points at K=2** — a ~13x widening. [drop 1, 3 quant incidents]
- **"Hollow Convergence" study (arXiv 2607.09999)**: NF4 quantization preserves final-answer accuracy (max 3.1pp drop across 5 models, 4 benchmarks) while silently changing **how** the model reasons — chain-of-thought quality shifts significantly, benchmark-specific (GSM8K "categorically immune," LogiQA/ARC-Challenge show largest shifts). In one 3B model, "Shortcut Collapse" (reaching a correct/incorrect answer via degenerate reasoning) rose from 44% → 78% of wrong-answer failures under NF4, while "Confidence Snowballing" collapsed from 15.8% → near zero — a qualitative shift **invisible to accuracy metrics alone** (best surface-feature detector F1 = 0.53, near chance). [drop 4 quant incidents]
- Root mechanism: quantization error is not uniform across layers — attention-projection layers handling long-range dependencies absorb the worst rounding error; a model can stay locally fluent (rewarded by perplexity) while losing the thread across a long context (punished only by trajectory-level task evals). [drop 5 quant incidents]

> ⚠️ Implication for interview/architecture discussions: **perplexity and standard accuracy benchmarks are insufficient acceptance gates for any quantization change feeding multi-step/agentic workloads.** Require trajectory-level or process-level evals as a hard gate before shipping INT4/NF4 in agentic or reasoning-heavy production paths.

### 5.3 Batching-induced latency spikes (head-of-line blocking)
- Mechanism: a long-prompt prefill is compute-intensive and, without chunking, executes as one monolithic scheduler step — every decode request already in flight stalls until that step completes, producing a bimodal TTFT distribution (p50 fine, p95 5–10x worse). [12][19]
- Sarathi-Serve measurement: naive hybrid batching (prefill + decode mixed without chunking) causes up to **28.3x** increase in time-between-tokens (TBT) vs. a decode-only batch. [20]
- FlowPrefill (2026) frames this as an inherent trade-off in chunked prefill itself: smaller chunks improve responsiveness but degrade throughput efficiency; larger chunks maximize throughput but worsen blocking — motivating **operator-level preemption** (interrupt at operator boundaries rather than fixed token-count chunks) plus SLO-aware admission that only batches a new request if the remaining-time budget can absorb the predicted batch latency. [19]

### 5.4 Router misclassification (weak model receives a hard task)
Extensively documented as a **silent failure mode** distinct from crashes/timeouts — the response is well-formed, so standard error/latency monitoring never fires: [drop TDS, cloudai, cosmicmeta, willianpinho, aionda]
- **Real production account** (Towards Data Science, 2026): a support-routing classifier trained on 6 months of historical traffic (65% simple / 35% complex split, holdout-validated) silently mis-routed "where is my charge from"-style billing queries that *looked* simple but nested fraud/reconciliation/billing-cycle-change intents the cheap model couldn't follow — the capable model had been "quietly handling these nested intents correctly because it had headroom," masking the router's fragility until it was removed.
- **Distribution drift** compounds silently: as production query mix evolves (new products, new cohorts), a classifier trained on stale traffic increasingly misroutes, but "the cost savings remain stable... the quality cost grows quietly."
- **Context-blindness**: routers that evaluate each request independently (ignoring session/conversation history) misjudge a simple-looking message embedded in a high-stakes multi-turn journey.
- **RouteLLM's own data**: the hardest-to-route queries sit exactly at the decision boundary — complex enough to strain the weak model, not obviously hard enough to trigger escalation; matrix-factorization routers handle this edge better than BERT classifiers because preference data encodes "almost good enough" signal that hard labels miss.
- **Mitigation consensus**: confidence-based escalation (re-route to frontier model if cheap-model/judge confidence dips below threshold); instrument **cost-per-successful-task** and **fallback-rate trend**, not just cost-per-token and error-rate, since "a quality score sliding while the error rate stays at zero is the signature of a silent regression."

### 5.5 Cross-provider/region resilience failure modes
- **Retry-storm amplification**: uncoordinated retries at handler + SDK + gateway layers multiply into up to **27x** actual upstream call volume from a single user action — a documented anti-pattern, not a hypothetical. [drop resilience 10]
- **Streaming failover ambiguity**: mid-stream provider failure is the hardest failover case because partial output may already have been sent to the user before the failure is detected. [drop resilience 4]
- **Shared-database single point of coupling** in multi-region gateways: if the shared control-plane Postgres goes down, *every* region loses key-validation/config simultaneously unless `allow_requests_on_db_unavailable` is explicitly configured to serve from cache. [drop 16 multi-region]

## 6. Enterprise System Design Scenarios

### 6.1 Published scale benchmarks
| System / Hardware | Model | Metric | Value | Source |
|---|---|---|---|---|
| NVIDIA GB300 NVL72 (72 GPUs) | DeepSeek-R1 | Offline throughput | 2,494,310 tok/s | [27] |
| NVIDIA GB300 NVL72 | DeepSeek-R1 | Server throughput | 1,555,110 tok/s | [27] |
| NVIDIA GB300 NVL72 | GPT-OSS-120B | Offline throughput | 1,046,150 tok/s | [27] |
| NVIDIA GB200 NVL72 | Qwen3.5-397B-A17B-NVFP4 | Total TPS/GPU (disaggregated P/D) | 25,000 tok/s/GPU | [3, qwen source] |
| AMD MI355X (11 nodes, 87 GPUs) | Llama 2 70B | Offline / Server / Interactive | 1,042,110 / 1,016,380 / 785,522 tok/s | [28] |
| AMD MI355X (12 nodes, 94 GPUs) | GPT-OSS-120B | Offline / Server | 1,031,070 / 900,054 tok/s (92–93% scale-out efficiency) | [28] |
| Together Inference Engine | Kimi K2.5 (coding agent workload) | TPS vs. TensorRT-LLM | +31% more TPS, 2x better TTFT at saturation | [23] |
| Baseten (B200) | GLM-5.2 | Peak / avg speed | 280 tok/s peak, ~100 tok/s avg (2x+ improvement post-launch tuning) | [22 disc.] |

### 6.2 Architecture case study — disaggregated prefill/decode (P/D)
- Modern frontier-scale serving separates **prefill** (compute-bound, benefits from high tensor/expert parallelism) from **decode** (memory-bandwidth-bound, benefits from high data/expert parallelism) onto physically distinct GPU pools, connected by a fast KV-cache transfer fabric (NVLink/InfiniBand). [3, GLM-5.2 blog]
- Documented production journey (Baseten/vLLM-class, GLM-5.2 on B300): moved mean TPOT from **~40ms → ~17ms** (SLA: ≤20ms) via a 4-Prefill + 1-Decode topology — notably, **not** the configuration with the highest raw throughput, because that config required more prefill endpoints, starving KV-cache capacity on the decode side. This illustrates the core **capacity-planning trade-off**: throughput-optimal ≠ SLA-optimal, and the right choice depends on which constraint (KV cache capacity vs. raw TPS) binds first.
- A single execution-path fix (eliminating regression from mixed batches) delivered the **single largest** improvement of that entire tuning effort (~40ms → ~22ms TPOT) — underscoring that scheduler/execution-path bugs often dominate over precision/parallelism tuning.

### 6.3 Trade-off matrix — quantization level selection
| Precision | Typical throughput vs FP16 | Typical quality retention | Best use case | Risk |
|---|---|---|---|---|
| FP8 | 1.3–2.3x | >99% (near-lossless on Hopper/Blackwell) | Default first choice for GPU serving at scale | Minimal; requires Hopper+ hardware for native support |
| INT8 (SmoothQuant) | 1.3–1.5x | ~99% | Fallback if FP8 unavailable | Slightly more accuracy risk than FP8 for KV cache specifically |
| AWQ INT4 | ~3.5–3.8x vs FP16, matches/beats GPTQ speed with Marlin | 96–98% (perplexity), but can still hide reasoning degradation (see §5.2) | VRAM-constrained GPU serving (vLLM production default in 2026) | Requires trajectory-level eval, not perplexity, as acceptance gate |
| GPTQ INT4 | ~3.5–3.8x, GPTQ+Machete pulls ahead on Hopper at high concurrency | 94–96% (perplexity); documented HumanEval Pass@1 drop 56.1%→46.0% (worse than AWQ/GGUF at 51.8%) in one test | Legacy compatibility, Hopper+Machete high-concurrency deployments | Cumulative error-propagation drift on multi-step code/reasoning tasks |
| GGUF Q4_K_M | 3.5–3.8x vs FP16 (llama.cpp/CPU); ~8x *slower* than AWQ if run on vLLM/GPU | 95–98% | CPU/Apple Silicon/edge/local, NOT GPU production serving | Format-serving-path mismatch is the #1 practical error |
| Below Q4 (Q3/Q2, INT2/INT3) | Higher compression | Reasoning degrades meaningfully (+11–14% perplexity, GGUF Q2_K) | Rarely justified in production | Not recommended for agentic/reasoning workloads |

### 6.4 Trade-off matrix — routing strategy selection
| Strategy | Cost savings | Quality risk | Operational overhead | Best fit |
|---|---|---|---|---|
| Static tiering (always cheap for category X) | High, predictable | High (§5.4 misclassification) | Low | Well-understood, low-stakes categories only |
| Learned classifier router (RouteLLM-style) | Up to 85% (best case) | Medium — decision-boundary queries are the failure zone | Medium (training data, retraining cadence for drift) | High-volume, well-instrumented production systems |
| Confidence-based cascading | Slightly less savings than pure routing | Lower — catches low-confidence misroutes before returning to user | Medium-high (needs a judge model or confidence signal) | Regulated / high-stakes domains (e.g., compliance, KYC) |
| Commercial hosted router (Martian/NotDiamond) | 20–97% (vendor-claimed, wide variance) | Unknown/opaque decision logic | Low (rent the calibration) | Teams without ML capacity to build/audit their own router |
| No routing (single frontier model) | 0% savings | Lowest quality risk | Lowest | Low-volume or quality-critical-only workloads |

### 6.5 Capacity planning notes
- KV-cache capacity, not raw compute, is frequently the binding constraint at high concurrency — MLPerf submitters explicitly capped concurrency sweeps (e.g., at 5120) specifically because they ran out of KV cache capacity on a fixed decode topology, not because compute saturated. [Qwen3.5 tps post]
- GPU-hour economics: "run inference 2x faster and you've effectively halved your infrastructure costs" holds precisely when paying for GPU-hours rather than per-token API pricing — this reframes quantization/batching/speculative-decoding investment as direct infra-cost reduction, not just a UX latency win. [22]
- Real dollar comparison (Together AI, coding-agent workload, 80–100K input tokens/~450 output tokens per request): Kimi K2.7 Code at **$0.029/request** vs. Claude Opus 4.8 at **$0.097/request** — a 150-engineer team running 7.5M TPM, 5 hrs/day, 250 days/year saves **~$421K/year** by switching, illustrating the scale at which model-selection/routing decisions compound. [23]

## Sources
- [1] https://dl.acm.org/doi/10.1145/3600006.3613165 — PagedAttention paper (SOSP'23), 2-4x throughput vs FasterTransformer/Orca
- [2] https://arxiv.org/pdf/2309.06180 — PagedAttention arXiv full text, KV cache block/paging mechanics
- [3] https://vllm.ai/blog/2024-09-05-perf-update — vLLM v0.6.0 perf update: multi-step scheduling, 2.7x throughput/5x latency reduction
- [4] https://sgl-project-sglang-93.mintlify.app/concepts/radix-attention — SGLang RadixAttention architecture docs
- [5] https://wiki.charleschen.ai/ai/processed/wiki/llm-core/inference/raw/papers/prefix-caching-radixattention — RadixAttention paper notes, LRU eviction policy detail
- [6] https://platform.claude.com/docs/en/build-with-claude/prompt-caching — Anthropic official prompt caching pricing/TTL docs
- [7] https://github.com/NVIDIA/TensorRT-LLM/blob/main/docs/source/blogs/quantization-in-TRT-LLM.md — TensorRT-LLM quantization blog, FP8/INT8/AWQ/GPTQ guidance and benchmarks
- [8] https://www.ndss-symposium.org/wp-content/uploads/2026-f200-paper.pdf — Semantic cache poisoning attack paper (NDSS 2026)
- [9] https://arxiv.org/html/2601.23088v2 — Key Collision Attack on LLM Semantic Caching
- [10] https://arxiv.org/pdf/2406.18665 — RouteLLM paper, cost-savings benchmarks vs Martian/Unify AI
- [11] https://www.lmsys.org/blog/2024-07-01-routellm/ — RouteLLM LMSYS blog, MT-Bench/MMLU/GSM8K savings figures
- [12] https://www.spheron.network/blog/llm-serving-optimization-continuous-batching-paged-attention/ — Continuous batching / chunked prefill throughput and TTFT p95 benchmarks
- [13] https://dreaming.press/posts/2026-06-21-routellm-vs-notdiamond-vs-martian.html — Router vendor comparison, realistic savings caveats
- [14] https://www.truefoundry.com/blog/llm-failover-load-balancing-provider-outages — Circuit breaker state machine for LLM provider failover
- [15] https://localaimaster.com/blog/awq-vs-gptq-vs-gguf-comparison — GPTQ/AWQ/GGUF quality retention comparison table
- [16] https://aifoss.dev/blog/gptq-awq-gguf-vllm-production-inference-2026/ — Marlin/Machete kernel comparison, GGUF-on-vLLM performance gap
- [17] https://docs.lmcache.ai/developer_guide/architecture.html — LMCache tiered KV cache architecture (GPU/CPU/disk/remote)
- [18] https://docs.lmcache.ai/kv_cache/storage_backends/mooncake.html — Mooncake distributed KV cache backend
- [19] https://arxiv.org/pdf/2602.16603 — FlowPrefill: operator-level preemption for head-of-line blocking mitigation
- [20] https://arxiv.org/html/2403.02310v2 — Sarathi-Serve: stall-free batching, 28.3x TBT degradation without chunking
- [21] https://github.com/envoyproxy/ai-gateway/blob/53e58b4e/site/docs/concepts/architecture/system-architecture.md — Envoy AI Gateway control/data plane architecture
- [22] https://www.together.ai/blog/optimizing-inference-speed-and-costs — Together AI production inference optimization, quantization/speculator cost data
- [23] https://www.together.ai/blog/coding-agent-benchmarks — Together AI coding-agent benchmark, ThunderMLA, cost-per-request comparison
- [24] https://cloudai.pt/llm-routing-cuts-85-of-api-spend-heres-the-engineering/ — Confidence-based escalation mitigation pattern
- [25] https://towardsdatascience.com/we-built-a-routing-layer-to-cut-our-ai-costs-it-broke-the-product/ — Real-world router misclassification production incident account
- [26] https://mlcommons.org/2026/04/mlperf-inference-v6-0-results/ — MLPerf Inference v6.0 official results release
- [27] https://developer.nvidia.com/blog/nvidia-platform-delivers-lowest-token-cost-enabled-by-extreme-co-design/ — NVIDIA MLPerf v6.0 GB300 NVL72 throughput numbers
- [28] https://www.amd.com/en/blogs/2026/amd-delivers-breakthrough-mlperf-inference-6-0-results.html — AMD MLPerf v6.0 MI355X multi-node results
- [29] https://developer.nvidia.com/blog/an-introduction-to-speculative-decoding-for-reducing-latency-in-ai-inference/ — EAGLE-3 speculative decoding mechanics
- [30] https://introl.com/blog/speculative-decoding-llm-inference-speedup-guide-2025 — Speculative decoding production maturity, acceptance rates
- [31] https://arxiv.org/html/2503.01840v1 — EAGLE-3 paper, speedup vs batch size table
- [32] https://arxiv.org/pdf/2508.08192 — Speculative decoding at Llama production scale, engineering challenges
- [33] https://www.deepinspect.ai/blog/llm-routing-strategies — Audit-record vs. routing-log separation pattern
- [34] https://go.temporal.io/platform-hub/ai-engineering/ai-reference-architecture — Durable execution reference architecture for LLM agent pipelines
- [35] https://dev.to/marcuswwchen/perplexity-held-flat-after-int4-task-accuracy-dropped-7-points-4fg6 — Production quantization incident: perplexity vs. task-completion divergence
- [36] https://arxiv.org/html/2607.27275 — "Flat Score, Amplified Failures": quantization error-budget masking study
- [37] https://www.alphaxiv.org/abs/2607.09999 — "Hollow Convergence" quantized reasoning taxonomy study
- [38] https://www.systemshardening.com/articles/ai-landscape/gpu-shared-kernel-ai-isolation/ — Multi-tenant GPU isolation failure analysis
- [39] https://gvisor.dev/docs/user%5Fguide/gpu/ — gVisor nvproxy GPU sandboxing mechanism
- [40] https://tianpan.co/blog/2026-06-03-the-pii-redactor-that-scrubbed-the-user-question-and-left-the-prompt-cache-untouched — PII/cache retention compliance gap case study
- [41] https://www.privatemode.ai/blog/secure-prompt-caching — vLLM cache salting for timing side-channel mitigation
- [42] https://docs.litellm.ai/docs/proxy/multi_region — Multi-region LLM gateway deployment topology (LiteLLM)
- [43] https://aws.amazon.com/blogs/machine-learning/implementing-resilience-patterns-with-amazon-bedrock-and-llm-gateway/ — Bedrock Cross-Region Inference resilience patterns
- [44] https://github.com/nathanmaine/governed-llm-gateway — Hash-chained audit trail implementation pattern for compliance
