# Research: AI Agents — Memory, State & Consistency

**Date researched**: 2026-09-30
**Sources consulted**: 18
**Scope note**: Agent-state layer only — short/long-term memory, checkpointers/stores, consistency rules, compaction vs sliding window, CoALA memory tiers. **Vector DB internals** (indexing, HNSW, ANN trade-offs) are the next roadmap topic; retrieval is covered here only as an agent-facing API (scope, metadata filters, what enters the prompt).

## 1. System Topology & Mechanics

### State vs memory (control plane of continuity)

The System Design Newsletter’s production framing separates three concerns that frameworks often conflate ([System Design Newsletter — AI Agent Memory](https://newsletter.systemdesign.one/p/ai-agent-memory)):

| Concern | Owns | Lifetime | Update rule |
| --- | --- | --- | --- |
| **State** | Current workflow: step, plan, active constraints, what is done/next | Task / thread | React **fast** — update on every instruction |
| **Memory** | Preferences, past decisions, reusable facts across tasks | Cross-session | Learn **slowly** — write after change looks steady |
| **External systems (SoR)** | Prices, inventory, bookings, live APIs | Authoritative | Always win conflicts with stored memory |

Stateless agents (one-shot translate/summarize) need no persistence. Stateful agents answer three questions per decision: *what step am I on, what is already done, what next?* — then load the latest snapshot before each step ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)).

**Four-layer reference architecture** ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)):

1. **Agent brain** — LLM plans, parses intent, picks next action from state + retrieved memory.
2. **State layer** — workflow progress, short-term overrides, rollback targets on constraint change.
3. **Memory layer** — short-term (this task) + long-term (preferences/habits) outside the prompt by default.
4. **External systems** — systems of record; memory may suggest, SoR decides.

