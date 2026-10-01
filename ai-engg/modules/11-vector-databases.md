# 11. How Vector Databases Work

**Sub-areas covered**: Three-layer architecture (storage/index/query) with memory-disk trade-offs and crash-recovery semantics, index algorithms (HNSW multi-layer graph search, IVF Voronoi partitioning, IVF-PQ composite compression, DiskANN SSD-resident Vamana graph, ScaNN anisotropic quantization) with complexity analysis and decision matrix, ANN search mechanics vs. brute-force O(n), hybrid search pipeline (BM25 + dense + RRF + cross-encoder reranking) with WANDS benchmark data (0.7497 NDCG hybrid vs. 0.6983 BM25-only), pre-filter vs. post-filter with selectivity-dependent QPS collapse, quantization cost levers (SQ8 4x, BQ 32x, PQ 32x compression), token economics (Pinecone consumption vs. Qdrant capacity pricing, 3-10x cost inversion above 60-80M queries/month, hidden 2.5-4x bill multipliers), latency benchmarks (Tiger Data 50M-vector p99 comparison, arXiv:2608.12812 7-system evaluation, multi-process QPS under 100ms p99 budget), distributed resilience (stateful vs. stateless compute-storage separation, hash/consistent/semantic-aware sharding, Raft consensus, index divergence across replicas, rebuild storms), 9 documented production failure modes (hot shards, centroid collapse, memory saturation, index divergence, rebuild storms, embedding drift, SSD wear, routing misfires, parameter drift), enterprise security (multi-tenancy isolation patterns with semantic data leak risk, embedding inversion attacks via arXiv:2504.00147, SPARSE framework defense via arXiv:2602.07090, RBAC maturity gaps, CVE-2025-64513 Milvus auth bypass, OWASP LLM08:2025), vendor comparison (Pinecone/Qdrant/Weaviate/Milvus/pgvector), and two enterprise system-design scenarios with trade-off matrices

---

## 1. System Topology & Data Flow

A production vector database deployment decomposes into three core layers -- a **storage layer** handling vector persistence and compression, an **index layer** maintaining graph or cluster structures for sub-linear search, and a **query layer** routing requests, applying filters, and fusing results. Surrounding these are a **control plane** managing configuration and access, a **telemetry layer** tracking retrieval quality and cost, and optional **tool proxies** integrating with embedding services and reranking models.

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│                                CONTROL PLANE                                      │
│                                                                                   │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌──────────────────────────┐  │
│  │ Cluster Coordinator  │  │ Auth / RBAC Engine   │  │ Index Lifecycle Manager  │  │
│  │ (shard map, routing  │  │ (API keys, JWT,      │  │ (build triggers, rebuild │  │
│  │  table, node health, │  │  collection-level     │  │  scheduling, concurrency │  │
│  │  rebalance triggers) │  │  roles, tenant        │  │  limits, stagger policy, │  │
│  │                      │  │  isolation policy)    │  │  compaction windows)     │  │
│  └──────────┬───────────┘  └──────────┬───────────┘  └───────────┬──────────────┘  │
└─────────────┼──────────────────────────┼──────────────────────────┼─────────────────┘
              │ routing metadata          │ auth context             │ build/compact cmds
┌─────────────▼──────────────────────────▼──────────────────────────▼─────────────────┐
│                                QUERY LAYER (DATA PLANE)                             │
│                                                                                     │
│  ┌───────────────┐  ┌────────────────┐  ┌──────────────────┐  ┌─────────────────┐  │
│  │ Query Router   │  │ Filter Engine   │  │ ANN Search       │  │ Result Merger   │  │
│  │ (fan-out to    │  │ (pre-filter:    │  │ (HNSW graph walk │  │ (shard results  │  │
│  │  relevant      │  │  metadata index │  │  or IVF cluster  │  │  merge, top-K   │  │
│  │  shards via    │  │  integration;   │  │  probe; ef_search│  │  extraction,    │  │
│  │  shard map or  │  │  post-filter:   │  │  / nprobe tuning │  │  optional cross │  │
│  │  scatter-      │  │  k*N oversample │  │  per latency     │  │  -encoder       │  │
│  │  gather)       │  │  + discard)     │  │  budget)         │  │  rerank)        │  │
│  └───────┬───────┘  └───────┬────────┘  └────────┬─────────┘  └───────┬─────────┘  │
│          │ shard addrs       │ filter plan         │ candidates         │ ranked K    │
│          ▼                   ▼                     ▼                    ▼             │
│  ┌──────────────────────────────────────────────────────────────────────────────┐   │
│  │                        Hybrid Search Coordinator                             │   │
│  │  BM25 sparse path ──┐                                                       │   │
│  │                      ├── Reciprocal Rank Fusion (by rank, NOT score) ──▶ K   │   │
│  │  Dense vector path ──┘                                                       │   │
│  └──────────────────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────┬────────────────────────────────────┘
                                                 │ read/write ops
┌────────────────────────────────────────────────▼────────────────────────────────────┐
│                              INDEX LAYER                                            │
│                                                                                     │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────────────┐  │
│  │ HNSW Engine       │  │ IVF/IVF-PQ       │  │ DiskANN Engine                   │  │
│  │ (multi-layer      │  │ Engine           │  │ (Vamana graph in RAM,            │  │
│  │  skip-list graph; │  │ (k-means         │  │  PQ codes in RAM,                │  │
│  │  greedy descent   │  │  centroids +     │  │  full-precision vectors on SSD;  │  │
│  │  from top layer   │  │  per-cluster     │  │  rerank from SSD on final        │  │
│  │  to layer 0;      │  │  exact search;   │  │  candidates only)                │  │
│  │  incremental      │  │  optional PQ     │  │                                  │  │
│  │  insert/delete)   │  │  compression)    │  │                                  │  │
│  └──────────┬────────┘  └──────────┬───────┘  └───────────────┬──────────────────┘  │
└─────────────┼───────────────────────┼──────────────────────────┼─────────────────────┘
              │                       │                          │
┌─────────────▼───────────────────────▼──────────────────────────▼─────────────────────┐
│                             STORAGE / PERSISTENCE LAYER                              │
│                                                                                      │
│  ┌──────────────────────┐  ┌───────────────────────┐  ┌──────────────────────────┐  │
│  │ Vector Store          │  │ WAL (Write-Ahead Log)  │  │ Metadata Store           │  │
│  │ (raw float32 vectors; │  │ (crash recovery;       │  │ (payload fields,         │  │
│  │  6 KB per 1536-dim    │  │  sync = durability,    │  │  tenant_id, model_ver,   │  │
│  │  vector; 600 GB at    │  │  async = speed;        │  │  source, timestamps;     │  │
│  │  100M vectors;        │  │  HNSW rebuild from     │  │  payload indexes for     │  │
│  │  quantized variants:  │  │  WAL can take minutes  │  │  pre-filter traversal    │  │
│  │  SQ8=4x, BQ=32x,     │  │  to hours at scale)    │  │  integration)            │  │
│  │  PQ=32x compression)  │  │                        │  │                          │  │
│  └──────────────────────┘  └───────────────────────┘  └──────────────────────────┘  │
│                                                                                      │
│  ┌──────────────────────────────────────────────────────────────────────────────┐    │
│  │ Tiered Storage Controller                                                    │    │
│  │ (hot vectors in RAM ──▶ warm in memory-mapped files ──▶ cold on SSD/S3)     │    │
│  │ (stateless arch: compute nodes + object storage; stateful: shard-local)      │    │
│  └──────────────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────────────┘
              │ metrics: QPS, p50/p95/p99 latency, recall, shard skew, embedding drift
┌─────────────▼────────────────────────────────────────────────────────────────────────┐
│                          TELEMETRY / OBSERVABILITY LAYER                              │
│                                                                                      │
│  Shard load skew ratio (alert >3-5x) | Centroid query concentration (>40% = alarm)  │
│  Replica neighbor mismatch rate (any non-zero = problem) | p99/p50 ratio (>3x)      │
│  KL divergence on distance distributions (embedding drift) | SSD TBW monitor        │
│  Index rebuild duration + resource contention | QPS per recall tier | cost/query     │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                         TOOL PROXY LAYER                                       │
│  (external services invoked by the vector DB pipeline)                         │
│                                                                                │
│  ┌──────────────────┐  ┌───────────────────┐  ┌────────────────────────────┐  │
│  │ Embedding Service │  │ Reranker Service   │  │ BM25 Index                 │  │
│  │ (OpenAI, Cohere,  │  │ (Voyage rerank-    │  │ (Elasticsearch, Tantivy,   │  │
│  │  Voyage; MUST use │  │  2.5, Cohere       │  │  or native -- Weaviate,    │  │
│  │  same model for   │  │  rerank-v3;        │  │  Qdrant v1.10;             │  │
│  │  ingestion +      │  │  adds 100-300ms;   │  │  manual composition for    │  │
│  │  query -- shared  │  │  15-25% Precision  │  │  pgvector)                 │  │
│  │  vector space     │  │  @5 lift)          │  │                            │  │
│  │  invariant)       │  │                    │  │                            │  │
│  └──────────────────┘  └───────────────────┘  └────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A client submits a vector search query (embedding or raw text) to the query layer. The auth engine validates credentials and resolves tenant context -- multi-tenant isolation failures here mean another customer's knowledge base appears in AI responses, a semantic data leak with no relational-DB equivalent. (2) If raw text, the query is embedded via the embedding service; the shared vector space invariant is enforced (same model for ingest and query -- a mismatch silently destroys recall). (3) The query router consults the shard map to identify target shards. In stateful architectures (Qdrant, Weaviate), each shard holds data + index locally. In stateless architectures (Milvus, Vespa), the router dispatches to compute nodes that load index segments from object storage on demand. (4) The filter engine applies metadata predicates. Pre-filter integrates constraints into the graph traversal (Qdrant, Weaviate) -- at <1% selectivity, post-filter recall collapses because the graph exhausts before finding k survivors. (5) The ANN search engine executes the index-specific algorithm: HNSW greedy descent, IVF centroid probe, or DiskANN SSD-backed rerank. (6) For hybrid search, sparse BM25 results and dense vector results are merged via Reciprocal Rank Fusion (by rank, not score -- BM25 and cosine scores are incomparable). An optional cross-encoder reranker rescores the fused shortlist, adding 100-300ms but lifting Precision@5 by 15-25%. (7) The result merger aggregates shard-local results into a global top-K, returned to the client. (8) Telemetry records per-query latency, recall (against scheduled ground-truth), shard load distribution, and cost -- the only defense against the silent degradation pattern that defines vector DB failure modes.

