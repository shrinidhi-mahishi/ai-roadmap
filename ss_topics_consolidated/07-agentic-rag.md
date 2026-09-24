# Topic 7: Agentic RAG
> Consolidated from multi-model research (Grok + Opus) | Study & Interview Prep

---

## Introduction

### What This Topic Covers

Agentic RAG takes retrieval-augmented generation from a simple "retrieve-then-generate" pipeline to an **intelligent, self-correcting retrieval system** where the agent decides when, what, and how to retrieve. This topic covers the full spectrum: query rewriting (HyDE, step-back, multi-query decomposition), hybrid search (dense + sparse + metadata with RRF fusion), reranking (cross-encoder, ColBERT, Cohere), Self-RAG with reflection tokens, Corrective RAG with web search fallback, multi-hop reasoning (IRCoT, FLARE, CoRAG), citation tracking and attribution, GraphRAG, RAPTOR, and Anthropic's contextual retrieval. It's the difference between a system that retrieves blindly and one that retrieves intelligently.

### Why Study This

- **Most asked topic**: RAG is the most common production use case for LLMs, and "how would you improve this RAG system?" is the most frequent system design interview question.
- **Naive RAG fails**: 73% of RAG failures trace to retrieval, not generation. Moving from naive RAG to agentic RAG improves accuracy by +33% overall and +52% for complex queries — at ~10x the cost. Knowing when this tradeoff is worth it is a Director-level decision.
- **Rapidly evolving**: Self-RAG, CRAG, GraphRAG, RAPTOR, and contextual retrieval are all 2024-2025 innovations. Interviewers test whether you know the state of the art.
- **Citation is mandatory**: In regulated industries (finance, healthcare, legal), every claim must cite its source. Citation hallucination (model cites wrong sources) is a real production failure. This topic covers 5 attribution techniques that reduce hallucination from 8-15% to <3%.

### What Details Are Included

- Full agentic RAG pipeline from query to grounded, cited response
- Hybrid search with RRF fusion formula and WANDS benchmark results
- Reranking comparison (cross-encoder vs ColBERT vs Cohere) with latency and cost
- Self-RAG with 4 reflection tokens and ICLR 2024 results
- CRAG with 3 corrective paths and quality evaluator
- RAGAS metrics (faithfulness, answer relevancy, context precision/recall) with healthy benchmarks
- Vector DB scaling comparison (Pinecone, Qdrant, Weaviate, Milvus, pgvector) at 1M and 100M scale
- Document-level access control (RBAC/ABAC) for multi-tenant RAG
- NLI-based citation verification code
- Two enterprise system design scenarios with trade-off matrices

### How to Approach This Document

> **First pass (2-3 hours)**: Sections 1-4. Trace through the full retrieval pipeline diagram. Understand the progression from naive → advanced → agentic RAG. Study the RRF fusion formula.
>
> **Second pass (2-3 hours)**: Sections 5-7. Work through the per-query cost breakdown. Run the hybrid search and citation verification code. Study Self-RAG and CRAG — these are the patterns interviewers ask about most.
>
> **Interview prep (1 hour)**: Section 10. Practice explaining the agentic RAG pipeline in 5 minutes. Memorize the RAGAS metric benchmarks and the cost-quality tradeoff table.
>
> **Before an interview (30 min)**: Re-read section 10 only.

### How This Document Is Structured

This guide follows a **10-section progressive learning flow** — each section builds on the previous:

| # | Section | What It Covers | Study Approach |
|---|---------|---------------|----------------|
| 1 | Concept Overview | What and why | Read first for orientation |
| 2 | Core Concepts | Fundamental building blocks | Study deeply, take notes |
| 3 | Architecture & System Design | ASCII diagrams, topology, data flow | Draw diagrams from memory |
| 4 | Key Algorithms & Mechanics | Technical depth, complexity analysis | Understand the "why" |
| 5 | Token Economics & Cost Analysis | Pricing, cost formulas, optimization | Memorize key numbers |
| 6 | Production Patterns & Code | Runnable Python implementations | Run, modify, and break the code |
| 7 | Failure Modes & Mitigations | What goes wrong, how to handle it | Practice explaining failure scenarios |
| 8 | Security & Governance | Enterprise security considerations | Know compliance frameworks by name |
| 9 | System Design Scenarios | Real-world problems with trade-offs | Practice whiteboarding these |
| 10 | Interview Quick Reference | Key numbers, frameworks, talking points | Review 30 min before interviews |

---

**Scope**: End-to-end architecture of agentic retrieval-augmented generation -- query analysis, hybrid search, reranking, self-reflective retrieval, citation tracking, multi-hop reasoning, enterprise security, production code, and system design scenarios.

**Key papers**: Self-RAG (Asai et al., ICLR 2024), CRAG (Yan et al., 2024), Adaptive-RAG (Jeong et al., NAACL 2024), IRCoT (Trivedi et al., ACL 2023), FLARE (Jiang et al., EMNLP 2023), RAPTOR (Sarthi et al., ICLR 2024), CoRAG (arXiv:2501.14342), GraphRAG (Microsoft Research 2024), Contextual Retrieval (Anthropic Sep 2024), HippoRAG (NeurIPS 2024), ALCE (Gao et al., EMNLP 2023).

**Pricing assumptions**: OpenAI text-embedding-3-small $0.02/1M tokens; text-embedding-3-large $0.13/1M; Voyage-4-large ~$0.12/1M; Cohere Rerank v3.5 $2/1K searches (Bedrock/aggregator rate, not cohere.com HTML); Claude Sonnet 5 input $2/1M, output $10/1M; GPT-4o input $2.50/1M, output $10/1M. All as of mid-2026.

---

## 1. Concept Overview

### 1.1 What Is Agentic RAG?

A production agentic RAG product is **two independently scaled planes sharing versioned indexes**, plus a **bounded control loop** around those indexes.

The generator's **parametric** memory is not the corpus (Lewis, Perez, Piktus et al., NeurIPS 2020). Non-parametric memory is an index. The model never searches. It emits a tool call or a rewritten query; the retriever executes under an ACL predicate; chunks return as observations.

**Ingest (write)** parses, DLP-redacts, ACL-stamps, chunks (parent/child), contextualizes, embeds, sparse-encodes, and checkpoints. **Query (read)** authz-filters, hybrid-retrieves, fuses (RRF / RSF / alpha), reranks (a **tool**, not a generator prefill), then runs `retrieve -> grade -> rewrite -> generate` with a hop cap.

### 1.2 Naive vs Advanced vs Agentic vs Memory

| Product | Retrieve | Loop | Isolation | When to use |
| --- | --- | --- | --- | --- |
| **Naive RAG** | Always top-k dense | None | Shared corpus | Demo only; fails IDs and ACL |
| **Advanced RAG** | Query transform + hybrid + rerank | **DAG**, one pass | Shared, versioned docs | **80% of enterprise KB chat**; single-pass Q&A |
| **Agentic RAG** | Retrieval is a **tool** | Bounded `retrieve -> grade -> rewrite -> generate` | Same filters on **every hop** | Support/research copilot; ambiguous + multi-hop |
| **Agent memory (Module 06)** | Per-user Store / Mem0 / Graphiti | Write path = extract | Per-user / per-agent | Interaction stream, **not** the world corpus |

**Why it matters -- a concrete example**: Enterprise support receives "what is error TS-999?" Naive RAG embeds the query, returns the nearest 5 chunks (may miss the exact code), and generates. Advanced RAG does BM25 (catches "TS-999" exactly) + dense (catches paraphrases like "widget replacement error"), fuses with RRF, reranks to top-8, generates with citations. Agentic RAG does all of the above, but if the grader says "irrelevant," it rewrites the query (e.g., adds "procedure section"), retrieves again, and only generates when evidence is sufficient -- or returns "insufficient evidence" after 3 hops.

**CoALA framework**: RAG over Wikipedia is **read-only semantic memory of the world**; agent memory is **writable semantic + episodic memory of the interaction** (Sumers et al.). Graphiti/Zep graphs are **memory**. Microsoft GraphRAG community reports over a **corpus** are RAG. Mixing them in one Pinecone namespace without a type tag is how a ticket preference retrieves a Wikipedia cluster. **Do not fuse them.**

### 1.3 Why Agentic RAG Matters (Cost and Quality)

- **Indiscriminate retrieval is actively harmful.** Research on the Atlas model found that retrieved context can override correct parametric knowledge -- the model relies on retrieved text even when its own knowledge was right.
- **Running every query through agentic RAG wastes ~10x cost** and adds 5s latency on queries that need neither.
- **A query complexity classifier at the front door reduces costs by ~40% and latency by ~35%** compared to routing every query through the full agentic pipeline.
- Adaptive-RAG measured step counts: always-C is **2.81 steps / 3.33x time** vs Adaptive **1.03 steps / 1.46x**.

---

## 2. Core Concepts

### 2.1 Query Rewriting Techniques

**HyDE (Hypothetical Document Embeddings)** (Gao, Ma, Lin, Callan, ACL 2023)

The core problem: short user queries (5-15 tokens) produce embeddings that are semantically distant from longer document chunks (200-500 tokens). HyDE bridges this gap by generating a hypothetical answer document (50-200 tokens) and embedding that instead.

Mechanism: User query -> LLM generates a plausible (possibly factually incorrect) answer document -> embed the hypothetical document -> use that embedding for vector search. The hypothetical document captures relevant vocabulary, phrasing, and thematic content that align with how real documents are written.

Cost: **one extra generate + one extra embed** per hop (~200-500ms latency, ~$0.001-0.003 cost).

**When NOT to use HyDE:**
- SKU / error-code / statute lookups -- BM25 already wins; the hypothetical contaminates the dense neighborhood.
- Citation-sensitive queries (legal, deposition transcripts) -- hypotheticals introduce non-record text into the dense neighborhood.
- Unlike contextual retrieval (which adds context at ingestion time, once), HyDE adds latency **per query**.

GraphRAG **DRIFT** uses HyDE as a **primer** over community reports -- a router feature, not a replacement for hybrid.

**Step-Back Prompting** (Zheng et al., ICLR 2024)

Generates a broader, more abstract version of the query to retrieve high-level context. Example: "What is the refresh rate of iPhone 13 Pro Max?" becomes "What are the technical specifications of iPhone 13 Pro Max?" The broader query retrieves the spec sheet, which contains the specific answer.

**Important**: Fuse with the original query -- step-back-only drops the identifier.

**Multi-Query / RAG-Fusion**

LLM emits n paraphrases (commonly 3-5); retrieve each; fuse with RRF. Cap n=3 unless eval shows otherwise. Sub-question decomposition is sequential (closer to IRCoT). Example: "Compare the regulatory requirements for AI in healthcare between the EU and US" -> Sub-Q1: "EU AI Act requirements for healthcare AI" + Sub-Q2: "US FDA regulations for AI medical devices" + Sub-Q3: "Comparison framework for AI regulation across jurisdictions."

**Decision framework**: Do not add a transformation step until you can name the retrieval failure mode it fixes and the latency cost you accept.

| Technique | Extra LLM calls | Latency cost | Best for |
| --- | --- | --- | --- |
| Query rewriting | 0 (CPU) | ~50ms | Typo correction, term standardization |
| Step-back | 1 | ~200-500ms | Overly specific queries missing broader context |
| Multi-query | 3-5 parallel | ~200-500ms | Ambiguous/multi-faceted questions |
| HyDE | 1 generate + 1 embed | ~200-500ms | Broad open-domain where semantic gap is widest |

### 2.2 Hybrid Search: RRF vs RSF vs Alpha

**Why hybrid**: BM25 (sparse) excels at exact-match queries (product codes "ERR-502-BAD-GATEWAY", entity names, regulation numbers "SEC Rule 10b-5"). Dense vector retrieval handles paraphrase and conceptual similarity but underweights rare exact terms. Neither alone is sufficient for enterprise workloads.

**Score incompatibility problem**: BM25 produces unbounded positive integers (a score of 15.7 means nothing in absolute terms). Cosine similarity is bounded in [-1, 1]. Normalizing both to [0, 1] is unreliable because score distributions have completely different shapes across queries and corpora. **This is why RRF exists.**

**RRF (Reciprocal Rank Fusion)** (Cormack, Clarke, Buettcher, SIGIR 2009). Rank-only, scale-free:

```
RRF_score(d) = SUM over all rankers r: 1 / (k + rank_r(d))
```

k=60 was fixed in a TREC pilot and not altered in validation; near-optimal but not critical. Rank 1 contributes 1/61 ~ 0.0164; rank 60 contributes 1/120 = 0.0083. Documents in **both** lists outrank a document that wins only one. After each retriever returns top-k: O(k * |R|) to accumulate, then sort the union -- dominated by ANN + inverted-index latency, not the fuse.

**Tuning k**: k=60 is the original paper default for web search. For short corpora (<100 docs), use k=10-30 to amplify rank differences. For noisy corpora with many irrelevant candidates, larger k smooths results. Treat as a hyperparameter tuned on held-out queries.

**Why ranks over scores**: As the corpus grows, BM25 distributions drift with DF; vector scores jump when the embedder changes; **ranks stay comparable**.

**WANDS e-commerce benchmark results**:
- BM25-only: NDCG 0.6983
- Dense-only: NDCG 0.6953
- Tuned hybrid (BM25 + dense + RRF): NDCG **0.7497 (+7.4%)**
- Hybrid + Cohere Rerank: Recall@5 **0.816 (+17.4%** over hybrid RRF alone, **+39.0%** over dense-only)

