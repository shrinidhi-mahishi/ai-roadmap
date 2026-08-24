# Module 07 — Memory

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `research_cursor_grok/research/07-memory.md` (researched 2026-08-21, 89 sources).
**Mandatory topics**: Short-term · Long-term · Semantic · Episodic · Memory retrieval.

The unit of production is not “the model remembers.” It is two independently scaled **planes sharing durable stores**: a **write (ingest / learning) plane** that extracts facts, stamps ACL + tenant + `user_id`, resolves entities, invalidates conflicts, embeds, inserts graph edges, consolidates, and forgets; and a **read (retrieve / assemble) plane** that authorizes, hybrid-retrieves, scores recency × importance × relevance, reranks, budgets tokens into the window, and generates. CoALA (Sumers et al., TMLR 2024) still holds: the LLM is **not** the memory. Working memory is a data structure the prompt is *compiled from*; long-term memory is read via **retrieval** and written via **learning**. The model never searches. It emits a tool call or the control plane runs a retriever; observations return as tokens. Interview answers that skip the write/read split fail when the follow-up is “why did p99 track the extractor, and who stamped `tenant_id`?”

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns whether to write, which store, which \(k\), whether to compact, sleep-time/dreaming schedules, circuit breakers, and the ingest watermark that query is allowed to trust. Data plane owns ANN + BM25 + graph BFS, RRF, cross-encoder, STM trim/compaction, and generator tokenize/prefill/decode. Persistence is **three coexisting tiers**, not one vector table: **hot** STM (thread messages, Letta core blocks, profile card), **warm** semantic facts + recent episodes (pgvector / Neo4j / Mem0 / Zep), **cold** traces, old episodes, community rebuilds, Anthropic `/memories` object files. Tool proxies are MCP memory servers (`memory_view` / `memory_create` / `memory_delete`) plus optional CRM. Telemetry is the only place retrieve vs extract latency, `memory_miss`, constructor tokens, entitlement violations, and Art. 17 fan-out completeness are authoritative.

Write and read share storage, not threads. Coupling them makes query p99 track extract; a 600k-token graph rebuild (Mem0 paper’s Zep observation) makes “just-added” memories unsearchable for hours.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS  (chat SSE / HITL resume / batch eval / MCP host / Art.17 erasure)      │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │  TLS + correlation-id + verified tenant token (never tool args)
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE                                                                   │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │ API Gateway│─▶│ Policy       │─▶│ Memory router│─▶│ Orchestrator           │ │
│  │ auth,quota │  │ PII detect   │  │ STM-only |   │  │ compile window         │ │
│  │ RPM vs     │  │ redact       │  │ retrieve |   │  │ constructor tok cap    │ │
│  │ extract QPS│  │ ACL from     │  │ compact  |   │  │ pin index_version      │ │
│  │ breaker    │  │  token (PEP) │  │ sleep-time   │  │ enqueue write (async)  │ │
│  └────────────┘  └──────┬───────┘  └──────┬───────┘  └──────────┬─────────────┘ │
└─────────────────────────┼─────────────────┼─────────────────────┼───────────────┘
                          │                 │                     │
          ┌───────────────┘                 │                     │
          ▼ READ (sync; user-critical)      │                     ▼ WRITE (async)
┌─────────────────────────────────────────┐ │  ┌──────────────────────────────────┐
│ DATA PLANE (read)                       │ │  │ WRITE PLANE — Temporal + Kafka   │
│ model = untrusted planner               │ │  │  ┌────────┐ ┌────────┐ ┌───────┐ │
│  ┌──────────┐  ┌──────────┐  ┌────────┐ │ │  │  │Extract │─▶│Conflict│─▶│Embed │ │
│  │ Authz    │─▶│ Hybrid   │─▶│ Score  │ │ │  │  │LLM /   │  │ADD/UPD │  │graph │ │
│  │ pre-     │  │ dense ∥  │  │ recency│ │ │  │  │pair m  │  │/DEL/   │  │insert│ │
│  │ filter   │  │ BM25 ∥   │  │ +impt  │ │ │  │  │_{t-1,t}│  │NOOP or │  │      │ │
│  │ tenant   │  │ BFS      │  │ +relev │ │ │  │  └────────┘  │v3 ADD  │  └───┬───┘ │
│  └──────────┘  └────┬─────┘  └───┬────┘ │ │  │               └────────┘      │   │
│                     │            │      │ │  │  ┌────────────┐  ┌────────────┴─┐ │
│  ┌──────────────────┴────────────┘      │ │  │  │Consolidate │  │ Forget/GC    │ │
│  │ RRF k=60 → rerank → constructor      │ │  │  │sleep-time  │  │ TTL, Art.17  │ │
│  │ budget 1.6k–7k tok into STM window   │ │  │  │dreaming    │  │ hard-delete  │ │
│  └──────────────────┬───────────────────┘ │  │  └────────────┘  └──────────────┘ │
│                     │                     │  └──────────────────────────────────┘
│  ┌──────────────────┴───────────────────┐ │
│  │ STM assembler                        │ │
│  │ trim_messages / tool-clear / compact │ │
│  │ pin core blocks + profile card       │ │
│  └──────────────────┬───────────────────┘ │
│                     ▼                     │
│  ┌──────────┐  ┌────────────────────────┐ │
│  │Generator │  │ TOOL PROXIES (MCP)     │ │
│  │ cite mem │  │ memory_view/create/del │ │
│  │ IDs only │  │ ticket: tenant,expiry  │ │
│  └──────────┘  │ /memories FS is yours  │ │
│                └────────────────────────┘ │
└─────────────────────┬─────────────────────┘
                      │
┌─────────────────────┴───────────────────────────────────────────────────────────┐
│ PERSISTENCE (shared; independently scaled)                                      │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌─────────────────────────┐ │
│  │ HOT          │ │ WARM         │ │ COLD         │ │ Checkpoints / Store     │ │
│  │ last-N msgs  │ │ semantic     │ │ episodes TTL │ │ LangGraph thread_id     │ │
│  │ core blocks  │ │ facts+edges  │ │ traces 7–30d │ │ Store (org,agent,user)  │ │
│  │ profile card │ │ recent eps.  │ │ /memories    │ │ Redis TTL / Postgres WAL│ │
│  │ Redis STM    │ │ pgvector     │ │ object store │ │ Graphiti valid_at       │ │
│  │ p50 50–200ms │ │ Neo4j, Mem0  │ │ community    │ │                         │ │
│  │ assembly     │ │ Zep 155–162ms│ │ rebuilds     │ │                         │ │
│  └──────────────┘ └──────────────┘ └──────────────┘ └─────────────────────────┘ │
└─────────────────────────────────────┬───────────────────────────────────────────┘
                                      │
┌─────────────────────────────────────┴───────────────────────────────────────────┐
│ TELEMETRY / OBSERVABILITY SINKS                                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌───────────────────────┐  │
│  │ Audit (WORM)│  │ Metrics      │  │ Traces      │  │ Governance            │  │
│  │ who wrote/  │  │ retrieve vs  │  │ gateway →   │  │ Art.17 fan-out        │  │
│  │ read, k,    │  │ extract p99  │  │ retrieve →  │  │ completeness, HNSW    │  │
│  │ mem_ids in  │  │ constructor  │  │ constructor │  │ VACUUM, crypto-shred  │  │
│  │ prompt,     │  │ tok, miss,   │  │ → generate  │  │ origin HMAC           │  │
│  │ index_ver   │  │ breaker, lag │  │ (redact txt)│  │                       │  │
│  └─────────────┐  └──────────────┘  └─────────────┘  └───────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Write vs read planes and memory tiers

| Plane | Owns | Typical components | Failure if coupled |
| --- | --- | --- | --- |
| **Write (ingest / learning)** | Extract facts, stamp ACL + tenant + `user_id`, entity-resolve, conflict/invalidation, embed, graph insert, consolidate, forget | Session workers, extraction LLMs, Graphiti builders, sleep-time / dreaming agents, checkpoint writers | Query p99 tracks extract; 600k-tok graph rebuild (Mem0 paper on Zep) makes new memories unsearchable for hours |
| **Read (retrieve / assemble)** | Authz pre-filter, hybrid retrieve, recency/importance score, rerank, budget into the window, generate | ANN + BM25 + graph BFS, RRF, cross-encoder, memory blocks pinned in-context | Write schema change (embedder, ontology) silently mismatches query embeddings |

MCP memory servers sit on the **tool boundary**: data-plane stores with control-plane auth. Letta ADE and Zep Context Lake are control-plane UIs over data-plane graphs.

**CoALA four stores (what actually ships):**

| CoALA type | Holds | Production analogue | Hotness |
| --- | --- | --- | --- |
| **Working (STM)** | Active symbols for this decision cycle — not identical to the LLM context string | Message buffer, LangGraph thread state, Letta in-context messages, token-budgeted scratchpad | **Hot** (every token billed) |
| **Episodic** | Time-stamped events / trajectories (“what happened”) | Graphiti episodes, conversation logs, LangGraph checkpoints, Letta recall/conversation search | **Warm** (searchable, not pinned) |
| **Semantic** | Facts, entities, user/world knowledge (“what is true”) | Mem0 memories, Graphiti entity/fact edges, Letta core blocks + archival, user profiles | **Warm/cold** (blocks hot; archival cold) |
| **Procedural** | How to act (prompts, skills, code, weights) | System prompt, tools, Letta skills, Claude Skills, CLAUDE.md | **Hot** if in prompt; **cold** if tool-fetched |

Park et al. *Generative Agents* (UIST 2023): a **memory stream** of NL observations + **reflection** (higher-level inferences written back) + **planning**. Reflections are semantic summaries *with pointers* to episodic evidence — the same episode→fact→community hierarchy Zep productized.

**Hot / warm / cold (latency is assembly + search, not one SLA):**

| Tier | Latency target | Contents | Typical backing |
| --- | --- | --- | --- |
| **Hot** | p50 <50–200 ms of *assembly*, plus model TTFT | System prompt, core blocks, last-N messages, user profile card | In-process / Redis / pinned tokens |
| **Warm** | p50 100–400 ms search (Mem0 paper **148 ms**; Zep vendor **155–162 ms** retrieve) | Semantic facts, recent episodes, entity subgraph | pgvector, Neo4j, Mem0/Zep APIs |
| **Cold** | seconds–hours | Full traces, old episodes, community rebuilds, object-store memory files | Object storage, warehouse, Graphiti community refresh |

Letta’s hierarchy: if it must be true every turn, it is a **block** (hot); if it is a fact the agent might need, **archival** (warm); if it is a corpus, **files/RAG** (cold). Anthropic memory files are cold until `view` pages them hot.

### 1.3 End-to-end request flow

**Read path (synchronous; personalization is on the critical path, “can the agent answer” is not).**

