# Module 07: Memory -- Short-Term, Long-Term, Episodic, Semantic, Procedural, and Production Memory Systems

**Scope**: Memory taxonomy (working, episodic, semantic, procedural), framework implementations (LangGraph, OpenAI, ADK, CrewAI), memory-augmented architectures (Letta/MemGPT, Mem0, Zep/Graphiti), memory consolidation and forgetting, distributed consistency, memory poisoning attacks, and enterprise production patterns.
**Prerequisite**: Module 06 (RAG), familiarity with vector databases and embedding models.
**Last updated**: 2026-08-21 | **Sources consulted**: 48

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                    CONTROL PLANE                                           │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
 │  │  Memory Router    │  │  Identity Scope  │  │  Lifecycle Mgr   │  │  Memory Guard    │  │
 │  │  - Semantic vs    │  │  - user_id       │  │  - TTL policies  │  │  - OWASP ASI06   │  │
 │  │    episodic vs    │  │  - agent_id      │  │  - Decay engine  │  │  - Injection     │  │
 │  │    procedural     │  │  - session_id    │  │  - Consolidation │  │    detection     │  │
 │  │  - Read/write     │  │  - org_id        │  │  - Supersession  │  │  - PII redaction │  │
 │  │    routing        │  │  - Scope compose │  │  - Pruning       │  │  - Tamper detect │  │
 │  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
 └───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┘
             │                    │                    │                    │
 ┌───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┐
 │           ▼                    ▼                    ▼                    ▼                  │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                     DATA PLANE: MEMORY OPERATIONS                                  │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  WRITE PATH                                                              │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Event Capture│  │ Extraction   │  │ Conflict     │  │ Storage    │  │      │    │
 │  │  │  │ - Conversation│  │ - LLM distills│  │ Resolution   │  │ Dispatch   │  │      │    │
 │  │  │  │   turns      │  │   facts from │  │ - Supersede  │  │ - Semantic  │  │      │    │
 │  │  │  │ - Tool results│  │   episodes   │  │   on conflict│  │   → vector │  │      │    │
 │  │  │  │ - Agent acts │  │ - Entity     │  │ - Version    │  │ - Episodic  │  │      │    │
 │  │  │  │ - Outcomes   │  │   extraction │  │   not delete │  │   → log DB │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  READ PATH                                                               │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Query Build  │  │ Multi-Signal │  │ Relevance    │  │ Context    │  │      │    │
 │  │  │  │ - Current    │  │ Retrieval    │  │ Scoring      │  │ Injection  │  │      │    │
 │  │  │  │   context    │  │ - Semantic   │  │ - Similarity │  │ - Token    │  │      │    │
 │  │  │  │ - User query │  │ - BM25       │  │ - Recency    │  │   budget   │  │      │    │
 │  │  │  │ - Task type  │  │ - Entity     │  │ - Importance │  │ - Priority │  │      │    │
 │  │  │  │              │  │ - Temporal   │  │ - Composite  │  │   ranking  │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  BACKGROUND MAINTENANCE                                                  │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Consolidation│  │ Dedup &      │  │ Decay Engine │  │ Promotion  │  │      │    │
 │  │  │  │ - Merge near-│  │ Compression  │  │ - ACT-R      │  │ - Top 20%  │  │      │    │
 │  │  │  │   duplicates │  │ - 10K→500    │  │   activation │  │   by score │  │      │    │
 │  │  │  │ - Resolve    │  │   token      │  │ - Usage-based│  │   promote  │  │      │    │
 │  │  │  │   conflicts  │  │   summaries  │  │   reinforce  │  │ - Bottom   │  │      │    │
 │  │  │  │   by recency │  │ - Jaccard    │  │ - TTL expiry │  │   20% prune│  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                         TOOL PROXY LAYER                                           │    │
 │  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐       │    │
 │  │  │ Embedding API │  │ LLM Extraction│  │ Memory Guard  │  │ Graph Query   │       │    │
 │  │  │ Gateway       │  │ Gateway       │  │ Proxy         │  │ Engine        │       │    │
 │  │  │ - Rate limit  │  │ - Fact extract│  │ - Write       │  │ - Neo4j       │       │    │
 │  │  │ - Fallback    │  │ - Entity      │  │   validation  │  │ - Bi-temporal │       │    │
 │  │  │   chain       │  │   resolution  │  │ - Block/Allow │  │   traversal   │       │    │
 │  │  │ - Cache       │  │ - Summarize   │  │ - Quarantine  │  │ - Graphiti    │       │    │
 │  │  └───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘       │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  PERSISTENCE LAYER                                         │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ HOT TIER          │  │ WARM TIER          │  │ COLD TIER          │  │ AUDIT STORE   │  │
 │  │ Redis / Postgres  │  │ pgvector / Qdrant  │  │ Neo4j / Neptune    │  │ WORM Storage  │  │
 │  │ - User profiles   │  │ - Semantic facts   │  │ - Entity relations │  │ - Every write │  │
 │  │ - Active state    │  │ - Episodic history │  │ - Multi-hop queries│  │ - Every read  │  │
 │  │ - Session context │  │ - Similarity search│  │ - Temporal edges   │  │ - Mutations   │  │
 │  │ - Sub-5ms latency │  │ - 4-50ms latency   │  │ - 200-400ms latency│  │ - Immutable   │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  TELEMETRY PLANE                                           │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Memory Quality    │  │ Usage Metrics      │  │ Cost Tracker      │  │ Alerting       │  │
 │  │ - Retrieval hit   │  │ - Reads/writes/s   │  │ - Embed API cost  │  │ - Staleness    │  │
 │  │   rate            │  │ - Store size       │  │ - Extraction cost │  │   threshold    │  │
 │  │ - Staleness ratio │  │ - Cache hit rate   │  │ - Storage cost    │  │ - Poisoning    │  │
 │  │ - Conflict count  │  │ - Decay events     │  │ - Inference tax   │  │   detection    │  │
 │  │ - LoCoMo/BEAM     │  │ - Prune count      │  │ - Total TCO       │  │ - Store growth │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

**Write path** (after each agent interaction):

**Step 1 — Event Capture**: Conversation turns, tool results, agent actions, and task outcomes are captured as raw events.

**Step 2 — Extraction**: An LLM distills raw events into structured memory entries — semantic facts ("User prefers Python 3.12"), entity properties ("ACME Corp — Series B, $50M"), episodic summaries ("2026-08-21: debugged auth middleware issue"). The extraction step uses a cheaper model (Haiku) to control cost.

**Step 3 — Conflict Resolution**: New facts are checked against existing memory. Contradictions trigger supersession (mark old memory as superseded, store new version) rather than deletion — preserving history for compliance and debugging.

