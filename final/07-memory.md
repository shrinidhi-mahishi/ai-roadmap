# Module 07: Memory

## What Is This?

LLMs are **stateless** -- every API call starts completely fresh with zero memory of previous calls. If you chat with Claude and then send a new message, the model doesn't "remember" your earlier conversation. The application has to re-send the entire conversation history each time.

**Agent memory** is the system that solves this. It comes in two forms:
- **Short-term memory (STM)**: The current conversation -- recent messages kept in the context window. Like your desk: whatever you're actively working on right now.
- **Long-term memory (LTM)**: Facts and experiences stored in an external database, retrieved when relevant. Like a filing cabinet: things you wrote down months ago that you pull out when needed.

For example, if a customer support agent helped a user with a billing issue last month, LTM lets the agent recall "this user had a billing dispute on June 5 about order #1234" when the user returns -- even though that conversation is long gone from the context window.

The key trade-off: you could just replay the entire conversation history every time (full-context), and this actually gives the best accuracy. But it gets expensive fast -- at 10M monthly users, full-context replay is economically infeasible. Memory systems let you store the important bits cheaply and retrieve them on demand.

## Why It Matters

Memory is what makes an agent feel like a persistent assistant rather than a goldfish. Without memory, every interaction starts from scratch. With memory, agents build up knowledge about users, learn from past mistakes, and maintain context across sessions.

---

## 2. Core Concepts

### Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                 AGENT MEMORY ARCHITECTURE (CoALA)                │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │           WORKING MEMORY / STM  (the prompt)              │  │
│  │  ┌──────────┐ ┌───────────┐ ┌────────┐ ┌──────────────┐  │  │
│  │  │ System   │ │Msg Buffer │ │ Tool   │ │Pinned Facts  │  │  │
│  │  │ Prompt   │ │ (last k)  │ │Schemas │ │(core blocks) │  │  │
│  │  └──────────┘ └───────────┘ └────────┘ └──────────────┘  │  │
│  └──────────────────────┬───────────────────────────────────┘   │
│            READ: query ─┴──> retrieve ──> rerank ──> inject     │
│                 │               │               │               │
│  ┌──────────────v──┐ ┌─────────v─────────┐ ┌───v─────────────┐ │
│  │   SEMANTIC LTM  │ │   EPISODIC LTM    │ │  PROCEDURAL LTM │ │
│  │  (user facts,   │ │  (conversation    │ │  (system prompt, │ │
│  │   preferences)  │ │   logs, traces,   │ │   tool defs,     │ │
│  │  Vector DB /    │ │   checkpoints)    │ │   skills)        │ │
│  │  Mem0 / Zep     │ │  Graph DB / Store │ │  CLAUDE.md       │ │
│  └────────┬────────┘ └────────┬──────────┘ └─────────────────┘ │
│           └────────────┬──────┘                                  │
│           WRITE: experience ──> extract ──> store                │
│           (LLM extract, entity resolve, sleep-time consolidate)  │
└──────────────────────────────────────────────────────────────────┘
```

### The CoALA Framework (Cognitive Architectures for Language Agents)

The best mental model for agent memory comes from CoALA (Sumers et al., TMLR 2024). Think of it like your own brain:

- **Working Memory** = your desk. Whatever you are actively thinking about right now. In agents, this is the prompt: message buffer, system prompt, tool schemas, and any pinned facts. Hot, expensive per token, limited by context window.
- **Semantic Memory** = your knowledge. Facts you know: "Paris is the capital of France," "this user is vegetarian," "policy P-12 requires dual control." In agents: user profiles, preference stores, knowledge graphs.
- **Episodic Memory** = your autobiography. Things that happened: "yesterday the user complained about order #123," "last week we discussed the Q3 report." In agents: conversation logs, trajectories, checkpoints.
- **Procedural Memory** = your muscle memory. How to do things: riding a bike, writing SQL. In agents: system prompts, tool definitions, skills, CLAUDE.md files.

**Key invariant**: The LLM is **not** the memory. Working memory is a data structure the prompt is compiled from; long-term memory is read via retrieval and written via learning. The model never "searches" -- it emits a tool call or the control plane runs a retriever; observations return as tokens.

### Write Path vs Read Path

A production memory system is two independently scaled planes sharing durable stores, just like the ingest/query split in RAG:

| Plane | Owns | Typical Components | Failure If Coupled |
|-------|------|-------------------|--------------------|
| **Write (ingest/learning)** | Extract facts, stamp ACL + tenant + user_id, entity-resolve, conflict/invalidation, embed, graph insert, consolidate, forget | Session workers, extraction LLMs, graph builders, sleep-time agents, checkpoint writers | Query p99 tracks extract; a 600k-token graph rebuild makes "just-added" memories unsearchable for hours |
| **Read (retrieve/assemble)** | Authz pre-filter, hybrid retrieve, recency/importance score, rerank, budget into window, generate | ANN + BM25 + graph BFS, RRF, cross-encoder, memory blocks pinned in-context | Write schema change silently mismatches query embeddings |

### Semantic vs Episodic (Do Not Collapse Them)

**Semantic memory** answers "what is true of this user/world now?": vegetarian, works at Acme, policy P-12 requires dual control. Updates are upserts with a conflict policy.

**Episodic memory** answers "what happened, in what order, with what evidence?": conversation logs, trajectories, tool-call traces. Episodes are the non-lossy provenance for semantic edges.

**Why you need both**: Destroying episodes while keeping facts breaks citation, unlearning, and audit. If a user exercises GDPR Art. 17 (right to erasure), you need the episode-to-fact provenance map to find and delete all derived data.

### Hot / Warm / Cold Tiers

| Tier | Latency Target | Contents | Typical Backing |
|------|----------------|----------|-----------------|
| **Hot** | p50 <50-200 ms of assembly + model TTFT | System prompt, core blocks, last-N messages, user profile card | In-process / Redis / pinned tokens |
| **Warm** | p50 100-400 ms search | Semantic facts, recent episodes, entity subgraph | pgvector, Neo4j, Mem0/Zep APIs |
| **Cold** | seconds-hours | Full traces, old episodes, community rebuilds, object-store files | Object storage, warehouse, Graphiti community refresh |

---

## 3. How It Works

### 3.1 Short-Term / Working Memory

Working memory is what is in the prompt right now. It is the most expensive memory (every token is billed) and the most impactful (the model sees it on every turn).

**Conversation buffer (full history)**: Store every turn unfiltered. Linear growth; zero information loss until the window overflows. Simple mental model but unsustainable for long sessions.

**Window (k turns)**: Keep the last k human/AI pairs (FIFO). Constant RAM but drops early constraints ("I'm vegetarian" on turn 2 of a 200-turn chat). Use when recency is the task (IVR, short tickets). Do not use as the only memory for identity or policy.

**Token-budgeted buffer**: Drop oldest messages until `max_token_limit`. Strictly better than k turns when message size varies (tool dumps vs "ok"). LangChain Core `trim_messages(strategy="last", max_tokens=N, include_system=True, start_on="human")` is the 2025-26 primitive.

**Summarization as compression**: When cumulative tokens exceed a threshold, replace the prefix with `[summary] + remaining_messages`, carrying a running_summary. Compaction is lossy -- it invalidates prompt-cache prefixes.

**Anthropic Three-Layer STM (Messages API, 2025-26)**

| Primitive | Trigger | What It Does | Trade-off |
|-----------|---------|-------------|-----------|
| Tool-result clearing | 100k input tokens; keep last 3 tool uses | Mechanical delete of stale tool exhaust; placeholder remains | Cheapest; lossless for refetchable results |
| Compaction | 150k tokens (min 50k) | Server summarizes prefix into a compaction block; later requests drop everything before it | Lossy; invalidates cached prefix |
| Memory tool | Model-initiated | Client executes view/create/str_replace/insert/delete/rename under `/memories` | Survives compaction; storage is your infra |

Anthropic's eval (Sonnet 4.5): context editing alone +29% task performance and -84% tokens vs baseline; editing + memory tool +39%. The 84% is tool-exhaust deletion, not summarization.

**LangGraph STM = Checkpointer**: `PostgresSaver` / `RedisSaver` snapshot graph state per `thread_id`. That is conversation continuity, HITL interrupts, time-travel, and crash recovery -- not user profiles. MongoDB checkpointers: 16 MB document cap vs Postgres ~1 GB/field.

**Google ADK STM**: Session is the conversation/thread container, State is session-scoped scratch data, and Memory is a separate searchable cross-session store managed by a MemoryService. The clearest documented distinction between working memory and durable memory in current frameworks.

**OpenAI Agents SDK STM**: Session history persisted before/after runs. `previous_response_id` continuations carry prior turns without resending. For durable execution, points to Temporal, Dapr, Restate, DBOS.

**Letta STM**: In-context message buffer + pinned memory blocks. `max_message_buffer_length` is best-effort. Recall memory = searchable full conversation history (episodic); not pinned.

### 3.2 Long-Term Memory Products (2025-26)

**Letta (formerly MemGPT)** -- Packer et al. 2023: virtual context management -- main context (RAM) vs external context (disk), with the agent issuing OS-like page-in/page-out tool calls.

| Tier | Mechanism | Limits |
|------|-----------|--------|
| **Core / blocks** | Labeled, always-in-context strings; agent tools `memory_insert`/`memory_replace`/`memory_rethink`; shareable across agents | Recommended <50k chars/block, <20 blocks/agent |
| **Files** | Open/close + grep + semantic search | 5 MB/file, recommended <100 files |
| **Archival** | `archival_memory_insert`/`_search`; agent-curated facts | ~300 tokens/passage; unlimited count |
| **External RAG/MCP** | Custom tools | Unlimited |

**Sleep-time agents** (Letta 0.7+, arXiv:2504.13171): A background agent owns write tools for core blocks; the primary agent talks and searches recall/archival. Default frequency: every 5 primary steps. The paper shows ~5x less test-time compute for same accuracy on stateful benchmarks; scaling sleep-time +13%/+18% accuracy; 2.5x lower average cost when 10 queries share one precomputed context. Shared blocks are last-write-wins -- concurrent edits lose data unless serialized.

**Mem0** -- Two eras (do not mix scores):

*Paper (arXiv:2504.19413)*: Pair-wise extract from conversation pairs with summary + last m=10 messages -> candidate facts -> retrieve top s=10 similar memories -> LLM tool-call ADD/UPDATE/DELETE/NOOP. Mem0g: Neo4j directed labeled graph. Retrieval: Mem0 1,764 tokens; full-context 26,031 tokens. Construction: Mem0 ~7k tok/conversation, Mem0g ~14k, Zep >600k.

*Platform v3 (2026)*: Single-pass ADD-only (no UPDATE/DELETE at extract), native entity graph, hybrid semantic + BM25 + entity boost, temporal ranking. LoCoMo 92.5 (was 71.4) at 7.0k tok, p50 0.88s; LongMemEval 94.4 (was 67.8) at 6.8k tok, p50 1.09s. Scores are the managed platform; OSS SDK is "directionally similar, not identical."

**Zep + Graphiti** -- Three-tier temporal KG (arXiv:2501.13956):

1. **Episode subgraph** -- Raw messages/text/JSON + reference time; non-lossy; speaker auto-extracted as entity.
2. **Semantic entity subgraph** -- Entities (1024-d name embeddings + full-text) and fact edges; hybrid cosine + BM25; Cypher writes.
3. **Community subgraph** -- Label propagation (not Leiden); map-reduce summaries; periodic refresh.

**Bi-temporal model**: Valid time (`valid_at`/`invalid_at`) vs transaction time (`created`/`expired`). Contradiction -> set old edge `invalid_at`, do not delete. Point-in-time queries are first-class. Retrieval: search (cosine + BM25 + BFS) -> rerank (RRF, MMR, mention frequency, node-distance, cross-encoder) -> constructor (facts with date ranges + entity summaries + community summaries).

Paper LongMemEval_S: gpt-4o 71.2% vs full-context 60.2% (+18.5 pp), latency 2.58s vs 28.9s, context 1.6k vs 115k. Vendor site 2026: LoCoMo 94.7% at 155ms, LongMemEval 90.2% at 162ms.

**LangGraph Store**: Cross-thread JSON `(namespace, key) -> item`. Namespaces typically `("memories", user_id)`. `PostgresStore` with optional pgvector index for semantic search. `ttl=` requires `start_ttl_sweeper()`. No per-memory SKU -- your Postgres/Redis bill + embedding calls.

**OpenAI ChatGPT Memory** (product, not API): Apr 2024 saved memories; Jun 2026 Dreaming V3 (reviewable memory summary; ~5x cheaper dreaming compute). No Memory API on Chat Completions/Responses. Do not design enterprise agents assuming ChatGPT memory is callable.

**Cognee 1.0 (2026)**: Four verbs: `.remember` (ingest -> graph or session cache), `.recall` (auto-route hybrid: graph + vectors + BM25, RRF), `.improve` (bridge session -> permanent graph, feedback weights), `.forget` (item/dataset/user). MCP + LangGraph integrations first-party.

### 3.3 Retrieval: Salience, Recency, Importance

**Generative Agents scoring formula** (Park et al., UIST 2023 -- still the template copied in 2026):

```
score = alpha_r * recency + alpha_i * importance + alpha_v * relevance
```

All alpha = 1 after min-max to [0,1]. Recency = exponential decay 0.995 per sandbox hour since last access. Importance = LLM integer 1-10 ("brushing teeth" vs "breakup"). Relevance = cosine(query embedding, memory embedding). Top memories that fit the window go into the prompt.

**Production signal mappings**:

| Signal | Who Uses It | Mechanism |
|--------|-------------|-----------|
| Relevance | Everyone | Dense cosine; Mem0 v3 + BM25 + entity match fused |
| Recency | Graphiti valid_at; Qdrant decay; Mem0 temporal ranker | Soft rank vs hard status=current filter |
| Importance/salience | Generative Agents LLM score; Letta agent insert decision; Mem0 extract LLM | Write-time vs read-time |
| Graph distance | Graphiti node-distance reranker; Cognee traversal | Localize to user/org subgraph |
| RRF | Graphiti, Cognee, hybrid stacks | Fuse lexical + dense + graph lists, k=60 typical |

**Write-time vs read-time salience**: Write-time filters (consistency checks, origin tags) kill L1 poisoning but miss L2/L3 compositional attacks. Read-time must re-score in the current query context.

### 3.4 Consolidation (Sleep-time / Dreaming)

All consolidation is batch LLM jobs on the write plane. The insight: write-time notes rot (preferences change, facts become stale). Background processing refreshes them.

- **Letta sleep-time**: Background agent rewrites core blocks while primary agent serves users. ~5x less test-time compute. 2.5x lower average cost when 10 queries share precomputed context.
- **OpenAI Dreaming V3**: Background synthesis. ~5x cheaper compute than prior dreaming. Reviewable memory summary in UI.
- **Mem0 Dream**: Pro SKU. Consolidation of accumulated memories.
- **Cognee .improve**: Bridge session -> permanent graph with feedback weights.
- **Graphiti community refresh**: Map-reduce summaries over entity subgraphs.

### 3.5 Forgetting

**MemoryBank** (Zhong et al., AAAI 2024): Ebbinghaus-style strength -- recall reinforces, idle decays.

**Graphiti**: Invalidate, do not delete (history preserved for point-in-time queries).

**Mem0 v3**: ADD-only accumulation. Forgetting is a separate product/policy problem.

**LangGraph Store**: TTL + Redis checkpoint TTL for automatic expiry.

**GDPR erasure is NOT Ebbinghaus** -- see Security section.

### 3.6 Trajectories and Agentic Memory

**A-Mem** (Xu et al., NeurIPS 2025): Zettelkasten notes (keywords, tags, contextual description) + LLM link generation + evolution of neighbor notes on insert. Retrieve top-k plus linked neighbors. LoCoMo: A-Mem avg F1 rank 1.6 at 1,216 tokens vs MemGPT 16,987 -- denser notes, still no first-class forgetting.

Agent run traces (tool calls, observations, rewards) are episodic. Reflexion/A-Mem write lessons back as semantic or procedural memory.

---

## 4. Key Patterns & Best Practices

### Memory Architecture Decision Tree

1. **Split STM from LTM**. Thread/session state (checkpointer) is not user profile (store). LangGraph already separates these in the API.
2. **Split semantic from episodic; keep pointers**. You need episodes for audit, unlearning, and citation.
3. **Budget constructor tokens, not top-k**. Zep 1.6k vs Mem0 ~7k vs 115k full-context is the real product decision.
4. **Run consolidation off the user path**. Sleep-time/dreaming should be async, not blocking TTFT.
5. **Put authz on the retrieve path before ANN**. Memory poisoning and tenant leak are the same bug class.
6. **Prove Art. 17 on a staging clone** of prod indexes, including HNSW compaction and traces.

### Framework Memory Patterns

| Memory Pattern | Best Fit | Strengths | Trade-offs |
|---------------|----------|-----------|------------|
| Thread/session memory | Multi-turn chat, approvals, active workflow | Simple continuity, checkpoint/resume | Grows prompt cost; context-window degradation |
| Long-term semantic store | Durable user/org facts, preferences, policies | Reusable across sessions | Requires validation, versioning, auth |
| Exact-prefix cache | Stable large prefixes: policies, schemas, docs | Deterministic cost savings | Brittle to serialization drift |
| Semantic cache | FAQ/support with paraphrased repeats | High hit-rate for near-duplicates | False-positive reuse risk |
| Retrieval memory (RAG) | Large mutable corpora, domain knowledge | Can refresh without retraining | Quality depends on candidate recall |
| Graph memory | Corpus-wide reasoning, entity-centric discovery | Structured entities/relations/communities | Heavy indexing cost unless LazyGraphRAG |

### Choosing a Memory Product

| Requirement | Prefer | Avoid | Why |
|-------------|--------|-------|-----|
| Sub-200 ms retrieve, temporal facts, CRM+chat | Zep/Graphiti | Full-context; STM-only | Bi-temporal + constructor; vendor retrieve 155-162 ms |
| Ship memory this week, conversational personalization | Mem0 platform | Rolling your own Neo4j | v3 hybrid + SKU; OSS if you accept score gap |
| Agent must self-edit always-on persona + ADE debug | Letta blocks + sleep-time | Vector-only RAG as "memory" | Memory = context engineering; sleep-time owns writes |
| Multi-tenant LangGraph app, HITL, time-travel | Checkpointer + Store | ConversationBufferMemory | Deprecated; thread vs user scopes differ |
| Cross-session files Claude already understands | memory_20250818 + encrypted bucket | Putting PII in the window | Survives compaction; ZDR is your job |
| Point-in-time + audit "what did we believe on date D?" | Graphiti invalidation + episode provenance | Physical DELETE of facts | Need both PIT and Art. 17 hard-delete |
| Lowest generator $ at 26k-turn histories | Extractive memory (~2-7k tok) | Stuffing 26k-115k | Paper: 91% p95 cut vs full-context |
| Strict erasure + no residual HNSW | Per-user crypto keys or per-user indexes | Shared index + metadata filter | Soft-delete is not Art. 17 |
| Poison-resistant writes | Origin-bound IFC (TMA-NM class) | LLM "is this a good memory?" as sole gate | Laundering via summary/tool echo |

---

## 5. System Design Considerations

### Durable Stores and Checkpoints

| System | Durability Primitive | Isolation Key | Resume/Time-Travel |
|--------|---------------------|---------------|---------------------|
| LangGraph checkpointer | Postgres WAL / Redis AOF+repl | thread_id | Replay from checkpoint; delete_thread() |
| LangGraph Store | Postgres rows / RedisJSON | namespace tuple | TTL sweeper; no built-in CRDT |
| Letta | DB-backed agent state | agent_id | Messages survive compaction; ADE inspect |
| Graphiti/Zep | Graph DB + indexes | user/session graph | Point-in-time via valid_at |
| Anthropic memory tool | Your FS/DB | Your mapping | Beta Memory Stores: memory_version_id + SHA-256 |
| Mem0 platform | Managed vector+graph | user_id / agent / run | ADD-only = easier replay, harder correction |

### Conflict Resolution

- **Letta shared blocks**: Last write wins. Concurrent primary + sleep-time edits lose data unless serialized.
- **Graphiti**: LLM contradiction check, temporal invalidation, new information prioritized.
- **Mem0 paper**: LLM chooses ADD/UPDATE/DELETE/NOOP against top-s neighbors.
- **Mem0 v3**: Never overwrite. Conflicts become extra rows; read-time temporal ranker picks "current."
- **LangGraph Store**: `put` replaces key; no merge function.
- **Anthropic Memory Stores**: `content_sha256` precondition for optimistic version checks.

**Design principle**: Per-user single-writer or optimistic version checks.

### Circuit Breakers and Degraded Modes

Memory is on the critical path of personalization but should NOT be on the critical path of "can the agent answer at all."

| Dependency | Breaker | Degraded Mode |
|------------|---------|---------------|
| Memory retrieve (Mem0/Zep/Store) | Timeout ~200-500 ms; fail open | Serve STM only + cached profile card; log memory_miss |
| Memory write | Queue; do not block user turn | Ack user; extract async; idempotent add |
| Embedder | Pin model+version | Stale embedder = silent recall collapse |
| Graph construction | Backpressure | Read episodes (non-lossy) if facts not ready |
| Sleep-time / dreaming | Max parallel jobs per tenant | Skip consolidation; accept messier blocks |

### Unbounded Growth Controls

Without policy, episodic stores grow linearly with turns; semantic stores grow with extractions; graphs grow with entities x facts.

- Redis/Store TTL for automatic expiry
- Letta archival is unlimited by design (must add your own GC)
- Graphiti invalidation without delete (history grows)
- Mem0 v3 ADD-only (same growth problem)
- Shallow checkpointers (latest checkpoint only)
- Conversation-search vs archival split

**Capacity NFR is a product decision**, not a library default.

### Prompt Cache Interactions

STM prefix (system + core blocks + stable profile) should be the cached prefix. Compaction and block rewrites break the cache. Sleep-time that rewrites core memory mid-session trades personalization for cache hit rate.

Cache billing:
- OpenAI: cached reads 0.1x, writes 1.25x, minimum 1,024 tokens, TTL configurable to 30 min
- Anthropic: cached reads 0.1x, 5-minute write 1.25x, 1-hour write 2x
- Gemini: discounted cache-use tokens + storage rent per million tokens per hour

### Capacity Planning Formulas

```
working_memory_prompt
  ~= recent_turn_tokens
   + retrieved_memory_tokens
   + retrieved_document_tokens
   + tool_schema_tokens
