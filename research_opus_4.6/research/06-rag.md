# Research: RAG (Retrieval-Augmented Generation)

**Date researched**: 2026-08-21
**Sources consulted**: 42

---

## 1. System Topology & Mechanics

### 1.1 Ingestion Pipeline: Document Parsing

Production RAG begins with document parsing, which converts raw files (PDFs, HTML, DOCX, slides) into structured text elements. Key tools:

- **Unstructured.io**: Uses YOLOX layout model to detect tables, images, and document sections from PDFs. Partitions elements by type (paragraph, table, title, image) before chunking [35].
- **MinerU / Docling**: Used by LightRAG for multimodal document parsing -- handles images, tables, and formulas natively [22].
- **Multi-vector retriever pattern** (LangChain): Decouples retrieval references from synthesis documents. Table summaries are embedded for search, but raw tables are passed to the LLM for generation. For images, three strategies exist: (a) embed images via CLIP, (b) generate text summaries via VLM and embed those, (c) hybrid of both [35].

### 1.2 Chunking Strategies

**Fixed-size (character) splitting**: Divides text into N-character chunks with overlap. Simple but causes "disruptions in flow, mixing of topics, sentences split mid-word" [40]. Appropriate only for uniform, unstructured text.

**Recursive chunking**: Uses ordered separators (`\n\n` -> `\n` -> `.` -> ` `) to split progressively. Reduces mid-sentence breaks but remains unaware of semantic boundaries like tables and headers [40].

**Structure-aware (smart) chunking**: Operates on pre-identified document elements. Four sub-strategies:
- **By character**: Combines elements up to max size.
- **By title**: Preserves section boundaries, prevents topic mixing.
- **By page**: Keeps page content separate.
- **By similarity**: Groups topically similar elements using embedding cosine similarity [40].

**Parent-child chunking**: Small child chunks are embedded for retrieval precision, but the parent chunk (larger context window) is returned for generation. This resolves the chunk-size trade-off: small chunks retrieve accurately, large chunks generate coherently.

**Contextual chunking** (Anthropic, 2024): Uses an LLM to prepend 50-100 tokens of document-level context to each chunk before embedding. Example: `"The company's revenue grew by 3%"` becomes `"This chunk is from an SEC filing on ACME corp's Q2 2023 performance; previous quarter revenue was $314M. The company's revenue grew by 3%."` Reduces top-20 retrieval failure rate by 35% with contextual embeddings alone, 49% combined with contextual BM25, and 67% with reranking added [30].

**Optimal chunk size**: Starting point of ~250 tokens (~1,000 characters). Hard max is the embedding model's context window. Smaller chunks improve retrieval precision; larger chunks preserve context but dilute the embedding representation [40].

**Overlap**: Reduces abrupt cutoffs but increases redundancy. Treat as a tunable hyperparameter requiring empirical evaluation [40].

### 1.3 Embedding Models

| Model | Dimensions | Max Tokens | Price/1M Tokens | Notable Features |
|-------|-----------|------------|-----------------|------------------|
| OpenAI `text-embedding-3-small` | 1536 (configurable) | 8,192 | $0.02 | 62.3% MTEB. Matryoshka-style dim reduction [4] |
| OpenAI `text-embedding-3-large` | 3072 (configurable) | 8,192 | $0.13 | 64.6% MTEB. Shortened to 256d still beats ada-002 [4] |
| OpenAI `text-embedding-ada-002` | 1536 | 8,192 | $0.10 | Legacy. 61.0% MTEB [4] |
| Cohere `embed-v4.0` | 256/512/1024/1536 | 128,000 | $0.10 | Multimodal (text+images+PDFs). 100+ languages [7, 8] |
| Cohere `embed-english-v3.0` | 1024 | 512 | $0.10 | English only. Image support [7] |
| Cohere `embed-multilingual-v3.0` | 1024 | 512 | $0.10 | 100+ languages [7] |
| Voyage `voyage-4-large` | 1024 (256-2048) | 32,000 | $0.12 | Best Voyage quality. int8/binary quantization [11, 12] |
| Voyage `voyage-4` | 1024 (256-2048) | 32,000 | $0.06 | General-purpose. Batch API at 33% discount [11, 12] |
| Voyage `voyage-4-lite` | 1024 (256-2048) | 32,000 | $0.02 | Latency/cost optimized [11, 12] |
| Voyage `voyage-code-4` | 1024 (256-2048) | 32,000 | $0.12 | Code retrieval specialized [11, 12] |
| BAAI `BGE-M3` | 1024 | 8,192 | Free (open) | Dense + sparse + ColBERT multi-vector. 100+ languages. MIT license [26] |
| BAAI `BGE-EN-ICL` | 4096 | 8,192 | Free (open) | 7B params (Mistral). In-context learning for embeddings. SOTA on BEIR [25] |
| Jina `jina-embeddings-v5-text-small` | 1024 | 32,000 | Tiered | 67.0 MMTEB. Matryoshka (truncate to 32d). Late chunking [20] |
| Jina `jina-embeddings-v5-omni-small` | 1024 | 32,000 | Tiered | Multimodal: text, images, audio, video, PDFs. ~1.74B params [20] |
| Jina `jina-embeddings-v4` | 1024+ | 32,000 | Free (research) | 3.8B params. Late-interaction (ColBERT). Task-specific LoRA [20] |

