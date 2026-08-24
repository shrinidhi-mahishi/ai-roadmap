# Research: RAG - Hybrid Search, Reranking, Agentic RAG, Graph RAG

**Date researched**: 2026-08-21
**Sources consulted**: 38

## Scope and evidence labels

This brief treats retrieval-augmented generation (RAG) as a production information system, not as the single operation "embed a question and paste the nearest chunks into a prompt." It covers hybrid search, reranking, Agentic RAG, and Graph RAG, including their mechanics, economics, distributed state, security, failure modes, and enterprise architecture. Plain factual claims are sourced from primary papers or first-party documentation. `[inferred]` marks an engineering recommendation derived from the cited evidence. Historical benchmark results are identified with their workload and should not be read as current vendor comparisons.

## 1. System Topology & Mechanics

### The common pipeline

The original RAG formulation combines a model's parametric memory with an explicit non-parametric corpus retrieved at inference time. Its important architectural contribution is not a particular vector database: it makes knowledge independently updateable and can expose provenance. [[1]](https://arxiv.org/abs/2005.11401)

A production RAG path normally contains two asynchronously coupled planes:

```text
INGESTION PLANE
source -> parse/OCR -> normalize -> classify/ACL -> chunk
       -> sparse index + embedding/late-interaction index
       -> optional entity/relationship/community index
       -> versioned publication

QUERY PLANE
identity + request -> policy/filter -> query analysis
                   -> candidate retrieval/fusion
                   -> rerank -> context assembly
                   -> generate/abstain -> claim/citation checks -> response
```

The source system remains authoritative. Search, vector, and graph structures are derived materialized views. That distinction controls deletion, access revocation, rebuild, and disaster recovery.

### Hybrid search: widen candidate recall

Hybrid retrieval combines complementary signals rather than assuming one representation dominates every query:

