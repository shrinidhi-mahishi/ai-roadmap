# Research: Memory

**Date researched**: 2026-08-21
**Sources consulted**: 89

Scope: short-term working memory (conversation buffers, windows, token-budgeted scratchpads, compaction), long-term stores (user profiles, vector indexes, Mem0, Zep/Graphiti, Letta/MemGPT, LangGraph Store), semantic memory (facts, entities, KGs), episodic memory (event traces, trajectories, time-indexed episodes), retrieval (salience, recency, importance, write vs read path, consolidation, forgetting). Prices, rate limits, and latency percentiles below are from vendor docs, papers, or named blogs as of 2026-08-21. ⚠️ No unpublished production p50/p95/p99 memory SLOs are invented; missing percentiles are marked. `$ per 1k sessions` figures are **[inferred]** from published SKUs × a stated reference session, not a vendor “per session” product.

---

## 1. System Topology & Mechanics

### 1.1 Two planes: write path vs read path

A production memory system is two independently scaled planes sharing **durable stores**, not a single “remember then recall” function. Coupling them is the dominant latency and correctness failure: a stuck extractor stalls answers, or a query-time LLM rewrite silently mutates the store.

| Plane | Owns | Typical components | Failure if coupled |
| --- | --- | --- | --- |
| **Write (ingest / learning)** | Extract facts, stamp ACL + tenant + `user_id`, entity-resolve, conflict/invalidation, embed, graph insert, consolidate, forget | Session workers, extraction LLMs, graph builders (Graphiti), sleep-time / dreaming agents, checkpoint writers | Query p99 tracks extract; a 600k-token graph rebuild (Mem0 paper’s Zep observation) makes “just-added” memories unsearchable for hours |
| **Read (retrieve / assemble)** | Authz pre-filter, hybrid retrieve, recency/importance score, rerank, budget into the window, generate | ANN + BM25 + graph BFS, RRF, cross-encoder, memory blocks pinned in-context | Write schema change (embedder, ontology) silently mismatches query embeddings |

Invariant (CoALA, Sumers et al., TMLR 2024): the LLM is **not** the memory. Working memory is a data structure the prompt is *compiled from*; long-term memory is read via **retrieval** and written via **learning**. The model never “searches”; it emits a tool call or the control plane runs a retriever; observations return as tokens.

**Control plane vs data plane.** The control plane decides *whether* to write, *which* store, *which* k, and *whether* to compact. The data plane is Postgres/Neo4j/vector indexes, object storage for Anthropic `/memories` files, and LangGraph checkpoints. Letta’s ADE and Zep’s Context Lake are control-plane UIs over data-plane graphs. MCP memory servers sit on the **tool boundary**: they are data-plane with control-plane auth.

### 1.2 Memory taxonomy (what actually ships)

CoALA’s four stores map onto products with almost no renaming:

| CoALA type | What it holds | Production analogue | Hotness |
| --- | --- | --- | --- |
| **Working** | Active symbols for this decision cycle — not identical to the LLM context string | Message buffer, LangGraph thread state, Letta in-context messages, token-budgeted scratchpad | **Hot** (every token billed) |
| **Episodic** | Time-stamped events / trajectories (“what happened”) | Graphiti episodes, conversation logs, LangGraph checkpoints, Letta recall/conversation search | **Warm** (searchable, not pinned) |
| **Semantic** | Facts, entities, user/world knowledge (“what is true”) | Mem0 memories, Graphiti entity/fact edges, Letta core blocks + archival, user profiles | **Warm/cold** (blocks hot; archival cold) |
| **Procedural** | How to act (prompts, skills, code, weights) | System prompt, tools, Letta skills, Claude Skills, CLAUDE.md | **Hot** if in prompt; **cold** if tool-fetched |

Park et al. *Generative Agents* (UIST 2023) is the other canonical split: a **memory stream** of NL observations, plus **reflection** (higher-level inferences written back into the stream) and **planning**. Reflections are semantic summaries *with pointers* to episodic evidence — the same episode→fact→community hierarchy Zep later productized.

### 1.3 Short-term / working memory: buffers, windows, token budgets

**Conversation buffer (full history).** LangChain classic `ConversationBufferMemory` stores every turn unfiltered (`memory_key="history"`). Linear growth; zero information loss until the window overflows. Deprecated since **0.3.1**; replacement is `create_agent` + a **checkpointer**. Still the right *mental model*: STM is the thread’s messages.

**Window (`k` turns).** `ConversationBufferWindowMemory` keeps the last `k` human/AI pairs (FIFO). Constant RAM; drops early constraints (“I’m vegetarian” on turn 2 of a 200-turn support chat). Use when recency *is* the task (IVR, short tickets). Do not use as the only memory for identity or policy.

**Token-budgeted buffer.** `ConversationTokenBufferMemory` drops oldest messages until `max_token_limit`. Strictly better than `k` turns when message size varies (tool dumps vs “ok”). LangChain Core `trim_messages(..., strategy="last", max_tokens=N, include_system=True, start_on="human")` is the 2025–26 primitive: keep system + a legal human/AI suffix under a token budget. Approximate counting (`'approximate'`) is recommended on the hot path; `model.get_num_tokens_from_messages` for billing-accurate trim.

**Summarization as STM compression.** LangMem `summarize_messages` / `SummarizationNode`: when cumulative tokens ≥ `max_tokens_before_summary`, replace the prefix with `[summary_message] + remaining_messages`, carrying `running_summary` so you do not re-summarize the same prefix every turn. LangChain `SummarizationMiddleware` triggers on `("tokens", N)`, `("messages", N)`, `("fraction", 0.8)`, or AND/OR combinations; default keep is last **20** messages. Compaction is **lossy**; it invalidates prompt-cache prefixes (Anthropic documents this for `compact_20260112`).

**Anthropic three-layer STM (Messages API, 2025–26).** These are *not* interchangeable:

| Primitive | ID | Default trigger | What it does | Cache / loss |
| --- | --- | --- | --- | --- |
| Tool-result clearing | `clear_tool_uses_20250919` | 100k input tokens; keep last 3 tool uses | Mechanical delete of stale tool exhaust; placeholder remains | Cheapest; lossless for refetchable results |
| Compaction | `compact_20260112` (beta `compact-2026-01-12`) | 150k input tokens; **min 50k** | Server summarizes prefix into a `compaction` block; later requests drop everything before it | Lossy; **invalidates cached prefix** |
| Memory tool | `memory_20250818` (GA, Claude 4+) | Model-initiated | Client executes `view/create/str_replace/insert/delete/rename` under `/memories` | Survives compaction; storage is **your** infra |