1. **Ingress.** Gateway stamps correlation-id, authenticates, extracts `tenant_id` / `user_id` / roles from the **verified token**. Circuit-breaker state on retrieve, embedder, and generator are routing inputs. A closed breaker on Mem0/Zep already means STM-only.
2. **Policy.** Redact query PII before embed APIs see it. Attach **only** the memory MCP tools this principal may call (`memory_view` vs `memory_create` vs `memory_delete`). `tenant_id` is **not** a tool argument the model fills.
3. **STM assemble.** Load thread checkpoint (`thread_id`). `trim_messages(strategy="last", max_tokens=N, include_system=True, start_on="human")` or Anthropic tool-result clearing. Pin core blocks + profile card as the **prompt-cache prefix**. Do not wait on consolidation.
4. **Authorize as a hard pre-filter.** Namespace / RLS / bitmap **before** ANN, BM25, and Cypher. Post-filter after top-k leaks neighbors (same bug class as RAG tenant leak).
5. **Hybrid retrieve (if breaker closed).** Dense cosine ∥ BM25 ∥ optional BFS from recent episodes. Fuse with RRF (\(k=60\)). Mem0 v3: semantic + BM25 + entity boost + temporal ranker. Graphiti: \(f = \chi \circ \rho \circ \varphi\) — search → rerank → constructor.
6. **Score.** Generative Agents template (still copied in 2026): \(\mathrm{score}=\alpha_r\,\mathrm{recency}+\alpha_i\,\mathrm{importance}+\alpha_v\,\mathrm{relevance}\) with \(\alpha=1\) after min-max. Recency = \(0.995\) decay per sandbox hour since last *access*. Re-score in the **current** query context (write-time filters miss L2/L3 compositional poison).
7. **Constructor.** Hard token cap after fusion: Zep paper **1.6k** vs Mem0 v3 **~7k** vs full-context **26k–115k**. MMR / mention-frequency / node-distance optional. Over-retrieval is lost-in-the-middle plus injection volume.
8. **Generate.** Prompt = system + core blocks + constructor hits + STM suffix. Citations constrained to retrieved `memory_id`s. If retrieve score < threshold, **abstain** (“I don’t have a memory of that”) — BEAM 10M abstention **40.0** (Mem0 platform) shows this remains hard.
9. **Enqueue write; emit.** User-facing ack does **not** wait on extract. Audit: who read, which query, which \(k\), which memory_ids entered the prompt, model/index versions.
10. **Degrade.** Retrieve timeout **200–500 ms [inferred SLO, not vendor]**. Breaker open → STM + cached profile card; log `memory_miss`. Never block TTFT on the write plane.

**Write path (asynchronous; Letta sleep-time pattern).**

1. **Ack the turn.** Publish `memory.write` (Kafka) or start a Temporal workflow keyed by `(tenant, user_id, turn_id)`. Idempotency: `memory_id = hash(tenant, user_id, fact_norm, embedder_version)`.
2. **PII detect → redact before embed.** SSN/PAN never become vectors (embeddings invert to approximate text in published attacks). Credentials belong in a vault, never archival or CLAUDE.md.
3. **ACL stamp.** `{tenant_id, user_id, session_id, category, ingested_at, origin, origin_hmac}` at **write**. Article 17 cannot find untagged vectors.
4. **Extract.** Mem0 paper: pair-wise \((m_{t-1}, m_t)\) + conversation summary \(S\) + last \(m=10\) messages → candidate facts → retrieve top \(s=10\) similar → LLM tool-call ADD/UPDATE/DELETE/NOOP. Mem0 v3: **ADD-only** (no UPDATE/DELETE at extract); conflicts become extra rows; read-time temporal ranker picks “current.”
5. **Conflict / invalidation.** Graphiti: contradiction → set old edge `invalid_at` ← new `valid_at`, `expired_at` ← now; **do not delete** (history preserved; Art. 17 still needs a hard-delete path). Letta shared blocks: **last-write-wins** — serialize or lose data. LangGraph Store `put` replaces key; no CRDT.
6. **Embed + graph insert.** Pin `embedding_model` + `index_version`. Graphiti episodes: last **n=4** messages as NER context; Cypher writes, not LLM-generated queries. Ingest watermark: facts are not queryable until the episode is linked (Zep async pipeline: immediate-after-write retrieval often failed in the Mem0 harness; hours later improved).
7. **Consolidate off the user path.** Sleep-time (Letta default `sleeptime_agent_frequency=5`), Mem0 Dream (Pro), OpenAI Dreaming V3, Cognee `.improve`, Graphiti community refresh, A-Mem neighbor evolution. Single writer on core blocks.
8. **Forget / GC.** TTL (Redis/Store), Ebbinghaus-style strength (MemoryBank), or Art. 17 fan-out. These are **different pipelines** — see §4.4.

**Interview talking point:** “Memory is two planes. The model is an untrusted planner. IAM and tenant stamps live on the write host. Retrieve authz is a pre-filter. Consolidation is a batch job, not a chat round-trip.”

---

## 2. Core Mechanics & Algorithms

### 2.1 Short-term / working memory: buffers, windows, token budgets

STM is **thread-scoped**. It is not a user profile. LangGraph already splits this in the API: checkpointer = STM; Store = LTM.

**Conversation buffer (full history).** LangChain classic `ConversationBufferMemory` stores every turn (`memory_key="history"`). Linear growth; zero information loss until the window overflows. Deprecated since **0.3.1**; replacement is `create_agent` + a **checkpointer**. Still the right *mental model*: STM is the thread’s messages.

**Window (\(k\) turns).** `ConversationBufferWindowMemory` keeps the last \(k\) human/AI pairs (FIFO). Constant RAM; drops early constraints (“I’m vegetarian” on turn 2 of a 200-turn support chat). Use when recency *is* the task (IVR, short tickets). Do not use as the only memory for identity or policy.

**Token-budgeted buffer.** `ConversationTokenBufferMemory` drops oldest messages until `max_token_limit`. Strictly better than \(k\) turns when message size varies (tool dumps vs “ok”). LangChain Core `trim_messages(..., strategy="last", max_tokens=N, include_system=True, start_on="human")` is the 2025–26 primitive: keep system + a legal human/AI suffix under a token budget. Approximate counting (`'approximate'`) on the hot path; `model.get_num_tokens_from_messages` for billing-accurate trim.

**Complexity.** Trim from the tail: \(\Theta(n)\) messages, \(\Theta(T)\) token estimates. FIFO window: \(\Theta(1)\) amortized insert/evict. Full buffer: \(\Theta(n)\) RAM and prompt tokens — this is why compaction exists.

**Summarization as STM compression.** LangMem `summarize_messages` / `SummarizationNode`: when cumulative tokens ≥ `max_tokens_before_summary`, replace the prefix with `[summary_message] + remaining_messages`, carrying `running_summary` so you do not re-summarize the same prefix every turn. LangChain `SummarizationMiddleware` triggers on `("tokens", N)`, `("messages", N)`, `("fraction", 0.8)`, or AND/OR; default keep is last **20** messages. Compaction is **lossy**; it invalidates prompt-cache prefixes (Anthropic documents this for `compact_20260112`).

**Anthropic three-layer STM (Messages API, 2025–26) — not interchangeable:**

| Primitive | ID | Default trigger | What it does | Cache / loss |
| --- | --- | --- | --- | --- |
| Tool-result clearing | `clear_tool_uses_20250919` | 100k input tokens; keep last 3 tool uses | Mechanical delete of stale tool exhaust; placeholder remains | Cheapest; lossless for refetchable results |
| Compaction | `compact_20260112` (beta `compact-2026-01-12`) | 150k input tokens; **min 50k** | Server summarizes prefix into a `compaction` block; later requests drop everything before it | Lossy; **invalidates cached prefix** |
| Memory tool | `memory_20250818` (GA, Claude 4+) | Model-initiated | Client executes `view/create/str_replace/insert/delete/rename` under `/memories` | Survives compaction; storage is **your** infra |

Anthropic agentic-search eval (Sonnet 4.5 launch, 2025): context editing alone **+29%** task performance and **−84%** tokens vs baseline; editing + memory tool **+39%**. The 84% is tool-exhaust deletion, not summarization. Compaction `instructions` fully replace the default summarizer — pin IDs, decisions, and open work items. `pause_after_compaction` is a **human circuit breaker** for lossy STM.

**STM compaction state machine:**

```
                    tokens < trigger
                 ┌──────────────────┐
                 │                  ▼
┌─────────┐   ┌──┴───┐  ≥100k    ┌────────────┐  ≥150k (min 50k)  ┌──────────┐
│ ACCEPT  │──▶│ STM  │──────────▶│ TOOL_CLEAR │──────────────────▶│ COMPACT  │
│  turn   │   │ ACCUM│           │ keep last 3│   instructions    │  prefix  │
└─────────┘   └──┬───┘           └─────┬──────┘                   └────┬─────┘
                 ▲                     │ placeholder remains            │
                 │                     ▼                               │ cache bust
                 └────────── append suffix ◀───────────────────────────┘
                              memory tool pages cold → hot independently
```

**LangGraph STM = checkpointer.** `PostgresSaver` / `RedisSaver` snapshot **graph state per `thread_id`**. That is conversation continuity, HITL interrupts, time-travel, and crash recovery — not user profiles. Redis: `defaultTTL` in **minutes**, `refreshOnRead`, plus `ShallowRedisSaver` (latest checkpoint only). Postgres: no native TTL in OSS (cron/`delete_thread`); Agent Server / `langgraph.json` TTL is the managed path. MongoDB checkpointers: **16 MB** document cap vs Postgres **~1 GB**/field.

**Invariant (PostgresSaver concurrency):** a **single saver + pool + compiled graph per process**; the Python saver’s `_cursor` lock serializes ops on one instance. Horizontal scale = more workers, not more savers per event loop. RedisSaver does not use that lock. Call `setup()` once.

**Letta STM.** In-context message buffer + pinned **memory blocks**. `max_message_buffer_length` is best-effort (user/assistant interleaving can overshoot). `message_buffer_autoclear=true` forgets previous messages while retaining core blocks + archival/recall — advanced only. Recall memory = searchable full conversation history (episodic); not pinned.

### 2.2 Long-term stores (2025–26)

LTM is **user-scoped** (or org-scoped for team procedures). Compile LangGraph with `checkpointer=` **and** `store=`. Namespaces typically `("memories", user_id)` or `(org, agent, user)` — never a shared `thread_id` as the identity key.

**Letta (formerly MemGPT).** Packer et al. 2023: virtual context management — main context (RAM) vs external context (disk), agent issues OS-like page-in/page-out tool calls.

| Tier | Mechanism | Limits (docs) |
| --- | --- | --- |
| **Core / blocks** | Labeled, always-in-context strings; `memory_insert` / `memory_replace` / `memory_rethink`; shareable across agents | Rec. **<50k characters/block**, **<20 blocks/agent** |
| **Files** | Open/close + grep + semantic search | **5 MB**/file, rec. **<100 files** |
| **Archival** | `archival_memory_insert` / `_search`; REST `/v1/agents/{id}/archival-memory` with tag + datetime | **~300 tokens**/passage; unlimited count |
| **External RAG / MCP** | Custom tools | Unlimited |