**Key insight**: Cohere embed-v4 uniquely offers a 128K-token context window, enabling whole-document embedding without chunking for documents under ~100 pages. Most other models cap at 8K-32K tokens [7].

### 1.4 Vector Databases

**Pinecone** (Managed serverless):
- Architecture: Serverless indexes with dense + sparse vector support. Namespaces for tenant isolation. BM25 full-text search built in (no external model required -- Pinecone handles tokenization, IDF, length normalization) [9].
- Indexing: Proprietary (based on IVF/graph-based methods). Supports integrated embedding (upsert raw text, Pinecone embeds at index time) [9].
- Hybrid search: Single-index dense+sparse via alpha weighting (`combined = alpha * dense + (1 - alpha) * sparse`). Recommended default: `alpha=0.75` for natural-language queries. Alternative: document schema with BM25 string fields alongside dense vectors [16].

**Qdrant** (Open-source, managed cloud):
- Architecture: HNSW index with payload filtering integrated into graph traversal (single-pass, not pre/post-filter). ACORN algorithm for high-cardinality multi-filter queries [3].
- Named vectors: Multiple vectors per point with independent configs (e.g., separate `image` and `text` vectors) [37].
- Sparse vectors: Native support for lexical/BM25-style retrieval [3].
- Quantization: float16, uint8, turbo4 (4-bit) vector datatypes [37].
- Distributed: Configurable shard count (recommend 12 for flexibility). Replication factor 2+ for production [3].

**Weaviate** (Open-source, managed cloud):
- Index types: HNSW (default), Flat (small collections/multi-tenancy), Dynamic (auto-switches flat->HNSW at 10K objects), HFresh (cluster-based, centroid HNSW in memory + posting lists on disk with 1-bit RQ compression) [14].
- Memory: HNSW requires 2-12KB per vector in RAM. At 1M vectors: 2-12GB. At 100M vectors: 200-1200GB [14].
- Replication: Leaderless for data (AP system), Raft-based for cluster metadata. Tunable consistency: ONE, QUORUM, ALL [32].

**Milvus** (Open-source, Zilliz Cloud):
- 4-layer architecture: Access (stateless proxy) -> Coordinator (brain) -> Worker Nodes (streaming, query, data) -> Storage (etcd + object storage + WAL) [6].
- Indexes: FLAT, IVF_FLAT, IVF_SQ8, IVF_PQ, HNSW, DiskANN, GPU_IVF_FLAT, GPU_IVF_PQ, SPARSE_INVERTED_INDEX [27].
- WAL options: Woodpecker (cloud-native, zero-disk), Kafka, or Pulsar [6].
- Fully disaggregated storage and compute. Horizontal scaling of stateless worker nodes on Kubernetes [6].

**pgvector** (PostgreSQL extension):
- Index types: HNSW (better query performance, slower builds, no training needed) and IVFFlat (faster builds, lower query performance, requires existing data) [15].
- Max dimensions: 16,000 (2,000 for HNSW indexes). Supports float, halfvec, bit, sparsevec types [15].
- Distance metrics: L2, inner product, cosine, L1, Hamming, Jaccard [15].
- ACID compliant. 32TB per non-partitioned table. Replication via WAL. Binary quantization for faster builds at scale [15].

**ChromaDB** (Open-source, cloud):
- Dense, sparse, and hybrid search. Multi-modal (text, images, audio). Metadata filtering. Full-text and regex search [24].
- Deployment: In-process SDK, self-hosted, or Chroma Cloud (managed). Apache 2.0 license [24].
- Best for: Prototyping, small-to-medium datasets. Less battle-tested at billion-scale compared to Pinecone/Milvus/Qdrant.

### 1.5 Retrieval Pipeline

**Query transformation**: Techniques to improve retrieval quality before hitting the vector DB:
- **HyDE (Hypothetical Document Embeddings)**: LLM generates a hypothetical answer, which is then embedded and used for retrieval instead of the raw query.
- **Multi-query**: LLM generates multiple reformulations of the query; results are merged.
- **Step-back prompting**: Generates a more abstract/general query to retrieve broader context.

**Hybrid search (dense + sparse/BM25)**: Combines semantic similarity (dense vectors) with exact lexical matching (sparse/BM25). BM25 excels where embeddings fail -- e.g., finding `"Error code TS-999"` requires exact string matching [30]. Fusion methods include alpha-weighted combination [16] and Reciprocal Rank Fusion (RRF).

**Reranking**: Cross-encoders process query-passage pairs jointly, providing superior relevance scoring but at higher latency (must process each pair, not just encode independently) [23].
- **Cohere Rerank**: v4 Pro ($4/1K searches), v4 Fast ($2/1K searches), v3.5 ($2/1K searches). Each search = 1 query + up to 100 docs. Docs >500 tokens are auto-chunked. 100+ languages. Relevance scores normalized 0-1 [17, 28].
- **Voyage Rerank**: rerank-2.5 ($0.05/1M tokens), rerank-2.5-lite ($0.02/1M tokens) [12].
- **Cross-encoder models** (open-source): `cross-encoder/ms-marco-MiniLM-L6-v2` for text. Multimodal: `Qwen/Qwen3-VL-Reranker-2B`. MS Marco models return logits (use sigmoid for 0-1 normalization) [23].

