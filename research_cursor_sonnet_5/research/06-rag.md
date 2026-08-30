# Research: RAG — Hybrid Search, Reranking, Agentic RAG, Graph RAG

**Date researched**: 2026-08-21
**Sources consulted**: 41 (20 web searches; primary official docs, engineering blogs, and papers cited inline)

## 1. System Topology & Mechanics

### 1.1 Hybrid search fusion mechanics (dense + sparse/BM25)

**Reciprocal Rank Fusion (RRF)** is the dominant fusion mechanism across the industry because it fuses *rank positions*, not raw scores, sidestepping the incompatibility between BM25 scores (unbounded, implementation/corpus-dependent) and cosine similarity (bounded `[-1, 1]`) [7][8].

- Formula: `RRF_score(d) = Σ 1/(k + rank_i(d))` across each ranked list `i`, with `k` (rank constant) typically **60** as a default across Elasticsearch, OpenSearch, and most implementations [7][9].
- Elasticsearch: `rrf` retriever combines ≥2 child retrievers (e.g., a `standard` BM25 retriever + a `knn` retriever), with a `rank_window_size` and `rank_constant` parameter [1].
- OpenSearch 2.19+: Neural Search plugin's hybrid pipeline supports `score-ranker-processor` with `technique: rrf`, default `rank_constant: 60`. On BEIR-style datasets, "Hybrid with RRF" outperformed plain BM25 and plain neural search on most corpora (e.g., FIQA: BM25 0.239 vs Hybrid+RRF 0.247 NDCG@10; Quora: BM25 0.742 vs Hybrid+RRF 0.796) [9].
- **Vendor implementations of fusion**:
  - **Pinecone**: single-index "vector-API hybrid" pattern stores dense + sparse vectors per record; weighting via `alpha` (default 0.5); sparse scores are **not normalized** to dense range by default — production deployments must apply `hybrid_score_norm` or risk the sparse component dominating [2][3][5].
  - **Weaviate**: runs BM25 and vector search in parallel, fuses via two selectable algorithms — `rankedFusion` (position-only, legacy default pre-v1.24) and `relativeScoreFusion` (min-max normalizes each list to `[0,1]` before weighted summing; default since v1.24). Alpha parameter: `0`=pure keyword, `0.75`=default, `1`=pure vector [4][6].
  - **Qdrant**: `query_points` API accepts a `Prefetch` list (dense + sparse legs) and a top-level `FusionQuery(fusion=Fusion.RRF)`; also supports **DBSF** (distribution-based score fusion) as an alternative fusion function [11][12].
- **Fusion function research caveat**: An empirical analysis (HF paper 2210.11934) found RRF is *sensitive to its parameters* and generalizes worse **out-of-domain** than a tuned convex combination (CC) of normalized scores — contradicting the "RRF is parameter-free and always safe" folk wisdom. CC requires only a small tuning set but outperforms RRF in-domain and out-of-domain in their experiments [10]. `> ⚠️ This directly conflicts with some vendor marketing that frames RRF as strictly superior — treat as a nuanced trade-off, not settled science.`

### 1.2 Reranking pipeline placement

Universal pattern across vendors: **retrieve wide (top-50 to top-500) → fuse (RRF) → rerank narrow (top-10 to top-20) → generate**. Rerankers are placed as a **second-stage precision layer** operating only on the fused candidate shortlist — never on the full index — because cross-encoders are O(candidates) in compute cost and too slow for full-corpus scoring [7][11].

- **Qdrant multi-stage retrieval**: dense + sparse prefetch (limit 500 each) → fuse → **late-interaction (ColBERT) rerank** as the final `query` stage. ColBERT multivector fields should have HNSW disabled (`m=0`) since they're used for reranking (exhaustive comparison on a small candidate set), not ANN search [11][13].
- Any retrieval mechanism can double as a reranking mechanism (e.g., prefetch with sparse, rerank with dense; or Matryoshka-embedding oversampling then progressive dimensionality reduction) [12].

### 1.3 Agentic RAG loop topology

Agentic RAG replaces the fixed "retrieve-once" pipeline with an LLM-driven **control loop**: plan → retrieve/act → observe/grade → decide (retrieve again, reformulate, or answer) [17][18][19][20].

Canonical topologies (per SoK survey, arXiv 2603.07379) [18]:
- **Loop-based** (Self-RAG, CRAG): single agent self-reflects mid-generation using special "reflection tokens" to decide when to retrieve or critique its own draft (Self-RAG); CRAG adds a lightweight retrieval-quality evaluator (correct/incorrect/ambiguous) that triggers query rewriting or web search fallback on low confidence [17].
- **Interleaved reasoning** (IRCoT, Iter-RetGen): retrieval interleaved with chain-of-thought steps; each reasoning step conditions the next retrieval; Iter-RetGen alternates "use generation to refine retrieval" and "use retrieval to improve generation" [17][19].
- **Confidence-triggered** (FLARE, DRAGIN): retrieval fires only when token-level generation confidence/entropy drops below a threshold, rather than on a fixed schedule [17][20].
- **Tree/hierarchical** (RAPTOR): recursive summarization tree built over chunks at index time, enabling multi-hop reasoning without a live agentic loop [17].
- The SoK paper formalizes the agentic retrieval-generation loop as a **finite-horizon partially observable Markov decision process (POMDP)**, modeling control policy and state transitions explicitly — a useful mental model for interview framing [18].

### 1.4 GraphRAG indexing/query topology (Microsoft GraphRAG)

