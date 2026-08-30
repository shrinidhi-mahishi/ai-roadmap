# Research: Inference & Optimization
**Date researched**: 2026-08-21
**Sources consulted**: 76

Scope: **caching** (KV cache, prefix/prompt cache, semantic cache, speculative-decoding cache, cache-aware routing), **routing** (model cascade, complexity routers, fallback chains, geo routing, LoRA multiplex), **batching** (continuous / in-flight batching, chunked prefill, vLLM / SGLang / TensorRT-LLM schedulers), **quantization** (FP8, INT8, INT4, AWQ/GPTQ, KV-cache quantization, quality vs latency). Prices are **vendor-published token SKUs** as of 2026-08-21. ⚠️ No unpublished production p50/p95/p99 TTFT/ITL SLOs are invented. `$ per 1k executions` figures are **[inferred]** from a named token mix × official $/MTok — not a universal GPU-hour rate. Paper speedups are the authors’ published numbers on *their* hardware/workload, not portable SLAs.

Invariant: **inference has two clocks and two planes.** Prefill is compute-bound (TTFT); decode is memory-bandwidth-bound (ITL / TPOT). The **control plane** (routers, cache indexes, SLA planners, circuit breakers, tenant salts) is not the **data plane** (KV tensors, weights, activations). Collapsing those planes — putting PII in a globally hashed prefix cache, then routing on cache affinity without isolation — is how teams simultaneously leak prompts via timing side-channels, OOM the GPU, and bill 1.25× cache writes forever.

---

## 1. System Topology & Mechanics

### 1.1 Control plane vs data plane

| Plane | What it is | Clock | Typical store | Failure if mixed |
| --- | --- | --- | --- | --- |
| **Control** | Request admission, cache-affinity scoring, xPyD pool sizing, fallbacks, rate-limit budgets, tenant `cache_salt` | Queue + 429 windows | Gateway (LiteLLM / Envoy / llm-d / Dynamo frontend), IdP, Redis cooldown keys | App code that “picks the GPU with the cache hit” by hashing the raw prompt in the client |
| **Data (compute)** | Prefill GEMM, decode attention, speculative verify, LoRA SGMV | TTFT / ITL | GPU HBM + Tensor Cores | Prefill and decode sharing one iteration budget without a token cap |
| **Data (state)** | KV blocks, radix/prefix trees, offloaded KV (CPU/SSD/object), draft-model KV | Block TTL / eviction | vLLM block pool, SGLang RadixCache, LMCache / Mooncake / Dynamo KVBM | Treating KV as “ephemeral scratch” while it is a **materialization of the prompt** |