**Contextual compression**: After retrieval, an LLM extracts only the relevant portions of retrieved chunks, reducing context window usage and improving generation focus.

### 1.6 Knowledge Graphs & GraphRAG

**Microsoft GraphRAG**: Structured, hierarchical approach to RAG [10]:
1. Text chunked into TextUnits.
2. LLM extracts all entities, relationships, and claims.
3. Hierarchical community detection via Leiden algorithm.
4. Bottom-up community summaries generated.

Query modes: Global search (holistic reasoning via community summaries), Local search (entity-focused fan-out to neighbors), DRIFT search (community-enriched local), Basic search (standard vector) [10].

Addresses two weaknesses of naive RAG: (a) failure to "connect the dots" across disparate information linked by shared attributes, and (b) inability to holistically summarize semantic concepts over large corpora [10].

**LightRAG**: Dual-level retrieval combining knowledge graphs with vector embeddings [22]:
- Extracts entities and relationships via LLM, stores graph + vectors separately.
- Retrieval modes: Local (entity-focused), Global (cross-document), Hybrid, Naive (chunk-based), Mix (all combined, default).
- Benchmarks vs GraphRAG: Agriculture 54.8% vs 45.2%, CS 52.0% vs 48.0%, Legal 52.8% vs 47.2% [22].
- Drastically fewer LLM calls during indexing and querying. Supports incremental updates [22].
- Backends: PostgreSQL, MongoDB, Neo4j, Milvus, OpenSearch [22].

### 1.7 Agentic RAG

**Self-RAG** (Akari et al., 2023): Trains a single LM to adaptively retrieve passages on-demand using special reflection tokens [18]:
- Model learns when retrieval is necessary (not every query needs it).
- Generates, then self-critiques both retrieved passages and its own outputs.
- 7B/13B parameter versions outperform ChatGPT and retrieval-augmented Llama2-chat on open-domain QA, reasoning, and fact verification [18].

**Corrective RAG (CRAG)**: Adds explicit relevance evaluation after retrieval [21]:
1. Retrieve documents from vector index.
2. LLM evaluates each document's relevance (binary: yes/no).
3. If any documents are irrelevant, query is transformed and web search is performed via Tavily AI.
4. Response generated from relevant retrieved docs + web results.
5. Creates self-correcting retrieval that falls back to the web when local knowledge is insufficient [21].

**Adaptive RAG**: Routes queries to different retrieval strategies based on complexity -- simple queries skip retrieval entirely, moderate queries use single-step retrieval, complex queries use multi-step iterative retrieval.

---

## 2. Token Economics & NFR Metrics

### 2.1 Embedding Costs per 1M Tokens

| Provider | Model | Cost/1M Tokens | Notes |
|----------|-------|---------------|-------|
| OpenAI | text-embedding-3-small | $0.02 | Cheapest major provider [4] |
| OpenAI | text-embedding-3-large | $0.13 | 6.5x cost of small for ~4% MTEB gain [4] |
| OpenAI | text-embedding-ada-002 | $0.10 | Legacy, not recommended [4] |
| Cohere | embed-v4.0 | $0.10 | 128K context, multimodal [28] |
| Cohere | embed-v3.0 | $0.10 | Same price, 512-token limit [28] |
| Voyage | voyage-4-large | $0.12 | Best Voyage quality [12] |
| Voyage | voyage-4 | $0.06 | Good quality/cost balance [12] |
| Voyage | voyage-4-lite | $0.02 | Matches OpenAI small pricing [12] |
| Voyage | voyage-code-4 | $0.12 | Code-specialized [12] |
| BAAI | BGE-M3 / BGE-EN-ICL | Free | Self-hosted compute costs only [25, 26] |
| Jina | v5 models | Tiered (package-based) | Free tier: 100 RPM, 100K TPM [20] |

**Cost comparison for 1M documents (avg 1,000 tokens each = 1B tokens)**:
- OpenAI small: $20
- Voyage lite: $20
- Voyage standard: $60
- Cohere v4: $100
- OpenAI large: $130

### 2.2 Vector DB Pricing

**Pinecone (Serverless)** [13]:
- Storage: $0.33/GB/month
- Read units: $16-18/M (Standard), $24-27/M (Enterprise)
- Write units: $4-4.50/M (Standard), $6-6.75/M (Enterprise)
- Free tier: 2GB storage, 1M reads, 2M writes/month
- Builder plan: $20/month (10GB, 5M writes, 2M reads)

**Qdrant Cloud** [5]:
- Free tier: 0.5 vCPU, 1GB RAM, 4GB disk
- Standard: Usage-based (vCPU + RAM + storage hourly). 99.5% SLA
- Premium: Minimum spend. SSO, private VPC. 99.9% SLA (99.95% multi-AZ)
- Self-hosted: Free (open-source, Apache 2.0)

**Milvus**: Open-source (self-hosted, free). Zilliz Cloud for managed service [6].

**pgvector**: Free (PostgreSQL extension). Infrastructure costs only. Leverages existing Postgres expertise [15].

**ChromaDB**: Open-source (Apache 2.0). Chroma Cloud for managed service [24].

### 2.3 Reranker Costs

