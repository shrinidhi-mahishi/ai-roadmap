# RAG

## Why It Matters
RAG matters even in a 1M-token world because larger context windows do not solve the three production problems that actually dominate enterprise systems: freshness, access control, and cost. If the source data changes hourly, if each user should only see their tenant's documents, or if the corpus is too large to stuff into every prompt, retrieval remains the right primitive.

In interviews, the strongest framing is that RAG is not "a vector DB attached to an LLM." It is a retrieval system plus a generation system with different failure modes, scaling knobs, and SLIs. Bigger context helps small corpora. It does not replace indexed search, ACL-aware retrieval, or citation-safe answering for large mutable knowledge bases.

## Mental Model
Think of RAG as a two-plane system:

- The ingest plane is the library back office. It parses source files, chunks them, stamps metadata, computes embeddings, and builds indexes.
- The query plane is the reference desk. It interprets the question, retrieves candidates, reranks them, and asks the model to answer only from grounded evidence.

That separation matters. Coupling ingest and query hurts both latency and freshness. A reindex should not block user-facing retrieval, and a hot query path should not depend on expensive parsing or graph extraction work.

The default production view is: parametric memory is general reasoning, retrieval is current evidence. RAG is how you join them without pretending the model "knows" your corpus.

## Architecture / Flow
```text
offline ingest:
  source docs -> parse -> chunk -> embed/sparse encode -> index -> alias swap

online query:
  user query -> authz filter -> rewrite if needed -> dense + BM25 retrieval
  -> fuse -> rerank -> grounded generation -> citations -> answer
```

A solid interview answer usually breaks the flow into two stages:

1. Ingest
   - Parse PDFs, HTML, docs, tickets, or code into clean text.
   - Choose chunking policy.
   - Stamp every chunk with `doc_id`, tenant, ACL, version, timestamps, and section metadata.
   - Build at least one dense index and one lexical index.

2. Query serving
   - Authenticate the caller first.
   - Push ACL and tenant filters before ANN retrieval, not after top-k.
   - Retrieve a broad candidate set.
   - Fuse and rerank.
   - Generate only from retrieved evidence, ideally with constrained citation IDs.

## Key Concepts
- Why RAG still matters:
  - Freshness: update indexes without retraining.
  - ACLs: retrieval can enforce tenant and role boundaries.
  - Cost: retrieving 5-10 passages is cheaper than sending 500 pages.
  - Auditability: you can log what evidence the answer used.

- Chunking is a first-order design choice:
  - Fixed windows are simple and predictable.
  - Recursive chunking reduces ugly sentence breaks.
  - Structure-aware chunking is better for headings, legal sections, or code blocks.
  - Contextual chunking prepends document-level context to each chunk and helps pronoun/entity recall.
  - Late chunking keeps document-level context inside dense embeddings, but it does not help lexical search.
  - Parent-child retrieval uses small chunks for precision and larger parents for synthesis.

- Embedding selection is not just "pick the highest leaderboard score":
  - Check dimension, context window, multilingual support, code support, and latency.
  - Pin model ID, dimension, similarity metric, and prompt template.
  - Any change in those should be treated as an index-schema change that forces re-embedding.

- Hybrid retrieval is the default:
  - Dense retrieval handles paraphrase and semantics.
  - BM25 or sparse retrieval catches SKUs, IDs, statute numbers, error codes, and exact product names.
  - Dense-only systems routinely miss the exact token strings businesses care about.

- Fusion and reranking:
  - RRF is safer when score scales differ.
  - Score fusion can win when magnitudes are meaningful and normalized.
  - Cross-encoder reranking is the standard second stage because it jointly reads query and passage.

- Agentic RAG:
  - Query rewrite, retrieve-grade-rewrite, and bounded fallbacks can improve hard cases.
  - The value comes from new evidence, not from more self-talk.
  - Always cap hops, retries, and wall-clock time.

- GraphRAG-class methods:
  - Use them for global or corpus-level questions such as themes, trends, or entity-centered multi-hop reasoning.
  - Do not pay graph indexing cost for FAQ-style fact lookups that hybrid + rerank already solves.

- Citation-safe generation:
  - The model should only cite IDs from the retrieved set.
  - Faithfulness and citation correctness are separate checks.