```

```
memory_augmented_run_cost
  ~= uncached_input_tokens * input_rate
   + cached_read_tokens * cached_rate
   + cache_write_tokens * write_rate
   + output_tokens * output_rate
   + retrieval_or_rerank_surcharges
```

```
cache_break_even_uses
  ~= first reuse for 1.25x write / 0.1x read caches
```

---

## 6. Code Examples

### LangGraph: STM (Trim) + LTM (Store)

```python
from langgraph.graph import StateGraph, MessagesState
from langgraph.store.postgres import PostgresStore
from langchain_core.messages import trim_messages

# STM: Token-budgeted message trimming
trimmed = trim_messages(
    messages,
    strategy="last",
    max_tokens=8000,
    include_system=True,
    start_on="human",           # Never start on an AI message
    token_counter=model,        # Or 'approximate' on hot path
)

# LTM: Cross-thread semantic store
store = PostgresStore(
    conn_string="postgresql://...",
    index={                    # Enable semantic search
        "dims": 1536,
        "embed": embedding_model,
        "fields": ["text"],
    },
)
store.setup()                  # Call once

# Write a memory
store.put(
    namespace=("memories", user_id),
    key="dietary_preference",
    value={"text": "User is vegetarian since 2024-01"},
)

# Retrieve memories
memories = store.search(
    namespace=("memories", user_id),
    query="food preferences",
    limit=5,
)
```

### Anthropic Memory Tool Pattern

```python
# The memory tool survives compaction -- use it for durable facts
response = client.messages.create(
    model="claude-sonnet-5-20260620",
    max_tokens=8192,
    betas=["memory-2025-08-18"],
    system=[{
        "type": "text",
        "text": "You have a memory tool for persistent facts."
    }],
    tools=[{
        "type": "memory",
        "memory": {
            "memory_store_id": store_id,
            "instructions": "Store user preferences and key decisions."
        }
    }],
    messages=messages,
)
# Memory operations: view, create, str_replace, insert, delete, rename
# Files persist in YOUR storage, not Anthropic's
```

### Generative Agents Retrieval Scoring

```python
import numpy as np

