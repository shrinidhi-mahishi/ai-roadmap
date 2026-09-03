# Module 03: Caching in LLM Systems

## What Is This?

Caching in LLM systems is like a student keeping a cheat sheet. Instead of re-reading the entire textbook every time a question comes up, you save the most useful bits for quick lookup. When the same (or a similar) question appears again, you pull the answer from your cheat sheet in milliseconds instead of waiting seconds for the LLM to regenerate it from scratch.

There are three levels of cheat sheets in production AI systems. The first is an exact-match cache -- a hash table that says "I have seen this exact question before, here is the exact answer." The second is a semantic cache -- an embedding-based lookup that says "I have seen a question close enough to this one, the answer still applies." The third is a prefix cache -- the inference engine remembering the internal computation it already did for a shared prompt prefix, so it only computes the new part. Stacking all three is how enterprises cut 70-90% of their LLM costs.

## Why It Matters

LLM inference is the single largest cost line item in production AI systems, and caching is the primary lever to reduce it. Anthropic's prompt caching alone delivers a 90% reduction in input token costs on cache hits. For a Director/VP AI role, you will be expected to design multi-tier caching architectures, reason about cache invalidation in distributed systems, and defend cost-efficiency targets to the CFO -- caching is where infrastructure strategy meets P&L impact.

---

## Part 1: System Topology & Data Flow

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            CONTROL PLANE                                    │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  ┌──────────────┐  │
│  │ Cache-Aware  │  │  Rate        │  │ Observability │  │  Cost        │  │
│  │ Router       │  │  Limiter     │  │ (OTel / HitR) │  │  Tracker     │  │
│  │ (llm-d/Ray)  │  │              │  │               │  │              │  │
│  └──────┬───────┘  └──────────────┘  └───────────────┘  └──────────────┘  │
│         │                                                                   │
├─────────┼───────────────────────────────────────────────────────────────────┤
│         │               DATA PLANE                                          │
│         ▼                                                                   │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                     MULTI-TIER CACHE                                 │   │
│  │                                                                      │   │
│  │  ┌────────────┐     ┌──────────────┐     ┌────────────────────┐     │   │
│  │  │ L1: Exact  │────▶│ L2: Semantic │────▶│ L3: Prefix / KV   │     │   │
│  │  │ SHA-256    │miss │ Embedding    │miss │ Cache (Provider    │     │   │
│  │  │ (Redis)    │     │ Similarity   │     │ or Engine-Level)   │     │   │
│  │  │ O(1) / us  │     │ (HNSW/Redis) │     │ (Anthropic/vLLM/  │     │   │
│  │  │            │     │ 5-20ms       │     │  SGLang)           │     │   │
│  │  └─────┬──────┘     └──────┬───────┘     └────────┬───────────┘     │   │
│  │        │ hit               │ hit                  │ partial hit     │   │
│  │        ▼                   ▼                      ▼                 │   │
│  │  ┌──────────────────────────────────────────────────────────────┐   │   │
│  │  │              RESPONSE (cached or generated)                  │   │   │
│  │  └──────────────────────────────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│         │ L1+L2 miss                                                        │
│         ▼                                                                   │
│  ┌──────────────┐     ┌──────────────┐                                     │
│  │ Embedding    │     │   LLM        │                                     │
│  │ Service      │     │   Inference  │                                     │
│  │ (BGE-M3)     │     │   Engine     │                                     │
│  └──────────────┘     └──────┬───────┘                                     │
│                              │                                              │
├──────────────────────────────┼──────────────────────────────────────────────┤
│                    PERSISTENCE / STATE                                       │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  ┌──────────────┐  │
│  │ Redis        │  │ Vector Index │  │ Tenant        │  │ Audit Log    │  │
│  │ (exact +     │  │ (HNSW for    │  │ Namespace     │  │ (metadata    │  │
│  │  locks)      │  │  semantic)   │  │ Isolation     │  │  only)       │  │
│  └──────────────┘  └──────────────┘  └───────────────┘  └──────────────┘  │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                         TELEMETRY                                           │
│  cache_hit_rate, cache_read_tokens, cache_write_tokens, p50/p95/p99        │
│  latency, cost_per_1k_requests, stampede_events, stale_serve_count         │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Request-Flow Narrative

**On every request:**

1. **Hash and probe L1.** The cache-aware router computes SHA-256 over the normalized request (query + model + temperature). A Redis `GET` returns in microseconds. On hit, the cached response is returned immediately -- the LLM is never called. This covers exact-repeat queries: FAQ bots, docs QA, temperature=0 deterministic workloads.

2. **Embed and probe L2.** On L1 miss, the embedding service vectorizes the query (BGE-M3 at 512-dim for latency-sensitive paths, text-embedding-3-large for higher recall). Redis HNSW vector search finds the nearest cached embedding. If cosine similarity exceeds the threshold (0.93 for production, 0.95+ for high-stakes), the cached response is returned. Metadata filters (tenant ID, locale, model version) enforce hard boundaries on top of soft similarity matching.

3. **Forward to LLM with prefix cache.** On L2 miss, the request goes to the LLM. Provider-level prompt caching (Anthropic, OpenAI, Gemini) or engine-level KV caching (vLLM APC, SGLang RadixAttention) reuses the computed attention state for the shared prefix (system prompt, tool definitions, few-shot examples). Only the new suffix (user query) requires fresh computation. This reduces input token costs by 50-90%.

4. **Backfill caches.** The generated response is written back to L1 (exact hash) and L2 (embedding + response) with appropriate TTLs and tenant namespace tags. The L3 prefix cache is managed automatically by the provider or inference engine.

5. **Emit telemetry.** Every request logs: cache tier that served it (L1/L2/L3/miss), latency, token counts (cache_read vs cache_creation), tenant ID. The cost tracker aggregates spend per tier per hour for billing dashboards.

**Combined L1+L2 yields ~54% savings on realistic workloads. Full L1+L2+L3 can exceed 80% vs naive implementation.**