- **Lexical retrieval**, commonly BM25, rewards discriminative term matches and remains strong for identifiers, error codes, names, quotations, and rare terminology. BM25 descends from the probabilistic relevance framework and includes term-frequency saturation and document-length normalization. [[2]](https://doi.org/10.1561/1500000019)
- **Dense retrieval** encodes the query and passage independently and searches by vector similarity. DPR demonstrated that a dual encoder could outperform a strong Lucene-BM25 baseline by 9-19 absolute points in top-20 passage-retrieval accuracy on its 2020 open-domain QA workloads; this is historical task-specific evidence, not a universal dense-versus-BM25 guarantee. [[3]](https://arxiv.org/abs/2004.04906)
- **Approximate nearest-neighbor (ANN) search** trades exactness for latency and memory. HNSW builds a multi-layer proximity graph with tunable construction/search parameters; changing those parameters changes recall, build time, query time, and memory. [[4]](https://arxiv.org/abs/1603.09320)
- **Sparse learned retrieval and late interaction** occupy useful middle ground. ColBERTv2 retains token-level late interaction rather than collapsing each document to one vector; its paper reports a 6-10x reduction in late-interaction space footprint versus its predecessor while preserving strong effectiveness on its evaluated benchmarks. [[8]](https://arxiv.org/abs/2112.01488)

Elastic's current first-party guidance defines hybrid search as full-text plus vector search in one request and recommends Reciprocal Rank Fusion (RRF) as a default fusion method. [[5]](https://www.elastic.co/docs/solutions/search/hybrid-search) RRF combines ranks without assuming that BM25 scores, cosine similarities, and other scores share a calibrated scale:

```text
RRF(d) = sum over retrievers r of 1 / (k + rank_r(d))
```

The original SIGIR paper reported that RRF exceeded the compared Condorcet, CombMNZ, and best automated individual runs by roughly 4-5% on average across its TREC experiments. That is evidence for a robust fusion baseline, not a fixed gain for modern RAG corpora. [[6]](https://research.google/pubs/reciprocal-rank-fusion-outperforms-condorcet-and-individual-rank-learning-methods/) Elastic documents the current formula and notes implementation constraints such as unsupported `scroll`, `sort`, and `rescore` combinations in its RRF retriever. [[7]](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion)

`[inferred]` A dependable initial design is parallel BM25 and dense retrieval with identical authorization filters, union by stable chunk ID, RRF fusion, then a second-stage reranker. Preserve per-retriever ranks and scores for diagnosis; returning only the fused list hides which retrieval path failed.

```python
async def hybrid_candidates(query, principal, k_each=100):
    acl = compile_server_side_acl(principal)
    lexical, dense = await gather(
        bm25.search(query, filter=acl, limit=k_each),
        vectors.search(embed(query), filter=acl, limit=k_each),
    )
    return reciprocal_rank_fuse(
        [lexical, dense], key=lambda hit: hit.chunk_id, rank_constant=60
    )
```

The `60` is an explicit starting parameter, not a universal optimum. Tune branch depth, fusion constant, field weights, ANN search effort, and final `k` on judged queries from the target domain.

### Reranking: improve precision inside the candidate set

A first-stage retriever must score millions of items cheaply, so it uses limited query-document interaction. A reranker spends more compute on tens or hundreds of candidates:

- A **cross encoder** jointly attends to the query and each candidate. The BERT passage-reranking paper established the now-common pattern of using a pretrained transformer as a query-passage relevance classifier. [[9]](https://arxiv.org/abs/1901.04085)
- A **listwise LLM reranker** compares several candidates together and can follow richer ranking instructions, but consumes more tokens and can be nondeterministic.
- A **hosted rerank API** externalizes model serving. Cohere's current documentation lists `rerank-v4.0-pro` for quality and `rerank-v4.0-fast` for latency/throughput, alongside v3.5; query plus document content counts against the model's per-document context. [[10]](https://docs.cohere.com/docs/rerank) Its best-practices page documents automatic chunking and model-specific query truncation, which can silently change the unit being scored. [[11]](https://docs.cohere.com/v1/docs/reranking-best-practices)

Reranking cannot recover a relevant document omitted by candidate generation. Azure AI Search makes this boundary concrete: its semantic ranker reranks only the top 50 results from BM25, vector, or RRF retrieval and trims long configured fields before ranking. [[12]](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview) Thus evaluate candidate recall before optimizing reranker nDCG.

```text
corpus -> retrieve 100 per branch -> fuse 150 unique candidates
       -> rerank top 50 -> context-select top 5-12
       -> generate with stable source IDs
```

`[inferred]` Pass chunks, not entire documents, when the reranker limit would truncate the evidence-bearing region. Carry parent document and adjacency metadata so context assembly can add neighboring chunks only after relevance is established.

### Agentic RAG: make retrieval a bounded decision process

Agentic RAG adds a controller that can decide whether, where, and how often to retrieve. Common actions include:

1. classify the request and decide whether retrieval is needed;
2. decompose a multi-part question;
3. select one or more knowledge sources;
4. rewrite or expand subqueries;
5. run lexical, vector, graph, SQL, or web retrieval;
6. grade evidence sufficiency and conflict;
7. retry within a budget, ask a clarifying question, or abstain;
8. synthesize and verify claims against source IDs.

Research systems instantiate this in different ways:

- **Self-RAG** trains a model to emit reflection tokens that decide when to retrieve and critique relevance, support, and generation quality. It is not merely an application loop around an arbitrary chat model. [[14]](https://arxiv.org/abs/2310.11511)
- **Corrective RAG (CRAG)** adds a retrieval evaluator and corrective actions when initially retrieved documents are weak, including knowledge refinement and web search in the paper's design. [[15]](https://arxiv.org/abs/2401.15884)
- **Adaptive-RAG** trains a smaller classifier to route questions among no-retrieval, single-step, and iterative strategies based on estimated complexity. [[16]](https://arxiv.org/abs/2403.14403)
- **HyDE** generates a hypothetical document, embeds it, and retrieves nearby real documents. The generated text may contain false details, so it is a retrieval representation and must never be presented as evidence. [[25]](https://arxiv.org/abs/2212.10496)

Azure AI Search's current agentic retrieval product provides a concrete managed implementation: an LLM can decompose queries, select knowledge sources, execute subqueries, unify/rerank results, and optionally perform an iterative pass. Its `minimal` effort bypasses LLM query planning, while higher effort spends more model work; semantic ranking is part of the pipeline. [[17]](https://learn.microsoft.com/en-us/azure/search/search-agentic-retrieval-concept) Knowledge sources can be indexed or remote, and current documentation includes search indexes, storage, SharePoint, Fabric, MCP, and web sources with differing GA/preview status and compliance boundaries. [[18]](https://learn.microsoft.com/en-us/azure/search/agentic-knowledge-source-overview)

`[inferred]` Implement the controller as an explicit state machine with budgets, not an unbounded `while not enough` loop:

```python
state = {
    "request_id": request_id,
    "principal": principal_snapshot,
    "questions": [user_query],
    "evidence": {},
    "retrieval_attempts": 0,
    "model_calls": 0,
    "deadline_ms": deadline_ms,
}

while needs_more_evidence(state):
    enforce_limits(state, max_attempts=3, max_model_calls=6)
    plan = plan_next_retrieval(state)
    hits = retrieve_with_acl(plan, state["principal"])
    state = record_evidence_and_decision(state, plan, hits)

return synthesize_or_abstain(state)
```

Persist plans, subqueries, source/version IDs, grades, and stop reasons. Do not persist hidden model reasoning; store concise decision records suitable for audit and replay.

### Graph RAG: retrieve explicit relationships and corpus-level structure

"Graph RAG" is overloaded. An HNSW vector index is a graph data structure but is not a knowledge-graph RAG system. Knowledge-graph RAG represents entities, claims, relationships, provenance, or communities explicitly and retrieves over that structure.

Microsoft's GraphRAG paper describes an indexing pipeline that extracts an entity graph from documents, detects communities, and pre-generates community summaries. For global questions, it generates partial answers from community reports and reduces them into a final response. On the paper's global sensemaking questions over corpora around one million tokens, this improved comprehensiveness and diversity over its conventional RAG baseline. The result is scoped to that query class and evaluation. [[19]](https://www.microsoft.com/en-us/research/publication/from-local-to-global-a-graph-rag-approach-to-query-focused-summarization/)

The current open-source GraphRAG query engine exposes distinct modes:

- **Local search** combines entity/relationship data with source text for entity-focused questions.
- **Global search** map-reduces across community reports for dataset-wide questions and is resource intensive.
- **DRIFT search** begins with community information and develops detailed follow-up questions for broader local exploration.
- **Basic search** provides a vector-RAG comparison path. [[20]](https://microsoft.github.io/graphrag/query/overview/)

The standard index uses LLMs for entity and relationship extraction, summarization, optional claims, and community reports. FastGraphRAG substitutes noun-phrase extraction and co-occurrence for much of the LLM work; Microsoft estimates graph extraction at roughly 75% of standard indexing cost and describes the fast graph as cheaper but noisier. [[21]](https://github.com/microsoft/graphrag/blob/main/docs/index/methods.md)

Graph-based retrieval is not one technique:

| Query need | Suitable structure | Typical retrieval |
|---|---|---|
| Known fact in a passage | lexical/vector chunks | hybrid + rerank |
| Specific entity and its evidence | entity graph plus source chunks | seed entity, bounded traversal, rerank evidence |
| Multi-hop relationship | typed graph with provenance | constrained traversal/path search, then source verification |
| Themes across a corpus | communities and summaries | hierarchical/map-reduce global search |
| Long-document abstraction | hierarchical summary tree | retrieve at multiple abstraction levels |

RAPTOR is an adjacent hierarchical method: it recursively embeds, clusters, and summarizes chunks into a tree, then retrieves at multiple abstraction levels. It is useful to compare with knowledge graphs because not every global or long-document question needs entity-relation extraction. [[24]](https://arxiv.org/abs/2401.18059)

`[inferred]` Store every extracted graph edge and summary with source chunk IDs, extractor/prompt/model version, time interval, tenant, and confidence. The graph is a fallible derived index. Before a consequential answer or action, resolve graph-derived claims back to authoritative text or structured records.

### Architecture selection

| Dimension | Hybrid + rerank | Agentic RAG | Graph RAG |
|---|---|---|---|
| Best fit | high-volume lookup, FAQ, support facts | ambiguous, multi-source, multi-step research | relationships, multi-hop, corpus-global themes |
| Offline cost | sparse/vector indexing | same plus optional tool catalog | entity/edge extraction, communities, summaries, embeddings |
| Online cost | usually one retrieval and one generation | planner, multiple retrievals, grading, generation | local traversal or global map-reduce plus generation |
| Predictability | highest | lower unless bounded | local moderate; global cost can be high |
| Freshness difficulty | chunks and embeddings | source selection plus chunks | graph, communities, summaries, and evidence all invalidate |
| Main failure | low recall or bad ranking | runaway/incorrect retrieval plan | extraction errors or stale inferred relationships |

`[inferred]` These approaches compose. A sound enterprise default is hybrid retrieval and reranking as the evidence service; add an agent controller only for query classes that benefit; add a graph-derived view only for measured relationship/global tasks.

## 2. Token Economics & NFR Metrics

### Account for offline and online work separately

For a corpus version `v`, approximate lifecycle cost as:

```text
index_cost(v) = parse/OCR
              + embedding_tokens * embedding_rate
              + sparse/vector/graph build compute
              + graph_extraction_input_output_tokens * model_rates
              + summary/community_tokens * model_rates
              + index storage + replicas + backups

query_cost(q) = query_embedding
              + retrieval/ANN compute
              + rerank candidates or rerank tokens
              + planner/grader/synthesis model tokens
              + graph traversal/map-reduce calls
              + network egress and observability

cost_per_verified_success = total lifecycle cost / verified successful tasks
```

The final ratio matters more than cost per model call. A cheap query that returns unsupported answers or causes human rework is not economical.

### Cost and latency levers

| Lever | Quality effect | Cost/latency effect | Hidden risk |
|---|---|---|---|
| smaller chunks | finer matching | more vectors and candidates | loses surrounding context |
| larger chunks | more local context | fewer vectors; more prompt/rerank tokens | topic dilution and truncation |
| deeper ANN search | higher vector recall | more CPU/latency | still misses lexical identifiers |
| more hybrid branches | broader recall | parallel compute and larger union | correlated branches add little |
| larger rerank pool | potential precision gain | roughly more scoring work | initial recall ceiling remains |
| more final context | potential evidence coverage | input-token cost and latency | distraction/lost-in-middle |
| agent retries | can repair weak retrieval | additional model and tool turns | loops and duplicated evidence |
| graph global search | corpus-level coverage | map-reduce model calls | summary error propagation |
| precomputed summaries | cheaper repeated global query | expensive/freshness-sensitive indexing | stale abstractions |

The "Lost in the Middle" study found that model performance can vary substantially with the position of relevant evidence and often degrades when evidence is in the middle of long context. More retrieved tokens are therefore not equivalent to more usable evidence. [[28]](https://arxiv.org/abs/2307.03172)

GraphRAG's repository explicitly warns that indexing can be expensive, advises starting small, and describes the code as a research demonstration rather than an officially supported Microsoft offering. Its documented versioning can require configuration regeneration or migration across releases. [[22]](https://github.com/microsoft/graphrag)

### Latency budget

Measure each span independently:

```text
T_total = T_auth
        + max(T_lexical, T_dense, T_graph, T_remote_sources)
        + T_fusion
        + T_rerank
        + sum(T_agent_planning_and_retries)
        + T_context_assembly
        + T_generation
        + T_verification
```

Record p50, p95, and p99, not averages alone. Also record time to first token and complete response, timeout/cancellation rate, queue time, cold-start time, index freshness lag, and degraded-mode rate. For agentic retrieval, split planner, each subquery, each knowledge source, reranking, synthesis, and verification; otherwise a slow remote source is indistinguishable from a slow model.

### Evaluation stack

Do not collapse retrieval and generation into a single subjective score:

1. **Corpus and ingestion**: parse coverage, OCR error, chunk/source alignment, ACL propagation, duplicate rate, deletion lag, embedding coverage, graph edge provenance.
2. **Candidate retrieval**: Recall@k, hit rate, MRR, nDCG@k, per-query-class recall, filter correctness, ANN versus exact-retrieval recall on a sample.
3. **Reranking**: nDCG/MRR lift over the same candidate sets, relevant-item demotion, truncation rate, latency per candidate, stability across model versions.
4. **Context assembly**: context precision/recall, redundant-token fraction, evidence ordering, source diversity, conflicting-source coverage.
5. **Generation**: task correctness, claim-level faithfulness, citation entailment and completeness, abstention precision/recall, harmful-answer rate.
6. **Agent trajectory**: source-selection accuracy, query-decomposition quality, unnecessary-retrieval rate, retries, tool errors, stop-reason accuracy, budget breaches.
7. **System**: p95/p99, throughput, availability, freshness, cost per query and per verified success.

BEIR evaluated lexical, sparse, dense, late-interaction, and reranking systems across 18 heterogeneous datasets. It found BM25 a robust baseline and strong average zero-shot performance from reranking/late interaction at higher computational cost, while also demonstrating domain variability. [[13]](https://arxiv.org/abs/2104.08663) Use such public benchmarks for model screening, then evaluate on production queries, corpus versions, languages, adversarial cases, and access policies.

RAGAS proposes reference-free measures that separate context relevance, faithfulness, and answer quality. [[26]](https://arxiv.org/abs/2309.15217) RAGChecker adds diagnostic retrieval and generation metrics and reports stronger correlation with human judgments in its meta-evaluation. [[27]](https://arxiv.org/abs/2408.08067) Both are useful instruments, but model-graded metrics must be calibrated against human judgment and cannot certify safety or factual correctness by themselves.

### Capacity test matrix

`[inferred]` Benchmark at least:

- 1x, 10x, and projected corpus size;
- warm/cold cache and hot/sharded tenants;
- lexical, semantic, hybrid, reranked, agentic, graph-local, and graph-global query classes;
- short and long documents, tables, scans, code, multilingual text, and malformed files;
- authorization filters with large group memberships;
- simultaneous ingestion and querying;
- embedding/reranker/model version transitions;
- remote-source latency, partial outage, and rate limiting;
- prompt injection and poisoned-document suites.

> ⚠️ Limited public data available for this dimension. There is no stable, audited, apples-to-apples benchmark covering hybrid search, reranking, Agentic RAG, and Graph RAG with the same corpus, models, relevance judgments, authorization filters, hardware, freshness workload, and p50/p95/p99/cost-per-verified-success measurements. Published gains are workload- and implementation-specific; size infrastructure from an internal replay and load test.

## 3. Distributed Resilience & State

### Model the indexes as versioned projections

Each published chunk should carry enough identity to make ingestion idempotent and answers reproducible:

```text
tenant_id
source_system + source_object_id + source_version
parser_version + chunker_version + chunk_ordinal
content_hash
embedding_model + embedding_dimension
acl_version + classification
valid_from / valid_to / deleted_at
index_generation
```

Use a deterministic chunk ID derived from tenant, source, version, chunker version, and ordinal. An at-least-once event can then upsert the same derived record rather than create duplicates. Keep a separate ingestion ledger with stage, attempts, error, artifact hashes, and publication generation.

### Ingestion state machine

`[inferred]` A recoverable flow is:

```text
DISCOVERED -> PARSED -> POLICY_TAGGED -> CHUNKED
           -> EMBEDDED -> INDEXED_SPARSE -> INDEXED_VECTOR
           -> INDEXED_GRAPH(optional) -> VALIDATED -> PUBLISHED
           -> SUPERSEDED / TOMBSTONED
```

- Commit stage outputs to durable object storage before acknowledging the work item.
- Make every stage retryable by content/version key.
- Send terminal parse or model failures to a dead-letter queue with the source reference.
- Publish only after expected counts, ACLs, source links, and sampled retrieval tests pass.
- Do not expose a half-built generation as the active alias.

Azure AI Search documents a comparable managed pattern: indexers maintain an internal high-water mark and can resume changed-data processing when the source supports change detection. A run marked successful can still contain warnings, so status alone is insufficient. [[33]](https://learn.microsoft.com/en-us/azure/search/search-how-to-create-indexers)

### Deletes and permission changes are first-class events

Stale deletion is both a correctness and privacy incident. Change detection does not imply deletion detection. Azure's Blob indexer documentation states that object deletion is not tracked automatically and requires an established soft-delete policy; if it was absent on the first run, already orphaned index records can remain. It also documents timestamp edge cases for restored or renamed content. [[34]](https://learn.microsoft.com/en-us/azure/search/search-how-to-index-azure-blob-changed-deleted)

`[inferred]` Requirements:

- ingest tombstones through the same durable channel as updates;
- delete sparse chunks, vectors, reranker caches, graph nodes/edges, community summaries, and generated answer caches;
- propagate ACL changes faster than content freshness targets;
- periodically reconcile source manifests against every derived store;
- maintain a privacy-delete job that verifies absence, including backups according to retention policy;
- alert on orphan chunks and graph evidence pointing to a deleted version.

### Consistent query snapshots

Hybrid branches can observe different generations during a rolling rebuild. RRF may then merge a lexical hit from generation `N` with a vector hit or ACL from `N+1`.

`[inferred]` Pin each request to a compatible bundle:

```text
retrieval_bundle = {
  sparse_index: "kb-2026-08-21-07",
  vector_index: "kb-2026-08-21-07",
  graph_index: "kg-2026-08-21-07",
  acl_snapshot: "acl-88421",
  embedding_model: "embed-X@digest",
}
```

Build a new generation, validate it, atomically change the read alias, and retain the old generation for rollback. Elasticsearch documents aliases as a means to change active indexes in real time and reindex without downtime. [[35]](https://www.elastic.co/guide/en/elasticsearch/reference/current/aliases.html)

### Embedding and schema migrations

Embeddings from different models, dimensions, or normalization rules are generally not comparable. Dual-write old and new embeddings into separate fields/indexes, replay a fixed eval set, then switch the bundle. Never mix them silently in one ANN space.

Graph migrations are broader. Entity IDs, extraction prompts, community algorithms, and summaries may all change. Microsoft GraphRAG's breaking-change record includes renamed fields, vector-store requirements, required embeddings, migration notebooks, and cases where reindexing or cache-aware migration is needed. [[23]](https://github.com/microsoft/graphrag/blob/main/breaking-changes.md)

`[inferred]` Treat graph index version as a single immutable release. Do not update entities but leave old community reports live. Incremental graph changes can merge/split communities and invalidate summaries far from the changed document.

### Query-path resilience

Use explicit degradation modes:

| Failure | Bounded fallback |
|---|---|
| dense encoder unavailable | lexical retrieval only, label degraded response |
| vector store timeout | lexical plus cached safe results if versions/ACLs match |
| reranker unavailable | return fused order with a quality flag |
| planner/model unavailable | deterministic single-pass retrieval |
| one remote knowledge source fails | partial answer only if required evidence is not missing |
| graph global search exceeds budget | local/hybrid evidence or ask to narrow scope |
| generator unavailable | return search results, not fabricated synthesis |

Retries need exponential backoff, jitter, deadline propagation, idempotency keys, and per-dependency circuit breakers. Hedging can reduce tail latency for read-only retrieval but must not double-bill uncontrolled model calls. Cancel abandoned subqueries.

For Agentic RAG, checkpoint after each externally expensive step with plan hash, query, filters, returned stable IDs, index generation, and cost. On resume, reuse a result only when its source/version/authorization snapshot remains valid. Side-effecting downstream tools do not belong in an untracked retrieval retry loop.

### Backup and disaster recovery

Back up source manifests, parser outputs when legally permitted, sparse/vector/graph configurations, model/prompt digests, ingestion ledger, judgments, and deployment manifests. Rebuildability is more valuable than opaque index snapshots alone.

`[inferred]` Recovery drills should prove:

- point-in-time restore without cross-tenant leakage;
- rebuild from source within recovery-time and recovery-point objectives;
- restoration of tombstones and ACL state, not only content;
- reattachment of citations to the exact restored source version;
- ability to roll back embedding, reranker, graph, and prompt releases independently;
- no answer cache survives beyond the content or permission version that authorized it.

Monitor processed/failed item counts, last successful watermark, indexing lag, and warnings. Azure documents indexer execution history and processed/failed item fields, but retains only a bounded recent history, so export required audit telemetry to a durable observability system. [[36]](https://learn.microsoft.com/en-us/azure/search/search-monitor-indexers)

## 4. Enterprise Security & Governance

### Retrieved content is untrusted data, not instruction

RAG expands the prompt-injection boundary from the user to every indexed or remote source. The indirect prompt-injection paper demonstrated that adversarial instructions placed in data likely to be retrieved can manipulate LLM-integrated applications, including tool use and data disclosure. [[30]](https://arxiv.org/abs/2302.12173)

The core rule is structural: retrieved text may supply facts but may not redefine system policy, authorization, tool permissions, or the controller's stop conditions. Delimit it as evidence, retain source labels, scan it, and keep effectful actions behind deterministic authorization and approval gates. Prompt language alone does not create a security boundary.

### Authorization must happen before exposure and ranking

Post-filtering the top results is unsafe and harms recall. If an ANN query retrieves 20 unauthorized nearest neighbors and the application removes them afterward, it may return nothing even though authorized evidence exists. Worse, remote rerank/model services may already have received unauthorized text.

Apply the caller's tenant and document-level authorization inside every lexical, vector, graph, cache, and remote-source query. Azure documents both application-supplied security filters and current identity-aware ACL/RBAC enforcement. Permission metadata is indexed and matched at query time; some native ACL, Purview, and SharePoint capabilities remain preview and have synchronization limitations that must be evaluated. [[31]](https://learn.microsoft.com/en-us/azure/search/search-document-level-access-overview)

In Azure's identity-aware path, the service constructs security filters from the authorization token and matches user, group, or RBAC-scope metadata in each indexed document. A document can pass when any independently evaluated permission route succeeds, so ingestion and policy tests must confirm that the intended union semantics match the source system. [[32]](https://learn.microsoft.com/en-us/azure/search/search-query-access-control-rbac-enforcement)

```python
def retrieve(request, authenticated_principal):
    # The client never supplies an arbitrary tenant or ACL expression.
    policy = policy_service.compile(
        subject=authenticated_principal,
        action="knowledge.read",
        resource_scope=request.knowledge_base,
    )
    return search.query(
        request.query,
        mandatory_filter=policy.search_filter,
        allowed_graph_labels=policy.graph_labels,
        index_generation=policy.approved_generation,
    )
```

`[inferred]` For high-assurance multi-tenancy, prefer separate namespaces/indexes and encryption scopes over filters alone when scale permits. Test negative authorization cases under lexical, vector, graph traversal, semantic rerank, caches, exports, logs, and fallback modes.

### Poisoning, embedding, and graph threats

OWASP's Vector and Embedding Weaknesses category calls out unauthorized access, cross-context leakage, embedding inversion, and data poisoning in RAG systems. It recommends permission-aware partitioning, source validation, monitoring, and data classification. [[29]](https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/)

Threats include:

- **corpus poisoning**: malicious or low-quality content is indexed to dominate a target query;
- **hidden instructions**: white text, metadata, comments, or OCR layers inject model instructions;
- **retrieval manipulation**: repeated keywords or adversarial embeddings win candidate rank;
- **graph poisoning**: extracted false entities/edges gain centrality and contaminate community summaries;
- **embedding leakage/inversion**: vectors and nearest-neighbor APIs reveal sensitive properties;
- **inference through graph edges**: a relationship may disclose sensitive membership even when node text is hidden;
- **citation laundering**: an answer cites a real source that does not support the generated claim;
- **stale authorization**: permission revocation does not reach derived chunks, graph edges, or caches.

Controls:

1. allowlist and authenticate ingestion sources;
2. malware-scan and parse in an isolated environment;
3. preserve raw/source hashes and immutable lineage;
4. normalize hidden text and flag anomalous instruction-like content;
5. separate trust tiers and make authority/recency explicit ranking features;
6. require multiple independent evidence items for high-impact claims;
7. quarantine suspicious sources and support rapid reindex/tombstone;
8. bound per-source contribution so one document cannot flood context;
9. validate every citation at claim level;
10. authorize graph traversal and source resolution, not only the final text response.

### Data governance

Create a catalog entry for every knowledge source recording owner, lawful purpose, classification, geography, retention, refresh SLO, supported query purposes, and contact for correction/deletion. Propagate those controls to chunks, embeddings, graphs, summaries, caches, traces, eval datasets, and backups.

Remote knowledge sources cross additional boundaries. Azure's web knowledge-source documentation states that it uses Bing Custom Search, may query unrestricted public internet when domains are not constrained, incurs costs, and waives certain government-cloud security/compliance commitments. [[37]](https://learn.microsoft.com/en-us/azure/search/agentic-knowledge-source-how-to-web) Equivalent due diligence is required for any external search or rerank provider: data processing location, retention/training terms, encryption, private connectivity, sub-processors, deletion, and incident notification.

`[inferred]` Do not send protected chunks to a third-party reranker unless the data-processing agreement and technical boundary permit it. A self-hosted reranker may be operationally expensive but is sometimes the only compliant option.

### Provenance and audit

For each answer, retain:

- authenticated subject and policy decision ID;
- normalized user request hash and purpose;
- planner/source-selection decisions;
- exact queries and mandatory filters;
- corpus, index, embedding, reranker, graph, prompt, and generator versions;
- retrieved candidate IDs/ranks, selected context, and source version IDs;
- claims and citation-support outcomes;
- latency, tokens, cost, retries, degradation, and stop reason;
- human approval or correction when applicable.

Minimize logged content. Encrypt sensitive fields, redact secrets/PII, use role-separated trace access, apply retention, and make audit logs tamper-evident. Logging the complete retrieved context can create a second, less-protected copy of the knowledge base.

### Release governance

No embedding, chunker, reranker, agent policy, graph extractor, source, or generation prompt should reach production solely on aggregate answer quality. Require:

- versioned offline replay by tenant/query class/language;
- authorization and deletion tests;
- citation and abstention tests;
- poisoning and indirect-injection red-team cases;
- canary traffic with rollback thresholds;
- cost and tail-latency limits;
- documented model/source licenses and data lineage;
- change approval proportional to impact.

`[inferred]` Run evaluation with service identities that cannot access production-only content. An eval harness that bypasses ACLs can leak data into prompts, judge logs, or human annotation queues.

## 5. Production Failure Modes

### Retrieval and ranking failures

| Failure | Symptom | Detection | Mitigation |
|---|---|---|---|
| lexical-only semantic miss | correct concept absent | low judged Recall@k on paraphrases | add dense/sparse semantic branch |
| dense identifier miss | SKU/error code absent | per-query-class branch recall | retain BM25/exact fields |
| ANN under-search | exact vector match exists but ANN misses | sample exact-versus-ANN recall | tune search effort/partitions |
| bad fusion | one branch overwhelms useful results | branch-rank contribution | tune RRF/weights on judgments |
| candidate starvation | reranker never sees evidence | recall before rerank | increase/fix first-stage retrieval |
| reranker truncation | relevant passage late in document | truncation and chunk coverage metrics | rerank chunks/priority fields |
| score-threshold drift | sudden empty/overfull results | score distribution by model version | calibrate per version; favor rank/eval |
| duplicate chunks | context repeats one source | unique source/token ratio | canonical IDs and diversity selection |
| stale index | old policy/product answer | freshness and source-version checks | CDC, reconciliation, expiry |

Azure's current relevance documentation illustrates why stage-aware diagnostics matter: hybrid retrieval runs lexical and vector queries, combines them with RRF, then may apply semantic L2 reranking; agentic retrieval can add LLM planning and an iterative L3 pass. A single final score cannot identify which layer failed. [[38]](https://learn.microsoft.com/en-us/azure/search/search-relevance-overview)

### Context and generation failures

- **Right document, wrong chunk**: retrieval finds the parent but not the passage containing the answer. Evaluate chunk-level evidence and add adjacency only after selection.
- **Evidence overload**: excessive `k` adds contradictions and buries the decisive passage. Optimize verified answer quality against token count, not retrieval recall alone.
- **Unsupported synthesis**: the answer blends sources into a new claim no source supports. Perform claim-level entailment/citation checks and abstain.
- **Citation offset drift**: reprocessing changes chunk numbering so old citations resolve incorrectly. Cite stable source/version plus a content hash and location.
- **Fresh/authoritative conflict**: a recent forum post outranks policy. Model source authority, validity intervals, and conflict-handling rules.
- **Model prior overrides evidence**: the generator supplies familiar but obsolete facts. Require evidence-grounded answers for governed domains and test conflicts deliberately.
- **Lost-in-middle**: evidence is present but poorly used because of position. Rerank, compress, order intentionally, and test placements. [[28]](https://arxiv.org/abs/2307.03172)

### Agentic RAG failures

- The router incorrectly chooses no retrieval for a freshness-sensitive question.
- Decomposition changes the user's meaning or drops a constraint.
- Query rewriting hallucinates a named entity and retrieves a convincing wrong neighborhood.
- A relevance grader accepts lexical overlap without answer support.
- Repeated retries consume budget but return correlated copies of the same source.
- The controller chooses a remote source that is prohibited for the tenant or geography.
- Parallel subqueries use inconsistent authorization or index generations.
- Partial source failure is hidden and the answer implies full coverage.
- Conversation context causes a query to inherit the wrong user/entity scope.
- A retrieved instruction redirects the controller or triggers a tool.

`[inferred]` Set maximum planning steps, retrieval attempts, sources, candidates, context tokens, model tokens, wall time, and spend. Stop with explicit states such as `SUPPORTED`, `INSUFFICIENT_EVIDENCE`, `CONFLICTING_EVIDENCE`, `POLICY_DENIED`, `PARTIAL_SOURCE_FAILURE`, or `BUDGET_EXCEEDED`.

### Graph RAG failures

- **entity collision** merges two people/products with the same name;
- **entity fragmentation** creates multiple nodes for one entity;
- **false edge** turns co-occurrence or extraction error into asserted relationship;
- **temporal collapse** treats formerly true and currently true edges alike;
- **community instability** a small update reshapes clusters and invalidates summaries;
- **summary propagation** an early extraction error appears in many community reports;
- **hub domination** generic high-degree nodes consume traversal/context;
- **path explosion** unconstrained multi-hop traversal destroys latency;
- **orphan provenance** an edge survives after source deletion;
- **global-search cost surprise** map-reduce spans more communities than expected;
- **migration mismatch** entities, embeddings, Parquet schema, and summaries come from different releases.

Microsoft's repository warns that GraphRAG is expensive and requires careful version migration, while its query docs call global search resource intensive. [[22]](https://github.com/microsoft/graphrag) [[20]](https://microsoft.github.io/graphrag/query/overview/) These are design constraints, not implementation footnotes.

### Distributed and operational failures

- indexer reports success with warnings or partial items;
- duplicate delivery creates duplicate vectors/edges because IDs are random;
- source deletion lacks a tombstone and derived data remains searchable;
- permission change does not update because it does not modify the content watermark;
- sparse and vector aliases point to different generations;
- cache key omits tenant, ACL version, corpus version, or retrieval policy;
- embedding service changes normalization/dimension without a full migration;
- retry storm amplifies a vector/rerank provider outage;
- cancellation is not propagated, so abandoned agent branches keep spending;
- trace sampling excludes rare denied/degraded cases needed for audit;
- backup restores content but not deletion/ACL history.

### Evaluation failures

- test questions are generated from the same chunks, making retrieval unrealistically easy;
- benchmark answers are stale or ambiguous;
- only answer style is judged, masking unsupported claims;
- LLM judges prefer verbose answers or share the generator's errors;
- aggregate nDCG hides a catastrophic regulated-query segment;
- offline corpus lacks production ACL filtering and latency;
- the eval set leaks into tuning prompts or reranker training;
- graph evaluation checks plausibility instead of source-supported edges.

Use blinded human review for a stratified critical set, inter-annotator checks, stable holdouts, and separate retrieval/generation/security scorecards.

> ⚠️ Limited public data available for this dimension. Vendors and framework owners do not publish normalized production incident rates for stale or unauthorized retrieval, poisoning, ANN recall loss, reranker truncation, agent loops, graph extraction errors, orphan deletions, citation mismatch, or cross-tenant leakage. Public benchmarks and issue trackers have no common workload or denominator; internal incident taxonomy, canaries, reconciliation, and adversarial tests are required.

## 6. Enterprise System Design Scenarios

### Scenario A: regulated policy assistant

**Need:** Employees ask about current policies; answers must honor region, role, and effective date and cite exact clauses.

`[inferred]` Use hybrid BM25+dense retrieval with mandatory pre-retrieval ACL and temporal filters, RRF, a locally approved cross encoder, and extractive context assembly. Keep policy documents as the source of truth. Require source/version/page citations and return `INSUFFICIENT_EVIDENCE` rather than free-form policy advice.

Do not begin with Graph RAG unless evaluated questions truly require relationships across policies. Do not use unrestricted web search. Agentic query decomposition may help compare regions, but constrain it to approved knowledge sources and two or three subqueries. Propagate access policy to every subquery and citation.

**SLO/eval:** zero unauthorized-document exposure in the adversarial suite; deletion/revocation freshness tighter than content freshness; Recall@k on policy clauses; claim support and citation accuracy; p95 latency and cost per supported answer.

### Scenario B: global investigation over narrative reports

**Need:** Analysts ask for themes, actors, relationships, and changes across thousands of incident reports.

`[inferred]` Build a versioned Graph RAG projection with entity resolution, typed/temporal edges, community reports, and direct links to report chunks. Route entity questions to local graph-plus-text search and corpus-wide theme questions to budgeted global search. Retain hybrid retrieval for quotations, IDs, and verification. Require analysts to inspect source evidence before consequential conclusions.

Use a staged bake-off against hybrid RAG and a hierarchical method such as RAPTOR. The GraphRAG paper's global-query results justify testing, not automatic adoption. [[19]](https://www.microsoft.com/en-us/research/publication/from-local-to-global-a-graph-rag-approach-to-query-focused-summarization/) [[24]](https://arxiv.org/abs/2401.18059)

**SLO/eval:** entity-resolution precision/recall, edge provenance coverage, temporal correctness, global-answer comprehensiveness/diversity plus factual support, index cost per source token, update invalidation time, and analyst task completion.

### Scenario C: multi-source support research agent

**Need:** Resolve a customer issue using product docs, account-specific structured data, live service status, and approved web sources.

`[inferred]` Use a deterministic outer workflow and a bounded Agentic RAG node:

1. authenticate customer/account scope;
2. classify issue and redact secrets;
3. plan at most four source-specific queries;
4. execute account SQL/API, hybrid product search, and status lookup with separate credentials;
5. grade whether evidence addresses each issue component;
6. run one corrective query if necessary;
7. synthesize with per-claim citations and explicitly list unavailable sources;
8. propose, but do not autonomously execute, a consequential account change.

Persist the source-selection record and evidence IDs. Web content is untrusted; it cannot authorize actions or override account policy. This resembles documented managed agentic retrieval mechanics but keeps business authorization outside the model. [[17]](https://learn.microsoft.com/en-us/azure/search/search-agentic-retrieval-concept)

**SLO/eval:** source-selection accuracy, unnecessary retrieval rate, supported resolution rate, escalation precision, tool/timeout rate, p95 turns and cost, and zero cross-account evidence.

### Scenario D: high-volume commerce or documentation search

**Need:** Millions of low-latency queries include exact SKUs, natural-language descriptions, filters, and freshness-sensitive inventory/docs.

`[inferred]` Prefer hybrid search plus a fast reranker. Run lexical and ANN branches in parallel with category/tenant filters, fuse by RRF, rerank a bounded pool, and cache only against index generation and authorization. Reserve generation for questions requiring synthesis; many navigation/search queries should return ranked results directly.

Agentic RAG is usually a poor default here because planner turns increase tail latency and cost. Graph RAG is justified only for explicit compatibility, dependency, or relationship queries, and those relationships may be better served by an authoritative product graph than LLM-extracted edges.

**SLO/eval:** Recall@k and nDCG by identifier/semantic/filter query, p95/p99 under concurrent ingestion, inventory freshness, exact filter correctness, rerank timeout fallback quality, and revenue/task metrics guarded against popularity bias.

### Build-versus-buy and design review matrix

| Question | Evidence required before commitment |
|---|---|
| Managed or self-hosted search? | corpus/traffic sizing, compliance boundary, private networking, backup/restore, price under p95 load |
| Hosted or local reranker? | domain nDCG lift, candidate-token distribution, p95, data-processing terms, fallback |
| Add agentic control? | measured gain on complex query class, budget distribution, failure/abstention quality, auditable state |
| Add Graph RAG? | material relationship/global workload, graph extraction accuracy, update cost, provenance, baseline comparison |
| Shared or isolated tenancy? | regulatory requirement, filter correctness, noisy-neighbor tests, operational cost |
| Precompute or query-time summarize? | corpus churn, repeated-query rate, staleness tolerance, offline versus online budget |

### Principal-engineer interview checklist

1. What is the source of truth, and how are updates, deletes, and ACL revocations propagated to every derived store?
2. What query classes need exact lexical, semantic, multi-hop, or global retrieval?
3. Is Recall@k measured before reranking, and can the team explain a miss by stage?
4. Are authorization filters applied inside every retrieval branch before content reaches a reranker or model?
5. What index/model/prompt versions make an answer reproducible?
6. What stops an Agentic RAG loop, and what happens on partial source failure?
7. Are graph edges evidence-backed, typed, temporal, and independently authorizable?
8. How are claim support, citation accuracy, abstention, latency, and cost per verified success measured?
9. What is the rollback and rebuild plan for an embedding or graph schema migration?
10. What happens when retrieved text contains instructions aimed at the model?

### Recommended learning order

1. Build and evaluate BM25 and dense baselines independently.
2. Add hybrid RRF and learn branch-level recall diagnostics.
3. Add a reranker and prove lift on a fixed candidate set.
4. Implement citation-grounded generation and abstention.
5. Make ingestion versioned, idempotent, permission-aware, and reversible.
6. Add bounded Agentic RAG only for a measured multi-step query class.
7. Add Graph RAG only after hybrid/hierarchical baselines fail on relationship or global questions.
8. Operate all variants under the same security, observability, evaluation, and cost scorecard.

## Sources

- [1] https://arxiv.org/abs/2005.11401 - Original retrieval-augmented generation formulation.
- [2] https://doi.org/10.1561/1500000019 - Probabilistic relevance framework, BM25, and BM25F.
- [3] https://arxiv.org/abs/2004.04906 - Dense Passage Retrieval architecture and original benchmark results.
- [4] https://arxiv.org/abs/1603.09320 - HNSW approximate nearest-neighbor index.
- [5] https://www.elastic.co/docs/solutions/search/hybrid-search - Current Elastic hybrid-search guidance.
- [6] https://research.google/pubs/reciprocal-rank-fusion-outperforms-condorcet-and-individual-rank-learning-methods/ - Original RRF paper record and results.
- [7] https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion - RRF formula and Elasticsearch behavior.
- [8] https://arxiv.org/abs/2112.01488 - ColBERTv2 late-interaction retrieval and compression.
- [9] https://arxiv.org/abs/1901.04085 - BERT cross-encoder passage reranking.
- [10] https://docs.cohere.com/docs/rerank - Current Cohere rerank models and input behavior.
- [11] https://docs.cohere.com/v1/docs/reranking-best-practices - Reranker chunking, truncation, and input limits.
- [12] https://learn.microsoft.com/en-us/azure/search/semantic-search-overview - Azure semantic-ranker mechanics and top-50 boundary.
- [13] https://arxiv.org/abs/2104.08663 - BEIR heterogeneous retrieval benchmark.
- [14] https://arxiv.org/abs/2310.11511 - Self-RAG adaptive retrieval and reflection tokens.
- [15] https://arxiv.org/abs/2401.15884 - Corrective Retrieval-Augmented Generation.
- [16] https://arxiv.org/abs/2403.14403 - Adaptive-RAG complexity-based strategy routing.
- [17] https://learn.microsoft.com/en-us/azure/search/search-agentic-retrieval-concept - Azure agentic retrieval pipeline.
- [18] https://learn.microsoft.com/en-us/azure/search/agentic-knowledge-source-overview - Indexed and remote agentic knowledge sources.
- [19] https://www.microsoft.com/en-us/research/publication/from-local-to-global-a-graph-rag-approach-to-query-focused-summarization/ - Microsoft GraphRAG paper and scoped results.
- [20] https://microsoft.github.io/graphrag/query/overview/ - GraphRAG local, global, DRIFT, and basic query modes.
- [21] https://github.com/microsoft/graphrag/blob/main/docs/index/methods.md - Standard and FastGraphRAG indexing methods and cost tradeoff.
- [22] https://github.com/microsoft/graphrag - Current GraphRAG repository, status, versioning, and cost warning.
- [23] https://github.com/microsoft/graphrag/blob/main/breaking-changes.md - GraphRAG schema and migration history.
- [24] https://arxiv.org/abs/2401.18059 - RAPTOR hierarchical retrieval.
- [25] https://arxiv.org/abs/2212.10496 - HyDE hypothetical-document retrieval.
- [26] https://arxiv.org/abs/2309.15217 - RAGAS retrieval/generation evaluation framework.
- [27] https://arxiv.org/abs/2408.08067 - RAGChecker fine-grained diagnostics.
- [28] https://arxiv.org/abs/2307.03172 - Lost-in-the-middle long-context study.
- [29] https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/ - OWASP vector, embedding, leakage, and poisoning risks.
- [30] https://arxiv.org/abs/2302.12173 - Indirect prompt-injection attacks through retrieved content.
- [31] https://learn.microsoft.com/en-us/azure/search/search-document-level-access-overview - Document-level authorization patterns and limitations.
- [32] https://learn.microsoft.com/en-us/azure/search/search-query-access-control-rbac-enforcement - Query-time ACL/RBAC enforcement mechanics.
- [33] https://learn.microsoft.com/en-us/azure/search/search-how-to-create-indexers - Change tracking, watermarks, warnings, and indexer execution.
- [34] https://learn.microsoft.com/en-us/azure/search/search-how-to-index-azure-blob-changed-deleted - Delete-detection requirements and edge cases.
- [35] https://www.elastic.co/guide/en/elasticsearch/reference/current/aliases.html - Index aliases and zero-downtime reindex publication.
- [36] https://learn.microsoft.com/en-us/azure/search/search-monitor-indexers - Indexer status and execution-history monitoring.
- [37] https://learn.microsoft.com/en-us/azure/search/agentic-knowledge-source-how-to-web - Web knowledge-source provider and compliance constraints.
- [38] https://learn.microsoft.com/en-us/azure/search/search-relevance-overview - Current multi-level ranking architecture.
