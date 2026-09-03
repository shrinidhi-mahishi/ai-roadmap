# Module 01: Retrieval-Augmented Generation (RAG)

**Canonical study + interview prep.** Consolidated from 3 AI model research sources (GPT, Opus, Grok), grounded in 97+ references dated 2026-09-02. Prices, rate limits, and compression ratios are vendor docs / papers / named blogs as of that date. `$ per 1k queries` figures marked **[inferred]** are derived from published token/search-unit rates applied to a stated reference query, not a vendor SKU. Do not treat inferred figures as list prices.

---

## What Is This?

RAG is like an **open-book exam**. Instead of memorizing every fact, the model looks up relevant information from your documents before answering. You give the LLM a search engine for your private data, and it reads the top results before generating a response.

Without RAG, an LLM only knows what was in its training data. With RAG, it can answer questions about your company's internal docs, yesterday's support tickets, or this morning's policy update -- things no pre-trained model could possibly know.

**The basic flow:** user asks a question, the system searches a database of your documents for relevant passages, feeds those passages alongside the question to the LLM, and the LLM writes an answer grounded in the retrieved evidence.

Lewis et al. (NeurIPS 2020) defined the split that still holds: a **parametric** generator plus a **non-parametric** index. The generator never searches. The retriever returns documents; those documents are concatenated with the user input; then the model generates (originally marginalized per-sequence or per-token over retrieved latents).

**Real-world analogy:** Think of a library. **Ingest** is the back office -- cataloging books, stamping who may read them, writing the card catalog. **Query** is the reference desk -- a patron asks, you are allowed to pull only the shelves they are entitled to, you rank the best passages, and you read them aloud with page citations. If you merge those jobs into one function, a stuck cataloger stalls every answer, and a schema change silently poisons retrieval.

**Why not dump the whole corpus into the prompt?** Anthropic's own rule: if the knowledge base is **< ~200k tokens (~500 pages)**, skip RAG and cache the corpus. Past that, you cannot afford (and the model cannot *use*) a uniformly stuffed window -- see the lost-in-the-middle failure mode below.

The default production view is: **parametric memory is general reasoning, retrieval is current evidence.** RAG is how you join them without pretending the model "knows" your corpus.

---

## Why It Matters