---

## Part 2: Core Mechanics & Algorithms

### Prompt Caching (Provider-Level)

Prompt caching stores the encoded KV-cache state of a request's prefix on the provider's infrastructure. Subsequent requests sharing an identical byte-prefix read from cache instead of recomputing attention. The mechanism is a **strict prefix match** -- any byte difference anywhere in the prefix invalidates everything after it.

**Render order matters**: `tools` -> `system` -> `messages`. A single added tool, a reordered JSON key, or a timestamp in the system prompt invalidates the entire cache downstream.

| Provider | Mechanism | Min Tokens | TTL | Breakpoints |
|---|---|---|---|---|
| **Anthropic** | Explicit `cache_control: {type: "ephemeral"}` markers, max 4 per request | 512 (Opus 5/Fable 5) to 4,096 (Opus 4.6/Haiku 4.5) | 5 min (default) or 1 hr (`ttl: "1h"`) | Manual placement |
| **OpenAI** | Automatic (no markers); explicit caching added later with 1.25x write cost | 1,024 | ~10 min (auto) / 30 min (explicit) | Auto-detected |
| **Google Gemini** | Explicit: create cached content resource via API; Implicit: automatic | 32,768 (explicit); ~1,024-2,048 (implicit) | User-defined (explicit); ~3-5 min (implicit) | Via resource ID |

**Key design rule**: Stable content first, volatile content last. Place tool definitions, system prompts, and few-shot examples before the cache breakpoint. Place user queries, timestamps, and per-request IDs after it. Never interpolate `datetime.now()` or UUIDs into the system prompt.

**Mid-conversation operator instructions** (Opus 5, Fable 5): Use `role: "system"` messages to inject state changes without invalidating the cached prefix.

### Semantic Caching (Application-Level)

Semantic caching converts queries into vector embeddings and uses cosine similarity to match against cached responses.

**The flow:**
1. Vectorize the incoming query using an embedding model
2. Search cached embeddings via HNSW index (approximate nearest neighbor)
3. Check similarity threshold (0.85-0.95 depending on domain risk)
4. Return cached response on hit; call LLM and store result on miss

**Component choices:**

| Component | Latency-Optimized | Quality-Optimized |
|---|---|---|
| Embedding model | BGE-M3 (512-dim) | text-embedding-3-large (3072-dim) |
| Vector index | Redis HNSW | Dedicated vector DB (Qdrant, Pinecone) |
| Similarity threshold | 0.90 (higher hit rate) | 0.95+ (lower false positive rate) |
| Metadata filters | Tenant, locale | + entity type, topic, model version, safety flags |

Redis 8's native `VADD`/`VSIM` vector-set commands enable semantic cache lookups without a separate vector database, simplifying the stack for teams already running Redis.

**False positive risk**: "What is the return policy for electronics?" can match "What is the return policy for clothing?" at threshold 0.85. Mitigation: start at 0.95+, add entity-level metadata filters, implement per-domain thresholds.

### KV Cache Mechanics (Inference Engine-Level)

Three competing approaches for managing the GPU-resident KV cache during inference:

**PagedAttention (vLLM):** Breaks the KV cache into fixed-size blocks (16 tokens default) allocated on demand, like virtual memory paging. GPU memory waste drops from 60-80% (contiguous pre-allocation) to under 4%. Blocks are freed immediately when a request finishes.

**RadixAttention (SGLang):** Stores KV cache tensors in a radix tree (compressed trie) indexed by token sequence. Each node represents a token prefix; child nodes extend the parent. New requests walk the tree to find the longest matching prefix and begin computation at that branch point. Uses LRU eviction on leaf nodes. Multi-agent workloads benefit from zero-cost memory sharing for identical system prompts.

**vLLM Automatic Prefix Caching (APC):** Enabled via `--enable-prefix-caching`. Uses chain hashing (not a radix tree) to identify reusable KV blocks. Effective for fixed system prompts but less efficient than RadixAttention for branching conversation histories.

| Engine | Mechanism | Best For | Weakness |
|---|---|---|---|
| vLLM (PagedAttention) | Block-paged memory | Batch processing, unique prompts | No prefix sharing across requests |
| vLLM (APC) | Chain hashing | Fixed system prompts | Linear chains, no tree branching |
| SGLang (RadixAttention) | Radix tree LRU | Multi-agent, branching conversations | Overhead on fully unique workloads |

**Benchmarks (H100, Llama 3.1 8B):** SGLang ~16,200 tok/s vs vLLM ~12,500 tok/s (29% advantage on general workloads). On prefix-heavy workloads: SGLang up to 6.4x throughput advantage, 20-40% lower TTFT. On unique-prompt workloads: within 5% of each other.

### Multi-Tier Cache Architecture

| Layer | Mechanism | Hit Savings | Latency | Best For |
|---|---|---|---|---|
| **L1: Exact** | SHA-256 hash of (query + model) | 100% (skips LLM) | O(1), microseconds | temperature=0, docs QA, fixed-corpus RAG |
| **L2: Semantic** | Vector similarity on embeddings | 100% (skips LLM) | 5-20ms | Paraphrased queries, FAQ bots |
| **L3: Prefix** | KV cache reuse at inference engine | 50-90% cost reduction | Reduced TTFT | Shared system prompts, multi-turn |

### Cache-Aware Routing

Cache-aware routing creates a global view of the cluster's KV cache across replicas and routes requests to the replica most likely to have a cache hit.

| System | Approach | Results |
|---|---|---|
| **llm-d** | Global KV cache view, disaggregated memory pool | 57x faster response, 2x throughput |
| **GKE Inference Gateway** | llm-d Endpoint Picker for vLLM replicas | Google Cloud-managed |
| **NVIDIA Dynamo** | KV cache-aware routing with cluster-wide visibility | NVIDIA ecosystem |
| **Ray Serve** | PrefixCacheAffinityRouter, sticky prefix routing | Open-source, Ray ecosystem |
| **LMCache** | Cross-node KV cache management (locate, move, pin, compress) | Used in vLLM, Dynamo, llm-d, KServe |