Anthropic’s internal agentic-search eval (Sonnet 4.5 launch, 2025): context editing alone **+29%** task performance and **−84%** tokens vs baseline; editing + memory tool **+39%**. The 84% is tool-exhaust deletion, not summarization. Compaction `instructions` fully replace the default summarizer — use them to pin IDs, decisions, and open TODOs.

**LangGraph STM = checkpointer.** `PostgresSaver` / `RedisSaver` snapshot **graph state per `thread_id`**. That is conversation continuity, HITL interrupts, time-travel, and crash recovery — not user profiles. Redis: `defaultTTL` in **minutes**, `refreshOnRead`, plus `ShallowRedisSaver` (latest checkpoint only). Postgres: LangChain team-maintained; no native TTL in OSS (cron/`delete_thread`); Agent Server / `langgraph.json` TTL is the managed path. MongoDB checkpointers: **16 MB** document cap vs Postgres **~1 GB**/field.

**Letta STM.** In-context message buffer + pinned **memory blocks**. `max_message_buffer_length` is best-effort (user/assistant interleaving can overshoot). `message_buffer_autoclear=true` forgets previous messages while retaining core blocks + archival/recall — advanced only. Recall memory = searchable full conversation history (episodic); not pinned.

### 1.4 Long-term memory products (2025–26)

**Letta (formerly MemGPT).** Packer et al. 2023: virtual context management — main context (RAM) vs external context (disk), with the agent issuing OS-like page-in/page-out tool calls. Letta 2025–26 productizes this as:

| Tier | Mechanism | Limits (docs) |
| --- | --- | --- |
| **Core / blocks** | Labeled, always-in-context strings; agent tools `memory_insert` / `memory_replace` / `memory_rethink`; shareable across agents | Rec. **<50k characters/block**, **<20 blocks/agent** |
| **Files** | Open/close + grep + semantic search | **5 MB**/file, rec. **<100 files** |
| **Archival** | `archival_memory_insert` / `_search`; agent-curated facts; REST `/v1/agents/{id}/archival-memory` with tag + `start_datetime`/`end_datetime` | **~300 tokens**/passage; unlimited count |
| **External RAG / MCP** | Custom tools | Unlimited |

Sleep-time agents (Letta 0.7+, paper arXiv:2504.13171): a background agent **owns write tools** for core blocks; the primary agent talks and searches recall/archival. Default `sleeptime_agent_frequency=5` primary steps. Shared blocks are **last-write-wins** — concurrent primary + sleep-time edits lose data unless serialized. Letta Code 2026 adds **MemFS** (git-backed memory filesystem) and **dreaming** (background subagents; optional second-pass review). Constellation naming retired after Letta Code 0.28.13; cloud-hosted state is **Letta Cloud**.

**Mem0.** Two eras — do not mix scores.

*Paper (arXiv:2504.19413, LOCOMO ~26k tok/conversation, GPT-4o-mini extract):* pair-wise extract `(m_{t-1}, m_t)` with conversation summary `S` + last `m=10` messages → candidate facts → retrieve top `s=10` similar memories → LLM tool-call ADD/UPDATE/DELETE/NOOP. Mem0g: Neo4j directed labeled graph, entity+relation extract, mark obsolete edges invalid (not physical delete). Retrieval tokens on LOCOMO: Mem0 **1,764**; Mem0g **3,616**; Zep **3,911**; OpenAI playground memories **4,437**; full-context **26,031**. Construction footprint: Mem0 ~**7k** tok/conversation, Mem0g ~**14k**, Zep **>600k** (node summaries + edge facts; Mem0 authors). Immediate-after-write retrieval on Zep often failed in that harness; hours later improved — Graphiti’s async LLM pipeline.

*Platform v3 (rolled out 2026, docs + GitHub README, **managed** numbers):* single-pass **ADD-only** (no UPDATE/DELETE at extract), agent-generated facts first-class, native entity graph (no external Neo4j), hybrid **semantic + BM25 + entity boost**, temporal ranking. GitHub table: LoCoMo **92.5** (was 71.4) @ **7.0k** tok, p50 **0.88 s**; LongMemEval **94.4** (was 67.8) @ **6.8k**, p50 **1.09 s**; BEAM 1M **64.1** @ **6.7k**, p50 **1.00 s**; BEAM 10M **48.6** @ **6.9k**, p50 **1.05 s**. Docs: LoCoMo overall 92.5 with single-hop 91.2 / multi-hop 91.3 / open-domain **72.7** / temporal 92.0; LongMemEval overall 94.4 with assistant recall **98.2**, multi-session **88.0**. ⚠️ Scores are the **managed platform**; OSS SDK is “directionally similar, not identical.” ±1 judge CI stated.

**Zep + Graphiti.** Three-tier temporal KG (arXiv:2501.13956):

1. **Episode subgraph** — raw messages/text/JSON + `t_ref`; non-lossy; speaker auto-extracted as entity; last **n=4** messages (2 turns) as NER context.
2. **Semantic entity subgraph** — entities (1024-d name embeddings + full-text) and fact edges; hybrid cosine + BM25; Cypher writes (not LLM-generated queries).
3. **Community subgraph** — label propagation (not Leiden) so new nodes join without full recompute; map-reduce summaries; periodic refresh still required.

**Bi-temporal model** (Graphiti docs): *valid time* (`valid_at` / `invalid_at`) vs *transaction time* (`created` / `expired`). Contradiction → set old edge `invalid_at` ← new `valid_at`, `expired_at` ← now; **do not delete**. Point-in-time queries are first-class; GraphRAG/vector DBs are not. Retrieval: `f = χ ∘ ρ ∘ φ` — search (cosine + BM25 + BFS) → rerank (RRF, MMR, episode-mention frequency, node-distance from centroid, cross-encoder) → constructor (facts with date ranges + entity summaries + community summaries). BFS can seed from recent episodes so “just talked about X” stays in context.

