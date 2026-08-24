# Module 06: RAG -- Retrieval-Augmented Generation, Vector Search, Knowledge Graphs, and Agentic Retrieval

**Scope**: Ingestion pipelines, chunking strategies, embedding models, vector databases, hybrid search, rerankers, knowledge graphs (GraphRAG, LightRAG), agentic RAG (Self-RAG, CRAG), evaluation frameworks, and production deployment patterns.
**Prerequisite**: Module 01 (LLM Foundations), Module 02 (Context Engineering), familiarity with information retrieval basics (TF-IDF, cosine similarity).
**Last updated**: 2026-08-21 | **Sources consulted**: 42

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                    CONTROL PLANE                                           │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
 │  │  Ingestion Orch.  │  │  Index Manager   │  │  Query Router    │  │  Tenant / RBAC   │  │
 │  │  - Doc queue mgmt │  │  - Collection    │  │  - Naive/Adv/    │  │  - Per-doc ACLs  │  │
 │  │  - Retry/DLQ      │  │    lifecycle     │  │    Agentic route │  │  - Namespace     │  │
 │  │  - Progress track │  │  - Alias swap    │  │  - Model select  │  │    isolation     │  │
 │  │  - Dedup (hash)   │  │  - Reindex sched │  │  - Cost budget   │  │  - PII redaction │  │
 │  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
 └───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┘
             │                    │                    │                    │
 ┌───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┐
 │           ▼                    ▼                    ▼                    ▼                  │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                     DATA PLANE: INGESTION PIPELINE                                 │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │    │
 │  │  │ Doc Parser   │  │ Chunker      │  │ Context      │  │ Embedder     │           │    │
 │  │  │ - Unstructured│  │ - Recursive  │  │ Enricher     │  │ - OpenAI v3  │           │    │
 │  │  │ - MinerU     │  │ - Semantic   │  │ - LLM adds   │  │ - Cohere v4  │           │    │
 │  │  │ - Docling    │  │ - Parent-    │  │   50-100 tok │  │ - Voyage v4  │           │    │
 │  │  │ - PDF/HTML/  │  │   child      │  │   context per│  │ - BGE-M3     │           │    │
 │  │  │   DOCX/Slides│  │ - Contextual │  │   chunk      │  │ - Jina v5    │           │    │
 │  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘           │    │
 │  │         │                 │                  │                 │                    │    │
 │  │         ▼                 ▼                  ▼                 ▼                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │                     METADATA ENRICHMENT                                  │      │    │
 │  │  │  doc_id, chunk_id, source_url, tenant_id, acl_groups, pii_tags,         │      │    │
 │  │  │  ingestion_ts, content_hash, parent_chunk_id                            │      │    │
 │  │  └──────────────────────────────┬───────────────────────────────────────────┘      │    │
 │  └─────────────────────────────────┼──────────────────────────────────────────────────┘    │
 │                                    │                                                       │
 │  ┌─────────────────────────────────┼──────────────────────────────────────────────────┐    │
 │  │                     DATA PLANE: RETRIEVAL PIPELINE                    ▼             │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │    │
 │  │  │ Query Xform  │  │ Hybrid Search│  │ Reranker     │  │ Context      │           │    │
 │  │  │ - HyDE       │  │ - Dense vec  │  │ - Cohere v4  │  │ Assembler    │           │    │
 │  │  │ - Multi-query │  │   (HNSW)    │  │ - Voyage     │  │ - Top-K sel  │           │    │
 │  │  │ - Step-back  │  │ - Sparse BM25│  │ - Cross-enc  │  │ - Dedup      │           │    │
 │  │  │ - Routing    │  │ - RRF / alpha│  │ - Two-stage  │  │ - Compress   │           │    │
 │  │  │   (adaptive) │  │   fusion     │  │   (fast+pro) │  │ - Cite       │           │    │
 │  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘           │    │
 │  │         │                 │                  │                 │                    │    │
 │  │         └─────────────────┴──────────────────┴─────────────────┘                    │    │
 │  │                                    │                                                │    │
 │  └────────────────────────────────────┼────────────────────────────────────────────────┘    │
 │                                       │                                                     │
 │  ┌────────────────────────────────────┼────────────────────────────────────────────────┐    │
 │  │                     TOOL PROXY LAYER                  ▼                             │    │
 │  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐       │    │
 │  │  │ Embedding API │  │ Reranker API  │  │ Web Search    │  │ Knowledge     │       │    │
 │  │  │ Gateway       │  │ Gateway       │  │ Fallback      │  │ Graph Query   │       │    │
 │  │  │ - Rate limit  │  │ - Rate limit  │  │ - Tavily/SERP │  │ - Neo4j/PG    │       │    │
 │  │  │ - Fallback    │  │ - Latency cap │  │ - CRAG path   │  │ - GraphRAG    │       │    │
 │  │  │   chain       │  │ - Two-stage   │  │ - Agentic     │  │ - LightRAG    │       │    │
 │  │  └───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘       │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  PERSISTENCE LAYER                                         │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Vector Database   │  │ BM25 / Sparse     │  │ Knowledge Graph   │  │ Cache Layer    │  │
 │  │ - Pinecone        │  │ Index             │  │ - Neo4j           │  │ - Embedding    │  │
 │  │ - Qdrant (HNSW)   │  │ - Pinecone native │  │ - PostgreSQL     │  │   cache (hash) │  │
 │  │ - Weaviate        │  │ - Elasticsearch   │  │ - Entity store    │  │ - Query result │  │
 │  │ - Milvus          │  │ - Qdrant sparse   │  │ - Community       │  │   cache (TTL)  │  │
 │  │ - pgvector        │  │ - BGE-M3 sparse   │  │   summaries      │  │ - Semantic     │  │
 │  │ - ChromaDB        │  │                   │  │                   │  │   cache (sim)  │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  TELEMETRY PLANE                                           │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Retrieval Metrics │  │ Quality Metrics   │  │ Cost Tracker      │  │ Alerting       │  │
 │  │ - Hit rate @K     │  │ - RAGAS scores    │  │ - Embed API cost  │  │ - Recall drop  │  │
 │  │ - MRR, nDCG@K     │  │ - Faithfulness    │  │ - Rerank cost     │  │ - Latency p99  │  │
 │  │ - Latency per     │  │ - Context prec.   │  │ - LLM gen cost    │  │ - Stale index  │  │
 │  │   pipeline stage  │  │ - Answer relevance│  │ - Storage cost    │  │ - Embed drift  │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