**Score fusion (when magnitudes are trusted):**

| Method | Who | Mechanism | When it wins |
| --- | --- | --- | --- |
| **Relative Score Fusion (RSF)** | Weaviate default since v1.24 | Min-max each list to [0,1], then alpha-weighted sum | Score gaps carry signal |
| **Alpha convex combo** | Pinecone single-index; Weaviate `alpha` | alpha * dense + (1 - alpha) * sparse | Same index, same query; A/B alpha |
| **DBSF** | Qdrant | Mean/std of the prefetch top-k (3-sigma remap) | Calibrated retrievers; outlier-sensitive |
| **min_max + arithmetic_mean** | OpenSearch `normalization-processor` (2.10+) | Score-space mix via search pipeline | Explicit 0.3/0.7 weights |

**Vendor traps (critical for interviews):**

- **Weaviate.** `alpha`: 0 = keyword, 1 = vector, **server default 0.75** if unset. gRPC `alpha=0` is Go's zero value -> **pure BM25**. **Always set `alpha` explicitly.** `fusionType`: `relativeScoreFusion` (default >= v1.24) vs `rankedFusion`.
- **Pinecone.** Single-index dense+sparse requires `metric=dotproduct` and `hybrid_score_norm` or sparse **dominates**. Starting alpha (vendor walkthrough): 0.75 NL docs, 0.5 mixed, 0.25 SKU/ID-heavy. Serverless hybrid may **preselect by dense** then re-rank with sparse inside that k -- changing alpha then appears to do nothing. `pinecone-sparse-english-v0` requires `input_type` `query` vs `passage`.
- **Elasticsearch.** `rrf` wraps >= 2 children. Nest `text_similarity_reranker` **outside** `rrf`. `rank_window_size` default **10** (must be >= `size` -- raise to 50-100 or the reranker never sees the right doc).
- **OpenSearch.** `hybrid` query + **search pipeline** (not in-query fusion). Max **5** subqueries. Cannot nest under `function_score`. `pagination_depth` changes the fused set.
- **Qdrant (>= 1.10).** `prefetch[]` then top-level `FusionQuery`. Fusion **inside** prefetch = per-shard (wrong for multi-shard hybrid).
- **Vertex RAG Engine.** `hybrid_search.alpha` default **0.5**, unlike Weaviate 0.75 -- another unset-default trap.
- **Bedrock KB.** `HYBRID` needs a filterable text field else **falls back to semantic**. Guardrails cover query and answer, not retrieved source text.
- **Azure AI Search.** BM25 + HNSW -> RRF -> Semantic Ranker over the hybrid top **50**. Agentic retrieve does **not** apply classic scoring profiles. Pass Entra in `x-ms-query-source-authorization`.
- **pgvector.** CTE dense + CTE `tsvector`/`websearch_to_tsquery` or ParadeDB BM25, `FULL OUTER JOIN`, `1/(k+rank)`. `ts_rank` is **not BM25** -- true BM25 is ParadeDB `pg_search`.

### 2.3 Two-Stage Retrieve / Rerank

**Bi-encoder vs cross-encoder.** Bi-encoder: encode query once, docs offline, ANN. O(1) query encode + ANN. Stage-1 recall, k=50-200. Cross-encoder: jointly attend over (query, document) -- **one forward pass per candidate**. Stage-2 precision, keep 3-20 for the generator.

Quality lift from reranking: **+5 to +15 NDCG@10 points** across MTEB/BEIR benchmarks. One case study: accuracy jumped from 73% to 91% with cross-encoder reranking, but added 300ms latency.

**Production default pipeline:**

```
query -> [authz filter]
      -> dense ANN (k=50-100) || BM25/sparse (k=50-100)
      -> RRF / RSF / alpha  -> fused N ~ 50-150
      -> cross-encoder / Cohere / Voyage / ColBERT top_n=5-20
      -> generator (citation IDs must be a subset of this set)
```

**Reranker options:**

| Reranker | Score range | Relevant pairs | Latency | Cost | Best for |
| --- | --- | --- | --- | --- | --- |
| **Cohere Rerank v3.5** | [0, 1] | 0.85-0.95 | P50 ~220ms | $2/1K searches | Teams without GPU infra |
| **BGE-v2-m3** (self-hosted) | [0, 1] (sigmoid) | 0.55-0.75 | Depends on GPU | Self-hosted | Full control, data residency |
| **ColBERT** (late interaction) | Unbounded (MaxSim) | 4-18 (varies) | Tens of ms on 100 docs | Self-hosted | Large k (>200), tight latency |

**Score calibration warning**: A "keep if score > 0.7" gate copied from a Cohere notebook onto ColBERT either drops everything or keeps everything. Calibrate on **your** labeled set; log the reranker **model id** next to the score.

**Common pitfall**: Reranking too few candidates. Retrieving top-5 and reranking to top-3 barely shuffles the deck. Use N=30-50 minimum input to the reranker.

**LLM-as-reranker**: Pointwise / pairwise (O(n^2) unless tournament) / listwise. A 70B judge over 50 chunks **dwarfs** a cross-encoder. Use a cheap model for agentic **binary** `grade_documents`, not as the primary 100-way ranker.

**Cohere limits**: `num_documents * max_chunks_per_doc <= 10,000`; recommend <= 1,000 docs/request. Search unit: 1 query + up to 100 documents; if query+doc > 500 tokens, auto-split. **80 fused 800-token chunks inflate billed units.**

**Voyage limits**: (q_tok * n_docs) + sum(d_i); caps query <= 8k tok, query+any doc <= 32k, <= 1,000 docs, total <= 600k.

### 2.4 Parent-Document / Hierarchical Chunking

**Small-to-big retrieval**: Embed small "child" chunks (50-200 tokens, ~400 characters) for precise matching, but replace them with larger "parent" chunks (500-1500 tokens, ~2000 characters) before passing to the LLM. This decouples two competing requirements: retrieval granularity (small = clean, focused embeddings) vs. generation granularity (large = sufficient reasoning context).

**Why small chunks embed better**: Embeddings degrade on long, topic-mixed text. A 2000-character chunk spanning two topics produces a blurred embedding that matches neither topic well. A 400-character child chunk is topically focused and produces a precise embedding.

**Why the LLM needs larger chunks**: Small chunks alone give the LLM tunnel vision -- it sees a sentence about a policy clause but misses the surrounding context needed to interpret it correctly.

**Two variants**:
1. **Parent-child chunk retrieval**: Fetch child chunks by vector similarity, follow parent IDs, deduplicate by parent, return parent chunks. LangChain `ParentDocumentRetriever` (deprecated in v0.2.0, Feb 2024) extended `MultiVectorRetriever`: children go to the vectorstore with `id_key` in metadata; parents go to a `docstore`.
2. **Sentence window retrieval**: Fetch a single sentence, expand +/- N (typical 1-3). LlamaIndex `AutoMergingRetriever` merges siblings until the merged node would exceed the generator budget.

**Production invariants for parent/child:**

1. **ACL copy**: Stamp `acl` / `tenant` / `version` / `char_span` on **child and parent**. Generate-time expansion that loads a parent the user cannot see is an entitlement bug. If the parent is the whole contract and the child is a public exhibit, **do not** use whole-doc parents.
2. **Atomic alias**: Rebuild swaps vectorstore + docstore under one `index_version`. Orphan children (`mget` -> `None`) are dropped silently -- you will retrieve "successfully" with fewer parents than children. Monitor `mget` miss rate.
3. **Token cap**: Cap parent tokens (1,200-2,000) or fall back to child + heading path. Lost-in-the-middle applies inside a too-large parent.
4. **Cite the child**: Citations should point at the child span that matched, then optionally display parent context. Citing the parent ID while quoting a child from a different version is provenance fraud.

**Contextual Retrieval (Anthropic, Sep 2024)**

Prepends 50-100 tokens of **chunk-specific** context (not a generic doc summary) before embedding **and** before BM25. An LLM generates the context using the full document as reference, capturing document subject, section information, entities, and dates that were separated from the chunk during splitting.

Key advantage over HyDE: Contextualization happens **once at ingestion**, not per query. No added query-time latency.

One-time cost (official): **$1.02 per million document tokens** (assuming 800-token chunks, 8k-token documents, 50-token instructions, 100 tokens of context per chunk via prompt caching).

Results (their mix, 1 - recall@20):
- Baseline fail **5.7%** -> contextual embeddings **3.7%** (-35%)
- + contextual BM25 **2.9%** (-49%)
- + Cohere rerank 150->20 **1.9%** (-67%)

**Recommendation**: Always pair contextual embeddings with contextual BM25. Add reranking if latency tolerance allows. KB < ~200k tokens (~500 pages) -> skip RAG, cache the corpus. Use 20 chunks for best retrieval results.

**Practical starting point**: 400-800 tokens, 10-20% overlap, sentence snap, `doc_id` / `section` / `acl` / `version` / `parent_id` / `char_span` on every child.

### 2.5 Adaptive Retrieval (Adaptive-RAG)

**Adaptive-RAG** (Jeong et al., NAACL 2024): A T5-Large classifier routes queries by complexity:

- **Level A** (simple): Use parametric knowledge, no retrieval. "What is the capital of France?"
- **Level B** (moderate): Single-step hybrid RAG. "What were our Q3 revenue numbers?"
- **Level C** (complex): Multi-step iterative RAG. "How did our pricing strategy changes in 2025 affect churn rates across enterprise vs SMB segments?"

**Benchmark results** (averaged over NQ/SQuAD/TriviaQA + MuSiQue/HotpotQA/2Wiki, GPT-3.5-Turbo-Instruct):

| Strategy | F1 | Accuracy | Avg Steps | Time multiplier |
| --- | --- | --- | --- | --- |
| Single-step | 46.99 | -- | 1.00 | 1.00x |
| **Adaptive-RAG** | **50.91** | **48.97** | **1.03** | **1.46x** |
| Always multi-step | 50.87 | -- | 2.81 | 3.33x |
| Oracle classifier | 62.80 | -- | -- | -- |

**Key insight**: Adaptive-RAG gets nearly the same F1 as always-multi-step (50.91 vs 50.87) at **half the steps** and **less than half the time**. But the oracle classifier at 62.80 shows the gap left on the table.

**Production confusion**: ~47% of true-A predicted as B; ~31% of true-C as B -- production clothing is a cheap classifier plus hop cap, not a trained T5 you skip measuring.

**Production mapping**: Chitchat -> no retrieve; factoid -> hybrid+rerank; multi-hop -> agent 2-3 hops; global themes -> LazyGraphRAG.

### 2.6 Self-RAG: Self-Reflection Tokens

**Self-RAG** (Asai et al., ICLR 2024): Trains a single LM to adaptively retrieve passages on-demand and generate/reflect using special reflection tokens.

**Four reflection tokens:**

| Token | Input | Values | Role |
| --- | --- | --- | --- |
| `Retrieve` | x / x,y | yes / no / continue | Whether to call retriever |
| `ISREL` | x, d | relevant / irrelevant | Passage useful for x |
| `ISSUP` | x, d, y | fully / partially / no support | Attribution |
| `ISUSE` | x, y | 5...1 | Utility |

Default inference weights: ISREL **1.0**, ISSUP **1.0**, ISUSE **0.5**; retrieval threshold 0.2; beam width 2; Contriever top 5.

**Training pipeline**: A critic model (trained on GPT-4 annotations) inserts reflection tokens offline into training data. The generator is then trained with standard next-token prediction on this augmented corpus. **No critic model needed at inference** -- the reflection tokens are generated as part of normal autoregressive decoding.

**Results**:

| Benchmark | Self-RAG 7B | Always-retrieve Llama2-7B | Self-RAG 13B | ChatGPT |
| --- | --- | --- | --- | --- |
| PopQA | 54.9 | 38.2 | 55.8 | -- |
| PubHealth | -- | -- | 74.5 | -- |
| ASQA citation precision | 66.9 | 2.9 | -- | -- |
| ASQA citation recall | 67.8 | 4.0 | -- | -- |

Raising ISSUP weight lifts citation precision and **hurts MAUVE** (fluency). Production teams almost always **prompt** a separate grader rather than train tokens. LangGraph's official tutorial is the production approximation of Self-RAG + CRAG **without** training reflection tokens.

### 2.7 Corrective RAG (CRAG)

**CRAG** (Yan et al., 2024): Adds a lightweight retrieval evaluator (T5-based) that scores retrieved document quality and triggers corrective actions.

**Three action paths:**
- **Correct** (high confidence): Refine retrieved documents using decompose-then-recompose -- split into sentences, keep only answer-relevant pieces, reassemble.
- **Incorrect** (low confidence): Discard faulty retrievals entirely. Trigger web search fallback.
- **Ambiguous** (uncertain): Combine refined internal retrieval with web search results.

**Results** (SelfRAG-LLaMA2-7b on PopQA): RAG **40.3** -> CRAG **59.3** -> Self-RAG **54.9** -> Self-CRAG **61.8**. T5 evaluator **beat ChatGPT** at judging retrieval quality in their Table 4.

**Enterprise invariant**: Incorrect -> web is an **exfil path**. Bound fallback to an **approved** corpus, not the open internet, on confidential queries. A CRAG web fallback that **writes the public internet into the corpus** is a data contamination incident.

