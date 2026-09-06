# Module 01: Retrieval-Augmented Generation (RAG)

## What Is This?

RAG is an open-book exam for language models. Instead of relying solely on what a model memorized during training, you give it a search engine over your private data -- at question time, it fetches the relevant passages and reads them before answering. Lewis et al. (NeurIPS 2020) defined the split that still holds: a **parametric** generator plus a **non-parametric** index. The generator never searches; the retriever returns documents, those documents are concatenated with the user input, and then the model generates grounded in retrieved evidence. Think of a library: **ingest** is the back office (cataloging, stamping permissions, writing the card catalog), and **query** is the reference desk (a patron asks, you pull only shelves they are entitled to see, rank the best passages, and read them aloud with page citations).

**Why not dump the whole corpus into the prompt?** Anthropic's own guidance: if the knowledge base is **under ~200k tokens (~500 pages)**, skip RAG and cache the corpus. Past that threshold, you cannot afford (and the model cannot effectively use) a uniformly stuffed window -- see lost-in-the-middle below.

---

## Part 1: System Topology & Data Flow

A production RAG system is **two independently scaled planes sharing versioned indexes**, plus a **control loop** (grade, rewrite, retrieve, generate) around those indexes. Coupling ingest to query means p99 latency tracks reindex operations.

### Architecture Diagram

```
                         TELEMETRY / OBSERVABILITY SINKS
         +--------------------------------------------------------------+
         |  OTel traces (ACL-redacted spans)   watermark lag canaries    |
         |  nDCG@k golden set   RU/RPM/TPM meters   provenance (WORM)  |
         |  LangSmith/equivalent: chunk text redacted to caller's ACL   |
         +------^----------------^-----------------^--------------------+
                | spans          | metrics          | audit events
                |                |                  |
+---------------+----------------+------------------+-------------------+
| CONTROL PLANE  (authz, routing, versioning, loop caps)                |
|                                                                       |
|  +-------------+  +----------------+  +-----------+  +-------------+  |
|  | PEP / IdP   |  | Adaptive Router|  | LangGraph |  | Ingest      |  |
|  | Entra/JWT   |  | chitchat|fact  |  | orchestr. |  | watermarks  |  |
|  | -> ACL pred.|  | multi-hop|glob.|  | + hop cap |  | alias flip  |  |
|  +------+------+  +-------+-------+  +-----+-----+  +------+------+  |
|         | tenant           | route          | tools         | pin     |
+---------+------------------+----------------+---------------+---------+
          |                  |                |               |
          v                  v                v               v
+---------+------------------+----------------+------------------+------+
| DATA PLANE  (ingest write vs query read -- independently scaled)      |
|                                                                       |
|  INGEST (write):  source -> parse -> PII DLP -> ACL stamp -> chunk -> |
|                   contextualize -> embed/sparse -> graph extract ->    |
|                   upsert (live only after alias/snapshot flip)         |
|                                                                       |
|  QUERY (read):    authz filter -> embed q -> hybrid retrieve -> fuse  |
|                   -> rerank -> [grade/rewrite loop] -> generate -> cite|
|                                                                       |
|  +--- TOOL PROXIES (MCP tools/call -- least privilege) -----------+   |
|  | retrieve_public_kb | retrieve_hr | sql_customer | graph_local  |   |
|  | rerank_api         | generate_fm | (NO omnibus search(coll.))  |   |
|  | Identity from verified token / RunContext -- NEVER model JSON  |   |
|  +----------------------------------------------------------------+   |
+---------+--------------+------------------+-------------------+-------+
          |              |                  |                   |
          v              v                  v                   v
+-------------------------------------------------------------------------+
| PERSISTENCE LAYER  (five indexes; query pins a complete snapshot)        |
|                                                                         |
|  +-----------+ +-----------+ +-----------+ +-----------+ +-----------+  |
|  | Dense ANN | | Sparse/   | | ACL bitmap| | Graph     | | Caches    |  |
|  | HNSW/IVF/ | | lexical   | | pre-filter| | entities +| | rerank    |  |
|  | BBQ-HNSW  | | BM25/     | | before ANN| | reports + | | retrieve  |  |
|  |           | | SPLADE    | |           | | text units| | embed     |  |
|  +-----------+ +-----------+ +-----------+ +-----------+ +-----------+  |
|  LangGraph: PostgresSaver (threads) + Store (cross-thread)              |
|  Graph artifacts: Parquet + vector, keyed by graph_build_id             |
+-------------------------------------------------------------------------+
```

### Planes (Do Not Couple)

| Plane | Owns | Typical Components | Failure if Coupled |
|---|---|---|---|
| **Ingest (write)** | Parse, PII redaction, ACL stamp, chunk, contextualize, embed, sparse encode, graph extract, community reports, checkpoint | Connectors, workers, embedding/rerank batch APIs, HNSW/IVF build, Leiden clustering | Query p99 tracks reindex; a stuck extractor stalls answers |
| **Query (read)** | Authz filter, hybrid retrieve, fuse, rerank, agent loop, generate, cite | ANN + inverted index, RRF/RSF, cross-encoder, LangGraph/LlamaIndex loop, generator | Ingest schema change silently mismatches query embeddings |

### Five Index Types in One Product

1. **Dense ANN** -- HNSW / IVF / BBQ-HNSW over embedding vectors (cosine or inner product).
2. **Sparse / lexical** -- BM25 (Elasticsearch/OpenSearch/Weaviate), SPLADE or `pinecone-sparse-english-v0`, Postgres `tsvector` (not BM25 -- use ParadeDB/`pg_search` for true BM25).
3. **Metadata / ACL bitmap** -- pre-filter before ANN. Pinecone slab metadata as roaring bitmap of eligible IDs; Weaviate/OpenSearch/ES filter clauses; Azure document-level ACL at query time.
4. **Graph** -- entity/relationship tables + community reports + optional vector index over entities, text units, and reports.
5. **Rerank cache** -- `(query_hash, doc_id, model, version) -> score` with short TTL. Not a recall index.

### Request-Flow Narrative (One User Question, Hybrid + Optional Rewrite)

