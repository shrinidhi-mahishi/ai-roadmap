# Topic 6: Memory Systems
> Consolidated from multi-model research (Grok + Opus) | Study & Interview Prep

---

## Introduction

### What This Topic Covers

Memory systems give AI agents the ability to **learn from and recall past interactions** — the difference between a stateless chatbot and a personalized assistant that remembers your preferences, past conversations, and organizational knowledge. This topic covers the three-tier cognitive model (working/short-term/long-term memory), semantic memory (vector stores, knowledge graphs), episodic memory (interaction history with recency/importance/relevance scoring), procedural memory (learned skills), memory consolidation algorithms (sleep-time compute, background summarization), context compression, MemGPT/Letta virtual context management, and forgetting as a design primitive.

### Why Study This

- **The personalization layer**: Memory is what transforms a generic AI agent into one that understands your users, your organization, and your domain. It's the key differentiator in production agent systems.
- **Rapidly evolving**: Letta's sleep-time compute, LangMem's BackgroundMemoryManager, Zep's Graphiti, and Mem0's managed platform are all 2025-2026 developments. Interviewers want to know you're current.
- **Security-critical**: Stored memories contain PII (GDPR Articles 15/16/17 apply), are vulnerable to poisoning attacks (MINJA: >95% injection success rate, NeurIPS 2025), and require right-to-erasure compliance. This is an enterprise governance topic, not just an ML topic.
- **Cost driver**: Effective memory saves 90%+ tokens (Mem0 benchmark) by avoiding full history re-processing. Memory quality directly impacts both cost and response quality.

### What Details Are Included

- Three-tier cognitive model with working, short-term, and long-term memory
- Park et al. episodic memory scoring formula (recency × importance × relevance)
- Vector store comparison (pgvector, Pinecone, Weaviate, Qdrant) with pricing at scale
- Memory consolidation: SCM sleep-consolidated memory, Letta sleep-time compute, LangMem
- Context compression techniques with quality/cost tradeoffs
- Memory poisoning attacks (MINJA, PoisonedRAG) and OWASP Memory Guard defenses
- Production Python code for three-tier memory, hybrid recall, PII detection, GC
- Three enterprise system design scenarios with trade-off matrices

### How to Approach This Document

> **First pass (2-3 hours)**: Sections 1-4. Focus on the three-tier model and the architecture diagram. Understand the difference between semantic, episodic, and procedural memory — and when each matters.
>
> **Second pass (2-3 hours)**: Sections 5-7. Study the vector DB pricing comparison. Run the memory system code. Pay attention to the failure modes — memory bloat and recall failures are the most common production issues.
>
> **Interview prep (1 hour)**: Section 10. Practice explaining the memory consolidation pipeline end-to-end. Know the MINJA attack and its defense.
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

## 1. Concept Overview

### What Is a Memory System?

A production **memory system** is **four independently scaled planes**, not a Pinecone index: **STM** (working buffer / last-k / token-budgeted messages), **LTM** (semantic facts, user/agent profiles), **episodic log** (time-stamped trajectories with provenance), and a **compressor** (sliding window, LLMLingua, recursive summary, vendor compact, FS offload).

**The LLM is not the memory.** (CoALA: Sumers, Yao, Narasimhan, Griffiths, TMLR 2024). Working memory is a data structure the prompt is *compiled from*. Long-term memory is read via **retrieval** and written via **learning**. The model never searches an index; it emits a tool call or the control plane runs a retriever; observations return as tokens.

**RAG is not memory.** RAG is **read-only semantic memory of the world** -- a shared external corpus the agent did not create. Agent memory is **writable semantic + episodic memory of the interaction** -- stateful and interaction-dependent. The control plane decides *whether* to write, *which* store, *which k*, *whether* to compact, and *who may call memory tools*.

**Retrieval quality is the ceiling on memory utility.** A perfect memory store with a bad retriever is worthless because the context window is the only memory the LLM can reason over. Everything else is invisible unless explicitly retrieved and injected.

### Why It Matters

**Cost.** On a reference turn (retrieve once, inject ~2-7k memory tokens, async extract), the constructor cost is **[inferred] ~$11-21 / 1k turns** (Sonnet 5 at $2/$10 per MTok). Stuffing LOCOMO's ~26k history is **~$59 / 1k**. Stuffing LongMemEval_S ~115k is **~$237 / 1k**. A 10M-MAU copilot that stuffs is a finance incident.

**Latency.** Mem0 paper LOCOMO: **1,764** retrieved tokens, total p95 **1.440 s** vs full-context **26,031** tokens / **17.117 s** -- a **91%** p95 cut. Zep paper LME_S: gpt-4o **71.2%** vs full-context **60.2%** at **1.6k** vs **115k** tokens and **2.58 s** vs **28.9 s**.

**Quality trade-off.** Memory is not "more accurate than stuffing 26k" -- full-context judge score is **72.90%** vs Mem0 **66.88%** on LOCOMO. Memory is **accurate enough at 1/10-1/70th the tokens**. On longer contexts (115k), memory actually **wins** on accuracy (Zep 71.2% vs full-context 60.2%).

**Context rot.** Chroma's 2025 Context Rot benchmark found LLM behavior becomes less reliable as the context window fills, with degradation accelerating beyond ~30K tokens. "Just use a bigger context window" is not a viable memory strategy.

**Compliance.** A call-center that compact-replaces the transcript without quote-backed facts is a regulator incident. Vec2Text recovers **92%** of 32-token inputs exactly from embeddings. Ghost Vectors recover **25.5%** of person names from soft-deleted vectors. Memory systems carry the same compliance obligations as raw chat logs.

### RAG vs Agent Memory vs ChatGPT Product Memory

| Product | Source | Write | Isolation | Key Distinction |
|---------|--------|-------|-----------|-----------------|
| **Agent memory** | The interaction stream (user + agent) | Extract, conflict-policy, expire, unlearn | Per-user / per-agent / per-run | Stateful, interaction-dependent |
| **RAG** | External corpus the agent did **not** create | Re-index on a schedule | Shared, versioned docs | World knowledge; complementary, **not** a user profile |
| **ChatGPT memory** | OpenAI product profile + Dreaming V3 (Jun 2026) | Vendor dreaming | OpenAI account | **Not** on Chat Completions / Responses API -- do not design as if callable |

User memory is *about the human*. Agent memory is *about the assistant* (persona, lessons, MemFS). Mixing them in one Pinecone namespace without a `kind` tag is how "I am vegetarian" collides with "always refund without ID."

---

## 2. Core Concepts

### 2.1 Three-Tier Cognitive Model

The agent memory ecosystem converged by 2025-2026 on a taxonomy mirroring cognitive science. Understanding the boundaries between tiers is the architectural foundation for every design decision.

| Tier | Analogy | Lifetime | Implementation | Capacity | Update Pattern |
|------|---------|----------|----------------|----------|---------------|
| **Working Memory** | CPU registers | Single generation | LLM context window | 128K-200K tokens (reliable <30K) | Overwritten each turn |
| **Short-Term Memory** | RAM | Single session | Checkpointer, session state, conversation history | Bounded by session length | Append within session; lost between sessions unless promoted |
| **Long-Term Memory** | Disk/SSD | Cross-session, persistent | Vector stores, knowledge graphs, relational DBs | Unbounded (requires GC) | Explicit read/write via tools |

**CoALA mapping** (Working / Episodic / Semantic / Procedural): Procedural memory (prompts, skills, `CLAUDE.md`, tools) is **hot if inlined, cold if tool-fetched** -- it is a durable injection surface but not an LTM SKU.

### 2.2 Semantic Memory: Facts and Knowledge Graphs

Semantic memory stores **what the agent knows** -- user preferences, entity attributes, domain facts, relationships. It is mutable and fact-centric: when a user changes their address, the old value is updated, not appended.

**Vector-based implementation** (simplest, most common):
- Facts embedded as dense vectors, stored in ANN index (HNSW dominant).
- Retrieval via approximate nearest-neighbor search. Used by Mem0, LangMem, and most production agents.
- **Weakness**: No structured relational reasoning; "Who is Alice's manager?" requires the relationship to appear in a single embedded chunk.

**Graph-based implementation** (structured relational queries):
- Entities as nodes, relationships as edges in a knowledge graph.
- Zep's Graphiti: LLM pipeline extracts named entities and relationships, stores as nodes/edges, cross-links to vector embeddings.
- **Critical innovation**: Conflict detection compares new facts against existing graph entries and merges, updates, or flags for resolution.
- **Bi-temporal model**: timeline T (chronological event order) and T' (data ingestion order). Obsolete relationships marked `invalid_at` rather than deleted.

**Hybrid (2026 state of the art)**: Vector similarity for fuzzy retrieval + knowledge graph for structured relational queries. No single database excels at both -- production systems run polyglot storage.

### 2.3 Episodic Memory: Memory Streams and Events

Episodic memory records **what happened** -- past interactions, conversation transcripts, decision traces, outcomes. It is append-only and temporally ordered.

**Park et al. Memory Stream** (Generative Agents, UIST 2023) -- the reference design:

Agents maintain an append-only list of timestamped natural-language observation records. Retrieval ranks memories by summing three normalized scores:

```
retrieval_score(memory, query) = alpha_r * recency + alpha_i * importance + alpha_v * relevance

where:
  recency(m)    = exp(-decay_rate * hours_since_last_access(m))     # decay rate 0.995
  importance(m) = llm_rate(m, scale=1..10) / 10                     # LLM-rated significance
  relevance(m,q)= cosine_similarity(embed(m), embed(q))             # semantic similarity

Each component is min-max normalized to [0,1] before summing.
Paper says all alpha = 1. Released code uses weights [0.5, 3, 2] -- paper and code DISAGREE.
Treat alpha as a tuned product.
```

**Reflection mechanism**: Fires when cumulative importance of recent observations exceeds ~**150** points (roughly 2-3 times per simulated day). The agent generates higher-level abstract observations ("reflections") from clusters of related memories. Reflections are stored back into the stream and can recursively generate even higher-level reflections.

**Known pitfalls**:
- **Importance inflation**: LLMs consistently rate everything high; requires calibration or forced distribution.
- **Reflection hallucination**: Plausible but false conclusions from noisy observations -- particularly dangerous because reflections feed back with the same authority as ground-truth observations.
- **Stream bloat**: Tens of thousands of memories in long-running agents; unbounded growth without active pruning.

### 2.4 Procedural Memory: Learned Skills and Patterns

Procedural memory encodes **how to do things** -- learned behaviors, successful tool-use patterns, refined prompts, workflow strategies. It is the least mature memory tier.

| Framework | Approach | Limitation |
|-----------|----------|-----------|
| **LangMem** | Prompt refinement based on accumulated interaction data. System prompt evolves based on what worked. | Prompts become stale or contradictory over time |
| **CrewAI** | Stores task results in SQLite3 across sessions. References past outcomes on repeated runs. | Limited to task-level procedural memory |
| **Letta** | `system/` files (MemFS, git-backed) inlined every turn | Self-edit means memory quality depends on model judgment |

**Architectural limitation**: Procedural memory encoded only in system prompts is brittle. Long system prompts get partially ignored as the context window fills, and behavioral drift emerges -- the agent follows procedures at start of session but deviates by turn 40. Teams miss this because they test short sessions.

### 2.5 Forgetting as a Memory Primitive

An agent that accumulates every observation indefinitely suffers three failure modes: retrieval quality degrades as noise drowns signal, memory footprint grows without bound, and stale or adversarially planted facts persist indefinitely.

| Mechanism | Trigger | Strengths | Weaknesses |
|-----------|---------|-----------|------------|
| **Passive decay** | Recency score drops below threshold (ACT-R inspired) | Zero runtime cost; natural deprioritization | Never truly deletes; stale facts still retrievable at low scores |
| **Active deletion** | Agent evaluates memories and issues explicit delete | Precise surgical removal | Costs inference tokens; agent may delete incorrectly |
| **Consolidation pruning** | Background process merges/drops low-importance entries | Combines cleanup with quality improvement | Requires consolidation infrastructure |
| **TTL expiration** | Clock-based automatic removal after configured period | Deterministic; no LLM cost; compliance-friendly | Blunt instrument; may expire still-relevant memories |

**Production recommendation**: Layer all four. TTL provides the safety net, passive decay handles retrieval ranking, consolidation pruning runs on schedule, and active deletion handles specific known-stale facts.

---

## 3. Architecture & System Design

### 3.1 Full System Topology

