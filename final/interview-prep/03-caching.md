# Module 03: Caching in LLM Systems

## What Is This?

Caching in LLM systems is keeping a cheat sheet so you do not re-read the entire textbook for every question. A transformer does not "remember" your tools JSON between HTTP calls -- every request either rebuilds the prompt's key/value tensors from scratch (prefill) or reuses tensors someone already paid to compute. That reuse is caching. The critical distinction interviewers test: **exact** caches (KV, prefix, hosted prompt) reuse identical computation with zero quality loss. **Approximate** caches (semantic) decide a new question is "close enough" to an old one and return a prior answer -- that is a product decision, not a free lunch.

Start with the two inference phases: **prefill** reads the whole prompt in parallel (dominates TTFT). **Decode** generates output token by token (dominates long-generation latency). Most caching primarily helps prefill, not decode. If the model is writing a long answer, prompt caching may barely change total end-to-end latency even though TTFT improves dramatically. Stacking exact + semantic + application caching is how enterprises cut 70-90% of their LLM costs.

---

## Part 1: System Topology & Data Flow

### Five Cache Layers (Not the Same Cache)

| Layer | What Is Stored | A Hit Means | Analogy |
|---|---|---|---|
| **KV / PagedAttention** | K,V for this sequence's past tokens | Decode does not recompute prefill | Scratch paper for the problem you are still solving |
| **Prefix / APC** | KV blocks keyed by chained block hashes, shared across requests | Skip prefill of the shared left-hand tokens | The reserved stack: same first chapters, different last page |
| **Hosted Prompt Cache** | Provider-side KV you never see (OpenAI: "stores KV tensors, not the tokens") | Input billed ~0.1x; TTFT drops | The library keeps a photocopy; cheap to reread |
| **Semantic Cache** | Text of a previous response, keyed by embedding kNN | Skip the LLM entirely | A colleague hands you yesterday's answer because the question "sounds the same" |
| **Application / Result Cache** | Exact (model, prompt_hash, tools_hash, params) or a LangGraph node output | Skip a subgraph or tool | Browser cache / memoized function |

### Architecture Diagram

```
                         TELEMETRY / OBSERVABILITY SINKS
         +--------------------------------------------------------------+
         |  cached_tokens / cache_write_tokens / cache_read_input_tokens |
         |  token hit rate vs request hit rate   write/read ratio        |
         |  RPM per prompt_cache_key (~15 overflow)   stampede spikes    |
         |  WORM audit: (block_hash, salt, pod, tenant) -- not raw text |
         +------^-----------------^-----------------^-------------------+
                | spans           | metrics          | audit events
                |                 |                  |
+---------------+-----------------+------------------+------------------+
| CONTROL PLANE  (keys, breakpoints, TTL, affinity, salt, admission)    |
|                                                                       |
|  +-------------+  +---------------+  +--------------+  +------------+ |
|  | IdP/PEP     |  | Canonical     |  | Breakpoint / |  | Affinity   | |
|  | HMAC salt   |  | prefix render |  | cache_control|  | router     | |
|  | NEVER from  |  | (no ts in     |  | prompt_cache_|  | key/LPM/   | |
|  | client JSON |  |  left span)   |  | options      |  | Dynamo     | |
|  +------+------+  +-------+-------+  +------+-------+  +------+-----+ |
|         | tenant          | key            | markers         | route  |
+---------+-----------------+----------------+-----------------+--------+
          |                 |                |                 |
          v                 v                v                 v
+---------+-----------------+----------------+-----------------+--------+
| DATA PLANE  (compute vs state -- independently scheduled)             |
|                                                                       |
|  COMPUTE: tokenizer -> scheduler admit -> prefill GEMM (or skip       |
|           matched prefix blocks) -> decode attention / speculative     |
|                                                                       |
|  STATE:   KV block pool (vLLM) / radix tree (SGLang) / hosted KV     |
|           (you never see tensors; usage fields only)                  |
|                                                                       |
|  +--- TOOL PROXIES (MCP -- least privilege) ----------------------+  |
|  | lookup_semantic | write_prefix | generate_cached | prewarm_cache|  |
|  | (NO omnibus cache(prompt, tenant_id, salt))                     |  |
|  | Identity + salt from verified token -- NEVER model JSON         |  |
|  +-----------------------------------------------------------------+  |
+---------+-----------+------------------+-------------------+----------+
          |           |                  |                   |
          v           v                  v                   v
+-------------------------------------------------------------------------+
| PERSISTENCE LAYER                                                        |
|  +----------+ +----------+ +-----------+ +----------+ +----------+      |
|  | GPU HBM  | | Host DRAM| | L3 remote | | Semantic | | App /    |      |
|  | APC/radix| | Offload  | | LMCache / | | Redis    | | LangGraph|      |
|  | (process | | HiCache  | | Mooncake /| | HNSW+TAG | | node     |      |
|  |  local)  | | L2       | | HiCache L3| |          | | cache    |      |
|  +----------+ +----------+ +-----------+ +----------+ +----------+      |
|  Hosted OpenAI/Anthropic KV: NO dump API. Failover region = cold.       |
+-------------------------------------------------------------------------+
```

### Planes (Do Not Couple)

| Plane | Owns | Failure if Coupled |
|---|---|---|
| **Control** | Cache keys, breakpoint placement, TTL, affinity routing, tenant salt, admission, stampede serialization | Client-supplied salt; geo routing that fights cache locality |
| **Data (compute)** | Prefill GEMM, decode attention, speculative verify | Prefill and decode sharing one iteration budget (Sarathi: up to 28.3x TBT vs decode-only) |
| **Data (state)** | KV blocks, radix/prefix trees, offloaded KV | Treating KV as ephemeral scratch while it is a materialization of the prompt |

### Request-Flow Narrative

1. **PEP / salt (control).** TLS terminates. Verified token yields `tenant_id`. Gateway injects `cache_salt = HMAC(server_secret, tenant_id)` -- never a client header. For semantic path: the same identity becomes a Redis TAG in the same `FT.SEARCH` as the KNN.

2. **Canonicalize (control).** Render `tools -> system -> messages` (Anthropic order). Strip clocks from the left span. Application key = `sha256(canonical_json(model, tools, system, params))`. Hosted routing key = stable `workflow:tenant:shard-n`.

3. **L1 exact hash (optional).** SHA-256 of normalized (query + model + tenant_id). Redis `GET` in microseconds. On hit, the LLM is never called. Covers exact-repeat queries: FAQ bots, temperature=0 deterministic workloads.

4. **L2 semantic lookup (optional, FAQ only).** Embed the user question (not the 8k tools block). HNSW + tenant/model/policy tags. Hit = return stored response text, skip the LLM. Miss / breaker open = continue. **Threshold too low = wrong answer served as truth.** Never on tool-using or regulated loops.

5. **L3 exact prefix lookup (data state).** Self-hosted: walk chained block hashes (vLLM) or radix LPM (SGLang) from the left until first miss. Hosted: provider matches the rendered prefix at breakpoints; you observe `cached_tokens` / `cache_read_input_tokens`.

6. **Hit -- skip prefix prefill.** Scheduler only prefills the unmatched suffix. Decode still runs (exact cache does not skip the generator). Bill read SKU (~0.1x). TTFT approaches ITL on a full-prefix hit.

