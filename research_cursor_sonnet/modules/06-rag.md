# 06. RAG

**Sub-areas covered**: hybrid search fusion mechanics (dense+sparse via Reciprocal Rank Fusion and convex-combination alternatives; vendor implementations across Elasticsearch, Pinecone, Weaviate, Qdrant) · cross-encoder and late-interaction (ColBERT) reranking placement and cost math · Agentic RAG control loops (Self-RAG, CRAG, IRCoT/Iter-RetGen, FLARE/DRAGIN, RAPTOR) formalized as a finite-horizon POMDP · Microsoft GraphRAG indexing (Leiden hierarchical community detection) and query-time modes (global/local/DRIFT search) · token economics across embedding + retrieval + reranking + generation with explicit cost formulas · latency SLA targets (P50/P95/P99) per pipeline stage with mitigations · explicit availability %, RPO/RTO figures tied to index-update granularity, and freshness-vs-cost / recall-vs-latency trade-offs · durable execution for multi-hop agentic retrieval, index consistency, distributed locking, checkpointing, dead-letter handling · failure taxonomy (transient/permanent/poison-pill) and idempotency design · Zero-Trust MCP, multi-tenant RBAC/row-level isolation in vector DBs, PII redaction (detect→redact→audit), retrieval-provenance auditability · production failure modes (stale index, embedding-model mismatch, GraphRAG entity-resolution compounding error, infinite agentic retrieval loops) · a hardened hybrid-search+rerank Python pipeline with circuit breakers and fallback chains · two enterprise system-design scenarios with trade-off matrices

---

## 1. System Topology & Data Flow

A production RAG stack that supports hybrid search, reranking, agentic iteration, and graph-based retrieval is not one pipeline but four retrieval *modes* sharing one control plane and one persistence substrate. The diagram below places each vendor/library-specific mechanic (cited inline, cross-referenced to §2–§4) into the generic plane it occupies.

```
                    ┌───────────────────────────────────────────────────────────────────────────┐
                    │                              CONTROL PLANE                                  │
                    │                                                                              │
                    │  ┌─────────────────┐   ┌───────────────────┐   ┌────────────────────────┐  │
                    │  │ Query Router /   │──▶│ Agentic Loop        │──▶│ Guardrail / PII Gate    │  │
                    │  │ Intent Classifier │   │ Controller           │   │ (pre-prompt redaction,  │  │
                    │  │ (routine lookup  │   │ plan→retrieve/act→  │   │  ACL intersection,      │  │
                    │  │  vs. multi-hop   │   │ observe/grade→decide │   │  §4.5)                  │  │
                    │  │  vs. thematic/   │   │ -- Self-RAG/CRAG/    │   └────────────┬────────────┘  │
                    │  │  graph query)    │   │ IRCoT/FLARE/RAPTOR   │                │ tripwire clear? │
                    │  └────────┬────────┘   │ topologies, §2.3     │                │                  │
                    │           │             └──────────┬───────────┘                │                  │
                    │           │  route: dense-only /    │  retrieve-again /          │                  │
                    │           │  hybrid / graph / agentic│  reformulate / answer      │                  │
                    │           │                          ▼                            │                  │
                    │           │             ┌────────────────────────┐               │                  │
                    │           └────────────▶│ Termination / Budget    │◀──────────────┘                  │
                    │                          │ Supervisor: max-iter    │                                   │
                    │                          │ (3-5 cap), token budget │                                   │
                    │                          │ (12-40k/trace), wall-   │                                   │
                    │                          │ clock timeout (30-60s), │                                   │
                    │                          │ new-evidence dedup check│                                   │
                    │                          │ (§3.6, §5.5 of research)│                                   │
                    │                          └────────────┬────────────┘                                   │
                    └───────────────────────────────────────┼───────────────────────────────────────────────┘
                                                              │ stop_reason: tool_use | end_turn
                    ┌─────────────────────────────────────────▼──────────────────────────────────────────────┐
                    │                                    DATA PLANE                                            │
                    │                                                                                          │
                    │  ┌───────────────┐  ┌────────────────────┐  ┌───────────────┐  ┌───────────────────┐  │
                    │  │ Query          │─▶│ Hybrid Retrieval     │─▶│ Fusion         │─▶│ Reranker            │  │
                    │  │ Embedding      │  │ (parallel legs)      │  │ (RRF k=60 /    │  │ (cross-encoder /    │  │
                    │  │ (dense vector; │  │  dense: ANN/HNSW     │  │  CC / DBSF /   │  │  ColBERT late-      │  │
                    │  │  §2.1 model    │  │  sparse: BM25/SPLADE │  │  relativeScore-│  │  interaction;       │  │
                    │  │  choice)       │  │  (§2.1)              │  │  Fusion, §2.1) │  │  narrow top-10..20, │  │
                    │  └───────────────┘  └──────────┬───────────┘  └───────┬────────┘  │  §2.2)              │  │
                    │                                 │                       │           └──────────┬──────────┘  │
                    │                                 │                       │                       │             │
                    │                       ┌─────────▼───────────┐          │            ┌──────────▼──────────┐  │
                    │                       │ Graph Traversal       │          │            │ Generation (LLM)     │  │
                    │                       │ (GraphRAG only):      │──────────┘            │ grounded answer +    │  │
                    │                       │ local / global map-   │                       │ citation extraction  │  │
                    │                       │ reduce / DRIFT search │                       │ (§2.4, §6)           │  │
                    │                       │ over community reports│                       └──────────┬──────────┘  │
                    │                       └───────────────────────┘                                  │             │
                    └──────────────────────────────────────────────────────────────────────────────────┼─────────────┘
                                                                                                           │
                    ┌──────────────────────────────────────────────────────────────────────────────────▼─────────────┐
                    │                                  TOOL PROXY LAYER                                                │
                    │  ┌────────────────────────┐   ┌─────────────────────────┐   ┌──────────────────────────────┐  │
                    │  │ MCP Tool Gateway         │   │ Circuit-Breaker-Gated     │   │ Fallback Chain Dispatcher      │  │
                    │  │ (Zero-Trust, deny-by-    │   │ Vector DB / Reranker      │   │ vector DB down → keyword/     │  │
                    │  │  default; web-search /   │   │ Client (Closed→Open→      │   │ Postgres FT ("cold" tier) →   │  │
                    │  │  internal-DB tools for   │   │ Half-Open, §4.4)          │   │ parametric LLM (no context) → │  │
                    │  │  agentic retrieval, §4.5)│   │                           │   │ smaller/faster model (§4.4)   │  │
                    │  └────────────────────────┘   └─────────────────────────┘   └──────────────────────────────┘  │
                    └─────────────────────────────────────────┬────────────────────────────────────────────────────────┘
                                                                 │
                    ┌────────────────────────────────────────────▼───────────────────────────────────────────────────┐
                    │                                  PERSISTENCE LAYER                                                │
                    │  ┌───────────────┐ ┌────────────────┐ ┌───────────────────┐ ┌────────────────────────────────┐ │
                    │  │ Vector Store   │ │ Sparse / BM25   │ │ Graph Store         │ │ Ingestion Manifest / CDC Log      │ │
                    │  │ (dense ANN;    │ │ Inverted Index  │ │ (entities/edges/    │ │ (stable chunk IDs + content hash; │ │
                    │  │ namespace- or  │ │ (per-tenant     │ │ community reports;  │ │  source-of-truth for freshness,   │ │
                    │  │ shard-per-     │ │ filtered)       │ │ Leiden hierarchy,   │ │  deletion propagation, §4.2)      │ │
                    │  │ tenant, §4.5)  │ │                 │ │ §2.4)               │ │                                    │ │
                    │  └───────────────┘ └────────────────┘ └───────────────────┘ └────────────────────────────────┘ │
                    └──────────────────────────────────────────┬───────────────────────────────────────────────────────┘
                                                                  │
                    ┌───────────────────────────────────────────▼──────────────────────────────────────────────────────┐
                    │                              TELEMETRY / OBSERVABILITY SINKS                                         │
                    │  Immutable retrieval-provenance log (query, retrieval query, chunk IDs + scores, context sent to    │
                    │  LLM, response, SHA-256/Ed25519 chain-of-custody signature, §4.5) · circuit-breaker state + fallback- │
                    │  tier metrics · per-stage P50/P95/P99 latency and cost-per-query dashboards (§3.5-3.6) · freshness-  │
                    │  lag probe (poll-until-retrievable diagnostic, §5.2 of research)                                     │
                    └───────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A query enters the **Control Plane**; the Query Router classifies it as a routine single-hop lookup, a multi-hop research question, or a holistic/thematic query ("summarize the last two weeks") — this decision determines whether the request takes the dense-only, hybrid, agentic, or graph path, and is a compiled/deterministic classification, not a model-emitted routing choice, so it remains auditable. (2) For routine and hybrid queries, control passes directly into the **Data Plane**: the query is embedded, dense (ANN/HNSW) and sparse (BM25) retrieval legs run in parallel against a wide candidate window (top-50 to top-500), and results are fused via RRF (or a tuned convex combination) — never by summing raw scores, since BM25's unbounded range and cosine similarity's `[-1,1]` range are not comparable. (3) The fused candidate list is narrowed by a **Reranker** (cross-encoder or ColBERT late-interaction) to the top-10–20 that actually enter the generation prompt — the reranker is a second-stage precision layer, never run against the full index, because its O(candidates) compute cost makes full-corpus scoring intractable. (4) For queries the router flags as multi-hop, the **Agentic Loop Controller** takes over: it issues a retrieval/tool call via the **Tool Proxy Layer** (MCP-gated, Zero-Trust), observes/grades the result, and decides to retrieve again, reformulate the query, or answer — this loop is modeled as a finite-horizon POMDP (§2.3) and is bounded at every iteration by the **Termination/Budget Supervisor**, which checks max-iteration count, cumulative token spend, and wall-clock elapsed time *before* allowing the next hop, exactly mirroring the pre-step budget-check pattern used for agent-framework termination elsewhere in this roadmap. (5) For thematic/holistic queries, control routes to **Graph Traversal**: global search runs a map-reduce over pre-computed community reports (optionally pruned top-down via dynamic community selection), local search expands from entity-embedding matches into their community context, and DRIFT search combines both. (6) Every vector DB, reranker, and LLM call in the data plane is wrapped by a **circuit breaker** in the tool proxy layer; on sustained failure, the **Fallback Chain Dispatcher** degrades gracefully (dense-only → keyword search → parametric LLM answer → smaller model) rather than surfacing a timeout to the user. (7) The **Persistence Layer** separates four physically distinct stores — vector index, sparse index, graph store, and an ingestion manifest/CDC log that is the actual source of truth for chunk freshness and deletion — because a vector index has no built-in invalidation strategy and cannot itself distinguish a stale embedding from a fresh one. (8) Every retrieved chunk ID, similarity/rerank score, the exact context window sent to the LLM, and the generated response are written to an **immutable, append-only provenance log** before the response streams back to the user, closing the audit-trail requirement that most compliance sign-offs actually block on.

---

## 2. Core Mechanics & Algorithms

### 2.1 Hybrid search fusion: RRF, convex combination, and vendor implementations

**Reciprocal Rank Fusion (RRF)** dominates production hybrid search because it fuses **rank positions**, not raw scores, sidestepping the fact that BM25 scores are unbounded and corpus/implementation-dependent while cosine similarity is bounded to `[-1, 1]`.

```
RRF_score(d) = Σ_i  1 / (k + rank_i(d))
```

— summed across each ranked list `i` (one per retrieval leg) that contains document `d`; `rank_i(d)` is `d`'s 1-indexed position in list `i`; `k` (the rank constant) defaults to **60** across Elasticsearch, OpenSearch, and most implementations. The constant `k` exists to dampen the influence of top-ranked documents in any single list — without it, a document ranked #1 in one leg and absent from the other would dominate the fused score regardless of how weak that single signal actually is.

- **Complexity**: `O(N log N)` per leg for the initial ranking (dominated by the ANN search or BM25 scoring, not the fusion step itself), then `O(M)` to merge `M` total candidates across legs — RRF itself is a single pass over the candidate union.
- **Invariant**: RRF requires only rank order, not calibrated scores — this is simultaneously its main strength (no cross-signal normalization needed) and its documented weakness: an empirical fusion-function study found RRF is *sensitive to the choice of `k`* and generalizes worse **out-of-domain** than a tuned convex combination (`CC_score(d) = α·norm(dense_score) + (1-α)·norm(sparse_score)`) of min-max-normalized scores. CC requires a small tuning set to fit `α` but outperformed RRF both in-domain and out-of-domain in that study.

  > ⚠️ Gap: this directly conflicts with vendor marketing that frames RRF as strictly superior and parameter-free; treat rank-based fusion as a solid, low-effort default, not a settled-science optimum — a compliance-sensitive or high-value retrieval surface should A/B RRF against a tuned CC before locking in fusion strategy.

**Vendor fusion implementations** (the interview-relevant mechanics differ enough to be individually testable):

| Vendor | Fusion mechanism | Parameter surface | Gotcha |
|---|---|---|---|
| Elasticsearch | `rrf` retriever combining ≥2 child retrievers (e.g. `standard` BM25 + `knn`) | `rank_window_size`, `rank_constant` | — |
| OpenSearch 2.19+ | Neural Search hybrid pipeline, `score-ranker-processor` with `technique: rrf` | `rank_constant` (default 60) | On BEIR-style benchmarks, Hybrid+RRF beat plain BM25 and plain vector on most corpora (e.g. Quora NDCG@10: BM25 0.742 → Hybrid+RRF 0.796) |
| Pinecone | Single-index "vector-API hybrid": dense + sparse vectors per record | `alpha` weighting (default 0.5) | Sparse scores are **not normalized** to the dense range by default — production deployments must apply `hybrid_score_norm` or the sparse leg silently dominates |
| Weaviate | Parallel BM25 + vector search, fused via `rankedFusion` (rank-position only, legacy default) or `relativeScoreFusion` (min-max normalizes each list to `[0,1]` before weighted sum; default since v1.24) | `alpha` (0 = pure keyword, 0.75 = default, 1 = pure vector) | Switching the fusion algorithm changes result ordering even at a fixed `alpha` — the two algorithms are not drop-in equivalents |
| Qdrant | `query_points` API: `Prefetch` list (dense + sparse legs) + top-level `FusionQuery` | `Fusion.RRF` or `Fusion.DBSF` (distribution-based score fusion) | DBSF is a distinct alternative, not a synonym for RRF — worth distinguishing explicitly in an interview |

### 2.2 Reranking: placement and cross-encoder mechanics

The universal production pattern is **retrieve wide (top-50 to top-500) → fuse (RRF/CC) → rerank narrow (top-10 to top-20) → generate**. A cross-encoder reranker jointly encodes `(query, document)` pairs through a single transformer forward pass and outputs one relevance score per pair — unlike a bi-encoder (used for the initial ANN retrieval), which encodes query and document independently so their vectors can be pre-computed and compared with a cheap dot product. This joint-encoding is *why* cross-encoders are meaningfully more accurate (they can attend across query and document tokens) and *why* they are too expensive to run against a full index: cost is `O(candidates)` full-transformer forward passes, not `O(1)` vector comparisons.

**ColBERT late-interaction** is the middle ground: it produces per-token (not per-document) dense vectors for both query and document, then scores via a `MaxSim` operator — for each query token, take the max similarity across all document tokens, then sum across query tokens. This retains most of the accuracy benefit of full cross-attention while allowing document-token vectors to be pre-computed and indexed. Because ColBERT multivector fields are used for exhaustive reranking over a small candidate set rather than ANN search, their HNSW index should be disabled (`m=0`) — building an approximate graph over vectors that will only ever be exhaustively compared is wasted index-build cost.

**State machine (query → answer via hybrid+rerank):**

```
   ┌───────────┐   embed query    ┌───────────────┐   fuse (RRF/CC)   ┌───────────┐
   │  QUERY     │─────────────────▶│ DENSE + SPARSE │──────────────────▶│  FUSED     │
   │  RECEIVED  │                  │ RETRIEVE       │                   │  CANDIDATE │
   └───────────┘                  │ (top-50..500)  │                   │  SET       │
                                    └───────────────┘                   └─────┬─────┘
                                                                                │ rerank (cross-encoder/ColBERT)
                                                                                ▼
   ┌───────────┐   grounded answer  ┌───────────────┐   top-10..20      ┌───────────┐
   │  RESPONSE  │◀───────────────────│  GENERATION    │◀──────────────────│  RERANKED  │
   │  RETURNED  │                    │  (LLM + context)│                  │  SHORTLIST │
   └───────────┘                    └───────────────┘                   └───────────┘