---

## 2. Core Mechanics & Algorithms

### 2.1 Why ANN: The Brute-Force Wall

Exact nearest-neighbor search on N vectors requires O(N) distance computations per query. At 10M vectors, a single brute-force query takes ~1,000 seconds. Every production vector database uses **Approximate Nearest Neighbor (ANN)** algorithms that trade exact correctness for sub-linear search time, typically delivering <5ms latency at 10M scale with 95-99% recall.

### 2.2 HNSW (Hierarchical Navigable Small World)

The dominant production algorithm. Used by Pinecone, Qdrant, Weaviate, Milvus, pgvector.

**Mechanism.** Builds a multi-layer graph inspired by skip lists. The top layer is extremely sparse -- an "express lane" for long-range jumps across the vector space. Each lower layer adds density. Layer 0 (bottom) contains all N vectors. A vector's probability of appearing at layer L decreases exponentially: P(L) ~ 1/M^L, where M is the max-connections parameter. This creates a hierarchy where search starts with coarse positioning at the top and refines to fine-grained neighborhood search at layer 0.

**Search process.** Enter at the top layer's entry point. Greedily navigate to the node closest to the query vector. Descend one layer. Repeat greedy navigation at each layer. At layer 0, expand the search to a candidate list of size ef_search, evaluating neighbors of neighbors. Return the k closest from this final candidate set.

**Build process.** For each new vector: (1) determine its maximum layer via the exponential distribution, (2) starting from the top layer, greedily descend to the target layer, (3) at each layer from the target down to 0, select the M nearest neighbors and create bidirectional edges. Build complexity: O(N log N) distance computations plus graph update overhead.

**Key parameters and their effects:**

```
┌──────────────────┬────────────────────────────────────────────┬──────────────┐
│ Parameter        │ Effect                                     │ Typical Range│
├──────────────────┼────────────────────────────────────────────┼──────────────┤
│ M                │ Max connections per node per layer. Higher │ 16-64        │
│ (max connections)│ = better recall, more RAM, slower build.   │              │
│                  │ Memory overhead scales linearly with M.    │              │
├──────────────────┼────────────────────────────────────────────┼──────────────┤
│ ef_construction  │ Candidate list size during index build.    │ 100-400      │
│                  │ Higher = better graph quality but slower   │              │
│                  │ construction. Set once, cannot change      │              │
│                  │ without rebuild.                           │              │
├──────────────────┼────────────────────────────────────────────┼──────────────┤
│ ef_search        │ Candidate list size during query. Higher   │ 50-200+      │
│                  │ = better recall, higher latency. Tunable   │              │
│                  │ at query time -- the primary recall/speed  │              │
│                  │ knob in production.                        │              │
└──────────────────┴────────────────────────────────────────────┴──────────────┘
```

**Characteristics.** Supports incremental inserts and deletes without full rebuild (key advantage over IVF). Memory overhead: ~1.5x raw vector size for graph edges. Typical recall: 98%+ at ef_search=100. The graph is topology-dependent on insert order -- two replicas built from the same vectors in different order produce different neighbor sets for the same query (the index divergence failure mode).

### 2.3 IVF (Inverted File Index)

Partitions vector space into Voronoi cells via k-means clustering.

**Mechanism.** Build phase: run k-means to create `nlist` cluster centroids (typically 100-10,000). Assign each vector to its nearest centroid. Query phase: compute distance from query to all centroids, identify the `nprobe` closest centroids, perform exact brute-force search only within those clusters.

**Complexity.** Build: O(N * nlist * iterations) for k-means. Query: O(nlist + nprobe * N/nlist) -- the first term finds nearest centroids, the second searches within selected clusters. With nprobe << nlist, effective query cost is O(N * nprobe/nlist), a significant reduction over brute-force.

**Trade-offs vs. HNSW.** Lower memory footprint (no graph edges stored). Does NOT support efficient incremental inserts -- new vectors may land in wrong clusters, requiring periodic k-means retraining. Less recall at equivalent latency compared to HNSW. Better suited for static, large datasets where memory is the constraint.

### 2.4 IVF-PQ (IVF + Product Quantization)

Composite index combining IVF's space partitioning with PQ's vector compression.

**Mechanism.** IVF narrows the search to relevant clusters. Within each cluster, Product Quantization splits each D-dimensional vector into M sub-vectors of D/M dimensions each. Each sub-vector is replaced by the index of its nearest entry in a learned codebook (typically 256 entries = 8 bits per sub-vector). Distance computation uses pre-computed lookup tables on the compressed codes -- asymmetric distance computation (ADC) computes exact distance from the query sub-vector to each codebook entry, then sums across sub-vectors.

**Compression math.** A 1536-dim float32 vector = 6,144 bytes. With M=48 sub-quantizers at 8 bits each: 48 bytes per vector = 128x compression. At 500M vectors: ~24 GB RAM vs. ~3 TB uncompressed. Trade-off: 3-5% recall loss vs. full-precision HNSW.

**Best for.** Billion-scale, memory-constrained deployments where 95% recall is acceptable.

### 2.5 DiskANN (Microsoft Research)

Graph-based index designed for SSD-resident operation, powering Bing semantic search and Azure Cosmos DB.

**Mechanism.** Builds a Vamana graph -- similar to HNSW but single-layer with better degree control (bounded out-degree, no multi-layer hierarchy). The key insight: keep PQ-compressed vectors and the graph adjacency list in RAM for traversal, but store full-precision vectors on NVMe SSD. During search, the graph is walked using compressed vectors for approximate distance. Only the final top candidates are fetched from SSD for precise reranking.

**Performance.** Sub-millisecond latency at 95%+ recall on billion-scale datasets using a fraction of HNSW's RAM. 5,000+ QPS on a single node with SSD. **Filtered-DiskANN** (Gollapudi et al., WWW 2023) extends edge construction to respect label sets, preventing metadata filters from destroying recall -- a direct solution to the post-filter selectivity collapse problem.

### 2.6 ScaNN (Google Research)

Optimized for high-throughput inner-product search (recommendation systems, Google-scale retrieval).

**Mechanism.** Uses **anisotropic quantization** -- a quantization-aware training procedure that preserves inner-product ordering better than standard PQ by assigning higher fidelity to dimensions that contribute more to the inner product. Two-stage pipeline: fast approximate scoring with compressed codes to produce candidates, then precise reranking with full-precision vectors.

### 2.7 Algorithm Decision Matrix

```
┌────────────┬────────────────────┬──────────────┬──────────┬───────────┬──────────────┐
│ Algorithm  │ Best For           │ Memory       │ Recall   │ Scale     │ Incremental  │
│            │                    │              │          │           │ Inserts      │
├────────────┼────────────────────┼──────────────┼──────────┼───────────┼──────────────┤
│ HNSW       │ Best recall,       │ High (~1.5x  │ 98%+     │ Millions  │ Yes          │
│            │ in-memory          │ overhead)    │          │           │              │
├────────────┼────────────────────┼──────────────┼──────────┼───────────┼──────────────┤
│ IVF-Flat   │ Static datasets,   │ Low          │ Good     │ 10M-100M │ No (rebuild) │
│            │ memory-conscious   │              │          │           │              │
├────────────┼────────────────────┼──────────────┼──────────┼───────────┼──────────────┤
│ IVF-PQ     │ Billion-scale,     │ Very low     │ Good     │ Billions  │ No (rebuild) │
│            │ RAM-constrained    │ (~32-128x    │ (3-5%    │           │              │
│            │                    │ compression) │ loss)    │           │              │
├────────────┼────────────────────┼──────────────┼──────────┼───────────┼──────────────┤
│ DiskANN    │ Billion-scale,     │ Low (PQ in   │ 95%+     │ Billions  │ Limited      │
│            │ single-node SSD    │ RAM only)    │          │           │              │
├────────────┼────────────────────┼──────────────┼──────────┼───────────┼──────────────┤
│ ScaNN      │ High-throughput    │ Moderate     │ High     │ Millions- │ Limited      │
│            │ inner product      │              │          │ Billions  │              │
└────────────┴────────────────────┴──────────────┴──────────┴───────────┴──────────────┘
```

### 2.8 Hybrid Search: BM25 + Vector + Reranking

Pure vector search underperforms hybrid on most production workloads. Embeddings treat "E-4521" and "E-4522" as nearly identical; BM25 treats them as completely different terms. This complementarity is the foundation of hybrid search.

**Three-stage production pipeline:**

```
Stage 1a: BM25 sparse retrieval ──────────────────────┐
          (top-100 to top-1000 candidates)             │
                                                       ├──▶ Reciprocal Rank Fusion
Stage 1b: Dense vector ANN retrieval ─────────────────┘    (merges by RANK, not score;
          (top-100 to top-1000 candidates)                  k=60 default parameter)
                                                                     │
                                                                     ▼
                                                       Cross-Encoder Neural Reranker
                                                       (joint query+doc scoring;
                                                        adds 100-300ms latency;
                                                        15-25% Precision@5 lift)
                                                                     │
                                                                     ▼
                                                              Top-K Final Results
```

**Benchmark data.** WANDS e-commerce: tuned hybrid = 0.7497 NDCG, BM25-only = 0.6983, vector-only = 0.6953 -- hybrid delivers 7.4% lift. Financial documents: hybrid + reranking achieves Recall@5 of 0.816.

**Common architectural mistake.** Using a single weighted formula to combine BM25 and cosine scores instead of rank-based fusion. BM25 scores and cosine similarity scores live in different distributions; direct combination produces unstable results.

### 2.9 Pre-Filter vs. Post-Filter

**Post-filter (HNSW default).** Walk graph, collect k*N candidates, discard those failing the metadata filter. At low filter selectivity (<1%), recall collapses because the graph exhausts its traversal budget before finding k survivors.

**Pre-filter.** Apply metadata filter first, then run ANN on the filtered subset. Works well when filters are selective. Qdrant and Weaviate integrate payload filtering directly into the HNSW traversal -- the filter is evaluated as part of neighbor expansion, avoiding both the recall collapse of post-filter and the full-scan cost of naive pre-filter.