NVIDIA Dynamo is the orchestration layer *above* engines: it does not replace vLLM, SGLang, or TensorRT-LLM; it coordinates them as a multi-node system ([Dynamo GitHub](https://github.com/ai-dynamo/dynamo)). llm-d is the Kubernetes-native twin: Gateway API Inference Extension + a Precise Prefix-Cache Scorer over a global KV-block index built from vLLM `KVEvents` ([llm-d KV-cache blog](https://llm-d.ai/blog/kvcache-wins-you-can-see)). LiteLLM is the *API* control plane: cooldowns, `order`-based failover, RPM/TPM-aware shuffle ([LiteLLM routing](https://docs.litellm.ai/docs/routing); [load balancing](https://docs.litellm.ai/docs/proxy/load_balancing)).

**Interview move:** draw three boxes — **gateway**, **prefill pool**, **decode pool** — and put the KV transfer (NIXL / Mooncake Transfer Engine / LMCache connector) on the edge between prefill and decode. Anything that scores cache overlap belongs on the gateway. Anything that writes KV tensors belongs on the engine.

### 1.2 Prefill / decode: colocated vs disaggregated

Autoregressive serving is two phases with opposite hardware profiles ([DistServe, OSDI 2024](https://arxiv.org/abs/2401.09670); [Splitwise, ISCA 2024](https://arxiv.org/abs/2311.18677)):

- **Prefill:** all prompt tokens in parallel. Compute-bound. Dominates **TTFT**.
- **Decode:** one (or a speculative tree of) token(s) per step, reading the growing KV cache. Memory-bandwidth-bound. Dominates **ITL / TPOT**.

Colocation (Orca/vLLM default) batches both into the same forward pass. A long prefill **stalls** every in-flight decode: Sarathi-Serve measured up to **28.3×** higher token-by-token (TBT) latency for a naive hybrid batch vs decode-only ([Sarathi-Serve, OSDI 2024](https://arxiv.org/abs/2403.02310)). DistServe’s published result on colocated SOTA: **7.4×** more requests *or* **12.6×** tighter SLO while staying inside TTFT+TPOT for >90% of requests. Splitwise independently argued for **heterogeneous** pools (e.g. H100 prefill / A100 decode) and measured **1.4×** throughput and **~20%** lower cost in their setting. ⚠️ Those two papers’ multipliers are not interchangeable; DistServe optimizes goodput under dual SLOs, Splitwise optimizes hardware efficiency.

**Disagg data path (Dynamo):** (1) PrefillRouter picks a prefill worker by KV overlap + load; (2) prefill writes KV and returns `disaggregated_params`; (3) decode worker pulls KV via NIXL (NVLink / IB / UCX), non-blocking so other requests keep running; (4) xPyD (x prefill, y decode) is runtime-reconfigurable ([Dynamo disaggregated serving](https://docs.nvidia.com/dynamo/dev/design-docs/disaggregated-serving)). Dynamo’s marketing claim for KV-aware routing: **~2× faster TTFT** by skipping redundant prefill ([Dynamo README](https://github.com/ai-dynamo/dynamo)). Mooncake (Kimi / Moonshot, FAST 2025 Best Paper) runs this at cluster scale: thousands of nodes, **>100B tokens/day**; production A800/H800 lifts of **+115% / +107%** requests vs prior system; simulated long-context throughput up to **+525%** under SLO ([Mooncake arXiv](https://arxiv.org/abs/2407.00079); [FAST PDF](https://www.usenix.org/system/files/fast25-qin.pdf)). vLLM+Mooncake 1P1D microbench (Qwen3-8B, 8× CX7 RoCE): **142.25 GB/s** KV transfer (71.1% of ~200 GB/s theoretical); 32,768-token prompt, 4.50 GB KV, **31.65 ms** transfer = **4.2% of TTFT** ([Mooncake vLLM benchmark](https://kvcache-ai.github.io/Mooncake/performance/vllm-v1-support-benchmark.html)).

**When not to disaggregate.** Short prompts + high QPS: KV transfer and extra hop dominate. Single-node <8 GPUs: colocation + chunked prefill is the default. DistServe’s own placement rule: put P and D on the same node when interconnect cannot hide transfer.

### 1.3 Caching: five layers that are not the same cache

| Layer | Match key | What is stored | Hit savings | Wrong if… |
| --- | --- | --- | --- | --- |
| **KV / PagedAttention** | Sequence’s own past tokens | K,V per layer (or MLA latent) | Decode does not recompute prefill | You confuse this with *cross-request* reuse |
| **Prefix / APC** | Hash of token blocks + parent hash (+ LoRA id, mm hash, salt) | KV *blocks* shared across requests | Skip prefill of the shared prefix | One-token prefix mutation; xxhash collision; no tenant salt |
| **Prompt cache (hosted APIs)** | Exact rendered prefix at a breakpoint + `prompt_cache_key` | Provider-side KV (you never see it) | Input billed at 0.1×; TTFT drop | Timestamp/tools before the breakpoint |
| **Semantic cache** | Embedding kNN above a threshold | *Text* of a previous **response** | Skip the LLM entirely | Threshold too low → wrong answer served as truth |
| **Speculative cache** | Draft KV + target KV; tree attention state | Two (or more) KV pools + verify buffer | Extra decode tokens per target forward | Draft mismatch → rollback; VRAM × (1+draft) |

**KV cache math (must state in an interview).** Per token, per layer, BF16: `2 × n_kv_heads × d_head × 2 bytes`. MHA: `n_kv_heads = n_q`. GQA (Ainslie et al., EMNLP 2023): groups of query heads share KV — Llama-class 64 Q / 8 KV is an **8×** KV cut vs MHA ([GQA](https://arxiv.org/abs/2305.13245)). MQA (Shazeer 2019): one KV head for all Q. MLA (DeepSeek-V2): cache only latent `c_KV` plus a short RoPE key `k^R`. DeepSeek-V2: **93.3%** KV reduction vs DeepSeek 67B MHA, **5.76×** max generation throughput ([DeepSeek-V2](https://arxiv.org/abs/2405.04434)). DeepSeek-V3 (671B total / 37B active): **~70 KB/token** KV vs Llama-3.1-405B **516 KB/token** and Qwen-2.5-72B **327 KB/token** ([DeepSeek-V3 report](https://arxiv.org/abs/2412.19437); [hardware reflection paper](https://doi.org/10.1145/3695053.3731412)).

**PagedAttention (Kwon et al., SOSP 2023).** KV is virtual memory: fixed-size blocks, block table per sequence, near-zero internal fragmentation, copy-on-write sharing for beam/parallel sampling. vLLM: **2–4×** throughput vs FasterTransformer and Orca at comparable latency, larger gains at long sequences ([PagedAttention](https://arxiv.org/abs/2309.06180)). vLLM v1 automatic prefix caching hashes each block by `(parent_hash, block_tokens, extra_hashes)` so a shared system prompt is one set of physical blocks. Hash algos: `sha256` (default), `sha256_cbor` (reproducible), `xxhash` / `xxhash_cbor` (faster, **not cryptographically secure** — documented collision/leak risk in multi-tenant) ([vLLM prefix caching](https://docs.vllm.ai/en/stable/design/prefix_caching/)). `cache_salt` is mixed into the **first** block hash so only same-salt requests share ([vLLM RFC #16016](https://github.com/vllm-project/vllm/issues/16016)). Timing side-channel: [Leaking Secrets from Prefix Caches](https://arxiv.org/html/2411.18191v1); patched in vLLM ≥0.9.0 via salting. KVGov (2026) measures a cold/cached TTFT ratio of **0.22** on Qwen2.5-7B / vLLM 0.26.0 / A100 — the channel is exploitable at production scale ([KVGov](https://arxiv.org/html/2608.09225v1)).

**SGLang RadixAttention (Zheng et al., 2024).** Instead of discarding KV when a request ends, retain it in a radix tree (compressed trie) keyed by token sequences; longest-prefix match on admission; LRU/LFU/FIFO/… eviction of **leaves** so shared roots (system prompts) survive; refcount so in-flight nodes are unevictable. Compatible with continuous batching, paged layout, TP. Up to **5×** throughput vs baselines on their structured-program suite; largest win is TTFT on prefix hits ([SGLang paper](https://arxiv.org/abs/2312.07104); [LMSYS blog](https://lmsys.org/blog/2024-01-17-sglang/)). Scheduler policy `--schedule-policy lpm` (longest prefix match) is cache-aware *admission*, not just lookup.

**Hosted prompt cache is a productized prefix cache with a bill.**

OpenAI ([prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)):

- Eligible models: `gpt-4o` and newer. Routing: `prompt_cache_key` primary, prefix hash secondary.
- GPT-5.6+: exact match at breakpoints; implicit breakpoint on latest user/tool message; optional `prompt_cache_options.mode=explicit`; min **1,024** tokens through the breakpoint; cache writes **1.25×** uncached input; cache reads **0.1×**; TTL **30m** (refreshes on reuse). Keep a given `prompt_cache_key` at ~**15 RPM** or hit rate falls.
- Pre-5.6: automatic best-effort prefix reuse; **no** write fee; min 1,024–2,048 by model; hits in **128-token** increments; in-memory retention typically **5–10 min** idle, max ~1 h; extended up to **24 h**.
- Isolation: cache sharing limited to the **organization** (stated in the vLLM salt RFC citing OpenAI docs).

Anthropic ([prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)):

- Explicit `cache_control: { type: "ephemeral", ttl: "5m"|"1h" }` and/or automatic caching. Prefix = tools → system → messages up to the marked block.
- Multipliers vs base input: 5m write **1.25×**, 1h write **2×**, read **0.1×**. Breakpoints themselves are free.
- TTL refreshes on hit. Lifetime is measured from **request start**, so a 4-minute stream leaves ~1 minute for the next turn on a 5-minute cache.
- Min tokens: **512** (Opus 5 / Fable 5 / Mythos 5) … **4,096** (Haiku 4.5, some Opus 4.x). Silent no-op below min (`cache_*` usage = 0).
- Lookback: **20 content blocks** past the last write. Concurrent requests: entry is usable only **after the first response begins**.
- Isolation: org-level; workspace-level on Claude API, Claude Platform on AWS, Microsoft Foundry. KV + hashes **in memory only**, ZDR-eligible; not stored at rest.

Gemini / Vertex ([context cache overview](https://cloud.google.com/vertex-ai/generative-ai/docs/context-cache/context-cache-overview); [Vertex blog](https://cloud.google.com/blog/products/ai-machine-learning/vertex-ai-context-caching)): implicit (default, 90% off on hits, write = standard input, no storage fee) vs explicit (guaranteed discount: **90%** on Gemini 2.5+, **75%** on Gemini 2.0; plus **storage** $/MTok-hour). Min tokens: Gemini 2 family **2,048**; Gemini 3 family **4,096** (some Flash/Pro previews **6,144**). Explicit TTL min **1 minute**; no documented max.

**Semantic cache (GPTCache, Redis LangCache / RedisVL).** Embed the *query*, HNSW kNN, threshold (commonly discussed 0.85–0.95 cosine — ⚠️ **not a universal constant**; set per task with a false-hit eval set). Hit returns a **previous completion**, not KV. Sub-ms lookup is the Redis claim ([Redis semantic cache](https://redis.io/docs/latest/develop/use-cases/semantic-cache/); [GPTCache](https://github.com/zilliztech/gptcache)). This is an **application** cache in front of the model. It does not reduce prefill on a miss. Tenant + model-version + locale **must** be TAG filters in the same `FT.SEARCH` or you cross-talk answers.

**Speculative decoding cache.** Leviathan et al. ICML 2023: draft model proposes γ tokens; target verifies in one forward; rejection sampling recovers the **exact** target distribution; **2×–3×** on T5-XXL vs T5X ([paper](https://proceedings.mlr.press/v202/leviathan23a.html)). Chen et al. (DeepMind, 2023) independent speculative sampling. Medusa: extra heads on the target, tree attention, no separate draft weights. EAGLE: draft predicts **hidden states**, higher accept rate. vLLM blog: up to **2.8×** with their scheduler/memory changes (draft+target KV, multi-token slots per step) ([vLLM spec decode](https://vllm.ai/blog/2024-10-17-spec-decode)). DeepSeek-V3 uses MTP (multi-token prediction) as a trained speculative head. **Cache implication:** you store KV for draft *and* target; on reject you truncate KV back to the last accepted token. Acceptance rate α is the NFR: low α burns extra FLOPs and pollutes the batch token budget.

**Cache-aware routing.** Dynamo KV router: cost = f(load, overlap). `overlap_credit_blocks` can weight device/host/shared memory differently; `router_temperature` softmax-samples among workers to avoid herding ([Dynamo routing concepts](https://docs.nvidia.com/dynamo/components/router/routing-concepts)). llm-d: `kvevents.Pool` → block index (hash → pod + GPU/CPU medium) → `kvcache.Index` (prefix → pods) → Precise Prefix-Cache Scorer (% of this request already on each pod). Mooncake prefill scheduler: chained block hashes, compare against each prefill instance’s keys, pick max `prefix_len` under load. OpenAI’s `prompt_cache_key` is the hosted equivalent of affinity routing.

### 1.4 Routing: five mechanisms

| Mechanism | Decision time | Extra model calls | Typical win |
| --- | --- | --- | --- |
| **Complexity / preference router** (RouteLLM) | Before any LLM | 0 (tiny classifier) | **>2×** cost cut vs always-strong; MT Bench CPT(50%) ≈ **37%** GPT-4 calls, score 8.8 vs GPT-4 9.3 (**95%**); up to **75%** cost vs random on their table ([RouteLLM](https://arxiv.org/abs/2406.18665)) |
| **Cascade** (FrugalGPT) | After a cheap answer fails a judger | 1..k | Match GPT-4 with up to **98%** cost cut, or **+4%** accuracy at same cost, on *their* classification/QA sets ([FrugalGPT](https://arxiv.org/abs/2305.05176)) |
| **Cascade routing** (De Koninck et al., ICML 2025) | Hybrid | Variable | Proves when to combine; quality estimator is the bottleneck ([paper](https://proceedings.mlr.press/v267/dekoninck25a.html)) |
| **Fallback / HA** (LiteLLM) | On error | Retries | `order=1→2→fallback`; 429 puts deployment on `cooldown_time`; separate `content_policy_fallbacks` / `context_window_fallbacks` |
| **LoRA multiplex** | Per request adapter id | 0 | Punica SGMV: **12×** throughput vs SOTA, **~2 ms** extra latency/token ([Punica](https://arxiv.org/abs/2310.18547)). S-LoRA Unified Paging: **thousands** of adapters, up to **4×** vs naive vLLM LoRA, **30×** vs PEFT; 2,000 adapters on one GPU in their Llama-7B setup ([S-LoRA](https://arxiv.org/abs/2311.03285)) |

**Geo routing** is not the same as cache-affinity routing. Data-residency endpoints (OpenAI: **10%** uplift for eligible models released on/after 2026-03-05 on regional processing — [OpenAI pricing](https://developers.openai.com/api/docs/pricing)) pin the *control+data* region. Cache locality wants the *same replica* that holds the prefix. A user in EU with a US-warm prefix cache loses both: either a miss (full prefill) or a residency violation. Production pattern: regional KV indexes + regional model pools; replicate **only** non-PII system-prompt blocks if policy allows.

### 1.5 Batching and schedulers

**Orca (Yu et al., OSDI 2022)** invented **iteration-level scheduling** (continuous batching): after each forward, finished sequences leave, waiting sequences enter. Selective batching: linear/elementwise ops batch across ragged lengths; attention is per-sequence. GPT-3 175B: **36.9×** throughput vs FasterTransformer at equal latency ([Orca](https://www.usenix.org/system/files/osdi22-yu.pdf)). Anyscale popularized the term “continuous batching” for the same idea.

**vLLM scheduler (v1).** Three queues: waiting (not yet prefilled), running (decoding), swapped (KV spilled to CPU). After each iteration: free finished blocks → maybe swap → admit waiting under `max_num_batched_tokens` / KV headroom ([vLLM anatomy blog, 2025-09-05](https://vllm.ai/blog/2025-09-05-anatomy-of-vllm); [PagedAttention design](https://docs.vllm.ai/en/latest/design/paged_attention/)). Prefix caching is on by default in v1; `--no-enable-prefix-caching` to disable.

**Chunked / stall-free prefill (Sarathi-Serve).** Cap tokens per iteration: pack all running decodes first, then leftover budget as a prefill **chunk** (chunk size multiple of KV block size except the last). Removes the “generation stall.” TensorRT-LLM IFB (in-flight batching) is the NVIDIA name for the same iteration-level mix of context+generation, packed (no pad), with optional context chunking ([TRT-LLM IFB](https://nvidia.github.io/TensorRT-LLM/latest/features/paged-attention-ifb-scheduler.html)). Knobs: `max_batch_size`, `max_num_tokens` (often started at 8,192–16,384 in vendor guides — treat as **starting points**, not SLOs), `enable_chunked_prefill`, `free_gpu_memory_fraction` (default 0.9; back off to 0.7–0.8 on OOM), `enable_block_reuse`.

**SGLang scheduler.** Waiting queue + running batch; policies FCFS / LPM / DFS-weight; `--chunked-prefill-size` (tune down to 4,096/2,048 on prefill OOM); `--mem-fraction-static` ~0.9; `--schedule-conservativeness` as memory headroom. Radix match runs **before** priority so LPM can pack cache-friendly requests into the same batch.

**FlashAttention is the kernel under the scheduler, not a scheduler.** FA1 (Dao et al., NeurIPS 2022): IO-aware tiling, linear HBM instead of quadratic attention materialization ([FA1](https://arxiv.org/abs/2205.14135)). FA2 (2023): better work partitioning, **~2×** vs FA1, 50–73% of A100 peak, up to 225 TFLOP/s/GPU training ([FA2](https://arxiv.org/abs/2307.08691)). FA3 (Hopper, 2024): warp-specialized async + FP8; **1.5–2.0×** vs FA2 FP16, up to **740 TFLOP/s (75% H100)**; FP8 ~**1.2 PFLOP/s**, **2.6×** lower numerical error than naive FP8 attention ([FA3](https://arxiv.org/abs/2407.08608); [Dao blog](https://tridao.me/blog/2024/flash3/)). Decode still needs a **paged** FA variant (FlashInfer / TRT-LLM FMHA) because K/V are non-contiguous blocks.

### 1.6 Quantization topology

Two independent tensors: **weights** and **KV** (activations are a third, in W8A8).

| Recipe | What shrinks | Hardware | Role |
| --- | --- | --- | --- |
| **FP8** (E4M3/E5M2; Hopper/Blackwell tensor cores; Transformer Engine) | W and/or A and/or KV, 2× vs BF16 | H100+ | Default production quant on modern NVIDIA |
| **INT8 SmoothQuant** (Xiao et al., 2022) | W8A8 via migrating activation outliers into weights | Ampere+ INT8 tensor cores | Ada/Ampere when FP8 unavailable |
| **W4A16 GPTQ** (Frantar et al., ICLR 2023) | Weights 4-bit, second-order compensation | Any, dequant to FP16 on GEMM | Offline PTQ, calibration-set sensitive |
| **W4A16 AWQ** (Lin et al., MLSys 2024 Best Paper) | Weights 4-bit, protect salient channels using **activations** | Same | Generally better generalization than GPTQ at 4/3-bit; TinyChat **3.2–3.3×** vs HF FP16 on their GPU set ([AWQ](https://arxiv.org/abs/2306.00978)) |
| **FP8 KV** (TRT-LLM / DeepSeek R1 Blackwell blog) | KV 2× | Hopper+ | TRT-LLM: **+6%** E2E throughput at *same* concurrency plus higher max concurrency; GSM8K “no meaningful drop” in their table |
| **KIVI 2-bit KV** (Liu et al., ICML 2024) | KV ~4× vs FP16 with residual FP window | CUDA | Per-channel K, per-token V; **2.6×** peak memory (incl. weights) Llama-2-7B; up to **4×** batch, **2.35–3.47×** throughput |
| **QServe W4A8KV4** (Lin et al., 2024) | All three | A100 / L40S | vs TRT-LLM: Llama-3-8B **1.2×** A100 / **1.4×** L40S; Qwen1.5-72B **2.4×** A100 / **3.5×** L40S; claimed **~3×** $ serving on L40S vs A100+TRT-LLM |
| **NVFP4 / MXFP4** | W and KV on Blackwell | sm100/103 | TRT-LLM matrix: DeepSeek-R1 NVFP4 + FP8 KV on Blackwell |

MLA + FP8 KV stack: cache the latent in FP8, attention as absorbed MQA in FP8 ([TRT-LLM DeepSeek R1 blog](https://nvidia.github.io/TensorRT-LLM/1.2.0rc0/blogs/tech_blog/blog3_Optimizing_DeepSeek_R1_Throughput_on_NVIDIA_Blackwell_GPUs.html)).

---

## 2. Token Economics & NFR Metrics

### 2.1 The NFR board (what to put on the SLO doc)

| Metric | Plane | Notes |
| --- | --- | --- |
| **TTFT** p50/p95/p99 | Prefill + queue | Prefix/prompt-cache hits move **p50** more than **p99** (p99 is still a miss + queue). ⚠️ Vendor APIs do not publish these; measure. |
| **ITL / TPOT / TBT** p95 | Decode | Chunked prefill bounds the stall; disagg removes it. Sarathi: naive hybrid up to **28.3×** TBT vs decode-only. |
| **E2E latency** p99 | Sum + tools | Agent loops: cache TTL can expire *during* a long stream (Anthropic 5m clock starts at request start). |
| **Goodput** | Control | DistServe’s definition: max QPS with TTFT **and** TPOT SLOs both met for >90%. Throughput without this is a vanity metric. |
| **Cache hit rate** | Prefix / prompt / semantic | Split **token** hit rate vs **request** hit rate. OpenAI: `cached_tokens` / `cache_write_tokens`. Anthropic: `cache_read_input_tokens` / `cache_creation_input_tokens`. |
| **KV utilization** | Data | `% of GPU KV pool in use`. At ~100% you swap or reject (Mooncake early-reject). |
| **Accept length α** | Spec decode | Tokens kept per verify. Below ~1.5, spec often loses. |
| **RPM / TPM / RPD** | Control | OpenAI: whichever exhausts first. Headers: `x-ratelimit-limit-requests|tokens`, `remaining-*`, `reset-*`, plus **project** token limits ([rate limits](https://developers.openai.com/api/docs/guides/rate-limits); [error codes](https://developers.openai.com/api/docs/guides/error-codes)). 429 → honor `Retry-After`. Limits are **tier × model** and change; read the dashboard, do not freeze a blog’s GPT-4o table into an architecture doc. |
| **Concurrency (in-flight seqs)** | Decode | Set by KV bytes, not by “batch size 32.” MLA/GQA/KV-quant raise this cap. |

### 2.2 Official hosted cache prices (do not round into folklore)

**Anthropic, per million tokens** ([docs table, 2026-08-21](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)):

| Model | Base in | 5m write | 1h write | Read | Out |
| --- | --- | --- | --- | --- | --- |
| Claude Opus 4.6 / 4.7 / 4.8 / 5 | $5 | $6.25 | $10 | $0.50 | $25 |
| Claude Sonnet 4.6 / 4.5 | $3 | $3.75 | $6 | $0.30 | $15 |
| Claude Sonnet 5 | $2 | $2.50 | $4 | $0.20 | $10 |
| Claude Haiku 4.5 | $1 | $1.25 | $2 | $0.10 | $5 |
| Claude Fable 5 / Mythos 5 | $10 | $12.50 | $20 | $1 | $50 |

**OpenAI GPT-5.6 family, short-context, per million tokens** ([pricing](https://developers.openai.com/api/docs/pricing)): cached input **0.1×**, cache writes **1.25×**. Examples from that table: `gpt-5.6-sol` $5 / $0.50 / $6.25 / $30; `gpt-5.6-terra` $2 / $0.20 / $2.50 / $12; `gpt-5.6-luna` $0.20 / $0.02 / $0.25 / $1.20 (input / cached / writes / output). Long-context columns are 2× input and higher output — use the long column when the request actually crosses the long-context threshold.

**Gemini:** implicit/explicit reads typically **0.1×** input on 2.5+; explicit storage billed hourly (Gemini API pricing page lists **$1.00 per 1M cached tokens per hour** as the storage unit on several paid SKUs — confirm the live row for the exact model) ([Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)).

**Break-even (vendor arithmetic, not a benchmark).** Anthropic 5m: write 1.25, read 0.1 ⇒ first hit pays back the 0.25 premium; **second** read is net-cheaper than two uncached calls on the cached span. 1h write 2.0 ⇒ need **more** hits (order-of-magnitude: ~(2.0−1.0)/(1.0−0.1) ≈ **1.1** extra full-price equivalents, i.e. a handful of hits — exact N depends on whether you count the write as replacing a 1.0× process). OpenAI GPT-5.6 is the same 1.25 / 0.1 shape as Anthropic 5m. Pre-5.6 OpenAI: write is free ⇒ **any** hit is pure savings on that span.

### 2.3 `$ per 1k executions` — [inferred] worked example

Assumptions (label them on the slide): **1,000** sequential turns; stable prefix **8,000** tokens; variable suffix **500** input; **400** output; 5-minute Anthropic cache stays warm (TTL refresh); first call is a 5m write, the rest are reads; no batch discount; Sonnet 4.6 SKUs above.

| Mode | Formula | $ / 1k executions |
| --- | --- | --- |
| **No cache** | 1,000 × (8,500 × $3/1M + 400 × $15/1M) | **$31.50** |
| **Prompt cache 5m** | 1 × (8,000×$3.75 + 500×$3 + 400×$15)/1M + 999 × (8,000×$0.30 + 500×$3 + 400×$15)/1M | **$9.93** |
| **Always-Haiku 4.5, no cache** | 1,000 × (8,500×$1 + 400×$5)/1M | **$10.50** |
| **Route 70% Haiku / 30% Sonnet, no cache** [inferred] | 0.7×$10.50 + 0.3×$31.50 | **$16.80** |
| **Same mix + Sonnet prefix cache on the 30%** | ⚠️ path-dependent; do not quote without a trace | — |

Same shape on `gpt-5.6-terra` short-context: uncached **$21.80** / 1k; warm cache **~$7.42** / 1k ([inferred] from $2 / $0.20 / $2.50 / $12). Output still dominates long generations: a 2,000-token answer at $15/MTok is **$0.030**/call — cache cannot touch it. **Interview punchline:** cache is an *input* lever; routing/quantization/spec decode are the *output-time* levers.

Self-hosted **$/1k**: ⚠️ do not invent GPU-hour prices. Translate papers into **capacity**: PagedAttention 2–4× seqs/GPU; KIVI up to 4× batch from KV shrink; QServe 1.2–3.5× tok/s vs TRT-LLM on named GPUs; Punica 12× when the workload is many LoRAs. Multiply *your* measured tok/s by *your* cloud GPU SKU.

### 2.4 How each optimization moves the bill

| Lever | Token $ | GPU $ | Latency | Quality |
| --- | --- | --- | --- | --- |
| Prefix / prompt cache | Input 0.1× on hits; writes 1.25× (OpenAI 5.6+, Anthropic) | Less prefill FLOPs | TTFT p50 ↓ | Bit-identical if exact match |
| Semantic cache | **0** model tokens on hit | Embed + kNN | Tens of ms | **Not** identical — policy risk |
| Cascade | Pays 1..k models | — | p95 ↑ (serial) | Can *beat* the expensive model (FrugalGPT +4%) |
| Preference router | Mix of cheap/dear | — | ~unchanged | Bounded by CPT/PGR |
| Continuous batching | — | More tok/s/GPU | p50 TTFT may ↑ at high load | Same |
| Chunked prefill | — | Slight tok/s trade | ITL p99 ↓ | Same |
| P/D disagg | — | Better goodput/$ | Dual SLO | Same |
| FP8 W/KV | — | ~2× density | Decode ↑ | Small; measure GSM8K/MMLU/agent eval |
| INT4 AWQ | — | ~4× weight density; decode win at **small batch** (memory-bound) | Big at bs≤4; shrinks at large bs | Watch reasoning/long-context |
| Spec decode | Same output tokens | More FLOPs, fewer steps | ITL ↓ if α high | Lossless if reject-sampled |
| LoRA multiplex | Same | 1 base + N adapters | +~2 ms/tok (Punica) | Adapter-quality, not quant |

### 2.5 RPM/TPM interaction with cache and routers

Prefix cache **increases tokens processed per wall-clock second** on the GPU but **decreases billed input tokens** on hosted APIs. OpenAI TPM still counts input including cached? ⚠️ **confirm on the live rate-limit guide for that model** — do not assume cached tokens are excluded from TPM. Project-scoped `x-ratelimit-*-project-tokens` can strand you while org TPM remains. Routers that retry on 429 without jitter amplify stampedes (see §5). LiteLLM `routing_strategy` options include rate-limit-aware and lowest-cost; `simple-shuffle` is their production default.

---

## 3. Distributed Resilience & State

### 3.1 KV as clustered state

KV is **not** a cache in the Redis sense until you add a hierarchical store. On a single replica it is working memory: lose the pod, lose the prefix. LMCache makes it a **tiered store**: GPU working set → pinned CPU → local NVMe → remote (Redis, Mooncake Store, InfiniStore, S3, NIXL). In-process connector vs **MP daemon** (KV survives engine crash — no fate-sharing) ([LMCache docs](https://docs.lmcache.ai/); [LMCache paper](https://arxiv.org/html/2510.09665v2); [vLLM LMCache examples](https://docs.vllm.ai/en/latest/examples/disaggregated/lmcache/)). Dynamo KVBM: offload to host / local / object, “petabyte” class pools in the launch blog ([Dynamo intro](https://developer.nvidia.com/blog/introducing-nvidia-dynamo-a-low-latency-distributed-inference-framework-for-scaling-reasoning-ai-models/)). Mooncake Store: hash-indexed distributed KV, RDMA, used as SGLang hierarchical backend and vLLM connector ([Mooncake site](https://kvcache-ai.github.io/Mooncake/)).

**Failover implications:**

- **Decode replica death:** in-flight request’s KV is on that GPU. Without offload, **restart prefill** (TTFT spike). With disagg, a new decode worker can pull KV from prefill or from Mooncake/LMCache if it was published.
- **Prefill replica death mid-transfer:** decode must retry prefill or read a durable KV object. NIXL transfer is not a commit.
- **Router view stale:** llm-d/Dynamo indexes are event-sourced. A lagging index routes to a pod that already evicted the block → silent miss (correct, slower), or to a dead pod → error + fallback.
- **Swap (vLLM `swapped` queue):** CPU spill under memory pressure. Latency cliff; treat as a **degraded mode**, not capacity.

Mooncake’s **prediction-based early rejection** is the overload valve: under extreme load, refuse rather than accept and miss SLO. That is the correct distributed behavior; unbounded queues destroy p99 for everyone.

### 3.2 Replica and pool failover

| Failure | Detection | Action | Cache consequence |
| --- | --- | --- | --- |
| Engine 5xx / OOM | Health check + `allowed_fails` | LiteLLM cooldown `cooldown_time`; k8s restart | Prefix on that GPU gone unless LMCache/Mooncake |
| 429 TPM | Header / 429 | Cooldown that **deployment**, try `order+1` | Do not retry the same key immediately |
| P pool saturation | Queue depth, TTFT | Scale x in xPyD; Dynamo SLA planner | New prefills cold |
| D pool saturation | ITL, KV util | Scale y; admit fewer prefills | Backpressure P |
| Region outage | DNS / mesh | Geo failover | **Cold cache** in the new region; budget TTFT |

Dynamo SLA-based planner sizes GPU pools against latency SLOs ([Dynamo README](https://github.com/ai-dynamo/dynamo)). Grove (AKS series) places P/D pods using topology so NIXL does not cross the wrong spine ([AKS Dynamo part 3](https://blog.aks.azure.com/2026/03/16/dynamo-on-aks-part-3)).

### 3.3 Circuit breakers and rate limits

- **Per-deployment cooldown** (LiteLLM): after N failures, skip for `cooldown_time`. 429 is a first-class cooldown trigger.
- **Per-tenant RPM/TPM** at the gateway: do not wait for OpenAI to 429 the whole org key.
- **Token-budget circuit:** if KV utilization > threshold, stop admitting prefills (Sarathi/vLLM already refuse when blocks cannot allocate). Surface this as **429 with Retry-After**, not 500.
- **Semantic-cache circuit:** if embedder 5xx, fail **open** to the LLM (availability) or fail **closed** (cost cap) — pick one in the design review.
- **Spec-decode circuit:** if moving average α < threshold, disable spec for that model (it is stealing token budget from the batch).

Retries: exponential backoff + jitter; cap `max_fallbacks`. Cascade **is** a retry with a different model — count it in the error budget or you will hide outages as “quality variance.”

### 3.4 Consistency of prefix caches

Hash-based APC is **eventually consistent** across pods until a global index exists. Two requests with the same prefix on two cold GPUs both prefill. Cache-aware routing’s job is to make that rare. After a rolling deploy, **weights change ⇒ all KV is invalid**; engines already key extra hashes (LoRA id, mm hash). Quantization or FA kernel swap that is not bit-identical must bump a **cache generation** or you serve garbage attention.

---

## 4. Enterprise Security & Governance

### 4.1 Zero-Trust MCP at the inference gateway

MCP `tools/call` is a **privileged side channel into the same GPU** that holds other tenants’ KV. The gateway (not the engine) must:

1. Authenticate the MCP client (OAuth 2.1 / SPIFFE) and bind `tenant_id`.
2. Authorize tool name + args against policy **before** tokens hit the model (prompt injection via tool results is an inference-time incident).
3. Inject `cache_salt = HMAC(server_secret, tenant_id)` — **never** accept a client-supplied salt ([vLLM RFC](https://github.com/vllm-project/vllm/issues/16016); [Nudibranches write-up](https://nudibranchestecnologies.substack.com/p/vllm-and-data-leak)).
4. Propagate W3C `traceparent` (MCP SEP-414 `_meta`) so a tool span and the LLM span share `trace_id` for audit.
5. Rate-limit **per tenant × model**, not per cluster.

Zero-Trust here means: the GPU pool does not trust the prompt, the tool host, or the router score. Each hop presents identity; KV namespaces are cryptographically disjoint.

### 4.2 Tenant isolation of caches

| Cache | Isolation primitive | Residual risk |
| --- | --- | --- |
| vLLM / SGLang prefix | `cache_salt` / extra_key; or disable APC | Root-salted: lose sharing of global system prompts. KVGov **boundary salt** (inject at first PII block) estimated **93%** of prefix benefit retained — research, not a vLLM flag yet |
| OpenAI prompt cache | Organization (and `prompt_cache_key` routing) | Same-org multi-tenant apps **share** unless you partition keys and treat org as one trust domain |
| Anthropic | Org; workspace on some platforms | Workspaces are the product isolation unit — map them 1:1 to tenants |
| Gemini explicit | Project / cache resource IAM | IAM misbind = cross-app read of CachedContent |
| Semantic (Redis) | TAG `tenant` in the same KNN query | App-side filter after kNN is a classic bypass |
| LMCache / Mooncake remote | Key prefix + ACL on the store | A global hash of tokens without tenant is a **cross-tenant KV oracle** |
| LoRA multiplex | Adapter ACL; do not load tenant B’s adapter into tenant A’s batch without isolation story | S-LoRA batches heterogeneous adapters — side channels less studied than prefix cache |

vLLM docs: **xxhash is a security decision**, not a perf default, for multi-tenant ([prefix caching](https://docs.vllm.ai/en/stable/design/prefix_caching/)). Prefer `sha256_cbor` if hashes leave the box (Dynamo/llm-d global index).

### 4.3 PII in KV

KV tensors are a **lossy-but-invertible-enough** form of the prompt (attention keys/values). Legal/compliance view: **data in use**, same category as the prompt, not “ephemeral telemetry.”

- Do not put account numbers in the first *n* tokens of a shared prefix (timing channel infers them even when logits never leave the box).
- Prompt-cache providers: Anthropic states raw text is not stored at rest; KV+hashes in memory; ZDR-eligible. OpenAI in-memory vs 24h retention is a **DPA checkbox**. Gemini explicit storage is a **retained object** with TTL — treat like a document store.
- Offload to S3/Blob (Dynamo NIXL Azure Blob plugin): encryption at rest, CMK, path per tenant, lifecycle = TTL.
- Logs: `cached_tokens` is OK; dumping KV dumps PII. Span attributes should store **hashes of prefixes**, not prefixes.

### 4.4 Audit

Minimum audit tuple per request: `tenant_id`, `model`, `adapter_id`, `cache_salt_id` (not the secret), `prefix_hash`, `cached_tokens` / `cache_write_tokens`, `router_choice`, `fallback_reason`, `kv_pod_id`, `trace_id`. Cache hits must be auditable or you cannot explain a data-residency miss (request served from the wrong region’s warm cache). Semantic-cache hits need `source_request_id` of the stored answer for defamation/PII takedown.

Quantization and spec-decode are **quality-affecting**; log `quant_scheme` and `spec_method` in the model card of the serving revision so eval can be reproduced.

---

## 5. Production Failure Modes

### 5.1 Cache stampede (thundering herd)

**Symptom:** traffic spike or deploy → all prefixes miss → every replica prefills the 8k system prompt → GPU compute saturates → TTFT p99 explodes → retries make it worse. Hosted: many **1.25× writes** of the same prefix; Anthropic: parallel requests cannot hit until the first response **starts**.

**Mitigations:** (1) single-flight / request coalescing on `prefix_hash` at the gateway; (2) cache-aware routing so the *one* writer’s pod gets the herd; (3) pre-warm (Anthropic documents pre-warm; OpenAI 15 RPM/key cap — shard keys rather than one global key); (4) jittered retries; (5) explicit breakpoints so you do not rewrite the unstable suffix every time (OpenAI 5.6 implicit trap).

### 5.2 Prefix mismatch (silent miss)

**Causes:** timestamp/ISO date in the system prompt; tool JSON key order; `parallel_tool_calls` flag; image bytes; LoRA id not in extra hash; whitespace; ChatML vs Harmony template; a “helpful” gateway that injects a request id **before** the cache breakpoint. OpenAI: hits in 128-token steps on older models — a 1,030-token prefix may cache 1,024. Anthropic: 20-block lookback exceeded in long tool traces.

**Detect:** `cached_tokens==0` with `prompt_length` above min. Alert on **write/read ratio** > threshold (OpenAI: high `cache_write_tokens`, low `cached_tokens` = breakpoint includes mutables).

### 5.3 OOM and KV exhaustion

**Causes:** `gpu_memory_utilization` / `mem-fraction-static` too high; `max_model_len` × concurrency × layers × KV-bytes; spec decode doubling KV; chunked prefill still allocating full output horizon; fragmentation without paging (should be rare on vLLM); multimodal activations.

**Symptoms:** engine SIGKILL, `CUDA out of memory`, vLLM swap storm, Mooncake reject. **Mitigations:** lower max concurrent seqs; GQA/MLA/FP8-KV; LMCache offload; reject at the gateway when KV util > 85% (pick a measured number); never “retry OOM” without shrinking `max_tokens`.

### 5.4 Quality collapse from over-quant

**Patterns (from papers, not a license to skip eval):**

- GPTQ overfit to calibration domain; AWQ was designed to avoid that, still **eval on *your* long-context and tool-calling suite**.
- W4A16 helps **memory-bound** small batches; at large continuous-batch decode, INT4 dequant can **lose** to FP8 Tensor Cores (TRT-LLM guidance: FP8 first on Hopper/Blackwell; INT4 for memory-constrained / bs≤4).
- KV INT2/INT4 without KIVI’s residual window or QServe SmoothAttention: long-context perplexity and retrieval-in-context fail first.
- FP8 attention without FA3-style incoherent processing: FA3 reports **2.6×** less numerical error than naive FP8 — naive FP8 is a real failure mode.
- Mixing KV dtype across P/D disagg without converting on transfer: garbage decode.

**Governance:** a quantized checkpoint is a **new model version**. Gate on agent eval + safety eval, not just MMLU.

### 5.5 Router misclassification

RouteLLM-style **under-routing** (hard query → weak model): silent quality drop, no 4xx. **Over-routing:** cost returns to always-strong. Cascades **mis-judge** (DistilBERT says “good” on a fluent wrong answer): FrugalGPT’s win is task-dependent; 98% savings will not transfer to open-ended legal advice.

**Mitigations:** (1) shadow-route a % to strong and measure PGR; (2) escalate on self-reported uncertainty / tool-failure / policy classifiers; (3) never cascade on the **latency-critical** path without a parallel-router alternative (RouteLLM’s point: one call); (4) pin `model` for regulated workflows — routers are for **Tier-B** traffic.

### 5.6 Other high-severity modes

| Mode | Signature | Fix |
| --- | --- | --- |
| **Prefix timing leak** | Attacker varies 1 token, measures TTFT | Tenant salt; disable APC; don’t share GPUs across trust domains |
| **Spec rollback bugs** | Duplicate/missing tokens, KV desync | Disable spec; pin engine version |
| **Hash algo change** | After upgrade, 0% prefix hits | `sha256_cbor` for cross-version; bump generation |
| **Herding to the cache-rich pod** | One GPU 100% KV, others idle | Dynamo load term + `router_temperature` |
| **Semantic false hit** | Confident wrong FAQ | Raise threshold; tenant+version tags; human eval of hit set |
| **TTL vs streaming** | Turn 2 misses after long completion | 1h Anthropic TTL or OpenAI 30m with keep-alive |
| **LoRA mix-up** | Wrong adapter in batch | Adapter id in block extra_hash; authz at gateway |

---

## 6. Enterprise System Design Scenarios

### 6.1 Decision matrix (control-plane choice)

| Workload | Cache | Route | Batch | Quant | Why |
| --- | --- | --- | --- | --- | --- |
| **SaaS chatbot, shared system prompt, many tenants** | APC + **gateway HMAC salt**; hosted prompt cache with per-tenant `prompt_cache_key` | HA fallbacks, not quality cascade | Continuous + chunked prefill | FP8 W+KV | Isolation > max hit rate |
| **RAG / long doc QA, same corpus, many questions** | Prefix or Gemini explicit cache of the doc; LMCache for self-host | Cache-affinity router | Disagg P/D if TTFT SLO tight | FP8; consider KIVI if context >> 32k | Prefill dominates $ and TTFT |
| **Agent, 40-turn, 20 tools** | Breakpoint **after tools+system**, before each turn; watch 20-block lookback | Pin model; fallbacks for 429 | Chunked prefill; cap `max_tokens` | FP8; spec decode if α high on this schema | Tools mutate prefix; implicit OpenAI 5.6 writes junk |
| **Regulated (health/finance)** | Disable cross-tenant APC; no semantic cache; in-memory TTL only | No cost router on PHI | Colocate in-region; geo pin | Conservative (FP8 or BF16) | PII in KV = in-scope |
| **Multi-LoRA product (N customers × 1 base)** | Prefix salt per tenant; adapters in extra_hash | Punica/S-LoRA multiplex, not N replicas | Heterogeneous LoRA batch | Base FP8; adapters FP16 | Punica 12× is the economics |
| **Code / reasoning, quality-first** | Prefix OK | Strong model; cascade only with a **reliable** judger | Disagg for ITL SLO | Avoid INT4 until eval; FP8 KV OK if GSM8K-class holds | Over-quant shows up as “the model got dumber” tickets |
| **Burst FAQ / support** | Semantic cache **after** policy/PII filter, TTL short | Cheap model + escalate | High batch | INT4 on the cheap tier | False-hit risk is the design constraint |
| **Global app + residency** | Regional KV indexes; no cross-region prefix | Geo DNS + regional pools | Per-region xPyD | Same checkpoint hash all regions | 10% OpenAI residency uplift vs a residency incident |

### 6.2 Scenario A — “Cut 60% of input $ without touching quality”

**Allowed:** exact prefix/prompt cache, FP8 that passed eval, HA fallbacks. **Forbidden:** semantic cache, INT4, complexity router.

Shape the prompt: stable tools+policy ≥ min cache tokens, then user. OpenAI 5.6: `mode=explicit`, one breakpoint, `prompt_cache_key=tenant:promptver`. Anthropic: 5m unless inter-arrival >5m then `ttl=1h`. [Inferred] Sonnet 4.6 example in §2.3 is a **68%** input-side cut at 1k warm turns ($31.50 → $9.93) — **only** if the 8k prefix actually hits. Instrument `cache_read / input` daily.

### 6.3 Scenario B — “p99 ITL is the page”

Chunked prefill first (Sarathi). If p99 still tracks prefill arrivals, **disagg** (DistServe/Dynamo/Mooncake). Spec decode if α≥2 on this workload (vLLM 2.8× is an upper bound, not a forecast). Do not raise batch size as the first move — that **worsens** ITL. Track TBT/ITL histograms **separately** from TTFT.

### 6.4 Scenario C — “2,000 adapters, one 70B”

S-LoRA/Punica (or vLLM LoRA if N is tens, not thousands). Unified paging for adapters+KV. Gateway maps `Authorization` → `adapter_id`. Cache salt **and** LoRA id in the block hash so tenant A cannot ride tenant B’s prefix. Budget the **2 ms/tok** Punica add on the SLO.

### 6.5 Scenario D — “Blackwell fleet, DeepSeek-class MoE”

MLA already crushed KV (70 KB/tok). Add FP8 KV + FA3/FlashMLA. TRT-LLM or SGLang with EP. Dynamo xPyD: small TP on prefill, larger TP/EP on decode (Dynamo design: compute-bound vs memory-bound parallelism **decoupled**). NVFP4 weights where the TRT-LLM matrix says Y for that GPU. MTP spec decode as DeepSeek intended. Failure mode: expert-parallel imbalance looking like “random p99” — that’s routing in the **MoE** layer, not the request router.

### 6.6 Scenario E — “Zero-Trust agent platform”

```
Client → mTLS/OAuth gateway → (MCP policy + HMAC salt + RPM)
       → cache-affinity router (tenant-scoped index)
       → P pool → NIXL → D pool
       → LMCache remote with tenant key prefix
Audit  → SIEM (tuple in §4.4)
```

Semantic cache **off** for tools that mutate world state. Circuit breaker on KV util. Prefix caching **on** only with salts. xxhash **off**.

### 6.7 Trade-off cheat sheet (interview close)

1. **Exact cache** is free lunch on quality; **semantic cache** is a product decision.
2. **Router-before** (RouteLLM) protects latency; **cascade-after** (FrugalGPT) spends latency to buy $ and sometimes quality.
3. **Chunking** is the poor man’s disagg; **disagg** is for when TTFT and ITL SLOs both bind.
4. **FP8** is the 2026 default on Hopper/Blackwell; **INT4** is for memory walls and small batches.
5. **KV isolation** is a security control with a throughput cost; salts are cheaper than separate clusters; separate clusters are what you do for hostile multi-tenant.
6. **Never** quote a paper’s 7.4× / 12× / 98% as *your* SLO. Quote it as the **shape** of the curve you will measure.

---

## Sources

1. https://arxiv.org/abs/2309.06180
2. https://docs.vllm.ai/en/stable/design/prefix_caching/
3. https://docs.vllm.ai/en/latest/design/paged_attention/
4. https://vllm.ai/blog/2025-09-05-anatomy-of-vllm
5. https://vllm.ai/blog/2024-10-17-spec-decode
6. https://github.com/vllm-project/vllm
7. https://github.com/vllm-project/vllm/issues/16016
8. https://arxiv.org/abs/2312.07104
9. https://lmsys.org/blog/2024-01-17-sglang/
10. https://github.com/sgl-project/sglang
11. https://www.usenix.org/system/files/osdi22-yu.pdf
12. https://arxiv.org/abs/2403.02310
13. https://www.usenix.org/system/files/osdi24-agrawal.pdf
14. https://arxiv.org/abs/2401.09670
15. https://www.usenix.org/system/files/osdi24-zhong-yinmin.pdf
16. https://haoailab.com/blogs/distserve/
17. https://haoailab.com/blogs/distserve-retro/
18. https://arxiv.org/abs/2311.18677
19. https://arxiv.org/abs/2205.14135
20. https://arxiv.org/abs/2307.08691
21. https://arxiv.org/abs/2407.08608
22. https://tridao.me/blog/2024/flash3/
23. https://github.com/Dao-AILab/flash-attention
24. https://proceedings.mlr.press/v202/leviathan23a.html
25. https://arxiv.org/abs/2302.01318
26. https://nvidia.github.io/Model-Optimizer/guides/5_speculative_decoding.html
27. https://arxiv.org/abs/2405.04434
28. https://arxiv.org/abs/2412.19437
29. https://doi.org/10.1145/3695053.3731412
30. https://arxiv.org/abs/2305.13245
31. https://arxiv.org/abs/2306.00978
32. https://github.com/mit-han-lab/llm-awq
33. https://arxiv.org/abs/2210.17323
34. https://arxiv.org/abs/2211.10438
35. https://arxiv.org/abs/2402.02750
36. https://arxiv.org/abs/2405.04532
37. https://arxiv.org/abs/2310.18547
38. https://github.com/punica-ai/punica
39. https://arxiv.org/abs/2311.03285
40. https://www.lmsys.org/blog/2023-11-15-slora/
41. https://arxiv.org/abs/2305.05176
42. https://arxiv.org/abs/2406.18665
43. https://proceedings.mlr.press/v267/dekoninck25a.html
44. https://arxiv.org/abs/2106.09685
45. https://developers.openai.com/api/docs/guides/prompt-caching
46. https://developers.openai.com/api/docs/pricing
47. https://developers.openai.com/api/docs/guides/rate-limits
48. https://developers.openai.com/api/docs/guides/error-codes
49. https://platform.claude.com/docs/en/build-with-claude/prompt-caching
50. https://cloud.google.com/vertex-ai/generative-ai/docs/context-cache/context-cache-overview
51. https://cloud.google.com/blog/products/ai-machine-learning/vertex-ai-context-caching
52. https://ai.google.dev/gemini-api/docs/pricing
53. https://github.com/ai-dynamo/dynamo
54. https://developer.nvidia.com/blog/introducing-nvidia-dynamo-a-low-latency-distributed-inference-framework-for-scaling-reasoning-ai-models/
55. https://docs.nvidia.com/dynamo/dev/design-docs/disaggregated-serving
56. https://docs.nvidia.com/dynamo/components/router/routing-concepts
57. https://nvidia.github.io/TensorRT-LLM/latest/features/paged-attention-ifb-scheduler.html
58. https://nvidia.github.io/TensorRT-LLM/latest/features/quantization.html
59. https://nvidia.github.io/TensorRT-LLM/1.2.0rc0/blogs/tech_blog/blog3_Optimizing_DeepSeek_R1_Throughput_on_NVIDIA_Blackwell_GPUs.html
60. https://arxiv.org/abs/2407.00079
61. https://www.usenix.org/system/files/fast25-qin.pdf
62. https://kvcache-ai.github.io/Mooncake/
63. https://kvcache-ai.github.io/Mooncake/performance/vllm-v1-support-benchmark.html
64. https://docs.lmcache.ai/
65. https://arxiv.org/html/2510.09665v2
66. https://docs.vllm.ai/en/latest/examples/disaggregated/lmcache/
67. https://llm-d.ai/blog/kvcache-wins-you-can-see
68. https://docs.litellm.ai/docs/routing
69. https://docs.litellm.ai/docs/proxy/load_balancing
70. https://github.com/zilliztech/gptcache
71. https://redis.io/docs/latest/develop/use-cases/semantic-cache/
72. https://arxiv.org/html/2411.18191v1
73. https://arxiv.org/html/2608.09225v1
74. https://blog.aks.azure.com/2026/03/16/dynamo-on-aks-part-3
75. https://www.anyscale.com/blog/continuous-batching-llm-inference
76. https://nudibranchestecnologies.substack.com/p/vllm-and-data-leak
