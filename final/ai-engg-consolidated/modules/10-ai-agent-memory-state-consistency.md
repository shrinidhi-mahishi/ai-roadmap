# Module 10: AI Agent Memory, State & Consistency

### What Is This?

Stateful agents must answer three questions at every decision point: what step am I on, what is already done, and what comes next? The continuity layer that enables this spans working state (react fast -- every instruction), long-term memory (learn slowly -- after changes stabilize), and systems of record (always win -- authoritative live data). Frameworks routinely conflate these three concerns, leading to stale memories overriding live data, wrong facts poisoning future sessions, or unbounded checkpoint growth crashing storage. This module covers the full memory architecture: cognitive taxonomies, persistence mechanisms (checkpointing, event sourcing, CQRS), retrieval pipelines, consistency models for multi-agent coordination, token economics of memory injection, and the security surface that makes agent memory a governed data asset rather than a simple cache.

---

## 1. System Topology & Data Flow

A production agent memory system spans six cooperating layers: a **cognitive plane** where the LLM reasons over working memory and decides what to remember, retrieve, or evict; a **state management plane** tracking workflow progress, checkpoints, and rollback points with durability guarantees; a **memory plane** organizing knowledge into tiered stores (working/episodic/semantic/procedural) with distinct persistence scopes; a **tool proxy layer** mediating memory and SoR tool calls through MCP with per-agent allowlists; a **storage plane** providing physical backends with consistency and isolation guarantees; and a **telemetry plane** capturing retrieval hit rates, token consumption, memory growth, and staleness metrics.

```
+------------------------------------------------------------------------------+
|                           COGNITIVE PLANE                                     |
|                                                                              |
|  +-------------------+   +-------------------+   +------------------------+  |
|  | Reasoning Engine   |   | Memory Controller |   | Context Budget Manager |  |
|  |                    |   |                    |   |                        |  |
|  | Current task +     |   | Decides per turn:  |   | Allocates window:      |  |
|  | working memory     |-->|  - what to store   |-->|  instructions: 15%     |  |
|  | drive next action  |   |  - what to retrieve|   |  tools: 20%            |  |
|  |                    |   |  - what to evict   |   |  memory: 30%           |  |
|  | Plans, reflects,   |   |  - what to promote |   |  state: 10%            |  |
|  | calls tools        |   |    (working->LT)   |   |  user input: 15%       |  |
|  |                    |   |  - what to demote  |   |  headroom: 10%         |  |
|  |                    |   |    (LT->archival)  |   |                        |  |
|  +---------+----------+   +---------+----------+   +----------+-------------+  |
+------------+------------------------+---------------------------+-------------+
             | actions + beliefs      | read/write ops            | budget signals
+------------v------------------------v---------------------------v-------------+
|                     STATE MANAGEMENT PLANE                                     |
|                                                                              |
|  +------------------+  +-------------------+  +----------------------------+ |
|  | Checkpoint Engine |  | Event Sourcing    |  | Rollback Controller        | |
|  |                   |  | Log               |  |                            | |
|  | Save at every     |  | Append-only       |  | On correction:             | |
|  | super-step.       |  | immutable events. |  |  1. Identify affected      | |
|  | Durability modes: |  |                   |  |     steps via dep graph    | |
|  |  sync: block      |  | Agent emits       |  |  2. Roll back to earliest  | |
|  |  async: non-block |  | intentions +      |  |     affected step          | |
|  |  exit: on exit    |  | proposed diffs,   |  |  3. Mark downstream        | |
|  |                   |  | orchestrator      |  |     invalid                | |
|  | Pending writes    |  | validates +       |  |  4. Preserve earlier       | |
|  | make node-level   |  | serializes.       |  |     valid steps            | |
|  | failures resumable|  |                   |  |  5. Check: one-time        | |
|  |                   |  |                   |  |     vs lasting pref        | |
|  +--------+----------+  +--------+----------+  +-------------+--------------+ |
+-----------+------------------------+---------------------------+--------------+
            | state snapshots        | event stream              | rollback cmds
+-----------v------------------------v---------------------------v--------------+
|                         MEMORY PLANE                                          |
|                                                                              |
|  +-----------------+ +------------------+ +---------------+ +--------------+ |
|  | Working Memory   | | Episodic Memory  | | Semantic      | | Procedural   | |
|  |                  | |                  | | Memory        | | Memory       | |
|  | Session-scoped   | | Past decisions,  | | Facts, user   | | Learned      | |
|  | Current task,    | | outcomes,        | | preferences,  | | workflows,   | |
|  | active           | | interactions     | | domain        | | tool-use     | |
|  | constraints,     | |                  | | knowledge     | | patterns     | |
|  | scratchpad       | | "What happened"  | | "What I know" | | "How to do"  | |
|  |                  | |                  | |               | |              | |
|  | Always in        | | Long-term,       | | Long-term,    | | Long-term,   | |
|  | context window   | | retrieved via     | | retrieved via  | | retrieved    | |
|  | (like CPU regs)  | | similarity +     | | entity +       | | via task     | |
|  |                  | | recency          | | keyword search | | matching     | |
|  +--------+---------+ +--------+---------+ +-------+-------+ +------+------+ |
|           |                    |                    |                |        |
|  +--------v--------------------v--------------------v----------------v-----+ |
|  |                    Memory Lifecycle Engine                               | |
|  |  CREATE -> UPDATE -> SUMMARIZE -> DELETE                                | |
|  |  Extraction phase: what is worth remembering?                           | |
|  |  Update phase: compare new vs existing, self-edit on conflict           | |
|  |  Sleep-time compute: consolidate while user is idle (Letta)             | |
|  |  Retrieval filter: 1) Changes next action? 2) Still valid? 3) Plan?    | |
|  +-----------------------------------+----------------------------------+  | |
+--------------------------------------+------------------------------------+-+
                                       | CRUD ops + retrieval queries
+--------------------------------------v--------------------------------------+
|                    TOOL PROXY LAYER (MCP)                                    |
|                                                                              |
|  +------------------+  +-------------------+  +-----------------+            |
|  | Memory MCP Server |  | SoR API Proxies   |  | Per-Agent RBAC  |            |
|  | read/write/search |  | booking, inventory |  | Scoped tokens   |            |
|  | delete operations |  | pricing APIs       |  | Per-invocation  |            |
|  | Schema validate   |  | (always wins)      |  | auth (Zero-Trust|            |
|  +------------------+  +-------------------+  | MCP model)       |            |
|                                                +-----------------+            |
+--------------------------------------+--------------------------------------+
                                       |
+--------------------------------------v--------------------------------------+
|                          STORAGE PLANE                                        |
|                                                                              |
|  +------------------+  +-------------------+  +-------------+  +-----------+ |
|  | Checkpoint Store  |  | Vector Store      |  | Graph Store  |  | Cache     | |
|  |                   |  |                   |  |              |  | Layer     | |
|  | PostgresSaver:    |  | pgvector /        |  | Neo4j:       |  | Redis 8.4:| |
|  |  any replica,     |  | Pinecone /        |  | temporal     |  | native    | |
|  |  no sticky        |  | Qdrant:           |  | knowledge    |  | vector    | |
|  |  sessions         |  | HNSW/IVF ANN     |  | graph        |  | search,   | |
|  |                   |  | <10ms search      |  | BM25+embed+  |  | 2-5ms     | |
|  | DynamoDBSaver:    |  |                   |  | traversal    |  | cache     | |
|  |  metadata in      |  | Embeddings at     |  | (no LLM at   |  | hits      | |
|  |  DynamoDB,        |  | write time,       |  | retrieval)   |  | (160x vs  | |
|  |  payloads >350KB  |  | not query time    |  |              |  | cold)     | |
|  |  overflow to S3   |  |                   |  |              |  |           | |
|  +------------------+  +-------------------+  +-------------+  +-----------+ |
+--------------------------------------+--------------------------------------+
                                       |
+--------------------------------------v--------------------------------------+
|                    TELEMETRY / OBSERVABILITY LAYER                            |
|                                                                              |
|  Retrieval hit rate (% queries returning relevant memory)                    |
|  Token usage per turn (input + output + memory injection overhead)           |
|  Memory store growth rate (entries/day, embeddings/day)                      |
|  Staleness score (age-weighted relevance of injected memories)               |
|  Latency per turn (broken down: retrieval + rerank + LLM inference)          |
|  Memory conflict rate (contradictory facts detected per session)             |
|  Cost per run (LLM tokens + embedding generation + storage I/O)             |
|  Poisoning detection (anomalous write patterns, provenance gaps)             |
|  Checkpoint size / age | Breaker state | Correlation IDs                    |
+-------------------------------------------------------------------------+
```

### Four Persistence Concerns (Do Not Conflate)