### 2.8 Multi-Hop Reasoning: IRCoT, FLARE, CoRAG

**IRCoT (Interleaving Retrieval with Chain-of-Thought)** (Trivedi et al., ACL 2023): What to retrieve at step n depends on step n-1: retrieve -> generate next CoT **sentence** -> that sentence is the next query. Loop runs up to 8 steps, capping at 15 paragraphs. No training required -- entirely prompting-based.

**Results**:
- HotpotQA (GPT-3): +11.3 retrieval recall points and +7.1 QA F1 points over one-step retrieval.
- 2WikiMultihopQA: +22.6 recall and +13.2 F1.
- Flan-T5-XL (3B) with IRCoT outperforms GPT-3 (175B) with one-step retrieval.
- Reduces factual errors in CoT by 50% on HotpotQA.

**FLARE (Forward-Looking Active Retrieval)** (Jiang et al., EMNLP 2023): Generates the next sentence, checks token probabilities. If any token falls below confidence threshold theta, triggers retrieval using the generated sentence as query, then regenerates. Achieves 51.0 EM on 2WikiMultihopQA vs 39.4 for single-retrieval.

**Limitation**: Depends on model calibration. Instruction-tuned and RLHF models are often overconfident, making confidence thresholds unreliable. This limits FLARE's practical applicability with modern chat models.

**CoRAG (Chain-of-Retrieval Augmented Generation)** (arXiv:2501.14342): Uses rejection sampling to augment RAG datasets with intermediate retrieval chains, then fine-tunes open-source LMs with standard next-token prediction. Supports greedy decoding, best-of-N sampling, and tree search at inference.

**HippoRAG** (NeurIPS 2024): Single-step Personalized PageRank **10-20x cheaper, 6-13x faster** than iterative retrieve **in HippoRAG's experiments**. A strong alternative to IRCoT for multi-hop when cost matters.

**Common challenge across all multi-hop methods**: As retrieval iterations increase, generated queries can drift from the correct reasoning path, and irrelevant information accumulates. Latency scales linearly with reasoning depth.

**Loop bound invariant**: Official LangGraph can loop until runtime timeout. Production: `retry_count`, wall-clock, terminal `insufficient_evidence`. Persist `retry_count` in graph state (checkpointer) so a replay does not reset the cap. Store `original_question` and `search_query` as separate keys -- the tutorial rewrite replaces the user message and grade/generate then read the **rewritten** question. HyDE + rewrite stacked without a cap is how you spend four generates before the first cited answer.

---

## 3. Architecture & System Design

### 3.1 Full Agentic RAG Topology

The system is organized into independently scaled planes that must not be coupled:

```
+----------------------------------------------------------------------------------+
| CLIENTS                                                                          |
|  chat query | connector ingest | GDPR / legal-hold | canary CODE-CANARY-9       |
+----------+-----------------------------------------------------------------------+
           | TLS + session JWT (tenant/acl FROM TOKEN) + correlation-id
           v
+----------------------------------------------------------------------------------+
| CONTROL PLANE  (RAG orchestrator -- your process / Agent Server, not the GPU)    |
|                                                                                  |
|  +------------+  +------------+  +------------+  +------------+  +-----------+   |
|  | Edge       |->| Policy     |->| ADAPTIVE   |->| LOOP       |->| ALIAS PIN |   |
|  | auth, SSO  |  | PII redact |  | ROUTER     |  | retrieve ->|  | query     |   |
|  | tenant     |  | BEFORE     |  | chitchat | |  | grade ->   |  | reads     |   |
|  | from token |  | embed      |  | factoid |  |  | rewrite -> |  | index_    |   |
|  | MCP aud.   |  | Vec2Text   |  | multi-hop ||  | generate   |  | alias=N   |   |
|  |            |  |            |  | global     |  | hop cap=3  |  | ingest    |   |
|  |            |  |            |  |            |  | wall-clock |  | writes    |   |
|  |            |  |            |  |            |  |            |  | N+1       |   |
|  +------------+  +------+-----+  +------+-----+  +------+-----+  +------+----+   |
|                         |              |              |              |            |
|  +---------------------------------------------------------------------------+   |
|  | TWO-PLANE ORCHESTRATOR                                                    |   |
|  |  QUERY: PEP filter -> dense||BM25 -> RRF/RSF/alpha -> rerank             |   |
|  |         tool -> grade -> rewrite (cap) | generate+cite                    |   |
|  |  INGEST: watermark -> parse -> DLP -> ACL stamp -> chunk                  |   |
|  |          parent/child -> embed+sparse -> upsert N+1                       |   |
|  |          shadow nDCG -> alias flip                                        |   |
|  |  original_question != search_query (keep both in state)                   |   |
|  +----------------------------------+----------------------------------------+   |
|                                      |                                           |
|  +------------+  +------------+      |       +------------------+                |
|  | Circuit    |  | Fallback   |<-----+------>| SIGTERM / drain  |                |
|  | search !=  |  | hybrid ->  |              | finish hop;      |                |
|  | rerank !=  |  | BM25-only  |              | do not half-     |                |
|  | generate   |  | -> refuse  |              | flip the alias   |                |
|  | != ingest  |  | (no ungrd) |              |                  |                |
|  +------------+  +------------+              +------------------+                |
+----------------------------------------------------------------------------------+
           |                                          |
           | chat / agent SSE, REST                   | ingest / re-embed (async)
           v                                          v
+----------------------------------+  +--------------------------------------------+
| DATA PLANE  QUERY / GENERATE     |  | DATA PLANE  INGEST (Temporal/Kafka)        |
| (provider-owned on hosted APIs)  |  | model NEVER holds IAM or writes the alias  |
|                                  |  |                                            |
|  Tokenizer -> Prefill -> Decode  |  |  parse / chunker_version / sha256          |
|  prompt = instr + top_n chunks   |  |  DLP BEFORE embed AND contextualize        |
|  citation IDs subset of R        |  |  ACL copy to child AND parent              |
|  stop: end_turn / max_tokens /   |  |  dense embed + sparse encode SAME text     |
|    insufficient_evidence         |  |  parent docstore version = vectorstore     |
+----------------------------------+  +--------------------------------------------+
           |                                          |
           v                                          v
+----------------------------------+  +--------------------------------------------+
| TOOL PROXIES  (MCP retrieve)     |  | PERSISTENCE  (five indexes + docstore)     |
| Zero-Trust wrap; RFC 8707 aud.   |  |                                            |
| tenant NEVER from tool args      |  |  +-------------+  +-------------+          |
|  +----------+  +-------------+   |  |  | DENSE ANN   |  | SPARSE      |          |
|  | retrieve |  | rerank_api  |   |  |  | HNSW/IVF    |  | BM25/SPLADE |          |
|  | _kb /_hr |  | (own RPM,   |---+--+  | + ACL       |  | tsvector != |          |
|  | no omni  |  |  timeout,   |   |  |  | bitmap      |  | BM25        |          |
|  | search() |  |  cache)     |   |  |  +-------------+  +-------------+          |
|  +----------+  +-------------+   |  |  +-------------+  +-------------+          |
|  graph_local vs graph_global     |  |  | PARENT      |  | RERANK      |          |
|  CRAG web = allowlist domains    |  |  | docstore    |  | cache (q,   |          |
|  observation != corpus write     |  |  | + alias     |  |  doc, model)|          |
|                                  |  |  +-------------+  +-------------+          |
+----------------------------------+  |  graph (entities/reports) optional         |
                                      +--------------------------------------------+
                                                          |
+----------------------------------------------------------------------------------+
| TELEMETRY / OBSERVABILITY SINKS                                                  |
|  +--------------+  +--------------+  +--------------+  +----------------------+  |
|  | Audit (WORM) |  | Metrics      |  | Traces       |  | Usage (authoritative |  |
|  | cid, tenant, |  | retrieve     |  | gateway->PEP |  | on terminal event)   |  |
|  | R ids, spans,|  | p50/p95/p99, |  | ->ANN||BM25->|  | embed tok, rerank    |  |
|  | index_build, |  | hop depth,   |  | RRF->rerank->|  | units, generate tok, |  |
|  | cite-not-R   |  | nDCG, alias  |  | grade; PII   |  | hops, total_cost_usd |  |
|  | rate         |  | lag, breaker |  | stripped     |  |                      |  |
|  +--------------+  +--------------+  +--------------+  +----------------------+  |
+----------------------------------------------------------------------------------+
```

**Planes (do not couple):**

| Plane | Owns | Failure if coupled |
| --- | --- | --- |
| **Control** | Adaptive router, hop cap, alias pin, PEP, retrieve-tool RBAC | Unbounded rewrite; web exfil; model-invented `tenant_id` |
| **Query (read)** | Authz filter, hybrid, fuse, rerank tool, grade, generate, cite | Ingest schema change silently mismatches query embeddings |
| **Ingest (write)** | Parse, DLP, ACL stamp, chunk, embed, sparse, parent IDs, checkpoint | Query p99 tracks reindex; a stuck extractor stalls answers |
| **Rerank tool** | Joint encode (query, doc); own RPM/timeout/cache | 80 joint encodes inside generator prefill; Cohere 1k RPM ceiling as chat SLO |
| **Tool proxies** | Audience-bound MCP `retrieve_*` vs `rerank_api` vs CRAG-web | Omnibus `search(query, collection)`; token passthrough |
| **Persistence** | Dense, sparse, ACL bitmap, parent docstore, rerank cache, graph snapshot | Orphan children; alias half-flip; replica ONE cites deleted docs |
| **Telemetry** | Hop histograms, citation fidelity, search_units, alias lag | Finance dashboards that ignore rewrite hops |

**Ingest vs query (do not fuse).**

| Plane | Write path | Read path | Failure if fused |
| --- | --- | --- | --- |
| Ingest | Watermark -> blob+sha256 -> chunk -> embed+sparse -> upsert `index_version=N+1` -> shadow eval -> **alias flip** | -- | A CRAG web fallback that writes the public internet into the corpus |
| Query | -- | PEP -> hybrid -> fuse -> rerank -> loop -> cite R only | Query p99 tracks HNSW rebuild; dual-read RRF across incompatible embed spaces |

### 3.2 End-to-End Request Flow

**Query path (user-facing):**

1. **Ingress (5ms).** Gateway stamps `correlation_id`. Bind `tenant_id` / ACL groups from the **verified token**, never from tool JSON the model invented.

2. **Policy (1-5ms).** Detect -> redact PII **before** the query is embedded, reranked, or traced. Tool RBAC maps `(principal, tenant, tool, collection)` -> allow/deny. Consult the **search** breaker independently of the **generate** breaker. Search timeout: **200-500 ms [policy, not vendor SLO]**.

3. **Adaptive router (5-50ms CPU, 0-500ms if LLM rewrite).** Chitchat -> **no retrieve** (class A, ~5% of queries). Factoid/SKU -> single-hop hybrid+rerank (class B, ~65%). Multi-hop "compare X vs Y" -> agent 2-3 hops (class C, ~30%). Global themes -> `graph_global` / LazyGraphRAG -- not vector top-k.

4. **PEP on every arm.** ACL predicate is a **mandatory filter** on dense, BM25, parent `mget`, and graph-report lookup. Soft recency decay **after** ACL.

5. **Hybrid retrieve (10-50ms).** Dense ANN k=50-100 **parallel** BM25/sparse k=50-100. Fuse with RRF (default when magnitudes are untrusted). Fused N ~ 50-150. Metadata filters (date, type, ACL) applied **during** search, not post-hoc.

6. **Rerank tool (100-300ms).** Cross-encoder / Cohere / Voyage / ColBERT -> `top_n=5-20`. Timeout -> drop to fused top-8; do **not** block generate on a 1k-RPM Cohere ceiling. Cache key **includes ACL + index_version**.

7. **Grade (50-200ms).** Binary `relevant` / `irrelevant` (LangGraph `GradeDocuments`; Self-RAG `ISREL` as a **prompted** grader in production). Grader "no" is a **successful** node -- it is NOT `RetryPolicy`.

8. **Rewrite or generate.** Irrelevant -> rewrite (`original_question` stays on another state key; `search_query` is what you retrieve). Persist `retry_count` in the checkpointer so a replay does not reset the cap. `retry_count >= MAX_ATTEMPTS` (common **3**) or wall-clock -> terminal `insufficient_evidence`. **Do not** fall back to parametric knowledge on ACL-sensitive corpora. Relevant -> generate with citation IDs subset of R this hop. Place top evidence at **prompt edges** (lost-in-the-middle).

9. **Parent expansion (optional, 1-5ms).** `mget` parents by `parent_id`; drop `None`; **re-apply ACL**; cap parent tokens (1,200-2,000) or fall back to child + heading path. Cite the **child span** that matched.

10. **Citation verification (50-200ms, for agentic path).** NLI model checks each (claim, cited_chunk) pair for entailment. Non-entailed citations flagged. If any claim unsupported, regenerate or return "insufficient evidence."

11. **Output guardrails (5-20ms).** PII regurgitation scan, toxicity filter, confidence threshold check.

12. **Halt + WORM.** cid, tenant, hashed user, R, rerank scores, spans, `index_build_id`, hop depth, breaker state, model ids. Hash-verify chunk body vs ingest sha256.

**End-to-end latency**: Category B queries: 300-800ms. Category C (agentic): 2-30s depending on hop count and retrieval retries.

**Ingest path (async, independently scaled):**

