# Module 06: RAG (Retrieval-Augmented Generation)

## What Is This?

**RAG (Retrieval-Augmented Generation)** solves a fundamental problem: LLMs only know what they learned during training. They don't know your company's internal documents, they can't access today's stock prices, and their knowledge has a cutoff date. RAG fixes this by fetching relevant information at query time and stuffing it into the prompt.

The process has two phases:
1. **Ingestion** (offline, ahead of time): Split your documents into chunks (paragraphs or sections), convert each chunk into an **embedding** (a list of numbers that represents the chunk's meaning -- similar text gets similar numbers), and store these embeddings in a **vector database**.
2. **Retrieval + Generation** (at query time): When a user asks a question, convert their question into an embedding, find the most similar document chunks using **vector similarity** (comparing the numbers -- like finding the nearest neighbor), stuff those chunks into the LLM prompt, and ask the model to answer based on the retrieved context.

A simple example: A user asks "What's our parental leave policy?" Your system (1) converts this question into an embedding, (2) searches the vector database and finds the HR policy document chunk about parental leave, (3) sends the prompt: "Based on this document: [parental leave policy text], answer the user's question: What's our parental leave policy?", (4) the LLM generates an answer grounded in your actual policy.

**Why not just stuff everything in the context?** For small document sets, you can. But if you have 10,000 documents, they won't fit in the context window, and even if they did, it would be extremely expensive (you pay per token). RAG lets you retrieve only the 5-10 most relevant chunks.

## Why It Matters

RAG is the most common pattern for building AI applications over private data. Nearly every enterprise AI product -- customer support bots, internal knowledge assistants, document Q&A -- uses some form of RAG.

---

## 2. Core Concepts

### Architecture Overview

```
┌───────────────────────────────────────────────────────────────┐
│                    INGEST PLANE (Offline)                      │
│                                                               │
│  ┌──────────┐   ┌───────┐   ┌─────────┐   ┌───────┐         │
│  │Documents │──>│ Parse │──>│  Chunk  │──>│ Embed │──┐       │
│  └──────────┘   └───────┘   └────┬────┘   └───────┘  │       │
│                                  │ LLM extract        v       │
│                            ┌─────v──────┐    ┌────────────┐   │
│                            │ Graph Build│    │Vector Store│   │
│                            │ KG + Leiden│    │HNSW + BM25 │   │
│                            └────────────┘    └────────────┘   │
└───────────────────────────────┼──────────────────┼────────────┘
                                │    shared stores │
┌───────────────────────────────v──────────────────v────────────┐
│                    QUERY PLANE (Online)                        │
│                                                               │
│  ┌───────┐  ┌───────┐  ┌───────────────┐  ┌────────┐        │
│  │ Query │─>│ Embed │─>│Hybrid Retrieve│─>│ Rerank │──┐     │
│  └───────┘  └───────┘  │Dense ANN+BM25 │  │150──>20│  │     │
│                        │ (+Graph local)│  └────────┘  │     │
│                        └───────────────┘              v     │
│                                              ┌────────────┐  │
│  ┌──────────┐                                │LLM Generate│  │
│  │ Response │<───────────────────────────────│+ Citations │  │
│  │          │                                │(augmented  │  │
│  └──────────┘                                │  prompt)   │  │
│                                              └────────────┘  │
└───────────────────────────────────────────────────────────────┘
```

### The Two Planes: Ingest vs Query

Think of a library. The **ingest plane** is the back office -- cataloging books, assigning shelf numbers, and building the card catalog. The **query plane** is the reference desk -- a patron asks a question, the librarian finds the right books, and reads you the relevant passages.

| Plane | Owns | Typical Components | Failure If Coupled |
|-------|------|-------------------|--------------------|
| **Ingest (write)** | Parse, PII redaction, ACL stamp, chunk, contextualize, embed, sparse encode, graph extract, community reports, checkpoint | Connectors, workers, embedding/rerank batch APIs, HNSW/IVF build, Leiden clustering | Query p99 tracks reindex; a stuck extractor stalls answers |
| **Query (read)** | Authz filter, hybrid retrieve, fuse, rerank, agent loop, generate, cite | ANN + inverted index, RRF/RSF, cross-encoder, LangGraph/LlamaIndex loop, generator | Ingest schema change silently mismatches query embeddings |

**Key invariant**: The model never "searches." It emits a tool call or a rewritten query; the retriever executes; chunks return as observations. The generator's parametric memory is not the corpus.

### Five Index Types in a Production RAG System

1. **Dense ANN** -- HNSW / IVF / BBQ-HNSW over embedding vectors (cosine or inner product).
2. **Sparse / lexical** -- BM25 inverted index (Elasticsearch/OpenSearch/Weaviate), SPLADE or sparse vectors, Postgres `tsvector` (note: not true BM25), ParadeDB `pg_search` for true BM25.
3. **Metadata / ACL bitmap** -- Pre-filter before ANN (Pinecone slab metadata index to bitmap of eligible IDs; Weaviate/OpenSearch/ES filter clauses; pgvector `WHERE tenant_id = $1` with RLS).
4. **Graph** -- Entity/relationship tables + community reports + optional vector index over entities, text units, and reports.
5. **Rerank cache** -- `(query_hash, doc_id, model, version) -> score` with short TTL; not a recall index.

### Bi-Encoder vs Cross-Encoder (The Two-Stage Intuition)

A **bi-encoder** independently embeds the query and each document, then compares vectors via cosine similarity. It is fast (O(1) query encode + ANN lookup) but sees query and document in isolation.

A **cross-encoder** jointly attends over `(query, document)` in one forward pass -- much better relevance scoring, but must run once **per candidate**. That is why production systems use a two-stage architecture: stage-1 (bi-encoder + BM25) for cheap recall over thousands, then stage-2 (cross-encoder) for precise reranking of the top 50-200.

### Naive vs Advanced vs Agentic RAG

| Dimension | Naive RAG | Advanced RAG | Agentic RAG |
|-----------|-----------|-------------|-------------|
| Query handling | Direct embedding | Query transformation, HyDE | Adaptive routing, multi-step planning |
| Retrieval | Single-pass vector search | Hybrid search + reranking | Iterative retrieval with relevance checks |
| Context | Raw chunks | Contextual chunks, compression | Self-correcting with web fallback |
| Evaluation | None | Post-hoc metrics | Inline reflection tokens |
| Latency | ~200-500ms | ~500-2000ms | ~2-10s (multiple LLM calls) |
| Retrieval failure rate | ~5.7% | ~1.9% | <1% [inferred] |
| Cost per query | Low ($0.001-0.01) | Medium ($0.01-0.05) | High ($0.05-0.50) [inferred] |

---

## 3. How It Works

### 3.1 Document Parsing (Ingest Entry Point)

Production RAG begins with document parsing -- converting raw files (PDFs, HTML, DOCX, slides) into structured text elements.

- **Unstructured.io**: Uses YOLOX layout model to detect tables, images, and document sections from PDFs. Partitions elements by type (paragraph, table, title, image) before chunking.
- **MinerU / Docling**: Used by LightRAG for multimodal document parsing -- handles images, tables, and formulas natively.
- **Multi-vector retriever pattern** (LangChain): Decouples retrieval references from synthesis documents. Table summaries are embedded for search, but raw tables are passed to the LLM for generation. For images: (a) embed images via CLIP, (b) generate text summaries via VLM and embed those, (c) hybrid of both.

### 3.2 Chunking Strategies

Chunking is an ingest-plane compiler. Retrieval quality is often more sensitive to chunk policy than to embedding model brand (confirmed across Anthropic, LlamaIndex, and 2026 multi-objective chunking evals).

| Strategy | Extra Model Calls | Helps | Hurts |
|----------|------------------|-------|-------|
| Fixed token window + overlap | No | Simple, predictable vector count | Mid-sentence splits; orphaned pronouns |
| Recursive chunking (`\n\n` -> `\n` -> `.` -> ` `) | No | Reduces mid-sentence breaks | Unaware of semantic boundaries |
| Structure-aware (by title/page/similarity) | No | Legal/markdown headings; section boundaries | Uneven sizes; huge tables |
| Semantic (embedding breakpoints) | Embed sentences | Topic shifts | Cost + unstable boundaries |
| Title/summary prepend | Summary: yes | Cheap lexical boost | Generic summary != chunk-specific |
| **Contextual Retrieval** (Anthropic, 2024-09-19) | LLM per chunk; prompt-cache the document | BM25 **and** dense **and** reranker **and** generator see the same situated text | Ingest $ and latency |
| **Late chunking** (Jina, arXiv 2409.04701) | No extra LLM; long-context embedder | Dense vectors carry doc-level context via token-then-pool | Lexical index unchanged; needs long-context embedder |
| Parent-child (small-to-big) | No | Retrieve small for precision, generate on parent for context | Parent may exceed context; ACL must copy to both |

**Contextual Retrieval deep dive**: Uses an LLM to prepend 50-100 tokens of document-level context to each chunk before embedding. Example: `"The company's revenue grew by 3%"` becomes `"This chunk is from an SEC filing on ACME corp's Q2 2023 performance; previous quarter revenue was $314M. The company's revenue grew by 3%."` Anthropic's eval (Gemini Text 004, top-20): baseline failed retrieval **5.7%** -> contextual embeddings **3.7%** (-35%) -> contextual embeddings+BM25 **2.9%** (-49%) -> + Cohere rerank of top-150 down to 20: **1.9%** (-67%). Prompt-cache cost: **$1.02 per million document tokens** (one-time contextualize). They also note: KB **<~200k tokens (~500 pages)** -> skip RAG, cache the whole corpus in the prompt.

**Late chunking**: Embed the full document (or max window), mean-pool **token** vectors per chunk. Jina reports nDCG@10 lifts vs naive chunking on several BEIR sets. It does not inject company names into BM25.

**GraphRAG chunking**: Longer chunks -> fewer extraction LLM calls (cheaper) but lost-in-the-middle recall of early-chunk entities.

**Practical production default** [inferred]: 400-800 tokens, 10-20% overlap, sentence snap, `doc_id`/`section`/`acl`/`version` on every chunk, parent pointer for generate-time expansion. Starting point for simpler setups: ~250 tokens. Promote to contextual BM25 when eval shows pronoun/entity misses; promote to late chunking when you are dense-only and already on an 8k-32k embedder.

### 3.3 Embedding Models (as of 2026-08)

Pin **model id + dimension + similarity metric + version** in the index schema. Changing any of them is a full re-embed.

| Model | Dim (native) | Context | Price/1M Tokens | Notes |
|-------|-------------|---------|-----------------|-------|
| OpenAI `text-embedding-3-small` | 1536 (Matryoshka) | 8,191 | **$0.02** | Default cost pick; 62.3% MTEB |
| OpenAI `text-embedding-3-large` | 3072 (Matryoshka) | 8,191 | **$0.13** | Higher recall; 64.6% MTEB |
| Voyage `voyage-4-large` | 1024 (256-2048) | 32k | **$0.12** | Best Voyage quality; int8/binary quantization |
| Voyage `voyage-4` | 1024 (256-2048) | 32k | **$0.06** | Good quality/cost balance |
| Voyage `voyage-4-lite` | 1024 (256-2048) | 32k | **$0.02** | Latency/cost optimized |
| Voyage `voyage-code-4` | 1024 (256-2048) | 32k | **$0.12** | Code retrieval specialized |
| Cohere `embed-v4.0` | 256-3072 Matryoshka | 128k; text+image | **$0.10-0.12** | Multimodal; 100+ languages. 128K enables whole-doc embedding |
| BAAI **BGE-M3** | 1024 dense+sparse+ColBERT | 8,192 | Free (self-host, 569M params) | One model, three retrieval modes; 100+ languages; MIT |
| BAAI **BGE-EN-ICL** | 4096 | 8,192 | Free (7B params, Mistral) | In-context learning for embeddings; SOTA on BEIR |
| Jina `jina-embeddings-v5-text-small` | 1024 | 32k | Tiered | 67.0 MMTEB; Matryoshka; late chunking |
| Jina `jina-embeddings-v5-omni-small` | 1024 | 32k | Tiered | Multimodal: text, images, audio, video, PDFs |
| Pinecone `llama-text-embed-v2` | -- | -- | **$0.16/M** | Pinecone Inference hosted |
| Pinecone `pinecone-sparse-english-v0` | -- | -- | **$0.08/M** | Sparse encoder for hybrid |

**Cost comparison for 1M documents (avg 1,000 tokens = 1B tokens)**: OpenAI small $20, Voyage lite $20, Voyage standard $60, Cohere v4 $100, OpenAI large $130.

**Key insight**: Cohere embed-v4 uniquely offers a 128K-token context window, enabling whole-document embedding without chunking for documents under ~100 pages. MTEB/RTEB leaderboard deltas are not your nDCG -- always evaluate on your own data.

### 3.4 Hybrid Search: BM25 + Dense, Fusion

**Why hybrid?** Dense retrieval misses exact IDs (`TS-999`, SKUs, statute numbers). BM25 misses paraphrase. Run both in parallel, then merge.

**RRF (Reciprocal Rank Fusion)** -- Cormack et al., SIGIR 2009. Rank-only fusion:

```
RRF(d) = SUM over retrievers: 1 / (k + rank(d))
```

Default k = 60 in Elasticsearch, OpenSearch, Weaviate `rankedFusion`, Qdrant RRF, and most Postgres CTEs. Rank 1 contributes 1/61 ~ 0.0164; rank 60 contributes 1/120 = 0.0083. Documents present in **both** lists outrank documents that win only one list. RRF is scale-free: BM25 unbounded scores and cosine [-1,1] never share a numeric space.

**Score Fusion Methods (when you trust magnitudes)**

| Method | Who | Mechanism | When It Wins |
|--------|-----|-----------|-------------|
| **Relative Score Fusion** | Weaviate default >= v1.24 | Min-max normalize each list to [0,1], then alpha-weighted sum | Score gaps carry signal |
| **Alpha convex combo** | Pinecone; Weaviate `alpha` | `combined = alpha * dense + (1-alpha) * sparse` | Same index, same query; you can A/B alpha |
| **DBSF** | Qdrant | Normalize by mean/std of prefetch top-k | Calibrated retrievers; outlier-sensitive |
| **min_max + arithmetic_mean** | OpenSearch `normalization-processor` (2.10+) | Score-space mix via search pipeline | You want explicit 0.3/0.7 weights |

**Vendor Topology (Query Path)**

**Weaviate**: Hybrid since v1.17. `alpha`: 0 = keyword, 1 = vector, server default 0.75 if unset -- **set alpha explicitly**. `fusionType`: `relativeScoreFusion` (default >= v1.24) vs `rankedFusion`.

**Pinecone**: Three patterns: (1) single index dense+sparse, `metric=dotproduct` only; (2) two indexes + client RRF; (3) document schema with FTS + dense_vector. **Production trap**: BM25/sparse scores are unbounded; dense is ~[-1,1]. Without `hybrid_score_norm`, sparse dominates. Starting alpha: 0.75 NL docs, 0.5 mixed, 0.25 SKU/ID-heavy.

**Elasticsearch**: Retrievers GA 8.16. `rrf` wraps >=2 children. Defaults: `rank_constant=60`, `rank_window_size=10`. ES 9.2+: per-retriever weight. BBQ: up to 32x compression, >95% memory reduction. Nest `text_similarity_reranker` outside `rrf` for two-stage.

**OpenSearch**: `hybrid` query + search pipeline (not in-query fusion). Max 5 subqueries. Processors: `normalization-processor` or `score-ranker-processor` (2.19, RRF).

**Qdrant (>=1.10)**: `prefetch[]` dense + sparse, then `FusionQuery` RRF or DBSF. **Fusion as top-level query = global across shards**; fusion inside prefetch = per-shard (wrong for multi-shard hybrid).

**pgvector + lexical**: One SQL CTE: dense HNSW + tsvector/BM25, FULL OUTER JOIN, RRF. Honest naming: Postgres `ts_rank` is **not BM25** (no corpus IDF). True BM25: ParadeDB `pg_search`. Practitioner ceiling [inferred]: a few million chunks on one primary before HNSW RAM + filtered-recall collapse.

### 3.5 Recency and Metadata Filters

Filters are a first-class retriever, not a post-process.

- **Pinecone**: `$eq/$gt/$gte/$in/$and/$or`; `$in/$nin` max 10,000 values. Highly selective filters bypass IVF and scan the bitmap.
- **Qdrant**: Formula decay on a datetime payload after RRF, so recall is intact and time is a ranking feature.
- **OpenSearch/ES**: `filter` context (no score) on all hybrid arms -- required for tenant + `effective_date`.
- **Weaviate**: Hybrid inherits BM25 property search; vector distance cap is dense-only.

**Rule**: Apply **authorization as a hard pre-filter**; apply recency as either hard (`status=current`) or soft (decay). Soft recency without ACL still leaks.

### 3.6 Reranking and Two-Stage Retrieval

**Two-Stage Production Default**

```
query -> [authz filter]
      -> dense ANN (k=50-100) || BM25/sparse (k=50-100)
      -> RRF / RSF / alpha -> fused N~50-150
      -> cross-encoder top_n=5-20
      -> generator (and optional citation check)
```

Anthropic used 150 -> 20. Never send pre-rerank noise to the generator.

**Reranker Options**

| Provider | Model | Pricing | Notes |
|----------|-------|---------|-------|
| Cohere | Rerank v4.0 Pro | $4/1K searches | 32,768 context; query up to 16,384 |
| Cohere | Rerank v4.0 Fast | $2/1K searches | Same API |
| Cohere | Rerank v3.5 | $2/1K searches | On Bedrock |
| Voyage | rerank-2.5 | $0.05/1M tokens | 32k context; <=1,000 docs |
| Voyage | rerank-2.5-lite | $0.02/1M tokens | Budget option |
| Open-source | bge-reranker-v2-m3 | Free (self-host via HF TEI) | Multilingual; Pinecone hosts at $2/1k requests |
| Open-source | cross-encoder/ms-marco-MiniLM-L6-v2 | Free (self-host) | Text; returns logits (sigmoid for 0-1) |

**Cohere search unit**: 1 query + up to 100 documents. If query+doc > 500 tokens, auto-split; each chunk counts toward the 100. Hard cap: `num_documents * max_chunks_per_doc <= 10,000`. Rate limits: trial 10 req/min, production 1,000 req/min.

**Voyage rerank-2.5**: Total tokens `q_tokens * n_docs + sum(doc_tokens)` <= 600k. Vendor estimate: ~$0.0025/request assuming 100 docs and 500 tokens per (query+doc). Vendor quality claim: +7.94% NDCG@10 vs Cohere Rerank v3.5 averaged over four first-stage methods.

**LLM-as-reranker**: Pointwise (yes/no per chunk), pairwise (which of two), listwise (reorder 10). Cost: a frontier judge over 50 chunks dwarfs a cross-encoder. Use for: (a) agentic `grade_documents` (binary, structured output, cheap model), (b) citation/faithfulness after generate, not as the primary 100-way ranker.

**Contextual compression**: After retrieval, an LLM extracts only the relevant portions of retrieved chunks, reducing context window usage and improving generation focus.

### 3.7 Agentic RAG

Naive RAG: always retrieve top-k, always generate. Agentic RAG: **retrieval is a tool** with a bounded loop.

**LangGraph Agentic RAG (official tutorial)**: Nodes: `generate_query_or_respond` (model + retriever_tool) -> retrieve -> `grade_documents` (structured binary relevance) -> conditional edge: `generate_answer` or `rewrite_question` -> back to retrieve. Grade-all-irrelevant -> rewrite; else generate. This is the production approximation of Self-RAG + CRAG without fine-tuning reflection tokens.

**Self-RAG** (Asai et al., ICLR 2024): One LM trained to emit reflection tokens: whether to retrieve, whether passages are relevant, whether generation is supported, whether the answer is useful. Adaptive retrieval vs always-on. 7B/13B models beat always-retrieve Llama2-chat on open QA / fact verification. Production teams almost always **prompt** a separate grader rather than train tokens.

**Corrective RAG / CRAG** (Yan et al., arXiv 2401.15884): Retrieval evaluator -> Correct (use internal docs) / Incorrect (web/external fallback) / Ambiguous (mix). Knowledge refinement strips noisy passages. LangGraph cookbooks add a web-search node on the "all irrelevant" edge.

**Adaptive-RAG** (Jeong et al., NAACL 2024): Classifier on question complexity routes: no retrieval / single-shot retrieve / multi-hop iterative. Saves tokens on chitchat; spends them on multi-hop.

**LlamaIndex patterns**: Query rewriting (multi-query -> ensemble/fusion), sub-question generator (tools + decompose), `MultiStepQueryEngine` loop with stop when the rewrite is `"none"`, HyDE as a rewrite agent.

**IRCoT / iterative retrieve**: Interleave chain-of-thought with retrieval (Trivedi et al.). HippoRAG compares against it: single-step PPR 10-20x cheaper, 6-13x faster in their experiments.

**Tool-using retrieval**: Retriever, web search, SQL, KG traversal, and MCP `tools/call` are peers. The agent loop must cap: max retrieve retries (~3), max hops, max tools/turn, wall-clock. Unbounded CRAG+web is an open proxy.

### 3.8 Graph RAG

**Problem it solves**: Vector RAG fails **global** questions ("themes in this corpus") because they are query-focused summarization, not top-k lookup (Edge et al., arXiv 2404.16130). GraphRAG beats vector RAG on comprehensiveness and diversity of answers.

**Microsoft GraphRAG Indexing Pipeline**

1. Chunk source docs.
2. LLM extract entities, relationships, optional claims + descriptions.
3. Build KG; **Leiden** hierarchical communities.
4. Bottom-up community reports (LLM summaries).
5. Embed text units / entities / reports for local lookup.
6. Persist Parquet + vector store.

LLM extraction is ~**75% of indexing cost**. GitHub `microsoft/graphrag` (2026): research project, **maintenance mode**, no new features/PRs; bugfix/CVE only.

**Query Modes**

| Mode | Mechanism | Query Class |
|------|-----------|-------------|
| **Local** | Match entities -> neighborhood + text chunks | "Healing properties of chamomile?" |
| **Global** | Map-reduce over all community reports | "Significant values of the herbs?" |
| **DRIFT** | HyDE + top-K community reports -> follow-up questions -> local search iterations -> hierarchical Q/A | Local questions needing global primer |
| **Basic** | Vanilla top-k vector RAG | Ablation baseline |

**LazyGraphRAG** (MSR, 2024-11): No LLM community summaries at index time. Index cost identical to vector RAG and **0.1% of full GraphRAG**. At 4% of GraphRAG query cost: beats all compared methods on local+global. **>700x lower query cost** than GraphRAG global.

**LightRAG** (Guo et al., EMNLP 2025): Dual-level retrieve (entity + relationship) + incremental graph updates. Avoids full rebuilds. Benchmarks vs GraphRAG: Agriculture 54.8% vs 45.2%, CS 52.0% vs 48.0%, Legal 52.8% vs 47.2%. Backends: PostgreSQL, MongoDB, Neo4j, Milvus, OpenSearch.

**HippoRAG** (NeurIPS 2024): LLM + KG + Personalized PageRank (hippocampal indexing). Single-step multi-hop; up to ~20% over SOTA RAG on multi-hop QA; 10-20x cheaper / 6-13x faster than IRCoT. HippoRAG 2 (ICML 2025): continual non-parametric memory.

**Hybrid graph+vector is the real production shape.** An agent router that picks `vector_tool` vs `graph_local` vs `graph_global` per query avoids paying global map-reduce for "what's the refund SLA?"

### 3.9 Vector Databases

**Pinecone** (Managed serverless): Serverless indexes with dense + sparse. Namespaces for tenant isolation. BM25 full-text search built in. Supports integrated embedding (upsert raw text). Consistency is eventual.

**Qdrant** (Open-source, managed cloud): HNSW with payload filtering integrated into graph traversal (single-pass, not pre/post-filter). ACORN algorithm for high-cardinality filters. Named vectors (separate image and text vectors). Quantization: float16, uint8, turbo4 (4-bit). Recommend 12 shards, RF=2+ for production.

**Weaviate** (Open-source, managed cloud): HNSW (default), Flat (small/multi-tenancy), Dynamic (auto-switches at 10K), HFresh (centroid HNSW + posting lists on disk). HNSW: 2-12KB per vector in RAM. 100M vectors = 200-1200GB. Leaderless replication for data (AP), Raft for cluster metadata. Tunable: ONE, QUORUM, ALL.

**Milvus** (Open-source, Zilliz Cloud): 4-layer architecture. Indexes: FLAT, IVF, HNSW, DiskANN, GPU variants. WAL: Woodpecker (cloud-native), Kafka, or Pulsar. Fully disaggregated storage and compute.

**pgvector** (PostgreSQL extension): HNSW (better queries) and IVFFlat (faster builds). Max 16,000 dims (2,000 for HNSW). ACID compliant. 32TB per table. Replication via WAL.

**ChromaDB** (Open-source, cloud): Dense, sparse, hybrid. Multi-modal. Best for prototyping and small-to-medium datasets.

### 3.10 Evaluation Frameworks

**RAGAS** (Retrieval Augmented Generation Assessment):
- **Faithfulness**: (Supported claims / Total claims). LLM extracts claims, verifies each against context. Score 0-1.
- **Answer Relevancy**: How relevant the response is to the question.
- **Context Precision**: Relevance and ranking quality of retrieved chunks.
- **Context Recall**: How well retrieved context covers needed information.
- Uses LLM-as-judge methodology. Each metric may require 1-3 LLM calls.

**DeepEval**: 50+ metrics including all RAGAS metrics plus agentic, multi-turn, multimodal, and MCP metrics. RAG-specific: faithfulness, contextual recall/precision/relevancy, hallucination. Agentic-specific: task completion, tool correctness, plan adherence. Pytest integration for CI/CD. Apache 2.0, 17.8K GitHub stars.

**Custom metrics** commonly implemented:
- **Hit rate**: Fraction of queries where at least one relevant document is in top-K.
- **MRR**: Average 1/rank of first relevant document.
- **nDCG@K**: Normalized Discounted Cumulative Gain at K.

---

## 4. Key Patterns & Best Practices

### The Retrieval Hierarchy (apply in order)

1. **Recall first**: Hybrid (RRF or explicit alpha) beats dense-only on IDs. Prove with a labeled set, not MTEB.
2. **Precision second**: Cross-encoder 50-150 -> 5-20. LLM grade is a router, not a 100-way ranker.
3. **Loop third**: Cap hops; retrieval as tool; CRAG fallback only to approved corpora.
4. **Graph last**: Only if eval shows global/multi-hop failure; prefer Lazy/HippoRAG/LightRAG over naive full GraphRAG.
5. **Security always**: ACL pushdown, namespace isolation, citation IDs, PII-before-embed, MCP tools without client-supplied tenant.

### Choosing Hybrid vs Graph vs Agentic

| Axis | Hybrid + Rerank (Default) | Agentic (LangGraph/LlamaIndex) | GraphRAG-class |
|------|---------------------------|-------------------------------|----------------|
| **Best query class** | Factoid, FAQ, SKU+semantics | Ambiguous, multi-hop, "should I retrieve?" | Global themes, corpus sensemaking |
| **Index $** | Embed + BM25 | Same + maybe extra rewrites stored | LLM extract + communities (75% extract) or Lazy ~vector |
| **Query $** | 1 embed + 1 hybrid + 1 rerank + 1 generate | x(1+retries) LLM + retrieve | Local ~ hybrid; global map-reduce >> |
| **p99** | Predictable 2-stage | Fat tail (loops) | Global: worst; DRIFT: multi-pass |
| **Security** | Filter/namespace | Tool isolation + same filters on every hop | ACL on nodes **and** reports |
| **Failure** | Score mix, stale ANN | Infinite rewrite, web exfil | Graph explosion, stale communities |
| **When to choose** | 80% of enterprise KB chat | Support/research copilot | Exec "what changed this quarter across 10k docs" |

**Do not** run GraphRAG global on every turn. Router (Adaptive-RAG classifier or cheap LLM): chitchat -> no retrieve; factoid -> hybrid+rerank; multi-hop -> agent 2-3 hops; global -> LazyGraphRAG or scheduled community reports.

### Query Transformation Techniques

- **HyDE (Hypothetical Document Embeddings)**: LLM generates a hypothetical answer, embedded for retrieval instead of the raw query.
- **Multi-query**: LLM generates multiple reformulations; results are merged.
- **Step-back prompting**: Generates a more abstract query for broader context.
- **Sub-question decomposition**: Break complex queries into simpler parts, retrieve for each.

---

## 5. System Design Considerations

### Architecture Scenarios

**Scenario A -- Multi-tenant SaaS KB (10-100M chunks)**: Namespace-per-tenant (Pinecone) or RLS+HNSW (pgvector); hybrid BM25+dense; rerank N=80->8; no GraphRAG. ACL pre-filter only. Pinecone RUs dominated by namespace GB; keep hot tenants small.

**Scenario B -- Pharma/legal multi-hop**: Hybrid retrieve + HippoRAG-style PPR or agent 2-hop with IRCoT cap; graph edges from controlled NER (ontology). Citations = `chunk_id+offsets`. CRAG without open web. Avoid: full Leiden global search; entity explosion.

**Scenario C -- Enterprise "what happened this quarter?"**: LazyGraphRAG or FastGraphRAG + vector hybrid for local. Do not re-Leiden daily on GPT-4-class extract. LightRAG if incremental updates matter more than community reports.

**Scenario D -- Cost-capped internal GPT**: OpenAI 3-small or Voyage-4-lite embed; Postgres hybrid RRF; self-host `bge-reranker-v2-m3` on TEI; Adaptive-RAG skip retrieve on greetings; generate with mini-tier; prompt-cache system+tool schemas.

### Scaling & Infrastructure

**Vector search throughput** (approximate):
- HNSW (hnswlib): 1,000-10,000 QPS at 95%+ recall on 1M vectors
- IVF (faiss): 500-5,000 QPS depending on nprobe
- DiskANN: Lower QPS but handles billion-scale with disk

**Index replication and consistency**:
- **Weaviate**: Raft for metadata; leaderless for data (ONE/QUORUM/ALL). QUORUM = n/2+1. Use QUORUM for RAG corpora that must not cite deleted docs.
- **Pinecone serverless**: Eventual consistency. 100,000 namespaces/index. Enterprise 99.95% uptime SLA.
- **Elasticsearch/OpenSearch**: Primary + replica shards; hybrid fusion on coordinator. Replica lag = BM25 and kNN seeing different live sets.
- **pgvector**: Postgres WAL + streaming replicas. HNSW build is heavy; build after bulk load.

### Checkpointed Ingest Pipeline

Idempotent pipeline:
1. Source watermark (S3 etag / Drive revision / DB CDC LSN).
2. Raw blob + sha256 (poisoning detection).
3. Parse/chunk with `chunk_id = hash(doc_id, chunker_version, text)`.
4. Embed job keyed by `embed_model + dim + chunk_id`.
5. Upsert vectors with `index_version`; only then flip the query alias.
6. Graph extract: per-chunk checkpoint; community detect only on a closed chunk set; reports last.

### Circuit Breakers on the Vector DB

Treat ANN like a downstream HTTP dependency:
- **Timeout** (e.g. 200-500 ms retrieve).
- **Error-rate breaker** (5xx, resource_exhausted, RU throttle).
- **Bulkhead** separate from LLM pool -- a Pinecone RU storm must not starve generate.
- **Fallback chain**: (1) last-good retrieve cache, (2) BM25-only, (3) "index unavailable" refusal -- never generate ungrounded if policy forbids.
- Agent: on retrieve failure, do not infinite rewrite; surface `retrieval_degraded`.

### Cache Layers

| Cache | Key | Hit Saves |
|-------|-----|-----------|
| Embedding cache | `(model, dim, text_hash)` | Ingest re-runs; identical query embed |
| Retriever cache | `(index_version, filter, query_hash, k)` | Duplicate questions |
| Rerank cache | `(reranker, query, doc_id)` | Agent retries |
| Prompt cache | Document prefix (Anthropic) | Contextualize + generate |
| Semantic cache | Query similarity threshold | Paraphrased repeats (GPTCache, LangChain SemanticCache) |
| Community report | Static until reindex | Global search map |

---

## 6. Code Examples

### Hybrid Search with pgvector + RRF (One SQL Round-Trip)

```sql
-- Dense + lexical retrieval with RRF fusion in a single query
WITH dense AS (
  SELECT id, chunk_text,
         ROW_NUMBER() OVER (ORDER BY embedding <=> $query_vec) AS rank
  FROM chunks
  WHERE tenant_id = $tenant                    -- ACL pre-filter
  ORDER BY embedding <=> $query_vec
  LIMIT 100
),
lexical AS (
  SELECT id, chunk_text,
         ROW_NUMBER() OVER (ORDER BY ts_rank_cd(tsv, q) DESC) AS rank
  FROM chunks, websearch_to_tsquery('english', $query_text) q
  WHERE tenant_id = $tenant AND tsv @@ q
  ORDER BY ts_rank_cd(tsv, q) DESC
  LIMIT 100
)
SELECT COALESCE(d.id, l.id) AS id,
       COALESCE(d.chunk_text, l.chunk_text) AS chunk_text,
       COALESCE(1.0/(60 + d.rank), 0) + COALESCE(1.0/(60 + l.rank), 0) AS rrf_score
FROM dense d FULL OUTER JOIN lexical l ON d.id = l.id
ORDER BY rrf_score DESC
LIMIT 20;
```

### LangGraph Agentic RAG Pattern

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, List

class RAGState(TypedDict):
    question: str
    documents: List[str]
    generation: str
    rewrite_count: int

def retrieve(state: RAGState) -> RAGState:
    """Hybrid retrieve: dense + BM25, RRF, then rerank top-20."""
    docs = hybrid_search(state["question"], k=100)
    reranked = reranker.rerank(state["question"], docs, top_n=20)
    return {"documents": reranked}

def grade_documents(state: RAGState) -> str:
    """Binary relevance check via cheap LLM."""
    relevant = [d for d in state["documents"]
                if llm_grade(state["question"], d) == "relevant"]
    if not relevant:
        return "rewrite"           # All irrelevant -> rewrite query
    return "generate"

def rewrite_query(state: RAGState) -> RAGState:
    """LLM rewrites the query for better retrieval."""
    new_q = llm_rewrite(state["question"])
    return {"question": new_q,
            "rewrite_count": state["rewrite_count"] + 1}

def should_continue(state: RAGState) -> str:
    if state["rewrite_count"] >= 3:
        return "generate"          # Cap rewrites at 3
    return grade_documents(state)

def generate(state: RAGState) -> RAGState:
    answer = llm_generate(state["question"], state["documents"])
    return {"generation": answer}

# Build the graph
graph = StateGraph(RAGState)
graph.add_node("retrieve", retrieve)
graph.add_node("rewrite", rewrite_query)
graph.add_node("generate", generate)

graph.set_entry_point("retrieve")
graph.add_conditional_edges("retrieve", should_continue,
                            {"generate": "generate", "rewrite": "rewrite"})
graph.add_edge("rewrite", "retrieve")
graph.add_edge("generate", END)

app = graph.compile(checkpointer=postgres_saver)
```

### Weaviate Hybrid Search with Explicit Alpha

```python
import weaviate

client = weaviate.connect_to_local()
collection = client.collections.get("Documents")

response = collection.query.hybrid(
    query="Error code TS-999 troubleshooting",
    alpha=0.5,           # 0=keyword, 1=vector -- set explicitly!
    fusion_type="relativeScoreFusion",
    limit=20,
    filters=weaviate.classes.query.Filter.by_property("tenant_id").equal("acme"),
    return_metadata=weaviate.classes.query.MetadataQuery(score=True),
)
```

---

## 7. Common Pitfalls & Failure Modes

| Failure | Mechanism | Blast Radius | Detection | Mitigation |
|---------|-----------|-------------|-----------|------------|
| **Stale indexes** | CDC lag, failed upsert, alias not flipped, replica ONE | Answers from deleted/old policy | Watermark lag, sample-query canaries | Alias swap; QUORUM; ingest checkpoints |
| **Embedding drift** | New model/dim/prompt, Matryoshka trim, undocumented API change | Silent recall collapse | nDCG on frozen golden set after every embed bump | Pin model; dual-write + shadow eval; full re-embed |
| **Score-scale hybrid** | Pinecone sparse unbounded vs dense [-1,1]; Weaviate client alpha != 0.75 | Keyword-only or semantic-only in practice | Offline A/B alpha; debug score components | `hybrid_score_norm`; RRF; set alpha explicitly |
| **Filter/ANN interaction** | Metadata filter + IVF; post-filter ACL | Recall -> 0 for rare tenants | Recall@k per tenant | Pinecone bitmap bypass; pushdown ACL; namespaces |
| **Over-retrieval** | k=50 into 128k context; agent 4 hops | Lost-in-the-middle, $ explosion | Context tokens/query histogram | Rerank to 5-20; Adaptive-RAG router; hop cap |
| **Hallucinated citations** | Generate without grounding check | Legal/compliance incident | Citation ID not in retrieved set | ID-constrained cites; CRAG/Self-RAG grade; refuse |
| **Grader false negative** | LLM says "irrelevant" on good docs | Rewrite loop; web leak of confidential query | Loop-depth metrics | Max 3 rewrites; fallback "insufficient evidence" |
| **Grader false positive** | Noise marked relevant | Grounded-looking hallucination | Faithfulness eval | Reranker + NLI; don't trust binary grade alone |
| **Graph explosion** | LLM NER duplicates, co-occurrence cliques, no entity resolution | Index $ 10x; global search timeout | Entity count vs doc count | Canonicalize; Fast/LazyGraphRAG; cap degree |
| **Community staleness** | New docs, old Leiden cut | Global answers miss recent data | `graph_build_id` age | Incremental LightRAG or scheduled rebuild |
| **Poisoned ingest** | Unreviewed connector | Persistent retrieval hijack | sha256 + source allowlist | Quarantine; signed ingest; re-embed audits |
| **Rerank RPM/timeout** | 1k QPS x 80 docs | p99 blowup | Rerank error rate | Cache; lite model; local bge; drop to fused top-8 |
| **Contextual PII spread** | Context prepend copies secrets into every chunk | Broader ACL miss | DLP on chunks | Redact before contextualize |
| **Chunk boundary loss** | Info spanning two chunks lost | Incomplete answers | Quality eval | Overlap; parent-child; contextual chunking; late chunking |
| **Contradictory context** | Different doc versions retrieved | LLM hallucinates synthesis | Faithfulness scoring | Source dedup; conflict detection; citation forcing |

---

## 8. Interview Questions & Answers

**Q1: What is RAG and why do we need it instead of just using a large context window?**

RAG separates the model's parametric knowledge from your actual data. Even with million-token context windows, you still need RAG for three reasons. First, cost -- stuffing 10M tokens of documents into every query is prohibitively expensive. Second, freshness -- you can update the index without retraining. Third, access control -- you can filter retrieval by tenant/role, which you cannot do with fine-tuned weights. Anthropic themselves note that for KBs under ~200k tokens (~500 pages), you can skip RAG and cache the whole corpus, but anything larger needs retrieval.

**Q2: Walk me through a production RAG pipeline.**

I think about it as two planes. On the ingest side: parse documents (Unstructured.io, Docling), chunk them (400-800 tokens with sentence snapping and 10-20% overlap), optionally prepend context (Anthropic's Contextual Retrieval), embed with a pinned model version, build both a dense ANN index and a BM25 inverted index, stamp every chunk with ACL metadata. On the query side: embed the user's query, run hybrid search (dense + BM25 in parallel), fuse with RRF (k=60), rerank the top 150 down to 20 with a cross-encoder like Voyage rerank-2.5 or Cohere v4, then pass the top chunks into the LLM with source attribution. This is the Anthropic Contextual Retrieval pattern that cuts retrieval failure from 5.7% to 1.9%.

**Q3: Explain RRF (Reciprocal Rank Fusion) and why it is preferred over score-based fusion.**

RRF computes each document's score as the sum of `1/(k + rank)` across all retriever lists, where k is typically 60. The beauty is that it is scale-free -- BM25 scores are unbounded, cosine similarity is [-1, 1], but ranks are always comparable. Documents appearing in both lists naturally outrank single-list winners. Score fusion methods like alpha-weighted combination require the scores to be on compatible scales, which is fragile when the corpus changes (BM25 distributions drift) or the embedder changes. That said, Weaviate's Relative Score Fusion (min-max to [0,1] then weighted sum) can capture score gaps that rank order misses -- if one BM25 hit is far above the rest, RSF preserves that signal.

**Q4: When would you use Agentic RAG vs standard hybrid RAG?**

Standard hybrid retrieval handles 80% of enterprise KB chat -- factoid questions, FAQ, SKU lookups. Agentic RAG is for when the query is ambiguous, multi-hop, or the system needs to decide whether to retrieve at all. The LangGraph pattern implements this: retrieve, grade relevance, and if all docs are irrelevant, rewrite the query and try again (with a cap of ~3 rewrites). CRAG adds a web fallback when internal docs are insufficient. The cost is 2-10x more per query and fat-tail latency. Adaptive-RAG routes intelligently: chitchat skips retrieval entirely, simple factoids get one-shot hybrid, complex questions get iterative retrieval.

**Q5: What is GraphRAG and when is it justified?**

GraphRAG solves the problem that vector RAG fails on global questions like "what are the themes across this corpus?" because those require query-focused summarization, not top-k lookup. Microsoft's approach extracts entities and relationships via LLM, builds a knowledge graph, applies Leiden community detection, and generates community summaries. Global queries map-reduce over these summaries. The catch: LLM extraction is ~75% of indexing cost, the OSS repo is maintenance-mode, and global queries are expensive. LazyGraphRAG drops index cost to 0.1% of full GraphRAG and is >700x cheaper for global queries. For production, I would use a router: factoid questions go to hybrid+rerank, multi-hop to an agent loop with HippoRAG PPR, and global summaries to LazyGraphRAG.

**Q6: How do you handle multi-tenancy in a RAG system?**

There is an isolation ladder. Cheapest: metadata `tenant_id` filter on every query -- but an app bug can omit the filter. Better: namespace-per-tenant (Pinecone supports 100k namespaces/index) -- queries physically cannot cross namespaces, and a 1GB tenant = 1 read unit vs scanning 100GB with a filter. Strongest: index or instance per tenant, with PrivateLink and BYOC for HIPAA/finance. The critical rule is to apply authorization as a mandatory pre-filter at the ANN level (predicate pushdown), not as a post-filter after top-k, because post-filtering loses recall as the corpus grows. For pgvector, use Row-Level Security policies.

**Q7: How do you choose a chunking strategy?**

Start with the production default: 400-800 tokens, sentence-snap boundaries, 10-20% overlap. From there, use eval to decide. If you see orphaned pronouns and entity misses (the chunk says "the company" but not which company), add Anthropic's Contextual Retrieval -- it prepends document context to each chunk, reducing retrieval failure by 35-67% depending on stack. If you are dense-only with a long-context embedder (8k-32k), try Jina's late chunking (token-then-pool preserves cross-chunk context). For structured documents like legal filings or technical docs, use structure-aware chunking that respects headings and sections. Parent-child is great when you want precise retrieval (small chunks) but rich generation context (return the parent).

**Q8: What is the cost breakdown of a RAG query?**

For a reference query with 1k user questions (no retries), 80 fused chunks reranked to 8, ~4k context tokens: embed ~$0.001 (OpenAI 3-small), rerank **~$2.20** (Voyage rerank-2.5 at $0.05/1M tokens), generate **~$0.84** (mini-tier model). Total ~$3/1k queries. Rerank dominates when generation is cheap; generation dominates with a frontier model ($3-15/1M output tokens). Self-hosted bge-reranker eliminates the rerank API cost entirely -- your GPU/RAM is the bill.

**Q9: How do you prevent and detect hallucinated citations?**

The model can invent `[doc 17]` or a URL that never existed. Three mitigations: First, constrain citations to IDs from the actual retrieved set -- the model can only reference chunk IDs that were in context. Second, use a faithfulness checker (RAGAS metric or NLI model) that verifies each claim is actually supported by the cited chunk. Third, hash-verify chunk body vs ingest sha256 to ensure the cited chunk was not tampered with. Measure **provenance fidelity** = fraction of cited IDs that (a) were in the retrieved set, (b) support the claim via NLI, (c) the user was entitled to see.

**Q10: What are the key differences between Pinecone, Qdrant, Weaviate, and pgvector for RAG?**

Pinecone is fully managed serverless with built-in BM25 and namespace isolation -- great if you want zero ops. But consistency is eventual, and metadata filters scan the full namespace (1 GB = 1 RU). Qdrant is open-source with the best filtering story -- payload filtering is integrated into HNSW graph traversal (single-pass, not pre/post-filter), with ACORN for high-cardinality queries. Weaviate gives you leaderless replication with tunable consistency (ONE/QUORUM/ALL) and HFresh for memory-efficient large-scale deployments, but HNSW needs 2-12KB per vector in RAM. pgvector gives you ACID compliance, SQL joins, RLS, and you can combine dense search with true BM25 (via ParadeDB) in one SQL query. The ceiling is a few million chunks before HNSW RAM pressure; beyond that, escalate to a dedicated vector DB.

**Q11: Explain the two-stage retrieval architecture.**

The intuition is: recall is cheap, precision is expensive. Stage-1 uses bi-encoders (independent query and doc embedding) plus BM25 to cast a wide net -- retrieve maybe 50-150 candidates. This is fast because it is just ANN lookup plus inverted index. Stage-2 uses a cross-encoder that jointly attends over each (query, document) pair -- much better relevance scoring but O(N) per candidate. So you only cross-encode the top candidates. Anthropic's benchmark used 150 -> 20. The key decisions are: how many candidates in stage-1 (more = better recall, higher rerank cost), how many to keep for the generator (5-20 is typical), and which reranker (Voyage rerank-2.5 at $0.05/1M tokens is excellent quality/cost).

**Q12: How do you handle document freshness in a RAG system?**

This is the "stale index" problem. Solutions layer: (1) Change detection via content hashing -- re-embed only changed documents. (2) Incremental updates -- LightRAG and Milvus support this without full rebuild. (3) Index aliasing -- build the new index in parallel, swap the alias atomically. (4) Recency metadata -- tag chunks with ingestion timestamp, use either hard filter (`status=current`) or soft decay (Qdrant formula query with Gaussian/exponential time decay). (5) Ingest watermarks -- track the latest CDC LSN so you know how far behind you are. The anti-pattern is coupling ingest and query planes so that a reindex blocks queries.

**Q13: Design a RAG system for a regulated industry (pharma/legal) with citation requirements.**

I would use hybrid retrieve + HippoRAG-style PPR for multi-hop questions (e.g., "compare trial X vs Y across protocols"), with controlled NER from a domain ontology rather than unconstrained LLM entities. Citations must be `chunk_id + character offsets` from the actual retrieved set -- no generated URLs. Use CRAG but without open web (only licensed corpora as fallback). Every retrieval decision gets logged: query, retrieved chunk IDs with scores, reranker scores, final selection, user identity, model version. For 21 CFR 11 audit compliance, persist plan JSON and tool-arg hashes. Avoid: full Leiden global search (cost), entity explosion, LLM-as-only-reranker on 200 chunks.

---

## 9. Key Numbers to Memorize

| Metric | Value | Source |
|--------|-------|--------|
| Contextual Retrieval failure reduction | 5.7% -> 1.9% (67% drop) | Anthropic 2024 eval |
| Contextual Retrieval ingest cost | $1.02 / 1M document tokens (prompt-cached) | Anthropic |
| RRF constant k | 60 (default across ES, OpenSearch, Weaviate, Qdrant) | SIGIR 2009 |
| Weaviate hybrid alpha default | 0.75 (dense-leaning) | Weaviate docs |
| Pinecone $in filter max | 10,000 values | Pinecone docs |
| Pinecone namespaces/index | 100,000 | Pinecone docs |
| Cohere Rerank doc cap | 10,000 (num_documents * max_chunks) | Cohere docs |
| Cohere Rerank rate limit | 1,000 req/min (production) | Cohere docs |
| Voyage rerank-2.5 token cap | 600k total tokens per request | Voyage docs |
| GraphRAG extraction cost | ~75% of total indexing cost | Microsoft docs |
| LazyGraphRAG index cost | 0.1% of full GraphRAG | Microsoft blog |
| LazyGraphRAG query savings | >700x cheaper than GraphRAG global | Microsoft blog |
| OpenAI embed 3-small price | $0.02 / 1M tokens | OpenAI |
| Voyage rerank-2.5 price | $0.05 / 1M tokens | Voyage |
| HNSW RAM per vector (Weaviate) | 2-12 KB | Weaviate docs |
| pgvector HNSW ef_search 40 | ~8 ms p95 (10M rows, one benchmark) | CallSphere blog |
| Two-stage typical flow | 50-150 retrieve -> rerank -> 5-20 to generator | Industry standard |
| Anthropic skip-RAG threshold | <200k tokens (~500 pages) | Anthropic |
| BBQ compression (Elasticsearch) | Up to 32x, >95% memory reduction | Elastic 8.16 |

---

## 10. Quick Reference

### RAG Cheat Sheet

**Pipeline**: Parse -> Chunk (400-800 tok) -> Embed (pin model+dim) -> Index (dense ANN + BM25) -> Query (hybrid + RRF) -> Rerank (top 150->20) -> Generate (with citations)

**Fusion**: RRF (k=60) for most cases. Score fusion (RSF/alpha) when score gaps matter. Never mix raw BM25 + cosine scores without normalization.

**Chunking decision tree**:
- Start: 400-800 tokens, sentence-snap, 10-20% overlap
- Orphan pronouns? -> Add Contextual Retrieval
- Dense-only + long-context embedder? -> Try late chunking
- Structured docs? -> Structure-aware (by title/heading)
- Need precise retrieve + rich context? -> Parent-child

**Reranking**: Always rerank. Cross-encoder for the serious list (50-150 -> 5-20). LLM grade for binary "relevant?" routing decisions. Never LLM-rerank 100 candidates.

**Agentic RAG cap rules**: max 3 query rewrites, max 3 hops, wall-clock timeout. No unbounded CRAG+web.

**Graph RAG decision**: Only when eval shows global/multi-hop failure. Prefer LazyGraphRAG (index ~ vector cost, 700x cheaper queries) or LightRAG (incremental updates) over full GraphRAG (maintenance-mode, 75% index cost is extraction).

**Security checklist**:
- ACL as pre-filter (predicate pushdown), not post-filter
- Namespace or per-tenant index, not shared-index metadata hope
- PII redaction before embed (vectors invert to approximate text)
- Citation IDs from retrieved set only (constrained decode)
- MCP tools: tenant_id from verified token, never from tool args
- No raw chunk echo to unauthorized traces

**Cost formula** (per 1k queries, reference):
```
embed (~$0.001) + rerank (~$2.20) + generate (~$0.84) = ~$3.00
(excludes vector DB RUs, graph map-reduce, retries)
```