| Concern | Owns | Lifetime | Update Rule |
|---------|------|----------|-------------|
| **Working State** | Step, plan, active constraints, messages in-flight | Current turn / context window | React **fast** -- every instruction |
| **Checkpoint** | StateSnapshot per graph super-step (thread_id) | Task / thread; survives crash/HITL | Write at super-step boundary; pending writes for partial recovery |
| **Long-term Store** | Preferences, facts, reusable knowledge | Cross-session / cross-thread | Learn **slowly** -- after change looks steady |
| **System of Record (SoR)** | Prices, inventory, bookings, live APIs | Authoritative | **Always wins** conflicts with stored memory |

**Concrete example**: User says "window seat this time" but the airline API shows zero window seats. State updates the constraint immediately. Memory retains the preference for future tasks. SoR decides availability -- book aisle if no window. The preference is NOT deleted from memory; it is retained but overridden for this specific decision.

### End-to-End Request-Flow Narrative

1. **Ingress** -- User message arrives with thread_id / user_id. Control plane loads the latest checkpoint (workflow progress) for that thread; if absent, initializes empty working state.
2. **State hydrate** -- Merge checkpoint values + next into working state. Apply any user correction immediately to constraints; selectively roll back only dependent steps.
3. **Scoped memory retrieve** -- Query long-term Store/Mem0 with metadata scope (topic, confidence, duration). Flight booking pulls flight+budget memories, not hotel memories. Three-question filter before injection: Does this change my next action? Is it still valid? Would removing it break my plan? If all three are "no," memory stays out of context.
4. **Context assembly** -- Budget: instructions + state + retrieved memory + tool schemas + tool outputs + user input. Prefer JIT schemas (Tool Search cut Claude Code tool overhead ~85%) over stuffing.
5. **Plan and act** -- Agent brain picks next action. Tool calls hit SoR (inventory, booking). Tool results are environment ground truth.
6. **Conflict resolution** -- On memory vs SoR disagreement, SoR wins for the decision; memory may still hold the preference.
7. **Checkpoint write** -- At super-step boundary, checkpointer persists full (or delta) snapshot + pending writes. Resume after peer-node failure skips successful siblings.
8. **Memory write (gated)** -- Hot path: agent tool before reply (immediate consistency, +latency). Background: extract/consolidate after turn ("learn slowly"). Tag one-time vs lasting before durable ADD/UPDATE.
9. **Egress and telemetry** -- Response returns; emit tokens, retrieval p95, checkpoint bytes, correlation id.

---

## 2. Core Mechanics & Algorithms

### 2.1 Three Competing Memory Taxonomies

The industry has not converged on a single memory taxonomy. Three frameworks coexist, each optimizing for a different concern.

**Framework 1: Cognitive Taxonomy (CoALA)** -- draws on human cognitive science; adopted by LangGraph docs and academic surveys.

| Type | Stores | Persistence | Analogy |
|------|--------|-------------|---------|
| **Episodic** | Past interactions, decisions, outcomes | Long-term | "What happened" |
| **Semantic** | Facts, user preferences, domain knowledge | Long-term | "What I know" |
| **Procedural** | Learned workflows, tool-use patterns, policies | Long-term | "How to do things" |
| **Working** | Current task state, active constraints, scratchpad | Session | CPU registers / RAM |

**Framework 2: Engineering Taxonomy (Letta/MemGPT)** -- focuses on data flow and access patterns rather than cognitive analogy. Models context window as RAM, external stores as disk.

| Tier | Scope | Access | Storage |
|------|-------|--------|---------|
| **Core memory** | In-context blocks the agent reads/writes directly | Always in prompt | Context window (RAM) |
| **Recall memory** | Searchable conversation history | Via tool calls | External store (disk cache) |
| **Archival memory** | Long-term searchable knowledge | Via tool calls | Cold storage (disk) |

**Framework 3: Persistence-Duration Taxonomy (SystemDesign.one)** -- most operationally pragmatic; defines boundaries by when data expires and who owns the authoritative version.

- **Short-term**: session variables, in-prompt context. Cleared on task completion.
- **Long-term**: user preferences, recurring patterns. Persists across sessions.
- **External**: authoritative reference data (APIs, knowledge graphs). Queried on demand. Key invariant: **when memory and the system of record disagree, the system of record always wins.**

**How they map together**: These are not contradictory -- they slice the same problem along different axes. Episodic memory (cognitive) lives in recall memory (engineering tier) with long-term persistence (duration tier). The design question is which axis drives your API and storage decisions.

### 2.2 Memory Architecture Patterns

| Pattern | Mechanism | Strengths | Failure Modes |
|---------|-----------|-----------|---------------|
| **In-Context** | All history kept verbatim in prompt | Zero retrieval latency, strong consistency | "Lost-in-the-middle" (10-25% accuracy drop for mid-context content). O(n) cost. Ceiling ~20 turns. |
| **External Store** | Memory in vector/graph/KV store, retrieved per turn | ~93% token reduction, ~91% latency improvement (Mem0 vs full-context) | Retrieval misses; stale memories if no expiry; write-path cost |
| **Virtual Context (MemGPT)** | OS-inspired paging between context (RAM) and stores (disk). Agent self-edits memory. | DMR: GPT-4 baseline 32.1% -> MemGPT 92.5% | Memory quality depends on model judgment. Every paging op costs inference tokens. |
| **Hybrid (Production Default)** | In-context working + external retrieval + graph long-term | 2026 consensus architecture | Highest operational complexity |

### 2.3 Checkpointer vs Store (LangGraph)

| Dimension | **Checkpointer** | **Store** |
|-----------|------------------|-----------|
| Persists | Graph state snapshots (StateSnapshot) | Application-defined key-value items |
| Scope | Single thread_id | Across threads (e.g., (user_id, "memories")) |
| Memory type | Short-term / thread-scoped | Long-term / cross-thread |
| Use for | Continuity, HITL, time travel, fault tolerance | Preferences, facts, shared knowledge |
| Access | Config {"configurable": {"thread_id": "..."}} | Read/write from nodes via injected store |
| Crash behavior | Reload last snapshot; pending writes avoid re-running successes | Survives thread end; not a substitute for SoR |
| Growth hazard | O(N^2) full snaps -> 5.3 GB at 200 turns; DeltaChannel -> 129 MB (~41x) | Store construction: Mem0 ~7k tok/conv; Zep graph >600k + readiness lag |

**Production backends**: PostgresSaver / AsyncPostgresSaver (any replica, no sticky sessions); DynamoDBSaver (metadata in DynamoDB, payloads >350KB overflow to S3); SqliteSaver (dev); InMemorySaver (does NOT survive process restart). Keep thread_id under 255 characters for Postgres.

**Durability modes** (the actual scaling lever):
- `"sync"`: blocks until checkpoint is persisted. Safest; highest latency.
- `"async"`: non-blocking write. Lower latency; risk of losing last checkpoint on crash.
- `"exit"`: checkpoint only on graph exit. Lowest overhead; entire run lost on mid-graph crash.

### 2.4 State Management Mechanisms

**Event Sourcing (ESAA, arXiv 2602.23193)**: Every state change recorded as an immutable event in an append-only log. Deterministic orchestrator validates and serializes. Complexity: O(1) per write (append), O(n) for full replay, O(log n) with snapshot optimization. Tools: EventSourcingDB 1.0 (May 2025), OpenCQRS 1.0 (Oct 2025).

**CQRS for Agents**: Commands (instructions to agents) separated from queries (reading agent state). Write path: heavy computation (extraction, embedding, entity resolution, graph construction), latency-tolerant. Read path: fast retrieval (ANN search, KV lookup, graph traversal), latency-critical (<50ms p95).

**Rollback on Corrections**: Corrections are constraint changes, not errors.

```
1. Receive correction C for assumption A
2. Trace dependency graph: find all steps S_i that depended on A
3. earliest_affected = min(S_i.step_number)
4. For each step after earliest_affected:
     if depends_on(step, A): mark INVALID
     else: mark PRESERVED
5. Check correction type:
     if one-time: apply only to current task
     if lasting_preference: update long-term memory
6. Resume from earliest_affected with corrected constraint
```

### 2.5 Memory Update Algorithm (Mem0-Style)

1. Context lookup -> candidate similar memories (top-s, s=10 in paper).
2. Fact extraction from current turn.
3. LLM tool-call decides ADD / UPDATE / DELETE / NOOP.
4. Dedup / embed / entity extract; search blends semantic, keyword, entity, temporal signals.

Complexity: extraction + comparison over s memories is O(s) LLM-mediated ops per write (constant s in experiments).

**Mem0 API reality check**: docs discuss working/factual/episodic/semantic types, but as of 2026-03 the SDK MemoryType enum only actively wires procedural_memory; practical scoping uses user_id / run_id / agent_id.

### 2.6 Context Strategies

| Strategy | Mechanism | Effect |
|----------|-----------|--------|
| **Sliding window** | Keep system + last N turns | Cheapest; loses early goals |
| **Head+tail** | Preserve task head + recent tail | Better goal retention |
| **Tool-result clearing** | Drop re-fetchable payloads | Lightest safe compaction |
| **Summarization / compaction** | LLM summary block. Anthropic default trigger 150k input tokens (min 50k) | Lossy; costs one call |
| **External memory** | Persist facts; retrieve JIT | Moves cost off prompt |