13. **Watermark.** S3 etag / Drive revision / CDC LSN / SharePoint ACL version. Raw blob + sha256 (poisoning detection). Unknown source -> **quarantine**, not the live alias.

14. **Parse / DLP / ACL stamp.** Redact **before** embed **and** before Contextual Retrieval prepend. Stamp `acl` / `tenant` / `version` / `char_span` on **child and parent**. Microsoft Azure AI Foundry (July 2026) introduced boundary-aware chunking API with automatic deduplication -- early adopters report 40% fewer retrieval artifacts vs LangChain RecursiveCharacterTextSplitter.

15. **Chunk.** `chunk_id = hash(doc_id, chunker_version, text)`. Sparse encode the **same** text the BM25 index will see.

16. **Upsert N+1.** New collection / namespace / ES index. Parent docstore version **must** match. Dual-write during embedder migration; **do not** dual-read RRF across different dims/metrics.

17. **Shadow eval** nDCG@k **and** citation-NLI on a frozen golden set (SKU/ID queries **and** paraphrases). **Then** flip `index_alias -> N+1`. Keep N readable until the error budget is green.

18. **Canary.** Scheduled query "the canary runbook says CODE-CANARY-9" must retrieve the current body within the SLO. Hit N after flip -> alias did not move. Stale text -> CDC behind -- **fail closed** on policy documents.

---

## 4. Key Algorithms & Mechanics

### 4.1 RRF Fusion -- Detailed

```python
def rrf_fuse(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Cormack SIGIR 2009. Rank-only; k=60. Docs in both lists outrank a single-list winner."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
```

### 4.2 ColBERT Late Interaction

ColBERT encodes queries and documents separately into per-token embeddings, then scores with MaxSim:

```
Score(Q, D) = SUM over each query token q_i: MAX over all doc tokens d_j: cos(q_i, d_j)
```

More expressive than single-vector cosine, far cheaper than full cross-encoder because document representations are precomputed and cached. MaxSim scores are **unbounded** (a relevant pair might score 4 or 18) -- a completely different numeric space from Cohere or BGE. PLAID: tens of ms GPU at 140M passages.

### 4.3 GraphRAG, RAPTOR, and Knowledge Graphs

**GraphRAG** (Microsoft Research, April 2024): Instead of indexing raw document chunks, extracts entities and relationships to build a knowledge graph, then uses graph structure for retrieval.

Three components: (1) **Graph construction** -- LLM extracts entity-relationship triples from text; (2) **Community detection** -- hierarchical clustering via Leiden algorithm; (3) **Community summarization** -- natural language summaries per community.

Two query modes:
- **Local Query**: Specific factual questions -- extract entities, find in graph, traverse 1-2 hop neighbors, collect context.
- **Global Query**: Broad synthesis -- use community summaries for holistic answers.

**Why graphs help multi-hop**: With flat document chunks, retrieving all pieces of a 3-hop reasoning chain (A->B->C) in top-k is statistically unlikely. A graph lets you start from a matched entity, traverse to related entities, and pull context from each step.

**Production caveats**:
- Entity extraction quality must be above 85%; below that, the graph introduces noise.
- The project is now largely in **maintenance mode** (2026). Microsoft CLI: `graphrag init --force` between minor versions; 1.0 indexes are **not** backward compatible.
- Do NOT use GraphRAG global for "what's the refund SLA?" -- that is a factoid (class B).
- Community reports can **summarize secrets** into a high-level node that global search retrieves for everyone with graph access -- ACL on **reports**, not just raw chunks.

**RAPTOR (Recursive Abstractive Processing for Tree-Organized Retrieval)** (Sarthi et al., ICLR 2024): Recursively embeds, clusters, and summarizes text chunks, building a tree with differing levels of abstraction from bottom up. Text chunks are embedded with SBERT, clustered using soft clustering, and each cluster gets an LLM-generated summary as a parent node. On QuALITY benchmark, RAPTOR + GPT-4 improves best performance by **20% absolute accuracy**. Tree construction cost scales linearly with document length.

### 4.4 Citation Tracking: ID Constraint, Not a Footnote Style

**The scale of the problem**: Over 95% of answers from tested open-source LLMs contain at least one sentence without any attribution. 57% of citations in a RAG-optimized model show unfaithful behavior (Wallat et al.). Citation-shaped hallucinations are more dangerous than plain hallucinations because users lower their guard.

**Three species of hallucinated citation:**

1. **ID not in retrieved set R** -- `[doc 17]`, a URL, a statute the retriever never returned.
2. **Retrieved but non-entailing** -- right entity, claim not in the chunk (ALCE precision miss).
3. **Post-hoc rationalization** -- the model decided the answer, then attached a nearby chunk.

**Liu, Zhang, Liang (EMNLP Findings 2023)** on Bing Chat / NeevaAI / perplexity.ai / YouChat: **51.5%** of generated sentences fully supported (citation **recall**); **74.5%** of citations support their sentence (citation **precision**). Precision **inversely** correlated with perceived utility (r = -0.96). This means the more useful an answer looks, the less likely its citations actually support it -- a facade of trustworthiness.

**Gao et al. ALCE (EMNLP 2023)**: A claim is supported when cited passages jointly **entail** it (NLI). On ELI5, even the best models lack complete citation support ~50% of the time.

**Key techniques:**

1. **Inline citation generation**: Citations generated during answer synthesis (not post-hoc). Post-hoc citation attachment is unreliable because the model has already committed to claims without evidence grounding.

2. **Quote-then-answer**: Extract relevant quotes from context first, then synthesize answer using only those quotes. Commits the model to evidence before generating claims.

3. **NLI-based verification**: A Natural Language Inference model checks whether each cited claim is entailed by its source. Classifies (premise=cited chunk, hypothesis=claim) as entailment/contradiction/neutral.

4. **Token-level attribution (UAF)**: Aggregates token-level provenance scores into grounding confidence per claim. Cuts fabricated claims by ~42% vs baseline RAG.

5. **FACTUM**: Detects citation hallucination by analyzing model internal activation pathways. State-of-the-art for mechanistic citation verification but requires model-internal access.

**Production benchmark**: Even with perfect retrieval, GPT-4-class models fabricate details in **8-15%** of responses. Citation enforcement (quote-then-answer + NLI verification) reduces this to **under 3%**.

**Production control -- citation as an ID allowlist.** At the citation site, only emit IDs in R **this hop** (constrained decode / tool-only citations). Stronger: store `char_span` at ingest; emit a 3-30 token span; UI highlights it. Strongest: prefix-tree decode so tokens exist as a contiguous span in the retrieved set.

**Provenance fidelity** = fraction of cited IDs that (a) were in R this hop, (b) NLI-support the claim, (c) the caller was entitled to see. Target for (a)+(c): **0 violations** in canaries.

**Lost-in-the-middle** (Liu et al., TACL / arXiv 2307.03172). U-shaped performance; GPT-3.5-Turbo with the gold passage **in the middle** of a multi-doc prompt scored **below closed-book 56.1%**. Operational: rerank so the best evidence is not buried; place top chunks at edges; do not stuff 50 pre-rerank hits into 128k.

### 4.5 Complexity Analysis

Let k be per-arm retrieve width, N the fused set, n the post-rerank set, H the hop cap.

- **Hybrid retrieve:** ANN + inverted index dominate; RRF fuse is O(k * |retrievers|) then sort.
- **Rerank:** Theta(N) joint encodes for a cross-encoder (plus network). ColBERT MaxSim is cheaper at query time (docs encoded offline).
- **Parent expand:** O(P) `mget`; cap tokens before generate, not after OOM.
- **Agent loop:** Theta(H * (retrieve + rerank + grade)). Adaptive-RAG always-C is 2.81 steps / 3.33x time. Uncapped H = **no p99**.
- **Citation check:** O(|cites|) set membership against R. NLI canary is offline/sampled, not per-request.
- **Generate cost:** Theta(instruction + n * chunk tokens), usually **>50%** of e2e cost.

### 4.6 Core Invariants (Memorize These)

1. The LLM **never searches**. It emits a tool call or a rewritten query; the retriever executes; chunks return as observations.
2. **RAG != memory.** Shared corpus is world knowledge. Agent memory is the interaction stream. Complementary, never a substitute.
3. **ACL in the filter, not the prompt.** Pre-filter `tenant`/`acl` on every dense, BM25, parent `mget`, graph-report, **and rewrite hop**. Post-filter after top-k leaks neighbors.
4. **Rerank is a tool**, not 80 joint encodes inside generator prefill. Own RPM, timeout, cache key that includes ACL.
5. **Citation IDs subset of R this hop.** ID-not-in-R is a control-plane refuse, not a style guide.
6. **Ingest plane != query plane.** Alias flip after shadow eval. Query embeddings from model B against index A = silent recall collapse.
7. **Hop cap is checkpointed state**, not `RetryPolicy`. Grader "no" is success. Terminal `insufficient_evidence`; no parametric fallback on ACL-sensitive corpora.
8. **Parent ACL is copied.** Orphan `mget` is a drop, not a generate-on-empty. Cite the child span.
9. **Score scales do not mix** without RRF or explicit `hybrid_score_norm`. Set alpha explicitly.
10. **CRAG web is an exfil path** unless the domain is allowlisted and the query is stripped of customer identifiers.
11. **Two-stage:** recall (hybrid k=50-150) then precision (rerank n=5-20). Over-retrieve into 128k is lost-in-the-middle.
12. Sparse encode the **same** chunk text BM25 will see. Dual-index skew is a silent recall bug.

---

## 5. Token Economics & Cost Analysis

### 5.1 Per-Query Cost Breakdown

**Reference query (stated, not a SKU):** 1k user questions, no agent retries; query embed 50 tok; retrieve 80 fused; rerank 80; keep 8 x 500 tok = 4k context; generate 4k in + 400 out.

**Cost formula:**

```
C_query = (50 * P_embed) + ((q_tok * N + sum(d_i)) * P_rerank) + (4000 * P_in + 400 * P_out)
```

| Line item | Arithmetic | $ / 1k queries |
| --- | --- | --- |
| Query embed (text-embedding-3-small) | 50k tok x $0.02/1M | **$0.001** |
| Voyage rerank-2.5 | 44k tok/query x $0.05/1M = $0.0022/q | **$2.20** |
| Generate Claude Sonnet 5 | 4k x $2 + 400 x $10 per 1M = $0.012/q | **$12.00** |
| **Subtotal** | | **~ $14.2** |

**On Sonnet 5, generation dominates.** Flip the generator to a mini-tier (~$0.15/$0.60 class): generate ~$0.84/1k -> subtotal **~$3.0** and **rerank dominates**.

**Component-level costs for GPT-4o path (8 chunks, ~250 token answer):**

| Component | Cost per Query | Notes |
| --- | --- | --- |
| Query embedding | ~$0.0000004 | 20 tokens at $0.02/1M |
| Vector search (self-managed) | ~$0.0005 | Amortized infra cost |
| BM25 search | ~$0.0002 | Amortized infra cost |
| RRF fusion | negligible | CPU-only, <1ms |
| Reranking (Cohere v3.5) | $0.002 | $2/1K searches |
| LLM generation (GPT-4o) | ~$0.008 | ~1800 input + 250 output tokens |
| **Total (advanced RAG)** | **~$0.004-0.01** | |

### 5.2 Agentic RAG Cost Multiplier

Each retrieval iteration adds another search + rerank + partial generation cycle. A 3-hop query costs roughly 3-5x the single-pass cost.

| Policy | Extra LLM calls / user Q | Extra rerank | Extra $ / 1k (Sonnet 5 + Voyage 2.5) |
| --- | --- | --- | --- |
| Naive hybrid+rerank+1 generate | 1 generate | 1 | (baseline ~$14.2) |
| Always grade, never rewrite | +1 cheap grade | 0 | **~$0.8-2** |
| 20% of queries rewrite once | 0.2 x (grade+rewrite+generate) + 0.2 rerank | 0.2 | **~$3-4** |
| Uncapped CRAG+web, 3 hops avg | 3x generate-class | 3 | **tens of $ / 1k** + web SKU |

**Hidden cost multipliers:**
- HyDE adds a **full generate** before the first retrieve -- budget it as hop 0.
- OpenAI web-search tool: **$10 / 1k calls** + content tokens. CRAG Incorrect->web on 10% of traffic = $1/1k user questions in tool fees alone.
- Cohere search-unit inflation: 80 fused 800-token chunks auto-split -> more billed units than expected.

**Pinecone Database RUs**: Standard ~$16-$18 / million RUs, storage $0.33/GB/mo. Query RUs scale with **namespace size** (1 GB ns = 1 RU/query; metadata-filter a 100 GB shared ns = 100 RUs). 1k queries x 1 RU x $16/M = $0.016; same against 100 GB = $1.60.

**Monthly cost at scale:**

| Configuration | Cost/Query | Monthly (5k queries/day) |
| --- | --- | --- |
| Naive RAG (dense only, no rerank) | ~$0.001 | ~$150 |
| Hybrid + Rerank | ~$0.004 | ~$600 |
| Blended (70% simple, 30% agentic) | ~$0.015 | ~$2,250-4,500 |
| Full agentic every query | ~$0.01-0.06 | ~$3,000-18,000 |

### 5.3 Embedding Model Cost-Quality Tradeoffs

