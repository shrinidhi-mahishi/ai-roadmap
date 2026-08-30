# Module 06 — RAG (Retrieval-Augmented Generation)

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `research_cursor_grok/research/06-rag.md` (researched 2026-08-21, 78 sources).
**Mandatory topics**: Hybrid search · Reranking · Agentic RAG · Graph RAG.

The unit of production is not “retrieve then generate.” It is two independently scaled **planes sharing indexes**: an **ingest (write) plane** that parses, redacts, ACL-stamps, chunks, embeds, sparse-encodes, and (optionally) extracts a graph; and a **query (read) plane** that authorizes, hybrid-retrieves, fuses, reranks, optionally loops an agent, generates, and cites. Lewis et al. (NeurIPS 2020) still holds: the generator’s parametric memory is **not** the corpus. The model never searches. It emits a tool call or a rewritten query; the retriever executes; chunks return as observations. Interview answers that skip the ingest/query split fail when the follow-up is “why did p99 jump during reindex, and who stamped `tenant_id`?”

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns identity, ACL principal extraction (never from model-filled tool args), Adaptive-RAG routing (no-retrieve / hybrid / multi-hop / global-graph), hop fuses, circuit breakers, and the alias that query pins (`index_version` / `graph_build_id`). Data plane owns ANN + inverted index + fusion + cross-encoder + generator tokenize/prefill/decode. Persistence is **five coexisting indexes**, not one vector table: dense ANN, sparse/lexical, metadata/ACL bitmap, graph snapshot, rerank cache. Tool proxies are retriever MCP servers (`retrieve_public_kb` vs `retrieve_hr`) plus optional web/SQL/KG peers. Telemetry is the only place retrieve+rerank latency, loop depth, entitlement violations, and provenance fidelity are authoritative.

Ingest and query share storage, not threads. Coupling them makes query p99 track reindex and a stuck extractor stall answers.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS  (chat SSE / sync extract / batch eval / MCP host)                      │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │  TLS + correlation-id + verified tenant token
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE (query)                                                           │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │ API Gateway│─▶│ Policy       │─▶│ Query router │─▶│ Orchestrator           │ │
│  │ auth,quota │  │ PII redact   │  │ Adaptive-RAG │  │ retrieve-as-tool       │ │
│  │ RPM vs loop│  │ ACL from     │  │ chitchat|FAQ │  │ grade → rewrite        │ │
│  │ breaker    │  │  token (PEP) │  │ hop|global   │  │ max hops ≈ 3           │ │
│  └────────────┘  └──────┬───────┘  └──────┬───────┘  │ pin index_version      │ │
│                         │                 │          └──────────┬─────────────┘ │
└─────────────────────────┼─────────────────┼─────────────────────┼───────────────┘
                          │                 │                     │
                          │                 ▼                     ▼
┌─────────────────────────┼───────────────────────────────────────────────────────┐
│ DATA PLANE (query)      │  model = untrusted planner; indexes execute search    │
│                         │                                                       │
│  ┌──────────┐  ┌────────┴──────┐  ┌─────────────┐  ┌──────────┐  ┌───────────┐ │
│  │ Query    │─▶│ Hybrid retrieve│─▶│ Fusion      │─▶│ Rerank   │─▶│ Generator │ │
│  │ embed    │  │ dense k=50–100│  │ RRF k=60 /  │  │ cross-enc│  │ cite IDs  │ │
│  │          │  │ BM25  k=50–100│  │ RSF / α     │  │ N→5–20   │  │ only      │ │
│  └──────────┘  └──────┬────────┘  └─────────────┘  └──────────┘  └─────┬─────┘ │
│                       │  ACL bitmap / namespace pre-filter on BOTH arms │       │
│  ┌────────────────────┴─────────┐  ┌───────────────────────────────────┘       │
│  │ Graph query                  │  │ TOOL PROXIES (MCP)                        │
│  │ local | DRIFT | global map   │  │ retrieve_public_kb / retrieve_hr / sql    │
│  │ (only if router said global) │  │ ticket: tenant, tool, expiry — not args   │
│  └──────────────────────────────┘  └───────────────────────────────────────────┘
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────┴──────────────────────────────────────────┐
│ INGEST PLANE (write) — independently scaled; Temporal workflows + Kafka log     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │Connector │─▶│Parse+PII │─▶│ACL stamp │─▶│Chunk     │─▶│Embed + sparse     │  │
│  │watermark │  │redact    │  │owner,    │  │400–800 tok│  │HNSW/IVF + BM25   │  │
│  │sha256    │  │before    │  │role,class│  │+overlap  │  │optional graph NER │  │
│  └──────────┘  │embed     │  └──────────┘  └──────────┘  └─────────┬─────────┘  │
│                └──────────┘                                        │            │
│  Leiden + community reports ONLY on a closed chunk set → alias flip│            │
└──────────────────────────────────────┬─────────────────────────────┘            │
                                       │                                          │
┌──────────────────────────────────────┴──────────────────────────────────────────┐
│ PERSISTENCE                                                                     │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌───────────────┐  │
│  │ Dense ANN  │ │ Sparse     │ │ ACL/meta   │ │ Graph snap │ │ Soft caches   │  │
│  │ HNSW/IVF/  │ │ BM25/SPLADE│ │ bitmap /   │ │ entities,  │ │ embed, retrieve│ │
│  │ BBQ-HNSW   │ │ ParadeDB   │ │ RLS / ns   │ │ reports,   │ │ rerank TTL    │  │
│  │            │ │ pinecone-  │ │ Pinecone   │ │ graph_build│ │ prompt cache  │  │
│  │            │ │  sparse-v0 │ │  slab idx  │ │  _id       │ │               │  │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘ └───────────────┘  │
└──────────────────────────────────────┬──────────────────────────────────────────┘
                                       │
┌──────────────────────────────────────┴──────────────────────────────────────────┐
│ TELEMETRY / OBSERVABILITY SINKS                                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌───────────────────────┐  │
│  │ Audit (WORM)│  │ Metrics      │  │ Traces      │  │ Eval / canaries       │  │
│  │ call_id,    │  │ retrieve+    │  │ gateway →   │  │ nDCG on frozen gold   │  │
│  │ chunk_ids,  │  │ rerank vs    │  │ hybrid →    │  │ watermark lag         │  │
│  │ acl_dec,    │  │ generate p99 │  │ rerank →    │  │ entitlement violation │  │
│  │ pii_ph,     │  │ loop depth,  │  │ generate    │  │ provenance fidelity   │  │
│  │ index_build │  │ breaker      │  │ (redact txt)│  │                       │  │
│  └─────────────┘  └──────────────┘  └─────────────┘  └───────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Ingest vs query planes

| Plane | Owns | Typical components | Failure if coupled |
| --- | --- | --- | --- |
| **Ingest (write)** | Parse, PII redaction, ACL stamp, chunk, contextualize, embed, sparse encode, graph extract, community reports, checkpoint, alias flip | Connectors, Temporal activities / Kafka workers, embedding/rerank batch APIs, HNSW/IVF build, Leiden | Query p99 tracks reindex; a stuck extractor stalls answers |
| **Query (read)** | Authz filter, hybrid retrieve, fuse, rerank, agent loop, generate, cite | ANN + inverted index, RRF/RSF, cross-encoder, LangGraph/LlamaIndex loop, generator | Ingest schema change silently mismatches query embeddings |

**Five index types that coexist in one product RAG:**

1. **Dense ANN** — HNSW / IVF / BBQ-HNSW (Elastic BBQ: vendor-stated up to **32×** compression, **>95%** memory reduction vs full-precision).
2. **Sparse / lexical** — BM25 (Elasticsearch/OpenSearch/Weaviate; ParadeDB/`pg_search` or Tiger `pg_textsearch` on Postgres). Postgres `tsvector`/`ts_rank` is **not BM25** (no corpus IDF); under RRF, rank order is what matters. SPLADE or `pinecone-sparse-english-v0` for sparse vectors.
3. **Metadata / ACL bitmap** — pre-filter **before** ANN (Pinecone slab metadata index → compressed bitmap of eligible IDs; ICML 2025: highly selective filters **bypass IVF** and scan the bitmap). `$in`/`$nin` max **10,000** values.
4. **Graph** — entity/relationship tables + community reports + optional vectors over entities, text units, and reports. Pin `graph_build_id`; query never mixes two snapshots.
5. **Rerank cache** — `(query_hash, doc_id, model, version) → score`, short TTL. Not a recall index.

Pin **model id + dimension + similarity metric + version** in the index schema. Changing any of them is a full re-embed.

### 1.3 End-to-end request flow

**Ingest (write path).**

1. **Watermark.** Connector records S3 etag / Drive revision / DB CDC LSN. Raw blob stored with sha256 (poisoning detection).
2. **Policy at rest.** PII detect → redact **before** embed. ACL stamped on every chunk: owner, tenant, role, classification, delete-state, source version. Contextual Retrieval prepends more PII into every chunk — redact **before** contextualize, not after.
3. **Compile chunks.** Production default **[inferred]**: 400–800 tokens, 10–20% overlap, sentence snap, `doc_id`/`section`/`acl`/`version` plus parent pointer. `chunk_id = hash(doc_id, chunker_version, text)`.
4. **Encode.** Dense embed (keyed by `embed_model + dim + chunk_id`). Sparse BM25/SPLADE. Optional GraphRAG extract per chunk (checkpointed); Leiden + community reports **only** on a closed chunk set.
5. **Publish.** Upsert into a new `index_version`. **Then** flip the query alias. Mid-Leiden crash must not become live. CRAG/web retries must not write into the corpus index without quarantine.

**Query (read path).**