**Indexing pipeline** [13][14][15][16]:
1. Slice corpus into **TextUnits** (chunks) for fine-grained provenance.
2. LLM-based extraction of entities, relationships, and claims/covariates from each TextUnit.
3. Build a graph (nodes = entities, edges = relationships, weighted by normalized relationship-instance counts).
4. **Hierarchical community detection via the Leiden algorithm** (improves on Louvain) — recursively partitions the graph into nested communities of densely-interconnected nodes, producing multiple levels of abstraction (root = broad themes, leaf = fine detail) [14][15].
5. LLM generates a **community report** (summary) per community, bottom-up.

**Query-time modes** [13][15]:
- **Global search**: for holistic/thematic queries (e.g., "catch me up on the last two weeks"). Uses a **map-reduce over community reports**: map step generates intermediate answers per report, reduce step aggregates. An improved variant uses **dynamic community selection**: an LLM rates each community report's relevance top-down, pruning irrelevant subtrees before the map-reduce, cutting cost [14].
- **Local search**: for entity-specific queries — finds relevant entities via embedding similarity, expands to their community members, adds community reports, and merges with raw text units.
- **DRIFT search**: hybrid — primer (global-style community context) + local search follow-up, building a question/sub-question tree.

## 2. Token Economics & NFR Metrics

### 2.1 Embedding costs

| Model | Standard $/1M tok | Batch $/1M tok | Dim | MTEB Retrieval (NDCG@10) |
|---|---|---|---|---|
| OpenAI text-embedding-3-small | $0.02 | $0.01 | 1536 | ~59 (approx, per aggregate) |
| OpenAI text-embedding-3-large | $0.13 | $0.065 | 3072 | ~59–62 |
| OpenAI ada-002 (legacy) | $0.10 | $0.05 | 1536 | lower, deprecated |
| Cohere Embed v4 | $0.12 | — | — | ~61 |
| Voyage-3.5 / voyage-4 | $0.06 | — | — | ~57.5–66 |
| Voyage-4-large | $0.12–0.18 | — | — | ~66 |
| Gemini Embedding 2 (Mar 2026) | $0.20 ($0.10 batch) | — | 3072 (Matryoshka to 768) | **67.71 (SOTA, Jul 2026)** |
[21][31]

Cost example: embedding 10,000 docs × 500 tokens (5M tokens) costs **$0.10** (3-small standard) vs **$0.65** (3-large standard) — a 6.5× premium for roughly a 4-point MTEB lift, justified mainly for legal/medical/high-stakes retrieval [21].

### 2.2 Reranker costs & latency

**Cohere Rerank 3.5**: $2.00 per 1,000 searches ($0.002/search); one "search" = one query + up to 100 documents; documents >500 tokens are auto-chunked, each chunk billed as a separate document [26][27]. Latency (OCI benchmark, single RERANK_COHERE unit) [28]:

| # Documents | Latency (s) | Throughput (RPS) |
|---|---|---|
| 1 | 0.13 | 7.64 |
| 24 | 0.12–0.20 | 4.8–8.3 |
| 48 | 0.14–0.73 | 1.3–7.2 |
| 96 (64 tok/doc) | 0.17 | 5.86 |
| 96 (4096 tok/doc) | **7.35** | 0.14 |

Latency scales strongly with document token length, not just count — a >40× latency spread between short (64-tok) and long (4096-tok) documents at the same batch size [28].

**Voyage AI rerankers**: token-based pricing — `rerank-2.5`: $0.05/1M tokens; `rerank-2.5-lite`: $0.02/1M tokens; first 200M tokens free per account. Total tokens = `(query_tokens × num_docs) + Σ(doc_tokens)`, capped at 600K tokens/request (300K for legacy rerank-1). Voyage recommends `rerank-2.5-lite` and ≤200K tokens/request for latency-sensitive apps [22][23][24][25].

### 2.3 Cost of agentic multi-hop retrieval vs single-shot

| Dimension | Classic (single-shot) RAG | Agentic RAG |
|---|---|---|
| Retrieval calls | 1 (fixed) | 2–7 (agent-decided) |
| Latency | 1–3s | 10–60s |
| Token cost | Baseline | **3–10× baseline** |
| Failure mode | Silent context miss | Runaway cost / loop non-termination |
[35]

Contextual Retrieval (Anthropic) cost overhead: **+$12 per 1,000 documents** at indexing time (with prompt caching for the situating-context generation step) and **+$0.03/query** for reranking — a comparatively small marginal spend for a 35–67% reduction in retrieval failure rate (see §5) [40][41].

### 2.4 Vector DB query latency / throughput benchmarks

**Milvus 2.2.0** (1M SIFT vectors, HNSW M=8, efConstruction=200) [30]:
| CPU cores | Concurrency | QPS | P99 (ms) | P50 (ms) |
|---|---|---|---|---|
| 8 | 500 | 7,153 | 127 | 83 |
| 16 | 600 | 14,135 | 85 | 42 |
| 32 | 600 | 20,281 | 63 | 28 |

Linear QPS scaling was observed both when scaling CPU cores (8→32) and query-node replicas (1→8: 7,153 → 30,655 QPS) [30].

**VectorDBBench (Zilliz) leaderboard**, Cohere-1M dataset, $1,000/month cost tier: Milvus/Zilliz variants reach ~5,900–9,600 QPS at 2.3–2.4ms P99; Pinecone p2.x8 reaches ~1,131–1,300 QPS at ~6.5–13.7ms P99 [29]. `> ⚠️ Exact cross-vendor QPS comparisons are highly configuration- and dataset-dependent; treat as directional, not universal truth.`

