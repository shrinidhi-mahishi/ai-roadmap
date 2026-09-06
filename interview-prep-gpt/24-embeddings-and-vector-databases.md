# Embeddings and Vector Databases

## Why It Matters
Embeddings are not a drop-in utility. In production they are part of the index schema: model ID, dimensions, normalization rule, chunk template, and similarity metric all have to stay aligned. Change one and you usually owe yourself a re-embed or reindex.

In interviews, the strongest answer is that retrieval quality depends at least as much on chunking, filtering, and evaluation as on which vector database logo you pick. Public cross-vendor QPS claims are limited and workload-specific, so lean on workload shape, tenancy, and ops model instead of headline benchmarks.

## Mental Model
Think of an embedding index like a typed secondary index for meaning:
- text -> chunker/template -> vector -> ANN structure -> rerank -> answer
- schema drift at any stage quietly breaks recall
- dense retrieval gives semantic recall; lexical retrieval covers exact strings

The useful split is:
- the embedder defines the geometry
- the ANN index defines the speed/recall trade-off
- metadata and ACL indexes define who can see what

## Architecture / Flow
```text
offline:
  docs -> parse -> chunk -> stamp metadata/ACL -> embed -> normalize
       -> build ANN + lexical indexes -> alias/version swap

online:
  query -> authz filter -> embed -> ANN + BM25/sparse retrieval
        -> fuse -> rerank -> grounded answer
```

Pin an index version to at least `embed_model + dims + normalization + similarity_metric + chunker_version + template_version`. Treat that tuple as one artifact.

## Key Concepts
- Model selection matrix:
  - general text, code, multilingual, and multimodal embedders behave differently
  - compare context length, latency, hosting model, dim count, and price, not just leaderboard rank
- Evaluate retrieval, not just embeddings:
  - MTEB is a useful prior, not a release gate
  - score on your own corpus with `Recall@k`, `MRR@10`, and `nDCG@10`
- Similarity metric choice:
  - cosine is common when vectors are normalized
  - dot product is equivalent for L2-normalized vectors and is often simpler operationally
  - L2 is fine when the model and index expect it, but do not switch casually
- HNSW intuition:
  - layered proximity graph
  - excellent recall/latency trade-off for read-heavy workloads
  - expensive in RAM and slower to build/update than simpler indexes
- IVF / IVFFlat / IVFPQ intuition:
  - cluster vectors into lists, then search only nearby lists
  - faster and smaller at scale, but requires training and tuning `nlist` and `nprobe`
- Quantization:
  - scalar or int8 is the safer first compression step
  - binary and PQ buy much bigger memory savings but usually need oversampling and rescoring
- Filtering and multi-tenancy:
  - push tenant and ACL filters before ANN retrieval
  - post-filter ACLs are both unsafe and bad for recall on selective queries
- Vector DB comparison lens:
  - Pinecone: managed ops, namespaces, good default for serverless teams
  - Qdrant: strong filterable HNSW, named vectors, quantization, open-source control
  - pgvector: best when vectors live beside relational data and you want SQL joins, ACID, and RLS
- "When is Postgres enough?":
  - keep `pgvector` when scale is modest and relational locality dominates
  - move to a dedicated vector DB when RAM pressure, independent scaling, or retrieval throughput becomes the bottleneck

## Metrics and Formulas to Memorize
- `cosine_similarity(a, b) = (a . b) / (||a|| ||b||)`
- `cosine_distance = 1 - cosine_similarity`
- If vectors are L2-normalized, cosine ranking and dot-product ranking are equivalent
- Retrieval metrics:
  - `MRR@k = (1 / |Q|) * sum_q (1 / rank_q)` for the first relevant hit within `k`
  - `nDCG@k = DCG@k / IDCG@k`
- OpenAI `text-embedding-3-small`:
  - `1536` dims
  - `8192` max input
  - `62.3%` MTEB
  - `$0.02 / 1M` tokens
- OpenAI `text-embedding-3-large`:
  - `3072` dims
  - `8192` max input
  - `64.6%` MTEB
  - `$0.13 / 1M` tokens
  - can be shortened to `256` dims and still beat full `ada-002` on MTEB