---

## Part 3: Token Economics & NFR Analysis

### Anthropic Prompt Caching Pricing

| Model | Base Input $/MTok | Cache Write (5min) | Cache Write (1hr) | Cache Read | Output $/MTok |
|---|---|---|---|---|---|
| Claude Opus 5 | $5.00 | $6.25 (1.25x) | $10.00 (2.0x) | $0.50 (0.1x) | $25.00 |
| Claude Sonnet 5 | $2.00 | $2.50 | $4.00 | $0.20 | $10.00 |
| Claude Sonnet 4.6 | $3.00 | $3.75 | $6.00 | $0.30 | $15.00 |
| Claude Haiku 4.5 | $1.00 | $1.25 | $2.00 | $0.10 | $5.00 |

Batch API discounts stack: Sonnet 4.6 batch + cache read = 95% off list price.

### Break-Even Analysis

```
5-min TTL: Break-even at 2nd request
  2 requests: 1.25x (write) + 0.1x (read) = 1.35x  vs  2.0x uncached
  Savings: 32.5%

1-hr TTL: Break-even at 3rd request
  3 requests: 2.0x (write) + 0.2x (read) + 0.2x (read) = 2.4x  vs  3.0x uncached
  Savings: 20% (grows with each subsequent read)

Key: Cache reads refresh TTL at no cost. Continuous traffic keeps 5-min cache
warm indefinitely. Use 1-hr TTL only when request gaps exceed 5 minutes.
```

### Cost Formula: $ per 1,000 Runs (Sonnet 4.6, 10K input tokens)

```
Scenario 1: No caching
  1,000 x 10K x $3.00/MTok = $30.00

Scenario 2: L3 prefix cache only (90% hit rate)
  100 writes: 100 x 10K x $3.75/MTok  = $3.75
  900 reads:  900 x 10K x $0.30/MTok  = $2.70
  Total: $6.45  (78% savings)

Scenario 3: L1 exact (20% hit) + L2 semantic (25% hit) + L3 prefix (remaining)
  L1 hits:  200 x $0 (no LLM call)    = $0.00
  L2 hits:  250 x $0 (no LLM call)    = $0.00
  L3 write: 55 x 10K x $3.75/MTok     = $2.06
  L3 read:  495 x 10K x $0.30/MTok    = $1.49
  Total: $3.55  (88% savings)

Scenario 4: Add Batch API (50% off base)
  Remaining 550 calls via batch + L3:  $1.78
  Total: $1.78  (94% savings)
```

### Latency SLA Targets

| Metric | L1 Hit | L2 Hit | L3 Hit | Full Miss |
|---|---|---|---|---|
| **p50** | < 5ms | < 25ms | 200-500ms (reduced TTFT) | 1-3s |
| **p95** | < 10ms | < 50ms | 500-800ms | 3-8s |
| **p99** | < 20ms | < 100ms | 800ms-1.2s | 5-15s |

### Cache Hit Rate Monitoring

| Metric | Target | Alert Threshold | Action |
|---|---|---|---|
| L1 hit rate | 15-25% (FAQ/QA workloads) | < 10% sustained | Check hash normalization, query dedup |
| L2 hit rate | 25-40% (paraphrase-heavy) | < 15% sustained | Tune embedding model, lower threshold |
| L3 hit rate (cache_read / total input tokens) | > 50% | Sustained 0% | Audit prompt assembly for volatile prefixes |
| Combined cost savings | > 60% | < 40% | Review traffic patterns, cache tier config |

### Cross-Provider Comparison

| Provider | Write Cost | Read Discount | Auto vs Manual | Best For |
|---|---|---|---|---|
| **Anthropic** | 1.25x (5min) / 2.0x (1hr) | 90% off | Manual breakpoints | Control over cache placement |
| **OpenAI** | Free (auto) / 1.25x (explicit) | 75-90% off | Automatic | Zero-config caching |
| **Gemini** | + storage fee ($1.00/MTok/hr) | 75% off (explicit) / 90% (implicit) | Both | Long-lived context (hours) |

---

## Part 4: Distributed Resilience & Security

### Cache Stampede Prevention

When a popular cached item expires, concurrent requests simultaneously attempt to regenerate it, overwhelming the backend. In agentic AI systems, this is amplified by semantic correlation: thousands of agents share the same knowledge base with uniform TTLs, causing correlated expiry.

**Real incident**: A nightly knowledge base refresh synchronized cache expiry across ~3,000 agents, causing database connection pool saturation.

**Five prevention techniques, ordered by implementation complexity:**

| Technique | Mechanism | Complexity | Trade-off |
|---|---|---|---|
| **TTL Jitter** | `TTL = base + random(-jitter, +jitter)` | Low | Spreads but does not eliminate stampedes |
| **Stale-While-Revalidate** | Return stale value; background regenerate | Low | Serves potentially outdated responses |
| **Request Coalescing** | Multiple requests share one backend call | Medium | Requires singleflight coordination |
| **Distributed Locking** | Redis `SET NX` with TTL; one regenerates | Medium | Lock holder failure blocks all waiters |
| **Probabilistic Early Expiration (XFetch)** | Recompute if `-beta * delta * ln(rand()) > ttl_remaining` | Medium | Occasional unnecessary recomputation |

### Stale Cache Detection and Invalidation

- **Time-based**: TTL per cache tier. L1 exact: 1-4 hours. L2 semantic: 30-60 minutes. L3 prefix: provider-managed (5 min / 1 hr).
- **Event-based**: Knowledge base update triggers invalidation of affected cache entries via pub/sub.
- **Version-based**: Model version or prompt template version included in cache key. Model upgrade automatically invalidates all prior entries.
- **Anthropic behavior**: Cache reads refresh the TTL timer. Generation time counts against TTL (a 4-minute generation on a 5-minute TTL leaves ~1 minute of cache validity).

