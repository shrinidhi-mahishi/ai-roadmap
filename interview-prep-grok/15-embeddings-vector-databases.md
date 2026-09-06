# Module 15: Embeddings & Vector Databases

**Study + interview prep.** Grounded in research dated 2026-09-03 (74 sources). This file is the retrieval **index/embedding substrate** — model pin, similarity metric, ANN type, quantization codec, vendor ops/cost, sparse encodings **in the same index product**. Retrieve-then-generate, hybrid RRF/rerank, GraphRAG, and ACL-aware retrieve live in [01-rag](01-rag.md); mention `hybrid_score_norm` here as an **index-product trap**, fusion policy stays in 01. Embedding fine-tune (sentence-transformers `MultipleNegativesRankingLoss` + `MatryoshkaLoss`; OpenAI does not FT `text-embedding-3-*`) lives in [02-fine-tuning](02-fine-tuning.md). Pin the schema invariant: **`model_id + dimension + similarity metric + index type + quantization codec`**. Changing any is a full re-embed and/or rebuild. `$ per 1k` is **[inferred]** from published rates × a stated query shape, not a vendor SKU. Public pages do **not** publish a global p50/p95/p99 SLO for vector search — missing percentiles are architecture-derived **[inferred] policy targets** and are marked. Pinecone “O(100 ms)” / “sub-100 ms” is a **design target**, not an SLO. Cohere self-serve $/1M is **not confirmed** from static pricing HTML — do not invent $0.12 as a Cohere SKU. Zero-Trust MCP (OAuth 2.1 / RFC 8707 / hash-pins / gateway PEP) is **§4.4 in this file**; there is no module 19 yet, so do not defer it.

---

## What Is This?

**The vector index is not RAG.** RAG is retrieve-then-generate (module 01). This layer is the **barcode printer + warehouse racking**: an embedding model maps text (and sometimes image/PDF) to a vector; an ANN index finds near-neighbors under a pinned metric; a codec may store those vectors compressed; a sparse/BM25 lane may live **in the same product**. Generation, RRF, rerank, GraphRAG, and “put ACL in the prompt” are out of scope here.

Lewis’s non-parametric memory is an **index with a schema**. Two independently scaled planes share that schema and the vector files:

| Plane | Owns | Failure if coupled |
| --- | --- | --- |
| **Control** | Collection create: `model_id`, `dimension`, `metric`, vector type (dense/sparse/hybrid), index algorithm (or vendor auto-select), quantization codec, payload schema, tenancy (namespace / tenant shard / RLS), alias/snapshot pointer | Query p99 tracks reindex; a schema change silently mismatches query embeddings |
| **Data** | Embed API, upsert WAL, ANN walk, payload/metadata pre-filter, sparse inverted/SPLADE lane, quantization encode/rescore | Ingest schema drift poisons recall without HTTP 4xx |

Think of a warehouse. **Control** is how you label bays (SKU printer model, carton size, aisle map, packing codec, who may enter which cage). **Data** is the forklift walk: print a barcode for the query, cache it if you have seen that carton before, walk the graph/cells, skip locked cages, unpack a shortlist, return IDs. **Persistence** is the slabs/WAL/HNSW files — not the forklift. **Tool proxies** are badge-gated MCP `query_index` / `upsert` — not a master key that dumps `include_values`. If you merge cataloging into every pick, a stuck printer stalls every order, and swapping the barcode format without rebuilding the racking silently returns the wrong pallets.

**Interview one-liner:** I pin `model_id + dim + metric + index type + codec` in the schema. I do not “tune Pinecone `M`/`ef`.” Pinecone has **never used HNSW** (Ananas / PQFS / IVF per slab). BBQ-HNSW is **Elasticsearch**.

## Why It Matters

Every private-data AI product that cannot stuff the corpus in the prompt (Anthropic’s ~200k-token skip-ANN rule is in 01) still has **this** layer: support search, legal matter retrieve, tenant-isolated SaaS. Interviews test whether you can split **control vs data plane**, refuse to compare MTEB v1 64.6 to MMTEB 70.58, name **cosine ≡ IP ≡ −L2² iff L2-normalized**, budget **RU per GB of targeted namespace** (not embed pennies), put **ACL in the index predicate**, run **PII detect → redact → audit before embed** (Vec2Text: **92% exact** on 32 tokens; **89% MIMIC names**), and put **Zero-Trust MCP on `query`/`upsert`/`fetch_values`** in this module — not “see later.”

The cost trap is not `$0.02/1M` for `3-small`. It is **one fat namespace**: 20M × 1536-d at 50 QPS is **~$263k/mo** Pinecone Standard reads vs **~$8.3k/mo** with tenant namespaces **[inferred, Plan 1]**. The latency trap is treating Pinecone “sub-100 ms” as an SLO. The security trap is redacting PII in the **prompt** after you already embedded it.

---

### 1. System Topology & Data Flow

Five planes, **not** a single “embed then search” function. Construction of the index is control; the ANN walk is data; WAL/slabs/HNSW files are persistence; MCP/SDK clients are tool proxies behind a gateway; recall canaries and invert-surface audits are telemetry.

```
                         TELEMETRY / OBSERVABILITY SINKS
         ┌──────────────────────────────────────────────────────────────────┐
         │  recall@k canaries (filter-selectivity slices)                   │
         │  nDCG@10 golden set (in-domain; not MTEB as SLO)                 │
         │  RU / QPS / WU meters   embed TPM/RPM   cache hit-rate           │
         │  invert-surface: include_values audits (must be false for asst.) │
         │  WORM: (cid, index, ns, filter_digest, k, include_values=false)  │
         │  watermark lag; alias generation; dim/metric mismatch 4xx        │
         └────────────▲─────────────────────▲──────────────────▲────────────┘
                      │ spans               │ meters            │ audit
                      │                     │                   │
┌─────────────────────┴─────────────────────┴───────────────────┴───────────┐
│ CONTROL PLANE  (schema create — LLM-free; allow/deny is the predicate)    │
│                                                                           │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌─────────┐ │
│  │ model_id   │ │ dimension  │ │ metric     │ │ index type │ │ codec   │ │
│  │ pin        │ │ 256–4096   │ │ cos/IP/L2  │ │ HNSW/IVF/  │ │ SQ/PQ/  │ │
│  │ (Voyage-4  │ │ MRL prefix │ │ sparse=dot │ │ Ananas/    │ │ BQ/BBQ/ │ │
│  │  family OK)│ │            │ │ product    │ │ PQFS/Disk  │ │ halfvec │ │
│  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └────┬────┘ │
│        │              │              │              │              │      │
│        ▼              ▼              ▼              ▼              ▼      │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ create_index / CREATE INDEX: tenancy (ns / shard / RLS), alias,    │  │
│  │   payload schema, input_type (Cohere), vector_type dense|sparse    │  │
│  │ Pinecone: you do NOT set M/ef — slabs pick Ananas / PQFS / IVF     │  │
│  │ ES: bbq_hnsw / bbq_disk.  pgvector: hnsw | ivfflat + ops class     │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────┬──────────────────────────────────────────┘
                                 │ pinned schema + alias pointer
                                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ DATA PLANE  (untrusted query text — embed, then ANN; tools do not dump)   │
│                                                                           │
│  q_text → PII redact → query embed (or cache hit) → optional L2-norm      │
│        → ANN walk + payload bitmap → oversample → rescore → IDs only      │
│                                                                           │
│  ┌────────────── TOOL PROXIES (least privilege — not an omnibus dump) ─┐ │
│  │ embed client (OpenAI/Voyage/Cohere/self-host)  vector-DB SDK        │ │
│  │ MCP behind gateway PEP:  query_index  |  upsert  |  fetch_values    │ │
│  │   query_index: k + filter from verified auth — NOT from tool args   │ │
│  │   upsert: ingest workers only; idempotent chunk_id keys             │ │
│  │   fetch_values / include_values: DENY for assistants (invert surface)│ │
│  │ Identity = verified token / RunContext. MCP is not the namespace PDP│ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│  Sparse lane (optional, SAME product): SPLADE/BM25 coords; Pinecone      │
│  hybrid index metric = dotproduct ONLY. Fusion policy (RRF/α) → 01.      │
└─────────┬───────────────┬─────────────────┬─────────────────┬─────────────┘
          │               │                 │                 │
          ▼               ▼                 ▼                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE LAYER  (files ≠ query path; restore is often a NEW index)     │
│                                                                           │
│  ┌──────────────┐ ┌──────────────┐ ┌─────────────┐ ┌──────────────────┐  │
│  │ Pinecone     │ │ Qdrant/      │ │ pgvector    │ │ ES / Chroma      │  │
│  │ WAL on S3    │ │ Weaviate     │ │ Postgres WAL│ │ wal3 / commitlog │  │
│  │ immutable    │ │ WAL+HNSW     │ │ HNSW/IVF    │ │ BBQ files /      │  │
│  │ slabs / ns   │ │ snapshots    │ │ files; PITR │ │ SPANN+SPFresh    │  │
│  │ memtable     │ │ RF on shards │ │             │ │                  │  │
│  └──────────────┘ └──────────────┘ └─────────────┘ └──────────────────┘  │
│  Alias/name swap = promotion. Backup restore = new index, then flip.      │
│  Dual-index during model migrate.  Do not in-place mutate dim/metric.     │
└───────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not couple):**

| Plane | Lives here | LLM-free? | Failure if coupled |
| --- | --- | --- | --- |
| **Control** | `create_index` / `CREATE INDEX`, model pin, dim, metric, codec, tenancy, alias | Yes | Query embeddings from model B against index A; p99 = rebuild |
| **Data** | Embed HTTP, ANN walk, filters, sparse lane, rescore | Embed is a model call; ANN is not | Letting the model pick `namespace` or `include_values=true` |
| **Persistence** | WAL, slabs, HNSW/IVF files, snapshots | Yes | Treating a snapshot restore as in-place mutate of dim |
| **Tool proxies** | SDK + MCP `query_index`/`upsert` behind gateway | Yes for authz | Omnibus `search(collection, values=true)` |
| **Telemetry** | recall@k, nDCG golden, RU, invert audits | Yes | Logging `values` “for debug” |

**Interview traps in this diagram:**

- **Pinecone has never used HNSW** in serverless or the old pod architecture (stated 2026-06-14). Per-slab: **Ananas** (SimHash / FJLT + sign bit) for ~≤10k vectors, **PQFS** for ~10k–100k, **IVF+PQFS** for ~≥100k. “Tune Pinecone `M`/`ef`” is a fail.
- **BBQ-HNSW / DiskBBQ is Elasticsearch**, not Pinecone. Pinecone Ananas is 1-bit after FJLT — different codec.
- Sparse/hybrid in one Pinecone vector index is **`dotproduct` only**. Cosine dense + unbounded sparse without query-side `hybrid_score_norm` drowns dense — **trap in this product**; **α / RRF policy is 01**.

**Request-flow narrative (query embed → cache → ANN + filter → rescore → IDs):**

1. **Control / construction (once).** Application creates the index with pinned `model_id`, `dimension`, `metric`, index type (or Pinecone auto), codec, tenancy, alias. Payload fields used in filters get **payload indexes before ingest** (Qdrant) or you pay a degraded filtered HNSW. Alias points at generation N. MCP gateway allowlists `query_index`; denies `include_values` for assistant principals.
2. **Ingest (write path, independently scaled).** Temporal/Kafka workflow: PII detect→redact→audit → embed batch (splitter ≤2048 inputs / 300k tokens) → idempotent upsert keyed by `chunk_id` → watermark. Pinecone write ack **<100 ms** to WAL; **visibility is seconds** (memtable then slabs). Not the query SLO.
3. **Query admit.** Gateway PEP binds `tenant_id` / matter-id from the **verified token**, not from tool JSON. Filter predicate is composed here. `k` and `include_values=false` are policy, not model suggestions.
4. **Query embed.** Cache key = `(model_id, dim, metric, normalized_query_hash)`. Hit: skip embed $ and embed latency. Miss: embed API with `input_type=search_query` (Cohere) or Voyage-4-**lite** against a **large**-embedded corpus **only inside that family**. Do not embed 3-small against a 3-large index.
5. **ANN + filter.** Pinecone: roaring bitmap ∩ eligible IDs, then Ananas/PQFS/IVF with **bypass** if the bitmap is small and **adaptive scan** as selectivity drops (ICML). HNSW products: in-walk skip (Qdrant/ES ACORN) or **iterative_scan** (pgvector) — not post-filter-and-pray. Sparse lane may prefetch in the same product; **do not fuse inside Qdrant prefetch on multi-shard**.
6. **Quantization rescore.** ANN ran in SQ/PQ/BQ/BBQ space. Oversample top-\(cK\) (BBQ default **3×**; Qdrant BQ examples **4×**). Rescore with residuals/float. Return **IDs + scores**, never `values`, to the assistant.
7. **Observe / stop.** Span: cache hit, embed ms, ANN ms, rescore ms, RU, filter selectivity, `include_values=false`. Golden-set nDCG canary. Alias unchanged. Generation is **01**.

---

### 2. Core Mechanics & Algorithms

#### 2.1 Invariants (index, not a RAG loop)

**I1.** Schema pin is five-tuple: `model_id + dimension + metric + index type + quantization codec`. Any change → new index (or MRL truncate **+ re-normalize** on an MRL-trained model only).

**I2.** Cosine, inner product, and **negated** L2-squared rank identically **iff every vector is L2-normalized**. Otherwise they are different geometries. Pin the metric the **embedder was trained with**.

**I3.** Pinecone serverless **never HNSW**. Elasticsearch BBQ-HNSW is not portable advice onto Pinecone.

**I4.** Quantization is a **codec in front of ANN**, not a different ANN. Production path: encode → ANN compressed → **oversample + rescore**. Skipping rescore steals recall.

**I5.** Filters belong **in the walk or as a bitmap pre-filter**. Post-filter after ANN is both an under-k recall bug and, if used as ACL, an **authorization bug** (unauthorized vectors entered the candidate set). ACL-aware retrieve policy: **01**.

**I6.** In-domain nDCG/recall **beats** any public board. Do not compare MTEB v1 to Eng v2 to MMTEB to RTEB.

#### 2.2 MTEB / MMTEB / Eng v2 / RTEB (do not mix boards)

**MTEB (eng, v1)** (Muennighoff et al., 2022/23): English-heavy, eight task types. Retrieval is **one slice**. OpenAI still publishes v1-style **mean** scores: `text-embedding-3-small` **62.3%**, `text-embedding-3-large` **64.6%**, `ada-002` **61.0%**, max input **8192** tokens. A 2-point overall gap is not your nDCG@10.

**MMTEB** (Enevoldsen et al., ICLR 2025): **500+** tasks, **250+** languages, plus instruction-following, LongEmbed, CoIR. At publication the best **public** model on their representative multilingual set was **multilingual-e5-large-instruct (560M)**, not a 7B LLM. They ship a downsampled English zero-shot split that keeps ranking order at **~2% of original documents**.

**MTEB (eng, v2)** is a **different board**. Qwen’s tables (their runs, leaderboard snapshot **2025-06-06**):

| Model | Board | Mean (task) | Retrieval slice | Dims |
| --- | --- | --- | --- | --- |
| Qwen3-Embedding-8B | MMTEB multilingual | **70.58** | 70.88 | 4096 (MRL) |
| Qwen3-Embedding-8B | MTEB Eng v2 | **75.22** | **69.44** | 4096 |
| Qwen3-Embedding-4B | MMTEB / Eng v2 | 69.45 / 74.60 | 69.60 / 68.46 | 2560 |
| Qwen3-Embedding-0.6B | MMTEB / Eng v2 | 64.33 / 70.70 | 64.64 / 61.83 | 1024 |
| BGE-M3 | MMTEB multilingual | 59.56 | 54.60 | 1024 |
| text-embedding-3-large | MMTEB multilingual | 58.93 | 59.27 | 3072 |
| gemini-embedding-exp-03-07 | MMTEB / Eng v2 | 68.37 / 73.3 | 67.71 / 64.35 | — |
| multilingual-e5-large-instruct | MMTEB | 63.22 | 57.12 | — |

**Do not** rank OpenAI **64.6 (v1 overall)** against Qwen **75.22 (Eng v2)** or **70.58 (MMTEB, 2025-06-05)**. Different task sets. Qwen3-8B was **#1 MMTEB multilingual 70.58 as of 2025-06-05** (vendor).

**RTEB** (MTEB maintainers + Voyage, ~2025-10-01): retrieval-only, legal/healthcare/finance/code, ~20 languages, open **and private** sets. Voyage-4-large launch blog claims +**3.87 / +8.20 / +14.05** average nDCG@10 vs Gemini Embedding 001 / Cohere Embed v4 / OpenAI v3 Large on **their** 29-dataset RTEB run. **Governance caveat:** MTEB maintainers **temporarily removed the private RTEB column** because Voyage (MongoDB) had access to the private eval data (`mteb#3934`). Private datasets remain in the library; the public “Mean (Private)” column is **not** a trusted procurement number until the set is diversified.