**Selectivity impact on performance:**

```
┌────────────────────────┬──────────┬─────────────┐
│ Filter Selectivity     │ QPS      │ p99 Latency │
├────────────────────────┼──────────┼─────────────┤
│ 0-1% (rare tenant)    │ 180      │ 220ms       │
├────────────────────────┼──────────┼─────────────┤
│ 25-100% (broad)       │ 2,400    │ 38ms        │
└────────────────────────┴──────────┴─────────────┘
```

This 13x QPS gap is why multi-tenant vector databases with small tenants require integrated filter traversal rather than post-filtering. It is also why Filtered-DiskANN builds label awareness directly into graph edges.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Per Million Vectors (Managed Cloud, 1536-dim)

```
┌─────────────────┬─────────────────────┬────────────────┬──────────────┬────────────────┐
│ Scale           │ Pinecone Serverless │ Weaviate Cloud │ Qdrant Cloud │ pgvector (RDS) │
├─────────────────┼─────────────────────┼────────────────┼──────────────┼────────────────┤
│ 10M vectors     │ ~$70/mo             │ ~$135/mo       │ ~$65/mo      │ ~$45/mo        │
├─────────────────┼─────────────────────┼────────────────┼──────────────┼────────────────┤
│ 100M vectors    │ $700+/mo            │ Varies (BQ     │ Significantly│ <$100/mo       │
│                 │                     │ helps)         │ less         │ (self-hosted)  │
└─────────────────┴─────────────────────┴────────────────┴──────────────┴────────────────┘
```

**Pricing model divergence -- this is what catches teams:**

- **Pinecone (consumption-based).** Storage $0.30/GB/mo + read units $16/million + write units $4/million. Cheap when idle. Query-heavy applications produce bill shock.
- **Qdrant Cloud (capacity-based).** Reserved RAM/CPU/disk, no per-query charge. High-QPS apps get cheaper per query as they scale. Self-hosted on a 16GB VPS: ~$30-50/mo for millions of vectors.
- **Weaviate Cloud (dimension-based).** ~$0.095 per million dimensions stored per month.

**Tipping point.** Above 60-80M queries/month, self-hosted Qdrant or Weaviate on fixed-cost infrastructure undercuts Pinecone Serverless by 3-10x. Real-world validation: a consumer AI startup migrated 50M vectors from Pinecone at $1,200/mo to self-hosted Qdrant on a 64GB Hetzner instance at $130/mo -- a 9.2x reduction.

**Hidden cost multipliers (actual bills average 2.5-4x the pricing page estimate):**

```
┌────────────────────────────────┬──────────────────────────────────────────────┐
│ Hidden Cost                    │ Impact                                       │
├────────────────────────────────┼──────────────────────────────────────────────┤
│ Egress fees                    │ $0.08-0.09/GB on AWS                        │
├────────────────────────────────┼──────────────────────────────────────────────┤
│ Index rebuild compute          │ $12-40 per 10M vectors                      │
├────────────────────────────────┼──────────────────────────────────────────────┤
│ HNSW storage overhead          │ ~1.5x raw vector size for graph edges       │
├────────────────────────────────┼──────────────────────────────────────────────┤
│ Embedding generation           │ Can match or exceed the database bill       │
├────────────────────────────────┼──────────────────────────────────────────────┤
│ Re-embedding 50M docs          │ ~25 billion tokens (a real budget line)     │
└────────────────────────────────┴──────────────────────────────────────────────┘
```

**Quantization as a cost lever:**

- Qdrant Binary Quantization: up to 40x memory reduction.
- Weaviate BQ: reduces 100M vectors from ~$1,459/mo to ~$45/mo on dimension-based billing -- a 32x cost reduction from a single configuration change.
- Qdrant 1.5-bit/2-bit quantization: up to 64x memory reduction.

### 3.1.1 Cost-Per-1K-Queries Formula

Consolidating the per-query economics into a single formula with explicit assumptions:

**Assumptions:**
- 10M vectors, 1536-dim, float32
- Embedding model: OpenAI text-embedding-3-small ($0.02 per 1M tokens, ~6 tokens avg per query)
- Vector DB: Pinecone Serverless (storage $0.30/GB/mo, reads $16/1M read units, 1 read unit per query)
- Cross-encoder reranker: self-hosted (amortized compute ~$0.0001 per query)
- Storage prorated over 1M queries/month baseline

```
Cost_per_1K_queries =
    (1000 * embedding_cost_per_query)
  + (1000 * search_cost_per_query)
  + (storage_cost_monthly / queries_per_month * 1000)
  + (1000 * reranker_cost_per_query)

= (1000 * $0.00000012)           # embedding: 6 tokens * $0.02/1M tokens
+ (1000 * $0.000016)             # Pinecone read: $16/1M read units
+ ($27.65 / 1,000,000 * 1000)    # storage: 10M * 6KB * 1.5x overhead / 1GB * $0.30/mo
+ (1000 * $0.0001)               # reranker: self-hosted amortized GPU

= $0.00012 + $0.016 + $0.028 + $0.10

= ~$0.144 per 1K queries (Pinecone Serverless, 10M vectors)
```

**Comparison under same assumptions but self-hosted Qdrant on a $50/mo VPS:**

```
Cost_per_1K_queries (self-hosted) =
    (1000 * $0.00000012)           # embedding (same)
  + (1000 * $0.00)                 # search: no per-query charge on capacity pricing
  + ($50.00 / 1,000,000 * 1000)    # VPS cost prorated
  + (1000 * $0.0001)               # reranker (same)

= $0.00012 + $0.00 + $0.05 + $0.10

= ~$0.150 per 1K queries (self-hosted, 10M vectors, 1M queries/mo)
```

At 1M queries/month, the costs are comparable. The tipping point: above ~3M queries/month, the Pinecone per-read charge ($0.016/query) dominates and self-hosted becomes 3-10x cheaper. At 10M queries/month, Pinecone cost rises to ~$0.188/1K while self-hosted stays at ~$0.105/1K. These numbers exclude egress fees ($0.08-0.09/GB on AWS) and index rebuild compute ($12-40 per 10M vectors), which can add 20-40% to either path.

### 3.2 Latency Benchmarks

**Large-scale: 50M vectors, 768-dim, 99% recall (Tiger Data benchmark):**

```
┌──────────┬────────────┬────────────────────────┐
│ Metric   │ Qdrant     │ pgvector+pgvectorscale │
├──────────┼────────────┼────────────────────────┤
│ p50      │ 30.75ms    │ 31.07ms                │
├──────────┼────────────┼────────────────────────┤
│ p95      │ 36.73ms    │ 60.42ms                │
├──────────┼────────────┼────────────────────────┤
│ p99      │ 38.71ms    │ 74.60ms                │
├──────────┼────────────┼────────────────────────┤
│ QPS      │ 41.47      │ 471.57                 │
└──────────┴────────────┴────────────────────────┘
```

Qdrant wins on tail latency (48% better p99). pgvectorscale wins on throughput (11.4x higher QPS on a single node). The right choice depends on whether you are optimizing for worst-case user experience (Qdrant) or aggregate system throughput (pgvectorscale).

**7-system evaluation (arXiv:2608.12812, SIFT1M):**

```
┌───────────┬─────────────┬───────────────┬──────┬───────────────────────────────┐
│ System    │ p50 Latency │ p99/p50 Ratio │ QPS  │ Notes                         │
├───────────┼─────────────┼───────────────┼──────┼───────────────────────────────┤
│ Qdrant    │ 4.55ms      │ 1.85          │ 216  │ Best latency-throughput       │
│           │             │               │      │ balance                       │
├───────────┼─────────────┼───────────────┼──────┼───────────────────────────────┤
│ FAISS     │ -           │ -             │ 866  │ Throughput leader (library,   │
│           │             │               │      │ not DB)                       │
├───────────┼─────────────┼───────────────┼──────┼───────────────────────────────┤
│ Weaviate  │ -           │ -             │ -    │ >99% out-of-box recall        │
├───────────┼─────────────┼───────────────┼──────┼───────────────────────────────┤
│ Milvus    │ -           │ -             │ -    │ Best at high-dim (0.971       │
│           │             │               │      │ recall at 960D)               │
├───────────┼─────────────┼───────────────┼──────┼───────────────────────────────┤
│ pgvector  │ Higher      │ 1.63          │ 154  │ Most consistent tail behavior │
│           │             │ (tightest)    │      │                               │
└───────────┴─────────────┴───────────────┴──────┴───────────────────────────────┘
```

**Multi-process throughput (p99 < 100ms budget):**

```
┌───────────┬───────┐
│ System    │ QPS   │
├───────────┼───────┤
│ Weaviate  │ 8,290 │
├───────────┼───────┤
│ pgvector  │ 4,828 │
├───────────┼───────┤
│ Milvus    │ 4,725 │
├───────────┼───────┤
│ Qdrant    │ 1,737 │
├───────────┼───────┤
│ Redis     │ 1,642 │
└───────────┴───────┘
```

**By vendor (general ranges for typical workloads):**

```
┌────────────────────────────┬──────────────────┬────────────────────────────────┐
│ Vendor                     │ Typical Latency  │ Notes                          │
├────────────────────────────┼──────────────────┼────────────────────────────────┤
│ Pinecone (serverless)      │ 45-80ms          │ Consistent, zero-tuning        │
├────────────────────────────┼──────────────────┼────────────────────────────────┤
│ Pinecone (pod-based)       │ 20-40ms          │ Better latency, more ops       │
├────────────────────────────┼──────────────────┼────────────────────────────────┤
│ Qdrant (in-memory)         │ 15-30ms          │ Rust performance edge          │
├────────────────────────────┼──────────────────┼────────────────────────────────┤
│ Qdrant (memory-mapped)     │ 30-60ms          │ SSD-backed                     │
├────────────────────────────┼──────────────────┼────────────────────────────────┤
│ Milvus                     │ 25-50ms          │ Depends on index type          │
├────────────────────────────┼──────────────────┼────────────────────────────────┤
│ Weaviate                   │ 30-70ms          │ Hybrid search adds 10-20ms     │
└────────────────────────────┴──────────────────┴────────────────────────────────┘
```

