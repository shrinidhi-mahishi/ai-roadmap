# Research: How Vector Databases Work
**Date researched**: 2026-09-29
**Sources consulted**: 28

---

## 1. System Topology & Mechanics

### Architecture: Three-Layer Stack

Every vector database decomposes into three layers:

| Layer | Responsibility | Key Concern |
|-------|---------------|-------------|
| **Storage** | Vector persistence, compression, durability | Cost, crash recovery, rebuild time |
| **Index** | Graph/cluster structure for fast ANN search | Speed vs. accuracy trade-off |
| **Query** | Request routing, filtering, ranking, fusion | Latency, filtering strategy |

**Storage layer.** A single 1,536-dim vector (float32) consumes 6 KB. At 100M vectors, raw storage alone is ~600 GB before index overhead. Quantization compresses this:
- Scalar quantization (SQ8): float32 to int8, ~4x reduction.
- Binary quantization (BQ): float32 to 1-bit, ~32x reduction, minor recall loss.
- Product quantization (PQ): splits vector into sub-vectors, quantizes each independently, ~32x compression with 3-5% recall loss.

**Memory vs. disk trade-off.** Maximum query speed requires indexes in RAM. Tiered storage keeps hot vectors in memory, cold on disk. Hybrid approaches keep index structure in RAM but fetch raw vectors from SSD on demand (DiskANN pattern).

**Durability.** Synchronous disk writes guarantee crash safety but add write latency. Asynchronous writes risk data loss on crash. HNSW index rebuild from WAL after crash can take minutes to hours depending on dataset size.

### Index Algorithms

#### HNSW (Hierarchical Navigable Small World)
The dominant production algorithm. Used by Pinecone, Qdrant, Weaviate, Milvus, pgvector.

**Mechanism:** Builds a multi-layer graph inspired by skip lists. Top layers are sparse (express lanes for long-range jumps), bottom layers are dense (fine-grained local search). Each vector appears at layer 0; probability of promotion to layer L decreases exponentially.

**Search process:** Enter at top layer, greedily navigate to closest node, descend one layer, repeat until layer 0. Final neighborhood search at layer 0 yields k nearest neighbors.

**Key parameters:**
| Parameter | Effect | Typical Range |
|-----------|--------|--------------|
| `M` (max connections) | Higher = better recall, more RAM, slower build | 16-64 |
| `ef_construction` | Candidate list during build; higher = better graph quality | 100-400 |
| `ef_search` | Candidate list during query; higher = better recall, slower query | 50-200+ |

**Characteristics:**
- Supports incremental inserts/deletes without full rebuild (key advantage over IVF).
- Memory overhead: ~1.5x raw vector size for graph edges.
- Typical recall: 98%+ at ef_search=100.
- Build time is expensive: O(N log N) distance computations + graph updates.

#### IVF (Inverted File Index)
Partitions vector space into Voronoi cells via k-means clustering.

**Mechanism:** Build phase runs k-means to create `nlist` cluster centroids (100-10,000). Each vector assigned to nearest centroid. Query phase identifies `nprobe` closest centroids, performs exact search only within those clusters.

**Trade-offs vs. HNSW:**
- Lower memory footprint (no graph edges).
- Does NOT support efficient incremental inserts -- new vectors may require cluster reassignment or full rebuild.
- Less recall at equivalent latency compared to HNSW.
- Better suited for static, large datasets where memory is the constraint.

#### IVF-PQ (IVF + Product Quantization)
Composite index combining IVF's space partitioning with PQ's vector compression.

**Mechanism:** IVF narrows search space to relevant clusters. PQ compresses vectors within each cluster by splitting each vector into sub-vectors and replacing each with a codebook entry. Distance computed using lookup tables on compressed codes.

**Scale:** 500M vectors with 48 sub-quantizers at 8 bits each fit in ~24 GB RAM. Trade-off: 3-5% recall loss vs. full-precision HNSW.

**Best for:** Billion-scale, memory-constrained deployments.

#### DiskANN (Microsoft Research)
Graph-based index designed for SSD-resident operation.

**Mechanism:** Builds a Vamana graph (similar to HNSW but single-layer with better degree control). PQ-compressed vectors and adjacency list stay in RAM for graph traversal. Full-precision vectors live on NVMe SSD, fetched only for final rerank of top candidates.

