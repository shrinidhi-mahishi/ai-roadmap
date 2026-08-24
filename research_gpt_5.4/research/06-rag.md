# Research: RAG - Hybrid Search, Reranking, Agentic RAG, Graph RAG

**Date researched**: 2026-08-21
**Sources consulted**: 19

---

## 1. System Topology & Mechanics

The canonical `RAG` architecture couples a seq2seq generator with a non-parametric memory: a dense vector index over Wikipedia retrieved by `DPR`, with the generator marginalizing over top-`k` retrieved passages either once per sequence (`RAG-Sequence`) or once per output token (`RAG-Token`) ([Lewis et al., 2020](https://arxiv.org/html/2005.11401v4)). The original paper used a December 2018 Wikipedia dump split into `21M` disjoint `100-word` chunks and retrieved via approximate `MIPS` over a `FAISS` index with `HNSW` approximation ([Lewis et al., 2020](https://arxiv.org/html/2005.11401v4)).

`Hybrid retrieval` is now the mainstream production topology for enterprise RAG because it runs lexical and vector retrieval in parallel and fuses them afterward. Azure AI Search defines hybrid search as one request containing both full-text and vector queries, executed in parallel and merged with `RRF`; the lexical side is `BM25`, while the vector side can use `HNSW` or exhaustive `kNN` ([Azure hybrid search overview](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview)). This directly addresses the failure mode where vector search captures semantic similarity but misses exact identifiers such as product codes, names, dates, or specialized jargon that keyword search handles better ([Azure hybrid search overview](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview)).

`Reranking` is a second-stage topology layered on top of first-stage retrieval. Azure semantic ranker reranks an initial `BM25` or `RRF` result set with multilingual deep learning models adapted from Microsoft Bing and returns a new `@search.rerankerScore` in the range `0-4` ([Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview)). Cohere's `Rerank` API exposes the same topology more explicitly: the caller submits a query plus a candidate document list, and the service returns a reordered list with normalized `relevance_score` values in `[0,1]` ([Cohere Rerank API](https://docs.cohere.com/reference/rerank.mdx)). In practice, reranking only improves the candidates it is given; it cannot recover evidence that first-stage retrieval never surfaced ([Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview), [Cohere Rerank API](https://docs.cohere.com/reference/rerank.mdx)) [inferred].

`Late interaction` retrieval sits between single-vector dense retrieval and full cross-encoder reranking. `ColBERTv2` encodes queries and passages as multi-vector token representations and scores a passage by summing a per-query-token `MaxSim` over passage token vectors, preserving finer-grained matching than single-vector bi-encoders while staying scalable enough for retrieval ([ColBERTv2](https://arxiv.org/html/2112.01488v3)). ColBERTv2 also reports that its compressed late-interaction index reduces storage footprint by `6-10x` while preserving quality, making it architecturally relevant as a retrieval layer for higher-recall RAG systems ([ColBERTv2](https://arxiv.org/html/2112.01488v3)).

`Agentic RAG` changes the control plane more than the retrieval primitive. Azure AI Search's agentic retrieval is a multi-query pipeline: the application calls a knowledge base, an LLM optionally decomposes the request into subqueries, those subqueries run in parallel against knowledge sources, each subquery is semantically reranked, and the system returns merged grounding data plus optional references and an activity log ([Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)). `minimal` reasoning skips LLM query planning, while `low` and `medium` reasoning call an LLM for decomposition and source selection ([Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)).

The `LangGraph` agentic RAG tutorial exposes the same orchestration pattern at framework level: the agent can answer directly or call a retriever tool, then a follow-up grading node decides whether retrieved documents are relevant, whether the question should be rewritten, or whether to proceed to answer generation ([LangGraph agentic RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag)). Mechanically, that is no longer a fixed retrieve-then-generate DAG; it is a guarded retrieval loop with decision, retrieval, relevance grading, query rewrite, and answer nodes ([LangGraph agentic RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag)).

`GraphRAG` changes the index itself. Microsoft's GraphRAG pipeline slices documents into `TextUnits`, extracts entities, relationships, and optional claims, clusters the resulting graph with `Leiden`, and generates bottom-up summaries over the community hierarchy ([GraphRAG docs](https://microsoft.github.io/graphrag/), [From Local to Global](https://r.jordan.im/download/language-models/2404.16130v1.pdf)). Query-time modes are materially different from classic vector RAG: `Global Search` answers corpus-wide questions from community summaries, `Local Search` reasons around specific entities and neighbors, `DRIFT Search` combines entity-local exploration with community context, and `Basic Search` falls back to baseline vector search ([GraphRAG docs](https://microsoft.github.io/graphrag/)).

The core GraphRAG contribution is that global questions such as "what are the main themes in the dataset?" are treated as query-focused summarization rather than nearest-neighbor lookup. The paper's global-answer pipeline maps a question across community summaries in parallel, scores partial answers for helpfulness, and reduces the top-scoring partial answers into a final response ([From Local to Global](https://r.jordan.im/download/language-models/2404.16130v1.pdf)). This is a different execution topology from both classic RAG and agentic RAG: it is precomputed corpus abstraction plus query-time map-reduce, not only query-time retrieval.

## 2. Token Economics & NFR Metrics

> ⚠️ Limited public data available for comparable end-to-end `p50/p95/p99` latency across production RAG stacks. The public sources are much stronger on component limits, billing units, and benchmark quality than on stable percentile SLAs.

For `classic hybrid RAG`, the main NFR lever is how many candidates survive to reranking. Azure recommends `k=50` for vector queries when semantic ranker is used so the reranker gets enough inputs, and semantic ranker itself considers only the top `50` preranked results ([Azure hybrid search overview](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview), [Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview)). The reranker's summarization stage accepts up to `2,000` tokens per document input and emits a per-document summary string up to `2,048` tokens before rescoring ([Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview)).

That yields a useful first-order sizing formula for semantic-reranked hybrid search:

```text
rerank_token_load
  ~= subqueries * candidate_docs_per_subquery * avg_tokens_per_doc
```

([Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview)) [inferred]

For billing, Azure semantic ranker is usage-based with a `free` default plan and a monthly free allowance; the pricing page currently states `First 1k requests free per month`, after which the standard plan is pay-as-you-go ([Azure semantic billing](https://learn.microsoft.com/en-us/azure/search/semantic-how-to-enable-disable), [Azure AI Search pricing](https://azure.microsoft.com/en-us/pricing/details/search/)). Charges apply only when `queryType=semantic` is used with a non-empty search string ([Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview)).

For `agentic retrieval`, cost shifts from "one query, maybe one rerank" to "planned fan-out plus rerank plus optional answer synthesis." Azure explicitly bills agentic retrieval in two planes: Azure AI Search bills retrieval tokens consumed during subquery execution and semantic ranking, while Azure OpenAI bills query-planning and answer-synthesis tokens when an LLM is attached to the knowledge base ([Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)). Azure's own worked example assumes `2,000` agentic retrievals, `3` subqueries per plan, `50` reranked chunks per subquery, and `500` tokens per chunk, which totals `150M` reranking tokens; the example's hypothetical pricing then comes to `$3.30` for reranking plus `$1.02` for query planning, or `$4.32` combined ([Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)). The pricing page also states `First 50M tokens free per month` for agentic retrieval ([Azure AI Search pricing](https://azure.microsoft.com/en-us/pricing/details/search/)).

`Cohere Rerank` exposes operational constraints that are useful even if the production stack uses another reranker. The API recommends no more than `1,000` documents per request for optimal performance, defaults `max_tokens_per_doc` to `4096`, and returns billed `search_units` plus token metadata in the response ([Cohere Rerank API](https://docs.cohere.com/reference/rerank.mdx)). Cohere's best-practices page sets a hard upper bound of `10,000` documents per request, explains that `rerank-v4.0` uses a `32,768` token context window and `rerank-v3.5` uses `4096`, and notes that queries longer than `16,384` or `2,048` tokens respectively are truncated ([Cohere reranking best practices](https://docs.cohere.com/docs/reranking-best-practices.md)). Rate limits are public: `Rerank` is `10 req/min` on trial keys and `1,000 req/min` on production keys ([Cohere rate limits](https://docs.cohere.com/docs/rate-limits)).

For retrieval quality versus efficiency, `BEIR` remains the best broad public baseline. The original BEIR paper evaluated `18` datasets across `9` task families and concluded that `BM25` is a robust baseline, while `re-ranking` and `late-interaction` models achieve the best average zero-shot performance but at higher computational cost ([BEIR](https://doi.org/10.48550/arxiv.2104.08663)). The follow-up "Resources for Brewing BEIR" paper gives concrete aggregate `nDCG@10` numbers for first-stage retrievers: `BM25 0.429`, `uniCOIL 0.428`, `SPLADE 0.474`, `TAS-B 0.424`, `Contriever 0.448` across the full benchmark ([Resources for Brewing BEIR](https://doi.org/10.48550/arxiv.2306.07471)). The same paper reports that dense-sparse hybrid fusion improved over the best individual model on most datasets and beat BM25 on all but `2` datasets in that experiment ([Resources for Brewing BEIR](https://doi.org/10.48550/arxiv.2306.07471)).

For `GraphRAG`, public economics are dominated by index cost. Microsoft's LazyGraphRAG post states that full GraphRAG's LLM-heavy preprocessing can be prohibitive, and that `LazyGraphRAG` reduces indexing cost to `0.1%` of full GraphRAG, equal to vector RAG indexing cost, while achieving comparable global-query answer quality at more than `700x` lower query cost than GraphRAG Global Search in one compared configuration ([LazyGraphRAG](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/)). That is the clearest public signal that graph-enhanced RAG quality is no longer automatically worth the original GraphRAG preprocessing bill.

## 3. Distributed Resilience & State

RAG systems split state into at least two layers: `index-time state` and `query-time state`. The original RAG paper is explicit that the non-parametric memory can be replaced independently of the generator, which means knowledge refresh can happen by rebuilding or swapping the retrieval corpus rather than retraining the model ([Lewis et al., 2020](https://arxiv.org/html/2005.11401v4)). That separation is operationally valuable because it isolates corpus refresh from model deployment [inferred].

For `hybrid search`, resilience comes mostly from decoupled retrieval stages rather than durable workflows. Full-text and vector retrieval run in parallel, then `RRF` merges them; if one retrieval family underperforms for a query, the other can still contribute candidates ([Azure hybrid search overview](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview)). However, the second stage is brittle in a specific way: semantic ranker only reranks the top `50` results that the first stage already found, so recall errors upstream are locked in downstream ([Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview)).

`Rerank APIs` have the same bounded-state property. Cohere reranks only the documents the caller sends, recommends staying under `1,000` docs for performance, and can reject requests above the `10,000` document cap; this makes request shaping and first-stage candidate generation part of system reliability, not just ranking quality ([Cohere Rerank API](https://docs.cohere.com/reference/rerank.mdx), [Cohere reranking best practices](https://docs.cohere.com/docs/reranking-best-practices.md)).

`Agentic RAG` introduces explicit orchestration state. Azure's knowledge base stores retrieval configuration, knowledge-source references, reasoning effort, and optional LLM linkage, then returns not just grounding data but also source references and an activity log of what retrieval actions were taken ([Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)). This is better than opaque retrieve-then-generate flows for auditability, but the public docs do not describe checkpoint granularity, replay semantics, or durable recovery if a multi-subquery plan fails midway.

`GraphRAG` makes intermediate state first-class. The persisted artifacts are not just embeddings, but `TextUnits`, extracted entities and relations, graph communities, and community summaries ([GraphRAG docs](https://microsoft.github.io/graphrag/), [From Local to Global](https://r.jordan.im/download/language-models/2404.16130v1.pdf)). Those artifacts can be reused across many queries, which shifts work from query time to index time. The query path itself is naturally parallel: community-level partial answers are generated independently and then reduced into a global answer ([From Local to Global](https://r.jordan.im/download/language-models/2404.16130v1.pdf)).

The strongest public resilience insight for graph-based RAG is actually cost-state management: Microsoft now positions `LazyGraphRAG` as a way to defer almost all LLM work to query time and avoid expensive up-front summarization, while still using graph statistics and concept relationships to guide search ([LazyGraphRAG](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/)). In other words, GraphRAG's biggest public resilience improvement over 2024 is not better locking or checkpointing; it is a less fragile cost profile.

> ⚠️ Limited public data available for exact checkpoint semantics, distributed locking, leader election, or exactly-once side-effect guarantees in hybrid search, reranking services, Azure agentic retrieval, or GraphRAG OSS. Public materials focus on retrieval quality and pipeline composition, not workflow-engine internals.

## 4. Enterprise Security & Governance

The best-documented governance story in this source set comes from `Azure AI Search`, not from academic RAG papers. Microsoft positions Foundry IQ as a managed knowledge layer that transforms enterprise data into `permission-aware knowledge bases` for agents ([Azure hybrid search overview](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview)). Agentic retrieval's how-to docs add two concrete controls: retrieval can be called through a `retrieve` action or an `MCP` endpoint, and access can be enforced via the `Search Index Data Reader` role or API keys ([Query a knowledge base using retrieve or MCP](https://learn.microsoft.com/en-us/azure/search/search-agentic-retrieval-how-to-retrieve)). Knowledge base creation docs also expose custom properties for routing, source selection, and object encryption ([Create a knowledge base](https://learn.microsoft.com/en-us/azure/search/search-agentic-retrieval-how-to-create)).

Azure's documentation is also unusually explicit about compliance boundaries. The preview agentic retrieval docs warn that connections to third-party services can result in data processing or storage outside the Azure compliance boundary, and they put responsibility on the application owner to decide whether such data flow is acceptable and to add their own mitigations such as metaprompts, content filters, or other safety systems ([Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)).

For `semantic reranking`, one governance advantage is that Azure captions and semantic answers are `verbatim` extractions from indexed content, not newly generated text ([Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview)). That lowers one class of hallucination risk: the reranker can surface or highlight the wrong evidence, but it is not itself fabricating a new grounded answer string.

`Cohere Rerank` exposes operational metadata that is useful for audit and chargeback. Responses include billed `search_units` and token usage metadata, and the endpoint requires bearer-token authentication ([Cohere Rerank API](https://docs.cohere.com/reference/rerank.mdx)). Public docs do not, however, describe multi-tenant RBAC hierarchies, field-level filtering, or immutable audit-log schemas for rerank traffic.

`GraphRAG` and the original `RAG` literature are weak on enterprise governance. The papers and OSS docs explain retrieval quality, indexing, clustering, and summarization mechanics, but do not specify first-party patterns for `PII redaction`, `tool-level RBAC`, `sandboxing`, or immutable audit logs ([Lewis et al., 2020](https://arxiv.org/html/2005.11401v4), [GraphRAG docs](https://microsoft.github.io/graphrag/), [From Local to Global](https://r.jordan.im/download/language-models/2404.16130v1.pdf)).

> ⚠️ Limited public data available for built-in PII detection/redaction, record-level authorization propagation into retrieval results, sandbox isolation, and compliance-grade audit trails across RAG frameworks. The public material is dominated by relevance, cost, and benchmark claims rather than governance implementation details.

## 5. Production Failure Modes

`Naive vector RAG` fails on exact-match queries and on corpus-wide synthesis. Azure's hybrid-search docs call out cases such as product codes, specialized jargon, dates, and people's names where keyword retrieval often outperforms vector-only retrieval ([Azure hybrid search overview](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview)). Microsoft's GraphRAG paper argues that naive RAG also fails on global questions like "what are the main themes in the dataset?" because those are query-focused summarization tasks rather than localized retrieval tasks ([From Local to Global](https://r.jordan.im/download/language-models/2404.16130v1.pdf)).

`Reranker starvation` is a hard failure mode. Azure semantic ranker only sees the top `50` preranked results, and Cohere reranks only the documents the caller sends it ([Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview), [Cohere Rerank API](https://docs.cohere.com/reference/rerank.mdx)). If exact-match evidence or the crucial bridge passage is absent from that candidate pool, second-stage ranking cannot repair the miss [inferred].

`Agentic RAG` trades relevance for latency and cost. Microsoft states directly that agentic retrieval adds latency compared to a single-query pipeline because it performs LLM-based planning, subquery fan-out, parallel execution, reranking, and optional synthesis ([Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)). In production, the failure mode is over-decomposition: too many subqueries or too-broad knowledge-source fan-out can inflate token spend and still return noisy grounding [inferred].

`Long-context extraction degradation` is a documented GraphRAG indexing failure mode. In the GraphRAG paper, `600-token` chunks with zero gleanings extracted almost `2x` as many entity references as `2400-token` chunks in the HotPotQA analysis, showing that larger chunks can reduce extraction recall before retrieval even begins ([From Local to Global](https://r.jordan.im/download/language-models/2404.16130v1.pdf)). The same paper cites "lost in the middle" work as part of the reason simply increasing context windows is not a complete answer to corpus-wide summarization ([From Local to Global](https://r.jordan.im/download/language-models/2404.16130v1.pdf)).

`GraphRAG cost blow-up` was serious enough that Microsoft introduced a new variant to avoid it. LazyGraphRAG exists specifically because full GraphRAG's up-front LLM summarization can be too expensive for one-off queries, exploratory analysis, or streaming data; Microsoft claims LazyGraphRAG cuts indexing cost to `0.1%` of full GraphRAG and can match or beat competing methods across the cost-quality curve ([LazyGraphRAG](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/)).

`Benchmark mismatch` is another practical failure mode: optimizing on one retrieval family can overfit the wrong query class. BEIR shows that BM25 remains strong and that dense models can underperform badly on some domains, especially specialized ones ([BEIR](https://doi.org/10.48550/arxiv.2104.08663), [Resources for Brewing BEIR](https://doi.org/10.48550/arxiv.2306.07471)). BenchmarkQED shows the same query-class effect at system level: GraphRAG Global performs relatively better on global questions, vector RAG performs relatively better on local questions, and LazyGraphRAG was designed to close that gap across the spectrum ([BenchmarkQED](https://www.microsoft.com/en-us/research/blog/benchmarkqed-automated-benchmarking-of-rag-systems/)).

One notable public benchmark claim is that LazyGraphRAG outperformed every comparison condition using the same generative model, winning all `96` head-to-head comparisons in the reported BenchmarkQED experiment, while even `1M`-token vector RAG did not close the gap in most comparisons ([BenchmarkQED](https://www.microsoft.com/en-us/research/blog/benchmarkqed-automated-benchmarking-of-rag-systems/)). That does **not** mean vector RAG is obsolete; it means long context alone does not reliably solve global sensemaking.

## 6. Enterprise System Design Scenarios

### 6.1 Decision matrix

| Approach | Best fit | Strongest documented strengths | Main trade-offs |
| --- | --- | --- | --- |
| `Hybrid search` | Enterprise assistants that mix fuzzy semantic lookup with exact identifiers | Parallel `BM25` + vector retrieval with `RRF`, filters/facets, multilingual vector search, semantic rerank compatibility ([Azure hybrid search overview](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview)) | Reranking still depends on first-stage recall; no native multi-step reasoning |
| `Hybrid + reranker` | High-precision citation-heavy QA over prose documents | L2 reranking over top `50`, captions/answers, exact score surfaces, public billing model ([Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview), [Azure semantic billing](https://learn.microsoft.com/en-us/azure/search/semantic-how-to-enable-disable)) | Extra latency/cost; reranker cannot rescue missing evidence |
| `Agentic RAG` | Multi-part questions, conversational retrieval, source routing | Query planning, parallel subqueries, merged grounding, references, activity log, MCP exposure ([Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview), [Query a knowledge base using retrieve or MCP](https://learn.microsoft.com/en-us/azure/search/search-agentic-retrieval-how-to-retrieve)) | Higher latency and token variance; more control-plane complexity |
| `GraphRAG` | Corpus-wide sensemaking over long narrative collections | Entity/relationship graph, hierarchical communities, global/local/drift query modes, better comprehensiveness/diversity for global questions ([GraphRAG docs](https://microsoft.github.io/graphrag/), [From Local to Global](https://r.jordan.im/download/language-models/2404.16130v1.pdf)) | Heavy indexing cost and prompt-tuning burden |
| `LazyGraphRAG` | Global plus local reasoning where GraphRAG quality is desired without GraphRAG preprocessing cost | Indexing cost equal to vector RAG and `0.1%` of full GraphRAG, reported `>700x` cheaper global queries at comparable quality in one setup ([LazyGraphRAG](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/)) | Newer and less standardized; public evidence is mostly Microsoft-authored |

### 6.2 Recommended deployment patterns

**Pattern A: SaaS copilot over manuals, tickets, and product data**

Start with `hybrid retrieval + semantic reranking`, not graph indexing. The documented reason is simple: hybrid search covers exact-match fields like SKU names and dates while still retrieving semantically related prose, and semantic ranker adds a high-signal L2 rerank over the combined candidate set ([Azure hybrid search overview](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview), [Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview)).

**Pattern B: Analyst assistant for complex multi-part questions across many sources**

Use `agentic retrieval` when questions naturally decompose into subproblems, when conversation history changes retrieval intent, or when source references and execution logs matter operationally ([Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)). A framework-level implementation like `LangGraph` is the right fit when you want to own the retrieval-decision loop, relevance grading, and rewrite logic instead of delegating them to a managed service ([LangGraph agentic RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag)).

**Pattern C: Research or intelligence workflows over large narrative corpora**

Use `GraphRAG` when the primary questions are global, thematic, or connective rather than fact lookup. The public GraphRAG paper shows gains over naive RAG on comprehensiveness and diversity for datasets in the `~1M token` range, which is exactly the regime where "retrieve top passages" stops being enough ([From Local to Global](https://r.jordan.im/download/language-models/2404.16130v1.pdf)).

**Pattern D: Cost-constrained global reasoning**

Use `LazyGraphRAG` if full GraphRAG indexing is too expensive or the corpus changes too often. Microsoft's current public position is that LazyGraphRAG is the new quality-cost frontier for mixed local/global workloads ([LazyGraphRAG](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/), [BenchmarkQED](https://www.microsoft.com/en-us/research/blog/benchmarkqed-automated-benchmarking-of-rag-systems/)).

### 6.3 Capacity-planning heuristics

Useful first-order formulas:

```text
hybrid_query_cost
  ~= lexical_query
   + vector_query
   + optional_semantic_rerank
```

([Azure hybrid search overview](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview), [Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview)) [inferred]

```text
agentic_rag_rerank_tokens
  ~= subqueries * reranked_docs_per_subquery * avg_doc_tokens
```

([Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)) [inferred]

```text
query_latency
  ~= planning_latency
   + max(parallel_retrieval_branches)
   + reranking_latency
   + answer_synthesis_latency
```

([Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview), [LangGraph agentic RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag)) [inferred]

Operational heuristics supported by public limits:

- If you rely on Azure semantic ranker, provision retrieval so at least `50` strong candidates reach reranking; otherwise the L2 ranker is underfed ([Azure hybrid search overview](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview)).
- If you rely on external rerank APIs, treat request fan-out as a capacity bottleneck: Cohere recommends `<=1000` docs per request and exposes a `1000 req/min` production limit ([Cohere Rerank API](https://docs.cohere.com/reference/rerank.mdx), [Cohere rate limits](https://docs.cohere.com/docs/rate-limits)).
- If you need cross-domain robustness, benchmark against `BM25` and at least one sparse or hybrid baseline. BEIR evidence does not support assuming dense-only retrieval will generalize best ([BEIR](https://doi.org/10.48550/arxiv.2104.08663), [Resources for Brewing BEIR](https://doi.org/10.48550/arxiv.2306.07471)).

### 6.4 Strongest practical conclusions

1. `Hybrid retrieval + reranking` is the current production default because it is the most defensible answer to the dense-versus-lexical trade-off, and public docs expose concrete limits and billing knobs.
2. `Agentic RAG` is best understood as a control-plane upgrade over hybrid RAG, not a replacement for first-stage retrieval quality.
3. `GraphRAG` is strongest when the user asks corpus-level synthesis questions that no single passage can answer.
4. `LazyGraphRAG` is the most important recent shift in public RAG architecture because it weakens the old argument that graph-enhanced RAG is too expensive to operationalize.

## Sources

- [1] https://arxiv.org/html/2005.11401v4 - Original RAG paper with architecture, retriever/generator mechanics, and experimental setup.
- [2] https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview - Azure AI Search hybrid retrieval mechanics, BM25/vector parallelism, and RRF fusion.
- [3] https://learn.microsoft.com/en-us/azure/search/semantic-search-overview - Azure semantic reranking pipeline, top-50 limit, token limits, and score semantics.
- [4] https://learn.microsoft.com/en-us/azure/search/semantic-how-to-enable-disable - Azure semantic ranker billing plans and free-vs-standard behavior.
- [5] https://azure.microsoft.com/en-us/pricing/details/search/ - Azure AI Search pricing page with current free allowances for semantic ranker and agentic retrieval.
- [6] https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview - Azure agentic retrieval architecture, reasoning effort modes, and cost example.
- [7] https://learn.microsoft.com/en-us/azure/search/search-agentic-retrieval-how-to-retrieve - Retrieve action, MCP endpoint, and access-control prerequisites for querying knowledge bases.
- [8] https://learn.microsoft.com/en-us/azure/search/search-agentic-retrieval-how-to-create - Knowledge-base creation details including routing, source selection, and object encryption.
- [9] https://docs.langchain.com/oss/python/langgraph/agentic-rag - LangGraph agentic RAG tutorial with retrieval decision, relevance grading, and rewrite loop.
- [10] https://docs.cohere.com/reference/rerank.mdx - Cohere Rerank API contract, request/response fields, token metadata, and recommendations.
- [11] https://docs.cohere.com/docs/rate-limits - Cohere public rate limits, including Rerank trial and production limits.
- [12] https://docs.cohere.com/docs/reranking-best-practices.md - Cohere reranker document caps, context windows, and truncation behavior.
- [13] https://doi.org/10.48550/arxiv.2104.08663 - BEIR benchmark paper on zero-shot retrieval quality and efficiency trade-offs.
- [14] https://doi.org/10.48550/arxiv.2306.07471 - Follow-up BEIR paper with aggregate model scores and dense-sparse hybrid observations.
- [15] https://arxiv.org/html/2112.01488v3 - ColBERTv2 late-interaction retrieval architecture and compression results.
- [16] https://microsoft.github.io/graphrag/ - GraphRAG documentation covering index pipeline and query modes.
- [17] https://r.jordan.im/download/language-models/2404.16130v1.pdf - GraphRAG paper with global-search mechanics, chunking observations, and evaluation results.
- [18] https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/ - Microsoft Research post on LazyGraphRAG cost-quality trade-offs.
- [19] https://www.microsoft.com/en-us/research/blog/benchmarkqed-automated-benchmarking-of-rag-systems/ - BenchmarkQED evaluation methodology and LazyGraphRAG comparison results.
