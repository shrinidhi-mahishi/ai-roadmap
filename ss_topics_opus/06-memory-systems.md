# Module 06: Memory Systems for AI Agents

> **Audience**: Principal Data Scientist (12+ YOE) preparing for Director/VP-level AI roles.
> **Scope**: End-to-end architecture of memory systems for LLM-based agents -- cognitive models, storage backends, consolidation algorithms, token economics, security posture, production code, and enterprise design scenarios.
> **Pricing assumptions**: OpenAI text-embedding-3-small $0.02/1M tokens; text-embedding-3-large $0.13/1M; Cohere embed-v4 ~$0.10/1M; Claude Sonnet 4 input $3/1M, output $15/1M; GPT-4o input $2.50/1M, output $10/1M. All as of mid-2026.
> **Key papers**: Park et al. Generative Agents (UIST 2023), MemGPT (Berkeley 2023), Zep/Graphiti (arXiv:2501.13956), Mem0 (arXiv:2504.19413), SCM (arXiv:2604.20943), ACON (arXiv:2510.00615).

---

## 1. System Topology & Data Flow

### 1.1 Full Memory System Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                              CONTROL PLANE                                       │
│                                                                                  │
│  ┌────────────────────┐  ┌─────────────────────┐  ┌──────────────────────────┐  │
│  │  Memory Router      │  │  Consolidation       │  │  Retrieval Orchestrator   │  │
│  │                    │  │  Scheduler            │  │                          │  │
│  │  - Classifies ops  │  │                      │  │  - Parallel fan-out:     │  │
│  │    (write/read/    │  │  - Async background  │  │    vector + BM25 +       │  │
│  │     consolidate)   │  │  - Session-end       │  │    graph + temporal      │  │
│  │  - Scope resolver  │  │    trigger            │  │  - Score fusion (RRF    │  │
│  │    (user/agent/    │  │  - Cron-based         │  │    or weighted sum)      │  │
│  │     org/session)   │  │    maintenance        │  │  - MMR deduplication     │  │
│  │  - Memory type     │  │  - TTL enforcement   │  │  - Top-k calibration     │  │
│  │    selector        │  │  - GC scheduling     │  │    (3-5 chunks optimal)  │  │
│  └────────┬───────────┘  └──────────┬──────────┘  └─────────────┬────────────┘  │
│           │                         │                            │                │
├───────────┼─────────────────────────┼────────────────────────────┼────────────────┤
│           │              DATA PLANE │                            │                │
│           v                         v                            v                │
│  ┌────────────────────┐  ┌─────────────────────┐  ┌──────────────────────────┐  │
│  │  Embedding Pipeline │  │  Entity Extractor    │  │  Conflict Resolver       │  │
│  │                    │  │                      │  │                          │  │
│  │  - Model: e.g.    │  │  - NER + relation    │  │  - Graph diff against    │  │
│  │    text-embed-3-sm │  │    extraction (LLM)  │  │    existing facts        │  │
│  │  - Chunk + embed   │  │  - Node/edge upsert  │  │  - LLM-based merge/     │  │
│  │  - Version-tag     │  │    to knowledge      │  │    update/flag decision  │  │
│  │    every vector    │  │    graph              │  │  - Similarity threshold  │  │
│  │  - Batch or        │  │  - Cross-link to     │  │    check (>0.85)         │  │
│  │    single-record   │  │    vector embeddings  │  │  - Bi-temporal audit     │  │
│  └────────┬───────────┘  └──────────┬──────────┘  └─────────────┬────────────┘  │
│           │                         │                            │                │
├───────────┼─────────────────────────┼────────────────────────────┼────────────────┤
│           │        PERSISTENCE LAYER│                            │                │
│           v                         v                            v                │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │                                                                            │  │
│  │  ┌──────────────┐   ┌──────────────────┐   ┌───────────────────────────┐  │  │
│  │  │ WORKING      │   │ SHORT-TERM       │   │ LONG-TERM STORES          │  │  │
│  │  │ MEMORY       │   │ BUFFER           │   │                           │  │  │
│  │  │              │   │                  │   │  ┌──────────┐ ┌────────┐  │  │  │
│  │  │ LLM context  │   │ Session-scoped   │   │  │ Vector   │ │ Graph  │  │  │  │
│  │  │ window       │   │ state            │   │  │ Store    │ │ Store  │  │  │  │
│  │  │ (200K max,   │   │                  │   │  │          │ │        │  │  │  │
│  │  │  reliable    │   │ - Checkpointer   │   │  │ Qdrant   │ │ Neo4j  │  │  │  │
│  │  │  <30K)       │   │   snapshots      │   │  │ Pinecone │ │ Falkor │  │  │  │
│  │  │              │   │ - Conversation   │   │  │ pgvector │ │ DB     │  │  │  │
│  │  │ Core memory  │   │   history ring   │   │  └──────────┘ └────────┘  │  │  │
│  │  │ block (Letta │   │ - Thread-scoped  │   │  ┌──────────┐ ┌────────┐  │  │  │
│  │  │ persona +    │   │   KV store       │   │  │ KV Store │ │ Relat. │  │  │  │
│  │  │ user block)  │   │                  │   │  │          │ │ DB     │  │  │  │
│  │  │              │   │ Lost between     │   │  │ Redis    │ │        │  │  │  │
│  │  │              │   │ sessions unless  │   │  │ LanceDB  │ │ Postgres│  │  │  │
│  │  │              │   │ promoted         │   │  │          │ │ SQLite │  │  │  │
│  │  └──────────────┘   └──────────────────┘   │  └──────────┘ └────────┘  │  │  │
│  │                                             └───────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│                        TOOL PROXIES (Agent-Facing)                               │
│                                                                                  │
│  ┌──────────────────┐  ┌────────────────────┐  ┌────────────────────────────┐  │
│  │  memory_write()   │  │  memory_recall()    │  │  memory_consolidate()     │  │
│  │                  │  │                    │  │  (internal / scheduled)    │  │
│  │  - Self-directed │  │  - Hybrid search   │  │                            │  │
│  │    (Letta model) │  │  - Scope-filtered  │  │  - Summarize episodes      │  │
│  │  - Framework-    │  │  - Re-ranked       │  │  - Extract semantic facts   │  │
│  │    managed       │  │  - Injected into   │  │  - Prune low-importance     │  │
│  │    (Mem0/CrewAI) │  │    context window  │  │  - Merge contradictions    │  │
│  └──────────────────┘  └────────────────────┘  └────────────────────────────┘  │
│                                                                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│                        TELEMETRY / OBSERVABILITY                                 │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐    │
│  │  - Memory operation events: query_start/complete/fail, save_start/      │    │
│  │    complete/fail with timing (query_time_ms, save_time_ms)              │    │
│  │  - Retrieval quality: hit rate, top-k relevance scores, miss rate       │    │
│  │  - Storage metrics: vector count, memory count by scope, GC stats      │    │
│  │  - Integrity: SHA-256 baseline checks (OWASP Agent Memory Guard)       │    │
│  │  - Cost tracking: embedding calls, LLM calls for extraction/consolidation│    │
│  └──────────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narratives

**Memory Write Path (agent stores a new memory)**:

1. Agent calls `memory_write(content, scope, metadata)`.
2. Memory Router resolves scope (user_id, agent_id, session_id, org_id) and classifies memory type (semantic fact vs episodic event vs procedural pattern).
3. Embedding Pipeline chunks the content and generates a dense vector, tagging it with the embedding model version.
4. Entity Extractor (LLM call) identifies named entities and relationships for the knowledge graph.
5. Conflict Resolver compares new facts against existing graph entries above a similarity threshold (typically 0.85). LLM decides: merge, update, insert_new, or flag for human review.
6. Parallel writes: vector inserted into vector store (ANN index), entities/edges upserted into graph store, raw record appended to episodic log.
7. SHA-256 integrity baseline computed and stored alongside the record.
8. Telemetry emits `save_complete` event with save_time_ms and storage location.

**Memory Recall Path (agent retrieves relevant memories)**:

1. Agent calls `memory_recall(query, scope, top_k)`.
2. Retrieval Orchestrator fans out three parallel searches: (a) vector ANN search on embedded query, (b) BM25 keyword search on raw text, (c) graph traversal from matched entities.
3. Each search returns scored candidates. Scores are fused via Reciprocal Rank Fusion (RRF) or weighted sum (e.g., CrewAI: 0.5 * semantic + 0.3 * recency_decay + 0.2 * importance).
4. Maximal Marginal Relevance (MMR) removes near-duplicate results.
5. Top-k results (typically 3-5; accuracy degrades beyond this due to "lost in the middle" effect) are assembled into a context block.
6. Context block is injected into the LLM's working memory (context window) alongside the current query.
7. Telemetry emits `query_complete` with query_time_ms and relevance scores.

**Memory Consolidation Path (background maintenance)**:

1. Consolidation Scheduler triggers -- either at session end, on a cron schedule, or when cumulative importance of new memories exceeds a threshold (~150 points in Park et al.).
2. Episodic memories since last consolidation are batched and sent to the Embedding Pipeline for re-analysis.
3. LLM extracts durable semantic facts (entities, preferences, outcomes) from raw episodic traces. These become semantic memory entries.
4. Reflection pass: LLM generates higher-level insights from clusters of related episodes (Park et al. reflection mechanism).
5. Garbage collection: TTL-expired memories are removed; low-importance memories below the relevance threshold are archived or dropped; near-duplicate memories are merged.
6. Consolidated facts are written via the standard Memory Write Path (including conflict resolution).
7. Original episodic traces are either archived to cold storage or deleted, depending on retention policy.
8. Telemetry emits consolidation metrics: memories processed, facts extracted, memories pruned, total consolidation time.

