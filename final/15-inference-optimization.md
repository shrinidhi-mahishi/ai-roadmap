# Topic 15: Inference Optimization

## What Is This?

**Inference** is the process of running a trained model to generate output — every time you send a message to ChatGPT or call the Claude API, that's inference. It's expensive because the model needs to read its entire set of weights (billions of numbers) from GPU memory for every token it generates.

The two phases of inference:
- **Prefill**: The model reads your entire input prompt at once. This is fast because it can process all tokens in parallel (like reading a whole page at a glance).
- **Decode**: The model generates output tokens one at a time, each depending on all previous tokens. This is slow because it's inherently sequential (like writing a sentence word by word).

**KV cache** is the most important concept: as the model processes each token, it computes intermediate values (called keys and values) that it needs to reference when generating future tokens. Instead of recomputing these for every new token, the model stores them in GPU memory — this is the KV cache. Think of it like scratch work on a whiteboard: instead of redoing the math each time, you keep your intermediate results visible.

The problem: KV cache grows with sequence length and eats up expensive GPU memory. A single 128K-token conversation can use 5+ GB of KV cache. This is why inference optimization matters — techniques like **prompt caching** (reuse KV cache for repeated prefixes), **batching** (process multiple requests together to better utilize the GPU), and **quantization** (use smaller numbers to represent model weights, trading a tiny bit of accuracy for 2-4x memory savings) can cut costs by 50-90%.

## Why It Matters

Inference cost is the dominant expense in production AI systems. A naive deployment might cost $10 per 1,000 requests; an optimized one might cost $1. Understanding these optimization techniques is the difference between an AI product that's profitable and one that bleeds money.

---

## 2. Core Concepts

### Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│  CONTROL PLANE                                                   │
│  ┌───────────┐ ┌────────────┐ ┌────────────┐ ┌───────────────┐  │
│  │Autoscaler │ │Health Check│ │Rate Limiter│ │  SLA Planner  │  │
│  │  (xPyD)   │ │ (cooldown) │ │(per tenant)│ │   (Dynamo)    │  │
│  └───────────┘ └────────────┘ └────────────┘ └───────────────┘  │
└──────────────────────────┬───────────────────────────────────────┘
                           │ manage
┌──────────────────────────v───────────────────────────────────────┐
│  DATA PLANE (Request Flow)                                       │
│                                                                  │
│  ┌────────┐  ┌──────────┐  ┌───────────┐  ┌─────────────────┐  │
│  │ Client │─>│ Gateway  │─>│Cache Layer│─>│     Router      │  │
│  │Request │  │Auth+Salt │  │Exact /    │  │RouteLLM/Cascade │  │
│  └────────┘  └──────────┘  │Semantic   │  │/Cache-Affinity  │  │
│                            └─────┬─────┘  └────────┬────────┘  │
│                             hit  │            miss  │           │
│                                  v                  v           │
│                           ┌──────────┐     ┌──────────────┐    │
│                           │  Return  │     │ Prefill Pool │    │
│                           │  Cached  │     │(compute-bound)│    │
│                           │ Response │     └──────┬───────┘    │
│                           └──────────┘    KV xfer │ (NIXL)    │
│                                                   v            │
│  ┌──────────────┐                         ┌──────────────┐     │
│  │  Streaming   │<────────────────────────│ Decode Pool  │     │
│  │  Response    │                         │(mem-BW bound)│     │
│  └──────────────┘                         └──────────────┘     │
└──────────────────────────────────────────────────────────────────┘
```

### The Two Clocks

Autoregressive serving splits into two phases with opposite hardware profiles:

- **Prefill:** Process all prompt tokens in parallel. Compute-bound. Dominates Time To First Token (TTFT).
- **Decode:** Generate one token per step (or a speculative tree), reading the growing KV cache. Memory-bandwidth-bound. Dominates Inter-Token Latency (ITL) / Time Per Output Token (TPOT).

Think of prefill as loading a program into memory, decode as executing it step-by-step. A long prefill stalls every in-flight decode in a naive colocated system. Sarathi-Serve measured up to 28.3x higher token-by-token latency when prefill and decode share the same iteration budget without isolation.

### The Two Planes

| Plane | What it is | Clock | Failure if mixed |
|-------|-----------|-------|------------------|
| **Control** | Request admission, cache-affinity scoring, pool sizing, fallbacks, rate-limit budgets, tenant isolation | Queue + 429 windows | App code that "picks the GPU with the cache hit" by hashing the raw prompt in the client |
| **Data** | KV blocks, weights, activations, GEMM/attention compute, offloaded state | TTFT / ITL | Prefill and decode sharing one iteration budget without a token cap; treating KV as ephemeral scratch when it is a materialization of the prompt |

NVIDIA Dynamo is orchestration above engines: it coordinates vLLM, SGLang, or TensorRT-LLM as a multi-node system. llm-d is the Kubernetes-native variant: Gateway API Inference Extension plus a Precise Prefix-Cache Scorer over a global KV-block index. LiteLLM is the API control plane: cooldowns, order-based failover, RPM/TPM-aware routing.

### Send Less First

Before optimizing model execution, optimize what you send. OpenAI Agents SDK can continue runs via `conversation_id` or `previous_response_id` instead of resending the entire transcript. Google ADK compacts older history once token or turn thresholds are hit, and ADK artifacts keep large blobs out of the default prompt until explicitly loaded. LangGraph checkpoints at super-step boundaries let the graph resume without recomputing already-persisted sibling outputs. Mechanically, inference optimization often starts with "send less," not with faster model kernels.

### KV Cache as State, Not Scratch

The KV cache is working memory that materializes your prompt. Per token, per layer, BF16: `2 x n_kv_heads x d_head x 2 bytes`. For multi-head attention (MHA), `n_kv_heads = n_q`. Grouped-Query Attention (GQA, Ainslie et al., EMNLP 2023) shares KV across query groups: Llama-class 64 Q / 8 KV is an 8x KV cut vs MHA. Multi-Query Attention (MQA, Shazeer 2019) uses one KV head for all queries. Multi-Latent Attention (MLA, DeepSeek-V2) caches only latent `c_KV` plus a short RoPE key `k^R`, achieving 93.3% KV reduction vs DeepSeek 67B MHA and 5.76x max generation throughput.

DeepSeek-V3 (671B total / 37B active) uses ~70 KB/token KV vs Llama-3.1-405B 516 KB/token and Qwen-2.5-72B 327 KB/token.

Lose the pod, lose the KV unless you have hierarchical offload (LMCache, Mooncake, Dynamo KVBM). This is not Redis-style caching until you add a durable tier.

### Effective Total Tokens Per Run

```text
effective_total_tokens_per_run
  = planner_tokens
  + executor_tokens
  + verifier_tokens
  + replayed_history
  + tool_outputs
  - cached_or_compacted_prefix