**Critical caveat.** At 1M vectors with no filters, almost every system delivers sub-10ms p99 at 99% recall. Differences become material at 10M+ vectors with metadata filters -- that is where architecture choices and parameter tuning determine whether you hit SLA or not.

### 3.3 NFR Summary

```
┌──────────────────────────┬──────────────────────────────────────────────────────────┐
│ NFR                      │ Production Target                                        │
├──────────────────────────┼──────────────────────────────────────────────────────────┤
│ Recall                   │ 95-99% (trade-off with latency and cost)                │
├──────────────────────────┼──────────────────────────────────────────────────────────┤
│ p99 Latency              │ <50ms for interactive RAG, <200ms with reranking         │
├──────────────────────────┼──────────────────────────────────────────────────────────┤
│ Throughput               │ 1,000-8,000+ QPS per node (system-dependent)            │
├──────────────────────────┼──────────────────────────────────────────────────────────┤
│ Availability             │ 99.9%+ (replication factor >= 2, automated failover)    │
├──────────────────────────┼──────────────────────────────────────────────────────────┤
│ Durability               │ Sync WAL for zero data loss; async WAL acceptable for   │
│                          │ re-derivable embeddings                                  │
├──────────────────────────┼──────────────────────────────────────────────────────────┤
│ Cost efficiency          │ Budget 2.5-4x the pricing page estimate; self-host      │
│                          │ above 60-80M queries/month                               │
├──────────────────────────┼──────────────────────────────────────────────────────────┤
│ Filter performance       │ Integrated pre-filter for <1% selectivity tenants       │
├──────────────────────────┼──────────────────────────────────────────────────────────┤
│ Index rebuild time       │ Minutes (HNSW at 10M) to hours (HNSW at 100M+)         │
│                          │ Plan for blue-green index swap                           │
└──────────────────────────┴──────────────────────────────────────────────────────────┘
```

---

## 4. Distributed Resilience & Security

### 4.1 Sharding Strategies

Two dominant architectural paradigms:

**Stateful (Qdrant, Weaviate, Vald).** Each worker owns and stores a shard's data + index. Simpler operationally. Rule of thumb for Qdrant: 1 shard per 5M vectors.

**Stateless / Compute-Storage Separation (Milvus, Vespa).** Workers are stateless compute nodes. Data lives in object storage (S3). Index segments loaded into cache on demand. Enables independent scaling of compute and storage -- add query capacity without duplicating data.

**Sharding methods:**

```
┌─────────────────────┬──────────────────────────────────────────────────────────────┐
│ Method              │ Characteristics                                              │
├─────────────────────┼──────────────────────────────────────────────────────────────┤
│ Hash-based          │ Deterministic routing by vector ID. Uniform distribution     │
│                     │ but no range queries. Simple, predictable.                   │
├─────────────────────┼──────────────────────────────────────────────────────────────┤
│ Consistent hashing  │ Minimizes data movement on node add/remove. Virtual nodes   │
│                     │ smooth distribution. Standard for elastic clusters.          │
├─────────────────────┼──────────────────────────────────────────────────────────────┤
│ Semantic-aware      │ [Emerging] Uses IVF coarse centroids for top-level routing, │
│ (IVF+HNSW hybrid)   │ HNSW within each shard. Queries route to semantically       │
│                     │ relevant shards only, reducing fan-out.                      │
└─────────────────────┴──────────────────────────────────────────────────────────────┘
```

**Query fan-out trade-off triangle.** Higher recall requires wider search (more shards probed), which conflicts with low latency and low cost. Distributed vector search is fundamentally an optimization problem over this triangle. Semantic-aware sharding reduces the conflict by routing queries to fewer, more relevant shards.

### 4.2 Replication and Consistency Models

**Leader-follower model.** Writes go to the leader, propagated to followers synchronously (strong consistency, slower writes) or asynchronously (faster writes, risk of stale reads).

**Raft consensus.** Used by both Qdrant and Milvus for replica coordination. Qdrant offers point-in-time consistency guarantees and ACID-compliant operations. Milvus provides tunable consistency: strong, bounded staleness, or eventual.

**Unique vector DB challenge -- index divergence.** Two replicas of the "same" HNSW graph can produce different neighbor sets for the same query because different insert order produces different graph topology. This is not a bug -- it is an inherent property of greedy graph construction. Detection: replica-to-replica neighbor mismatch rate. Any non-zero rate means replicas are diverged. Mitigation: replicate raw vectors and WAL for durability; build index independently on each node; accept that approximate results may vary across replicas within the recall guarantee.

### 4.3 Index Rebuild and Compaction

- **HNSW rebuild** on large datasets can saturate CPU for minutes to hours. Best practice: build on a replica or staging node, then swap in (blue-green index deployment).
- **IVF/IVF-PQ** requires full k-means retraining to incorporate new data. Periodic offline rebuild is standard operating procedure.
- **Milvus 2.6** replaced Kafka/Pulsar dependency with Woodpecker (custom WAL on object storage), reducing operational complexity significantly.
- **Rebuild storms.** Multiple background maintenance jobs (graph optimization, shard rebalancing, compaction) running concurrently cause cluster-wide CPU saturation and p99 spikes. Mitigation: stagger jobs across non-overlapping windows, enforce concurrency limits, prioritize compaction during off-peak hours.

### 4.4 Database Architecture Comparison

```
┌──────────────┬──────────────────┬──────────────────┬──────────────────┬──────────────────┐
│ Aspect       │ Pinecone         │ Qdrant           │ Weaviate         │ Milvus           │
├──────────────┼──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Architecture │ Serverless,      │ Single binary    │ Docker Compose,  │ K8s-native,      │
│              │ compute-storage  │ or Docker, Rust  │ Go               │ disaggregated    │
│              │ separated        │                  │                  │ nodes            │
├──────────────┼──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Sharding     │ Automatic,       │ Manual or auto,  │ Automatic        │ Hash/range,      │
│              │ hidden           │ 1 shard/5M vecs  │                  │ proxy-managed    │
├──────────────┼──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Replication  │ Managed          │ Raft-based,      │ Built-in         │ Raft for         │
│              │                  │ configurable RF  │                  │ consistency      │
├──────────────┼──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Consistency  │ Eventual         │ Point-in-time,   │ Eventual         │ Tunable (strong/ │
│              │ (managed)        │ ACID             │                  │ bounded/eventual)│
├──────────────┼──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Ops          │ None (managed)   │ Low (single      │ Medium (Docker)  │ High (K8s        │
│ complexity   │                  │ binary)          │                  │ required)        │
├──────────────┼──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Hybrid       │ Alpha-weighted   │ Native (v1.10    │ Native (BM25 +   │ Native           │
│ search       │ single-index     │ Query API)       │ vector, RRF)     │                  │
├──────────────┼──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Filter       │ Metadata         │ Payload index,   │ Integrated into  │ Partition-key    │
│ integration  │ filtering        │ integrated       │ traversal        │ filtering        │
└──────────────┴──────────────────┴──────────────────┴──────────────────┴──────────────────┘
```

### 4.5 Multi-Tenancy Isolation

Multi-tenancy in vector databases carries a unique risk: if tenant isolation fails, another customer's knowledge base gets surfaced directly into AI responses -- a semantic-level data leak with no relational-DB equivalent.

**Isolation approaches ranked by strength:**

```
┌──────────────────────────┬──────────────┬──────────────┬──────────────────────────────┐
│ Approach                 │ Isolation    │ Overhead     │ Notes                         │
│                          │ Strength     │              │                               │
├──────────────────────────┼──────────────┼──────────────┼──────────────────────────────┤
│ Collection-per-tenant    │ Strongest    │ Highest      │ Separate index per tenant.    │
│                          │              │              │ True resource isolation.       │
├──────────────────────────┼──────────────┼──────────────┼──────────────────────────────┤
│ Weaviate native          │ Strong       │ Moderate     │ Purpose-built tenant          │
│ multi-tenancy            │              │              │ isolation with per-tenant     │
│                          │              │              │ data lifecycle management.    │
├──────────────────────────┼──────────────┼──────────────┼──────────────────────────────┤
│ Namespace/partition      │ Moderate     │ Low          │ Pinecone namespaces, Milvus  │
│                          │              │              │ partitions. WARNING: Pinecone│
│                          │              │              │ namespaces are NOT security  │
│                          │              │              │ boundaries.                   │
├──────────────────────────┼──────────────┼──────────────┼──────────────────────────────┤
│ Metadata filtering       │ Weakest      │ Lowest       │ Single shared index with     │
│ (tenant_id field)        │              │              │ tenant_id filter. Misconfig  │
│                          │              │              │ = cross-tenant leak.          │
└──────────────────────────┴──────────────┴──────────────┴──────────────────────────────┘
```

### 4.6 Encryption and Access Control

**Encryption:**

```
┌───────────────────────┬──────────┬──────────┬──────────┬──────────┐
│ Capability            │ Pinecone │ Weaviate │ Qdrant   │ Milvus   │
├───────────────────────┼──────────┼──────────┼──────────┼──────────┤
│ Encryption at rest    │ AES-256  │ AES-256  │ AES-256  │ Config.  │
├───────────────────────┼──────────┼──────────┼──────────┼──────────┤
│ Encryption in transit │ TLS 1.3  │ TLS 1.3  │ TLS 1.3  │ TLS      │
├───────────────────────┼──────────┼──────────┼──────────┼──────────┤
│ Customer-managed keys │ Enterp.  │ Limited  │ Self-    │ Self-    │
│                       │ tier     │          │ hosted   │ hosted   │
├───────────────────────┼──────────┼──────────┼──────────┼──────────┤
│ Field-level encrypt.  │ No       │ No       │ No       │ No       │
└───────────────────────┴──────────┴──────────┴──────────┴──────────┘
```

**Embedding inversion risk.** A zero-shot technique (arXiv:2504.00147, April 2025) achieved meaningful text recovery from embeddings without training on the target model. If an attacker can query your embedding store, they can reconstruct source text. Every embedding exposure is a data breach. Defense: the SPARSE framework (arXiv:2602.07090, February 2026) injects dimension-sensitive Mahalanobis noise targeting semantically critical embedding dimensions while minimally affecting retrieval quality.