Anthropic’s agent loop requires gaining **ground truth from the environment at each step** (tool results, code execution) to assess progress — the same “SoR wins” rule in different language ([Anthropic — Building effective agents](https://www.anthropic.com/research/building-effective-agents)).

### LangGraph: checkpointer (short-term) vs Store (long-term)

LangGraph’s official persistence model is explicitly dual ([LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence); [Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers); [Stores](https://docs.langchain.com/oss/python/langgraph/stores)):

| | **Checkpointer** | **Store** |
| --- | --- | --- |
| Persists | Graph state snapshots (`StateSnapshot`) | Application-defined key-value items |
| Scope | Single `thread_id` | Across threads (e.g. `(user_id, "memories")`) |
| Memory type | Short-term / thread-scoped | Long-term / cross-thread |
| Use for | Continuity, HITL interrupts, time travel, fault tolerance | Preferences, facts, shared knowledge |
| Access | Config `{"configurable": {"thread_id": "..."}}` | Read/write from nodes via injected store / `Runtime` |

Production backends: `PostgresSaver` / `AsyncPostgresSaver`, `SqliteSaver` (dev); in-memory `InMemorySaver` / `MemorySaver` **does not survive process restart** ([LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)). Postgres `thread_id` column limit: keep under **255** characters ([LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)).

**Checkpoint mechanics** ([LangGraph Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)):

- A checkpoint is written at each **super-step** boundary (all nodes scheduled for that tick).
- Per-node **pending writes** go to `checkpoint_writes` as tasks complete; on resume after a peer-node failure, successful nodes are **not** re-run.
- `StateSnapshot` exposes `values`, `next`, `config` (`thread_id`, `checkpoint_ns`, `checkpoint_id`), `metadata`, `created_at`, `parent_config`, `tasks`.
- Time travel / fork: resume or branch from any prior `checkpoint_id` via `get_state` / `get_state_history`.
- Subgraphs get their own checkpoint namespace — parent may not see child updates immediately; use Store for cross-graph durable data ([LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)).

LinkedIn’s internal SQL Bot (hierarchical multi-agent on LangGraph) relies on this checkpoint layer to pause, inspect, and resume long-running query workflows ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory); [LangChain — Is LangGraph used in production?](https://www.langchain.com/blog/is-langgraph-used-in-production)).

### Memory taxonomy in agent systems (CoALA + industry mapping)

CoALA (Sumers, Yao, Narasimhan, Griffiths) defines four memory tiers for language agents ([CoALA arXiv:2309.02427](https://arxiv.org/abs/2309.02427); restated in [LangChain — Memory for agents](https://www.langchain.com/blog/memory-for-agents)):

| Tier | Question | Agent implementation (today) |
| --- | --- | --- |
| **Working** | What am I thinking about *now*? | Context window / thread state / checkpointer `messages` |
| **Episodic** | What happened *to me*? | Past trajectories, few-shot episode examples, session logs |
| **Semantic** | What do I *know*? | Extracted facts/preferences in Store / Mem0 / RAG corpora |
| **Procedural** | What do I *know how to do*? | LLM weights + agent code/prompts/tools; rare: self-updating system prompt |

**Newsletter shorthand** (short-term / long-term / external) maps onto CoALA as: short-term ≈ working; long-term ≈ semantic (+ some episodic); external ≈ read-only semantic SoR ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)).

**Mem0** (production memory layer): sits between app and model — `add` extracts/consolidates facts; `search` returns ranked memories for the next prompt. Pipeline: context lookup → fact extraction → dedup/embed → entity extraction; search blends semantic, keyword, entity, and temporal signals ([Mem0 how-it-works](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/how-it-works.mdx); [Mem0 paper](https://arxiv.org/abs/2504.19413)). API reality check: docs discuss working/factual/episodic/semantic types, but as of 2026-03 the SDK’s `MemoryType` enum only **actively wires `procedural_memory`**; SEMANTIC/EPISODIC enum values are not used — scoping via `user_id` / `run_id` / `agent_id` is the practical differentiator ([Mem0 issue #3455](https://github.com/mem0ai/mem0/issues/3455); [Mem0 blog comparison](https://mem0.ai/blog/semantic-vs-episodic-vs-procedural-memory-in-ai-agents-a-complete-comparison)).

### Hot-path vs background memory writes

LangChain frames two update topologies ([LangChain — Memory for agents](https://www.langchain.com/blog/memory-for-agents); echoed in [System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)):

| Mode | When | Pros | Cons |
| --- | --- | --- | --- |
| **Hot path** | Agent decides to remember (often via tool) before replying | Immediate consistency | Adds latency; couples memory logic into agent turn |
| **Background** | Separate process during/after conversation | No user-facing latency; “learn slowly” | Memory not immediately available; needs kickoff/TTL logic |

### Consistency under preference change

Canonical rule: **react fast with state, learn slowly with memory** ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)). On correction (“window seat this time”):

1. Update state constraints immediately; roll back only steps that depended on the old constraint.
2. Tag change as one-time / task-specific / lasting (linguistic cues like “always” / “going forward”).
3. Write long-term memory only after the change looks steady.
4. If airline SoR has no window seats → report unavailability; **do not** delete the preference.

Retrieval is scoped by metadata (topic, duration, confidence) so booking a flight pulls flight+budget memory, not hotel memory ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)).

### Context assembly (what fights for the window)

Mental budget ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory); [Anthropic — Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)):

```
Context size ≈ instructions + state + retrieved memory + tool schemas + tool outputs + user input
```

Anthropic: context is a finite **attention budget**; transformers create *n²* pairwise token relationships; longer contexts → **context rot** (needle-in-haystack degradation) ([Anthropic — Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)). Tool definitions alone in Claude Code historically consumed ~**134,000** tokens; Tool Search (lazy schema load) cut overhead **~85%**, raised usable context ~**122,800 → 191,300** tokens, and improved MCP tool-use accuracy for Claude Opus 4 from **49% → 74%** ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory) citing Anthropic Advanced tool use).

## 2. Token Economics & NFR Metrics

### Context growth and retrieval token budgets (Mem0 / LOCOMO)