1. **Ingress.** Gateway stamps correlation-id, authenticates, extracts tenant/roles from the verified token (`RunContext`). Circuit breaker state on ANN, BM25, reranker, and LLM are routing inputs.
2. **Policy.** Redact query PII before embed APIs see it. Attach **only** the retriever MCP tools this principal may call. `tenant_id` is **not** a tool argument the model fills (ABAC before search; arXiv 2605.05287).
3. **Route.** Adaptive-RAG classifier (Jeong et al., NAACL 2024): chitchat → no retrieve; factoid → hybrid+rerank; multi-hop → agent 2–3 hops; global QFS → LazyGraphRAG / community map-reduce. Do **not** run GraphRAG global on every turn.
4. **Authorize as a hard pre-filter.** Namespace / collection / RLS / bitmap **before** ANN. Recency is either hard (`status=current`) or soft (Qdrant formula decay **after** RRF). Soft recency without ACL still leaks.
5. **Hybrid retrieve.** Dense ANN \(k=50\)–\(100\) **in parallel with** BM25/sparse \(k=50\)–\(100\). Fuse with RRF (\(k=60\)) unless you trust score magnitudes (Weaviate RSF, Pinecone \(\alpha\) with `hybrid_score_norm`, Qdrant DBSF).
6. **Rerank.** Cross-encoder over fused \(N \approx 50\)–\(150\) → keep 5–20 (Anthropic eval used **150 → 20**). Never send pre-rerank noise to the generator (lost-in-the-middle + hallucinated citations).
7. **Agent loop (only if routed).** `generate_query_or_respond` → retrieve → `grade_documents` → `generate_answer` **or** `rewrite_question` (LangGraph). Cap hops (~3). On retrieve failure: `retrieval_degraded`, do not infinite-rewrite.
8. **Generate and cite.** Prompt = instruction + reranked chunks. Citations constrained to retrieved `chunk_id`s. Audit: `source_uri`, `version`, `char_span`, `retriever`, `rerank_score`, `user_id`, `tenant`, `index_build_id`.
9. **Degrade.** Breaker open on dense → BM25-only; rerank timeout → fused top-8; both indexes down → last-good retrieve cache or **refuse**. Never generate ungrounded if policy forbids.

**Interview talking point:** “Filters are a first-class retriever. Authorization is a hard pre-filter; recency may be a ranker. Post-filter-only ACL fills top-k with forbidden hits and recall collapses as the corpus grows.”

---

## 2. Core Mechanics & Algorithms

### 2.1 Hybrid search: BM25 + dense + RRF

Dense misses exact IDs (`TS-999`, SKUs, statute numbers). BM25 misses paraphrase. Hybrid runs both, then merges. Anthropic Contextual Retrieval (2024-09-19) restates: chunk → TF-IDF + embeddings → BM25 top + dense top → rank fusion → top-K into the prompt. Their eval (1−recall@20, Gemini Text 004, top-20): baseline fail **5.7%** → contextual embeddings **3.7%** (−35%) → +BM25 **2.9%** (−49%) → + Cohere rerank 150→20: **1.9%** (−67%). ~800-token chunks, ~8k-token docs. Prompt-cache contextualize: **$1.02 / 1M document tokens** (vendor-stated). KB **< ~200k tokens (~500 pages)** → skip RAG, cache the corpus.

**RRF (Cormack, Clarke, Buettcher, SIGIR 2009).** Rank-only, scale-free. For document \(d\) and ranked lists \(R\):

\[
\mathrm{RRF}(d)=\sum_{r\in R}\frac{1}{k+\mathrm{rank}_r(d)}
\]

Default \(k=60\) in Elasticsearch `rank_constant`, OpenSearch, Weaviate `rankedFusion` \(1/(\mathrm{RANK}+60)\), Qdrant RRF, and client-side Postgres CTEs. Rank 1 contributes \(1/61\approx 0.0164\); rank 60 contributes \(1/120=0.0083\). Documents in **both** lists outrank single-list winners. Redis’s operational reason: BM25 score distributions drift as the corpus grows; vector scores jump when the embedder changes; **ranks stay comparable**.

**Complexity.** Each arm: ANN \(\approx O(\log N)\) HNSW probes (ef_search dominates latency, not big-O) plus BM25 inverted-list scan. Fusion: sort each list \(O(k\log k)\), hash-merge \(O(k_{\mathrm{dense}}+k_{\mathrm{sparse}})\). Client RRF over two indexes is two round-trips; server hybrid is one.

**Score fusion (when magnitudes are trusted):**

| Method | Who | Mechanism | When it wins |
| --- | --- | --- | --- |
| **Relative Score Fusion** | Weaviate default ≥ **v1.24** | Min-max each list to \([0,1]\), \(\alpha\)-weighted sum | Score gaps carry signal |
| **Alpha convex combo** | Pinecone single-index; Weaviate `alpha` | \(\alpha\cdot\mathrm{dense}+(1-\alpha)\cdot\mathrm{sparse}\) | Same index, A/B \(\alpha\) |
| **DBSF** | Qdrant | Mean/std of prefetch top-k; 3-σ remap; identical scores → 0.5 | Calibrated retrievers |
| **min_max + arithmetic_mean** | OpenSearch `normalization-processor` 2.10+ | Search-pipeline mix | Explicit 0.3/0.7 weights |
| **Linear retriever** | Elasticsearch | Weighted normalized sum of children | Scores comparable |

**Vendor traps (invariants):**

- **Weaviate.** Hybrid since v1.17. Server default `alpha=0.75` **if unset** (dense-leaning). **Set `alpha` explicitly.** `max vector distance` gates dense only.
- **Pinecone.** Single-index dense+sparse requires `metric=dotproduct`. Sparse/`pinecone-sparse-english-v0` scores are **unbounded**; cosine dense is \(\sim[-1,1]\). Without `hybrid_score_norm` (scale dense by \(\alpha\), sparse by \(1-\alpha\) **on the query vectors**), sparse **dominates**. Starting \(\alpha\): 0.75 NL, 0.5 mixed, 0.25 SKU/ID-heavy. Single-index cannot do sparse-only or integrated embed+rerank.
- **Elasticsearch.** Retrievers preview 8.14, GA 8.16 (`rrf` wraps ≥2 children). Defaults: `rank_constant=60`, `rank_window_size=10` (**must be ≥ `size`**; default 10 is recall-hostile for a later reranker — raise to 50–100 and pay latency). Nest `text_similarity_reranker` **outside** `rrf`. RRF+retrievers GA **for Enterprise licensed customers**. Stack 9.2+: per-retriever `weight`.
- **OpenSearch.** Fusion lives in a **search pipeline**, not in-query. Max **5** subqueries. Hybrid rescore (2.18): **per subquery, per shard**, then coordinator fuse. Hybrid **cannot** nest under `function_score` / `constant_score` / `script_score` / `boosting`. ≥3.5: `min_score` after fusion; **>512 shards** auto-disables batched reduction (coordinator RAM).
- **Qdrant ≥1.10.** `prefetch[]` then `FusionQuery` RRF or DBSF. Fusion as **top-level query** = global across shards; fusion **inside** prefetch = **per-shard** (wrong for multi-shard hybrid). Formula decay (recency/geo) **after** RRF so recall is intact.
- **pgvector.** One SQL round-trip: CTE dense (`<=>` + HNSW) + CTE lexical, `FULL OUTER JOIN`, `1/(k+rank)`. Practitioner ceiling **[inferred from ops blogs, not a pgvector SLA]**: a few million chunks on one primary before HNSW RAM + filtered-recall collapse.

**BM25 parameters** (\(k_1\), \(b\), tokenization, property boosts) apply **inside** the lexical arm of hybrid. They do not mix with cosine.

### 2.2 Cross-encoder reranking and two-stage retrieval

**Bi-encoder vs cross-encoder.** Bi-encoder: one query encode + ANN, \(O(1)\) query encode. Cross-encoder: joint attention over `(query, document)`, **one forward pass per candidate**. Stage-1 is cheap recall (\(k=50\)–\(200\)); stage-2 is expensive precision (keep 3–20). Canonical Sentence-Transformers split.

**Production two-stage:**

```
query → [authz filter]
      → dense ANN (k=50–100) ∥ BM25/sparse (k=50–100)
      → RRF / RSF / α  → fused N≈50–150
      → cross-encoder top_n=5–20
      → generator (optional citation/NLI check)
```

**Cohere Rerank.** `rerank-v4.0-pro` / `fast`, `v3.5`, `v3.0`. `max_tokens_per_doc` default **4096**; v4.0 context **32,768** (query up to half, truncate 16,384). Cap: `num_documents * max_chunks_per_doc ≤ 10,000`. Recommend ≤1,000 docs/request. **Search unit:** 1 query + up to **100 documents**; if query+doc > **500 tokens**, auto-split; each chunk counts toward 100. Rate limits: trial **10 req/min**, production **1,000 req/min**. Model Vault: Rerank 4 Fast/Pro Medium **$5.00/hr, $3,250/mo**; Pro Large **$10.00/hr, $6,500/mo**. Bedrock listings commonly quote Rerank 3.5 at **$2.00 / 1,000 searches** (third-party/Bedrock, not Cohere.com HTML fetched 2026-08-21).

**Voyage Rerank.** `rerank-2.5` / `lite`, 32k context. Caps: ≤**1,000** docs; query+any doc ≤32k; total tokens \(q\times n_{\mathrm{docs}}+\sum\mathrm{doc}\leq\mathbf{600k}\). Official (2026-08-13): **$0.05 / $0.02 per 1M tokens**; 200M free. Vendor estimate: **~$0.0025/request** at 100 docs × 500 tokens. Vendor claim: +**7.94%** NDCG@10 vs Cohere v3.5 on 93 datasets — **vendor-stated**.