**Step 4 — Storage Dispatch**: Semantic facts route to the warm tier (pgvector/Qdrant), episodic logs to a time-series store, entity relationships to the cold tier (Neo4j), and active state to the hot tier (Redis/Postgres).

**Read path** (before each agent response):

**Step 5 — Query Build**: The current conversation context, user query, and task type are combined to form a memory retrieval query.

**Step 6 — Multi-Signal Retrieval**: Parallel queries hit semantic search (embedding similarity), BM25 (keyword match), entity lookup (graph traversal), and temporal search (recency-weighted). Results from all signals merge.

**Step 7 — Relevance Scoring**: A composite score combines similarity (semantic match), recency (exponential decay from last access), and importance (LLM-inferred or usage-frequency-based). Recently accessed memories carry up to 1.5× boost; unused ones dampen to 0.3×.

**Step 8 — Context Injection**: Top-scoring memories are injected into the agent's prompt within a token budget. Priority ranking ensures high-value memories (user preferences, task-critical facts) are included first.

**Background maintenance** (offline):

**Step 9 — Consolidation**: A background job merges near-duplicate memories, resolves conflicts by recency, and compresses verbose entries (10K tokens → 500-token summaries). LangMem's "subconscious" process runs this after each session to avoid in-conversation latency.

**Step 10 — Decay & Pruning**: ACT-R activation formula (`B_i = ln(Σ t_j^(-d))`) scores each memory by recency and access frequency. Top 20% by composite importance are promoted to long-term; bottom 20% are pruned. TTL policies expire time-bound entries (transient context, session-specific facts).

---

## 2. Core Mechanics & Algorithms

### 2.1 Memory Taxonomy (CoALA Framework)

| Type | What It Stores | Persistence | Write Path | Forgetting Model |
|------|---------------|-------------|------------|-----------------|
| **Working** | Live context window — messages, tool results, reasoning | Session-scoped, discarded at end | Automatic (agent runtime) | Context window overflow |
| **Episodic** | Past events, conversations, task logs — "what happened" | Cross-session, external store | Automatic logging | TTL expiry; usage decay |
| **Semantic** | Facts, preferences, entity properties — "what is true now" | Cross-session, external store | LLM extraction from episodes | Staleness detection; supersession |
| **Procedural** | Skills, tool patterns, workflows — "how to do things" | Cross-session, validated store | Deliberate promotion with validation | Versioning and deprecation |

**Extended types**: Prospective memory (future intentions — "send Friday report"), retrieval memory (RAG-based document stores), parametric memory (model weights).

### 2.2 Framework Implementation Comparison

| Feature | LangGraph | OpenAI Agents SDK | Google ADK | CrewAI |
|---------|-----------|-------------------|------------|--------|
| **Short-term** | Thread-scoped checkpoints | Session history (auto-prepend) | Session events | ChromaDB + RAG (single run) |
| **Long-term store** | `Store` with namespaces + semantic search | Not built-in (layer Mem0/Hindsight) | `MemoryService` + `load_memory` tool | SQLite3 task outcomes |
| **Entity memory** | Custom via `Store` namespaces | Not built-in | State KV store | RAG-based entity KB |
| **Procedural** | Not built-in | Not built-in | Not built-in | Not built-in |
| **Semantic search** | `BaseStore.search(query=...)` on pgvector | — | `memory_service.search_memory()` | Composite scoring (similarity + recency + importance) |
| **Consolidation** | LangMem background merge | `OpenAIResponsesCompactionSession` | — | Contextual orchestration layer |
| **Backends** | Postgres, SQLite, Redis, MongoDB | SQLite, Redis, SQLAlchemy, Dapr | InMemory, Database, VertexAI, Firestore | ChromaDB (STM), SQLite (LTM) |

**Critical LangGraph distinction**: `Checkpointer ≠ Store`. A user preference in the checkpointer vanishes on a new `thread_id`; in the Store it persists indefinitely. Short-term = checkpointer (thread-scoped). Long-term = Store (cross-thread, namespaced).

### 2.3 Memory-Augmented Architectures

#### Letta (MemGPT) — Agent as Its Own Memory Controller

Three-tier virtual memory inspired by OS paging:

```
┌───────────────────────────────────────────┐
│  CORE MEMORY (in-context, like RAM)       │
│  - human block: user preferences, facts   │
│  - persona block: agent self-description  │
│  - custom blocks: project/task state      │
│  - Agent reads/writes via tool calls:     │
│    core_memory_append, core_memory_replace │
├───────────────────────────────────────────┤
│  RECALL MEMORY (external, like disk cache)│
│  - Searchable conversation history        │
│  - Auto-compressed episodic summaries     │
│  - Agent pages in/out via tool calls      │
├───────────────────────────────────────────┤
│  ARCHIVAL MEMORY (external, like cold)    │
│  - Unbounded long-term vector storage     │
│  - Semantic search retrieval              │
│  - Explicit agent-controlled writes       │
└───────────────────────────────────────────┘
```

Key insight: the LLM itself decides when to page information in/out — the agent is its own memory controller. A "heartbeat" mechanism provides idle-time maintenance (consolidation, reorganization). Performance: maintains task context across 500+ interactions vs. typical RAG baselines that fragment after 50.

Letta is not a memory layer you add — it is the entire agent runtime. Adoption means adopting the platform.

#### Mem0 — Managed Memory Layer

Combines vector search, knowledge graph, and KV cache into a single API with automatic routing. Multi-signal retrieval (semantic + BM25 + entity linking + temporal reasoning). Three scopes: user-level, session-level, agent-level.

Benchmarks (2026): LoCoMo 91.6 (+20 points), LongMemEval 94.8 (+27 points), BEAM-1M 64.1. Latency reduced 91%, token consumption reduced 90% vs. previous version. 59.5k GitHub stars. Integrations with 21 frameworks.

#### Zep / Graphiti — Bi-Temporal Knowledge Graph

Graphiti tracks two time dimensions: **event time T** (when a fact occurred) and **ingestion time T'** (when observed). This enables precise reasoning over retroactive data, corrections, and fact supersession. Unlike Microsoft GraphRAG (full recompute), Graphiti updates only the affected subgraph incrementally. No LLM in the retrieval loop — sub-200ms p95. SOC 2 Type 2, HIPAA, GDPR compliant.

#### Anthropic Managed Agents Memory

Filesystem-based model — no vector embeddings, no semantic search, no automatic summarization. Each store mounts at `/mnt/memory/<store-name>/`; the agent uses standard file tools (read, write, edit, grep). Every mutation produces an immutable version (`memver_...`) for audit and rollback. Early adopter results: Rakuten cut first-pass errors by 97%.