**RBAC maturity varies significantly across vendors:**

- Weaviate: Added RBAC in v1.29.0. Collection-level roles.
- Milvus: RBAC with partition-level granularity.
- Qdrant: API key auth only; instances are insecure by default per official docs.
- Chroma: A 2025 survey found 1,170 internet-accessible instances, ~1/3 exposing production data with no authentication.
- Milvus: CVE-2025-64513 (CVSS 9.3) -- single HTTP header with hardcoded constant bypassed all authentication.

**Best practices:** Separate roles (ingestion writer, index maintainer, read-only RAG service, security auditor). Short-lived credentials with rotation. OWASP LLM08:2025 "Vector and Embedding Weaknesses" is now in the 2025 LLM Top 10.

### 4.7 Production Failure Modes (9 Documented)

These define how vector databases actually break in production. The overarching pattern: vector databases rarely fail with clean outages. The system drifts -- retrieval quality slips, tail latency stretches, agent responses become less trustworthy. Everything still appears "up" but behavior is no longer correct.

**1. Hot Shards.** Semantic clustering (support tickets, error logs) causes non-uniform shard load. Detection: shard load skew ratio >3-5x.

**2. Centroid Collapse (IVF).** Domain evolution causes centroids to misrepresent the space. One centroid absorbs 20-50x more vectors than others. Early sign: teams continually increasing nprobe to maintain recall.

**3. Memory Saturation and Fragmentation.** Rising p99 with no QPS increase. NUMA misses, page faults. Memory pressure rarely produces explicit errors -- it corrupts performance slowly. Rule: always oversize RAM for vector workloads.

**4. Index Divergence Across Replicas.** Different insert order produces different HNSW topology. Everything looks healthy until neighbors are inspected across replicas.

**5. Rebuild Storms.** Concurrent background jobs (optimization, rebalancing, compaction) cause cluster-wide CPU saturation. Goes from quiet to catastrophic instantly.

**6. Embedding Drift.** When the embedding model changes, vectors before and after live in incompatible spaces. Two models with the same output dimension are especially dangerous -- no dimension mismatch to crash on; the system silently returns garbage. Detection: KL divergence on distance distributions.

**7. SSD Wear (PQ Hidden Cost).** Product Quantization reduces RAM but dramatically increases SSD I/O. Write amplification causes device failure months ahead of schedule. Monitor TBW (terabytes written).

**8. Routing Misfires.** Drifting centroids, stale routing metadata cause queries to hit wrong shards. Never shows up as an error. Manifests as quality degradation.

**9. Recall/Latency Parameter Drift.** As dataset grows, engineers increase query-time parameters, silently accepting higher latency or degraded recall. The culmination of all other failure modes.

**Detection thresholds:**

```
┌──────────────────────────────────┬───────────────────────────────────────┐
│ Metric                           │ Alert Threshold                       │
├──────────────────────────────────┼───────────────────────────────────────┤
│ Shard load skew ratio            │ >3-5x                                │
├──────────────────────────────────┼───────────────────────────────────────┤
│ Centroid query concentration     │ >40% to single centroid              │
├──────────────────────────────────┼───────────────────────────────────────┤
│ Centroid population imbalance    │ >20-50x between centroids            │
├──────────────────────────────────┼───────────────────────────────────────┤
│ Replica neighbor mismatch rate   │ Any non-zero value                   │
├──────────────────────────────────┼───────────────────────────────────────┤
│ p99/p50 latency ratio            │ >3x                                  │
├──────────────────────────────────┼───────────────────────────────────────┤
│ KL divergence (embedding drift)  │ Rising trend over baseline           │
├──────────────────────────────────┼───────────────────────────────────────┤
│ SSD TBW consumption rate         │ Exceeding device lifetime projection │
└──────────────────────────────────┴───────────────────────────────────────┘
```

### 4.8 Zero-Trust MCP for Vector Operations

When vector databases are exposed as MCP tool servers (e.g., a `vector_search` tool callable by LLM agents), the standard MCP transport model introduces security gaps that traditional API-key auth does not address. An agent orchestrator may invoke search, insert, or delete operations on behalf of different users or sub-agents -- without per-invocation authorization, a compromised or misprompted agent can exfiltrate tenant data or corrupt the index.

**Per-invocation auth model.** Every MCP tool call against the vector DB must carry an auth context that is validated independently of the session-level credential. The orchestrator signs each invocation with the caller's identity (user ID, agent ID, session ID). The vector DB MCP server validates this signature and resolves the caller's permissions before executing. Session tokens alone are insufficient -- a hijacked session grants unlimited vector operations.

**Capability scoping.** MCP tool definitions for vector operations should expose the minimum required surface area:

```
┌──────────────────────┬──────────────────────────────────────────────────────────┐
│ Capability           │ Scoping Rule                                             │
├──────────────────────┼──────────────────────────────────────────────────────────┤
│ search               │ Restricted to caller's tenant namespace. No cross-tenant │
│                      │ search even if the underlying index is shared. Query     │
│                      │ metadata (top_k, filters) bounded to prevent resource    │
│                      │ exhaustion (e.g., max top_k=100).                        │
├──────────────────────┼──────────────────────────────────────────────────────────┤
│ insert / upsert      │ Write-scoped to caller's tenant. Payload schema          │
│                      │ validated before embedding (reject arbitrary fields).    │
│                      │ Rate-limited per caller to prevent index pollution.      │
├──────────────────────┼──────────────────────────────────────────────────────────┤
│ delete               │ Requires elevated privilege. Soft-delete by default with │
│                      │ hard-delete requiring explicit confirmation token.       │
│                      │ Batch deletes capped and logged.                         │
└──────────────────────┴──────────────────────────────────────────────────────────┘
```

**Transport security.** MCP stdio transport (local) does not traverse the network but still requires process-level isolation -- a malicious tool server sharing the process can intercept vector data. MCP SSE/HTTP transport must enforce mutual TLS between orchestrator and vector DB tool server. All vector payloads in transit are encrypted; embedding vectors are as sensitive as the source text they encode (see embedding inversion risk in Section 4.6).

### 4.9 PII in Vector Stores

Embeddings encode semantic content of source text, which means PII embedded into vectors persists in a form that is not searchable by traditional DLP tools, not removable by standard database field-level redaction, and potentially recoverable via embedding inversion attacks (Section 4.6). This creates a compliance gap: GDPR right-to-erasure requires deleting the vector, not just the source document.

**Detection pipeline (pre-embedding):**

```
Source Text ──▶ Regex Pass ──▶ NER Pass ──▶ Decision ──▶ Embedding
                 │                │              │
                 │ SSN, credit    │ spaCy/       │ ALLOW: no PII detected
                 │ card, email,   │ Presidio     │ REDACT: replace PII spans with
                 │ phone patterns │ entity       │   placeholders before embedding
                 │                │ detection    │ REJECT: block embedding entirely
                 │                │ (PERSON,     │ SEPARATE: embed into isolated
                 │                │  ORG, GPE,   │   PII-designated namespace
                 │                │  DATE, etc.) │
```

**Regex layer** catches structured PII (SSN: `\d{3}-\d{2}-\d{4}`, credit cards, email, phone). Fast, deterministic, low false-positive. **NER layer** (Microsoft Presidio or spaCy) catches unstructured PII (names, addresses, dates of birth). Higher recall, higher latency (~5-15ms per chunk). The two-pass design keeps the NER model off the hot path for clean text.

**Handling strategies for PII vectors:**

1. **Redaction before embedding.** Replace PII spans with type tokens (`[PERSON]`, `[EMAIL]`). Preserves semantic context while removing identifiable content. Trade-off: slight retrieval quality degradation on PII-heavy queries (e.g., "find documents mentioning John Smith" will not match redacted vectors).
2. **Encrypted PII namespace.** Route PII-containing vectors to a separate collection with customer-managed encryption keys and stricter access controls. Enables GDPR-compliant per-user deletion without scanning the entire index.
3. **Embedding inversion mitigation.** Apply the SPARSE framework (arXiv:2602.07090) noise injection to PII namespace vectors. This degrades inversion attack fidelity while preserving retrieval quality within the noise-calibrated tolerance.

### 4.10 Immutable Audit Logs for Vector Operations

Vector databases lack the mature audit logging of relational databases. A silent cross-tenant query, an unauthorized bulk delete, or an embedding poisoning attack leaves no trace unless explicitly instrumented. Compliance frameworks (SOC2 CC7.2, GDPR Article 30, HIPAA 164.312(b)) require demonstrable records of who accessed what data and when.

**Append-only log schema:**

```
┌──────────────────┬──────────────────────────────────────────────────────────────┐
│ Field            │ Description                                                  │
├──────────────────┼──────────────────────────────────────────────────────────────┤
│ timestamp        │ ISO-8601 with microsecond precision, UTC                     │
├──────────────────┼──────────────────────────────────────────────────────────────┤
│ operation        │ SEARCH | INSERT | UPSERT | DELETE | INDEX_REBUILD |          │
│                  │ COLLECTION_CREATE | COLLECTION_DROP | CONFIG_CHANGE          │
├──────────────────┼──────────────────────────────────────────────────────────────┤
│ actor_id         │ User ID, service account, or agent ID (MCP caller identity) │
├──────────────────┼──────────────────────────────────────────────────────────────┤
│ tenant_id        │ Tenant context resolved from auth                            │
├──────────────────┼──────────────────────────────────────────────────────────────┤
│ collection       │ Target collection name                                       │
├──────────────────┼──────────────────────────────────────────────────────────────┤
│ vector_count     │ Number of vectors affected (inserted, deleted, returned)     │
├──────────────────┼──────────────────────────────────────────────────────────────┤
│ query_metadata   │ For SEARCH: top_k, filter predicates, model_version used.   │
│                  │ Embedding vectors themselves are NOT logged (inversion risk).│
├──────────────────┼──────────────────────────────────────────────────────────────┤
│ latency_ms       │ Operation duration                                           │
├──────────────────┼──────────────────────────────────────────────────────────────┤
│ status           │ SUCCESS | DENIED | ERROR                                     │
├──────────────────┼──────────────────────────────────────────────────────────────┤
│ source_ip        │ Client IP or MCP transport origin                            │
├──────────────────┼──────────────────────────────────────────────────────────────┤
│ log_hash         │ SHA-256 hash chaining previous entry (tamper evidence)       │
└──────────────────┴──────────────────────────────────────────────────────────────┘
```

