# Research: Memory - Short/Long-Term, Semantic, Episodic, Memory Retrieval

**Date researched**: 2026-08-21
**Sources consulted**: 35

## Scope and evidence labels

This brief treats agent memory as security-relevant application state, not as a synonym for chat history or a vector database. It covers short-term and long-term memory, semantic and episodic representations, and memory retrieval across the full write-retrieve-update-forget lifecycle. Plain factual claims are sourced from first-party documentation or primary papers. `[inferred]` marks an engineering recommendation derived from those sources. Paper-reported benchmarks are labeled as such and are not assumed to transfer to other models, corpora, or production workloads.

## 1. System Topology & Mechanics

### A practical taxonomy

Framework documentation is converging on a useful scope distinction. LangGraph defines short-term memory as thread-scoped state and long-term memory as data stored across sessions under custom namespaces. It further uses the cognitive categories semantic facts, episodic experiences, and procedural rules. [[1]](https://docs.langchain.com/oss/python/concepts/memory) Google ADK similarly distinguishes `Session`/`State` for one conversation from searchable cross-session information managed by `MemoryService`. [[6]](https://adk.dev/sessions/)

| Memory type | Scope | Typical representation | Examples | Truth/update semantics |
|---|---|---|---|---|
| Working / short-term | one invocation or thread | messages, tool results, task variables, checkpoints | current cart, pending approval, active plan | frequently mutable; task-owned |
| Semantic long-term | user, team, tenant, or application | atomic facts, profile document, typed entity/relationship | preferred language, account tier, project convention | corrected/invalidated as facts change |
| Episodic long-term | prior interaction or task episode | timestamped event/trajectory plus outcome and feedback | failed deployment and fix, past support case | append-oriented; preserve what happened |
| Procedural long-term | agent/application policy or learned method | instruction, checklist, skill, exemplar | release steps, response style, successful workflow | tightly governed because it changes behavior |

The user explicitly named semantic and episodic memory; procedural memory is included because many systems store "lessons" or instructions from past runs. Treating those instructions as ordinary facts creates a privilege problem.

**Short-term is a scope, not necessarily volatile storage.** A thread can persist for months while remaining short-term in the conceptual sense because it belongs to one conversation. LangGraph checkpointers save graph state by thread; OpenAI Agents SDK sessions store conversation items for a session; ADK sessions hold events and state. [[2]](https://docs.langchain.com/oss/python/langgraph/add-memory) [[4]](https://openai.github.io/openai-agents-python/sessions/) [[7]](https://adk.dev/sessions/state/)

**Long-term does not mean inject on every turn.** It means the information can survive and be recalled across threads. Most long-term items should remain out of context until a retrieval policy selects them.

### Short-term memory mechanics

A thread record normally contains:

```text
thread_id, subject_id, tenant_id
event sequence: user/model/tool/approval events
task state: intent, plan, intermediate artifacts, budgets
checkpoint/version and last update time
summary/compaction artifacts with source event range
retention and authorization metadata
```

LangGraph stores checkpoints at graph steps and can resume a thread, inspect prior state, fork it, and retain successful pending writes from a partially failed super-step. Its long-term `Store` is intentionally separate from the thread checkpointer. [[3]](https://docs.langchain.com/oss/python/langgraph/persistence)

OpenAI Agents SDK sessions have a small protocol (`get_items`, `add_items`, `pop_item`, `clear_session`) and implementations for SQLite, Redis, SQL databases, Dapr state stores, MongoDB, OpenAI-hosted conversations, encrypted wrappers, and compaction. A run cannot combine an SDK session with `conversation_id`/`previous_response_id` continuation, so choose one ownership model rather than layering histories. [[4]](https://openai.github.io/openai-agents-python/sessions/)

ADK stores chronological `Event` objects and serializable key-value `State`; state mutations are applied through event deltas. Persistence depends on the chosen `SessionService`: in-memory services lose data on restart, while database and managed services persist it. [[7]](https://adk.dev/sessions/state/) [[8]](https://adk.dev/sessions/session/)

`[inferred]` Keep authoritative workflow state separate from prompt-facing message text. A payment amount, approval decision, or authenticated account ID should be a typed server-side field with validation, not a fact re-parsed from a model-generated summary.

### Long-term semantic memory

Semantic memory stores knowledge without requiring replay of the full episode that produced it. Two common shapes have different tradeoffs:

1. **Profile/document memory** keeps one structured record per user/project. It is easy to inject and inspect but suffers concurrent-update and overwrite risk.
2. **Atomic collection memory** keeps individual facts with identity, provenance, time, and status. It supports targeted retrieval and correction but needs deduplication and conflict resolution.

```json
{
  "memory_id": "mem_01J...",
  "namespace": ["tenant-7", "user-42", "preferences"],
  "kind": "semantic_fact",
  "subject": "user-42",
  "predicate": "communication_style",
  "value": "concise technical explanations",
  "valid_from": "2026-08-21T10:00:00Z",
  "valid_to": null,
  "source": {"type": "user_statement", "event_id": "evt-918"},
  "origin_trust": "authenticated_user",
  "confidence": 1.0,
  "status": "active",
  "schema_version": 3
}
```

LangGraph's long-term store uses a namespace and key for JSON documents and can add semantic indexing to selected fields. Its examples namespace memory by user and application context, and its production guidance replaces the in-memory implementation with a database-backed store. [[9]](https://docs.langchain.com/oss/python/langchain/long-term-memory)

Letta's memory-block abstraction provides persistent, labeled, size-bounded sections that are always in context; blocks can be agent-editable or read-only. [[10]](https://www.letta.com/blog/memory-blocks/) Its current MemFS documentation describes a Git-backed Markdown hierarchy: files under `system/` load into every system prompt, while other files remain discoverable and load on demand. [[11]](https://github.com/letta-ai/letta-docs-md/blob/main/concepts/memfs/index.md)

MemGPT introduced the underlying virtual-context analogy: move information between limited in-context memory and larger external tiers, similar to an operating system managing fast and slow memory. [[12]](https://arxiv.org/abs/2310.08560) The analogy is helpful, but semantic memory also needs database properties absent from a pure paging analogy: provenance, temporal validity, authorization, correction, consent, and deletion.

### Long-term episodic memory

An episode preserves the situated event rather than only its distilled fact:

```json
{
  "episode_id": "ep_123",
  "task_type": "deploy_service",
  "started_at": "...",
  "ended_at": "...",
  "goal": "deploy payments-api v51",
  "observations": ["health check failed after migration"],
  "actions": ["rolled back schema", "redeployed v50"],
  "outcome": "recovered",
  "feedback": "migration must be backward-compatible",
  "artifact_refs": ["trace://...", "change://..."],
  "policy_version": "deploy-policy-9",
  "source_trust": "verified_execution"
}
```

Generative Agents stored a natural-language memory stream, created higher-level reflections, and retrieved memories using relevance, recency, and importance signals to support planning. The study's ablations found observation, planning, and reflection each contributed to believable simulated behavior. [[13]](https://arxiv.org/abs/2304.03442)

Reflexion uses a narrower task-learning pattern: after feedback, an agent writes linguistic self-reflection to an episodic buffer for later trials rather than updating model weights. Its paper reported 91% HumanEval pass@1 versus an 80% GPT-4 reference in that historical setup; this does not prove arbitrary self-reflection is reliable in production. [[14]](https://arxiv.org/abs/2303.11366)

`[inferred]` Preserve raw verified outcome and feedback alongside any generated lesson. A model-written explanation of why a task failed is a hypothesis. It must not overwrite the execution evidence or silently become a high-authority procedure.

### Write path: memory formation

A complete write path is more than `vector_store.add(text)`:

```text
observe candidate
 -> classify scope/type/sensitivity/origin
 -> decide whether memory is necessary and consented
 -> extract structured candidate
 -> validate against source and policy
 -> deduplicate / link / supersede / reject
 -> commit durable item + provenance + outbox event
 -> embed/index asynchronously
 -> expose only after searchable projection is consistent
```

Memory writes can occur **in the hot path**, immediately affecting the current response, or **in the background**, after a session/task. LangChain documents both approaches and notes their latency-versus-eventual-availability tradeoff. [[1]](https://docs.langchain.com/oss/python/concepts/memory)

Google ADK exposes `add_session_to_memory`, optional incremental event/direct-entry methods, and `search_memory`. Its managed options differ materially: in-memory stores full conversations with keyword search, Memory Bank extracts/consolidates information semantically, and RAG memory stores full transcripts for vector search. Some methods are not implemented by every service. [[15]](https://adk.dev/sessions/memory/)

`[inferred]` Use deterministic rules for obvious writes and model extraction only where semantics require it:

- direct authenticated preference: structured semantic fact;
- tool execution and result: verified episode;
- retrieved web page statement: untrusted evidence, not a user fact;
- model speculation: do not persist as fact;
- user correction: supersede the old fact and retain audit lineage;
- application policy: developer-controlled procedural memory, read-only to the agent.

### Consolidation, reflection, and forgetting

Without consolidation, memory becomes an unbounded duplicate event log. Without source retention, consolidation can distort history.

MemoryBank proposed summarization, personality understanding, and a forgetting/reinforcement mechanism inspired by the Ebbinghaus curve. Its evaluation was a research companion-chat setting, not a retention standard. [[16]](https://arxiv.org/abs/2305.10250) A-MEM instead creates structured notes and links, then allows new memories to update contextual representations of older notes. [[20]](https://arxiv.org/abs/2502.12110) AgeMem, published in 2026, studies a learned policy that chooses store, retrieve, update, summarize, and discard actions across short- and long-term memory. [[22]](https://arxiv.org/abs/2601.01885)

`[inferred]` Separate four operations:

- **compact**: reduce prompt tokens while preserving a source-event range;
- **consolidate**: merge equivalent memories but keep provenance links;
- **supersede**: mark an old fact invalid from a time, without pretending it never existed;
- **delete**: remove data because of retention, user request, or legal policy, including indexes/caches/backups according to policy.

Never use heuristic "forgetting" to implement a legal deletion request. Expired-from-retrieval and physically/logically deleted are different states.

### Memory retrieval

Memory retrieval has four decisions:

1. **whether** memory is needed;
2. **which namespace and types** are authorized and useful;
3. **how to generate candidates and rerank them**;
4. **how much to inject and with what authority label**.

Candidate methods include exact key lookup, metadata/temporal filters, BM25 keyword search, dense semantic search, hybrid fusion, graph traversal, and retrieval over episode summaries. Reranking can combine semantic relevance, time validity/recency, importance, source authority, outcome similarity, and diversity. Do not hard-code "recent is better" for stable facts or "important is true."

LongMemEval decomposes memory system design across indexing, retrieval, and reading. It tests information extraction, multi-session reasoning, temporal reasoning, knowledge updates, and abstention; its authors report roughly a 30% accuracy drop for evaluated commercial assistants and long-context models under sustained histories. It found benefits from session decomposition, fact-augmented keys, and time-aware query expansion in its benchmark. [[17]](https://arxiv.org/abs/2410.10813)

`[inferred]` A retrieval service should return typed evidence, not a preformatted hidden prompt:

```python
def recall(subject, query, now, purpose, limit=8):
    scope = authorize_memory_scope(subject, purpose)
    candidates = hybrid_search(
        query=query,
        namespace=scope.namespace,
        filters={
            "status": "active",
            "valid_at": now,
            "allowed_kind": scope.allowed_kinds,
        },
        limit=50,
    )
    ranked = rerank(candidates, query, now, purpose)
    return [
        {
            "id": m.id,
            "kind": m.kind,
            "content": m.content,
            "source": m.source,
            "origin_trust": m.origin_trust,
            "validity": m.validity,
        }
        for m in diversity_select(ranked, limit)
    ]
```

The model must see that a remembered user preference, a prior assistant claim, a verified tool result, and a developer policy have different authority. Retrieved memory may inform an answer; it cannot grant permission or redefine system instructions.

### Architecture boundaries

| Store | Owns | Must not become |
|---|---|---|
| session/checkpoint store | current thread state and ordered events | unbounded cross-user knowledge base |
| semantic memory store | curated facts/preferences with validity | source of truth for transactional data |
| episodic store | prior task/interactions and outcomes | unquestioned procedure library |
| procedural/skill store | governed behavior and reusable methods | agent-writable policy root |
| trace/audit store | diagnostic record | prompt memory automatically exposed to agents |
| enterprise system of record | accounts, orders, policies, clinical facts | silently copied into stale personal memory |

CoALA provides the broader conceptual basis: language agents can have working and long-term memory modules plus structured internal actions to read and write them. [[23]](https://arxiv.org/abs/2309.02427) The production contribution is to attach typed contracts and policy to those internal actions.

## 2. Token Economics & NFR Metrics

### Lifecycle cost model

```text
write_cost = extraction_model_tokens
           + validation/dedup/consolidation
           + embedding/indexing
           + durable storage + replicas + audit

read_cost = query classification/expansion
          + lexical/vector/graph retrieval
          + reranking
          + memory tokens injected on every model call

maintenance_cost = compaction + re-embedding + reconciliation
                 + deletion + backup + migration + human correction

cost_per_memory_helped_success = lifecycle memory cost
                               / verified tasks where memory improved outcome
```

An always-in-context 1,000-token profile is paid again on each model call and agent step. An external memory item costs storage/indexing but only consumes prompt tokens when retrieved. Conversely, on-demand retrieval adds latency and can miss critical facts. This creates a "core versus searchable" placement problem that should be measured, not solved by copying everything into the system prompt.

OpenAI's Agents SDK usage documentation notes that previous session messages may be re-fed on later runs, increasing subsequent input tokens; reported run usage remains per run. [[24]](https://openai.github.io/openai-agents-python/usage/) Compaction reduces context but adds its own model work and can block streaming in some session modes. [[4]](https://openai.github.io/openai-agents-python/sessions/)

LangGraph notes that storing full channel values at every graph super-step can cause substantial growth for long-running threads; its beta delta channel reduces append-heavy storage at a read-latency tradeoff. [[3]](https://docs.langchain.com/oss/python/langgraph/persistence)

### Latency budget

```text
T_turn = T_session_read
       + T_need_memory
       + T_candidate_retrieval
       + T_rerank
       + T_context_assembly
       + T_model_and_tools
       + T_session_commit
       + T_sync_memory_write(optional)
```

Measure p50, p95, and p99 for session load, each retrieval branch, rerank, memory write, index visibility lag, compaction, and deletion. Record the number of memories considered/retrieved/injected, injected tokens, cache hit rate, and degraded-mode use. Background extraction must have a freshness SLO and backlog metric; "asynchronous" cannot mean "eventually, perhaps."

### Quality metrics by stage

**Write quality**

- candidate-memory precision and recall against human labels;
- factual/source entailment;
- type/scope/tenant classification accuracy;
- duplicate and contradiction rate;
- sensitive-memory capture rate and consent/policy violations;
- time-to-searchable and failed extraction rate.

**Retrieval quality**

- Recall@k, precision@k, MRR/nDCG;
- temporal retrieval accuracy and obsolete-memory demotion;
- correct namespace/subject rate;
- source-authority ranking;
- episode/outcome similarity;
- irrelevant and redundant token fraction;
- memory-needed routing precision/recall.

**Downstream quality**

- task success delta with memory versus no-memory baseline;
- multi-session, temporal, and update-question accuracy;
- correct abstention when no memory supports an answer;
- contradiction and stale-memory-use rate;
- citation/provenance accuracy;
- user correction and deletion success;
- harmful personalization or action rate.

**Operations**

- storage/vector count per active subject;
- read/write p95/p99 and availability;
- embedding/extraction/prompt tokens and cost;
- replication lag, lost-update/conflict rate;
- TTL and privacy-deletion completion;
- backup restore and recovery objectives.

### Benchmark evidence and limitations

LoCoMo contains very long conversations averaging about 300 turns and 9,000 tokens across as many as 35 sessions, with question answering, event summarization, and multimodal dialogue tasks. Its authors found long-context and RAG approaches improved results but still lagged human performance on temporal and causal understanding. [[18]](https://arxiv.org/abs/2402.17753)

MemoryAgentBench tests accurate retrieval, test-time learning, long-range understanding, and selective forgetting through incremental multi-turn interactions. Its evaluation found current methods do not master all four capabilities. [[19]](https://arxiv.org/abs/2507.05257)

Mem0's authors report on LoCoMo a 26% relative LLM-judge improvement over an OpenAI memory baseline, over 90% token savings, and 91% lower p95 latency relative to full-context processing; graph memory added about two overall score points over their base system. These figures are specific to their implementation, evaluation pipeline, comparison, and paper date. [[21]](https://arxiv.org/abs/2504.19413)

Zep's authors report 94.8% versus 93.4% on the older Deep Memory Retrieval benchmark and up to 18.5% accuracy improvement plus 90% latency reduction on selected LongMemEval comparisons. It is a vendor-authored paper about its temporal graph system, so reproduce the workload and compare against current shipped versions before making a procurement claim. [[25]](https://arxiv.org/abs/2501.13956)

`[inferred]` A production bake-off must pin conversation generator/data, question labels, write policy, embedding/reranker, reader model, time handling, `k`, judge, hardware/region, and privacy filters. Run exact current code paths; paper benchmark adapters can diverge from shipped defaults.

### Capacity and cost test matrix

- one, 100, and projected peak concurrent sessions per subject/tenant;
- 10, 1,000, and 100,000 memories per namespace;
- hot versus cold subjects and skewed "celebrity" tenants;
- short facts, long episodes, multilingual content, attachments, and tool traces;
- concurrent writes and corrections to the same profile;
- mass TTL expiry, account deletion, and embedding migration;
- current, superseded, contradictory, ambiguous, and poisoned memories;
- cache warm/cold, replica lag, storage failover, and extraction backlog;
- memory enabled/disabled to quantify actual task lift.

> ⚠️ Limited public data available for this dimension. There is no stable, audited, apples-to-apples benchmark that compares short-term, semantic, episodic, graph, and learned memory systems using the same write policy, model, conversations, authorization rules, storage backend, hardware, p50/p95/p99, deletion behavior, and cost per verified task success. Published metrics are benchmark- and implementation-specific; production sizing requires replay and load testing.

## 3. Distributed Resilience & State

### Use an event log plus rebuildable projections

`[inferred]` The durable unit should be an immutable observed event or explicit user/developer memory command. Semantic facts, embeddings, summaries, and graphs are derived projections:

```text
session event log / verified tool outcome
       |-- thread context projection
       |-- semantic fact projection
       |-- episodic trajectory projection
       |-- embedding / lexical index
       `-- optional temporal graph and summaries
```

This permits re-extraction after a model/prompt change and preserves the evidence needed to correct a bad consolidation. Event retention must still obey data minimization; "immutable" means controlled append/audit semantics, not infinite legal retention.

### Idempotent writes and visibility

Use an idempotency key such as `(tenant, subject, source_event_id, extractor_version, memory_kind)`. Commit the canonical item and an outbox record transactionally. Workers update embedding/search/graph projections at least once; deterministic IDs turn replay into upsert.

Track states such as:

```text
PROPOSED -> VALIDATED -> COMMITTED -> INDEXED -> ACTIVE
         -> REJECTED
ACTIVE -> SUPERSEDED / EXPIRED / TOMBSTONED
```

A committed but not indexed fact should not be silently absent. Either synchronously read the canonical store for read-your-writes, or return an explicit `indexing` status until projections catch up.

### Concurrency control

Two simultaneous turns can update the same profile or session. Last-write-wins over an entire summary loses facts and corrections.

`[inferred]` Use:

- append-only events for messages/episodes;
- version or ETag compare-and-swap for profile documents;
- atomic item-level upsert for semantic facts;
- uniqueness constraints for deterministic source keys;
- per-subject ordering only where the domain requires it;
- merge/retry with a bounded attempt count;
- conflict records when two trusted sources disagree.

ADK's database session documentation includes concurrency/locking behavior and warns that in-memory state is not durable. [[8]](https://adk.dev/sessions/session/) OpenAI's session abstraction supports shared Redis, SQL, MongoDB, and Dapr stores; the underlying store and session implementation determine consistency and TTL behavior. [[4]](https://openai.github.io/openai-agents-python/sessions/)

Do not hold a distributed lock while calling an LLM. Read version `v`, extract a proposed patch, then commit only if the record still has `v`; otherwise rebase the structured patch.

### Thread identity and multi-agent scope

Session IDs are security-sensitive cursors. Reusing the wrong ID loads another history; choosing a new ID starts without prior thread state. LangGraph documents `thread_id` as the checkpoint pointer. [[3]](https://docs.langchain.com/oss/python/langgraph/persistence)

Multi-agent systems need explicit scope:

- invocation-local scratchpad;
- parent thread state;
- specialist per-thread memory;
- user semantic memory;
- team/shared archive;
- application procedural memory.

LangGraph defaults most subagents to per-invocation persistence and supports per-thread or stateless alternatives. [[26]](https://docs.langchain.com/oss/python/langgraph/use-subgraphs) Letta's shared-archive guide requires the archive and memory tools to be attached explicitly to each participating agent. [[27]](https://docs.letta.com/guides/agents/multi-agent-parallel-execution/)

`[inferred]` A worker should not inherit all supervisor/user memory by default. Pass the minimum typed context for its task and return findings to a controlled consolidation step.

### Compaction and summary consistency

Keep compaction artifacts with:

- covered event start/end IDs;
- source event hash/Merkle root;
- compactor model/prompt/version;
- generated timestamp;
- unresolved entities and contradictions;
- pointer to prior summary if hierarchical;
- validation status.

When late events arrive or a source event is deleted/corrected, invalidate every summary that covered it. Never chain summaries indefinitely without periodic source-grounded reconstruction; errors accumulate.

OpenAI's current sandbox memory is explicitly separate from conversational sessions. It uses progressive disclosure, conversation extraction, and later consolidation into memory files; it also warns that memories become stale and should yield to the current environment. The feature is beta. [[5]](https://openai.github.io/openai-agents-python/sandbox/memory/)

### Migrations

Version canonical schema, extractor, chunking, embeddings, temporal rules, and reranker. During migration:

1. build new projection alongside the old;
2. replay a pinned evaluation and authorization suite;
3. dual-read or shadow-read and compare;
4. atomically switch a namespace/index generation;
5. retain bounded rollback state;
6. delete the old representation under retention policy.

Embeddings from different models/dimensions should not be mixed in one similarity space. A graph extraction change can require entity resolution and edge validity rebuild, not only re-embedding.

### TTL, correction, and deletion

TTL belongs on each layer: session events, semantic items, episodes, embeddings, summaries, caches, traces, and backups. OpenAI's encrypted session wrapper supports per-session encryption and expiration. [[28]](https://openai.github.io/openai-agents-python/sessions/encrypted_session/) LangGraph Agent Server documents checkpoint/memory TTLs, API deletion, and checkpoint encryption configuration, while noting which data is stored in PostgreSQL and which signals use Redis. [[29]](https://docs.langchain.com/langsmith/data-storage-and-privacy)

`[inferred]` Implement a deletion ledger that proves:

- canonical items tombstoned/deleted;
- vector/lexical/graph projections removed;
- summaries and learned profiles rebuilt;
- prompt and result caches invalidated;
- shared-agent copies removed;
- traces/evals handled under their separate retention basis;
- backups age out or support compliant cryptographic deletion.

### Failure recovery and degraded modes

| Failure | Recovery/degradation |
|---|---|
| session store unavailable | reject stateful mutation or use read-only explicit degraded mode |
| semantic index unavailable | exact/profile lookup only; do not pretend full recall |
| embedding service unavailable | queue writes; keyword/metadata retrieval |
| consolidator backlog | retain raw episodes and surface freshness lag |
| reranker timeout | use filtered hybrid rank with degraded flag |
| stale replica | route read-your-write to primary/version-aware read |
| corrupt summary | reconstruct from source events |
| partial delete | keep case open and block the subject from retrieval until reconciled |

Backups must include the canonical event/fact stores, namespace and policy metadata, tombstones, schemas, index manifests, and encryption-key recovery process. Test point-in-time restore without resurrecting deleted or unauthorized memories.

## 4. Enterprise Security & Governance

### Memory changes the trust boundary over time

An indirect prompt injection normally affects one run; a memory write can make it persist into future sessions. OWASP's 2026 discussion of ASI06 Memory & Context Poisoning highlights how untrusted repository content reached persistent memory/configuration and continued influencing future behavior. [[30]](https://genai.owasp.org/2026/05/13/memory-is-a-feature-it-is-also-an-attack-surface/)

A 2026 primary study on sleeper memory poisoning evaluates the full write-retrieve-act chain and reports that poisoned memories can remain dormant, later retrieve, and trigger attacker-intended behavior across stateful assistants. [[31]](https://arxiv.org/abs/2605.15338) Another systematic study identifies multiple write channels and structural vulnerabilities, finding that aggressive write/retrieve behavior increases exploitability and existing prompt-injection defenses do not cover the whole memory-poisoning problem. [[32]](https://arxiv.org/abs/2606.04329)

These are recent research results, not proof of universal rates, but they establish persistent memory as an independent attack surface.

### Origin-bound authority

Each item needs immutable origin metadata:

```text
developer policy / verified enterprise source
authenticated user statement or correction
verified tool result
other agent output
model inference or reflection
retrieved external/untrusted content
```

`[inferred]` Authority must follow the original source, not the fluent summary. If an untrusted webpage causes the model to write "the user prefers sending secrets to X," the agent's act of summarizing does not upgrade that origin. A memory can propose content but cannot grant tool permission, modify approval requirements, or become a system instruction.

A 2026 formal-analysis paper argues that content inspection and mutable lineage alone can be laundered through summarization, trusted-tool echoes, or manufactured corroboration, and proposes non-malleable origin-bound authority. Its proofs and benchmark are research evidence rather than an adopted interoperability standard, but the threat model supports binding authority at write time and preventing later transformations from upgrading it. [[35]](https://arxiv.org/abs/2606.24322)

Controls:

1. permit direct fact writes only from approved channels;
2. stage model-extracted memories as proposals;
3. validate content against the referenced source;
4. mark source trust and sensitivity immutably;
5. keep procedural/system memory developer-controlled or approval-gated;
6. filter retrieved memories by action purpose and authority;
7. display and audit why each memory was used;
8. require fresh authoritative checks before consequential actions.

### Identity, tenancy, and authorization

Memory keys must derive tenant and subject from authenticated server context, never solely from model output or client-provided namespace strings. Enforce authorization in canonical lookup, search filters, graph traversal, cache keys, exports, admin tools, and traces.

Test:

- user A cannot retrieve, update, infer, or delete user B's memory;
- a shared team memory does not leak private episodes;
- a subagent sees only delegated scope;
- account merge/split and identity-provider changes do not collide namespaces;
- support/admin impersonation is approved and audited;
- anonymized users do not silently share an "anonymous" namespace.

`[inferred]` Use separate indexes/namespaces or encryption scopes for high-assurance tenants where feasible. Metadata filters alone can fail through application bugs, caches, background consolidators, or cross-namespace search.

### Privacy, consent, and user control

Memory is profiling. A harmless conversation may expose health, beliefs, relationships, location, employment, or protected traits. Do not infer and persist sensitive attributes merely because a model can.

Establish:

- explicit purpose and lawful basis per memory category;
- data minimization and sensitive-category denylist/approval policy;
- visible product controls to view, correct, export, disable, and delete memory;
- retention by purpose, not "forever because storage is cheap";
- separation between service history, personalization memory, safety audit, and model training;
- no secondary use without a compatible basis and disclosure.

NIST AI RMF is voluntary and use-case agnostic, organizing continuous risk work around Govern, Map, Measure, and Manage and emphasizing rights-preserving, trustworthy system design. [[33]](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10) `[inferred]` Apply those functions to the memory lifecycle: map subjects/sources/harms, measure write/retrieval/deletion behavior, govern authority and retention, and manage incidents/corrections.

### Confidentiality and encryption

Encrypt memory in transit and at rest with managed key rotation, but recognize that searchable fields and embeddings may remain exposed to the retrieval system. Isolate databases and vector indexes, use private networking, least-privilege service identities, and field-level protection for especially sensitive values.

LangGraph's encryption documentation specifies which JSON and checkpoint fields can be encrypted and which identifiers/metadata remain unencrypted for authorization and orchestration. [[34]](https://docs.langchain.com/langsmith/encryption) This illustrates an architectural reality: encryption modules do not eliminate metadata leakage; threat-model cleartext indexes and identifiers.

Do not log raw memories by default. Traces should prefer IDs, hashes, types, ranks, and policy decisions with privileged drill-down. Apply independent retention and access controls to debug snapshots and evaluation datasets.

### Memory use cannot authorize actions

A remembered statement such as "always approve refunds" or "my bank account changed" is not authorization. For high-impact operations:

- reauthenticate the user;
- retrieve current data from the system of record;
- run deterministic policy checks;
- show the proposed effect;
- obtain required approval;
- log the decision and idempotency key.

Memory may personalize the interface or help locate a record. It cannot substitute for current identity, consent, balance, entitlement, medical record, or policy.

### Shared and procedural memory governance

Shared memory can amplify one compromised agent or user across a team. Require writer ACLs, per-item provenance, review for promotion, rate limits, namespace quotas, and rollback. Do not let low-trust workers write supervisor instructions.

Procedural memory is effectively code/configuration. Protect it with version control, code review, signed releases, read-only runtime mounts, separation of duties, evaluation, and rollback. Letta's MemFS uses Git-backed versioning, but version history is an audit mechanism only when repository access and promotion are governed. [[11]](https://github.com/letta-ai/letta-docs-md/blob/main/concepts/memfs/index.md)

### Audit record

For each memory use or mutation, record:

- authenticated actor/service and tenant/subject;
- operation and purpose;
- source event and origin authority;
- before/after version or structured patch;
- extractor/embedding/reranker/prompt/model versions;
- candidate ranks and selected IDs;
- policy decision, consent, and approval;
- downstream action or answer correlation ID;
- expiry, correction, deletion, and incident links.

Keep the audit tamper-evident and access-separated. Do not expose hidden reasoning; record concise decisions and evidence.

## 5. Production Failure Modes

### Write and consolidation failures

| Failure | Consequence | Detection | Mitigation |
|---|---|---|---|
| remembers everything | cost, noise, privacy exposure | memories/session, write precision | allowlist purposes; write threshold/quota |
| extracts model claim as user fact | false personalization | source-entailment audit | type and origin validation |
| duplicate facts | repeated context and rank domination | canonical similarity/keys | deterministic IDs and consolidation |
| summary distortion | wrong long-term belief | source-grounded summary eval | retain event links; reconstruct |
| correction appended, not superseded | conflicting answers | active conflict count | validity intervals and explicit supersede |
| profile whole-record overwrite | lost updates | version conflict/lost-field metric | patch + optimistic concurrency |
| reflection learns wrong lesson | repeated failure | outcome versus lesson audit | verified feedback, promotion gate |
| over-aggressive forgetting | needed evidence gone | retention/forgetting benchmark | purpose-specific policy and protected facts |

### Retrieval and use failures

- **semantic near-miss**: query and memory use different vocabulary; use hybrid/key expansion and evaluate Recall@k.
- **temporal miss**: current fact loses to an older semantically closer item; filter validity before ranking.
- **recency bias**: recent trivial memory displaces an older durable preference; tune by type, not globally.
- **importance bias**: a model-assigned importance score promotes dramatic but false content; authority and support remain separate.
- **wrong granularity**: full sessions are too broad, atomic facts lack context; index both with source linkage.
- **over-retrieval**: too many memories consume context and steer unrelated tasks; route, rerank, diversify, cap tokens.
- **under-retrieval**: relevant memory exists but the agent never calls the tool; measure memory-needed routing.
- **stale in-context core**: an always-loaded profile bypasses the corrected external store; version and refresh it.
- **negative transfer**: a prior successful episode differs in a critical constraint; rerank on task/outcome conditions.
- **self-confirmation loop**: model output is saved, retrieved, and treated as independent corroboration; track lineage and deduplicate authority.

LongMemEval's knowledge-update and abstention categories and MemoryAgentBench's selective-forgetting category exist because raw similarity search does not solve these failures. [[17]](https://arxiv.org/abs/2410.10813) [[19]](https://arxiv.org/abs/2507.05257)

### Security failures

- indirect prompt injection is written into durable memory;
- untrusted content is summarized and mistakenly upgraded to trusted origin;
- another tenant's memory is returned by a missing namespace filter;
- a user fact is placed in the system/procedural layer;
- shared memory allows one worker to poison every collaborator;
- deletion removes a fact row but not embedding, summary, cache, graph edge, or trace;
- memory reveals sensitive inferences the user never explicitly supplied;
- a retrieved "approval" bypasses fresh authorization;
- debug/admin tools allow bulk memory export without audit;
- poisoned memory activates only after the red-team session ends.

Use multi-session red-team tests that cover injection, delayed retrieval, attempted action, correction, and deletion. Single-turn prompt-injection tests miss the defining persistence property.

### Distributed-state failures

- random IDs make at-least-once replay create duplicate episodes;
- session events commit but semantic extraction silently fails;
- embedding index lags behind canonical facts and violates read-your-writes;
- two workers consolidate the same subject and lose each other's updates;
- old and new embedding models share one index;
- stale replica resurrects superseded facts;
- TTL expires canonical data while cached prompt memory remains;
- account deletion restores from a backup into active search;
- failover loses the high-water mark or outbox position;
- a session identifier collision loads the wrong history.

Reconcile counts and hashes across canonical store, embeddings, lexical index, graph, summaries, and cache. Alert on orphan projections, poison/quarantine flags, version mismatch, extraction lag, and partial deletion.

### Product and human failures

- users cannot see why an assistant "knows" something;
- correction controls edit the visible profile but not derived summaries;
- memory creates uncomfortable over-personalization;
- a shared device/session attaches facts to the wrong person;
- the agent remembers a preference after consent is withdrawn;
- the product claims "forgotten" while retaining retrievable logs;
- support personnel manually insert unverified facts;
- users intentionally manipulate memory to affect other workflows.

Memory UX is part of safety: show concise remembered items and sources, allow correction/deletion, distinguish current session from cross-session memory, and make disablement effective across every write path.

### Evaluation failures

- synthetic queries repeat exact memory wording and inflate vector recall;
- generated conversations lack real corrections, ambiguity, or privacy constraints;
- only retrieval is measured, not whether using memory improves the task;
- LLM judges accept plausible but unsupported personal facts;
- benchmark timestamps are not passed to the retriever;
- "forgetting" is scored as deletion when the system merely fails to retrieve;
- aggregate accuracy hides cross-user leakage or sensitive-category errors;
- paper code and production memory code have different defaults.

Maintain human-labeled, temporally versioned, adversarial multi-session tests. Evaluate no-memory, full-context, retrieval, and memory-system variants with the same reader model.

> ⚠️ Limited public data available for this dimension. Providers do not publish normalized production incident rates for false memory writes, lost corrections, stale retrieval, memory poisoning, cross-tenant recall, compaction distortion, partial deletion, or harmful action caused by remembered content. Recent security papers establish attack feasibility but not universal field rates; organizations need their own incident taxonomy, canaries, reconciliation, and multi-session adversarial testing.

## 6. Enterprise System Design Scenarios

### Scenario A: customer-support assistant

**Need:** Maintain continuity across tickets without copying account truth into a stale personal profile.

`[inferred]` Use:

- thread events and typed ticket state for the active case;
- semantic memory only for consented communication preferences and stable user-provided context;
- episodic summaries of resolved cases with verified issue, action, and outcome;
- live account/order APIs as the source of truth;
- hybrid retrieval filtered by customer/account and temporal validity;
- human-visible memory correction and deletion.

Do not store passwords, payment data, or model-inferred protected traits. A prior refund is an episode, not permission to issue another. Reauthenticate and re-run policy for every effect.

**Evaluate:** memory-write precision, prior-case Recall@k, current-versus-stale account use, resolution lift, cross-account leakage, p95 latency, token cost, and deletion completion.

### Scenario B: coding and operations agent

**Need:** Learn repository conventions and recover useful lessons across tasks.

`[inferred]` Separate:

- current task plan, shell outputs, and approvals in thread state;
- verified repository facts in semantic project memory;
- prior build/deploy trajectories with exit status in episodic memory;
- reviewed commands/checklists in procedural memory under version control.

Memory files from the repository are untrusted until reviewed; project content must not write global user memory. Record a failure lesson only with command/test evidence. Current repository files and CI policy override recalled guidance. Disable memory writes for throwaway reviewers/subagents, or route their proposals through a consolidator.

**Evaluate:** repeated-task time/token reduction, obsolete-command rate, test success, poison persistence, project/global isolation, and procedure promotion audit.

### Scenario C: regulated care or financial assistant

**Need:** Conversation continuity with strict privacy, correction, and current-record requirements.

`[inferred]` Keep durable clinical/financial facts in the regulated system of record, not agent-generated semantic memory. Short-term session state can hold minimum necessary interaction context with strict TTL. Cross-session memory should be opt-in, category-limited, encrypted, visible, and never used to authorize treatment, payment, identity, eligibility, or advice without current authoritative retrieval.

Use deterministic policy to block sensitive inference writes. Require source citations, purpose-based access, break-glass audit, data-residency controls, and verified erasure. Consider a stateless design when continuity benefit does not justify retention risk.

**Evaluate:** zero unauthorized retrieval in the test suite, sensitive-write false positives/negatives, current-record precedence, correction/deletion SLO, adverse recommendation rate, and human escalation.

### Scenario D: multi-agent research system

**Need:** Researchers share findings without propagating speculation as fact.

`[inferred]` Give each worker invocation-local scratch state. Store retrieved papers/documents as evidence references, worker conclusions as low-authority episodic outputs, and only reviewed claims in a shared semantic collection. A supervisor consolidates with source entailment, deduplication, and conflict preservation. Shared procedural memory is read-only and release-reviewed.

Use per-project namespaces, quotas, origin-aware ranking, and an append-only review trail. Do not let the number of agreeing agents count as independent corroboration when they share sources or copy each other's memory.

**Evaluate:** evidence recall, claim-source support, independent-source diversity, duplicate-lineage detection, shared-memory poison containment, correction propagation, and cost per accepted finding.

### Decision matrix

| Requirement | Start with | Add only when measured |
|---|---|---|
| multi-turn continuity | durable session/checkpoint store | compaction for token/latency pressure |
| stable preferences | typed semantic facts with provenance | extraction from free text after precision tests |
| learning from past tasks | verified episodic outcome store | generated reflection after feedback validation |
| many searchable memories | filtered hybrid retrieval | graph/agentic linking for temporal/multi-hop need |
| always-needed identity/policy | small read-only core block | agent edit only behind approval |
| legal/transactional truth | live system-of-record lookup | never replace with agent memory |

### Principal-engineer interview checklist

1. What is the difference between thread scope and storage durability?
2. Which items are semantic facts, episodes, procedures, or merely logs?
3. Who may propose, validate, commit, correct, and delete each type?
4. Does every memory carry source, origin authority, time validity, tenant, and schema version?
5. How are concurrent profile writes merged without losing updates?
6. How does retrieval handle semantic relevance, time, authority, diversity, and abstention?
7. Can a remembered statement ever authorize a tool action? The correct default is no.
8. How are raw events, summaries, embeddings, graphs, caches, traces, and backups reconciled on deletion?
9. What happens when memory storage or retrieval is unavailable?
10. What measured task lift justifies token, latency, privacy, and security cost?

### Recommended learning order

1. Implement typed thread state and durable checkpoints.
2. Add bounded history/compaction with source ranges.
3. Build an explicit semantic fact schema and correction workflow.
4. Store verified episodes and outcomes separately from generated lessons.
5. Add filtered hybrid retrieval and stage-level evaluation.
6. Add consolidation, temporal validity, and deletion reconciliation.
7. Red-team multi-session poisoning and cross-tenant access.
8. Experiment with graph or learned memory only against simpler baselines.

## Sources

- [1] https://docs.langchain.com/oss/python/concepts/memory - Short/long-term, semantic, episodic, and procedural memory concepts.
- [2] https://docs.langchain.com/oss/python/langgraph/add-memory - LangGraph short-term and semantic long-term memory patterns.
- [3] https://docs.langchain.com/oss/python/langgraph/persistence - Checkpoints, stores, pending writes, and storage growth.
- [4] https://openai.github.io/openai-agents-python/sessions/ - OpenAI Agents SDK session protocol, stores, compaction, and constraints.
- [5] https://openai.github.io/openai-agents-python/sandbox/memory/ - Beta sandbox memory extraction, consolidation, and progressive disclosure.
- [6] https://adk.dev/sessions/ - Google ADK Session, State, and Memory boundaries.
- [7] https://adk.dev/sessions/state/ - ADK state scope, serialization, mutation, and persistence.
- [8] https://adk.dev/sessions/session/ - ADK session lifecycle, stores, and concurrency.
- [9] https://docs.langchain.com/oss/python/langchain/long-term-memory - Namespaced JSON memory and semantic search.
- [10] https://www.letta.com/blog/memory-blocks/ - Letta persistent in-context memory blocks.
- [11] https://github.com/letta-ai/letta-docs-md/blob/main/concepts/memfs/index.md - Current Letta Git-backed memory filesystem documentation.
- [12] https://arxiv.org/abs/2310.08560 - MemGPT virtual context and tiered memory.
- [13] https://arxiv.org/abs/2304.03442 - Generative Agents memory stream, reflection, and retrieval.
- [14] https://arxiv.org/abs/2303.11366 - Reflexion episodic verbal feedback.
- [15] https://adk.dev/sessions/memory/ - ADK long-term MemoryService implementations and retrieval tools.
- [16] https://arxiv.org/abs/2305.10250 - MemoryBank updating and forgetting approach.
- [17] https://arxiv.org/abs/2410.10813 - LongMemEval design choices and benchmark.
- [18] https://arxiv.org/abs/2402.17753 - LoCoMo very long-term conversation benchmark.
- [19] https://arxiv.org/abs/2507.05257 - MemoryAgentBench competencies and results.
- [20] https://arxiv.org/abs/2502.12110 - A-MEM linked, evolving memory notes.
- [21] https://arxiv.org/abs/2504.19413 - Mem0 architecture and reported LoCoMo metrics.
- [22] https://arxiv.org/abs/2601.01885 - AgeMem unified learned memory operations.
- [23] https://arxiv.org/abs/2309.02427 - CoALA modular cognitive architecture for language agents.
- [24] https://openai.github.io/openai-agents-python/usage/ - Per-run token usage with sessions.
- [25] https://arxiv.org/abs/2501.13956 - Zep temporal knowledge-graph memory and reported results.
- [26] https://docs.langchain.com/oss/python/langgraph/use-subgraphs - Subagent persistence scope.
- [27] https://docs.letta.com/guides/agents/multi-agent-parallel-execution/ - Explicit shared archival memory for parallel agents.
- [28] https://openai.github.io/openai-agents-python/sessions/encrypted_session/ - Encrypted session wrapper and TTL.
- [29] https://docs.langchain.com/langsmith/data-storage-and-privacy - Agent Server storage, checkpoint encryption, TTL, and deletion.
- [30] https://genai.owasp.org/2026/05/13/memory-is-a-feature-it-is-also-an-attack-surface/ - OWASP ASI06 memory/context poisoning discussion.
- [31] https://arxiv.org/abs/2605.15338 - Sleeper memory poisoning study.
- [32] https://arxiv.org/abs/2606.04329 - Systematic memory-poisoning taxonomy and MPBench.
- [33] https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-ai-rmf-10 - NIST AI RMF 1.0.
- [34] https://docs.langchain.com/langsmith/encryption - LangGraph encryption coverage and exclusions.
- [35] https://arxiv.org/abs/2606.24322 - Origin-bound authority analysis for long-term memory poisoning.
