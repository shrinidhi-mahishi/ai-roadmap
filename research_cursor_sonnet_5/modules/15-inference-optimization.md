# 15. Inference & Optimization

**Sub-areas covered**: control/data-plane serving architecture (Envoy AI Gateway-style ExtProc pattern) · three-tier caching (GPU-resident KV cache/PagedAttention/RadixAttention, application-layer semantic cache, provider-hosted prompt cache) · learned-classifier and cascading model routing (RouteLLM-style) · continuous/chunked-prefill batching schedulers · PTQ quantization (GPTQ/AWQ/FP8) and kernel coupling (Marlin/Machete) · durable execution, multi-region failover, and circuit breakers for inference backends · Zero-Trust MCP applied to routing/model-serving infrastructure · semantic-cache poisoning and PII-before-caching governance

---

## 1. System Topology & Data Flow

An inference-serving stack that layers caching, routing, batching, and quantization on top of raw model execution is not "a model behind a load balancer" — it is a control plane that decides *which model, at what precision, with what tool-scope* a request is entitled to reach, sitting in front of a data plane whose entire job is to maximize GPU utilization per request without violating that decision.

```
                                   ┌─────────────────────────────────────────────────────────────────┐
                                   │                          CONTROL PLANE                             │
                                   │                                                                     │
  ┌──────────┐   HTTP/gRPC in     │  ┌───────────────┐   ┌─────────────────┐   ┌──────────────────┐    │
  │  Caller  │────────────────────▶│  │ AI Gateway     │──▶│ ABAC/RBAC Policy │──▶│ Budget & Rate     │    │
  │ (agent / │                     │  │ Controller     │   │ Decision Point   │   │ Limiter (per-     │    │
  │  app)    │◀────────────────────│  │ (xDS config    │   │ (virtual key,    │   │ tenant token      │    │
  └──────────┘   response          │  │  push, model/  │   │  model/route/    │   │ bucket, ITPM/RPM) │    │
                                   │  │  route CRDs)   │   │  MCP-scope grant)│   └─────────┬─────────┘    │
                                   │  └───────┬────────┘   └────────┬─────────┘             │              │
                                   │          │                     │                        │              │
                                   │          ▼                     ▼                        ▼              │
                                   │  ┌────────────────────────────────────────────────────────────────┐  │
                                   │  │  Router Policy Store -- cost thresholds, model-tier map,          │  │
                                   │  │  MCP tool-group entitlements per route (§4.6); Circuit-Breaker    │  │
                                   │  │  Registry, scoped per (model-tier, region) -- CLOSED/OPEN/         │  │
                                   │  │  HALF_OPEN (§4.3)                                                 │  │
                                   │  └──────────────────────────────┬─────────────────────────────────┘  │
                                   └─────────────────────────────────┼──────────────────────────────────────┘
                                                                     │
                                   ┌─────────────────────────────────▼──────────────────────────────────────┐
                                   │                              DATA PLANE                                  │
                                   │                                                                           │
                                   │  ┌────────────┐   ┌─────────────┐   ┌────────────┐   ┌────────────────┐ │
                                   │  │ PII Filter  │──▶│ Semantic /   │──▶│  Router     │──▶│ Continuous /    │ │
                                   │  │ (detect →   │   │ Prompt Cache │   │ (classifier │   │ Chunked-Prefill │ │
                                   │  │  redact,    │   │ Lookup       │   │  / cascade, │   │ Batching        │ │
                                   │  │  §4.8)      │   │ (embedding   │   │  §2.5)      │   │ Scheduler       │ │
                                   │  │             │   │  similarity) │   │             │   │ (§2.2, §2.3)    │ │
                                   │  └────────────┘   └──────┬──────┘   └──────┬──────┘   └───────┬─────────┘ │
                                   │                          │ hit             │ miss              │           │
                                   │                          ▼                 ▼                   ▼           │
                                   │                  ┌───────────────┐  ┌──────────────────────────────────┐ │
                                   │                  │ Cached response│  │  ExtProc sidecar: token accounting,│ │
                                   │                  │ (skip model    │  │  model-name rewrite, KV-cache      │ │
                                   │                  │ call entirely) │  │  affinity hint injection           │ │
                                   │                  └───────────────┘  └───────────────┬──────────────────┘ │
                                   └───────────────────────────────────────────────────────┼──────────────────┘
                                                                                            │
                                   ┌────────────────────────────────────────────────────────▼──────────────────┐
                                   │                        TOOL / MODEL-BACKEND PROXIES                          │
                                   │                                                                              │
                                   │  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  ┌───────────┐ │
                                   │  │ Frontier tier   │  │ Mid tier        │  │ Quantized/self- │  │ MCP Tool  │ │
                                   │  │ (FP16/FP8,      │  │ (FP8/INT8,      │  │ hosted tier     │  │ Gateway   │ │
                                   │  │  vLLM/SGLang    │  │  smaller model) │  │ (AWQ/GPTQ INT4, │  │ (scoped   │ │
                                   │  │  engine)        │  │                 │  │  Marlin/Machete)│  │ per §4.6) │ │
                                   │  └───────┬────────┘  └───────┬────────┘  └───────┬────────┘  └─────┬─────┘ │
                                   └──────────┼───────────────────┼───────────────────┼─────────────────┼───────┘
                                              │                   │                   │                 │
                                   ┌──────────▼───────────────────▼───────────────────▼─────────────────▼───────┐
                                   │                             PERSISTENCE LAYER                                │
                                   │                                                                              │
                                   │  ┌────────────────┐  ┌─────────────────┐  ┌────────────────┐  ┌───────────┐│
                                   │  │ KV Cache Tiers  │  │ Semantic-Cache   │  │ Control-Plane   │  │ Immutable ││
                                   │  │ GPU HBM → CPU   │  │ Vector Store     │  │ Config/Budget   │  │ Audit Log ││
                                   │  │ DRAM → NVMe →   │  │ (namespace-      │  │ DB (Postgres,   │  │ (hash-    ││
                                   │  │ Remote pool     │  │  isolated per    │  │  one shared +   │  │  chained, ││
                                   │  │ (LMCache/       │  │  tenant, §4.9)   │  │  one Redis per  │  │  routing +││
                                   │  │  Mooncake,      │  │                  │  │  region, §4.4)  │  │  decision ││
                                   │  │  §2.1)          │  │                  │  │                 │  │  records) ││
                                   │  └────────────────┘  └─────────────────┘  └────────────────┘  └───────────┘│
                                   └──────────────────────────────────────────────────────────────────────────────┘
                                                                     │
                                   ┌─────────────────────────────────▼──────────────────────────────────────────┐
                                   │                          TELEMETRY / OBSERVABILITY SINKS                      │
                                   │                                                                                │
                                   │  ┌────────────────┐  ┌─────────────────┐  ┌────────────────┐  ┌────────────┐ │
                                   │  │ Cost/Token Meter │  │ Cache-Hit-Rate   │  │ Latency Histogram│  │ Quantization││
                                   │  │ (per model tier, │  │ Monitor (KV +    │  │ (TTFT/TPOT p50/  │  │ Drift/Eval  ││
                                   │  │  per route)      │  │  semantic)       │  │  p95/p99, §3.2)  │  │ Monitor     ││
                                   │  │                  │  │                  │  │                  │  │ (trajectory-││
                                   │  │                  │  │                  │  │                  │  │  level, §5.2││
                                   │  │                  │  │                  │  │                  │  │  research)  ││
                                   │  └────────────────┘  └─────────────────┘  └────────────────┘  └────────────┘ │
                                   └────────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A request enters the **AI Gateway Controller** — the control-plane component that watches routing/model Custom Resources and pushes config to data-plane proxies via xDS, mirroring Envoy's service-mesh split applied to AI traffic. (2) The **ABAC/RBAC Policy Decision Point** resolves the caller's virtual key into an entitlement set: allowed model tiers, budget ceiling, and — critically for §4.6 — the **MCP tool-group scope** this route is permitted to invoke if the selected model needs to call tools. (3) The **Budget & Rate Limiter** checks a per-tenant token-bucket against ITPM/RPM ceilings before any GPU cycle is spent. (4) In the data plane, the **PII Filter** redacts sensitive spans *before* the request reaches any caching layer (§4.8) — this ordering is load-bearing, not cosmetic. (5) The **Semantic/Prompt Cache** is checked next; a hit returns immediately, skipping the model entirely (near-zero marginal cost); a miss proceeds to the **Router**, which runs a lightweight classifier or cascading judge to pick a model tier under the caller's cost/quality policy (§2.5). (6) The **ExtProc sidecar** rewrites the model name, injects a KV-cache-affinity routing hint (so repeat requests from the same session land on a GPU replica that already holds the relevant prefix in HBM), and forwards to the **Continuous/Chunked-Prefill Batching Scheduler**, which admits the request into an in-flight batch without waiting for a full batch to form (§2.2). (7) The request lands on one of three **model-backend tiers** — frontier, mid, or quantized/self-hosted — each behind its own circuit breaker (§4.3); if the selected model needs to call a tool, it does so only through the **MCP Tool Gateway**, whose scope was fixed at step 2, never expanded at execution time. (8) KV-cache state persists across the tiered **KV Cache** hierarchy (GPU HBM → CPU DRAM → NVMe → remote pool) so a session's prefix survives eviction pressure without a full recompute; the semantic-cache write (if this response is cacheable) lands in a **namespace-isolated vector store** to prevent cross-tenant collision (§4.9). (9) Every hop emits to **telemetry**: cost/token meter attributes spend per model tier, a cache-hit-rate monitor tracks KV and semantic hit ratios separately, a latency histogram feeds the P50/P95/P99 SLA dashboard (§3.2), and a quantization drift monitor watches for the "flat perplexity, degraded task completion" failure class (§5.2 research) that standard latency/error monitoring cannot see. (10) The routing decision itself — which model, which tier, which MCP scope — is written to the **immutable audit log** as a record separate from, but correlated by request ID with, the operational routing log (§4.9).

---

## 2. Core Mechanics & Algorithms

### 2.1 KV cache management — PagedAttention and RadixAttention

The KV cache is the dominant memory consumer in autoregressive decoding: every generated token requires storing a key/value vector per attention head per layer, and naive implementations pre-allocate a maximally-sized contiguous buffer per request, wasting an estimated 60–80% of allocated memory to internal/external fragmentation on variable-length outputs.

**PagedAttention** (vLLM) applies the OS-paging analogy directly: the KV cache is partitioned into fixed-size **blocks** (typically 16 tokens each), and a per-request **block table** maps logical token positions to physical, non-contiguous block addresses — identical in spirit to a virtual-memory page table.

```
Request A logical KV:  [ t0 t1 ... t15 | t16 t17 ... t31 | t32 ... ]
                              │                 │                │
                        block table:  A→[phys#7]   A→[phys#2]   A→[phys#19]

Physical block pool (GPU HBM):
  [#2: A blk1][#7: A blk0][#12: free][#19: A blk2][#23: B blk0]...

Shared prefix (system prompt) across requests A, B:
  block #7 has refcount=2 -- both A's and B's block tables point to
  the SAME physical block until a token in that block diverges
  (copy-on-write semantics)
```

This yields near-zero fragmentation (<4% waste, down from 60–80%) and **block-level sharing**: two requests with an identical prefix (e.g., a shared system prompt) reference the same physical blocks with copy-on-write semantics, converting what would be duplicated memory into a single shared allocation.

**RadixAttention** (SGLang) generalizes this to **token-level** granularity using a radix tree (a trie with variable-length edge labels) as the cache-key structure rather than fixed 16-token blocks: any two requests sharing an arbitrary-length common prefix — not just a block-aligned one — share the corresponding tree path. Eviction uses **LRU on leaf nodes first**, which structurally protects shared ancestor nodes (the parts of the tree with the highest reuse potential) from being evicted before their less-shared descendants.

**Tiered placement** (LMCache as the cross-engine "glue" layer): GPU HBM holds the active working set; CPU DRAM (pinned memory) is a warm overflow tier; local NVMe (GPU-Direct Storage) holds large-capacity long-document caches; and a remote/distributed pool (Mooncake, Redis, InfiniStore) aggregates DRAM+SSD across a cluster into one addressable store, favoring throughput over strict consistency (§4.2). Async migration between tiers uses LRU eviction and allows KV state to survive process restarts when backed by disk/remote storage — a durability property raw GPU HBM alone cannot provide.

**Invariant**: a block/tree node may only be evicted if its reference count is zero across all in-flight requests; violating this invariant (evicting a block another request's table still points to) is a correctness bug, not a performance regression — production implementations gate eviction behind the refcount check as a hard precondition, never a best-effort heuristic.

### 2.2 Continuous (iteration-level) batching — scheduler algorithm

Static batching waits for an entire batch to finish before admitting new requests, capping GPU utilization at roughly 30–40% because the longest sequence in the batch blocks everyone else from being replaced. **Continuous batching** (vLLM default, SGLang, TensorRT-LLM's "in-flight batching," HF TGI's "persistent batching") instead re-evaluates the batch composition every iteration:

```
LOOP each scheduler tick (one decode step):
  1. for req in running_batch:
       if req.is_finished(): evict(req); free_kv_blocks(req)
  2. while has_capacity(gpu_memory, max_batch_tokens):
       req = waiting_queue.peek()
       if not req: break
       if can_allocate_kv_blocks(req): admit(req); waiting_queue.pop()
       else: break  # out of KV cache budget, not out of compute budget
  3. execute_one_decode_step(running_batch)   # single fused forward pass
  4. tick += 1
```

**Complexity**: each tick is `O(B)` in the batch size `B` for the admission/eviction bookkeeping, dominated in practice by the `O(B × d)` forward-pass compute (`d` = model dimension); the algorithmic win over static batching is not asymptotic but **utilization-shaped** — it eliminates idle GPU slots that a fixed-size batch would otherwise leave empty while waiting for its longest member to finish. Reported gains: 2–3x throughput over static batching alone, and 23–28x when stacked with PagedAttention plus kernel tuning. **Multi-step scheduling** (vLLM v0.6+) amortizes the CPU-side scheduling overhead by running N consecutive decode steps per scheduler invocation instead of round-tripping to the CPU scheduler every single step — a documented 28% throughput gain on Llama 70B / 4×H100 from cutting CPU-overhead-induced GPU idle time alone, with zero change to the underlying batching algorithm.

**Key invariant**: admission in step 2 is gated on **KV cache capacity**, not raw compute headroom — a request can be compute-ready but memory-blocked, which is why capacity planning for continuous batching is fundamentally a KV-cache-budget problem (§3.3), not a FLOPs problem.

### 2.3 Chunked prefill / stall-free batching

Naively mixing a long prefill (compute-bound, can take hundreds of milliseconds for a 32K-token prompt) into a batch of in-flight decode steps (each normally a few milliseconds) causes the prefill to monopolize a scheduler tick, stalling every decode request in that tick — the mechanism behind bimodal, tail-heavy time-between-tokens (TBT) distributions.

**Sarathi-Serve's chunked-prefill algorithm** splits a long prefill into fixed **token-budget chunks** and interleaves each chunk with ongoing decode steps rather than running the whole prefill as one monolithic step:

```
Naive hybrid batching:           Chunked prefill (stall-free):
tick 1: [ PREFILL (32K tok) ]    tick 1: [ chunk_0 (2K) | decode_a | decode_b ]
tick 2: [ decode_a, decode_b ]   tick 2: [ chunk_1 (2K) | decode_a | decode_b ]
        (both STALLED during           ...
         tick 1 -- up to 28.3x   tick 16:[ chunk_15(2K) | decode_a | decode_b ]
         worse TBT)                    (decode_a/b advance every tick --
                                        no multi-hundred-ms stall)
```

**Trade-off (formalized by FlowPrefill, 2026)**: smaller chunks improve decode responsiveness (less time any single decode waits) but reduce prefill throughput efficiency (more scheduling overhead per token of prefill); larger chunks do the reverse. FlowPrefill's refinement decouples **preemption granularity** from **scheduling frequency** using operator-level preemption (interrupting at operator boundaries inside the prefill computation graph) instead of fixed token-count chunks, letting the scheduler hit TTFT SLOs on heterogeneous request mixes without sacrificing aggregate throughput — this is the state-machine-level generalization of the fixed-chunk-size heuristic.

### 2.4 Quantization mathematics — GPTQ, AWQ, FP8

**GPTQ** (layer-by-layer, second-order error minimization): for each layer's weight matrix `W`, quantization proceeds column-by-column, and after quantizing column `q`, the remaining unquantized columns are updated to compensate for the introduced error using the inverse Hessian of the layer's activation covariance:

```
δ = (w_q - quant(w_q)) / [H^-1]_qq
W[:, q+1:] -= δ · H^-1[q, q+1:]
```

This greedily minimizes the layer's output reconstruction error (`||WX - ŴX||²`) rather than treating each weight independently, which is why GPTQ retains more accuracy than naive round-to-nearest quantization at the same bit width — at the cost of requiring calibration-time access to per-layer activation statistics.

**AWQ** (activation-aware): observes that a small fraction (~1%) of weight *channels* — those multiplied by consistently large-magnitude activations — dominate quantization error, and protects exactly those channels by scaling them up before quantization and scaling the corresponding activations down by the inverse factor (a mathematically lossless rescaling since `(s·w)(a/s) = wa`), then quantizes the now-more-uniform weight distribution aggressively. Because it doesn't require the full Hessian computation GPTQ does, AWQ calibrates 5–10x faster.

**FP8** (E4M3/E5M2 floating-point, native on Hopper/Blackwell tensor cores): unlike INT4/INT8, FP8 retains a floating exponent, so its per-value relative error stays roughly constant across the dynamic range instead of concentrating error at the tails the way a fixed integer scale does — the mechanistic reason FP8 achieves near-lossless (>99%) quality retention while INT8 needs an auxiliary technique (SmoothQuant, which migrates quantization difficulty from activations to weights via a per-channel scaling factor) to reach comparable quality.

**Kernel coupling as an invariant, not a preference**: a quantization *format* is inert without a GPU kernel that can execute it efficiently — Marlin (Ampere/Ada) handles both GPTQ and AWQ INT4 at group_size 128; Machete (Hopper-optimized) is GPTQ-only as of 2026. Running a format without its matched kernel (e.g., GGUF weights on a vLLM/H200 GPU-serving stack) can leave up to 8x throughput on the table (~93 tok/s vs. ~741 tok/s for AWQ+Marlin in one benchmark) — this is a **format-serving-path mismatch**, the single most common practical quantization-deployment error, not a quantization-quality problem at all.

### 2.5 Routing algorithms — classifiers, cascades, and cost thresholds

**Learned classifier routing** (RouteLLM): a matrix-factorization or small causal-LM classifier is trained on human-preference data (e.g., Chatbot Arena win/loss labels) to predict `P(weak_model_sufficient | query)`, with a tunable cost threshold `τ` deciding the cheap/strong split: route to the weak model if `P(sufficient) ≥ τ`. Matrix-factorization routers outperform BERT-style hard-label classifiers specifically on decision-boundary queries because preference data encodes graded "almost good enough" signal that binary difficulty labels discard.

**Cascading (confidence-based escalation)**: start every request on the cheapest model tier; a lightweight judge scores the response's confidence; if confidence falls below a threshold, escalate to the next tier and repeat.

```
tier = 0
loop:
    response = call_model(tiers[tier], request)
    confidence = judge(response, request)
    if confidence >= threshold[tier] or tier == len(tiers) - 1:
        return response
    tier += 1
```

**Complexity**: worst-case `O(T)` model calls for `T` tiers if every escalation fails its confidence check, but the expected cost is dominated by the (typically large) fraction of traffic resolved at tier 0 — cascading trades a small latency tax on the escalating minority for large aggregate savings, the same trade-off structure as retry-with-backoff trades latency for reliability.

**Complexity-based static routing**: classifies queries by structural proxies (token count, presence of code blocks, multi-step keyword detection) rather than a learned model — cheapest to operate, but the least accurate at the decision boundary and the most exposed to the silent-misrouting failure mode (§5.4 research; distribution drift silently degrades a classifier trained on stale traffic while cost metrics stay flat).

**Routing latency overhead**: commercial hosted routers add roughly 100–200ms per hop for the embedding pass or small-classifier inference — a fixed tax that must be weighed against the per-token savings the routing decision unlocks (§3.1); for a request whose total generation time is already multiple seconds, this tax is negligible, but for latency-sensitive interactive workloads it can materially shift the P95 latency budget (§3.2).

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost formulas

```
cost_per_run = cache_lookup_cost                                    # embedding + vector search, ~fixed
             + (1 - P(cache_hit)) × [
                   routing_overhead_cost                            # classifier/cascade inference
                 + (prompt_tokens × price_in[tier] / 1e6 × cache_multiplier[tier])
                 + (output_tokens × price_out[tier] / 1e6)
               ]
             + P(cache_hit) × cached_read_cost                      # ~0.1x-0.25x of price_in, provider-dependent

# quantized self-hosted tier substitutes GPU-hour cost for per-token price:
cost_per_run_selfhosted = (gpu_seconds_used × price_per_gpu_second) / requests_per_batch_window
```

**Caching savings ($ per 1k runs, illustrative, August 2026 pricing snapshot)**:

| Scenario | Assumptions | $/run (baseline, no cache) | $/run (with technique) | **$ per 1k runs saved** |
|---|---|---|---|---|
| Anthropic prompt cache, 5-min TTL, stable system prompt | 100K cached tokens, $3/MTok base input, 0.1x read cost after 1.25x write | $0.30 | $0.033 (amortized over ~8 reads before expiry) | **~$267 per 1k runs (~89%)** |
| Anthropic prompt cache, 1-hour TTL | Same base, 2x write, break-even ≥6 reads/hr | $0.30 | ~$0.04 at sustained high-frequency reuse | **~$260 per 1k runs (~87%)** at qualifying traffic volume |
| OpenAI prompt cache (GPT-5.6+, 1.25x write / 0.1x read) | 50K cached tokens, $2/MTok base | $0.10 | $0.011 | **~$89 per 1k runs (~89%)** |
| Google Gemini cache (0.25x read discount) | 50K cached tokens, $1.25/MTok base | $0.0625 | $0.0156 | **~$47 per 1k runs (~75%)** |
| Self-hosted vLLM prefix caching (no provider fee, compute-only) | Shared-prompt workload, 30–60% throughput gain reduces effective GPU-seconds/request | GPU-hour cost held constant | 30–60% fewer GPU-seconds per request | **~30–60% reduction in GPU-hour cost per 1k runs** |

Real-world reference point: a documented Claude bill dropped from $720/month to $72/month (90% reduction) purely by caching a stable system prompt across high-volume requests — a single-lever change with no model or architecture swap.

**Quantization savings ($ per 1k runs, self-hosted GPU-hour model)**: "running inference 2x faster effectively halves infrastructure cost" holds exactly when paying for GPU-hours rather than per-token API pricing. FP8 vs. FP16 on H100: 1.3–1.8x throughput at <1% accuracy loss, translating directly to a 23–44% reduction in GPU-hours per 1k requests at fixed traffic. AWQ INT4 (Marlin-kernel-matched): ~3.5–3.8x throughput vs. FP16, i.e. **roughly 70–74% GPU-hour reduction per 1k runs**, provided the accuracy-retention caveat in §3.4/§5.2 is independently verified with trajectory-level evals, not perplexity alone.

**Routing savings**: RouteLLM reports up to 85% cost reduction on MT-Bench-style traffic at 95% retained quality, requiring only ~54% of calls to route to the strong model after data augmentation. Realistic, workload-validated enterprise savings (as opposed to vendor headline numbers up to 97%) cluster **20–40%** for a mixed traffic profile — the gap between vendor claims and realistic outcomes is explained by "routable share": routing only pays off in proportion to the fraction of real traffic that is genuinely answerable by the cheap tier, which must be measured per-workload, not assumed from a benchmark.

**Stacked effect**: continuous batching + PagedAttention + FP8 KV cache + chunked prefill in one documented progression moved a vLLM deployment from 4,200 to 7,100 tok/s (~1.7x cumulative) on the same hardware — the economics of these four techniques are additive on the same GPU fleet, not competing alternatives.

### 3.2 Latency SLA targets — explicit P50/P95/P99 per technique

> Vendor-reported figures are marked `[reported]`; figures without a published vendor SLA are architect-constructed design targets `[inferred]`, consistent with the treatment of latency budgets elsewhere in this roadmap.

| Technique / path | P50 | P95 | P99 | Primary failure mode | Mitigation |
|---|---|---|---|---|---|
| Semantic/prompt cache hit (vector lookup only, no model call) | 15–40ms `[inferred]` | ≤80ms | ≤150ms | Embedding-service latency spike | Co-locate vector index with gateway; local ANN index for hot namespace |
| KV-cache-hit prefill (long shared prefix, e.g. 100K-token doc) | 2.4s TTFT `[reported, Anthropic]` | ≤4s | ≤6s | Cache eviction under memory pressure (tier demotion to CPU/disk adds latency) | Pin high-value prefixes; LMCache tiered fallback instead of hard miss |
| Cold prefill, same prompt, no cache | 11.5s TTFT `[reported, Anthropic]` | ≤16s | ≤22s | Long-context compute-bound prefill | Chunked prefill (below); provider prompt caching |
| Continuous-batching decode step (TPOT), disaggregated P/D | 17ms mean `[reported, GLM-5.2/B300]` | ≤25ms | ≤35ms | Mixed-batch execution-path regression | Dedicated decode pool sized to KV-cache headroom, not raw TPS |
| Continuous-batching decode step (TPOT), monolithic (non-disaggregated) | ~40ms mean `[reported, same system pre-optimization]` | ≤65ms | ≤100ms | Long prefill sharing the batch stalls decode | Migrate to disaggregated P/D or chunked prefill |
| Chunked-prefill mixed workload TTFT (32K-token inputs) | 890ms p95-optimized `[reported]` | 890ms (post-chunking) vs. 2,800ms (pre-chunking) | ≤1.4s | Chunk-size misconfiguration re-introduces blocking | Operator-level preemption (FlowPrefill) over fixed chunk size |
| Naive hybrid batching (no chunking), TBT | baseline | 5–10x worse than decode-only `[reported]` | up to 28.3x worse `[reported, Sarathi-Serve]` | Long prefill monopolizes a scheduler tick | Chunked prefill is mandatory above a prompt-length threshold, not optional |
| Router classifier/cascade hop | 100ms `[reported]` | ≤180ms | ≤250ms | Classifier-service cold start or overload | Warm classifier pool; fall back to static tiering on classifier timeout (§5) |
| FP8-quantized serving, first-token latency (batch=16 constraint) | ≤500ms by design `[reported, TensorRT-LLM]` | ≤700ms | ≤900ms | Batch-size/latency trade-off breach | Dynamic batch-size cap tied to the SLA, not throughput-max |
| Speculative decoding (EAGLE-3), effective decode latency | 3.0–6.5x faster than vanilla `[reported]` | Benefit crosses over below 1.0x at batch ≥48–56 | N/A (degrades to vanilla latency, not worse) | Draft-model acceptance-rate collapse at high concurrency | Disable speculative path above the measured crossover batch size |

**Bimodal-TTFT is a scheduling failure, not a capacity failure.** The single most important latency finding in this domain: a vLLM-class server under bursty, mixed-length traffic produces p50-fine/p95-5-to-10x-worse TTFT specifically because long prefills block in-flight decodes — the fix is chunked prefill or disaggregated P/D, not more GPUs, since adding compute to a scheduling-bound bottleneck does not resolve it.

### 3.3 Throughput and back-pressure design

- **Capacity planning is KV-cache-bound, not compute-bound, at high concurrency.** MLPerf submitters explicitly capped concurrency sweeps at fixed values because they exhausted KV-cache capacity on a fixed decode topology before compute saturated — the practical implication is that throughput capacity planning for a continuous-batching deployment must model `max_concurrent_requests = kv_cache_budget / avg_kv_footprint_per_request`, not `gpu_flops / flops_per_request`.
- **Back-pressure signal**: reject new admissions (HTTP 429 with `Retry-After`) once `waiting_queue_depth` exceeds a threshold calibrated to the scheduler's TTFT SLO, rather than allowing unbounded queueing that silently degrades every in-flight request's latency — an admission-control gate at the scheduler boundary is cheaper than discovering the SLA breach downstream.
- **Routing and rate-limiting are logically separate gates, applied in that order**: the rate limiter enforces budget/ITPM ceilings *before* the router picks a model tier, so a caller that is over budget never consumes classifier-inference cost for a request that will be rejected anyway.
- **Published scale reference points**: NVIDIA GB300 NVL72 sustained 2.49M tok/s (offline) / 1.56M tok/s (server) on DeepSeek-R1 across 72 GPUs (MLPerf v6.0, April 2026); AMD MI355X crossed 1M tok/s on Llama 2 70B at 92–98% scale-out efficiency across multi-node deployments — useful as upper-bound sanity checks when sizing a fleet, not as a substitute for workload-specific KV-cache-budget modeling.

### 3.4 NFRs: availability, RPO/RTO, and explicit trade-off discussions

| Component | Availability target | RPO | RTO | Notes |
|---|---|---|---|---|
| AI Gateway control plane (routing config, policy) | 99.95% `[inferred]` | Minutes (config re-sync from source of truth) | ≤60s (xDS re-push) | Config is declarative and re-derivable; not the durability-critical tier |
| Router / classifier service | 99.9% `[inferred]` | N/A (stateless per-request) | Immediate — fail to static-tiering fallback (§5), never fail the request | Router unavailability degrades cost optimization, not correctness — must never block serving |
| Model backend pool (per tier, per region) | 99.9% per tier `[inferred]`; ≥99.95% aggregate across tiers with fallback chain | N/A (stateless inference) | Seconds (circuit-breaker cutover to next tier, §4.3) | Independent breaker per tier is what makes the aggregate number achievable — a shared breaker would couple unrelated failure domains |
| KV cache (GPU HBM tier) | N/A (ephemeral, in-process) | **Zero recoverable** — HBM state is lost on process crash by design | Seconds (recompute from scratch, or reload from CPU/disk tier if LMCache-backed) | Treat GPU-resident KV cache as a pure performance cache, never a durability boundary |
| Distributed KV pool (Mooncake/Redis/remote) | 99.9% `[inferred]` | Up to one async-migration interval (eventually consistent, not synchronously replicated) | Seconds–minutes depending on backend | Explicitly favors throughput over strict consistency (§4.2) — acceptable because KV cache is a performance optimization, not a system of record |
| Semantic/prompt cache store (vector DB) | 99.9% `[inferred]` | Minutes (async index updates) | Seconds (fail open to cache-miss path — degrades cost, not correctness) | A cache-store outage should degrade to "always call the model," never to serving a stale/wrong cached response |
| Control-plane config DB (shared Postgres, multi-region gateway) | 99.95% `[inferred]` | Near-zero with multi-AZ synchronous replication | Minutes | Shared-DB-outage blast radius spans **every region** unless `allow_requests_on_db_unavailable: true` (serve from cache) is explicitly configured |
| Audit log (routing decisions, hash-chained) | 99.99% `[inferred]` | **Zero** — a routing decision not durably logged before dispatch is treated as a compliance gap equivalent to it never having been authorized | N/A (append-only, restore from independent replica) | EU AI Act Article 12 treats this as a hard requirement for high-risk systems, not best-effort |

**Named trade-off 1 — quantization aggressiveness vs. accuracy.** Perplexity is not a reliable acceptance gate: a documented INT4 GPTQ case moved held-out perplexity by only 1% (3.81→3.85) while multi-step task completion dropped 7 points (81.2%→74.1%), with failures concentrated in long sequences (6+ steps) requiring holding an early constraint to a later step — exactly the degradation class token-level perplexity cannot see. A separate 2026 study found quantization creates no new failure types but **amplifies existing ones by up to 2.5x in volume** (tool-name hallucination rate 19.5%→38.3% in one model) and widens error-budget-sensitive failure gaps by up to ~13x under tighter budgets. The trade-off is therefore not "INT4 vs. FP16 accuracy," it is "compression ratio vs. *tail-risk exposure on multi-step/agentic tasks specifically*" — the mitigation is a **hard trajectory-level or process-level eval gate**, never a perplexity-only sign-off, before shipping any sub-FP8 precision into an agentic production path.

**Named trade-off 2 — cache TTL vs. staleness/cost.** Longer TTL increases hit rate and cost savings but increases staleness risk and — for prompt/response caches — regulatory exposure: LRU-by-memory-budget optimizes for hit rate, not for data-minimization commitments, and a hot cache key in a high-traffic system can silently persist for months, meaning the user-message content most likely to carry PII is exactly the content determining retention. Anthropic's own default TTL shift from 1 hour to 5 minutes in early 2026 reflects this tension directly: a shorter default trades some cost savings for materially reduced staleness/retention exposure. The correct dial position is workload-specific — a static system prompt with no per-user content can safely use a long TTL; any cache key derived from user-turn content should default to the shortest TTL that still clears the cost break-even point (§3.1).

**Named trade-off 3 — batch size vs. latency / head-of-line blocking.** Larger batches improve throughput (more tokens computed per kernel launch) but increase head-of-line blocking risk: a large batch admits more concurrent decode requests that a subsequent long prefill can stall simultaneously, and it increases the compute time of the fused decode step itself, directly raising TPOT for every request in that batch. Sarathi-Serve's chunked-prefill data (28.3x TBT degradation without chunking) is the extreme case of this trade-off left unmanaged; the resolution is not "pick a smaller batch size" (which sacrifices throughput unconditionally) but decoupling prefill admission from decode-step batching via chunking — the batch-size dial and the head-of-line-blocking risk become independently tunable rather than coupled once chunked prefill is in place.

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution for inference pipelines

Every model call and tool invocation inside a multi-turn or agentic inference pipeline should be wrapped in a durable-execution **Activity**, with the orchestrating **Workflow** holding conversation/session state and never calling the model directly — a direct in-workflow model call breaks replay determinism, since the model's output is nondeterministic by construction. On worker crash, the workflow replays its event history and re-hydrates already-completed LLM/tool results from the log rather than re-invoking them, which is the concrete mechanism that prevents duplicate-billed model calls after a crash — durability here is a cost-control property, not just a reliability one.

**Error-class-aware retry policy** at the Activity boundary: HTTP 429 honors the `Retry-After` header via an explicit `next_retry_delay` rather than blind exponential backoff (the two policies produce very different wait times and only one respects the backend's stated recovery estimate); HTTP 400/422 is marked `non_retryable=True` and fails fast; HTTP 500/502/503/529 gets moderate exponential backoff with jitter. SDK-level auto-retries must be disabled (`max_retries=0`) whenever a durable-execution layer owns retries — otherwise a 3-layer independent-retry stack (handler × SDK × gateway, 3×3×3) produces 27 upstream attempts from a single user action, the canonical retry-amplification failure.

### 4.2 Distributed cache consistency

The KV cache and the semantic cache have fundamentally different consistency models, and conflating them is a design error:

- **KV cache**: Mooncake/LMCache-class remote backends are explicitly "reliable but not as performant" — an overflow/persistence tier, not a synchronously-consistent primary store. Eventual consistency across tiers is acceptable because a KV cache miss simply falls back to recomputation; it is a performance optimization with a correct fallback, never a system of record.
- **Semantic/prompt cache**: consistency here is fundamentally probabilistic, not exact — a "hit" is an embedding-similarity threshold crossing, not a key match, so "consistency" really means **collision-risk management** (§4.9), a qualitatively different problem than staleness.
- **Cache salting**: in multi-tenant KV-cache-sharing deployments (e.g., shared vLLM instances across tenants), a secret per-tenant/per-team salt is mixed into cache keys so identical prompts from different tenants don't produce observably identical cache-hit timing — this closes a timing side-channel (§4.9) that would otherwise let one tenant probabilistically infer whether another tenant recently sent a specific prompt.

### 4.3 Circuit breakers for inference backend failures

The canonical three-layer resilience stack for any inference dependency:

1. **Retry** (same candidate model/tier, transient-error assumption only) — owned by exactly one layer to avoid the amplification failure in §4.1.
2. **Circuit breaker**, `CLOSED → OPEN → HALF_OPEN`, scoped per `(model_tier, region)` tuple — never one global breaker, since a frontier-tier outage in one region is an unrelated failure domain from a quantized self-hosted tier's GPU OOM in another. Trips on 5xx/529/timeouts/mid-stream errors, explicitly **not** on the caller's own 429s (a 429 means the caller is over budget, not that the backend is unhealthy). While open, requests fail fast to the next tier instead of paying the full timeout cost.
3. **Fallback chain**: same model via a different cloud/region first (preserves behavior), then a different model tier (changes behavior, should be pre-approved for the route's quality bar), then a cached/degraded response as the last resort (§5).

Documented amplification failure to guard against explicitly: an uncoordinated 3-layer retry stack (handler + SDK + gateway) can turn one user action into 27 upstream attempts — retries must live **inside** the breaker's decision, never wrapped around an already-open breaker, or the outer retry loop simply re-triggers fast failures and inflates the breaker's own failure counter.

### 4.4 Multi-region failover and dead-letter handling

Two architectural paths, with different failure characteristics:

1. **Managed global routing** (AWS Bedrock Cross-Region Inference, Azure Global Standard): near-zero implementation effort, automatic capacity-based rerouting — but this is explicitly a **capacity mechanism, not a DR mechanism**: it does not protect against a specific model/provider disruption, and it may route "beyond geographic boundaries" to any supported region, which surfaces as a **data-residency compliance finding**, not an outage alert. Residency-vs-availability is a **policy decision, not an engineering default** — an EU-region outage cannot blindly fail over to US-East if GDPR/residency terms prohibit it; the routing/policy layer must evaluate residency rules **before** dispatching a failover, not after.
2. **Custom multi-region AI gateway** (LiteLLM pattern): active-active or active-passive, DNS-routed gateway instances, one shared Postgres for global config/budget/key state, one **Redis per region** (kept in-region specifically to avoid cross-region round trips on every rate-limit check). Failure mode: if the shared Postgres goes down, **every region** loses DB access simultaneously — mitigated with `allow_requests_on_db_unavailable: true` (serve from local cache) plus multi-AZ Postgres.

**Health-check design**: failover routing must probe **liveness**, not **readiness**, endpoints — a readiness probe typically returns 503 whenever a shared database is unreachable, which would incorrectly pull every region out of rotation simultaneously during a transient DB blip, converting a localized issue into a global outage.

**Dead-letter handling**: requests that exhaust retries, trip every tier's breaker, and have no cached fallback are routed to a dead-letter queue with the full request context (redacted per §4.8) and correlation ID, rather than silently dropped — this is what makes §5's `degraded` response tier auditable after the fact rather than a black hole.

### 4.5 Failure taxonomy: transient vs. permanent vs. poison-pill

| Class | Examples | Policy |
|---|---|---|
| Transient | 429 (own budget), 5xx/529 from backend, timeout, KV-cache-tier migration lag | Retry with exponential backoff + full jitter, bounded attempts |
| Permanent | 400/422 malformed request, auth failure, invalid model-tier name, MCP scope denied | Never retry — fail fast to fallback chain (§4.3) |
| Poison-pill | A specific prompt/argument combination that deterministically crashes the same backend on every retry (e.g., a pathological input that triggers an OOM in a specific quantized kernel every time) | Detect via repeated-failure-on-identical-input hashing; quarantine and dead-letter (§4.4), never retry indefinitely |

**Idempotency keys** are mandatory for any mutating side effect a tool-augmented model call can trigger (payment, notification, deletion) — without one, a network-level retry after an ambiguous timeout (the call may have already succeeded server-side) causes double execution, which for an inference gateway specifically also means double-billed model calls on top of the mutating side effect.

### 4.6 Zero-Trust MCP for model-serving and routing infrastructure

This topic's Zero-Trust surface is distinct from generic tool-call gating (covered elsewhere in this roadmap): here, the **router's model-selection decision and its MCP tool-scope grant are the same authorization event**, and treating them as separable is the specific architectural gap that creates risk.

**The coupling problem.** A routing decision does not just pick a model — for any tool-augmented request, it implicitly determines which MCP tool servers the selected backend is permitted to call. Enterprise AI-gateway RBAC (the Bifrost pattern) models this explicitly: an **access profile** bundles allowed model providers *and* allowed MCP tool groups *and* budget/rate limits into one unit attached to a role, so a routing decision to "use the cheap self-hosted tier for this request" cannot silently also grant that tier a broader MCP tool scope than the frontier tier would have received — the tool-scope grant must be derived from the caller's entitlement, never from which model tier the router happened to pick for cost reasons.

**Reference flow, specific to this stack:**

```
1. Caller authenticates → virtual key resolves to (role, MCP-tool-group-entitlement)
2. Router picks model tier for cost/quality reasons  -- INDEPENDENT decision
3. Policy engine intersects: effective_MCP_scope = entitlement ∩ tier_max_scope
   (never the union -- a cheaper tier must never receive a WIDER tool scope
   as an accidental side effect of routing logic; tier_max_scope exists
   specifically to cap what a lower-trust/quantized backend may reach)
4. Gateway issues a short-lived, scoped capability token for effective_MCP_scope
   ONLY, mints it fresh per request, and passes it to the model backend
5. Model backend authenticates to the MCP Tool Gateway using ONLY that token
   over mTLS -- the backend never holds a standing, reusable MCP credential
6. MCP Tool Gateway independently re-validates the token's scope on every
   call, not just at session start (closes tool-definition/scope "drift"
   between routing time and call time)
```

**Why this matters for caching specifically — the cache-poisoning-via-tool-scope risk.** If a response generated under a *broad* MCP tool scope (e.g., a frontier-tier request with access to an internal-data tool group) is written into the semantic/prompt cache, and a later request under a *narrower* scope (routed to the cheap tier, without that internal-data entitlement) hits that cached entry, the narrower-scoped caller receives content that was only supposed to be reachable through a tool it is not authorized to invoke — a **privilege-escalation-via-cache** variant distinct from the semantic-cache key-collision attack in §4.9. The mitigation is structural: the **cache key must incorporate the effective MCP scope** (or a scope-equivalence class) alongside the prompt content, so a cache hit is only served across requests with an identical-or-narrower authorized scope — analogous to cache salting (§4.2) but salted by authorization scope rather than tenant identity.

**Auditability requirement specific to this coupling**: the audit record for a routing decision must capture the model tier chosen *and* the MCP scope grant *and* the policy-intersection result from step 3 as one correlated entry — recording only "routed to tier X" without the accompanying scope decision is an incomplete audit trail for any incident investigation into a tool-access issue, since the question "why did this tier get access to that tool" cannot be answered from the routing log alone.

### 4.7 Tool-level RBAC for model/router access

Beyond the MCP-scope coupling above, RBAC for the routing/model layer itself models permissions as (resource × operation) pairs across: virtual keys, model providers/tiers, guardrails, MCP gateways/tool groups, audit logs, adaptive-routing configuration, and user provisioning. Access profiles bundle these into a single unit per role, auto-provisioning every user in that role with independently-tracked budget counters — eliminating hand-issued raw provider credentials entirely. RBAC answers "was this routing/model-access change **permitted**"; the audit log (§4.9) answers "**how** did the system arrive at this decision" — an incident investigation needs both, since a permitted-but-poorly-reasoned routing decision (e.g., a misconfigured cost threshold) is invisible to RBAC checks alone.

### 4.8 PII filtering before caching (detect → redact → audit)

This is a documented, non-hypothetical governance gap specific to caching layers:

- **Retention mismatch**: caches are engineered for hit-rate (LRU by memory budget), not regulatory retention — a hot cache key can persist for months in a high-traffic system, and the user-turn content most likely to carry PII is exactly the content determining that retention.
- **GDPR Article 17 conflict**: as of early 2026, no major LLM provider offers a per-entry cache-eviction API, meaning a user's right-to-erasure request cannot be selectively fulfilled against cached prompt content.
- **Cross-tenant cache-hit ambiguity**: if a cache serves User B a response originally generated from User A's prompt, whether this constitutes processing User A's data on User B's behalf under GDPR is an open legal question, not a settled one.
- **Mitigation pattern — the only one that closes the gap structurally**: redact PII **at the request boundary**, before the prompt reaches any caching or logging layer — redacting only at display/read time leaves raw PII sitting in the cache/warehouse for the interim, which is precisely the retention-mismatch problem above.
- **Timing side-channel**: prefix-cache hit/miss latency differential lets an attacker with backend visibility infer whether a specific (possibly sensitive) prompt was previously cached by another tenant, then reconstruct it incrementally — mitigated by cache salting (§4.2).

### 4.9 Semantic cache poisoning and auditability of routing decisions

**Semantic cache poisoning** is a formally documented, inherent design vulnerability, not an implementation bug: embedding-similarity key matching is a locality-preserving fuzzy hash that does not satisfy the avalanche property required for collision resistance. An attacker crafts an adversarial query semantically similar (in embedding space) to a target query but engineered to elicit a harmful/incorrect cached response; once that pair is cached, any future user issuing the target query receives the poisoned response — a **response-hijacking** failure, categorically worse than staleness. A 2026 NDSS study covering vLLM, SGLang, GPTCache, AIBrix, rtp-llm, and LMDeploy identified six attack-vector classes, including blockwise/multimodal collisions used to bypass content-moderation checks, with all vendors acknowledging the vulnerabilities after responsible disclosure. Defenses: response-level validation before serving any cache hit, cluster-centroid indexing (raises the bar for adversarial collision crafting), per-tenant namespace isolation (never a shared cross-tenant vector index), and treating semantic-cache hit-rate improvements as a security/quality trade-off to be explicitly signed off on, not a free win.

**Auditability of routing decisions**: the audit record (compliance-grade, hash-chained/HMAC-signed, append-only) captures identity, policy version, data-classification tag, MCP-scope grant (§4.6), and decision outcome — explicitly **not** raw prompt/response content, only content fingerprints, to satisfy traceability without creating new PII exposure inside the audit trail itself. The router's separate operational log records the actual model/endpoint chosen; the two are correlated via a shared request ID, following the design principle that policy evaluation happens **before** routing dispatch ("Budget → Policy → PII → Guardrails → Routing"), so a DENY decision means the prompt never reaches any model provider and never needs a routing-cost audit entry. EU AI Act Article 12 requires "automatic recording of events" for high-risk AI systems; compliant implementations export tamper-evident JSONL with a verifiable hash chain where any modification, deletion, or reorder breaks the chain from that point forward — a property the production code in §5 implements directly.

---

## 5. Production Enterprise Code

The module below implements a runnable, self-contained **caching + routing inference gateway**: retries with exponential backoff + full jitter (transient errors only), a per-`(model_tier, region)` circuit breaker (closed→open→half-open), a router that falls back to a default model tier on classifier failure, a semantic-cache layer with scope-aware keys (§4.6), structured JSON logging with correlation IDs, and graceful degradation to a cached/static response as the last resort. Standard library only.

```python
"""
inference_gateway.py

Production-grade caching + routing inference gateway covering Module 15's
core resilience surface: retries w/ backoff+jitter, per-(model_tier,region)
circuit breakers, a router with a classifier-failure fallback to a default
tier, a semantic-cache layer with scope-aware (tenant + MCP-scope) keys to
close the cache-poisoning-via-tool-scope gap (Sec 4.6/4.9), correlation-ID
structured logging, and a graceful-degradation last-resort tier.

All external calls (model backends, classifier, vector-cache store) are
injected as callables so this module is fully testable without a live
inference backend.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import uuid
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
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
    logger = logging.getLogger("inference_gateway")
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
    """Binds one correlation ID to every log line for a single request's
    cache-lookup -> route -> dispatch -> audit chain (Sec 1's request-flow
    narrative)."""

    def __init__(self, correlation_id: Optional[str] = None):
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self._token = None

    def __enter__(self) -> str:
        self._token = _correlation_id.set(self.correlation_id)
        return self.correlation_id

    def __exit__(self, *exc_info) -> None:
        _correlation_id.reset(self._token)


# --------------------------------------------------------------------------
# 2. Failure taxonomy (Sec 4.5)
# --------------------------------------------------------------------------

class InferenceError(Exception):
    """`transient=False` marks permanent errors (malformed request, auth
    failure, invalid tier name, MCP-scope denial) that must never be
    retried."""

    def __init__(self, message: str, transient: bool = True):
        super().__init__(message)
        self.transient = transient


class RoutingError(Exception):
    """Raised by the router/classifier path specifically -- caught by the
    dispatcher to trigger the classifier-failure fallback to a default
    tier (Sec 5's core requirement), never propagated as a hard failure."""


class MCPScopeDeniedError(InferenceError):
    """Raised when the intersection of caller entitlement and model-tier
    max scope (Sec 4.6, step 3) is empty for a tool the model attempted
    to call. Permanent -- never retried."""

    def __init__(self, message: str):
        super().__init__(message, transient=False)


# --------------------------------------------------------------------------
# 3. Exponential backoff with full jitter (Sec 4.3, layer 1)
# --------------------------------------------------------------------------

def backoff_with_full_jitter(attempt: int, base_s: float = 0.2, cap_s: float = 8.0) -> float:
    """AWS-style full jitter: sleep(random(0, min(cap, base * 2^attempt))).
    Avoids thundering-herd resynchronization when many concurrent requests
    retry a degraded model tier simultaneously."""
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
# 4. Circuit breaker: CLOSED -> OPEN -> HALF_OPEN, scoped per
#    (model_tier, region) (Sec 4.3, layer 2)
# --------------------------------------------------------------------------

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str  # e.g. "tier:frontier@us-east" or "tier:quantized-int4@eu-west"
    failure_threshold_ratio: float = 0.5
    window_size: int = 20
    cooldown_s: float = 15.0
    half_open_max_probes: int = 2

    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _outcomes: Deque[bool] = field(default_factory=lambda: deque(maxlen=20), init=False)
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
# 5. Scope-aware semantic cache (Sec 4.6, 4.9 -- cache key incorporates
#    tenant + effective MCP scope, not prompt content alone)
# --------------------------------------------------------------------------

@dataclass
class ScopedSemanticCache:
    """Namespace-isolated per (tenant, effective_mcp_scope) to close the
    cache-poisoning-via-tool-scope gap: a response generated under a
    broader scope must never be served to a request with a narrower one."""

    similarity_threshold: float = 0.95
    _store: dict[str, list[tuple[str, list[float], dict]]] = field(default_factory=dict, init=False)

    @staticmethod
    def _namespace(tenant_id: str, effective_scope: frozenset[str]) -> str:
        scope_fingerprint = hashlib.sha256(
            "|".join(sorted(effective_scope)).encode()
        ).hexdigest()[:16]
        return f"{tenant_id}:{scope_fingerprint}"

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

    def lookup(self, tenant_id: str, effective_scope: frozenset[str],
               embedding: list[float]) -> Optional[dict]:
        ns = self._namespace(tenant_id, effective_scope)
        for _, cached_embedding, response in self._store.get(ns, []):
            if self._cosine(embedding, cached_embedding) >= self.similarity_threshold:
                log.info(json.dumps({"event": "semantic_cache_hit", "namespace": ns}))
                return response
        return None

    def write(self, tenant_id: str, effective_scope: frozenset[str],
              embedding: list[float], response: dict) -> None:
        ns = self._namespace(tenant_id, effective_scope)
        self._store.setdefault(ns, []).append((str(uuid.uuid4()), embedding, response))
        log.info(json.dumps({"event": "semantic_cache_write", "namespace": ns}))


# --------------------------------------------------------------------------
# 6. Router with classifier-failure fallback to a default tier (Sec 2.5)
# --------------------------------------------------------------------------

@dataclass
class ModelRouter:
    classifier_fn: Callable[[str], str]   # returns a tier name
    default_tier: str
    tier_max_scope: dict[str, frozenset[str]]  # Sec 4.6 step 3

    def route(self, prompt: str, caller_entitlement: frozenset[str]) -> tuple[str, frozenset[str]]:
        try:
            tier = call_with_retry(lambda: self._classify(prompt), max_attempts=2, cap_s=1.0)
        except (InferenceError, RoutingError) as exc:
            log.info(json.dumps({"event": "router_fallback_default_tier", "reason": str(exc)}))
            tier = self.default_tier

        effective_scope = caller_entitlement & self.tier_max_scope.get(tier, frozenset())
        log.info(json.dumps({"event": "routing_decision", "tier": tier,
                              "effective_scope": sorted(effective_scope)}))
        return tier, effective_scope

    def _classify(self, prompt: str) -> str:
        try:
            return self.classifier_fn(prompt)
        except Exception as exc:  # classifier service failure -> treat as routing failure
            raise RoutingError(f"classifier unavailable: {exc}") from exc


# --------------------------------------------------------------------------
# 7. Inference dispatcher: cache -> route -> breaker-guarded model call ->
#    fallback chain -> graceful degradation (Sec 4.3, 4.4)
# --------------------------------------------------------------------------

@dataclass
class InferenceGateway:
    router: ModelRouter
    cache: ScopedSemanticCache
    embed_fn: Callable[[str], list[float]]
    model_call_fns: dict[str, Callable[[str], dict]]     # tier -> backend call
    cached_fallback_fn: Callable[[str], Optional[dict]]  # last-known-good static response
    breakers: dict[str, CircuitBreaker]                  # tier -> breaker

    def handle_request(self, tenant_id: str, prompt: str,
                        caller_entitlement: frozenset[str]) -> tuple[str, dict]:
        """Returns (source_tier, response). source_tier is one of
        'cache', <model_tier>, 'next_tier_fallback', or 'degraded' --
        always logged so degraded-mode traffic is observable."""
        embedding = self.embed_fn(prompt)

        # Route first so the cache key can be scoped correctly (Sec 4.6)
        tier, effective_scope = self.router.route(prompt, caller_entitlement)

        cached = self.cache.lookup(tenant_id, effective_scope, embedding)
        if cached is not None:
            return "cache", cached

        tiers_to_try = [tier] + [t for t in self.model_call_fns if t != tier]
        for candidate_tier in tiers_to_try:
            breaker = self.breakers[candidate_tier]
            if not breaker.allow_request():
                log.info(json.dumps({"event": "tier_skipped_breaker_open", "tier": candidate_tier}))
                continue
            try:
                response = call_with_retry(lambda: self._call_tier(candidate_tier, prompt))
                breaker.record_success()
                self.cache.write(tenant_id, effective_scope, embedding, response)
                source = candidate_tier if candidate_tier == tier else "next_tier_fallback"
                log.info(json.dumps({"event": "tier_success", "tier": candidate_tier, "source": source}))
                return source, response
            except InferenceError as exc:
                breaker.record_failure()
                log.info(json.dumps({"event": "tier_failed", "tier": candidate_tier, "reason": str(exc)}))

        cached_static = self.cached_fallback_fn(prompt)
        if cached_static is not None:
            log.info(json.dumps({"event": "fallback_cached_static"}))
            return "degraded", cached_static

        log.info(json.dumps({"event": "fallback_to_degraded_no_cache"}))
        return "degraded", {
            "status": "unavailable",
            "message": "All model tiers are temporarily unavailable; please retry later.",
        }

    def _call_tier(self, tier: str, prompt: str) -> dict:
        fn = self.model_call_fns[tier]
        try:
            return fn(prompt)
        except Exception as exc:
            raise InferenceError(f"tier '{tier}' backend error: {exc}", transient=True) from exc


# --------------------------------------------------------------------------
# Example wiring (graceful degradation end-to-end)
# --------------------------------------------------------------------------

if __name__ == "__main__":
    def fake_classifier(prompt: str) -> str:
        return "frontier" if "complex" in prompt else "quantized"

    def flaky_frontier_backend(prompt: str) -> dict:
        if random.random() < 0.5:
            raise RuntimeError("upstream 503")
        return {"text": "frontier-tier response", "tier": "frontier"}

    def reliable_quantized_backend(prompt: str) -> dict:
        return {"text": "quantized-tier response", "tier": "quantized"}

    def static_last_known_good(prompt: str) -> Optional[dict]:
        return {"text": "stale cached response", "tier": "unknown", "stale": True}

    gateway = InferenceGateway(
        router=ModelRouter(
            classifier_fn=fake_classifier,
            default_tier="quantized",
            tier_max_scope={
                "frontier": frozenset({"internal_data", "search", "code_exec"}),
                "quantized": frozenset({"search"}),
            },
        ),
        cache=ScopedSemanticCache(),
        embed_fn=lambda prompt: [float(ord(c)) for c in prompt[:16].ljust(16)],
        model_call_fns={"frontier": flaky_frontier_backend, "quantized": reliable_quantized_backend},
        cached_fallback_fn=static_last_known_good,
        breakers={
            "frontier": CircuitBreaker(name="tier:frontier", window_size=5, failure_threshold_ratio=0.6, cooldown_s=2),
            "quantized": CircuitBreaker(name="tier:quantized", window_size=5, failure_threshold_ratio=0.6, cooldown_s=2),
        },
    )

    with correlation_scope() as cid:
        log.info(json.dumps({"event": "request_start", "correlation_id": cid}))
        source, response = gateway.handle_request(
            tenant_id="acme_corp",
            prompt="a complex multi-step billing reconciliation query",
            caller_entitlement=frozenset({"search", "internal_data"}),
        )
        log.info(json.dumps({"event": "request_complete", "source": source, "response": response}))
```

This demonstrates every required pattern in one coherent flow specific to a caching+routing inference gateway: the router resolves a tier and intersects the caller's entitlement with that tier's max MCP scope *before* any cache lookup, so the semantic cache is keyed on the resulting scope rather than prompt content alone — directly closing the cache-poisoning-via-tool-scope gap from §4.6; a classifier failure (simulated by wrapping `classifier_fn` in `RoutingError`) falls back to the configured default tier rather than failing the request; the flaky frontier backend (50% failure rate) exercises retry-with-jitter and, once its dedicated breaker trips within a 5-call window, the dispatcher falls through to the next tier in `tiers_to_try` rather than failing the turn outright — with each tier's *independent* breaker meaning a frontier-tier outage never blocks the quantized tier, which is actually healthy; and the final `cached_fallback_fn` path guarantees the dispatcher never hard-fails silently, always returning an observable `degraded` source tier instead.

---

## 6. Architectural System Design Scenarios

### Scenario A — Multi-tenant SaaS platform: cost-optimized inference gateway with cache-poisoning defense

**Problem statement.** A B2B SaaS platform serves AI-assisted support-ticket drafting to hundreds of enterprise tenants through a single shared inference layer. The initial design routed every request to a single frontier model with no caching, producing an unsustainable per-seat cost as usage scaled past 50M requests/month. The platform must cut cost materially without (a) degrading quality on genuinely complex tickets or (b) introducing a cross-tenant data-leakage risk through a shared cache — a documented risk class in this exact deployment pattern (§4.9).

**Proposed architecture.**

```
Tenant request → PII Filter (redact before cache/log, Sec 4.8)
                        │
                        ▼
        Scope-aware Semantic Cache lookup (namespace = tenant + effective
        MCP scope, Sec 4.6/5) -- HIT: return in ~15-40ms, skip model call
                        │ MISS
                        ▼
        RouteLLM-style learned classifier: simple drafting task → quantized
        AWQ INT4 tier (Marlin kernel, self-hosted); complex/escalation-
        flagged ticket → frontier tier (Sec 2.5)
                        │
                        ▼
        Per-tier Circuit Breaker (Sec 4.3) → model backend → response
                        │
                        ▼
        Write to Scoped Semantic Cache (Sec 5) + hash-chained audit
        record correlating tenant, tier, scope, decision (Sec 4.9)
```

**Trade-off evaluation matrix.**

| Dimension | No caching, single frontier model (baseline) | Caching only, no routing | Caching + routing + scope-aware cache keys (proposed) |
|---|---|---|---|
| Cost / 1k runs | Highest (~$23-30 per 1k runs at frontier pricing, §3.1) | Moderate — caching alone can cut 50-90% on repeat-heavy traffic, but every miss still pays frontier price | Lowest — caching absorbs the repeat-query tail, routing shifts the majority of novel-but-simple queries to a ~70%+ cheaper quantized tier |
| Latency P95 | Frontier TTFT on every request (§3.2) | Near-zero on hits; unchanged frontier latency on misses | Near-zero on hits; quantized-tier P95 well under frontier's on the routed majority |
| Quality risk | Lowest (always frontier) | Same as baseline on misses; a poisoned/collided cache entry risks response-hijacking (§4.9) regardless of routing | Router misclassification is the dominant residual risk (§2.5/§5.4 research) — mitigated by confidence-based escalation, not eliminated |
| Security posture | Simplest, but cache-poisoning risk doesn't apply (no cache) | Cache-poisoning and cross-tenant collision risk if the cache is not namespace-isolated per tenant | Namespace isolation per (tenant, effective scope) closes both cross-tenant leakage and the tool-scope-escalation variant from §4.6 |
| Ops complexity | Lowest | Low-medium (vector store, TTL policy) | Higher (classifier training/retraining cadence, per-tier breaker tuning, scope-intersection logic) — justified by the cost delta at this traffic volume |

**Decision rationale.** At 50M+ requests/month, the caching-only option leaves the majority of cost exposure on the table because most support-ticket traffic is novel (low cache-hit rate on the *content*, even if the *task type* repeats), so routing is necessary to capture savings on the non-cached majority. Namespace-isolating the semantic cache by `(tenant, effective_scope)` rather than a single shared index is non-negotiable given this platform's multi-tenant blast radius — a single shared vector index would make the cross-tenant cache-hit legal ambiguity (§4.8) and the semantic-collision attack (§4.9) both live risks against the exact deployment pattern documented in the 2026 NDSS study. The residual risk this architecture accepts is router misclassification (§5.4 research: silent, well-formed-looking failures that standard error monitoring won't catch) — mitigated operationally by instrumenting cost-per-successful-task and fallback-rate trend, not just cost-per-token, as the drift-detection signal.

### Scenario B — Regulated financial-services enterprise: on-prem quantized serving with multi-region compliance

**Problem statement.** A financial-services firm must serve an internal AI-assisted underwriting-support tool using only on-premises/VPC-hosted models for data-residency and regulatory reasons (no third-party API calls with customer financial data), across two regions (EU and US) with a hard requirement that an EU-region outage must never fail over to the US region if doing so would violate GDPR residency terms. The firm also needs auditable proof, per the EU AI Act's high-risk-system provisions, of every model-routing decision made against a given underwriting case.

**Proposed architecture.**

```
EU region                              US region
┌─────────────────────────┐            ┌─────────────────────────┐
│ AI Gateway (region-local)│            │ AI Gateway (region-local)│
│  Redis (in-region only,   │            │  Redis (in-region only,   │
│  Sec 4.4 -- no cross-     │            │  Sec 4.4)                 │
│  region round trips)      │            │                            │
│         │                 │            │         │                  │
│         ▼                 │            │         ▼                  │
│  Residency-aware Policy   │            │  Residency-aware Policy   │
│  Layer: EU traffic MUST   │            │  Layer: US traffic may    │
│  stay in-region even on   │            │  fail over to EU only if  │
│  full regional outage     │            │  no residency constraint  │
│  (Sec 4.4's explicit      │            │  applies to that case      │
│  policy-before-failover   │            │                            │
│  rule)                    │            │                            │
│         │                 │            │         │                  │
│         ▼                 │            │         ▼                  │
│  FP8-quantized frontier-  │            │  Same tier structure       │
│  class model (Hopper GPU  │            │                            │
│  pool) + AWQ INT4 fallback│            │                            │
│  tier for burst capacity  │            │                            │
└──────────┬────────────────┘            └──────────┬────────────────┘
           │                                          │
           └───────────────┬──────────────────────────┘
                            ▼
             Shared control-plane Postgres (multi-AZ, NOT
             cross-region for case data -- only for global
             budget/key config) with allow_requests_on_db_
             unavailable=true (serve from region-local cache
             on shared-DB outage, Sec 4.4)
                            │
                            ▼
             Hash-chained, append-only audit log per region,
             replicated to a compliance data warehouse (EU AI
             Act Article 12): tier, quantization level, policy
             version, MCP scope, decision outcome -- content
             fingerprints only, never raw case data (Sec 4.9)
```

**Trade-off evaluation matrix.**

| Dimension | Third-party API, managed cross-region routing (rejected — non-compliant) | On-prem FP16 only, no quantization | On-prem FP8/AWQ tiered quantization + residency-aware failover (proposed) |
|---|---|---|---|
| Cost / 1k runs | Lowest sticker price, but non-viable — data-residency requirement rules this out entirely regardless of cost | Highest GPU-hour cost per request (no throughput multiplier from quantization) | ~70-74% lower GPU-hour cost per request at AWQ INT4 vs. FP16 baseline (§3.1), FP8 as the primary near-lossless tier |
| Latency P95 | N/A (non-compliant) | Acceptable but requires the largest GPU fleet to hit the same throughput as the quantized alternative | Comparable or better P95 (§3.2's FP8 first-token constraint table) at a fraction of the fleet size |
| Compliance / residency | Fails outright — "may route beyond geographic boundaries" is disqualifying for this workload (§4.4) | Compliant by construction (on-prem only) | Compliant by construction, plus explicit residency-aware policy gate that blocks failover even under regional outage pressure |
| Accuracy risk | N/A | Lowest quantization-related risk (no quantization at all) | Requires the trajectory-level eval gate from §3.4's Named Trade-off 1 before shipping INT4 into underwriting-support reasoning paths — perplexity alone is an insufficient sign-off for this workload class given the documented multi-step-task degradation pattern |
| Ops complexity | Lowest (fully managed) | Medium (on-prem fleet ops, no quantization pipeline) | Highest — on-prem fleet ops plus a quantization-calibration/eval pipeline plus per-region residency-policy logic — justified because the alternative (FP16-only) requires a substantially larger capital GPU footprint to hit the same throughput target |

**Decision rationale.** The managed-third-party option is eliminated on compliance grounds alone before cost or latency even enter the comparison, since "may route beyond geographic boundaries" (§4.4) is categorically incompatible with a hard residency requirement — this is a case where the trade-off matrix's normal cost/latency weighting is overridden by a disqualifying constraint. Between the two on-prem options, quantization is adopted specifically because the GPU capital cost of an FP16-only fleet at this firm's underwriting volume is prohibitive, and FP8's near-lossless quality profile (>99% retention on Hopper-class hardware) removes most of the accuracy objection for the majority of the traffic; AWQ INT4 is scoped only to burst-capacity handling, not the primary underwriting-reasoning path, specifically because underwriting support is exactly the kind of multi-step reasoning workload where §3.4's Named Trade-off 1 (perplexity-blind task-completion degradation) is most dangerous — the firm requires a trajectory-level eval gate before that tier is trusted with primary traffic, not just a benchmark perplexity check. The residency-aware policy layer sitting strictly *before* any failover decision closes the specific compliance gap the research identifies as a common architectural blind spot: treating cross-region failover as a purely technical availability decision rather than a policy decision that must consult residency rules first.