**Implementation requirements.** Logs are append-only -- no UPDATE or DELETE operations on the log store. Write to a separate storage system from the vector DB itself (e.g., S3 with Object Lock, or an append-only PostgreSQL table with row-level security preventing deletes). Hash chaining (each entry includes the SHA-256 of the previous entry) provides tamper evidence without requiring a full blockchain. Retention: minimum 1 year for SOC2, aligned with data retention policy for GDPR. Critical: embedding vectors are excluded from the audit log to prevent the log itself from becoming an inversion attack surface -- log the query metadata (filters, top_k, collection) but not the vector.

---

## 5. Production Enterprise Code

### 5.1 Resilient Vector DB Client with Retries, Circuit Breaker, and Structured Logging

```python
"""
Production vector database client with:
- Exponential backoff + jitter retries
- Circuit breaker (prevents cascading failures)
- Structured JSON logging
- Graceful degradation to cached/fallback results
- Embedding drift detection
"""

import time
import json
import random
import logging
import hashlib
from enum import Enum
from typing import Optional
from dataclasses import dataclass, field
from collections import deque

import numpy as np

# ---------------------------------------------------------------------------
# Structured JSON Logger
# ---------------------------------------------------------------------------

class StructuredLogger:
    """Emits one JSON object per log line -- machine-parseable, grep-friendly."""

    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

    def log(self, level: str, event: str, **kwargs):
        record = {
            "ts": time.time(),
            "level": level,
            "event": event,
            **kwargs,
        }
        self.logger.info(json.dumps(record, default=str))

    def info(self, event: str, **kwargs):
        self.log("INFO", event, **kwargs)

    def warn(self, event: str, **kwargs):
        self.log("WARN", event, **kwargs)

    def error(self, event: str, **kwargs):
        self.log("ERROR", event, **kwargs)


log = StructuredLogger("vecdb")

# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class CircuitState(Enum):
    CLOSED = "closed"        # Normal operation
    OPEN = "open"            # Failing -- reject all calls
    HALF_OPEN = "half_open"  # Trying one probe call

@dataclass
class CircuitBreaker:
    """
    Trips after `failure_threshold` consecutive failures within
    `window_seconds`. Stays open for `recovery_timeout` seconds,
    then allows one probe call (half-open). On probe success, resets.
    """
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    window_seconds: float = 60.0

    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    failure_timestamps: deque = field(default_factory=deque, init=False)
    last_failure_time: float = field(default=0.0, init=False)

    def _prune_old_failures(self):
        cutoff = time.time() - self.window_seconds
        while self.failure_timestamps and self.failure_timestamps[0] < cutoff:
            self.failure_timestamps.popleft()

    def record_failure(self):
        now = time.time()
        self.failure_timestamps.append(now)
        self.last_failure_time = now
        self._prune_old_failures()
        if len(self.failure_timestamps) >= self.failure_threshold:
            self.state = CircuitState.OPEN
            log.warn("circuit_breaker_opened",
                     failures=len(self.failure_timestamps),
                     window_s=self.window_seconds)

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
                return True  # Allow one probe
            return False
        # HALF_OPEN: already allowing one probe; block additional calls
        return False

# ---------------------------------------------------------------------------
# Retry with Exponential Backoff + Jitter
# ---------------------------------------------------------------------------

def retry_with_backoff(
    func,
    max_retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    retryable_exceptions: tuple = (ConnectionError, TimeoutError),
):
    """
    Retries `func` with exponential backoff and full jitter.
    Jitter prevents thundering-herd on shared vector DB clusters.
    """
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            result = func()
            if attempt > 0:
                log.info("retry_succeeded", attempt=attempt)
            return result
        except retryable_exceptions as exc:
            last_exception = exc
            if attempt == max_retries:
                log.error("retries_exhausted",
                          max_retries=max_retries,
                          error=str(exc))
                raise
            # Full jitter: uniform [0, min(cap, base * 2^attempt)]
            delay = random.uniform(0, min(max_delay, base_delay * (2 ** attempt)))
            log.warn("retry_backoff",
                     attempt=attempt + 1,
                     delay_s=round(delay, 3),
                     error=str(exc))
            time.sleep(delay)
    raise last_exception  # unreachable, but satisfies type checker

# ---------------------------------------------------------------------------
# LRU Result Cache for Graceful Degradation
# ---------------------------------------------------------------------------

class ResultCache:
    """
    Bounded cache keyed by query embedding hash.
    Used as fallback when primary DB is circuit-broken.
    """
    def __init__(self, max_size: int = 10_000):
        self.max_size = max_size
        self._cache: dict[str, list[dict]] = {}
        self._access_order: deque = deque()

    @staticmethod
    def _hash_vector(vector: list[float]) -> str:
        raw = np.array(vector, dtype=np.float32).tobytes()
        return hashlib.sha256(raw).hexdigest()[:16]

    def get(self, vector: list[float]) -> Optional[list[dict]]:
        key = self._hash_vector(vector)
        return self._cache.get(key)

    def put(self, vector: list[float], results: list[dict]):
        key = self._hash_vector(vector)
        if key not in self._cache:
            if len(self._cache) >= self.max_size:
                evict_key = self._access_order.popleft()
                self._cache.pop(evict_key, None)
            self._access_order.append(key)
        self._cache[key] = results

# ---------------------------------------------------------------------------
# Embedding Drift Detector
# ---------------------------------------------------------------------------

class EmbeddingDriftDetector:
    """
    Monitors cosine distance distributions for shifts indicating
    the embedding model has changed (the 'slow poison' failure mode).
    Uses a rolling window of median distances.
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
                     baseline_median=round(self.baseline_median, 4),
                     current_median=round(current_median, 4),
                     shift=round(shift, 4))
            return True
        return False

# ---------------------------------------------------------------------------
# Production Vector DB Client
# ---------------------------------------------------------------------------

class VectorDBClient:
    """
    Wraps any vector DB SDK (Qdrant shown) with production resilience.

    Usage:
        from qdrant_client import QdrantClient
        raw = QdrantClient(url="https://...", api_key="...")
        client = VectorDBClient(raw, collection="products")
        results = client.search(query_vector, top_k=10, tenant_id="acme")
    """

    def __init__(
        self,
        raw_client,            # e.g. qdrant_client.QdrantClient instance
        collection: str,
        max_retries: int = 3,
        circuit_failure_threshold: int = 5,
        circuit_recovery_timeout: float = 30.0,
        cache_max_size: int = 10_000,
    ):
        self.raw = raw_client
        self.collection = collection
        self.max_retries = max_retries
        self.breaker = CircuitBreaker(
            failure_threshold=circuit_failure_threshold,
            recovery_timeout=circuit_recovery_timeout,
        )
        self.cache = ResultCache(max_size=cache_max_size)
        self.drift_detector = EmbeddingDriftDetector()

    def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        tenant_id: Optional[str] = None,
        score_threshold: Optional[float] = None,
    ) -> list[dict]:
        """
        Search with full resilience chain:
          1. Check circuit breaker
          2. Execute with retries + backoff + jitter
          3. On success: cache result, record distances for drift detection
          4. On failure: degrade to cached results
        """
        start = time.time()

        # --- Circuit breaker gate ---
        if not self.breaker.allow_request():
            log.warn("circuit_open_fallback_to_cache", collection=self.collection)
            cached = self.cache.get(query_vector)
            if cached:
                log.info("cache_hit_during_circuit_open", results=len(cached))
                return cached
            log.error("circuit_open_no_cache", collection=self.collection)
            return []  # Graceful empty response, not an exception

        # --- Build the search call ---
        def _do_search() -> list[dict]:
            # Qdrant example; adapt for Pinecone/Weaviate/Milvus
            from qdrant_client.models import Filter, FieldCondition, MatchValue

            query_filter = None
            if tenant_id:
                query_filter = Filter(must=[
                    FieldCondition(
                        key="tenant_id",
                        match=MatchValue(value=tenant_id),
                    )
                ])

            hits = self.raw.query_points(
                collection_name=self.collection,
                query=query_vector,
                query_filter=query_filter,
                limit=top_k,
                score_threshold=score_threshold,
            ).points

            return [
                {"id": h.id, "score": h.score, "payload": h.payload}
                for h in hits
            ]

        # --- Execute with retry + backoff ---
        try:
            results = retry_with_backoff(
                _do_search,
                max_retries=self.max_retries,
                retryable_exceptions=(ConnectionError, TimeoutError, Exception),
            )
            elapsed_ms = (time.time() - start) * 1000
            self.breaker.record_success()

            # Cache for future degradation fallback
            self.cache.put(query_vector, results)

            # Feed drift detector
            for r in results:
                self.drift_detector.record_distance(r["score"])

            log.info("search_ok",
                     collection=self.collection,
                     top_k=top_k,
                     results=len(results),
                     elapsed_ms=round(elapsed_ms, 1),
                     tenant_id=tenant_id)

            return results

        except Exception as exc:
            elapsed_ms = (time.time() - start) * 1000
            self.breaker.record_failure()

            log.error("search_failed",
                      collection=self.collection,
                      error=str(exc),
                      elapsed_ms=round(elapsed_ms, 1))

            # Graceful degradation: return cached results if available
            cached = self.cache.get(query_vector)
            if cached:
                log.info("degraded_to_cache", results=len(cached))
                return cached

            return []  # Empty, not an exception -- caller decides what to show

    def upsert_with_model_version(
        self,
        vectors: list[list[float]],
        payloads: list[dict],
        ids: list[str],
        model_version: str,
    ):
        """
        Upsert with mandatory model_version tagging.
        Enables safe embedding model migration:
          1. Dual-write with new model_version
          2. Background re-embed historical vectors
          3. Query-time version-aware routing or linear projection adapter
          4. Drop old version after full re-embed
        """
        from qdrant_client.models import PointStruct

        for payload in payloads:
            payload["model_version"] = model_version

        points = [
            PointStruct(id=id_, vector=vec, payload=pay)
            for id_, vec, pay in zip(ids, vectors, payloads)
        ]

        def _do_upsert():
            self.raw.upsert(
                collection_name=self.collection,
                points=points,
            )

        try:
            retry_with_backoff(_do_upsert, max_retries=self.max_retries)
            log.info("upsert_ok",
                     collection=self.collection,
                     count=len(points),
                     model_version=model_version)
        except Exception as exc:
            self.breaker.record_failure()
            log.error("upsert_failed",
                      collection=self.collection,
                      count=len(points),
                      error=str(exc))
            raise
```