**Production rule:** shortlist on language, dim, license, context; bake three models on **your** queries. MTEB retrieval is largely BEIR-descended; contamination is the expected failure mode.

#### 2.3 Model cards (index-relevant)

**OpenAI `text-embedding-3-*`.** `3-small`: default **1536-d**, MTEB v1 **62.3**, **$0.02/1M** (from 62,500 pages/$ at OpenAI’s ~800-token page convention **[inferred from pages/$ table]**; matches the list rate used in 01). `3-large`: default **3072-d**, **64.6**, **$0.13/1M**. `ada-002`: 1536-d, 61.0 — do not start new indexes. Matryoshka: `dimensions` API shortens; vendor claim **256-d `3-large` still beats unshortened 1536-d ada-002**. Manual truncation **must be L2-normalized**. Cited cutoffs: small 512/1536; large 256/1024/3072. Hard limits: **8192** tokens/input; **2048** inputs/request; **300,000** tokens summed. Batch: **50%** off, 24h, **50k** embedding inputs/batch, **200 MB**. OpenAI does **not** fine-tune `3-*` — that story is **02**.

**Cohere `embed-v4.0`.** Multimodal text + image + mixed PDF; **128k** context; Matryoshka **256 / 512 / 1024 / 1536 (default)**. `embedding_types`: float, int8, uint8, binary, ubinary. **`input_type`**: `search_document | search_query | classification | clustering` — pin in schema (asymmetric retrieval **within one model**). Rate: Embed **2,000 inputs/min**; images **400/min** production. Dedicated Model Vault Embed 4 Small **$4.00/hr**, **$2,500/mo**. Self-serve **$/1M is not confirmed from static pricing HTML**.

> ⚠️ Gap: Cohere’s public pricing page (fetched 2026-09-03) renders Model Vault hourly/monthly instance rates, not a scrapeable `$ / 1M` table. Docs say embed is billed per token. Aggregators commonly quote **$0.12 / 1M text** and **$0.47 / 1M image** — **not a Cohere SKU confirmation**. Do not budget $0.12 as if it were a list price.

**Voyage 4 family** (2026-01-15). **Shared embedding space:** `voyage-4-large` / `voyage-4` / `voyage-4-lite` / open `voyage-4-nano` (Apache 2.0) produce **compatible** vectors. Vendor-recommended high-QPS pattern: embed **corpus with large, queries with lite**. `voyage-4-large`: first production embedding **MoE**; vendor serving cost **40%** below comparable dense. MRL **2048 / 1024 / 512 / 256**; dtypes float32, int8/uint8, binary. Price: large **$0.12/1M**, mid **$0.06/1M**, lite **$0.02/1M**; **200M free** tokens/account on the 4-series (not Batch). Batch: **33%** off, **12h**, **1 GB** files, **100k** inputs/batch. Domain SKUs still listed: `voyage-law-2`, `voyage-finance-2`, `voyage-code-2` at **$0.12/1M**, **50M** free. Shared space does **not** apply across vendors or to OpenAI 3-small vs 3-large.

**BGE-M3** (~568–569M, MIT, **1024-d** dense, **8192** tokens, **100+** languages). One forward pass: **dense + sparse (lexical weights via linear+ReLU) + ColBERT-style multi-vector** (MaxSim). Index implication: populate a dense HNSW **and** a sparse inverted/SPLADE-like lane from the same encoder. Dense-only MTEB is not the product story.

**Qwen3-Embedding.** 0.6B / 4B / 8B, Apache 2.0, **32K** context, native dims **1024 / 2560 / 4096**, MRL custom dims, instruction-aware, **100+** languages including code. Dual-encoder; `[EOS]` hidden state. Training: weak-sup contrastive → supervised → merge. Self-host cost is GPU, not $/1M. Fine-tune of this family (if you go there) is **02**.

**Jina.** `jina-embeddings-v5-text-small`: **677M**, **1024-d**, **32K**, MRL down to **32**, vendor **67.0 MMTEB** task-avg, **71.7 MTEB English**. `v5-text-nano`: **239M**, **768-d**, **8K**, vendor **65.5 MMTEB**. `v5-omni-*`: shared space text/image/audio/video; byte-compatible with v5-text in that family (vendor: no reindex when swapping text↔omni). `v4`: multimodal, **2048-d**, **32K**, optional late-interaction; `late_chunking` flag. Hosted: Embed API paid **500 RPM / 2M TPM**, premium **5,000 RPM / 50M TPM**. Exact $/1M is on `GET https://api.jina.ai/v1/models` (`pricing.prompt`); third-party catalogs often list **$0.05 / 1M** / nano **$0.02 / 1M** — confirm per model ID.

**Matryoshka Representation Learning** (Kusupati et al., NeurIPS 2022): train so **prefixes** of the vector remain informative. Index trick: store full dim, serve **256-d first-pass ANN**, rescore with full dim — **only if MRL-trained**. Naive PCA/truncate of a non-MRL model is a different (worse) curve. pgvector `subvector()` can index a prefix.

#### 2.4 Similarity metrics

| Metric | Formula / notes | When |
| --- | --- | --- |
| **L2 / Euclidean** | \(d(\mathbf{a},\mathbf{b})=\|\mathbf{a}-\mathbf{b}\|_2\). Pinecone: **lower score = closer**. Magnitude-sensitive | Unnormalized count-like encodings; rare for modern contrastive embedders |
| **Inner product / dot** | \(\mathbf{a}\cdot\mathbf{b}=\|\mathbf{a}\|\|\mathbf{b}\|\cos\alpha\) | Two-tower recommenders (magnitude ≈ popularity); **required** for Pinecone sparse/hybrid |
| **Cosine** | \(\mathrm{sim}=\frac{\mathbf{a}\cdot\mathbf{b}}{\|\mathbf{a}\|\|\mathbf{b}\|}\in[-1,1]\) | Default for OpenAI/Cohere-class L2-trained embedders |

**Equivalence:** L2-normalized ⇒ \(\mathbf{a}\cdot\mathbf{b}=\cos\) and \(\|\mathbf{a}-\mathbf{b}\|_2^2=2-2\cos\). Ranking by cosine, IP, and negated L2-squared is then identical. Jina `normalized: true` is this (L2=1 so cosine via dot).

**Pinecone metric constraints:** dense allows `cosine` / `euclidean` / `dotproduct` (default cosine). Sparse = **`dotproduct` only**. Hybrid dense+sparse in one vector index = **`dotproduct`**. Document-schema indexes (API `2026-01.alpha` preview): a record may mix FTS `string` (BM25), `dense_vector`, `sparse_vector`; query ranks with `score_by` one signal at a time.

**pgvector operators:** `<->` L2, `<#>` **negative** inner product (Postgres ASC-only index scans), `<=>` cosine **distance** (similarity = `1 - <=>`), `<+>` L1, `<~>` Hamming, `<%>` Jaccard. For unit-normalized OpenAI vectors, pgvector recommends **IP (`<#>`)** for exact-search speed (skips the cosine divide); rankings match cosine. Weaviate default distance **cosine**; `distance` is **immutable** after collection create.

**Mismatch failure:** indexing cosine-trained OpenAI/Cohere with L2, or IP-trained `msmarco-bert-base-dot-v5` with cosine, **silently** reorders neighbors.

#### 2.5 Indexing algorithms

**HNSW** (Malkov & Yashunin): layered NSW; search starts at a sparse top layer and greedily descends. Query cost ~logarithmic in \(N\) **if RAM-resident**. Distance evaluations ≈ \(ef \times \log N\) order-of-magnitude **[inferred from algorithm, not a vendor SLO]**.

| Param | Meaning | Typical defaults |
| --- | --- | --- |
| `M` (Weaviate `maxConnections`) | Max edges per node (layer 0 often `2M`) | pgvector **16**; Weaviate **32**; Qdrant commonly **16** |
| `efConstruction` | Candidate beam at **build** | pgvector **64**; Weaviate **128**; Qdrant often **100** |
| `ef` / `ef_search` | Candidate beam at **query** | pgvector **40**; Weaviate **-1** (dynamic, min 100 max 500 factor 8) |