```

Optimization reduces each term: cache cuts replayed history and static prefixes, compaction shrinks replayed history, artifacts externalize tool outputs, and routing assigns cheaper models to executors.

## 3. How It Works

### 3.1 Prefill / Decode: Colocated vs Disaggregated

**Colocated (Orca/vLLM default):** Prefill and decode batch into the same forward pass. Simple, single pool. Trade-off: long prefill stalls all decodes. DistServe published result on colocated SOTA: 7.4x more requests or 12.6x tighter SLO while staying inside TTFT+TPOT for >90% of requests. Splitwise argued for heterogeneous pools (e.g., H100 prefill / A100 decode) and measured 1.4x throughput and ~20% lower cost.

**Disaggregated data path (Dynamo):**

1. PrefillRouter picks a prefill worker by KV overlap + load.
2. Prefill writes KV and returns `disaggregated_params`.
3. Decode worker pulls KV via NIXL (NVLink / InfiniBand / UCX), non-blocking so other requests keep running.
4. xPyD (x prefill, y decode) is runtime-reconfigurable.

Dynamo's marketing claim for KV-aware routing: ~2x faster TTFT by skipping redundant prefill. Mooncake (Kimi / Moonshot, FAST 2025 Best Paper) runs this at cluster scale: thousands of nodes, >100B tokens/day; production A800/H800 lifts of +115% / +107% requests vs prior system; simulated long-context throughput up to +525% under SLO.

vLLM+Mooncake 1P1D microbench (Qwen3-8B, 8x CX7 RoCE): 142.25 GB/s KV transfer (71.1% of ~200 GB/s theoretical); 32,768-token prompt, 4.50 GB KV, 31.65 ms transfer = 4.2% of TTFT.

**When not to disaggregate:** Short prompts + high QPS (KV transfer and extra hop dominate). Single-node <8 GPUs (colocation + chunked prefill is the default). DistServe's own placement rule: put P and D on the same node when interconnect cannot hide transfer.

### 3.2 Caching: Five Layers That Are Not the Same Cache

| Layer | Match key | What is stored | Hit savings | Wrong if... |
|-------|-----------|---------------|-------------|-------------|
| **KV / PagedAttention** | Sequence's own past tokens | K,V per layer (or MLA latent) | Decode does not recompute prefill | You confuse this with cross-request reuse |
| **Prefix / APC** | Hash of token blocks + parent hash (+ LoRA id, mm hash, salt) | KV blocks shared across requests | Skip prefill of the shared prefix | One-token prefix mutation; xxhash collision; no tenant salt |
| **Prompt cache (hosted APIs)** | Exact rendered prefix at a breakpoint + `prompt_cache_key` | Provider-side KV (you never see it) | Input billed at 0.1x; TTFT drop | Timestamp/tools before the breakpoint |
| **Semantic cache** | Embedding kNN above a threshold | Text of a previous response | Skip the LLM entirely | Threshold too low = wrong answer served as truth |
| **Speculative cache** | Draft KV + target KV; tree attention state | Two (or more) KV pools + verify buffer | Extra decode tokens per target forward | Draft mismatch = rollback; VRAM x (1+draft) |

#### PagedAttention (Kwon et al., SOSP 2023)

KV is virtual memory: fixed-size blocks, block table per sequence, near-zero internal fragmentation, copy-on-write sharing for beam/parallel sampling. vLLM: 2-4x throughput vs FasterTransformer and Orca at comparable latency.

vLLM v1 automatic prefix caching hashes each block by `(parent_hash, block_tokens, extra_hashes)` so a shared system prompt is one set of physical blocks. Hash algorithms: `sha256` (default), `sha256_cbor` (reproducible), `xxhash` / `xxhash_cbor` (faster, not cryptographically secure). `cache_salt` is mixed into the first block hash so only same-salt requests share.

**Timing side-channel:** KVGov (2026) measures a cold/cached TTFT ratio of 0.22 on Qwen2.5-7B / vLLM 0.26.0 / A100 -- the channel is exploitable at production scale. Patched in vLLM >=0.9.0 via salting.

#### SGLang RadixAttention (Zheng et al., 2024)

Instead of discarding KV when a request ends, retain it in a radix tree (compressed trie) keyed by token sequences; longest-prefix match on admission; LRU/LFU/FIFO eviction of leaves so shared roots (system prompts) survive; refcount so in-flight nodes are unevictable. Compatible with continuous batching, paged layout, tensor parallelism. Up to 5x throughput vs baselines on their structured-program suite; largest win is TTFT on prefix hits. Scheduler policy `--schedule-policy lpm` (longest prefix match) is cache-aware admission.

#### Hosted Prompt Cache (Productized Prefix Cache with a Bill)

**OpenAI:**
- Eligible models: gpt-4o and newer. Routing: `prompt_cache_key` primary, prefix hash secondary.
- GPT-5.6+: exact match at breakpoints; implicit breakpoint on latest user/tool message; optional `prompt_cache_options.mode=explicit`; min 1,024 tokens through the breakpoint; cache writes 1.25x uncached input; cache reads 0.1x; TTL 30m (refreshes on reuse). Keep a given `prompt_cache_key` at ~15 RPM or hit rate falls.
- Pre-5.6: automatic best-effort prefix reuse; no write fee; min 1,024-2,048 by model; hits in 128-token increments; in-memory retention typically 5-10 min idle, max ~1 h; extended up to 24 h.
- Isolation: cache sharing limited to the organization.

**Anthropic:**
- Explicit `cache_control: { type: "ephemeral", ttl: "5m"|"1h" }` and/or automatic caching. Prefix = tools -> system -> messages up to the marked block.
- Multipliers vs base input: 5m write 1.25x, 1h write 2x, read 0.1x.
- TTL refreshes on hit. Lifetime measured from request start.
- Min tokens: 512 (Opus 5 / Fable 5 / Mythos 5) ... 4,096 (Haiku 4.5, some Opus 4.x). Silent no-op below min.
- Lookback: 20 content blocks past the last write.
- Isolation: org-level; workspace-level on some platforms. KV + hashes in memory only, ZDR-eligible; not stored at rest.
- Anthropic often excludes cache_read_input_tokens from ITPM. One documented example yields about 10,000,000 effective total input tokens/minute from a 2,000,000 ITPM ceiling at 80% cache hit rate.

**Gemini / Vertex:**
Implicit (default, 90% off on hits, write = standard input, no storage fee) vs explicit (guaranteed discount: 90% on Gemini 2.5+, 75% on Gemini 2.0; plus storage dollar/MTok-hour). Min tokens: Gemini 2 family 2,048; Gemini 3 family 4,096. Explicit TTL min 1 minute; no documented max.

**Break-even:** Anthropic 5m: write 1.25, read 0.1 => first hit pays back the 0.25 premium; second read is net-cheaper. 1h write 2.0 => need more hits. OpenAI GPT-5.6 same 1.25 / 0.1 shape. Pre-5.6 OpenAI: write is free => any hit is pure savings.

#### Semantic Cache (GPTCache, Redis LangCache / RedisVL)

Embed the query, HNSW kNN, threshold (commonly discussed 0.85-0.95 cosine -- not a universal constant; set per task with a false-hit eval set). Hit returns a previous completion, not KV. Sub-ms lookup is the Redis claim. This is an application cache in front of the model. It does not reduce prefill on a miss. Tenant + model-version + locale must be TAG filters in the same `FT.SEARCH` or you cross-talk answers.

Correctness risk framing: similarity-based reuse can return "close enough" prior answers whose hidden constraints differ from the new request. Correctness failure disguised as optimization win.

#### Speculative Decoding Cache

Leviathan et al. ICML 2023: draft model proposes gamma tokens; target verifies in one forward; rejection sampling recovers the exact target distribution; 2x-3x on T5-XXL vs T5X. Medusa: extra heads on the target, tree attention, no separate draft weights. EAGLE: draft predicts hidden states, higher accept rate. vLLM blog: up to 2.8x with their scheduler/memory changes. DeepSeek-V3 uses MTP (multi-token prediction) as a trained speculative head.

Cache implication: you store KV for draft and target; on reject you truncate KV back to the last accepted token. Acceptance rate alpha is the NFR: low alpha burns extra FLOPs and pollutes the batch token budget.

#### Cache-Aware Routing

Dynamo KV router: cost = f(load, overlap). `overlap_credit_blocks` can weight device/host/shared memory differently; `router_temperature` softmax-samples among workers to avoid herding. llm-d: `kvevents.Pool` -> block index -> `kvcache.Index` -> Precise Prefix-Cache Scorer (% of this request already on each pod). Mooncake prefill scheduler: chained block hashes, compare against each prefill instance's keys, pick max `prefix_len` under load. OpenAI's `prompt_cache_key` is the hosted equivalent of affinity routing.

### 3.3 Routing: Five Mechanisms

| Mechanism | Decision time | Extra model calls | Typical win |
|-----------|--------------|-------------------|-------------|
| **Complexity / preference router** (RouteLLM) | Before any LLM | 0 (tiny classifier) | >2x cost cut vs always-strong; MT Bench CPT(50%) approx 37% GPT-4 calls, score 8.8 vs GPT-4 9.3 (95%); up to 75% cost vs random |
| **Cascade** (FrugalGPT) | After a cheap answer fails a judger | 1..k | Match GPT-4 with up to 98% cost cut, or +4% accuracy at same cost |
| **Cascade routing** (De Koninck et al., ICML 2025) | Hybrid | Variable | Proves when to combine; quality estimator is the bottleneck |
| **Fallback / HA** (LiteLLM) | On error | Retries | `order=1->2->fallback`; 429 puts deployment on `cooldown_time`; separate `content_policy_fallbacks` / `context_window_fallbacks` |
| **LoRA multiplex** | Per request adapter id | 0 | Punica SGMV: 12x throughput vs SOTA, ~2 ms extra latency/token. S-LoRA: thousands of adapters, up to 4x vs naive vLLM LoRA, 30x vs PEFT; 2,000 adapters on one GPU |

**Planner/executor routing economics:** The corpus repeatedly converges on "strong planner, cheaper bounded executors." LLMCompiler is the clearest benchmark: up to 3.7x lower latency and 6.7x lower cost than ReAct when dependency-aware parallelism is possible.

**Geo routing** is not the same as cache-affinity routing. Data-residency endpoints pin the control+data region. Cache locality wants the same replica that holds the prefix. A user in EU with a US-warm prefix cache loses both: either a miss (full prefill) or a residency violation.

### 3.4 Batching and Schedulers

#### Orca (Yu et al., OSDI 2022)

Invented **iteration-level scheduling** (continuous batching): after each forward, finished sequences leave, waiting sequences enter. Selective batching: linear/elementwise ops batch across ragged lengths; attention is per-sequence. GPT-3 175B: 36.9x throughput vs FasterTransformer at equal latency.

#### vLLM Scheduler (v1)

Three queues: waiting (not yet prefilled), running (decoding), swapped (KV spilled to CPU). After each iteration: free finished blocks -> maybe swap -> admit waiting under `max_num_batched_tokens` / KV headroom. Prefix caching is on by default in v1.

#### Chunked / Stall-Free Prefill (Sarathi-Serve)

Cap tokens per iteration: pack all running decodes first, then leftover budget as a prefill chunk (chunk size multiple of KV block size except the last). Removes the "generation stall." TensorRT-LLM IFB (in-flight batching) is the NVIDIA name for the same iteration-level mix. Knobs: `max_batch_size`, `max_num_tokens` (often started at 8,192-16,384), `enable_chunked_prefill`, `free_gpu_memory_fraction` (default 0.9; back off to 0.7-0.8 on OOM), `enable_block_reuse`.

#### SGLang Scheduler

Waiting queue + running batch; policies FCFS / LPM / DFS-weight; `--chunked-prefill-size` (tune down to 4,096/2,048 on prefill OOM); `--mem-fraction-static` ~0.9; `--schedule-conservativeness` as memory headroom. Radix match runs before priority so LPM can pack cache-friendly requests into the same batch.

#### FlashAttention (Kernel, Not Scheduler)

FA1 (Dao et al., NeurIPS 2022): IO-aware tiling, linear HBM instead of quadratic attention materialization. FA2 (2023): better work partitioning, ~2x vs FA1, 50-73% of A100 peak, up to 225 TFLOP/s/GPU training. FA3 (Hopper, 2024): warp-specialized async + FP8; 1.5-2.0x vs FA2 FP16, up to 740 TFLOP/s (75% H100); FP8 ~1.2 PFLOP/s, 2.6x lower numerical error than naive FP8 attention. Decode still needs a paged FA variant (FlashInfer / TRT-LLM FMHA) because K/V are non-contiguous blocks.

### 3.5 Quantization Topology

Two independent tensors: weights and KV (activations are a third, in W8A8).

| Recipe | What shrinks | Hardware | Role |
|--------|-------------|----------|------|
| **FP8** (E4M3/E5M2; Hopper/Blackwell tensor cores; Transformer Engine) | W and/or A and/or KV, 2x vs BF16 | H100+ | Default production quant on modern NVIDIA |
| **INT8 SmoothQuant** (Xiao et al., 2022) | W8A8 via migrating activation outliers into weights | Ampere+ INT8 tensor cores | Ada/Ampere when FP8 unavailable |
| **W4A16 GPTQ** (Frantar et al., ICLR 2023) | Weights 4-bit, second-order compensation | Any, dequant to FP16 on GEMM | Offline PTQ, calibration-set sensitive |
| **W4A16 AWQ** (Lin et al., MLSys 2024 Best Paper) | Weights 4-bit, protect salient channels using activations | Same | Generally better generalization than GPTQ at 4/3-bit; TinyChat 3.2-3.3x vs HF FP16 |
| **FP8 KV** (TRT-LLM / DeepSeek R1 Blackwell blog) | KV 2x | Hopper+ | TRT-LLM: +6% E2E throughput at same concurrency plus higher max concurrency; GSM8K "no meaningful drop" |
| **KIVI 2-bit KV** (Liu et al., ICML 2024) | KV ~4x vs FP16 with residual FP window | CUDA | Per-channel K, per-token V; 2.6x peak memory; up to 4x batch, 2.35-3.47x throughput |
| **QServe W4A8KV4** (Lin et al., 2024) | All three | A100 / L40S | vs TRT-LLM: Llama-3-8B 1.2x A100 / 1.4x L40S; Qwen1.5-72B 2.4x A100 / 3.5x L40S; claimed ~3x dollar serving on L40S vs A100+TRT-LLM |
| **NVFP4 / MXFP4** | W and KV on Blackwell | sm100/103 | TRT-LLM matrix: DeepSeek-R1 NVFP4 + FP8 KV on Blackwell |

MLA + FP8 KV stack: cache the latent in FP8, attention as absorbed MQA in FP8.

### 3.6 LangGraph Node Caching & Checkpoints

LangGraph documents `CachePolicy(ttl=...)` and cached node returns via `__metadata__.cached = True`, while checkpoints at super-step boundaries let the graph resume without recomputing already-persisted sibling outputs. This turns the optimization unit from "whole conversation" into "reusable subgraph result."

Checkpoints provide durability: the graph can restart mid-execution without losing progress. Combine with node caching to avoid redundant calls when the same node is visited with the same inputs across runs.

### 3.7 Capacity Formula

```text
max_completed_runs_per_minute
  = min(
      provider_rpm / avg_model_turns_per_run,
      provider_tpm / avg_total_tokens_per_run
    )