| Model | Cost per 1M Tokens | MTEB Score | Best For |
| --- | --- | --- | --- |
| text-embedding-3-small | ~$0.02 | ~62 | Budget deployments, prototyping |
| text-embedding-3-large | ~$0.13 | 64.6 | Production baseline |
| Voyage-4-large | ~$0.12 | Best retrieval API | High-stakes retrieval |
| Gemini Embedding 001 | ~$0.15 | Best all-rounder | Multimodal (text, image, video, audio) |
| Qwen3-Embedding-0.6B | Self-hosted | 64.34 | Self-hosted, latency-sensitive |
| KaLM-Gemma3-12B | Self-hosted | 72.32 | Current MTEB leader, requires GPU |
| BGE-M3 | Self-hosted | 63.0 | Multi-language, flexible |

**Corpus embedding costs:**
- 10K documents (~5M tokens): ~$0.10-0.75 one-time. Trivial.
- 1M documents (~500M tokens): ~$10-75 one-time + $70-400/month hosting.
- 10M documents (~5B tokens): ~$100-750 one-time + $400-2,000/month hosting.

**Contextual Retrieval ingest (Anthropic official):** $1.02 / 1M document tokens one-time with prompt cache. 100M-token corpus -> ~$102 ingest LLM before embeddings.

### 5.4 Latency SLA Targets

**Key insight**: Retrieval takes ~35% of time-to-first-token in production systems, while generation takes >50% of dollar cost. The expensive stage (generation, in dollars) and the slow stage (retrieval + reranking, in wall-clock) are often different.

**Stage-level latency budget:**

| Stage | P50 | P95 | Budget % |
| --- | --- | --- | --- |
| Query embed | 5-20ms | 30ms | 2% |
| Vector search (HNSW, 1M vectors) | 2-10ms | 25ms | 3% |
| BM25 search | 5-15ms | 30ms | 3% |
| RRF fusion | <1ms | <2ms | <1% |
| Cross-encoder rerank (100 docs) | 100-300ms | 500ms | 35% |
| Context assembly | 1-5ms | 10ms | 1% |
| LLM generation (TTFT) | 200-800ms | 2000ms | 55% |
| **Total (single-pass)** | **350-800ms** | **1.5-2.5s** | **100%** |

**SLO architecture targets (inferred policy):**

| Metric | Target | Mitigation |
| --- | --- | --- |
| p50 retrieve+rerank | 150-400 ms | Hot rerank cache with ACL in key; skip retrieve on Adaptive-A |
| p95 retrieve+rerank | 400 ms - 1.5 s | Cap fused N; Voyage lite; drop to fused top-8 on rerank timeout |
| p99 retrieve+rerank | Fail closed at 1-3 s | Search breaker; BM25-only; then `retrieval_degraded` refuse |
| p50 e2e with generate | 0.8-2 s | Stream generate; Adaptive skip retrieve; n=5-20 not 50 |
| p95 e2e | 2-6 s | Hop cap=3; grade on Haiku; do not HyDE SKUs |
| p99 e2e | 8-15 s with hop cap=3 | Wall-clock fuse; `insufficient_evidence`. Unbounded rewrite has **no** p99 |

**Agentic RAG latency**: Re-querying the retriever every 4 generated tokens pushes end-to-end latency to ~30 seconds, with retrieval and re-prefill eating 81% of total time. This is why the query complexity classifier matters.

### 5.5 Quality Metrics: RAGAS Framework

**RAGAS** (Retrieval-Augmented Generation Assessment, EACL 2024): Open-source framework processing 5M+ evaluations/month used by AWS, Microsoft, Databricks, Moody's.

**Four core metrics** (all 0-1, higher is better):

1. **Faithfulness** (generation quality): LLM-as-judge breaks the answer into atomic claims and checks each against retrieved context. Does NOT require ground truth. **Most important single metric.**
2. **Answer Relevancy** (generation quality): Reverse-generation approach -- judge generates hypothetical questions the answer could answer, measures cosine similarity to the actual question.
3. **Context Precision** (retrieval quality): Are relevant chunks ranked highly?
4. **Context Recall** (retrieval quality): Does retrieved context contain all information needed? Only metric requiring ground-truth.

**Healthy production benchmarks:**
- Context recall > 0.80
- Context precision > 0.70
- Faithfulness > 0.85
- Answer relevance > 0.80

**Diagnostic patterns:**

| Pattern | Root Cause | Fix |
| --- | --- | --- |
| High context recall + Low faithfulness | Right chunks retrieved, model ignoring/distorting them | Improve prompt, switch model, add citation enforcement |
| Low context recall + High faithfulness | Model faithful to what it has, retrieval missing key info | Improve chunking, add reranking, expand retrieval k |
| Low answer relevancy + High faithfulness | Retrieved context does not address the question | Fix query rewriting, improve index coverage |
| Both low faithfulness + low context recall | Systemic failure across retrieval and generation | Audit chunking, embedding model, and prompt in that order |

**Additional metrics for agentic RAG**: Tool Call Accuracy, Agent Goal Accuracy, Topic Adherence.

### 5.6 Throughput and Back-Pressure

| Knob | Value | Impact |
| --- | --- | --- |
| Cohere Rerank | trial **10 RPM**, production **1,000 RPM** | 1k user QPS x 3 hops = 3k retrieve RPM -- size for the **loop** |
| Voyage rerank | <= 1,000 docs; total <= 600k tok; Batch 33% off / 12h | Token meter, not search-units |
| OpenAI embeddings | 300k tok/request, 2048 inputs | Ingest batch != query embed pool |
| Pinecone RUs | 1 RU / GB of that namespace | Shared 100 GB ns = 100x RUs |
| ES `rank_window_size` | default 10 | Raise to 50-100: recall vs coordinator RAM |
| OpenSearch hybrid | max 5 subqueries | Coordinator OOM is a query-plane incident |
| Agent hop cap | **3** + wall-clock | Uncapped = unbounded $ and no p99 |

**Back-pressure design:**
1. Admit the query iff search breaker in {closed, half-open} or you will take the BM25-only/refuse path.
2. Shed in order: skip rerank (fused top-8) -> BM25-only -> `retrieval_degraded` refuse. **Never** generate ungrounded if policy forbids.
3. Size rerank RPM for H x user QPS, not user QPS. Cache rerank on agent retries.
4. Ingest: queue; watermark; never block query p99 on HNSW rebuild. Embedder version-pin.

---

## 6. Production Patterns & Code

### 6.1 Full Agentic RAG Control Plane (Grok -- Production Runtime)

This is the recommended production code. It encodes: full-jitter HTTP retries, circuit breakers per plane, ACL pre-filter, RRF fusion, rerank as a tool with timeout fallback, parent-child expansion with ACL copy, hop-capped rewrite loop, citation allowlist, zero-trust tool proxies, and JSON structured logging.

