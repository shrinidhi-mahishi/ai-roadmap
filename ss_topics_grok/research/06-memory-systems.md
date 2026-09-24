# Research: Memory Systems

**Date researched**: 2026-09-23
**Sources consulted**: 106

Scope: **working-memory buffers, long-term vector/graph recall, episodic summaries, and context compression as four independently scaled planes** — conversation windows, last-k / token-budget STM, LangGraph `messages` as a *memory product*, embeddings + namespaces (user vs agent), Mem0 / Zep / Letta / LangGraph Store *semantics* (not the Store API surface already in [`05-langgraph-state-machines.md`](05-langgraph-state-machines.md) §1.6), session summaries and recursive/hierarchical memory, LLMLingua-class extractive compressors vs vendor abstractive compact. Tokenizer IDs, embedder SKUs, and generation list prices live in [`01-python-llm-foundations.md`](01-python-llm-foundations.md). Packing order, compact *triggers* (Anthropic 150k/50k min, tool-clear 100k/keep-3, Deep Agents 20k offload / 85% summarize, OpenAI `/responses/compact`), and cache-bust mechanics live in [`02-context-engineering.md`](02-context-engineering.md). This file does **not** recopy those tables. ⚠️ No unpublished p50/p95/p99 for Mem0/Zep/Letta/Store retrieve is invented; vendor retrieval ms ≠ chat SLO. `$ / 1k turns` figures are **[inferred]** from a stated reference turn × list prices in 01 plus published memory SKUs — not a vendor “per session” product.