LOCOMO conversations average ~**600** dialogues / ~**26,000** tokens; ~**200** questions per conversation ([Mem0 paper](https://arxiv.org/abs/2504.19413)). Published Table 2 (gpt-4o-mini judge, cl100k_base token accounting):

| Method | Context / memory tokens (avg) | Total p50 (s) | Total p95 (s) | Overall J |
| --- | --- | --- | --- | --- |
| Full-context | **26,031** | 9.870 | **17.117** | **72.90%** ±0.19 |
| Best RAG (k=2, 256) | chunked | 0.802 | 1.907 | 60.97% ±0.20 |
| OpenAI memory | 4,437 | 0.466 | 0.889 | 52.90% ±0.14 |
| Zep | 3,911 | 1.292 | 2.926 | 65.99% ±0.16 |
| LangMem (hot path) | 127 | 18.53 | **60.40** | 58.10% ±0.21 |
| **Mem0** | **1,764** | **0.708** | **1.440** | 66.88% ±0.15 |
| Mem0ᵍ (graph) | 3,616 | 1.091 | 2.590 | 68.44% ±0.17 |

Derived savings vs full-context (same table / paper claims):

- **Token context reduction**: Mem0 **1,764 / 26,031 ≈ 93.2%** fewer prompt tokens than full history (paper: “saves more than **90%** token cost”).
- **p95 total latency**: Mem0 **1.440 / 17.117 ≈ 91.6%** lower (paper abstract: **91%** lower p95; §4.3 also states ~**92%** reduction for Mem0 and ~**85%** for Mem0ᵍ).
- **Quality trade**: full-context still leads on J (**72.9%** vs Mem0 **66.9%** / Mem0ᵍ **68.4%**); Mem0 is **+26% relative** vs OpenAI memory on LLM-as-judge (abstract; OpenAI J 52.9% → Mem0 66.9% is ~+26% relative).
- Search p95: Mem0 **0.200s** vs LangMem **59.82s** (interactive-hostile).
- Store footprint (construction): Mem0 ~**7k** tokens/conversation; Mem0ᵍ ~**14k**; Zep graph **>600k** tokens with multi-hour async readiness lag ([Mem0 paper §4.5](https://arxiv.org/abs/2504.19413)).

> Cost in **$ per 1k executions** is not published for these runs — depends on model price × (prompt + completion) tokens. [inferred] Using Mem0’s ~1.8k vs 26k prompt tokens implies roughly **order-of-magnitude** lower input-token spend per query at equal model rates, before extraction/update LLM calls on the write path.

### Summarization / compaction vs sliding window

| Strategy | Mechanism | Token / quality effect | Source |
| --- | --- | --- | --- |
| **Sliding window** | Keep system + last N turns; drop older | Cheapest; loses early goals/constraints | [Victor Dibia context engineering](https://newsletter.victordibia.com/p/context-engineering-101-how-agents); community FR for Claude Code |
| **Head+tail** | Preserve task head + recent tail; drop middle | Better goal retention than pure slide | Same |
| **Tool-result clearing** | Drop stale re-fetchable tool payloads | Lightest safe compaction | [Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents); [Claude cookbook](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools) |
| **Summarization / compaction** | LLM summarizes older history into a block | Lossy but continuous; costs one summarization call | Anthropic Claude Code + API |
| **External memory** | Persist notes/facts outside window; retrieve JIT | Moves cost off the prompt; retrieval latency | Anthropic “structured note-taking”; Mem0/Store |

**Anthropic server-side compaction** (`compact_20260112`) ([Claude Compaction docs](https://platform.claude.com/docs/en/build-with-claude/compaction)):

- Default trigger: `input_tokens` **150,000** (minimum **50,000**).
- API emits a `compaction` content block; subsequent requests drop everything before that block.
- Custom `instructions` **replace** (do not append) the default summarizer prompt.
- Claude Code client pattern: summarize → keep critical decisions + **five most recently accessed files** ([Anthropic — Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

**Sub-agent isolation economics**: sub-agents may burn “tens of thousands” of tokens exploring, then return **~1,000–2,000** token summaries to the lead — explicit token firewall between working sets ([Anthropic — Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

### Checkpoint storage growth (published sizes)

Default LangGraph checkpointers write a **full channel snapshot every super-step**. For append-only `messages`/`files`, storage grows **O(N²)**. LangChain Deep Agents benchmark (mocked coding agent, `InMemorySaver`) ([LangChain — Delta Channels](https://www.langchain.com/blog/delta-channels-evolving-agent-runtime)):

| Turns | Full-snapshot storage | DeltaChannel storage | Reduction |
| --- | --- | --- | --- |
| 200 (heavy multi-file) | **5.3 GB** | **129 MB** | **~41×** |
| 500 (light coding) | **~4 GB** | **<110 MB** | up to **~41×** |

`DeltaChannel` (LangGraph **1.2**, beta): store per-step deltas; full snapshot every `snapshot_frequency` (API default **1000**; Deep Agents uses **50**). Caps resume replay depth at K steps. Deep Agents v0.6 defaults `messages` and `files` to delta-backed ([LangChain — Delta Channels](https://www.langchain.com/blog/delta-channels-evolving-agent-runtime); [DeltaChannel API](https://reference.langchain.com/python/langgraph/channels/delta/DeltaChannel)).

LangGraph docs also warn: unchecked checkpoint history increases latency and storage — prune / retention cron recommended ([LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)).

### Prompt / semantic caching

> ⚠️ Limited public data available for this dimension. Agent memory frameworks publish retrieval token counts and latencies, not Anthropic/OpenAI **prompt-cache hit rates** or TTLs specifically for memory-injected prefixes. [inferred] Stable system prompts + stable retrieved preference blocks are the natural cache keys; hot-path memory rewrites invalidate cached prefixes more often than background writes.

## 3. Distributed Resilience & State

### Durable execution via checkpointers

LangGraph checkpointers are the agent-native durable-execution layer (analogous in *role* to Temporal workflows, but scoped to graph super-steps) ([LangGraph Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)):

- Crash / timeout / redeploy → reload last checkpoint; resume without re-asking locked constraints ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)).
- HITL: interrupt, human edits state, resume from same `thread_id`.
- Pending writes enable **partial super-step recovery** without re-executing successful parallel nodes.
- State versioning needed when schemas evolve mid-flight (new fields, reordered steps) — migrate old checkpoints or block incompatible paths ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)).

### Consistency model (application-level)

Not distributed consensus — **policy consistency** between layers ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)):

1. State reflects latest user constraints immediately.
2. Memory writes gated (hot path with confirmation, or background with hold-out).
3. External SoR overrides memory on conflict.
4. Correction = constraint change → selective rollback of dependent steps only.

Mem0 update phase encodes ADD / UPDATE / DELETE / NOOP via LLM tool-call over top-*s* similar memories (*s*=10 in paper experiments) to reduce contradictory long-term facts ([Mem0 paper §2.1](https://arxiv.org/abs/2504.19413)).

### Concurrency & locking

> ⚠️ Limited public data available for this dimension. LangGraph Store/checkpointer docs do not publish distributed lock protocols or leader election. [inferred] Thread-scoped checkpointers serialize a single workflow via `thread_id`; multi-writer Store updates need application-level optimistic versioning or DB constraints. ADK-style shared `session.state` across parallel branches (covered in multi-agent research) is a separate race surface.

### Circuit breakers / rate limits

> ⚠️ Limited public data available for this dimension for *memory subsystems specifically*. General agent rate-limit/fallback patterns belong to orchestration topics; memory layers inherit LLM and DB rate limits on extract/search paths (Mem0 extraction uses GPT-4o-mini in paper experiments).

## 4. Enterprise Security & Governance

### Memory as a regulated data store

Long-term memory holds preferences, PII-adjacent facts, and conversation distillations. Mem0 guidance: avoid storing secrets/credentials/unredacted sensitive data; always scope searches with `user_id` / `agent_id` / `run_id` ([Mem0 how-it-works](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/how-it-works.mdx)).

[inferred] Enterprise controls that map cleanly onto the four-layer model:

| Control | Where to enforce |
| --- | --- |
| Tenant isolation | Store namespaces + checkpointer `thread_id` prefixes |
| Write approval | Gate long-term ADD/UPDATE (human or policy) before durable write |
| Retention / right-to-delete | Memory lifecycle Delete stage + checkpoint prune |
| Audit | Log memory writes/reads with actor, scope keys, operation (ADD/UPDATE/DELETE) |
| SoR authority | Never let cached memory override pricing/inventory without re-fetch |

### Tool / memory RBAC

> ⚠️ Limited public data available for this dimension. Neither LangGraph Store nor Mem0 publishes a first-class RBAC matrix for memory keys. [inferred] Treat Store items like application data: IAM at the DB, row-level filters on `user_id`/tenant, and separate privileges for procedural (`agent_id`) vs personal (`user_id`) memories (Mem0 scoping convention).

### PII & audit

Wrong information captured into long-term memory (newsletter failure mode #2) is a **governance** problem: a mistaken loyalty number or phone number persists across tasks ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)). Mitigations: confidence tags (`confirmed` vs `inferred`), human approval before durable write, retention windows, and delete on booking completion for trip-specific data ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)).

### Sandbox isolation

Anthropic recommends extensive testing in sandboxed environments for autonomous agents ([Anthropic — Building effective agents](https://www.anthropic.com/research/building-effective-agents)). Memory does not replace environment isolation: Replit’s July 2025 incident (production DB deleted during a code freeze despite “DON’T DO IT” in context) shows **retrieval/application failure**, not missing storage — later mitigated with automatic separation of dev vs production environments ([AI Incident Database #1152](https://incidentdatabase.ai/cite/1152/); [System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)).

## 5. Production Failure Modes

### Context window degradation

**Symptoms**: early goals/constraints drop out; agent re-asks questions; attention dilutes (context rot) ([Anthropic — Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents); [System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)).

**Mitigations**: scoped retrieval; summarization/compaction; tool-result clearing; external note-taking; sub-agent context firewalls; lazy tool schemas ([Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents); [System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)).

### Three memory pipeline failures ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory))

1. **Stale memory** — outdated preference overrides newer intent (no expire/update).
2. **Wrong information captured** — extraction bug poisons many future tasks.
3. **Retrieval failure** — correct memory exists but is not surfaced (ranking/scope/metadata miss). Replit incident framed as this class: instruction was in context but not correctly applied.

### State drift & checkpoint hazards

- Partial writes without pending-write recovery → divergent retries [inferred from LangGraph pending-writes design].
- Unbounded checkpoint growth → latency/storage blowups; prune required ([LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)).
- Schema evolution without versioning → in-flight threads unreadable ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)).
- **DeltaChannel reducer bugs**: if reducer violates batching-invariance, state **silently diverges** across snapshot boundaries ([LangChain — Delta Channels](https://www.langchain.com/blog/delta-channels-evolving-agent-runtime)).
- Memory rot: rare exceptions written as permanent rules when “learn too fast” ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory); [LangChain — Memory for agents](https://www.langchain.com/blog/memory-for-agents)).

### Compaction thrashing

Aggressive summarization drops details the agent later needs → re-read files / re-run tools (“thrashing”) ([Victor Dibia](https://newsletter.victordibia.com/p/context-engineering-101-how-agents)). Anthropic: tune compaction prompts for **recall first, then precision**; overly aggressive compaction loses subtle critical context ([Anthropic — Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

### Infinite loops / cost caps

Anthropic agents guidance: include stopping conditions (max iterations) ([Anthropic — Building effective agents](https://www.anthropic.com/research/building-effective-agents)). Memory layers add write amplification if every turn triggers extract+embed without NOOP gating ([Mem0 paper](https://arxiv.org/abs/2504.19413)).

### Cascading timeouts

> ⚠️ Limited public data available for this dimension specific to memory services. Zep’s multi-hour graph construction delay is a published readiness failure mode (search wrong immediately after write) ([Mem0 paper §4.5](https://arxiv.org/abs/2504.19413)).

## 6. Enterprise System Design Scenarios

### Design checklist (production)

From the newsletter synthesis ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)):

1. Separate state from memory.
2. Checkpoint and selectively roll back.
3. Keep memory outside the prompt; retrieve by state.
4. Budget context; summarize old checkpoints; drop stale summaries.
5. Let the system of record win.
6. Trade cost/latency/reliability deliberately (spend precision on bookings/payments).
7. Monitor context usage, retrieval hits, tool calls, latency.
8. Safeguards: freshness rules, approval before long-term write, scope-aware updates.

### Trade-off matrix

| Approach | Cost | Latency | Ops complexity | Consistency strength | Scale fit |
| --- | --- | --- | --- | --- | --- |
| Full conversation in prompt | Highest tokens | Worst p95 (LOCOMO ~17s) | Lowest | High fidelity until window fills | Short sessions only |
| Sliding window | Low | Low | Low | Weak on early constraints | Chat UX |
| Compaction / summarization | Medium (extra LLM call) | Medium | Medium | Lossy but continuous | Long single sessions |
| LangGraph checkpointer + Store | Storage + DB ops | Fast resume if pruned/delta | Medium–high | Strong workflow consistency | Multi-step / HITL |
| Mem0-style extractive memory | Write-path LLM cost | Best published interactive p95 | Medium | Strong if update ops gated | Multi-session personalization |
| Graph memory (Mem0ᵍ / Zep-class) | Higher store + latency | Higher than dense Mem0 | High | Better temporal/relational | Complex entity relations |

### Capacity planning anchors (published)

| Metric | Value | Source |
| --- | --- | --- |
| LOCOMO avg conversation | ~26k tokens / ~600 turns | Mem0 paper |
| Mem0 retrieved tokens / query | ~1.8k | Mem0 Table 2 |
| Mem0 p95 total latency | 1.44s | Mem0 Table 2 |
| Full-context p95 | 17.1s | Mem0 Table 2 |
| Checkpoint @ 200 coding turns (full snap) | 5.3 GB | LangChain Delta Channels |
| Same with DeltaChannel | 129 MB | LangChain Delta Channels |
| Compaction default trigger | 150k input tokens (min 50k) | Anthropic Compaction API |
| Tool schema bloat (pre Tool Search) | ~134k tokens | Newsletter ← Anthropic |
| Sub-agent return summary | ~1–2k tokens | Anthropic context engineering |

### Architecture case: travel-agent loop

End-to-end loop used as the newsletter’s reference design ([System Design Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)): user input → intent/reasoning → **state update** → **scoped memory access** → tool execution (SoR) → response → state/memory write → next step. Checkpoints after preferences confirmed, outbound selected, return selected, hotel booked — enabling hotel-step retry without recollecting flights.

### Hybrid Anthropic pattern

For long-horizon work, combine ([Anthropic — Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)):

1. **Compaction** for conversational continuity inside a session.
2. **Structured note-taking** (files / memory tool) for cross-compaction persistence.
3. **Sub-agents** when exploration would pollute the lead context.
4. **Just-in-time** tool retrieval (paths, queries) over stuffing corpora into working memory.

Claude Code hybrid: `CLAUDE.md` loaded up front; glob/grep for just-in-time file context ([Anthropic — Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

---

## Sources

- [1] https://newsletter.systemdesign.one/p/ai-agent-memory — Primary article: state vs memory, consistency, reference architecture, failure modes, context budget (May 2026)
- [2] https://docs.langchain.com/oss/python/langgraph/persistence — LangGraph checkpointer vs Store persistence overview
- [3] https://docs.langchain.com/oss/python/langgraph/checkpointers — Super-step checkpoints, pending writes, StateSnapshot, time travel
- [4] https://docs.langchain.com/oss/python/langgraph/stores — Cross-thread long-term Store API
- [5] https://docs.langchain.com/oss/python/langgraph/add-memory — Short-term vs long-term memory how-to
- [6] https://www.langchain.com/blog/memory-for-agents — CoALA-aligned memory types; hot-path vs background writes (Chase, Oct 2024)
- [7] https://www.langchain.com/blog/delta-channels-evolving-agent-runtime — O(N²) checkpoint growth; 5.3 GB → 129 MB @ 200 turns
- [8] https://reference.langchain.com/python/langgraph/channels/delta/DeltaChannel — DeltaChannel API, snapshot_frequency defaults
- [9] https://www.langchain.com/blog/is-langgraph-used-in-production — Production LangGraph adoption (incl. LinkedIn SQL Bot context)
- [10] https://arxiv.org/abs/2504.19413 — Mem0 paper: LOCOMO latency/token/J metrics
- [11] https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/how-it-works.mdx — Mem0 add/search pipeline and ranking signals
- [12] https://mem0.ai/blog/semantic-vs-episodic-vs-procedural-memory-in-ai-agents-a-complete-comparison — Scoping conventions for memory types
- [13] https://github.com/mem0ai/mem0/issues/3455 — MemoryType enum: only PROCEDURAL actively wired
- [14] https://arxiv.org/abs/2309.02427 — CoALA: working / episodic / semantic / procedural memory
- [15] https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents — Context rot, compaction, note-taking, sub-agent summaries
- [16] https://www.anthropic.com/research/building-effective-agents — Ground-truth-from-environment; max-iteration guards
- [17] https://platform.claude.com/docs/en/build-with-claude/compaction — Server-side compaction triggers (150k default, 50k min)
- [18] https://incidentdatabase.ai/cite/1152/ — Replit agent production DB deletion (Jul 2025)
