# Module 11 -- How Vector Databases Work

### What Is This?

Vector databases answer "what is similar?" rather than "what is equal?" -- they index high-dimensional embeddings and return approximate nearest neighbors in sub-linear time instead of scanning every vector. Traditional relational indexes (B-tree, hash, GiST) answer equality and range predicates; they cannot efficiently compute distance or inner product across an entire 1,536-dimensional vector. Without an ANN index, every query is O(N) brute force -- at 10M vectors that means roughly 1,000 seconds per query. This module covers the **index/store layer only**: ANN algorithms (HNSW, IVF, IVF-PQ, DiskANN, ScaNN), filtering semantics, hybrid search, durability, multi-tenancy, security, and the nine ways vector databases silently fail in production. Chunking, contextual retrieval, and prompt assembly live in Module 06.

---

## Part 1 -- System Topology & Data Flow

### Architecture Map

A production vector database decomposes into a **storage layer** handling vector persistence and compression, an **index layer** maintaining graph or cluster structures for sub-linear search, and a **query layer** routing requests, applying filters, and fusing results. Surrounding these are a **control plane** managing configuration and access, a **telemetry layer** tracking retrieval quality and cost, and optional **tool proxies** integrating with embedding services and reranking models.

```
+----------------------------------------------------------------------------------+
|                                CONTROL PLANE                                      |
|                                                                                   |
|  +---------------------+  +---------------------+  +--------------------------+  |
|  | Cluster Coordinator  |  | Auth / RBAC Engine   |  | Index Lifecycle Manager  |  |
|  | (shard map, routing  |  | (API keys, JWT,      |  | (build triggers, rebuild |  |
|  |  table, node health, |  |  collection-level     |  |  scheduling, concurrency |  |
|  |  rebalance triggers) |  |  roles, tenant        |  |  limits, stagger policy, |  |
|  |                      |  |  isolation policy)    |  |  compaction windows)     |  |
|  +----------+-----------+  +----------+-----------+  +-------------+------------+  |
+-------------+----------------------------+----------------------------+------------+
              | routing metadata           | auth context               | build/compact
+-------------v----------------------------v----------------------------v------------+
|                                QUERY LAYER (DATA PLANE)                            |
|                                                                                    |
|  +---------------+  +----------------+  +------------------+  +-----------------+  |
|  | Query Router   |  | Filter Engine   |  | ANN Search       |  | Result Merger   |  |
|  | (fan-out to    |  | (pre-filter:    |  | (HNSW graph walk |  | (shard results  |  |
|  |  relevant      |  |  metadata index |  |  or IVF cluster  |  |  merge, top-K   |  |
|  |  shards via    |  |  integration;   |  |  probe; ef_search|  |  extraction,    |  |
|  |  shard map or  |  |  post-filter:   |  |  / nprobe tuning |  |  optional cross |  |
|  |  scatter-      |  |  k*N oversample |  |  per latency     |  |  -encoder       |  |
|  |  gather)       |  |  + discard)     |  |  budget)         |  |  rerank)        |  |
|  +-------+-------+  +-------+--------+  +--------+---------+  +-------+---------+  |
|          |                  |                      |                    |            |
|  +-------v------------------v----------------------v--------------------v---------+  |
|  |                      Hybrid Search Coordinator                                |  |
|  |  BM25 sparse path --+                                                         |  |
|  |                      +-- Reciprocal Rank Fusion (by rank, NOT score) --> K     |  |
|  |  Dense vector path --+                                                         |  |
|  +-------------------------------------------------------------------------------+  |
+---------------------------------------------+--------------------------------------+
                                              | read/write ops
+---------------------------------------------v--------------------------------------+
|                              INDEX LAYER                                            |
|                                                                                     |
|  +------------------+  +------------------+  +----------------------------------+  |
|  | HNSW Engine       |  | IVF/IVF-PQ       |  | DiskANN Engine                   |  |
|  | (multi-layer      |  | Engine           |  | (Vamana graph in RAM,            |  |
|  |  skip-list graph; |  | (k-means         |  |  PQ codes in RAM,                |  |
|  |  greedy descent   |  |  centroids +     |  |  full-precision vectors on SSD;  |  |
|  |  from top layer   |  |  per-cluster     |  |  rerank from SSD on final        |  |
|  |  to layer 0;      |  |  exact search;   |  |  candidates only)                |  |
|  |  incremental      |  |  optional PQ     |  |                                  |  |
|  |  insert/delete)   |  |  compression)    |  |                                  |  |
|  +----------+--------+  +----------+-------+  +-----------------+----------------+  |
+-------------+---------------------------+----------------------------+---------------+
              |                           |                            |
+-------------v---------------------------v----------------------------v---------------+
|                             STORAGE / PERSISTENCE LAYER                              |
|                                                                                      |
|  +----------------------+  +-----------------------+  +--------------------------+  |
|  | Vector Store          |  | WAL (Write-Ahead Log)  |  | Metadata Store           |  |
|  | (raw float32 vectors; |  | (crash recovery;       |  | (payload fields,         |  |
|  |  6 KB per 1536-dim    |  |  sync = durability,    |  |  tenant_id, model_ver,   |  |
|  |  vector; 600 GB at    |  |  async = speed;        |  |  source, timestamps;     |  |
|  |  100M vectors;        |  |  HNSW rebuild from     |  |  payload indexes for     |  |
|  |  quantized variants:  |  |  WAL can take minutes  |  |  pre-filter traversal    |  |
|  |  SQ8=4x, BQ=32x,     |  |  to hours at scale)    |  |  integration)            |  |
|  |  PQ=32x compression)  |  |                        |  |                          |  |
|  +----------------------+  +-----------------------+  +--------------------------+  |
|                                                                                      |
|  +------------------------------------------------------------------------------+    |
|  | Tiered Storage Controller                                                    |    |
|  | (hot vectors in RAM --> warm in memory-mapped files --> cold on SSD/S3)      |    |
|  | (stateless arch: compute nodes + object storage; stateful: shard-local)      |    |
|  +------------------------------------------------------------------------------+    |
+--------------------------------------------------------------------------------------+
              | metrics
+-------------v------------------------------------------------------------------------+
|                          TELEMETRY / OBSERVABILITY LAYER                              |
|                                                                                      |
|  Shard load skew ratio (alert >3-5x) | Centroid query concentration (>40% = alarm)  |
|  Replica neighbor mismatch rate (any non-zero = problem) | p99/p50 ratio (>3x)      |
|  KL divergence on distance distributions (embedding drift) | SSD TBW monitor        |
|  Index rebuild duration + resource contention | QPS per recall tier | cost/query     |
+--------------------------------------------------------------------------------------+
```

```
+--------------------------------------------------------------------------------+
|                         TOOL PROXY LAYER                                        |
|  (external services invoked by the vector DB pipeline)                         |
|                                                                                |
|  +------------------+  +-------------------+  +----------------------------+   |
|  | Embedding Service |  | Reranker Service   |  | BM25 Index                |   |
|  | (OpenAI, Cohere,  |  | (Voyage rerank-    |  | (Elasticsearch, Tantivy,  |   |
|  |  Voyage; MUST use |  |  2.5, Cohere       |  |  or native -- Weaviate,   |   |
|  |  same model for   |  |  rerank-v3;        |  |  Qdrant v1.10;            |   |
|  |  ingestion +      |  |  adds 100-300ms;   |  |  manual composition for   |   |
|  |  query -- shared  |  |  15-25% Precision  |  |  pgvector)                |   |
|  |  vector space     |  |  @5 lift)          |  |                           |   |
|  |  invariant)       |  |                    |  |                           |   |
|  +------------------+  +-------------------+  +----------------------------+   |
+--------------------------------------------------------------------------------+
```

### Plane Responsibilities

| Plane | Role in a Vector Store | Concrete Components |
| --- | --- | --- |
| **Control Plane** | Index/collection lifecycle, schema (dim, metric, named vectors), ANN params, payload index defs, tenancy policy, auth keys | Pinecone global control plane (projects/indexes); Qdrant collection + `hnsw_config` + payload indexes; Postgres DDL for `pgvector` |
| **Data Plane** | Upsert -> durable log -> memtable/segment; query routers/executors; ANN +/- filter -> top-k merge | Pinecone regional write path vs query executors; FAISS in-process; Qdrant segments; Postgres heap + HNSW/IVFFlat |
| **Persistence** | Raw/quantized vectors + metadata; RAM/SSD/object tiers; WAL/request log; immutable slabs | Pinecone LSN log -> memtable -> slabs; Postgres WAL; Qdrant segments + payload tiers `pinned`/`cached`/`cold` |
| **Tool Proxies** | Agent-facing retrieve/upsert tools, hybrid fuse, gateway-enforced tenant filters | MCP `retrieve` tool; Pinecone/Qdrant/Weaviate clients; app gateway that injects `tenant_id` |
| **Telemetry** | Query latency, QPS, filter selectivity, recall proxies, breaker state, correlation IDs | Cloud metrics + app spans; rate-limit counters |

### Three Layers Inside the Store

1. **Storage** -- vectors + metadata; durability, tiering (RAM / SSD / object store), quantization.
2. **Index** -- ANN structure (HNSW graph, IVF lists, PQ codes) trading recall for sublinear search.
3. **Query** -- accept/embed query vector -> filter x ANN -> distance rank -> top-k.

**Real-world example.** A single 1,536-dim float32 vector consumes 1,536 x 4 = 6,144 bytes (~6 KB). At 100M vectors, raw storage alone is ~600 GB before any index overhead. This is why quantization (SQ8 = 4x, BQ = 32x, PQ = 32x) and tiered storage (hot in RAM, warm on memory-mapped files, cold on SSD/S3) are architectural necessities, not optimizations.

### End-to-End Request-Flow Narrative

**Online query path (embed -> filter -> ANN -> hydrate):**

1. **Ingress** -- Client or MCP `retrieve` tool sends query text (or precomputed vector), `top_k`, and metadata predicates. The gateway attaches **mandatory** tenant scope before the call leaves the proxy. Multi-tenant isolation failures here mean another customer's knowledge base appears in AI responses -- a semantic data leak with no relational-DB equivalent.
2. **Embed** -- If the request carries text, the application produces a query vector matching the corpus model/dim/metric. The shared vector space invariant is enforced: same model for ingest and query. A mismatch silently destroys recall.
3. **Filter** -- Metadata/ACL predicates are applied. The strategy varies critically by engine:
   - **Pre-filter** (Weaviate): inverted allow-list before HNSW admits IDs.
   - **Filter-aware graph** (Qdrant): filterable HNSW adds payload edges; planner chooses HNSW vs payload-index+rescore.
   - **Adaptive** (Pinecone): per-slab metadata bitmaps adapt by selectivity.
   - **Post-filter** (pgvector): predicates applied **after** ANN -- selective filters under-fill top-k.
4. **ANN** -- Traversal (HNSW `efSearch`, IVF `nprobe`) produces approximate neighbors among admitted IDs. Fresh Pinecone writes are visible from the **memtable** before slab flush.
5. **Hydrate** -- Rank by distance/IP/cosine; return top-k IDs + payloads. Hybrid paths may prefetch dense+sparse and fuse with RRF in-engine before hydrate.
6. **Telemetry** -- Emit stage timers, selectivity, correlation ID; feed circuit breakers.