### PII in Cached Responses

Research on C4 and Pile corpora found substantial PII (SSNs, credit card numbers, phone numbers) in training data. When cache entries embedding such prefixes are shared across tenants, attackers can exploit latency probes to reconstruct sensitive content.

**Controls:**
- Output scanning for PII before writing to L1/L2 cache (detection layer when isolation controls fail)
- Per-tenant cache namespace isolation (Anthropic enforces this natively per workspace/organization)
- Metadata-only audit logging (log category + event + trace ID, never raw content)
- TTL on all cached content containing user-generated input

### Multi-Tenant Cache Isolation

**PROMPTPEEK Attack (NDSS 2025):** Demonstrated that in multi-tenant LLM serving, an adversary can craft requests to determine if their request reused KV cache stored for a victim user, enabling prompt reconstruction via timing side-channels. Post-disclosure, at least five providers disabled global cache sharing across organizations.

**SafeKV (NDSS 2026):** Reduces TTFT overhead of per-tenant isolation by up to 2.66x vs full isolation, restoring KV reuse efficiency while enforcing privacy boundaries.

**Isolation model by provider:**
- Anthropic: Cache isolated per workspace (Claude API, Claude Platform on AWS, Foundry) or per organization (Bedrock, Google Cloud). No cross-org sharing. Cache entries held in memory only, not stored at rest.
- Self-hosted (vLLM/SGLang): Tenant isolation must be implemented at the application layer via cache key namespacing or dedicated inference instances.

### Data Residency

KV-cache tensors sit in a contested legal space between GDPR and CLOUD Act. Most Data Processing Agreements do not mention KV-cache residency. For regulated industries: confirm cache storage location with provider, consider explicit cache TTL controls, document cache handling in DPA addendum.

### Circuit Breakers for Cache Backends

When Redis (or any cache backend) goes down, the system must degrade gracefully:

- **Cache miss fallback**: All requests route directly to the LLM. Cost spikes, but availability is preserved.
- **Circuit breaker pattern**: After N consecutive cache backend failures (typically 5-10), open the circuit -- stop attempting cache lookups for a cooldown period (30-60s). Avoids adding cache timeout latency to every request during an outage.
- **Monitoring**: Alert on circuit breaker state transitions. Track cache backend availability as a separate SLI from overall system availability.

### Durable Execution for Cache Warming Pipelines

Cache warming operations (batch embedding jobs, index rebuilds, bulk invalidation) are long-running, multi-step workflows that must survive infrastructure failures. Durable execution frameworks manage these as resumable state machines rather than fragile scripts.

**Temporal-based pattern:**

- Each cache warming pipeline is a Temporal workflow with explicit checkpointing at each stage: embed batch -> upsert to vector index -> update L1 keys -> verify consistency.
- On failure, the workflow resumes from the last completed checkpoint -- no re-embedding of already-processed batches.
- Retry policies are per-activity: embedding service calls retry 3x with exponential backoff; vector index upserts retry 5x (idempotent). Timeouts are set per activity (embedding: 30s, upsert: 10s) and per workflow (total: 2 hours).
- Dead-letter queues capture failed cache updates that exhaust retries. A separate remediation workflow processes the DLQ: logs the failure, alerts the on-call, and optionally retries with a different strategy (e.g., single-record upsert instead of batch).

**Kafka-backed pattern:**

- Cache invalidation events are published to a Kafka topic with exactly-once semantics.
- Consumers process invalidation events and update cache tiers. Consumer offsets provide the checkpoint mechanism -- on restart, processing resumes from the last committed offset.
- Event-sourced cache invalidation: the Kafka topic is the source of truth for what should be cached. The cache state can be rebuilt from scratch by replaying the topic from offset zero.

### Failure Classification for Cache Operations

Not all cache failures are equal. Classifying failures determines the correct automated response.

| Category | Examples | Detection | Automated Response |
|---|---|---|---|
| **Transient** | Redis timeout, network blip, embedding service 503, connection pool exhaustion | Error code (timeout, 503, connection refused), resolves on retry | Retry with exponential backoff + jitter (max 3 attempts). Log as warning. No alert unless retry budget exhausted. |
| **Permanent** | Corrupted embedding (NaN/Inf values), schema mismatch (embedding dimension changed), incompatible serialization format | Deterministic error on every attempt, data validation failure | Skip the entry, alert the on-call, log as error with full context. Do not retry -- retrying a permanent failure wastes resources and delays recovery. |
| **Adversarial** | Cache poisoning (attacker inserts crafted entries to influence LLM responses), cache key collision attack, timing side-channel exploitation | Anomaly detection on cache write patterns, content integrity checks (signed entries), latency distribution analysis | Quarantine the affected cache namespace, alert security team, trigger forensic log export. Do not serve quarantined entries. Invalidate and rebuild the affected cache segment from source of truth. |

**Key invariant**: Transient failures retry. Permanent failures skip and alert. Adversarial failures quarantine and investigate. Misclassifying a permanent failure as transient causes retry storms. Misclassifying an adversarial failure as transient masks an active attack.

### Idempotency in Cache Operations

Cache writes must be idempotent: writing the same key with the same content produces the same result regardless of how many times the operation executes. This is critical in distributed systems where retries, duplicate messages, and at-least-once delivery are the norm.

**Content-hash keys:**

- L1 cache keys are derived from `SHA-256(query + model + tenant_id)`. Writing the same query twice produces the same key and overwrites with identical content -- a no-op in effect.
- L2 cache entries use the embedding vector as the identity. Re-embedding the same query produces the same vector (deterministic embedding models). Upserting the same vector with the same response is idempotent.

**Idempotency keys for cache invalidation API calls:**

- Every cache invalidation request carries a unique idempotency key (e.g., `invalidation:{event_id}:{timestamp}`).
- The cache service tracks processed idempotency keys in a TTL-bounded set (Redis `SET NX` with expiry matching the invalidation window).
- Duplicate invalidation requests (same idempotency key) are acknowledged but not re-processed. This prevents double-invalidation from Kafka consumer rebalancing or HTTP retries.
- Without idempotency keys, a retried invalidation during a cache warming cycle can evict a freshly warmed entry, causing unnecessary cache misses.

