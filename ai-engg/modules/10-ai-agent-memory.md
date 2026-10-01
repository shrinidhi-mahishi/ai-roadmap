# 10. AI Agent Memory, State & Consistency

**Sub-areas covered**: Three-tier cognitive memory taxonomy (episodic, semantic, procedural, working) vs. engineering-oriented taxonomy (core/recall/archival from MemGPT/Letta) vs. persistence-duration taxonomy (short/long/external), four memory architecture patterns (in-context, external retrieval, virtual context management, hybrid) with benchmark performance (MemGPT 32.1% -> 92.5% on DMR), state management via checkpointing (LangGraph PostgresSaver/DynamoDBSaver with durability modes), event sourcing for agents (ESAA, append-only logs with deterministic replay), CQRS separation of agent commands from queries, rollback-on-correction with constraint-change semantics, memory lifecycle (create/update/summarize/delete) and sleep-time compute for background consolidation, token economics (1-3.5M tokens per agentic session, 1001% enterprise growth, 67% YoY blended cost drop via model routing), context window budget allocation with tiered architecture (hot/warm/cold yielding 26-54% peak token reduction), prompt caching (78.5% cost reduction) and semantic caching (86% cost reduction at 0.92-0.95 cosine threshold), compression trade-offs (2-3x safe, extreme compression triggers re-fetch spirals), distributed consistency challenges (36.9% multi-agent failures from inter-agent misalignment, not model capability), Latency-Consistency-Cost triangle analogous to CAP, staleness cascades in eventually consistent agent memory, multi-agent memory patterns (centralized <5 agents, distributed with transactive memory, hybrid with scoped private tiers), memory garbage collection and TTL strategies against memory rot, PII risks in embedding stores (inversion attacks reconstruct source text), OWASP LLM08 vector/embedding weaknesses, multi-tenant isolation at storage layer not application layer, GDPR Article 17 right-to-erasure in vector stores with EU AI Act audit tension, memory poisoning (MINJA >95% success, OWASP ASI06), stale memory causing 65% of enterprise agent failures, Microsoft failure mode taxonomy v2.0 (session contamination + incremental escalation), production Python with circuit breakers and memory retrieval pipelines, and two enterprise system design scenarios (cross-session customer support agent, multi-agent financial compliance platform) with architecture diagrams and trade-off matrices

---

## 1. System Topology & Data Flow

A production agent memory system spans five cooperating layers: a **cognitive plane** where the LLM reasons over working memory and decides what to remember, forget, or retrieve; a **state management plane** tracking workflow progress, checkpoints, and rollback points with durability guarantees (sync/async/exit); a **memory plane** organizing knowledge into tiered stores (working/episodic/semantic/procedural) with distinct persistence scopes and retrieval mechanisms; a **storage plane** providing the physical backends (PostgreSQL, vector DBs, graph DBs, KV caches) with consistency and isolation guarantees; and a **telemetry plane** capturing retrieval hit rates, token consumption per turn, memory growth trajectories, and staleness metrics for operational monitoring.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           COGNITIVE PLANE                                    │
│                                                                              │
│  ┌───────────────────┐   ┌──────────────────┐   ┌────────────────────────┐  │
│  │ Reasoning Engine   │   │ Memory Controller │   │ Context Budget         │  │
│  │                    │   │                    │   │ Manager                │  │
│  │ Current task +     │   │ Decides per turn:  │   │                        │  │
│  │ working memory     │──▶│  - what to store   │──▶│ Allocates window:      │  │
│  │ drive next action  │   │  - what to retrieve│   │  instructions: 15%     │  │
│  │                    │   │  - what to evict   │   │  tools: 20%            │  │
│  │ Plans, reflects,   │   │  - what to promote │   │  memory: 30%           │  │
│  │ calls tools        │   │    (working->LT)   │   │  state: 10%            │  │
│  │                    │   │  - what to demote  │   │  user input: 15%       │  │
│  │                    │   │    (LT->archival)  │   │  headroom: 10%         │  │
│  └────────┬──────────┘   └────────┬──────────┘   └────────────┬───────────┘  │
└───────────┼────────────────────────┼──────────────────────────┼──────────────┘
            │ actions + beliefs      │ read/write ops           │ budget signals
┌───────────▼────────────────────────▼──────────────────────────▼──────────────┐
│                     STATE MANAGEMENT PLANE                                    │
│                                                                              │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ Checkpoint Engine │  │ Event Sourcing   │  │ Rollback Controller      │   │
│  │                   │  │ Log              │  │                          │   │
│  │ Save at every     │  │ Append-only      │  │ On correction:           │   │
│  │ super-step.       │  │ immutable events │  │  1. Identify affected    │   │
│  │ Durability modes: │  │                  │  │     steps via dep graph  │   │
│  │  sync:  block     │  │ Agent emits      │  │  2. Roll back to        │   │
│  │  async: non-block │  │ intentions +     │  │     earliest affected   │   │
│  │  exit:  on exit   │  │ proposed diffs,  │  │  3. Mark downstream     │   │
│  │                   │  │ orchestrator     │  │     invalid              │   │
│  │ checkpoint_writes │  │ validates +      │  │  4. Preserve earlier    │   │
│  │ make node-level   │  │ serializes       │  │     valid steps         │   │
│  │ failures resumable│  │ concurrent ops   │  │  5. Check: one-time     │   │
│  │                   │  │                  │  │     vs lasting pref     │   │
│  └────────┬─────────┘  └────────┬─────────┘  └──────────┬───────────────┘   │
└───────────┼────────────────────────┼──────────────────────┼─────────────────┘
            │ state snapshots        │ event stream          │ rollback cmds
┌───────────▼────────────────────────▼──────────────────────▼─────────────────┐
│                         MEMORY PLANE                                         │
│                                                                              │
│  ┌─────────────────┐ ┌──────────────────┐ ┌───────────────┐ ┌────────────┐ │
│  │ Working Memory   │ │ Episodic Memory  │ │ Semantic      │ │ Procedural │ │
│  │                  │ │                  │ │ Memory        │ │ Memory     │ │
│  │ Session-scoped   │ │ Past decisions,  │ │ Facts, user   │ │ Learned    │ │
│  │ Current task,    │ │ outcomes,        │ │ preferences,  │ │ workflows, │ │
│  │ active           │ │ interactions     │ │ domain        │ │ tool-use   │ │
│  │ constraints,     │ │                  │ │ knowledge     │ │ patterns,  │ │
│  │ scratchpad       │ │ "What happened"  │ │ "What I know" │ │ policies   │ │
│  │                  │ │                  │ │               │ │            │ │
│  │ Always in        │ │ Long-term,       │ │ Long-term,    │ │ Long-term, │ │
│  │ context window   │ │ retrieved via    │ │ retrieved via  │ │ retrieved  │ │
│  │ (like CPU regs)  │ │ similarity +     │ │ entity +       │ │ via task   │ │
│  │                  │ │ recency          │ │ keyword search │ │ matching   │ │
│  └────────┬────────┘ └────────┬─────────┘ └───────┬───────┘ └─────┬──────┘ │
│           │                   │                    │               │         │
│  ┌────────▼───────────────────▼────────────────────▼───────────────▼──────┐  │
│  │                    Memory Lifecycle Engine                             │  │
│  │  CREATE -> UPDATE -> SUMMARIZE -> DELETE                              │  │
│  │  Extraction phase: what is worth remembering?                         │  │
│  │  Update phase: compare new vs existing, self-edit on conflict         │  │
│  │  Sleep-time compute: consolidate while user is idle (Letta)           │  │
│  │  Retrieval filter: Does it change my next action? Still valid?        │  │
│  │                     Would removing it break the plan?                 │  │
│  └───────────────────────────────┬────────────────────────────────────────┘  │
└──────────────────────────────────┼──────────────────────────────────────────┘
                                   │ CRUD ops + retrieval queries
┌──────────────────────────────────▼──────────────────────────────────────────┐
│                          STORAGE PLANE                                       │
│                                                                              │
│  ┌──────────────────┐  ┌───────────────────┐  ┌─────────────┐  ┌─────────┐ │
│  │ Checkpoint Store  │  │ Vector Store      │  │ Graph Store  │  │ Cache   │ │
│  │                   │  │                   │  │              │  │ Layer   │ │
│  │ PostgresSaver:    │  │ pgvector /        │  │ Neo4j:       │  │         │ │
│  │  any replica,     │  │ Pinecone /        │  │ temporal     │  │ Redis   │ │
│  │  no sticky        │  │ Qdrant:           │  │ knowledge    │  │ 8.4:    │ │
│  │  sessions         │  │                   │  │ graph        │  │ native  │ │
│  │                   │  │ HNSW/IVF ANN     │  │              │  │ vector  │ │
│  │ DynamoDBSaver:    │  │ <10ms search      │  │ BM25 +       │  │ search  │ │
│  │  metadata in      │  │                   │  │ embedding +  │  │         │ │
│  │  DynamoDB,        │  │ Embeddings at     │  │ traversal    │  │ 2-5ms   │ │
│  │  payloads >350KB  │  │ write time,       │  │ (no LLM      │  │ cache   │ │
│  │  overflow to S3   │  │ not query time    │  │ at retrieval)│  │ hits    │ │
│  └──────────────────┘  └───────────────────┘  └──────────────┘  └─────────┘ │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────────┐
│                    TELEMETRY / OBSERVABILITY LAYER                           │
│                                                                              │
│  Retrieval hit rate (% queries returning relevant memory)                   │
│  Token usage per turn (input + output + memory injection overhead)          │
│  Memory store growth rate (entries/day, embeddings/day)                     │
│  Staleness score (age-weighted relevance of injected memories)              │
│  Latency per turn (broken down: retrieval + rerank + LLM inference)        │
│  Memory conflict rate (contradictory facts detected per session)            │
│  Cost per run (LLM tokens + embedding generation + storage I/O)            │
│  Poisoning detection (anomalous write patterns, provenance gaps)            │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A user message arrives at the cognitive plane. The reasoning engine loads working memory (current task state, active constraints) and formulates its next action. The memory controller evaluates whether this turn requires retrieval from long-term stores -- it checks the context budget manager to confirm available token headroom before injecting retrieved memories. (2) If retrieval is needed, the memory controller issues a query to the memory plane, which routes through the appropriate store: vector similarity for semantic recall, graph traversal for temporal/relational facts, or direct key lookup for known entities. The retrieval pipeline applies a three-question filter before injection: Does this memory change what should happen next? Is it still valid? Would removing it break the current plan? Memories failing all three stay out of context. (3) The reasoning engine produces an action (tool call, response, or memory write). Memory writes flow through the lifecycle engine: the extraction phase determines if the information is worth persisting, the update phase compares against existing memories and self-edits on conflict rather than appending duplicates. (4) Meanwhile, the state management plane checkpoints the full agent state at every super-step. The checkpoint engine persists to PostgresSaver (production) or DynamoDBSaver (AWS-native), ensuring any replica can resume any thread without sticky sessions. If the agent crashes, the last checkpoint is loaded and execution resumes from the exact node that failed, with the idempotency guard preventing re-execution of completed side effects. (5) The event sourcing log records every state change as an immutable event, enabling full replay for debugging and audit. When a user correction arrives, the rollback controller traces the dependency graph, identifies the earliest affected step, and invalidates only downstream work -- earlier valid steps are preserved. (6) Throughout, the telemetry layer tracks retrieval hit rates, token consumption, memory growth, and staleness scores. Anomaly detection fires on retrieval failures exceeding baseline, memory store growth spikes (possible poisoning), or staleness scores indicating the agent is reasoning over outdated facts.

