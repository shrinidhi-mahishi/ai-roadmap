# Module 01: Python & LLM Foundations

**Audience**: Principal/Director-level AI systems architects  
**Scope**: Async Python for LLM APIs, SDK internals, tokenization, embeddings, structured outputs, cost engineering, resilience patterns, enterprise security  
**Pricing data vintage**: September 2026

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
                              CONTROL PLANE
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                         LLM Gateway / Router                          │
 │  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  ┌───────────┐ │
 │  │ Intent      │  │ Circuit      │  │ Rate Limiter  │  │ RBAC /    │ │
 │  │ Classifier  │  │ Breakers     │  │ (Token Bucket)│  │ Policy    │ │
 │  │ (route to   │  │ (per-provider│  │ RPM + TPM per │  │ Enforcer  │ │
 │  │  model tier)│  │  per-model)  │  │ key/org       │  │           │ │
 │  └──────┬──────┘  └──────┬───────┘  └───────┬───────┘  └─────┬─────┘ │
 └─────────┼────────────────┼──────────────────┼─────────────────┼───────┘
           │                │                  │                 │
           ▼                ▼                  ▼                 ▼
                              DATA PLANE
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                                                                         │
 │  ┌──────────────────────────────────────────────────────────────────┐   │
 │  │              Async Python Runtime (uvloop)                       │   │
 │  │                                                                  │   │
 │  │  ┌─────────────────┐    ┌──────────────────────────────────┐    │   │
 │  │  │ asyncio.Queue   │───>│ N Worker Coroutines              │    │   │
 │  │  │ (backpressure)  │    │ (bounded by asyncio.Semaphore)   │    │   │
 │  │  └─────────────────┘    └───────────────┬──────────────────┘    │   │
 │  │                                         │                       │   │
 │  │                          ┌──────────────▼──────────────┐        │   │
 │  │                          │  httpx.AsyncClient          │        │   │
 │  │                          │  (long-lived, pooled)       │        │   │
 │  │                          │  max_connections=N          │        │   │
 │  │                          │  Timeout(connect=5,read=60) │        │   │
 │  │                          └──────────────┬──────────────┘        │   │
 │  │                                         │                       │   │
 │  └─────────────────────────────────────────┼───────────────────────┘   │
 │                                            │                           │
 │  ┌─────────────────┐    ┌─────────────────┐│   ┌────────────────────┐  │
 │  │ PII Redaction   │◄───│ Pre/Post Filter ││   │ Structured Output  │  │
 │  │ Pipeline        │    │ (regex + NER)   │├──>│ Validator          │  │
 │  │ (Presidio/spaCy)│    └─────────────────┘│   │ (Pydantic/schema)  │  │
 │  └─────────────────┘                       │   └────────────────────┘  │
 └────────────────────────────────────────────┼───────────────────────────┘
                                              │
              ┌───────────────────────────────┼───────────────────────┐
              │           PROVIDER TIER       │                       │
              │   ┌───────────┐  ┌────────────▼─┐  ┌──────────────┐  │
              │   │ Anthropic │  │ OpenAI       │  │ Self-hosted  │  │
              │   │ Claude API│  │ GPT/o-series │  │ (vLLM/Ollama)│  │
              │   └───────────┘  └──────────────┘  └──────────────┘  │
              └───────────────────────────────────────────────────────┘
                                              │
                              PERSISTENCE LAYER
 ┌────────────────────────────────────────────┼───────────────────────────┐
 │  ┌────────────────┐  ┌────────────────┐   │   ┌────────────────────┐  │
 │  │ Vector Store   │  │ Semantic Cache │   │   │ Prompt/Response    │  │
 │  │ (Pinecone/     │  │ (Redis + cosine│   │   │ Archive            │  │
 │  │  Qdrant/pgvec) │  │  similarity)   │   │   │ (S3 / GCS)        │  │
 │  └────────────────┘  └────────────────┘   │   └────────────────────┘  │
 └───────────────────────────────────────────┼───────────────────────────┘
                                              │
                          TELEMETRY / OBSERVABILITY
 ┌────────────────────────────────────────────┼───────────────────────────┐
 │  ┌────────────────┐  ┌────────────────┐   │   ┌────────────────────┐  │
 │  │ Structured     │  │ Metrics        │   │   │ Immutable Audit    │  │
 │  │ Logs           │  │ (Prometheus/   │◄──┘   │ Trail              │  │
 │  │ (correlation   │  │  Datadog)      │       │ (append-only,      │  │
 │  │  IDs, cost,    │  │ - token counts │       │  signed, 3yr       │  │
 │  │  latency)      │  │ - TTFT/TPS     │       │  retention)        │  │
 │  │                │  │ - cache hit %  │       │                    │  │
 │  │                │  │ - circuit state│       │                    │  │
 │  └────────────────┘  └────────────────┘       └────────────────────┘  │
 └───────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

A request enters the **LLM Gateway** where four operations occur in sequence:

1. **Intent classification** determines model tier. A lightweight classifier (or heuristic rule set) routes simple queries to Haiku/GPT-4o-mini, standard queries to Sonnet/GPT-4o, and complex reasoning to Opus/o3. This single decision drives 60-80% of cost optimization.

2. **Circuit breaker check**. Each provider-model pair maintains an independent circuit breaker. If the circuit is OPEN (recent failure rate exceeded threshold), the request immediately routes to the next healthy provider in the fallback chain rather than adding to a retry storm.

3. **Rate limiter**. A token-bucket algorithm enforces RPM and TPM limits per API key. Anthropic's server-side rate limiter uses token bucket with continuous refill; the client-side mirror prevents unnecessary 429s. The rate limiter reads `anthropic-ratelimit-tokens-remaining` headers from prior responses for proactive throttling.

4. **RBAC / policy enforcement**. Virtual API keys carry scoped permissions (model access, budget ceiling, data residency). The gateway holds real provider keys; services never see them.

The request then enters the **async Python data plane**. An `asyncio.Queue` provides backpressure; N worker coroutines (bounded by `asyncio.Semaphore`) draw from it. Each worker uses a shared, long-lived `httpx.AsyncClient` with tuned pool size. Before the request hits the wire, the **PII redaction pipeline** (regex for structured PII like SSNs, NER for names/addresses) masks sensitive data. The `uvloop` event loop (2-4x throughput over stock asyncio) drives the I/O.

The provider returns a streaming SSE response. The **structured output validator** (Pydantic model or JSON schema) parses and validates the output. If validation fails, a self-healing parser attempts recovery (quote normalization, boolean correction, brace completion) before escalating to retry.

The **persistence layer** stores embeddings in a vector store (versioned by model + config hash), caches semantically similar queries in Redis (cosine similarity threshold), and archives raw prompt/response pairs to object storage for compliance.

The **telemetry layer** captures every call: structured logs with correlation IDs, Prometheus/Datadog metrics (token counts, TTFT, TPS, cache hit ratio, circuit state transitions), and an immutable audit trail (append-only, cryptographically signed) retained for 3+ years per SOC 2 / HIPAA requirements.

---

## 2. Core Mechanics & Algorithms

### 2.1 Byte Pair Encoding (BPE) -- The Tokenization Algorithm

BPE underpins tokenization across GPT-4o, Claude, Gemini, and Llama model families.

**Algorithm (Philip Gage 1994, adapted by Sennrich et al. 2016 for NMT):**

```
Input:  Raw byte sequence of training corpus
Output: Vocabulary V of size |V| = target_vocab_size

1. Initialize V = {byte_0, byte_1, ..., byte_255}   -- 256 base tokens
2. REPEAT:
   a. Count frequency of every adjacent token pair (t_i, t_{i+1}) in corpus
   b. Select pair (a, b) with maximum frequency
   c. Create new token t_new = merge(a, b)
   d. Add t_new to V
   e. Replace all occurrences of (a, b) in corpus with t_new
3. UNTIL |V| == target_vocab_size
```

