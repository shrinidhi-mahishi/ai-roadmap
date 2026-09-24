# Research: Agentic RAG

**Date researched**: 2026-09-23
**Sources consulted**: 100

Scope: **hybrid search, reranking, parent-document / hierarchical / contextual retrieval, query rewriting, and citation tracking** as the query-time stack around a **shared, mostly read-only corpus**. Ingest vs query planes; the agent loop `retrieve → grade → rewrite → generate`; rerank as a **tool**, not a generator. Embedder SKUs, tokenizer IDs, and generation list prices live in [`01-python-llm-foundations.md`](01-python-llm-foundations.md). Working-memory buffers, Mem0/Zep/Letta/Store **write** paths, and episodic unlearning live in [`06-memory-systems.md`](06-memory-systems.md). This file does **not** recopy those tables. ⚠️ No unpublished p50/p95/p99 for “RAG e2e” is invented; `$ / 1k queries` figures are **[inferred]** from a stated reference query × published rates — not a vendor SKU.

Invariant (Lewis, Perez, Piktus et al., NeurIPS 2020): the generator’s **parametric** memory is not the corpus. Non-parametric memory is an index (originally DPR over Wikipedia + BART). The model never searches. It emits a tool call or a rewritten query; the retriever executes; chunks return as observations ([paper](https://proceedings.nips.cc/paper_files/paper/2020/file/6b493230205f780e1bc26945df7481e5-Paper.pdf)).

---

## 1. System Topology & Mechanics

### 1.1 Two planes + a control loop (not a function)

A production RAG product is **two independently scaled planes sharing indexes**, plus a **control loop** around those indexes. Couple ingest to query and p99 tracks reindex. Fuse the loop with ingest and a CRAG web fallback writes the public internet into the corpus.

| Plane | Owns | Typical components | Failure if coupled |
| --- | --- | --- | --- |
| **Ingest (write)** | Parse, PII DLP, ACL stamp, chunk, contextualize, embed, sparse encode, parent/child IDs, graph extract, checkpoint | Connectors, workers, batch embed APIs, HNSW/IVF build, Leiden | Query p99 tracks reindex; a stuck extractor stalls answers |
| **Query (read)** | Authz filter, hybrid retrieve, fuse, rerank, agent loop, generate, cite | ANN + inverted index, RRF/RSF, cross-encoder, LangGraph/LlamaIndex, generator | Ingest schema change silently mismatches query embeddings |
| **Control** | Adaptive router, hop cap, alias pin, PEP | LangGraph graph, classifier, circuit breakers | Unbounded rewrite; web exfil of confidential queries |

**Five indexes coexist in one product RAG:** (1) dense ANN (HNSW / IVF / BBQ-HNSW); (2) sparse/lexical (BM25, SPLADE, `pinecone-sparse-english-v0`; Postgres `tsvector` is **not** BM25 — true BM25 is ParadeDB `pg_search`); (3) metadata/ACL bitmap **pre-filter before ANN**; (4) graph (entities, reports, text units); (5) rerank cache `(query_hash, doc_id, model, version) → score` — not a recall index.

**Agentic vs naive vs advanced.** Naive: always retrieve top-k, always generate. Advanced: query transform + hybrid + rerank as a **DAG**. Agentic: retrieval is a **tool** with a bounded loop. LangGraph’s official tutorial is the production approximation of Self-RAG + CRAG **without** training reflection tokens: `generate_query_or_respond` binds a retriever tool → retrieve **only if the model emits a tool call** → `grade_documents` (`GradeDocuments.binary_score` yes/no) → `generate_answer` **or** `rewrite_question` → back to retrieve ([LangGraph agentic RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag)). The tutorial **has no hop counter**. Production adds `retry_count` / `MAX_ATTEMPTS` (common cap **3**) and a wall-clock; LangGraph issue [#7481](https://github.com/langchain-ai/langgraph/issues/7481) is the missing `max_retries` edge. `RetryPolicy(max_attempts=3)` retries **node exceptions**, not “grader said irrelevant” ([fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)).

```
query → PEP (ACL predicate from verified token)
      → Adaptive router: chitchat | factoid | multi-hop | global
      → [authz filter on every arm]
      → dense ANN (k=50–100) ∥ BM25/sparse (k=50–100)
      → RRF / RSF / α  → fused N≈50–150
      → rerank tool → top_n=5–20
      → grade ──irrelevant──► rewrite (cap hops) ──► retrieve again
              └──relevant──► generate + cite (IDs from retrieved set only)
```

Rerank is a **tool** with its own RPM, timeout, and cache. Do not inline 80 joint encodes inside the generator prefill.

### 1.2 RAG vs memory (product distinction — do not collapse)

RAG and memory share embeddings and ANN. They are different products. Full write-path semantics: **06**. The interview trap is using a shared RAG index as “user memory.”

| Dimension | RAG (this file) | Memory (06) |
| --- | --- | --- |
| Source | External corpus the agent did **not** create | The agent’s / user’s interaction stream |
| Scope | Shared, versioned documents | Per-user / per-agent / per-run |
| Write path | Re-index on a schedule or document write | Extract, conflict-policy, expire, unlearn |
| Freshness | Stale *documents* | Stale *beliefs about a person* |
| Typical failure | Missing/outdated chunk; wrong parent; hallucinated cite | Contradictory facts; identity mix-up; poisoning |
| Cost pattern | Read-heavy | Read **and** write; extract LLM after turns |

CoALA: RAG over Wikipedia is **read-only semantic memory of the world**; agent memory is **writable semantic + episodic memory of the interaction** ([Sumers et al.](https://arxiv.org/abs/2309.02427)). The 2026 survey restates complementarity ([arXiv:2604.01707](https://arxiv.org/html/2604.01707v3)). Graphiti/Zep graphs are **memory**; Microsoft GraphRAG community reports over a **corpus** are RAG. Mixing them in one Pinecone namespace without a type tag is how a ticket preference retrieves a Wikipedia cluster.

### 1.3 Hybrid search: BM25 + dense, RRF / RSF / α

Dense misses exact IDs (`TS-999`, SKUs, statute numbers). BM25 misses paraphrase. Anthropic’s 2024-09-19 Contextual Retrieval restates the six-step sketch: chunk → TF-IDF + embeddings → BM25 top + dense top → rank fusion → top-K into the prompt ([engineering post](https://www.anthropic.com/engineering/contextual-retrieval)). Their example: embedding-only on `"Error code TS-999"` retrieves general error-code docs; BM25 hits the exact string.

**RRF** (Cormack, Clarke, Buettcher, SIGIR 2009). Rank-only, scale-free. For document \(d\) over rankings \(R\):

\[
\mathrm{RRF}(d)=\sum_{r\in R}\frac{1}{k+\mathrm{rank}_r(d)}
\]

\(k=60\) was fixed in a TREC pilot and not altered in validation; the authors note the choice is near-optimal but **not critical** ([PDF](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf); [ACM](https://doi.org/10.1145/1571941.1572114)). Rank 1 contributes \(1/61\approx 0.0164\); rank 60 contributes \(1/120=0.0083\). Documents in **both** lists outrank a document that wins only one. BM25 unbounded scores and cosine \([-1,1]\) never share a numeric space — that is why RRF exists. Elasticsearch `rank_constant` default **60**; `rank_window_size` default **10** (must be \(\ge\) `size`) ([RRF retriever](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/retrievers/rrf-retriever)). OpenSearch `score-ranker-processor` default \(k=60\), available **2.19+** ([RRF](https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/rrf/)). Weaviate `rankedFusion` is \(1/(\mathrm{RANK}+60)\). After each retriever returns top-\(k\): \(O(k\cdot|R|)\) to accumulate, then sort the union — dominated by ANN + inverted-index latency, not the fuse.

**Score fusion (when magnitudes are trusted):**

| Method | Who | Mechanism | When it wins |
| --- | --- | --- | --- |
| **Relative Score Fusion** | Weaviate default since **v1.24** | Min-max each list to \([0,1]\), then α-weighted sum | Score gaps carry signal |
| **Alpha convex combo** | Pinecone single-index; Weaviate `alpha` | \(\alpha\cdot\mathrm{dense}+(1-\alpha)\cdot\mathrm{sparse}\) | Same index, same query; A/B α |
| **DBSF** | Qdrant | Mean/std of the **prefetch** top-k (3-σ remap) | Calibrated retrievers; outlier-sensitive |
| **min_max + arithmetic_mean** | OpenSearch `normalization-processor` (2.10+) | Score-space mix via search pipeline | Explicit 0.3/0.7 weights |

**Weaviate.** Hybrid since **v1.17**. `alpha`: 0 = keyword, 1 = vector, **server default 0.75** *if unset*. Clients historically sent their own default; gRPC `alpha=0` is Go’s zero value → **pure BM25** if the client does not set the field ([PR #10553](https://github.com/weaviate/weaviate/pull/10553)). **Set `alpha` explicitly.** `fusionType`: `relativeScoreFusion` (default ≥ v1.24) vs `rankedFusion`. Optional max vector distance gates the **dense** arm only ([concepts](https://docs.weaviate.io/weaviate/concepts/search/hybrid-search); [blog](https://weaviate.io/blog/hybrid-search-explained)).

**Pinecone (vector API).** Three patterns: (1) **single index** dense+sparse, `metric=dotproduct` only, server-side weighted dotproduct; (2) **two indexes** + client RRF; (3) document schema with FTS `string` (BM25) + `dense_vector`. Official trap: BM25 / `pinecone-sparse-english-v0` scores are **unbounded**; cosine dense is ~[-1,1]. Without `hybrid_score_norm` (scale dense by α, sparse by \(1-\alpha\) **on the query vectors**), sparse **dominates** ([hybrid search](https://docs.pinecone.io/guides/search/hybrid-search)). Starting α (vendor walkthrough, not a law): 0.75 NL docs, 0.5 mixed, 0.25 SKU/ID-heavy. **Serverless extra trap:** hybrid may **preselect by dense** then re-rank with sparse inside that \(k\) — changing α then appears to do nothing ([community](https://community.pinecone.io/t/when-i-perform-hybrid-search-on-sparse-dense-vector-db-and-when-i-change-the-alpha-value-it-returns-the-same-output/6285)). Single-index cannot do sparse-only queries. `pinecone-sparse-english-v0` requires `input_type` `query` vs `passage`; vendor: up to **44%** (avg **23%**) NDCG@10 vs BM25 on TREC DL, up to **24%** (avg **8%**) on BEIR — **vendor-stated** ([model card](https://docs.pinecone.io/models/pinecone-sparse-english-v0)).

**Elasticsearch.** Retrievers preview **8.14**, GA **8.16**. `rrf` wraps ≥2 children (`standard` BM25, `knn`, `sparse_vector`/ELSER, `semantic`). Nest `text_similarity_reranker` **outside** `rrf`. Elastic Stack **9.2+**: per-retriever `weight`. BBQ: up to **32×** compression (vendor-stated). 8.16 blog: RRF + retrievers GA **for Enterprise licensed customers**.

**OpenSearch.** `hybrid` query + **search pipeline** (not in-query fusion). Max **5** subqueries ([hybrid query](https://docs.opensearch.org/latest/query-dsl/compound/hybrid/)). Processors: `normalization-processor` (2.10) or `score-ranker-processor` (2.19, RRF). Hybrid cannot nest under `function_score` / `constant_score` / `script_score` / `boosting`. Pagination: `pagination_depth` per subquery per shard; changing it **changes the fused set**. ≥3.5: `min_score` after fusion; >512 shards auto-disables batched reduction.

**Qdrant (≥1.10 Query API).** `prefetch[]` then **top-level** `FusionQuery` (`RRF` or `DBSF`). Fusion **inside** prefetch = per-shard (wrong for multi-shard hybrid) ([hybrid queries](https://qdrant.tech/documentation/search/hybrid-queries/)). Optional formula decay **after** fusion so recency is ranking, not a hard gate.

**pgvector + lexical.** One SQL round-trip: CTE dense + CTE `tsvector`/`websearch_to_tsquery` or ParadeDB BM25, `FULL OUTER JOIN`, `1/(k+rank)`. Postgres `ts_rank` is **not BM25**.

**Vertex RAG Engine / Weaviate-backed hybrid.** `hybrid_search.alpha` default **0.5** (equal), unlike Weaviate’s 0.75 dense lean — another “unset default” trap if you port a Weaviate query string. One rerank layer. Retrieval RPM ⚠️ org-quota; do not copy a blog’s 600 RPM as an SLO.

**Redis Query Engine RRF.** Operational reason to prefer ranks over scores as the corpus grows: BM25 distributions drift with DF; vector scores jump when the embedder changes; ranks stay comparable ([Redis explainer](https://redis.io/blog/reciprocal-rank-fusion/)). Same \(k=60\) formula.

**Bedrock Knowledge Bases.** `HYBRID` or `SEMANTIC`; hybrid needs a filterable text field else the service **falls back to semantic**. Guardrails cover **query and answer**, not retrieved source text — citations can still quote unredacted chunks. Managed KB list prices are commonly quoted as Standard Retrieve **$1.00 / 1k API calls**, Agentic Retrieve **$4.00 / 1k + $1.00 / 1k** underlying Retrieve, storage **$5.00 / GB raw / month** — ⚠️ confirm the live AWS region price page (not re-fetched as HTML on 2026-09-23).

**Azure AI Search.** BM25 + HNSW → RRF → Semantic Ranker over the hybrid top **50**. Agentic retrieve (2026): parallel subqueries; **does not** apply classic scoring profiles. Pass Entra in `x-ms-query-source-authorization`. Semantic Ranker: 1k req/mo free then **region $ / 1k** — do not invent a USD rate.

### 1.4 Reranking: cross-encoders, Cohere / Voyage, LLM, late interaction

**Bi-encoder vs cross-encoder.** Bi-encoder: encode query once, docs offline, ANN. **O(1) query encode + ANN.** Stage-1 recall, \(k=50–200\). Cross-encoder: jointly attend over `(query, document)` — **one forward pass per candidate**. Stage-2 precision, keep **3–20** for the generator. Sentence-Transformers documents this as the canonical pattern ([sbert](https://www.sbert.net/examples/applications/cross-encoder/README.html)). Anthropic eval: retrieve **150**, rerank to **20** ([Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)). Azure Semantic Ranker reorders hybrid top **50**. Never send pre-rerank noise to the generator (lost-in-the-middle + hallucinated citations).

**Cohere Rerank.** Models in current docs: `rerank-v4.0-pro`, `rerank-v4.0-fast`, `rerank-v3.5`, `rerank-v3.0`. API: `top_n`; `max_tokens_per_doc` default **4096**; v4.0 context **32,768** (query can consume half). Hard cap: `num_documents * max_chunks_per_doc ≤ 10,000`. Recommend ≤**1,000** docs/request. **Search unit (official FAQ on cohere.com/pricing, 2026-09-23):** 1 query + up to **100 documents**; if query+doc > **500 tokens**, auto-split; each chunk counts as a document toward the 100 ([pricing FAQ](https://cohere.com/pricing); [API](https://docs.cohere.com/reference/rerank.mdx)). Rate limits (official): Rerank **trial 10 req/min**, **production 1,000 req/min**; Embed 2,000 inputs/min ([rate limits](https://docs.cohere.com/docs/rate-limits.mdx)). **Pay-as-you-go $:** cohere.com/pricing on 2026-09-23 lists **Model Vault instance** SKUs (Rerank 4 Fast/Pro hourly/monthly), **not** a public per-search table. Bedrock / third-party aggregators commonly quote Rerank 3.5 at **$2.00 / 1,000 searches** — ⚠️ confirm the dashboard you bill against ([Metacto May 2026](https://www.metacto.com/blogs/cohere-pricing-explained-a-deep-dive-into-integration-development-costs)). Pinecone Inference hosts `cohere-rerank-v3.5` at **$2 / 1k requests** (Standard/Enterprise).

**Voyage Rerank (official pricing page, 2026-09-23).** Formula: \((q_\mathrm{tok}\times n_\mathrm{docs})+\sum d_i\). Caps (`rerank-2.5` / `2.5-lite`): query ≤**8,000** tok; query+any doc ≤**32,000**; ≤**1,000** docs; total processed ≤ **600k**. Latency-sensitive: lite + ≤200k total ([reranker](https://docs.voyageai.com/docs/reranker); [FAQ](https://docs.voyageai.com/docs/faq)). List prices ([pricing](https://docs.voyageai.com/docs/pricing)):

| Model | $/1M processed | Free tokens | Vendor est. $/req (100 docs × 500 tok) |
| --- | --- | --- | --- |
| `rerank-3` / `rerank-2.5` | **$0.05** | 200M on **3**; **0** on **2.5** | **$0.0025** |
| `rerank-3-lite` / `rerank-2.5-lite` | **$0.02** | same pattern | **$0.001** |

Voyage’s 2025-08-11 blog: on 93 datasets, `rerank-2.5` **+7.94%** NDCG@10 vs Cohere Rerank v3.5 averaged over four first-stage methods — **vendor-stated** ([blog](https://blog.voyageai.com/2025/08/11/rerank-2-5/)). Batch API **33% off**, 12h window; **free credits do not apply**.

**bge-reranker.** `BAAI/bge-reranker-v2-m3`: multilingual, sequence-classification; serve via HF TEI `/rerank`. Pinecone Inference **$2 / 1k**. BGE-M3 emits dense + sparse + ColBERT in **one** forward pass (Chen et al., [arXiv:2402.03216](https://arxiv.org/abs/2402.03216)).

**Late interaction (ColBERT).** Khattab & Zaharia (SIGIR 2020): query and doc encoded independently into **token** vectors; score \(=\sum_i\max_j\cos(q_i,d_j)\) (MaxSim). Docs encoded offline; query encoded once ([arXiv:2004.12832](https://arxiv.org/abs/2004.12832)). ColBERTv2: 6–10× smaller index via residual compression ([NAACL 2022](https://aclanthology.org/2022.naacl-main.272.pdf)). PLAID: **2.5–7× GPU** and **9–45× CPU** vs vanilla ColBERTv2; tens of ms GPU / tens-to-few-hundreds ms CPU at **140M passages** — paper-stated ([arXiv:2205.09707](https://arxiv.org/abs/2205.09707)). MaxSim is **unbounded** (a relevant pair might score 4 or 18). Do not share a numeric threshold with Cohere \([0,1]\) or BGE-v2 ~0.55–0.75 relevant.

**LLM-as-reranker.** Pointwise (yes/no or 0–1 per chunk), pairwise (which of two), listwise (reorder 10). Cost: a 70B/frontier judge over 50 chunks **dwarfs** a cross-encoder. Pairwise is \(O(n^2)\) comparisons unless you tournament. Use a cheap model for agentic **binary** `grade_documents`, not as the primary 100-way ranker. Self-RAG **trains** `ISREL`/`ISSUP` into the generator — that is not a drop-in API rerank.

**Score calibration (do not mix thresholds).** BGE-v2-m3 sequence-classification logits after sigmoid typically land in \([0,1]\) with relevant pairs often ~0.55–0.75 **[practitioner range, not a paper constant]**. Cohere relevance scores are \([0,1]\) but the mass sits higher on in-domain FAQ. ColBERT MaxSim is unbounded. A “keep if score > 0.7” gate copied from a Cohere notebook onto ColBERT either drops everything or keeps everything. Calibrate on **your** labeled set; log the reranker **model id** next to the score.

**Two-stage production default (stack the lifts Anthropic measured):**

```
query → [authz filter]
      → dense ANN (k=50–100) ∥ BM25/sparse (k=50–100)
      → RRF / RSF / α  → fused N≈50–150
      → cross-encoder / Cohere / Voyage / ColBERT top_n=5–20
      → generator (citation IDs ⊆ this set)
```

Elastic nests `text_similarity_reranker` **over** `rrf`. Qdrant: prefetch 20–100, fuse, optionally ColBERT as the final `query` instead of fusion. Google Ranking API: **$1.00 / 1k** (100-doc units), 80k free / 30 days on some Vertex SKUs — ⚠️ confirm live quota. Bedrock Managed KB rerank can be **$0** (included) — still apply ACL before the KB sees the query.

### 1.5 Parent document retrieval, hierarchical chunking, contextual retrieval

Chunking is an **ingest-plane compiler**. Retrieval quality is often more sensitive to chunk policy than to embedding brand (Anthropic, LlamaIndex cookbooks, 2026 multi-objective evals).

| Strategy | Extra model calls | Helps | Hurts |
| --- | --- | --- | --- |
| Fixed token window + overlap | No | Predictable vector count | Mid-sentence splits; orphaned pronouns |
| Recursive (`\n\n` → `\n` → `.`) | No | Fewer mid-sentence breaks | Unaware of semantic shifts |
| Sentence / structure-aware | No | Legal/markdown headings | Uneven sizes; huge tables |
| Semantic breakpoints | Embed sentences | Topic shifts | Cost + unstable boundaries |
| Title/summary prepend | Summary: yes | Cheap lexical boost | Generic summary ≠ chunk-specific |
| **Contextual Retrieval** | LLM per chunk; **prompt-cache the document** | BM25 **and** dense **and** reranker **and** generator see situated text | Ingest $; PII spread |
| **Late chunking** (Jina, [arXiv:2409.04701](https://arxiv.org/abs/2409.04701)) | No extra LLM; long-context embedder | Dense vectors carry doc-level context via token-then-pool | Lexical index unchanged |
| Parent-document / small-to-big | No | Retrieve small, generate on parent | Parent may exceed context; **ACL must copy to both** |
| RAPTOR tree (Sarthi et al., [arXiv:2401.18059](https://arxiv.org/abs/2401.18059)) | Summary LLM per cluster | Multi-granularity retrieve | Abstractive loss; rebuild on corpus flux |

**Small-to-big.** Embed child chunks (typically 200–400 tokens / ~400 chars in LangChain tutorials) for precise matching; at query time look up `parent_id` and return the parent (500–2000 tokens, or the raw document). The embedding of a mixed 2k-token parent is a **bag of topics**; the child is a **needle**. LangChain `ParentDocumentRetriever` extends `MultiVectorRetriever`: children go to the vectorstore with `id_key` in metadata; parents go to a `docstore`; retrieve deduplicates by parent ID (union, **not** two copies of the same parent) and `mget`s ([reference](https://reference.langchain.com/python/langchain-classic/retrievers/parent_document_retriever/ParentDocumentRetriever); [source](https://github.com/langchain-ai/langchain/blob/e8ca09d54e9a04c2c8906d3b238a32dfa898ec28/libs/langchain/langchain_classic/retrievers/parent_document_retriever.py)). If `parent_splitter` is omitted, the parent **is the raw document** — a 40-page PDF becomes one generate-time payload. Two variants: (1) parent-child with an explicit parent splitter; (2) **sentence-window** — retrieve a sentence, expand ±N sentences (LlamaIndex `SentenceWindowNodeParser`; typical window 1–3). LlamaIndex `AutoMergingRetriever` walks a hierarchy and merges siblings when enough children hit — stop merging when the merged node would exceed the generator budget.

**Production invariants for parent/child:**

1. Stamp `acl` / `tenant` / `version` / `char_span` on **child and parent**. Generate-time expansion that loads a parent the user cannot see is an entitlement bug. If the parent is the whole contract and the child is a public exhibit, **do not** use whole-doc parents.
2. Rebuild swaps **vectorstore + docstore** under one `index_version`. Orphan children (`mget` → `None`) are dropped silently in the reference `MultiVectorRetriever` — you will retrieve “successfully” with fewer parents than children.
3. Cap parent tokens (e.g. 1,200–2,000) or fall back to child + heading path. Lost-in-the-middle applies **inside** a too-large parent as well as across many chunks.
4. Citations should point at the **child span** that matched, then optionally display parent context. Citing the parent ID while quoting a child from a **different** version is provenance fraud.

**Contextual Retrieval (Anthropic, 2024-09-19).** Prepend 50–100 tokens of **chunk-specific** context (not a generic doc summary — they tried that and saw limited gains) before embedding **and** before BM25. Prompt-cache the whole document; Haiku-class contextualizer. Stated assumptions: ~800-token chunks, ~8k-token docs, ~50-token instructions, ~100-token contexts. **One-time cost they state: $1.02 per million document tokens.** Eval (Gemini Text 004, top-20, 1−recall@20, their mix): baseline fail **5.7%** → contextual embeddings **3.7%** (−35%) → + contextual BM25 **2.9%** (−49%) → + Cohere rerank 150→20 **1.9%** (−67%). KB **< ~200k tokens (~500 pages)** → skip RAG, cache the corpus. Cookbook Pass@k on 248 code-chunk queries is a **different** table (baseline Pass@10 **87.15%** → +rerank **95.26%**) — do not flatten it onto the 5.7% failure series ([cookbook](https://platform.claude.com/cookbook/capabilities-contextual-embeddings-guide)).

**Late chunking.** Embed the full document (or max window), mean-pool **token** vectors per chunk. Does **not** inject company names into BM25. Promote when you are dense-only and already on an 8k–32k embedder.

**Practical starting point [inferred]:** 400–800 tokens, 10–20% overlap, sentence snap, `doc_id` / `section` / `acl` / `version` / `parent_id` / `char_span` on every child. Promote to contextual BM25 when eval shows pronoun/entity misses; promote to parent expansion when the generator lacks surrounding statute/section context.

### 1.6 Query rewriting: HyDE, multi-query, Adaptive-RAG, CRAG, Self-RAG, IRCoT

**HyDE** (Gao, Ma, Lin, Callan, ACL 2023 / arXiv 2022). Zero-shot: instruct an LLM to “write a document that answers the question”; encode the **hypothetical** with an unsupervised contrastive encoder (Contriever); retrieve real neighbors. The encoder’s bottleneck is supposed to drop hallucinated details ([arXiv:2212.10496](https://arxiv.org/abs/2212.10496); [code](https://github.com/texttron/hyde)). Cost: **one extra generate + one extra embed** per hop. LlamaIndex documents two failures: mis-interprets queries without corpus context; **biases** open-ended queries toward the generator’s parametric style. Do not HyDE SKU/error-code lookups — BM25 already wins those. GraphRAG **DRIFT** uses HyDE as a **primer** over community reports, then local search — HyDE here is a router feature, not a replacement for hybrid.

**Multi-query / RAG-Fusion.** LLM emits \(n\) paraphrases (commonly 3–5); retrieve each; fuse with RRF (the “RAG-Fusion” blog pattern). Recall lift on ambiguous questions; \(n\times\) retrieve **and** \(n\times\) query-embed. Cap \(n=3\) unless eval shows otherwise. Sub-question decomposition (LlamaIndex `SubQuestionQueryEngine` / `StepDecomposeQueryTransform`) is sequential: each sub-answer is `prev_reasoning` for the next retrieve — closer to IRCoT than to parallel multi-query. Stop when the rewrite is `"none"` (LlamaIndex multi-step engine).

**Step-back** (Zheng et al., ICLR 2024). Generate a more abstract question first (“what governs iPhone 13 Pro Max display specs?”) then retrieve; use both original and step-back hits. Helps when the user query is over-specific and BM25 overfits a rare token. Still fuse with the original query — step-back-only retrieval drops the identifier.

**Adaptive-RAG** (Jeong, Baek, Cho, Hwang, Park, NAACL 2024). T5-Large classifier routes: **A** = no retrieval (`LLM(q)`); **B** = single-step (`LLM(q,d)`); **C** = multi-step iterative (`LLM(q,d,c)`). Labels from which strategy actually answered correctly + dataset inductive bias (single-hop vs multi-hop). Averaged over NQ/SQuAD/TriviaQA + MuSiQue/HotpotQA/2Wiki, GPT-3.5-Turbo-Instruct: Adaptive-RAG **F1 50.91 / Acc 48.97 / 1.03 steps / 1.46× time** vs always multi-step **F1 50.87 / 2.81 steps / 3.33× time** vs single-step **F1 46.99 / 1.00 step**. Oracle classifier F1 **62.80**. Confusion: **~47%** of true-A predicted as B; **~31%** of true-C as B ([arXiv:2403.14403](https://arxiv.org/html/2403.14403v2); [ACL](https://aclanthology.org/2024.naacl-long.389/); [code](https://github.com/starsuzi/Adaptive-RAG)). Production clothing: chitchat → no retrieve; factoid → hybrid+rerank; multi-hop → agent 2–3 hops; global → LazyGraphRAG / community reports.

**CRAG** (Yan, Gu, Zhu, Ling, arXiv 2401.15884). Lightweight **T5-large** evaluator scores each of ~10 retrieved docs. Confidence → **Correct** (refine strips: decompose-then-recompose) / **Incorrect** (discard internal; **web search**) / **Ambiguous** (mix). Plug-in on RAG and Self-RAG. SelfRAG-LLaMA2-7b PopQA: RAG **40.3** → CRAG **59.3** → Self-RAG **54.9** → Self-CRAG **61.8** accuracy; Biography FactScore RAG **59.2** → CRAG **74.1** → Self-RAG **81.2** → Self-CRAG **86.2**. Ablating Correct/Incorrect/Ambiguous each drops PopQA. T5 evaluator **beat ChatGPT** at judging retrieval quality on PopQA in their Table 4 ([abs](https://arxiv.org/abs/2401.15884); [code](https://github.com/HuskyInSalt/CRAG)). **Enterprise invariant:** Incorrect→web is an **exfil path**. Bound fallback to an **approved** corpus (licensed news, internal second index), not the open internet, on confidential queries.

**Self-RAG** (Asai, Wu, Wang, Sil, Hajishirzi, ICLR 2024). One LM trained to emit reflection tokens:

| Token | Input | Values | Role |
| --- | --- | --- | --- |
| `Retrieve` | \(x\) / \(x,y\) | yes / no / continue | Whether to call \(\mathcal{R}\) |
| `ISREL` | \(x,d\) | relevant / irrelevant | Passage useful for \(x\) |
| `ISSUP` | \(x,d,y\) | fully / partially / no support | Attribution |
| `ISUSE` | \(x,y\) | 5…1 | Utility |

Default inference weights: ISREL **1.0**, ISSUP **1.0**, ISUSE **0.5**; retrieval threshold **0.2** (0 on ALCE because citations are required); beam width **2**; Contriever top **5**. Self-RAG 7B vs always-retrieve Llama2-7B: PopQA **54.9 vs 38.2**; ASQA citation precision **66.9 vs 2.9**, recall **67.8 vs 4.0**. Raising ISSUP weight lifts citation precision and **hurts MAUVE** (Liu et al. 2023 consistency) ([arXiv:2310.11511](https://arxiv.org/abs/2310.11511); [ICLR PDF](https://proceedings.iclr.cc/paper_files/paper/2024/file/25f7be9694d7b32d5cc670927b8091e1-Paper-Conference.pdf); [selfrag.github.io](https://selfrag.github.io/)). Production teams almost always **prompt** a separate grader rather than train tokens.

**IRCoT** (Trivedi, Balasubramanian, Khot, Sabharwal, ACL 2023). What to retrieve at step \(n\) depends on step \(n-1\). Loop: retrieve from the question → generate next CoT **sentence** → that sentence is the next query → until answer or max steps. GPT-3: retrieval up to **+21 points**, QA up to **+15 F1** on HotpotQA / 2Wiki / MuSiQue / IIRC ([arXiv:2212.10509](https://arxiv.org/abs/2212.10509)). HippoRAG (NeurIPS 2024): single-step Personalized PageRank **10–20× cheaper, 6–13× faster** than iterative retrieve **in HippoRAG’s experiments** ([arXiv:2405.14831](https://arxiv.org/abs/2405.14831)).

**Loop bound invariant.** Official LangGraph can loop until runtime timeout. Production: `retry_count`, wall-clock, terminal `insufficient_evidence`. Do **not** fall back to parametric knowledge on ACL-sensitive corpora. LangGraph `RetryPolicy(max_attempts=3)` retries **thrown exceptions** with backoff; a grader returning `"no"` is a **successful** node. Those are different knobs ([fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)). Persist `retry_count` in graph state (checkpointer) so a replay does not reset the cap.

**Rewrite quality.** LangGraph’s tutorial rewrite prompt asks for “underlying semantic intent” and replaces the user message with a `HumanMessage` of the rewrite — the **original question can disappear from state** unless you keep it on another channel. Grade/generate prompts that read `state["messages"][0]` then grade against the **rewritten** question, not the user’s words. Store `original_question` and `search_query` as separate keys. HyDE + rewrite stacked without a cap is how you spend four generates before the first cited answer.

### 1.7 Citation tracking: grounding, quote spans, hallucination, provenance

**Task split.** Retrieval can be perfect and generation still invents a cite. Gao, Yen, Yu, Chen (EMNLP 2023) **ALCE**: first benchmark for Automatic LLMs’ Citation Evaluation. Metrics: fluency, correctness, **citation quality** via NLI (TRUE) — a claim is supported when cited passages jointly **entail** it. On ELI5, even the best models lack complete citation support **~50%** of the time ([ACL](https://aclanthology.org/2023.emnlp-main.398/)). AIS (Bohnet et al., CL 2023) stipulates NLG about the external world is verified against an **identified source** ([PDF](https://aclanthology.org/2023.cl-4.2.pdf)).

**Verifiability of generative search.** Liu, Zhang, Liang (EMNLP Findings 2023): Bing Chat, NeevaAI, perplexity.ai, YouChat. Average: **51.5%** of generated sentences fully supported (citation **recall**); **74.5%** of citations support their sentence (citation **precision**). Precision **inversely** correlated with perceived utility (\(r=-0.96\)) — a **facade of trustworthiness** ([arXiv:2304.09848](https://arxiv.org/abs/2304.09848)).

**Hallucinated citations (three species).** (1) **ID not in retrieved set** — `[doc 17]`, a URL, a statute the retriever never returned. (2) **Retrieved but non-entailing** — the chunk is about the right entity; the claim is not in it (ALCE precision miss). (3) **Post-hoc rationalization** — the model decided the answer, then attached a nearby chunk (MIRAGE / 2026 engineering notes). Self-citation (prompt “cite your sources”) on Llama-2-7B/Zephyr often fails format or points at **non-existing** documents (EMNLP 2024 MIRAGE analysis).

**Quote spans vs chunk IDs.** Chunk-level footnotes do not prove the decoder used the evidence. Stronger provenance: store `char_span` / token offsets at ingest; emit citations that resolve to a **3–30 token span**; UI highlights the span. Strongest decode constraint: at the citation site, only emit tokens that exist as a contiguous span in the retrieved set (prefix tree / constrained decoding). Cost: less fluent paraphrase; gain: mechanical provenance.

**Production metric — provenance fidelity.** Fraction of cited IDs that (a) were in the retrieved set **this hop**, (b) NLI-support the claim, (c) the caller was **entitled** to see. Log: `source_uri`, `version`, `chunk_id`, `char_span`, `retriever` (bm25|dense|parent|graph_local|web), `rerank_score`, `user_id`, `tenant`, `index_build_id`. Hash-verify chunk body vs ingest sha256. Mitigations: constrained decode / tool-only citations from retrieved IDs; refuse if grader `ISSUP=no`; never cite a parent the ACL filter did not authorize.

**Lost-in-the-middle** (Liu, Lin, Hewitt et al., TACL / arXiv 2307.03172). U-shaped performance: primacy + recency; middle degrades. GPT-3.5-Turbo with the gold passage **in the middle** of a multi-doc prompt scored **below closed-book 56.1%** ([arXiv](https://arxiv.org/abs/2307.03172)). Found-in-the-middle (Hsieh et al., 2024): U-shaped **attention** bias; calibration up to **+15 pp** RAG ([arXiv:2406.16008](https://arxiv.org/html/2406.16008)). Operational: rerank so the best evidence is **not** buried; place top chunks at **edges** of the prompt; do not stuff 50 pre-rerank hits into 128k.

### 1.8 Graph RAG as a **tool**, not the default path

Vector RAG fails **global** questions (“themes in this corpus”) — query-focused summarization, not top-k (Edge et al., [arXiv:2404.16130](https://arxiv.org/abs/2404.16130)). Microsoft indexing: chunk → LLM extract → KG → **Leiden** communities → bottom-up reports → embed. Extraction ~**75%** of index $ ([methods](https://microsoft.github.io/graphrag/index/methods/)). Query modes: **Local** (entity neighborhood), **Global** (map-reduce all reports), **DRIFT** (HyDE primer + top-K reports → follow-ups → local iterations, default **2**), **Basic** (vanilla vector). `microsoft/graphrag` is a **research project in maintenance mode** — bugfix/CVE only, not an officially supported Microsoft offering ([GitHub](https://github.com/microsoft/GraphRAG)). LazyGraphRAG (MSR 2024-11): no LLM summaries at index time; index $ ≈ vector RAG and **0.1%** of full GraphRAG; at vector-RAG-like query $: **>700×** lower than GraphRAG global for comparable global quality — **Microsoft-stated** ([blog](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/)). LightRAG: dual-level + incremental updates ([arXiv:2410.05779](https://arxiv.org/abs/2410.05779)). Agent router: `vector_tool` vs `graph_local` vs `graph_global`. Do not run global map-reduce on “what’s the refund SLA?”

---

## 2. Token Economics & NFR Metrics

Embedder and generator **list prices**: **01**. This section is hybrid+rerank+generate and the **hop cost** of rewrite. Contextualize ingest \( is Anthropic’s, not an embed table.

### 2.1 Latency budget (no fake percentiles)

> ⚠️ Public vendor pages do **not** publish p50/p95/p99 for “RAG e2e.” Decompose. Timeout numbers marked **[policy, not a vendor SLO]**.

| Stage | What dominates | Order-of-magnitude |
| --- | --- | --- |
| Query embed | Small encoder / API | Tens of ms local; 50–200 ms hosted RTT **[inferred]** |
| Hybrid retrieve | ANN + inverted + fuse | Pinecone semantic-search **design target O(100 ms)** (architecture blog, **not an SLO**); PLAID: tens of ms GPU at 140M passages |
| Cross-encoder rerank | \(N\) joint encodes + network | Voyage prices tokens, not ms; **no Cohere Rerank SLA** on public pages |
| Agent extra hop | Grade + rewrite + 2nd retrieve + 2nd rerank | **+1–3 LLM calls** + another retrieve; Adaptive-RAG always-multi-step is **3.33×** GPT-3.5 time vs 1.00 single-step |
| Generate | Instr + chunks | Usually **>50%** of e2e $ and often of e2e latency **[inferred]** |
| Graph global | Map over community reports | Worst; LazyGraphRAG exists to kill this |

SLO: **p99 retrieve+rerank** separate from **p99 generate**. Circuit-break the vector DB independently of the LLM. Architecture-derived **[inferred]** targets: retrieve+rerank p50 150–400 ms / p95 400 ms–1.5 s / p99 **fail closed** at 1–3 s; e2e with generate p50 0.8–2 s / p95 2–6 s / p99 8–15 s with hop cap=3. Unbounded rewrite has **no** p99.

### 2.2 Reference query — `$ / 1k` hybrid + rerank + generate **[inferred]**

Assumptions (state in a design review; not a quote): 1k user questions, **no** agent retries; query embed 50 tok; retrieve 80 fused; rerank 80; keep 8 × 500 tok = 4k context; generate 4k in + 400 out. Dense embedder: OpenAI `text-embedding-3-small` **$0.02/1M** (**01**). Rerank: Voyage `rerank-2.5` \(50\times80 + 80\times500 = 44{,}000\) tok/query × $0.05/1M = **$0.0022/query**.

| Line item | Arithmetic | **[inferred] $ / 1k** |
| --- | --- | --- |
| Query embed | 50k tok × $0.02/1M | **$0.001** |
| Voyage rerank-2.5 | $0.0022/q | **$2.20** |
| Generate Claude Sonnet 5 (**01**: $2 / $10 per MTok) | 4k×$2 + 400×$10 per 1M = $0.012/q | **$12.00** |
| **Subtotal** | embed + rerank + generate | **≈ $14.2** |

**Excludes** vector DB RUs, graph map-reduce, retries. On Sonnet 5, **generation dominates**. Flip the generator to a mini-tier (~$0.15/$0.60 class, **verify live 01**): generate ~$0.84/1k → subtotal **~$3.0** and **rerank dominates**. Cohere search-unit path: if the billed meter is **$2.00 / 1k searches** (Bedrock/aggregator, not cohere.com HTML on 2026-09-23) and each question is **1** unit (≤100 docs, each ≤500 tok with query) → **$2.00/1k** plus embed plus generate. **80 fused 800-token chunks inflate units** (official split rule).

Pinecone Database RUs (official, cited in prior RAG research; confirm live [pricing](https://www.pinecone.io/pricing/)): Standard **~$16–$18 / million RUs**, storage **$0.33/GB/mo**. Query RUs scale with **namespace size** (1 GB ns → 1 RU/query; metadata-filter a 100 GB shared ns → 100 RUs). **[inferred]** 1k queries × 1 RU × $16/M = **$0.016**; same against 100 GB = **$1.60**. Dedicated Read Nodes: provisioned, no shared read rate limits.

**Anthropic contextualize [official]:** **$1.02 / 1M document tokens** one-time with prompt cache. 100M-token corpus → **~$102** ingest LLM **before** embeddings.

**Corpus embed (cite 01, do not recopy the table):** 1B tokens ≈ 1M docs × 1k tok is tens-to-low-hundreds of dollars at 3-small / voyage-4-lite vs a **single day** of Sonnet-5 generation at the reference mix. Query embed at 1k × 50 tok is noise.

### 2.3 Hop cost of agentic rewrite **[inferred]**

One extra hop ≈: (grade LLM) + (rewrite LLM) + (2nd hybrid retrieve) + (2nd rerank) + (maybe 2nd generate, or the same generate with new context).

**Grade** (LangGraph-shaped): ~400–800 tok in, ~10 tok structured `yes`/`no`. Haiku 4.5 (**01**: $1/$5): \(\approx\$0.0008\) in + negligible out. **Rewrite:** ~300 in / 80 out on Sonnet 5: \(\approx\$0.0014\). **Second Voyage rerank** at the 44k mix: **+$0.0022**. **Second generate** if you throw away the first: another **$0.012** on Sonnet 5.

| Policy | Extra LLM calls / user Q | Extra rerank | **[inferred] extra $ / 1k** (Sonnet 5 + Voyage 2.5) |
| --- | --- | --- | --- |
| Naive hybrid+rerank+1 generate | 1 generate | 1 | (baseline §2.2) |
| Always grade, never rewrite | +1 cheap grade | 0 | **~$0.8–2** |
| 20% of queries rewrite once | 0.2×(grade+rewrite+generate) + 0.2 rerank | 0.2 | **~$3–4** |
| Uncapped CRAG+web, 3 hops avg | 3× generate-class | 3 | **tens of $ / 1k** + web SKU |

Adaptive-RAG’s measured step counts are the **honest** multiplier: always-C is **2.81 steps / 3.33× time** on GPT-3.5 vs Adaptive **1.03 steps / 1.46×**. IRCoT with 4–8 BM25 pulls per hop is a different RU profile than one hybrid call. HyDE adds a **full generate** before the first retrieve — budget it as hop 0.

OpenAI web-search tool: **$10 / 1k calls** + content tokens (**01**). CRAG Incorrect→web on 10% of traffic is **$1 / 1k user questions** in tool fees alone, before tokens.

### 2.4 Caching, RPM, back-pressure

| Cache | Key | Hit saves |
| --- | --- | --- |
| Embedding | `(model, dim, text_hash)` | Ingest re-runs; identical query embed |
| Retriever | `(index_version, filter, query_hash, k)` | Duplicate questions; **must** include ACL in the key |
| Rerank | `(reranker, query_hash, doc_id)` | Agent retries |
| Prompt | Static instructions + tool schemas (01/02) | Generate prefill |
| Parent docstore | `parent_id` | Small-to-big expansion |

RPM: Cohere Rerank prod **1,000 req/min** (trial **10**). Agent loops: 3 retrieves × 1k user QPS = **3k retrieve RPM** — size the vector DB and reranker **for the loop, not the user QPS**. OpenAI embeddings: **300k tok/request**, **2048** inputs, org-tier RPM/TPM (**01**). Voyage Batch 12h / 33% off. Hedging a retrieve to a replica doubles RU on the p99 path — budget it.

Prompt-cache mechanics (TTL, breakpoints, 272k cliff): **01/02**. RAG fights the cache when every request prepends a different top-8; cache the **system + tool schemas**, not the chunks, unless you have a small hot FAQ prefix.

---

## 3. Distributed Resilience & State

### 3.1 Index rebuild, alias flip, dual-write

Changing **embedder id, dimension, similarity metric, or chunker version** is a full re-embed. Query embeddings from model B against index A → silent recall collapse (Invariant I2).

**Blue/green alias (production default):**

1. Source watermark (S3 etag / Drive revision / CDC LSN / SharePoint ACL version).
2. Raw blob + sha256 (poisoning detection).
3. Parse/chunk with `chunk_id = hash(doc_id, chunker_version, text)`.
4. Embed job keyed by `embed_model + dim + chunk_id`. Sparse encode with the **same** chunk text the BM25 index will see (contextualized or not — both arms must agree).
5. Upsert into `index_version=N+1` (new collection / namespace / ES index). Parent docstore version **must** match.
6. **Shadow eval** nDCG@k **and** citation-NLI on a frozen golden set against the live alias. Include SKU/ID queries (BM25 arm) and paraphrase queries (dense arm).
7. **Then** flip the query alias (`index_alias → N+1`). Keep N readable until error budget is green. ES/OpenSearch: atomic `_alias` remove+add in one request. Pinecone: switch the query-plane `namespace` or host config, not a row-level flag the model can see.
8. Graph: per-chunk extract checkpoint; Leiden **only** on a closed chunk set; reports last. Query plane pins a complete `graph_build_id`. Mid-Leiden crash → entities without reports. Microsoft CLI: `graphrag init --force` between minor versions; 1.0 indexes are **not** backward compatible.

**Dual-write** during an embedder migration: every ingest writes N and N+1; query still reads N until flip. Cost: 2× WU / 2× embed tokens for the cutover window. Do **not** dual-read and RRF across incompatible spaces (different dims or metrics). Shadow traffic: copy 1–5% of queries to N+1, log rank correlation vs N, **do not** show N+1 citations to users until flip.

**Watermark lag canaries.** A scheduled query against a document you control (“the canary runbook says CODE-CANARY-9”) must retrieve the current body within the SLO. If the canary hits N after you flipped to N+1, the alias did not move. If it hits stale text, CDC is behind — **fail closed** on policy documents, not on FAQs.

**Weaviate consistency.** Cluster metadata: **Raft**. Data objects: leaderless, tunable `ONE` / `QUORUM` (default) / `ALL`. Hybrid under `ONE` can return a replica missing the latest upsert → **stale chunk cited**. Use `QUORUM` for corpora that must not cite deleted docs ([replication](https://docs.weaviate.io/weaviate/concepts/replication-architecture/consistency)).

**Pinecone serverless.** Object-storage-backed; you do not set RF. Consistency is **eventual** at the product surface (upsert then immediately query can miss). Namespaces: Standard/Enterprise **100,000 / index**. Backups **$0.10/GB/mo**, restore **$0.15/GB**. Enterprise **99.95%** uptime SLA; Starter/Builder/Standard: no uptime SLA on the public table.

**Elasticsearch / OpenSearch.** Primary + replica shards; hybrid fusion on the coordinating node after shard-local subquery scores. Replica lag = BM25 and kNN seeing different live sets — **same query, two ranks**. OpenSearch hybrid + huge shard counts: coordinator RAM; 3.5+ disables batched reduction >512 shards. ES `rank_window_size` default 10 is conservative — raising to 50–100 is a cost/latency/RAM choice.

**pgvector.** WAL + streaming replicas. HNSW build is heavy; **build after bulk load**. Many teams ingest to a staging table and `REINDEX` / swap. RLS + replica: filters must exist on standby too.

**Parent/child consistency.** Child vectors without a parent docstore row → silent drop in `mget`. Rebuild must swap **both** stores under one `index_version`. Contextualized BM25 text that drifted from the dense payload is a dual-index skew bug.

### 3.2 Search timeout circuit

Treat ANN like a downstream HTTP dep:

- **Timeout** 200–500 ms retrieve **[policy, not a vendor SLO]**.
- **Error-rate breaker** (5xx, `resource_exhausted`, RU throttle).
- **Bulkhead** separate from the LLM pool — a Pinecone RU storm must not starve generate.
- **Fallback chain:** (1) last-good retrieve cache keyed by ACL, (2) BM25-only, (3) `retrieval_degraded` refusal — **never** generate ungrounded if policy forbids.
- **Hedging:** duplicate retrieve to a replica/region on p99; cancel loser.
- Agent: on retrieve failure, **do not** infinite rewrite.
- Rerank timeout: drop to fused top-8; do not block generate on a 1k-RPM Cohere ceiling.
- OpenSearch/ES: cap `rank_window_size` / `pagination_depth` — coordinator OOM is a **query-plane** incident.

LangGraph `RetryPolicy` is for **exceptions**, not “empty result set.” Empty retrieve is a **control-plane** branch to fallback, not a retry storm.

CRAG/web and agent retries must not write into the **corpus** index without a human/quarantine path.

---

## 4. Enterprise Security & Governance

### 4.1 ACL in the query predicate, not the prompt

Invariant I3: authorization is a **hard pre-filter** applied **before** ANN, on **every hop**, including rewrite, parent expansion, and graph report lookup. Prompt text (“ignore docs you shouldn’t see”) is not a control. Post-filter-only ANN: as the forbidden set grows, top-k fills with unauthorized neighbors and **authorized recall → 0**.

Oracle’s enterprise RAG checklist: **policy travels with evidence**. Stamp at ingest: owner, tenant, role, classification, delete-state, source version. Enforce as **mandatory query predicates**.

**Isolation ladder:**

| Pattern | Guarantee | Cost |
| --- | --- | --- |
| Metadata `tenant_id` filter | App-bug can omit filter | Cheapest; Pinecone: scans **full namespace** |
| **Namespace / collection / index per tenant** | Query cannot cross (Pinecone: 1 GB tenant = 1 RU; 100×1 GB cheaper than 100 GB filter) | More indexes; Pinecone `$in`/`$nin` max **10,000** |
| Instance / BYOC per tenant | Strongest (HIPAA/finance) | Pinecone BYOC: zero inbound SSH; PrivateLink |

Soft recency (decay) **after** ACL; soft recency without ACL still leaks. Azure AI Search agentic retrieve: pass Entra token in `x-ms-query-source-authorization`; it **does not** apply index scoring profiles the way classic hybrid does — verify ACL still pushes down.

Delete/tombstone: vector delete must match source ACL revocation; **eventual consistency** windows (Weaviate `ONE`, Pinecone serverless) are a compliance bug.

### 4.2 Citation of unauthorized docs

A citation is a **read**. If the generator cites `chunk_id` the caller cannot fetch, you have already leaked existence, title, or quote text in the answer. Controls:

1. Retrieved set \(R\) is ACL-filtered.
2. Parent expansion uses the **same** predicate; do not `mget` a parent the child was allowed to see if the parent ACL is tighter (or copy ACL down at ingest so they cannot diverge).
3. Citation decoder may only emit IDs \(\in R\).
4. UI “open source” button re-fetches through the same PEP — never a signed URL minted for the model.
5. Traces (LangSmith/OTel): document body redacted to the **caller’s** ACL, not the operator’s.

Graph community reports can **summarize secrets** into a high-level node that global search then retrieves for everyone with graph access — ACL on **reports**, not just raw chunks.

### 4.3 PII in chunks

Vectors are **derived personal data**. Contextual Retrieval **prepends** more PII (names, quarters, revenue) into every chunk — better retrieval, larger blast radius. Controls: ingest redaction **before** embed **and** before contextualize; deterministic + ML DLP **after** retrieve, **before** prompt; never log rerank documents at full text in shared SaaS traces. Embed APIs (OpenAI/Voyage/Cohere) see plaintext — DPA, zero-retention, or self-host BGE-M3. Graph extraction amplifies PII into entity nodes. Vec2Text-class inversion (Morris et al., EMNLP 2023) is why “we only store vectors” is not a GDPR answer — details in **06**.

### 4.4 Zero-Trust retrieve tools (MCP)

MCP `tools/call` on a retriever is a **data exfil API**.

1. **Server-side identity.** Tenant/ACL from the verified token / `RunContext`, never from tool arguments the model filled (`tenant_id` in JSON schema is a leak primitive). ABAC before search; chunk filter after; **predicate pushdown** so ANN never ranks cross-tenant rows ([arXiv:2605.05287](https://arxiv.org/html/2605.05287)).
2. **Least privilege per tool.** Separate MCP servers: `retrieve_public_kb` vs `retrieve_hr` vs `sql_customer` vs `rerank_api`. No omnibus `search(query, collection)`.
3. **Rerank as a tool** still sees document text — same DPA as embed; do not send HR chunks to a shared SaaS reranker without a BAA.
4. **Stateless MCP + stateful RAG.** LangGraph `/mcp` is stateless per request; conversation memory stays in the checkpointer (**05/06**), **not** the MCP session.
5. **Hosted MCP:** the provider’s network path sees queries; contract for data residency.
6. CRAG web tool: allowlist domains; strip query of customer identifiers; log the outbound URL.

OWASP LLM: vector/embedding weaknesses, poisoned ingest, cross-tenant namespace bugs. NIST SP 800-162 mapping (Secure RAG, doi [10.52710/cfs.976](https://doi.org/10.52710/cfs.976)): PEP at the vector query boundary; PDP for ABAC; redaction gate; citation validity gate. Measure **leakage rate**, **entitlement violation rate**, **provenance fidelity**, **false refusal**.

---

## 5. Production Failure Modes

| Failure | Mechanism | Blast radius | Detect | Mitigate |
| --- | --- | --- | --- | --- |
| **Score-scale hybrid** | Pinecone sparse unbounded vs dense [-1,1]; Weaviate client α≠0.75 / gRPC α=0; serverless dense-preselect | Keyword-only or semantic-only in practice | Offline A/B α; debug score components | `hybrid_score_norm`; RRF; set α explicitly; two-index + client RRF if serverless preselect bites |
| **Citation hallucination** | Generate without grounding; ID ∉ \(R\); NLI-fail; post-hoc attach | Legal/compliance incident; facade of trust (Liu 51.5% recall) | Citation ID ∉ retrieved set; ALCE-style NLI canary | ID-constrained cites; quote spans; ISSUP/CRAG grade; refuse |
| **Parent-child mismatch** | Child hits, parent missing / wrong version / looser ACL / oversize | Empty generate context, or **ACL upgrade** via parent | `mget` miss rate; parent token histogram; ACL diff child vs parent | Atomic alias of vectorstore+docstore; copy ACL; cap parent tokens |
| **Rewrite loops** | Grader false negative; HyDE drift; official LangGraph has no cap | $ explosion; p99 unbounded; web exfil | Loop-depth metrics; rewrite-count/user Q | `MAX_ATTEMPTS=3`; Adaptive-RAG front door; `insufficient_evidence` |
| **Grader false positive** | Noise marked relevant | Grounded-looking hallucination | Faithfulness eval | Reranker + NLI; don’t trust binary grade alone |
| **Stale indexes** | CDC lag, failed upsert, alias not flipped, replica `ONE` | Answers from deleted/old policy | Watermark lag; sample-query canaries | Alias swap; QUORUM; ingest checkpoints |
| **Embedding drift** | New model/dim/prompt, Matryoshka trim | Silent recall collapse | nDCG on frozen golden set after every embed bump | Pin model; dual-write + shadow eval; full re-embed |
| **Filter/ANN interaction** | Metadata filter + IVF; post-filter ACL | Recall → 0 for rare tenants | Recall@k per tenant | Bitmap/IVF bypass; pushdown ACL; namespaces |
| **Over-retrieval** | k=50 into 128k; agent 4 hops | Lost-in-the-middle (middle < closed-book 56.1% on GPT-3.5) | Context tokens/query histogram | Rerank to 5–20; edge-place evidence; hop cap |
| **Contextual PII spread** | Context prepend copies secrets | Broader ACL miss | DLP on chunks | Redact **before** contextualize |
| **OpenSearch hybrid nest** | `function_score(hybrid)` | Silent wrong scores / error | Query lint | Hybrid as top-level only |
| **Qdrant per-shard fusion** | Fusion inside prefetch | Wrong global rank | Multi-shard A/B | Fusion as main query |
| **ES rank_window_size=10** | Default too small for recall | Reranker never sees the right doc | Recall@50 vs @10 | Raise window; pay latency |
| **Cohere unit inflation** | Docs >500 tok with query | Surprise 2–8× rerank $ | `billed_units.search_units` | Truncate docs; pre-chunk; Voyage token meter instead |
| **HyDE parametric bias** | Hypothetical doc not in corpus style | Dense retrieves the wrong neighborhood | nDCG HyDE vs raw query | Disable HyDE on IDs; always fuse BM25 |
| **CRAG open web** | Incorrect → Google | Confidential query in third-party logs | Outbound URL audit | Approved corpus only |
| **Graph explosion / stale communities** | LLM NER dupes; old Leiden cut | Index $ 10×; global miss this week | Entity count vs docs; `graph_build_id` age | Canonicalize; Lazy/Light; pin snapshot |
| **Poisoned ingest** | Unreviewed connector | Persistent retrieval hijack | sha256 + source allowlist | Quarantine; signed ingest |
| **Rerank RPM/timeout** | 1k QPS × 80 docs > 1000 RPM | p99 blowup | Rerank error rate | Cache; lite model; local bge; drop to fused top-8 |
| **Maintenance-mode GraphRAG** | CVE/deps only | You fork forever | GitHub status | Treat as algorithm, not product |

---

## 6. Enterprise System Design Scenarios

### 6.1 Trade-off matrix

| Axis | Hybrid + rerank (default) | Agentic (LangGraph) | GraphRAG-class |
| --- | --- | --- | --- |
| **Best query class** | Factoid, FAQ, SKU+semantics | Ambiguous, multi-hop, “should I retrieve?” | Global themes |
| **Index $** | Embed + BM25 (+ optional contextualize $1.02/M) | Same | LLM extract ~**75%** or Lazy **~vector** |
| **Query $** | 1 embed + 1 hybrid + 1 rerank + 1 generate | ×(1+retries) | Local ≈ hybrid; **global ≫**; Lazy budgeted |
| **p99** | Predictable 2-stage | Fat tail (loops) | Global: worst; DRIFT: multi-pass |
| **Ops** | Two indexes or one hybrid engine; alias flip | Checkpointer, hop caps, traces | Graph snapshot versioning |
| **Security** | Filter/namespace | Tool isolation + **same filters on every hop** | ACL on nodes **and** reports |
| **Failure** | Score mix, stale ANN | Infinite rewrite, citation theater, web exfil | Graph explosion, stale communities |
| **When to choose** | 80% of enterprise KB chat | Support/research copilot | Exec “what changed this quarter across 10k docs” |

**Do not** run GraphRAG global on every turn. Router = Adaptive-RAG in production clothing.

### 6.2 Scenario A — Multi-tenant support KB with ACL

**Requirements:** tenant isolation, SKU/error-code queries (`TS-999`), p95 chat of a few seconds, SOC2. Shared corpus of runbooks + per-tenant overrides.

**Design:**

- Namespace-per-tenant (Pinecone) or RLS+HNSW (pgvector) until a few million chunks/tenant. Hybrid BM25+dense (α low on SKU-heavy tenants). Voyage/Cohere/bge rerank N=80→8. **No** GraphRAG.
- ACL as **query predicate** from Entra/JWT groups (`rbacScope`, `status=current`). Recency decay **after** ACL.
- Parent-child for runbooks: retrieve the error-code sentence, expand to the procedure section; copy ACL to both.
- Contextual BM25 if eval shows orphan figure references (“see Table 3”).
- Adaptive router: greeting → no retrieve; “what is TS-999?” → single hop; “compare last two RCA docs” → cap=2.
- Citations: `runbook_id` + `char_span` only; UI jumps to the span. Traces redacted per tenant.
- Circuit: retrieve timeout → BM25-only for that tenant namespace → `retrieval_degraded` if both fail. **Never** ungrounded refund advice.
- Observability: nDCG@10 on a per-tenant golden set of 50 tickets; loop-depth histogram; `search_units` / Voyage processed tokens; citation ID ∉ \(R\) rate (target: **0**).

**Economics [inferred]:** Pinecone RUs dominated by namespace GB — keep hot tenants small; reject metadata-filter-only 100 GB shared index. Rerank ~$2/1k if 1 search-unit/query (or Voyage ~$2.20 at 44k tok). Generate on Haiku/mini unless the ticket is legal-adjacent. Contextualize once per corpus version, not per query. Adaptive-RAG front door on a support mix that is 60% chitchat/status is the largest $ lever — skipping retrieve on those turns saves **the entire** rerank+generate-context bill, not pennies of embed.

### 6.3 Scenario B — Legal / research agentic RAG with citations

**Requirements:** “compare protocol X vs Y”; quote-level provenance; 21 CFR 11-style or litigation hold; no open web.

**Design:**

- Hybrid retrieve + **HippoRAG-style PPR** or IRCoT-capped 2-hop. Graph edges from **controlled** NER (ontology), not unconstrained LLM entities. Human review on new edges.
- Chunking: structure-aware (heading/article/section); children ~300–500 tok; parents = section. Contextual Retrieval with a **domain glossary** in the contextualizer prompt (Anthropic’s own recommendation). Redact PII **before** contextualize.
- Rerank 150→20 (Anthropic topology) on a **BAA’d** reranker or self-hosted bge. LLM listwise rerank only on the final 10 if the cross-encoder disagrees with the legal taxonomy.
- Citations: constrained to retrieved IDs; **quote spans** required for every numeric/date claim; ALCE NLI canary on a golden set (target: do not ship at ELI5’s 50% unsupported). Refuse `insufficient_evidence` rather than parametric completion.
- CRAG evaluator: Correct/Ambiguous only against the **licensed** corpus; Incorrect → second internal collection or human, **not** Google.
- Audit log: WORM store of `index_build_id`, \(R\), rerank scores, spans, model ids. Replay must reconstruct the prompt.
- Hop cap **2–3**; wall-clock; no HyDE on citation-sensitive queries (hypotheticals contaminate dense neighborhood with non-record text).

**Avoid:** full Leiden global search; entity explosion; LLM-as-only-reranker on 200 chunks; stuffing 50 chunks (lost-in-the-middle); citing a parent the ACL filter did not return; HyDE on a deposition transcript (the hypothetical is not evidence).

### 6.4 Scenario C — Cost-capped internal GPT (brief)

Postgres hybrid RRF + self-hosted `bge-reranker-v2-m3` on TEI; Adaptive-RAG skip retrieve on greetings; generate mini-tier; prompt-cache system+tool schemas; agent max **1** rewrite; graph: none. **[inferred] $ / 1k** well under $1 if generate is mini and rerank is self-hosted — **your** GPU/RAM is the rerank bill. This is the control for “do we need Cohere.” Measure nDCG before you pay $2/1k search units.

### 6.5 What “done” looks like in a design review

Bring: (1) a labeled query set that includes IDs **and** paraphrases; (2) RRF vs α A/B with score-component logs; (3) rerank 80→8 vs 20→8 recall@10; (4) hop-cap trace showing the third rewrite is impossible; (5) citation canary with a planted unsupported claim that the system **refuses**; (6) tenant B cannot cite tenant A’s runbook even when the strings match; (7) alias-flip runbook with dual-write and canary; (8) $ / 1k split as embed (01) vs RU vs rerank vs \(\sum\) hops vs generate. If any of those is a slide with no metric, it is not a RAG design — it is a demo.

### 6.4 Decision checklist (interview-ready)

1. **Recall first:** hybrid (RRF or explicit α) beats dense-only on IDs; prove with a labeled set, not MTEB.
2. **Precision second:** cross-encoder 50–150 → 5–20; LLM grade is a **router**, not a 100-way ranker.
3. **Loop third:** cap hops; retrieval as tool; CRAG fallback only to **approved** corpora; Adaptive-RAG at the front door.
4. **Citations always:** IDs from \(R\) only; prefer quote spans; measure provenance fidelity; ACL on the cite path.
5. **Parents:** small-to-big with ACL copied; atomic alias of both stores.
6. **Graph last:** only if eval shows global/multi-hop failure; prefer Lazy/HippoRAG/LightRAG; Microsoft OSS is maintenance-mode research.
7. **NFR:** budget $ as embed (pennies, **01**) + RU + rerank + \(\sum\) LLM loops; SLO retrieve vs generate separately; breakers on the index; dual-write + alias flip on re-embed.

---

## Sources

1. https://proceedings.nips.cc/paper_files/paper/2020/file/6b493230205f780e1bc26945df7481e5-Paper.pdf — Lewis et al., RAG, NeurIPS 2020
2. https://arxiv.org/abs/2309.02427 — CoALA; RAG as read-only world memory
3. https://arxiv.org/html/2604.01707v3 — 2026 memory vs RAG survey
4. https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf — RRF SIGIR 2009; k=60
5. https://doi.org/10.1145/1571941.1572114 — RRF ACM
6. https://www.anthropic.com/engineering/contextual-retrieval — 35/49/67% failure drops; $1.02/M; 150→20; 200k-token skip-RAG
7. https://platform.claude.com/cookbook/capabilities-contextual-embeddings-guide — Pass@k cookbook table (248 queries)
8. https://www-cdn.anthropic.com/5722e7658c9302d8b97a3238de1bb8e6afdf04b9.pdf — Contextual Retrieval appendix
9. https://docs.weaviate.io/weaviate/concepts/search/hybrid-search — alpha 0.75; RSF vs rankedFusion
10. https://docs.weaviate.io/weaviate/search/hybrid — hybrid API
11. https://weaviate.io/blog/hybrid-search-explained — RSF default v1.24
12. https://github.com/weaviate/weaviate/pull/10553 — gRPC alpha=0 BM25 trap
13. https://docs.weaviate.io/weaviate/concepts/replication-architecture/consistency — Raft vs ONE/QUORUM/ALL
14. https://docs.pinecone.io/guides/search/hybrid-search — hybrid_score_norm; unbounded sparse
15. https://docs.pinecone.io/guides/search/hybrid-search.md — same, markdown
16. https://www.pinecone.io/learn/hybrid-search-intro — alpha walkthrough
17. https://docs.pinecone.io/models/pinecone-sparse-english-v0 — query vs passage; TREC/BEIR vendor lifts
18. https://community.pinecone.io/t/when-i-perform-hybrid-search-on-sparse-dense-vector-db-and-when-i-change-the-alpha-value-it-returns-the-same-output/6285 — serverless dense preselect
19. https://docs.pinecone.io/guides/index-data/implement-multitenancy — 1 ns/tenant; 1 RU/GB
20. https://docs.pinecone.io/guides/search/filter-by-metadata — $in 10,000
21. https://www.pinecone.io/pricing/ — RU/WU/storage; Inference rerank $2/1k
22. https://www.elastic.co/docs/reference/elasticsearch/rest-apis/retrievers/rrf-retriever — rank_constant 60; rank_window_size
23. https://www.elastic.co/guide/en/elasticsearch/reference/8.19/rrf.html — RRF 8.19
24. https://www.elastic.co/blog/whats-new-elastic-search-8-16-0 — retrievers GA; BBQ
25. https://docs.opensearch.org/latest/query-dsl/compound/hybrid/ — max 5 clauses
26. https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/index/ — pipelines
27. https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/rrf/ — RRF vs min_max
28. https://docs.opensearch.org/latest/search-plugins/search-pipelines/score-ranker-processor/ — k=60; 2.19
29. https://docs.opensearch.org/latest/search-plugins/search-pipelines/normalization-processor/ — min_max fusion
30. https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/pagination/ — pagination_depth
31. https://qdrant.tech/documentation/search/hybrid-queries/ — RRF, DBSF, shard fusion
32. https://docs.voyageai.com/docs/reranker — rerank-2.5 caps 32k / 1k docs / 600k
33. https://docs.voyageai.com/docs/pricing — $0.05/$0.02; formula; rerank-3; 2.5 free-tier 0
34. https://docs.voyageai.com/docs/faq — 600k total tokens; lite 200k latency hint
35. https://blog.voyageai.com/2025/08/11/rerank-2-5/ — vendor +7.94% NDCG vs Cohere v3.5
36. https://cohere.com/pricing — search-unit definition; Model Vault SKUs (2026-09-23)
37. https://docs.cohere.com/docs/rate-limits.mdx — Rerank 10 / 1000 RPM
38. https://docs.cohere.com/reference/rerank.mdx — 10k chunk cap; billed search_units
39. https://docs.cohere.com/docs/reranking-with-cohere.mdx — v4.0 / v3.5 models
40. https://www.metacto.com/blogs/cohere-pricing-explained-a-deep-dive-into-integration-development-costs — Rerank 3.5 $2/1k (third-party, May 2026)
41. https://www.sbert.net/examples/applications/cross-encoder/README.html — bi- vs cross-encoder
42. https://arxiv.org/abs/2004.12832 — ColBERT MaxSim
43. https://aclanthology.org/2022.naacl-main.272.pdf — ColBERTv2 6–10× compression
44. https://arxiv.org/abs/2205.09707 — PLAID 2.5–7× GPU / 9–45× CPU; 140M passages
45. https://arxiv.org/abs/2402.03216 — BGE-M3 dense+sparse+ColBERT
46. https://huggingface.co/BAAI/bge-reranker-v2-m3 — OSS reranker
47. https://reference.langchain.com/python/langchain-classic/retrievers/parent_document_retriever/ParentDocumentRetriever — ParentDocumentRetriever
48. https://github.com/langchain-ai/langchain/blob/e8ca09d54e9a04c2c8906d3b238a32dfa898ec28/libs/langchain/langchain_classic/retrievers/parent_document_retriever.py — child metadata id_key
49. https://github.com/langchain-ai/langchain/blob/dfca7f44246f50208fcfab914ca265a277cdc0ae/libs/langchain/langchain_classic/retrievers/multi_vector.py — mget parents; MMR
50. https://arxiv.org/abs/2409.04701 — Late Chunking
51. https://arxiv.org/abs/2401.18059 — RAPTOR
52. https://arxiv.org/abs/2212.10496 — HyDE Gao et al.
53. https://aclanthology.org/2023.acl-long.99/ — HyDE ACL 2023
54. https://github.com/texttron/hyde — HyDE code
55. https://arxiv.org/html/2403.14403v2 — Adaptive-RAG tables; A/B/C; confusion
56. https://aclanthology.org/2024.naacl-long.389/ — Adaptive-RAG NAACL
57. https://github.com/starsuzi/Adaptive-RAG — Adaptive-RAG code
58. https://arxiv.org/abs/2401.15884 — CRAG
59. https://arxiv.org/pdf/2401.15884v2 — CRAG numbers PopQA/Bio/Pub/ARC
60. https://github.com/HuskyInSalt/CRAG — CRAG code
61. https://arxiv.org/abs/2310.11511 — Self-RAG
62. https://proceedings.iclr.cc/paper_files/paper/2024/file/25f7be9694d7b32d5cc670927b8091e1-Paper-Conference.pdf — Self-RAG ICLR; Table 2 citations
63. https://selfrag.github.io/ — Self-RAG project
64. https://arxiv.org/abs/2212.10509 — IRCoT +21 retrieval / +15 QA
65. https://aclanthology.org/2023.acl-long.557/ — IRCoT ACL
66. https://github.com/StonyBrookNLP/ircot — IRCoT bm25_retrieval_count 2/4/6/8
67. https://docs.langchain.com/oss/python/langgraph/agentic-rag — retrieve as tool; grade; rewrite; no hop cap
68. https://docs.langchain.com/oss/python/langgraph/fault-tolerance — RetryPolicy ≠ rewrite cap
69. https://github.com/langchain-ai/langgraph/issues/7481 — max_retries for self-RAG graphs
70. https://arxiv.org/abs/2307.03172 — Lost in the Middle; middle < closed-book 56.1%
71. https://doi.org/10.1162/tacl_a_00638 — Lost in the Middle TACL
72. https://arxiv.org/html/2406.16008 — Found in the Middle; +15 pp
73. https://aclanthology.org/2023.emnlp-main.398/ — ALCE; ELI5 ~50% unsupported
74. https://aclanthology.org/2023.cl-4.2.pdf — AIS attribution framework
75. https://arxiv.org/abs/2304.09848 — Liu et al. verifiability 51.5% / 74.5%; r=−0.96
76. https://aclanthology.org/2024.emnlp-main.347.pdf — MIRAGE; self-citation misses
77. https://tianpan.co/blog/2026/04/23/rag-citations-post-hoc-rationalization — post-hoc citation; span constraints
78. https://arxiv.org/abs/2404.16130 — GraphRAG local→global
79. https://microsoft.github.io/graphrag/ — query modes
80. https://microsoft.github.io/graphrag/index/methods/ — 75% extract cost; FastGraphRAG
81. https://microsoft.github.io/graphrag/query/drift_search/ — DRIFT default 2 local iterations
82. https://github.com/microsoft/GraphRAG — maintenance mode warning
83. https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/ — 0.1% index; 700× query
84. https://arxiv.org/abs/2410.05779 — LightRAG
85. https://arxiv.org/abs/2405.14831 — HippoRAG vs IRCoT 10–20× cheaper
86. https://arxiv.org/abs/2502.14802 — HippoRAG 2
87. https://arxiv.org/abs/2502.11371 — RAG vs GraphRAG systematic eval
88. https://arxiv.org/pdf/2506.02404 — GraphRAG-Bench
89. https://blogs.oracle.com/developers/secure-enterprise-rag-acls-tenant-filters-provenance-and-oracle-deep-data-security — policy travels with evidence
90. https://arxiv.org/html/2605.05287 — multitenant retrieval; ABAC; predicate pushdown
91. https://doi.org/10.52710/cfs.976 — Secure RAG PEP/PDP; provenance metrics
92. https://developers.llamaindex.ai/python/examples/query_transformations/query_transform_cookbook/ — multi-query / HyDE cookbook
93. https://github.com/pgvector/pgvector — pgvector
94. https://www.paradedb.com/blog/hybrid-search-in-postgresql-the-missing-manual — ParadeDB BM25 + pgvector RRF
95. https://redis.io/blog/reciprocal-rank-fusion/ — RRF operational: ranks vs drifting BM25/vector scores
96. https://arxiv.org/abs/2310.06117 — Step-Back prompting (Zheng et al., ICLR 2024)
97. https://developers.llamaindex.ai/python/examples/workflow/multi_step_query_engine/ — multi-hop stop on `"none"`
98. https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base.html — Bedrock KB HYBRID vs SEMANTIC; guardrails scope
99. https://learn.microsoft.com/en-us/azure/search/semantic-search-overview — Azure Semantic Ranker top-50 pattern
100. https://docs.langchain.com/oss/python/langgraph/persistence — checkpointer vs Store (hop cap must persist)

**Coverage confirmation:** hybrid (BM25+dense, RRF/RSF/DBSF/α, vendor traps) §1.3; reranking (cross-encoders, Cohere/Voyage, LLM, ColBERT/PLAID) §1.4; parent-document / hierarchical / contextual / late chunking §1.5; query rewriting (HyDE, multi-query, Adaptive-RAG, CRAG, Self-RAG, IRCoT, LangGraph) §1.6; citation tracking (ALCE, AIS, Liu verifiability, spans, unauthorized cites) §1.7–§4.2. RAG vs memory §1.2 (details: **06**). Embed list prices **01**. All six dimensions: topology §1, token/NFR §2, resilience §3, security §4, failures §5, scenarios §6. Claims dated on or before 2026-09-23. Cohere per-search $ is labeled third-party/Bedrock where the first-party page only published search-unit semantics + Model Vault.