| Provider | Model | Pricing |
|----------|-------|---------|
| Cohere | Rerank v4 Pro | $4.00/1K searches [28] |
| Cohere | Rerank v4 Fast | $2.00/1K searches [28] |
| Cohere | Rerank v3.5 | $2.00/1K searches [28] |
| Voyage | rerank-2.5 | $0.05/1M tokens [12] |
| Voyage | rerank-2.5-lite | $0.02/1M tokens [12] |
| Open-source | cross-encoder/ms-marco-MiniLM-L6-v2 | Free (self-hosted) [23] |

Note: Cohere defines a "search" as 1 query + up to 100 documents. Documents >500 tokens are auto-split, each chunk counting as a separate document [28].

### 2.4 Latency Benchmarks

> Limited public data available for precise p50/p95/p99 latencies. Most vendors do not publish these granularly. The following are assembled from documentation and community benchmarks.

**Embedding generation**:
- OpenAI API: ~50-200ms per batch of 100 short texts [inferred from API response times]
- Self-hosted (BGE-M3 on GPU): ~10-50ms per batch [inferred]
- Jina API rate limits suggest ~100-500ms per request at scale [20]

**Vector search** (approximate, varies with index size and configuration):
- HNSW on Qdrant/Weaviate: 1-10ms for <1M vectors, 5-50ms for 10M+ vectors [inferred from architecture docs]
- pgvector HNSW: 5-20ms for <1M vectors [inferred]
- DiskANN (Milvus): Higher latency than in-memory HNSW but handles billion-scale with disk-based indexing [27]

**Reranking**:
- Cross-encoder (self-hosted, GPU): 50-200ms for 20 documents
- Cohere Rerank API: 100-500ms per request [inferred]
- LightRAG notes reranking adds 1-2s latency [22]

### 2.5 Chunk Size vs Retrieval Quality Trade-offs

- Starting recommendation: ~250 tokens per chunk [40]
- Smaller chunks (100-200 tokens): Higher retrieval precision, risk losing context
- Medium chunks (250-500 tokens): Best general-purpose balance
- Large chunks (500-1000 tokens): More context for generation, coarser retrieval signal
- Contextual chunking (Anthropic): Adds 50-100 tokens of context per chunk, improving retrieval without increasing chunk size [30]
- Anthropic testing showed 20 retrieved chunks outperformed 5 or 10 for generation quality [30]

---

## 3. Distributed Resilience & State

### 3.1 Vector DB Replication & Sharding

**Qdrant** [3]:
- Sharding: Collections split into shards, each an independent store. Recommend 12 shards for flexibility across 1-12 nodes.
- Replication: RF=1 non-production only. RF=2 recommended for production (availability + load balancing). RF>2 for read-heavy throughput.
- Custom shards supported for data locality control.

**Weaviate** [32]:
- Leaderless replication for data (availability-first, AP system). Eventual consistency by default.
- Raft consensus for cluster metadata (leader-based, strongly consistent) since v1.25.
- Tunable consistency per operation: ONE (fastest), QUORUM (n/2+1 -- balanced), ALL (synchronous, strongest).
- Replication and sharding are independent: e.g., 3 replicas x 3 shards = 9 total shards.
- When writes use ALL, the system becomes synchronous. Otherwise writes are async from the client's perspective.

**Milvus** [6]:
- Streaming Nodes provide shard-level consistency and fault recovery.
- WAL (Woodpecker/Kafka/Pulsar) ensures data durability before acknowledgment.
- Fully disaggregated storage/compute: stateless workers on Kubernetes for horizontal scaling and disaster recovery.
- etcd for metadata, MinIO/S3/Azure Blob for object storage.

**Pinecone** [9]:
- Serverless architecture abstracts away sharding/replication decisions.
- Namespaces partition data within indexes for isolation and faster lookups.
- Backups/collections: Static, non-queryable copies consuming only storage.

**pgvector** [15]:
- Replication via standard PostgreSQL WAL streaming replication.
- Partitioning for horizontal data distribution.
- ACID compliance inherited from PostgreSQL.

### 3.2 Index Consistency Models

| Database | Data Consistency | Metadata Consistency | Notes |
|----------|-----------------|---------------------|-------|
| Weaviate | Tunable (ONE/QUORUM/ALL) | Strong (Raft) | AP system, availability-first [32] |
| Qdrant | Configurable write_consistency_factor | Consensus protocol | RF and write concern per collection [37] |
| Milvus | Shard-level via WAL | Strong (etcd) | Woodpecker zero-disk WAL option [6] |
| pgvector | Strong (ACID) | Strong (PostgreSQL) | Synchronous replication available [15] |
| Pinecone | Eventual (managed) | Managed | Abstracted from user [9] |

### 3.3 Embedding Model Fallback Chains

Production systems should implement fallback chains for embedding generation resilience:
1. Primary: API-based model (e.g., OpenAI text-embedding-3-large)
2. Secondary: Alternative API (e.g., Cohere embed-v4 or Voyage)
3. Tertiary: Self-hosted model (e.g., BGE-M3 on GPU)

**Critical constraint**: All models in the fallback chain must produce compatible embeddings (same dimensionality and similar vector space) OR the system must maintain separate indexes per embedding model. Mixing embeddings from different models in the same vector space degrades retrieval quality severely.