7. **Miss -- prefill + write.** Compute-bound prefill builds KV. Hosted GPT-5.6+/Anthropic 5m: 1.25x write at the breakpoint. Stampede: N parallel cold prefixes = N writes (Anthropic: entry available only after the first response begins). Control plane single-flights or `max_tokens: 0` pre-warm.

8. **Admit / route (control).** OpenAI: machine load + hash of initial tokens (~256 tokens) + optional `prompt_cache_key`. Above ~15 RPM per prefix/key, overflow to a machine without the entry. Self-hosted: Dynamo overlap credits or llm-d precise scorer; naive kube round-robin destroys locality.

9. **Backfill caches.** Generated response written to L1 (exact hash) and L2 (embedding + response) with appropriate TTLs and tenant namespace tags. L3 prefix cache is managed automatically.

10. **Emit telemetry.** Every request logs: cache tier that served it (L1/L2/L3/miss), latency, token counts (cache_read vs cache_creation), tenant ID. Board token hit rate AND request hit rate AND write/read ratio.

---

## Part 2: Core Mechanics & Algorithms

### Four Invariants

**I1.** Intra-request KV is not cross-request reuse. Decode reading its own past tokens is table stakes. APC/hosted prompt cache is sharing those blocks across requests. Mixing them in a design review is an instant fail.

**I2.** Exact caches match rendered tokens from the left. `model`, tool order, schema whitespace, `reasoning.effort`, image `detail`, compaction, and a timestamp in system all change the prefix. One mutated token invalidates everything after it.

**I3.** Pin `model_id + tokenizer + tools_hash + salt_namespace + serve_dtype`. A LoRA id, multimodal hash, or `cache_salt` is in vLLM's extra hashes -- a new adapter is a different cache.

**I4.** Salt is a secret the gateway injects. vLLM: omit `cache_salt` = globally content-addressed sharing. Never accept a client-supplied salt. `prompt_cache_key` is routing, not a tenant wall.

### KV Cache Memory Math

Per token, per layer, BF16: **`2 x n_kv_heads x d_head x 2 bytes`**

GQA (Ainslie et al., EMNLP 2023): Llama-class 64 Q / 8 KV is an 8x KV cut vs MHA. MLA (DeepSeek-V2): 93.3% KV reduction vs DeepSeek 67B MHA.

| Model | Architecture | KB/token (BF16) |
|---|---|---|
| Llama-3.1-405B | GQA | **516.096** |
| Qwen-2.5-72B | GQA | **327.680** |
| DeepSeek-V3 | MLA | **70.272** |

**Worked example:** 8k-token tools prefix on Llama-405B: 8000 x 516.096 KB = **~4.13 GB** of KV for that prefix alone. Same prefix on DeepSeek-V3 MLA: **~0.56 GB**. APC sharing that prefix across 32 same-salt tenants is 4.13 GB once, not x32. HMAC-per-tenant salt makes it x32.

**Practical anchor:** Llama-3.1-8B with GQA: ~128 KB/token. 32k context is ~4 GB of KV.

### PagedAttention (Kwon et al., SOSP 2023)

KV is virtual memory: fixed-size blocks (vLLM default 16 tokens; TRT-LLM default 128), a block table per sequence, near-zero internal fragmentation (waste only in the last block, "under 4%" in the vLLM blog), copy-on-write for beam/parallel sampling. vLLM: 2-4x throughput vs FasterTransformer and Orca; up to 24x vs HuggingFace Transformers.

### RadixAttention (Zheng et al., SGLang)

Retains KV in a radix tree (compressed trie) keyed by token sequences. Longest-prefix match on admission. LRU eviction of leaves so shared roots (system prompts) survive. Refcount so in-flight nodes are unevictable. LMSYS blog: up to 5x throughput vs Guidance and vLLM; "no noticeable overhead even in the absence of cache hits." NeurIPS: up to 6.4x throughput and 3.7x lower latency. Multi-agent workloads benefit from zero-cost memory sharing for identical system prompts.

**Scheduler:** `--schedule-policy lpm` (default) vs `fcfs`. If LPM and `len(waiting_queue) > 128`, policy degrades to FCFS to avoid expensive prefix matching. That is the complexity valve.

### vLLM Automatic Prefix Caching (APC)

Each block hash = `(parent_hash, block_tokens, extra_hashes)`. Extra hashes: LoRA IDs, multi-modality input hashes, `cache_salt`. `--prefix-caching-hash-algo`: `sha256` (default), `sha256_cbor` (recommended for deterministic cross-environment hashing), `xxhash`/`xxhash_cbor` (faster but NOT cryptographically secure -- vendor warning of collision/leak risk in multi-tenant). Prefix caching is on by default in V1.

`cache_salt` is injected into the first block hash so only same-salt requests share. Treat the salt as a secret: 256-bit HMAC, not a user name. An attacker who obtains the salt can still mount the timing attack against that tenant.

| Engine | Mechanism | Best For | Weakness |
|---|---|---|---|
| vLLM (PagedAttention) | Block-paged memory | Batch processing, unique prompts | No prefix sharing across requests |
| vLLM (APC) | Chain hashing | Fixed system prompts | Linear chains, no tree branching |
| SGLang (RadixAttention) | Radix tree LRU | Multi-agent, branching conversations | Overhead on fully unique workloads |

**Benchmarks (H100, Llama 3.1 8B):** SGLang ~16,200 tok/s vs vLLM ~12,500 tok/s (29% advantage). On prefix-heavy workloads: SGLang up to 6.4x throughput advantage, 20-40% lower TTFT. On unique-prompt workloads: within 5%.

### HiCache (LMSYS, 2025-09-10)

L1 = GPU HBM (instance-private); L2 = host DRAM (instance-private -- two instances on same node do NOT share L2); L3 = storage backend (shared only if namespace is cluster-wide). Prefetch from L3 if remaining hit >256 tokens. Write policies: `write_through`, `write_through_selective`, `write_back`. `file` at `/tmp/hicache` does not survive rolling restart; `mooncake`/`hf3fs`/`nixl`/`aibrix` with shared namespace can.

### Splitwise / DistServe / Sarathi

**Splitwise (ISCA 2024):** Prefill and decode have distinct latency, throughput, memory, and power characteristics. KV transfer <7% of prompt compute. Cluster: up to 1.4x throughput at 20% lower cost.

**DistServe (OSDI 2024):** Colocating prefill+decode causes "strong prefill-decoding interferences." 7.4x more requests or 12.6x tighter SLO while staying inside both SLOs for >90% of requests.

**Sarathi-Serve:** Naive hybrid batching causes up to 28.3x higher TBT vs decode-only. Stall-free chunked prefill packs running decodes first.

### Hosted Prompt Cache -- Provider Comparison

