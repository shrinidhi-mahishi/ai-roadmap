# 07. Memory

**Sub-areas covered**: the CoALA layer taxonomy (working/episodic/semantic/procedural) and consolidation as the episodic→semantic transform · MemGPT-style OS-inspired virtual context paging (main context partitioning, FIFO queue eviction, recall/archival storage) · production tiered-memory architectures (Letta core-memory/files/archival, Mem0 additive extract→dedupe→store pipeline, Zep/Graphiti bi-temporal knowledge graph, LangGraph checkpointer-vs-store split, sleep-time compute primary/sleep-time agent split) · embedding-based recall scoring (Generative Agents recency/importance/relevance formula) and decay/eviction/forgetting policies · token economics with explicit `$/1k runs` formulas for write, retrieval, and consolidation, grounded in OpenAI embedding pricing, Pinecone/Turbopuffer benchmarks, and the Mem0 research paper's measured latencies · P50/P95/P99 latency SLA table across the full read/write/consolidation path · explicit availability %, RPO/RTO figures tied to checkpoint granularity, and GDPR right-to-erasure trade-offs · durable storage, OCC vs. pessimistic concurrency, distributed locking with fencing tokens, checkpointing, circuit breakers, and a failure taxonomy mapping HaluMem/STALE/UCC/context-rot to transient/permanent/poison-pill classes · Zero-Trust MCP, hard-boundary multi-tenant isolation, PII detect→redact→audit with crypto-shredding for erasure, and immutable chain-of-custody audit logging · a hardened Python memory read/write/consolidation pipeline with retries, circuit breakers, fallback chains, and structured logging · two enterprise system-design scenarios with trade-off matrices

---

## 1. System Topology & Data Flow

A production agent-memory system is not a single database but four cooperating subsystems — a **working-memory context window**, an **episodic (recall) store**, a **semantic (archival/graph) store**, and an asynchronous **consolidation pipeline** that moves data from the second into the third — sitting behind one control plane and one persistence substrate. The diagram below places MemGPT's paging model, Letta's tiering, Mem0's extract→dedupe→store loop, Zep/Graphiti's bi-temporal graph, and LangGraph's checkpointer/store split into the generic planes they occupy.

```
                    ┌────────────────────────────────────────────────────────────────────────────┐
                    │                              CONTROL PLANE                                   │
                    │                                                                               │
                    │  ┌──────────────────┐   ┌────────────────────┐   ┌─────────────────────────┐ │
                    │  │ Memory Router /   │──▶│ Budget / Eviction    │──▶│ PII / Redaction Gate     │ │
                    │  │ Policy Engine     │   │ Supervisor            │   │ (detect→redact→audit,   │ │
                    │  │ (working vs.      │   │ FIFO queue overflow  │   │  pre-write, §4.7)        │ │
                    │  │  episodic vs.     │   │ check; <80% of ctx   │   └────────────┬─────────────┘ │
                    │  │  semantic read/   │   │ window (Letta), §2.1 │                │ clear? write   │
                    │  │  write route)     │   └──────────┬───────────┘                │ proceeds       │
                    │  └────────┬─────────┘              │ evict → summarize →         │                │
                    │           │ query type              │ recall storage write         ▼                │
                    │           │                          ▼                  ┌─────────────────────────┐ │
                    │           │             ┌─────────────────────────┐    │ Consolidation Scheduler / │ │
                    │           └────────────▶│ Retrieval Policy /       │◀───│ Sleep-Time Controller     │ │
                    │                         │ Decay-Rescoring Engine   │    │ (primary agent: no core-  │ │
                    │                         │ (recency·importance·     │    │  memory-edit tools;       │ │
                    │                         │  relevance, §2.3; TTL/   │    │  sleep-time agent: holds  │ │
                    │                         │  supersession, §2.4)     │    │  all memory-edit tools,   │ │
                    │                         └────────────┬─────────────┘    │  §2.2, runs off critical  │ │
                    │                                       │                  │  path)                    │ │
                    │                                       │                  └─────────────┬─────────────┘ │
                    └───────────────────────────────────────┼────────────────────────────────┼───────────────┘
                                                               │ retrieved items                │ consolidated
                                                               │ into working memory             │ semantic writes
                    ┌──────────────────────────────────────────▼────────────────────────────────▼──────────────┐
                    │                                     DATA PLANE                                             │
                    │                                                                                            │
                    │  ┌────────────────┐   ┌──────────────────────┐   ┌───────────────────┐  ┌──────────────┐ │
                    │  │ Working Memory  │   │ Write Path:            │   │ Read Path: Hybrid   │  │ Consolidation │ │
                    │  │ (context window;│──▶│ Extract (LLM call) →   │──▶│ Retrieval           │  │ Pipeline       │ │
                    │  │ core memory     │   │ Dedupe (MD5 hash,      │   │ dense (ANN) +       │  │ (episodic →    │ │
                    │  │ blocks pinned,  │   │ Mem0-style, §2.2) →    │   │ sparse (BM25) +     │  │ semantic       │ │
                    │  │ <50k chars /    │   │ Embed → route to       │   │ entity/graph match  │  │ synthesis,     │ │
                    │  │ <20 blocks,     │   │ episodic + semantic    │   │ + decay/importance   │  │ batched/async, │ │
                    │  │ Letta, §2.1)    │   │ stores                 │   │ rerank              │  │ §2.2)          │ │
                    │  └────────────────┘   └───────────┬────────────┘   └──────────┬──────────┘  └───────┬───────┘ │
                    │                                     │                           │                     │        │
                    └─────────────────────────────────────┼───────────────────────────┼─────────────────────┼────────┘
                                                             │                           │                     │
                    ┌─────────────────────────────────────▼───────────────────────────▼─────────────────────▼────────┐
                    │                                  TOOL PROXY LAYER                                                │
                    │  ┌────────────────────────┐   ┌──────────────────────────┐   ┌───────────────────────────────┐ │
                    │  │ MCP Tool Gateway         │   │ Circuit-Breaker-Gated      │   │ Fallback Chain Dispatcher       │ │
                    │  │ (Zero-Trust, deny-by-    │   │ Store Clients (vector DB,  │   │ vector store down → BM25/       │ │
                    │  │  default; bank_id/tenant │   │ graph DB, relational store;│   │ cached recall → skip retrieval  │ │
                    │  │  derived from authenti-  │   │ Closed→Open→Half-Open,     │   │ (answer w/o memory context) →   │ │
                    │  │  cated caller only, §4.6)│   │ per-dependency, §4.5)      │   │ consolidation failure → defer   │ │
                    │  └────────────────────────┘   └──────────────────────────┘   │ to next cycle, non-blocking     │ │
                    │                                                                └───────────────────────────────┘ │
                    └────────────────────────────────────────────┬───────────────────────────────────────────────────┘
                                                                     │
                    ┌────────────────────────────────────────────▼───────────────────────────────────────────────────┐
                    │                                  PERSISTENCE LAYER                                               │
                    │  ┌────────────────┐ ┌────────────────────┐ ┌───────────────────┐ ┌─────────────────────────┐  │
                    │  │ Checkpoint Store │ │ Episodic / Recall    │ │ Semantic / Archival │ │ Temporal Knowledge Graph │  │
                    │  │ (LangGraph        │ │ Store (lossless      │ │ Store (vector DB;   │ │ (Zep/Graphiti; bi-       │  │
                    │  │ PostgresSaver /   │ │ conversation-message │ │ namespace-per-tenant│ │ temporal valid_at/       │  │
                    │  │ RedisStore; full  │ │ DB; Postgres/Mongo   │ │ hard boundary, §4.6;│ │ transaction_at; invalid_ │  │
                    │  │ graph-state       │ │ WAL; never           │ │ Pinecone/Turbopuffer│ │ at on contradiction,     │  │
                    │  │ snapshot per      │ │ InMemorySaver in     │ │ /pgvector)          │ │ never hard-delete, §2.2) │  │
                    │  │ super-step, §2.1) │ │ prod, §4.1)          │ │                     │ │                          │  │
                    │  └────────────────┘ └────────────────────┘ └───────────────────┘ └─────────────────────────┘  │
                    └──────────────────────────────────────────────┬───────────────────────────────────────────────────┘
                                                                        │
                    ┌──────────────────────────────────────────────▼───────────────────────────────────────────────────┐
                    │                              TELEMETRY / OBSERVABILITY SINKS                                        │
                    │  Immutable, append-only memory-access audit log (identity, intent, tool-call sequence, affected-    │
                    │  record IDs, before/after hash, cryptographic chain-of-custody, §4.8) · circuit-breaker state +     │
                    │  fallback-tier metrics · per-stage P50/P95/P99 latency + cost-per-1k-runs dashboards (§3.6-3.7) ·   │
                    │  decay/consolidation health (HaluMem-style update-success rate, staleness-detection rate, §4.4)     │
                    └───────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A user turn enters the **Control Plane**; the Memory Router classifies whether this turn needs a write (new fact to remember), a read (retrieve context before responding), or both — mirroring MemGPT's function-executor pattern of chaining memory operations before yielding control back to the user. (2) On the write side, the **Budget/Eviction Supervisor** first checks whether the working-memory context window (or Letta's pinned core-memory blocks) is approaching its token/char ceiling; if so it evicts the oldest FIFO-queue entries, writes a recursive summary at index 0, and flushes the evicted raw messages to the **episodic/recall store** — this is MemGPT's virtual-context-management paging model applied literally. (3) Every candidate write passes through the **PII/Redaction Gate** before touching any persistence layer — detect → redact → audit, never after-the-fact — because embeddings themselves are personal data under GDPR (§4.7) and cannot be sanitized retroactively once indexed. (4) A cleared write proceeds through the **Extract → Dedupe → Embed** pipeline (Mem0's V3 architecture): a single LLM call extracts atomic facts from the raw turn, an MD5 content hash prevents exact-duplicate re-writes, and the surviving facts are embedded and routed to both the episodic store (lossless log) and the semantic store (vector/graph index) — Mem0's ADD-only design defers conflict resolution to retrieval-time ranking rather than write-time consolidation. (5) On the read side, the **Retrieval Policy/Decay-Rescoring Engine** issues a **hybrid retrieval** (dense ANN + sparse BM25 + entity/graph match) against the semantic store, then re-ranks candidates using the recency·importance·relevance formula (§2.3) before injecting the top-k into working memory — retrieval is never a raw similarity search alone. (6) Independently and asynchronously, the **Consolidation Scheduler / Sleep-Time Controller** runs a background **sleep-time agent** that holds all memory-editing tools and periodically synthesizes accumulated episodic traces into durable semantic memory-block edits (`memory_insert`/`memory_replace`/`memory_rethink`), while the user-facing **primary agent** holds none of those tools — this decouples conversation latency from consolidation latency entirely (§2.2). (7) Every store client (vector DB, graph DB, relational checkpoint store) sits behind a **per-dependency circuit breaker** in the Tool Proxy Layer; on sustained failure the **Fallback Chain Dispatcher** degrades gracefully — vector store down → BM25-only recall → no memory context at all — rather than blocking the user turn. (8) Zep/Graphiti's semantic store is architecturally distinct from the others: contradictory new facts **invalidate** (`invalid_at`) rather than delete an existing graph edge, preserving a full bi-temporal audit trail that directly addresses the "stale memory" failure class (§4.4). (9) Every read, write, and consolidation operation — including the exact retrieved items, the decay/importance scores that ranked them, and any redaction applied — is written to an **immutable append-only audit log** before the response streams back, closing the chain-of-custody requirement most compliance reviews actually block on (§4.8).

---

## 2. Core Mechanics & Algorithms

### 2.1 MemGPT: OS-inspired virtual context management

MemGPT (arXiv:2310.08560) treats the LLM's prompt window as **main memory (RAM)** and external databases as **disk**, with the LLM itself issuing function calls to page data between the two — mirroring OS hierarchical memory management. The main-context partition has three regions:

- **System instructions** (read-only, fixed).
- **Working context** (read/write, unstructured; holds persona/key facts — Letta's production evolution of this is the pinned **core memory block**, recommended capped at <50k chars total, <20 blocks, and kept under 80% of the total context window so there is always headroom for the current turn's retrieved items).
- **FIFO message queue** (rolling recent history; index 0 is reserved for a recursive summary of everything already evicted).

External stores are **recall storage** (a lossless, append-only DB of every message — the episodic tier) and **archival storage** (a document/vector-indexed store — the semantic tier). A **queue manager** appends new messages, and on overflow evicts the oldest entries, folds them into the recursive summary at index 0, and writes the raw evicted messages to recall storage. The **function executor** paired with a heartbeat flag lets the model chain multiple memory operations (search archival → edit working context → confirm) within a single decision cycle before yielding control.

**Algorithm — queue eviction and summarization:**

```
def on_queue_overflow(queue, working_context, token_budget):
    while total_tokens(queue) + total_tokens(working_context) > token_budget:
        oldest = queue.pop_oldest()                 # O(1), deque
        recall_storage.append(oldest)                # O(1) amortized write
        queue[0].summary = llm_summarize(             # O(k) LLM call, k = evicted batch size
            queue[0].summary, oldest)
    return queue