### 3.4 Ingestion Pipeline Durability

**Exactly-once processing**: Use message queues (Kafka, Pulsar, SQS) with consumer group offsets. Milvus natively supports Kafka/Pulsar as WAL backends [6].

**Dead-letter queues**: Route failed document parsing or embedding generation to DLQ for manual review. Critical for handling malformed PDFs, unsupported file types, or API rate-limit errors.

**Idempotent upserts**: Most vector DBs support upsert semantics (Pinecone, Qdrant, Milvus) -- retrying a failed write with the same ID overwrites safely rather than duplicating.

**Checkpointing**: For large-corpus ingestion, track progress per document/chunk to enable resume-from-failure without reprocessing the entire corpus.

### 3.5 Cache Layers

**Embedding cache**: Cache embedding vectors keyed by content hash. Avoids re-embedding identical or unchanged documents during re-indexing. Saves API costs on repeated ingestion runs.

**Query result cache**: Cache (query_embedding, top_k_results) pairs with TTL. Effective for popular/repeated queries. Redis or Memcached as cache layer.

**Semantic cache**: Cache based on query similarity rather than exact match. If a new query is within cosine-similarity threshold of a cached query, return the cached result. GPTCache and LangChain SemanticCache implement this pattern. Trade-off: risks returning stale or slightly mismatched results.

---

## 4. Enterprise Security & Governance

### 4.1 Document-Level and Chunk-Level Access Control

**Metadata-based filtering**: The most common pattern. Store ACL metadata (user roles, group IDs, tenant IDs) as payload/metadata on each chunk. At query time, apply metadata filters to restrict results to authorized chunks.

- **Pinecone**: Metadata filters with `$and`, `$or`, `$not`, `$exists` operators. Namespace-level isolation for multi-tenancy [9].
- **Qdrant**: Payload indexes integrated into HNSW graph traversal (single-pass filtering, not pre/post-filter). Supports collection-level metadata synced via consensus [3, 37].
- **Weaviate**: Metadata filtering at query time. Multi-tenant mode with per-tenant flat indexes [14].
- **pgvector**: Leverages PostgreSQL row-level security (RLS) policies. Most mature ACL model since it inherits full SQL access control [15].

**Pre-filtering vs post-filtering**: Pre-filtering (applied during vector search) is more secure but can reduce recall if the filtered subset is small. Qdrant's integrated payload filtering during HNSW traversal is the most architecturally efficient approach [3].

### 4.2 PII Detection in Ingested Documents

Best practice: Run PII detection (Microsoft Presidio, AWS Comprehend, Google DLP) during the ingestion pipeline before embedding. Options:
- **Redact**: Remove PII before embedding (cleanest but loses information).
- **Mask**: Replace with placeholders (`[NAME]`, `[SSN]`) -- preserves structure.
- **Tag**: Store PII presence as metadata for access-control-based filtering without modifying content.

### 4.3 Data Residency & Encryption

**At rest**:
- Pinecone: Encryption at rest with customer-managed keys (Enterprise tier). SOC 2 Type II certified [13].
- Qdrant: Self-hosted gives full control. Cloud offers regional deployment (AWS, Azure, GCP). Private Cloud for air-gapped setups [5].
- Milvus: Self-hosted (full control) or Zilliz Cloud (managed). Data stored in customer's object storage (S3/Azure Blob/MinIO) [6].
- pgvector: Inherits PostgreSQL TDE (Transparent Data Encryption) and disk encryption [15].

**In transit**: All managed services use TLS. Self-hosted deployments require configuring TLS for gRPC/HTTP endpoints.

**Data residency**: Pinecone supports specific cloud regions. Qdrant offers Hybrid Cloud (customer infrastructure + Qdrant management) and Private Cloud (fully customer-controlled) [5]. Milvus can be self-hosted in any region [6].

### 4.4 Audit Trails for Retrieval Decisions

Production RAG systems should log:
- Query text and transformed query variants
- Retrieved chunk IDs, scores, and metadata
- Reranker scores and final chunk selection
- Generated response with source attribution
- User identity and timestamp

This enables debugging hallucinations, investigating retrieval failures, and demonstrating compliance. No vector DB provides this natively -- it must be implemented in the application layer.

### 4.5 Tenant Isolation in Multi-Tenant RAG

**Namespace isolation** (Pinecone): Each tenant gets a namespace within the index. All operations are scoped to one namespace. Lightweight but shares underlying index infrastructure [9].

**Collection-per-tenant** (Qdrant, Milvus): Strongest isolation. Each tenant has a separate collection with independent HNSW indexes, replication, and access control. Higher resource overhead [3].

**Metadata filtering** (any DB): All tenants share one collection. Tenant ID stored as metadata, filtered at query time. Most efficient but requires rigorous filter enforcement to prevent data leakage.

**Weaviate multi-tenancy**: Native support with per-tenant data isolation and flat indexes. Tenants can be activated/deactivated independently. Designed for high-tenant-count scenarios (100K+ tenants) [14].

---

## 5. Production Failure Modes

### 5.1 Embedding Drift (Model Updates Changing Vector Space)

