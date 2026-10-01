# Research: How Vector Databases Work

**Date researched**: 2026-09-30
**Sources consulted**: 22
**Scope note**: Index/store layer only — ANN structures (HNSW, IVF, PQ), storage/quantization, metadata filtering, hybrid search at the index, ops trade-offs. **RAG application patterns** (chunking, contextual retrieval, agentic RAG, prompt assembly) were covered in topic 6; not re-derived here.

## 1. System Topology & Mechanics

### Why traditional indexes fail on embeddings

Relational indexes (`B-tree`, hash, GiST) answer equality and range predicates (`WHERE user_id = 12345`). Embeddings encode meaning as high-dimensional coordinates (e.g. OpenAI `text-embedding-3-small` at **1,536** dims). Similarity is a distance/inner-product over the whole vector, not an exact key lookup — so without an ANN index the engine must compare the query to every stored vector: **O(n)** ([System Design Newsletter — What is a Vector Database](https://newsletter.systemdesign.one/p/what-is-a-vector-database)).

Worked linear-scan arithmetic from the same source (illustrative unit cost, not a vendor benchmark): at **0.0001 s** per distance, **10,000** chunks → **~1 s/query**; **10 million** chunks → **~1,000 s (~16 min)/query**. At **100 QPS** of concurrent queries the system saturates. A float32 1,536-d vector is **1,536 × 4 = 6,144 bytes (~6 KB)**; memory and I/O scale with that footprint before any graph overhead ([System Design Newsletter](https://newsletter.systemdesign.one/p/what-is-a-vector-database)).

**Decision heuristic (newsletter)**: pgvector is often enough below **~100k** vectors and moderate QPS; millions of vectors plus tight latency SLAs push toward dedicated vector engines ([System Design Newsletter](https://newsletter.systemdesign.one/p/what-is-a-vector-database)).

### Control plane vs data plane (store-centric)

| Plane | Responsibility | Concrete components |
| --- | --- | --- |
| **Control plane** | Index/collection lifecycle, schema (dim, metric, named vectors), HNSW/IVF params, payload/metadata index defs, tenancy/namespace policy, auth keys | Pinecone global control plane (projects/indexes); Qdrant collection + `hnsw_config` + payload index API; Postgres DDL for `pgvector` ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture); [Qdrant Indexing](https://qdrant.tech/documentation/manage-data/indexing/); [pgvector README](https://github.com/pgvector/pgvector)) |
| **Data plane** | Upsert → durable log → memtable/segment → ANN search ± filter → top-k merge | Pinecone regional data plane (write path vs query routers/executors); FAISS in-process indexes; Qdrant segments; Postgres heap + HNSW/IVFFlat ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture); [FAISS wiki](https://github.com/facebookresearch/faiss/wiki)) |

Pinecone serverless separates **writes** (request log + LSN → memtable → immutable **slabs** in object storage) from **reads** (query router → per-slab executors + memtable merge). Fresh writes are searchable from the memtable before slab flush ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture); [Pinecone serverless blog](https://www.pinecone.io/blog/serverless-architecture/)).

### Three layers inside a vector DB

Canonical infrastructure split ([System Design Newsletter](https://newsletter.systemdesign.one/p/what-is-a-vector-database)):

1. **Storage** — raw/compressed vectors + metadata; durability, tiering (RAM / SSD / object store), quantization.
2. **Index** — ANN structure (HNSW graph, IVF lists, PQ codes) trading recall for sublinear search.
3. **Query** — embed-or-accept query vector → ANN traversal → optional metadata filter → distance ranking → top-k.

### HNSW (primary production ANN)

**Paper**: Malkov & Yashunin, *Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs* (arXiv:1603.09320; IEEE TPAMI 2020) ([arXiv](https://arxiv.org/abs/1603.09320)).

Mechanics:

- Incremental multi-layer proximity graphs. Each element’s max layer is drawn from an **exponentially decaying** distribution; upper layers are sparse long-range links, layer 0 is dense.
- Search starts at the top entry point, greedily descends layers, then expands on layer 0 with a candidate list of size **`ef`** (aka `efSearch`).
- Construction params: **`M`** (max edges per node on upper layers; paper suggests reasonable range **5–48**), **`Mmax0 ≈ 2M`** on layer 0, **`efConstruction`** (candidate list during insert; paper notes **efConstruction = 100** builds a usable index on 10M SIFT in ~3 min on a 4×10-core Xeon E5-4650 v2 in their experiment), **`mL`** (level normalization).
- Neighbor-selection **heuristic** (diversity of directions) improves high-recall / clustered data vs naïve closest-M links ([HNSW paper](https://arxiv.org/abs/1603.09320)).

**Complexity (paper)**: under idealized Delaunay-graph assumptions, expected hops per layer are bounded by a constant → **logarithmic search scaling** in \(N\); insertion is a sequence of layer searches ⇒ construction **\(O(N \log N)\)** for relatively low-dimensional regimes. High-\(d\) regimes remain empirically strong but the strict Delaunay analysis does not fully carry over ([HNSW paper §4.2](https://arxiv.org/abs/1603.09320)).

**Faiss `IndexHNSWFlat` memory** (wiki table): **`4d + x·M·2·4` bytes/vector** (float vectors + graph links); exact Flat is **`4d`** ([FAISS indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes)). Newsletter framing matches ops intuition: more **`M` / `ef`** → better recall, higher RAM and slower queries ([System Design Newsletter](https://newsletter.systemdesign.one/p/what-is-a-vector-database)).

### IVF + product quantization (scale / memory path)

Faiss cell-probe (**IVF**): partition into **`nlist`** cells via a coarse quantizer; at query visit **`nprobe`** lists. Rule of thumb: **`nlist ≈ C · √n`** (with \(C\) on the order of ~10 in their cost-balancing argument). Fraction scanned ≈ **`nprobe/nlist`** (underestimate if lists unbalanced) ([FAISS indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes)).

**Product quantization (PQ)** (Jégou et al., PAMI 2011; implemented throughout Faiss): split vector into \(M\) subvectors, each coded in few bits — e.g. factory `IVFx,PQ…` stores **`ceil(M·nbits/8)+8` bytes/vector** (codes + id) vs **`4d`** for Flat ([FAISS wiki](https://github.com/facebookresearch/faiss/wiki); [FAISS indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes)).

Pinecone serverless **slabs** may use different algorithms by size: small slabs → fast random-projection indexes; larger compacted slabs → IVF / PQ / HNSW ([Pinecone ICML 2025 filtering paper](https://www.pinecone.io/research/ICML_2025.pdf)).

### pgvector topology (Postgres-native)

Default without ANN index: **exact** nearest neighbor (perfect recall). Approximate indexes: **HNSW** (better speed–recall, slower build, more memory; no training step) and **IVFFlat** (faster build, less memory, weaker speed–recall; needs data for list training). Distance ops: L2 `<->`, inner product `<#>`, cosine `<=>`, plus Hamming/Jaccard for binary ([pgvector README](https://github.com/pgvector/pgvector)).

HNSW defaults commonly shown: **`m = 16`**, **`ef_construction = 64`**; query-time **`hnsw.ef_search`** (default **40**). IVFFlat: start **`lists`**, query **`ivfflat.probes`** (rule of thumb start **`√lists`**). Dim caps with indexes: **`vector` ≤ 2,000**, **`halfvec` ≤ 4,000**, **`bit` ≤ 64,000** ([pgvector README](https://github.com/pgvector/pgvector)).

### Filtering semantics at the index layer (critical)

| System | Filter × ANN interaction | Source |
| --- | --- | --- |
| **pgvector** | With HNSW/IVFFlat, filters apply **after** the ANN scan. If a predicate matches 10% of rows and `ef_search=40`, expect ~**4** survivors on average → under-filled top-k. Mitigations: B-tree on filter cols, **partial HNSW**, partitioning, **iterative index scans** (`hnsw.iterative_scan = strict_order \| relaxed_order`, cap `hnsw.max_scan_tuples`) | [pgvector README](https://github.com/pgvector/pgvector) |
| **Qdrant** | Payload indexes + **filterable HNSW** (extra edges per indexed payload). Planner: weak filters → HNSW; very strict → payload index + full rescore; mid-selectivity needs filter-aware edges. Create payload indexes **before** ingest (else rebuild HNSW). Defaults: `m=16`, `ef_construct=100`, `full_scan_threshold=10000`. ACORN for multi-filter disconnect / soft-deletes | [Qdrant Indexing](https://qdrant.tech/documentation/manage-data/indexing/); [Qdrant filtering guide](https://qdrant.tech/documentation/search-patterns/vector-search-filtering/) |
| **Weaviate** | Per-shard inverted index builds an allow-list; HNSW only admits IDs on that list (**pre-filter style**). Strategies: **`acorn`** (default as of v1.34 docs) vs **`sweeping`**. `flatSearchCutoff` default **40,000** (restrictive filters fall back to flat). HNSW defaults: `efConstruction=128`, `maxConnections=32`, `ef=-1` (dynamic) | [Weaviate vector index](https://docs.weaviate.io/weaviate/config-refs/indexing/vector-index); [Weaviate filtering concepts](https://archive.docs.weaviate.io/weaviate/concepts/filtering) |
| **Pinecone serverless** | Per-slab **metadata bitmap** → adaptive pre-filter / mid-scan / IVF bypass by selectivity. Operators `$eq/$in/$gt/…/$and/$or`; `$in`/`$nin` max **10,000** values. Executors exclude non-matching IDs before ranking | [Pinecone filter docs](https://docs.pinecone.io/guides/search/filter-by-metadata); [Pinecone ICML 2025](https://www.pinecone.io/research/ICML_2025.pdf); [Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture) |

### Hybrid search at the index (not the LLM)

- **Pinecone**: (1) text-match filter then dense rank; (2) client-side RRF of keyword + dense; (3) Vectors API dense+sparse in one index with client **`alpha`** scaling ([Pinecone hybrid search](https://docs.pinecone.io/guides/search/hybrid-search)).
- **Qdrant**: named dense + sparse vectors; `prefetch` both; fuse with **`RrfQuery` / `Fusion.RRF`** in one request ([Qdrant hybrid course](https://qdrant.tech/course/beginners/module-3/hybrid-search-in-qdrant/)).
- **Weaviate**: vector index + inverted indexes for BM25 / filter acceleration ([Weaviate Indexing](https://weaviate.io/developers/weaviate/concepts/indexing)).
- **pgvector**: dense ANN in Postgres; lexical hybrid typically via Postgres FTS / external BM25 then fuse in app SQL — no first-class sparse+dense fusion API in the extension itself `[inferred from extension surface]`.

---

## 2. Token Economics & NFR Metrics

> ⚠️ Limited public data available for this dimension. Vector DBs are not token-metered; “cost” is storage/RAM/QPS. Embedding **token** spend is an application concern (topic 6). Below: published ANN latency/recall/memory and storage arithmetic — not LLM pricing.

### Storage cost drivers (quantified)

| Item | Value | Source |
| --- | --- | --- |
| float32 1536-d vector | **6,144 B** | \(1536×4\); newsletter cites ~**6 KB** ([System Design Newsletter](https://newsletter.systemdesign.one/p/what-is-a-vector-database)) |
| Scalar quantization 32→8 bit | **~4×** smaller codes | Newsletter; Faiss SQ8 stores **`d` bytes** vs Flat **`4d`** ([FAISS indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes)) |
| Faiss HNSW Flat | **`4d + O(M)`** link overhead | [FAISS indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes) |
| Faiss IVF+PQ example | codes **`ceil(M·nbits/8)+8`** B/vec | [FAISS indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes) |
| nmslib HNSW on SIFT1M (Faiss wiki table) | vectors **512 MB** + graph **~796 MB**; build **173 s**; search batch **0.081 s** at R@1 **0.8195** | [FAISS Indexing 1M](https://github.com/facebookresearch/faiss/wiki/Indexing-1M-vectors) |

**pgvector ops levers**: keep HNSW graph in `maintenance_work_mem` during build (notice fires when graph spills after e.g. **100k** tuples); prefer `halfvec` / binary quantization + re-rank at scale ([pgvector README](https://github.com/pgvector/pgvector)).

### Published recall@k / latency / QPS proxies

**Faiss HNSW Flat on SIFT1M** (20 threads, batch-friendly; wiki warns single-thread ~**5–12×** slower, one-by-one another **~2–3×**) ([FAISS Indexing 1M](https://github.com/facebookresearch/faiss/wiki/Indexing-1M-vectors)):

| `efSearch` | ms/query | R@1 |
| --- | --- | --- |
| 16 | 0.011 | 0.8740 |
| 32 | 0.020 | 0.9492 |
| 64 | 0.033 | 0.9779 |
| 128 | 0.059 | 0.9887 |
| 256 | 0.104 | 0.9920 |

Equivalent QPS (single-query latency inverse, **batch/20-thread context** — not client-facing p99): e.g. **0.033 ms → ~30k QPS/core-batch** `[inferred arithmetic from published ms/query; do not treat as production SLA]`.

**Faiss IVFFlat baseline (same bench)**: `nprobe=64` → **0.141 ms**, R@1 **0.9470**; HNSW reaches **>0.9** R@1 near **0.020 ms** — better speed/precision at higher memory ([FAISS Indexing 1M](https://github.com/facebookresearch/faiss/wiki/Indexing-1M-vectors)).

**HNSW + SQ** (same page): `efSearch=64` → **0.011 ms**, R@1 **0.9242** — faster/cheaper than full-precision HNSW at that recall point.

**Pinecone serverless filtered search** (vendor research, warm slabs, internal latency excl. client RTT) ([Pinecone ICML 2025](https://www.pinecone.io/research/ICML_2025.pdf)):

| Dataset | Scale | Metric | Mean recall | Internal latency |
| --- | --- | --- | --- | --- |
| YFCC (BigANN filter track) | **10M × 192-d** | recall@10 | **0.989** | **~20 ms** |
| Production customer | **35M** | recall@100 | **0.986** | **~75 ms** |

Selectivity swept from ~**10** matches to ~**2M** on YFCC; adaptive IVF avoided the recall cliffs of naïve filtered IVF ([same paper](https://www.pinecone.io/research/ICML_2025.pdf)).

### Latency SLA framing `[inferred]`

| Tier | Typical target (architect judgment) | What drives it |
| --- | --- | --- |
| In-process Faiss HNSW (warm, batched) | sub-ms–few ms | `efSearch`, threads, dim |
| Managed DB p50 | tens of ms | network + filter + cold slab fetch |
| Managed DB p99 | 100s of ms+ | cold cache, strict filters, compaction |

No universal vendor p50/p95/p99 SLA table is cited here — Pinecone/Weaviate/Qdrant public docs emphasize architecture and knobs more than contractual percentile SLOs.

### “Token” adjacency (embedding side only)

Index size ∝ **#chunks × dim × bytes/dim × (1 + graph overhead)**. Reducing chunks (topic 6) or dim (Matryoshka / PCA in Faiss) cuts RAM faster than tuning `ef` alone `[inferred]`. Semantic/prompt caching is orthogonal to the vector store.

---

## 3. Distributed Resilience & State

### Durability & write visibility

- **Pinecone**: write → durable **request log** with **LSN** → immediate **200 OK**; background index builder; memtable for read-your-writes; slabs immutable; deletes/updates via **tombstones** + compaction (LSM-like) ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture); [ICML 2025](https://www.pinecone.io/research/ICML_2025.pdf)).
- **pgvector**: inherits **Postgres WAL**, checkpoints, replication, PITR — vector indexes are Postgres indexes rebuilt/maintained under normal vacuum/index machinery ([pgvector README](https://github.com/pgvector/pgvector); Postgres durability `[standard Postgres]`).
- **Qdrant**: segment-oriented storage; payload indexes persisted; memory tiers `pinned` / `cached` / `cold` for payload indexes; HNSW rebuild is **heavy** (nudge `ef_construct` by 1 to force optimizer reindex) ([Qdrant Indexing](https://qdrant.tech/documentation/manage-data/indexing/)).
- **Faiss**: library, not a distributed DB — persistence is explicit `write_index` / reload; no built-in replication ([FAISS wiki](https://github.com/facebookresearch/faiss/wiki)).

### Consistency under filters & deletes

- Soft-deleted points can **disconnect** filterable HNSW; Qdrant documents **ACORN** (2-hop exploration) when extra payload edges are insufficient ([Qdrant Indexing](https://qdrant.tech/documentation/manage-data/indexing/)).
- Pinecone query path merges slab results with memtable and applies tombstones so updates/deletes stay correct without rewriting slabs in place ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture); [ICML 2025](https://www.pinecone.io/research/ICML_2025.pdf)).
- pgvector **iterative scans** reconcile post-filter emptiness by scanning further into the graph until enough rows or caps hit ([pgvector README](https://github.com/pgvector/pgvector)).

### Scaling / isolation patterns

- **Namespaces** (Pinecone): hard partition; queries scoped to a namespace; isolation unit for multi-tenant designs ([Pinecone serverless blog](https://www.pinecone.io/blog/serverless-architecture/)).
- **Payload tenancy** (Qdrant): `is_tenant=true` keyword index co-locates tenant points for sequential reads; optional shard keys for large tenants ([Qdrant Multitenancy](https://qdrant.tech/documentation/manage-data/multitenancy/)).
- **pgvector**: shared HNSW across tenants lets foreign vectors affect recall/speed — prefer **partitioning** or **partial indexes** per tenant ([pgvector README](https://github.com/pgvector/pgvector)).
- HNSW paper notes skip-list-like structure enables **balanced distributed** implementations in principle ([HNSW paper](https://arxiv.org/abs/1603.09320)).

### Circuit breakers / rate limits

Pinecone documents request validation against **rate and object limits** on the query path ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture)). Qdrant Cloud **strict mode** rejects expensive filters on unindexed fields with **400** ([Qdrant hybrid course](https://qdrant.tech/course/beginners/module-3/hybrid-search-in-qdrant/)). Generic token-bucket agent circuit breakers are application-layer, not store-native `[inferred]`.

---

## 4. Enterprise Security & Governance

### AuthN / AuthZ at the store

| Product | Mechanism | Notes |
| --- | --- | --- |
| **Pinecone** | Project-scoped **API key** at gateway; routes to control vs data plane | Namespace isolation is a data-plane partition, not a substitute for app auth ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture)) |
| **Qdrant** | TLS; admin / read-only API keys; **JWT RBAC** (`access`: `r` / `m` / per-collection `r`/`rw`); optional `value_exists` claim; Cloud enables granular keys by default | Collection-level RBAC ≠ automatic tenant payload injection — app/gateway must force `tenant_id` filters ([Qdrant security docs](https://github.com/qdrant/landing_page/blob/master/qdrant-landing/content/documentation/security.md); [Secure Qdrant tutorial](https://qdrant.tech/documentation/tutorials-operations/secure-qdrant/); [GitHub issue #8015](https://github.com/qdrant/qdrant/issues/8015)) |
| **pgvector** | Postgres roles, TLS, optional **RLS** on tables | Vector column is ordinary table data — govern like PII-bearing rows `[inferred]` |
| **Weaviate** | Cluster auth / API keys (product-dependent) | Filter allow-lists enforce *query* predicates, not identity `[inferred]` |

### Multi-tenant isolation (governance critical)

- **Hard isolation**: separate indexes/namespaces/collections/databases per tenant — strongest, highest ops cost.
- **Soft isolation**: shared index + **mandatory** metadata/`tenant_id` filter on every query. Failure to inject the filter is a **cross-tenant data leak** class of bug ([Qdrant Multitenancy](https://qdrant.tech/documentation/manage-data/multitenancy/); pgvector tenancy warning ([pgvector README](https://github.com/pgvector/pgvector))).
- Qdrant JWT can scope **which collection** is readable; it does **not** (as of cited issue discussion) auto-bind a tenant claim into payload filters ([issue #8015](https://github.com/qdrant/qdrant/issues/8015)).

### PII, audit, sandbox

> ⚠️ Limited public data available for this dimension. Vector DB docs rarely specify NER/PII redaction pipelines; those belong in the ingestion control plane. Auditability typically means: API access logs (Cloud), Postgres `pgaudit`, or app-level query logging — not a standard “vector audit schema.” Mark compliance mappings (SOC2/HIPAA) as vendor Cloud certifications, not ANN properties `[inferred]`.

Embeddings of PII are still PII-bearing artifacts: retention, encryption-at-rest, and delete/tombstone latency (Pinecone LSM tombstones) matter for right-to-erasure `[inferred from architecture]`.

---

## 5. Production Failure Modes

### 1. Post-filter / restrictive-filter recall collapse

**Symptom**: top-k underfilled or semantically wrong neighbors when metadata is selective.  
**Mechanism**: pgvector post-filters ANN candidates; naïve IVF ignores filter when choosing `nprobe` clusters → true neighbors never scanned ([pgvector README](https://github.com/pgvector/pgvector); [Pinecone ICML 2025 Fig. 4 narrative](https://www.pinecone.io/research/ICML_2025.pdf)).  
**Mitigation**: iterative scans / partial indexes (pgvector); filterable HNSW + payload indexes before ingest (Qdrant); allow-list+ACORN or flat cutoff (Weaviate); adaptive IVF bypass + scan fraction (Pinecone).

### 2. Graph disconnection under deletes / multi-filter

**Symptom**: high latency or low recall with multiple `must` filters.  
**Mechanism**: HNSW edges assume navigable neighborhoods; filtered traversal can strand the search. Qdrant adds payload-specific edges; still may need **ACORN** ([Qdrant Indexing](https://qdrant.tech/documentation/manage-data/indexing/)).

### 3. Index build / rebuild outages

**Symptom**: long write stalls, recovery after node loss measured in hours.  
**Mechanism**: HNSW insert is itself ANN search at `efConstruction`; bulk rebuild is CPU-heavy. Newsletter flags rebuild time as an incident differentiator ([System Design Newsletter](https://newsletter.systemdesign.one/p/what-is-a-vector-database); [HNSW paper](https://arxiv.org/abs/1603.09320); [Qdrant rebuild note](https://qdrant.tech/documentation/manage-data/indexing/)).  
**pgvector**: graph spill out of `maintenance_work_mem` slows builds ([pgvector README](https://github.com/pgvector/pgvector)).

### 4. Quantization / ANN approximation error

**Symptom**: “correct” chunk missing from top-k though present in corpus.  
**Mechanism**: ANN + SQ/PQ intentionally trade recall; Faiss HNSW R@1 at `efSearch=16` is **0.874**, not 1.0 ([FAISS Indexing 1M](https://github.com/facebookresearch/faiss/wiki/Indexing-1M-vectors)).  
**Mitigation**: raise `ef`/`nprobe`, re-rank shortlist with full precision (Faiss `RFlat` / pgvector binary-quantize then re-rank pattern).

### 5. Cold slab / tier miss latency spikes

**Symptom**: p99 ≫ p50 on managed serverless.  
**Mechanism**: Pinecone executors fetch uncached slabs from object storage ([Pinecone architecture](https://docs.pinecone.io/guides/get-started/database-architecture)). Qdrant cold/cached payload tiers add I/O ([Qdrant Indexing](https://qdrant.tech/documentation/manage-data/indexing/)).

### 6. Dimension / metric mismatch

**Symptom**: systematic garbage neighbors.  
**Mechanism**: query and corpus must share embedding model, dimensionality, and metric (L2 vs IP vs cosine). pgvector indexes are per-opclass (`vector_l2_ops` vs `vector_cosine_ops`) ([pgvector README](https://github.com/pgvector/pgvector)).

### 7. Multi-tenant filter omission (security incident)

**Symptom**: tenant A retrieves tenant B chunks.  
**Mechanism**: soft tenancy relies on every query including the tenant predicate; JWT collection RBAC alone is insufficient ([Qdrant Multitenancy](https://qdrant.tech/documentation/manage-data/multitenancy/); [issue #8015](https://github.com/qdrant/qdrant/issues/8015)).

### 8. Faiss operational limits

`IndexHNSW` **does not support remove** without destroying graph structure — deletes need rebuild or external ID maps ([FAISS indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes)).

---

## 6. Enterprise System Design Scenarios

### Scenario A — “Postgres is enough”

- **Shape**: <~**100k** vectors, QPS moderate, strong need for joins/transactions with business tables ([System Design Newsletter](https://newsletter.systemdesign.one/p/what-is-a-vector-database)).
- **Choice**: `pgvector` HNSW (`m=16`, tune `ef_search`), B-tree on filter columns, iterative scans if selective filters, `halfvec` if RAM-bound ([pgvector README](https://github.com/pgvector/pgvector)).
- **Risk**: post-filter under-recall; shared-tenant HNSW interference.

### Scenario B — “Filtered product search at 10M–35M”

- **Shape**: categorical/numeric metadata on every query; need **≥0.98** recall@k under varying selectivity.
- **Evidence**: Pinecone serverless adaptive filtering — YFCC **recall@10 = 0.989 @ ~20 ms**; customer **recall@100 = 0.986 @ ~75 ms** ([ICML 2025](https://www.pinecone.io/research/ICML_2025.pdf)).
- **Design**: bitmap/payload indexes co-designed with ANN; never assume unfiltered HNSW params transfer to filtered traffic.

### Scenario C — “Self-hosted filterable HNSW”

- **Choice**: Qdrant — create payload indexes first; `m=16`, `ef_construct=100`; tenant field `is_tenant=true`; hybrid via dense+sparse prefetch + RRF ([Qdrant Indexing](https://qdrant.tech/documentation/manage-data/indexing/); [Multitenancy](https://qdrant.tech/documentation/manage-data/multitenancy/); [hybrid](https://qdrant.tech/course/beginners/module-3/hybrid-search-in-qdrant/)).
- **Security**: TLS + JWT collection RBAC + **mandatory** tenant filter in gateway.

### Scenario D — “Weaviate collection with BM25 + vectors”

- **Choice**: HNSW + inverted indexes; `filterStrategy=acorn` for negatively correlated filters; watch `flatSearchCutoff=40000` ([Weaviate vector index](https://docs.weaviate.io/weaviate/config-refs/indexing/vector-index); [filtering](https://archive.docs.weaviate.io/weaviate/concepts/filtering)).

### Scenario E — “Embedded library in the retrieval worker”

- **Choice**: Faiss IVF-PQ or HNSW in the app process; own sharding/replication. Best published knobs for pure ANN speed/recall (SIFT1M tables), weakest for multi-tenant metadata governance ([FAISS wiki](https://github.com/facebookresearch/faiss/wiki)).

### Trade-off matrix

| Approach | Query latency (warm) | Recall control | Filtered search | Ops complexity | Best fit |
| --- | --- | --- | --- | --- | --- |
| Exact scan (pgvector no index / Faiss Flat) | Poor at large \(N\) | Perfect | Trivial | Low | Tiny corpora |
| pgvector HNSW | Good | `ef_search` | Post-filter (+ iterative) | Low (Postgres) | <100k–low millions, SQL joins |
| Faiss HNSW | Excellent (batch) | `efSearch`/`M` | DIY | App-owned | Offline / single-tenant workers |
| Faiss IVF+PQ | Excellent @ scale | `nprobe` + codes | DIY | App-owned | Memory-bound billions-class |
| Qdrant filterable HNSW | Strong | `ef`/`m` | First-class | Medium | Self-hosted filtered ANN |
| Weaviate HNSW+inverted | Strong | `ef` / dynamic ef | Pre-filter allow-list | Medium | Hybrid BM25+vector |
| Pinecone serverless | Strong (20–75 ms internal in cited benches) | Managed | Adaptive bitmap+IVF | Low (managed) | Multi-tenant SaaS |

### Capacity planning sketch `[inferred from published formulas]`

For **\(N\)** float32 vectors dim **\(d\)**, HNSW RAM ≈ \(N · (4d + c·M)\) with \(c≈8\) bytes/link-ish from Faiss’s `4d + x·M·2·4` model ([FAISS indexes](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes)). Example: \(N=10^7\), \(d=1536\), \(M=16\): vectors alone ≈ **61.4 GB**; graph adds several GB more — before replicas. Quantize or IVF-PQ when this exceeds node RAM; newsletter’s 4× SQ is the first lever ([System Design Newsletter](https://newsletter.systemdesign.one/p/what-is-a-vector-database)).

### Architect interview soundbites

1. Exact NN is **O(n)**; HNSW targets **O(log n)** navigation with **`M` / `ef`** as the recall dial ([HNSW paper](https://arxiv.org/abs/1603.09320)).
2. Always ask: **pre-filter, post-filter, or filter-aware graph?** — pgvector vs Qdrant/Weaviate/Pinecone diverge here.
3. Hybrid belongs in the **index API** (RRF, sparse+dense, text-match) before the LLM ([Pinecone hybrid](https://docs.pinecone.io/guides/search/hybrid-search); [Qdrant hybrid](https://qdrant.tech/course/beginners/module-3/hybrid-search-in-qdrant/)).
4. Multi-tenancy without enforced filters is a **security** design bug, not a tuning issue.

---

## Sources

- [1] https://newsletter.systemdesign.one/p/what-is-a-vector-database — System Design Newsletter #141: What is a Vector Database (primary article)
- [2] https://arxiv.org/abs/1603.09320 — Malkov & Yashunin HNSW paper (arXiv:1603.09320)
- [3] https://github.com/facebookresearch/faiss/wiki — Faiss home / research foundations
- [4] https://github.com/facebookresearch/faiss/wiki/Faiss-indexes — Index types, bytes/vector, HNSW/IVF/PQ params
- [5] https://github.com/facebookresearch/faiss/wiki/Indexing-1M-vectors — SIFT1M HNSW/IVF recall@1 and ms/query benches
- [6] https://github.com/pgvector/pgvector — pgvector README (HNSW/IVFFlat, filtering, iterative scans, dims)
- [7] https://docs.pinecone.io/guides/get-started/database-architecture — Pinecone control/data plane, slabs, memtable
- [8] https://docs.pinecone.io/guides/search/filter-by-metadata — Metadata filter operators and limits
- [9] https://docs.pinecone.io/guides/search/hybrid-search — Hybrid patterns (filter, RRF, dense+sparse)
- [10] https://www.pinecone.io/research/ICML_2025.pdf — Serverless metadata filtering; recall@k and latency on 10M/35M
- [11] https://www.pinecone.io/blog/serverless-architecture — WAL, freshness layer, namespaces
- [12] https://qdrant.tech/documentation/manage-data/indexing/ — Payload indexes, HNSW defaults, filterable HNSW, ACORN
- [13] https://qdrant.tech/documentation/search-patterns/vector-search-filtering/ — Filter cardinality and planner behavior
- [14] https://qdrant.tech/course/beginners/module-3/hybrid-search-in-qdrant/ — Dense+sparse prefetch + RRF; strict mode
- [15] https://qdrant.tech/documentation/manage-data/multitenancy/ — `is_tenant` payload tenancy
- [16] https://github.com/qdrant/landing_page/blob/master/qdrant-landing/content/documentation/security.md — API keys, JWT RBAC, TLS claims
- [17] https://qdrant.tech/documentation/tutorials-operations/secure-qdrant/ — Secure deployment tutorial (TLS + JWT_RBAC)
- [18] https://github.com/qdrant/qdrant/issues/8015 — Tenant filter not auto-injected from JWT
- [19] https://weaviate.io/developers/weaviate/concepts/indexing — Vector + inverted index overview
- [20] https://docs.weaviate.io/weaviate/config-refs/indexing/vector-index — HNSW params, filterStrategy, flatSearchCutoff
- [21] https://archive.docs.weaviate.io/weaviate/concepts/filtering — Pre-filter allow-list + HNSW; ACORN/sweeping
- [22] https://weaviate.io/blog/weaviate-1-27-release — ACORN filtered-search motivation and behavior
