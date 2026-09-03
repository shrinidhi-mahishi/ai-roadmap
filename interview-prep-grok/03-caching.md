# Module 03: Caching (KV / Prefix / Hosted Prompt / Semantic)

**Study + interview prep.** Grounded in research dated 2026-09-02 (90 sources). Prices, minima, TTLs, and paper numbers are vendor docs / named papers as of that date. `$ per 1k queries` figures are **[inferred]** from published token rates × a stated reference query, not a vendor SKU. Public vendor pages do **not** publish production p50/p95/p99 TTFT/ITL SLOs — missing percentiles are marked. Fine-tuning is out of scope. RAG appears only where caching changes the retrieve-or-stuff decision.

---

## What Is This?

A transformer does not “remember” your tools JSON between HTTP calls. Every request either **rebuilds** the prompt’s key/value tensors (prefill) or **reuses** tensors someone already paid to compute. **Caching** in LLM serving is that reuse — and interviewers fail people who treat it as one knob.

Five layers that are **not** the same cache:

| Layer | What is stored | A hit means | Analogy |
| --- | --- | --- | --- |
| **KV / PagedAttention** | K,V for *this* sequence’s past tokens | Decode does not recompute prefill | Scratch paper for the problem you are still solving |
| **Prefix / APC** | KV *blocks* keyed by chained block hashes, shared across requests | Skip prefill of the shared left-hand tokens | The reserved stack: same first chapters, different last page |
| **Hosted prompt cache** | Provider-side KV you never see (OpenAI: “stores key-value (KV) tensors, not the tokens”) | Input billed ~0.1×; TTFT drops | The library keeps a photocopy on one desk; you pay to print it, cheap to reread |
| **Semantic cache** | *Text* of a previous **response**, keyed by embedding kNN | **Skip the LLM entirely** | A colleague hands you yesterday’s answer because the question “sounds the same” |
| **Application / result cache** | Exact `(model, prompt_hash, tools_hash, params)` or a LangGraph node output | Skip a subgraph or tool | Browser cache / memoized function — exact key, exact body |

**Exact** caches (KV, prefix, hosted prompt) require the rendered prefix to match **from the left**. One mutated token invalidates everything after it. **Approximate** semantic cache matches nearby questions and returns a prior completion as truth. That is the whole product distinction: exact reuse of **compute state** vs approximate reuse of **answers**.

Prefill is compute-bound (all prompt tokens in parallel, building KV). Decode is memory-bandwidth-bound (one token per step, reading the growing KV). NVIDIA NIM: **TTFT** = query submit → first token (queue + prefill + network); **ITL / TPOT** = `(e2e − TTFT) / (output_tokens − 1)` so ITL characterizes decode only. GenAI-Perf / AIPerf exclude TTFT from ITL; LLMPerf includes it — the same number is not portable.

## Why It Matters

Almost every production agent has an 8k-token tools+system block that is identical across turns. Hosted prompt cache turns that into a **1.25× write / 0.1× read** meter (OpenAI GPT-5.6+, Anthropic 5-minute). Self-hosted prefix cache turns it into HBM you share once — or leak via TTFT if you omit `cache_salt`. Semantic cache can drop e2e from seconds to milliseconds and also serve the wrong tenant’s balance.

Interviews test whether you split **control plane vs data plane**, place the **breakpoint after stable content**, shard `prompt_cache_key` before **~15 RPM**, HMAC-salt tenants, and refuse semantic cache on tool-using loops. A Principal answer names write/read multipliers, min-token silent no-ops, stampede, and the fallback **semantic → exact prefix → uncached prefill**.

---

### 1. System Topology & Data Flow

A production cache stack is a **control plane** plus a **data plane**. Collapsing them — hashing the raw prompt in the client, then routing on cache affinity without a tenant salt — is how teams leak prompts via TTFT and bill 1.25× writes forever.

```
                         TELEMETRY / OBSERVABILITY SINKS
         ┌──────────────────────────────────────────────────────────────────┐
         │  cached_tokens / cache_write_tokens / cache_read_input_tokens    │
         │  token hit rate vs request hit rate   write/read ratio           │
         │  RPM per prompt_cache_key (~15 overflow)   stampede write spikes │
         │  HiCache BLOCKED / prefetch   Fireworks cache-adjusted TPS       │
         │  WORM audit: (block_hash, salt, pod, tenant) — not raw prefix    │
         └────────────▲─────────────────────▲──────────────────▲────────────┘
                      │ spans               │ metrics           │ audit events
                      │                     │                   │
┌─────────────────────┴─────────────────────┴───────────────────┴────────────┐
│ CONTROL PLANE  (keys, breakpoints, TTL, affinity, salt, admission)         │
│                                                                            │
│  ┌──────────┐ ┌─────────────┐ ┌──────────────┐ ┌───────────┐ ┌──────────┐ │
│  │ IdP/PEP  │ │ Canonical   │ │ Breakpoint / │ │ Affinity  │ │ Stampede │ │
│  │ HMAC salt│ │ prefix      │ │ cache_control│ │ router    │ │ single-  │ │
│  │ NEVER    │ │ (no ts in   │ │ prompt_cache_│ │ key/LPM/  │ │ flight   │ │
│  │ client   │ │  left span) │ │ options      │ │ Dynamo    │ │ pre-warm │ │
│  └────┬─────┘ └──────┬──────┘ └──────┬───────┘ └─────┬─────┘ └────┬─────┘ │
│       │ tenant       │ key           │ markers       │ replica    │ lock  │
└───────┼──────────────┼───────────────┼───────────────┼────────────┼───────┘
        │              │               │               │            │
        ▼              ▼               ▼               ▼            ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ DATA PLANE  (compute vs state — independently scheduled)                  │
│                                                                           │
│  COMPUTE: tokenizer → scheduler admit → prefill GEMM (or skip matched     │
│           prefix blocks) → decode attention / speculative verify          │
│                                                                           │
│  STATE:   KV block pool (vLLM) / radix tree (SGLang) / hosted KV          │
│           (you never see tensors; usage fields only)                      │
│                                                                           │
│  ┌────────────── TOOL PROXIES (MCP / vendor APIs — least privilege) ───┐  │
│  │ lookup_semantic │ write_prefix │ generate_cached │ generate_uncached│  │
│  │ prewarm_cache   │ (NO omnibus cache(prompt, tenant_id, salt))       │  │
│  │ Identity + salt from verified token / RunContext — NEVER model JSON │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
└─────────┬───────────────┬─────────────────┬─────────────────┬─────────────┘
          │               │                 │                 │
          ▼               ▼                 ▼                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE LAYER  (most prefix KV is ephemeral; be honest in DR reviews) │
│                                                                           │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌─────────┐  │
│  │ GPU HBM    │ │ Host DRAM  │ │ L3 / remote│ │ Semantic   │ │ App /   │  │
│  │ APC/radix  │ │ Offload    │ │ LMCache /  │ │ Redis HNSW │ │ LangGraph│ │
│  │ (process-  │ │ Connector  │ │ Mooncake / │ │ + TAG      │ │ node    │  │
│  │  local)    │ │ HiCache L2 │ │ HiCache L3 │ │ tenant+    │ │ cache   │  │
│  │            │ │ (instance- │ │ (shared ns │ │ model+     │ │ (text,  │  │
│  │            │ │  private)  │ │  only)     │ │ policy)    │ │ not KV) │  │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘ └─────────┘  │
│  Gemini explicit CachedContent = named resource + storage rent            │
│  Hosted OpenAI/Anthropic KV: NO dump API. Failover region = cold.         │
└───────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not couple):**

| Plane | Owns | Typical components | Failure if coupled |
| --- | --- | --- | --- |
| **Control** | Cache keys, breakpoint placement, TTL, affinity routing, tenant salt, admission, stampede serialization | Gateway (`prompt_cache_key`, Dynamo KV router, llm-d Precise Prefix-Cache Scorer, SGLang `--schedule-policy lpm`), IdP, Redis semantic index | Client-supplied salt; geo routing that fights cache locality |
| **Data (compute)** | Prefill GEMM, decode attention, speculative verify | GPU HBM + Tensor Cores | Prefill and decode sharing one iteration budget without a token cap (Sarathi: up to **28.3×** TBT vs decode-only) |
| **Data (state)** | KV blocks, radix/prefix trees, offloaded KV (CPU/SSD/object) | vLLM block pool, SGLang RadixCache, LMCache / Mooncake / Dynamo KVBM | Treating KV as ephemeral scratch while it is a **materialization of the prompt** |

**Where the cache sits relative to tokenizer, scheduler, and generator:**

```
tokenizer → token IDs
         → scheduler (admit under KV headroom + prefix match)
         → prefill (or skip matched prefix blocks)
         → KV block pool / radix / hosted KV
         → decode loop (generator)