**bge-reranker.** `BAAI/bge-reranker-v2-m3` via HF TEI `/rerank`. Pinecone Inference: **$2 / 1k requests** (same SKU price for `cohere-rerank-v3.5` and `pinecone-rerank-v0`).

**LLM-as-reranker.** Pointwise / pairwise / listwise. A 70B/frontier judge over 50 chunks **dwarfs** a cross-encoder. Use for (a) agentic **binary** `grade_documents` on a cheap model, (b) citation faithfulness **after** generate — not as the primary 100-way ranker. Self-RAG (Asai et al., ICLR 2024) **trains** reflection tokens (`Retrieve`, `ISREL`, `ISSUP`, `ISUSE`); production almost always **prompts** a separate grader.

**Invariant:** the reranker is a precision operator on a recalled set. If `rank_window_size=10` (ES default) never recalled the right doc, no cross-encoder recovers it.

### 2.3 Agentic RAG retrieve loops

Naive RAG: always retrieve top-k, always generate. Agentic RAG: **retrieval is a tool** with a bounded loop.

**LangGraph production approximation of Self-RAG + CRAG (no fine-tune):**

```
                    ┌───────────────────────────┐
                    │ generate_query_or_respond │
                    │ (bind retriever_tool)     │
                    └─────────────┬─────────────┘
                                  │ tool_call? yes          no → respond
                                  ▼
                    ┌───────────────────────────┐
              ┌────▶│ retrieve                  │
              │     └─────────────┬─────────────┘
              │                   ▼
              │     ┌───────────────────────────┐
              │     │ grade_documents           │
              │     │ structured GradeDocuments │
              │     └─────────────┬─────────────┘
              │                   │
              │         all irrelevant?         some relevant
              │                   │                    │
              │                   ▼                    ▼
              │     ┌─────────────────┐    ┌─────────────────────┐
              │     │ rewrite_question│    │ generate_answer     │
              │     └────────┬────────┘    └─────────────────────┘
              │              │ hop < N
              └──────────────┘  else: insufficient evidence
```

**State machine.** States: `PLAN | RETRIEVE | GRADE | REWRITE | GENERATE | REFUSE | DEGRADED`. Transitions: PLAN→RETRIEVE iff the model requested the tool (Adaptive-RAG may skip). GRADE→REWRITE iff all chunks irrelevant; GRADE→GENERATE otherwise. REWRITE→RETRIEVE while `hops < N` (LangGraph examples \(N\approx 3\)). RETRIEVE failure → DEGRADED, not REWRITE. Unbounded CRAG+web is an **open proxy**.

**Self-RAG (Asai et al., arXiv 2310.11511).** One LM emits whether to retrieve, whether passages are relevant, whether generation is supported, whether the answer is useful. 7B/13B beat always-retrieve Llama2-chat **in the paper**.

**CRAG (Yan et al., arXiv 2401.15884).** Evaluator → **Correct** (internal docs) / **Incorrect** (web/external fallback) / **Ambiguous** (mix). Knowledge refinement strips noise. Enterprise: CRAG fallback only to **approved** corpora.

**LlamaIndex.** Multi-query rewrite → ensemble/fusion; sub-question tools; `MultiStepQueryEngine` / `StepDecomposeQueryTransform` until rewrite is `"none"`; HyDE as a rewrite agent. Multi-hop is sequential: each sub-answer is `prev_reasoning` for the next retrieve.

**IRCoT** interleaves CoT with retrieval. HippoRAG: single-step Personalized PageRank **10–20× cheaper, 6–13× faster** than iterative retrieve **in their experiments**.

**Complexity.** Naive RAG: 1 embed + 1 hybrid + 1 rerank + 1 generate. Agentic: \(\times(1+\mathrm{retries})\) LLM calls plus extra retrieves. Size the vector DB and reranker for **loop QPS**, not user QPS: 3 retrieves × 1k QPS = **3k retrieve RPM**. Cohere Rerank prod cap is **1,000 req/min**.

### 2.4 GraphRAG communities and hybrid graph+vector

Vector RAG fails **global** questions (“themes in this corpus”) because they are query-focused summarization, not top-k lookup (Edge et al., arXiv 2404.16130). Paper eval: ~**1M token** datasets; GraphRAG beats vector RAG on **comprehensiveness and diversity** (LLM-as-judge, no gold global answers). Systematic 2025 eval (arXiv 2502.11371v3): community GraphRAG helps multi-hop/summarization; **vector RAG often wins single-hop**; extraction noise is a first-class error. GraphRAG-Bench (arXiv 2506.02404): not all graph methods beat a strong GPT-4o-mini baseline — over-structure can **hurt**.

**Indexing pipeline (Microsoft docs + paper):**

1. Chunk source docs. Longer chunks → fewer extract LLM calls (cheaper) but **lost-in-the-middle** of early-chunk entities.
2. LLM extract entities, relationships, optional claims + descriptions. Standard GraphRAG: extraction is ~**75% of indexing cost**.
3. Build KG; **Leiden** hierarchical communities.
4. Bottom-up **community reports** (LLM summaries). ACL must apply to **reports**, not just raw chunks — reports can summarize secrets into a node global search then serves to everyone with graph access.
5. Embed text units / entities / reports for local lookup.
6. Persist Parquet + vector store. `graphrag init --force` between minor versions; major bumps need migration or full reindex.

**Query modes:**

| Mode | Mechanism | Query class |
| --- | --- | --- |
| **Local** | Match entities → neighborhood + text chunks | “Healing properties of chamomile?” |
| **Global** | Map-reduce over **all** community reports | “Significant values of the herbs in this notebook?” |
| **DRIFT** | Primer: HyDE + top-K reports → follow-ups → local iterations (default **2**) → hierarchical Q/A | Local questions that need a global primer |
| **Basic** | Vanilla top-k vector RAG | Ablation |

Dynamic community selection (MSR): from the root, LLM-rate report relevance; prune irrelevant subtrees; then map-reduce — cuts global-search cost vs scoring every report.

**Cheaper graph family:**

- **FastGraphRAG:** NLP noun phrases + co-occurrence; reports from raw text units; noisier; aimed at global questions.
- **LazyGraphRAG (MSR, 2024-11):** no LLM community summaries at index time. Indexing cost **identical to vector RAG** and **0.1% of full GraphRAG** (Microsoft-stated). Query: iterative deepening with a relevance-test **budget** (Z100 / Z500). At vector-RAG-like query cost: beats local competitors on local queries; **>700× lower query cost** than GraphRAG global for comparable global quality (Microsoft-stated). At **4%** of GraphRAG global query cost (Z500): beats compared methods on local+global **in their study**.
- **LightRAG (Guo et al., EMNLP 2025):** dual-level retrieve (entity/low-level + relationship/high-level) + **incremental** graph updates (avoid full Leiden rebuilds).
- **HippoRAG (Gutiérrez et al., NeurIPS 2024):** LLM + KG + Personalized PageRank; single-step multi-hop; up to **~20%** over SOTA RAG on multi-hop QA in the paper. HippoRAG 2 (ICML 2025): continual non-parametric memory.

**GitHub `microsoft/graphrag` (2026):** research project, **maintenance mode**, no new features/PRs; bugfix/CVE only. Not an officially supported Microsoft offering. Treat as an algorithm, not a product.

**Production shape:** GraphRAG, LightRAG, and HippoRAG all still **embed something**. An agent router picks `vector_tool` vs `graph_local` vs `graph_global` per query so “what’s the refund SLA?” never pays global map-reduce.

**Leiden invariant:** community detect only on a **closed** chunk set. A crash mid-Leiden leaves entities without reports. Query pins `graph_build_id`.

### 2.5 Complexity, state machines, invariants

| Operator | Time (query) | Failure if unbounded |
| --- | --- | --- |
| Dense ANN | HNSW probe; ef_search is the latency knob | Filtered IVF without bitmap → recall 0 |
| BM25 | Inverted lists | Tokenization mismatch vs ingest |
| RRF | \(O(k\log k)\) per list | Mixing raw BM25 with cosine without ranks |
| Cross-encoder | \(\Theta(N)\) forwards | \(N=200\) LLM-judge |
| Agentic hop | \(\times\) LLM + retrieve | Rewrite storm; web exfil |
| Graph global | Map over **all** reports | Timeout; $ cliff |
| LazyGraphRAG | Budgeted relevance tests | Budget too low → miss |

**Invariants worth stating in an interview:**

1. Parametric memory \(\neq\) corpus; search is a tool observation.
2. ACL is a **mandatory query predicate** (pushdown), not prompt text.
3. RRF is the default fusion when score spaces differ; set Weaviate/Pinecone \(\alpha\) explicitly if you fuse scores.
4. Rerank cannot fix stage-1 miss; window/k must admit the gold doc.
5. Agent hops are a **fuse**, not a quality heuristic. Grade-false-negative → rewrite loop; grade-false-positive → grounded-looking hallucination.
6. Graph last: only if eval shows global/multi-hop failure. Prefer Lazy/HippoRAG/LightRAG over naive full GraphRAG.
7. Never generate ungrounded when the index is unavailable if policy forbids.

---

## 3. Token Economics & NFR Analysis

Prices, search units, and compression ratios below are from vendor docs, papers, or named blogs as of **2026-08-21**. Public vendor pages **do not** publish p50/p95/p99 for “RAG e2e.” `$ per 1k queries` figures are **[inferred]** from published token/search-unit rates × a stated reference query, not a vendor SKU.

**Reference query (state in a design review):** 1k user questions, **no** agent retries. Query embed 50 tokens; retrieve 80 fused chunks; rerank 80; keep 8 chunks × 500 tokens = 4k context; generate 4k input + 400 output. Dense: OpenAI `text-embedding-3-small` **$0.02/1M**. Rerank: Voyage `rerank-2.5`. Generate placeholder: input **$0.15/1M**, output **$0.60/1M** (illustrative mini-tier — **verify live**).