1. **Control / PEP.** TLS terminates. The verified Entra/JWT (not a tool argument) expands groups. The PEP emits a **hard filter predicate** (`tenant_id`, `userIds`/`groupIds`/`rbacScope`, `status=current`). Recency may be soft (decay) after ACL; soft recency without ACL still leaks.
2. **Adaptive router (control).** Classifier or cheap LLM: `chitchat` -> no retrieve; `factoid` -> hybrid+rerank; `multi-hop` -> agent 2-3 hops; `global` -> LazyGraphRAG / community reports. This is Adaptive-RAG (Jeong et al., NAACL 2024) in production clothing.
3. **Data plane, retrieve.** Query embed (50-token class) hits dense ANN **and** BM25/sparse **in parallel**, both with the ACL filter on every arm. Use `filter` context (unscored) on all hybrid legs so neither BM25 nor kNN leaks stale/unauthorized hits.
4. **Fuse.** RRF (rank-only, k=60 default) or RSF/alpha/DBSF (score-space). Pinecone single-index hybrid **must** enable `hybrid_score_norm` or unbounded sparse drowns cosine `[-1,1]`.
5. **Tool proxy, rerank.** Cross-encoder over fused N=50-150, keep 5-20 (Anthropic eval used 150->20; Azure Semantic Ranker reorders hybrid top **50**). Never send pre-rerank noise to the generator (lost-in-the-middle + hallucinated citations).
6. **Agent loop (control + tools).** LangGraph: `generate_query_or_respond` binds a retriever tool; retrieval runs only when the model emits a tool call; `grade_documents` routes to `generate_answer` or `rewrite_question`. Official tutorial has **no hop counter** -- production adds `retry_count` / `MAX_ATTEMPTS` (common cap: **3**) and a wall-clock. Checkpointer (`PostgresSaver`) snapshots thread state after every super-step.
7. **Generate + cite.** Prompt = instructions + edge-placed top chunks (U-shaped attention). Citations are **IDs from the retrieved set only**. Telemetry writes provenance: `source_uri`, `chunk_id`, `retriever`, `rerank_score`, `user_id`, `tenant`, `index_build_id`.
8. **Ingest (async, other plane).** Connector watermark (S3 etag / Drive revision / CDC LSN / SharePoint ACL version) -> sha256 blob -> DLP **before** embed -> chunk with `chunk_id = hash(doc_id, chunker_version, text)` -> embed keyed by `embed_model+dim+chunk_id` -> upsert under `index_version` -> **then** flip the query alias. Graph: per-chunk extract checkpoint; Leiden **only** on a closed chunk set; reports last. Query plane pins a complete `graph_build_id`.

### Vendor Query-Path Topology (Interview Traps)

| Vendor | Key Behavior | Trap |
|---|---|---|
| **Weaviate** | Hybrid since v1.17; `alpha` 0=keyword, 1=vector, **server default 0.75**; `relativeScoreFusion` default >= v1.24 | Forgetting to set alpha explicitly means you think you are 50/50 but are 75/25 |
| **Pinecone** | Single index dense+sparse `metric=dotproduct` only; two-index + client RRF; FTS + `dense_vector`. Naive IVF+filter: recall collapse at **50%** filtered, unusable at **90%** | Without `hybrid_score_norm`, sparse dominates. `$in`/`$nin` max **10,000**. Cost: **1 RU per 1 GB** of the queried namespace |
| **Elasticsearch** | Retrievers GA **8.16**; `rank_constant=60`, `rank_window_size=10` (>= `size`). Nest reranker **outside** `rrf`. BBQ up to **32x** compression | `rank_window_size=10` default is too small -- raise to 50-100 |
| **OpenSearch** | `hybrid` + search pipeline; max **5** subqueries; coordinator fusion after per-shard legs; `pagination_depth` required when `from > 0` | Cannot nest under `function_score` / `constant_score` / `script_score` / `boosting` |
| **Qdrant >=1.10** | `prefetch[]` then **top-level** `FusionQuery` (RRF/DBSF) | Fusion inside prefetch = per-shard (wrong for multi-shard) |
| **pgvector** | One SQL round-trip, `FULL OUTER JOIN`, RRF | `ts_rank` is NOT BM25 -- use ParadeDB for true BM25 |
| **Bedrock KB** | `HYBRID` or `SEMANTIC`; hybrid needs a filterable text field else semantic fallback | Guardrails cover query and answer, **not** retrieved source text |
| **Azure AI Search** | BM25+HNSW -> RRF -> Semantic Ranker top **50** | Agentic retrieve does **not** apply index scoring profiles. Pass Entra token in `x-ms-query-source-authorization` |

---

## Part 2: Core Mechanics & Algorithms

### Three Invariants

**I1. The generator does not search.** Retrieval is a tool (or a DAG stage). The parametric model emits a query or a tool call; the data plane executes; chunks return as observations. Prompt text is not an authorization boundary.

**I2. Pin `model_id + dimension + similarity metric + version` in the index schema.** Changing any one is a full re-embed. Query embeddings from model B against index A produce silent recall collapse.

**I3. Authorization is a query predicate applied before ANN, on every hop.** Post-filter-only ANN: as the forbidden set grows, top-k fills with unauthorized neighbors and **authorized recall -> 0**.

### Hybrid Retrieve and Fusion

Dense misses exact IDs (`TS-999`, SKUs, statute numbers). BM25 misses paraphrase. Anthropic Contextual Retrieval production sketch: chunk -> TF-IDF + embeddings -> BM25 top + dense top -> rank fusion -> top-K into the prompt.

**RRF** (Cormack, Clarke, Buettcher, SIGIR 2009). Rank-only, scale-free:

```
RRF(d) = SUM over all retrievers r of: 1 / (k + rank_r(d))
```

Default **k = 60** in Elasticsearch `rank_constant`, OpenSearch, Weaviate `rankedFusion`, Qdrant RRF, and typical Postgres CTEs.

| Rank | Contribution (k=60) |
|---|---|
| 1 | 1/61 = 0.0164 |
| 60 | 1/120 = 0.0083 |

Documents in **both** lists outrank a document that wins only one list. BM25 unbounded scores and cosine `[-1,1]` never share a numeric space -- that is why RRF exists. Tuned hybrid RRF reaches **NDCG 0.7497** on WANDS -- 7.5% above either retriever alone.

**Complexity:** After each retriever returns its top-k: O(k * |R|) to accumulate scores (hash map keyed by `doc_id`), then O(k * |R| * log(k * |R|)) to sort the union. Dominated by ANN + inverted-index latency, not the fuse.

**Score Fusion (When Magnitudes Are Trusted)**

| Method | Who | Mechanism | When It Wins |
|---|---|---|---|
| **Relative Score Fusion** | Weaviate default since v1.24 | Min-max each list to [0,1], then alpha-weighted sum | Score gaps carry signal |
| **Alpha convex combo** | Pinecone single-index; Weaviate `alpha` | `combined = alpha*dense + (1-alpha)*sparse` | Same index, same query |
| **DBSF** | Qdrant | Normalize by mean/std of the prefetch top-k | Calibrated retrievers; outlier-sensitive |
| **min_max + arithmetic_mean** | OpenSearch `normalization-processor` | Score-space mix via search pipeline | Explicit 0.3/0.7 weights |

**Pinecone production trap:** Sparse/BM25 scores are unbounded; dense cosine is about [-1,1]. Without `hybrid_score_norm` on the query vectors, sparse **dominates**.

### Two-Stage Ranking: Bi-Encoder Then Cross-Encoder