When an embedding model is updated or replaced, the new model produces vectors in a different space. All existing vectors in the index become incompatible. Mitigation strategies:
- **Full re-indexing**: Re-embed entire corpus with the new model. Safe but expensive (time + API costs).
- **Shadow indexing**: Build new index alongside old one. Switch atomically when ready. Qdrant supports atomic alias operations for this [37].
- **Version pinning**: Lock to a specific model version (e.g., `text-embedding-3-small` not `text-embedding-latest`). OpenAI and Cohere maintain stable model versions [4, 7].
- **Matryoshka models**: Models like OpenAI v3 and Jina v5 support dimension truncation. If the vector space is stable within a model version, dimension changes are safe [4, 20].

### 5.2 Stale Index / Document Freshness

Documents change, but the index retains old embeddings. Failure mode: users retrieve outdated information.
- **Incremental updates**: LightRAG and Milvus support incremental index updates without full rebuild [22, 6].
- **Change detection**: Hash document content; re-embed only changed documents.
- **TTL metadata**: Tag chunks with ingestion timestamp. At query time, optionally filter or boost recent documents.
- **Scheduled re-indexing**: For document corpora with known update cycles (e.g., weekly knowledge base refresh).

### 5.3 Retrieval Quality Degradation Under Scale

As index size grows from thousands to millions to billions of vectors:
- HNSW recall degrades if `ef_search` is not tuned upward (more candidates = better recall, slower queries) [14, 15].
- IVF methods require retuning `nlist` and `nprobe` parameters as data distribution shifts [15].
- Memory pressure: HNSW at 100M vectors requires 200-1200GB RAM on Weaviate [14]. Solutions: disk-based indexes (DiskANN on Milvus [27], HFresh on Weaviate [14]), quantization (pgvector binary quantization [15], Qdrant turbo4 [37]).
- Metadata filter selectivity: Highly selective filters (small result sets) can degrade HNSW recall. Qdrant's ACORN algorithm addresses this [3].

### 5.4 Hallucination from Retrieved Context Conflicts

When retrieved chunks contain contradictory information (e.g., different document versions), the LLM may hallucinate a synthesis or arbitrarily choose one. Mitigations:
- **Source deduplication**: Detect near-duplicate chunks during retrieval and keep only the most recent or authoritative version.
- **Conflict detection**: Use LLM to identify contradictions in retrieved context before generation.
- **Source attribution**: Force the LLM to cite which chunk supports each claim, making conflicts visible.
- **RAGAS faithfulness scoring**: Post-hoc evaluation of whether the response is grounded in context [38].

### 5.5 Chunk Boundary Information Loss

Information spanning two chunks is lost when neither chunk alone contains the complete fact. Example: "The CEO" in chunk N and "announced revenue of $5B" in chunk N+1 -- neither chunk alone is useful.
- **Overlap**: Mitigates partially but increases storage and can cause duplicate retrieval [40].
- **Parent-child chunking**: Retrieve child (precision), return parent (completeness).
- **Contextual chunking**: LLM-generated context prepended to each chunk provides cross-boundary information [30].
- **Late chunking** (Jina): Generates token-level embeddings across the full document first, then pools into chunk-level embeddings, preserving cross-chunk context [20].

### 5.6 Reranker Latency Spikes

Cross-encoder rerankers process each query-document pair independently, so latency scales linearly with document count [23]. At 100 documents, a self-hosted cross-encoder may take 1-5 seconds. API-based rerankers (Cohere) are subject to network latency and rate limits.
- **Mitigation**: Limit initial retrieval to top-20-50 candidates. Use a bi-encoder score threshold to pre-filter before reranking.
- **Two-stage reranking**: Fast/cheap reranker (Cohere v4 Fast, Voyage lite) first, then expensive reranker (Cohere v4 Pro) on top-10.
- **Async reranking**: For non-latency-critical applications, offload reranking to background processing.

---

## 6. Enterprise System Design Scenarios

### 6.1 Published Architectures

**Naive RAG pipeline**:
Query -> Embed query -> Vector search (top-K) -> Stuff into prompt -> LLM generates answer.
Weaknesses: No query transformation, no reranking, no relevance evaluation. Retrieval failure rate ~5.7% [30].