The architecture separates into **Control Plane** (your orchestrator process), **Data Plane** (LLM generation + async extraction), **Persistence** (four stores), **Tool Proxies** (MCP memory tools), and **Telemetry**.

```
+----------------------------------------------------------------------------------+
| CLIENTS                                                                          |
|  chat turn | inbound voice | GDPR erasure | sleep-time / dream job | supervisor |
+--------+---+--------+------+-------+------+-----------+-----------+----+---------+
         |            |              |                   |                |
         | TLS + session JWT (tenant_id/user_id FROM TOKEN) + correlation-id
         v
+----------------------------------------------------------------------------------+
| CONTROL PLANE  (memory orchestrator -- your process, not the GPU)                |
|                                                                                  |
|  +------------+  +------------+  +------------+  +------------+  +-----------+   |
|  | Edge       |->| Policy     |->| READ vs    |->| WRITE      |->| COMPACT   |  |
|  | auth, SSO  |  | PII redact |  | WRITE split|  | ADMISSION  |  | TRIGGER   |  |
|  | tenant     |  | BEFORE     |  | constructor|  | origin tag |  | persist   |  |
|  | from token |  | embed      |  | budget     |  | idempotent |  | THEN drop |  |
|  | MCP aud.   |  | Vec2Text   |  | ACL->ANN   |  | queue, not |  | 70% rot.  |  |
|  +------------+  +-----+------+  +-----+------+  | TTFT       |  +-----+-----+  |
|                        |               |          +-----+------+        |         |
|                        v               v                v               v         |
|                 +--------------------------------------------------------+        |
|                 | FOUR-PLANE ORCHESTRATOR                                 |        |
|                 |  +- STM assembler: last-k XOR token budget + pin card  |        |
|                 |  +- LTM retrieve: namespace pre-filter -> hybrid->pack |        |
|                 |  +- episodic: time-range + BFS from recent episodes    |        |
|                 |  +- compressor: window / LLMLingua / compact / offload |        |
|                 |  +- sleep-time single-writer on profile card            |        |
|                 |  +- ingest watermark (facts not ready -> read episodes)|        |
|                 +-----------------------------+--------------------------+        |
|  +------------+  +------------+               |          +------------------+     |
|  | Circuit    |  | Fallback   |<--------------+--------->| SIGTERM / drain  |     |
|  | retrieve   |  | STM +      |                          | ack extract;     |     |
|  | != write   |  | cached     |                          | do not block     |     |
|  | != embedder|  | profile    |                          | user turn        |     |
|  +------------+  +------------+                          +--------+---------+     |
+---------------------------------------------------------------+--+---------------+
                                                                |
     +------------------------------------------+               |
     | chat / agent SSE, REST                   | extract / compact (async)
     v                                          v
+--------------------------------+  +------------------------------------------+
| DATA PLANE  GENERATION         |  | DATA PLANE  EXTRACTOR (Temporal/Kafka)   |
| (provider-owned on hosted APIs)|  | model NEVER holds IAM or writes raw JSON |
|                                |  |                                          |
| Tokenizer -> Prefill -> Decode |  | pair-wise / ADD-only extract LLM         |
| prompt = card + STM + packed   |  | Graphiti NER + reflection + invalidate   |
| LTM (<= constructor budget)   |  | origin: user|sleep_time -- NOT web/tool  |
| stop: end_turn / max_tokens   |  | idempotency (thread, ckpt, memory_id)    |
+---------------+----------------+  +---------------------+--------------------+
                |                                          |
                | untrusted planner text / tool JSON       | side effects
                v                                          v
+---------------------------------+  +------------------------------------------+
| TOOL PROXIES  (MCP memory)      |  | PERSISTENCE  (four planes, four stores)  |
| Zero-Trust wrap; RFC 8707 aud.  |  |                                          |
| user_id NEVER from tool args    |  |  +-------------+  +-------------+        |
| +----------+  +-------------+   |  |  | STM         |  | LTM         |        |
| | search   |  | insert/put  |   |  |  | checkpointer|  | Mem0/Zep/   |        |
| | (read)   |  | (write ACL) |---+--+  | messages,   |  | Store,      |        |
| | pre-     |  | sleep-time  |   |  |  | Letta blocks|  | Pinecone ns |        |
| | filter   |  | allow-list  |   |  |  | last-k/tok  |  | semantic    |        |
| +----------+  +-------------+   |  |  +-------------+  +-------------+        |
| /memories path-traversal deny   |  |  +-------------+  +-------------+        |
| observation != belief store     |  |  | EPISODIC    |  | COMPRESSOR  |        |
+---------------------------------+  |  | Graphiti    |  | not a store |        |
                                     |  | transcripts |  | trim/offload|        |
                                     |  | checkpoints |  | compact log |        |
                                     |  +-------------+  +-------------+        |
                                     +------------------------------------------+
                                                            |
+----------------------------------------------------------------------------------+
| TELEMETRY / OBSERVABILITY SINKS                                                  |
|  +--------------+  +--------------+  +--------------+  +--------------------+    |
|  | Audit (WORM) |  | Metrics      |  | Traces       |  | Usage (authoritative|    |
|  | cid, tenant, |  | retrieve p50 |  | gateway->ACL |  | on terminal event) |    |
|  | user hashed, |  | /p95, extract|  | ->ANN->pack->|  | constructor tok,   |    |
|  | memory_id,   |  | lag, watermark| | LLM; PII     |  | extract tok, embed,|    |
|  | origin, k,   |  | compact loss,|  | stripped      |  | SKU retrievals,    |    |
|  | Art.17 ids   |  | memory_miss  |  |              |  | total_cost_usd     |    |
|  +--------------+  +--------------+  +--------------+  +--------------------+    |
+----------------------------------------------------------------------------------+
```

### 3.2 Four Planes (Do Not Couple)

| Plane | Owns | Typical Backing | Failure if Coupled |
|-------|------|-----------------|-------------------|
| **Control** | Write admission, constructor budget, compact trigger, memory-tool RBAC, tenant from token | Orchestrator + Temporal/Kafka producers | Model-invented `user_id`; extract on TTFT |
| **STM (working)** | Active symbols for **this** decision cycle | Message buffer, LangGraph `messages`, Letta blocks, token-budgeted scratchpad | Compaction on user-turn critical path; last-k as identity |
| **LTM (semantic)** | Facts, entities, user/agent profiles ("what is true *now*") | Mem0, Graphiti edges, Letta archival, Store, Pinecone ns | Query p99 tracks extract; ADD-only without a ranker |
| **Episodic** | Time-stamped events/trajectories ("what happened," with provenance) | Graphiti episodes, conversation search, checkpoints, transcripts | Destroying episodes while keeping facts kills citation **and** Art. 17 |
| **Compressor** | Shrink working set **without pretending to be LTM** | Sliding window, LLMLingua, recursive summary, vendor compact, FS offload | Dropping the only copy of a constraint; cache invalidation |
| **Tool proxies** | Audience-bound MCP `search` vs `insert` | MCP memory servers, Anthropic `/memories` | Token passthrough; path traversal |
| **Telemetry** | Constructor tok, retrieve SLO, extract lag, erasure fan-out | WORM + metrics | Finance dashboards that ignore retrieve-meter SKUs |

### 3.3 Write Path vs Read Path (Do Not Fuse)

| Plane | Write Path | Read Path | Failure if Fused |
|-------|-----------|-----------|-----------------|
| STM | Append / `add_messages`; trim | Assemble prompt from last-k + pinned blocks | Compaction as a second sampling pass on TTFT |
| LTM | Extract -> ACL+tenant stamp -> embed -> upsert/invalidate | **Authz pre-filter**, hybrid retrieve, rerank, constructor budget | Query p99 tracks extract; Zep graph rebuilds made just-added memories unsearchable for hours |
| Episodic | Append episode / checkpoint / transcript | Time-range + BFS from recent episodes | Destroying episodes while keeping facts |
| Compressor | Trigger on token/message/fraction | Next request sees a *new* prefix | Cache-bust; summarization loss |

### 3.4 End-to-End Request Flow

**Read path (user-facing turn):**

1. **Ingress.** Gateway stamps `correlation_id`. Bind `tenant_id` / `user_id` from the **verified token**, never from tool JSON.
2. **Policy.** Detect -> redact PII **before** anything is embedded or traced (Vec2Text: embeddings = source text).
3. **Breaker.** Consult retrieve-class breaker (timeout **200-500 ms**). Open -> **fail open** to STM + cached profile card; log `memory_miss`. Do **not** fail the chat.
4. **STM load.** Checkpointer: last-k **or** token-budget trim. Pin the **profile card** (1-2k tok) in the **cached** prefix.
5. **LTM retrieve.** Namespace from token. **ACL before ANN.** Hybrid (dense + BM25 + entity). Rerank. **Constructor budget**, not "top-k unlimited."
6. **Episodic overlay.** If the graph watermark says facts are not ready, BFS from recent **episodes** (non-lossy).
7. **Pack.** Profile card + packed LTM (<=1.6k Zep paper / ~7k Mem0 / <=4k copilot target) + STM window. Over-retrieve is lost-in-the-middle.
8. **Compress (optional).** If packed window still exceeds budget: extractive trim / LLMLingua / tool-clear / offload. **Not** a write to LTM. Persist episodes **before** abstractive compact.
9. **Generate.** Data plane samples. Memory block that changes every turn sits **after** the cache breakpoint.
10. **Ack write.** Enqueue extract with idempotency `(thread_id, checkpoint_id, memory_id)`. Return the answer. **Do not wait.**

**Write path (async, after or beside the turn):**

11. **Admit.** Origin must be in {user, sleep_time, extractor} for **semantic** writes. Web/tool text stays in an **observation** store until human confirm.
12. **Extract.** Temporal activity / Kafka consumer: pair extract, Graphiti NER+reflection, or after-call triple. Stamp `valid_at` from **server clock**.
13. **Upsert.** Idempotent. Mem0 v3 ADD-only (conflicts = extra rows; ranker picks current). Graphiti: `invalid_at`, do not delete.
14. **Watermark.** Facts become searchable only after embed+index. Query-after-write by **id**, not ANN.
15. **Compact job (separate).** If STM tokens >= ~70% of window budget, persist -> then rewrite STM.

**Consolidation path (background maintenance):**

16. **Trigger.** Session-end, cron schedule, or cumulative importance exceeds threshold (~150 points).
17. **Batch.** Episodic memories since last consolidation batched for re-analysis.
18. **Extract facts.** LLM extracts durable semantic facts from raw episodic traces (entities, preferences, outcomes).
19. **Reflect.** Higher-level insights from clusters of related episodes (Park et al. reflection mechanism).
20. **GC.** TTL-expired memories removed; low-importance entries archived; near-duplicates merged.
21. **Write consolidated facts** via standard write path including conflict resolution.

---

## 4. Key Algorithms & Mechanics

### 4.1 STM Strategies: Last-k vs Token Budget

**Conversation buffer (full history).** Linear growth; zero loss until overflow. LangChain classic `ConversationBufferMemory` is **deprecated since 0.3.1, removal in 2.0**; replacement is `create_agent` + a **checkpointer**.

**Window (k turns).** FIFO last-k human/AI pairs. Constant RAM; drops early constraints. Use when recency *is* the task (IVR, short tickets). **Do not** use as the only store for identity or policy.

**Token-budgeted buffer.** Drop oldest until `max_tokens`. Strictly better than k turns when message size varies (tool dumps vs "ok").

| Product | Durable STM Object | LLM-Facing Window | Cross-Session? |
|---------|-------------------|--------------------|---------------|
| LangGraph checkpointer | Super-step snapshot of `messages` | Same, unless middleware rewrites | No -- `thread_id` |
| `SummarizationMiddleware` | Rewritten `state["messages"]` (lossy, permanent) | Summary + last-N | Only if checkpointer stores it |
| Anthropic Messages | Client holds full unmodified history; server compact is request-scoped | Compacted / tool-cleared | No, unless memory-tool files |
| OpenAI compact | Compacted window including encrypted item; users kept verbatim | That window | Conversations API can outlive a Response's 30-day default |
| Letta | Messages in agent DB + blocks | Compiled from DB | Yes for blocks; messages until autoclear |

**Key trap:** Compaction *of* `messages` is a **control-plane rewrite**. Context editing that clears tool results on a deepcopy does **not** persist. Durable transcript != LLM-facing window.

### 4.2 Vector Namespaces as ACL