Sleep-time agents (Letta 0.7+, arXiv:2504.13171): a background agent **owns write tools** for core blocks; the primary agent talks and searches recall/archival. Default frequency **5** primary steps. Shared blocks are **last-write-wins**. Sleep-time paper: ~**5×** less test-time compute for same accuracy on Stateful GSM-Symbolic/AIME; scaling sleep-time **+13%** / **+18%** accuracy; **2.5×** lower average cost when **10** queries share one precomputed \(c'\). Letta Code 2026: **MemFS** (git-backed) + **dreaming** (background subagents). Constellation naming retired; cloud-hosted state is **Letta Cloud**.

**Mem0 — two eras; do not mix scores.**

*Paper (arXiv:2504.19413, LOCOMO ~26k tok/conversation, GPT-4o-mini extract):* pair-wise extract → top \(s=10\) similar → ADD/UPDATE/DELETE/NOOP. Mem0g: Neo4j directed labeled graph; mark obsolete edges invalid (not physical delete). Retrieval tokens on LOCOMO: Mem0 **1,764**; Mem0g **3,616**; Zep **3,911**; OpenAI playground memories **4,437**; full-context **26,031**. Construction: Mem0 ~**7k** tok/conversation, Mem0g ~**14k**, Zep **>600k**.

*Platform v3 (2026, managed):* single-pass **ADD-only**, native entity graph (no external Neo4j), hybrid **semantic + BM25 + entity boost**, temporal ranking. GitHub: LoCoMo **92.5** (was 71.4) @ **7.0k** tok, p50 **0.88 s**; LongMemEval **94.4** (was 67.8) @ **6.8k**, p50 **1.09 s**; BEAM 1M **64.1** @ **6.7k**; BEAM 10M **48.6** @ **6.9k**. ⚠️ Scores are the **managed platform**; OSS SDK is “directionally similar, not identical.”

**Zep + Graphiti.** Three-tier temporal KG (arXiv:2501.13956):

1. **Episode subgraph** — raw messages/text/JSON + \(t_{\mathrm{ref}}\); non-lossy; speaker auto-extracted; last **n=4** messages as NER context.
2. **Semantic entity subgraph** — entities (1024-d name embeddings + full-text) and fact edges; hybrid cosine + BM25; Cypher writes.
3. **Community subgraph** — label propagation (not Leiden) so new nodes join without full recompute; map-reduce summaries; periodic refresh still required.

**Bi-temporal model:** *valid time* (`valid_at` / `invalid_at`) vs *transaction time* (`created` / `expired`). Point-in-time queries are first-class; GraphRAG/vector DBs are not. Retrieval constructor: facts with date ranges + entity summaries + community summaries. BFS can seed from recent episodes so “just talked about X” stays in context.

Paper LongMemEval_S (~**115k** tok): gpt-4o **71.2%** vs full-context **60.2%** (+**18.5** pp), latency **2.58 s** (IQR 0.684) vs **28.9 s**, context **1.6k** vs **115k**. Vendor 2026: LoCoMo **94.7% @ 155 ms**, LongMemEval **90.2% @ 162 ms**, “sub-200 ms regardless of graph size.” ⚠️ Paper e2e ≠ retrieval-only vendor ms.

**LangGraph Store.** Cross-thread JSON `(namespace, key) → item`. `PostgresStore` optional pgvector `index=` (semantic search **off** until configured); `ttl=` requires `start_ttl_sweeper()`. Agent Server manages both; custom `BaseStore` allowed, custom checkpointer **not** on managed platform.

**Cognee 1.0 (2026).** Four verbs: `.remember` (ingest→graph or session cache), `.recall` (auto-route hybrid: graph + vectors + BM25, RRF), `.improve` (session→permanent graph), `.forget` (item/dataset/user).

**OpenAI ChatGPT memory is a product, not an API.** No Memory API on Chat Completions/Responses. Do not design enterprise agents assuming ChatGPT memory is callable. Dreaming V3 (2026-06-04): reviewable memory summary; ~**5×** cheaper dreaming compute.

### 2.3 Semantic vs episodic (do not collapse them)

**Semantic memory** answers “what is true of this user/world *now* (and historically)?”: vegetarian, works at Acme, policy P-12 requires dual control. Stores: Mem0 memories, Graphiti facts with `valid_at`/`invalid_at`, Letta `human`/`persona` blocks, profile JSON in LangGraph Store. Updates are **upserts with conflict policy** (ADD-only vs UPDATE/DELETE vs temporal invalidation).

**Episodic memory** answers “what happened, in what order, with what evidence?”: Graphiti episodes, Letta conversation search, LangGraph checkpoint history, raw traces in LangSmith/Langfuse. Episodes are the **non-lossy provenance** for semantic edges. Destroying episodes while keeping facts breaks citation, unlearning, and audit.

**Trajectories.** Agent run traces (tool calls, observations, rewards) are episodic. Reflexion/A-Mem write *lessons* back as semantic or procedural memory. A-Mem (Xu et al., NeurIPS 2025): Zettelkasten notes + LLM link generation + **evolution** of neighbor notes on insert; retrieve top-k plus linked neighbors. LoCoMo in their paper: A-Mem avg F1 rank 1.6 at **1,216** tokens vs MemGPT 16,987 — denser notes, still no first-class forgetting in the original design.

**Invariant:** every semantic row carries `episode_ids[]`. Point-in-time (“what did we believe on date D?”) uses Graphiti invalidation + episode provenance. Art. 17 hard-delete uses the same map to fan out. You need **both** PIT and a delete path.

### 2.4 Memory retrieval: salience, recency, importance, fusion

**Generative Agents retrieval (the scoring template still copied in 2026):**

\[
\mathrm{score} = \alpha_r\,\mathrm{recency} + \alpha_i\,\mathrm{importance} + \alpha_v\,\mathrm{relevance}
\]

All \(\alpha = 1\) in the paper after min-max to \([0,1]\). Recency = exponential decay **0.995** per sandbox hour since last *access* (not creation). Importance = LLM integer 1–10 (“brushing teeth” vs “breakup”). Relevance = cosine(query embedding, memory embedding). Top memories that fit the window go into the prompt.

**Complexity.** After candidate generation of size \(k\): score is \(\Theta(k)\) dot-products plus \(\Theta(k\log k)\) sort. Candidate generation dominates: HNSW \(\approx O(\log N)\) probes (ef_search dominates wall-clock, not big-O) plus BM25 inverted-list scan plus optional BFS \(O(|E_{\mathrm{local}}|)\). Fusion: RRF hash-merge \(O(k_{\mathrm{dense}}+k_{\mathrm{sparse}}+k_{\mathrm{graph}})\). Constructor is a greedy pack under a token cap: \(\Theta(k)\).

**Production mappings:**

| Signal | Who uses it | Mechanism |
| --- | --- | --- |
| Relevance | Everyone | Dense cosine; Mem0 v3 + BM25 + entity match fused into one `score` |
| Recency | Graphiti `valid_at`; Qdrant decay formulas; Mem0 temporal ranker; Generative Agents decay | Soft rank vs hard `status=current` filter |
| Importance / salience | Generative Agents LLM score; Letta agent decides insert; Mem0 extract LLM; Graphiti mention-frequency reranker | Write-time vs read-time — **not symmetric** |
| Graph distance | Graphiti node-distance reranker; Cognee traversal | Localize to a user/org subgraph |
| RRF | Graphiti, Cognee, hybrid vector stacks | Fuse lexical + dense + graph lists, \(k=60\) typical |

**Write-time vs read-time salience.** Write-time filters (consistency checks, origin tags) kill L1 poisoning but miss L2/L3 compositional attacks (MemPoison 2026). Read-time must re-score in the *current* query context. SMSR: HMAC provenance at write **plus** randomized ablation at read. Hidden in Memory: poisoned writes up to **99.8%** GPT-5.5 / **95%** Kimi-K2.6; among retrievals, attacker-intended **actions 60–89%**. eTAMP: one malicious page, no direct memory API; ASR up to **32.5%** GPT-5-mini — do not auto-promote web observations to semantic memory.

**RRF (same primitive as RAG).** For memory \(m\) and ranked lists \(R\):

\[
\mathrm{RRF}(m)=\sum_{r\in R}\frac{1}{k+\mathrm{rank}_r(m)}\quad(k=60)
\]

Rank 1 contributes \(1/61\approx 0.0164\). Memories in **both** dense and BM25 lists outrank single-list winners. Use RRF when score magnitudes are incomparable (cosine vs BM25 vs graph distance).

**Constructor budgets:** Zep paper LME **1.6k** tok (20 edges+nodes); Mem0 v3 **~7k** tok/query vs 25k+ full-context; Letta archival ~300 tok/passage so \(k=10\) ≈ 3k before generate. Cap after fusion, not unbounded top-k.

### 2.5 Consolidation, forgetting, invariants

**Consolidation** is a **batch LLM job on the write plane**: sleep-time / dreaming / Mem0 Dream (Pro SKU) / Cognee `.improve` / Graphiti community refresh / A-Mem evolution. OpenAI: saved memories go stale; dreaming exists *because* write-time notes rot. Do not run it on the user turn.

**Forgetting is two different algorithms.**

1. **Ebbinghaus-style (MemoryBank, Zhong et al., AAAI 2024):** recall reinforces strength; idle decays. Product analogue: Redis/Store TTL, shallow checkpointers, archival vs core split. Graphiti: invalidate, don’t delete (history **grows**). Mem0 v3 ADD-only: forgetting is a **separate** product/policy problem.
2. **GDPR Art. 17:** erasure is a **fan-out**, not `DELETE FROM memories` and not `invalid_at`. Clock: without undue delay, **max one month** (extendable +2 months if you notify). HNSW soft-delete until compaction/VACUUM is **not** erasure (EDPB-aligned commentary 2026: query suppression ≠ erasure).

**Conflict state machine (write plane):**

```
┌──────────┐  similar hit s=10   ┌─────────────┐
│ EXTRACT  │────────────────────▶│ LLM / policy│
│ candidate│                     │ ADD|UPDATE  │
└──────────┘                     │ DELETE|NOOP │
                                 └──────┬──────┘
                    ┌─────────┬─────────┼─────────┐
                    ▼         ▼         ▼         ▼
                 ┌─────┐  ┌───────┐  ┌──────┐  ┌──────┐
                 │ ADD │  │UPDATE │  │DELETE│  │ NOOP │
                 │ new │  │mutate │  │tomb- │  │ drop │
                 │ row │  │or v3  │  │stone │  │      │
                 │     │  │extra  │  │+audit│  │      │
                 └─────┘  │ row   │  └──────┘  └──────┘
                          └───────┘
Graphiti: UPDATE ≡ set invalid_at; physical DELETE is Art.17 only.
Letta blocks: last-write-wins unless single-writer sleep-time.
```

**Invariants (fail the design review if any is missing):**

1. LLM is not the memory (CoALA). Prompt is compiled from stores.
2. STM (`thread_id`) ≠ LTM (`user_id` / Store namespace).
3. Semantic ≠ episodic; facts keep `episode_ids[]`.
4. `tenant_id` / `user_id` from verified token, never tool args.
5. Authz **pre-filter** before ANN/BM25/Cypher.
6. Write does not block TTFT; ingest watermark gates fact visibility.
7. Constructor token cap after fusion, not “top-k unbounded.”
8. Pin `embedding_model` + `index_version`; embedder swap = silent recall collapse.
9. Single writer on shared/core blocks (sleep-time owns writes in Letta 0.7).
10. Art. 17 is hard-delete + HNSW VACUUM + crypto-shred + traces; `invalid_at` is PIT, not erasure.

---

## 3. Token Economics & NFR Analysis

Prices, rate limits, and latency percentiles below are from vendor docs, papers, or named blogs as of **2026-08-21**. ⚠️ No unpublished production p50/p95/p99 memory SLOs are invented; missing percentiles are marked. `$ per 1k sessions` figures are **[inferred]** from published SKUs × a stated reference session, not a vendor “per session” product.

**Reference session (stated, not a SKU):** 1 user-facing turn that (a) **retrieves** once, (b) **adds** 2 memories (user+assistant pair, Mem0 paper), (c) injects ~2k–7k memory tokens into a mid-size generator. Consolidation **not** included unless noted.

### 3.1 Cost per 1k runs

**Mem0 hosted (pricing page 2026-08-21):** Hobby $0 / 10k adds + 1k retrievals; Starter **$19**/mo / 50k adds + **5k** retrievals; Pro **$249**/mo / 500k adds + **50k** retrievals (graph + Dream consolidation); Enterprise custom.

| Plan | Retrieval quota | **[inferred]** retrieval $ / 1k sessions | Notes |
| --- | --- | --- | --- |
| Starter | 5,000 / $19 | **$3.80** | 1k sessions = 20% of retrieval quota; 2k adds ≪ 50k |
| Pro | 50,000 / $249 | **$4.98** | Retrieval-bound; Dream consolidation is **extra LLM** not in this SKU math |

Generator tokens at ~7k in / 0.5k out on a $3/M input / $15/M output class model: **[inferred]** ~$0.021 + $0.0075 ≈ **$0.03** per session → **~$30 / 1k sessions** for the *chat* model, i.e. **memory-layer SKU is 5–6× cheaper than generation** on this reference. Full-context 26k in: **[inferred]** ~$0.078/session generation (**~$78 / 1k**), matching the paper’s “>90% token cost” claim directionally.

**Zep (2026 pricing):** Flex **$125**/mo / 50k credits, overage **$25 / 10k** credits; Flex Plus **$375** / 200k credits, **$75 / 40k**; Enterprise custom. ⚠️ **Credit-per-add and credit-per-retrieve are not fully specified on the public pricing table** — do not convert to $/session without a contract quote. RPM **600** (Flex) / **1,000** (Flex Plus) is the published NFR. API log retention **1 day / 7 days / 1 year**.

**Letta API plan:** **$20**/mo + **$0.10 / active agent / month** + **$0.00015 / s** server-side tool execution + pay-as-you-go LLM. Remote MCP tools billed by the MCP provider. **[inferred]** 1k sessions on **one** always-on agent: platform fee ≈ $20/30d + $0.10 ≈ **$20.10/mo**, i.e. **~$0.02 / 1k sessions** of *Letta SKU* if that agent’s 1k sessions fit in the month — **memory LLM tokens dominate**. Sleep-time on a frontier model every 5 steps can exceed chat-model spend; the paper’s 2.5× amortization assumes many queries share \(c'\).

**LangGraph.** Checkpointer/Store = **your** Postgres/Redis bill + embedding calls if `index=` set. No per-memory SKU. **Cognee Cloud:** usage-based; ⚠️ no public per-recall unit price in sources consulted.

| Path | Memory layer | Generate (7k in / 0.5k out) | **[inferred] \(C_{1k}\)** |
| --- | --- | --- | --- |
| Mem0 Starter + mid generator | **$3.80** | **~$30** | **~$34** |
| Mem0 Pro + mid generator | **$4.98** | **~$30** | **~$35** + Dream LLM |
| Full-context 26k in, no memory SKU | $0 | **~$78** | **~$78** (91% higher p95 tokens in Mem0 Table 2 vs Mem0) |
| Letta SKU only (1 always-on agent) | **~$0.02** | LLM PAS | Dominated by sleep-time + chat tokens |
| Zep Flex | ⚠️ credits unspecified | LLM PAS | Use RPM 600/1000 as the NFR, not $/session |

**Cache and consolidation batch:**

- **Prompt cache:** STM prefix (system + core blocks + stable profile) should be the cached prefix. Compaction and block rewrites **break** the cache. Sleep-time that rewrites core memory mid-session trades personalization for cache hit rate.
- **Retrieval cache:** `(user_id, query_hash, index_version) → hits` with short TTL; invalidate on write. ⚠️ Hit rates unpublished.
- **Consolidation batch:** offline LLM. Sleep-time paper: 10 queries/context → **2.5×** lower average cost vs single-query test-time scaling. OpenAI Dreaming V3 ~**5×** less compute than prior dreaming (vendor; no $). Mem0 paper: Zep graph construction not real-time (hours); Mem0g “under a minute even in worst-case” in their harness.

### 3.2 Latency SLA targets and mitigations

Decompose **p99 retrieve** from **p99 generate**. Circuit-break the memory store independently of the LLM. Memory must **not** be on the critical path of “can the agent answer at all.”

**Mem0 paper Table 2 (LOCOMO, search + generate, GPT-4o-mini stack, 2025):**

| Method | Retrieved tok | Search p50 / p95 (s) | Total p50 / p95 (s) | Overall J |
| --- | --- | --- | --- | --- |
| Mem0 | 1,764 | **0.148 / 0.200** | **0.708 / 1.440** | 66.88% |
| Mem0g | 3,616 | 0.476 / 0.657 | 1.091 / 2.590 | 68.44% |
| Zep | 3,911 | 0.513 / 0.778 | 1.292 / 2.926 | 65.99% |
| Best RAG (k=2, 256) | 256×2 | 0.255 / 0.699 | 0.802 / 1.907 | 60.97% |
| A-Mem | 2,520 | 0.668 / 1.485 | 1.410 / 4.374 | 48.38% |
| LangMem (hot path) | 127 | **17.99 / 59.82** | 18.53 / 60.40 | 58.10% |
| OpenAI memories (pre-extracted) | 4,437 | — | 0.466 / 0.889 | 52.90% |
| Full-context | 26,031 | — | 9.870 / **17.117** | **72.90%** |

Mem0 p95 total is **91%** below full-context (1.440 vs 17.117). Full-context still wins J by ~6 pp on this 26k-token set — the economic case is *not* “memory is more accurate,” it is “memory is accurate enough at 1/10th the tokens and ~1/12th the p95.” LangMem’s 18–60 s search is a **control-plane** cost (LLM memory ops per query), not ANN.

**Mem0 platform v3 (2026, GitHub; p50 only, no p95/p99):** LoCoMo 0.88 s, LongMemEval 1.09 s, BEAM ~1.0–1.05 s, ~6.7–7.0k tok/query. ⚠️ No p99. OSS ≠ platform.

**Zep paper LongMemEval_S:** 2.58–3.20 s **e2e including generation and residential RTT to us-west-2**. Vendor retrieval-only: **155–162 ms**. Sub-200 ms is a **retrieve** SLO, not chat SLO.

**Anthropic.** ⚠️ No public p50 for compaction or memory-tool round-trips. Compaction adds one summarization forward pass at the trigger. Memory tool is extra tool-loop RTTs (`view` directory, then `view` files).

**p99:** ⚠️ **unpublished** for Mem0, Zep, Letta, LangGraph Store in the sources consulted. Budget p99 ≈ **2–4× p95 [inferred from typical ANN+LLM tails, not a vendor number]** and measure yourself.

Working interactive memory SLO **[inferred]** — not a vendor contract. Targets assume retrieve + constructor + mid generate, write async, cache-cold:

| Percentile | Retrieve / assemble | E2E to first token (incl. generate TTFT) | Mitigation |
| --- | --- | --- | --- |
| **p50** | Mem0 paper search **148 ms**; Zep vendor **155–162 ms**; hot STM assembly **<50–200 ms** | Mem0 paper total **0.708 s**; v3 LoCoMo **0.88 s** (p50 only) | Pin profile card; parallel dense∥BM25; do not wait on extract |
| **p95** | Mem0 search **200 ms**; Mem0 total **1.440 s**; Zep search **778 ms** (paper) | Mem0 total **1.440 s** vs full-context **17.117 s** | Timeout 200–500 ms retrieve **[policy]**; constructor cap; STM-only fallback |
| **p99** | **[inferred]** 2–4× p95: ~0.4–0.8 s search / ~3–6 s e2e if generate is mid-tier; graph-not-ready and RU throttle dominate | Separate generate p99; do not fold extract into chat SLO | Breaker → cached profile + STM; ingest watermark; never compact on the interactive fuse |

| Tier | Mitigations |
| --- | --- |
| p50 | Hot profile card; approximate token trim; retrieval cache `(user_id, query_hash, index_version)`; BFS from recent episodes instead of global graph |
| p95 | Constructor 1.6k–7k not 26k; skip cross-encoder on timeout; hedge replica; tool-clear before compact |
| p99 | Retrieve timeout 200–500 ms **[inferred SLO]**; error-rate breaker; fail **open** to STM; extract queued; community refresh / Dream as jobs |

### 3.3 Throughput and back-pressure

**RPM that matters is retrieve + extract, not user QPS.** 1k user QPS × 1 retrieve = 1k memory QPS; plus 2 adds/session on the write plane. Zep Flex **600 RPM** (~10 rps) / Flex Plus **1,000 RPM** is a hard fuse — 1k user QPS does not fit without cache, local ANN, or shedding. Mem0 Starter **5k retrievals/mo** is a **quota fuse**, not a latency SLO.

**Back-pressure design:**

1. Gateway admits interactive traffic if **retrieve** breaker is closed/half-open **or** STM-only is an allowed degrade. Extract breaker does **not** shed chat.
2. Bulkhead retrieve pool from extract/sleep-time pool — a graph rebuild must not starve TTFT.
3. Honor 429 with full jitter; do not retry poison extracts (same sha256 crash → DLQ).
4. Shed: drop sleep-time first, then cross-encoder (use RRF top-k), then dense (BM25 + STM), then STM-only. Never shed ACL. Never auto-write from web/tools when shed.
5. Write vs read isolation: extract workers use a separate LLM quota and a **watermark**, not the live constructor.
6. Community refresh / Dream / Graphiti rebuild: queue as a job (Flex/Batch class), not the interactive pool.

**Worked capacity [inferred].** 50 interactive turns/s, 1 retrieve, Mem0-class constructor, mid generate: memory SKU ~$4–5/1k → **$0.20–0.25/s** ≈ **$17–22k/mo** on the memory layer at 50 rps continuous, plus generate **~$30/1k** → **~$130k/mo**. Same 50 rps against Zep Flex 600 RPM **does not fit** (need 3,000 RPM). Cache and per-tenant local indexes are the escape hatch, not a bigger interactive extract.

**Unbounded growth.** Without policy, episodic stores grow linearly with turns; semantic stores grow with extract recall; graphs grow with entities×facts. Controls that exist: Redis/Store TTL; Letta archival unlimited **by design** (must add your own GC); Graphiti invalidation without delete (history **grows**); Mem0 v3 ADD-only (same); shallow checkpointers. **Capacity NFR is a product decision**, not a library default.

### 3.4 Availability, RPO/RTO, compliance — explicit NFR trade-offs

| NFR | Working target | Tension |
| --- | --- | --- |
| Availability | 99.9% **chat gateway** with STM-only degrade. Zep Enterprise: custom SLA/RPM. Mem0/Letta public pages: no e2e chat SLO | Degraded ≠ personalized; log `memory_miss` as a product metric |
| RPO | Write: last checkpointed `turn_id` / Kafka offset (seconds–minutes). Read: ingest **watermark** (facts invisible until episode linked). STM: last LangGraph checkpoint | Treating mid-graph-build as live violates citation integrity (Mem0’s Zep observation) |
| RTO | Interactive: fail over **<1 s** to STM + cached profile. Graph rebuild: hours (Zep construction); schedule, do not block chat | Fast failover vs same personalization |
| Consistency | Graphiti bi-temporal PIT. Letta shared blocks last-write-wins. Mem0 v3 ADD-only (read-time ranker picks current). LangGraph Store `put` replaces. PostgresSaver: one saver per process | PIT history vs Art. 17 hard-delete; LWW vs sleep-time races |
| Compliance | PII-before-embed; Art. 17 fan-out ≤1 month; Zep Enterprise SOC 2 Type II **checked on Enterprise, “—” on Flex/Flex Plus** in the public matrix (confirm Trust Center); HIPAA BAA listed on Zep Enterprise; Mem0 GDPR-ready claim + trust.mem0.ai; Letta Enterprise SAML/OIDC/RBAC | Invalidation ≠ erasure; HNSW tombstone ≠ VACUUM |
| Cost vs latency | Memory SKU **[inferred] $3.80–$4.98/1k** vs generate **~$30/1k** vs full-context **~$78/1k**; Zep retrieve 155 ms vs constructor 1.6k tok | Paying 115k full-context for a 1.6k constructor job |
| Recall vs poison | Write-time origin HMAC + no auto-write from web vs read-time ablation (SMSR authenticated ASR **8.0%** in 20-seed store) | Quality of extract vs L3 sleeper |
| Cache vs freshness | Stable profile prefix for prompt cache vs sleep-time rewrite mid-session | Personalization vs TTFT |

---

## 4. Distributed Resilience & Security

### 4.1 Durable write (Temporal / Kafka)

Research specifies an **idempotent checkpointed write plane**, not a vendor Temporal/Kafka runbook. Map it:

**Temporal (workflow = one turn extract or one CDC batch of episodes).** Activities: `redact_pii` → `acl_stamp` → `extract_facts` → `conflict_resolve` → `embed` → `graph_insert` → `watermark_advance` → optional `enqueue_consolidate`. Each activity is the checkpoint. Replay must **not** re-call the embed API for an already-keyed `embed_model+dim+memory_id`. Community / Dream / sleep-time is a **separate workflow** with max-parallel-per-tenant. Poison extract (repeated crash on same `turn_id`) → DLQ workflow, not infinite retry. Query plane never reads un-watermarked facts.

**Kafka (log = chain of custody).** Topics per tenant-shard: `memory.write`, `memory.forget`, `memory.watermark`, `memory.dlq`. Produce the redacted episode + origin HMAC **before** embed (outbox). Compact on `memory_id`. Poison (unparseable, repeated handler crash) → DLQ after N; do not block the partition. Watermark advance is a **single commit message** on `memory.watermark` that the read router consumes. Art. 17: `memory.forget` is a fan-out command with a completion offset; the audit log is a **different**, immutable topic.

**Durable stores and isolation keys:**

| System | Durability primitive | Isolation key | Resume / time-travel |
| --- | --- | --- | --- |
| LangGraph checkpointer | Postgres WAL / Redis AOF+repl | `thread_id` (+ optional `checkpoint_id`) | Replay from checkpoint; `delete_thread()` |
| LangGraph Store | Postgres rows / RedisJSON | `namespace` tuple | TTL sweeper; no built-in CRDT |
| Letta | DB-backed agent state (messages, blocks, passages) | `agent_id` | Messages survive compaction; ADE inspect |
| Graphiti/Zep | Graph DB + indexes (Neo4j in OSS paper; Zep proprietary Context Graph Engine in cloud 2026) | user/session graph | Point-in-time via `valid_at` |
| Anthropic memory tool | **Your** FS/DB | Your mapping of `/memories` | Versioning is your job; beta Memory Stores API has `memory_version_id` + SHA-256 |
| Mem0 platform | Managed vector+graph | `user_id` / agent / run (docs: four scopes) | ADD-only v3 = no in-place mutate; easier replay, harder correction |

**Conflict resolution.** Letta shared blocks: **last write wins**. Graphiti: LLM contradiction check, temporal invalidation, new information prioritized on transaction timeline \(T'\). Mem0 paper: LLM chooses ADD/UPDATE/DELETE/NOOP against top-s neighbors. Mem0 v3: **never overwrite**. Design **per-user single-writer** or optimistic version checks (Anthropic Memory Stores: `content_sha256` precondition).

> ⚠️ Gap: research has no measured Temporal replay cost for Graphiti community tables and no Kafka lag SLO for extract workers. Treat the mapping as the enterprise shape of research §3.

### 4.2 Failure taxonomy

| Class | Examples | Handler |
| --- | --- | --- |
| Transient | 429, 503, TLS reset, retrieve timeout, replica miss, embedder blip | Full-jitter retry on **idempotent reads**; honor Retry-After; do not retry Art. 17 blindly |
| Permanent | 400 illegal namespace, embed dim change, missing `user_id` on write | Fail the write to DLQ; read degrades to STM; do not loop extract |
| Poison pill | Same turn crashes the extractor; web observation auto-promoted to semantic; recursive sleep-time rewrite | sha256/`turn_id` + N crashes → DLQ; never auto-write from tools/web; single-writer + max jobs per tenant |
| Semantic | Schema-valid retrieve with omitted ACL; stale “training for a marathon” + “sprained ankle”; sleeper L3 record | PEP at vector boundary; bi-temporal ranker; read-time context-sensitive score; not a retry |

**Mechanism → mitigate:** poisoning write → origin HMAC (SMSR unsigned ASR 93–100% → **0%**; TMA-NM **0%**); never treat retrieved text as write-authorized. L3 sleeper → read-time score + ablation (SMSR ASR **8.0%**, 95% CI [5.8, 10.9]). eTAMP (ASR **32.5%** GPT-5-mini, **×8** under UI frustration) → no web auto-write. Stale facts → bi-temporal + dreaming. Over-retrieval → constructor cap. Identity mix-up → namespace. Growth → TTL/GC. Write/read race → watermark; fallback to episodes. LWW clobber → sleep-time single writer. Compaction amnesia → memory tool **before** compact. Soft-delete ≠ Art. 17. Procedural injection (CLAUDE.md/MCP) → trust UI + pin versions. DMR/LoCoMo overfitting → LongMemEval_M / BEAM 10M + poison tests.

**Idempotency key (write):** `memory_id = hash(tenant, user_id, fact_norm, embedder_version)`. Extract job: `turn_id`. Query retries: cache key includes `index_version` + ACL principal. Forget: `erasure_id` with a completion watermark.

### 4.3 Circuit breaker and fallback chain

Per downstream (memory retrieve, embedder, extract LLM, graph construction, sleep-time pool):

- **Closed:** traffic flows; consecutive failures or error-rate window trips **open**.
- **Open:** fail fast; timer (e.g. 30 s). Interactive traffic takes the next fallback; extract can wait on a queue.
- **Half-open:** one probe (or a small percentage). Success → closed; fail → open.

Retrieve timeout **200–500 ms [inferred SLO, not vendor]**. Bulkhead vs LLM pool. Anthropic `pause_after_compaction` is a **human** breaker for lossy STM.

```
CLOSED ──(failures ≥ N or error-rate)──▶ OPEN ──(timer elapsed)──▶ HALF_OPEN
  ▲                                      │ fail fast                 │
  │                                      │ fallback chain            ├── probe OK ──▶ CLOSED
  └──────────────────────────────────────┴───────────────────────────┘ probe fail ──▶ OPEN
```

**Fallback chain (research order):**

1. Last-good **retrieve cache** (`user_id`, query_hash, `index_version`).
2. **Cached profile card** (hot semantic; may be stale).
3. **STM only** — last-N + core blocks; log `memory_miss`.
4. Answer **without personalization** — memory is not allowed to take down chat. (Contrast RAG: refuse ungrounded if policy forbids. Memory fail-open is the documented degrade.)

Hedging: duplicate retrieve to a replica on p99; cancel loser. On graph-not-ready: **read episodes** (non-lossy) if facts are not watermarked. On extract failure: ack user; retry async; idempotent add.

### 4.4 Zero-Trust MCP, tool RBAC, PII, right-to-be-forgotten, immutable logs

**Zero-Trust MCP around memory.** MCP is **transport, not governance** (Cloudflare; repeated in 2026 production guides). Memory MCP servers (Mem0 MCP, Zep Memory MCP, Cognee MCP, custom archival) must implement the 2025–26 authorization stack:

- **OAuth 2.1 + PKCE (S256)** for remote servers; implicit flow gone.
- **RFC 9728** Protected Resource Metadata + **RFC 8414** AS metadata.
- **RFC 8707 Resource Indicators** so a token minted for server A cannot be replayed on server B.
- **No token passthrough** (confused deputy); **RFC 8693** token exchange for downstream APIs.
- `tenant_id` / `user_id` **only from the verified token**, never from tool arguments (Asana-class leak pattern).

Claude Code: first-time folder + new MCP servers require trust verification; **disabled under `-p`**. Anthropic does **not** security-audit third-party MCP servers. Check Point 2026: CVE-2025-59536 / **CVE-2026-21852** — malicious repo `.mcp.json` / `ANTHROPIC_BASE_URL` ran or exfiltrated **before** trust prompt (fixed ≥ 2.0.65). GitHub issue #21674: `~/.claude/CLAUDE.md` is a **global persistent injection** surface. Anthropic engineering (2026) names **persistent memory poisoning** (product memory, CLAUDE.md, workspaces, scheduled-agent state) as a session-startup classifier problem.

**Tool RBAC.** Separate MCP tools: `memory_view` (read), `memory_create` (write semantic/episodic), `memory_delete` (Art. 17 / user edit). No omnibus `memory(query, tenant_id)`. Write tools off the schema unless the turn is authorized to learn. Sleep-time agent gets write tools; primary worker agents get read-only on **shared** blocks.

**Tenant isolation:**

| Layer | Correct control | Failure |
| --- | --- | --- |
| Vector/graph query | **Pre-filter** `tenant_id`/`user_id` on every ANN, BM25, and Cypher path | Post-filter after top-k leaks neighbors |
| Namespaces | LangGraph `("t", tenant, "u", user)`; Mem0 user/agent/app/run scopes; Letta per-agent (shared blocks are explicit) | Shared block attached to two customers’ agents |
| Embeddings | Tag `{user_id, session_id, category, ingested_at}` at **write** | Article 17 cannot find vectors |
| MCP | Token-bound tenant registry; tool allow-list at auth time | Tenant in JSON args |
| Physical | Enterprise: per-tenant index / VPC / BYOC (Zep Enterprise: Cloud, BYOK, BYOC) | Shared HNSW + metadata hope |

**PII pipeline:** detect → redact **before embed** → audit placeholders (never raw). Memories **are** PII when they encode identity, health, location, credentials, or behavioral profiles. Minimize at extract: do not write raw message logs into semantic memory; write facts; keep episodes access-controlled. Purpose limitation: ChatGPT-style “chat history memory” that cannot be inspected (2025 Ars) is a governance defect for enterprise — require **exportable, editable** semantic stores (OpenAI 2026 memory summary is the product correction). After retrieve, DLP **before prompt**.

**Right to be forgotten (GDPR Art. 17 + Art. 12(3)).** Fan-out, not one DELETE:

1. Semantic rows / graph nodes+edges tagged by `user_id`.
2. Episodes / checkpoints / Store keys / `/memories` files.
3. Vector IDs (HNSW **soft-delete** until compaction/VACUUM).
4. Prompt/response caches.
5. Trace vendors (LangSmith/Langfuse: API delete, physical purge delayed).
6. Backups — crypto-shred **per-user keys** or wait backup TTL inside the month.
7. Fine-tuned weights: **unlearning unsolved**; architectural control = **do not train on raw personal memory**.

Mem0 v3 ADD-only makes “update in place” easier to audit but **deletion is a separate pipeline**. Graphiti invalidation preserves history — **Art. 17 requires a hard-delete path**. Provenance maps (episode ↔ fact) are what make fan-out possible. Prove Art. 17 on a **staging clone** of prod indexes, including HNSW compaction and traces.

**Audit / immutable logs.** Need: who wrote, who read, which query, which \(k\), which memories entered the prompt, model/index versions. Zep Enterprise: audit + API logs **1 year**. Mem0 platform: audit logs “by default.” LangGraph: checkpoint metadata + LangSmith. Anthropic Memory Stores: `content_sha256`, `memory_version_id` for tamper-evident heads. TMA-NM (2026): **origin-bound, non-malleable authority** — content- and lineage-only defenses fail under summarization/tool-echo laundering (up to **68%** ASR); write-time origin binding + Sybil-resistant corroboration → **0%** ASR in their harness. Kafka log or WORM object store; **hash-chain** the audit events. Metrics: leakage rate, entitlement violation rate, `memory_miss`, erasure completeness.

---

## 5. Production Enterprise Code

Stdlib-only module: full-jitter retries, circuit breaker (closed → open → half-open), fallback chain (hybrid retrieve → cached profile → STM-only), correlation-id JSON logs, PII detect→redact→audit, tenant isolation from the token principal (never tool args), Generative Agents salience, constructor token cap, async write vs sync read, Art. 17 forget fan-out, hash-chained audit. Run: `python memory_gateway.py`.

```python
#!/usr/bin/env python3
"""Production memory gateway primitives (stdlib only). Run: python memory_gateway.py"""
from __future__ import annotations
import hashlib
import hmac
import json
import logging
import math
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable
RRF_K = 60
RECENCY_DECAY = 0.995
RETRIEVE_TIMEOUT_S = 0.4
CONSTRUCTOR_TOKENS = 400
STM_TOKEN_BUDGET = 200
TOOL_CLEAR_KEEP = 3

class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "user_id": getattr(record, "user_id", None),
            "index_version": getattr(record, "index_version", None),
            "breaker": getattr(record, "breaker", None),
            "degraded": getattr(record, "degraded", None),
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
    correlation_id: str, tenant: str, user_id: str, index_version: str
) -> CorrelationAdapter:
    base = logging.getLogger("memory.gateway")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    return CorrelationAdapter(
        base,
        {
            "correlation_id": correlation_id,
            "tenant": tenant,
            "user_id": user_id,
            "index_version": index_version,
        },
    )
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
)
def redact_pii(text: str) -> tuple[str, list[dict[str, str]]]:
    audit: list[dict[str, str]] = []
    out = text
    for label, pat in _PII_PATTERNS:
        def _sub(m: re.Match[str], _label: str = label) -> str:
            digest = hashlib.sha256(m.group(0).encode()).hexdigest()[:12]
            token = f"<{_label}:{digest}>"
            audit.append({"type": _label, "placeholder": token})
            return token
        out = pat.sub(_sub, out)
    return out, audit

class TransientError(Exception):
    pass

class PermanentError(Exception):
    pass

class CircuitOpenError(Exception):
    pass

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_seconds: float = 30.0,
        half_open_max: int = 1,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self.half_open_max = half_open_max
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = 0
        self._lock = threading.Lock()
    @property
    def state(self) -> BreakerState:
        with self._lock:
            self._maybe_half_open()
            return self._state
    def _maybe_half_open(self) -> None:
        if (
            self._state is BreakerState.OPEN
            and (time.monotonic() - self._opened_at) >= self.recovery_seconds
        ):
            self._state = BreakerState.HALF_OPEN
            self._half_open_inflight = 0
    def allow(self) -> None:
        with self._lock:
            self._maybe_half_open()
            if self._state is BreakerState.OPEN:
                raise CircuitOpenError("circuit open")
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_inflight >= self.half_open_max:
                    raise CircuitOpenError("half-open probe in flight")
                self._half_open_inflight += 1
    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._half_open_inflight = 0
            self._state = BreakerState.CLOSED
    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_inflight = 0
def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 3,
    base_seconds: float = 0.05,
    max_seconds: float = 1.0,
    retry_after: float | None = None,
) -> Any:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            cap = min(max_seconds, base_seconds * (2**i))
            sleep_s = max(cap, retry_after or 0.0)
            time.sleep(random.random() * sleep_s)
    assert last is not None
    raise last
_TOKEN = re.compile(r"[a-z0-9]+", re.I)
def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())
def approx_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)
def embed(text: str, dim: int = 32) -> tuple[float, ...]:
    vec = [0.0] * dim
    for tok in tokenize(text):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
        vec[(h >> 16) % dim] -= 0.35
    return tuple(vec)
def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
def minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]
@dataclass(frozen=True)

class Principal:
    tenant: str
    user_id: str
    roles: frozenset[str]
    origin_key: bytes

class MemoryKind(Enum):
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    WORKING = "working"
@dataclass

class Message:
    role: str
    text: str
    tokens: int
    is_tool_result: bool = False
@dataclass

class MemoryRecord:
    memory_id: str
    tenant: str
    user_id: str
    kind: MemoryKind
    text: str
    tokens: list[str]
    vector: tuple[float, ...]
    importance: float
    created_at: float
    last_access: float
    origin: str
    origin_hmac: str
    episode_ids: tuple[str, ...] = ()
    valid: bool = True
    deleted: bool = False
    watermarked: bool = True
@dataclass

class ScoredMemory:
    record: MemoryRecord
    score: float
    rank: int
    source: str
    recency: float = 0.0
    relevance: float = 0.0
    salience: float = 0.0
@dataclass

class AuditEvent:
    seq: int
    prev_hash: str
    digest: str
    payload: dict[str, Any]

class ImmutableAuditLog:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = threading.Lock()
    def append(self, payload: dict[str, Any]) -> AuditEvent:
        with self._lock:
            prev = self._events[-1].digest if self._events else "genesis"
            body = json.dumps({"prev": prev, "payload": payload}, sort_keys=True, default=str)
            digest = hashlib.sha256(body.encode()).hexdigest()
            event = AuditEvent(len(self._events) + 1, prev, digest, payload)
            self._events.append(event)
            return event
    def __len__(self) -> int:
        return len(self._events)
def origin_mac(key: bytes, origin: str, text: str) -> str:
    return hmac.new(key, f"{origin}\n{text}".encode(), hashlib.sha256).hexdigest()
def verify_origin(record: MemoryRecord, key: bytes) -> bool:
    expected = origin_mac(key, record.origin, record.text)
    return hmac.compare_digest(expected, record.origin_hmac)
def write_salience(text: str) -> float:
    """Write-time importance in [1, 10]. Interview stand-in for the Generative Agents LLM rater."""
    lowered = text.lower()
    score = 4.0
    for needle, bump in (
        ("allerg", 4.0),
        ("vegetarian", 3.5),
        ("policy", 3.0),
        ("prefer", 2.0),
        ("hello", -2.0),
    ):
        if needle in lowered:
            score += bump
    return max(1.0, min(10.0, score))
def trim_messages(messages: list[Message], max_tokens: int) -> list[Message]:
    """Keep system + a legal human/AI suffix under a token budget (trim_messages analogue)."""
    system = [m for m in messages if m.role == "system"]
    rest = [m for m in messages if m.role != "system"]
    kept: list[Message] = []
    budget = max_tokens - sum(m.tokens for m in system)
    for msg in reversed(rest):
        if msg.tokens > budget:
            break
        kept.append(msg)
        budget -= msg.tokens
    kept.reverse()
    if kept and kept[0].role == "ai":
        kept = kept[1:]
    return system + kept
def clear_stale_tool_results(messages: list[Message], keep: int = TOOL_CLEAR_KEEP) -> list[Message]:
    tool_idx = [i for i, m in enumerate(messages) if m.is_tool_result]
    drop = set(tool_idx[:-keep]) if len(tool_idx) > keep else set()
    out: list[Message] = []
    for i, msg in enumerate(messages):
        if i in drop:
            out.append(Message(msg.role, "[cleared tool result]", 4, True))
        else:
            out.append(msg)
    return out
def rrf_merge(lists: list[list[ScoredMemory]], k: int = RRF_K) -> list[ScoredMemory]:
    scores: dict[str, float] = {}
    by_id: dict[str, MemoryRecord] = {}
    for ranking in lists:
        for item in ranking:
            scores[item.record.memory_id] = scores.get(item.record.memory_id, 0.0) + 1.0 / (
                k + item.rank
            )
            by_id[item.record.memory_id] = item.record
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [
        ScoredMemory(by_id[mid], score, rank, "rrf")
        for rank, (mid, score) in enumerate(ordered, start=1)
    ]
def bm25_scores(
    query_tokens: list[str],
    records: list[MemoryRecord],
    k1: float = 1.2,
    b: float = 0.75,
) -> list[tuple[MemoryRecord, float]]:
    if not records:
        return []
    df: dict[str, int] = {}
    for rec in records:
        for tok in set(rec.tokens):
            df[tok] = df.get(tok, 0) + 1
    n = len(records)
    avgdl = sum(len(r.tokens) for r in records) / n
    scored: list[tuple[MemoryRecord, float]] = []
    for rec in records:
        tf: dict[str, int] = {}
        for tok in rec.tokens:
            tf[tok] = tf.get(tok, 0) + 1
        score = 0.0
        dl = max(len(rec.tokens), 1)
        for tok in query_tokens:
            f = tf.get(tok, 0)
            if f == 0:
                continue
            n_q = df.get(tok, 0)
            idf = math.log(1.0 + (n - n_q + 0.5) / (n_q + 0.5))
            denom = f + k1 * (1.0 - b + b * dl / avgdl)
            score += idf * (f * (k1 + 1.0)) / denom
        scored.append((rec, score))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored
def generative_agents_score(
    records: list[MemoryRecord],
    query_vec: tuple[float, ...],
    now: float,
) -> list[ScoredMemory]:
    if not records:
        return []
    recency = [RECENCY_DECAY ** max(0.0, (now - r.last_access) / 3600.0) for r in records]
    importance = [r.importance / 10.0 for r in records]
    relevance = [cosine(query_vec, r.vector) for r in records]
    rec_n, imp_n, rel_n = minmax(recency), minmax(importance), minmax(relevance)
    fused = [rec_n[i] + imp_n[i] + rel_n[i] for i in range(len(records))]
    order = sorted(range(len(records)), key=lambda i: fused[i], reverse=True)
    return [
        ScoredMemory(
            records[i],
            fused[i],
            rank,
            "salience",
            recency=rec_n[i],
            relevance=rel_n[i],
            salience=fused[i],
        )
        for rank, i in enumerate(order, start=1)
    ]
def pack_constructor(hits: list[ScoredMemory], budget: int) -> list[ScoredMemory]:
    packed: list[ScoredMemory] = []
    used = 0
    for hit in hits:
        cost = max(1, len(hit.record.tokens))
        if used + cost > budget:
            continue
        packed.append(hit)
        used += cost
        hit.record.last_access = time.time()
    return packed

class MemoryStore:
    """In-process stand-in for pgvector + episode log + Redis profile card."""
    def __init__(self, index_version: str = "idx-1") -> None:
        self.index_version = index_version
        self._lock = threading.Lock()
        self.records: dict[str, MemoryRecord] = {}
        self.threads: dict[tuple[str, str], list[Message]] = {}
        self.profile: dict[tuple[str, str], str] = {}
        self.vector_tombstones: set[str] = set()
        self.write_queue: list[dict[str, Any]] = []
    def namespace(self, principal: Principal) -> tuple[str, str]:
        return (principal.tenant, principal.user_id)
    def eligible(self, principal: Principal) -> list[MemoryRecord]:
        with self._lock:
            return [
                r
                for r in self.records.values()
                if r.tenant == principal.tenant
                and r.user_id == principal.user_id
                and r.valid
                and not r.deleted
                and r.watermarked
                and r.memory_id not in self.vector_tombstones
            ]
    def put(self, record: MemoryRecord) -> None:
        with self._lock:
            self.records[record.memory_id] = record
    def forget_user(self, tenant: str, user_id: str) -> list[str]:
        """Art. 17 fan-out: semantic + episodic + STM + profile + vector tombstones."""
        removed: list[str] = []
        with self._lock:
            for mid, rec in list(self.records.items()):
                if rec.tenant == tenant and rec.user_id == user_id:
                    rec.deleted = True
                    rec.valid = False
                    rec.text = ""
                    rec.tokens = []
                    rec.vector = tuple(0.0 for _ in rec.vector)
                    self.vector_tombstones.add(mid)
                    removed.append(mid)
            self.threads.pop((tenant, user_id), None)
            self.profile.pop((tenant, user_id), None)
        return removed

class MemoryGateway:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.retrieve_breaker = CircuitBreaker()
        self.audit = ImmutableAuditLog()
        self._cache: dict[tuple[str, str, str], list[ScoredMemory]] = {}
    def _log(self, principal: Principal, correlation_id: str) -> CorrelationAdapter:
        return build_logger(
            correlation_id, principal.tenant, principal.user_id, self.store.index_version
        )
    def append_stm(self, principal: Principal, role: str, text: str, *, tool: bool = False) -> None:
        ns = self.store.namespace(principal)
        redacted, _ = redact_pii(text)
        msg = Message(role, redacted, approx_tokens(redacted), tool)
        with self.store._lock:
            buf = self.store.threads.setdefault(ns, [])
            buf.append(msg)
            cleared = clear_stale_tool_results(buf)
            self.store.threads[ns] = trim_messages(cleared, STM_TOKEN_BUDGET)
    def stm_window(self, principal: Principal) -> list[Message]:
        with self.store._lock:
            return list(self.store.threads.get(self.store.namespace(principal), []))
    def enqueue_write(
        self,
        principal: Principal,
        text: str,
        *,
        origin: str,
        correlation_id: str,
    ) -> str:
        """Ack the user; extract is off the TTFT path (Letta sleep-time pattern)."""
        redacted, pii = redact_pii(text)
        if origin == "web":
            raise PermanentError("web observations are not auto-promoted to semantic memory")
        job = {
            "turn_id": str(uuid.uuid4()),
            "tenant": principal.tenant,
            "user_id": principal.user_id,
            "text": redacted,
            "origin": origin,
            "pii": pii,
            "correlation_id": correlation_id,
        }
        with self.store._lock:
            self.store.write_queue.append(job)
        return job["turn_id"]
    def drain_writes(self, principal: Principal) -> list[str]:
        """Temporal-activity analogue: extract → HMAC → stamp → watermarked put."""
        created: list[str] = []
        with self.store._lock:
            jobs = [
                j
                for j in self.store.write_queue
                if j["tenant"] == principal.tenant and j["user_id"] == principal.user_id
            ]
            self.store.write_queue = [
                j
                for j in self.store.write_queue
                if not (j["tenant"] == principal.tenant and j["user_id"] == principal.user_id)
            ]
        for job in jobs:
            ids = self._commit_extract(principal, job)
            created.extend(ids)
        return created
    def _commit_extract(self, principal: Principal, job: dict[str, Any]) -> list[str]:
        now = time.time()
        episode_id = "ep-" + job["turn_id"][:8]
        episode = MemoryRecord(
            memory_id=episode_id,
            tenant=principal.tenant,
            user_id=principal.user_id,
            kind=MemoryKind.EPISODIC,
            text=job["text"],
            tokens=tokenize(job["text"]),
            vector=embed(job["text"]),
            importance=3.0,
            created_at=now,
            last_access=now,
            origin=job["origin"],
            origin_hmac=origin_mac(principal.origin_key, job["origin"], job["text"]),
            episode_ids=(episode_id,),
        )
        self.store.put(episode)
        facts: list[str] = []
        for raw in re.split(r"[.;\n]", job["text"]):
            fact = raw.strip()
            if len(tokenize(fact)) >= 3:
                facts.append(fact)
        if not facts:
            facts = [job["text"]]
        ids = [episode_id]
        for fact in facts:
            mid = hashlib.sha256(
                f"{principal.tenant}|{principal.user_id}|{fact.lower()}|{self.store.index_version}".encode()
            ).hexdigest()[:16]
            rec = MemoryRecord(
                memory_id=mid,
                tenant=principal.tenant,
                user_id=principal.user_id,
                kind=MemoryKind.SEMANTIC,
                text=fact,
                tokens=tokenize(fact),
                vector=embed(fact),
                importance=write_salience(fact),
                created_at=now,
                last_access=now,
                origin=job["origin"],
                origin_hmac=origin_mac(principal.origin_key, job["origin"], fact),
                episode_ids=(episode_id,),
            )
            self.store.put(rec)
            ids.append(mid)
            if rec.importance >= 6.0:
                ns = self.store.namespace(principal)
                prev = self.store.profile.get(ns, "")
                self.store.profile[ns] = (prev + " " + fact).strip()
        self.audit.append(
            {
                "op": "write",
                "correlation_id": job["correlation_id"],
                "tenant": principal.tenant,
                "user_id": principal.user_id,
                "ids": ids,
                "pii": job["pii"],
            }
        )
        return ids
    def _dense(self, records: list[MemoryRecord], query: str, k: int) -> list[ScoredMemory]:
        qv = embed(query)
        ranked = sorted(records, key=lambda r: cosine(qv, r.vector), reverse=True)
        return [
            ScoredMemory(r, cosine(qv, r.vector), i, "dense")
            for i, r in enumerate(ranked[:k], start=1)
        ]
    def _sparse(self, records: list[MemoryRecord], query: str, k: int) -> list[ScoredMemory]:
        ranked = bm25_scores(tokenize(query), records)
        return [
            ScoredMemory(r, score, i, "bm25")
            for i, (r, score) in enumerate(ranked[:k], start=1)
        ]
    def retrieve(
        self,
        principal: Principal,
        query: str,
        *,
        correlation_id: str,
        k: int = 20,
        now: float | None = None,
    ) -> tuple[list[ScoredMemory], str, list[dict[str, str]]]:
        log = self._log(principal, correlation_id)
        redacted, pii = redact_pii(query)
        cache_key = (principal.tenant, principal.user_id, hashlib.sha256(redacted.encode()).hexdigest())
        stm = self.stm_window(principal)
        def _search() -> list[ScoredMemory]:
            self.retrieve_breaker.allow()
            eligible = self.store.eligible(principal)
            for rec in eligible:
                if not verify_origin(rec, principal.origin_key):
                    raise PermanentError("origin hmac mismatch")
            fused = rrf_merge(
                [self._dense(eligible, redacted, k), self._sparse(eligible, redacted, k)]
            )
            scored = generative_agents_score(
                [h.record for h in fused], embed(redacted), now or time.time()
            )
            packed = pack_constructor(scored, CONSTRUCTOR_TOKENS)
            self.retrieve_breaker.record_success()
            self._cache[cache_key] = packed
            return packed
        mode = "hybrid"
        hits: list[ScoredMemory] = []
        try:
            hits = retry_call(_search)
        except CircuitOpenError:
            mode = "cached_profile" if cache_key in self._cache or self.store.profile.get(
                self.store.namespace(principal)
            ) else "stm_only"
            hits = list(self._cache.get(cache_key, []))
            log.warning("retrieve circuit open; degrading", extra={"degraded": mode, "breaker": "open"})
        except TransientError:
            self.retrieve_breaker.record_failure()
            mode = "cached_profile"
            hits = list(self._cache.get(cache_key, []))
            log.warning("retrieve transient; cached fallback", extra={"degraded": mode})
        except PermanentError:
            mode = "stm_only"
            log.error("retrieve permanent failure; STM only", extra={"degraded": mode})
        if not hits:
            profile = self.store.profile.get(self.store.namespace(principal), "")
            if profile and mode != "stm_only":
                mode = "cached_profile"
                fake = MemoryRecord(
                    memory_id="profile",
                    tenant=principal.tenant,
                    user_id=principal.user_id,
                    kind=MemoryKind.SEMANTIC,
                    text=profile,
                    tokens=tokenize(profile),
                    vector=embed(profile),
                    importance=8.0,
                    created_at=time.time(),
                    last_access=time.time(),
                    origin="profile",
                    origin_hmac=origin_mac(principal.origin_key, "profile", profile),
                )
                hits = [ScoredMemory(fake, 1.0, 1, "profile", salience=1.0)]
            else:
                mode = "stm_only"
                log.info("memory_miss", extra={"degraded": mode})
        self.audit.append(
            {
                "op": "read",
                "correlation_id": correlation_id,
                "tenant": principal.tenant,
                "user_id": principal.user_id,
                "k": k,
                "mode": mode,
                "memory_ids": [h.record.memory_id for h in hits],
                "stm_msgs": len(stm),
                "pii": pii,
            }
        )
        return hits, mode, pii
    def forget(self, principal: Principal, *, correlation_id: str) -> list[str]:
        removed = self.store.forget_user(principal.tenant, principal.user_id)
        self._cache = {k: v for k, v in self._cache.items() if k[0:2] != (principal.tenant, principal.user_id)}
        self.audit.append(
            {
                "op": "forget",
                "correlation_id": correlation_id,
                "tenant": principal.tenant,
                "user_id": principal.user_id,
                "removed": removed,
            }
        )
        return removed
def _demo() -> None:
    store = MemoryStore()
    gw = MemoryGateway(store)
    alice = Principal("acme", "u-alice", frozenset({"user"}), b"alice-origin-key")
    bob = Principal("acme", "u-bob", frozenset({"user"}), b"bob-origin-key")
    other = Principal("globex", "u-alice", frozenset({"user"}), b"globex-key")
    cid = str(uuid.uuid4())
    gw.append_stm(alice, "system", "You are a support copilot.")
    gw.append_stm(alice, "human", "I am vegetarian. Email me at jane@acme.test. SSN 123-45-6789.")
    turn = gw.enqueue_write(
        alice,
        "I am vegetarian. Email me at jane@acme.test. SSN 123-45-6789.",
        origin="user",
        correlation_id=cid,
    )
    assert turn
    written = gw.drain_writes(alice)
    assert any(store.records[i].kind is MemoryKind.SEMANTIC for i in written if i in store.records)
    assert any(store.records[i].kind is MemoryKind.EPISODIC for i in written if i in store.records)
    assert all("jane@acme.test" not in store.records[i].text for i in written)
    assert all("123-45-6789" not in store.records[i].text for i in written)
    hits, mode, pii = gw.retrieve(
        alice, "email jane@acme.test about the vegetarian diet", correlation_id=cid
    )
    assert mode == "hybrid"
    assert pii and any(x["type"] == "email" for x in pii)
    assert any("vegetarian" in h.record.text.lower() for h in hits)
    assert all(h.record.user_id == "u-alice" and h.record.tenant == "acme" for h in hits)
    assert all(h.salience >= 0.0 for h in hits)
    leaked, leaked_mode, _ = gw.retrieve(bob, "what diet does the user follow?", correlation_id=str(uuid.uuid4()))
    assert all(h.record.user_id != "u-alice" or h.record.memory_id == "profile" for h in leaked)
    assert leaked_mode in {"hybrid", "stm_only", "cached_profile"}
    cross, _, _ = gw.retrieve(other, "vegetarian?", correlation_id=str(uuid.uuid4()))
    assert all(h.record.tenant != "acme" or h.record.user_id != "u-alice" for h in cross)
    try:
        gw.enqueue_write(alice, "promo from a webpage", origin="web", correlation_id=cid)
        raise AssertionError("web auto-write must fail")
    except PermanentError:
        pass
    gw.retrieve_breaker.record_failure()
    gw.retrieve_breaker.record_failure()
    gw.retrieve_breaker.record_failure()
    assert gw.retrieve_breaker.state is BreakerState.OPEN
    degraded, dmode, _ = gw.retrieve(alice, "diet?", correlation_id=str(uuid.uuid4()))
    assert dmode in {"cached_profile", "stm_only"}
    assert degraded
    removed = gw.forget(alice, correlation_id=str(uuid.uuid4()))
    assert removed
    gone, gmode, _ = gw.retrieve(alice, "diet?", correlation_id=str(uuid.uuid4()))
    assert all(h.record.deleted or h.record.memory_id == "profile" for h in gone) or gmode == "stm_only"
    assert not store.eligible(alice)
    assert len(gw.audit) >= 3
    print(
        json.dumps(
            {
                "write_ids": written,
                "retrieve_mode": mode,
                "salience_top": hits[0].salience if hits else None,
                "pii_placeholders": pii,
                "bob_isolated": all(h.record.user_id != "u-alice" for h in leaked),
                "cross_tenant_empty": len(cross) == 0 or all(h.record.tenant == "globex" for h in cross),
                "breaker_open_mode": dmode,
                "forgotten": removed,
                "post_forget_eligible": len(store.eligible(alice)),
                "audit_head": gw.audit._events[0].digest[:12],
                "stm_tokens": sum(m.tokens for m in gw.stm_window(alice)),
            },
            indent=2,
        )
    )

if __name__ == "__main__":
    _demo()
```

**Interview probes:** token principal pre-filters the store; semantic ≠ episodic (`episode_ids`); write is enqueue-then-drain; salience = recency+importance+relevance then constructor-pack; origin HMAC on read; web cannot auto-write; breaker open → STM-only (`memory_miss`); `forget` fans out rows+STM+profile+vector tombstones; PII never embeds raw; audit hash-chains. Run `python memory_gateway.py`.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers and component choices are from the research file.

### Scenario 1 — B2C copilot (10M MAU, $ budget, PII)

**Problem statement.** Design memory for a consumer copilot at **10M MAU**. Users expect “it remembers I’m vegetarian” across sessions. Budget is generation-dominated; memory SKU must stay **[inferred] ~$4–5 / 1k sessions** (Mem0 Starter/Pro math), not full-context **~$78 / 1k**. PII (email, health, location) will land in chat. Retrieve p95 target **<300 ms [target, measure]**. Consolidation must not block TTFT. Art. 17 fan-out must be testable quarterly. A PM wants to “just stuff the last 26k tokens like the LoCoMo full-context winner.”

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Mobile /   │ SSE │ CONTROL PLANE                                             │
│ Web copilot│────▶│ Gateway: auth, tenant TPM, correlation-id, retrieve RPM   │
└────────────┘     │ Policy: PII redact before embed; ACL from token           │
                   │ Router: STM trim 8k + profile card; retrieve if breaker ok│
                   │ Orchestrator: constructor ≤4k tok; enqueue write; no wait │
                   │ pin index_version; retrieve/extract breakers bulkheaded   │
                   └────┬──────────────────────────────┬───────────────────────┘
                        │ READ                         │ WRITE (async)
                        ▼                              ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ DATA PLANE       │        │ Temporal extract + Kafka     │
                   │ hot: 2–4k profile│        │ pair-wise facts; ADD-only or │
                   │  + last-8k STM   │        │ invalidation; origin HMAC    │
                   │ warm: Mem0/Zep   │        │ sleep-time / Dream daily     │
                   │  k≤20, ≤4k tok   │        │ TOOL: memory_view (primary)  │
                   │ RRF + salience   │        │      memory_create (sleep)   │
                   └────────┬─────────┘        └──────────────┬───────────────┘
                            │                                 │
                            ▼                                 ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE / TELEMETRY                                   │
                   │ Per-user encryption keys; episodes 30–90d TTL; traces 7–30d│
                   │ WORM audit; Art.17 fan-out + HNSW VACUUM + crypto-shred   │
                   └───────────────────────────────────────────────────────────┘
```

**Technology choices.** Hot: 2–4k tok profile card (Letta-style blocks or Mem0 pinned top memories) + last 8k tok window (`trim_messages`). Warm: Mem0 platform or Zep retrieve \(k\leq 20\), constructor ≤4k tok. Write: **async** extract; do not block TTFT. Cold: episodes 30–90 d TTL; traces 7–30 d. Per-user encryption keys; no training on memories. Dreaming/sleep-time **daily**, not per turn. Fallback: cache → profile card → STM-only. Reject stuffing 26k–115k (Mem0 Table 2: full-context J +6 pp, p95 **17.1 s** vs Mem0 **1.44 s**).

**Trade-off evaluation matrix.**

| Dimension | A. Full-context 26k–115k in the window, no LTM | B. Recommended: hot profile 2–4k + STM 8k + async Mem0/Zep retrieve constructor ≤4k; daily Dream | C. Letta blocks + sleep-time every 5 steps on a frontier model for every user |
| --- | --- | --- | --- |
| Cost | Generate **[inferred] ~$78/1k** at 26k in; wins LoCoMo J by ~6 pp | Memory SKU **[inferred] $3.80–$4.98/1k** + generate **~$30/1k** → **~$34–35/1k**. Dream extra LLM, offline | Letta SKU ~$0.02/1k/agent **but** sleep-time frontier tokens can **exceed** chat spend; 2.5× amortize only if 10 queries share \(c'\) |
| Latency | p95 total **17.117 s** (Mem0 Table 2 full-context) | Retrieve p50 148–162 ms class; p95 total **1.440 s** (paper Mem0); target retrieve p95 <300 ms **[measure]** | Extra sleep-time RTTs; mid-session block rewrite **breaks prompt cache** |
| Ops | No extract pipeline; window overflow = silent drop | Two planes; watermark; quota (Mem0 5k retrievals/mo Starter) | ADE debug; last-write-wins on blocks; per-user always-on agents at 10M MAU |
| Security | PII sits in every prompt-cache prefix for the TTL | PII-before-embed; per-user keys; Art. 17 fan-out; no web auto-write | Shared blocks across agents are an identity mix-up primitive; MemFS/CLAUDE.md injection surface |
| Scalability | Linear tokens × 10M MAU is the bill | Horizontal by user namespace; shed to STM; Zep Flex 600 RPM **does not** fit 50 rps without cache | 10M always-on agents × $0.10/agent/mo = **$1M/mo** Letta seat tax before LLM |

**Decision rationale.** **B** is the only option that hits the $ budget (memory layer 5–6× cheaper than generation; full-context 2.5× generation), the retrieve p95 target, and Art. 17 (tagged rows + episode pointers). A fails cost and p95 and puts raw PII in the cached prefix. C is the right *control-plane* for a high-touch agent, not 10M MAU always-on seats; use Letta-style **blocks as the hot profile card**, not one sleep-time agent per MAU. Interview close: “Constructor tokens, not top-k; write async; fail open to STM.”

### Scenario 2 — Enterprise support agent (HIPAA / SOC2, knowledge updates)

**Problem statement.** Multi-tenant clinical/support agent. Tickets span sessions; policies change (`valid_at`). Must cite **ticket IDs** (episodic provenance). HIPAA BAA + SOC 2. Stale policy facts are a safety incident. MCP to CRM; no token passthrough. Ingest is async (Graphiti pipeline); answering from un-watermarked facts is forbidden. A vendor slide says LoCoMo **94.7% @ 155 ms** — procurement wants that number as the chat SLO. Volume: hundreds of concurrent agents, not 10M MAU. Human confirm on DELETE-class knowledge updates.

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Agent      │────▶│ CONTROL PLANE                                             │
│ desktop /  │     │ Gateway: SSO, correlation-id, Zep RPM class, BAA session  │
│ EHR sidebar│     │ Policy: PII-before-embed; tenant from token; tool RBAC    │
│            │     │ Router: ticket thread = STM; customer Store = LTM         │
│            │     │ Orchestrator: watermark gate; PIT query; HITL on DELETE   │
└────────────┘     └────┬─────────────────────────────┬────────────────────────┘
                        │                             │
                        ▼                             ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ DATA PLANE       │        │ TOOL PROXIES                 │
                   │ Graphiti/Zep     │        │ memory_view (ticket facts)   │
                   │ episode + entity │        │ memory_create (sleep-time)   │
                   │ + community      │        │ crm_read via RFC 8693 xchg   │
                   │ f=χ∘ρ∘φ 1.6k tok │        │ NO token passthrough         │
                   │ LangGraph thread │        │ HITL on DELETE-class updates │
                   │  = ticket_id     │        │                              │
                   └────────┬─────────┘        └──────────────┬───────────────┘
                            │                                 │
                            ▼                                 ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE                                               │
                   │ Store ns=(tenant, customer_id); valid_at/invalid_at       │
                   │ 1-year audit logs (Zep Enterprise); WORM hash-chain       │
                   │ Art.17 hard-delete ≠ invalid_at; per-tenant index/BYOC    │
                   └───────────────────────────────────────────────────────────┘
```

**Technology choices.** Graphiti/Zep for **knowledge-update** and multi-session (Zep LME gains; Mem0 v3 LongMemEval knowledge-update **93.6** platform). Episodes cite ticket IDs. Semantic facts carry `valid_at`. LangGraph thread = ticket; Store namespace = `(tenant, customer_id)`. Zep Enterprise SLA/RPM; **1-year** audit logs. Ingest watermark: don’t answer from facts until episode linked. BAA; tenant pre-filter on Cypher+ANN; MCP OAuth to CRM; RFC 8693 token exchange. Confirm Trust Center: SOC 2 Type II is **checked on Enterprise, “—” on Flex/Flex Plus** in the public matrix. Fallback: episodes if facts not ready; STM ticket thread if retrieve breaker open — then **abstain on policy**, do not invent (BEAM 10M abstention **40.0**).

**Trade-off evaluation matrix.**

| Dimension | A. Mem0 v3 ADD-only SaaS + metadata tenant filter; chat SLO = vendor 155 ms | B. Recommended: Zep/Graphiti bi-temporal + LangGraph checkpointer/Store; watermark; Enterprise BAA/BYOC; constructor 1.6k; HITL on DELETE | C. STM checkpointer only + full ticket dump into 115k context |
| --- | --- | --- | --- |
| Cost | Starter/Pro **[inferred] $3.80–$4.98/1k** retrieve; ADD-only correction is extra rows + ranker | Flex $125 / Flex Plus $375 / Enterprise custom; ⚠️ no public $/session; RPM 600/1000 is the NFR | Generate **[inferred] ~$78/1k** at 26k; 115k path is Zep paper’s **28.9 s** / 115k baseline |
| Latency | Platform p50 **0.88–1.09 s** (no p95/p99). Do **not** treat 155 ms retrieve as chat SLO | Vendor retrieve **155–162 ms**; paper e2e **2.58 s** incl. generate + RTT. Watermark can add hours if you couple planes | Full-context p95 **17–29 s** class; compaction amnesia on turn 90 |
| Ops | ADD-only = easier replay, harder correction; OSS ≠ platform scores | Bi-temporal PIT; community refresh as a job; single-writer sleep-time on shared procedure blocks | `delete_thread` is the only forget; no user profile across tickets |
| Security | Post-filter metadata can leak neighbors; Flex SOC 2 **not** checked on public matrix | Pre-filter Cypher+ANN; BAA; BYOC; RFC 8707 resource indicators; 1-year audit; Art. 17 **hard-delete** besides `invalid_at` | PII in every checkpoint; prompt-cache prefix holds PHI |
| Scalability | Four scopes (user/agent/app/run); quota 5k–50k retrievals/mo on listed SKUs | Horizontal by tenant graph; Enterprise RPM custom; do not run community map-reduce on the ticket path | Linear in ticket length; Mongo 16 MB checkpoint cap vs Postgres ~1 GB |

**Decision rationale.** **B** is the only option that simultaneously (1) treats 155 ms as a **retrieve** SLO, (2) keeps episodes as non-lossy citation of ticket IDs, (3) invalidates policy facts temporally **and** offers Art. 17 hard-delete, (4) puts HIPAA/SOC 2 on the Enterprise/BYOC SKU that actually lists them. A is the right *ship-this-week personalization* stack (Scenario 1), not the knowledge-update + PIT + BAA stack. C fails cross-ticket memory, p95, and PHI-in-the-window. Interview close: “Split STM thread from LTM customer; split semantic from episodic; authz before ANN; watermark before facts; 155 ms is retrieve, not chat.”

---

*End of module. Six sections. Five topics (short-term, long-term, semantic, episodic, memory retrieval). Token `$ / 1k` tables are **[inferred]** from the stated reference session and list prices dated 2026-08-21. No unpublished memory e2e p50/p95/p99 SLOs — percentiles are labeled **[inferred]** or cited to Mem0 Table 2 / Zep paper / vendor retrieve-only ms.*