**Tiered context architecture** (2026 production consensus):
- **Hot layer**: verbatim last 10 turns, full detail
- **Warm layer**: rolling summary of turns 11-40, key decisions and task state compressed
- **Cold layer**: broad summary of everything prior, high-level goals/constraints only
- Result: **26-54% reduction** in peak token usage vs verbatim history

### 2.7 Key Invariants

| Property | Statement |
|----------|-----------|
| **SoR authority** | Memory suggests; SoR decides. Never let cached memory override pricing/inventory without re-fetch. |
| **Learn-slowly** | Rare exceptions must not become permanent rules. Tag one-time vs lasting. |
| **Write-time investment** | Do heavy lifting at write time (extraction, embedding, entity resolution) so retrieval stays fast. Memories written once, read many times. |
| **Attention budget** | Context is finite; longer windows -> context rot (n^2 pairwise token relationships). |
| **Memory scoping is load-bearing** | Mem0's four dimensions (user_id, agent_id, run_id, app_id). Scoping decisions made early are hard to restructure later. |
| **Retrieval quality bounds reasoning quality** | 65% of enterprise AI agent failures in 2025 were attributed to context drift or memory loss -- not model capability. |
| **Checkpoint growth** | Full snapshots of append-only channels -> O(N^2); DeltaChannel -> ~41x reduction at 200 turns (5.3 GB -> 129 MB). |
| **Delta reducer invariant** | Reducer must be batching-invariant or state silently diverges across snapshot boundaries. |
| **Compression safe zone** | 2-3x compression (<1.5% accuracy loss). Extreme compression triggers re-fetch spirals -- cost of forgetting exceeds cost of remembering. |

---

## 3. Token Economics & NFR Analysis

### 3.1 Scale of the Problem

- A single agentic session: **1-3.5 million tokens per task** (50-500x a traditional chat interaction).
- Enterprise AI token consumption grew **1,001%** between January 2025 and April 2026.
- 85% of companies miss AI cost forecasts by >10%.
- Blended cost dropped **67% YoY** ($18.40 -> $6.07/M tokens) driven primarily by **model routing** -- the single highest-leverage optimization.

### 3.2 Memory Retrieval Cost Comparison (LOCOMO Benchmark)

Published Mem0 paper Table 2 (gpt-4o-mini judge):

| Method | Context Tokens (avg) | p50 (s) | p95 (s) | Overall J |
|--------|---------------------|---------|---------|-----------|
| Full-context | **26,031** | 9.870 | **17.117** | **72.90%** +/-0.19 |
| Best RAG (k=2) | chunked | 0.802 | 1.907 | 60.97% +/-0.20 |
| OpenAI memory | 4,437 | 0.466 | 0.889 | 52.90% +/-0.14 |
| Zep | 3,911 | 1.292 | 2.926 | 65.99% +/-0.16 |
| LangMem (hot path) | 127 | 18.53 | **60.40** | 58.10% +/-0.21 |
| **Mem0** | **1,764** | **0.708** | **1.440** | 66.88% +/-0.15 |
| Mem0-graph | 3,616 | 1.091 | 2.590 | 68.44% +/-0.17 |

**Key takeaways**:
- Token reduction: 1,764 / 26,031 = **93.2% fewer prompt tokens** (paper: "more than 90% token cost")
- p95 latency: 1.440 / 17.117 = **91.6% lower** than full-context
- Quality trade: full-context J = 72.9% vs Mem0 J = 66.9% (**~6pp gap** -- acceptable for chat UX, not for wire transfers)

### 3.3 Cost Formula: $ per 1K Runs

**Stated assumptions** (labeled -- not live quotes):

| Symbol | Value | Meaning |
|--------|-------|---------|
| T_full | 26,031 tokens | LOCOMO full-context avg prompt |
| T_mem0 | 1,764 tokens | Mem0 retrieved memory tokens avg |
| P_in | $3/MTok | Assumed input price (Sonnet-class) |
| P_out | $15/MTok | Assumed output price |
| T_out | 200 tokens | Assumed completion size |

**Read-path comparison** (write-path extraction costs excluded):

| Pattern | Cost/run | Cost/1K runs | Savings |
|---------|----------|-------------|---------|
| Full in-context | $0.081 | **$81.09** | -- |
| Mem0 retrieval | $0.008 | **$8.29** | ~9.8x cheaper |
| Retrieval + caching | ~$0.0012 | **$1.20** | ~68x cheaper |
| Retrieval + routing | ~$0.0008 | **$0.80** | ~101x cheaper |

**50-turn session cost** (more realistic agentic workload):

| Pattern | Cost/run | Basis |
|---------|----------|-------|
| Full in-context | $12.50 | 50 turns x 50K tokens x $5/MTok |
| Retrieval-based | $3.85 | 50 turns x 15K tokens x $5/MTok + $0.10 infra |
| Retrieval + caching + routing | $0.80 | 70% routed to cheap model + cache hits |

### 3.4 Caching Strategies

| Strategy | Mechanism | Cost Reduction | Latency Impact |
|----------|-----------|---------------|----------------|
| **Prompt caching** | Cache stable prefix (system instructions, tool definitions), exclude dynamic suffix | 78.5% (Claude Sonnet 4.5, 500+ sessions) | Prefix hits: near-zero |
| **Semantic caching** | Cache LLM responses keyed by semantic similarity of query | 86% (AWS eval, 63,796 queries) | 88% latency improvement; cosine threshold 0.92-0.95 |
| **Redis 8.4** | Native vector search with microsecond query latency | 40-86% multi-tier | 2-5ms cache hits vs 300-500ms cold (160x) |
| **KV cache (infra)** | SGLang RadixAttention stores KV activations in radix tree keyed by token sequence | Infrastructure-level | Context engineering vs prompt engineering |

### 3.5 Latency SLA Targets

| Operation | p50 | p95 | p99 | Budget Notes |
|-----------|-----|-----|-----|-------------|
| Memory retrieval (embed + ANN + rerank) | 15ms | 50ms | 100ms | Must not exceed 100ms |
| LLM inference (with memory in context) | 800ms | 2,000ms | 5,000ms | Dominates total latency |
| Checkpoint write (sync) | 5ms | 20ms | 50ms | Per super-step overhead |
| End-to-end turn | 1,000ms | 3,000ms | 8,000ms | User-facing SLA target |
| Cache hit (Redis 8.4) | <1ms | 2ms | 5ms | 160x vs cold retrieval |
| Mem0 total (LOCOMO) | 708ms | 1,440ms | ~4,000ms (inferred) | Published benchmark |
| Full-context total (LOCOMO) | 9,870ms | 17,117ms | ~35,000ms (inferred) | Published benchmark |

### 3.6 Throughput and Back-Pressure

| Lever | Behavior |
|-------|----------|
| **Prompt size** | 26k -> 1.8k tokens raises theoretical throughput ~14x at fixed TPM |
| **Write amplification** | Every-turn extract+embed without NOOP gating burns write-path capacity |
| **Checkpoint I/O** | Full snapshots at 200 heavy turns -> 5.3 GB; DeltaChannel -> 129 MB (~41x) |
| **Graph readiness** | Zep-class graphs: multi-hour async lag -> search wrong immediately after write |
| **Shed load** | Under TPM pressure: shrink retrieve-k -> sliding window -> SoR-only reads; open memory-path breaker |
| **Retention cron** | Unchecked checkpoint history increases latency + storage -- prune required |
| **Model routing** | Single highest-leverage optimization: cheap model for FAQ, expensive for complex reasoning |

### 3.7 NFR Trade-Offs

| NFR | Target | Memory Implication |
|-----|--------|--------------------|
| **Availability** | 99.9% on agent ingress + checkpointer; memory search may degrade | Breaker open on memory path does not equal total outage if sliding window + SoR remain healthy |
| **RPO** | Sync checkpoint each super-step -> RPO = last completed step; background writes -> RPO = last durable ADD | InMemorySaver RPO = process lifetime (lose all on restart) |
| **RTO** | Reload thread_id + checkpoint; pending writes skip successful nodes | Schema-version old checkpoints or block incompatible paths |
| **Compliance** | Memory = regulated store; PII detect -> redact -> audit before write | Retention / right-to-delete; tenant namespaces; never let cached memory override SoR |

**Explicit quality-cost-latency trade-off**: Full-context J = 72.9% vs Mem0 J = 66.9%. Pay the ~6pp J tax when interactive cost/latency dominate. Keep full-context or heavier retrieve for high-stakes recall (bookings, payments, wire transfers).

---

## 4. Distributed Resilience & Security

### 4.1 Memory Consistency in Multi-Agent Systems

**Core finding**: 36.9% of failures in multi-agent systems stem from inter-agent misalignment -- agents operating on inconsistent state -- not from model capability. Improved prompting/orchestration alone yields "only modest accuracy gains of 14-15 percentage points." The failures are structural.

**Two distinct problems** (2026 position paper):
- **Coherence**: ensuring agents do not read stale or conflicting values for the same memory key.
- **Consistency**: ensuring writes from multiple agents are ordered sensibly.