| Provider | Write | Read | Min Tokens | TTL | Breakpoints | Isolation |
|---|---|---|---|---|---|---|
| **OpenAI GPT-5.6+** | **1.25x** | **0.1x** | 1,024 visible | 30m sliding | Up to 4; `mode=explicit` recommended | Org; not cross-region |
| **OpenAI pre-5.6** | 1.0x | 0.5x (4o) / 0.1x (many 5.x) | 1,024-2,048 | 5-10 min | Auto | Org |
| **Anthropic 5m** | **1.25x** | **0.1x** (Fable/Mythos 5.1: **0.025x**) | 512-4,096 | 5m sliding | Up to 4 `cache_control` | Workspace (API/Foundry); org (Bedrock/GCP) |
| **Anthropic 1h** | **2.0x** | same read | same | 1h sliding | same | same |
| **Gemini implicit** | 1.0x | 0.1x (2.5+) | 2,048-6,144 | best-effort <=24h | none | Project; $0 storage |
| **Gemini explicit** | 1.0x + storage rent | 0.1x (2.5+) | same | default 60 min | cache name | IAM; VPC-SC |
| **Fireworks** | 1.0x | default 0.5x | none published | LRU, minutes-hours | none | Per account; dedicated shared unless isolation key |
| **DeepSeek** | miss SKU | ~3% of miss (~0.032) | unit-match | hours-days | none | Per user, best-effort |
| **Together** | 1.0x | 19-81% off | none published | fleet-evicted | none | Fleet-shared |

**Key design rule:** Stable content first, volatile content last. Tools, system prompts, few-shots before the breakpoint. User queries, timestamps, per-request IDs after it. **Never** interpolate `datetime.now()` or UUIDs into the system prompt.

**Anthropic specifics:** Render order `tools -> system -> messages`. Writes only at the breakpoint; reads look back at most 20 content blocks. Below floor: **silent no-op** (no error, cache fields = 0). Pre-warm: `max_tokens: 0`. Launch TTFT: 100k book 11.5s -> 2.4s (-79%).

**OpenAI specifics:** Implicit breakpoint at end of latest eligible user/tool message. `mode=explicit` recommended to avoid paying 1.25x to cache volatile latest message. Routing: ~15 RPM per prefix/key before overflow. Official arithmetic: 1 write + 9 reads = 2.15x vs 10x uncached.

**Interview traps:** Bedrock: not on batch inference. Azure: not shared between subscriptions; PTU-M no breakpoints. Fireworks dedicated: shared by default (timing-attack residual documented). Together dedicated: cache on and cannot be disabled (flags ignored through February 2026). Cohere: no first-party prompt-cache SKU.

### Semantic Cache

Embeds the query, HNSW/FLAT kNN, threshold, returns a previous completion. **Skips the LLM entirely** -- that is both the win and the danger.

| Component | Latency-Optimized | Quality-Optimized |
|---|---|---|
| Embedding model | BGE-M3 (512-dim) | text-embedding-3-large (3072-dim) |
| Vector index | Redis HNSW (sub-ms in-index) | Dedicated vector DB |
| Similarity threshold | 0.90 (higher hit rate) | 0.95+ (lower false positive rate) |
| Metadata filters | Tenant, locale | + entity type, topic, model version, safety flags |

Redis blog: vector search "adds 5-20 ms" but "saves 1-5 seconds"; hits "typically 2-4x faster, with optimal cases reaching 50-100x." Those are different claims (in-index kNN vs e2e including embedding) -- do not collapse them.

**False positive risk:** "What is the return policy for electronics?" can match "What is the return policy for clothing?" at threshold 0.85. RedisVL default `distance_threshold=0.1` (cosine distance, lower is closer). FAQ blog: start cosine similarity 0.88, lower to 0.84 if paraphrases miss. GPTCache default evaluator is ExactMatchEvaluation; threshold 0-1 scale is NOT cosine intuition. **Never semantic-cache tool-using, regulated, or live-state traffic.**

### Cache-Aware Routing

| System | Approach | Key Result |
|---|---|---|
| **llm-d** | Global KV cache view, precise prefix-cache scorer | **57x** faster response, **2x** throughput vs naive LB |
| **NVIDIA Dynamo** | KV cache-aware routing with overlap credits | Prevents herding via temperature > 0 |
| **SGLang LPM** | In-process admission order (not cross-replica routing) | Degrades to FCFS above 128 waiting |
| **Ray Serve** | PrefixCacheAffinityRouter, sticky prefix routing | Open-source |
| **LMCache** | Cross-node KV management (locate, move, pin, compress) | Used in vLLM, Dynamo, llm-d, KServe |

**Dynamo cost formula:** `cost = prefill_load_scale x max(raw_prefill - overlap_credit, 0) + decode_blocks`. Worker A overlap 400, decode_blocks=50 -> cost 150. Worker B overlap 0, decode_blocks=10 -> cost 510. Temperature 0 sends to A. After 15 RPM of identical prefixes on A, decode_blocks explode -- herding is the hidden ITL cost.

**Critical:** Naive Kubernetes round-robin destroys prefix locality. llm-d exists because of this. SGLang LPM is in-process admission, not a fleet router.

### Skip RAG When the Corpus Fits the Cache

Anthropic Contextual Retrieval: knowledge base **< ~200,000 tokens (~500 pages)** -> stuff the corpus, skip RAG. Prompt caching: >2x latency cut, up to 90% cost reduction. Above 200k, or when you need per-query slices, use RAG. Do not put retrieved chunks before the breakpoint unless chunks are stable (else CacheBlend or do not cache RAG).

---

## Part 3: Token Economics & NFR Analysis

### Pricing Detail (as of 2026-09-02)

**OpenAI GPT-5.6 per 1M tokens (short context):**

| Model | Input | Cached Input | Write | Output |
|---|---|---|---|---|
| gpt-5.6-sol | $4.00 | $0.40 | $5.00 | $20.00 |
| gpt-5.6-terra | $2.00 | $0.20 | $2.50 | $12.00 |
| gpt-5.6-luna | $0.20 | $0.02 | $0.25 | $1.20 |
| gpt-5.6-cyber | $12.50 | $1.25 | $15.625 | $75.00 |

Sol promo through 2026-11-21. Regional residency +10% (eligible models from 2026-03-05). Long-context Sol >=~272k: 2x in / 1.5x out on the full request.

**Anthropic per MTok:**

| Model | Base Input | 5m Write | 1h Write | Cache Read | Output |
|---|---|---|---|---|---|
| Claude Opus 5 | $5.00 | $6.25 | $10.00 | $0.50 | $25.00 |
| Claude Sonnet 5 | $2.00 | $2.50 | $4.00 | $0.20 | $10.00 |
| Claude Sonnet 4.6 | $3.00 | $3.75 | $6.00 | $0.30 | $15.00 |
| Claude Haiku 4.5 | $1.00 | $1.25 | $2.00 | $0.10 | $5.00 |

Fable/Mythos 5.1 hit: $0.25 on $10 base (0.025x). Batch API discounts stack: Sonnet 4.6 batch + cache read = 95% off list.

**Vertex explicit storage:** Pro: $4.50/MTok-hour. Flash family: $1.00/MTok-hour. A 1M-token explicit Pro cache for 24h: 24 x $4.50 = **$108** storage whether or not it is read.

### Break-Even Reuse Count

Uncached input = 1. n identical-prefix requests cost W + (n-1)R vs n. Break-even: n >= (W - R) / (1 - R).