### 5.2 Hybrid Search with RRF and Reranking

```python
"""
Hybrid search pipeline: BM25 + dense vector + Reciprocal Rank Fusion + reranking.
Runnable against Qdrant (with native hybrid) or any BM25 + vector DB combination.
"""

from dataclasses import dataclass


@dataclass
class SearchResult:
    doc_id: str
    text: str
    score: float = 0.0  # RRF fused score or reranker score


def reciprocal_rank_fusion(
    result_lists: list[list[SearchResult]],
    k: int = 60,
) -> list[SearchResult]:
    """
    Reciprocal Rank Fusion (Cormack et al., 2009).

    Merges N ranked lists by rank position, not score.
    This is critical: BM25 scores and cosine similarity scores are
    drawn from different distributions -- direct score combination
    is the most common architectural mistake in hybrid search.

    RRF score for document d = SUM over lists L of: 1 / (k + rank_L(d))
    """
    fused_scores: dict[str, float] = {}
    doc_map: dict[str, SearchResult] = {}

    for result_list in result_lists:
        for rank, result in enumerate(result_list, start=1):
            fused_scores[result.doc_id] = (
                fused_scores.get(result.doc_id, 0.0)
                + 1.0 / (k + rank)
            )
            doc_map[result.doc_id] = result

    ranked = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)

    return [
        SearchResult(
            doc_id=doc_id,
            text=doc_map[doc_id].text,
            score=score,
        )
        for doc_id, score in ranked
    ]


def cross_encoder_rerank(
    query: str,
    candidates: list[SearchResult],
    top_k: int = 5,
    model_name: str = "cross-encoder/ms-marco-MiniLM-L-12-v2",
) -> list[SearchResult]:
    """
    Neural reranking with a cross-encoder model.
    Adds 100-300ms latency but lifts Precision@5 by 15-25%.
    The cross-encoder sees (query, document) pairs jointly --
    far more precise than bi-encoder dot-product scoring.
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
    Full hybrid search pipeline:
      1. BM25 sparse retrieval (top-100)
      2. Dense vector retrieval (top-100)
      3. RRF fusion (by rank, k=60)
      4. Cross-encoder reranking on fused shortlist (optional, +100-300ms)
    """
    # Stage 1: parallel retrieval
    bm25_results = bm25_search_fn(query, first_stage_k)
    vector_results = vector_search_fn(query_vector, first_stage_k)

    # Stage 2: rank fusion
    fused = reciprocal_rank_fusion(
        [bm25_results, vector_results],
        k=60,
    )

    # Stage 3: neural reranking (optional)
    if use_reranker:
        return cross_encoder_rerank(query, fused[:50], top_k=final_k)

    return fused[:final_k]
```

### 5.3 Embedding Model Migration with Dual-Write and Version Routing

```python
"""
Safe embedding model migration without downtime.
Handles the 'embedding drift' failure mode -- the silent poison
where two models with the same output dimension produce vectors
in incompatible mathematical spaces.
"""

import numpy as np
from typing import Optional


class EmbeddingMigrator:
    """
    Orchestrates embedding model migration:
      1. Tag every vector with model_version
      2. Dual-write with new model during transition
      3. Background re-embed historical vectors
      4. Query-time linear projection adapter for cross-version queries
      5. Drop old vectors after full re-embed + validation
    """

    def __init__(
        self,
        vecdb_client,  # VectorDBClient from section 5.1
        old_model_version: str,
        new_model_version: str,
        projection_matrix: Optional[np.ndarray] = None,
    ):
        self.client = vecdb_client
        self.old_version = old_model_version
        self.new_version = new_model_version
        # Learned linear projection: old_space -> new_space
        # Train on a paired sample of (old_embedding, new_embedding)
        # using least-squares: W = argmin ||X_new - X_old @ W||^2
        self.projection = projection_matrix

    def learn_projection(
        self,
        old_embeddings: np.ndarray,  # (N, D) from old model
        new_embeddings: np.ndarray,  # (N, D) from new model
    ):
        """
        Learn linear projection from old embedding space to new.
        Recovers 95-99% retrieval performance during migration window.
        Uses ordinary least squares: W = (X_old^T X_old)^-1 X_old^T X_new
        """
        W, residuals, rank, sv = np.linalg.lstsq(
            old_embeddings, new_embeddings, rcond=None
        )
        self.projection = W

        # Validate: cosine similarity between projected old and actual new
        projected = old_embeddings @ W
        cosine_sims = np.array([
            np.dot(p, n) / (np.linalg.norm(p) * np.linalg.norm(n))
            for p, n in zip(projected, new_embeddings)
        ])
        median_sim = float(np.median(cosine_sims))

        from __main__ import log  # reuse structured logger
        log.info("projection_learned",
                 samples=old_embeddings.shape[0],
                 median_cosine_similarity=round(median_sim, 4))

        if median_sim < 0.90:
            log.warn("projection_quality_low",
                     median_sim=round(median_sim, 4),
                     recommendation="Consider increasing sample size "
                                    "or using nonlinear adapter")

        return W

    def adapt_query_vector(self, query_vector: np.ndarray) -> np.ndarray:
        """
        If query is embedded with the new model but old vectors exist,
        project the query into the old space for cross-version search.
        """
        if self.projection is None:
            raise ValueError("Projection matrix not learned. "
                             "Call learn_projection() first.")
        # Inverse projection: new_space -> old_space
        # Use pseudo-inverse for non-square or rank-deficient W
        W_inv = np.linalg.pinv(self.projection)
        return query_vector @ W_inv

    def validate_migration(
        self,
        benchmark_queries: list[np.ndarray],
        benchmark_ground_truth: list[list[str]],
        top_k: int = 10,
    ) -> dict:
        """
        Validate recall on a fixed benchmark set before and after migration.
        This is the only way to confirm migration did not degrade quality.
        """
        hits = 0
        total = 0

        for query_vec, truth_ids in zip(benchmark_queries, benchmark_ground_truth):
            results = self.client.search(
                query_vector=query_vec.tolist(),
                top_k=top_k,
            )
            retrieved_ids = {r["id"] for r in results}
            hits += len(set(truth_ids[:top_k]) & retrieved_ids)
            total += min(top_k, len(truth_ids))

        recall = hits / total if total > 0 else 0.0
        return {
            "recall_at_k": round(recall, 4),
            "k": top_k,
            "queries_evaluated": len(benchmark_queries),
        }
```

---

## 6. Architectural System Design Scenarios

### Scenario 1: Multi-Tenant Product Search for 500M Embeddings

**Problem statement.** A B2B e-commerce platform serves 2,000 merchant tenants. Each tenant has 50K-5M product embeddings (500M total across all tenants). Requirements: <50ms p99 latency, strict tenant isolation (no cross-tenant data leakage), hybrid search (keyword + semantic), cost target <$5,000/month infrastructure.

**Architecture:**

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                           API GATEWAY / AUTH                                     │
│  JWT validation ──▶ extract tenant_id ──▶ rate limiting per tenant              │
└───────────────────────────────────┬──────────────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼──────────────────────────────────────────────┐
│                          QUERY COORDINATOR                                       │
│                                                                                  │
│  ┌────────────────────┐  ┌─────────────────────┐  ┌──────────────────────────┐  │
│  │ Tenant Router       │  │ Embedding Service    │  │ Query Classifier         │  │
│  │ (tenant_id ──▶      │  │ (Cohere embed-v4;   │  │ (keyword-heavy ──▶ BM25  │  │
│  │  shard set via      │  │  shared vector space │  │  weight boost;           │  │
│  │  consistent hash    │  │  invariant enforced) │  │  semantic ──▶ vector     │  │
│  │  of tenant_id)      │  │                      │  │  weight boost)           │  │
│  └─────────┬──────────┘  └──────────┬───────────┘  └──────────┬───────────────┘  │
└────────────┼────────────────────────┼──────────────────────────┼──────────────────┘
             │                        │                          │
┌────────────▼────────────────────────▼──────────────────────────▼──────────────────┐
│                        SEARCH CLUSTER (5 nodes)                                   │
│                                                                                   │
│  ┌─────────────────┐  ┌─────────────────┐        ┌─────────────────┐            │
│  │ Node 1           │  │ Node 2           │  ...   │ Node 5           │            │
│  │ Shards 1-20      │  │ Shards 21-40     │        │ Shards 81-100    │            │
│  │ ~100M vectors    │  │ ~100M vectors    │        │ ~100M vectors    │            │
│  │                  │  │                  │        │                  │            │
│  │ IVF-PQ index     │  │ IVF-PQ index     │        │ IVF-PQ index     │            │
│  │ (48 sub-quant,   │  │                  │        │                  │            │
│  │  8-bit, ~48 bytes│  │                  │        │                  │            │
│  │  per vector)     │  │                  │        │                  │            │
│  │                  │  │                  │        │                  │            │
│  │ BM25 shard       │  │ BM25 shard       │        │ BM25 shard       │            │
│  │ (Tantivy)        │  │ (Tantivy)        │        │ (Tantivy)        │            │
│  │                  │  │                  │        │                  │            │
│  │ Payload index    │  │ Payload index    │        │ Payload index    │            │
│  │ (tenant_id,      │  │ (tenant_id,      │        │ (tenant_id,      │            │
│  │  category,       │  │  category,       │        │  category,       │            │
│  │  price_range)    │  │  price_range)    │        │  price_range)    │            │
│  └─────────────────┘  └─────────────────┘        └─────────────────┘            │
│                                                                                   │
│  Replication factor: 2 (Raft consensus for writes)                               │
│  Shard count: 100 (1 shard per ~5M vectors)                                     │
└───────────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│                         RESULT PIPELINE                                          │
│                                                                                  │
│  Shard results ──▶ RRF (k=60) ──▶ Cross-encoder rerank (top-20 ──▶ top-5)      │
│                                    (budget: 15ms rerank on 20 candidates)        │
└───────────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