**Write path:** upsert -> durable request log / WAL with LSN -> 200 OK -> background index builder; deletes via tombstones + compaction (LSM-like on Pinecone).

---

## Part 2 -- Core Mechanics & Algorithms

### Why Exact Scan Collapses

Brute-force exact search is O(N). At 10M vectors, a single query takes ~1,000 seconds. ANN indexes reduce this to sub-linear time, typically <5ms at 10M scale with 95-99% recall. The engineering decision is always: **how much recall to trade for how much speed**.

Illustrative arithmetic: at 0.0001 s per distance computation, 10k vectors -> ~1 s/query; 10M -> ~1,000 s. At 100 QPS the system saturates.

**Decision heuristic:** pgvector is often sufficient below ~100k vectors and moderate QPS; millions of vectors plus tight latency SLAs push toward dedicated vector engines.

### HNSW (Hierarchical Navigable Small World) -- The Dominant Production Algorithm

Used by Pinecone, Qdrant, Weaviate, Milvus, pgvector. Paper: Malkov & Yashunin, arXiv:1603.09320.

**Mechanism.** Builds a multi-layer graph inspired by skip lists. Top layers are sparse (express lanes for long-range jumps), bottom layers are dense (fine-grained local search). Each vector appears at layer 0; probability of promotion to layer L decreases exponentially: P(L) ~ 1/M^L where M is the max-connections parameter.

**Search process.** Enter at top layer entry point. Greedily navigate to the closest node. Descend one layer. Repeat until layer 0. Final neighborhood search at layer 0 with a candidate list of size `ef_search`, evaluating neighbors of neighbors. Return the k closest from this final candidate set.

**Build process.** For each new vector: (1) determine maximum layer via exponential distribution, (2) greedily descend to the target layer, (3) at each layer from target down to 0, select M nearest neighbors and create bidirectional edges. The neighbor-selection **heuristic** (directional diversity) beats naive closest-M on clustered data. Build complexity: O(N log N).

**Key parameters:**

| Parameter | Effect | Typical Range |
| --- | --- | --- |
| **M** (max connections) | Higher = better recall, more RAM, slower build. Memory overhead scales linearly with M. Mmax0 ~ 2M on layer 0. | 16-64 |
| **ef_construction** | Candidate list during build. Higher = better graph quality, slower construction. Set once -- cannot change without rebuild. Paper notes 100 builds usable index on 10M SIFT in ~3 min on 4x10-core Xeon. | 100-400 |
| **ef_search** | Candidate list during query. Higher = better recall, higher latency. **The primary recall/speed knob in production** -- tunable at query time. | 50-200+ |

**Characteristics:**
- Supports incremental inserts/deletes without full rebuild (key advantage over IVF).
- Memory overhead: ~1.5x raw vector size for graph edges. Faiss formula: `4d + x*M*2*4` bytes/vector (floats + graph links); Flat is `4d`.
- Typical recall: 98%+ at ef_search=100.
- **Topology-dependent on insert order** -- two replicas built from the same vectors in different order produce different neighbor sets (the index divergence failure mode).

**Complexity (paper).** Under idealized Delaunay-graph assumptions, expected hops per layer are bounded by a constant -> **logarithmic search** in N. Construction O(N log N) in relatively low-d regimes. High-d regimes remain empirically strong but strict Delaunay analysis does not fully carry over.

### IVF (Inverted File Index)

Partitions vector space into Voronoi cells via k-means clustering.

**Mechanism.** Build phase: run k-means to create `nlist` cluster centroids (100-10,000). Assign each vector to nearest centroid. Query phase: compute distance from query to all centroids, identify `nprobe` closest centroids, perform exact brute-force search only within those clusters. Rule of thumb: `nlist ~ C * sqrt(N)` with C ~ 10. Fraction scanned ~ `nprobe/nlist`.

**Complexity.** Build: O(N * nlist * iterations). Query: O(nlist + nprobe * N/nlist). With nprobe << nlist, effective query cost is O(N * nprobe/nlist).

**Trade-offs vs. HNSW.** Lower memory footprint (no graph edges). Does NOT support efficient incremental inserts -- new vectors may land in wrong clusters, requiring periodic k-means retraining. Less recall at equivalent latency. Better for static, large datasets where memory is the constraint.

### IVF-PQ (IVF + Product Quantization)

Composite index combining IVF's space partitioning with PQ's vector compression.

**Mechanism.** IVF narrows search to relevant clusters. Within each cluster, Product Quantization (Jegou et al., PAMI 2011) splits each D-dimensional vector into M sub-vectors of D/M dimensions each. Each sub-vector is replaced by the index of its nearest entry in a learned codebook (typically 256 entries = 8 bits per sub-vector). Distance computed using asymmetric distance computation (ADC): exact distance from query sub-vector to each codebook entry, summed across sub-vectors.

**Compression math.** A 1536-dim float32 vector = 6,144 bytes. With M=48 sub-quantizers at 8 bits: 48 bytes per vector = **128x compression**. At 500M vectors: ~24 GB RAM vs ~3 TB uncompressed. Trade-off: 3-5% recall loss vs full-precision HNSW. Faiss stores `ceil(M*nbits/8)+8` bytes/vector for IVF-PQ.

**Best for.** Billion-scale, memory-constrained deployments where 95% recall is acceptable.

### DiskANN (Microsoft Research)

Graph-based index designed for SSD-resident operation. Powers Bing semantic search and Azure Cosmos DB vector index.

**Mechanism.** Builds a Vamana graph -- similar to HNSW but single-layer with better degree control (bounded out-degree, no multi-layer hierarchy). The key insight: keep PQ-compressed vectors and the graph adjacency list in RAM for traversal, but store full-precision vectors on NVMe SSD. During search, the graph is walked using compressed vectors for approximate distance. Only the final top candidates are fetched from SSD for precise reranking.

**Performance.** Sub-millisecond latency at 95%+ recall on billion-scale datasets using a fraction of HNSW's RAM. 5,000+ QPS on a single node with SSD. **Filtered-DiskANN** (Gollapudi et al., WWW 2023) extends edge construction to respect label sets so metadata filters do not destroy recall -- a direct solution to the post-filter selectivity collapse problem.

### ScaNN (Google Research)

Optimized for high-throughput inner-product search (recommendation systems, Google-scale retrieval).

**Mechanism.** Uses **anisotropic quantization** -- a quantization-aware training procedure that preserves inner-product ordering better than standard PQ by assigning higher fidelity to dimensions that contribute more to the inner product. Two-stage pipeline: fast approximate scoring with compressed codes, then precise reranking with full-precision vectors.

### Algorithm Decision Matrix

| Algorithm | Best For | Memory | Recall | Scale | Incremental Inserts |
| --- | --- | --- | --- | --- | --- |
| **HNSW** | Best recall, in-memory | High (~1.5x overhead) | 98%+ | Millions | Yes |
| **IVF-Flat** | Static datasets, memory-conscious | Low | Good | 10M-100M | No (rebuild) |
| **IVF-PQ** | Billion-scale, RAM-constrained | Very low (~32-128x compression) | Good (3-5% loss) | Billions | No (rebuild) |
| **DiskANN** | Billion-scale, single-node SSD | Low (PQ in RAM only) | 95%+ | Billions | Limited |
| **ScaNN** | High-throughput inner product | Moderate | High | Millions-Billions | Limited |

### Filtering x ANN (Architect Invariant)

The interaction between metadata filters and ANN search is the single most underestimated production concern. The strategy varies critically by engine:

| Engine | Interaction | Failure If Ignored |
| --- | --- | --- |
| **pgvector** | Post-filter after ANN; `hnsw.iterative_scan` optional; `hnsw.max_scan_tuples` cap | 10% match rate x `ef_search=40` -> ~4 survivors -> under-filled top-k |
| **Qdrant** | Filterable HNSW + payload indexes; planner: weak -> HNSW, strict -> payload+rescore; ACORN for disconnect. Defaults: `m=16`, `ef_construct=100`, `full_scan_threshold=10000` | Soft-deletes / multi-filter strand navigation; create payload indexes **before** ingest or face HNSW rebuild |
| **Weaviate** | Pre-filter allow-list via per-shard inverted index; strategies: `acorn` (default v1.34+) vs `sweeping`; `flatSearchCutoff` default 40,000 | Restrictive filters fall back to flat scan |
| **Pinecone serverless** | Adaptive pre/mid/bypass via per-slab metadata bitmaps; operators `$eq/$in/$gt/.../$and/$or`; `$in`/`$nin` max 10,000 values | Naive filtered IVF recall cliffs under varying selectivity |

**Selectivity impact on performance:**

| Filter Selectivity | QPS | p99 Latency |
| --- | --- | --- |
| **0-1%** (rare tenant) | 180 | 220ms |
| **25-100%** (broad) | 2,400 | 38ms |

This **13x QPS gap** is why multi-tenant vector databases with small tenants require integrated filter traversal rather than post-filtering. It is also why Filtered-DiskANN builds label awareness directly into graph edges.

### Hybrid Search: BM25 + Vector + Reranking

Pure vector search underperforms hybrid on most production workloads. Embeddings treat "E-4521" and "E-4522" as nearly identical; BM25 treats them as completely different terms. This complementarity is the foundation of hybrid search.

**Three-stage production pipeline:**

```
Stage 1a: BM25 sparse retrieval (top-100 to top-1000)
                                                        +---> Reciprocal Rank Fusion
Stage 1b: Dense vector ANN retrieval (top-100 to top-1000)    (merges by RANK, not score;
                                                                k=60 default parameter)
                                                                        |
                                                                        v
                                                        Cross-Encoder Neural Reranker
                                                        (joint query+doc scoring;
                                                         adds 100-300ms latency;
                                                         15-25% Precision@5 lift)
                                                                        |
                                                                        v
                                                                Top-K Final Results
```

**Benchmark data:**
- **WANDS e-commerce:** tuned hybrid = 0.7497 NDCG, BM25-only = 0.6983, vector-only = 0.6953. Hybrid delivers **7.4% lift**.
- **Financial documents:** hybrid + reranking achieves Recall@5 of 0.816.
- **Precision@5 lift from cross-encoder reranking:** 15-25% over pure vector search alone.

**Common architectural mistake:** Using a single weighted formula to combine BM25 and cosine scores instead of rank-based fusion. BM25 scores and cosine similarity scores are drawn from different distributions; direct combination produces unstable results.

**RRF formula:** For document d across N ranked lists: `RRF(d) = SUM over lists L of: 1 / (k + rank_L(d))` where k=60 is the standard default.

**Hybrid search by vendor:**

| Database | Hybrid Search | Filter Integration |
| --- | --- | --- |
| **Weaviate** | Native (BM25 + vector, selectable fusion) | Integrated into traversal |
| **Qdrant** | Native (v1.10 Query API, `RrfQuery`/`Fusion.RRF`, `prefetch`) | Payload index, integrated |
| **Milvus** | Native | Partition-key filtering |
| **Pinecone** | Alpha-weighted single-index; client-side RRF; dense+sparse Vectors API | Metadata filtering |
| **pgvector** | Manual composition via Postgres FTS + app SQL fusion | SQL WHERE clauses (post-filter) |

### Key Invariants