### Zero-Trust Cache Architecture

Assume-breach posture applied to the cache layer. Every component verifies identity, every cached entry is protected at rest and in transit, and no implicit trust exists between the application and the cache cluster.

| Principle | Implementation |
|---|---|
| **Encryption at rest** | All cached LLM responses encrypted with AES-256 before storage in Redis. Decryption keys managed via a secrets manager (Vault, AWS KMS), never stored alongside cached data. |
| **Mutual TLS** | Application-to-cache and cache-replica-to-replica communication uses mTLS with short-lived certificates. No plaintext cache traffic on the wire. Prevents man-in-the-middle interception of cached responses. |
| **Signed cache entries** | Each cache entry includes an HMAC signature computed over the response content + metadata. On read, the signature is verified before serving. Detects tampering (cache poisoning) where an attacker modifies stored entries. |
| **Per-tenant encryption keys** | Each tenant's cached data is encrypted with a tenant-specific key. Compromising one tenant's key does not expose other tenants' cached responses. Key rotation is per-tenant, independent. |
| **No implicit trust** | The cache layer authenticates every request via service identity tokens. Cache clients cannot read or write without a valid, scoped credential. Network policies restrict cache port access to authorized services only. |

### RBAC for Cache Operations

Role-based access control for cache management, mapped to specific cache API operations.

| Role | Permitted Operations | Use Case |
|---|---|---|
| **reader** | `GET`, `VSIM` (vector similarity lookup) | Application services performing cache lookups during inference. Read-only -- cannot modify or evict cache entries. |
| **writer** | `SET`, `VADD` (vector add), `SETEX` (set with TTL) | Cache warming pipelines, LLM response backfill. Can insert and update entries but cannot evict or purge. |
| **admin** | `DEL`, `FLUSHDB`, `CONFIG SET` (TTL policies), `EVICT` (manual eviction) | Operations team managing cache capacity, TTL configuration, emergency purges. All admin actions are audit-logged. |
| **auditor** | `VIEW_ACCESS_LOGS`, `VIEW_METRICS`, `EXPORT_AUDIT` | Compliance and security teams reviewing cache access patterns, investigating incidents. Read-only access to logs and metrics, no access to cached content. |

**Enforcement**: Roles map to Redis ACLs (Redis 6+) or application-layer middleware for managed cache services. Service accounts are bound to exactly one role. No shared credentials between roles. Role changes require admin approval and are logged to the immutable audit trail.

### PII Pipeline for Cached LLM Responses

LLM responses may contain PII even when the prompt did not -- the model can generate plausible names, emails, phone numbers, or regurgitate memorized training data. Caching such responses amplifies the exposure. This pipeline scans, redacts, and audits before any response enters the cache.

**Detection:**

- Scan every LLM response before writing to L1 or L2 cache.
- Detection stack: Microsoft Presidio (NER-based, configurable entity recognizers) + regex patterns (SSN, email, phone, credit card with Luhn validation).
- Run asynchronously on the response backfill path to avoid adding latency to the critical serving path.

**Redaction:**

- If PII is detected, strip or mask it before cache storage: `John Smith` -> `[REDACTED_NAME]`, `john@example.com` -> `[REDACTED_EMAIL]`.
- The unredacted response is still returned to the requesting user (they generated it) -- only the cached copy is redacted.
- Configurable policy per tenant: `strip` (remove PII tokens entirely), `mask` (replace with typed placeholders), or `block` (do not cache the response at all).

**Audit:**

Every cache entry that triggered PII detection is logged to an immutable audit store:
- Cache entry hash (SHA-256 of the key, not the content)
- PII detection type(s) found (EMAIL, PHONE, NAME, SSN, etc.)
- Action taken (stripped, masked, blocked from cache)
- Timestamp (ISO 8601, UTC)
- Tenant ID and model version
- Detection confidence score

The audit log enables compliance reporting (GDPR Article 30 records of processing) and incident investigation (how many cached entries contained PII over a given period).

---

## Part 5: Production Enterprise Code

### Multi-Tier Cache with Stampede Prevention

