# Research: Memory — Short/Long-Term, Semantic, Episodic, Memory Retrieval

**Date researched**: 2026-08-21
**Sources consulted**: 34

## 1. System Topology & Mechanics

### 1.1 The canonical layer model (CoALA)

The academic reference architecture is **CoALA (Cognitive Architectures for Language Agents)**, a Princeton/CMU framework (arXiv:2309.02427) that formalizes Tulving's human-memory taxonomy for LLM agents [15]. It defines four memory types:
- **Working memory**: the active context window / scratchpad for the current decision cycle — symbolic, ephemeral, holds perceptions, retrieved items, and intermediate reasoning.
- **Episodic memory**: "experience from earlier decision cycles" — logs of what happened, in what sequence (conversation turns, trajectories, event flows). Written via logging; read via retrieval into working memory during planning.
- **Semantic memory**: the agent's knowledge about the world/itself — facts, entity relationships, abstracted generalizations; can be read-only (RAG over a corpus) or agent-writable (learned knowledge).
- **Procedural memory**: skills/code — how the agent acts (distinct from the two above).

CoALA names the transformation from episodic → semantic as **consolidation**: raw, instance-specific traces are synthesized into abstracted, durable insights [15][16]. Most production frameworks (Letta, Mem0, LangChain) implicitly adopt this taxonomy [16].

### 1.2 MemGPT: OS-inspired virtual context management (the seminal architecture)

**MemGPT** (arXiv:2310.08560, Packer/Wooders et al., 2023) introduced **virtual context management**: treat the LLM prompt window as "main memory" (RAM) and external databases as "disk," and let the LLM page data in/out via function calls, mirroring OS hierarchical memory/paging [1][2]. Core primitives [4]:
- **Main context partitioning**: system instructions (read-only) + working context (read/write, unstructured, holds key facts/persona) + FIFO message queue (rolling history; index 0 stores a recursive summary of evicted messages).
- **External stores**: recall storage (lossless conversation-message DB) and archival storage (document DB, typically vector-indexed).
- **Queue manager**: appends, evicts, summarizes, and writes to recall storage when the FIFO queue overflows.
- **Function executor + heartbeat flag**: lets the LLM chain multiple memory operations (e.g., search → edit → confirm) before yielding control back to the user.

Design principle distilled by later analysis: *tiered memory is the right default* (working context → recall/episodic store → archival/document store), and *paging + token budgeting must be first-class*, not an afterthought [4].

### 1.3 Letta (MemGPT's production successor): three-tier memory + explicit access API

Letta operationalizes MemGPT's design into three concrete tiers with different context/tool semantics [6][8][10]:

| Tier | In-context? | Access pattern | Size/count limits | Tools |
|---|---|---|---|---|
| **Core Memory (Memory Blocks)** | Yes, always pinned | Editable, agent-managed | Recommended <50k chars total, <20 blocks; keep <80% of context window | `memory_insert`, `memory_replace`, `memory_rethink` |
| **Files** | Partial (open/close) | Read-only, agent opens/searches | 5MB/file, <100 files/agent | `open`, `close`, `semantic_search`, `grep` |
| **Archival Memory** | No | Explicit query, semantic search | 300-token chunks, unlimited count | `archival_memory_insert`, `archival_memory_search` |
| **External RAG** | No | Custom tool/MCP | Unlimited | Custom tools |

Archival memory is **not** automatically populated on context overflow — it must be explicitly written to, and semantic search is vector-based [6]. The Letta REST API exposes this directly: `POST /v1/agents/{agent_id}/archival-memory` (params: `text`, `created_at`, `tags`) and `GET /v1/agents/{agent_id}/archival-memory/search` (params: `query`, `tags`, `top_k`) [6].

**Sleep-time compute** (Letta/UC Berkeley, arXiv:2504.13171) is the production evolution beyond MemGPT's single-agent design: it splits the agent into a **primary agent** (user-facing, no core-memory-edit tools) and a **sleep-time agent** (background, holds all memory-editing tools) [13][14]. This decouples conversation latency from memory consolidation and allows continuous, non-blocking refinement of memory blocks. Benchmarked gains: ~1/5 the tokens for equivalent accuracy, or ~15% higher accuracy at equal compute budget, and 2–3× cost amortization when context is reused across related queries [14][12].

### 1.4 Mem0: additive extraction pipeline (V3)

Mem0's V3 architecture is a **managed 3-step loop** (extract → dedupe → store) sitting between the app and the vector/entity stores [3]:
1. **Extraction**: single LLM call pulls facts from conversation messages.
2. **Deduplication**: MD5 hash-based dedup prevents exact-duplicate writes.
3. **Storage**: vector store (embeddings for semantic similarity) + entity store (relationship-aware retrieval).

As of V3, Mem0 moved to **ADD-only extraction** — no UPDATE/DELETE at write time; memories accumulate and conflict resolution is deferred to retrieval-time ranking rather than write-time consolidation [3][5]. This is a deliberate simplification versus MemGPT-style consolidation, trading write-time complexity for read-time ranking complexity. Retrieval (`POST /v3/memories/search/`) is **hybrid**: semantic (vector) + BM25 (keyword) + entity matching, with support for logical/comparison filter operators [5]. Mem0 separates memory into 4 scoping dimensions (`user_id`, `agent_id`, `app_id`, `run_id`) — each combination is stored as an isolated record set [3].

### 1.5 Zep/Graphiti: temporal knowledge graph as memory substrate