### 3.1 Cost per 1k runs

**Rerank (Voyage formula, official $0.05/1M):** \(50\times 80 + 80\times 500 = 44{,}000\) tokens/query \(\times \$0.05/10^6 = \$0.0022\)/query → **$2.20 / 1k**.

**Embed (query):** \(1\mathrm{k}\times 50 = 50\mathrm{k}\) tokens \(\times \$0.02/1\mathrm{M} = \$0.001 / 1k\) (negligible vs corpus embed).

**Generate (mini placeholder):** \(4\mathrm{k}\times \$0.00015 + 400\times \$0.00024 = \$0.00084\)/query → **$0.84 / 1k**.

**[inferred] per 1k queries (no agent, no RUs, no graph):** embed $0.001 + rerank **$2.20** + generate **$0.84** ≈ **$3.04**. **Rerank dominates** this mix once generation is mini-tier; **generation dominates** on a frontier SKU ($3–15/1M out).

| Path | Rerank | Generate | **[inferred] \(C_{1k}\)** (ex-RU, ex-graph) |
| --- | --- | --- | --- |
| Voyage 2.5 + mini generate | $2.20 | $0.84 | **~$3.0** |
| Cohere search-unit (Bedrock 3.5 **$2.00/1k searches**) + 1 unit/query | $2.00 | + embed + generate | **~$2.84** if docs stay ≤500 tok; units inflate on split |
| Pinecone Inference rerank | **$2.00 / 1k req** | + generate | **~$2.84** + RUs |
| Self-host `bge-reranker-v2-m3` + mini | GPU/RAM (your bill) | $0.84 | **[inferred] well under $1** plus GPU |
| Agent 3 hops (3× retrieve+rerank + 2 extra LLM grade/rewrite) | \(\sim 3\times\) rerank | \(\times\) LLM | **[inferred] ~$7–12** on the mini+Voyage mix before frontier generate |

**Pinecone Database (official):** Standard **$16–$18 per million RUs** (region-dependent), storage **$0.33/GB/mo**, write units **$4–$4.50/M**, **$50/mo minimum**. Query cost scales with **namespace size**: 1 GB namespace → **1 RU/query**; 100 GB single namespace with metadata filter **still scans 100 GB**. Dedicated Read Nodes: provisioned, no shared read rate limits. Egress **$0.10/GB** after 100 GB. Enterprise **99.95%** uptime SLA; Starter/Builder/Standard: no uptime SLA on the public table.

**Ingest cliffs:**

- Anthropic contextualize: **$1.02 / 1M document tokens** one-time with prompt cache. 100M-token corpus → **~$102** LLM **before** embeddings.
- GraphRAG index: extraction ~**75%** of index $. LazyGraphRAG: index $ ≈ vector RAG, **0.1%** of full GraphRAG (Microsoft). Dollar cliffs like “$33k” on blogs are **scenario calculators**, not a list price — recompute from chunk count × extract prompt × model tariff.
- Batch: Voyage Batch API **33% off**, **no** free-token credit, 12h window. OpenAI embeddings Batch commonly **50% off** on 3-small/3-large (confirm live).

**Embedding ladder (official / listed, 2026-08-21):** `text-embedding-3-small` **$0.02/1M** (1536d, 8191 ctx); `3-large` **$0.13/1M** (3072d); Voyage-4-large **$0.12/1M**; voyage-4 / lite **$0.06 / $0.02**; Cohere embed-v4.0 **$0.12/1M text** on aggregators (confirm dashboard — Model Vault SKUs dominate cohere.com); Pinecone Inference llama-text-embed-v2 / e5 / sparse **$0.16 / $0.08 / $0.08 per M**. BGE-M3: self-host 569M params, ~2.27 GB, dense+sparse+ColBERT.

**Caches (hit saves):** embed `(model, dim, text_hash)`; retriever `(index_version, filter, query_hash, k)`; rerank `(reranker, query, doc_id)` — especially agent retries; Anthropic prompt cache on document prefix; community reports until reindex.

### 3.2 Latency SLA targets and mitigations

Decompose **p99 retrieve+rerank** from **p99 generate**. Circuit-break the vector DB independently of the LLM.

| Stage | What dominates | Order-of-magnitude (published or labeled) |
| --- | --- | --- |
| Query embed | Small transformer / API | Tens of ms local; **50–200 ms** hosted RTT **[inferred]** |
| Hybrid retrieve | ANN + inverted + fuse | CallSphere pgvector 10M-row **bench** (not your hardware): ef_search 40 → **~8 ms p95**; 100 → **~14 ms**; 200 → **~26 ms**; 400 → **~51 ms** |
| Cross-encoder rerank | \(N\) joint encodes + network | Voyage token-based; third-party Cohere v3.5 RTT often cited ~**600 ms** ⚠️ not Cohere SLA |
| Agent extra hop | Grade + rewrite + 2nd retrieve | **+1–3 LLM calls**; multiplies p95 if unbounded |
| Generate | Prompt = instr+chunks | Usually **>50%** of e2e $ and often of e2e latency |
| Graph global | Map over many community reports | Worst; LazyGraphRAG exists to kill this |

Working interactive RAG SLO **[inferred]** — not a vendor contract. Targets assume hybrid+rerank+mini generate, no agent, cache-cold:

| Percentile | Retrieve+rerank | E2E to first token (incl. generate TTFT) | Mitigation |
| --- | --- | --- | --- |
| **p50** | **[inferred]** 100–400 ms (embed RTT + ANN/BM25; rerank may dominate if hosted) | **[inferred]** 0.5–1.5 s if generate is non-reasoning mini | Parallel arms; RRF not sequential; embed cache |
| **p95** | **[inferred]** 0.8–2 s (hosted rerank RTT ~600 ms class + ANN tail + queue) | **[inferred]** 2–5 s | Rerank cache; lite/local bge; cap `rank_window_size`; hedge replica |
| **p99** | **[inferred]** 2–6 s+ (RU throttle, coordinator OOM, agent hop, graph global) | Separate generate p99; do not fold into retrieve SLO | Breaker → BM25-only or cached; hop cap; never global map-reduce on the interactive path |

**[inferred]** Hosted p95 retrieve+rerank is typically dominated by **rerank RTT + queue**, not HNSW, once \(N\geq 80\).

| Tier | Mitigations |
| --- | --- |
| p50 | Parallel dense∥sparse; server-side hybrid; query-embed cache; keep 8 chunks not 50 in the prompt |
| p95 | Cross-encoder cache; Voyage-lite / local bge on timeout; ES `rank_window_size` 50–100 not 10; Pinecone namespaces so RU ≠ full-corpus scan |
| p99 | Timeout 200–500 ms retrieve **[policy, not vendor SLO]**; error-rate breaker; hedge replica and cancel loser; Adaptive-RAG skip retrieve on chitchat; pin `graph_build_id` so global is an async/job path |

### 3.3 Throughput and back-pressure

**RPM that matters is loop RPM.** User 1k QPS × 3 retrieves = 3k hybrid QPS and 3k rerank QPS. Cohere Rerank production **1,000 req/min** (~16.7 rps) is a hard fuse — 1k user QPS does not fit without cache, local rerank, or shedding. Voyage/OpenAI embed RPM: org-specific dashboards. Pinecone serverless: RU/WU quotas by plan; DRN removes noisy-neighbor read limits.

OpenSearch hybrid + huge shard counts: coordinator memory; 3.5+ disables batched reduction **>512 shards**. Elasticsearch coordinating-node OOM on hybrid + huge `rank_window_size` — cap the window.

**Back-pressure design:**

1. Gateway admits only if **all** relevant breakers (dense, sparse, rerank, LLM) are closed/half-open **and** the token/RU bucket has room.
2. Bulkhead ANN pool from LLM pool — a Pinecone RU storm must not starve generate.
3. Honor 429 / RU throttle with full jitter; do not retry poison upserts.
4. Shed: drop agent rewrite first, then rerank (use fused top-8), then dense (BM25-only), then refuse. Never shed ACL.
5. Ingest vs query isolation: reindex workers use a separate embed quota and a **non-live** alias.
6. Graph global / DRIFT: queue as a job (Flex/Batch class), not the interactive pool.

**Worked capacity [inferred].** 50 interactive RAG/s, 1 retrieve, 1 Voyage rerank, mini generate: rerank **$0.0022/query** → **$0.11/s** ≈ **$9.5k/mo** on rerank alone, plus generate ~$3.6k/mo, plus RUs. Same 50 rps with 3 agent hops without cache **does not fit** Cohere’s 1k rerank RPM (need 150 rps).

### 3.4 Availability, RPO/RTO, compliance — explicit NFR trade-offs

| NFR | Working target | Tension |
| --- | --- | --- |
| Availability | 99.9% **query gateway**; Pinecone Enterprise **99.95%** if that is the index. Starter/Standard: no public uptime SLA — your SLO must include BM25-only fallback | Multi-index fallback ≠ identical ranking |
| RPO | Ingest: last checkpointed `chunk_id` / watermark (seconds–minutes). Query alias: **0** (pin complete snapshot). Graph: last `graph_build_id` | Treating mid-Leiden Parquet as live violates citation integrity |
| RTO | Interactive: fail over **<1 s** to BM25-only or retrieve cache. Graph rebuild: hours–days (schedule, do not block chat) | Fast failover vs same nDCG |
| Consistency | Weaviate RAG corpora: **QUORUM** (default; RF=6 → 4). `ONE` can cite a deleted doc. Pinecone serverless: **eventual** (upsert-then-query can miss). ES/OS replica lag = two ranks for one query | QUORUM vs ingest p99 |
| Compliance | PII-before-embed; DPA/zero-retention or self-host BGE-M3; ACL pushdown; immutable citation audit; 21 CFR 11-style provenance for regulated | Contextual Retrieval quality vs blast radius |
| Cost vs latency | Rerank ~$2/1k vs self-host bge; GraphRAG extract 75% of index $ vs Lazy 0.1% | Paying global map-reduce for FAQ |
| Recall vs RU | Namespace-per-tenant (1 GB → 1 RU) vs 100 GB filtered shared index (100× RU) | More indexes vs cheaper metadata filter |
| Consistency vs availability | Alias flip after full build vs dual-write during re-embed | Embedding drift vs downtime |