**pgvector HNSW**: index memory footprint ≈ **20–25 KB per 1536-dim vector** including graph overhead (vs. 6 KB for raw floats) — 10M rows requires 200–250 GB of RAM to keep the index memory-resident; falling back to disk degrades latency from milliseconds to seconds [39][38]. Recommended tuning: `m=16` (default), `ef_construction=128–200`, `ef_search=40–200` as the per-query recall/latency dial [38][39]. `float16` (halfvec, pgvector 0.7.0+) halves memory with negligible recall loss and up to 3× faster HNSW builds on ARM [39].

**Enterprise case study (10M documents, Fortune 500 financial services)** — end-to-end P50/P95 latency budget [32]:
| Stage | P50 | P95 |
|---|---|---|
| Query embedding | 8ms | 12ms |
| Vector search | 15ms | 25ms |
| Reranking | 20ms | 35ms |
| LLM generation | 45ms | 70ms |
| **Total** | **88ms** | **142ms** |
Peak QPS: 850 [32].

## 3. Distributed Resilience & State

### 3.1 Durable execution for multi-step agentic retrieval

**Temporal** is the reference pattern for durable agentic-RAG loops [33][34]:
- The **Workflow** is deterministic and holds conversation/loop state; it never calls the LLM, retriever, or reranker directly — it schedules **Activities** for all non-deterministic I/O (LLM calls, vector DB queries, tool calls) and awaits results.
- Every Activity's input/output is recorded in an immutable **Event History**. On worker crash, Temporal **replays** the history — already-completed LLM/retrieval calls are not re-executed, only resumed from the last completed step, eliminating duplicate spend and non-deterministic re-planning [33][34].
- For long-running agentic sessions, use **`continue_as_new`** after N turns to cap unbounded event-history growth [34].
- Branch control flow on the API's structured **stop_reason** (e.g., `tool_use` vs `end_turn`), not on parsing model prose — this is the deterministic contract the Workflow replays against [Bounded Agentic Loop pattern, from agentic-loop guardrail research].

### 3.2 Index update consistency & concurrent writes

- Vector indexes behave like **caches with no built-in invalidation strategy** — an embedding is frozen at creation time and nothing in the vector index distinguishes a stale chunk from a fresh one; relevance-as-cosine-similarity and freshness are orthogonal dimensions the index doesn't encode [36].
- Recommended pattern: **Change Data Capture (CDC)** from the source system (DB WAL, webhook, file-hash diff) drives **incremental re-embedding** — cost scales with change rate, not corpus size [36][37].
- **Stable chunk IDs + content hash per chunk** are required so an edit to one sentence in a 40-page document re-embeds only the affected chunk(s), not the whole document, and so stored citations don't silently repoint to different text after an ID-shift bug [36][37].
- **Full re-embed as a "fix"** is an anti-pattern at scale: for hundreds of thousands of chunks it costs real money, takes hours, and leaves the index in a **non-deterministic partially-stale state** for the run's duration — a query mid-run can straddle old and new embeddings [36].
- **Deletion propagation** is harder than edits: a deleted source document produces no competing new embedding — the stale vector simply lingers and keeps matching queries. Recommended: maintain a manifest/side-table of valid chunk IDs per document, compute set differences, and purge orphans; update the manifest **after** the store acknowledges both index and delete operations so crash-consistency favors under-claiming (repairable) over over-claiming (unrecoverable without a scan) [37].

### 3.3 Circuit breakers & rate-limit fallbacks for vector DB / reranker calls

Standard 3-state circuit breaker (Closed → Open → Half-Open) applied to retriever/reranker calls [42][43][44][45]:
- **Closed**: requests flow normally; breaker tracks failure/slow-call rate over a sliding window.
- **Open**: once failure rate crosses a threshold (e.g., 50% over 30s, or per Resilience4j-style config: `failureRateThreshold=50`, `slowCallDurationThreshold=2s`), the breaker fails fast — no network call attempted — for a `waitDurationInOpenState` cooldown (e.g., 60s) [44].
- **Half-Open**: a limited number of probe requests (e.g., 5) test recovery; success → Closed; any failure → back to Open.
- **Fallback tiers for RAG specifically** [42][46]: vector DB down → fall back to keyword/Postgres full-text search ("hot" vs "cold" tier) → if that also fails, fall back to LLM parametric knowledge with no retrieved context → if the primary LLM is also down, fall back to a smaller/faster model. Each degradation step trades quality for availability, but the user always gets *an* answer rather than a timeout.
- **Semantic caching** as a fallback: when the circuit is open, check a Redis-backed cache of recently-answered similar queries before failing outright [42].
- Retries (exponential backoff + jitter) are for **transient** faults; circuit breakers are for **sustained outages** — conflating the two causes retry storms that amplify load on an already-degraded vector DB or reranker, a documented cascading-failure pattern in agentic systems (retry → latency spike → orchestrator timeout → more agent instances spawned → more load) [45].

## 4. Enterprise Security & Governance

### 4.1 Multi-tenancy: RBAC / row-level security in vector DBs

| Vector DB | Isolation model | RBAC maturity |
|---|---|---|
| **Weaviate** | Native multi-tenancy: each tenant gets a **separate physical shard**; supports 50,000+ active shards/node, 1M+ concurrently active tenants/cluster; inactive tenants auto-offload from memory | Fine-grained collection+tenant-level RBAC since **v1.29.0** (earlier versions: only Admin / Read-Only) [47][48][49][50] |
| **Pinecone** | **Namespaces** are logical partitions within a single index — **not a security boundary**. A compromised/over-scoped API key with index-level access reaches *all* namespaces in that index | No native RBAC; recommendation for security-sensitive multi-tenant workloads is **one dedicated index per tenant** with a scoped key — costlier but provides true isolation [47][48][49] |
| **pgvector (Postgres)** | Row-Level Security (RLS) at the database layer | Inherits Postgres's mature RBAC/RLS |
| **Qdrant** | JWT collection-scoping | — |