**Performance:** Sub-millisecond latency at 95%+ recall on billion-scale datasets using a fraction of HNSW's RAM. 5,000+ QPS on a single node with SSD. Powers Bing semantic search and Azure Cosmos DB vector index.

**Filtered-DiskANN** (Gollapudi et al., WWW 2023) extends edge construction to respect label sets so metadata filters don't destroy recall.

#### ScaNN (Google Research)
Optimized for high-throughput inner-product search.

**Mechanism:** Uses anisotropic quantization (quantization-aware training that preserves inner-product ordering better than standard PQ). Two-stage pipeline: fast approximate scoring with compressed codes to produce candidates, then precise reranking with full-precision vectors.

**Best for:** High-QPS inner-product workloads (recommendation systems, Google-scale retrieval).

#### Algorithm Decision Matrix

| Algorithm | Best For | Memory | Recall | Scale | Incremental Inserts |
|-----------|----------|--------|--------|-------|-------------------|
| **HNSW** | Best recall, in-memory | High (~1.5x overhead) | 98%+ | Millions | Yes |
| **IVF-Flat** | Static datasets, memory-conscious | Low | Good | 10M-100M | No (rebuild) |
| **IVF-PQ** | Billion-scale, RAM-constrained | Very low (~32x compression) | Good (3-5% loss) | Billions | No (rebuild) |
| **DiskANN** | Billion-scale, single-node SSD | Low (compressed in RAM) | 95%+ | Billions | Limited |
| **ScaNN** | High-throughput inner product | Moderate | High | Millions-Billions | Limited |

### Query Processing

#### ANN (Approximate Nearest Neighbor) Search
All production vector databases return approximate results. Brute-force exact search is O(n) -- at 10M vectors, a single query takes ~1,000 seconds. ANN indexes reduce this to sub-linear time, typically <5ms at 10M scale.

#### Hybrid Search: BM25 + Vector + Reranking
Pure vector search underperforms hybrid on most production workloads. Embeddings treat "E-4521" and "E-4522" as nearly identical; BM25 treats them as completely different terms.

**Three-stage production architecture:**
1. **Stage 1a:** BM25 sparse retrieval (top-100 to top-1000).
2. **Stage 1b:** Dense vector ANN retrieval (top-100 to top-1000).
3. **Fusion:** Reciprocal Rank Fusion (RRF) merges both lists by rank (not score -- BM25 and cosine scores are incomparable).
4. **Stage 2:** Cross-encoder neural reranking on fused shortlist (adds 100-300ms latency).

**Benchmark:** On WANDS e-commerce benchmark, tuned hybrid reaches 0.7497 NDCG -- 7.4% lift over BM25 alone (0.6983) or vector alone (0.6953). On financial documents, hybrid + reranking achieves Recall@5 of 0.816.

**Common architectural mistake:** Using a single weighted formula to combine BM25 and cosine scores instead of rank-based fusion.

#### Pre-Filter vs. Post-Filter
- **Post-filter (HNSW default):** Walk graph, collect k*N candidates, drop those failing filter. At low filter selectivity (<1%), recall collapses because graph exhausts before finding k survivors.
- **Pre-filter:** Apply metadata filter first, then run ANN on filtered subset. Works well when filter is selective. Qdrant and Weaviate integrate payload filtering directly into the search traversal.
- **Benchmark:** At 0-1% selectivity (rare tenant), median QPS drops to 180 with p99 reaching 220ms. At 25-100% selectivity (broad filters), QPS climbs to 2,400 with p99 at 38ms.

**Vendor support:**
| Database | Hybrid Search | Filter Integration |
|----------|--------------|-------------------|
| Weaviate | Native (BM25 + vector, selectable fusion) | Integrated into traversal |
| Qdrant | Native (v1.10 Query API) | Payload index, integrated |
| Milvus | Native | Partition-key filtering |
| Pinecone | Alpha-weighted single-index | Metadata filtering |
| pgvector | Manual composition required | SQL WHERE clauses |

---

## 2. Token Economics & NFR Metrics

### Cost Per Million Vectors (Managed Cloud, 1536-dim)

| Scale | Pinecone Serverless | Weaviate Cloud | Qdrant Cloud | pgvector (RDS) |
|-------|-------------------|----------------|-------------|----------------|
| **10M vectors** | ~$70/mo | ~$135/mo | ~$65/mo | ~$45/mo |
| **100M vectors** | $700+/mo | Varies (BQ helps) | Significantly less | <$100/mo (self-hosted) |