```python
#!/usr/bin/env python3
"""Agentic RAG production control plane. Python 3.11+.

  python agentic_rag_runtime.py

Offline self-test uses in-process indexes (no network, no LLM, no Pinecone).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, TypeVar

INITIAL_RETRY_DELAY, MAX_RETRY_DELAY, SDK_DEFAULT_MAX_RETRIES = 0.5, 8.0, 2
RRF_K, FUSED_N, RERANK_TOP_N, MAX_ATTEMPTS = 60, 80, 8, 3
PARENT_TOKEN_CAP, DENSE_K, BM25_K = 2000, 50, 50

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Logging: JSON structured with correlation_id, tenant, user_hash, plane
# ---------------------------------------------------------------------------

class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
            "msg": record.getMessage(), "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "user_hash": getattr(record, "user_hash", None),
            "plane": getattr(record, "plane", None),
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


def build_logger(correlation_id: str, tenant: str, user_id: str | None = None,
                 plane: str | None = None) -> CorrelationAdapter:
    base = logging.getLogger("rag.runtime")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    extra: dict[str, Any] = {"correlation_id": correlation_id, "tenant": tenant}
    if user_id:
        extra["user_hash"] = hashlib.sha256(user_id.encode()).hexdigest()[:12]
    if plane:
        extra["plane"] = plane
    return CorrelationAdapter(base, extra)


# ---------------------------------------------------------------------------
# Error types: transient (retryable) vs permanent (fail the turn)
# ---------------------------------------------------------------------------

class TransientError(Exception):
    def __init__(self, msg: str, retry_after: float | None = None,
                 status: int | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after
        self.status = status


class PermanentError(Exception):
    """RBAC deny, cite-not-in-R, parent ACL skew. Never retry."""
    pass


class CircuitOpenError(TransientError):
    pass


# ---------------------------------------------------------------------------
# Circuit breaker: one per search / rerank / generate / ingest
# ---------------------------------------------------------------------------

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class BreakerStateMachine:
    """Per search / rerank / generate / ingest. Do not trip on 429-with-Retry-After."""

    def __init__(self, name: str, failure_threshold: int = 5,
                 recovery_seconds: float = 30.0, half_open_max: int = 1) -> None:
        self.name, self.failure_threshold = name, failure_threshold
        self.recovery_seconds, self.half_open_max = recovery_seconds, half_open_max
        self._state, self._failures, self._opened_at = BreakerState.CLOSED, 0, 0.0
        self._half_open_inflight, self._lock = 0, asyncio.Lock()

    async def allow(self) -> None:
        async with self._lock:
            if (self._state is BreakerState.OPEN
                    and (time.monotonic() - self._opened_at) >= self.recovery_seconds):
                self._state = BreakerState.HALF_OPEN
                self._half_open_inflight = 0
            if self._state is BreakerState.OPEN:
                raise CircuitOpenError(f"circuit_open:{self.name}")
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_inflight >= self.half_open_max:
                    raise CircuitOpenError(f"circuit_half_open_busy:{self.name}")
                self._half_open_inflight += 1

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._half_open_inflight = 0
            self._state = BreakerState.CLOSED

    async def record_failure(self, *, trip: bool = True) -> None:
        async with self._lock:
            if not trip:
                return
            self._failures += 1
            if (self._state is BreakerState.HALF_OPEN
                    or self._failures >= self.failure_threshold):
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = 0

    @property
    def state(self) -> BreakerState:
        return self._state


# ---------------------------------------------------------------------------
# Retry with full jitter. Never wrap PermanentError.
# ---------------------------------------------------------------------------

async def retry_with_jitter(
    fn: Callable[[], Awaitable[T]], *, log: CorrelationAdapter,
    attempts: int = SDK_DEFAULT_MAX_RETRIES + 1,
    base: float = INITIAL_RETRY_DELAY, cap: float = MAX_RETRY_DELAY,
) -> T:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except PermanentError:
            raise
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            ra = exc.retry_after
            sleep_s = (ra if ra is not None and 0 < ra <= 60
                       else random.random() * min(cap, base * (2 ** i)))
            log.warning("http_retry attempt=%s sleep_s=%.3f err=%s", i + 1, sleep_s, exc)
            await asyncio.sleep(sleep_s)
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def redact_pii(text: str) -> str:
    """Deterministic DLP stand-in: digit runs of length >=9 become [PII]."""
    return re.sub(r"(?<!\d)(?:\d[\- ]*){8,}\d(?!\d)", "[PII]", text)


def rrf_fuse(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Cormack SIGIR 2009. Rank-only; k=60."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    tenant_id: str
    collection: str
    acl: frozenset[str]
    text: str
    parent_id: str | None = None
    char_span: tuple[int, int] | None = None
    version: str = "v1"
    sha256: str = ""
    tokens: int = 0
    quarantined: bool = False

    def __post_init__(self) -> None:
        if not self.sha256:
            object.__setattr__(self, "sha256",
                               hashlib.sha256(self.text.encode()).hexdigest()[:16])
        if not self.tokens:
            object.__setattr__(self, "tokens", estimate_tokens(self.text))


@dataclass(frozen=True)
class ParentDoc:
    parent_id: str
    tenant_id: str
    collection: str
    acl: frozenset[str]
    text: str
    version: str = "v1"
    tokens: int = 0

    def __post_init__(self) -> None:
        if not self.tokens:
            object.__setattr__(self, "tokens", estimate_tokens(self.text))


@dataclass
class LoopState:
    original_question: str
    search_query: str
    retry_count: int = 0
    retrieved_ids: frozenset[str] = field(default_factory=frozenset)
    cited_ids: tuple[str, ...] = ()
    status: str = "ok"


# ---------------------------------------------------------------------------
# Reranker protocol + offline stand-in
# ---------------------------------------------------------------------------

class Reranker(Protocol):
    model_id: str

    def rerank(self, query: str, docs: Sequence[Chunk], top_n: int) -> list[Chunk]: ...


class OverlapReranker:
    """Offline stand-in for a cross-encoder."""
    model_id = "overlap-rerank-offline"

    def rerank(self, query: str, docs: Sequence[Chunk], top_n: int) -> list[Chunk]:
        q = set(query.lower().split())
        scored = []
        for d in docs:
            toks = set(d.text.lower().split())
            scored.append((len(q & toks) + 0.01 * (1.0 / max(1, d.tokens)), d))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [d for _s, d in scored[:top_n]]


# ---------------------------------------------------------------------------
# ACL: namespace-level + tool RBAC
# ---------------------------------------------------------------------------

class NamespaceACL:
    retrieve_tools: frozenset[str] = frozenset({
        "retrieve_public_kb", "retrieve_hr", "retrieve_legal", "rerank_api"
    })

    def authorize(self, token_tenant: str, token_roles: frozenset[str],
                  chunk: Chunk) -> None:
        if chunk.tenant_id != token_tenant:
            raise PermanentError("acl_tenant_denied")
        if chunk.quarantined:
            raise PermanentError("acl_quarantine")
        if (chunk.acl and chunk.acl.isdisjoint(token_roles)
                and "admin" not in token_roles):
            raise PermanentError("acl_role_denied")

    def authorize_tool(self, tool: str, token_roles: frozenset[str],
                       collection: str) -> None:
        if tool not in self.retrieve_tools:
            raise PermanentError(f"rbac_deny:{tool}")
        if tool == "retrieve_hr" and "hr" not in token_roles and "admin" not in token_roles:
            raise PermanentError("rbac_hr_denied")
        if tool == "retrieve_legal" and "counsel" not in token_roles and "admin" not in token_roles:
            raise PermanentError("rbac_legal_denied")


# ---------------------------------------------------------------------------
# HybridIndex: ACL pre-filter THEN dense || BM25
# ---------------------------------------------------------------------------

class HybridIndex:
    """ACL pre-filter THEN dense||BM25. Post-filter-after-top-k is the leak."""

    def __init__(self) -> None:
        self.chunks: dict[str, Chunk] = {}
        self.parents: dict[str, ParentDoc] = {}
        self.alias: str = "N"
        self.worm: list[dict[str, Any]] = []

    def upsert(self, chunk: Chunk, parent: ParentDoc | None = None) -> None:
        self.chunks[chunk.chunk_id] = chunk
        if parent is not None:
            if (parent.acl != chunk.acl or parent.tenant_id != chunk.tenant_id
                    or parent.version != chunk.version):
                raise PermanentError("parent_acl_or_version_skew")
            self.parents[parent.parent_id] = parent

    def _visible(self, token_tenant: str, token_roles: frozenset[str],
                 collection: str) -> list[Chunk]:
        out = []
        for c in self.chunks.values():
            if (c.tenant_id != token_tenant or c.collection != collection
                    or c.quarantined):
                continue
            if (c.acl and c.acl.isdisjoint(token_roles)
                    and "admin" not in token_roles):
                continue
            out.append(c)
        return out

    def bm25_rank(self, query: str, token_tenant: str,
                  token_roles: frozenset[str], collection: str,
                  k: int = BM25_K) -> list[str]:
        q = query.lower().split()
        scored: list[tuple[int, str]] = []
        for c in self._visible(token_tenant, token_roles, collection):
            text = c.text.lower()
            overlap = sum(1 for t in q if t in text.split() or t in text)
            if overlap:
                scored.append((overlap, c.chunk_id))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [cid for _s, cid in scored[:k]]

    def dense_rank(self, query: str, token_tenant: str,
                   token_roles: frozenset[str], collection: str,
                   k: int = DENSE_K) -> list[str]:
        # Simulates semantic search via synonym expansion
        synonyms = {"refund": "sla", "error": "ts-999", "protocol": "section"}
        q = set(query.lower().split())
        q |= {synonyms[t] for t in list(q) if t in synonyms}
        scored: list[tuple[float, str]] = []
        for c in self._visible(token_tenant, token_roles, collection):
            toks = set(c.text.lower().split())
            inter = len(q & toks)
            if inter:
                scored.append((inter / max(1, len(q | toks)), c.chunk_id))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [cid for _s, cid in scored[:k]]

    def hybrid_rrf(self, query: str, token_tenant: str,
                   token_roles: frozenset[str], collection: str) -> list[Chunk]:
        dense = self.dense_rank(query, token_tenant, token_roles, collection)
        bm25 = self.bm25_rank(query, token_tenant, token_roles, collection)
        fused = rrf_fuse([dense, bm25])[:FUSED_N]
        return [self.chunks[cid] for cid, _s in fused if cid in self.chunks]

    def parent_lookup(self, children: Sequence[Chunk], token_tenant: str,
                      token_roles: frozenset[str]) -> list[Chunk]:
        """Small-to-big. Drop orphan mget; re-apply ACL; cap parent tokens."""
        seen: set[str] = set()
        expanded: list[Chunk] = []
        for child in children:
            pid = child.parent_id
            if pid is None or pid in seen:
                expanded.append(child)
                continue
            seen.add(pid)
            parent = self.parents.get(pid)
            if parent is None:
                continue  # orphan -- surface via mget_miss metric in prod
            if parent.tenant_id != token_tenant:
                continue
            if (parent.acl and parent.acl.isdisjoint(token_roles)
                    and "admin" not in token_roles):
                continue
            text = parent.text if parent.tokens <= PARENT_TOKEN_CAP else child.text
            expanded.append(Chunk(
                chunk_id=child.chunk_id, tenant_id=child.tenant_id,
                collection=child.collection, acl=child.acl, text=text,
                parent_id=pid, char_span=child.char_span,
                version=child.version, sha256=child.sha256,
                tokens=estimate_tokens(text),
            ))
        return expanded


# ---------------------------------------------------------------------------
# Citation allowlist: cite-not-in-R is a PermanentError
# ---------------------------------------------------------------------------

def citation_allowlist(cited: Sequence[str],
                       retrieved: frozenset[str]) -> tuple[str, ...]:
    extra = [c for c in cited if c not in retrieved]
    if extra:
        raise PermanentError(f"citation_not_in_R:{extra}")
    return tuple(cited)


def deterministic_degraded(turn_id: str) -> dict[str, Any]:
    return {"status": "retrieval_degraded", "turn_id": turn_id,
            "answer": None, "citations": [], "retrieved": []}


# ---------------------------------------------------------------------------
# Orchestrator: the bounded retrieve -> grade -> rewrite -> generate loop
# ---------------------------------------------------------------------------

class AgenticRAGOrchestrator:
    def __init__(self, index: HybridIndex | None = None,
                 reranker: Reranker | None = None) -> None:
        self.index = index or HybridIndex()
        self.acl = NamespaceACL()
        self.reranker: Reranker = reranker or OverlapReranker()
        self.search_breaker = BreakerStateMachine("rag.search")
        self.rerank_breaker = BreakerStateMachine("rag.rerank")

    def grade(self, query: str, docs: Sequence[Chunk]) -> bool:
        """Binary ISREL stand-in. Production: cheap structured yes/no."""
        q = set(query.lower().split())
        return any(q & set(d.text.lower().split()) for d in docs)

    def rewrite(self, original: str, previous: str, hop: int) -> str:
        """Deterministic rewrite stand-in. Production: LLM call."""
        if "ts-999" not in previous.lower() and "error" in original.lower():
            return f"{previous} TS-999"
        return f"{previous} procedure section {hop}"

    async def retrieve(
        self, query: str, token_tenant: str, token_roles: frozenset[str],
        collection: str, log: CorrelationAdapter, *,
        lexical_only: bool = False,
        retrieve_fn: Callable[[], Awaitable[list[Chunk]]] | None = None,
    ) -> list[Chunk]:
        await self.search_breaker.allow()

        async def _do() -> list[Chunk]:
            if retrieve_fn is not None:
                return await retrieve_fn()
            if lexical_only:
                ids = self.index.bm25_rank(query, token_tenant, token_roles,
                                           collection)
                return [self.index.chunks[i] for i in ids[:FUSED_N]]
            return self.index.hybrid_rrf(query, token_tenant, token_roles,
                                         collection)

        try:
            hits = await retry_with_jitter(_do, log=log)
            await self.search_breaker.record_success()
            return hits
        except TransientError:
            await self.search_breaker.record_failure(trip=True)
            raise

    async def rerank_tool(self, query: str, docs: Sequence[Chunk],
                          log: CorrelationAdapter) -> list[Chunk]:
        if not docs:
            return []
        try:
            await self.rerank_breaker.allow()
            out = self.reranker.rerank(query, docs, RERANK_TOP_N)
            await self.rerank_breaker.record_success()
            return out
        except (CircuitOpenError, TransientError) as exc:
            log.warning("rerank_timeout_fused_top8 err=%s", exc)
            await self.rerank_breaker.record_failure(trip=True)
            return list(docs[:RERANK_TOP_N])  # fallback to fused top-8

    async def run_loop(
        self, *, token_tenant: str, token_roles: frozenset[str],
        collection: str, question: str, log: CorrelationAdapter,
        turn_id: str, require_grounding: bool = True,
        retrieve_fn: Callable[[], Awaitable[list[Chunk]]] | None = None,
    ) -> dict[str, Any]:
        state = LoopState(original_question=question, search_query=question)
        last_docs: list[Chunk] = []
        lexical_only = False

        while state.retry_count < MAX_ATTEMPTS:
            # --- Retrieve (hybrid or BM25-only fallback) ---
            try:
                fused = await self.retrieve(
                    state.search_query, token_tenant, token_roles,
                    collection, log, lexical_only=lexical_only,
                    retrieve_fn=retrieve_fn,
                )
            except (CircuitOpenError, TransientError) as exc:
                log.warning("search_fallback lexical=%s err=%s",
                            lexical_only, exc)
                if not lexical_only:
                    lexical_only = True
                    if isinstance(exc, CircuitOpenError):
                        state.retry_count += 1
                    continue
                if require_grounding:
                    return deterministic_degraded(turn_id)
                raise PermanentError("parametric_fallback_forbidden") from exc

            # --- Rerank -> Parent expand -> Grade ---
            ranked = await self.rerank_tool(state.search_query, fused, log)
            expanded = self.index.parent_lookup(ranked, token_tenant,
                                                 token_roles)
            last_docs = expanded
            state.retrieved_ids = frozenset(d.chunk_id for d in expanded)

            if self.grade(state.search_query, expanded) and expanded:
                # --- Generate with citation constraint ---
                cites = citation_allowlist(
                    [d.chunk_id for d in expanded[:3]], state.retrieved_ids)
                state.cited_ids = cites
                answer = (f"grounded:{state.original_question}"
                          f"|q={state.search_query}|hops={state.retry_count}")
                self.index.worm.append({
                    "op": "generate",
                    "cid": log.extra.get("correlation_id"),
                    "R": list(state.retrieved_ids),
                    "cites": list(cites),
                    "index_alias": self.index.alias,
                    "hops": state.retry_count,
                })
                log.info("generate hops=%s cites=%s nR=%s",
                         state.retry_count, cites, len(state.retrieved_ids))
                return {
                    "status": "ok", "turn_id": turn_id, "answer": answer,
                    "citations": list(cites),
                    "retrieved": list(state.retrieved_ids),
                    "hops": state.retry_count,
                    "lexical_only": lexical_only,
                    "reranker": self.reranker.model_id,
                }

            # --- Rewrite and loop ---
            state.retry_count += 1
            if state.retry_count >= MAX_ATTEMPTS:
                break
            state.search_query = self.rewrite(
                state.original_question, state.search_query,
                state.retry_count)
            log.info("rewrite hop=%s q=%s", state.retry_count,
                     state.search_query)

        # --- Terminal: insufficient evidence ---
        if require_grounding:
            log.warning("insufficient_evidence hops=%s nR=%s",
                        state.retry_count, len(last_docs))
            return {
                "status": "insufficient_evidence", "turn_id": turn_id,
                "answer": None, "citations": [],
                "retrieved": [d.chunk_id for d in last_docs],
                "hops": state.retry_count,
            }
        raise PermanentError("parametric_fallback_forbidden")


# ---------------------------------------------------------------------------
# Zero-Trust MCP proxy: identity never from model JSON
# ---------------------------------------------------------------------------

class ZeroTrustRetrieveProxy:
    def __init__(self, orch: AgenticRAGOrchestrator,
                 allowed: frozenset[str]) -> None:
        self.orch = orch
        self.allowed = allowed

    def execute(self, *, principal: str, token_tenant: str,
                token_roles: frozenset[str], tool: str,
                args: dict[str, Any]) -> str:
        if not principal:
            raise PermanentError("missing_principal")
        if tool not in self.allowed:
            raise PermanentError(f"rbac_deny:{tool}")
        # Reject confused-deputy: model-invented tenant_id
        if "tenant_id" in args and args["tenant_id"] != token_tenant:
            raise PermanentError("confused_deputy_tenant_id")
        if "collection" in args:
            self.orch.acl.authorize_tool(tool, token_roles,
                                          str(args["collection"]))
        collection = str(args.get("collection", "support"))
        query = redact_pii(str(args.get("q", "")))
        hits = self.orch.index.hybrid_rrf(query, token_tenant, token_roles,
                                           collection)
        return json.dumps([{"id": c.chunk_id, "collection": c.collection}
                           for c in hits[:8]])


if __name__ == "__main__":
    # Self-test: offline, no network, no LLM, no Pinecone
    cid, tenant = str(uuid.uuid4()), "acme"
    log = build_logger(cid, tenant, "user:alice", "query")
    orch = AgenticRAGOrchestrator()

    parent = ParentDoc(
        parent_id="p-runbook", tenant_id=tenant, collection="support",
        acl=frozenset({"support"}),
        text="Procedure: Error code TS-999 replace the widget. "
             "Refund SLA is 30 days for paid plans.",
    )
    child = Chunk(
        chunk_id="c-ts999", tenant_id=tenant, collection="support",
        acl=frozenset({"support"}),
        text="Error code TS-999 replace the widget",
        parent_id="p-runbook", char_span=(0, 36),
    )
    orch.index.upsert(child, parent)

    out = asyncio.run(orch.run_loop(
        token_tenant=tenant, token_roles=frozenset({"support"}),
        collection="support", question="what is error TS-999",
        log=log, turn_id="t1",
    ))
    assert out["status"] == "ok" and "c-ts999" in out["citations"]
    print(json.dumps(out, indent=2))
```