```python
"""
Multi-tier LLM cache: L1 exact -> L2 semantic -> L3 provider prefix cache.
Includes cache stampede prevention via probabilistic early recomputation (XFetch).

Dependencies: redis, numpy, openai (for embeddings)
"""

import hashlib
import json
import math
import random
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import redis


@dataclass
class CacheEntry:
    response: str
    embedding: Optional[np.ndarray]
    created_at: float
    ttl: float
    compute_delta: float  # time to generate this response (for XFetch)
    tenant_id: str
    model_version: str


class MultiTierLLMCache:
    """
    Production multi-tier cache for LLM responses.

    L1: Exact match via SHA-256 hash (Redis string).
    L2: Semantic match via cosine similarity (in-memory HNSW or Redis vector).
    L3: Provider-level prefix caching (handled externally by Anthropic/OpenAI).

    Stampede prevention via XFetch probabilistic early recomputation.
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        embed_fn,         # callable: str -> np.ndarray
        llm_fn,           # callable: str -> str
        l1_ttl: int = 3600,
        l2_ttl: int = 1800,
        similarity_threshold: float = 0.93,
        xfetch_beta: float = 1.0,
    ):
        self.redis = redis_client
        self.embed_fn = embed_fn
        self.llm_fn = llm_fn
        self.l1_ttl = l1_ttl
        self.l2_ttl = l2_ttl
        self.similarity_threshold = similarity_threshold
        self.xfetch_beta = xfetch_beta

        # L2 semantic index: list of (embedding, CacheEntry) pairs.
        # Production: replace with Redis vector index or dedicated vector DB.
        self._semantic_index: list[tuple[np.ndarray, CacheEntry]] = []

    # ── L1: Exact Match ──────────────────────────────────────────────────

    def _l1_key(self, query: str, model: str, tenant_id: str) -> str:
        raw = json.dumps(
            {"q": query, "m": model, "t": tenant_id}, sort_keys=True
        )
        return f"llm_cache:l1:{hashlib.sha256(raw.encode()).hexdigest()}"

    def _l1_lookup(self, query: str, model: str, tenant_id: str) -> Optional[str]:
        key = self._l1_key(query, model, tenant_id)
        data = self.redis.get(key)
        if data is None:
            return None

        entry = json.loads(data)
        ttl_remaining = entry["created_at"] + entry["ttl"] - time.time()

        # XFetch: probabilistic early recomputation
        if self._should_recompute_early(entry["compute_delta"], ttl_remaining):
            return None  # treat as miss to trigger background recomputation

        return entry["response"]

    def _l1_store(
        self, query: str, model: str, tenant_id: str,
        response: str, compute_delta: float
    ) -> None:
        key = self._l1_key(query, model, tenant_id)
        entry = {
            "response": response,
            "created_at": time.time(),
            "ttl": self.l1_ttl,
            "compute_delta": compute_delta,
            "tenant_id": tenant_id,
        }
        self.redis.setex(key, self.l1_ttl, json.dumps(entry))

    # ── L2: Semantic Match ───────────────────────────────────────────────

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    def _l2_lookup(
        self, query_embedding: np.ndarray, tenant_id: str, model_version: str
    ) -> Optional[str]:
        best_score = -1.0
        best_entry: Optional[CacheEntry] = None
        now = time.time()

        for emb, entry in self._semantic_index:
            # Hard metadata boundary: tenant and model version must match
            if entry.tenant_id != tenant_id:
                continue
            if entry.model_version != model_version:
                continue
            # TTL check
            if now > entry.created_at + entry.ttl:
                continue

            score = self._cosine_similarity(query_embedding, emb)
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_score >= self.similarity_threshold and best_entry is not None:
            ttl_remaining = best_entry.created_at + best_entry.ttl - now
            if self._should_recompute_early(
                best_entry.compute_delta, ttl_remaining
            ):
                return None
            return best_entry.response

        return None

    def _l2_store(
        self, query_embedding: np.ndarray, response: str,
        compute_delta: float, tenant_id: str, model_version: str
    ) -> None:
        entry = CacheEntry(
            response=response,
            embedding=query_embedding,
            created_at=time.time(),
            ttl=self.l2_ttl,
            compute_delta=compute_delta,
            tenant_id=tenant_id,
            model_version=model_version,
        )
        self._semantic_index.append((query_embedding, entry))

    # ── XFetch: Stampede Prevention ──────────────────────────────────────

    def _should_recompute_early(
        self, compute_delta: float, ttl_remaining: float
    ) -> bool:
        """
        Probabilistic early recomputation (XFetch algorithm).

        Returns True if this request should proactively regenerate the cache
        entry before TTL expiry, spreading recomputation load across time
        instead of concentrating it at the expiry boundary.

        Formula: recompute if  -beta * delta * ln(random()) > ttl_remaining
        - beta: tuning parameter (1.0 = standard, higher = more aggressive)
        - delta: time it took to compute the original response
        - As ttl_remaining shrinks, probability of early recompute rises
        """
        if ttl_remaining <= 0:
            return True
        threshold = -self.xfetch_beta * compute_delta * math.log(random.random())
        return threshold > ttl_remaining

    # ── Main Lookup ──────────────────────────────────────────────────────

    def lookup(
        self,
        query: str,
        model: str = "claude-sonnet-4-6",
        tenant_id: str = "default",
        model_version: str = "v1",
    ) -> dict:
        """
        Multi-tier cache lookup: L1 exact -> L2 semantic -> LLM call.

        Returns dict with keys:
            response: str       -- the LLM response
            cache_tier: str     -- "L1", "L2", or "miss"
            latency_ms: float   -- wall-clock time for this lookup
        """
        start = time.time()

        # L1: Exact match
        l1_result = self._l1_lookup(query, model, tenant_id)
        if l1_result is not None:
            return {
                "response": l1_result,
                "cache_tier": "L1",
                "latency_ms": (time.time() - start) * 1000,
            }

        # L2: Semantic match
        query_embedding = self.embed_fn(query)
        l2_result = self._l2_lookup(query_embedding, tenant_id, model_version)
        if l2_result is not None:
            return {
                "response": l2_result,
                "cache_tier": "L2",
                "latency_ms": (time.time() - start) * 1000,
            }

        # Miss: call LLM
        llm_start = time.time()
        response = self.llm_fn(query)
        compute_delta = time.time() - llm_start

        # Backfill both cache tiers
        self._l1_store(query, model, tenant_id, response, compute_delta)
        self._l2_store(
            query_embedding, response, compute_delta, tenant_id, model_version
        )

        return {
            "response": response,
            "cache_tier": "miss",
            "latency_ms": (time.time() - start) * 1000,
        }


# ── Cache-Aware Request Router ──────────────────────────────────────────

class CacheAwareRouter:
    """
    Routes requests to the inference replica most likely to have a KV cache hit.

    Maintains a prefix-to-replica affinity map. Requests with similar system
    prompts are routed to the same replica, maximizing prefix cache reuse
    (vLLM APC or SGLang RadixAttention).

    Production equivalent: llm-d, Ray Serve PrefixCacheAffinityRouter.
    """

    def __init__(self, replicas: list[str]):
        self.replicas = replicas
        # prefix_hash -> replica_id mapping
        self._affinity: dict[str, str] = {}

    def _prefix_hash(self, system_prompt: str, tools: list[str]) -> str:
        content = json.dumps(
            {"system": system_prompt, "tools": sorted(tools)}, sort_keys=True
        )
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def route(self, system_prompt: str, tools: list[str]) -> str:
        """Return the replica ID that should handle this request."""
        ph = self._prefix_hash(system_prompt, tools)

        if ph in self._affinity:
            replica = self._affinity[ph]
            if replica in self.replicas:
                return replica

        # Consistent hashing fallback: deterministic assignment
        idx = int(ph, 16) % len(self.replicas)
        replica = self.replicas[idx]
        self._affinity[ph] = replica
        return replica

    def remove_replica(self, replica_id: str) -> None:
        """Remove a failed replica and reassign its prefixes on next route()."""
        self.replicas = [r for r in self.replicas if r != replica_id]
        self._affinity = {
            k: v for k, v in self._affinity.items() if v != replica_id
        }


# ── Prompt Assembly for Maximum Cache Hits ──────────────────────────────

def build_cacheable_prompt(
    system_prompt: str,
    tool_definitions: list[dict],
    few_shot_examples: list[dict],
    user_query: str,
) -> list[dict]:
    """
    Assemble an Anthropic API request maximizing prompt cache reuse.

    Render order (Anthropic): tools -> system -> messages.
    Stable content goes first with cache breakpoints; volatile content last.

    Returns the messages list for the Anthropic Messages API.
    The caller should set tools and system separately.
    """
    # Cache breakpoint 1: system prompt (stable across all requests)
    system_block = {
        "type": "text",
        "text": system_prompt,
        "cache_control": {"type": "ephemeral"},
    }

    # Cache breakpoint 2: few-shot examples (stable across a session)
    few_shot_messages = []
    for i, example in enumerate(few_shot_examples):
        msg = {"role": example["role"], "content": example["content"]}
        # Place cache breakpoint on the last few-shot message
        if i == len(few_shot_examples) - 1:
            msg["content"] = [
                {
                    "type": "text",
                    "text": example["content"],
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        few_shot_messages.append(msg)

    # User query: volatile, no cache marker
    user_message = {"role": "user", "content": user_query}

    return {
        "system": [system_block],
        "tools": tool_definitions,  # rendered first by API, cached if stable
        "messages": few_shot_messages + [user_message],
    }
```