```

Optimization moves both denominators down: fewer turns (routing, caching entire turns) and fewer total tokens (prefix cache, compaction, artifacts).

## 4. Key Patterns & Best Practices

### 4.1 Prompt Structuring for Cache Hits

**Stable prefix, dynamic suffix.** Place tools, system prompt, shared context before variable user messages. Ensure ordering is deterministic: tool JSON key order, image bytes, whitespace, ChatML vs Harmony template. Timestamp/ISO date in the system prompt is a common thrashing bug.

**Breakpoint discipline (hosted APIs).**
- OpenAI GPT-5.6: implicit breakpoint at latest user/tool message; explicit mode available. Min 1,024 tokens through the breakpoint.
- Anthropic: mark `cache_control` on the last stable block. Min tokens varies by model (512 to 4,096). Lookback is 20 content blocks.
- Gemini: automatic or explicit caching; min tokens 2,048 (Gemini 2) or 4,096 (Gemini 3).

**Continuation APIs.** OpenAI Agents SDK: use `conversation_id` or `previous_response_id` instead of resending the entire transcript. Google ADK: compacts older history once token or turn thresholds are hit; artifacts keep large blobs external until explicitly loaded.

### 4.2 Routing Strategy Selection

| Workload | Recommended routing | Why |
|----------|---------------------|-----|
| High-volume FAQ/support | Semantic cache + RouteLLM complexity router | Avoid LLM entirely on repeat questions; cheap model for simple |
| Multi-step agent with bounded executors | Strong planner, cheaper executors (LLMCompiler pattern) | Amortize reasoning cost; executors are tool-calling or simple QA |
| Latency-critical interactive | Pin strong model; HA fallbacks only | Cascade adds serial latency; router misclassification risk |
| Cost-sensitive batch | FrugalGPT cascade with quality judger | Serial latency acceptable; up to 98% cost cut |
| Regulated workflows (health/finance) | Pin model; no cost router on PHI | Auditability and consistency over cost |

**Router becomes worthwhile when it reduces repeated high-end reasoning turns or isolates workers with much smaller context.**

### 4.3 Batching & Scheduling Configuration

**Default starting point (vLLM/SGLang):**
- `max_num_tokens`: 8,192-16,384 (continuous batching token budget per iteration)
- `enable_chunked_prefill`: true (stall-free)
- `enable_block_reuse` / `enable_prefix_caching`: true (v1 default)
- `free_gpu_memory_fraction` / `mem-fraction-static`: 0.9 (back off to 0.7-0.8 on OOM)
- `schedule-policy`: lpm (longest prefix match) for cache-aware admission (SGLang)

**When to tune down:** Prefill OOM -> reduce `chunked-prefill-size` to 4,096 or 2,048. High p99 ITL with bursty prefills -> disaggregate or increase chunking granularity.

**When to disaggregate:** TTFT and ITL SLOs both bind; DistServe/Dynamo/Mooncake justified. Short prompts + high QPS -> colocation wins.

### 4.4 Quantization Recipe Selection

| Use case | Recipe | Quality trade-off |
|----------|--------|-------------------|
| Production serving, modern NVIDIA (H100/B200) | FP8 W+A+KV | Minimal; GSM8K/MMLU typically unchanged |
| Ampere/Ada, no FP8 tensor cores | INT8 SmoothQuant | Small drop; watch reasoning evals |
| Memory-constrained, small batch | W4A16 AWQ | Better than GPTQ at 4/3-bit; eval on your domain |
| Long-context KV pressure | FP8 KV or KIVI 2-bit + residual window | KIVI: eval retrieval-in-context; FP8 KV "no meaningful drop" per TRT-LLM |
| Extreme density (research/edge) | QServe W4A8KV4 | Full eval suite required; perplexity/long-context fail first |
| Blackwell fleet, DeepSeek-class MoE | NVFP4 + FP8 KV + MLA | MLA already 93.3% KV reduction; stack further compression |

**Quality gate:** A quantized checkpoint is a new model version. Gate on agent eval + safety eval, not just MMLU. Over-quantization shows up as "the model got dumber" tickets.

### 4.5 Isolation & Multi-Tenancy

**Prefix cache isolation primitives:**
- vLLM/SGLang: `cache_salt` = HMAC(server_secret, tenant_id). Mix into first block hash so only same-salt requests share.
- OpenAI: organization-scoped; plus `prompt_cache_key` routing.
- Anthropic: org-level; workspace-level on some platforms.
- Gemini explicit: project / cache resource IAM.
- Semantic (Redis): TAG `tenant` in the same kNN query.

**Never accept a client-supplied salt.** Gateway injects it. KVGov boundary salt estimated 93% of prefix benefit retained -- research, not a vLLM flag yet. For hostile multi-tenant, separate clusters are the product isolation unit.

**xxhash is a security decision, not a perf default, for multi-tenant.** Prefer sha256_cbor if hashes leave the box.

### 4.6 Circuit Breakers & Resilience

**Control-plane circuits:**
- Per-deployment cooldown (LiteLLM): after N failures, skip for cooldown_time. 429 is a first-class cooldown trigger.
- Per-tenant RPM/TPM at the gateway: do not wait for OpenAI to 429 the whole org key.
- Token-budget circuit: if KV utilization > threshold (commonly 85%), stop admitting prefills. Surface as 429 with Retry-After, not 500.
- Semantic-cache circuit: if embedder 5xx, fail open to the LLM (availability) or fail closed (cost cap) -- pick one in the design review.
- Spec-decode circuit: if moving average alpha < threshold, disable spec for that model (it is stealing token budget from the batch).

**Retries:** Exponential backoff + jitter; cap max_fallbacks. Cascade is a retry with a different model -- count it in the error budget or you will hide outages as "quality variance."

**Three-type resilience split:**
- **Cache drift:** Exact-prefix cache retrieval is brittle; when serialization drifts, optimization silently degrades into repeated cache writes and fresh-input misses. Prompt formatting discipline is part of system resilience.
- **Route drift:** Router decisions are usually application logic; a route can be efficient and still unauthorized.
- **Tool/auth drift:** Shared cache, shared knowledge plane, or shared worker pool can become a cross-tenant leakage path.

## 5. System Design Considerations

### 5.1 NFR Metrics (What to Put on the SLO Doc)

| Metric | Plane | Notes |
|--------|-------|-------|
| **TTFT** p50/p95/p99 | Prefill + queue | Prefix/prompt-cache hits move p50 more than p99 (p99 is still a miss + queue). Vendor APIs do not publish these; measure. |
| **ITL / TPOT / TBT** p95 | Decode | Chunked prefill bounds the stall; disagg removes it. Sarathi: naive hybrid up to 28.3x TBT vs decode-only. |
| **E2E latency** p99 | Sum + tools | Agent loops: cache TTL can expire during a long stream. |
| **Goodput** | Control | DistServe's definition: max QPS with TTFT and TPOT SLOs both met for >90%. Throughput without this is a vanity metric. |
| **Cache hit rate** | Prefix / prompt / semantic | Split token hit rate vs request hit rate. OpenAI: `cached_tokens` / `cache_write_tokens`. Anthropic: `cache_read_input_tokens` / `cache_creation_input_tokens`. |
| **KV utilization** | Data | % of GPU KV pool in use. At ~100% you swap or reject (Mooncake early-reject). |
| **Accept length alpha** | Spec decode | Tokens kept per verify. Below ~1.5, spec often loses. |
| **RPM / TPM / RPD** | Control | OpenAI: whichever exhausts first. Headers: `x-ratelimit-limit-requests\|tokens`, `remaining-*`, `reset-*`. 429 -> honor `Retry-After`. Limits are tier x model and change; read the dashboard. |
| **Concurrency (in-flight seqs)** | Decode | Set by KV bytes, not by "batch size 32." MLA/GQA/KV-quant raise this cap. |

### 5.2 Decision Matrix (Control-Plane Choice)

| Workload | Cache | Route | Batch | Quant | Why |
|----------|-------|-------|-------|-------|-----|
| SaaS chatbot, shared system prompt, many tenants | APC + gateway HMAC salt; hosted prompt cache with per-tenant prompt_cache_key | HA fallbacks, not quality cascade | Continuous + chunked prefill | FP8 W+KV | Isolation > max hit rate |
| RAG / long doc QA, same corpus, many questions | Prefix or Gemini explicit cache of the doc; LMCache for self-host | Cache-affinity router | Disagg P/D if TTFT SLO tight | FP8; consider KIVI if context >> 32k | Prefill dominates dollar and TTFT |
| Agent, 40-turn, 20 tools | Breakpoint after tools+system, before each turn; watch 20-block lookback | Pin model; fallbacks for 429 | Chunked prefill; cap max_tokens | FP8; spec decode if alpha high on this schema | Tools mutate prefix; implicit OpenAI 5.6 writes junk |
| Regulated (health/finance) | Disable cross-tenant APC; no semantic cache; in-memory TTL only | No cost router on PHI | Colocate in-region; geo pin | Conservative (FP8 or BF16) | PII in KV = in-scope |
| Multi-LoRA product (N customers x 1 base) | Prefix salt per tenant; adapters in extra_hash | Punica/S-LoRA multiplex, not N replicas | Heterogeneous LoRA batch | Base FP8; adapters FP16 | Punica 12x is the economics |
| Code / reasoning, quality-first | Prefix OK | Strong model; cascade only with a reliable judger | Disagg for ITL SLO | Avoid INT4 until eval; FP8 KV OK if GSM8K-class holds | Over-quant shows up as "the model got dumber" tickets |
| Burst FAQ / support | Semantic cache after policy/PII filter, TTL short | Cheap model + escalate | High batch | INT4 on the cheap tier | False-hit risk is the design constraint |
| Global app + residency | Regional KV indexes; no cross-region prefix | Geo DNS + regional pools | Per-region xPyD | Same checkpoint hash all regions | 10% OpenAI residency uplift vs a residency incident |

### 5.3 KV as Clustered State (Distributed Serving)

KV is not a cache in the Redis sense until you add a hierarchical store. On a single replica it is working memory: lose the pod, lose the prefix. LMCache makes it a tiered store: GPU working set -> pinned CPU -> local NVMe -> remote (Redis, Mooncake Store, InfiniStore, S3, NIXL). In-process connector vs MP daemon (KV survives engine crash -- no fate-sharing).

**Dynamo KVBM:** Offload to host / local / object, "petabyte" class pools.

**Mooncake Store:** Hash-indexed distributed KV, RDMA, used as SGLang hierarchical backend and vLLM connector.

**Failover implications:**
- Decode replica death: in-flight request's KV is on that GPU. Without offload, restart prefill (TTFT spike). With disagg, a new decode worker can pull KV from prefill or from Mooncake/LMCache if it was published.
- Prefill replica death mid-transfer: decode must retry prefill or read a durable KV object. NIXL transfer is not a commit.
- Router view stale: llm-d/Dynamo indexes are event-sourced. A lagging index routes to a pod that already evicted the block -> silent miss (correct, slower), or to a dead pod -> error + fallback.
- Swap (vLLM swapped queue): CPU spill under memory pressure. Latency cliff; treat as a degraded mode, not capacity.

Mooncake's prediction-based early rejection is the overload valve: under extreme load, refuse rather than accept and miss SLO.

**Dynamo SLA-based planner sizes GPU pools against latency SLOs.**

### 5.4 Replica and Pool Failover

| Failure | Detection | Action | Cache consequence |
|---------|-----------|--------|-------------------|
| Engine 5xx / OOM | Health check + `allowed_fails` | LiteLLM cooldown; k8s restart | Prefix on that GPU gone unless LMCache/Mooncake |
| 429 TPM | Header / 429 | Cooldown that deployment, try order+1 | Do not retry the same key immediately |
| P pool saturation | Queue depth, TTFT | Scale x in xPyD; Dynamo SLA planner | New prefills cold |
| D pool saturation | ITL, KV util | Scale y; admit fewer prefills | Backpressure P |
| Region outage | DNS / mesh | Geo failover | Cold cache in the new region; budget TTFT |

### 5.5 Enterprise Security & Governance

#### Zero-Trust MCP at the Inference Gateway

MCP tools/call is a privileged side channel into the same GPU that holds other tenants' KV. The gateway (not the engine) must:

1. Authenticate the MCP client (OAuth 2.1 / SPIFFE) and bind tenant_id.
2. Authorize tool name + args against policy before tokens hit the model.
3. Inject cache_salt = HMAC(server_secret, tenant_id) -- never accept a client-supplied salt.
4. Propagate W3C traceparent (MCP SEP-414 _meta) so a tool span and the LLM span share trace_id for audit.
5. Rate-limit per tenant x model, not per cluster.

#### PII in KV

KV tensors are a lossy-but-invertible-enough form of the prompt. Legal/compliance view: data in use, same category as the prompt, not "ephemeral telemetry."

- Do not put account numbers in the first n tokens of a shared prefix (timing channel infers them).
- Prompt-cache providers: Anthropic states raw text is not stored at rest; KV+hashes in memory; ZDR-eligible. OpenAI in-memory vs 24h retention is a DPA checkbox. Gemini explicit storage is a retained object with TTL -- treat like a document store.
- Offload to S3/Blob: encryption at rest, CMK, path per tenant, lifecycle = TTL.
- Logs: cached_tokens is OK; dumping KV dumps PII.

#### Audit

Minimum audit tuple per request: tenant_id, model, adapter_id, cache_salt_id (not the secret), prefix_hash, cached_tokens / cache_write_tokens, router_choice, fallback_reason, kv_pod_id, trace_id. Cache hits must be auditable or you cannot explain a data-residency miss. Semantic-cache hits need source_request_id.

Quantization and spec-decode are quality-affecting; log quant_scheme and spec_method in the model card of the serving revision so eval can be reproduced.

#### Consistency of Prefix Caches

Hash-based APC is eventually consistent across pods until a global index exists. Two requests with the same prefix on two cold GPUs both prefill. Cache-aware routing's job is to make that rare. After a rolling deploy, weights change => all KV is invalid; engines already key extra hashes (LoRA id, mm hash). Quantization or FA kernel swap that is not bit-identical must bump a cache generation or you serve garbage attention.

## 6. Code Examples

### 6.1 vLLM with Prefix Caching and Chunked Prefill

```python
from vllm import LLM, SamplingParams