```
┌───────────────────────┬──────────────────────────┬──────────────────────────┬──────────────┐
│ Decision              │ Option A                 │ Option B                 │ Chosen       │
├───────────────────────┼──────────────────────────┼──────────────────────────┼──────────────┤
│ Index type            │ HNSW: 98%+ recall, high  │ IVF-PQ: 95% recall,     │ IVF-PQ       │
│                       │ RAM (~900 GB for 500M)   │ ~24 GB RAM (48 sub-q)   │ (cost)       │
├───────────────────────┼──────────────────────────┼──────────────────────────┼──────────────┤
│ Tenant isolation      │ Collection-per-tenant:   │ Metadata filter on      │ Metadata     │
│                       │ strongest isolation,     │ tenant_id: shared index, │ filter +     │
│                       │ 2,000 indexes = overhead │ filter correctness      │ integrated   │
│                       │                          │ critical                │ traversal    │
├───────────────────────┼──────────────────────────┼──────────────────────────┼──────────────┤
│ Hosting               │ Managed (Pinecone):      │ Self-hosted (Qdrant):   │ Self-hosted  │
│                       │ zero ops, $700+/mo at    │ full control, ~$130/mo  │ (3-10x       │
│                       │ this scale              │ per node x 5            │ cheaper)     │
├───────────────────────┼──────────────────────────┼──────────────────────────┼──────────────┤
│ Hybrid search         │ Manual BM25 + vector     │ Native hybrid (Qdrant   │ Native       │
│                       │ composition: two systems │ v1.10 Query API): single│ hybrid       │
│                       │ to maintain              │ system                  │              │
├───────────────────────┼──────────────────────────┼──────────────────────────┼──────────────┤
│ Consistency           │ Strong (Raft sync):      │ Eventual (async):       │ Eventual     │
│                       │ higher write latency     │ faster writes, brief    │ (product     │
│                       │                          │ stale reads acceptable  │ search is    │
│                       │                          │                         │ not ACID)    │
└───────────────────────┴──────────────────────────┴──────────────────────────┴──────────────┘
```

**Latency budget breakdown:**

```
┌──────────────────────────┬──────────┐
│ Stage                    │ Budget   │
├──────────────────────────┼──────────┤
│ Network (API ──▶ cluster)│ 5ms      │
├──────────────────────────┼──────────┤
│ Fan-out + ANN search     │ 25ms     │
├──────────────────────────┼──────────┤
│ RRF merge                │ 2ms      │
├──────────────────────────┼──────────┤
│ Cross-encoder rerank     │ 15ms     │
├──────────────────────────┼──────────┤
│ Response serialization   │ 3ms      │
├──────────────────────────┼──────────┤
│ TOTAL                    │ 50ms p99 │
└──────────────────────────┴──────────┘
```

**Decision rationale.** IVF-PQ is chosen over HNSW because 500M vectors at full-precision HNSW would require ~900 GB RAM (600 GB vectors + 1.5x graph overhead), making the infrastructure budget unattainable. IVF-PQ at 48 sub-quantizers compresses to ~24 GB across the cluster, fitting the $5K/mo target. The 3-5% recall loss is offset by the cross-encoder reranker on the final shortlist -- the reranker catches precision errors that the compressed index misses, achieving effective recall above 97% on the final top-5 results.

Metadata filtering with integrated traversal is chosen over collection-per-tenant because 2,000 separate collections would fragment the index and create 2,000x the operational surface area. The risk of cross-tenant leakage via filter misconfiguration is mitigated by enforcing tenant_id injection at the API gateway layer -- the search service never accepts a raw tenant_id from the client; it extracts it from the JWT claim.

---

### Scenario 2: Embedding Model Migration Without Downtime for a RAG Platform

**Problem statement.** A SaaS RAG platform has 50M document chunks embedded with Cohere embed-v3 (1024-dim). The team needs to migrate to embed-v4 (also 1024-dim -- same dimension, different space) for 12% retrieval quality improvement. Requirements: zero downtime, no query quality degradation during migration, re-embedding budget of 25 billion tokens, migration window of 2 weeks.

**Architecture:**

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                           MIGRATION ORCHESTRATOR                                 │
│                                                                                  │
│  Phase tracker: DUAL_WRITE ──▶ RE_EMBED ──▶ VALIDATE ──▶ CUTOVER ──▶ CLEANUP   │
│  Progress: 0/50M chunks re-embedded | model_version: v3 (old), v4 (new)         │
└───────────────────────────────────┬──────────────────────────────────────────────┘
                                    │
                 ┌──────────────────┼──────────────────────┐
                 │                  │                      │
┌────────────────▼──────┐  ┌───────▼────────┐  ┌──────────▼───────────────────────┐
│ INGESTION PATH        │  │ RE-EMBED WORKER │  │ QUERY PATH                       │
│                       │  │                 │  │                                  │
│ New documents ──▶     │  │ Background job: │  │ Query arrives ──▶ embed with v4  │
│ Dual-write:           │  │ Read chunks     │  │                                  │
│  1. Embed with v4     │  │ from source     │  │ ┌─────────────────────────────┐  │
│  2. Store with        │  │ store ──▶       │  │ │ Version-Aware Router        │  │
│     model_version=v4  │  │ embed with v4   │  │ │                             │  │
│  3. Keep old v3       │  │ ──▶ upsert with │  │ │ If chunk has v4 embedding:  │  │
│     vector in place   │  │ model_ver=v4    │  │ │   use directly              │  │
│                       │  │ ──▶ mark chunk  │  │ │                             │  │
│                       │  │ as migrated     │  │ │ If chunk has v3 only:       │  │
│                       │  │                 │  │ │   apply linear projection   │  │
│                       │  │ Rate: ~3.5M     │  │ │   W (v3 ──▶ v4 space)      │  │
│                       │  │ chunks/day      │  │ │   recovers 95-99% quality   │  │
│                       │  │ (25B tokens     │  │ │                             │  │
│                       │  │  / 14 days)     │  │ │ Merge results from both     │  │
│                       │  │                 │  │ │ via RRF                     │  │
│                       │  │ Checkpoint:     │  │ └─────────────────────────────┘  │
│                       │  │ resume from     │  │                                  │
│                       │  │ last committed  │  │ ┌─────────────────────────────┐  │
│                       │  │ batch on crash  │  │ │ Drift Detector              │  │
│                       │  │                 │  │ │ KL divergence on distance   │  │
│                       │  │                 │  │ │ distributions, per-version  │  │
│                       │  │                 │  │ │ Alert if shift > threshold  │  │
│                       │  │                 │  │ └─────────────────────────────┘  │
└───────────────────────┘  └─────────────────┘  └──────────────────────────────────┘
                                                            │
                                                            ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│                         VALIDATION GATE                                          │
│                                                                                  │
│  Fixed benchmark set (1,000 queries with known-good results)                     │
│  Automated recall comparison: v4-only recall >= v3 baseline recall               │
│  If PASS: proceed to cutover (drop v3 vectors, remove projection adapter)       │
│  If FAIL: halt, investigate, increase re-embed sample for projection training   │
└───────────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix:**

```
┌───────────────────────┬────────────────────────────┬────────────────────────────┬──────────────┐
│ Decision              │ Option A                   │ Option B                   │ Chosen       │
├───────────────────────┼────────────────────────────┼────────────────────────────┼──────────────┤
│ Migration approach    │ Big-bang: re-embed all 50M │ Gradual with adapter:      │ Gradual      │
│                       │ before cutover. Simple     │ dual-write + background    │ (zero        │
│                       │ but requires 2-week        │ re-embed + linear          │ downtime)    │
│                       │ degraded quality window.   │ projection during gap.     │              │
├───────────────────────┼────────────────────────────┼────────────────────────────┼──────────────┤
│ Cross-version query   │ Ignore old vectors during  │ Linear projection adapter: │ Projection   │
│ handling              │ transition (recall drops    │ 95-99% quality recovery,   │ adapter      │
│                       │ proportional to un-migrated│ adds ~0.5ms compute        │              │
│                       │ fraction)                  │                            │              │
├───────────────────────┼────────────────────────────┼────────────────────────────┼──────────────┤
│ Re-embedding budget   │ All at once: spike cost,   │ Rate-limited: 3.5M/day,    │ Rate-limited │
│                       │ API rate limits, burst     │ steady cost, respects API  │              │
│                       │ pricing                    │ quotas                     │              │
├───────────────────────┼────────────────────────────┼────────────────────────────┼──────────────┤
│ Validation            │ Spot-check: manual review  │ Automated benchmark:       │ Automated    │
│                       │ of sample queries          │ 1,000 queries, recall      │ benchmark    │
│                       │                            │ comparison, automated      │              │
│                       │                            │ pass/fail gate             │              │
├───────────────────────┼────────────────────────────┼────────────────────────────┼──────────────┤
│ Rollback strategy     │ None: forward-only         │ Keep v3 vectors until      │ Keep v3      │
│                       │                            │ validation passes, single  │ until gate   │
│                       │                            │ config flag to revert      │ passes       │
│                       │                            │ query routing              │              │
└───────────────────────┴────────────────────────────┴────────────────────────────┴──────────────┘
```

**Decision rationale.** The gradual migration with a linear projection adapter is chosen because the big-bang approach requires either downtime or accepting degraded retrieval quality for the entire 2-week re-embedding window. The projection adapter (a learned linear transformation from v3 space to v4 space, trained on ~100K paired embeddings) recovers 95-99% of retrieval performance on un-migrated vectors, making the quality impact during migration nearly imperceptible to users.

The most dangerous aspect of this migration is that both models output 1024-dimensional vectors -- there is no dimension mismatch to cause a crash. Without the model_version tag on every vector, the system would silently mix embeddings from incompatible spaces, producing garbage retrieval results with no error signal. The model_version metadata field is the single most important safeguard. The validation gate on a fixed benchmark set is the only way to confirm the migration succeeded -- monitoring production metrics alone cannot distinguish between "model improved retrieval" and "model broke retrieval in ways that don't surface in aggregate metrics."

The 25B token re-embedding cost (~$500-$2,500 depending on provider) is a real budget line item that must be approved before migration begins. This is the hidden cost that teams routinely underestimate -- the embedding generation cost can match or exceed the vector database bill itself.