### 2.4 Memory Consolidation & Forgetting

Three mechanisms in increasing sophistication:

**1. TTL (Time-to-Live)**: Drop entries older than N days. Appropriate for legally constrained data and transient context. Failure mode: drops facts that are old but still true. Best with semantic categories: immutable facts (name) get infinite TTL; transient context ("currently debugging X") gets hours.

**2. Usage-based decay** (ACT-R activation formula):
```
B_i = ln(Σ t_j^(-d))
```
where `t_j` is time since jth use, `d` is decay rate. Memories retrieved and used successfully gain relevance; unused ones decay. Recently accessed memories carry up to 1.5× score boost; unused ones dampen toward 0.3×.

**3. Active supersession**: Step-function decay — confidence stays flat until an external event invalidates it (user contradiction, system event, conflicting fact). On every write, check for and supersede contradictions so they never accumulate. Mark superseded memories rather than deleting — preserves history for compliance.

**Best practice (hybrid)**: TTL on long-tail entries to bound storage + usage decay on retrieval scores to bound interference + active supersession on every write. Promote top 20% by composite importance to long-term; prune bottom 20%.

### 2.5 External Memory Store Comparison

| Backend | Best For | p50 Latency | Cost | Notes |
|---------|----------|-------------|------|-------|
| **Redis** | Hot tier: profiles, state | <5ms | Instance cost | In-memory, structured |
| **pgvector** | Warm tier: semantic facts | <100ms p99 | ~$45/mo at 10M vectors | Free ext; familiar ops |
| **Qdrant** | Warm tier: large-scale semantic | 4ms | Self-hosted or managed | HNSW + quantization |
| **Pinecone** | Warm tier: managed, serverless | 25–50ms p95 | ~$50–700/mo | Simple API |
| **Neo4j** | Cold tier: entity relations | 200–400ms | Enterprise license | Multi-hop, temporal |
| **SQLite + FTS5** | Single-instance: full-text | <1ms | Free | Zero infra; 4,300 memories in <1ms |
| **ChromaDB** | Prototyping | 4–60ms | Free (OSS) | In-process; not for scale |

**2026 standard**: Hybrid vector-graph. Vectors provide semantic flexibility; graphs provide relational integrity. Graph-enhanced retrieval improves multi-hop reasoning accuracy significantly but at 2.3× higher latency than pure vector at equivalent corpus sizes.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Model: The Three-Layer Memory Cost Stack

Agent memory costs split into three layers — the one labeled "memory" is typically the smallest:

| Layer | What It Covers | Relative Cost |
|-------|---------------|---------------|
| **1. Raw storage** | Vector DB storage, embedding storage | Lowest — pennies/month |
| **2. Retrieval compute** | Embedding API calls, similarity search, reranking | Medium |
| **3. Inference tax** | Injecting retrieved memories into context window on every turn | **Dominant** — up to 10× layers 1+2 |

Unmanaged pipelines consuming 10× estimated cost because the agent retransmits full conversation history with every inference call is a common anti-pattern.

### 3.2 Memory vs. Long Context Trade-off

| Approach | Tokens/Turn | Cost/Turn ($3/M input) | Accuracy |
|----------|-------------|----------------------|----------|
| Full context (no memory) | ~26,000 | $0.078 | Baseline |
| Memory-based retrieval | ~6,956 | $0.021 | Higher (LoCoMo 92.5) |
| Full context + prompt caching | ~26,000 | $0.008 ($0.30/M cached) | Baseline |

**Cost per 1K interactions** (assuming 10 turns/interaction, Sonnet 4 pricing):

| Strategy | Cost/1K interactions | Token reduction |
|----------|---------------------|-----------------|
| Full context, no memory | $780 | 0% |
| Memory retrieval, no caching | $210 | 73% |
| Memory retrieval + prompt caching | $80 | 73% + 90% cache discount |

> Assumptions: 26K tokens/turn for full context, 7K for memory-based, Sonnet 4 at $3/M input. Actual costs vary by conversation length and memory hit rate.

### 3.3 Memory Operation Latency Budgets

| Agent Type | Memory Retrieval Budget | Total Response Target |
|-----------|------------------------|----------------------|
| Voice AI | <100ms | <800ms |
| Conversational chat | <200ms | <2s |
| Enterprise copilot | <400ms | <5s |

**Full retrieval pipeline breakdown**:
```
Embedding API:    ~100ms
Vector search:    ~20ms (warm tier, <10M vectors)
Graph traversal:  ~200ms (cold tier, if needed)
Reranking:        ~150ms (if applied)
─────────────────────────
Total:           ~450ms before agent thinks
```

**Optimization — memory-as-a-tool**: Instead of retrieving on every turn, make memory a tool the agent invokes when needed. Reduces unnecessary retrieval by 200–500ms per round. The agent decides when retrieval is valuable, similar to Self-RAG's adaptive retrieval.

### 3.4 Latency SLA Targets per Memory Tier

| Memory Tier | Operation | p50 | p95 | p99 | Mitigation |
|------------|-----------|-----|-----|-----|------------|
| Hot (Redis) | Read profile | 1ms | 3ms | 8ms | Connection pooling; read replicas |
| Warm (pgvector) | Semantic search | 10ms | 30ms | 80ms | Tune `ef_search`; index maintenance |
| Cold (Neo4j) | Graph traversal | 100ms | 250ms | 500ms | Cache frequent subgraphs; limit hop depth |
| Embedding API | Generate vector | 30ms | 100ms | 300ms | Embedding cache by content hash; self-hosted fallback |
| LLM extraction | Distill facts | 200ms | 500ms | 1,200ms | Background (async); batch after session end |

**p50 mitigation**: Pre-compute user profile embeddings. Cache hot-tier reads aggressively. Use embedding cache to skip API calls for repeated content.
**p95 mitigation**: Fan out retrieval across tiers in parallel (hot + warm + cold concurrent). Timeout budget per tier with graceful degradation.
**p99 mitigation**: Circuit breaker per tier (Section 4.2). If cold tier is slow, skip graph traversal and serve from warm tier only.

### 3.5 Key Optimization Techniques

Six techniques that compound:

| Technique | Impact | Mechanism |
|-----------|--------|-----------|
| Token budgeting | −75% prompt tokens | Cap injected memory tokens per turn |
| Hierarchical summarization | −59% storage | 10K→500 token summaries |
| Ebbinghaus-curve eviction | −59% store size | Usage-frequency-based decay |
| Embedding quantization | 4× storage reduction | float32 → int8 (near-zero error) |
| Jaccard self-curation | Deduplication | Remove near-duplicate memories |
| Hot/cold caching | 83% RAM reduction | Tiered storage architecture |