- **Bi-encoder:** Encode query once, encode docs offline, score by cosine/IP. O(1) query encode + ANN. Stage-1 recall, k=50-200.
- **Cross-encoder:** Jointly attend over `(query, document)` -- one forward pass per candidate. Stage-2 precision, keep **3-20** for the generator. This is the single largest precision gain in the pipeline: **Recall@5 jumps from 0.695 to 0.816 (+17.4%), MRR@3 from 0.433 to 0.605 (+39.7%)**.
- **ColBERT late interaction** (Khattab & Zaharia, SIGIR 2020): each passage is a matrix of token embeddings; score = SUM_i MAX_j cos(q_i, d_j) (MaxSim). ColBERTv2 + PLAID: **2.5-7x GPU** and **9-45x CPU** latency cut vs vanilla ColBERTv2. BGE-M3 emits ColBERT + dense + sparse in **one** forward pass.
- **LLM-as-reranker:** Pointwise/pairwise/listwise. A frontier judge over 50 chunks dwarfs a cross-encoder. Use a cheap model for agentic **binary** `grade_documents`, not as the primary 100-way ranker.

### Chunking Strategies

| Strategy | Extra Model Calls | Helps | Hurts | Vecta 2026 Accuracy |
|---|---|---|---|---|
| Fixed token window + overlap | No | Predictable vector count | Mid-sentence splits; orphaned pronouns | Matches semantic on many tasks |
| Recursive (`\n\n`->`\n`->`.`->` `) | No | Fewer mid-sentence breaks | Unaware of semantic boundaries | 69% (top in benchmark) |
| Sentence / structure-aware | No | Legal/markdown headings | Uneven sizes; huge tables | -- |
| Semantic (embedding breakpoints) | Embed sentences | Topic shifts | Cost + unstable boundaries; ~14x slower | 54-70% (variable) |
| **Contextual Retrieval** (Anthropic 2024) | LLM per chunk; prompt-cache the document | BM25 **and** dense **and** reranker **and** generator see situated text | Ingest cost; PII spread | Cuts top-20 failures ~67% with rerank |
| **Late chunking** (Jina, 2024) | No extra LLM; long-context embedder | Dense vectors carry doc-level context via token-then-pool | Lexical index unchanged; no gain on short docs (~62 chars) | -- |
| **Contextualized chunk models** (`voyage-context-4`) | No extra LLM | Chunk vectors conditioned on the full document | Vendor API; BM25 text unchanged | +23.66% vs Jina-v3 late (vendor-stated) |
| Parent-document / small-to-big | No | Retrieve small, generate on parent | Parent may exceed context; ACL must copy to both | -- |
| Agentic (LLM-decided) | 10-50x indexing cost | Highest retrieval quality | Cost | -- |

**Contextual Retrieval eval** (Gemini Text 004, top-20, 1-recall@20): baseline fail **5.7%** -> contextual embeddings **3.7%** (-35%) -> +BM25 **2.9%** (-49%) -> + Cohere rerank 150->20: **1.9%** (-67%). Prompt-cache contextualize: **$1.02 / 1M document tokens** (one-time). 737-chunk demo ingest **~$15 -> ~$3** at 70-80% cache hits.

**Late chunking.** Berlin Wikipedia cosine "Berlin" vs "Its more than 3.85 million inhabitants": **0.7084 -> 0.8249**. Does **not** inject company names into BM25. No gain on Quora (~62 char docs).

**Practical starting point:** 400-800 tokens, 10-20% overlap, sentence snap, `doc_id`/`section`/`acl`/`version` on every chunk, parent pointer for generate-time expansion. A "context cliff" at ~2,500 tokens where response quality degrades.

### Embedding Models

| Model | Dim | Context | Price | Notes |
|---|---|---|---|---|
| OpenAI `text-embedding-3-small` | 1536 (Matryoshka) | 8191 | **$0.02 / 1M** | MTEB 62.3% |
| OpenAI `text-embedding-3-large` | 3072 | 8191 | **$0.13 / 1M** | MTEB 64.6%; `dimensions` can shorten |
| Voyage `voyage-4-large` | 1024 (256/512/2048) | 32k | **$0.12 / 1M**; 200M free | Batch 33% off |
| Voyage `voyage-4` | 1024 | 32k | **$0.06 / 1M**; 200M free | Quality/cost pick |
| Voyage `voyage-4-lite` | 1024 | 32k | **$0.02 / 1M**; 200M free | Latency/cost |
| Voyage `voyage-context-4` | 1024 | 32k / 120k doc | **$0.12 / 1M** | Contextualized chunks; +6.76% vs Anthropic contextual (vendor) |
| Cohere `embed-v4.0` | 256-1536 | 128k; text+image | **$0.12 / 1M text**; $0.47 image | Confirm dashboard |
| Pinecone `llama-text-embed-v2` | -- | -- | **$0.16 / 1M** | Hosted |
| BAAI **BGE-M3** | 1024 dense + sparse + ColBERT | 8192; 100+ langs | Self-host (~569M) | One pass, three modes; free (MIT) |
| Qwen3-Embedding-8B | Variable | 32K | Self-hosted | ~70.6 MTEB |
| Jina v5-text | -- | -- | TBD | 71.7 MTEB v2 |

**Critical caveat**: MTEB is a useful prior, not a decision oracle. One legal retrieval system found the MTEB top-3 models ranked 5th, 7th, and 2nd on their in-domain eval while BGE-large-en-v1.5 (MTEB rank 11th) won. Always run in-domain evaluation.

OpenAI hard limits: **8192 tokens/input**, **2048 inputs/request**, **300,000 tokens summed**. Batch commonly **50% off**. A **256-dim** `3-large` can beat unshortened **1536-dim** `ada-002` on MTEB retrieval (vendor-stated).

**Storage math:** float32 1536-d ~ 6.1 KB/vector; 3072-d ~ 12.3 KB. 10M chunks at 1536-d ~ 61 GB raw; Pinecone **$0.33/GB/mo** -> **~$20/mo** if billed size matched raw vectors. Elastic BBQ **32x** compression (vendor claim).

### Vector Database Selection

| Database | QPS (1M) | p99 Latency (10M) | Best For | Cost |
|---|---|---|---|---|
| pgvector | ~640 | 5-8ms | <10M vectors, existing Postgres | $0 (existing infra) |
| Qdrant | ~1,840 | ~12ms | Latency-critical, complex filters | $600-$1,200/mo |
| Weaviate | ~1,620 | ~16ms | Native hybrid search, multimodal | Moderate |
| Pinecone | ~1,620 | Varies | Zero-ops, quick scaling | $1,500-$3,000/mo |
| Milvus | High | Low | Billion-vector scale | Self-hosted |

**Scale reversal**: At 50M vectors, pgvectorscale (471 QPS) outperforms Qdrant (41.47 QPS). Above 1B vectors, only Vespa and Milvus distributed deployments are production-grade.

### Agentic RAG State Machine