Paper LongMemEval_S (~**115k** tok, Dec 2024–Jan 2025, laptop→AWS us-west-2): gpt-4o **71.2%** vs full-context **60.2%** (+**18.5** pp), latency **2.58 s** (IQR 0.684) vs **28.9 s**, context **1.6k** vs **115k**. gpt-4o-mini **63.8%** / **3.20 s** vs **55.4%** / **31.3 s**. DMR: Zep **94.8%** vs MemGPT **93.4%** (gpt-4-turbo); authors note DMR is too easy (full-context 94.4%). Vendor site 2026: LoCoMo **94.7% @ 155 ms**, LongMemEval **90.2% @ 162 ms**, “sub-200 ms regardless of graph size.” ⚠️ Paper e2e latency ≠ retrieval-only vendor ms; different judges/models/years.

**LangGraph Store.** Cross-thread JSON `(namespace, key) → item`. Namespaces typically `("memories", user_id)` or `(org, agent, user)`. `PostgresStore` optional pgvector `index=` (semantic search **off** until configured); `ttl=` requires `start_ttl_sweeper()`. Compile `checkpointer=` **and** `store=`. Agent Server manages both; custom `BaseStore` allowed, custom checkpointer **not** on managed platform.

**OpenAI ChatGPT memory (product, not API).** Timeline: **Apr 2024** saved memories (explicit + inferred notes, always in context, user-editable); **10 Apr 2025** “reference chat history” + Dreaming V0 (background synthesis; EU/UK delayed); **3 Jun 2025** Free users get lightweight short-term continuity, Plus/Pro longer-term; **4 Jun 2026** Dreaming V3 (reviewable memory summary; ~**5×** cheaper dreaming compute enabling Free rollout). Temporary Chat uses/updates neither. Developer community threads confirm **no Memory API** on Chat Completions/Responses; Conversations API is intra-conversation state. Do not design enterprise agents assuming ChatGPT memory is callable.

**Cognee 1.0 (2026).** Four verbs: `.remember` (ingest→graph or session cache), `.recall` (auto-route hybrid: graph traversal + vectors + BM25, RRF), `.improve` (bridge session→permanent graph, feedback weights), `.forget` (item/dataset/user). Session memory is the fast STM path; permanent memory is the KG. MCP + LangGraph integrations first-party.

### 1.5 Semantic vs episodic (do not collapse them)

**Semantic memory** answers “what is true of this user/world *now* (and historically)?”: vegetarian, works at Acme, policy P-12 requires dual control. Stores: Mem0 memories, Graphiti facts with `valid_at`/`invalid_at`, Letta `human`/`persona` blocks, profile JSON in LangGraph Store. Updates are **upserts with conflict policy** (ADD-only vs UPDATE/DELETE vs temporal invalidation).

**Episodic memory** answers “what happened, in what order, with what evidence?”: Graphiti episodes, Letta conversation search, LangGraph checkpoint history, raw traces in LangSmith/Langfuse. Episodes are the **non-lossy provenance** for semantic edges. Destroying episodes while keeping facts breaks citation, unlearning, and audit.

**Trajectories.** Agent run traces (tool calls, observations, rewards) are episodic. Reflexion/A-Mem write *lessons* back as semantic or procedural memory. A-Mem (Xu et al., NeurIPS 2025): Zettelkasten notes (keywords, tags, contextual description) + LLM link generation + **evolution** of neighbor notes on insert; retrieve top-k plus linked neighbors. LoCoMo table in their paper notes: A-Mem avg F1 rank 1.6 at **1,216** tokens vs MemGPT 16,987 — denser notes, still no first-class forgetting in the original design.

### 1.6 Retrieval: salience, recency, importance, fusion

**Generative Agents retrieval (the scoring template still copied in 2026):**

\[
\mathrm{score} = \alpha_r\,\mathrm{recency} + \alpha_i\,\mathrm{importance} + \alpha_v\,\mathrm{relevance}
\]

All α = **1** in the paper after min-max to [0,1]. Recency = exponential decay **0.995** per sandbox hour since last *access* (not creation). Importance = LLM integer 1–10 (“brushing teeth” vs “breakup”). Relevance = cosine(query embedding, memory embedding). Top memories that fit the window go into the prompt.

Production mappings:

| Signal | Who uses it | Mechanism |
| --- | --- | --- |
| Relevance | Everyone | Dense cosine; Mem0 v3 + BM25 + entity match fused into one `score` |
| Recency | Graphiti `valid_at`; Qdrant decay formulas; Mem0 temporal ranker; Generative Agents decay | Soft rank vs hard `status=current` filter |
| Importance / salience | Generative Agents LLM score; Letta agent decides insert; Mem0 extract LLM; Graphiti mention-frequency reranker | Write-time vs read-time |
| Graph distance | Graphiti node-distance reranker; Cognee traversal | Localize to a user/org subgraph |
| RRF | Graphiti, Cognee, hybrid vector stacks | Fuse lexical + dense + graph lists, k=60 typical |

**Write path vs read path (salience is not symmetric).** Write-time filters (consistency checks, origin tags) kill L1 poisoning but miss L2/L3 compositional attacks (MemPoison 2026). Read-time must re-score in the *current* query context. SMSR: HMAC provenance at write **plus** randomized ablation at read.

**Consolidation.** Sleep-time / dreaming / Mem0 “Dream” (Pro SKU) / Cognee `.improve` / Graphiti community refresh / A-Mem evolution. All are **batch LLM jobs** on the write plane. OpenAI: saved memories go stale; dreaming exists *because* write-time notes rot. Letta sleep-time paper: ~**5×** less test-time compute for same accuracy on Stateful GSM-Symbolic/AIME; scaling sleep-time **+13%** / **+18%** accuracy; **2.5×** lower average cost when **10** queries share one precomputed `c'`.

**Forgetting.** MemoryBank (Zhong et al., AAAI 2024): Ebbinghaus-style strength — recall reinforces, idle decays. Graphiti: invalidate, don’t delete (history preserved). Mem0 v3: ADD-only accumulation (forgetting is a **separate** product/policy problem). LangGraph Store TTL + Redis checkpoint TTL. GDPR erasure is *not* Ebbinghaus — see §4.

### 1.7 Hot / warm / cold tiers

| Tier | Latency target | Contents | Typical backing |
| --- | --- | --- | --- |
| **Hot** | p50 <50–200 ms of *assembly*, plus model TTFT | System prompt, core blocks, last-N messages, user profile card | In-process / Redis / pinned tokens |
| **Warm** | p50 100–400 ms search (Mem0 paper 148 ms; Zep vendor 155–162 ms retrieve) | Semantic facts, recent episodes, entity subgraph | pgvector, Neo4j, Mem0/Zep APIs |
| **Cold** | seconds–hours | Full traces, old episodes, community rebuilds, object-store memory files | Object storage, warehouse, Graphiti community refresh |

