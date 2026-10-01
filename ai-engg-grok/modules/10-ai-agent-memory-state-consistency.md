# Module 10 — AI Agents: Memory, State & Consistency

**Audience**: Principal AI Architect interview prep · personal deep study  
**Sequence**: 10 (agent-state layer after multi-agent topology)  
**Grounded in**: `research/10-ai-agent-memory-state-consistency.md` (18 sources, 2026-09-30)

Stateful agents answer three questions per decision — *what step am I on, what is already done, what next?* — then load the latest snapshot before each step. Frameworks often conflate **state** (react fast), **memory** (learn slowly), and **systems of record** (always win conflicts) ([System Design Newsletter — AI Agent Memory](https://newsletter.systemdesign.one/p/ai-agent-memory); [Anthropic — Building effective agents](https://www.anthropic.com/research/building-effective-agents)). This module covers that continuity layer only. **Vector index internals** (HNSW, ANN) are the next roadmap topic; retrieval appears here only as an agent-facing API (scope, metadata filters, what enters the prompt).

---

## Part 1 — System Topology & Data Flow

### Architecture map

```
                         ┌──────────────────────────────────────────────────────────┐
                         │                    CONTROL PLANE                         │
                         │  Agent brain (plan / intent / next action)               │
                         │  Consistency policy: react-fast state · learn-slow mem   │
                         │  SoR-wins conflict rule · hot-path vs background writes  │
                         │  Compaction / sliding-window / retrieval budget gates    │
                         │                                                          │
                         │  ┌────────────┐  ┌────────────┐  ┌────────────────────┐  │
                         │  │ Graph /    │  │ Preference │  │ Write gate         │  │
                         │  │ super-step │  │ classifier │  │ (confirm / hold)   │  │
                         │  └─────┬──────┘  └─────┬──────┘  └─────────┬──────────┘  │
                         └────────┼───────────────┼───────────────────┼─────────────┘
                                  │               │                   │
                                  ▼               ▼                   ▼
                         ┌──────────────────────────────────────────────────────────┐
                         │                     DATA PLANE                           │
                         │  Working state (messages, step, constraints, next[])     │
                         │  Context assembly: instructions+state+mem+tools+user     │
                         │  Tool results as environment ground truth                │
                         └───┬──────────────────────┼───────────────────────┬───────┘
                             │                      │                       │
              ┌──────────────┴───────┐  ┌───────────┴─────────┐  ┌──────────┴───────┐
              │     TOOL PROXIES     │  │    PERSISTENCE      │  │    TELEMETRY     │
              ├──────────────────────┤  ├─────────────────────┤  ├──────────────────┤
              │  Memory add/search   │  │  Checkpointer       │  │  tokens · $ · ms │
              │  MCP memory tools    │  │  (thread snapshots) │  │  retrieval hit % │
              │  SoR APIs (booking,  │  │  Store / Mem0       │  │  breaker state   │
              │   inventory, CRM)    │  │  (cross-thread)     │  │  ckpt size / age │
              │  Schema validate     │  │  SoR (authoritative)│  │  correlation IDs │
              └──────────────────────┘  └─────────────────────┘  └──────────────────┘
```

**Plane responsibilities**

| Plane | Role in memory / state systems |
| --- | --- |
| **CONTROL PLANE** | LLM plans next action from state + retrieved memory; gates long-term writes; enforces SoR-wins; chooses compaction vs retrieval vs slide ([Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)) |
| **DATA PLANE** | Working context window: instructions + workflow state + scoped memory + tool schemas/outputs + user input ([Anthropic — Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)) |
| **PERSISTENCE** | Checkpointer (thread-scoped snapshots), Store / Mem0 (cross-thread facts), SoR (authoritative live data) ([LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)) |
| **TOOL PROXIES** | Memory add/search tools, MCP-wrapped memory servers, SoR tool calls; validate args before side effects |
| **TELEMETRY** | Prompt tokens, retrieval latency, J/quality proxies, checkpoint footprint, breaker state, correlation IDs |

### Four persistence concerns (do not conflate)

| Concern | Owns | Lifetime | Update rule |
| --- | --- | --- | --- |
| **Working state** | Step, plan, active constraints, messages in-flight | Current turn / context window | React **fast** — every instruction |
| **Checkpoint** | `StateSnapshot` per graph super-step (`thread_id`) | Task / thread; survives crash/HITL | Write at super-step boundary; pending writes for partial recovery |
| **Long-term store** | Preferences, facts, reusable knowledge | Cross-session / cross-thread | Learn **slowly** — after change looks steady |
| **System of record (SoR)** | Prices, inventory, bookings, live APIs | Authoritative | **Always wins** conflicts with stored memory |

Sources: [Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory); [LangGraph Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers); [LangGraph Stores](https://docs.langchain.com/oss/python/langgraph/stores); [Anthropic agents](https://www.anthropic.com/research/building-effective-agents).

### End-to-end request-flow narrative

1. **Ingress** — User message arrives with `thread_id` / `user_id`. CONTROL PLANE loads the latest **checkpoint** (workflow progress) for that thread; if absent, initialize empty working state ([LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)).
2. **State hydrate (DATA PLANE)** — Merge checkpoint `values` + `next` into working state. Apply any user correction immediately to constraints (“window seat this time”); selectively roll back only dependent steps ([Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)).
3. **Scoped memory retrieve (TOOL PROXIES → PERSISTENCE)** — Query long-term Store / Mem0 with metadata scope (topic, confidence, duration). Flight booking pulls flight+budget memories, not hotel memories. Retrieved facts stay **outside** the default prompt until selected ([Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory); [Mem0 how-it-works](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/how-it-works.mdx)).
4. **Context assembly** — Budget: `instructions + state + retrieved memory + tool schemas + tool outputs + user input`. Prefer JIT schemas (Tool Search cut Claude Code tool overhead ~**85%**) over stuffing ([Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents); [Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)).
5. **Plan & act** — Agent brain picks next action. Tool calls hit SoR (inventory, booking). Tool results are **environment ground truth** — if SoR has no window seats, report unavailability; do **not** delete the preference from memory ([Anthropic agents](https://www.anthropic.com/research/building-effective-agents); [Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)).
6. **Conflict resolution** — On memory vs SoR disagreement, **SoR wins** for the decision; memory may still hold the preference for future tasks.
7. **Checkpoint write** — At super-step boundary, checkpointer persists full (or delta) snapshot + pending writes. Resume after peer-node failure skips successful siblings ([LangGraph Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).
8. **Memory write (gated)** — Hot path: agent tool before reply (immediate consistency, +latency). Background: extract/consolidate after turn (“learn slowly”). Tag one-time vs lasting (“always” / “going forward”) before durable ADD/UPDATE ([LangChain — Memory for agents](https://www.langchain.com/blog/memory-for-agents); [Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)).
9. **Egress & TELEMETRY** — Response returns; emit tokens, retrieval p95, checkpoint bytes, correlation id. Prune / delta-channel retention prevents O(N²) storage blowup ([LangChain — Delta Channels](https://www.langchain.com/blog/delta-channels-evolving-agent-runtime)).

Message style: **synchronous agent loop with durable super-steps**; memory is async-capable (background) but decisions wait on SoR for authoritative facts.

---

## Part 2 — Core Mechanics & Algorithms

### Theoretical fundamentals

**Policy consistency** (not distributed consensus) between layers ([Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)):

1. State reflects latest user constraints immediately.  
2. Memory writes are gated (hot path with confirmation, or background with hold-out).  
3. External SoR overrides memory on conflict.  
4. Correction = constraint change → selective rollback of dependent steps only.

Anthropic’s framing is equivalent: gain **ground truth from the environment** at each step (tool results) to assess progress ([Anthropic agents](https://www.anthropic.com/research/building-effective-agents)).

### CoALA memory tiers → production mapping

| Tier | Question | Agent implementation (today) |
| --- | --- | --- |
| **Working** | What am I thinking about *now*? | Context window / thread state / checkpointer `messages` |
| **Episodic** | What happened *to me*? | Past trajectories, session logs, few-shot episodes |
| **Semantic** | What do I *know*? | Extracted facts/preferences in Store / Mem0 / RAG corpora |
| **Procedural** | What do I *know how to do*? | LLM weights + agent code/prompts/tools |

([CoALA arXiv:2309.02427](https://arxiv.org/abs/2309.02427); [LangChain — Memory for agents](https://www.langchain.com/blog/memory-for-agents)). Newsletter short-term ≈ working; long-term ≈ semantic (+ some episodic); external ≈ read-only SoR semantic ([Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)).

Mem0 API reality: docs discuss working/factual/episodic/semantic types, but as of 2026-03 the SDK `MemoryType` enum only **actively wires `procedural_memory`**; practical scoping uses `user_id` / `run_id` / `agent_id` ([Mem0 issue #3455](https://github.com/mem0ai/mem0/issues/3455)).

### Checkpointer vs Store (LangGraph)

| | **Checkpointer** | **Store** |
| --- | --- | --- |
| Persists | Graph state snapshots (`StateSnapshot`) | Application-defined key-value items |
| Scope | Single `thread_id` | Across threads (e.g. `(user_id, "memories")`) |
| Memory type | Short-term / thread-scoped | Long-term / cross-thread |
| Use for | Continuity, HITL, time travel, fault tolerance | Preferences, facts, shared knowledge |
| Access | Config `{"configurable": {"thread_id": "..."}}` | Read/write from nodes via injected store |

Production backends: `PostgresSaver` / `AsyncPostgresSaver`; `SqliteSaver` (dev); `InMemorySaver` **does not survive process restart**. Keep `thread_id` under **255** characters for Postgres ([LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)).

### Checkpoint state machine

```
                    ┌────────────┐
                    │  INGRESS   │──load──► last StateSnapshot (thread_id)
                    └──────┬─────┘
                           ▼
                    ┌────────────┐
                    │  SUPERSTEP │──schedule nodes──► pending writes
                    └──────┬─────┘
                           │ all scheduled nodes complete (or interrupt)
                           ▼
                    ┌────────────┐     parent_config / checkpoint_id
                    │ CHECKPOINT │──────────────────────────────────► PERSISTENCE
                    └──────┬─────┘
                           │ HITL? crash?
              ┌────────────┼────────────┐
              ▼            ▼            ▼
         ┌────────┐  ┌──────────┐  ┌──────────┐
         │ RESUME │  │ TIME     │  │  FORK    │
         │ next[] │  │ TRAVEL   │  │ new id   │
         └────────┘  └──────────┘  └──────────┘
```

Mechanics ([LangGraph Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)):

- Checkpoint written at each **super-step** boundary.  
- Per-node **pending writes** → on resume after peer failure, successful nodes are **not** re-run.  
- `StateSnapshot`: `values`, `next`, `config` (`thread_id`, `checkpoint_ns`, `checkpoint_id`), `metadata`, `created_at`, `parent_config`, `tasks`.  
- Subgraphs get their own checkpoint namespace; use Store for cross-graph durable data.

### Memory update algorithm (Mem0-style)

1. Context lookup → candidate similar memories (top-*s*, *s*=10 in paper).  
2. Fact extraction from turn.  
3. LLM tool-call decides ADD / UPDATE / DELETE / NOOP.  
4. Dedup / embed / entity extract; search blends semantic, keyword, entity, temporal signals.

Complexity: extraction + comparison over *s* memories is **O(s)** LLM-mediated ops per write (constant *s* in experiments); search ranking is product-specific ([Mem0 paper](https://arxiv.org/abs/2504.19413); [Mem0 how-it-works](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/how-it-works.mdx)).

### Context strategies (complexity / invariants)

| Strategy | Mechanism | Effect |
| --- | --- | --- |
| **Sliding window** | Keep system + last N turns | Cheapest; loses early goals |
| **Head+tail** | Preserve task head + recent tail | Better goal retention |
| **Tool-result clearing** | Drop re-fetchable payloads | Lightest safe compaction |
| **Summarization / compaction** | LLM summary block | Lossy; costs one call; Anthropic default trigger **150k** input tokens (min **50k**) |
| **External memory** | Persist facts; retrieve JIT | Moves cost off prompt |

([Victor Dibia](https://newsletter.victordibia.com/p/context-engineering-101-how-agents); [Anthropic Compaction](https://platform.claude.com/docs/en/build-with-claude/compaction); [Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

**Invariants**

| Property | Statement |
| --- | --- |
| Attention budget | Context is finite; longer windows → context rot ([Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)) |
| SoR authority | Memory suggests; SoR decides ([Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)) |
| Learn-slowly | Rare exceptions must not become permanent rules ([LangChain](https://www.langchain.com/blog/memory-for-agents)) |
| Checkpoint growth | Full snapshots of append-only channels → **O(N²)** storage; DeltaChannel → ~**41×** reduction @ 200 turns (**5.3 GB → 129 MB**) ([LangChain Delta Channels](https://www.langchain.com/blog/delta-channels-evolving-agent-runtime)) |
| Delta reducer | Reducer must be batching-invariant or state **silently diverges** across snapshot boundaries ([Delta Channels](https://www.langchain.com/blog/delta-channels-evolving-agent-runtime)) |

---

## Part 3 — Token Economics & NFR Analysis

### Cost formula: `$ per 1k runs`

**Stated assumptions (labeled)** — LOCOMO prompt sizes from Mem0 Table 2; prices are **assumptions for arithmetic**, not live quotes or Mem0-published unit costs.

| Symbol | Value | Meaning |
| --- | --- | --- |
| \(T_{\text{full}}\) | **26,031** tokens | LOCOMO full-context avg prompt ([Mem0 paper Table 2](https://arxiv.org/abs/2504.19413)) |
| \(T_{\text{mem0}}\) | **1,764** tokens | Mem0 retrieved memory tokens avg (≈ **1.8k**) |
| Model | Claude Sonnet-class | Same price book as Module 09 for cross-module comparison |
| \(P_{\text{in}}\) | **$3 / MTok** | Assumed input price (*assumption*) |
| \(P_{\text{out}}\) | **$15 / MTok** | Assumed output price (*assumption*) |
| \(T_{\text{out}}\) | **200** tokens | Assumed completion size per QA (*assumption*; not in Table 2) |
| Write-path | **Excluded** | Mem0 add/extract LLM calls are extra; figures below are **read-path prompt** comparison only |

Input-dominant cost per query:

\[
C_{\text{full}} = \frac{26031}{10^6}\cdot \$3 + \frac{200}{10^6}\cdot \$15
= \$0.078093 + \$0.003 = \mathbf{\$0.081093}
\]

\[
C_{\text{mem0}} = \frac{1764}{10^6}\cdot \$3 + \frac{200}{10^6}\cdot \$15
= \$0.005292 + \$0.003 = \mathbf{\$0.008292}
\]

**$ per 1k runs** (read-path only, labeled assumptions):

\[
C_{\text{1k, full}} = 1000 \times \$0.081093 = \mathbf{\$81.09\ /\ 1k\ runs}
\]

\[
C_{\text{1k, mem0}} = 1000 \times \$0.008292 = \mathbf{\$8.29\ /\ 1k\ runs}
\]

\[
\Delta = \$81.09 - \$8.29 = \mathbf{\$72.80\ /\ 1k\ runs}
\quad
(\approx\mathbf{9.8\times}\ \text{cheaper input+out under these prices})
\]

Token reduction: \(1764 / 26031 \approx 93.2\%\) fewer prompt tokens (paper: “more than **90%** token cost”) ([Mem0 paper](https://arxiv.org/abs/2504.19413)).

> ⚠️ **Gap**: Mem0 / LOCOMO do **not** publish **$ per 1k executions**. Dollar figures above are **[inferred]** from Table 2 token counts × labeled model prices. Real bills add write-path extraction, embeddings, and store infra.

**Alternate book (gpt-4o-mini-class assumption)** — if \(P_{\text{in}}=\$0.15/\text{MTok}\), \(P_{\text{out}}=\$0.60/\text{MTok}\), same token shapes: \(C_{\text{1k, full}}\approx\$4.02\), \(C_{\text{1k, mem0}}\approx\$0.38\) **[inferred]**.

Prompt-cache note: > ⚠️ Limited public data for memory-injected prefix hit rates. **[inferred]** Stable system + stable preference blocks cache well; hot-path memory rewrites invalidate prefixes more than background writes.

### Latency SLA targets

**Published (Mem0 Table 2 — total latency, LOCOMO)** ([Mem0 paper](https://arxiv.org/abs/2504.19413)):

| Method | Context tokens | **p50** (s) | **p95** (s) | Overall J |
| --- | --- | --- | --- | --- |
| Full-context | 26,031 | **9.870** | **17.117** | **72.90%** ±0.19 |
| Mem0 | 1,764 | **0.708** | **1.440** | 66.88% ±0.15 |
| Mem0ᵍ | 3,616 | 1.091 | 2.590 | 68.44% ±0.17 |
| LangMem (hot path) | 127 | 18.53 | **60.40** | 58.10% ±0.21 |
| Best RAG (k=2) | chunked | 0.802 | 1.907 | 60.97% ±0.20 |

Mem0 search p95 alone: **0.200 s** vs LangMem **59.82 s**. Paper: Mem0 p95 total ≈ **91%** lower than full-context (\(1.440 / 17.117 \approx 91.6\%\)).

> ⚠️ **Gap**: Vendor **agent-loop** production SLAs do **not** publish unified **p50 / p95 / p99** for (checkpointer resume + memory search + LLM + SoR tools) as one product percentile curve. Table 2 is LOCOMO benchmark total latency, not a LangGraph/Mem0 SaaS SLA. **p99 is unpublished** in consulted sources.

**[inferred] latency budget** for a production stateful turn (engineering targets — extend beyond published numbers):

| Tier | Full-context path | Memory-retrieval path | Covers / mitigations |
| --- | --- | --- | --- |
| **p50** | **≤ 10 s** (pub. 9.87 s) | **≤ 0.8 s** (pub. 0.708 s) | Warm Store; scoped retrieve; avoid hot-path LangMem-class extract |
| **p95** | **≤ 18 s** (pub. 17.12 s) | **≤ 1.5 s** (pub. 1.44 s) | Cache stable prefs; prune checkpoints; parallel SoR only when needed |
| **p99** | **≤ 35 s [inferred]** | **≤ 4 s [inferred]** | Compaction thrash, cold Store, SoR timeouts, breaker→fallback; cap iterations |

Mitigations by tier: caching stable prefixes; streaming user-visible tokens; background memory writes; DeltaChannel + prune; sub-agent returns of **~1–2k** tokens as firewall ([Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)).

### Throughput & back-pressure

| Lever | Behavior |
| --- | --- |
| **Prompt size** | 26k → 1.8k tokens raises theoretical prompt throughput ~**14×** at fixed TPM **[inferred]** from token ratio |
| **Write amplification** | Every-turn extract+embed without NOOP gating burns write-path capacity ([Mem0 paper](https://arxiv.org/abs/2504.19413)) |
| **Checkpoint I/O** | Full snapshots @ 200 heavy turns → **5.3 GB**; DeltaChannel → **129 MB** (~**41×**) — back-pressure on DB/disk ([Delta Channels](https://www.langchain.com/blog/delta-channels-evolving-agent-runtime)) |
| **Graph readiness** | Zep-class graphs: multi-hour async lag → search wrong immediately after write ([Mem0 paper §4.5](https://arxiv.org/abs/2504.19413)) |
| **Shed load** | Under TPM pressure: shrink retrieve-k → sliding window → SoR-only reads; open memory-path breaker **[inferred]** |
| **Retention cron** | Unchecked checkpoint history ↑ latency + storage — prune required ([LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)) |

### NFR trade-offs

| NFR | Target / posture | Memory / state implication |
| --- | --- | --- |
| **Availability** | Design **99.9%** on agent ingress + checkpointer; memory search may degrade | Breaker open on memory path ≠ total outage if sliding window + SoR remain healthy **[inferred]** |
| **RPO** | Sync checkpoint each super-step → RPO ≈ last completed step; memory background writes → RPO = last durable ADD | In-memory saver RPO = process lifetime (lose all on restart) ([LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)) |
| **RTO** | Reload `thread_id` + checkpoint; pending writes skip successful nodes | Prefer resume over full restart; schema-version old checkpoints or block incompatible paths ([Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)) |
| **Compliance** | Memory = regulated store; PII detect→redact→audit before write; retention / right-to-delete; tenant namespaces | Never let cached memory override pricing/inventory without SoR re-fetch ([Mem0 guidance](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/how-it-works.mdx); [Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)) |

**Explicit trade-off — recall quality vs token / latency**: Full-context leads on LLM-as-judge **J = 72.9%** vs Mem0 **J = 66.9%** (Mem0ᵍ **68.4%**), but Mem0 uses ~**1.8k** vs ~**26k** tokens and cuts p95 from **17.1 s → 1.44 s**. Pay the **~6 pp J** tax when interactive cost/latency dominate; keep full-context or heavier retrieve for high-stakes recall (bookings/payments) ([Mem0 paper Table 2](https://arxiv.org/abs/2504.19413); [Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)).

---

## Part 4 — Distributed Resilience & Security

### Checkpointer vs Store durability

| Dimension | **Checkpointer** | **Store / long-term memory** |
| --- | --- | --- |
| Durability role | Workflow resume, HITL, time travel | Cross-session personalization / facts |
| Crash behavior | Reload last snapshot; pending writes avoid re-running successes | Survives thread end; not a substitute for SoR |
| Consistency | Thread-serialized via `thread_id` **[inferred]** | Multi-writer needs optimistic versioning / DB constraints **[inferred]** |
| Failure if lost | Lose in-flight step progress | Lose prefs; workflow may still resume from checkpoint |
| Growth hazard | O(N²) full snaps → **5.3 GB** @ 200 turns; delta → **129 MB** | Store construction: Mem0 ~**7k** tok/conv; Zep graph **>600k** + readiness lag |

LangGraph checkpointers are the agent-native durable-execution layer (role-analogous to Temporal workflows, scoped to graph super-steps) ([LangGraph Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).

> ⚠️ **Gap**: LangGraph Store/checkpointer docs do not publish distributed lock protocols or leader election. Thread-scoped serialization and Store optimistic concurrency are **[inferred]** application patterns.

### Failure taxonomy

| Class | Examples | Detection / response |
| --- | --- | --- |
| **Transient** | Memory search 429/5xx; SoR timeout; extract LLM blip | Retry + full jitter; breaker counts failures |
| **Permanent** | Auth deny on Store; schema-invalid checkpoint; unknown namespace | Fail-closed; do not write poisoned memory |
| **Poison-pill** | Wrong fact extracted (loyalty #); every-turn write loops; compaction thrash | Confidence tags; approval before durable write; NOOP gating; max iterations ([Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory); [Anthropic agents](https://www.anthropic.com/research/building-effective-agents)) |
| **State drift** | Partial writes without pending-write recovery; DeltaChannel reducer non-invariant; schema evolve mid-flight; memory rot (“learn too fast”) | Pending writes; batching-invariant reducers; version checkpoints; hold-out before lasting write ([Delta Channels](https://www.langchain.com/blog/delta-channels-evolving-agent-runtime); [Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)) |
| **Retrieval / application** | Correct memory exists but not surfaced; instruction in context not applied (Replit Jul 2025 prod DB delete — AIID #1152) | Scope/metadata fix; sandbox env separation — memory ≠ isolation ([AIID #1152](https://incidentdatabase.ai/cite/1152/)) |

Idempotency **[inferred]**: key memory writes by `(user_id, fact_hash, op)` and tool side effects by `(thread_id, checkpoint_id, tool, args_hash)`.

### Circuit breaker: closed → open → half-open

> ⚠️ Limited public data for memory-subsystem-specific breaker curves. Pattern below is the standard closed→open→half-open machine applied to the **memory retrieve / extract** path **[inferred]**.

```
          success                    recovery_timeout
     ┌──────────────┐  fail≥N   ┌──────┐  elapsed   ┌───────────┐
     │    CLOSED    │──────────►│ OPEN │───────────►│ HALF_OPEN │
     └──────▲───────┘           └──────┘            └─────┬─────┘
            │ success                                      │
            └──────────────────────────────────────────────┘
                         fail → OPEN
```

1. **CLOSED** — Memory retrieve/extract accepts traffic; failures in sliding window counted.  
2. **OPEN** — After ≥ \(N\) failures, reject memory path; start recovery timer; route to fallback.  
3. **HALF_OPEN** — One probe through memory path; success → CLOSED; fail → OPEN.

### Fallback chain (memory retrieve → sliding window → SoR read)

```
  Memory retrieve (Store / Mem0)  ──fail/breaker──►  Sliding window (last N turns)
                 │                                            │
                 │                                            ▼
                 └──────────────────►  SoR read (authoritative tools / APIs)
```

| Stage | Behavior |
| --- | --- |
| **Memory retrieve** | Scoped search; inject ~1.8k-class facts into prompt |
| **Sliding window** | System + recent turns only; no long-term prefs; lower fidelity on early constraints |
| **SoR read** | Live inventory/price/booking; no LLM memory dependency; always terminates with ground truth |

### Enterprise security

> ⚠️ Limited public data for formal Zero-Trust MCP mutual-auth matrices, first-class Store RBAC, or immutable audit schemas **specific to memory tools**. Rows marked **[inferred]** are interview-grade posture mapped onto the four-layer model — not vendor SLAs ([research §4](../research/10-ai-agent-memory-state-consistency.md)).

**Zero-Trust MCP for memory tools** **[inferred]**:

- Authenticate every `memory.add` / `memory.search` call (mTLS or short-lived OAuth).  
- Deny-by-default MCP registry; no ambient credentials in prompts.  
- Per-request scoped tokens bound to `tenant_id` + `user_id`; memory MCP cannot call prod mutation SoR tools unless explicitly granted.  
- Re-auth on thread resume — checkpoint restore does not widen tool grants.

**Namespace RBAC** **[inferred]** (Mem0 scoping convention as practical axes):

| Scope key | Privilege |
| --- | --- |
| `tenant_id` / DB IAM | Hard isolation |
| `user_id` | Personal semantic/episodic memories |
| `agent_id` | Procedural / agent-owned memories |
| `run_id` / `thread_id` | Session-ephemeral; tighter retention |

Neither LangGraph Store nor Mem0 publishes a first-class RBAC matrix — treat Store items like application data with row-level filters ([research §4](../research/10-ai-agent-memory-state-consistency.md)).

**PII pipeline before memory write**: **detect → redact → audit**. Wrong PII in long-term memory poisons many future tasks (newsletter failure mode #2). Mitigations: confidence tags (`confirmed` vs `inferred`), human/policy approval before durable ADD, retention windows, delete trip-specific data on booking completion ([Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)). Avoid secrets/credentials/unredacted sensitive data in Mem0 ([Mem0 how-it-works](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/how-it-works.mdx)).

**Immutable checkpoint chain** **[inferred]**: append-only sequence of `(checkpoint_id, parent_config, thread_id, hash(values), actor, correlation_id, created_at)` so time-travel forks preserve chain-of-custody. Pair with append-only memory audit: `(op, scope keys, redaction_id, disposition)`. Prefer tracing decision structure over raw conversation contents where policy requires.

Sandbox isolation remains mandatory: memory does not replace env separation ([Anthropic agents](https://www.anthropic.com/research/building-effective-agents); [AIID #1152](https://incidentdatabase.ai/cite/1152/)).

---

## Part 5 — Production Enterprise Code

Runnable Python: **checkpointer + memory store**, conflict rule (**SoR wins**), retries + full jitter, circuit breaker (closed → open → half-open), fallback (**memory retrieve → sliding window → SoR read**), correlation IDs, structured logging. **Deterministic** — no API keys, no network, no `# TODO` stubs.

```python
#!/usr/bin/env python3
"""Agent memory/state layer with enterprise resilience primitives."""

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
            "ts": time.time(),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "thread_id": getattr(record, "thread_id", None),
            "attempt": getattr(record, "attempt", None),
            "breaker_state": getattr(record, "breaker_state", None),
            "path": getattr(record, "path", None),
            "degraded": getattr(record, "degraded", None),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


LOG = build_logger("agent.memory_state")


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------

class FailureKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    STATE_DRIFT = "state_drift"


class AgentError(Exception):
    def __init__(self, message: str, kind: FailureKind) -> None:
        super().__init__(message)
        self.kind = kind


# ---------------------------------------------------------------------------
# Circuit breaker: closed → open → half-open
# ---------------------------------------------------------------------------

class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    recovery_timeout_s: float = 0.05
    failures: int = 0
    state: BreakerState = BreakerState.CLOSED
    opened_at: float = 0.0
    clock: Callable[[], float] = time.time

    def allow(self) -> bool:
        if self.state is BreakerState.CLOSED:
            return True
        if self.state is BreakerState.OPEN:
            if self.clock() - self.opened_at >= self.recovery_timeout_s:
                self.state = BreakerState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN: single probe

    def record_success(self) -> None:
        self.failures = 0
        self.state = BreakerState.CLOSED

    def record_failure(self) -> None:
        self.failures += 1
        if self.state is BreakerState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = self.clock()


def retry_with_jitter(
    fn: Callable[[], Any],
    *,
    should_retry: Callable[[Exception], bool],
    max_attempts: int = 3,
    base_delay_s: float = 0.01,
    correlation_id: str,
    rng: random.Random,
) -> Any:
    last: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — classified below
            last = exc
            retryable = should_retry(exc)
            LOG.warning(
                "retryable_failure" if retryable else "permanent_failure",
                extra={
                    "correlation_id": correlation_id,
                    "attempt": attempt,
                    "path": type(exc).__name__,
                },
            )
            if not retryable or attempt == max_attempts:
                raise
            # Full jitter: delay ~ U(0, base * 2^(attempt-1))
            delay = rng.uniform(0.0, base_delay_s * (2 ** (attempt - 1)))
            time.sleep(delay)
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# PII: detect → redact → audit
# ---------------------------------------------------------------------------

@dataclass
class AuditLog:
    entries: list[dict[str, Any]] = field(default_factory=list)

    def append(self, entry: dict[str, Any]) -> None:
        self.entries.append(entry)


_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")


def redact_pii(text: str, audit: AuditLog, correlation_id: str) -> str:
    redacted, n_email = _EMAIL.subn("[REDACTED_EMAIL]", text)
    redacted, n_phone = _PHONE.subn("[REDACTED_PHONE]", redacted)
    audit.append(
        {
            "event": "pii_redaction",
            "correlation_id": correlation_id,
            "emails": n_email,
            "phones": n_phone,
            "sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
        }
    )
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

    def __init__(self) -> None:
        self._chain: dict[str, list[Checkpoint]] = {}

    def save(self, thread_id: str, values: dict[str, Any], next_steps: list[str]) -> Checkpoint:
        history = self._chain.setdefault(thread_id, [])
        parent_id = history[-1].checkpoint_id if history else None
        payload = json.dumps(values, sort_keys=True, default=str)
        content_hash = hashlib.sha256(payload.encode()).hexdigest()
        ckpt = Checkpoint(
            thread_id=thread_id,
            checkpoint_id=str(uuid.uuid4()),
            parent_id=parent_id,
            values=dict(values),
            next_steps=list(next_steps),
            created_at=time.time(),
            content_hash=content_hash,
        )
        history.append(ckpt)
        return ckpt

    def load(self, thread_id: str) -> Checkpoint | None:
        history = self._chain.get(thread_id) or []
        return history[-1] if history else None

    def history(self, thread_id: str) -> list[Checkpoint]:
        return list(self._chain.get(thread_id, []))


@dataclass
class MemoryItem:
    key: str
    value: str
    topic: str
    version: int = 1


class MemoryStore:
    """Namespace-scoped long-term store (user_id → items)."""

    def __init__(self, fail_times: int = 0) -> None:
        self._data: dict[str, dict[str, MemoryItem]] = {}
        self._fail_times = fail_times
        self._calls = 0

    def add(self, user_id: str, key: str, value: str, topic: str) -> MemoryItem:
        ns = self._data.setdefault(user_id, {})
        prev = ns.get(key)
        item = MemoryItem(key=key, value=value, topic=topic, version=(prev.version + 1 if prev else 1))
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
    """Authoritative live data — wins conflicts with memory."""

    def __init__(self, inventory: dict[str, Any] | None = None) -> None:
        self.inventory = inventory or {
            "flight:AA100": {"seats": {"aisle": 2, "window": 0}, "price_usd": 420},
            "flight:UA200": {"seats": {"aisle": 0, "window": 4}, "price_usd": 455},
        }

    def read(self, resource_id: str) -> dict[str, Any]:
        if resource_id not in self.inventory:
            raise AgentError(f"unknown resource {resource_id}", FailureKind.PERMANENT)
        return dict(self.inventory[resource_id])


# ---------------------------------------------------------------------------
# Consistency: SoR wins
# ---------------------------------------------------------------------------

def resolve_preference(
    memory_pref: str | None,
    sor_seats: dict[str, int],
) -> dict[str, Any]:
    """Conflict rule: SoR decides availability; memory preference is advisory."""
    if memory_pref and sor_seats.get(memory_pref, 0) > 0:
        return {"seat": memory_pref, "source": "memory+sor", "available": True}
    # Prefer any available seat from SoR; keep memory pref for future tasks
    for seat, n in sor_seats.items():
        if n > 0:
            return {
                "seat": seat,
                "source": "sor",
                "available": True,
                "memory_pref_retained": memory_pref,
            }
    return {
        "seat": None,
        "source": "sor",
        "available": False,
        "memory_pref_retained": memory_pref,
    }


# ---------------------------------------------------------------------------
# Agent runtime with fallback chain
# ---------------------------------------------------------------------------

@dataclass
class TurnResult:
    correlation_id: str
    thread_id: str
    path: str
    answer: str
    checkpoint_id: str
    degraded: bool
    decision: dict[str, Any]


class MemoryStateAgent:
    def __init__(
        self,
        checkpointer: Checkpointer,
        store: MemoryStore,
        sor: SystemOfRecord,
        audit: AuditLog,
        breaker: CircuitBreaker,
        rng: random.Random,
        window_size: int = 3,
    ) -> None:
        self.checkpointer = checkpointer
        self.store = store
        self.sor = sor
        self.audit = audit
        self.breaker = breaker
        self.rng = rng
        self.window_size = window_size

    def _should_retry(self, exc: Exception) -> bool:
        return isinstance(exc, AgentError) and exc.kind is FailureKind.TRANSIENT

    def _memory_retrieve(self, user_id: str, topic: str, correlation_id: str) -> list[MemoryItem]:
        if not self.breaker.allow():
            raise AgentError("circuit open", FailureKind.TRANSIENT)

        def call() -> list[MemoryItem]:
            return self.store.search(user_id, topic=topic)

        try:
            items = retry_with_jitter(
                call,
                should_retry=self._should_retry,
                max_attempts=3,
                correlation_id=correlation_id,
                rng=self.rng,
            )
            self.breaker.record_success()
            return items
        except Exception:
            self.breaker.record_failure()
            raise

    def _sliding_window(self, messages: list[str]) -> list[str]:
        return messages[-self.window_size :]

    def run(
        self,
        *,
        user_id: str,
        thread_id: str,
        user_text: str,
        resource_id: str,
        topic: str = "flight",
    ) -> TurnResult:
        correlation_id = str(uuid.uuid4())
        prior = self.checkpointer.load(thread_id)
        messages: list[str] = list((prior.values if prior else {}).get("messages", []))
        messages.append(user_text)

        # PII gate before any durable memory write
        safe_text = redact_pii(user_text, self.audit, correlation_id)

        path = "memory_retrieve"
        degraded = False
        memory_pref: str | None = None

        try:
            items = self._memory_retrieve(user_id, topic, correlation_id)
            for item in items:
                if item.key == "seat_preference":
                    memory_pref = item.value
            context_bits = [f"{i.key}={i.value}" for i in items]
            LOG.info(
                "memory_hit",
                extra={
                    "correlation_id": correlation_id,
                    "thread_id": thread_id,
                    "path": path,
                    "breaker_state": self.breaker.state.value,
                },
            )
        except Exception:
            degraded = True
            if self.breaker.state is BreakerState.OPEN:
                path = "sliding_window"
                window = self._sliding_window(messages)
                context_bits = [f"window:{m}" for m in window]
                LOG.warning(
                    "fallback_sliding_window",
                    extra={
                        "correlation_id": correlation_id,
                        "thread_id": thread_id,
                        "path": path,
                        "breaker_state": self.breaker.state.value,
                        "degraded": True,
                    },
                )
            else:
                context_bits = []

        # SoR read — authoritative; always attempted for booking decisions
        sor_data = self.sor.read(resource_id)
        decision = resolve_preference(memory_pref, sor_data["seats"])
        if path != "memory_retrieve" or degraded:
            # Final fallback label when memory path failed entirely
            if memory_pref is None and degraded:
                path = "sor_read" if path == "sliding_window" else path
        # Explicit SoR-only path when breaker open and no pref from window
        if degraded and memory_pref is None:
            path = "sor_read"

        if decision["available"]:
            answer = (
                f"Booked {decision['seat']} on {resource_id} @ ${sor_data['price_usd']} "
                f"(source={decision['source']}; ctx={len(context_bits)})"
            )
        else:
            answer = (
                f"No seats on {resource_id}; preference retained="
                f"{decision.get('memory_pref_retained')}"
            )

        # Learn slowly: only persist redacted preference when user states lasting intent
        if "always" in safe_text.lower() and "window" in safe_text.lower():
            self.store.add(user_id, "seat_preference", "window", topic=topic)
            self.audit.append(
                {
                    "event": "memory_write",
                    "correlation_id": correlation_id,
                    "user_id": user_id,
                    "key": "seat_preference",
                    "op": "ADD",
                }
            )

        values = {
            "messages": messages,
            "last_resource": resource_id,
            "last_decision": decision,
        }
        ckpt = self.checkpointer.save(thread_id, values, next_steps=["confirm_or_next"])
        self.audit.append(
            {
                "event": "checkpoint",
                "correlation_id": correlation_id,
                "thread_id": thread_id,
                "checkpoint_id": ckpt.checkpoint_id,
                "parent_id": ckpt.parent_id,
                "content_hash": ckpt.content_hash,
            }
        )

        LOG.info(
            "turn_complete",
            extra={
                "correlation_id": correlation_id,
                "thread_id": thread_id,
                "path": path,
                "degraded": degraded,
                "breaker_state": self.breaker.state.value,
            },
        )
        return TurnResult(
            correlation_id=correlation_id,
            thread_id=thread_id,
            path=path,
            answer=answer,
            checkpoint_id=ckpt.checkpoint_id,
            degraded=degraded,
            decision=decision,
        )


def main() -> None:
    rng = random.Random(42)
    audit = AuditLog()
    ckpt = Checkpointer()
    # Fail first 3 search calls → trips breaker (threshold 3), then recovers
    store = MemoryStore(fail_times=3)
    store.add("user-1", "seat_preference", "window", topic="flight")
    sor = SystemOfRecord()
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout_s=0.01, clock=time.time)
    agent = MemoryStateAgent(ckpt, store, sor, audit, breaker, rng)

    # 1) Memory path fails (transient) → fallback; SoR has no window on AA100 → aisle
    r1 = agent.run(
        user_id="user-1",
        thread_id="t-1",
        user_text="Book AA100 please",
        resource_id="flight:AA100",
    )
    assert r1.degraded is True
    assert r1.decision["source"] == "sor"
    assert r1.decision["seat"] == "aisle"
    assert r1.decision.get("memory_pref_retained") in (None, "window")
    # During failures, preference may not load — SoR still books aisle
    assert "aisle" in r1.answer

    # Allow breaker recovery
    time.sleep(0.02)

    # 2) After failures exhausted, search succeeds; UA200 has window — memory+sor
    r2 = agent.run(
        user_id="user-1",
        thread_id="t-1",
        user_text="Try UA200 instead",
        resource_id="flight:UA200",
    )
    assert r2.degraded is False
    assert r2.path == "memory_retrieve"
    assert r2.decision["seat"] == "window"
    assert r2.decision["source"] == "memory+sor"

    # 3) Checkpoint chain linked
    history = ckpt.history("t-1")
    assert len(history) == 2
    assert history[1].parent_id == history[0].checkpoint_id

    # 4) SoR wins even when memory wants window on AA100 (0 window seats)
    store2 = MemoryStore(fail_times=0)
    store2.add("user-1", "seat_preference", "window", topic="flight")
    agent2 = MemoryStateAgent(
        Checkpointer(), store2, sor, AuditLog(), CircuitBreaker(), random.Random(0)
    )
    r3 = agent2.run(
        user_id="user-1",
        thread_id="t-2",
        user_text="Book AA100",
        resource_id="flight:AA100",
    )
    assert r3.decision["seat"] == "aisle"
    assert r3.decision["source"] == "sor"
    assert r3.decision["memory_pref_retained"] == "window"

    # 5) PII redacted before lasting memory write
    agent2.run(
        user_id="user-1",
        thread_id="t-2",
        user_text="Always prefer window — my email is jane@example.com",
        resource_id="flight:UA200",
    )
    assert any(e.get("event") == "pii_redaction" and e.get("emails") == 1 for e in agent2.audit.entries)

    print(json.dumps({
        "r1_path": r1.path,
        "r1_seat": r1.decision["seat"],
        "r2_path": r2.path,
        "r2_seat": r2.decision["seat"],
        "r3_source": r3.decision["source"],
        "checkpoints": len(history),
        "ok": True,
    }))


if __name__ == "__main__":
    main()
```

Run: `python3 modules/10-ai-agent-memory-state-consistency.md` is not valid — extract the code block or copy to a `.py` file. From a scratch file:

```bash
python3 -c "exec(open('…').read())"  # or paste into agent_memory_state.py && python3 agent_memory_state.py
```

Expected stdout includes `"ok": true`, `r2_seat=window`, `r3_source=sor`.

---

## Part 6 — Architectural System Design Scenarios

### Scenario 1 — Multi-session travel concierge (10M MAU personalization)

**Problem**: Design memory/state for a travel concierge handling multi-week trip planning across sessions. Must resume mid-booking after crashes/HITL, personalize from past trips, never invent inventory, keep interactive p95 near Mem0-class (~**1.5 s** retrieve+answer budget), and control checkpoint growth for threads with 100+ tool turns.

**Proposed architecture** (component diagram)

```
                    ┌─────────────────────────────────────────┐
                    │           CONTROL PLANE                 │
                    │  trip graph · preference classifier     │
                    │  write gate (lasting vs one-time)       │
                    │  SoR-wins · compaction trigger 150k     │
                    └───────────────┬─────────────────────────┘
                                    │
                    ┌───────────────▼─────────────────────────┐
                    │            DATA PLANE                   │
                    │  working state: step · constraints      │
                    │  scoped retrieve (flight≠hotel)         │
                    └─────┬─────────────┬─────────────┬───────┘
                          │             │             │
               ┌──────────▼───┐  ┌──────▼──────┐  ┌───▼──────────┐
               │ TOOL PROXIES │  │ PERSISTENCE │  │  TELEMETRY   │
               ├──────────────┤  ├─────────────┤  ├──────────────┤
               │ GDS / hotel  │  │ Postgres    │  │ tok · p95    │
               │ MCP memory   │  │ checkpointer│  │ ckpt bytes   │
               │ fare quote   │  │ + DeltaChan │  │ retrieval %  │
               │              │  │ Mem0/Store  │  │ correlation  │
               └──────────────┘  └─────────────┘  └──────────────┘
```

**Trade-off matrix**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **A. Full history in prompt** | Highest (~\$81/1k @ labeled Sonnet book on 26k prompts) | Worst p95 ~**17 s** (LOCOMO) | Lowest | Full transcript retention risk | Short sessions only |
| **B. Sliding window only** | Low | Low | Low | Smaller retention surface | Weak early-constraint recall |
| **C. Checkpointer + Store/Mem0 + SoR (recommended)** | Medium (write-path LLM + DB); read ~\$8.29/1k **[inferred]** | Best published interactive p95 ~**1.44 s** | Medium–high (prune/delta) | Namespace RBAC + PII gate | Multi-session; checkpoint **5.3 GB→129 MB** with deltas |

**Decision rationale**: **C** wins under personalization + HITL resume constraints. Accept **J 66.9% vs 72.9%** on LOCOMO-style QA in exchange for ~**90%** token and p95 cuts; spend full-context or heavier retrieve only on payment/ticket confirmation steps. DeltaChannel + retention cron are mandatory once coding-like tool threads grow ([Mem0 paper](https://arxiv.org/abs/2504.19413); [Delta Channels](https://www.langchain.com/blog/delta-channels-evolving-agent-runtime); [Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory)).

---

### Scenario 2 — Regulated claims assistant (PII-heavy, audit-mandatory)

**Problem**: Design state/memory for an insurance claims agent. Thread must pause for adjuster HITL, long-term memory may hold contact prefs but **not** raw SSNs/claim medical text, every durable write audited, and memory/SoR conflicts (coverage limits) must resolve to policy admin SoR. Target availability **99.9%** with memory-path degradation allowed.

**Proposed architecture** (component diagram)

```
         ┌──────────────────────────────────────────────────┐
         │                 CONTROL PLANE                    │
         │  claims graph · HITL interrupt · policy engine   │
         │  memory write = detect→redact→approve→audit      │
         └──────────────────────┬───────────────────────────┘
                                │
         ┌──────────────────────▼───────────────────────────┐
         │                  DATA PLANE                      │
         │  working claim state · redacted context only     │
         └──────┬─────────────────┬─────────────────┬───────┘
                │                 │                 │
     ┌──────────▼────┐   ┌────────▼────────┐  ┌────▼────────────┐
     │ TOOL PROXIES  │   │  PERSISTENCE    │  │   TELEMETRY     │
     ├───────────────┤   ├─────────────────┤  ├─────────────────┤
     │ MCP memory    │   │ checkpointer    │  │ immutable audit │
     │  (scoped JWT) │   │  chain (hash)   │  │ breaker state   │
     │ Policy SoR    │   │ Store (prefs)   │  │ RPO/RTO probes  │
     │ Doc vault API │   │ Doc vault = SoR │  │ correlation IDs │
     └───────────────┘   └─────────────────┘  └─────────────────┘
```

**Trade-off matrix**

| Approach | Cost | Latency | Ops | Security | Scalability |
| --- | --- | --- | --- | --- | --- |
| **A. Embed full claim PDF in context** | Very high tokens; re-send each turn | High; context rot | Low | Worst — PII in every prompt/log | Fails at long claims |
| **B. Memory-only facts, no SoR re-fetch** | Low read cost | Low | Medium | Dangerous — stale coverage/limits | Fast but non-compliant |
| **C. Checkpoint + redacted Store + SoR policy engine (recommended)** | Medium (vault fetches + gates) | Medium (SoR round-trips on money paths) | Higher (RBAC, audit, breakers) | Strongest: Zero-Trust MCP, namespace RBAC, immutable ckpt chain | Horizontal Store + thread shards |

**Decision rationale**: **C** is the only posture that satisfies compliance. Memory holds **contact/channel prefs** post-redaction; coverage limits and reserve amounts always re-read from policy SoR. Fallback **memory → sliding window → SoR** keeps intake available when Store is down. Immutable checkpoint chain + memory audit satisfy chain-of-custody for regulators **[inferred]** controls layered on LangGraph/Mem0 primitives ([Newsletter](https://newsletter.systemdesign.one/p/ai-agent-memory); research §4).

---

### Interview prompts (after both scenarios)

1. Walk the four-layer model (working state vs checkpoint vs Store vs SoR) for a user who says “window seat this time” but the airline API has zero window seats — what is written where?  
2. Derive **\$/1k runs** for full-context vs Mem0 using LOCOMO **26,031** vs **1,764** tokens and a price book you state explicitly. What did you exclude (write path)?  
3. Why is **J 66.9% vs 72.9%** an acceptable trade for a chat UX but not for a wire-transfer confirmation agent?  
4. Explain checkpointer pending writes vs Store durability — which restores a crashed parallel super-step?  
5. Draw the closed→open→half-open breaker on the memory path and the fallback chain to SoR.  
6. Where do you enforce PII detect→redact→audit relative to Mem0 `add`, and what is namespace RBAC keyed on?  
7. Checkpoint storage grew **5.3 GB → 129 MB** — what mechanism, and what invariant must deltas preserve?  
8. Design Scenario 2’s RPO/RTO if background memory writes lag HITL resume by 30 s.

---

## Sources (selected)

- [System Design Newsletter — AI Agent Memory](https://newsletter.systemdesign.one/p/ai-agent-memory)  
- [LangGraph Persistence / Checkpointers / Stores](https://docs.langchain.com/oss/python/langgraph/persistence)  
- [LangChain — Memory for agents](https://www.langchain.com/blog/memory-for-agents) · [Delta Channels](https://www.langchain.com/blog/delta-channels-evolving-agent-runtime)  
- [Mem0 paper arXiv:2504.19413](https://arxiv.org/abs/2504.19413) · [CoALA arXiv:2309.02427](https://arxiv.org/abs/2309.02427)  
- [Anthropic — Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) · [Building effective agents](https://www.anthropic.com/research/building-effective-agents)  
- [AI Incident Database #1152](https://incidentdatabase.ai/cite/1152/)