```
                    +-----------------------------+
                    | generate_query_or_respond   |
                    | (model + retriever.bind)    |
                    +--------------+--------------+
                     tool call?    |     no tool: respond
                    +--------------+--------------+
                    v                             v
            +---------------+              +----------+
            | ToolNode      |              |  END     |
            | retrieve      |              +----------+
            +-------+-------+
                    v
            +---------------+     all irrelevant
            | grade_docs    +--------------------+
            | yes/no        |                    v
            +-------+-------+          +-----------------+
              some  | relevant         | rewrite_question|
                    v                  +--------+--------+
            +---------------+                  |  MUST cap hops
            | generate_     |                  |  (tutorial does not)
            | answer        |                  v
            +---------------+           back to generate_query...
```

**Self-RAG** (Asai et al., ICLR 2024): reflection tokens `Retrieve` {yes, no, continue}; `IsRel` {relevant, irrelevant}; `IsSup` {fully, partial, none}; `IsUse` {5...1}. Production teams almost always **prompt** a separate grader rather than train tokens.

**CRAG** (Yan et al.): evaluator -> Correct / Incorrect (web/external) / Ambiguous (mix). Open web fallback is an **exfil path** for confidential queries.

**HyDE** (Gao et al.): LLM writes a hypothetical answer; embed that; retrieve neighbors. Two documented failures: mis-interprets queries without corpus context; **biases** open-ended queries. Use `include_original=True`.

**IRCoT** (Trivedi et al., ACL 2023): what to retrieve at step n depends on step n-1. GPT-3 paper-stated: retrieval up to **+21 points**, QA up to **+15 points** on HotpotQA / 2Wiki / MuSiQue / IIRC. HippoRAG: single-step PPR **10-20x cheaper, 6-13x faster** than iterative retrieve.

**Loop bound invariant.** Official LangGraph tutorial can loop until runtime timeout. Production: `retry_count`, wall-clock, terminal `insufficient_evidence`. Do **not** fall back to parametric knowledge on ACL-sensitive corpora.

**Carnegie Mellon (June 2026):** Agentic RAG with knowledge graphs cut hallucinations from 14.1% to 4.9% on a 9,000-question financial-compliance dataset at ~220ms extra latency per round. MLOps Community benchmark (May 2026): ~62% hallucination reduction across 47 production deployments.

### Graph RAG

Vector RAG fails **global** questions ("themes in this corpus") -- query-focused summarization, not top-k lookup (Edge et al., arXiv 2404.16130).

**Index pipeline:** chunk (TextUnits) -> LLM extract entities/relationships/claims -> KG -> Leiden hierarchical communities -> bottom-up community reports -> embed units/entities/reports -> persist Parquet + vector. Microsoft: LLM extraction is ~**75% of indexing cost**.

`microsoft/graphrag` (fetched 2026-09): **maintenance mode**, bugfix/CVE only (e.g. v3.0.9 2026-04-13). Not an officially supported Microsoft offering.

| Query Mode | Mechanism | Query Class |
|---|---|---|
| **Local** | Match entities -> neighborhood + chunks | Entity-specific |
| **Global** | Map-reduce over **all** community reports | Corpus themes |
| **DRIFT** | HyDE primer + top-K reports -> follow-ups -> local iterations (default **2**) | Local that needs a global primer |

**LazyGraphRAG** (MSR 2024): no LLM community summaries at index time. Index cost **identical to vector RAG** and **0.1% of full GraphRAG** (Microsoft-stated). At Z100: **>700x lower query cost** than GraphRAG global. At Z500 (**4%** of GraphRAG global query cost): beats compared methods on local+global.

**HippoRAG:** LLM + KG + Personalized PageRank; up to **~20%** over SOTA RAG on multi-hop QA. HippoRAG 2: indexing tokens **9M vs 115M** for GraphRAG-class on MuSiQue. But: structure-based methods can **drop 5-10 F1** on simple QA vs strong embeddings -- keep a vector path for factoid.

**Production shape:** graph **and** vector. Router picks `vector_tool` vs `graph_local` vs `graph_global`. GraphRAG-Bench: not all graph methods beat a strong GPT-4o-mini baseline -- over-structure can **hurt**.

---

## Part 3: Token Economics & NFR Analysis

### Reference Query -- Cost per 1k Runs (Inferred)

Public vendor pages do **not** sell a "RAG query" SKU. Figures multiply published rates by a stated mix. State assumptions in a design review.

**Assumptions:** 1k user questions, no retries. Query embed 50 tokens; retrieve 80 fused chunks; rerank 80; keep 8 x 500 tokens = 4k context; generate 4k input + 400 output. Dense: OpenAI `3-small` $0.02/1M. Rerank: Voyage `rerank-2.5`. Generate: `gpt-5.6-luna` uncached $0.20/$1.20 per 1M in/out.

| Line Item | Arithmetic | Inferred $/1k |
|---|---|---|
| Query embed | 1k x 50 tok x $0.02/1M | **$0.001** |
| Voyage rerank-2.5 | (50x80 + 80x500 = 44k tok/q) x $0.05/1M | **$2.20** |
| Generate luna uncached | 4k in x $0.20/1M + 400 out x $1.20/1M | **$1.28** |
| **Subtotal** | embed + rerank + generate | **~$3.50** |

**Excludes** vector DB RUs, graph map-reduce, and retries. **Rerank dominates this mix** on mini-tier generation.

**Model-tier flip (same 4k in + 400 out, uncached):**

| Generator | In/Out per 1M | Generate Inferred/1k |
|---|---|---|
| `gpt-5.6-luna` | $0.20 / $1.20 | **$1.28** |
| `gpt-5.6-terra` | $2 / $12 | **$12.80** |
| Claude Sonnet 5 | $2 / $10 | **$12.00** |
| Claude Haiku 4.5 | $1 / $5 | **$6.00** |

On Sonnet/terra, **generation dominates**; on luna + Voyage rerank, **rerank dominates**.

**Prompt-caching impact:** Anthropic 5m cache write **1.25x**, read **0.1x** (Fable/Mythos 5.1 hits **0.025x**). OpenAI auto cache on prompts **>1,024 tokens**, cached input on luna is **$0.02** (vs $0.20 uncached). Break-even: **2** hits at 1.25/0.1.

**Rerank Alternatives**

| Path | Rate | Inferred $/1k queries |
|---|---|---|
| Voyage `rerank-2.5` | $0.05/1M tok | **$2.20** |
| Voyage `rerank-2.5-lite` | $0.02/1M | **~$1.00** |
| Cohere Rerank 3.5 (Bedrock) | $2.00/1k searches | **$2.00** |
| Pinecone Inference rerank | $2/1k requests | **$2.00** |
| Google Ranking API | $1.00/1k; 80k free units/30d | **$1.00** |
| Bedrock Managed KB rerank | $0 | included |

**Cohere search-unit inflation:** 1 query + up to 100 documents; if query+doc > 500 tokens, auto-split; each chunk counts as a document. 80 fused 800-token chunks can become **>1 search unit** per question.