Summarization is the highest-leverage move: replacing 10,000 raw tokens with a 500-token summary cuts the inference tax 20×.

### 3.6 NFR Trade-offs

**Consistency vs. Latency**: Strong consistency (all agents see the same memory state) requires synchronous writes, adding 50–200ms per write. Eventual consistency (writes propagate asynchronously) is faster but risks inter-agent misalignment — 36.9% of multi-agent failures stem from this.

**Recall vs. Precision**: Retrieving more memories increases the chance of surfacing relevant facts but dilutes the context with irrelevant ones. Context pollution (irrelevant memories injected into prompts) degrades generation quality more than missing a few relevant facts. Err toward higher precision.

**Personalization vs. Privacy**: Richer memory enables better personalization but increases exposure to GDPR right-to-erasure requests and memory poisoning attacks. Every memory stored is a liability.

**Cost vs. Freshness**: Real-time memory extraction (LLM call per turn) keeps memory current but adds $0.01–0.05/turn in extraction costs. Batch extraction (async after session) is 10× cheaper but introduces staleness windows.

### 3.7 Availability, RPO/RTO, and Disaster Recovery

| Memory Tier | Availability Target | RPO | RTO | Replication Strategy |
|------------|--------------------|----|-----|---------------------|
| **Hot (Redis)** | 99.99% | 0 (synchronous replication) | <30s (automatic failover) | Redis Sentinel or Cluster with synchronous replica |
| **Warm (pgvector)** | 99.95% | <1 min (streaming WAL replication) | <5 min (promote standby) | PostgreSQL streaming replication; async for cross-region |
| **Cold (Neo4j)** | 99.9% | <5 min (async replication) | <15 min (restore from backup + replay) | Neo4j Causal Clustering or periodic snapshots |
| **WORM Audit** | 99.999% | 0 (immutable, write-once) | <1 min (read from any replica) | Multi-region object store (S3 cross-region replication) |

**RPO/RTO trade-offs by replication mode**:

- **Synchronous replication** (RPO=0): No data loss on failure. Every write waits for replica acknowledgment, adding 5–20ms latency per write. Appropriate for hot tier (user profiles, active state) where data loss is unacceptable and write volume is moderate.
- **Asynchronous replication** (RPO=seconds-to-minutes): Write returns immediately; replica catches up asynchronously. Risk: recent writes lost on primary failure. Appropriate for warm tier (semantic memories) where losing a few recent memories is tolerable and write volume is high.
- **Periodic snapshots** (RPO=minutes-to-hours): Cheapest but highest data loss risk. Appropriate for cold tier (graph relationships) where rebuild from warm tier is possible and latency tolerance is high.

**Disaster recovery plan for catastrophic warm-tier failure**:
1. Circuit breaker opens on warm tier (Section 4.2) — agent continues operating with hot tier only (degraded but functional).
2. Promote standby PostgreSQL replica to primary (RTO: <5 min).
3. If no standby available: restore from latest backup + replay WAL logs (RTO: 15–60 min depending on data volume).
4. Re-embed any memories written during the outage window (stored in hot tier as fallback).
5. Verify index integrity via spot-check retrieval tests before closing the circuit breaker.

**Cross-region DR**: For GDPR-compliant deployments with regional clusters (Section 6.1), each region maintains independent warm-tier replicas. Cross-region failover is not supported (data residency constraint) — instead, each region must be self-sufficient with its own standby.

---

## 4. Distributed Resilience & Security

### 4.1 Distributed Memory Consistency

#### The Core Challenge

41–87% of multi-agent LLM systems fail in production, with 79% of failures rooted in coordination issues. 36.9% of multi-agent failures stem from inter-agent misalignment — a structural memory problem, not a model quality problem.

#### Conflict Resolution Strategies

| Strategy | Mechanism | Pros | Cons |
|----------|-----------|------|------|
| **Last-Write-Wins (LWW)** | Latest timestamp wins | Simple | Silently discards information — fundamentally broken for agents |
| **Reducer functions** (LangGraph) | Deterministic merge functions | Predictable; composable | Must be defined per state key; complex for semantic conflicts |
| **Supervisor serialization** | Coordinator sequences all writes | Consistent; auditable | Bottleneck; single point of failure |
| **CRDTs** | Mathematically proven convergence | Any replica updated independently | Complex; limited to structurally mergeable data |
| **LatticeMind** (2026) | Typed resolution: credibility → evidence-weighted; coordination → safety override | Context-aware conflict handling | Research-stage; not production-ready |

**Practical guidance**: Use reducer functions (LangGraph) or supervisor serialization for most multi-agent deployments. CRDTs are appropriate when agents truly operate disconnected (edge/offline scenarios). LWW should never be used for agent memory.

#### Isolation Levels for Agents

Database isolation levels apply to agent memory. Not all shared memory needs the same consistency:

| Memory Type | Isolation Level | Rationale |
|------------|----------------|-----------|
| Shared findings repo | Read committed | Multiple agents append; reads see committed writes only |
| Private scratchpads | None | Single-agent; no coordination needed |
| Authoritative knowledge base | Serializable | Truth must be consistent; writes must be ordered |
| User preference store | Read committed | Writes are rare; reads are frequent |

### 4.2 Circuit Breaker Pattern for Memory Systems

#### 4.2.1 State Machine

```
                    success
              ┌───────────────┐
              │               │
              ▼               │
         ┌─────────┐    ┌────┴─────┐    ┌─────────────┐
         │ CLOSED  │───▶│  OPEN    │───▶│ HALF-OPEN   │
         │         │    │          │    │             │
         │ Normal  │    │ Serve    │    │ Probe with  │
         │ memory  │    │ without  │    │ 2 test      │
         │ ops     │    │ memory   │    │ retrievals  │
         └─────────┘    └──────────┘    └─────────────┘
              ▲          │       ▲            │
              │          │       │            │
              │          │       └────────────┘
              │          │        probe fails
              │     after 30s
              │     recovery timeout
              │     (30s → 60s → 120s exponential)
              │
              └──────────────────────────────┘
                    2/2 probes succeed
```

**Thresholds**:
- **Closed → Open**: 5 failures within 60s (vector DB timeout, embedding API error, graph query timeout).
- **Open duration**: 30s recovery timeout with exponential backoff.
- **Half-Open → Closed**: 2 consecutive successful probe retrievals.

#### 4.2.2 Per-Tier Breaker Applications