| Scheme | Write (W) | Read (R) | Break-Even n | At n=10: cached vs uncached |
|---|---|---|---|---|
| OpenAI 5.6 / Anthropic 5m | 1.25 | 0.1 | **2** | 2.15x vs 10x (78.5% savings) |
| Anthropic 1h | 2.0 | 0.1 | **3** | 2.90x vs 10x (71% savings) |
| Fable 5.1 5m | 1.25 | 0.025 | **2** | 1.475x vs 10x (85.3% savings) |
| Gemini implicit / DeepSeek | 1.0 | ~0.1 / ~0.032 | **2** / **2** | Gemini 1.9x; DeepSeek ~1.29x vs 10x |
| Fireworks default | 1.0 | 0.5 | **2** | 5.5x vs 10x (shallower) |

**Key:** Cache reads refresh TTL at no cost (Anthropic). Continuous traffic keeps 5-min cache warm indefinitely. Use 1h when gaps >5 min and <1h.

### Worked Cost per 1k Queries

**Reference mix:** 8,000-token tools+system prefix, 400-token user/tool suffix, 400-token output. 1,000 queries.

**Sonnet 5, 5-minute cache:**
- Uncached: 1000 x (8400 x $2 + 400 x $10) / 10^6 = **$20.80**
- 1 write + 999 reads: write $0.020 + reads $1.598 + suffix $0.80 + out $4.00 = **~$6.42/1k** (69% off)
- Rewrite every 10 queries (100 writes + 900 reads): **~$8.24/1k**

**gpt-5.6-luna:**
- 1 write + 999 reads: **~$0.72/1k** vs uncached $2.16

**Sonnet 4.6, 10k input tokens, with tiers:**
- No caching: 1000 x 10k x $3.00/MTok = **$30.00**
- L3 prefix only (90% hit): 100 writes + 900 reads = **$6.45** (78% savings)
- L1 exact (20%) + L2 semantic (25%) + L3 prefix (remaining): **$3.55** (88% savings)
- Add Batch API (50% off): **$1.78** (94% savings)

**Gemini 3.1 Pro explicit, 100k corpus, 1h:**
- 1,000 queries: create $0.20 + 1,000 reads $20 + storage $0.45 = **$20.65** vs $200 uncached
- Idle 24h, 0 reads: storage $10.80 + create $0.20 = **$11.00** for nothing
- 10 reads in 1h: $2.65 vs $2.00 uncached -- **worse than not caching** unless TTFT is the objective

**Cost trap:** A shared prefix below the minimum is billed 1.0x forever (silent no-op). A 3k-token prompt caches on Opus 5 / Sonnet 4.5 and silently will not on Haiku 4.5 / Opus 4.5 (4,096 floor).

### Rate Limits: Cache Reads as ITPM Multiplier

Anthropic: for most models, `cache_read_input_tokens` do NOT count toward ITPM. Exception: Claude Haiku 3.5 counts cache reads toward ITPM. Official: 2,000,000 ITPM at 80% cache hit -> 10,000,000 total input tokens/minute processed.

### Latency SLA Targets

No vendor publishes fleet-wide cache-hit p50/p95/p99 TTFT/ITL. Numbers below are architecture-derived, not vendor SLOs.

| Path | p50 | p95 | p99 | Mitigation |
|---|---|---|---|---|
| **L1 exact hit** | < 5ms | < 10ms | < 20ms | Redis cluster health |
| **L2 semantic hit** | < 25ms | < 50ms | < 100ms | Embedding model latency |
| **Prefix-hot, self-hosted APC** | ~50ms | ~150ms | ~400ms | llm-d/Dynamo locality |
| **Prefix-hot, hosted** | ~1,000ms | ~2,000ms | ~3,500ms | Shard `prompt_cache_key` before ~15 RPM |
| **Prefix-cold (miss), self-hosted** | ~600ms | ~2,000ms | ~8,000ms | Chunked prefill (Sarathi); PD-disagg |
| **Prefix-cold, hosted** | ~1,600ms | ~5,000ms | ~12,000ms | Pre-warm `max_tokens: 0`; timeout cache independently |

**Published existence proofs (not SLOs):**
- Anthropic 100k book: 11.5s -> 2.4s TTFT (-79%)
- KVGov A100 2119-token: 149.6ms cold / 32.8ms cached (ratio 0.22)
- HiCache + 3FS: Qwen3-Coder-480B ~25k: TTFT -56%, throughput 2x

**Decode-heavy workloads** (short prompt, long output): prefix cache does NOT change ITL/TPOT. Output SKUs are unchanged.

### Cache Hit Rate Monitoring

| Metric | Target | Alert Threshold | Action |
|---|---|---|---|
| L1 hit rate | 15-25% (FAQ/QA) | < 10% sustained | Check hash normalization, query dedup |
| L2 hit rate | 25-40% (paraphrase-heavy) | < 15% sustained | Tune embedding model, lower threshold |
| L3 hit rate (cache_read / total input tokens) | > 50% | Sustained 0% | Audit prompt assembly for volatile prefixes |
| Combined cost savings | > 60% | < 40% | Review traffic patterns, cache tier config |

### NFRs

| NFR | Production Stance | Competes With |
|---|---|---|
| **Availability** | Prefix cache is best-effort affinity, not a replicated datastore. Circuit-break semantic, prefix, and generator independently. | Hit rate (sticky routing) vs tail ITL (herding) |
| **RPO** | Hosted KV has no dump API. RPO for prefix state = empty after process kill / region fail. LMCache/Mooncake/HiCache L3 are the checkpoint if backend namespace is shared. | Durability vs $ (Gemini storage; DRAM/SSD for Mooncake) |
| **RTO** | Failover region is cold (OpenAI: caches cannot cross regional boundaries). RTO = time to re-warm (one TTL window of write SKUs). | Deploy velocity vs write-cost budget |
| **Compliance** | Anthropic ZDR: in-memory KV+hash only -- still in RAM during TTL. PII in shared tools/system block is materialized for every same-salt/org hit. | Latency (residency path) vs hit rate |
| **Security vs sharing** | Org-level hosted cache maximizes tools-prefix hits and opens same-org timing. Per-tenant HMAC salt closes the channel and duplicates the KV. | Cost (HBM x tenants) vs leak surface |

---

## Part 4: Distributed Resilience & Security

### Cache Stampede Prevention

When a popular cached item expires, concurrent requests simultaneously attempt to regenerate it. In agentic AI systems, this is amplified by semantic correlation: thousands of agents share the same knowledge base with uniform TTLs.

| Technique | Mechanism | Complexity | Trade-off |
|---|---|---|---|
| **TTL Jitter** | `TTL = base + random(-jitter, +jitter)` | Low | Spreads but does not eliminate stampedes |
| **Stale-While-Revalidate** | Return stale value; background regenerate | Low | Serves potentially outdated responses |
| **Request Coalescing (Single-Flight)** | Multiple requests share one backend call | Medium | Lock holder failure blocks all waiters |
| **Distributed Locking** | Redis `SET NX` with TTL; one regenerates | Medium | Lock holder failure blocks all waiters |
| **Probabilistic Early Expiration (XFetch)** | Recompute if `-beta * delta * ln(rand()) > ttl_remaining` | Medium | Occasional unnecessary recomputation |

**Anthropic-specific:** N identical prefixes on a cold cache = N writes at 1.25x/2.0x because the entry exists only after the first response begins. Serialize a `max_tokens: 0` pre-warm, then fan out.

### Timing Side Channels

A cache hit skips prefill, so TTFT drops. This creates a timing oracle.