# Initialize engine with prefix caching and chunked prefill
llm = LLM(
    model="meta-llama/Llama-3.1-8B-Instruct",
    enable_prefix_caching=True,
    enable_chunked_prefill=True,
    max_num_batched_tokens=8192,
    gpu_memory_utilization=0.9,
    trust_remote_code=True,
    # For multi-tenant, inject per-tenant salt via extra hashes
)

# Shared system prompt (will be cached)
system_prompt = """You are a helpful assistant with expertise in..."""

# Multiple requests sharing prefix
prompts = [
    f"{system_prompt}\n\nUser: {user_query}"
    for user_query in [
        "What is Python?",
        "Explain decorators",
        "What are generators?"
    ]
]

sampling_params = SamplingParams(
    temperature=0.7,
    top_p=0.9,
    max_tokens=512
)

# First request prefills system_prompt; subsequent reuse KV blocks
outputs = llm.generate(prompts, sampling_params)

for output in outputs:
    print(f"Prompt: {output.prompt}")
    print(f"Generated: {output.outputs[0].text}\n")
```

### 6.2 Anthropic Prompt Caching (5m TTL)

```python
import anthropic

client = anthropic.Anthropic()

# Tools and system prompt are stable prefix
tools = [
    {
        "name": "search",
        "description": "Search the knowledge base",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"]
        }
    }
]

system_blocks = [
    {
        "type": "text",
        "text": "You are a customer support agent. Always be polite.",
        "cache_control": {"type": "ephemeral", "ttl": "5m"}
    }
]

# First request: writes cache
response = client.messages.create(
    model="claude-sonnet-5",
    max_tokens=1024,
    tools=tools,
    system=system_blocks,
    messages=[{"role": "user", "content": "How do I reset my password?"}]
)

# Check cache stats
usage = response.usage
print(f"Input tokens: {usage.input_tokens}")
print(f"Cache write tokens: {usage.cache_creation_input_tokens}")
print(f"Cache read tokens: {usage.cache_read_input_tokens}")

# Subsequent request within 5m: reads cache
response2 = client.messages.create(
    model="claude-sonnet-5",
    max_tokens=1024,
    tools=tools,
    system=system_blocks,
    messages=[{"role": "user", "content": "What's my account number?"}]
)

usage2 = response2.usage
print(f"\nSecond request:")
print(f"Cache read tokens: {usage2.cache_read_input_tokens}")  # Should match system+tools
```

### 6.3 OpenAI GPT-5.6 Explicit Prompt Cache

```python
from openai import OpenAI

client = OpenAI()

# prompt_cache_key for affinity routing
cache_key = "tenant:acme:v2"

# Stable prefix >= 1024 tokens
system_content = "..." # Long system prompt

response = client.chat.completions.create(
    model="gpt-5.6-sol",
    messages=[
        {"role": "system", "content": system_content},
        {"role": "user", "content": "Analyze this log"}
    ],
    prompt_cache_options={
        "mode": "explicit",
        "key": cache_key
    }
)

# Cache stats in usage
print(f"Cached tokens: {response.usage.prompt_tokens_details.cached_tokens}")
print(f"Cache writes: {response.usage.prompt_tokens_details.cache_write_tokens}")
```

### 6.4 LiteLLM Fallback Routing with Cooldown

```python
import litellm

litellm.set_verbose = True

# Fallback chain with cooldown
response = litellm.completion(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello"}],
    fallbacks=[
        "azure/gpt-4o-deployment",
        "anthropic/claude-sonnet-4.6"
    ],
    context_window_fallbacks=[
        {"gpt-4o": ["claude-sonnet-4.6"]}
    ],
    num_retries=2,
    allowed_fails=3,  # Cooldown after 3 fails
    cooldown_time=60  # 60s cooldown
)
```

### 6.5 Semantic Cache with Redis

```python
from redis import Redis
from redis.commands.search.query import Query
import openai
import numpy as np

redis_client = Redis(host='localhost', port=6379, decode_responses=False)

def get_embedding(text: str):
    response = openai.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return np.array(response.data[0].embedding, dtype=np.float32).tobytes()