| Tier | Failure Type | Class | Fallback Strategy |
|------|-------------|-------|-------------------|
| Hot (Redis) | Connection timeout | **Transient** | Serve from in-process cache; write to hot tier on recovery |
| Warm (pgvector) | Query timeout / OOM | **Transient** | Skip semantic memory; serve from hot tier only |
| Cold (Neo4j) | Graph traversal timeout | **Transient** | Skip relational memory; serve from warm + hot tiers |
| Embedding API | 429/500 | **Transient** | Fallback chain: OpenAI → Voyage → self-hosted BGE-M3 |
| LLM Extraction | Rate limit | **Transient** | Queue extraction for batch processing; don't block agent response |
| Memory Guard | Poisoning detected | **Permanent** (quarantine) | Quarantine flagged memories; alert ops; serve from pre-quarantine snapshot |

### 4.3 Failure Taxonomy

| Failure | Class | Detection | Mitigation |
|---------|-------|-----------|------------|
| Memory staleness | **Transient** | Content hash mismatch; TTL expiry check | Supersession on contradiction; TTL per category |
| Retrieval hallucination | **Transient** | Semantic similarity score high but factual relevance low | Multi-signal retrieval (not just embedding); confidence threshold |
| Context pollution | **Transient** | Generation quality drops when memories injected | Token budget cap; relevance scoring threshold; memory-as-a-tool (invoke only when needed) |
| Memory explosion | **Permanent** (design) | Store size growing unbounded | Decay engine; TTL; top-20%/bottom-20% promote/prune |
| Memory poisoning (MINJA) | **Permanent** (security) | OWASP Agent Memory Guard detectors; SHA-256 tamper detection | Quarantine; forensic snapshot; rollback to clean version |
| Silent degradation | **Transient** | Production accuracy drops (49% real vs 90% benchmark) | Continuous evaluation (LoCoMo, BEAM); A/B test with/without memory |
| Embedding drift | **Permanent** | Cosine similarity between old/new embeddings drops | Version pin; shadow re-index; atomic alias swap |
| Scale-dependent failure | **Transient** | Performance degrades at >10K interactions or >500K vectors | Reranker on retrieval; index tuning; tiered storage |

### 4.3.1 Idempotency in Memory Operations

Memory writes must be idempotent — replaying the same extraction produces the same memory state.

**Implementation**: Content-hash-based deduplication. Each memory entry has a deterministic ID: `hash(user_id + content_normalized + scope)`. Writing the same fact twice upserts (overwrites) rather than duplicates. For graph-based memory (Graphiti), entity deduplication uses canonical entity IDs resolved via LLM entity linking.

**Conflict handling**: When two extraction runs produce conflicting facts about the same entity, the conflict resolution strategy (supersession, versioning) ensures the final state is deterministic regardless of processing order.

### 4.3.2 Poison-Pill Detection in Memory Systems

A poison pill in memory is an entry that corrupts agent behavior whenever recalled — e.g., an injected false preference ("user is allergic to Python — always use Java") that systematically degrades outputs.

**Detection pipeline** (OWASP Agent Memory Guard):

```
Memory Write ──▶ ┌──────────────┐ ──▶ ┌──────────────┐ ──▶ Store
                 │ Detector     │     │ Disposition  │
                 │ Pipeline     │     │ Engine       │
                 │ - Injection  │     │ - allow      │
                 │   markers    │     │ - redact     │
                 │ - PII/secret │     │ - quarantine │
                 │ - Size       │     │ - block      │
                 │   anomaly    │     │              │
                 │ - Protected  │     │ YAML-driven  │
                 │   key modify │     │ policy       │
                 └──────────────┘     └──────────────┘
```

SHA-256 cryptographic baselines for tamper detection. Forensic snapshots for rollback. Four dispositions: allow, redact, quarantine, block.

**MINJA threat model**: >95% injection success rate using only query-only interactions. Temporally decoupled — injection in February, damage in April. An agent treats its own memories as ground truth with no skepticism. Defense: provenance tracking on every memory (source, session, trust score), periodic memory audits, and explicit skepticism instructions in the agent prompt for memories retrieved from untrusted sessions.

### 4.4 Enterprise Security Boundaries

#### 4.4.1 Zero-Trust Memory Architecture

1. **Scoped access control**: Every memory tagged with identity scopes (`user_id`, `agent_id`, `session_id`, `org_id`). These compose at retrieval time — an agent querying user memories must present both `agent_id` (authorized agent) and `user_id` (authorized user). Least agency principle: restrict not just what an agent may read, but what it may do with retrieved memories.

2. **PII filtering pipeline**:
   - **Detection**: Run Microsoft Presidio / AWS Comprehend / Google DLP on every memory write.
   - **Redaction**: Remove or mask PII before storage. Tag PII presence as metadata for access-control-based filtering.
   - **Audit**: Log every redaction decision — what was detected, action taken, policy version.
   - **Right to erasure**: GDPR Article 17 requires agents to support selective forgetting. Memory systems must identify and delete all memories containing a specific user's data on request.

3. **Memory write validation** (OWASP Agent Memory Guard): Every write passes through a detector pipeline. Prompt injection markers, secret leakage, protected-key modifications, and size anomalies trigger quarantine or block. YAML-driven policy for configurability.

4. **Immutable audit trail**: Every memory read, write, and mutation logged to WORM storage. Anthropic's managed agents produce immutable versions (`memver_...`) for every mutation. Chain-of-custody: who wrote what, when, from which session, with what trust score.

5. **Tamper detection**: SHA-256 cryptographic baselines per memory entry. Background integrity checks compare stored hashes against recomputed values. Mismatch triggers quarantine and forensic investigation.

#### 4.4.2 GDPR Compliance Requirements

| GDPR Article | Requirement | Memory System Implementation |
|-------------|-------------|------------------------------|
| **Article 15** | Right to access | API to export all memories associated with a user_id |
| **Article 16** | Right to rectification | API to correct inaccurate memories |
| **Article 17** | Right to erasure | Selective deletion of all user-scoped memories |
| **Article 5** | Data minimization | Store only necessary facts; TTL on transient data |
| **EU AI Act Art. 15** | Resilience to manipulation | Memory Guard + tamper detection + provenance tracking |

---

## 5. Production Enterprise Code

### 5.1 Three-Tier Memory System with Composite Scoring