```

vLLM V1: `waiting` holds new / preempted / grammar-blocked / remote-KV-wait; `running` holds in-flight. Each `schedule()` continues `running` first, allocates KV / preempts if HBM cannot grow, then admits from `waiting` under `token_budget` and `max_num_running_reqs`. Prefix cache is consulted at admit via `kv_cache_manager.get_computed_blocks()`; a hit raises `num_computed_tokens` so this step only schedules the unmatched suffix.

**Request-flow narrative (cache key → lookup → hit/miss → prefill/decode):**

1. **PEP / salt (control).** TLS terminates. Verified token → `tenant_id`. Gateway injects `cache_salt = HMAC(server_secret, tenant_id)` — **never** a client header. Semantic path: the same identity becomes a Redis `TAG` in the **same** `FT.SEARCH` as the KNN.
2. **Canonicalize (control).** Render `tools → system → messages` (Anthropic order; OpenAI: tools names/schemas/**ordering**, `text.format`, `reasoning.effort` are in the prefix). Strip clocks from the **left** span. Application key = `sha256(canonical_json(model, tools, system, params))`. Hosted routing key = stable `workflow:tenant:shard-n` (`prompt_cache_key` influences routing; it is **not** a confidentiality boundary).
3. **L0 semantic lookup (optional).** Embed the *user question* (not the 8k tools block). HNSW + tenant/model/policy tags. Hit → return stored **response text**, skip the LLM. Miss / breaker open → continue. Threshold too low = wrong answer served as truth.
4. **L1 exact prefix lookup (data state).** Self-hosted: walk chained block hashes (vLLM) or radix LPM (SGLang) from the left until first miss. Hosted: provider matches the rendered prefix at breakpoints; you observe `cached_tokens` / `cache_read_input_tokens`.
5. **Hit — skip prefix prefill.** Scheduler only prefills the unmatched suffix. Decode still runs (exact cache does **not** skip the generator). Bill read SKU (~0.1×). TTFT approaches ITL on a full-prefix hit (TRT-LLM qualitative: identical back-to-back requests).
6. **Miss — prefill + write.** Compute-bound prefill builds KV. Hosted GPT-5.6+/Anthropic 5m: **1.25×** write at the breakpoint. Stampede: N parallel cold prefixes ⇒ **N writes** (Anthropic: entry available only after the first response **begins**). Control plane single-flights or `max_tokens: 0` pre-warm.
7. **Admit / route (control).** OpenAI: machine load + hash of initial tokens after hidden content (incl. tools; engineering note ~**256** tokens) + optional `prompt_cache_key`. Above **~15 RPM** per prefix/key, overflow to a machine without the entry. Self-hosted: Dynamo overlap credits or llm-d precise scorer; naive kube round-robin **destroys** locality.
8. **Decode + telemetry.** Output billed at the full output SKU on every provider in this research. Log token hit rate **and** request hit rate **and** `cache_write_tokens`. A 10k prefix + 50-token question looks like 99% token hit even if every question is unique.

**Hosted vs self-hosted data plane (you never see hosted KV):**

| Provider | You place | Data plane | Routing knob | You observe |
| --- | --- | --- | --- | --- |
| OpenAI 5.6 | up to 4 breakpoints; optional key | Machine-local KV | `prompt_cache_key` (~15 RPM) | `cached_tokens`, `cache_write_tokens` |
| Anthropic | up to 4 `cache_control`; 20-block lookback | Workspace/org KV; in-memory for ZDR | none (sticky implied) | `cache_read_*`, `cache_creation_*` |
| Gemini explicit | named `CachedContent` | Project resource + storage | cache name | `cachedContentTokenCount` |
| Gemini implicit | nothing | short-window, load-dependent, ≤24 h | none | cached token field |
| Azure | same as OpenAI | **Subscription**-local | `prompt_cache_key` | same; PTU-M no writes |
| Fireworks | session / isolation keys | Replica-local | `x-session-affinity` | `fireworks-cached-prompt-tokens` |
| Together serverless | nothing | Fleet-shared, evicted | none | cached-input SKU |
| DeepSeek | nothing | Disk units, seconds to build | none | `prompt_cache_hit_tokens` |

---

### 2. Core Mechanics & Algorithms

#### 2.1 Invariants

**Invariant I1.** Intra-request KV is not cross-request reuse. Decode reading its own past tokens is table stakes. APC / hosted prompt cache is **sharing those blocks across requests**. Mixing them in a design review is an instant fail.

**Invariant I2.** Exact caches match **rendered tokens from the left**. `model`, tool order, schema whitespace, `reasoning.effort`, image `detail`, compaction, and a timestamp in system all change the prefix. Semantic cache matches **embedding neighbors of the query** and returns a **previous completion**.

**Invariant I3.** Pin `model_id + tokenizer + tools_hash + salt_namespace + serve_dtype`. A LoRA id, multimodal hash, or `cache_salt` is in vLLM’s extra hashes — a new adapter is a different cache. Hosted: `model` is in the prefix key.

**Invariant I4.** Salt is a **secret the gateway injects**. vLLM: omit `cache_salt` ⇒ globally content-addressed sharing. Never accept a client-supplied salt. `prompt_cache_key` is routing, not a tenant wall.

#### 2.2 Intra-request KV: PagedAttention, GQA, MLA

**PagedAttention (Kwon et al., SOSP 2023).** KV is virtual memory: fixed-size blocks, a block table per sequence, near-zero internal fragmentation (waste only in the last block, “under 4%” in the vLLM blog), copy-on-write for beam/parallel sampling. vLLM: **2–4×** throughput vs FasterTransformer and Orca at comparable latency; up to **24×** vs HuggingFace Transformers (paging *and* continuous batching). Default block size in modern vLLM V1 is **16 tokens**. KVGov: vLLM 0.26.0 V1 **removed `block_size=1`** (`ValueError`) — EarlyBird-style single-token reconstruction collapses for free text.

**KV bytes per token, BF16.** Per token, per layer: `2 × n_kv_heads × d_head × 2 bytes`. GQA (Ainslie et al., EMNLP 2023): Llama-class 64 Q / 8 KV is an **8×** KV cut vs MHA. MLA (DeepSeek-V2): **93.3%** KV reduction vs DeepSeek 67B MHA. DeepSeek-V3 (Table 1, BF16): **70.272 KB/token** (MLA) vs Llama-3.1-405B **516.096 KB/token** (GQA, 7.28×) and Qwen-2.5-72B **327.680 KB/token** (GQA, 4.66×).

Worked HBM (same formula, not a vendor quote): 8k-token tools prefix × Llama-405B-class **516.096 KB/token** ≈ **4.13 GB** of KV *for that prefix alone* before paging waste. Same prefix on DeepSeek-V3 MLA ≈ **0.56 GB**. APC sharing that prefix across 32 same-salt tenants is **4.13 GB once**, not ×32; HMAC-per-tenant salt makes it **×32**. KVGov boundary salt is the research attempt to keep ~**93%** of the sharing. A 45 GiB TRT-LLM host offload buffer holds ~**87** such 8k Llama-405B prefixes, or ~**80** DeepSeek-V3 32k contexts **[inferred from KB/token × token counts]**.

**Splitwise (Patel et al., ISCA 2024).** Prefill and decode have distinct latency, throughput, memory, and power. KV transfer **<7%** of prompt compute; non-overlapped remainder **~8 ms (A100) / ~5 ms (H100)**; E2E transfer **0.8%** vs **3%** serialized. Cluster: up to **1.4×** throughput at **20%** lower cost, or **2.35×** at iso-power and iso-cost. This is why hosted prompt cache still needs replica affinity: the KV that prompt cache *is* has to live on a machine the next request can reach.

**DistServe (Zhong et al., OSDI 2024):** colocating prefill+decode causes “strong prefill-decoding interferences.” Published vs colocated SOTA: **7.4×** more requests **or** **12.6×** tighter SLO while staying inside both SLOs for **>90%** of requests. **Sarathi-Serve:** naive hybrid batching up to **28.3×** higher TBT vs decode-only; stall-free chunked prefill packs running decodes first.

#### 2.3 Cross-request exact prefix: block hashing and radix trees

**vLLM Automatic Prefix Caching.** Each block hash = `(parent_hash, block_tokens, extra_hashes)`. Extra hashes: LoRA IDs, multi-modality input hashes, `cache_salt`. `--prefix-caching-hash-algo`: `sha256` (default; pickle serialization, **not** stable across Python/vLLM versions), `sha256_cbor` (cbor2, recommended for deterministic cross-environment hashing), `xxhash` / `xxhash_cbor` (faster; **not cryptographically secure** — docs warn of collision/leak risk in multi-tenant). Prefix caching is on by default in v1.

`find_longest_cache_hit` walks hashes from the start until the first miss; because hashes chain through the parent, a hit on block *N* implies blocks 0…*N−1* match.

`cache_salt` is injected into the **first** block hash so only same-salt requests share. Treat the salt as a secret: random values long enough to be unpredictable (e.g. **43 base64 characters, 256 bits**), not a user name. An attacker who obtains the salt can still mount the timing attack against that tenant.

**Complexity of exact prefix match.** Prompt length \(n\) tokens, block size \(B\) (vLLM default **16**; TRT-LLM default **128**). Hash-walk is \(O(n/B)\) lookups until first miss — linear in blocks, not a suffix array. One-token mutation at position \(t\) wastes blocks after \(\lfloor t/B \rfloor\). TRT-LLM: **only full blocks** are shared; a 180-token shared prefix at block size 64 reuses only the first **128** tokens. TGI v3: prefix-cache query overhead “roughly **6 µs**” (not a portable SLO).

**SGLang RadixAttention (Zheng et al.).** Retain KV in a **radix tree** (compressed trie) keyed by token sequences; longest-prefix match on admission; LRU eviction of **leaves** so shared roots (system prompts) survive; refcount so in-flight nodes are unevictable. LMSYS blog: up to **5×** throughput vs Guidance and vLLM on Llama-7B / Mixtral-8x7B / A10G; “no noticeable overhead even in the absence of cache hits,” so RadixAttention is always on. NeurIPS: up to **6.4×** throughput and **3.7×** lower latency on a broader suite.

Scheduler: `--schedule-policy lpm` (default) vs `fcfs`. If LPM and `len(waiting_queue) > 128`, policy **degrades to FCFS** to avoid expensive prefix matching. That is the complexity valve: radix LPM is \(O(\text{matched tokens})\) per waiting request; at queue depth 128 they stop paying it.

**HiCache (LMSYS, 2025-09-10).** L1 = GPU HBM (instance-private); L2 = host DRAM (instance-private — two instances on the same node do **not** share L2); L3 = storage backend (shared only if the namespace is cluster-wide). Prefetch from L3 if remaining hit **>256 tokens**. Timeout \(= \min(30\mathrm{s},\; 2\mathrm{s} + 0.1\mathrm{s}\times\mathrm{tokens}/1024)\). Write policies: `write_through`, `write_through_selective`, `write_back`. `file` at `/tmp/hicache` does **not** survive a rolling restart; `mooncake` / `hf3fs` / `nixl` / `aibrix` with a shared namespace can.

**TensorRT-LLM.** `enableBlockReuse=true` default. Reuse is possible only **after the request that computed the block terminates** — a large in-flight batch of identical system prompts sees **zero** reuse until the first request finishes. Host offload example **45 GiB** pinned; pinning tens of GB on x86 can take **tens of seconds**, one-time.

**Hugging Face TGI.** Prefix caching on by default in TGI v3 when flashinfer/flashdecoding and CUDA CC **≥ 8.0**. Auto-disabled for VLMs, encoder-decoder, LoRA adapters, `ATTENTION=paged`. HF reports **13×** vs vLLM *with prefix caching on* and **30×** without on a long-prompt L4/Llama-3.1-8B setup — **second-run** benches (first run is cold).

**LMCache / CacheBlend / Mooncake.** LMCache: chunk default **256 tokens**; up to **15×** throughput vs basic vLLM on multi-round QA (abstract); remote fetch can beat full prefill (Company C: **22–32%** lower TTFT). Context truncation **destroys** prefix hit rate. `--cpu-offload-gb` offloads **weights**, not KV. **CacheBlend (EuroSys 2025):** prefix-only reuse fails when RAG chunks are not a prefix; recomputes typically **<15%** of tokens; TTFT **2.2–3.3×**, throughput **2.8–5×** vs full recompute. **Mooncake:** thousands of nodes, **>100B tokens/day**; **+115% / +107%** requests on A800 / H800 vs prior system.

#### 2.4 Hosted prompt cache (productized prefix with a bill)

Matching is **exact** on the rendered prefix (tokenizer + hidden system + tools + messages), **not** semantic. Isolation is org/workspace/subscription, not per-API-key.

**OpenAI GPT-5.6+.** Min **1,024** visible input tokens (hidden system tokens do not count). Writes **1.25×**, reads **0.1×**. `ttl` only **`30m`** (sliding after last write or reuse). Implicit breakpoint at end of latest eligible user/tool message; `mode=explicit` ⇒ **no breakpoints ⇒ no caching and no write charges**. Up to **4** cache writes; reads consider up to the latest **50** breakpoints. Official arithmetic: one write + one full read = **1.35×** vs **2×** uncached; ten requests (1 write + 9 reads) = **2.15×** vs **10×**. Routing: caches live on individual machines; **~15 RPM** per prefix/key can overflow. Caches **not** shared across organizations or regional processing boundaries. Pre-5.6: often no write fee; 4o-era **50%** off cached input; 5–10 min idle / gone within **1 h**. GPT-5.6 **replaced** that 50%/no-write model.

**Anthropic.** Prefix order **`tools` → `system` → `messages`**. Up to **4** `cache_control` breakpoints. Writes **only at the breakpoint**; reads look back at most **20 content blocks**. Minima **512–4,096** by model (Haiku 4.5 / Opus 4.5 / Opus 4.6 = **4,096**). Below floor: **silent no-op**. TTL default **5 minutes** sliding; `ttl: "1h"` at **2×** write. Pre-warm: `max_tokens: 0`. Stampede: parallel N identical prefixes on a cold cache ⇒ **N writes**. ZDR: KV + hashes in memory only, not at rest; isolation workspace-level on Claude API / Claude-on-AWS / Foundry, **org-level only** on Bedrock and Google Cloud. Launch TTFT (only vendor-published pair): 100k-token book **11.5 s → 2.4 s (−79%)**.

**Gemini.** Implicit: on by default; **90%** discount; **no storage fee**; always deleted within **24 hours**. Explicit: named resource; storage rent; default TTL **60 minutes**; min TTL **1 minute**; no documented maximum. Min tokens: Gemini 3 family **4,096**; 3.0 Flash / 3.1 Pro Preview implicit **6,144**; Gemini 2 family **2,048**. Max blob/text **10 MB**.

**Others (interview traps).** Bedrock: not on **batch** inference; cross-region “may lead to increased cache writes.” Azure: **not shared between subscriptions**; PTU-M does **not** support breakpoints. Fireworks: default **50%** off serverless; replica-local; `x-prompt-cache-isolation-key` because dedicated is **shared by default** (they document a timing-attack residual). Together dedicated: cache **on and cannot be disabled** (flags ignored through **February 2026**). DeepSeek disk: first two long-doc questions with the same system+document **miss by design**; hit/miss ≈ **0.032**. Cohere: **no** first-party prompt-cache SKU as of 2026-09-02.

#### 2.5 Semantic cache vs application cache

**Exact prefix** reuses **KV** and still runs the generator on the suffix. **Semantic cache** embeds the query, HNSW/FLAT kNN, threshold, returns a previous completion.

Redis: in-index HNSW “sub-millisecond”; blog: vector search “adds 5–20 ms” but “saves 1–5 seconds”; hits “typically 2–4× faster, with optimal cases reaching 50–100×.” Those are **different claims** (in-index kNN vs e2e including embedding) — do not collapse them.

**GPTCache:** default evaluator **ExactMatchEvaluation**. Threshold **0–1**; their docs: 0 = no hits, 1 = all neighbors are hits (opposite of cosine intuition — calibrate against the evaluator). **RedisVL:** default `distance_threshold=0.1` (cosine distance, lower is closer). FAQ blog: start cosine similarity **0.88**, lower to **0.84** if paraphrases miss. LangCache: `use_exact_search` and `use_semantic_search` both default **True**. **LangGraph `CachePolicy`:** application-level (pickle-hash of node input), not KV. Handlers are never cached.

#### 2.6 Cache-aware routing

**OpenAI `prompt_cache_key`.** Primary grouping under overflow; prefix hash secondary. Shard pattern: `prompt_version:tenant:shard-{n}` with SHA-256 of tenant+session. Keep keys stable; split busy groups rather than a new key per request. A tools-list reorder in the first ~256 hashed tokens is a **different machine**, not just a miss.

**NVIDIA Dynamo.** \(\mathrm{cost} = \textit{prefill_load_scale} \times \max(\textit{raw_prefill} - \textit{overlap_credit}, 0) + \textit{decode_blocks}\). Worked **[inferred from the formula, not a Dynamo SLO]:** request needs 500 prefill blocks. Worker A overlap 400, decode_blocks=50, credit=1.0 → cost \(= 100 + 50 = 150\). Worker B overlap 0, decode_blocks=10 → cost \(= 510\). Temperature 0 sends to A. After 15 RPM of identical prefixes on A, decode_blocks explode — herding is the hidden ITL cost.

**llm-d Precise Prefix-Cache Scorer:** vLLM `KVEvents` over ZMQ → score = fraction of this request’s prefix already on each pod. Official blog: **57×** faster responses and **2×** throughput vs naive load balancing (authors’ bench, not a portable SLO). Manus quote they reprint: “The KV-cache hit rate is the single most important metric for a production-stage AI agent.”

SGLang LPM is *admission order inside one engine*, not cross-replica routing. Geo vs affinity: OpenAI regional processing **10%** uplift for eligible models released on/after **2026-03-05**; caches **cannot** cross regional boundaries.

#### 2.7 Skip RAG when the corpus fits the cache

Anthropic Contextual Retrieval (2024-09-19): knowledge base **< ~200,000 tokens (~500 pages)** → stuff the corpus, skip RAG. Prompt caching: **>2×** latency cut, up to **90%** cost. Contextualize-ingest with cache: **$1.02 per million document tokens** under their 800-token chunks / 8k-token docs / 50-token instructions / 100-token contexts assumption. This is the only RAG intersection this module covers. Above 200k, or when you need per-query slices, RAG — and **do not** put retrieved chunks before the breakpoint unless chunks are stable (else CacheBlend or don’t cache RAG).

---

### 3. Token Economics & NFR Analysis

#### 3.1 Multipliers, minima, TTLs (as of 2026-09-02)

| Provider | Write \(W\) | Read \(R\) | Min tokens | TTL | Isolation |
| --- | --- | --- | --- | --- | --- |
| OpenAI GPT-5.6+ | **1.25×** | **0.1×** | **1,024** visible | **30m** sliding | Org; not across regions |
| OpenAI pre-5.6 | **1.0×** | often 0.5× (4o) / 0.1× (many 5.x) | 1,024–2,048 | 5–10 min / `24h` | Org |
| Anthropic 5m | **1.25×** | **0.1×** (Fable/Mythos 5.1: **0.025×**) | 512–4,096 | **5m** sliding | Workspace (API/Foundry/Claude-on-AWS); org on Bedrock/GCP |
| Anthropic 1h | **2.0×** | same read | same | **1h** sliding | same |
| Gemini implicit | **1.0×** | **0.1×** (2.5+) | 2,048 / 4,096 / 6,144 | best-effort ≤24 h | Project; storage **$0** |
| Gemini explicit | **1.0×** create **+ storage rent** | 0.1× (2.5+); 0.25× residual on 2.0 | same | default **60 min** | Cache resource IAM |
| Fireworks serverless | **1.0×** | default **0.5×** | none published | minutes–hours, LRU | Per account; dedicated shared unless isolation key |
| DeepSeek disk | miss SKU (no write premium) | ~**3%** of miss (≈0.032) | unit-match | hours–days | Per user, best-effort |
| Together serverless | **1.0×** | model column (examples 19–81% off) | none published | fleet-evicted | Fleet-shared |

OpenAI Standard short-context per 1M: `gpt-5.6-sol` **$4 / $0.40 cached / $5 write / $20 out**; terra **$2 / $0.20 / $2.50 / $12**; luna **$0.20 / $0.02 / $0.25 / $1.20**; cyber **$12.50 / $1.25 / $15.625 / $75**. Sol promo through **2026-11-21**. Regional residency **+10%** (eligible models from 2026-03-05). Realtime `gpt-realtime-2.1` audio input **$32**, cached audio **$0.40** (98.75% off). Long-context Sol ≥~272k: **2× in / 1.5× out on the full request**.

Anthropic Sonnet 5 per MTok: base **$2**, 5m write **$2.50**, 1h write **$4**, hit **$0.20**, out **$10**. Fable/Mythos 5.1 hit **$0.25** on **$10** base (0.025×).

Vertex explicit storage: Gemini 3.1/3/2.5 Pro **$0.0000045 per token-hour** = **$4.50 / MTok-hour** **[inferred as 1M × rate]**; Flash family **$0.000001** = **$1.00 / MTok-hour**. A 1M-token explicit Pro cache for 24 h: **24 × $4.50 = $108** storage whether or not it is read **[inferred]**.

DeepSeek V4 Flash off-peak/peak per 1M: hit **$0.007 / $0.014**, miss **$0.22 / $0.44**. Peak windows: **01:00–04:00 and 06:00–10:00 UTC, Mon–Fri**.

#### 3.2 Break-even reuse count

Uncached input = 1. \(n\) identical-prefix requests cost \(W + (n-1)R\) vs \(n\). Break-even \(n \ge (W-R)/(1-R)\).

| Scheme | \(W\) | \(R\) | Break-even \(n\) | At \(n=10\): cached vs uncached |
| --- | --- | --- | --- | --- |
| OpenAI 5.6 / Anthropic 5m | 1.25 | 0.1 | **2** (1.278 → 2) | 2.15× vs 10× (**78.5%** prefix savings) |
| Anthropic 1h | 2.0 | 0.1 | **3** (2.111 → 3) | 2.90× vs 10× (**71%**) |
| Fable 5.1 5m | 1.25 | 0.025 | **2** | 1.475× vs 10× (**85.3%**) |
| Gemini implicit / DeepSeek | 1.0 | ~0.1 / ~0.032 | **2** / **2** | Gemini 1.9× vs 10×; DeepSeek ~1.29× vs 10× |
| Gemini explicit | 1.0 + storage | 0.1 | **depends on TTL hours and hit rate** | storage can dominate idle caches |
| Fireworks default | 1.0 | 0.5 | **2** | 5.5× vs 10× (shallower than 0.1×) |

Anthropic: caching pays off after **one** cache read for 5-minute (1.25× write), or after **two** cache reads for 1-hour (2× write). Use 5m if the prefix is used **more often than every 5 minutes** (refreshes at no extra write). Use 1h when gaps are **>5 min and <1 h**.

**Cost trap:** a shared prefix **below the minimum** is billed 1.0× forever (silent no-op). A 3k-token prompt caches on Opus 5 / Sonnet 4.5 and **silently will not** on Haiku 4.5 / Opus 4.5 (4,096 floor).

#### 3.3 Worked `$ per 1k queries` **[inferred]**

Assumptions stated so the arithmetic is auditable. **Not** a vendor SKU.

**A. Multi-tenant agent.** Shared 8,000-token tools+system, 400-token user/tool suffix, 400-token output. 1k queries.

Sonnet 5, 5-minute cache:

- Uncached: \(1000 \times (8400\times\$2 + 400\times\$10)/10^6 = \$20.80\)
- 1 write + 999 reads: write \(8000\times\$2.50/10^6=\$0.020\); reads \(8000\times999\times\$0.20/10^6=\$1.598\); suffix \(0.80\); out \(4.00\); **total ≈ $6.42 / 1k** (**69%** off). Prefix-only: 1.25 + 999×0.1 = 101.15 vs 1000 → **89.9%** off the prefix line.
- Rewrite every 10 queries (100 writes + 900 reads): **≈ $8.24 / 1k**.

`gpt-5.6-luna` short-context, 1 write + 999 reads: **≈ $0.72 / 1k** vs uncached **$2.16**.

**B. Gemini 3.1 Pro explicit.** 100k-token corpus, TTL 1 h, 1,000 queries in that hour. Create at $2.00/MTok = **$0.20**; 1,000 reads at $0.20/MTok × 0.1M = **$20**; storage **$0.0000045 × 100,000 × 1 h = $0.45**. Context side **≈ $20.65** vs **$200** uncached. Idle 24 h, **0** reads: storage **$10.80** + create **$0.20** = **$11.00** to store unused context. Ten reads in 1 h: storage $0.45 + create $0.20 + reads **$2.00** = **$2.65** vs 10 × $0.20 = **$2.00** uncached — **worse than not caching** unless TTFT (not dollars) is the objective.

**C. DeepSeek V4 Flash off-peak.** 8k prefix, miss once then 999 hits: prefix **≈ $0.058 / 1k** vs **$1.76** uncached.

**D. Fireworks default 50% vs OpenAI 5.6 0.1×.** If Fireworks input were $2/MTok (illustrative; **plug the live Model Library SKU**), 999 hits at $1/MTok ⇒ prefix **≈ $8.016 / 1k** vs $16 uncached. OpenAI Sol 1 write + 999 reads: prefix **≈ $3.23 / 1k**. The 0.1× SKU beats a 50% automatic cache on long reused prefixes; Fireworks wins on **zero write tax** and no 1,024 floor **if** affinity holds **[inferred]**.

#### 3.4 Rate limits: cache reads as ITPM multiplier

Anthropic: for most models, **`cache_read_input_tokens` do not count toward ITPM**; `input_tokens` after last breakpoint and `cache_creation_input_tokens` do. Exception: **Claude Haiku 3.5** counts cache reads toward ITPM. Official: **2,000,000 ITPM** at **80%** cache hit ⇒ **10,000,000** total input tokens/minute processed. Output tokens and RPM still apply. Bedrock GPT-5.6: cached tokens excluded from ITPM. OpenAI’s prompt-caching guide does **not** state an ITPM exclusion — **[limited public data — confirm live quota docs]**.

#### 3.5 Latency SLA

> ⚠️ Gap: OpenAI, Anthropic, Google, Azure, and Bedrock do **not** publish fleet-wide cache-hit p50/p95/p99 TTFT/ITL, and there is **no vendor RAG or prompt-cache p99 SLO**. HiCache PR #19320 publishes p90/p99 on one engine bench — not a portable SLO. Numbers in the published-datapoint table are launch microbenches or paper hardware. Policy targets below are architecture-derived **[inferred]**, not vendor SLOs. Measure `cached_tokens` and TTFT yourself.

| Published datapoint | What it is **not** |
| --- | --- |
| Anthropic 100k book **11.5 s → 2.4 s** TTFT (−79%); 10k many-shot **1.6 s → 1.1 s**; 10-turn **~10 s → ~2.5 s**; marketing cap **85%** latency / **90%** cost | Not a p99 SLO |
| KVGov A100 / Qwen2.5-7B / vLLM 0.26.0: TTFT **149.6 ms** cold / **32.8 ms** cached (ratio **0.22**) at 2119 tokens; 1447-token ratio **0.37**; llama.cpp Metal **0.093** | Lab, one GPU |
| HiCache + 3FS: Qwen3-Coder-480B ~25k / 8 turns: TTFT **−56%**, throughput **2×**, hit **40% → 80%**; R1-671B PD-disagg QA hits **−84%** TTFT | LMSYS post, not your fleet |
| HiCache PR #19320: L2-only avg **3.49 s** / p90 **14.13 s** / p99 **21.29 s**; L2+L3 **2.34 / 6.56 / 18.44 s**; other before/after avg **1.20 s → 0.44 s** | One PR’s bench |
| Fireworks marketing: TTFT “as much as **80%**”; TGI **6 µs** match overhead; TRT-LLM TTFT→ITL on identical back-to-back | Caps / qualitative |
| DistServe **7.4×** requests or **12.6×** tighter SLO; Splitwise **1.4×** @ −20% cost | PD-disagg papers, not prompt-cache SKUs |

**Architecture-derived policy targets [inferred] — not vendor SLOs.** Clock = **TTFT** (prefix cache does not change ITL/TPOT). Reference mix = the module’s 8k tools+system + 400-token suffix. Prefix-hot = exact hit on that left span (suffix still prefills). Prefix-cold = miss / overflow / full prefill. Hits move **p50**; a **mixed-fleet** p99 is still miss-dominated — so p99 is split: hot p99 is the tail **among hits**, cold p99 is miss + queue + long prefill.

Calibration used as existence proofs, **not** copied into the table as SLOs: KVGov A100 2119-token **32.8 ms** hit / **149.6 ms** miss (self-hosted floor; 8k miss ≈ \(149.6 \times 8000/2119 \approx 565\) ms); Anthropic 10k many-shot **1.6 s → 1.1 s** (closest hosted 8k pair) and 100k **11.5 s → 2.4 s**; HiCache PR L2+L3 avg / p90 / p99 **2.34 / 6.56 / 18.44 s** (tail shape on a 25k-class bench — do not paste 18 s as an 8k SLO).

| Path | p50 | p95 | p99 | Mitigation (one line) |
| --- | --- | --- | --- | --- |
| **Prefix-hot, self-hosted APC** **[inferred]** | **p50 = 50 ms** | **p95 = 150 ms** | **p99 = 400 ms** | Stream first token; llm-d / Dynamo locality (naive kube round-robin destroys the hit) |
| **Prefix-hot, hosted prompt cache** **[inferred]** | **p50 = 1,000 ms** | **p95 = 2,000 ms** | **p99 = 3,500 ms** | Shard `prompt_cache_key` before ~15 RPM; Fireworks `x-session-affinity`; explicit breakpoint after stable prefix |
| **Prefix-cold, self-hosted** **[inferred]** | **p50 = 600 ms** | **p95 = 2,000 ms** | **p99 = 8,000 ms** | Chunked prefill (Sarathi 28.3× TBT if off); single-flight stampede; PD-disagg so miss prefill cannot stall ITL |
| **Prefix-cold, hosted** **[inferred]** | **p50 = 1,600 ms** | **p95 = 5,000 ms** | **p99 = 12,000 ms** | Pre-warm (`max_tokens: 0`); drop semantic on miss/timeout (do not wait on the embedder); timeout the cache API independently → uncached generate |

Decode-heavy workloads (short prompt, long output): prefix cache **does not** change ITL/TPOT. Output SKUs are unchanged. Speculative decoding stores draft+target KV — a different pool (Leviathan et al.: 2–3× on T5-XXL; not prompt cache).

#### 3.6 Throughput and back-pressure

Hit vs miss is a **prefill** capacity change, not a decode miracle.

| Source | Hit vs miss (as published) |
| --- | --- |
| Anthropic launch | 100k-token TTFT **11.5 s → 2.4 s** |
| LMCache | TTFT **1.9–8.1×** smaller; throughput **2.3–14×** (CPU-offload microbench) |
| CacheBlend | TTFT **2.2–3.3×**; throughput **2.8–5×** vs full recompute |
| llm-d blog | **57×** / **2×** vs naive LB (authors’ bench) |
| Mooncake | **+115% / +107%** requests A800/H800 |
| HiCache LMSYS | hit TTFT **−84%** vs recompute (R1 QA) |

**Stampede (thundering herd):** Anthropic N writes at 1.25×/2.0×; OpenAI GPT-5.6 implicit writes the **latest** (volatile) message; Gemini implicit wants similar prefixes in a short window — the inverse of a stampede is also a miss. Self-hosted: many replicas miss, all prefills, LRU evicts each other’s blocks. Dynamo `router_temperature > 0` and llm-d load scores exist to stop **herding**. Fireworks: `random()` canary splits a user across deployments and **destroys** session prefix hits; hash-sticky canary keeps the prefix on one side.

**Back-pressure design:** (1) admit on `prompt_cache_key` RPM (split shards before ~15); (2) bulkhead **semantic Redis** vs **prefix provider** vs **generate** — an embedder 5xx must not stall generate if policy is fail-open; (3) single-flight cold writes; (4) degrade: semantic off → exact prefix → uncached generate; (5) Mooncake early-reject rather than admit a request that will miss SLO.

**Capacity identity:** goodput = DistServe’s definition — max QPS with **both** TTFT and TPOT SLOs met for **>90%**. Cache hit rate without a miss-tail SLO is a vanity metric. Token hit rate \(= \mathrm{cache\_read}/(\mathrm{cache\_read}+\mathrm{cache\_write}+\mathrm{uncached\_in})\). Request hit rate = fraction with `cached_tokens > 0`.

#### 3.7 NFRs and explicit trade-offs

| NFR | Production stance | Competes with |
| --- | --- | --- |
| **Availability** | Prefix cache is **best-effort affinity**, not a replicated datastore. Circuit-break semantic index, cache API, and generator independently. Vertex SFT-style “no SLA” is not published for prompt cache either — do not quote a cache availability SKU. | Hit rate (sticky routing) vs tail ITL (herding) |
| **RPO** | Honest: hosted KV has **no dump**. RPO for prefix state ≈ **empty** after process kill / region fail. Gemini explicit is the exception (named resource in-region). LMCache/Mooncake/HiCache L3 **are** the checkpoint if the backend namespace is shared. LangGraph checkpointers persist **graph text**, not GPU KV. | Durability vs $ (Gemini storage rent; DRAM/SSD for Mooncake) |
| **RTO** | Failover region is **cold** (OpenAI: caches cannot cross regional boundaries). RTO = time to re-warm the working set (one TTL window of write SKUs) or prefetch from L3 (`best_effort` prefers TTFT over hit rate after deploy). Rolling deploy: new pods empty; TRT-LLM in-flight identical prefixes still **N prefills** until first terminates. | Deploy velocity vs write-cost budget |
| **Compliance** | Anthropic ZDR: in-memory KV+hash only — still **in RAM during TTL**. OpenAI ZDR orgs default `in_memory`, not `24h`. PII in the shared tools/system block is materialized for every same-salt/org hit. OpenAI regional **+10%**. VPC-SC: Gemini cache cannot leave the perimeter. | Latency (residency path) vs hit rate (cannot reuse a US-warm prefix in EU) |
| **Security vs sharing** | Org-level hosted cache **maximizes** tools-prefix hits and **opens** same-org timing. Per-tenant HMAC salt **closes** the channel and **duplicates** the 8k KV. | Cost (HBM × tenants) vs leak surface |
| **Correctness vs skip-LLM** | Semantic cache: false-positive = **wrong answer as truth**. Exact prefix: false prefix match is cryptographically a hash collision (do not use `xxhash` multi-tenant). | FAQ latency vs regulated / tool-using loops |

**RPO/RTO [inferred from architecture, not vendor RPO SKUs]:** RPO = last L3/LMCache blob or “none” on hosted APIs. RTO = alias to a warm replica (seconds, if one exists) vs full re-prefill (TTFT of the long prefix) vs **cannot restore** OpenAI KV after region fail (RTO becomes “send traffic and pay writes”).

---

### 4. Distributed Resilience & Security

#### 4.1 Durable execution: what CAN be checkpointed vs GPU-local

> ⚠️ Gap: research does not specify Temporal/Kafka product SKUs. Map the equivalent: cache-key + single-flight lease is the workflow handle; DLQ = poison prefix / semantic false-hit eval, not a silent serve.

| Layer | Checkpoint? | Restore after crash / region fail |
| --- | --- | --- |
| Hosted prompt cache | No dump API; Anthropic: no manual clear either | Cold |
| Gemini explicit | Named resource (`create`/`patch`/`delete`) | Same region/project; CMEK |
| vLLM GPU APC | Process memory | Empty after pod kill |
| vLLM OffloadingConnector | Host/FS/object | Survives process if backend does; hashes must be `sha256_cbor` |
| LMCache remote | First-class blobs | Yes — that is the product |
| Mooncake Store | Global KV pool | Yes; early-reject if restore would miss SLO |
| HiCache L3 | Backend-dependent | Yes iff shared `mooncake`/`hf3fs`/`nixl` namespace |
| LangGraph Sqlite/Postgres | Node outputs (text) | Restores text, then you **prefill again** |

**Replica locality.** Hosted KV is **machine-local**. OpenAI overflow ~**15 RPM**; `prompt_cache_key` is an affinity **hint**, not a lease. Bedrock cross-region may rewrite. Azure: no sharing **across subscriptions**. Self-hosted APC is **process-local** unless a KV connector exports blocks. Naive Kubernetes Service / round-robin **destroys** prefix locality — llm-d’s reason for existing. llm-d: on subscriber connect, replay buffered KVEvents to rebuild the index without waiting for live traffic.

**Rolling-deploy cold start.** New pods: empty GPU prefix caches; TTFT and write SKUs spike until the working set is rebuilt. HiCache L1/L2 miss is expected; L3 is the only tier that survives if the namespace is shared. Prefetch timeout defaults (2 s + 0.1 s/KiTok, cap 30 s) are the SLO valve. A model id, tokenizer, LoRA, or `cache_salt` change is a **full prefix miss**. There is **no** documented way to warm OpenAI/Anthropic caches except by sending traffic (or Anthropic `max_tokens: 0`). Plan a write-cost budget for the first TTL window after a prompt or model rollout.

**Multi-node radix.** SGLang does not share one radix across nodes by default. Dynamo’s KvIndexer is a **prefix tree of worker-reported events**, not a shared GPU radix. HiRadixTree does **not** continuously sync L3 metadata; L3 existence is queried at prefetch time.

**TTL vs stream length.** A 5-minute cache that starts at request begin and a 4-minute stream leaves ~1 minute for the next turn — a common agent-loop miss. Anthropic TTL refreshes on **use** (sliding). OpenAI 5.6: 30 minutes from **most recent write or reuse**.

#### 4.2 Failure taxonomy

| Class | Examples | Detection | Handling |
| --- | --- | --- | --- |
| **Transient** | Provider 429/5xx, Redis timeout, overflow miss (~15 RPM), HiCache prefetch timeout, Bedrock cross-region rewrite | Error rate, `cache_write` spike with traffic, Retry-After | Full-jitter retries on **idempotent** reads; single-flight writes; hedge replica |
| **Permanent** | 4xx auth, prefix below min (silent 0 cache fields), PTU-M no breakpoints, Together disable-cache flags ignored, TGI VLM auto-disables APC | Non-retryable code; both cache fields 0 on a 3k prompt against Haiku 4.5 | Fail closed on auth; **do not** retry a sub-floor prefix expecting hits; change model or lengthen prefix |
| **Poison pill (prefix)** | Timestamp/unsorted JSON in the marked block; attacker-controlled tool body inside the cached span; RAG chunk order before breakpoint; schema rollout vs 1h cached tools | `cache_write_tokens ≫ cached_tokens`; tool-call mismatch after schema rev | Canonical JSON; version string in tools block (no Anthropic purge API); validate tool schema **before** appending inside the cached span |
| **Poison pill (semantic)** | False-positive neighbor; InputSnatch using hits as an oracle; stale price/inventory; GPTCache threshold=1 | False-hit eval set; tenant TAG missing | Fail to exact prefix; TTL; never semantic-cache tool-using / regulated traffic |
| **Idempotency** | N parallel cold writes; double POST of Gemini `cachedContents.create`; LangGraph `key_func` ignoring tool version | Duplicate 1.25× bills; two cache resource names | Single-flight key = canonical prefix hash; Anthropic pre-warm once; Gemini create-if-not-exists by `displayName` |
| **Stale** | 5m TTL vs user think-time; Gemini explicit idle; compaction replacing earlier content; Fireworks oldest-first eviction | TTL miss after gap; storage $ with 0 reads | Anthropic 1h when gaps >5 min; delete unused Gemini caches; compact **after** last breakpoint |

**Idempotency keys:** prefix write `sha256(canonical_prefix)`; semantic `(tenant, model, policy_ver, locale, query_hash)`; generate `(tenant, request_id)` so a retried HTTP POST does not double-write semantic entries **and** does not double-bill.

#### 4.3 Circuit breaker (closed → open → half-open)

Independent breakers: **semantic index**, **prefix/cache API**, **generator**. A Redis HNSW storm must not starve generate (**bulkhead**). A cache-API 5xx must not block uncached generate.

```
        failures ≥ threshold or error-rate window
  ┌──────────┐  ─────────────────────────────────►  ┌──────────┐
  │  CLOSED  │                                       │   OPEN   │
  │ pass all │  success resets consecutive count     │ fail fast│
  └────┬─────┘                                       └────┬─────┘
       ▲                                                  │ cooldown elapsed
       │ trial success                                    ▼
       │                                            ┌──────────┐
       └──────────── trial OK ──────────────────────│ HALF-OPEN│
                    trial fail → OPEN               │ 1 probe  │
                                                    └──────────┘