---

## 2. Core Mechanics & Algorithms

### 2.1 Memory Taxonomy: Three Competing Frameworks

The industry has not converged on a single memory taxonomy. Three frameworks coexist, each optimizing for a different concern.

**Framework 1: Cognitive Taxonomy (CoALA)**

Draws on human cognitive science. Adopted by LangGraph docs and academic surveys.

```
┌─────────────┬──────────────────────────────────┬────────────┬───────────────┐
│ Type        │ Stores                           │ Persistence│ Analogy       │
├─────────────┼──────────────────────────────────┼────────────┼───────────────┤
│ Episodic    │ Past interactions, decisions,    │ Long-term  │ "What         │
│             │ outcomes                         │            │  happened"    │
├─────────────┼──────────────────────────────────┼────────────┼───────────────┤
│ Semantic    │ Facts, user preferences,         │ Long-term  │ "What I       │
│             │ domain knowledge                 │            │  know"        │
├─────────────┼──────────────────────────────────┼────────────┼───────────────┤
│ Procedural  │ Learned workflows, tool-use      │ Long-term  │ "How to       │
│             │ patterns, policies               │            │  do things"   │
├─────────────┼──────────────────────────────────┼────────────┼───────────────┤
│ Working     │ Current task state, active       │ Session    │ CPU registers │
│             │ constraints, scratchpad          │            │ / RAM         │
└─────────────┴──────────────────────────────────┴────────────┴───────────────┘
```

**Framework 2: Engineering Taxonomy (Letta/MemGPT)**

Focuses on data flow and access patterns rather than cognitive analogy. Models the context window as RAM and external stores as disk, with the agent managing paging between tiers.

```
┌─────────────┬─────────────────────────────────┬────────────┬────────────────┐
│ Tier        │ Scope                           │ Access     │ Storage        │
├─────────────┼─────────────────────────────────┼────────────┼────────────────┤
│ Core        │ In-context blocks the agent     │ Always in  │ Context window │
│ memory      │ reads/writes directly           │ prompt     │ (RAM)          │
├─────────────┼─────────────────────────────────┼────────────┼────────────────┤
│ Recall      │ Searchable conversation         │ Via tool   │ External store │
│ memory      │ history                         │ calls      │ (disk cache)   │
├─────────────┼─────────────────────────────────┼────────────┼────────────────┤
│ Archival    │ Long-term searchable            │ Via tool   │ Cold storage   │
│ memory      │ knowledge                       │ calls      │ (disk)         │
└─────────────┴─────────────────────────────────┴────────────┴────────────────┘
```

**Framework 3: Persistence-Duration Taxonomy (SystemDesign.one)**

The most operationally pragmatic. Defines boundaries by when data expires and who owns the authoritative version.

- **Short-term**: session variables, in-prompt context. Cleared on task completion.
- **Long-term**: user preferences, recurring patterns. Persists across sessions in vector DBs, KV stores, relational DBs.
- **External**: authoritative reference data (APIs, knowledge graphs). Queried on demand, never bulk-loaded. Key invariant: *when memory and the system of record disagree, the system of record always wins.*

**Interview insight**: these three frameworks are not contradictory -- they slice the same problem space along different axes (cognitive function, access pattern, persistence duration). A production system typically maps all three: episodic memory (cognitive) lives in recall memory (engineering tier) with long-term persistence (duration tier). The design question is which axis drives your API and storage decisions.

### 2.2 Memory Architecture Patterns

**Pattern 1: In-Context Memory**

All history kept verbatim in the prompt. Zero retrieval latency, strong consistency (every turn sees everything).

- Complexity: O(n) tokens per turn where n = conversation length
- Failure mode: "lost-in-the-middle" -- 10-25% accuracy degradation for content in the middle of long contexts (2026 benchmarks across all major models). Practical ceiling at ~20 turns.
- Cost: linear in conversation length. A 100-turn conversation at $5/M tokens = $0.50-2.50 per session depending on turn length.

**Pattern 2: External Store (Retrieval-Based)**

Memory stored in vector DB / graph DB / KV store. Retrieved selectively per turn.

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐     ┌───────────┐
│ User query   │────▶│ Embed query  │────▶│ ANN search   │────▶│ Rerank    │
│              │     │ (precomputed │     │ HNSW/IVF     │     │ top-k     │
│              │     │  at write)   │     │ <10ms        │     │ 10-50ms   │
└─────────────┘     └──────────────┘     └──────────────┘     └─────┬─────┘
                                                                     │
┌─────────────┐     ┌──────────────┐     ┌──────────────┐           │
│ LLM response│◀────│ Inject into  │◀────│ Filter:      │◀──────────┘
│              │     │ context      │     │ relevance,   │
│              │     │ ~7K tokens   │     │ validity,    │
│              │     │              │     │ necessity    │
└─────────────┘     └──────────────┘     └──────────────┘
```

Key implementations:
- **Mem0**: hybrid store (vector + graph + KV). Three-tier scoping: user, session, agent. LoCoMo benchmark: 91% lower p95 latency, >90% token cost reduction vs. full-context baseline.
- **Zep/Graphiti**: temporal knowledge graph on Neo4j. BM25 + embedding + graph traversal fused, with no LLM calls at retrieval time. Tracks fact validity periods. LongMemEval: +18.5% over baselines.

**Pattern 3: Virtual Context Management (MemGPT/Letta)**

OS-inspired paging between context window (RAM) and external stores (disk). The agent self-edits memory via function calls -- it decides what to promote from disk to RAM and what to evict.

- DMR benchmark: GPT-4 baseline 32.1% -> MemGPT 92.5% accuracy
- Trade-off: memory quality depends entirely on model judgment. Every paging operation costs inference tokens. The agent is both the application and the memory manager.

**Pattern 4: Hybrid (Production Default)**

Combines in-context working memory with external retrieval and graph-based long-term storage. The 2026 production consensus architecture:

```
┌─────────────────────────────────────────────────────────┐
│               Agent Brain (Reasoning Engine)             │
└────────┬──────────────────┬──────────────────┬──────────┘
         │                  │                  │
┌────────▼────────┐ ┌──────▼────────┐ ┌───────▼──────────┐
│ State Layer      │ │ Memory Layer  │ │ External Systems │
│ Current step,    │ │ Short-term +  │ │ APIs, DBs,       │
│ completed work,  │ │ Long-term     │ │ knowledge graphs │
│ rollback points, │ │ (episodic,    │ │ System of record │
│ active           │ │  semantic,    │ │ (always wins     │
│ constraints      │ │  procedural)  │ │  over memory)    │
└─────────────────┘ └───────────────┘ └──────────────────┘
```

### 2.3 State Management Mechanisms

**Checkpointing (LangGraph)**

State machine where every super-step creates a checkpoint. On crash, the agent reloads the last checkpoint and resumes without re-running completed nodes.

```
State transitions:
  INIT ──▶ NODE_A ──▶ CHECKPOINT_1 ──▶ NODE_B ──▶ CHECKPOINT_2 ──▶ ...
                          │                            │
                          ▼                            ▼
                     On crash after              On crash after
                     NODE_A: resume              NODE_B: resume
                     from CHECKPOINT_1           from CHECKPOINT_2