**Complexity**: O(N * |V|) where N = corpus size in bytes, |V| = target vocabulary size. Each merge pass is O(N) and there are |V| - 256 merge passes.

**Key invariants**:
- No OOV tokens -- any byte sequence is representable via the 256 base tokens.
- High-frequency substrings get single tokens; rare text decomposes to byte-level pieces.
- Token density: ~1.3 tokens/English word, ~1.5-3x for code, ~3-6x for Japanese/Arabic.

**Production tokenizer variants (September 2026)**:

| Provider | Tokenizer | Vocab Size | Library |
|----------|-----------|------------|---------|
| OpenAI (GPT-4, 4o) | cl100k_base | 100,256 | tiktoken |
| OpenAI (GPT-4o, o-series) | o200k_base | 200,000 | tiktoken |
| Anthropic (Claude <= 4.6) | Proprietary | ~100k | `client.messages.count_tokens()` |
| Anthropic (Claude >= 4.7) | Newer tokenizer | ~100k | Same API; ~30% more tokens for same text |
| Meta (Llama) | SentencePiece BPE | 128,000 | sentencepiece |

**Cost lever**: Switching from cl100k_base to o200k_base cuts per-character token cost for Portuguese and Indonesian by ~35% without changing the prompt. The tokenizer alone moves the cost needle.

**Emerging alternatives**:
- **Byte Latent Transformer (BLT)** -- Meta: Groups bytes into variable-length patches via an entropy model at training time. 8B-parameter BLT matches Llama-3 8B BPE on benchmarks with better typo/code robustness.
- **T-FREE** (Deiseroth et al., 2024): Tokenizer-free LLM using sparse hashed character-trigram embeddings. Embedding table compresses by ~85% vs. BPE vocab.

### 2.2 Token Bucket Rate Limiting

Anthropic's server-side rate limiter uses the token bucket algorithm. Understanding it is essential for client-side mirroring.

**State machine**:

```
State: {tokens: float, last_refill: timestamp, capacity: int, refill_rate: float}

On request(cost):
  1. refill = (now - last_refill) * refill_rate
  2. tokens = min(capacity, tokens + refill)
  3. last_refill = now
  4. IF tokens >= cost:
       tokens -= cost
       ALLOW
     ELSE:
       DENY (429 Too Many Requests)
       retry_after = (cost - tokens) / refill_rate
```

**Properties**: Permits burst up to `capacity`; sustains `refill_rate` tokens/second over time. Unlike fixed-window counters, no "burst at window boundary" problem.

**Why it matters for LLM APIs**: Rate limits are measured separately as RPM, ITPM, and OTPM. Each is its own bucket. A single request can be blocked by any of the three. With prompt caching, only uncached input tokens + cache creation tokens count toward ITPM. An 80% cache hit rate on a 2M ITPM limit effectively supports ~10M real input tokens/minute.

### 2.3 HNSW -- Approximate Nearest Neighbor Search

**Hierarchical Navigable Small World graphs** power sub-50ms similarity search over 10M+ vectors in production vector stores (Pinecone, Qdrant, Weaviate, pgvector with `ivfflat`/`hnsw` index).

**Algorithm (Malkov & Yashunin 2018)**:

```
Structure: L layers of navigable small-world graphs
           Layer 0: all vectors
           Layer l: random subset of Layer l-1 (exponential decay)

Insert(v):
  1. Assign v to layers 0..l where l ~ -ln(uniform()) * m_L
  2. From top layer, greedily descend to layer l+1 finding nearest neighbor
  3. At each layer l..0, connect v to M nearest neighbors
  4. Maintain degree constraint: prune edges if any node exceeds M_max

Search(q, k):
  1. Enter at top layer with a single entry point
  2. Greedy search at each layer: move to the neighbor closest to q
  3. At layer 0: beam search with ef_search candidates, return top-k
```

**Complexity**: O(log N) search time (N = number of vectors). O(N * M * log N) build time.

**Trade-offs**: Higher `ef_search` improves recall but increases latency. Higher `M` (edges per node) improves recall but increases memory and build time. Typical production settings achieve 95-99% recall at < 5ms for 10M vectors.

**Key invariant**: Cosine similarity and dot product are equivalent for L2-normalized embeddings (OpenAI embeddings are pre-normalized). Always verify normalization before selecting a distance metric.

### 2.4 Circuit Breaker State Machine

```
                      ┌────────────────────────────────────┐
                      │                                    │
                      ▼                                    │
                 ┌─────────┐     failure_rate >        ┌───┴────┐
          ──────>│ CLOSED  │──── threshold ──────────>│  OPEN  │
                 │ (normal)│     (e.g., 50% in 60s)    │(reject)│
                 └─────────┘                           └───┬────┘
                      ▲                                    │
                      │                              cooldown expires
                 probe succeeds                            │
                      │                                    ▼
                 ┌────┴──────┐                      ┌──────────┐
                 │           │◄─────────────────────│HALF-OPEN │
                 │           │                      │ (probe)  │
                 └───────────┘    probe fails ──────►└──────────┘
                                 (back to OPEN)
```

**States**:
- **CLOSED**: Track success/failure over a sliding window. All requests pass through.
- **OPEN**: All requests fail fast (no external call) or route to fallback. Timer starts.
- **HALF-OPEN**: After cooldown, send one probe request. Success -> CLOSED; failure -> OPEN with reset timer.

**Why this matters for LLM APIs**: Without circuit breakers, retry logic on 429s converts rate limits into cascading retry storms. At 200 concurrent users, a single high-traffic hour can generate exponential retry amplification where every client retries simultaneously.

### 2.5 Constrained Decoding for Structured Outputs

Both Anthropic and OpenAI guarantee schema compliance via constrained decoding.

**Mechanism**: The JSON schema is compiled into a context-free grammar (CFG). At each decoding step, the grammar constrains the set of valid next tokens to only those that keep the output on a path toward a schema-valid document. Invalid tokens receive -infinity logit bias.

**Key properties**:
- OpenAI: 100% schema compliance in evals on GPT-4o. Uses CFG constrained decoding.
- Anthropic: ~99.8%+ compliance (<0.2% failure across 300k calls on Sonnet 4.6). Uses grammar constrained decoding.
- First request with a new schema incurs compilation latency (Anthropic caches compiled grammars for 24 hours).
- Supported: basic types, enum/const, anyOf/allOf, $ref/$def, additionalProperties. Not supported: recursion, external $ref, numeric bounds, string-length constraints.

### 2.6 Matryoshka Representation Learning (Embedding Dimensionality)

OpenAI's text-embedding-3 models support adjustable dimensionality via a `dimensions` parameter. This is not truncation -- the model was retrained so that the first D dimensions of a higher-dimensional embedding preserve maximal semantic information.

**Property**: A 256-dim text-embedding-3-large embedding often outperforms a 1536-dim ada-002 embedding. This cuts vector storage by 6x and accelerates similarity search proportionally, with minimal quality loss.

**Production rule**: The embedding model has a bigger impact on retrieval quality than the vector database choice. A great embedding model with a basic store outperforms a mediocre model with the fanciest database.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Formulas

**Base cost per API call**:
```
cost = (input_tokens / 1M) * input_price + (output_tokens / 1M) * output_price
```

**With prompt caching (Anthropic)**:
```
cost = (uncached_tokens / 1M) * input_price * cache_write_multiplier
     + (cached_tokens   / 1M) * input_price * cache_read_multiplier
     + (output_tokens   / 1M) * output_price

Where:
  5-min TTL:  cache_write_multiplier = 1.25,  cache_read_multiplier = 0.10
  1-hour TTL: cache_write_multiplier = 2.00,  cache_read_multiplier = 0.10
  (Fable 5.1 cache read: 0.025x; Opus 5.5 cache read: 0.05x)
```

**Break-even**: 5-min cache pays off after 1 cache read. 1-hour cache after 2 reads.