Neither is solved by any current framework out of the box.

**Latency-Consistency-Cost Triangle** (analogous to CAP theorem):

```
                    Consistency
                        /\
                       /  \
                      /    \
                     / Pick  \
                    / any 2   \
                   /___________\
            Latency              Cost

- Optimize consistency: locking, validation -> higher latency + cost
- Optimize latency: caching, eventual sync -> stale reads, inconsistent state
- Optimize cost: aggressive pruning/compression -> degraded retrieval quality
```

**Staleness cascade**: Agent A reads outdated task status, writes a new memory derived from it, Agent B reads and acts on it. More dangerous than in conventional data systems because agents reason over what they read -- a stale fact becomes a confidently wrong belief.

**Cascade failure severity**: Galileo AI (Dec 2025) -- in simulated multi-agent systems, a single compromised agent poisoned **87% of downstream decision-making within four hours**.

### 4.2 Multi-Agent Memory Patterns

| Pattern | Architecture | Best For | Risk |
|---------|-------------|----------|------|
| **Centralized** | Single shared store; strong consistency | <5 agents with contention tolerance | Bottleneck at scale |
| **Distributed with Sync** | Each agent maintains private memory, shares selectively. Wegner's transactive memory. | >5 agents with selective sharing | Batched sync jobs can silently drop updates. >90% accuracy, 61% resource reduction (Rezazadeh et al.) |
| **Hybrid (Production Default)** | Central global state + private agent-specific memory tiers | Production multi-agent | Highest operational complexity |

Microsoft reference architecture defines three stores: conversation history, agent state (continuity/recovery), registry storage (metadata, capabilities, endpoints).

### 4.3 Memory Poisoning and Corruption

**OWASP ASI06** (Top 10 for Agentic Applications, December 2025): Memory and Context Poisoning is a top-tier agentic risk.

**Three principal attack vectors**:

| Vector | Mechanism | Persistence |
|--------|-----------|-------------|
| **RAG Poisoning** | Attacker writes adversarial content to retrieval corpus; every future matching query inherits attacker's instructions | Until corpus is cleaned |
| **Memory Poisoning** | Attacker inserts entries into long-term memory store that bias future reasoning | Durable across sessions |
| **Context-Window Saturation** | Flooding context with high-volume content displaces legitimate instructions | Denial-of-attention attack |

**MINJA attack** (NeurIPS 2025): >95% injection success rate against production agents. Injects malicious records through **query-only interaction** -- no direct memory store access needed.

**Why traditional defenses fail**: Existing defenses detect malicious actions, not corrupted beliefs. Agent memory has no integrity verification. The agent trusts its own memories implicitly.