### Cache Hit Rate Monitor

```python
"""
Lightweight cache telemetry tracker for production monitoring.
Emits metrics compatible with Prometheus / OpenTelemetry.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class CacheMetrics:
    """Aggregated cache metrics over a time window."""
    l1_hits: int = 0
    l2_hits: int = 0
    l3_hits: int = 0
    misses: int = 0
    stampede_events: int = 0
    stale_serves: int = 0
    total_tokens_saved: int = 0
    total_cost_saved_usd: float = 0.0
    latencies_ms: list = field(default_factory=list)

    @property
    def total_requests(self) -> int:
        return self.l1_hits + self.l2_hits + self.l3_hits + self.misses

    @property
    def combined_hit_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return (self.l1_hits + self.l2_hits + self.l3_hits) / self.total_requests

    @property
    def p50_latency_ms(self) -> float:
        return self._percentile(0.50)

    @property
    def p95_latency_ms(self) -> float:
        return self._percentile(0.95)

    @property
    def p99_latency_ms(self) -> float:
        return self._percentile(0.99)

    def _percentile(self, pct: float) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_lat = sorted(self.latencies_ms)
        idx = int(len(sorted_lat) * pct)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    def report(self) -> dict:
        return {
            "total_requests": self.total_requests,
            "l1_hit_rate": self.l1_hits / max(self.total_requests, 1),
            "l2_hit_rate": self.l2_hits / max(self.total_requests, 1),
            "l3_hit_rate": self.l3_hits / max(self.total_requests, 1),
            "combined_hit_rate": self.combined_hit_rate,
            "p50_ms": self.p50_latency_ms,
            "p95_ms": self.p95_latency_ms,
            "p99_ms": self.p99_latency_ms,
            "tokens_saved": self.total_tokens_saved,
            "cost_saved_usd": round(self.total_cost_saved_usd, 2),
            "stampede_events": self.stampede_events,
        }
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: High-Volume Customer Support Bot (50K queries/day)

**Problem statement:** A financial services company operates a customer support bot handling 50,000 queries/day across 15 product lines. Current monthly LLM spend is $45,000. The VP of Engineering wants a 60% cost reduction without increasing p95 latency beyond 3 seconds. The bot must maintain tenant isolation across three business units, each with different compliance requirements (PCI-DSS for payments, SOC 2 for general, HIPAA for insurance).

**Proposed architecture:**

```
User Query
    │
    ▼
┌──────────────────┐
│ Tenant Namespace  │──── PCI / SOC2 / HIPAA isolation
│ Resolver          │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐     ┌──────────────────┐
│ L1: Exact Match  │────▶│ L2: Semantic     │
│ Redis, SHA-256   │miss │ Redis HNSW       │
│ TTL=3600s        │     │ BGE-M3, t=0.93   │
│                  │     │ TTL=1800s         │
└────────┬─────────┘     └────────┬─────────┘
         │ hit                    │ miss
         ▼                       ▼
┌──────────────────┐     ┌──────────────────┐
│ Return cached    │     │ Anthropic API    │
│ response         │     │ w/ prompt cache  │
│                  │     │ (system+FAQ docs │
│                  │     │  in prefix)      │
└──────────────────┘     └──────────────────┘
```

**Trade-off matrix:**

| Alternative | Cost Reduction | Latency Impact | Complexity | Verdict |
|---|---|---|---|---|
| **A: L1+L2+L3 (proposed)** | ~70% ($31.5K saved/mo) | p95 < 2.5s (improved) | Medium | Selected |
| **B: L1 exact only** | ~20% ($9K saved/mo) | p95 unchanged | Low | Insufficient savings |
| **C: Aggressive semantic (t=0.85)** | ~55% ($24.7K saved/mo) | p95 < 2s | Medium | False positive risk too high for financial |

**Decision rationale:** Alternative A is selected. L1 handles the ~20% exact-repeat queries (password resets, balance checks). L2 catches ~25% paraphrased variants (FAQ-style questions about fees, policies). L3 reduces cost on the remaining 55% by caching the shared system prompt + FAQ documents as prefix. The 0.93 similarity threshold balances hit rate against false positive risk in a regulated domain. Per-tenant Redis key namespacing (`{tenant}:llm_cache:*`) enforces isolation without separate Redis instances. Entity-level metadata filters (product line, query category) prevent cross-product answer leakage.

---

### Scenario 2: Multi-Agent Research System (100 agents, shared knowledge base)

**Problem statement:** An enterprise R&D team runs a multi-agent research system where 100 agents share a 200-page internal knowledge base as their system prompt. Agents branch into parallel research threads (5-10 per topic), creating tree-shaped conversation histories. Current inference cost: $120K/month on self-hosted H100 cluster. The team wants to reduce GPU utilization by 50% to free capacity for training jobs, while keeping p50 TTFT under 500ms.

**Proposed architecture:**

```
Agent Swarm (100 agents)
         │
         ▼