`> ⚠️ Do not treat Pinecone namespace partitioning as a security boundary in a compliance-sensitive design — this is a commonly-tested interview trap.` [49]

### 4.2 PII redaction in indexed documents

Reference architecture (AWS Bedrock Knowledge Bases pattern) [51][52]:
1. Document lands in S3 `inputs/` → EventBridge (5-min poll) triggers Lambda.
2. Lambda launches an async **Amazon Comprehend PII redaction job**; entities (names, SSNs, addresses, financial PII) replaced with typed placeholders (`[NAME]`, `[SSN]`) — NOT a masking character, because a uniform mask character would create false-collision retrieval noise across many redacted documents.
3. Redacted output undergoes **secondary verification via Amazon Macie**; severity ≥3 → quarantine folder; severity <3 → redacted bucket, which triggers the Bedrock ingestion job.
4. This is a **zero-trust, pre-ingestion redaction** pattern: PII never reaches the embedding step, vector store, or retrieval path [51][52].

Alternative "selective redaction" pattern: redact contact info (email/phone/SSN) at ingestion for search-safety, but **retain names** for searchability; apply reversible **tokenization** at query time on the combined retrieved-context + question before it reaches the LLM, restoring PII only in the final personalized response [53]. Placement is critical: the PII redactor should sit **between parsing and chunking** — the chunker, embedder, and vector store should never see raw PII [54]. Under the EU AI Act, high-risk AI systems must demonstrate this data-governance compliance by **August 2027** [54].

### 4.3 Zero-Trust for retrieval sources & sandboxed ingestion

- **Format parsers are the largest ingestion attack surface**: PDF/DOCX/XML parsers are complex state machines with a long CVE history (e.g., **CVE-2025-66516**: malicious XFA payload embedded in a PDF triggers XXE in Apache Tika, escalating to RCE/SSRF when Tika runs with excess privilege) [59].
- **"Airlock" pattern**: never parse untrusted documents in the same process/container as the application or vector DB. Run parsers in **ephemeral, sandboxed, zero-egress environments** (distroless containers, gVisor, Firecracker microVMs) with strict CPU/memory/wall-time limits (guards against XML "Billion Laughs" and algorithmic-complexity DoS bombs); the parser should hold no secrets, no DB credentials, and no network egress beyond what's strictly required [59][60][61].
- **Format-breaking / OCR conversion**: converting PDFs to images via OCR (e.g., Amazon Textract) strips embedded scripts, macros, and hidden elements (including invisible white-on-white prompt-injection text), at the cost of losing non-text structure [63].
- Treat the LLM itself as an **untrusted actor** with only least-privilege access to data sources; maintain human/system control over any downstream plugin or agent action triggered by retrieved content, since documents from external/untrusted sources are a known **indirect prompt-injection** vector [63].

### 4.4 Audit logs of retrieval provenance

Enterprise/regulatory-grade RAG audit trails must capture the **full decision chain**, immutably and append-only [55][56][57][58]:
- Fields: original user query, the actual retrieval query issued, retrieved chunk IDs + source doc ID + version/timestamp + similarity/rerank scores, the full context window sent to the LLM, the generated response, and (ideally) a cryptographic signature (SHA-256 / Ed25519) chaining query→retrieval→answer for tamper evidence.
- Regulatory mappings: **EU AI Act Art. 30** (log every inference: input, output, sources, model identity, timestamp, user), **GDPR Art. 30** (records of processing activities), **HIPAA §164.312(b)** (audit controls for PHI-touching systems), **SOX / MiFID II** (financial services: 5–7 year retention, document versioning to reconstruct "what the system knew" at any point in time) [56][58].
- Practical gap: most RAG stacks assemble independently-logging components (retriever, reranker, LLM) — a unified lineage record requires instrumenting the **orchestration layer** to emit structured events centrally; this is frequently the actual blocker to production sign-off, not retrieval quality [56][57].
- **Permission-aware retrieval** must happen *before* generation: tag ACLs on each chunk at ingestion time, filter at the ANN/pre-filter stage (never post-filter after generation) so relevance is intersected with permission, then redact PII/secrets from retrieved passages before they reach the prompt or any log [ingestion security research, §4.2/4.3].

## 5. Production Failure Modes

### 5.1 Taxonomy of retrieval failures

A production RAG debugging playbook identifies **7 distinct, individually-diagnosable failure modes** hiding under the generic complaint "retrieval is bad" [35]:
1. Wrong chunk retrieved
2. Right chunk missed entirely (recall failure)
3. Right chunk retrieved but ranked too low (needs reranking)
4. **Stale/outdated index** (see below)
5. Embedding-model mismatch between index-time and query-time (e.g., index built with model v1, queried with v2)
6. Chunk boundaries cutting an answer in half
7. Metadata/filter bugs silently excluding valid results

Each requires a different fix — "rerank harder" when the actual root cause is a stale index burns a sprint improving a metric that was never the problem [35].

### 5.2 Stale index / freshness failures