def semantic_cache_lookup(query: str, tenant: str, threshold: float = 0.90):
    """Return cached response if similarity >= threshold."""
    query_vec = get_embedding(query)
    
    # KNN search with tenant+model TAG filter
    search_query = (
        Query(f"(@tenant:{{{tenant}}} @model:{{gpt-4o}})=>[KNN 1 @embedding $vec AS score]")
        .return_fields("response", "score")
        .sort_by("score", asc=False)
        .dialect(2)
    )
    
    result = redis_client.ft("semantic_cache_idx").search(
        search_query,
        query_params={"vec": query_vec}
    )
    
    if result.docs and float(result.docs[0].score) >= threshold:
        return result.docs[0].response
    return None

def semantic_cache_write(query: str, response: str, tenant: str, ttl: int = 3600):
    """Store query embedding + response."""
    query_vec = get_embedding(query)
    key = f"cache:{tenant}:{hash(query)}"
    
    redis_client.hset(key, mapping={
        "tenant": tenant,
        "model": "gpt-4o",
        "query": query,
        "response": response,
        "embedding": query_vec
    })
    redis_client.expire(key, ttl)

# Usage
tenant_id = "acme_corp"
user_query = "How do I reset my password?"

cached = semantic_cache_lookup(user_query, tenant_id)
if cached:
    print(f"Cache hit: {cached}")
else:
    # Call LLM
    llm_response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": user_query}]
    ).choices[0].message.content
    
    # Write to cache
    semantic_cache_write(user_query, llm_response, tenant_id)
    print(f"Cache miss, wrote: {llm_response}")
```

### 6.6 LangGraph Node Caching

```python
from langgraph.graph import StateGraph, CachePolicy
from langgraph.checkpoint.memory import MemorySaver

def expensive_node(state):
    """Simulate expensive LLM call."""
    # This result will be cached for 5 minutes
    return {"result": "expensive computation"}

# Define graph with cache policy
workflow = StateGraph()
workflow.add_node(
    "expensive",
    expensive_node,
    cache_policy=CachePolicy(ttl=300)  # 5 minute TTL
)

# Checkpointer for durability
checkpointer = MemorySaver()
app = workflow.compile(checkpointer=checkpointer)

# First run: computes
result1 = app.invoke({"input": "query"}, config={"thread_id": "1"})
print(f"Cached: {result1.get('__metadata__', {}).get('cached', False)}")

# Second run within TTL: cached
result2 = app.invoke({"input": "query"}, config={"thread_id": "1"})
print(f"Cached: {result2.get('__metadata__', {}).get('cached', False)}")  # True
```

### 6.7 SGLang RadixAttention with LPM Scheduling

```bash
# Launch SGLang with longest-prefix-match scheduling
python -m sglang.launch_server \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --port 8000 \
  --schedule-policy lpm \
  --chunked-prefill-size 4096 \
  --mem-fraction-static 0.9 \
  --disable-radix-cache false
```

```python
import sglang as sgl

@sgl.function
def cached_chat(s, system_prompt, user_query):
    # System prompt shared across requests -> radix tree root
    s += sgl.system(system_prompt)
    s += sgl.user(user_query)
    s += sgl.assistant(sgl.gen("response", max_tokens=256))

# Shared system prompt
sys_prompt = "You are a helpful assistant..."

# Multiple queries share the radix tree prefix
runtime = sgl.Runtime(model_path="meta-llama/Llama-3.1-8B-Instruct")
runtime.endpoint = "http://localhost:8000"

for query in ["What is AI?", "Explain ML", "Define NLP"]:
    state = cached_chat.run(
        system_prompt=sys_prompt,
        user_query=query,
        runtime=runtime
    )
    print(state["response"])