```

**Thresholds [policy, not vendor SLO]:** trip semantic on embedder 5xx / Redis timeout; trip prefix-provider on 5xx and on **sustained write/read inversion** (thrash) if you treat that as a dependency fault; trip generate on 5xx/TPM. Cooldown tens of seconds. One probe in half-open.

**Fallback chain (cited policy):** **semantic off** → **exact prefix / hosted prompt cache** → **uncached generate** (full prefill). Never the reverse on a safety-critical path (do not “fail open” into semantic). Hedging: duplicate generate to a second replica on p99; cancel loser. Agent: on cache backend failure, surface `cache_degraded` — still generate.

#### 4.4 Enterprise security

**Zero-Trust MCP.** `tools/call` on `lookup_semantic` or `generate_cached` is a **prompt-and-answer exfil API**.

1. **Server-side identity.** `tenant_id` / salt / `prompt_cache_key` from verified token / `RunContext`, never from tool arguments the model filled. An omnibus `cache(prompt, tenant_id, salt)` is a leak primitive.
2. **Least privilege per tool.** `lookup_semantic` (FAQ corpus only) vs `generate_cached` vs `generate_uncached` vs `prewarm_cache` (admin). No tool that accepts a raw `cache_salt`.
3. **Stateless MCP + stateful KV.** Conversation memory in the checkpointer; GPU KV is engine-local. Do not store salts in the MCP session.
4. **No raw prefix echo** to unauthorized traces. Log hashes + `cached_tokens`, not medical notes that sat in the tools block.
5. **Hosted cache:** provider sees the prefix. Contract residency (OpenAI **+10%**; Azure Standard vs Global; Gemini VPC-SC). Fireworks dedicated shared cache is a **documented** timing residual — isolation key is mandatory for mutually distrustful tenants on one dedicated deployment.

**Isolation ladder:**

| System | Default sharing | Isolation primitive |
| --- | --- | --- |
| vLLM APC | Global content-addressed | `cache_salt` on first block; omit = share with everyone. CVE-**2025-46570** patched ≥**0.9.0** via salting |
| SGLang Radix | Global token-sequence tree | No first-class salt in the 2024 paper; partition engines for hostile tenants |
| OpenAI | Org-wide | Org + region; `prompt_cache_key` is **routing**, not confidentiality |
| Anthropic | Workspace (API/Foundry/Claude-on-AWS); org (Bedrock/GCP) | Cryptographic hash of prefix; never across orgs. Workspace split (2026-02) closes cross-workspace probing on API/Foundry/Claude-on-AWS |
| Azure | Subscription | No cross-subscription share |
| Gemini explicit | Project / cache resource | IAM; VPC-SC |
| Fireworks dedicated | **Shared by default** | `x-prompt-cache-isolation-key` |
| Together serverless | Fleet-shared best-effort | No customer isolation knob published |
| Semantic (Redis/GPTCache) | Whatever you put in the index | **TAG filter on tenant_id in the same `FT.SEARCH`**; missing filter = cross-talk |

**Timing side channels (KVGov).** A cache hit skips prefill → TTFT drops. KVGov breach threshold ratio **0.40**; measured **0.22** (A100) / **0.093** (Metal). Attacks: **EarlyBird** 100% ASR at `block_size=1` (V1 `block_size=16` makes free-text reconstruction ≈ \(10^{83}\) guesses; **template fields remain O(1)**); **PROMPTPEEK** 99–100% on SGLang radix; **InputSnatch** prefix 62% disease / 13.5% symptoms; semantic **43–100%** across 13 legal domains. CVE-2025-46570: ROC AUC **0.571** at 1-token prefix, **0.99** at 8 tokens. KVGov: HMAC-salt **100% → 0%** ASR (N=1000); Gaussian \(\sigma=20\) ms: PROMPTPEEK **0.5%**, InputSnatch still **100%** (membership, not magnitude). **Boundary salting** retains estimated **93%** of prefix-cache benefit — **research, not a vLLM flag**. Evolutionary tipping point: **31.6%** adversary prevalence. **CacheSolidarity:** isolate only suspicious prefixes; up to **70%** higher reuse vs isolate-everyone — complementary to HMAC, not a replacement for hostile multi-tenant.

**PII pipeline (detection → redaction → audit).** Applies **before** a prefix KV / hosted-cache write, **before** a semantic `put`, and **before** traces log content. Research §4.3 names the blast radius (medical notes, account numbers, retrieved docs in the shared tools/system block) but does **not** specify a DLP SKU, regex catalog, or NER model — detector choice below is architecture, not a vendor quote.

1. **Detection (regex + NER/classifier).** Scan the **cacheable left span** (tools+system and anything that would sit before the breakpoint), tool/RAG text about to be appended inside that span, and the **semantic answer** body. Regex: structured identifiers the research calls out as prefix poison (account numbers / PAN with Luhn, MRN-like tokens, email, phone, SSN). NER/classifier: PERSON, LOCATION, ORG, and free-text clinical/financial spans regex misses. Dual-gate because regex is cheap and high-precision on PANs; NER catches names in medical notes. If the classifier is down, **fail closed on cache writes** (still serve the authorized caller) — do not insert un-scanned text into reusable KV or the semantic index.

2. **Redaction.** Strip or tokenize PII from cacheable prefixes (`acct-****` / `[EMAIL_<hash12>]`) so the KV that APC / hosted prompt cache *is* does not materialize raw PII. Keep raw PII only **after** the cache breakpoint, in the non-reusable suffix (per-tenant RAG, user turn, live tool body). **Never** store raw PII in semantic answers — a false-positive hit returns that text verbatim as truth (InputSnatch already uses semantic hits as an oracle). Tenant policy: `strip` | `tokenize` | `block-from-cache` (return unredacted to the entitled caller; do not write). Unvalidated 5xx / attacker-controlled tool bodies are not redaction-exempt: schema-validate **then** DLP **then** append, or they freeze into the prefix until TTL. ZDR (Anthropic: in-memory KV+hash only) does **not** mean “not in RAM during TTL” — redaction is what stops reuse, not the ZDR paperwork.

3. **Audit trail (WORM).** Immutable log of detect/redact **decisions**, not values: `content_sha256` (pre- and post-redact), entity **types** + counts (EMAIL, PAN, PERSON, …), action (`strip`/`tokenize`/`block`/`none`), detector (`regex`|`ner`), confidence bucket — **never the raw span**. Correlate with `cid`, `tenant_id`, `cache_layer` (semantic|prefix|uncached), `salt_fingerprint` (not the salt), `prefix_hash` / `block_hash`, `pod_id`. This is the GDPR Art. 30 / incident record for “did we cache PII,” distinct from hit-attribution logs.

**Audit / immutable logs (cache attribution, keep with KVGov).** Hosted APIs do **not** expose “this hit was served from tenant B’s write” — you see `cached_tokens > 0`. Self-hosted: log `(block_hash, salt, pod_id, tenant_id)` on insert and hit. Chain-of-custody: `cid`, `tenant`, `cache_layer` (semantic|prefix|uncached), `cached_tokens`, `cache_write_tokens`, `prompt_cache_key` shard, `salt_fingerprint` (not the salt). **xxhash** in multi-tenant vLLM: do not use. ORIGAMI is an audit *scheduler*, not a SIEM.

**Semantic-cache governance.** TAG filters: **tenant + model-id + model-version + locale + safety-policy-version** in the **same** vector query. Threshold is not a confidentiality control. Fail-open (embedder 5xx → call LLM) vs fail-closed (cost cap) is a design-review choice — pick one and log it.

---

### 5. Production Enterprise Code

Self-contained stdlib. Optional Redis/HTTP wiring is commented. Run: `python cache_runtime.py`.

Fallback wired: **semantic → exact prefix (hosted/self) → uncached generate**. Salt is HMAC’d server-side. Prefix keys are canonical JSON (sorted keys, no clocks in the left span). Stampede: single-flight on cold prefix writes.

```python
#!/usr/bin/env python3
"""Cache-plane resilience: retries, breakers, semantic→prefix→uncached, key canon.

Stdlib only. Swap Fake* ports for RedisVL / vendor HTTP / vLLM.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

# Optional deps (not required to run this file):
#   import redis  # RedisVL SemanticCache; FT.SEARCH with TAG + KNN
#   import httpx  # OpenAI/Anthropic/Gemini clients


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = getattr(record, "correlation_id", "-")
        record.tenant_id = getattr(record, "tenant_id", "-")
        record.cache_layer = getattr(record, "cache_layer", "-")
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("cache")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"cid":"%(correlation_id)s","tenant":"%(tenant_id)s",'
            '"layer":"%(cache_layer)s","msg":"%(message)s"}'
        )
    )
    handler.addFilter(CorrelationFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


LOG = configure_logging()


def slog(
    level: int,
    msg: str,
    *,
    cid: str,
    tenant: str,
    layer: str = "-",
    **fields: object,
) -> None:
    extra = {
        "correlation_id": cid,
        "tenant_id": tenant,
        "cache_layer": layer,
    }
    LOG.log(level, "%s %s", msg, json.dumps(fields, default=str), extra=extra)


class TransientError(Exception):
    """429, 5xx, timeout, circuit open — safe to retry idempotent reads."""


class PermanentError(Exception):
    """4xx auth, sub-floor prefix policy, poison config — do not retry."""


def retry_with_jitter(
    fn: Callable[[], object],
    *,
    cid: str,
    tenant: str,
    op: str,
    attempts: int = 4,
    base_s: float = 0.05,
    cap_s: float = 1.0,
) -> object:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except PermanentError:
            raise
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            sleep = min(cap_s, base_s * (2**i))
            sleep = random.uniform(0, sleep)  # full jitter (AWS-style)
            slog(
                logging.WARNING, "retry",
                cid=cid, tenant=tenant, op=op,
                attempt=i + 1, sleep_s=round(sleep, 3), err=str(exc),
            )
            time.sleep(sleep)
    assert last is not None
    raise last


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(TransientError):
    pass


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    cooldown_s: float = 15.0
    half_open_probes: int = 1
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _probes_used: int = 0

    def allow(self) -> None:
        now = time.monotonic()
        if self._state is CircuitState.OPEN:
            if now - self._opened_at >= self.cooldown_s:
                self._state = CircuitState.HALF_OPEN
                self._probes_used = 0
            else:
                raise CircuitOpenError(f"circuit_open:{self.name}")
        if self._state is CircuitState.HALF_OPEN:
            if self._probes_used >= self.half_open_probes:
                raise CircuitOpenError(f"circuit_half_open_busy:{self.name}")
            self._probes_used += 1

    def record_success(self) -> None:
        self._failures = 0
        self._state = CircuitState.CLOSED
        self._probes_used = 0

    def record_failure(self) -> None:
        self._failures += 1
        if self._state is CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            return
        if self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()


@dataclass(frozen=True)
class Authz:
    tenant_id: str
    actor: str
    # Server-side: HMAC salt + routing shard. NEVER parsed from model JSON.
    allow_semantic: bool
    traffic_class: str  # faq | agent — semantic forbidden on agent


@dataclass(frozen=True)
class PromptParts:
    model: str
    tools: list[dict]
    system: str
    user: str
    params: dict

    def prefix_dict(self) -> dict:
        """Left-hand exact-cache span: no clocks, tools order pinned by name."""
        tools = sorted(self.tools, key=lambda t: json.dumps(t, sort_keys=True))
        return {
            "model": self.model,
            "tools": tools,
            "system": self.system,
            "params": self.params,
        }


def canonical_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def prefix_hash(parts: PromptParts) -> str:
    return hashlib.sha256(canonical_json(parts.prefix_dict()).encode()).hexdigest()


def tenant_salt(server_secret: bytes, tenant_id: str) -> str:
    """vLLM-class secret: 256-bit HMAC, not a user name. Gateway-only."""
    digest = hmac.new(server_secret, tenant_id.encode(), hashlib.sha256).digest()
    return hashlib.sha256(digest).hexdigest()  # 64 hex chars > 43 b64 / 256-bit bar


def routing_key(parts: PromptParts, tenant_id: str, shard: int) -> str:
    """OpenAI-style affinity hint — NOT a confidentiality boundary."""
    material = f"{parts.model}:{tenant_id}:{prefix_hash(parts)[:16]}"
    return f"{material}:shard-{shard}"


def estimate_tokens(text: str) -> int:
    """Coarse visible-token stand-in (~4 chars/tok). Production: real tokenizer."""
    return max(1, len(text) // 4)


class SemanticCache(Protocol):
    name: str

    def get(self, tenant_id: str, model: str, policy_ver: str, query: str) -> str | None: ...

    def put(self, tenant_id: str, model: str, policy_ver: str, query: str, answer: str) -> None: ...


class PrefixGenerator(Protocol):
    """Hosted prompt cache or self-hosted APC. Still runs decode."""

    name: str

    def complete(
        self, parts: PromptParts, salt: str, route_key: str, cid: str
    ) -> tuple[str, int, int]:
        """Returns (text, cached_tokens, cache_write_tokens)."""
        ...


class UncachedGenerator(Protocol):
    name: str

    def complete(self, parts: PromptParts, cid: str) -> str: ...


@dataclass
class SingleFlight:
    """Serialize cold prefix writes so N parallel requests are not N 1.25× writes."""

    _locks: dict[str, threading.Lock] = field(default_factory=dict)
    _guard: threading.Lock = field(default_factory=threading.Lock)

    def lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]


@dataclass
class InMemorySemantic:
    """Process-local stand-in. Production: Redis TAG+KNN, distance 0.1, TTL set."""

    name: str = "semantic"
    ttl_s: float = 300.0
    fail: bool = False
    _store: dict[str, tuple[float, str]] = field(default_factory=dict)

    def _key(self, tenant_id: str, model: str, policy_ver: str, query: str) -> str:
        raw = canonical_json(
            {"tenant": tenant_id, "model": model, "policy": policy_ver, "q": query}
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, tenant_id: str, model: str, policy_ver: str, query: str) -> str | None:
        if self.fail:
            raise TransientError("semantic_backend_down")
        rec = self._store.get(self._key(tenant_id, model, policy_ver, query))
        if rec is None:
            return None
        ts, answer = rec
        if time.monotonic() - ts > self.ttl_s:
            return None
        return answer

    def put(self, tenant_id: str, model: str, policy_ver: str, query: str, answer: str) -> None:
        if self.fail:
            return
        self._store[self._key(tenant_id, model, policy_ver, query)] = (
            time.monotonic(),
            answer,
        )


@dataclass
class FakePrefixGen:
    name: str = "prefix"
    min_tokens: int = 1024
    fail: bool = False
    _warm: set[str] = field(default_factory=set)

    def complete(
        self, parts: PromptParts, salt: str, route_key: str, cid: str
    ) -> tuple[str, int, int]:
        if self.fail:
            raise TransientError("prefix_provider_down")
        _ = (route_key, cid)
        left = canonical_json(parts.prefix_dict())
        n = estimate_tokens(left)
        if n < self.min_tokens:
            # Hosted silent no-op: request succeeds, cache_* = 0.
            text = f"[uncached-subfloor] {parts.user[:80]}"
            return text, 0, 0
        key = f"{salt}:{prefix_hash(parts)}"
        if key in self._warm:
            text = f"[prefix-hit] {parts.user[:80]}"
            return text, n, 0
        self._warm.add(key)
        text = f"[prefix-write] {parts.user[:80]}"
        return text, 0, n


@dataclass
class FakeUncachedGen:
    name: str = "uncached"
    fail: bool = False

    def complete(self, parts: PromptParts, cid: str) -> str:
        if self.fail:
            raise TransientError("generate_down")
        _ = cid
        return f"[uncached] {parts.user[:80]}"


@dataclass
class CachedResult:
    text: str
    layer: str  # semantic | prefix | uncached | deterministic
    degraded: bool
    cached_tokens: int
    cache_write_tokens: int


class CacheRuntime:
    def __init__(
        self,
        server_secret: bytes,
        semantic: SemanticCache,
        prefix_gen: PrefixGenerator,
        uncached_gen: UncachedGenerator,
        policy_ver: str = "safety-2026-09",
        min_prefix_tokens: int = 1024,
        shards: int = 4,
    ) -> None:
        self.server_secret = server_secret
        self.semantic = semantic
        self.prefix_gen = prefix_gen
        self.uncached_gen = uncached_gen
        self.policy_ver = policy_ver
        self.min_prefix_tokens = min_prefix_tokens
        self.shards = shards
        self.flight = SingleFlight()
        self.breakers = {
            "semantic": CircuitBreaker("semantic"),
            "prefix": CircuitBreaker("prefix"),
            "uncached": CircuitBreaker("uncached"),
        }

    def _shard(self, tenant_id: str) -> int:
        h = int(hashlib.sha256(tenant_id.encode()).hexdigest(), 16)
        return h % self.shards

    def _call_breaker(self, name: str, fn: Callable[[], object], cid: str, tenant: str, op: str) -> object:
        br = self.breakers[name]

        def _op() -> object:
            br.allow()
            try:
                out = fn()
            except PermanentError:
                br.record_failure()
                raise
            except Exception as exc:
                br.record_failure()
                raise TransientError(str(exc)) from exc
            br.record_success()
            return out

        return retry_with_jitter(_op, cid=cid, tenant=tenant, op=op)

    def complete(self, parts: PromptParts, authz: Authz) -> CachedResult:
        cid = str(uuid.uuid4())
        salt = tenant_salt(self.server_secret, authz.tenant_id)
        salt_fp = hashlib.sha256(salt.encode()).hexdigest()[:12]
        route = routing_key(parts, authz.tenant_id, self._shard(authz.tenant_id))
        left_tok = estimate_tokens(canonical_json(parts.prefix_dict()))
        slog(
            logging.INFO, "request_start",
            cid=cid, tenant=authz.tenant_id,
            model=parts.model, prefix_hash=prefix_hash(parts)[:16],
            salt_fp=salt_fp, route=route, prefix_tok=left_tok,
            class_=authz.traffic_class,
        )

        # L0 semantic — FAQ only, tenant-tagged, never on agent/tool loops.
        if authz.allow_semantic and authz.traffic_class == "faq":
            try:
                hit = self._call_breaker(
                    "semantic",
                    lambda: self.semantic.get(
                        authz.tenant_id, parts.model, self.policy_ver, parts.user
                    ),
                    cid, authz.tenant_id, "semantic_get",
                )
                if isinstance(hit, str) and hit:
                    slog(
                        logging.INFO, "semantic_hit",
                        cid=cid, tenant=authz.tenant_id, layer="semantic",
                    )
                    return CachedResult(hit, "semantic", False, 0, 0)
            except (TransientError, PermanentError) as exc:
                slog(
                    logging.ERROR, "semantic_failed",
                    cid=cid, tenant=authz.tenant_id, layer="semantic", err=str(exc),
                )

        # L1 exact prefix — still decode; stampede-serialized on cold key.
        flight_key = f"{salt}:{prefix_hash(parts)}"
        try:
            with self.flight.lock_for(flight_key):
                text, cached, wrote = self._call_breaker(
                    "prefix",
                    lambda: self.prefix_gen.complete(parts, salt, route, cid),
                    cid, authz.tenant_id, "prefix_complete",
                )  # type: ignore[misc]
            assert isinstance(text, str)
            if authz.allow_semantic and authz.traffic_class == "faq":
                self.semantic.put(
                    authz.tenant_id, parts.model, self.policy_ver, parts.user, text
                )
            slog(
                logging.INFO, "prefix_done",
                cid=cid, tenant=authz.tenant_id, layer="prefix",
                cached_tokens=cached, cache_write_tokens=wrote,
                subfloor=left_tok < self.min_prefix_tokens,
            )
            return CachedResult(text, "prefix", False, int(cached), int(wrote))
        except (TransientError, PermanentError) as exc:
            slog(
                logging.ERROR, "prefix_failed",
                cid=cid, tenant=authz.tenant_id, layer="prefix", err=str(exc),
            )

        # L2 uncached full prefill
        try:
            text = self._call_breaker(
                "uncached",
                lambda: self.uncached_gen.complete(parts, cid),
                cid, authz.tenant_id, "uncached_complete",
            )
            assert isinstance(text, str)
            slog(
                logging.WARNING, "fallback_uncached",
                cid=cid, tenant=authz.tenant_id, layer="uncached",
            )
            return CachedResult(text, "uncached", True, 0, 0)
        except (TransientError, PermanentError) as exc:
            slog(
                logging.ERROR, "uncached_failed",
                cid=cid, tenant=authz.tenant_id, layer="uncached", err=str(exc),
            )
        return CachedResult(
            "Generation unavailable. Deterministic fallback: retry later.",
            "deterministic",
            True,
            0,
            0,
        )


if __name__ == "__main__":
    secret = hashlib.sha256(b"gateway-secret-not-a-username").digest()
    parts = PromptParts(
        model="gpt-5.6-luna",
        tools=[{"name": "search_kb", "schema": {"type": "object"}}],
        system="You are the support agent. " + ("policy " * 900),
        user="What is the refund window?",
        params={"temperature": 0, "reasoning.effort": "low"},
    )
    authz = Authz(
        tenant_id="acme", actor="u1", allow_semantic=True, traffic_class="faq",
    )
    runtime = CacheRuntime(
        secret,
        semantic=InMemorySemantic(),
        prefix_gen=FakePrefixGen(),
        uncached_gen=FakeUncachedGen(),
    )
    first = runtime.complete(parts, authz)
    second = runtime.complete(parts, authz)
    other = PromptParts(
        model=parts.model, tools=parts.tools, system=parts.system,
        user="How do I reset MFA?", params=parts.params,
    )
    prefix_reuse = runtime.complete(other, authz)
    agent = Authz(
        tenant_id="acme", actor="u1", allow_semantic=True, traffic_class="agent",
    )
    toolish = runtime.complete(parts, agent)
    degraded = CacheRuntime(
        secret,
        semantic=InMemorySemantic(fail=True),
        prefix_gen=FakePrefixGen(fail=True),
        uncached_gen=FakeUncachedGen(),
    ).complete(parts, authz)
    print(json.dumps({
        "first_layer": first.layer,
        "first_write_tokens": first.cache_write_tokens,
        "second_layer": second.layer,
        "semantic_second": second.layer == "semantic",
        "prefix_reuse_cached_tokens": prefix_reuse.cached_tokens,
        "agent_skips_semantic": toolish.layer != "semantic",
        "degraded_layer": degraded.layer,
        "degraded": degraded.degraded,
        "salt_differs_by_tenant": tenant_salt(secret, "acme") != tenant_salt(secret, "other"),
    }, indent=2))
```

**Wired here:** full-jitter retries; closed→open→half-open breakers on **semantic**, **prefix provider**, and **uncached generate**; fallback **semantic → prefix → uncached → deterministic**; HMAC `cache_salt` from gateway secret (never client JSON); canonical prefix JSON (sorted keys, tools pinned, clocks kept out of the left span); `prompt_cache_key`-style shard; single-flight cold writes; JSON logs with `cid`+`tenant`+`layer`+`salt_fp` (not the salt). Semantic is skipped for `traffic_class=agent`. Sub-floor prefixes still call the prefix port (hosted silent no-op). Real RedisVL: `FT.SEARCH` with `TAG tenant_id` **and** KNN in one query; default distance **0.1**. Real vLLM: pass gateway salt on the first block; `sha256_cbor`; never `xxhash` multi-tenant.

---

### 6. Architectural System Design Scenarios

#### Scenario A — Multi-tenant agent with shared 8k tools prefix (15–200 RPM aggregate)

**Problem.** B2B copilot. 8k-token tool schemas + system are identical across tenants; per-tenant RAG/PII lives in the suffix. Aggregate 15–200 RPM; some tenants burst past 15 RPM alone. Requirements: SOC 2 isolation, tools-prefix TTFT after warmup, no cross-tenant timing oracle, stampede-safe fan-out on the first turn of a workflow. Do **not** put retrieved tenant docs before the breakpoint.

**Proposed architecture:**

```
  ┌─────────────┐   ┌─────────────────────────────────────────────────┐
  │ IdP / PEP   │──▶│ CONTROL: HMAC salt = HMAC(K, tenant_id)         │
  │ JWT → tenant│   │   breakpoint AFTER tools+system                 │
  │             │   │   prompt_cache_key = wf:tenant:shard-{n}        │
  │             │   │   split shard before ~15 RPM / key              │
  │             │   │   single-flight / Anthropic max_tokens:0 warm   │
  └─────────────┘   └──────────────────┬──────────────────────────────┘
                                       ▼
                    ┌──────────────────────────────────────────────────┐
                    │ Hosted: Anthropic workspace-per-tenant OR        │
                    │ OpenAI 5.6 explicit breakpoints + 30m TTL        │
                    │ Self-hosted alt: vLLM APC + llm-d/Dynamo         │
                    │   salt-per-tenant ⇒ tools KV NOT shared unless   │
                    │   KVGov boundary salt (not a vLLM flag) or a     │
                    │   dedicated “public prefix” engine               │
                    └──────────────────┬───────────────────────────────┘
                                       ▼
                    ┌──────────────────────────────────────────────────┐
                    │ DATA: tenant docs / PII AFTER the breakpoint     │
                    │ Decode on suffix; output billed full SKU         │
                    │ Telemetry: cached_tokens AND cache_write_tokens  │
                    │ Audit: salt_fp + prefix_hash + pod, not raw KV   │
                    └──────────────────────────────────────────────────┘
```

**Technology choices:** Hosted Anthropic 5m or OpenAI 5.6 explicit on the stable tools block (implicit-on-latest-message is the 1.25× thrash trap). `prompt_cache_key` shard above ~15 RPM. Pre-warm Anthropic `max_tokens: 0` before fan-out. Self-hosted if you need CacheBlend, custom salt, or no 15 RPM cap: vLLM `sha256_cbor` + gateway HMAC salt + llm-d precise scorer; hostile tenants → **separate engines**. Sonnet 5 5m economics **[inferred §3.3A]: ~$6.42 / 1k** at 8k+400/400 with one write vs **$20.80** uncached.

**Trade-off matrix:**

| Axis | **A1 Hosted explicit breakpoint + workspace/org isolation (recommended for bursty SaaS)** | **A2 Self-hosted vLLM APC + HMAC salt + llm-d** | **A3 Shared unsalted APC / one org key for all tenants** |
| --- | --- | --- | --- |
| **Cost** | Sonnet 5 **[inferred] ~$6.4/1k** at 1 write+999 reads; rewrite-every-10 **~$8.24/1k**; 1.25× stampede if you skip pre-warm | GPU-hour; prefix hit cuts TTFT / batch size, not a 0.1× invoice | Cheapest HBM (one 4.13 GB Llama-405B 8k prefix) — paid in leak risk |
| **Latency** | Hit: Anthropic-class 79% TTFT cut on 100k; miss: full prefill + ~15 RPM overflow | High if affinity works; rolling deploy cold until L3/LMCache | Best hit rate, worst threat model |
| **Ops complexity** | Breakpoint discipline; 20-block lookback; no purge API | Dynamo/llm-d, salt rotation, `sha256_cbor` across upgrades | Lowest ops, highest incident cost |
| **Security posture** | Workspace-per-tenant (Anthropic API) closes cross-workspace probing; same-org timing remains if you share a workspace | HMAC salt; CVE-2025-46570 if `<0.9.0`; boundary salt 93% sharing is **research** | TTFT oracle (KVGov 0.22); EarlyBird template O(1) |
| **Scalability ceiling** | 4 breakpoints; shard keys; regional cache cannot DR-warm | Multi-node index; HiCache L3 for restart; LPM→FCFS if queue>128 | Herd onto one replica; ITL explodes |

**Decision.** **A1 wins** when GPU ops are not a core competency and tenants can be workspace-isolated (or Azure-subscription-isolated). Put tools+system left of the breakpoint, PII right, shard `prompt_cache_key`, pre-warm. **A2 wins** for steady GPU utilization, CacheBlend RAG, or hostile tenants you will not put on one org cache. **A3 fails** the design review: shared unsalted KV is a prompt leak, not a cache-tuning issue. Do not buy PD-disagg to save the 1.25× write SKU; buy it so a **miss** prefill cannot stall in-flight decodes (Sarathi 28.3× TBT). Prompt cache so the **hit** never prefills. They stack.

#### Scenario B — FAQ semantic cache vs exact-only prefix

**Problem.** Public help-center: high-volume paraphrases of ~200 gold answers (“reset password”, “refund window”). Peak QPS high; answers are policy text that changes weekly, not live balances. A second surface is an **authenticated agent** with tools (ticket state, wallet). One stack is proposed “because both are caching.”

**Proposed architecture:**

```
  ┌──────────────┐    ┌─────────────────────────────────────────────┐
  │ Public FAQ   │───▶│ L0 RedisVL/LangCache  TAG tenant+model+     │
  │ paraphrases  │    │     policy_ver  distance from eval (start   │
  │              │    │     cosine 0.88; tighten if “refund” hits   │
  │              │    │     “invoice upload”)  short TTL            │
  └──────────────┘    └──────────────────┬──────────────────────────┘
                     miss                ▼
                    ┌──────────────────────────────────────────────┐
                    │ L1 hosted/self prefix cache on tools+policy  │
                    │ L2 uncached generate → write L0+L1           │
                    └──────────────────────────────────────────────┘

  ┌──────────────┐    ┌─────────────────────────────────────────────┐
  │ Agent+tools  │───▶│ Semantic OFF. Exact prefix only.            │
  │ live state   │    │ Breakpoint after tools; live tool results   │
  └──────────────┘    │ AFTER breakpoint. No answer reuse.          │
                      └─────────────────────────────────────────────┘
```

**Technology choices:** Redis one `FT.SEARCH` with TAG + KNN (sub-ms in-index; 5–20 ms if you count embedding). GPTCache threshold calibrated on a **labeled false-hit set** (their 0–1 scale is not cosine). Measure **false-hit rate**, not just hit rate. Exact prefix already discounts the 8k tools block at 0.1× — semantic only wins if you skip **decode**, which is the dangerous part. TTL on FAQ; `ttl=None` in RedisVL is “never expire” — do not use that on policy text. InputSnatch extracted legal-domain prompts from semantic caches at **43–100%** ASR — keep semantic off regulated corpora.

**Trade-off matrix:**

| Axis | **B1 Hybrid: tagged semantic FAQ + exact prefix for agents (recommended)** | **B2 Exact prefix / prompt cache only** | **B3 Semantic-only (skip LLM on any near neighbor)** |
| --- | --- | --- | --- |
| **Cost** | FAQ hits ≈ embed+$0 decode; agent still 0.1× prefix | Sonnet/OpenAI prefix economics; decode always billed | Near-zero on hit; silent quality/compliance cost on false hit |
| **Latency** | FAQ: 2–4× typical, 50–100× optimal (Redis blog); agent: prefix TTFT | Prefix TTFT only; no skip-LLM | Fastest — and wrong when threshold is loose |
| **Ops complexity** | Two evals (false-hit + `cached_tokens`); embedder breaker | Breakpoint hygiene only | Index + embedder + threshold drift |
| **Security posture** | TAG in the same query; semantic off for tools/PII | Exact match cannot serve another user’s answer | Highest leak/poison; missing TAG = cross-talk; InputSnatch |
| **Scalability ceiling** | Horizontal Redis; prefix still ~15 RPM / key on OpenAI | 15 RPM / LPM / replica pin | HNSW scale; not a substitute for prefix on long tools |

**Decision.** **B1 wins**: semantic is a **FAQ product** with a gold eval set and a short TTL; exact prefix is the **agent product**. **B2** is the right call when paraphrase volume is low or policy is safety-critical — you already get 0.1× on the tools block without serving yesterday’s answer. **B3** fails on tool-using loops, regulated text, multi-tenant without TAG filters, and model/policy version changes (cached text was sampled from model A). When prefix cache already covers cost, semantic only pays if skipping decode is worth the false-hit risk.

---

## Common Failure Modes

| Failure | Cause | Detection | Mitigation |
| --- | --- | --- | --- |
| **Prefix thrash (silent 1.25× loop)** | Timestamp / unsorted JSON / tool reorder / implicit breakpoint on latest message | `cache_write_tokens ≫ cached_tokens`; both Anthropic cache fields 0 | Move clocks after breakpoint; canonical JSON; explicit breakpoint on tools+system; `allowed_tools` not list edits |
| **Sub-floor silent no-op** | OpenAI <1,024 visible; Haiku 4.5 <4,096 | Cache usage = 0, no error | Lengthen stable instructions; pick a model whose floor you meet |
| **Stampede** | N parallel cold prefixes; entry ready only after first response begins | Write spike, near-zero reads in the same minute | Single-flight; Anthropic `max_tokens: 0` pre-warm; `mode=explicit` |
| **Overflow miss** | OpenAI/Azure ~15 RPM per key+prefix | RPM/key up, `cached_tokens` down | Shard `prompt_cache_key`; Fireworks hash-sticky, not `random()` |
| **Affinity vs geo** | Regional processing; kube round-robin; Fireworks canary split | Hits die after region pin or deploy | Regional KV indexes; llm-d/Dynamo; session affinity |
| **Rolling-deploy cold** | New pods empty; TRT-LLM no reuse until first request **terminates** | TTFT + write SKU spike post-rollout | HiCache L3 shared ns; write-cost budget; serial first-turn |
| **Semantic false hit** | Threshold too loose; GPTCache 1=all neighbors; no TTL | Gold FAQ eval false-hit rate | Distance from eval (0.88 cosine start); TTL; TAG tenant+model+policy |
| **Stale tool / schema** | 1h cached tools vs new runtime schema; no purge API | Undefined tool-call behavior | Version string in tools block; Gemini `delete`; wait TTL |
| **Dashboards that lie** | 10k prefix + 50-token unique q → 99% token hit | Token hit high, user quality flat | Board token **and** request hit **and** write/read ratio |
| **Decode-heavy no win** | Long output SKU; TTFT 50 ms, decode 10 s | $ unchanged | Do not buy prompt cache for summarization output tokens |
| **Truncation kills hits** | Sliding-window context rewrite | Hit rate collapse after “clever” trim | LMCache: truncation destroys prefix identity |
| **Timing leak as “perf win”** | Unsalted vLLM; dedicated Fireworks shared cache | TTFT bimodal by tenant | HMAC salt; isolation key; ≥vLLM 0.9.0 |
| **xxhash collision** | Multi-tenant `xxhash` APC | Undefined / leak (vendor warning) | `sha256_cbor` |
| **Gemini storage idle tax** | Explicit cache, low QPS | Storage $ > uncached input (§3.3E) | Implicit, or delete; do not 24h-rent a unread 100k corpus |
| **Batch / PTU gap** | Bedrock batch: no prompt cache; PTU-M no breakpoints; 4o-era Batch no discount | Writes or 0 cache fields | Online path for cache; do not plan 0.1× on batch |

---

## Key Takeaways

- Caching is **five layers**. Intra-request KV ≠ cross-request prefix ≠ hosted prompt cache (KV you never see) ≠ semantic (skip LLM, return text) ≠ LangGraph node cache.
- **Control plane vs data plane.** Keys, breakpoints, HMAC salt, and affinity routing do not belong in the client. KV is a **materialization of the prompt**, not scratch.
- Exact match is **from the left**. One timestamp before the breakpoint is a 1.25× write forever. Break-even \(n \ge (W-R)/(1-R)\): **2** at 1.25/0.1, **3** at 2.0/0.1.
- **p50 is hits; p99 is misses.** Vendors do not publish cache p99. Overflow (~15 RPM), stampede, and rolling-deploy cold start **are** the tail.
- Hosted cache is **ephemeral** (no dump, no cross-region warm). LMCache/Mooncake/HiCache L3 are the durable KV story. RPO is usually “empty.”
- **Salt or leak.** CVE-2025-46570; KVGov TTFT ratio 0.22. `prompt_cache_key` is not a tenant wall. Semantic TAG in the **same** query or you cross-talk.
- Fallback **semantic (FAQ only) → exact prefix → uncached generate**. Semantic is wrong for tool-using, regulated, and live-state traffic.
- Skip RAG under **~200k tokens** if prompt-cache economics win. Do not put unstable RAG chunks before the breakpoint unless you have CacheBlend.

---

## Interview Q&A

**Q1. Explain production LLM caching to someone who only knows Redis.**  
I split five caches. The GPU KV cache is scratch paper for the request still running. Prefix / hosted prompt cache is exact left-to-right reuse of that scratch across requests — you still generate the suffix. Semantic cache is Redis-with-vectors of **answers**, and a hit skips the model. Application cache is exact memoization of a node. I never hash the raw prompt in the client and call that “affinity.”

**Q2. KV cache vs prefix cache vs prompt cache vs semantic — one sentence each.**  
KV: this sequence’s K,V so decode is not prefill. Prefix/APC: hashed token blocks shared across requests. Hosted prompt cache: the same exact-prefix KV as a billed product (1.25× write / 0.1× read on GPT-5.6 / Anthropic 5m). Semantic: embedding kNN returns a previous completion.

**Q3. Where do you put the breakpoint on an 8k tools agent?**  
After tools and static system, before timestamps, tenant RAG, and the latest user/tool message. Anthropic looks back 20 content blocks and writes **only** at the marker — a timestamp on block 6 is infinite writes. OpenAI GPT-5.6 implicit marks the **latest** message; I switch to explicit so I do not pay 1.25× to cache volatility.

**Q4. Give me the cost model for 1,000 agent calls.**  
8k prefix + 400 suffix + 400 out. Sonnet 5 uncached **$20.80/1k**. One 5m write + 999 reads **≈ $6.42/1k inferred**. Rewrite every 10 **≈ $8.24**. Luna **≈ $0.72 vs $2.16**. Break-even is **2** hits at 1.25/0.1. Gemini explicit 100k corpus idle 24 h is **$11** storage+create with zero reads — worse than not caching.

**Q5. What SLO do you put in the contract?**  
I do **not** quote a vendor cache p99 — nobody publishes one. I SLO prefix-hot TTFT separately from miss+prefill. I treat Anthropic 11.5 s→2.4 s and KVGov 149.6→32.8 ms as existence proofs. I shard before ~15 RPM, circuit-break Redis independently of the FM, and I accept that p99 is a **miss**.

**Q6. Cache stampede on a cold workflow fan-out — what do you do?**  
Anthropic: N identical prefixes ⇒ N writes because the entry exists only after the first response begins. I serialize a `max_tokens: 0` pre-warm, then fan out. OpenAI: explicit mode on the stable prefix. Self-hosted: single-flight the prefix hash and pin one replica before opening.

**Q7. Multi-tenant vLLM without salt shipped. What happened?**  
The cache is globally content-addressed. TTFT is an oracle (KVGov 0.22; CVE-2025-46570 AUC 0.99 at 8 tokens). I inject `HMAC(server_secret, tenant_id)` at the gateway, upgrade to ≥0.9.0, ban `xxhash`, and I do not take a client `cache_salt`. Hostile tenants get separate engines. Boundary salt (93% sharing) is a paper, not a flag.

**Q8. Why did token hit rate look like 99% while we still paid 1.25×?**  
A huge static prefix plus a tiny unique suffix is 99% token hit and 100% request hit after warmup even if every *question* is unique. Or we thrashed: high `cache_write_tokens`, zero `cached_tokens`. I board both meters plus write/read ratio and RPM per key.

**Q9. FAQ bot vs wallet agent — same semantic cache?**  
No. FAQ with a labeled false-hit set, tenant TAG, short TTL, start cosine 0.88. Wallet/agent: live tool state — a paraphrase of “what’s my balance?” must not return another user’s number. Exact prefix still discounts the tools block at 0.1× without skipping decode.

**Q10. We failed over to EU and prompt cache “broke.”**  
OpenAI caches cannot cross regional processing boundaries. Anthropic/hosted KV have no dump API. DR that assumes a warm prefix in the failover region is wrong. RTO is re-warm (write SKUs) or a self-hosted L3 in that region. Regional processing is also **+10%** on eligible models from 2026-03-05.

**Q11. Dynamo vs round-robin vs `prompt_cache_key` — pick.**  
Kube round-robin scatters prefixes (llm-d’s 57× blog is the cautionary tale). Hosted: stable `prompt_cache_key` shards, split before 15 RPM. Self-hosted: precise prefix scorer or Dynamo overlap credits; temperature >0 to avoid herding. SGLang LPM is in-process admission, not a fleet router, and it **falls back to FCFS above 128** waiting.

**Q12. Zero-Trust MCP around cache tools — failure mode?**  
An omnibus `cache(prompt, tenant_id, salt)` the model fills. That is a timing-oracle and data-exfil API. I split lookup_semantic / generate_cached / generate_uncached / prewarm, take identity from the verified token, HMAC salt in the gateway, log salt fingerprints and block hashes, and I keep PII out of the cached left span.

---

## Key Numbers to Memorize

### Layers / algorithms
| Number | What |
| --- | --- |
| **16 / 128 / 256** | vLLM V1 block tokens; TRT-LLM default `tokens_per_block`; LMCache chunk default |
| **<4% / 2–4× / 24×** | PagedAttention waste; vLLM vs Orca/FT; vs HF (paging+batching) |
| **516.096 / 70.272 / 327.680 KB/tok** | Llama-405B GQA / DeepSeek-V3 MLA / Qwen-72B GQA (BF16) |
| **≈4.13 GB / 0.56 GB** | 8k prefix KV Llama-405B vs DeepSeek-V3 |
| **93.3% / 8× / 93%** | MLA vs 67B MHA; GQA 64Q/8KV vs MHA; KVGov boundary-salt retention (research) |
| **O(n/B)** | vLLM chained-hash longest prefix; hit on block N ⇒ 0…N−1 |
| **>128 waiting → FCFS** | SGLang LPM complexity valve |
| **6 µs** | TGI v3 prefix-match overhead (their number) |
| **<15%** | CacheBlend recompute tokens |
| **5× / 6.4× / 3.7×** | SGLang LMSYS / NeurIPS throughput / latency caps |
| **28.3× TBT** | Sarathi naive hybrid batch vs decode-only |
| **7.4× / 12.6× / >90%** | DistServe requests or tighter SLO; dual-SLO fraction |
| **<7% / 0.8% vs 3%** | Splitwise KV transfer vs prompt compute; overlapped vs serialized E2E |
| **~200k tok / ~500 pages** | Anthropic: skip RAG, cache the corpus |
| **$1.02 / MTok** | Anthropic contextualize-with-cache (their mix) |

### $ / multipliers / minima
| Number | What |
| --- | --- |
| **1.25× / 0.1× / 2.0×** | GPT-5.6 & Anthropic 5m write / read / Anthropic 1h write |
| **0.025× / ≈0.032 / 0.5×** | Fable/Mythos 5.1 hit; DeepSeek hit/miss; Fireworks default |
| **2 / 3** | Break-even n at 1.25/0.1 and 2.0/0.1 |
| **1.35× vs 2×; 2.15× vs 10×** | OpenAI official 1 write+1 read; 1 write+9 reads |
| **1,024 / 512–4,096 / 4,096 / 6,144** | OpenAI 5.6+ visible min; Anthropic by model; Haiku 4.5 floor; Gemini 3.x Flash/Pro implicit |
| **4 / 50 / 20** | Max writes (OpenAI/Anthropic); OpenAI read lookback breakpoints; Anthropic content-block lookback |
| **30m / 5m / 60 min / ≤24 h** | OpenAI 5.6 TTL; Anthropic default; Gemini explicit default; Gemini implicit hard delete |
| **~15 RPM** | OpenAI/Azure overflow per prefix+key |
| **+10%** | OpenAI regional processing (eligible models from 2026-03-05) |
| **$4/$0.40/$5/$20** | gpt-5.6-sol in / cached / write / out per 1M (short) |
| **$2/$2.50/$4/$0.20/$10** | Sonnet 5 base / 5m write / 1h write / hit / out |
| **$0.0000045 / $4.50 / $108** | Gemini Pro token-hour; **[inferred]** $/MTok-hour; 1M tok × 24 h storage |
| **[inferred] $6.42 / $8.24 / $20.80** | Sonnet 5 8k+400/400 per 1k: 1-write / rewrite-every-10 / uncached |
| **[inferred] $0.72 vs $2.16** | luna same shape |
| **[inferred] $20.65 vs $200; $11 idle** | Gemini 3.1 Pro 100k × 1k reads / 1 h; 24 h zero reads |
| **2M ITPM → 10M** | Anthropic 80% cache hit example (reads excluded except Haiku 3.5) |

### Latency / security
| Number | What |
| --- | --- |
| **11.5 s → 2.4 s (−79%)** | Anthropic 100k “chat with a book” TTFT |
| **149.6 / 32.8 ms (0.22)** | KVGov A100 2119-token cold/cached TTFT |
| **0.40 / 0.093** | KVGov “breach” ratio; llama.cpp Metal ratio |
| **−56% / 2× / 40→80%** | HiCache+3FS Qwen3-Coder-480B |
| **−84%** | HiCache R1-671B PD-disagg QA hit vs recompute |
| **3.49 / 14.13 / 21.29 s** | HiCache PR L2-only avg / p90 / p99 TTFT |
| **100% / ≈10^83 / O(1)** | EarlyBird ASR at block_size=1; V1 free-text guesses; template fields |
| **99–100% / 43–100%** | PROMPTPEEK on SGLang; InputSnatch semantic legal domains |
| **AUC 0.571 / 0.99; ≥0.9.0** | CVE-2025-46570 1-token / 8-token; vLLM patch |
| **100%→0%; 19.5%/9.8%; 31.6%** | KVGov HMAC ASR; session-flush only; adversary tipping point |
| **93% / 70% / 30%** | Boundary-salt benefit kept; CacheSolidarity reuse / latency vs isolate-all |
| **5–20 ms vs 1–5 s; 2–4× / 50–100×** | Redis semantic blog (do not collapse with sub-ms in-index) |
| **0.1 / 0.88** | RedisVL default cosine **distance**; FAQ blog start **similarity** |
| **45 GiB ≈ 87** | TRT-LLM host offload example; **[inferred]** 8k Llama-405B prefixes |
| **>100B tok/day; +115%/+107%** | Mooncake production |
| **Feb 2026 / 2026-11-21** | Together disable-cache flags ignored through; Sol promo through |

---

*End of module. Practice the Q&A out loud; recode the breaker states and semantic→prefix→uncached chain from memory; recompute the 8k+400/400 Sonnet mix and the Gemini storage idle tax on a whiteboard with the assumptions listed.*
