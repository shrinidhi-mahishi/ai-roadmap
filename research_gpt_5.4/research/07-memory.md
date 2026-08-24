# Research: Memory - Short-term, long-term, semantic, episodic, memory retrieval

**Date researched**: 2026-08-21
**Sources consulted**: 29

---

## 1. System Topology & Mechanics

`Memory` in production agent systems is not one substrate; the public architecture in this local source set separates it into at least four planes: `short-term conversational state`, `long-term stored facts`, `retrieval memory`, and `cache memory`. LangChain's current context model distinguishes static runtime context, dynamic runtime state, and dynamic cross-conversation context, while its memory docs split memory into short-term and long-term forms ([LangChain Context](https://docs.langchain.com/oss/python/concepts/context), [LangChain Memory](https://docs.langchain.com/oss/python/concepts/memory)).

In `LangGraph`, short-term memory is thread-scoped state persisted by checkpointers, while long-term memory is stored as JSON documents in namespaced stores; operationally, this is "hot trajectory state in checkpoints, durable facts in stores" rather than "entire history replayed forever" ([LangChain Memory](https://docs.langchain.com/oss/python/concepts/memory), [LangGraph Add Memory](https://docs.langchain.com/oss/python/langgraph/add-memory), [LangGraph Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)).

`Google ADK` uses an even more explicit taxonomy: `Session` is the conversation/thread container, `State` is session-scoped scratch data, and `Memory` is a separate searchable cross-session store managed by a `MemoryService` ([ADK sessions overview](https://adk.dev/sessions/), [ADK memory](https://adk.dev/sessions/memory/)). That is one of the clearest documented distinctions between working memory and durable memory in the current agent-framework landscape.

`OpenAI Agents SDK` is less prescriptive about named memory tiers but still exposes distinct state channels: local or server-backed `Session` history for conversational continuity, plus `conversation_id` or `previous_response_id` continuations that let the Responses API carry prior turns without resending the full transcript ([OpenAI sessions](https://openai.github.io/openai-agents-python/sessions/), [OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)). Mechanically, that is still short-term conversational memory, even if the SDK does not formalize a separate semantic-memory service [inferred].

Within this source set, the cleanest mapping to human memory terms is:

- `short-term / working memory`: recent turns, tool outputs, and workflow-local state kept in sessions or thread checkpoints.
- `semantic memory`: durable facts such as user profile, preferences, policies, or domain knowledge persisted in long-term stores or retrievable corpora.
- `episodic memory`: conversation- or task-specific traces that preserve what happened in a particular thread/run, including checkpoints, event history, or activity logs.

That taxonomy is a synthesis over the documented storage primitives rather than a verbatim vendor definition ([LangChain Memory](https://docs.langchain.com/oss/python/concepts/memory), [LangGraph Add Memory](https://docs.langchain.com/oss/python/langgraph/add-memory), [ADK sessions overview](https://adk.dev/sessions/), [Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)) [inferred].

`Memory retrieval` also has multiple topologies. For stable reusable prompt prefixes, OpenAI, Anthropic, and Gemini expose exact-prefix or explicit cache retrieval keyed by request structure rather than meaning ([OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching), [Anthropic Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), [Gemini Context Caching Guide](https://ai.google.dev/gemini-api/docs/generate-content/caching)). For durable knowledge retrieval, RAG-style systems query a non-parametric memory such as a vector index, hybrid index, or graph-derived knowledge representation ([Lewis et al., 2020](https://arxiv.org/html/2005.11401v4), [Azure hybrid search overview](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview), [GraphRAG docs](https://microsoft.github.io/graphrag/)).

## 2. Token Economics & NFR Metrics

> ⚠️ Limited public data available for stable `p50/p95/p99` latency specifically for memory-heavy agent workloads. The local source set is much stronger on billing units, cache thresholds, and retrieval pipeline limits than on percentile SLAs.

The main memory-cost fact is that every memory strategy moves cost between `write`, `storage`, and `read` rather than making it disappear. Session replay pays repeatedly in prompt tokens, explicit prompt caches pay an up-front write premium and then discounted reads, and retrieval memory pays query plus rerank cost when semantic search or agentic retrieval is involved ([OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching), [Anthropic Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), [Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview), [Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)).

For exact-prefix cache memory, the core billing logic is already explicit in the existing research set:

```text
memory_augmented_run_cost
  ~= uncached_input_tokens * input_rate
   + cached_read_tokens * cached_rate
   + cache_write_tokens * write_rate
   + output_tokens * output_rate
   + retrieval_or_rerank_surcharges
```

([OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching), [Anthropic Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)) [inferred]

`OpenAI` documents the strongest exact-prefix cache economics in this source set: cacheable prefixes require at least `1,024` tokens on GPT-5.6-and-later families, cached reads cost `0.1x` ordinary input, cache writes cost `1.25x`, and TTL can be set to `30 minutes` via `prompt_cache_options.ttl` ([OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching)). `Anthropic` uses the same `0.1x` read / `1.25x` 5-minute write structure, plus a `2x` 1-hour write tier; because of that, a 5-minute cache becomes cheaper on first reuse, while a 1-hour cache becomes cheaper on second reuse ([Anthropic Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)) [inferred].

`Gemini` makes memory more resource-like than prefix-like. Explicit caches have a default `1 hour` TTL if not set, support TTL mutation, and charge both discounted cache-use tokens and storage rent per million tokens per hour; minimum cache sizes are `2,048` tokens for Gemini `2.x` and `4,096` for Gemini `3` families in the cited docs ([Gemini Context Caching Guide](https://ai.google.dev/gemini-api/docs/generate-content/caching), [Gemini Enterprise Context Caching](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/context-cache/context-cache-overview)).

For retrieval memory, the main NFR lever is candidate volume. Azure semantic ranker only reranks the top `50` preranked results and can consume up to `2,000` tokens per document in its summarization stage, while Azure's agentic retrieval cost example assumes `3` subqueries, `50` reranked chunks per subquery, and `500` tokens per chunk, totaling `150M` reranking tokens across `2,000` retrievals ([Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview), [Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)).

That yields a practical sizing rule for memory retrieval:

```text
retrieval_memory_token_load
  ~= subqueries * retrieved_candidates * avg_tokens_per_candidate
```

([Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview), [Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)) [inferred]

`ADK` exposes an important NFR control for short-term memory growth: context compaction summarizes older history once a token threshold or sliding-window / turn threshold is reached, while `event_retention_size` preserves a recent raw tail for coherence ([ADK context compaction](https://adk.dev/context/compaction/)). `OpenAI Agents SDK` offers similar outcomes more manually through `SessionSettings(limit=N)`, `session_input_callback`, `call_model_input_filter`, and `nest_handoff_history` ([OpenAI sessions](https://openai.github.io/openai-agents-python/sessions/), [OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)).

## 3. Distributed Resilience & State

The central resilience question for memory systems is whether they store `trajectory`, `facts`, or `retrieval artifacts`, because those state types fail differently. `LangGraph` stores trajectory state with per-super-step checkpoints keyed by `thread_id` and also persists pending writes from successful sibling nodes inside the same super-step ([LangGraph Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)). That makes it well suited for episodic task memory that must survive pauses or partial failures.

Because `LangGraph` separates thread checkpoints from long-term stores, short-lived conversation state and durable profile/domain memory can recover independently ([LangChain Memory](https://docs.langchain.com/oss/python/concepts/memory), [LangGraph Add Memory](https://docs.langchain.com/oss/python/langgraph/add-memory)). That is a stronger resilience pattern than collapsing both into one giant transcript [inferred].

`Google ADK` provides the clearest published concurrency controls. `DatabaseSessionService` uses in-process locking for same-session updates inside one process and row-level locking with `SELECT ... FOR UPDATE` for PostgreSQL/MySQL/MariaDB across replicas, while `MemoryService` keeps cross-session memory as a separate concern from session state ([ADK session service](https://adk.dev/sessions/session/), [ADK sessions overview](https://adk.dev/sessions/), [ADK memory](https://adk.dev/sessions/memory/)).

`OpenAI Agents SDK` persists session history before and after runs, and resumable approval flows serialize `RunState`; however, the framework docs stop short of claiming a full distributed workflow or memory engine and instead recommend external orchestrators such as Dapr, Temporal, Restate, or DBOS for longer-lived durable execution ([OpenAI sessions](https://openai.github.io/openai-agents-python/sessions/), [OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)).

Exact-prefix cache memory has a more fragile state model than session or store memory. Anthropic's backward lookup only searches up to `20` blocks from a breakpoint, and OpenAI exact-prefix caching requires the shared prefix to remain byte-for-byte stable at eligible breakpoints ([Anthropic Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), [OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching)). This means cache retrieval is durable only if serialization is stable; otherwise, the cache silently degrades into write-heavy miss traffic [inferred].

Semantic cache memory broadens hit rates but weakens exactness guarantees. Redis LangCache supports `exact` and `semantic` search modes, configurable similarity thresholds, TTLs, and eviction policies, so reliability depends partly on retrieval threshold tuning rather than only on storage durability ([Redis LangCache Docs](https://redis.io/docs/latest/develop/ai/context-engine/langcache/), [LangCache API Examples](https://redis.io/docs/latest/develop/ai/langcache/api-examples/)).

Retrieval memory systems add another state layer: the index itself. In classic RAG, the non-parametric memory can be rebuilt or replaced independently of the generator ([Lewis et al., 2020](https://arxiv.org/html/2005.11401v4)). In GraphRAG, persisted artifacts include `TextUnits`, extracted entities/relations, communities, and community summaries, so "memory" is not just embeddings but a structured intermediate representation reused across many queries ([GraphRAG docs](https://microsoft.github.io/graphrag/), [From Local to Global](https://r.jordan.im/download/language-models/2404.16130v1.pdf)).

## 4. Enterprise Security & Governance

Memory systems create a governance problem because they are durable by design: once a preference, profile fact, tool output, or retrieved snippet enters memory, later runs may surface it automatically. The strongest documented mitigation in this local source set is not a dedicated "memory policy plane" but **strict trust-boundary handling** around what gets written into memory and where it is later injected.

`OpenAI` treats prompt injection as a context-engineering problem and advises against concatenating untrusted external data into developer instructions; instead, data should move through structured outputs or isolated channels ([OpenAI Safety in Building Agents](https://developers.openai.com/api/docs/guides/agent-builder-safety)). `Anthropic` is more concrete: third-party or tool-returned content should be passed only in `tool_result` blocks, not upgraded into `system` prompts or plain user text, and tool outputs can be screened by a smaller model before the main model sees them ([Anthropic Mitigate Jailbreaks](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks)).

For memory, that implies a useful governance rule: `semantic memory writes should be lower-trust and more validated than episodic logs`, because semantic memory is designed for reuse across future tasks and users' later sessions [inferred]. A bad episodic event pollutes one run; a bad semantic memory can poison many future runs.

`Azure AI Search` is the strongest cited source on permission-aware retrieval memory. Microsoft positions the knowledge layer as producing `permission-aware knowledge bases`, and agentic retrieval can be exposed through `retrieve` or `MCP` interfaces with role- or key-based access controls ([Azure hybrid search overview](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview), [Query a knowledge base using retrieve or MCP](https://learn.microsoft.com/en-us/azure/search/search-agentic-retrieval-how-to-retrieve)). That is important because retrieval memory is only safe if authorization propagates into result filtering, not only into index administration [inferred].

`Anthropic` explicitly warns that `allowed_callers` is not a hard security boundary for tools, and the same logic applies to memory-connected tools or stores: visibility hints are not substitutes for enforcement ([Anthropic Programmatic Tool Calling](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling)). Memory services therefore need real application-side policy checks, especially when profile or organization knowledge spans tenants [inferred].

> ⚠️ Limited public data available for first-party `PII redaction` pipelines before memory writes, immutable audit-log schemas for long-term memory changes, or built-in RBAC hierarchies over semantic/episodic memory stores in the frameworks cited here.

## 5. Production Failure Modes

`Context-window degradation` is the obvious short-term-memory failure mode. "Lost in the Middle" found that relevant information in the middle of long context is often used worse than evidence near the beginning or end, and RULER showed that retrieval-style benchmarks can overstate actual long-context robustness as context grows ([Lost in the Middle](https://aclanthology.org/2024.tacl-1.9/), [RULER](https://arxiv.org/abs/2404.06654)). If short-term memory is just "keep adding transcript," working memory quality can drop long before the window is technically full.

`Memory overgrowth` therefore needs an active policy. `ADK` addresses this with token-threshold and turn-threshold compaction plus a retained recent-event tail, while `OpenAI Agents SDK` provides trimming and filtering hooks rather than one built-in compactor ([ADK context compaction](https://adk.dev/context/compaction/), [OpenAI sessions](https://openai.github.io/openai-agents-python/sessions/), [OpenAI running agents](https://openai.github.io/openai-agents-python/running_agents/)).

`Semantic cache false positives` are a distinct memory bug class. Redis LangCache can match by semantic similarity rather than exact prefix, which improves hit rate but introduces the risk that "close enough" prior answers are reused where the hidden constraints differ ([Redis LangCache Docs](https://redis.io/docs/latest/develop/ai/context-engine/langcache/), [LangCache API Examples](https://redis.io/docs/latest/develop/ai/langcache/api-examples/)). That is a retrieval-quality error disguised as a cache hit [inferred].

`Exact-prefix cache thrash` is the opposite failure mode: the system keeps paying cache writes without earning cache reads because serialization changes or the shared prefix no longer reaches the required breakpoint. OpenAI explicitly recommends monitoring `cache_write_tokens` versus `cached_tokens`, and Anthropic documents block-position sensitivity plus the `20`-block backward lookup constraint ([OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching), [Anthropic Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).

`Retrieval starvation` is a long-term-memory failure mode. Azure semantic ranker only reranks the top `50` first-stage hits, so if the correct memory item never enters the candidate set, the memory layer appears authoritative while actually missing the needed evidence ([Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview)). The same bounded-candidate problem exists for external rerank APIs and agentic retrieval flows [inferred].

`Memory poisoning` is the most serious governance failure. If untrusted retrieved text, tool outputs, or user content are promoted into durable semantic memory without validation, future runs can be steered by malicious or stale state. OpenAI and Anthropic both recommend strict separation of low-trust content from high-trust instruction channels precisely to reduce this risk ([OpenAI Safety in Building Agents](https://developers.openai.com/api/docs/guides/agent-builder-safety), [Anthropic Mitigate Jailbreaks](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks)).

`Graph memory cost blow-up` is another practical failure. Microsoft introduced `LazyGraphRAG` because full GraphRAG's up-front summarization/indexing can be too expensive for one-off or fast-changing workloads; the cited post claims indexing cost drops to `0.1%` of full GraphRAG while global-query cost in one compared setup is `>700x` lower ([LazyGraphRAG](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/)). Here the failure is not wrong memory retrieval but an economically unsustainable memory construction pipeline.

## 6. Enterprise System Design Scenarios

### 6.1 Memory pattern matrix

| Memory pattern | Best fit | Strongest documented strengths | Main trade-offs |
| --- | --- | --- | --- |
| `Thread/session memory` | Multi-turn chat, approvals, active workflow state | Simple continuity, checkpoint/resume semantics, local reasoning context ([LangGraph Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers), [OpenAI sessions](https://openai.github.io/openai-agents-python/sessions/)) | Grows prompt cost quickly; vulnerable to context-window degradation |
| `Long-term semantic store` | Durable user/org facts, preferences, policies | Reusable across sessions; cleaner than replaying all history ([LangChain Memory](https://docs.langchain.com/oss/python/concepts/memory), [LangGraph Add Memory](https://docs.langchain.com/oss/python/langgraph/add-memory), [ADK memory](https://adk.dev/sessions/memory/)) | Requires validation, versioning, and auth to avoid stale or poisoned facts |
| `Exact-prefix cache memory` | Stable large prefixes: policies, schemas, repeated docs | Deterministic cost savings and lower repeated input latency ([OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching), [Anthropic Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), [Gemini Context Caching Guide](https://ai.google.dev/gemini-api/docs/generate-content/caching)) | Brittle to serialization drift; poor fit for semantically similar but non-identical inputs |
| `Semantic cache memory` | FAQ/support workloads with paraphrased repeats | High hit-rate potential for near-duplicates ([Redis LangCache Docs](https://redis.io/docs/latest/develop/ai/context-engine/langcache/), [LangCache API Examples](https://redis.io/docs/latest/develop/ai/langcache/api-examples/)) | False-positive reuse risk; requires similarity-threshold tuning |
| `Retrieval memory (RAG / hybrid)` | Large mutable corpora and domain knowledge | Non-parametric memory can refresh without retraining; hybrid retrieval covers semantic + exact-match queries ([Lewis et al., 2020](https://arxiv.org/html/2005.11401v4), [Azure hybrid search overview](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview)) | Quality depends on candidate recall; rerankers cannot recover unseen evidence |
| `Graph memory` | Corpus-wide reasoning, entity-centric discovery, global summarization | Structured entities/relations/community summaries for global and local query modes ([GraphRAG docs](https://microsoft.github.io/graphrag/), [From Local to Global](https://r.jordan.im/download/language-models/2404.16130v1.pdf)) | Heavy indexing/prompt-tuning cost unless a lighter variant such as LazyGraphRAG is used |

### 6.2 Recommended deployment patterns

**Pattern A: SaaS copilot with repeat users and stable product policies**

Use a layered memory stack: thread/session memory for the active conversation, semantic profile memory for durable user preferences, and exact-prefix caching for long stable instruction/tool prefixes ([LangGraph Add Memory](https://docs.langchain.com/oss/python/langgraph/add-memory), [OpenAI sessions](https://openai.github.io/openai-agents-python/sessions/), [OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching)). This keeps working memory small while still reusing durable facts and cached static context.

**Pattern B: Enterprise assistant over internal documents**

Use retrieval memory rather than writing large corpora into profile-style memory. Hybrid search plus reranking is the safer default because it preserves exact-match recall for names, codes, and dates while still using semantic similarity for prose ([Azure hybrid search overview](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview), [Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview)).

**Pattern C: Regulated multi-tenant assistant**

Treat memory write paths and memory read paths as separate control planes: validate low-trust inputs before durable writes, enforce authorization at retrieval time, and never let retrieved memory bypass instruction-isolation rules ([OpenAI Safety in Building Agents](https://developers.openai.com/api/docs/guides/agent-builder-safety), [Anthropic Mitigate Jailbreaks](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks), [Query a knowledge base using retrieve or MCP](https://learn.microsoft.com/en-us/azure/search/search-agentic-retrieval-how-to-retrieve)) [inferred].

**Pattern D: Research/analysis assistant over narrative corpora**

Use graph-derived memory only when the dominant questions are thematic, global, or relationship-heavy. For local fact lookup, standard hybrid retrieval is cheaper and simpler; for corpus-wide sensemaking, GraphRAG or LazyGraphRAG can justify the added complexity ([GraphRAG docs](https://microsoft.github.io/graphrag/), [From Local to Global](https://r.jordan.im/download/language-models/2404.16130v1.pdf), [LazyGraphRAG](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/)).

### 6.3 Capacity-planning heuristics

Useful first-order formulas:

```text
working_memory_prompt
  ~= recent_turn_tokens
   + retrieved_memory_tokens
   + retrieved_document_tokens
   + tool_schema_tokens
```

([OpenAI sessions](https://openai.github.io/openai-agents-python/sessions/), [ADK context compaction](https://adk.dev/context/compaction/), [Azure semantic ranking overview](https://learn.microsoft.com/en-us/azure/search/semantic-search-overview)) [inferred]

```text
cache_break_even_uses
  ~= first reuse for 1.25x write / 0.1x read caches
```

([OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching), [Anthropic Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)) [inferred]

```text
retrieval_memory_latency
  ~= planning_latency
   + max(parallel_retrieval_branches)
   + reranking_latency
   + answer_synthesis_latency
```

([Azure agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)) [inferred]

### 6.4 Strongest practical conclusions

1. The most production-relevant memory split is not "one memory" but `working memory + durable semantic memory + retrieval memory + cache memory`.
2. `Episodic` memory is best implemented as thread/run history with checkpoint or event semantics; `semantic` memory is best implemented as validated durable facts or retrievable knowledge objects rather than raw transcripts ([LangGraph Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers), [ADK memory](https://adk.dev/sessions/memory/), [LangGraph Add Memory](https://docs.langchain.com/oss/python/langgraph/add-memory)) [inferred].
3. Exact-prefix caches are the cheapest memory to operate when reuse is high and formatting is stable; retrieval memory is the most flexible when knowledge changes often; graph memory is justified only when questions require corpus-level synthesis.
4. The hardest unsolved public problem is not storing memory but governing it: validation, authorization, poisoning resistance, and stale-fact handling are still more application responsibilities than first-class framework guarantees.

## Sources

- [1] https://docs.langchain.com/oss/python/concepts/context - LangChain context categories and context-engineering framing.
- [2] https://docs.langchain.com/oss/python/concepts/memory - LangChain short-term and long-term memory concepts.
- [3] https://docs.langchain.com/oss/python/langgraph/add-memory - LangGraph memory patterns, stores, trimming, and summarization.
- [4] https://docs.langchain.com/oss/python/langgraph/checkpointers - LangGraph checkpointing, thread state, and pending writes.
- [5] https://openai.github.io/openai-agents-python/sessions/ - OpenAI session persistence, history shaping, and continuation behavior.
- [6] https://openai.github.io/openai-agents-python/running_agents/ - OpenAI run loop, continuation models, and durable-execution integrations.
- [7] https://developers.openai.com/api/docs/guides/prompt-caching - OpenAI prompt-cache thresholds, TTL, and billing semantics.
- [8] https://developers.openai.com/api/docs/guides/agent-builder-safety - OpenAI guidance on isolating untrusted data and prompt-injection risk.
- [9] https://platform.claude.com/docs/en/build-with-claude/prompt-caching - Anthropic cache modes, thresholds, pricing, and lookup behavior.
- [10] https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks - Anthropic isolation guidance for tool outputs and untrusted content.
- [11] https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling - Anthropic `allowed_callers` caveat and programmatic tool behavior.
- [12] https://adk.dev/sessions/ - ADK Session, State, and Memory model.
- [13] https://adk.dev/sessions/memory/ - ADK long-term searchable memory abstractions.
- [14] https://adk.dev/sessions/session/ - ADK session-service locking and persistence guidance.
- [15] https://adk.dev/context/compaction/ - ADK token-threshold and sliding-window compaction.
- [16] https://ai.google.dev/gemini-api/docs/generate-content/caching - Gemini cache behavior, TTL, and token thresholds.
- [17] https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/context-cache/context-cache-overview - Gemini Enterprise cache modes, discounts, and storage model.
- [18] https://redis.io/docs/latest/develop/ai/context-engine/langcache/ - Redis LangCache exact vs semantic cache behavior.
- [19] https://redis.io/docs/latest/develop/ai/langcache/api-examples/ - LangCache API examples and threshold-based retrieval behavior.
- [20] https://arxiv.org/html/2005.11401v4 - Original RAG paper framing non-parametric memory.
- [21] https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview - Hybrid lexical + vector retrieval and permission-aware knowledge-layer positioning.
- [22] https://learn.microsoft.com/en-us/azure/search/semantic-search-overview - Azure semantic reranking limits and token behavior.
- [23] https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview - Agentic retrieval planning, fan-out, activity log, and cost example.
- [24] https://learn.microsoft.com/en-us/azure/search/search-agentic-retrieval-how-to-retrieve - Retrieval and MCP access-control prerequisites for Azure knowledge bases.
- [25] https://microsoft.github.io/graphrag/ - GraphRAG indexing pipeline and global/local/drift query modes.
- [26] https://r.jordan.im/download/language-models/2404.16130v1.pdf - GraphRAG paper with structured graph memory and global summarization.
- [27] https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/ - LazyGraphRAG cost-quality trade-offs.
- [28] https://aclanthology.org/2024.tacl-1.9/ - "Lost in the Middle" long-context degradation benchmark.
- [29] https://arxiv.org/abs/2404.06654 - RULER long-context benchmark.