Raise `efConstruction` → better graph, slower inserts. Raise `ef` → better recall, slower queries. `ef > 512` is diminishing on Weaviate’s stated curve. Graph RAM scales with `M × N` **plus** vectors. If HNSW falls out of RAM, latency cliffs — DiskANN / DiskBBQ / Pinecone slabs exist because of this.

**HNSW query walk.** Entry point on the top layer. At each layer, greedy-search the neighborhood until no neighbor is closer; descend; repeat. At layer 0, expand a candidate list of size `ef`, return closest `k`. **Post-filter** throws away candidates after the walk (under-k). **In-walk skip** (Qdrant filterable HNSW, ES ACORN/Lucene) refuses edges to disallowed IDs and must explore more. **Bitmap pre-filter** (Pinecone roaring) intersects eligible IDs then scores.

**HNSW insert walk.** Greedy search to find `M` neighbors; bidirectional links; maybe drop the farthest edge. Online insert is correct; a graph grown under churn ≠ bulk `efConstruction` build — rebuild when recall canaries drop. pgvector: `COPY` binary **then** `CREATE INDEX`. Weaviate `ASYNC_INDEXING` decouples object write from graph mutation.

**IVF:** k-means into `nlist` Voronoi cells; query probes `nprobe` nearest cells and scans them. pgvector start: `lists ≈ rows/1000` below 1M, `sqrt(rows)` beyond; `probes ≈ sqrt(lists)`. Build cheap vs HNSW; recall is **data-at-build** sensitive. **nprobe is the production dial**; `nlist` is the build dial.

**IVF query walk.** (1) Distance to all `nlist` centroids. (2) Open `nprobe` posting lists. (3) Scan vectors/PQ codes. (4) Optional exact rescore. Recall vs nprobe is monotonic until `nprobe = nlist`. Pinecone replaces fixed nprobe with **scan fraction** of the slab so unbalanced clusters do not starve recall.

**IVF+PQ:** posting lists hold product-quantized codes. Query: coarse nprobe, then ADC in PQ space, optional exact rescore. Memory: codes + codebook, not \(4dN\). Pinecone large slabs = IVF with **PQFS** inside each cluster.

**Naive IVF + metadata filter collapse** (Pinecone ICML 2025): fixed `nprobe` after deleting 50% by filter → recall **drops**; at **90%** filtered, true neighbors are often **outside** probed cells → recall **collapses**. Mitigations: (1) **IVF bypass** — bitmap small enough, skip IVF, scan all matches; (2) **scan fraction** instead of fixed nprobe; (3) **adaptive scan** \(f_0\cdot\sigma^{-\alpha}\) as selectivity \(\sigma\) shrinks. Figures: at 50% filter, nprobe 4→7; at 90%, 4→**45**, recall restored. YFCC ~10M-scale: mean **recall@10 = 0.989**, internal ~**20 ms**; production customer: mean **recall@100 = 0.986**, ~**75 ms** internal. **Paper internals, not client SLOs.**

**DiskANN / Vamana** (NeurIPS 2019): SSD-resident graph + **in-RAM PQ** for navigation. SIFT1B (1B × 128-d) on **one node, 64 GB RAM + SSD**: **>5000 QPS**, **<3 ms mean**, **95%+ 1-recall@1** (16 cores). Label as **paper**, not your SLO. High-recall regime: **5–10×** more points per node than in-memory HNSW. Milvus: `MaxDegree`, `SearchListSize`, `PQCodeBudgetGBRatio` default **0.125**, `BeamWidthRatio`.

**Filtered-DiskANN** (WWW 2023): build edges using **vector + label set**, not post-filter. Natural labels: **order-of-magnitude** faster filtered queries vs post-filter SOTA; SSD: **thousands of QPS**, **>90% recall@10**. pgvectorscale label-based filtered search is this lineage.

**BBQ-HNSW / DiskBBQ (Elasticsearch, not Pinecone).** BBQ: float32 → **1 bit/dim + 14 bytes corrective** ≈ **32×**; designed with **oversample + rescore** (default oversample **3×**). `bbq_hnsw`: HNSW over BBQ codes. Datasets **<384-d** may see worse accuracy. `bbq_disk` / DiskBBQ: hierarchical k-means IVF-like, BBQ-compressed, disk-primary; default dense type when **Enterprise** license allows (GA **9.4**). Vendor 1M in-RAM: HNSW BBQ index **1,054,319 ms**, latency **~3.4 ms**, recall **92%**; DiskBBQ index **94,075 ms** (~**11×** faster build), **~4.0 ms**, **91%**. `visit_percentage` ≈ nprobe. ES 9.4: restrictive DiskBBQ filters **3–5×** faster via centroid↔doc mapping. Auto-calibrate targets **90% recall@10** on ≥10k-vector segments.

**Weaviate extras:** `flat` for tiny per-tenant indexes; `dynamic` (flat → HNSW past a threshold, needs `ASYNC_INDEXING`); `hfresh`; `filterStrategy` **`acorn` default as of v1.34**; `flatSearchCutoff` default **40,000**.

**ES / OpenSearch kNN:** ES `knn.filter` is a **pre-filter**: gather `num_candidates` matching docs (default `1.5×k`, cap **10,000**). If filtered set ≤ `num_candidates`, **bypass HNSW** and brute-force. OpenSearch: **efficient k-NN** (filter inside kNN) vs **post_filter** (may return <k).

**pgvector post-filter under-k.** Default is post-index-scan. With `ef_search=40` and a **10%** selective `WHERE`, expect ~**4** surviving rows (`40 × 0.10`). Fix: `hnsw.iterative_scan = strict_order | relaxed_order` (0.8.0+), or partial indexes `WHERE category_id = 123`. Session GUCs — pin in the app.

#### 2.6 Quantization

Typical pipeline: encode at upsert → ANN in compressed space → oversample top-\(cK\) → rescore with residual/float.