**Zep** (arXiv:2501.13956) departs from pure vector-store or MemGPT-style paging in favor of a **temporally-aware dynamic knowledge graph** `G = (N, E, φ)` with three hierarchical subgraph tiers: episode subgraph, semantic entity subgraph, and community subgraph [10][11]. Its open-source engine, **Graphiti**, implements a **bi-temporal model** tracking both:
- **Valid time**: when a fact was true in the real world.
- **Transaction time**: when the system learned it.

When new information contradicts an existing edge, Graphiti **invalidates** (sets `invalid_at`/`expired_at`) rather than deletes, preserving a full audit trail, and creates a new edge with the updated fact [11]. This directly solves the "stale memory" problem (§5) at the architecture level rather than the ranking level. At scale, Zep runs Graphiti atop a proprietary **Context Graph Engine** built for millions of small, mostly-cold graphs, rather than one large graph — architecturally distinct from general-purpose graph DBs (Neo4j/FalkorDB), which Graphiti can also run on when self-hosted [11].

### 1.6 LangGraph: checkpointer (short-term) vs. store (long-term)

LangGraph formalizes the short/long-term split as two orthogonal persistence primitives [7]:

| | Checkpointer | Store |
|---|---|---|
| Persists | Full graph-state snapshots | Application-defined key-value docs |
| Scope | Single thread | Cross-thread |
| Memory type | Short-term, thread-scoped | Long-term, cross-thread |
| Use for | Conversation continuity, human-in-the-loop, time-travel, fault tolerance | User preferences, facts, shared knowledge |