- A **stale document produces a fluent, well-cited, confidently wrong answer** — this is *more dangerous* than a missing document, which at least produces an honest "I don't have information about that" [36][37].
- Documented real-world pattern ("hallucinates more on Mondays"): nightly batch indexing meant weekend-created documents (incident reports, on-call handoffs) weren't indexed until Monday night, so Monday queries hit a knowledge base **48–72 hours stale** for the most operationally critical content [37].
- Total pipeline latency for a single document change ranges from **~15 minutes** (real-time streaming CDC architectures) to **24+ hours** (nightly batch) [37]. Diagnostic: modify a document, poll every 30 minutes until the change is retrievable — if that "freshness lag" exceeds 2 hours, the system is serving stale answers during peak usage daily [37].
- Partial re-indexing (only affected chunks) reduces re-embedding compute by **90%+** vs. full-document re-embed, and dramatically shortens the freshness window [37].

### 5.3 Reranker latency spikes

Reranker latency is **highly sensitive to document token length**, not just batch size — Cohere Rerank 3.5 benchmarks show a >40× latency multiplier between 64-token and 4096-token documents at the same 96-document batch size (0.17s vs 7.35s) [28]. This is a common source of P99 latency spikes when document truncation/chunking policy isn't enforced upstream of the reranker call.

### 5.4 GraphRAG entity-resolution errors