Letta’s hierarchy is explicit: if it must be true every turn, it is a **block** (hot); if it is a fact the agent might need, **archival** (warm); if it is a corpus, **files/RAG** (cold). Anthropic memory files are cold until `view` pages them hot.

---

## 2. Token Economics & NFR Metrics

### 2.1 Published latency (do not flatten into one SLA)

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

**Zep Flex rate limits (pricing page):** 600 RPM (Flex) / 1,000 RPM (Flex Plus); Enterprise “guaranteed, custom.” API log retention **1 day / 7 days / 1 year**.

**Anthropic.** ⚠️ No public p50 for compaction or memory-tool round-trips. Compaction adds one summarization forward pass at the trigger. Memory tool is extra tool-loop RTTs (`view` directory, then `view` files).

**p99:** ⚠️ **unpublished** for Mem0, Zep, Letta, LangGraph Store in the sources consulted. Budget p99 ≈ 2–4× p95 **[inferred from typical ANN+LLM tails, not a vendor number]** and measure yourself.

### 2.2 `$ per 1k sessions` **[inferred]**

**Reference session (stated, not a SKU):** 1 user-facing turn that (a) **retrieves** once, (b) **adds** 2 memories (user+assistant pair, Mem0 paper), (c) injects ~2k–7k memory tokens into a mid-size generator. Consolidation **not** included unless noted.

**Mem0 hosted (pricing page 2026-08-21):** Hobby $0 / 10k adds + 1k retrievals; Starter **$19**/mo / 50k adds + **5k** retrievals; Pro **$249**/mo / 500k adds + **50k** retrievals (graph + Dream consolidation); Enterprise custom; usage-based available.

| Plan | Retrieval quota | **[inferred]** retrieval $ / 1k sessions | Notes |
| --- | --- | --- | --- |
| Starter | 5,000 / $19 | **$3.80** | 1k sessions = 20% of retrieval quota; 2k adds ≪ 50k |
| Pro | 50,000 / $249 | **$4.98** | Retrieval-bound; Dream consolidation is **extra LLM** not in this SKU math |

Generator tokens at ~7k in / 0.5k out on a $3/M input / $15/M output class model: **[inferred]** ~$0.021 + $0.0075 ≈ **$0.03** per session → **~$30 / 1k sessions** for the *chat* model, i.e. **memory-layer SKU is 5–6× cheaper than generation** on this reference. Full-context 26k in: **[inferred]** ~$0.078/session generation (**~$78 / 1k**), matching the paper’s “>90% token cost” claim directionally.

**Zep (2026 pricing):** Flex **$125**/mo / 50k credits, overage **$25 / 10k** credits; Flex Plus **$375** / 200k credits, **$75 / 40k**; Enterprise custom. ⚠️ **Credit-per-add and credit-per-retrieve are not fully specified on the public pricing table** — do not convert to $/session without a contract quote. RPM 600/1000 is the published NFR.

**Letta API plan (docs.letta.com/letta-agent/pricing):** **$20**/mo + **$0.10 / active agent / month** + **$0.00015 / s** server-side tool execution + pay-as-you-go LLM. Remote MCP tools billed by the MCP provider, not Letta credits. **[inferred]** 1k sessions on **one** always-on agent: platform fee ≈ $20/30d + $0.10 ≈ **$20.10/mo**, i.e. **~$0.02 / 1k sessions** of *Letta SKU* if that agent’s 1k sessions fit in the month — **memory LLM tokens dominate**. Sleep-time on a frontier model every 5 steps can exceed chat-model spend; the paper’s 2.5× amortization assumes many queries share `c'`.

**LangGraph.** Checkpointer/Store = **your** Postgres/Redis bill + embedding calls if `index=` set. No per-memory SKU.

**Cognee Cloud.** Usage-based; ⚠️ no public per-recall unit price in sources consulted.

### 2.3 Cache and consolidation batch cost

- **Prompt cache:** STM prefix (system + core blocks + stable profile) should be the cached prefix. Compaction and block rewrites **break** the cache. Sleep-time that rewrites core memory mid-session trades personalization for cache hit rate.
- **Retrieval cache:** `(user_id, query_hash, index_version) → hits` with short TTL; invalidate on write. ⚠️ Hit rates unpublished.
- **Consolidation batch:** Graphiti community refresh, Mem0 Dream, Letta sleep-time, OpenAI dreaming, Cognee `.improve`. Cost is LLM-bound and **offline**. Sleep-time paper: amortize across queries; 10 queries/context → **2.5×** lower average cost vs single-query test-time scaling. OpenAI: Dreaming V3 ~**5×** less compute than prior dreaming (vendor; no $). Mem0 paper: Zep graph construction not real-time (hours); Mem0g “under a minute even in worst-case” in their harness.

### 2.4 Token budgets that actually bind

| Budget | Who | Binding constraint |
| --- | --- | --- |
| Letta block <50k chars | Hot path | ~12.5k tok **[inferred]** at 4 chars/tok; 20 blocks → theoretical 250k chars — you will hit the **model window** first |
| Archival passage ~300 tok | Warm | k=10 archival hits ≈ 3k tok before the generator |
| Anthropic compaction trigger 150k (min 50k) | STM | Plus tool-clear at 100k |
| Mem0 v3 ~7k tok/query | Read | vs 25k+ full-context on their research page |
| Zep constructor 1.6k tok (paper LME) / 20 edges+nodes | Read | Precision via rerank, not dumping the graph |
| ChatGPT saved-memory list | Product | Historically small; 2025 chat-history path is **not** a visible list (Ars Technica); Dreaming V3 adds a **summary** UI (2026) |

---

## 3. Distributed Resilience & State

### 3.1 Durable stores and checkpoints

| System | Durability primitive | Isolation key | Resume / time-travel |
| --- | --- | --- | --- |
| LangGraph checkpointer | Postgres WAL / Redis AOF+repl | `thread_id` (+ optional `checkpoint_id`) | Replay from checkpoint; `delete_thread()` |
| LangGraph Store | Postgres rows / RedisJSON | `namespace` tuple | TTL sweeper; no built-in CRDT |
| Letta | DB-backed agent state (messages, blocks, passages) | `agent_id` | Messages survive compaction; ADE inspect |
| Graphiti/Zep | Graph DB + indexes (Neo4j in OSS paper; Zep proprietary Context Graph Engine in cloud 2026) | user/session graph | Point-in-time via `valid_at` |
| Anthropic memory tool | **Your** FS/DB | Your mapping of `/memories` | Versioning is your job; beta Memory Stores API has `memory_version_id` + SHA-256 |
| Mem0 platform | Managed vector+graph | `user_id` / agent / run (docs: four scopes) | ADD-only v3 = no in-place mutate; easier replay, harder correction |