```

## 7. Common Pitfalls & Failure Modes

### 7.1 Exact-Prefix Cache Thrash (Silent Cost Regression)

**Symptom:** Many 1.25x cache writes, few cache reads. Cost and latency regression despite "caching enabled."

**Causes:**
- Timestamp/ISO date in the system prompt.
- Tool JSON key order changes.
- Whitespace drift (ChatML vs Harmony template).
- Image bytes changing on each request.
- LoRA id or multimodal hash not in extra_hash.
- Gateway injecting request_id before the breakpoint.
- OpenAI pre-5.6: hits in 128-token steps; one-token shift = miss.
- Anthropic: 20-block lookback exceeded in long tool traces.

**Detection:** Alert on `cache_write_tokens / (cache_write_tokens + cache_read_tokens) > 0.5` for prompts above min cache tokens.

**Fix:** Stabilize prefix serialization. Move dynamic fields after the breakpoint. Use `prompt_cache_key` routing (OpenAI) or explicit `cache_control` blocks (Anthropic). Deterministic tool schema serialization.

### 7.2 Semantic Cache False Positives (Correctness Risk)

**Symptom:** "Close enough" prior answers whose hidden constraints differ from the new request. User reports wrong information served confidently.

**Cause:** Threshold too low; missing TAG filters (tenant, model version, locale); paraphrased query with different constraints.

**Example:** "Best Italian restaurant downtown" vs "Best Italian restaurant downtown under $20" -- high cosine similarity, different answers.

**Fix:**
- Set threshold per task with a false-hit eval set (commonly 0.90-0.95, not universal).
- TAG filter on `tenant`, `model`, `locale` in the same `FT.SEARCH` KNN query.
- Fail open (LLM) on embedder 5xx if availability > cost; fail closed if cost cap is the constraint.
- Human eval of hit set before production.

### 7.3 Cache Stampede (Thundering Herd)

**Symptom:** Traffic spike or deploy -> all prefixes miss -> every replica prefills the 8k system prompt -> GPU compute saturates -> TTFT p99 explodes -> retries make it worse. Hosted: many parallel 1.25x writes of the same prefix.

**Cause:** Cold start, rolling deploy, or serialization change invalidates all cached prefixes simultaneously.

**Mitigations:**
1. Single-flight / request coalescing on prefix_hash at the gateway.
2. Cache-aware routing so the one writer's pod gets the herd.
3. Pre-warm: send a canary request after deploy.
4. Jittered retries (exponential backoff + random jitter).
5. Explicit breakpoints so you do not rewrite the unstable suffix every time.
6. Anthropic: parallel requests cannot hit until the first response starts; serialize the first write.

### 7.4 OOM and KV Exhaustion

**Symptom:** Engine SIGKILL, CUDA out of memory, vLLM swap storm, Mooncake early reject.

**Causes:**
- `gpu_memory_utilization` / `mem-fraction-static` too high (default 0.9).
- `max_model_len` x concurrency x layers x KV-bytes exceeds HBM.
- Speculative decode doubling KV (draft + target).
- Chunked prefill still allocating full output horizon.
- Fragmentation without paging.
- Multimodal activations.

**Mitigations:**
- Lower max concurrent sequences.
- GQA/MLA/FP8-KV to shrink KV footprint.
- LMCache offload to CPU/NVMe/object.
- Reject at the gateway when KV util > 85%.
- Never "retry OOM" without shrinking max_tokens.
- Monitor `vllm_gpu_cache_usage_perc` metric.

### 7.5 Quality Collapse from Over-Quantization

**Patterns:**
- GPTQ overfit to calibration domain; AWQ was designed to avoid that, still eval on your long-context and tool-calling suite.
- W4A16 helps memory-bound small batches; at large continuous-batch decode, INT4 dequant can lose to FP8 Tensor Cores.
- KV INT2/INT4 without KIVI's residual window or QServe SmoothAttention: long-context perplexity and retrieval-in-context fail first.
- FP8 attention without FA3-style incoherent processing: FA3 reports 2.6x less numerical error than naive FP8.
- Mixing KV dtype across P/D disagg without converting on transfer: garbage decode.

**Governance:** A quantized checkpoint is a new model version. Gate on agent eval + safety eval, not just MMLU. Over-quant shows up as "the model got dumber" tickets.

**Fix:** Conservative quantization recipes (FP8 W+KV on Hopper+). Full eval suite before prod. Log quant_scheme in model card.

### 7.6 Router Misclassification

**RouteLLM-style under-routing (hard query -> weak model):** Silent quality drop, no 4xx. Over-routing: cost returns to always-strong.

**Cascades mis-judge (DistilBERT says "good" on a fluent wrong answer):** FrugalGPT's win is task-dependent.

**Mitigations:**
1. Shadow-route a % to strong and measure Preference Gap Ratio (PGR).
2. Escalate on self-reported uncertainty / tool-failure / policy classifiers.
3. Never cascade on the latency-critical path without a parallel-router alternative.
4. Pin model for regulated workflows -- routers are for Tier-B traffic.

### 7.7 Over-Decomposition and Fan-Out Burn

**Symptom:** Query rewrite loops, multi-subquery retrieval, and planner/executor expansion add cost and latency faster than they add quality.

**Cause:** The task was not actually decomposable; parallelism without dependency analysis; naive fan-out without bounded executors.

**Example:** LLMCompiler measures up to 3.7x lower latency and 6.7x lower cost vs ReAct when dependency-aware parallelism is possible -- but only when dependencies permit parallelism. Serial tasks pay orchestration overhead for no gain.

**Fix:** Profile agent traces. Measure whether decomposition actually improves quality on your eval set. Use LLMCompiler-style dependency analysis, not naive fan-out. Bound executor token budgets.

### 7.8 Parallelism Outrunning Durability

**Symptom:** Async durability improves throughput but creates operational pressure if persistence lags execution. LangGraph needed a fix for checkpoint-task backlog under this pattern.

**Cause:** Graph executes faster than checkpointer can flush; queue fills; memory pressure or data loss on crash.

**Fix:** Monitor checkpoint backlog depth. Apply backpressure (slow execution) or scale checkpoint store. LangGraph now documents this trade-off.

### 7.9 Context-Window Bloat Despite Optimization

**Symptom:** Optimization efforts (caching, routing) deliver weak results because too much history, tool schemas, or artifacts still get replayed every turn.

**Cause:** No compaction; artifacts embedded in messages; full transcript replayed; tool schemas duplicated.

**Fix:**
- ADK compaction: condense older history once token or turn thresholds hit.
- ADK artifacts: keep large blobs external until explicitly loaded.
- OpenAI conversation_id / previous_response_id: continuation without resend.
- LangGraph node caching: cache subgraph results, not whole conversation.
- Prune tool schemas not needed for this turn.

### 7.10 Other High-Severity Modes

| Mode | Signature | Fix |
|------|-----------|-----|
| **Prefix timing leak** | Attacker varies 1 token, measures TTFT | Tenant salt; disable APC; don't share GPUs across trust domains |
| **Spec rollback bugs** | Duplicate/missing tokens, KV desync | Disable spec; pin engine version |
| **Hash algo change** | After upgrade, 0% prefix hits | sha256_cbor for cross-version; bump generation |
| **Herding to the cache-rich pod** | One GPU 100% KV, others idle | Dynamo load term + router_temperature |
| **TTL vs streaming** | Turn 2 misses after long completion | 1h Anthropic TTL or OpenAI 30m with keep-alive |
| **LoRA mix-up** | Wrong adapter in batch | Adapter id in block extra_hash; authz at gateway |
| **Wrong-model routing** | Ambiguous specialist descriptions, fuzzy delegation | Explicit model pins in routing logic; shadow eval |
| **Route drift** | Efficient route is unauthorized | Route authorization at gateway before execution |

## 8. Interview Questions & Answers

### Q1: What is the difference between prefill and decode, and why does it matter for optimization?

**A:** Prefill processes the entire prompt in parallel -- it's compute-bound and dominates Time To First Token (TTFT). Decode generates one token at a time, reading the growing KV cache -- it's memory-bandwidth-bound and dominates Inter-Token Latency (ITL). They have opposite bottlenecks.

This matters because optimizations target different phases. Prefix caching cuts prefill compute (lower TTFT). Quantized KV or MLA cuts memory bandwidth (higher decode throughput). In a naive colocated system, a long prefill stalls all in-flight decodes -- Sarathi measured up to 28.3x higher token-by-token latency. That's why chunked prefill (cap tokens per iteration) or disaggregation (separate prefill and decode pools) exist: to prevent prefill from blocking decode.

In an interview, draw two boxes labeled P and D, and put TTFT over P, ITL over D. That shows you understand the two clocks.

### Q2: Explain prefix caching. How does it differ from semantic caching?

**A:** Prefix caching (also called automatic prefix caching or prompt caching on hosted APIs) is exact-match reuse of the KV cache for a shared token sequence. If two requests have the same first N tokens, the second request skips prefilling those N tokens. It's bit-identical: same tokens -> same KV -> same output.

Semantic caching embeds the query, does kNN, and if similarity is above a threshold, returns a previous response without calling the LLM at all. It's approximate: "close enough" based on embedding distance.

Key differences:
- Prefix: exact match, operates on KV blocks, reduces prefill time but still runs the model. Semantic: approximate match, operates on embeddings, skips the model entirely.
- Prefix: no quality risk (bit-identical). Semantic: false-positive risk (wrong answer for a similar question).
- Prefix: requires stable token serialization. Semantic: requires TAG filters (tenant, model, locale) or you cross-talk.

Prefix is an infrastructure optimization; semantic is a product decision with correctness trade-offs.

### Q3: You have 1,000 requests/min, each with an 8,000-token system prompt and 500-token user query. How much does Anthropic prompt caching (5m TTL) save vs no cache?

**A:** Assumptions: Sonnet 4.6 pricing. 400 output tokens. Warm cache (all but first request hit).

No cache: 1,000 x (8,500 input x $3/1M + 400 output x $15/1M) = $31.50 / 1k executions.

With 5m cache: First request writes the 8,000-token prefix at 1.25x = $3.75/1M; 500 variable at $3/1M; 400 output $15/1M. Next 999 requests read 8,000 at 0.1x = $0.30/1M; 500 variable at $3/1M; 400 output $15/1M.

Total = (8,000 x 3.75 + 500 x 3 + 400 x 15)/1M + 999 x (8,000 x 0.30 + 500 x 3 + 400 x 15)/1M = $0.0459 + $9.89 = $9.93 / 1k.

Savings: ($31.50 - $9.93) / $31.50 = 68%. Break-even after first hit because 1.25 write + 0.1 read < 1.0 + 1.0.

Key insight: output still dominates long generations. Cache is an input lever; routing/quantization/spec decode are output-time levers.

### Q4: What is PagedAttention and what problem does it solve?

**A:** PagedAttention (Kwon et al., SOSP 2023) treats the KV cache like virtual memory. Instead of allocating one contiguous tensor per sequence, it divides KV into fixed-size blocks and maintains a block table per sequence. Think of it as paging for GPU memory.

Problems it solves:
1. **Fragmentation:** Contiguous allocation wastes memory when sequence lengths vary. Paged blocks have near-zero internal fragmentation.
2. **Sharing:** Beam search or parallel sampling can copy-on-write share blocks. Prefix caching shares blocks across requests.
3. **Memory efficiency:** vLLM measured 2-4x throughput vs FasterTransformer and Orca at comparable latency because more sequences fit in memory.

Block hashing enables automatic prefix caching: each block is hashed by (parent_hash, block_tokens, extra_hashes), so a shared system prompt is one set of physical blocks.

vLLM is the reference implementation. SGLang RadixAttention extends this with a radix tree for longest-prefix-match retention across request lifecycles.

### Q5: When should you disaggregate prefill and decode, and when should you keep them colocated?

**A:** Disaggregate when both TTFT and ITL SLOs bind, and you can't meet them with chunked prefill alone. DistServe measured 7.4x more requests or 12.6x tighter SLO. Mooncake showed +115% to +525% throughput in production clusters.

Colocate when:
- Short prompts + high QPS: KV transfer overhead dominates.
- Single-node <8 GPUs: interconnect can't hide transfer.
- Cost-sensitive and can tolerate slight ITL variance: chunked prefill is the simpler default.

DistServe's placement rule: put P and D on the same node when interconnect cannot hide transfer.

In practice, start colocated with chunked prefill. Profile TTFT p99 and ITL p99. If prefill arrivals still cause ITL spikes despite chunking, or if TTFT is the bottleneck and you need cache-affinity routing, disagg is the next step.

Disagg also enables heterogeneous pools (H100 prefill / A100 decode) for cost optimization.

### Q6: What is the timing side-channel in prefix caching, and how do you mitigate it?

**A:** KVGov (2026) showed that an attacker can infer whether their prompt shares a prefix with another tenant by measuring TTFT. Cache hit = fast (TTFT ratio ~0.22 on their benchmark). Cache miss = slow. By varying one token and timing, the attacker can extract information about other tenants' prompts.

This is exploitable at production scale if prefix caching is global (no tenant isolation).

Mitigations:
1. **Tenant salt:** vLLM `cache_salt` = HMAC(server_secret, tenant_id). Mixed into the first block hash. Only same-tenant requests share blocks. Patched in vLLM >=0.9.0.
2. **Disable cross-tenant APC:** Separate clusters or disable prefix caching in multi-tenant hostile environments.
3. **Boundary salt (research):** KVGov's boundary salt estimated 93% of prefix benefit retained while isolating tenants. Not a vLLM flag yet.

For hosted APIs: OpenAI org-scoped, Anthropic org/workspace-scoped, Gemini IAM on CachedContent.

Never hash the raw prompt in the client and route based on that hash. The gateway injects the salt.

### Q7: Walk me through chunked prefill. What problem does it solve, and what knobs do you tune?

**A:** Chunked prefill (Sarathi-Serve) caps the token budget per iteration. Instead of prefilling a 10k-token prompt in one step (which blocks all decodes), you split it into chunks (e.g., 4k tokens/iter).

Each iteration:
1. Run all in-flight decodes (they get tokens/iter == their count).
2. Use leftover token budget for prefill chunks.

This prevents "generation stall." Sarathi measured up to 28.3x TBT improvement vs naive hybrid batching.

Knobs (vLLM):
- `max_num_batched_tokens`: Total token budget per iteration (e.g., 8,192-16,384).
- `enable_chunked_prefill`: true.
- `max_num_seqs`: Max sequences in a batch (bounds scheduler complexity).
- `free_gpu_memory_fraction`: 0.9 default; back off to 0.7-0.8 on OOM.

SGLang equivalent: `--chunked-prefill-size` (tune down to 4,096 or 2,048 on OOM).

Trade-off: TTFT slightly higher (prefill is chunked), but ITL much more stable. Disaggregation is the next step if you need both tight TTFT and tight ITL.

### Q8: Explain the economics of planner/executor routing. When does it win?

**A:** Strong planner, cheaper bounded executors. LLMCompiler benchmark: up to 3.7x lower latency and 6.7x lower cost vs ReAct when dependency-aware parallelism is possible.

Why it wins:
- Planner (strong model) does the hard reasoning once: decompose task, identify dependencies.
- Executors (cheaper models) run bounded subtasks in parallel: tool calls, simple QA, retrieval.
- Amortize the planner cost over many cheap executor calls.

When it wins:
- Task is actually decomposable (parallel subtasks, not serial chain).
- Executors can be bounded (fixed schema, small context).
- Quality holds with cheap executors (they're not doing open-ended reasoning).

When it loses:
- Serial tasks: orchestration overhead for no gain.
- Over-decomposition: fan-out burns more tokens than it saves.
- Weak executors produce wrong results: planner has to retry or self-correct.

Formula from GPT 5.4:
```
effective_total_tokens_per_run
  = planner_tokens
  + executor_tokens
  + verifier_tokens
  + replayed_history
  + tool_outputs
  - cached_or_compacted_prefix