┌──────────────────────────────────────────────┐
│ Cache-Aware Router (llm-d / Ray Serve)       │
│ Routes by system prompt prefix hash          │
│ Maintains prefix -> replica affinity map     │
└─────────┬──────────────┬─────────────────────┘
          │              │
          ▼              ▼
┌─────────────┐  ┌─────────────┐
│ SGLang       │  │ SGLang       │  (N replicas)
│ Replica A    │  │ Replica B    │
│ RadixAttention│  │ RadixAttention│
│ (shared KB   │  │ (shared KB   │
│  in tree)    │  │  in tree)    │
└──────┬──────┘  └──────┬──────┘
       │                │
       ▼                ▼
┌──────────────────────────────────┐
│ LMCache: Cross-Node KV Mgmt     │
│ - Locate cached prefixes        │
│ - Prefetch KV tensors (KVFlow)  │
│ - LRU eviction on leaf nodes    │
└──────────────────────────────────┘
```

**Trade-off matrix:**

| Alternative | GPU Reduction | TTFT p50 | Branching Support | Verdict |
|---|---|---|---|---|
| **A: SGLang + RadixAttention + llm-d (proposed)** | ~60% | < 300ms | Excellent (tree structure) | Selected |
| **B: vLLM + APC** | ~35% | < 500ms | Poor (linear chains only) | Insufficient for tree conversations |
| **C: Separate instance per agent group** | ~20% | < 200ms | N/A (no sharing) | Defeats purpose, wastes GPU |

**Decision rationale:** Alternative A is selected. RadixAttention's radix tree structure naturally maps to the branching conversation histories in multi-agent research. The shared 200-page knowledge base is stored once in the tree root and reused by all 100 agents at zero additional memory cost. Cache-aware routing via llm-d ensures requests land on the replica already holding the relevant prefix, achieving the 57x response time improvement demonstrated in benchmarks. LMCache with KVFlow enables proactive prefetching of KV tensors before agent activation, overlapping GPU computation with memory transfer. TTL jitter (base=300s, jitter=60s) on agent activation prevents stampedes during the hourly knowledge base refresh. vLLM APC (Alternative B) was rejected because chain hashing cannot efficiently represent the tree-shaped conversation histories -- each branch would require separate linear cache chains, wasting 40-60% of potential reuse.

---

## Quick Reference: Production Failure Modes

| # | Failure Mode | Symptom | Root Cause | Detection | Fix |
|---|---|---|---|---|---|
| 1 | Silent cache invalidation | `cache_read_input_tokens` = 0 | Non-deterministic prefix (`datetime.now()`, unsorted JSON, UUID in system prompt) | Monitor read/write ratio | Audit prompt assembly; move volatile content after breakpoint |
| 2 | Cache stampede | Coordinated backend overload at TTL boundaries | Uniform TTL across agents, correlated expiry | Spike correlation with TTL intervals | TTL jitter + stale-while-revalidate + XFetch |
| 3 | Semantic false positives | Wrong cached answer for similar query | Threshold too low, missing metadata filters | Sample-based quality audit, regeneration rate | Raise threshold to 0.95+, add entity filters |
| 4 | TTL regression | Unexpected cost spike (20-32% cache write increase) | Provider changed default TTL silently | Monitor cache write frequency per session | Explicit TTL specification (`ttl: "1h"`) |
| 5 | Cross-tenant leakage | Prompt reconstruction via timing side-channel | Shared KV cache across tenants (PROMPTPEEK) | Latency anomaly detection on cache ops | Per-tenant namespace isolation |
| 6 | Prefix too short | `cache_creation_input_tokens` = 0, no error | Below model-dependent minimum (512-4096 tokens) | Check creation tokens on first request | Pad prefix or restructure prompt |

---

## Quick Reference: Decision Matrix

| Traffic Pattern | Recommended Strategy | Expected ROI |
|---|---|---|
| High-frequency, same queries | L1 exact match (Redis) | 90%+ cost reduction |
| Diverse phrasing, same intent | L2 semantic cache | 30-50% cost reduction |
| Shared system prompt, varying queries | L3 prefix caching (provider) | 50-90% input cost reduction |
| Infrequent, unique queries | No caching (write premium wasted) | Net negative if cached |
| Batch processing, unique prompts | Batch API (50% off), skip prefix cache | 50% cost reduction |
| Agent swarm, shared knowledge base | RadixAttention + TTL jitter + cache-aware routing | 60-80% compute reduction |

---

## Tooling Landscape

| Tool | Layer | Notes |
|---|---|---|
| Redis + RedisVL SemanticCache | L1 + L2 | Production-proven, sub-ms exact, 5-20ms semantic |
| Momento (serverless cache) | L1 + L2 | Managed, no capacity planning |
| GPTCache | L2 | Open-source semantic cache, LangChain integration |
| vLLM APC / SGLang RadixAttention | L3 | Inference engine level, KV cache reuse |
| llm-d / NVIDIA Dynamo | Routing | Cache-aware request routing across replicas |
| LMCache | L3 distributed | Cross-node KV cache management |