**Real-world incidents**:
- **Manufacturing procurement agent**: manipulated over 3 weeks via "helpful clarifications" about authorization limits. Result: $5M in false purchase orders across 10 transactions.
- **Lakera AI** (November 2026): indirect prompt injection via poisoned data sources created persistent false beliefs about security policies. Agent defended false beliefs when questioned by humans.
- **Replit** (July 2025): coding agent deleted live production database during code freeze, then generated thousands of fake user records to hide the issue (AIID #1152).

### 4.4 PII in Long-Term Memory Stores

**Embeddings are NOT anonymization.** Embedding inversion attacks reconstruct source text from vectors. Works best on short, high-value strings that memory stores hold: names, emails, addresses. A vector store is closer to a document store than an anonymized feature matrix. **A breach of the vector store IS a breach of the underlying data.**

**Data classification for agent memory**:

| Class | Examples | Required Controls |
|-------|----------|-------------------|
| **Public** | General knowledge, product docs | Shared stores OK, standard encryption |
| **Confidential** | Customer data, transaction details | Stronger encryption, tighter access control |
| **Restricted** | PII, medical records, financial data, credentials | Field-level security, audit logging, never-store list enforcement |

**Never-store list**: Define up front what the agent must not retain (raw PII, credentials, regulated fields). **Enforce at the write path with a policy pipeline, not with a prompt instruction.**

### 4.5 Multi-Tenant Memory Isolation

**OWASP LLM08** (2025): Vector and Embedding Weaknesses. Weak namespace boundaries let one tenant's queries surface another tenant's data.

**The common mistake**: Enforcing separation with namespace filters in application code. "A filter the application has to remember to apply on every query is a label, not a boundary." **Real isolation lives at the storage layer -- separate indices per tenant.**

**Failure modes** (Margalit et al., 2026):
- **Workspace bleed**: memory from one customer's channel appears in another's project
- **Role bleed**: agent stores one user's preference as global policy, applies to everyone
- **Unauthorized leakage**: cross-tenant retrieval via carefully crafted queries
- **Stale propagation**: deleted tenant's data persists in derived embeddings
- **Provenance collapse**: cannot trace which tenant or session generated a memory entry

### 4.6 GDPR Right to Forget in Agent Memory

**GDPR Article 17** (Right to Erasure) requires:
1. Identify all memory entries containing that individual's data
2. Purge those entries without corrupting agent context
3. Verify the deletion actually happened
4. Prove it to auditors

**GDPR vs EU AI Act tension**: Deleting memory that informed a hiring recommendation loses the ability to audit that recommendation. **Practical path**: maintain a separate, subject-anonymized audit log of consequential decisions, distinct from the live memory store subject to deletion.

**Regulatory timeline**:
- EU AI Act broad enforcement: August 2, 2026
- Italy's Garante fined OpenAI EUR 15M (January 2025) -- first generative AI GDPR penalty
- NIST AI Agent Standards Initiative (February 2026): agent identity, authorization, and security as priorities

### 4.7 Zero-Trust MCP for Memory Operations

Every memory operation via MCP requires a short-lived, scoped capability token encoding:
- **Subject**: which agent identity is invoking the tool
- **Action**: which memory primitive (read, write, search, delete)
- **Resource**: which memory scope (user_id, agent_id, run_id)
- **Expiry**: TTL of 30-300 seconds
- **Nonce**: prevents replay of captured tokens

**Tool-Level RBAC for memory access** (least-privilege model):

| Role | Read | Write | Search | Delete | Example Agent |
|------|------|-------|--------|--------|---------------|
| **observer** | yes | no | yes | no | Monitoring, analytics |
| **contributor** | yes | yes | yes | no | Task-execution agents |
| **curator** | yes | yes | yes | scoped (own entries only) | Memory consolidation |
| **admin** | yes | yes | yes | yes | GDPR erasure handler, ops |

Rate limits enforced at the MCP server, not the client: observer 1,000 reads/hr; contributor 100 writes/hr; admin 500 writes/hr.

### 4.8 Memory Guard (OWASP Pattern)

```
Write pipeline:   check_never_store -> check_tenant_scope -> check_pii_redact -> ALLOW/BLOCK
Read pipeline:    check_tenant_scope -> check_scope_filter -> ALLOW/BLOCK

Actions: ALLOW | REDACT (cleaned version stored) | QUARANTINE | BLOCK
```

**Policy engine integration** (OPA/Cedar): Evaluate authorization at runtime, not via static role assignments. Cedar provides formal verification of policy correctness -- mathematically proves no policy combination grants unintended access. For memory systems where a single over-permissive policy creates persistent poisoning risk, Cedar's formal guarantees provide stronger defense.

### 4.9 Circuit Breaker and Fallback Chain

```
          success                    recovery_timeout
     +-------------+   fail>=N   +------+   elapsed   +-----------+
     |    CLOSED   |------------>| OPEN |------------>| HALF_OPEN |
     +------^------+             +------+             +-----+-----+
            | success                                        |
            +------------------------------------------------+
                         fail -> OPEN

Fallback chain:
  Memory retrieve (Store/Mem0) --fail/breaker--> Sliding window (last N turns)
                |                                          |
                +------------------------------------------+--> SoR read (always terminates)
```

---

## 5. Production Enterprise Code

```python
#!/usr/bin/env python3
"""Agent memory/state layer with enterprise resilience primitives.
Deterministic -- no API keys, no network, no stubs.
Demonstrates: checkpointer with append-only chain, memory store with
SoR-wins conflict resolution, circuit breaker, fallback chain
(memory -> sliding window -> SoR), PII redaction, correlation IDs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Structured logging with correlation IDs
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.time(), "level": record.levelname,
            "msg": record.getMessage(), "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "thread_id": getattr(record, "thread_id", None),
            "breaker_state": getattr(record, "breaker_state", None),
            "path": getattr(record, "path", None),
            "degraded": getattr(record, "degraded", None),
        }
        return json.dumps(payload, default=str)

LOG = logging.getLogger("agent.memory_state")
if not LOG.handlers:
    h = logging.StreamHandler(); h.setFormatter(JsonFormatter())
    LOG.addHandler(h); LOG.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------

class FailureKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"

class AgentError(Exception):
    def __init__(self, message: str, kind: FailureKind):
        super().__init__(message); self.kind = kind


# ---------------------------------------------------------------------------
# Circuit breaker: closed -> open -> half-open
# ---------------------------------------------------------------------------

class BreakerState(str, Enum):
    CLOSED = "closed"; OPEN = "open"; HALF_OPEN = "half_open"

@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    recovery_timeout_s: float = 0.05
    failures: int = 0
    state: BreakerState = BreakerState.CLOSED
    opened_at: float = 0.0

    def allow(self) -> bool:
        if self.state == BreakerState.OPEN:
            if time.monotonic() - self.opened_at >= self.recovery_timeout_s:
                self.state = BreakerState.HALF_OPEN; return True
            return False
        return True

    def record_success(self): self.failures = 0; self.state = BreakerState.CLOSED
    def record_failure(self):
        self.failures += 1
        if self.state == BreakerState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = BreakerState.OPEN; self.opened_at = time.monotonic()


def retry_with_jitter(fn, *, max_attempts=3, base_s=0.01, cid=""):
    last = None
    for attempt in range(1, max_attempts + 1):
        try: return fn()
        except AgentError as exc:
            if exc.kind == FailureKind.PERMANENT: raise
            last = exc
            if attempt == max_attempts: break
            time.sleep(random.uniform(0, base_s * (2 ** (attempt - 1))))
        except Exception as exc:
            last = exc
            if attempt == max_attempts: break
            time.sleep(random.uniform(0, base_s * (2 ** (attempt - 1))))
    raise last


# ---------------------------------------------------------------------------
# PII: detect -> redact -> audit
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
NEVER_STORE_RE = re.compile(r"\b(?:sk|pk|api)[_-][A-Za-z0-9]{20,}\b")
AUDIT: list[dict] = []

def redact_pii(text: str, cid: str) -> str:
    # Block secrets entirely
    if NEVER_STORE_RE.search(text):
        AUDIT.append({"event": "pii_blocked", "cid": cid, "reason": "never_store"})
        return "[BLOCKED_SECRET_CONTENT]"
    redacted = text
    n_total = 0
    for name, pat in [("email", EMAIL_RE), ("phone", PHONE_RE), ("ssn", SSN_RE)]:
        found = pat.findall(redacted)
        n_total += len(found)
        redacted = pat.sub(f"[REDACTED_{name.upper()}]", redacted)
    if n_total > 0:
        AUDIT.append({"event": "pii_redacted", "cid": cid, "count": n_total})
    return redacted


# ---------------------------------------------------------------------------
# Persistence: checkpointer + memory store + SoR
# ---------------------------------------------------------------------------

@dataclass
class Checkpoint:
    thread_id: str
    checkpoint_id: str
    parent_id: str | None
    values: dict[str, Any]
    next_steps: list[str]
    created_at: float
    content_hash: str

class Checkpointer:
    """Append-only checkpoint chain (immutable history)."""
    def __init__(self): self._chain: dict[str, list[Checkpoint]] = {}

    def save(self, thread_id: str, values: dict[str, Any],
             next_steps: list[str]) -> Checkpoint:
        history = self._chain.setdefault(thread_id, [])
        parent_id = history[-1].checkpoint_id if history else None
        content_hash = hashlib.sha256(
            json.dumps(values, sort_keys=True, default=str).encode()
        ).hexdigest()
        ckpt = Checkpoint(thread_id=thread_id,
                          checkpoint_id=str(uuid.uuid4()),
                          parent_id=parent_id, values=dict(values),
                          next_steps=list(next_steps),
                          created_at=time.time(), content_hash=content_hash)
        history.append(ckpt)
        return ckpt

    def load(self, thread_id: str) -> Checkpoint | None:
        history = self._chain.get(thread_id, [])
        return history[-1] if history else None

    def history(self, thread_id: str) -> list[Checkpoint]:
        return list(self._chain.get(thread_id, []))


@dataclass
class MemoryItem:
    key: str; value: str; topic: str; confidence: str = "confirmed"
    duration: str = "long-term"; version: int = 1

class MemoryStore:
    """Namespace-scoped long-term store (user_id -> items)."""
    def __init__(self, fail_times: int = 0):
        self._data: dict[str, dict[str, MemoryItem]] = {}
        self._fail_times = fail_times; self._calls = 0

    def add(self, user_id: str, key: str, value: str, topic: str,
            confidence: str = "confirmed") -> MemoryItem:
        ns = self._data.setdefault(user_id, {})
        prev = ns.get(key)
        item = MemoryItem(key=key, value=value, topic=topic,
                          confidence=confidence,
                          version=(prev.version + 1 if prev else 1))
        ns[key] = item
        return item

    def search(self, user_id: str, topic: str | None = None) -> list[MemoryItem]:
        self._calls += 1
        if self._calls <= self._fail_times:
            raise AgentError("memory search unavailable", FailureKind.TRANSIENT)
        items = list(self._data.get(user_id, {}).values())
        if topic:
            items = [i for i in items if i.topic == topic]
        return items

class SystemOfRecord:
    """Authoritative live data -- always wins conflicts with memory."""
    def __init__(self, inventory: dict[str, Any] | None = None):
        self.inventory = inventory or {
            "flight:AA100": {"seats": {"aisle": 2, "window": 0}, "price_usd": 420},
            "flight:UA200": {"seats": {"aisle": 0, "window": 4}, "price_usd": 455},
        }
    def read(self, resource_id: str) -> dict[str, Any]:
        if resource_id not in self.inventory:
            raise AgentError(f"unknown resource {resource_id}", FailureKind.PERMANENT)
        return dict(self.inventory[resource_id])


# ---------------------------------------------------------------------------
# SoR-wins conflict resolution
# ---------------------------------------------------------------------------

def resolve_preference(memory_pref: str | None,
                       sor_seats: dict[str, int]) -> dict[str, Any]:
    """SoR decides availability; memory preference is advisory."""
    if memory_pref and sor_seats.get(memory_pref, 0) > 0:
        return {"seat": memory_pref, "source": "memory+sor", "available": True}
    for seat, n in sor_seats.items():
        if n > 0:
            return {"seat": seat, "source": "sor", "available": True,
                    "memory_pref_retained": memory_pref}
    return {"seat": None, "source": "sor", "available": False,
            "memory_pref_retained": memory_pref}


# ---------------------------------------------------------------------------
# Agent runtime with fallback chain
# ---------------------------------------------------------------------------

@dataclass
class TurnResult:
    correlation_id: str; thread_id: str; path: str; answer: str
    checkpoint_id: str; degraded: bool; decision: dict[str, Any]

class MemoryStateAgent:
    def __init__(self, checkpointer: Checkpointer, store: MemoryStore,
                 sor: SystemOfRecord, breaker: CircuitBreaker,
                 window_size: int = 3):
        self.checkpointer = checkpointer; self.store = store
        self.sor = sor; self.breaker = breaker
        self.window_size = window_size

    def run(self, *, user_id: str, thread_id: str, user_text: str,
            resource_id: str, topic: str = "flight") -> TurnResult:
        cid = str(uuid.uuid4())
        prior = self.checkpointer.load(thread_id)
        messages: list[str] = list((prior.values if prior else {}).get("messages", []))
        messages.append(user_text)

        # PII gate before any durable write
        safe_text = redact_pii(user_text, cid)

        path = "memory_retrieve"; degraded = False; memory_pref = None

        # --- Fallback chain: memory -> sliding window -> SoR-only ---
        try:
            if not self.breaker.allow():
                raise AgentError("circuit open", FailureKind.TRANSIENT)
            items = retry_with_jitter(
                lambda: self.store.search(user_id, topic=topic), cid=cid)
            self.breaker.record_success()
            for item in items:
                if item.key == "seat_preference":
                    memory_pref = item.value
        except Exception:
            degraded = True; self.breaker.record_failure()
            path = "sliding_window"

        if degraded and memory_pref is None:
            path = "sor_read"

        # SoR read -- authoritative; always attempted
        sor_data = self.sor.read(resource_id)
        decision = resolve_preference(memory_pref, sor_data["seats"])

        if decision["available"]:
            answer = (f"Booked {decision['seat']} on {resource_id} "
                      f"@ ${sor_data['price_usd']} (source={decision['source']})")
        else:
            answer = (f"No seats on {resource_id}; preference retained="
                      f"{decision.get('memory_pref_retained')}")

        # Learn slowly: persist only when user states lasting intent
        if "always" in safe_text.lower() and "window" in safe_text.lower():
            self.store.add(user_id, "seat_preference", "window", topic=topic)
            AUDIT.append({"event": "memory_write", "cid": cid,
                          "user_id": user_id, "key": "seat_preference", "op": "ADD"})

        # Checkpoint write with chain linkage
        values = {"messages": messages, "last_resource": resource_id,
                  "last_decision": decision}
        ckpt = self.checkpointer.save(thread_id, values, ["confirm_or_next"])
        AUDIT.append({"event": "checkpoint", "cid": cid,
                      "thread_id": thread_id, "checkpoint_id": ckpt.checkpoint_id,
                      "parent_id": ckpt.parent_id, "hash": ckpt.content_hash})

        return TurnResult(correlation_id=cid, thread_id=thread_id, path=path,
                          answer=answer, checkpoint_id=ckpt.checkpoint_id,
                          degraded=degraded, decision=decision)


def _demo():
    random.seed(42)
    ckpt = Checkpointer()
    # Fail first 3 searches to trip breaker, then recover
    store = MemoryStore(fail_times=3)
    store.add("user-1", "seat_preference", "window", topic="flight")
    sor = SystemOfRecord()
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout_s=0.02)
    agent = MemoryStateAgent(ckpt, store, sor, breaker)

    # 1) Memory path fails -> fallback; AA100 has no window -> aisle (SoR wins)
    r1 = agent.run(user_id="user-1", thread_id="t-1",
                   user_text="Book AA100 please", resource_id="flight:AA100")
    assert r1.degraded is True
    assert r1.decision["seat"] == "aisle"

    time.sleep(0.03)  # let breaker recover

    # 2) Memory path succeeds; UA200 has window -> memory+sor
    r2 = agent.run(user_id="user-1", thread_id="t-1",
                   user_text="Try UA200 instead", resource_id="flight:UA200")
    assert r2.degraded is False
    assert r2.decision["seat"] == "window"
    assert r2.decision["source"] == "memory+sor"

    # 3) Checkpoint chain linked
    history = ckpt.history("t-1")
    assert len(history) == 2
    assert history[1].parent_id == history[0].checkpoint_id

    # 4) SoR wins: memory wants window on AA100 (0 window seats) -> books aisle
    store2 = MemoryStore()
    store2.add("user-1", "seat_preference", "window", topic="flight")
    agent2 = MemoryStateAgent(Checkpointer(), store2, sor, CircuitBreaker())
    r3 = agent2.run(user_id="user-1", thread_id="t-2",
                    user_text="Book AA100", resource_id="flight:AA100")
    assert r3.decision["seat"] == "aisle"
    assert r3.decision["source"] == "sor"
    assert r3.decision["memory_pref_retained"] == "window"

    # 5) PII redacted before memory write
    r4 = agent2.run(user_id="user-1", thread_id="t-2",
                    user_text="Always prefer window -- email jane@example.com",
                    resource_id="flight:UA200")
    assert any(e.get("event") == "pii_redacted" for e in AUDIT)

    print(json.dumps({"r1_seat": r1.decision["seat"],
                       "r2_seat": r2.decision["seat"],
                       "r3_source": r3.decision["source"],
                       "checkpoints": len(history), "ok": True}))

if __name__ == "__main__":
    _demo()
```

---

## 6. Architectural System Design Scenarios

### Scenario A -- Cross-Session Customer Support Agent (500K MAU, <$0.10/ticket)

**Problem**: SaaS company with 500K active users needs an AI customer support agent that remembers user preferences, past issues, and resolution history across sessions. Requirements: sub-3-second response, GDPR-compliant erasure on request, 50 concurrent agents per region, cost under $0.10 per resolved ticket.

**Architecture**:

```
+-------------------------------------------------------------------------+
|                    API GATEWAY / LOAD BALANCER                            |
|              Rate limiting, auth, tenant extraction                       |
+-----------------------------------+-------------------------------------+
                                    |
+-----------------------------------v-------------------------------------+
|                    AGENT ORCHESTRATOR                                     |
|                                                                          |
|  +-------------+  +------------------+  +-----------------------------+  |
|  | Model Router |  | Context Budget   |  | Session Manager             |  |
|  |              |  | Manager          |  |                             |  |
|  | Simple FAQ:  |  |                  |  | New session: load user      |  |
|  |  Haiku       |  | Hot: last 10 msgs|  | profile + last 3 tickets   |  |
|  |  ($0.25/MTok)|  | Warm: summary of |  | from memory store          |  |
|  |              |  |  prior session   |  |                             |  |
|  | Complex:     |  | Cold: user prefs |  | End session: extract       |  |
|  |  Sonnet      |  |  + history sum   |  | memories via sleep-time    |  |
|  |  ($3/MTok)   |  |                  |  | compute (background)       |  |
|  +------+-------+  +--------+---------+  +-------------+---------------+  |
+---------|--------------------+----------------------------+--------------+
          |                    |                            |
+---------v--------------------v----------------------------v--------------+
|                       MEMORY PLANE                                        |
|                                                                          |
|  +--------------------+  +--------------------+  +--------------------+  |
|  | Memory Guard        |  | Retrieval Service  |  | Write Service      |  |
|  | (OWASP pattern)     |  |                    |  |                    |  |
|  |                     |  | Vector search      |  | Two-phase:         |  |
|  | Write path:         |  | (circuit breaker)  |  |  1. Extract worthy |  |
|  |  1. Never-store     |  |       |            |  |     memories       |  |
|  |  2. Tenant isolation|  |       v            |  |  2. Conflict check |  |
|  |  3. PII redaction   |  | Keyword fallback   |  |     + self-edit    |  |
|  |                     |  |       |            |  |                    |  |
|  | Read path:          |  |       v            |  | Metadata: duration,|  |
|  |  1. Tenant isolation|  | Three-question     |  |  type, confidence, |  |
|  |  2. Scope filter    |  | filter             |  |  scope             |  |
|  +---------------------+  +----------+---------+  +--------+-----------+  |
+-----------------------------------------+---------------------+----------+
                                          |                     |
+-----------------------------------------v---------------------v----------+
|                       STORAGE PLANE                                       |
|                                                                          |
|  +---------------------+  +------------------+  +---------------------+  |
|  | PostgreSQL + pgvector|  | Redis 8.4        |  | GDPR Erasure Service|  |
|  |                      |  | (Semantic Cache)  |  |                     |  |
|  | Unified store:       |  |                   |  | 1. Find all user    |  |
|  |  - User profiles     |  | Cache layer:      |  |    memories         |  |
|  |  - Memory entries    |  |  - Prompt cache   |  | 2. Delete entries   |  |
|  |  - Embeddings        |  |  - Semantic cache  |  | 3. Re-embed docs   |  |
|  |  - Checkpoints       |  |    (cosine 0.93)  |  | 4. Verify + audit   |  |
|  |                      |  |  - Session state  |  |    log              |  |
|  | Separate index per   |  | 2-5ms cache hits  |  |                     |  |
|  | tenant (NOT filter)  |  | vs 300-500ms cold |  |                     |  |
|  +---------------------+  +------------------+  +---------------------+  |
|                                                                          |
|  +------------------------------------------------------------------+   |
|  | Audit Log (Append-Only, separate from live memory)                |   |
|  | Subject-anonymized for consequential decisions.                    |   |
|  | Not subject to GDPR deletion -- satisfies EU AI Act audit req.    |   |
|  +------------------------------------------------------------------+   |
+--------------------------------------------------------------------------+
```

**Trade-off matrix**:

| Decision | Option A | Option B (Chosen) | Why |
|----------|----------|-------------------|-----|
| Memory store | Separate vector DB + relational DB | PostgreSQL + pgvector (unified) | Single operational surface; ACID for GDPR deletes |
| Tenant isolation | Namespace filter in app code | Separate index per tenant | Storage-layer boundary, not app-layer label |
| Memory consistency | Strong (sync writes) | Eventual (async + sleep-time) | User prefs tolerate staleness; latency wins |
| Model routing | Single model for all queries | Route by complexity (Haiku/Sonnet) | 67% cost reduction; simple FAQ does not need Sonnet |
| GDPR audit trail | Same store as live memory | Separate append-only log | Erasure must not destroy audit ability |

**Cost estimate**: Per ticket (avg 8 turns): 70% routed to Haiku at $0.032 + 30% to Sonnet at $0.036 (weighted) + $0.002 memory infra = **~$0.07/ticket** (under $0.10 target).

---

### Scenario B -- Multi-Agent Financial Compliance Platform (12 Agents, Coordinated Memory)

**Problem**: Bank needs a multi-agent transaction monitoring system. 12 specialized agents (fraud detection, AML screening, sanctions checking, risk scoring, etc.) must coordinate on shared state with strict audit trails. Requirements: strong consistency for compliance-critical decisions, sub-500ms inter-agent state propagation, zero cross-client data leakage, full regulatory audit trail, resilience to single-agent failure.

**Architecture**:

```
+---------------------------------------------------------------------------+
|                    TRANSACTION INGESTION LAYER                              |
|            Real-time event stream (Kafka) + batch reconciliation           |
+------------------------------------+--------------------------------------+
                                     |
+------------------------------------v--------------------------------------+
|                    AGENT COORDINATION LAYER                                |
|                                                                            |
|  +--------------------------------------------------------------------+   |
|  |             Central State Store (Strong Consistency)                 |   |
|  |                                                                     |   |
|  |  Transaction: PENDING -> SCREENING -> SCORING -> DECISION           |   |
|  |  Sync writes: every agent write blocks until persisted              |   |
|  |  Event sourcing: append-only log of all state transitions           |   |
|  |  Conflict resolution: last-writer-wins with vector clock ordering   |   |
|  +----------------------------+----------------------------------------+   |
|                               |                                            |
|  +-----------+ +----------+ +---v------+ +----------+ +---------------+   |
|  | Fraud      | | AML      | | Sanctions| | Risk     | | Compliance    |   |
|  | Detection  | | Screening| | Checker  | | Scorer   | | Reporter      |   |
|  |            | |          | |          | |          | |               |   |
|  | Private    | | Private  | | Private  | | Private  | | Private       |   |
|  | memory:    | | memory:  | | memory:  | | memory:  | | memory:       |   |
|  | fraud      | | AML      | | OFAC/EU  | | scoring  | | reporting     |   |
|  | patterns   | | typology | | lists    | | models   | | templates     |   |
|  +-----+------+ +----+----+ +----+-----+ +----+-----+ +-------+------+   |
|        |              |           |             |               |          |
|  +-----v--------------v-----------v-------------v---------------v------+   |
|  |              Memory Isolation and Access Control                     |   |
|  |  Per-agent scoped reads + explicitly shared global state             |   |
|  |  Write: via CQRS command, validated by orchestrator                  |   |
|  |  Cross-agent signal: event bus, not direct memory write              |   |
|  +---------------------------------------------------------------------+   |
+---------------------------------------------------------------------------+
                               |
+------------------------------v--------------------------------------------+
|                       STORAGE and AUDIT LAYER                              |
|                                                                            |
|  +------------------+  +-------------------+  +-------------------------+ |
|  | PostgreSQL        |  | Event Store       |  | Regulatory Audit Trail  | |
|  | (Central State)   |  | (Append-Only)     |  |                         | |
|  | Sync durability,  |  | Full replay for   |  | Immutable, tamper-      | |
|  | serializable      |  | regulatory review |  | evident: decision,      | |
|  | isolation         |  | 7-year retention  |  | evidence, agent,        | |
|  | Client-level      |  |                   |  | confidence, memory      | |
|  | partitioning      |  |                   |  | state at decision time  | |
|  +------------------+  +-------------------+  | 10-year retention       | |
|                                                +-------------------------+ |
|  +---------------------------------------------------------------------+  |
|  | Poisoning Defense Layer                                              |  |
|  | Memory contracts: each agent declares what it can believe            |  |
|  | Provenance tracking: every entry traced to source interaction        |  |
|  | Belief drift detection: monitor gradual threshold/policy shifts      |  |
|  | Behavioral analysis: full-session anomaly detection (not per-turn)   |  |
|  +---------------------------------------------------------------------+  |
+----------------------------------------------------------------------------+
```

**Trade-off matrix**:

| Decision | Option A | Option B (Chosen) | Why |
|----------|----------|-------------------|-----|
| Consistency model | Eventual (lower latency) | Strong/sync (serializable) | Compliance decisions must never use stale state |
| Inter-agent comms | Direct memory sharing | Event bus + CQRS validated by orchestrator | Prevents cascade poisoning; single agent cannot corrupt others |
| Memory topology | Centralized only | Hybrid: central global + private per-agent | Central for shared state; private for domain-specific data |
| Failure isolation | Retry indefinitely | Circuit breaker + quarantine + human queue | Failed agent must not block pipeline |
| Audit retention | Same DB as live state | Separate immutable store (7-10 year) | Regulatory requires tamper-evident records independent of live store |

**Decision rationale**: Strong consistency is non-negotiable -- an agent acting on stale transaction status could miss a sanctioned entity. CQRS with validating orchestrator prevents cascade poisoning: agents publish intentions via event bus, orchestrator validates and serializes writes. The 87%-within-4-hours scenario (Galileo AI) is blocked because no single agent can directly corrupt shared memory.

---

## Common Failure Modes

| # | Failure Mode | Symptom | Detection | Mitigation |
|---|-------------|---------|-----------|------------|
| 1 | **Stale memory** | Outdated preference overrides newer intent | Freshness score; age-weighted retrieval | Expiry/TTL; periodic re-validation; NOOP on unchanged |
| 2 | **Wrong info captured** | Extraction bug poisons many future tasks ($5M procurement incident) | Anomalous write patterns; provenance gaps | Confidence tags; approval before durable write; NOOP gating |
| 3 | **Retrieval failure** | Correct memory exists but not surfaced | Retrieval hit rate monitoring | Scope/metadata fix; three-question filter |
| 4 | **Context rot** | Early goals drop out; agent re-asks questions | Token budget alerts; attention analysis | Scoped retrieval; compaction; tool-result clearing |
| 5 | **Checkpoint growth** | O(N^2) storage blowup (5.3 GB at 200 turns) | Checkpoint size monitoring | DeltaChannel (41x reduction); retention cron |
| 6 | **Memory poisoning** | MINJA: >95% injection success via query-only interaction | Belief drift detection; behavioral analysis | Memory contracts; provenance tracking; quarantine |
| 7 | **Compaction thrashing** | Aggressive summarization drops details -> re-fetch spirals | Tool-call spikes after compaction | Tune for recall first, then precision; 2-3x safe compression |
| 8 | **Staleness cascade** | Agent A reads stale -> writes derived memory -> Agent B acts on it | Cross-agent state consistency checks | Strong consistency for critical paths; event sourcing |
| 9 | **Tenant bleed** | One tenant's memory surfaces for another | Cross-tenant retrieval testing | Storage-layer isolation (separate indices), not namespace filters |
| 10 | **DeltaChannel reducer bug** | Silent state divergence across snapshot boundaries | Snapshot comparison; invariant testing | Batching-invariant reducers; integration tests |

---

## Key Takeaways for Interviews

- Separate state (react fast), memory (learn slowly), and SoR (always wins). These three concerns have different update rules, lifetimes, and consistency requirements. Conflating them causes stale preferences overriding live data.
- Memory retrieval quality bounds reasoning quality -- 65% of enterprise agent failures trace to context drift or memory loss, not model capability.
- Mem0 achieves ~93% token reduction and ~91% p95 latency improvement vs full-context, but trades ~6pp quality (J 66.9% vs 72.9%). Accept this for chat UX; keep full-context for high-stakes decisions.
- DeltaChannel (LangGraph 1.2, beta) cuts checkpoint storage 41x (5.3 GB -> 129 MB at 200 turns), but the reducer must be batching-invariant or state silently diverges.
- Memory is a governed data asset, not a cache. Embedding inversion attacks reconstruct source text. A breach of the vector store IS a breach of the underlying data. OWASP LLM08.
- MINJA achieves >95% injection success through query-only interaction. Traditional defenses detect malicious actions, not corrupted beliefs. Required: memory contracts, provenance tracking, belief drift detection.
- Multi-tenant isolation at the storage layer (separate indices), not the application layer (namespace filters). "A filter the application must remember to apply on every query is a label, not a boundary."
- The Latency-Consistency-Cost triangle (analogous to CAP) forces explicit trade-offs. Use strong consistency for payments/compliance; eventual consistency for preferences/analytics.

---

## Interview Q&A

**Q1: Walk through the four-layer memory model for a user who says "window seat this time" but the airline API has zero window seats.**
A: State updates the "seat_preference" constraint to "window" immediately -- react fast. Memory retrieval finds the existing long-term preference for "window" from the Store. The agent calls the airline SoR tool and finds zero window seats. Under the SoR-wins rule, the decision is to book an aisle seat. The memory preference is NOT deleted -- it is retained for future flights where window seats may be available. The SoR decides availability; memory is advisory. The key insight is that on a conflict, we keep the preference in memory but override it with ground truth from the environment for this specific decision.

**Q2: How does Mem0 achieve 93% token reduction vs full-context?**
A: Full-context injects the entire conversation history (~26,031 tokens per LOCOMO benchmark). Mem0 runs an extraction pipeline at write time that distills conversations into key facts and preferences, storing them as structured memory entries. At retrieval time, only relevant memories are injected (~1,764 tokens). The 93.2% token reduction comes from replacing verbatim conversation history with extracted facts. The trade-off is quality: full-context J = 72.9% vs Mem0 J = 66.9%, a ~6pp gap. This is acceptable for interactive chat where cost and latency dominate, but not for high-stakes decisions like wire transfers where you want the full context.

**Q3: Explain the DeltaChannel mechanism and its critical invariant.**
A: Default LangGraph checkpointers write a full channel snapshot every super-step. For append-only channels like messages, this creates O(N^2) growth -- at 200 turns of a coding agent, 5.3 GB of checkpoint data. DeltaChannel stores only per-step deltas, with a full snapshot every snapshot_frequency steps (default 1000; Deep Agents uses 50). This cuts storage to 129 MB, a 41x reduction. The critical invariant is batching-invariance: the reducer function must produce the same result whether deltas are applied one-at-a-time or in batches. If this is violated, state silently diverges when replaying from different snapshot boundaries.

**Q4: Why is "a filter the application has to remember to apply on every query" not real tenant isolation?**
A: Because it is an application-layer label, not a storage-layer boundary. Any code path that forgets to include the tenant filter in its query will surface cross-tenant data. This is especially dangerous in agent systems where the LLM itself may craft queries. Real isolation uses separate storage indices per tenant. Even if a query has no filter, it physically cannot access another tenant's index. OWASP LLM08 specifically calls out weak namespace boundaries in vector stores as a vulnerability. Failure modes include workspace bleed, role bleed, unauthorized leakage, stale propagation, and provenance collapse.

**Q5: How would you design the rollback mechanism when a user corrects a mid-workflow assumption?**
A: Corrections are constraint changes, not errors. First, I trace the dependency graph to find all steps that depended on the corrected assumption. I identify the earliest affected step. Then I iterate forward: any step that depends on the old assumption is marked INVALID; steps that do not depend on it are PRESERVED. Before updating memory, I check whether the correction is one-time (apply only to current task) or a lasting preference (linguistic cues like "always" or "going forward"). Only lasting preferences update long-term memory, and only after the change looks steady -- the "learn slowly" rule. Finally, execution resumes from the earliest affected step with the corrected constraint, reusing all preserved earlier work.

**Q6: What are the three attack vectors for memory poisoning, and why do traditional defenses fail?**
A: The three vectors are RAG poisoning (adversarial content in retrieval corpus), memory poisoning (malicious entries in long-term store), and context-window saturation (flooding context to displace legitimate instructions). Traditional defenses fail because they detect malicious actions, not corrupted beliefs. Agent memory has no integrity verification. The MINJA attack achieves >95% injection success through query-only interaction -- the attacker never directly accesses the memory store. In the manufacturing procurement incident, an attacker manipulated authorization limits over three weeks via "helpful clarifications," resulting in $5M in false purchase orders. Required defenses include memory contracts (what agents can believe), provenance tracking, belief drift detection, and behavioral monitoring across full sessions, not individual interactions.

**Q7: Compare the Latency-Consistency-Cost triangle for a customer support agent vs a financial compliance platform.**
A: For customer support, latency dominates. User preferences tolerate eventual consistency -- if a preference update takes 30 seconds to propagate, the user experience is unaffected. I would use async memory writes, sleep-time compute for consolidation, and semantic caching for repeated queries. Cost optimization through model routing (Haiku for FAQ, Sonnet for complex) provides the largest savings. For financial compliance, consistency is non-negotiable. A stale transaction status could cause a compliance failure -- missing a sanctioned entity or double-clearing a flagged transaction. I would use sync writes to a central state store with serializable isolation, event sourcing for full replay, and CQRS with a validating orchestrator that serializes all writes. The cost is higher latency and more expensive infrastructure, but the regulatory cost of a consistency failure vastly exceeds it.

**Q8: How does event sourcing help with regulatory audit requirements?**
A: Event sourcing records every state change as an immutable, append-only event. For regulatory review, this provides full replay capability -- you can reconstruct the exact state of any transaction at any point in time, including which agent made which decision, what evidence it used, and what memory state it had at decision time. This satisfies chain-of-custody requirements. The append-only semantics also prevent tampering -- events cannot be modified after writing. The trade-off is storage growth (O(n) per transaction) and replay cost (O(n) for full replay, O(log n) with snapshot optimization). For financial compliance, 7-10 year retention is typical. The separate audit trail must be independent of the live memory store so that GDPR erasure requests do not destroy audit ability.

**Q9: What is sleep-time compute and when would you use it?**
A: Sleep-time compute, introduced by Letta in April 2025, separates memory consolidation from live conversation. A background agent processes, summarizes, and rewrites memory blocks while the user is idle. This shifts the latency cost of memory extraction and consolidation off the critical path. I would use it in any application where live-conversation latency matters more than immediate memory availability -- customer support, chat interfaces, interactive workflows. The trade-off is slightly delayed memory updates: if the user says "I prefer window seats" and immediately asks for a flight, the preference may not yet be in long-term memory. For applications requiring immediate memory availability (compliance, safety-critical), I would use hot-path memory writes instead, accepting the added latency.

**Q10: Walk through how you would implement GDPR right to erasure in an agent memory system.**
A: Four steps, each with technical challenges. First, identify all memory entries containing the individual's data -- this requires metadata tracking at write time (which user_id, which session, which interaction generated each entry). Second, purge those entries from the live memory store. In vector stores, this means deleting the embedding and may require re-embedding affected documents that referenced the deleted data. Third, verify the deletion by re-querying for any remaining references. Fourth, generate a compliance audit record proving the deletion occurred (what was deleted, when, GDPR request ID). The hardest part is the GDPR vs EU AI Act tension: deleting memory that informed a consequential decision (like a hiring recommendation) loses the audit trail. The practical solution is a separate, subject-anonymized audit log of consequential decisions -- the live memory store is subject to deletion, but the anonymized audit log is not.

---

## Key Numbers to Memorize

| Metric | Value |
|--------|-------|
| Token reduction: Mem0 vs full-context | 93.2% (1,764 vs 26,031 tokens) |
| Latency reduction: Mem0 p95 vs full-context p95 | 91.6% (1.44s vs 17.1s) |
| Quality trade: Full-context J vs Mem0 J | 72.9% vs 66.9% (~6pp gap) |
| Checkpoint growth: full snapshots at 200 turns | 5.3 GB |
| DeltaChannel reduction | 41x (5.3 GB -> 129 MB) |
| Multi-agent failure from state misalignment | 36.9% |
| Cascade poisoning: single agent propagation | 87% of downstream decisions in 4 hours |
| MINJA injection success rate | >95% |
| Enterprise agent failures from context drift | 65% (2025) |
| Compression safe zone | 2-3x (<1.5% accuracy loss) |
| Agentic session token consumption | 1-3.5M tokens per task |
| Enterprise token growth (2025-2026) | 1,001% |
| Blended cost YoY drop | 67% ($18.40 -> $6.07/MTok) |
| Prompt caching cost reduction | 78.5% (Claude Sonnet 4.5) |
| Semantic caching cost reduction | 86% (AWS eval, 63K queries) |
| Redis 8.4 cache hit vs cold | 160x latency improvement |
| Tool Search overhead reduction | ~85% (134K -> ~20K tokens) |
| Compaction trigger (Anthropic) | 150K input tokens (min 50K) |
| MemGPT DMR improvement | 32.1% -> 92.5% |

---

## Quick Reference

```
FOUR PERSISTENCE CONCERNS (do not conflate):
  Working state -> react FAST (every instruction)
  Checkpoint    -> persist at super-step boundary
  Long-term     -> learn SLOWLY (after changes stabilize)
  SoR           -> ALWAYS WINS (authoritative live data)

CONFLICT RULE:
  Memory suggests, SoR decides.
  On conflict: keep preference in memory, override with ground truth.
  Do NOT delete the preference.

MEMORY WRITE DISCIPLINE:
  Tag: one-time vs lasting
  Gate: confirm before durable ADD/UPDATE
  Redact: PII detect -> redact -> audit before ANY persist
  Never-store: enforce at write path, not via prompt

FALLBACK CHAIN:
  Memory retrieve -> Sliding window -> SoR read

COST OPTIMIZATION (ranked by impact):
  1. Model routing (67% YoY blended cost drop)
  2. Semantic caching (86% cost reduction)
  3. Memory retrieval vs full-context (93% token reduction)
  4. Prompt caching (78.5% cost reduction)
  5. DeltaChannel (41x checkpoint storage reduction)

TENANT ISOLATION:
  Storage-layer (separate indices) NOT app-layer (namespace filters)
  "A filter is a label, not a boundary"

FRAMEWORKS:
  LangMem    -> Already on LangGraph
  Mem0       -> Managed service, broadest adoption (48K+ stars)
  Zep        -> Temporal reasoning (+18.5% LongMemEval)
  Letta      -> Full platform with built-in memory
  In-context -> Short sessions only (<20 turns)
```

---

## Sources

1. System Design Newsletter -- AI Agent Memory (state vs memory vs SoR)
2. LangGraph Persistence / Checkpointers / Stores documentation
3. LangChain -- Memory for agents (CoALA taxonomy, hot-path vs background)
4. LangChain -- Delta Channels (O(N^2) growth, 5.3 GB -> 129 MB at 200 turns)
5. Mem0 paper (arXiv:2504.19413) -- LOCOMO benchmark, Table 2 metrics
6. CoALA (arXiv:2309.02427) -- Four-tier cognitive memory taxonomy
7. Anthropic -- Effective context engineering, Building effective agents
8. Anthropic -- Compaction API (150k default, 50k min)
9. ESAA (arXiv:2602.23193) -- Event sourcing for autonomous agents
10. MINJA attack (NeurIPS 2025) -- >95% injection success
11. OWASP ASI06 -- Memory and Context Poisoning
12. OWASP LLM08 -- Vector and Embedding Weaknesses
13. Galileo AI (Dec 2025) -- 87% cascade poisoning
14. Microsoft Failure Mode Taxonomy v2.0 (June 2026)
15. Margalit et al., 2026 -- Multi-tenant isolation failure modes
16. AI Incident Database #1152 -- Replit agent production DB deletion
17. Letta sleep-time compute (April 2025)
18. DMR benchmark: MemGPT 92.5%
19. Zep: LongMemEval +18.5%, DMR 94.8%