---

## 4. Distributed Resilience & Security

### 4.1 Durable ingest (Temporal / Kafka)

Research specifies an **idempotent checkpointed pipeline**, not a vendor Temporal/Kafka runbook. Map it:

**Temporal (workflow = one document or one CDC batch).** Activities: `fetch` → `sha256_store` → `parse_redact` → `acl_stamp` → `chunk` → `embed` → `sparse_encode` → `upsert_staging` → optional `graph_extract` → `community_on_closed_set` → `alias_flip`. Each activity is the checkpoint. Replay must **not** re-call the embed API for an already-keyed `embed_model+dim+chunk_id` (idempotency map). Community detect is a **barrier**: it waits until the chunk set is closed. Poison parse (repeated crash on same sha256) → DLQ workflow, not infinite retry. Query plane never reads staging.

**Kafka (log = chain of custody).** Topics per tenant-shard: `ingest.raw`, `ingest.chunks`, `ingest.vectors`, `ingest.dlq`. Produce the raw blob + watermark **before** embed (outbox). Compact on `chunk_id`. Poison (unparseable, repeated handler crash) → DLQ after N; do not block the partition. Alias flip is a **single commit message** on `ingest.control` that the query router consumes.

**Index replication (query consistency):**

- **Weaviate:** cluster metadata Raft; data objects leaderless, tunable `ONE` / `QUORUM` (default) / `ALL`. Historical: v1.17 writes were `ALL`. Hybrid under `ONE` → replica missing the latest upsert → **stale chunk in RAG**. Use `QUORUM` if you must not cite deleted docs.
- **Pinecone serverless:** object-storage-backed; you do not set RF; **eventual** at the product surface. Namespaces: Standard/Enterprise **100,000 / index**. Backups **$0.10/GB/mo**, restore **$0.15/GB**.
- **ES/OS:** primary + replica shards; hybrid fusion on the coordinating node. Replica lag = BM25 and kNN seeing different live sets.
- **pgvector:** WAL + streaming replicas; **build HNSW after bulk load**; many teams ingest to staging and swap. RLS must exist on standby.
- **GraphRAG artifacts:** crash mid-Leiden leaves entities without reports. Versioned snapshot only.

> ⚠️ Gap: research has no measured Temporal replay cost for multi-GB Parquet community tables and no Kafka lag SLO for embed workers. Treat the mapping as the enterprise shape of §3.2 in the research file.

### 4.2 Failure taxonomy

| Class | Examples | Handler |
| --- | --- | --- |
| Transient | 429, RU throttle, 503, TLS reset, rerank timeout, replica miss | Full-jitter retry on **idempotent reads**; honor Retry-After; do not retry alias_flip blindly |
| Permanent | 400 illegal filter, schema mismatch (dim change), ES hybrid nested under `function_score` | Fail the turn; fix query or route to BM25-only if policy allows |
| Poison pill | Same blob crashes the parser; recursive rewrite storm; CRAG writing web text into the corpus index | sha256 + N crashes → DLQ; hop cap; quarantine path for ingest |
| Semantic | Schema-valid retrieve with omitted ACL; hallucinated `[doc 17]`; grader false positive | PEP at vector boundary; ID-constrained cites; NLI/faithfulness; not a retry |

**Research failure table (mechanism → mitigate):** stale indexes (alias not flipped, `ONE` reads) → watermark canaries + QUORUM; embedding drift (new model/dim/Matryoshka trim) → pin model, dual-write + shadow nDCG, full re-embed; score-scale hybrid (Pinecone sparse vs dense) → `hybrid_score_norm` / RRF / explicit \(\alpha\); filter/ANN (post-filter ACL) → bitmap/IVF bypass, namespaces; over-retrieval (k=50 into 128k, 4 hops) → rerank 5–20, hop cap; hallucinated citations → constrained IDs, refuse if `ISSUP=no`; grader FN/FP → max 3 + don’t trust binary grade alone; graph explosion / community staleness → canonicalize, Lazy/LightRAG, `graph_build_id` age; poisoned ingest → signed ingest, source allowlist; OpenSearch hybrid nest / Qdrant per-shard fusion / ES window=10 — query lint.

**Idempotency key (ingest):** `chunk_id = hash(doc_id, chunker_version, text)`. Embed job: `embed_model + dim + chunk_id`. Query retries: cache key includes `index_version` + ACL principal.

### 4.3 Circuit breaker and fallback chain

Per downstream (dense ANN, BM25, rerank API, LLM, graph query):

- **Closed:** traffic flows; consecutive failures or error-rate window trips **open**.
- **Open:** fail fast; timer (e.g. 30 s). Interactive traffic takes the next fallback; ingest can wait.
- **Half-open:** one probe (or a small percentage). Success → closed; fail → open.

Retrieve timeout **200–500 ms [policy, not a vendor SLO]**. Bulkhead vs LLM pool.

**Fallback chain (research order):**

1. Last-good **retrieve cache** (`index_version`, filter, query_hash, k).
2. **BM25-only** / keyword-only (dense breaker open).
3. Skip rerank; fused top-8 (rerank breaker open).
4. `"index unavailable"` **refusal** — **never** generate ungrounded if policy forbids.

Hedging: duplicate retrieve to a replica/region on p99; cancel loser. Agent: on retrieve failure, **do not** infinite rewrite; surface `retrieval_degraded`.

### 4.4 Zero-Trust MCP, document ACL, PII, immutable logs

**Zero-Trust MCP for retrievers.** `tools/call` on a retriever is a **data exfil API**.

1. **Server-side identity.** Tenant/ACL from verified token / `RunContext`, never from tool arguments (`tenant_id` in JSON schema is a leak primitive). ABAC **before** search; chunk filter after; **predicate pushdown** so ANN never ranks cross-tenant rows (arXiv 2605.05287). Post-filter-only backends lose recall as the corpus grows.
2. **Least privilege per tool.** Separate MCP servers: `retrieve_public_kb` vs `retrieve_hr` vs `sql_customer`. No omnibus `search(query, collection)`.
3. **Stateless MCP + stateful RAG.** LangGraph `/mcp` is stateless per request; conversation memory stays in the checkpointer, **not** the MCP session.
4. **No raw chunk echo to unauthorized traces.** LangSmith/OTel redact document text at the same ACL as the user.
5. **Hosted MCP:** the provider’s network path sees queries; contract for residency.

**Document ACL.** Oracle enterprise RAG checklist: **policy travels with evidence**. Stamp at ingest; enforce as **mandatory query predicates**, not “ignore docs you shouldn’t see.”

| Pattern | Guarantee | Cost |
| --- | --- | --- |
| Metadata `tenant_id` filter | App-bug can omit filter | Cheapest; Pinecone: scans **full namespace** |
| **Namespace / collection / index per tenant** | Query cannot cross (1 GB tenant = 1 RU; 100×1 GB cheaper than 100 GB filter) | More indexes; Pinecone Standard 20 indexes/project, 100k namespaces |
| Instance / BYOC per tenant | Strongest (HIPAA/finance) | Pinecone BYOC: zero inbound SSH; PrivateLink |

Anti-pattern: `$in` of tens of thousands of user IDs (10k cap). Use groups, namespaces, or post-filter **after** a group-scoped retrieve. Delete/tombstone must match source ACL revocation; **eventual consistency** windows are a compliance bug (Weaviate `ONE` reads).

**PII pipeline:** detect → redact **before embed** → audit placeholders (never raw). After retrieve, DLP **before prompt**. Embed APIs (OpenAI/Voyage/Cohere) see plaintext — DPA, zero-retention, or self-host BGE-M3. Graph extraction **amplifies** PII into entity nodes; community reports need ACL. Contextual Retrieval **widens** blast radius.

**Audit / chain of custody (NIST SP 800-162 mapping, Secure RAG doi 10.52710/cfs.976):** PEP at the vector query boundary; PDP for ABAC; redaction gate; citation validity gate. Provenance: `source_uri`, `version`, `chunk_id`, `char_span`, `retriever` (bm25\|dense\|graph_local\|web), `rerank_score`, `user_id`, `tenant`, `index_build_id`. **Provenance fidelity** = cited IDs were (a) in the retrieved set, (b) support the claim (NLI), (c) the user was entitled to see. Hallucinated citations: constrained decode / tool-only IDs; refuse if grader `ISSUP=no`; hash-verify chunk body vs ingest sha256. Metrics: leakage rate, entitlement violation rate, provenance fidelity, false refusal. Kafka log or WORM object store; hash-chain the audit events. OWASP LLM: vector poisoning, cross-tenant namespace bugs.

---

## 5. Production Enterprise Code

Stdlib-only module: full-jitter retries, circuit breaker (closed → open → half-open), fallback chain (hybrid → cached → keyword-only → refuse), correlation-id JSON logs, PII detect→redact→audit, ACL **pre-filter from the principal** (never from model args), hybrid **RRF** merge, pluggable **rerank hook**, hop-capped agentic grade/rewrite, hash-chained audit. Run: `python rag_gateway.py`.