**Conflict resolution.** Letta shared blocks: **last write wins**. Graphiti: LLM contradiction check, temporal invalidation, new information prioritized on transaction timeline `T'`. Mem0 paper: LLM chooses ADD/UPDATE/DELETE/NOOP against top-s neighbors. Mem0 v3: **never overwrite** — conflicts become extra rows; read-time temporal ranker must pick “current.” LangGraph Store: `put` replaces key; no merge function in BaseStore. Design **per-user single-writer** or optimistic version checks (Anthropic Memory Stores: `content_sha256` precondition).

**PostgresSaver concurrency.** Forum guidance: a **single saver + pool + compiled graph per process**; the Python saver’s `_cursor` lock serializes ops on one instance. Horizontal scale = more workers, not more savers per event loop. RedisSaver does not use that lock. Call `setup()` once.

### 3.2 Circuit breakers and degraded modes

Memory is on the **critical path** of personalization but should **not** be on the critical path of “can the agent answer at all.”

| Dependency | Breaker | Degraded mode |
| --- | --- | --- |
| Memory retrieve (Mem0/Zep/Store) | Timeout ~200–500 ms on search **[inferred SLO, not vendor]**; fail open | Serve STM only + cached profile card; log `memory_miss` |
| Memory write | Queue; do not block the user turn (Letta sleep-time pattern) | Ack user; extract async; idempotent add |
| Embedder | Same as RAG: stale embedder version = silent recall collapse | Pin `embedding_model` + `index_version` in metadata |
| Graph construction | Backpressure; Zep webhooks when ingest completes | Read episodes (non-lossy) if facts not ready |
| Sleep-time / dreaming | Max parallel jobs per tenant | Skip consolidation; accept messier blocks |

Anthropic: `pause_after_compaction` exists so you can inspect summaries before continuing — a **human circuit breaker** for lossy STM.

### 3.3 Unbounded growth

Without policy, episodic stores grow linearly with turns; semantic stores grow with extract recall; graphs grow with entities×facts. Controls that exist in products: Redis/Store TTL; Letta archival unlimited **by design** (must add your own GC); Graphiti invalidation without delete (history **grows**); Mem0 v3 ADD-only (same); shallow checkpointers; conversation-search vs archival split. **Capacity NFR is a product decision**, not a library default.

---

## 4. Enterprise Security & Governance

### 4.1 Zero-Trust MCP around memory

MCP is **transport, not governance** (Cloudflare; repeated in 2026 production guides). Memory MCP servers (Mem0 MCP, Zep Memory MCP seats, Cognee MCP, custom archival tools) must implement the 2025–26 authorization stack:

- **OAuth 2.1 + PKCE (S256)** for remote servers; implicit flow gone.
- **RFC 9728** Protected Resource Metadata + **RFC 8414** AS metadata.
- **RFC 8707 Resource Indicators** so a token minted for server A cannot be replayed on server B.
- **No token passthrough** (confused deputy); **RFC 8693** token exchange for downstream APIs.
- `tenant_id` / `user_id` **only from the verified token**, never from tool arguments (Asana-class leak pattern).

Claude Code: first-time folder + new MCP servers require trust verification; **disabled under `-p`**. Anthropic does **not** security-audit third-party MCP servers in the directory. Check Point 2026: CVE-2025-59536 / **CVE-2026-21852** — malicious repo `.mcp.json` / `ANTHROPIC_BASE_URL` ran or exfiltrated **before** trust prompt (fixed ≥ 2.0.65). GitHub issue #21674: `~/.claude/CLAUDE.md` is a **global persistent injection** surface (any user-level process can rewrite it). Anthropic engineering (2026) names **persistent memory poisoning** (product memory, CLAUDE.md, workspaces, scheduled-agent state) as a session-startup classifier problem.

### 4.2 Tenant isolation

| Layer | Correct control | Failure |
| --- | --- | --- |
| Vector/graph query | **Pre-filter** `tenant_id`/`user_id` on every ANN, BM25, and Cypher path | Post-filter after top-k leaks neighbors |
| Namespaces | LangGraph `("t", tenant, "u", user)`; Mem0 user/agent/app/run scopes; Letta per-agent (shared blocks are explicit) | Shared block attached to two customers’ agents |
| Embeddings | Tag `{user_id, session_id, category, ingested_at}` at **write** | Article 17 cannot find vectors |
| MCP | Token-bound tenant registry; tool allow-list at auth time | Tenant in JSON args |
| Physical | Enterprise: per-tenant index / VPC / BYOC (Zep Enterprise: Cloud, BYOK, BYOC) | Shared HNSW + metadata hope |

Zep: SOC 2 Type II on Flex **table shows “—” for Flex/Flex Plus, checked on Enterprise** in the public matrix — **confirm Trust Center** (trust.getzep.com) for which SKUs inherit the cert. HIPAA BAA listed on Enterprise. Mem0: GDPR-ready claim + trust.mem0.ai. Letta Enterprise: SAML/OIDC, RBAC.

### 4.3 PII in memories

Memories **are** PII when they encode identity, health, location, credentials, or behavioral profiles. Controls:

- **Minimize at extract:** do not write raw message logs into semantic memory; write facts; keep episodes access-controlled.
- **Redact on write** (SSN, PAN) before embed — embeddings invert to approximate text in published attacks; treat vectors as **confidential as source**.
- **Purpose limitation:** ChatGPT-style “chat history memory” that cannot be inspected (2025 Ars) is a governance defect for enterprise — require **exportable, editable** semantic stores (OpenAI 2026 memory summary is the product correction).
- **Separation:** credentials and secrets belong in a vault, **never** in archival memory or CLAUDE.md.

### 4.4 Right to be forgotten (GDPR Art. 17 + Art. 12(3))

Clock: without undue delay, **max one month** (extendable +2 months if you notify). Erasure is a **fan-out**, not `DELETE FROM memories`:

1. Semantic rows / graph nodes+edges tagged by `user_id`.
2. Episodes / checkpoints / Store keys / `/memories` files.
3. Vector IDs (HNSW **soft-delete** until compaction/VACUUM — EDPB-aligned commentary in 2026: query suppression ≠ erasure).
4. Prompt/response caches.
5. Trace vendors (LangSmith/Langfuse: API delete, physical purge delayed).
6. Backups — crypto-shred **per-user keys** or wait backup TTL inside the month.
7. Fine-tuned weights: **unlearning unsolved**; architectural control = **do not train on raw personal memory**.

Mem0 v3 ADD-only makes “update in place” easier to audit but **deletion is a separate pipeline** you must still build. Graphiti invalidation preserves history — **Art. 17 requires a hard-delete path**, not only `invalid_at`. Provenance maps (episode ↔ fact) are what make fan-out possible.

### 4.5 Audit

Need: who wrote, who read, which query, which k, which memories entered the prompt, model/index versions. Zep Enterprise: audit + API logs **1 year**. Mem0 platform: audit logs “by default” (overview). LangGraph: checkpoint metadata + LangSmith. Anthropic Memory Stores: `content_sha256`, `memory_version_id` for tamper-evident heads. TMA-NM (2026): **origin-bound, non-malleable authority** — content- and lineage-only defenses fail under summarization/tool-echo laundering (up to **68%** ASR in that paper); write-time origin binding + Sybil-resistant corroboration → **0%** ASR in their harness.

---

## 5. Production Failure Modes

| Failure | Mechanism | Blast radius | Mitigations that exist |
| --- | --- | --- | --- |
| **Memory poisoning** | User, retrieved doc, or webpage causes a **write** of a false belief; later **read** steers tools (payments, exfil) | Cross-session, cross-site (eTAMP) | Origin tags + HMAC (SMSR: unsigned ASR 93–100% → **0%**); TMA-NM 0%; never treat retrieved text as write-authorized; separate “observation” vs “belief” stores |
| **Sleeper / L3 dormant** | Benign-looking record; trigger in a future query (MemPoison L3; Hidden in Memory 2026) | Delayed; write-time filters miss | Read-time context-sensitive scoring; ablation (SMSR Component 2: authenticated ASR **8.0%** in 20-seed store, 95% CI [5.8, 10.9], n=450) |
| **Environment-injected trajectory poison (eTAMP)** | One malicious page; no direct memory API access | Cross-site; ASR up to **32.5%** GPT-5-mini, **23.4%** GPT-5.2, **19.5%** GPT-OSS-120B on VisualWebArena; **×8** under UI frustration | Don’t auto-promote web observations to semantic memory; human confirm for preference writes |
| **Stale facts** | Saved memories without temporal invalidation (“training for a marathon” + “sprained ankle”) | Wrong personalization; OpenAI’s stated reason for dreaming | Bi-temporal edges; recency×validity in ranker; dreaming/sleep-time; user-visible profile |
| **Over-retrieval** | k too large / no rerank / no token budget | Lost-in-the-middle; cost; prompt injection volume | Constructor budgets (Zep 1.6k; Mem0 ~7k); MMR; hard token cap after fusion |
| **Identity mix-up** | Shared thread_id, shared Letta block, missing `user_id` filter, two speakers in one Mem0 store | Cross-customer disclosure | Namespace discipline; Mem0 paper prompt even warns not to confuse character names with users |
| **Unbounded growth** | ADD-only + no TTL + full checkpoint history | Cost, p99, stale neighborhood in HNSW | TTL, shallow checkpoints, archival vs core split, GC jobs |
| **Write/read race** | Query before graph construction finishes (Mem0’s Zep observation) | Empty or wrong recall | Ingest watermark; fallback to episodes |
| **Last-write-wins clobber** | Sleep-time + primary both edit a block | Lost preferences | Single writer (sleep-time owns core memory in Letta 0.7 design) |
| **Compaction amnesia** | Summary drops the constraint you need on turn 90 | Silent quality drop | Memory tool / blocks **before** compact; custom `instructions`; `pause_after_compaction` |
| **Soft-delete “erasure”** | HNSW flag, trace TTL, backup | Compliance finding | Compaction/VACUUM + crypto-shred + provenance map |
| **Procedural injection** | CLAUDE.md / skills / MCP config as durable “memory” | Persistent RCE / prompt injection | Trust prompts, ignore repo MCP until approval, lock down `~/.claude` |
| **Judge-metric overfitting** | LoCoMo/LongMemEval as procurement truth | Buying the wrong system (DMR almost saturates) | Use LongMemEval_M (~1.5M tok) / BEAM 10M; hold-out customer traces |

MemPoison (arXiv:2607.14651): 1,227 cases, L1/L2/L3, three injection channels, three substrates; write-time consistency checks suppress L1, not L2/L3. Hidden in Memory: poisoned writes up to **99.8%** GPT-5.5 / **95%** Kimi-K2.6; among retrievals, attacker-intended **actions 60–89%**. Capability ≠ safety (eTAMP: GPT-5.2 more capable, still vulnerable).

---

## 6. Enterprise System Design Scenarios

### 6.1 Trade-off matrix (choose two planes, not a logo)

| Requirement | Prefer | Avoid | Why |
| --- | --- | --- | --- |
| Sub-200 ms **retrieve**, temporal facts, CRM+chat | Zep/Graphiti | Full-context; STM-only | Bi-temporal + constructor; vendor retrieve 155–162 ms |
| Ship memory this week, conversational personalization | Mem0 platform | Rolling your own extract+Neo4j | v3 hybrid + SKU; OSS if you accept score gap |
| Agent must **self-edit** always-on persona + ADE debug | Letta blocks + sleep-time | Vector-only RAG as “memory” | Memory = context engineering; sleep-time owns writes |
| Multi-tenant LangGraph app, HITL, time-travel | Checkpointer **+** Store | `ConversationBufferMemory` | Deprecated; thread vs user scopes differ |
| Cross-session files Claude already understands | `memory_20250818` + your encrypted bucket | Putting PII in the window | Survives compaction; ZDR/path traversal are your job |
| Point-in-time + audit of “what did we believe on date D?” | Graphiti invalidation + episode provenance | Physical DELETE of facts | Need both PIT **and** Art. 17 hard-delete |
| Lowest **generator** $ at 26k-turn histories | Extractive memory (~2–7k tok) | Stuffing 26k–115k | Paper: 91% p95 cut vs full-context |
| Strict erasure + no residual HNSW | Per-user crypto keys or per-user indexes | Shared index + metadata filter only | Soft-delete is not Art. 17 |
| Poison-resistant writes | Origin-bound IFC (TMA-NM class) + no auto-write from tools/web | LLM “is this a good memory?” as sole gate | Laundering via summary/tool echo |