**Reasoning token trap (OpenAI o3)**: o3 generates 8,000-20,000 internal reasoning tokens per query, all billed at the output rate ($8/MTok). A single o3 query can cost 10-15x more than GPT-4o for the same question. Hidden reasoning tokens are the largest cost surprise in production LLM systems.

**Tokenizer cost trap (Anthropic)**: Claude 4.7+ uses a newer tokenizer that produces ~30% more tokens for the same text. Upgrading from 4.6 to 4.7+ increases effective cost by ~30% even at identical per-token pricing.

### 3.2 Cost per 1K Calls -- Concrete Examples

**Assumptions for chatbot scenario**: 1,000 input tokens + 500 output tokens per call.

| Model | Input/MTok | Output/MTok | Cost per 1K Calls |
|-------|-----------|-------------|-------------------|
| GPT-4o mini | $0.15 | $0.60 | $0.00045 |
| GPT-5.6 Luna | $0.20 | $1.20 | $0.00080 |
| Claude Haiku 4.5 | $1.00 | $5.00 | $0.00350 |
| Claude Sonnet 5 | $2.00 | $10.00 | $0.00700 |
| GPT-4o | $2.50 | $10.00 | $0.00750 |
| Claude Opus 5 | $5.00 | $25.00 | $0.01750 |

**RAG scenario**: 4,000 input tokens + 1,000 output tokens per call.

| Model | Cost per 1K Calls |
|-------|-------------------|
| GPT-4o mini | $0.0012 |
| Claude Haiku 4.5 | $0.0090 |
| Claude Sonnet 5 | $0.0180 |
| GPT-4o | $0.0200 |

**Monthly cost projection**: 100K RAG calls/month on Sonnet 5 = $1.80/month without caching. With 80% prompt cache hit rate on 3K of the 4K input tokens: ~$0.72/month. Caching delivers ~60% cost reduction for conversational and RAG workloads.

**Batch API**: 50% discount on all Anthropic and OpenAI models for async processing (24h SLA). For non-time-sensitive workloads (embedding, evaluation, data extraction), always use batch.

### 3.3 Full Pricing Reference (September 2026)

**Anthropic Claude**:

| Model | Input/MTok | Output/MTok | Context |
|-------|-----------|-------------|---------|
| Claude Fable 5.1 | $10.00 | $50.00 | 1M |
| Claude Opus 5.5 | $4.00 | $20.00 | 1M |
| Claude Opus 5 / 4.8 / 4.7 / 4.6 | $5.00 | $25.00 | 1M |
| Claude Sonnet 5 | $2.00 | $10.00 | 1M |
| Claude Sonnet 4.6 | $3.00 | $15.00 | 1M |
| Claude Haiku 4.5 | $1.00 | $5.00 | 1M |

**OpenAI**:

| Model | Input/MTok | Output/MTok |
|-------|-----------|-------------|
| GPT-6 Astra | $10.00 | $50.00 |
| GPT-5.6 Sol | $4.00 (promo) | $20.00 (promo) |
| GPT-5.6 Terra | $2.00 | $12.00 |
| GPT-5.6 Luna | $0.20 | $1.20 |
| GPT-4o | $2.50 | $10.00 |
| GPT-4o mini | $0.15 | $0.60 |
| o3 | $2.00 | $8.00 |
| o3-pro | $20.00 | $80.00 |

**Embeddings**:

| Model | Price/MTok |
|-------|-----------|
| text-embedding-3-small | $0.02 |
| text-embedding-3-large | $0.13 |
| Google text-embedding-005 | $0.00625 |
| text-embedding-ada-002 (legacy) | $0.10 |

### 3.4 Latency SLA Targets

**Measured benchmarks (September 2026)**:

| Model | TTFT p50 | Tokens/sec | Notes |
|-------|----------|------------|-------|
| Groq (any model) | <200ms | 1,200-2,000+ | Hardware-optimized inference |
| Gemini 2.5 Flash | ~350ms | ~213 | Fastest major-provider model |
| Claude Haiku 4.5 | <600ms | ~180 | Best Anthropic latency |
| Claude Opus 4.7 | ~850ms | ~78 | |
| GPT-5.5 standard | ~1,100ms | ~92 | |
| GPT-4.1 Mini | ~2,400ms | -- | Surprising outlier |

**Reasoning mode latency (not suitable for interactive UX)**:

| Config | TTFT p50 |
|--------|----------|
| Claude Opus 4.7 extended thinking | ~28s |
| Gemini 3 Pro Deep Think | ~52s |
| GPT-5.5 Pro (high reasoning) | ~67s |