```python
#!/usr/bin/env python3
"""Production RAG gateway primitives (stdlib only). Run: python rag_gateway.py"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

RRF_K = 60
MAX_HOPS = 3
RETRIEVE_TIMEOUT_S = 0.4


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "index_version": getattr(record, "index_version", None),
            "breaker": getattr(record, "breaker", None),
            "degraded": getattr(record, "degraded", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class CorrelationAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs


def build_logger(correlation_id: str, tenant: str, index_version: str) -> CorrelationAdapter:
    base = logging.getLogger("rag.gateway")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    return CorrelationAdapter(
        base,
        {
            "correlation_id": correlation_id,
            "tenant": tenant,
            "index_version": index_version,
        },
    )


_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
)


def redact_pii(text: str) -> tuple[str, list[dict[str, str]]]:
    audit: list[dict[str, str]] = []
    out = text
    for label, pat in _PII_PATTERNS:
        def _sub(m: re.Match[str], _label: str = label) -> str:
            digest = hashlib.sha256(m.group(0).encode()).hexdigest()[:12]
            token = f"<{_label}:{digest}>"
            audit.append({"type": _label, "placeholder": token})
            return token

        out = pat.sub(_sub, out)
    return out, audit


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class CircuitOpenError(Exception):
    pass


class RetrievalDegraded(Exception):
    pass


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_seconds: float = 30.0,
        half_open_max: int = 1,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.half_open_max = half_open_max
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._maybe_half_open()
            return self._state

    def _maybe_half_open(self) -> None:
        if (
            self._state is BreakerState.OPEN
            and (time.monotonic() - self._opened_at) >= self.recovery_seconds
        ):
            self._state = BreakerState.HALF_OPEN
            self._half_open_inflight = 0

    def allow(self) -> None:
        with self._lock:
            self._maybe_half_open()
            if self._state is BreakerState.OPEN:
                raise CircuitOpenError("circuit open")
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_inflight >= self.half_open_max:
                    raise CircuitOpenError("half-open probe in flight")
                self._half_open_inflight += 1

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._half_open_inflight = 0
            self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = 0


def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    base_seconds: float = 0.05,
    max_seconds: float = 1.0,
    retry_after: float | None = None,
) -> Any:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            cap = min(max_seconds, base_seconds * (2**i))
            sleep_s = max(cap, retry_after or 0.0)
            time.sleep(random.random() * sleep_s)
    assert last is not None
    raise last


_TOKEN = re.compile(r"[a-z0-9]+", re.I)


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def embed(text: str, dim: int = 32) -> tuple[float, ...]:
    vec = [0.0] * dim
    for tok in tokenize(text):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
        vec[(h >> 16) % dim] -= 0.35
    return tuple(vec)


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


@dataclass(frozen=True)
class Principal:
    tenant: str
    roles: frozenset[str]
    user_id: str


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    tenant: str
    acl_roles: frozenset[str]
    text: str
    tokens: list[str]
    vector: tuple[float, ...]
    community_id: str | None = None
    sha256: str = ""


@dataclass
class ScoredChunk:
    chunk: Chunk
    score: float
    rank: int
    source: str


@dataclass
class AuditEvent:
    seq: int
    prev_hash: str
    digest: str
    payload: dict[str, Any]


class ImmutableAuditLog:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = threading.Lock()

    def append(self, payload: dict[str, Any]) -> AuditEvent:
        with self._lock:
            prev = self._events[-1].digest if self._events else "genesis"
            body = json.dumps({"prev": prev, "payload": payload}, sort_keys=True, default=str)
            digest = hashlib.sha256(body.encode()).hexdigest()
            event = AuditEvent(len(self._events) + 1, prev, digest, payload)
            self._events.append(event)
            return event

    def __len__(self) -> int:
        return len(self._events)


def acl_prefilter(chunks: list[Chunk], principal: Principal) -> list[Chunk]:
    """Hard pre-filter. Principal comes from the verified token, never tool args."""
    out: list[Chunk] = []
    for chunk in chunks:
        if chunk.tenant != principal.tenant:
            continue
        if chunk.acl_roles and chunk.acl_roles.isdisjoint(principal.roles):
            continue
        out.append(chunk)
    return out


def rrf_merge(lists: list[list[ScoredChunk]], k: int = RRF_K) -> list[ScoredChunk]:
    scores: dict[str, float] = {}
    by_id: dict[str, Chunk] = {}
    for ranking in lists:
        for item in ranking:
            scores[item.chunk.chunk_id] = scores.get(item.chunk.chunk_id, 0.0) + 1.0 / (
                k + item.rank
            )
            by_id[item.chunk.chunk_id] = item.chunk
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [
        ScoredChunk(by_id[cid], score, rank, "rrf")
        for rank, (cid, score) in enumerate(ordered, start=1)
    ]


def rerank_hook(query: str, candidates: list[ScoredChunk], top_n: int) -> list[ScoredChunk]:
    """Stand-in for a cross-encoder: query-term overlap + mild length prior."""
    q = set(tokenize(query))
    if not q:
        return candidates[:top_n]
    rescored: list[ScoredChunk] = []
    for item in candidates:
        overlap = len(q.intersection(item.chunk.tokens)) / len(q)
        length_pen = 1.0 / (1.0 + abs(len(item.chunk.tokens) - 40) / 80.0)
        rescored.append(
            ScoredChunk(item.chunk, overlap * 0.85 + length_pen * 0.15, item.rank, "rerank")
        )
    rescored.sort(key=lambda s: s.score, reverse=True)
    return [
        ScoredChunk(s.chunk, s.score, rank, "rerank")
        for rank, s in enumerate(rescored[:top_n], start=1)
    ]


def bm25_scores(
    query_tokens: list[str],
    chunks: list[Chunk],
    k1: float = 1.2,
    b: float = 0.75,
) -> list[tuple[Chunk, float]]:
    if not chunks:
        return []
    df: dict[str, int] = {}
    for chunk in chunks:
        for tok in set(chunk.tokens):
            df[tok] = df.get(tok, 0) + 1
    n = len(chunks)
    avgdl = sum(len(c.tokens) for c in chunks) / n
    scored: list[tuple[Chunk, float]] = []
    for chunk in chunks:
        tf: dict[str, int] = {}
        for tok in chunk.tokens:
            tf[tok] = tf.get(tok, 0) + 1
        score = 0.0
        dl = max(len(chunk.tokens), 1)
        for tok in query_tokens:
            f = tf.get(tok, 0)
            if f == 0:
                continue
            n_q = df.get(tok, 0)
            idf = math.log(1.0 + (n - n_q + 0.5) / (n_q + 0.5))
            denom = f + k1 * (1.0 - b + b * dl / avgdl)
            score += idf * (f * (k1 + 1.0)) / denom
        scored.append((chunk, score))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored


class HybridIndex:
    def __init__(self, chunks: list[Chunk], index_version: str) -> None:
        self.chunks = chunks
        self.index_version = index_version

    def dense_search(self, query: str, principal: Principal, k: int) -> list[ScoredChunk]:
        allowed = acl_prefilter(self.chunks, principal)
        qv = embed(query)
        ranked = sorted(allowed, key=lambda c: cosine(qv, c.vector), reverse=True)[:k]
        return [ScoredChunk(c, cosine(qv, c.vector), i, "dense") for i, c in enumerate(ranked, 1)]

    def sparse_search(self, query: str, principal: Principal, k: int) -> list[ScoredChunk]:
        allowed = acl_prefilter(self.chunks, principal)
        scored = bm25_scores(tokenize(query), allowed)
        return [ScoredChunk(c, s, i, "bm25") for i, (c, s) in enumerate(scored[:k], 1) if s > 0]

    def keyword_only(self, query: str, principal: Principal, k: int) -> list[ScoredChunk]:
        return self.sparse_search(query, principal, k)


class RetrieverCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, list[ScoredChunk]] = {}

    def key(self, index_version: str, tenant: str, query: str, k: int) -> str:
        raw = f"{index_version}|{tenant}|{query}|{k}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key: str) -> list[ScoredChunk] | None:
        with self._lock:
            hit = self._store.get(key)
            return list(hit) if hit is not None else None

    def put(self, key: str, value: list[ScoredChunk]) -> None:
        with self._lock:
            self._store[key] = list(value)


@dataclass
class QueryResult:
    chunks: list[ScoredChunk]
    mode: str
    hops: int
    refused: bool
    pii_audit: list[dict[str, str]]
    citations: list[str]


class RagGateway:
    def __init__(
        self,
        index: HybridIndex,
        *,
        dense_breaker: CircuitBreaker | None = None,
        sparse_breaker: CircuitBreaker | None = None,
        rerank_breaker: CircuitBreaker | None = None,
        ungrounded_ok: bool = False,
        reranker: Callable[[str, list[ScoredChunk], int], list[ScoredChunk]] = rerank_hook,
    ) -> None:
        self.index = index
        self.dense_breaker = dense_breaker or CircuitBreaker()
        self.sparse_breaker = sparse_breaker or CircuitBreaker()
        self.rerank_breaker = rerank_breaker or CircuitBreaker()
        self.ungrounded_ok = ungrounded_ok
        self.reranker = reranker
        self.cache = RetrieverCache()
        self.audit = ImmutableAuditLog()

    def _arm(
        self,
        breaker: CircuitBreaker,
        fn: Callable[[], list[ScoredChunk]],
        fail_open: bool,
    ) -> list[ScoredChunk]:
        try:
            breaker.allow()
        except CircuitOpenError:
            if fail_open:
                return []
            raise

        def wrapped() -> list[ScoredChunk]:
            started = time.monotonic()
            result = fn()
            if time.monotonic() - started > RETRIEVE_TIMEOUT_S:
                raise TransientError("retrieve timeout")
            return result

        try:
            result = retry_call(wrapped)
            breaker.record_success()
            return result
        except (TransientError, PermanentError, CircuitOpenError):
            breaker.record_failure()
            if fail_open:
                return []
            raise

    def hybrid(self, query: str, principal: Principal, k: int = 8) -> tuple[list[ScoredChunk], str]:
        dense = self._arm(
            self.dense_breaker,
            lambda: self.index.dense_search(query, principal, k),
            fail_open=True,
        )
        sparse = self._arm(
            self.sparse_breaker,
            lambda: self.index.sparse_search(query, principal, k),
            fail_open=True,
        )
        if dense and sparse:
            fused, mode = rrf_merge([dense, sparse]), "hybrid"
        elif sparse:
            fused, mode = sparse, "keyword"
        elif dense:
            fused, mode = dense, "dense"
        else:
            raise RetrievalDegraded("both retrieval arms failed")
        try:
            self.rerank_breaker.allow()
            reranked = self.reranker(query, fused, min(k, 8))
            self.rerank_breaker.record_success()
            return reranked, mode
        except CircuitOpenError:
            return fused[: min(k, 8)], mode
        except Exception:
            self.rerank_breaker.record_failure()
            return fused[: min(k, 8)], mode

    def retrieve_with_fallback(
        self,
        query: str,
        principal: Principal,
        log: CorrelationAdapter,
        k: int = 8,
    ) -> tuple[list[ScoredChunk], str]:
        cache_key = self.cache.key(self.index.index_version, principal.tenant, query, k)
        try:
            hits, mode = self.hybrid(query, principal, k)
            self.cache.put(cache_key, hits)
            degraded = self.dense_breaker.state is BreakerState.OPEN or (
                self.sparse_breaker.state is BreakerState.OPEN
            )
            log.info(
                "retrieve_ok",
                extra={"degraded": degraded, "breaker": self.dense_breaker.state.value},
            )
            return hits, mode
        except (RetrievalDegraded, CircuitOpenError, TransientError) as exc:
            cached = self.cache.get(cache_key)
            if cached:
                log.info("fallback_cache", extra={"degraded": True})
                return cached, "cache"
            try:
                self.sparse_breaker.allow()
                kw = self.index.keyword_only(query, principal, k)
                self.sparse_breaker.record_success()
                if kw:
                    log.info("fallback_keyword", extra={"degraded": True})
                    return kw, "keyword"
            except CircuitOpenError:
                pass
            if self.ungrounded_ok:
                log.info("fallback_ungrounded", extra={"degraded": True})
                return [], "ungrounded"
            log.info("fallback_refuse", extra={"degraded": True})
            raise RetrievalDegraded("index unavailable") from exc

    def grade(self, query: str, hits: list[ScoredChunk]) -> bool:
        q = set(tokenize(query))
        if not hits or not q:
            return False
        return any(len(q.intersection(h.chunk.tokens)) / len(q) >= 0.25 for h in hits)

    def answer(self, query: str, hits: list[ScoredChunk]) -> str:
        if not hits:
            return "insufficient evidence"
        citations = ", ".join(h.chunk.chunk_id for h in hits[:3])
        snippet = hits[0].chunk.text[:180]
        return f"{snippet} [cites: {citations}]"

    def run(
        self,
        query: str,
        principal: Principal,
        *,
        agentic: bool = False,
        correlation_id: str | None = None,
    ) -> QueryResult:
        cid = correlation_id or str(uuid.uuid4())
        redacted, pii_audit = redact_pii(query)
        log = build_logger(cid, principal.tenant, self.index.index_version)
        hops = 0
        current = redacted
        last_hits: list[ScoredChunk] = []
        last_mode = "none"
        while True:
            try:
                last_hits, last_mode = self.retrieve_with_fallback(current, principal, log)
            except RetrievalDegraded:
                self.audit.append(
                    {
                        "correlation_id": cid,
                        "tenant": principal.tenant,
                        "user_id": principal.user_id,
                        "decision": "refuse",
                        "pii": pii_audit,
                        "chunk_ids": [],
                    }
                )
                return QueryResult([], last_mode, hops, True, pii_audit, [])
            if not agentic:
                break
            hops += 1
            if self.grade(current, last_hits) or hops >= MAX_HOPS:
                break
            current = current + " " + " ".join(tokenize(current)[:4])
            log.info("rewrite_hop", extra={"degraded": False})
        citations = [h.chunk.chunk_id for h in last_hits]
        text = self.answer(redacted, last_hits)
        self.audit.append(
            {
                "correlation_id": cid,
                "tenant": principal.tenant,
                "user_id": principal.user_id,
                "decision": "generate",
                "mode": last_mode,
                "hops": hops,
                "pii": pii_audit,
                "chunk_ids": citations,
                "answer_hash": hashlib.sha256(text.encode()).hexdigest()[:16],
            }
        )
        return QueryResult(last_hits, last_mode, hops, False, pii_audit, citations)


def _chunk(doc_id: str, tenant: str, roles: set[str], text: str, community: str | None = None) -> Chunk:
    redacted, _ = redact_pii(text)
    digest = hashlib.sha256(redacted.encode()).hexdigest()
    cid = hashlib.sha256(f"{doc_id}|v1|{redacted}".encode()).hexdigest()[:16]
    return Chunk(
        chunk_id=cid,
        doc_id=doc_id,
        tenant=tenant,
        acl_roles=frozenset(roles),
        text=redacted,
        tokens=tokenize(redacted),
        vector=embed(redacted),
        community_id=community,
        sha256=digest,
    )


def _demo() -> None:
    corpus = [
        _chunk("sku", "acme", {"support"}, "Error TS-999 means the valve is stuck. Reset SKU-441.", "ops"),
        _chunk("refund", "acme", {"support"}, "Refund SLA is five business days after ticket close.", "ops"),
        _chunk("hr", "acme", {"hr"}, "Headcount plan Q3 secret: hire 40 in Dublin.", "hr"),
        _chunk("other", "globex", {"support"}, "Globex TS-999 is unrelated firmware.", "ops"),
        _chunk("pii", "acme", {"support"}, "Contact Jane 123-45-6789 or jane@acme.test about TS-999.", "ops"),
    ]
    gw = RagGateway(HybridIndex(corpus, "idx-1"))
    support = Principal("acme", frozenset({"support"}), "u-support")
    hr = Principal("acme", frozenset({"hr"}), "u-hr")

    sku = gw.run("what does TS-999 mean for SKU-441?", support)
    assert sku.citations and not sku.refused
    assert all(c.chunk.tenant == "acme" for c in sku.chunks)
    assert all("hr" not in c.chunk.acl_roles or c.chunk.acl_roles & support.roles for c in sku.chunks)

    leaked = gw.run("Dublin headcount plan", support)
    assert all(c.chunk.doc_id != "hr" for c in leaked.chunks)

    hr_hit = gw.run("Dublin headcount plan", hr)
    assert any(c.chunk.doc_id == "hr" for c in hr_hit.chunks)

    pii = gw.run("email jane@acme.test about 123-45-6789 and TS-999", support)
    assert pii.pii_audit and not any("jane@acme.test" in h.chunk.text for h in pii.chunks)

    gw.dense_breaker.record_failure()
    gw.dense_breaker.record_failure()
    gw.dense_breaker.record_failure()
    assert gw.dense_breaker.state is BreakerState.OPEN
    degraded = gw.run("Refund SLA business days", support)
    assert degraded.mode == "keyword"
    assert not degraded.refused

    gw2 = RagGateway(HybridIndex(corpus, "idx-1"), ungrounded_ok=False)
    gw2.dense_breaker.record_failure()
    gw2.dense_breaker.record_failure()
    gw2.dense_breaker.record_failure()
    gw2.sparse_breaker.record_failure()
    gw2.sparse_breaker.record_failure()
    gw2.sparse_breaker.record_failure()
    empty = HybridIndex([], "idx-empty")
    gw2.index = empty
    refused = gw2.run("anything", support)
    assert refused.refused

    agentic = gw.run("valve stuck reset policy", support, agentic=True)
    assert agentic.hops <= MAX_HOPS
    assert len(gw.audit) >= 1
    print(
        json.dumps(
            {
                "sku_mode": sku.mode,
                "sku_cites": sku.citations,
                "hr_blocked_for_support": all(c.chunk.doc_id != "hr" for c in leaked.chunks),
                "hr_allowed": any(c.chunk.doc_id == "hr" for c in hr_hit.chunks),
                "pii_placeholders": pii.pii_audit,
                "dense_open_mode": degraded.mode,
                "refused": refused.refused,
                "agentic_hops": agentic.hops,
                "audit_chain_head": gw.audit._events[0].digest[:12],
                "rrf_k": RRF_K,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    _demo()
```