**Advanced RAG pipeline** (Anthropic's Contextual Retrieval as exemplar) [30]:
1. **Ingestion**: Chunk documents -> LLM generates context per chunk -> Embed contextual chunks + build BM25 index.
2. **Retrieval**: Embed query -> Hybrid search (contextual embeddings + contextual BM25) -> Retrieve top-150 -> Rerank to top-20.
3. **Generation**: Top-20 contextual chunks stuffed into prompt -> LLM generates.
Retrieval failure rate: ~1.9% (67% reduction vs naive) [30].
Cost: $1.02/1M document tokens for contextual preprocessing (with prompt caching) [30].

**Agentic RAG pipeline**:
1. Query analysis: Classify query complexity (simple/moderate/complex).
2. Simple queries: Direct LLM response (no retrieval).
3. Moderate: Single-step retrieval with CRAG-style relevance check [21].
4. Complex: Multi-step iterative retrieval with planning. Self-RAG-style reflection tokens for retrieval decisions [18].
5. All paths: Faithfulness check on generated response.

**GraphRAG pipeline** (Microsoft) [10]:
1. Ingestion: Chunk -> LLM entity/relationship extraction -> Knowledge graph construction -> Leiden community detection -> Community summary generation.
2. Global queries: Traverse community summaries for holistic reasoning.
3. Local queries: Fan out from entity to neighbors for specific answers.
4. Significantly better at "connecting the dots" across disparate information and holistic summarization.

**LightRAG pipeline** [22]:
- Dual-level: Graph + vector. Five retrieval modes (Local, Global, Hybrid, Naive, Mix).
- Outperforms GraphRAG on multiple benchmarks while using drastically fewer LLM calls.
- Supports incremental updates (GraphRAG requires full rebuild).

### 6.2 Naive RAG vs Advanced RAG vs Agentic RAG

| Dimension | Naive RAG | Advanced RAG | Agentic RAG |
|-----------|-----------|-------------|-------------|
| Query handling | Direct embedding | Query transformation, HyDE | Adaptive routing, multi-step planning |
| Retrieval | Single-pass vector search | Hybrid search + reranking | Iterative retrieval with relevance checks |
| Context | Raw chunks | Contextual chunks, compression | Self-correcting with web fallback |
| Evaluation | None | Post-hoc metrics | Inline reflection tokens |
| Latency | ~200-500ms | ~500-2000ms | ~2-10s (multiple LLM calls) |
| Retrieval failure rate | ~5.7% | ~1.9% | <1% [inferred] |
| Complexity | Low | Medium | High |
| Cost per query | Low ($0.001-0.01) | Medium ($0.01-0.05) | High ($0.05-0.50) [inferred] |

### 6.3 Evaluation Frameworks

**RAGAS** (Retrieval Augmented Generation Assessment) [29]:
- **Faithfulness**: (Supported claims / Total claims). LLM extracts claims from response, verifies each against context. Score 0-1 [38].
- **Answer Relevancy**: LLM evaluates how relevant the response is to the input question [29].
- **Context Precision**: Evaluates relevance and ranking quality of retrieved chunks [29].
- **Context Recall**: Measures how well retrieved context covers information needed to answer [29].
- **Context Entities Recall**: Entity-level recall from ground truth in retrieved context [29].
- **Noise Sensitivity**: Tests robustness to irrelevant information in context [29].
- Uses LLM-as-judge methodology. Each metric may require 1-3 LLM calls [38].

**DeepEval** [19]:
- 50+ metrics including all RAGAS metrics plus agentic, multi-turn, multimodal, and MCP metrics.
- **RAG-specific**: Answer Relevancy, Faithfulness, Contextual Recall/Precision/Relevancy, Hallucination [19].
- **Agentic-specific**: Task Completion, Tool Correctness, Goal Accuracy, Step Efficiency, Plan Adherence [19].
- **G-Eval**: Research-backed LLM-as-judge for custom criteria [19].
- **DAG**: Graph-based deterministic metric builder [19].
- Pytest integration for CI/CD. Framework-agnostic (works with OpenAI, LangChain, CrewAI, Anthropic, etc.) [19].
- Apache 2.0 license. 17.8K GitHub stars [19].
- Enterprise platform (Confident AI): Dataset management, production monitoring with live traces, MCP server for IDE integration [19].

**Custom metrics** commonly implemented:
- **Answer correctness**: Factual overlap with ground-truth answers (requires labeled dataset).
- **Retrieval hit rate**: Fraction of queries where at least one relevant document is in top-K.
- **Mean Reciprocal Rank (MRR)**: Average 1/rank of first relevant document.
- **nDCG@K**: Normalized Discounted Cumulative Gain at K.

### 6.4 Scale Benchmarks

**Vector search throughput** (approximate, from ANN benchmarks and vendor documentation):
- HNSW (hnswlib): 1,000-10,000 QPS at 95%+ recall on 1M vectors (SIFT-128d) [36]
- IVF (faiss): 500-5,000 QPS depending on nprobe configuration [36]
- DiskANN: Lower QPS than in-memory HNSW but handles billion-scale datasets with disk [27]
- Pinecone serverless: Scales automatically; pricing based on read/write units rather than QPS guarantees [13]

**Index size capacity**:
- Pinecone: No documented upper limit for serverless. Pod-based previously supported up to ~1B vectors per index [9].
- Qdrant: Tested at hundreds of millions of vectors. Disk-based storage for memory-constrained deployments [3].
- Milvus/Zilliz: Designed for billion-scale. DiskANN index for datasets exceeding available RAM [27].
- pgvector: 32TB per non-partitioned table. Practical limit depends on RAM for HNSW index (~2-12KB per vector) [15].

**End-to-end RAG latency** (typical production):
- Embedding query: 20-100ms (API) / 5-20ms (self-hosted GPU)
- Vector search: 5-50ms (HNSW, <10M vectors)
- Reranking (20 docs): 100-500ms (API) / 50-200ms (self-hosted GPU)
- LLM generation: 500-3000ms (depending on model and output length)
- **Total**: 600-3500ms for Advanced RAG, 200-500ms for Naive RAG

---

## Sources

- [1] Lewis et al. (2020), "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks" -- foundational RAG paper
- [2] Gao et al. (2023), "Retrieval-Augmented Generation for Large Language Models: A Survey" (arXiv:2312.10997)
- [3] Qdrant Documentation -- Overview, architecture, HNSW, sharding, replication (https://qdrant.tech/documentation/overview/)
- [4] OpenAI Embeddings Guide -- model specs, MTEB scores, pricing (https://developers.openai.com/api/docs/guides/embeddings)
- [5] Qdrant Pricing -- tiers, SLAs, deployment options (https://qdrant.tech/pricing/)
- [6] Milvus Architecture Overview -- 4-layer design, WAL, worker nodes (https://milvus.io/docs/architecture_overview.md)
- [7] Cohere Embed Documentation -- model specs, dimensions, multilingual (https://docs.cohere.com/docs/cohere-embed)
- [8] Cohere Embed Product Page -- multimodal, compression, deployment (https://cohere.com/embed)
- [9] Pinecone Key Concepts -- serverless, namespaces, hybrid search, metadata (https://docs.pinecone.io/guides/get-started/key-concepts)
- [10] Microsoft GraphRAG -- entity extraction, community detection, query modes (https://microsoft.github.io/graphrag/)
- [11] Voyage AI Embeddings -- model lineup, dimensions, quantization (https://docs.voyageai.com/docs/embeddings)
- [12] Voyage AI Pricing -- cost per 1M tokens, reranker pricing (https://docs.voyageai.com/reference/pricing)
- [13] Pinecone Pricing -- serverless RU/WU, tiers, free plan (https://www.pinecone.io/pricing/)
- [14] Weaviate Vector Index Concepts -- HNSW, Flat, Dynamic, HFresh, memory estimates (https://docs.weaviate.io/weaviate/concepts/vector-index)
- [15] pgvector GitHub -- HNSW, IVFFlat, dimensions, distance metrics, performance (https://github.com/pgvector/pgvector)
- [16] Pinecone Hybrid Search Guide -- alpha weighting, dense+sparse combination (https://docs.pinecone.io/guides/data/understanding-hybrid-search)
- [17] Cohere Rerank Overview -- models, languages, structured data (https://docs.cohere.com/docs/rerank-overview)
- [18] Asai et al. (2023), "Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection" (arXiv:2310.11511)
- [19] DeepEval GitHub -- 50+ metrics, agentic evaluation, CI/CD (https://github.com/confident-ai/deepeval)
- [20] Jina AI Embeddings -- v5 models, Matryoshka, late chunking, multimodal (https://jina.ai/embeddings/)
- [21] LlamaIndex CRAG Workflow -- corrective RAG architecture, web fallback (https://developers.llamaindex.ai/python/examples/workflow/corrective_rag_pack/)
- [22] LightRAG GitHub -- graph+vector RAG, benchmarks vs GraphRAG (https://github.com/HKUDS/LightRAG)
- [23] Sentence-Transformers Cross-Encoder Usage -- reranking, bi-encoder comparison (https://www.sbert.net/docs/cross_encoder/usage/usage.html)
- [24] ChromaDB Documentation -- architecture, search types, deployment (https://docs.trychroma.com/docs/overview/introduction)
- [25] BGE-EN-ICL HuggingFace -- in-context learning embeddings, benchmarks (https://huggingface.co/BAAI/bge-en-icl)
- [26] BGE-M3 HuggingFace -- multi-functionality, multilingual, MIT license (https://huggingface.co/BAAI/bge-m3)
- [27] Milvus Index Documentation -- HNSW, IVF, DiskANN, GPU indexes (https://milvus.io/docs/index-vector-fields.md)
- [28] Cohere Pricing -- embed and rerank model costs (https://cohere.com/pricing)
- [29] RAGAS Documentation -- available metrics overview (https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/)
- [30] Anthropic Contextual Retrieval -- contextual chunks, hybrid search, benchmarks (https://www.anthropic.com/news/contextual-retrieval)
- [31] OpenAI Pricing Page -- embedding costs per 1M tokens (https://developers.openai.com/api/docs/pricing)
- [32] Weaviate Replication Architecture -- leaderless, Raft, tunable consistency (https://docs.weaviate.io/weaviate/concepts/replication-architecture)
- [33] Cohere Rerank API Reference -- max documents, search units (https://docs.cohere.com/reference/rerank)
- [34] Qdrant Collection Configuration -- named vectors, sparse vectors, distance metrics (https://qdrant.tech/documentation/concepts/collections/)
- [35] LangChain Semi-Structured Multi-Modal RAG -- table/image handling, multi-vector retriever (https://www.langchain.com/blog/semi-structured-multi-modal-rag)
- [36] ANN-Benchmarks -- algorithm comparison across datasets (https://ann-benchmarks.com/)
- [37] Qdrant Collections Documentation -- vector datatypes, memory tiers, aliases (https://qdrant.tech/documentation/concepts/collections/)
- [38] RAGAS Faithfulness Metric -- claim extraction, verification, scoring (https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/)
- [39] Unstructured.io -- document parsing, chunking strategies (https://unstructured.io/)
- [40] Unstructured.io Chunking Best Practices -- strategies, optimal sizes, overlap (https://unstructured.io/blog/chunking-for-rag-best-practices)
- [41] Yan et al. (2024), "Corrective Retrieval Augmented Generation" (arXiv:2401.15884)
- [42] Edge et al. (2024), "From Local to Global: A Graph RAG Approach to Query-Focused Summarization" -- Microsoft GraphRAG paper