**Pricing models differ fundamentally:**
- **Pinecone:** Consumption-based. Storage $0.30/GB/mo + read units $16/million + write units $4/million. Cheap when idle; query-heavy apps surprise you.
- **Qdrant Cloud:** Capacity-based. Pay for reserved RAM/CPU/disk. No per-query charge. High-QPS apps get cheaper per query as they scale. Self-hosted on a 16GB VPS: ~$30-50/mo for millions of vectors.
- **Weaviate Cloud:** Dimension-based. ~$0.095 per million dimensions stored per month.

**Tipping point:** Above 60-80M queries/month, self-hosted Qdrant or Weaviate on fixed-cost VPS undercuts Pinecone Serverless by 3-10x.

**Real-world example:** A consumer AI startup migrated from 50M vectors on Pinecone at $1,200/mo to self-hosted Qdrant on a 64GB Hetzner instance at $130/mo.

**Hidden costs (actual bill averages 2.5-4x pricing page estimate):**
- Egress fees: $0.08-0.09/GB on AWS.
- Index rebuild compute: $12-40 per 10M vectors.
- HNSW storage overhead: ~1.5x raw vector size.
- Embedding generation cost can match or exceed the database bill itself.
- Re-embedding 50M documents: ~25 billion tokens (a real budget line item).

**Quantization as cost lever:**
- Qdrant Binary Quantization: up to 40x memory reduction, making large-scale deployments dramatically cheaper.
- Weaviate BQ: reduces 100M vectors from ~$1,459/mo to ~$45/mo on dimension-based billing.
- Qdrant 1.5-bit/2-bit quantization: up to 64x memory reduction.

### Latency Benchmarks

#### Large-Scale: 50M Vectors, 768-dim, 99% Recall (Tiger Data Benchmark)

| Metric | Qdrant | pgvector+pgvectorscale |
|--------|--------|----------------------|
| p50 | 30.75ms | 31.07ms |
| p95 | 36.73ms | 60.42ms |
| p99 | 38.71ms | 74.60ms |
| QPS | 41.47 | 471.57 |

Qdrant wins on tail latency (48% better p99). pgvectorscale wins on throughput (11.4x higher QPS on single node).

#### 7-System Evaluation (arXiv:2608.12812, SIFT1M)

| System | p50 Latency | p99/p50 Ratio | QPS | Notes |
|--------|------------|---------------|-----|-------|
| **Qdrant** | 4.55ms | 1.85 | 216 | Best latency-throughput balance |
| **FAISS** | - | - | 866 | Throughput leader (library, not DB) |
| **Weaviate** | - | - | - | >99% out-of-box recall |
| **Milvus** | - | - | - | Best at high-dim (0.971 recall at 960D) |
| **pgvector** | Higher | 1.63 (tightest) | 154 | Most consistent tail behavior |

#### Multi-Process Throughput (p99 < 100ms Budget)

| System | QPS |
|--------|-----|
| Weaviate | 8,290 |
| pgvector | 4,828 |
| Milvus | 4,725 |
| Qdrant | 1,737 |
| Redis | 1,642 |

#### By Vendor (General Ranges)

| Vendor | Typical Latency | Notes |
|--------|----------------|-------|
| Pinecone (serverless) | 45-80ms | Consistent, zero-tuning |
| Pinecone (pod-based, legacy) | 20-40ms | Better but more ops |
| Qdrant (in-memory) | 15-30ms | Rust performance edge |
| Qdrant (memory-mapped) | 30-60ms | SSD-backed |
| Milvus | 25-50ms | Depends on index type |
| Weaviate | 30-70ms | Hybrid search adds 10-20ms |

**Critical caveat:** At 1M vectors with no filters, almost every system delivers sub-10ms p99 at 99% recall. Differences become material at 10M+ with metadata filters.

### Throughput: QPS at Different Recall Levels

Rankings shift with recall target:
- At 90% recall: all systems deliver high QPS; differences small.
- At 99% recall: QPS drops significantly; parameter tuning and index choice dominate.
- Filter selectivity matters enormously: 1% selectivity drops QPS by >10x vs. broad filters.

---

## 3. Distributed Resilience & State

### Sharding Strategies

Two dominant architectural paradigms:

**Stateful (Qdrant, Weaviate, Vald):** Each worker owns and stores a shard's data + index. Simpler operationally. Rule of thumb for Qdrant: 1 shard per 5M vectors.