### 6.2 NLI Citation Verification (Opus -- Verification Pipeline)

```python
"""
Citation verification using Natural Language Inference (NLI).
Checks whether each claim-citation pair is actually entailed by the source.
"""
import re
from dataclasses import dataclass
from transformers import pipeline


@dataclass
class CitationVerification:
    claim: str
    cited_chunk_id: str
    cited_text: str
    nli_label: str      # entailment | contradiction | neutral
    nli_score: float
    is_faithful: bool


class CitationVerifier:
    """NLI-based citation verifier using DeBERTa-v3."""

    def __init__(self, model_name: str = "microsoft/deberta-v3-large-mnli"):
        self.nli = pipeline("text-classification", model=model_name, top_k=None)
        self.entailment_threshold = 0.7

    def _extract_claims_and_citations(
        self, answer: str, chunks_by_id: dict[str, str],
    ) -> list[tuple[str, str, str]]:
        sentences = re.split(r'(?<=[.!?])\s+', answer)
        triples = []
        for sentence in sentences:
            cited_ids = re.findall(r'\[([^\]]+)\]', sentence)
            clean_sentence = re.sub(r'\[[^\]]+\]', '', sentence).strip()
            if not clean_sentence:
                continue
            for cid in cited_ids:
                if cid in chunks_by_id:
                    triples.append((clean_sentence, cid, chunks_by_id[cid]))
            if not cited_ids:
                triples.append((clean_sentence, "__UNCITED__", ""))
        return triples

    def verify(self, answer: str,
               chunks_by_id: dict[str, str]) -> list[CitationVerification]:
        triples = self._extract_claims_and_citations(answer, chunks_by_id)
        results = []
        for claim, chunk_id, source_text in triples:
            if chunk_id == "__UNCITED__":
                results.append(CitationVerification(
                    claim=claim, cited_chunk_id="__UNCITED__",
                    cited_text="", nli_label="no_citation",
                    nli_score=0.0, is_faithful=False,
                ))
                continue
            nli_output = self.nli(f"{source_text}", candidate_labels=None,
                                  hypothesis=claim)
            label_scores = {item["label"]: item["score"]
                            for item in nli_output}
            entailment_score = label_scores.get("ENTAILMENT", 0.0)
            top_label = max(label_scores, key=label_scores.get)
            results.append(CitationVerification(
                claim=claim, cited_chunk_id=chunk_id,
                cited_text=source_text[:200],
                nli_label=top_label.lower(),
                nli_score=entailment_score,
                is_faithful=entailment_score >= self.entailment_threshold,
            ))
        return results

    def compute_faithfulness_score(
        self, verifications: list[CitationVerification],
    ) -> float:
        """RAGAS-style faithfulness: fraction of claims faithfully cited."""
        if not verifications:
            return 0.0
        return sum(1 for v in verifications if v.is_faithful) / len(verifications)
```

---

## 7. Failure Modes & Mitigations

### 7.1 Comprehensive Failure Taxonomy

| Class | Examples | Handler |
| --- | --- | --- |
| **Transient** | 408/429/5xx/529, ANN timeout, Cohere 1000 RPM, embedder blip | Full jitter; search breaker; BM25-only; last-good retrieve cache keyed by ACL |
| **Permanent** | 400 schema, 401/403, RBAC deny, cite ID not in R, spend-cap 429 | Fail the turn (`insufficient_evidence` / 403). Do not failover schema 400s. Do not retry Art. 17 |
| **Poison pill (docs)** | Unreviewed connector; prompt-injection in a PDF; CRCP chunk-aware adversarial payloads | sha256 + source allowlist; quarantine; signed ingest; never auto-write CRAG web into the corpus |
| **Poison pill (ops)** | Score-scale hybrid (sparse unbounded vs dense [-1,1]); Weaviate alpha=0 gRPC; serverless dense-preselect; ES rank_window_size=10; Qdrant per-shard fusion | `hybrid_score_norm`; RRF; set alpha explicitly; two-index + client RRF |
| **Citation theater** | ID not in R; NLI-fail; post-hoc attach; parent/child version skew | ID-constrained cites; quote spans; ISSUP/CRAG grade; refuse |
| **Parent-child mismatch** | Child hits, parent missing / wrong version / looser ACL / oversize | `mget` miss rate; ACL diff child vs parent; atomic alias of both stores; cap parent tokens |
| **Rewrite loops** | Grader false negative; HyDE drift; official LangGraph has no cap | MAX_ATTEMPTS=3; Adaptive-RAG front door; `insufficient_evidence` |
| **Stale indexes** | CDC lag, failed upsert, alias not flipped, replica ONE | Watermark lag; canary CODE-CANARY-9; QUORUM; ingest checkpoints |
| **Embedding drift** | New model/dim/prompt, Matryoshka trim | nDCG on frozen golden set after every embed bump; dual-write + shadow eval |
| **Filter/ANN interaction** | Metadata filter + IVF; post-filter ACL as forbidden set grows, authorized recall -> 0 | Recall@k per tenant; bitmap/IVF bypass; namespaces |
| **Over-retrieval** | k=50 into 128k; agent 4 hops | Rerank to 5-20; edge-place evidence; hop cap |
| **Contextual PII spread** | Context prepend copies secrets into every chunk | Redact **before** contextualize |
| **Cohere unit inflation** | Docs >500 tok with query auto-split | Truncate; pre-chunk; Voyage token meter; log `billed_units.search_units` |
| **CRAG open web** | Incorrect -> Google on confidential query | Approved corpus only; outbound URL audit |
| **Rerank RPM/timeout** | 1k QPS x 80 docs > 1000 RPM | Cache; lite model; local bge; drop to fused top-8 |

### 7.2 Ingestion Failures

**Critical finding**: 80% of RAG failures trace back to ingestion and chunking, not the LLM. Most teams discover this after weeks of tuning prompts while retrieval quietly returns wrong context.

**Chunking artifacts**: When a parser drops table boundaries, merges columns, or misorders sections, retrieval returns plausible but incorrect passages. Smaller or proposition-based chunking creates 3-5x more vectors = more embedding API calls + more storage + more index overhead.

**Embedding model staleness**: Embedding model generations move every 6-12 months with 10-20 point MTEB lifts. An index built on a 2024-era model is one or two generations behind by 2026. **Staleness test**: Run eval on 50 hand-labeled queries against a current-generation model. If recall@10 lifts by 5+ points, re-embed the entire corpus. Every index must be tagged with the embedding model used. Mixing embeddings from different models in the same index makes dot-product scoring meaningless.

### 7.3 RAG Poisoning Attacks

**Attack vector**: Attacker injects malicious content into enterprise knowledge base. Does not need access to AI model, vector store, or retrieval infrastructure -- only write access to any document source the KB ingests from.

**CRCP (Chunk-aware and Rerank-Consistent Poisoning)**: Advanced adversarial framework that jointly optimizes retrieval relevance, reranker consistency, and chunk-boundary robustness. Document-level adversarial signals fragment during chunking, so CRCP crafts chunk-boundary-aware payloads.

**Poison recovery is not "re-embed and hope":**
1. Quarantine by source allowlist + sha256.
2. Do not promote web/tool observations into the corpus.
3. Hard-delete poisoned vector IDs + HNSW compaction (soft-delete is not erasure).
4. Alias rollback to N if N+1 is poisoned.
5. Replay query canaries.

### 7.4 Circuit Breaker Pattern

One breaker per **search/ANN**, one per **rerank**, one per **generate**, one per **ingest embedder**, one per **MCP retrieve server**.

```
           5xx/529/timeout rate >= threshold           probe success
  +--------+  ---------------------------------->  +------+  -----> CLOSED
  | CLOSED |                                       | OPEN |
  +---+----+  429 with Retry-After = throttle      +--+---+
      |       (stay CLOSED; sleep)                    | timer (e.g. 30 s)
      | success resets window                         v
      |                                          +----------+
      +------------------------------------------| HALF_OPEN|-- probe fail --> OPEN
                                                 | 1 cheap  |
                                                 | id-fetch |
                                                 +----------+
```

**Do not** open solely on 429-with-Retry-After. Half-open: probe with a cheap read (canary doc_id fetch), not a 150-doc rerank. Search breaker: fail toward BM25-only then refuse -- not toward ungrounded generate.

**Fallback chain (query):** hybrid (dense||BM25 + RRF + rerank) -> (search breaker / timeout) -> **BM25-only** (same ACL predicate) -> (both fail or empty R on policy corpus) -> **`retrieval_degraded` refusal**. PermanentError on RBAC / cite-not-in-R does NOT failover.

---

## 8. Security & Governance

### 8.1 Document-Level Access Control

**The security gap is in the retrieval engine, not the LLM.** In most enterprise RAG deployments, the retrieval engine operates without awareness of user permissions. OWASP LLM Top 10 (2025): Sensitive Information Disclosure jumped from number 6 to **number 2**. Vector and Embedding Weaknesses added as new category LLM08.

**ACL enforcement**: Authorization is a **hard query predicate before ANN**, on every hop, including rewrite, parent expansion, and graph-report lookup. Post-filter-only ANN: as the forbidden set grows, top-k fills with unauthorized neighbors and authorized recall -> 0.

**Isolation ladder:**
1. Metadata `tenant_id` filter (cheapest; app-bug can omit; Pinecone scans full namespace)
2. **Namespace / collection / index per tenant** (query cannot cross; 1 GB tenant = 1 RU vs 100 GB filter = 100 RUs; `$in`/`$nin` max 10,000)
3. Instance / BYOC (HIPAA/finance; Pinecone BYOC: zero inbound SSH; PrivateLink)

**Tool RBAC -- per collection, not one search():**

| Tool | Who may call | Bind |
| --- | --- | --- |
| `retrieve_public_kb` | Any authenticated principal in the tenant | Namespace from token; filter `status=current` |
| `retrieve_hr` / `retrieve_legal` | Role allow-list (HRBP, counsel) | Separate index/ns; not a `collection=` argument |
| `rerank_api` | Query plane only, after retrieve | Same ACL as the R it sees; BAA or self-host for HR/legal |
| `graph_local` | Multi-hop research roles | Entity neighborhood; ACL on nodes |
| `graph_global` | Exec/analyst roles, not L1 support | ACL on community reports, not just raw chunks |
| CRAG `web_search` | Denied on confidential tenants | Strip customer identifiers; never write hits into corpus |

### 8.2 Zero-Trust Retrieve Tools (MCP)

MCP `tools/call` on a retriever is a **data exfil API**.

1. **Server-side identity.** Tenant/ACL from the verified token, never from tool arguments the model filled. ABAC before search; predicate pushdown so ANN never ranks cross-tenant rows.
2. **Least privilege per tool.** Separate MCP servers: `retrieve_public_kb` vs `retrieve_hr` vs `rerank_api`. No omnibus `search(query, collection)`.
3. **Rerank as a tool** still sees document text -- same DPA as embed; do not send HR chunks to a shared SaaS reranker without a BAA.
4. **Stateless MCP + stateful RAG.** LangGraph `/mcp` is stateless per request; conversation memory stays in the checkpointer, not the MCP session.
5. CRAG web tool: allowlist domains; strip query of customer identifiers; log the outbound URL.
6. Re-validate authorization **at execution**, not only at plan-approval.

Remote MCP servers are OAuth 2.1 resource servers. RFC 9728 metadata; **RFC 8707** `resource` indicator; PKCE; MUST validate audience; MUST NOT token-passthrough.

### 8.3 PII in Chunks and Vectors

**Vectors are derived personal data.** Contextual Retrieval **prepends** more PII (names, quarters, revenue) into every chunk -- better retrieval, larger blast radius. Embed APIs (OpenAI/Voyage/Cohere) see plaintext -- DPA, zero-retention, or self-host BGE-M3. Vec2Text-class inversion (Morris et al., EMNLP 2023; **92% exact** on 32-token inputs) is why "we only store vectors" is not a GDPR answer.

**PII pipeline:** detect -> redact -> audit at ingress **and** before embed **and** before contextualize **and** before trace. Deterministic + ML DLP after retrieve, before prompt. Never log rerank documents at full text in shared SaaS traces. PCI: do not put PAN in chunks at all.

**Embedding inversion attacks**: Vectors are not inherently secure. Sophisticated attacks can reverse-engineer vectors to reconstruct original sensitive text. Do not treat embedding as anonymization.

Tools: Amazon Macie, Microsoft Presidio for automated PII detection and redaction.

### 8.4 Data Provenance and Compliance

**Immutable WORM audit:** `correlation_id`, tenant, hashed user, R (chunk_ids + sha256), rerank scores + model id, char_spans, `index_build_id` / alias, hop depth, `original_question` vs `search_query`, breaker state, tool name, CRAG outbound URL if any. Reconstruct an answer as: policy snapshot + R + hashed chunk bodies + generate.

**EU AI Act Article 12** (enforcement deadline: August 2, 2026): Organizations operating RAG in high-risk AI categories must maintain logs of system inputs/outputs for post-hoc auditing. Regulators will ask: which documents were retrieved, for which queries, with what access controls in place. **Penalties: up to EUR 35M or 7% of global annual revenue.**