**Corpus embed (1B tokens):** 3-small **$20**; voyage-4-lite **$20**; voyage-4 **$60**; context-4 **$120**; 3-large **$130**. Batch: OpenAI 50% off; Voyage 33% off (free-token credits do **not** apply to Batch).

**Production Cost by Scale**

| Scale | Monthly Cost | Key Driver |
|---|---|---|
| Small (<10K queries/mo) | $150-$400 | LLM inference |
| Mid-size (10K-100K) | $600-$1,500 | LLM inference + vector DB |
| Enterprise (1M queries/mo) | $5,000-$15,000 | All components at scale |

### Latency SLA Targets

No major vendor publishes p50/p95/p99 for "RAG end-to-end." Decompose stages.

| Stage | p50 | p95 | p99 | Mitigation |
|---|---|---|---|---|
| Query embed | ~70ms | ~100ms | ~120ms | Batch embeddings, local model |
| Hybrid retrieve | ~6ms added | ~10ms | ~15ms | BM25 + vector in parallel |
| Cross-encoder rerank (50) | ~100ms | ~200ms | ~300ms | Lighter reranker, reduce candidates |
| LLM generation | ~1000ms | ~2000ms | ~3000ms | Prompt caching, streaming |
| **Simple RAG total** | **~700ms** | **~2s** | **~3s** | |
| **Agentic RAG (3 rounds)** | **~8s** | **~15s** | **~20s** | Parallel retrieval, early stopping |

**Architecture-derived targets:** Set retrieve+rerank SLO independently of generate. Retrieve timeout **200-500 ms** as policy. Circuit-break the index independently of the FM. Cap agent hops because each hop adds +1-3 LLM calls on the p95 tail.

### Throughput and Back-Pressure

| Dependency | Published Limit |
|---|---|
| Cohere Rerank | Trial **10 RPM**; production **1,000 RPM** |
| OpenAI embeddings | **300k tok/request**, **2048** inputs |
| Pinecone serverless | RU/WU quotas by plan |
| Vertex RAG retrieval | **600 RPM** (confirm live quota) |

**Capacity identity:** `retrieve_RPM = user_QPS x expected_hops`. Agent loops: 3 retrieves x 1k user QPS = **3k retrieve RPM** -- size the vector DB and the reranker for the loop, not the user QPS.

### NFRs and Trade-offs

| NFR | Production Stance | Competes With |
|---|---|---|
| **Availability** | Pinecone Enterprise **99.95%** uptime SLA; Standard: none on public table | Cost (Enterprise min $500/mo vs Standard $50/mo) |
| **RPO** | Ingest watermark + sha256; alias flip only after complete upsert | Freshness (CDC lag) |
| **RTO** | Query alias rollback to previous snapshot (seconds) vs rebuild HNSW (hours) | Index rebuild time |
| **Consistency** | Weaviate QUORUM = n/2+1 (RF=6 -> 4). `ONE` can cite a deleted replica | p99 (wait for replicas) |
| **Compliance** | Pinecone HIPAA: Standard $190/mo or Enterprise included | Latency (residency path) |

---

## Part 4: Distributed Resilience & Security

### Durable Execution

**Ingest as a replayable workflow (Temporal / Kafka consumer):**
1. Source watermark -> idempotency key for the document version.
2. Raw blob + sha256. Failed parse -> dead-letter/quarantine, not into the live alias.
3. Parse/chunk with `chunk_id = hash(doc_id, chunker_version, text)`.
4. Embed job keyed by `embed_model + dim + chunk_id` (replay skips completed).
5. Upsert with `index_version`; only then flip query alias (compare-and-swap on alias is the distributed lock).
6. Graph extract: per-chunk checkpoint; community detect only on closed chunk set; reports last.

**Query-loop (LangGraph):** PostgresSaver snapshots each super-step; finished node writes in a failed super-step are durable and not recomputed. `InMemorySaver` is for tests only. Conversation memory stays in the checkpointer, **not** the MCP session.

### Failure Taxonomy

**When RAG fails, the failure point is retrieval 73% of the time, not generation.**

| Class | Examples | Detection | Handling |
|---|---|---|---|
| **Transient** | 429, RU throttle, 5xx, timeout | Error rate, Retry-After | Exponential backoff + jitter; hedge; retry idempotent reads only |
| **Permanent** | 4xx auth, missing index, filter `$in` > 10k | Non-retryable code | Fail closed; do not rewrite-loop |
| **Poison pill** | Connector blob that crashes parser; prompt-injected chunk; sha256 mismatch | Repeat crash on same chunk_id | Quarantine key; DLQ; never block partition |
| **Stale** | Alias not flipped; Weaviate `ONE`; Pinecone upsert lag; graph_build_id age | Watermark lag canaries | Pin snapshot; QUORUM; alias rollback |
| **Semantic poison** | HyDE biased hypothetical; embedding drift (model B vs index A) | Frozen golden nDCG | Pin schema; dual-write + shadow eval; alias flip after re-embed |

**Nested retries create self-inflicted outages.** An LLM loop retries a tool call, the SDK retries the API request, the workflow engine retries the step, and the provider retries internally. Production systems need **global retry budgets** across the entire run.

### Circuit Breaker (Closed -> Open -> Half-Open)

Independent breakers: **vector index**, **reranker**, **generator**. A Pinecone RU storm must not starve generate (bulkhead).

**Fallback chain:** (1) last-good retrieve cache, (2) BM25-only, (3) "index unavailable" refusal -- **never** generate ungrounded if policy forbids. Model chain: primary FM -> secondary FM -> deterministic extractive fallback (return top chunk titles + "insufficient evidence").

### Enterprise Security

**Zero-Trust MCP.** `tools/call` on a retriever is a **data exfil API**.

1. **Server-side identity.** Tenant/ACL from verified token / RunContext, never from tool arguments the model filled. Predicate pushdown so ANN never ranks cross-tenant rows.
2. **Least privilege per tool.** `retrieve_public_kb` vs `retrieve_hr` vs `sql_customer`. No omnibus `search(query, collection)`.
3. **Stateless MCP + stateful RAG.** Memory in the checkpointer, not the MCP session.
4. **No raw chunk echo** to unauthorized traces.

**Isolation Ladder**

| Pattern | Guarantee | Cost |
|---|---|---|
| Metadata `tenant_id` filter | App-bug can omit filter | Cheapest; scans full namespace |
| **Namespace / collection per tenant** | Query cannot cross (1 GB = 1 RU; 100x1 GB cheaper than 100 GB filter) | 100k namespaces/index (Pinecone) |
| Azure document-level ACL | Entra token vs ingested permissions; sync lag is a leak window | Indexer must ingest permission metadata |
| Weaviate native MT | Separate shard per tenant; omit key = error, not scan. Blog: 50,000+ shards/node; 1M tenants / 20 nodes | Shard ops |
| Instance / BYOC | Strongest (HIPAA/finance) | Highest $ |