1. Query and corpus **must** share embedding model, dimensionality, and metric. pgvector indexes are per-opclass (`vector_l2_ops` vs `vector_cosine_ops`).
2. Soft tenancy is only safe if **every** query injects the tenant predicate. Payload filter is NOT RBAC.
3. ANN + SQ/PQ intentionally trade recall -- Faiss HNSW R@1 at `efSearch=16` is **0.874**, not 1.0.

---

## Part 3 -- Token Economics & NFR Analysis

Vector indexes are **not token-metered**. Dollar cost is **embedding API spend + RAM/storage/QPS infrastructure**. Embedding token spend is an application concern (Module 06); formulas below keep it adjacent so architects can budget `$ per 1k queries`.

### Cost Per Million Vectors (Managed Cloud, 1536-dim)

| Scale | Pinecone Serverless | Weaviate Cloud | Qdrant Cloud | pgvector (RDS) |
| --- | --- | --- | --- | --- |
| **10M vectors** | ~$70/mo | ~$135/mo | ~$65/mo | ~$45/mo |
| **100M vectors** | $700+/mo | Varies (BQ helps) | Significantly less | <$100/mo (self-hosted) |

### Pricing Model Divergence

- **Pinecone (consumption-based).** Storage $0.30/GB/mo + read units $16/million + write units $4/million. Cheap when idle; query-heavy apps produce bill shock.
- **Qdrant Cloud (capacity-based).** Reserved RAM/CPU/disk, no per-query charge. High-QPS apps get cheaper per query as they scale. Self-hosted on a 16GB VPS: ~$30-50/mo for millions of vectors.
- **Weaviate Cloud (dimension-based).** ~$0.095 per million dimensions stored per month.

**Tipping point.** Above **60-80M queries/month**, self-hosted Qdrant or Weaviate on fixed-cost infrastructure undercuts Pinecone Serverless by **3-10x**. Real-world validation: a consumer AI startup migrated 50M vectors from Pinecone at $1,200/mo to self-hosted Qdrant on a 64GB Hetzner instance at $130/mo -- a **9.2x** reduction.

### Hidden Cost Multipliers (Actual Bills Average 2.5-4x Pricing Page Estimate)

| Hidden Cost | Impact |
| --- | --- |
| Egress fees | $0.08-0.09/GB on AWS |
| Index rebuild compute | $12-40 per 10M vectors |
| HNSW storage overhead | ~1.5x raw vector size for graph edges |
| Embedding generation | Can match or exceed the database bill itself |
| Re-embedding 50M docs | ~25 billion tokens (a real budget line item, ~$500-$2,500) |

### Quantization as Cost Lever

- **SQ8 (Scalar Quantization):** float32 -> int8, ~4x reduction.
- **Binary Quantization (BQ):** float32 -> 1-bit, ~32x reduction, minor recall loss. Weaviate BQ reduces 100M vectors from ~$1,459/mo to ~$45/mo on dimension-based billing.
- **Product Quantization (PQ):** splits vector into sub-vectors, quantizes each, ~32x compression with 3-5% recall loss.
- **Qdrant 1.5-bit/2-bit quantization:** up to 64x memory reduction.

### Cost-Per-1K-Queries Formula

**Assumptions:** 10M vectors, 1536-dim float32; OpenAI text-embedding-3-small ($0.02/1M tokens, ~6 tokens avg per query); Pinecone Serverless; self-hosted cross-encoder reranker.

```
Cost_per_1K (Pinecone Serverless) =
    $0.00012   (embedding: 1000 * 6 tokens * $0.02/1M)
  + $0.016     (Pinecone reads: 1000 * $16/1M read units)
  + $0.028     (storage: 10M * 6KB * 1.5x / 1GB * $0.30/mo / 1M queries)
  + $0.10      (reranker: amortized GPU)
  = ~$0.144 per 1K queries

Cost_per_1K (self-hosted Qdrant, $50/mo VPS) =
    $0.00012   (embedding, same)
  + $0.00      (search: no per-query charge)
  + $0.05      (VPS prorated)
  + $0.10      (reranker, same)
  = ~$0.150 per 1K queries (at 1M queries/mo)
```

At 1M queries/month, costs are comparable. Above ~3M queries/month, Pinecone's per-read charge dominates. At 10M queries/month: Pinecone ~$0.188/1K vs self-hosted ~$0.105/1K.

### Index Memory Cost Formulas

| Item | Value | Source |
| --- | --- | --- |
| float32 1536-d | **6,144 B ~ 6 KB**/vector | 1536 x 4 |
| SQ8 | **d bytes** vs Flat 4d (~4x smaller) | Faiss indexes |
| PQ codes | **ceil(M*nbits/8)+8 B**/vec | Faiss indexes |
| HNSW Flat | **4d + O(M)** link overhead | Faiss formula: `4d + x*M*2*4` |
| nmslib HNSW on SIFT1M | vectors 512 MB + graph ~796 MB; build 173s | Faiss wiki |

**Capacity planning example:** N=10M, d=1536, M=16 -> vectors alone ~ 61.4 GB; graph adds several GB more before replicas. Quantize or use IVF-PQ when this exceeds node RAM.

### Latency Benchmarks

**Faiss HNSW Flat on SIFT1M** (20 threads, batch-friendly; wiki warns single-thread ~5-12x slower, one-by-one another ~2-3x):

| efSearch | ms/query (index-only) | R@1 |
| --- | --- | --- |
| **16** | **0.011** | 0.8740 |
| 32 | 0.020 | 0.9492 |
| **64** | **0.033** | 0.9779 |
| 128 | 0.059 | 0.9887 |
| 256 | 0.104 | 0.9920 |

**Key comparison points:**
- HNSW+SQ at efSearch=64: **0.011 ms**, R@1 **0.9242** -- faster/cheaper at that recall point.
- IVFFlat nprobe=64: **0.141 ms**, R@1 **0.9470** -- HNSW reaches >0.9 R@1 at 0.020 ms.

**Explicit trade-off:** Raising efSearch from 16 -> 64 moves R@1 **0.874 -> 0.978** while index-only time goes **0.011 -> 0.033 ms** (3x). At managed e2e the same dial dominates p95/p99 more than the microbench suggests.

**Managed filtered search (Pinecone, ICML 2025, warm slabs, excludes client RTT):**

| Dataset | Scale | Metric | Mean Recall | Internal Latency |
| --- | --- | --- | --- | --- |
| YFCC (BigANN filter track) | 10M x 192-d | recall@10 | **0.989** | ~20 ms |
| Production customer | 35M | recall@100 | **0.986** | ~75 ms |

**Large-scale: 50M vectors, 768-dim, 99% recall (Tiger Data benchmark):**

| Metric | Qdrant | pgvector+pgvectorscale |
| --- | --- | --- |
| p50 | 30.75ms | 31.07ms |
| p95 | 36.73ms | 60.42ms |
| p99 | **38.71ms** | 74.60ms |
| QPS | 41.47 | **471.57** |

Qdrant wins on tail latency (48% better p99). pgvectorscale wins on throughput (11.4x higher QPS). Choose based on whether you optimize for worst-case user experience or aggregate throughput.

**7-system evaluation (arXiv:2608.12812, SIFT1M):**

| System | p50 Latency | p99/p50 Ratio | QPS | Notes |
| --- | --- | --- | --- | --- |
| **Qdrant** | 4.55ms | 1.85 | 216 | Best latency-throughput balance |
| **FAISS** | - | - | 866 | Throughput leader (library, not DB) |
| **Weaviate** | - | - | - | >99% out-of-box recall |
| **Milvus** | - | - | - | Best at high-dim (0.971 recall at 960D) |
| **pgvector** | Higher | 1.63 (tightest) | 154 | Most consistent tail behavior |

**Multi-process throughput (p99 < 100ms budget):**

| System | QPS |
| --- | --- |
| Weaviate | 8,290 |
| pgvector | 4,828 |
| Milvus | 4,725 |
| Qdrant | 1,737 |
| Redis | 1,642 |

**Vendor typical latency ranges:**

| Vendor | Typical Latency | Notes |
| --- | --- | --- |
| Pinecone (serverless) | 45-80ms | Consistent, zero-tuning |
| Pinecone (pod-based) | 20-40ms | Better latency, more ops |
| Qdrant (in-memory) | 15-30ms | Rust performance edge |
| Qdrant (memory-mapped) | 30-60ms | SSD-backed |
| Milvus | 25-50ms | Depends on index type |
| Weaviate | 30-70ms | Hybrid search adds 10-20ms |

**Critical caveat.** At 1M vectors with no filters, almost every system delivers sub-10ms p99 at 99% recall. Differences become material at **10M+ vectors with metadata filters**.

### Inferred End-to-End Latency Budget (managed dense retrieve with mandatory filter)

| Stage | p50 ms | p95 ms | p99 ms |
| --- | --- | --- | --- |
| Embed API RTT | 25 | 80 | 200 |
| Filter / bitmap | 2 | 8 | 25 |
| ANN (warm) | 5 | 25 | 75 |
| Hydrate payloads | 3 | 12 | 40 |
| **E2E sum** | **35** | **125** | **340** |

| Tier | Target | Mitigations |
| --- | --- | --- |
| **p50** | <=40 ms | Hot working set in RAM; pre-embedded queries; modest efSearch |
| **p95** | <=150 ms | Raise ef only for recall-critical tenants; payload indexes before ingest |
| **p99** | <=400 ms | Warm critical slabs; cap scan tuples; shed to lower ef or lexical under load |

### NFR Summary

| NFR | Production Target |
| --- | --- |
| Recall | 95-99% (trade-off with latency and cost) |
| p99 Latency | <50ms interactive RAG, <200ms with reranking |
| Throughput | 1,000-8,000+ QPS per node (system-dependent) |
| Availability | 99.9%+ (replication factor >= 2) |
| Durability | Sync WAL for zero data loss; async acceptable for re-derivable embeddings |
| Cost efficiency | Budget 2.5-4x pricing page; self-host above 60-80M queries/month |
| Filter performance | Integrated pre-filter for <1% selectivity tenants |
| Index rebuild time | Minutes (10M) to hours (100M+); plan for blue-green swap |

---

## Part 4 -- Distributed Resilience & Security

### Sharding Strategies

Two dominant architectural paradigms:

**Stateful (Qdrant, Weaviate, Vald).** Each worker owns and stores a shard's data + index. Simpler operationally. Rule of thumb for Qdrant: **1 shard per 5M vectors**.

**Stateless / Compute-Storage Separation (Milvus, Vespa).** Workers are stateless compute nodes. Data lives in object storage (S3). Index segments loaded into cache on demand. Enables independent scaling of compute and storage.

| Sharding Method | Characteristics |
| --- | --- |
| **Hash-based** | Deterministic routing by vector ID. Uniform distribution but no range queries. |
| **Consistent hashing** | Minimizes data movement on node add/remove. Virtual nodes smooth distribution. Standard for elastic clusters. |
| **Semantic-aware** | [Emerging] Uses IVF coarse centroids for top-level routing, HNSW within each shard. Queries route to semantically relevant shards only, reducing fan-out. |

**Query fan-out trade-off triangle.** Higher recall requires wider search (more shards probed), conflicting with low latency and low cost. Distributed vector search is fundamentally an optimization over this triangle.

### Replication and Consistency

**Leader-follower model:** Writes go to leader, propagated to followers (sync or async). Synchronous replication: strong consistency, slower writes. Asynchronous: faster writes, risk of stale reads.