```

- **Complexity**: appending a new message is `O(1)`; an eviction event costs `O(1)` for the storage write plus one `O(k)`-token LLM summarization call, where `k` is the size of the evicted batch — summarization is the dominant cost, not the data movement. Archival search is `O(log N)` for an ANN index (HNSW-class) over `N` archived items.
- **Invariant**: recall storage is lossless and append-only — nothing evicted from the FIFO queue is ever destroyed, only relocated; only archival storage requires an explicit write (`archival_memory_insert`) and is **never auto-populated on context overflow** — a commonly missed distinction that means an agent that never calls the archival-write tool has no semantic long-term memory at all, no matter how much conversation it has had.

### 2.2 Memory consolidation algorithms: episodic → semantic

CoALA (arXiv:2309.02427) names the episodic→semantic transformation **consolidation**: raw, instance-specific traces are synthesized into abstracted, durable insights. Three production consolidation designs, in increasing order of write-time sophistication:

1. **Mem0 V3 (additive, ADD-only)**: extract (one LLM call pulls facts from the turn) → dedupe (MD5 hash of the normalized fact string; `O(1)` average-case hash lookup) → store (vector store for similarity, entity store for relationship-aware retrieval). No UPDATE/DELETE at write time — conflicting facts simply accumulate, and disambiguation is deferred to retrieval-time ranking. This trades write-time complexity for read-time ranking complexity, a deliberate simplification versus MemGPT-style consolidation.
2. **Letta sleep-time compute (arXiv:2504.13171)**: splits the agent into a **primary agent** (user-facing, holds no memory-editing tools) and a **sleep-time agent** (background, holds `memory_insert`/`memory_replace`/`memory_rethink`). The sleep-time agent runs continuously off the critical path, incrementally refining memory blocks — this is a Pareto improvement over in-line consolidation: ~1/5 the tokens for equivalent accuracy, or ~15% higher accuracy at equal compute budget, with 2-3× cost amortization when the same consolidated context serves multiple related queries (§3.5).
3. **Zep/Graphiti bi-temporal invalidation (arXiv:2501.13956)**: rather than overwriting or deleting a contradicted fact, Graphiti sets `invalid_at`/`expired_at` on the old graph edge and inserts a new edge carrying the updated fact, tracking both **valid time** (when the fact was true in the world) and **transaction time** (when the system learned it). This solves the stale-memory problem at the write-time architecture level rather than the read-time ranking level, and produces a full, non-destructive audit trail as a side effect.

**Complexity**: extraction is `O(1)` LLM calls per turn (fixed cost regardless of history length — MemGPT/Mem0's key advantage over full-context re-serialization, §3.4 of research). Consolidation/reflection over accumulated episodic traces is `O(n)` in the number of unconsolidated traces per batch; Graphiti's edge-invalidation check is `O(1)` per new fact against the currently-valid edge set (assuming an index on `(subject, predicate)`), not `O(N)` against the full graph history.

### 2.3 Embedding-based recall: the recency·importance·relevance scoring formula

Stanford's Generative Agents (2023) established the canonical retrieval-scoring formula, still the reference point for production episodic retrieval:

```
Score(memory) = α · Recency(memory) + β · Importance(memory) + γ · Relevance(memory, query)