```

**Invariant**: any retrieval mechanism can double as a reranking mechanism on a smaller candidate set (e.g. prefetch with sparse, rerank with dense; or oversample with a Matryoshka embedding and progressively reduce dimensionality) — reranking is a *role* a model plays at a given stage, not a fixed architectural component.

### 2.3 Agentic RAG: the iterative retrieval loop as a POMDP

Agentic RAG replaces the fixed "retrieve-once" pipeline with an LLM-driven control loop: **plan → retrieve/act → observe/grade → decide** (retrieve again, reformulate, or answer). A 2026 systematization-of-knowledge survey formalizes this loop as a **finite-horizon partially observable Markov decision process (POMDP)** — the agent's true state (whether it has sufficient grounding to answer correctly) is never directly observable, only inferable from retrieved evidence and its own confidence signals, which is precisely why the loop needs an explicit stopping policy rather than a fixed retrieval count.

**Canonical topologies:**

| Topology | Representative systems | Mechanism |
|---|---|---|
| Loop-based (self-reflective) | Self-RAG, CRAG | Self-RAG emits special "reflection tokens" mid-generation to decide when to retrieve or critique its own draft; CRAG adds a lightweight retrieval-quality evaluator (correct/incorrect/ambiguous) that triggers query rewriting or web-search fallback on low confidence |
| Interleaved reasoning | IRCoT, Iter-RetGen | Retrieval is interleaved with chain-of-thought steps; each reasoning step conditions the next retrieval; Iter-RetGen alternates "use generation to refine retrieval" and "use retrieval to improve generation" |
| Confidence-triggered | FLARE, DRAGIN | Retrieval fires only when token-level generation confidence/entropy drops below a threshold, rather than on a fixed schedule — this is retrieval-as-interrupt rather than retrieval-as-loop-stage |
| Tree/hierarchical | RAPTOR | Recursive summarization tree built over chunks **at index time**, enabling multi-hop reasoning without a live agentic loop at query time — the multi-hop cost is paid once, during indexing, not per-query |

**State machine view:**

```
   ┌───────────┐  formulate query   ┌───────────┐  observe result   ┌────────────┐
   │   PLAN     │────────────────────▶│  RETRIEVE  │───────────────────▶│   GRADE     │
   └───────────┘                     │  / ACT     │                    │ (confidence/│
        ▲                            └───────────┘                    │  relevance) │
        │  reformulate                                                └──────┬──────┘
        │                                                        insufficient │ sufficient
        └────────────────────────────────────────────────────────────────────┘        │
                                                                                         ▼
                                                                                  ┌────────────┐
                                                                                  │   ANSWER    │
                                                                                  │ (terminate) │
                                                                                  └────────────┘
