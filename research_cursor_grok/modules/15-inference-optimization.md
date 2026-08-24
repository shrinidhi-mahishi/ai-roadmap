# Module 15 — Inference Optimization

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `research_cursor_grok/research/15-inference-optimization.md` (researched 2026-08-21, 76 sources). Prices are **vendor-published token SKUs** as of 2026-08-21. ⚠️ No unpublished production p50/p95/p99 TTFT/ITL SLOs are invented. `$ per 1k executions` is **[inferred]** from a named token mix × official $/MTok — not a GPU-hour rate. Paper speedups are the authors’ numbers on *their* hardware/workload, not portable SLAs.
**Mandatory topics**: Caching · Routing · Batching · Quantization.

The unit of production is not “we turned on vLLM prefix caching.” It is a **control plane** (admission, cache-affinity scoring, xPyD pool sizing, fallbacks, tenant `cache_salt`, SLA planner) wrapping a **data plane** with **two clocks**: prefill is compute-bound (**TTFT**); decode is memory-bandwidth-bound (**ITL / TPOT**). Persistence is two stores: **application checkpoints** versus **KV / prompt cache** (a materialization of the prompt, not scratch). NVIDIA Dynamo / llm-d sit *above* engines (vLLM, SGLang, TensorRT-LLM); LiteLLM is the *API* control plane. Collapsing planes — hashing the raw prompt in the client, then routing on cache affinity without isolation — is how teams leak prompts via timing side-channels, OOM the GPU, and bill **1.25×** cache writes forever.

**Invariant:** anything that *scores* cache overlap belongs on the gateway. Anything that *writes* KV tensors belongs on the engine.

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns identity, RPM/TPM, HMAC `cache_salt`, cache-affinity scoring, cascade/preference routing, circuit breakers, xPyD pool sizing, and the admission 429. Data plane owns tokenizer → prefill GEMM → KV write → (optional NIXL/Mooncake handoff) → decode attention → sampler. The **batcher** is an engine scheduler (Orca iteration-level / vLLM v1 / Sarathi chunked prefill / TRT-LLM IFB), not a gateway queue. Persistence splits **hard** (checkpoints, WORM audit, Kafka `KVEvents` index) from **soft** (PagedAttention blocks, RadixCache, hosted prompt cache, LMCache tiers). Tool proxies execute side effects; the GPU never holds IAM. Telemetry is the only authoritative token bill on streaming (`cached_tokens` / `cache_write_tokens` / `cache_read_input_tokens`).

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS  (SSE / sync HTTP / Batch / MCP client)                                 │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │  TLS + W3C traceparent + correlation-id
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE  (LiteLLM / Envoy / llm-d Gateway / Dynamo frontend)              │
│                                                                                 │
│  ┌────────────┐  ┌──────────────┐  ┌────────────────┐  ┌─────────────────────┐  │
│  │ API Gateway│─▶│ Policy       │─▶│ ROUTER         │─▶│ SLA / xPyD planner  │  │
│  │ auth, RPM  │  │ PII detect→  │  │ affinity score │  │ admit if KV util    │  │
│  │ TPM, 429   │  │ redact→audit │  │ cascade/pref.  │  │  < 85%; cooldown    │  │
│  │ breaker    │  │ tool RBAC    │  │ HA order=1→2   │  │  on 429/5xx         │  │
│  │            │  │ HMAC salt    │  │ LoRA adapter   │  │ single-flight hash  │  │
│  │            │  │ MCP allowlist│  │ geo pin        │  │                     │  │
│  └────────────┘  └──────┬───────┘  └───────┬────────┘  └──────────┬──────────┘  │
└─────────────────────────┼──────────────────┼──────────────────────┼─────────────┘
                          │                  │ hosted complete()    │
                          │ tools/call       │ or engine RPC        │
                          ▼                  ▼                      │
┌─────────────────────────┼─────────────────────────────────────────┼─────────────┐
│ DATA PLANE              │  (vLLM / SGLang / TRT-LLM; Dynamo coords│  pools)     │
│                         │                                         │             │
│  ┌───────────┐  ┌───────┴────────┐  ┌─────────────┐  ┌──────────┐ │ ┌────────┐ │
│  │ Tokenizer │─▶│ PREFILL pool   │─▶│ KV transfer │─▶│ DECODE   │─┘ │ Sampler│ │
│  │ + template│  │ compute-bound  │  │ NIXL /      │  │ memory-  │   │        │ │
│  │           │  │ writes KV      │  │ Mooncake /  │  │ bound    │   │        │ │
│  │           │  │ TTFT KPI       │  │ LMCache     │  │ ITL/TPOT │   │        │ │
│  └───────────┘  └───────┬────────┘  └─────────────┘  └────┬─────┘   └────┬───┘ │
│                         │                                 │              │     │
│                         ▼                                 ▼              │     │
│                  ┌──────────────────────────────────────────────┐        │     │
│                  │ BATCHER (engine, iteration-level)            │        │     │
│                  │ waiting │ running │ swapped                  │        │     │
│                  │ pack DECODE first, leftover = PREFILL chunk  │        │     │
│                  │ cap = max_num_batched_tokens / KV headroom   │        │     │
│                  └──────────────────────┬───────────────────────┘        │     │
│                                         │ logits                         ▼     │
│                                         └──────────────────────────▶ parser    │
└─────────────────────────────────────────────────────────────────────────┬───────┘
                                                                          │
              ┌───────────────────────────────────────────────────────────┤
              │ stop_reason = tool_use                                    │ final
              ▼                                                           ▼
┌─────────────────────────────────┐              ┌────────────────────────────────┐
│ TOOL PROXIES (untrusted planner │              │ PERSISTENCE                    │
│  never holds IAM)               │              │                                │
│  ┌──────────┐  ┌─────────────┐  │              │  ┌──────────────────────────┐  │
│  │ Signed   │─▶│ Sandbox     │  │              │  │ App / Temporal history   │  │
│  │ ticket   │  │ HTTP/SQL    │──┼── tool_result│  │ Kafka outbox + DLQ       │  │
│  │ STS      │  │ JSON-encode │  │              │  └──────────────────────────┘  │
│  └──────────┘  └─────────────┘  │              │  ┌──────────────────────────┐  │
└─────────────────────────────────┘              │  │ Soft caches (not RPO=0)  │  │
                                                 │  │ PagedAttention KV blocks │  │
                                                 │  │ Radix / APC prefix tree  │  │
                                                 │  │ Hosted prompt cache TTL  │  │
                                                 │  │ Semantic (Redis) answers │  │
                                                 │  │ LMCache GPU→CPU→NVMe→obj │  │
                                                 │  └──────────────────────────┘  │
                                                 └──────────────┬─────────────────┘
                                                                │
┌───────────────────────────────────────────────────────────────┴─────────────────┐
│ TELEMETRY                                                                       │
│  ┌──────────────┐  ┌───────────────┐  ┌─────────────┐  ┌──────────────────────┐ │
│  │ Audit (WORM) │  │ Metrics       │  │ Traces      │  │ Usage (authoritative │ │
│  │ tenant, salt │  │ TTFT p50/95   │  │ gw→prefill  │  │ on terminal event)   │ │
│  │ _id (not     │  │ ITL/TBT, α    │  │ →decode→    │  │ cached_tokens,       │ │
│  │ secret),     │  │ hit%, KV util │  │ tool        │  │ cache_write_tokens,  │ │
│  │ prefix_hash, │  │ breaker, α    │  │             │  │ quant_scheme, spec   │ │
│  │ router_choice│  │ goodput       │  │             │  │                      │ │
│  └──────────────┘  └───────────────┘  └─────────────┘  └──────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Interview move:** three boxes — **gateway**, **prefill pool**, **decode pool** — with KV transfer on the P↔D edge. Cache scoring on the gateway. KV writes on the engine. Do not put PII above a shared breakpoint.

### 1.2 End-to-end request flow