Invariant (CoALA): **the LLM is not the memory**. Working memory is a data structure the prompt is *compiled from*; long-term memory is read via **retrieval** and written via **learning**. The model never searches an index; it emits a tool call or the control plane runs a retriever; observations return as tokens ([Sumers, Yao, Narasimhan, Griffiths, TMLR 2024](https://arxiv.org/abs/2309.02427)).

---

## 1. System Topology & Mechanics

### 1.1 Four planes: working memory vs long-term store vs episodic log vs compressor

A production memory stack is **four planes**, not “a vector DB.” Coupling them is the dominant latency and correctness failure: a stuck extractor stalls answers, a query-time LLM rewrite silently mutates the store, a compressor drops the constraint you need on turn 90, or an episode is destroyed while a fact remains — breaking citation and Art. 17 fan-out.

| Plane | Owns | Typical backing | Hotness |
| --- | --- | --- | --- |
| **Working (STM)** | Active symbols for this decision cycle — *not* identical to the LLM context string | Message buffer, LangGraph `messages` channel, Letta in-context blocks, token-budgeted scratchpad | **Hot** (every token billed) |
| **Long-term store (semantic / archival)** | Facts, entities, user/agent profiles (“what is true *now*”) | Mem0 memories, Graphiti entity/fact edges, Letta archival, LangGraph Store, Pinecone namespaces | **Warm** |
| **Episodic log** | Time-stamped events / trajectories (“what happened,” with provenance) | Graphiti episodes, conversation search, checkpoints, call transcripts | **Warm/cold** |
| **Compressor** | Shrink working set without pretending to be LTM | Sliding window, LLMLingua, recursive summary, Anthropic compact, OpenAI compact, FS offload | **On trigger** (lossy unless extractive+logged) |

**Control plane vs data plane.** The control plane decides *whether* to write, *which* store, *which k*, *whether* to compact, and *who is allowed to call memory tools*. The data plane is Postgres/Neo4j/HNSW, object storage for Anthropic `/memories` files, and LangGraph checkpoints. Letta’s ADE and Zep’s Context Lake are control-plane UIs over data-plane graphs. MCP memory servers sit on the **tool boundary**: they are data-plane with control-plane auth.

**Write path vs read path (do not fuse).**

| Plane | Write path | Read path | Failure if coupled |
| --- | --- | --- | --- |
| Working | Append message / `add_messages`; trim; compact | Assemble prompt from last-k + pinned blocks | Compaction on the user-turn critical path adds a full sampling pass (see 02) |
| LTM | Extract facts, ACL+tenant stamp, embed, upsert/invalidate | Authz **pre-filter**, hybrid retrieve, rerank, constructor budget | Query p99 tracks extract; Mem0 authors: Zep graph rebuilds made just-added memories unsearchable for hours ([Mem0 paper](https://arxiv.org/html/2504.19413)) |
| Episodic | Append episode / checkpoint / transcript | Time-range + BFS from recent episodes | Destroying episodes while keeping facts kills audit |
| Compressor | Trigger on token/message/fraction | Next request sees a *new* prefix | Cache invalidation; summarization loss |

CoALA’s four stores map onto products with almost no renaming: **working / episodic / semantic / procedural**. Procedural memory (prompts, skills, `CLAUDE.md`, tools) is **hot if inlined, cold if tool-fetched** — it is not this module’s LTM SKU, but it *is* a durable injection surface (§4).

### 1.2 RAG vs memory (product distinction)

RAG and memory share embeddings and ANN, so teams collapse them. They are different products:

| Dimension | RAG | Memory |
| --- | --- | --- |
| Source | External corpus the agent did not create | The agent’s (or user’s) own interaction stream |
| Scope | Shared knowledge; versioned documents | Per-user / per-agent / per-run |
| Write path | Re-index on a schedule or on document write | Extract, conflict-policy, expire, unlearn |
| Freshness | Stale *documents* | Stale *beliefs about a person* |
| Typical failure | Missing/outdated doc; wrong chunk | Contradictory facts; identity mix-up; poisoning |
| Cost pattern | Read-heavy | Read **and** write; extract LLM after every turn or every session |

CoALA already said this: RAG over Wikipedia is **read-only semantic memory of the world**; agent memory is **writable semantic + episodic memory of the interaction** ([CoALA](https://arxiv.org/html/2309.02427v3)). The 2026 survey restates it: memory targets stateful, interaction-dependent information; RAG targets external knowledge grounding; they are complementary ([arXiv:2604.01707](https://arxiv.org/html/2604.01707v3)). Using a shared RAG index as “user memory” is how you retrieve another tenant’s ticket.

**User memory vs agent memory.** User memory is *about the human* (preferences, identity, commitments). Agent memory is *about the assistant* (persona, lessons, tool-use patterns, MemFS files). Shared blocks (Letta) and shared Store namespaces (team procedures) are **agent/org memory**. Mixing them in one Pinecone namespace without a type tag is how “I am vegetarian” collides with “always refund without ID.”

### 1.3 Short-term / working memory: buffers, last-k, token budgets, LangGraph `messages`

**Conversation buffer (full history).** LangChain classic `ConversationBufferMemory` stores every turn unfiltered. Linear growth; zero loss until the window overflows. **Deprecated since 0.3.1, removal in 2.0**; replacement is `create_agent` + a **checkpointer** ([ConversationSummaryMemory deprecation](https://reference.langchain.com/python/langchain-classic/memory/summary/ConversationSummaryMemory); [short-term memory](https://docs.langchain.com/oss/python/langchain/short-term-memory)). Still the right *mental model*: STM is the thread’s messages.

**Window (`k` turns).** `ConversationBufferWindowMemory` keeps the last `k` human/AI pairs (FIFO). Constant RAM; drops early constraints (“I’m vegetarian” on turn 2 of a 200-turn support chat). Use when recency *is* the task (IVR, short tickets). Do **not** use as the only memory for identity or policy.

**Token-budgeted buffer.** `ConversationTokenBufferMemory` / `trim_messages(..., strategy="last", max_tokens=N, include_system=True, start_on="human")` drops oldest messages until a token budget. Strictly better than `k` turns when message size varies (tool dumps vs “ok”). Approximate counting is the hot-path default; billing-accurate counts use the tokenizer in 01. LangGraph’s `messages` channel is this buffer **plus a reducer**: without `add_messages`, a node that returns `{"messages": [new]}` **wipes prior history** — the dominant “lost STM” bug (API and `RemoveMessage` / `DeltaChannel` details: **05** §1.2; do not recopy).

**Product semantics of the `messages` channel.** In LangGraph, STM is **thread-scoped** (`thread_id`). It is crash recovery, HITL, and time-travel — **not** a user profile. Cross-thread facts belong in Store (05 §1.6). `DeltaChannel` (`>=1.2`, beta) stores *deltas* so checkpoint bytes do not re-serialize the full transcript every super-step; changing a live thread from delta to non-delta **cannot reconstruct** those checkpoints (05). Compaction *of* `messages` is a **control-plane rewrite** (`SummarizationMiddleware` `before_model` permanently replaces state — 02). Context editing that clears tool results on a *deepcopy* does **not** persist (02). That split is the STM product: **durable transcript vs LLM-facing window**.

**Letta STM.** In-context message buffer + pinned **memory blocks**. `max_message_buffer_length` is best-effort (user/assistant interleaving can overshoot). Voice/sleeptime defaults in source: `DEFAULT_MAX_MESSAGE_BUFFER_LENGTH = 30`, min 15 ([letta/constants.py](https://github.com/letta-ai/letta/blob/bb52a890/letta/constants.py)). `message_buffer_autoclear=true` forgets previous messages while retaining core blocks + archival/recall — advanced only. Recall memory = searchable full conversation history (episodic); not pinned.

**STM products compared (what you actually persist).**

| Product | Durable STM object | LLM-facing window | Cross-session? |
| --- | --- | --- | --- |
| LangGraph checkpointer | Super-step snapshot of `messages` (+ other channels) | Same, unless middleware rewrites | No — `thread_id` |
| LangChain `SummarizationMiddleware` | Rewritten `state["messages"]` (lossy, permanent) | The summary + last-N | Only if checkpointer stores it |
| Anthropic Messages | Client holds **full** unmodified history; server compact/clear is request-scoped (02) | Compacted / tool-cleared | No, unless memory tool files |
| OpenAI compact | Compacted window **including encrypted item**; users kept verbatim | That window | Conversations API object can outlive a Response’s 30-day default |
| Letta | Messages in agent DB + blocks | Compiled from DB | Yes for blocks; messages until autoclear |
| ChatGPT memory | Product profile / dreaming summary | Injected by OpenAI | Yes — **not** on the API |

The interview trap: “we use LangGraph memory” without saying **checkpointer (STM) vs Store (LTM)**. They are different isolation keys, TTLs, and GDPR objects (05).

### 1.4 Long-term vector/graph recall — product semantics

#### MemGPT → Letta (virtual context)

Packer et al. 2023: treat the context window as **physical RAM**; page overflow to **disk** (archival + recall tables) via OS-like tool calls. The agent *self-manages* what is in-context ([MemGPT](https://arxiv.org/pdf/2310.08560)). Letta 2025–26 productizes this as a **compiled prompt** from DB state:

| Tier | Mechanism | Limits (docs) | Product role |
| --- | --- | --- | --- |
| **Core / blocks** | Always-in-context labeled strings; `memory_insert` / `memory_replace` / `memory_rethink`; shareable | Rec. **<50k characters/block**, **<20 blocks/agent** ([context hierarchy](https://docs.letta.com/v1-sdk/memory/context-hierarchy)) | User card + persona (hot) |
| **Files** | Open/close + grep + semantic search | **5 MB**/file, rec. **<100 files** | Repo/docs (cold until paged) |
| **Archival** | `archival_memory_insert` / `_search`; ~**300 tokens**/passage; unlimited count | Agent-curated facts | Warm semantic |
| **Conversation search** | Hybrid over messages | Automatic; not curated | Episodic |
| **External RAG / MCP** | Custom tools | Unlimited | World knowledge, not user memory |

**Last-write-wins on shared blocks.** Setting `value` **replaces** the entire block; concurrent primary + sleep-time edits clobber ([memory blocks](https://docs.letta.com/guides/core-concepts/memory/memory-blocks/index.md)). Sleep-time (Letta 0.7+, [arXiv:2504.13171](https://arxiv.org/abs/2504.13171)): a background agent **owns write tools** for core blocks; default `sleeptime_agent_frequency=5` primary steps. Letta Code 2026: **MemFS** — git-backed memory filesystem; dreaming subagents use git worktrees so they do not block the main agent ([MemFS](https://docs.letta.com/concepts/memfs/index.md)). Files under `system/` are inlined every turn; everything else is a file tree the agent reads on demand.

#### Mem0 — two eras (do not mix scores)

**Paper (arXiv:2504.19413).** Pair-wise extract \((m_{t-1}, m_t)\) with conversation summary \(S\) + last **\(m=10\)** messages → candidate facts → retrieve top **\(s=10\)** similar memories → LLM tool-call **ADD / UPDATE / DELETE / NOOP**. Mem0g: Neo4j directed labeled graph; mark obsolete edges invalid (not physical delete). Retrieval tokens on LOCOMO: Mem0 **1,764**; Mem0g **3,616**; Zep **3,911**; OpenAI playground memories **4,437**; full-context **26,031**. Construction footprint: Mem0 ~**7k** tok/conversation, Mem0g ~**14k**, Zep **>600k** (node summaries + edge facts; Mem0 authors). Immediate-after-write retrieval on Zep often failed in that harness; hours later improved — Graphiti’s async LLM pipeline ([HTML](https://arxiv.org/html/2504.19413)).

**Platform / OSS v3 (2026).** Single-pass **ADD-only** (no UPDATE/DELETE at extract). Agent-generated facts first-class. Native entity graph on Platform (OSS: graph store **removed**; entity boost via a parallel collection). Hybrid **semantic + BM25 + entity boost**, temporal ranking at **read** time. When the user moves from NYC to SF, **both** facts remain; retrieval ranks current. If you needed UPDATE/DELETE to keep counts low, v3 pushes that to **expiration + delete API** ([platform v2→v3](https://docs.mem0.ai/migration/platform-v2-to-v3); [OSS v2→v3](https://docs.mem0.ai/migration/oss-v2-to-v3)). GitHub/research table (managed, **not** the 2025 paper): LoCoMo **92.5** @ **~7.0k** tok, p50 **0.88 s**; LongMemEval **94.4** @ **6.8k**, p50 **1.09 s**; BEAM 1M **64.1** @ **6.7k**; BEAM 10M **48.6** @ **6.9k**. ⚠️ OSS ≠ platform; ±1 judge CI stated; do not procure on LoCoMo alone.

**Scopes.** `user_id` / `agent_id` / `app_id` / `run_id`. v3: `user_id` on `search()` as a top-level kwarg **raises**; it must be inside `filters` — the product is teaching you that **tenant is a filter, not an argument the model can invent**.

**Read-path fusion (v3 OSS).** Three signals are normalized and fused into one `score`: dense cosine, BM25, and entity match (spaCy + parallel entity collection). Fusion **adapts** if BM25 or entities are missing. Temporal ranker is a *fourth* overlay for “current vs historical.” This is why ADD-only can work: you stopped asking the extract LLM to be a CRDT and started asking the retriever to be a **bi-temporal-ish ranker**. It is still not Graphiti — there is no `invalid_at` on a fact edge, so “what was true on date D?” is a ranking hope unless you stored dates in the text.

#### Zep + Graphiti — temporal KG, not “RAG with timestamps”

Three-tier graph ([arXiv:2501.13956](https://arxiv.org/html/2501.13956v1)):

1. **Episode subgraph** — raw messages/text/JSON + `t_ref`; non-lossy; speaker auto-extracted; last **n=4** messages (2 turns) as NER context; reflection pass to cut hallucination.
2. **Semantic entity subgraph** — entities (name embeddings + full-text) and fact edges; hybrid cosine + BM25; Cypher writes (not LLM-generated queries).
3. **Community subgraph** — **label propagation** (not Leiden) so new nodes join without full recompute; map-reduce summaries; periodic refresh still required.

**Bi-temporal model** ([docs](https://getzep-graphiti.mintlify.app/concepts/temporal-model)): *valid time* (`valid_at` / `invalid_at`) vs *transaction time* (`created_at` / `expired_at`). Contradiction → `invalid_at` ← new `valid_at`, `expired_at` ← now; **do not delete**. Point-in-time queries are first-class; vanilla GraphRAG/vector DBs are not. Retrieval: \(f = \chi \circ \rho \circ \varphi\) — search (cosine + BM25 + BFS) → rerank (RRF, MMR, episode-mention frequency, node-distance, cross-encoder) → constructor (facts with date ranges + entity summaries + community summaries). BFS can seed from recent episodes so “just talked about X” stays in context. **No LLM at retrieve time** in the paper stack — the constructor is a *budget*, not a second extract.

Paper LongMemEval_S (~**115k** tok): gpt-4o **71.2%** vs full-context **60.2%** (+**18.5** pp), latency **2.58 s** (IQR 0.684) vs **28.9 s**, context **1.6k** vs **115k**. gpt-4o-mini **63.8%** / **3.20 s** vs **55.4%** / **31.3 s**. DMR: Zep **94.8%** vs MemGPT **93.4%** (gpt-4-turbo); authors note DMR is too easy (full-context already ~94%). Vendor 2026: LoCoMo **94.7% @ 155 ms**, LongMemEval **90.2% @ 162 ms**, “sub-200 ms regardless of graph size.” ⚠️ Paper e2e latency ≠ retrieval-only vendor ms; different judges/models/years. Mem0’s 2026 leaderboard post still cites Zep LME **71.2%** under a GPT-4o judge — **do not flatten vendor LoCoMo 94.7 with paper LME 71.2**.

#### LangGraph Store — memory *product*, not the API

API surface (namespace tuples, `limit=10` silent truncation, prefix search `("alice",)` matching `("alice","memories")`, `index=` required for semantic search, `start_ttl_sweeper()`): **05** §1.6. Product semantics that 05 does not own:

- **STM ≠ LTM.** Checkpointer = this thread. Store = cross-thread JSON `(namespace, key) → item`. Compile **both**.
- **Namespaces are ACL.** Convention: `("t", tenant, "u", user, "kind", "facts")`. Prefix search is a **leak primitive** if you search `("t", tenant)` and forget the user component.
- **User vs agent.** Put user facts under the user tuple; put persona/lessons under `("agent", agent_id, ...)`. Do not overload one key `"memory"`.
- **Semantic search is off until you pay embedder tokens** (prices: 01). `query=` without `index=` is a no-op or `NotImplementedError`.
- **`put` replaces the key** — no CRDT, no Mem0-style ADD-only. Concurrent writers last-write-wins, same as Letta blocks.
- **TTL is forgetting, not GDPR.** `default_ttl` is **minutes**; `refresh_on_read` turns access into immortality (Generative Agents recency, inverted). Sweeper is **not** Art. 17.

#### Pinecone namespaces as per-user memory

Pinecone’s documented multi-tenancy pattern is **one namespace per tenant** in a shared serverless index: queries cannot cross namespaces; each namespace is stored separately; query RUs scale with **namespace size** (1 RU / 1 GB). 100 tenants × 1 GB: query one tenant = **1 RU**; metadata-filter a 100 GB shared namespace = **100 RUs** for the same query ([implement multitenancy](https://docs.pinecone.io/guides/index-data/implement-multitenancy)). Metadata `$in`/`$nin` max **10,000** values — filtering by a large list of user IDs is an anti-pattern; use namespaces or access-control **groups**. For *user* memory inside a tenant, two layers: namespace = tenant (isolation), metadata = `user_id` / `memory_kind` (subdivision). Shared-namespace + post-filter after top-k is how you retrieve another user’s memories under ANN neighbor leakage.

### 1.5 Episodic summaries, hierarchical memory, recursive summarization

**Generative Agents (Park et al., UIST 2023).** Append-only **memory stream** of NL observations. Retrieval:

\[
\mathrm{score} = \alpha_r\,\mathrm{recency} + \alpha_i\,\mathrm{importance} + \alpha_v\,\mathrm{relevance}
\]

All \(\alpha = 1\) after min-max to [0,1]. Recency = exponential decay **0.995** per sandbox hour since last *access* (not creation). Importance = LLM integer 1–10. Relevance = cosine(query, memory). Top memories that fit the window go into the prompt. **Reflection** fires when cumulative importance of recent observations exceeds ~**150** points (~2–3 times per simulated day); reflections are stored *back* into the stream and can recurse ([arXiv:2304.03442](https://arxiv.org/abs/2304.03442)). Released code uses weights `[0.5, 3, 2]` for recency/relevance/importance — paper and code disagree; treat α as a **tuned product**, not a constant ([retrieve.py](https://github.com/joonspk-research/generative_agents/blob/main/reverie/backend_server/persona/cognitive_modules/retrieve.py)).

**MemoryBank (Zhong et al., AAAI 2024).** Daily event summaries + user portrait + FAISS. Forgetting: \(R = e^{-t/S}\); \(S\) starts at 1; recall increments \(S\) and resets \(t\) ([paper](https://huggingface.co/papers/2305.10250)). Summaries sit *outside* the decay draw in the reference code — the derived record is the durable one; the turns it came from are what die ([atlas note](https://neoneye.github.io/agent-memory-atlas/systems/memorybank/)). That is the opposite of Graphiti (invalidate facts, keep episodes).

**Recursive summarization (Wang et al., 2023/24).** \(M_i = \mathrm{LLM}(\mathrm{session}_i, M_{i-1})\); respond from latest \(M\) ([arXiv:2308.15022](https://arxiv.org/html/2308.15022v3)). Complements long context *and* RAG. Zep DMR baseline: recursive summarization **35.3%** vs MemGPT **93.4%** — a single rolling summary is **not** a memory system for multi-session QA.

**HiGMem (ACL Findings 2026).** Two-level **event / turn** memory: event summaries as cheap semantic anchors; LLM then picks which linked turns to read. LoCoMo10: best F1 on 4/5 categories; adversarial F1 **0.54 → 0.78** vs A-Mem while retrieving an **order of magnitude fewer turns** ([arXiv:2604.18349](https://arxiv.org/html/2604.18349v2)). Hierarchical memory only works if you **keep pointers** to episodes.

**RAPTOR (Sarthi et al., 2024)** recursively embeds summaries of clusters so retrieval can hop granularity; HiGMem cites it as the document-RAG ancestor of event/turn memory. Use RAPTOR-shaped trees for **corpus** RAG; use HiGMem/Graphiti-shaped trees for **interaction** memory. Mixing them in one index is how a user preference retrieves a Wikipedia cluster.

**Extractive vs abstractive episodes (product rule).** After-call summaries are **abstractive** (lossy). The transcript (or Graphiti episode, or AgentCall quote) is **extractive provenance**. LangChain persistent summarization **replaces** old messages in state (02) — the UI must not assume the checkpoint still has the needle. Recursive \(M_i\) without storing sessions is how you get summarization loss on turn 90. Graphiti’s design (keep episodes, invalidate facts) is the one that still lets you cite.

**A-Mem (Xu et al., NeurIPS 2025).** Zettelkasten notes (keywords, tags, contextual description) + LLM link generation + **evolution** of neighbor notes on insert; retrieve top-k plus linked neighbors ([arXiv:2502.12110](https://arxiv.org/html/2502.12110)). LoCoMo with GPT-4o: **1,216** tokens vs LoCoMo/MemGPT **16,910**; authors claim **85–93%** token cut and **<$0.0003**/memory op on commercial APIs. Mem0 re-run of A-Mem on LOCOMO: **2,520** retrieved tok, search p50/p95 **0.668 / 1.485 s**, J **48.38%** — denser notes, still no first-class forgetting in the original design. Evolution is a **write-amplification** job (LLM over neighbors on every insert).

**Hippocampal indexing (do not cargo-cult “hippocampus = vector DB”).** HippoRAG (NeurIPS 2024): LLM as neocortex (entity extract → schemaless KG as hippocampal index); query entities seed **Personalized PageRank**; pattern completion in one retrieval step. Multi-hop QA: up to **+20%**; vs iterative IRCoT **10–20× cheaper** and **6–13× faster** ([arXiv:2405.14831](https://arxiv.org/pdf/2405.14831)). EM-LLM (ICLR 2025): surprise-based event segmentation + temporal-contiguity recall; retrieval across **10M** tokens ([paper](https://proceedings.iclr.cc/paper_files/paper/2025/file/c05144b635df16ac9bbf8246bbbd55ca-Paper-Conference.pdf)). REMem (2026): time-aware event graph + agentic retriever; +**3.4** / +**13.4** pp vs Mem0 / HippoRAG 2 on episodic recollection vs reasoning ([arXiv:2602.13530](https://arxiv.org/html/2602.13530)). Production mapping: Graphiti’s episode→entity→community *is* this loop; a flat Pinecone index is **not**.

### 1.6 Context compression (the fourth plane)

Triggers, cache-bust, and `iterations[]` billing: **02**. Memory-product consequences:

| Method | Extractive vs abstractive | Survives as LTM? | Typical ratio |
| --- | --- | --- | --- |
| Sliding window / `trim_messages` | Extractive drop | No (unless event log kept) | Window / history |
| LLMLingua (EMNLP 2023) | Extractive token drop via small-LM PPL | No | Up to **20×**; GSM8K **−1.5** EM ([arXiv:2310.05736](https://arxiv.org/pdf/2310.05736)) |
| LongLLMLingua (ACL 2024) | Question-aware; document reorder | No | ~**4×** tokens; NQ **+21.4%**; LooGLE **94%** cost cut; 10k tok **1.4–2.6×** e2e ([site](https://llmlingua.com/longllmlingua.html)) |
| LLMLingua-2 | Token classification (XLM-R); task-agnostic | No | **2–5×**; compressor **3–6×** faster than LLMLingua; e2e **1.6–2.9×**; coarse-to-fine ~**15×** for multi-doc ([arXiv:2403.12968](https://arxiv.org/html/2403.12968)) |
| Recursive / session summary | Abstractive | Becomes a *new* memory (lossy) | Session → hundreds of tokens |
| FS offload (Deep Agents, Anthropic memory tool) | Extractive move | Yes, if the file is durable | Tool dump → 10-line preview + path (02: **20k** offload) |
| Anthropic `compact_20260112` | Abstractive server summary | No — unless memory tool wrote first | Trigger/min: **02**; +29% / +39% vendor eval |
| Anthropic `clear_tool_uses_20250919` | Mechanical delete | Tool results refetchable | Vendor: **−84%** tokens vs baseline (tool exhaust, not summarization) |
| OpenAI `/responses/compact` | Opaque encrypted item; **user messages kept verbatim** | Users remain; assistant traces not human-QA | Must fit the window *before* compact (02) |

**LLMLingua is not memory.** It is a **read-path compressor** for ICL/RAG/history already selected. It does not extract facts, resolve contradictions, or isolate tenants. Dropping tokens with a small LM can delete the only copy of a constraint unless the episodic log still has it.

**Anthropic stack as complementary products** (cookbook): compaction (lossy STM), tool clearing (cheap STM), memory tool (client-side LTM under `/memories` — `view/create/str_replace/insert/delete/rename`, Claude 4+). Memory **survives** compaction only if the client actually persisted files. Path traversal deny is **your** job ([memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)). Compaction can keep the **system** prefix cached if you breakpoint the system prompt separately from the compaction block ([compaction docs](https://platform.claude.com/docs/en/build-with-claude/compaction)).

**OpenAI compact vs ChatGPT memory.** `/responses/compact` and Conversations API are **intra-conversation state**, not a Memory API. ChatGPT saved memories (Apr 2024) + “reference chat history” (Apr 2025) + Dreaming V3 (4 Jun 2026, vendor: ~**5×** cheaper dreaming compute, time-sensitive accuracy **9.4% → 75.1%** in a secondary writeup) are **product**, not API. Community: **no Memory API** on Chat Completions/Responses. Do not design enterprise agents assuming ChatGPT memory is callable ([conversation state](https://developers.openai.com/api/docs/guides/conversation-state); [Dreaming V3](https://openai.com/index/chatgpt-memory-dreaming/)).

---

## 2. Token Economics & NFR Metrics

Embedder and generator **list prices**: **01**. Compaction as a **second sampling pass** and GPT-5.4 **>272k** 2×/1.5× cliff: **02**. This section is retrieve-k vs stuff-all, summary LLM cost, and `$ / 1k turns` with memory.

### 2.1 Retrieve-k vs stuff-all (the actual product)

LOCOMO conversations average ~**26k** tokens (Mem0 paper, `cl100k_base`). LongMemEval_S ~**115k**; LongMemEval_M ~**1.5M** (Zep used _S because gpt-4o’s 128k window). Stuff-all wins judge score on 26k (full-context J **72.90%** vs Mem0 **66.88%**) and **loses** on 115k (60.2% vs Zep 71.2% gpt-4o). The economic case is not “memory is more accurate”; it is “memory is accurate enough at 1/10–1/70th the tokens.”

**Mem0 paper Table 2 (LOCOMO, search + generate, GPT-4o-mini stack):**

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

Mem0 p95 total is **91%** below full-context (1.440 vs 17.117). LangMem’s 18–60 s search is a **control-plane** cost (LLM memory ops per query), not ANN. Constructor budgets that actually ship: Zep paper **1.6k**; Mem0 v3 **~7k**; A-Mem paper **~1.2k**; Letta archival k=10 × ~300 tok ≈ **3k** before the generator. Over-retrieve (k too large, no rerank, no token cap) is lost-in-the-middle + injection volume (02).

**p99:** ⚠️ **unpublished** for Mem0, Zep, Letta, LangGraph Store. Budget p99 ≈ 2–4× p95 **[inferred from typical ANN+LLM tails, not a vendor number]** and measure yourself. Zep vendor **155–162 ms** is retrieve-only; paper **2.58 s** includes generation + RTT to us-west-2.

### 2.2 Summary / extract / compress LLM cost

Write-path extract is **extra generation**, not free:

- Mem0 paper: two LLM passes (extract + ADD/UPDATE/DELETE) per message pair; v3: **one** ADD-only pass.
- Graphiti: NER + reflection + per-new-edge invalidation LLM + community map-reduce. Construction is **async and LLM-bound** (hours in Mem0’s harness; “under a minute” for Mem0g).
- Recursive summary: one LLM call per session (or per N turns) that must itself fit a window.
- Sleep-time: ~**5×** less **test-time** compute for same accuracy on Stateful GSM-Symbolic/AIME; scaling sleep-time **+13% / +18%** accuracy; **2.5×** lower average cost when **10** queries share one precomputed `c'` ([arXiv:2504.13171](https://arxiv.org/abs/2504.13171)). Amortization **fails** for one-shot chats.
- Anthropic compact: billed like a normal request; top-level `usage` **excludes** the compaction iteration — sum `usage.iterations[]` (02).
- LLMLingua-2: compressor is a **small classifier**, not a frontier LLM — the cost is GPU/CPU on the compressor, then fewer billed tokens on the closed model.

Embed vs generation (prices: 01). `text-embedding-3-small` **$0.02 / 1M**. **[inferred]** embedding 500 memory writes × 200 tokens = 100k embed tok → **$0.002** — noise vs one Sonnet 5 extract of 2k in / 400 out ≈ **$0.008**. Memory-layer **SKU** can still dominate if you retrieve every turn on a metered plan (below).

### 2.3 `$ per 1k turns` with memory **[inferred]**

**Reference turn (stated, not a SKU):** 1 user-facing turn that (a) retrieves once, (b) injects ~2k–7k memory tokens into a mid-size generator, (c) **async** extract of 2 memories (user+assistant pair). Consolidation not included unless noted. Generator: Claude Sonnet 5 **$2 / $10 per MTok** (01) or GPT-5.4 **$2.50 / $15** (01).

| Generator context | Input tok | **[inferred]** gen $ / turn (Sonnet 5, 500 out) | **[inferred]** $ / 1k turns |
| --- | --- | --- | --- |
| Mem0-like 1.8k memory + 1k query | ~2.8k in | \( (2800\times2 + 500\times10)/10^6 \) ≈ **$0.0106** | **~$11** |
| Mem0 v3 ~7k | ~8k in | ≈ **$0.021** | **~$21** |
| Zep constructor 1.6k | ~2.6k in | ≈ **$0.0102** | **~$10** |
| Full LOCOMO 26k | ~27k in | ≈ **$0.059** | **~$59** |
| LongMemEval_S 115k | ~116k in | ≈ **$0.237** | **~$237** |

Directionally matches Mem0’s “>90% token cost” vs full-context on 26k. GPT-5.4 at 300k after the **272k cliff** (02) is a different regime — compact/offload first.

**Memory SKU overlay (2026 public pages; treat as marketing until a contract):**

| Product | Published meter | **[inferred]** memory $ / 1k retrieve-turns |
| --- | --- | --- |
| Mem0 Starter **$19**/mo / 50k adds + **5k** retrievals | Retrieval-bound | 1k sessions = 20% of retrieval quota → **$3.80** |
| Mem0 Pro **$249**/mo / 500k adds + **50k** retrievals | Retrieval-bound; graph + Dream extra LLM | **$4.98** |
| Mem0 Hobby $0 / 10k adds + **1k** retrievals | 1 retrieve/turn saturates at 1k turns | **$0** until overage |
| Zep Flex **$125**/mo / 50k credits, overage **$25 / 10k**; reads often **unmetered** on comparison pages | ⚠️ credit-per-add not fully specified publicly | Do not convert to $/turn without a quote; RPM **600** Flex / **1000** Flex Plus |
| Letta API **$20**/mo + **$0.10 / active agent / mo** + **$0.00015 / s** server-side tools + pay-go LLM | Memory LLM tokens dominate | **[inferred]** 1k turns on **one** always-on agent: platform ≈ $20.10/mo if they fit the month |

On the reference turn, **generation still beats the memory SKU** (~$10–21 vs ~$4–5) unless you stuff 26k–115k or run sleep-time on a frontier model every 5 steps without amortization.

**Compression ROI.** LLMLingua 20× on GSM8K is an ICL demo, not an agent SLO. Production-shaped: LongLLMLingua **4×** with quality *up*; LLMLingua-2 **2–5×** with a cheap compressor; tool-clear **84%** (Anthropic vendor, tool exhaust). Abstractive compact trades cache hits and auditability for window headroom (02).

### 2.4 Prompt cache vs memory writes

STM prefix (system + core blocks + stable profile) should be the **cached** prefix (mechanics: 01/02). Memory systems fight the cache:

| Event | Cache effect |
| --- | --- |
| Sleep-time rewrites a Letta `human` block mid-session | Prefix hash changes; next turn is a cache miss |
| Mem0 injects different top-k each turn | Memory block cannot sit *before* the cache breakpoint unless you pin a stable card and put retrieve-k **after** the breakpoint |
| Anthropic compact | New `compaction` block; system can stay cached if you breakpoint it separately ([compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)) |
| Store `put` of a fact | No cache effect until you **inject** it |
| `trim_messages` dropping the oldest user turn | Prefix changes unless you only trim *after* the breakpoint |

**[inferred] design:** pin a 1–2k **profile card** (updated by sleep-time, not per turn) in the cached prefix; put retrieve-k in the uncached suffix. That is the only way memory personalization and 5-minute Anthropic cache TTL coexist. Retrieval cache `(user_id, query_hash, index_version) → hits` with short TTL; invalidate on write. ⚠️ Hit rates unpublished.

### 2.5 Throughput NFRs that exist

- **Zep Flex:** 600 RPM; Flex Plus 1,000 RPM; Enterprise custom. API log retention **1 day / 7 days / 1 year** depending on SKU (pricing page). Sub-200 ms is retrieve, not RPM of chat.
- **Mem0:** public pages meter **adds and retrievals**, not RPM. A 1-retrieve-per-turn agent on Starter dies at **5,000** turns/month — that is the NFR, not latency.
- **LangGraph Store:** embedder TPM (01) plus Postgres. `asearch` default `limit=10` **silently truncates** — overflowing k is a correctness bug, not a 429.
- **Pinecone:** query RUs = f(namespace GB). Per-user namespaces keep RU **and** isolation aligned; a “one index for all users” memory design pays 100× RUs and fails Art. 17 locality.
- **Letta:** tool execution **$0.00015/s** server-side; archival search is an agent tool loop (extra TTFT), not a 150 ms ANN.

> ⚠️ Limited public data for p99 memory retrieve, Store `put` vs model RTT, and Mem0/Zep multi-region replication lag. Do not invent them.

---

## 3. Distributed Resilience & State

### 3.1 Eventual consistency of memory writes

LTM write is **not** linearizable with the user turn unless you block TTFT on extract+index.

| System | When a write is searchable | Mitigation |
| --- | --- | --- |
| Mem0 paper / v3 add | After extract LLM + embed + index | Async extract; accept STM-only for the next turn |
| Graphiti / Zep | After async LLM graph pipeline (NER, invalidation, community) | Ingest **watermark**; fallback to **episodes** (non-lossy); webhooks on complete |
| Letta archival | After insert + embed | Agent-initiated; sleep-time owns blocks |
| LangGraph Store | After `put` + optional embed | Same process as the node; still not a distributed commit with the checkpointer unless you wrap a txn |
| Pinecone | After upsert; ANN **eventual** for fresh vectors | Query-after-write with id fetch, not ANN, for the just-written fact |
| OpenAI compact / Anthropic compact | Immediate for the *next* request’s prefix | Lossy; not a store |

Mem0’s Zep observation (hours until retrieve works) is the canonical **write/read race**. Product rule: **never answer “what did I just tell you” from LTM**; that is STM.

**Checkpoint vs store (resilience, not API).** Checkpointer resume is **at-least-once** nodes at the last committed super-step (05 durability modes). Store `put` during a node that later fails can leave an **orphan memory** (write happened, checkpoint rolled back) or a **ghost gap** (checkpoint committed, store put lost) unless you make store writes idempotent and keyed by `(thread_id, checkpoint_id, memory_id)`. Agent Server: **≤1 run per `thread_id`** (05) — that lease is STM concurrency, not LTM.

Worked orphan: node extracts “user is allergic to penicillin,” `store.put(("u", alice, "facts"), "allergy", ...)`, then the model call throws. Durability `"async"` (Agent Server default) may have already committed the previous super-step. Resume **replays the node**; a non-idempotent extract inserts a duplicate allergy. Durability `"sync"` puts the checkpoint on the critical path (05) but still does not wrap Store in the same Postgres transaction unless you built that. **[inferred] rule:** memory writes are **exactly-once via idempotency keys**, never via hoping the checkpointer and Store share a txn.

**Hot / warm / cold (latency, not marketing).**

| Tier | Target | Contents | Backing |
| --- | --- | --- | --- |
| Hot | p50 <50–200 ms **assembly** + model TTFT | System, core blocks, last-N, profile card | In-process / Redis / pinned tokens |
| Warm | p50 100–400 ms search (Mem0 paper 148 ms; Zep vendor 155–162 ms retrieve) | Semantic facts, recent episodes | pgvector, Neo4j, Mem0/Zep APIs |
| Cold | seconds–hours | Full traces, old episodes, community rebuilds, `/memories` files | Object storage, Graphiti community refresh |

Letta’s hierarchy is explicit: every-turn truth = **block**; maybe-needed fact = **archival**; corpus = **files/RAG**. Anthropic memory files are cold until `view` pages them hot.

**Conflict resolution.** Letta shared blocks: **last write wins**. Graphiti: LLM contradiction check + temporal invalidation; new information prioritized on transaction timeline. Mem0 paper: LLM chooses ADD/UPDATE/DELETE/NOOP against top-s. Mem0 v3: **never overwrite** — conflicts become extra rows; read-time ranker must pick “current.” LangGraph Store: `put` replaces key. Anthropic Memory Stores (beta): `content_sha256` precondition. Design **per-user single-writer** (sleep-time owns core memory in Letta 0.7) or optimistic versions.

### 3.2 TTL, GC, unbounded growth

Without policy: episodic stores grow linearly with turns; semantic stores grow with extract recall; graphs grow with entities×facts; ADD-only (Mem0 v3) and invalidate-don’t-delete (Graphiti) **never shrink**.

| Control | Who | Notes |
| --- | --- | --- |
| Redis / Store TTL | LangGraph | Minutes; **must** `start_ttl_sweeper()`; `refresh_on_read` fights forgetting |
| Shallow checkpointer | LangGraph Redis | Latest checkpoint only — loses time-travel |
| Letta archival unlimited | Letta | You add GC |
| Graphiti invalidation | Zep | History **grows**; Art. 17 needs a hard-delete path |
| Mem0 expiration + delete API | Mem0 v3 | Separate from extract |
| MemoryBank Ebbinghaus | Research | Not GDPR; probabilistic drop |
| Pinecone namespace delete | Pinecone | Tenant-shaped GC |

**Capacity NFR is a product decision**, not a library default.

### 3.3 Circuit breakers and degraded modes

Memory is on the critical path of **personalization**, not of **“can the agent answer.”**

| Dependency | Breaker | Degraded mode |
| --- | --- | --- |
| Memory retrieve | Timeout **200–500 ms** on search **[inferred SLO, not vendor]**; fail **open** | STM + cached profile card; log `memory_miss` |
| Memory write | Queue; do not block the user turn | Ack; extract async; idempotent add |
| Embedder | Same as RAG: version pin | Stale embedder = silent recall collapse |
| Graph construction | Backpressure; watermark | Read episodes if facts not ready |
| Sleep-time / dreaming | Max parallel jobs per tenant | Skip consolidation; messier blocks |
| Compressor | If compact fails near 95% window (OpenAI Codex #10823 — 02) | Rotate ~70%; offload first |

Anthropic `pause_after_compaction` is a **human circuit breaker** for lossy STM (02).

### 3.4 Memory poisoning recovery

Poisoned writes are **durable**. Recovery is not “re-embed”:

1. **Quarantine** by origin tag (tool vs user vs web vs trusted CRM).
2. **Replay** from episodic log with a new extract policy (Graphiti episodes make this possible; ADD-only + no episodes does not).
3. **Hard-delete** poisoned vector IDs + HNSW compaction (soft-delete is not recovery — §4).
4. **Do not** promote web/tool observations to semantic memory without human confirm (eTAMP).
5. Write-time consistency checks suppress MemPoison **L1**, not **L2/L3** (compositional / trigger-conditioned). Need read-time context-sensitive scoring.

---

## 4. Enterprise Security & Governance

### 4.1 Tenant isolation of memories

| Layer | Correct control | Failure |
| --- | --- | --- |
| Vector/graph query | **Pre-filter** `tenant_id`/`user_id` on every ANN, BM25, and Cypher path | Post-filter after top-k leaks neighbors |
| Namespaces | Pinecone ns = tenant; LangGraph `("t", tenant, "u", user)`; Mem0 filters dict | Prefix search `("t", tenant)` |
| Shared Letta blocks | Explicit attach; read-only to workers | Two customers on one `human` block |
| MCP | Token-bound tenant; never `user_id` in tool args | Asana-class confused deputy |
| Physical | Per-tenant index / VPC / BYOC | Shared HNSW + metadata hope |

Zep: SOC 2 Type II / HIPAA BAA on **Enterprise** in public matrices — confirm Trust Center per SKU. Mem0: GDPR-ready claim + trust.mem0.ai. Letta Enterprise: SAML/OIDC, RBAC.

### 4.2 PII in embeddings (Vec2Text)

Morris et al., EMNLP 2023: iterative invert+re-embed recovers **92%** of **32-token** inputs **exactly**; recovers **full names** from clinical-note embeddings ([anthology](https://aclanthology.org/2023.emnlp-main.765/)). Attackers need **black-box embed pairs**, not model weights — hosted embed APIs are in-scope ([CIKM 2024 mitigation paper](https://doi.org/10.1145/3673791.3698414)). Reproducibility work (2025) still finds high BLEU on short texts; quantization, noise, and user-specific transforms reduce leakage but do **not** anonymize. **Treat memory vectors as confidential as source text.** Redact SSN/PAN **before** embed. Do not ship “we only store embeddings” as a GDPR anonymization story.

### 4.3 GDPR Art. 17 vs vectors

Clock: without undue delay, **max one month** (Art. 12(3), +2 months if you notify). Erasure is a **fan-out**:

1. Semantic rows / graph nodes+edges tagged by `user_id`.
2. Episodes / checkpoints / Store keys / `/memories` files / call recordings.
3. Vector IDs. HNSW **soft-delete** until compaction/VACUUM. Ghost Vectors (arXiv:2606.18497, 2026): soft-deleted embeddings remain reconstructible; **25.5%** exact person-name recovery from soft-deleted text embeddings in that harness; query suppression ≠ erasure ([HTML](https://arxiv.org/html/2606.18497v1)). EDPB-aligned commentary: functional API 200 ≠ storage-layer erase.
4. Prompt/response caches; retrieval caches keyed by `user_id`.
5. Trace vendors (physical purge delayed).
6. Backups — crypto-shred **per-user keys** or wait backup TTL inside the month.
7. Fine-tuned weights: **unlearning unsolved**; do not train on raw personal memory.

Graphiti invalidation preserves history — **Art. 17 requires a hard-delete path**, not only `invalid_at`. Mem0 v3 ADD-only makes in-place update easier to audit but **deletion is a separate pipeline**. Provenance maps (episode ↔ fact) are what make fan-out possible. Per-user encryption keys or per-user indexes beat shared HNSW + metadata.

### 4.4 Zero-Trust: who can write memory tools

Memory tools (`archival_memory_insert`, Mem0 `add`, Store `put`, Anthropic `create`/`str_replace` under `/memories`) are **privilege**. Rules:

- **OAuth 2.1 + PKCE**; RFC 9728 resource metadata; RFC 8707 resource indicators so a token for MCP server A cannot hit server B; **no token passthrough**.
- `tenant_id` / `user_id` **only from the verified token**.
- Separate **observation** vs **belief** stores: web/tool text is not write-authorized to semantic memory.
- Anthropic memory tool: restrict to `/memories`; path-traversal deny; storage is **your** infra.
- Claude Code: repo `.mcp.json` / `CLAUDE.md` are **procedural memory** injection surfaces (Check Point CVE-2025-59536 / CVE-2026-21852; GitHub #21674) — cite as persistent-memory threat, not “just prompts.”
- Sleep-time / Dream agents that can rewrite core blocks are **admin writers**. Single-writer. Audit who launched them.

**Tool RBAC (memory-specific).**

| Tool | Who may call | Bind |
| --- | --- | --- |
| `archival_memory_search` / Store `search` | Any turn, after authz pre-filter | Namespace from token |
| `archival_memory_insert` / Mem0 `add` / Store `put` | Allow-list: user-confirmed preferences; sleep-time agent; never raw tool-result text | Origin tag required |
| Anthropic `delete` / `str_replace` on `/memories` | Same allow-list + path prefix check | `/memories/{user_id}/` |
| Graphiti `add_episode` | Ingest workers, not the user-facing tool loop | `reference_time` from server clock + speaker id |
| Compact / summarize | Control plane, not the model (Deep Agents gates `compact_conversation` at ~50% of auto trigger — 02) | Audit the summary |

Audit minimum: who wrote, who read, which query, which k, which memories entered the prompt, model/index versions, origin tag. Zep Enterprise: API logs up to **1 year** on some SKUs. Anthropic Memory Stores: `memory_version_id` + SHA-256.

---

## 5. Production Failure Modes

| Failure | Mechanism | Blast radius | Mitigation |
| --- | --- | --- | --- |
| **Stale memory** | Saved fact without temporal invalidation (“trains for a marathon” + “sprained ankle”) | Wrong personalization; OpenAI’s stated reason for dreaming | Bi-temporal edges; recency×validity; sleep-time; user-visible profile |
| **Contradictory facts** | ADD-only accumulation; last-write-wins clobber; two speakers in one store | Coin-flip answers | Graphiti invalidation; v3 ranker; **do not** mix users; single writer on blocks |
| **Summarization loss** | Abstractive compact / recursive \(M_i\) drops the constraint needed on turn 90 | Silent quality drop | Memory tool / blocks **before** compact; custom `instructions`; `pause_after_compaction`; keep episode pointers (HiGMem) |
| **Namespace leak** | Prefix `asearch(("tenant",))`; shared Pinecone ns + post-filter; default ns `""` | Cross-customer disclosure | Full namespace; Pinecone one-ns-per-tenant; Mem0 `filters` |
| **Other-user retrieval** | Missing `user_id` pre-filter; ANN neighbors; shared Letta `human` block; two characters in one Mem0 store | Privacy incident | Pre-filter; Mem0 paper prompt even warns not to confuse character names with users |
| **Memory poisoning** | User/doc/webpage causes a **write** of a false belief | Cross-session (Hidden in Memory: add up to **99.8%** GPT-5.5 / **95%** Kimi-K2.6; among retrievals, attacker-intended **actions 60–89%**) | Origin tags; no auto-promote web→semantic |
| **Sleeper / L3** | Benign-looking record; trigger later (MemPoison L3; 1,227-case bench) | Delayed; write-time filters miss | Read-time scoring; ablation |
| **eTAMP** | One malicious page; no memory API access | Cross-site; ASR up to **32.5%** GPT-5-mini, **23.4%** GPT-5.2, **19.5%** GPT-OSS-120B; **×8** under UI frustration | Don’t auto-write from trajectories |
| **Write/read race** | Query before graph construction finishes | Empty or wrong recall | Watermark; episode fallback |
| **Unbounded growth** | ADD-only + no TTL + full checkpoints | Cost, p99, HNSW neighborhood pollution | TTL, shallow checkpoints, GC |
| **Soft-delete “erasure”** | HNSW flag, trace TTL, backup | Compliance finding | VACUUM/compaction + crypto-shred |
| **Judge overfitting** | LoCoMo/DMR as procurement truth | Buying the wrong system (DMR saturates) | LongMemEval_M / BEAM 10M; hold-out traces; poison + identity tests |
| **Embedder drift** | New embed model, old index | Silent recall collapse | Pin `embedding_model` + `index_version` in metadata |

---

## 6. Enterprise System Design Scenarios

### 6.1 Trade-off matrix

| Requirement | Prefer | Avoid | Why |
| --- | --- | --- | --- |
| Sub-200 ms **retrieve**, temporal facts, CRM+chat | Zep/Graphiti | Full-context; STM-only | Bi-temporal + constructor; vendor retrieve 155–162 ms |
| Ship conversational personalization this week | Mem0 platform v3 | Rolling your own extract+Neo4j | Hybrid + SKU; OSS if you accept score gap **and** no graph |
| Agent must **self-edit** persona + ADE | Letta blocks + sleep-time | Vector-only RAG as “memory” | Virtual context; sleep-time owns writes |
| Multi-tenant LangGraph app, HITL, time-travel | Checkpointer **+** Store | `ConversationBufferMemory` | Deprecated; thread vs user scopes differ (05) |
| Cross-session files Claude already understands | `memory_20250818` + encrypted bucket | Putting PII in the window | Survives compaction (02) |
| Point-in-time “what did we believe on date D?” | Graphiti invalidation + episode provenance | Physical DELETE of facts | Need both PIT **and** Art. 17 hard-delete |
| Lowest generator $ at 26k-turn histories | Extractive memory (~2–7k tok) | Stuffing 26k–115k | Paper: 91% p95 cut vs full-context |
| Strict erasure | Per-user crypto keys or per-user indexes / namespaces | Shared HNSW + metadata filter only | Ghost Vectors |
| Poison-resistant writes | Origin-bound writes; no auto-write from tools/web | LLM “is this a good memory?” as sole gate | MemPoison L2/L3 |

### 6.2 Scenario A — Multi-month B2C copilot memory

**Problem.** 10M MAU assistant; users return weeks later; preferences drift; PII; cost cap.

**Topology.** Hot: 2–4k tok profile card (Letta-style `human`/`persona` blocks **or** pinned top Mem0 facts) + last ~8k tok `trim_messages` window. Warm: retrieve k≤20, **constructor ≤4k tok** (not “top-k unlimited”). Write: **async** extract; never block TTFT. Cold: episodes 30–90 d TTL; traces 7–30 d. Consolidation: nightly sleep-time/dreaming, not per turn.

**Namespaces.** Pinecone (or pgvector) **namespace = tenant or user-shard**; metadata `user_id` + `kind ∈ {fact, preference, episode}`. LangGraph: `thread_id` = session; Store `("u", user_id, "facts")`.

**NFR.** Retrieve p95 <300 ms **[target, measure]**; never wait on consolidation. **[inferred]** memory SKU ~$4–5 / 1k turns (Mem0 Starter/Pro) vs ~$11–21 generation at 2–8k in on Sonnet 5 (01 prices). Stuff-all 26k → ~$59 / 1k — do not.

**Resilience.** Fail open to STM + cached card. Ingest watermark if using a graph. Single writer on the profile card.

**Security.** Per-user encryption keys; Art. 17 fan-out tested quarterly including HNSW compaction; no training on memories; Vec2Text ⇒ embeddings in the same DLP class as chat logs.

**Failure.** Stale preferences → dreaming daily. Identity mix-up → never share `human` blocks across users. Poisoning → do not auto-write from browsing tools.

### 6.3 Scenario B — Call-center episodic after-call summaries

**Problem.** Voice/chat; ACW time; returning caller in seconds-to-months; HIPAA/PCI; supervisors must audit.

**Topology (matches OpenSearch agentic memory + AWS Connect + AgentCall-class products).** During the call, STM = transcript window + (optional) one-paragraph **pre-call brief**. After hangup, **three parallel extractors** from the same transcript ([OpenSearch blog](https://opensearch.org/blog/personalizing-your-contact-center-agent-using-opensearch-agentic-memory/)):

1. **Semantic** — facts/life events (retirement target, account product).
2. **Preference** — how they want to be served (callback before noon).
3. **Summary** — session episode: discussed / decided / open tasks.

AWS Connect generative **post-contact summaries** for ACW (voice, chat, email); granular PII redaction is **not** supported for summaries — identified PII becomes `[PII]` ([Connect](https://docs.aws.amazon.com/connect/latest/adminguide/view-generative-ai-contact-summaries.html)). AgentCall-class: every memory row carries `sourceCallId` + **verbatim quote**; inbound loads a one-paragraph brief ([AgentCall](https://agentcall.co/docs/memory)). Retell: map post-call extraction → contact fields with **merge/accumulate**; `interaction_history` capped at **10** recent interactions ([Retell](https://docs.retellai.com/integrations/build-contact-memory)).

**Write path is after-call, not mid-turn.** Ordering: transcript webhook first; extract a few seconds later (`call.report.ready`). If the agent already acted on the live transcript, attach by `callId` — eventual consistency is **expected**. CRM (account balance, address) stays **authoritative**; memory is *commitments and preferences*, not ledger.

**Read path.** On inbound: authenticate → load brief (constructor, not full history) in parallel with CRM. Do not dump 90 days of transcripts into the greeting.

**NFR.** Extract SLA: seconds, not the live-call budget. Brief: **one paragraph**. Store episodes with TTL matching retention policy (PCI: do not put PAN in memory at all).

**Security.** BAA; redact before embed; tenant = brand/queue + `contact_id`; supervisors edit/delete with audit actor (`extractor` vs `owner`). Art. 17 fan-out includes recordings, summaries, vectors, CRM notes.

**Failure.** Summarization loss of a promise (“we will refund $40”) → require **quote-backed** facts (AgentCall pattern) and keep the transcript as the non-lossy episode. Stale policy vs last-call promise → bi-temporal facts + human confirm on DELETE-class updates. Namespace leak across brands is a regulator event.

**Capacity sketch [inferred].** 1k agents × 80 calls/day × 3 extracts (fact/pref/summary) × ~1k tok extract in / ~200 tok out on Haiku-class (01: Haiku 4.5 **$1 / $5** per MTok) ≈ 240k extract calls/day × ~$0.002 = **~$480/day** extract LLM **before** embeddings. Embed 240k × 400 tok × $0.02/1M ≈ **$1.92/day**. Generation of the live call still dominates. After-call extract is the memory bill; do it **once per call**, not per turn.

### 6.4 Long-running coding / ops agent (memory, not packing)

Triggers and offload thresholds: **02**. Memory product: Anthropic **clear tool exhaust** (vendor −84% tokens) → compact at the 02 trigger with `instructions` that pin decisions, IDs, open TODOs → **memory tool** for lessons that must survive summarization. Optional Letta MemFS (git history of lessons) or Cognee-class graph for repo semantics. Sleep-time overnight over the repo (`c → c'`): paper ~5× less test-time reasoning on stateful math; analogous for “what broke last time.” Compaction **will** cache-bust; schedule compact at session boundaries when possible. Never store secrets in `/memories`. Treat `CLAUDE.md` as procedural memory: trust UI, pin versions (Anthropic 2026 containment).

### 6.5 Decision checklist (Principal Architect)

1. Split **STM** (thread, tokens, compaction — 02/05) from **LTM** (user, facts) from **episodes** (provenance).
2. Split **semantic** from **episodic**; keep pointers. You need episodes for audit, unlearning, and citation.
3. Put **authz on the retrieve path** before ANN. Poisoning and tenant leak are the same bug class: untrusted text became trusted state.
4. Budget **constructor tokens**, not “top-k.” Zep 1.6k vs Mem0 ~7k vs 115k full-context is the product.
5. Run consolidation **off the user path**. Pay sleep-time’s 5× only if `c` is stable and queries amortize.
6. Prove Art. 17 on a **staging clone** of prod indexes, including HNSW compaction, traces, and backups.
7. Do not procure on DMR/LoCoMo alone; add LongMemEval_M / BEAM 10M and **your** poison + identity tests.
8. Compressors (LLMLingua, compact) are **not** memory; they are STM hygiene. Persist before you drop.

---

## Interview bullets (numbers)

1. **CoALA:** LLM is not memory; working memory is compiled into the prompt; LTM is retrieval (read) + learning (write). Four stores: working / episodic / semantic / procedural.
2. **Mem0 paper LOCOMO:** 1,764 retrieved tok, search p50/p95 **0.148 / 0.200 s**, total p95 **1.440 s** vs full-context 26,031 tok / **17.117 s** / J 72.90 vs 66.88 — **91%** p95 cut. Extract: last **m=10** msgs, top **s=10** neighbors, ADD/UPDATE/DELETE/NOOP. v3 is **ADD-only** + hybrid retrieve.
3. **Zep paper LongMemEval_S:** gpt-4o **71.2%** vs 60.2% full-context, **1.6k** vs **115k** tok, **2.58 s** vs 28.9 s. DMR 94.8 vs MemGPT 93.4 (too easy). NER context **n=4** messages. Vendor 2026: LoCoMo **94.7% @ 155 ms** — retrieve SLO, not chat SLO.
4. **Letta:** blocks rec. **<50k chars**, **<20**/agent; files **5 MB** / **<100**; archival **~300 tok**/passage unlimited. Shared blocks **last-write-wins**. Sleep-time default every **5** steps; paper **~5×** less test-time compute, **2.5×** cheaper when 10 queries share `c'`. API **$20/mo + $0.10/agent + $0.00015/s**.
5. **Generative Agents:** score = recency (**0.995**/sandbox-hour since **access**) + importance (1–10) + cosine; α=1 after min-max. Reflection ~**150** importance points. Code weights `[0.5, 3, 2]` ≠ paper.
6. **A-Mem:** GPT-4o LoCoMo **1,216** tok vs **16,910**; Mem0 re-run **2,520** tok / J 48.38%. Evolution = write amplification on neighbors. HiGMem adversarial F1 **0.54 → 0.78** with 10× fewer turns.
7. **LLMLingua 20×** (GSM8K −1.5 EM); LongLLMLingua **~4×** +21.4% NQ; LLMLingua-2 **2–5×**, compressor **3–6×** faster. Not a memory store. Anthropic tool-clear vendor **−84%** tokens; compact+memory **+39%** task (vendor eval; triggers in **02**).
8. **Vec2Text:** **92%** exact recovery of 32-token texts. Ghost Vectors 2026: **25.5%** exact names from **soft-deleted** embeddings. Art. 17 = fan-out + physical purge, not API 200.
9. **Pinecone:** 1 ns/tenant; query cost **1 RU/GB of that namespace**. Shared 100 GB + metadata filter = **100 RUs**. `$in` max **10,000**. LangGraph Store prefix search is a leak if you omit `user_id` (API in **05**).
10. **Poisoning:** Hidden in Memory add **99.8% / 95%**; actions **60–89%**. eTAMP **32.5% / 23.4% / 19.5%**, **×8** under frustration. MemPoison L1 ≠ L2/L3. Write-time filters are not enough.
11. **`$ / 1k turns` [inferred, Sonnet 5 from 01]:** constructor 2–8k in → **~$11–21** generation; full 26k → **~$59**; 115k → **~$237**. Mem0 Starter retrieval meter **~$3.80 / 1k**. Embed **$0.02/1M** (01) is noise vs extract LLM.
12. **Call center:** after-call semantic + preference + summary in parallel; inbound = one-paragraph brief + quote-backed facts; CRM remains system of record; Connect summaries cannot do granular PII (all `[PII]`).

---

## Gaps (do not fabricate)

- No vendor **p99** for Mem0 / Zep / Letta / PostgresStore search; 155–162 ms is retrieve-only marketing, not e2e chat.
- Mem0 **platform v3** scores (LoCoMo 92.5, LME 94.4) are **not** the 2025 paper (J 66.88); OSS ≠ platform; harnesses disagree with Zep’s 94.7 vs paper 71.2 LME.
- Zep **credit-per-add** is not fully specified on the public Flex table — do not invent `$ / session`.
- Anthropic compaction / memory-tool **p50** unpublished; +29/+39/+84 are **vendor evals**.
- ChatGPT Dreaming V3 **9.4% → 75.1%** time-sensitive accuracy appears in secondary writeups — confirm against OpenAI’s own post before quoting in a design review.
- No public **per-namespace** Pinecone cardinality cap that is stable across SKUs; free-tier ns counts move (20→100 in 2026 release notes) — check current docs.
- LLMLingua 20× is GSM8K/ICL, not multi-month copilot memory; no production p95 for LLMLingua-2 in front of Claude/GPT agent loops.
- Hippocampal systems (HippoRAG, EM-LLM, REMem) report QA/recollection numbers, **not** multi-tenant write SLOs or Art. 17 procedures.
- No published production **poisoning ASR** for Mem0/Zep/Letta themselves — cite MemPoison/eTAMP/Hidden in Memory as threat models, not vendor scores.
- Store vs checkpointer **orphan writes** on node failure: documented as an engineering obligation, not a LangGraph bug with a measured rate.

---

## Sources

1. https://arxiv.org/abs/2309.02427 — CoALA, Sumers, Yao, Narasimhan, Griffiths (TMLR 2024)
2. https://arxiv.org/html/2309.02427v3 — CoALA HTML: working vs episodic vs semantic vs procedural; RAG as read-only semantic
3. https://arxiv.org/pdf/2310.08560 — Packer et al., MemGPT: Towards LLMs as Operating Systems
4. https://docs.letta.com/v1-sdk/memory/context-hierarchy — blocks <50k chars / <20; files 5MB / <100; archival 300 tok unlimited
5. https://docs.letta.com/guides/core-concepts/memory/memory-blocks/index.md — always-visible blocks; last-write-wins replace
6. https://docs.letta.com/concepts/memfs/index.md — MemFS git-backed memory; dreaming worktrees
7. https://docs.letta.com/letta-agent/pricing/ — $20/mo + $0.10/agent + $0.00015/s tools
8. https://arxiv.org/abs/2504.13171 — Sleep-time Compute: ~5× test-time, +13/+18%, 2.5× amortize over 10 queries
9. https://www.letta.com/blog/sleep-time-compute/ — sleep-time agents productization
10. https://github.com/letta-ai/letta/blob/bb52a890/letta/constants.py — DEFAULT_MAX_MESSAGE_BUFFER_LENGTH=30
11. https://docs.letta.com/v1-sdk/memory/archival-memory/index.md — archival vs conversation search
12. https://arxiv.org/html/2504.19413 — Mem0 paper: m=10, s=10, Table 1–2, 7k/14k/600k construction
13. https://arxiv.org/abs/2504.19413 — Mem0 abs
14. https://docs.mem0.ai/migration/platform-v2-to-v3 — ADD-only v3; hybrid retrieval; expiration
15. https://docs.mem0.ai/migration/oss-v2-to-v3 — OSS graph removed; filters dict; entity boost
16. https://mem0.ai/blog/ai-memory-benchmarks-in-2026 — LoCoMo 92.5 / LME 94.4 / BEAM; token ~7k
17. https://mem0.ai/pricing — Hobby/Starter $19/Pro $249 meters
18. https://arxiv.org/html/2501.13956v1 — Zep/Graphiti: n=4, label propagation, DMR 94.8 vs 93.4, LME table
19. https://arxiv.org/abs/2501.13956 — Zep abs
20. https://getzep-graphiti.mintlify.app/concepts/temporal-model — valid_at/invalid_at vs created_at/expired_at
21. https://github.com/getzep/graphiti — Graphiti README: invalidate not delete; vs GraphRAG
22. https://blog.getzep.com/state-of-the-art-agent-memory/ — 71.2 vs 60.2; 1.6k vs 115k; chose LME_S not _M
23. https://www.getzep.com/ai-agents/how-to-test-agent-memory/ — vendor LoCoMo 94.7% @ 155 ms; LME 90.2% @ 162 ms
24. https://www.getzep.com/pricing/ — Flex $125 / Flex Plus $375
25. https://docs.langchain.com/oss/python/langgraph/stores — prefix search leak; limit=10 silent truncate; semantic off by default
26. https://docs.langchain.com/oss/python/langgraph/add-memory — STM vs LTM recipes
27. https://docs.langchain.com/oss/python/langchain/long-term-memory — namespace/key JSON product
28. https://docs.langchain.com/oss/python/langchain/short-term-memory — thread-scoped STM
29. https://reference.langchain.com/python/langgraph.store.postgres/base/PostgresStore — pgvector index; start_ttl_sweeper
30. https://reference.langchain.com/python/langchain-classic/memory/summary/ConversationSummaryMemory — deprecated 0.3.1 → create_agent
31. https://docs.pinecone.io/guides/index-data/implement-multitenancy — 1 ns/tenant; 1 RU/GB; metadata filter scans all
32. https://docs.pinecone.io/guides/search/filter-by-metadata — $in/$nin 10,000 cap
33. https://sdk.pinecone.io/python/how-to/vectors/namespaces.html — queries never cross namespaces
34. https://www.pinecone.io/learn/series/vector-databases-in-production-for-busy-engineers/vector-database-multi-tenancy/ — ns vs metadata tenancy
35. https://arxiv.org/pdf/2310.05736 — LLMLingua up to 20×, −1.5 GSM8K
36. https://www.microsoft.com/en-us/research/blog/llmlingua-innovating-llm-efficiency-with-prompt-compression/ — LLMLingua MSR blog
37. https://llmlingua.com/longllmlingua.html — LongLLMLingua 4×, +21.4% NQ, 94% LooGLE cost, 1.4–2.6× latency
38. https://arxiv.org/html/2403.12968 — LLMLingua-2 2–5×, 3–6× faster compressor, 1.6–2.9× e2e
39. https://github.com/microsoft/LLMLingua — PromptCompressor; LangChain compressor
40. https://platform.claude.com/docs/en/build-with-claude/compaction — compact_20260112; cache breakpoint on compaction block
41. https://platform.claude.com/docs/en/build-with-claude/context-editing — clear_tool_uses_20250919
42. https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool — memory_20250818; client storage; path traversal
43. https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools — compaction vs tool-clear vs memory
44. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents — attention budget; compact lossy
45. https://developers.openai.com/api/docs/guides/compaction — server compact_threshold; /responses/compact; store=false ZDR
46. https://developers.openai.com/api/docs/guides/conversation-state — Conversations API vs previous_response_id; not ChatGPT memory
47. https://developers.openai.com/api/reference/resources/responses/methods/compact — compact endpoint
48. https://openai.com/index/chatgpt-memory-dreaming/ — Dreaming V3 (2026-06-04)
49. https://openai.com/index/memory-and-new-controls-for-chatgpt/ — saved memories + chat history (2025)
50. https://arxiv.org/abs/2304.03442 — Generative Agents Park et al. memory stream
51. https://dl.acm.org/doi/10.1145/3586183.3606763 — UIST 2023
52. https://github.com/joonspk-research/generative_agents/blob/main/reverie/backend_server/persona/cognitive_modules/retrieve.py — code weights 0.5/3/2
53. https://arxiv.org/html/2502.12110 — A-Mem: Zettelkasten, evolution, 1,216 tok
54. https://proceedings.neurips.cc/paper_files/paper/2025/file/19909c36f51abc4856b4560aff3d36d6-Paper-Conference.pdf — A-Mem NeurIPS 2025
55. https://github.com/agiresearch/a-mem/ — A-Mem production code (ChromaDB)
56. https://huggingface.co/papers/2305.10250 — MemoryBank Ebbinghaus R=e^{-t/S}
57. https://ojs.aaai.org/index.php/AAAI/article/view/29946 — MemoryBank AAAI 2024
58. https://arxiv.org/html/2308.15022v3 — Recursive summarization M_i = LLM(session_i, M_{i-1})
59. https://arxiv.org/html/2604.18349v2 — HiGMem event/turn; adversarial F1 0.54→0.78
60. https://aclanthology.org/2026.findings-acl.1690/ — HiGMem ACL Findings 2026
61. https://arxiv.org/pdf/2405.14831 — HippoRAG: PPR hippocampal index; 10–20× cheaper than IRCoT
62. https://proceedings.iclr.cc/paper_files/paper/2025/file/c05144b635df16ac9bbf8246bbbd55ca-Paper-Conference.pdf — EM-LLM surprise segmentation; 10M tokens
63. https://arxiv.org/html/2602.13530 — REMem episodic graph; +3.4/+13.4 vs Mem0/HippoRAG 2
64. https://arxiv.org/html/2604.01707v3 — Memory vs RAG unified survey 2026
65. https://atlan.com/know/ai-memory-system-vs-rag/ — RAG stateless vs memory write path
66. https://machinelearningmastery.com/retrieval-vs-memory-in-agentic-ai-systems/ — retrieval vs memory table
67. https://arxiv.org/html/2410.10813v1 — LongMemEval (ICLR 2025)
68. https://arxiv.org/abs/2402.17753 — LoCoMo
69. https://aclanthology.org/2023.emnlp-main.765/ — Vec2Text Morris et al. 92% / names in clinical notes
70. https://github.com/vec2text/vec2text — inversion library
71. https://doi.org/10.1145/3673791.3698414 — Vec2Text threat to dense retrieval (CIKM 2024)
72. https://arxiv.org/html/2507.07700 — Vec2Text reproducibility
73. https://arxiv.org/html/2606.18497v1 — Ghost Vectors: soft-deleted HNSW still reconstructible; 25.5% names
74. https://dreaming.press/posts/right-to-be-forgotten-vector-database.html — Art. 17 fan-out; one-month clock
75. https://arxiv.org/html/2605.15338 — Hidden in Memory sleeper poisoning 99.8%/95%; actions 60–89%
76. https://arxiv.org/html/2607.14651 — MemPoison 1,227 cases; L1/L2/L3; write-time fails L2/L3
77. https://arxiv.org/pdf/2604.02623 — eTAMP 32.5/23.4/19.5%; ×8 frustration
78. https://opensearch.org/blog/personalizing-your-contact-center-agent-using-opensearch-agentic-memory/ — semantic + preference + summary after-call
79. https://docs.aws.amazon.com/connect/latest/adminguide/view-generative-ai-contact-summaries.html — Connect ACS; PII → [PII]
80. https://agentcall.co/docs/memory — quote-backed call memory; call.report.ready
81. https://docs.retellai.com/integrations/build-contact-memory — post-call merge; 10-interaction cap
82. https://www.letta.com/blog/memory-blocks/ — blocks compiled from DB; sleep-time learned context
83. https://github.com/letta-ai/skills/blob/HEAD/letta/letta-api-client/sleeptime.md — sleeptime_agent_frequency=5
84. https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/authorization — MCP OAuth 2.1
85. https://www.anthropic.com/engineering/how-we-contain-claude — persistent memory poisoning as classifier problem
86. https://research.checkpoint.com/2026/rce-and-api-token-exfiltration-through-claude-code-project-files-cve-2025-59536/ — CLAUDE.md / mcp.json as durable memory
87. https://nvd.nist.gov/vuln/detail/cve-2026-21852 — related Claude Code CVE
88. https://github.com/anthropics/claude-code/issues/21674 — global CLAUDE.md injection
89. https://docs.langchain.com/oss/python/deepagents/context-engineering — FS offload 20k; summarize 85% (triggers: 02)
90. https://reference.langchain.com/python/langchain/agents/middleware/summarization/SummarizationMiddleware — persistent summary rewrite
91. https://blog.getzep.com/beyond-static-knowledge-graphs/ — Graphiti bi-temporal edge fields; LLM invalidation prompt
92. https://trust.getzep.com — Zep Trust Center (confirm SOC2 SKU)
93. https://github.com/langchain-ai/langgraph/issues/4343 — PostgresStore fields vs text_fields embed bug (fixed 2.0.21)
94. https://www.aipolicydesk.com/blog/ai-agent-persistent-memory-gdpr-compliance-2026 — persistent memory GDPR 2026
95. https://www.anthropic.com/news/context-management — vendor +29% context editing / +39% +memory eval
96. https://community.openai.com/t/will-memory-capabilities-come-to-the-api/934907 — no public ChatGPT Memory API
97. https://github.com/xiaowu0162/LongMemEval — 115k / 1.5M settings
98. https://ojs.aaai.org/index.php/AAAI/article/download/40557/44518 — GSW hippocampal Operator/Reconciler (AAAI)
99. https://docs.langchain.com/oss/python/langgraph/persistence — checkpointer vs Store isolation (API in 05)
100. https://neoneye.github.io/agent-memory-atlas/systems/memorybank/ — summaries sit outside Ebbinghaus draw
101. https://help.openai.com/en/articles/8590148-memory-faq — ChatGPT memory product FAQ
102. https://llmlingua.com/llmlingua.html — LLMLingua project page
103. https://github.com/letta-ai/sleep-time-compute — sleep-time paper code
104. https://docs.aws.amazon.com/bedrock/latest/userguide/claude-messages-compaction.html — Bedrock compaction iterations billing
105. https://arxiv.org/abs/2005.11401 — Lewis et al. RAG (2020); CoALA’s read-only semantic memory
106. https://arxiv.org/abs/2401.18059 — RAPTOR recursive summary trees (Sarthi et al.)

**Source count:** 106 listed URLs. Intra-roadmap: list prices **01**; compact triggers **02**; Store/checkpointer API **05**. Claims in §§1–6 are tied to papers, vendor docs, or named blogs dated on or before 2026-09-23. Vendor head-to-heads (Zep vs Mem0 leaderboards) are labeled marketing where used.