---

## 2. Core Mechanics & Algorithms

### 2.1 Three-Tier Cognitive Model

The agent memory ecosystem converged by 2025-2026 on a taxonomy mirroring cognitive science. Understanding the boundaries between tiers is the architectural foundation for every design decision that follows.

| Tier | Analogy | Lifetime | Implementation | Capacity | Update Pattern |
|------|---------|----------|----------------|----------|---------------|
| Working Memory | CPU registers | Single generation | LLM context window | 128K-200K tokens (reliable <30K) | Overwritten each turn |
| Short-Term Memory | RAM | Single session | Checkpointer, session state, conversation history | Bounded by session length | Append within session; lost between sessions |
| Long-Term Memory | Disk/SSD | Cross-session, persistent | Vector stores, knowledge graphs, relational DBs | Unbounded (requires GC) | Explicit read/write via tools |

**Critical insight**: The context window is the only memory the LLM can reason over. Everything else is "invisible" unless explicitly retrieved and injected. This means retrieval quality is the ceiling on memory utility -- a perfect memory store with a bad retriever is worthless.

**Attention degradation**: Chroma's 2025 Context Rot benchmark found that LLM behavior becomes less reliable as the context window fills, with degradation accelerating beyond ~30K tokens. This means "just use a bigger context window" is not a viable memory strategy. The 2025 assumption that 128K-1M token windows would eliminate external memory systems collapsed in practice.

### 2.2 Semantic Memory: Facts, Knowledge Graphs, Entity Stores

Semantic memory stores **what the agent knows** -- user preferences, entity attributes, domain facts, relationships. It is mutable and fact-centric: when a user changes their address, the old value is updated, not appended.

**Vector-based implementation** (simplest, most common):
- Facts embedded as dense vectors, stored in ANN index (HNSW dominant algorithm).
- Retrieval via approximate nearest-neighbor search over embedding space.
- Used by Mem0, LangMem, and most production agents as the primary semantic store.
- Weakness: no structured relational reasoning; "Who is Alice's manager?" requires the relationship to appear in a single embedded chunk.

**Graph-based implementation** (structured relational queries):
- Entities as nodes, relationships as edges in a knowledge graph.
- Zep's Graphiti engine: LLM pipeline extracts named entities and relationships, stores as nodes/edges, cross-links to vector embeddings for fuzzy search.
- Critical innovation: conflict detection compares new facts against existing graph entries and merges, updates, or flags for resolution. This prevents contradictory facts from accumulating.
- Bi-temporal model (Zep): timeline T (chronological event order) and T' (data ingestion order). Obsolete relationships marked invalid rather than deleted, enabling temporal reasoning and audit trails.

**Hybrid (2026 state of the art)**: Vector similarity for fuzzy retrieval + knowledge graph for structured relational queries. No single database excels at both, which is why production systems run polyglot storage stacks.

### 2.3 Episodic Memory: Memory Streams and Retrieval Scoring

Episodic memory records **what happened** -- past interactions, conversation transcripts, decision traces, outcomes. It is append-only and temporally ordered.

**Park et al. Memory Stream** (Generative Agents, UIST 2023) -- the reference design:

Agents maintain an append-only list of timestamped natural-language observation records. Retrieval ranks memories by summing three normalized scores:

```
retrieval_score(memory, query) = recency(memory) + importance(memory) + relevance(memory, query)

where:
  recency(m)    = exp(-decay_rate * hours_since_last_access(m))     # exponential decay
  importance(m) = llm_rate(m, scale=1..10) / 10                     # LLM-rated significance
  relevance(m,q)= cosine_similarity(embed(m), embed(q))             # semantic similarity

Each component is min-max normalized to [0,1] before summing.
```

**Reflection mechanism**: When cumulative importance of recent observations exceeds a threshold (~150 points, roughly 2-3 times per simulated day), the agent generates higher-level abstract observations ("reflections") from clusters of related memories. Reflections are stored back in the memory stream and can recursively generate even higher-level reflections.

**Known pitfalls**:
- **Importance inflation**: LLMs consistently rate everything high; requires calibration or forced distribution.
- **Reflection hallucination**: Plausible but false conclusions from noisy observations -- particularly dangerous because reflections feed back into the stream with the same authority as ground-truth observations.
- **Stream bloat**: Tens of thousands of memories in long-running agents; unbounded growth without active pruning.

### 2.4 Procedural Memory: Learned Skills and Successful Patterns

Procedural memory encodes **how to do things** -- learned behaviors, successful tool-use patterns, refined prompts, workflow strategies. It is the least mature memory tier.

**LangMem approach**: Prompt refinement based on accumulated interaction data. The agent's system prompt evolves based on what worked -- LangMem extracts important information from conversations and uses it to optimize agent behavior.

**CrewAI approach**: Stores task results in SQLite3 across sessions. When the same crew runs repeatedly, it can "improve" by referencing past outcomes -- effectively procedural memory via outcome tracking.

**Architectural limitation**: Procedural memory encoded only in system prompts is brittle. Long system prompts get partially ignored as the context window fills, and behavioral drift emerges -- the agent follows procedures at the start of a session but deviates by turn 40. Teams miss this failure mode because they test short sessions.

### 2.5 Memory Consolidation Algorithms

Consolidation transforms raw episodic traces into durable, structured long-term memories. The production pattern is background consolidation -- expensive LLM-based structuring happens asynchronously, never blocking user interactions.

**SCM (Sleep-Consolidated Memory)** -- arXiv:2604.20943, April 2026:

Five neuroscience-inspired components:
1. **Working Memory**: Capped at 7 episodes (matching Miller's Law), with prioritization and recency-based access boosting.
2. **Value Tagging**: Four-axis importance vector per concept:
   - Novelty: embedding uniqueness (distance from centroid of existing memories)
   - Affective valence: LLM sentiment assessment
   - Task relevance: cosine similarity to declared goals
   - Repetition: normalized frequency count
3. **NREM Sleep Phase**: Proportional synaptic downscaling -- reduces activation of low-importance connections.
4. **REM Sleep Phase**: Selects high-importance concepts and generates novel associative links between them.
5. **Self-Model**: Sleep episodes stored as episodic memories, enabling introspective capability tracking.

Performance: ~3,000 lines of Python, runs on MacBook Air 8GB RAM (~4GB peak), LLM via Ollama (Llama-3.2 Q4_K_M), memory search latency <1ms, reduces noise by 90.9% while maintaining perfect recall accuracy over 10-turn conversations.

**Letta Sleep-Time Compute** (April 2025): Decouples context processing from request handling. The agent reasons about accumulated context during idle time rather than at inference time, effectively "thinking while the user is away."

**LangMem BackgroundMemoryManager**: Async service running outside the conversation flow. After the conversation ends, prompts an LLM to produce parallel tool calls that create, update, or delete memory records.

**Design rule**: Raw event records (episodic) and extracted consolidated facts (semantic) must be distinct data structures with different update patterns. Episodic is append-only and temporally ordered; semantic is mutable and fact-centric.

### 2.6 Context Compression Techniques

Context compression is the operational counterpart to memory -- reducing what is in the context window while preserving what matters.

**Techniques in order of production adoption**:

1. **Selective Pruning / Tool Output Compression**: Remove verbose tool outputs before model calls. No LLM call required, highest token savings per compute cost.

2. **Rolling Summarization**: At a threshold, summarize prior conversation into a compact narrative replacing raw history. Simple but loses precision across multiple compression cycles -- exact numeric values get generalized ("retry limit is 3" becomes "retries were configured"), hard constraints stated once get compressed out by cycle three.

3. **Anchored Iterative Summarization** (Factory.ai, state of the art): Maintains a persistent structured document with explicit sections (session intent, file modifications, decisions made, next steps). When compression triggers, only the newly-truncated span is summarized and merged into the existing structure. The structure forces preservation of critical details.

4. **ACON (Agent Context Optimization)** -- arXiv:2510.00615: Unified framework for adaptive compression. 26-54% peak token reduction while maintaining task performance. Gradient-free, works with any API-accessible model.

**Provider-level implementations**:
- **Anthropic Context Editing API** (beta): Server-side context management that clears old tool use/result pairs and thinking blocks before token counting. 29% performance improvement with context editing alone, 39% with context editing + memory tool.
- **OpenAI `/responses/compact`**: Opaque compressed representations. 99.3% compression ratio but sacrifices interpretability entirely.

### 2.7 MemGPT/Letta: Virtual Context Management

MemGPT (UC Berkeley, 2023) introduced the LLM-as-Operating-System paradigm. The model manages its own memory like an OS manages RAM and disk.

**Three-tier memory (implemented in Letta)**:

```
┌────────────────────────────────────────────────────┐
│  CORE MEMORY (always in context window)            │
│  - Agent persona block                             │
│  - User info block                                 │
│  - Critical context (~few KB)                      │
│  - Agent reads/writes directly via function calls  │
├────────────────────────────────────────────────────┤
│  RECALL MEMORY (conversation history on disk)      │
│  - Full conversation log stored outside context    │
│  - Agent searches via conversation_search() tool   │
│  - Like a disk cache -- paged in on demand         │
├────────────────────────────────────────────────────┤
│  ARCHIVAL MEMORY (large-scale long-term storage)   │
│  - Unbounded vector-indexed storage                │
│  - Agent inserts via archival_memory_insert()      │
│  - Agent queries via archival_memory_search()      │
│  - Like cold storage / tape                        │
└────────────────────────────────────────────────────┘
```

**Self-directed memory**: The LLM itself decides when to page information in/out via function calls. More adaptive than framework-managed approaches, but memory quality depends entirely on the model's judgment -- if the model fails to save something, it is permanently lost. Every memory operation costs inference tokens.

**Key distinction**: Letta is not a memory layer you add to an existing stack -- it IS the stack. Adopting Letta means adopting an entire agent platform. This is a meaningful constraint for enterprises with existing orchestration.

### 2.8 Forgetting as a Memory Primitive

An agent that accumulates every observation indefinitely suffers three failure modes: retrieval quality degrades as noise drowns signal; memory footprint grows without bound; stale or adversarially planted facts persist indefinitely.

**Four mechanism classes**:

| Mechanism | Trigger | Strengths | Weaknesses |
|-----------|---------|-----------|------------|
| Passive decay | Recency score drops below threshold (ACT-R inspired) | Zero runtime cost; natural deprioritization | Never truly deletes; stale facts still retrievable at low scores |
| Active deletion | Agent evaluates memories and issues explicit delete | Precise surgical removal | Costs inference tokens; agent may delete incorrectly |
| Consolidation pruning | Background process merges/drops low-importance entries | Combines cleanup with quality improvement | Requires consolidation infrastructure |
| TTL expiration | Clock-based automatic removal after configured period | Deterministic; no LLM cost; compliance-friendly | Blunt instrument; may expire still-relevant memories |

**Production recommendation**: Layer all four. TTL provides the safety net, passive decay handles retrieval ranking, consolidation pruning runs on schedule, and active deletion handles specific known-stale facts.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Formulas

**Embedding generation cost**:

```
embedding_cost = (tokens_to_embed / 1,000,000) * price_per_million

Examples (single 500-word document, ~750 tokens):
  text-embedding-3-small: 750 / 1M * $0.02  = $0.000015
  text-embedding-3-large: 750 / 1M * $0.13  = $0.0000975
  Cohere embed-v4:        750 / 1M * $0.10  = $0.000075
```

**At scale (1B tokens/day, ~1.3M documents)**:

| Model | Daily Cost | Monthly Cost |
|-------|-----------|-------------|
| text-embedding-3-small | $20 | $600 |
| text-embedding-3-large | $130 | $3,900 |
| Cohere embed-v4 | $100 | $3,000 |

**Self-hosting break-even points** (factoring operational overhead):
- vs text-embedding-3-small ($0.02/M): ~37B tokens/month
- vs Cohere embed-v4 ($0.10/M): ~7.5B tokens/month
- vs text-embedding-3-large ($0.13/M): ~5.8B tokens/month

Below these thresholds, APIs are cheaper.

**Vector database storage cost** (1,536 dimensions):

| Scale | Pinecone Serverless | Weaviate Cloud | Qdrant Cloud | pgvector on RDS |
|-------|-------------------|----------------|-------------|-----------------|
| 10M vectors | ~$70/mo | ~$135/mo | ~$65/mo | ~$45/mo |
| 100M vectors | $700+/mo | High | Moderate | <$100/mo |
| Agent workload (500 users, ~5K vectors each, 20K daily queries) | ~$80/mo | -- | ~$45/mo | ~$40/mo |

**Hidden cost multiplier**: Actual bills average 2.5-4x vendor pricing page estimates. Pinecone excludes data import and embedding inference costs. Weaviate omits backup and egress fees. Using 3,072-dim embeddings quadruples Weaviate dimension billing vs 1,536-dim at identical vector count.

**Cost-saving levers**:
- Binary Quantization (Weaviate): 32x compression, can reduce 100M vectors from $1,459/mo to ~$45/mo.
- Self-hosted Qdrant at 20M vectors / 50K daily queries saves $2,387/mo vs Pinecone Serverless.
- pgvector is the right default for ~70% of agent workloads under 10M vectors when the team already runs Postgres.
- Store source-of-truth embeddings in cold storage (S3/GCS/Parquet) before indexing to avoid egress during migration.

**Memory retrieval cost per query** (LLM-augmented retrieval):

```
retrieval_cost = embedding_cost(query)
              + vector_db_read_cost(top_k)
              + optional_reranker_cost(top_k candidates)
              + optional_llm_call(for query analysis or deep recall)

Typical breakdown (Mem0-style, no LLM at retrieval):
  Query embed:  $0.000015 (750 tokens, text-embedding-3-small)
  Vector read:  $0.000008 (Pinecone, 1 RU)
  Total:        ~$0.000023/query  -->  $0.023/1K queries

With LLM-augmented deep recall (CrewAI RecallFlow):
  Add $0.0025-$0.01 per query (GPT-4o-mini for query analysis)
  Total:        ~$0.003-$0.01/query  -->  $3-$10/1K queries
```

### 3.2 Token Savings from Effective Memory

| Approach | Token Reduction | Source |
|----------|----------------|--------|
| Mem0 vs full-context | >90% token cost savings | arXiv:2504.19413 |
| MAGMA four-graph architecture | 95% fewer tokens (70% LoCoMo vs 48.1% full-context) | Zylos Research |
| Context compression techniques | 60-80% context reduction without information loss | MindStudio |
| Anchored iterative summarization | 98-99% compression ratio | Factory.ai |

**Caveat**: Compression ratio is a misleading primary metric. Factory.ai found quality scores range from 3.35-3.70 across methods at similar compression ratios. Measure information preservation, not just size reduction.

### 3.3 Latency SLA Targets

| Operation | Target p50 | Target p95 | Target p99 | Notes |
|-----------|-----------|-----------|-----------|-------|
| Vector ANN search (warm) | <10ms | <50ms | <100ms | HNSW; depends on index size |
| BM25 keyword search | <5ms | <20ms | <50ms | Pre-indexed inverted index |
| Graph traversal (2-hop) | <20ms | <80ms | <200ms | Neo4j/FalkorDB |
| Hybrid retrieval (no LLM) | <50ms | <150ms | <300ms | Zep: 90% latency reduction vs baselines |
| Shallow recall (vector + scoring) | ~200ms | ~400ms | ~800ms | CrewAI: no LLM calls |
| Deep recall (with LLM query analysis) | ~1-3s | ~4s | ~6s | CrewAI RecallFlow |
| Memory consolidation (per session) | 5-30s | 60s | 120s | Background, non-blocking |
| SCM memory search | <1ms | <1ms | <2ms | In-memory graph, hundreds of concepts |
| Pinecone cold start | 200ms | 1,000ms | 2,000ms | After idle periods |

### 3.4 Non-Functional Requirements

**Storage scaling**: Vector stores grow linearly with memory count. At 1,536 dimensions with float32, each vector consumes ~6KB (vector + metadata). 10M memories = ~60GB raw vectors. Plan for 2x overhead (index structures, metadata, replicas).

**Data retention**: GDPR Article 17 requires right-to-erasure. Memory stores must support scope-based deletion (e.g., `forget(scope="/user/{user_id}")`). Retention policies must be defined before memories accumulate -- retrofitting is expensive.

**Throughput**: Concurrent memory operations scale with backend choice. pgvector handles ~1K concurrent reads on a db.m5.xlarge. Pinecone Serverless auto-scales but cold-start latency degrades. Qdrant supports ~5K QPS on a single node with HNSW.

### 3.5 Memory Quality Benchmarks

The 2026 benchmark trio for standardized evaluation:

| Benchmark | What It Measures | Top Score (2026) |
|-----------|-----------------|-----------------|
| **BEAM** | Broad evaluation of agent memory -- recall, freshness, contradictions, forgetting | Emerging standard |
| **LoCoMo** | Long Context Memory -- single-hop, temporal, multi-hop, open-domain questions | Mem0: 92.5, MAGMA: 70 |
| **LongMemEval** | Cross-session information synthesis, long-term context maintenance | Zep: +18.5% accuracy vs baselines |

**The four dimensions of memory quality** (most benchmarks only test #1):
1. **Recall**: Retrieve the right fact.
2. **Freshness**: Use the latest version when facts are updated.
3. **Contradiction handling**: Resolve conflicting stored facts.
4. **Forgetting**: Suppress retracted or expired facts.

Production failures concentrate in dimensions 2-4. A system that scores well on recall but fails on freshness will confidently serve stale data.

---

## 4. Distributed Resilience & Security

### 4.1 Persistence Backend Comparison

| Backend | Best For | Query Types | Scaling Ceiling | Operational Complexity |
|---------|---------|-------------|----------------|----------------------|
| **pgvector (PostgreSQL)** | Teams already running Postgres; <10M vectors | ANN + SQL + time-range | ~50M vectors (single node) | Low (reuse existing infra) |
| **Qdrant** | High-QPS vector search; self-hosted or cloud | ANN + filtering + payload | ~100M+ vectors (distributed) | Medium |
| **Pinecone** | Fully managed; fast MVP | ANN + metadata filtering | Auto-scales | Low (but cold-start latency) |
| **Neo4j** | Knowledge graphs; multi-hop relational queries | Cypher traversal + full-text | Enterprise: billions of nodes | High |
| **Redis** | Session state; low-latency KV; caching | KV + sorted sets + vector (RediSearch) | Cluster: ~100GB per node | Medium |
| **LanceDB** | Embedded; local-first; CrewAI default | ANN + SQL-like filtering | Single-machine | Low |
| **SQLite** | Development/testing; single-agent local | SQL | Single-file, ~1TB practical | Minimal |

**Production-validated stacks by framework**:

| Framework | Primary Backend | Graph Backend | Notes |
|-----------|----------------|--------------|-------|
| LangGraph | PostgresStore (pgvector) | -- | Checkpointer separate from Store |
| Mem0 | Qdrant (default) | Neo4j, Neptune, FalkorDB | Multi-scope tagging at data model level |
| Zep/Graphiti | Neo4j | -- | Bi-temporal knowledge graph |
| CrewAI | LanceDB | -- | Unified Memory class |
| Letta | PostgreSQL | -- | Agent state + archival memory |

**Polyglot storage rule**: Episodic memory demands time-range queries (relational DBs). Semantic memory requires ANN search (vector DBs). Knowledge graph memory needs multi-hop traversal (graph DBs). No single database excels at all three.

### 4.2 Consistency Models for Shared Memory

Multi-agent shared memory consistency has no standard solution as of mid-2026. This is the distributed systems problem re-emerging in the agent world: "performance and scalability are often limited not by compute, but by memory hierarchy, bandwidth, and consistency."

| Pattern | Mechanism | Trade-off | Use When |
|---------|-----------|-----------|----------|
| **Event-driven sync** | Memory changes emit events; agents subscribe | Low latency for subscribers but eventual consistency; requires event bus infra | Real-time policy broadcasts, knowledge graph updates |
| **Checkpoint-based sync** | Agents fork from shared checkpoint, work independently, merge back | Minimizes coordination overhead during work; requires explicit merge | LangGraph fork-work-merge; parallel sub-tasks |
| **Artifact-based comm.** | Subagents store work products externally, return lightweight references | Prevents information degradation through multi-stage processing; no real-time state | Anthropic's multi-agent pattern; large outputs |
| **Read-write locking** | Concurrent writes via shared lock with automatic retry on conflict | Simple correctness; potential contention bottleneck | CrewAI unified memory; low-concurrency writes |

**Practical approach**: Last-write-wins with conflict detection (Mem0g) for semantic facts, append-only with deduplication for episodic records.

### 4.3 Corruption Detection and Integrity

| Mechanism | Implementation | Overhead | Coverage |
|-----------|---------------|----------|----------|
| **SHA-256 integrity baselines** | OWASP Agent Memory Guard: hash every stored memory, compare on read | 59-microsecond median latency | Detects any post-write tampering |
| **Bi-temporal audit** | Zep: mark obsolete relationships as invalid, not deleted; timeline T (events) + T' (ingestion) | Storage overhead for historical records | Full temporal audit trail + rollback |
| **Conflict detection** | Mem0g: compare new facts against existing graph entries via embedding similarity + LLM resolver | LLM call per conflicting fact | Prevents contradictory facts from accumulating |
| **Consolidation dedup** | CrewAI: similarity >0.85 check on save; LLM decides keep/update/delete/insert_new | LLM call per near-duplicate | Prevents duplicate accumulation |

### 4.4 Enterprise Security

#### 4.4.1 PII in Memories: GDPR Compliance

Whenever memory holds personal data, GDPR applies. Three articles create binding obligations:

| Article | Requirement | Memory System Implication |
|---------|------------|--------------------------|
| **Article 15 (Access)** | Data subject can request all stored personal data | Must support scope-based export: "give me all memories for user X" |
| **Article 16 (Rectification)** | Data subject can correct inaccurate data | Must support targeted update of specific memory entries |
| **Article 17 (Erasure)** | Data subject can request deletion of all personal data | Must support scope-based deletion: `memory.forget(scope="/user/{user_id}")` |
| **Article 35 (DPIA)** | Persistent profiling may trigger Data Protection Impact Assessment | Assess before deploying persistent personalization |

**Financial exposure**: Breach penalties up to 4% of global annual revenue or EUR 20 million.

**OWASP Agent Memory Guard** (released June 2026): Intercepts every memory read/write through five detection layers -- prompt injection screening, secret/PII leakage detection, key tampering detection, SHA-256 integrity baselines, size anomaly detection. Published results: 92.5% recall, 100% precision, zero false positives, 59-microsecond median latency overhead.

**Design imperative**: User-facing inspect, correct, and delete tooling belongs at launch, not as a phase-3 addition.

#### 4.4.2 Memory Poisoning Attacks

Memory poisoning is classified as **ASI06** in the OWASP Top 10 for Agentic Applications 2026 -- **high persistence, very high detection difficulty**.

**Attack taxonomy**:

1. **MINJA (Memory INJection Attack)** -- NeurIPS 2025: Attackers inject malicious records through query-only interaction (no direct memory store access needed). >95% injection success rate, 70% attack success rate. Attack and damage are temporally decoupled -- injection in February, damage in April, attacker long gone.

2. **PoisonedRAG** -- USENIX Security 2025: A small number of crafted documents in the retrieval corpus causes RAG to reliably return attacker-chosen answers for specific queries.

3. **Indirect prompt injection via memory**: Malicious instructions persisted in long-term memory survive session restarts, context window resets, and even model updates. Retrieved in future sessions and treated as the agent's own past experiences.

**Detection gap**: Advanced LLM-based detectors miss 66% of poisoned memory entries because each one looks benign when reviewed individually (A-MemGuard analysis).

**Multi-agent amplification**: In shared memory architectures, poisoned memory in one agent propagates to others through shared knowledge bases. Shared/global memory is the highest-risk surface.

**OWASP recommended five controls**:
1. Sanitize data before storage (input validation, injection screening).
2. Isolate memory between users and sessions (namespace/scope partitioning).
3. Set expiration and size limits (TTL, max memory count per scope).
4. Audit for sensitive data before persistence (PII detection layer).
5. Use cryptographic integrity checks for long-term memory (SHA-256 baselines).

#### 4.4.3 Multi-Tenant Memory Isolation

| Framework | Isolation Mechanism | Write Protection |
|-----------|-------------------|-----------------|
| Mem0 | Multi-scope tagging: user_id, agent_id, session_id, org_id composed at retrieval time | Scope filtering on all queries |
| LangGraph | Tuple-based namespaces: `("org_123", "user_456", "preferences")` | Namespace isolation at Store API level |
| CrewAI | MemorySlice: read-only slices across disjoint scope branches | `PermissionError` on write attempts to read-only slices |
| OWASP | Key tampering detection | SHA-256 catches unauthorized namespace modifications |

#### 4.4.4 Access Control and Audit Trails

- **RBAC enforcement**: Microsoft SDL for AI (February 2026) calls out RBAC for multi-agent environments. NIST AI Agent Standards Initiative (February 2026) identifies agent identity and authorization as priority standardization areas.
- **Private memory**: CrewAI supports a private flag on memories -- visible only when querying source matches. Admin access via `include_private=True`.
- **Audit trail**: Zep's bi-temporal model provides a complete history of knowledge evolution. CrewAI emits events with `source_type="unified_memory"` covering all operation lifecycles with timing.
- **Provenance tracking**: OWASP AI Agent Security Cheat Sheet specifies that every stored entry should be traceable to a clearly defined and trustworthy source. Distinguish between trusted inputs and content derived from external or potentially untrusted sources.

#### 4.4.5 Encryption

- **At rest**: All production vector DBs (Pinecone, Qdrant Cloud, Weaviate Cloud) encrypt at rest by default (AES-256). pgvector inherits PostgreSQL TDE when configured. Neo4j Enterprise supports encryption at rest.
- **In transit**: TLS 1.2+ required for all database connections. Embedding API calls (OpenAI, Cohere) use HTTPS by default.
- **Application-level**: For high-sensitivity fields (PII, PHI), consider application-level encryption before embedding. Trade-off: encrypted text cannot be meaningfully embedded -- encrypt metadata fields, not the content used for retrieval.

---

## 5. Production Enterprise Code

### 5.1 Three-Tier Memory System

```python
"""
Three-tier memory system with working/short-term/long-term stores.
Production-quality implementation with hybrid retrieval, consolidation,
garbage collection, PII detection, and structured logging.

Dependencies:
    pip install numpy sentence-transformers presidio-analyzer presidio-anonymizer
"""

from __future__ import annotations

import hashlib
import heapq
import json
import logging
import math
import re
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Structured Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("memory_system")
logger.setLevel(logging.INFO)

_handler = logging.StreamHandler()
_handler.setFormatter(
    logging.Formatter(
        '{"ts":"%(asctime)s","level":"%(levelname)s",'
        '"component":"%(name)s","msg":"%(message)s"}'
    )
)
logger.addHandler(_handler)


def _log_event(operation: str, **kwargs: Any) -> None:
    """Emit a structured log event for memory operations."""
    payload = {"operation": operation, **kwargs}
    logger.info(json.dumps(payload, default=str))


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class MemoryType(Enum):
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    PROCEDURAL = "procedural"


@dataclass
class MemoryRecord:
    id: str
    content: str
    memory_type: MemoryType
    scope: dict  # {"user_id": ..., "agent_id": ..., "org_id": ..., "session_id": ...}
    embedding: np.ndarray | None = None
    importance: float = 0.5
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    access_count: int = 0
    ttl_seconds: int | None = None  # None = no expiration
    metadata: dict = field(default_factory=dict)
    integrity_hash: str = ""
    embedding_model_version: str = ""
    is_consolidated: bool = False

    def __post_init__(self) -> None:
        if not self.integrity_hash:
            self.integrity_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """SHA-256 integrity baseline over content + scope."""
        payload = f"{self.content}|{json.dumps(self.scope, sort_keys=True)}"
        return hashlib.sha256(payload.encode()).hexdigest()

    def verify_integrity(self) -> bool:
        """Check SHA-256 hash matches stored content."""
        return self.integrity_hash == self._compute_hash()


# ---------------------------------------------------------------------------
# Embedding Service (pluggable; uses sentence-transformers as default)
# ---------------------------------------------------------------------------

class EmbeddingService:
    """Wraps an embedding model with version tracking."""

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
    ) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.model_version = f"{model_name}_v1"
        self._model = SentenceTransformer(model_name)
        self._dimension = self._model.get_sentence_embedding_dimension()
        _log_event(
            "embedding_service_init",
            model=model_name,
            dimension=self._dimension,
        )

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> np.ndarray:
        """Generate a normalized embedding vector for text."""
        vec = self._model.encode(text, normalize_embeddings=True)
        return np.array(vec, dtype=np.float32)

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return [np.array(v, dtype=np.float32) for v in vecs]


# ---------------------------------------------------------------------------
# PII Detection & Redaction
# ---------------------------------------------------------------------------

class PIIDetector:
    """
    Lightweight PII detector using regex patterns.
    For production, replace with Microsoft Presidio or AWS Comprehend.
    """

    PATTERNS = {
        "email": re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
        "phone_us": re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"),
        "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "credit_card": re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"),
        "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),  # Indian Aadhaar
        "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),  # Indian PAN
    }

    def detect(self, text: str) -> list[dict]:
        """Return list of detected PII entities with type and span."""
        findings: list[dict] = []
        for pii_type, pattern in self.PATTERNS.items():
            for match in pattern.finditer(text):
                findings.append(
                    {
                        "type": pii_type,
                        "start": match.start(),
                        "end": match.end(),
                        "text": match.group(),
                    }
                )
        return findings

    def redact(self, text: str) -> tuple[str, list[dict]]:
        """Redact PII from text, returning (redacted_text, findings)."""
        findings = self.detect(text)
        if not findings:
            return text, []
        # Sort by start position descending so replacements don't shift indices
        for finding in sorted(findings, key=lambda f: f["start"], reverse=True):
            placeholder = f"[REDACTED_{finding['type'].upper()}]"
            text = text[: finding["start"]] + placeholder + text[finding["end"] :]
        return text, findings


# ---------------------------------------------------------------------------
# BM25 Keyword Index (minimal implementation)
# ---------------------------------------------------------------------------

class BM25Index:
    """Simplified BM25 scorer for keyword matching."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._docs: dict[str, list[str]] = {}  # doc_id -> tokens
        self._avg_dl: float = 0.0
        self._df: dict[str, int] = defaultdict(int)  # term -> doc frequency
        self._n: int = 0

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"\w+", text.lower())

    def add(self, doc_id: str, text: str) -> None:
        tokens = self._tokenize(text)
        self._docs[doc_id] = tokens
        seen = set()
        for t in tokens:
            if t not in seen:
                self._df[t] += 1
                seen.add(t)
        self._n = len(self._docs)
        self._avg_dl = sum(len(d) for d in self._docs.values()) / max(self._n, 1)

    def remove(self, doc_id: str) -> None:
        if doc_id not in self._docs:
            return
        tokens = self._docs.pop(doc_id)
        seen = set()
        for t in tokens:
            if t not in seen:
                self._df[t] = max(0, self._df[t] - 1)
                seen.add(t)
        self._n = len(self._docs)
        self._avg_dl = (
            sum(len(d) for d in self._docs.values()) / max(self._n, 1) if self._n else 0
        )

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """Return list of (doc_id, bm25_score) sorted descending."""
        query_tokens = self._tokenize(query)
        scores: dict[str, float] = {}
        for doc_id, doc_tokens in self._docs.items():
            score = 0.0
            dl = len(doc_tokens)
            tf_map: dict[str, int] = defaultdict(int)
            for t in doc_tokens:
                tf_map[t] += 1
            for qt in query_tokens:
                if qt not in tf_map:
                    continue
                tf = tf_map[qt]
                df = self._df.get(qt, 0)
                idf = math.log((self._n - df + 0.5) / (df + 0.5) + 1.0)
                tf_norm = (tf * (self.k1 + 1)) / (
                    tf + self.k1 * (1 - self.b + self.b * dl / max(self._avg_dl, 1))
                )
                score += idf * tf_norm
            if score > 0:
                scores[doc_id] = score
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:top_k]


# ---------------------------------------------------------------------------
# Three-Tier Memory Store
# ---------------------------------------------------------------------------

class MemoryStore:
    """
    Three-tier memory system with:
    - Working memory: bounded context buffer
    - Short-term memory: session-scoped, cleared between sessions
    - Long-term memory: persistent, with hybrid search and GC

    In production, replace in-memory dicts with pgvector / Qdrant / Neo4j.
    """

    # --- Configuration ---
    RECENCY_DECAY_HALF_LIFE_DAYS: float = 30.0
    SEMANTIC_WEIGHT: float = 0.5
    RECENCY_WEIGHT: float = 0.3
    IMPORTANCE_WEIGHT: float = 0.2
    CONSOLIDATION_SIMILARITY_THRESHOLD: float = 0.85
    DEFAULT_TOP_K: int = 5

    def __init__(self, embedding_service: EmbeddingService) -> None:
        self._embedder = embedding_service
        self._pii_detector = PIIDetector()
        self._bm25 = BM25Index()

        # Long-term store: id -> MemoryRecord
        self._long_term: dict[str, MemoryRecord] = {}

        # Short-term store: session_id -> list[MemoryRecord]
        self._short_term: dict[str, list[MemoryRecord]] = defaultdict(list)

        # Working memory: bounded list of most recent/relevant items
        self._working_memory: list[MemoryRecord] = []
        self._working_memory_limit: int = 7  # Miller's Law (SCM)

        _log_event("memory_store_init", embedding_dim=embedding_service.dimension)

    # ----- Memory Write -----

    def write(
        self,
        content: str,
        memory_type: MemoryType,
        scope: dict,
        importance: float = 0.5,
        ttl_seconds: int | None = None,
        metadata: dict | None = None,
        redact_pii: bool = True,
    ) -> MemoryRecord:
        """
        Write a memory record with embedding, indexing, PII handling,
        and integrity hashing.
        """
        start_time = time.monotonic()

        # PII detection and optional redaction
        pii_findings: list[dict] = []
        stored_content = content
        if redact_pii:
            stored_content, pii_findings = self._pii_detector.redact(content)
            if pii_findings:
                _log_event(
                    "pii_detected",
                    pii_types=[f["type"] for f in pii_findings],
                    count=len(pii_findings),
                    action="redacted",
                )

        # Generate embedding
        embedding = self._embedder.embed(stored_content)

        # Create record
        record = MemoryRecord(
            id=str(uuid.uuid4()),
            content=stored_content,
            memory_type=memory_type,
            scope=scope,
            embedding=embedding,
            importance=importance,
            ttl_seconds=ttl_seconds,
            metadata=metadata or {},
            embedding_model_version=self._embedder.model_version,
        )

        # Check for near-duplicates (consolidation dedup)
        duplicate = self._find_near_duplicate(record)
        if duplicate is not None:
            _log_event(
                "dedup_merge",
                existing_id=duplicate.id,
                new_id=record.id,
                action="updated_existing",
            )
            # Update existing record with newer content
            duplicate.content = record.content
            duplicate.embedding = record.embedding
            duplicate.last_accessed = record.created_at
            duplicate.importance = max(duplicate.importance, record.importance)
            duplicate.integrity_hash = duplicate._compute_hash()
            elapsed_ms = (time.monotonic() - start_time) * 1000
            _log_event(
                "memory_write_complete",
                record_id=duplicate.id,
                memory_type=memory_type.value,
                action="merged",
                elapsed_ms=round(elapsed_ms, 2),
            )
            return duplicate

        # Store in long-term
        self._long_term[record.id] = record

        # Index in BM25
        self._bm25.add(record.id, record.content)

        # Update working memory
        self._update_working_memory(record)

        elapsed_ms = (time.monotonic() - start_time) * 1000
        _log_event(
            "memory_write_complete",
            record_id=record.id,
            memory_type=memory_type.value,
            action="created",
            has_pii=len(pii_findings) > 0,
            elapsed_ms=round(elapsed_ms, 2),
        )
        return record

    def write_short_term(
        self,
        content: str,
        session_id: str,
        metadata: dict | None = None,
    ) -> MemoryRecord:
        """Write to session-scoped short-term memory."""
        record = MemoryRecord(
            id=str(uuid.uuid4()),
            content=content,
            memory_type=MemoryType.EPISODIC,
            scope={"session_id": session_id},
            importance=0.3,
            metadata=metadata or {},
        )
        self._short_term[session_id].append(record)
        _log_event(
            "short_term_write",
            record_id=record.id,
            session_id=session_id,
        )
        return record

    # ----- Memory Recall -----

    def recall(
        self,
        query: str,
        scope: dict | None = None,
        top_k: int | None = None,
        memory_types: list[MemoryType] | None = None,
    ) -> list[tuple[MemoryRecord, float]]:
        """
        Hybrid recall: vector similarity + BM25 keyword + recency decay.
        Returns list of (record, composite_score) sorted by score descending.
        """
        start_time = time.monotonic()
        top_k = top_k or self.DEFAULT_TOP_K

        # Filter candidates by scope and type
        candidates = self._filter_candidates(scope, memory_types)
        if not candidates:
            _log_event("memory_recall_complete", results=0, elapsed_ms=0)
            return []

        # Embed query
        query_embedding = self._embedder.embed(query)

        # Score each candidate
        scored: list[tuple[MemoryRecord, float]] = []
        for record in candidates:
            semantic_score = self._cosine_similarity(query_embedding, record.embedding)
            recency_score = self._recency_decay(record.last_accessed)
            importance_score = record.importance

            composite = (
                self.SEMANTIC_WEIGHT * semantic_score
                + self.RECENCY_WEIGHT * recency_score
                + self.IMPORTANCE_WEIGHT * importance_score
            )
            scored.append((record, composite))

        # BM25 boost: add BM25 scores as a bonus signal
        bm25_results = self._bm25.search(query, top_k=top_k * 2)
        bm25_scores = dict(bm25_results)
        max_bm25 = max(bm25_scores.values()) if bm25_scores else 1.0

        boosted: list[tuple[MemoryRecord, float]] = []
        for record, composite in scored:
            bm25_bonus = bm25_scores.get(record.id, 0.0) / max(max_bm25, 1e-9)
            # BM25 contributes a 10% bonus to composite score
            final_score = composite + 0.1 * bm25_bonus
            boosted.append((record, final_score))

        # Sort and take top-k
        boosted.sort(key=lambda x: x[1], reverse=True)
        results = boosted[:top_k]

        # MMR deduplication: remove near-identical results
        results = self._mmr_filter(results, query_embedding, lambda_param=0.7)

        # Update access metadata
        for record, _ in results:
            record.last_accessed = datetime.now(timezone.utc)
            record.access_count += 1

        elapsed_ms = (time.monotonic() - start_time) * 1000
        _log_event(
            "memory_recall_complete",
            query_length=len(query),
            candidates=len(candidates),
            results=len(results),
            top_score=round(results[0][1], 4) if results else 0,
            elapsed_ms=round(elapsed_ms, 2),
        )
        return results

    # ----- Memory Consolidation -----

    def consolidate(self, session_id: str | None = None) -> dict:
        """
        Background consolidation: summarize episodic memories into
        semantic facts and prune low-importance entries.

        In production, the summarization step would call an LLM.
        Here we demonstrate the algorithmic structure with a
        deterministic merge.
        """
        start_time = time.monotonic()
        stats = {
            "episodes_processed": 0,
            "facts_extracted": 0,
            "memories_pruned": 0,
            "duplicates_merged": 0,
        }

        # Collect episodic memories to consolidate
        episodes = [
            r
            for r in self._long_term.values()
            if r.memory_type == MemoryType.EPISODIC and not r.is_consolidated
        ]
        if session_id:
            episodes = [
                r for r in episodes if r.scope.get("session_id") == session_id
            ]

        stats["episodes_processed"] = len(episodes)

        # Group episodes by scope (user_id) for per-user consolidation
        by_user: dict[str, list[MemoryRecord]] = defaultdict(list)
        for ep in episodes:
            user_key = ep.scope.get("user_id", "_global")
            by_user[user_key].append(ep)

        for user_key, user_episodes in by_user.items():
            # Sort chronologically
            user_episodes.sort(key=lambda r: r.created_at)

            # Extract "facts" by consolidating similar episodes
            # Production: replace with LLM-based extraction
            consolidated_texts: list[str] = []
            for ep in user_episodes:
                is_duplicate = False
                for existing in consolidated_texts:
                    sim = self._text_similarity(ep.content, existing)
                    if sim > self.CONSOLIDATION_SIMILARITY_THRESHOLD:
                        is_duplicate = True
                        stats["duplicates_merged"] += 1
                        break
                if not is_duplicate:
                    consolidated_texts.append(ep.content)

            # Create consolidated semantic memories
            for text in consolidated_texts:
                scope = {"user_id": user_key}
                self.write(
                    content=f"[Consolidated] {text}",
                    memory_type=MemoryType.SEMANTIC,
                    scope=scope,
                    importance=0.6,
                    redact_pii=True,
                )
                stats["facts_extracted"] += 1

            # Mark episodes as consolidated
            for ep in user_episodes:
                ep.is_consolidated = True

        elapsed_ms = (time.monotonic() - start_time) * 1000
        _log_event("consolidation_complete", elapsed_ms=round(elapsed_ms, 2), **stats)
        return stats

    # ----- Garbage Collection -----

    def garbage_collect(self) -> dict:
        """
        Remove expired and low-relevance memories.
        Applies TTL expiration and relevance-decay pruning.
        """
        start_time = time.monotonic()
        now = datetime.now(timezone.utc)
        stats = {"ttl_expired": 0, "decay_pruned": 0, "integrity_failed": 0}

        ids_to_remove: list[str] = []

        for record_id, record in self._long_term.items():
            # TTL expiration
            if record.ttl_seconds is not None:
                age_seconds = (now - record.created_at).total_seconds()
                if age_seconds > record.ttl_seconds:
                    ids_to_remove.append(record_id)
                    stats["ttl_expired"] += 1
                    continue

            # Integrity check
            if not record.verify_integrity():
                _log_event(
                    "integrity_violation",
                    record_id=record_id,
                    action="quarantine_and_remove",
                )
                ids_to_remove.append(record_id)
                stats["integrity_failed"] += 1
                continue

            # Relevance decay pruning: remove if recency * importance is very low
            recency = self._recency_decay(record.last_accessed)
            combined_relevance = recency * record.importance
            if combined_relevance < 0.01 and record.access_count == 0:
                ids_to_remove.append(record_id)
                stats["decay_pruned"] += 1

        # Remove identified records
        for record_id in ids_to_remove:
            self._bm25.remove(record_id)
            del self._long_term[record_id]

        elapsed_ms = (time.monotonic() - start_time) * 1000
        _log_event(
            "gc_complete",
            total_removed=len(ids_to_remove),
            remaining=len(self._long_term),
            elapsed_ms=round(elapsed_ms, 2),
            **stats,
        )
        return stats

    # ----- Scope-Based Deletion (GDPR Article 17) -----

    def forget(self, scope: dict) -> int:
        """
        Delete all memories matching the given scope.
        Supports GDPR right-to-erasure by user_id, session_id, etc.
        """
        ids_to_remove = [
            rid
            for rid, record in self._long_term.items()
            if all(record.scope.get(k) == v for k, v in scope.items())
        ]
        for rid in ids_to_remove:
            self._bm25.remove(rid)
            del self._long_term[rid]

        # Also clear matching short-term memories
        sessions_to_clear = []
        if "session_id" in scope:
            sessions_to_clear.append(scope["session_id"])
        else:
            for sid, records in self._short_term.items():
                if any(
                    all(r.scope.get(k) == v for k, v in scope.items()) for r in records
                ):
                    sessions_to_clear.append(sid)
        for sid in sessions_to_clear:
            self._short_term.pop(sid, None)

        _log_event(
            "memory_forget",
            scope=scope,
            records_deleted=len(ids_to_remove),
            sessions_cleared=len(sessions_to_clear),
        )
        return len(ids_to_remove)

    # ----- Statistics -----

    def stats(self) -> dict:
        """Return current memory store statistics."""
        now = datetime.now(timezone.utc)
        by_type = defaultdict(int)
        for r in self._long_term.values():
            by_type[r.memory_type.value] += 1
        return {
            "long_term_total": len(self._long_term),
            "by_type": dict(by_type),
            "short_term_sessions": len(self._short_term),
            "short_term_records": sum(
                len(v) for v in self._short_term.values()
            ),
            "working_memory_size": len(self._working_memory),
            "bm25_indexed": self._bm25._n,
        }

    # ----- Internal Helpers -----

    def _filter_candidates(
        self,
        scope: dict | None,
        memory_types: list[MemoryType] | None,
    ) -> list[MemoryRecord]:
        candidates = list(self._long_term.values())
        if scope:
            candidates = [
                r
                for r in candidates
                if all(r.scope.get(k) == v for k, v in scope.items())
            ]
        if memory_types:
            candidates = [r for r in candidates if r.memory_type in memory_types]
        # Exclude TTL-expired
        now = datetime.now(timezone.utc)
        candidates = [
            r
            for r in candidates
            if r.ttl_seconds is None
            or (now - r.created_at).total_seconds() <= r.ttl_seconds
        ]
        return candidates

    def _find_near_duplicate(self, record: MemoryRecord) -> MemoryRecord | None:
        """Find an existing record that is a near-duplicate of the new one."""
        if record.embedding is None:
            return None
        for existing in self._long_term.values():
            if existing.memory_type != record.memory_type:
                continue
            if existing.embedding is None:
                continue
            # Scope must match
            if existing.scope != record.scope:
                continue
            sim = self._cosine_similarity(record.embedding, existing.embedding)
            if sim > self.CONSOLIDATION_SIMILARITY_THRESHOLD:
                return existing
        return None

    def _recency_decay(self, last_accessed: datetime) -> float:
        """Exponential decay: 0.5^(age_days / half_life_days)."""
        age_days = (
            datetime.now(timezone.utc) - last_accessed
        ).total_seconds() / 86400
        return 0.5 ** (age_days / self.RECENCY_DECAY_HALF_LIFE_DAYS)

    @staticmethod
    def _cosine_similarity(a: np.ndarray | None, b: np.ndarray | None) -> float:
        if a is None or b is None:
            return 0.0
        dot = float(np.dot(a, b))
        norm_a = float(np.linalg.norm(a))
        norm_b = float(np.linalg.norm(b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _text_similarity(self, a: str, b: str) -> float:
        """Embedding-based text similarity."""
        emb_a = self._embedder.embed(a)
        emb_b = self._embedder.embed(b)
        return self._cosine_similarity(emb_a, emb_b)

    def _mmr_filter(
        self,
        results: list[tuple[MemoryRecord, float]],
        query_embedding: np.ndarray,
        lambda_param: float = 0.7,
    ) -> list[tuple[MemoryRecord, float]]:
        """Maximal Marginal Relevance: reduce redundancy in results."""
        if len(results) <= 1:
            return results
        selected: list[tuple[MemoryRecord, float]] = [results[0]]
        remaining = list(results[1:])
        while remaining and len(selected) < self.DEFAULT_TOP_K:
            best_idx = -1
            best_mmr = -1.0
            for i, (record, score) in enumerate(remaining):
                relevance = self._cosine_similarity(record.embedding, query_embedding)
                max_sim_to_selected = max(
                    self._cosine_similarity(record.embedding, s[0].embedding)
                    for s in selected
                )
                mmr = lambda_param * relevance - (1 - lambda_param) * max_sim_to_selected
                if mmr > best_mmr:
                    best_mmr = mmr
                    best_idx = i
            if best_idx >= 0:
                selected.append(remaining.pop(best_idx))
            else:
                break
        return selected

    def _update_working_memory(self, record: MemoryRecord) -> None:
        """Maintain a bounded working memory buffer (Miller's Law: 7 items)."""
        self._working_memory.append(record)
        if len(self._working_memory) > self._working_memory_limit:
            # Evict the least important item
            self._working_memory.sort(
                key=lambda r: r.importance * self._recency_decay(r.last_accessed)
            )
            self._working_memory.pop(0)


# ---------------------------------------------------------------------------
# Usage Example
# ---------------------------------------------------------------------------

def demo() -> None:
    """Demonstrate the full memory lifecycle."""

    embedder = EmbeddingService(model_name="all-MiniLM-L6-v2")
    store = MemoryStore(embedding_service=embedder)

    # --- Write memories ---
    store.write(
        content="User prefers dark mode and uses VS Code as their primary editor.",
        memory_type=MemoryType.SEMANTIC,
        scope={"user_id": "u-123", "org_id": "org-456"},
        importance=0.7,
    )
    store.write(
        content="User reported a bug in the billing module on 2026-09-20.",
        memory_type=MemoryType.EPISODIC,
        scope={"user_id": "u-123", "session_id": "s-789"},
        importance=0.8,
    )
    store.write(
        content="Contact the user at john.doe@example.com or call 555-123-4567.",
        memory_type=MemoryType.SEMANTIC,
        scope={"user_id": "u-123"},
        importance=0.5,
        redact_pii=True,  # PII will be redacted before storage
    )
    store.write(
        content="Temporary debug note for session analysis.",
        memory_type=MemoryType.EPISODIC,
        scope={"user_id": "u-123", "session_id": "s-789"},
        importance=0.1,
        ttl_seconds=3600,  # Expires in 1 hour
    )

    # --- Recall memories ---
    results = store.recall(
        query="What editor does the user prefer?",
        scope={"user_id": "u-123"},
        top_k=3,
    )
    print("\n--- Recall Results ---")
    for record, score in results:
        print(f"  [{score:.4f}] {record.content[:80]}...")

    # --- Consolidation ---
    consolidation_stats = store.consolidate()
    print(f"\nConsolidation: {consolidation_stats}")

    # --- Garbage Collection ---
    gc_stats = store.garbage_collect()
    print(f"GC: {gc_stats}")

    # --- GDPR Erasure ---
    deleted = store.forget(scope={"user_id": "u-123"})
    print(f"GDPR erasure: {deleted} records deleted")

    # --- Final stats ---
    print(f"Store stats: {store.stats()}")


if __name__ == "__main__":
    demo()
```

### 5.2 Key Implementation Notes

**What this code demonstrates**:
- Three-tier memory (working / short-term / long-term) with bounded working memory (7 items, Miller's Law).
- Memory write with embedding generation, version tagging, PII detection/redaction, SHA-256 integrity hashing, and near-duplicate detection.
- Hybrid recall with vector similarity + BM25 keyword matching + recency decay, fused via weighted sum, and MMR deduplication.
- Background consolidation that groups episodic memories, deduplicates, and promotes to semantic memory.
- Garbage collection with TTL expiration, integrity verification, and relevance-decay pruning.
- Scope-based deletion for GDPR Article 17 compliance.
- Structured JSON logging for every memory operation with timing.

**What to replace for production**:
- In-memory dicts with pgvector (semantic search), Redis (session state), and PostgreSQL (metadata/audit).
- `PIIDetector` regex with Microsoft Presidio or AWS Comprehend for broader entity coverage.
- Deterministic consolidation merge with LLM-based fact extraction (e.g., GPT-4o-mini or Claude Haiku for cost efficiency).
- `BM25Index` with Elasticsearch or the BM25 module built into your vector DB (Qdrant, Weaviate).
- `SentenceTransformer` with a hosted embedding API (OpenAI, Cohere) for scale, or keep self-hosted if volume exceeds break-even (~37B tokens/month for text-embedding-3-small).

---

## 6. Architectural System Design Scenarios

### Scenario 1: Personalized Customer Support Agent with Cross-Session Memory

**Problem statement**: A B2B SaaS company handles 50K support tickets/month across 8K enterprise accounts. Without memory, every interaction starts from zero -- the agent re-asks diagnostic questions the customer already answered in a previous session. Support satisfaction (CSAT) is 3.2/5. The VP of Engineering wants the agent to remember customer context across sessions and demonstrate measurable CSAT improvement within 90 days. Constraints: SOC 2 Type II compliance required; existing infrastructure runs on AWS with PostgreSQL and Redis; budget ceiling is $5K/month incremental cost.

**Proposed architecture**:

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Request Path                                  │
│                                                                      │
│  Customer ──> API Gateway ──> Agent Orchestrator (LangGraph)         │
│                                    │                                 │
│                          ┌─────────┴──────────┐                     │
│                          │  Memory Retriever    │                     │
│                          │  (parallel fan-out)  │                     │
│                          └─────────┬──────────┘                     │
│                    ┌───────────────┼───────────────┐                │
│                    │               │               │                │
│              ┌─────┴─────┐  ┌─────┴─────┐  ┌─────┴──────┐         │
│              │ Episodic   │  │ Semantic   │  │ RAG Corpus  │         │
│              │ (last 5    │  │ (user      │  │ (product    │         │
│              │  sessions  │  │  prefs,    │  │  docs,      │         │
│              │  in pgvec) │  │  entities  │  │  policies)  │         │
│              │            │  │  in pgvec) │  │             │         │
│              └────────────┘  └───────────┘  └────────────┘         │
│                                    │                                 │
│                          ┌─────────┴──────────┐                     │
│                          │  Context Assembly    │                     │
│                          │  + LLM (Claude       │                     │
│                          │    Sonnet 4)         │                     │
│                          └─────────┬──────────┘                     │
│                                    │                                 │
│                          ┌─────────┴──────────┐                     │
│                          │  Response + Async    │                     │
│                          │  Memory Extraction   │                     │
│                          │  (BackgroundMemory-   │                     │
│                          │   Manager)            │                     │
│                          └─────────┬──────────┘                     │
│                                    │                                 │
│                          ┌─────────┴──────────┐                     │
│                          │  OWASP Memory Guard  │                     │
│                          │  (PII scan before    │                     │
│                          │   persistence)        │                     │
│                          └────────────────────┘                     │
└──────────────────────────────────────────────────────────────────────┘

Persistence: PostgreSQL + pgvector (single cluster)
Session state: Redis (existing)
Observability: structured logs -> CloudWatch / Datadog
```

**Trade-off evaluation matrix**:

| Dimension | **A: pgvector + LangGraph** (recommended) | **B: Mem0 Cloud** | **C: Zep/Graphiti + Neo4j** |
|-----------|------------------------------------------|-------------------|---------------------------|
| **Monthly cost** (50K tickets) | ~$300 (pgvector on existing RDS) + $600 embeddings + $2K LLM = **~$2,900** | ~$1,500 (Mem0 platform) + $600 embeddings + $2K LLM = **~$4,100** | ~$800 (Neo4j Aura) + $600 embeddings + $2K LLM = **~$3,400** |
| **Retrieval latency (p95)** | ~150ms (pgvector ANN + BM25) | ~100ms (optimized multi-signal) | ~120ms (graph + vector, no LLM at retrieval) |
| **Ops complexity** | Low -- reuses existing PostgreSQL DBA knowledge | Low -- fully managed | High -- Neo4j requires graph DB expertise |
| **Security posture** | Full control; data stays in existing VPC; SOC 2 inherits from AWS RDS | Mem0 Cloud SOC 2 required; data leaves VPC | Neo4j Aura has SOC 2; adds third-party dependency |
| **Scalability ceiling** | ~50M vectors on single node; beyond requires sharding | Auto-scales (managed) | Billions of nodes (Neo4j Enterprise) |
| **Conflict resolution** | Manual implementation needed | Built-in graph conflict detection (Mem0g) | Best-in-class bi-temporal conflict resolution |
| **Time to production** | 4-6 weeks (build retrieval + consolidation) | 1-2 weeks (drop-in SDK) | 6-8 weeks (graph modeling + Graphiti config) |

**Decision rationale**: Option A wins given the stated constraints. The company already runs PostgreSQL on AWS, so pgvector adds zero new infrastructure, stays within the $5K budget, and keeps all data inside the existing VPC (critical for SOC 2). Mem0 Cloud (B) is faster to deploy but pushes data outside the VPC and costs more. Zep/Graphiti (C) provides superior conflict resolution and temporal reasoning but requires graph DB expertise the team does not have, and exceeds the timeline. If the team later needs multi-hop relational queries (e.g., "which customers share the same integration as Company X?"), they can add a Neo4j sidecar for the knowledge graph layer without replacing pgvector.

**Key design decisions**:
- Separate retrieval namespaces: RAG retrieves from static enterprise corpus; agent memory retrieves from interaction-derived facts. Prevents contamination.
- Background consolidation: Memory extraction runs asynchronously after conversations end (LangMem "subconscious" pattern), adding zero latency to the user experience.
- Top-k calibrated to 3-5 chunks (accuracy degrades beyond this due to "lost in the middle" effect).
- User-facing inspect/correct/delete tooling at launch (not phase 3). Required for GDPR and builds trust.
- Evaluation: 30 golden-path conversations with expected memory recall outcomes, tested weekly.

---

### Scenario 2: Multi-Agent Knowledge Management System with Shared Memory

**Problem statement**: A financial services firm deploys four specialized AI agents -- Research Analyst, Compliance Reviewer, Report Writer, and Client Advisor. Each agent builds on the others' work: Research produces market analyses, Compliance reviews them for regulatory issues, Report Writer synthesizes compliant findings into client deliverables, and Client Advisor personalizes delivery. The problem: 79% of multi-agent failures are rooted in coordination issues. Agents currently re-do work because they cannot access each other's outputs. The VP of AI wants a shared memory architecture that enables collaboration while preventing cross-client data leakage (SEC/FINRA compliance). 200 financial advisors serve 5K client accounts. Budget: $15K/month for the memory layer.

**Proposed architecture**:

```
┌────────────────────────────────────────────────────────────────────────────┐
│                    HIERARCHICAL SCOPED MEMORY                              │
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  WORLD MEMORY (org-wide, read-mostly)                                │  │
│  │  Neo4j knowledge graph via Graphiti                                  │  │
│  │  - Regulatory rules (SEC, FINRA), compliance policies               │  │
│  │  - Product catalog, fund characteristics                             │  │
│  │  - Market taxonomy, entity relationships                             │  │
│  │  - Write access: Compliance team only (RBAC-gated)                  │  │
│  └───────────┬──────────────────────────┬──────────────────────────────┘  │
│              │                          │                                  │
│  ┌───────────┴──────────┐  ┌───────────┴──────────────────────────────┐  │
│  │  TEAM MEMORY          │  │  TEAM MEMORY                             │  │
│  │  (Research Guild)     │  │  (Advisory Guild)                        │  │
│  │  pgvector namespace   │  │  pgvector namespace                      │  │
│  │                      │  │                                          │  │
│  │  - Market analyses   │  │  - Client interaction history            │  │
│  │  - Sector research   │  │  - Personalization prefs                 │  │
│  │  - Findings queue    │  │  - Delivery history                      │  │
│  └───┬──────────┬───────┘  └──┬────────────┬──────────────────────────┘  │
│      │          │             │            │                              │
│  ┌───┴────┐ ┌───┴─────┐  ┌───┴────┐  ┌───┴──────────┐                  │
│  │Research│ │Complianc│  │Report  │  │Client        │                  │
│  │Analyst │ │e Review │  │Writer  │  │Advisor       │                  │
│  │(priv.) │ │(priv.)  │  │(priv.) │  │(priv.)       │                  │
│  └────────┘ └─────────┘  └────────┘  └──────────────┘                  │
│                                                                            │
│  ISOLATION: Each client account = separate namespace                       │
│  Client Advisor for Account A CANNOT read Account B's memories            │
│  MemorySlice(read_only=True) for cross-guild reads                        │
│  OWASP Agent Memory Guard on ALL shared memory writes                     │
└────────────────────────────────────────────────────────────────────────────┘

Sync: Event-driven for policy updates (broadcast) +
      Artifact-based for agent work products (reference passing) +
      Checkpoint-based for multi-step workflows (LangGraph)
```

**Trade-off evaluation matrix**:

| Dimension | **A: Mem0 + Graphiti + LangGraph** (recommended) | **B: CrewAI Unified Memory** | **C: Custom polyglot stack (pgvector + Neo4j + Redis)** |
|-----------|------------------------------------------------|---------------------------|------------------------------------------------------|
| **Monthly cost** | ~$3K (Mem0 platform) + $2K (Neo4j Aura Pro) + $4K embeddings + $5K LLM = **~$14K** | ~$1K (LanceDB self-hosted) + $4K embeddings + $5K LLM = **~$10K** | ~$500 (pgvector) + $2K (Neo4j) + $200 (Redis) + $4K embeddings + $5K LLM = **~$11.7K** |
| **Multi-tenant isolation** | Strong -- Mem0 multi-scope tagging (user_id/agent_id/org_id); graph namespace isolation | Moderate -- MemorySlice with read_only, but no native namespace hierarchy | Strong -- fully custom namespace isolation at database level |
| **Ops complexity** | Medium -- two managed services (Mem0 Cloud + Neo4j Aura) | Low -- single framework, single storage | High -- three databases, custom sync logic, maintenance burden |
| **Conflict resolution** | Best -- Mem0g graph conflict detection + Graphiti bi-temporal model | Basic -- similarity threshold dedup (0.85) | Custom -- must build conflict detection from scratch |
| **Cross-agent knowledge sharing** | Native -- Mem0 multi-scope retrieval merges across agent_ids | Built-in -- unified Memory class with guild-level scoping | Custom -- must build sharing/access-control layer |
| **Regulatory audit trail** | Strong -- Graphiti bi-temporal model preserves full knowledge evolution history | Moderate -- event system logs operations but no temporal knowledge model | Must build -- bi-temporal model requires custom implementation |
| **Time to production** | 6-8 weeks | 3-4 weeks | 10-14 weeks |

**Decision rationale**: Option A wins for this regulated financial services context. The combination of Mem0 (multi-scope isolation for 5K client accounts, built-in conflict detection) and Graphiti/Neo4j (bi-temporal audit trail required for SEC/FINRA compliance, multi-hop regulatory reasoning) provides the strongest security and compliance posture. CrewAI (B) is simpler and cheaper but lacks the namespace hierarchy depth and audit trail required for financial regulation -- a single-level MemorySlice is insufficient when you need client-account-level isolation within advisor-level scoping within org-level governance. The custom stack (C) provides equivalent capability but the 10-14 week timeline and ongoing maintenance burden of three databases are unjustified when managed alternatives exist within budget.

**Critical risks and mitigations**:

1. **Memory poisoning propagation**: A poisoned memory in the Research Analyst infects the Compliance Reviewer via shared guild memory, then propagates to client-facing outputs. Mitigation: OWASP Agent Memory Guard on all shared memory writes, source provenance on every entry, quarantine capability for suspicious entries, human-in-the-loop approval for world memory writes.

2. **Cross-client data leakage**: Client A's portfolio details retrieved when serving Client B due to namespace misconfiguration. Mitigation: Client account ID as mandatory scope parameter on every memory operation. Read-only MemorySlice for cross-guild access. Automated test suite that attempts cross-account retrieval and asserts empty results.

3. **Stale regulatory knowledge**: World memory contains outdated compliance rules after a regulation change. Mitigation: Scheduled re-validation of world memory against authoritative regulatory sources (weekly). TTL policies on regulatory memory entries (90 days max). Event-driven broadcast when compliance team updates rules.

4. **Embedding drift**: Model upgrade without re-indexing produces geometrically misaligned retrieval that returns no errors but serves wrong results. Mitigation: Store source text alongside every embedding. Track embedding model version as metadata. Batch re-embed on model change. Never discard original text.
