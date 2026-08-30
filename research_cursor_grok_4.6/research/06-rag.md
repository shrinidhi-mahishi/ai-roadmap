# Research: RAG

**Date researched**: 2026-08-21
**Sources consulted**: 78

Scope: hybrid search (BM25 + dense, RRF, recency/metadata filters, chunking, embedding models), reranking (cross-encoders, Cohere / Voyage / bge-reranker, LLM-as-reranker, two-stage retrieval), Agentic RAG (query rewrite, multi-hop, Self-RAG, Corrective RAG, tool-using retrieval, iterative retrieve), Graph RAG (Microsoft GraphRAG, KGs, community summaries, hybrid graph+vector). Prices, rate limits, and compression ratios below are from vendor docs, papers, or named blogs as of 2026-08-21. ⚠️ No unpublished p50/p95/p99 RAG SLOs are invented; missing percentiles are marked. `$ per 1k queries` figures are **[inferred]** from published token/search-unit rates × a stated reference query, not a vendor SKU.

---

## 1. System Topology & Mechanics

### 1.1 Two planes: ingest vs query

A production RAG system is two independently scaled planes sharing **indexes**, not a single retrieve-then-generate function.

| Plane | Owns | Typical components | Failure if coupled |
| --- | --- | --- | --- |
| **Ingest (write)** | Parse, PII redaction, ACL stamp, chunk, contextualize, embed, sparse encode, graph extract, community reports, checkpoint | Connectors, workers, embedding/rerank batch APIs, HNSW/IVF build, Leiden clustering | Query p99 tracks reindex; a stuck extractor stalls answers |
| **Query (read)** | Authz filter, hybrid retrieve, fuse, rerank, agent loop, generate, cite | ANN + inverted index, RRF/RSF, cross-encoder, LangGraph/LlamaIndex loop, generator | Ingest schema change silently mismatches query embeddings |

Invariant (Lewis et al., NeurIPS 2020): the generator’s parametric memory is **not** the corpus. Non-parametric memory is an index (originally DPR over Wikipedia + BART). Modern systems add a **control loop** around that index (grade → rewrite → retrieve → generate → ground). The model never “searches”; it emits a tool call or a rewritten query; the retriever executes; chunks return as observations.

**Index types that coexist in one product RAG:**

1. **Dense ANN** — HNSW / IVF / BBQ-HNSW over embedding vectors (cosine or inner product).
2. **Sparse / lexical** — BM25 inverted index (Elasticsearch/OpenSearch/Weaviate), SPLADE or `pinecone-sparse-english-v0` sparse vectors, Postgres `tsvector` (not BM25), ParadeDB/`pg_search` true BM25.
3. **Metadata / ACL bitmap** — pre-filter before ANN (Pinecone slab metadata index → bitmap of eligible IDs; Weaviate/OpenSearch/ES filter clauses; pgvector `WHERE tenant_id = $1` with RLS).
4. **Graph** — entity/relationship tables + community reports + (optional) vector index over entities, text units, and reports.
5. **Rerank cache** — `(query_hash, doc_id, model, version) → score` with short TTL; not a recall index.

### 1.2 Hybrid search: BM25 + dense, fusion, vendor APIs

**Definition.** Run lexical and dense retrieval in parallel (or in one fused query), then merge into a single ranking. Hybrid exists because dense misses exact IDs (`TS-999`, SKUs, statute numbers) and BM25 misses paraphrase. Anthropic’s 2024 Contextual Retrieval write-up restates the same six-step sketch: chunk → TF-IDF + embeddings → BM25 top + dense top → rank fusion → top-K into the prompt.

**RRF (Cormack, Clarke, Buettcher, SIGIR 2009).** Rank-only fusion. For document \(d\):

\[
\mathrm{RRF}(d) = \sum_{r \in R} \frac{1}{k + \mathrm{rank}_r(d)}
\]

Default \(k = 60\) in Elasticsearch (`rank_constant`), OpenSearch (`rank_constant`), Weaviate `rankedFusion` (`1/(RANK+60)`), Qdrant RRF, and most client-side Postgres CTEs. Rank 1 contributes \(1/61 \approx 0.0164\); rank 60 contributes \(1/120 = 0.0083\). Documents present in **both** lists outrank documents that win only one list. RRF is scale-free: BM25 unbounded scores and cosine \([-1,1]\) never share a numeric space. Redis’s 2025/26 explainer notes the operational reason to prefer RRF: BM25 score distributions drift as the corpus grows; vector scores jump when the embedder changes; ranks stay comparable.

**Score fusion (when you *do* trust magnitudes).**

| Method | Who | Mechanism | When it wins |
| --- | --- | --- | --- |
| **Relative Score Fusion** | Weaviate default since **v1.24** | Min-max normalize each list to \([0,1]\), then \(\alpha\)-weighted sum | Score gaps carry signal (one BM25 hit far above the rest) |
| **Alpha convex combo** | Pinecone single-index hybrid; Weaviate `alpha` | `combined = α·dense + (1-α)·sparse` | Same index, same query; you can A/B α |
| **DBSF** | Qdrant | Normalize by mean/std of the **prefetch** top-k (3-σ remap; identical scores → 0.5) | Calibrated retrievers; outlier-sensitive |
| **min_max + arithmetic_mean** | OpenSearch `normalization-processor` (2.10+) | Score-space mix via search pipeline | You want explicit 0.3/0.7 weights |
| **Linear retriever** | Elasticsearch | Weighted normalized sum of child retrievers | Alternative to RRF when scores are comparable |

**Vendor topology (query path).**

**Weaviate.** Hybrid since **v1.17**. Parallel BM25/BM25F + vector, then fusion. `alpha`: 0 = keyword, 1 = vector, **server default 0.75** (dense-leaning) *if unset*; clients may send a different default — **set `alpha` explicitly**. `fusionType`: `relativeScoreFusion` (default ≥ v1.24) vs `rankedFusion`. Optional `max vector distance` gates the dense arm only; BM25 has no analogous threshold. BM25 knobs (`k1`, `b`, tokenization, property boosts, `and`/`or`/`and_cross`) apply inside hybrid.

**Pinecone (vector API).** Three patterns: (1) **single index** dense+sparse, `metric=dotproduct` only, server-side weighted dotproduct; (2) **two indexes** + client RRF; (3) **document schema** with FTS `string` (BM25) + `dense_vector`, combine via `$match_*` filter or client merge. **Production trap:** BM25 / `pinecone-sparse-english-v0` scores are **unbounded**; cosine dense is ~[-1,1]. Without `hybrid_score_norm` (scale dense by α, sparse by 1−α **on the query vectors**), sparse **dominates**. Pinecone starting α: 0.75 NL docs, 0.5 mixed, 0.25 SKU/ID-heavy. Single-index cannot do sparse-only queries or integrated embed+rerank; split indexes can.