**PII Pipeline:**
1. **Detect** at ingest before embed. Vectors are derived personal data.
2. **Redact** before Contextual Retrieval prepend (otherwise names/quarters/revenue copy into every chunk).
3. **Audit** immutable provenance (who retrieved which chunk_id).
4. Graph: extraction amplifies PII into entity nodes; community reports can summarize secrets into a globally readable node -- ACL on **reports**, not just raw chunks.

**Auditability:** ALCE (Gao et al., EMNLP 2023): on ELI5 even the best models lacked complete citation support **50% of the time**. RAGAS WikiEval: faithfulness aligned with humans at **0.95** accuracy vs **0.72** for direct GPT scoring. Production metric **provenance fidelity** = cited IDs (a) were retrieved, (b) support the claim (NLI/IsSup), (c) user was entitled to see.

**Poison-pill incidents:** EchoLeak (late 2025): unclicked email manipulated Microsoft 365 Copilot's RAG pipeline, exfiltrating corporate data. March 2026 mass poisoning: flooded external knowledge bases with manipulated data. Detection: hash-based integrity checks, source reputation scoring, anomaly detection on chunk content distributions.

---

## Part 5: Production Enterprise Code

```python
"""RAG query-plane resilience: retries, circuit breaker, fallbacks, logging.
Stdlib only. Wire real HTTP clients behind Retriever/Generator protocols.
"""
from __future__ import annotations

import hashlib, json, logging, random, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

# --- Structured logging (correlation id on every line) ---

def slog(level, msg, *, cid, tenant, **fields):
    logging.getLogger("rag").log(
        level, "%s %s", msg,
        json.dumps({"cid": cid, "tenant": tenant, **fields}, default=str),
    )

# --- Retries: exponential backoff + full jitter (AWS-style) ---

class TransientError(Exception): pass   # 429, 5xx, timeout
class PermanentError(Exception): pass   # 4xx auth, schema mismatch

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
                slog(logging.WARNING, "retry", cid=cid, tenant=tenant,
                     op=op, attempt=i+1, sleep_s=round(sleep, 3))
                time.sleep(sleep)
    raise last

# --- Circuit breaker: closed -> open -> half-open ---

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

# --- Retrieval + generation ports ---

@dataclass(frozen=True)
class Authz:
    tenant_id: str
    user_id: str
    acl_filter: dict   # pushed into every retriever arm

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

# --- Fallback chain: hybrid -> BM25 -> cache -> refuse ---

@dataclass
class LastGoodCache:
    ttl_s: float = 300.0
    _store: dict = field(default_factory=dict)

    def get(self, q, authz, k):
        key = hashlib.sha256(
            f"{authz.tenant_id}|{authz.user_id}|{k}|{q}".encode()
        ).hexdigest()
        rec = self._store.get(key)
        if rec and time.monotonic() - rec[0] <= self.ttl_s:
            return rec[1]
        return None

    def put(self, q, authz, k, chunks):
        key = hashlib.sha256(
            f"{authz.tenant_id}|{authz.user_id}|{k}|{q}".encode()
        ).hexdigest()
        self._store[key] = (time.monotonic(), chunks)

# --- RAG Runtime with full resilience ---

@dataclass
class DegradedResult:
    chunks: list[Chunk]
    answer: str
    retrieval_degraded: bool
    generation_degraded: bool
    citations: list[str]

class RagRuntime:
    def __init__(self, hybrid, bm25, primary_gen, secondary_gen,
                 cache=None, max_hops=3):
        self.hybrid = hybrid
        self.bm25 = bm25
        self.primary_gen = primary_gen
        self.secondary_gen = secondary_gen
        self.cache = cache or LastGoodCache()
        self.max_hops = max_hops
        self.breakers = {
            n: CircuitBreaker(n)
            for n in ["hybrid", "bm25", "primary_gen", "secondary_gen"]
        }

    def _call_retriever(self, r, query, authz, k, cid):
        br = self.breakers[r.name]
        def _op():
            br.allow()
            try:
                hits = r.search(query, authz, k)
            except PermanentError:
                br.record_failure(); raise
            except Exception as exc:
                br.record_failure()
                raise TransientError(str(exc)) from exc
            br.record_success()
            return hits
        return retry_with_jitter(_op, cid=cid, tenant=authz.tenant_id,
                                 op=f"retrieve:{r.name}")

    def retrieve(self, query, authz, k, cid):
        degraded = False
        for retriever in [self.hybrid, self.bm25]:
            try:
                hits = self._call_retriever(retriever, query, authz, k, cid)
                if hits:
                    self.cache.put(query, authz, k, hits)
                    return hits, degraded
                degraded = True
            except (TransientError, PermanentError):
                degraded = True
        cached = self.cache.get(query, authz, k)
        if cached:
            return cached, True
        return [], True   # caller must refuse

    def generate_grounded(self, chunks, question, cid, tenant):
        if not chunks:
            return ("Retrieval unavailable. Ungrounded generation disabled "
                    "for this corpus.", True)
        allowed = frozenset(c.chunk_id for c in chunks)
        # Lost-in-the-middle: highest-score chunks at the edges
        ranked = sorted(chunks, key=lambda c: c.score, reverse=True)
        edge = [ranked[0]] + ranked[2:] + ([ranked[1]] if len(ranked)>1 else [])
        ctx = "\n".join(f"[{c.chunk_id}] {c.text}" for c in edge)
        prompt = (f"Answer ONLY from the passages. "
                  f"Cite ids in {sorted(allowed)}.\n"
                  f"Passages:\n{ctx}\nQuestion: {question}")
        for gen in [self.primary_gen, self.secondary_gen]:
            br = self.breakers[gen.name]
            def _op():
                br.allow()
                try: text = gen.complete(prompt, allowed)
                except PermanentError: br.record_failure(); raise
                except Exception as e:
                    br.record_failure(); raise TransientError(str(e)) from e
                br.record_success(); return text
            try:
                return retry_with_jitter(
                    _op, cid=cid, tenant=tenant, op=f"gen:{gen.name}"), False
            except (TransientError, PermanentError):
                pass
        # Deterministic extractive fallback
        titles = ", ".join(c.chunk_id for c in ranked[:3])
        return (f"Generation unavailable. Top passages: {titles}. "
                "Insufficient evidence.", True)

    def answer(self, question, authz, k=8):
        cid = str(uuid.uuid4())
        chunks, retr_deg = self.retrieve(question, authz, k, cid)
        answer, gen_deg = self.generate_grounded(
            chunks, question, cid, authz.tenant_id)
        return DegradedResult(
            chunks, answer, retr_deg, gen_deg,
            [c.chunk_id for c in chunks])
```

---

## Part 6: Architectural System Design Scenarios

### Scenario A -- Multi-Tenant SaaS Knowledge Base (10-100M chunks)