**Stateless / Compute-Storage Separation (Milvus, Vespa):** Workers are stateless compute nodes. Data lives in object storage (S3). Index loaded into cache on demand. Enables independent scaling of compute and storage.

**Sharding methods:**
- Hash-based: Deterministic routing by vector ID. Uniform distribution but no range queries.
- Consistent hashing: Minimizes rebalancing on node add/remove.
- Semantic-aware: [Emerging] Uses IVF coarse centroids for top-level routing, HNSW within each shard. Combines sharding efficiency of IVF with local search speed of HNSW.

**Query fan-out trade-off triangle:** Higher recall requires wider search (more shards), which conflicts with low latency and low cost. Distributed vector search is fundamentally an optimization over this triangle.

### Replication and Consistency Models

**Leader-follower model:** Writes go to leader, propagated to followers (sync or async).
- Synchronous replication: Strong consistency, slower writes.
- Asynchronous replication: Faster writes, risk of temporary inconsistency.

**Raft consensus:** Used by both Qdrant and Milvus for replica coordination. Qdrant offers point-in-time consistency guarantees and ACID-compliant operations.

**Unique vector DB challenges:**
- Large index structures complicate replication (replicate raw vectors for durability, build index separately on each node, vs. replicate entire index).
- Index divergence: Two replicas of the "same" HNSW graph can produce different neighbor sets for the same query (different insert order = different graph topology).

### Index Rebuild and Compaction

- HNSW build on large datasets: Can saturate CPU for minutes to hours. Best practice: build on replica, swap in.
- IVF/IVF-PQ: Requires full k-means retraining to incorporate new data. Periodic offline rebuild is standard.
- Milvus 2.6: Replaced Kafka/Pulsar dependency with Woodpecker (custom WAL on object storage), reducing operational complexity.
- Rebuild storms: Multiple background maintenance jobs (graph optimization, shard rebalancing, compaction) running concurrently cause cluster-wide resource contention. Mitigation: stagger jobs, enforce concurrency limits.

### Database Architecture Comparison

| Aspect | Pinecone | Qdrant | Weaviate | Milvus |
|--------|---------|--------|----------|--------|
| **Architecture** | Serverless, compute-storage separated | Single binary or Docker, Rust | Docker Compose, Go | K8s-native, disaggregated nodes |
| **Sharding** | Automatic, hidden | Manual or auto, 1 shard/5M vectors | Automatic | Hash/range, proxy-managed |
| **Replication** | Managed | Raft-based, configurable factor | Built-in | Raft for consistency |
| **Consistency** | Eventual (managed) | Point-in-time, ACID | Eventual | Tunable (strong/bounded/eventual) |
| **Ops complexity** | None (managed) | Low (single binary) | Medium (Docker) | High (K8s required) |

---

## 4. Enterprise Security & Governance

### Multi-Tenancy Isolation Patterns

Multi-tenancy is critical for SaaS RAG platforms. If tenant isolation fails in a vector database, another customer's knowledge base gets surfaced directly into AI responses -- a semantic-level data leak with no relational-DB equivalent.

**Isolation approaches:**
- **Namespace/partition isolation:** Pinecone namespaces, Milvus partitions, Qdrant collections. Warning: Pinecone namespaces are NOT security boundaries.
- **Collection-per-tenant:** Strongest isolation but highest overhead (separate index per tenant).
- **Metadata filtering:** Single shared index with tenant_id metadata filter. Relies on filter correctness; misconfigured filter = cross-tenant leak.
- **Weaviate native multi-tenancy:** Purpose-built tenant isolation with per-tenant data lifecycle management. Strongest multi-tenant story among open-source options.

**Resource isolation:** Quotas on compute, memory, and storage prevent one tenant from monopolizing shared infrastructure.

### Encryption at Rest and in Transit

| Capability | Pinecone | Weaviate | Qdrant | Milvus |
|-----------|---------|----------|--------|--------|
| **Encryption at rest** | AES-256 | AES-256 | AES-256 | Configurable |
| **Encryption in transit** | TLS 1.3 | TLS 1.3 | TLS 1.3 | TLS |
| **Customer-managed keys** | Enterprise tier | Limited | Self-hosted only | Self-hosted only |
| **Field-level encryption** | No | No | No | No |