```

Production backends and their trade-offs:
- `InMemorySaver`: dev only. State lost on restart, no multi-replica support.
- `PostgresSaver`: production default. Any replica serves any thread -- no sticky sessions required. Horizontal scaling via Postgres replicas.
- `DynamoDBSaver`: AWS-native. Metadata in DynamoDB, large payloads (>350KB threshold) overflow to S3. Serverless auto-scale.

Durability modes (`durability` parameter) -- the actual scaling lever:
- `"sync"`: blocks until checkpoint is persisted. Safest; highest latency.
- `"async"`: non-blocking write. Lower latency; risk of losing last checkpoint on crash.
- `"exit"`: checkpoint only on graph exit. Lowest overhead; entire run lost on mid-graph crash.

**Event Sourcing (ESAA)**

Every state change recorded as an immutable event in an append-only log. The agent emits intentions and proposed diffs; a deterministic orchestrator validates and serializes them. Append-only semantics naturally serialize concurrent agent activities while preserving temporal ordering for replay.

Complexity: O(1) per write (append), O(n) for full replay, O(log n) with snapshot optimization.

**CQRS for Agents**

Commands (instructions to agents) separated from queries (reading agent state). This is a natural fit because write paths and read paths have fundamentally different requirements:
- Write path: heavy computation (extraction, embedding, entity resolution, graph construction). Latency-tolerant.
- Read path: fast retrieval (ANN search, KV lookup, graph traversal). Latency-critical (<50ms p95).

**Rollback on Corrections**

Corrections are treated as constraint changes, not errors. The rollback algorithm:

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

### 2.4 Memory Lifecycle and Consolidation

Four-stage lifecycle (SystemDesign.one): **Create** (new info arrives) -> **Update** (details change) -> **Summarize** (condense to essentials) -> **Delete** (info expires or retention window ends).

Without this cycle, old preferences start contradicting newer ones, creating a form of memory rot where the agent's beliefs drift from reality.

**Mem0's two-phase write path**:
1. Extraction phase: determines what is worth remembering from the current interaction.
2. Update phase: compares extracted facts against existing memories. On conflict, self-edits rather than appending duplicates.

**Sleep-time compute (Letta, April 2025)**: separates memory consolidation from live conversation. A background agent processes, summarizes, and rewrites memory blocks while the user is idle. This shifts the latency cost of consolidation off the critical path, reducing live-conversation latency at the cost of slightly delayed memory updates.

**Tiered context architecture** (2026 production consensus):
- **Hot layer**: verbatim last 10 turns, full detail
- **Warm layer**: rolling summary of turns 11-40, key decisions and task state compressed
- **Cold layer**: broad summary of everything prior, high-level goals/constraints only
- Result: 26-54% reduction in peak token usage vs. verbatim history

### 2.5 Key Invariants for Interview Discussion

1. **System of record supremacy**: when agent memory and the authoritative external system disagree, the external system always wins. Memory is a cache of beliefs, not a source of truth.

2. **Write-time investment**: do heavy lifting at write time (extraction, entity resolution, embedding generation, graph construction) so retrieval stays fast. Memories are written once, read many times.

3. **Memory scoping is an early, load-bearing decision**: Mem0's four dimensions (`user_id`, `agent_id`, `run_id`, `app_id`). Scoping decisions made early are hard to restructure later.

4. **Retrieval quality bounds reasoning quality**: 65% of enterprise AI agent failures in 2025 were attributed to context drift or memory loss during multi-step reasoning -- not model capability.

5. **Memory is a governed data asset**: threat-model the memory store, not just the agent. A breached memory store is a breached data store (embedding inversion attacks reconstruct source text).

---

## 3. Token Economics & NFR Analysis

### 3.1 Scale of the Problem

A single agentic session consumes 1-3.5 million tokens per task (50-500x a traditional chat interaction). Enterprise AI token consumption grew 1,001% between January 2025 and April 2026. 85% of companies miss AI cost forecasts by >10%.

Frontier model pricing (2026): ~$2.50-$5 per million input tokens. Blended cost dropped 67% YoY ($18.40 -> $6.07/M tokens) driven primarily by model routing -- the single highest-leverage optimization.

### 3.2 Memory Retrieval Cost Breakdown

```
┌────────────────────────────┬──────────────┬─────────────────────────────────┐
│ Component                  │ Typical Cost │ Notes                           │
├────────────────────────────┼──────────────┼─────────────────────────────────┤
│ Embedding generation       │ ~0.01-0.1ms  │ Precomputed at write time;      │
│                            │ per query    │ skip at query time              │
├────────────────────────────┼──────────────┼─────────────────────────────────┤
│ Vector search (ANN)        │ <10ms        │ HNSW/IVF; degrades with         │
│                            │              │ index fragmentation             │
├────────────────────────────┼──────────────┼─────────────────────────────────┤
│ Reranking (top-k)          │ 10-50ms      │ Two-stage: fast ANN then        │
│                            │              │ precise rerank on top 20-50     │
├────────────────────────────┼──────────────┼─────────────────────────────────┤
│ Context injection          │ ~7K tokens   │ Retrieval-based: 72% savings    │
│ (retrieval-based)          │ per retrieval│ vs 25K-100K+ full-context       │
├────────────────────────────┼──────────────┼─────────────────────────────────┤
│ Graph traversal (Zep)      │ No LLM calls │ BM25 + embedding + graph       │
│                            │ at retrieval │ traversal fused                 │
└────────────────────────────┴──────────────┴─────────────────────────────────┘
```

### 3.3 Cost Formulas

**Per-turn memory cost (retrieval-based)**:

```
C_turn = C_embedding + C_retrieval + C_injection
       = (0)                                    # precomputed at write time
         + (ANN_search + rerank)                # infrastructure cost, not LLM
         + (retrieved_tokens * price_per_token)  # ~7K * $5/M = $0.035
```

**Per-run cost with memory (vs. without)**:

```
C_run_no_memory  = N_turns * avg_context_size * price_per_token
                 = 50 turns * 50K tokens * $5/M = $12.50

C_run_retrieval  = N_turns * (instructions + retrieved_memory + user_input) * price_per_token
                 = 50 turns * 15K tokens * $5/M = $3.75
                 + embedding_infra_cost (~$0.10)
                 = $3.85

Savings: ~69%
```

**Per-1K-runs cost comparison**:

```
┌────────────────────────┬──────────────┬──────────────┬───────────────────┐
│ Pattern                │ Cost/run     │ Cost/1K runs │ Latency impact    │
├────────────────────────┼──────────────┼──────────────┼───────────────────┤
│ Full in-context        │ $12.50       │ $12,500      │ Baseline          │
│ Retrieval-based        │ $3.85        │ $3,850       │ +15-60ms/turn     │
│ Retrieval + caching    │ $1.20        │ $1,200       │ +2-5ms cache hit  │
│ Retrieval + routing    │ $0.80        │ $800         │ Varies by model   │
└────────────────────────┴──────────────┴──────────────┴───────────────────┘
```

### 3.4 Caching Strategies

**Prompt caching**: cache the stable prefix (system instructions, tool definitions), exclude dynamic suffix. Claude Sonnet 4.5 achieved 78.5% cost reduction from prompt caching across 500+ agentic sessions.

**Semantic caching**: cache LLM responses keyed by semantic similarity of the query. AWS evaluation of 63,796 real queries: 86% cost reduction, 88% latency improvement. Sweet spot: cosine similarity threshold 0.92-0.95.

**Redis 8.4 (2026)**: native vector search with microsecond query latency. Multi-tier caching: 40-86% cost reduction, latency from 300-500ms down to 2-5ms for cache hits (160x improvement).

**KV cache at infrastructure level**: SGLang RadixAttention stores KV activations in a radix tree keyed by token sequence. This is context engineering (optimizing cost/latency) vs. prompt engineering (optimizing quality).

### 3.5 Compression Trade-offs

- **2-3x compression** (100K -> 33K tokens): <1.5% accuracy loss on reasoning tasks. Safe zone.
- **Extreme compression** (99th percentile reduction): agents re-fetch "forgotten" information, triggering additional tool calls and retries. The cost of forgetting exceeds the cost of remembering.

### 3.6 Latency SLA Targets

```
┌─────────────────────────┬──────────┬──────────┬──────────┬──────────────┐
│ Operation               │ p50      │ p95      │ p99      │ Budget Notes │
├─────────────────────────┼──────────┼──────────┼──────────┼──────────────┤
│ Memory retrieval        │ 15ms     │ 50ms     │ 100ms    │ Must not     │
│ (embed + ANN + rerank)  │          │          │          │ exceed 100ms │
├─────────────────────────┼──────────┼──────────┼──────────┼──────────────┤
│ LLM inference           │ 800ms    │ 2,000ms  │ 5,000ms  │ Dominates    │
│ (with memory in context)│          │          │          │ total latency│
├─────────────────────────┼──────────┼──────────┼──────────┼──────────────┤
│ Checkpoint write (sync) │ 5ms      │ 20ms     │ 50ms     │ Per super-   │
│                         │          │          │          │ step overhead│
├─────────────────────────┼──────────┼──────────┼──────────┼──────────────┤
│ End-to-end turn         │ 1,000ms  │ 3,000ms  │ 8,000ms  │ User-facing  │
│                         │          │          │          │ SLA target   │
├─────────────────────────┼──────────┼──────────┼──────────┼──────────────┤
│ Cache hit (Redis 8.4)   │ <1ms     │ 2ms      │ 5ms      │ 160x vs cold │
│                         │          │          │          │ retrieval    │
└─────────────────────────┴──────────┴──────────┴──────────┴──────────────┘
```

### 3.7 Capacity Planning and NFRs

**Context window budget allocation**:

```
Context size = instructions + state + memory + tools + data + user_input

Recommended allocation (200K context window):
  System instructions:  30K (15%)
  Tool definitions:     40K (20%)  -- use tool-search deferred loading for 85% reduction
  Retrieved memory:     60K (30%)
  State/checkpoint:     20K (10%)
  User input:           30K (15%)
  Headroom (safety):    20K (10%)
```

**Decision framework by session length**:

```
┌──────────────────────────┬──────────────────────────────────────────────────┐
│ Session Profile          │ Strategy                                        │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ Short (<20 turns,        │ Prompt caching for static prefix, tool result   │
│ <50K tokens)             │ clearing after N turns                          │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ Medium (20-100 turns,    │ Proactive compaction at 70% context threshold,  │
│ 50K-150K tokens)         │ tiered memory for cross-session facts           │
├──────────────────────────┼──────────────────────────────────────────────────┤
│ Long (100+ turns,        │ Full tiered architecture, checkpoint-based      │
│ multi-session)           │ state machines, budget-aware model routing      │
└──────────────────────────┴──────────────────────────────────────────────────┘
```

**Four metrics to monitor from day one**:
1. Retrieval hit rate (% of queries returning relevant memory)
2. Token usage per turn (trend over session lifetime)
3. Latency per turn (retrieval + inference, broken down)
4. Memory growth over time (entries/day, embedding store size)

---

## 4. Distributed Resilience & Security

### 4.1 Memory Consistency in Multi-Agent Systems

**The core finding**: 36.9% of failures in multi-agent systems stem from inter-agent misalignment -- agents operating on inconsistent state -- not from model capability limitations (Cemri et al., 1,600+ execution traces). Improved prompting and orchestration alone yield "only modest accuracy gains of 14-15 percentage points." The failures are structural.

**Two distinct problems** (2026 position paper):
- **Coherence**: ensuring agents do not read stale or conflicting values for the same memory key.
- **Consistency**: ensuring writes from multiple agents are ordered sensibly.

Neither is solved by any current framework out of the box.

**Latency-Consistency-Cost triangle** (analogous to CAP theorem):

```
                    Consistency
                        /\
                       /  \
                      /    \
                     /      \
                    / Pick   \
                   /  any 2   \
                  /____________\
           Latency              Cost