| Attack | Method | ASR |
|---|---|---|
| **EarlyBird** | Single-token block reconstruction | 100% at block_size=1; V1 block_size=16 makes free-text guesses ~10^83; template fields remain O(1) |
| **PROMPTPEEK** (NDSS 2025) | Multi-tenant KV cache timing probes | 99-100% on SGLang radix |
| **InputSnatch** | Prefix + semantic oracle | 62% disease / 13.5% symptoms; semantic 43-100% across 13 legal domains |
| **CVE-2025-46570** | vLLM timing | ROC AUC 0.571 at 1-token, **0.99** at 8 tokens; patched >=0.9.0 via salting |

**KVGov defenses:**
- HMAC salt: 100% -> 0% ASR (N=1000)
- Gaussian noise sigma=20ms: PROMPTPEEK 0.5%, InputSnatch still 100% (membership, not magnitude)
- Boundary salting retains ~93% of prefix-cache benefit (research, not a vLLM flag)
- CacheSolidarity: isolate only suspicious prefixes; up to 70% higher reuse vs isolate-everyone
- Evolutionary tipping point: 31.6% adversary prevalence

**SafeKV (NDSS 2026):** Reduces TTFT overhead of per-tenant isolation by up to 2.66x vs full isolation while enforcing privacy boundaries.

### Isolation Ladder

| System | Default Sharing | Isolation Primitive |
|---|---|---|
| vLLM APC | Global content-addressed | `cache_salt` on first block; omit = share with everyone |
| SGLang Radix | Global token-sequence tree | No first-class salt; partition engines for hostile tenants |
| OpenAI | Org-wide | Org + region; `prompt_cache_key` is routing, not confidentiality |
| Anthropic | Workspace (API/Foundry); org (Bedrock/GCP) | Workspace split (2026-02) closes cross-workspace probing |
| Azure | Subscription | No cross-subscription share |
| Gemini explicit | Project / cache resource | IAM; VPC-SC |
| Fireworks dedicated | Shared by default | `x-prompt-cache-isolation-key` |
| Semantic (Redis/GPTCache) | Whatever you put in the index | TAG filter on tenant_id in same `FT.SEARCH`; missing = cross-talk |

### Zero-Trust Cache Architecture

| Principle | Implementation |
|---|---|
| **Encryption at rest** | All cached responses encrypted with AES-256 before storage. Keys via Vault/KMS. |
| **Mutual TLS** | App-to-cache and replica-to-replica mTLS. No plaintext cache traffic. |
| **Signed cache entries** | HMAC signature over content + metadata. Verify before serving. Detects tampering. |
| **Per-tenant encryption keys** | Tenant-specific key. Compromise of one does not expose others. |
| **No implicit trust** | Cache layer authenticates every request via service identity tokens. |

### RBAC for Cache Operations

| Role | Permitted Operations | Use Case |
|---|---|---|
| **reader** | `GET`, `VSIM` (vector similarity) | Inference services, read-only |
| **writer** | `SET`, `VADD`, `SETEX` | Cache warming, LLM response backfill |
| **admin** | `DEL`, `FLUSHDB`, `CONFIG SET`, `EVICT` | Ops managing capacity, TTL, emergency purges |
| **auditor** | `VIEW_ACCESS_LOGS`, `VIEW_METRICS`, `EXPORT_AUDIT` | Compliance review, no access to cached content |

### PII Pipeline for Cached Content

**Before cache write, not after:**

1. **Detection:** Scan the cacheable left span (tools+system), tool/RAG text about to be appended, and semantic answer body. Regex for structured identifiers (PAN with Luhn, SSN, email, phone). NER/classifier for PERSON, LOCATION, ORG, free-text clinical/financial spans. Dual-gate: regex is cheap and high-precision on PANs; NER catches names in medical notes.

2. **Redaction:** Strip or tokenize PII from cacheable prefixes (`acct-****` / `[EMAIL_<hash12>]`). Keep raw PII only after the cache breakpoint in the non-reusable suffix. Never store raw PII in semantic answers (a false-positive hit returns that text verbatim). Tenant policy: `strip` | `tokenize` | `block-from-cache`.

3. **Audit trail (WORM):** Immutable log of detect/redact decisions, not values: `content_sha256` (pre- and post-redact), entity types + counts, action, detector, confidence. Never the raw span. Correlate with `cid`, `tenant_id`, `cache_layer`, `salt_fingerprint`, `prefix_hash`.

### Circuit Breaker Pattern

Independent breakers: **semantic index**, **prefix/cache API**, **generator**. A Redis HNSW storm must not starve generate (bulkhead). A cache-API 5xx must not block uncached generate.

**Fallback chain:** semantic off -> exact prefix / hosted prompt cache -> uncached generate (full prefill). Never the reverse on a safety-critical path (do not "fail open" into semantic).

### Failure Taxonomy

| Class | Examples | Detection | Handling |
|---|---|---|---|
| **Transient** | 429/5xx, Redis timeout, overflow miss (~15 RPM), HiCache prefetch timeout | Error rate, Retry-After | Full-jitter retries on idempotent reads; single-flight writes |
| **Permanent** | 4xx auth, prefix below min (silent 0 fields), PTU-M no breakpoints, TGI VLM auto-disables APC | Non-retryable code; both cache fields 0 | Fail closed; change model or lengthen prefix |
| **Poison (prefix)** | Timestamp in cached span, attacker-controlled tool body, RAG chunk order before breakpoint | `cache_write_tokens >> cached_tokens` | Canonical JSON; version string in tools block |
| **Poison (semantic)** | False-positive neighbor, InputSnatch, stale price/inventory | False-hit eval set; missing tenant TAG | Fail to exact prefix; TTL; never semantic-cache regulated traffic |
| **Adversarial** | Cache key collision, timing side-channel exploitation | Anomaly detection, latency distribution analysis | Quarantine affected namespace; invalidate and rebuild |
| **Stale** | 5m TTL vs user think-time, Gemini explicit idle, compaction replacing earlier content | TTL miss after gap; storage $ with 0 reads | Anthropic 1h when gaps >5 min |

**Key invariant:** Transient failures retry. Permanent failures skip and alert. Adversarial failures quarantine and investigate.

---

## Part 5: Production Enterprise Code