- Voyage 4-series:
  - `32k` context
  - `1024` default dims with `256/512/2048` options
  - supports `int8`, `uint8`, `binary`, `ubinary`
- Cohere `embed-v4.0`:
  - `128k` context
  - `256/512/1024/1536` dims
  - multimodal input
- Qdrant quantization anchors:
  - scalar about `4x`
  - binary up to `32x`
  - PQ up to `64x` compression
- HNSW RAM anchors such as `2-12 KB` per vector appear in public vendor docs, but treat them as workload-specific rather than universal

## Trade-offs and Failure Modes
- Chasing one MTEB average and never checking real retrieval slices
- Changing model, dimension, prompt template, or metric without a re-embed plan
- Forgetting normalization and silently getting wrong cosine vs dot behavior
- Applying ACLs after ANN retrieval and collapsing recall for rare-tenant queries
- Treating HNSW as "free" and discovering too late that build time and RAM are the bottleneck
- Leaving IVF under-tuned with low `nprobe`, then blaming the embedder
- Turning on aggressive quantization without oversampling or rescoring
- Keeping everything in `pgvector` after the primary database is already the scale ceiling
- Believing vendor headline QPS claims without matching your filter selectivity, chunk counts, and tail-latency needs

## Interview Q&A
**Q: Why are embeddings part of the index schema, not a swappable implementation detail?**  
A: Because model ID, dimensions, normalization, chunk template, and similarity metric jointly define the vector space. Change any of them and previously stored vectors are no longer directly comparable.

**Q: Cosine, dot product, or L2?**  
A: Use the metric the model and index were designed for. If vectors are normalized, cosine and dot product give the same ranking; dot is often simpler operationally. L2 is fine when the model is trained for it, but do not mix metrics casually.

**Q: HNSW or IVF?**  
A: HNSW is the default for read-heavy, high-recall ANN. IVF is the scale and memory lever when you can afford training and tuning. IVFPQ adds more compression at additional recall risk.

**Q: How do you evaluate an embedder?**  
A: Not by one leaderboard row. Run retrieval evals on your own corpus and business slices with `Recall@k`, `MRR@10`, and `nDCG@10`, then include latency and cost.

**Q: When is `pgvector` enough?**  
A: When vectors live next to relational data, scale is modest, and SQL joins or RLS matter more than independent vector-database scaling.

**Q: Pinecone vs Qdrant vs `pgvector` in one sentence each?**  
A: Pinecone for managed serverless simplicity, Qdrant for filterable open-source vector search with strong quantization support, and `pgvector` when SQL locality and transactional consistency win.

**Q: What is the cleanest multi-tenant answer?**  
A: ACL pushdown before retrieval: namespaces, payload filters, or RLS enforced ahead of ANN. Post-filtering is both insecure and bad for recall.

**Q: Biggest interview anti-pattern here?**  
A: Treating vector search as only "pick a vendor and top-k." The serious answer covers schema versioning, filters, reranking, and evaluation.

## Sources
- Local anchors:
  - `ai-roadmap/interview-prep-gpt/01-rag.md`
  - `ai-roadmap/interview-prep-gpt/03-caching.md`
  - `ai-roadmap/final/ai-concepts/06-rag.md`
  - `ai-roadmap/final/ai-concepts/12-evaluation.md`
  - `ai-roadmap/consolidated_study_guide.md`
- External:
  - [MTEB leaderboard docs](https://docs.mteb.org/get_started/usage/leaderboard/)
  - [OpenAI embeddings guide](https://developers.openai.com/api/docs/guides/embeddings)
  - [OpenAI embedding models launch](https://openai.com/index/new-embedding-models-and-api-updates/)
  - [HNSW paper](https://arxiv.org/abs/1603.09320v4)
  - [Faiss indexes wiki](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes)
  - [Pinecone hybrid search docs](https://docs.pinecone.io/guides/search/hybrid-search)
  - [Qdrant hybrid search docs](https://qdrant.tech/documentation/search/text-search/hybrid-search/)
  - [Qdrant quantization docs](https://qdrant.tech/documentation/manage-data/quantization/)
  - [pgvector README](https://github.com/pgvector/pgvector?tab=readme-ov-file)