**Problem.** B2B help-center copilot. Tenants upload manuals + SKU tables + error-code matrices. Requirements: tenant isolation (SOC 2), exact-ID queries (`TS-999`) **and** paraphrase, p95 chat < 2s, no GraphRAG (queries are factoid/FAQ, not corpus themes). Peak: size rerank RPM for hops x user QPS, not user QPS alone.

**Technology choices:** OpenAI `3-small` or Voyage-4-lite ($0.02/1M); hybrid with explicit alpha or RRF; Voyage/Cohere/bge rerank; generate `gpt-5.6-luna` or Haiku 4.5 + prompt cache; no Leiden/global.

| Axis | A1 Namespace/tenant + hybrid+rerank (recommended) | A2 Shared 100 GB index + metadata filter | A3 Bedrock Managed KB |
|---|---|---|---|
| **Cost** | RU ~ 1x namespace GB; Standard $50 min | Same query **100x RU** vs 1 GB tenant | $5/GB + $1/1k; $350/mo at 50 GB/100k |
| **Latency** | Predictable 2-stage; hop cap 1 | Filter+IVF risk: recall collapse at 50-90% filtered | Managed; hybrid falls back to semantic |
| **Security** | Query cannot cross namespace; omit-key is isolation | App-bug omits filter -> cross-tenant leak | Metadata filters; Guardrails not on source text |
| **Scalability** | 100k namespaces; DRN for noisy-neighbor reads | Recall->0 for rare tenants | 3 imports/region |

**Decision.** A1 wins: isolation is structural, RU math favors small hot namespaces, hybrid+rerank matches SKU+semantics.

### Scenario B -- Pharma/Legal Multi-Hop with Citation Spans

**Problem.** Clinical-ops / law-firm copilot: "compare trial X vs Y across protocols"; answers must carry `chunk_id+char_span`; 21 CFR 11-style audit; corpus in M365/SharePoint; **no** confidential query on the open web.

**Technology choices:** Azure document-level ACL; hybrid BM25+HNSW -> RRF -> Semantic Ranker top 50 + HippoRAG-style PPR; agent 2-hop loop with retry_count=2; CRAG Incorrect -> licensed corpus only, no web.

| Axis | B1 Hybrid + capped 2-hop + HippoRAG + Azure ACL (recommended) | B2 Full GraphRAG global | B3 Unbounded LangGraph + CRAG web |
|---|---|---|---|
| **Cost** | PPR 10-20x cheaper than IRCoT; HippoRAG 9M vs 115M tokens | Extract ~75% of index cost; global map-reduce >> $3.5/1k | Each hop multiplies all costs |
| **Latency** | p99 bounded by hop cap 2 | Global: worst; DRIFT 2 follow-ups still multi-pass | Fat tail; loops until timeout |
| **Security** | Query-time Entra ACL; no web exfil | Community reports can summarize restricted docs if ACL omitted | CRAG web = exfil path |

**Decision.** B1 wins for multi-hop without paying GraphRAG global map-reduce; citations are first-class; Azure ACL matches SharePoint provenance; web is forbidden by threat model.

---

## Common Failure Modes

| Failure | Cause | Detection | Mitigation |
|---|---|---|---|
| **Lost-in-the-middle** | U-shaped attention; answer in middle scored below closed-book (56.1%) | Faithfulness drop as k increases | Rerank to 5-20; put top chunks at edges |
| **Embedding drift** | Changed model id / dim / Matryoshka trim / metric | nDCG on frozen golden set | Pin schema; dual-write + shadow; alias flip after re-embed |
| **ACL leak** | Omitted metadata filter; $in >10k; hop without re-applying token; CRAG web; graph reports | Entitlement-violation canaries | Namespace isolation; QUORUM |
| **Authorized recall -> 0** | Post-filter-only ANN as forbidden set grows | Tenant-stratified recall | Bitmap/IVF bypass; namespace isolation |
| **Stale index** | CDC lag, failed upsert, alias not flipped, old graph_build_id | Watermark lag; source canaries | Alias swap; QUORUM; pin snapshot |
| **Hallucinated citations** | Model invents `[doc 17]` / URL; ALCE: ~50% lack complete support on ELI5 | ID not in retrieved set; NLI/IsSup | Constrained cites; refuse if empty retrieve |
| **Hybrid score collapse** | Pinecone sparse unbounded vs dense [-1,1]; client alpha != Weaviate default | Keyword-only or semantic-only in practice | `hybrid_score_norm`; set alpha explicitly; prefer RRF |
| **Filter+IVF collapse** | Naive IVF+filter at 50%/90% selectivity | Recall->0 for rare tenants | IVF bypass; adaptive scan fraction; namespaces |
| **Infinite agent loop** | No hop cap; grader false negatives | RPM/cost spike; timeout | `retry_count` max 3; wall-clock; `insufficient_evidence` |
| **Graph explosion** | LLM NER duplicates, co-occurrence cliques | Entity count vs doc count | Canonicalize; Fast/LazyGraphRAG; cap degree |
| **Rerank RPM/timeout** | 1k QPS x 80 docs vs Cohere 1000 RPM | Rerank error rate | Cache; lite/local bge; drop to fused top-8 |
| **Contextual PII spread** | Prepend copies secrets into every chunk | DLP on chunks | Redact before contextualize |
| **Poisoned ingest** | Unreviewed connector | sha256 + source allowlist | Quarantine; signed ingest |

---

## Interview Q&A

**Q1. Walk me through production RAG as if I have never seen a vector DB.**
I split ingest from query. Ingest parses, redacts PII, stamps ACL, chunks, embeds, and only then flips an alias. At query time I never let the LLM "search": I push the caller's ACL as a filter, run BM25 and dense in parallel, fuse (usually RRF), rerank 50-150 down to 5-20, and generate with citations drawn only from those IDs.

**Q2. Why hybrid? Why not just embeddings?**
Dense misses exact IDs (TS-999); BM25 misses paraphrase. Anthropic's production sketch is both lists then fusion. RRF with k=60 is scale-free -- rank 1 contributes about 0.0164 -- so I do not have to pretend BM25 and cosine share a numeric space.

**Q3. How do you stop tenant leaks?**
I do not put tenant_id in the tool JSON the model fills. Identity comes from the verified token. Prefer namespace-per-tenant so omitting a filter is an error, not a full scan. On a shared index I pre-filter; post-filter ANN fills top-k with forbidden neighbors and authorized recall goes to zero. Pinecone $in caps at 10,000 IDs -- I use groups, not a user-id dump.

**Q4. Give me a cost model for 1,000 questions.**
I state the mix: 50-token query embed, 80-chunk rerank, 4k generate in / 400 out, no retries. On that mix, 3-small embed is $0.001/1k, Voyage rerank-2.5 is ~$2.20/1k, luna generate ~$1.28/1k, total about $3.50/1k, excluding RUs. If I move generate to terra or Sonnet 5, generate alone is ~$12-13/1k and dominates. Pinecone RUs are 1 per GB of the namespace I actually query -- a 100 GB shared namespace is a 100x tax.