**Raft consensus:** Used by both Qdrant and Milvus. Qdrant offers point-in-time consistency guarantees and ACID-compliant operations. Milvus provides tunable consistency (strong/bounded/eventual).

**Unique vector DB challenge -- index divergence.** Two replicas of the "same" HNSW graph can produce different neighbor sets for the same query because different insert order produces different graph topology. This is inherent to greedy graph construction, not a bug. Detection: replica-to-replica neighbor mismatch rate (any non-zero value is a problem).

### Index Rebuild and Compaction

- **HNSW rebuild** on large datasets saturates CPU for minutes to hours. Best practice: build on replica, then swap in (blue-green index deployment).
- **IVF/IVF-PQ** requires full k-means retraining. Periodic offline rebuild is standard.
- **Milvus 2.6** replaced Kafka/Pulsar dependency with Woodpecker (custom WAL on object storage), reducing operational complexity.
- **Rebuild storms:** Multiple background jobs (optimization, rebalancing, compaction) running concurrently cause cluster-wide CPU saturation and p99 spikes. Mitigation: stagger jobs, enforce concurrency limits.

### Durability and Write Visibility

| Product | Durability Mechanism | Write Visibility |
| --- | --- | --- |
| **Pinecone** | Write -> durable request log with LSN -> 200 OK; background index builder. Slabs immutable; deletes via tombstones + compaction. | Memtable for read-your-writes before slab flush. |
| **pgvector** | Inherits Postgres WAL, checkpoints, replication, PITR. Vector indexes rebuilt under normal vacuum. | Standard Postgres visibility rules. |
| **Qdrant** | Segment-oriented storage; payload indexes persisted; memory tiers pinned/cached/cold. | HNSW rebuild heavy -- nudge ef_construct by 1 to force optimizer reindex. |
| **Faiss** | Library, not DB. Explicit `write_index`/reload. No built-in replication. | Process death without snapshot = empty worker. |

### Database Architecture Comparison

| Aspect | Pinecone | Qdrant | Weaviate | Milvus |
| --- | --- | --- | --- | --- |
| **Architecture** | Serverless, compute-storage separated | Single binary or Docker, Rust | Docker Compose, Go | K8s-native, disaggregated nodes |
| **Sharding** | Automatic, hidden | Manual or auto, 1 shard/5M vectors | Automatic | Hash/range, proxy-managed |
| **Replication** | Managed | Raft-based, configurable RF | Built-in | Raft for consistency |
| **Consistency** | Eventual (managed) | Point-in-time, ACID | Eventual | Tunable (strong/bounded/eventual) |
| **Hybrid search** | Alpha-weighted single-index | Native (v1.10 Query API) | Native (BM25 + vector, RRF) | Native |
| **Filter integration** | Metadata filtering (adaptive) | Payload index, integrated | Integrated into traversal | Partition-key filtering |
| **Ops complexity** | None (managed) | Low (single binary) | Medium (Docker) | High (K8s required) |

### Multi-Tenancy Isolation

Multi-tenancy in vector databases carries a unique risk: if tenant isolation fails, another customer's **knowledge base** gets surfaced directly into AI responses -- a semantic-level data leak with no relational-DB equivalent.

**Isolation approaches ranked by strength:**

| Approach | Isolation Strength | Overhead | Notes |
| --- | --- | --- | --- |
| **Collection-per-tenant** | Strongest | Highest | Separate index per tenant. True resource isolation. |
| **Weaviate native multi-tenancy** | Strong | Moderate | Purpose-built tenant isolation with per-tenant data lifecycle management. |
| **Namespace/partition** | Moderate | Low | Pinecone namespaces, Milvus partitions. **WARNING:** Pinecone namespaces are NOT security boundaries. |
| **Metadata filtering (tenant_id)** | Weakest | Lowest | Single shared index. Misconfigured filter = cross-tenant leak. |