**Embedding inversion risk:** A zero-shot technique (arXiv:2504.00147, April 2025) achieved meaningful text recovery from embeddings without training on the target model. Practical implication: if an attacker can query your embedding store, they can reconstruct source text. Every exposure is a data breach.

**Defense:** The SPARSE framework (arXiv:2602.07090, February 2026) uses dimension-sensitive Mahalanobis noise targeting semantically critical embedding dimensions while minimally affecting retrieval quality.

### Access Control for Vector Operations

**RBAC maturity varies significantly:**
- Weaviate: Added RBAC in v1.29.0. Collection-level roles.
- Milvus: RBAC with partition-level granularity.
- Qdrant: API key auth; instances are insecure by default per official docs.
- Chroma: A 2025 UpGuard survey found 1,170 internet-accessible Chroma instances, ~1/3 exposing production data with no authentication.
- Milvus: CVE-2025-64513 (CVSS 9.3) -- single HTTP header with hardcoded constant bypassed all authentication.

**Best practices:**
- Separate roles: ingestion writer, index maintainer, read-only RAG service, security auditor.
- Short-lived credentials with rotation.
- Least privilege enforcement.
- OWASP LLM08:2025 "Vector and Embedding Weaknesses" added to 2025 LLM Top 10.

---

## 5. Production Failure Modes

### 9 Documented Failure Modes

#### 1. Hot Shards
Semantic clustering (support tickets, error logs, billing) causes non-uniform shard load. Detection: shard load skew ratio >3-5x, single centroid receiving >40% of queries. Manifests as p95/p99 spikes isolated to one shard.

#### 2. Centroid Collapse (IVF)
Domain evolution causes centroids to misrepresent the space. One centroid absorbs 20-50x more vectors than others. Early sign: teams continually increasing `nprobe` to maintain recall. Recall collapses because true nearest neighbors fall outside checked clusters.

#### 3. Memory Saturation & Fragmentation
Rising p99 latency with no QPS increase. NUMA misses, page faults, GC spikes. Memory pressure rarely produces explicit errors -- it "corrupts performance slowly." Architectural rule: always oversize RAM for vector workloads.

#### 4. Index Divergence Across Replicas
Two replicas of the same HNSW graph produce different neighbor sets for the same query (different insert order = different topology). Extremely dangerous because everything looks healthy until neighbors are inspected across replicas. Detection: replica-to-replica neighbor mismatch rate (any non-zero rate is a problem).

#### 5. Rebuild Storms
Multiple background maintenance jobs (graph optimization, shard rebalancing, compaction) running concurrently cause cluster-wide CPU saturation and p99 spikes. Goes from quiet to catastrophic instantly. Especially common in multi-tenant, high-ingest pipelines.

#### 6. Embedding Drift (The Slow Poison)
When embedding model changes (vendor API update, team upgrade), vectors before and after the change live in incompatible mathematical spaces. Two models with the same output dimension are particularly dangerous -- no dimension mismatch to crash on; system silently returns garbage. Detection: KL divergence on distance distributions, recall degradation on fixed benchmark set.

#### 7. SSD Wear (PQ Hidden Cost)
Product Quantization reduces RAM but dramatically increases SSD I/O. Write amplification factor increases over time. Device failure arrives months ahead of schedule. Monitor TBW (terabytes written) as a first-class metric.

#### 8. Routing Misfires
Drifting centroids, stale routing metadata, replica disagreement cause queries to hit wrong shards. Almost never shows up as an error. Manifests as quality degradation -- wrong neighbors, inconsistent reasoning, reduced RAG quality.

#### 9. Recall/Latency Parameter Drift
Initial index parameters calibrated for dataset size N. As dataset grows (or drift/collapse occurs), engineers increase query-time parameters, accepting either higher latency or quietly degraded recall. The culmination of all other failure modes.

### Overarching Pattern
Vector databases rarely fail with clean outages or loud exceptions. The system drifts: retrieval quality slips, tail latency stretches, agent responses become less trustworthy. Everything still appears "up," but behavior is no longer correct. Blast radius extends to RAG pipelines, agents, copilots, and any workflow depending on semantic retrieval.

### Key Detection Thresholds

| Metric | Alert Threshold |
|--------|----------------|
| Shard load skew ratio | >3-5x |
| Centroid query concentration | >40% to single centroid |
| Centroid population imbalance | >20-50x between centroids |
| Replica neighbor mismatch rate | Any non-zero value |
| p99/p50 latency ratio | >3x (system-dependent) |
| KL divergence (embedding drift) | Rising trend over baseline |