**Q5. Naive vs advanced vs agentic -- when do you pay for the loop?**
Naive is always-retrieve. Advanced is a DAG: hybrid+rerank+maybe HyDE. Agentic is retrieval-as-tool with a grader. I route with Adaptive-RAG: greetings skip retrieve; factoids stay 2-stage; multi-hop gets 2-3 hops; global questions get LazyGraphRAG, not Leiden-on-every-turn. Unbounded CRAG+web is an exfil path.

**Q6. GraphRAG in the architecture review -- do we need it?**
Only if eval shows global/multi-hop failure. Full GraphRAG extract is ~75% of index cost; the repo is maintenance-mode research. LazyGraphRAG indexes at vector-RAG cost (0.1% of full GraphRAG, Microsoft-stated) and budgets query-time relevance tests. I still keep a vector path -- HippoRAG 2 reports structure methods can drop 5-10 F1 on simple QA.

**Q7. Citations keep being invented. Fix?**
ALCE showed even strong models lack complete citation support about half the time on ELI5. RAGAS faithfulness catches unsupported claims, not fake IDs. I constrain decode to retrieved chunk_ids, NLI/IsSup gate, hash-check body vs ingest sha256, and refuse on empty retrieve. I also stop stuffing 20-50 unreranked chunks -- that is lost-in-the-middle plus hallucination fuel.

**Q8. How does LangGraph not lose a rewrite on crash?**
Production checkpointer is PostgresSaver. It snapshots each super-step; finished node writes in a failed super-step are durable and not recomputed. InMemorySaver is for tests. I store retry_count in state because the official agentic-RAG tutorial has no hop cap and will loop until runtime timeout.

**Q9. Filtered search quality collapsed for a small tenant. Why?**
Classic IVF+filter: Pinecone's paper shows recall collapse at 50% filtered and unusable results at 90%. Their fix is IVF bypass when the match set is small, plus adaptive scan fraction. Operationally I isolate that tenant into its own namespace so I am not probing a 100 GB slab for 0.1% selectivity.

**Q10. What SLO do you put in the contract?**
I do not quote a vendor RAG p99 -- nobody publishes one. I SLO retrieve+rerank separately from generate. I treat Pinecone's O(100 ms) as a design target, set a 200-500 ms retrieve timeout as policy, circuit-break the index independently of the FM, and cap agent hops because each hop is +1-3 LLM calls on the p95 tail.

**Q11. What is the biggest anti-pattern in interview answers?**
Describing RAG as only a vector search problem. Production RAG is retrieval engineering plus grounding discipline. It is two planes sharing versioned indexes, not retrieve() then generate().

**Q12. Zero-Trust MCP for retrieval -- what is the failure mode?**
An omnibus search(query, collection, tenant_id) where tenant_id is model-filled. MCP tools/call becomes a data-exfil API. I split tools by sensitivity, push predicates server-side, keep memory in the checkpointer not the MCP session, redact traces to the user's ACL.

---

## Key Numbers to Memorize

### Quality / Algorithms
| Number | What |
|---|---|
| **5.7% -> 1.9%** | Anthropic 1-recall@20: baseline -> contextual+BM25+rerank 150->20 (-67%) |
| **k = 60** | RRF default; rank 1 -> 1/61 ~ 0.0164 |
| **150->20 / top 50** | Anthropic rerank window; Azure Semantic Ranker |
| **0.7084 -> 0.8249** | Late chunking cosine, Berlin Wikipedia |
| **56.1%** | Lost-in-the-middle: GPT-3.5-Turbo with answer in middle below closed-book |
| **~50%** | ALCE: incomplete citation support on ELI5 |
| **0.95 vs 0.72** | RAGAS faithfulness vs direct GPT scoring vs humans |
| **+21 / +15 pts** | IRCoT retrieval / QA gains |
| **10-20x / 6-13x** | HippoRAG PPR cheaper / faster than IRCoT |
| **5-10 F1** | HippoRAG 2: structure methods can drop this on simple QA |
| **+17.4% / +39.7%** | Cross-encoder reranking Recall@5 / MRR@3 gains |
| **NDCG 0.7497** | Tuned hybrid RRF on WANDS; 7.5% above either retriever alone |

### Embeddings & Ingest $
| Number | What |
|---|---|
| **$0.02 / $0.13** | OpenAI 3-small / 3-large per 1M |
| **$0.02 / $0.06 / $0.12** | Voyage-4-lite / 4 / 4-large per 1M |
| **$20-$130** | 1B-token corpus embed range |
| **$1.02 / 1M** | Anthropic contextualize with prompt cache |
| **~200k tokens / ~500 pages** | Anthropic: skip RAG, cache the whole corpus |
| **32x** | Elastic BBQ compression vs full-precision |

### Rerank & Generate $
| Number | What |
|---|---|
| **~$2.00-2.50 / 1k** | Cohere/Pinecone rerank |
| **~$2.20 / 1k** | Voyage rerank-2.5 on 44k tok mix |
| **$1.00 / 1k** | Google Ranking API |
| **~$3.50 / 1k** | Inferred luna+Voyage rerank reference mix |
| **~$12-13 / 1k** | terra/Sonnet generate alone |

### Vector DB / Managed KB
| Number | What |
|---|---|
| **$50 / $500 min** | Pinecone Standard / Enterprise per month |
| **99.95%** | Pinecone Enterprise uptime SLA |
| **1 RU / GB namespace** | Query cost scales with namespace size |
| **100k namespaces** | Per index, both Standard and Enterprise |
| **$5/GB + $1/1k** | Bedrock KB storage + Standard Retrieve |
| **50,000+ shards/node** | Weaviate MT blog claim |

### Graph & Agent
| Number | What |
|---|---|
| **~75%** | GraphRAG indexing $ in LLM extraction |
| **0.1% / >700x** | LazyGraphRAG index vs full GraphRAG; Z100 query vs global |
| **3** | Common production max retrieve retries (official tutorial: none) |
| **~62%** | Agentic RAG hallucination reduction across 47 deployments |

---

## Quick Reference

- **Default stack:** Hybrid BM25+dense -> RRF (k=60) -> cross-encoder rerank 50-150 to 5-20 -> citation-aware generation
- **Chunk size:** 400-800 tokens, 10-20% overlap, sentence snap
- **ACL:** Pre-filter / namespace, not a prompt instruction. Post-filter ANN collapses authorized recall
- **Agent loops:** Cap retries at 3, forbid ungrounded generate on sensitive corpora, wall-clock timeout
- **Graph:** Use LazyGraphRAG for global questions; vector still wins single-hop; microsoft/graphrag is maintenance-mode
- **Skip RAG:** Under ~200k tokens if prompt-cache economics win
- **Budget:** Rerank + SUM(LLM loops), not embedding pennies. $3.50/1k on luna; $12-13/1k on terra/Sonnet