```python
"""Multi-tier LLM cache: semantic -> exact prefix -> uncached generate.
Salt is HMAC'd server-side. Stampede: single-flight + XFetch.
Stdlib only. Swap Fake* for RedisVL / vendor HTTP / vLLM.
"""
from __future__ import annotations

import hashlib, hmac, json, logging, math, random, threading, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol, Optional

log = logging.getLogger("cache")

# ── Retry with full jitter (AWS-style) ────────────────────────────

class TransientError(Exception): pass
class PermanentError(Exception): pass

def retry_with_jitter(fn, *, cid, tenant, op, attempts=4, base=0.05, cap=1.0):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except PermanentError:
            raise
        except TransientError as exc:
            last = exc
            if i < attempts - 1:
                sleep = random.uniform(0, min(cap, base * 2**i))
                log.warning("retry cid=%s tenant=%s op=%s attempt=%d",
                            cid, tenant, op, i + 1)
                time.sleep(sleep)
    raise last

# ── Circuit Breaker ───────────────────────────────────────────────

class CircuitState(str, Enum):
    CLOSED = "closed"; OPEN = "open"; HALF_OPEN = "half_open"

@dataclass
class CircuitBreaker:
    name: str
    threshold: int = 5
    cooldown_s: float = 15.0
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0

    def allow(self):
        if self._state is CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = CircuitState.HALF_OPEN
            else:
                raise TransientError(f"circuit_open:{self.name}")

    def record_success(self):
        self._failures = 0
        self._state = CircuitState.CLOSED

    def record_failure(self):
        self._failures += 1
        if self._state is CircuitState.HALF_OPEN or \
           self._failures >= self.threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

# ── XFetch Stampede Prevention ────────────────────────────────────

def should_recompute_early(compute_delta: float, ttl_remaining: float,
                           beta: float = 1.0) -> bool:
    """Probabilistic early recomputation (XFetch algorithm).
    Returns True if this request should proactively regenerate before
    TTL expiry. As ttl_remaining shrinks, probability rises.
    Formula: recompute if -beta * delta * ln(random()) > ttl_remaining
    """
    if ttl_remaining <= 0:
        return True
    return -beta * compute_delta * math.log(random.random()) > ttl_remaining

# ── Single-Flight for Cold Prefix Writes ──────────────────────────

@dataclass
class SingleFlight:
    """Serialize cold prefix writes so N cold requests are not N 1.25x writes."""
    _locks: dict[str, threading.Lock] = field(default_factory=dict)
    _guard: threading.Lock = field(default_factory=threading.Lock)

    def lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

# ── Canonical Prefix + HMAC Salt ──────────────────────────────────

@dataclass(frozen=True)
class PromptParts:
    model: str
    tools: list
    system: str
    user: str
    params: dict

    def prefix_dict(self) -> dict:
        """Left-hand exact-cache span: no clocks, tools pinned by name."""
        tools = sorted(self.tools, key=lambda t: json.dumps(t, sort_keys=True))
        return {"model": self.model, "tools": tools,
                "system": self.system, "params": self.params}

def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))

def prefix_hash(parts: PromptParts) -> str:
    return hashlib.sha256(canonical_json(parts.prefix_dict()).encode()).hexdigest()

def tenant_salt(server_secret: bytes, tenant_id: str) -> str:
    """256-bit HMAC salt. Gateway-only. Never from client JSON."""
    digest = hmac.new(server_secret, tenant_id.encode(), hashlib.sha256).digest()
    return hashlib.sha256(digest).hexdigest()

def routing_key(parts: PromptParts, tenant_id: str, shard: int) -> str:
    """Affinity hint (not a confidentiality boundary)."""
    return f"{parts.model}:{tenant_id}:{prefix_hash(parts)[:16]}:shard-{shard}"

# ── Protocols ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Authz:
    tenant_id: str
    actor: str
    allow_semantic: bool
    traffic_class: str   # faq | agent -- semantic forbidden on agent

class SemanticCache(Protocol):
    def get(self, tenant_id: str, model: str, policy: str, q: str) -> Optional[str]: ...
    def put(self, tenant_id: str, model: str, policy: str, q: str, answer: str) -> None: ...

class PrefixGenerator(Protocol):
    def complete(self, parts: PromptParts, salt: str, route: str,
                 cid: str) -> tuple[str, int, int]: ...

class UncachedGenerator(Protocol):
    def complete(self, parts: PromptParts, cid: str) -> str: ...

# ── Cache Runtime ─────────────────────────────────────────────────

@dataclass
class CachedResult:
    text: str
    layer: str        # semantic | prefix | uncached | deterministic
    degraded: bool
    cached_tokens: int
    cache_write_tokens: int

class CacheRuntime:
    """Fallback chain: semantic (FAQ only) -> exact prefix -> uncached -> refuse."""

    def __init__(self, server_secret: bytes, semantic: SemanticCache,
                 prefix_gen: PrefixGenerator, uncached_gen: UncachedGenerator,
                 policy_ver: str = "2026-09", min_prefix_tokens: int = 1024,
                 shards: int = 4):
        self.server_secret = server_secret
        self.semantic = semantic
        self.prefix_gen = prefix_gen
        self.uncached_gen = uncached_gen
        self.policy_ver = policy_ver
        self.min_prefix_tokens = min_prefix_tokens
        self.shards = shards
        self.flight = SingleFlight()
        self.breakers = {n: CircuitBreaker(n)
                         for n in ["semantic", "prefix", "uncached"]}

    def _shard(self, tenant_id: str) -> int:
        return int(hashlib.sha256(tenant_id.encode()).hexdigest(), 16) % self.shards

    def _call(self, name, fn, cid, tenant, op):
        br = self.breakers[name]
        def _op():
            br.allow()
            try:
                out = fn()
            except PermanentError:
                br.record_failure(); raise
            except Exception as exc:
                br.record_failure(); raise TransientError(str(exc)) from exc
            br.record_success()
            return out
        return retry_with_jitter(_op, cid=cid, tenant=tenant, op=op)

    def complete(self, parts: PromptParts, authz: Authz) -> CachedResult:
        cid = str(uuid.uuid4())
        salt = tenant_salt(self.server_secret, authz.tenant_id)
        route = routing_key(parts, authz.tenant_id, self._shard(authz.tenant_id))

        # L0: Semantic -- FAQ only, tenant-tagged, never agent/tool loops
        if authz.allow_semantic and authz.traffic_class == "faq":
            try:
                hit = self._call("semantic",
                    lambda: self.semantic.get(authz.tenant_id, parts.model,
                                              self.policy_ver, parts.user),
                    cid, authz.tenant_id, "semantic_get")
                if isinstance(hit, str) and hit:
                    return CachedResult(hit, "semantic", False, 0, 0)
            except (TransientError, PermanentError):
                pass   # fall through to prefix

        # L1: Exact prefix -- still decodes; single-flight cold writes
        flight_key = f"{salt}:{prefix_hash(parts)}"
        try:
            with self.flight.lock_for(flight_key):
                text, cached, wrote = self._call("prefix",
                    lambda: self.prefix_gen.complete(parts, salt, route, cid),
                    cid, authz.tenant_id, "prefix_complete")
            # Backfill semantic on FAQ traffic
            if authz.allow_semantic and authz.traffic_class == "faq":
                self.semantic.put(authz.tenant_id, parts.model,
                                  self.policy_ver, parts.user, text)
            return CachedResult(text, "prefix", False, int(cached), int(wrote))
        except (TransientError, PermanentError):
            pass

        # L2: Uncached full prefill (fallback)
        try:
            text = self._call("uncached",
                lambda: self.uncached_gen.complete(parts, cid),
                cid, authz.tenant_id, "uncached_complete")
            return CachedResult(text, "uncached", True, 0, 0)
        except (TransientError, PermanentError):
            pass

        # Deterministic refusal
        return CachedResult(
            "Generation unavailable. Retry later.", "deterministic", True, 0, 0)
```

---

## Part 6: Architectural System Design Scenarios

### Scenario A -- Multi-Tenant Agent with Shared 8k Tools Prefix

**Problem.** B2B copilot. 8,000-token tool schemas + system identical across tenants; per-tenant RAG/PII in the suffix. 15-200 RPM aggregate; some tenants burst past 15 RPM alone. Requirements: SOC 2 isolation, prefix TTFT after warmup, no cross-tenant timing oracle, stampede-safe fan-out.