```

Optimization: cache planner's stable prefix, bound executor max_tokens, compact history.

### Q9: You're serving a multi-tenant SaaS chatbot. Every tenant shares the same 5,000-token system prompt. How do you optimize without leaking prompts across tenants?

**A:** Use tenant-isolated prefix caching.

Architecture:
1. **Gateway:** Inject `cache_salt = HMAC(server_secret, tenant_id)` for every request. Never accept client-supplied salt.
2. **vLLM/SGLang:** Enable prefix caching with `cache_salt` in extra_hash. Same tenant -> same salt -> shared blocks. Different tenant -> different salt -> isolated blocks.
3. **Hosted API (Anthropic/OpenAI):** Use per-tenant `prompt_cache_key` routing. OpenAI: `prompt_cache_key = f"tenant:{tenant_id}:v1"`. Anthropic: org/workspace isolation built-in.

Economics: 5,000-token system prompt cached within each tenant. First request writes at 1.25x; subsequent reads at 0.1x. Break-even after first hit. Tenant A's cache doesn't help tenant B (isolation), but within tenant A you get full reuse.

If you share globally without salt, timing side-channel leaks prompts (KVGov 2026). If you don't cache at all, you pay full prefill every time.

Alternative: Disable APC entirely and rely on hosted prompt cache with org-scoped isolation. Trade-off: simpler (no salt management), but hosted pricing and TTL constraints.

### Q10: What is speculative decoding, and when should you enable it?

**A:** Speculative decoding (Leviathan et al., ICML 2023): a small draft model proposes gamma tokens; the target model verifies all of them in one forward pass; rejection sampling keeps the distribution exact. Net result: more tokens per target forward, lower ITL if the acceptance rate alpha is high.

vLLM blog: up to 2.8x speedup. DeepSeek-V3 uses MTP (multi-token prediction) as a trained speculative head.

When to enable:
- Alpha >= 1.5-2.0 on your workload (measure on a sample).
- Memory budget allows draft + target KV (roughly 2x KV footprint, or more for tree attention).
- Latency (ITL) is the bottleneck, not throughput at high batch.

When not to enable:
- Low alpha: you burn extra FLOPs and pollute the batch token budget with rejected drafts.
- Memory-constrained: draft KV + target KV can OOM.
- High batch continuous decode: the batch is already saturating memory bandwidth; speculative overhead may not pay.

Implementation: draft can be a separate small model (Medusa, EAGLE) or extra heads on the target (Medusa-style, MTP). On reject, truncate KV back to the last accepted token.

Circuit breaker: if moving average alpha < threshold, disable spec for that model.

### Q11: How does quantization affect serving economics? Compare FP8, INT4 AWQ, and FP8 KV.

**A:** Quantization trades memory/compute density for potential quality loss.

**FP8 (W+A, Hopper+):**
- 2x density vs BF16. Tensor Core support. Minimal quality loss (GSM8K/MMLU typically unchanged).
- Role: Default production quant on H100/B200. Transformer Engine auto-scales.
- Trade-off: Requires Hopper+. Need FA3 for low numerical error in attention.

**INT4 AWQ (weights only):**
- 4x weight density vs BF16. Protects salient channels using activation stats. Better generalization than GPTQ.
- Role: Memory-constrained serving, small batch (memory-bound). TinyChat 3.2-3.3x vs HF FP16.
- Trade-off: Dequant overhead at large batch can lose to FP8 Tensor Cores. Eval on your long-context and tool-calling suite; calibration-set sensitive.

**FP8 KV:**
- 2x KV density. Doubles max concurrency or context length.
- TRT-LLM: +6% E2E throughput at same concurrency, "no meaningful drop" on GSM8K.
- Role: Long-context, high concurrency.
- Trade-off: Need FA3 or careful scaling. Mix with MLA for extreme KV shrink (DeepSeek-V3 ~70 KB/tok).

**Economics (self-hosted):** FP8 is the 2026 default on modern NVIDIA. INT4 for edge/cost-sensitive with full eval. FP8 KV stacks with weight quant (QServe W4A8KV4). Measure tok/s, multiply by your GPU SKU cost.

Governance: quantized checkpoint = new model version. Gate on agent eval, not just perplexity.

### Q12: What is the difference between RouteLLM and FrugalGPT cascade?

**A:** Both reduce cost by mixing cheap and expensive models, but they decide at different times.

**RouteLLM (complexity/preference router):**
- Decide before calling any LLM. Tiny classifier (or LLM-as-judge on a sample) predicts whether the query is hard or easy.
- Hard -> strong model. Easy -> weak model.
- Zero extra LLM calls (classifier is not a full LLM).
- Typical win: >2x cost cut vs always-strong. MT Bench CPT(50%): 37% GPT-4 calls, score 8.8 vs GPT-4 9.3.
- Trade-off: Under-routing (hard query -> weak model) = silent quality drop. Requires shadow eval.

**FrugalGPT (cascade):**
- Call weak model first. Judge the answer (another LLM or heuristic). If bad, escalate to stronger model. Repeat up to k tiers.
- Serial latency: 1..k LLM calls.
- Typical win: Match GPT-4 with up to 98% cost cut, or +4% accuracy at same cost (task-dependent).
- Trade-off: p95 latency up. Judger quality is the bottleneck (fluent wrong answer can look good).

**When to use which:**
- Latency-critical interactive: RouteLLM (no serial penalty).
- Cost-sensitive batch: FrugalGPT (serial latency OK, quality matters).
- Regulated workflows: Neither; pin model for auditability.

Hybrid (De Koninck et al., ICML 2025): cascade routing with a quality estimator; proves when to combine.

### Q13: Your TTFT p99 is 8 seconds. Walk me through your optimization plan.

**A:** TTFT is prefill-bound. Diagnose, then optimize.

**Diagnose:**
1. Is this a cache miss or a queue? Check `cached_tokens` in usage. If zero on prompts >1k, it's a cache issue. If queue depth is high, it's admission/scheduling.
2. Profile one request: TTFT = queue_wait + prefill_compute. Instrument both.

**Optimize (in order of impact):**

**If cache miss:**
- Stabilize prefix serialization (timestamps, tool order, whitespace).
- Hosted API: use `prompt_cache_key` (OpenAI) or `cache_control` (Anthropic). Ensure min tokens met.
- Self-hosted: verify prefix caching enabled, check block reuse in logs. Add cache-aware routing (llm-d / Dynamo).

**If queue wait:**
- Scale prefill pool (xPyD: increase x).
- Dynamo SLA planner: set TTFT SLO, let it size the pool.
- Admission control: reject when queue depth > threshold (429 with Retry-After).

**If prefill compute:**
- Chunked prefill if not enabled (won't help p99, but stabilizes ITL).
- Disagg P/D if TTFT and ITL both bind.
- FP8 if on Hopper+ (2x density, more batch).
- Larger prefill GPUs (H100 > A100).

**After each change:** Measure p50/p95/p99 TTFT and cache hit rate. TTFT p99 is often a miss + queue; cache hits move p50 more than p99.

### Q14: Explain the failure mode: "context-window bloat despite optimization."

**A:** You've enabled caching and routing, but cost and latency are still high because too much is replayed every turn.

**Causes:**
- Full transcript replayed (no continuation API).
- Tool schemas duplicated in every message.
- Artifacts (code blocks, large docs) embedded in messages instead of externalized.
- No compaction: 40-turn conversation, all 40 in context.

**Fix:**
- **OpenAI Agents SDK:** `conversation_id` or `previous_response_id` continuation. Don't resend the transcript.
- **ADK compaction:** Condense older history once token or turn thresholds hit. Artifacts stay external until loaded.
- **LangGraph node caching:** Cache subgraph results, not whole conversation. Checkpoints at super-step boundaries.
- **Prune tool schemas:** Only include tools relevant to this turn.
- **Externalize artifacts:** Store large blobs (code, docs) with a reference; fetch only when needed.

Quote from GPT 5.4: "Optimization can fail because too much history, tool schemas, or artifacts still get replayed. ADK compaction, artifact isolation, OpenAI session shaping, and LangGraph caching exist precisely because raw transcript growth can erase other inference optimizations."

### Q15: Design a zero-trust inference gateway for a multi-tenant agent platform with MCP tools.

**A:** MCP tools are a privileged side channel into the same GPU that holds other tenants' KV. Gateway must enforce isolation and auditability.

**Architecture:**

```
Client -> mTLS/OAuth gateway
       -> (1) Authenticate client, bind tenant_id
       -> (2) Authorize tool name + args vs policy
       -> (3) Inject cache_salt = HMAC(server_secret, tenant_id)
       -> (4) Propagate W3C traceparent (trace_id for audit)
       -> (5) Rate-limit per tenant x model
       -> Cache-affinity router (tenant-scoped KV index)
       -> Prefill pool -> NIXL -> Decode pool
       -> LMCache remote (tenant key prefix)