**Elasticsearch.** Retrievers preview **8.14**, GA **8.16**. `rrf` wraps ≥2 children (`standard` BM25, `knn`, `sparse_vector`/ELSER, `semantic`). Defaults: `rank_constant=60`, `rank_window_size=10` (must be ≥ `size`). Elastic Stack **9.2+**: per-retriever `weight`. Nest `text_similarity_reranker` **outside** `rrf` for two-stage. Elastic’s 8.16 blog: RRF + retrievers GA **for Enterprise licensed customers**. BBQ: up to **32×** compression, **>95%** memory reduction vs full-precision (vendor-stated). `semantic_text` ingest chunking: word vs sentence, adjustable window.

**OpenSearch.** `hybrid` query + **search pipeline** (not in-query fusion). Max **5** subqueries. Pre-filter via `filter` on all arms. Processors: `normalization-processor` (2.10, min_max / arithmetic_mean + weights) or `score-ranker-processor` (2.19, RRF, default k=60). RRF `weights` in [0,1]. Hybrid rescore (2.18): **per subquery, per shard**, then coordinate-node fuse — `window_size` is per arm, not global. Hybrid cannot nest under `function_score` / `constant_score` / `script_score` / `boosting`. ≥3.5: `min_score` after fusion; >512 shards auto-disables batched reduction (higher coordinator RAM).

**Qdrant (≥1.10 Query API).** `prefetch[]` dense + sparse (BM25 FastEmbed or SPLADE), then `FusionQuery` `RRF` or `DBSF`. Global payload filters propagate to all prefetches; per-prefetch filters extra-constrain. Formula query: fuse first, then Gaussian/exponential **recency decay**, popularity, geo. Fusion as **top-level query** = global across shards; fusion **inside** prefetch = per-shard (wrong for multi-shard hybrid).

**pgvector + lexical.** One SQL round-trip: CTE dense (`<=>` + HNSW) + CTE `tsvector`/`websearch_to_tsquery` or ParadeDB BM25, `FULL OUTER JOIN`, `1/(k+rank)`. Honest naming: Postgres `ts_rank` / `ts_rank_cd` is **not BM25** (no corpus IDF). Under RRF, rank order is what matters. True BM25: ParadeDB `pg_search` or Tiger Data `pg_textsearch` (preview early 2026, v1.3.0 mid-2026 per Alonso). Practitioner ceiling **[inferred from ops blogs, not a pgvector SLA]**: a few million chunks on one primary before HNSW RAM + filtered-recall collapse; escalate to `pg_search`, Qdrant, or ES.

### 1.3 Recency and metadata filters

Filters are a **first-class retriever**, not a post-process.

- **Pinecone:** `$eq/$gt/$gte/$in/$and/$or`; `$in`/`$nin` max **10,000** values. ICML 2025: each slab has a metadata index → compressed **bitmap** of matching IDs; highly selective filters **bypass IVF** and scan the bitmap (recall preserved by design); scan fraction adapts to filter selectivity. Recency: store `updated_at` (unix) and `$gte` a cutoff, or two-stage (recent namespace + global).
- **Qdrant:** formula decay on a datetime payload **after** RRF, so lexical+dense recall is intact and time is a ranking feature, not a hard gate (unless you also filter).
- **OpenSearch/ES:** `filter` context (no score) on all hybrid arms — required for tenant + `effective_date` so neither BM25 nor kNN leaks stale/unauthorized hits.
- **Weaviate:** hybrid inherits BM25 property search; vector distance cap is dense-only.

**ACL vs recency:** apply **authorization as a hard pre-filter**; apply recency as either hard (`status=current`) or soft (decay). Soft recency without ACL still leaks.

### 1.4 Chunking strategies

Chunking is an ingest-plane compiler. Retrieval quality is often more sensitive to chunk policy than to embedding brand (repeated across Anthropic, LlamaIndex cookbooks, and 2026 multi-objective chunking evals).

| Strategy | Extra model calls | Helps | Hurts |
| --- | --- | --- | --- |
| Fixed token window + overlap | No | Simple, predictable vector count | Mid-sentence splits; orphaned pronouns |
| Sentence / structure-aware | No | Legal/markdown headings | Uneven sizes; huge tables |
| Semantic (embedding breakpoints) | Embed sentences | Topic shifts | Cost + unstable boundaries |
| Title/summary prepend | Summary: yes | Cheap lexical boost | Generic summary ≠ chunk-specific |
| **Contextual Retrieval** (Anthropic, 2024-09-19) | LLM per chunk; **prompt-cache the document** | BM25 **and** dense **and** reranker **and** generator see the same situated text | Ingest $ and latency |
| **Late chunking** (Jina, arXiv 2409.04701) | No extra LLM; long-context embedder | Dense vectors carry doc-level context via token-then-pool | Lexical index unchanged; needs long-context embedder |
| Parent-document / small-to-big | No | Retrieve small, generate on parent | Parent may exceed context; ACL must copy to both |

Anthropic **vendor numbers** (their eval, 1−recall@20, Gemini Text 004, top-20): baseline failed retrieval **5.7%** → contextual embeddings **3.7%** (−35%) → contextual embeddings+BM25 **2.9%** (−49%) → + Cohere rerank of top-150 down to 20: **1.9%** (−67%). They used ~800-token chunks, ~8k-token docs, ~50-token instructions, ~100-token contexts. **Prompt-cache cost they state: $1.02 per million document tokens** (one-time contextualize). They also state: KB **< ~200k tokens (~500 pages)** → skip RAG, cache the whole corpus in the prompt.

Late chunking: embed the **full document** (or max window), mean-pool **token** vectors per chunk. Jina reports nDCG@10 lifts vs naive chunking on several BEIR sets (e.g. SciFact, TREC-COVID — see paper tables; do not treat as your corpus). It does **not** inject company names into BM25.

**GraphRAG chunking** (Edge et al., arXiv 2404.16130): longer chunks → fewer extraction LLM calls (cheaper) but **lost-in-the-middle** recall of early-chunk entities. Extraction prompt design is a first-class cost/quality knob; FastGraphRAG later replaces LLM NER with spaCy/NLTK noun phrases.

Practical production default **[inferred]**: 400–800 tokens, 10–20% overlap, sentence snap, `doc_id`/`section`/`acl`/`version` on every chunk, parent pointer for generate-time expansion. Promote to contextual BM25 when eval shows pronoun/entity misses; promote to late chunking when you are dense-only and already on a 8k–32k embedder.

### 1.5 Embedding models (as of 2026-08-21)

Pin **model id + dimension + similarity metric + version** in the index schema. Changing any of them is a full re-embed.