1. **Ingress.** Client opens SSE or sync HTTP. Gateway stamps `correlation_id` + W3C `traceparent`, authenticates, checks per-tenant RPM/TPM. A **closed** breaker on the primary deployment is already a routing input. If KV util ≥ admit watermark, return **429 + Retry-After**, not 500 (Mooncake early-reject is the cluster analogue).
2. **Policy.** PII **detect → redact → audit** *before* tokenize and *before* any cache key. Inject `cache_salt = HMAC(server_secret, tenant_id)` — **never** a client-supplied salt (vLLM RFC #16016). Tool RBAC attaches only authorized tools. MCP `tools/call` is authorized here; the GPU is not a policy engine.
3. **Route.** Five mechanisms, one request: (a) **geo pin** (residency; OpenAI +10% on eligible regional SKUs) before affinity; (b) **cache-affinity** (Dynamo overlap+load, llm-d Precise Prefix-Cache Scorer, Mooncake max `prefix_len`); (c) **preference / complexity** (RouteLLM-style, one cheap classifier); (d) **cascade** only on Tier-B, never the latency-critical path; (e) **HA order** on 429/5xx. `prompt_cache_key = tenant:promptver` on hosted APIs. LoRA `adapter_id` is an extra hash, not a replica.
4. **Cache lookup (control).** Semantic cache (embed kNN) may return a **previous answer** and skip the LLM — tenant+model-version+locale **must** be TAG filters in the same query. Prefix/APC is a **block-hash** lookup, not embedding. Miss continues.
5. **Prefill (data).** Compute-bound; all prompt tokens in parallel; KPI = TTFT. Colocated: chunked prefill (Sarathi) so a long prompt does not stall in-flight decode (naive hybrid: up to **28.3×** TBT vs decode-only). Disagg: PrefillRouter picks worker by KV overlap + load; worker writes KV and returns `disaggregated_params`.
6. **KV handoff.** Decode pulls via NIXL (NVLink / IB / UCX) — Mooncake vLLM 1P1D microbench: 32,768-tok prompt, 4.50 GB KV, **31.65 ms** transfer = **4.2% of TTFT** at 142.25 GB/s. NIXL is **not a commit**. Short prompts + high QPS: skip disagg (transfer+hop dominate). DistServe rule: colocate P and D when interconnect cannot hide transfer.
7. **Decode + batcher.** Memory-bound, one token (or speculative tree) per step; KPI = ITL/TPOT. After each forward: finished sequences leave, waiting enter (Orca). Pack **running decodes first**, leftover `max_num_batched_tokens` as a prefill **chunk** (multiple of KV block size except last). Spec decode: draft γ tokens, target verifies; on reject, **truncate KV** to last accepted token.
8. **Sample / tool / persist.** Parser emits tokens. `tool_use` → proxy (signed ticket, sandbox, JSON-encode). Orchestrator checkpoints **app** state. KV remains soft. Audit tuple: `tenant_id`, `model`, `adapter_id`, `cache_salt_id`, `prefix_hash`, `cached_tokens`, `router_choice`, `fallback_reason`, `kv_pod_id`, `quant_scheme`, `spec_method`, `trace_id`.

**Interview talking point:** “Prefill and decode are different hardware profiles. Cache is five layers that are not the same cache. The gateway salts; the engine pages.”

---

## 2. Core Mechanics & Algorithms

### 2.1 Caching — five layers, five keys

| Layer | Match key | Stored | Hit savings | Wrong if… |
| --- | --- | --- | --- | --- |
| **KV / PagedAttention** | This sequence’s past tokens | K,V per layer (or MLA latent) | Decode does not re-prefill | Confused with *cross-request* reuse |
| **Prefix / APC** | Hash(parent, block tokens, extra) + salt | KV **blocks** shared across requests | Skip shared-prefix prefill | One-token mutation; xxhash in multi-tenant; no salt |
| **Prompt cache (hosted)** | Rendered prefix at breakpoint + `prompt_cache_key` | Provider KV (opaque) | Input **0.1×**; TTFT drop | Timestamp/tools *before* breakpoint |
| **Semantic** | Embedding kNN above a threshold | **Text of a previous response** | Skip the LLM | Threshold too low; no tenant TAG |
| **Speculative** | Draft KV + target KV + tree attn | Two+ KV pools | Extra decode tokens / target fwd | Low α; VRAM × (1+draft) |

**KV bytes (interview formula).** Per token, per layer, BF16:

\[
\mathrm{KV}_{tok,layer} = 2 \times h_{kv} \times d_{head} \times 2\ \mathrm{bytes}
\]

MHA: \(h_{kv}=h_q\). GQA (Ainslie et al., EMNLP 2023): Llama-class 64 Q / 8 KV = **8×** KV cut vs MHA. MQA: one KV head. MLA (DeepSeek-V2): cache latent \(c_{KV}\) + short RoPE key \(k^R\) → **93.3%** KV reduction vs DeepSeek 67B MHA, **5.76×** max generation throughput. DeepSeek-V3: **~70 KB/token** KV vs Llama-3.1-405B **516 KB/token**, Qwen-2.5-72B **327 KB/token**. Concurrency is set by **KV bytes**, not “batch size 32.”

**PagedAttention (Kwon et al., SOSP 2023).** KV as virtual memory: fixed blocks, per-sequence block table, near-zero internal fragmentation, CoW for beam/parallel. vLLM: **2–4×** throughput vs FasterTransformer/Orca at comparable latency. v1 APC hashes `(parent_hash, block_tokens, extra_hashes)`; `cache_salt` mixes into the **first** block. Hash: `sha256` (default), `sha256_cbor` (reproducible, use if hashes leave the box), `xxhash` (**not** cryptographically secure — documented collision/leak). Timing channel: *Leaking Secrets from Prefix Caches*; patched vLLM ≥0.9.0 via salting. KVGov (2026): cold/cached TTFT ratio **0.22** on Qwen2.5-7B / vLLM 0.26.0 / A100 — exploitable at production scale.

**SGLang RadixAttention.** Retain KV in a radix tree after the request ends; longest-prefix match on admission; evict **leaves** (LRU/LFU/FIFO) so shared roots survive; refcount ⇒ in-flight nodes unevictable. `--schedule-policy lpm` is cache-aware *admission*. Up to **5×** throughput on their structured-program suite; largest win is TTFT on prefix hits.

**Hosted prompt cache (productized prefix + a bill).**

| Provider | Match | Min | Write | Read | TTL / isolation |
| --- | --- | --- | --- | --- | --- |
| OpenAI GPT-5.6+ | Exact at breakpoints; `prompt_cache_key` primary | **1,024** through breakpoint | **1.25×** | **0.1×** | 30 m refresh; keep key ~**15 RPM**; **org** |
| OpenAI pre-5.6 | Best-effort prefix; 128-token steps | 1,024–2,048 | **free** | hit savings | ~5–10 min idle, max ~1 h; ext. 24 h |
| Anthropic | `cache_control` ephemeral `5m`/`1h`; tools→system→messages | **512** (Opus/Fable/Mythos 5) … **4,096** (Haiku 4.5) | 5m **1.25×**, 1h **2×** | **0.1×** | TTL from **request start**; 20-block lookback; concurrent usable only **after first response begins**; org / workspace; in-memory, ZDR-eligible |
| Gemini | Implicit (default) vs explicit named | 2,048 (2.x) / 4,096 (3.x); some **6,144** | implicit = standard in; explicit + **storage $/MTok-h** | typically **0.1×** (2.5+); explicit 75% on 2.0 | explicit TTL min **1 min**; IAM on CachedContent |

Break-even (vendor arithmetic): 5m write 1.25 / read 0.1 ⇒ **first hit** pays the 0.25 premium; second read is net-cheaper on the cached span. 1h write 2.0 ⇒ ~(2.0−1.0)/(1.0−0.1) ≈ **1.1** extra full-price equivalents.

**Semantic cache (GPTCache, Redis LangCache).** Embed the *query*, HNSW kNN, threshold commonly 0.85–0.95 cosine — ⚠️ **not a universal constant**; set per task with a false-hit eval set. Sub-ms lookup is the Redis claim. Application cache: miss does **not** reduce prefill. Fail-open to LLM on embedder 5xx (availability) or fail-closed (cost cap) — pick in the design review.

**Speculative cache.** Leviathan et al. ICML 2023: draft proposes γ tokens; target verifies in one forward; rejection sampling ⇒ **exact** target distribution; **2×–3×** on T5-XXL. vLLM blog: up to **2.8×** with their scheduler. Medusa/EAGLE/MTP are variants. **α** (accept length) is the NFR: below ~**1.5**, spec often loses (extra FLOPs + polluted token budget). Circuit: disable spec when moving-average α is below threshold.

**Prefix-cache state machine**

```
        ┌─────────┐  TTL expiry / quant|weight gen bump / salt change
        │  COLD   │◀──────────────────────────────────────────────┐
        └────┬────┘                                               │
             │ prefill + write (1.25× or 2× hosted)               │
             ▼                                                    │
        ┌─────────┐  hit 0.1×, refresh TTL                        │
        │  WARM   │───────────────────────────────────────────────┤
        └────┬────┘                                               │
             │ silent miss: timestamp/tools/LoRA/template above   │
             ▼   breakpoint; OpenAI 1,030 tok caches 1,024        │
        ┌─────────┐  cached_tokens=0, write/read ratio high ──────┘
        │MISMATCH │
        └─────────┘
```

**Invariants.** (1) Same `(salt, block tokens, LoRA id, mm hash, cache generation)` ⇒ same physical blocks; otherwise disjoint. (2) In-flight radix nodes unevictable. (3) Spec reject truncates KV to last accepted token. (4) Semantic hit is **not** bit-identical — policy risk. (5) `xxhash` is a **security** decision, not a perf default, for multi-tenant.

### 2.2 Routing — five mechanisms, two planes

| Mechanism | Decision time | Extra model calls | Published shape |
| --- | --- | --- | --- |
| **Preference / complexity** (RouteLLM) | Before any LLM | 0 (tiny classifier) | **>2×** cost cut vs always-strong; MT Bench CPT(50%) ≈ **37%** GPT-4 calls, score 8.8 vs 9.3 (**95%**); up to **75%** cost vs random on *their* table |
| **Cascade** (FrugalGPT) | After cheap answer fails a judger | 1..k | Match GPT-4 with up to **98%** cost cut, or **+4%** accuracy at same cost, on *their* classification/QA — **will not** transfer to open-ended legal |
| **Cascade routing** (De Koninck et al., ICML 2025) | Hybrid | Variable | Quality estimator is the bottleneck |
| **Fallback / HA** (LiteLLM) | On error | Retries | `order=1→2`; 429 → `cooldown_time`; separate content-policy / context-window fallbacks |
| **LoRA multiplex** | Per-request adapter id | 0 | Punica SGMV: **12×** throughput, **~2 ms**/tok extra. S-LoRA: **thousands** of adapters; up to **4×** vs naive vLLM LoRA, **30×** vs PEFT; 2,000 adapters / GPU in their Llama-7B setup |

**Cache-aware routing** is not quality routing. Dynamo: cost = \(f(\mathrm{load}, \mathrm{overlap})\); `overlap_credit_blocks` weights device/host/shared; `router_temperature` softmax-samples to avoid **herding** onto the cache-rich pod. llm-d: `kvevents.Pool` → block index (hash → pod + GPU/CPU) → `kvcache.Index` → Precise Prefix-Cache Scorer. Mooncake prefill scheduler: chained block hashes, max `prefix_len` under load. OpenAI `prompt_cache_key` is hosted affinity.

**Geo ≠ affinity.** Residency pins the *region*. Cache locality wants the *replica that holds the prefix*. EU user + US-warm cache = miss **or** residency violation. Pattern: regional KV indexes + regional pools; replicate **only** non-PII system-prompt blocks if policy allows.

**Complexity.** Preference router: \(\Theta(1)\) extra (classifier). Cascade worst case: \(\Theta(k)\) serial LLM calls; p95 **adds** tails unless you hedge (which doubles $). Affinity scoring: \(\Theta(W \cdot P)\) overlap against \(W\) workers’ prefix indexes — keep this on the **control** plane, not in the client. LoRA: SGMV batches heterogeneous adapters in one forward; adapter id **must** be in `extra_hash` or tenant A rides tenant B’s prefix.

**Under-routing** (hard query → weak model): silent quality drop, no 4xx. **Over-routing:** cost returns to always-strong. Mitigations: shadow-route a % to strong and measure PGR; escalate on uncertainty / tool-failure / policy; **pin `model`** for regulated workflows — routers are for **Tier-B**.

### 2.3 Batching and schedulers

**Orca (Yu et al., OSDI 2022)** — iteration-level scheduling (“continuous batching”): after each forward, finished sequences leave, waiting enter. Selective batching: linear ops across ragged lengths; attention per-sequence. GPT-3 175B: **36.9×** throughput vs FasterTransformer at equal latency.

**vLLM v1 queues:** `waiting` (not prefilled) → `running` (decoding) → `swapped` (KV spilled to CPU). After each iteration: free finished blocks → maybe swap → admit waiting under `max_num_batched_tokens` / KV headroom. Prefix caching **on** by default; `--no-enable-prefix-caching` to disable. Swap is a **degraded mode**, not capacity.

**Sarathi-Serve chunked / stall-free prefill.** Cap tokens per iteration: pack all running **decodes first**, leftover budget as a prefill **chunk** (chunk size multiple of KV block size except last). Removes the generation stall. TRT-LLM **IFB** is the NVIDIA name (packed, no pad, optional context chunking). Starting knobs (not SLOs): `max_num_tokens` often 8,192–16,384; `free_gpu_memory_fraction` default 0.9, back off to 0.7–0.8 on OOM; `enable_chunked_prefill`; `enable_block_reuse`.

**SGLang:** waiting + running; FCFS / LPM / DFS-weight; `--chunked-prefill-size` (tune down to 4,096/2,048 on prefill OOM); `--mem-fraction-static` ~0.9. Radix match runs **before** priority so LPM packs cache-friendly requests.

**FlashAttention is a kernel, not a scheduler.** FA1: IO-aware tiling, linear HBM vs quadratic materialization. FA2: **~2×** vs FA1, 50–73% A100 peak. FA3 (Hopper): **1.5–2.0×** vs FA2 FP16, up to **740 TFLOP/s (75% H100)**; FP8 ~**1.2 PFLOP/s**, **2.6×** lower numerical error than naive FP8 attention. Decode needs a **paged** FA variant (FlashInfer / TRT-LLM FMHA) because K/V are non-contiguous blocks.

**Batcher state machine**

```
 WAITING --admit (token cap ∧ KV headroom)--> PREFILL_CHUNK --complete--> DECODE
    ▲                                              │                         │
    │         naive hybrid: long prefill           │                         │ finish
    │         stalls every in-flight decode        ▼                         ▼
    │                                         RUNNING mix              FINISHED
    │                                              │                  (free blocks)
    └──── KV pressure: swap to CPU  SWAPPED ◀──────┘
```

**Complexity / invariants.** Per iteration work \(\Theta(B_{\mathrm{tok}})\) at the scheduler (admit/evict/pack), kernels \(\Theta(B_{\mathrm{tok}} \cdot d \cdot L)\) plus attention as above. **Invariant:** decode tokens consume the budget **before** prefill chunks. **Invariant:** do not raise `max_batch_size` to fix p99 ITL — that **worsens** ITL. **Invariant:** `max_model_len × concurrency × KV_bytes` must fit; spec decode **doubles** KV. Goodput (DistServe): max QPS with **both** TTFT and TPOT SLOs met for **>90%** — throughput without this is vanity.

**When not to disaggregate.** Short prompts + high QPS; single-node <8 GPUs → colocation + chunked prefill. DistServe: **7.4×** more requests *or* **12.6×** tighter SLO vs colocated SOTA (dual-SLO goodput). Splitwise: heterogeneous pools (H100 prefill / A100 decode), **1.4×** throughput and **~20%** lower cost in *their* setting. ⚠️ Multipliers are not interchangeable. Dynamo marketing: **~2×** TTFT from KV-aware routing. Mooncake (FAST 2025): thousands of nodes, **>100B tok/day**; A800/H800 **+115% / +107%** requests vs prior; simulated long-context up to **+525%** under SLO.

### 2.4 Quantization — weights, activations, KV are independent

| Recipe | What shrinks | Hardware | Role / published shape |
| --- | --- | --- | --- |
| **FP8** E4M3/E5M2 | W and/or A and/or KV, **2×** vs BF16 | Hopper/Blackwell TE | 2026 default on modern NVIDIA |
| **INT8 SmoothQuant** | W8A8; migrate act. outliers into W | Ampere+ INT8 | When FP8 unavailable |
| **W4A16 GPTQ** | Weights 4-bit; 2nd-order compensation | Dequant to FP16 on GEMM | Offline PTQ; **calibration-set sensitive** |
| **W4A16 AWQ** | 4-bit W; protect salient channels via **activations** | Same | Better generalization than GPTQ at 4/3-bit; TinyChat **3.2–3.3×** vs HF FP16 on *their* GPUs |
| **FP8 KV** | KV **2×** | Hopper+ | TRT-LLM: **+6%** E2E throughput at *same* concurrency + higher max conc.; GSM8K “no meaningful drop” in *their* table |
| **KIVI 2-bit KV** | KV ~**4×** vs FP16; residual FP window | CUDA | Per-channel K, per-token V; **2.6×** peak mem (incl. W) Llama-2-7B; up to **4×** batch, **2.35–3.47×** throughput |
| **QServe W4A8KV4** | All three | A100 / L40S | vs TRT-LLM: Llama-3-8B **1.2×** A100 / **1.4×** L40S; Qwen1.5-72B **2.4×** / **3.5×**; claimed **~3×** $ on L40S vs A100+TRT-LLM |
| **NVFP4 / MXFP4** | W and KV | Blackwell sm100/103 | TRT-LLM matrix; DeepSeek-R1 NVFP4 + FP8 KV |

**Quality collapse modes (papers, not a license to skip eval).** GPTQ overfit to calibration domain. W4A16 helps **memory-bound small batches**; at large continuous-batch decode, INT4 dequant can **lose** to FP8 Tensor Cores — TRT-LLM: FP8 first on Hopper/Blackwell; INT4 for memory-constrained / **bs≤4**. KV INT2/INT4 without KIVI residual or QServe SmoothAttention: long-context perplexity and retrieval-in-context fail first. Naive FP8 attention vs FA3: **2.6×** more numerical error. Mixing KV dtype across P/D without convert-on-transfer: garbage decode.

**Invariants.** (1) A quantized checkpoint is a **new model version** — gate on *your* agent + safety eval, not just MMLU. (2) `quant_scheme` + `spec_method` in the serving revision / audit tuple. (3) Weight/KV/activation dtype change **bumps cache generation** or you serve garbage attention. (4) MLA + FP8 KV: cache latent in FP8, attention as absorbed MQA in FP8 (TRT-LLM DeepSeek R1 / Blackwell).

---

## 3. Token Economics & NFR Analysis

Two meters: **provider tokens** (including cache read/write) and **GPU-seconds** (self-host). They do not convert without *your* tok/s × *your* SKU. Interview failure: quoting a paper’s 7.4× as a cost SLO.

### 3.1 `$ per 1k runs` — official SKUs + named mix

**Anthropic, $/MTok (docs table, 2026-08-21):**

| Model | Base in | 5m write | 1h write | Read | Out |
| --- | --- | --- | --- | --- | --- |
| Opus 4.6 / 4.7 / 4.8 / 5 | $5 | $6.25 | $10 | $0.50 | $25 |
| Sonnet 4.6 / 4.5 | $3 | $3.75 | $6 | $0.30 | $15 |
| Sonnet 5 | $2 | $2.50 | $4 | $0.20 | $10 |
| Haiku 4.5 | $1 | $1.25 | $2 | $0.10 | $5 |
| Fable 5 / Mythos 5 | $10 | $12.50 | $20 | $1 | $50 |

**OpenAI GPT-5.6 short-context:** cached in **0.1×**, writes **1.25×**. Table examples: `gpt-5.6-sol` $5 / $0.50 / $6.25 / $30; `gpt-5.6-terra` $2 / $0.20 / $2.50 / $12; `gpt-5.6-luna` $0.20 / $0.02 / $0.25 / $1.20 (in / cached / writes / out). Long-context columns are **2×** input — use only when the request crosses the threshold. Regional processing: **+10%** for eligible models on/after 2026-03-05.

**Gemini:** implicit/explicit reads typically **0.1×** on 2.5+; explicit storage **$1.00 per 1M cached tokens per hour** on several paid SKUs (confirm live row).

**Worked example [inferred] — assumptions on the slide:** 1,000 sequential turns; stable prefix **8,000** tok; variable suffix **500** in; **400** out; Anthropic **5m** cache stays warm (TTL refresh); first call is a 5m write, rest reads; no batch discount; **Sonnet 4.6** SKUs above.

| Mode | Formula | $ / 1k |
| --- | --- | --- |
| **No cache** | \(1000 \times (8500 \times 3 + 400 \times 15) / 10^6\) | **$31.50** |
| **Prompt cache 5m** | \(1 \times (8000\times 3.75 + 500\times 3 + 400\times 15)/10^6 + 999 \times (8000\times 0.30 + 500\times 3 + 400\times 15)/10^6\) | **$9.93** |
| **Always-Haiku 4.5, no cache** | \(1000 \times (8500\times 1 + 400\times 5)/10^6\) | **$10.50** |
| **Route 70% Haiku / 30% Sonnet, no cache [inferred]** | \(0.7\times 10.50 + 0.3\times 31.50\) | **$16.80** |
| **Same mix + Sonnet prefix cache on the 30%** | ⚠️ path-dependent; do not quote without a trace | — |

Same shape on `gpt-5.6-terra` short-context: uncached **$21.80** / 1k; warm cache **~$7.42** / 1k **[inferred]** from $2 / $0.20 / $2.50 / $12. Output still dominates long generations: 2,000-tok answer at $15/MTok = **$0.030**/call — cache cannot touch it.

**Punchline:** cache is an *input* lever; routing / quantization / spec decode are the *output-time* levers. 5m cache here is a **68%** cut ($31.50 → $9.93) **only** if the 8k prefix actually hits. Instrument `cache_read / input` daily.

**Self-host $/1k:** ⚠️ do not invent GPU-hour prices. Translate papers into **capacity**: PagedAttention **2–4×** seqs/GPU; KIVI up to **4×** batch; QServe **1.2–3.5×** tok/s vs TRT-LLM on named GPUs; Punica **12×** when the workload is many LoRAs. Multiply *your* measured tok/s by *your* cloud GPU SKU.

| Lever | Token $ | GPU $ | Latency | Quality |
| --- | --- | --- | --- | --- |
| Prefix / prompt cache | In 0.1× on hits; writes 1.25× (OpenAI 5.6+, Anthropic 5m) | Less prefill FLOPs | TTFT **p50** ↓ | Bit-identical if exact |
| Semantic cache | **0** model tokens on hit | Embed + kNN | Tens of ms | **Not** identical |
| Cascade | Pays 1..k models | — | p95 ↑ (serial) | Can *beat* expensive model (FrugalGPT +4% on *their* sets) |
| Preference router | Mix of cheap/dear | — | ~unchanged | Bounded by CPT/PGR |
| Continuous batching | — | More tok/s/GPU | p50 TTFT may ↑ at high load | Same |
| Chunked prefill | — | Slight tok/s trade | ITL p99 ↓ | Same |
| P/D disagg | — | Better goodput/$ | Dual SLO | Same |
| FP8 W/KV | — | ~2× density | Decode ↑ | Small; measure |
| INT4 AWQ | — | ~4× W density; win at **small batch** | Big at bs≤4; shrinks at large bs | Watch reasoning / long-ctx |
| Spec decode | Same out tokens | More FLOPs, fewer steps | ITL ↓ if α high | Lossless if reject-sampled |
| LoRA multiplex | Same | 1 base + N adapters | +~2 ms/tok (Punica) | Adapter quality |

### 3.2 Latency — p50 / p95 / p99 (label **[inferred]**)

⚠️ **Vendor APIs do not publish TTFT/ITL percentile SLOs.** Bound from mechanics; measure. Prefix/prompt-cache hits move **p50** more than **p99** (p99 is still a miss + queue). DistServe goodput: **>90%** of requests inside **both** TTFT and TPOT SLOs — that is the SLO shape, not a vendor p99.

| Percentile | TTFT (prefill + queue) | ITL / TBT (decode) | E2E |
| --- | --- | --- | --- |
| **p50** | **[inferred]** dominated by prefix **hit** path + small queue; Dynamo KV-aware routing claims **~2×** TTFT when overlap is high; KVGov cold/cached ratio **0.22** on a named 7B/A100 setup | Decode-only iteration; FA/paged FMHA | First token ≈ TTFT on stream |
| **p95** | **[inferred]** miss + admission queue; 1.5–3× p50 is a common *working* envelope on mixed hit/miss, not a contract | Chunked prefill bounds stall vs naive hybrid (**28.3×** TBT in Sarathi) | Agent loops: Anthropic 5m clock starts at **request start** — a 4 min stream leaves ~1 min for turn 2 |
| **p99** | **[inferred]** cache stampede, region failover (**cold** cache), P-pool saturation, index lag (silent miss), OOM swap | Prefill still in the decode batch, or D-pool KV exhaustion | Tool timeout + TTL expiry mid-session; HITL |

| Tier | Mitigations |
| --- | --- |
| p50 | Stable prefix ≥ min cache tokens **above** breakpoint; `prompt_cache_key=tenant:promptver`; sticky affinity; FP8 on Hopper+; streaming |
| p95 | Continuous batching + **chunked prefill**; LPM admission; `router_temperature` against herding; keep ~15 RPM per OpenAI cache key (shard keys); explicit breakpoints (OpenAI 5.6 implicit trap) |
| p99 | Single-flight on `prefix_hash` (stampede); disagg when both SLOs bind (DistServe/Dynamo/Mooncake); admit-shed at KV util; spec only if α≥2 on *this* workload (vLLM 2.8× is an upper bound, not a forecast); **do not** raise batch size first — that worsens ITL; geo failover **budgets cold TTFT** |

Track TBT/ITL histograms **separately** from TTFT. Goodput > raw tok/s.

### 3.3 Throughput and back-pressure

\[
\mathrm{throughput} = \min(\mathrm{RPM/TPM},\ \mathrm{prefill\ FLOPs},\ \mathrm{decode\ HBM},\ \mathrm{KV\ blocks},\ \mathrm{NIXL\ BW},\ \mathrm{admit\ watermark})
\]

Hosted: OpenAI whichever of RPM/TPM/RPD exhausts first; headers `x-ratelimit-*` plus **project** token limits. 429 → honor `Retry-After`. Limits are **tier × model** — do not freeze a blog’s GPT-4o table. ⚠️ Confirm on the live guide whether cached tokens count toward TPM; do not assume exclusion. Prefix cache **increases** GPU tokens/wall-clock and **decreases** billed input on hosted APIs. Routers that retry 429 without jitter amplify stampedes. LiteLLM: rate-limit-aware / lowest-cost strategies; `simple-shuffle` is their production default.

Self-host: at ~100% KV pool you **swap or reject**. Mooncake **prediction-based early rejection** under extreme load is correct; unbounded queues destroy p99 for everyone. `gpu_memory_utilization` / `mem-fraction-static` too high + `max_model_len` × concurrency × KV-bytes + spec doubling = SIGKILL. Back off `free_gpu_memory_fraction` to 0.7–0.8 on OOM; never “retry OOM” without shrinking `max_tokens`.

**Back-pressure protocol:**

1. Gateway admits iff breaker ∈ {closed, half-open} **and** tenant token bucket has room **and** KV util < watermark (research working number: reject when KV util > **85%** — pick a *measured* number).
2. Surface saturation as **429 Retry-After**, not 500. Scale **x** in xPyD on P saturation (TTFT); scale **y** and admit fewer prefills on D saturation (ITL, KV util).
3. Single-flight / coalesce on `prefix_hash` so one writer’s pod gets the herd (Anthropic: parallel requests cannot hit until first response **starts**; OpenAI 5.6: many **1.25×** writes of the same prefix).
4. User path can fail-open to a cheaper model; effectful tools **fail-closed** without WORM. Semantic-cache embedder 5xx: fail-open to LLM **or** fail-closed — explicit.
5. Cascade **is** a retry with a different model — count it in the error budget or you hide outages as “quality variance.”

### 3.4 Availability, RPO/RTO, compliance, explicit NFR trade-offs

⚠️ Research publishes no numeric RPO/RTO for hosted prompt caches or Dynamo indexes. Architecture mapping:

| NFR | Working target | Tension |
| --- | --- | --- |
| Availability | Gateway 99.9%; engine 5xx → cooldown + `order+1`; KV exhaustion → 429 not 500; semantic fail-open vs fail-closed is a **chosen** policy | Multi-vendor fallback ⇒ output-distribution drift; cascade spends p95 |
| RPO | App/tool: **0** (outbox before effect). Action audit: **0** (WORM). KV/prefix: **minutes–hours**, best-effort (pod death = miss unless LMCache/Mooncake). Hosted prompt cache: not customer-exportable | Treating KV as RPO=0 over-provisions HBM; Gemini explicit cache is a **retained object** |
| RTO | Interactive: fail over <1 s to secondary **model** (cold prefix). Decode replica death without offload: **restart prefill** (TTFT spike). With disagg + published KV: new D worker pulls | Fast failover vs identical tokens (temp>0) |
| Consistency | APC **eventually consistent** across pods until a global index; two cold GPUs both prefill. Weights/quant/FA kernel change ⇒ **all KV invalid** (extra hashes / cache generation) | Sticky affinity ↑ hit, ↓ spread |
| Compliance | KV = **data in use**, same category as the prompt. Regional indexes; no cross-region prefix. Anthropic: KV+hashes in memory, ZDR-eligible. OpenAI in-memory vs 24 h = **DPA checkbox**. Offload to S3: CMK, path per tenant, TTL=lifecycle | Isolation vs hit rate (salts cheaper than clusters; clusters for hostile multi-tenant) |
| Cost vs quality | $31.50 → $9.93 on the named 5m mix; RouteLLM 37% strong calls on *their* bench; FrugalGPT 98% on *their* sets | Semantic/INT4/complexity router forbidden if quality must be bit-identical |
| Latency vs $ | Chunking = poor man’s disagg; disagg when **both** SLOs bind; INT4 at large bs can **lose** | Paper 12× is the **shape** of the curve you will measure |

**Explicit trade-offs.**

| Dimension | Cheap / fast | Balanced | Strict / regulated |
| --- | --- | --- | --- |
| Cache | Semantic on; xxhash; global prefix | Exact APC + HMAC salt; hosted 5m; sha256_cbor if index is global | Disable cross-tenant APC; no semantic; in-memory TTL only |
| Route | Always-cheap; cascade on UX path | HA fallbacks; RouteLLM on Tier-B; pin model on Tier-A | No cost router on PHI; geo pin |
| Batch | Huge `max_num_tokens`; colocate | Chunked prefill; KV-util admit | Disagg in-region; reject not swap |
| Quant | INT4 everywhere | FP8 W+KV after eval; INT4 only bs≤4 / memory wall | FP8 or BF16; new version gated |
| Audit | stdout | `cached_tokens` + prefix **hash** | WORM tuple §4.4; no KV dumps |

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution — Temporal / Kafka

KV is **not** Redis until you add a hierarchy. Single replica: lose the pod, lose the prefix. LMCache: GPU working set → pinned CPU → local NVMe → remote (Redis, Mooncake Store, InfiniStore, S3, NIXL). **MP daemon** so KV survives engine crash (no fate-sharing). Dynamo KVBM: host / local / object, “petabyte” class in the launch blog. Mooncake Store: hash-indexed distributed KV, RDMA. llm-d / Dynamo indexes are **event-sourced** from vLLM `KVEvents`.

**Temporal.** Workflow id = `tenant:agent:thread_id`. Activities = (PII/policy + salt), (admission / KV-util check), (LLM complete **recording** `ModelTurn` + `cached_tokens` + `quant_scheme`), (NIXL publish is **not** an activity success until decode ack or object commit), (MCP `tools/call`), (WORM append). Replay reconstructs **control** state; activities are idempotent and **must not re-sample** the model. Continue-As-New at history bounds. Compensating action = new turn, not overwrite WORM. Prefill/decode pool resize (xPyD) is a **control-plane** decision, not a workflow side effect inside replay.

**Kafka.** Topics: `kvevents.blocks` (hash → pod + medium) for the global prefix index; `agent.turns` / `agent.dlq` for app. Produce **intent** (tool_call + idempotency key) **before** the side effect (outbox). Poison (unparseable, repeated crash) → DLQ; do not block the partition. Online path: user request **does not** wait for index catch-up (lagging index ⇒ silent miss — correct, slower); it **does** wait for WORM if the tool is effectful. NIXL transfer is not a commit: prefill death mid-transfer ⇒ decode retries prefill or reads durable KV.

> ⚠️ Gap: research has no Temporal replay-cost numbers for multi-MB KV indexes and no Kafka lag SLO for `KVEvents`. Treat Temporal as durable **app** execution; treat Kafka as the published event bus for the **control-plane index**, not as a KV store.

**Resume keys.** `trace_id`. `thread_id`. `prefix_hash` + `cache_generation`. `disaggregated_params` / remote block ids. `prompt_cache_key`. None substitutes for the others.

### 4.2 Failure taxonomy

| Class | Symptom | Handler |
| --- | --- | --- |
| Transient | 429 TPM/RPM; engine 5xx; NIXL blip; Kafka lag; embedder 5xx | Full-jitter retry on **idempotent** complete(); honor `Retry-After`; cooldown **that** deployment; do not retry the same cache-write key immediately; semantic fail-open/closed as chosen |
| Permanent | 400 illegal payload; content-policy; context-window exceeded; quant checkpoint failed eval | Fail the turn; `context_window_fallbacks` / `content_policy_fallbacks` if configured; do not retry OOM without shrinking `max_tokens` |
| Poison pill | Same prefix crashes the engine; spec rollback (dup/missing tokens, KV desync); hash algo change after upgrade → 0% hits; KV dtype mismatch P/D | Disable spec; pin engine; `sha256_cbor` + bump generation; convert on transfer; DLQ the payload hash |
| Semantic | Silent prefix miss (timestamp in system prompt, tool JSON key order, image bytes, ChatML vs Harmony, gateway injects request id **before** breakpoint); RouteLLM under-route; FrugalGPT judger accepts fluent wrong; semantic false-hit FAQ; herding to cache-rich pod | Alert `cached_tokens==0` above min length; write/read ratio; shadow-route PGR; raise kNN threshold + tenant tags; `router_temperature` + load term |

| Failure | Detection | Action | Cache consequence |
| --- | --- | --- | --- |
| Engine 5xx / OOM | Health + `allowed_fails` | Cooldown; k8s restart | Prefix on that GPU **gone** unless LMCache/Mooncake |
| 429 TPM | Header | Cooldown deployment; `order+1` | Do not stampede the same key |
| P pool saturation | Queue depth, TTFT | Scale x; new prefills **cold** | |
| D pool saturation | ITL, KV util | Scale y; admit fewer prefills | Back-pressure P |
| Region outage | DNS / mesh | Geo failover | **Cold cache**; budget TTFT |
| Router view stale | Index lag | Fallback on error | Silent miss (correct, slower) or dead pod + HA |
| Swap storm | `swapped` queue | Shed load | Latency cliff — degraded mode |

**Cache stampede.** Spike/deploy → all prefixes miss → every replica prefills the 8k system prompt → TTFT p99 explodes → retries worsen; hosted: many 1.25× writes; Anthropic parallel cannot hit until first response starts. Mitigate: single-flight, affinity to the one writer, pre-warm, jitter, explicit breakpoints.

### 4.3 Circuit breaker and fallbacks

Per **deployment** (model×region×quant revision), not per cluster:

- **Closed:** traffic flows; consecutive failures or error-rate window, or KV-util/α circuits, trip to open.
- **Open:** fail fast; start recovery timer. Interactive → fallback chain. Flex/Batch can wait. User chat **fail-open** to secondary model; **effectful tool fail-closed** without WORM.
- **Half-open:** one probe (`half_open_max`). Success → closed; fail → open.

Published, not folklore:

1. LiteLLM per-deployment cooldown after N failures; **429 is a first-class cooldown trigger**.
2. Token-budget / KV-util circuit: stop admitting prefills when blocks cannot allocate (Sarathi/vLLM already refuse) — surface **429 Retry-After**.
3. Semantic-cache circuit: embedder 5xx → fail-open (avail) or fail-closed (cost).
4. Spec-decode circuit: moving-average α below threshold → disable spec (it steals batch token budget).
5. Collector/engine memory: `free_gpu_memory_fraction` / `--mem-fraction-static`.

**Fallback chain:** primary (Sonnet / Terra / FP8 replica) → secondary (other vendor or Haiku / Luna / different region — **cold cache**) → **deterministic degrade** (FAQ template / “degraded: cannot complete”, still schema-valid). Do not fall back from exact cache to semantic “so we stay cheap.” Do not fall back from salted APC to unsalted global prefix. Do not fall back from FP8-eval’d checkpoint to an INT4 file that skipped agent eval. Cascade **counts** as a fallback hop in the error budget.

Retries: exponential backoff + **full jitter**; cap `max_fallbacks`. ⚠️ No vendor publishes breaker trips/hour as an SLO.

### 4.4 Zero-Trust MCP, tool RBAC, PII, immutable logs, tenant cache isolation

MCP `tools/call` is a **privileged side channel into the same GPU** that holds other tenants’ KV. The gateway (not the engine):

1. Authenticate MCP client (OAuth 2.1 / SPIFFE); bind `tenant_id`.
2. Authorize tool name + args **before** tokens hit the model (prompt injection via tool results is an inference-time incident).
3. Inject `cache_salt = HMAC(server_secret, tenant_id)` — never client-supplied.
4. Propagate W3C `traceparent` (MCP SEP-414 `_meta`) so tool span and LLM span share `trace_id`.
5. Rate-limit **per tenant × model**, not per cluster.

Zero-Trust: the GPU pool does not trust the prompt, the tool host, or the router score. Each hop presents identity; KV namespaces are cryptographically disjoint.

**Tool RBAC.** Allowlist `execute_tool {name}` per turn. Adapter ACL for LoRA multiplex — do not load tenant B’s adapter into tenant A’s batch without an isolation story. S-LoRA batches heterogeneous adapters; side channels less studied than prefix cache — extra_hash + gateway authz anyway.

**PII pipeline:** detect → redact **before tokenize and before cache key** → audit placeholder (HMAC id, never raw). Residual: KV tensors are lossy-but-invertible-enough forms of the prompt — **data in use**. Do not put account numbers in the first *n* tokens of a shared prefix (timing infers them even when logits never leave). Logs: `cached_tokens` OK; dumping KV dumps PII; span attrs store **hashes of prefixes**, not prefixes. Offload (Dynamo NIXL Azure Blob): encryption at rest, CMK, path per tenant, lifecycle = TTL.

**Tenant isolation of caches**

| Cache | Isolation primitive | Residual risk |
| --- | --- | --- |
| vLLM / SGLang prefix | `cache_salt` / extra_key; or disable APC | Root-salted: lose sharing of global system prompts. KVGov **boundary salt** (inject at first PII block) estimated **93%** of prefix benefit retained — research, not a vLLM flag yet |
| OpenAI prompt cache | Organization + `prompt_cache_key` | Same-org multi-tenant apps **share** unless you partition keys |
| Anthropic | Org; workspace on some platforms | Map workspaces 1:1 to tenants |
| Gemini explicit | Project / cache resource IAM | IAM misbind = cross-app CachedContent |
| Semantic (Redis) | TAG `tenant` in the **same** KNN query | App-side filter after kNN is a classic bypass |
| LMCache / Mooncake remote | Key prefix + ACL | Global hash of tokens without tenant = **cross-tenant KV oracle** |
| LoRA multiplex | Adapter ACL; id in block extra_hash | Wrong adapter in batch |

Prefer `sha256_cbor` if hashes leave the box (Dynamo/llm-d global index).

**Immutable audit tuple per request:** `tenant_id`, `model`, `adapter_id`, `cache_salt_id` (not the secret), `prefix_hash`, `cached_tokens` / `cache_write_tokens`, `router_choice`, `fallback_reason`, `kv_pod_id`, `quant_scheme`, `spec_method`, `trace_id`. Semantic hits need `source_request_id` of the stored answer (defamation/PII takedown). Cache hits must be auditable or you cannot explain a residency miss (served from the wrong region’s warm cache). Hash-chained WORM; not sampling-eligible.

---

## 5. Production Enterprise Code

Stdlib-only inference gateway: HMAC tenant salt + sha256 block hashes (not xxhash), prefix + tagged semantic cache, cache-generation bump on quant change, cache-aware router with load term + temperature, cascade + HA fallback, Sarathi-style batch scheduler (decode first, prefill chunk, waiting/running/swapped), KV-util admission, single-flight stampede coalesce, full-jitter retries, circuit breaker closed→open→half-open, correlation-id JSON logs, PII detect→redact→audit, hash-chained WORM, graceful deterministic degrade. Run: `python inf_opt_gateway.py`.

```python
#!/usr/bin/env python3
"""Inference optimization gateway (stdlib only). Run: python inf_opt_gateway.py"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import random
import re
import threading
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

POLICY_VERSION = "infopt-2026-08-21"
BREAKER_FAILURES = 3
BREAKER_RECOVERY_S = 0.05
MAX_BATCHED_TOKENS = 8_192
KV_UTIL_ADMIT = 0.85
SEMANTIC_THRESHOLD = 0.92
BLOCK_SIZE = 16
SALT_SECRET = b"demo-server-secret-not-from-client"
PII_HMAC_KEY = "infopt-hmac-demo"


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "trace_id": getattr(record, "trace_id", None),
            "breaker": getattr(record, "breaker", None),
            "degraded": getattr(record, "degraded", None),
            "cache": getattr(record, "cache", None),
            "router": getattr(record, "router", None),
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
    base = logging.getLogger("infopt.gateway")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    return CorrelationAdapter(
        base, {"correlation_id": correlation_id, "tenant": tenant, "trace_id": correlation_id}
    )


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class CircuitOpenError(Exception):
    pass


class ShedLoad(TransientError):
    pass


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, failures: int = BREAKER_FAILURES, recovery_s: float = BREAKER_RECOVERY_S):
        self.failures = failures
        self.recovery_s = recovery_s
        self._state = BreakerState.CLOSED
        self._n = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> BreakerState:
        with self._lock:
            if self._state is BreakerState.OPEN and (time.monotonic() - self._opened_at) >= self.recovery_s:
                self._state = BreakerState.HALF_OPEN
            return self._state

    def allow(self) -> None:
        if self.state is BreakerState.OPEN:
            raise CircuitOpenError("circuit open")

    def record_success(self) -> None:
        with self._lock:
            self._n = 0
            self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._n += 1
            if self._state is BreakerState.HALF_OPEN or self._n >= self.failures:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()


def full_jitter_sleep(attempt: int, base: float = 0.01, cap: float = 0.05, rng: random.Random | None = None) -> float:
    r = rng or random
    return r.uniform(0.0, min(cap, base * (2 ** attempt)))


def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 4,
    breaker: CircuitBreaker,
    rng: random.Random,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    breaker.allow()
    last: Exception | None = None
    for i in range(attempts):
        try:
            out = fn()
            breaker.record_success()
            return out
        except CircuitOpenError:
            raise
        except PermanentError:
            breaker.record_failure()
            raise
        except TransientError as exc:
            last = exc
            breaker.record_failure()
            if i == attempts - 1 or breaker.state is BreakerState.OPEN:
                break
            sleep(full_jitter_sleep(i, rng=rng))
            try:
                breaker.allow()
            except CircuitOpenError:
                break
    if last:
        raise last
    raise TransientError("retry exhausted")


_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("KEY", re.compile(r"\b(?:sk|rk)-[A-Za-z0-9]{8,}\b")),
    ("BEARER", re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.I)),
    ("ACCT", re.compile(r"\b(?:acct|account)[:\s#-]*\d{6,}\b", re.I)),
)


def hmac_id(value: str, key: str = PII_HMAC_KEY) -> str:
    return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()[:16]


def detect_redact(text: str) -> tuple[str, list[dict[str, str]]]:
    found: list[dict[str, str]] = []
    out = text
    for label, pat in _PII_PATTERNS:
        for m in pat.finditer(out):
            found.append({"type": label, "id": hmac_id(m.group(0))})
        out = pat.sub(f"[REDACTED:{label}]", out)
    return out, found


@dataclass
class WormLog:
    rows: list[dict[str, Any]] = field(default_factory=list)

    def append(self, record: dict[str, Any]) -> str:
        prev = self.rows[-1]["hash"] if self.rows else "genesis"
        body = json.dumps(record, sort_keys=True, default=str)
        digest = hashlib.sha256((prev + body).encode()).hexdigest()
        row = dict(record)
        row["prev"] = prev
        row["hash"] = digest
        self.rows.append(row)
        return digest


def cache_salt(tenant_id: str) -> str:
    return hmac.new(SALT_SECRET, tenant_id.encode(), hashlib.sha256).hexdigest()


def block_hash(parent: str, tokens: tuple[str, ...], extra: tuple[str, ...]) -> str:
    h = hashlib.sha256()
    h.update(parent.encode())
    h.update(json.dumps(tokens, separators=(",", ":")).encode())
    h.update(json.dumps(extra, separators=(",", ":")).encode())
    return h.hexdigest()


def prefix_blocks(salt: str, prefix: str, extra: tuple[str, ...]) -> list[str]:
    toks = tuple(prefix.split())
    hashes: list[str] = []
    parent = salt
    for i in range(0, max(len(toks), 1), BLOCK_SIZE):
        chunk = toks[i : i + BLOCK_SIZE] or ("",)
        parent = block_hash(parent, chunk, extra if i == 0 else extra)
        hashes.append(parent)
    return hashes


def embed(text: str, dim: int = 32) -> tuple[float, ...]:
    vec = [0.0] * dim
    padded = f"  {text.lower()}  "
    for i in range(len(padded) - 2):
        gram = padded[i : i + 3]
        vec[int(hashlib.sha256(gram.encode()).hexdigest(), 16) % dim] += 1.0
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return tuple(x / n for x in vec)


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b))


class PrefixCache:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._lock = threading.Lock()

    def lookup(self, blocks: list[str]) -> int:
        with self._lock:
            hit = 0
            for b in blocks:
                if b in self._store:
                    hit += 1
                else:
                    break
            return hit

    def write(self, blocks: list[str], tenant: str) -> None:
        with self._lock:
            for b in blocks:
                self._store[b] = tenant


class SemanticCache:
    def __init__(self, threshold: float = SEMANTIC_THRESHOLD) -> None:
        self.threshold = threshold
        self._rows: list[tuple[str, str, str, tuple[float, ...], str]] = []
        self._lock = threading.Lock()

    def get(self, tenant: str, model_ver: str, query: str) -> str | None:
        q = embed(query)
        with self._lock:
            best = -1.0
            ans: str | None = None
            for t, mv, _src, vec, text in self._rows:
                if t != tenant or mv != model_ver:
                    continue
                s = cosine(q, vec)
                if s > best:
                    best, ans = s, text
            return ans if best >= self.threshold else None

    def put(self, tenant: str, model_ver: str, source_id: str, query: str, answer: str) -> None:
        with self._lock:
            self._rows.append((tenant, model_ver, source_id, embed(query), answer))


class SingleFlight:
    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._guard = threading.Lock()

    def lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks[key]


@dataclass
class Worker:
    worker_id: str
    load: float
    kv_util: float
    blocks: set[str]
    quant: str
    alive: bool = True
    region: str = "us"


@dataclass
class RouteDecision:
    worker: Worker
    overlap: int
    reason: str
    model: str


class AffinityRouter:
    def __init__(self, workers: list[Worker], temperature: float = 0.15, rng: random.Random | None = None):
        self.workers = workers
        self.temperature = temperature
        self.rng = rng or random.Random(0)

    def score(self, w: Worker, blocks: list[str]) -> float:
        overlap = 0
        for b in blocks:
            if b in w.blocks:
                overlap += 1
            else:
                break
        frac = overlap / max(len(blocks), 1)
        return frac * 2.0 - w.load - (0.5 if w.kv_util > KV_UTIL_ADMIT else 0.0)

    def pick(self, blocks: list[str], region: str) -> RouteDecision:
        alive = [w for w in self.workers if w.alive and w.region == region]
        if not alive:
            raise TransientError("no alive workers in region")
        scored = [(self.score(w, blocks), w) for w in alive]
        scored.sort(key=lambda x: x[0], reverse=True)
        if self.temperature <= 0:
            best_s, best_w = scored[0]
        else:
            weights = [math.exp(s / self.temperature) for s, _ in scored]
            pick = self.rng.choices(scored, weights=weights, k=1)[0]
            best_s, best_w = pick
        ov = 0
        for b in blocks:
            if b in best_w.blocks:
                ov += 1
            else:
                break
        reason = "affinity" if ov else "load"
        if best_w.kv_util >= KV_UTIL_ADMIT:
            raise ShedLoad("kv util admit")
        return RouteDecision(best_w, ov, reason, "primary")


class SeqState(Enum):
    WAITING = "waiting"
    PREFILL = "prefill"
    DECODE = "decode"
    SWAPPED = "swapped"
    FINISHED = "finished"


@dataclass
class Sequence:
    seq_id: str
    prompt_tokens: int
    prefill_done: int = 0
    max_new: int = 8
    generated: int = 0
    kv_blocks: int = 0
    state: SeqState = SeqState.WAITING


class BatchScheduler:
    """vLLM-v1 / Sarathi stub: decode first, leftover budget = prefill chunk."""

    def __init__(self, max_batched_tokens: int = MAX_BATCHED_TOKENS, kv_blocks_total: int = 4_096):
        self.max_batched_tokens = max_batched_tokens
        self.kv_blocks_total = kv_blocks_total
        self.waiting: OrderedDict[str, Sequence] = OrderedDict()
        self.running: dict[str, Sequence] = {}
        self.swapped: dict[str, Sequence] = {}
        self.kv_used = 0

    @property
    def kv_util(self) -> float:
        return self.kv_used / max(self.kv_blocks_total, 1)

    def submit(self, seq: Sequence) -> None:
        self.waiting[seq.seq_id] = seq

    def iterate(self) -> dict[str, Any]:
        finished: list[str] = []
        for sid, seq in list(self.running.items()):
            if seq.state is SeqState.FINISHED:
                self.kv_used -= seq.kv_blocks
                finished.append(sid)
                del self.running[sid]
        if self.kv_util > 0.95:
            for sid, seq in list(self.running.items()):
                if seq.state is SeqState.DECODE:
                    self.swapped[sid] = seq
                    seq.state = SeqState.SWAPPED
                    del self.running[sid]
                    break
        budget = self.max_batched_tokens
        decode_tok = 0
        for seq in self.running.values():
            if seq.state is SeqState.DECODE and budget > 0:
                seq.generated += 1
                budget -= 1
                decode_tok += 1
                if seq.generated >= seq.max_new:
                    seq.state = SeqState.FINISHED
        prefill_tok = 0
        for seq in list(self.running.values()):
            if seq.state is SeqState.PREFILL and budget > 0:
                chunk = min(budget, seq.prompt_tokens - seq.prefill_done, BLOCK_SIZE)
                if chunk <= 0:
                    continue
                seq.prefill_done += chunk
                seq.kv_blocks += 1
                self.kv_used += 1
                budget -= chunk
                prefill_tok += chunk
                if seq.prefill_done >= seq.prompt_tokens:
                    seq.state = SeqState.DECODE
        admitted = 0
        while self.waiting and budget > 0 and self.kv_util < KV_UTIL_ADMIT:
            sid, seq = self.waiting.popitem(last=False)
            seq.state = SeqState.PREFILL
            self.running[sid] = seq
            admitted += 1
            chunk = min(budget, seq.prompt_tokens, BLOCK_SIZE)
            seq.prefill_done += chunk
            seq.kv_blocks += 1
            self.kv_used += 1
            budget -= chunk
            prefill_tok += chunk
            if seq.prefill_done >= seq.prompt_tokens:
                seq.state = SeqState.DECODE
        return {
            "decode_tok": decode_tok,
            "prefill_tok": prefill_tok,
            "admitted": admitted,
            "finished": finished,
            "kv_util": round(self.kv_util, 4),
            "swapped": list(self.swapped),
        }


@dataclass
class ModelTurn:
    text: str
    model: str
    cached_blocks: int
    output_tokens: int
    degraded: bool = False


class ModelClient:
    def __init__(self, name: str, *, fail: type[Exception] | None = None, answer: str = "ok"):
        self.name = name
        self._fail = fail
        self.answer = answer
        self.calls = 0

    def complete(self, prompt: str) -> ModelTurn:
        self.calls += 1
        if self._fail is not None:
            raise self._fail(f"{self.name} down")
        return ModelTurn(self.answer, self.name, 0, max(len(self.answer.split()), 1))


def cheap_classifier(prompt: str) -> str:
    hard = ("legal", "reason step by step", "diagnose", "cite")
    if len(prompt.split()) > 40 or any(k in prompt.lower() for k in hard):
        return "strong"
    return "cheap"


def judge_ok(text: str) -> bool:
    bad = ("i don't know", "degraded", "as an ai")
    return len(text.strip()) >= 8 and not any(b in text.lower() for b in bad)


class InferenceGateway:
    def __init__(
        self,
        router: AffinityRouter,
        primary: ModelClient,
        secondary: ModelClient,
        breaker: CircuitBreaker,
        *,
        prefix: PrefixCache,
        semantic: SemanticCache,
        batcher: BatchScheduler,
        worm: WormLog,
        rng: random.Random,
        quant: str = "fp8",
        cache_gen: str = "g1",
        semantic_on: bool = False,
        cascade: bool = False,
    ) -> None:
        self.router = router
        self.primary = primary
        self.secondary = secondary
        self.breaker = breaker
        self.prefix = prefix
        self.semantic = semantic
        self.batcher = batcher
        self.worm = worm
        self.rng = rng
        self.quant = quant
        self.cache_gen = cache_gen
        self.semantic_on = semantic_on
        self.cascade = cascade
        self.flight = SingleFlight()

    def extra(self, adapter_id: str) -> tuple[str, ...]:
        return (self.quant, self.cache_gen, adapter_id, POLICY_VERSION)

    def handle(
        self,
        prompt: str,
        *,
        tenant: str,
        prefix_text: str,
        adapter_id: str = "base",
        region: str = "us",
        allow_tools: set[str] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        cid = correlation_id or hashlib.sha256(f"{tenant}:{time.time_ns()}".encode()).hexdigest()[:16]
        log = build_logger(cid, tenant)
        redacted, pii = detect_redact(prompt)
        stable, _ = detect_redact(prefix_text)
        if "acct" in prefix_text.lower() or any(x["type"] == "ACCT" for x in pii):
            log.warning("pii_blocked_from_shared_prefix", extra={"cache": "deny_prefix_pii"})
        salt = cache_salt(tenant)
        extra = self.extra(adapter_id)
        blocks = prefix_blocks(salt, stable, extra)
        suffix = redacted
        if allow_tools is None:
            allow_tools = set()

        if self.semantic_on:
            hit = self.semantic.get(tenant, extra[1], suffix)
            if hit:
                self.worm.append(
                    {
                        "tenant": tenant,
                        "kind": "semantic_hit",
                        "trace_id": cid,
                        "prefix_hash": blocks[0],
                        "cache_salt_id": salt[:12],
                    }
                )
                log.info("semantic_hit", extra={"cache": "semantic"})
                return {"text": hit, "model": "semantic", "degraded": False, "cached_blocks": 0, "pii": pii}

        with self.flight.lock_for(blocks[0] if blocks else tenant):
            try:
                decision = self.router.pick(blocks, region)
            except ShedLoad:
                log.warning("shed_kv", extra={"degraded": True})
                return self._degrade(log, tenant, cid, blocks, salt, pii, "kv_shed")
            except TransientError as exc:
                log.warning("route_fail %s", exc)
                return self._degrade(log, tenant, cid, blocks, salt, pii, "route")

            cached = self.prefix.lookup(blocks)
            seq = Sequence(seq_id=cid, prompt_tokens=max(len(stable.split()) + len(suffix.split()), 1))
            self.batcher.submit(seq)
            sched = self.batcher.iterate()

            def _call(client: ModelClient) -> ModelTurn:
                return retry_call(lambda: client.complete(stable + "\n" + suffix), breaker=self.breaker, rng=self.rng)

            turn: ModelTurn | None = None
            fallback_reason = ""
            try:
                if self.cascade and cheap_classifier(suffix) == "cheap":
                    cheap_turn = _call(self.secondary)
                    if judge_ok(cheap_turn.text):
                        turn = cheap_turn
                    else:
                        fallback_reason = "cascade_judge"
                        turn = _call(self.primary)
                else:
                    turn = _call(self.primary)
            except (TransientError, CircuitOpenError):
                fallback_reason = "ha_secondary"
                try:
                    turn = retry_call(
                        lambda: self.secondary.complete(stable + "\n" + suffix),
                        breaker=CircuitBreaker(failures=8, recovery_s=0.0),
                        rng=self.rng,
                    )
                except (TransientError, CircuitOpenError, PermanentError):
                    return self._degrade(log, tenant, cid, blocks, salt, pii, "both_down")

            assert turn is not None
            self.prefix.write(blocks, tenant)
            decision.worker.blocks.update(blocks)
            self.worm.append(
                {
                    "tenant": tenant,
                    "model": turn.model,
                    "adapter_id": adapter_id,
                    "cache_salt_id": salt[:12],
                    "prefix_hash": blocks[0],
                    "cached_blocks": cached,
                    "router_choice": decision.worker.worker_id,
                    "fallback_reason": fallback_reason,
                    "kv_pod_id": decision.worker.worker_id,
                    "quant_scheme": self.quant,
                    "trace_id": cid,
                    "tools_allowed": sorted(allow_tools),
                    "sched": sched,
                    "pii_types": [x["type"] for x in pii],
                }
            )
            log.info(
                "complete",
                extra={
                    "cache": f"hit={cached}/{len(blocks)}",
                    "router": decision.reason,
                    "breaker": self.breaker.state.value,
                },
            )
            return {
                "text": turn.text,
                "model": turn.model,
                "degraded": False,
                "cached_blocks": cached,
                "overlap": decision.overlap,
                "worker": decision.worker.worker_id,
                "sched": sched,
                "fallback_reason": fallback_reason,
                "pii": pii,
                "salt_id": salt[:12],
                "prefix_hash": blocks[0],
                "trace_id": cid,
            }

    def _degrade(
        self,
        log: CorrelationAdapter,
        tenant: str,
        cid: str,
        blocks: list[str],
        salt: str,
        pii: list[dict[str, str]],
        reason: str,
    ) -> dict[str, Any]:
        log.warning("degraded", extra={"degraded": True, "router": reason})
        self.worm.append(
            {
                "tenant": tenant,
                "model": "deterministic",
                "degraded": True,
                "fallback_reason": reason,
                "trace_id": cid,
                "cache_salt_id": salt[:12],
                "prefix_hash": blocks[0] if blocks else "",
            }
        )
        return {
            "text": "degraded: cannot complete this turn",
            "model": "deterministic",
            "degraded": True,
            "cached_blocks": 0,
            "fallback_reason": reason,
            "pii": pii,
            "trace_id": cid,
        }


def _demo() -> None:
    rng = random.Random(7)
    workers = [
        Worker("p0", load=0.2, kv_util=0.3, blocks=set(), quant="fp8"),
        Worker("p1", load=0.4, kv_util=0.4, blocks=set(), quant="fp8"),
    ]
    gw = InferenceGateway(
        AffinityRouter(workers, temperature=0.0, rng=rng),
        ModelClient("sonnet", answer="cached-path answer ok"),
        ModelClient("haiku", answer="cheap ok path"),
        CircuitBreaker(failures=3, recovery_s=0.05),
        prefix=PrefixCache(),
        semantic=SemanticCache(),
        batcher=BatchScheduler(max_batched_tokens=64, kv_blocks_total=256),
        worm=WormLog(),
        rng=rng,
    )
    sys_prefix = "policy tools schema " + " ".join(f"t{i}" for i in range(40))
    a1 = gw.handle("hello user@x.com", tenant="acme", prefix_text=sys_prefix, correlation_id="c1")
    a2 = gw.handle("hello again", tenant="acme", prefix_text=sys_prefix, correlation_id="c2")
    b1 = gw.handle("hello user@x.com", tenant="other", prefix_text=sys_prefix, correlation_id="c3")
    assert a1["cached_blocks"] == 0
    assert a2["cached_blocks"] > 0
    assert a1["salt_id"] != b1["salt_id"]
    assert a1["prefix_hash"] != b1["prefix_hash"]
    assert any(x["type"] == "EMAIL" for x in a1["pii"])
    assert a2["worker"] == a1["worker"]

    gw.quant = "awq4"
    gw.cache_gen = "g2"
    a3 = gw.handle("hello", tenant="acme", prefix_text=sys_prefix, correlation_id="c4")
    assert a3["cached_blocks"] == 0

    sched = BatchScheduler(max_batched_tokens=20, kv_blocks_total=64)
    sched.submit(Sequence("d1", prompt_tokens=4, prefill_done=4, state=SeqState.DECODE, max_new=3, kv_blocks=1))
    sched.running["d1"] = sched.waiting.pop("d1")
    sched.kv_used = 1
    sched.submit(Sequence("p1", prompt_tokens=40))
    step = sched.iterate()
    assert step["decode_tok"] >= 1
    assert step["prefill_tok"] > 0

    br = CircuitBreaker(failures=1, recovery_s=0.05)
    dead = InferenceGateway(
        AffinityRouter([Worker("p0", 0.1, 0.1, set(), "fp8")], temperature=0.0, rng=rng),
        ModelClient("dead", fail=TransientError),
        ModelClient("also", fail=TransientError),
        br,
        prefix=PrefixCache(),
        semantic=SemanticCache(),
        batcher=BatchScheduler(max_batched_tokens=32, kv_blocks_total=64),
        worm=WormLog(),
        rng=rng,
    )
    d = dead.handle("q", tenant="acme", prefix_text=sys_prefix, correlation_id="c5")
    assert d["degraded"] is True

    cas = InferenceGateway(
        AffinityRouter([Worker("p0", 0.1, 0.1, set(), "fp8")], temperature=0.0, rng=rng),
        ModelClient("sonnet", answer="strong cascade answer"),
        ModelClient("haiku", answer="no"),
        CircuitBreaker(failures=8, recovery_s=0.0),
        prefix=PrefixCache(),
        semantic=SemanticCache(),
        batcher=BatchScheduler(max_batched_tokens=32, kv_blocks_total=64),
        worm=WormLog(),
        rng=rng,
        cascade=True,
    )
    c = cas.handle("what is up", tenant="acme", prefix_text=sys_prefix, correlation_id="c6")
    assert c["fallback_reason"] == "cascade_judge"
    assert c["model"] == "sonnet"

    sem = SemanticCache(threshold=0.5)
    sem.put("acme", "g1", "src1", "hours?", "we close at 5")
    assert sem.get("acme", "g1", "hours?") is not None
    assert sem.get("other", "g1", "hours?") is None

    br2 = CircuitBreaker(failures=2, recovery_s=0.05)
    def _boom() -> None:
        raise TransientError("429")

    try:
        retry_call(_boom, attempts=3, breaker=br2, rng=rng, sleep=lambda _s: None)
    except TransientError:
        pass
    assert br2.state in {BreakerState.OPEN, BreakerState.HALF_OPEN}
    time.sleep(0.06)
    assert br2.state is BreakerState.HALF_OPEN

    print(json.dumps({"ok": True, "hit": a2["cached_blocks"], "degraded": d["fallback_reason"]}, indent=2))


if __name__ == "__main__":
    _demo()
```

**Behavior encoded (maps to §§2–4):**

- Cache key = HMAC(server secret, tenant) in block 0 + sha256(parent, tokens, extra); extra includes quant + cache generation + adapter id. Tenants with identical prefixes do not share blocks. Quant bump ⇒ miss.
- Affinity router: longest-prefix overlap minus load; `temperature=0` is deterministic sticky (demo); KV util ≥ 0.85 sheds as 429-class degrade, not 500.
- Batcher packs **decode tokens before** prefill chunks; waiting/running/swapped; swap under 0.95 KV.
- Cascade: cheap client then judge; fail ⇒ primary. HA: primary 429-class `TransientError` trips breaker; secondary; both down ⇒ schema-stable `degraded: cannot complete this turn`.
- PII redacted before it would be keyed; audit stores types + HMAC ids. WORM is hash-chained. Single-flight on first block hash coalesces stampedes.
- Semantic cache requires tenant + model-version in the same lookup (no post-filter).

**Interview talking point:** jittered retries handle 429; they do not make an unsalted prefix cache multi-tenant-safe. Salt + generation bump + decode-first scheduling are three different invariants.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers are from the research file. Decision rules: **exact cache** is the quality-preserving $ lever; **semantic cache** is a product decision; **chunking** before disagg; **FP8** before INT4 on Hopper/Blackwell; **KV isolation** is a security control with a throughput cost.

### Scenario 1 — Multi-tenant SaaS chatbot: cut ~60% input $ without quality regression

**Problem statement.** Shared system prompt + tools (~8k tok) across many tenants; 1,000 sequential turns/day/workspace shape; variable user suffix 500 in / 400 out. Leadership wants **~60% input-side $ cut** with **bit-identical** answers vs today’s always-Sonnet 4.6 path. Isolation: tenants must not share prefix blocks (timing channel; KVGov cold/cached TTFT **0.22**). Forbidden if quality is a contract: semantic cache, INT4, complexity/cascade routers (research §6.2). Allowed: exact prefix/prompt cache, FP8 that passed eval, HA fallbacks. Compliance: org-level hosted caches are **one trust domain** — partition `prompt_cache_key`. NFR: interactive TTFT p50 benefits from hits; p99 remains a miss+queue. Cost baseline **[inferred]** no-cache Sonnet 4.6 **$31.50 / 1k**.

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Chat UI /  │ SSE │ CONTROL PLANE                                             │
│ MCP client │────▶│ Gateway: SSO, tenant TPM, breaker, correlation-id, W3C    │
└────────────┘     │ Policy: PII detect→redact→audit BEFORE tokenize/cache key │
                   │  HMAC cache_salt; tools+policy ≥ min cache tokens ABOVE   │
                   │  breakpoint; user/PII BELOW; no request-id in prefix      │
                   │ Router: HA order only (no quality cascade);               │
                   │  prompt_cache_key=tenant:promptver; ~15 RPM/key (shard)   │
                   │  single-flight(prefix_hash); geo pin before affinity      │
                   │ Orchestrator: Temporal wf=tenant:agent:thread; Kafka      │
                   │  outbox WORM before effectful MCP; fail-open complete()   │
                   └────┬──────────────────────────────┬───────────────────────┘
                        │ hosted complete()            │ tools/call + ticket
                        ▼                              ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ DATA PLANE       │        │ TOOL PROXIES                 │
                   │ Anthropic 5m     │        │ audience-bound tokens;       │
                   │  cache_control   │        │ JSON-encode; RBAC per turn   │
                   │  or OpenAI 5.6   │        │                              │
                   │  mode=explicit   │        │                              │
                   └────────┬─────────┘        └──────────────┬───────────────┘
                            │                                 │
                            ▼                                 ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE / TELEMETRY                                   │
                   │ Checkpoints; WORM tuple (salt_id, prefix_hash,            │
                   │  cached_tokens, write/read ratio); no KV dumps            │
                   └───────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix.**

| Dimension | A. Always-Sonnet 4.6, no cache, random routing, xxhash if self-host | B. Recommended: exact 5m prompt cache + per-tenant key/HMAC salt + HA fallbacks; no semantic / INT4 / quality router | C. Semantic cache + RouteLLM 70/30 Haiku/Sonnet + INT4 on the cheap tier |
| --- | --- | --- | --- |
| Cost | **$31.50 / 1k** | **$9.93 / 1k** (68% cut) **iff** 8k prefix hits; OpenAI terra analogue uncached **$21.80** → warm **~$7.42** | Mix **$16.80 / 1k** without cache; semantic **$0** model tokens on hit — false-hit is the real $ |
| Latency | p50 = full 8.5k prefill every turn | p50 TTFT drops on hits; **p99 still miss+queue**; Anthropic parallel cannot hit until first response starts | kNN tens of ms on hit; cascade/router mis-route is a **silent** quality p95 |
| Ops | Trivial | Breakpoint hygiene; 15 RPM/key; instrument `cache_read/input`; 1h TTL if inter-arrival >5m (write **2×**) | Eval set for threshold; judger/PGR shadow; INT4 agent eval |
| Security | No cross-tenant KV *if* no APC — or xxhash leak if APC on | Org cache partitioned by key; salt on self-host; sha256_cbor if index is global | Semantic TAG bypass; INT4 not a security control; under-route on legal |
| Scalability | TPM burns on 8.5k in × 1k | Input TPM ↓ on hits (confirm cached vs TPM on live guide); stampede on deploy unless single-flight | Embed cluster + false-hit load; 70% cheap still 100% if classifier fails open to strong |

**Decision rationale.** **B** is research §6.2: first 5m hit pays the 1.25× write; steady state **$9.93** vs **$31.50** is the 68% input-side cut **without** changing the model. A leaves $ on the table and, if someone “just enables APC,” becomes a timing oracle. C violates the quality contract (semantic ≠ bit-identical; RouteLLM 95% of GPT-4 on *their* MT Bench is not this SKU; INT4 is a different model version). Interview close: “Cache is an input lever. Partition the hosted key. Salt the self-host blocks. Do not route quality to save the other $6 vs Haiku.”

### Scenario 2 — Internal RAG / long-context: p99 ITL is the page

**Problem statement.** Same corpus, many questions; prefill dominates $ and TTFT; colocated naive hybrid measured up to **28.3×** TBT vs decode-only (Sarathi). Product pages on **p99 ITL**, not mean tok/s. Air-gapped weights acceptable; GPU budget enough for two pools (not a <8 GPU box). Target DistServe goodput: **>90%** of requests inside **both** TTFT and TPOT SLOs. Isolation: tenant salts even internally (contractor + employee). Spec decode only if measured α≥2 (vLLM **2.8×** is an upper bound). Do **not** raise batch size as the first move.

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Apps / RAG │     │ CONTROL PLANE                                             │
│ gateway    │────▶│ Admit iff KV util < 85% (measured); 429 Retry-After       │
└────────────┘     │ HMAC salt; extra_hash += LoRA id + quant + cache_gen      │
                   │ Affinity: llm-d / Dynamo overlap+load; router_temperature │
                   │  against herding; regional index; Kafka KVEvents          │
                   │ Pin model (no cost cascade on this SLO path)              │
                   └────┬──────────────────────────┬───────────────────────────┘
                        │                          │
                        ▼                          ▼
                   ┌─────────────────┐      ┌─────────────────┐
                   │ PREFILL pool    │ NIXL │ DECODE pool     │
                   │ high-FLOP       │ /    │ high-BW         │
                   │ APC / Radix LPM │Moon- │ BATCHER: decode │
                   │ chunked prefill │cake  │  first, leftover│
                   │ FP8 W           │      │  = prefill chunk│
                   │                 │      │ FP8 KV; paged FA│
                   └────────┬────────┘      └────────┬────────┘
                            │                        │
                            ▼                        ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE: LMCache GPU→CPU→NVMe→object (tenant prefix)  │
                   │ TELEMETRY: TTFT ≠ ITL histograms; α; hit%; KV util;       │
                   │  goodput; WORM salt_id + prefix_hash + quant_scheme       │
                   └───────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix.**

| Dimension | A. Colocated vLLM, naive hybrid batch, INT4 AWQ first, sticky=off | B. Recommended: chunked prefill → P/D disagg + FP8 W/KV + affinity + salts; spec iff α≥2 | C. Hosted long-context SKU + Gemini explicit cache of the doc |
| --- | --- | --- | --- |
| Cost | Fewer GPUs; INT4 **~4×** W density but dequant can **lose** to FP8 at large bs; no paper $/1k | More GPUs (xPyD); QServe **1.2–3.5×** tok/s vs TRT-LLM on *named* GPUs; Mooncake **+115%/+107%** req vs *their* prior | Gemini explicit: 0.1× reads + **$1/MTok-h** storage (confirm row); OpenAI long-ctx **2×** in if over threshold |
| Latency | p50 OK; **p99 ITL tracks prefill arrivals** (Sarathi 28.3× TBT); PagedAttention 2–4× still colocated | DistServe **7.4×** req *or* **12.6×** tighter SLO; Dynamo ~**2×** TTFT on overlap; Mooncake transfer **4.2% of TTFT** in 1P1D microbench | Vendor-opaque p99; explicit cache helps TTFT on the **doc** span; ITL still vendor decode |
| Ops | One pool; swap storms look like capacity | Two autoscalers; NIXL/LMCache; Grove-style topology so RDMA does not cross the wrong spine | No GPU ops; IAM on CachedContent; TTL/storage bill |
| Security | Random workers + xxhash = timing + collision; INT4 is not isolation | sha256_cbor in global index; tenant key prefix on Mooncake; P/D dtype convert-on-transfer | Prompts at vendor; explicit cache = **retained object**; residency +10% vs incident |
| Scalability | Bound by interference; herding N/A if no affinity — **wasted** prefill | Independent TTFT vs ITL scale; MLA 70 KB/tok vs 516 KB Llama-405B; reject not unbounded queue | Org TPM; explicit min 2,048–6,144 tok; storage hourly |

**Decision rationale.** **B** matches research §6.3 + RAG row of §6.1: chunked prefill first; if p99 still tracks prefill arrivals, **disagg**; FP8 W+KV after eval (TRT-LLM +6% E2E at same concurrency on FP8 KV); affinity so the corpus prefix is not re-prefilled on a cold GPU; salts so contractors do not share employee KV. A is the outage you already have (ITL p99) plus a quality ticket from INT4. C wins if data may leave the VPC — then do not self-host for ideology; still treat explicit cache as a document store with IAM. Interview close: “Do not raise batch size to fix ITL. Chunk, then disagg. Quote DistServe as the shape of dual-SLO goodput you will measure, not as your SLO.”

---

*End of module. Six sections. Four mandatory topics (caching, routing, batching, quantization). Token `$ / 1k` tables use official Anthropic/OpenAI SKUs dated 2026-08-21 and **[inferred]** named mixes (8k prefix / 500 suffix / 400 out × 1k turns). No unpublished production TTFT/ITL p50/p95/p99 SLOs — percentiles are labeled **[inferred]** or bound from documented mechanics (Sarathi 28.3× TBT, DistServe 7.4×/12.6×, Dynamo ~2× TTFT, KVGov 0.22 cold/cached, Mooncake 31.65 ms KV transfer).*