**GDPR Article 28**: Documents surfaced through RAG oversharing that include personal data processed without valid Data Processing Agreement trigger unauthorized processing obligations with **72-hour breach notification** requirements.

**NIST SP 800-162 mapping** (Secure RAG): PEP at the vector query boundary; PDP for ABAC; redaction gate; citation validity gate. Measure: leakage rate, entitlement violation rate, provenance fidelity, false refusal.

**Delete/tombstone**: Vector delete must match source ACL revocation; eventual consistency windows (Weaviate ONE, Pinecone serverless) are a compliance bug. Graph community reports can summarize secrets into a high-level node -- ACL on reports, not just raw chunks.

---

## 9. System Design Scenarios

### Scenario 1 -- Multi-Tenant Support KB with ACL

**Problem statement.** Multi-tenant support copilot: shared runbooks + per-tenant overrides; SKU/error-code queries (TS-999); p95 chat of a few seconds; SOC2. 1k questions is the unit you price. Budget: hybrid+rerank+Sonnet 5 generate ~$14.2/1k; 20% rewrite once +$3-4/1k; Adaptive-A skip on 60% chitchat saves the entire rerank+context bill. Constraints: tenant isolation; never ungrounded refund advice; citation ID not-in-R rate target 0; retrieve timeout 200-500 ms then BM25-only then refuse.

**Architecture:**

```
                    +----------------------------------------------------------+
                    | EDGE  auth, tenant TPM, cid, PII redact BEFORE embed     |
                    | tenant/acl FROM TOKEN; no model collection=/tenant_id    |
                    +----------------------------+-----------------------------+
                                                 |
                    +----------------------------v-----------------------------+
                    | CONTROL  Adaptive router + hop cap=3 + alias pin         |
                    |  A chitchat -> no retrieve                               |
                    |  B "what is TS-999?" -> hybrid alpha low + rerank 80->8  |
                    |  C compare last two RCAs -> cap=2                        |
                    |  PEP filter on every hop; recency decay AFTER ACL        |
                    |  BREAKER search != rerank != generate                    |
                    |  FALLBACK hybrid -> BM25-only -> retrieval_degraded      |
                    |  NEVER parametric refund                                 |
                    +-----+-------------------------------+--------------------+
                          |                               |
                          v                               v
                    +------------------+            +-------------------------+
                    | DATA  Generation |            | DATA  Ingest (async)    |
                    | Sonnet 5 / Haiku |            | Temporal/Kafka; sha256  |
                    | cite child span  |            | DLP; ACL copy parent    |
                    | n=8 after rerank |            | dual-write N|N+1; flip  |
                    +--------+---------+            +-----------+-------------+
                             |                                  |
                    +--------v---------+            +-----------v-------------+
                    | TOOL PROXIES     |            | PERSIST  ns-per-tenant  |
                    | retrieve_public  |            | dense+BM25; parent      |
                    | _kb (not HR)     |            | docstore same version   |
                    | rerank_api RPM   |            | WORM R+spans+build_id   |
                    +------------------+            +-------------------------+
```

**Trade-off evaluation matrix:**

| Dimension | A. Dense-only 50 stuffed into 128k; shared ns + post-filter ACL | B. Recommended: ns-per-tenant; hybrid RRF; rerank 80->8; Adaptive front door; parent-child; hop cap 3 | C. Always-on agent + HyDE + CRAG-web + GraphRAG global |
| --- | --- | --- | --- |
| **Cost/1k** | Generate on 50 chunks dwarfs $14.2; shared 100 GB ns = 100x RUs | Mix ~$14.2 + ~$3-4 at 20% rewrite; skip retrieve on 60% chitchat | HyDE = extra generate before retrieve; CRAG-web $10/1k; global map-reduce >> hybrid |
| **Latency** | Lost-in-the-middle (middle < closed-book 56.1%); p99 tracks 50-chunk prefill | p50 150-400 ms; e2e p95 2-6 s with hop cap; Adaptive 1.03 steps/1.46x | Unbounded rewrite has no p99; Cohere 1000 RPM is the chat SLO you accidentally bought |
| **Security** | Post-filter ANN; prompt-ACL; citations leak other-tenant titles | PEP before ANN every hop; ns isolation; Zero-Trust proxy; WORM; no ungrounded refund | CRAG-web exfils the ticket; graph reports can summarize another tenant's RCA |
| **Scalability** | 1k QPS x 50 chunks is a generate bill | Size rerank for loop RPM not user QPS; Enterprise 99.95% | 3 retrieves x 1k QPS = 3k RPM vs Cohere 1k |

**Decision rationale.** B is the only design that treats RAG as two planes + a bounded loop + ACL-in-the-filter. A fails IDs, tenancy, and lost-in-the-middle. C is the right research toolbox applied to the wrong query class at unbounded cost.

### Scenario 2 -- Legal / Research Agentic RAG with Citations

**Problem statement.** "Compare protocol X vs Y"; quote-level provenance; 21 CFR 11-style or litigation hold; **no open web**. Multi-hop is real (IRCoT-class), but a hallucinated statute number is a sanctions event. Constraint: constrained decode to retrieved IDs; quote spans on every numeric/date claim; NLI canary (do not ship at ELI5's ~50% unsupported); refuse `insufficient_evidence` rather than parametric completion; WORM replay of the prompt.

**Architecture:**

```
  +-------------+    +---------------------------------------------------------+
  | Counsel /   |--->| CONTROL  Adaptive-C capped 2-3; NO HyDE on records      |
  | researcher  |    |  PEP Entra/JWT; collection=legal from TOKEN             |
  |             |    |  hybrid + optional HippoRAG PPR / IRCoT                 |
  |             |    |  CRAG Correct/Ambiguous vs licensed corpus ONLY          |
  |             |    |  Incorrect -> second internal collection or HITL         |
  |             |    |  citation decoder subset of R; quote spans required      |
  |             |    |  hop cap 2-3 + wall-clock; insufficient_evidence         |
  |             |    |  BREAKER search -> BM25 -> refuse (never parametric)     |
  +-------------+    +-----------+--------------------------+------------------+
                                 |                          |
                                 v                          v
                     +---------------------+     +-----------------------------+
                     | DATA  Generation    |     | DATA  Ingest (Temporal)     |
                     | BAA'd Sonnet /      |     | structure-aware chunk       |
                     | constrained cites   |     | children 300-500; parent=   |
                     | NLI canary sampled  |     | section; glossary in        |
                     |                     |     | contextualizer; DLP FIRST   |
                     +----------+----------+     +--------------+--------------+
                                v                               v
                     +---------------------------------------------------------+
                     | TOOL  retrieve_legal + rerank (BAA or self-host bge)     |
                     |       graph_local (ontology NER, human-reviewed edges)   |
                     | PERSIST  licensed corpus; WORM index_build_id + R +      |
                     |          spans + model ids; legal-hold = no alias GC     |
                     +---------------------------------------------------------+
```

**Trade-off evaluation matrix:**

| Dimension | A. Vector top-50 + self-cite; HyDE; CRAG->Google; parent = whole PDF | B. Recommended: hybrid + BAA rerank 150->20; parent=section; ID-constrained cites + quote spans; CRAG internal-only; hop cap 2-3; WORM replay | C. Full GraphRAG Leiden global + LLM listwise 200 chunks + unconstrained NER |
| --- | --- | --- | --- |
| **Cost/1k** | HyDE extra generate; web $10/1k; 50-chunk generate > $14.2 | Baseline ~$14.2 + bounded hops; HippoRAG PPR 10-20x cheaper | Extract ~75% of GraphRAG index cost; global map-reduce >> query cost; 200-way listwise is frontier bill |
| **Latency** | Gold in middle < closed-book 56.1%; HyDE adds full generate | p99 exists because H <= 3; rerank 150 is the precision tax you chose | Global = worst p99; DRIFT adds 2 local iterations on top |
| **Citation accuracy** | Self-cite points at non-existing docs (MIRAGE); 8-15% hallucination rate | <3% with quote-then-answer + NLI; ID-constrained cites; refuse not fabricate | ~5% (entity extraction errors); community reports summarize secrets |
| **Audit readiness** | Weak (no verification layer) | Full (NLI scores logged per claim; WORM chain-of-custody) | Medium (graph traversal logged) |
| **Security** | Web exfil of matter names; whole-PDF parent ACL-upgrades public exhibit | PEP every hop; parent=section with copied ACL; no open web; WORM | Community reports summarize secrets into globally searchable nodes |

**Decision rationale.** B is the only design that treats a citation as an ID constraint plus a quote span plus WORM, not a footnote style and not a web-augmented chatbot. A fails provenance and confidentiality. C is the right global-themes tool used as a default path -- Microsoft OSS is maintenance-mode.

---

## 10. Interview Quick Reference

### Key Numbers to Memorize

| Number | What |
| --- | --- |
| **~ $14.2 / 1k** | Hybrid + Voyage rerank-2.5 + Sonnet 5 generate (gen $12, rerank $2.20, embed $0.001) |
| **~$3 / 1k** | Same mix on mini-tier generator -- rerank dominates |
| **~$3-4 / 1k** | Extra at 20% rewrite-once |
| **tens of $ / 1k** | Uncapped CRAG+web, 3 hops avg |
| **$1.02 / 1M** | Anthropic contextualize one-time (official) |
| **k=60 / 1/61 ~ 0.0164** | RRF constant; rank-1 contribution |
| **alpha = 0.75 / 0.5 / 0** | Weaviate default if unset / Vertex default / gRPC zero-value BM25 trap |
| **150->20 / 80->8 / top-50** | Anthropic rerank / support default / Azure Semantic Ranker |
| **1,000 RPM / 10 RPM** | Cohere Rerank prod / trial -- size for loop QPS |
| **MAX_ATTEMPTS=3** | Production hop cap; LangGraph tutorial has none; RetryPolicy != grader "no" |
| **1.03 vs 2.81 steps** | Adaptive-RAG vs always-C (1.46x vs 3.33x time; F1 50.91 vs 46.99) |
| **66.9 vs 2.9** | Self-RAG 7B ASQA citation precision vs always-retrieve Llama2-7B |
| **51.5% / 74.5% / r=-0.96** | Liu citation recall / precision / inverse utility facade |
| **< closed-book 56.1%** | Lost-in-the-middle gold-in-middle on GPT-3.5 |
| **5.7% -> 1.9% (-67%)** | Anthropic contextual + rerank failure drop |
| **8-15% -> <3%** | Hallucination rate: standard RAG vs quote-then-answer + NLI |
| **0.816 Recall@5** | Hybrid + Cohere Rerank on WANDS (+39% over dense-only) |
| **99.95%** | Pinecone Enterprise uptime SLA; Starter/Standard: none |

### Interview Traps (Fail These, Fail the Round)

1. **RAG index as "user memory."** RAG != memory. Shared corpus + missing `user_id`/`tenant` pre-filter -> other-tenant retrieval.
2. **ACL in the prompt** ("ignore docs you shouldn't see"). Authorization is a hard query predicate before ANN, on every hop.
3. **Inlining 80 joint encodes inside the generator prefill.** Rerank is a tool with its own RPM, timeout, and cache.
4. **Mixing BM25 unbounded scores with cosine without RRF.** Set alpha explicitly.
5. **No hop counter.** Official LangGraph agentic-RAG tutorial has no hop counter. RetryPolicy retries node exceptions, not "grader said irrelevant."
6. **HyDE on SKU/error-code lookups.** BM25 already wins; the hypothetical contaminates the dense neighborhood.
7. **CRAG Incorrect -> open web on confidential query.** That is exfil.
8. **Citing parent ID while quoting child from different version.**
9. **Stuffing pre-rerank k=50 into 128k.** Lost-in-the-middle.
10. **Falling back to parametric knowledge on an ACL-sensitive corpus when retrieve fails.**
11. **GraphRAG global on a factoid query.** Microsoft OSS is maintenance-mode research.
12. **Treating Cohere search-unit $2/1k as a first-party SKU** -- it is Bedrock/aggregator rate.

### RAGAS Health Benchmarks

| Metric | Healthy | Alert threshold |
| --- | --- | --- |
| Context recall | > 0.80 | < 0.70 |
| Context precision | > 0.70 | < 0.60 |
| Faithfulness | > 0.85 | < 0.80 |
| Answer relevance | > 0.80 | < 0.70 |

### Interview Closer

"The model never searches. I pre-filter ACL, hybrid-retrieve (RRF when score scales disagree), rerank as a tool (80->8), expand parents with copied ACL, cap rewrite at 3 in checkpointed state, and constrain cites to R (~$14.2/1k, not tens of dollars of uncapped hops). RAG is the world; memory is the relationship. Ingest flips an alias; it does not ride the query p99."

### Decision Framework: When to Use What

| Query type | Strategy | Cost | Latency |
| --- | --- | --- | --- |
| Chitchat / known fact | No retrieval (Adaptive-A) | $0 retrieval | <100ms |
| Single-fact lookup (SKU, error code) | Hybrid + rerank, one pass | ~$0.004-0.01 | 300-800ms |
| Multi-hop comparison | Agentic loop, 2-3 hops | ~$0.01-0.06 | 2-30s |
| Global theme ("top risks this quarter") | LazyGraphRAG / graph global | Expensive | Seconds |
| Citation-critical (legal, regulatory) | Quote-then-answer + NLI + WORM | ~$0.03-0.08 | 1.5-3s |