---

## 6. Enterprise System Design Scenarios

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
|--------|-----------|
| Zero operational work | Pinecone |
| Hybrid (keyword + vector) search | Weaviate |
| Lowest query latency, predictable cost | Qdrant |
| Billion-scale, GPU-accelerated | Milvus / Zilliz Cloud |
| Existing Postgres stack, <10M vectors | pgvector |
| Multi-tenant SaaS | Weaviate (strongest tenant isolation) |
| Prototyping / local dev | Chroma or LanceDB |

### Hybrid Search Architecture (Production RAG)

```
User Query
    |
    v
[Query Router] -- identifies query type (keyword-heavy vs. semantic)
    |
    +---> [BM25 Index] --> Top-K sparse results (exact match strength)
    |
    +---> [Vector Index (HNSW)] --> Top-K dense results (semantic strength)
    |
    v
[Reciprocal Rank Fusion (RRF)] -- merges by rank, not score
    |
    v
[Cross-Encoder Reranker] -- neural precision layer on shortlist
    |                       (adds 100-300ms, optional but recommended)
    v
[Top-K Final Results] --> LLM Context Window
```

**Key design decisions:**
- First-stage retrieval: top-100 to top-1000 candidates per retriever.
- RRF parameter k=60 is the standard default.
- Reranking: Voyage rerank-2.5 (Aug 2025) supports instruction-following for domain-specific relevance.
- Precision@5 improvement: 15-25% over pure vector search alone.

### Trade-off Matrices

#### Index Type vs. Requirements

| Requirement | HNSW | IVF-PQ | DiskANN |
|-------------|------|--------|---------|
| Sub-10ms latency | Yes (in-memory) | Possible (compressed) | Yes (SSD + RAM cache) |
| <$100/mo at 100M vectors | No (RAM cost) | Yes (32x compression) | Yes (SSD-backed) |
| Live incremental inserts | Yes | No (rebuild needed) | Limited |
| 99%+ recall | Yes | No (3-5% loss) | Possible (95%+) |
| Single-node billion-scale | No | Yes | Yes |

#### Managed vs. Self-Hosted

| Factor | Managed (Pinecone/Zilliz) | Self-Hosted (Qdrant/Milvus) |
|--------|--------------------------|---------------------------|
| Ops overhead | Zero | Moderate to high |
| Cost at <10M queries/mo | Lower or comparable | Higher (fixed infra) |
| Cost at >80M queries/mo | 3-10x higher | Lower (no per-query fee) |
| Customization | Limited | Full control |
| Data sovereignty | Vendor cloud only | Any cloud or on-prem |
| Vendor lock-in | High | None |
| SLA | Contractual | Self-managed |

#### Consistency vs. Performance

| Model | Write Latency | Read Consistency | Best For |
|-------|-------------|-----------------|---------|
| Async replication | Low | Eventual | High-write, tolerance for stale reads |
| Sync replication (Raft) | Higher | Strong | Financial, compliance workloads |
| Read-your-writes | Medium | Session-level | User-facing apps |

### Interview-Ready Design Questions

**Q: "Design a vector search system for 500M product embeddings with <50ms p99 and multi-tenant isolation."**

Architecture:
1. **Index:** IVF-PQ for memory efficiency at 500M scale, or DiskANN if single-node SSD is viable.
2. **Sharding:** Consistent hashing across N nodes, ~100M vectors/node.
3. **Multi-tenancy:** Partition-key filtering (tenant_id in metadata), NOT namespace-based (not a security boundary in most implementations).
4. **Query path:** Fan-out to relevant shards -> local ANN search within tenant partition -> merge results -> rerank.
5. **Latency budget:** 10ms network + 25ms ANN search + 10ms merge/rerank = ~45ms.
6. **Cost optimization:** Binary quantization for cold tenants, full-precision for premium tenants.

**Q: "How would you handle embedding model migration without downtime?"**

1. Tag every vector with `model_version`.
2. Deploy new model, begin dual-writing (new vectors get new model embeddings).
3. Background re-embedding job for historical vectors (budget: ~25B tokens for 50M docs).
4. Query-time adapter: if query embedding is new-model, transform old-model vectors via learned linear projection (Drift-Adapter recovers 95-99% retrieval performance [inferred from limited studies]).
5. Once re-embedding complete, drop old vectors, remove adapter.
6. Validate: run fixed benchmark set, compare recall before/after.