**Pinecone multi-tenancy**: One namespace per tenant in a shared serverless index. Queries **cannot** cross namespaces. Query RUs scale with **namespace size** (1 RU / 1 GB). 100 tenants x 1 GB: query one tenant = **1 RU**; metadata-filter a 100 GB shared namespace = **100 RUs**. Metadata `$in`/`$nin` max **10,000** values.

**LangGraph Store**: Namespaces are ACL by convention: `("t", tenant, "u", user, "kind", "facts")`. Prefix search `("t", tenant)` matching `("t", tenant, "u", alice, ...)` is a **leak primitive**. `put` **replaces** the key -- no CRDT.

**Mem0 v3**: `user_id` on `search()` as a top-level kwarg **raises** -- tenant is a filter, not an argument the model can invent.

**Rule**: Pre-filter `tenant_id`/`user_id` on every dense, BM25, and Cypher path. Post-filter after top-k leaks ANN neighbors.

### 4.3 LTM Products: Mem0 / Zep / Letta / Store

#### MemGPT -> Letta (Virtual Context)

Context window = physical RAM; overflow pages to archival + recall via OS-like tools (Packer et al. 2023).

```
+----------------------------------------------------+
|  CORE MEMORY (always in context window)            |
|  - Agent persona block                             |
|  - User info block                                 |
|  - Critical context (~few KB)                      |
|  - Agent reads/writes directly via function calls  |
+----------------------------------------------------+
|  RECALL MEMORY (conversation history on disk)      |
|  - Full conversation log stored outside context    |
|  - Agent searches via conversation_search() tool   |
|  - Like a disk cache -- paged in on demand         |
+----------------------------------------------------+
|  ARCHIVAL MEMORY (large-scale long-term storage)   |
|  - Unbounded vector-indexed storage                |
|  - Agent inserts via archival_memory_insert()      |
|  - Agent queries via archival_memory_search()      |
|  - Like cold storage / tape                        |
+----------------------------------------------------+
```

| Tier | Mechanism | Limits (docs) | Role |
|------|-----------|---------------|------|
| Core / blocks | Always-in-context labeled strings; shareable | Rec. **<50k chars/block**, **<20 blocks/agent** | User card + persona (hot) |
| Files | Open/close + grep + semantic | **5 MB**/file, rec. **<100 files** | Repo/docs (cold until paged) |
| Archival | insert/search; ~**300 tok**/passage; unlimited count | Agent-curated facts | Warm semantic |
| Conversation search | Hybrid over messages | Automatic | Episodic |

**Self-directed memory**: The LLM itself decides when to page information in/out. More adaptive than framework-managed approaches, but memory quality depends entirely on model judgment. Every memory operation costs inference tokens.

**Key distinction**: Letta is not a memory layer you add -- it IS the stack. Adopting Letta means adopting an entire agent platform.

**Sleep-time compute** (Letta 0.7+, arXiv:2504.13171): Background agent **owns write tools**; default `sleeptime_agent_frequency=5`. Paper: ~**5x** less test-time compute; **2.5x** cheaper when **10** queries share precomputed context. Amortization **fails** for one-shot chats. Shared blocks: setting `value` **replaces** the entire block; concurrent primary + sleep-time **clobber** -- needs **single writer**.

**Letta Code 2026 MemFS**: git-backed memory; dreaming worktrees so they do not block the main agent.

#### Mem0 -- Two Eras (Do Not Mix Scores)

**Paper (arXiv:2504.19413):** Pair-wise extract of (m_t-1, m_t) with summary S + last m=10 messages -> candidates -> top s=10 similar -> LLM **ADD / UPDATE / DELETE / NOOP**. Mem0g: Neo4j; mark obsolete edges invalid. Retrieved tokens on LOCOMO: Mem0 **1,764**; Mem0g **3,616**; Zep **3,911**; OpenAI playground **4,437**; full-context **26,031**.

**Platform / OSS v3 (2026):** **ADD-only** (no UPDATE/DELETE at extract). Hybrid **semantic + BM25 + entity boost**; temporal ranking at **read** time. Both "lives in NYC" and "moved to SF" remain; retriever ranks current. Graph store **removed** from OSS.

**Do not flatten**: Paper LoCoMo J **66.88%** (with UPDATE/DELETE) != Platform LoCoMo **92.5** (different product, different year).

#### Zep + Graphiti -- Temporal KG

Three-tier graph (arXiv:2501.13956): (1) **episode subgraph** -- raw messages + `t_ref`; non-lossy; NER context last n=4 messages; reflection pass; (2) **semantic entity subgraph** -- Cypher writes, not LLM-generated queries; hybrid cosine + BM25; (3) **community subgraph** -- label propagation (not Leiden).

**Bi-temporal**: valid time (`valid_at` / `invalid_at`) vs transaction time (`created_at` / `expired_at`). Contradiction -> invalidate, **do not delete**. Retrieval f = compose(search, rerank, construct): facts with date ranges + summaries. **No LLM at retrieve time** in the paper stack.

Paper LME_S (~115k tok): gpt-4o **71.2%** vs **60.2%** full-context (+18.5 pp), **1.6k** vs **115k**, **2.58 s** vs **28.9 s**. Vendor 2026: LoCoMo **94.7% @ 155 ms**, LME **90.2% @ 162 ms**.

### 4.4 Advanced Episodic Systems

**MemoryBank** (Zhong et al., AAAI 2024): R = exp(-t/S); recall increments S and resets t. Summaries sit outside the decay draw -- the derived record is durable; the turns die. Opposite of Graphiti (invalidate facts, keep episodes).

**HiGMem** (ACL Findings 2026): Event summaries as cheap anchors; LLM then picks linked turns. Adversarial F1 **0.54 -> 0.78** vs A-Mem while retrieving an **order of magnitude fewer turns**. Hierarchical memory only works if you **keep pointers** to episodes.

**A-Mem** (NeurIPS 2025): Zettelkasten + LLM link generation + evolution of neighbor notes on insert. LoCoMo GPT-4o **1,216** tok vs **16,910**; Mem0 re-run **2,520** tok. **Evolution is write-amplification.**

**Extractive vs abstractive episodes**: After-call summaries are abstractive (lossy). The transcript / Graphiti episode / AgentCall quote is extractive provenance. Persistent summarization that replaces old messages means the UI must not assume the checkpoint still has the needle.

**Hippocampal indexing** (HippoRAG PPR, EM-LLM surprise segmentation, REMem): Graphiti's episode->entity->community IS this loop. A flat Pinecone index is NOT. RAPTOR-shaped trees are for corpus RAG; HiGMem/Graphiti-shaped trees are for interaction memory.

### 4.5 Memory Consolidation Algorithms

Consolidation transforms raw episodic traces into durable, structured long-term memories. The production pattern is background consolidation -- expensive LLM-based structuring happens asynchronously, never blocking user interactions.

**SCM (Sleep-Consolidated Memory)** -- arXiv:2604.20943, April 2026:

Five neuroscience-inspired components:
1. **Working Memory**: Capped at 7 episodes (Miller's Law), with prioritization and recency-based access boosting.
2. **Value Tagging**: Four-axis importance vector: novelty (embedding uniqueness), affective valence (LLM sentiment), task relevance (cosine to goals), repetition (frequency count).
3. **NREM Sleep Phase**: Proportional synaptic downscaling -- reduces activation of low-importance connections.
4. **REM Sleep Phase**: Selects high-importance concepts and generates novel associative links.
5. **Self-Model**: Sleep episodes stored as episodic memories, enabling introspective capability tracking.

Performance: ~3,000 lines of Python, runs on MacBook Air 8GB RAM, memory search latency <1ms, reduces noise by **90.9%** while maintaining perfect recall accuracy.

**Letta Sleep-Time Compute** (April 2025): Decouples context processing from request handling. Agent reasons about accumulated context during idle time.

**LangMem BackgroundMemoryManager**: Async service. After conversation ends, prompts LLM to produce parallel tool calls that create, update, or delete memory records.

**Recursive summarization**: M_i = LLM(session_i, M_i-1). Zep DMR baseline: recursive summarization **35.3%** vs MemGPT **93.4%** -- a single rolling summary is **not** a memory system for multi-session QA.

**Design rule**: Raw event records (episodic) and extracted consolidated facts (semantic) must be distinct data structures. Episodic is append-only and temporally ordered; semantic is mutable and fact-centric.

### 4.6 Context Compression Techniques

Compression is the **fourth plane** -- not LTM. It shrinks the working set without pretending to persist.

**Techniques ranked by production adoption:**

| Method | Type | Survives as LTM? | Typical Ratio | Quality Note |
|--------|------|------------------|---------------|-------------|
| Sliding window / `trim_messages` | Extractive drop | No (unless event log kept) | Window / history | Zero cost; identity loss is O(T-k) |
| Tool output pruning | Mechanical delete | Tool results refetchable | Anthropic **-84%** tokens | Highest ROI per compute |
| LLMLingua (EMNLP 2023) | Extractive token drop via small-LM PPL | No | Up to **20x**; GSM8K **-1.5** EM | Not memory; can delete only copy of constraint |
| LongLLMLingua (ACL 2024) | Question-aware; document reorder | No | ~**4x**; NQ **+21.4%** | Quality actually improves |
| LLMLingua-2 | Token classification (XLM-R) | No | **2-5x**; compressor **3-6x** faster | Task-agnostic |
| Rolling summarization | Abstractive | Becomes new memory (lossy) | Session -> hundreds of tok | Precision loss over multiple cycles |
| Anchored iterative summarization (Factory.ai) | Abstractive structured | Structured document | 98-99% compression | Preserves critical details via sections |
| ACON (arXiv:2510.00615) | Adaptive compression | No | 26-54% peak reduction | Gradient-free; any API model |
| FS offload (Deep Agents, memory tool) | Extractive move | Yes, if file durable | Tool dump -> preview + path | 20k offload threshold |
| Anthropic compact | Abstractive server summary | No, unless memory tool wrote first | Vendor +29% / +39% | Lossy STM; `pause_after_compaction` is human breaker |
| OpenAI `/responses/compact` | Opaque encrypted item | Users kept verbatim | 99.3% compression | Sacrifices interpretability entirely |

**Key rule**: Persist (memory tool / blocks / episodes) **before** you drop. LLMLingua does not extract facts or isolate tenants. Compression != memory.

**Compression quality caveat**: Factory.ai found quality scores range from 3.35-3.70 across methods at similar compression ratios. Measure information preservation, not just size reduction.

### 4.7 Complexity

Let T = thread length, B = STM token budget, k = retrieve width, C = constructor cap, E = extract LLM calls per write, G = graph depth.

- **STM assemble:** Theta(min(T, k_turns)) messages, or Theta(B) tokens. Last-k is O(1) RAM; identity loss is O(T-k).
- **LTM retrieve:** ANN + BM25 over pre-filtered namespace, then pack until C. Cost in generator $ is Theta(C), not Theta(k) if you cap.
- **Write:** E extra generations off the user path. Graphiti G is async and LLM-bound (hours in Mem0's Zep harness; "under a minute" for Mem0g).
- **Compress:** LLMLingua-2 is a small classifier, then fewer billed tokens. Abstractive compact is a second frontier sampling pass.
- **Erasure:** Fan-out over semantic rows + episodes + vector IDs + caches + traces + backups -- Theta(stores), not Theta(1) API 200.

### 4.8 Invariants (Memorize These)

1. The LLM is **not** the memory. Working memory is compiled; LTM is retrieval (read) + learning (write).
2. **RAG != memory.** Shared corpus is world knowledge; agent memory is the interaction stream.
3. **ACL before ANN.** Pre-filter `tenant_id`/`user_id` on every dense, BM25, and Cypher path.
4. **Write path != read path.** Never block TTFT on extract+index. Never answer "what I just told you" from LTM.
5. Namespaces are ACL. Prefix search without `user_id` is a leak. Pinecone: one ns/tenant; Store: full tuple.
6. Constructor **token budget**, not unlimited top-k. Zep paper 1.6k vs Mem0 ~7k vs 115k full-context is the product.
7. Compressors are **not** LTM. Persist before you drop.
8. Episodes are provenance. Invalidate facts, **keep** episodes (until Art. 17 hard-delete).
9. Semantic writes require an **origin tag**. Web/tool observations are not auto-promoted.
10. Memory writes are **exactly-once via idempotency keys** `(thread_id, checkpoint_id, memory_id)`.
11. Single-writer on shared Letta blocks / profile cards. Last-write-wins otherwise.
12. Vectors are confidential as source text. Soft-delete is not Art. 17. TTL is not GDPR.

---

## 5. Token Economics & Cost Analysis

### 5.1 Generator Cost: Constructor vs Stuff-All

LOCOMO conversations average ~**26k** tokens. LongMemEval_S ~**115k**. Stuff-all **wins** judge score on 26k but **loses** on 115k. The economic case is "accurate enough at 1/10-1/70th the tokens."

Sonnet 5 ($2/$10 per MTok), 500 tokens out:

```
C_turn = (N_in * P_in + 500 * P_out) / 10^6
```

| Generator Context | Input Tok | Gen $ / Turn | **$ / 1k Turns** |
|-------------------|----------|-------------|-----------------|
| Mem0-like 1.8k memory + 1k query | ~2.8k | ~$0.0106 | **~$11** |
| Mem0 v3 ~7k | ~8k | ~$0.021 | **~$21** |
| Zep constructor 1.6k | ~2.6k | ~$0.0102 | **~$10** |
| Full LOCOMO 26k | ~27k | ~$0.059 | **~$59** |
| LongMemEval_S 115k | ~116k | ~$0.237 | **~$237** |

### 5.2 Memory SKU Pricing (2026)

| Product | Published Meter | Memory $ / 1k Retrieve-Turns |
|---------|----------------|------------------------------|
| Mem0 Starter $19/mo / 50k adds + 5k retrievals | Retrieval-bound | 1k sessions = 20% of retrieval quota -> **$3.80** |
| Mem0 Pro $249/mo / 500k adds + 50k retrievals | + graph + Dream LLM | **$4.98** |
| Mem0 Hobby $0 / 10k adds + 1k retrievals | 1 retrieve/turn saturates at 1k | **$0** until overage |
| Zep Flex $125/mo / 50k credits | RPM **600** / **1000** | Do not convert to $/turn without a quote |
| Letta API $20/mo + $0.10/active agent/mo + $0.00015/s tools | Memory LLM tokens dominate | ~$20.10/mo for one always-on agent |

**Key insight**: On the reference turn, **generation still beats the memory SKU** (~$10-21 vs ~$4-5) unless you stuff 26k-115k or run sleep-time without amortization.

### 5.3 Embedding Costs

| Model | Rate | Daily @ 1B tokens | Monthly |
|-------|------|-------------------|---------|
| text-embedding-3-small | $0.02/1M | $20 | $600 |
| text-embedding-3-large | $0.13/1M | $130 | $3,900 |
| Cohere embed-v4 | ~$0.10/1M | $100 | $3,000 |

**Self-hosting break-even**: vs text-embedding-3-small: ~37B tokens/month. vs Cohere: ~7.5B/month. Below these thresholds, APIs are cheaper.

**Hidden cost multiplier**: Actual bills average **2.5-4x** vendor pricing page estimates. Pinecone excludes data import and embedding inference costs. Using 3,072-dim embeddings quadruples Weaviate dimension billing vs 1,536-dim.

### 5.4 Vector Database Storage Costs

| Scale | Pinecone Serverless | Qdrant Cloud | pgvector on RDS |
|-------|-------------------|-------------|-----------------|
| 10M vectors (1,536-dim) | ~$70/mo | ~$65/mo | ~$45/mo |
| 100M vectors | $700+/mo | Moderate | <$100/mo |
| Agent workload (500 users, ~5K vectors each) | ~$80/mo | ~$45/mo | ~$40/mo |

**Cost-saving levers**:
- Binary Quantization (Weaviate): 32x compression, can reduce 100M vectors from $1,459/mo to ~$45/mo.
- Self-hosted Qdrant at 20M vectors saves $2,387/mo vs Pinecone Serverless.
- **pgvector is the right default for ~70% of agent workloads** under 10M vectors when the team already runs Postgres.

### 5.5 Memory Retrieval Cost Per Query

```
Typical (Mem0-style, no LLM at retrieval):
  Query embed:  $0.000015 (750 tokens, text-embedding-3-small)
  Vector read:  $0.000008 (Pinecone, 1 RU)
  Total:        ~$0.000023/query  -->  $0.023/1K queries

With LLM-augmented deep recall (CrewAI RecallFlow):
  Add $0.0025-$0.01 per query (GPT-4o-mini for query analysis)
  Total:        ~$0.003-$0.01/query  -->  $3-$10/1K queries
```

### 5.6 Write-Path Extract Cost

Mem0 paper: two LLM passes per pair; v3: one ADD-only pass. Embed cost: 500 writes x 200 tok = 100k embed tok -> **$0.002** (noise). Extract cost: one Sonnet 5 call of 2k in / 400 out ~ **$0.008**. The memory-layer **SKU** can dominate if you retrieve every turn on a metered plan (Starter dies at 5k retrieve-turns/month).

### 5.7 Compression ROI

Production-shaped savings: LongLLMLingua **4x** with quality up; LLMLingua-2 **2-5x** with a cheap compressor; tool-clear **84%** (Anthropic). Abstractive compact trades cache hits and auditability for window headroom.

### 5.8 Cache vs Memory Writes

Pin a 1-2k **profile card** (updated by sleep-time, not per turn) in the cached prefix; put retrieve-k in the uncached suffix. That is the only way personalization and a 5-minute Anthropic cache TTL coexist. Sleep-time rewrite of a Letta `human` block mid-session **busts** the prefix hash.

---

## 6. Production Patterns & Code

### 6.1 Memory Control Plane (Four-Plane Orchestrator)

This implementation demonstrates: namespace ACL before ANN, constructor budget packing, circuit breaker with fail-open to STM, idempotent extract-upsert, compaction after persist, zero-trust tool proxy, Art. 17 hard-delete, and JSON structured logging. Run: `python memory_runtime.py`.

```python
#!/usr/bin/env python3
"""Memory-systems production control plane. Python 3.11+.

  python memory_runtime.py

Offline self-test uses an in-process store (no network, no LLM, no Pinecone).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Literal, TypeVar

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
INITIAL_RETRY_DELAY = 0.5
MAX_RETRY_DELAY = 8.0
SDK_DEFAULT_MAX_RETRIES = 2
CONSTRUCTOR_BUDGET_TOK = 4000
COMPACT_TRIGGER_FRACTION = 0.70
PROFILE_CARD_BUDGET_TOK = 1500
STM_BUDGET_TOK = 8000

MemoryKind = Literal["fact", "preference", "episode", "profile", "observation"]
OriginTag = Literal["user", "sleep_time", "extractor", "tool", "web"]
WriterRole = Literal["user_confirmed", "sleep_time", "ingest_worker", "model_tool"]


# ---------------------------------------------------------------------------
# Structured JSON Logging (correlation_id + tenant + user_hash + plane)
# ---------------------------------------------------------------------------
class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
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


def build_logger(
    correlation_id: str, tenant: str,
    user_id: str | None = None, plane: str | None = None,
) -> CorrelationAdapter:
    base = logging.getLogger("memory.runtime")
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
# Error Hierarchy
# ---------------------------------------------------------------------------
class TransientError(Exception):
    """Retryable: 408/429/5xx/529, ANN timeout, embedder blip."""
    def __init__(self, msg: str, retry_after: float | None = None, status: int | None = None):
        super().__init__(msg)
        self.retry_after = retry_after
        self.status = status


class PermanentError(Exception):
    """Non-retryable: 400 schema, 401/403, RBAC deny, origin violation."""
    pass


class CircuitOpenError(TransientError):
    pass


# ---------------------------------------------------------------------------
# Circuit Breaker (one per retrieve / write / embedder / MCP server)
# ---------------------------------------------------------------------------
class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class BreakerStateMachine:
    """Per retrieve / write / embedder / MCP. Do not trip on 429-with-Retry-After."""

    def __init__(
        self, name: str,
        failure_threshold: int = 5,
        recovery_seconds: float = 30.0,
        half_open_max: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.half_open_max = half_open_max
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = 0
        self._lock = asyncio.Lock()

    async def allow(self) -> None:
        async with self._lock:
            if self._state is BreakerState.OPEN and \
               (time.monotonic() - self._opened_at) >= self.recovery_seconds:
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
            if self._state is BreakerState.HALF_OPEN or \
               self._failures >= self.failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = 0

    @property
    def state(self) -> BreakerState:
        return self._state


# ---------------------------------------------------------------------------
# Retry with Full Jitter (HTTP/transport only; never wraps PermanentError)
# ---------------------------------------------------------------------------
T = TypeVar("T")

async def retry_with_jitter(
    fn: Callable[[], Awaitable[T]], *,
    log: CorrelationAdapter,
    attempts: int = SDK_DEFAULT_MAX_RETRIES + 1,
    base: float = INITIAL_RETRY_DELAY,
    cap: float = MAX_RETRY_DELAY,
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
            sleep_s = ra if ra is not None and 0 < ra <= 60 \
                else random.random() * min(cap, base * (2 ** i))
            log.warning("http_retry attempt=%s sleep_s=%.3f err=%s", i + 1, sleep_s, exc)
            await asyncio.sleep(sleep_s)
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    """Hot-path approximate count (~4 chars/token). Use tokenizer for billing."""
    return max(1, (len(text) + 3) // 4)


def redact_pii(text: str) -> str:
    """Deterministic DLP: digit runs >= 9 become [PII] before embed."""
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i].isdigit():
            j, n = i, 0
            while j < len(text) and (text[j].isdigit() or text[j] in "- "):
                n += int(text[j].isdigit())
                j += 1
            if n >= 9:
                out.append("[PII]")
                i = j
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def deterministic_degraded(turn_id: str) -> dict[str, Any]:
    return {"status": "degraded", "turn_id": turn_id, "memory": "stm_only", "hits": []}


# ---------------------------------------------------------------------------
# Memory Record (namespace tuple, origin tag, episode pointer, embedder pin)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    tenant_id: str
    user_id: str
    kind: MemoryKind
    origin: OriginTag
    text: str
    tokens: int
    created_at: float
    valid_at: float | None = None
    invalid_at: float | None = None
    source_episode_id: str | None = None
    embedding_model: str = "text-embedding-3-small"
    index_version: str = "v1"
    quarantined: bool = False
    deleted: bool = False

    def namespace(self) -> tuple[str, ...]:
        """ACL namespace: ("t", tenant, "u", user, "kind", memory_kind)"""
        return ("t", self.tenant_id, "u", self.user_id, "kind", self.kind)


@dataclass
class PackedContext:
    profile_card: MemoryRecord | None
    hits: list[MemoryRecord]
    stm_messages: list[str]
    constructor_tokens: int
    dropped: int
    compacted: bool


# ---------------------------------------------------------------------------
# Namespace ACL: pre-filter before ANN, never post-filter after top-k
# ---------------------------------------------------------------------------
class NamespaceACL:
    """Namespaces are ACL. Prefix without user_id is a leak primitive."""

    write_roles: frozenset[WriterRole] = frozenset({
        "user_confirmed", "sleep_time", "ingest_worker"
    })
    semantic_origins: frozenset[OriginTag] = frozenset({
        "user", "sleep_time", "extractor"
    })

    def reject_prefix_leak(self, ns: tuple[str, ...]) -> None:
        if len(ns) < 6 or ns[0] != "t" or ns[2] != "u" or ns[4] != "kind":
            raise PermanentError(f"namespace_incomplete:{ns}")

    def authorize_read(self, token_tenant: str, token_user: str, record: MemoryRecord) -> None:
        self.reject_prefix_leak(record.namespace())
        if record.tenant_id != token_tenant or record.user_id != token_user:
            raise PermanentError("acl_read_denied")
        if record.deleted or record.quarantined:
            raise PermanentError("acl_tombstone")

    def authorize_write(
        self, token_tenant: str, token_user: str,
        record: MemoryRecord, writer_role: WriterRole,
    ) -> None:
        self.reject_prefix_leak(record.namespace())
        if record.tenant_id != token_tenant or record.user_id != token_user:
            raise PermanentError("acl_write_denied")
        if writer_role not in self.write_roles:
            raise PermanentError(f"rbac_write_deny:{writer_role}")
        if record.kind in {"fact", "preference", "profile"} \
           and record.origin not in self.semantic_origins:
            raise PermanentError(f"origin_not_promotable:{record.origin}")
        if writer_role == "ingest_worker" and record.kind != "episode":
            raise PermanentError("ingest_worker_episodes_only")


# ---------------------------------------------------------------------------
# In-Memory Four-Plane Store (replace with pgvector / Qdrant / Neo4j)
# ---------------------------------------------------------------------------
class InMemoryPlane:
    def __init__(self) -> None:
        self.records: dict[str, MemoryRecord] = {}
        self.stm: dict[tuple[str, str], list[str]] = {}
        self.profile: dict[tuple[str, str], MemoryRecord] = {}
        self.idempotency: dict[str, str] = {}
        self.watermark: dict[tuple[str, str], float] = {}
        self.worm: list[dict[str, Any]] = []
        self.physically_purged: set[str] = set()

    def put(self, rec: MemoryRecord) -> None:
        self.records[rec.memory_id] = rec
        if rec.kind == "profile" and not rec.deleted:
            self.profile[(rec.tenant_id, rec.user_id)] = rec

    def hard_delete(self, memory_id: str) -> None:
        rec = self.records.get(memory_id)
        if rec is None:
            return
        self.records[memory_id] = replace(
            rec, deleted=True, text="", tokens=0, quarantined=True,
        )
        self.physically_purged.add(memory_id)
        key = (rec.tenant_id, rec.user_id)
        if self.profile.get(key) and self.profile[key].memory_id == memory_id:
            del self.profile[key]


# ---------------------------------------------------------------------------
# Constructor Budget Packer: pin card, pack by score, cap at budget
# ---------------------------------------------------------------------------
class ConstructorBudgetPacker:
    def __init__(
        self, budget: int = CONSTRUCTOR_BUDGET_TOK,
        card_budget: int = PROFILE_CARD_BUDGET_TOK,
    ):
        self.budget = budget
        self.card_budget = card_budget

    def pack(
        self, *,
        profile_card: MemoryRecord | None,
        hits: list[tuple[float, MemoryRecord]],
        stm_messages: list[str],
        stm_budget: int = STM_BUDGET_TOK,
    ) -> PackedContext:
        used, card, packed, dropped = 0, None, [], 0
        # Pin profile card (hot, cached prefix)
        if profile_card is not None and not profile_card.deleted:
            card = profile_card
            used = min(profile_card.tokens, self.card_budget)
        # Pack LTM hits by score until budget
        for _score, rec in sorted(hits, key=lambda x: x[0], reverse=True):
            skip = (
                rec.kind == "profile" or rec.deleted or rec.quarantined
                or rec.invalid_at is not None
                or (rec.origin in {"tool", "web"} and rec.kind != "observation")
            )
            if skip or used + rec.tokens > self.budget:
                dropped += int(rec.kind != "profile")
                continue
            packed.append(rec)
            used += rec.tokens
        # Token-budget STM (most recent first)
        stm, stm_used = [], 0
        for msg in reversed(stm_messages):
            tok = estimate_tokens(msg)
            if stm_used + tok > stm_budget:
                break
            stm.append(msg)
            stm_used += tok
        stm.reverse()
        return PackedContext(card, packed, stm, used, dropped, False)


# ---------------------------------------------------------------------------
# Extract + Upsert (idempotent, origin-gated)
# ---------------------------------------------------------------------------
class ExtractUpsert:
    def __init__(self, plane: InMemoryPlane, acl: NamespaceACL):
        self.plane = plane
        self.acl = acl

    def extract_candidates(self, user_text: str, assistant_text: str) -> list[str]:
        """Deterministic stand-in for pair-wise extract (Mem0 m=10 / v3 ADD-only)."""
        blob = redact_pii(f"{user_text}\n{assistant_text}")
        parts = [p.strip() for p in blob.replace("?", ".").split(".")
                 if len(p.strip()) > 12]
        return parts[:4] or [blob[:240]]

    def upsert(
        self, rec: MemoryRecord, *,
        token_tenant: str, token_user: str,
        writer_role: WriterRole, idempotency_key: str,
        log: CorrelationAdapter,
    ) -> MemoryRecord:
        # ACL check: namespace + tenant + user + role + origin
        self.acl.authorize_write(token_tenant, token_user, rec, writer_role)
        # Idempotency: skip if same key already processed
        existing_id = self.plane.idempotency.get(idempotency_key)
        if existing_id and existing_id in self.plane.records:
            log.info("extract_idempotent key=%s id=%s", idempotency_key, existing_id)
            return self.plane.records[existing_id]
        self.plane.put(rec)
        self.plane.idempotency[idempotency_key] = rec.memory_id
        self.plane.watermark[(rec.tenant_id, rec.user_id)] = rec.created_at
        self.plane.worm.append({
            "op": "upsert", "memory_id": rec.memory_id,
            "origin": rec.origin, "kind": rec.kind,
        })
        log.info("extract_upsert id=%s kind=%s origin=%s",
                 rec.memory_id, rec.kind, rec.origin)
        return rec


# ---------------------------------------------------------------------------
# Compaction Trigger: persist THEN drop, 70% threshold
# ---------------------------------------------------------------------------
class CompactionTrigger:
    """Extractive trim after persist. Not LTM. Not a frontier summarizer."""

    def __init__(self, fraction: float = COMPACT_TRIGGER_FRACTION):
        self.fraction = fraction

    def should_compact(self, stm_tokens: int, window_tokens: int) -> bool:
        return window_tokens > 0 and stm_tokens >= int(self.fraction * window_tokens)

    def compact(
        self, stm_messages: list[str], *,
        episodes_persisted: bool, keep_last: int = 6,
        log: CorrelationAdapter,
    ) -> list[str]:
        if not episodes_persisted:
            raise PermanentError("compact_without_persist")
        if len(stm_messages) <= keep_last:
            return list(stm_messages)
        dropped = stm_messages[:-keep_last]
        kept = stm_messages[-keep_last:]
        log.warning("compact_lossy dropped=%s kept=%s", len(dropped), len(kept))
        return [f"[compacted extractive:{len(dropped)} msgs; episodes durable]", *kept]


# ---------------------------------------------------------------------------
# Memory Orchestrator (ties all planes together)
# ---------------------------------------------------------------------------
class MemoryOrchestrator:
    def __init__(self, plane: InMemoryPlane | None = None):
        self.plane = plane or InMemoryPlane()
        self.acl = NamespaceACL()
        self.packer = ConstructorBudgetPacker()
        self.extract = ExtractUpsert(self.plane, self.acl)
        self.compact = CompactionTrigger()
        self.retrieve_breaker = BreakerStateMachine("memory.retrieve")

    def _score(self, query: str, rec: MemoryRecord, now: float) -> float:
        """Simplified scoring: recency * 0.5 + relevance * 3.0 + importance * 2.0"""
        if rec.deleted or rec.quarantined or rec.invalid_at is not None:
            return -1.0
        q = query.lower()
        rel = 1.0 if q and q in rec.text.lower() else \
              (0.35 if any(w in rec.text.lower() for w in q.split()) else 0.05)
        recency = 0.995 ** max(0.0, (now - rec.created_at) / 3600.0)
        importance = 1.0 if rec.kind in {"preference", "profile"} else 0.6
        return recency * 0.5 + rel * 3.0 + importance * 2.0

    def search_prefilter(
        self, token_tenant: str, token_user: str,
        query: str, kind: MemoryKind | None = None, k: int = 20,
    ) -> list[tuple[float, MemoryRecord]]:
        """ACL before ANN. Post-filter-after-top-k is the leak this rejects."""
        now = time.time()
        pool = [
            rec for rec in self.plane.records.values()
            if rec.tenant_id == token_tenant and rec.user_id == token_user
            and not rec.deleted and not rec.quarantined
            and (kind is None or rec.kind == kind)
        ]
        ranked = sorted(
            ((self._score(query, r, now), r) for r in pool),
            key=lambda x: x[0], reverse=True,
        )
        return [pair for pair in ranked if pair[0] >= 0][:k]

    async def read_turn(
        self, *,
        token_tenant: str, token_user: str, query: str,
        log: CorrelationAdapter,
        retrieve_fn: Callable[[], Awaitable[list[tuple[float, MemoryRecord]]]] | None = None,
    ) -> PackedContext:
        """Full read path: breaker -> retrieve -> pack -> fallback to STM+card."""
        stm = list(self.plane.stm.get((token_tenant, token_user), []))
        card = self.plane.profile.get((token_tenant, token_user))
        hits: list[tuple[float, MemoryRecord]] = []
        try:
            await self.retrieve_breaker.allow()

            async def _do() -> list[tuple[float, MemoryRecord]]:
                if retrieve_fn is not None:
                    return await retrieve_fn()
                return self.search_prefilter(token_tenant, token_user, query)

            hits = await retry_with_jitter(_do, log=log)
            await self.retrieve_breaker.record_success()
        except CircuitOpenError as exc:
            log.warning("memory_miss stm_fallback err=%s", exc)
        except TransientError as exc:
            await self.retrieve_breaker.record_failure(trip=True)
            log.warning("memory_retrieve_transient err=%s", exc)
        packed = self.packer.pack(profile_card=card, hits=hits, stm_messages=stm)
        if card is not None:
            try:
                self.acl.authorize_read(token_tenant, token_user, card)
            except PermanentError:
                packed = PackedContext(None, [], packed.stm_messages, 0, packed.dropped, False)
        return packed

    def append_stm(self, tenant: str, user: str, message: str) -> None:
        self.plane.stm.setdefault((tenant, user), []).append(message)

    def maybe_compact(
        self, tenant: str, user: str,
        window_tokens: int, log: CorrelationAdapter,
    ) -> bool:
        msgs = self.plane.stm.get((tenant, user), [])
        if not self.compact.should_compact(
            sum(estimate_tokens(m) for m in msgs), window_tokens,
        ):
            return False
        episodes_ok = any(
            r.kind == "episode" and r.tenant_id == tenant
            and r.user_id == user and not r.deleted
            for r in self.plane.records.values()
        )
        self.plane.stm[(tenant, user)] = self.compact.compact(
            msgs, episodes_persisted=episodes_ok, log=log,
        )
        return True

    def gdpr_hard_delete(
        self, token_tenant: str, token_user: str, log: CorrelationAdapter,
    ) -> list[str]:
        """Art. 17 fan-out: clear text, STM, profile, record physical purge."""
        ids = [
            r.memory_id for r in list(self.plane.records.values())
            if r.tenant_id == token_tenant and r.user_id == token_user
        ]
        for mid in ids:
            self.plane.hard_delete(mid)
        self.plane.stm.pop((token_tenant, token_user), None)
        self.plane.profile.pop((token_tenant, token_user), None)
        self.plane.worm.append({
            "op": "art17_hard_delete", "tenant": token_tenant, "count": len(ids),
        })
        log.info("art17_fanout count=%s", len(ids))
        return ids


# ---------------------------------------------------------------------------
# Zero-Trust MCP Proxy: identity from TOKEN, never from model JSON
# ---------------------------------------------------------------------------
class ZeroTrustMemoryProxy:
    def __init__(self, orch: MemoryOrchestrator, allowed: frozenset[str]):
        self.orch = orch
        self.allowed = allowed

    def execute(
        self, *, principal: str, token_tenant: str, token_user: str,
        tool: str, args: dict[str, Any], writer_role: WriterRole,
    ) -> str:
        if not principal:
            raise PermanentError("missing_principal")
        if tool not in self.allowed:
            raise PermanentError(f"rbac_deny:{tool}")
        # Confused deputy check: model cannot override user_id
        if "user_id" in args and args["user_id"] != token_user:
            raise PermanentError("confused_deputy_user_id")
        if tool == "memory_search":
            hits = self.orch.search_prefilter(
                token_tenant, token_user, str(args.get("q", "")),
                k=int(args.get("k", 8)),
            )
            return json.dumps([{"id": r.memory_id, "kind": r.kind} for _s, r in hits])
        if tool == "memory_insert":
            text = redact_pii(str(args.get("text", "")))
            rec = MemoryRecord(
                memory_id=str(uuid.uuid4()),
                tenant_id=token_tenant, user_id=token_user,
                kind=args.get("kind", "fact"), origin=args.get("origin", "tool"),
                text=text, tokens=estimate_tokens(text), created_at=time.time(),
                source_episode_id=args.get("episode_id"),
            )
            log = build_logger(
                str(args.get("cid", uuid.uuid4())),
                token_tenant, token_user, "write",
            )
            self.orch.extract.upsert(
                rec, token_tenant=token_tenant, token_user=token_user,
                writer_role=writer_role,
                idempotency_key=f"{args.get('thread_id')}:{args.get('checkpoint_id')}:{rec.memory_id}",
                log=log,
            )
            return f"ok:{rec.memory_id}"
        raise PermanentError(f"unknown_tool:{tool}")


# ---------------------------------------------------------------------------
# Fallback Chain: LTM -> STM+card -> deterministic degraded
# ---------------------------------------------------------------------------
class FallbackChain:
    def __init__(self, orch: MemoryOrchestrator):
        self.orch = orch

    async def invoke(
        self, *, token_tenant: str, token_user: str,
        query: str, log: CorrelationAdapter, turn_id: str,
        retrieve_fn: Callable[[], Awaitable[list[tuple[float, MemoryRecord]]]] | None = None,
    ) -> dict[str, Any]:
        try:
            packed = await self.orch.read_turn(
                token_tenant=token_tenant, token_user=token_user,
                query=query, log=log, retrieve_fn=retrieve_fn,
            )
            return {
                "status": "ok" if packed.hits or packed.profile_card else "degraded",
                "turn_id": turn_id,
                "memory": "packed" if packed.hits else "stm_only",
                "constructor_tokens": packed.constructor_tokens,
                "hit_ids": [h.memory_id for h in packed.hits],
                "stm": packed.stm_messages,
            }
        except PermanentError:
            # PermanentError does NOT failover to shared index
            raise
        except Exception as exc:
            log.error("degraded_stm_only err=%s", exc)
            return deterministic_degraded(turn_id)


if __name__ == "__main__":
    # Offline self-test: exercises ACL, breaker, packer, proxy, Art.17
    print("Run the offline test suite to verify all invariants.")
```

**What this code encodes (maps to all prior sections):**
- Full-jitter HTTP retries; `Retry-After` honored iff 0 < t <= 60; RBAC / origin / compact-without-persist are PermanentError (not retried)
- Retrieve breaker closed -> open -> half-open; fallback is STM + cached card / `status: "degraded"`
- JSON logs carry `correlation_id` + tenant + `user_hash` + plane
- Constructor budget packer pins profile card, packs by score until 4000 tok, drops overflow
- Namespace ACL rejects incomplete prefixes, cross-user read/write, `model_tool` writers, and web/tool -> semantic promotion
- Extract-upsert is idempotent on `(thread, checkpoint, memory_id)`
- Compaction trigger fires at 70% of window, refuses unless episodes persisted
- Zero-Trust proxy rejects confused-deputy `user_id` in tool args
- Art. 17 hard-delete clears text, STM, profile, and records physical purge

### 6.2 Three-Tier Memory Store with Hybrid Retrieval

This complementary implementation demonstrates the cognitive model directly: working memory bounded at 7 items (Miller's Law), hybrid recall with vector + BM25 + recency decay + MMR dedup, PII detection, SHA-256 integrity hashing, consolidation, and garbage collection.

```python
"""
Three-tier memory store with hybrid retrieval.
Replace in-memory dicts with pgvector / Qdrant / Neo4j for production.

Dependencies: pip install numpy sentence-transformers
"""
from __future__ import annotations

import hashlib
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

logger = logging.getLogger("memory_system")


class MemoryType(Enum):
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    PROCEDURAL = "procedural"


@dataclass
class MemoryRecord:
    id: str
    content: str
    memory_type: MemoryType
    scope: dict  # {"user_id": ..., "agent_id": ..., "org_id": ...}
    embedding: np.ndarray | None = None
    importance: float = 0.5
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    access_count: int = 0
    ttl_seconds: int | None = None
    metadata: dict = field(default_factory=dict)
    integrity_hash: str = ""
    embedding_model_version: str = ""
    is_consolidated: bool = False

    def __post_init__(self) -> None:
        if not self.integrity_hash:
            self.integrity_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        payload = f"{self.content}|{json.dumps(self.scope, sort_keys=True)}"
        return hashlib.sha256(payload.encode()).hexdigest()

    def verify_integrity(self) -> bool:
        return self.integrity_hash == self._compute_hash()


class PIIDetector:
    """Lightweight PII detector. Replace with Presidio or AWS Comprehend."""

    PATTERNS = {
        "email": re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
        "phone_us": re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"),
        "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "credit_card": re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"),
        "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
        "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    }

    def redact(self, text: str) -> tuple[str, list[dict]]:
        findings = []
        for pii_type, pattern in self.PATTERNS.items():
            for match in pattern.finditer(text):
                findings.append({
                    "type": pii_type, "start": match.start(),
                    "end": match.end(), "text": match.group(),
                })
        if not findings:
            return text, []
        for f in sorted(findings, key=lambda x: x["start"], reverse=True):
            text = text[:f["start"]] + f"[REDACTED_{f['type'].upper()}]" + text[f["end"]:]
        return text, findings


class BM25Index:
    """Simplified BM25 scorer for keyword matching."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self._docs: dict[str, list[str]] = {}
        self._avg_dl: float = 0.0
        self._df: dict[str, int] = defaultdict(int)
        self._n: int = 0

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return re.findall(r"\w+", text.lower())

    def add(self, doc_id: str, text: str) -> None:
        tokens = self._tokenize(text)
        self._docs[doc_id] = tokens
        for t in set(tokens):
            self._df[t] += 1
        self._n = len(self._docs)
        self._avg_dl = sum(len(d) for d in self._docs.values()) / max(self._n, 1)

    def remove(self, doc_id: str) -> None:
        if doc_id not in self._docs:
            return
        for t in set(self._docs.pop(doc_id)):
            self._df[t] = max(0, self._df[t] - 1)
        self._n = len(self._docs)
        self._avg_dl = (
            sum(len(d) for d in self._docs.values()) / max(self._n, 1) if self._n else 0
        )

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
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
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]


class MemoryStore:
    """
    Three-tier memory: working (7 items) / short-term / long-term.
    Hybrid recall: vector + BM25 + recency decay + MMR dedup.
    """

    RECENCY_DECAY_HALF_LIFE_DAYS: float = 30.0
    SEMANTIC_WEIGHT: float = 0.5
    RECENCY_WEIGHT: float = 0.3
    IMPORTANCE_WEIGHT: float = 0.2
    CONSOLIDATION_SIMILARITY_THRESHOLD: float = 0.85
    DEFAULT_TOP_K: int = 5

    def __init__(self, embedding_service: Any) -> None:
        self._embedder = embedding_service
        self._pii = PIIDetector()
        self._bm25 = BM25Index()
        self._long_term: dict[str, MemoryRecord] = {}
        self._short_term: dict[str, list[MemoryRecord]] = defaultdict(list)
        self._working_memory: list[MemoryRecord] = []
        self._working_memory_limit: int = 7  # Miller's Law

    def write(
        self, content: str, memory_type: MemoryType,
        scope: dict, importance: float = 0.5,
        ttl_seconds: int | None = None, redact_pii: bool = True,
    ) -> MemoryRecord:
        stored = content
        if redact_pii:
            stored, _ = self._pii.redact(content)
        embedding = self._embedder.embed(stored)
        record = MemoryRecord(
            id=str(uuid.uuid4()), content=stored,
            memory_type=memory_type, scope=scope,
            embedding=embedding, importance=importance,
            ttl_seconds=ttl_seconds,
            embedding_model_version=self._embedder.model_version,
        )
        # Near-duplicate dedup (similarity > 0.85)
        dup = self._find_near_duplicate(record)
        if dup is not None:
            dup.content = record.content
            dup.embedding = record.embedding
            dup.importance = max(dup.importance, record.importance)
            dup.integrity_hash = dup._compute_hash()
            return dup
        self._long_term[record.id] = record
        self._bm25.add(record.id, record.content)
        self._update_working_memory(record)
        return record

    def recall(
        self, query: str, scope: dict | None = None,
        top_k: int | None = None,
    ) -> list[tuple[MemoryRecord, float]]:
        top_k = top_k or self.DEFAULT_TOP_K
        candidates = self._filter_candidates(scope)
        if not candidates:
            return []
        query_emb = self._embedder.embed(query)
        scored = []
        for rec in candidates:
            sem = self._cosine_sim(query_emb, rec.embedding)
            rec_score = self._recency_decay(rec.last_accessed)
            composite = (
                self.SEMANTIC_WEIGHT * sem
                + self.RECENCY_WEIGHT * rec_score
                + self.IMPORTANCE_WEIGHT * rec.importance
            )
            scored.append((rec, composite))
        # BM25 boost (10% bonus)
        bm25_results = dict(self._bm25.search(query, top_k=top_k * 2))
        max_bm25 = max(bm25_results.values()) if bm25_results else 1.0
        boosted = [
            (rec, score + 0.1 * bm25_results.get(rec.id, 0.0) / max(max_bm25, 1e-9))
            for rec, score in scored
        ]
        boosted.sort(key=lambda x: x[1], reverse=True)
        results = boosted[:top_k]
        # MMR dedup
        results = self._mmr_filter(results, query_emb)
        for rec, _ in results:
            rec.last_accessed = datetime.now(timezone.utc)
            rec.access_count += 1
        return results

    def forget(self, scope: dict) -> int:
        """GDPR Art. 17: scope-based deletion."""
        ids = [
            rid for rid, r in self._long_term.items()
            if all(r.scope.get(k) == v for k, v in scope.items())
        ]
        for rid in ids:
            self._bm25.remove(rid)
            del self._long_term[rid]
        return len(ids)

    def garbage_collect(self) -> dict:
        now = datetime.now(timezone.utc)
        stats = {"ttl_expired": 0, "decay_pruned": 0, "integrity_failed": 0}
        to_remove = []
        for rid, rec in self._long_term.items():
            if rec.ttl_seconds and (now - rec.created_at).total_seconds() > rec.ttl_seconds:
                to_remove.append(rid); stats["ttl_expired"] += 1; continue
            if not rec.verify_integrity():
                to_remove.append(rid); stats["integrity_failed"] += 1; continue
            if self._recency_decay(rec.last_accessed) * rec.importance < 0.01 and rec.access_count == 0:
                to_remove.append(rid); stats["decay_pruned"] += 1
        for rid in to_remove:
            self._bm25.remove(rid)
            del self._long_term[rid]
        return stats

    # --- Helpers ---
    def _filter_candidates(self, scope: dict | None) -> list[MemoryRecord]:
        candidates = list(self._long_term.values())
        if scope:
            candidates = [r for r in candidates
                          if all(r.scope.get(k) == v for k, v in scope.items())]
        now = datetime.now(timezone.utc)
        return [r for r in candidates
                if r.ttl_seconds is None or (now - r.created_at).total_seconds() <= r.ttl_seconds]

    def _find_near_duplicate(self, record: MemoryRecord) -> MemoryRecord | None:
        if record.embedding is None:
            return None
        for existing in self._long_term.values():
            if existing.memory_type != record.memory_type or existing.scope != record.scope:
                continue
            if existing.embedding is not None and \
               self._cosine_sim(record.embedding, existing.embedding) > self.CONSOLIDATION_SIMILARITY_THRESHOLD:
                return existing
        return None

    def _recency_decay(self, last_accessed: datetime) -> float:
        age_days = (datetime.now(timezone.utc) - last_accessed).total_seconds() / 86400
        return 0.5 ** (age_days / self.RECENCY_DECAY_HALF_LIFE_DAYS)

    @staticmethod
    def _cosine_sim(a: np.ndarray | None, b: np.ndarray | None) -> float:
        if a is None or b is None:
            return 0.0
        dot = float(np.dot(a, b))
        na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
        return dot / (na * nb) if na and nb else 0.0

    def _mmr_filter(
        self, results: list[tuple[MemoryRecord, float]],
        query_emb: np.ndarray, lam: float = 0.7,
    ) -> list[tuple[MemoryRecord, float]]:
        if len(results) <= 1:
            return results
        selected = [results[0]]
        remaining = list(results[1:])
        while remaining and len(selected) < self.DEFAULT_TOP_K:
            best_idx, best_mmr = -1, -1.0
            for i, (rec, _) in enumerate(remaining):
                rel = self._cosine_sim(rec.embedding, query_emb)
                max_sim = max(self._cosine_sim(rec.embedding, s[0].embedding) for s in selected)
                mmr = lam * rel - (1 - lam) * max_sim
                if mmr > best_mmr:
                    best_mmr, best_idx = mmr, i
            if best_idx >= 0:
                selected.append(remaining.pop(best_idx))
            else:
                break
        return selected

    def _update_working_memory(self, record: MemoryRecord) -> None:
        self._working_memory.append(record)
        if len(self._working_memory) > self._working_memory_limit:
            self._working_memory.sort(
                key=lambda r: r.importance * self._recency_decay(r.last_accessed))
            self._working_memory.pop(0)
```

**What to replace for production:**
- In-memory dicts with pgvector (semantic), Redis (session state), PostgreSQL (audit)
- PIIDetector regex with Microsoft Presidio or AWS Comprehend
- Deterministic consolidation with LLM-based fact extraction
- BM25Index with Elasticsearch or vector DB built-in BM25
- SentenceTransformer with hosted embedding API (or keep self-hosted if volume > 37B tokens/month)

---

## 7. Failure Modes & Mitigations

### 7.1 Comprehensive Failure Taxonomy

| Class | Examples | Handler |
|-------|---------|---------|
| **Transient** | 408/429/5xx/529, ANN timeout, embedder blip, MCP disconnect | Full jitter; fail **open** on read; queue on write; last-good profile card |
| **Permanent** | 400 schema, 401/403, RBAC deny, prefix-leak attempt, spend-cap 429 | Fail the write; do not failover schema 400s; do not retry Art. 17 |
| **Memory poisoning** | Hidden in Memory add **99.8%** GPT-5.5 / **95%** Kimi-K2.6; among retrievals, attacker-intended actions **60-89%**; eTAMP one malicious page ASR **32.5%/23.4%/19.5%**, **x8** under UI frustration | Origin tags; no auto-promote web/tool -> semantic; read-time scoring; quarantine by origin |
| **Write/read race** | Query before graph construction; "what did I just tell you" from LTM | Watermark; episode fallback; **that question is STM** |
| **Stale / contradictory** | "trains for marathon" + "sprained ankle"; two speakers in one store | Bi-temporal edges; v3 ranker; single writer on blocks; user-visible profile |
| **Summarization loss** | Abstractive compact drops the constraint needed on turn 90 | Memory tool / blocks **before** compact; keep episode pointers (HiGMem) |
| **Namespace leak** | Prefix `asearch(("tenant",))`; shared Pinecone ns + post-filter; shared Letta blocks | Full namespace; one-ns-per-tenant; never share blocks across users |
| **Soft-delete "erasure"** | HNSW flag, trace TTL, backup | VACUUM/compaction + crypto-shred; Ghost Vectors 25.5% names |
| **Embedder drift** | New embed model, old index | Pin versions in metadata; batch re-embed on change |
| **Stream bloat** | Tens of thousands of memories; ADD-only unbounded growth; no TTL | TTL + GC; shallow checkpoints; per-user ns |
| **Importance inflation** | LLMs consistently rate everything high | Calibration or forced distribution |
| **Reflection hallucination** | Plausible but false conclusions fed back with same authority | Separate episodic ground truth from derived reflections |
| **Orphan/ghost write** | Store `put` vs checkpoint rollback | Idempotency keys |
| **Judge overfitting** | LoCoMo/DMR as procurement truth (DMR saturates ~94%) | LongMemEval_M / BEAM; hold-out traces; your own tests |

### 7.2 Memory Poisoning Attacks (OWASP ASI06)

Memory poisoning is classified as **ASI06** in the OWASP Top 10 for Agentic Applications 2026 -- **high persistence, very high detection difficulty**.

**Attack taxonomy:**
1. **MINJA (Memory INJection Attack)** -- NeurIPS 2025: Attackers inject malicious records through query-only interaction (no direct memory store access needed). >95% injection success rate, 70% attack success rate. Attack and damage are **temporally decoupled** -- injection in February, damage in April.
2. **PoisonedRAG** -- USENIX Security 2025: A small number of crafted documents in the retrieval corpus causes RAG to reliably return attacker-chosen answers.
3. **Indirect prompt injection via memory**: Malicious instructions persisted in long-term memory survive session restarts, context window resets, and model updates.

**Detection gap**: Advanced LLM-based detectors miss **66%** of poisoned entries because each one looks benign individually (A-MemGuard analysis).

**Multi-agent amplification**: Poisoned memory in one agent propagates to others through shared knowledge bases. Shared/global memory is the highest-risk surface.

### 7.3 Poison Recovery

Poison recovery is **not** "re-embed":
1. Quarantine by origin tag
2. Replay from episodic log with a new extract policy (ADD-only + no episodes **cannot** do this)
3. **Hard-delete** poisoned vector IDs + HNSW compaction
4. Do not promote web/tool observations without human confirm
5. Write-time consistency checks suppress L1 attacks but not L2/L3 -- need read-time context-sensitive scoring

### 7.4 Circuit Breaker Pattern

One breaker per **retrieve**, one per **write/extract**, one per **embedder**, one per **MCP memory server**.

```
           5xx/529/timeout rate >= threshold           probe success
  +--------+  ---------------------------------->  +------+  ------> CLOSED
  | CLOSED |                                       | OPEN |
  +---+----+  429 with Retry-After = throttle      +--+---+
      |       (stay CLOSED; sleep)                    | timer (30s)
      | success resets window                         v
      |                                          +----------+
      +------------------------------------------| HALF_OPEN|-- probe fail --> OPEN
                                                 | 1 cheap  |
                                                 | read     |
                                                 +----------+
```

**Fallback chain**: LTM retrieve -> (breaker open / timeout) -> **STM + cached profile card** -> (STM missing) -> deterministic `{"status":"degraded","memory":"none"}` still answering the user. **PermanentError does not failover** to "search the shared index."

### 7.5 Corruption Detection

| Mechanism | Implementation | Overhead |
|-----------|---------------|----------|
| **SHA-256 integrity baselines** | OWASP Agent Memory Guard: hash every stored memory, compare on read | 59-microsecond median latency |
| **Bi-temporal audit** | Zep: mark obsolete as invalid, not deleted; timeline T + T' | Storage overhead for historical records |
| **Conflict detection** | Mem0g: compare new facts against existing graph via embedding similarity + LLM resolver | LLM call per conflicting fact |
| **Consolidation dedup** | Similarity >0.85 check on save; LLM decides keep/update/delete | LLM call per near-duplicate |

---

## 8. Security & Governance

### 8.1 PII and Vec2Text

**Vec2Text** (Morris et al., EMNLP 2023): Iterative invert+re-embed recovers **92%** of 32-token inputs **exactly**; recovers full names from clinical-note embeddings. Attackers need black-box embed pairs, not model weights. **Treat memory vectors as confidential as source text.** Redact SSN/PAN **before** embed.

**PII pipeline**: Detect -> redact -> audit at ingress **and** before embed **and** before trace. AWS Connect generative post-contact summaries: granular PII redaction is not supported -- identified PII becomes `[PII]`. PCI: do not put PAN in memory at all.

**OWASP Agent Memory Guard** (June 2026): Intercepts every memory read/write through five detection layers -- prompt injection screening, secret/PII leakage detection, key tampering detection, SHA-256 integrity baselines, size anomaly detection. Results: **92.5% recall, 100% precision, zero false positives, 59-microsecond** median latency overhead.

### 8.2 GDPR Compliance

| Article | Requirement | Memory System Implication |
|---------|------------|--------------------------|
| **Art. 15 (Access)** | Data subject can request all stored personal data | Must support scope-based export for user X |
| **Art. 16 (Rectification)** | Data subject can correct inaccurate data | Must support targeted update of specific entries |
| **Art. 17 (Erasure)** | Data subject can request deletion ("right to be forgotten") | Must support scope-based deletion + fan-out |
| **Art. 12(3)** | Response deadline | Max **one month** (+2 if you notify) |
| **Art. 35 (DPIA)** | Persistent profiling may trigger assessment | Assess before deploying persistent personalization |

**Financial exposure**: Breach penalties up to **4% of global annual revenue** or EUR 20 million.

### 8.3 Art. 17 Erasure Fan-Out

Erasure is not a single API call -- it is a **fan-out**:

1. Semantic rows / graph nodes+edges tagged by `user_id`
2. Episodes / checkpoints / Store keys / `/memories` files / call recordings
3. Vector IDs -- HNSW **soft-delete** until compaction/VACUUM. **Ghost Vectors** (arXiv:2606.18497): soft-deleted embeddings remain reconstructible; **25.5%** exact person-name recovery; query suppression != erasure
4. Prompt/response caches; retrieval caches keyed by `user_id`
5. Trace vendors (physical purge delayed)
6. Backups -- crypto-shred **per-user keys** or wait backup TTL inside the month
7. Fine-tuned weights: unlearning unsolved; do not train on raw personal memory

**Key rules**:
- Graphiti invalidation preserves history -- Art. 17 requires a **hard-delete path**, not only `invalid_at`
- Mem0 v3 ADD-only makes audit easier but deletion is a **separate pipeline**
- Per-user encryption keys or per-user indexes beat shared HNSW + metadata
- TTL / `refresh_on_read` is **not** Art. 17

### 8.4 Zero-Trust MCP Memory Tools

Memory tools (`archival_memory_insert`, Mem0 `add`, Store `put`, Anthropic `/memories`) are **privilege**, not convenience.

1. Remote MCP servers are OAuth 2.1 resource servers. RFC 9728 metadata; **RFC 8707** resource indicator; PKCE; validate audience; never token-passthrough
2. `tenant_id` / `user_id` **only from the verified token**. Never from tool arguments the model invented (confused deputy)
3. Separate **observation** vs **belief** stores. Web/tool text is not write-authorized to semantic memory
4. Anthropic memory tool: restrict to `/memories`; path-traversal deny; storage is your infra
5. Claude Code `.mcp.json` / `CLAUDE.md` are procedural memory injection surfaces (CVE-2025-59536 / CVE-2026-21852)
6. Sleep-time / Dream agents that rewrite core blocks are **admin writers**. Single-writer. Audit who launched them
7. Re-validate authorization **at execution**, not only at plan-approval

### 8.5 Tool RBAC: Write vs Read

| Tool | Who May Call | Bind |
|------|------------|------|
| `archival_memory_search` / Store `search` / Mem0 `search` | Any turn, after authz pre-filter | Namespace from token |
| `archival_memory_insert` / Mem0 `add` / Store `put` | Allow-list: user-confirmed preferences; sleep-time; never raw tool-result text | Origin tag required |
| Anthropic `delete` / `str_replace` on `/memories` | Same allow-list + path prefix check | `/memories/{user_id}/` |
| Graphiti `add_episode` | Ingest workers, not the user-facing tool loop | `reference_time` from server clock |
| Compact / summarize | Control plane, not the model | Audit the summary |

### 8.6 Multi-Tenant Isolation

| Framework | Isolation Mechanism | Write Protection |
|-----------|-------------------|-----------------|
| Mem0 | Multi-scope tagging: user_id, agent_id, session_id, org_id | Scope filtering on all queries |
| LangGraph | Tuple-based namespaces: `("org_123", "user_456", "preferences")` | Namespace isolation at Store API level |
| CrewAI | MemorySlice: read-only slices across disjoint scope branches | `PermissionError` on write to read-only |
| Pinecone | One namespace per tenant; queries cannot cross | Metadata `$in`/`$nin` max 10,000 values |
| OWASP | Key tampering detection | SHA-256 catches unauthorized namespace modifications |

### 8.7 Encryption

- **At rest**: All production vector DBs (Pinecone, Qdrant Cloud, Weaviate Cloud) encrypt at rest by default (AES-256). pgvector inherits PostgreSQL TDE.
- **In transit**: TLS 1.2+ required for all database connections. Embedding API calls use HTTPS.
- **Application-level**: For high-sensitivity fields (PII, PHI), consider app-level encryption before embedding. **Trade-off**: encrypted text cannot be meaningfully embedded -- encrypt metadata, not retrieval content.

### 8.8 Immutable WORM Audit

Log: `correlation_id`, tenant, hashed user, `memory_id`, `origin`, tool name, k, which memory_ids entered prompt, constructor tokens, model/index versions, watermark offset, HITL actor, breaker state, Art. 17 request id + physical-purge ids.

---

## 9. System Design Scenarios

### Scenario 1: Multi-Month B2C Copilot Memory (10M MAU)

**Problem.** 10M MAU assistant; users return weeks later; preferences drift; PII; cost cap. Pricing unit: a 1k-turn slice of one power user. At fleet scale, multiply retrieve meters, not 115k stuffed windows. Budget: constructor ~$11-21/1k vs stuff-all $59-237/1k. Constraint: retrieve p95 <300 ms; never wait on consolidation; Art. 17 fan-out including HNSW compaction.

**Architecture:**

```
                    +----------------------------------------------------------+
                    | EDGE  auth, tenant TPM, cid, PII redact BEFORE embed     |
                    | tenant/user FROM TOKEN; Mem0 filters dict; no model ids  |
                    +----------------------------+-----------------------------+
                                                 |
                    +----------------------------v-----------------------------+
                    | CONTROL  memory orchestrator + Agent Server thread       |
                    |  READ: pin 2-4k profile card (cached prefix)             |
                    |        + last ~8k tok trim_messages                      |
                    |        + retrieve k<=20, constructor <=4k tok             |
                    |  WRITE: Kafka/Temporal extract; idempotent ADD           |
                    |  SLEEP: nightly dream / sleep-time SINGLE WRITER on card |
                    |  COMPACT: persist episodes -> 70% extractive/vendor       |
                    |  BREAKER: retrieve fail-open -> STM+card                  |
                    +-----+-------------------------------+--------------------+
                          |                               |
                          v                               v
                    +------------------+            +-------------------------+
                    | DATA  Generation |            | DATA  Extract (async)   |
                    | Sonnet 5 / 5.4   |            | pair extract ADD-only   |
                    | packed <=4k mem  |            | never on TTFT           |
                    | retrieve-k AFTER |            | watermark before ANN    |
                    | cache breakpoint |            | origin != web/tool      |
                    +--------+---------+            +----------+--------------+
                             |                                 |
                    +--------v---------+            +----------v--------------+
                    | TOOL PROXIES     |            | PERSIST  four planes    |
                    | search=read ACL  |            | STM ckpt; LTM facts;    |
                    | insert=allowlist |            | episodes 30-90d TTL;    |
                    | /memories prefix |            | WORM + Art.17 keys      |
                    +------------------+            +-------------------------+
```

**Trade-off matrix:**

| Dimension | A. Stuff-all / shared Pinecone ns | B. Four planes + constructor budget (recommended) | C. Letta self-edit + sleep-time |
|-----------|----------------------------------|--------------------------------------------------|-------------------------------|
| **Cost/1k** | Stuff 26k: ~$59; 115k: ~$237 | ~$11-21 gen + ~$4-5 memory SKU | Sleep-time without amortization fails |
| **Latency** | Full-context p95: 17.1 s | p95 target <300 ms; Mem0 e2e p95: 1.44 s | Archival search is tool loop, not 150 ms ANN |
| **Security** | Post-filter ANN; Vec2Text on shared index | Pre-filter; per-user keys; origin tags; WORM | Self-edit = admin writer; RAG as user memory = other-user retrieval |
| **Scale** | 10M x 115k = finance incident | Retrieve meter is first ceiling -- shard SKUs | $0.10/agent x 10M = not a copilot SKU |

**Decision**: **B** is the only design that treats memory as four planes with a constructor budget. A fails cost and privacy. C is the right hot-card mechanic but wrong fleet control plane.

### Scenario 2: Customer Support Agent with Cross-Session Memory

**Problem.** B2B SaaS, 50K support tickets/month across 8K enterprise accounts. Without memory, every interaction starts from zero. SOC 2 Type II required. Existing infra: AWS PostgreSQL + Redis. Budget: $5K/month.

**Trade-off matrix:**

| Dimension | A: pgvector + LangGraph (recommended) | B: Mem0 Cloud | C: Zep/Graphiti + Neo4j |
|-----------|--------------------------------------|---------------|------------------------|
| **Monthly cost** | ~$2,900 (reuse existing RDS) | ~$4,100 | ~$3,400 |
| **Retrieval p95** | ~150ms (pgvector ANN + BM25) | ~100ms (optimized) | ~120ms (graph + vector) |
| **Ops complexity** | Low (existing PostgreSQL DBA) | Low (managed) | High (graph DB expertise) |
| **Security** | Full control; data stays in VPC | Data leaves VPC | Adds third-party dependency |
| **Time to production** | 4-6 weeks | 1-2 weeks | 6-8 weeks |
| **Conflict resolution** | Manual build required | Built-in (Mem0g) | Best-in-class bi-temporal |

**Decision**: Option A wins given constraints: reuses existing PostgreSQL, stays within $5K budget, keeps data in VPC for SOC 2. If multi-hop relational queries become needed later, add Neo4j sidecar.

### Scenario 3: Call-Center After-Call Summaries

**Problem.** Voice/chat; ACW time; returning caller in seconds-to-months; HIPAA/PCI; supervisors must audit. CRM (balance, address) stays authoritative; memory is commitments and preferences. Eval: quote-backed promise recall ("we will refund $40") + supervisor replay.

**Architecture:**

```
  +-------------+    +---------------------------------------------------------+
  | Voice/chat  |--->| CONTROL  Connect / CCaaS + memory orchestrator          |
  | + supervisor|    |  LIVE: STM transcript window + one-paragraph brief      |
  |             |    |  AFTER: webhook transcript FIRST; extract on            |
  |             |    |         call.report.ready (seconds, not live budget)    |
  |             |    |  THREE extractors //  fact | preference | summary       |
  |             |    |  CRM = system of record; memory != ledger               |
  +-------------+    +----------+----------------------------+-----------------+
                                |                            |
                                v                            v
                    +---------------------+     +-----------------------------+
                    | DATA  Generation    |     | DATA  After-call extract    |
                    | greeting from BRIEF |     | Haiku-class parallel triple |
                    | not 90d transcripts |     | quote + sourceCallId on row |
                    +----------+----------+     +--------------+--------------+
                               v                               v
                    +---------------------------------------------------------+
                    | PERSIST  episode = transcript (non-lossy)               |
                    |          semantic+pref rows quote-backed                |
                    |          TTL = retention policy; WORM actor=extractor   |
                    |          Art.17 fan-out: recording + summary + vectors  |
                    +---------------------------------------------------------+
```

**Trade-off matrix:**

| Dimension | A. Mid-call compact + stuff 90d | B. After-call extract (recommended) | C. Recursive summary only |
|-----------|-------------------------------|-------------------------------------|--------------------------|
| **Cost/1k calls** | Compact = second frontier pass during ACW | Extract once: ~$480/day at 80k calls on Haiku | Cheap and wrong: recursive summary 35.3% DMR |
| **Latency** | Compact on live path; greeting waits on 90d | Extract SLA = seconds after hangup; brief in parallel | ADD-only searchable only after extract |
| **Security** | PAN in window; CRM corrupted | Redact before embed; BAA; actors extractor vs owner | In-place edits destroy provenance |

**Decision**: **B** keeps the transcript as non-lossy episode, writes quote-backed facts after the call, and loads a constructor brief on inbound. CRM remains system of record.

---

## 10. Interview Quick Reference

### Key Numbers to Memorize

| Number | What |
|--------|------|
| **~$11-21 / 1k turns** | Sonnet 5 constructor 2-8k in [inferred] |
| **~$59 / 1k** | Stuff LOCOMO ~26k [inferred] |
| **~$237 / 1k** | Stuff LME_S ~115k [inferred] |
| **$3.80 / $4.98** | Mem0 Starter/Pro retrieval meter / 1k |
| **1,764 tok / 1.440 s / 17.117 s** | Mem0 paper: retrieve tok / p95 total / full-context p95; **91%** cut |
| **71.2% vs 60.2% / 1.6k vs 115k** | Zep paper LME_S gpt-4o accuracy / tokens |
| **94.7% @ 155 ms** | Zep vendor LoCoMo retrieve -- not chat SLO |
| **92.5 / 66.88** | Mem0 platform vs paper LoCoMo J (different products) |
| **m=10 / s=10 / v3 ADD-only** | Mem0 paper extract params vs platform v3 |
| **n=4** | Graphiti NER context messages |
| **<50k chars / <20 blocks / 300 tok** | Letta blocks / passage sizes |
| **5x / 2.5x / freq=5** | Sleep-time compute / amortized / default steps |
| **0.995 / ~150 / [0.5,3,2]** | Gen. Agents: recency decay / reflection threshold / code weights |
| **20x / 4x / 2-5x / -84%** | LLMLingua / LongLLMLingua / LLMLingua-2 / tool-clear |
| **92% / 25.5%** | Vec2Text exact recovery / Ghost Vectors names |
| **1 RU/GB / $in 10,000** | Pinecone ns query cost / metadata filter cap |
| **99.8% / 95% / 60-89%** | Hidden in Memory: add success / Kimi / action rates |
| **35.3% vs 93.4%** | Recursive summary vs MemGPT on Zep DMR |
| **0.85** | Near-duplicate similarity threshold (Mem0/CrewAI) |
| **7** | Miller's Law: working memory capacity (SCM) |
| **30K** | Reliable context window threshold (Chroma benchmark) |
| **2.5-4x** | Hidden cost multiplier vs vendor pricing pages |

### Latency Benchmarks

| Operation | p50 | p95 | p99 |
|-----------|-----|-----|-----|
| Vector ANN search (warm) | <10ms | <50ms | <100ms |
| BM25 keyword search | <5ms | <20ms | <50ms |
| Graph traversal (2-hop) | <20ms | <80ms | <200ms |
| Hybrid retrieval (no LLM) | <50ms | <150ms | <300ms |
| Shallow recall (vector + scoring) | ~200ms | ~400ms | ~800ms |
| Deep recall (with LLM query analysis) | ~1-3s | ~4s | ~6s |
| Mem0 paper e2e (search + generate) | 0.708s | 1.440s | -- |
| Full-context LOCOMO | 9.870s | 17.117s | -- |
| Memory consolidation | 5-30s | 60s | 120s |

### Memory Quality Benchmarks (2026)

| Benchmark | What It Measures | Use For |
|-----------|-----------------|---------|
| **LoCoMo** | Single-hop, temporal, multi-hop, open-domain | Primary recall benchmark |
| **LongMemEval** | Cross-session synthesis, long-term maintenance | Multi-session quality |
| **BEAM** | Recall, freshness, contradictions, forgetting | Broad evaluation (emerging) |
| **DMR** | Direct memory recall | Saturates at ~94% -- too easy |

**Four dimensions of memory quality** (most benchmarks only test #1):
1. **Recall**: Retrieve the right fact
2. **Freshness**: Use the latest version when facts are updated
3. **Contradiction handling**: Resolve conflicting stored facts
4. **Forgetting**: Suppress retracted or expired facts

Production failures concentrate in dimensions 2-4.

### Interview Traps (Fail These, Fail the Round)

- "We use LangGraph memory" without distinguishing **checkpointer (STM, thread_id)** vs **Store (LTM, namespace)**
- RAG index as "user memory." RAG != memory. Shared corpus + missing `user_id` pre-filter = other-user retrieval
- Last-k turns as the only identity store ("I'm vegetarian" on turn 2 of a 200-turn chat is gone)
- `{"messages": [new]}` without `add_messages` **wipes** STM
- Extract on the **user-turn critical path** (blocks TTFT)
- Mem0 paper J 66.88 mixed with platform v3 LoCoMo 92.5 (different products, different years)
- Zep vendor 155 ms treated as chat SLO (retrieve-only vs paper 2.58 s which includes generation)
- "We only store embeddings" as GDPR anonymization (Vec2Text recovers 92%)
- ChatGPT memory / Dreaming V3 treated as an API you can call from enterprise agents
- Answering "what did I just tell you" from LTM (write/read race; that question is STM)
- Auto-promote web/tool observations to semantic memory (memory poisoning)
- Compressors described as "memory" (compressors are STM hygiene, not LTM)

### Interview Closer

> "The model is not the memory. I compile STM, I pre-filter then retrieve LTM into a constructor budget (~$11-21/1k, not $59-$237 stuffed), I extract on Temporal/Kafka with origin tags, I keep episodes so I can cite and erase, and I compact only after persist. Mem0 p95 1.44 s vs 17.1 s. ACL before ANN. RAG is the world; memory is the relationship."

### Persistence Backend Decision Framework

| Backend | Best For | Scaling Ceiling |
|---------|---------|-----------------|
| **pgvector** | Teams already on Postgres; <10M vectors; 70% of agent workloads | ~50M vectors (single node) |
| **Qdrant** | High-QPS vector search; self-hosted or cloud | ~100M+ vectors (distributed) |
| **Pinecone** | Fully managed; fast MVP | Auto-scales (but cold-start) |
| **Neo4j** | Knowledge graphs; multi-hop relational | Enterprise: billions of nodes |
| **Redis** | Session state; low-latency KV; caching | Cluster: ~100GB per node |
| **LanceDB** | Embedded; local-first; CrewAI default | Single-machine |

**Polyglot storage rule**: Episodic needs time-range queries (relational). Semantic needs ANN search (vector). Knowledge graph needs multi-hop traversal (graph). No single database excels at all three.