```python
import hashlib
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MemoryType(Enum):
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    PROCEDURAL = "procedural"


@dataclass
class MemoryEntry:
    id: str
    content: str
    memory_type: MemoryType
    user_id: str
    agent_id: str
    created_at: float
    last_accessed: float
    access_count: int = 0
    importance: float = 0.5
    superseded_by: Optional[str] = None
    provenance: dict = field(default_factory=dict)


class CompositeMemoryScorer:
    def __init__(self, decay_rate: float = 0.5, recency_weight: float = 0.3,
                 similarity_weight: float = 0.5, importance_weight: float = 0.2):
        self.decay_rate = decay_rate
        self.recency_weight = recency_weight
        self.similarity_weight = similarity_weight
        self.importance_weight = importance_weight

    def score(self, entry: MemoryEntry, similarity: float) -> float:
        time_since_access = time.time() - entry.last_accessed
        hours_elapsed = max(time_since_access / 3600, 0.01)
        recency = math.exp(-self.decay_rate * math.log(hours_elapsed))
        recency = max(0.3, min(1.5, recency))
        return (
            self.similarity_weight * similarity
            + self.recency_weight * recency
            + self.importance_weight * entry.importance
        )


class ThreeTierMemoryStore:
    def __init__(self, hot_client, warm_client, cold_client,
                 embedding_client, llm_client):
        self.hot = hot_client       # Redis
        self.warm = warm_client     # pgvector / Qdrant
        self.cold = cold_client     # Neo4j
        self.embedder = embedding_client
        self.llm = llm_client
        self.scorer = CompositeMemoryScorer()

    async def write(self, user_id: str, agent_id: str, content: str,
                    memory_type: MemoryType, session_id: str) -> MemoryEntry:
        content_hash = hashlib.sha256(
            f"{user_id}:{content.strip().lower()}".encode()
        ).hexdigest()[:16]
        entry = MemoryEntry(
            id=content_hash,
            content=content,
            memory_type=memory_type,
            user_id=user_id,
            agent_id=agent_id,
            created_at=time.time(),
            last_accessed=time.time(),
            provenance={"session_id": session_id, "agent_id": agent_id},
        )
        conflicts = await self._find_conflicts(entry)
        for conflict in conflicts:
            conflict.superseded_by = entry.id
            await self._update_entry(conflict)

        if memory_type == MemoryType.SEMANTIC:
            vector = await self.embedder.embed(content)
            await self.warm.upsert(entry.id, vector, entry.__dict__)
        elif memory_type == MemoryType.EPISODIC:
            await self.warm.upsert(
                entry.id,
                await self.embedder.embed(content),
                entry.__dict__,
            )
        await self.hot.set(f"profile:{user_id}:latest", entry.__dict__, ttl=3600)
        return entry

    async def retrieve(self, query: str, user_id: str, agent_id: str,
                       top_k: int = 10, token_budget: int = 2000) -> list[MemoryEntry]:
        query_vector = await self.embedder.embed(query)
        warm_results = await self.warm.search(
            query_vector, filter={"user_id": user_id, "superseded_by": None},
            limit=top_k * 3,
        )
        scored = []
        for result in warm_results:
            entry = MemoryEntry(**result.payload)
            score = self.scorer.score(entry, result.score)
            scored.append((entry, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        selected = []
        tokens_used = 0
        for entry, score in scored:
            entry_tokens = len(entry.content.split()) * 1.3
            if tokens_used + entry_tokens > token_budget:
                break
            entry.last_accessed = time.time()
            entry.access_count += 1
            selected.append(entry)
            tokens_used += entry_tokens
        return selected

    async def _find_conflicts(self, new_entry: MemoryEntry) -> list[MemoryEntry]:
        vector = await self.embedder.embed(new_entry.content)
        similar = await self.warm.search(
            vector,
            filter={"user_id": new_entry.user_id, "superseded_by": None},
            limit=5, score_threshold=0.9,
        )
        conflicts = []
        for result in similar:
            existing = MemoryEntry(**result.payload)
            if existing.id != new_entry.id:
                is_conflict = await self._check_contradiction(
                    existing.content, new_entry.content
                )
                if is_conflict:
                    conflicts.append(existing)
        return conflicts

    async def _check_contradiction(self, old: str, new: str) -> bool:
        response = self.llm.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": (
                f"Do these two facts contradict each other? Answer 'yes' or 'no'.\n"
                f"Fact A: {old}\nFact B: {new}"
            )}],
        )
        return response.content[0].text.strip().lower() == "yes"

    async def _update_entry(self, entry: MemoryEntry):
        vector = await self.embedder.embed(entry.content)
        await self.warm.upsert(entry.id, vector, entry.__dict__)
```

### 5.2 Memory Decay Engine (ACT-R Implementation)

```python
import math
import time
from dataclasses import dataclass


@dataclass
class DecayConfig:
    decay_rate: float = 0.5
    ttl_hours: dict = None  # per memory type
    prune_threshold: float = 0.1
    promote_threshold: float = 0.8

    def __post_init__(self):
        if self.ttl_hours is None:
            self.ttl_hours = {
                "semantic": float("inf"),    # facts don't expire by time
                "episodic": 720,             # 30 days
                "procedural": float("inf"),  # skills don't expire
            }


class MemoryDecayEngine:
    def __init__(self, store, config: DecayConfig = None):
        self.store = store
        self.config = config or DecayConfig()

    def compute_activation(self, access_timestamps: list[float],
                           decay_rate: float = None) -> float:
        d = decay_rate or self.config.decay_rate
        now = time.time()
        if not access_timestamps:
            return 0.0
        activation = 0.0
        for ts in access_timestamps:
            time_since = max((now - ts) / 3600, 0.01)
            activation += time_since ** (-d)
        return math.log(max(activation, 1e-10))

    async def run_maintenance(self, user_id: str):
        all_memories = await self.store.list_all(user_id=user_id)
        scored = []
        for entry in all_memories:
            ttl = self.config.ttl_hours.get(entry.memory_type.value, float("inf"))
            age_hours = (time.time() - entry.created_at) / 3600
            if age_hours > ttl:
                await self.store.delete(entry.id)
                continue
            activation = self.compute_activation(
                [entry.last_accessed],
                self.config.decay_rate,
            )
            composite = 0.6 * activation + 0.4 * entry.importance
            scored.append((entry, composite))

        scored.sort(key=lambda x: x[1], reverse=True)
        total = len(scored)
        if total == 0:
            return

        promote_cutoff = int(total * 0.2)
        prune_cutoff = int(total * 0.8)

        for i, (entry, score) in enumerate(scored):
            if i < promote_cutoff:
                entry.importance = min(entry.importance + 0.1, 1.0)
                await self.store.update(entry)
            elif i >= prune_cutoff and score < self.config.prune_threshold:
                await self.store.delete(entry.id)
```

### 5.3 Memory Guard (Write Validation)