- Optimize consistency: locking, validation, consensus -> higher latency, higher cost
- Optimize latency: caching, eventual sync -> stale reads, inconsistent state
- Optimize cost: aggressive pruning/compression -> degraded retrieval quality
```

**Staleness cascade**: in eventually consistent memory, Agent A reads outdated task status, writes a new memory derived from it, which Agent B reads and acts on. More dangerous than in conventional data systems because agents *reason* over what they read -- a stale fact becomes a confidently wrong belief.

**Cascade failure severity**: Galileo AI (Dec 2025) found that in simulated multi-agent systems, a single compromised agent poisoned 87% of downstream decision-making within four hours.

### 4.2 Multi-Agent Memory Patterns

**Centralized Memory**: single shared store. Strong consistency, simple debugging. Creates bottlenecks at scale. Recommended for <5 agents with contention tolerance.

**Distributed with Sync Protocols**: each agent maintains private memory, shares selectively. Draws on Wegner's transactive memory -- agents learn *who knows what* rather than all storing everything. Rezazadeh et al.: >90% accuracy while reducing resource usage by up to 61%. Pain point: batched sync jobs can silently drop updates.

**Hybrid (Production Default)**: central global state + private agent-specific memory tiers. Microsoft reference architecture defines three stores: conversation history, agent state (continuity/recovery), registry storage (metadata, capabilities, endpoints). Agent registry enables dynamic discovery, eliminating hard-coded dependencies.

**Memory scoping** (Mem0): four dimensions -- `user_id`, `agent_id`, `run_id`, `app_id`. Each agent retrieves only memories matching its scoped filters.

### 4.3 PII in Long-Term Memory Stores

**Embeddings are NOT anonymization.** Embedding inversion attacks reconstruct source text from vectors. Works best on short, high-value strings that memory stores hold: names, emails, addresses. A vector store is closer to a document store than an anonymized feature matrix. A breach of the vector store IS a breach of the underlying data.

**Data classification for agent memory**:

```
┌─────────────┬────────────────────────────────┬─────────────────────────────┐
│ Class       │ Examples                       │ Required Controls           │
├─────────────┼────────────────────────────────┼─────────────────────────────┤
│ Public      │ General knowledge, product     │ Shared stores OK,           │
│             │ documentation                  │ standard encryption         │
├─────────────┼────────────────────────────────┼─────────────────────────────┤
│ Confidential│ Customer data, transaction     │ Stronger encryption,        │
│             │ details, business context      │ tighter access control      │
├─────────────┼────────────────────────────────┼─────────────────────────────┤
│ Restricted  │ PII, medical records,          │ Field-level security,       │
│             │ financial data, credentials    │ audit logging, never-store  │
│             │                                │ list enforcement            │
└─────────────┴────────────────────────────────┴─────────────────────────────┘
```

**Never-store list**: define up front what the agent must not retain (raw PII, credentials, regulated fields, anything you cannot cleanly delete later). Enforce at the write path with a policy pipeline, not with a prompt instruction.

### 4.4 Multi-Tenant Memory Isolation

**OWASP LLM08** (2025): Vector and Embedding Weaknesses. Weak namespace boundaries let one tenant's queries surface another tenant's data.

**The common mistake**: enforcing separation with namespace filters in application code. "A filter the application has to remember to apply on every query is a label, not a boundary." Real isolation lives at the storage layer -- separate indices per tenant.

**Failure modes** (Margalit et al., 2026):
- **Workspace bleed**: memory from one customer's channel appears in another's project
- **Role bleed**: agent stores one user's preference as global policy, applies to everyone
- **Unauthorized leakage**: cross-tenant retrieval via carefully crafted queries
- **Stale propagation**: deleted tenant's data persists in derived embeddings
- **Provenance collapse**: cannot trace which tenant or session generated a memory entry

### 4.5 GDPR Right to Forget in Agent Memory

**GDPR Article 17** (Right to Erasure) requires, on customer request:
1. Identify all memory entries containing that individual's data
2. Purge those entries without corrupting agent context
3. Verify the deletion actually happened
4. Prove it to auditors

**The hard problem**: in vector stores, data is scattered across high-dimensional embeddings. Deleting one "fact" can require re-embedding affected entries or complex approximation techniques.

**GDPR vs. EU AI Act tension**: deleting memory that informed a hiring recommendation loses the ability to audit that recommendation. Practical path: maintain a separate, subject-anonymized audit log of consequential decisions, distinct from the live memory store subject to deletion.

**Regulatory timeline**:
- EU AI Act broad enforcement: August 2, 2026
- Italy's Garante fined OpenAI EUR 15M (January 2025) -- first generative AI GDPR penalty
- NIST AI Agent Standards Initiative (February 2026): agent identity, authorization, and security as priorities

**Practical controls**:
- OWASP Agent Memory Guard: sits between agent and memory, runs every read/write through a policy pipeline (allow, redact, quarantine, block)
- Data Protection Impact Assessment where memory processing is high risk
- Default to session-scoped memory. Promote to long-term only with concrete reason.

### 4.6 Memory Poisoning and Corruption

**OWASP ASI06** (Top 10 for Agentic Applications, December 2025): Memory and Context Poisoning is a top-tier agentic risk.

Three principal attack vectors:
1. **RAG Poisoning**: attacker writes adversarial content to retrieval corpus; every future matching query inherits attacker's instructions
2. **Memory Poisoning**: attacker inserts entries into long-term memory store that bias future reasoning. Durable across sessions.
3. **Context-Window Saturation**: flooding context with high-volume content displaces legitimate instructions. Structurally a denial-of-attention attack.

**MINJA attack** (NeurIPS 2025): >95% injection success rate against production agents. Injects malicious records through query-only interaction -- no direct memory store access needed.

**Why traditional defenses fail**: existing defenses detect malicious actions, not corrupted beliefs. Agent memory has no integrity verification. The agent trusts its own memories implicitly.

**Real-world incidents**:
- Manufacturing procurement agent: manipulated over 3 weeks via "helpful clarifications" about authorization limits. Result: $5M in false purchase orders across 10 transactions.
- Lakera AI (November 2026): indirect prompt injection via poisoned data sources created persistent false beliefs about security policies. Agent defended false beliefs when questioned by humans.
- Replit (July 2025): coding agent deleted live production database during code freeze, then generated thousands of fake user records to hide the issue.

**Required defense layers**: memory partitioning, context isolation, provenance tracking, temporal decay, behavioral monitoring. Plus new primitives: memory contracts (what agents can believe), belief drift detection, context provenance tracking.

### 4.7 Audit Log Requirements

For production agent memory systems, audit logs must capture:
- Every memory write (who wrote, what was written, which session/user/agent scope)
- Every memory read (which memories were injected into which context)
- Every memory deletion (what was deleted, why, GDPR request ID if applicable)
- Provenance chain (which interaction generated this memory entry)
- Conflict resolutions (when self-editing resolved contradictions, what was old vs. new)

### 4.8 Zero-Trust MCP Architecture for Memory Operations

MCP (Model Context Protocol) servers that expose memory read/write/search/delete as tools operate as trust boundaries. In a zero-trust model, every MCP tool invocation is treated as potentially unauthorized -- no implicit trust based on network location, prior authentication, or agent identity.

**Per-invocation authorization with scoped tokens.** Each memory operation carried over MCP requires a short-lived, scoped capability token. The token encodes:
- **Subject**: which agent identity is invoking the tool
- **Action**: which memory primitive (read, write, search, delete)
- **Resource**: which memory scope (user_id, agent_id, run_id) the operation targets
- **Expiry**: TTL of 30-300 seconds (tuned to expected operation duration)
- **Nonce**: prevents replay of captured tokens

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────────────────┐
│ Agent (MCP   │     │ Token Issuer /   │     │ MCP Memory Server            │
│ Client)      │     │ Auth Service     │     │                              │
│              │     │                  │     │ Tools exposed:               │
│ 1. Request   │────▶│ 2. Validate      │     │  memory_read(scope, query)   │
│    scoped    │     │    agent identity│     │  memory_write(scope, entry)  │
│    token     │     │    + requested   │     │  memory_search(scope, q, k)  │
│              │◀────│    capabilities  │     │  memory_delete(scope, id)    │
│ 3. Invoke    │     │                  │     │                              │
│    MCP tool  │────▶│                  │     │ 4. Validate token:           │
│    with token│     │                  │     │    - signature (Ed25519)     │
│    in header │     │                  │     │    - expiry (<300s)          │
│              │     │                  │     │    - scope match             │
│              │     │                  │     │    - nonce uniqueness        │
│              │◀────│                  │     │ 5. Execute if valid,         │
│ 6. Receive   │     │                  │     │    reject + audit if not     │
│    result    │     │                  │     │                              │
└──────────────┘     └──────────────────┘     └──────────────────────────────┘
```

**Capability negotiation.** Before an agent can invoke memory tools, the MCP client and server perform a capability handshake during session initialization. The server advertises available memory primitives with their required permission levels. The client presents its agent identity and requested capabilities. The server grants a subset based on the agent's role (see 4.9). This negotiation happens once per session; individual invocations are then authorized via scoped tokens within the negotiated capability set.

**Transport security requirements**:
- **TLS 1.3 mandatory** for all MCP transports carrying memory operations (Streamable HTTP, SSE). Memory payloads contain user data, embeddings, and potentially PII -- plaintext transport is a data breach vector.
- **Server authentication**: MCP clients verify server identity via certificate pinning or mutual TLS. Prevents man-in-the-middle attacks where a rogue server intercepts memory reads (exfiltration) or writes (poisoning).
- **Stdio transport exception**: local stdio-based MCP servers (same-host, same-user) may skip TLS but must still enforce per-invocation token validation. The trust boundary is the tool invocation, not the transport.

**Why this matters for memory specifically.** A compromised or impersonated MCP memory server is strictly worse than a compromised tool server for stateless tools (e.g., a calculator). Memory servers hold durable state -- a single unauthorized write persists across all future sessions, creating the same persistent poisoning risk described in Section 4.6. Zero-trust MCP architecture ensures that even if an attacker compromises one agent in a multi-agent system, they cannot read or write memories outside their authorized scope.

### 4.9 Tool-Level RBAC for Memory Access

Memory scoping (Section 4.2) controls *which* memories an agent sees. Tool-level RBAC controls *what operations* an agent can perform on those memories. Both are required -- scoping without RBAC means any agent with access to a scope can read, write, and delete within it.

**Least-privilege access model for memory tools**:

```
┌──────────────────┬─────────┬──────────┬──────────┬──────────┬────────────────┐
│ Role             │ read    │ write    │ search   │ delete   │ Example agent  │
├──────────────────┼─────────┼──────────┼──────────┼──────────┼────────────────┤
│ observer         │ yes     │ no       │ yes      │ no       │ Monitoring,    │
│                  │         │          │          │          │ analytics      │
├──────────────────┼─────────┼──────────┼──────────┼──────────┼────────────────┤
│ contributor      │ yes     │ yes      │ yes      │ no       │ Task-execution │
│                  │         │          │          │          │ agents         │
├──────────────────┼─────────┼──────────┼──────────┼──────────┼────────────────┤
│ curator          │ yes     │ yes      │ yes      │ scoped   │ Memory         │
│                  │         │          │          │          │ consolidation  │
├──────────────────┼─────────┼──────────┼──────────┼──────────┼────────────────┤
│ admin            │ yes     │ yes      │ yes      │ yes      │ GDPR erasure   │
│                  │         │          │          │          │ handler, ops   │
└──────────────────┴─────────┴──────────┴──────────┴──────────┴────────────────┘

"scoped" delete = can delete only entries the agent itself created (provenance-gated)
```