**Real-world example.** Qdrant `is_tenant=true` keyword index co-locates tenant points for sequential reads; optional shard keys for large tenants. But Qdrant JWT can scope which collection is readable -- it does **not** auto-bind a tenant claim into payload filters (GitHub issue #8015). The gateway must inject `tenant_id` server-side.

**pgvector warning:** Shared HNSW across tenants lets foreign vectors affect recall/speed. Prefer partitioning or partial indexes per tenant.

### Encryption and Access Control

| Capability | Pinecone | Weaviate | Qdrant | Milvus |
| --- | --- | --- | --- | --- |
| **Encryption at rest** | AES-256 | AES-256 | AES-256 | Configurable |
| **Encryption in transit** | TLS 1.3 | TLS 1.3 | TLS 1.3 | TLS |
| **Customer-managed keys** | Enterprise tier | Limited | Self-hosted only | Self-hosted only |
| **Field-level encryption** | No | No | No | No |

**Embedding inversion risk.** A zero-shot technique (arXiv:2504.00147, April 2025) achieved meaningful text recovery from embeddings without training on the target model. If an attacker can query your embedding store, they can reconstruct source text. **Every embedding exposure is a data breach.**

**Defense:** The SPARSE framework (arXiv:2602.07090, February 2026) injects dimension-sensitive Mahalanobis noise targeting semantically critical embedding dimensions while minimally affecting retrieval quality.

**RBAC maturity varies significantly:**
- **Weaviate:** Added RBAC in v1.29.0. Collection-level roles.
- **Milvus:** RBAC with partition-level granularity.
- **Qdrant:** API key auth; TLS; JWT RBAC (`access`: `r`/`m`/per-collection `r`/`rw`). Instances are **insecure by default**.
- **Chroma:** A 2025 UpGuard survey found 1,170 internet-accessible instances, ~1/3 exposing production data with no authentication.
- **Milvus CVE-2025-64513** (CVSS 9.3): single HTTP header with hardcoded constant bypassed all authentication.

**Best practices:** Separate roles (ingestion writer, index maintainer, read-only RAG service, security auditor). Short-lived credentials with rotation. Least privilege enforcement. **OWASP LLM08:2025** "Vector and Embedding Weaknesses" is now in the 2025 LLM Top 10.

### Zero-Trust MCP for Vector Operations

When vector databases are exposed as MCP tool servers (e.g., a `vector_search` tool callable by LLM agents), per-invocation authorization is critical. A compromised or misprompted agent can exfiltrate tenant data or corrupt the index.

**Per-invocation auth model:** Every MCP tool call carries auth context validated independently of session-level credentials. The orchestrator signs each invocation with caller identity (user ID, agent ID, session ID).

| Capability | Scoping Rule |
| --- | --- |
| **search** | Restricted to caller's tenant namespace. Max top_k=100 to prevent resource exhaustion. |
| **insert/upsert** | Write-scoped to caller's tenant. Payload schema validated. Rate-limited per caller. |
| **delete** | Requires elevated privilege. Soft-delete by default. Batch deletes capped and logged. |

**Transport security:** MCP SSE/HTTP must enforce mutual TLS between orchestrator and vector DB tool server. Embedding vectors are as sensitive as source text (embedding inversion risk).

### PII in Vector Stores

Embeddings encode semantic content of source text. PII embedded into vectors persists in a form that is not searchable by traditional DLP tools, not removable by standard field-level redaction, and potentially recoverable via embedding inversion attacks. GDPR right-to-erasure requires deleting the **vector**, not just the source document.

**Detection pipeline (pre-embedding):**

```
Source Text --> Regex Pass --> NER Pass --> Decision --> Embedding
                 |               |              |
                 | SSN, credit   | spaCy/       | ALLOW: no PII detected
                 | card, email,  | Presidio     | REDACT: replace PII spans
                 | phone         | entity       |   with [PERSON], [EMAIL]
                 |               | detection    | REJECT: block entirely
                 |               |              | SEPARATE: embed into
                 |               |              |   isolated PII namespace
```

**Handling strategies:**
1. **Redaction before embedding.** Replace PII spans with type tokens. Trade-off: slight retrieval quality degradation on PII-heavy queries.
2. **Encrypted PII namespace.** Separate collection with customer-managed keys. Enables per-user deletion without scanning entire index.
3. **SPARSE noise injection.** Apply to PII namespace vectors to degrade inversion attack fidelity while preserving retrieval quality.

### Immutable Audit Logs

Vector databases lack mature audit logging. A silent cross-tenant query or unauthorized bulk delete leaves no trace unless explicitly instrumented. Compliance: SOC2 CC7.2, GDPR Article 30, HIPAA 164.312(b).

**Append-only log schema:**

| Field | Description |
| --- | --- |
| timestamp | ISO-8601 with microsecond precision, UTC |
| operation | SEARCH, INSERT, UPSERT, DELETE, INDEX_REBUILD, CONFIG_CHANGE |
| actor_id | User ID, service account, or agent ID |
| tenant_id | Resolved from auth |
| collection | Target collection name |
| vector_count | Vectors affected |
| query_metadata | top_k, filters, model_version. **Embedding vectors excluded** (inversion risk). |
| latency_ms | Operation duration |
| status | SUCCESS, DENIED, ERROR |
| log_hash | SHA-256 hash chaining previous entry (tamper evidence) |

Write to separate storage from the vector DB (S3 with Object Lock, append-only PostgreSQL with RLS preventing deletes). Hash chaining provides tamper evidence without requiring a blockchain.

---

## Part 5 -- Production Failure Modes

Vector databases rarely fail with clean outages or loud exceptions. The system **drifts**: retrieval quality slips, tail latency stretches, agent responses become less trustworthy. Everything still appears "up" but behavior is no longer correct. Blast radius extends to RAG pipelines, agents, copilots, and any workflow depending on semantic retrieval.

| # | Failure Mode | Mechanism | Detection | Mitigation |
| --- | --- | --- | --- | --- |
| 1 | **Hot Shards** | Semantic clustering (support tickets, error logs, billing) causes non-uniform shard load. Single centroid receives >40% of queries. | Shard load skew ratio >3-5x; p95/p99 spikes isolated to one shard. | Semantic-aware sharding; centroid rebalancing; monitoring per-shard QPS. |
| 2 | **Centroid Collapse (IVF)** | Domain evolution causes centroids to misrepresent the space. One centroid absorbs 20-50x more vectors. | Teams continually increasing nprobe to maintain recall. Centroid population imbalance >20-50x. | Periodic offline k-means retraining; monitor centroid population distribution. |
| 3 | **Memory Saturation / Fragmentation** | Rising p99 with no QPS increase. NUMA misses, page faults, GC spikes. | p99/p50 ratio >3x with stable QPS. | Always oversize RAM for vector workloads. Memory pressure rarely produces explicit errors -- it corrupts performance slowly. |
| 4 | **Index Divergence Across Replicas** | Different insert order produces different HNSW topology. | Replica-to-replica neighbor mismatch rate (any non-zero value). Everything looks healthy until neighbors are compared cross-replica. | Replicate raw vectors + WAL; accept approximate result variance within recall guarantee. |
| 5 | **Rebuild Storms** | Concurrent background jobs (optimization, rebalancing, compaction) saturate cluster CPU. | Sudden cluster-wide p99 spikes. | Stagger jobs across non-overlapping windows; enforce concurrency limits. |
| 6 | **Embedding Drift (The Slow Poison)** | Embedding model changes; vectors before and after live in incompatible spaces. Same output dimension is especially dangerous -- no crash, just garbage. | KL divergence on distance distributions; recall degradation on fixed benchmark set. | Tag every vector with `model_version`; dual-write during migration; linear projection adapter recovers 95-99% quality. |
| 7 | **SSD Wear (PQ Hidden Cost)** | Product Quantization reduces RAM but increases SSD I/O. Write amplification causes device failure ahead of schedule. | Monitor TBW (terabytes written) as first-class metric. | Budget for accelerated device replacement. |
| 8 | **Routing Misfires** | Drifting centroids, stale routing metadata cause queries to hit wrong shards. | Almost never shows as an error. Manifests as quality degradation. | Monitor retrieval quality metrics end-to-end, not just infrastructure health. |
| 9 | **Recall/Latency Parameter Drift** | As dataset grows, engineers increase query-time parameters, silently accepting higher latency or degraded recall. | Continuous recall benchmarking against fixed ground-truth set. | The culmination of all other failure modes. Requires active monitoring, not passive. |

### Detection Thresholds

| Metric | Alert Threshold |
| --- | --- |
| Shard load skew ratio | >3-5x |
| Centroid query concentration | >40% to single centroid |
| Centroid population imbalance | >20-50x between centroids |
| Replica neighbor mismatch rate | Any non-zero value |
| p99/p50 latency ratio | >3x |
| KL divergence (embedding drift) | Rising trend over baseline |
| SSD TBW consumption rate | Exceeding device lifetime projection |

### Additional Failure Taxonomy

| Failure | Class | Symptom | Mitigation |
| --- | --- | --- | --- |
| Transient timeout / 429 / cold slab | Transient | p99 spike | Retry + jitter; warm hot namespaces |
| Stale index | Consistency | Fresh docs missing from ANN | Read memtable+slabs; wait for builder |
| Filter leakage / omission | Security (permanent) | Cross-tenant chunks | Gateway-mandatory tenant_id |
| Post-filter recall collapse | Permanent for that config | Under-filled / wrong top-k | Iterative scans, filterable HNSW |
| Graph disconnection (deletes/multi-filter) | Permanent until reindex | High latency, low recall | Payload edges + ACORN |
| Dim/metric mismatch | Permanent config bug | Garbage neighbors | Pin model+opclass at control plane |
| Poison upsert (bad dim / NaN) | Permanent for that ID | Query errors / skew | Validate at proxy; DLQ bad points |
| Faiss delete limitation | Operational | IndexHNSW does not support remove | Rebuild or external ID maps |

---

## Part 6 -- Architectural System Design Scenarios

### Vector DB Selection Decision Framework

```
START
  |
  v
Dataset size?
  |
  +-- <100K vectors --> pgvector (avoid adding infra)
  |
  +-- 100K-10M vectors --> Any option works; choose by ops preference
  |     +-- Want zero ops? --> Pinecone
  |     +-- Want hybrid search? --> Weaviate
  |     +-- Want lowest latency? --> Qdrant
  |     +-- Already on Postgres? --> pgvector
  |
  +-- 10M-1B vectors --> Narrows to:
  |     +-- Managed: Pinecone (cost compounds), Zilliz Cloud
  |     +-- Self-hosted: Qdrant, Weaviate, Milvus
  |     +-- Need distributed: Milvus, Vespa
  |
  +-- >1B vectors --> Milvus/Zilliz or Vespa
        (DiskANN index if single-node SSD viable)
```

**Secondary decision factors:**

| Factor | Best Pick |
| --- | --- |
| Zero operational work | Pinecone |
| Hybrid (keyword + vector) search | Weaviate |
| Lowest query latency, predictable cost | Qdrant |
| Billion-scale, GPU-accelerated | Milvus / Zilliz Cloud |
| Existing Postgres stack, <10M vectors | pgvector |
| Multi-tenant SaaS | Weaviate (strongest tenant isolation) |
| Prototyping / local dev | Chroma or LanceDB |

---

### Scenario 1 -- Multi-Tenant Product Search for 500M Embeddings

**Problem statement.** A B2B e-commerce platform serves 2,000 merchant tenants. Each tenant has 50K-5M product embeddings (500M total). Requirements: <50ms p99 latency, strict tenant isolation (no cross-tenant data leakage), hybrid search (keyword + semantic), cost target <$5,000/month.

**Architecture:**

```
+---------------------------------------------------------------------------------+
|                           API GATEWAY / AUTH                                     |
|  JWT validation --> extract tenant_id --> rate limiting per tenant               |
+------------------------------------+--------------------------------------------+
                                     |
+------------------------------------v--------------------------------------------+
|                          QUERY COORDINATOR                                       |
|                                                                                  |
|  +--------------------+  +---------------------+  +--------------------------+  |
|  | Tenant Router       |  | Embedding Service    |  | Query Classifier         |  |
|  | (tenant_id -->      |  | (Cohere embed-v4;   |  | (keyword-heavy --> BM25  |  |
|  |  shard set via      |  |  shared vector space |  |  weight boost;           |  |
|  |  consistent hash)   |  |  invariant enforced) |  |  semantic --> vector     |  |
|  +--------+------------+  +----------+----------+  |  weight boost)           |  |
|           |                          |              +-----------+--------------+  |
+-----------+--------------------------+------------------------------+------------+
            |                          |                              |
+-----------v--------------------------v------------------------------v------------+
|                        SEARCH CLUSTER (5 nodes)                                  |
|                                                                                  |
|  +-----------------+  +-----------------+        +-----------------+             |
|  | Node 1           |  | Node 2           |  ...  | Node 5           |             |
|  | Shards 1-20      |  | Shards 21-40     |       | Shards 81-100    |             |
|  | ~100M vectors    |  | ~100M vectors    |       | ~100M vectors    |             |
|  |                  |  |                  |       |                  |             |
|  | IVF-PQ index     |  | IVF-PQ index     |       | IVF-PQ index     |             |
|  | (48 sub-quant,   |  |                  |       |                  |             |
|  |  8-bit, ~48 bytes|  |                  |       |                  |             |
|  |  per vector)     |  |                  |       |                  |             |
|  |                  |  |                  |       |                  |             |
|  | BM25 shard       |  | BM25 shard       |       | BM25 shard       |             |
|  | (Tantivy)        |  | (Tantivy)        |       | (Tantivy)        |             |
|  |                  |  |                  |       |                  |             |
|  | Payload index    |  | Payload index    |       | Payload index    |             |
|  | (tenant_id,      |  | (tenant_id,      |       | (tenant_id,      |             |
|  |  category,       |  |  category,       |       |  category,       |             |
|  |  price_range)    |  |  price_range)    |       |  price_range)    |             |
|  +-----------------+  +-----------------+        +-----------------+             |
|                                                                                  |
|  Replication factor: 2 (Raft consensus for writes)                              |
|  Shard count: 100 (1 shard per ~5M vectors)                                    |
+--------------------------------------+-------------------------------------------+
                                       |
+--------------------------------------v-------------------------------------------+
|                         RESULT PIPELINE                                          |
|  Shard results --> RRF (k=60) --> Cross-encoder rerank (top-20 --> top-5)       |
|                                    (budget: 15ms rerank on 20 candidates)        |
+-----------------------------------------------------------------------------------+
```

**Trade-off matrix:**

| Decision | Option A | Option B | Chosen |
| --- | --- | --- | --- |
| Index type | HNSW: 98%+ recall, ~900 GB RAM for 500M | IVF-PQ: 95% recall, ~24 GB RAM (48 sub-q) | **IVF-PQ** (cost) |
| Tenant isolation | Collection-per-tenant: strongest, 2,000 indexes = overhead | Metadata filter on tenant_id: shared index, filter correctness critical | **Metadata filter + integrated traversal** |
| Hosting | Managed (Pinecone): zero ops, $700+/mo at this scale | Self-hosted (Qdrant): ~$130/mo per node x 5 | **Self-hosted** (3-10x cheaper) |
| Hybrid search | Manual BM25 + vector: two systems | Native hybrid (Qdrant v1.10 Query API): single system | **Native hybrid** |
| Consistency | Strong (Raft sync): higher write latency | Eventual (async): faster writes, brief stale reads | **Eventual** (product search is not ACID) |

**Latency budget breakdown:**

| Stage | Budget |
| --- | --- |
| Network (API -> cluster) | 5ms |
| Fan-out + ANN search | 25ms |
| RRF merge | 2ms |
| Cross-encoder rerank | 15ms |
| Response serialization | 3ms |
| **TOTAL** | **50ms p99** |

**Decision rationale.** IVF-PQ over HNSW because 500M vectors at full-precision HNSW would require ~900 GB RAM (600 GB vectors + 1.5x graph overhead), making the $5K/mo budget unattainable. IVF-PQ at 48 sub-quantizers compresses to ~24 GB across the cluster. The 3-5% recall loss is offset by the cross-encoder reranker on the final shortlist -- achieving effective recall above 97% on final top-5.

Metadata filtering with integrated traversal over collection-per-tenant because 2,000 separate collections would create 2,000x operational surface area. Cross-tenant leakage risk is mitigated by enforcing tenant_id injection at the API gateway -- the search service never accepts a raw tenant_id from the client; it extracts it from the JWT claim.

---

### Scenario 2 -- Embedding Model Migration Without Downtime for a RAG Platform

**Problem statement.** A SaaS RAG platform has 50M document chunks embedded with Cohere embed-v3 (1024-dim). The team needs to migrate to embed-v4 (also 1024-dim -- same dimension, different space) for 12% retrieval quality improvement. Requirements: zero downtime, no quality degradation during migration, re-embedding budget of 25 billion tokens, 2-week migration window.

**Architecture:**

```
+---------------------------------------------------------------------------------+
|                           MIGRATION ORCHESTRATOR                                 |
|                                                                                  |
|  Phase tracker: DUAL_WRITE --> RE_EMBED --> VALIDATE --> CUTOVER --> CLEANUP     |
|  Progress: 0/50M chunks re-embedded | model_version: v3 (old), v4 (new)         |
+---------------------------------+------------------------------------------------+
                                  |
               +------------------+-------------------+
               |                  |                    |
+--------------v------+  +-------v--------+  +--------v----------------------------+
| INGESTION PATH      |  | RE-EMBED WORKER |  | QUERY PATH                         |
|                     |  |                 |  |                                    |
| New documents -->   |  | Background job: |  | Query arrives --> embed with v4    |
| Dual-write:         |  | Read chunks     |  |                                    |
|  1. Embed with v4   |  | from source     |  | +-------------------------------+  |
|  2. Store with      |  | --> embed v4    |  | | Version-Aware Router          |  |
|     model_ver=v4    |  | --> upsert with |  | |                               |  |
|  3. Keep old v3     |  | model_ver=v4    |  | | If chunk has v4: use directly  |  |
|     vector          |  | --> mark done   |  | | If chunk has v3 only:          |  |
|                     |  |                 |  | |   apply linear projection      |  |
|                     |  | Rate: ~3.5M     |  | |   W (v3 --> v4 space)          |  |
|                     |  | chunks/day      |  | |   recovers 95-99% quality      |  |
|                     |  | (25B tokens     |  | | Merge results via RRF          |  |
|                     |  |  / 14 days)     |  | +-------------------------------+  |
|                     |  |                 |  |                                    |
|                     |  | Checkpoint:     |  | +-------------------------------+  |
|                     |  | resume from     |  | | Drift Detector                |  |
|                     |  | last committed  |  | | KL divergence on distance     |  |
|                     |  | batch on crash  |  | | distributions, per-version    |  |
|                     |  |                 |  | | Alert if shift > threshold    |  |
+---------------------+  +-----------------+  | +-------------------------------+  |
                                              +------------------------------------+
                                                           |
+----------------------------------------------------------v-----------------------+
|                         VALIDATION GATE                                           |
|                                                                                   |
|  Fixed benchmark set (1,000 queries with known-good results)                      |
|  Automated recall comparison: v4-only recall >= v3 baseline recall                |
|  If PASS: proceed to cutover (drop v3 vectors, remove projection adapter)        |
|  If FAIL: halt, investigate, increase sample for projection training             |
+-----------------------------------------------------------------------------------+
```

**Linear projection math.** The adapter is a learned linear transformation W from v3 space to v4 space, trained on ~100K paired embeddings using ordinary least squares:

```
W = argmin ||X_new - X_old @ W||^2
W = (X_old^T X_old)^-1 X_old^T X_new
```

This recovers 95-99% of retrieval performance on un-migrated vectors. Validation: median cosine similarity between projected old and actual new embeddings should exceed 0.90.

**Trade-off matrix:**

| Decision | Option A | Option B | Chosen |
| --- | --- | --- | --- |
| Migration approach | Big-bang: re-embed all 50M before cutover. Simple but 2-week degraded quality. | Gradual: dual-write + background re-embed + linear projection during gap. | **Gradual** (zero downtime) |
| Cross-version query | Ignore old vectors during transition (recall drops proportional to un-migrated fraction) | Linear projection adapter: 95-99% recovery, ~0.5ms compute | **Projection adapter** |
| Re-embedding budget | All at once: spike cost, API rate limits | Rate-limited: 3.5M/day, steady cost | **Rate-limited** |
| Validation | Spot-check: manual review | Automated benchmark: 1,000 queries, recall comparison, pass/fail gate | **Automated benchmark** |
| Rollback | Forward-only | Keep v3 vectors until validation passes; single config flag to revert routing | **Keep v3 until gate passes** |

**Critical insight.** Both models output 1024-dimensional vectors -- there is no dimension mismatch to cause a crash. Without the `model_version` tag on every vector, the system would silently mix embeddings from incompatible spaces. The `model_version` metadata field is the **single most important safeguard**. The re-embedding cost (~$500-$2,500 for 25B tokens) is a real budget line item that must be approved before migration begins.

---

### Scenario 3 -- Postgres-Native Product Search (<100k Vectors)

**Problem.** B2B SaaS catalog search over ~80k product embeddings (1536-d), moderate QPS (~50), must join ANN results to Postgres inventory/price tables in one transaction, selective filters on `org_id` + category, target p95 <=150 ms, team already runs Postgres HA.

**Technology choices:** pgvector HNSW (`m=16`, tune `hnsw.ef_search`), B-tree / partial HNSW on `org_id`, enable iterative scans for selective filters, `halfvec` if RAM-bound; gateway injects `org_id`.

**Trade-off matrix:**

| Approach | Cost | Latency | Security | Scalability |
| --- | --- | --- | --- | --- |
| **A. Exact scan (no ANN)** | Low | Poor as N grows | Joins+RLS natural | Hits O(N) wall |
| **B. pgvector HNSW + iterative/partial** | Low (shared Postgres) | Good warm; post-filter risk mitigated | RLS + mandatory org_id | Fits <100k-low millions |
| **C. Sidecar Faiss HNSW** | Medium (extra fleet) | Best batch ANN (0.011-0.033ms index-only) | DIY tenant filter | Strong ANN; weak SQL join |

**Decision:** Choose **B**. Transactional joins and PITR outweigh Faiss's microbench wins. Accept post-filter semantics with iterative scans / partial indexes. Escalate to dedicated engine when N and QPS leave Postgres comfort zone.

---

## Production Enterprise Code

### Resilient Vector DB Client with Circuit Breaker, Retries, Drift Detection, and Fallback Chain

```python
"""
Production vector database client with full resilience chain:
- Exponential backoff + jitter retries (prevents thundering herd)
- Circuit breaker: closed -> open -> half-open (prevents cascading failures)
- Structured JSON logging with correlation IDs
- Fallback: ANN -> exact on shortlist -> lexical (graceful degradation)
- Embedding drift detection via rolling distance distribution monitoring
- Embedding model migration with version tagging and linear projection adapter
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Sequence

import numpy as np


# ---------------------------------------------------------------------------
# Structured JSON Logger
# ---------------------------------------------------------------------------

class StructuredLogger:
    """Emits one JSON object per log line -- machine-parseable, grep-friendly."""

    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def _emit(self, level: str, event: str, **kwargs):
        record = {"ts": time.time(), "level": level, "event": event, **kwargs}
        self.logger.info(json.dumps(record, default=str))

    def info(self, event: str, **kwargs):
        self._emit("INFO", event, **kwargs)

    def warn(self, event: str, **kwargs):
        self._emit("WARN", event, **kwargs)

    def error(self, event: str, **kwargs):
        self._emit("ERROR", event, **kwargs)


log = StructuredLogger("vecdb")


# ---------------------------------------------------------------------------
# Circuit Breaker: closed -> open -> half-open
# ---------------------------------------------------------------------------

class CircuitState(Enum):
    CLOSED = "closed"        # Normal operation
    OPEN = "open"            # Failing -- reject all calls, wait for cooldown
    HALF_OPEN = "half_open"  # Allow single probe call


@dataclass
class CircuitBreaker:
    """
    Trips after failure_threshold failures within window_seconds.
    Stays open for recovery_timeout, then allows one probe (half-open).
    On probe success, resets to closed.

    Diagram:
      successes                         cooldown elapsed
          |                                    |
          v                                    v
     +----------+  failures >= N    +----------+ probe OK +-----------+
     | CLOSED   | ----------------> |  OPEN    | -------> | HALF_OPEN |
     +----------+                   +----------+          +-----+-----+
          ^                           ^  fail                   |
          |                           +-------------------------+
          +-------- probe success ------------------------------|
    """
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    window_seconds: float = 60.0

    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    failure_timestamps: deque = field(default_factory=deque, init=False)
    last_failure_time: float = field(default=0.0, init=False)

    def _prune_old(self):
        cutoff = time.time() - self.window_seconds
        while self.failure_timestamps and self.failure_timestamps[0] < cutoff:
            self.failure_timestamps.popleft()

    def record_failure(self):
        now = time.time()
        self.failure_timestamps.append(now)
        self.last_failure_time = now
        self._prune_old()
        if len(self.failure_timestamps) >= self.failure_threshold:
            self.state = CircuitState.OPEN
            log.warn("circuit_breaker_opened",
                     failures=len(self.failure_timestamps))

    def record_success(self):
        self.failure_timestamps.clear()
        if self.state != CircuitState.CLOSED:
            log.info("circuit_breaker_closed")
        self.state = CircuitState.CLOSED

    def allow_request(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                log.info("circuit_breaker_half_open")
                return True
            return False
        return False  # HALF_OPEN: one probe already in flight


# ---------------------------------------------------------------------------
# Retry with Exponential Backoff + Full Jitter
# ---------------------------------------------------------------------------

def retry_with_backoff(
    func: Callable[[], Any],
    *,
    max_retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    retryable_exceptions: tuple = (ConnectionError, TimeoutError),
) -> Any:
    """
    Full jitter: uniform [0, min(cap, base * 2^attempt)].
    Prevents thundering-herd on shared vector DB clusters.
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            result = func()
            if attempt > 0:
                log.info("retry_succeeded", attempt=attempt)
            return result
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt == max_retries:
                log.error("retries_exhausted", max_retries=max_retries,
                          error=str(exc))
                raise
            delay = random.uniform(0, min(max_delay, base_delay * (2 ** attempt)))
            log.warn("retry_backoff", attempt=attempt + 1,
                     delay_s=round(delay, 3), error=str(exc))
            time.sleep(delay)
    raise last_exc


# ---------------------------------------------------------------------------
# Embedding Drift Detector
# ---------------------------------------------------------------------------

class EmbeddingDriftDetector:
    """
    Monitors cosine distance distributions for shifts indicating
    the embedding model has changed (the 'slow poison' failure mode).
    Uses rolling window of median distances.
    """
    def __init__(self, window_size: int = 1000, alert_threshold: float = 0.15):
        self.window_size = window_size
        self.alert_threshold = alert_threshold
        self.baseline_median: Optional[float] = None
        self.recent_distances: deque = deque(maxlen=window_size)

    def record_distance(self, distance: float):
        self.recent_distances.append(distance)

    def calibrate_baseline(self):
        if len(self.recent_distances) >= self.window_size // 2:
            self.baseline_median = float(np.median(list(self.recent_distances)))
            log.info("drift_baseline_calibrated",
                     median=round(self.baseline_median, 4),
                     samples=len(self.recent_distances))

    def check_drift(self) -> bool:
        if self.baseline_median is None or len(self.recent_distances) < 100:
            return False
        current_median = float(np.median(list(self.recent_distances)))
        shift = abs(current_median - self.baseline_median)
        if shift > self.alert_threshold:
            log.warn("embedding_drift_detected",
                     baseline=round(self.baseline_median, 4),
                     current=round(current_median, 4),
                     shift=round(shift, 4))
            return True
        return False


# ---------------------------------------------------------------------------
# LRU Result Cache for Graceful Degradation
# ---------------------------------------------------------------------------

class ResultCache:
    """Bounded cache keyed by query embedding hash. Fallback when DB is down."""
    def __init__(self, max_size: int = 10_000):
        self.max_size = max_size
        self._cache: dict[str, list[dict]] = {}
        self._access_order: deque = deque()

    @staticmethod
    def _hash_vector(vector: list[float]) -> str:
        raw = np.array(vector, dtype=np.float32).tobytes()
        return hashlib.sha256(raw).hexdigest()[:16]

    def get(self, vector: list[float]) -> Optional[list[dict]]:
        return self._cache.get(self._hash_vector(vector))

    def put(self, vector: list[float], results: list[dict]):
        key = self._hash_vector(vector)
        if key not in self._cache:
            if len(self._cache) >= self.max_size:
                evict_key = self._access_order.popleft()
                self._cache.pop(evict_key, None)
            self._access_order.append(key)
        self._cache[key] = results


# ---------------------------------------------------------------------------
# Embedding Migration with Linear Projection Adapter
# ---------------------------------------------------------------------------

class EmbeddingMigrator:
    """
    Orchestrates embedding model migration:
      1. Tag every vector with model_version
      2. Dual-write with new model during transition
      3. Background re-embed historical vectors
      4. Query-time linear projection adapter for cross-version queries
      5. Drop old vectors after full re-embed + validation
    """

    def __init__(self, old_version: str, new_version: str):
        self.old_version = old_version
        self.new_version = new_version
        self.projection: Optional[np.ndarray] = None

    def learn_projection(
        self,
        old_embeddings: np.ndarray,  # (N, D)
        new_embeddings: np.ndarray,  # (N, D)
    ) -> np.ndarray:
        """
        Learn linear projection: old space -> new space.
        Uses OLS: W = (X_old^T X_old)^-1 X_old^T X_new
        Recovers 95-99% retrieval performance during migration.
        """
        W, _, _, _ = np.linalg.lstsq(old_embeddings, new_embeddings, rcond=None)
        self.projection = W

        # Validate: cosine similarity between projected and actual
        projected = old_embeddings @ W
        cosine_sims = np.array([
            np.dot(p, n) / (np.linalg.norm(p) * np.linalg.norm(n))
            for p, n in zip(projected, new_embeddings)
        ])
        median_sim = float(np.median(cosine_sims))
        log.info("projection_learned", samples=old_embeddings.shape[0],
                 median_cosine_similarity=round(median_sim, 4))
        if median_sim < 0.90:
            log.warn("projection_quality_low", median_sim=round(median_sim, 4),
                     recommendation="Increase sample size or use nonlinear adapter")
        return W

    def adapt_query_vector(self, query_vector: np.ndarray) -> np.ndarray:
        """Project new-model query into old space for cross-version search."""
        if self.projection is None:
            raise ValueError("Call learn_projection() first")
        W_inv = np.linalg.pinv(self.projection)
        return query_vector @ W_inv


# ---------------------------------------------------------------------------
# Hybrid Search: RRF + Cross-Encoder Reranking
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    doc_id: str
    text: str
    score: float = 0.0


def reciprocal_rank_fusion(
    result_lists: list[list[SearchResult]],
    k: int = 60,
) -> list[SearchResult]:
    """
    Merges N ranked lists by rank position, not score.
    RRF(d) = SUM over lists L of: 1 / (k + rank_L(d))
    k=60 is the standard default (Cormack et al., 2009).
    """
    fused_scores: dict[str, float] = {}
    doc_map: dict[str, SearchResult] = {}
    for result_list in result_lists:
        for rank, result in enumerate(result_list, start=1):
            fused_scores[result.doc_id] = (
                fused_scores.get(result.doc_id, 0.0) + 1.0 / (k + rank)
            )
            doc_map[result.doc_id] = result
    ranked = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
    return [
        SearchResult(doc_id=doc_id, text=doc_map[doc_id].text, score=score)
        for doc_id, score in ranked
    ]


def cross_encoder_rerank(
    query: str,
    candidates: list[SearchResult],
    top_k: int = 5,
    model_name: str = "cross-encoder/ms-marco-MiniLM-L-12-v2",
) -> list[SearchResult]:
    """
    Neural reranking: sees (query, document) pairs jointly.
    Adds 100-300ms but lifts Precision@5 by 15-25%.
    """
    from sentence_transformers import CrossEncoder
    reranker = CrossEncoder(model_name)
    pairs = [(query, c.text) for c in candidates]
    scores = reranker.predict(pairs)
    for candidate, score in zip(candidates, scores):
        candidate.score = float(score)
    reranked = sorted(candidates, key=lambda x: x.score, reverse=True)
    return reranked[:top_k]


def hybrid_search(
    query: str,
    query_vector: list[float],
    bm25_search_fn,       # callable(query, top_k) -> list[SearchResult]
    vector_search_fn,     # callable(vector, top_k) -> list[SearchResult]
    first_stage_k: int = 100,
    final_k: int = 5,
    use_reranker: bool = True,
) -> list[SearchResult]:
    """
    Full pipeline: BM25 sparse (top-100) + dense vector (top-100)
    -> RRF fusion (k=60) -> optional cross-encoder rerank.
    """
    bm25_results = bm25_search_fn(query, first_stage_k)
    vector_results = vector_search_fn(query_vector, first_stage_k)
    fused = reciprocal_rank_fusion([bm25_results, vector_results], k=60)
    if use_reranker:
        return cross_encoder_rerank(query, fused[:50], top_k=final_k)
    return fused[:final_k]


# ---------------------------------------------------------------------------
# Deterministic Filtered Brute-Force Index (for teaching / testing)
# ---------------------------------------------------------------------------

def fake_embed(text: str, dim: int = 16) -> list[float]:
    """Deterministic bag-of-tokens embedding. No network, no keys."""
    vec = [0.0] * dim
    for tok in text.lower().split():
        h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
        vec[(h // dim) % dim] += 0.37
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@dataclass(frozen=True)
class Point:
    point_id: str
    tenant_id: str
    text: str
    tags: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Hit:
    point_id: str
    score: float
    text: str
    path: str  # ann_bruteforce | exact_shortlist | lexical


class FilteredBruteForceIndex:
    """
    In-memory index: BRUTE-FORCE EXACT over points passing the metadata
    filter (filter-first, then full scan). Not HNSW -- labeled for clarity.
    Demonstrates the fallback chain: ANN -> exact shortlist -> lexical.
    """

    def __init__(self, points: list[Point], dim: int = 16):
        self.dim = dim
        self.points = list(points)
        self.vectors = {p.point_id: fake_embed(p.text, dim=dim) for p in points}
        self._fail_remaining = 0

    def inject_failures(self, n: int):
        """Simulate transient ANN dependency failures (shard timeout)."""
        self._fail_remaining = n

    def _filter(self, tenant_id: str, tag: Optional[str]) -> list[Point]:
        return [p for p in self.points
                if p.tenant_id == tenant_id
                and (tag is None or tag in p.tags)]

    def ann_search(self, query_vec: Sequence[float], tenant_id: str,
                   tag: Optional[str], top_k: int) -> list[Hit]:
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise ConnectionError("ann_shard_timeout")
        candidates = self._filter(tenant_id, tag)
        scored = [(cosine(query_vec, self.vectors[p.point_id]), p)
                  for p in candidates]
        scored.sort(key=lambda t: t[0], reverse=True)
        return [Hit(p.point_id, s, p.text, "ann_bruteforce")
                for s, p in scored[:top_k]]

    def exact_on_shortlist(self, query_vec: Sequence[float],
                           ids: Sequence[str], tenant_id: str,
                           top_k: int) -> list[Hit]:
        by_id = {p.point_id: p for p in self.points}
        scored = []
        for pid in ids:
            p = by_id.get(pid)
            if p and p.tenant_id == tenant_id:
                scored.append((cosine(query_vec, self.vectors[pid]), p))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [Hit(p.point_id, s, p.text, "exact_shortlist")
                for s, p in scored[:top_k]]

    def lexical(self, query: str, tenant_id: str, tag: Optional[str],
                top_k: int) -> list[Hit]:
        q = set(query.lower().split())
        scored = []
        for p in self._filter(tenant_id, tag):
            overlap = len(q & set(p.text.lower().split()))
            if overlap:
                scored.append((float(overlap), p))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [Hit(p.point_id, s, p.text, "lexical")
                for s, p in scored[:top_k]]


# ---------------------------------------------------------------------------
# Demo: full fallback chain
# ---------------------------------------------------------------------------

def main():
    corpus = [
        Point("a1", "tenantA", "refund policy for annual subscriptions",
              frozenset({"billing"})),
        Point("a2", "tenantA", "how to reset password and mfa tokens",
              frozenset({"auth"})),
        Point("a3", "tenantA", "vector index hnsw recall and ef search tuning",
              frozenset({"eng"})),
        Point("b1", "tenantB", "refund policy secret cross tenant leak bait",
              frozenset({"billing"})),
    ]
    idx = FilteredBruteForceIndex(corpus, dim=16)
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=0.02)

    # Happy path: tenant-scoped ANN
    qv = fake_embed("annual subscription refund", dim=16)
    hits = idx.ann_search(qv, "tenantA", "billing", top_k=2)
    assert hits[0].point_id == "a1"
    assert all(h.point_id.startswith("a") for h in hits)
    print(f"Happy path: {[h.point_id for h in hits]}")

    # Trip breaker -> fallback to lexical
    idx.inject_failures(20)
    for i in range(3):
        try:
            retry_with_backoff(
                lambda: idx.ann_search(qv, "tenantA", None, 1),
                max_retries=1, base_delay=0.001,
                retryable_exceptions=(ConnectionError,))
        except ConnectionError:
            breaker.record_failure()

    assert breaker.state == CircuitState.OPEN
    lexical = idx.lexical("refund policy", "tenantA", "billing", top_k=1)
    assert lexical[0].point_id == "a1"
    print(f"Degraded (lexical): {[h.point_id for h in lexical]}")
    print("All assertions passed.")


if __name__ == "__main__":
    main()
```

---

## Common Failure Modes Table

| Failure Mode | Blast Radius | Detection Signal | Time to Detect | Recovery |
| --- | --- | --- | --- | --- |
| Post-filter recall collapse | Single query pattern | Under-filled top-k; recall drop on selective filters | Minutes (if monitored) | Config change: iterative scans / pre-filter |
| Graph disconnection | Filtered queries | High latency + low recall with multi-filter | Hours | ACORN rebuild; payload edges before ingest |
| Embedding drift | All queries | KL divergence rising; recall on benchmark degrading | Days-weeks | model_version tag + re-embed + projection adapter |
| Cross-tenant data leak | Security incident | None unless audited | Indefinite | Gateway-mandatory tenant_id injection |
| Index rebuild storm | Cluster-wide | Sudden p99 spike across all shards | Minutes | Stagger jobs; concurrency limits |
| Hot shards | Subset of queries | Skew ratio >3-5x; isolated p99 spikes | Hours | Rebalance; semantic-aware sharding |
| Centroid collapse (IVF) | All IVF queries | nprobe continuously increasing; population imbalance >20-50x | Weeks | Offline k-means retraining |
| SSD wear | Storage tier | TBW exceeding lifetime projection | Months | Proactive device replacement |
| CVE-2025-64513 Milvus auth bypass | Full cluster | CVSS 9.3; single HTTP header bypass | N/A (patch) | Upgrade immediately |

---

## Key Takeaways for Interviews

1. **HNSW is the dominant production ANN** with O(log N) search, but its recall is tuned by `efSearch` -- at efSearch=16, Faiss SIFT1M R@1 is only 0.874; at efSearch=64 it reaches 0.978. Know the exact numbers.

2. **Pre-filter vs post-filter is the architect's invariant.** pgvector post-filters (under-fills top-k at <1% selectivity). Qdrant/Weaviate integrate filters into traversal. Pinecone adapts per-slab. This 13x QPS gap (180 vs 2,400) at different selectivities is the exam question.

3. **Hybrid search (BM25 + dense + RRF) beats either alone.** WANDS benchmark: hybrid 0.7497 NDCG vs BM25 0.6983 vs vector 0.6953. The common mistake is score-combining instead of rank-fusing.

4. **Cost tipping point at 60-80M queries/month.** Below: managed is comparable. Above: self-hosted is 3-10x cheaper. Real example: $1,200/mo Pinecone -> $130/mo self-hosted Qdrant (9.2x).

5. **Embedding drift is the slow poison.** Same-dimension model changes are the most dangerous because nothing crashes -- the system silently returns garbage. The only defense is `model_version` tags + KL divergence monitoring + linear projection adapter during migration.

6. **Multi-tenancy filter omission is a security bug, not a tuning issue.** JWT collection RBAC (Qdrant) scopes which collection, not which tenant within a shared index. The gateway must inject tenant_id server-side.

7. **Vector databases fail silently.** The overarching pattern: quality drifts, latency stretches, but nothing crashes. Monitor recall against fixed benchmarks, not just infrastructure health.

8. **IVF-PQ for billion-scale on a budget.** 500M vectors at 48 sub-quantizers = ~24 GB RAM vs ~900 GB for full HNSW. The 3-5% recall loss is recovered by cross-encoder reranking on the final shortlist.

---

## Interview Q&A

**Q1: Walk me through HNSW search, step by step. What makes it sub-linear?**

A: "HNSW builds a multi-layer graph inspired by skip lists. Top layers are sparse -- they let you make long-range jumps. Bottom layers are dense. Search starts at the top layer's entry point. At each layer, I greedily navigate to the node closest to my query vector. Then I descend one layer and repeat. At layer 0, I expand to an ef_search-sized candidate list, evaluating neighbors of neighbors. The key insight is that each layer's expected hops are bounded by a constant under Delaunay assumptions, so total search is O(log N). In practice on Faiss SIFT1M, this gives 0.033ms per query at 97.8% recall (efSearch=64). The tuning knob is ef_search -- higher means better recall but more work."

**Q2: Why does post-filtering collapse recall, and how do different engines solve it?**

A: "Post-filter runs ANN first, collects k*N candidates, then discards those failing the metadata predicate. If only 1% of vectors match the filter and ef_search=40, you get ~4 survivors on average -- your top-k is under-filled or wrong. pgvector uses this approach by default, with iterative scans as a mitigation. Qdrant solves it with filterable HNSW -- it adds payload-specific edges to the graph so filtered traversal stays navigable, plus ACORN for handling graph disconnection from deletes. Weaviate pre-filters using an inverted allow-list before HNSW admits IDs. Pinecone uses adaptive per-slab metadata bitmaps. The QPS impact is dramatic: at 0-1% selectivity you get 180 QPS with 220ms p99; at 25-100% selectivity you get 2,400 QPS with 38ms p99 -- a 13x gap."

**Q3: Explain the cost economics. When should I self-host vs use managed?**

A: "Pinecone is consumption-based: $0.30/GB storage plus $16 per million reads. Cheap when idle, expensive under load. Qdrant Cloud is capacity-based: fixed RAM/CPU, no per-query charge. The tipping point is around 60-80M queries per month. Below that, managed is comparable or cheaper. Above that, self-hosted undercuts by 3-10x. A real example: a startup migrated 50M vectors from Pinecone at $1,200/month to self-hosted Qdrant on a 64GB Hetzner box at $130/month -- 9.2x savings. But budget 2.5-4x the pricing page because of hidden costs: egress fees, index rebuild compute, HNSW overhead, and especially embedding generation -- re-embedding 50M docs costs 25 billion tokens, which is $500-$2,500 just for the embeddings."

**Q4: Design a vector search system for 500M product embeddings with <50ms p99 and multi-tenant isolation.**

A: "At 500M scale, full HNSW requires ~900 GB RAM -- budget-breaking. I'd use IVF-PQ with 48 sub-quantizers at 8 bits each, compressing to ~24 GB across a 5-node cluster with 100 shards. The 3-5% recall loss is recovered by a cross-encoder reranker on the final shortlist of 20 candidates. For tenancy, I'd use metadata filtering with integrated traversal rather than collection-per-tenant, because 2,000 separate collections create unmanageable ops overhead. The critical safety measure is injecting tenant_id at the API gateway from the JWT claim -- never accepting it from the client. Latency budget: 5ms network + 25ms ANN + 2ms RRF + 15ms rerank + 3ms serialization = 50ms p99."

**Q5: How would you migrate embedding models without downtime?**

A: "This is dangerous because same-dimension models produce vectors in incompatible spaces with no crash signal. Step 1: tag every vector with model_version metadata. Step 2: new documents get dual-written with the new model. Step 3: background re-embed job at ~3.5M chunks/day over 14 days. Step 4: during transition, a query-time linear projection adapter maps old vectors into new space, recovering 95-99% retrieval quality. The projection is learned via OLS on ~100K paired embeddings. Step 5: after re-embedding completes, run a validation gate -- 1,000 benchmark queries, compare recall v4-only vs v3 baseline. Only if it passes, drop old vectors and remove the adapter. The re-embedding cost is ~$500-$2,500 for 25B tokens, a real budget line item."

**Q6: What is the most dangerous failure mode of vector databases?**

A: "The most dangerous pattern is that vector databases fail silently. Unlike relational databases that give you constraint violations or deadlock errors, vector DBs drift: retrieval quality slips, tail latency stretches, agent responses become less trustworthy, but nothing crashes. The specific worst case is embedding drift -- when the model changes and old and new vectors coexist in the same index with the same dimension. No error is thrown. Results just get worse. The only defense is active monitoring: KL divergence on distance distributions, recall benchmarks against a fixed ground-truth set, and mandatory model_version tagging on every vector."

**Q7: Compare pgvector vs Qdrant vs Pinecone for a new project.**

A: "pgvector if you're below 100K vectors and need SQL joins -- it rides on your existing Postgres stack with WAL, PITR, and RLS. The weakness is post-filter recall collapse at low selectivity. Qdrant if you need self-hosted performance -- Rust-based, 15-30ms latency in-memory, filterable HNSW with payload indexes, native hybrid search via RRF. Best latency-throughput balance in the arXiv benchmark (4.55ms p50, 216 QPS). Pinecone if you want zero ops -- serverless, adaptive filtered search, 45-80ms latency. But cost scales linearly with queries: above 60-80M queries/month, it's 3-10x more expensive than self-hosted."

**Q8: Why is rank-based fusion (RRF) better than score-based fusion for hybrid search?**

A: "BM25 scores and cosine similarity scores are drawn from completely different distributions. BM25 scores can range from 0 to 20+; cosine similarity is bounded -1 to 1. If you try to weight-combine them (like 0.5*BM25 + 0.5*cosine), you're adding apples and oranges -- the result is dominated by whichever scoring system happens to produce larger absolute values. RRF avoids this by using only rank position: each document gets score 1/(k + rank) from each list, then scores are summed. This makes the fusion invariant to the scoring distribution. The k=60 parameter controls how much rank position matters versus mere presence in the list."

**Q9: What security controls are needed for vector databases in a multi-tenant SaaS?**

A: "First, tenant isolation: metadata filtering with gateway-injected tenant_id is the minimum. The gateway extracts tenant from the JWT -- the search API never accepts tenant_id from the client. Second, embedding inversion defense: arXiv:2504.00147 showed zero-shot text recovery from embeddings, so treat every embedding as PII. Apply SPARSE noise injection and encrypt at rest. Third, RBAC with least privilege: separate roles for ingestion, index maintenance, read-only search, and audit. Fourth, audit logging: append-only logs of every search/insert/delete with actor identity, but never log the embedding vectors themselves -- the log becomes an inversion attack surface. Finally, be aware of maturity gaps: Qdrant is insecure by default, Chroma had 1,170 exposed instances, and Milvus had CVE-2025-64513 (CVSS 9.3) where a single HTTP header bypassed all auth."

**Q10: How do you detect embedding drift in production?**

A: "Three layers of defense. First, instrument the search path to record cosine distances for every query result. Feed these into a rolling window and compute the median. Calibrate a baseline, then alert when the median shifts by more than a threshold (0.15 is a reasonable starting point). This detects the distribution shift that occurs when the embedding model changes. Second, maintain a fixed benchmark set of ~1,000 queries with known-good ground-truth results. Run recall evaluation on a schedule -- hourly or daily. If recall drops, something changed. Third, the model_version tag on every vector: if you ever see vectors with a version you don't expect, investigate immediately. The insidious thing about drift is that it's gradual -- today's results are 1% worse, next week 5%, next month 15%. Without active monitoring, teams don't notice until agent responses are visibly wrong."

---

## Key Numbers to Memorize

| Metric | Value | Context |
| --- | --- | --- |
| float32 1536-d vector size | 6 KB | Base unit for all capacity planning |
| HNSW memory overhead | ~1.5x raw vector size | Graph edges add ~50% |
| Faiss HNSW R@1 at efSearch=16 | 0.874 | The "ANN is approximate" proof point |
| Faiss HNSW R@1 at efSearch=64 | 0.978 | 3x slower but 11.9% better recall |
| IVF-PQ compression ratio | 32-128x | 500M vectors in ~24 GB |
| DiskANN single-node QPS | 5,000+ | SSD-backed, 95%+ recall at billion scale |
| Hybrid RRF k parameter | 60 | Standard default |
| WANDS hybrid NDCG | 0.7497 | vs BM25 0.6983 vs vector 0.6953 |
| Cross-encoder reranking lift | 15-25% Precision@5 | Adds 100-300ms |
| Selectivity QPS gap | 180 vs 2,400 | 0-1% vs 25-100% selectivity (13x) |
| Cost tipping point | 60-80M queries/mo | Self-hosted 3-10x cheaper above this |
| Pinecone YFCC recall@10 | 0.989 at ~20ms | 10M vectors, filtered, ICML 2025 |
| Tiger Data Qdrant p99 | 38.71ms | 50M vectors, 99% recall |
| pgvectorscale QPS | 471.57 | 11.4x higher than Qdrant on same benchmark |
| CVE-2025-64513 | CVSS 9.3 | Milvus auth bypass via single HTTP header |
| Hidden cost multiplier | 2.5-4x pricing page | Real production bills |
| Qdrant BQ memory reduction | up to 40x | Binary quantization |

---

## Quick Reference

```
Algorithm Selection:
  <100K vectors, need SQL joins      -> pgvector HNSW (m=16, ef_search tuned)
  Millions, best recall in-memory    -> HNSW (M=16-64, ef_construction=100-400)
  Billions, RAM-constrained          -> IVF-PQ (48 sub-quantizers, 8-bit)
  Billions, single-node SSD          -> DiskANN (Vamana graph, PQ in RAM)
  High-throughput inner product      -> ScaNN (anisotropic quantization)

Filtering Strategy:
  pgvector:   post-filter (+ iterative scans for <1% selectivity)
  Qdrant:     filterable HNSW + payload indexes (create BEFORE ingest)
  Weaviate:   pre-filter allow-list + ACORN/sweeping
  Pinecone:   adaptive bitmap per slab

Hybrid Search Pipeline:
  1. BM25 top-100 + Dense top-100
  2. RRF fusion (k=60, by rank not score)
  3. Cross-encoder rerank (top-50 -> top-5, +100-300ms, +15-25% P@5)

Multi-Tenancy (by strength):
  Collection-per-tenant > Weaviate native > Namespace > Metadata filter
  CRITICAL: payload filter != RBAC; gateway must inject tenant_id

Cost Decision:
  <60M queries/mo  -> managed (Pinecone) is comparable
  >60-80M queries/mo -> self-host (3-10x cheaper)
  Budget: 2.5-4x pricing page for actual bills

Fallback Chain:
  1. Filtered HNSW/IVF ANN at configured ef/nprobe
  2. Exact distance on bounded candidate set (BM25 shortlist)
  3. Lexical BM25 / Postgres FTS (tag: degraded=true)

Key Monitoring Thresholds:
  Shard skew > 3-5x           -> rebalance
  Centroid population > 20-50x -> retrain k-means
  Replica mismatch > 0         -> investigate
  p99/p50 > 3x                -> memory / compaction issue
  KL divergence rising         -> embedding drift
```