### 6.2 Scenario A — B2C copilot (10M MAU, $ budget, PII)

**Design.** Hot: 2–4 k tok profile card (Letta-style blocks or Mem0 pinned top memories) + last 8k tok window (`trim_messages`). Warm: Mem0/Zep retrieve k≤20, constructor ≤4k tok. Write: **async** extract; do not block TTFT. Cold: episodes 30–90 d TTL; traces 7–30 d.

**NFR.** Retrieve p95 <300 ms **[target, measure]**; never wait on consolidation. **[inferred]** memory SKU ~$4–5 / 1k sessions (Mem0 Starter/Pro math) vs ~$30 generation.

**Security.** Per-user encryption keys; Art. 17 fan-out tested quarterly; no training on memories.

**Failure.** Over-retrieval of stale preferences → dreaming/sleep-time daily, not per turn.

### 6.3 Scenario B — Enterprise support agent (HIPAA/SOC2, knowledge updates)

**Design.** Graphiti/Zep for **knowledge-update** and multi-session (Zep LME gains; Mem0 v3 LongMemEval knowledge-update **93.6** platform). Episodes cite ticket IDs. Semantic facts carry `valid_at`. LangGraph thread = ticket; Store namespace = `(tenant, customer_id)`.

**NFR.** Zep Enterprise SLA/RPM; 1-year audit logs. Ingest watermark: don’t answer from facts until episode linked.

**Security.** BAA; tenant pre-filter on Cypher+ANN; MCP OAuth to CRM; no token passthrough.

**Failure.** Stale policy facts — temporal invalidation + human confirm on DELETE-class updates.

### 6.4 Scenario C — Long-running coding / ops agent

**Design.** Anthropic stack: clear tool exhaust (84% token win) → compact at 150k with `instructions` that preserve decisions → memory tool for durable lessons. Optional Letta MemFS / Cognee for repo-level semantic graph. Sleep-time overnight over the repo (`c → c'`): ~5× less test-time reasoning on stateful math; analogous for “what broke last time.”

**NFR.** Compaction cache-bust is expected; schedule compact at session boundaries when possible.

**Security.** Treat CLAUDE.md and MCP as **procedural memory**: trust UI, pin versions, startup classifiers (Anthropic 2026). Never store secrets in `/memories`.

**Failure.** Persistent injection via memory files — path traversal deny; per-repo stores not `~/.claude`.

### 6.5 Scenario D — Multi-agent org (shared team memory + private user memory)

**Design.** Letta **shared blocks** for team procedures (read-only to workers). Per-user semantic store (Mem0 user scope or Store namespace). Sleep-time **single writer** on shared blocks.

**Failure.** Identity mix-up and last-write-wins. Mitigate: no shared *user* blocks; CRDT or version preconditions if multiple consolidators.

### 6.6 Scenario E — Regulated assistant that must abstain

LongMemEval and BEAM include **abstention**. Full-context and aggressive k-retrieve **hallucinate memories**. Design: if retrieve score < threshold, **do not** invent; BEAM 10M abstention **40.0** (Mem0 platform) shows this remains hard at 10M-token histories. Prefer “I don’t have a memory of that” over a semantic near-miss.

### 6.7 Decision checklist (Principal Architect)

1. Split **STM** (thread, tokens, compaction) from **LTM** (user, facts, episodes). LangGraph already does this in the API.
2. Split **semantic** from **episodic**; keep pointers. You need episodes for audit, unlearning, and citation.
3. Put **authz on the retrieve path** before ANN. Memory poisoning and tenant leak are the same bug class: untrusted text became trusted state.
4. Budget **constructor tokens**, not “top-k.” Zep 1.6k vs Mem0 ~7k vs 115k full-context is the real product.
5. Run consolidation **off the user path** (sleep-time/dreaming). Pay the paper’s 5× test-time reduction only if `c` is stable.
6. Prove Art. 17 on a **staging clone** of prod indexes, including HNSW compaction and traces.
7. Do not procure on DMR/LoCoMo alone; add LongMemEval_M / BEAM 10M and **your** poison + identity tests.

---

## Sources

