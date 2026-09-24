# Research: Agentic RAG

**Date researched**: 2026-09-23
**Sources consulted**: 42

---

## 1. System Topology & Mechanics

### Agentic RAG vs Naive RAG

**Naive RAG** follows a fixed pipeline: query -> embed -> top-k vector search -> stuff into prompt -> generate. It works for simple factual questions against a clean, well-chunked knowledge base but plateaus at 70-80% precision for complex queries. It is a one-shot pipeline with no reasoning or validation over retrieval quality. ([LlamaIndex Blog](https://www.llamaindex.ai/blog/rag-is-dead-long-live-agentic-retrieval))

**Advanced RAG** (2024 transition) treats retrieval as a multi-stage process: query rewriting, decomposition, routing to different indexes, reranking, metadata filtering, and sometimes second-pass retrieval. This is where hybrid search (vector + keyword + reranking) entered the standard stack.

**Agentic RAG** (2025-2026) replaces the fixed pipeline with a control loop. The LLM becomes an agent that decides what to retrieve, evaluates whether results are sufficient, and takes corrective action if not. It decomposes complex queries into sub-queries, routes each to different data sources, validates consistency, and re-retrieves when results are poor. Benchmarks show +33% average accuracy improvement over traditional RAG, +47% for multi-hop queries, and +52% for complex queries. However, a naive RAG query costs ~$0.001 while agentic RAG costs ~10x that with 5 seconds additional latency. For simple factual queries, agentic RAG is pure waste. ([LlamaIndex](https://blog.llamaindex.ai/agentic-rag-with-llamaindex-2721b8a49ff6), [Weaviate](https://weaviate.io/blog/what-is-agentic-rag))

**Practical recommendation**: Use a query complexity classifier at the front door to route simple queries through standard advanced RAG and complex multi-hop queries through the agentic path. This reduces costs by ~40% and latency by ~35% compared to running every query through the agentic pipeline. ([ArXiv: Adaptive-RAG](https://arxiv.org/abs/2403.14403))

### Query Analysis and Rewriting

**HyDE (Hypothetical Document Embeddings)**: Instead of embedding the short user query, the LLM generates a hypothetical answer document (50-200 tokens), which is then embedded and used for vector search. This bridges the semantic gap between short queries and longer documents. The hypothetical document captures relevant vocabulary and themes even if factually inaccurate. Trade-off: adds one LLM inference call per query (~200-500ms latency). Works best for broad, open-domain retrieval; may struggle with narrow, domain-specific searches where factual details matter. ([Haystack Docs](https://docs.haystack.deepset.ai/docs/hypothetical-document-embeddings-hyde), [Zilliz Learn](https://zilliz.com/learn/improve-rag-and-information-retrieval-with-hyde-hypothetical-document-embeddings))

**Step-back prompting**: Generates a broader, more abstract version of the query to retrieve high-level context before answering. Example: "What is the refresh rate of iPhone 13 Pro Max?" becomes "What are the technical specifications of the iPhone 13 Pro Max?" This is effective for queries requiring conceptual understanding or background knowledge. ([NirDiamant/RAG_Techniques](https://github.com/NirDiamant/RAG_Techniques/blob/main/all_rag_techniques/query_transformations.ipynb))

**Multi-query decomposition**: The LLM rewrites the original query from several perspectives. Each variant retrieves results separately, and results are merged using Reciprocal Rank Fusion (RRF). This improves both recall and precision for complex or ambiguous questions. Sub-query decomposition breaks complex questions into independent sub-questions solved separately, then synthesized. ([DEV Community](https://dev.to/jamesli/in-depth-understanding-of-rag-query-transformation-optimization-multi-query-problem-decomposition-and-step-back-27jg))

**When to use which**: Query rewriting is the cheapest transformation. Step-back prompting helps when queries are too specific. Multi-query works for ambiguous or multi-faceted questions. Do not add a transformation step until you can name the retrieval failure mode it fixes and the latency cost you accept. ([Alex Chernysh Blog](https://alexchernysh.com/blog/query-transformation-for-rag))

### Hybrid Search: Dense + Sparse + Fusion

**The problem**: BM25 (sparse) excels at exact-match queries (product codes, entity names, error codes) but has no semantic understanding. Dense vector retrieval handles paraphrase and conceptual queries but may underweight rare exact terms. A search for "ERR-502-BAD-GATEWAY" with dense-only might retrieve general networking docs instead of the specific runbook.

**Score incompatibility**: BM25 produces unbounded positive integers; cosine similarity is bounded in [-1, 1]. Mixing raw scores gives BM25 dominant weight. Normalizing to [0, 1] is unreliable because score distributions have completely different shapes.

**Reciprocal Rank Fusion (RRF)** (Cormack et al., SIGIR 2009): Discards scores entirely and works on rank position. Formula: `RRF_score(d) = sum(1 / (k + rank_i(d)))` where k is a smoothing constant (default 60). Properties: normalization-free, scalable for billion-scale sharded indices, finds consensus across methods. ([Guillaume Laforge Blog](https://glaforge.dev/posts/2026/02/10/advanced-rag-understanding-reciprocal-rank-fusion-in-hybrid-search/))

**Tuning k**: k=60 is the original paper default for web search. For short corpora (<100 docs), use k=10-30 to amplify rank differences. For noisy corpora, use larger k to smooth results. Treat as a hyperparameter.

**Benchmark results**: On the WANDS e-commerce benchmark, tuned hybrid reaches 0.7497 NDCG, a 7.4% lift over BM25-only (0.6983) or dense-only (0.6953). Two-stage hybrid + Cohere Rerank achieves +17.4% relative Recall@5 over hybrid RRF alone (0.816 vs 0.695) and +39.0% over dense-only. ([Digital Applied](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026))

**Vendor support**: Hybrid search is the production default across OpenSearch, Elasticsearch, Weaviate, Vespa, Qdrant, and Milvus.

### Reranking: Cross-Encoders, Cohere Rerank, ColBERT

**Cross-encoder reranking**: Takes query-document pairs jointly through a single transformer model to produce a relevance score. Quality lift is typically +5 to +15 NDCG@10 points across MTEB/BEIR benchmarks. Every candidate requires a forward pass, so it runs only on the top 30-100 candidates from first-stage retrieval.

**Cohere Rerank v3.5**: Closed API, $2 per 1K searches (one query against up to 100 documents). P50 latency ~220ms. 4096-token context, 100+ language support. Strong on BEIR and enterprise domains. The "just works" choice for teams without GPU infra. ([Cohere Docs](https://docs.cohere.com/reference/rerank))

**ColBERT (late interaction)**: Encodes queries and documents separately into per-token embeddings, then scores with MaxSim at query time. More expressive than single-vector cosine, far cheaper than full cross-encoder. Document representations are precomputed and cached. Latency: tens of ms on 100 docs on a single GPU. Best when corpus is large, latency budget is tight, and k > 200. ([Thread Transfer](https://thread-transfer.com/blog/2026-06-17-rag-reranking-llm-colbert/))

**Score calibration warning**: BGE-v2 normalized scores land in [0, 1] with relevant pairs at 0.55-0.75. Cohere scores are [0, 1] with relevant pairs at 0.85-0.95. ColBERT MaxSim is unbounded (relevant pair might score 4 or 18). Cannot use a single threshold across rerankers.

**Production pipeline**: BM25 top-50 + Dense top-50 -> RRF merge to top-100 -> Cross-encoder rerank to top-10 -> LLM generation. A common pitfall: reranking too few candidates. If you retrieve top-5 and rerank to top-3, you are barely shuffling the deck. Use N=30-50 minimum.

### Parent Document Retrieval / Hierarchical Chunking

**Small-to-big retrieval**: Embed small "child" chunks (50-200 tokens) for precise matching, but replace them with larger "parent" chunks (500-1500 tokens) before passing to the LLM. This decouples retrieval granularity (small = precise embeddings) from generation granularity (large = sufficient context). ([LanceDB Blog](https://www.lancedb.com/blog/modified-rag-parent-document-bigger-chunk-retriever-62b3d1e79bc6), [Sophia Yang / TDS](https://medium.com/data-science/advanced-rag-01-small-to-big-retrieval-172181b396d4))

**Why it works**: Small chunks embed cleanly (embeddings degrade on long, topic-mixed text), but small chunks alone give the LLM tunnel vision. The parent gives it room to reason.

**Two variants**: (1) Parent-child chunk retrieval: fetch child chunks, follow parent IDs, return parent chunks. (2) Sentence window retrieval: fetch a single sentence, return the surrounding window of text.

**Recommended sizes**: ~2000-character parent chunks along structural boundaries, ~400-character child chunks. Embed and index children only. At query time, retrieve top-k children, deduplicate by parent, send parents to LLM.

**Framework support**: LlamaIndex provides AutoMergingRetriever. LangChain's ParentDocumentRetriever was deprecated and removed in v0.2.0 (Feb 2024).

### Adaptive Retrieval

**Adaptive-RAG** (Jeong et al., NAACL 2024): Uses a small classifier to predict query complexity and route to the appropriate retrieval strategy. Three levels: "A" (simple, use parametric knowledge, no retrieval), "B" (moderate, single-step RAG), "C" (complex, multi-step iterative RAG). The classifier is a smaller LM trained on automatically collected labels from actual model outcomes. Validated on open-domain QA datasets, showing enhanced efficiency and accuracy over baselines. ([ArXiv](https://arxiv.org/abs/2403.14403), [ACL Anthology](https://aclanthology.org/2024.naacl-long.389/))

**Self-Routing RAG (SR-RAG)**: Reformulates selective retrieval as a knowledge source selection problem. The LLM self-routes between retrieving external knowledge and verbalizing its own parametric knowledge. ([ArXiv](https://arxiv.org/html/2504.01018v1))

**Key finding**: Indiscriminate retrieval introduces noise, degrades performance on well-known entities, increases cost, and can harm response quality when parametric knowledge would suffice. Atlas model research found that when the model can choose between parametric and non-parametric information, it relies more on retrieved context than parametric knowledge. ([ArXiv](https://arxiv.org/abs/2410.05162))

### Self-RAG: Self-Reflection Tokens

**Self-RAG** (Asai et al., ICLR 2024): Trains a single LM to adaptively retrieve passages on-demand and generate/reflect using special **reflection tokens** as part of the model's vocabulary. ([ArXiv](https://arxiv.org/abs/2310.11511), [Project Page](https://selfrag.github.io/))

**Four reflection tokens**:
- **Retrieve**: Should I retrieve information right now?
- **ISREL**: Is this retrieved passage relevant to the query?
- **ISSUP**: Is the generated claim supported by the evidence?
- **ISUSE**: Is the overall response useful?

**Three-stage process per segment**: (1) Decode retrieval token to decide if retrieval is needed. (2) If yes, retrieve top passages and generate critique tokens evaluating relevance. (3) Generate output conditioned on relevant passages, then evaluate support and utility.

**Training**: A critic model (trained on GPT-4 annotations) inserts reflection tokens offline into training data. The generator is trained with standard next-token prediction on this augmented corpus. No critic model needed at inference.

**Results**: Self-RAG 13B outperforms ChatGPT and retrieval-augmented Llama2-chat. On PopQA: 55.8% accuracy (vs Llama2-13B 14.7%). On PubHealth: 74.5% (vs Alpaca-13B 51.1%). On ARC-Challenge: 73.1% (vs Alpaca-13B 57.6%).

### Corrective RAG (CRAG)

**CRAG** (Yan et al., 2024): Adds a lightweight retrieval evaluator (T5-based) that scores retrieved document quality and triggers corrective actions. ([ArXiv](https://arxiv.org/abs/2401.15884))

**Three action paths**:
- **Correct (high confidence)**: Refine retrieved documents using decompose-then-recompose (split into sentences, keep only answer-relevant pieces, reassemble).
- **Incorrect (low confidence)**: Discard faulty retrievals entirely, trigger web search fallback.
- **Ambiguous (uncertain)**: Combine refined retrieval with web search results.

**Key property**: Plug-and-play design that couples with any RAG pipeline. Tested with LLaMA2 and SelfRAG-LLaMA2-7b generators. More robust than baselines when retrieval quality degrades -- even with fewer correct documents, CRAG's performance drops less due to web search fallback and careful filtering.

### Multi-Hop Reasoning: Iterative Retrieval

**IRCoT** (Interleaving Retrieval with Chain-of-Thought): Alternates between reasoning and retrieval steps. After generating each reasoning step, uses that step as a query for additional retrieval. Loop runs up to 8 steps, capping at 15 paragraphs. No training required -- entirely prompting-based. ([ArXiv](https://arxiv.org/abs/2212.10509))

**Results**: On HotpotQA, +11.3 retrieval recall points and +7.1 QA F1 points over one-step retrieval (GPT-3). On 2WikiMultihopQA: +22.6 recall and +13.2 F1. Striking finding: Flan-T5-XL (3B) with IRCoT outperforms GPT-3 (175B) with one-step retrieval. Reduces factual errors in CoT by 50% on HotpotQA.

**FLARE** (Forward-Looking Active Retrieval, EMNLP 2023): Generates next sentence, checks token probabilities. If any token falls below confidence threshold theta, triggers retrieval using the generated sentence as query, then regenerates. Achieves 51.0 EM on 2WikiMultihopQA vs 39.4 for single-retrieval. Limitation: depends on model calibration; instruction-tuned/RLHF models are often overconfident, making confidence thresholds unreliable. ([ArXiv](https://arxiv.org/abs/2305.06983), [ACL Anthology](https://aclanthology.org/2023.emnlp-main.495/))

**CoRAG (Chain-of-Retrieval Augmented Generation)**: Uses rejection sampling to augment RAG datasets with intermediate retrieval chains, then fine-tunes open-source LMs with standard next-token prediction. Supports greedy decoding, best-of-N sampling, and tree search at inference. ([ArXiv](https://arxiv.org/pdf/2501.14342))

**Challenge**: As retrieval iterations increase, generated queries can drift from correct reasoning path, and irrelevant information accumulates. Latency scales linearly with reasoning depth.

### Citation Tracking and Attribution

**The problem**: Over 95% of answers from tested open-source LLMs contain at least one sentence without any attribution. 57% of citations in a RAG-optimized model show unfaithful behavior (Wallat et al.). Citation-shaped hallucinations (answers that appear grounded because they include source markers while the actual claim is unsupported) are more dangerous than plain hallucinations because users lower their guard. ([ArXiv: Attribution Survey](https://arxiv.org/html/2601.19927v1))

**Key techniques**:
- **Inline citation generation**: Citations generated during answer synthesis (not post-hoc) are a necessary condition for faithfulness.
- **Quote-then-answer**: Model extracts relevant quotes from context first, then synthesizes an answer using only those quotes. Reduces hallucination by committing to evidence before generating claims.
- **NLI-based verification**: Natural Language Inference model checks whether each cited claim is entailed by its source. Classifies (premise=cited chunk, hypothesis=claim) as entailment/contradiction/neutral.
- **Token-level attribution (UAF)**: Aggregates token-level provenance scores into grounding confidence. Claims below threshold are flagged. Cuts fabricated claims by ~42% vs baseline RAG.
- **FACTUM**: Detects citation hallucination by analyzing model internal pathways. State-of-the-art for mechanistic citation verification.

**Production finding**: Even with perfect retrieval, GPT-4-class models fabricate details in 8-15% of responses. Citation enforcement reduces this to under 3%. ([Medium: RAG Grounding](https://medium.com/@Nexumo_/rag-grounding-11-tests-that-expose-fake-citations-30d84140831a))

### GraphRAG: Knowledge Graph-Enhanced Retrieval

**GraphRAG** (Microsoft Research, April 2024): Instead of indexing raw document chunks, extracts entities and relationships to build a knowledge graph, then uses graph structure for retrieval. ([GitHub](https://github.com/microsoft/graphrag), [Microsoft Research](https://www.microsoft.com/en-us/research/project/graphrag/))

**Architecture**: Three components inserted into RAG pipeline: (1) Graph construction (text -> entity-relationship graphs via LLM extraction), (2) Community detection (hierarchical clustering via Leiden algorithm), (3) Community summarization (natural language summaries per community).

**Two query modes**: Local Query (specific factual questions: extract entities, find in graph, traverse 1-2 hop neighbors, collect context) and Global Query (broad synthesis questions: use community summaries for holistic answers).

**Why graphs help multi-hop**: With flat documents, retrieving all pieces of a 3-hop chain (A->B->C) in top-k is statistically unlikely. A graph lets you start from a matched entity, traverse to related entities, and pull context from each step.

**Production caveat**: Entity extraction quality is the hard part. The paper assumes extraction accuracy above 85%; below that, the knowledge graph introduces noise that degrades performance relative to simple RAG. The project is now largely in maintenance mode (2026), with technology available through Microsoft Discovery.

### RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval

**RAPTOR** (Sarthi et al., ICLR 2024): Recursively embeds, clusters, and summarizes text chunks, building a tree with differing levels of abstraction from bottom up. At inference, retrieves from this tree to integrate information at multiple granularities. ([ArXiv](https://arxiv.org/abs/2401.18059), [GitHub](https://github.com/parthsarthi03/raptor))

**How the tree is built**: Text chunks are embedded with SBERT (multi-qa-mpnet-base-cos-v1), clustered using soft clustering algorithms, and each cluster gets an LLM-generated summary as a parent node. This process recurses up the tree.

**Results**: On QuALITY benchmark (complex multi-step reasoning), RAPTOR + GPT-4 improves best performance by 20% absolute accuracy. F-1 scores are at least 1.8% points higher than DPR and 5.3% higher than BM25 across all tested LMs.

**Scalability**: Tree construction costs scale linearly with document length.

### Contextual Retrieval (Anthropic)

**Contextual Retrieval** (Anthropic, September 2024): Prepends a 50-100 token explanatory preamble to every chunk before embedding and BM25 indexing. An LLM generates the context using the full document as reference, capturing document subject, section, entities, and dates separated from the chunk during splitting. ([Anthropic Engineering](https://www.anthropic.com/engineering/contextual-retrieval))

**Key results**: Cut retrieval failures by 49%, and by 67% when combined with reranking. Contextual Embeddings alone reduced top-20 retrieval failure rate by 35% (5.7% -> 3.7%).

**Cost via prompt caching**: One-time cost of $1.02 per million document tokens (assuming 800-token chunks, 8k-token documents, 50-token instructions, 100 tokens of context per chunk). Contextualization happens once at ingestion, not per query -- unlike HyDE which adds latency per query.

**Recommendations**: Always pair contextual embeddings with contextual BM25. Add reranking if latency tolerance allows. Use 20 chunks for best results.

---

## 2. Token Economics & NFR Metrics

### Per-Query Cost Breakdown

Total cost per RAG answer = Embedding (amortized) + Search + LLM generation. Typical range: $0.004-$0.06 per answer. At 1,000 queries/day: $120-$1,800/month. ([SandBase Blog](https://blog.sandbase.ai/rag-cost-structure-embedding-search-2026/), [TechNovice](https://www.technovice.net/post/latency-budget-production-rag))

**Breakdown by component**:
- Self-managed search (e.g., Pinecone Standard + text-embedding-3-large): ~$0.0007/query for embedding + vector search
- Managed search APIs: $0.001-$0.02/query all-inclusive
- Reranking (Cohere Rerank v3.5): $0.002/query ($2 per 1K searches)
- Production allocation: embedding generation 40-60%, vector storage 20-35%, LLM inference 15-25%, infrastructure 10-20%

**Token math for typical query**: Each retrieved chunk ~200 tokens. 8 chunks = ~1,800 input tokens. Typical answer ~250 output tokens. Query embedding itself is ~20 tokens (rounding error). Output tokens priced several times higher than input, but input is 7x larger, so context stuffing can cost more than the answer.

### Embedding Model Costs (2026)

| Model | Cost per 1M tokens | MTEB Score | Notes |
|-------|-------------------|------------|-------|
| Voyage-4-large | ~$0.12 | Best retrieval quality API | |
| Gemini Embedding 001 | ~$0.15 | Best all-rounder | |
| text-embedding-3-large | ~$0.13 | 64.6 | 6.5x more than 3-small for ~2-3 MTEB points |
| text-embedding-3-small | ~$0.02 | ~62 | Best budget option |

Embedding a 10K-document corpus: ~$1.50 one-time + $0-$25/month hosting on pgvector. A 1M-document corpus: ~$150 one-time + $70-$400/month. A 10M-document corpus at 500 tokens/chunk requires embedding 5B tokens: ~$650 using OpenAI, ~$300 using Voyage AI. ([AI-TLDR](https://ai-tldr.dev/learn/rag/rag-fundamentals/rag-cost-and-latency-basics/))

### Latency Breakdown

Retrieval takes ~35% of time-to-first-token in a 2024 systems study, against folk wisdom that generation dominates. The expensive stage and the slow stage are often different: generation is the biggest dollar cost, but retrieval round-trips and reranking dominate wall-clock time.

**Stage-level latency** (production estimates):
- Query embedding: 5-20ms
- Vector search (HNSW): 2-25ms (Qdrant: 3.5ms avg at 1M vectors)
- BM25 search: 5-15ms
- RRF fusion: <1ms
- Cross-encoder rerank (100 docs): 100-300ms (ColBERT: tens of ms)
- LLM generation (TTFT): 200-2000ms depending on model and context length

**Reranking trade-off**: One case study showed accuracy jumping from 73% to 91% with cross-encoder reranking, but added 300ms latency. Another chatbot had worse user experience because of the added delay.

**Agentic RAG latency**: Re-querying the retriever every 4 generated tokens pushes end-to-end latency to ~30 seconds, with retrieval and re-prefill eating 81% of total.

### Quality Metrics: RAGAS Framework

**RAGAS** (Retrieval-Augmented Generation Assessment, EACL 2024): Open-source framework processing 5M+ evaluations/month for AWS, Microsoft, Databricks, Moody's. ([RAGAS Docs](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/))

**Four core metrics** (all 0-1, higher is better):
1. **Faithfulness** (generation): LLM-as-judge breaks answer into atomic claims, checks each against retrieved context. Does NOT require ground truth.
2. **Answer Relevancy** (generation): Reverse-generation approach. Judge generates hypothetical questions the answer could be answering, measures cosine similarity to actual question.
3. **Context Precision** (retrieval): Are relevant chunks ranked highly? Measures ordering quality.
4. **Context Recall** (retrieval): Does retrieved context contain all information needed? Only metric requiring ground-truth reference answer.

**Healthy benchmarks**: Context recall > 0.80, Context precision > 0.70, Faithfulness > 0.85, Answer relevance > 0.80.

**Diagnostic patterns**: High context recall + low faithfulness = the right chunks are there but the model is ignoring/distorting them (generation problem). Low faithfulness + high context recall is the most dangerous combination. Low answer relevancy + high faithfulness = retrieved context is not helping answer the actual question (retrieval problem disguised as generation problem).

**Additional metrics**: Context entity recall, answer correctness, answer similarity, aspect critique. For agentic RAG: Tool Call Accuracy, Agent Goal Accuracy, Topic Adherence.

### Benchmark Comparisons: MTEB / BEIR

**MTEB** (Massive Text Embedding Benchmark): 56+ tasks, superset of BEIR. **BEIR**: 18+ zero-shot retrieval datasets spanning scientific papers, financial docs, COVID-19 research. Key metric: NDCG@10. ([HuggingFace Leaderboard](https://huggingface.co/spaces/mteb/leaderboard))

**Top models (2024-2026)**:
| Model | MTEB Score | Parameters | Notes |
|-------|-----------|------------|-------|
| KaLM-Gemma3-12B | 72.32 | 11.76B | Current leader |
| NV-Embed (NVIDIA) | 69.32 | - | 2024 record |
| Gemini Embedding 2 | 68.32 | - | Multimodal (text, image, video, audio) |
| Cohere embed-v4 | 65.2 | - | |
| OpenAI text-3-large | 64.6 | - | |
| BGE-M3 | 63.0 | - | |
| Qwen3-Embedding-0.6B | 64.34 | 600M | Best small model |

**Evolution**: Pre-2024 embedding models were small encoder-only transformers (BERT/RoBERTa, <560M params, ~60 MTEB). E5-Mistral proved decoder-only LLMs make better embedding backbones. Dense retrieval now consistently outperforms BM25 by 15-25% on BEIR. Hybrid retrieval still adds 2-5% on top.

**Warning**: Benchmark leaders may not be best for your domain. A model tuned for retrieval may underperform on clustering. Always validate on held-out domain data after benchmark screening.

### Cost-Quality Tradeoffs

| Configuration | Cost/query | Latency | Quality |
|--------------|-----------|---------|---------|
| Naive RAG (dense only) | ~$0.001 | 200-500ms | Baseline |
| Hybrid (dense + BM25 + RRF) | ~$0.002 | 250-550ms | +7-15% NDCG |
| Hybrid + Rerank | ~$0.004 | 400-800ms | +17-25% Recall@5 |
| Agentic RAG (multi-step) | ~$0.01-0.06 | 2-30s | +33-52% accuracy |

Hidden cost: when retrieval quality is poor, the pipeline retries (reformulated query, another fetch, another round). Each retry multiplies token cost invisibly. Hash-based change detection for incremental updates reduces daily embedding spend by ~100x (500 calls instead of 50,000 for a 50K corpus with 1% daily change). ([LinkUp Blog](https://www.linkup.so/blog/the-real-cost-of-rag))

---

## 3. Distributed Resilience & State

### Vector DB Scaling: Sharding, Replication, Index Management

**Market context**: Global vector database market valued at ~EUR 1.8B in 2024, growing 25%+ annually. RAG is the primary adoption driver. ([DataCamp](https://www.datacamp.com/blog/the-top-5-vector-databases))

**Pinecone**: Serverless architecture (introduced 2024) auto-handles sharding, replication, load balancing. Proprietary indexing (graph + tree hybrid) achieves O(log n) for inserts and queries. Scales to billions of vectors with zero ops. Costs 3-5x more than alternatives. 150 QPS at 1ms latency, $70 per 50K vectors.

**Weaviate**: Native hybrid search (BM25 + dense fusion, first-class, not bolted on). GraphQL interface combines semantic similarity with structured filters across object relationships. Multi-node clusters with configurable replication.

**Qdrant**: Best latency self-hosted. 1,238 QPS at 3.5ms average latency for 1M vectors (1536 dims). Best for complex metadata filtering.

**Milvus/Zilliz**: Built for billion-scale. Best choice for 100M+ vectors where other systems falter.

**pgvector**: Scales to ~10M vectors cost-effectively within existing PostgreSQL stack. 141 QPS at 8ms latency. Requires careful HNSW parameter tuning at scale.

**Scale benchmarks**: At 1M vectors, all major DBs hit 95%+ recall with defaults. At 100M vectors, Pinecone and Weaviate maintain recall without tuning; pgvector requires optimization.

**Replication strategies**: Synchronous replication for strong consistency (higher write latency). Asynchronous for availability/performance (temporary stale reads possible). Multi-region for latency reduction and disaster recovery. ([The Neural Base](https://theneuralbase.com/vector-databases/qna/vector-database-replication-strategies/))

### Ingestion Pipelines at Scale

**Critical finding**: 80% of RAG failures trace back to ingestion and chunking, not the LLM. Most teams discover this after weeks of tuning prompts while retrieval quietly returns wrong context. ([Unstructured.io](https://unstructured.io/insights/rag-pipeline-challenges-from-data-ingestion-to-retrieval))

**Parsing**: Must preserve reading order, tables, headings, metadata. When parser drops table boundaries, merges columns, or misorders sections, retrieval returns plausible but incorrect passages. Microsoft Azure AI Foundry (July 2026) introduced boundary-aware chunking API with automatic deduplication. Early adopters report 40% fewer retrieval artifacts vs LangChain RecursiveCharacterTextSplitter.

**Chunking economics**: Smaller or proposition-based chunking creates 3-5x more vectors than recursive splitting. More vectors = more embedding calls, more storage, more index overhead, more retrieval compute. Chunking is an economic decision, not just an IR decision.

**Embedding throughput**: 10M documents at 500 tokens/chunk = 5B tokens. Parallelize across multiple API keys or GPU nodes, processing in batches of 100-1000. This constrains initial deployment timelines.

### Index Refresh Strategies

**Incremental sync** (production standard): Only reprocess changed objects. Requires a change detector using last-modified timestamps when trustworthy, content hashing as fallback. ([NStarX](https://nstarxinc.com/blog/from-data-lake-to-rag-factory-the-technical-view-building-incremental-embedding-pipelines-without-melting-your-cloud-bill/))

**Required metadata per chunk**: Stable document ID, stable chunk ID (for deterministic update/delete), source URI/path (auditability), created/modified timestamps (freshness management), permission attributes (retrieval-time filtering).

**Orchestration**: Nightly job checking document hashes for most pipelines. Event-driven ingestion (S3 event notifications, webhooks) for real-time sources. Tools: Apache Airflow (multi-step workflows), AWS Step Functions (serverless), SageMaker Pipelines.

**Deletion handling**: When source documents are deleted or access is revoked, corresponding chunks must be removed from the index. Stale embeddings from deleted SaaS records are a documented data leakage vector.

### Handling Stale or Outdated Documents

Embedding model generations move every 6-12 months with 10-20 point lifts on retrieval benchmarks. A stack indexed on a 2024-era model and never re-embedded is one or two generations behind by 2026. ([DEV Community](https://dev.to/mridul_nagpal_e33b6be1260/rag-in-production-the-failure-modes-nobody-warns-you-about-62i))

**Staleness test**: Run a measured eval against a current-generation embedding model on 50 hand-labeled queries. If recall@10 lifts by 5+ points, your index is stale and needs re-embedding.

### Multi-Source Retrieval: Federation

Three dominant patterns for multi-source architectures: ([Applied AI](https://www.applied-ai.com/briefings/enterprise-rag-architecture/))
1. **Unstructured-first**: Flatten structured records into text, serve through same vector+keyword index as documents.
2. **Knowledge-graph-first**: Traverse entity-relationship graph, return surrounding subgraph.
3. **Hybrid graph+document**: Link unstructured documents into graph by entity reference, run graph traversal followed by document retrieval.

**Intelligent routing**: Classifier determines query domain (finance, HR, engineering, legal) and routes to specialized RAG applications. Improves precision by 30-40% over monolithic approaches. ([Xenoss](https://xenoss.io/blog/enterprise-knowledge-base-llm-rag-architecture))

---

## 4. Enterprise Security & Governance

### Document-Level Access Control

In most enterprise RAG deployments, the retrieval engine operates without awareness of user permissions, document classification, or access governance. The security gap is in the retrieval engine, not the LLM. ([CSO Online](https://www.csoonline.com/article/4163888/securing-rag-pipelines-in-enterprise-saas.html), [Kiteworks](https://www.kiteworks.com/cybersecurity-risk-management/prevent-data-leakage-rag-pipelines/))

**Retrieval-native access control** (2026 standard): Enforce document-level permissions during retrieval. When similarity search executes, the vector DB must honor the querying user's access rights. If a user cannot view a document in the source CRM, the RAG system must not retrieve it. Implemented via RBAC (role-based) and ABAC (attribute-based) filtering at query time.

**Authorization metadata at ingestion**: Capture ACLs (owner, department, user/group entitlements, expiration) at ingestion time. Treat as primary retrieval filter. Build change detection so access revocations propagate to the index quickly -- delayed revocation is a common leakage avenue.

**OWASP LLM Top 10 (2025)**: Sensitive Information Disclosure jumped from #6 to #2. Vector and Embedding Weaknesses added as new category LLM08. ([Truto Blog](https://truto.one/blog/how-to-maintain-document-level-rbac-in-enterprise-rag-pipelines/))

### PII in Retrieved Documents

**Data minimization at ingestion**: Every document entering a RAG system should be treated as potentially sensitive until sanitized. Remove or pseudonymize PII (names, emails, IDs). Detect and strip encoded content (base64). Tools: Amazon Macie, Microsoft Presidio for automated PII detection and redaction. ([AWS Blog](https://aws-solutions-library-samples.github.io/ai-ml/securing-sensitive-data-in-rag-applications-using-amazon-bedrock.html))

**Embedding inversion attacks**: Vectors are not inherently secure. Sophisticated attacks can reverse-engineer vectors to reconstruct original sensitive text. ([Prompt Security](https://prompt.security/blog/the-embedded-threat-in-your-llm-poisoning-rag-pipelines-via-vector-embeddings))

**Output guardrails**: Deploy output filters to evaluate generated responses for regurgitated PII, toxic content, or anomalous behavior before delivery to users. Do not implicitly trust LLM output.

### Data Provenance and Lineage Tracking

**Comprehensive audit logging**: Every RAG event (ingestion, retrieval, modification, deletion) logged in real time with dual attribution (system + human). Critical for compliance, governance, and investigations.

**EU AI Act Article 12**: Organizations operating RAG in high-risk AI categories must maintain logs of system inputs/outputs for post-hoc auditing. Regulators will ask: which documents were retrieved, for which queries, with what controls. Enforcement deadline: August 2, 2026. Penalties: up to EUR 35M or 7% of global annual revenue.

**GDPR Article 28**: Documents surfaced through RAG oversharing that include personal data processed without valid Data Processing Agreement trigger unauthorized processing obligations with 72-hour breach notification.

### Preventing Data Leakage

**Cross-tenant contamination**: In multi-tenant SaaS, poor isolation can allow one customer to retrieve another's proprietary data via normal semantic search.

**RAG poisoning**: Attacker injects malicious content into enterprise knowledge base. Does not need access to AI model, vector store, or retrieval infrastructure -- only write access to any document source the KB ingests from.

**Zero-trust posture**: Security must be layered across ingestion (sanitization, ACLs), retrieval (permission filtering), and generation (output guardrails). Cannot rely solely on the LLM to behave safely.

**Cloud provider solutions**: AWS Bedrock (data redaction at storage + RBAC), Google Cloud Sensitive Data Protection (PII classification/redaction before chunking + Vertex AI IAM integration).

---

## 5. Production Failure Modes

### Retrieval Failure: No Relevant Documents Found

73% of RAG failures originate at the retrieval stage, not LLM generation. The defining trait of a failed production RAG system is that nothing crashes -- the pipeline keeps serving answers and dashboards show healthy latency while quality degrades silently. ([Towards AI](https://pub.towardsai.net/rag-is-not-enough-when-retrieval-augmented-generation-fails-in-production-9dd2a7aa92c1))

**Mitigation**: Retrieval-aware prompting: "Below are retrieved document excerpts. First, assess which excerpts are directly relevant. Then answer using only relevant excerpts. If none are relevant, say so." Also: set minimum relevance score thresholds and return "I don't have enough information" when threshold is not met.

### Context Poisoning: Adversarial Documents in the Index

Context window poisoning occurs when irrelevant or redundant content fills the context window, crowding out needed information. Sources: overly long system prompts, high chunk overlap (80% overlap documented to produce 70% duplicate retrieved content), growing conversation history, top-K prioritizing recall over precision. ([Digital Applied](https://www.digitalapplied.com/blog/rag-anti-patterns-7-failure-modes-2026-engineering-guide))

**Adversarial corpus poisoning**: CRCP (Chunk-aware and Rerank-Consistent Poisoning) framework jointly optimizes retrieval relevance, reranker consistency, and chunk-boundary robustness to generate locally self-contained adversarial passages. Retrieval granularity mismatch is a key factor: document-level adversarial signals fragment during chunking. ([ArXiv](https://arxiv.org/html/2606.11265))

**OWASP LLM Top 10 (July 2026)**: Added three RAG-specific threat vectors: prompt injection through retrieved documents, indirect context poisoning, temporal inconsistency attacks.

### Chunking Artifacts

Applying fixed-size chunking to mixed document types is the single most common RAG production failure. A compliance policy document chunked at 512 tokens splits in the middle of a clause. The model either answers incorrectly from incomplete information or "helpfully" completes the clause from pre-training data. ([DEV Community](https://dev.to/mridul_nagpal_e33b6be1260/rag-in-production-the-failure-modes-nobody-warns-you-about-62i))

**Fix**: Document-aware chunking using parsing layer that respects document structure. Microsoft Azure AI Foundry boundary-aware chunking API reports 40% fewer retrieval artifacts. Chunk on semantic boundaries, not character counts. Add reranking so top-k is by relevance, not raw vector distance. Store metadata for pre-search filtering.

### Embedding Model Changes Invalidating Indexes

Embedding model mismatch (index-time model differs from retrieval-time model) makes dot-product scoring meaningless. Models move every 6-12 months with 10-20 point benchmark lifts. ([Atlan](https://atlan.com/know/rag-accuracy-problems/))

**Mitigation**: Version-tag all indexes with the embedding model used. Run recall@10 eval on 50 queries against current-generation model. If 5+ point improvement, re-embed. Plan for periodic full re-indexing as an operational cost.

### Relevance Decay: Index Quality Degrading Over Time

**A formal taxonomy** (clawRxiv, 2024) identified 14 failure modes along three axes: retrieval-side failures dominate at low corpus quality (52% of errors), fusion-side failures dominate at high corpus quality (47%), pure generation hallucinations stabilize at 9-12% across regimes. ([clawRxiv](https://www.clawrxiv.io/abs/2604.02033))

Silent degradation: users feel the regression months before any team metric does. Continuous evaluation is essential -- "Validation of a RAG system is only feasible during operation."

### Citation Hallucination: Model Citing Wrong Sources

Even with perfect retrieval, GPT-4-class models fabricate details in 8-15% of responses. Citation enforcement reduces this to under 3%. Retrieved chunks containing contradictory information (different document versions or authors) cause the model to pick one, sometimes blend both, and present as settled fact without flagging the contradiction. ([ArXiv: FACTUM](https://arxiv.org/html/2601.05866v1))

### Query-Document Mismatch: Semantic Gap

When users phrase queries differently from how knowledge is stored, dense retrieval fails to bridge the gap. Particularly problematic for jargon-heavy domains where users may use informal language while documents use formal terminology. HyDE, query rewriting, and contextual retrieval (Anthropic) are the primary mitigations.

---

## 6. Enterprise System Design Scenarios

### Scenario 1: Enterprise Knowledge Base with Multi-Source Agentic RAG

**Problem**: Large enterprise (10,000+ employees) needs unified Q&A across Confluence wikis, SharePoint documents, Jira tickets, Slack threads, internal code repos, and CRM data. Users span engineering, sales, legal, and HR. Documents have varying access levels.

**Architecture**:

```
User Query
    |
[Query Complexity Classifier] -- routes by complexity
    |
    +-- Simple (70%) --> Advanced RAG Pipeline
    |     |-- Hybrid search (BM25 + dense + RRF)
    |     |-- Cross-encoder rerank (top-100 -> top-10)
    |     |-- Generate with inline citations
    |
    +-- Complex (30%) --> Agentic RAG Pipeline
          |-- [Intent Router] -- classifies domain
          |     |-- Engineering -> Code/Wiki sub-index
          |     |-- Sales -> CRM/Deal sub-index
          |     |-- Legal -> Policy/Contract sub-index
          |     |-- HR -> Benefits/Handbook sub-index
          |
          |-- [Query Decomposer] -- breaks into sub-queries
          |-- [Iterative Retrieval Loop] (IRCoT-style)
          |     |-- Per sub-query: retrieve -> reason -> retrieve
          |     |-- Max 5 iterations, 15 chunks cap
          |-- [CRAG Evaluator] -- assess retrieval quality
          |     |-- High confidence -> refine + generate
          |     |-- Low confidence -> web search fallback
          |     |-- Ambiguous -> merge refined + web
          |-- [Citation Verifier] -- NLI check per claim
          |-- Generate with verified citations
```

**Ingestion pipeline**:
- Source connectors for each system (Confluence API, SharePoint Graph API, Jira REST, Slack export)
- Document-aware parsing preserving structure (tables, headers, code blocks)
- Parent-child chunking: 2000-char parents, 400-char children
- Contextual retrieval (Anthropic method): prepend 50-100 token context per chunk
- ACL metadata captured at ingestion (department, role, classification)
- Incremental sync via content hashing, daily cron + event-driven for real-time sources
- Embedding: Voyage-4-large or Gemini Embedding for quality; text-embedding-3-small for cost-sensitive deployments

**Security layer**:
- Retrieval-time RBAC filtering using ACL metadata
- PII detection (Microsoft Presidio) at ingestion with redaction
- Output guardrails scanning for PII regurgitation
- Complete audit logging for EU AI Act compliance
- Multi-tenant isolation at vector DB level

**Monitoring**: RAGAS metrics (faithfulness > 0.85, context recall > 0.80) on sampled production queries. Weekly retrieval eval on 50 hand-labeled queries. Alert on faithfulness drops below 0.80.

**Cost estimate**: At 5,000 queries/day: ~$0.01-0.03/query average (blended simple + complex), $1,500-4,500/month compute + API costs. Vector DB (Weaviate or Qdrant self-hosted): $500-1,500/month for 10M chunks.

### Scenario 2: Regulatory Compliance Document Q&A with Citation Tracking

**Problem**: Financial services firm needs auditable Q&A over 50,000+ regulatory documents (SEC filings, internal policies, compliance manuals, regulatory guidance letters). Every answer must cite specific document sections. Responses used for audit preparation and compliance decisions. Zero tolerance for hallucination.

**Architecture**:

```
Compliance Query
    |
[Query Rewriter] -- standardize regulatory terminology
    |
[Hybrid Retrieval]
    |-- Dense (Voyage-4-large, fine-tuned on financial domain)
    |-- BM25 (critical for regulation numbers, section codes)
    |-- Metadata filter: document type, jurisdiction, effective date
    |-- RRF fusion -> top-100 candidates
    |
[Cross-Encoder Rerank] -- BGE-v2 or Cohere Rerank v3.5
    |-- Score against regulatory domain criteria
    |-- Top-10 passages
    |
[Quote-then-Answer Generation]
    |-- Step 1: Extract exact quotes from top-10 passages
    |-- Step 2: Synthesize answer using ONLY extracted quotes
    |-- Step 3: Format with inline citations [Doc ID, Section, Para]
    |
[NLI Verification Pipeline]
    |-- For each claim-citation pair:
    |     |-- NLI model classifies: entailment / contradiction / neutral
    |     |-- Flag any non-entailment citations
    |-- If any claim unsupported: regenerate or return "insufficient evidence"
    |
[Audit Logger]
    |-- Log: query, retrieved doc IDs, retrieved text, generated answer,
    |         citation verification results, user ID, timestamp
    |-- Immutable audit trail (append-only)
    |-- 7-year retention for regulatory compliance
```

**Chunking strategy**: Regulatory documents have clear hierarchical structure. Use section-aware chunking respecting regulation structure (Part -> Section -> Subsection -> Paragraph). Parent-child with regulation-specific boundaries. Preserve cross-references as metadata links.

**Citation requirements**:
- Every factual claim must cite: Document title, Section number, Paragraph number, Effective date
- Quote-then-answer prompting enforces evidence-first generation
- NLI verification achieves <3% citation hallucination rate
- Unsupported claims trigger re-generation or "insufficient evidence" response

**Temporal handling**: Regulations supersede each other. Metadata must include effective dates and supersession chains. Retrieval must prefer most current version unless user specifies historical query. Stale regulation detection via automated effective-date checks.

**Quality gates**:
- Faithfulness > 0.95 (higher bar than general enterprise)
- Context precision > 0.85
- Zero tolerance for citation-shaped hallucinations
- Monthly audit of 200 randomly sampled responses by compliance team
- Automated regression testing against known Q&A pairs from prior audits

**Cost**: Higher per-query ($0.03-0.08) due to reranking + NLI verification + detailed citation formatting. At 500 queries/day: $450-1,200/month. Justified by replacing hours of manual regulatory research per query.

---

## Sources

- [1] [ArXiv: Self-RAG (Asai et al., 2310.11511)](https://arxiv.org/abs/2310.11511) -- Self-reflection tokens for retrieve-on-demand
- [2] [ArXiv: CRAG (Yan et al., 2401.15884)](https://arxiv.org/abs/2401.15884) -- Corrective retrieval with web search fallback
- [3] [ArXiv: Adaptive-RAG (Jeong et al., 2403.14403)](https://arxiv.org/abs/2403.14403) -- Query complexity routing (NAACL 2024)
- [4] [ArXiv: RAPTOR (Sarthi et al., 2401.18059)](https://arxiv.org/abs/2401.18059) -- Recursive abstractive tree retrieval (ICLR 2024)
- [5] [ArXiv: FLARE (Jiang et al., 2305.06983)](https://arxiv.org/abs/2305.06983) -- Active retrieval via token confidence (EMNLP 2023)
- [6] [ArXiv: IRCoT (2212.10509)](https://arxiv.org/abs/2212.10509) -- Interleaving retrieval with chain-of-thought
- [7] [Anthropic Engineering: Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval) -- Chunk context prepending
- [8] [Microsoft Research: GraphRAG](https://www.microsoft.com/en-us/research/project/graphrag/) -- Knowledge graph enhanced retrieval
- [9] [GitHub: microsoft/graphrag](https://github.com/microsoft/graphrag) -- GraphRAG implementation
- [10] [GitHub: AkariAsai/self-rag](https://github.com/akariasai/self-rag) -- Self-RAG implementation
- [11] [GitHub: parthsarthi03/raptor](https://github.com/parthsarthi03/raptor) -- RAPTOR implementation
- [12] [LlamaIndex: Agentic RAG](https://blog.llamaindex.ai/agentic-rag-with-llamaindex-2721b8a49ff6) -- QueryEngineTool and agent routing
- [13] [LlamaIndex: Agentic Retrieval Guide](https://www.llamaindex.ai/blog/rag-is-dead-long-live-agentic-retrieval) -- Naive vs agentic comparison
- [14] [Weaviate: What Is Agentic RAG](https://weaviate.io/blog/what-is-agentic-rag) -- Control loop architecture
- [15] [Cohere Rerank API](https://docs.cohere.com/reference/rerank) -- Cross-encoder reranking service
- [16] [RAGAS Documentation](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/) -- RAG evaluation metrics
- [17] [MTEB Leaderboard (HuggingFace)](https://huggingface.co/spaces/mteb/leaderboard) -- Embedding model benchmarks
- [18] [Haystack: HyDE Documentation](https://docs.haystack.deepset.ai/docs/hypothetical-document-embeddings-hyde) -- Hypothetical document embeddings
- [19] [NirDiamant/RAG_Techniques (GitHub)](https://github.com/NirDiamant/RAG_Techniques) -- Query transformation implementations
- [20] [Digital Applied: Hybrid Search Reference 2026](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026) -- BM25 + vector + RRF benchmarks
- [21] [Guillaume Laforge: RRF in Hybrid Search](https://glaforge.dev/posts/2026/02/10/advanced-rag-understanding-reciprocal-rank-fusion-in-hybrid-search/) -- RRF formula and tuning
- [22] [Thread Transfer: RAG Reranking](https://thread-transfer.com/blog/2026-06-17-rag-reranking-llm-colbert/) -- ColBERT vs cross-encoder comparison
- [23] [CSO Online: Securing RAG Pipelines](https://www.csoonline.com/article/4163888/securing-rag-pipelines-in-enterprise-saas.html) -- Enterprise RAG security
- [24] [Kiteworks: RAG Data Leakage Prevention](https://www.kiteworks.com/cybersecurity-risk-management/prevent-data-leakage-rag-pipelines/) -- Zero-trust RAG architecture
- [25] [Truto: Document-Level RBAC for RAG](https://truto.one/blog/how-to-maintain-document-level-rbac-in-enterprise-rag-pipelines/) -- Access control implementation
- [26] [Prompt Security: Poisoning RAG via Embeddings](https://prompt.security/blog/the-embedded-threat-in-your-llm-poisoning-rag-pipelines-via-vector-embeddings) -- Embedding inversion attacks
- [27] [ArXiv: Attribution Techniques Survey (2601.19927)](https://arxiv.org/html/2601.19927v1) -- Citation and grounding methods
- [28] [ArXiv: FACTUM (2601.05866)](https://arxiv.org/html/2601.05866v1) -- Mechanistic citation hallucination detection
- [29] [ArXiv: RAG Failure Taxonomy (2604.02033)](https://www.clawrxiv.io/abs/2604.02033) -- 14 failure modes across 3 axes
- [30] [DEV Community: RAG Failure Modes](https://dev.to/mridul_nagpal_e33b6be1260/rag-in-production-the-failure-modes-nobody-warns-you-about-62i) -- Production failure patterns
- [31] [Digital Applied: RAG Anti-Patterns](https://www.digitalapplied.com/blog/rag-anti-patterns-7-failure-modes-2026-engineering-guide) -- 7 failure modes engineering guide
- [32] [SandBase: RAG Cost Structure](https://blog.sandbase.ai/rag-cost-structure-embedding-search-2026/) -- Cost breakdown per component
- [33] [TechNovice: Latency Budget for Production RAG](https://www.technovice.net/post/latency-budget-production-rag) -- Stage-level latency analysis
- [34] [Unstructured.io: RAG Pipeline Challenges](https://unstructured.io/insights/rag-pipeline-challenges-from-data-ingestion-to-retrieval) -- Ingestion pipeline best practices
- [35] [NStarX: Incremental Embedding Pipelines](https://nstarxinc.com/blog/from-data-lake-to-rag-factory-the-technical-view-building-incremental-embedding-pipelines-without-melting-your-cloud-bill/) -- Change detection and incremental updates
- [36] [Xenoss: Enterprise AI Knowledge Base](https://xenoss.io/blog/enterprise-knowledge-base-llm-rag-architecture) -- Multi-source agentic RAG architecture
- [37] [Applied AI: Enterprise RAG Architecture](https://www.applied-ai.com/briefings/enterprise-rag-architecture/) -- Practitioner's guide
- [38] [ArXiv: AgenticRAG for Enterprise KBs (2605.05538)](https://arxiv.org/html/2605.05538v1) -- Enterprise agentic retrieval
- [39] [LanceDB: Parent Document Retrieval](https://www.lancedb.com/blog/modified-rag-parent-document-bigger-chunk-retriever-62b3d1e79bc6) -- Small-to-big chunking
- [40] [ArXiv: CoRAG (2501.14342)](https://arxiv.org/pdf/2501.14342) -- Chain-of-retrieval augmented generation
- [41] [AWS: Securing Sensitive Data in RAG](https://aws-solutions-library-samples.github.io/ai-ml/securing-sensitive-data-in-rag-applications-using-amazon-bedrock.html) -- Bedrock guardrails and redaction patterns
- [42] [ArXiv: Corpus Poisoning under Chunking/Reranking (2606.11265)](https://arxiv.org/html/2606.11265) -- CRCP adversarial framework