**What this encodes that interviews probe:** (1) ACL is applied **before** both arms, from the token principal. (2) RRF \(k=60\) merges incompatible score spaces. (3) Rerank is a hook you can drop when the breaker opens (fused top-8). (4) Fallback is cache → keyword → refuse, never ungrounded by default. (5) Agentic rewrite is hop-capped. (6) PII never enters the embed/index path in the raw form. (7) Audit events hash-chain.

Extract the fence to a file and run `python rag_gateway.py` in an interview dry-run; `_demo` asserts tenant isolation, PII placeholders, dense-open degradation, and refuse-when-empty.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers and component choices are from the research file.

### Scenario 1 — Multi-tenant SaaS knowledge base (10–100M chunks)

**Problem statement.** Design a multi-tenant support copilot over 10–100M chunks: SKU/error-code queries (`TS-999`) mixed with paraphrase, **tenant isolation** (SOC2), p95 chat of a few seconds, no global “themes in the corpus” requirement. Peak **50 QPS** interactive. Must not scan a 100 GB shared namespace per query. Contextual pronoun misses show up in eval. GraphRAG is on the table because a VP saw a Microsoft blog.

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Ticket UI  │ SSE │ CONTROL PLANE                                             │
│ / Slack    │────▶│ Gateway: auth, tenant TPM, loop RPM, correlation-id       │
└────────────┘     │ Policy: PII redact, ACL from token, tool = retrieve_kb    │
                   │ Router: Adaptive-RAG — chitchat skip; else hybrid+rerank  │
                   │ Orchestrator: max hops = 0 on FAQ path (1 retrieve)       │
                   │ pin index_version; dense/sparse/rerank breakers           │
                   └────┬──────────────────────────────┬───────────────────────┘
                        │                              │
                        ▼                              ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ DATA PLANE       │        │ TOOL PROXY                   │
                   │ ns-per-tenant    │        │ MCP retrieve_public_kb       │
                   │ BM25 ∥ dense k80 │        │ ticket: tenant, expiry       │
                   │ RRF k=60         │        │ no tenant_id in tool schema  │
                   │ Voyage/bge 80→8  │        │                              │
                   └────────┬─────────┘        └──────────────────────────────┘
                            │
                            ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE / INGEST                                      │
                   │ Kafka ingest.raw → Temporal chunk/embed/upsert staging    │
                   │ alias flip; QUORUM or Pinecone ns; parent-doc pointers    │
                   │ TELEMETRY: retrieve vs generate p99, entitlement rate     │
                   └───────────────────────────────────────────────────────────┘