| Axis | A1 Hosted explicit breakpoint + workspace isolation (recommended) | A2 Self-hosted vLLM APC + HMAC salt + llm-d | A3 Shared unsalted APC |
|---|---|---|---|
| **Cost** | Sonnet 5 ~$6.42/1k at 1 write+999 reads vs $20.80 uncached | GPU-hour; prefix hit cuts TTFT, not a 0.1x invoice | Cheapest HBM -- paid in leak risk |
| **Latency** | Hit: 79% TTFT cut (Anthropic 100k); miss: full prefill + ~15 RPM overflow | High if affinity works; rolling deploy cold until L3/LMCache | Best hit rate, worst threat model |
| **Security** | Workspace-per-tenant closes cross-workspace probing; same-org timing remains | HMAC salt; CVE-2025-46570 if <0.9.0; boundary salt 93% is research | TTFT oracle (KVGov 0.22); EarlyBird template O(1) |
| **Scalability** | 4 breakpoints; shard keys; regional cache cannot DR-warm | Multi-node index; HiCache L3 for restart | Herd onto one replica; ITL explodes |

**Decision.** A1 wins when GPU ops are not a core competency. Put tools+system left of breakpoint, PII right, shard `prompt_cache_key`, pre-warm. A2 for steady GPU utilization or hostile tenants. A3 fails the design review: shared unsalted KV is a prompt leak.

### Scenario B -- High-Volume Support Bot (50K queries/day)

**Problem.** Financial services, 50,000 queries/day across 15 product lines. Current monthly LLM spend: $45,000. Target: 60% cost reduction. Must maintain tenant isolation (PCI-DSS, SOC 2, HIPAA across three business units).

| Alternative | Cost Reduction | Latency Impact | Verdict |
|---|---|---|---|
| **L1+L2+L3 (recommended)** | ~70% ($31.5K saved/mo) | p95 < 2.5s (improved) | Selected |
| **L1 exact only** | ~20% ($9K saved/mo) | p95 unchanged | Insufficient savings |
| **Aggressive semantic (t=0.85)** | ~55% ($24.7K saved/mo) | p95 < 2s | False positive risk too high for financial |

**Architecture:** L1 handles ~20% exact-repeat queries (password resets, balance checks). L2 catches ~25% paraphrased variants (FAQ-style fee questions) with threshold 0.93. L3 reduces cost on remaining 55% via shared system prompt + FAQ documents as prefix. Per-tenant Redis key namespacing (`{tenant}:llm_cache:*`) enforces isolation.

### Scenario C -- Multi-Agent Research System (100 agents, shared knowledge base)

**Problem.** 100 agents share a 200-page internal knowledge base as system prompt. Tree-shaped conversation histories (5-10 branches per topic). Current: $120K/month on self-hosted H100 cluster. Target: 50% GPU reduction, p50 TTFT < 500ms.

| Alternative | GPU Reduction | TTFT p50 | Branching Support | Verdict |
|---|---|---|---|---|
| **SGLang + RadixAttention + llm-d (recommended)** | ~60% | < 300ms | Excellent (tree structure) | Selected |
| **vLLM + APC** | ~35% | < 500ms | Poor (linear chains only) | Insufficient for trees |
| **Separate instance per agent group** | ~20% | < 200ms | N/A | Wastes GPU |

**Decision:** RadixAttention's radix tree naturally maps to branching conversation histories. Shared 200-page knowledge base stored once in tree root. LMCache with KVFlow for proactive prefetching. TTL jitter (base=300s, jitter=60s) prevents stampedes during hourly knowledge base refresh.

### Scenario D -- FAQ Semantic Cache vs Exact-Only Prefix

**Problem.** Public help-center: high-volume paraphrases of ~200 gold answers. A second surface is an authenticated agent with tools (ticket state, wallet). One stack is proposed "because both are caching."

**Decision.** Split them. FAQ: tagged semantic cache with labeled false-hit eval set, tenant TAG, short TTL, start cosine 0.88. Agent: semantic OFF, exact prefix only. A paraphrase of "what's my balance?" must not return another user's number. Exact prefix already discounts the tools block at 0.1x without skipping decode.

---

## Common Failure Modes

| Failure | Cause | Detection | Mitigation |
|---|---|---|---|
| **Prefix thrash (silent 1.25x loop)** | Timestamp/unsorted JSON/tool reorder in cached span | `cache_write_tokens >> cached_tokens` | Move clocks after breakpoint; canonical JSON; explicit breakpoints |
| **Sub-floor silent no-op** | Prefix below model minimum (1024-4096) | Cache usage = 0, no error | Lengthen stable instructions; pick model whose floor you meet |
| **Cache stampede** | N parallel cold prefixes; entry ready only after first response begins | Write spike, near-zero reads | Single-flight; Anthropic `max_tokens: 0` pre-warm |
| **Overflow miss** | >15 RPM per key+prefix on OpenAI/Azure | RPM up, `cached_tokens` down | Shard `prompt_cache_key` |
| **Semantic false hit** | Threshold too loose; missing tenant TAG | Gold FAQ eval false-hit rate | Distance from eval (0.88 start); TTL; TAG tenant+model+policy |
| **Cross-tenant timing leak** | Unsalted KV; TTFT oracle | TTFT bimodal by tenant | HMAC salt; >=vLLM 0.9.0; `sha256_cbor` |
| **Rolling-deploy cold** | New pods empty; TRT-LLM no reuse until first request terminates | TTFT + write SKU spike post-rollout | HiCache L3 shared namespace; write-cost budget |
| **Stale tool/schema** | 1h cached tools vs new runtime schema; no purge API | Undefined tool-call behavior | Version string in tools block; wait TTL |
| **Dashboards that lie** | 10k prefix + 50-token unique query = 99% token hit | Token hit high, user quality flat | Board token AND request hit AND write/read ratio |
| **Decode-heavy no win** | Long output; prefix cache does not change ITL | $ unchanged | Do not buy prompt cache for summarization output tokens |
| **Gemini storage idle tax** | Explicit cache, low QPS | Storage $ > uncached input | Use implicit, or delete unused caches |
| **xxhash collision** | Multi-tenant xxhash APC | Undefined / leak | Use `sha256_cbor` |
| **Truncation kills hits** | Context window trimming | Hit rate collapse | LMCache: truncation destroys prefix identity |
| **Region failover "breaks" cache** | Hosted KV cannot cross regional boundaries | All miss post-failover | Plan write-cost budget for re-warm |

---

## Interview Q&A

**Q1. Explain the five LLM cache layers to someone who only knows Redis.**
KV cache is scratch paper for the request still running. Prefix/APC is exact left-to-right reuse of that scratch across requests -- you still generate the suffix. Hosted prompt cache is the same exact-prefix KV as a billed product (1.25x write, 0.1x read). Semantic cache is Redis-with-vectors of answers, and a hit skips the model entirely. Application cache is exact memoization of a workflow node.

**Q2. Where do you put the cache breakpoint on an 8k tools agent?**
After tools and static system, before timestamps, tenant RAG, and the latest user/tool message. Anthropic looks back 20 content blocks and writes only at the marker -- a timestamp on block 6 is infinite writes. OpenAI 5.6 implicit marks the latest message; I switch to explicit so I do not pay 1.25x to cache volatility.