| Method | Compression vs float32 | Who | Notes |
| --- | --- | --- | --- |
| Scalar int8 | **4×** | Qdrant SQ; Weaviate SQ; Cohere `int8` | Qdrant historical default; 4-bit TurboQuant now preferred except L1 |
| Product quantization | **8–64×** | Qdrant PQ; Weaviate PQ; Faiss IVF_PQ; Pinecone PQFS | Weaviate PQ **only after 10k–100k vectors/shard** |
| Binary / 1-bit | **32×** | Qdrant BQ; Weaviate BQ; Cohere binary | Best at **high dim (≥1024–1536)**; **rescore required** |
| BBQ (Elastic) | **32× + 14 B** | `bbq_hnsw` / `bbq_disk` | Asymmetric 1-bit docs / 4-bit queries by default |
| TurboQuant (Qdrant) | up to **32×** | Qdrant | Fast random rotation + global codebook; query full-precision |
| Matryoshka trim | \(d'/d\) | OpenAI/Cohere/Voyage/Qwen/Jina | Truncate prefix; **re-normalize** |
| Pinecone Ananas | 1 bit/coord after FJLT | Small slabs | SimHash, not Elastic BBQ |
| halfvec (fp16) | **2×** | pgvector `halfvec`, max **4000-d** | Index `halfvec_l2_ops` |
| pgvector `binary_quantize()` | 32× | bit Hamming HNSW | Then rerank with float |

**Qdrant:** quantized **pinned in RAM**, originals on disk vs both in RAM (`always_ram`). `rescore` default **on** only for binary and TurboQuant 1 / 1.5 / 2-bit; SQ/PQ **do not** rescore by default. `oversampling` 2.4 with `limit=100` preselects **240** quantized hits. SQ error “usually **<1%**” in their experiments; BQ up to **40×** faster via bitwise SIMD. **BQ+rescore:** OpenAI ada-002 1536-d DBpedia **0.98 recall@100** at **4×** oversample; Cohere embed-english-v2.0 4096-d Wikipedia **0.98 recall@50** at **2×**. Disable HNSW on rescore-only named vectors with `m=0`.

**Weaviate PQ:** enable only after **10k–100k / shard**; `trainingLimit` default **100,000**/shard; `centroids` default **256** (max 256); `segments` must divide `d`. BQ `rescoreLimit` applies to **flat** only; under HNSW it is **silently discarded**.

**pgvector order:** `COPY` binary → create HNSW/IVF **after** load → `CREATE INDEX CONCURRENTLY` in prod → `halfvec` to shrink the working set → binary quantize + **rerank**. HNSW vacuum is slow; `REINDEX INDEX CONCURRENTLY` then `VACUUM`.

Quantization is **not** a HIPAA control (see §4.5).

#### 2.7 Sparse encodings in the same index product

Full hybrid fusion (RRF k=60, DBSF, α, `hybrid_score_norm` **policy**) belongs to **01**. Index-layer facts:

- **Pinecone:** (1) single index dense+sparse, **metric=`dotproduct` only**; (2) separate sparse index (`pinecone-sparse-english-v0`, DeepImpact-style, max seq 512 or 2048); (3) document schema BM25 `string` + `dense_vector`. Sparse scores unbounded vs cosine \([-1,1]\) — **must** `hybrid_score_norm` on the **query** (scale dense by \(\alpha\), sparse by \(1-\alpha\)). That call is an **index-product trap** if omitted; the fusion **policy** (when RRF vs α, k=60) is 01. Hosted sparse embed **$0.08 / 1M**.
- **Qdrant:** named dense + sparse; Query API `prefetch[]` then top-level `FusionQuery` RRF/DBSF. Fusion **inside** prefetch = **per-shard** (wrong for multi-shard).
- **Weaviate:** native BM25/BM25F + vector since v1.17; `alpha` historically defaulted 0.75 dense-leaning — **set explicitly**. RSF vs rankedFusion: **01**.
- **Elasticsearch:** `sparse_vector` / **ELSER** + `knn` + BM25; BBQ on dense.
- **pgvector:** `sparsevec` up to **1,000 non-zero** elements; not BM25. True BM25: **ParadeDB `pg_search`**. Postgres `tsvector`/`ts_rank` is **not** BM25.
- **BGE-M3 / SPLADE:** learned sparse in ~30k-d BERT vocab; same collection as dense if the product supports named/sparse vectors.

---

### 3. Token Economics & NFR Analysis

Reference query unless noted: **500-token** query embed + **1 ANN**, no rerank, no `include_values`. All `$ / 1k` below are **[inferred]** from published rates × that shape — not a SKU. Do not say “see module 01” for these numbers; they are **this layer’s** bill.

#### 3.1 Embed $/1M (published) and query-embed cache

| Model | $/1M tokens | Batch | Dims | Context |
| --- | --- | --- | --- | --- |
| OpenAI `text-embedding-3-small` | **$0.02** | **50% → $0.01** | 1536 MRL | 8192 |
| OpenAI `text-embedding-3-large` | **$0.13** | **$0.065** | 3072 MRL | 8192 |
| OpenAI `ada-002` | **$0.10** (legacy) | **$0.05** | 1536 | 8192 |
| Voyage-4-lite | **$0.02** | −33% → **$0.0134 [inferred]** | 256–2048 MRL | 32k family |
| Voyage-4 | **$0.06** | −33% | same | same |
| Voyage-4-large | **$0.12** | −33% | same | same |
| Voyage-law-2 / finance-2 / code-2 | **$0.12** | — | — | confirm docs |
| Pinecone `llama-text-embed-v2` | **$0.16** | — | hosted | — |
| Pinecone `multilingual-e5-large` / sparse-english-v0 | **$0.08** | — | hosted | — |
| Cohere Embed 4 API | **not confirmed from static HTML** | — | 256–1536 | 128k |
| Cohere Model Vault Embed 4 Small | **$4.00/hr / $2,500/mo** | N/A | 1536 | 128k |
| Qwen3 / BGE-M3 self-host | GPU-hour | — | 1024–4096 | 8k–32k |

**Query-embedding cache.** Key = `(model_id, dim, metric, normalized_query_hash)`. Hit skips embed **$** and embed **latency**; ANN still runs (unless you also cache the ID list — a different, ACL-sensitive cache). Hit-rate assumption for worked numbers: **40%** repeated support queries (FAQ/ticket paraphrases).

| Path | Uncached **$ / 1k** | 40% cache **$ / 1k** |
| --- | --- | --- |
| Embed-only 3-small (500 tok × $0.02/1M) | **$0.010** | **$0.006** |
| Embed-only 3-large | **$0.065** | **$0.039** |
| Embed-only Voyage-4-lite | **$0.010** | **$0.006** |
| Embed-only Voyage-4-large | **$0.060** | **$0.036** |

Asymmetric Voyage-4 (docs **large**, queries **lite**): ingest pays large; query embed is lite **$0.010 / 1k** uncached. Cohere `search_document` vs `search_query` is the same idea **within one model**, not across sizes.

#### 3.2 Vector DB SKUs and worked `$ / 1k`

**Pinecone serverless:** Standard **$50/mo min**, storage **$0.33/GB/mo**, WU **$4–4.50/M**, RU **$16–18/M**, **100,000** namespaces, **no SLA**. Enterprise **$500/mo min**, RU **$24–27/M**, SLA **99.95%**. Starter $0 / Builder **$20/mo flat**: **no SLA**. Query RU: **1 RU per 1 GB of targeted namespace**, min **0.25 RU**. Fetch: 1 RU / 10 records. Upsert: 1 WU / 1 KB, min 5 WU. HIPAA add-on Standard **$190/mo** (6-month min); included in Enterprise. DRN: hourly per-node, **no RU rate limits**, slabs kept warm.

Worked ANN-only (Standard **$16/M RU**):

| Path | Formula | **$ / 1k queries [inferred]** |
| --- | --- | --- |
| Pinecone ANN only, 1 GB ns | 1 RU × $16/1e6 × 1000 | **$0.016** |
| Same, 100 GB shared ns | 100 RU | **$1.60** |
| Enterprise $24/M RU, 1 GB | 1 RU | **$0.024** |
| Min RU 0.25, tiny ns | 0.25 × $16/1e6 × 1000 | **$0.004** |
| Query embed 3-small + 1 GB Std | 0.010 + 0.016 | **$0.026** |
| Same, 40% embed-cache | 0.006 + 0.016 | **$0.022** |

**RU cliff (Plan 1 — 20M × 1536-d, 200 B metadata, 50 QPS, 5 tenants) [inferred].** Raw size: \(20\times10^6 \times (8 + 1536\times 4 + 200) = 20\times10^6 \times 6344\) B ≈ **126.9 GB**. Storage ≈ 126.9 × $0.33 ≈ **$41.9/mo**. Monthly queries: \(50 \times 86{,}400 \times 30 = 129.6\)M.

- **One shared 127 GB namespace:** 127 RU/query → \(129.6\times10^6 \times 127 \approx 1.65\times10^{10}\) RU/mo ≈ 16,470 million RU × $16 ≈ **$263k/mo reads**.
- Five **25 GB** namespaces: 25 RU/query → **~$52k/mo**.
- Five **~4 GB tenant** namespaces: 4 RU/query → \(129.6\times10^6 \times 4 \approx 518\) million RU × $16 ≈ **$8.3k/mo**.

This is why namespace-per-tenant is a **cost** control, not just a security control. Pinecone query cost **ignores** filter cardinality — you pay full namespace GB even if ACL matches 0.1%.

**Ingest embed.** 10M chunks × 400 tokens = 4B tokens. `3-small` sync: \(4\times10^9 \times \$0.02/10^6 = \$80\). **Batch 50% → $40**. `3-large` Batch **$260**. Voyage-4-large Batch ~**$322 [inferred]**. Pinecone rebuild tax example: 10M × ~7.14 KB ≈ **71.4M WU**; at $4.25/M WU ≈ **$303** writes plus embed **[inferred]**.

**Weaviate Cloud** (model effective 2025-10-27): bill vector-dimensions × replication + storage + backups. Flex **$45/mo**, **99.5%**; Plus **$280/mo**, **99.9%**; Premium **99.95%**. **Qdrant Cloud:** CPU+RAM+disk; no public global $/GB-RAM list — calculator. **Chroma Cloud:** writes **$2.50 / logical GiB**; reads **$0.0075 / TiB queried** + **$0.09 / GiB returned**; storage **$0.33 / GiB/mo**. **Zilliz:** CU-based; marketing floors from **$16 / million vectors / month** (capacity) / **$5** (tiered) — treat as calculator output. **pgvector:** instance $, no RU. Timescale vendor bench vs **legacy Pinecone pods** (not 2026 serverless): 50M × 768-d vs s1 at 99% recall: **28×** lower p95, **16×** QPS, **$835/mo** EC2 vs **$3,241/mo** s1 — **pods ≠ serverless quote**.

**Memory per million (vectors only, 768-d):** float32 **3.072 GB**; halfvec **1.536 GB**; int8 **0.768 GB**; BBQ ~**0.110 GB**; 1-bit **0.096 GB**. Pinecone examples: 1M × 1536-d + 1 kB meta = **7.15 GB**; 10M × 1536-d = **71.5 GB**. HNSW graph extra on self-host: DigitalOcean **1M × 1536-d HNSW ~6 GB** resident; practitioner **~20–25 KB/row** at 1536-d including graph → **20–25 GB / million [ops blogs, not a pgvector SLA]**. DiskANN PQ at `PQCodeBudgetGBRatio=0.125` → **~0.38 GB PQ / million 768-d [inferred]** + SSD for graph.

#### 3.3 Latency — published evidence, then **[inferred] policy targets**

Public pages do **not** publish a global p50/p95/p99 SLO for vector search as a class. Pinecone “interactive **sub-100 ms**” / “O(100 ms)” is a **design target**, not an SLO.

| Evidence | Number | Caveat |
| --- | --- | --- |
| Pinecone write ack | **<100 ms** to WAL | Not query visibility |
| Pinecone visibility | **seconds** (memtable) | Eventual at the product surface |
| Pinecone cold start | seconds typical; up to **~20 s** billion-scale first touch | Architecture blog |
| Pinecone ICML internals | ~**20 ms** YFCC; ~**75 ms** customer | Excludes client RTT |
| Pinecone **DRN customer** | 480M: p50 **80 ms**, p99 **170 ms** at **380 QPS**; 1.4B filtered: p50 **26 ms**, p99 **60 ms** at **5.7k QPS** | Customer benches, not a catalog SLO |
| DiskANN SIFT1B paper | **>5k QPS**, **<3 ms mean**, 95%+ recall@1 | **Paper**, 128-d, not your SLO |
| Elastic DiskBBQ vs BBQ-HNSW | ~**4.0 vs 3.4 ms**, 91 vs 92% recall, 1M in-RAM | Vendor labs |

**[inferred] policy targets** for a typical serverless query path (embed + 1 ANN, warm ns, no rerank, no `include_values`), not a vendor SLO:

| Percentile | Target **[inferred]** | Mitigations |
| --- | --- | --- |
| **p50** | **40–80 ms** | Query-embed cache; DRN or hot ns; skip cold empty namespaces |
| **p95** | **120 ms** | Namespace split so RU-walk is small; quantization + **budgeted** rescore (oversample 3–4× not 20×); payload indexes |
| **p99** | **250 ms** | Warmup cron; DRN for sustained QPS; circuit-open → BM25-only (degraded, different quality clock); never treat cold start **20 s** as in-SLO |

Embed API latency is **on top of** ANN if cache misses — bulkhead them. pgvector: no published p99; raise `ef_search` and pin `iterative_scan`. HITL/rerank clocks belong to **01**, not this SLO.

#### 3.4 Throughput and back-pressure

| Bottleneck | Limit | Back-pressure |
| --- | --- | --- |
| OpenAI embed | **2048** inputs / **300k** tok/request; org RPM/TPM | Client splitter; Batch API for ingest; 429 → jittered retry **on embed only** |
| Cohere Embed | **2,000 inputs/min** (trial and production) | Queue; do not silently drop `input_type` |
| Jina paid | **2M TPM** (premium 50M) | Confirm per model ID |
| Pinecone on-demand | RU quotas | Namespace split; DRN uncapped reads; 429 on Starter caps |
| Qdrant / HNSW | cores × `ef` trade | Lower `ef` under load; dedicated tenant shards |
| Ingest | WU + embed TPM | Temporal/Kafka; watermark; do not block query plane |

**Back-pressure design:** (1) split embed batches **before** the 2048/300k wall — 400s are cheaper than 400s; (2) bulkhead **embed API** vs **vector-DB query** vs **upsert**; (3) query-embed cache keyed by the schema four-tuple + hash; (4) circuit on embed 429 so retries do not stampede; (5) Pinecone `$in` max **10,000** — large ACL lists 400, not slow; (6) do not create 100k empty namespaces (cold-start tax).

#### 3.5 Availability, RPO/RTO, compliance

| NFR | Production stance | Competes with |
| --- | --- | --- |
| **Availability** | Starter/Builder **no SLA**. Pinecone Enterprise **99.95%**. Weaviate Flex **99.5** / Plus **99.9** / Premium **99.95**. Product SLO is **query**; ingest/rebuild is a different clock | Cost of Enterprise min $500 vs “we will page on Starter” |
| **RPO (WAL)** | Pinecone: last S3 WAL ack (**<100 ms** write path). Qdrant/Weaviate: last WAL+snapshot. pgvector: Postgres WAL / PITR. Chroma Cloud: wal3 | Durability vs “upsert then immediately query” (Pinecone visibility **seconds**) |
| **RTO (restore)** | Snapshot restore = **new index**, then **alias flip** (minutes if the copy exists) vs **rebuild** (hours: re-embed + WU). Dual-index during model migrate: RTO = flip; RPO of the **new** model = last B-index watermark | Alias minutes vs rebuild hours |
| **RPO (alias)** | Queries pin a generation. Flip after golden-set nDCG gate. Keep A until rollback window | Disk $ of two indexes vs instant rollback |
| **Compliance** | Pinecone: encryption all plans; SOC 2 all; GDPR+ISO Builder+; HIPAA Std **$190/mo** or Ent included; CMEK **one Key ARN per project** (cannot rotate in-product without support), Private Endpoints, SCIM: **Enterprise**. Quantization is **not** a HIPAA control. Vectors = source sensitivity (Vec2Text) | Debug (`include_values`) vs invert surface |

**RPO/RTO [inferred from architecture, not vendor RPO SKUs]:** RPO_index = last compacted slab / last Postgres commit. RTO_alias = DNS/name swap (**minutes**). RTO_rebuild = re-embed + reindex (**hours** at 10M+). RTO_cold_ns = warmup or DRN (p99 hole of **seconds–20 s** if you pretend idle namespaces are warm). A dim-mismatch 4xx is a **completed refuse**, not an RPO hole.

When **not** to use a dedicated vector DB: corpus **< ~200k tokens** → skip ANN (01). Exact search without index is fine at tens of thousands of rows. Chroma **embedded** ≠ Chroma **Cloud** (SPANN + wal3).

---

### 4. Distributed Resilience & Security

#### 4.1 Durable execution: ingest / re-embed as Temporal or Kafka

The query path is request/response. **Ingest and re-embed are workflows.** Do not run them as a for-loop in the API process.

| Step | Checkpoint | Idempotency |
| --- | --- | --- |
| Chunk batch offset | Kafka offset / Temporal activity heartbeat | `chunk_id` is the upsert key — replay safe |
| Embed-batch | Store embedding **IDs only** in workflow state; never persist raw `values` in Temporal payload if the workflow store is weaker than the index CMEK | Same `chunk_id` + `model_id` + dim |
| Dual-write A/B | Watermark: last `chunk_id` applied to B | Alias still points at A until nDCG gate |
| Alias flip | Single control-plane compare-and-swap | Rollback = flip back to A |
| Delete A | After rollback window | Irreversible; snapshot A first |

**Dead-letter:** chunks that exceed embed context (**8192** OpenAI, **32k** Qwen3/Jina v5, **128k** Cohere v4) or fail schema (dim mismatch) go to a DLQ — not a retry storm. Poison metric (wrong `metric` on upsert) may **not** 4xx; canaries catch it.

Pinecone: you do not set RF; durability = object store. Qdrant: `replication_factor`; fusion must be **top-level** across shards. Weaviate: commit logs + snapshots (`PERSISTENCE_HNSW_MAX_LOG_SIZE` default **500 MiB**); tombstone cleanup **300 s**. pgvector: streaming replicas + PITR; HNSW build needs `maintenance_work_mem` or `hnsw graph no longer fits`. Chroma Cloud: wal3 + setsum integrity; strong consistency claimed vs Pinecone’s eventual visibility.

**Reindex on model change:** dual-write → shadow nDCG → alias flip → keep A. Voyage-4 **shared space** is the exception **inside that family**. Cohere v3 (1024-d) → v4 (1536-d): **new index**. OpenAI `dimensions` truncate without re-embed is MRL **+ normalize** only.

#### 4.2 Failure taxonomy

| Class | Examples | Detection | Handling |
| --- | --- | --- | --- |
| **Transient** | Embed 429/5xx, Pinecone cold start seconds–**20 s**, RU 429 on Starter, checkpointer blip | Error rate; p99 window | Full-jitter retry on **idempotent** embed/query; **do not** retry upsert without `chunk_id`; warmup/DRN for cold |
| **Permanent** | Dim mismatch **4xx**; `$in` > **10,000**; metric/codec schema reject; DiskBBQ without Enterprise license | Non-retryable 4xx | Fail closed; new index or namespace/group ACL. Never pad/truncate silently |
| **Poison** | Model B queries vs index A (often **no** 4xx); metric mismatch; Qdrant fusion in prefetch; Weaviate PQ before 10k–100k/shard; Cohere `input_type` mixup; quantization without rescore | Golden-set nDCG cliff; score distribution vs expected \([-1,1]\) cosine | Pin schema on every vector; dual-write; do not “fix” by swapping embedder |
| **Poison ACL** | Metadata filter omitted; post-filter as authz; `$in` of 50k user IDs | Cross-tenant IDs in top-k | Namespace/RLS/tenant shard; predicate from verified auth |
| **Idempotency** | Two upserts on resume; alias flip twice | Duplicate WU; split-brain alias | `chunk_id` keys; single-writer alias CAS |
| **Denial of wallet** | Fat shared namespace RU; `include_values` egress; GP-style agent looping `query_index` | RU/query dash; invert-surface audit | Namespace split; deny `include_values` for assistants; rate-limit MCP |

#### 4.3 Circuit breaker closed → open → half-open

Independent breakers on **embed API** and **vector-DB query** (error rate **and** p99). A 429 on OpenAI must not stall a BM25-only degrade **and** must not open the DB breaker.

```
        embed 429/5xx | DB error-rate | DB p99 window | cold-start storm
  ┌──────────┐  ─────────────────────────────────────────────────►  ┌──────────┐
  │  CLOSED  │                                                       │   OPEN   │
  │  embed / │  success resets consecutive count                     │ FAIL FAST│
  │  query   │                                                       │ fallback │
  └────┬─────┘                                                       │ chain    │
       ▲                                                             └────┬─────┘
       │ probe OK                                                         │ cooldown
       │                                                            ┌─────▼──────┐
       └──────────── probe allow ───────────────────────────────────│ HALF-OPEN  │
                    probe fail → stay OPEN                          │ 1 synthetic│
                                                                    │ probe      │
                                                                    └────────────┘
```

**Thresholds [policy, not vendor SLO]:**

| Trip | Closed → open | Half-open | Fallback (**space-safe**) |
| --- | --- | --- | --- |
| Embed API 429/5xx | consecutive ≥ **5** or error-rate window | One tiny embed of a canary token | **Cached embedding → primary** (already tried) → **BM25-only** (degraded). Secondary dense embedder **only if same vector space** (Voyage-4 family: lite↔large). **Never** 3-small queries onto a 3-large index |
| Vector-DB query 5xx / p99 | error-rate + p99 > policy | One `query` k=1 on a warmup ID | Serve cache of recent ID lists **only if** ACL digest matches; else BM25-only; else 503 |
| Vector-DB upsert | consecutive ≥ **3** | One idempotent upsert | Pause ingest workflow; **do not** open query breaker |
| Cold start p99 | first-touch **>5 s** rate | Warmup probe | DRN / cron warmup; fail that ns, not the fleet |

**Fallback chain (required interview answer):** **query-embed cache → primary embedder → BM25-only (lexical degraded).** I would not fall back 3-small onto 3-large. I would not swap Cohere v4 queries onto an OpenAI index. Voyage-4 lite queries against voyage-4-large docs are the **documented** exception.

#### 4.4 Zero-Trust MCP (this module — not deferred)

Agents will wrap this layer as MCP tools (`query_index`, `upsert`, `embed`, `fetch_values`). There is **no module 19** yet. Zero-Trust lives **here**. MCP is **not** the PDP for namespace ACL — the **index predicate** (namespace / RLS / tenant shard / metadata filter composed from verified auth) is. The gateway is the PEP **in front of** the vector DB and embed APIs.

**Three trust boundaries:** (1) model ↔ host — model cannot verify tool descriptions (`query_index` vs `fetch_values`); (2) client ↔ MCP server — authN/Z + integrity of `tools/list`; (3) MCP server ↔ Pinecone/OpenAI — the server is a deputy. CVE-2025-6514 CVSS **9.6**: **connecting** to hostile `authorization_endpoint` metadata can be RCE before any tool call. CVE-2025-54136 (MCPoison) CVSS **8.8**: no re-validate of tool JSON.

**Zero-Trust minimum on embed/query/upsert tools:**

| Control | Spec | On this index layer |
| --- | --- | --- |
| **Transport** | OAuth 2.1 + PKCE `S256`. RFC **8707** `resource` = **canonical MCP server URI** on authorize *and* token. Servers accept only tokens whose audience is themselves. **MUST NOT** passthrough the client token to Pinecone/OpenAI; obtain a new token (typically RFC **8693** exchange) scoped to the upstream | Gateway holds Pinecone/OpenAI service credentials. A static `Authorization: Bearer` reused upstream is still passthrough |
| **Capability** | `initialize` + tool list; 2026-07-28 Streamable HTTP `Mcp-Method`/`Mcp-Name` so the gateway can authz per tool without parsing JSON-RPC | Allowlist: `query_index` yes for assistants; `upsert` ingest-workers only; `embed` rarely exposed (PII); `fetch_values` / `include_values` **no** for assistants |
| **Hash-pin** | `toolSurfaceHash` over canonical JSON of **name + description + inputSchema (+ outputSchema)**. Re-verify every `tools/call`. Mismatch → session pause | Pin `query_index` schema: no `namespace` from model JSON; no `include_values`; `k` capped |
| **Identity** | Verified access token. **Never** the LLM | `tenant_id` / matter-id / `user_id` from IdP → gateway → index predicate. Tool arg `tenant_id` is a **proposal** to discard |

**No token passthrough:** the MCP server must not forward the user’s IdP access token to `api.pinecone.io` or `api.openai.com`. Exchange or use a **service credential** with least privilege (query-only API key for the assistant path; write key only on the ingest worker). Hash-pin **server commands** (the argv/image digest of the MCP process) so a swapped binary cannot dump vectors.

**Tool-level RBAC (least privilege):**

| Tool | Who | Allowed | Forbidden |
| --- | --- | --- | --- |
| `query_index` | Assistant / user-delegated | `k` ≤ policy, filter **from auth**, IDs+scores | `include_values`, raw filter from model, cross-ns |
| `upsert` | Ingest worker | Batches with `chunk_id`, schema pin | Interactive agents; overwrite without watermark |
| `embed` | Ingest / query service | After PII pipeline | Logging `values`; arbitrary model_id from JSON |
| `fetch_values` / dump | Break-glass admin + HITL | CMEK path, audit row | Assistants; “debug traces” |
| `delete` | Ingest / GDPR worker | By `chunk_id` + tenant | Bulk delete from model args |

One tool, one verb. Credentials **never** in model-visible context. Argument PEP: `k` cap, filter allowlist, namespace from **verified** auth. HITL for: enabling `include_values`, new MCP server registration, namespace-wide delete.

**MCP is not the namespace PDP.** A correctly gated `query_index` that still searches a **shared** namespace with a model-supplied `tenant_id` is a leak. Isolation is namespace / shard / RLS. Post-filter ACL after ANN is an authz bug **and** an under-k bug.

Audit (WORM): `(cid, index, ns, filter_digest, k, include_values=false, tool, arg_digest, jti)` — **not** raw vectors, **not** raw PII chunks.

#### 4.5 PII pipeline — detect → redact → audit **before embed**

Embeddings are **not** a one-way hash. **Vec2Text** (Morris et al., EMNLP 2023): hypothesis → re-embed → corrector. Black-box encoder, **32-token** inputs: BLEU **97.3**, **92% exact** recovery. **MIMIC clinical notes: 89% of full names** recovered. Attack needs text–embedding pairs from the **same** model (API-accessible). CIKM 2024 follow-up: threat to dense retrieval systems.

Redacting in the **prompt** (module 01 generate path) does **not** un-embed stored PII. Quantization/MRL trim may reduce inversion fidelity **[inferred]** but is **not** a HIPAA control.

**Pipeline (explicit), on every chunk and every query string, before the embed API:**

1. **Detection (control plane, before bytes become a vector).** Dual-gate: **regex** (email, PAN, SSN, phones) + **ML NER** (Presidio/Bedrock/gateway) if available. Scan: ingest chunks, query text, tool args, metadata fields you were about to store, log payloads. If ML is down: **fail closed to mask** on query text; **fail closed (block)** on ingest of PAN/SSN into the index — do not embed raw PAN “and DLP later.”
2. **Redaction.** Stable tokens (`[EMAIL_<hash12>]`, `[PAN]`) so retrieval can still match a redacted corpus; `block` when policy says the field must not exist (secrets, unconsented health IDs). Store **IDs + ACL stamps** in the vector DB; join to the primary store for body text. Do not put raw chunk text in metadata if the vector DB’s access policy is weaker than the source.
3. **Audit trail (WORM).** Decisions, not values: `content_sha256` pre/post, entity **types** + counts, action (`redact` / `mask` / `block-from-embed`), detector, `correlation_id`, `tenant`, index, namespace. A successful embed without an audit row is a control-plane bug. GDPR erasure: delete vector + audit-digest legal-hold policy — deleting the prompt log is insufficient.

Treat the vector store as **equivalent sensitivity** to the source corpus. Encrypt at rest; CMEK; no world-readable buckets of `values`. Minimize `include_values` (egress + inversion). Pinecone CMEK: **one ARN/project**.

---

### 5. Production Enterprise Code

Self-contained stdlib. Optional `openai` / `pinecone` / `psycopg` imports. Same control flow without keys: retries + full jitter, circuit breaker **closed → open → half-open**, fallback **cache → primary embedder → BM25-only** (never mix embedding spaces), PII detect→redact→audit **before embed**, structured logs with correlation IDs, **never** log embedding `values` or raw PII. Namespace comes from **auth context**, not tool args. pgvector path pins `hnsw.iterative_scan`. Run: `python embeddings_index_runtime.py`.

```python
#!/usr/bin/env python3
"""Index runtime: embed client + ANN query, stdlib fallbacks.

Fallback chain: query-embed cache → primary embedder → BM25-only.
Never mix embedding spaces (no 3-small queries onto a 3-large index).
Run: python embeddings_index_runtime.py
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

# Optional (not required to run this file):
#   from openai import OpenAI
#   from pinecone import Pinecone
#   import psycopg

OPENAI_MAX_INPUTS = 2048
OPENAI_MAX_TOKENS = 300_000
SCHEMA_DIM = 1536
SCHEMA_METRIC = "cosine"
SCHEMA_MODEL = "text-embedding-3-small"


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for k, d in (
            ("correlation_id", "-"),
            ("tenant_id", "-"),
            ("index_ns", "-"),
            ("model_id", "-"),
        ):
            setattr(record, k, getattr(record, k, d))
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("vec_index")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"cid":"%(correlation_id)s","tenant":"%(tenant_id)s",'
            '"ns":"%(index_ns)s","model":"%(model_id)s",'
            '"msg":"%(message)s"}'
        )
    )
    handler.addFilter(CorrelationFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


LOG = configure_logging()


def slog(level: int, msg: str, **extra: Any) -> None:
    LOG.log(level, msg, extra=extra)


def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    base_s: float = 0.2,
    cap_s: float = 2.0,
    retryable: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError),
) -> Any:
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except retryable as exc:
            last = exc
            if i == attempts - 1:
                break
            sleep_s = min(cap_s, base_s * (2**i))
            sleep_s = random.random() * sleep_s
            slog(logging.WARNING, f"retry_backoff attempt={i + 1} sleep_s={sleep_s:.3f}")
            time.sleep(sleep_s)
    assert last is not None
    raise last


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    pass


class PermanentEmbedError(RuntimeError):
    pass


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    cooldown_s: float = 30.0
    half_open_probes: int = 1
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _probes_used: int = 0

    def allow(self) -> None:
        if self._state is CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
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


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PAN_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def pii_detect_redact_audit(
    text: str,
    *,
    audit: list[dict[str, Any]],
    correlation_id: str,
    tenant_id: str,
    sink: str,
    block_on_pan: bool = True,
) -> str:
    kinds: list[str] = []
    if EMAIL_RE.search(text):
        kinds.append("email")
    if PAN_RE.search(text):
        kinds.append("pan")
    pre = _sha(text)
    if "pan" in kinds and block_on_pan and sink in {"embed_ingest", "mcp_args"}:
        audit.append(
            {
                "cid": correlation_id,
                "tenant": tenant_id,
                "sink": sink,
                "kinds": kinds,
                "action": "block-from-embed",
                "pre": pre,
                "post": _sha(""),
                "detector": "regex",
            }
        )
        raise PermissionError(f"pii_block:{sink}:pan")
    redacted = EMAIL_RE.sub(
        lambda m: f"[EMAIL_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]",
        text,
    )
    redacted = PAN_RE.sub("[PAN]", redacted)
    action = "redact" if redacted != text else "allow"
    audit.append(
        {
            "cid": correlation_id,
            "tenant": tenant_id,
            "sink": sink,
            "kinds": kinds,
            "action": action,
            "pre": pre,
            "post": _sha(redacted),
            "detector": "regex",
        }
    )
    return redacted


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def split_embed_batch(texts: list[str]) -> list[list[str]]:
    batches: list[list[str]] = []
    cur: list[str] = []
    tok = 0
    for t in texts:
        n = estimate_tokens(t)
        if n > OPENAI_MAX_TOKENS:
            raise PermanentEmbedError("chunk_exceeds_300k_or_context")
        if cur and (len(cur) >= OPENAI_MAX_INPUTS or tok + n > OPENAI_MAX_TOKENS):
            batches.append(cur)
            cur, tok = [], 0
        cur.append(t)
        tok += n
    if cur:
        batches.append(cur)
    return batches


def cache_key(model_id: str, dim: int, metric: str, text: str) -> str:
    norm = " ".join(text.lower().split())
    h = hashlib.sha256(norm.encode()).hexdigest()
    return f"{model_id}|{dim}|{metric}|{h}"


def l2_normalize(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


class ScriptedEmbedder:
    """Deterministic stand-in. Production: OpenAI embeddings.create."""

    def __init__(self, name: str, dim: int = SCHEMA_DIM, fail_kind: str | None = None) -> None:
        self.name = name
        self.dim = dim
        self.fail_kind = fail_kind

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self.fail_kind == "transient":
            raise TimeoutError("embed_timeout")
        if self.fail_kind == "permanent":
            raise PermanentEmbedError("dim_mismatch")
        out: list[list[float]] = []
        for t in texts:
            seed = int(hashlib.sha256(t.encode()).hexdigest()[:8], 16)
            rng = random.Random(seed)
            raw = [rng.uniform(-1.0, 1.0) for _ in range(self.dim)]
            out.append(l2_normalize(raw))
        return out


@dataclass
class AuthContext:
    tenant_id: str
    principal: str
    roles: frozenset[str]


def namespace_from_auth(auth: AuthContext) -> str:
    return f"tenant-{auth.tenant_id}"


class BM25Index:
    def __init__(self) -> None:
        self.df: dict[str, int] = defaultdict(int)
        self.postings: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.doclen: dict[str, int] = {}
        self.n_docs = 0

    def upsert(self, doc_id: str, text: str) -> None:
        toks = text.lower().split()
        self.doclen[doc_id] = max(1, len(toks))
        seen: set[str] = set()
        for tok in toks:
            self.postings[tok][doc_id] += 1
            if tok not in seen:
                self.df[tok] += 1
                seen.add(tok)
        self.n_docs = len(self.doclen)

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        toks = query.lower().split()
        scores: dict[str, float] = defaultdict(float)
        avgdl = (sum(self.doclen.values()) / self.n_docs) if self.n_docs else 1.0
        for tok in toks:
            df = self.df.get(tok, 0)
            if df == 0:
                continue
            idf = math.log((self.n_docs - df + 0.5) / (df + 0.5) + 1.0)
            for doc_id, tf in self.postings[tok].items():
                dl = self.doclen[doc_id]
                denom = tf + 1.2 * (1 - 0.75 + 0.75 * dl / avgdl)
                scores[doc_id] += idf * (tf * 2.2) / denom
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:k]


@dataclass
class QueryResult:
    ids: list[str]
    scores: list[float]
    source: str
    degraded: bool
    include_values: bool = False


class IndexRuntime:
    def __init__(self, embedder: ScriptedEmbedder) -> None:
        self.embedder = embedder
        self.embed_breaker = CircuitBreaker("embed_api")
        self.db_breaker = CircuitBreaker("vector_db")
        self.cache: dict[str, list[float]] = {}
        self.vectors: dict[str, dict[str, list[float]]] = defaultdict(dict)
        self.meta: dict[str, dict[str, str]] = defaultdict(dict)
        self.bm25: dict[str, BM25Index] = defaultdict(BM25Index)
        self.audit: list[dict[str, Any]] = []

    def upsert(
        self,
        *,
        auth: AuthContext,
        chunk_id: str,
        text: str,
        correlation_id: str,
    ) -> None:
        if "ingest" not in auth.roles:
            raise PermissionError("upsert_denied")
        ns = namespace_from_auth(auth)
        redacted = pii_detect_redact_audit(
            text,
            audit=self.audit,
            correlation_id=correlation_id,
            tenant_id=auth.tenant_id,
            sink="embed_ingest",
        )
        vecs = self._embed([redacted], correlation_id=correlation_id, tenant_id=auth.tenant_id, ns=ns)
        self.vectors[ns][chunk_id] = vecs[0]
        self.meta[ns][chunk_id] = redacted
        self.bm25[ns].upsert(chunk_id, redacted)
        slog(
            logging.INFO,
            f"upsert_ok chunk_id={chunk_id} bytes={len(redacted.encode())}",
            correlation_id=correlation_id,
            tenant_id=auth.tenant_id,
            index_ns=ns,
            model_id=SCHEMA_MODEL,
        )

    def query(
        self,
        *,
        auth: AuthContext,
        query_text: str,
        k: int,
        correlation_id: str,
        include_values: bool = False,
    ) -> QueryResult:
        if include_values and "fetch_values" not in auth.roles:
            raise PermissionError("include_values_denied")
        ns = namespace_from_auth(auth)
        k = min(max(1, k), 50)
        redacted = pii_detect_redact_audit(
            query_text,
            audit=self.audit,
            correlation_id=correlation_id,
            tenant_id=auth.tenant_id,
            sink="embed_query",
            block_on_pan=False,
        )
        filter_digest = _sha(ns)
        try:
            vec = self._embed_one_cached(redacted, correlation_id=correlation_id, tenant_id=auth.tenant_id, ns=ns)
            hits = self._ann(ns, vec, k, correlation_id=correlation_id)
            source = "ann"
            degraded = False
        except (CircuitOpenError, TimeoutError, ConnectionError) as exc:
            slog(logging.WARNING, f"fallback_bm25 reason={type(exc).__name__}", correlation_id=correlation_id, tenant_id=auth.tenant_id, index_ns=ns, model_id=SCHEMA_MODEL)
            hits = self.bm25[ns].search(redacted, k)
            source = "bm25"
            degraded = True
        ids = [h[0] for h in hits]
        scores = [float(h[1]) for h in hits]
        self.audit.append(
            {
                "cid": correlation_id,
                "index": "docs",
                "ns": ns,
                "filter_digest": filter_digest,
                "k": k,
                "include_values": False,
                "source": source,
            }
        )
        slog(
            logging.INFO,
            f"query_ok k={k} n={len(ids)} source={source} degraded={degraded}",
            correlation_id=correlation_id,
            tenant_id=auth.tenant_id,
            index_ns=ns,
            model_id=SCHEMA_MODEL,
        )
        return QueryResult(ids=ids, scores=scores, source=source, degraded=degraded)

    def _embed_one_cached(self, text: str, *, correlation_id: str, tenant_id: str, ns: str) -> list[float]:
        key = cache_key(SCHEMA_MODEL, SCHEMA_DIM, SCHEMA_METRIC, text)
        hit = self.cache.get(key)
        if hit is not None:
            slog(logging.INFO, "embed_cache_hit", correlation_id=correlation_id, tenant_id=tenant_id, index_ns=ns, model_id=SCHEMA_MODEL)
            return hit
        vec = self._embed([text], correlation_id=correlation_id, tenant_id=tenant_id, ns=ns)[0]
        self.cache[key] = vec
        return vec

    def _embed(self, texts: list[str], *, correlation_id: str, tenant_id: str, ns: str) -> list[list[float]]:
        out: list[list[float]] = []
        for batch in split_embed_batch(texts):
            def _call(b: list[str] = batch) -> list[list[float]]:
                self.embed_breaker.allow()
                try:
                    vecs = self.embedder.embed_batch(b)
                except PermanentEmbedError:
                    self.embed_breaker.record_failure()
                    raise
                except (TimeoutError, ConnectionError):
                    self.embed_breaker.record_failure()
                    raise
                if any(len(v) != SCHEMA_DIM for v in vecs):
                    raise PermanentEmbedError("dim_mismatch")
                self.embed_breaker.record_success()
                return vecs

            part = retry_call(_call)
            out.extend(part)
        slog(logging.INFO, f"embed_ok n={len(out)}", correlation_id=correlation_id, tenant_id=tenant_id, index_ns=ns, model_id=SCHEMA_MODEL)
        return out

    def _ann(self, ns: str, query: list[float], k: int, *, correlation_id: str) -> list[tuple[str, float]]:
        self.db_breaker.allow()
        try:
            scored: list[tuple[str, float]] = []
            for cid, vec in self.vectors[ns].items():
                scored.append((cid, float(sum(a * b for a, b in zip(query, vec)))))
            scored.sort(key=lambda x: x[1], reverse=True)
            self.db_breaker.record_success()
            return scored[:k]
        except Exception:
            self.db_breaker.record_failure()
            raise

    def pgvector_sql(self, k: int) -> str:
        return (
            "BEGIN; SET LOCAL hnsw.ef_search = 40; "
            "SET LOCAL hnsw.iterative_scan = relaxed_order; "
            "SELECT id, 1 - (embedding <=> %(q)s) AS score "
            "FROM chunks WHERE tenant_id = %(tenant)s "
            f"ORDER BY embedding <=> %(q)s LIMIT {int(k)}; COMMIT;"
        )


def build_runtime() -> IndexRuntime:
    return IndexRuntime(ScriptedEmbedder(name="primary"))


if __name__ == "__main__":
    rt = build_runtime()
    ingest = AuthContext(tenant_id="acme", principal="worker", roles=frozenset({"ingest"}))
    user = AuthContext(tenant_id="acme", principal="ada", roles=frozenset({"search"}))
    rt.upsert(auth=ingest, chunk_id="c1", text="Reset MFA via the security desk. Contact ada@example.com", correlation_id="cid-1")
    r1 = rt.query(auth=user, query_text="how do I reset MFA?", k=5, correlation_id="cid-2")
    print(r1)
    assert r1.source == "ann" and r1.include_values is False
    assert any(row.get("action") in {"redact", "allow", "block-from-embed"} for row in rt.audit)
    try:
        rt.query(auth=user, query_text="leak", k=5, correlation_id="cid-3", include_values=True)
        raise SystemExit("include_values should deny")
    except PermissionError:
        pass
    rt.embedder.fail_kind = "transient"
    rt.embed_breaker = CircuitBreaker("embed_api", failure_threshold=1, cooldown_s=60)
    r2 = rt.query(auth=user, query_text="reset MFA", k=5, correlation_id="cid-4")
    print(r2)
    assert r2.degraded is True and r2.source == "bm25"
    print("ok", len(rt.audit), "audit rows")
    print(rt.pgvector_sql(10))
```

**Wiring notes (not in the script):** production OpenAI client sets `dimensions` only for MRL cutoffs then **re-normalizes** if you truncated locally. Pinecone `query(namespace=namespace_from_auth(auth), include_values=False, filter=...)` — namespace **not** from MCP args. Hybrid dense+sparse: metric `dotproduct` + query-side `hybrid_score_norm` (policy in 01). Cohere: pin `input_type`. Voyage-4: docs large / queries lite is the only cross-size fallback inside one space. Temporal activities wrap `upsert` with `chunk_id` idempotency. Gateway hash-pins the MCP tool JSON; denies `fetch_values` for assistant tokens.

---

### 6. Architectural System Design Scenarios

#### Scenario 1 — Multi-tenant SaaS, 20M × 1536-d, 50 QPS, p95 < 100 ms, 5 tenants

**Problem.** B2B product search over 20M chunks (1536-d cosine, ~200 B metadata). Five tenants, roughly equal corpus share. 50 QPS sustained, product wants p95 **< 100 ms** on the **index path** (embed + 1 ANN, no rerank). Security wants no cross-tenant leak. Finance just saw a shared-namespace RU quote and panicked. Starter/Builder have **no SLA**; Standard is acceptable if cost is bounded; Enterprise **99.95%** is optional.

**Proposed architecture (recommended B, with C if you already run the cluster):**

```
  ┌─────────┐   ┌─────────────────────────────────────────────────────────┐
  │ IdP/PEP │──▶│ CONTROL: create_index pin 3-small, 1536, cosine, auto   │
  │ JWT →   │   │   namespace = tenant-{tid} from verified token          │
  │ tenant  │   │   alias docs-vN   MCP query_index allowlist             │
  │         │   │   include_values denied   PII before embed              │
  └─────────┘   └──────────────────────────┬──────────────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────────────┐
                    │ DATA: query-embed cache (40% FAQ)                    │
                    │   embed 3-small → ANN in tenant ns (≈4 GB / tenant)  │
                    │   bitmap ACL redundant with ns isolation             │
                    │   DRN if p99 cold; warmup cron otherwise             │
                    │   ingest: Temporal, chunk_id upsert, watermark       │
                    └──────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

| Axis | **A Shared ns + metadata `tenant_id` filter** | **B Namespace-per-tenant Pinecone Standard (recommended if little SRE)** | **C Self-host Qdrant SQ + payload tenant (recommended if you run K8s)** |
| --- | --- | --- | --- |
| **Cost** | One ~127 GB ns: **~$263k/mo** reads at 50 QPS **[inferred]** + **$41.9/mo** storage. Filter does **not** cut RU | Five ~4 GB ns: **~$8.3k/mo** reads **[inferred]** + same storage sticker. 3-small + 1 GB reference **$0.026 / 1k**; this shape ≈ 4 RU → **~$0.064 ANN / 1k** + **$0.010** embed | No RU. SQ int8 ~32 GB vectors + HNSW; 64 GB RAM node class + HA. Ingest embed still **$40** Batch for 10M×400 tok 3-small |
| **Latency** | Fat scatter-gather; p95 **<100 ms** is a wish unless DRN. Shared cold-start hits everyone | Small ns; closer to DRN 480M p50 **80** / p99 **170** class only if you **buy** DRN — otherwise **[inferred] p50 40–80 / p95 120 / p99 250** with warmup. Split ns is the p95 lever | RAM-resident SQ+rescore can beat serverless p95 if sized; you own p99 |
| **Ops** | Trivial routing; lethal invoice | 5 ns routing from JWT; 100k ns ceiling is not the issue | Cluster, snapshots, payload indexes **before** ingest, version pins |
| **Security** | App-enforced filter; omit filter → **cross-tenant leak**. `$in` ≤10k | Physical ns files; query cannot cross. MCP still must not take ns from tool args | `is_tenant=true` + shard key; mis-set shard key = leak. RLS equivalent is payload filter **plus** dedicated shard |
| **Scalability** | RU linear in **full** ns GB. 20M → 100M makes this worse | Add ns per tenant; 4 GB→40 GB/tenant is a **known** RU multiply | Vertical then shard; no 100k-ns product limit |

**Decision.** **B wins** for this problem statement if the team will not staff a vector cluster: the RU cliff is the design, namespace-per-tenant is the cost **and** isolation control, MCP `query_index` binds ns from JWT. **C wins** if you already operate Qdrant/K8s and want filterable HNSW + SQ knobs without RU; size RAM from SQ+graph and put payload tenant indexes on first. **A never wins** at 20M×1536-d and 50 QPS — **$263k/mo** vs **$8.3k/mo** is not a rounding error **[inferred]**. p95 < 100 ms: do not cite Pinecone “sub-100 ms” as the SLO; put DRN or Qdrant-RAM on the table if the **[inferred] 120 ms p95** policy is too loose. I would not put all five tenants in one ns “and filter.”

#### Scenario 2 — Legal multilingual (statutes + contracts, 20+ languages, ACL by matter), 8M chunks

**Problem.** A firm wants retrieve-over-statutes-and-contracts in 20+ languages, ACL **by matter** (not by listing 200k user IDs). 8M chunks. Exact citations (`§ 2.3(a)`, docket numbers) must hit. Missed hold is a malpractice event. Vectors must be treated as the **contracts** (Vec2Text). English-only MTEB v1 is a procurement non-starter. Fusion/rerank policy is 01; this scenario picks **embedder + index product + tenancy + first-pass dim**.

**Proposed architecture (recommended B):**

```
  ┌─────────┐   ┌─────────────────────────────────────────────────────────┐
  │ Matter  │──▶│ CONTROL: pin Qwen3-4B/8B or Cohere v4 + input_type      │
  │ IdP ACL │   │   MRL store 1024 (or 1536); first-pass 256 + rescore    │
  │         │   │   ns = matter-{id}   hybrid dense+BM25 in Qdrant/ES     │
  │         │   │   CMEK   include_values=false   PII before embed        │
  └─────────┘   └──────────────────────────┬──────────────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────────────┐
                    │ DATA: query embed (search_query / instruction)       │
                    │   256-d ANN → oversample → 1024/1536 rescore         │
                    │   BM25/SPLADE lane for § cites (fusion policy = 01)  │
                    │   golden nDCG on held-out matters, not MMTEB mean    │
                    └──────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

| Axis | **A OpenAI 3-large + pgvector cosine** | **B Qwen3-4B/8B or Cohere v4 + hybrid dense+BM25 in Qdrant/ES, matter namespaces (recommended)** | **C Voyage-law-2 only, dense-only** |
| --- | --- | --- | --- |
| **Cost** | 3-large **$0.13/1M**; 8M×400 tok ingest sync **$416** / Batch **$208 [inferred]**. Query 500 tok **$0.065 / 1k**. Instance $ for pgvector, no RU | Qwen3 = GPU-hour. Cohere v4 **$/1M unconfirmed** (Model Vault **$4/hr** if dedicated). Hybrid is index RAM + BM25. MRL 256 first-pass cuts RAM: 8M×256-d ≈ **8.2 GB** hot vs 1024-d **32.8 GB** | **$0.12/1M**, 50M free. Ingest 8M×400 tok ≈ **$384** sync **[inferred]**. Dense-only misses lexical cites |
| **Latency** | pgvector HNSW + `iterative_scan` for matter `WHERE`; p95 is yours. English-biased embedder may force rerank (01) to salvage | 256-d walk then rescore top-100–200; ES DiskBBQ if RAM bound. Matter ns keeps graphs small | Fast dense path; citation misses become extra hops in 01 |
| **Ops** | One RDS; `ef=40` × 10% ACL ≈ **4 rows** without `iterative_scan` | Two named vectors or `subvector`; payload/BM25 indexes; Qwen vs Cohere bake-off | Single SKU; still need a lexical lane later |
| **Security** | RLS if `FORCE ROW LEVEL SECURITY`; Vec2Text: treat as contracts; no `include_values` | Matter ns / ES index-per-matter; CMEK; MCP deny dump. Cohere 128k helps long statutes **after** PII | Same inversion story; domain SKU ≠ encryption |
| **Scalability / quality** | 3-large MMTEB multilingual **58.93** (Qwen table) — English-biased for 20+ langs. Cosine pin OK if L2-normalized | Qwen3-8B MMTEB **70.58** (2025-06-05) / 4B **69.45**; Cohere v4 128k + `input_type`. Hybrid catches `§` / docket. Eval on **held-out matters** | Vendor legal SKU; **dense-only** fails exact citations. Compare voyage-4-large on the **same** legal golden set before locking law-2 |

**Decision.** **B wins.** I pin a multilingual MRL model (Qwen3-4B/8B self-host or Cohere v4 if we accept the **$/1M gap** and 2,000 inputs/min), **hybrid dense+BM25/SPLADE** in Qdrant or Elasticsearch with **matter namespaces**, first-pass **MRL 256** + rescore full dim. I would not ship A as the multilingual system of record. I would not ship C dense-only. Fusion α/RRF and ACL-aware retrieve stay in **01**; here I refuse a 200k-value `$in` of user IDs. Vec2Text **92% / 89%** means the index **is** the contract store: CMEK, PII before embed, `include_values=false`, WORM of `(cid, ns, filter_digest, k)` not vectors.

---

## Common Failure Modes

| Failure | Cause | Detection | Mitigation |
| --- | --- | --- | --- |
| **Embedding drift** | Model B queries vs index A | nDCG cliff after “upgrade”; often no 4xx | Pin `model_id` on every vector; dual-write; alias flip |
| **Metric mismatch** | L2 index on cosine models; IP model with cosine | Neighbors “related but wrong”; scores outside \([-1,1]\) | Recreate with training metric; normalize+IP if equivalent |
| **MTEB board mix** | 64.6 v1 vs 70.58 MMTEB vs 75.22 Eng v2 | Wrong bake-off winner | In-domain nDCG; never rank across boards |
| **IVF + filter collapse** | Fixed nprobe at 50–90% filtered | Recall vs selectivity canary; ICML-class drop | Bypass + adaptive scan; namespaces; Filtered-DiskANN |
| **HNSW post-filter under-k** | `ef=40` × 10% ≈ **4** rows | `LIMIT 10` returns 2 | `iterative_scan`; ES/OS inner filter; raise ef |
| **Pinecone hybrid without `hybrid_score_norm`** | Sparse unbounded vs cosine \([-1,1]\) in one **dotproduct** index | Sparse scores ≫ 1 | Query-time α scale — **policy in 01**; index constraint: **dotproduct** |
| **Quantization without rescore** | BQ/PQ ANN only | Recall −5 to −20 pts vs float | Oversample 3–8×; skip BQ on <1024-d; Weaviate PQ only after 10k–100k/shard |
| **Pinecone “tune HNSW”** | Assuming pods/HNSW | No `M` API | Ananas / PQFS / IVF per slab; BBQ-HNSW is **ES** |
| **Fat namespace RU** | Shared ns + metadata ACL | Invoice cliff; 429 | Namespace-per-tenant; Plan 1 **$263k vs $8.3k [inferred]** |
| **`$in` > 10,000** | ACL by user-id list | Pinecone 400 | Groups / namespaces |
| **Cold slab** | Idle ns first touch | p99 seconds–**20 s** | DRN; warmup; do not treat design target as SLO |
| **OpenAI 2048 / 300k** | Unsplit batch | 400 `max_tokens_per_request` | Splitter; Batch ingest |
| **Cohere `input_type` mixup** | Query as `search_document` | Asymmetric eval drop | Pin in client |
| **Qdrant fusion in prefetch** | Multi-shard hybrid | Shard-level nDCG | Top-level `FusionQuery` |
| **Vec2Text / `include_values`** | Dump of `values`; PII embedded | Access logs; reconstructed names | PII **before** embed; deny dump; CMEK; quantization ≠ HIPAA |
| **3-small fallback onto 3-large index** | “Any embedder in the circuit” | Silent recall death | Same vector space only (Voyage-4 family) else BM25-only |
| **MCP ns from tool args** | Model-chosen tenant | Cross-tenant IDs | Gateway PEP; predicate from JWT; MCP is not the PDP |
| **RTEB private column** | Voyage access to private eval | Procurement on a tainted board | `mteb#3934`; in-domain holdout |

No public post-mortem corpus beyond vendor papers (ICML IVF-filter, Vec2Text). Do not invent incidents.

---

## Key Takeaways

- This layer is the **index/embedding substrate**, not RAG. Retrieve-then-generate, RRF/rerank, GraphRAG, ACL-aware retrieve: **01**. Embedding FT: **02**. Pin **`model_id + dim + metric + index type + codec`**.
- Pinecone has **never used HNSW** (Ananas / PQFS / IVF). BBQ-HNSW/DiskBBQ is **Elasticsearch**. Cosine ≡ IP ≡ −L2² **iff** L2-normalized. Sparse/hybrid Pinecone = **dotproduct only**; `hybrid_score_norm` is an index-product **trap**, fusion policy is 01.
- Do not compare MTEB v1 **62.3 / 64.6** to MMTEB **70.58** (Qwen3-8B, 2025-06-05) to Eng v2 **75.22**. RTEB private column was **pulled** (Voyage access). In-domain nDCG wins. Voyage-4 is a **shared space** (docs large / queries lite); OpenAI 3-small vs 3-large is not.
- Quantization without **oversample + rescore** steals recall (Qdrant BQ+rescore **0.98 recall@100** ada-002 at **4×**). pgvector `ef=40` × 10% ≈ **4** rows → `iterative_scan`. IVF+filter dies at 50–90% selectivity; ICML recall@10 **0.989** with bypass/adaptive scan (**paper**).
- **$ / 1k [inferred]:** 3-small embed-only **$0.010**; 3-small + 1 GB Pinecone Std **$0.026**; 100 GB shared ns ANN-only **$1.60**. 10M×400 tok 3-small Batch **$40**. Plan 1: **~$263k/mo** vs **~$8.3k/mo**. Cache key `(model_id, dim, metric, normalized_query_hash)`; 40% hit → embed **$0.006 / 1k**.
- Latency: DRN customer **80/170 ms** (480M @ 380 QPS) and **26/60 ms** (1.4B @ 5.7k QPS) are **evidence**. Policy **[inferred] p50 40–80 / p95 120 / p99 250 ms**. Pinecone “sub-100 ms” is a **design target**. DiskANN **<3 ms mean** is a **paper**. Write ack **<100 ms**; visibility **seconds**; cold start to **~20 s**.
- Fallback: **cache → primary embedder → BM25-only**. Never mix spaces. Ingest is Temporal/Kafka with `chunk_id` keys. Zero-Trust MCP is **in this file**: OAuth 2.1, RFC 8707, **no** passthrough to Pinecone/OpenAI, hash-pin, `query` yes / `include_values` no. MCP is not the namespace PDP. PII: **detect → redact → audit before embed**. Vec2Text **92% exact / 89% MIMIC names**. Quantization is not HIPAA.

---

## Interview Q&A

**Q1. What is this layer, in one minute?**  
I pin the retrieval **index**, not the RAG product. Control plane creates schema: `model_id`, dimension, metric, index type, quantization codec, tenancy, alias. Data plane embeds the query (or hits a cache), walks ANN with a payload filter, rescores, returns IDs. Persistence is WAL/slabs/HNSW files. MCP `query_index`/`upsert` sit behind a gateway — not an omnibus dump. Generation, RRF, GraphRAG, and ACL-aware retrieve policy are 01. Embedding fine-tune is 02.

**Q2. Walk a query through your diagram.**  
Gateway binds tenant from the JWT. PII redact the query. Cache key `(model_id, dim, metric, normalized_query_hash)` — hit skips embed $ and embed latency. Miss: embed with the pinned model (Cohere `search_query`; Voyage-4-lite only against voyage-4-large docs). ANN + filter: Pinecone bitmap + Ananas/PQFS/IVF with bypass/adaptive scan; HNSW products iterative/in-walk filter. Oversample and rescore. Return IDs, `include_values=false`. I would not let the model pick the namespace.

**Q3. Pinecone HNSW vs BBQ-HNSW — what do you pin?**  
I would not pin HNSW on Pinecone. They have **never used HNSW**; slabs pick Ananas, PQFS, or IVF. BBQ-HNSW and DiskBBQ are **Elasticsearch**. If I need `M`/`ef`, I am on Qdrant, Weaviate, or pgvector — or I am failing the interview.

**Q4. Give me `$ per 1k` for the reference query.**  
Inferred, 500-token embed + 1 ANN, no rerank, no `include_values`. Embed-only `3-small` **$0.010 / 1k** (`$0.02/1M`). `3-large` **$0.065**. Voyage-4-lite **$0.010**, voyage-4 **$0.030**, voyage-4-large **$0.060**. Pinecone Standard 1 GB ns ANN **$0.016**; 3-small + that ns **$0.026**. 100 GB shared ns ANN-only **$1.60**. 40% query-embed cache: 3-small embed **$0.006 / 1k**, + 1 GB ANN **$0.022 / 1k**. I do not quote Cohere $0.12/1M as a SKU — static HTML did not confirm it.

**Q5. The 20M × 1536-d, 50 QPS, 5-tenant RU question.**  
I copy Plan 1. ~127 GB in one namespace: **~$263k/mo** Standard reads **[inferred]**. Five ~4 GB tenant namespaces: **~$8.3k/mo**. I recommend namespace-per-tenant Pinecone **or** Qdrant SQ with payload tenant if we run the cluster. I would not share one ns and metadata-filter. Storage is ~**$42/mo** — not the story. p95 < 100 ms is **not** Pinecone’s design-target sentence; I put DRN or RAM-resident Qdrant on the table and I publish **[inferred] p50 40–80 / p95 120 / p99 250 ms** until we measure.

**Q6. What p50/p95/p99 do you put on vector search?**  
Nobody publishes a global SLO. I use DRN customer benches as evidence: 480M p50 **80 ms** p99 **170 ms** at 380 QPS; 1.4B p50 **26 ms** p99 **60 ms** at 5.7k QPS. DiskANN SIFT1B **<3 ms mean** is a paper. Then I contract **[inferred] policy** p50 **40–80** / p95 **120** / p99 **250 ms** for a typical serverless embed+ANN path, with embed cache, ns split, warmup, quantization+rescore budget. Write ack **<100 ms** is not visibility; visibility is **seconds**; cold start can be **~20 s**.

**Q7. Metrics and mixed boards.**  
Cosine ≡ IP ≡ negated L2-squared **iff** L2-normalized. I pin the training metric. Pinecone sparse/hybrid is **dotproduct only**. pgvector `<->` `<#>` `<=>`. I would not compare OpenAI **62.3 / 64.6** (MTEB v1) to Qwen3-8B **70.58** MMTEB (2025-06-05) or **75.22** Eng v2. RTEB’s private column was pulled because Voyage had access. I bake in-domain nDCG.

**Q8. IVF filters, quantization, pgvector under-k.**  
Fixed-nprobe IVF dies at 50–90% filter selectivity; Pinecone’s ICML path is bypass + adaptive scan, recall@10 **0.989** in their YFCC figure — paper internals. Quantization without oversample+rescore steals recall; Qdrant BQ+rescore **0.98 recall@100** ada-002 at 4×; Weaviate PQ only after 10k–100k/shard. pgvector `ef=40` with 10% `WHERE` ≈ 4 rows — I set `iterative_scan`. I would not use post-filter as ACL.

**Q9. Circuit breaker and fallback.**  
Independent breakers on embed and vector-DB query, closed → open → half-open on error rate and p99. Fallback is **cached embedding → primary embedder → BM25-only**. Secondary dense only inside a **shared space** (Voyage-4 family). I would not fall back 3-small onto a 3-large index. Poison dim mismatch is 4xx — no retry.

**Q10. Zero-Trust MCP on the index — module 19 does not exist.**  
I put the PEP **here**. MCP tools wrap `query_index` / `upsert` / `embed` / `fetch_values`. Gateway: OAuth 2.1, RFC 8707 resource = this MCP server, **no** token passthrough to Pinecone or OpenAI, hash-pin tool JSON and server commands, audience-bound tokens. Allowlist: `query` yes for assistants, `include_values`/dump no. Tenant from verified auth, not tool args. MCP is **not** the namespace PDP — the index predicate is. CVE-2025-54136 if I skip re-hash; CVE-2025-6514 if I `open()` hostile auth metadata.

**Q11. PII — detect → redact → audit.**  
Before embed, not in the generate prompt. Vec2Text recovered **92% exact** on 32 tokens and **89% of MIMIC names**. I regex+NER, redact to stable tokens or block PAN on ingest, WORM of hashes and types, never log `values`. Redacting later does not un-embed. Quantization is not a HIPAA control. HIPAA add-on **$190/mo** Std vs Ent included; CMEK one ARN/project.

**Q12. Legal multilingual design in 90 seconds.**  
I would not ship OpenAI 3-large + English-biased pgvector cosine as the system of record, and I would not ship Voyage-law-2 dense-only. I pin Qwen3-4B/8B or Cohere v4, hybrid dense+BM25 in Qdrant or ES, **matter namespaces**, MRL **256** first-pass + rescore, in-domain holdout — not MMTEB mean. Vectors **are** the contracts.

---

## Key Numbers to Memorize

### Schema / models / boards
| Number | What |
| --- | --- |
| **`model_id + dim + metric + index type + codec`** | Schema invariant; change ⇒ re-embed/rebuild |
| **62.3 / 64.6 / 61.0** | OpenAI 3-small / 3-large / ada-002 MTEB **v1** mean |
| **70.58 / 75.22 / 69.44** | Qwen3-8B MMTEB mean / Eng v2 mean / Eng v2 retrieval (2025-06-05/06) |
| **8192 / 2048 / 300k** | OpenAI tokens/input; inputs/request; tokens/request |
| **128k / 256–1536** | Cohere embed-v4 context / MRL dims; pin `input_type` |
| **$0.12 / $0.06 / $0.02** | Voyage-4-large / voyage-4 / voyage-4-lite **per 1M** |
| **dotproduct only** | Pinecone sparse and hybrid-in-one-index metric |
| **never HNSW** | Pinecone Ananas / PQFS / IVF; BBQ-HNSW = Elasticsearch |

### Algorithms / recall
| Number | What |
| --- | --- |
| **M=16 / efC=64 / ef=40** | pgvector HNSW defaults |
| **ef=40 × 10% ≈ 4** | pgvector post-filter under-k → `iterative_scan` |
| **50–90%** | IVF+filter collapse band; nprobe 4→7 / 4→**45** (ICML) |
| **0.989 / 0.986** | ICML recall@10 YFCC / recall@100 customer (**paper**) |
| **0.98 @4×** | Qdrant BQ+rescore recall@100 ada-002 DBpedia |
| **10k–100k/shard** | Weaviate PQ enable threshold |
| **32× + 14 B / 3×** | Elastic BBQ compression / default oversample |
| **<3 ms mean / >5k QPS** | DiskANN SIFT1B **paper** (1B × 128-d) |
| **`$in` ≤ 10,000** | Pinecone filter limit |

### $ / RU **[inferred]** where marked
| Number | What |
| --- | --- |
| **$0.02 / $0.13 per 1M** | OpenAI 3-small / 3-large |
| **$16–18 / M RU, $0.33/GB/mo** | Pinecone Standard; 1 RU per 1 GB targeted ns, min 0.25 |
| **[inferred] $0.010 / 1k** | 500-tok 3-small embed-only |
| **[inferred] $0.026 / 1k** | 3-small + 1 GB Pinecone Std ANN |
| **[inferred] $1.60 / 1k** | 100 GB shared ns ANN-only |
| **[inferred] $0.006 / 1k** | 3-small embed with **40%** cache hit |
| **$40** | 10M × 400 tok 3-small **Batch** ingest |
| **[inferred] ~$263k/mo vs ~$8.3k/mo** | Plan 1 one fat ns vs tenant ns, 50 QPS |
| **$190/mo** | Pinecone HIPAA add-on Standard; Ent included |
| **> ⚠️ Gap** | Cohere self-serve $/1M **not confirmed** — do not invent $0.12 |

### Latency / availability / security
| Number | What |
| --- | --- |
| **80 / 170 ms @ 380 QPS** | DRN customer 480M p50/p99 (**not** an SLO) |
| **26 / 60 ms @ 5.7k QPS** | DRN customer 1.4B p50/p99 |
| **40–80 / 120 / 250 ms** | **[inferred] policy** p50 / p95 / p99 typical serverless query path |
| **<100 ms / seconds / ~20 s** | Pinecone write ack / visibility / cold start |
| **sub-100 ms** | Pinecone **design target**, not SLO |
| **99.95% / 99.5 / 99.9 / 99.95** | Pinecone Ent; Weaviate Flex / Plus / Premium |
| **no SLA** | Pinecone Starter/Builder |
| **92% exact / 89% MIMIC names** | Vec2Text inversion (32-tok / clinical names) |
| **2,000 inputs/min** | Cohere Embed rate limit |
| **RFC 8707 / RFC 8693** | MCP resource indicator / **no** token passthrough |
| **8.8 / 9.6** | CVE-2025-54136 MCPoison / CVE-2025-6514 connect-time RCE |
| **detect → redact → audit** | **Before embed**; quantization ≠ HIPAA |

**Dates:** research frozen **2026-09-03** (74 sources). Do not treat inferred `$` or ms as list prices or vendor SLOs. Do not treat aggregator Cohere $/1M as a SKU.