1. https://arxiv.org/abs/2310.08560 — Packer et al., MemGPT: Towards LLMs as Operating Systems (2023)
2. https://par.nsf.gov/servlets/purl/10524107 — MemGPT NSF preprint PDF
3. https://github.com/letta-ai/letta — Letta (formerly MemGPT) server
4. https://docs.letta.com/guides/core-concepts/memory/context-hierarchy/ — blocks vs files vs archival vs RAG
5. https://docs.letta.com/guides/core-concepts/memory/archival-memory/ — archival insert/search
6. https://docs.letta.com/guides/core-concepts/memory/memory-blocks/ — always-in-context blocks, last-write-wins
7. https://docs.letta.com/guides/agents/architectures/sleeptime — sleep-time agents, frequency default 5
8. https://www.letta.com/blog/sleep-time-compute/ — MemGPT 2.0 sleep-time productization
9. https://arxiv.org/abs/2504.13171 — Lin, Snell, Packer, Wooders et al., Sleep-time Compute
10. https://github.com/letta-ai/sleep-time-compute — paper code
11. https://docs.letta.com/letta-agent/pricing/ — $20 + $0.10/agent/mo + $0.00015/s
12. https://docs.letta.com/reference/terminology — Constellation retired; Letta Cloud
13. https://www.leoniemonigatti.com/blog/memgpt.html — virtual context management explainer
14. https://arxiv.org/abs/2504.19413 — Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory
15. https://arxiv.org/html/2504.19413 — Mem0 HTML (Table 1–2 latencies, tokens)
16. https://github.com/mem0ai/mem0 — v3 algorithm table (LoCoMo 92.5, p50)
17. https://docs.mem0.ai/platform/overview — managed platform features
18. https://docs.mem0.ai/migration/platform-v2-to-v3 — ADD-only, hybrid retrieval
19. https://docs.mem0.ai/core-concepts/memory-evaluation — LoCoMo/LongMemEval/BEAM category scores
20. https://mem0.ai/pricing — Hobby/Starter $19/Pro $249
21. https://mem0.ai/research — token-efficient algorithm benchmarks
22. https://mem0.ai/blog/state-of-ai-agent-memory-2026 — 2026 landscape + scores
23. https://arxiv.org/abs/2501.13956 — Zep: A Temporal Knowledge Graph Architecture for Agent Memory
24. https://arxiv.org/html/2501.13956 — Zep HTML (DMR, LongMemEval tables)
25. https://github.com/getzep/graphiti — Graphiti OSS temporal KG
26. https://www.getzep.com/ — sub-200 ms, SOC 2 marketing
27. https://www.getzep.com/pricing/ — Flex $125 / Flex Plus $375 / Enterprise
28. https://help.getzep.com/retrieval-philosophy.mdx — 94.7% LoCoMo @ 155 ms; 90.2% LME @ 162 ms
29. https://getzep-graphiti.mintlify.app/concepts/temporal-model — bi-temporal valid vs transaction time
30. https://www.getzep.com/mem0-alternative/ — vendor head-to-head (treat as marketing)
31. https://trust.getzep.com — Zep Trust Center
32. https://docs.langchain.com/oss/python/langgraph/persistence — checkpointer vs Store
33. https://docs.langchain.com/oss/python/langgraph/stores — PostgresStore, TTL sweeper
34. https://docs.langchain.com/oss/python/langgraph/add-memory — STM/LTM recipes
35. https://docs.langchain.com/oss/python/langchain/long-term-memory — namespace/key JSON
36. https://docs.langchain.com/oss/python/langchain/short-term-memory — thread-scoped STM
37. https://reference.langchain.com/python/langgraph.store.postgres/base/PostgresStore
38. https://reference.langchain.com/python/langchain-classic/memory/buffer/ConversationBufferMemory
39. https://reference.langchain.com/python/langchain-classic/memory/buffer_window/ConversationBufferWindowMemory
40. https://reference.langchain.com/python/langchain-classic/memory/token_buffer/ConversationTokenBufferMemory
41. https://reference.langchain.com/python/langchain-core/messages/utils/trim_messages
42. https://reference.langchain.com/python/langchain/agents/middleware/summarization/SummarizationMiddleware
43. https://langchain-ai.github.io/langmem/guides/summarization/
44. https://langchain-ai.github.io/langmem/reference/short_term/
45. https://redis.io/blog/langgraph-redis-build-smarter-ai-agents-with-memory-persistence/
46. https://forum.langchain.com/t/does-the-postgres-checkpointer-serialize-concurrent-fastapi-requests/2882
47. https://support.langchain.com/articles/1242226068-how-do-i-configure-checkpointing-in-langgraph
48. https://platform.claude.com/docs/en/build-with-claude/compaction — compact_20260112, trigger 150k
49. https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool — memory_20250818
50. https://platform.claude.com/docs/en/api/beta/memory_stores — workspace memory stores, SHA-256 versions
51. https://www.anthropic.com/news/context-management — +29% / +39% agentic-search eval
52. https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools
53. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
54. https://www.anthropic.com/engineering/how-we-contain-claude — persistent memory poisoning
55. https://code.claude.com/docs/en/security/ — MCP trust verification
56. https://openai.com/index/memory-and-new-controls-for-chatgpt/ — saved memories + chat history (2025)
57. https://openai.com/index/chatgpt-memory-dreaming/ — Dreaming V3 (2026-06-04), ~5× compute
58. https://help.openai.com/en/articles/8590148-memory-faq
59. https://arstechnica.com/ai/2025/04/chatgpt-can-now-remember-and-reference-all-your-previous-chats/
60. https://community.openai.com/t/will-memory-capabilities-come-to-the-api/934907 — no public Memory API
61. https://arxiv.org/abs/2309.02427 — CoALA, Sumers, Yao, Narasimhan, Griffiths
62. https://dl.acm.org/doi/10.1145/3586183.3606763 — Generative Agents, Park et al. 2023
63. https://arxiv.org/abs/2410.10813 — LongMemEval (ICLR 2025)
64. https://github.com/xiaowu0162/LongMemEval — 115k / 1.5M settings
65. https://arxiv.org/abs/2402.17753 — LoCoMo
66. https://arxiv.org/abs/2502.12110 — A-Mem: Agentic Memory for LLM Agents (NeurIPS 2025)
67. https://github.com/WujiangXu/AgenticMemory — A-Mem code
68. https://doi.org/10.1609/aaai.v38i17.29946 — MemoryBank (Zhong et al., AAAI 2024)
69. https://arxiv.org/abs/2305.10250 — MemoryBank arXiv v3
70. https://www.cognee.ai/inside-cognee-1-0 — remember/recall/improve/forget
71. https://docs.cognee.ai/getting-started/introduction
72. https://www.cognee.ai/
73. https://arxiv.org/html/2605.15338 — Hidden in Memory: Sleeper Memory Poisoning
74. https://arxiv.org/html/2607.14651 — MemPoison
75. https://arxiv.org/pdf/2604.02623 — eTAMP environment-injected poisoning
76. https://arxiv.org/html/2606.24322 — TMA-NM non-malleable origin-bound memory authority
77. https://arxiv.org/html/2606.12703 — SMSR certified defence
78. https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/authorization
79. https://www.permit.io/blog/oauth-on-mcp — MCP OAuth 2.1 / RFC 9728 / 8707
80. https://letsbuildsolutions.com/blog/system-design/designing-a-secure-mcp-server-authentication-tenant-isolation-and-transport-hardening-for-production-model-context-protocol-integrations/
81. https://atlan.com/know/ai-agent/gdpr-compliance-for-ai-agents/
82. https://dreaming.press/posts/right-to-be-forgotten-vector-database.html
83. https://tianpan.co/blog/2026-07-05-the-user-you-cannot-delete-right-to-be-forgotten-in-ai
84. https://www.aipolicydesk.com/blog/ai-agent-persistent-memory-gdpr-compliance-2026
85. https://research.checkpoint.com/2026/rce-and-api-token-exfiltration-through-claude-code-project-files-cve-2025-59536/
86. https://nvd.nist.gov/vuln/detail/cve-2026-21852
87. https://github.com/anthropics/claude-code/issues/21674 — global CLAUDE.md injection
88. https://vectorize.io/articles/hindsight-vs-letta — Letta runtime vs standalone memory (2026)
89. https://www.developersdigest.tech/blog/best-ai-agent-memory-providers-2026 — Mem0/Zep/Letta SKU roundup

**Source count:** 89 URLs. Claims in §§1–6 are tied to papers, vendor docs, or named blogs dated on or before 2026-08-21. Vendor head-to-heads (Zep vs Mem0, provider roundups) are labeled marketing where used.
