# Module 07: Agentic RAG

> **Audience**: Principal Data Scientist (12+ YOE) preparing for Director/VP-level AI roles.
> **Scope**: End-to-end architecture of agentic retrieval-augmented generation -- query analysis, hybrid search, reranking, self-reflective retrieval, citation tracking, multi-hop reasoning, enterprise security, production code, and system design scenarios.
> **Pricing assumptions**: OpenAI text-embedding-3-small $0.02/1M tokens; text-embedding-3-large $0.13/1M; Voyage-4-large ~$0.12/1M; Cohere Rerank v3.5 $2/1K searches; Claude Sonnet 4 input $3/1M, output $15/1M; GPT-4o input $2.50/1M, output $10/1M. All as of mid-2026.
> **Key papers**: Self-RAG (Asai et al., ICLR 2024), CRAG (Yan et al., 2024), Adaptive-RAG (Jeong et al., NAACL 2024), IRCoT (Trivedi et al., 2023), FLARE (Jiang et al., EMNLP 2023), RAPTOR (Sarthi et al., ICLR 2024), CoRAG (arXiv:2501.14342), GraphRAG (Microsoft Research 2024), Contextual Retrieval (Anthropic 2024).

---

## 1. System Topology & Data Flow

### 1.1 Full Agentic RAG Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                              CONTROL PLANE                                       │
│                                                                                  │
│  ┌────────────────────┐  ┌─────────────────────┐  ┌──────────────────────────┐  │
│  │  Query Analyzer     │  │  Retrieval Router    │  │  Quality Evaluator       │  │
│  │                    │  │                      │  │                          │  │
│  │  - Complexity      │  │  - Simple → Advanced │  │  - CRAG-style scoring:   │  │
│  │    classifier      │  │    RAG (70% of Qs)   │  │    correct / incorrect / │  │
│  │    (A/B/C routing) │  │  - Complex → Agentic │  │    ambiguous             │  │
│  │  - Intent / domain │  │    loop (30%)         │  │  - Retrieval sufficiency │  │
│  │    detection       │  │  - Domain-specific   │  │    check per sub-query   │  │
│  │  - Query rewriting │  │    sub-index routing  │  │  - Re-retrieve trigger   │  │
│  │    (HyDE, step-    │  │    (eng, legal, HR,  │  │    on low confidence     │  │
│  │     back, multi-Q) │  │     sales)           │  │  - Web search fallback   │  │
│  └────────┬───────────┘  └──────────┬──────────┘  └─────────────┬────────────┘  │
│           │                         │                            │                │
│  ┌────────────────────┐  ┌─────────────────────┐  ┌──────────────────────────┐  │
│  │  Citation Tracker   │  │  Iteration Manager   │  │  Context Budget Manager  │  │
│  │                    │  │                      │  │                          │  │
│  │  - Inline citation │  │  - IRCoT reasoning   │  │  - Chunk count cap (15) │  │
│  │    generation      │  │    loop (max 8 hops) │  │  - Token budget per      │  │
│  │  - NLI entailment  │  │  - Sub-query fan-out │  │    stage                 │  │
│  │    verification    │  │    and merge          │  │  - Dedup by parent doc  │  │
│  │  - Quote-then-     │  │  - Termination:      │  │  - MMR diversity filter │  │
│  │    answer enforce  │  │    sufficiency or     │  │                          │  │
│  │  - Provenance log  │  │    max iterations    │  │                          │  │
│  └────────┬───────────┘  └──────────┬──────────┘  └─────────────┬────────────┘  │
│           │                         │                            │                │
├───────────┼─────────────────────────┼────────────────────────────┼────────────────┤
│           │              DATA PLANE │                            │                │
│           v                         v                            v                │
│  ┌────────────────────┐  ┌─────────────────────┐  ┌──────────────────────────┐  │
│  │  Embedding Pipeline │  │  Hybrid Search       │  │  Reranker                │  │
│  │                    │  │                      │  │                          │  │
│  │  - Model: Voyage-  │  │  - Dense: HNSW ANN  │  │  - Cross-encoder (BGE-  │  │
│  │    4-large or      │  │    over embeddings   │  │    v2) or Cohere Rerank │  │
│  │    Gemini Embed    │  │  - Sparse: BM25 over │  │    v3.5 (API)           │  │
│  │  - Contextual      │  │    full text + chunk │  │  - ColBERT late-inter-  │  │
│  │    preamble        │  │    context preamble  │  │    action for large-k   │  │
│  │    (Anthropic      │  │  - Metadata filters: │  │  - Input: top-100 from  │  │
│  │     method)        │  │    date, type, ACL   │  │    RRF fusion           │  │
│  │  - Parent-child    │  │  - RRF fusion (k=60) │  │  - Output: top-10       │  │
│  │    chunk indexing  │  │                      │  │    re-scored candidates │  │
│  └────────┬───────────┘  └──────────┬──────────┘  └─────────────┬────────────┘  │
│           │                         │                            │                │
│  ┌────────────────────┐  ┌─────────────────────┐  ┌──────────────────────────┐  │
│  │  Context Assembler  │  │  Generator           │  │  Output Guardrails       │  │
│  │                    │  │                      │  │                          │  │
│  │  - Parent-doc      │  │  - Quote-then-answer │  │  - PII regurgitation     │  │
│  │    expansion       │  │    prompting          │  │    scan                  │  │
│  │  - Chunk ordering  │  │  - Inline citation   │  │  - Toxicity filter       │  │
│  │    by relevance    │  │    enforcement        │  │  - Confidence threshold  │  │
│  │  - Overlap dedup   │  │  - Self-RAG reflect  │  │    ("insufficient        │  │
│  │  - System prompt   │  │    tokens (ISREL,    │  │     evidence" fallback) │  │
│  │    injection       │  │    ISSUP, ISUSE)     │  │  - NLI claim-citation    │  │
│  └────────────────────┘  └─────────────────────┘  │    verification          │  │
│                                                    └──────────────────────────┘  │
│                                                                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│                        PERSISTENCE LAYER                                         │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐    │
│  │  ┌──────────────────┐  ┌────────────────────┐  ┌─────────────────────┐  │    │
│  │  │ VECTOR STORE      │  │ DOCUMENT STORE      │  │ INDEX METADATA       │  │    │
│  │  │                  │  │                    │  │                     │  │    │
│  │  │ Child-chunk      │  │ Raw documents with │  │ - Embedding model   │  │    │
│  │  │ embeddings       │  │ full structure      │  │   version tag       │  │    │
│  │  │ (400-char child, │  │                    │  │ - Chunk→parent map  │  │    │
│  │  │  2000-char       │  │ Parent chunks with │  │ - Content hashes    │  │    │
│  │  │  parent)         │  │ contextual          │  │   (change detect)   │  │    │
│  │  │                  │  │ preambles          │  │ - ACL attributes    │  │    │
│  │  │ Qdrant / Weaviate│  │                    │  │ - Created/modified  │  │    │
│  │  │ / pgvector       │  │ PostgreSQL / S3    │  │   timestamps        │  │    │
│  │  └──────────────────┘  └────────────────────┘  └─────────────────────┘  │    │
│  │  ┌──────────────────┐  ┌────────────────────┐                           │    │
│  │  │ KNOWLEDGE GRAPH   │  │ CITATION LOG        │                           │    │
│  │  │ (optional)       │  │                    │                           │    │
│  │  │ Entity-relation  │  │ Query, retrieved   │                           │    │
│  │  │ graph for multi- │  │ doc IDs, generated │                           │    │
│  │  │ hop traversal    │  │ answer, NLI scores,│                           │    │
│  │  │ (GraphRAG /      │  │ user ID, timestamp │                           │    │
│  │  │  Neo4j)          │  │ Append-only,       │                           │    │
│  │  │                  │  │ 7-year retention   │                           │    │
│  │  └──────────────────┘  └────────────────────┘                           │    │
│  └──────────────────────────────────────────────────────────────────────────┘    │
│                                                                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│                        TOOL PROXIES                                              │
│                                                                                  │
│  ┌──────────────────┐  ┌────────────────────┐  ┌────────────────────────────┐  │
│  │  Search APIs       │  │  Knowledge Graph    │  │  Web Search Fallback      │  │
│  │                  │  │  Traversal           │  │                            │  │
│  │  - Confluence API │  │  - Entity lookup    │  │  - Triggered by CRAG on   │  │
│  │  - SharePoint     │  │  - 1-2 hop neighbor │  │    low-confidence          │  │
│  │    Graph API      │  │    expansion         │  │    retrieval               │  │
│  │  - Jira REST      │  │  - Community         │  │  - Tavily / Bing API      │  │
│  │  - Slack export   │  │    summary recall    │  │  - Result merged with     │  │
│  │  - CRM (SFDC)     │  │    (GraphRAG global  │  │    refined internal       │  │
│  │  - Code repos     │  │     query)           │  │    results                │  │
│  └──────────────────┘  └────────────────────┘  └────────────────────────────┘  │
│                                                                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│                        TELEMETRY / OBSERVABILITY                                 │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐    │
│  │  - Query-level traces: total latency, per-stage timing, iteration count │    │
│  │  - Retrieval quality: RAGAS faithfulness/recall per sampled query        │    │
│  │  - Citation integrity: NLI pass/fail rate, unsupported claim count      │    │
│  │  - Cost tracking: embedding calls, LLM calls (rewrite + generate +     │    │
│  │    reflect), reranker API calls, web search calls                        │    │
│  │  - Index health: staleness score, chunk count, ACL sync lag             │    │
│  │  - Agentic loop: avg iterations/query, termination reasons, fallback %  │    │
│  │  - Alerting: faithfulness < 0.80, context recall < 0.70, p95 > 10s     │    │
│  └──────────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative: Query to Grounded, Cited Response

**Step 1 -- Query Analysis (5-50ms CPU, 0-500ms if LLM rewrite)**:
User query arrives. The Query Analyzer runs a lightweight complexity classifier (a small fine-tuned LM or rule-based heuristic) to categorize the query: (A) simple factual -- route to parametric knowledge, no retrieval; (B) moderate -- single-pass advanced RAG; (C) complex multi-hop -- agentic retrieval loop. 70% of enterprise queries are category B. For categories B and C, the analyzer optionally rewrites the query (HyDE for broad open-domain, step-back for overly specific queries, multi-query decomposition for ambiguous/multi-faceted questions).

**Step 2 -- Retrieval Router (1-2ms)**:
The router dispatches to the appropriate pipeline. For category B, it sends the (possibly rewritten) query to hybrid search. For category C, it identifies the domain (engineering, legal, HR, sales) and routes to specialized sub-indexes. Multi-query decomposition yields 2-5 sub-queries that are each dispatched independently.

**Step 3 -- Hybrid Search (10-50ms)**:
Dense search (HNSW over child-chunk embeddings) and sparse search (BM25 over chunk text + contextual preambles) run in parallel. Metadata filters (date range, document type, ACL permissions for the requesting user) are applied during search, not post-hoc. Results are fused using Reciprocal Rank Fusion (k=60 default) to produce a ranked candidate list of ~100 documents.

**Step 4 -- Reranking (100-300ms)**:
A cross-encoder (BGE-v2 self-hosted or Cohere Rerank v3.5 API) jointly scores each (query, candidate) pair. The top-100 from RRF are reranked down to the top-10. Score calibration note: BGE-v2 relevant pairs score 0.55-0.75; Cohere relevant pairs score 0.85-0.95 -- thresholds are not portable between rerankers.

**Step 5 -- Context Assembly (1-5ms)**:
Child chunks in the top-10 are expanded to their parent chunks (2000-char parents provide the LLM with sufficient reasoning context). Deduplication removes overlapping parents. Chunks are ordered by relevance score. A token budget cap (typically 15 chunks or ~4000 tokens) prevents context overflow.

**Step 6 -- Quality Evaluation (CRAG gate, 50-200ms)**:
For agentic queries, a lightweight evaluator (T5-based or LLM call) scores retrieval quality. Three paths: (a) high confidence -- proceed to generation with refined context; (b) low confidence -- discard faulty retrievals, trigger web search fallback; (c) ambiguous -- merge refined internal results with web search results. This prevents the generator from producing answers from poor context.

**Step 7 -- Iterative Retrieval (agentic path only, 2-15s)**:
For multi-hop queries, the Iteration Manager runs an IRCoT-style loop: generate a reasoning step, use that step as a new retrieval query, evaluate the new results, repeat. Max 8 iterations, 15-chunk total cap. Each iteration re-enters the pipeline at Step 3. The loop terminates on either sufficiency (evaluator scores all sub-queries as answered) or max iterations.

**Step 8 -- Generation with Citations (200-2000ms)**:
The generator produces the answer using quote-then-answer prompting: first extract exact quotes from retrieved context, then synthesize an answer using only those quotes. Self-RAG reflection tokens (ISREL, ISSUP, ISUSE) evaluate relevance, support, and utility inline. Each claim is tagged with its source [DocID, Section, Paragraph].

**Step 9 -- Citation Verification (50-200ms)**:
An NLI model checks each (claim, cited_chunk) pair for entailment. Non-entailed citations are flagged. If any claim is unsupported, the pipeline either regenerates that claim or returns "insufficient evidence" for that portion.

**Step 10 -- Output Guardrails and Logging (5-20ms)**:
PII regurgitation scanner and toxicity filter check the response. The complete audit trail (query, retrieved doc IDs, retrieved text, generated answer, NLI scores, user ID, timestamp) is appended to the immutable citation log. Response is returned to the user.

**End-to-end latency**: Category B queries: 300-800ms. Category C (agentic): 2-30s depending on hop count and retrieval retries.

---

## 2. Core Mechanics & Algorithms

### 2.1 Query Rewriting Techniques

**HyDE (Hypothetical Document Embeddings)**

The core problem: short user queries (5-15 tokens) produce embeddings that are semantically distant from longer document chunks (200-500 tokens). HyDE bridges this gap by generating a hypothetical answer document (50-200 tokens) and embedding that instead.

Mechanism: User query -> LLM generates a plausible (possibly factually incorrect) answer document -> embed the hypothetical document -> use that embedding for vector search. The hypothetical document captures relevant vocabulary, phrasing, and thematic content that align with how real documents are written.

Trade-offs:
- Adds one LLM inference call per query (~200-500ms latency, ~$0.001-0.003 cost).
- Works best for broad, open-domain retrieval where the semantic gap is widest.
- May struggle with narrow domain-specific queries where exact terminology matters more than semantic similarity.
- Unlike contextual retrieval (which adds context at ingestion time, once), HyDE adds latency per query.

**Step-Back Prompting**

Generates a broader, more abstract version of the query to retrieve high-level context. Example: "What is the refresh rate of iPhone 13 Pro Max?" becomes "What are the technical specifications of iPhone 13 Pro Max?" The broader query retrieves the spec sheet, which contains the specific answer.

Best for: Queries that are too specific for vector search to match against broader documents. Queries requiring conceptual understanding or background knowledge before answering.

**Multi-Query Decomposition**

The LLM rewrites the original query from 3-5 different perspectives. Each variant retrieves results independently, and results are merged using RRF. This improves both recall (different phrasings find different relevant documents) and precision (consensus across variants filters noise).

Sub-query decomposition is a stronger variant: complex questions are broken into independent sub-questions, each solved separately, then synthesized. Example: "Compare the regulatory requirements for AI in healthcare between the EU and US" -> Sub-Q1: "EU AI Act requirements for healthcare AI" + Sub-Q2: "US FDA regulations for AI medical devices" + Sub-Q3: "Comparison framework for AI regulation across jurisdictions."

**Decision framework**: Do not add a transformation step until you can name the retrieval failure mode it fixes and the latency cost you accept. Query rewriting is the cheapest (~50ms CPU). Step-back adds one LLM call. Multi-query adds 3-5 parallel LLM calls plus RRF fusion.

### 2.2 Hybrid Search and RRF Fusion

**Why hybrid**: BM25 (sparse) excels at exact-match queries (product codes "ERR-502-BAD-GATEWAY", entity names, regulation numbers "SEC Rule 10b-5"). Dense vector retrieval handles paraphrase and conceptual similarity but underweights rare exact terms. Neither alone is sufficient for enterprise workloads.

**Score incompatibility problem**: BM25 produces unbounded positive integers (a score of 15.7 means nothing in absolute terms). Cosine similarity is bounded in [-1, 1]. Normalizing both to [0, 1] is unreliable because score distributions have completely different shapes across queries and corpora.

**Reciprocal Rank Fusion (RRF)** (Cormack et al., SIGIR 2009):

```
RRF_score(d) = SUM over all rankers r: 1 / (k + rank_r(d))
```

Where k is a smoothing constant (default 60). RRF discards scores entirely and works on rank position. Properties:
- Normalization-free: no need to calibrate score distributions across methods.
- Scalable: works on billion-scale sharded indices where each shard produces its own ranking.
- Finds consensus: documents ranked highly by multiple methods float to the top.

**Tuning k**: k=60 is the original paper default for web search. For short corpora (<100 docs), use k=10-30 to amplify rank differences. For noisy corpora with many irrelevant candidates, larger k smooths results. Treat as a hyperparameter tuned on held-out queries.

**Benchmark results (WANDS e-commerce)**:
- BM25-only: NDCG 0.6983
- Dense-only: NDCG 0.6953
- Tuned hybrid (BM25 + dense + RRF): NDCG 0.7497 (+7.4%)
- Hybrid + Cohere Rerank: Recall@5 0.816 (+17.4% over hybrid RRF alone, +39.0% over dense-only)

### 2.3 Reranking: Cross-Encoders, ColBERT, Cohere Rerank

**Cross-encoder reranking**: Takes each (query, document) pair jointly through a single transformer forward pass to produce a relevance score. Quality lift: +5 to +15 NDCG@10 points across MTEB/BEIR benchmarks. Cost: one forward pass per candidate, so only feasible on top-30 to top-100 candidates from first-stage retrieval.

**Cohere Rerank v3.5**: Closed API, $2 per 1K searches (one query against up to 100 documents). P50 latency ~220ms. 4096-token context per document. 100+ language support. The "just works" choice for teams without GPU infra.

**ColBERT (late interaction)**: Encodes queries and documents separately into per-token embeddings, then scores with MaxSim (maximum similarity between each query token and all document tokens) at query time. More expressive than single-vector cosine, far cheaper than full cross-encoder because document representations are precomputed and cached. Latency: tens of ms on 100 docs on a single GPU. Best when corpus is large, latency budget is tight, and k > 200.

**Score calibration warning**: BGE-v2 normalized scores land in [0, 1] with relevant pairs at 0.55-0.75. Cohere scores are [0, 1] with relevant pairs at 0.85-0.95. ColBERT MaxSim is unbounded (relevant pair might score 4 or 18). A single relevance threshold cannot be used across rerankers -- calibrate per model.

**Production pipeline**: BM25 top-50 + Dense top-50 -> RRF merge to top-100 -> Cross-encoder rerank to top-10 -> LLM generation. A common pitfall: reranking too few candidates. Retrieving top-5 and reranking to top-3 barely shuffles the deck. Use N=30-50 minimum input to the reranker.

### 2.4 Parent Document / Hierarchical Chunking

**Small-to-big retrieval**: Embed small "child" chunks (50-200 tokens, ~400 characters) for precise matching, but replace them with larger "parent" chunks (500-1500 tokens, ~2000 characters) before passing to the LLM. This decouples two competing requirements: retrieval granularity (small = clean, focused embeddings) vs. generation granularity (large = sufficient reasoning context).

Why small chunks embed better: Embeddings degrade on long, topic-mixed text. A 2000-character chunk spanning two topics produces a blurred embedding that matches neither topic well. A 400-character child chunk is topically focused and produces a precise embedding.

Why the LLM needs larger chunks: Small chunks alone give the LLM tunnel vision -- it sees a sentence about a policy clause but misses the surrounding context needed to interpret it correctly.

**Two variants**:
1. Parent-child chunk retrieval: Fetch child chunks by vector similarity, follow parent IDs, deduplicate by parent, return parent chunks.
2. Sentence window retrieval: Fetch a single sentence, return a window of N surrounding sentences.

**Implementation**: Embed and index children only. At query time, retrieve top-k children, deduplicate by parent ID, send parents to LLM. LlamaIndex provides AutoMergingRetriever. LangChain's ParentDocumentRetriever was deprecated and removed in v0.2.0 (Feb 2024).

### 2.5 Adaptive Retrieval

**Adaptive-RAG** (Jeong et al., NAACL 2024): A small classifier predicts query complexity and routes to the appropriate retrieval strategy:
- Level A (simple): Use parametric knowledge, no retrieval. "What is the capital of France?"
- Level B (moderate): Single-step hybrid RAG. "What were our Q3 revenue numbers?"
- Level C (complex): Multi-step iterative RAG. "How did our pricing strategy changes in 2025 affect churn rates across enterprise vs SMB segments?"

The classifier is a smaller LM trained on automatically collected labels from actual model outcomes (did the simple path produce a correct answer?).

**Why this matters**: Indiscriminate retrieval is actively harmful. Research on the Atlas model found that retrieved context can override correct parametric knowledge -- the model relies more on retrieved text even when its own knowledge was right. Running every query through agentic RAG wastes ~10x cost and adds 5s latency on queries that need neither.

**Practical recommendation**: A query complexity classifier at the front door reduces costs by ~40% and latency by ~35% compared to routing every query through the full agentic pipeline.

### 2.6 Self-RAG: Self-Reflection Tokens

**Self-RAG** (Asai et al., ICLR 2024): Trains a single LM to adaptively retrieve passages on-demand and generate/reflect using special reflection tokens added to the model's vocabulary.

**Four reflection tokens**:
- **Retrieve**: Should I retrieve information right now? (yes/no/continue)
- **ISREL**: Is this retrieved passage relevant to the query? (relevant/irrelevant)
- **ISSUP**: Is the generated claim supported by the evidence? (fully_supported/partially_supported/no_support)
- **ISUSE**: Is the overall response useful? (5-point scale)

**Three-stage process per generation segment**:
1. Decode the Retrieve token. If "no," continue generating from parametric knowledge.
2. If "yes," retrieve top passages. Generate ISREL tokens evaluating each passage. Discard irrelevant passages.
3. Generate the response conditioned on relevant passages. Evaluate ISSUP (is each claim supported?) and ISUSE (overall utility).

**Training pipeline**: A critic model (trained on GPT-4 annotations) inserts reflection tokens offline into training data. The generator is then trained with standard next-token prediction on this augmented corpus. No critic model is needed at inference -- the reflection tokens are generated as part of normal autoregressive decoding.

**Results**: Self-RAG 13B outperforms ChatGPT and retrieval-augmented Llama2-chat. PopQA: 55.8% accuracy (vs Llama2-13B at 14.7%). PubHealth: 74.5% (vs Alpaca-13B at 51.1%). ARC-Challenge: 73.1% (vs Alpaca-13B at 57.6%).

### 2.7 Corrective RAG (CRAG)

**CRAG** (Yan et al., 2024): Adds a lightweight retrieval evaluator (T5-based) that scores retrieved document quality and triggers corrective actions. Plug-and-play design that couples with any existing RAG pipeline.

**Three action paths**:
- **Correct (high confidence)**: Refine retrieved documents using decompose-then-recompose -- split into sentences, keep only answer-relevant pieces, reassemble. Strips noise from otherwise good retrievals.
- **Incorrect (low confidence)**: Discard faulty retrievals entirely. Trigger web search fallback (Tavily, Bing API) to find external evidence.
- **Ambiguous (uncertain)**: Combine refined internal retrieval with web search results.

**Key property**: Tested with LLaMA2 and SelfRAG-LLaMA2-7b generators. More robust than baselines when retrieval quality degrades -- even with fewer correct documents, CRAG's performance drops less steeply due to web search fallback and careful noise filtering.

### 2.8 Multi-Hop Reasoning: IRCoT, FLARE, CoRAG

**IRCoT (Interleaving Retrieval with Chain-of-Thought)** (Trivedi et al., 2023):
Alternates between reasoning and retrieval steps. After generating each reasoning step, uses that step as a query for additional retrieval. Loop runs up to 8 steps, capping at 15 paragraphs. No training required -- entirely prompting-based.

Results on HotpotQA: +11.3 retrieval recall points and +7.1 QA F1 points over one-step retrieval (GPT-3). On 2WikiMultihopQA: +22.6 recall and +13.2 F1. Flan-T5-XL (3B) with IRCoT outperforms GPT-3 (175B) with one-step retrieval. Reduces factual errors in CoT by 50% on HotpotQA.

**FLARE (Forward-Looking Active Retrieval)** (Jiang et al., EMNLP 2023):
Generates the next sentence, checks token probabilities. If any token falls below confidence threshold theta, triggers retrieval using the generated sentence as query, then regenerates. Achieves 51.0 EM on 2WikiMultihopQA vs 39.4 for single-retrieval.

Limitation: Depends on model calibration. Instruction-tuned and RLHF models are often overconfident, making confidence thresholds unreliable. This limits FLARE's practical applicability with modern chat models.

**CoRAG (Chain-of-Retrieval Augmented Generation)** (arXiv:2501.14342):
Uses rejection sampling to augment RAG datasets with intermediate retrieval chains, then fine-tunes open-source LMs with standard next-token prediction. Supports greedy decoding, best-of-N sampling, and tree search at inference for exploring multiple retrieval paths.

**Common challenge across all multi-hop methods**: As retrieval iterations increase, generated queries can drift from the correct reasoning path, and irrelevant information accumulates. Latency scales linearly with reasoning depth.

### 2.9 Citation Tracking and Attribution

**The scale of the problem**: Over 95% of answers from tested open-source LLMs contain at least one sentence without any attribution. 57% of citations in a RAG-optimized model show unfaithful behavior (Wallat et al.). Citation-shaped hallucinations -- answers that appear grounded because they include source markers while the actual claim is unsupported -- are more dangerous than plain hallucinations because users lower their guard.

**Key techniques**:

1. **Inline citation generation**: Citations generated during answer synthesis (not post-hoc) are a necessary condition for faithfulness. Post-hoc citation attachment is unreliable because the model has already committed to claims without evidence grounding.

2. **Quote-then-answer**: The model extracts relevant quotes from context first, then synthesizes an answer using only those quotes. This commits the model to evidence before generating claims, reducing hallucination.

3. **NLI-based verification**: A Natural Language Inference model checks whether each cited claim is entailed by its source. Classifies (premise=cited chunk, hypothesis=claim) as entailment/contradiction/neutral. Non-entailed citations are flagged or stripped.

4. **Token-level attribution (UAF)**: Aggregates token-level provenance scores into grounding confidence per claim. Claims below threshold are flagged. Cuts fabricated claims by ~42% vs baseline RAG.

5. **FACTUM**: Detects citation hallucination by analyzing model internal activation pathways. State-of-the-art for mechanistic citation verification but requires model-internal access.

**Production benchmark**: Even with perfect retrieval, GPT-4-class models fabricate details in 8-15% of responses. Citation enforcement (quote-then-answer + NLI verification) reduces this to under 3%.

### 2.10 GraphRAG, RAPTOR, and Contextual Retrieval

**GraphRAG** (Microsoft Research, April 2024):

Instead of indexing raw document chunks, extracts entities and relationships to build a knowledge graph, then uses graph structure for retrieval. Architecture has three components: (1) Graph construction -- LLM extracts entity-relationship triples from text; (2) Community detection -- hierarchical clustering via Leiden algorithm; (3) Community summarization -- natural language summaries per community.

Two query modes: Local Query (specific factual questions -- extract entities, find in graph, traverse 1-2 hop neighbors, collect context) and Global Query (broad synthesis -- use community summaries for holistic answers).

Why graphs help multi-hop: With flat document chunks, retrieving all pieces of a 3-hop reasoning chain (A->B->C) in top-k is statistically unlikely. A graph lets you start from a matched entity, traverse to related entities, and pull context from each step.

**Production caveat**: Entity extraction quality is the hard part. The paper assumes extraction accuracy above 85%; below that, the knowledge graph introduces noise that degrades performance relative to simple RAG. The project is now largely in maintenance mode (2026), with technology available through Microsoft Discovery.

**RAPTOR (Recursive Abstractive Processing for Tree-Organized Retrieval)** (Sarthi et al., ICLR 2024):

Recursively embeds, clusters, and summarizes text chunks, building a tree with differing levels of abstraction from bottom up. At inference, retrieves from this tree to integrate information at multiple granularities.

How the tree is built: Text chunks are embedded with SBERT (multi-qa-mpnet-base-cos-v1), clustered using soft clustering, and each cluster gets an LLM-generated summary as a parent node. This process recurses up the tree. On QuALITY benchmark, RAPTOR + GPT-4 improves best performance by 20% absolute accuracy. Tree construction cost scales linearly with document length.

**Contextual Retrieval** (Anthropic, September 2024):

Prepends a 50-100 token explanatory preamble to every chunk before embedding and BM25 indexing. An LLM generates the context using the full document as reference, capturing document subject, section information, entities, and dates that were separated from the chunk during splitting.

Results: Cut retrieval failures by 49%. Combined with reranking: 67% reduction. One-time ingestion cost of $1.02 per million document tokens (assuming 800-token chunks, 8k-token documents, 50-token instructions, 100 tokens of context per chunk via prompt caching).

Key advantage over HyDE: Contextualization happens once at ingestion, not per query. No added query-time latency.

**Recommendation**: Always pair contextual embeddings with contextual BM25. Add reranking if latency tolerance allows. Use 20 chunks for best retrieval results.

---

## 3. Token Economics & NFR Analysis

### 3.1 Per-Query Cost Breakdown

Total cost per RAG answer = Embedding (amortized) + Search infrastructure + Reranking + LLM generation.

**Component-level costs for a typical query (8 retrieved chunks, ~250 token answer)**:

| Component | Cost per Query | Notes |
|-----------|---------------|-------|
| Query embedding | ~$0.0000004 | 20 tokens at $0.02/1M (text-embedding-3-small) |
| Vector search (self-managed) | ~$0.0005 | Amortized infra cost |
| BM25 search | ~$0.0002 | Amortized infra cost |
| RRF fusion | negligible | CPU-only, <1ms |
| Reranking (Cohere v3.5) | $0.002 | $2/1K searches |
| LLM generation (GPT-4o) | ~$0.008 | ~1800 input + 250 output tokens |
| **Total (advanced RAG)** | **~$0.004-0.01** | |

**Agentic RAG multiplier**: Each retrieval iteration adds another search + rerank + partial generation cycle. A 3-hop query costs roughly 3-5x the single-pass cost. With query rewriting (HyDE), add ~$0.001-0.003 per rewrite call.

| Configuration | Cost/Query | Latency | Quality vs Baseline |
|--------------|-----------|---------|---------------------|
| Naive RAG (dense only, no rerank) | ~$0.001 | 200-500ms | Baseline |
| Hybrid (dense + BM25 + RRF) | ~$0.002 | 250-550ms | +7-15% NDCG |
| Hybrid + Rerank | ~$0.004 | 400-800ms | +17-25% Recall@5 |
| Agentic RAG (multi-step, 3 iterations avg) | ~$0.01-0.06 | 2-30s | +33-52% accuracy |

**Monthly cost at scale**:
- 1,000 queries/day, hybrid + rerank: ~$120-300/month
- 5,000 queries/day, blended (70% simple, 30% agentic): ~$1,500-4,500/month
- 10,000 queries/day, full agentic: ~$3,000-18,000/month

**Hidden cost -- retrieval retries**: When retrieval quality is poor, the agentic loop retries (reformulated query, another fetch, another rerank). Each retry multiplies token cost invisibly. Hash-based change detection for incremental index updates reduces daily embedding spend by ~100x (500 calls instead of 50,000 for a 50K corpus with 1% daily change rate).

### 3.2 Embedding Model Cost-Quality Tradeoffs

| Model | Cost per 1M Tokens | MTEB Score | Best For |
|-------|-------------------|------------|----------|
| text-embedding-3-small | ~$0.02 | ~62 | Budget deployments, prototyping |
| text-embedding-3-large | ~$0.13 | 64.6 | Production baseline (6.5x cost for ~2-3 MTEB points) |
| Voyage-4-large | ~$0.12 | Best retrieval quality API | High-stakes retrieval |
| Gemini Embedding 001 | ~$0.15 | Best all-rounder | Multimodal (text, image, video, audio) |
| Qwen3-Embedding-0.6B | Self-hosted | 64.34 | Self-hosted, latency-sensitive |
| KaLM-Gemma3-12B | Self-hosted | 72.32 | Current MTEB leader, requires GPU |

**Corpus embedding costs**:
- 10K documents (~5M tokens): ~$0.10-0.75 one-time. Trivial.
- 1M documents (~500M tokens): ~$10-75 one-time + $70-400/month hosting.
- 10M documents (~5B tokens): ~$100-750 one-time + $400-2,000/month hosting.

**Evolution note**: Pre-2024 embedding models were small encoder-only transformers (BERT/RoBERTa, <560M params, ~60 MTEB). E5-Mistral proved decoder-only LLMs make better embedding backbones. Dense retrieval now consistently outperforms BM25 by 15-25% on BEIR. Hybrid retrieval still adds 2-5% on top. But benchmark leaders may not be best for your domain -- always validate on held-out domain data.

### 3.3 Latency SLA Targets

Retrieval takes ~35% of time-to-first-token in production systems, against folk wisdom that generation dominates. The expensive stage (generation, in dollars) and the slow stage (retrieval + reranking, in wall-clock) are often different.

**Stage-level latency budget**:

| Stage | P50 Latency | P95 Latency | Budget Allocation |
|-------|-------------|-------------|-------------------|
| Query embedding | 5-20ms | 30ms | 2% |
| Vector search (HNSW, 1M vectors) | 2-10ms | 25ms | 3% |
| BM25 search | 5-15ms | 30ms | 3% |
| RRF fusion | <1ms | <2ms | <1% |
| Cross-encoder rerank (100 docs) | 100-300ms | 500ms | 35% |
| Context assembly | 1-5ms | 10ms | 1% |
| LLM generation (TTFT) | 200-800ms | 2000ms | 55% |
| **Total (single-pass)** | **350-800ms** | **1.5-2.5s** | **100%** |

**Reranking trade-off**: One case study showed accuracy jumping from 73% to 91% with cross-encoder reranking, but added 300ms latency. Another chatbot had worse user experience because of the delay. ColBERT (tens of ms on 100 docs) is the compromise when latency budget is tight.

**Agentic RAG latency**: Re-querying the retriever every 4 generated tokens pushes end-to-end latency to ~30 seconds, with retrieval and re-prefill eating 81% of total time. This is why the query complexity classifier matters -- routing simple queries through this path wastes 5-25 seconds.

### 3.4 Quality Metrics: RAGAS Framework

**RAGAS** (Retrieval-Augmented Generation Assessment, EACL 2024): Open-source framework processing 5M+ evaluations/month used by AWS, Microsoft, Databricks, Moody's.

**Four core metrics** (all 0-1, higher is better):

1. **Faithfulness** (generation quality): LLM-as-judge breaks the answer into atomic claims and checks each against retrieved context. Does NOT require ground truth. Most important single metric.

2. **Answer Relevancy** (generation quality): Reverse-generation approach -- judge generates hypothetical questions the answer could answer, measures cosine similarity to the actual question. Detects tangential or off-topic answers.

3. **Context Precision** (retrieval quality): Are relevant chunks ranked highly? Measures ordering quality of retrieved results.

4. **Context Recall** (retrieval quality): Does retrieved context contain all information needed? Only metric requiring a ground-truth reference answer.

**Healthy production benchmarks**:
- Context recall > 0.80
- Context precision > 0.70
- Faithfulness > 0.85
- Answer relevance > 0.80

**Diagnostic patterns**:

| Pattern | Root Cause | Fix |
|---------|-----------|-----|
| High context recall + Low faithfulness | Right chunks retrieved, model ignoring/distorting them | Improve prompt, switch model, add citation enforcement |
| Low context recall + High faithfulness | Model is faithful to what it has, but retrieval is missing key info | Improve chunking, add reranking, expand retrieval k |
| Low answer relevancy + High faithfulness | Retrieved context does not address the actual question | Fix query rewriting, improve index coverage |
| High faithfulness + Low answer relevancy | Retrieval problem disguised as generation problem | Route to different sub-index, add domain routing |

**Additional metrics for agentic RAG**: Tool Call Accuracy, Agent Goal Accuracy, Topic Adherence.

### 3.5 Benchmark Context: MTEB / BEIR

**MTEB** (Massive Text Embedding Benchmark): 56+ tasks across retrieval, clustering, classification, reranking, STS. Superset of BEIR. **BEIR**: 18+ zero-shot retrieval datasets spanning scientific papers, financial docs, COVID-19 research. Key metric: NDCG@10.

**Top models (2024-2026)**:

| Model | MTEB Score | Parameters | Notes |
|-------|-----------|------------|-------|
| KaLM-Gemma3-12B | 72.32 | 11.76B | Current leader, requires GPU |
| NV-Embed (NVIDIA) | 69.32 | -- | 2024 record |
| Gemini Embedding 2 | 68.32 | -- | Multimodal |
| Cohere embed-v4 | 65.2 | -- | |
| OpenAI text-3-large | 64.6 | -- | |
| Qwen3-Embedding-0.6B | 64.34 | 600M | Best small model |
| BGE-M3 | 63.0 | -- | |

---

## 4. Distributed Resilience & Security

### 4.1 Vector DB Scaling

**Market context**: Global vector database market valued at ~EUR 1.8B in 2024, growing 25%+ annually. RAG is the primary adoption driver.

**Vendor comparison for production RAG**:

| Vector DB | QPS (1M vectors) | P50 Latency | Scaling Ceiling | Differentiator |
|-----------|-------------------|-------------|-----------------|----------------|
| Pinecone (serverless) | 150 | ~1ms | Billions (auto) | Zero-ops, auto-shard, 3-5x cost premium |
| Qdrant | 1,238 | 3.5ms | 100M+ (manual) | Best latency self-hosted, complex metadata filtering |
| Weaviate | -- | -- | 100M+ (multi-node) | Native hybrid search (BM25 + dense), GraphQL |
| Milvus/Zilliz | -- | -- | Billions | Purpose-built for 100M+ vectors |
| pgvector | 141 | 8ms | ~10M (practical) | Runs in existing PostgreSQL, requires HNSW tuning at scale |

**At 1M vectors**: All major DBs hit 95%+ recall with defaults. **At 100M vectors**: Pinecone and Weaviate maintain recall without tuning; pgvector requires significant HNSW parameter optimization.

**Replication strategies**:
- **Synchronous**: Strong consistency, higher write latency. Use for financial/compliance workloads where stale reads are unacceptable.
- **Asynchronous**: Higher availability and performance, temporary stale reads possible. Acceptable for most enterprise Q&A workloads.
- **Multi-region**: Latency reduction for globally distributed users and disaster recovery.

### 4.2 Ingestion Pipelines at Scale

**Critical finding**: 80% of RAG failures trace back to ingestion and chunking, not the LLM. Most teams discover this after weeks of tuning prompts while retrieval quietly returns wrong context.

**Parsing**: Must preserve reading order, tables, headings, and metadata. When a parser drops table boundaries, merges columns, or misorders sections, retrieval returns plausible but incorrect passages. Microsoft Azure AI Foundry (July 2026) introduced boundary-aware chunking API with automatic deduplication. Early adopters report 40% fewer retrieval artifacts vs LangChain RecursiveCharacterTextSplitter.

**Chunking economics**: Smaller or proposition-based chunking creates 3-5x more vectors than recursive splitting. More vectors = more embedding API calls + more storage + more index overhead + more retrieval compute. Chunking is an economic decision, not just an IR decision.

**Embedding throughput**: 10M documents at 500 tokens/chunk = 5B tokens. Parallelize across multiple API keys or GPU nodes, processing in batches of 100-1000 items. This constrains initial deployment timelines -- a 10M document corpus can take 12-48 hours to embed even with parallelization.

**Required metadata per chunk**: Stable document ID, stable chunk ID (for deterministic update/delete), source URI/path (auditability), created/modified timestamps (freshness management), permission attributes (retrieval-time ACL filtering), parent chunk ID (for parent-child retrieval).

### 4.3 Index Management: Refresh and Staleness

**Incremental sync (production standard)**: Only reprocess changed documents. Requires a change detector using last-modified timestamps when trustworthy, content hashing (SHA-256) as fallback. Hash-based detection reduces daily embedding spend by ~100x for corpora with low daily change rates.

**Orchestration patterns**:
- Nightly batch job checking document hashes for most pipelines.
- Event-driven ingestion (S3 event notifications, webhooks) for real-time sources.
- Tools: Apache Airflow (multi-step workflows), AWS Step Functions (serverless), SageMaker Pipelines.

**Deletion handling**: When source documents are deleted or access is revoked, corresponding chunks must be removed from the index. Stale embeddings from deleted SaaS records are a documented data leakage vector.

**Embedding model staleness**: Embedding model generations move every 6-12 months with 10-20 point MTEB lifts. An index built on a 2024-era model is one or two generations behind by 2026. **Staleness test**: Run a measured eval on 50 hand-labeled queries against a current-generation model. If recall@10 lifts by 5+ points, re-embed the entire corpus.

**Version-tagging**: Every index must be tagged with the embedding model used. Mixing embeddings from different models in the same index makes dot-product scoring meaningless.

### 4.4 Enterprise Security

**Document-Level Access Control (RBAC/ABAC)**

In most enterprise RAG deployments, the retrieval engine operates without awareness of user permissions. The security gap is in the retrieval engine, not the LLM. OWASP LLM Top 10 (2025): Sensitive Information Disclosure jumped from number 6 to number 2. Vector and Embedding Weaknesses added as new category LLM08.

**Implementation**: Capture ACLs (owner, department, user/group entitlements, classification level, expiration) at ingestion time. Enforce as primary retrieval filter -- when vector search executes, the DB must honor the querying user's access rights. If a user cannot view a document in the source CRM, the RAG system must not retrieve it.

**Revocation propagation**: Build change detection so access revocations propagate to the index quickly. Delayed revocation is a common leakage avenue. Event-driven sync from the identity provider is preferred over periodic polling.

**RAG Poisoning Attacks and Defenses**

**Attack vector**: Attacker injects malicious content into enterprise knowledge base. Does not need access to AI model, vector store, or retrieval infrastructure -- only write access to any document source the KB ingests from (a Confluence page, a SharePoint document, a Slack message in an indexed channel).

**CRCP (Chunk-aware and Rerank-Consistent Poisoning)**: Advanced adversarial framework that jointly optimizes retrieval relevance, reranker consistency, and chunk-boundary robustness to generate locally self-contained adversarial passages. Retrieval granularity mismatch is a key factor -- document-level adversarial signals fragment during chunking, so CRCP crafts chunk-boundary-aware payloads.

**Defenses**:
- Source provenance validation at ingestion (reject unsigned/unverified documents).
- Anomaly detection on new chunks (semantic outlier detection against corpus).
- Contributor reputation scoring.
- Periodic content integrity audits.

**PII in Retrieved Documents**

**Data minimization at ingestion**: Every document entering a RAG system should be treated as potentially sensitive until sanitized. Remove or pseudonymize PII (names, emails, SSNs, IDs). Detect and strip encoded content (base64 payloads). Tools: Amazon Macie, Microsoft Presidio for automated PII detection and redaction.

**Embedding inversion attacks**: Vectors are not inherently secure. Sophisticated attacks can reverse-engineer vectors to reconstruct original sensitive text. Do not treat embedding as anonymization.

**Output guardrails**: Deploy output filters to evaluate generated responses for regurgitated PII, toxic content, or anomalous behavior before delivery to users. Do not implicitly trust LLM output.

**Data Provenance and Compliance**

**Comprehensive audit logging**: Every RAG event (ingestion, retrieval, modification, deletion) logged in real time with dual attribution (system actor + human actor). Critical for compliance, governance, and incident investigation.

**EU AI Act Article 12** (enforcement deadline: August 2, 2026): Organizations operating RAG in high-risk AI categories must maintain logs of system inputs/outputs for post-hoc auditing. Regulators will ask: which documents were retrieved, for which queries, with what access controls in place. Penalties: up to EUR 35M or 7% of global annual revenue.

**GDPR Article 28**: Documents surfaced through RAG oversharing that include personal data processed without valid Data Processing Agreement trigger unauthorized processing obligations with 72-hour breach notification requirements.

**Zero-trust posture**: Security must be layered across ingestion (sanitization, ACLs), retrieval (permission filtering), and generation (output guardrails). No single layer is sufficient. Cloud provider solutions: AWS Bedrock (data redaction at storage + RBAC), Google Cloud Sensitive Data Protection (PII classification/redaction before chunking + Vertex AI IAM integration).

---

## 5. Production Enterprise Code

### 5.1 Query Analysis and Rewriting (HyDE, Multi-Query)

```python
"""
Query analysis and rewriting module for agentic RAG.
Implements HyDE, step-back, and multi-query decomposition.
"""

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from openai import AsyncOpenAI


class QueryComplexity(Enum):
    SIMPLE = "A"       # Parametric knowledge sufficient, no retrieval
    MODERATE = "B"     # Single-pass hybrid RAG
    COMPLEX = "C"      # Multi-step agentic RAG


@dataclass
class AnalyzedQuery:
    original: str
    complexity: QueryComplexity
    rewritten_queries: list[str]
    domain: Optional[str]  # engineering, legal, hr, sales, etc.


COMPLEXITY_CLASSIFIER_PROMPT = """Classify this query's complexity for a RAG system.

- A: Simple factual question answerable from general knowledge (no retrieval needed)
- B: Moderate question requiring single-pass retrieval from a knowledge base
- C: Complex question requiring multi-hop reasoning, comparison across sources, or synthesis

Query: {query}

Respond with exactly one letter: A, B, or C"""

HYDE_PROMPT = """Write a detailed passage (100-200 words) that would answer this question.
The passage should read like an excerpt from a relevant document.
Do not hedge or say "I don't know" -- write a plausible answer even if uncertain.

Question: {query}

Passage:"""

MULTI_QUERY_PROMPT = """Generate {n} alternative versions of this question.
Each version should approach the question from a different angle or use
different terminology, to maximize the chance of finding relevant documents.

Original question: {query}

Return exactly {n} questions, one per line, no numbering or bullets."""

STEP_BACK_PROMPT = """Given this specific question, generate a broader, more general
question that would retrieve useful background context.

Specific question: {query}

Broader question:"""


class QueryAnalyzer:
    def __init__(self, client: AsyncOpenAI, model: str = "gpt-4o"):
        self.client = client
        self.model = model

    async def classify_complexity(self, query: str) -> QueryComplexity:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": COMPLEXITY_CLASSIFIER_PROMPT.format(query=query)}],
            max_tokens=1,
            temperature=0.0,
        )
        label = response.choices[0].message.content.strip().upper()
        return QueryComplexity(label) if label in ("A", "B", "C") else QueryComplexity.MODERATE

    async def hyde_rewrite(self, query: str) -> str:
        """Generate a hypothetical document embedding for the query."""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": HYDE_PROMPT.format(query=query)}],
            max_tokens=300,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()

    async def multi_query_rewrite(self, query: str, n: int = 3) -> list[str]:
        """Generate n alternative query formulations."""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": MULTI_QUERY_PROMPT.format(query=query, n=n)}],
            max_tokens=300,
            temperature=0.7,
        )
        lines = [line.strip() for line in response.choices[0].message.content.strip().split("\n") if line.strip()]
        return lines[:n]

    async def step_back_rewrite(self, query: str) -> str:
        """Generate a broader version of the query for context retrieval."""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": STEP_BACK_PROMPT.format(query=query)}],
            max_tokens=100,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()

    async def analyze(self, query: str) -> AnalyzedQuery:
        """Full query analysis: classify complexity and apply appropriate rewriting."""
        complexity = await self.classify_complexity(query)

        if complexity == QueryComplexity.SIMPLE:
            return AnalyzedQuery(
                original=query, complexity=complexity,
                rewritten_queries=[query], domain=None,
            )

        if complexity == QueryComplexity.MODERATE:
            hyde_doc = await self.hyde_rewrite(query)
            return AnalyzedQuery(
                original=query, complexity=complexity,
                rewritten_queries=[query, hyde_doc], domain=None,
            )

        # Complex: multi-query decomposition + step-back
        sub_queries, step_back = await asyncio.gather(
            self.multi_query_rewrite(query, n=3),
            self.step_back_rewrite(query),
        )
        all_queries = [query] + sub_queries + [step_back]
        return AnalyzedQuery(
            original=query, complexity=complexity,
            rewritten_queries=all_queries, domain=None,
        )
```

### 5.2 Hybrid Search with RRF Fusion

```python
"""
Hybrid search combining dense vector retrieval and BM25 sparse retrieval,
fused with Reciprocal Rank Fusion (RRF).
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class SearchResult:
    chunk_id: str
    parent_id: str
    text: str
    score: float
    metadata: dict = field(default_factory=dict)


def reciprocal_rank_fusion(
    ranked_lists: list[list[SearchResult]],
    k: int = 60,
    top_n: int = 100,
) -> list[SearchResult]:
    """
    Merge multiple ranked lists using RRF.

    RRF_score(d) = sum(1 / (k + rank_i(d))) for each ranker i.

    Args:
        ranked_lists: List of ranked result lists from different retrievers.
        k: Smoothing constant. Default 60 (original paper). Use 10-30 for
           small corpora (<100 docs) to amplify rank differences.
        top_n: Number of results to return.
    """
    scores: dict[str, float] = {}
    results_by_id: dict[str, SearchResult] = {}

    for ranked_list in ranked_lists:
        for rank, result in enumerate(ranked_list, start=1):
            scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + 1.0 / (k + rank)
            if result.chunk_id not in results_by_id:
                results_by_id[result.chunk_id] = result

    sorted_ids = sorted(scores, key=scores.__getitem__, reverse=True)[:top_n]
    return [
        SearchResult(
            chunk_id=cid,
            parent_id=results_by_id[cid].parent_id,
            text=results_by_id[cid].text,
            score=scores[cid],
            metadata=results_by_id[cid].metadata,
        )
        for cid in sorted_ids
    ]


class HybridSearcher:
    """
    Combines dense (vector) and sparse (BM25) search with RRF fusion
    and metadata-based access control filtering.
    """

    def __init__(self, vector_store, bm25_index, embedding_model, rrf_k: int = 60):
        """
        Args:
            vector_store: Vector DB client (Qdrant, Weaviate, pgvector, etc.)
                          Must implement .search(embedding, top_k, filters) -> list[SearchResult]
            bm25_index: BM25 index client.
                        Must implement .search(query_text, top_k, filters) -> list[SearchResult]
            embedding_model: Embedding model client.
                             Must implement .embed(text) -> list[float]
            rrf_k: RRF smoothing constant.
        """
        self.vector_store = vector_store
        self.bm25_index = bm25_index
        self.embedding_model = embedding_model
        self.rrf_k = rrf_k

    def _build_acl_filter(self, user_roles: list[str], user_groups: list[str]) -> dict:
        """Build a metadata filter enforcing document-level access control."""
        return {
            "must": [
                {
                    "any_of": [
                        {"key": "acl_roles", "match_any": user_roles},
                        {"key": "acl_groups", "match_any": user_groups},
                        {"key": "acl_public", "match": True},
                    ]
                }
            ]
        }

    def search(
        self,
        query: str,
        top_k: int = 100,
        user_roles: Optional[list[str]] = None,
        user_groups: Optional[list[str]] = None,
        date_filter: Optional[dict] = None,
    ) -> list[SearchResult]:
        """
        Execute hybrid search with ACL filtering and RRF fusion.

        Args:
            query: The search query (raw text or HyDE-generated document).
            top_k: Number of final results after RRF fusion.
            user_roles: User's RBAC roles for access control filtering.
            user_groups: User's group memberships for access control filtering.
            date_filter: Optional date range filter {"after": "2025-01-01"}.
        """
        filters = {}
        if user_roles or user_groups:
            filters.update(self._build_acl_filter(user_roles or [], user_groups or []))
        if date_filter:
            filters["date_range"] = date_filter

        # Dense retrieval: embed query, search HNSW index
        query_embedding = self.embedding_model.embed(query)
        dense_results = self.vector_store.search(
            embedding=query_embedding, top_k=top_k, filters=filters,
        )

        # Sparse retrieval: BM25 keyword search
        sparse_results = self.bm25_index.search(
            query_text=query, top_k=top_k, filters=filters,
        )

        # Fuse with RRF
        fused = reciprocal_rank_fusion(
            ranked_lists=[dense_results, sparse_results],
            k=self.rrf_k,
            top_n=top_k,
        )
        return fused
```

### 5.3 Reranking Pipeline

```python
"""
Reranking pipeline supporting cross-encoder (local) and Cohere Rerank (API).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import cohere


@dataclass
class RankedResult:
    chunk_id: str
    parent_id: str
    text: str
    original_score: float
    rerank_score: float
    metadata: dict


class Reranker(ABC):
    @abstractmethod
    def rerank(self, query: str, candidates: list, top_k: int = 10) -> list[RankedResult]:
        ...


class CohereReranker(Reranker):
    """
    Cohere Rerank v3.5 API wrapper.
    Cost: $2 per 1,000 searches. P50 latency: ~220ms.
    Score range: [0, 1], relevant pairs typically 0.85-0.95.
    """

    def __init__(self, api_key: str, model: str = "rerank-v3.5"):
        self.client = cohere.Client(api_key)
        self.model = model

    def rerank(self, query: str, candidates: list, top_k: int = 10) -> list[RankedResult]:
        if not candidates:
            return []

        documents = [c.text for c in candidates]
        response = self.client.rerank(
            model=self.model,
            query=query,
            documents=documents,
            top_n=top_k,
        )

        results = []
        for item in response.results:
            candidate = candidates[item.index]
            results.append(RankedResult(
                chunk_id=candidate.chunk_id,
                parent_id=candidate.parent_id,
                text=candidate.text,
                original_score=candidate.score,
                rerank_score=item.relevance_score,
                metadata=candidate.metadata,
            ))
        return results


class CrossEncoderReranker(Reranker):
    """
    Local cross-encoder reranker using a HuggingFace model.
    Score range varies by model -- BGE-v2: [0, 1], relevant at 0.55-0.75.
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        from sentence_transformers import CrossEncoder
        self.model = CrossEncoder(model_name)

    def rerank(self, query: str, candidates: list, top_k: int = 10) -> list[RankedResult]:
        if not candidates:
            return []

        pairs = [(query, c.text) for c in candidates]
        scores = self.model.predict(pairs)

        scored = list(zip(candidates, scores))
        scored.sort(key=lambda x: x[1], reverse=True)

        return [
            RankedResult(
                chunk_id=c.chunk_id,
                parent_id=c.parent_id,
                text=c.text,
                original_score=c.score,
                rerank_score=float(s),
                metadata=c.metadata,
            )
            for c, s in scored[:top_k]
        ]
```

### 5.4 Self-RAG with Reflection and Adaptive Retrieval

```python
"""
Self-RAG implementation with adaptive retrieval decisions and reflection.
Uses LLM-as-judge to simulate Self-RAG reflection tokens (Retrieve, ISREL, ISSUP, ISUSE)
without requiring a fine-tuned model with special tokens.
"""

import asyncio
from dataclasses import dataclass
from typing import Optional

from openai import AsyncOpenAI


@dataclass
class ReflectionResult:
    should_retrieve: bool
    relevant_chunks: list[str]      # chunk IDs that passed ISREL
    support_verdict: str            # fully_supported | partially_supported | no_support
    utility_score: int              # 1-5
    answer: str
    citations: list[dict]           # [{chunk_id, quote, claim}]


RETRIEVE_DECISION_PROMPT = """You are evaluating whether retrieval is needed to answer this query.

Query: {query}
Current context available: {context_summary}

Decide: Does answering this query require retrieving external documents,
or can it be answered from general knowledge alone?

Respond with exactly one word: RETRIEVE or GENERATE"""

RELEVANCE_FILTER_PROMPT = """Evaluate whether this retrieved passage is relevant to the query.

Query: {query}
Passage: {passage}

Is this passage relevant to answering the query? Respond: RELEVANT or IRRELEVANT"""

SUPPORTED_CHECK_PROMPT = """Evaluate whether the generated claim is supported by the evidence.

Claim: {claim}
Evidence: {evidence}

Classification:
- FULLY_SUPPORTED: The evidence directly states or clearly implies the claim.
- PARTIALLY_SUPPORTED: The evidence supports part of the claim but not all of it.
- NO_SUPPORT: The evidence does not support the claim.

Respond with exactly one classification."""


class SelfRAGGenerator:
    def __init__(
        self,
        client: AsyncOpenAI,
        searcher,          # HybridSearcher instance
        reranker,          # Reranker instance
        model: str = "gpt-4o",
    ):
        self.client = client
        self.searcher = searcher
        self.reranker = reranker
        self.model = model

    async def _should_retrieve(self, query: str, context_summary: str = "None") -> bool:
        """Simulate the Retrieve reflection token."""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": RETRIEVE_DECISION_PROMPT.format(
                query=query, context_summary=context_summary,
            )}],
            max_tokens=5,
            temperature=0.0,
        )
        return "RETRIEVE" in response.choices[0].message.content.strip().upper()

    async def _filter_relevant(self, query: str, chunks: list) -> list:
        """Simulate ISREL reflection tokens -- filter irrelevant passages."""
        async def check_one(chunk):
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": RELEVANCE_FILTER_PROMPT.format(
                    query=query, passage=chunk.text,
                )}],
                max_tokens=5,
                temperature=0.0,
            )
            is_relevant = "RELEVANT" in response.choices[0].message.content.strip().upper()
            return chunk if is_relevant else None

        results = await asyncio.gather(*[check_one(c) for c in chunks])
        return [r for r in results if r is not None]

    async def _check_support(self, claim: str, evidence: str) -> str:
        """Simulate ISSUP reflection token."""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": SUPPORTED_CHECK_PROMPT.format(
                claim=claim, evidence=evidence,
            )}],
            max_tokens=10,
            temperature=0.0,
        )
        text = response.choices[0].message.content.strip().upper()
        if "FULLY" in text:
            return "fully_supported"
        if "PARTIAL" in text:
            return "partially_supported"
        return "no_support"

    async def generate(
        self,
        query: str,
        user_roles: Optional[list[str]] = None,
        user_groups: Optional[list[str]] = None,
    ) -> ReflectionResult:
        """
        Full Self-RAG generation loop:
        1. Decide whether to retrieve.
        2. If yes, retrieve and filter relevant passages (ISREL).
        3. Generate answer with inline citations.
        4. Check support (ISSUP) for each claim.
        """
        # Step 1: Retrieve decision
        needs_retrieval = await self._should_retrieve(query)

        if not needs_retrieval:
            # Generate from parametric knowledge
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": query}],
                max_tokens=500,
                temperature=0.3,
            )
            return ReflectionResult(
                should_retrieve=False,
                relevant_chunks=[],
                support_verdict="parametric",
                utility_score=3,
                answer=response.choices[0].message.content,
                citations=[],
            )

        # Step 2: Retrieve and rerank
        search_results = self.searcher.search(
            query=query, top_k=100,
            user_roles=user_roles, user_groups=user_groups,
        )
        reranked = self.reranker.rerank(query=query, candidates=search_results, top_k=10)

        # Step 3: Filter relevant passages (ISREL)
        relevant = await self._filter_relevant(query, reranked)
        if not relevant:
            return ReflectionResult(
                should_retrieve=True,
                relevant_chunks=[],
                support_verdict="no_support",
                utility_score=1,
                answer="I don't have enough information in the available documents to answer this question.",
                citations=[],
            )

        # Step 4: Generate with quote-then-answer
        context_block = "\n\n---\n\n".join(
            f"[{r.chunk_id}]: {r.text}" for r in relevant
        )
        gen_prompt = f"""Answer the following question using ONLY the provided context.
First, extract the most relevant quotes from the context.
Then, synthesize your answer citing each claim with [chunk_id].

Context:
{context_block}

Question: {query}

Answer:"""

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": gen_prompt}],
            max_tokens=800,
            temperature=0.2,
        )
        answer = response.choices[0].message.content

        # Step 5: Check support (ISSUP) for the overall answer
        combined_evidence = " ".join(r.text for r in relevant)
        support_verdict = await self._check_support(claim=answer, evidence=combined_evidence)

        return ReflectionResult(
            should_retrieve=True,
            relevant_chunks=[r.chunk_id for r in relevant],
            support_verdict=support_verdict,
            utility_score=4 if support_verdict == "fully_supported" else 2,
            answer=answer,
            citations=[{"chunk_id": r.chunk_id, "text_preview": r.text[:200]} for r in relevant],
        )
```

### 5.5 Citation Extraction and NLI Verification

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
    """
    NLI-based citation verifier.
    Uses a pretrained NLI model to check (premise=source, hypothesis=claim).
    """

    def __init__(self, model_name: str = "microsoft/deberta-v3-large-mnli"):
        self.nli = pipeline(
            "text-classification",
            model=model_name,
            top_k=None,   # return all label scores
        )
        self.entailment_threshold = 0.7

    def _extract_claims_and_citations(
        self, answer: str, chunks_by_id: dict[str, str],
    ) -> list[tuple[str, str, str]]:
        """
        Parse an answer with inline citations like [chunk_123] into
        (claim_text, chunk_id, source_text) triples.
        """
        # Split answer into sentences, find citation markers
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
                # Uncited claim -- flag it
                triples.append((clean_sentence, "__UNCITED__", ""))
        return triples

    def verify(
        self, answer: str, chunks_by_id: dict[str, str],
    ) -> list[CitationVerification]:
        """
        Verify all citation-claim pairs in the answer.

        Args:
            answer: Generated answer with inline citations [chunk_id].
            chunks_by_id: Mapping of chunk_id -> source text.

        Returns:
            List of CitationVerification results, one per claim.
        """
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

            # NLI: premise = source text, hypothesis = claim
            nli_output = self.nli(
                f"{source_text}",
                candidate_labels=None,
                hypothesis=claim,
            )
            # nli_output is a list of dicts: [{"label": "ENTAILMENT", "score": 0.95}, ...]
            label_scores = {item["label"]: item["score"] for item in nli_output}
            entailment_score = label_scores.get("ENTAILMENT", 0.0)
            top_label = max(label_scores, key=label_scores.get)

            results.append(CitationVerification(
                claim=claim,
                cited_chunk_id=chunk_id,
                cited_text=source_text[:200],
                nli_label=top_label.lower(),
                nli_score=entailment_score,
                is_faithful=entailment_score >= self.entailment_threshold,
            ))

        return results

    def compute_faithfulness_score(self, verifications: list[CitationVerification]) -> float:
        """RAGAS-style faithfulness: fraction of claims that are faithfully cited."""
        if not verifications:
            return 0.0
        faithful_count = sum(1 for v in verifications if v.is_faithful)
        return faithful_count / len(verifications)
```

### 5.6 Document-Level Access Control Filtering

```python
"""
Document-level access control for RAG retrieval.
Enforces RBAC/ABAC filtering so users can only retrieve documents
they have permission to view in the source system.
"""

import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DocumentACL:
    doc_id: str
    owner: str
    department: str
    roles: list[str]           # Roles that can access (e.g., ["engineering", "all"])
    groups: list[str]          # Group names (e.g., ["backend-team", "platform"])
    classification: str        # public, internal, confidential, restricted
    expires_at: Optional[float] = None  # Unix timestamp, None = no expiry


@dataclass
class ChunkRecord:
    chunk_id: str
    parent_id: str
    doc_id: str
    text: str
    embedding: list[float]
    acl: DocumentACL
    content_hash: str
    created_at: float
    embedding_model_version: str
    metadata: dict = field(default_factory=dict)


class ACLFilteredRetriever:
    """
    Wraps a vector store to enforce access control at retrieval time.
    The ACL check happens during search, not after -- preventing
    information leakage via search result counts or latency side channels.
    """

    def __init__(self, vector_store, bm25_index):
        self.vector_store = vector_store
        self.bm25_index = bm25_index

    def _user_can_access(self, acl: DocumentACL, user_roles: list[str],
                         user_groups: list[str], user_clearance: str) -> bool:
        """Check if user satisfies the document's access control requirements."""
        # Check expiry
        if acl.expires_at and time.time() > acl.expires_at:
            return False

        # Check classification clearance
        clearance_levels = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
        if clearance_levels.get(acl.classification, 99) > clearance_levels.get(user_clearance, 0):
            return False

        # Public documents are accessible to all authenticated users
        if acl.classification == "public":
            return True

        # Check role-based access
        if set(user_roles) & set(acl.roles):
            return True

        # Check group-based access
        if set(user_groups) & set(acl.groups):
            return True

        return False

    def build_filter_predicate(
        self, user_roles: list[str], user_groups: list[str], user_clearance: str = "internal",
    ) -> dict:
        """
        Build a vector DB filter that enforces ACL at the database level.
        This is the preferred approach -- push filtering into the DB query
        rather than post-filtering, to avoid retrieving forbidden documents.
        """
        return {
            "should": [
                {"key": "acl_classification", "match": "public"},
                {"key": "acl_roles", "match_any": user_roles},
                {"key": "acl_groups", "match_any": user_groups},
            ],
            "must_not": [
                {"key": "acl_expired", "match": True},
            ],
        }


def compute_chunk_hash(text: str) -> str:
    """Stable content hash for incremental sync change detection."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def detect_stale_chunks(
    existing_chunks: list[ChunkRecord],
    current_embedding_model: str,
) -> list[str]:
    """
    Identify chunks that were embedded with an outdated model version.
    Returns chunk IDs that need re-embedding.
    """
    return [
        c.chunk_id for c in existing_chunks
        if c.embedding_model_version != current_embedding_model
    ]
```

### 5.7 RAGAS-Based Quality Evaluation

```python
"""
RAGAS-based quality evaluation for RAG pipelines.
Implements the four core metrics: faithfulness, answer relevancy,
context precision, and context recall.
"""

import asyncio
from dataclasses import dataclass

import numpy as np
from openai import AsyncOpenAI


@dataclass
class RAGASScores:
    faithfulness: float           # 0-1: are claims supported by context?
    answer_relevancy: float       # 0-1: does the answer address the question?
    context_precision: float      # 0-1: are relevant chunks ranked highly?
    context_recall: float         # 0-1: does context cover all needed info? (requires ground truth)

    @property
    def overall(self) -> float:
        """Harmonic mean of all four metrics."""
        scores = [self.faithfulness, self.answer_relevancy,
                  self.context_precision, self.context_recall]
        scores = [s for s in scores if s > 0]
        if not scores:
            return 0.0
        return len(scores) / sum(1.0 / s for s in scores)

    def is_healthy(self) -> bool:
        """Check against production health benchmarks."""
        return (
            self.context_recall >= 0.80
            and self.context_precision >= 0.70
            and self.faithfulness >= 0.85
            and self.answer_relevancy >= 0.80
        )

    def diagnose(self) -> list[str]:
        """Return diagnostic messages for unhealthy metrics."""
        issues = []
        if self.context_recall < 0.80 and self.faithfulness >= 0.85:
            issues.append(
                "Low context recall + high faithfulness: model is faithful to what it has, "
                "but retrieval is missing key information. Fix: improve chunking, expand k, add reranking."
            )
        if self.faithfulness < 0.85 and self.context_recall >= 0.80:
            issues.append(
                "High context recall + low faithfulness: right chunks retrieved but model is "
                "ignoring or distorting them. Fix: improve prompt, add citation enforcement, switch model."
            )
        if self.answer_relevancy < 0.80 and self.faithfulness >= 0.85:
            issues.append(
                "Low answer relevancy + high faithfulness: retrieval problem disguised as generation "
                "problem. Fix: improve query rewriting, add domain routing."
            )
        if self.faithfulness < 0.85 and self.context_recall < 0.80:
            issues.append(
                "Both faithfulness and context recall low: systemic failure across retrieval "
                "and generation. Fix: audit chunking, embedding model, and prompt in that order."
            )
        return issues


FAITHFULNESS_PROMPT = """Given the following context and answer, break the answer into
atomic factual claims. For each claim, determine if it is supported by the context.

Context:
{context}

Answer:
{answer}

List each claim and whether it is SUPPORTED or UNSUPPORTED.
End with a summary line: "Supported: X/Y"
"""

RELEVANCY_PROMPT = """Given this answer, generate 3 questions that this answer could
plausibly be answering. Each question should capture a different aspect.

Answer: {answer}

Questions (one per line):"""


class RAGASEvaluator:
    def __init__(self, client: AsyncOpenAI, embedding_model, judge_model: str = "gpt-4o"):
        self.client = client
        self.embedding_model = embedding_model
        self.judge_model = judge_model

    async def evaluate_faithfulness(self, answer: str, context: str) -> float:
        """Fraction of claims in the answer that are supported by the context."""
        response = await self.client.chat.completions.create(
            model=self.judge_model,
            messages=[{"role": "user", "content": FAITHFULNESS_PROMPT.format(
                context=context, answer=answer,
            )}],
            max_tokens=500,
            temperature=0.0,
        )
        text = response.choices[0].message.content
        # Parse "Supported: X/Y" from end of response
        import re
        match = re.search(r"Supported:\s*(\d+)\s*/\s*(\d+)", text)
        if match:
            supported, total = int(match.group(1)), int(match.group(2))
            return supported / total if total > 0 else 0.0
        return 0.5  # fallback if parsing fails

    async def evaluate_answer_relevancy(self, query: str, answer: str) -> float:
        """Cosine similarity between query and reverse-generated questions."""
        response = await self.client.chat.completions.create(
            model=self.judge_model,
            messages=[{"role": "user", "content": RELEVANCY_PROMPT.format(answer=answer)}],
            max_tokens=200,
            temperature=0.3,
        )
        generated_questions = [
            q.strip() for q in response.choices[0].message.content.strip().split("\n")
            if q.strip()
        ]
        if not generated_questions:
            return 0.0

        query_emb = np.array(self.embedding_model.embed(query))
        question_embs = [np.array(self.embedding_model.embed(q)) for q in generated_questions]

        similarities = [
            float(np.dot(query_emb, q_emb) / (np.linalg.norm(query_emb) * np.linalg.norm(q_emb) + 1e-10))
            for q_emb in question_embs
        ]
        return float(np.mean(similarities))

    async def evaluate(
        self,
        query: str,
        answer: str,
        context_chunks: list[str],
        ground_truth: str = "",
    ) -> RAGASScores:
        """Run all four RAGAS evaluations."""
        context = "\n\n".join(context_chunks)

        faithfulness, relevancy = await asyncio.gather(
            self.evaluate_faithfulness(answer, context),
            self.evaluate_answer_relevancy(query, answer),
        )

        # Context precision: simplified as average reciprocal rank of relevant chunks
        # In production, use RAGAS library directly for full implementation
        context_precision = 0.75  # placeholder -- use ragas.metrics in production

        # Context recall: requires ground truth comparison
        context_recall = 0.80  # placeholder -- use ragas.metrics in production
        if ground_truth:
            gt_emb = np.array(self.embedding_model.embed(ground_truth))
            ctx_emb = np.array(self.embedding_model.embed(context[:2000]))
            context_recall = float(
                np.dot(gt_emb, ctx_emb) / (np.linalg.norm(gt_emb) * np.linalg.norm(ctx_emb) + 1e-10)
            )

        return RAGASScores(
            faithfulness=faithfulness,
            answer_relevancy=relevancy,
            context_precision=context_precision,
            context_recall=context_recall,
        )
```

---

## 6. Architectural System Design Scenarios

### Scenario 1: Enterprise Knowledge Base with Multi-Source Agentic RAG

**Problem Statement**: A 10,000+ employee enterprise needs unified Q&A across Confluence wikis, SharePoint documents, Jira tickets, Slack threads, internal code repositories, and CRM data. Users span engineering, sales, legal, and HR. Documents have varying access levels (public, internal, confidential, restricted). The system must handle 5,000 queries/day with sub-second latency for simple questions and acceptable latency for complex multi-hop queries. Answers must cite sources. Compliance with EU AI Act (Article 12) is mandatory.

**Proposed Architecture**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         QUERY INGRESS                                       │
│                                                                             │
│  User Query + Auth Token                                                    │
│       │                                                                     │
│       v                                                                     │
│  ┌─────────────────────────┐                                               │
│  │ Query Complexity         │                                               │
│  │ Classifier (fine-tuned   │──── A (5%) ──── Parametric answer, no RAG    │
│  │ DistilBERT, <5ms)       │                                               │
│  └─────────┬───────────────┘                                               │
│            │                                                                │
│     B (65%)│           C (30%)                                              │
│            v                v                                               │
│  ┌──────────────┐  ┌───────────────────────────────────────────────────┐   │
│  │ ADVANCED RAG  │  │ AGENTIC RAG LOOP                                 │   │
│  │              │  │                                                   │   │
│  │ HyDE rewrite │  │ ┌─────────────┐   ┌────────────────────────┐    │   │
│  │      │       │  │ │ Intent       │   │ Query Decomposer       │    │   │
│  │      v       │  │ │ Router       │──>│ (2-5 sub-queries)      │    │   │
│  │ Hybrid search│  │ └──────┬──────┘   └────────────┬───────────┘    │   │
│  │ (BM25+dense) │  │        │                       │                │   │
│  │      │       │  │        v                       v                │   │
│  │ ACL filter   │  │ ┌──────────────────────────────────────────┐    │   │
│  │      │       │  │ │ Per-sub-query iterative loop (IRCoT)     │    │   │
│  │ RRF fusion   │  │ │  retrieve → reason → retrieve (max 5)   │    │   │
│  │      │       │  │ │  ACL-filtered at every retrieval step    │    │   │
│  │ Rerank top-10│  │ └──────────────────────────────┬───────────┘    │   │
│  │      │       │  │                                │                │   │
│  │      v       │  │                                v                │   │
│  │ Generate +   │  │                    ┌───────────────────────┐    │   │
│  │ cite         │  │                    │ CRAG Quality Gate      │    │   │
│  └──────┬───────┘  │                    │ High → refine+generate │    │   │
│         │          │                    │ Low → web fallback     │    │   │
│         │          │                    │ Ambig → merge both     │    │   │
│         │          │                    └───────────┬───────────┘    │   │
│         │          │                                │                │   │
│         │          │                                v                │   │
│         │          │                    ┌───────────────────────┐    │   │
│         │          │                    │ Generate with citations│    │   │
│         │          │                    │ + NLI verification     │    │   │
│         │          │                    └───────────┬───────────┘    │   │
│         │          └────────────────────────────────┘                │   │
│         │                                           │                    │
│         v                                           v                    │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ Output Guardrails: PII scan + toxicity filter + audit log       │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Ingestion pipeline**:
- Source connectors: Confluence API, SharePoint Graph API, Jira REST, Slack export, GitHub API, SFDC.
- Document-aware parsing preserving tables, headers, code blocks (Azure AI Foundry or Unstructured.io).
- Parent-child chunking: 2000-char parents along structural boundaries, 400-char children.
- Contextual retrieval (Anthropic method): 50-100 token context preamble per chunk at ingestion.
- ACL metadata captured at ingestion: department, role, classification level, group entitlements, expiry.
- Incremental sync: content hashing (SHA-256), daily cron + event-driven (S3/webhook) for real-time sources.
- Embedding model: Voyage-4-large for quality-first; text-embedding-3-small for cost-sensitive.

**Technology choices**: Weaviate (native hybrid search, multi-node replication) or Qdrant (best latency self-hosted). Cohere Rerank v3.5 for reranking. GPT-4o or Claude Sonnet 4 for generation. DeBERTa-v3-large-MNLI for NLI citation verification. DistilBERT fine-tuned on query complexity labels for routing.

**Monitoring**: RAGAS on sampled production queries (faithfulness > 0.85, context recall > 0.80). Weekly retrieval eval on 50 hand-labeled queries. Alert on faithfulness drops below 0.80. Full audit logging for EU AI Act compliance.

**Cost estimate**: 5,000 queries/day blended (65% advanced RAG, 30% agentic, 5% parametric): ~$0.015/query average, $2,250/month compute + API. Vector DB hosting (Weaviate self-hosted, 10M chunks): $500-1,500/month. Total: $3,000-4,000/month.

**Trade-Off Evaluation Matrix**:

| Dimension | Option A: Full Agentic (every query) | Option B: Adaptive Routing (recommended) | Option C: Advanced RAG Only (no agentic) |
|-----------|--------------------------------------|------------------------------------------|------------------------------------------|
| Cost/month (5K queries/day) | $9,000-18,000 | $3,000-4,000 | $1,200-1,800 |
| P50 latency | 3-8s | 600ms (blended) | 400-800ms |
| Accuracy (complex queries) | Best (+52%) | Same as A for complex queries | Baseline (misses multi-hop) |
| Accuracy (simple queries) | Same as C (wasted compute) | Same as C | Baseline |
| Ops complexity | High (loop debugging, cost monitoring) | Medium (classifier maintenance) | Low |
| Scalability ceiling | Lower (LLM calls scale linearly) | High (simple path is cheap) | Highest |

**Decision rationale**: Option B (adaptive routing) wins because 65-70% of enterprise queries are moderate complexity and need only single-pass hybrid RAG. Routing them through the agentic loop wastes 3-10x cost and adds 2-25 seconds of unnecessary latency. The query complexity classifier adds negligible overhead (<5ms) and can be trained on production data within the first 2 weeks of operation. Complex queries still get the full agentic treatment. The only maintenance burden is periodic retraining of the complexity classifier as query patterns shift.

---

### Scenario 2: Regulatory Compliance Document Q&A with Citation Tracking

**Problem Statement**: A financial services firm needs auditable Q&A over 50,000+ regulatory documents: SEC filings, internal compliance policies, regulatory guidance letters, Basel III/IV frameworks, and MiFID II documentation. Every answer must cite specific document sections (Document title, Section number, Paragraph, Effective date). Responses are used for audit preparation and compliance decisions. Zero tolerance for hallucination -- a fabricated citation in an audit response is a regulatory event. The system must maintain a 7-year immutable audit trail. 500 queries/day from compliance analysts and auditors.

**Proposed Architecture**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     COMPLIANCE RAG PIPELINE                                 │
│                                                                             │
│  Compliance Query + Analyst Auth                                            │
│       │                                                                     │
│       v                                                                     │
│  ┌──────────────────────────────────┐                                      │
│  │ Query Rewriter                    │                                      │
│  │ - Standardize regulatory terms    │                                      │
│  │ - Expand acronyms (e.g., KYC,    │                                      │
│  │   AML, CRD, LCR)                 │                                      │
│  │ - Multi-query for cross-reg Qs   │                                      │
│  └──────────────┬───────────────────┘                                      │
│                 │                                                            │
│                 v                                                            │
│  ┌──────────────────────────────────┐                                      │
│  │ Hybrid Retrieval                  │                                      │
│  │ ┌──────────┐  ┌───────────────┐  │                                      │
│  │ │ Dense     │  │ BM25           │  │                                      │
│  │ │ Voyage-4  │  │ (critical for  │  │                                      │
│  │ │ fine-tuned│  │ reg numbers:   │  │                                      │
│  │ │ on fin.   │  │ "Rule 10b-5", │  │                                      │
│  │ │ domain    │  │ "Art. 28")     │  │                                      │
│  │ └─────┬────┘  └──────┬────────┘  │                                      │
│  │       │              │            │                                      │
│  │       v              v            │                                      │
│  │  Metadata filter: doc type,       │                                      │
│  │  jurisdiction, effective date,    │                                      │
│  │  supersession status              │                                      │
│  │       │                           │                                      │
│  │       v                           │                                      │
│  │  RRF fusion → top-100             │                                      │
│  └──────────────┬───────────────────┘                                      │
│                 │                                                            │
│                 v                                                            │
│  ┌──────────────────────────────────┐                                      │
│  │ Cross-Encoder Rerank              │                                      │
│  │ Cohere Rerank v3.5 or BGE-v2     │                                      │
│  │ scored against regulatory domain  │                                      │
│  │ → top-10 passages                │                                      │
│  └──────────────┬───────────────────┘                                      │
│                 │                                                            │
│                 v                                                            │
│  ┌──────────────────────────────────┐                                      │
│  │ Quote-then-Answer Generation      │                                      │
│  │                                  │                                      │
│  │ Step 1: Extract exact quotes     │                                      │
│  │         from top-10 passages     │                                      │
│  │ Step 2: Synthesize answer using  │                                      │
│  │         ONLY extracted quotes    │                                      │
│  │ Step 3: Format with citations    │                                      │
│  │   [DocTitle, Sec X.Y, Para Z,   │                                      │
│  │    Effective YYYY-MM-DD]         │                                      │
│  └──────────────┬───────────────────┘                                      │
│                 │                                                            │
│                 v                                                            │
│  ┌──────────────────────────────────┐                                      │
│  │ NLI Verification Pipeline         │                                      │
│  │                                  │                                      │
│  │ For each (claim, citation) pair: │                                      │
│  │  DeBERTa-v3 NLI → entailment /  │                                      │
│  │  contradiction / neutral          │                                      │
│  │                                  │                                      │
│  │ If ANY claim unsupported:        │                                      │
│  │  → regenerate that claim         │                                      │
│  │  → or return "insufficient       │                                      │
│  │    evidence" for that portion    │                                      │
│  └──────────────┬───────────────────┘                                      │
│                 │                                                            │
│                 v                                                            │
│  ┌──────────────────────────────────┐                                      │
│  │ Immutable Audit Logger            │                                      │
│  │                                  │                                      │
│  │ Logged per query:                │                                      │
│  │  - Query text, analyst ID, time  │                                      │
│  │  - All retrieved doc IDs + text  │                                      │
│  │  - Generated answer              │                                      │
│  │  - NLI scores per citation       │                                      │
│  │  - Any regeneration events       │                                      │
│  │                                  │                                      │
│  │ Append-only store, 7-year        │                                      │
│  │ retention, tamper-evident        │                                      │
│  └──────────────────────────────────┘                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Chunking strategy**: Regulatory documents have clear hierarchical structure. Section-aware chunking respects regulation boundaries (Part -> Section -> Subsection -> Paragraph). Parent-child with regulation-specific boundaries. Cross-references preserved as metadata links (e.g., "See also Rule 17a-4" links to that rule's chunks).

**Temporal handling**: Regulations supersede each other. Each chunk carries metadata: effective date, supersession chain (which regulation it replaced, which replaced it), jurisdiction. Retrieval prefers the most current version unless the analyst specifies a historical date. Automated effective-date checks detect stale regulations.

**Quality gates**:
- Faithfulness > 0.95 (higher bar than general enterprise -- a fabricated citation in a regulatory response is a compliance event).
- Context precision > 0.85.
- Zero tolerance for citation-shaped hallucinations (the NLI pipeline catches these).
- Monthly audit: 200 randomly sampled responses reviewed by compliance team.
- Automated regression testing against known Q&A pairs from prior audits.

**Technology choices**: Voyage-4-large fine-tuned on financial/regulatory domain for embeddings. Qdrant self-hosted (latency, data residency). Cohere Rerank v3.5 for reranking. GPT-4o or Claude Opus for generation (highest faithfulness). DeBERTa-v3-large-MNLI for NLI verification. PostgreSQL for audit log (append-only, WORM-compliant storage backend).

**Trade-Off Evaluation Matrix**:

| Dimension | Option A: Quote-then-Answer + NLI (recommended) | Option B: Standard RAG + Post-hoc Citation | Option C: GraphRAG + Entity Linking |
|-----------|------------------------------------------------|-------------------------------------------|-------------------------------------|
| Citation accuracy | <3% hallucination rate | 8-15% hallucination rate | ~5% (entity extraction errors) |
| Latency (P50) | 1.5-3s | 0.5-1s | 3-8s |
| Cost/query | $0.03-0.08 | $0.01-0.02 | $0.05-0.15 |
| Audit readiness | Full (NLI scores logged per claim) | Weak (no verification layer) | Medium (graph traversal logged) |
| Ops complexity | Medium (NLI model hosting) | Low | High (graph construction + maintenance) |
| Temporal handling | Good (metadata-based) | Basic | Complex (graph versioning needed) |
| Scalability ceiling | 50K+ docs, scales well | 50K+ docs, scales well | 10-20K docs practical limit |

**Decision rationale**: Option A (quote-then-answer + NLI verification) wins for this use case because the non-negotiable requirement is citation accuracy. Post-hoc citation attachment (Option B) has a documented 8-15% hallucination rate -- unacceptable for audit responses. GraphRAG (Option C) adds value for cross-regulation relationship queries but introduces graph construction and maintenance complexity that is hard to justify for a 50K-document corpus where most queries are single-regulation lookups. The 1-2 second latency premium of Option A over Option B is acceptable for compliance analysts who currently spend 30-60 minutes per manual research query. The NLI verification layer provides the audit trail regulators require under EU AI Act Article 12 -- every claim has a logged entailment score against its cited source. At 500 queries/day, the monthly cost of $450-1,200 is justified by replacing hours of manual regulatory research per query.