def retrieve_memories(query: str, memories: list, k: int = 10) -> list:
    """
    Generative Agents scoring: recency + importance + relevance.
    All signals min-max normalized to [0,1], equal weights.
    """
    now = time.time()
    query_vec = embed(query)

    scores = []
    for m in memories:
        # Recency: exponential decay, 0.995 per hour since last access
        hours_since = (now - m.last_accessed) / 3600
        recency = 0.995 ** hours_since

        # Importance: LLM-rated 1-10 at write time
        importance = m.importance / 10.0

        # Relevance: cosine similarity
        relevance = cosine_sim(query_vec, m.embedding)

        scores.append((m, recency + importance + relevance))

    # Return top-k that fit the token budget
    scores.sort(key=lambda x: x[1], reverse=True)
    return [m for m, _ in scores[:k]]
```

### Mem0 Integration

```python
from mem0 import MemoryClient

client = MemoryClient(api_key="...")

# Add memories (v3: ADD-only, no UPDATE/DELETE at extract time)
client.add(
    messages=[
        {"role": "user", "content": "I'm vegetarian and work at Acme Corp"},
        {"role": "assistant", "content": "Noted! I'll remember that."},
    ],
    user_id="user_123",
)

# Retrieve memories (hybrid: semantic + BM25 + entity boost)
memories = client.search(
    query="What are the user's dietary preferences?",
    user_id="user_123",
    limit=10,
)
# Returns ~7k tokens vs 26k+ full-context
```

---

## 7. Common Pitfalls & Failure Modes

| Failure | Mechanism | Blast Radius | Mitigation |
|---------|-----------|-------------|------------|
| **Memory poisoning** | User, retrieved doc, or webpage causes a write of a false belief; later read steers tools | Cross-session, cross-site | Origin tags + HMAC; never treat retrieved text as write-authorized; separate "observation" vs "belief" stores |
| **Sleeper / L3 dormant** | Benign-looking record; trigger in a future query context | Delayed; write-time filters miss | Read-time context-sensitive scoring; ablation |
| **Environment-injected (eTAMP)** | One malicious page triggers memory write; no direct API access | Cross-site; ASR up to 32.5% GPT-5-mini | Do not auto-promote web observations to semantic memory; human confirm for preference writes |
| **Stale facts** | Saved memories without temporal invalidation ("training for marathon" + "sprained ankle") | Wrong personalization | Bi-temporal edges; recency x validity in ranker; dreaming/sleep-time; user-visible profile |
| **Over-retrieval** | k too large / no rerank / no token budget | Lost-in-the-middle; cost; prompt injection volume | Constructor budgets (Zep 1.6k; Mem0 ~7k); MMR; hard token cap |
| **Identity mix-up** | Shared thread_id, shared Letta block, missing user_id filter | Cross-customer disclosure | Namespace discipline; per-user stores |
| **Unbounded growth** | ADD-only + no TTL + full checkpoint history | Cost, p99, stale HNSW neighborhood | TTL, shallow checkpoints, archival vs core split, GC jobs |
| **Write/read race** | Query before graph construction finishes | Empty or wrong recall | Ingest watermark; fallback to episodes |
| **Last-write-wins clobber** | Sleep-time + primary both edit a block | Lost preferences | Single writer (sleep-time owns core memory) |
| **Compaction amnesia** | Summary drops the constraint you need on turn 90 | Silent quality drop | Memory tool/blocks before compact; custom instructions; pause_after_compaction |
| **Soft-delete "erasure"** | HNSW flag, trace TTL, backup | Compliance finding | Compaction/VACUUM + crypto-shred + provenance map |
| **Procedural injection** | CLAUDE.md / skills / MCP config as durable "memory" | Persistent RCE / prompt injection | Trust prompts, ignore repo MCP until approval, lock down ~/.claude |
| **Context-window degradation** | "Lost in the Middle" -- evidence buried in long context used poorly | Degraded accuracy before window full | Active compaction, summarization, STM trimming |
| **Semantic cache false positive** | "Close enough" cached answer returned for different question | Wrong answer | Similarity threshold tuning; monitor miss/hit quality |
| **Exact-prefix cache thrash** | Serialization changes, prefix unstable | Paying writes without reads | Monitor cache_write vs cached_tokens; stabilize prefix |

**Poisoning research numbers (2026)**:
- MemPoison: 1,227 cases, L1/L2/L3; write-time consistency checks suppress L1, not L2/L3
- Hidden in Memory: Poisoned writes up to 99.8% GPT-5.5 / 95% Kimi-K2.6; among retrievals, attacker-intended actions 60-89%
- eTAMP: GPT-5.2 more capable, still vulnerable (capability != safety)
- SMSR defense: HMAC provenance at write + randomized ablation at read -> unsigned ASR 93-100% to 0%
- TMA-NM defense: Origin-bound, non-malleable authority -> 0% ASR in their harness

---

## 8. Interview Questions & Answers

**Q1: What are the different types of memory in an agentic AI system?**

I use the CoALA framework (Sumers et al., TMLR 2024) which maps cleanly to products. Working memory is the current prompt -- message buffer, system prompt, pinned facts. It is hot and expensive. Semantic memory is durable facts: "user is vegetarian," "policy P-12 requires dual control." Products like Mem0, Zep's entity subgraph, Letta core blocks. Episodic memory is what happened: conversation logs, trajectories, checkpoints. Graphiti episodes, LangGraph checkpoints, Letta recall memory. Procedural memory is how to act: system prompts, tool definitions, skills. The key insight is these are not interchangeable -- you need episodes for audit and erasure even if you only query semantic facts day-to-day.

**Q2: How does short-term memory work in production agents?**

Short-term memory is the prompt, and the challenge is managing its growth. There are three strategies in increasing sophistication. First, windowing: keep the last k turns (FIFO). Simple but drops early constraints. Second, token-budgeted trimming: `trim_messages(strategy="last", max_tokens=8000)` -- strictly better than k turns because it handles variable message sizes. Third, summarization: when tokens exceed a threshold, replace the prefix with a running summary + recent messages. Anthropic takes this further with three layers: tool-result clearing at 100k tokens (lossless), compaction at 150k (lossy summarization), and the memory tool for facts that must survive compaction. Their eval shows context editing alone gives +29% task performance and -84% tokens.

**Q3: Compare Mem0, Zep/Graphiti, and Letta for long-term memory.**

Mem0 is the fastest to ship -- managed platform with hybrid semantic + BM25 + entity boost retrieval. V3 scores 92.5 on LoCoMo at ~7k tokens and p50 0.88s. The trade-off: ADD-only accumulation means forgetting is a separate pipeline you build yourself. Zep/Graphiti is the strongest for temporal reasoning -- bi-temporal model with valid_at/invalid_at on every fact edge, so point-in-time queries are first-class. Paper shows 71.2% vs full-context 60.2% at 2.58s vs 28.9s. Vendor claims sub-200ms retrieval. The trade-off: graph construction can take hours (Mem0's paper measured >600k tokens for Zep construction). Letta is the best when the agent must self-edit its own persona -- core blocks are always in context, sleep-time agents own the writes, and you get ADE for debugging. The trade-off: shared blocks are last-write-wins, and you need careful serialization between primary and sleep-time agents.

**Q4: What is the "sleep-time compute" pattern?**

It is the idea that you can shift computation from test-time (when the user is waiting) to sleep-time (background processing). Instead of the agent reasoning deeply on every query, a background agent periodically reviews conversation history and updates core memory blocks with distilled insights. Letta's paper shows ~5x less test-time compute for the same accuracy, and 2.5x lower average cost when 10 queries share one precomputed context. OpenAI's Dreaming V3 is the same concept -- background synthesis of saved memories. The key design decision: the sleep-time agent should be the single writer to core memory to avoid last-write-wins conflicts.

**Q5: How do you handle GDPR Article 17 (right to erasure) with agent memory?**

This is a fan-out problem, not a single DELETE. You need to erase across: (1) Semantic rows / graph nodes+edges tagged by user_id. (2) Episodes / checkpoints / Store keys / memory files. (3) Vector IDs -- HNSW soft-delete until compaction/VACUUM; query suppression alone is not erasure per EDPB guidance. (4) Prompt/response caches. (5) Trace vendors (LangSmith/Langfuse API delete, physical purge delayed). (6) Backups -- crypto-shred per-user keys or wait backup TTL within the one-month deadline. (7) Fine-tuned weights -- unlearning is unsolved; architectural control is to not train on raw personal memory. The clock is max one month (extendable +2 with notice). Mem0 v3 ADD-only makes audit easier but you still need a deletion pipeline. Graphiti's invalidation preserves history -- Art. 17 requires a hard-delete path, not only `invalid_at`.

**Q6: What is memory poisoning and how do you defend against it?**

Memory poisoning is when untrusted content (a malicious webpage, tool output, or user message) causes the system to write a false belief into durable semantic memory, which then steers future behavior -- potentially across sessions and even across sites. The 2026 research is alarming: Hidden in Memory achieved 99.8% success rate on GPT-5.5, and eTAMP showed that a single malicious page can poison memory without direct API access (up to 32.5% attack success). Defense layers: (1) Never auto-promote web observations or tool outputs to semantic memory without validation. (2) Origin-bound provenance -- HMAC at write time so you know where each memory came from. TMA-NM achieved 0% attack success rate. (3) Separate "observation" stores (low trust) from "belief" stores (high trust). (4) Read-time context-sensitive scoring and randomized ablation to catch dormant sleeper memories.

**Q7: How do you design memory for a multi-tenant system?**

Layer the controls. At the data layer: pre-filter every vector, BM25, and graph query by tenant_id/user_id from the verified token, never from tool arguments (Asana-class leak pattern). For stronger isolation: namespace-per-tenant or collection-per-tenant rather than shared index with metadata filter. For regulated workloads: per-tenant index/VPC/BYOC. At the memory layer: LangGraph uses namespace tuples like ("t", tenant, "u", user); Mem0 supports user/agent/app/run scopes; Letta isolates by agent_id with explicit shared blocks. The identity mix-up failure -- shared thread_id or shared Letta block between customers -- is the same bug class as memory poisoning: untrusted data becoming trusted state.

**Q8: What are the token economics of memory vs full-context?**

The economic case for extractive memory is not "memory is more accurate" -- full-context often wins on accuracy. It is "memory is accurate enough at 1/10th the tokens and ~1/12th the p95 latency." Mem0 paper: 1,764 tokens and 1.44s p95 vs full-context 26,031 tokens and 17.1s p95. Full-context won accuracy by ~6 percentage points on their 26k-token set. At enterprise scale, the math is clear: Mem0 Starter ($19/mo for 5k retrievals) gives ~$3.80/1k sessions for the memory layer, while generation at ~7k tokens on a frontier model is ~$30/1k sessions. Full-context at 26k tokens would be ~$78/1k sessions for generation alone. Memory-layer SKU is 5-6x cheaper than generation.

**Q9: How should you structure memory for a long-running coding agent?**

Use the Anthropic stack as the template. First, clear tool exhaust at 100k tokens -- this is the biggest win (84% token reduction) and is lossless for refetchable results. Second, compact at 150k with custom `instructions` that preserve decisions, open TODOs, and architectural choices. Third, use the memory tool for durable lessons ("this codebase uses factory pattern for X," "never use library Y because of CVE Z"). Optionally add Letta MemFS or Cognee for repo-level semantic graph. Sleep-time overnight over the repo gives ~5x less test-time reasoning on stateful tasks. Critical security: treat CLAUDE.md and MCP configs as procedural memory -- trust UI, pin versions, use startup classifiers. Never store secrets in memory files.

**Q10: What benchmarks should you use to evaluate memory systems?**

Do not procure on DMR/LoCoMo alone -- DMR almost saturates (full-context gets 94.4%, Zep 94.8%), so it cannot differentiate systems. Use LongMemEval_M (~1.5M tokens) for realistic scale and BEAM 10M for stress testing (Mem0 platform scores 48.6 there -- the task is genuinely hard). Also add your own tests: identity/tenant isolation (does the system ever leak user A's memories to user B?), poisoning resistance (can a malicious tool output persist as a trusted fact?), temporal validity (if a fact becomes false, does the old version stop appearing?), and abstention (if no relevant memory exists, does the system say "I don't know" rather than hallucinate?).

**Q11: Explain the Generative Agents memory architecture.**

Park et al. (UIST 2023) designed a memory stream of natural-language observations (episodic), plus two processes on top. Reflection generates higher-level inferences (semantic summaries with pointers to evidence), written back into the stream on a threshold (e.g., every ~100 importance points accumulated). Planning creates natural-language agendas. Retrieval uses a three-signal score: recency (exponential decay 0.995/hour), importance (LLM 1-10 rating), and relevance (cosine similarity). All three are min-max normalized and equally weighted. This architecture is still the template for production systems -- Mem0, Zep, and Cognee all implement variants of this recency+importance+relevance fusion.

**Q12: How do you prevent "compaction amnesia" in long conversations?**

Compaction amnesia happens when the summarization step drops a constraint you need on turn 90 -- "I'm allergic to peanuts" gets summarized away. Three defenses: (1) Before compaction triggers, the agent should write critical facts to durable memory (Anthropic memory tool, Letta core blocks) that survives summarization. (2) Use custom compaction instructions that tell the summarizer to preserve IDs, decisions, and constraints. (3) Use `pause_after_compaction` (Anthropic) as a human circuit breaker to inspect summaries before continuing. The fundamental insight is that compaction is lossy by design, so anything important must be promoted to a higher tier before it happens.

---

## 9. Key Numbers to Memorize

| Metric | Value | Source |
|--------|-------|--------|
| Mem0 retrieved tokens | ~1,764 (paper) / ~7k (v3 platform) | Mem0 paper / platform |
| Full-context tokens (LOCOMO) | 26,031 | Mem0 paper |
| Mem0 p50 search latency | 0.148s (paper) / 0.88s (v3 platform) | Mem0 |
| Zep retrieve latency | 155-162 ms (vendor) / 2.58s e2e (paper) | Zep |
| Anthropic context editing token savings | -84% | Anthropic eval |
| Anthropic context editing + memory | +39% task performance | Anthropic eval |
| Anthropic compaction trigger | 150k tokens (min 50k) | Anthropic docs |
| Anthropic tool-clear trigger | 100k tokens | Anthropic docs |
| Sleep-time compute savings | ~5x less test-time compute | Letta paper |
| Sleep-time cost amortization | 2.5x lower when 10 queries share | Letta paper |
| OpenAI Dreaming V3 compute | ~5x cheaper than prior dreaming | OpenAI |
| Letta block limit | <50k chars/block, <20 blocks/agent | Letta docs |
| Letta archival passage | ~300 tokens/passage | Letta docs |
| Mem0 Starter price | $19/mo (5k retrievals, 50k adds) | Mem0 pricing |
| Mem0 Pro price | $249/mo (50k retrievals, 500k adds) | Mem0 pricing |
| Zep Flex price | $125/mo (50k credits) | Zep pricing |
| Generative Agents recency decay | 0.995 per sandbox hour | Park et al. |
| GDPR Art. 17 deadline | Max 1 month (+2 with notice) | GDPR |
| Hidden in Memory attack success | Up to 99.8% (GPT-5.5) | 2026 paper |
| SMSR defense success | Unsigned ASR 93-100% -> 0% | 2026 paper |
| MongoDB checkpoint doc cap | 16 MB | MongoDB |
| Postgres checkpoint field cap | ~1 GB | PostgreSQL |

---

## 10. Quick Reference

### Memory Cheat Sheet

**Memory taxonomy** (CoALA): Working (prompt) -> Semantic (facts) -> Episodic (events) -> Procedural (skills). Never collapse semantic and episodic.

**STM management hierarchy**:
1. Token-budgeted trim (always)
2. Tool-result clearing at ~100k (lossless, biggest win)
3. Compaction/summarization at ~150k (lossy)
4. Memory tool for facts that must survive compaction (durable)

**LTM product selection**:
- Ship fast + personalization: Mem0 platform
- Temporal reasoning + point-in-time: Zep/Graphiti
- Agent self-editing + always-on persona: Letta blocks + sleep-time
- Custom multi-tenant: LangGraph Store + checkpointer

**Consolidation rule**: Run off the user path (sleep-time, dreaming, async). Never block TTFT for memory writes.

**Forgetting rule**: GDPR erasure is a fan-out (semantic + episodic + vectors + caches + traces + backups). Soft-delete is not erasure. Crypto-shred per-user keys for backups.

**Poisoning defense stack**:
1. Never auto-promote tool/web output to semantic memory
2. Origin-bound provenance (HMAC at write)
3. Separate observation stores from belief stores
4. Read-time ablation for dormant sleeper detection

**Security checklist**:
- tenant_id/user_id from verified token only, never tool args
- Pre-filter on every ANN, BM25, and Cypher query
- Treat vectors as confidential as source (inversion attacks exist)
- Per-user encryption keys for erasure compliance
- Credentials in vault, never in archival memory or CLAUDE.md

**Token budget rule of thumb**:
```
Memory layer cost << Generation cost
Mem0: ~$4-5/1k sessions vs ~$30 generation
Full-context: ~$78/1k sessions generation
```