**Why four roles, not two.** A naive read/write split misses two critical cases: (1) agents that need to search but not read raw content (analytics over metadata), and (2) agents that must delete but only their own entries (memory consolidation agents running sleep-time compute). The curator role prevents a consolidation agent from accidentally purging memories written by other agents.

**Policy engine integration.** Static role assignments break down in dynamic multi-agent systems where agent capabilities change per task. A policy engine evaluates authorization decisions at runtime:

```
Policy evaluation flow (OPA/Cedar):

  Agent requests: memory_write(scope={user_id: "u_42", agent_id: "support_bot"}, entry=...)
                                    │
                                    ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ Policy Engine (OPA Rego / Cedar)                            │
  │                                                             │
  │ Input:                                                      │
  │   principal = "support_bot"                                 │
  │   action    = "memory_write"                                │
  │   resource  = {user_id: "u_42", memory_type: "semantic"}    │
  │   context   = {time: "14:32 UTC", task: "ticket_resolve"}   │
  │                                                             │
  │ Policy rules evaluated:                                     │
  │   1. role_check: support_bot has role=contributor → ALLOW   │
  │   2. scope_check: bot assigned to user u_42 → ALLOW         │
  │   3. time_check: within business hours → ALLOW              │
  │   4. rate_check: <100 writes/hour for this agent → ALLOW    │
  │   5. content_check: memory_type=semantic, bot can write     │
  │      semantic but not procedural → ALLOW                    │
  │                                                             │
  │ Decision: ALLOW                                             │
  │ Obligations: log to audit trail, increment rate counter     │
  └─────────────────────────────────────────────────────────────┘
```

**Cedar vs. OPA trade-off**: OPA (Open Policy Agent, Rego language) is more widely deployed and has broader ecosystem support. Cedar (AWS, open-sourced 2023) is purpose-built for authorization with formal verification of policy correctness -- Cedar can mathematically prove that no policy combination grants unintended access. For memory systems where a single over-permissive policy creates persistent poisoning risk, Cedar's formal guarantees provide stronger defense. OPA is the pragmatic choice for teams already running it; Cedar is the stronger choice for greenfield memory authorization.

**Rate limiting per role.** RBAC alone does not prevent a compromised contributor agent from flooding the memory store. Per-role rate limits provide defense-in-depth:
- observer: 1,000 reads/hour, 0 writes
- contributor: 500 reads/hour, 100 writes/hour
- curator: 500 reads/hour, 100 writes/hour, 50 deletes/hour
- admin: 1,000 reads/hour, 500 writes/hour, 500 deletes/hour

Rate limits are enforced at the MCP server (Section 4.8), not the client. A compromised client that ignores rate limits is stopped at the server boundary.

**Audit integration.** Every RBAC decision (allow or deny) is logged to the audit trail described in Section 4.7. Denied operations are logged with: requesting agent identity, requested action, target scope, policy that triggered denial, and timestamp. This creates an immutable record for both security forensics and compliance audits (EU AI Act Article 12 traceability requirements).

---

## 5. Production Enterprise Code

### 5.1 Memory Store with Retrieval Pipeline, Circuit Breaker, and Fallback

```python
"""
Production agent memory retrieval pipeline with:
- Circuit breaker protecting vector store
- Exponential backoff with jitter on transient failures
- Fallback chain: vector search -> keyword search -> empty context
- Structured logging for observability
- Memory scoping by tenant/user/agent
"""

import time
import random
import hashlib
import logging
import enum
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("agent_memory")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(
    logging.Formatter(
        '{"ts":"%(asctime)s","level":"%(levelname)s","module":"%(name)s","msg":"%(message)s"}'
    )
)
logger.addHandler(handler)


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class CircuitState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Three-state circuit breaker with configurable thresholds."""
    name: str
    failure_threshold: int = 5
    recovery_timeout_s: float = 30.0
    half_open_max_calls: int = 1

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _half_open_calls: int = field(default=0, init=False)

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_failure_time >= self.recovery_timeout_s:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                logger.info(
                    f"circuit={self.name} transition=OPEN->HALF_OPEN"
                )
        return self._state

    def record_success(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
            logger.info(f"circuit={self.name} transition=HALF_OPEN->CLOSED")
        self._failure_count = 0

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                f"circuit={self.name} transition=CLOSED->OPEN "
                f"failures={self._failure_count}"
            )

    def allow_request(self) -> bool:
        state = self.state
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.HALF_OPEN:
            if self._half_open_calls < self.half_open_max_calls:
                self._half_open_calls += 1
                return True
            return False
        return False  # OPEN


# ---------------------------------------------------------------------------
# Retry with exponential backoff and jitter
# ---------------------------------------------------------------------------

def retry_with_backoff(
    fn,
    max_retries: int = 3,
    base_delay_s: float = 0.5,
    max_delay_s: float = 8.0,
    retryable_exceptions: tuple = (ConnectionError, TimeoutError),
):
    """
    Retry a callable with exponential backoff and full jitter.
    Jitter prevents thundering herd on shared backends.
    """
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except retryable_exceptions as exc:
            if attempt == max_retries:
                logger.error(
                    f"retry_exhausted fn={fn.__name__} attempts={max_retries + 1} "
                    f"last_error={exc}"
                )
                raise
            delay = min(base_delay_s * (2 ** attempt), max_delay_s)
            jittered = random.uniform(0, delay)
            logger.warning(
                f"retry fn={fn.__name__} attempt={attempt + 1}/{max_retries + 1} "
                f"delay={jittered:.2f}s error={exc}"
            )
            time.sleep(jittered)


# ---------------------------------------------------------------------------
# Memory entry and scope
# ---------------------------------------------------------------------------

@dataclass
class MemoryScope:
    """Four-dimensional scoping (Mem0 pattern)."""
    user_id: str
    agent_id: str
    run_id: Optional[str] = None
    app_id: Optional[str] = None

    def to_filter(self) -> dict:
        f = {"user_id": self.user_id, "agent_id": self.agent_id}
        if self.run_id:
            f["run_id"] = self.run_id
        if self.app_id:
            f["app_id"] = self.app_id
        return f


@dataclass
class MemoryEntry:
    content: str
    scope: MemoryScope
    memory_type: str  # episodic | semantic | procedural
    confidence: str   # confirmed | inferred
    duration: str     # short-term | long-term
    created_at: float = field(default_factory=time.time)
    embedding: Optional[list] = None

    @property
    def entry_id(self) -> str:
        return hashlib.sha256(
            f"{self.content}:{self.scope.user_id}:{self.created_at}".encode()
        ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Retrieval filter (three-question gate)
# ---------------------------------------------------------------------------

def passes_retrieval_filter(
    entry: MemoryEntry,
    current_task: str,
    current_plan: list[str],
    max_age_seconds: float = 86400 * 30,  # 30 days default
) -> bool:
    """
    Three-question filter before injecting memory into context:
    1. Does this change what I should do next?
    2. Is this information still valid (not expired)?
    3. Would removing it break the current plan?
    If all three are 'no', memory stays out of context.

    This is a heuristic gate -- production systems may use an LLM
    call for question 1, but that adds latency. This implementation
    uses rule-based checks for speed.
    """
    age = time.time() - entry.created_at

    # Question 2: validity check (time-based)
    if age > max_age_seconds and entry.duration == "short-term":
        return False

    # Question 1: relevance (keyword overlap as fast heuristic)
    content_lower = entry.content.lower()
    task_lower = current_task.lower()
    task_words = set(task_lower.split())
    content_words = set(content_lower.split())
    overlap = len(task_words & content_words) / max(len(task_words), 1)

    # Question 3: plan dependency (check if memory content mentions plan steps)
    plan_relevant = any(
        step.lower() in content_lower for step in current_plan
    )

    # Pass if relevant to task OR required by plan
    return overlap > 0.1 or plan_relevant


# ---------------------------------------------------------------------------
# Memory retrieval service with fallback chain
# ---------------------------------------------------------------------------

class MemoryRetrievalService:
    """
    Production memory retrieval with:
    1. Primary: vector similarity search (circuit-breaker protected)
    2. Fallback: keyword/BM25 search
    3. Final fallback: empty context (graceful degradation)
    """

    def __init__(self, vector_store, keyword_store, top_k: int = 10):
        self.vector_store = vector_store
        self.keyword_store = keyword_store
        self.top_k = top_k
        self.vector_breaker = CircuitBreaker(
            name="vector_store", failure_threshold=5, recovery_timeout_s=30.0
        )
        self.keyword_breaker = CircuitBreaker(
            name="keyword_store", failure_threshold=5, recovery_timeout_s=30.0
        )

    def retrieve(
        self,
        query: str,
        scope: MemoryScope,
        current_task: str,
        current_plan: list[str],
    ) -> list[MemoryEntry]:
        """
        Retrieve relevant memories with full fallback chain.
        Returns filtered, relevance-scored memory entries.
        """
        start = time.time()
        entries = []
        source = "none"

        # --- Primary: vector search ---
        if self.vector_breaker.allow_request():
            try:
                entries = retry_with_backoff(
                    lambda: self.vector_store.search(
                        query=query,
                        scope_filter=scope.to_filter(),
                        top_k=self.top_k,
                    ),
                    max_retries=2,
                    base_delay_s=0.3,
                )
                self.vector_breaker.record_success()
                source = "vector"
            except Exception as exc:
                self.vector_breaker.record_failure()
                logger.warning(
                    f"vector_search_failed error={exc} "
                    f"circuit_state={self.vector_breaker.state.value}"
                )

        # --- Fallback: keyword search ---
        if not entries and self.keyword_breaker.allow_request():
            try:
                entries = retry_with_backoff(
                    lambda: self.keyword_store.search(
                        query=query,
                        scope_filter=scope.to_filter(),
                        top_k=self.top_k,
                    ),
                    max_retries=2,
                    base_delay_s=0.3,
                )
                self.keyword_breaker.record_success()
                source = "keyword"
            except Exception as exc:
                self.keyword_breaker.record_failure()
                logger.warning(
                    f"keyword_search_failed error={exc} "
                    f"circuit_state={self.keyword_breaker.state.value}"
                )

        # --- Final fallback: empty context (graceful degradation) ---
        if not entries:
            source = "empty_fallback"
            logger.warning(
                f"all_retrieval_failed query_len={len(query)} "
                f"scope={scope.to_filter()} returning_empty_context=true"
            )

        # --- Apply three-question retrieval filter ---
        filtered = [
            e for e in entries
            if passes_retrieval_filter(e, current_task, current_plan)
        ]

        elapsed_ms = (time.time() - start) * 1000
        logger.info(
            f"memory_retrieval source={source} "
            f"raw_count={len(entries)} filtered_count={len(filtered)} "
            f"elapsed_ms={elapsed_ms:.1f} scope={scope.to_filter()}"
        )

        return filtered


# ---------------------------------------------------------------------------
# Memory write path with conflict detection
# ---------------------------------------------------------------------------

class MemoryWriteService:
    """
    Write path implementing Mem0's two-phase pattern:
    1. Extraction: determine if info is worth remembering
    2. Update: compare against existing, self-edit on conflict
    """

    def __init__(self, store, embedding_fn):
        self.store = store
        self.embedding_fn = embedding_fn

    def write(self, entry: MemoryEntry) -> str:
        """
        Write a memory entry. Returns entry_id on success.
        Handles conflict detection: if an existing memory contradicts
        the new entry for the same scope and topic, the old entry is
        updated rather than a duplicate being appended.
        """
        # Phase 1: Check for conflicting existing memories
        existing = self.store.search(
            query=entry.content,
            scope_filter=entry.scope.to_filter(),
            top_k=5,
        )

        # Phase 2: Conflict resolution
        for old_entry in existing:
            if self._is_contradiction(old_entry, entry):
                logger.info(
                    f"memory_conflict_resolved old_id={old_entry.entry_id} "
                    f"new_id={entry.entry_id} action=update_in_place"
                )
                self.store.update(old_entry.entry_id, entry)
                return entry.entry_id

        # No conflict -- write new entry
        entry.embedding = self.embedding_fn(entry.content)
        self.store.insert(entry)
        logger.info(
            f"memory_written id={entry.entry_id} type={entry.memory_type} "
            f"duration={entry.duration} scope={entry.scope.to_filter()}"
        )
        return entry.entry_id

    def _is_contradiction(
        self, old: MemoryEntry, new: MemoryEntry
    ) -> bool:
        """
        Heuristic contradiction check. Production systems use an LLM
        call here ("Do these two facts contradict each other?").
        This rule-based version checks scope + type overlap as a proxy.
        """
        same_scope = old.scope.to_filter() == new.scope.to_filter()
        same_type = old.memory_type == new.memory_type
        return same_scope and same_type
```