**Q3. Give me the cost model for 1,000 agent calls.**
8k prefix + 400 suffix + 400 out. Sonnet 5 uncached: $20.80/1k. One 5m write + 999 reads: ~$6.42/1k (69% off). Break-even is 2 hits at 1.25/0.1. Luna: ~$0.72 vs $2.16. Gemini explicit 100k corpus idle 24h is $11 storage+create with zero reads -- worse than not caching.

**Q4. KV cache vs prefix cache vs prompt cache vs semantic -- one sentence each.**
KV: this sequence's K,V so decode is not prefill. Prefix/APC: hashed token blocks shared across requests. Hosted prompt cache: the same KV as a billed product you never see. Semantic: embedding kNN returns a previous completion, skipping the model entirely.

**Q5. What SLO do you put in the contract for cache?**
I do not quote a vendor cache p99 -- nobody publishes one. I SLO prefix-hot TTFT separately from miss+prefill. I treat Anthropic 11.5s->2.4s and KVGov 149.6->32.8ms as existence proofs. I shard before ~15 RPM, circuit-break Redis independently of the FM, and I accept that p99 is a miss.

**Q6. Cache stampede on a cold workflow fan-out -- what do you do?**
Anthropic: N identical prefixes = N writes because the entry exists only after the first response begins. I serialize a `max_tokens: 0` pre-warm, then fan out. Self-hosted: single-flight the prefix hash and pin one replica before opening.

**Q7. Multi-tenant vLLM without salt. What happened?**
The cache is globally content-addressed. TTFT is an oracle (KVGov 0.22; CVE-2025-46570 AUC 0.99 at 8 tokens). I inject HMAC(server_secret, tenant_id) at the gateway, upgrade to >=0.9.0, ban xxhash, and never accept a client cache_salt. Hostile tenants get separate engines.

**Q8. Why did token hit rate look like 99% while we still paid 1.25x?**
A huge static prefix plus a tiny unique suffix is 99% token hit even if every question is unique. Or we thrashed: high cache_write_tokens, zero cached_tokens. I board both meters plus write/read ratio and RPM per key.

**Q9. FAQ bot vs wallet agent -- same semantic cache?**
No. FAQ with a labeled false-hit eval set, tenant TAG, short TTL, start cosine 0.88. Wallet/agent: live tool state -- a paraphrase of "what's my balance?" must not return another user's number. Exact prefix already discounts the tools block at 0.1x without skipping decode.

**Q10. We failed over to EU and prompt cache "broke."**
OpenAI caches cannot cross regional processing boundaries. Anthropic/hosted KV have no dump API. DR that assumes a warm prefix in the failover region is wrong. RTO is re-warm (write SKUs) or a self-hosted L3 in that region. Regional processing is also +10% on eligible models from 2026-03-05.

**Q11. When should you avoid semantic caching?**
Personalized requests, stateful multi-turn interactions, creative generation, tool-using agent loops, regulated domains, and high-stakes decisions where "close enough" is unsafe. InputSnatch extracted legal-domain prompts from semantic caches at 43-100% ASR.

**Q12. Biggest cache anti-pattern?**
Thinking "cache hit rate" alone proves success. You need request hit rate, token hit rate, write/read ratio, and correctness checks. A 99% token hit rate with 100% write rate means you are paying 1.25x on every request.

---

## Key Numbers to Memorize

### KV / Algorithms
| Number | What |
|---|---|
| **16 / 128 / 256** | vLLM V1 block tokens; TRT-LLM default; LMCache chunk default |
| **< 4% / 2-4x / 24x** | PagedAttention waste; vLLM vs Orca; vs HuggingFace |
| **516 / 70 / 328 KB/tok** | Llama-405B GQA / DeepSeek-V3 MLA / Qwen-72B GQA (BF16) |
| **~4.13 GB / ~0.56 GB** | 8k prefix KV: Llama-405B vs DeepSeek-V3 |
| **93.3% / 8x** | MLA KV reduction vs MHA; GQA 64Q/8KV vs MHA |
| **5x / 6.4x / 3.7x** | SGLang: throughput (LMSYS / NeurIPS) / latency |
| **28.3x TBT** | Sarathi: naive hybrid batch vs decode-only |
| **> 128 waiting -> FCFS** | SGLang LPM complexity valve |
| **57x / 2x** | llm-d vs naive LB: response time / throughput |

### $ / Multipliers
| Number | What |
|---|---|
| **1.25x / 0.1x** | GPT-5.6 & Anthropic 5m write / read |
| **2.0x / 0.1x** | Anthropic 1h write / read |
| **0.025x / ~0.032 / 0.5x** | Fable 5.1 hit; DeepSeek hit/miss; Fireworks default |
| **2 / 3** | Break-even n at 1.25/0.1 and 2.0/0.1 |
| **1,024 / 512-4,096 / 4,096** | OpenAI 5.6+ min; Anthropic by model; Haiku 4.5 floor |
| **30m / 5m / 60m / <=24h** | OpenAI 5.6 TTL; Anthropic default; Gemini explicit; Gemini implicit |
| **~15 RPM** | OpenAI/Azure overflow per prefix+key |
| **~$6.42 / $20.80** | Sonnet 5 8k+400/400 per 1k: cached vs uncached |
| **~$0.72 / $2.16** | Luna same shape: cached vs uncached |
| **$4.50/MTok-hr** | Gemini Pro explicit storage |
| **$108** | 1M-token Gemini Pro cache idle 24h |

### Latency / Security
| Number | What |
|---|---|
| **11.5s -> 2.4s (-79%)** | Anthropic 100k book TTFT |
| **149.6 / 32.8 ms (0.22)** | KVGov A100 cold/cached TTFT (ratio) |
| **100% / ~10^83 / O(1)** | EarlyBird ASR at block_size=1; V1 free-text guesses; template fields |
| **99-100%** | PROMPTPEEK on SGLang radix |
| **43-100%** | InputSnatch semantic legal domains |
| **AUC 0.99 at 8 tokens** | CVE-2025-46570; patched >=0.9.0 |
| **93%** | Boundary-salt benefit retained (research) |
| **~200k tokens / ~500 pages** | Anthropic: skip RAG, cache the corpus |

---

## Quick Reference

- **Five layers, not one knob:** KV (intra-request) != prefix/APC (cross-request) != hosted prompt cache (billed KV) != semantic (skip LLM) != application (memoized node)
- **Stable left, volatile right:** Tools+system before breakpoint; user/RAG/timestamps after
- **Break-even:** n >= 2 at 1.25/0.1; n >= 3 at 2.0/0.1
- **Salt or leak:** HMAC(server_secret, tenant_id) at gateway; never client JSON; CVE-2025-46570
- **Fallback chain:** Semantic (FAQ only) -> exact prefix -> uncached generate. Never reverse on safety paths
- **Semantic cache:** Only for bounded-intent FAQ; never for tool-using, regulated, or personalized traffic
- **p99 is a miss:** No vendor publishes cache p99. Shard before ~15 RPM. Circuit-break each layer independently
- **Hosted cache is ephemeral:** No dump API. Failover region = cold. Plan write-cost budget for re-warm
- **Do not cache decode:** Prefix cache helps TTFT, not long output generation