- **Compounding error problem**: if entity extraction is 85% accurate (optimistic for specialized domains — tianpan.co measured 60–85% in practice) and a query requires a 5-hop graph traversal, answer reliability is `0.85^5 ≈ 44%` — fewer than half of multi-hop answers are trustworthy. At 3 hops: 61%; at 2 hops: 72% [16]. A single misidentified entity **poisons every downstream path** that traverses through it (e.g., "Dr. Smith" appearing 847 times — a wrong merge decision corrupts every query touching that entity) [16].
- **Documented bug** (microsoft/graphrag issue #1718): `finalize_entities` groups by `(title, type)` for deduplication but the drop operation doesn't correctly handle same-title-different-type collisions (e.g., "IBM" as ORGANIZATION vs. "IBM" as COMPANY) — silently keeps only the first node and **discards the other along with its edges** [16].
- **Cold-start / incremental update gap**: entity resolution doesn't "keep up" with incremental corpus updates unless a full reindex is run — new content won't resolve against existing canonical entities, creating duplicate entity nodes over time [16].
- **Graph decay**: production knowledge graphs without automated refresh drift **15–20% from ground truth per quarter**; community summaries are frozen at index time and don't auto-update [16].
- **Triple-index synchronization burden**: production GraphRAG requires keeping **three indexes in sync** (full-text, vector, graph) — every document change must propagate to all three, and most tutorials/frameworks don't solve this cleanly [16].

### 5.5 Agentic RAG infinite retrieval loops

- Documented reference-implementation bug: **LangGraph's own official agentic-RAG tutorial** shipped with an infinite retrieval loop bug, requiring a `rewrite_count` cap fix — "if the reference implementation can loop forever, production systems certainly will" [33][34].
- Root cause: the agent optimizes locally each step ("do I have enough? if uncertain → get more"), and without a hard stop, this **spirals — retrieve, escalate, retrieve again, burning tokens without guaranteed progress** [34].
- Mandatory infrastructure-level (not model-level) guardrails: **max iterations (3–5 hard cap)**, **token/cost budget** (e.g., 12k–40k tokens per full trace, checked by the orchestrator — not "asked nicely" of the model), **wall-clock timeout** (30–60s interactive), and a **"new evidence" / minimum-improvement check** (deduplicate against prior retrieval results; if the new retrieval doesn't surface meaningfully different content, stop and answer) [33][34][35].
- Branch control flow on the API's structured `stop_reason` field, not on parsed model prose [35].

### 5.6 Chunking failures

- Chunk-boundary context loss is one of the most common and cheaply-fixable failure modes — see Anthropic's Contextual Retrieval (§5.7) which was designed specifically to address it [40].
- Naive chunk-ID schemes (`doc_id + ":" + index`) break under edits: inserting a sentence near the top of a document shifts every subsequent chunk's ID, silently invalidating stored citations that pointed at the old IDs [37].

### 5.7 Real, quantified incident data: Anthropic Contextual Retrieval

Anthropic's internal benchmark (published; widely cited) is one of the few instances of **quantified, stacked improvement data** in the RAG literature [40][41]:

| Configuration | Top-20-chunk retrieval failure rate | Relative reduction |
|---|---|---|
| Naive RAG (raw chunk embeddings) | 5.7% | baseline |
| + Contextual Embeddings (LLM-generated chunk-context prepended before embedding) | 3.7% | −35% |
| + Contextual BM25 (same context prepended before BM25 indexing, RRF-fused) | 2.9% | −49% |
| + Reranking (cross-encoder on fused top-N) | **1.9%** | **−67%** |

This is the single most quantitatively rigorous public data point tying specific architectural choices to failure-rate reduction, and is a high-value citation for system-design interviews [40].

## 6. Enterprise System Design Scenarios

### 6.1 Real-world scale benchmarks

- **Milvus**: "billion-scale similarity search with little performance degradation," linear QPS scaling across CPU cores (8→32) and replicas (1→8, 7,153→30,655 QPS) [30].
- **VectorDBBench (Zilliz) leaderboard**: benchmarks up to **100M-vector single-tenant (LAION-100M)** and **multi-tenant 10M-vector / 1,000-tenant (Cohere-Large)** workloads, explicitly measuring P99 latency and max sustained QPS **under concurrent ingestion pressure**, not just static-index performance — a more realistic production proxy than idealized ANN benchmarks [29].
- **pgvector + pgvectorscale**: reported "28× lower P95 latency and 16× higher query throughput compared to Pinecone's storage-optimized (s1) index" in one third-party benchmark — `[inferred/vendor-benchmark, verify independently before citing as fact]` [39].

### 6.2 Published enterprise architecture case studies

**10M-document / 50M-vector Fortune 500 financial services deployment** [32]:
- Sources: SharePoint, Confluence, internal systems → intelligent structure-aware chunking → dense embedding (domain-tuned model) → distributed, sharded vector store with metadata filtering.
- Query path: intent classification/query expansion → **hybrid retrieval** (vector + keyword + metadata filters) → **cross-encoder rerank** → grounded generation with citation extraction.
- Security: **pre-filtering** (only search documents the user can access) *and* **post-filtering** (verify permissions before returning results) — defense in depth; full audit logging; automatic PII detection/masking; dedicated model-inference instances for isolation.
- Result: 88ms P50 / 142ms P95 end-to-end, 850 peak QPS.

**1,000-tenant / 10M-docs-per-tenant / 100 QPS-per-tenant reference design** (HLD Handbook) [32]:
- Tenant isolation: **namespace-per-tenant** for the long tail of small tenants, **shard-per-tenant** for the top 5% by document volume (blast-radius isolation).
- ACL-aware retrieval must **pre-filter at the ANN stage**, never post-filter (post-filtering after ANN search can return fewer than `k` results if many top candidates are filtered out, and wastes the ANN search's compute).
- Contextual Retrieval is applied at **index time** (situating-context generation via a cheap model like Claude Haiku, prepended before embedding + BM25 indexing) so it "costs nothing at retrieval" — the expensive LLM call is a one-time indexing cost, not a per-query cost.
- Reranking (Cohere rerank-v4.0, up to 10,000 docs / 32,768 joint-context tokens) is explicitly called out as "the most expensive retrieval step (50–200ms) but the highest-leverage" — citing the Anthropic 67% failure-rate reduction figure.
- Core trade-off: retrieval correctness vs. latency, tuned via reranker candidate-pool size (200 candidates for quality-sensitive tenants, 50 for latency-sensitive tenants) within a 1,500ms total latency budget.

### 6.3 Trade-off matrices

**Hybrid vs. pure-vector search**:
| Factor | Pure dense vector | Hybrid (dense+sparse+RRF) |
|---|---|---|
| Exact-match / rare terms, IDs, product codes | Poor (embeddings underweight rare tokens) | Strong (BM25 leg) |
| Semantic paraphrase | Strong | Strong (dense leg) |
| Score comparability | N/A (single signal) | Requires RRF/normalization (do not naively sum scores) |
| Multi-tenant filtering complexity | Lower | Slightly higher (two indexes/legs to maintain) |
| Anthropic benchmark evidence | 5.7% baseline failure (naive dense) | 2.9% failure (contextual dense + BM25, pre-rerank) |

**Agentic vs. single-shot RAG**:
| Factor | Single-shot | Agentic |
|---|---|---|
| Latency | 1–3s | 10–60s |
| Cost | 1× | 3–10× |
| Best fit | FAQ, scoped lookups, chat | Multi-hop research/synthesis |
| Primary failure mode | Silent context miss | Runaway cost, non-terminating loops |
| Requires | Fixed pipeline | Iteration cap, token budget, wall-clock timeout, stop-condition design as a first-class product decision |

**GraphRAG vs. vector RAG**:
| Factor | Vector RAG | GraphRAG |
|---|---|---|
| Query type fit | Specific-fact / narrow lookup | Holistic / thematic / "summarize the corpus" queries |
| Indexing cost | Embedding only — e.g., $0.0056 for one representative corpus in Microsoft's own cost study | **90% of cost/runtime** in LLM extraction + community summarization; embeddings "nearly negligible by comparison" [16] |
| Update/delete | Trivial (add/remove vectors) | Hard — full reindex is expensive but consistent; incremental updates are cheaper but drift; requires an explicit operational policy [16] |
| Reliability at multi-hop | N/A (single retrieval hop) | Compounds entity-resolution error exponentially with hop count (0.85^hops) |
| Operational maturity | High — most vendors production-ready | Lower — "most GraphRAG libraries assume local development patterns; production requires batch infra for long-running builds" [16] |
| Recommended adoption pattern | Default choice | Start small, keep a vector-RAG baseline, prove value on a narrow question set before scaling [16] |

### 6.4 Capacity planning heuristics

- **Vector index memory**: budget ~20–25 KB/vector (1536-dim, HNSW) for RAM sizing; plan for the **entire index to fit in memory** — disk fallback degrades latency by 100–1000× [38][39].
- **Reranker throughput**: budget request latency as a function of `(document_count × avg_token_length)`, not document count alone — a 96-doc batch at 4096 tokens/doc is ~43× slower than the same batch at 64 tokens/doc [28].
- **GraphRAG indexing cost model**: build one full (no-cache) index run on a representative corpus sample, log tokens/duration per pipeline stage (extraction vs. summarization vs. embedding), derive a cost-per-document or cost-per-10K-tokens unit economic, and use it to forecast full-corpus indexing spend before committing [16].
- **Agentic RAG budget gating**: token budget (20–40K/query loop), iteration cap (3–7), wall-clock timeout (30–60s interactive / longer for async) — these three numbers are the primary cost-control levers and should be tuned from real trace data, not guessed upfront [35].

> ⚠️ **Data gaps**: No vendor publishes a directly comparable, apples-to-apples billion-scale (>1B vectors) production latency benchmark across Pinecone/Weaviate/Milvus/Qdrant under identical hardware and identical dataset — cross-vendor scale claims in this document are sourced from each vendor's own benchmark methodology and should be treated as directional. Similarly, no public source quantifies "average enterprise incident cost" for stale-index or GraphRAG entity-resolution failures — the failure-mode analysis in §5 is qualitative/architectural except where Anthropic's Contextual Retrieval numbers are explicitly cited.

## Sources

- [1] https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion — Elasticsearch RRF retriever official docs
- [2] https://docs.pinecone.io/guides/search/hybrid-search — Pinecone hybrid search (vector-API pattern, alpha weighting)
- [3] https://docs.pinecone.io/guides/get-started/concepts — Pinecone concepts: dense/sparse vectors, namespaces
- [4] https://docs.weaviate.io/weaviate/concepts/search/hybrid-search — Weaviate hybrid search concepts (alpha, fusion algorithms)
- [5] https://www.pinecone.io/learn/hybrid-search-intro/ — Pinecone hybrid search intro tutorial (alpha parameter walkthrough)
- [6] https://docs.weaviate.io/weaviate/search/hybrid — Weaviate hybrid search operational docs
- [7] https://redis.io/blog/reciprocal-rank-fusion/ — RRF explainer, why raw score summing breaks rankings
- [8] https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026 — BM25/vector/reranking reference incl. WANDS benchmark numbers
- [9] https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/ — OpenSearch RRF feature + NDCG@10 comparison table
- [10] https://d6108366.hf-mirror.com/papers/2210.11934 — "An Analysis of Fusion Functions for Hybrid Retrieval" (RRF sensitivity/OOD generalization findings)
- [11] https://qdrant.tech/documentation/tutorials-basics/reranking-hybrid-search/ — Qdrant hybrid search + ColBERT late-interaction reranking tutorial
- [12] https://qdrant.tech/articles/hybrid-search/ — Qdrant Query API hybrid search engineering deep dive
- [13] https://microsoft.github.io/graphrag/ — Microsoft GraphRAG official docs (indexing pipeline, search modes)
- [14] https://www.microsoft.com/en-us/research/blog/graphrag-improving-global-search-via-dynamic-community-selection/ — Dynamic community selection for global search
- [15] https://microsoft-graphrag.mintlify.app/concepts/community-detection — GraphRAG community detection (Leiden algorithm) concepts
- [16] https://medium.com/data-science-at-microsoft/graphrag-beyond-the-demo-lessons-from-the-trenches-add83180f849 — GraphRAG production lessons (cost breakdown, update complexity); also https://mohammadkhan.dev/blog/graphrag-pilots-succeed-production-fails and https://github.com/microsoft/graphrag/issues/1718 (entity dedup bug) and https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/graphrag-costs-explained-what-you-need-to-know/4207978
- [17] https://www.alphaxiv.org/abs/2506.10408 — Survey on Reasoning Agentic RAG (Self-RAG, CRAG, RAPTOR taxonomy)
- [18] https://arxiv.org/html/2603.07379v1 — SoK: Agentic RAG (POMDP formalization, taxonomy)
- [19] https://dl.acm.org/doi/10.1145/3805774 — Survey on Retrieval-Augmented Text Generation (Loop RAG, adaptive retrieval)
- [20] https://arxiv.org/html/2501.09136v4 — Agentic RAG survey (design patterns: reflection, planning, tool use)
- [21] https://tokenmix.ai/blog/openai-embedding-pricing — OpenAI embedding pricing comparison (3-small vs 3-large)
- [22] https://docs.voyageai.com/docs/pricing — Voyage AI reranker/embedding pricing
- [23] https://docs.voyageai.com/docs/reranker — Voyage reranker model specs (context length, generations)
- [24] https://blog.voyageai.com/2024/09/30/rerank-2/ — Voyage rerank-2/rerank-2-lite launch blog (accuracy benchmarks)
- [25] https://docs.voyageai.com/docs/faq — Voyage FAQ (token limits, latency recommendations)
- [26] https://www.metacto.com/blogs/cohere-pricing-explained-a-deep-dive-into-integration-development-costs — Cohere pricing breakdown
- [27] https://cohere.com/pricing — Cohere official pricing (Rerank 3.5 search-unit definition)
- [28] https://docs.oracle.com/en-us/iaas/Content/generative-ai/benchmark-cohere-rerank-3-5.htm — OCI Cohere Rerank 3.5 latency/throughput benchmarks
- [29] https://zilliz.com/vdbbench-leaderboard-v2 — VectorDBBench leaderboard (100M/multitenant streaming QPS/P99)
- [30] https://milvus.io/docs/v2.6.x/benchmark.md — Milvus 2.2 official benchmark report (QPS/P99 scale-up/scale-out)
- [31] https://awesomeagents.ai/leaderboards/rag-benchmarks-leaderboard/ — 2026 MTEB/BEIR retrieval leaderboard summary
- [32] https://hld.handbook.academy/curriculum/case-studies/enterprise-rag/ — Enterprise RAG system design case study (1000 tenants, latency budget); also https://scx.ai/resources/enterprise-rag-case-study (10M docs, P50/P95 table)
- [33] https://learn.temporal.io/tutorials/ai/durable-ai-agent/ — Durable AI agent with Temporal (Workflow/Activity pattern)
- [34] https://temporal.io/blog/durable-execution-meets-ai-why-temporal-is-the-perfect-foundation-for-ai — Temporal durable execution rationale for agentic loops
- [35] https://dev.to/dublecc/the-rag-retrieval-debugging-playbook-a-diagnostic-order-of-operations-for-production-failures-5ced — RAG failure-mode taxonomy (7 distinct retrieval failure types)
- [36] https://tianpan.co/blog/2026-05-17-vector-index-cache-no-invalidation-strategy — Vector index as an uninvalidated cache; CDC-based re-indexing
- [37] https://moonpool.ai/resources/blog/technical/rag-system-hallucinates-more-on-mondays — Real incident: nightly-batch freshness lag causing stale answers; also https://multigrid.ai/learn/rag-index-freshness (chunk ID/manifest design) and https://tianpan.co/blog/2026-04-20-rag-knowledge-base-freshness-index-rot
- [38] https://particula.tech/blog/pgvector-hnsw-tuning-millions-rows-production — pgvector HNSW tuning at 10M+ rows (memory sizing, m/ef_construction/ef_search)
- [39] https://www.crunchydata.com/blog/hnsw-indexes-with-postgres-and-pgvector — pgvector HNSW trade-offs (build cost, memory); also https://supabase.com/blog/pgvector-0-7-0 (float16/halfvec) and Medium pgvector/Supabase scaling post
- [40] https://www.anthropic.com/engineering/contextual-retrieval — Anthropic Contextual Retrieval (35%/49%/67% failure-rate reduction data)
- [41] https://dreaming.press/posts/how-to-implement-contextual-retrieval-hybrid-bm25-rerank.html — Contextual Retrieval implementation walkthrough
- [42] https://ansezz.com/blog/circuit-breakers-vector-db/ — Circuit breaker pattern applied specifically to vector DB calls (hot/cold tier fallback)
- [43] https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker — Circuit breaker pattern reference (Closed/Open/Half-Open states)
- [44] https://medium.com/@abhi.strike/microservices-patterns-circuit-breaker-pattern-4af499b112cb — Circuit breaker config example (Resilience4j)
- [45] https://preporato.com/blog/error-handling-resilience-patterns-agentic-ai-systems — Circuit breakers vs retries in agentic AI systems (cascading failure scenario)
- [46] https://theneuralbase.com/llamaindex/learn/advanced/graceful-degradation-when-retrieval-fails/ — LlamaIndex graceful degradation pattern for retrieval failures
- [47] https://docs.weaviate.io/weaviate/manage-collections/multi-tenancy — Weaviate multi-tenancy official docs (shard-per-tenant isolation)
- [48] https://www.productionai.institute/insights/vector-database-comparison — Pinecone vs Weaviate vs Chroma security/multi-tenancy comparison
- [49] https://beyondscale.tech/blog/vector-database-hardening-pinecone-pgvector-guide — Vector DB hardening guide (namespace vs. RBAC vs. RLS distinctions)
- [50] https://docs.weaviate.io/weaviate/configuration/rbac — Weaviate RBAC overview (permissions model)
- [51] https://aws.amazon.com/blogs/machine-learning/protect-sensitive-data-in-rag-applications-with-amazon-bedrock/ — AWS Bedrock PII redaction reference architecture (Comprehend + Macie)
- [52] https://docs.aws.amazon.com/solutions/securing-sensitive-data-in-rag-applications-using-amazon-bedrock/ — AWS zero-trust RAG data security guidance
- [53] https://blindfold.dev/blog/pii-safe-rag-pipeline — Selective redaction + query-time tokenization pattern
- [54] https://www.ertas.ai/blog/rag-pipeline-pii-redaction-guide — PII redactor placement (between parsing and chunking); EU AI Act compliance deadline
- [55] https://github.com/dakshtrehan/ragcompliance — RAGCompliance audit-trail middleware (SHA-256 chain signatures, RLS)
- [56] https://helain-zimmermann.com/blog/enterprise-rag-with-citation-tracking-and-audit-trails — Citation tracking / audit trail architecture for regulated RAG
- [57] https://domino.ai/blog/enterprise-rag-production — Enterprise RAG governance & monitoring (unified lineage record challenges)
- [58] https://pypi.org/project/trailrag/ — TrailRAG compliance audit layer (EU AI Act Art. 30 / GDPR / HIPAA field mapping)
- [59] https://www.penligent.ai/hackinglabs/anatomy-of-a-rag-killer-deep-dive-into-cve-2025-66516-and-the-apache-tika-rce/ — CVE-2025-66516 Apache Tika XXE/RCE in RAG ingestion pipeline
- [60] https://github.com/martinholovsky/sota-skills/blob/main/skills/sota-code-security/rules/09-untrusted-data-ingestion.md — Untrusted data ingestion security rules (sandboxing, broker pattern)
- [61] https://rowantreescientific.co.uk/bomb-detection-in-rag-systems/ — RAG ingestion pipeline as an automated execution environment for untrusted content
- [62] https://notchrisgroves.com/ai-file-upload-security-advisory-overview/ — AI file upload security advisory (parser isolation architecture)
- [63] https://aws.amazon.com/blogs/security/securing-the-rag-ingestion-pipeline-filtering-mechanisms/ — AWS Security Blog: RAG ingestion filtering mechanisms (OCR sanitization, zero-trust)
- [64] https://www.applied-ai.com/briefings/enterprise-rag-architecture/ — Enterprise RAG architecture practitioner's guide (maturity tiers, routing)
- [65] https://vibeengines.com/ai-system-design/secure-document-ingestion-and-rag-system-design — Secure document ingestion + permission-aware retrieval system design