## Metrics and Formulas to Memorize
- `RRF(d) = sum_i 1 / (k + rank_i(d))`
- Production default for RRF: `k = 60`
- Common two-stage pattern: retrieve `50-150` candidates, rerank down to `5-20`
- Practical chunking default: `400-800` tokens with `10-20%` overlap
- Anthropic Contextual Retrieval top-20 failure rate:
  - baseline `5.7%`
  - contextual embeddings `3.7%`
  - contextual embeddings + BM25 `2.9%`
  - contextual embeddings + BM25 + rerank `1.9%`
- Small-corpus skip-RAG rule of thumb from Anthropic: below about `200k tokens` or about `500 pages`, stuffing or caching the corpus can be simpler than building full RAG
- Reference economics from local material: advanced RAG can land around `~$3 / 1k queries` on a cheap embed + rerank + mini-generation stack, but the exact number is workload-dependent
- GraphRAG extraction is roughly `~75%` of indexing cost
- LazyGraphRAG reports about `0.1%` of full GraphRAG index cost and more than `700x` cheaper global queries
- Agentic RAG often costs and delays about `2-10x` versus standard hybrid retrieval if you are not careful with loop caps

## Trade-offs and Failure Modes
- Stale index:
  weak CDC, failed alias flip, or lazy rebuilds produce grounded-looking but outdated answers.

- Embedding drift:
  changing model, dimension, or prompt without full re-embed silently collapses recall.

- Score mixing bugs:
  raw BM25 and cosine scores should not be naively added without normalization.

- ACL post-filtering:
  filtering after ANN retrieval both leaks and destroys recall. Push authz before retrieval.

- Over-retrieval:
  sending too many passages hurts cost and can trigger lost-in-the-middle degradation.

- Hallucinated citations:
  answers may look grounded while citing made-up chunk IDs unless citation outputs are constrained.

- Poisoned documents:
  unvalidated retrieved text can inject instructions or bad facts into the generation step.

- Graph overuse:
  global graph methods are expensive and often unnecessary for ordinary fact retrieval.

The interview-friendly answer is not "RAG always helps." It is "RAG helps when the knowledge is large, mutable, access-controlled, or auditable. Otherwise, simpler context engineering may win."

## Interview Q&A
**Q: Why does RAG still matter if models support 1M tokens?**  
A: Because long context does not solve freshness, per-tenant ACLs, or the economics of sending huge corpora on every turn. RAG solves those directly.

**Q: What is the default production retrieval stack?**  
A: Hybrid first-stage retrieval, RRF or normalized fusion, cross-encoder reranking, then citation-aware generation.

**Q: Why is dense-only retrieval usually not enough?**  
A: Dense search is weak on exact strings like SKUs, IDs, legal cites, and error codes. BM25 or sparse retrieval covers that gap.

**Q: How do you choose chunking?**  
A: Start with `400-800` token chunks plus overlap, then adjust based on evals. Move to contextual or late chunking only if recall failures justify the extra complexity.

**Q: When should I use GraphRAG?**  
A: When the task is global synthesis or entity-centered multi-hop reasoning across the corpus, not basic FAQ lookup.

**Q: What is the cleanest way to explain multi-tenancy in RAG?**  
A: ACL pushdown before retrieval. The retriever should only search the caller's allowed slice of the corpus.

**Q: What do you evaluate in RAG?**  
A: At least four things separately: retrieval quality, generation faithfulness, citation correctness, and latency/cost.

**Q: What is the biggest anti-pattern in interview answers?**  
A: Describing RAG as only a vector search problem. Production RAG is retrieval engineering plus grounding discipline.

## Sources
- Local anchors:
  - `ai-roadmap/final/06-rag.md`
  - `ai-roadmap/final/02-context-engineering.md`
  - `ai-roadmap/final/12-evaluation.md`
  - `ai-roadmap/consolidated_study_guide.md`
- External:
  - [Anthropic Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)
  - [Claude Cookbook: Contextual Retrieval](https://platform.claude.com/cookbook/capabilities/contextual-embeddings-guide)
  - [Microsoft GraphRAG Docs](https://github.com/microsoft/graphrag/blob/main/docs/index.md)
  - [SELF-RAG Repo](https://github.com/AkariAsai/self-rag)
  - [RAGAS Docs](https://docs.ragas.io/)
  - [DeepEval Repo](https://github.com/confident-ai/deepeval/)