**Ingestion path** (offline):

**Step 1 — Document Parsing**: Raw files (PDF, HTML, DOCX, slides) enter the ingestion queue. Parsers (Unstructured.io, MinerU) extract structured elements — paragraphs, tables, images, titles. Tables are summarized by an LLM for embedding; raw tables are stored for generation (multi-vector retriever pattern).

**Step 2 — Chunking**: Parsed elements are split into retrieval units. Strategy selection depends on document type: recursive chunking for unstructured text, structure-aware chunking for documents with headers/sections, parent-child chunking when both retrieval precision and generation context are needed.

**Step 3 — Context Enrichment**: An LLM prepends 50–100 tokens of document-level context to each chunk (Anthropic's contextual chunking). Example: `"This is from ACME Q2 2023 SEC filing..."` + original chunk. This reduces top-20 retrieval failure rate by 35% (67% with hybrid search + reranking).

**Step 4 — Embedding + Indexing**: Contextual chunks are embedded (OpenAI, Cohere, Voyage, or self-hosted BGE-M3) and upserted into the vector database with metadata (doc_id, tenant_id, ACL groups, PII tags, content_hash). A parallel BM25/sparse index is built for lexical matching.

**Retrieval path** (online):

**Step 5 — Query Transformation**: The query router classifies complexity. Simple queries proceed directly; complex queries undergo HyDE (generate hypothetical answer, embed that), multi-query expansion, or step-back prompting.

**Step 6 — Hybrid Search**: Dense vector search (HNSW) and sparse BM25 search run in parallel. Results are fused via alpha weighting (`combined = 0.75 × dense + 0.25 × sparse`) or Reciprocal Rank Fusion (RRF). Top 50–150 candidates retrieved.

**Step 7 — Reranking**: A cross-encoder reranker (Cohere v4, Voyage, or self-hosted) jointly scores each query–passage pair. Two-stage option: fast reranker on top-50 → pro reranker on top-10. Output: top-K (typically 10–20) passages.

**Step 8 — Context Assembly + Generation**: Selected passages are assembled into the LLM prompt with source attribution. Contextual compression optionally extracts only relevant portions. The LLM generates a response with inline citations.

---

## 2. Core Mechanics & Algorithms

### 2.1 Chunking Strategy Comparison

| Strategy | Mechanism | Strengths | Weaknesses | Best For |
|----------|-----------|-----------|------------|----------|
| **Fixed-size** | Split at N characters with overlap | Simple, predictable size | Splits mid-sentence, mixes topics | Uniform unstructured text |
| **Recursive** | Ordered separators: `\n\n` → `\n` → `.` → ` ` | Reduces mid-sentence breaks | Unaware of semantic boundaries | General-purpose default |
| **Structure-aware** | Split on pre-identified elements (title, page, similarity) | Preserves section boundaries | Requires structured parsing | Documents with headers/sections |
| **Parent-child** | Small child chunks embedded; large parent returned | Precise retrieval + rich generation context | 2× storage, index complexity | Technical documentation |
| **Contextual** (Anthropic) | LLM prepends 50–100 tokens of document context | 35–67% retrieval improvement | LLM cost during ingestion (~$1/1M tokens) | High-value knowledge bases |
| **Late chunking** (Jina) | Full-doc token embeddings pooled into chunks | Preserves cross-chunk context | Requires model support (Jina v5) | Long documents |

**Optimal chunk size**: Start at ~250 tokens. Smaller (100–200) for precision-critical retrieval, larger (500–1000) for context-heavy generation. Always a tunable hyperparameter — empirical evaluation required.

### 2.2 Embedding Model Selection Matrix

| Model | Dims | Max Tokens | $/1M Tokens | MTEB | Key Differentiator |
|-------|------|------------|-------------|------|-------------------|
| OpenAI `text-embedding-3-small` | 1536 | 8,192 | $0.02 | 62.3% | Cheapest major-provider option |
| OpenAI `text-embedding-3-large` | 3072 | 8,192 | $0.13 | 64.6% | Best OpenAI quality; Matryoshka dim reduction |
| Cohere `embed-v4.0` | 256–1536 | 128,000 | $0.10 | — | 128K context; multimodal (text+images+PDFs) |
| Voyage `voyage-4-large` | 1024 | 32,000 | $0.12 | — | Best Voyage quality; int8/binary quantization |
| Voyage `voyage-4` | 1024 | 32,000 | $0.06 | — | Best quality/cost balance |
| Voyage `voyage-4-lite` | 1024 | 32,000 | $0.02 | — | Cost-optimized; matches OpenAI small pricing |
| BGE-M3 | 1024 | 8,192 | Free | — | Dense + sparse + ColBERT; 100+ languages; MIT |
| BGE-EN-ICL | 4096 | 8,192 | Free | SOTA BEIR | 7B params; in-context learning for embeddings |
| Jina `v5-text-small` | 1024 | 32,000 | Tiered | 67.0 MMTEB | Matryoshka (truncate to 32d); late chunking |

**Selection heuristic**:
- Cost-sensitive, English-only → OpenAI small or Voyage lite ($0.02/1M)
- Quality-critical, multilingual → Cohere v4 (128K context, 100+ languages)
- Self-hosted, air-gapped → BGE-M3 (MIT, dense+sparse in one model)
- Code retrieval → Voyage code-4 ($0.12/1M, specialized)

### 2.3 Vector Database Architecture Comparison

| Dimension | Pinecone | Qdrant | Weaviate | Milvus | pgvector |
|-----------|----------|--------|----------|--------|----------|
| **Deployment** | Managed only | Open-source + cloud | Open-source + cloud | Open-source + Zilliz Cloud | PG extension |
| **Index types** | Proprietary | HNSW + payload filter | HNSW, Flat, Dynamic, HFresh | HNSW, IVF, DiskANN, GPU | HNSW, IVFFlat |
| **Hybrid search** | Native (dense + BM25) | Dense + sparse vectors | Dense + BM25 | Dense + sparse | Dense only (BM25 via pg_trgm) |
| **Max dims** | — | — | — | 32,768 | 16,000 (2,000 for HNSW) |
| **Filtering** | Post-filter metadata | Integrated into HNSW traversal (ACORN) | Metadata filter | Scalar + metadata | SQL WHERE clauses (RLS) |
| **Multi-tenancy** | Namespaces | Collection-per-tenant | Native multi-tenant (100K+) | Partitions | Row-level security |
| **Consistency** | Eventual (managed) | Configurable write concern | Tunable (ONE/QUORUM/ALL) | Shard-level (WAL) | Strong (ACID) |
| **Memory/vector** | Managed | Configurable | 2–12 KB (HNSW) | Configurable | ~same as HNSW |
| **License** | Proprietary | Apache 2.0 | BSD-3 | Apache 2.0 | PostgreSQL |

### 2.4 Hybrid Search Fusion Methods

**Alpha-weighted combination** (Pinecone):
```
score = alpha × dense_score + (1 - alpha) × sparse_score
```
Default `alpha=0.75` favors semantic matching. Lower alpha for exact-match-heavy queries (error codes, product IDs).

**Reciprocal Rank Fusion (RRF)**:
```
RRF_score(doc) = Σ 1 / (k + rank_in_list_i)    for each retrieval list i
```
where `k=60` is a standard constant. RRF is parameter-free and robust to score scale differences between dense and sparse retrievers.

**BGE-M3 native hybrid**: Produces dense, sparse, and ColBERT vectors from a single model pass. No separate BM25 index needed — the sparse vector handles lexical matching natively.

### 2.5 Reranking Pipeline

Cross-encoders process (query, passage) pairs jointly — more accurate than bi-encoders but O(n) per query:

```
Retrieval (top-150)  ──▶  Fast Rerank (top-50)  ──▶  Pro Rerank (top-10)  ──▶  LLM Prompt
                          Voyage lite: $0.02/1M       Cohere v4 Pro: $4/1K
                          ~100ms                      ~200ms
```

| Reranker | Pricing | Latency | Best For |
|----------|---------|---------|----------|
| Cohere Rerank v4 Pro | $4.00/1K searches | 200–500ms | Highest quality, production |
| Cohere Rerank v4 Fast | $2.00/1K searches | 100–300ms | Balanced quality/speed |
| Voyage rerank-2.5 | $0.05/1M tokens | 100–300ms | Token-based pricing, cost-effective |
| Voyage rerank-2.5-lite | $0.02/1M tokens | 50–150ms | Budget-conscious |
| Cross-encoder (self-hosted) | Free (compute only) | 50–200ms (GPU) | Air-gapped, high-volume |

### 2.6 Knowledge Graphs: GraphRAG and LightRAG

**Microsoft GraphRAG pipeline**:
1. Chunk documents into TextUnits.
2. LLM extracts entities, relationships, and claims.
3. Leiden algorithm performs hierarchical community detection.
4. Bottom-up community summaries generated.
5. **Query modes**: Global (holistic reasoning via community summaries), Local (entity fan-out to neighbors), DRIFT (community-enriched local), Basic (standard vector).

Addresses two naive-RAG weaknesses: (a) inability to "connect the dots" across documents linked by shared entities, (b) inability to holistically summarize across large corpora.

**LightRAG** — more efficient alternative:
- Dual-level retrieval: knowledge graph + vector embeddings.
- Five modes: Local, Global, Hybrid, Naive, Mix (default).
- Benchmarks vs GraphRAG: Agriculture 54.8% vs 45.2%, CS 52.0% vs 48.0%, Legal 52.8% vs 47.2%.
- Drastically fewer LLM calls during indexing. Supports incremental updates (GraphRAG requires full rebuild).
- Backends: PostgreSQL, MongoDB, Neo4j, Milvus, OpenSearch.

### 2.7 Agentic RAG Patterns

**Self-RAG** (Akari et al., 2023): Trains a single LM to decide when retrieval is necessary using special reflection tokens. The model generates, then self-critiques both retrieved passages and its own outputs. 7B/13B versions outperform ChatGPT on open-domain QA, reasoning, and fact verification.

**Corrective RAG (CRAG)**: Adds explicit relevance evaluation after retrieval:
1. Retrieve from vector index.
2. LLM evaluates each document's relevance (binary yes/no).
3. If any documents are irrelevant → transform query → web search (Tavily).
4. Generate from relevant local + web results.
5. Creates a self-correcting pipeline that falls back to the web when local knowledge is insufficient.

**Adaptive RAG**: Routes by query complexity — simple queries skip retrieval entirely, moderate queries use single-step retrieval, complex queries use multi-step iterative retrieval with planning.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Model: $ per 1K RAG Queries

**Assumptions**: 1 query → embed query + retrieve top-150 + rerank to top-20 + generate with Sonnet 4 ($3/$15 per MTok). Average 500-token query, 250-token chunks, 500-token output.

| Component | Cost/query | Cost/1K queries | Notes |
|-----------|-----------|-----------------|-------|
| Query embedding | $0.00001 | $0.01 | OpenAI small, 500 tokens |
| Vector search | $0.00002 | $0.02 | Pinecone ~$18/M read units |
| Reranking (20 docs) | $0.004 | $4.00 | Cohere v4 Pro, $4/1K searches |
| LLM generation (5K input + 500 output) | $0.0225 | $22.50 | Sonnet 4: $15/M input, $75/M output |
| **Total (Advanced RAG)** | **$0.027** | **$26.53** | |

**Comparison across RAG tiers**:

| RAG Tier | Cost/1K queries | Retrieval failure rate | End-to-end latency |
|----------|----------------|----------------------|-------------------|
| Naive RAG | ~$15 | ~5.7% | 200–500ms |
| Advanced RAG (contextual + hybrid + rerank) | ~$27 | ~1.9% | 500–2,000ms |
| Agentic RAG (multi-step + CRAG) | ~$80–150 | <1% (inferred) | 2–10s |
| GraphRAG | ~$50–100 | Low for cross-doc queries | 1–5s |

**Ingestion cost**: Contextual chunking with Sonnet 4 and prompt caching: ~$1.02 per 1M document tokens. Full corpus of 1M documents (1B tokens): ~$1,020 for contextual enrichment + ~$20–130 for embeddings.

### 3.2 Latency SLA Targets

| Pipeline Stage | p50 | p95 | p99 | Mitigation |
|---------------|-----|-----|-----|------------|
| Query embedding | 30ms | 100ms | 250ms | Batch API; self-hosted GPU fallback |
| Vector search (HNSW, <10M vectors) | 5ms | 20ms | 50ms | Tune `ef_search`; pre-filter with metadata |
| BM25 sparse search | 3ms | 15ms | 40ms | Inverted index in-memory |
| Reranking (20 docs, API) | 150ms | 400ms | 800ms | Two-stage (fast→pro); limit candidate count |
| LLM generation | 500ms | 1,500ms | 3,000ms | Streaming; model routing (Haiku for simple) |
| **Total (Advanced RAG)** | **700ms** | **2,000ms** | **4,000ms** | |

**p50 mitigation**: Pre-compute embeddings for common queries (semantic cache). Limit rerank candidates to top-20.
**p95 mitigation**: Two-stage reranking cuts tail latency. Streaming LLM output to reduce perceived wait.
**p99 mitigation**: Circuit breaker on embedding/reranker APIs with self-hosted fallback. Timeout budget: 4s total, decomposed as 250ms embed + 50ms search + 800ms rerank + 2,900ms generation.

### 3.3 Throughput & Back-Pressure

**Vector search throughput** (approximate, from ANN-benchmarks):

| System | QPS at 95%+ recall (1M vectors, 128d) | Notes |
|--------|---------------------------------------|-------|
| HNSW (hnswlib) | 1,000–10,000 | In-memory, single node |
| IVF (faiss) | 500–5,000 | Depends on `nprobe` |
| DiskANN (Milvus) | Lower QPS | Handles billion-scale with disk |
| Pinecone serverless | Auto-scaled | Pricing via read units, not QPS |

**Back-pressure mechanisms**:
- **Embedding API rate limits**: OpenAI: 10,000 RPM (Tier 5). Cohere: per-plan. Mitigation: request queuing with exponential backoff; pre-compute embeddings during ingestion.
- **Reranker rate limits**: Cohere: per-plan search limits. Mitigation: two-stage reranking reduces expensive API calls; self-hosted fallback.
- **Vector DB query limits**: Connection pool exhaustion under high concurrency. Mitigation: read replicas (Qdrant RF=3+, Weaviate consistency=ONE), query deduplication via semantic cache.

### 3.4 NFR Trade-offs

| NFR | Naive RAG | Advanced RAG | Agentic RAG |
|-----|-----------|-------------|-------------|
| **Availability** | High (simple pipeline, few deps) | Medium (more external APIs) | Lower (multiple LLM calls, web fallback) |
| **RPO** | Index rebuild latency | Same + reranker model version | Same + agent state |
| **RTO** | Minutes (rebuild index from source) | Minutes (index + reranker warmup) | Minutes (same) |
| **Compliance** | Simple audit trail | Richer audit (query xform + rerank scores) | Full agent trace required |

**Key trade-offs**:

- **Cost vs. Quality**: Naive RAG at $15/1K queries has 5.7% retrieval failure. Advanced RAG at $27/1K cuts failure to 1.9%. Agentic RAG at $80–150/1K approaches <1% but 3–6× the cost. The marginal cost per percentage point of improvement increases exponentially.

- **Latency vs. Recall**: HNSW `ef_search` directly trades latency for recall. At `ef_search=50`: 5ms, 95% recall. At `ef_search=200`: 20ms, 99% recall. For most applications, 95% recall at 5ms is preferable to 99% at 20ms — the LLM compensates for imperfect retrieval.

- **Freshness vs. Cost**: Real-time index updates (streaming ingestion) keep the index current but require embedding every document change. Batch re-indexing (daily/weekly) is cheaper but introduces staleness windows. Hybrid: stream high-priority documents, batch the rest.

- **Isolation vs. Efficiency**: Collection-per-tenant provides strongest data isolation but wastes resources (each tenant gets its own HNSW index). Namespace or metadata-filter isolation is more efficient but requires rigorous filter enforcement to prevent cross-tenant data leakage.

---

## 4. Distributed Resilience & Security

### 4.1 Vector Database Replication & Sharding

#### Qdrant
- **Sharding**: Collections split into shards (independent stores). Recommend 12 shards for flexibility across 1–12 nodes.
- **Replication**: RF=2 minimum for production (availability + read load balancing). RF=3+ for read-heavy throughput.
- **Filtering**: ACORN algorithm integrates payload filtering into HNSW graph traversal — single-pass, not pre/post-filter. Critical for high-cardinality multi-filter queries.

#### Weaviate
- **Replication**: Leaderless for data (AP system, availability-first). Raft-based for cluster metadata (strongly consistent).
- **Tunable consistency**: ONE (fastest, eventually consistent), QUORUM (n/2+1, balanced), ALL (synchronous, strongest).
- **Multi-tenancy**: Native support with per-tenant flat indexes. Tenants can be activated/deactivated independently. Designed for 100K+ tenants.
- **Memory**: HNSW requires 2–12 KB per vector in RAM. At 100M vectors: 200–1,200 GB.

#### Milvus
- **Architecture**: 4-layer — Access (stateless proxy) → Coordinator → Worker Nodes (streaming, query, data) → Storage (etcd + S3 + WAL).
- **WAL options**: Woodpecker (cloud-native, zero-disk), Kafka, or Pulsar. Ensures data durability before acknowledgment.
- **Scaling**: Fully disaggregated storage and compute. Stateless workers scale horizontally on Kubernetes.

#### pgvector
- **Replication**: Standard PostgreSQL WAL streaming replication. Synchronous replication available for zero-data-loss.
- **Consistency**: Full ACID compliance. Strongest consistency model of any vector DB.
- **Limits**: 32 TB per non-partitioned table. HNSW indexes limited to 2,000 dimensions.

### 4.2 Circuit Breaker Pattern for RAG Pipeline

#### 4.2.1 State Machine

```
                    success
              ┌───────────────┐
              │               │
              ▼               │
         ┌─────────┐    ┌────┴─────┐    ┌─────────────┐
         │ CLOSED  │───▶│  OPEN    │───▶│ HALF-OPEN   │
         │         │    │          │    │             │
         │ Normal  │    │ Fast-fail│    │ Probe       │
         │ pipeline│    │ to cache │    │ 2 test      │
         │         │    │ /fallback│    │ queries     │
         └─────────┘    └──────────┘    └─────────────┘
              ▲          │       ▲            │
              │          │       │            │
              │          │       └────────────┘
              │          │        probe fails
              │     after 30s
              │     recovery timeout
              │     (30s → 60s → 120s exponential)
              │
              └──────────────────────────────┘
                    2/2 probes succeed
```

**Thresholds**:
- **Closed → Open**: 5 failures within 60s sliding window.
- **Open duration**: 30s recovery timeout with exponential backoff (30s → 60s → 120s on repeated trips).
- **Half-Open → Closed**: 2 consecutive successful probe queries.
- **Half-Open → Open**: Any probe failure immediately re-opens.

#### 4.2.2 Per-Component Breaker Applications

| Component | Failure Type | Class | Fallback Strategy |
|-----------|-------------|-------|-------------------|
| Embedding API | 429/500 errors | **Transient** | Route to backup provider (OpenAI → Voyage → self-hosted BGE-M3) |
| Vector DB | Connection timeout | **Transient** | Serve from query result cache; degrade to BM25-only search |
| Reranker API | Latency spike >2s | **Transient** | Skip reranking, return vector search results directly |
| BM25 index | Index corruption | **Permanent** | Dense-only search until index rebuild completes |
| Knowledge graph | Neo4j unavailable | **Transient** | Fall back to vector-only retrieval (no graph-augmented results) |
| LLM generation | Rate limit / outage | **Transient** | Route to backup model (Sonnet → Haiku); serve cached answer for identical queries |

### 4.3 Failure Taxonomy

| Failure | Class | Detection | Mitigation |
|---------|-------|-----------|------------|
| Embedding drift (model update) | **Permanent** | Cosine similarity between old and new embeddings drops below threshold | Shadow index with new model; atomic alias swap (Qdrant); version pinning |
| Stale index | **Transient** | Content hash mismatch between source doc and indexed chunk | Incremental re-indexing; TTL metadata filtering; change detection pipeline |
| HNSW recall degradation at scale | **Transient** | Retrieval evaluation metrics (hit rate, MRR) drop below baseline | Increase `ef_search`; reshard with more nodes; quantize (turbo4, binary) |
| Chunk boundary info loss | **Permanent** (design) | Manual review reveals incomplete facts in retrieved chunks | Switch to parent-child or contextual chunking strategy |
| Context conflict hallucination | **Transient** | RAGAS faithfulness score drops; contradictory sources detected | Source deduplication; conflict detection pre-generation; version-aware retrieval |
| Reranker latency spike | **Transient** | p99 latency exceeds budget (>2s) | Two-stage reranking; reduce candidate count; self-hosted fallback |
| Cross-tenant data leakage | **Permanent** (security) | Metadata filter bypass detected in audit logs | Enforce filters at DB level (Qdrant payload, pgvector RLS); collection-per-tenant |

### 4.3.1 Idempotency in Ingestion Pipelines

Document ingestion must be idempotent — re-processing the same document produces the same index state without duplicates.

**Implementation pattern**: Content-hash-based idempotent upsert:
```
Document → hash(content) → chunk → embed → upsert(id=hash, vector, metadata)
```
Most vector DBs support upsert semantics (Pinecone, Qdrant, Milvus) — retrying a failed write with the same ID overwrites safely. For Kafka/Pulsar-backed ingestion (Milvus WAL), consumer group offsets provide exactly-once processing semantics.

**Dead-letter queue**: Failed documents (malformed PDFs, embedding API errors, serialization failures) route to DLQ for manual review rather than blocking the pipeline or being silently dropped.

### 4.3.2 Poison-Pill Detection

A poison pill in RAG ingestion is a document that deterministically causes pipeline failure — e.g., a PDF that always crashes the parser, a document exceeding the embedding model's max token limit, or a file triggering an unrecoverable encoding error.

```
Document Queue ──▶ Parser ──▶ Chunker ──▶ Embedder ──▶ Upsert
     │                │ fail      │ fail       │ fail
     │                ▼           ▼            ▼
     │           ┌─────────────────────────────────┐
     │           │ Retry Counter (max 3 attempts)  │
     │           └───────────────┬─────────────────┘
     │                           │ 3 failures, same error
     │                           ▼
     │           ┌─────────────────────────────────┐
     │           │ Dead-Letter Queue                │
     │           │ - Quarantine document            │
     │           │ - Alert ops with error trace     │
     │           │ - Log doc_id + failure class     │
     │           └─────────────────────────────────┘
```

### 4.4 Enterprise Security Boundaries

#### 4.4.1 Zero-Trust RAG Architecture

1. **Transport security**: mTLS between application layer and vector DB / embedding API / reranker API. All inter-service communication encrypted.

2. **Document-level RBAC**: ACL metadata (user roles, group IDs, tenant IDs) stored on every chunk at ingestion time. At query time, metadata filters restrict results to authorized chunks. pgvector leverages PostgreSQL row-level security (RLS) for the most mature ACL model.

3. **Pre-filter enforcement**: Qdrant's ACORN algorithm integrates payload filtering into HNSW graph traversal (single-pass), ensuring unauthorized chunks are never scored. Post-filtering risks returning fewer results than top-K when many are filtered out.

4. **PII filtering pipeline**:
   - **Detection**: Microsoft Presidio, AWS Comprehend, or Google DLP during ingestion.
   - **Redaction strategies**: Remove PII before embedding (cleanest, loses information), mask with placeholders `[NAME]`/`[SSN]` (preserves structure), or tag as metadata for access-control-based filtering.
   - **Audit trail**: Log every redaction decision — what was detected, what action was taken, by which policy version.

5. **Immutable retrieval audit logs**: Every query must log — query text, transformed query variants, retrieved chunk IDs with scores, reranker scores, final selection, generated response, source attributions, user identity, timestamp. Stored in WORM storage for compliance (SOC2, HIPAA, GDPR). No vector DB provides this natively — application-layer implementation required.

#### 4.4.2 Multi-Tenant Isolation Patterns

| Pattern | Isolation Level | Efficiency | Vector DB Support |
|---------|----------------|------------|-------------------|
| **Collection-per-tenant** | Strongest (separate HNSW indexes) | Low (resource overhead per tenant) | Qdrant, Milvus |
| **Namespace isolation** | Strong (logical partition within index) | Medium | Pinecone |
| **Native multi-tenancy** | Strong (per-tenant flat indexes, activate/deactivate) | High (designed for 100K+ tenants) | Weaviate |
| **Metadata filtering** | Weakest (shared collection, filter at query time) | Highest | All DBs |
| **Row-level security** | Strong (SQL-enforced, battle-tested) | High (single table, policy-based) | pgvector (PostgreSQL) |

---

## 5. Production Enterprise Code

### 5.1 Advanced RAG Pipeline with Hybrid Search and Reranking

```python
import hashlib
from dataclasses import dataclass

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue, SearchParams
)
from anthropic import Anthropic


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    score: float
    metadata: dict


class AdvancedRAGPipeline:
    def __init__(
        self,
        qdrant_url: str,
        collection_name: str,
        anthropic_client: Anthropic,
        embedding_client,
        reranker_client,
    ):
        self.qdrant = QdrantClient(url=qdrant_url)
        self.collection = collection_name
        self.llm = anthropic_client
        self.embedder = embedding_client
        self.reranker = reranker_client

    def ingest_document(self, doc_id: str, text: str, metadata: dict):
        chunks = self._chunk_recursive(text, max_tokens=250, overlap_tokens=25)
        points = []
        for i, chunk_text in enumerate(chunks):
            context = self._generate_context(text[:2000], chunk_text)
            contextual_chunk = f"{context}\n\n{chunk_text}"
            content_hash = hashlib.sha256(contextual_chunk.encode()).hexdigest()
            chunk_id = f"{doc_id}_chunk_{i}"
            vector = self.embedder.embed(contextual_chunk)
            points.append(PointStruct(
                id=chunk_id,
                vector=vector,
                payload={
                    "text": contextual_chunk,
                    "doc_id": doc_id,
                    "chunk_index": i,
                    "content_hash": content_hash,
                    **metadata,
                }
            ))
        self.qdrant.upsert(collection_name=self.collection, points=points)

    def query(
        self,
        query: str,
        tenant_id: str,
        top_k: int = 20,
        retrieve_n: int = 100,
    ) -> tuple[str, list[RetrievedChunk]]:
        query_vector = self.embedder.embed(query)
        tenant_filter = Filter(must=[
            FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))
        ])
        raw_results = self.qdrant.search(
            collection_name=self.collection,
            query_vector=query_vector,
            query_filter=tenant_filter,
            limit=retrieve_n,
            search_params=SearchParams(hnsw_ef=128),
        )
        candidates = [
            RetrievedChunk(
                chunk_id=str(r.id),
                text=r.payload["text"],
                score=r.score,
                metadata=r.payload,
            )
            for r in raw_results
        ]
        reranked = self.reranker.rerank(
            query=query,
            documents=[c.text for c in candidates],
            top_n=top_k,
        )
        top_chunks = [candidates[r.index] for r in reranked.results]
        for chunk, rerank_result in zip(top_chunks, reranked.results):
            chunk.score = rerank_result.relevance_score
        context = "\n\n---\n\n".join(
            f"[Source {i+1}] {c.text}" for i, c in enumerate(top_chunks)
        )
        response = self.llm.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": (
                f"Answer the question using ONLY the provided context. "
                f"Cite sources as [Source N].\n\n"
                f"Context:\n{context}\n\n"
                f"Question: {query}"
            )}],
        )
        return response.content[0].text, top_chunks

    def _generate_context(self, doc_excerpt: str, chunk: str) -> str:
        response = self.llm.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": (
                f"Given the document excerpt and a chunk from it, write a concise "
                f"1-2 sentence context that situates this chunk within the document. "
                f"Document: {doc_excerpt}\n\nChunk: {chunk}"
            )}],
        )
        return response.content[0].text

    def _chunk_recursive(self, text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
        separators = ["\n\n", "\n", ". ", " "]
        return self._split_recursive(text, separators, max_tokens * 4, overlap_tokens * 4)

    def _split_recursive(self, text, separators, max_chars, overlap_chars):
        if len(text) <= max_chars:
            return [text]
        sep = separators[0] if separators else " "
        parts = text.split(sep)
        chunks, current = [], ""
        for part in parts:
            candidate = f"{current}{sep}{part}" if current else part
            if len(candidate) > max_chars and current:
                chunks.append(current.strip())
                overlap_start = max(0, len(current) - overlap_chars)
                current = current[overlap_start:] + sep + part
            else:
                current = candidate
        if current.strip():
            chunks.append(current.strip())
        if any(len(c) > max_chars for c in chunks) and len(separators) > 1:
            refined = []
            for c in chunks:
                if len(c) > max_chars:
                    refined.extend(self._split_recursive(c, separators[1:], max_chars, overlap_chars))
                else:
                    refined.append(c)
            return refined
        return chunks
```

### 5.2 Corrective RAG (CRAG) with Web Fallback

```python
from dataclasses import dataclass
from enum import Enum


class Relevance(Enum):
    RELEVANT = "relevant"
    IRRELEVANT = "irrelevant"


@dataclass
class CRAGResult:
    answer: str
    sources: list[dict]
    used_web_fallback: bool


class CorrectiveRAGPipeline:
    def __init__(self, rag_pipeline, web_search_client, llm_client):
        self.rag = rag_pipeline
        self.web = web_search_client
        self.llm = llm_client

    async def query(self, query: str, tenant_id: str) -> CRAGResult:
        _, chunks = self.rag.query(query, tenant_id, top_k=10, retrieve_n=50)
        relevance_verdicts = await self._evaluate_relevance(query, chunks)
        relevant_chunks = [
            c for c, v in zip(chunks, relevance_verdicts)
            if v == Relevance.RELEVANT
        ]
        used_web = False
        web_results = []
        if len(relevant_chunks) < 3:
            transformed_query = await self._transform_query(query)
            web_results = await self.web.search(transformed_query, max_results=5)
            used_web = True

        all_context = (
            [{"text": c.text, "source": "index"} for c in relevant_chunks]
            + [{"text": w["snippet"], "source": w["url"]} for w in web_results]
        )
        answer = await self._generate(query, all_context)
        return CRAGResult(answer=answer, sources=all_context, used_web_fallback=used_web)

    async def _evaluate_relevance(self, query, chunks) -> list[Relevance]:
        response = self.llm.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": (
                f"For each passage below, determine if it is RELEVANT or IRRELEVANT "
                f"to answering: '{query}'\n\n"
                + "\n".join(f"[{i}] {c.text[:300]}" for i, c in enumerate(chunks))
                + "\n\nReturn a JSON array of verdicts: [\"relevant\", \"irrelevant\", ...]"
            )}],
        )
        import json
        verdicts = json.loads(response.content[0].text)
        return [Relevance(v) for v in verdicts]

    async def _transform_query(self, query: str) -> str:
        response = self.llm.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{"role": "user", "content": (
                f"Rewrite this query for web search to find more relevant results: {query}"
            )}],
        )
        return response.content[0].text

    async def _generate(self, query: str, context: list[dict]) -> str:
        context_str = "\n\n".join(
            f"[{c['source']}]: {c['text']}" for c in context
        )
        response = self.llm.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": (
                f"Answer using ONLY the provided context. Cite sources.\n\n"
                f"Context:\n{context_str}\n\nQuestion: {query}"
            )}],
        )
        return response.content[0].text
```

### 5.3 RAG Evaluation with RAGAS Metrics

```python
from dataclasses import dataclass


@dataclass
class RAGEvalResult:
    faithfulness: float
    answer_relevancy: float
    context_precision: float
    retrieval_hit_rate: float


class RAGEvaluator:
    def __init__(self, llm_client):
        self.llm = llm_client

    def evaluate_faithfulness(self, answer: str, contexts: list[str]) -> float:
        claims = self._extract_claims(answer)
        if not claims:
            return 1.0
        supported = sum(
            1 for claim in claims
            if self._is_supported(claim, contexts)
        )
        return supported / len(claims)

    def evaluate_retrieval_hit_rate(
        self, queries: list[str], retrieved_ids: list[list[str]],
        ground_truth_ids: list[list[str]], k: int = 10,
    ) -> float:
        hits = 0
        for retrieved, truth in zip(retrieved_ids, ground_truth_ids):
            top_k = set(retrieved[:k])
            if top_k & set(truth):
                hits += 1
        return hits / len(queries)

    def _extract_claims(self, answer: str) -> list[str]:
        response = self.llm.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": (
                f"Extract all factual claims from this answer as a JSON array of strings. "
                f"Each claim should be a single atomic fact.\n\nAnswer: {answer}"
            )}],
        )
        import json
        return json.loads(response.content[0].text)

    def _is_supported(self, claim: str, contexts: list[str]) -> bool:
        context_str = "\n".join(contexts)
        response = self.llm.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": (
                f"Is this claim supported by the context? Answer only 'yes' or 'no'.\n\n"
                f"Claim: {claim}\n\nContext: {context_str}"
            )}],
        )
        return response.content[0].text.strip().lower() == "yes"
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Enterprise Knowledge Base with Multi-Tenant RAG

**Business context**: A SaaS company serves 500 enterprise customers, each with 10K–500K documents. Requirements: sub-2-second query latency, document-level access control, GDPR data residency (EU customers' data must stay in EU), 99.9% availability, and <2% retrieval failure rate.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                        API GATEWAY (per region)                          │
 │  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────────┐      │
 │  │ Auth/AuthZ │  │ Rate Limit │  │ Region     │  │ Query        │      │
 │  │ (OAuth2)   │  │ (per-tenant│  │ Router     │  │ Classifier   │      │
 │  │            │  │  token     │  │ (EU↔US)    │  │ (naive/adv)  │      │
 │  │            │  │  bucket)   │  │            │  │              │      │
 │  └────────────┘  └────────────┘  └────────────┘  └──────────────┘      │
 └───────────────────────────────┬──────────────────────────────────────────┘
                                 │
           ┌─────────────────────┴─────────────────────┐
           ▼                                           ▼
 ┌──────────────────────────┐            ┌──────────────────────────┐
 │  EU REGION                │            │  US REGION                │
 │  ┌────────────────────┐  │            │  ┌────────────────────┐  │
 │  │ Ingestion Pipeline │  │            │  │ Ingestion Pipeline │  │
 │  │ Parser → Chunker → │  │            │  │ Parser → Chunker → │  │
 │  │ PII Redact →       │  │            │  │ PII Redact →       │  │
 │  │ Context Enrich →   │  │            │  │ Context Enrich →   │  │
 │  │ Embed → Upsert     │  │            │  │ Embed → Upsert     │  │
 │  └────────────────────┘  │            │  └────────────────────┘  │
 │  ┌────────────────────┐  │            │  ┌────────────────────┐  │
 │  │ Qdrant Cluster     │  │            │  │ Qdrant Cluster     │  │
 │  │ - 12 shards, RF=2  │  │            │  │ - 12 shards, RF=2  │  │
 │  │ - HNSW + sparse    │  │            │  │ - HNSW + sparse    │  │
 │  │ - Payload ACL      │  │            │  │ - Payload ACL      │  │
 │  │   filtering        │  │            │  │   filtering        │  │
 │  └────────────────────┘  │            │  └────────────────────┘  │
 │  ┌────────────────────┐  │            │  ┌────────────────────┐  │
 │  │ Redis (query cache) │  │            │  │ Redis (query cache) │  │
 │  └────────────────────┘  │            │  └────────────────────┘  │
 └──────────────────────────┘            └──────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Single Global Qdrant Cluster | B: Regional Qdrant Clusters (Recommended) | C: pgvector on Regional PostgreSQL |
|-----------|-------------------------------|------------------------------------------|-----------------------------------|
| **GDPR compliance** | ⬛⬜⬜ — Cross-border data flow requires complex DPAs | ⬛⬛⬛ — EU data stays in EU region by design | ⬛⬛⬛ — Same regional isolation |
| **Query latency** | ⬛⬜⬜ — Cross-region queries add 50–150ms | ⬛⬛⬛ — Co-located compute + storage | ⬛⬛⬜ — pgvector HNSW slower than dedicated vector DB at scale |
| **Operational complexity** | ⬛⬛⬛ — Single cluster to manage | ⬛⬛⬜ — Two clusters, coordinated schema changes | ⬛⬛⬛ — Leverage existing Postgres expertise |
| **Scale ceiling** | ⬛⬛⬛ — Qdrant handles 100M+ vectors per cluster | ⬛⬛⬛ — Same | ⬛⬛⬜ — HNSW limited to 2K dims; RAM-bound at 100M+ |
| **Multi-tenant isolation** | ⬛⬛⬜ — Payload filtering (weakest isolation) | ⬛⬛⬛ — Payload filtering + regional separation | ⬛⬛⬛ — PostgreSQL RLS (strongest per-row isolation) |
| **Cost** | ⬛⬛⬛ — Single cluster, shared infra | ⬛⬛⬜ — Duplicate infra per region | ⬛⬛⬛ — Reuses existing Postgres infra |

**Recommended approach**: **B (Regional Qdrant Clusters)** with payload-based ACL filtering.

**Decision rationale**: GDPR data residency is a hard constraint, immediately eliminating the single-cluster option for EU customers. Regional Qdrant clusters solve this architecturally — EU data never leaves EU infrastructure. Qdrant's ACORN integrated filtering ensures ACLs are enforced during HNSW traversal (not post-filter), providing both security and recall guarantees. pgvector (Option C) is viable but hits scaling limits earlier — HNSW index in pgvector is limited to 2,000 dimensions and competes for RAM with other PostgreSQL workloads. At 500 tenants with up to 500K docs each, Qdrant's purpose-built architecture handles the scale more gracefully.

### 6.2 Scenario: Migrating from Naive RAG to Advanced RAG at 100K Queries/Day

**Business context**: A legal-tech company runs naive RAG (embed → search → generate) serving 100K queries/day across 2M legal documents. Pain points: 8% retrieval failure rate (clients finding wrong precedents), no source attribution (compliance risk), and 3-second average latency. Goal: reduce retrieval failure to <2%, add verifiable citations, maintain sub-2-second p95 latency.

#### Component Diagram (Target State)

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                      QUERY PIPELINE (target state)                       │
 │                                                                          │
 │  Query ──▶ ┌──────────┐ ──▶ ┌──────────────┐ ──▶ ┌──────────────┐      │
 │            │ Query    │     │ Hybrid Search│     │ Two-Stage    │      │
 │            │ Xform    │     │ Dense (HNSW) │     │ Reranking    │      │
 │            │ - Multi- │     │ + Sparse     │     │ Voyage lite  │      │
 │            │   query  │     │ (BM25)       │     │ → Cohere Pro │      │
 │            │ - Legal  │     │ Top-150      │     │ Top-50→Top-10│      │
 │            │   synonym│     │              │     │              │      │
 │            └──────────┘     └──────────────┘     └──────┬───────┘      │
 │                                                         │               │
 │                   ┌─────────────────────────────────────┘               │
 │                   ▼                                                      │
 │            ┌──────────────┐ ──▶ ┌──────────────┐ ──▶  Response         │
 │            │ Context      │     │ LLM Generate │     with [Source N]    │
 │            │ Assembler    │     │ + Citation   │     citations          │
 │            │ - Dedup      │     │   Enforcement│                        │
 │            │ - Parent     │     │              │                        │
 │            │   retrieval  │     │              │                        │
 │            └──────────────┘     └──────────────┘                        │
 └──────────────────────────────────────────────────────────────────────────┘

 ┌──────────────────────────────────────────────────────────────────────────┐
 │                   RE-INGESTION PIPELINE (migration)                      │
 │                                                                          │
 │  2M docs ──▶ Parse ──▶ Parent-Child Chunk ──▶ Contextual Enrich         │
 │                         (child: 250 tok)       (Haiku: $0.50/M tok)     │
 │                         (parent: 1000 tok)                               │
 │              ──▶ Embed (Voyage v4: $0.06/M tok) ──▶ Qdrant Upsert      │
 │              ──▶ BM25 Sparse Index Build                                 │
 │                                                                          │
 │  Estimated re-ingestion cost: ~$300 (2B tokens × $0.06 embed            │
 │    + 2B tokens × $0.50 contextual enrichment with prompt caching)       │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Contextual Chunks Only | B: Contextual + Hybrid + Two-Stage Rerank (Recommended) | C: Full Agentic RAG (CRAG) |
|-----------|--------------------------|--------------------------------------------------------|---------------------------|
| **Retrieval failure rate** | ⬛⬛⬜ — ~3.7% (35% reduction from 5.7%) | ⬛⬛⬛ — ~1.9% (67% reduction) | ⬛⬛⬛ — <1% (web fallback for gaps) |
| **Latency (p95)** | ⬛⬛⬛ — ~800ms (no reranking step) | ⬛⬛⬛ — ~1.8s (within 2s target) | ⬛⬜⬜ — ~5s (multiple LLM calls for relevance eval) |
| **Cost/1K queries** | ⬛⬛⬛ — ~$18 (embed + search + generate) | ⬛⬛⬜ — ~$27 (+ reranking cost) | ⬛⬜⬜ — ~$80-150 (+ relevance eval + web search) |
| **Citation quality** | ⬛⬛⬜ — Source tracking possible but no relevance scoring | ⬛⬛⬛ — Reranker scores enable confidence-ranked citations | ⬛⬛⬛ — Relevance-verified citations |
| **Migration complexity** | ⬛⬛⬛ — Re-embed with context; no new infra | ⬛⬛⬜ — Add sparse index + reranker API integration | ⬛⬜⬜ — Add agent loop, web search, relevance evaluator |
| **Re-ingestion cost** | ⬛⬛⬜ — ~$200 (contextual enrichment + re-embed) | ⬛⬛⬜ — ~$300 (same + sparse index build) | ⬛⬛⬜ — ~$300 (same as B) |

**Recommended approach**: **B (Contextual + Hybrid + Two-Stage Rerank)**.

**Decision rationale**: The 8% → <2% retrieval failure reduction is the primary business requirement. Option B achieves 1.9% failure rate — meeting the target — at $27/1K queries with p95 latency under 2 seconds. Option A undershoots (3.7% failure rate). Option C overshoots on quality (<1%) but violates the latency constraint (~5s p95) and costs 3–5× more. For legal-tech, the citation quality from reranker relevance scores is particularly valuable — lawyers need to see which sources are most authoritative. The two-stage reranking pattern (Voyage lite for top-50 → Cohere Pro for top-10) controls reranker cost while preserving quality.

Migration plan:
1. **Week 1–2**: Deploy new ingestion pipeline alongside old. Re-ingest 2M docs with contextual enrichment + parent-child chunking + sparse index. Estimated cost: ~$300.
2. **Week 3**: Shadow-test new pipeline against production queries. Compare hit rates, MRR, and faithfulness scores.
3. **Week 4**: Gradual traffic shift (10% → 50% → 100%) with automatic rollback if p95 latency exceeds 2s or retrieval failure rate exceeds 3%.
4. **Week 5**: Decommission naive pipeline. Enable RAGAS-based continuous evaluation in production.

---

*Module 06 complete. Covers the full RAG stack from ingestion to evaluation, with production patterns for naive, advanced, and agentic RAG pipelines.*