Production backends: `PostgresSaver`/`MongoDBStore`/`RedisStore` (never `InMemorySaver`/`InMemoryStore` in production — they don't survive process restarts) [7]. Long-term memories are stored as JSON documents under a `(namespace, key)` addressing scheme, where namespace commonly encodes org/user IDs for hierarchical organization and cross-namespace filtering [7].

### 1.7 Consumer product architectures (ChatGPT, Claude)

**ChatGPT memory ("Dreaming")**: reverse-engineering shows a **4-layer context assembly** on every turn — session metadata, explicit long-term facts (stored via a dedicated tool), lightweight pre-computed summaries of past conversations, and the current session's raw transcript [17][18]. Notably, **no RAG/vector search is used across conversation history** — summaries are precomputed asynchronously ("dreaming") and injected wholesale, trading retrieval precision for latency/simplicity [17][19]. "Dreaming V3" (mid-2026) moved from explicit-save-triggered memory to **continuous background synthesis**, lifting factual recall from 41.5% (2024) to 82.8% [19]. The model itself remains stateless; the serving system reconstructs the "memory illusion" via prompt assembly + prefix/KV caching every turn [20].

**Claude memory**: two distinct surfaces. (1) **App-level memory** (web/desktop/mobile): a synthesized, editable "memory summary" per project/workspace, regenerated periodically (~every 24h in the legacy design), with "Incognito chats" that never write to memory [21][22][23]. (2) **Developer-facing memory tool** (API, Claude Sonnet 4.5+): a **file-based, client-side memory directory** the model can create/read/update/delete via tool calls — the storage backend (and thus durability/security model) is entirely developer-controlled, paired with **context editing** (auto-clearing of stale tool-result blocks) [9].

### 1.8 Forgetting/decay as a first-class retrieval mechanic

Stanford's **Generative Agents** (2023) established the canonical scoring formula for episodic retrieval: `Score = α·Recency + β·Importance + γ·Relevance`, where Recency uses exponential decay (`0.99^hours`), Importance is an LLM-assigned 1–10 poignancy score, and Relevance is cosine similarity [24][25]. Modern systems (Mem0, OBLIVION arXiv:2604.00131) extend this with distinct **decay vs. eviction** mechanics [23][26]:
- **Eviction** (hard delete, TTL, supersession-on-contradiction) physically removes data — needed for compliance/storage bounds.
- **Decay** (search-time re-ranking) leaves data in place but dampens its retrieval score (Mem0: up to 1.5× boost when recently accessed, dampened toward 0.3× when unused) — preserves audit trail while reducing interference.

`> ⚠️` The correct production policy composes multiple strategies (age/TTL for noise, salience floor for high-stakes low-frequency facts, supersession for contradictions) — no single policy (pure LRU, pure TTL) is safe alone; pure LRU famously prunes rare-but-critical facts like a stored allergy [23].

## 2. Token Economics & NFR Metrics

### 2.1 Embedding cost (write + retrieval path)

OpenAI embedding pricing (Aug 2026) [27][28]:

| Model | Standard | Batch (50% off) | Dimensions |
|---|---|---|---|
| `text-embedding-3-small` | $0.02 / 1M input tokens | $0.01 / 1M | 1536 |
| `text-embedding-3-large` | $0.13 / 1M input tokens | $0.065 / 1M | 3072 |
| `text-embedding-ada-002` (legacy) | $0.10 / 1M | $0.05 / 1M | 1536 |

Embeddings only charge for input tokens (no output cost); embedding is one of the cheapest LLM-adjacent operations, but at billions-of-memories scale the write-amplification (every user turn potentially triggers extraction + embedding) dominates infra spend rather than the per-token embedding rate itself [27][28].

### 2.2 Vector store retrieval cost & latency

Pinecone serverless (Aug 2026 pricing) [29]: storage ≈$0.33/GB/month; Read Units ≈$16–18/M; Write Units ≈$4–4.50/M; $50/month Standard-tier minimum. Published p95 query latency: **sub-10ms** on serverless for warm indexes, but **200–800ms cold-start latency** on idle indexes that cannot be disabled on the serverless tier [29]. Independent architecture teardown of a 10M-vector, 768-dim cluster on 3× r6g.2xlarge nodes: Pinecone 187ms p99 / 2,140 QPS vs. Milvus 312ms p99 / 1,520 QPS vs. Weaviate 345ms p99 / 1,210 QPS vs. Qdrant 241ms p99 / 1,870 QPS `[inferred — single third-party benchmark, not vendor-audited]` [30].

Pinecone's official "test-at-scale" benchmark (10M records, 48.8GB, llama-text-embed-v2): p90 latency target <100ms; cost model ≈$16/1M read units, $0.25/GB import, $0.33/GB-month storage — a 100K-query test against a 48.8GB namespace cost ≈$90 total [31].

### 2.3 End-to-end memory-system latency and cost (Mem0 research)

Mem0's paper (arXiv:2504.19413) benchmarks memory-system search latency directly: **p50 0.148s / p95 0.200s** for base Mem0 vector retrieval; graph-enhanced Mem0ᵍ trades latency for relational reasoning (p50 0.476s, p95 2.590s search-only; total p50 1.091s / p95 2.590s) [5][32]. Versus a **full-context** baseline (concatenating entire conversation history into every prompt), Mem0 achieves **91% lower p95 latency** and **>90% token cost reduction**, while scoring 26% higher on LLM-as-Judge accuracy than OpenAI's memory feature and outperforming six baseline categories on the LOCOMO benchmark [5][32]. Mem0's 2026 state-of-the-industry report cites **6,956 tokens per retrieval call** on LoCoMo vs. **~26,000 tokens** for full-context — a >3.7× reduction directly translating to inference-bill savings [39].

### 2.4 Production-scale case studies (quantified)

- **Mem0 → Turbopuffer migration** [38]: at hundreds of millions of memories, single-table Postgres/pgvector with HNSW hit **tail latency spikes to 14s** under load (query planner abandoned the HNSW index for a prefilter plan at scale), and `INSERT` averaged 800ms. Post-migration to per-customer Turbopuffer namespaces: **70× lower end-to-end retrieval latency**, 70ms p90 hybrid retrieval, 97% avg recall@10, sustained across 100M→400M+ memories / 3TB+ embeddings with no latency/recall degradation. Write-path side effects: INSERT 800ms→8.12ms (99×), UPDATE 500ms→13.7ms (36×), SELECT 50ms→13.4ms (3.7×) [38].
- **Sunflower (80K-user digital health app)** [37]: 1-day Mem0 integration; **70–80% token-usage reduction**; 3–4 weeks of engineering time saved by not building an internal memory layer.
- **Mem0 reliability layer (via Respan)**: 99.99% uptime SLA across "hundreds of millions of daily logs," with full request-level cost/latency tracing to defend the ~90% token-cost and 91% latency claims to enterprise/regulated customers [36].
- Async-write is now considered a required default: Mem0 made `async_mode=True` default in v1.0.0 after identifying synchronous memory writes blocking the response pipeline as "the most common production footgun" [39].

### 2.5 Consolidation / sleep-time compute economics

Sleep-time compute (arXiv:2504.13171) amortizes memory-consolidation LLM calls across idle GPU cycles rather than paying them inline per-request: benchmarked as a Pareto improvement — same accuracy at **~1/5 the tokens**, or **~15% more correct answers** at equal compute budget, with **2–3× cost reduction** when the same consolidated context serves multiple related queries [12][14]. A cited real-world estimate: pre-populating a 64K-token shared corpus prefix for 500 users/day saves ~$2.30/day in serving prefill cost for ~$0.003 in background compute `[inferred — vendor blog estimate, not independently audited]` [14].

`> ⚠️` No public, vendor-neutral benchmark quantifies the LLM-call cost of MemGPT/Letta-style *in-line* consolidation (i.e., the original non-sleep-time design) — most published cost figures are for the newer async/sleep-time or Mem0 additive-extraction architectures.

## 3. Distributed Resilience & State

### 3.1 Durable storage & consistency models

Production long-term memory stores are relational/document/vector systems requiring the same durability discipline as any OLTP system: LangGraph explicitly warns against `InMemorySaver`/`InMemoryStore` in production, mandating `PostgresSaver`/`PostgresStore`, `MongoDBStore`, `RedisStore`, etc. [7]. Concurrency control follows standard distributed-systems patterns, applied to memory writes:
- **Optimistic concurrency control (OCC)**: assumes conflicts are rare; validates read-set versions at commit time and retries on conflict (e.g., DynamoDB/Cosmos conditional writes, HTTP ETags/`If-Match`, version vectors for multi-leader replication) [33][34][35]. Recommended for the read-heavy, low-write-contention profile typical of per-user memory stores (80% of distributed-caching scenarios per one vendor's guidance) [35].
- **Pessimistic locking / distributed locks** (Redis, ZooKeeper, etcd): needed when concurrent writes to the *same* memory record are frequent (e.g., multiple agents/tools writing to one shared memory block simultaneously); requires **fencing tokens** (monotonically increasing) to prevent stale lock holders from corrupting state after a crash/GC pause [34].

`[inferred]` For agent memory specifically, per-user/per-session partitioning (see §4) naturally reduces write contention to near-zero across users, making OCC with per-record versioning the dominant pattern; pessimistic locks are reserved for shared/team memory blocks (e.g., Letta's shared memory blocks across multiple agents) [8].

### 3.2 Circuit breakers & fallback for memory-store calls

The standard three-state circuit breaker (closed → open → half-open) is applied to vector-store and memory-service calls exactly as to any external dependency [40][41][42]:
- **Closed**: normal traffic; breaker counts failures/latency over a sliding window (e.g., 60s window, 5-failure threshold is a commonly cited default) [43].
- **Open**: fail-fast; no network call attempted; immediate fallback served (empty result set, BM25 keyword-search fallback, cached/stale response) [40][41].
- **Half-open**: after a cooldown (commonly 60s, tunable to 300s for slow-recovering providers), a single probe request tests recovery before fully reopening traffic [42][43].

Recommended production topology: **per-dependency breaker scoping** (one breaker per provider/vector-store/tool endpoint, not one global breaker), since a single global breaker conflates unrelated failures and prevents failover to healthy alternatives [42]. Retries should occur *inside* the breaker's failure counting, not wrapped around it, or the breaker's window gets polluted by retry storms [42]. A memory/vector-store outage should degrade a single feature (e.g., no personalization context this turn) rather than fail the entire agent request [40].

### 3.3 Checkpointing memory state

LangGraph's checkpointer mechanism is the primary open-source pattern for state checkpointing: it snapshots full graph state (including in-progress memory-write operations) at each "super-step," enabling time-travel debugging, resumption after failure, and human-in-the-loop interruption without losing conversational state [7]. `[inferred]` For archival/long-term memory (outside the graph-state boundary), checkpointing is typically delegated to the underlying store's own durability guarantees (Postgres WAL, vector-store replication) rather than a separate agent-level checkpoint.

### 3.4 Rate limiting

Pinecone enforces per-project rate limits with HTTP 429 + `x-ratelimit-*` headers for self-throttling, tiered by plan (Starter/Standard/Enterprise) [29]. Production agent platforms layer client-side backoff/circuit-breaking on top of these provider-side limits rather than relying solely on retry-after headers, since an agent's inner loop can call memory-retrieval dozens of times per task, amplifying a single rate-limit event across the whole run [42].

## 4. Enterprise Security & Governance

### 4.1 Zero-trust / multi-tenant isolation for memory

The dominant 2026 guidance is: **make the tenant boundary a hard, storage-layer construct — never an application-level filter that can be forgotten** [46][45]. Concrete patterns:
- **Hard boundary = physically separate index/namespace/bank per tenant** (not a `tenant_id` column filtered at query time). Mem0's production architecture creates a **dedicated Turbopuffer namespace per customer** (150K+ customers, each isolated), which the vendor explicitly frames as "good for security" (physical isolation) in addition to performance [38]. The "Hindsight" memory pattern similarly makes `bank_id` "always derived from the authenticated caller, never from anything a request body can influence" [45].
- **Soft partition = tags**, used only for *organizing* memory within a tenant's own space — never as the security boundary, since tag filters "can be forgotten" [45].
- **Row-Level Security (RLS)** at the database engine level, not application-query level, so the constraint is enforced even if application code has a bug [44].
- **CI-enforced isolation tests**: store as Tenant A, authenticate as Tenant B, assert nothing is retrievable — explicitly called out as a *different test* than the commonly-run "tenant can retrieve their own data" test, and the one most teams skip [44][46].

### 4.2 RBAC and audit logging

Enterprise memory-governance requires two-dimensional RBAC — **who can spawn/invoke an agent** × **what the agent is scoped to do on which data class**, not just user-to-agent single-dimension RBAC [50][51]. A concrete open-source reference implementation (`memory-hub`) enforces per-tool RBAC via OAuth 2.1 JWTs, gates a distinct "enterprise memory" scope behind mandatory human-in-the-loop write approval, and implements **append-only audit logging via Postgres row-level security with a dedicated INSERT-only `audit_writer` role** — even the primary application role cannot UPDATE/DELETE audit rows [48].

Regulatory audit-log requirements converge on capturing: identity binding (SSO-linked, not anonymous service account), verbatim intent/prompt capture with timestamp, full tool-call sequence with parameters/return values, decision rationale (reasoning chain), affected-data lineage (which records/subjects touched), output sensitivity classification, and cryptographic tamper-evidence (hash-chaining or append-only architecture) — mapped explicitly to GDPR Art. 30, EU AI Act Art. 12–14, and SOC 2 CC6.1 [51][50][49]. SOC 2 Type II specifically expects **365-day immutable retention** as a commonly cited baseline [50].

### 4.3 PII redaction & retention (GDPR right-to-erasure)

`> ⚠️` This is the highest-friction, least-solved area in production memory systems as of 2026.

- **Embeddings are personal data under GDPR Art. 17**, not a sanitized abstraction — research cited shows **~40% of sensitive data in sentence-length embeddings is reconstructible with straightforward inversion code, rising to ~70% for shorter texts** [43]. This directly implicates vector-store memory, not just the raw text store.
- **Soft-delete is the default and is legally insufficient.** Most vector engines mark-and-filter rather than physically erase: Milvus marks entities deleted and purges only on compaction; pgvector's HNSW index retains "dead tuples" until `VACUUM`; Qdrant applies the delete immediately with `wait=true` but frees storage only on a later optimize pass [44]. The EDPB (per cited guidance) has stated erasure must be **verifiable and irreversible** — suppressing records from query results alone does not satisfy Article 17 [43][44].
- A 2026 arXiv paper, **"Ghost Vectors"** (arXiv:2606.18497), formally demonstrates that soft-deleted embeddings remain reconstructible from HNSW graph structure on disk even after the metadata-level delete [43].
- **Cascading deletion** is required across every derived copy: primary vector index, replicas, backups, evaluation/eval-trace snapshots, semantic caches, and fine-tuning datasets — a single `DELETE WHERE id=X` addresses only one of these [43][44].
- **Recommended mitigations**: (1) physical/scheduled purge (documented compaction cadence as part of Records of Processing Activities), (2) **crypto-shredding** — encrypt each user's vectors under a per-subject key at ingestion, destroy the key on erasure request so ciphertext becomes cryptographically meaningless without a full index rewrite [43].
- **Retention-policy guidance** (practitioner consensus, not a single regulatory source): tier memory by sensitivity and apply differentiated TTLs, e.g., user preferences 6–12 months, workflow/case state until closure+30 days, operational action logs 12–24 months for audit traceability, policy memory retained until explicitly superseded [47][46]. Treat an agent's unbounded persistent memory as a **"shadow database"** — an ungoverned covert-storage channel for credentials/PII that bypasses traditional DLP unless explicitly redacted at ingestion and schema/TTL/access-controlled like any other regulated data store [46].
- Declarative policy-as-code (e.g., OPA/Rego, or bespoke `data_policy` blocks specifying `pii_detectors`, `redaction_mode`, `retention_days`) is emerging as the preferred enforcement mechanism, decoupling governance logic from application code and enabling per-tenant policy variation on a shared runtime [47][52].

### 4.4 Sandbox isolation

`[inferred, thin evidence]` Cross-tenant infrastructure isolation failures documented for LLM platforms generalize directly to memory subsystems: the April 2024 Wiz research disclosure of cross-tenant breaches on a major AI-as-a-service platform ran through **misconfigured Kubernetes environments and pickle deserialization**, granting access to other customers' models/datasets without authentication bypass — illustrating that individually-solid isolation layers can still compose insecurely [46]. Recommended mitigation for agents executing dynamic code against memory/tools: **hardware-level isolation (microVMs)** rather than shared containers, to prevent cross-tenant filesystem or memory access [46].

## 5. Production Failure Modes

### 5.1 Memory hallucination (operation-level, not just output-level)

**HaluMem** (arXiv:2511.03506) is the first benchmark to evaluate hallucination *at the memory-operation level* (extraction, updating, QA) rather than only end-to-end QA accuracy, because end-to-end evaluation can't localize which pipeline stage introduced the error [53][54]. Key findings across evaluated production-style memory systems: **memory-updating correct-update rates are below 50% for all systems tested**, and **omission rates exceed 50%** — the dominant failure is not fabrication but **failure to extract/update a memory at all**, which then silently blocks any downstream correct update [54]. A subtle but critical caveat: systems showing "hallucination rates below 2%" in this study don't necessarily indicate strong hallucination suppression — it's partly an artifact of very few samples reaching the update stage in the first place [54].

### 5.2 Stale memory / implicit conflict (STALE benchmark)

**STALE** (arXiv:2605.06527) isolates a distinct failure from hallucination: **implicit conflict** — a later observation invalidates an earlier stored belief *without explicit negation*, requiring commonsense inference to detect [55][56]. Across 1,200 evaluation queries spanning 100+ topics, **the best frontier model achieved only 55.2% overall accuracy** [55]. Three sub-failure patterns identified:
1. **State Resolution failure**: model fails to detect that a prior belief is outdated at all.
2. **Premise Resistance failure**: even when staleness is *detected*, the model still answers as though a query's false presupposition (built on the stale state) were true — detection and refusal are separate skills, and most production agents fail the second even when they pass the first [55][57].
3. **Implicit Policy Adaptation failure**: model fails to proactively propagate an updated state into downstream behavior/decisions.

The paper's core conclusion: **"the dominant failure mode is not forgetting — it is continuing to act on information that was once correct but is no longer"** [57]. This directly motivates architectures like Graphiti's bi-temporal invalidation (§1.5), which resolves staleness at write-time via explicit `invalid_at` marking rather than relying on the LLM to infer conflicts at read-time.

### 5.3 Cross-user / cross-tenant memory leakage

- **Unintentional Cross-User Contamination (UCC)** (arXiv:2604.01350): a *non-adversarial* failure class — benign, locally-valid artifacts from one user's session persist in shared agent state and are misapplied to a different user with no attacker involved. Under **raw shared state**, benign interactions alone produced **contamination rates of 57–71%** in controlled evaluation. Write-time text sanitization mitigates contamination when shared state is conversational, but **leaves substantial residual risk when shared state includes executable artifacts** (code, structured plans), where contamination manifests as **silent wrong answers** rather than visibly wrong text [58].
- **Real incident**: In March 2023, a ChatGPT Redis caching bug exposed active users' chat-history titles and payment information to other users for several hours — root cause was a cache-key collision in the Redis cluster serving conversation data, not a memory-store bug per se, but the canonical cited example of a multi-tenant isolation failure in a conversational-AI serving stack [59].
- **April 2024 Wiz disclosure**: cross-tenant breach on a major AI-as-a-service platform via Kubernetes misconfiguration + pickle deserialization, exposing other customers' private models/datasets without authentication bypass [46] (see §4.4).
- **Timing side-channel on caches**: an attacker probing shared prompt/KV caches can distinguish a cache hit vs. miss on another tenant's cached prefix at **p < 10⁻⁸ statistical significance**, enabling inference of what other tenants are querying purely from response-latency patterns [46].

### 5.4 Unbounded memory growth / context rot

"Context rot" — the phenomenon where **model recall accuracy degrades as token count grows, even well before the hard context-window limit is reached** — is now treated as a first-class failure mode distinct from context-window overflow [60]. Reported production impact: **~70–80% of LLM cost in poorly-architected agents comes from unoptimized context windows, not inference pricing itself** [62]. Naive agent loops that re-serialize full history on every step incur **quadratic (O(N²)) total token cost** as a function of turn count, since linear history growth is rebilled on every subsequent call [64]. Mitigations converge on: tiered/virtual-memory context architecture (§1), rolling compaction triggered at a **soft threshold of 60–75% of context budget** (not waiting for the hard limit), and clearing stale re-fetchable tool-result blocks while preserving the `tool_use` record [60][61][63].

### 5.5 Consolidation errors

`> ⚠️` No dedicated large-scale public post-mortem was found specifically for "consolidation pipeline" failures (i.e., a sleep-time/reflection agent corrupting memory during summarization) as a named incident category — most available evidence is folded into the HaluMem "memory updating" failure data (§5.1: <50% correct-update rate) and the STALE implicit-conflict data (§5.2), which are effectively measuring consolidation/update-time correctness rather than a separately labeled "consolidation error" class. Treat HaluMem's update-stage numbers as the best available proxy.

## 6. Enterprise System Design Scenarios

### 6.1 Benchmark landscape

| Benchmark | Focus | Key result |
|---|---|---|
| **LoCoMo** (ACL 2024, arXiv:2402.17753) | Very long-term (up to 35 sessions, ~9K–16K tokens/conversation) conversational QA, event-graph summarization, multimodal dialogue | LLMs lag human performance by ~36% overall, ~41–73% on temporal reasoning specifically; long-context/RAG techniques improve 12–66% over naive baselines but don't close the gap [65][66][67] |
| **DMR** (MemGPT's benchmark) | Deep memory retrieval | Zep: 94.8% vs. MemGPT 93.4% [10] |
| **LongMemEval** | Enterprise-style complex temporal reasoning | Zep: up to 18.5% accuracy improvement + 90% latency reduction vs. baseline [10] |
| **HaluMem** (arXiv:2511.03506) | Operation-level hallucination (extraction/update/QA) | Update-stage correct-rate <50% across all evaluated systems [53][54] |
| **STALE** (arXiv:2605.06527) | Implicit conflict / belief revision | Best model 55.2% overall accuracy [55] |

### 6.2 Trade-off matrix: vector store vs. graph memory vs. structured DB

Convergent guidance across multiple 2026 sources [68][69][70][71][72]:

| Substrate | Strength | Weakness | Best for |
|---|---|---|---|
| **Vector store** | Fast, zero cold-start, fuzzy semantic recall over unstructured text | Flat similarity only — no multi-hop reasoning, no native governance/permissions, degrades to "similar chunks" for relationship queries | Episodic memory (conversation history, session recall), unstructured document recall |
| **Graph DB / temporal knowledge graph** | Deterministic multi-hop traversal, auditable paths (every answer traces to specific nodes), bi-temporal fact invalidation (Graphiti) | Cold-start problem (empty until populated), ontology-maintenance burden, no native semantic/fuzzy search, no governance layer by default | Semantic memory (entity relationships, facts that must compose: "customer X uses product Y, which had incident Z") |
| **Relational/structured DB** | Transactional guarantees, exact field lookups, strong consistency | No semantic search, no relationship traversal | State/working memory (in-flight task progress, concurrent-write coordination) |

**Consensus production pattern**: most mature fleets run **all three substrates simultaneously**, joined by shared canonical IDs — vector store for episodic/unstructured surface area, graph for semantic/relational structure, relational DB for transactional state — rather than picking one [68][71][72]. The typical query pattern is vector search to find entry-point entities, then graph traversal to expand relationships from those entities [70].

### 6.3 Capacity planning heuristics

- **Token budget tiering** (converged practitioner guidance): Tier 1 static anchors (system prompt/tool schemas) capped at 10–15% of total context; Tier 2 active retrieved/injected memory as the second-largest allocation via just-in-time retrieval (not pre-loading full documents); Tier 3 conversation history under rolling compression rather than raw accumulation; Tier 4 scratch/reasoning traces treated as fully expendable and stripped between turns [61].
- **Compaction economics**: summarizing 5,000 tokens of tool results into a 500-token summary costs ~2,500 output tokens to generate but saves 4,500 input tokens on every subsequent turn — decisively favorable once a session continues more than ~3 turns past the compaction point [61].
- **Cost range at production scale** `[inferred, wide variance across sources]`: a 500-turn agent session with no compaction/caching can run $15–50 in input tokens alone at frontier-model rates; conversely, adding a full MemGPT-style sleep-time memory system to a simple 10-turn assistant adds 2–3× latency/complexity that may not be justified for that workload — the architecture should match the task's actual memory depth requirement [61].
- **Real-world scale reference point**: Mem0's production platform operates at **400M+ memories, 3TB+ embeddings, 150K+ isolated per-customer namespaces**, sustaining sub-100ms p90 hybrid retrieval — this is a useful anchor for "what does memory infra look like at unicorn-SaaS scale" in system-design interviews [38].

### 6.4 Industry adoption context

Gartner projects 40% of enterprise applications will integrate task-specific AI agents by end of 2026 (up from <5% in 2025); McKinsey's 2025 State of AI Global Survey found 23% of organizations actively scaling an agentic AI system in production, with 39% still experimenting — i.e., memory-system architecture decisions are being made today by a plurality of the market, but most deployments have not yet reached the scale where the failure modes in §5 fully manifest [39].

---

## Sources

- [1] https://arxiv.org/abs/2310.08560 — MemGPT: Towards LLMs as Operating Systems (original paper, Packer/Wooders et al.)
- [2] https://ar5iv.labs.arxiv.org/html/2310.08560 — MemGPT full HTML text
- [3] https://github.com/mem0ai/mem0/blob/HEAD/skills/mem0/references/architecture.md — Mem0 platform architecture (V3 pipeline, scoping, comparison table)
- [4] https://github.com/lhl/agentic-memory/blob/HEAD/ANALYSIS-arxiv-2310.08560-memgpt.md — Third-party technical analysis of MemGPT primitives
- [5] https://deepwiki.com/mem0ai/mem0/7.2-rest-api-reference — Mem0 REST API reference (v1/v2/v3 endpoints)
- [6] https://docs.letta.com/guides/core-concepts/memory/context-hierarchy/ — Letta context hierarchy (memory blocks/files/archival/RAG comparison table)
- [7] https://docs.langchain.com/oss/python/concepts/memory — LangGraph/LangChain short-term vs long-term memory concepts
- [8] https://github.com/letta-ai/skills/blob/main/letta/agent-development/SKILL.md — Letta agent-development skill (memory architecture design guidance)
- [9] https://www.anthropic.com/news/context-management — Anthropic: context editing and memory tool for Claude Developer Platform
- [10] https://arxiv.org/html/2501.13956 — Zep: A Temporal Knowledge Graph Architecture for Agent Memory
- [11] https://github.com/getzep/graphiti — Graphiti open-source temporal knowledge graph engine
- [12] https://arxiv.org/html/2504.13171v1 — Sleep-time Compute: Beyond Inference Scaling at Test-time
- [13] https://www.letta.com/blog/sleep-time-compute/ — Letta blog: Sleep-time Compute agent architecture
- [14] https://www.spheron.network/blog/sleep-time-compute-gpu-cloud/ — Sleep-time compute GPU economics analysis
- [15] https://arxiv.org/html/2309.02427v3 — Cognitive Architectures for Language Agents (CoALA)
- [16] https://atlan.com/know/episodic-memory-ai-agents/ — Episodic memory for AI agents (CoALA synthesis, reflection mechanism)
- [17] https://manthanguptaa.in/posts/chatgpt_memory/ — Reverse-engineered ChatGPT memory system (4-layer architecture)
- [18] https://llmrefs.com/blog/reverse-engineering-chatgpt-memory — ChatGPT memory reverse-engineering follow-up
- [19] https://openai.com/index/chatgpt-memory-dreaming/ — OpenAI: Dreaming — Better memory for ChatGPT (official)
- [20] https://vibeengines.com/ai-system-design/chatgpt-system-design — ChatGPT system design teardown (context builder, prefix caching)
- [21] https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context — Anthropic Help Center: Claude chat search & memory
- [22] https://claude.com/blog/memory — Anthropic: Bringing memory to teams (official product blog)
- [23] https://mem0.ai/blog/memory-eviction-and-forgetting-in-ai-agents — Mem0: memory eviction and forgetting policies (LRU/TTL/decay/supersession)
- [24] https://www.arunbaby.com/ai-agents/0005-memory-architectures/ — Memory architectures overview (Generative Agents scoring formula)
- [25] https://medium.com/@Micheal-Lanham/your-ai-agent-needs-to-forget-on-purpose-45de04cd35cf — Forgetting-by-design (MemoryBank, Ebbinghaus decay curve)
- [26] https://arxiv.org/html/2604.00131 — OBLIVION: Self-Adaptive Agentic Memory Control through Decay-Driven Activation
- [27] https://tokenmix.ai/blog/openai-embedding-pricing — OpenAI embedding pricing comparison (2026)
- [28] https://developers.openai.com/api/docs/models/text-embedding-3-large — OpenAI official pricing: text-embedding-3-large/small
- [29] https://apibenchmarks.com/vectordb/pinecone — Pinecone pricing/latency/SLA benchmark summary
- [30] https://johal.in/architecture-teardown-pinecone-110-scales-10m-vectors-low — Independent Pinecone 1.10 architecture/latency teardown
- [31] https://docs.pinecone.io/guides/get-started/test-at-scale — Pinecone official test-at-scale benchmark methodology and costs
- [32] https://arxiv.org/html/2504.19413v1 — Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory (full paper, latency/cost tables)
- [33] https://docs.convex.dev/database/advanced/occ — Optimistic concurrency control explainer (Convex docs)
- [34] https://firstprinciplesengineering.tech/01-fundamentals/01-concepts/02-architecture/04-optimistic-pessimistic-locking — Distributed locking, fencing tokens, conditional writes
- [35] https://www.alachisoft.com/blogs/how-to-use-locking-in-a-distributed-cache-for-data-consistency/ — Distributed cache locking (optimistic vs pessimistic)
- [36] https://www.respan.ai/blog/mem0 — Mem0's 99.99% reliability layer (production reliability case study)
- [37] https://mem0.ai/blog/how-sunflower-scaled-personalized-recovery-support-to-80-000-users-with-mem0 — Sunflower case study (80K users, 70-80% token reduction)
- [38] https://turbopuffer.com/customers/mem0 — Mem0 migration from pgvector to Turbopuffer (400M+ memories, 70x latency reduction)
- [39] https://mem0.ai/blog/state-of-ai-agent-memory-2026 — Mem0: State of AI Agent Memory 2026 report
- [40] https://markaicode.com/architecture/tool-fault-tolerant-architecture/ — Fault-tolerant LangChain architecture (circuit breakers, vector gateway)
- [41] https://ansezz.com/blog/circuit-breakers-vector-db/ — Circuit breakers for vector databases (three-state pattern, fallback tiers)
- [42] https://geodocs.dev/ai-agents/agent-circuit-breaker-spec — Agent circuit breaker specification (per-dependency scoping)
- [43] https://tianpan.co/blog/2026-04-20-gdpr-llm-memory-erasure-vector-database — GDPR deletion problem for LLM memory stores (embedding inversion, soft-delete)
- [44] https://dreaming.press/posts/right-to-be-forgotten-vector-database.html — Right to be forgotten in RAG/vector databases (per-engine deletion mechanics)
- [45] https://hindsight.vectorize.io/blog/2026/08/04/per-user-multi-tenant-agent-memory — Per-user memory multi-tenant patterns (bank_id hard boundary)
- [46] https://tianpan.co/blog/2026-04-10-cross-tenant-data-leakage-llm-infrastructure — Cross-tenant data leakage in shared LLM infrastructure (Wiz incident, cache timing attack)
- [47] https://firstlinesoftware.com/blog/persistent-ai-memory-risks-retention-policy/ — Enterprise persistent-memory retention policy guide
- [48] https://github.com/redhat-ai-americas/memory-hub/blob/main/docs/design/governance.md — MemoryHub governance design (RBAC, append-only audit log)
- [49] https://www.celigo.com/blog/ai-agent-security/ — AI agent security framework for enterprise governance
- [50] https://dobby-ai.com/academy/soc2-compliance-ai-agents — SOC 2 compliance mapping for AI agents
- [51] https://www.teamazing.com/blog/ai-agent-audit-trail-rbac-requirements/ — AI agent audit trail + RBAC 2026 enterprise requirements
- [52] https://jamjet.dev/blog/data-governance-pii-retention/ — Data governance for AI agents: PII, redaction, retention (declarative policy)
- [53] https://arxiv.org/pdf/2511.03506 — HaluMem: Evaluating Hallucinations in Memory Systems of Agents
- [54] https://arxiv.org/html/2511.03506v2 — HaluMem full results (update-stage accuracy <50%)
- [55] https://arxiv.org/html/2605.06527 — STALE: Can LLM Agents Know When Their Memories Are No Longer Valid?
- [56] https://arxiv.org/html/2605.06527v1 — STALE full paper (implicit conflict taxonomy)
- [57] https://tandemly.ai/research/stale-agent-memory-validity — STALE research summary/analysis
- [58] https://arxiv.org/html/2604.01350v1 — No Attacker Needed: Unintentional Cross-User Contamination in Shared-State LLM Agents
- [59] https://rafter.so/blog/multi-tenant-ai-agent-isolation — Multi-tenant isolation for AI agents (ChatGPT March 2023 Redis incident)
- [60] https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools — Claude Cookbook: context engineering (memory, compaction, tool clearing)
- [61] https://tianpan.co/blog/2026-04-17-token-budget-allocation-complex-agents — Token budget allocation framework for complex agents (tiered budget model)
- [62] https://www.linkedin.com/posts/shubhamsaboo_ai-agents-forget-even-with-1-million-token-activity-7385501709017452544-Nyy5 — Context rot summary (70-80% of cost from unoptimized context)
- [63] https://zylos.ai/research/2026-05-27-context-window-economics-persistent-agents/ — Context window economics for persistent AI agents (soft/hard compaction thresholds)
- [64] https://www.augmentcode.com/guides/ai-agent-loop-token-cost-context-constraints — AI agent loop token costs (quadratic cost growth)
- [65] https://github.com/snap-research/locomo — LoCoMo benchmark official repository
- [66] https://aclanthology.org/anthology-files/anthology-files/pdf/acl/2024.acl-long.747.pdf — Evaluating Very Long-Term Conversational Memory of LLM Agents (ACL 2024, official paper)
- [67] https://snap-research.github.io/locomo/ — LoCoMo project page (task descriptions, results)
- [68] https://www.knowlee.ai/blog/persistent-memory-for-ai-agents — Persistent memory for AI agents: graph vs vector vs hybrid (2026)
- [69] https://atlan.com/know/vector-store-vs-graph-database-agent-memory/ — Vector stores vs graph databases for agent memory
- [70] https://machinelearningmastery.com/vector-databases-vs-graph-rag-for-agent-memory-when-to-use-which/ — Vector databases vs Graph RAG decision guide
- [71] https://atlan.com/know/agentic-ai-memory-vs-vector-database/ — Agentic AI memory vs vector database architecture guide (episodic/semantic/state layers)
- [72] https://www.getmaxim.ai/articles/comparing-agent-memory-architectures-vector-dbs-graph-dbs-and-hybrid-approaches/ — Comparing agent memory architectures (vector/graph/hybrid trade-offs)