```

**Technology choices.** Namespace-per-tenant (Pinecone) or RLS+HNSW until ~a few million chunks/tenant, then `pg_search`/Qdrant/ES. Hybrid BM25+dense with **RRF** (avoid Pinecone unbounded-sparse trap; if single-index hybrid, set `hybrid_score_norm` and \(\alpha=0.25\) on SKU-heavy corpora). Rerank N=80→8 (Cohere/Voyage/bge). **No** GraphRAG. Promote to Anthropic Contextual BM25 when eval shows orphan figures (ingest **$1.02/1M** doc tokens). ACL pre-filter only. Rerank **[inferred] ~$2/1k** at 1 search-unit/query; reject metadata-filter-only 100 GB shared index (100× RU). Fallback: cache → BM25-only → refuse.

**Trade-off evaluation matrix.**

| Dimension | A. One 100 GB index + metadata `tenant_id` post-filter, dense-only, no rerank | B. Recommended: namespace-per-tenant + hybrid RRF + cross-encoder 80→8, no graph | C. Full GraphRAG Leiden + global search on every turn |
| --- | --- | --- | --- |
| Cost | RU scans **100 GB**/query; cheap ops until the bill; no rerank $ | RUs scale with **hot tenant GB** (1 GB → 1 RU). Rerank **[inferred] ~$2/1k** + mini generate **~$0.84** → **~$3/1k** ex-RU. Contextualize optional ~$102/100M tok | Extract **~75%** of index $; global map-reduce ≫ hybrid query $ |
| Latency | ANN over a huge filtered set; post-filter recall collapse; p95 **[inferred]** worse than ns | Predictable 2-stage; p95 **[inferred]** 2–5 s e2e if generate is mini; rerank RTT is the knob | Global: worst p99; DRIFT multi-pass |
| Ops | One index; embedding drift is a fleet event | More namespaces (limit 100k); ingest alias discipline | Graph snapshot versioning; microsoft/graphrag **maintenance mode** |
| Security | App-bug omits filter → cross-tenant; post-filter fills top-k with forbidden hits | Query cannot cross ns; PEP on token; MCP tool split | ACL must cover **reports** too; larger PII graph blast |
| Scalability | 100M chunks in one ns is the anti-pattern Pinecone docs warn | Horizontal by tenant; escalate off pgvector at a few million/tenant **[inferred]** | Entity explosion; weekly Leiden vs document flux |

**Decision rationale.** **B** is the only option that hits SKU recall (BM25), paraphrase (dense), precision (rerank), tenant RU math, and the p95 chat budget. A fails isolation and RU economics and ID queries. C pays GraphRAG for a query class vector RAG already wins (single-hop FAQ; arXiv 2502.11371v3). Router keeps hops at 0 for this SKU. If eval later shows global QFS, add LazyGraphRAG as a **separate job path**, not on the interactive fuse.

### Scenario 2 — Regulated multi-hop research (pharma / legal) with optional global QFS

**Problem statement.** “Compare trial X vs Y across protocols”; citation **spans**; 21 CFR 11-style audit; CRAG **must not** open-web the confidential query. A second query class appears at exec review: “what changed this quarter across 10k docs” (global QFS). Volume: low hundreds of concurrent researchers, not 50 QPS chat. Wrong answers with invented `[doc 17]` are a legal incident. Entity explosion from unconstrained LLM NER is in-scope.

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Scientist  │────▶│ CONTROL PLANE                                             │
│ / counsel  │     │ Gateway: SSO, correlation-id, breaker, 21 CFR 11 session  │
└────────────┘     │ Policy: PII-before-embed; CRAG allowlist = licensed corpus│
                   │ Router: multi-hop → agent N≤2; global QFS → Lazy/DRIFT job│
                   │ Orchestrator: ID-constrained cites; ISSUP refuse          │
                   └────┬─────────────────────────────┬────────────────────────┘
                        │                             │
                        ▼                             ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ DATA PLANE       │        │ TOOL PROXIES                 │
                   │ hybrid RRF +     │        │ retrieve_protocol (MCP)      │
                   │ HippoRAG PPR or  │        │ kg_traverse (ontology NER)   │
                   │ agent 2-hop      │        │ NO web MCP on this principal │
                   │ rerank 80→8      │        │ HITL on new graph edges      │
                   │ LazyGraphRAG Z500│        │                              │
                   │  as async job    │        │                              │
                   └────────┬─────────┘        └──────────────────────────────┘
                            │
                            ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE                                               │
                   │ Versioned graph_build_id + vector alias; WORM audit hash  │
                   │ chunk_id+char_span+sha256; Temporal ingest quarantine     │
                   └───────────────────────────────────────────────────────────┘
```

**Technology choices.** Hybrid retrieve + **HippoRAG-style PPR** (paper: ~20% multi-hop lift; 10–20× cheaper / 6–13× faster than IRCoT **in their experiments**) **or** agent 2-hop with an IRCoT cap. Graph edges from **controlled** NER (ontology), not unconstrained LLM entities. Citations = `chunk_id+offsets` only. Human review on new graph edges. For the quarterly QFS class: **LazyGraphRAG** or FastGraphRAG + vector hybrid for local; do **not** re-Leiden daily on GPT-4-class extract. If Microsoft GraphRAG OSS is used, pin a weekly snapshot; serve DRIFT for mid-range questions. LightRAG if incremental entity updates matter more than community reports. Cost control: Microsoft Lazy index ~ vector; global quality at **≪** full map-reduce (**700×** query $ claim is **Microsoft-stated** on their mix — re-run **your** LLM-as-judge on **your** corpus).

**Trade-off evaluation matrix.**

| Dimension | A. Agentic IRCoT 4–6 hops + open-web CRAG + LLM-as-100-way reranker | B. Recommended: hybrid + HippoRAG/PPR or agent N≤2 + ontology graph; LazyGraphRAG **job** for global; cross-encoder 80→8; no open web | C. Full Microsoft GraphRAG Leiden global on every researcher turn |
| --- | --- | --- | --- |
| Cost | \(\times\) hops LLM + retrieve; LLM rerank dwarfs cross-encoder; web SKU extra | Local ≈ hybrid **[inferred] ~$3/1k** + PPR CPU. Lazy index ≈ vector (**0.1%** of full GraphRAG). Global only when routed | Extract **75%** of index $; global map-reduce on every turn |
| Latency | Fat-tail p99; HippoRAG paper: IRCoT **6–13×** slower than PPR **in their experiments** | Interactive local p95 **[inferred]** few seconds; global QFS is async | Global: worst; DRIFT multi-pass (default 2 local iterations) |
| Ops | Unbounded loop; web exfil incident response | Hop fuse; graph snapshot weekly; HITL on edges; OSS GraphRAG = algorithm fork | Maintenance-mode GitHub; community staleness vs weekly flux |
| Security | Confidential query to the public web; hallucinated cites; tool omnibus | CRAG only to **licensed** corpus; PEP/PDP; citation IDs; ACL on nodes **and** reports; WORM audit | Reports can summarize secrets unless ACL’d; PII in entity nodes |
| Scalability | Loop QPS blows Cohere 1k RPM | PPR single-step multi-hop scales with KG size; Lazy budget Z100/Z500 | Entity/degree explosion; indexing time (GraphRAG-Bench: HippoRAG maps can be longest — measure) |

**Decision rationale.** **B** is the only option that simultaneously (1) solves multi-hop without an open proxy, (2) keeps citations as retrieved spans, (3) pays graph structure only where vector RAG fails, and (4) isolates global QFS onto a budgeted Lazy/DRIFT path. A fails 21 CFR 11 (web + invented cites + unbounded hops). C fails the interactive path and the maintenance-mode product risk; use Leiden **weekly** for exec decks if Lazy quality is insufficient on **your** judge, not as the default retrieve tool. Interview close: “Recall first (hybrid RRF), precision second (cross-encoder), loop third (capped retrieve-as-tool), graph last (Lazy/Hippo/Light before naive full GraphRAG), security always (pushdown ACL, citation IDs, PII-before-embed).”

---

*End of module. Six sections. Four topics (hybrid search, reranking, Agentic RAG, Graph RAG). Token `$ / 1k` tables are **[inferred]** from the stated reference query and list prices dated 2026-08-21. No unpublished RAG e2e p50/p95/p99 SLOs — percentiles are labeled **[inferred]** or cited to named benches/vendor RTT anecdotes.*