Recency    = 0.99 ^ hours_since_last_accessed        # exponential decay
Importance = LLM-assigned poignancy score, 1-10       # fixed at write time
Relevance  = cosine_similarity(embed(memory), embed(query))
```

This is a **linear re-ranking pass applied after ANN retrieval**, not a replacement for it: the ANN index narrows `N` memories to a candidate pool of size `M` in `O(log N)`, and the scoring formula ranks that `O(M)` pool. Production hybrid retrieval (Mem0) extends `Relevance` into a three-way fusion of semantic (vector), BM25 (keyword), and entity-graph matching, with logical/comparison filter operators layered on top for structured queries (e.g. `user_id`, `agent_id`, `app_id`, `run_id` scoping — Mem0 treats each combination as an isolated record set).

**Invariant**: `Recency` alone is insufficient and `Importance` alone is insufficient — a purely-recency system forgets a rarely-referenced but high-stakes fact (a stored allergy) as fast as small talk, and a purely-importance system never surfaces genuinely time-sensitive context; the formula's value is specifically in combining independent, orthogonal signals rather than any single term's sophistication.

### 2.4 Forgetting and decay as a first-class mechanic, not an afterthought

Modern systems (Mem0, OBLIVION arXiv:2604.00131) treat **decay** and **eviction** as distinct mechanics with different guarantees:

| Mechanic | Effect | Removes data? | Use case |
|---|---|---|---|
| **Eviction** (hard delete, TTL expiry, supersession-on-contradiction) | Physically removes the record | Yes | Compliance/storage bounds; GDPR erasure (§4.7) |
| **Decay** (search-time re-ranking) | Dampens retrieval score, leaves data in place | No | Reduce interference from stale-but-not-wrong facts while preserving audit trail |

Mem0's decay implementation boosts recently-accessed memories up to 1.5× and dampens unused ones toward 0.3× at search time — a soft signal, reversible on next access, distinct from a hard TTL.

> ⚠️ No single decay/eviction policy is safe alone. Pure LRU famously prunes rare-but-critical facts (a stored allergy, referenced once at onboarding and never again) exactly because it optimizes for recency of *access*, not stakes of *omission*. The correct production policy composes multiple strategies simultaneously: age/TTL for routine noise, a **salience floor** that exempts high-importance-score facts from decay regardless of access recency, and **supersession-on-contradiction** (Graphiti-style invalidation, §2.2) for facts that are actively wrong rather than merely stale.

### 2.5 Memory lifecycle state machine

```
                    write (new turn)
   ┌───────────┐ ───────────────────▶ ┌────────────────┐
   │  WORKING   │                      │  EXTRACT/DEDUPE  │
   │  MEMORY    │◀──── retrieved ──────│  /EMBED (§2.2)   │
   │ (context   │      into context    └────────┬─────────┘
   │  window)   │                                │ route
   └─────┬─────┘                                │
         │ token budget                          ▼
         │ exceeded (§2.1)               ┌────────────────┐        contradiction /
         ▼                               │  EPISODIC STORE │        supersession
   ┌───────────┐   consolidation          │  (recall,       │───────────┐
   │  EVICTED / │   trigger (batched,     │  lossless)      │           │
   │  SUMMARIZED│──────────────────────▶ └────────┬─────────┘           ▼
   └───────────┘                                   │ sleep-time    ┌────────────────┐
                                                     │ consolidation │  INVALIDATED    │
                                                     ▼               │  (invalid_at set,│
                                          ┌────────────────┐         │  new edge        │
                                          │  SEMANTIC STORE │         │  inserted, §2.2) │
                                          │  (vector/graph, │         └────────────────┘
                                          │  durable)       │
                                          └────────┬─────────┘
                                                     │ decay below floor / TTL / GDPR erasure request
                                                     ▼
                                          ┌────────────────┐
                                          │  ARCHIVED (soft)│── crypto-shred / physical purge ──▶  ERASED
                                          │  or DAMPENED    │       (§4.7 — terminal from any state)
                                          └────────────────┘
```

**Invariant**: `ERASED` is reachable from every other state directly (GDPR Article 17 does not recognize a "queued for eventual deletion" state as compliant) — the erasure transition must be able to fire from `WORKING`, `EPISODIC`, `SEMANTIC`, or `INVALIDATED` without waiting for a scheduled consolidation or compaction cycle to reach it first.

### 2.6 Cross-mechanism complexity summary

| Mechanism | Per-turn cost | Complexity driver | Where cost is paid |
|---|---|---|---|
| Working-memory append | `O(1)` | Token counting only | In-request, negligible |
| Queue eviction + summarization | `O(k)` LLM tokens, `k` = evicted batch | Summarization LLM call | In-request (blocking unless async) |
| Extract → dedupe → embed (write) | `O(1)` LLM call + `O(1)` hash + `O(d)` embed, `d` = fact length | Extraction LLM call dominates (§3.1) | In-request or async queue |
| Archival/semantic ANN search | `O(log N)` | Index size `N` | In-request (read path) |
| Hybrid retrieval (dense+sparse+entity) | `O(log N)` + `O(N_sparse)` + `O(1)` graph lookup, fused | Fusion pass over candidate union | In-request (read path) |
| Recency/importance/relevance rerank | `O(M)`, `M` = candidate pool | Linear scan of already-narrowed pool | In-request, cheap relative to retrieval |
| Sleep-time consolidation | `O(n)` unconsolidated traces per batch | Consolidation LLM call size | Off critical path (async/scheduled) |
| Graph edge invalidation (Graphiti) | `O(1)` per new fact (indexed lookup) | Contradiction-check against currently-valid edge | Write time, in the consolidation or write path |

---

## 3. Token Economics & NFR Analysis

### 3.1 Embedding and extraction cost formulas (`$ per 1k runs`)

OpenAI embedding pricing (Aug 2026): `text-embedding-3-small` $0.02/1M input tokens standard ($0.01/1M batch), 1536 dim; `text-embedding-3-large` $0.13/1M ($0.065/1M batch), 3072 dim; legacy `text-embedding-ada-002` $0.10/1M ($0.05/1M batch), 1536 dim. Embeddings charge input tokens only.

**Write-path cost formula:**

```
Cost_write(1k runs) = 1000 × [ tok_extract_in  × price_llm_in
                              + tok_extract_out × price_llm_out
                              + tok_fact_embed  × price_embed ]
```

*Assumptions (stated, not sourced from the primary research file's citation list — flagged as inferred extraction-model pricing):* extraction call ≈250 input tokens (last few turns) + 80 output tokens (extracted-fact JSON), using a small/mini-class extraction model priced at $0.15/1M input, $0.60/1M output (a representative 2026 small-model tier); embedded fact text ≈40 tokens using `text-embedding-3-small` standard.

```
LLM in:    250 × 1000 = 250,000 tok = 0.25M × $0.15  = $0.0375
LLM out:    80 × 1000 =  80,000 tok = 0.08M × $0.60  = $0.0480
Embedding:  40 × 1000 =  40,000 tok = 0.04M × $0.02  = $0.0008
──────────────────────────────────────────────────────────────
Total ≈ $0.086 per 1k write-runs   (extraction LLM call ≈ 98% of cost)
```

This confirms the research finding that **write-amplification dominates infra spend, not the per-token embedding rate** — the extraction LLM call is ~55× the cost of the embedding call itself in this model.

**Retrieval-path cost formula** (Pinecone-anchored):

```
Cost_retrieval(1k runs) = 1000 × [ tok_query_embed × price_embed
                                  + read_units_per_query × price_per_RU ]
```

*Assumptions:* query ≈20 tokens; Pinecone Read Units ≈$16–18/M (using $17/M midpoint); ≈5 Read Units consumed per hybrid, metadata-filtered top-20 query.

```
Query embedding: 20 × 1000 = 20,000 tok = 0.02M × $0.02  = $0.0004
Read Units:       5 × 1000 =  5,000 RU  = 0.005M × $17/M = $0.0850
──────────────────────────────────────────────────────────────
Total ≈ $0.085 per 1k retrieval-runs   (Read Units ≈ 99.5% of cost)
```

**Consolidation-path cost formula** (sleep-time compute, batched/off-critical-path):

```
Cost_consolidation(1k runs) = 1000 × [ ctx_in_tokens × price_llm_in
                                       + summary_out_tokens × price_llm_out ]
```

*Assumptions:* one consolidation run synthesizes ≈5,000 tokens of accumulated episodic context into a ≈500-token semantic summary/memory-block edit, using a stronger reasoning-tier model at $2.50/1M input, $10/1M output (representative 2026 mid-tier reasoning-model pricing — inferred, not in the primary source list).

```
Context in:  5,000 × 1000 = 5,000,000 tok = 5.0M × $2.50 = $12.50
Summary out:   500 × 1000 =   500,000 tok = 0.5M × $10.00 = $5.00
──────────────────────────────────────────────────────────────
Total ≈ $17.50 per 1k consolidation-runs
```

Consolidation is ~200× more expensive per run than a write and ~206× more expensive than a retrieval — which is precisely why sleep-time compute batches and amortizes it rather than running it in-line per turn. The research's own vendor-estimate anchor: pre-populating a 64K-token shared context prefix for 500 users/day saves ≈$2.30/day in serving prefill cost for ≈$0.003/day in background consolidation compute `[inferred — vendor blog estimate, not independently audited]` — a ~750× return that only exists because the consolidated artifact is reused across many subsequent cheap reads rather than recomputed per query.

### 3.2 Measured retrieval latency and cost (Mem0 research paper, arXiv:2504.19413)

Mem0's own paper benchmarks memory-system search latency directly, which is a stronger source than the cost-formula estimates above:

| Retrieval mode | P50 search | P95 search | P50 total | P95 total |
|---|---|---|---|---|
| Mem0 (base, vector-only) | 0.148s | 0.200s | 0.148s | 0.200s |
| Mem0ᵍ (graph-enhanced) | 0.476s | 2.590s | 1.091s | 2.590s |
| Full-context baseline (concat entire history) | — | — | — (91% higher P95 than Mem0 base) | — |

Versus the full-context baseline, Mem0 achieves **91% lower P95 latency** and **>90% token-cost reduction**, while scoring 26% higher on LLM-as-Judge accuracy than OpenAI's memory feature on the LOCOMO benchmark. Mem0's 2026 state-of-industry report cites **6,956 tokens per retrieval call** vs. **~26,000 tokens** for full-context — a >3.7× reduction that translates directly into the retrieval-cost formula above scaling sub-linearly with conversation length, unlike full-context re-serialization which is `O(turns²)` in cumulative billed tokens.

### 3.3 Vector-store latency at scale: Pinecone and the pgvector→Turbopuffer migration

**Pinecone serverless** (official, Aug 2026): storage ≈$0.33/GB/month, Read Units ≈$16–18/M, Write Units ≈$4–4.50/M, $50/month Standard-tier minimum. Published P95: **sub-10ms on warm indexes**, but **200–800ms cold-start latency** on idle indexes — the serverless tier cannot disable this cold-start behavior. A third-party 10M-vector/768-dim benchmark reports Pinecone at 187ms P99 / 2,140 QPS vs. Milvus 312ms P99 / 1,520 QPS vs. Weaviate 345ms P99 / 1,210 QPS vs. Qdrant 241ms P99 / 1,870 QPS `[inferred — single third-party benchmark, not vendor-audited]`.

**Mem0's production migration from single-table Postgres/pgvector to per-customer Turbopuffer namespaces** is the most consequential real-world data point in the entire research set:

| Metric | Pre-migration (pgvector/HNSW, shared table) | Post-migration (Turbopuffer, per-tenant namespace) | Improvement |
|---|---|---|---|
| Tail retrieval latency | spikes to **14s** under load (query planner abandons HNSW for a prefilter plan at scale) | 70ms P90 hybrid retrieval | ~70× lower end-to-end |
| INSERT | 800ms avg | 8.12ms | 99× |
| UPDATE | 500ms | 13.7ms | 36× |
| SELECT | 50ms | 13.4ms | 3.7× |
| Recall@10 | — | 97% average | sustained across 100M→400M+ memories, 3TB+ embeddings |

The root cause of the pgvector regression is architecturally important, not just a tuning miss: at sufficient scale, the query planner **abandoned the HNSW index outright** in favor of a prefilter plan, meaning the "vector search" silently degraded into a full scan — a failure mode that is invisible until it manifests as a multi-second tail spike in production.

### 3.4 Production-scale case studies

- **Sunflower** (80K-user digital health app): a 1-day Mem0 integration produced a **70–80% token-usage reduction** and saved an estimated 3–4 engineering-weeks versus building an internal memory layer.
- **Mem0/Respan reliability layer**: publishes a **99.99% uptime SLA** across "hundreds of millions of daily logs," with full request-level cost/latency tracing — this is the strongest measured availability anchor in the research set and is used directly in §3.7's availability table.
- **`async_mode=True`** became Mem0's default in v1.0.0 after synchronous memory writes blocking the response pipeline was identified as "the most common production footgun" — i.e., the single highest-leverage latency fix in the entire memory stack was architectural (don't block on the write), not a tuning parameter.

### 3.5 Consolidation / sleep-time compute economics

Sleep-time compute amortizes consolidation LLM calls across idle cycles instead of paying them inline per-request: benchmarked as a Pareto improvement — same accuracy at **~1/5 the tokens**, or **~15% more correct answers** at equal compute budget, with **2–3× cost reduction** when the same consolidated context serves multiple related queries. `> ⚠️ Gap:` no public, vendor-neutral benchmark quantifies the LLM-call cost of MemGPT/Letta-style **in-line** (non-sleep-time) consolidation — nearly all published cost figures are for the newer async/sleep-time or Mem0 additive-extraction architectures, making an apples-to-apples "sleep-time vs. in-line" cost comparison currently unverifiable from public sources.

### 3.6 Latency SLA targets: P50/P95/P99 across the memory read/write/consolidation path

No public source discloses a formal, composed P99 SLA spanning extraction → embedding → store → retrieval → rerank for a production memory system as a single pipeline. The table below anchors every **measured** cell to the specific benchmark cited in §3.2–3.4 and extrapolates **inferred** P99 cells using the same tail-multiplier convention applied elsewhere in this roadmap (1.5–2× over P95 where P95 itself is measured, since by P95 most steady-state jitter is already absorbed and the P95→P99 gap is typically a rare whole-pipeline event rather than routine variance).

| Stage | P50 | P95 | P99 | Dominant tail cause | Mitigation |
|---|---|---|---|---|---|
| Working-memory read (in-context, no I/O) | <1ms | <1ms | ~2ms `[inferred]` | Token-counting overhead only | None needed — this is local to the request |
| Extraction LLM call (write path) | ~400ms `[inferred, small-model class]` | ~800ms `[inferred]` | ~1,500ms `[inferred]` | Provider queueing / cold model endpoint | Async write (`async_mode=True`, §3.4) so this never blocks the user-facing turn |
| Embedding call (write path) | ~50ms `[inferred]` | ~90ms `[inferred]` | ~150ms `[inferred]` | Embedding-API cold start | Connection pooling; batch small writes up to a latency-aware ceiling |
| Vector-store write (archival insert) | **8.12ms** (Turbopuffer, measured, post-migration) / 800ms (pgvector-at-scale, measured, pre-migration) | ~15ms (Turbopuffer) `[inferred]` / ~2,000ms (pgvector) `[inferred]` | ~30ms (Turbopuffer) `[inferred]` / **14,000ms** (pgvector, measured tail spike) | HNSW index abandonment by the query planner at scale (§3.3) | Per-tenant namespace sharding (Turbopuffer pattern) instead of one shared indexed table |
| Vector retrieval, base (Mem0, semantic-only) | **148ms** (measured) | **200ms** (measured) | ~320ms `[inferred, 1.6× P95]` | Embedding computation + ANN search | Warm connection pools; cache high-frequency queries |
| Graph-enhanced retrieval (Mem0ᵍ) | **476ms** search / **1,091ms** total (measured) | **2,590ms** search and total (measured) | ~4,000–4,600ms `[inferred]` | Multi-hop graph traversal cost | Route only queries that need relational reasoning through the graph path; default to base vector retrieval |
| Hybrid retrieval at scale (Turbopuffer, 400M+ memories) | — | **70ms P90** (measured) | ~120ms `[inferred]` | Cross-tenant contention at shared infra tier (mitigated by per-tenant namespace isolation) | Per-tenant namespace + hybrid index (already applied in the measured figure) |
| Pinecone serverless query, warm index | <10ms (measured) | <10ms (measured) | ~15–20ms `[inferred]` | — | Keep index warm; avoid the idle-index cold-start path entirely |
| Pinecone serverless query, cold index | 200–800ms (measured range) | 800ms (measured) | ~1,000ms+ `[inferred]` | Idle-index cold start (cannot be disabled on the serverless tier) | Keep-warm ping traffic, or a dedicated pod-based tier for latency-sensitive tenants |
| Sleep-time/consolidation batch | N/A — off critical path by design | N/A | N/A | — | Decoupled entirely from conversation latency via the primary/sleep-time agent split (§2.2) |
| **Composed synchronous read** (query embed + vector search + decay rerank) | **~200ms** `[derived: embed + Mem0-base search]` | **~300ms** `[inferred]` | **~500ms** `[inferred]` | Compounding of embedding + ANN + rerank stages | This composed figure — not any single-stage number — is what should be defended against a "sub-second memory recall" SLA |

### 3.7 Throughput: QPS capacity planning and back-pressure

**Capacity-planning formula** (single-tenant read path):

```
Sustained QPS_capacity = min(
    VectorStore_QPS_at_target_P99,   # e.g. 2,140 QPS at 187ms P99, Pinecone 10M-vector benchmark (§3.3)
    Embedding_API_TPM_limit / avg_tokens_per_query_embed,
    Extraction_LLM_TPM_limit / avg_tokens_per_write     # write path only; async, so rarely the binding constraint
)
```

At production scale, the **vector store is almost always the binding constraint on the read path**, and — per the Turbopuffer case study (§3.3) — a shared-table architecture can silently degrade from "elastic" to "the actual bottleneck" once the query planner abandons the index; per-tenant namespace sharding converts this from a shared, contention-prone resource into an embarrassingly parallel one, which is why Mem0 explicitly frames the migration as a scalability fix, not just a latency fix.

**Back-pressure design**: apply write-path back-pressure via the async queue itself (`async_mode=True`, §3.4) — a full queue signals upstream that consolidation/extraction capacity is saturated, and the correct response is to buffer or shed extraction work, never to block the user-facing turn waiting for a memory write to complete. On the read path, back-pressure is expressed as a **fallback-tier demotion** (§4.5): when the vector store's circuit breaker is open, retrieval demotes to a cheaper BM25-only or cached-recall path rather than queuing requests against an already-degraded dependency.

### 3.8 NFR Analysis: Availability, RPO/RTO tied to checkpoint granularity, and compliance trade-offs

No vendor publishes an availability SLA scoped to "a composed working+episodic+semantic+consolidation memory system." The one **measured** anchor in the research set is Mem0/Respan's reliability layer at **99.99% uptime across hundreds of millions of daily logs** (§3.4); every other figure below is an **`[inferred/recommended]`** design target derived from the durability characteristics documented in §1 and §4 — stated explicitly here because this is the section most commonly audited for exactly these numbers.

**Availability targets by deployment pattern:**

| Deployment pattern | Availability target | Basis |
|---|---|---|
| Single-region: managed vector store + Postgres episodic store, no fallback chain | **99.9%** (~8.7h/year) `[inferred]` | Bounded by the weakest external dependency — a managed vector-DB API typically publishes a ~99.9% SLA tier, and with no fallback chain the composed system inherits that ceiling directly |
| Single-region + circuit-breaker fallback (vector store degraded → BM25/cached recall) | **99.95%** (~4.4h/year) `[inferred]` | The fallback tier absorbs vector-store outages (§4.5); "answered without long-term memory context" no longer counts as a full outage |
| Multi-region active-active (replicated episodic + semantic stores) | **99.99%** (~52min/year) — **measured anchor**: Mem0/Respan's reliability layer publishes exactly this figure across hundreds of millions of daily logs | Cross-region replica failover removes single-region infra as a common-mode failure; residual risk is a correlated LLM/embedding-provider outage affecting all regions simultaneously |
| Sleep-time/consolidation subsystem | Decoupled — primary read/write path availability is **unaffected** by consolidation-pipeline outages by design | Letta's primary-agent/sleep-time-agent split (§2.2) explicitly isolates conversation-serving availability from background consolidation availability; a stalled sleep-time agent degrades memory *freshness*, not memory *availability* |

**RPO/RTO tied to persistence and checkpoint granularity:**

| Memory tier | Persistence backend | Checkpoint/update mechanism | RPO | RTO |
|---|---|---|---|---|
| Working memory / thread state | LangGraph `PostgresSaver`/`RedisStore` (never `InMemorySaver` in production, §4.1) | Full graph-state snapshot per super-step | **Seconds** (a snapshot is written after every super-step) | **Seconds–minutes** — resume from the last committed checkpoint; this is also what enables time-travel debugging and human-in-the-loop interruption without losing state |
| Episodic / recall store | Postgres/MongoDB with WAL-based replication | Continuous append-only log write (synchronous or async-queued with at-least-once delivery) | **Near-zero** for synchronous writes; bounded by queue-flush interval (commonly seconds) for async writes | **Minutes** — standard OLTP replica failover |
| Semantic / archival store (vector) | Managed serverless (Pinecone) or self-hosted (Turbopuffer/pgvector) with replication | Per-write insert + periodic backup snapshot | **Minutes to hours**, depending on backup cadence; managed serverless tiers typically replicate near-synchronously | **Hours** for a full HNSW rebuild if the index itself is corrupted (the pgvector-at-scale precedent, §3.3); **minutes** for managed serverless failover |
| Semantic store (temporal knowledge graph, Zep/Graphiti) | Neo4j/FalkorDB or a proprietary context-graph engine | Bi-temporal invalidation — contradicted facts are marked `invalid_at`, never deleted, so the full fact history is inherently self-archiving | **Effectively zero** for fact history (nothing is destroyed on update); full-graph-rebuild RPO is tied to the batch/CDC ingestion cadence feeding the graph | **Hours** for a full graph rebuild — Zep's own architecture runs millions of small, mostly-cold graphs rather than one large graph specifically to bound this |
| Consolidation checkpoints (memory-block edits) | Versioned memory-block store (Letta logs `memory_replace`/`memory_rethink` operations) | Snapshot taken before each consolidation-run overwrite | **One consolidation cycle** (hours, since sleep-time runs are batched/scheduled, not continuous) | **Minutes** — revert to the prior memory-block version |

**Trade-off 1 — consolidation frequency vs. cost/accuracy.** Running consolidation more frequently (smaller batches, shorter RPO on the semantic store) means paying the ~$17.50/1k-run consolidation cost (§3.1) more often against smaller, less-amortized context — eroding the 2–3× cost-reuse benefit sleep-time compute is specifically designed to capture (§3.5). Running it less frequently widens the effective RPO on the semantic tier (facts learned in session N aren't reflected in memory blocks until the next scheduled consolidation) but maximizes reuse of each consolidated artifact across the largest possible number of subsequent cheap reads. There is no cost-free middle: the correct default is to batch consolidation on a schedule sized to the workload's actual staleness tolerance (e.g., hourly for a fast-moving support agent, daily for a personal-assistant memory), exposed as an explicit, monitored parameter rather than a fixed global constant.

**Trade-off 2 — retention vs. GDPR compliance.** Embeddings are personal data under GDPR Art. 17 — research cited in §4.7 shows ~40–70% of sensitive content is reconstructible from sentence-length embeddings via straightforward inversion, and soft-delete (mark-and-filter) is legally insufficient because the EDPB requires erasure to be **verifiable and irreversible**, not merely suppressed from query results. The two available mitigations trade differently: **scheduled physical purge/compaction** is cheap and simple but widens the effective erasure RPO to "next scheduled compaction," which may not satisfy a regulator's expectation of prompt erasure; **crypto-shredding** (per-subject encryption key, destroyed on erasure request) achieves near-instant, cryptographically verifiable erasure without a full index rewrite, but adds per-subject key-management overhead to every write and read in the pipeline. For regulated workloads, crypto-shredding is the recommended default specifically because it decouples "erasure completed" from "index maintenance schedule," which is the gap that makes soft-delete legally fragile in the first place.

**Trade-off 3 — checkpoint granularity vs. write latency.** LangGraph's per-super-step full-state snapshot gives the tightest possible RPO (seconds) on working-memory/thread state, but every super-step now pays a synchronous (or near-synchronous) write to the checkpoint store — for a high-throughput agent issuing many tool calls per turn, this can become a meaningful fraction of per-turn latency. Batching checkpoints (e.g., snapshot only at turn boundaries, not every internal tool-call step) cuts write overhead proportionally but widens the RPO to "since the last turn boundary," meaning a mid-turn crash loses more in-flight state. The reference pattern is to checkpoint at every super-step for state that gates human-in-the-loop approval or billing-relevant tool calls (where losing in-flight state is unacceptable), and to batch checkpoints for purely conversational turns where a full turn replay is cheap and low-risk.

---

## 4. Distributed Resilience & Security

### 4.1 Durable storage and consistency models for long-term memory

Production long-term memory stores require the same durability discipline as any OLTP system. LangGraph explicitly mandates `PostgresSaver`/`PostgresStore`, `MongoDBStore`, or `RedisStore` in production and warns against `InMemorySaver`/`InMemoryStore`, which do not survive process restarts. Two concurrency-control patterns apply to memory writes, chosen by write-contention profile:

- **Optimistic concurrency control (OCC)**: assumes conflicts are rare; validates read-set versions at commit time and retries on conflict (DynamoDB/Cosmos conditional writes, HTTP ETags/`If-Match`, version vectors for multi-leader replication). Recommended for the read-heavy, low-write-contention profile typical of per-user memory stores — one vendor's guidance puts this at ~80% of distributed-caching scenarios. `[inferred]` Per-user/per-session partitioning (§4.6) naturally reduces cross-user write contention to near-zero, making OCC with per-record versioning the dominant pattern for agent memory specifically.
- **Pessimistic locking / distributed locks** (Redis, ZooKeeper, etcd): needed when concurrent writes to the *same* memory record are frequent — e.g., multiple agents/tools writing to one shared Letta memory block simultaneously. Requires **fencing tokens** (monotonically increasing) so a stale lock holder that resumes after a crash or GC pause cannot corrupt state that a newer holder has already modified.

### 4.2 Distributed locking, checkpointing, and dead-letter handling

- **Distributed locking**: a per-record lock (Redis `SET NX` with TTL, or a Postgres advisory lock keyed on the memory-record ID) must be acquired before a shared memory block is edited, with a fencing token attached to every write so a delayed writer from a previous lock generation is rejected rather than silently applied.
- **Checkpointing**: LangGraph's checkpointer snapshots full graph state — including in-progress memory-write operations — at each super-step, enabling time-travel debugging, resumption after a crash, and human-in-the-loop interruption without losing conversational state (§3.8's RPO/RTO table quantifies this). `[inferred]` For archival/long-term memory outside the graph-state boundary, checkpointing is delegated to the underlying store's own durability guarantees (Postgres WAL, vector-store replication) rather than a separate agent-level checkpoint.
- **Dead-letter handling**: a write that fails extraction/embedding/storage repeatedly (a poison-pill candidate, §4.4) and a consolidation batch that fails to complete after N attempts should both land in a dead-letter store tagged with the failure reason, the last-known-good state, and enough context (raw turn content + content hash) to replay deterministically once the underlying issue is fixed — never silently dropped, since a dropped write is functionally indistinguishable from an undetected hallucinated omission (§4.4's HaluMem finding).

### 4.3 Circuit breakers for memory-store calls

The standard three-state breaker (Closed → Open → Half-Open) applies to every external memory-store dependency (vector DB, graph DB, embedding API, extraction LLM):

- **Closed**: normal traffic; the breaker counts failures/latency over a sliding window (a 60-second window with a 5-failure threshold is a commonly cited default).
- **Open**: fail-fast, no network call attempted; immediate fallback (empty result set, BM25-only retrieval, or a cached/stale response).
- **Half-Open**: after a cooldown (commonly 60s, tunable to 300s for slow-recovering providers), a single probe request tests recovery before fully reopening traffic.

Recommended topology: **one breaker per dependency** (vector store, graph store, embedding API, extraction LLM independently), never a single global breaker — a global breaker conflates unrelated failures and prevents failover to still-healthy alternatives. Retries must occur *inside* the breaker's failure-counting window, not wrapped around it, or a retry storm pollutes the breaker's window and delays it from ever tripping. A memory-store outage should degrade a single feature (no personalization context this turn) rather than fail the entire agent request.

### 4.4 Failure taxonomy: transient, permanent, poison-pill — mapped to documented memory failure classes

| Class | Definition | Memory-specific examples (research-documented) | Mitigation |
|---|---|---|---|
| **Transient** | Resolves on retry without intervention | Vector-store 503, embedding-API timeout, extraction-LLM rate-limit 429 | Retry with exponential backoff + full jitter; honor `Retry-After` |
| **Permanent** | Fails identically on every retry | Embedding-dimension mismatch after a model migration; auth failure to the memory store; a memory record referencing a `user_id` deleted from the identity system | Never retry — fail fast to the fallback chain (§4.5) |
| **Poison-pill** | A specific input deterministically breaks the same step every time | A malformed turn that crashes the extraction parser on every attempt; an implicit-conflict query pattern the model can never resolve (STALE benchmark, best frontier model only 55.2% accuracy on 1,200 evaluation queries) | Idempotency-keyed claim-before-execute (below) + dead-letter after N attempts |

Three documented, named failure classes sit underneath this generic taxonomy and are specific to memory systems rather than generic distributed-systems failures:

- **Memory-operation hallucination (HaluMem, arXiv:2511.03506)**: the first benchmark to evaluate hallucination *at the operation level* (extraction, updating, QA) rather than only end-to-end QA. Finding: **memory-updating correct-update rates are below 50% for all evaluated systems**, and **omission rates exceed 50%** — the dominant failure is not fabrication but *silent failure to extract/update a memory at all*, which then blocks any correct downstream update. Systems reporting "hallucination rates below 2%" in this study are partly an artifact of very few samples reaching the update stage in the first place, not strong suppression.
- **Implicit conflict / stale memory (STALE, arXiv:2605.06527)**: a later observation invalidates an earlier belief *without explicit negation*, requiring commonsense inference to detect. Three sub-failures: **State Resolution failure** (the model never detects the prior belief is outdated), **Premise Resistance failure** (even after detecting staleness, the model still answers as though a false presupposition built on stale state were true — detection and refusal are separate skills), and **Implicit Policy Adaptation failure** (the model fails to propagate an updated state into downstream decisions). The paper's core conclusion: *"the dominant failure mode is not forgetting — it is continuing to act on information that was once correct but is no longer."* This is the direct motivation for Graphiti's bi-temporal invalidation (§2.2) — resolving staleness at write-time rather than relying on read-time LLM inference.
- **Unintentional cross-user contamination (UCC, arXiv:2604.01350)**: a non-adversarial failure where benign, locally-valid artifacts from one user's session persist in shared agent state and are misapplied to a different user. Under raw shared state, benign interactions alone produced **57–71% contamination rates** in controlled evaluation. Write-time text sanitization mitigates contamination for conversational shared state but **leaves substantial residual risk for executable artifacts** (code, structured plans), where contamination manifests as silent wrong answers rather than visibly wrong text.

**Idempotency keys**: every write derives a stable key from the content hash of the extracted fact (Mem0's MD5-based dedup, §2.2) rather than from attempt metadata — this makes a retried write resolve as a no-op against the existing record instead of creating a duplicate, and makes a Temporal-style replay-on-crash pattern cost-neutral rather than cost-doubling.

### 4.5 Fallback chains specific to memory

Vector-store circuit open → demote to BM25/keyword-only recall (a "hot" vs. "cold" tier) → if that also fails, proceed with **no memory context at all**, explicitly flagged to the user/logs as ungrounded rather than silently omitted → if the extraction LLM is down on the write path, buffer the raw turn in the episodic store for later re-extraction rather than dropping it. Each degradation step trades personalization/context quality for availability, but a memory subsystem outage should never fail the user-facing conversation turn outright.

### 4.6 Zero-Trust MCP and multi-tenant memory isolation

The dominant 2026 guidance: **the tenant boundary must be a hard, storage-layer construct — never an application-level filter that can be forgotten.**

- **Hard boundary = physically separate index/namespace/bank per tenant**, not a `tenant_id` column filtered at query time. Mem0's production architecture creates a **dedicated Turbopuffer namespace per customer** (150K+ customers, each isolated) — explicitly framed by the vendor as "good for security" (physical isolation) in addition to performance (§3.3). The "Hindsight" memory pattern makes `bank_id` "always derived from the authenticated caller, never from anything a request body can influence."
- **Soft partition = tags**, used only to *organize* memory within a tenant's own space — never as the security boundary, since tag filters "can be forgotten."
- **Row-Level Security (RLS)** enforced at the database engine level, not the application-query level, so the constraint holds even if application code has a bug.
- **CI-enforced isolation tests**: store as Tenant A, authenticate as Tenant B, assert nothing is retrievable — explicitly a *different* test than "Tenant A can retrieve their own data," and the one most teams skip.
- **Zero-Trust MCP**: every tool call a memory-editing agent makes (archival write, cross-tool memory sync) routes through a deny-by-default MCP gateway with short-lived, task-scoped credentials — no shared service accounts, and no tool granted broader access than the specific memory operation requires.

### 4.7 RBAC, PII filtering, and GDPR right-to-erasure

**RBAC** for memory governance is two-dimensional — **who can invoke an agent** × **what the agent is scoped to read/write on which data class** — not single-dimension user-to-agent RBAC. A concrete open-source reference (`memory-hub`) enforces per-tool RBAC via OAuth 2.1 JWTs, gates an "enterprise memory" scope behind mandatory human-in-the-loop write approval, and implements **append-only audit logging via Postgres RLS with a dedicated INSERT-only `audit_writer` role** — even the primary application role cannot UPDATE/DELETE audit rows.

**PII and GDPR erasure — the highest-friction, least-solved area in production memory systems as of 2026:**

- **Embeddings are personal data under GDPR Art. 17**, not a sanitized abstraction — research shows ~40% of sensitive data in sentence-length embeddings is reconstructible via straightforward inversion, rising to ~70% for shorter texts. This directly implicates the vector-store memory layer, not just the raw-text store.
- **Soft-delete is the default and is legally insufficient.** Milvus marks entities deleted and purges only on compaction; pgvector's HNSW index retains "dead tuples" until `VACUUM`; Qdrant applies the delete immediately with `wait=true` but frees storage only on a later optimize pass. The EDPB has stated erasure must be **verifiable and irreversible** — suppressing records from query results alone does not satisfy Article 17.
- **"Ghost Vectors" (arXiv:2606.18497)** formally demonstrates soft-deleted embeddings remain reconstructible from HNSW graph structure on disk even after the metadata-level delete.
- **Cascading deletion** is required across every derived copy — primary vector index, replicas, backups, eval-trace snapshots, semantic caches, and fine-tuning datasets — a single `DELETE WHERE id=X` addresses only one of these.
- **Recommended mitigations**: (1) documented, scheduled physical purge/compaction as part of Records of Processing Activities; (2) **crypto-shredding** — encrypt each subject's vectors under a per-subject key at ingestion, destroy the key on erasure request so ciphertext becomes cryptographically meaningless without a full index rewrite (§3.8's Trade-off 2).
- **Retention tiering** (practitioner consensus): user preferences 6–12 months, workflow/case state until closure+30 days, operational action logs 12–24 months, policy memory retained until explicitly superseded. Treat an agent's unbounded persistent memory as a **"shadow database"** — an ungoverned covert-storage channel for credentials/PII that bypasses traditional DLP unless explicitly redacted at ingestion and schema/TTL/access-controlled like any other regulated data store.
- **Declarative policy-as-code** (OPA/Rego, or bespoke `data_policy` blocks specifying `pii_detectors`, `redaction_mode`, `retention_days`) is emerging as the preferred enforcement mechanism, decoupling governance logic from application code and enabling per-tenant policy variation on a shared runtime.

### 4.8 Auditability: immutable logs and chain-of-custody

Regulatory audit-log requirements converge on capturing: identity binding (SSO-linked, not an anonymous service account), verbatim intent/prompt capture with timestamp, the full tool-call sequence with parameters/return values, decision rationale, affected-data lineage (which records/subjects were touched by a write or consolidation), output sensitivity classification, and cryptographic tamper-evidence (hash-chaining or append-only architecture) — mapped explicitly to **GDPR Art. 30**, **EU AI Act Art. 12–14**, and **SOC 2 CC6.1**. SOC 2 Type II specifically expects a **365-day immutable retention** baseline for these logs.

### 4.9 Sandbox isolation

`[inferred, thin evidence]` Cross-tenant infrastructure isolation failures documented for LLM platforms generalize directly to memory subsystems: the April 2024 Wiz disclosure of cross-tenant breaches on a major AI-as-a-service platform ran through misconfigured Kubernetes environments and pickle deserialization — illustrating that individually-solid isolation layers can still compose insecurely. Recommended mitigation for agents executing dynamic code against memory/tools: **hardware-level isolation (microVMs)** rather than shared containers, to prevent cross-tenant filesystem or memory access.

> ⚠️ Gap: no dedicated large-scale public post-mortem exists specifically for a "consolidation pipeline corrupted memory during summarization" incident as a named category — the best available evidence is the HaluMem update-stage data (§4.4: <50% correct-update rate) and the STALE implicit-conflict data, both of which effectively measure consolidation/update-time correctness without being labeled as a distinct "consolidation error" incident class.

---

## 5. Production Enterprise Code

The implementation below is a hardened Python memory read/write/consolidation pipeline wiring together every pattern from §3–§4: retries with exponential backoff + full jitter for transient faults, a per-dependency circuit breaker (CLOSED→OPEN→HALF_OPEN) for the extraction LLM, vector store, and consolidation LLM, a fallback chain (vector store failure → keyword-only recall; consolidation failure → defer non-blockingly to the next cycle), content-hash idempotency (Mem0-style dedup), a pre-write PII redaction gate, structured JSON logging with a correlation ID per request, and decay/recency/importance re-ranking on the read path. Standard library only.

```python
"""
production_memory_pipeline.py

A production-hardened memory read/write/consolidation pipeline
demonstrating every pattern from Module 07 (Memory) Sec 3-4:

  - content-hash idempotency / dedup on write (Mem0-style, Sec 2.2)
  - pre-write PII detect->redact->audit gate (Sec 4.7)
  - per-dependency circuit breaker: CLOSED -> OPEN -> HALF_OPEN (Sec 4.3)
  - retry with exponential backoff + full jitter for transient errors
    (Sec 4.4's transient/permanent/poison-pill taxonomy)
  - a fallback chain: vector store failure -> keyword-only recall;
    consolidation failure -> defer non-blockingly to next cycle (Sec 4.5)
  - recency . importance . relevance decay re-ranking on read (Sec 2.3)
  - structured JSON logging with a per-request correlation ID (Sec 4.8)

Install:  no dependencies (stdlib only; swap the Mock* clients for a
          real vector DB / embedding API / extraction LLM SDK in prod)
Run:      python production_memory_pipeline.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import re
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


# --------------------------------------------------------------------------
# 1. Structured logging with per-request correlation IDs (Sec 4.8)
# --------------------------------------------------------------------------

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get()
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("memory_pipeline")
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
    """Binds one correlation ID to every log line for a single memory
    operation (write, read, or consolidation run), so a full trajectory
    (extraction, redaction, dedup, store, or retrieval+rerank) can be
    reconstructed for audit (Sec 4.8) independent of which stage
    emitted the log."""

    def __init__(self, request_id: Optional[str] = None):
        self.request_id = request_id or str(uuid.uuid4())
        self._token = None

    def __enter__(self) -> str:
        self._token = _correlation_id.set(self.request_id)
        return self.request_id

    def __exit__(self, *exc_info) -> None:
        _correlation_id.reset(self._token)


# --------------------------------------------------------------------------
# 2. Failure taxonomy (Sec 4.4): transient vs. permanent
# --------------------------------------------------------------------------

class MemoryError_(Exception):
    """`transient=False` marks permanent errors that must never be
    retried (auth failure, embedding-dimension mismatch, a record
    referencing a deleted subject)."""

    def __init__(self, message: str, transient: bool = True):
        super().__init__(message)
        self.transient = transient


# --------------------------------------------------------------------------
# 3. Retry with exponential backoff + full jitter (Sec 4.4)
# --------------------------------------------------------------------------

def backoff_with_full_jitter(attempt: int, base_s: float = 0.05, cap_s: float = 2.0) -> float:
    upper_bound = min(cap_s, base_s * (2 ** attempt))
    return random.uniform(0, upper_bound)


def call_with_retry(fn: Callable[[], Any], max_attempts: int = 3,
                     base_s: float = 0.05, cap_s: float = 2.0) -> Any:
    last_error: Optional[MemoryError_] = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except MemoryError_ as exc:
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
# 4. Circuit breaker: CLOSED -> OPEN -> HALF_OPEN, per dependency (Sec 4.3)
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
    cooldown_s: float = 10.0
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
                                              failure_threshold_ratio=0.6, cooldown_s=8)
    return _BREAKERS[dep_name]


# --------------------------------------------------------------------------
# 5. PII detect -> redact -> audit gate (Sec 4.7), pre-write
# --------------------------------------------------------------------------

_PII_PATTERNS = {
    "EMAIL": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "PHONE": re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),
}


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Typed-placeholder redaction (never a uniform mask, which would
    create false-collision retrieval noise across many redacted
    records). Returns (redacted_text, detected_types) for the audit
    log -- detection is logged even when nothing is stored raw."""
    detected: list[str] = []
    redacted = text
    for pii_type, pattern in _PII_PATTERNS.items():
        if pattern.search(redacted):
            detected.append(pii_type)
            redacted = pattern.sub(f"[{pii_type}]", redacted)
    return redacted, detected


# --------------------------------------------------------------------------
# 6. Content-hash idempotency / dedup (Mem0-style, Sec 2.2)
# --------------------------------------------------------------------------

_SEEN_FACT_HASHES: set[str] = set()


def content_hash(user_id: str, fact_text: str) -> str:
    payload = f"{user_id}:{fact_text.strip().lower()}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# 7. Mock dependencies (swap for real SDKs in production)
# --------------------------------------------------------------------------

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def mock_extract_facts(turn_text: str) -> list[str]:
    """Simulates a single LLM extraction call (Sec 2.2, Sec 3.1). Splits
    on sentence-ending punctuation followed by whitespace (not on every
    '.') so an embedded email/domain like 'jane@example.com' isn't
    fractured mid-token -- a real extraction model would return
    structured facts rather than raw sentences, but this preserves the
    pipeline's shape without a real LLM dependency."""
    if random.random() < 0.1:
        raise MemoryError_("extraction LLM timeout", transient=True)
    return [s.strip() for s in _SENTENCE_BOUNDARY.split(turn_text.strip()) if s.strip()]


def mock_embed(text: str) -> list[float]:
    if random.random() < 0.05:
        raise MemoryError_("embedding API timeout", transient=True)
    return [float((hash((text, i)) % 1000)) / 1000 for i in range(8)]


def mock_vector_store_write(record: dict) -> None:
    if random.random() < 0.15:
        raise MemoryError_("vector store write timeout", transient=True)
    _SEMANTIC_STORE[record["id"]] = record


def mock_vector_store_search(query_embedding: list[float], top_k: int = 20) -> list[dict]:
    if random.random() < 0.15:
        raise MemoryError_("vector store search timeout", transient=True)
    def cos_sim(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1e-9
        nb = math.sqrt(sum(x * x for x in b)) or 1e-9
        return dot / (na * nb)
    scored = [
        {**rec, "relevance": cos_sim(query_embedding, rec["embedding"])}
        for rec in _SEMANTIC_STORE.values()
    ]
    scored.sort(key=lambda r: r["relevance"], reverse=True)
    return scored[:top_k]


def mock_keyword_search(query: str, top_k: int = 20) -> list[dict]:
    """Fallback path when the vector store breaker is open (Sec 4.5)."""
    terms = set(query.lower().split())
    scored = []
    for rec in _SEMANTIC_STORE.values():
        overlap = len(terms & set(rec["text"].lower().split()))
        if overlap:
            scored.append({**rec, "relevance": overlap / max(len(terms), 1)})
    scored.sort(key=lambda r: r["relevance"], reverse=True)
    return scored[:top_k]


_SEMANTIC_STORE: dict[str, dict] = {}
_EPISODIC_STORE: list[dict] = []


# --------------------------------------------------------------------------
# 8. Recency . Importance . Relevance decay re-ranking (Sec 2.3, Sec 2.4)
# --------------------------------------------------------------------------

def decay_rescore(records: list[dict], alpha: float = 0.3, beta: float = 0.3,
                   gamma: float = 0.4, salience_floor: float = 8.0) -> list[dict]:
    now = time.time()
    rescored = []
    for rec in records:
        hours_since_access = (now - rec.get("last_accessed_ts", now)) / 3600.0
        recency = 0.99 ** hours_since_access
        importance = rec.get("importance", 5.0) / 10.0
        relevance = rec.get("relevance", 0.0)
        # Sec 2.4 invariant: a salience floor exempts high-importance
        # facts from recency decay, so a rarely-accessed but critical
        # fact (e.g. a stored allergy) is never simply out-competed by
        # recently-touched noise.
        if rec.get("importance", 5.0) >= salience_floor:
            recency = max(recency, 0.9)
        score = alpha * recency + beta * importance + gamma * relevance
        rescored.append({**rec, "decay_score": score})
    rescored.sort(key=lambda r: r["decay_score"], reverse=True)
    return rescored


# --------------------------------------------------------------------------
# 9. Write path: extract -> redact -> dedupe -> embed -> store (Sec 1, Sec 2.2)
# --------------------------------------------------------------------------

def write_memory(user_id: str, turn_text: str, importance: float = 5.0) -> dict:
    """Never raises on a degraded store -- always returns a status
    the caller can log/act on (Sec 4.5 graceful degradation)."""
    facts = call_with_retry(lambda: mock_extract_facts(turn_text))
    written, deduped, redacted_count = [], [], 0

    for fact in facts:
        redacted_text, pii_types = redact_pii(fact)
        if pii_types:
            redacted_count += 1
            log.info(json.dumps({"event": "pii_redacted", "types": pii_types}))

        fact_hash = content_hash(user_id, redacted_text)
        if fact_hash in _SEEN_FACT_HASHES:
            deduped.append(fact_hash[:12])
            continue
        _SEEN_FACT_HASHES.add(fact_hash)

        _EPISODIC_STORE.append({
            "id": fact_hash, "user_id": user_id, "text": redacted_text,
            "ts": time.time(), "pii_redacted": bool(pii_types),
        })

        breaker = get_breaker("vector_store")
        if breaker.allow_request():
            try:
                embedding = call_with_retry(lambda: mock_embed(redacted_text))
                record = {
                    "id": fact_hash, "user_id": user_id, "text": redacted_text,
                    "embedding": embedding, "importance": importance,
                    "last_accessed_ts": time.time(),
                }
                call_with_retry(lambda: mock_vector_store_write(record))
                breaker.record_success()
                written.append(fact_hash[:12])
            except MemoryError_ as exc:
                breaker.record_failure()
                log.info(json.dumps({"event": "semantic_write_failed_episodic_retained",
                                      "error": str(exc)}))
        else:
            log.info(json.dumps({"event": "semantic_write_skipped_breaker_open",
                                  "episodic_fallback": True}))

    return {"written": written, "deduped": deduped, "pii_redactions": redacted_count,
            "episodic_count": len(facts)}


# --------------------------------------------------------------------------
# 10. Read path: hybrid retrieval + decay rerank, with fallback (Sec 1, Sec 4.5)
# --------------------------------------------------------------------------

def read_memory(query: str, top_k: int = 5) -> dict:
    breaker = get_breaker("vector_store")
    mode = "no_memory_available"
    candidates: list[dict] = []

    if breaker.allow_request():
        try:
            query_embedding = call_with_retry(lambda: mock_embed(query))
            candidates = call_with_retry(lambda: mock_vector_store_search(query_embedding, top_k=50))
            breaker.record_success()
            mode = "vector_retrieval"
        except MemoryError_ as exc:
            breaker.record_failure()
            log.info(json.dumps({"event": "vector_search_failed_falling_back_to_keyword",
                                  "error": str(exc)}))
    else:
        log.info(json.dumps({"event": "vector_search_skipped_breaker_open_falling_back"}))

    if not candidates:
        candidates = mock_keyword_search(query, top_k=50)
        mode = "keyword_fallback" if candidates else "no_memory_available"

    reranked = decay_rescore(candidates)[:top_k]
    return {"results": reranked, "mode": mode}


# --------------------------------------------------------------------------
# 11. Consolidation path: episodic -> semantic synthesis, non-blocking (Sec 2.2)
# --------------------------------------------------------------------------

def consolidate_memory(user_id: str, batch_size: int = 20) -> dict:
    """Runs off the critical path (sleep-time style, Sec 2.2). A
    failure here defers to the next scheduled cycle rather than
    blocking or retrying aggressively against the user-facing path
    (Sec 4.5 graceful degradation applied to the consolidation tier)."""
    breaker = get_breaker("consolidation_llm")
    user_traces = [r for r in _EPISODIC_STORE if r["user_id"] == user_id][-batch_size:]
    if not user_traces:
        return {"status": "no_traces", "consolidated": 0}

    if not breaker.allow_request():
        log.info(json.dumps({"event": "consolidation_skipped_breaker_open",
                              "deferred_to_next_cycle": True}))
        return {"status": "deferred", "consolidated": 0}

    try:
        def _consolidate():
            if random.random() < 0.2:
                raise MemoryError_("consolidation LLM overloaded", transient=True)
            # stand-in for a real sleep-time-agent summarization call
            return f"Consolidated {len(user_traces)} traces for {user_id}"
        summary = call_with_retry(_consolidate, max_attempts=2)
        breaker.record_success()
        log.info(json.dumps({"event": "consolidation_complete", "user_id": user_id,
                              "traces_consolidated": len(user_traces)}))
        return {"status": "ok", "consolidated": len(user_traces), "summary": summary}
    except MemoryError_ as exc:
        breaker.record_failure()
        log.info(json.dumps({"event": "consolidation_failed_deferred", "error": str(exc)}))
        return {"status": "deferred", "consolidated": 0}


# --------------------------------------------------------------------------
# 12. Example run
# --------------------------------------------------------------------------

if __name__ == "__main__":
    with request_scope() as request_id:
        log.info(json.dumps({"event": "write_start"}))
        write_result = write_memory(
            user_id="user-42",
            turn_text="I'm allergic to penicillin. My email is jane@example.com. I prefer async standups.",
            importance=9.0,
        )
        log.info(json.dumps({"event": "write_complete", **write_result}))

        log.info(json.dumps({"event": "read_start"}))
        read_result = read_memory("does the user have any allergies?", top_k=3)
        log.info(json.dumps({"event": "read_complete", "mode": read_result["mode"],
                              "result_count": len(read_result["results"])}))

        log.info(json.dumps({"event": "consolidation_start"}))
        consolidation_result = consolidate_memory("user-42")
        log.info(json.dumps({"event": "consolidation_complete_top_level", **consolidation_result}))

        print(json.dumps({"request_id": request_id, "write": write_result,
                           "read": read_result, "consolidation": consolidation_result}, indent=2))
```

**What each pattern buys, mapped back to §2–§4.** The content-hash `content_hash()` dedup mirrors Mem0's real MD5-based ADD-only pipeline (§2.2) — a retried write resolves as a no-op rather than a duplicate. The PII gate runs unconditionally before any store write, using typed placeholders rather than a uniform mask, exactly matching the detect→redact→audit pattern required for GDPR-adjacent workloads (§4.7); the redaction event is logged even though the raw PII never reaches persistence, satisfying the audit trail without storing the sensitive value itself. The per-dependency `CircuitBreaker` isolates the extraction LLM, vector store, and consolidation LLM from each other — a vector-store outage during `write_memory` still leaves the fact durably in the episodic store (§1's "episodic store retains lossless data even when semantic writes fail" design), and a vector-store outage during `read_memory` demotes to keyword search rather than returning nothing. `decay_rescore()` implements the recency·importance·relevance formula with an explicit salience floor (§2.3, §2.4) so a high-importance fact like a stored allergy is never simply out-competed by recently-touched noise. `consolidate_memory()` treats its own failures as **deferrals**, not retriable emergencies — consistent with the sleep-time-compute design principle that consolidation staleness degrades memory *freshness*, never conversation *availability* (§2.2, §3.8).

---

## 6. Architectural System Design Scenarios

### Scenario A — Multi-tenant long-term memory platform for a hyperscale customer-support SaaS

**Problem statement.** A SaaS company provides an AI customer-support agent to 150,000+ enterprise customers, each with many end-users, and needs per-user long-term memory (preferences, prior issues, resolution history) that persists across sessions and scales toward hundreds of millions of memory records and terabytes of embeddings, while guaranteeing hard per-tenant data isolation, sub-100ms P90 retrieval at peak load, and GDPR right-to-erasure on request — the exact shape of the real Mem0/Turbopuffer production case study (§3.3).

**Proposed architecture.**

```
User turn → Extraction LLM (fact pull) → PII redact → MD5 content-
            hash dedup (Sec 2.2/Sec 5) → embed
                                                    │
                                                    ▼
                Per-tenant-namespace vector store (Turbopuffer-
                style hard boundary, Sec 4.6) + per-tenant Postgres
                episodic log (WAL-replicated, Sec 4.1) — never a
                single shared table filtered by tenant_id column
                                                    │
                                                    ▼
                Read: hybrid retrieval (dense + BM25 + entity match)
                scoped to the caller's own namespace only → decay/
                importance rerank (Sec 2.3-2.4) → inject top-k into
                working memory
                                                    │
                                                    ▼
                Async sleep-time consolidation per tenant (Sec 2.2),
                batched off critical path, producing durable per-user
                memory-block summaries → immutable audit log (query,
                namespace, chunk IDs, redaction events, Sec 4.8)
```

**Trade-off matrix:**

| Dimension | Proposed: namespace-per-tenant + additive extraction (Mem0/Turbopuffer pattern) | Single shared pgvector table, `tenant_id` column filter | MemGPT/Letta-style in-line consolidation, single shared archival store |
|---|---|---|---|
| Cost / 1k runs | Write ≈$0.086, retrieval ≈$0.085 (§3.1) at steady state; per-namespace sharding adds modest per-tenant infra overhead | Lowest infra overhead initially, but hides a latent scaling cliff | Higher — in-line consolidation LLM calls block every turn instead of being batched/amortized (§3.5) |
| Latency at scale | **70ms P90** hybrid retrieval, sustained 100M→400M+ memories (measured, §3.3) | **Degrades catastrophically**: measured tail spikes to 14s once the query planner abandons the HNSW index at scale (§3.3) | Not benchmarked at this scale in the research set; in-line consolidation adds latency to every turn regardless of corpus size |
| Security / multi-tenancy | Physical namespace-per-tenant is a true hard boundary (§4.6) — a compromised key or query bug cannot cross tenants | `tenant_id` filtering is an **application-level filter that can be forgotten** — explicitly the anti-pattern §4.6 warns against | Same shared-store risk as the middle column unless explicitly redesigned per-tenant |
| Ops complexity | Moderate — per-tenant namespace provisioning and lifecycle management, but each tenant's data is independently scalable and independently erasable | Lowest upfront, but requires a disruptive migration once the scaling cliff is hit (exactly what Mem0 experienced) | Moderate-high — in-line consolidation requires careful token-budget management (§2.1) per agent instance |
| GDPR erasure | Per-tenant namespace deletion is a clean, verifiable erasure boundary; combine with crypto-shredding for per-subject erasure within a namespace (§4.7) | Erasure requires a scan-and-filter across a shared table — harder to verify as complete, higher risk of missed replicas/backups | Same shared-store erasure complexity as the middle column |

**Decision rationale.** The namespace-per-tenant pattern is selected specifically because it is not a hypothetical design — it is the documented outcome of Mem0's own production migration away from the shared-table alternative after that alternative's HNSW index was abandoned by the query planner at scale, producing a 14-second tail latency that a per-tenant architecture structurally cannot exhibit (each tenant's index is small and independent regardless of how large the platform grows in aggregate). The additive Mem0-style extraction pipeline (rather than MemGPT/Letta in-line consolidation) is chosen because this workload's dominant cost driver at 150K+ tenants is the sheer volume of routine per-turn writes, not deep multi-session synthesis per user — deferring conflict resolution to retrieval-time ranking (§2.2) keeps the write path cheap and async, and per-tenant sleep-time consolidation can still be layered in later for tenants that specifically need richer synthesized summaries, without redesigning the isolation boundary.

### Scenario B — GDPR-compliant relational memory for a regulated healthcare/fintech conversational assistant

**Problem statement.** A regulated enterprise (digital health or fintech) deploys a conversational assistant that must remember cross-session, relationally-connected facts about a customer (e.g., "medication X was changed after visit Y," "account restriction Z was lifted following review W"), where facts can be **superseded without explicit negation** (a classic implicit-conflict pattern) and the system must support GDPR Article 17 right-to-erasure with a verifiable, auditable erasure trail, plus a full chain-of-custody log defensible to a regulator — a composite of the STALE implicit-conflict failure mode (§4.4), the GDPR erasure requirements (§4.7), and the Sunflower-style production deployment pattern (§3.4), but with materially stricter compliance obligations than a consumer digital-health app.

**Proposed architecture.**

```
Clinical/financial event → structured entity+relationship extraction
                            → bi-temporal graph write (Graphiti-style,
                            Sec 2.2): contradicting facts set invalid_at
                            on the prior edge, never delete — full audit
                            trail preserved by construction
                                                    │
                                                    ▼
                Per-subject crypto-shredding key at ingestion (Sec 3.8
                Trade-off 2, Sec 4.7): every vector/graph write for
                this subject encrypted under a subject-specific key
                                                    │
                                                    ▼
                Query: multi-hop graph traversal for relational facts
                (deterministic, auditable paths) + vector recall for
                unstructured conversational context (Sec 2.6 dual-
                substrate pattern)
                                                    │
                                                    ▼
                Append-only, INSERT-only audit_writer role (Postgres
                RLS, Sec 4.7) logs every read/write/consolidation with
                identity binding + affected-subject lineage
                                                    │
                                                    ▼
                Erasure request → destroy the subject's crypto-shred
                key (ciphertext becomes meaningless immediately, no
                index rewrite required) → scheduled physical purge on
                next compaction cycle for full cleanup (Sec 3.8)
```

**Trade-off matrix:**

| Dimension | Proposed: bi-temporal graph + per-subject crypto-shredding | Pure vector-store memory (Mem0-style) with soft-delete | MemGPT/Letta in-line editable core-memory blocks with hard delete |
|---|---|---|---|
| Cost / 1k runs | Higher — graph-enhanced retrieval measured at P50 1.091s/P95 2.590s total vs. base vector's P50 0.148s/P95 0.200s (§3.2); crypto-shredding adds per-subject key management overhead | Lowest — no graph traversal, no per-subject key overhead | Moderate — hard delete is operationally simple but doesn't solve the underlying reconstructability problem |
| Answer reliability on implicit conflicts | Strongest — invalidation is enforced at write time (`invalid_at`), so a stale fact structurally cannot be surfaced as current without the read path explicitly following the invalidation chain (§2.2) | Weakest — relies on read-time ranking/LLM inference to detect staleness, the exact failure STALE measures at only 55.2% best-model accuracy (§4.4) | Better than pure vector (explicit `memory_replace` overwrites), but overwriting destroys the "what did the system believe and when" audit trail a regulator will ask for |
| GDPR erasure verifiability | Strong — crypto-shred key destruction is near-instant and cryptographically verifiable, satisfying the EDPB's "verifiable and irreversible" bar without a full index rewrite (§4.7, §3.8) | Weak — soft-delete (mark-and-filter) is explicitly called out as legally insufficient; Ghost Vectors shows reconstructability persists even after metadata-level delete (§4.7) | Hard delete is closer to compliant, but doesn't address backup/replica/eval-snapshot copies (cascading deletion, §4.7) unless separately engineered |
| Ops complexity | Highest — three moving parts (graph store, vector store, per-subject key management) plus a scheduled compaction cadence for full physical purge | Lowest | Moderate — single store, but the audit-trail gap above typically requires bolting on a separate append-only log after the fact |
| Auditability / regulator defensibility | Strong — bi-temporal history is self-documenting (nothing is destroyed on update) and pairs naturally with the append-only `audit_writer` log (§4.7, §4.8) | Weak on its own — additive-only writes accumulate but don't resolve conflicts, so "what does the system currently believe" requires re-deriving from ranking logic at read time, which is hard to audit after the fact | Moderate — explicit overwrites are simple to log, but the overwritten value's history is gone unless a separate versioned log is maintained |

**Decision rationale.** The bi-temporal graph is chosen over both alternatives specifically because this workload's core requirement — facts that get superseded without explicit negation, in a domain where acting on a stale fact has real (clinical/financial) consequences — is exactly the failure class STALE quantifies as unsolved by read-time LLM inference alone (best frontier model: 55.2% accuracy). Resolving staleness at write time via explicit edge invalidation removes this failure mode from the critical reasoning path entirely, at the accepted cost of higher per-query latency (graph traversal, §3.2) and higher ops complexity — an acceptable trade in a regulated domain where a wrong answer is far more costly than a slow one. Crypto-shredding is selected over both soft-delete and simple hard-delete because it is the only one of the three that gives a **near-instant, cryptographically verifiable** erasure event decoupled from the index's own maintenance schedule — critical because "erasure pending next compaction" is not a defensible answer to a regulator asking whether Article 17 has been honored. The residual cost (per-subject key management, higher graph-query latency) is justified by the fact that this scenario's failure modes — a stale clinical fact acted upon, or an unverifiable erasure claim — carry regulatory and safety consequences that dominate the pure infrastructure-cost calculus used in Scenario A.

---

> ⚠️ Data gaps carried over from the primary source: no public, vendor-neutral benchmark quantifies the LLM-call cost of MemGPT/Letta-style **in-line** (non-sleep-time) consolidation (§3.5); no dedicated large-scale public post-mortem exists for a named "consolidation pipeline corruption" incident category, distinct from the HaluMem/STALE data used as the best available proxy (§4.9); and every availability/RPO/RTO figure in §3.8 beyond the single measured Mem0/Respan 99.99% anchor is an architect-inferred design target, since no vendor publishes an SLA scoped to a composed working+episodic+semantic+consolidation memory system as a whole.