Audit  -> SIEM (tenant_id, model, adapter_id, cache_salt_id, prefix_hash, router_choice, kv_pod_id, trace_id)
```

**Key controls:**
1. **AuthN/AuthZ before execution:** OAuth 2.1 / SPIFFE for client. Policy check on MCP tool + args before tokens hit the model.
2. **Tenant isolation:** `cache_salt` prevents cross-tenant KV sharing. Semantic cache TAG filter on tenant. LMCache key prefix per tenant.
3. **Audit:** MCP SEP-414 _meta propagates traceparent so tool span and LLM span share trace_id. Log minimum tuple (S 4.4): tenant_id, model, adapter_id, cache_salt_id, prefix_hash, cached_tokens, router_choice, kv_pod_id, trace_id.
4. **Rate limits:** Per tenant x model RPM/TPM at gateway, not per cluster. Circuit breakers on KV util, cooldown on 429.
5. **PII in KV:** KV tensors are data-in-use. Offload to S3 with CMK, path per tenant, lifecycle = TTL. Don't dump KV in logs.

**Semantic cache circuit:** If embedder 5xx, fail open (LLM) for availability or fail closed (cost cap) -- design decision.

**Never:** Accept client-supplied cache_salt. Route on raw prompt hash. Share adapters across tenants without isolation story.

## 9. Key Numbers to Memorize

### Pricing (per million tokens, as of 2026-08)

**Anthropic Sonnet 4.6:**
- Base input: $3
- 5m cache write: $3.75 (1.25x)
- 5m cache read: $0.30 (0.1x)
- Output: $15

**OpenAI GPT-5.6-terra (short-context):**
- Input: $2
- Cached input: $0.20 (0.1x)
- Cache writes: $2.50 (1.25x)
- Output: $12

**Break-even:** 1.25 write + 0.1 read < 1.0 + 1.0 after first hit. Second read is net-cheaper.

### KV Cache Math

**Per token, per layer, BF16:** `2 x n_kv_heads x d_head x 2 bytes`

**Example (Llama-3.1-8B, GQA 64Q/8KV, d_head=128, 32 layers):**
- Per layer per token: 2 x 8 x 128 x 2 = 4,096 bytes = 4 KB
- 32 layers: 128 KB/token
- 32k context: 4 GB KV
- FP8 KV: 2 GB (2x shrink)

**DeepSeek-V3 MLA:** ~70 KB/token vs Llama-3.1-405B 516 KB/token (93.3% reduction vs hypothetical MHA).

### Throughput Multipliers (from papers, not guarantees)

- PagedAttention (vLLM): 2-4x vs FasterTransformer/Orca
- Orca continuous batching: 36.9x vs naive batching (GPT-3 175B)
- DistServe disagg: 7.4x requests or 12.6x tighter SLO at 90% goodput
- Mooncake production: +115% / +107% on A800/H800
- Sarathi chunked prefill: up to 28.3x TBT improvement vs naive hybrid
- Speculative decoding: up to 2.8x (vLLM blog, acceptance-rate-dependent)
- FrugalGPT cascade: up to 98% cost cut or +4% accuracy (task-dependent)
- RouteLLM: >2x cost cut vs always-strong at CPT(50%)
- LLMCompiler: up to 3.7x lower latency, 6.7x lower cost vs ReAct
- FlashAttention 3 FP8: 1.5-2.0x vs FA2 FP16; up to 1.2 PFLOP/s
- Punica LoRA multiplex: 12x throughput vs SOTA, ~2 ms/tok overhead
- QServe W4A8KV4: 1.2-3.5x tok/s vs TRT-LLM on Llama/Qwen
- AWQ TinyChat: 3.2-3.3x vs HuggingFace FP16

### Min Cache Tokens (hosted APIs)

- Anthropic: 512 (Opus 5 / Fable 5 / Mythos 5) to 4,096 (Haiku 4.5)
- OpenAI: 1,024 (GPT-5.6 family)
- Gemini: 2,048 (Gemini 2), 4,096 (Gemini 3)

### Cache TTL Defaults

- Anthropic 5m: 5 minutes, refreshes on hit
- Anthropic 1h: 1 hour, refreshes on hit
- OpenAI GPT-5.6: 30 minutes, refreshes on reuse; keep ~15 RPM for good hit rate
- OpenAI pre-5.6: 5-10 min idle, max ~1 h, extended up to 24 h
- Gemini explicit: min 1 minute, no documented max

### Scheduler Defaults (starting points)

- `max_num_batched_tokens`: 8,192-16,384
- `free_gpu_memory_fraction` / `mem-fraction-static`: 0.9 (back off to 0.7-0.8 on OOM)
- `chunked-prefill-size`: 4,096-8,192 (tune down to 2,048 on OOM)
- Reject threshold: KV util > 85%

### Quality Thresholds

- Semantic cache cosine similarity: 0.85-0.95 (task-dependent, not universal)
- Speculative decode acceptance rate alpha: >= 1.5-2.0 for net win
- Over-quantization signal: eval drop on GSM8K, MMLU, long-context retrieval before chat evals

### Rate Limits (OpenAI, tier-dependent)

- Headers: `x-ratelimit-limit-requests`, `x-ratelimit-limit-tokens`, `x-ratelimit-remaining-*`, `x-ratelimit-reset-*`
- 429 -> honor `Retry-After`
- Anthropic: `cache_read_input_tokens` often excluded from ITPM; one example: 10M effective ITPM from 2M ITPM ceiling at 80% hit rate

### Capacity Formula

```
max_runs_per_minute = min(
  provider_rpm / avg_turns_per_run,
  provider_tpm / avg_total_tokens_per_run
)
```

Optimization moves both denominators down.

## 10. Quick Reference

### Optimization Decision Tree

```
START: What is the bottleneck?

TTFT high?
  -> Cache miss?
    -> YES: Stabilize prefix, enable prefix/prompt cache, cache-aware routing
    -> NO: Queue wait?
      -> YES: Scale prefill pool, admission control, SLA planner
      -> NO: Prefill compute -> FP8, disagg, larger GPUs

ITL high?
  -> Prefill stalling decode?
    -> YES: Chunked prefill or disagg P/D
    -> NO: Memory bandwidth?
      -> YES: FP8 KV, MLA, GQA, KIVI, spec decode (if alpha high)
      -> NO: Batching not saturating -> tune max_num_batched_tokens up

Cost high?
  -> Input cost?
    -> YES: Prefix/prompt cache, compaction, artifacts, continuation APIs
    -> NO: Output cost?
      -> YES: Routing (RouteLLM, cascade), spec decode, cheaper model tiers
      -> NO: Infrastructure -> self-host, quantization

Quality drop?
  -> After quant? -> Eval suite, back off to FP8 or BF16
  -> After routing? -> Shadow eval, escalate on uncertainty, pin model
  -> Semantic cache false hit? -> Raise threshold, TAG filters, human eval
```

### Cache Layer Cheat Sheet

| Layer | Match | Stored | Savings | Risk |
|-------|-------|--------|---------|------|
| KV/Paged | Own past tokens | K,V per layer | Decode doesn't recompute prefill | Confuse with cross-request reuse |
| Prefix/APC | Token block hash + salt | KV blocks shared across requests | Skip prefill of shared prefix | Serialization drift, no salt |
| Prompt (hosted) | Exact prefix at breakpoint | Provider-side KV | Input 0.1x, TTFT drop | Timestamp before breakpoint |
| Semantic | Embedding kNN | Previous response text | Skip LLM entirely | Threshold too low = wrong answer |
| Spec | Draft KV + target KV | Two KV pools | Extra decode tokens per forward | Low alpha = FLOPs burn |

### Routing Strategy Matrix

| Pattern | When | Latency | Cost | Quality |
|---------|------|---------|------|---------|
| RouteLLM | High-volume, mixed complexity | ~Unchanged | >2x cut | Bounded by CPT |
| Cascade | Cost-sensitive batch | p95 up (serial) | Up to 98% cut | Can beat expensive model |
| Planner/executor | Multi-step, parallelizable | Up to 3.7x down | Up to 6.7x down | Bounded executor quality |
| Fallback/HA | Reliability | ~Unchanged | Retry overhead | Same |
| LoRA multiplex | Multi-tenant, shared base | +~2 ms/tok | 12x throughput | Adapter-dependent |

### Quantization Quick Guide

| Recipe | Density | Hardware | Quality | Use case |
|--------|---------|----------|---------|----------|
| FP8 W+A+KV | 2x | Hopper+ | Minimal | Default production (H100/B200) |
| INT8 SmoothQuant | W8A8 | Ampere+ | Small drop | Ampere/Ada when no FP8 |
| AWQ W4A16 | 4x weights | Any | Good at 4-bit | Memory-constrained, small batch |
| FP8 KV | 2x KV | Hopper+ | Minimal | Long-context, high concurrency |
| KIVI 2-bit KV | 4x KV | CUDA | Good with residual | Extreme KV pressure |
| QServe W4A8KV4 | All three | A100/L40S | Needs eval | Cost-optimized serving |

### Hosted Cache Pricing Summary

**Write multipliers:** 1.25x (OpenAI GPT-5.6, Anthropic 5m), 2x (Anthropic 1h)
**Read multipliers:** 0.1x (all)
**Break-even:** First hit (1.25 write + 0.1 read < 2.0 uncached)

### NFR SLO Template

```yaml
slo:
  ttft_p99_ms: 2000
  itl_p95_ms: 50
  e2e_p99_ms: 30000
  cache_hit_rate_target: 0.70
  kv_utilization_max: 0.85
  speculative_alpha_min: 1.5
  goodput_target_qps: 100  # With both TTFT and ITL SLOs met
```

### Circuit Breaker Checklist

- [ ] Per-deployment cooldown (LiteLLM) on 429 and 5xx
- [ ] Per-tenant RPM/TPM at gateway
- [ ] KV util > 85% -> reject with 429 + Retry-After
- [ ] Semantic cache embedder 5xx -> fail open or closed (design decision)
- [ ] Spec decode moving avg alpha < threshold -> disable
- [ ] Exponential backoff + jitter on retries
- [ ] Cap max_fallbacks to prevent infinite cascade

### Isolation Checklist (Multi-Tenant)

- [ ] Gateway injects `cache_salt = HMAC(secret, tenant_id)`
- [ ] Never accept client-supplied salt
- [ ] Semantic cache TAG filter on tenant+model+locale in same query
- [ ] Hosted API: per-tenant `prompt_cache_key` (OpenAI) or workspace (Anthropic)
- [ ] LMCache remote: key prefix per tenant
- [ ] LoRA adapters: authz at gateway, adapter_id in block extra_hash
- [ ] xxhash off for multi-tenant; prefer sha256_cbor
- [ ] Audit log: tenant_id, cache_salt_id, trace_id

### Failure Mode Quick Diag

| Symptom | Likely cause | First check |
|---------|--------------|-------------|
| High write/read ratio | Serialization drift | Timestamp in prefix? Tool order? |
| TTFT p99 spike after deploy | Cache stampede | Pre-warm? Jittered retries? |
| OOM / SIGKILL | KV exhaustion | `free_gpu_memory_fraction` too high? Spec decode on? |
| "Model got dumber" | Over-quantization | MMLU/GSM8K on this checkpoint? |
| Random quality variance | Router misclassification | Shadow eval? PGR measurement? |
| Wrong answer served confidently | Semantic cache false hit | Threshold? TAG filters? |
| Duplicate/missing tokens | Spec rollback | Disable spec, pin engine version |
| ITL cliff at certain times | Prefill stall | Chunked prefill enabled? Disagg? |

### Trade-Off Cheat Sheet (Interview Close)

1. **Exact cache is free lunch on quality; semantic cache is a product decision.**
2. **Router-before (RouteLLM) protects latency; cascade-after (FrugalGPT) spends latency to buy cost and sometimes quality.**
3. **Chunking is the poor man's disagg; disagg is for when TTFT and ITL SLOs both bind.**
4. **FP8 is the 2026 default on Hopper/Blackwell; INT4 is for memory walls and small batches.**
5. **KV isolation is a security control with a throughput cost; salts are cheaper than separate clusters; separate clusters are what you do for hostile multi-tenant.**
6. **Never quote a paper's 7.4x / 12x / 98% as your SLO. Quote it as the shape of the curve you will measure.**
7. **Send less first: continuation APIs, compaction, artifacts. Only then optimize kernel execution.**
8. **Caching is also a throughput lever (rate limits), not just a cost lever.**
9. **Parallelism is worthwhile when dependencies permit it; over-decomposition burns tokens faster than it saves them.**
10. **Every quantized checkpoint is a new model version. Gate on agent eval, not just perplexity.**

---

**End of Topic 15: Inference Optimization**