---

## Sources

1. [System Design Newsletter - What is a Vector Database](https://newsletter.systemdesign.one/p/what-is-a-vector-database)
2. [HNSW Explained - AI/TLDR](https://ai-tldr.dev/learn/embeddings-vector-databases/similarity-search-indexing/hnsw-explained/)
3. [Qdrant - HNSW Indexing Fundamentals](https://qdrant.tech/course/essentials/day-2/what-is-hnsw/)
4. [Weaviate - Vector Indexing Documentation](https://docs.weaviate.io/weaviate/concepts/vector-index)
5. [Milvus - Index Explained](https://milvus.io/docs/index-explained.md)
6. [Milvus - How Does Indexing Work (IVF, HNSW, PQ)](https://milvus.io/ai-quick-reference/how-does-indexing-work-in-a-vector-db-ivf-hnsw-pq-etc)
7. [NVIDIA - Accelerating Vector Search: cuVS IVF-PQ Deep Dive](https://developer.nvidia.com/blog/accelerating-vector-search-nvidia-cuvs-ivf-pq-deep-dive-part-1/)
8. [The Data Quarry - Not All Indexes Are Created Equal](https://thedataquarry.com/blog/vector-db-3/)
9. [Vector Search at Scale (HNSW, IVF-PQ, DiskANN) - HLD Handbook](https://hld.handbook.academy/curriculum/ai-ml-system-design/vector-search-at-scale/)
10. [Firecrawl - Best Vector Databases in 2026](https://www.firecrawl.dev/blog/best-vector-databases)
11. [DEV Community - Pinecone vs Weaviate vs Milvus vs Qdrant 2026](https://dev.to/krunalkanojiya/pinecone-vs-weaviate-vs-milvus-vs-qdrant-which-vector-db-in-2026-26dc)
12. [TensorBlue - Vector Database Comparison 2025](https://tensorblue.com/blog/vector-database-comparison-pinecone-weaviate-qdrant-milvus-2025)
13. [RankSquire - Vector Database Pricing Comparison 2026](https://ranksquire.com/2026/03/04/vector-database-pricing-comparison-2026/)
14. [SpendArk - Vector Database Pricing 2026](https://spendark.com/blog/vector-database-pricing/)
15. [LeanOps - Vector DB Bills Exposed: 2.5-4x Over Budget](https://leanopstech.com/blog/vector-database-cost-comparison-2026/)
16. [Mixpeek - How Much Does a Vector Database Cost](https://mixpeek.com/guides/vector-database-cost-comparison)
17. [Tiger Data - pgvector vs Qdrant](https://www.tigerdata.com/blog/pgvector-vs-qdrant)
18. [arXiv:2608.12812 - Comprehensive Empirical Evaluation of Vector Database Systems](https://arxiv.org/html/2608.12812v1)
19. [Markaicode - Pinecone vs pgvector Benchmark](https://markaicode.com/benchmarks/pinecone-vs-pgvector-benchmark/)
20. [9 Ways Vector Databases Fail in Production](https://aakashsharan.com/vector-database-failure-modes-production/)
21. [Redis Blog - Vector Database Challenges in Production](https://redis.io/blog/common-challenges-working-with-vector-databases/)
22. [Data Platform Advisory - Semantic Rot: Silent Embedding Drift](https://dataplatformadvisory.com/blog/2026/08/17/silent-embedding-drift-vector-search/)
23. [LevelOp - Vector Embedding Models: Versioning and Drift](https://levelop.dev/blog/vector-embedding-models-generation-versioning-drift)
24. [Blockchain Council - Securing and Governing Vector Databases in 2026](https://www.blockchain-council.org/ai/securing-and-governing-vector-databases-privacy-prompt-injection-multi-tenant-access-control/)
25. [Cisco - Securing Vector Databases](https://sec.cloudapps.cisco.com/security/center/resources/securing-vector-databases)
26. [Mirror Security - Vector Database Security](https://mirrorsecurity.io/blog/vector-database-security-key-considerations-for-enterprise-adoption)
27. [Digital Applied - Hybrid Search: BM25, Vector & Reranking Reference 2026](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026)
28. [Milvus - Sharding and Replication in Distributed Vector Databases](https://milvus.io/ai-quick-reference/how-do-distributed-vector-databases-handle-sharding-and-replication)