| Model | Dim (native) | Context | Official / listed price | Notes |
| --- | --- | --- | --- | --- |
| OpenAI `text-embedding-3-small` | 1536 (Matryoshka-truncatable) | 8191 | **$0.02 / 1M tokens** ([OpenAI model page](https://developers.openai.com/api/docs/models/text-embedding-3-small)) | Default cost pick |
| OpenAI `text-embedding-3-large` | 3072 | 8191 | **$0.13 / 1M** (same family page) | Higher recall, 2× storage vs 1536 |
| Voyage `voyage-4-large` | (see Voyage docs; family is 1024-class historically) | 32k class | **$0.12 / 1M**; 200M free tokens/account | Current Voyage flagship embed |
| Voyage `voyage-4` / `voyage-4-lite` | | | **$0.06 / $0.02 / 1M**; 200M free | Quality vs cost ladder |
| Voyage `voyage-3-large` (older) | 1024 | 32k | **$0.18 / 1M** (no free tier) | Still in “older models” table |
| Cohere `embed-v4.0` | 256–3072 Matryoshka | 128k; text+image | **$0.12 / 1M text** on Bedrock/Azure/Cohere list aggregators; **$0.47 / 1M image** | Official Cohere.com page (fetched 2026-08-21) emphasizes **Model Vault** instance SKUs, not a simple API table — ⚠️ confirm dashboard |
| Pinecone Inference `llama-text-embed-v2` / `multilingual-e5-large` / `pinecone-sparse-english-v0` | | | **$0.16 / $0.08 / $0.08 per M tokens** Standard | Sparse is the hybrid lexical encoder |
| BAAI **BGE-M3** | 1024 dense + sparse weights + ColBERT multi-vector | 8192; 100+ languages | Self-host (569M params, ~2.27 GB) | One model, three retrieval modes (Chen et al., arXiv 2402.03216) |

MTEB/RTEB leaderboard deltas are **not** your nDCG. Voyage’s own rerank-2.5 blog (2025-08-11) evaluates rerankers on top of BM25, `text-embedding-3-large`, `voyage-3-large`, `voyage-3.5` with **100 candidates → NDCG@10**. Treat vendor nDCG as **vendor-stated**.

### 1.6 Reranking and two-stage retrieval

**Cross-encoder vs bi-encoder.** Bi-encoder (embed query, embed doc, ANN) is O(1) query encode + ANN. Cross-encoder jointly attends over `(query, document)` — one forward pass **per candidate**. That is why stage-1 is cheap recall (k=50–200) and stage-2 is expensive precision (keep 3–20 for the generator). Sentence-Transformers documents this split as the canonical cross-encoder pattern.

**Cohere Rerank.** Models in current docs: `rerank-v4.0-pro`, `rerank-v4.0-fast`, `rerank-v3.5`, `rerank-v3.0`. API: `POST` Rerank; `top_n`; `max_tokens_per_doc` default **4096**. v4.0 context **32,768**; query can consume up to half (truncate at 16,384). Hard cap: `num_documents * max_chunks_per_doc ≤ 10,000` (default max_chunks=1). Recommend ≤1,000 docs/request for quality. **Search unit (official FAQ):** 1 query + up to **100 documents**; if query+doc > **500 tokens**, auto-split; each chunk counts as a document toward the 100. Rate limits (official): Rerank **trial 10 req/min**, **production 1,000 req/min**; Embed 2,000 inputs/min. Model Vault (official page): Rerank 4 Fast Medium **$5.00/hr, $3,250/mo**; Rerank 4 Pro Medium same; Pro Large **$10.00/hr, $6,500/mo**. Pay-as-you-go per-search $ ⚠️ not on the public page fetched 2026-08-21; Bedrock listings commonly quote Rerank 3.5 at **$2.00 / 1,000 searches** (third-party/Bedrock, not Cohere.com HTML).

**Voyage Rerank.** Current: `rerank-2.5` / `rerank-2.5-lite`, **32k** context, instruction-following. Caps: ≤**1,000** docs; query+any doc ≤32k; total tokens `q_tokens×n_docs + sum(doc_tokens)` ≤ **600k**. Pricing (official, updated 2026-08-13): `rerank-2.5` **$0.05 / 1M tokens**, lite **$0.02 / 1M**; 200M free. Voyage’s own estimate: **~$0.0025/request** assuming 100 docs and 500 tokens per (query+doc). Vendor quality claim: on 93 datasets, `rerank-2.5` **+7.94%** NDCG@10 vs Cohere Rerank v3.5 averaged over four first-stage methods — **vendor-stated**.

**bge-reranker.** `BAAI/bge-reranker-v2-m3`: multilingual, long context, sequence-classification; serve via HF TEI `/rerank`. Pinecone Inference hosts it at **$2 / 1k requests** (Standard/Enterprise), 500 req/mo included on Starter. Same SKU price for `cohere-rerank-v3.5` and `pinecone-rerank-v0` on Pinecone.

**LLM-as-reranker.** Pointwise (yes/no or 0–1 per chunk), pairwise (which of two), listwise (reorder 10). Cost: a 70B/frontier judge over 50 chunks **dwarfs** a cross-encoder. Use for: (a) agentic **grade_documents** (binary, structured output, cheap model), (b) citation/faithfulness after generate, not as the primary 100-way ranker. Self-RAG (Asai et al., ICLR 2024) **trains** reflection tokens (`Retrieve`, `ISREL`, `ISSUP`, `ISUSE`) into the generator — that is not a drop-in API rerank.

**Two-stage topology (production default).**

```
query → [authz filter]
      → dense ANN (k=50–100) ∥ BM25/sparse (k=50–100)
      → RRF / RSF / α  → fused N≈50–150
      → cross-encoder top_n=5–20
      → generator (and optional citation check)
```

Anthropic used **150 → 20**. Elastic nests `text_similarity_reranker` over `rrf`. Qdrant: prefetch 20–100, fuse, optionally ColBERT/late-interaction as the final `query` instead of fusion. Never send pre-rerank noise to the generator if the reranker already dropped it (lost-in-the-middle + hallucinated citations).

### 1.7 Agentic RAG

Naive RAG: always retrieve top-k, always generate. Agentic RAG: **retrieval is a tool** with a bounded loop.

**LangGraph (official tutorial).** Nodes: `generate_query_or_respond` (model + `retriever_tool.bind_tools`) → retrieve → `grade_documents` (structured `GradeDocuments`) → conditional edge: `generate_answer` **or** `rewrite_question` → back to retrieve. Retrieval runs **only when the model requests it**. Grade-all-irrelevant → rewrite; else generate. This is the production approximation of Self-RAG + CRAG without fine-tuning reflection tokens.

**Self-RAG (Asai et al., arXiv 2310.11511 / ICLR 2024).** One LM trained to emit reflection tokens: whether to retrieve, whether passages are relevant, whether generation is supported, whether the answer is useful. Adaptive retrieval vs always-on. 7B/13B models beat always-retrieve Llama2-chat on open QA / fact verification **in the paper**. Production teams almost always **prompt** a separate grader rather than train tokens.

**Corrective RAG / CRAG (Yan et al., arXiv 2401.15884).** Retrieval evaluator → **Correct** (use internal docs) / **Incorrect** (web/external fallback) / **Ambiguous** (mix). Knowledge refinement strips noisy passages. Plug-in on RAG and on Self-RAG. Datasets in the paper: PopQA, Biography, PubHealth, Arc-Challenge. LangGraph cookbooks add a web-search node on the “all irrelevant” edge.

**Adaptive-RAG (Jeong et al., NAACL 2024).** Classifier on **question complexity** routes: no retrieval / single-shot retrieve / multi-hop iterative. Saves tokens on chitchat; spends them on multi-hop.

**LlamaIndex.** Query rewriting (multi-query → ensemble/fusion), sub-question generator (tools + decompose), `MultiStepQueryEngine` / `StepDecomposeQueryTransform` loop with stop when the rewrite is `"none"`, HyDE as a rewrite agent in front of `RetrieverQueryEngine`. Multi-hop is sequential: each sub-answer is `prev_reasoning` for the next retrieve.

**IRCoT / iterative retrieve.** Interleave chain-of-thought with retrieval (Trivedi et al.). HippoRAG paper compares against it: single-step PPR **10–20× cheaper, 6–13× faster** than iterative retrieve **in their experiments**.

**Tool-using retrieval.** Retriever, web search, SQL, KG traversal, and MCP `tools/call` are peers. The agent loop must cap: max retrieve retries (LangGraph examples use ~3), max hops, max tools/turn, wall-clock. Unbounded CRAG+web is an open proxy.

### 1.8 Graph RAG

**Problem GraphRAG actually solves.** Vector RAG fails **global** questions (“themes in this corpus”) because they are query-focused summarization, not top-k lookup (Edge et al., arXiv 2404.16130). Eval in the paper: datasets in the **~1M token** range; GraphRAG beats vector RAG on **comprehensiveness and diversity** of answers (LLM-as-judge, no gold global answers).

**Indexing pipeline (Microsoft docs + paper).**

1. Chunk source docs.
2. LLM extract entities, relationships, optional claims + descriptions.
3. Build KG; **Leiden** hierarchical communities.
4. Bottom-up **community reports** (LLM summaries).
5. Embed text units / entities / reports for local lookup.
6. Persist Parquet + vector store.

Microsoft **methods** page: standard GraphRAG — LLM extraction is ~**75% of indexing cost**. **FastGraphRAG**: NLP noun phrases + co-occurrence edges, reports from raw text units — cheaper, noisier graph, aimed at **global** questions. GitHub **microsoft/graphrag** (2026): research project, **maintenance mode**, no new features/PRs; bugfix/CVE only. Not an “officially supported Microsoft offering.”

**Query modes (official query overview).**

| Mode | Mechanism | Query class |
| --- | --- | --- |
| **Local** | Match entities → neighborhood + text chunks | “Healing properties of chamomile?” |
| **Global** | Map-reduce over **all** community reports | “Significant values of the herbs in this notebook?” |
| **DRIFT** | Primer: HyDE + top-K community reports → follow-up questions → local search iterations (default **2**) → hierarchical Q/A | Local questions that need global primer |
| **Basic** | Vanilla top-k vector RAG | Ablation / keywordless-poor queries |

Dynamic community selection (MSR blog): from the root, LLM-rate report relevance; prune irrelevant subtrees; only then map-reduce — cuts global-search cost vs scoring every report.

**LazyGraphRAG (MSR, 2024-11).** No LLM community summaries at index time. Indexing cost **identical to vector RAG** and **0.1% of full GraphRAG** (Microsoft-stated). Query: iterative deepening, relevance-test **budget** (e.g. Z100 / Z500). At vector-RAG-like query cost: beats local competitors on local queries; **>700× lower query cost** than GraphRAG global for comparable global quality (Microsoft-stated). At **4%** of GraphRAG global query cost (Z500): beats all compared methods on local+global in their study. Unified interface for local and global.

**LightRAG (Guo et al., arXiv 2410.05779, EMNLP 2025, HKUDS/LightRAG).** Dual-level retrieve (entity/low-level + relationship/high-level) + incremental graph updates. Designed to avoid full GraphRAG rebuilds. GitHub: local vs global modes analogous in *name* but cheaper dual-level retrieval, not Leiden map-reduce.

**HippoRAG (Gutiérrez et al., NeurIPS 2024, arXiv 2405.14831).** LLM + KG + **Personalized PageRank** (hippocampal indexing). Single-step multi-hop; paper: up to **~20%** over SOTA RAG on multi-hop QA; 10–20× cheaper / 6–13× faster than IRCoT. HippoRAG 2 (arXiv 2502.14802, ICML 2025): continual non-parametric memory.

**Hybrid graph+vector is the real production shape.** GraphRAG, LightRAG, and HippoRAG all still embed something (entities, text units, reports). An agent router that picks `vector_tool` vs `graph_local` vs `graph_global` per query is how enterprises avoid paying global map-reduce for “what’s the refund SLA?”.

Systematic 2025 eval (arXiv 2502.11371v3): community GraphRAG helps multi-hop/summarization; vector RAG often wins **single-hop**; extraction noise is a first-class error. GraphRAG-Bench (arXiv 2506.02404): indexing time ordering includes HippoRAG longest (entity↔rel↔chunk maps); not all graph methods beat a strong GPT-4o-mini baseline — over-structure can **hurt**.

---

## 2. Token Economics & NFR Metrics

### 2.1 Latency budget (no fake percentiles)

⚠️ Public vendor pages do **not** publish p50/p95/p99 for “RAG e2e.” Decompose:

| Stage | What dominates | Order-of-magnitude (published or labeled) |
| --- | --- | --- |
| Query embed | Small transformer / API | Tens of ms local; 50–200 ms hosted RTT **[inferred]** |
| Hybrid retrieve | ANN + inverted + fuse | pgvector HNSW blogs: **ef_search 40 → ~8 ms p95**; 100 → ~14 ms; 200 → ~26 ms; 400 → ~51 ms on their 10M-row bench (CallSphere, **not** your hardware) |
| Cross-encoder rerank | N joint encodes + network | Voyage estimates token-based; third-party Cohere v3.5 RTT often cited ~**600 ms** ⚠️ not Cohere SLA |
| Agent extra hop | Grade + rewrite + 2nd retrieve | **+1–3 LLM calls**; multiplies p95 if unbounded |
| Generate | Prompt = instr+chunks | Usually **>50%** of e2e $ and often of e2e latency |
| Graph global | Map over many community reports | Worst; LazyGraphRAG exists to kill this |

SLO design: set **p99 retrieve+rerank** separate from **p99 generate**. Circuit-break the vector DB independently of the LLM provider.

### 2.2 Reference query for `$ / 1k queries` **[inferred]**

Assumptions (state these in a design review; do not treat as a quote):

- 1k user questions, **no** agent retries.
- Query embed 50 tokens; retrieve 80 fused chunks; rerank 80; keep 8 chunks × 500 tokens = 4k context; generate 4k input + 400 output.
- Dense embedder: OpenAI `text-embedding-3-small` **$0.02/1M**.
- Rerank: Voyage `rerank-2.5` formula `50×80 + 80×500 = 44,000 tokens/query` × $0.05/1M = **$0.0022/query**.
- Generate: use a cheap chat SKU only as a placeholder — e.g. if input $0.15/1M and output $0.60/1M (illustrative of a mini-tier, **verify live**): 4k in × $0.00015 + 400 out × $0.00024 = **$0.00084/query**.

**[inferred] per 1k queries:** embed $0.001 + rerank **$2.20** + generate **$0.84** ≈ **$3.0** **excluding** vector DB RUs, graph map-reduce, and retries. **Rerank dominates this mix** once generation is mini-tier; **generation dominates** if you use a frontier model ($3–15/1M out).

**Cohere search-unit path [inferred]:** 1k queries × 1 search unit (≤100 docs) . If Bedrock Rerank 3.5 is $2.00/1k searches → **$2.00/1k queries** plus embed plus generate. If any doc+query >500 tokens, units inflate (official split rule).

**Pinecone Database RUs (official):** Standard **$16–$18 per million RUs** (region-dependent), storage **$0.33/GB/mo**, write units **$4–$4.50/M**, **$50/mo minimum**. Query cost scales with **namespace size** (docs: 1 GB namespace → 1 RU per query; 100 GB single namespace with metadata filter still scans 100 GB). Dedicated Read Nodes: provisioned, no shared read rate limits. Egress **$0.10/GB** after 100 GB. Inference rerank **$2/1k requests**.

**Voyage embed [official]:** 1k queries × 50 tokens = 50k tokens → negligible vs index-time embed of the corpus.

**Anthropic contextualize [official]:** **$1.02 / 1M document tokens** one-time with prompt cache (their 800/8k/50/100 token assumption). 100M-token corpus → **~$102** ingest LLM **before** embeddings.

**GraphRAG index:** Microsoft: extraction ~**75%** of index $ . LazyGraphRAG: index $ ≈ vector RAG, **0.1%** of full GraphRAG (Microsoft). LightRAG incremental updates avoid full Leiden rebuild (design claim). ⚠️ Dollar cliffs like “$33k” on blogs are **scenario calculators**, not a list price — recompute from your chunk count × extract prompt × model tariff.

### 2.3 Caching and RPM

| Cache | Key | Hit saves |
| --- | --- | --- |
| Embedding cache | `(model, dim, text_hash)` | Ingest re-runs; identical query embed |
| Retriever cache | `(index_version, filter, query_hash, k)` | Duplicate questions |
| Rerank cache | `(reranker, query, doc_id)` | Agent retries |
| Prompt cache | Document prefix (Anthropic) | Contextualize + generate |
| Community report | Static until reindex | Global search map |

RPM: Cohere Rerank prod **1,000 req/min**; trial **10**. Voyage/OpenAI embed RPM ⚠️ org-specific dashboards. Pinecone serverless: RU/WU quotas by plan; DRN removes noisy-neighbor read limits. Agent loops: 3 retrieves × 1k QPS = 3k retrieve RPM — size the vector DB and reranker **for the loop, not the user QPS**.

Batch: Voyage Batch API **33% off**, **no** free-token credit, 12h window. OpenAI embeddings Batch commonly **50% off** on the 3-small/3-large pair (confirm live pricing page).

---

## 3. Distributed Resilience & State

### 3.1 Index replication and consistency

**Weaviate.** Cluster **metadata** (collection schema, tenant activity): **Raft**. **Data objects:** leaderless, tunable `ONE` / `QUORUM` (default) / `ALL` on read and write. QUORUM = n/2+1 (RF=6 → 4). Historical note in docs: v1.17 writes were `ALL`. Hybrid search under `ONE` can return a replica missing the latest upsert → **stale chunk in RAG**. Use `QUORUM` for RAG corpora that must not cite deleted docs.

**Pinecone serverless.** Object-storage-backed; you do not set RF. Consistency is **eventual** at the product surface (upsert then immediately query can miss). Namespaces are the isolation/scale unit (Standard/Enterprise: **100,000 namespaces/index**). Backups **$0.10/GB/mo**, restore **$0.15/GB**. Enterprise **99.95%** uptime SLA; Starter/Builder/Standard: no uptime SLA on the public table.

**Elasticsearch / OpenSearch.** Primary + replica shards; hybrid fusion on the coordinating node after shard-local subquery scores. OpenSearch hybrid + huge shard counts: coordinator memory; 3.5+ disables batched reduction >512 shards. Replica lag = BM25 and kNN seeing different live sets — **same query, two ranks**.

**pgvector.** Postgres WAL + streaming replicas. HNSW build is heavy; **build after bulk load**. Concurrent writes vs HNSW: follow pgvector release notes; many teams ingest to a staging table and `REINDEX` / swap. RLS + replica: filters must exist on standby too.

**GraphRAG artifacts.** Parquet communities + vector store. A crash mid-Leiden leaves entities without reports. Treat index as a **versioned snapshot** (`graph_build_id`); query plane pins a complete snapshot. Microsoft CLI: `graphrag init --force` between minor versions; major bumps need migration notebook or full reindex.

### 3.2 Checkpointed ingest

Idempotent pipeline:

1. Source watermark (S3 etag / Drive revision / DB CDC LSN).
2. Raw blob + sha256 (poisoning detection).
3. Parse/chunk with `chunk_id = hash(doc_id, chunker_version, text)`.
4. Embed job keyed by `embed_model + dim + chunk_id`.
5. Upsert vectors with `index_version`; only then flip the query alias.
6. Graph extract: per-chunk checkpoint; community detect **only** on a closed chunk set; reports last.

CRAG/web and agent retries must not write into the **corpus** index without a human/quarantine path (ingestion vector).

### 3.3 Circuit breakers on the vector DB

Treat ANN like a downstream HTTP dep:

- **Timeout** (e.g. 200–500 ms retrieve) **[policy, not a vendor SLO]**.
- **Error-rate breaker** (5xx, resource_exhausted, RU throttle).
- **Bulkhead** separate from LLM pool — a Pinecone RU storm must not starve generate.
- **Fallback chain:** (1) last-good retrieve cache, (2) BM25-only, (3) “index unavailable” refusal — **never** generate ungrounded if policy forbids.
- **Hedging:** duplicate retrieve to a replica/region on p99; cancel loser.
- Agent: on retrieve failure, **do not** infinite rewrite; surface `retrieval_degraded`.

OpenSearch/ES: coordinating-node OOM on hybrid+huge `rank_window_size` — cap window (ES default 10 is conservative; raising to 50–100 is a cost/latency choice).

---

## 4. Enterprise Security & Governance

### 4.1 Zero-Trust MCP for retrievers

MCP `tools/call` on a retriever is a **data exfil API**. Rules that survive a Principal Architect review:

1. **Server-side identity.** Tenant/ACL from the verified token / `RunContext`, never from tool arguments the model filled (`tenant_id` in JSON schema is a leak primitive). arXiv 2605.05287: ABAC before search; chunk filter after; **predicate pushdown** (pgvector/Qdrant/Milvus) so ANN never ranks cross-tenant rows. Post-filter-only backends lose recall as corpus grows (top-k filled with forbidden hits).
2. **Least privilege per tool.** Separate MCP servers: `retrieve_public_kb` vs `retrieve_hr` vs `sql_customer`. Agent Gateway / allowlists; no omnibus `search(query, collection)`.
3. **Stateless MCP + stateful RAG.** LangGraph `/mcp` is stateless per request; conversation memory stays in the checkpointer, **not** the MCP session.
4. **No raw chunk echo to unauthorized traces.** LangSmith/OTel must redact document text at the same ACL as the user.
5. **Hosted MCP** (OpenAI HostedMCP, etc.): the provider’s network path sees queries; contract for data residency.

### 4.2 Document ACL

Oracle’s enterprise RAG checklist: **policy travels with evidence**. Stamp at ingest: owner, tenant, role, classification, delete-state, source version. Enforce as **mandatory query predicates**, not prompt text (“ignore docs you shouldn’t see” is not a control).

**Isolation ladder:**

| Pattern | Guarantee | Cost |
| --- | --- | --- |
| Metadata `tenant_id` filter | App-bug can omit filter | Cheapest; Pinecone: scans **full namespace** |
| **Namespace / collection / index per tenant** | Query cannot cross (Pinecone: 1 GB tenant = 1 RU; 100×1 GB cheaper than 100 GB filter) | More indexes; Pinecone Standard 20 indexes/project, 100k namespaces |
| Instance / BYOC per tenant | Strongest (HIPAA/finance) | Pinecone BYOC: zero inbound SSH; PrivateLink |

Pinecone anti-pattern: `$in` of tens of thousands of user IDs (10k cap). Use groups, namespaces, or post-filter **after** a group-scoped retrieve.

Delete/tombstone: vector delete must match source ACL revocation; **eventual consistency** windows are a compliance bug (Weaviate `ONE` reads).

### 4.3 PII in chunks

Vectors are **derived personal data**. Contextual Retrieval **prepends** more PII (names, quarters, revenue) into every chunk — better retrieval, larger blast radius. Controls: ingest redaction **before** embed; deterministic + ML DLP **after** retrieve, **before** prompt; never log rerank documents at full text in shared SaaS traces. Embed APIs (OpenAI/Voyage/Cohere) see plaintext — DPA, zero-retention, or self-host BGE-M3.

Graph extraction **amplifies** PII into entity nodes (“Jane Doe — SSN context”). Community reports can **summarize secrets** into a high-level node that global search then retrieves for everyone with graph access — ACL on **reports**, not just raw chunks.

### 4.4 Audit of citations

Provenance fields: `source_uri`, `version`, `chunk_id`, `char_span`, `retriever` (bm25|dense|graph_local|web), `rerank_score`, `user_id`, `tenant`, `index_build_id`. Gao et al. ALCE (2023) framed citation as its own eval; production metric: **provenance fidelity** = fraction of cited IDs that (a) were in the retrieved set, (b) support the claim (NLI/faithfulness), (c) the user was entitled to see.

Hallucinated citations: the model invents `[doc 17]` or a URL. Mitigations: constrained decode / tool-only citations from retrieved IDs; refuse if grader `ISSUP=no` (Self-RAG token or LLM judge); hash-verify chunk body vs ingest sha256 (Raji architecture notes).

OWASP LLM: vector/embedding weaknesses, poisoned ingest, cross-tenant namespace bugs. NIST SP 800-162 mapping (Secure RAG paper, doi 10.52710/cfs.976): PEP at the vector query boundary; PDP for ABAC; redaction gate; citation validity gate. Measure **leakage rate**, **entitlement violation rate**, **provenance fidelity**, **false refusal**.

---

## 5. Production Failure Modes

| Failure | Mechanism | Blast radius | Detect | Mitigate |
| --- | --- | --- | --- | --- |
| **Stale indexes** | CDC lag, failed upsert, alias not flipped, replica `ONE` | Answers from deleted/old policy | Watermark lag, sample-query canaries against source | Alias swap; QUORUM; ingest checkpoints |
| **Embedding drift** | New model/dim/prompt, Matryoshka trim change, undocumented API snapshot | Silent recall collapse | nDCG on frozen golden set after every embed bump | Pin model; dual-write + shadow eval; full re-embed |
| **Score-scale hybrid** | Pinecone sparse unbounded vs dense [-1,1]; Weaviate client α≠0.75 | Keyword-only or semantic-only in practice | Offline A/B α; debug score components | `hybrid_score_norm`; RRF; set α explicitly |
| **Filter/ANN interaction** | Metadata filter + IVF; post-filter ACL | Recall → 0 for rare tenants | Recall@k per tenant | Pinecone bitmap/IVF bypass; pushdown ACL; namespaces |
| **Over-retrieval** | k=50 into 128k context; agent 4 hops | Lost-in-the-middle, $ explosion, distraction | Context tokens/query histogram | Rerank to 5–20; Adaptive-RAG router; hop cap |
| **Hallucinated citations** | Generate without grounding check | Legal/compliance incident | Citation ID ∉ retrieved set | ID-constrained cites; CRAG/Self-RAG grade; refuse |
| **Grader false negative** | LLM says “irrelevant” on good docs | Rewrite loop; web leak of confidential query | Loop-depth metrics | Max 3; fallback “insufficient evidence” |
| **Grader false positive** | Noise marked relevant | Grounded-looking hallucination | Faithfulness eval | Reranker + NLI; don’t trust binary grade alone |
| **Graph explosion** | LLM NER duplicates, co-occurrence cliques, no entity resolution | Index $ 10×; global search timeout | Entity count vs doc count | Canonicalize; Fast/LazyGraphRAG; cap degree |
| **Community staleness** | New docs, old Leiden cut | Global answers miss this week | `graph_build_id` age | Incremental LightRAG or scheduled rebuild |
| **Poisoned ingest** | Unreviewed connector | Persistent retrieval hijack | sha256 + source allowlist | Quarantine; signed ingest; re-embed audits |
| **Rerank RPM/timeout** | 1k QPS × 80 docs | p99 blowup | Rerank error rate | Cache; lite model; local bge; drop to fused top-8 |
| **Contextual PII spread** | Context prepend copies secrets | Broader ACL miss | DLP on chunks | Redact before contextualize |
| **OpenSearch hybrid nest** | `function_score(hybrid)` | Silent wrong scores / error | Query lint | Hybrid as top-level only |
| **Qdrant per-shard fusion** | Fusion inside prefetch | Wrong global rank | Multi-shard A/B | Fusion as main query |
| **ES rank_window_size=10** | Default too small for recall | Reranker never sees the right doc | Recall@50 vs @10 | Raise window; pay latency |
| **Maintenance-mode GraphRAG** | CVE/deps only | You fork forever | GitHub status | Treat as algorithm, not product |

---

## 6. Enterprise System Design Scenarios

### 6.1 Trade-off matrix: hybrid vs graph vs agentic

| Axis | Hybrid + rerank (default) | Agentic (LangGraph/LlamaIndex) | GraphRAG-class |
| --- | --- | --- | --- |
| **Best query class** | Factoid, FAQ, SKU+semantics | Ambiguous, multi-hop, “should I retrieve?” | Global themes, corpus sensemaking |
| **Index $** | Embed + BM25 | Same + maybe extra rewrites stored | LLM extract + communities (**75%** extract) or Lazy **~vector** |
| **Query $** | 1 embed + 1 hybrid + 1 rerank + 1 generate | ×(1+retries) LLM + retrieve | Local ≈ hybrid; **global map-reduce ≫**; Lazy budgeted |
| **p99** | Predictable 2-stage | Fat tail (loops) | Global: worst; DRIFT: multi-pass |
| **Ops** | Two indexes or one hybrid engine | Checkpointer, loop caps, traces | Graph snapshot versioning |
| **Security** | Filter/namespace | Tool isolation + same filters on every hop | ACL on nodes **and** reports |
| **Failure** | Score mix, stale ANN | Infinite rewrite, web exfil | Graph explosion, stale communities |
| **When to choose** | 80% of enterprise KB chat | Support/research copilot | Exec “what changed this quarter across 10k docs” |

**Do not** run GraphRAG global on every turn. Router (Adaptive-RAG classifier or cheap LLM): `chitchat` → no retrieve; `factoid` → hybrid+rerank; `multi-hop` → agent 2–3 hops; `global` → LazyGraphRAG or scheduled community reports.

### 6.2 Scenario A — Multi-tenant SaaS knowledge base (10–100M chunks)

**Requirements:** tenant isolation, SKU/error-code queries, p95 chat < few seconds, SOC2.

**Design:** namespace-per-tenant (Pinecone) or RLS+HNSW (pgvector) until ~few million chunks/tenant; hybrid BM25+dense; Cohere/Voyage/bge rerank N=80→8; **no** GraphRAG. Contextual BM25 if eval shows orphan figures. ACL pre-filter only.

**Economics [inferred]:** Pinecone RUs dominated by namespace GB; keep hot tenants small. Rerank ~$2/1k if 1 search-unit/query. Reject metadata-filter-only 100 GB shared index (100× RU).

### 6.3 Scenario B — Pharma / legal multi-hop

**Requirements:** “compare trial X vs Y across protocols”; citation spans; 21 CFR 11-style audit.

**Design:** hybrid retrieve + **HippoRAG-style PPR** or agent 2-hop with IRCoT cap; graph edges from **controlled** NER (ontology), not unconstrained LLM entities. Citations = `chunk_id+offsets` only. CRAG **without** open web (or web into a licensed corpus). Human review on new graph edges.

**Avoid:** full Leiden global search; entity explosion; LLM-as-only-reranker on 200 chunks.

### 6.4 Scenario C — Enterprise “what happened this quarter?”

**Requirements:** global QFS, weekly document flux.

**Design:** LazyGraphRAG or FastGraphRAG + vector hybrid for local; **do not** re-Leiden daily on GPT-4-class extract. If Microsoft GraphRAG OSS is used, pin a snapshot weekly; serve DRIFT for mid-range questions. LightRAG if incremental entity updates matter more than community reports.

**Cost control:** Microsoft Lazy: index ~ vector; global quality at **≪** full map-reduce (700× query $ claim is **Microsoft-stated** on their mix). Always re-run **your** LLM-as-judge on **your** corpus.

### 6.5 Scenario D — Cost-capped internal GPT

**Design:** OpenAI 3-small or Voyage-4-lite embed; Postgres hybrid RRF; self-host `bge-reranker-v2-m3` on TEI; Adaptive-RAG skip retrieve on greetings; generate with mini-tier; prompt-cache system+tool schemas. Agent max 1 rewrite. Graph: none.

**[inferred] $ / 1k:** well under $1 if generate is mini and rerank is self-hosted; **your** GPU/RAM is the rerank bill.

### 6.6 Decision checklist (interview-ready)

1. **Recall first:** hybrid (RRF or explicit α) beats dense-only on IDs; prove with a labeled set, not MTEB.
2. **Precision second:** cross-encoder 50–150 → 5–20; LLM grade is a **router**, not a 100-way ranker.
3. **Loop third:** cap hops; retrieval as tool; CRAG fallback only to **approved** corpora.
4. **Graph last:** only if eval shows global/multi-hop failure; prefer Lazy/HippoRAG/LightRAG over naive full GraphRAG; Microsoft OSS is maintenance-mode research.
5. **Security always:** ACL pushdown, namespace isolation, citation IDs, PII-before-embed, MCP tools without client-supplied tenant.
6. **NFR:** budget $ as embed + RU + rerank + Σ LLM loops; SLO retrieve vs generate separately; breakers on the index.

---

## Sources

1. https://proceedings.nips.cc/paper_files/paper/2020/file/6b493230205f780e1bc26945df7481e5-Paper.pdf — Lewis et al., RAG, NeurIPS 2020
2. https://dl.acm.org/doi/10.1145/1571941.1572114 — Cormack et al., RRF, SIGIR 2009
3. https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf — RRF PDF
4. https://redis.io/blog/reciprocal-rank-fusion/ — RRF operational explainer
5. https://weaviate.io/blog/hybrid-search-explained — Weaviate hybrid intro
6. https://docs.weaviate.io/weaviate/concepts/search/hybrid-search — alpha, RSF vs rankedFusion
7. https://docs.weaviate.io/weaviate/concepts/replication-architecture/consistency — Raft vs ONE/QUORUM/ALL
8. https://docs.pinecone.io/guides/search/hybrid-search — dense+sparse patterns, hybrid_score_norm
9. https://docs.pinecone.io/guides/search/filter-by-metadata — filter operators, 10k $in
10. https://docs.pinecone.io/guides/index-data/implement-multitenancy — namespaces vs metadata
11. https://docs.pinecone.io/guides/index-data/data-modeling — schema, namespace RU math
12. https://www.pinecone.io/pricing/ — RU/WU/storage, Inference rerank $2/1k, SLAs
13. https://www.pinecone.io/research/ICML_2025.pdf — metadata bitmaps, IVF bypass
14. https://www.elastic.co/docs/reference/elasticsearch/rest-apis/retrievers/rrf-retriever — rank_constant 60, weights 9.2
15. https://www.elastic.co/docs/solutions/search/hybrid-search — recommended RRF hybrid
16. https://www.elastic.co/docs/solutions/search/retrievers-overview — retrievers 8.14/8.16
17. https://www.elastic.co/blog/whats-new-elastic-search-8-16-0 — BBQ 32×, RRF GA Enterprise, nested rerank
18. https://www.elastic.co/search-labs/blog/elasticsearch-retrievers-ga-8-16-0 — nested rrf + reranker
19. https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/index/ — pipelines
20. https://docs.opensearch.org/latest/query-dsl/compound/hybrid/ — max 5 clauses, rescore, limits
21. https://docs.opensearch.org/latest/search-plugins/search-pipelines/score-ranker-processor/ — RRF k=60
22. https://docs.opensearch.org/latest/search-plugins/search-pipelines/normalization-processor/ — min_max fusion
23. https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/ — 2.19 RRF
24. https://qdrant.tech/documentation/search/hybrid-queries/ — RRF, DBSF, formula, shard fusion
25. https://qdrant.tech/course/essentials/day-3/hybrid-search/ — Query API hybrid
26. https://github.com/pgvector/pgvector — pgvector
27. https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual — ParadeDB BM25 + pgvector RRF
28. https://www.pedroalonso.net/blog/postgres-bm25-search/ — pg_textsearch BM25 2026
29. https://docs.voyageai.com/docs/reranker — rerank-2.5 caps and API
30. https://docs.voyageai.com/docs/pricing — embed + rerank token prices (updated 2026-08-13)
31. https://blog.voyageai.com/2025/08/11/rerank-2-5/ — vendor NDCG vs Cohere v3.5
32. https://docs.cohere.com/docs/reranking-quickstart.mdx — rerank-v4.0-pro
33. https://docs.cohere.com/docs/reranking-best-practices.mdx — 10k doc cap, 32k context
34. https://docs.cohere.com/docs/rate-limits.mdx — Rerank 10 / 1000 RPM
35. https://docs.cohere.com/reference/rerank.mdx — API fields
36. https://docs.cohere.com/docs/how-does-cohere-pricing-work — search vs token billing
37. https://cohere.com/pricing — Model Vault SKUs, search-unit definition
38. https://huggingface.co/BAAI/bge-reranker-v2-m3 — OSS reranker
39. https://huggingface.co/BAAI/bge-m3 — BGE-M3 1024d / 8192 / 100+ langs
40. https://bge-model.com/bge/bge_m3.html — dense+sparse+ColBERT
41. https://arxiv.org/abs/2402.03216 — BGE-M3 paper
42. https://www.sbert.net/examples/applications/cross-encoder/README.html — cross-encoder pattern
43. https://www.anthropic.com/news/contextual-retrieval — 35/49/67% failure drops, $1.02/M
44. https://arxiv.org/abs/2409.04701 — Late Chunking
45. https://github.com/jina-ai/late-chunking — implementation + BEIR tables
46. https://developers.openai.com/api/docs/models/text-embedding-3-small — $0.02/1M
47. https://arxiv.org/abs/2310.11511 — Self-RAG
48. https://proceedings.iclr.cc/paper_files/paper/2024/file/25f7be9694d7b32d5cc670927b8091e1-Paper-Conference.pdf — Self-RAG ICLR
49. https://arxiv.org/abs/2401.15884 — CRAG
50. https://arxiv.org/html/2401.15884v3 — CRAG HTML
51. https://docs.langchain.com/oss/python/langgraph/agentic-rag — retrieve as tool, grade, rewrite
52. https://www.langchain.com/blog/agentic-rag-with-langgraph — Self-RAG/CRAG cookbooks
53. https://developers.llamaindex.ai/python/examples/query_transformations/query_transform_cookbook/ — rewrite, sub-questions
54. https://developers.llamaindex.ai/python/examples/workflow/multi_step_query_engine/ — multi-hop loop
55. https://aclanthology.org/2024.naacl-long.389/ — Adaptive-RAG
56. https://arxiv.org/abs/2404.16130 — From Local to Global GraphRAG
57. https://www.microsoft.com/en-us/research/publication/from-local-to-global-a-graph-rag-approach-to-query-focused-summarization/
58. https://github.com/microsoft/GraphRAG — maintenance mode
59. https://microsoft.github.io/graphrag/ — process overview
60. https://microsoft.github.io/graphrag/index/overview/ — indexing outputs
61. https://microsoft.github.io/graphrag/index/methods/ — 75% extract cost, FastGraphRAG
62. https://microsoft.github.io/graphrag/query/overview/ — local/global/DRIFT/basic
63. https://microsoft.github.io/graphrag/query/drift_search/ — DRIFT phases
64. https://www.microsoft.com/en-us/research/blog/introducing-drift-search-combining-global-and-local-search-methods-to-improve-quality-and-efficiency/
65. https://www.microsoft.com/en-us/research/blog/graphrag-improving-global-search-via-dynamic-community-selection/
66. https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/ — 0.1% index, 700× query
67. https://arxiv.org/abs/2410.05779 — LightRAG
68. https://github.com/HKUDS/LightRAG/ — dual-level modes
69. https://arxiv.org/abs/2405.14831 — HippoRAG
70. https://github.com/OSU-NLP-Group/HippoRAG — NeurIPS’24 / HippoRAG 2
71. https://arxiv.org/abs/2502.14802 — HippoRAG 2
72. https://arxiv.org/abs/2502.11371 — RAG vs GraphRAG systematic eval
73. https://arxiv.org/pdf/2506.02404 — GraphRAG-Bench
74. https://neo4j.com/blog/developer/drift-search-with-neo4j-and-llamaindex/ — DRIFT + LlamaIndex
75. https://blogs.oracle.com/developers/secure-enterprise-rag-acls-tenant-filters-provenance-and-oracle-deep-data-security
76. https://arxiv.org/html/2605.05287 — multitenant retrieval, ABAC, MCP-adjacent agent control
77. https://doi.org/10.52710/cfs.976 — Secure RAG PEP/PDP, provenance metrics
78. https://arxiv.org/html/2608.16586v1 — 2026 chunking multi-objective eval

**Coverage confirmation:** hybrid search (BM25+dense, RRF/RSF/DBSF/α, recency/metadata, chunking, embeddings) §1.2–1.5, §2, §5; reranking (cross-encoders, Cohere/Voyage/bge, LLM-as-reranker, two-stage) §1.6, §2.2; Agentic RAG (rewrite, multi-hop, Self-RAG, CRAG, tools, iterative) §1.7; Graph RAG (Microsoft, KG, communities, hybrid graph+vector, Lazy/Light/Hippo) §1.8, §6. All six research dimensions: topology §1, token/NFR §2, resilience §3, security §4, failures §5, design scenarios §6.