RAG is the dominant pattern for enterprise AI in 2026 because it solves the two hardest LLM problems simultaneously: **knowledge staleness** (the model doesn't know your data) and **hallucination** (the model invents plausible-sounding nonsense). Enterprise intent to adopt hybrid retrieval tripled from 10.3% to 33.3% in Q1 2026.

RAG matters even in a 1M-token world because larger context windows do not solve the three production problems that actually dominate enterprise systems:

| Problem | Why Long Context Does Not Solve It |
|---|---|
| **Freshness** | Source data changes hourly/daily. You update indexes without retraining. |
| **Access Control** | Each user should only see their tenant's documents. Retrieval enforces tenant and role boundaries per-query. |
| **Cost** | Retrieving 5-10 passages is cheaper than sending 500 pages on every turn. |
| **Auditability** | You can log exactly what evidence the answer used, enabling citation chains. |

In interviews, the strongest framing is that RAG is **not** "a vector DB attached to an LLM." It is a retrieval system plus a generation system with different failure modes, scaling knobs, and SLIs. Bigger context helps small corpora. It does not replace indexed search, ACL-aware retrieval, or citation-safe answering for large mutable knowledge bases.

Almost every enterprise AI product over private data is a RAG system: support bots, internal copilots, legal/pharma Q&A, "what changed this quarter?". Interviews test whether you can split **control plane vs data plane**, fuse **BM25 + dense** without score-scale bugs, put **ACL in the query predicate** (not the prompt), cap **agent hops**, and budget **rerank + generate** separately from embedding pennies.

---

## 1. System Topology & Data Flow

A production RAG product is **two independently scaled planes sharing indexes**, plus a **control loop** (grade -> rewrite -> retrieve -> generate) around those indexes. Coupling ingest to query causes p99 to track reindex.

### 1.1 High-Level Architecture Diagram

```
                         TELEMETRY / OBSERVABILITY SINKS
         +----------------------------------------------------------------------+
         |  OTel traces (ACL-redacted spans)   watermark lag canaries            |
         |  nDCG@k golden set   RU/RPM/TPM meters   provenance (WORM) logs      |
         |  LangSmith/equivalent: chunk text redacted to caller's ACL           |
         +----------^---------------------^------------------^------------------+
                    | spans               | metrics           | audit events
                    |                     |                   |
+-------------------+---------------------+-------------------+-----------------+
| CONTROL PLANE  (authz, routing, versioning, loop caps -- not token math)      |
|                                                                               |
|  +-------------+  +------------------+  +--------------+  +---------------+   |
|  | PEP / IdP   |  | Adaptive router  |  | LangGraph    |  | Ingest        |   |
|  | Entra/JWT   |  | chitchat|factoid |  | orchestrator |  | watermarks    |   |
|  | -> ACL pred.|  | multi-hop|global |  | + hop cap    |  | alias flip    |   |
|  +------+------+  +--------+---------+  +------+-------+  +------+--------+   |
|         | tenant+user      | route             | tool calls      | pin        |
+---------+------------------+-------------------+------------------+-----------+
          |                  |                   |                  |
          |                  v                   v                  v
+---------+---------------------------------------------------------------------+
| DATA PLANE  (ingest write vs query read -- independently scaled)              |
|                                                                               |
|  INGEST (write):  source -> parse -> PII DLP -> ACL stamp -> chunk ->         |
|                   contextualize -> embed/sparse -> graph extract -> upsert    |
|                   (live only after alias/snapshot flip)                        |
|                                                                               |
|  QUERY  (read):   authz filter -> embed q -> hybrid retrieve -> fuse ->       |
|                   rerank -> [grade/rewrite loop] -> generate -> cite          |
|                                                                               |
|  +--- TOOL PROXIES (MCP tools/call -- least privilege) --------------------+ |
|  | retrieve_public_kb | retrieve_hr | sql_customer | graph_local/global    | |
|  | rerank_api         | generate_fm | (NO omnibus search(collection))      | |
|  | Identity from verified token / RunContext -- NEVER from model JSON      | |
|  +------------------------------------------------------------------------+ |
+----------+---------------+-----------------+------------------+---------------+
           |               |                 |                  |
           v               v                 v                  v
+---------------------------------------------------------------------------+
| PERSISTENCE LAYER  (five indexes coexist; query pins a complete snapshot)  |
|                                                                           |
|  +-----------+ +----------+ +-----------+ +-----------+ +----------+      |
|  | Dense ANN | | Sparse/  | | ACL bitmap| | Graph     | | Caches   |      |
|  | HNSW/IVF/ | | lexical  | | pre-filter| | entities +| | rerank   |      |
|  | BBQ-HNSW  | | BM25/    | | before ANN| | reports + | | retrieve |      |
|  |           | | SPLADE   | |           | | text units| | embed    |      |
|  +-----------+ +----------+ +-----------+ +-----------+ +----------+      |
|  LangGraph: PostgresSaver (threads) + Store (cross-thread)                |
|  Graph artifacts: Parquet + vector, keyed by graph_build_id               |
|  *Postgres tsvector is NOT BM25; true BM25 = ParadeDB pg_search          |
+---------------------------------------------------------------------------+
```

### 1.2 Component-Level Architecture (Data Plane Detail)

```
+-------------------------------------------------------------------------+
|                          CONTROL PLANE                                    |
|  +------------+  +--------------+  +------------+  +---------------+    |
|  |   Query    |  |  Orchestrator|  |  Eval Loop |  | Observability |    |
|  |   Router   |--|  (LangGraph) |--|  (RAGAS /  |--|  (Langfuse /  |    |
|  |            |  |              |  |  DeepEval) |  |  OTel)        |    |
|  +-----+------+  +------+------+  +------------+  +---------------+    |
|        |                |                                               |
+--------+----------------+-----------------------------------------------+
|        |         DATA PLANE                                             |
|        v                v                                               |
|  +------------+  +--------------+                                       |
|  | Embedding  |  |   Reranker   |                                       |
|  | Service    |  | (Cross-Enc.) |                                       |
|  +-----+------+  +------+------+                                        |
|        |                |                                               |
|        v                v                                               |
|  +--------------------------------------------+                        |
|  |         HYBRID RETRIEVER                    |                        |
|  |  +--------------+  +--------------------+  |                         |
|  |  | Dense Vector  |  |  Sparse (BM25 /   |  |                        |
|  |  | Index         |  |  SPLADE)           |  |                        |
|  |  +------+-------+  +--------+-----------+  |                         |
|  |         +--------+----------+              |                         |
|  |                  v                          |                        |
|  |          RRF Fusion (k=60)                  |                        |
|  +---------------------------------------------+                       |
|                                                                         |
+-------------------------------------------------------------------------+
|                     PERSISTENCE LAYER                                   |
|  +------------+  +--------------+  +------------+  +---------------+   |
|  | Vector DB  |  |  Document    |  |  Metadata  |  |  Audit Log    |   |
|  | (Qdrant /  |  |  Store       |  |  + RBAC    |  |  (Immutable)  |   |
|  |  pgvector) |  |  (S3/GCS)    |  |  Tags      |  |               |   |
|  +------------+  +--------------+  +------------+  +---------------+   |
|                                                                         |
+-------------------------------------------------------------------------+
|                     INGESTION PIPELINE                                  |
|  +------------+  +--------------+  +------------+  +---------------+   |
|  | Source      |  |  Chunker     |  |  Embedding |  |  Indexer      |   |
|  | Connectors |--|  (Recursive  |--|  Model     |--|  (Batch)      |   |
|  |            |  |   512-1024t) |  |            |  |               |   |
|  +------------+  +--------------+  +------------+  +---------------+   |
+-------------------------------------------------------------------------+
```

### 1.3 Plane Separation (Do Not Couple)

| Plane | Owns | Typical Components | Failure If Coupled |
|---|---|---|---|
| **Ingest (write)** | Parse, PII redaction, ACL stamp, chunk, contextualize, embed, sparse encode, graph extract, community reports, checkpoint | Connectors, workers, embedding/rerank batch APIs, HNSW/IVF build, Leiden clustering | Query p99 tracks reindex; a stuck extractor stalls answers |
| **Query (read)** | Authz filter, hybrid retrieve, fuse, rerank, agent loop, generate, cite | ANN + inverted index, RRF/RSF, cross-encoder, LangGraph/LlamaIndex loop, generator | Ingest schema change silently mismatches query embeddings |

### 1.4 Five Index Types in One Product

1. **Dense ANN** -- HNSW / IVF / BBQ-HNSW over embedding vectors (cosine or inner product).
2. **Sparse / lexical** -- BM25 (Elasticsearch/OpenSearch/Weaviate), SPLADE or `pinecone-sparse-english-v0`, Postgres `tsvector` (not BM25), ParadeDB/`pg_search` true BM25.
3. **Metadata / ACL bitmap** -- pre-filter before ANN (Pinecone slab metadata -> roaring bitmap of eligible IDs; Weaviate/OpenSearch/ES filter clauses; Azure document-level ACL at query time).
4. **Graph** -- entity/relationship tables + community reports + optional vector index over entities, text units, and reports.
5. **Rerank cache** -- `(query_hash, doc_id, model, version) -> score` with short TTL. Not a recall index.

### 1.5 Request-Flow Narrative

#### Ingestion (Offline)

1. **Source connectors** pull documents from S3, Confluence, SharePoint, databases.
2. **Watermark** (S3 etag / Drive revision / DB CDC LSN / SharePoint ACL version) acts as the idempotency key for the document version.
3. **Raw blob + sha256** for poisoning detection. Failed parse -> dead-letter / quarantine, not into the live alias.
4. **DLP/PII redaction** runs **before** embedding. Vectors are derived personal data. Embed APIs see plaintext -- DPA, zero-retention, or self-host BGE-M3.
5. **Chunker** splits each document using recursive splitting at 512-1024 tokens with 10-20% overlap. Metadata tags (source, timestamp, RBAC permissions, data subject IDs for GDPR) are attached to each chunk. `chunk_id = hash(doc_id, chunker_version, text)`.
6. **Embedding model** (e.g., text-embedding-3-large or self-hosted BGE-M3) converts each chunk into a dense vector keyed by `embed_model + dim + chunk_id`.
7. **Indexer** writes vectors + metadata to the vector DB and BM25-compatible text to the sparse index under `index_version`.
8. **Alias flip** -- only then does the query alias swap. Compare-and-swap on the alias is the distributed lock / fencing token. Graph extract: per-chunk checkpoint; community detect only on a closed chunk set; reports last.

#### Query (Online)

1. **Authentication / PEP.** TLS terminates. The verified Entra/JWT (not a tool argument) expands groups. The PEP emits a **hard filter predicate** (`tenant_id`, `userIds`/`groupIds`/`rbacScope`, `status=current`). Recency may be soft (decay) *after* ACL; soft recency without ACL still leaks.
2. **Adaptive router (control).** Classifier or cheap LLM: `chitchat` -> no retrieve, generate; `factoid` -> hybrid+rerank; `multi-hop` -> agent 2-3 hops; `global` -> LazyGraphRAG / community reports. This is Adaptive-RAG (Jeong et al., NAACL 2024) in production clothing.
3. **Embedding service** converts the query to a dense vector (50-token class).
4. **Hybrid retriever** fires two parallel searches: dense vector similarity (top 50-100) and BM25/SPLADE sparse search (top 50-100), **both with the ACL filter on every arm**.
5. **RRF fusion** merges candidate lists: `score(d) = SUM(1/(k + rank(d)))`, k=60. This consistently outperforms either retriever alone (NDCG 0.7068 vs BM25 0.6983 vs KNN 0.6953 on WANDS benchmark).
6. **Cross-encoder reranker** jointly scores the top 50 fused candidates. This is the single largest precision gain in the pipeline: Recall@5 jumps from 0.695 to 0.816 (+17.4%), MRR@3 from 0.433 to 0.605 (+39.7%).
7. **RBAC + freshness filter** removes any chunks the user lacks permission to see and any chunks past their staleness threshold.
8. **Top 5-10 reranked passages** plus the query go to the LLM with citation metadata. Edge-placement (U-shaped attention) puts highest-scored chunks at the beginning and end of the context.
9. **LLM generates** an answer with inline citations constrained to retrieved chunk IDs only.
10. **Telemetry** writes provenance: `source_uri`, `chunk_id`, `retriever`, `rerank_score`, `user_id`, `tenant`, `index_build_id` -- document body redacted to the same ACL as the user.
11. **Eval loop (async)** scores faithfulness, answer relevancy, and context recall on a sample of production traffic using RAGAS / DeepEval.

### 1.6 Vendor Query-Path Topology (Interview Traps)

| Vendor | Key Details |
|---|---|
| **Weaviate** | Hybrid since v1.17; `alpha` 0=keyword, 1=vector, **server default 0.75**; `relativeScoreFusion` default >= v1.24 vs `rankedFusion` |
| **Pinecone** | (1) single index dense+sparse, `metric=dotproduct` only; (2) two indexes + client RRF; (3) FTS + `dense_vector`. Naive IVF+filter: recall collapse at **50%** filtered, unusable at **90%** (ICML 2025). `$in`/`$nin` max **10,000**. Cost: **1 RU per 1 GB** of the queried namespace |
| **Elasticsearch** | Retrievers GA **8.16** (Enterprise for RRF+retrievers). `rank_constant=60`, `rank_window_size=10` (>= `size`). Nest reranker **outside** `rrf`. BBQ: up to **32x** compression (vendor-stated) |
| **OpenSearch** | `hybrid` + **search pipeline**; max **5** subqueries; coordinator fusion after per-shard legs; `pagination_depth` required when `from > 0`. Cannot nest under `function_score` / `constant_score` / `script_score` / `boosting` |
| **Qdrant >= 1.10** | `prefetch[]` then **top-level** `FusionQuery` (RRF/DBSF). Fusion inside prefetch = per-shard (wrong for multi-shard). Optional recency decay after fusion |
| **pgvector** | One SQL round-trip, `FULL OUTER JOIN`, RRF. `ts_rank` != BM25 (use ParadeDB for true BM25) |
| **Bedrock KB** | `HYBRID` or `SEMANTIC`; hybrid needs a filterable text field else semantic fallback. Guardrails cover query and answer, not retrieved source text |
| **Azure AI Search** | BM25+HNSW -> RRF -> Semantic Ranker top **50**. Agentic retrieve (2026): parallel subqueries; does **not** apply index scoring profiles. Pass Entra token in `x-ms-query-source-authorization` |
| **Vertex RAG Engine** | `hybrid_search.alpha` default **0.5** (Weaviate-backed). One rerank layer. Retrieval **600 RPM** in third-party notes |

---

## 2. Core Mechanics & Algorithms

### 2.1 Three Invariants

**Invariant I1: The generator does not search.** Retrieval is a tool (or a DAG stage). The parametric model emits a query or a tool call; the data plane executes; chunks return as observations. Prompt text is not an authorization boundary.

**Invariant I2: Pin the embedding schema.** Pin `model_id + dimension + similarity metric + version` in the index schema. Changing any is a full re-embed. Query embeddings from model B against index A -> silent recall collapse.

**Invariant I3: Authorization is a query predicate.** Applied **before** ANN, on every hop, including rewrite and graph report lookup. Post-filter-only ANN: as the forbidden set grows, top-k fills with unauthorized neighbors and **authorized recall -> 0**.

### 2.2 Hybrid Retrieval & Fusion

Dense misses exact IDs (`TS-999`, SKUs, statute numbers). BM25 misses paraphrase. Production sketch (Anthropic Contextual Retrieval): chunk -> TF-IDF + embeddings -> BM25 top + dense top -> rank fusion -> top-K into the prompt.

#### RRF (Reciprocal Rank Fusion)

Cormack, Clarke, Buettcher, SIGIR 2009. Rank-only, scale-free:

```
RRF(d) = SUM over all retrievers r of: 1 / (k + rank_r(d))
```

Default k = 60 in Elasticsearch `rank_constant`, OpenSearch, Weaviate `rankedFusion` (`1/(RANK+60)`), Qdrant RRF, and typical Postgres CTEs.

| Rank | Contribution (k=60) |
|---|---|
| 1 | 1/61 ~ 0.0164 |
| 60 | 1/120 = 0.0083 |

Documents in **both** lists outrank a document that wins only one list. BM25 unbounded scores and cosine [-1,1] never share a numeric space -- that is why RRF exists.

**Complexity.** After each retriever returns its top-k: O(k * |R|) to accumulate scores (hash map keyed by `doc_id`), then O(k * |R| * log(k * |R|)) to sort the union. In practice, n=100, m=2, so negligible compared to ANN + inverted-index latency.

**Key benchmarks:**
- Tuned hybrid RRF reaches **NDCG 0.7497** on WANDS -- **7.5%** above either retriever alone
- NDCG 0.7068 (hybrid) vs BM25 0.6983 vs KNN 0.6953

#### Score Fusion (When Magnitudes Are Trusted)

| Method | Who | Mechanism | When It Wins |
|---|---|---|---|
| **Relative Score Fusion** | Weaviate default since v1.24 | Min-max each list to [0,1], then alpha-weighted sum | Score gaps carry signal |
| **Alpha convex combo** | Pinecone single-index; Weaviate `alpha` | `combined = alpha * dense + (1-alpha) * sparse` | Same index, same query; A/B alpha |
| **DBSF** | Qdrant | Normalize by mean/std of the prefetch top-k | Calibrated retrievers; outlier-sensitive |
| **min_max + arithmetic_mean** | OpenSearch `normalization-processor` | Score-space mix via search pipeline | Explicit 0.3/0.7 weights |

**Pinecone production trap.** Sparse/BM25 scores unbounded; dense cosine ~[-1,1]. Without `hybrid_score_norm` (scale dense by alpha, sparse by 1-alpha **on the query vectors**), sparse **dominates**. This is a silent keyword-only system masquerading as hybrid.

### 2.3 Two-Stage Ranking: Bi-Encoder then Cross-Encoder

| Stage | Mechanism | Complexity | Typical k |
|---|---|---|---|
| **Bi-encoder (recall)** | Encode query once, encode docs offline, score by cosine/IP | O(1) query encode + ANN | k = 50-200 |
| **Cross-encoder (precision)** | Jointly attend over (query, document) -- one forward pass per candidate | O(n) inference calls | Keep 3-20 for generator |
| **ColBERT late interaction** | Per-token embeddings; score = SUM_i MAX_j cos(q_i, d_j) (MaxSim) | Docs encoded offline; query encoded once | Between bi/cross on quality/latency |

**Cross-encoder reranking** is the single largest precision gain in the pipeline:
- Recall@5: 0.695 -> 0.816 (**+17.4%**)
- MRR@3: 0.433 -> 0.605 (**+39.7%**)

**ColBERT details:** ColBERTv2 + PLAID: latency cut 2.5-7x GPU and 9-45x CPU vs vanilla ColBERTv2; tens of ms GPU / tens-to-few-hundreds ms CPU at 140M passages (paper-stated). BGE-M3 emits ColBERT + dense + sparse in **one** forward pass. Qdrant supports ColBERT natively.

**LLM-as-reranker.** Pointwise / pairwise / listwise. A frontier judge over 50 chunks dwarfs a cross-encoder in cost. Use a cheap model for agentic **binary** `grade_documents`, not as the primary 100-way ranker.

### 2.4 Chunking Strategies

Retrieval quality is often more sensitive to chunk policy than to embedding brand.

| Strategy | Throughput | Accuracy (Vecta 2026) | Best For |
|---|---|---|---|
| Fixed-size (200-word) | ~4.82 MB/s | Matches semantic on many tasks | Simple docs, fast indexing |
| Recursive (512-token) | Moderate | 69% (top in benchmark) | **Production default** |
| Semantic (similarity) | ~0.33 MB/s (14x slower) | 54-70% (variable) | Topic-dense documents |
| Late chunking | Embedding-only cost | Cuts top-20 retrieval failures ~67% with reranking | Context preservation |
| Agentic (LLM-decided) | 10-50x indexing cost | Highest retrieval quality | High-value corpora |
| Hierarchical/Parent-Child | Moderate | Surgical search + rich context | Long documents |

**Expanded strategy comparison:**

| Strategy | Extra Model Calls | Helps | Hurts |
|---|---|---|---|
| Fixed token window + overlap | No | Predictable vector count | Mid-sentence splits; orphaned pronouns |
| Recursive (`\n\n` -> `\n` -> `.` -> ` `) | No | Fewer mid-sentence breaks | Unaware of semantic boundaries |
| Sentence / structure-aware | No | Legal/markdown headings | Uneven sizes; huge tables |
| Semantic (embedding breakpoints) | Embed sentences | Topic shifts | Cost + unstable boundaries |
| Title/summary prepend | Summary: yes | Cheap lexical boost | Generic summary != chunk-specific |
| **Contextual Retrieval** (Anthropic, 2024-09-19) | LLM per chunk; **prompt-cache the doc** | BM25 **and** dense **and** reranker **and** generator see situated text | Ingest cost; PII spread |
| **Late chunking** (Jina, arXiv 2409.04701) | No extra LLM; long-context embedder | Dense vectors carry doc-level context via token-then-pool | Lexical index unchanged |
| **Contextualized chunk models** (`voyage-context-4`) | No extra LLM | Chunk vectors conditioned on the full document | Vendor API; BM25 text unchanged |
| Parent-document / small-to-big | No | Retrieve small, generate on parent | Parent may exceed context; ACL must copy to both |

**2026 consensus:** Recursive splitting is the production default. Semantic chunking only when eval proves it justifies the 10x processing cost. Optimal chunk size: **512-1024 tokens**. A "context cliff" at ~2,500 tokens where response quality degrades.

**Practical starting point [inferred]:** 400-800 tokens, 10-20% overlap, sentence snap, `doc_id`/`section`/`acl`/`version` on every chunk, parent pointer for generate-time expansion.

#### Contextual Retrieval Eval Results

Anthropic's Contextual Retrieval (Gemini Text 004, top-20, 1-recall@20):

| Configuration | Failure Rate | Improvement vs Baseline |
|---|---|---|
| Baseline | **5.7%** | -- |
| Contextual embeddings | **3.7%** | -35% |
| Contextual embeddings + BM25 | **2.9%** | -49% |
| Contextual embeddings + BM25 + rerank (150->20) | **1.9%** | **-67%** |

Stated assumptions: ~800-token chunks, ~8k-token docs, ~50-token instructions, ~100-token contexts. Prompt-cache contextualize: **$1.02 / 1M document tokens** (one-time). Cache: >2x latency cut, up to 90% cost cut on cached prefixes; TTL 5 minutes; 737-chunk demo ingest **~$15 -> ~$3** at 70-80% cache hits.

#### Late Chunking Benchmarks

Embed full document (or max window), mean-pool token vectors per chunk. Jina BEIR nDCG@10 (traditional -> late):

| Dataset | Traditional | Late Chunking | Delta |
|---|---|---|---|
| SciFact | 64.20% | **66.10%** | +1.90 |
| TREC-COVID | 63.36% | 64.70% | +1.34 |
| FiQA2018 | 33.25% | 33.84% | +0.59 |
| NFCorpus | 23.46% | **29.98%** | +6.52 |
| Quora | 87.19% | 87.19% | 0 (no gain on 62-char docs) |

Berlin Wikipedia cosine "Berlin" vs "Its more than 3.85 million inhabitants...": **0.7084 -> 0.8249**. Does **not** inject company names into BM25.

#### Voyage-Context-4

(2026-06-29): **$0.12 / 1M**; 32k/chunk, 120k document. Vendor claim vs Jina-v3 late / Anthropic contextual: **+23.66%** / **+6.76%** on chunk-level retrieval -- **vendor-stated, not independently replicated**.

#### GraphRAG Chunking

Longer chunks -> fewer extract LLM calls but lost-in-the-middle of early-chunk entities. FastGraphRAG: **50-100 token** chunks for co-occurrence graphs. LangGraph tutorial `chunk_size=100`, `overlap=50` is a tutorial setting, not a production default.

### 2.5 Embedding Models

**Critical caveat**: MTEB is a useful prior, not a decision oracle. One legal retrieval system found the MTEB top-3 models ranked 5th, 7th, and 2nd on their in-domain eval while BGE-large-en-v1.5 (MTEB rank 11th) won. **Always run in-domain evaluation.**

| Model | Dim | Context | $/1M Tokens | Notes |
|---|---|---|---|---|
| Qwen3-Embedding-8B | Variable | 32K | Self-hosted | MTEB ~70.6 |
| Jina v5-text | -- | -- | TBD | MTEB 71.7 (v2) |
| Cohere embed-v4 | 256-1536 (default 1536) | 128K; text+image | $0.12 text; $0.47 image | Confirm dashboard |
| OpenAI text-embedding-3-large | 3072 (Matryoshka) | 8,192 | $0.13 | MTEB 64.6; `dimensions` shortens |
| OpenAI text-embedding-3-small | 1536 (Matryoshka) | 8,192 | $0.02 | MTEB ~62.3 |
| OpenAI text-embedding-ada-002 | 1536 | 8,192 | $0.10 | Do not start new indexes |
| Voyage voyage-4-large | 1024 (256/512/2048) | 32K | $0.12; 200M free | Batch 33% off |
| Voyage voyage-4 | 1024 | 32K | $0.06; 200M free | Quality/cost pick |
| Voyage voyage-4-lite | 1024 | 32K | $0.02; 200M free | Latency/cost |
| Voyage voyage-context-4 | 1024 | 32K / 120K doc | $0.12 | Contextualized chunks |
| Voyage voyage-code-4 | 1024 | 32K | $0.12 | Code retrieval |
| Pinecone llama-text-embed-v2 | -- | -- | $0.16 | Hosted |
| Pinecone multilingual-e5-large | -- | -- | $0.08 | Hosted |
| Pinecone pinecone-sparse-english-v0 | sparse | -- | $0.08 | Hybrid lexical |
| BAAI BGE-M3 | 1024 dense + sparse + ColBERT | 8,192; 100+ langs | Self-host (~569M params) | One pass, three modes |

**OpenAI embeddings hard limits:** 8192 tokens/input, 2048 inputs/request, 300,000 tokens summed. Batch commonly 50% off (3-small -> $0.01/1M, 3-large -> $0.065/1M), up to 24h. Vendor-stated: a 256-dim `3-large` can beat unshortened 1536-dim `ada-002` on MTEB retrieval.

**Voyage-4 / 4-lite / 4-large** share a vector space (official) -- rare. Do not assume cross-vendor compatibility.

**Storage math [inferred]:** float32 1536-d ~ 6.1 KB/vector; 3072-d ~ 12.3 KB. 10M chunks at 1536-d ~ 61 GB raw; Pinecone $0.33/GB/mo -> ~$20/mo if billed size matched raw vectors (indexes, metadata, sparse payloads add). Elastic BBQ 32x (vendor claim) and Cohere `int8`/`binary`/`ubinary` are the lever when storage, not embed $, dominates.

**Matryoshka dimensionality reduction:** Switching from 3072-dim to 768-dim reduced total system cost by **55%** while maintaining **92%** retrieval accuracy.

### 2.6 Agentic RAG State Machine

**Naive:** always retrieve top-k, always generate. **Advanced:** query transform + hybrid + rerank, still a DAG. **Agentic:** retrieval is a **tool** with a bounded loop.

```
                    +-----------------------------+
                    | generate_query_or_respond   |
                    | (model + retriever.bind)    |
                    +--------------+--------------+
                     tool call?    |     no tool: respond
                    +--------------+--------------+
                    v                             v
            +---------------+              +------------+
            | ToolNode      |              |  END       |
            | retrieve      |              +------------+
            +-------+-------+
                    v
            +---------------+     all irrelevant
            | grade_docs    +------------------------+
            | yes/no        |                        v
            +-------+-------+              +-----------------+
              some  | relevant             | rewrite_question|
                    v                      +--------+--------+
            +---------------+                       |
            | generate_     |                       |  MUST cap hops
            | answer        |                       |  (tutorial does not)
            +---------------+                       v
                                           back to generate_query...
```

**Simplified state machine view:**

```
                    +------------+
                    |  INITIAL   |
                    |  QUERY     |
                    +------+-----+
                           |
                           v
                    +------------+
               +----| RETRIEVE   |----+
               |    +------+-----+    |
               |           |          |
         insufficient   sufficient   no results
         evidence       evidence
               |           |          |
               v           v          v
        +----------+ +--------+ +---------+
        | REWRITE  | | ANSWER | | DECLINE |
        | QUERY    | |        | |         |
        +----+-----+ +--------+ +---------+
             |
             v
        (loop back to RETRIEVE, max 3 rounds)
```

**Carnegie Mellon (June 2026):** hallucinations fell from **14.1% to 4.9%** on a 9,000-question financial-compliance dataset at ~220ms extra latency per round.

**Key invariant:** Agentic RAG with knowledge graphs cut hallucination by **~62%** across 47 production deployments (May 2026 MLOps Community benchmark).

#### Key Agentic Patterns

**Self-RAG** (Asai et al., ICLR 2024): reflection tokens `Retrieve` {yes, no, continue}; `IsRel` {relevant, irrelevant}; `IsSup` {fully, partial, none}; `IsUse` {5...1}. Production teams almost always **prompt** a separate grader rather than train tokens.

**CRAG** (Yan et al.): evaluator -> Correct / Incorrect (web/external) / Ambiguous (mix). Open web fallback is an **exfil path** for confidential queries.

**HyDE** (Gao et al.): LLM writes a hypothetical answer; embed that; retrieve neighbors. LlamaIndex documents two failures: mis-interprets queries without corpus context; **biases** open-ended queries.

**IRCoT** (Trivedi et al., ACL 2023): what to retrieve at step n depends on step n-1. Loop: retrieve from question -> generate next CoT sentence -> that sentence is the next query -> until answer or max steps. GPT-3 paper-stated: retrieval up to **+21 points**, QA up to **+15 points** on HotpotQA / 2Wiki / MuSiQue / IIRC. HippoRAG: single-step PPR **10-20x cheaper, 6-13x faster** than iterative retrieve in HippoRAG's experiments.

**Loop bound invariant.** Official LangGraph tutorial can loop until runtime timeout. Production: `retry_count`, wall-clock, terminal `insufficient_evidence`. Do **not** fall back to parametric knowledge on ACL-sensitive corpora.

### 2.7 Graph RAG

Vector RAG fails **global** questions ("themes in this corpus") -- query-focused summarization, not top-k lookup (Edge et al., arXiv 2404.16130). Eval: ~1M token datasets; GraphRAG beats vector RAG on **comprehensiveness and diversity** (LLM-as-judge).

**Index pipeline:** chunk (TextUnits) -> LLM extract entities/relationships/claims -> KG -> **Leiden** hierarchical communities -> bottom-up **community reports** -> embed units/entities/reports -> persist Parquet + vector. Microsoft: LLM extraction is ~**75% of indexing cost**. FastGraphRAG: NLP noun phrases + co-occurrence, 50-100 token chunks, cheaper/noisier, aimed at global questions. Graph-O1 uses Monte Carlo Tree Search to explore graph nodes without exceeding context limits.

`microsoft/graphrag` (fetched 2026-09): **maintenance mode**, bugfix/CVE only (e.g. v3.0.9 2026-04-13). Not an officially supported Microsoft offering. Always `graphrag init --force` between minor versions.

#### Query Modes

| Query Mode | Mechanism | Query Class |
|---|---|---|
| **Local** | Match entities -> neighborhood + chunks | Entity-specific |
| **Global** | Map-reduce over **all** community reports | Corpus themes |
| **DRIFT** | HyDE primer + top-K reports -> follow-ups -> local iterations (default **2**) | Local that needs a global primer |
| **Basic** | Vanilla top-k vector | Ablation |
| **Question Generation** | From prior user queries, emit next questions | Investigator follow-ups |

#### Graph RAG Variants

**LazyGraphRAG** (MSR 2024-11-25): no LLM community summaries at index time. Index cost **identical to vector RAG** and **0.1% of full GraphRAG** (Microsoft-stated). Query: iterative deepening with relevance-test budget (Z100 / Z500 / Z1500). At Z100: **>700x lower query cost** than GraphRAG global for comparable global quality. At Z500 (4% of GraphRAG global query cost): beats compared methods on local+global.

**LightRAG:** dual-level retrieve + incremental graph updates (avoid full rebuilds).

**HippoRAG:** LLM + KG + Personalized PageRank; up to ~20% over SOTA RAG on multi-hop QA (paper). HippoRAG 2: indexing tokens e.g. 9M vs 115M for GraphRAG-class on MuSiQue. Also: structure-based methods can **drop 5-10 F1** on simple QA vs strong embeddings -- keep a vector path for factoid.

**RAPTOR:** recursive embed -> GMM soft clustering (BIC for k) -> abstractive summary -> tree; retrieve across levels.

**Production shape:** graph **and** vector. Router picks `vector_tool` vs `graph_local` vs `graph_global`. Systematic 2025 eval: community GraphRAG helps multi-hop/summarization; vector often wins single-hop; extraction noise is first-class. GraphRAG-Bench: not all graph methods beat a strong GPT-4o-mini baseline -- over-structure can hurt.

**Crash invariant.** Mid-Leiden crash -> entities without reports. Treat graph index as a versioned snapshot; query pins a complete `graph_build_id`.

### 2.8 Vector Database Selection

| Database | QPS (1M) | p99 Latency (10M) | Best For | Cost |
|---|---|---|---|---|
| pgvector | ~640 | 5-8ms | <10M vectors, existing Postgres | $0 (existing infra) |
| Qdrant | ~1,840 | ~12ms | Latency-critical, complex filters | $600-$1,200/mo |
| Weaviate | ~1,620 | ~16ms | Native hybrid search, multimodal | Moderate |
| Pinecone | ~1,620 | Varies | Zero-ops, quick scaling | $1,500-$3,000/mo |
| Milvus | High | Low | Billion-vector scale | Self-hosted |

**Scale reversal:** At 50M vectors, pgvectorscale (471 QPS) outperforms Qdrant (41.47 QPS). Above 1B vectors, only Vespa and Milvus distributed deployments are production-grade.

---

## 3. Token Economics & Cost Analysis

### 3.1 Cost Per 1K Queries -- Reference Mix

Public vendor pages do **not** sell a "RAG query" SKU. Figures below multiply published rates by a stated mix. State these assumptions in a design review.

**Assumptions (research reference query):**
- 1k user questions, **no** agent retries.
- Query embed 50 tokens; retrieve 80 fused chunks; rerank 80; keep 8 x 500 tokens = 4k context; generate 4k input + 400 output.
- Dense embedder: OpenAI `text-embedding-3-small` $0.02/1M.
- Rerank: Voyage `rerank-2.5` formula (q x n + SUM d_i).
- Generate: `gpt-5.6-luna` uncached $0.20 / $1.20 per 1M in/out.

| Component | Arithmetic | **[inferred] $ / 1k Queries** |
|---|---|---|
| Embedding (query) | 1k x 50 tok = 50k tok x $0.02/1M | **$0.001** |
| Voyage rerank-2.5 | (50x80 + 80x500 = 44,000) tok/q x $0.05/1M = $0.0022/q | **$2.20** |
| Generate luna uncached | 4k in x $0.20/1M = $0.0008; 400 out x $1.20/1M = $0.00048 -> $0.00128/q | **$1.28** |
| Vector search | pgvector near-zero; Pinecone ~$0.05/1k | **$0.01 - $0.05** |
| **Subtotal (simple RAG)** | embed + rerank + generate | **~ $3.50** |
| **Total (agentic, 4 rounds)** | 20-40x simple RAG | **~$30 - $200** |

**Excludes** vector DB RUs, graph map-reduce, and retries. **Rerank dominates this mix** on mini-tier generation.

### 3.2 Model-Tier Cost Comparison

Same 4k in + 400 out, uncached:

| Generator | In/Out per 1M | Generate [inferred] / Query | Generate [inferred] / 1k |
|---|---|---|---|
| `gpt-5.6-luna` | $0.20 / $1.20 | $0.00128 | **$1.28** |
| `gpt-5.6-terra` | $2 / $12 | ~$0.0128 | **$12.80** |
| Claude Sonnet 5 | $2 / $10 | $0.012 | **$12.00** |
| Claude Haiku 4.5 | $1 / $5 | $0.006 | **$6.00** |

**Key insight:** On Sonnet 5 / terra, **generation dominates**; on luna + Voyage rerank, **rerank dominates**.

### 3.3 Prompt-Caching Impact

**Anthropic (official multipliers, 2026):**
- 5-minute cache write **1.25x** base input, 1-hour write **2x**, cache read **0.1x** (Fable/Mythos 5.1 hits **0.025x**).
- Sonnet 5: base $2/MTok, 5m write $2.50, 1h write $4, hit $0.20, output $10.
- Minimum prefix 512-4096 tokens (model-specific); below floor -> silently not cached.
- Worked prefix example [inferred]: 1 write (1.25x) + 9 reads (0.9x total vs 10x uncached) = 2.15x vs 10x -> **~78.5% savings** on the cached block.

**OpenAI:**
- Auto cache on prompts >1,024 tokens, 128-token increments.
- `gpt-5.6-luna` cached input $0.02 (vs $0.20 uncached); cache writes $0.25.
- Regional processing: 10% uplift for eligible models released on/after 2026-03-05.

### 3.4 Rerank Cost Alternatives

| Path | Rate | **[inferred] / 1k User Queries** (1 search-unit, <=100 docs) |
|---|---|---|
| Voyage `rerank-2.5` | $0.05/1M tok | **$2.20** at the 44k-tok mix |
| Voyage `rerank-2.5-lite` | $0.02/1M | **~$1.00** |
| Cohere Rerank 3.5 (Bedrock) | $2.00 / 1k searches | **$2.00** if 1 unit/query |
| Cohere Rerank 4 Fast (Azure Foundry, preview) | $2.00 / 1k SU | **$2.00** |
| Cohere Rerank 4 Pro (Azure Foundry, preview) | $2.50 / 1k SU | **$2.50** |
| Pinecone Inference rerank | $2 / 1k requests | **$2.00** |
| Google Ranking API | $1.00 / 1k (100-doc units) | **$1.00**; 80k free units / 30 days |
| Bedrock Managed KB rerank | $0 | Included |
| Azure Semantic Ranker | 1k req/mo free, then region $/1k | Do not invent a USD rate |
| Self-hosted bge-reranker-v2-m3 | Your GPU/RAM | Well under **$1 / 1k** |

**Cohere search-unit inflation (official):** 1 query + up to 100 documents; if query+doc > 500 tokens, auto-split; each chunk counts as a document. 80 fused 800-token chunks can become >1 search unit per question. Hard cap: `num_documents * max_chunks_per_doc <= 10,000`. Voyage: <=1,000 docs; query+any doc <=32k; total tokens <= 600k.

### 3.5 Corpus Embedding Costs

1B tokens ~ 1M docs x 1k tok:

| Model | Cost to Embed 1B Tokens |
|---|---|
| OpenAI 3-small / Voyage-4-lite | **$20** |
| Voyage-4 | **$60** |
| Voyage-4-large / context-4 / Cohere embed-v4 text | **$120** |
| OpenAI 3-large | **$130** |
| BGE-M3 (self-hosted) | **$0** (your infra) |

Batch discounts: OpenAI **50% off**; Voyage **33% off** (12h; free-token credits do not apply to Batch).

**100M-token knowledge base** costs ~$13K with text-embedding-3-large or $0 with self-hosted BGE-M3.

### 3.6 Pinecone RU Path [inferred]

1 GB namespace -> 1 RU/query x $16-18 / M RU (Standard) -> **$0.016-0.018 / 1k**. Same query against a **100 GB** shared namespace: **$1.60-1.80 / 1k**. Enterprise RU: $24-27 / M.

### 3.7 Bedrock Managed KB (Official)

| Item | Price |
|---|---|
| Standard Retrieve | $1.00 / 1k API calls |
| Agentic Retrieve | $4.00 / 1k + $1.00 / 1k underlying Retrieve |
| Storage | $5.00 / GB raw / month |
| Official example (50 GB + 100k standard) | **$350/mo** |
| Official example (50 GB + 100k agentic x 2) | **$850/mo** |

Plus FM tokens. AOSS floor: ~2 OCUs x $0.24/hr ~ $345/mo -- not a Bedrock SKU.

### 3.8 Production Cost by Scale

| Scale | Monthly Cost | Key Driver |
|---|---|---|
| Small (<10K queries/mo) | $150-$400 | LLM inference |
| Mid-size (10K-100K) | $600-$1,500 | LLM inference + vector DB |
| Enterprise (1M queries/mo) | $5,000-$15,000 | All components at scale |
| Demo vs production | $340/mo vs $61,000/mo | Documented case study |

**Build cost:** Basic prototype $10K-$25K. Production hybrid retrieval $25K-$60K. Enterprise agentic RAG $60K-$150K+.

**Cost formula:**
```
Monthly cost = (queries/day) * 30 * cost_per_query
             + vector_db_hosting
             + embedding_refresh_cost / refresh_interval_months
```

### 3.9 Latency SLA Targets

No major vendor publishes p50/p95/p99 for "RAG end-to-end." Decompose stages. Timeout numbers below marked **[policy]** or **[inferred]**.

| Stage | p50 | p95 | p99 | Mitigation |
|---|---|---|---|---|
| Embedding generation | ~70ms | ~100ms | ~120ms | Batch embeddings, local model |
| Hybrid retrieval | ~6ms added | ~10ms | ~15ms | BM25 + vector in parallel |
| Cross-encoder rerank (50) | ~100ms | ~200ms | ~300ms | Lighter reranker, reduce candidates |
| LLM generation | ~1000ms | ~2000ms | ~3000ms | Prompt caching, streaming |
| **Simple RAG total** | **~700ms** | **~2s** | **~3s** | |
| **Agentic RAG (3 rounds)** | **~8s** | **~15s** | **~20s** | Parallel retrieval, early stopping |

**Architecture-derived SLO targets [inferred] -- set retrieve+rerank independently of generate:**

| Metric | Retrieve+Rerank [inferred] | E2E including Generate [inferred] | Rationale |
|---|---|---|---|
| p50 | 150-400 ms | 800 ms-2 s (generate-dominated) | embed RTT + O(100 ms) ANN + one rerank RTT |
| p95 | 400 ms-1.5 s | 2-6 s | filter-heavy IVF, one retry, or one extra agent hop |
| p99 | 1-3 s (then fail closed) | 8-15 s agentic with cap=3; unbounded if uncapped | timeout 200-500 ms retrieve [policy]; hedge replica |

**Mitigations by percentile:**

- **p50:** hybrid in parallel (not sequential BM25-then-dense); prompt-cache static prefixes (>2x on hits); keep fused N and rerank `top_n` small (5-20 to generator).
- **p95:** timeout+hedge retrieve to a replica/region, cancel loser; Adaptive-RAG skip retrieve on chitchat; ES `rank_window_size` default 10 is conservative -- raising to 50-100 is a latency/RAM choice.
- **p99:** circuit-break the vector DB **independently** of the LLM; on retrieve failure surface `retrieval_degraded`; bulkhead Pinecone RU pool from generate pool; drop rerank to fused top-8 on RPM/timeout.

### 3.10 Throughput, Back-Pressure & Availability

**Throughput:**
- pgvector handles ~640 QPS at 1M vectors on a single node
- For >1K QPS, use Qdrant (1,840 QPS) or horizontal sharding

**Published rate limits:**

| Dependency | Published Limit |
|---|---|
| Cohere Rerank | Trial **10 RPM**; production **1,000 RPM** |
| Cohere Embed | **2,000 inputs/min** |
| Cohere Embed images | Trial 5 / prod **400** inputs/min |
| OpenAI embeddings | Org-tier RPM/TPM; hard 300k tok/request, 2048 inputs |
| Pinecone serverless | RU/WU quotas by plan; Dedicated Read Nodes remove noisy-neighbor limits |
| Vertex RAG retrieval | **600 RPM** in architecture notes |
| Vertex Ranking | 80k free units/30d; max 1000 records/call |
| Vertex management APIs | 60 RPM; 3 concurrent imports/region; 10,000 files/import |
| Bedrock Guardrails | $0.15 / 1k text units |

**Capacity identity:** `retrieve_RPM = user_QPS x expected_hops`. Agent loops: 3 retrieves x 1k user QPS = 3k retrieve RPM -- size the vector DB **and** the reranker for the loop, not the user QPS. Cohere prod rerank 1000 RPM saturates at ~16 QPS sustained before shedding.

**Back-pressure design:** (1) admission control on the agent gateway (max in-flight hops); (2) bulkhead thread/connection pools: retrieve vs rerank vs generate; (3) token-bucket per tenant (noisy-neighbor); (4) degrade: skip rerank -> BM25-only -> last-good retrieve cache -> **refusal** if policy forbids ungrounded generate; (5) Vertex 429 `RESOURCE_EXHAUSTED`: retry, raise quota, Priority PayGo, or Provisioned Throughput.

**Availability target:** 99.9% (8.7 hours downtime/year). Achieved via multi-AZ vector DB deployment + LLM provider failover (OpenAI -> Anthropic -> self-hosted). Pinecone Enterprise: **99.95%** uptime SLA (Starter/Builder/Standard: none on the public table).

**RPO/RTO:** RPO = 0 for vector index (replicated). RTO = <5 minutes with pre-warmed standby. For corpus re-embedding after model upgrade: budget 4-8 hours for 100M tokens.

### 3.11 NFRs and Explicit Trade-Offs

| NFR | Production Stance | Competes With |
|---|---|---|
| **Availability** | Pinecone Enterprise 99.95% uptime SLA; circuit-break index independently of LLM | Cost (Enterprise min $500/mo vs Standard $50/mo) |
| **RPO** | Ingest watermark + sha256; alias flip only after complete upsert. Graph: pin `graph_build_id`. Pinecone serverless **eventual** -- upsert-then-query can miss | Freshness (CDC lag vs query-after-write) |
| **RTO** | Query alias rollback to previous `index_version` / snapshot. LangGraph `PostgresSaver` resumes super-steps; `InMemorySaver` loses the rewrite loop | Index rebuild time vs dual-write shadow |
| **Consistency** | Weaviate: `ONE` / `QUORUM` (default) / `ALL`. QUORUM = n/2+1 (RF=6 -> 4). Hybrid under `ONE` can cite a deleted replica. Use `QUORUM` for RAG corpora that must not cite deleted docs | p99 (wait for replicas) |
| **Compliance** | Pinecone: encryption all plans; SOC 2 all; GDPR/ISO 27001 from Builder up; HIPAA Standard $190/mo or Enterprise included; audit logs/CMEK/SCIM: Enterprise | Latency (residency path) and $ |
| **Recall vs Precision** | Hybrid for IDs; cross-encoder 50-150 -> 5-20. Over-retrieve k=50 into 128k -> lost-in-the-middle + $ explosion | Cost and p95 |
| **Security vs Recall** | ACL pre-filter / namespace vs post-filter. Post-filter -> authorized recall collapse | Ops (more namespaces) vs Pinecone RU (1 RU/GB namespace vs 100x on a fat shared namespace) |

---

## 4. Distributed Resilience & Security

### 4.1 Durable Execution Patterns

#### Platform Landscape (Mid-2026)

- **LangGraph persistence:** Saves graph state at each superstep, organizes runs by thread. Strongest agent-native checkpointing. `PostgresSaver` / `AsyncPostgresSaver` for production. Agent Server hides this; self-hosted graphs must compile with a checkpointer or every crash **loses the rewrite loop**. Conversation memory stays in the checkpointer, not the MCP session.
- **Temporal:** Durable execution with automatic retries, timeouts, state persistence. Ideal for multi-step RAG pipelines (ingest -> chunk -> embed -> index).
- **DBOS:** Database-backed workflow persistence with AI stack integrations.
- **AWS Lambda Durable Functions:** Steps, waits, checkpoints, replay, retries (December 2025).

#### Ingest as a Replayable Workflow

1. Source watermark (S3 etag / Drive revision / DB CDC LSN / SharePoint ACL version) -- the **idempotency key** for the document version.
2. Raw blob + sha256 (poisoning detection). Failed parse -> dead-letter / quarantine, not into the live alias.
3. Parse/chunk with `chunk_id = hash(doc_id, chunker_version, text)`.
4. Embed job keyed by `embed_model + dim + chunk_id` (replay skips completed keys).
5. Upsert vectors with `index_version`; **only then** flip the query alias (compare-and-swap on the alias is the distributed lock / fencing token).
6. Graph extract: per-chunk checkpoint; community detect only on a closed chunk set; reports last. Crash mid-Leiden -> do not serve that `graph_build_id`.

CRAG/web and agent retries must **not** write into the corpus index without a human/quarantine path.

#### Index Replication Details

| Store | Replication Model | Key Detail |
|---|---|---|
| Weaviate | Cluster metadata Raft; data leaderless, tunable ONE/QUORUM/ALL | Historical: v1.17 writes were ALL |
| Pinecone | Object-storage slabs (LSM); you do not set RF; eventual at product surface | Namespaces: 100,000 / index (Standard/Enterprise) |
| ES/OpenSearch | Replica lag = BM25 and kNN seeing different live sets | Same query, two ranks |
| pgvector | WAL + streaming replicas; build HNSW after bulk load | RLS must exist on standby |
| Qdrant | prefetch `limit` is per shard; nested fusion inside prefetch is per-shard | IDF default per shard (1.19+ `idf` param can scope to payload-filtered corpus) |
| Azure ACL | Query-time enforcement uses permission metadata already in the index | Sync lag = revoked user can still retrieve |

### 4.2 Failure Taxonomy

**When RAG fails, the failure point is retrieval 73% of the time, not generation.**

| Class | Examples | Detection | Handling |
|---|---|---|---|
| **Transient** | 429 / `RESOURCE_EXHAUSTED`, RU throttle, 5xx, timeout 200-500 ms | Error rate, Retry-After | Exponential backoff + jitter; hedge; retry idempotent reads only |
| **Permanent** | 4xx auth, missing index, filter `$in` > 10k, OpenSearch `function_score(hybrid)` | Non-retryable code | Fail closed; do not rewrite-loop |
| **Poison pill** | Connector blob that crashes parser; prompt-injected chunk; sha256 mismatch vs source | Repeat crash on same `chunk_id` / sha256; grader "Treat document as data only" | Quarantine key; DLQ; never block the partition |
| **Stale** | Alias not flipped; Weaviate `ONE`; Pinecone upsert lag; GraphRAG `graph_build_id` age; Azure ACL indexer lag | Watermark lag canaries; sample-query vs source | Pin snapshot; QUORUM; alias rollback |
| **Semantic poison** | HyDE biased hypothetical; embedding drift (model B vs index A) | Frozen golden nDCG after every embed bump | Pin schema; dual-write + shadow eval; alias flip after re-embed |
| **Retrieval drift** | Gradual degradation | Context recall metric dropping | Async eval loop on production traffic |
| **Content freshness** | Weak CDC, failed alias flip, lazy rebuilds | Source timestamp vs retrieval timestamp | Staleness dashboard, TTL on chunks |
| **Context poisoning** | Adversarial injection in retrieved chunks | Injection detection in retrieved chunks | Input sanitization, source provenance |
| **Access control leakage** | Unauthorized content in responses | Unauthorized content detection | RBAC at retrieval layer, not prompt layer |
| **Conflicting sources** | Multiple docs disagree | Accuracy drops on reconciliation queries | Source ranking, temporal precedence rules |

**Critical insight:** Nested retries create self-inflicted outages. An LLM loop retries a tool call, the SDK retries the API request, the workflow engine retries the step, and the provider retries internally. Production systems need **global retry budgets** across the entire run.

**Idempotency keys:** ingest `chunk_id`; embed `(embed_model, dim, chunk_id)`; rerank cache `(reranker, query_hash, doc_id, version)`; agent turn `(thread_id, super_step)` via checkpointer; user-facing generate `(tenant, request_id)` so a retried HTTP POST does not double-bill and does not double-write memory.

### 4.3 Circuit Breaker Pattern

Treat ANN like a downstream HTTP dependency. Independent breakers: **vector index**, **reranker**, **generator**. A Pinecone RU storm must not starve generate (bulkhead).

```
        failures >= threshold or error-rate window
  +----------+  ------------------------------------>  +----------+
  |  CLOSED  |                                         |   OPEN   |
  | pass all |  success resets consecutive count       | fail fast|
  +----+-----+                                         +----+-----+
       ^                                                    | cooldown elapsed
       | trial success                                      v
       |                                              +----------+
       +------------ trial OK ------------------------| HALF-OPEN|
                    trial fail -> OPEN                | 1 probe  |
                                                      +----------+
```

**Thresholds [policy]:** retrieve timeout 200-500 ms; trip on 5xx, `resource_exhausted`, RU throttle, Cohere 1000 RPM exhaustion; cooldown tens of seconds; one probe in half-open.

**Fallback chain:** (1) last-good retrieve cache, (2) BM25-only, (3) "index unavailable" **refusal** -- never generate ungrounded if policy forbids. Hedging: duplicate retrieve to replica/region on p99; cancel loser. Agent: on retrieve failure, do not infinite rewrite; surface `retrieval_degraded`.

**Model chain:** primary FM -> secondary FM (different provider) -> **deterministic** extractive fallback (return top chunk titles + "insufficient evidence"). CRAG "Incorrect" -> web only into allowlisted licensed corpora.

### 4.4 Poison-Pill Detection

- **EchoLeak (late 2025):** Unclicked email manipulated Microsoft 365 Copilot's RAG pipeline, exfiltrating corporate data.
- **March 2026 mass poisoning:** Flooded external knowledge bases with manipulated data, forcing AIs to push false information to millions.
- **Detection:** hash-based integrity checks on ingested content, source reputation scoring, anomaly detection on chunk content distributions.

### 4.5 Zero-Trust RAG Architecture

Security layers across the full pipeline:

1. **User Layer:** Authentication, authorization, identity verification.
2. **Input Layer:** Sanitization filters for prompt injection.
3. **Retrieval Layer:** RBAC + ABAC enforced at both index-time and query-time. Document-level permissions during retrieval is the most effective defense against data leakage.
4. **Model Layer:** LLM generation with guardrails.
5. **Output Layer:** Response scanning for PII, secrets, and unauthorized content.

**Key principle:** A model cannot be trusted to "unsee" unauthorized context. Access control must sit inside retrieval, not only around the final answer.

#### Zero-Trust MCP

`tools/call` on a retriever is a **data exfil API**. Five principles:

1. **Server-side identity.** Tenant/ACL from verified token / `RunContext`, never from tool arguments the model filled (`tenant_id` in JSON schema is a leak primitive). Predicate pushdown so ANN never ranks cross-tenant rows.
2. **Least privilege per tool.** `retrieve_public_kb` vs `retrieve_hr` vs `sql_customer`. No omnibus `search(query, collection)`.
3. **Stateless MCP + stateful RAG.** Memory in the checkpointer, not the MCP session.
4. **No raw chunk echo** to unauthorized traces. Azure agentic retrieve can return sensitivity labels in-band.
5. **Hosted MCP / cloud RAG:** provider network sees queries; contract residency (OpenAI regional 10% uplift). Azure knowledge base MCP endpoint must use the same Entra token path or MCP is a bypass.

#### Isolation Ladder

| Pattern | Guarantee | Cost |
|---|---|---|
| Metadata `tenant_id` filter | App-bug can omit filter | Cheapest; Pinecone scans full namespace |
| **Namespace / collection / index per tenant** | Query cannot cross (1 GB tenant = 1 RU; 100x1 GB cheaper than 100 GB filter) | Pinecone Standard 20 indexes/project, 100k namespaces |
| Azure document-level ACL | Entra token vs ingested `userIds`/`groupIds`/`rbacScope`; Graph group expansion at query time | Indexer must ingest permission metadata; sync lag is a leak window |
| Weaviate native MT | Separate shard per tenant; omit tenant key = error, not scan. `ACTIVE`/`INACTIVE`/`OFFLOADED`. Blog: 50,000+ active shards/node; 20 nodes -> 1M concurrently active tenants | Shard ops |
| Instance / BYOC | Strongest (HIPAA/finance). Pinecone BYOC: zero inbound SSH; PrivateLink / PSC | Highest $ |

### 4.6 PII Filtering & Compliance

| Framework | Key RAG Requirements |
|---|---|
| HIPAA | AES-256 encrypted PHI storage, RBAC, immutable audit logging, de-identification, MFA |
| SOC 2 | Type II audit trails for all model interactions, data access controls |
| GDPR | Data subject vector tracking for deletion requests, right to erasure across all chunks |

**GDPR deletion challenge:** Every fragmented, embedded vector chunk related to a data subject must be destroyed. Requires rigorous metadata tagging during ingestion with data subject IDs on every chunk.

**PII pipeline (detection -> redaction -> audit):**

1. **Detect** at ingest (deterministic + ML DLP) **before** embed. Vectors are derived personal data. Embed APIs see plaintext -- DPA, zero-retention, or self-host BGE-M3.
2. **Redact** before Contextual Retrieval prepend (otherwise names/quarters/revenue copy into every chunk -- better retrieval, larger blast radius). Second gate after retrieve, before prompt.
3. **Audit** immutable provenance (who retrieved which `chunk_id`, not necessarily full text in shared SaaS traces).
4. **Graph:** extraction amplifies PII into entity nodes; community reports can summarize secrets into a globally readable node -- ACL on **reports**, not just raw chunks.
5. **Cohere embed-v4 images:** interleaved tokens `(pixels/784)*4 + text`; ID-card screenshots are a PII ingest event.

### 4.7 Audit Trails & Auditability

Mandatory: immutable, hash-chained logs for every query, denial, and label change, with inline PII/PHI masking in snippets and prompts. Use a centralized gateway that mediates all model calls.

**Chain-of-custody fields:** `source_uri`, `version`, `chunk_id`, `char_span`, `retriever` (bm25|dense|graph_local|web), `rerank_score`, `user_id`, `tenant`, `index_build_id`.

ALCE (Gao et al., EMNLP 2023): on ELI5 even the best models lacked complete citation support **50% of the time**. Production metric **provenance fidelity** = cited IDs (a) were retrieved, (b) support the claim (NLI/`IsSup`), (c) the user was entitled to see. Constrained decode: citations subset-of retrieved IDs; hash-verify chunk body vs ingest sha256. RAGAS WikiEval: faithfulness aligned with humans at **0.95** accuracy vs **0.72** for direct GPT scoring.

OWASP LLM Top 10 mapping: poisoned ingest, embedding weaknesses, sensitive disclosure via retrieved context. Measure **leakage rate**, **entitlement violation rate**, **provenance fidelity**, **false refusal**.

---

## 5. Trade-Offs & Failure Modes (Comprehensive)

| # | Failure | Cause | Detection | Mitigation |
|---|---|---|---|---|
| 1 | **Lost-in-the-middle** | U-shaped attention; GPT-3.5-Turbo with answer in middle scored **below closed-book (56.1%)** on the paper's multi-doc QA | Faithfulness drop as k increases; position ablation | Rerank to 5-20; put top chunks at **edges**; do not treat 128k as uniformly usable |
| 2 | **Embedding drift** | Change model id / dim / Matryoshka trim / metric / API snapshot | nDCG on frozen golden set after every embed bump | Pin schema; dual-write + shadow; alias flip after full re-embed |
| 3 | **ACL leak** | Omitted metadata filter; `$in` >10k; Weaviate `ONE` tombstone; Azure indexer lag; hop without re-applying token; CRAG web; graph reports; prompt-injected chunk | Entitlement-violation canaries; DLP on traces | Namespace/shard isolation; QUORUM; grader "document is data only"; ACL on reports |
| 4 | **Authorized recall -> 0** | Post-filter-only ANN as forbidden set grows | Tenant-stratified recall | Bitmap/IVF bypass; namespace isolation (Pinecone) |
| 5 | **Stale index** | CDC lag, failed upsert, alias not flipped, replica `ONE`, old `graph_build_id` | Watermark lag; source canaries | Alias swap; QUORUM; pin complete snapshot |
| 6 | **Hallucinated citations** | Model invents `[doc 17]` / URL; ALCE: ~50% lack complete support on ELI5 | ID not in retrieved set; NLI/`IsSup` | Constrained cites; refuse if empty retrieve or `IsSup=no` |
| 7 | **Hybrid score collapse** | Pinecone sparse unbounded vs dense [-1,1]; client alpha != Weaviate 0.75 default | Keyword-only or semantic-only in practice | `hybrid_score_norm`; set alpha explicitly; prefer RRF if scales untrusted |
| 8 | **Score mixing bugs** | Raw BM25 and cosine scores naively added without normalization | Retrieval quality degraded | Use RRF or proper normalization |
| 9 | **Filter+IVF collapse** | Naive IVF+filter at 50% / 90% selectivity | Recall -> 0 for rare tenants | IVF bypass; adaptive scan fraction; namespaces |
| 10 | **ES window too small** | `rank_window_size=10` default | Reranker never sees the right doc | Raise 50-100 (latency/RAM trade) |
| 11 | **Qdrant per-shard fuse** | Fusion inside prefetch on multi-shard | Wrong global rank | Top-level `FusionQuery` |
| 12 | **OpenSearch illegal nest** | `function_score(hybrid)` | Silent wrong scores / error | Pipeline fusion only |
| 13 | **HyDE bias** | Hypothetical without corpus context | Wrong neighborhood | `include_original=True`; skip on open-ended |
| 14 | **Infinite agent loop** | No hop cap; grader false negatives | RPM/cost spike; timeout | `retry_count` max 3; wall-clock; `insufficient_evidence` |
| 15 | **Over-retrieval** | Too many passages sent to generator | Cost spike + lost-in-the-middle degradation | Rerank to 5-20 |
| 16 | **Poisoned documents** | Unvalidated retrieved text injects instructions or bad facts | sha256 + source allowlist | Quarantine; signed ingest |
| 17 | **Graph explosion** | LLM NER duplicates, co-occurrence cliques | Entity count vs doc count | Canonicalize; Fast/LazyGraphRAG; cap degree |
| 18 | **Community staleness** | New docs, old Leiden cut | `graph_build_id` age | LightRAG incremental or scheduled rebuild |
| 19 | **Graph overuse** | Global graph methods for ordinary fact retrieval | Unnecessary cost | Only use for global synthesis or multi-hop |
| 20 | **Rerank RPM/timeout** | 1k QPS x 80 docs vs Cohere 1000 RPM | Rerank error rate | Cache; lite/local bge; drop to fused top-8 |
| 21 | **Contextual PII spread** | Prepend copies secrets into every chunk | DLP on chunks | Redact **before** contextualize |
| 22 | **Maintenance-mode GraphRAG** | OSS CVE/deps only | GitHub status | Treat as algorithm, not product SLA |
| 23 | **Bedrock OSS leftover** | KB deleted, AOSS collection remains | AWS bill | Delete the collection |
| 24 | **Voyage Batch vs free tier** | Batch does not consume free 200M | Invoice surprise | Don't mix Batch with free-token planning |
| 25 | **Late chunking on short docs** | Quora-length (~62 chars): no gain | Per-set nDCG | Don't late-chunk short-passage corpora |
| 26 | **Nested retries** | LLM loop retries tool call, SDK retries API, workflow retries step, provider retries internally | Self-inflicted outages | Global retry budgets across entire run |

**The interview-friendly answer is not "RAG always helps." It is "RAG helps when the knowledge is large, mutable, access-controlled, or auditable. Otherwise, simpler context engineering may win."**

---

## 6. Production Enterprise Code

Two complete implementations provided: one using `structlog` (Opus-style) focusing on pipeline composition with fallback chains, and one stdlib-only (Grok-style) emphasizing protocol-based retriever/generator swapping with degradation tracking.

### Implementation A: Production RAG Pipeline with structlog

```python
"""
Production RAG pipeline with resilience patterns.
Demonstrates: retries with exponential backoff + jitter, circuit breaker,
fallback chains, structured logging with correlation IDs.
"""

import hashlib
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

# --- Structured Logging -------------------------------------------------------

logger = structlog.get_logger()


def create_correlation_id() -> str:
    return str(uuid.uuid4())[:12]


# --- Circuit Breaker ----------------------------------------------------------

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Standard circuit breaker: Closed -> Open -> Half-Open -> Closed."""

    name: str
    failure_threshold: int = 5
    recovery_timeout: float = 60.0
    half_open_max_calls: int = 2

    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    failure_count: int = field(default=0, init=False)
    last_failure_time: float = field(default=0.0, init=False)
    half_open_calls: int = field(default=0, init=False)

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
                logger.info("circuit_breaker.half_open", breaker=self.name)
                return True
            return False
        if self.state == CircuitState.HALF_OPEN:
            return self.half_open_calls < self.half_open_max_calls
        return False

    def record_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            self.half_open_calls += 1
            if self.half_open_calls >= self.half_open_max_calls:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                logger.info("circuit_breaker.closed", breaker=self.name)
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            logger.warning("circuit_breaker.reopened", breaker=self.name)
        elif self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "circuit_breaker.opened",
                breaker=self.name,
                failures=self.failure_count,
            )


# --- Retry with Exponential Backoff + Jitter ----------------------------------

def retry_with_backoff(
    func,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
    retryable_exceptions: tuple = (ConnectionError, TimeoutError),
    correlation_id: str = "",
):
    """Execute func with exponential backoff and full jitter."""
    for attempt in range(max_retries + 1):
        try:
            result = func()
            if attempt > 0:
                logger.info(
                    "retry.succeeded",
                    attempt=attempt,
                    correlation_id=correlation_id,
                )
            return result
        except retryable_exceptions as e:
            if attempt == max_retries:
                logger.error(
                    "retry.exhausted",
                    attempts=max_retries + 1,
                    error=str(e),
                    correlation_id=correlation_id,
                )
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            if jitter:
                delay = random.uniform(0, delay)  # full jitter
            logger.warning(
                "retry.backoff",
                attempt=attempt + 1,
                delay_s=round(delay, 2),
                error=str(e),
                correlation_id=correlation_id,
            )
            time.sleep(delay)


# --- Fallback Chain -----------------------------------------------------------

@dataclass
class LLMProvider:
    name: str
    circuit_breaker: CircuitBreaker
    generate_fn: Any  # callable(prompt, context) -> str

    def call(self, prompt: str, context: str, correlation_id: str) -> str:
        if not self.circuit_breaker.can_execute():
            raise RuntimeError(f"{self.name} circuit open")

        def _invoke():
            return self.generate_fn(prompt, context)

        try:
            result = retry_with_backoff(
                _invoke,
                max_retries=2,
                base_delay=0.5,
                retryable_exceptions=(ConnectionError, TimeoutError),
                correlation_id=correlation_id,
            )
            self.circuit_breaker.record_success()
            return result
        except Exception as e:
            self.circuit_breaker.record_failure()
            raise


class FallbackChain:
    """Try providers in order. First success wins. All fail = raise."""

    def __init__(self, providers: list[LLMProvider]):
        self.providers = providers

    def generate(self, prompt: str, context: str, correlation_id: str) -> dict:
        errors = []
        for provider in self.providers:
            try:
                result = provider.call(prompt, context, correlation_id)
                return {
                    "answer": result,
                    "provider": provider.name,
                    "fallback_used": provider != self.providers[0],
                }
            except Exception as e:
                errors.append((provider.name, str(e)))
                logger.warning(
                    "fallback.provider_failed",
                    provider=provider.name,
                    error=str(e),
                    correlation_id=correlation_id,
                )
        raise RuntimeError(
            f"All providers failed: {errors}"
        )


# --- RAG Pipeline -------------------------------------------------------------

@dataclass
class RetrievedChunk:
    text: str
    source: str
    score: float
    metadata: dict


@dataclass
class RAGResult:
    answer: str
    chunks: list[RetrievedChunk]
    provider: str
    fallback_used: bool
    latency_ms: float
    correlation_id: str
    cache_hit: bool


class ProductionRAGPipeline:
    """
    Production RAG with hybrid retrieval, reranking, RBAC filtering,
    circuit breakers, and fallback chains.
    """

    def __init__(
        self,
        dense_retriever,      # callable(query_embedding) -> list[RetrievedChunk]
        sparse_retriever,     # callable(query_text) -> list[RetrievedChunk]
        embedding_fn,         # callable(text) -> list[float]
        reranker_fn,          # callable(query, chunks) -> list[RetrievedChunk]
        llm_chain: FallbackChain,
        semantic_cache=None,  # optional: callable with get/set
        rbac_filter_fn=None,  # callable(chunks, user_id) -> list[RetrievedChunk]
        rrf_k: int = 60,
        top_k_retrieval: int = 50,
        top_k_rerank: int = 10,
    ):
        self.dense_retriever = dense_retriever
        self.sparse_retriever = sparse_retriever
        self.embedding_fn = embedding_fn
        self.reranker_fn = reranker_fn
        self.llm_chain = llm_chain
        self.semantic_cache = semantic_cache
        self.rbac_filter_fn = rbac_filter_fn
        self.rrf_k = rrf_k
        self.top_k_retrieval = top_k_retrieval
        self.top_k_rerank = top_k_rerank

    def _rrf_fusion(
        self,
        dense_results: list[RetrievedChunk],
        sparse_results: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """Reciprocal Rank Fusion: score(d) = SUM(1/(k + rank(d)))."""
        scores: dict[str, float] = {}
        chunk_map: dict[str, RetrievedChunk] = {}

        for rank, chunk in enumerate(dense_results):
            key = hashlib.sha256(chunk.text.encode()).hexdigest()[:16]
            scores[key] = scores.get(key, 0) + 1.0 / (self.rrf_k + rank + 1)
            chunk_map[key] = chunk

        for rank, chunk in enumerate(sparse_results):
            key = hashlib.sha256(chunk.text.encode()).hexdigest()[:16]
            scores[key] = scores.get(key, 0) + 1.0 / (self.rrf_k + rank + 1)
            chunk_map[key] = chunk

        sorted_keys = sorted(scores, key=lambda k: scores[k], reverse=True)
        return [
            RetrievedChunk(
                text=chunk_map[k].text,
                source=chunk_map[k].source,
                score=scores[k],
                metadata=chunk_map[k].metadata,
            )
            for k in sorted_keys[:self.top_k_retrieval]
        ]

    def query(self, user_query: str, user_id: str = "anonymous") -> RAGResult:
        correlation_id = create_correlation_id()
        start_time = time.time()

        log = logger.bind(
            correlation_id=correlation_id,
            user_id=user_id,
            query_hash=hashlib.sha256(user_query.encode()).hexdigest()[:8],
        )
        log.info("rag.query.start")

        # L2: Semantic cache check
        if self.semantic_cache:
            cached = self.semantic_cache.get(user_query)
            if cached:
                log.info("rag.cache.hit")
                return RAGResult(
                    answer=cached["answer"],
                    chunks=[],
                    provider="cache",
                    fallback_used=False,
                    latency_ms=(time.time() - start_time) * 1000,
                    correlation_id=correlation_id,
                    cache_hit=True,
                )

        # Step 1: Embed query
        query_embedding = self.embedding_fn(user_query)

        # Step 2: Parallel hybrid retrieval
        dense_results = self.dense_retriever(query_embedding)
        sparse_results = self.sparse_retriever(user_query)

        # Step 3: RRF fusion
        fused = self._rrf_fusion(dense_results, sparse_results)
        log.info("rag.retrieval.complete", fused_count=len(fused))

        # Step 4: RBAC filtering (pre-retrieval is better; this is a safety net)
        if self.rbac_filter_fn:
            fused = self.rbac_filter_fn(fused, user_id)
            log.info("rag.rbac.filtered", remaining=len(fused))

        # Step 5: Reranking
        reranked = self.reranker_fn(user_query, fused)[:self.top_k_rerank]
        log.info("rag.rerank.complete", reranked_count=len(reranked))

        # Step 6: Generate with fallback chain
        context = "\n\n---\n\n".join(
            f"[Source: {c.source}]\n{c.text}" for c in reranked
        )
        llm_result = self.llm_chain.generate(
            prompt=user_query, context=context, correlation_id=correlation_id
        )

        # Step 7: Cache result
        if self.semantic_cache:
            self.semantic_cache.set(user_query, {"answer": llm_result["answer"]})

        elapsed_ms = (time.time() - start_time) * 1000
        log.info(
            "rag.query.complete",
            provider=llm_result["provider"],
            fallback_used=llm_result["fallback_used"],
            latency_ms=round(elapsed_ms, 1),
            chunks_used=len(reranked),
        )

        return RAGResult(
            answer=llm_result["answer"],
            chunks=reranked,
            provider=llm_result["provider"],
            fallback_used=llm_result["fallback_used"],
            latency_ms=elapsed_ms,
            correlation_id=correlation_id,
            cache_hit=False,
        )
```

### Implementation B: Stdlib-Only RAG Runtime with Protocol-Based Ports

```python
#!/usr/bin/env python3
"""RAG query-plane resilience: retries, circuit breaker, fallbacks, logging.

Stdlib only. Wire real HTTP clients behind Retriever/Generator protocols.
Key features: TransientError/PermanentError distinction, full-jitter retry
(AWS-style), per-dependency circuit breakers, ACL on Authz dataclass (NEVER
from model JSON), hybrid->BM25->cache->refuse retrieval fallback,
primary->secondary->extractive deterministic generation fallback,
lost-in-the-middle mitigation (edge placement), hop cap (max_hops=3).
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

# --- Structured JSON Logging (correlation id + tenant on every line) ----------

class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = getattr(record, "correlation_id", "-")
        record.tenant_id = getattr(record, "tenant_id", "-")
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("rag")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"cid":"%(correlation_id)s","tenant":"%(tenant_id)s",'
            '"msg":"%(message)s"}'
        )
    )
    handler.addFilter(CorrelationFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


LOG = configure_logging()


def slog(level: int, msg: str, *, cid: str, tenant: str, **fields: object) -> None:
    extra = {"correlation_id": cid, "tenant_id": tenant}
    LOG.log(level, "%s %s", msg, json.dumps(fields, default=str), extra=extra)


# --- Retries: exponential backoff + full jitter (AWS-style) -------------------

class TransientError(Exception):
    """429, 5xx, timeout -- safe to retry idempotent reads."""


class PermanentError(Exception):
    """4xx auth, schema mismatch -- do not retry, do not rewrite-loop."""


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
            sleep = random.uniform(0, sleep)  # full jitter
            slog(
                logging.WARNING, "retry",
                cid=cid, tenant=tenant, op=op, attempt=i + 1, sleep_s=round(sleep, 3),
                err=str(exc),
            )
            time.sleep(sleep)
    assert last is not None
    raise last


# --- Circuit breaker: closed -> open -> half-open ----------------------------

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


# --- Retrieval + generation ports (swap in HTTP; identity is NEVER a tool arg)

@dataclass(frozen=True)
class Authz:
    tenant_id: str
    user_id: str
    acl_filter: dict  # pushed into every retriever arm


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    retriever: str
    score: float


class Retriever(Protocol):
    name: str
    def search(self, query: str, authz: Authz, k: int) -> list[Chunk]: ...


class Generator(Protocol):
    name: str
    def complete(self, prompt: str, allowed_ids: frozenset[str]) -> str: ...


# --- Fallbacks: hybrid -> BM25 -> cache -> refuse ----------------------------
#     Generate: primary -> secondary -> extractive deterministic

@dataclass
class LastGoodCache:
    """Process-local stand-in; production: Redis keyed by
    (index_version, filter, qh, k)."""
    ttl_s: float = 300.0
    _store: dict[str, tuple[float, list[Chunk]]] = field(default_factory=dict)

    def _key(self, query: str, authz: Authz, k: int) -> str:
        raw = f"{authz.tenant_id}|{authz.user_id}|{k}|{query}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, query: str, authz: Authz, k: int) -> list[Chunk] | None:
        rec = self._store.get(self._key(query, authz, k))
        if rec is None:
            return None
        ts, chunks = rec
        if time.monotonic() - ts > self.ttl_s:
            return None
        return chunks

    def put(self, query: str, authz: Authz, k: int, chunks: list[Chunk]) -> None:
        self._store[self._key(query, authz, k)] = (time.monotonic(), chunks)


@dataclass
class DegradedResult:
    chunks: list[Chunk]
    answer: str
    retrieval_degraded: bool
    generation_degraded: bool
    citations: list[str]


class RagRuntime:
    def __init__(
        self,
        hybrid: Retriever,
        bm25: Retriever,
        primary_gen: Generator,
        secondary_gen: Generator,
        cache: LastGoodCache | None = None,
        retrieve_timeout_s: float = 0.4,  # 200-500 ms policy band
        max_hops: int = 3,
    ) -> None:
        self.hybrid = hybrid
        self.bm25 = bm25
        self.primary_gen = primary_gen
        self.secondary_gen = secondary_gen
        self.cache = cache or LastGoodCache()
        self.retrieve_timeout_s = retrieve_timeout_s
        self.max_hops = max_hops
        self.breakers = {
            "hybrid": CircuitBreaker("hybrid"),
            "bm25": CircuitBreaker("bm25"),
            "primary_gen": CircuitBreaker("primary_gen"),
            "secondary_gen": CircuitBreaker("secondary_gen"),
        }

    def _call_retriever(
        self, r: Retriever, query: str, authz: Authz, k: int, cid: str
    ) -> list[Chunk]:
        br = self.breakers[r.name]

        def _op() -> list[Chunk]:
            br.allow()
            t0 = time.monotonic()
            try:
                hits = r.search(query, authz, k)
            except PermanentError:
                br.record_failure()
                raise
            except Exception as exc:
                br.record_failure()
                raise TransientError(str(exc)) from exc
            if time.monotonic() - t0 > self.retrieve_timeout_s:
                br.record_failure()
                raise TransientError(f"retrieve_timeout:{r.name}")
            br.record_success()
            return hits

        return retry_with_jitter(
            _op, cid=cid, tenant=authz.tenant_id, op=f"retrieve:{r.name}"
        )  # type: ignore[return-value]

    def retrieve(
        self, query: str, authz: Authz, k: int, cid: str
    ) -> tuple[list[Chunk], bool]:
        """ACL filter is on Authz -- never parsed from model-emitted JSON."""
        degraded = False
        try:
            hits = self._call_retriever(self.hybrid, query, authz, k, cid)
            if hits:
                self.cache.put(query, authz, k, hits)
                return hits, False
            degraded = True
        except (TransientError, PermanentError) as exc:
            slog(logging.ERROR, "hybrid_failed",
                 cid=cid, tenant=authz.tenant_id, err=str(exc))
            degraded = True
        try:
            hits = self._call_retriever(self.bm25, query, authz, k, cid)
            if hits:
                slog(logging.WARNING, "fallback_bm25",
                     cid=cid, tenant=authz.tenant_id)
                return hits, True
        except (TransientError, PermanentError) as exc:
            slog(logging.ERROR, "bm25_failed",
                 cid=cid, tenant=authz.tenant_id, err=str(exc))
        cached = self.cache.get(query, authz, k)
        if cached:
            slog(logging.WARNING, "fallback_cache",
                 cid=cid, tenant=authz.tenant_id)
            return cached, True
        slog(logging.ERROR, "retrieve_exhausted",
             cid=cid, tenant=authz.tenant_id)
        return [], True  # caller must refuse -- never ungrounded generate

    def _generate(
        self, gen: Generator, prompt: str, allowed: frozenset[str],
        cid: str, tenant: str
    ) -> str:
        br = self.breakers[gen.name]

        def _op() -> str:
            br.allow()
            try:
                text = gen.complete(prompt, allowed)
            except PermanentError:
                br.record_failure()
                raise
            except Exception as exc:
                br.record_failure()
                raise TransientError(str(exc)) from exc
            br.record_success()
            return text

        return retry_with_jitter(
            _op, cid=cid, tenant=tenant, op=f"generate:{gen.name}"
        )  # type: ignore[return-value]

    def generate_grounded(
        self, chunks: list[Chunk], question: str, cid: str, tenant: str
    ) -> tuple[str, bool]:
        if not chunks:
            return (
                "I cannot answer: the retrieval index is unavailable and "
                "ungrounded generation is disabled for this corpus.",
                True,
            )
        allowed = frozenset(c.chunk_id for c in chunks)
        # Lost-in-the-middle mitigation: put highest-score chunks at edges.
        ranked = sorted(chunks, key=lambda c: c.score, reverse=True)
        edge = [ranked[0]] + ranked[2:] + ([ranked[1]] if len(ranked) > 1 else [])
        ctx = "\n".join(f"[{c.chunk_id}] {c.text}" for c in edge)
        prompt = (
            f"Answer ONLY from the passages. "
            f"Cite ids in {sorted(allowed)}.\n"
            f"Passages:\n{ctx}\nQuestion: {question}"
        )
        try:
            return self._generate(
                self.primary_gen, prompt, allowed, cid, tenant
            ), False
        except (TransientError, PermanentError) as exc:
            slog(logging.ERROR, "primary_gen_failed",
                 cid=cid, tenant=tenant, err=str(exc))
        try:
            slog(logging.WARNING, "fallback_secondary_gen",
                 cid=cid, tenant=tenant)
            return self._generate(
                self.secondary_gen, prompt, allowed, cid, tenant
            ), True
        except (TransientError, PermanentError) as exc:
            slog(logging.ERROR, "secondary_gen_failed",
                 cid=cid, tenant=tenant, err=str(exc))
        # Deterministic extractive fallback -- no parametric invention.
        titles = ", ".join(c.chunk_id for c in ranked[:3])
        return (
            f"Generation models unavailable. Top retrieved passages: {titles}. "
            "Insufficient evidence to synthesize an answer.",
            True,
        )

    def answer(self, question: str, authz: Authz, k: int = 8) -> DegradedResult:
        cid = str(uuid.uuid4())
        slog(logging.INFO, "query_start",
             cid=cid, tenant=authz.tenant_id, q=question[:200])
        hops = 0
        query = question
        chunks: list[Chunk] = []
        retr_deg = False
        while hops < self.max_hops:
            hops += 1
            chunks, d = self.retrieve(query, authz, k, cid)
            retr_deg = retr_deg or d
            if chunks:
                break
            # Production cap: do not rewrite when retrieve is down.
            if d:
                break
            query = f"{question} (rewrite hop {hops})"
        answer, gen_deg = self.generate_grounded(
            chunks, question, cid, authz.tenant_id
        )
        cites = [c.chunk_id for c in chunks]
        slog(
            logging.INFO, "query_end",
            cid=cid, tenant=authz.tenant_id,
            hops=hops, n_chunks=len(chunks),
            retrieval_degraded=retr_deg,
            generation_degraded=gen_deg,
        )
        return DegradedResult(chunks, answer, retr_deg, gen_deg, cites)


# --- Demo backends (replace with Pinecone/ES/OpenAI clients) -----------------

class StaticRetriever:
    def __init__(self, name: str, fail: bool = False) -> None:
        self.name = name
        self.fail = fail

    def search(self, query: str, authz: Authz, k: int) -> list[Chunk]:
        if self.fail:
            raise TransientError("simulated_outage")
        _ = query
        return [
            Chunk(f"{authz.tenant_id}:policy-1",
                  "Parental leave is 16 weeks.", self.name, 0.9),
            Chunk(f"{authz.tenant_id}:policy-2",
                  "Error TS-999 means payment timeout.", self.name, 0.7),
        ][:k]


class StaticGenerator:
    def __init__(self, name: str, fail: bool = False) -> None:
        self.name = name
        self.fail = fail

    def complete(self, prompt: str, allowed_ids: frozenset[str]) -> str:
        if self.fail:
            raise TransientError("fm_outage")
        _ = prompt
        cid = next(iter(allowed_ids))
        return f"Grounded answer citing [{cid}]."


if __name__ == "__main__":
    authz = Authz(
        tenant_id="acme", user_id="u1",
        acl_filter={"tenant_id": {"$eq": "acme"}}
    )
    runtime = RagRuntime(
        hybrid=StaticRetriever("hybrid", fail=True),
        bm25=StaticRetriever("bm25"),
        primary_gen=StaticGenerator("primary_gen", fail=True),
        secondary_gen=StaticGenerator("secondary_gen"),
    )
    result = runtime.answer("What is TS-999?", authz)
    print(json.dumps({
        "answer": result.answer,
        "citations": result.citations,
        "retrieval_degraded": result.retrieval_degraded,
        "generation_degraded": result.generation_degraded,
    }, indent=2))
```

**What is wired in both implementations:** hop cap (max_hops=3; official LangGraph has none); ACL on `Authz` not model JSON; hybrid -> BM25 -> TTL cache -> refuse; primary FM -> secondary -> extractive; per-dependency breakers; full-jitter retries; JSON logs with `cid` + `tenant`. Real clients must **push** `authz.acl_filter` into every hybrid arm.

---

## 7. System Design Scenarios

### Scenario 1: Multi-Tenant Enterprise Knowledge Assistant

**Problem Statement:** A B2B SaaS company needs a RAG-powered assistant that serves 200 enterprise customers, each with 50K-500K proprietary documents. Documents include HR policies, engineering runbooks, and customer contracts. Strict data isolation is non-negotiable (SOC 2 Type II, some customers require HIPAA). Target: <2s p95 latency, 99.9% availability, <$15K/month total infrastructure.

**Proposed Architecture:**

```
+----------------------------------------------------------+
|                   API GATEWAY (Auth + Rate Limiting)       |
|                          |                                |
|                          v                                |
|  +----------------------------------------------------+  |
|  |  TENANT ROUTER  (tenant_id -> namespace mapping)    |  |
|  +----------+-----------------------------------------+  |
|             |                                             |
|             v                                             |
|  +------------------+  +------------------------------+  |
|  | pgvector          |  | Qdrant (large tenants only)  |  |
|  | (per-tenant       |  | (>100K docs, latency-        |  |
|  |  schema)          |  |  critical SLAs)               |  |
|  +------------------+  +------------------------------+  |
|             |                                             |
|             v                                             |
|  +--------------------------------------------------------+
|  |  LLM FALLBACK CHAIN:                                   |
|  |  Claude Sonnet 4.6 -> GPT-4.1 -> Self-hosted Llama     |
|  +--------------------------------------------------------+
+-----------------------------------------------------------+
```

**Trade-Off Matrix:**

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **A: Shared pgvector, namespace isolation** | Cheapest ($0 extra DB cost), simple ops | Noisy neighbor risk, RBAC complexity, SOC 2 auditors prefer physical isolation | Use for small tenants (<50K docs) |
| **B: Dedicated Qdrant per tenant** | Strong isolation, best latency | $600-$1,200/mo per tenant, 200 tenants = $120K-$240K/mo | Only for largest tenants on premium tier |
| **C: pgvector per-schema + Qdrant for top 10** | Cost-effective isolation, latency SLA for top accounts | Two systems to operate | **Selected** |

**Decision Rationale:** pgvector with per-tenant schemas provides logical isolation auditable for SOC 2 at near-zero marginal cost. The top 10 tenants (by document count and latency SLA) get dedicated Qdrant namespaces. At 50M total vectors across all tenants, pgvectorscale (471 QPS) outperforms Qdrant at this scale. The LLM fallback chain ensures 99.9% availability even during provider outages. Total cost: ~$8K/month (pgvector on existing RDS + 2 Qdrant nodes + LLM inference).

---

### Scenario 2: Real-Time Compliance RAG for Financial Services

**Problem Statement:** A financial services firm needs a RAG system that answers compliance questions from 500 analysts. The regulatory corpus (SEC filings, FINRA rules, internal policies) changes weekly. Answers must cite exact source paragraphs. Temporal reasoning is critical ("What was the margin requirement for crypto ETFs as of March 2026?"). Zero tolerance for stale answers. SOC 2 and internal audit trail mandatory.

**Proposed Architecture:**

```
+-----------------------------------------------------------------+
|                     INGESTION (WEEKLY + EVENT-DRIVEN)             |
|  +----------+  +-------------+  +----------+  +------------+    |
|  | SEC EDGAR |  | FINRA Rules |  | Internal |  | Freshness  |    |
|  | Crawler   |  | Feed        |  | Policies |  | Tracker    |    |
|  +-----+----+  +------+------+  +----+-----+  +-----+------+    |
|        +---------------+-------------+---------------+           |
|                          |                                       |
|                          v                                       |
|        +-------------------------------+                         |
|        | TEMPORAL METADATA ENRICHMENT   |                         |
|        | (effective_date, expiry_date,  |                         |
|        |  supersedes_doc_id)            |                         |
|        +--------------+----------------+                         |
|                       v                                          |
|  +---------------------------------------------------------+    |
|  | HYBRID INDEX (Qdrant + Elasticsearch BM25)               |    |
|  | + Knowledge Graph (Neo4j: regulation -> supersedes ->    |    |
|  |   cites -> amends relationships)                         |    |
|  +---------------------------------------------------------+    |
|                       |                                          |
|                       v                                          |
|  +---------------------------------------------------------+    |
|  | AGENTIC RAG (max 3 retrieval rounds)                     |    |
|  | Round 1: Direct retrieval                                |    |
|  | Round 2: Graph traversal for superseding docs            |    |
|  | Round 3: Temporal reconciliation (conflicting sources)   |    |
|  +---------------------------------------------------------+    |
|                       |                                          |
|                       v                                          |
|  +---------------------------------------------------------+    |
|  | CITED GENERATION with temporal qualifiers                |    |
|  | ("As of [date], per [source], the requirement is...")    |    |
|  +---------------------------------------------------------+    |
+-----------------------------------------------------------------+
```

**Trade-Off Matrix:**

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **A: Flat hybrid RAG (no temporal awareness)** | Simpler, cheaper, 1.5s latency | ERAA-2026 shows 29% hallucination on temporal queries vs 9% atemporal. Conflicting source accuracy drops from 95% to 61%. Unacceptable for compliance. | Rejected |
| **B: Agentic RAG + knowledge graph** | Handles temporal queries, resolves superseded docs, 62% hallucination reduction | 5-15s latency, 20-40x cost of simple RAG, complex ops | **Selected** |
| **C: Full Graph RAG (Microsoft GraphRAG)** | Best at cross-document synthesis | Very expensive indexing, overkill for structured regulatory corpus with clear relationships | Rejected |

**Decision Rationale:** Compliance cannot tolerate the 29% temporal hallucination rate of flat RAG. The knowledge graph captures explicit regulatory relationships (supersedes, amends, cites) that semantic similarity alone cannot model. The LinkedIn case study showed 28.6% faster issue resolution with knowledge graph augmentation. Agentic RAG with max 3 rounds provides the retrieval depth needed for multi-hop compliance questions. The 5-15s latency is acceptable for analyst-facing compliance queries where accuracy matters more than speed. Audit trail with hash-chained immutable logs satisfies SOC 2. Total cost: ~$25K/month.

---

### Scenario 3: Multi-Tenant SaaS Knowledge Base (Pinecone/Weaviate Focus)

**Problem:** B2B help-center copilot. Tenants upload manuals + SKU tables + error-code matrices. Requirements: tenant isolation (SOC 2), exact-ID queries (`TS-999`) **and** paraphrase, p95 chat in a few seconds, no GraphRAG (queries are factoid/FAQ, not corpus themes). Peak: size rerank RPM for hops x user QPS, not user QPS alone.

**Proposed Architecture:**

```
  +------------------+     +---------------------------------------------+
  | Tenant IdP       |     | CONTROL: Adaptive-RAG  chitchat->no retrieve|
  | JWT -> PEP       |---->|            factoid  -> hybrid+rerank        |
  +------------------+     |            hop cap = 1                      |
                           +--------------+------------------------------+
                                          v
                           +----------------------------------------------+
                           | Pinecone namespace-per-tenant  OR             |
                           | Weaviate tenant shard  OR pgvector RLS       |
                           | Dense HNSW + sparse/BM25 (hybrid_score_norm  |
                           | or client RRF). ACL is the namespace/tenant  |
                           | key -- not a $in of all user IDs (10k cap).  |
                           +--------------+-------------------------------+
                                          v
                           +----------------------------------------------+
                           | Rerank 80->8 (Voyage 2.5 / Cohere / bge)     |
                           | Prompt cache on system+tool schema            |
                           | Citations in subset of retrieved chunk_ids    |
                           +----------------------------------------------+
```

**Technology choices:** OpenAI `text-embedding-3-small` or Voyage-4-lite ($0.02/1M); hybrid with explicit alpha or RRF; Voyage/Cohere/bge rerank; generate `gpt-5.6-luna` or Haiku 4.5 + prompt cache; **no** Leiden/global.

**Trade-Off Matrix:**

| Axis | **A1 Namespace/tenant shard + hybrid + rerank (recommended)** | **A2 Shared 100 GB index + metadata `$eq tenant`** | **A3 Bedrock Managed KB hybrid** |
|---|---|---|---|
| **Cost** | RU ~ 1x namespace GB; Standard $50 min | Same query 100x RU vs 1 GB tenant | $5/GB + $1/1k; $350/mo official 50 GB/100k |
| **Latency** | Predictable 2-stage; hop cap 1 | Filter+IVF risk: recall collapse at 50-90% filtered | Managed; hybrid falls back to semantic if no filterable text field |
| **Ops** | 100k namespaces/index; 20 indexes/project Standard | One index, app must never omit filter | Lowest ops; deleting KB does not delete AOSS collection |
| **Security** | Query cannot cross namespace; omit-key is isolation | App-bug omits filter -> cross-tenant leak | Metadata filters; Guardrails not on retrieved source text |
| **Scale ceiling** | 100k namespaces; DRN for noisy-neighbor reads | Recall->0 for rare tenants under naive IVF+filter | 3 imports/region |

**Decision:** A1 wins: isolation is structural, RU math favors small hot namespaces, hybrid+rerank matches SKU+semantics, GraphRAG would burn extract cost (~75% of index) on FAQ traffic.

---

### Scenario 4: Pharma / Legal Multi-Hop with Citation Spans

**Problem:** Clinical-ops / law-firm copilot: "compare trial X vs Y across protocols"; answers must carry `chunk_id+char_span`; 21 CFR 11-style audit (who saw which passage); corpus in M365/SharePoint; **no** confidential query on the open web.

**Proposed Architecture:**

```
  +-------------+   +---------------------------------------------+
  | Entra token |-->| Azure AI Search  document-level ACL          |
  | x-ms-query- |   | (userIds OR groupIds OR rbacScope)           |
  | source-auth |   | indexer: ingestionPermissionOptions           |
  +-------------+   +-----------------+---------------------------+
                                      v
                    +----------------------------------------------+
                    | Hybrid BM25+HNSW -> RRF -> Semantic Ranker    |
                    | top 50                                        |
                    | + HippoRAG-style PPR over ontology-constrained|
                    | NER (not unconstrained LLM entities)          |
                    | Agent 2-hop IRCoT-shaped loop, retry_count=2  |
                    | CRAG Incorrect -> licensed corpus only, no web|
                    +-----------------+----------------------------+
                                      v
                    +----------------------------------------------+
                    | Citation gate: IDs in subset of retrieved set |
                    | NLI / IsSup; refuse if empty or IsSup=no      |
                    | WORM audit: chunk_id, span, sha256, actor      |
                    | Human review on new graph edges                |
                    | ACL copied onto graph reports, not just chunks  |
                    +----------------------------------------------+
```

**Technology choices:** Azure document-level ACL (REST 2026-04-01+ / 2026-05-01-preview for full agentic+ACL); semantic ranker billed when `queryType=semantic` and search string non-empty. Graph edges from controlled NER (ontology). ALCE-style citation precision/recall in CI. Avoid: full Leiden global on every turn; entity explosion; LLM-as-only-reranker on 200 chunks.

**Trade-Off Matrix:**

| Axis | **B1 Hybrid + capped 2-hop + HippoRAG PPR + Azure ACL (recommended)** | **B2 Full GraphRAG global** | **B3 Unbounded LangGraph + CRAG open web** |
|---|---|---|---|
| **Cost** | Local ~ hybrid; PPR 10-20x cheaper than IRCoT; HippoRAG 2 index 9M vs 115M tokens vs GraphRAG on MuSiQue | Extract ~75% of index $; global map-reduce >> $3.5/1k | Each hop multiplies embed+retrieve+rerank+LLM; web API extra |
| **Latency** | p99 bounded by hop cap 2 + DRIFT follow-ups only when routed | Worst; DRIFT default 2 follow-ups still multi-pass | Fat tail; official graph loops until timeout |
| **Security** | Query-time Entra ACL; no web exfil; ACL on reports | Community reports can summarize restricted docs into globally readable nodes | CRAG web = exfil path for confidential queries |
| **Scale** | Vector path still required: structure methods can drop 5-10 F1 on simple QA | Helps global themes; vector often wins single-hop | Size rerank for hops x QPS (Cohere 1000 RPM) |

**Decision:** B1 wins: multi-hop without paying GraphRAG global map-reduce; citations are first-class; Azure ACL matches SharePoint provenance; web is forbidden by threat model.

---

## 8. Interview Q&A

**Q1: Walk me through production RAG as if I have never seen a vector DB.**
I split ingest from query. Ingest parses, redacts PII, stamps ACL, chunks, embeds, and only then flips an alias. At query time I never let the LLM "search": I push the caller's ACL as a filter, run BM25 and dense in parallel, fuse (usually RRF), rerank 50-150 down to 5-20, and generate with citations drawn only from those IDs. The key architectural choice is that the ingest plane and query plane scale independently -- a stuck connector must never block user-facing answers, and an embedding model change must never silently corrupt the live index.

**Q2: Why does RAG still matter if models support 1M tokens?**
Because long context does not solve freshness (source data changes hourly), per-tenant ACLs (each user should only see their slice), or the economics of sending huge corpora on every turn. RAG solves those directly. Below ~200k tokens (~500 pages), caching the corpus can be simpler than building full RAG (Anthropic's own guidance).

**Q3: Why hybrid? Why not just embeddings?**
Dense misses exact IDs (`TS-999`); BM25 misses paraphrase. Anthropic's production sketch is both lists then fusion. RRF with k=60 is scale-free -- rank 1 contributes ~0.0164 -- so I do not have to pretend BM25 and cosine share a numeric space. Dense-only systems routinely miss the exact token strings businesses care about: SKUs, IDs, statute numbers, error codes, product names.

**Q4: What is the default production retrieval stack?**
Hybrid first-stage retrieval (dense + BM25/SPLADE in parallel), RRF or normalized fusion, cross-encoder reranking from 50-150 candidates down to 5-20, then citation-aware generation.

**Q5: Pinecone hybrid looked keyword-only in staging. What did we miss?**
Sparse scores are unbounded; dense cosine is about [-1,1]. Without `hybrid_score_norm` on the query vectors, sparse dominates. I would also check Weaviate's default `alpha=0.75` if someone "forgot" to set alpha and thought they were 50/50.

**Q6: How do you stop tenant leaks?**
I do not put `tenant_id` in the tool JSON the model fills. Identity comes from the verified token. Prefer namespace- or shard-per-tenant so omitting a filter is an error, not a full scan. On a shared index I still pre-filter; post-filter ANN fills top-k with forbidden neighbors and authorized recall goes to zero. Pinecone `$in` caps at 10,000 IDs -- I use groups, not a user-id dump. A model cannot be trusted to "unsee" unauthorized context -- access control must sit inside retrieval, not only around the final answer.

**Q7: How do you choose chunking?**
Start with 400-800 token chunks plus overlap, then adjust based on evals. Move to contextual or late chunking only if recall failures justify the extra complexity. The 2026 consensus is recursive splitting as production default. Semantic chunking only when eval proves it justifies the 10x processing cost. There is a context cliff at ~2,500 tokens where response quality degrades.

**Q8: Give me a cost model for 1,000 questions.**
I state the mix: 50-token query embed, 80-chunk rerank, 4k generate in / 400 out, no retries. On that mix, 3-small embed is $0.001/1k, Voyage rerank-2.5 is ~$2.20/1k, luna generate ~$1.28/1k, total ~$3.5/1k [inferred], excluding RUs. If I move generate to terra or Sonnet 5, generate alone is ~$12-13/1k and dominates. Pinecone RUs are 1 per GB of the namespace I actually query -- a 100 GB shared namespace is a 100x tax.

**Q9: What SLO do you put in the contract?**
I do not quote a vendor RAG p99 -- nobody publishes one. I SLO retrieve+rerank separately from generate. I treat Pinecone's O(100 ms) as a design target, set a 200-500 ms retrieve timeout as policy, circuit-break the index independently of the FM, and cap agent hops because each hop is +1-3 LLM calls on the p95 tail.

**Q10: Naive vs advanced vs agentic -- when do you pay for the loop?**
Naive is always-retrieve. Advanced is a DAG: hybrid+rerank+maybe HyDE. Agentic is retrieval-as-tool with a grader. I route with Adaptive-RAG: greetings skip retrieve; factoids stay 2-stage; multi-hop gets 2-3 hops; global questions get LazyGraphRAG, not Leiden-on-every-turn. Unbounded CRAG+web is an exfil path. Agentic RAG often costs 2-10x versus standard hybrid retrieval if you are not careful with loop caps.

**Q11: When should I use GraphRAG?**
Only if eval shows global/multi-hop failure. Full GraphRAG extract is ~75% of index cost; the GitHub repo is maintenance-mode research. LazyGraphRAG indexes at vector-RAG cost (0.1% of full GraphRAG, Microsoft-stated) and budgets query-time relevance tests. I still keep a vector path -- HippoRAG 2 reports structure methods can drop 5-10 F1 on simple QA. Do not pay graph indexing cost for FAQ-style fact lookups that hybrid + rerank already solves.

**Q12: Citations keep being invented. Fix?**
ALCE showed even strong models lack complete citation support about half the time on ELI5. RAGAS faithfulness catches unsupported claims, not fake IDs. I constrain decode to retrieved `chunk_id`s, NLI/`IsSup` gate, hash-check body vs ingest sha256, and refuse on empty retrieve. I also stop stuffing 20-50 unreranked chunks -- that is lost-in-the-middle plus hallucination fuel.

**Q13: What do you evaluate in RAG?**
At least four things separately: (1) retrieval quality (nDCG@k, recall@k on golden sets), (2) generation faithfulness (RAGAS scores 0.95 alignment with humans vs 0.72 for direct GPT scoring), (3) citation correctness (provenance fidelity -- cited IDs were retrieved, support the claim via NLI, and the user was entitled to see them), and (4) latency/cost.

**Q14: How does LangGraph not lose a rewrite on crash?**
Production checkpointer is `PostgresSaver`. It snapshots each super-step; finished node writes in a failed super-step are durable and not recomputed. `InMemorySaver` is for tests. I also store `retry_count` in state because the official agentic-RAG tutorial has no hop cap and will loop until the runtime times out.

**Q15: Filtered search quality collapsed for a small tenant. Why?**
Classic IVF+filter: Pinecone's paper shows recall collapse at 50% filtered and unusable results at 90%. Their fix is IVF bypass when the match set is small, plus adaptive scan fraction. Operationally I isolate that tenant into its own namespace so I am not probing a 100 GB slab for 0.1% selectivity.

**Q16: Zero-Trust MCP for retrieval -- what is the failure mode?**
An omnibus `search(query, collection, tenant_id)` where `tenant_id` is model-filled. MCP `tools/call` becomes a data-exfil API. I split tools by sensitivity, push predicates server-side, keep memory in the checkpointer not the MCP session, redact traces to the user's ACL, and if Azure exposes an MCP endpoint on the knowledge base I pass the same Entra token or that endpoint is a bypass.

**Q17: What is the biggest anti-pattern in interview answers?**
Describing RAG as only a vector search problem. Production RAG is retrieval engineering plus grounding discipline. The failure point is retrieval 73% of the time, not generation.

**Q18: GraphRAG in the architecture review -- do we need it?**
Only if eval shows global/multi-hop failure. Full GraphRAG extract is ~75% of index cost; `microsoft/graphrag` is maintenance-mode research. LazyGraphRAG indexes at vector-RAG cost (0.1% of full GraphRAG) and budgets query-time relevance tests. I still keep a vector path -- HippoRAG 2 reports structure methods can drop 5-10 F1 on simple QA. GraphRAG-Bench: not all graph methods beat a strong GPT-4o-mini baseline -- over-structure can hurt.

---

## 9. Key Numbers to Memorize

### Quality / Algorithms

| Number | What |
|---|---|
| **5.7% -> 1.9%** | Anthropic 1-recall@20: baseline -> contextual+BM25+rerank 150->20 (-67%) |
| **3.7% / 2.9%** | Contextual embeddings alone (-35%); +BM25 (-49%) |
| **k = 60** | RRF default; rank 1 -> 1/61 ~ 0.0164; rank 60 -> 1/120 = 0.0083 |
| **150->20 / top 50** | Anthropic rerank window; Azure Semantic Ranker reorders hybrid top 50 |
| **NDCG 0.7497** | Tuned hybrid RRF on WANDS -- 7.5% above either retriever alone |
| **+17.4% / +39.7%** | Cross-encoder rerank: Recall@5 (0.695->0.816) / MRR@3 (0.433->0.605) |
| **0.7084 -> 0.8249** | Late chunking cosine, Berlin Wikipedia example |
| **56.1%** | Lost-in-the-middle: GPT-3.5-Turbo with answer in middle below closed-book |
| **~50%** | ALCE: incomplete citation support on ELI5 |
| **0.95 vs 0.72** | RAGAS faithfulness vs direct GPT scoring vs humans (WikiEval) |
| **+21 / +15 pts** | IRCoT retrieval / QA gains (paper, GPT-3) |
| **10-20x / 6-13x** | HippoRAG PPR cheaper / faster than IRCoT (their experiments) |
| **~20%** | HippoRAG vs SOTA RAG on multi-hop QA (paper) |
| **5-10 F1** | HippoRAG 2: structure methods can drop this on simple QA vs strong embeddings |
| **~62%** | Hallucination reduction from agentic RAG with KG (47 production deployments, May 2026) |
| **14.1% -> 4.9%** | Carnegie Mellon agentic RAG hallucination reduction (June 2026, financial compliance) |
| **+7.94% / +12.70%** | Voyage rerank-2.5 vs Cohere v3.5 NDCG@10 / MAIR (vendor eval) |
| **73%** | When RAG fails, the failure point is retrieval, not generation |
| **~200k tokens / ~500 pages** | Anthropic: skip RAG, cache the whole corpus |

### Embeddings & Ingest Costs

| Number | What |
|---|---|
| **$0.02 / $0.13 / $0.10** | OpenAI 3-small / 3-large / ada-002 per 1M |
| **$0.02 / $0.06 / $0.12** | Voyage-4-lite / voyage-4 / 4-large or context-4 or code-4 per 1M |
| **$0.12 / $0.47** | Cohere embed-v4 text / image per 1M (listings -- confirm dashboard) |
| **$0.16 / $0.08 / $0.08** | Pinecone llama-text-embed-v2 / e5-large / sparse-english per 1M |
| **$20 / $60 / $120 / $130** | 1B-token corpus embed: 3-small or 4-lite / voyage-4 / context-4 or Cohere / 3-large |
| **50% / 33%** | OpenAI Batch off; Voyage Batch off (no free-token credit on Voyage Batch) |
| **$1.02 / 1M** | Anthropic contextualize with prompt cache (their 800/8k/50/100 tok mix) |
| **~$102** | 100M-token corpus contextualize LLM before embeddings |
| **~$15 -> ~$3** | Anthropic 737-chunk demo with 70-80% cache hits |
| **$13K** | 100M-token knowledge base with text-embedding-3-large |
| **55% cost reduction** | Matryoshka 3072->768-dim maintaining 92% retrieval accuracy |
| **8192 / 2048 / 300k** | OpenAI embed per-input tokens / inputs / summed tokens per request |
| **32x** | Elastic BBQ compression vs full-precision (vendor-stated) |
| **~6.1 KB / ~61 GB / ~$20/mo** | [inferred] float32 1536-d per vector / 10M chunks raw / Pinecone $0.33/GB |

### Rerank & Generate Costs

| Number | What |
|---|---|
| **$0.05 / $0.02 per 1M tok** | Voyage rerank-2.5 / lite |
| **$2.00 / $2.50 / 1k SU** | Cohere 3.5 Bedrock & 4 Fast; 4 Pro Azure Foundry preview |
| **$2 / 1k** | Pinecone Inference rerank |
| **$1.00 / 1k** | Google Ranking API; 80k free units / 30d; 100 docs = 1 unit |
| **$0** | Bedrock Managed KB managed reranker |
| **10 / 1,000 RPM** | Cohere Rerank trial / production |
| **100 docs; 500-tok split** | Cohere search unit |
| **$0.20/$1.20; cache in $0.02; write $0.25** | gpt-5.6-luna |
| **$2/$12; $4/$20** | gpt-5.6-terra / sol |
| **$0.25/$2** | gpt-5-mini |
| **1.25x / 2x / 0.1x** | Anthropic 5m write / 1h write / cache read (Mythos hits 0.025x) |
| **$2 / $2.50 / $4 / $0.20 / $10** | Sonnet 5 base / 5m write / 1h write / hit / output per MTok |
| **>1,024 tok** | OpenAI auto prompt cache threshold |
| **10%** | OpenAI regional processing uplift (eligible models from 2026-03-05) |
| **~ $3.5 / 1k** | [inferred] luna+Voyage rerank reference mix, no RUs/retries |
| **~ $12.8 / 1k** | [inferred] terra generate alone on the same 4k/400 mix |

### Vector DB, Managed KB, Quotas

| Number | What |
|---|---|
| **$50 / $500 min** | Pinecone Standard / Enterprise per month |
| **$0.33/GB/mo** | Pinecone storage (both) |
| **$16-18 / $24-27 per M RU** | Standard / Enterprise read units |
| **1 RU / GB namespace** | Query cost scales with namespace size, not filter selectivity |
| **99.95%** | Pinecone Enterprise uptime SLA (none on public table for Standard) |
| **$190/mo** | Pinecone HIPAA add-on on Standard (included on Enterprise) |
| **20 / 200 indexes; 100k namespaces** | Standard / Enterprise indexes; namespaces per index both |
| **~640 / ~1,840 / ~1,620 QPS** | pgvector / Qdrant / Weaviate at 1M vectors |
| **471 vs 41.47 QPS** | pgvectorscale vs Qdrant at 50M vectors (scale reversal) |
| **$5/GB/mo; $1/1k; $4+$1/1k** | Bedrock KB storage; Standard Retrieve; Agentic + underlying |
| **$350 / $850 / mo** | Official 50 GB + 100k standard / 100k agentic x 2 |
| **~ $345/mo** | Third-party AOSS floor 2 OCU x $0.24/hr |
| **$0.15 / 1k text units** | Bedrock Guardrails content filter |
| **$0.75 / $2 / $4 / 1k** | Google Agent Search Semantic / Core GA / Advanced GA |
| **600 / 60 RPM** | Vertex retrieval / management |
| **10,000** | Pinecone `$in` max values; Vertex files per import |

### Graph & Agent

| Number | What |
|---|---|
| **~75%** | GraphRAG indexing $ in LLM extraction (Microsoft) |
| **0.1% / >700x / 4%** | LazyGraphRAG index vs full GraphRAG; Z100 query vs global; Z500 vs C2 global |
| **50-100 tokens** | FastGraphRAG recommended chunk size |
| **2** | DRIFT default local follow-up iterations |
| **3** | Common production max retrieve retries (official tutorial: none) |
| **v3.0.9 (2026-04-13)** | microsoft/graphrag maintenance/CVE release |
| **50,000+ shards/node; 1M tenants / 20 nodes** | Weaviate MT blog claim |
| **n/2+1** | Weaviate QUORUM (RF=6 -> 4) |
| **2-10x** | Agentic RAG cost/delay vs standard hybrid if loop caps are not set |

---

## Key Takeaways

- RAG is **two planes sharing versioned indexes**, not `retrieve()` then `generate()`. The model never searches; tools and predicates do.
- **Hybrid + RRF (k=60)** is the default fuse because BM25 and cosine do not share a scale. Pinecone hybrid without `hybrid_score_norm` is a silent keyword-only system.
- **Two-stage ranking:** cheap recall (k=50-150), expensive precision (n=5-20). Stuffing 50 chunks into 128k is how you buy lost-in-the-middle **and** hallucinated citations.
- **ACL is a pre-filter / namespace**, not a prompt instruction. Post-filter ANN collapses authorized recall. Graph **reports** need ACL too.
- **Agentic RAG without a hop cap is an open proxy.** Official LangGraph has none. Cap retries (~3), forbid ungrounded generate on sensitive corpora, allowlist CRAG fallbacks.
- **Budget $ as rerank + SUM LLM loops**, not as embedding pennies. Reference mix [inferred] ~ $3.5/1k with luna+Voyage rerank, excluding RUs and retries; terra/Sonnet generate alone ~ $12-13/1k.
- **Graph last.** LazyGraphRAG index $ ~ vector and 0.1% of full GraphRAG. Vector still wins many single-hop evals. Microsoft OSS is maintenance-mode research, not a product SLA.
- Skip RAG under **~200k tokens** if prompt-cache economics win. Start chunks 400-800 tok [inferred]; promote to contextual/late/`voyage-context-4` from **eval**, not from a blog.
- The interview-friendly answer is not "RAG always helps." It is **"RAG helps when the knowledge is large, mutable, access-controlled, or auditable. Otherwise, simpler context engineering may win."**

---

*Practice the Q&A out loud; recode the breaker states from memory; recompute the $3.5/1k mix on a whiteboard with the assumptions listed.*

---

**Sources:**
- Lewis et al. (NeurIPS 2020) -- RAG original paper
- Anthropic Contextual Retrieval (2024-09-19)
- Microsoft GraphRAG (Edge et al., arXiv 2404.16130)
- Self-RAG (Asai et al., ICLR 2024)
- CRAG (Yan et al.)
- HyDE (Gao et al.)
- IRCoT (Trivedi et al., ACL 2023)
- ColBERT (Khattab & Zaharia, SIGIR 2020)
- RRF (Cormack, Clarke, Buettcher, SIGIR 2009)
- HippoRAG / HippoRAG 2
- LazyGraphRAG (MSR 2024-11-25)
- ALCE (Gao et al., EMNLP 2023)
- RAGAS
- DeepEval
- Adaptive-RAG (Jeong et al., NAACL 2024)
- Vecta 2026 Chunking Benchmark
- WANDS Benchmark
- Carnegie Mellon (June 2026) Financial Compliance Study
- MLOps Community Benchmark (May 2026)
- EchoLeak (late 2025)
- OWASP LLM Top 10