**UX thresholds (Jakob Nielsen's guidelines)**:

| Threshold | Perception | Target Tier |
|-----------|-----------|-------------|
| <200ms | Instant | Autocomplete, inline suggestions |
| <500ms | Responsive | Chat TTFT for premium UX |
| <1,000ms | Acceptable | Standard chat, tool calls |
| >1,000ms | Flow-breaking | Requires streaming + progress indicator |

**Recommended SLA targets for enterprise chat**:
- **p50**: <800ms TTFT, >100 TPS output
- **p95**: <2,000ms TTFT (use prompt caching to reduce cold-start)
- **p99**: <5,000ms TTFT (circuit breaker triggers fallback if exceeded)

**Mitigation strategies by tier**:
- **p50 improvement**: Prompt caching (eliminates KV cache recomputation), model tier routing (Haiku for simple queries), regional endpoint selection.
- **p95 improvement**: Connection pool pre-warming, semantic caching for repeated queries, pre-flight token count to reject over-long inputs early.
- **p99 improvement**: Circuit breaker with automatic fallback to faster model. Timeout at 10s with fallback chain. Never let a single slow call block the UX.

**Agentic pipeline compounding**: A 4-step agent pipeline with 600ms TTFT/step adds 2.4s in first-token latency alone. At 2,400ms/step (GPT-4.1 Mini), 9.6s before any useful output. TTFT is the primary model selection criterion for agent workloads.

### 3.5 Throughput & Capacity Planning

**Anthropic rate limits by tier**:

| Tier | RPM | ITPM | OTPM | Spend Cap |
|------|-----|------|------|-----------|
| Start | 1,000 | 2,000,000 | 400,000 | $500/mo |
| Build | 5,000 | 5,000,000 | 1,000,000 | $1,000/mo |
| Scale | 10,000 | 10,000,000 | 2,000,000 | $200,000/mo |
| Custom | Negotiated | Negotiated | Negotiated | None |

**Sizing formula**: To sustain R requests/minute with average response time T seconds:
```
concurrent_connections = R * T / 60
httpx.Limits(max_connections = concurrent_connections * 1.2)  # 20% headroom
asyncio.Semaphore(concurrent_connections)
```

**Worked example**: Saturate 30,000 RPM with 5s average response time:
```
concurrent = 30000 * 5 / 60 = 2,500
```
Default httpx pool of 100 connections is 25x too small. Set `max_connections=3000`.

**Back-pressure design**: When `asyncio.Queue` depth exceeds 2x worker count, reject new requests with HTTP 503 rather than queuing indefinitely. Unbounded queues convert latency problems into memory problems.

### 3.6 Availability & RPO/RTO

**Observed availability (2026)**: Anthropic had 114 incidents in a 90-day window in early 2026. OpenAI's 99.76% uptime = ~16 hours of downtime/year. On September 4, 2026, OpenAI, Anthropic, Google, and xAI all degraded simultaneously due to shared cloud infrastructure.

**Real multi-provider availability**: ~99.6-99.8% effective uptime, not theoretical 99.99%+. Correlated failures across providers are real.

**Recommended targets**:
- **Availability**: 99.9% (8.7h downtime/year) achievable with multi-provider fallback
- **RPO (Recovery Point Objective)**: 0 for stateless LLM calls; conversation state persisted per-turn
- **RTO (Recovery Time Objective)**: <30s for provider failover (circuit breaker cooldown)

---

## 4. Distributed Resilience & Security

### 4.1 Durable Execution

For multi-step LLM workflows (agent loops, chain-of-thought pipelines, document processing), individual API calls are idempotent but the workflow is not. A crash mid-pipeline loses all completed steps unless state is externalized.

**Temporal integration pattern**:

```
┌──────────────────────────────────────────────────────────────┐
│                    Temporal Workflow                          │
│                                                              │
│  @workflow.defn                                              │
│  class DocumentProcessingWorkflow:                           │
│                                                              │
│    @workflow.run                                             │
│    async def run(self, doc_batch):                           │
│      for doc in doc_batch:                                   │
│        # Each activity is independently retried & durable    │
│        chunks = await workflow.execute_activity(             │
│            chunk_document, doc,                              │
│            retry_policy=RetryPolicy(max_attempts=3))         │
│                                                              │
│        embeddings = await workflow.execute_activity(          │
│            embed_chunks, chunks,                             │
│            retry_policy=RetryPolicy(                         │
│                max_attempts=5,                               │
│                initial_interval=timedelta(seconds=1),        │
│                backoff_coefficient=2.0))                     │
│                                                              │
│        await workflow.execute_activity(                      │
│            upsert_vectors, embeddings,                       │
│            retry_policy=RetryPolicy(max_attempts=3))         │
│                                                              │
│  # Crash at any point -> Temporal replays from last          │
│  # completed activity. No duplicate API calls.               │
└──────────────────────────────────────────────────────────────┘
```

**Kafka integration for event-driven LLM pipelines**:

```
┌──────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────┐
│ Ingest   │───>│ Kafka Topic  │───>│ LLM Worker   │───>│ Kafka    │
│ Service  │    │ (requests)   │    │ Consumer Grp │    │ (results)│
└──────────┘    └──────────────┘    └──────┬───────┘    └──────────┘
                                          │
                                   ┌──────▼───────┐
                                   │ Dead Letter  │
                                   │ Queue (DLQ)  │
                                   └──────────────┘
```

**Design**: Consumer group provides at-least-once delivery. Idempotency key (hash of input + model + parameters) prevents duplicate processing on replay. Failed messages route to DLQ after max retries.

### 4.2 Failure Taxonomy

| Category | Error | Retryable? | Strategy |
|----------|-------|-----------|----------|
| **Transient** | 429 Rate Limit | Yes | Exponential backoff with jitter; read `retry-after` header |
| **Transient** | 500/502/503/504 | Yes | Exponential backoff, max 5 retries |
| **Transient** | Timeout (read) | Yes | Immediate retry up to 3x, then fail |
| **Transient** | Connection reset | Yes | Retry with new connection from pool |
| **Permanent** | 400 Bad Request | No | Log, alert, fail immediately |
| **Permanent** | 401 Unauthorized | No | Key rotation required; alert on-call |
| **Permanent** | 403 Forbidden | No | Permission issue; fail immediately |
| **Poison pill** | Infinite loop output | Detect | Monitor `finish_reason=="length"` rate; circuit-break at >5% |
| **Poison pill** | Schema-invalid output | Detect | Self-healing parser -> retry with explicit schema instruction -> circuit-break |
| **Silent** | Context truncation | Detect | Pre-flight token count; monitor `stop_reason=="max_tokens"` |
| **Silent** | Embedding drift | Detect | Canary queries with MRR tracking; alert on 7% week-over-week drop |
| **Silent** | Cache TTL regression | Detect | Monitor cache hit rate; alert on drop >20% |

**Idempotency keys**: Hash of `(input_content, model_id, temperature, seed, tool_definitions)`. Store completed request hashes in Redis with TTL matching your deduplication window. On replay, return cached result instead of re-calling the API.

### 4.3 Enterprise Security

#### Zero-Trust MCP Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         MCP Security Boundary                       │
│                                                                     │
│  ┌────────────┐     ┌───────────────┐     ┌──────────────────────┐ │
│  │ MCP Client │────>│ Auth Gateway  │────>│ Tool Registry        │ │
│  │ (Agent)    │     │ - mTLS        │     │ - Schema validation  │ │
│  │            │     │ - JWT verify  │     │ - Input sanitization │ │
│  │            │     │ - Rate limit  │     │ - Scope enforcement  │ │
│  └────────────┘     └───────┬───────┘     └──────────┬───────────┘ │
│                             │                        │             │
│                    ┌────────▼────────┐      ┌────────▼──────────┐  │
│                    │ Policy Engine   │      │ Audit Logger      │  │
│                    │ - OPA/Cedar     │      │ - Every tool call │  │
│                    │ - Per-tool RBAC │      │ - Input/output    │  │
│                    │ - Context-aware │      │ - Decision reason │  │
│                    └─────────────────┘      └───────────────────┘  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Principles**:
- Every tool call authenticated (mTLS + JWT), authorized (per-tool RBAC), and audited.
- Tools are never exposed directly to the LLM. The gateway validates inputs against the tool's JSON schema before execution.
- Least privilege: An agent analyzing financials gets read-only database access, no write tools, no shell access.

#### Tool-Level RBAC with Least-Privilege Policies

```
Role: "financial_analyst_agent"
Permissions:
  - tool: "query_database"
    actions: ["SELECT"]
    tables: ["transactions", "accounts"]
    row_filter: "org_id = {caller.org_id}"
    max_rows: 10000
  - tool: "generate_report"
    actions: ["create"]
    output_formats: ["pdf", "csv"]
  DENY:
  - tool: "execute_sql"          # No raw SQL
  - tool: "file_system_write"    # No disk writes
  - tool: "send_email"           # No external comms
```

#### PII Filtering Pipeline

```
┌───────────┐    ┌─────────────────┐    ┌──────────────┐    ┌──────────┐
│ Raw Input │───>│ Stage 1: Regex  │───>│ Stage 2: NER │───>│ Stage 3: │
│           │    │ - SSN patterns  │    │ - Presidio   │    │ Synthetic│
│           │    │ - CC numbers    │    │ - spaCy NER  │    │ Token    │
│           │    │ - MRN patterns  │    │ - Names,     │    │ Replace  │
│           │    │ - API keys      │    │   addresses, │    │          │
│           │    │ - Secrets       │    │   DOBs       │    │          │
│           │    └────────┬────────┘    └──────┬───────┘    └────┬─────┘
│           │             │                    │                 │
│           │             ▼                    ▼                 ▼
│           │    ┌──────────────────────────────────────────────────────┐
│           │    │ Redaction Map: {token_id: original_value, ...}      │
│           │    │ Stored in encrypted KV store, TTL = request lifetime│
│           │    └──────────────────────────────────────────────────────┘
│           │                                                    │
│           │    ┌─────────────────┐    ┌──────────────┐         │
│           │    │ LLM Response    │───>│ Stage 4:     │◄────────┘
│           │    │ (with synthetic │    │ Restore      │
│           │    │  tokens)        │    │ originals    │
│           │    └─────────────────┘    └──────┬───────┘
│           │                                  │
│           │                          ┌───────▼──────────┐
│           │                          │ Stage 5: Output  │
│           │                          │ PII Scan         │
│           │                          │ (catch leakage)  │
│           │                          └──────────────────┘
└───────────┘
```

**Audit trail requirements by compliance framework**:

| Framework | Key Requirements |
|-----------|-----------------|
| SOC 2 Type II | Immutable audit trails; key rotation evidence; access controls on logs |
| HIPAA | Cryptographic integrity verification; prompt lineage tracking |
| GDPR | Right-to-erasure for logged PII; data minimization |
| ISO 27001 | Key lifecycle documentation; incident response evidence |
| OWASP LLM Top 10 | Prompt injection detection logging; output validation records |

**Data retention policies**:
- Anthropic: API inputs/outputs not used for training. 30-day retention for trust & safety (reducible via zero-data-retention agreement).
- OpenAI: API data not used for training by default since March 2023. Enterprise agreements for zero retention.
- Logs: Minimum 3 years retention for compliance. Append-only, cryptographically signed, tamper-evident storage.

#### API Key Management

- **Scoping**: One key per service/application, scoped by function and environment (dev/staging/prod), not by department.
- **Rotation**: Automated 90-day rotation (SOC 2, ISO 27001, FedRAMP). Keys encrypted in transit (TLS 1.3) and at rest (AES-256 via KMS).
- **Virtual keys**: AI gateway (Portkey, TrueFoundry) holds real provider keys. Services authenticate with virtual keys carrying RBAC, budget limits, and residency policy. Revocable without touching the provider.
- **Offboarding**: Keys revoked within 1 hour of employee termination.
- **Storage**: HashiCorp Vault, AWS Secrets Manager, or GCP Secret Manager. Never in code, environment variables on dev machines, logs, or error messages.

---

## 5. Production Enterprise Code

### 5.1 Async LLM Client with Retry, Circuit Breaker, and Fallback

```python
"""
Production LLM client with:
- Exponential backoff with jitter
- Circuit breaker (closed -> open -> half-open)
- Fallback model chain
- Structured logging with correlation IDs
- Graceful degradation
- Async patterns for high-throughput

Requirements:
  pip install anthropic openai httpx tenacity structlog
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog
import anthropic
import openai

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Per-provider circuit breaker with sliding-window failure tracking."""

    name: str
    failure_threshold: float = 0.5       # 50% failure rate triggers OPEN
    window_seconds: float = 60.0         # sliding window size
    cooldown_seconds: float = 30.0       # time in OPEN before probing
    min_calls_in_window: int = 5         # minimum calls before evaluating

    state: CircuitState = CircuitState.CLOSED
    _opened_at: float = 0.0
    _call_log: list[tuple[float, bool]] = field(default_factory=list)

    def _prune_window(self) -> None:
        cutoff = time.monotonic() - self.window_seconds
        self._call_log = [(t, ok) for t, ok in self._call_log if t > cutoff]

    def record_success(self) -> None:
        self._call_log.append((time.monotonic(), True))
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            logger.info("circuit_breaker.closed", breaker=self.name)

    def record_failure(self) -> None:
        self._call_log.append((time.monotonic(), False))
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            logger.warning("circuit_breaker.reopened", breaker=self.name)
            return
        self._prune_window()
        if len(self._call_log) >= self.min_calls_in_window:
            failures = sum(1 for _, ok in self._call_log if not ok)
            rate = failures / len(self._call_log)
            if rate >= self.failure_threshold:
                self.state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                logger.warning(
                    "circuit_breaker.opened",
                    breaker=self.name,
                    failure_rate=round(rate, 3),
                )

    def allow_request(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                logger.info("circuit_breaker.half_open", breaker=self.name)
                return True  # allow probe
            return False
        # HALF_OPEN: allow one probe at a time
        return True


# ---------------------------------------------------------------------------
# Token Bucket Rate Limiter
# ---------------------------------------------------------------------------

@dataclass
class TokenBucketRateLimiter:
    """Client-side token bucket matching Anthropic's server-side algorithm."""

    capacity: float            # max burst size (e.g., RPM limit)
    refill_rate: float         # tokens per second (e.g., RPM / 60)
    tokens: float = 0.0
    _last_refill: float = field(default_factory=time.monotonic)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        self.tokens = self.capacity  # start full

    async def acquire(self, cost: float = 1.0) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self._last_refill = now

            if self.tokens >= cost:
                self.tokens -= cost
                return

            wait_time = (cost - self.tokens) / self.refill_rate
        await asyncio.sleep(wait_time)
        await self.acquire(cost)  # re-check after wait


# ---------------------------------------------------------------------------
# Model Configuration & Fallback Chain
# ---------------------------------------------------------------------------

@dataclass
class ModelConfig:
    provider: str          # "anthropic" or "openai"
    model: str             # e.g., "claude-sonnet-4-5-20250514"
    max_tokens: int = 4096
    temperature: float = 0.0


# Default fallback chain: same-provider cheaper model first, then cross-provider
DEFAULT_FALLBACK_CHAIN: list[ModelConfig] = [
    ModelConfig(provider="anthropic", model="claude-sonnet-4-5-20250514"),
    ModelConfig(provider="anthropic", model="claude-haiku-4-5-20250514"),
    ModelConfig(provider="openai",    model="gpt-4o"),
]


# ---------------------------------------------------------------------------
# Idempotency Key
# ---------------------------------------------------------------------------

def compute_idempotency_key(
    messages: list[dict[str, str]],
    model: str,
    temperature: float,
) -> str:
    payload = f"{model}:{temperature}:{str(messages)}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Enterprise LLM Client
# ---------------------------------------------------------------------------

class LLMClient:
    """
    Production-grade async LLM client.

    Features:
      - Per-provider circuit breakers
      - Exponential backoff with jitter on transient errors
      - Fallback chain across models/providers
      - Token bucket rate limiting
      - Structured logging with correlation IDs
      - Graceful degradation to deterministic fallback
    """

    def __init__(
        self,
        fallback_chain: list[ModelConfig] | None = None,
        max_retries: int = 3,
        rpm_limit: int = 1000,
    ) -> None:
        self.fallback_chain = fallback_chain or DEFAULT_FALLBACK_CHAIN
        self.max_retries = max_retries

        # One long-lived client per provider -- connection pooling
        self._anthropic = anthropic.AsyncAnthropic(max_retries=0)  # we handle retries
        self._openai = openai.AsyncOpenAI(max_retries=0)

        # Per-provider circuit breakers
        self._breakers: dict[str, CircuitBreaker] = {
            "anthropic": CircuitBreaker(name="anthropic"),
            "openai":    CircuitBreaker(name="openai"),
        }

        # Rate limiter (RPM-based)
        self._rate_limiter = TokenBucketRateLimiter(
            capacity=rpm_limit,
            refill_rate=rpm_limit / 60.0,
        )

    async def close(self) -> None:
        await self._anthropic.close()
        await self._openai.close()

    async def complete(
        self,
        messages: list[dict[str, str]],
        system: str = "",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Send a completion request through the fallback chain.

        Returns dict with keys: content, model, provider, tokens_in, tokens_out,
                                latency_ms, correlation_id, fallback_depth
        """
        cid = correlation_id or str(uuid.uuid4())[:8]
        log = logger.bind(correlation_id=cid)

        for depth, model_cfg in enumerate(self.fallback_chain):
            breaker = self._breakers[model_cfg.provider]

            if not breaker.allow_request():
                log.info(
                    "circuit_open_skipping",
                    provider=model_cfg.provider,
                    model=model_cfg.model,
                )
                continue

            try:
                result = await self._call_with_retry(
                    model_cfg, messages, system, cid
                )
                result["fallback_depth"] = depth
                breaker.record_success()
                return result

            except (anthropic.RateLimitError, openai.RateLimitError) as e:
                breaker.record_failure()
                log.warning(
                    "rate_limited",
                    provider=model_cfg.provider,
                    model=model_cfg.model,
                    error=str(e),
                )
                continue

            except (anthropic.APIStatusError, openai.APIStatusError) as e:
                status = getattr(e, "status_code", 0)
                if status in (400, 401, 403):
                    log.error(
                        "permanent_error",
                        provider=model_cfg.provider,
                        status=status,
                        error=str(e),
                    )
                    raise  # non-retryable, non-fallbackable
                breaker.record_failure()
                log.warning(
                    "transient_error",
                    provider=model_cfg.provider,
                    status=status,
                    error=str(e),
                )
                continue

            except Exception as e:
                breaker.record_failure()
                log.warning(
                    "unexpected_error",
                    provider=model_cfg.provider,
                    error=str(e),
                )
                continue

        # All providers exhausted -- deterministic fallback
        log.error("all_providers_exhausted", chain_length=len(self.fallback_chain))
        return {
            "content": "Service temporarily unavailable. Please try again shortly.",
            "model": "deterministic_fallback",
            "provider": "none",
            "tokens_in": 0,
            "tokens_out": 0,
            "latency_ms": 0,
            "correlation_id": cid,
            "fallback_depth": len(self.fallback_chain),
            "degraded": True,
        }

    async def _call_with_retry(
        self,
        model_cfg: ModelConfig,
        messages: list[dict[str, str]],
        system: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        """Call a single model with exponential backoff + jitter."""
        log = logger.bind(
            correlation_id=correlation_id,
            provider=model_cfg.provider,
            model=model_cfg.model,
        )

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                # Exponential backoff: 1s, 2s, 4s, ... with full jitter
                base_delay = min(2 ** (attempt - 1), 16)
                jitter = random.uniform(0, base_delay)
                delay = base_delay + jitter
                log.info("retrying", attempt=attempt, delay_s=round(delay, 2))
                await asyncio.sleep(delay)

            await self._rate_limiter.acquire()
            start = time.monotonic()

            try:
                if model_cfg.provider == "anthropic":
                    result = await self._call_anthropic(model_cfg, messages, system)
                else:
                    result = await self._call_openai(model_cfg, messages, system)

                latency_ms = round((time.monotonic() - start) * 1000, 1)
                result["latency_ms"] = latency_ms
                result["correlation_id"] = correlation_id

                log.info(
                    "llm_call_success",
                    tokens_in=result["tokens_in"],
                    tokens_out=result["tokens_out"],
                    latency_ms=latency_ms,
                    attempt=attempt,
                )
                return result

            except (anthropic.RateLimitError, openai.RateLimitError):
                raise  # bubble up for fallback handling

            except (
                anthropic.APIConnectionError,
                anthropic.APITimeoutError,
                openai.APIConnectionError,
                openai.APITimeoutError,
            ) as e:
                last_error = e
                log.warning("transient_error_retry", attempt=attempt, error=str(e))
                continue

            except (anthropic.APIStatusError, openai.APIStatusError) as e:
                status = getattr(e, "status_code", 0)
                if status in (400, 401, 403):
                    raise
                last_error = e
                log.warning(
                    "server_error_retry",
                    attempt=attempt,
                    status=status,
                    error=str(e),
                )
                continue

        raise last_error or RuntimeError("Retries exhausted")

    async def _call_anthropic(
        self,
        model_cfg: ModelConfig,
        messages: list[dict[str, str]],
        system: str,
    ) -> dict[str, Any]:
        response = await self._anthropic.messages.create(
            model=model_cfg.model,
            max_tokens=model_cfg.max_tokens,
            temperature=model_cfg.temperature,
            system=system or "You are a helpful assistant.",
            messages=messages,
        )
        return {
            "content": response.content[0].text,
            "model": response.model,
            "provider": "anthropic",
            "tokens_in": response.usage.input_tokens,
            "tokens_out": response.usage.output_tokens,
            "stop_reason": response.stop_reason,
        }

    async def _call_openai(
        self,
        model_cfg: ModelConfig,
        messages: list[dict[str, str]],
        system: str,
    ) -> dict[str, Any]:
        oai_messages = []
        if system:
            oai_messages.append({"role": "system", "content": system})
        oai_messages.extend(messages)

        response = await self._openai.chat.completions.create(
            model=model_cfg.model,
            max_tokens=model_cfg.max_tokens,
            temperature=model_cfg.temperature,
            messages=oai_messages,
        )
        choice = response.choices[0]
        return {
            "content": choice.message.content,
            "model": response.model,
            "provider": "openai",
            "tokens_in": response.usage.prompt_tokens,
            "tokens_out": response.usage.completion_tokens,
            "stop_reason": choice.finish_reason,
        }


# ---------------------------------------------------------------------------
# Usage Example
# ---------------------------------------------------------------------------

async def main() -> None:
    client = LLMClient(
        fallback_chain=[
            ModelConfig(provider="anthropic", model="claude-sonnet-4-5-20250514"),
            ModelConfig(provider="anthropic", model="claude-haiku-4-5-20250514"),
            ModelConfig(provider="openai",    model="gpt-4o"),
        ],
        max_retries=3,
        rpm_limit=1000,
    )

    try:
        result = await client.complete(
            messages=[{"role": "user", "content": "Explain HNSW in two sentences."}],
            system="You are a concise technical writer.",
        )
        print(f"[{result['provider']}/{result['model']}] "
              f"({result['latency_ms']}ms, depth={result['fallback_depth']})")
        print(result["content"])
    finally:
        await client.close()


if __name__ == "__main__":
    import uvloop
    uvloop.install()
    asyncio.run(main())
```

### 5.2 High-Throughput Batch Processor with Backpressure

```python
"""
Async batch processor for high-throughput LLM workloads.
Uses worker pool pattern with bounded queue for backpressure.

Requirements:
  pip install anthropic structlog uvloop
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import structlog
import anthropic

logger = structlog.get_logger()


@dataclass
class WorkItem:
    id: str
    messages: list[dict[str, str]]
    system: str = ""


@dataclass
class WorkResult:
    id: str
    content: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    error: str | None = None


class BatchLLMProcessor:
    """
    Process large batches of LLM calls with bounded concurrency,
    backpressure, and per-worker rate limiting.
    """

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20250514",
        max_workers: int = 50,
        max_queue_size: int = 200,
        max_tokens: int = 1024,
    ) -> None:
        self.model = model
        self.max_workers = max_workers
        self.max_tokens = max_tokens

        self._queue: asyncio.Queue[WorkItem | None] = asyncio.Queue(
            maxsize=max_queue_size
        )
        self._results: list[WorkResult] = []
        self._semaphore = asyncio.Semaphore(max_workers)
        self._client = anthropic.AsyncAnthropic(
            max_retries=3,
            timeout=anthropic.DEFAULT_TIMEOUT,
        )

    async def close(self) -> None:
        await self._client.close()

    async def process_batch(self, items: list[WorkItem]) -> list[WorkResult]:
        """Process a batch of work items with bounded parallelism."""
        self._results = []
        start = time.monotonic()

        # Start workers
        workers = [
            asyncio.create_task(self._worker(i))
            for i in range(self.max_workers)
        ]

        # Enqueue items (backpressure: blocks when queue is full)
        for item in items:
            await self._queue.put(item)

        # Send poison pills to stop workers
        for _ in range(self.max_workers):
            await self._queue.put(None)

        # Wait for all workers to finish
        await asyncio.gather(*workers)

        elapsed = time.monotonic() - start
        total_in = sum(r.tokens_in for r in self._results)
        total_out = sum(r.tokens_out for r in self._results)
        errors = sum(1 for r in self._results if r.error)

        logger.info(
            "batch_complete",
            items=len(items),
            errors=errors,
            total_tokens_in=total_in,
            total_tokens_out=total_out,
            elapsed_s=round(elapsed, 2),
            throughput_rps=round(len(items) / elapsed, 1),
        )

        return self._results

    async def _worker(self, worker_id: int) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                break

            async with self._semaphore:
                result = await self._process_item(item, worker_id)
                self._results.append(result)

    async def _process_item(self, item: WorkItem, worker_id: int) -> WorkResult:
        start = time.monotonic()
        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=item.system or "You are a helpful assistant.",
                messages=item.messages,
            )
            latency = round((time.monotonic() - start) * 1000, 1)
            return WorkResult(
                id=item.id,
                content=response.content[0].text,
                tokens_in=response.usage.input_tokens,
                tokens_out=response.usage.output_tokens,
                latency_ms=latency,
            )
        except Exception as e:
            latency = round((time.monotonic() - start) * 1000, 1)
            logger.error(
                "work_item_failed",
                item_id=item.id,
                worker_id=worker_id,
                error=str(e),
            )
            return WorkResult(
                id=item.id,
                content="",
                tokens_in=0,
                tokens_out=0,
                latency_ms=latency,
                error=str(e),
            )


async def main() -> None:
    processor = BatchLLMProcessor(
        model="claude-haiku-4-5-20250514",
        max_workers=20,
        max_queue_size=100,
    )

    items = [
        WorkItem(
            id=f"item_{i}",
            messages=[{"role": "user", "content": f"Summarize concept #{i} briefly."}],
        )
        for i in range(100)
    ]

    try:
        results = await processor.process_batch(items)
        succeeded = [r for r in results if r.error is None]
        print(f"Completed: {len(succeeded)}/{len(items)}")
    finally:
        await processor.close()


if __name__ == "__main__":
    import uvloop
    uvloop.install()
    asyncio.run(main())
```

### 5.3 Structured Output with Self-Healing Parser

```python
"""
Structured output extraction with validation and self-healing recovery.

Requirements:
  pip install anthropic pydantic structlog
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

import structlog
import anthropic
from pydantic import BaseModel, ValidationError

logger = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)


def heal_json(raw: str) -> str:
    """
    Attempt to fix common JSON malformations from LLM output.
    Applied only when standard parsing fails.
    """
    text = raw.strip()

    # Extract JSON from markdown code blocks
    md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if md_match:
        text = md_match.group(1).strip()

    # Python-style booleans and None
    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b", "null", text)

    # Single quotes to double quotes (naive but effective for simple cases)
    if text.startswith("{") and '"' not in text and "'" in text:
        text = text.replace("'", '"')

    # Close unclosed braces/brackets (LIFO)
    open_braces = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")
    text += "]" * max(0, open_brackets)
    text += "}" * max(0, open_braces)

    # Trailing comma before closing brace/bracket
    text = re.sub(r",\s*([}\]])", r"\1", text)

    return text


async def extract_structured(
    client: anthropic.AsyncAnthropic,
    model: str,
    messages: list[dict[str, str]],
    output_schema: type[T],
    system: str = "",
    max_retries: int = 2,
) -> T:
    """
    Extract structured data from an LLM call with multi-layer recovery:
    1. Constrained decoding (native structured output)
    2. Pydantic validation
    3. Self-healing JSON parser
    4. Retry with explicit schema instruction
    """
    log = logger.bind(schema=output_schema.__name__, model=model)

    # Build JSON schema from Pydantic model
    json_schema = output_schema.model_json_schema()

    for attempt in range(max_retries + 1):
        retry_system = system
        if attempt > 0:
            retry_system += (
                f"\n\nIMPORTANT: Return ONLY valid JSON matching this exact schema. "
                f"No markdown, no explanation.\nSchema: {json.dumps(json_schema)}"
            )

        response = await client.messages.create(
            model=model,
            max_tokens=4096,
            system=retry_system or "Extract the requested information as JSON.",
            messages=messages,
        )

        raw_text = response.content[0].text

        # Attempt 1: Direct parse
        try:
            data = json.loads(raw_text)
            result = output_schema.model_validate(data)
            log.info("structured_output.parsed", attempt=attempt, method="direct")
            return result
        except (json.JSONDecodeError, ValidationError):
            pass

        # Attempt 2: Self-healing parse
        try:
            healed = heal_json(raw_text)
            data = json.loads(healed)
            result = output_schema.model_validate(data)
            log.info("structured_output.parsed", attempt=attempt, method="healed")
            return result
        except (json.JSONDecodeError, ValidationError) as e:
            log.warning(
                "structured_output.parse_failed",
                attempt=attempt,
                error=str(e),
                raw_length=len(raw_text),
            )

    raise ValueError(
        f"Failed to extract {output_schema.__name__} after {max_retries + 1} attempts"
    )


# -- Example usage --

class CompanyInfo(BaseModel):
    name: str
    industry: str
    founded_year: int
    headquarters: str
    key_products: list[str]


async def main() -> None:
    client = anthropic.AsyncAnthropic()
    try:
        info = await extract_structured(
            client=client,
            model="claude-sonnet-4-5-20250514",
            messages=[{
                "role": "user",
                "content": "Extract company info for Anthropic.",
            }],
            output_schema=CompanyInfo,
        )
        print(info.model_dump_json(indent=2))
    finally:
        await client.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: High-Throughput Document Embedding Pipeline (100M Documents)

**Problem statement**: A financial services firm needs to embed 100 million regulatory documents (SEC filings, legal contracts, compliance memos) into a vector store for semantic search. The system must support incremental updates (10K new documents/day), handle embedding model version migrations without downtime, and meet a 4-hour SLA for full re-indexing during model upgrades.

**Proposed architecture**:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Document Ingestion                              │
│                                                                        │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────┐  │
│  │ S3 / GCS     │───>│ Change Data  │───>│ Content Hasher           │  │
│  │ Document     │    │ Capture      │    │ (skip unchanged docs)    │  │
│  │ Store        │    │ (event-      │    │ SHA-256 of normalized    │  │
│  │              │    │  driven)     │    │ content                  │  │
│  └──────────────┘    └──────────────┘    └────────────┬─────────────┘  │
└───────────────────────────────────────────────────────┼────────────────┘
                                                        │
┌───────────────────────────────────────────────────────▼────────────────┐
│                        Chunking & Embedding                            │
│                                                                        │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────┐  │
│  │ Semantic     │───>│ asyncio.Queue│───>│ 200 Worker Coroutines    │  │
│  │ Chunker     │    │ (maxsize=    │    │ asyncio.Semaphore(200)   │  │
│  │ (512 tok,   │    │  500)        │    │ httpx.AsyncClient        │  │
│  │  50 overlap)│    │              │    │ (max_connections=250)    │  │
│  └──────────────┘    └──────────────┘    └────────────┬─────────────┘  │
│                                                       │                │
│                                          ┌────────────▼─────────────┐  │
│                                          │ OpenAI text-embedding-   │  │
│                                          │ 3-small (batch API)      │  │
│                                          │ 256 dimensions           │  │
│                                          │ (Matryoshka)             │  │
│                                          └────────────┬─────────────┘  │
└───────────────────────────────────────────────────────┼────────────────┘
                                                        │
┌───────────────────────────────────────────────────────▼────────────────┐
│                        Vector Store                                    │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ Qdrant (HNSW index)                                              │  │
│  │                                                                  │  │
│  │ Metadata per vector:                                             │  │
│  │   model_name, model_version, embedding_dim, content_hash,       │  │
│  │   chunk_index, doc_id, created_at, preprocessing_config_hash    │  │
│  │                                                                  │  │
│  │ Two collections during migration:                                │  │
│  │   [v1_embeddings] ──read──> active queries                       │  │
│  │   [v2_embeddings] ──write─> new + re-indexed docs               │  │
│  │   Dual-read with model-tag filter during transition              │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────┐  │
│  │ DLQ (Redis)  │    │ Progress     │    │ Canary Query Monitor    │  │
│  │ Failed chunks│    │ Tracker      │    │ (50 queries, weekly MRR)│  │
│  └──────────────┘    └──────────────┘    └──────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

**Technology choices**: OpenAI text-embedding-3-small at 256 dimensions (Matryoshka) for cost efficiency. Qdrant for HNSW indexing with metadata filtering. Batch API for bulk processing (50% cost reduction). asyncio worker pool with `uvloop` for throughput.

**Cost estimation**:
- 100M docs x 500 tokens avg = 50B tokens
- text-embedding-3-small at $0.02/MTok: **$1,000 standard, $500 batch**
- Google text-embedding-005 alternative: **$312 standard** (3x cheaper)
- Storage: 100M vectors x 256 dims x 4 bytes = ~100 GB (vs. 600 GB at 1,536 dims)

**Trade-off evaluation matrix**:

| Dimension | A: OpenAI 3-small (256d, batch) | B: Google text-embedding-005 | C: BGE-M3 Self-hosted |
|-----------|-------------------------------|------------------------------|----------------------|
| **Cost (full index)** | $500 (batch) | $312 | ~$200 (GPU compute only) |
| **Cost (daily 10K docs)** | $0.10/day | $0.03/day | Fixed infra (~$50/mo) |
| **Latency (embed)** | ~200ms/batch (API) | ~150ms/batch (API) | ~50ms/batch (local GPU) |
| **Quality (MTEB)** | ~62% | ~60% | ~65% |
| **Ops complexity** | Low (managed API) | Low (managed API) | High (GPU infra, model updates) |
| **Data residency** | US (OpenAI default) | Configurable (GCP regions) | Full control |
| **Vendor lock-in** | Medium | Medium | None |

**Decision rationale**: Option A (OpenAI 3-small with batch API and 256-dim Matryoshka) wins for this use case. The $500 full-index cost is negligible for a financial services firm. The 6x storage reduction from 256 dims makes Qdrant cluster sizing manageable. API-based embedding eliminates GPU infrastructure ops. BGE-M3 (Option C) is superior on quality and data residency but introduces GPU infrastructure complexity that is not justified unless regulatory requirements mandate on-premise processing. The daily incremental cost ($0.10/day) is effectively zero.

**Drift mitigation plan**: 50 canary queries with known-good answers, run weekly. Track Mean Reciprocal Rank. Alert on 7% week-over-week drop. Quarterly full re-index on a parallel collection. Zero-downtime switchover after recall validation on the canary set passes.

---

### 6.2 Scenario: Multi-Model Routing Gateway for Enterprise AI Platform

**Problem statement**: A SaaS company serves 500 internal users across engineering, legal, and customer support teams. Monthly LLM spend has grown to $45K, with 80% going to Opus-tier models for all queries regardless of complexity. The team needs a routing layer that (a) cuts cost by 50%+ without degrading quality on complex tasks, (b) survives single-provider outages with <30s failover, and (c) enforces per-team budget caps and PII guardrails.

**Proposed architecture**:

```
┌────────────────────────────────────────────────────────────────────────┐
│                         API Gateway Layer                              │
│                                                                        │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────┐  │
│  │ Auth / RBAC  │    │ PII Filter   │    │ Budget Enforcer          │  │
│  │ (virtual keys│    │ (regex + NER │    │ (per-team monthly cap,   │  │
│  │  per team)   │    │  pre/post)   │    │  alert at 80%)           │  │
│  └──────┬───────┘    └──────┬───────┘    └────────────┬─────────────┘  │
└─────────┼──────────────────┼──────────────────────────┼────────────────┘
          │                  │                          │
          ▼                  ▼                          ▼
┌────────────────────────────────────────────────────────────────────────┐
│                         Routing Layer                                  │
│                                                                        │
│  ┌─────────────────────────────────────────────────────────────┐       │
│  │ Complexity Classifier (lightweight, rule-based + heuristic) │       │
│  │                                                             │       │
│  │ Signals:                                                    │       │
│  │  - Token count (short queries -> cheap model)               │       │
│  │  - Keyword triggers ("analyze", "compare" -> complex)       │       │
│  │  - Tool/structured-output required -> capable model         │       │
│  │  - Team override (legal always gets Opus for liability)     │       │
│  │  - Explicit user model preference (optional)                │       │
│  └──────────────────────────┬──────────────────────────────────┘       │
│                             │                                          │
│  ┌──────────────────────────▼──────────────────────────────────┐       │
│  │                    Semantic Cache (Redis)                    │       │
│  │  Key: embedding of query (cosine sim > 0.97 = hit)          │       │
│  │  Value: previous response + metadata                        │       │
│  │  TTL: 1 hour (invalidated on system prompt change)          │       │
│  └──────────────────────────┬──────────────────────────────────┘       │
│                             │ (cache miss)                             │
│  ┌──────────────────────────▼──────────────────────────────────┐       │
│  │              Provider Dispatch + Circuit Breakers            │       │
│  │                                                             │       │
│  │  SIMPLE (60% of traffic):                                   │       │
│  │    Haiku 4.5 -> GPT-4o mini -> deterministic fallback       │       │
│  │                                                             │       │
│  │  STANDARD (30% of traffic):                                 │       │
│  │    Sonnet 5 -> GPT-4o -> Haiku 4.5 (degraded)              │       │
│  │                                                             │       │
│  │  COMPLEX (10% of traffic):                                  │       │
│  │    Opus 5 -> Sonnet 5 (degraded) -> GPT-4o (degraded)      │       │
│  │                                                             │       │
│  │  Each arrow: circuit breaker (50% fail in 60s -> OPEN)      │       │
│  └─────────────────────────────────────────────────────────────┘       │
└────────────────────────────────────────────────────────────────────────┘
          │                  │                          │
          ▼                  ▼                          ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        Observability                                   │
│                                                                        │
│  Per-call: correlation_id, team, model, tokens_in/out, cost,           │
│            latency_ms, cache_hit, fallback_depth, pii_redactions       │
│  Dashboards: cost/team/day, cache hit rate, circuit state, p95 TTFT    │
│  Alerts: budget >80%, cache hit <40%, circuit open >5min               │
└────────────────────────────────────────────────────────────────────────┘
```

**Cost projection**:

| Scenario | Model Mix | Monthly Cost (500 users, ~300K calls/mo) |
|----------|-----------|------------------------------------------|
| Current (no routing) | 100% Opus 5 | ~$45,000 |
| With routing (60/30/10) | Haiku 4.5 / Sonnet 5 / Opus 5 | ~$8,500 |
| With routing + semantic cache (30% hit) | Same mix, 30% cached | ~$6,000 |
| With routing + cache + batch (off-peak) | Same + batch for async | ~$5,000 |

**Cost formula for the routed scenario**:
```
Assumptions: 300K calls/mo, avg 1,500 input + 500 output tokens per call

Simple  (60%):  180K * ((1500 * $1.00 + 500 *  $5.00) / 1M) = 180K * $0.0040 = $   720
Standard(30%):   90K * ((1500 * $2.00 + 500 * $10.00) / 1M) =  90K * $0.0080 = $   720
Complex (10%):   30K * ((1500 * $5.00 + 500 * $25.00) / 1M) =  30K * $0.0200 = $   600
                                                                 TOTAL         ~$2,040

Vs. all-Opus:   300K * $0.0200 = $6,000
Savings: ~66% from routing alone, before caching.
```

**Trade-off evaluation matrix**:

| Dimension | A: Complexity Router (rule-based) | B: Quality-Aware Router (ML classifier) | C: No Router (single model) |
|-----------|----------------------------------|----------------------------------------|---------------------------|
| **Cost savings** | 60-70% | 70-80% | 0% |
| **Latency overhead** | <1ms (heuristic rules) | 10-50ms (classifier inference) | 0ms |
| **Accuracy of routing** | ~85% (misroutes some edge cases) | ~93% | 100% (always uses best model) |
| **Ops complexity** | Low (static rules, easy to debug) | High (model training, eval pipeline) | None |
| **Time to implement** | 1-2 weeks | 4-8 weeks | 0 |
| **Quality risk** | Low (complex tasks always get Opus) | Very low (ML optimized) | None |
| **Scalability ceiling** | High | High | Bound by single provider limits |

**Decision rationale**: Option A (rule-based complexity router) is the correct starting point. The 60-70% cost reduction comes from the observation that most enterprise queries are simple lookups, summaries, or reformulations that Haiku handles well. The <1ms routing overhead is negligible. The 85% routing accuracy means ~15% of simple queries get sent to more expensive models, which wastes some cost but never degrades quality. The rule set is debuggable: when a user reports poor quality, you check the route and add a rule. Option B (ML classifier) is the right evolution after 3-6 months of production data accumulates for training, but it adds training pipeline complexity and a latency tax that is premature at launch. Option C (no routing) is only justified when monthly spend is under $1K or when the team lacks engineering capacity for even basic routing logic.

**Failover mechanics**: Each position in each fallback chain has an independent circuit breaker. A 429 from Anthropic triggers fallback to a cheaper Anthropic model first (same-provider = avoids cross-provider latency), then to OpenAI, then to a deterministic "service degraded" response. A 429 is per-key, not per-provider: distribute across multiple API keys before switching providers. Budget for fallback cost: during Anthropic outages, OpenAI costs may temporarily exceed normal operation.