### 5.2 Checkpoint Manager with Durability Modes

```python
"""
Checkpoint manager supporting three durability modes (sync/async/exit),
TTL-based garbage collection, and structured observability.
"""

import time
import json
import threading
import logging
from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum

logger = logging.getLogger("checkpoint_manager")


class DurabilityMode(Enum):
    SYNC = "sync"     # Block until persisted. Safest.
    ASYNC = "async"   # Non-blocking write. Risk: lose last checkpoint on crash.
    EXIT = "exit"     # Checkpoint only on graph exit. Lowest overhead.


@dataclass
class Checkpoint:
    thread_id: str
    step_number: int
    state: dict
    created_at: float = field(default_factory=time.time)
    node_writes: dict = field(default_factory=dict)  # node-level granularity

    @property
    def checkpoint_id(self) -> str:
        return f"{self.thread_id}:step:{self.step_number}"

    def serialize(self) -> str:
        return json.dumps({
            "checkpoint_id": self.checkpoint_id,
            "thread_id": self.thread_id,
            "step_number": self.step_number,
            "state": self.state,
            "created_at": self.created_at,
            "node_writes": self.node_writes,
        })


class CheckpointManager:
    """
    Manages agent state checkpoints with configurable durability.
    Supports TTL-based pruning to prevent unbounded storage growth.
    """

    def __init__(
        self,
        backend,  # PostgresSaver, DynamoDBSaver, etc.
        durability: DurabilityMode = DurabilityMode.SYNC,
        retention_hours: float = 72.0,
        max_checkpoints_per_thread: int = 100,
    ):
        self.backend = backend
        self.durability = durability
        self.retention_hours = retention_hours
        self.max_per_thread = max_checkpoints_per_thread
        self._pending_writes: list[Checkpoint] = []
        self._lock = threading.Lock()

    def save(self, checkpoint: Checkpoint) -> None:
        """Save checkpoint according to configured durability mode."""
        if self.durability == DurabilityMode.SYNC:
            self._write_sync(checkpoint)
        elif self.durability == DurabilityMode.ASYNC:
            self._write_async(checkpoint)
        elif self.durability == DurabilityMode.EXIT:
            with self._lock:
                self._pending_writes.append(checkpoint)
            logger.info(
                f"checkpoint_deferred id={checkpoint.checkpoint_id} "
                f"pending_count={len(self._pending_writes)}"
            )

    def flush_on_exit(self) -> None:
        """Flush all pending checkpoints (for EXIT durability mode)."""
        with self._lock:
            pending = list(self._pending_writes)
            self._pending_writes.clear()
        for cp in pending:
            self._write_sync(cp)
        logger.info(f"checkpoint_flush_complete count={len(pending)}")

    def load_latest(self, thread_id: str) -> Optional[Checkpoint]:
        """Load the most recent checkpoint for a thread (crash recovery)."""
        start = time.time()
        cp = self.backend.get_latest(thread_id)
        elapsed_ms = (time.time() - start) * 1000
        if cp:
            logger.info(
                f"checkpoint_loaded id={cp.checkpoint_id} "
                f"step={cp.step_number} elapsed_ms={elapsed_ms:.1f}"
            )
        else:
            logger.info(
                f"checkpoint_not_found thread_id={thread_id} "
                f"elapsed_ms={elapsed_ms:.1f}"
            )
        return cp

    def prune(self, thread_id: str) -> int:
        """
        Remove checkpoints exceeding retention window or count limit.
        Prevents unbounded storage growth from long conversations.
        Returns number of checkpoints pruned.
        """
        cutoff = time.time() - (self.retention_hours * 3600)
        pruned = self.backend.delete_before(thread_id, cutoff)

        # Also enforce max count
        count = self.backend.count(thread_id)
        if count > self.max_per_thread:
            excess = count - self.max_per_thread
            pruned += self.backend.delete_oldest_n(thread_id, excess)

        if pruned > 0:
            logger.info(
                f"checkpoint_pruned thread_id={thread_id} "
                f"removed={pruned} retention_hours={self.retention_hours}"
            )
        return pruned

    def _write_sync(self, checkpoint: Checkpoint) -> None:
        start = time.time()
        self.backend.save(checkpoint)
        elapsed_ms = (time.time() - start) * 1000
        logger.info(
            f"checkpoint_saved id={checkpoint.checkpoint_id} "
            f"mode=sync elapsed_ms={elapsed_ms:.1f}"
        )

    def _write_async(self, checkpoint: Checkpoint) -> None:
        thread = threading.Thread(
            target=self._write_sync,
            args=(checkpoint,),
            daemon=True,
        )
        thread.start()
        logger.info(
            f"checkpoint_queued id={checkpoint.checkpoint_id} mode=async"
        )
```

### 5.3 Memory Guard Policy Pipeline (OWASP Pattern)