```

Every transition from GRADE back to PLAN is gated by the Termination/Budget Supervisor from §1 — without an infrastructure-level cap, this loop has no natural termination guarantee (§4.3 covers the documented failure mode where this omission caused an infinite loop in a reference implementation).

**Complexity**: single-shot RAG is `O(1)` retrieval calls; agentic RAG is `O(h)` calls for `h` hops, each hop paying full embedding + retrieval + (optionally) reranking + a partial generation cost — so wall-clock and token cost both scale linearly in the *worst case* with the iteration cap, and the cap is therefore the single most important cost-control lever in the entire agentic-RAG design (quantified in §3.3).

### 2.4 GraphRAG: indexing pipeline and community detection

**Indexing pipeline** (Microsoft GraphRAG reference architecture):

1. Slice the corpus into **TextUnits** (chunks) for fine-grained provenance.
2. LLM-based extraction of entities, relationships, and claims/covariates from each TextUnit.
3. Build a graph: nodes = entities, edges = relationships, edge weights = normalized relationship-instance counts.
4. **Hierarchical community detection via the Leiden algorithm** (an improvement on Louvain that guarantees well-connected communities and converges faster) recursively partitions the graph into nested communities of densely-interconnected nodes, producing multiple levels of abstraction — root levels capture broad themes, leaf levels capture fine detail.
5. An LLM generates a **community report** (summary) per community, bottom-up, so higher-level reports are informed by their children's summaries.

**Query-time modes:**

- **Global search** (holistic/thematic queries, e.g. "catch me up on the last two weeks"): a **map-reduce over community reports** — the map step generates an intermediate answer per relevant report, the reduce step aggregates them into a final answer. An improved variant adds **dynamic community selection**: an LLM rates each community report's relevance top-down, pruning irrelevant subtrees *before* the expensive map-reduce, cutting cost proportional to how much of the graph is actually irrelevant to the query.
- **Local search** (entity-specific queries): finds relevant entities via embedding similarity, expands to their community members, adds the relevant community reports, and merges with raw text units for grounding detail global search alone can't provide.
- **DRIFT search** (hybrid): a "primer" step supplies global-style community context, followed by local-search follow-up, building a question/sub-question tree — this is the mode that best answers "give me the big picture, but also the specific facts."

**Key invariant — compounding entity-resolution error**: if entity extraction accuracy is `p` (measured 60–85% in practice for specialized domains) and answering a query requires traversing `h` hops through the graph, the probability that the full multi-hop chain is correct is approximately `p^h` — at `p=0.85`: 2 hops ≈ 72%, 3 hops ≈ 61%, 5 hops ≈ 44%. A single misidentified or wrongly-merged entity poisons *every* downstream path that traverses through it, which is why GraphRAG's reliability curve degrades multiplicatively with hop count in a way vector RAG (a single retrieval hop, no compounding) structurally does not.

### 2.5 Cross-mechanism complexity summary

| Mechanism | Query-time calls | Complexity driver | Where the cost is paid |
|---|---|---|---|
| Dense-only retrieval | 1 embed + 1 ANN search | `O(log N)` ANN search | Query time |
| Hybrid (dense+sparse+RRF) | 1 embed + 2 retrieval legs + 1 fusion pass | `O(N log N)` per leg + `O(M)` fusion | Query time |
| + Reranking | + 1 cross-encoder/ColBERT pass over `M` candidates | `O(M)` full-attention forward passes | Query time (dominant latency stage, §3.5) |
| Agentic (h hops) | `h` × (embed + retrieve [+ rerank] + partial generation) | `O(h)` sequential rounds | Query time, linear in iteration cap |
| GraphRAG indexing | one-time: extraction + Leiden + community summarization | `O(entities × relationships)` extraction; Leiden ≈ `O(E log V)` | Index time (90%+ of GraphRAG total cost, §3) |
| GraphRAG query (global) | map-reduce over relevant community reports | `O(communities selected)` LLM calls | Query time, bounded by dynamic community selection |

---

## 3. Token Economics & NFR Analysis

### 3.1 Embedding costs

| Model | Standard $/1M tok | Batch $/1M tok | Dim | MTEB Retrieval (NDCG@10) |
|---|---|---|---|---|
| OpenAI text-embedding-3-small | $0.02 | $0.01 | 1536 | ~59 |
| OpenAI text-embedding-3-large | $0.13 | $0.065 | 3072 | ~59–62 |
| Cohere Embed v4 | $0.12 | — | — | ~61 |
| Voyage-3.5 / voyage-4 | $0.06 | — | — | ~57.5–66 |
| Voyage-4-large | $0.12–0.18 | — | — | ~66 |
| Gemini Embedding 2 (Mar 2026) | $0.20 ($0.10 batch) | — | 3072 (Matryoshka → 768) | **67.71 (SOTA, Jul 2026)** |

*Assumption*: pricing as published by each vendor at time of writing; MTEB scores are aggregate approximations and vary by task subset. **Cost example**: embedding 10,000 docs × 500 tokens (5M tokens) costs **$0.10** on 3-small standard vs. **$0.65** on 3-large standard — a 6.5× premium for roughly a 4-point MTEB lift, justified mainly for legal/medical/high-stakes retrieval where marginal recall gains matter more than embedding cost.

### 3.2 Reranker costs and latency

**Cohere Rerank 3.5**: $2.00 per 1,000 searches ($0.002/search); one "search" = one query + up to 100 documents; documents >500 tokens are auto-chunked, with each chunk billed as a separate document.

| # Documents | Latency (s) | Throughput (RPS) |
|---|---|---|
| 1 | 0.13 | 7.64 |
| 24 | 0.12–0.20 | 4.8–8.3 |
| 48 | 0.14–0.73 | 1.3–7.2 |
| 96 (64 tok/doc) | 0.17 | 5.86 |
| 96 (4096 tok/doc) | **7.35** | 0.14 |

Latency scales strongly with **document token length**, not just batch size — a >40× latency spread between short (64-tok) and long (4096-tok) documents at the same batch size. This is the single largest reranker-side P99 risk (§3.5, §5.3 of research) and is the direct justification for enforcing a chunk-length ceiling upstream of the reranker.

**Voyage AI rerankers**: token-based — `rerank-2.5` at $0.05/1M tokens, `rerank-2.5-lite` at $0.02/1M tokens, first 200M tokens free per account. Total billed tokens = `(query_tokens × num_docs) + Σ(doc_tokens)`, capped at 600K tokens/request. Voyage explicitly recommends `rerank-2.5-lite` and ≤200K tokens/request for latency-sensitive applications.

### 3.3 Cost of agentic multi-hop retrieval vs. single-shot

| Dimension | Classic (single-shot) RAG | Agentic RAG |
|---|---|---|
| Retrieval calls | 1 (fixed) | 2–7 (agent-decided) |
| Latency | 1–3s | 10–60s |
| Token cost | Baseline (1×) | **3–10× baseline** |
| Failure mode | Silent context miss | Runaway cost / loop non-termination |

**Cost formula** (per query, agentic path): `Cost_query ≈ h × (C_embed + C_retrieve + C_rerank) + C_generate_total`, where `h` is the realized hop count (bounded by the iteration cap from §2.3) and `C_generate_total` grows with `h` because each hop's observation is appended to the context the final generation call must process. This is why an unbounded or loosely-bounded agentic loop is a cost-tail risk, not just a latency-tail risk: cost and latency both scale with the same `h`.

Anthropic's **Contextual Retrieval** adds a small, fixed indexing-time and query-time overhead in exchange for a large retrieval-failure-rate reduction: **+$12 per 1,000 documents** at indexing time (using prompt caching for the LLM-generated situating-context step) and **+$0.03/query** for reranking — a comparatively small marginal spend for a 35–67% reduction in retrieval failure rate (§5.7 of research; reproduced in §5.4 below).

### 3.4 Vector DB query latency / throughput benchmarks

**Milvus 2.2.0** (1M SIFT vectors, HNSW M=8, efConstruction=200):

| CPU cores | Concurrency | QPS | P99 (ms) | P50 (ms) |
|---|---|---|---|---|
| 8 | 500 | 7,153 | 127 | 83 |
| 16 | 600 | 14,135 | 85 | 42 |
| 32 | 600 | 20,281 | 63 | 28 |

QPS scaled near-linearly both with CPU cores (8→32) and with query-node replicas (1→8: 7,153 → 30,655 QPS).

**pgvector HNSW**: index memory footprint ≈ **20–25 KB per 1536-dim vector** including graph overhead (vs. 6 KB for raw floats) — 10M rows requires **200–250 GB of RAM** to keep the index memory-resident; falling back to disk degrades latency from milliseconds to seconds. Recommended tuning: `m=16` (default), `ef_construction=128–200`, `ef_search=40–200` as the per-query recall/latency dial. `halfvec` (pgvector 0.7.0+) halves memory footprint with negligible recall loss.

**Enterprise reference (10M documents, Fortune 500 financial services)** — end-to-end latency budget:

| Stage | P50 | P95 |
|---|---|---|
| Query embedding | 8ms | 12ms |
| Vector search | 15ms | 25ms |
| Reranking | 20ms | 35ms |
| LLM generation | 45ms | 70ms |
| **Total** | **88ms** | **142ms** |

Peak sustained QPS: 850.

> ⚠️ Gap: no vendor publishes a directly comparable, apples-to-apples billion-scale (>1B vectors) benchmark across Pinecone/Weaviate/Milvus/Qdrant under identical hardware and dataset — cross-vendor scale claims should be treated as directional, sourced from each vendor's own benchmark methodology.

### 3.5 Latency SLA targets: P50/P95/P99 per stage, with mitigations

No public source discloses a formal p99 SLA for a *composed* hybrid+rerank+generation pipeline; the table below anchors P50 to the measured enterprise benchmark (§3.4) and extrapolates P95/P99 using a **1.5–2× multiplier over P50 for P95, and a further 1.5–1.8× over P95 for P99** — the standard tail-latency extrapolation convention applied elsewhere in this roadmap, justified because by P95 most steady-state variance is already absorbed and the P95→P99 gap is typically dominated by rare whole-pipeline events (a reranker long-document spike, a circuit-breaker trip, an agentic loop retry) rather than routine jitter.

| Stage | P50 (measured) | P95 (measured/inferred) | P99 `[inferred]` | Dominant tail cause | Mitigation |
|---|---|---|---|---|---|
| Query embedding | 8ms | 12ms | ~20ms | Cold embedding-model endpoint / batching queue | Warm connection pool; batch small queries only up to a latency-aware ceiling |
| Vector search | 15ms | 25ms | ~40ms | `ef_search` set too high for the recall target; disk fallback if index doesn't fit in RAM | Right-size `ef_search`; enforce full in-memory index residency (§3.4); read replicas to spread load |
| Sparse (BM25) search | ~5–10ms `[inferred, typically sub-vector-search]` | ~15ms | ~25ms | Large posting lists for common terms | Standard inverted-index optimizations (skip lists, term-frequency pruning) |
| Fusion (RRF/CC) | <1ms | <2ms | ~5ms | Large candidate union (top-500 legs) | Cap prefetch window (`rank_window_size`) to the minimum needed for target recall |
| Reranking | 20ms (short docs) | 35ms | **up to 7,350ms at 96×4096-tok batches** | Document token length, not batch size (§3.2) | Enforce a hard chunk-length ceiling before the reranker call; use `rerank-2.5-lite` for latency-sensitive paths; cap candidate pool to 50 for latency-tier tenants (§6.2 of research) |
| Generation (LLM) | 45ms `[first-token proxy; full generation is model/output-length dependent]` | 70ms | ~130ms | Output length variance, provider queueing | Streaming to first token; smaller fallback model on circuit-breaker trip (§4.4) |
| **Composed (dense+rerank+gen)** | **88ms** | **142ms** | **~220–250ms** `[inferred]` | Compounding of the reranker tail specifically | Treat reranking as the single highest-leverage latency-SLA lever in the entire pipeline |
| Agentic (h=3 hops) | ~10s `[h × single-hop P50 + generation overhead]` | ~20–30s | ~45–60s (approaching the 30–60s wall-clock timeout cap) | Loop iteration count itself | Hard iteration cap + wall-clock timeout is the primary P99 control, not per-stage tuning |

### 3.6 Throughput: QPS capacity planning and back-pressure design

**Capacity-planning formula** (single-tenant, dense+hybrid path):

```
Sustained QPS_capacity = min(
    VectorDB_QPS_at_target_P99,     # e.g. 20,281 QPS at 63ms P99, 32-core Milvus (§3.4)
    Reranker_RPS_at_candidate_pool, # e.g. ~5.86 RPS per unit at 96×64-tok docs (§3.2)
    LLM_provider_TPM_limit / avg_tokens_per_generation
)
```

The reranker is almost always the binding constraint in a hybrid+rerank pipeline — at 5.86 RPS per inference unit, sustaining 850 QPS (the enterprise benchmark's peak, §3.4) requires roughly **145 parallel reranker inference units**, whereas the same throughput on the vector DB side is achievable with a handful of replicas. Capacity plans must therefore size the reranker tier first and treat the vector DB as comparatively elastic.

**Back-pressure design**: apply a token-bucket rate governor per tenant/workload ahead of the reranker tier specifically (not just at the API gateway), with reservation semantics — a request reserves reranker capacity before the fused candidate set is even assembled, and releases the reservation if fusion produces fewer candidates than expected. This prevents the documented cascading pattern where reranker slowness triggers client-side retries, which further saturate an already-degraded reranker tier (§4.3). For agentic RAG specifically, back-pressure is expressed as the iteration cap and token budget from §2.3/§3.3 — these *are* the back-pressure mechanism, since there is no other natural queueing point in a loop the agent itself controls.

### 3.7 NFR Analysis: Availability, RPO/RTO, and Compliance Trade-offs

No vendor publishes an availability SLA scoped to "a hybrid-search+rerank+agentic RAG pipeline" as a composed system; every figure below is an **`[inferred/recommended]`** design target derived from the durability characteristics of each layer documented in §1 and §4, not a single published number. This section is the most commonly audited gap in RAG system-design writeups — the numbers below are stated explicitly, with the freshness-vs-cost and recall-vs-latency trade-offs that justify each choice.

**Availability targets by deployment pattern:**

| Deployment pattern | Availability target | Basis |
|---|---|---|
| Single-region: managed vector DB + BM25 index + reranker API, no fallback chain | **99.5%** (~44h/year downtime) | Bounded by the weakest external dependency — a third-party reranker API (Cohere/Voyage) typically publishes ~99.5–99.9% SLA tiers, and with no fallback chain the composed system inherits that ceiling directly |
| Single-region + circuit-breaker fallback chain (vector DB → keyword search → parametric LLM) | **99.9%** (~8.7h/year) | Each fallback tier absorbs the failure of the tier above it (§4.4); the user-visible "no answer" rate drops even though individual component availability is unchanged, because degraded-but-answered no longer counts as an outage |
| Multi-region: replicated vector DB + reranker + LLM endpoints, active-active | **99.95%** (~4.4h/year) | Cross-region replica failover removes single-region infra as a common-mode failure; residual risk is a correlated provider-wide outage (LLM API, embedding API) affecting all regions simultaneously |
| Agentic RAG with Temporal durable execution (§4.1) | **99.9%** at the workflow-orchestration layer; composed availability still bounded by the LLM provider's own SLA (~99.9% typical) | Temporal removes orchestration-layer failure as a cause of lost work (crashed workers resume, don't restart), but cannot exceed the availability of the LLM/retrieval APIs it calls |
| GraphRAG query-serving (community reports pre-computed, static between rebuilds) | **99.95%** for query serving | Query-time reads are served from a largely static graph + report store between indexing runs, decoupling query-path availability from the (much less available, batch-oriented) indexing pipeline |

**RPO/RTO tied to index-update granularity** (the explicit, audited-for section):

| Index type | Update mechanism | RPO (data loss / staleness window) | RTO (recovery time) |
|---|---|---|---|
| Vector index, real-time streaming CDC | Source DB WAL / webhook / file-hash diff triggers incremental re-embedding per changed chunk | **~15 minutes** (measured pipeline latency for a single document change under streaming CDC) | **Minutes** — restore from the most recent snapshot/replica; a crash loses only in-flight CDC events since the last committed manifest update |
| Vector index, nightly batch re-embed | Full or partial batch job on a fixed schedule | **24 hours nominal, up to 48–72 hours effective** for content created just after a batch window closes (documented "hallucinates more on Mondays" pattern: weekend-created docs not indexed until Monday night) | **Hours** — full re-embed of hundreds of thousands of chunks measured in hours, not minutes; partial (affected-chunk-only) re-embed cuts this by 90%+ |
| Sparse (BM25) index | Typically updated in lockstep with the vector index via the same CDC/batch trigger | Same as the paired vector index's RPO | Minutes (incremental) to hours (full rebuild) |
| GraphRAG graph + community reports | Full reindex (extraction + Leiden + summarization) is the only consistent update path; incremental updates don't resolve new entities against existing canonical ones | **Days to one quarter** in practice — graphs without automated refresh drift 15–20% from ground truth per quarter, and a full reindex is the only way to bound that drift, so RPO is effectively "time since last full reindex," not a tight, engineered number | **Hours to a full day+** for a full reindex on a non-trivial corpus (extraction + summarization dominate; embeddings are "nearly negligible by comparison" in GraphRAG's own cost breakdown) |
| Deletion propagation (any index type) | Manifest/side-table diff against valid chunk IDs, purge orphans | Bounded by the same CDC/batch cadence as content updates, but crash-consistency should favor **under-claiming deletions** (repairable via a re-scan) over over-claiming (unrecoverable without a full scan) — update the manifest only *after* both the index delete and the store's acknowledgment land | Minutes (manifest-diff-driven purge) to hours (full-scan reconciliation if the manifest itself is suspected corrupt) |

**Trade-off 1 — index freshness vs. cost.** Real-time streaming CDC buys a ~15-minute RPO but pays for it in per-event embedding-API overhead: small, frequent re-embedding batches lose the volume discount available to large nightly batches, and the CDC infrastructure itself (WAL tailing, webhook processing, hash-diff computation) is a standing cost even when no documents change. Nightly batch re-embedding is cheaper per token and operationally simpler, but its RPO is 20–100× wider (24h nominal vs. 15min), and the *effective* freshness gap for content created just before a quiet period can reach 48–72 hours — a gap that is invisible in aggregate uptime metrics but produces a specific, dangerous failure mode: a stale document returns a fluent, confidently wrong answer, which is worse than a missing document's honest "I don't have information about that." The recommended middle ground — **stable chunk IDs + content hash per chunk**, so only the changed chunk(s) re-embed regardless of trigger cadence — decouples cost from corpus size in both regimes and is the single highest-leverage freshness/cost lever available, independent of which RPO target is chosen.

**Trade-off 2 — recall vs. latency (reranker candidate-pool sizing).** Within a fixed total latency budget (e.g. 1,500ms end-to-end, per the multi-tenant reference design in §6), the reranker candidate-pool size is the direct dial trading retrieval correctness against P95/P99 latency: a 200-candidate pool maximizes the chance the correct chunk survives fusion and reaches generation, at a proportionally higher reranker latency contribution (§3.2's token-length-dominated cost curve makes this worse, not better, at scale); a 50-candidate pool is 4× cheaper and faster on the reranker stage but raises the risk of a recall failure (§2 of the failure taxonomy: "right chunk retrieved but ranked too low," or dropped from the pool entirely before reranking even runs). The reference pattern is to expose this as an explicit per-tenant or per-query-class configuration (200 for quality-sensitive tenants, 50 for latency-sensitive tenants) rather than a single global constant — the trade-off is a product decision, not purely an engineering one.

**Trade-off 3 — availability vs. consistency at the index layer.** Treating the vector index as eventually consistent (async CDC, batch windows) trades a bounded staleness window for higher ingestion throughput and simpler write paths — synchronous index updates on every source-document write would serialize ingestion behind index-write latency and reduce ingestion throughput, but would tighten RPO to near-zero. Because a stale-but-available answer is measurably more dangerous than an honest "don't know" (§5.2 of research), the correct default is **eventual consistency with an explicit, monitored freshness-lag SLO** (poll a canary document every 30 minutes; alert if lag exceeds 2 hours) rather than either extreme of "always synchronous" (throughput-limiting) or "no monitored bound at all" (the root cause of the documented Monday-staleness incident).

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution for multi-hop agentic retrieval

**Temporal** is the reference pattern for durable agentic-RAG loops. The **Workflow** is deterministic and holds the loop's conversation/retrieval state; it never calls the LLM, retriever, or reranker directly — it schedules **Activities** for all non-deterministic I/O (LLM calls, vector DB queries, reranker calls, tool calls) and awaits their results. Every Activity's input/output is recorded in an immutable **Event History**; on worker crash, Temporal **replays** the history so already-completed LLM/retrieval calls are not re-executed — only the last incomplete step resumes, eliminating both duplicate spend and non-deterministic re-planning that a naive retry-from-scratch would introduce. For long-running agentic sessions, `continue_as_new` caps unbounded event-history growth after N turns. Control flow should branch on the API's structured `stop_reason` field (e.g. `tool_use` vs. `end_turn`), not on parsed model prose — this is the deterministic contract the Workflow replays against, and parsing prose for control flow reintroduces exactly the non-determinism durable execution is meant to eliminate.

### 4.2 Index consistency, distributed locking, and checkpointing

- Vector indexes behave like **caches with no built-in invalidation strategy** — an embedding is frozen at creation time, and nothing in the index distinguishes a stale chunk from a fresh one; relevance-as-cosine-similarity and freshness are orthogonal dimensions the index simply doesn't encode.
- The recommended pattern is **Change Data Capture (CDC)** from the source system (DB WAL, webhook, file-hash diff) driving **incremental re-embedding**, so cost scales with change rate rather than corpus size (§3.7's freshness-vs-cost trade-off).
- **Stable chunk IDs + a content hash per chunk** are required so that an edit to one sentence in a 40-page document re-embeds only the affected chunk(s) — naive chunk-ID schemes (`doc_id + ":" + index`) break under edits, since inserting a sentence near the top of a document shifts every subsequent chunk's ID and silently invalidates stored citations pointing at the old IDs.
- **Full re-embed as a "fix"** is an anti-pattern at scale: for hundreds of thousands of chunks it costs real money, takes hours, and leaves the index in a **non-deterministic partially-stale state** for the run's duration — a query mid-run can straddle old and new embeddings, producing inconsistent answers to the same question asked seconds apart.
- **Deletion propagation** requires a manifest/side-table of valid chunk IDs per document; compute the set difference against the index and purge orphans, updating the manifest **only after** the store acknowledges both the index delete and the manifest write — crash-consistency should favor under-claiming (repairable via re-scan) over over-claiming (unrecoverable without a scan).
- **Distributed locking for concurrent writes**: when multiple ingestion workers race to re-embed the same document (e.g. a webhook fires twice, or a CDC replay overlaps a scheduled batch), a per-document lock (Redis `SET NX` with a TTL, or a Postgres advisory lock keyed on `doc_id`) must be acquired before re-embedding begins — without it, two workers can both compute new embeddings for the same chunk, one write clobbers the other, and the manifest's content-hash bookkeeping can end up pointing at a hash that doesn't match what's actually stored.
- **Checkpointing for GraphRAG indexing**: because a full GraphRAG index build (extraction + Leiden + summarization) runs for hours on non-trivial corpora, the pipeline should checkpoint after each stage (post-extraction, post-community-detection, post-summarization) so a crash mid-build resumes from the last completed stage rather than restarting the entire multi-hour, LLM-call-heavy job — this directly protects the majority cost driver identified in §3 (extraction + summarization, not embeddings, dominate GraphRAG indexing spend).

### 4.3 Failure taxonomy: transient, permanent, poison-pill

| Class | Definition | RAG-specific examples | Mitigation |
|---|---|---|---|
| **Transient** | Resolves on retry without intervention | Vector DB 503, reranker rate-limit 429, embedding API timeout | Retry with exponential backoff + full jitter; honor `Retry-After` before re-attempting |
| **Permanent** | Fails identically on every retry | Malformed query embedding dimension mismatch (index rebuilt with a different model), auth failure to the vector DB, a chunk ID referencing a document deleted from the manifest | Never retry — fail fast to the fallback chain (§4.4) |
| **Poison-pill** | A specific input deterministically breaks the same step every time | A single malformed/oversized document that crashes the chunker on every ingestion attempt; an agentic loop query pattern that never converges the "new evidence" check and retries the same reformulation indefinitely | Idempotency-keyed **claim-before-execute**: derive a stable `request_id`/`ingestion_id` from the document's content hash (not attempt metadata), atomically mark it `PENDING` before processing (Redis `SET NX` / Postgres unique constraint), and route repeat failures on the same ID to a **dead-letter queue** for manual inspection after N attempts rather than retrying forever |

**Dead-letter handling**: documents that fail ingestion N times (poison-pill candidates) and agentic retrieval traces that exhaust their iteration cap without reaching a terminal answer should both land in a dead-letter store tagged with the failure reason, the last-known state, and enough context (document ID + hash, or the full retrieval trace) to replay the failure deterministically once fixed — this is the same idempotency-key discipline as §4.2's distributed-locking pattern, applied to the failure path instead of the happy path.

**Idempotency keys in the retrieval path itself**: an agentic loop's tool calls (e.g. "search the knowledge base for X") should be keyed so that a Temporal replay (§4.1) resolves against the previously recorded result rather than re-issuing the same search — this is what makes replay-on-crash cost-neutral rather than cost-doubling.

### 4.4 Circuit breakers and fallback chains

Standard 3-state circuit breaker (Closed → Open → Half-Open) applied to vector DB and reranker calls:

- **Closed**: requests flow normally; the breaker tracks failure/slow-call rate over a sliding window.
- **Open**: once the failure rate crosses a threshold (e.g. 50% over a 10-request window, or a Resilience4j-style `slowCallDurationThreshold=2s`), the breaker fails fast — no network call attempted — for a cooldown period (e.g. 60s).
- **Half-Open**: a limited number of probe requests test recovery; success → Closed; any failure → back to Open.

**Fallback tiers specific to RAG**: vector DB down → fall back to keyword/Postgres full-text search (a "hot" vs. "cold" tier) → if that also fails, fall back to LLM parametric knowledge with no retrieved context (clearly flagged to the user as ungrounded) → if the primary LLM is also down, fall back to a smaller/faster model. Each degradation step trades quality for availability, but the user always receives *an* answer rather than a timeout. A **reranker-specific fallback** — reranker circuit open → fall back to dense-only (or fused-but-unreranked) ranking — is the most commonly needed fallback in practice, since the reranker is both the highest-latency stage (§3.5) and (per §3.2) the stage most prone to token-length-driven latency spikes that look like failures to an upstream timeout. **Semantic caching** as an additional fallback: when the circuit is open, check a cache of recently-answered similar queries before failing outright.

Retries (exponential backoff + jitter) are for **transient** faults; circuit breakers are for **sustained outages** — conflating the two causes retry storms that amplify load on an already-degraded vector DB or reranker, a documented cascading-failure pattern (retry → latency spike → orchestrator timeout → more agent instances spawned → more load) that is especially dangerous in agentic RAG, where a single logical query can already fan out into multiple hops before any retry logic even engages.

### 4.5 Enterprise security and governance

**Zero-Trust MCP**: every tool call an agentic RAG loop makes (web search, internal DB query, secondary retrieval tool) should route through a deny-by-default MCP gateway with short-lived, task-scoped credentials — no shared service accounts, and no tool granted broader access than the specific retrieval action requires. This matters more in RAG than in many agent contexts because the tools being called are frequently *data-access* tools, making an over-scoped credential a direct data-exfiltration risk, not just a blast-radius concern.

**Multi-tenancy: RBAC / row-level isolation in vector DBs:**

| Vector DB | Isolation model | RBAC maturity |
|---|---|---|
| Weaviate | Native multi-tenancy: each tenant gets a **separate physical shard**; supports 50,000+ active shards/node, 1M+ concurrently active tenants/cluster; inactive tenants auto-offload from memory | Fine-grained collection+tenant-level RBAC since v1.29.0 |
| Pinecone | **Namespaces** are logical partitions within a single index — **not a security boundary**; a compromised/over-scoped API key with index-level access reaches *all* namespaces in that index | No native RBAC; recommendation for security-sensitive multi-tenant workloads is one dedicated index per tenant with a scoped key |
| pgvector (Postgres) | Row-Level Security (RLS) at the database layer | Inherits Postgres's mature RBAC/RLS |
| Qdrant | JWT collection-scoping | — |

> ⚠️ Do not treat Pinecone namespace partitioning as a security boundary in a compliance-sensitive design — this is a commonly-tested interview trap, and the correct architectural response is one-index-per-tenant, not "use namespaces carefully."

**PII redaction in indexed documents** (detect → redact → audit): a reference AWS Bedrock Knowledge Bases pattern lands a document in S3, triggers an async Amazon Comprehend PII redaction job replacing entities with **typed placeholders** (`[NAME]`, `[SSN]`) rather than a uniform mask character (a uniform mask would create false-collision retrieval noise across many redacted documents), then routes the redacted output through Amazon Macie for secondary verification — severity ≥3 quarantines, severity <3 proceeds to ingestion. This is a **zero-trust, pre-ingestion redaction** pattern: PII never reaches the embedding step, vector store, or retrieval path. An alternative "selective redaction" pattern retains names for searchability while redacting contact info, applying reversible tokenization at query time on the combined retrieved-context + question, and restoring PII only in the final personalized response. Placement is critical either way: the PII redactor sits **between parsing and chunking** — the chunker, embedder, and vector store should never see raw PII. Under the EU AI Act, high-risk AI systems must demonstrate this data-governance compliance by **August 2027**.

**Permission-aware retrieval**: ACLs must be tagged on each chunk at ingestion time and filtered at the **ANN/pre-filter stage**, never post-filter after generation — post-filtering after ANN search can return fewer than `k` results if many top candidates get filtered out, and wastes the ANN search's compute on candidates that were never eligible to be returned. Defense-in-depth production designs apply pre-filtering (only search documents the user can access) *and* post-filtering (verify permissions before returning results) as a belt-and-suspenders pair, not a substitute for each other.

**Auditability of retrieval provenance**: enterprise/regulatory-grade RAG audit trails capture the full decision chain, immutably and append-only — original user query, actual retrieval query issued, retrieved chunk IDs + source doc ID + version/timestamp + similarity/rerank scores, the full context window sent to the LLM, the generated response, and ideally a cryptographic signature (SHA-256/Ed25519) chaining query→retrieval→answer for tamper evidence. Regulatory mappings: **EU AI Act Art. 30** (log every inference: input, output, sources, model identity, timestamp, user), **GDPR Art. 30** (records of processing activities), **HIPAA §164.312(b)** (audit controls for PHI-touching systems), **SOX/MiFID II** (financial services: 5–7 year retention, versioning to reconstruct "what the system knew" at any point in time). The practical blocker to production sign-off is usually not retrieval quality but the fact that retriever, reranker, and LLM log independently — a unified lineage record requires instrumenting the **orchestration layer** to emit structured events centrally.

**Format-parser attack surface**: PDF/DOCX/XML parsers are a documented CVE history (e.g. a malicious XFA payload embedded in a PDF triggering XXE escalating to RCE/SSRF in an over-privileged parser). The "airlock" pattern — never parsing untrusted documents in the same process/container as the application or vector DB, running parsers in ephemeral, sandboxed, zero-egress environments with strict resource limits — is the correct isolation boundary for the ingestion pipeline specifically, since documents from external/untrusted sources are also a known indirect prompt-injection vector once retrieved and placed in a generation context.

---

## 5. Production Enterprise Code

The implementation below is a hardened Python hybrid-search + rerank retrieval pipeline wiring together every resilience pattern from §3–§4: retries with exponential backoff + full jitter for transient faults, a per-dependency circuit breaker (CLOSED→OPEN→HALF_OPEN) for the vector DB, sparse index, and reranker, a fallback chain (reranker failure → dense-only ranking; vector DB failure → sparse-only ranking), structured JSON logging with a correlation ID per request, and graceful degradation so a caller always receives a ranked result set rather than a bare exception. It uses only the standard library plus a documented dependency substitution point for real vector/sparse/reranker clients.

```python
"""
production_hybrid_rag_pipeline.py

A production-hardened hybrid-search + reranking retrieval pipeline
demonstrating every pattern from Module 06 (RAG) Sec 3-4:

  - RRF fusion of dense + sparse retrieval legs (Sec 2.1)
  - per-dependency circuit breaker: CLOSED -> OPEN -> HALF_OPEN (Sec 4.4)
  - retry with exponential backoff + full jitter for transient errors
    (Sec 4.3's transient/permanent/poison-pill taxonomy)
  - a fallback chain: reranker failure -> dense-only ranking;
    vector DB failure -> sparse-only ranking (Sec 4.4)
  - structured JSON logging with a per-request correlation ID
    (Sec 4.5 audit-logging / retrieval-provenance minimum bar)
  - idempotency-keyed retrieval so a retried call resolves against a
    cached result instead of re-querying (Sec 4.3)

Install:  no dependencies (stdlib only; swap the Mock* clients for
          real vector DB / sparse index / reranker SDKs in production)
Run:      python production_hybrid_rag_pipeline.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


# --------------------------------------------------------------------------
# 1. Structured logging with per-request correlation IDs (Sec 4.5)
# --------------------------------------------------------------------------

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get()
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("hybrid_rag_pipeline")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"correlation_id":"%(correlation_id)s","msg":%(message)s}'
        )
    )
    handler.addFilter(CorrelationIdFilter())
    logger.handlers = [handler]
    logger.propagate = False
    return logger


log = configure_logging()


class request_scope:
    """Binds one correlation ID to every log line for a single query,
    so a full retrieval trajectory (dense leg, sparse leg, fusion,
    rerank, fallback decisions) can be reconstructed for audit
    (Sec 4.5) independent of which stage emitted the log."""

    def __init__(self, request_id: Optional[str] = None):
        self.request_id = request_id or str(uuid.uuid4())
        self._token = None

    def __enter__(self) -> str:
        self._token = _correlation_id.set(self.request_id)
        return self.request_id

    def __exit__(self, *exc_info) -> None:
        _correlation_id.reset(self._token)


# --------------------------------------------------------------------------
# 2. Failure taxonomy (Sec 4.3): transient vs. permanent
# --------------------------------------------------------------------------

class RetrievalError(Exception):
    """`transient=False` marks permanent errors that must never be
    retried (auth failure, embedding-dimension mismatch, malformed
    query)."""

    def __init__(self, message: str, transient: bool = True):
        super().__init__(message)
        self.transient = transient


# --------------------------------------------------------------------------
# 3. Retry with exponential backoff + full jitter (Sec 4.3)
# --------------------------------------------------------------------------

def backoff_with_full_jitter(attempt: int, base_s: float = 0.1, cap_s: float = 4.0) -> float:
    upper_bound = min(cap_s, base_s * (2 ** attempt))
    return random.uniform(0, upper_bound)


def call_with_retry(fn: Callable[[], Any], max_attempts: int = 3,
                     base_s: float = 0.1, cap_s: float = 4.0) -> Any:
    last_error: Optional[RetrievalError] = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except RetrievalError as exc:
            last_error = exc
            if not exc.transient:
                log.info(json.dumps({"event": "retry_aborted_permanent_error", "reason": str(exc)}))
                raise
            if attempt < max_attempts - 1:
                delay = backoff_with_full_jitter(attempt, base_s, cap_s)
                log.info(json.dumps({"event": "retry_backoff", "attempt": attempt + 1,
                                      "delay_s": round(delay, 3)}))
                time.sleep(delay)
    assert last_error is not None
    raise last_error


# --------------------------------------------------------------------------
# 4. Circuit breaker: CLOSED -> OPEN -> HALF_OPEN, per dependency (Sec 4.4)
# --------------------------------------------------------------------------

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold_ratio: float = 0.5
    window_size: int = 10
    cooldown_s: float = 15.0
    half_open_max_probes: int = 2

    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _outcomes: list = field(default_factory=list, init=False)
    _opened_at: float = field(default=0.0, init=False)
    _half_open_probes_used: int = field(default=0, init=False)

    def _failure_ratio(self) -> float:
        if not self._outcomes:
            return 0.0
        return sum(1 for ok in self._outcomes if not ok) / len(self._outcomes)

    def allow_request(self) -> bool:
        if self._state == BreakerState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = BreakerState.HALF_OPEN
                self._half_open_probes_used = 0
                log.info(json.dumps({"event": "breaker_half_open", "dependency": self.name}))
            else:
                return False
        if self._state == BreakerState.HALF_OPEN:
            if self._half_open_probes_used >= self.half_open_max_probes:
                return False
            self._half_open_probes_used += 1
        return True

    def record_success(self) -> None:
        self._outcomes.append(True)
        self._outcomes = self._outcomes[-self.window_size:]
        if self._state == BreakerState.HALF_OPEN:
            self._state = BreakerState.CLOSED
            self._outcomes.clear()
            log.info(json.dumps({"event": "breaker_closed", "dependency": self.name}))

    def record_failure(self) -> None:
        self._outcomes.append(False)
        self._outcomes = self._outcomes[-self.window_size:]
        if self._state == BreakerState.HALF_OPEN:
            self._trip("half_open_probe_failed")
            return
        if len(self._outcomes) >= self.window_size and self._failure_ratio() >= self.failure_threshold_ratio:
            self._trip("failure_ratio_exceeded")

    def _trip(self, reason: str) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = time.monotonic()
        log.info(json.dumps({"event": "breaker_open", "dependency": self.name, "reason": reason,
                              "failure_ratio": round(self._failure_ratio(), 3)}))


_BREAKERS: dict[str, CircuitBreaker] = {}


def get_breaker(dep_name: str) -> CircuitBreaker:
    if dep_name not in _BREAKERS:
        _BREAKERS[dep_name] = CircuitBreaker(name=dep_name, window_size=5,
                                              failure_threshold_ratio=0.6, cooldown_s=10)
    return _BREAKERS[dep_name]


# --------------------------------------------------------------------------
# 5. Idempotency cache for retried retrieval calls (Sec 4.3)
# --------------------------------------------------------------------------

_IDEMPOTENCY_CACHE: dict[str, Any] = {}


def idempotency_key(dependency: str, query: str, params: dict) -> str:
    payload = json.dumps({"dependency": dependency, "query": query, "params": params}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def call_idempotent(dependency: str, query: str, params: dict, fn: Callable[[], Any]) -> Any:
    key = idempotency_key(dependency, query, params)
    if key in _IDEMPOTENCY_CACHE:
        log.info(json.dumps({"event": "idempotent_cache_hit", "dependency": dependency, "key": key[:12]}))
        return _IDEMPOTENCY_CACHE[key]
    result = fn()
    _IDEMPOTENCY_CACHE[key] = result
    return result


# --------------------------------------------------------------------------
# 6. Mock retrieval dependencies (swap for real SDKs in production)
# --------------------------------------------------------------------------

def mock_dense_search(query: str, top_k: int = 50) -> list[dict]:
    if random.random() < 0.15:
        raise RetrievalError("vector DB timeout", transient=True)
    return [{"id": f"dense-{i}", "text": f"dense result {i} for '{query}'", "score": 1.0 - i * 0.01}
            for i in range(top_k)]


def mock_sparse_search(query: str, top_k: int = 50) -> list[dict]:
    if random.random() < 0.10:
        raise RetrievalError("BM25 index unavailable", transient=True)
    return [{"id": f"sparse-{i}", "text": f"sparse result {i} for '{query}'", "score": 20.0 - i * 0.3}
            for i in range(top_k)]


def mock_reranker(query: str, candidates: list[dict], top_k: int = 10) -> list[dict]:
    if random.random() < 0.25:
        raise RetrievalError("reranker overloaded / timeout", transient=True)
    # simulate reranking by shuffling scores deterministically per id
    scored = sorted(candidates, key=lambda c: hash((query, c["id"])) % 1000, reverse=True)
    return scored[:top_k]


# --------------------------------------------------------------------------
# 7. RRF fusion (Sec 2.1)
# --------------------------------------------------------------------------

def reciprocal_rank_fusion(ranked_lists: list[list[dict]], k: int = 60) -> list[dict]:
    scores: dict[str, float] = {}
    docs: dict[str, dict] = {}
    for ranked_list in ranked_lists:
        for rank, doc in enumerate(ranked_list, start=1):
            scores[doc["id"]] = scores.get(doc["id"], 0.0) + 1.0 / (k + rank)
            docs[doc["id"]] = doc
    fused_ids = sorted(scores, key=lambda doc_id: scores[doc_id], reverse=True)
    return [{**docs[doc_id], "rrf_score": scores[doc_id]} for doc_id in fused_ids]


# --------------------------------------------------------------------------
# 8. The hybrid-search + rerank pipeline with breakers + fallback chain
# --------------------------------------------------------------------------

def hybrid_search_and_rerank(query: str, top_k: int = 10) -> dict:
    """Returns a dict with the final ranked results, the retrieval mode
    actually used (for observability/audit), and per-stage timings.
    Never raises -- always degrades gracefully (Sec 4.4)."""
    stage_source = {"dense": "unavailable", "sparse": "unavailable", "rerank": "unavailable"}
    ranked_lists: list[list[dict]] = []

    dense_breaker = get_breaker("vector_db")
    if dense_breaker.allow_request():
        try:
            dense_results = call_with_retry(
                lambda: call_idempotent("vector_db", query, {"top_k": 50},
                                         lambda: mock_dense_search(query, top_k=50))
            )
            dense_breaker.record_success()
            ranked_lists.append(dense_results)
            stage_source["dense"] = "ok"
        except RetrievalError as exc:
            dense_breaker.record_failure()
            log.info(json.dumps({"event": "dense_leg_failed", "error": str(exc)}))
    else:
        log.info(json.dumps({"event": "dense_leg_skipped_breaker_open"}))

    sparse_breaker = get_breaker("sparse_index")
    if sparse_breaker.allow_request():
        try:
            sparse_results = call_with_retry(
                lambda: call_idempotent("sparse_index", query, {"top_k": 50},
                                         lambda: mock_sparse_search(query, top_k=50))
            )
            sparse_breaker.record_success()
            ranked_lists.append(sparse_results)
            stage_source["sparse"] = "ok"
        except RetrievalError as exc:
            sparse_breaker.record_failure()
            log.info(json.dumps({"event": "sparse_leg_failed", "error": str(exc)}))
    else:
        log.info(json.dumps({"event": "sparse_leg_skipped_breaker_open"}))

    if not ranked_lists:
        # both retrieval legs are down -- the deepest fallback: no
        # grounded context, caller must decide whether to answer from
        # parametric knowledge or surface a clear "search unavailable".
        log.info(json.dumps({"event": "full_retrieval_outage"}))
        return {"results": [], "mode": "no_retrieval_available", "sources": stage_source}

    fused = reciprocal_rank_fusion(ranked_lists, k=60)
    candidate_pool = fused[:100]

    rerank_breaker = get_breaker("reranker")
    if rerank_breaker.allow_request():
        try:
            reranked = call_with_retry(lambda: mock_reranker(query, candidate_pool, top_k=top_k))
            rerank_breaker.record_success()
            stage_source["rerank"] = "ok"
            mode = "hybrid_reranked" if len(ranked_lists) == 2 else "single_leg_reranked"
            return {"results": reranked, "mode": mode, "sources": stage_source}
        except RetrievalError as exc:
            rerank_breaker.record_failure()
            log.info(json.dumps({"event": "reranker_failed_falling_back_to_fused_order", "error": str(exc)}))
    else:
        log.info(json.dumps({"event": "reranker_skipped_breaker_open_falling_back"}))

    # fallback: reranker unavailable -> serve the fused (dense+sparse
    # RRF, or single-leg) ranking directly, never a bare exception
    mode = "hybrid_fused_unranked" if len(ranked_lists) == 2 else "single_leg_unranked"
    return {"results": candidate_pool[:top_k], "mode": mode, "sources": stage_source}


# --------------------------------------------------------------------------
# 9. Example run
# --------------------------------------------------------------------------

if __name__ == "__main__":
    with request_scope() as request_id:
        log.info(json.dumps({"event": "request_start", "query": "quarterly revenue recognition policy"}))
        outcome = hybrid_search_and_rerank("quarterly revenue recognition policy", top_k=5)
        log.info(json.dumps({
            "event": "request_complete",
            "mode": outcome["mode"],
            "sources": outcome["sources"],
            "result_count": len(outcome["results"]),
        }))
        print(json.dumps({"request_id": request_id, **outcome}, indent=2))
```

**What each pattern buys, mapped back to §3–§4.** The per-dependency `CircuitBreaker` isolates the vector DB, sparse index, and reranker from each other's failures — a reranker outage never blocks the dense/sparse legs from completing, and a vector DB outage automatically degrades to sparse-only RRF (degenerating gracefully to a single-leg ranked list) rather than failing the whole request. `call_with_retry` handles only transient faults with backoff+jitter, while the breaker handles sustained outages — keeping the two mechanisms separate is what prevents the retry-storm cascade described in §4.4. `call_idempotent` ensures a retried dense/sparse call resolves against a cached result rather than re-querying, directly modeling the Temporal-replay idempotency requirement from §4.1 in a simpler synchronous form. The fallback chain is explicit and ordered — hybrid+reranked → hybrid fused-but-unranked → single-leg → no-retrieval — and the `mode` field returned with every response is exactly the provenance signal §4.5's audit requirement calls for: a downstream consumer (or auditor) can always tell whether an answer was grounded in a fully-reranked hybrid result or served from a degraded fallback tier. `request_scope` binds one correlation ID to every log line for the request's lifetime, satisfying the audit-logging minimum bar without needing a separate tracing system bolted on after the fact.

---

## 6. Architectural System Design Scenarios

### Scenario A — Multi-tenant hybrid-search RAG for a Fortune 500 financial-services knowledge base

**Problem statement.** A financial-services enterprise needs to expose 10M documents (50M vectors) drawn from SharePoint, Confluence, and internal systems to internal analysts and (in a separate tenant tier) to end-customers via a support assistant, with strict per-tenant document-level access control, regulatory audit requirements (SOX/MiFID II-grade retention and provenance), and a hard latency budget for interactive use (sub-200ms P95 target) at up to 850 peak QPS.

**Proposed architecture.**

```
Sources (SharePoint, Confluence, internal DBs) → intelligent, structure-
                                                   aware chunking → domain-
                                                   tuned dense embedding
                                                              │
                                                              ▼
                              Sharded, namespace-per-tenant vector store
                              + metadata filters; BM25 sparse index kept
                              in lockstep via the same CDC pipeline (§4.2)
                                                              │
                                                              ▼
                Query: intent classification/expansion → hybrid retrieval
                (dense + sparse + RRF, top-500 window) → pre-filter on
                tenant ACL at the ANN stage (never post-filter, §4.5)
                                                              │
                                                              ▼
                    Cross-encoder rerank (top-100 → top-10..20; 200-
                    candidate pool for quality-sensitive tenants, 50 for
                    latency-sensitive tenants within a 1,500ms budget, §3.7)
                                                              │
                                                              ▼
                Grounded generation + citation extraction → immutable
                provenance log (query, chunk IDs, scores, context, SHA-256
                chain) → post-filter permission re-verification (defense
                in depth, §4.5)
```

**Trade-off matrix** (hybrid+rerank on sharded vector DB vs. two alternatives):

| Dimension | Proposed: hybrid + rerank, namespace/shard-per-tenant | Pure dense vector, single shared index with metadata filters | GraphRAG (community-report-based) for the same corpus |
|---|---|---|---|
| Cost / 1k queries | Moderate — RRF fusion adds negligible compute; reranker is the dominant marginal cost (§3.2, §3.6) | Lowest — no sparse leg, no reranker | Highest — 90%+ of cost is one-time LLM extraction/summarization at index time, but query-time global search still costs a map-reduce over community reports |
| Latency | 88ms P50 / 142ms P95 measured (§3.4) | Lower P50 (no rerank stage) but materially worse recall on exact-match queries (account numbers, ticker symbols) — BM25's specific strength | Fast for local/entity queries, but global (thematic) queries pay a multi-call map-reduce cost per query |
| Security/multi-tenancy | Shard-per-tenant gives a true isolation boundary (blast-radius containment); ACL pre-filtering at the ANN stage is structurally required regardless of vector DB choice | Namespace-only isolation on Pinecone-class DBs is **not a security boundary** (§4.5) — unacceptable for a regulated fintech without moving to one-index-per-tenant | Same ACL-tagging requirement, but graph traversal paths must *also* respect per-tenant boundaries — an under-documented extra dimension of complexity |
| Recall for exact-match (account IDs, product codes) | Strong — BM25 leg handles rare/exact tokens embeddings underweight | Weak — dense embeddings systematically underweight rare tokens/IDs | N/A — GraphRAG is a poor fit for narrow exact-match lookups by design |
| Ops complexity | Higher — two indexes (dense+sparse) to keep in sync via CDC, plus reranker capacity planning (§3.6) | Lower — one index, no reranker capacity tier | Highest — three indexes to keep in sync (full-text, vector, graph); most tooling doesn't solve this cleanly |
| Compliance/audit fit | Strong — provenance log captures fused+reranked scores and the exact context sent to the LLM, satisfying EU AI Act Art. 30 / SOX retention (§4.5) | Weaker recall on exact-match queries increases the "wrong chunk retrieved" failure mode a regulator would flag | Community-report summarization is itself a form of LLM-generated content in the provenance chain, complicating "what did the system actually know" reconstruction |

**Decision rationale.** Hybrid search with reranking on a shard-per-tenant vector store is selected because financial-services queries mix exact-match lookups (account numbers, regulatory filing IDs) — where BM25's strength is decisive and pure-dense retrieval measurably underperforms — with semantic paraphrase queries where dense embeddings win; RRF fusion captures both without requiring the query router to guess which mode a given query needs. Shard-per-tenant is a compliance-driven, non-negotiable choice given the explicit finding that namespace-only isolation is not a defensible security boundary for a regulated multi-tenant workload. GraphRAG is deliberately excluded from the primary path: this corpus's dominant query shape is specific-document lookup, not "summarize the last quarter's regulatory changes across the whole corpus," so GraphRAG's 90%-of-cost-in-indexing profile and triple-index-synchronization burden aren't justified by the query mix — the recommended adoption pattern (start with vector RAG, prove GraphRAG's value on a narrow, genuinely thematic question set before scaling) applies directly here, and a GraphRAG add-on could be layered in later purely for the analyst-facing "give me an overview" query class if demand emerges.

### Scenario B — Agentic, graph-augmented research assistant for enterprise M&A due diligence

**Problem statement.** A consulting/advisory firm needs an assistant that answers both narrow factual questions ("what was Company X's debt covenant in the 2024 credit agreement?") and holistic, multi-document synthesis questions ("summarize all litigation risk themes across the data room") over a due-diligence corpus that changes incrementally as new documents are uploaded during an active deal, with human review required before any conclusion is delivered to a client, and a business requirement to support genuine multi-hop reasoning (a covenant reference in one document pointing to a defined term in another) without an unbounded-cost or non-terminating retrieval loop.

**Proposed architecture.**

```
New document uploaded → CDC-triggered incremental chunking + dense/
                         sparse indexing (Sec 4.2) AND incremental
                         entity/relationship extraction feeding the
                         graph index (full Leiden re-run scheduled,
                         not per-document -- Sec 4.2/3.7 RPO trade-off)
                                          │
                Query classified: narrow-factual → hybrid+rerank path
                (Scenario A's pipeline) | thematic/synthesis → GraphRAG
                global search (map-reduce over community reports) |
                multi-hop factual (covenant → cross-referenced defined
                term) → Agentic RAG loop (plan→retrieve→grade→decide,
                Sec 2.3), Temporal-backed for durable multi-hop execution
                (Sec 4.1), capped at 5 iterations / 40k tokens / 60s wall-
                clock (Sec 3.3/3.6)
                                          │
                                          ▼
                Every path converges on: provenance log (which mode
                served the query, retrieved chunk/community/hop chain)
                → mandatory human-review gate before client delivery
```

**Trade-off matrix** (three retrieval-mode router vs. two simpler alternatives):

| Dimension | Proposed: routed hybrid + GraphRAG + agentic | Vector-RAG-only (no graph, no agentic loop) | Agentic-RAG-only (no GraphRAG, treat thematic queries as many-hop retrieval) |
|---|---|---|---|
| Cost / 1k queries | Highest fixed cost (three retrieval paths to build and maintain) but each query pays only the cost its mode actually requires | Lowest — single retrieval path | Moderate-high — thematic queries forced through an agentic loop cost 3–10× baseline (§3.3) even for what GraphRAG would answer in one map-reduce pass |
| Latency | Narrow queries: ~88-142ms (Scenario A numbers); thematic: GraphRAG global search latency `[inferred, dominated by community-report count]`; multi-hop: 10-60s (agentic, §3.3) | Fastest per-query, but multi-hop and thematic queries get a shallow, single-retrieval-hop answer that misses cross-document synthesis | Multi-hop and thematic queries both pay agentic-loop latency (10-60s) even when a much cheaper graph map-reduce would suffice for the thematic case |
| Answer quality on thematic queries | Strong — GraphRAG is purpose-built for "summarize themes across the corpus" | Weak — a single retrieval hop over even a large top-k window doesn't reliably surface a corpus-wide theme | Better than vector-only, but reconstructing a GraphRAG-equivalent theme summary via repeated single-hop retrieval is both more expensive and less reliable than pre-computed community reports |
| Answer reliability on multi-hop factual queries | Bounded degradation — GraphRAG's `p^h` compounding error (§2.4) is avoided for these queries by using the agentic vector-retrieval path instead of graph traversal for hop-chasing, keeping reliability tied to retrieval recall rather than entity-resolution accuracy | Fails silently — a single retrieval hop cannot chase a cross-document reference at all (§5 of research, failure mode #2: right chunk missed entirely) | Handles multi-hop correctly, same reliability profile as the proposed design for this query class specifically |
| Ops complexity | Highest — three indexes (dense/sparse/graph) to keep consistent, a query router to maintain, and Temporal infrastructure for durable agentic execution | Lowest | Medium — one fewer index type (no graph) but still requires the full agentic-loop guardrail infrastructure (iteration cap, token budget, wall-clock timeout, Temporal durability) |
| Freshness/RPO fit | Graph index intentionally has a wider RPO (batched Leiden re-runs, §3.7) than the vector/sparse indexes (near-real-time CDC) — an explicit, accepted trade-off since new documents are still searchable via the narrow-query path immediately, only the thematic-summary view lags | Uniformly tight RPO, but no thematic-query capability at all makes the RPO question moot for that use case | Uniformly tight RPO (no graph index to go stale), but pays the ongoing latency/cost tax on thematic queries described above |

**Decision rationale.** The three-mode router is justified specifically because this workload has three genuinely distinct query shapes with no single retrieval mechanism that serves all of them well: narrow factual lookups are exactly what hybrid+rerank is built for, thematic synthesis is exactly what GraphRAG's community-report map-reduce is built for, and cross-document multi-hop factual chasing is exactly what a bounded agentic loop is built for — forcing any one query class through a mismatched mode either fails silently (vector-only on thematic/multi-hop) or wastes cost and latency (agentic-only on thematic). The decision to route multi-hop factual chasing through the *agentic vector-retrieval* path rather than graph traversal, even though a knowledge graph already exists in this design, is deliberate: GraphRAG's `p^h` compounding entity-resolution error (§2.4) makes graph-hop-chasing measurably less reliable for precise factual chains than iterative vector retrieval, whose failure mode (recall miss) is more diagnosable and doesn't compound multiplicatively per hop. The wider, batch-cadence RPO accepted for the graph index specifically (vs. near-real-time CDC for vector/sparse) is the direct application of §3.7's freshness-vs-cost trade-off to this scenario: a full Leiden re-run is expensive enough that running it per-document-upload isn't justified when the narrow-query path already serves newly uploaded documents immediately, so the graph — and therefore the thematic-summary view — is allowed to lag by design, with that lag surfaced explicitly to users rather than hidden.

---

> ⚠️ Data gaps carried over from the primary source: no vendor publishes a directly comparable, apples-to-apples billion-scale (>1B vectors) production latency benchmark across Pinecone/Weaviate/Milvus/Qdrant under identical hardware and dataset; no public source quantifies "average enterprise incident cost" for stale-index or GraphRAG entity-resolution failures; and the availability/RPO/RTO figures in §3.7 are architect-inferred design targets, not published SLAs, since no vendor scopes an SLA to a composed hybrid-search+rerank+agentic pipeline as a whole.