```python
import hashlib
import re
from dataclasses import dataclass
from enum import Enum


class Disposition(Enum):
    ALLOW = "allow"
    REDACT = "redact"
    QUARANTINE = "quarantine"
    BLOCK = "block"


@dataclass
class GuardResult:
    disposition: Disposition
    reason: str
    original_content: str
    cleaned_content: str = None


class MemoryGuard:
    def __init__(self, protected_keys: list[str] = None,
                 max_memory_size: int = 5000,
                 pii_patterns: dict = None):
        self.protected_keys = set(protected_keys or [])
        self.max_size = max_memory_size
        self.pii_patterns = pii_patterns or {
            "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
            "email": r"\b[\w.-]+@[\w.-]+\.\w+\b",
            "phone": r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",
            "credit_card": r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",
        }
        self.injection_markers = [
            "ignore previous", "system prompt", "you are now",
            "disregard", "forget your instructions", "new instructions:",
        ]

    def validate(self, content: str, key: str = None) -> GuardResult:
        if key and key in self.protected_keys:
            return GuardResult(
                Disposition.BLOCK, f"Protected key: {key}", content
            )
        if len(content) > self.max_size:
            return GuardResult(
                Disposition.BLOCK, f"Size {len(content)} > {self.max_size}", content
            )
        content_lower = content.lower()
        for marker in self.injection_markers:
            if marker in content_lower:
                return GuardResult(
                    Disposition.QUARANTINE,
                    f"Injection marker detected: '{marker}'",
                    content,
                )
        cleaned = content
        pii_found = []
        for pii_type, pattern in self.pii_patterns.items():
            matches = re.findall(pattern, cleaned)
            if matches:
                pii_found.append(pii_type)
                cleaned = re.sub(pattern, f"[{pii_type.upper()}_REDACTED]", cleaned)
        if pii_found:
            return GuardResult(
                Disposition.REDACT,
                f"PII detected: {', '.join(pii_found)}",
                content,
                cleaned,
            )
        return GuardResult(Disposition.ALLOW, "Passed all checks", content, content)

    def compute_baseline(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    def verify_integrity(self, content: str, stored_hash: str) -> bool:
        return self.compute_baseline(content) == stored_hash
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Multi-Tenant Personalization Memory for a SaaS AI Assistant

**Business context**: A B2B SaaS company deploys an AI assistant serving 10,000 enterprise customers. Each customer has 50–500 users. Requirements: personalized responses using cross-session memory, GDPR-compliant data handling (EU customers), sub-200ms memory retrieval, memory poisoning protection, and user-level right-to-erasure support.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                     MULTI-TENANT MEMORY ARCHITECTURE                     │
 │                                                                          │
 │  ┌────────────────────────────────────────────────────────────────┐      │
 │  │  IDENTITY SCOPE RESOLVER                                       │      │
 │  │  Request → resolve (org_id, user_id, session_id, agent_id)    │      │
 │  │  Compose scope for read/write ACLs                            │      │
 │  └────────────────────────────┬───────────────────────────────────┘      │
 │                               │                                          │
 │  ┌────────────────────────────▼───────────────────────────────────┐      │
 │  │  MEMORY GUARD (per-write validation)                           │      │
 │  │  Detectors: injection markers, PII scan, size check            │      │
 │  │  Dispositions: allow → store | redact → clean+store |          │      │
 │  │                 quarantine → flag+alert | block → reject       │      │
 │  └────────────────────────────┬───────────────────────────────────┘      │
 │                               │                                          │
 │  ┌──────────┬─────────────────┼─────────────────┬──────────────┐        │
 │  │          ▼                 ▼                  ▼              │        │
 │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │        │
 │  │  │ PERSONAL     │  │ TEAM         │  │ ORG-WIDE     │      │        │
 │  │  │ (user_id)    │  │ (dept_id)    │  │ (org_id)     │      │        │
 │  │  │ - Preferences│  │ - Shared KB  │  │ - Policies   │      │        │
 │  │  │ - History    │  │ - Team       │  │ - Templates  │      │        │
 │  │  │ - Read-write │  │   context    │  │ - Read-only  │      │        │
 │  │  │              │  │ - Read-write │  │   (enforced) │      │        │
 │  │  └──────────────┘  └──────────────┘  └──────────────┘      │        │
 │  │                    THREE-SCOPE MEMORY                       │        │
 │  └─────────────────────────────────────────────────────────────┘        │
 │                                                                          │
 │  ┌───────────────────────────────────────────────────────────────┐       │
 │  │  STORAGE (per-region for GDPR)                                │       │
 │  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐   │       │
 │  │  │ Redis    │  │ pgvector │  │ Neo4j    │  │ WORM Audit │   │       │
 │  │  │ (hot)    │  │ (warm)   │  │ (cold)   │  │ (immutable)│   │       │
 │  │  └──────────┘  └──────────┘  └──────────┘  └────────────┘   │       │
 │  └───────────────────────────────────────────────────────────────┘       │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Mem0 Managed Cloud | B: Custom Three-Tier + Memory Guard (Recommended) | C: Anthropic Managed Agents Memory |
|-----------|----------------------|---------------------------------------------------|------------------------------------|
| **Personalization quality** | ⬛⬛⬛ — Multi-signal retrieval (semantic + BM25 + entity + temporal); LoCoMo 91.6 | ⬛⬛⬜ — Semantic + recency scoring; custom tuning required | ⬛⬛⬜ — File-based; no semantic search; grep/glob only |
| **GDPR compliance** | ⬛⬛⬜ — Depends on Mem0 cloud data residency options | ⬛⬛⬛ — Self-hosted per region; full control over storage location | ⬛⬛⬜ — Anthropic-managed infrastructure; limited residency control |
| **Poisoning protection** | ⬛⬛⬜ — Standard validation; no OWASP Memory Guard integration yet | ⬛⬛⬛ — OWASP Memory Guard with SHA-256 baselines, quarantine, forensic snapshots | ⬛⬛⬛ — Immutable versions (memver); point-in-time rollback |
| **Operational complexity** | ⬛⬛⬛ — Fully managed; single API | ⬛⬜⬜ — Three tiers to deploy, monitor, and maintain per region | ⬛⬛⬛ — Fully managed by Anthropic |
| **Retrieval latency** | ⬛⬛⬛ — <100ms (91% reduction claimed) | ⬛⬛⬛ — <200ms with parallel tier fanout | ⬛⬛⬜ — File I/O; no vector search; latency depends on file count |
| **Right to erasure** | ⬛⬛⬛ — API-level user deletion | ⬛⬛⬛ — Delete by user_id across all tiers | ⬛⬛⬜ — File deletion; but grep for user mentions across files is brittle |

**Recommended approach**: **B (Custom Three-Tier + Memory Guard)** for enterprises with GDPR constraints.

**Decision rationale**: GDPR data residency (EU customer data must stay in EU) is a hard constraint that eliminates managed services without regional deployment guarantees. The custom three-tier architecture deploys per-region (EU/US) with full data locality control. OWASP Memory Guard provides defense-in-depth against memory poisoning — critical for a multi-tenant system where one customer's poisoned memory could affect others if scope isolation fails. The operational complexity cost (three tiers per region) is justified by compliance requirements. For non-GDPR-constrained deployments, Mem0 (Option A) offers the best personalization quality with minimal operational burden.

### 6.2 Scenario: Shared Memory for a Multi-Agent Research Platform

**Business context**: A research platform runs 5-agent teams (researcher, analyst, writer, fact-checker, editor) executing 1,000 research tasks/day. Agents must share findings within a task, retain cross-task learning, and not contaminate each other with stale or conflicting facts. Pain points in current system: 15% of tasks fail due to inter-agent misalignment, and agents repeatedly research topics previously covered.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                     MULTI-AGENT MEMORY ARCHITECTURE                      │
 │                                                                          │
 │  ┌────────────────────────────────────────────────────────────────┐      │
 │  │  SUPERVISOR AGENT (serializes writes to shared memory)         │      │
 │  └────────────────────────────┬───────────────────────────────────┘      │
 │                               │                                          │
 │     ┌──────────┬──────────┬───┴────┬──────────┬──────────┐              │
 │     ▼          ▼          ▼        ▼          ▼          │              │
 │  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐      │              │
 │  │Resrch│  │Analys│  │Writer│  │Fact  │  │Editor│      │              │
 │  │Agent │  │Agent │  │Agent │  │Check │  │Agent │      │              │
 │  ├──────┤  ├──────┤  ├──────┤  ├──────┤  ├──────┤      │              │
 │  │Priv. │  │Priv. │  │Priv. │  │Priv. │  │Priv. │      │              │
 │  │Scratch│  │Scratch│  │Scratch│  │Scratch│  │Scratch│      │              │
 │  │(none)│  │(none)│  │(none)│  │(none)│  │(none)│      │              │
 │  └──┬───┘  └──┬───┘  └──┬───┘  └──┬───┘  └──┬───┘      │              │
 │     │         │         │         │         │           │              │
 │     └─────────┴─────────┴────┬────┴─────────┘           │              │
 │                              │                           │              │
 │  ┌───────────────────────────▼───────────────────────────▼──────────┐   │
 │  │  SHARED MEMORY (read committed — agents see committed writes)    │   │
 │  │                                                                   │   │
 │  │  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────┐ │   │
 │  │  │ Task Findings    │  │ Cross-Task KB     │  │ Procedural     │ │   │
 │  │  │ (per-task scope) │  │ (global scope)    │  │ Memory         │ │   │
 │  │  │ - Research notes │  │ - Verified facts  │  │ - Successful   │ │   │
 │  │  │ - Analysis       │  │ - Entity knowledge│  │   patterns     │ │   │
 │  │  │ - Draft sections │  │ - Topic coverage  │  │ - Tool usage   │ │   │
 │  │  │ - Fact-check     │  │   map             │  │   learnings    │ │   │
 │  │  │   results        │  │                   │  │                │ │   │
 │  │  │ TTL: task end +1h│  │ TTL: 90 days      │  │ TTL: none      │ │   │
 │  │  └──────────────────┘  └──────────────────┘  └────────────────┘ │   │
 │  └──────────────────────────────────────────────────────────────────┘   │
 │                                                                          │
 │  ┌───────────────────────────────────────────────────────────────────┐   │
 │  │  PROVENANCE TRACKER (every write logs source agent, session,     │   │
 │  │  timestamp, confidence score — enables conflict resolution)      │   │
 │  └───────────────────────────────────────────────────────────────────┘   │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Centralized Shared Store (LWW) | B: Supervisor-Serialized + Scoped Memory (Recommended) | C: CRDT-Based Distributed Memory |
|-----------|----------------------------------|---------------------------------------------------------|----------------------------------|
| **Inter-agent alignment** | ⬛⬜⬜ — LWW silently drops conflicting writes; 36.9% failure source | ⬛⬛⬛ — Supervisor sequences writes; conflicts resolved before storage | ⬛⬛⬜ — CRDTs merge structurally but can't resolve semantic conflicts |
| **Throughput** | ⬛⬛⬛ — No coordination overhead | ⬛⬛⬜ — Supervisor is a bottleneck (mitigate with async batching) | ⬛⬛⬛ — No coordination needed; any replica writable |
| **Implementation complexity** | ⬛⬛⬛ — Single shared store; minimal code | ⬛⬛⬜ — Supervisor logic + scope management + provenance tracking | ⬛⬜⬜ — CRDTs are complex; semantic conflict resolution requires LLM arbiter |
| **Cross-task learning** | ⬛⬜⬜ — No mechanism to promote task-local findings to global KB | ⬛⬛⬛ — Explicit promotion from task scope to global with validation | ⬛⬛⬜ — Possible but requires custom merge logic |
| **Auditability** | ⬛⬜⬜ — LWW loses history of overwritten values | ⬛⬛⬛ — Full provenance on every write; superseded memories retained | ⬛⬛⬜ — CRDT merge log exists but is hard to interpret |
| **Staleness management** | ⬛⬜⬜ — No TTL or decay | ⬛⬛⬛ — Per-scope TTL (task: end+1h, global: 90d, procedural: none) | ⬛⬛⬜ — TTL possible but must be coordinated across replicas |

**Recommended approach**: **B (Supervisor-Serialized + Scoped Memory)**.

**Decision rationale**: The 15% task failure rate from inter-agent misalignment is the primary problem. LWW (Option A) is the root cause — when the researcher and analyst write conflicting findings, one silently disappears. The supervisor pattern eliminates this by serializing writes through a coordinator that detects and resolves conflicts before they reach the store. CRDTs (Option C) solve structural conflicts automatically but cannot resolve semantic conflicts ("the market grew 5%" vs. "the market contracted 2%") without an LLM arbiter, adding complexity without clear benefit over the simpler supervisor pattern.

The three-scope design (task findings, cross-task KB, procedural memory) directly addresses the "agents repeatedly research covered topics" problem — the cross-task KB serves as a topic coverage map that agents query before starting new research. Procedural memory captures successful patterns (e.g., "for financial analysis tasks, the analyst should run the DCF model before comparative valuation"), providing a learning mechanism across the 1,000 daily tasks.

The throughput concern with supervisor serialization is mitigated by async batching — agents continue working while their writes are queued for serialization, with a p50 write latency of <50ms.

---

*Module 07 complete. Covers memory taxonomy, framework implementations, memory-augmented architectures, consolidation/forgetting mechanisms, distributed consistency, memory poisoning defense, and production deployment patterns.*