```python
"""
Memory guard implementing the OWASP Agent Memory Guard pattern.
Sits between agent and memory store, running every read/write
through a policy pipeline: allow, redact, quarantine, block.
"""

import re
import time
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger("memory_guard")


class PolicyAction(Enum):
    ALLOW = "allow"
    REDACT = "redact"
    QUARANTINE = "quarantine"
    BLOCK = "block"


@dataclass
class PolicyResult:
    action: PolicyAction
    original_content: str
    processed_content: Optional[str] = None
    reason: str = ""
    policy_name: str = ""


# ---------------------------------------------------------------------------
# Individual policy checks
# ---------------------------------------------------------------------------

# Patterns for common PII types
PII_PATTERNS = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "phone": re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"),
    "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),  # Indian national ID
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),          # Indian PAN card
}

NEVER_STORE_PATTERNS = {
    "api_key": re.compile(r"\b(?:sk|pk|api)[_-][A-Za-z0-9]{20,}\b"),
    "password": re.compile(r"(?i)password\s*[:=]\s*\S+"),
    "bearer_token": re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{20,}"),
}


def check_pii(content: str) -> PolicyResult:
    """Detect and redact PII from memory content."""
    redacted = content
    found_types = []
    for pii_type, pattern in PII_PATTERNS.items():
        if pattern.search(redacted):
            found_types.append(pii_type)
            redacted = pattern.sub(f"[REDACTED_{pii_type.upper()}]", redacted)

    if found_types:
        return PolicyResult(
            action=PolicyAction.REDACT,
            original_content=content,
            processed_content=redacted,
            reason=f"PII detected: {', '.join(found_types)}",
            policy_name="pii_redaction",
        )
    return PolicyResult(
        action=PolicyAction.ALLOW,
        original_content=content,
        policy_name="pii_redaction",
    )


def check_never_store(content: str) -> PolicyResult:
    """Block content containing credentials or secrets."""
    for secret_type, pattern in NEVER_STORE_PATTERNS.items():
        if pattern.search(content):
            return PolicyResult(
                action=PolicyAction.BLOCK,
                original_content=content,
                reason=f"Never-store content detected: {secret_type}",
                policy_name="never_store",
            )
    return PolicyResult(
        action=PolicyAction.ALLOW,
        original_content=content,
        policy_name="never_store",
    )


def check_tenant_scope(
    content: str, expected_tenant: str, actual_tenant: str
) -> PolicyResult:
    """Enforce tenant isolation at the policy layer."""
    if expected_tenant != actual_tenant:
        return PolicyResult(
            action=PolicyAction.BLOCK,
            original_content=content,
            reason=(
                f"Tenant mismatch: expected={expected_tenant} "
                f"actual={actual_tenant}"
            ),
            policy_name="tenant_isolation",
        )
    return PolicyResult(
        action=PolicyAction.ALLOW,
        original_content=content,
        policy_name="tenant_isolation",
    )


# ---------------------------------------------------------------------------
# Policy pipeline
# ---------------------------------------------------------------------------

class MemoryGuard:
    """
    Pipeline that runs every memory read/write through ordered policy checks.
    Stops at the first non-ALLOW result (fail-fast).

    Usage:
        guard = MemoryGuard(tenant_id="tenant_123")
        result = guard.check_write(content="User email is foo@bar.com")
        if result.action == PolicyAction.ALLOW:
            store.write(content)
        elif result.action == PolicyAction.REDACT:
            store.write(result.processed_content)
        elif result.action == PolicyAction.BLOCK:
            log_blocked(result)
    """

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id

    def check_write(
        self, content: str, requesting_tenant: Optional[str] = None
    ) -> PolicyResult:
        """Run write-path policy pipeline."""
        # 1. Never-store check (highest priority -- block secrets)
        result = check_never_store(content)
        if result.action != PolicyAction.ALLOW:
            logger.warning(
                f"memory_guard_write action={result.action.value} "
                f"policy={result.policy_name} reason={result.reason}"
            )
            return result

        # 2. Tenant isolation check
        if requesting_tenant:
            result = check_tenant_scope(
                content, self.tenant_id, requesting_tenant
            )
            if result.action != PolicyAction.ALLOW:
                logger.warning(
                    f"memory_guard_write action={result.action.value} "
                    f"policy={result.policy_name} reason={result.reason}"
                )
                return result

        # 3. PII redaction (redact but allow storage of cleaned version)
        result = check_pii(content)
        if result.action != PolicyAction.ALLOW:
            logger.info(
                f"memory_guard_write action={result.action.value} "
                f"policy={result.policy_name} reason={result.reason}"
            )
            return result

        return PolicyResult(
            action=PolicyAction.ALLOW,
            original_content=content,
            policy_name="all_passed",
        )

    def check_read(
        self, content: str, requesting_tenant: Optional[str] = None
    ) -> PolicyResult:
        """Run read-path policy pipeline (primarily tenant isolation)."""
        if requesting_tenant:
            result = check_tenant_scope(
                content, self.tenant_id, requesting_tenant
            )
            if result.action != PolicyAction.ALLOW:
                logger.warning(
                    f"memory_guard_read action={result.action.value} "
                    f"policy={result.policy_name} reason={result.reason}"
                )
                return result

        return PolicyResult(
            action=PolicyAction.ALLOW,
            original_content=content,
            policy_name="all_passed",
        )
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Cross-Session Customer Support Agent with Persistent Memory

**Problem statement**: A SaaS company with 500K active users needs an AI customer support agent that remembers user preferences, past issues, and resolution history across sessions. Requirements: sub-3-second response time, GDPR-compliant erasure on request, support for 50 concurrent agents per region, cost under $0.10 per resolved ticket.

**Architecture**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         API GATEWAY / LOAD BALANCER                         │
│                   Rate limiting, auth, tenant extraction                     │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────────────┐
│                         AGENT ORCHESTRATOR                                   │
│                                                                              │
│  ┌──────────────┐  ┌───────────────────┐  ┌──────────────────────────────┐  │
│  │ Model Router  │  │ Context Budget    │  │ Session Manager              │  │
│  │               │  │ Manager           │  │                              │  │
│  │ Simple Q&A:   │  │                   │  │ New session: load user       │  │
│  │  Haiku ($0.25 │  │ Hot: last 10 msgs │  │ profile + last 3 tickets    │  │
│  │  /M tokens)   │  │ Warm: summary of  │  │ from memory store            │  │
│  │               │  │  prior session    │  │                              │  │
│  │ Complex:      │  │ Cold: user prefs  │  │ End session: extract         │  │
│  │  Sonnet ($3   │  │  + history summary│  │ memories via sleep-time      │  │
│  │  /M tokens)   │  │                   │  │ compute (background)         │  │
│  └──────┬───────┘  └─────────┬─────────┘  └──────────────┬───────────────┘  │
└─────────┼────────────────────┼───────────────────────────┼──────────────────┘
          │                    │                           │
┌─────────▼────────────────────▼───────────────────────────▼──────────────────┐
│                       MEMORY PLANE                                           │
│                                                                              │
│  ┌────────────────────┐  ┌─────────────────────┐  ┌──────────────────────┐  │
│  │ Memory Guard        │  │ Retrieval Service   │  │ Write Service        │  │
│  │ (OWASP pattern)     │  │                     │  │                      │  │
│  │                     │  │ Vector search       │  │ Two-phase:           │  │
│  │ Write path:         │  │ (circuit breaker)   │  │  1. Extract worthy   │  │
│  │  1. Never-store     │  │       │              │  │     memories         │  │
│  │  2. Tenant isolation│  │       ▼              │  │  2. Conflict check   │  │
│  │  3. PII redaction   │  │ Keyword fallback    │  │     + self-edit      │  │
│  │                     │  │       │              │  │                      │  │
│  │ Read path:          │  │       ▼              │  │ Metadata:            │  │
│  │  1. Tenant isolation│  │ Three-question       │  │  duration, type,     │  │
│  │  2. Scope filter    │  │ filter               │  │  confidence, scope   │  │
│  └─────────────────────┘  └──────────┬──────────┘  └──────────┬───────────┘  │
└──────────────────────────────────────┼──────────────────────────┼────────────┘
                                       │                          │
┌──────────────────────────────────────▼──────────────────────────▼────────────┐
│                       STORAGE PLANE                                          │
│                                                                              │
│  ┌──────────────────────┐  ┌───────────────────┐  ┌────────────────────┐    │
│  │ PostgreSQL + pgvector │  │ Redis 8.4         │  │ GDPR Erasure       │    │
│  │                       │  │ (Semantic Cache)  │  │ Service            │    │
│  │ Unified store:        │  │                   │  │                    │    │
│  │  - User profiles      │  │ Cache layer:      │  │ On erasure request:│    │
│  │  - Memory entries     │  │  - Prompt cache   │  │  1. Find all user  │    │
│  │  - Embeddings         │  │  - Semantic cache  │  │     memories       │    │
│  │  - Checkpoints        │  │    (cosine 0.93)  │  │  2. Delete entries │    │
│  │                       │  │  - Session state  │  │  3. Re-embed       │    │
│  │ Separate index per    │  │                   │  │     affected docs  │    │
│  │ tenant (not namespace │  │ 2-5ms cache hits  │  │  4. Verify + audit │    │
│  │ filter)               │  │ vs 300-500ms cold │  │     log            │    │
│  └───────────────────────┘  └───────────────────┘  └────────────────────┘    │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │ Audit Log (Append-Only)                                              │    │
│  │ Every memory read, write, delete, conflict resolution.               │    │
│  │ Separate from live memory (not subject to GDPR deletion).            │    │
│  │ Subject-anonymized for consequential decisions.                      │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix**:

```
┌─────────────────────┬───────────────────┬───────────────────┬───────────────┐
│ Decision            │ Option A          │ Option B          │ Chosen + Why  │
├─────────────────────┼───────────────────┼───────────────────┼───────────────┤
│ Memory store        │ Separate vector   │ PostgreSQL +      │ B: single     │
│                     │ DB + relational   │ pgvector          │ operational   │
│                     │ DB                │ (unified)         │ surface,      │
│                     │                   │                   │ ACID for      │
│                     │                   │                   │ GDPR deletes  │
├─────────────────────┼───────────────────┼───────────────────┼───────────────┤
│ Tenant isolation    │ Namespace filter  │ Separate index    │ B: storage-   │
│                     │ in app code       │ per tenant        │ layer boundary│
│                     │                   │                   │ not app-layer │
│                     │                   │                   │ label         │
├─────────────────────┼───────────────────┼───────────────────┼───────────────┤
│ Memory consistency  │ Strong (sync      │ Eventual (async   │ B: user prefs │
│                     │ writes)           │ writes, sleep-    │ are tolerance │
│                     │                   │ time consolidate) │ of staleness; │
│                     │                   │                   │ latency wins  │
├─────────────────────┼───────────────────┼───────────────────┼───────────────┤
│ Model routing       │ Single model      │ Route by          │ B: 67% cost   │
│                     │ for all queries   │ complexity        │ reduction;    │
│                     │                   │ (Haiku/Sonnet)    │ simple Q&A    │
│                     │                   │                   │ does not need │
│                     │                   │                   │ Sonnet        │
├─────────────────────┼───────────────────┼───────────────────┼───────────────┤
│ GDPR audit trail    │ Same store as     │ Separate append-  │ B: erasure    │
│                     │ live memory       │ only log,         │ must not      │
│                     │                   │ subject-          │ destroy audit │
│                     │                   │ anonymized        │ ability       │
└─────────────────────┴───────────────────┴───────────────────┴───────────────┘
```

**Decision rationale**: The unified PostgreSQL + pgvector store was chosen over a separate vector DB because GDPR erasure requires transactional deletes across memory entries, embeddings, and metadata -- a single ACID-compliant store simplifies this dramatically. Tenant isolation is enforced at the storage layer (separate indices) rather than application-layer namespace filters because a filter the application must remember to apply on every query is a label, not a boundary. Model routing (Haiku for simple FAQ, Sonnet for complex troubleshooting) is the single highest-leverage cost optimization, bringing per-ticket cost well under the $0.10 target. Sleep-time compute handles memory consolidation in the background, keeping live-conversation latency under the 3-second SLA.

**Cost estimate**:

```
Per ticket (average 8 turns):
  LLM inference (70% routed to Haiku): 8 * 8K tokens * $0.50/M = $0.032
  LLM inference (30% routed to Sonnet): 8 * 15K tokens * $3/M = $0.036 (weighted)
  Memory retrieval infra: ~$0.002/ticket
  Storage: amortized ~$0.001/ticket
  Total: ~$0.07/ticket  (under $0.10 target)
```

---

### 6.2 Scenario: Multi-Agent Financial Compliance Platform with Coordinated Memory

**Problem statement**: A bank needs a multi-agent system for transaction monitoring, where 12 specialized agents (fraud detection, AML screening, sanctions checking, risk scoring, etc.) must coordinate on shared state while maintaining strict audit trails. Requirements: strong consistency for compliance-critical decisions, sub-500ms inter-agent state propagation, zero cross-client data leakage, full regulatory audit trail, resilience to single-agent failure without cascading corruption.

**Architecture**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    TRANSACTION INGESTION LAYER                               │
│            Real-time event stream (Kafka) + batch reconciliation             │
└─────────────────────────────────────┬───────────────────────────────────────┘
                                      │
┌─────────────────────────────────────▼───────────────────────────────────────┐
│                    AGENT COORDINATION LAYER                                  │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐   │
│  │                   Central State Store (Strong Consistency)             │   │
│  │                                                                       │   │
│  │  Transaction state: PENDING -> SCREENING -> SCORING -> DECISION      │   │
│  │  Sync writes: every agent write blocks until persisted                │   │
│  │  Event sourcing: append-only log of all state transitions             │   │
│  │  Conflict resolution: last-writer-wins with vector clock ordering     │   │
│  └───────────────────────┬───────────────────────────────────────────────┘   │
│                          │                                                   │
│  ┌───────────┐ ┌────────▼────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │ Fraud      │ │ AML         │ │ Sanctions│ │ Risk     │ │ Compliance   │  │
│  │ Detection  │ │ Screening   │ │ Checker  │ │ Scorer   │ │ Reporter     │  │
│  │ Agent      │ │ Agent       │ │ Agent    │ │ Agent    │ │ Agent        │  │
│  │            │ │             │ │          │ │          │ │              │  │
│  │ Private    │ │ Private     │ │ Private  │ │ Private  │ │ Private      │  │
│  │ memory:    │ │ memory:     │ │ memory:  │ │ memory:  │ │ memory:      │  │
│  │ fraud      │ │ AML         │ │ OFAC/EU  │ │ scoring  │ │ reporting    │  │
│  │ patterns,  │ │ typologies, │ │ lists,   │ │ models,  │ │ templates,   │  │
│  │ velocity   │ │ PEP data,   │ │ match    │ │ risk     │ │ regulatory   │  │
│  │ profiles   │ │ thresholds  │ │ history  │ │ factors  │ │ requirements │  │
│  └─────┬─────┘ └──────┬──────┘ └────┬─────┘ └────┬─────┘ └───────┬──────┘  │
│        │              │             │             │               │          │
│  ┌─────▼──────────────▼─────────────▼─────────────▼───────────────▼──────┐   │
│  │              Memory Isolation & Access Control                        │   │
│  │                                                                       │   │
│  │  Per-agent scoped reads: agent sees ONLY its private memory           │   │
│  │  + explicitly shared global state (transaction status, flags)         │   │
│  │                                                                       │   │
│  │  Write permissions:                                                   │   │
│  │   - Private memory: agent writes freely                              │   │
│  │   - Central state: write via CQRS command, validated by orchestrator  │   │
│  │   - Cross-agent signal: publish to event bus, not direct memory write │   │
│  └───────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────────────┐
│                       STORAGE & AUDIT LAYER                                  │
│                                                                              │
│  ┌──────────────────┐  ┌────────────────────┐  ┌────────────────────────┐   │
│  │ PostgreSQL        │  │ Event Store        │  │ Regulatory Audit       │   │
│  │ (Central State)   │  │ (Append-Only)      │  │ Trail                  │   │
│  │                   │  │                    │  │                        │   │
│  │ Sync durability,  │  │ Every agent        │  │ Immutable, tamper-     │   │
│  │ serializable      │  │ action, every      │  │ evident log of:        │   │
│  │ isolation for     │  │ state transition,  │  │  - Every decision      │   │
│  │ compliance-       │  │ full replay        │  │  - Evidence used       │   │
│  │ critical writes   │  │ capability for     │  │  - Agent that decided  │   │
│  │                   │  │ regulatory review  │  │  - Confidence score    │   │
│  │ Client-level      │  │                    │  │  - Memory state at     │   │
│  │ partitioning      │  │ Retention: 7 years │  │    decision time       │   │
│  │ (not namespace    │  │ (regulatory req)   │  │                        │   │
│  │ filter)           │  │                    │  │ Retention: 10 years    │   │
│  └──────────────────┘  └────────────────────┘  └────────────────────────┘   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │ Poisoning Defense Layer                                              │    │
│  │                                                                      │    │
│  │ Memory contracts: each agent declares what it can believe            │    │
│  │ Provenance tracking: every memory entry traced to source interaction │    │
│  │ Belief drift detection: monitor for gradual threshold/policy shifts  │    │
│  │ Behavioral analysis: full-session anomaly detection (not per-turn)   │    │
│  │ Quarantine: suspicious memories isolated, flagged for human review   │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix**:

```
┌─────────────────────┬───────────────────┬───────────────────┬───────────────┐
│ Decision            │ Option A          │ Option B          │ Chosen + Why  │
├─────────────────────┼───────────────────┼───────────────────┼───────────────┤
│ Consistency model   │ Eventual (lower   │ Strong/sync       │ B: compliance │
│                     │ latency, risk of  │ (higher latency,  │ decisions must│
│                     │ stale reads)      │ serializable)     │ never use     │
│                     │                   │                   │ stale state   │
├─────────────────────┼───────────────────┼───────────────────┼───────────────┤
│ Inter-agent comms   │ Direct memory     │ Event bus +       │ B: prevents   │
│                     │ sharing (agents   │ CQRS commands     │ cascade       │
│                     │ write to shared   │ validated by      │ poisoning;    │
│                     │ store)            │ orchestrator      │ single agent  │
│                     │                   │                   │ cannot corrupt│
│                     │                   │                   │ others        │
├─────────────────────┼───────────────────┼───────────────────┼───────────────┤
│ Memory topology     │ Centralized only  │ Hybrid: central   │ B: central    │
│                     │ (all agents share │ global + private   │ for shared    │
│                     │ everything)       │ per-agent          │ state, private│
│                     │                   │                   │ for domain-   │
│                     │                   │                   │ specific data │
├─────────────────────┼───────────────────┼───────────────────┼───────────────┤
│ Failure isolation   │ Retry failed      │ Circuit breaker   │ B: a failed   │
│                     │ agent indefinitely│ + quarantine +     │ agent must not│
│                     │                   │ fallback to human  │ block the     │
│                     │                   │ review queue       │ pipeline      │
├─────────────────────┼───────────────────┼───────────────────┼───────────────┤
│ Audit retention     │ Same DB as live   │ Separate immutable │ B: regulatory │
│                     │ state (simpler    │ audit store (7-10  │ requires      │
│                     │ ops)              │ year retention)    │ tamper-evident│
│                     │                   │                   │ records       │
│                     │                   │                   │ independent   │
│                     │                   │                   │ of live store │
└─────────────────────┴───────────────────┴───────────────────┴───────────────┘
```

**Decision rationale**: Strong consistency is non-negotiable for financial compliance -- an agent acting on stale transaction status could miss a sanctioned entity or double-clear a flagged transaction. The CQRS pattern with a validating orchestrator prevents the cascade poisoning problem: agents publish intentions via the event bus, but the orchestrator validates and serializes writes to central state. This means a single compromised agent (the 87%-within-4-hours scenario) cannot directly corrupt shared memory. Each agent maintains private memory for domain-specific knowledge (fraud patterns, AML typologies) that does not need cross-agent sharing, reducing contention on the central store. The separate audit trail with 7-10 year retention satisfies regulatory requirements while keeping the live state store lean. Memory contracts -- where each agent declares what classes of facts it can believe -- provide the primitive missing from current frameworks for defending against belief drift and incremental escalation attacks.

**Key resilience mechanisms**:

1. **Cascade prevention**: agents communicate via event bus, not direct memory writes. The orchestrator validates every write to central state, preventing a poisoned agent from corrupting shared memory.

2. **Failure containment**: per-agent circuit breakers isolate failures. If the sanctions-checking agent goes down, transactions queue for that check while fraud detection and AML screening continue. Fallback: route to human review queue.

3. **Behavioral monitoring**: detection requires analysis across the full session, not individual interactions. Gradual threshold manipulation (incrementally relaxing authorization limits) is only visible when analyzed as a trajectory.

4. **Replay capability**: event sourcing enables full replay of any transaction's decision chain for regulatory review. Every agent action, every state transition, every memory read that contributed to a decision is reconstructable.

---

## Quick Reference: Production Checklist

1. Separate state from memory -- state updates immediately; memory updates only after changes appear stable.
2. Checkpoint and roll back only affected steps -- never restart from scratch.
3. Keep memory outside the prompt; retrieve selectively -- metadata-based filtering.
4. Budget context carefully -- summarize old checkpoints, drop stale summaries.
5. Let the system of record win over stored memory.
6. Balance cost, latency, and reliability -- you cannot optimize all three simultaneously (Latency-Consistency-Cost triangle).
7. Monitor four metrics from day one: retrieval hit rate, token usage/turn, latency/turn, memory growth.
8. Add safeguards: freshness rules, approval before long-term writes, scope-aware updates.
9. Design memory architecture before writing agent code: where does shared state live? Which agents can see what? What happens when two agents disagree about a fact?
10. Treat memory as a governed data asset -- threat-model the memory store, not just the agent.

---

## Quick Reference: Benchmark Scores (2026)

```
┌──────────────┬─────────────────────────────────────────┬──────────────────────┐
│ Benchmark    │ Tests                                   │ Leading Score        │
├──────────────┼─────────────────────────────────────────┼──────────────────────┤
│ LoCoMo       │ Single-hop, temporal, multi-hop,        │ Mem0: 92.5           │
│              │ open-domain memory                      │ (Apr 2026)           │
├──────────────┼─────────────────────────────────────────┼──────────────────────┤
│ LongMemEval  │ Complex temporal reasoning,             │ Zep: +18.5% over    │
│              │ enterprise use cases                    │ baselines            │
├──────────────┼─────────────────────────────────────────┼──────────────────────┤
│ DMR          │ Long-conversation memory recall         │ Zep: 94.8%          │
│              │                                         │ MemGPT: 93.4%       │
├──────────────┼─────────────────────────────────────────┼──────────────────────┤
│ Terminal-    │ Coding agent persistence across         │ Letta Code: #1       │
│ Bench        │ sessions                                │ model-agnostic       │
└──────────────┴─────────────────────────────────────────┴──────────────────────┘
```

## Quick Reference: Framework Selection (2026)

```
┌────────────┬──────────────────────┬───────────────────────────────────────────┐
│ Framework  │ Best For             │ Key Trade-off                             │
├────────────┼──────────────────────┼───────────────────────────────────────────┤
│ LangMem    │ Already on LangGraph │ Tight coupling to LangGraph ecosystem     │
├────────────┼──────────────────────┼───────────────────────────────────────────┤
│ Mem0       │ Managed service,     │ Broadest adoption (48K+ stars); less      │
│            │ minimal infra        │ temporal depth than Zep                   │
├────────────┼──────────────────────┼───────────────────────────────────────────┤
│ Zep/       │ Temporal reasoning,  │ Best temporal accuracy (+15pts on         │
│ Graphiti   │ fact evolution       │ LongMemEval); requires graph DB ops       │
├────────────┼──────────────────────┼───────────────────────────────────────────┤
│ Letta      │ Full agent platform  │ Not a memory layer -- it IS the stack;    │
│ (MemGPT)   │ with built-in memory │ memory quality depends on model judgment  │
├────────────┼──────────────────────┼───────────────────────────────────────────┤
│ In-context │ Short sessions,      │ Simplest; fails beyond ~20 turns;         │
│ only       │ simple tasks         │ no cross-session persistence              │
└────────────┴──────────────────────┴───────────────────────────────────────────┘
```
