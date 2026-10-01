# Module 17: Design Personal AI Chat Assistant

### What Is This?

A personal AI chat assistant is not "wrap ChatGPT." It is a product that owns auth, conversation history, system prompts, cost levers, injection defenses, and output policy while composing provider streaming APIs, optional tools, and multi-device sync into a coherent user experience. Think of it like building a restaurant: the LLM is the chef, but you still need the dining room (UI/streaming), the reservation system (conversation state), the menu pricing (cost routing), the health inspector (safety filters), and the manager (orchestration) -- without those, the chef just cooks in an empty kitchen. This module covers the full production stack: four-layer architecture, seven-stage request lifecycle, SSE streaming protocol, multi-turn context management with compaction, three-tier model routing for cost optimization, prompt caching mechanics, circuit breakers with fallback chains, prompt injection defense in depth, multi-device sync, multi-region data residency, and multi-tenant platform isolation.

---

## 1. System Topology & Data Flow

### Five-Plane Architecture

A production AI chat assistant spans five cooperating planes. The **CDN edge / WAF** terminates TLS, mitigates DDoS, and -- critically -- must set `X-Accel-Buffering: no` so SSE tokens flow through without proxy buffering. The **control plane** handles auth, conversation CRUD, policy, model routing, rate limits, and compaction/memory write gates. The **data plane** assembles context, runs prefill+decode, and delivers SSE/WebSocket token streams. The **persistence plane** stores server-canonical transcripts, session caches, vector embeddings, and telemetry. The **tool proxy plane** executes MCP/function tools with schema validation, RBAC, vault-held credentials, and HITL gates for irreversible actions.

```
+----------------------------------------------------------------------------------+
|                             CDN EDGE / WAF                                        |
|                                                                                  |
|  Anycast DNS (Route 53 / Cloudflare) --> nearest PoP                             |
|  TLS 1.3 termination, DDoS mitigation, static asset cache                        |
|  X-Accel-Buffering: no  (REQUIRED for SSE passthrough -- buffering kills TTFT)   |
+--------------------------------------+-------------------------------------------+
                                       | HTTPS
+--------------------------------------v-------------------------------------------+
|                       API GATEWAY (Kong / Envoy)                                  |
|                                                                                  |
|  +------------------+  +-------------------+  +--------------------------------+ |
|  | Auth Engine       |  | Rate Limiter      |  | Request Router                 | |
|  |                   |  | (Redis-backed)    |  |                                | |
|  | JWT verification  |  |                   |  | /auth/*     --> Auth Service    | |
|  | Bearer token      |  | RPM (per user)    |  | /convo/*    --> Conversation    | |
|  | API key scoping   |  | TPM (per tier)    |  | /generate/* --> Orchestrator    | |
|  | RBAC: free/pro/   |  | Concurrent        |  | /files/*    --> Upload Service  | |
|  |   enterprise/     |  |   streams (per    |  |                                | |
|  |   admin           |  |   connection)     |  | W3C Trace Context propagation  | |
|  | OAuth 2.0 / OIDC  |  | In-memory         |  | Request schema validation      | |
|  | Session: 15m AT,  |  |   fallback when   |  | API versioning                 | |
|  |   7d RT           |  |   Redis down      |  |                                | |
|  +------------------+  +-------------------+  +--------------------------------+ |
+--------------------------------------+-------------------------------------------+
                                       | validated request + trace ID
+--------------------------------------v-------------------------------------------+
|                    CONTEXT ENGINEERING / ORCHESTRATION                             |
|                                                                                  |
|  +------------------+  +-------------------+  +--------------------------------+ |
|  | Context Assembler |  | Pre-Inference     |  | LLM Router                     | |
|  |                   |  | Moderation        |  | (Model Selection)              | |
|  | 1. Retrieve       |  |                   |  |                                | |
|  |    history from   |  | Distilled BERT    |  | Complexity --> model tier:      | |
|  |    Redis/Postgres |  | classifiers       |  |  Simple  --> Haiku/Luna        | |
|  | 2. Apply context  |  | (sub-ms latency)  |  |  Standard--> Sonnet/Sol        | |
|  |    compaction     |  |                   |  |  Complex --> Opus/Astra         | |
|  | 3. Inject system  |  | Pattern filters:  |  |                                | |
|  |    prompt +       |  |  role-confusion   |  | Fallback chains                | |
|  |    structured     |  |  zero-width chars |  | Cost optimization ~60%         | |
|  |    memory         |  |  tool-call        |  |                                | |
|  | 4. Append user    |  |  mimicry          |  |                                | |
|  |    message        |  |  encoding attacks |  |                                | |
|  +------------------+  +-------------------+  +--------------------------------+ |
+--------------------------------------+-------------------------------------------+
                                       | assembled prompt + model target
+--------------------------------------v-------------------------------------------+
|                    GENERATION ENGINE (GPU Inference)                               |
|                                                                                  |
|  +------------------+  +-------------------+  +--------------------------------+ |
|  | Tokenizer (BPE)  |  | Inference Engine  |  | Post-Inference Filters         | |
|  |                   |  | (vLLM / TGI)     |  |                                | |
|  | tiktoken /        |  |                   |  | Output classifiers             | |
|  | SentencePiece     |  | Two-phase:        |  | Content policy enforcement     | |
|  |                   |  |  Prefill: process |  | Structured output validation   | |
|  | 30K-100K vocab    |  |   all input       |  | Schema compliance check        | |
|  | ~1.3-1.5 tok/word |  |   tokens          |  |                                | |
|  | Code: denser      |  |  Decode: generate |  |                                | |
|  | Non-EN: higher    |  |   one token at a  |  |                                | |
|  |  tok/word         |  |   time (KV-cache) |  |                                | |
|  |                   |  |                   |  |                                | |
|  |                   |  | Continuous batch  |  |                                | |
|  |                   |  | INT8/FP8 quant    |  |                                | |
|  +------------------+  +-------------------+  +--------------------------------+ |
+--------------------------------------+-------------------------------------------+
                                       | token stream
+--------------------------------------v-------------------------------------------+
|                    SSE STREAMING + PERSISTENCE                                    |
|                                                                                  |
|  +------------------+  +-------------------+  +------------------+               |
|  | SSE Event Framing|  | Connection Mgmt   |  | Conversation     |               |
|  |                   |  |                   |  | Persistence      |               |
|  | Events:           |  | Heartbeat: 30s   |  |                  |               |
|  |  start: conv_id,  |  |   timeout, then  |  | PostgreSQL (SoR) |               |
|  |   model, tier     |  |   reconnect w/   |  | Redis (24h TTL)  |               |
|  |  token: delta     |  |   exp backoff    |  | Vector DB        |               |
|  |  meta: usage,$    |  |                   |  |  (Pinecone/      |               |
|  |  done: stop       |  | Client:           |  |   pgvector)      |               |
|  |   reason          |  |  AbortController  |  |                  |               |
|  |                   |  |  for user cancel  |  | Pub/sub fan-out  |               |
|  | Claude events:    |  |                   |  |  for multi-device|               |
|  |  message_start    |  | HTTP timeout:     |  |                  |               |
|  |  content_block_*  |  |  120-300s (NOT    |  | Idempotency:     |               |
|  |  message_delta    |  |  default 30-60s)  |  |  Redis lock on   |               |
|  |  message_stop     |  |                   |  |  (conv, msg_id)  |               |
|  +------------------+  +-------------------+  +------------------+               |
|                                                                                  |
|  Telemetry: Kafka --> Flink --> ClickHouse | Prometheus + Grafana                |
|  Dashboard: Row 1: error rate | P95 latency | cost/hr                            |
|             Row 2: eval scores | user feedback | retry rate                       |
+--------------------------------------+-------------------------------------------+
                                       |
+--------------------------------------v-------------------------------------------+
|                    TOOL PROXIES (MCP / Function Calling)                          |
|                                                                                  |
|  MCP tool servers (calendar, email, CRM, code exec)                              |
|  Schema validate (strict: true) --> RBAC --> HITL for irreversible               |
|  Vault-held secrets -- model sees only tool names + sanitized results            |
+----------------------------------------------------------------------------------+
```

### API Gateway vs. Load Balancer

A load balancer is "dumb" -- it distributes TCP/HTTP connections across servers using weighted round-robin or least-connections, with no awareness of request content. An API gateway is "smart" -- it reads URL paths, routes to specific services, handles authentication, rate limiting, request validation, API versioning, and observability injection. Production systems use both: the load balancer distributes connections across API gateway instances, which then route to specific backend services.

### Seven-Stage Request Lifecycle

| Stage | What Happens | Key Detail |
|-------|-------------|------------|
| **1. Edge** | User sends message from browser/app. CDN (Cloudflare/CloudFront) terminates TLS 1.3, routes via anycast to nearest PoP | `X-Accel-Buffering: no` required for SSE passthrough |
| **2. Gateway** | API gateway authenticates (JWT/OIDC), checks rate limits (RPM + TPM + concurrent streams), validates schema, routes by URL path | W3C Trace Context trace ID injected for e2e observability |
| **3. Persist user turn** | User message appended to server-canonical conversation store BEFORE inference | If the stream crashes, the user's question is never lost |
| **4. Context assembly** | Orchestration service builds prompt: system prompt + tool schemas (stable prefix for cache) + last-K turns or compaction item + structured memory/RAG snippets | Token budget gates decide: full history vs compact vs retrieve |
| **5. Input guard + model call** | Pre-inference moderation (distilled BERT classifiers, sub-ms latency), then model router selects tier. Provider called with `stream=true` | Moderation runs OUTSIDE agent orchestration -- injection cannot disable it |
| **6. Stream tokens** | Stream gateway flushes each text delta to the originating SSE connection AND publishes to conversation channel for multi-device sync | **Do not buffer the proxy** -- buffering destroys TTFT |
| **7. Persist + telemetry** | On `response.completed` / `message_stop`: write assistant turn, emit telemetry (TTFT, tokens in/out, cache hit, cost, correlation ID). Reconnecting devices catch up via sequence number | Partial streams marked `interrupted`, not silently dropped |

**Message style**: synchronous turn loop with durable appends. Live tokens are ephemeral fan-out over a connection-scoped stream plus a channel. History is always reloaded from the server.

---

## 2. Core Mechanics & Algorithms

### 2.1 Inference Fundamentals

Inference is next-token prediction. **Prefill** processes the full prompt in parallel (TTFT scales with prompt length). **Decode** emits tokens autoregressively using a KV-cache of prior keys/values. Longer history raises both latency and cost: prefill work and billed input tokens grow with every turn.

Models operate on **subword tokens** (commonly BPE), not words. Rough English: **10 words ~ 13-15 tokens**; code and non-English languages are denser. BPE vocabulary size ranges 30K-100K. Character-level tasks (e.g., counting letters in "strawberry") fail when letters split across token boundaries. Pricing, context limits, and TTFT all scale with token count, not word count.

Post-training (SFT then RLHF/preference optimization) is what makes a base LM follow system prompts and refuse harmful asks. Scaling-law tiering (frontier vs mini/nano) is the rationale for model routing.

### 2.2 Conversation State Management

**LLMs are stateless** -- the model holds nothing between requests. The system must reconstruct the full conversational context on every single request. This is the fundamental architectural constraint that drives most design decisions.

**State reconstruction per request:**

```
+-----------------------------------------------------------------------+
|                   PER-REQUEST STATE RECONSTRUCTION                      |
|                                                                        |
|  Step 1: Retrieve history                                              |
|  +--------------+    miss     +---------------+                        |
|  | Redis Cache   |----------->| PostgreSQL /  |                        |
|  | (24h TTL)     |           | DynamoDB       |                        |
|  | Sub-ms reads  |<----------|  (durable)     |                        |
|  +------+-------+   hydrate  +---------------+                        |
|         |                                                              |
|  Step 2: Context window management                                     |
|  +------v---------------------------------------------------------+    |
|  | Last 6 exchanges  --> verbatim                                  |    |
|  | Older exchanges   --> summarized (triggered at 70-80% capacity) |    |
|  | System prompt     --> re-injected every 20-30 exchanges         |    |
|  | Structured facts  --> always included (survives summarization)  |    |
|  +------+---------------------------------------------------------+    |
|         |                                                              |
|  Step 3: Assemble final prompt                                         |
|  +------v---------------------------------------------------------+    |
|  | [System prompt]                                                  |    |
|  | [Structured memory: user preferences, confirmed decisions]       |    |
|  | [Summary of older conversation segments]                         |    |
|  | [Last 6 exchanges verbatim]                                      |    |
|  | [New user message]                                                |    |
|  +------------------------------------------------------------------+  |
+------------------------------------------------------------------------+
```

### 2.3 Conversation-State Patterns (Provider-Side)

Three patterns for managing conversation state with OpenAI APIs:

| Pattern | Semantics | Retention / Billing |
|---------|-----------|---------------------|
| **Manual transcript** | App persists turns; resends full `messages` / `input` each call | App owns durability. Chat Completions default. |
| **`previous_response_id`** | Pass only new user turn; provider threads prior response context | Responses retained **30 days** by default (`store: false` disables). **Prior input tokens in the chain are still billed.** |
| **Conversations API** | Durable `conversation_id` across sessions/devices/jobs | Conversation items **not** subject to 30-day response TTL. Explicitly positioned for cross-device continuity. |

**Server-side compaction**: `context_management` / `compact_threshold` (example **~200,000 tokens**) emits an opaque compaction item; standalone `POST /responses/compact` supports ZDR-friendly `store=false` flows. Compaction items are encrypted carriers of prior state -- pass through, do not invent human-readable summaries as a substitute.

**Realtime voice sessions**: 32,768-token window, max 4,096 output = ~28,672 max input before truncation. Session instructions+tools capped at 16,384 tokens. Sessions up to 60 minutes. `retention_ratio` (e.g., 0.8) amortizes cache busts from truncation.

### 2.4 Streaming Event Models

All major AI chat products use **Server-Sent Events (SSE)**, not WebSockets. Chat is unidirectional streaming (server to client) after the request -- SSE is purpose-built for this.

| Dimension | SSE | WebSocket |
|-----------|-----|-----------|
| Direction | Server to client (unidirectional) | Bidirectional |
| Protocol | HTTP/1.1 compatible | Upgrade from HTTP |
| Proxy/CDN | Works through standard proxies | Requires proxy configuration |
| Auto-reconnect | Built into browser EventSource API | Must implement manually |
| Complexity | Simple | More complex |
| Use case fit | Token streaming (industry consensus) | Real-time collaboration |

**OpenAI Responses API (recommended for new builds)**: `response.created` --> `response.output_text.delta` (repeated) --> `response.completed` / `error`. Tool args stream via `response.function_call_arguments.delta`. WebSocket mode exists for persistent transport with incremental inputs via `previous_response_id`.

**OpenAI Chat Completions (still supported)**: `data:` JSON with `choices[].delta.content`, terminated by `data: [DONE]`. Requires the app to resend the full `messages` array.

**Anthropic Messages API**: `message_start` --> (`content_block_start` --> `content_block_delta`* --> `content_block_stop`)* --> `message_delta` --> `message_stop`. **Measure text TTFT on first `content_block_delta` with `delta.type == text_delta`, NOT on `message_start`.** Tool-use blocks stream `input_json_delta` / `partial_json`; fine-grained streaming via per-tool `eager_input_streaming: true` disables server-side JSON buffering so clients must accumulate and repair.

**Moderation vs. streaming**: OpenAI explicitly warns that streaming makes content moderation harder. Partial completions are harder to evaluate; moderation scores arrive after full output, not with partial deltas. For high-risk sessions, delay irreversible tool side effects until full-output moderation check.

### 2.5 Turn State Machine

```
      +----------+   accept +   +------------+   assemble   +------------+
      |   IDLE   |--persist--->| USER_TURN  |------------->|  PREFILL   |
      +----------+  msg_id     +------------+              +-----+------+
            ^                                                    | first text delta
            |                                                    v
            |               +------------+   completed    +------------+
            |               | PERSISTED  |<---------------|  DECODING  |--tool?--> TOOL_LOOP
            |               +------+-----+                +-----+------+
            |                      |                            | disconnect / error
            |                      v                            v
            |               +------------+                +------------+
            +---------------|  COMPLETE  |                | INTERRUPTED|
                            +------------+                | (partial)  |
                                                          +------------+
```

**Invariants:**
1. Client `message_id` is idempotent: duplicate submit returns the same turn, does not double-bill or double-append.
2. User turn is durable BEFORE the first provider byte.
3. Assistant turn is either `completed`, `interrupted` (partial persisted), or absent -- never silently dropped.
4. Multi-device truth is the conversation store + sequence, not the SSE socket.

### 2.6 Prompt-Cache Algorithm

Stable prefix (system + tools + schemas) of length P, variable suffix (history + user) of length S:

- **Uncached prefill**: ~O(P+S) attention work and full input billing
- **Cache hit on exact prefix match**: billed at cached-input rate on P; TPM still counts full input
- **Prefix must match exactly** -- tools, structured-output schemas, reasoning effort, verbosity, and compaction all bust the cache
- **Cookbook heuristic**: ~15 RPM per `(prefix + prompt_cache_key)` before load-balancing causes one-time misses

**Anthropic cache economics**: default 5-minute ephemeral TTL (refreshed on hit); optional 1-hour TTL. Write multipliers: 5m write = 1.25x base input, 1h write = 2x. Cache read = ~0.1x base (some models 0.05x / 0.025x). Automatic caching moves the breakpoint forward each turn.

**OpenAI cache economics**: enabled by default for supported models; discounts up to 95% depending on model. For GPT-5.6+: cache writes 1.25x uncached input, reads 0.1x (0.05x on GPT-6.1 Sol). Minimum cacheable prefix length: 1,024 tokens (GPT-5.6+).

**Critical invariant**: **Cache hits do NOT reduce TPM rate-limit accounting.** TPM is enforced before cache lookup. Plan capacity on raw input+output tokens, not on post-cache billable dollars.

### 2.7 Multi-Modal Input/Output

Modern assistants handle text, images, code, and files. The orchestration layer detects input modality and routes to the appropriate processing pipeline. Vision tokens are typically more expensive than text tokens and require separate budget tracking. File uploads go through object storage (S3/GCS) via pre-signed URLs. The frontend renders structured outputs: markdown, code blocks with syntax highlighting, LaTeX, and charts.

### 2.8 Context Window Overflow -- The Most Common Silent Failure

Even 200K-400K token windows are insufficient for long agent sessions. Costs grow quadratically with conversation length. Model attention quality degrades in the middle of very long contexts ("lost in the middle" phenomenon). KV-cache memory grows linearly, eventually exhausting GPU VRAM.

**Production mitigation strategies (ordered by cost, cheapest first):**

| # | Strategy | Latency | Quality | Cost | When to Use |
|---|----------|---------|---------|------|-------------|
| 1 | **Sliding window** (last 10-15 exchanges) | Lowest | Lossy -- old context lost | Lowest | Short threads, debugging |
| 2 | **Token-level compression** (remove filler words, 40-60% reduction; tools: LLMLingua) | Low | Good | Low | Long transcripts with verbosity |
| 3 | **Conversation summarization** (trigger at 70-80% capacity) | Medium | Good (some info loss) | Medium | Approaching context limit |
| 4 | **Structured memory extraction** (pull deterministic facts: preferences, decisions) | Low | Lossless (facts survive) | Low | Cross-session preferences |
| 5 | **RAG for conversation memory** (embed chunks, retrieve by similarity) | Medium | High (relevance-based) | Higher | Very long histories |
| 6 | **Memory tiering (MemGPT-style)** -- working + short-term + long-term | Medium | Highest | Highest | OS-style memory hierarchy |
| 7 | **Prompt caching at infrastructure level** (KV prefix reuse) | Lowest | No loss | Lowest | Stable system prompt prefix |

**Best practice hybrid**: Keep last 6 exchanges verbatim + summarize everything older + inject summary into system prompt + re-inject system prompt every 20-30 exchanges. Users do not notice the summarization; continuity is preserved.

**Decision rule**: Keep last-K literal turns for local coherence; compact or summarize when approaching the window; retrieve durable facts from a memory/RAG store instead of infinite history. Realtime voice windows (32,768 total, max 4,096 out) make this trade-off acute.

**Claude Code's 5-layer context reduction pipeline** (executed before every model call, cheapest first):
1. **Budget reduction** -- targets individual tool outputs that overflow size limits
2. **Snip** -- handles temporal depth
3. **Microcompact** -- reacts to cache overhead
4. **Context collapse** -- manages very long histories
5. **Auto-compact** -- semantic compression as last resort

---

## 3. Token Economics & NFR Analysis

### 3.1 The Context Tax

Each turn in a multi-turn conversation re-sends the full conversation history as input. Costs grow **quadratically**, not linearly.

**Concrete example**: Each response ~500 tokens, user message ~100 tokens:

| Turn | Input Tokens | Output Tokens |
|------|-------------|---------------|
| 1 | ~100 | 500 |
| 2 | ~700 | 500 |
| 3 | ~1,300 | 500 |
| 4 | ~1,900 | 500 |
| 5 | ~2,600 | 500 |
| **Total** | **~6,500** | **2,500** |

Total input across 5 turns is 6,500, not 500. The accumulated history re-send is the "context tax."

**Hidden costs:**
- **Reasoning models** (o1, o3, DeepThink): invisible "thinking" tokens billed as output. A 500-token visible response can consume 2,000+ tokens total.
- **Long-context surcharge**: requests exceeding ~272K tokens bill input at 2x and output at 1.5x (OpenAI, Sep 2026).

### 3.2 API Pricing (Sep 2026, per 1M tokens)

| Provider / Model | Input | Cached Input | Output |
|------------------|-------|-------------|--------|
| GPT-6 Astra (frontier) | $10.00 | $1.00 | $50.00 |
| GPT-5.6 Sol (balanced) | $4.00 | $0.40 | $20.00 |
| GPT-5.6 Luna (economy) | $0.20 | $0.02 | $1.20 |
| GPT-4o | $2.50 | $1.25 | $10.00 |
| GPT-4.1 | $2.00 | $0.50 | $8.00 |
| Claude Sonnet 4.5 (est.) | ~$3.00 | ~$0.30 | ~$15.00 |
| Claude Haiku 4.5 (est.) | ~$0.25 | ~$0.025 | ~$1.25 |

Cached input is typically 10% of input price. Batch API typically halves costs with 24-hour SLA.

### 3.3 Per-Turn Cost Arithmetic

**Assumptions**: Support-style turn with 5,000 input tokens (system + tools + history) and 1,400 output tokens.

**GPT-4o (worked example):**

```
C_uncached  = 5000 x $2.50 x 10^-6  +  1400 x $10.00 x 10^-6
            = $0.0125 + $0.0140
            = $0.0265 per turn

C_cached    = 5000 x $1.25 x 10^-6  +  1400 x $10.00 x 10^-6
            = $0.00625 + $0.0140
            = $0.02025 per turn
```

| Scenario | Per Turn | Per 1K Turns | Per Month (15K turns) |
|----------|----------|-------------|----------------------|
| GPT-4o uncached | $0.0265 | **$26.50** | ~$398 |
| GPT-4o 100% cached input | $0.02025 | **$20.25** | ~$304 |
| GPT-4.1 uncached | $0.0212 | $21.20 | ~$318 |
| GPT-4.1 100% cached input | $0.0137 | $13.70 | ~$206 |
| Claude Sonnet 4.5 uncached | $0.0360 | $36.00 | ~$540 |
| Claude Sonnet 4.5 cached | $0.0225 | $22.50 | ~$338 |

At 15,000 turns/month (500 conversations/day x ~1 turn average): GPT-4o uncached ~ $398/month. Strong cache hits cut the input line ~50%.

### 3.4 Consolidated Cost per 1K Conversations

```
Cost_per_1K = 1000 x avg_turns x avg_tokens_per_turn x $/token x (1 - cache_hit_rate)

Worked example (Sonnet 4.5, Sep 2026):

  avg_turns            = 10
  avg_tokens_per_turn  = 2,000 (input + output, includes context tax)
  blended $/token      = $5.00/1M (weighted: ~60% input @ $3/M,
                                    ~40% output @ $15/M, adjusted
                                    for three-tier routing mix)
  cache_hit_rate       = 40%

  Cost_per_1K = 1000 x 10 x 2000 x ($5.00/1,000,000) x (1 - 0.40)
              = $60.00 per 1K conversations
              = $0.06 per conversation
              = $0.006 per turn

  At 100K DAU with 3 conversations/day:  ~$18K/month before routing savings.
  With three-tier routing (60% to economy): ~$7.2K/month.
```

### 3.5 Cost Optimization -- Seven Levers (ordered by impact)

| # | Lever | Savings | Mechanism |
|---|-------|---------|-----------|
| 1 | **Prompt caching** | 50-90% on input | Reuse cached prefill; cached input at ~10% of full price. Single biggest savings lever. |
| 2 | **Model routing** | ~60% blended | Route FAQ/simple to Luna/Haiku, complex to frontier. |
| 3 | **Response caching** | 100% on cache hit | Embedding-based keys in Redis for semantically similar prompts. |
| 4 | **Context compaction** | 40-60% tokens | Summarize older turns to reduce input count. |
| 5 | **Token budgets** | Prevents runaway | Per-request and per-conversation limits. |
| 6 | **Batch API** | 50% | Non-interactive workloads (summarization, classification) with 24h SLA. |
| 7 | **Dynamic scaling** | Variable | Scale GPU nodes to zero during 2-6 AM local. |

Teams following these practices report 30-70% savings vs. naive usage.

### 3.6 Estimated Monthly Production Costs

| Usage Tier | Monthly Cost |
|-----------|-------------|
| Light (personal projects) | $5 - $40 |
| Small apps | $40 - $200 |
| Production apps | $200 - $1,500 |
| Enterprise | $1,500+ |
| OpenAI's own inference | ~$700K+ / day |

**Capacity planning sketch** (2,000 conversations/day, 6 turns each, 4K input + 800 output tokens/turn): On GPT-4.1 list prices ~ $172.8/day uncached (~$5.2K/month). At 70% input cache hit: ~$122/day (~$3.7K/month). Self-hosting becomes relevant when steady throughput + data-boundary needs exceed API economics.

### 3.7 Latency Targets for Interactive Chat

Measure at percentiles, never averages.

| Metric | Definition | Target |
|--------|-----------|--------|
| **TTFT** (Time to First Token) | Time from request to first text token received by client | p50: < 400ms, p95: < 500ms, p99: < 1s |
| **ITL / TPOT** (Inter-Token Latency) | Average time between tokens after the first | < 30ms for smooth reading |
| **TPS** (Tokens Per Second) | Output generation rate | > 30 tok/s for interactive chat |
| **E2E Latency** | TTFT + (output_tokens x TPOT) | Derived from above |

**Key tension**: optimizing for throughput (batching more requests) hurts TTFT (increases queue wait time). Production SLAs must specify both: e.g., "TTFT p99 < 1s AND throughput > 2000 tok/s."

**TTFT scales with prompt length**: a 500-token prompt and a 100K-token document produce vastly different TTFT on the same model because the prefill phase must process all input tokens before generating the first output token.

**Streaming arithmetic (why stream)**: without streaming, user waits full TTFT+decode (~35s p50 for 1,400 tokens at ~40 tok/s). With streaming, time-to-first-paint = TTFT (~400ms p50). Perceived "instant" band often cited ~<500ms TTFT.

**Benchmark reference** (200 concurrent users): Avg TTFT 119.96ms, P95 TTFT 135.8ms, system-wide output 146.04 tokens/sec, zero unhandled 5xx errors.

**p99 TTFT** is dominated by queueing, routing, KV allocation, and proxy buffering -- not raw decode FLOPs.

### 3.8 Throughput & Rate Limits

OpenAI rate limits are multi-dimensional (RPM, TPM, RPD, ...); whichever trips first returns `429` with `Retry-After` / `x-ratelimit-*` headers.

| Tier | RPM | TPM |
|------|-----|-----|
| Build | 5,000 | 1,000,000 |
| Launch | 10,000 | 4,000,000 |
| Grow | 15,000 | 40,000,000 |

**Ramp rule**: near ~1M input TPM, increase no more than 50% every 15 minutes.

**Back-pressure design:**
1. Token-bucket / sliding window per `(user_id, tier)` at the gateway
2. Honor `Retry-After`; exponential backoff + jitter toward the provider
3. When RPM-bound but TPM-rich, batch; when TPM-bound, shed load or route to mini
4. Fallback chain: frontier --> mid --> mini --> template refusal

### 3.9 Capacity Planning Reference Model

| Metric | Value |
|--------|-------|
| Registered users | 1,000,000 |
| DAU | 100,000 (10%) |
| Throughput target | 18,000 TPS |
| Peak request rate | 150 req/sec |
| GPU nodes required | ~30x H100 |
| Single H100 cost | ~$3/hour |
| 70B model FP16 VRAM | ~140 GB (2x H100 min) |
| 405B model FP16 VRAM | ~810 GB (multi-node) |
| INT4 quantization savings | 4x memory reduction, 1-4% perplexity increase |

### 3.10 NFR Trade-Off Table

| NFR | Target | Notes |
|-----|--------|-------|
| **Availability** | 99.9% monthly (product target) | Degrade with fallback models rather than hard-fail. Provider 503/429 are expected. |
| **RPO (conversation history)** | 0 for acknowledged turns | Sync append before inference. Redis is hot cache, NOT source of record. |
| **RTO (history restore)** | < 60s from DB failover; < 2s reconnect | Multi-device resume = history catch-up + channel subscribe |
| **Compliance** | SSO/OIDC, per-user isolation, deletable stores (GDPR), BAA-capable providers (HIPAA) | Your build does NOT inherit ChatGPT Enterprise attestations |

---

## 4. Distributed Resilience & Security

### 4.1 Durable Execution -- Server-Canonical History

| Store | Role | Failure/Retention Notes |
|-------|------|------------------------|
| **App DB (PostgreSQL)** | Canonical transcript, branches, `active_leaf_id` | Required for multi-device and compliance deletes. Tree > flat list for regenerate/edit. |
| **OpenAI Conversations** | Optional provider-durable items (no 30-day TTL on conversation items) | Cross-device continuity; couples retention/compliance to vendor |
| **`previous_response_id`** | Soft chain; 30-day response retention | Broken ID --> resend full context |
| **Redis** | Last-N + summary for fast resume; rate-limit counters (24h TTL) | NOT source of truth |
| **Pub/sub channel** | Live token fan-out + history replay on reconnect | Connection-scoped |

**Write path**: authenticate --> rate-limit --> **append user message (durable)** --> assemble context --> stream model --> persist assistant turn --> fan-out. Prefer append-only message logs for auditability.

**Plain SSE cannot resume mid-stream across devices**: a new device has no stream cursor. Production pattern = server-canonical conversation + channel/pub-sub; reconnect = history catch-up then live subscribe.

**Optimistic concurrency**: revision / `active_leaf_id` prevents silent forks when two devices edit/regenerate. Presence: if all devices disconnect mid-stream, decide whether to cancel generation (save cost) or finish and push notification.

### 4.2 Mid-Conversation Failure Recovery

**Claude's 50-token recovery threshold:**
- If output broke off under ~50 tokens: client silently discards partial message, re-issues request. User never sees the half-rendered output.
- If more than ~50 tokens were already streamed: partial output is inserted into history as an assistant turn with a system note ("the previous reply was interrupted, please continue from where you left off"), and stream resumes.
- This approach reduced perceived disconnect rate from ~30% of sessions to under 10%.

**RPO/RTO trade-off:**
- **Synchronous persistence** (write to PostgreSQL before acknowledging each token batch): near-zero mid-stream RPO, but adds ~5-15ms latency per token batch.
- **Asynchronous persistence** (buffer and flush after `done`): higher mid-stream RPO but lower per-token latency.
- Most production systems choose asynchronous with the 50-token recovery threshold as an acceptable middle ground.

### 4.3 Circuit Breaker -- CLOSED --> OPEN --> HALF_OPEN

```
                     failures >= N
          +----------+ ----------------> +----------+
          |  CLOSED  |                   |   OPEN   |--cool-down-->+------------+
          +----------+ <--success--+     +----------+              | HALF-OPEN  |
               ^                   |                               +-----+------+
               |                   |          trial success              | trial fail
               +-------------------+-------------------------------------+
```

| State | Behavior |
|-------|----------|
| **Closed** | All calls to primary model; count consecutive failures |
| **Open** | Fail fast to fallback chain for cool-down window |
| **Half-open** | Allow a probe request; success --> closed; failure --> open |

Apply breakers **per dependency** (primary LLM, secondary LLM, each tool bulkhead) with separate timeouts.

### 4.4 Fallback Model Chain

Graceful degradation chain: **frontier --> mid --> mini --> deterministic template** ("I'm temporarily unavailable. Please try again."). Keep system-prompt prefix stable across tiers where possible to preserve prompt cache.

### 4.5 Idempotency of Client Message IDs

Client generates a UUID `message_id` per user submit. Server unique-constrains `(conversation_id, message_id)`:
- First insert --> process turn
- Duplicate --> return existing turn / in-flight stream handle; **no second provider call**

This is the chat analogue of payment idempotency keys. Required for flaky mobile networks and double-tap send. Implementation: SHA-256 fingerprint of `(conversation_id, turn_index, message_content)` as Redis distributed lock key with TTL = inference timeout.

### 4.6 Multi-Region Deployment with Cell-Based Data Residency

```
+------------------------------------------------------------------+
|                 ACTIVE-ACTIVE MULTI-REGION                         |
|                                                                   |
| +------------------+  +------------------+  +------------------+  |
| |  EU CELL          |  |  US CELL          |  |  AP CELL          |  |
| |                   |  |                   |  |                   |  |
| | Anycast CDN       |  | Anycast CDN       |  | Anycast CDN       |  |
| | Envoy L7 LB       |  | Envoy L7 LB       |  | Envoy L7 LB       |  |
| | Stateless API      |  | Stateless API      |  | Stateless API      |  |
| | GPU cluster        |  | GPU cluster        |  | GPU cluster        |  |
| | PostgreSQL (RLS)   |  | PostgreSQL (RLS)   |  | PostgreSQL (RLS)   |  |
| | Redis, Vector DB   |  | Redis, Vector DB   |  | Redis, Vector DB   |  |
| | Regional KMS       |  | Regional KMS       |  | Regional KMS       |  |
| |                   |  |                   |  |                   |  |
| | EU data NEVER      |  | US data NEVER      |  | AP data NEVER      |  |
| | leaves EU cell     |  | leaves US cell     |  | leaves AP cell     |  |
| +------------------+  +------------------+  +------------------+  |
|                              |                                    |
|              +---------------v---------------+                    |
|              | GLOBAL CONTROL PLANE           |                    |
|              | (holds NO personal data)        |                    |
|              | Tenant Registry + Cell Router   |                    |
|              | DNS-Based Failover (Route 53)   |                    |
|              | Uptime: 99.99%                  |                    |
|              +-------------------------------+                    |
+------------------------------------------------------------------+
```

**Critical insight**: data residency is NOT just a database setting. A chatbot's personal data lives in conversation stores, vector indexes, prompt logs, AND inference request bodies. Designing cells around the database alone means embeddings and model calls will quietly breach compliance. Each cell must be self-contained: storage, inference, embeddings, logs, full orchestration.

Boundaries enforced with account-per-cell SCPs, regional KMS keys, in-region model invocation. Regional processing endpoints carry 10% cost uplift (OpenAI, from March 2026).

### 4.7 Prompt Injection Defense

Prompt injection is ranked **#1 on OWASP Top 10 for LLM Applications 2025 (LLM01)**. Attacks surged **340% in 2026**. A meta-analysis of 78 studies found adaptive attack success rates against state-of-the-art defenses exceed **85%**. OpenAI publicly acknowledged in Feb 2026 that prompt injection in AI browsers "may never be fully patched."

**The "Lethal Trifecta" (Simon Willison, 2025):** An agent is structurally exploitable when three properties co-exist:
1. Access to private data
2. Exposure to untrusted content
3. Ability to communicate externally

Remove any ONE to break the attack path.

**Real-world incidents (2025-2026):**

| Incident | Impact |
|----------|--------|
| **EchoLeak (CVE-2025-32711)** | Zero-click M365 Copilot exploit -- hidden email instructions caused Copilot to exfiltrate documents during inbox summarization |
| **GitHub Copilot RCE (CVE-2025-53773)** | Malicious code comments disabled user confirmations, granted unrestricted shell access |
| **Crypto wallet exploit (May 2026)** | Morse-code-encoded attack tricked AI wallet into $150K unauthorized transfer |
| **Cursor AI (April 2026)** | Coding agent deleted production database and backups in 9 seconds |

**Defense-in-depth architecture (6 layers):**

| Layer | Defense | Why It Works |
|-------|---------|-------------|
| **1. Architectural separation (Reader vs. Doer)** | Agent processing untrusted content can ONLY return structured analysis. It CANNOT call tools. | Most effective single defense. Breaks the Lethal Trifecta. |
| **2. Gateway-layer control** | Every request passes through a defense point that runs OUTSIDE the agent's orchestration logic. | Injection cannot disable it. |
| **3. Per-invocation security scrutiny** | Separate security service analyzes intent and destination BEFORE every tool invocation. | Catches hijacked tool calls. |
| **4. Task-scoped tool access (least privilege)** | Summarization: read-document only. Reply-drafting: read-document + write-draft. Drafts queued for human review. | Limits blast radius. |
| **5. Input filtering** | Role-confusion, hidden content (zero-width chars, white-on-white), encoding obfuscation, tool-call mimicry detection. | Catches known attack patterns. |
| **6. Output validation** | Classify generated output for policy violations. | Last line of defense. |

Industry readiness (2026): Only **34.7%** of organizations have deployed dedicated prompt injection defenses. 83% plan agentic AI but only 29% feel ready to do so securely.

### 4.8 Zero-Trust MCP for Tools

Treat every MCP/function tool as an untrusted capability boundary:

1. **Authenticate** the calling user/session; never pass provider API keys into model context
2. **Authorize** tool name + args against RBAC/FGA (tenant, role, resource ACL) before execution
3. **Validate** JSON against `strict: true` schemas; reject invented IDs
4. **Isolate** credentials in a vault/tool proxy -- model sees only tool names and sanitized results
5. **HITL** for irreversible/money-moving tools (Rule of Two: untrusted input x sensitive data x state change)

**Tool RBAC**: Allowlist tools per role (`tool_choice` / allowed-tools subset). Default deny. Separate read tools (calendar get) from write tools (send email, refund). FGA over RAG corpora and tools for multi-tenant copilots.

### 4.9 PII Pipeline -- Detect --> Redact --> Audit

```
  user/assistant text --> DETECT (regex + NER / classifiers: SSN, CC, phone, email, names)
                         --> REDACT / tokenize for logs & analytics sinks
                         --> AUDIT event (what class redacted, where, by whom)
                         --> PERSIST policy-compliant form to conversation SoR
```

Three handling modes:
- **Redact** (enterprise default): replace PII with `[EMAIL_1]`, `[SSN_REDACTED]`, reverse-map in display
- **Log-scrub**: PII reaches LLM for response quality but scrubbed from all logs/telemetry/persistence
- **Opt-in pass-through**: explicit tenant-level opt-in with enhanced audit logging

PII detected in model outputs is scrubbed from telemetry and flagged if the output contains PII not present in the input (potential hallucinated PII).

### 4.10 Data Retention & Privacy (GDPR)

**Regulatory landscape (binding 2026):**
- GDPR: data processing, consent, right to erasure
- EU AI Act Article 50: mandatory transparency labelling for AI interactions (binding August 2026)
- NIST AI Risk Management Framework
- ISO 42001 (AI-specific controls)

**GDPR vs. EU AI Act tension**: GDPR demands erasure when purpose is fulfilled (Storage Limitation Principle, Article 5(1)(e)). EU AI Act demands lengthy archival of system documentation for traceability. Resolution: secure deletion of personal data immediately after system finalization, with anonymized data sets for compliance archival.

**Right to erasure (Article 17)**: users can request deletion of all personal data within 30 days. Conversation data flows across multiple systems -- conversation store, vector DB, prompt logs, inference logs -- ALL must be addressed. Data embedded in trained model parameters cannot be "deleted"; irreversible anonymization is the only compliant path for retained training data.

Fines: up to 4% of annual global revenue or EUR 20 million (whichever higher). EUR 2.1 billion in GDPR fines issued in 2025 alone.

### 4.11 Immutable Audit Logs

Every conversation turn logged to append-only store with: user identity (pseudonymized ID), tenant ID, model used, input/output token counts, content hash (SHA-256 -- enables integrity verification without storing raw content), moderation decisions (pass/flag/block with classifier scores), tool invocations and outcomes, timestamp.

Storage: WORM (Write Once Read Many) configuration -- S3 Object Lock with Compliance mode. Retention: 7 years for regulated industries, 2 years default. Satisfies EU AI Act Article 12 (record-keeping for high-risk AI). Pseudonymized audit record (content hash, not content) retained even after GDPR erasure.

### 4.12 Secure Infrastructure

- Read-only filesystems for model serving
- No egress network from inference containers
- Model weights loaded from encrypted pre-signed URLs
- Namespace isolation in Kubernetes for multi-tenancy
- gVisor sandboxing (not just containers) for execution isolation -- standard containers share host kernel

### 4.13 Multi-Dimensional Rate Limiter

Three dimensions enforced simultaneously:
- **RPM** (Requests Per Minute) per user
- **TPM** (Tokens Per Minute) per tier
- **Concurrent streams** per connection

Primary: Redis sliding window (shared across instances). Fallback: in-memory counters (per-instance, gracefully degraded when Redis unavailable). Under load test: 86.4% of excess requests shed as 429 responses, protecting downstream inference.

Tier definitions example:

| Tier | RPM | TPM | Max Concurrent |
|------|-----|-----|----------------|
| Free | 10 | 10,000 | 1 |
| Pro | 60 | 100,000 | 3 |
| Enterprise | 300 | 1,000,000 | 10 |

---

## 5. Failure Modes

### 5.1 GPU Degradation Under Load

GPU saturation is insidious because it produces **no error code** -- output quality simply declines silently.

**GPU saturation signals:**
- GPU at 40% compute utilization but 95% memory utilization --> P99 latency 8s while P50 looks healthy at 1.2s
- GPU temperature hitting throttle threshold + simultaneous utilization drop --> thermal throttling
- KV-cache exhaustion --> sudden latency spikes and timeouts
- DRAM bandwidth saturation --> over 50% of attention kernel cycles stalled on data access

**CPU-induced degradation**: under heavy load, tokenizer threads compete with the LLM engine for CPU. Frequent context switches and delayed kernel launches reduce GPU utilization. High CPU load can increase kernel launch latency from microseconds to milliseconds. Result: expensive GPU infrastructure delivers a fraction of expected throughput.

**Quantization quality thresholds:**

| Quantization | Quality Impact | Production Viability |
|-------------|---------------|---------------------|
| FP8 / INT8 | < 1-2% degradation | Imperceptible for most use cases |
| INT4 | 1-4% perplexity increase | Acceptable for chatbots, summarization, Q&A |
| Below INT4 | User-visible degradation | Not recommended |

**Alerting thresholds**: P99 latency > 5s, GPU error rates > 0.1%, KV-cache usage approaching capacity.

### 5.2 Comprehensive Failure Taxonomy

| Failure Class | Examples | Detection | Mitigation |
|--------------|----------|-----------|------------|
| **Transient** | 429 rate limit, 503 provider outage, SSE connection drop, idle stream timeout | HTTP status codes, `Retry-After`, missing heartbeats | Backoff + jitter; honor Retry-After; circuit breaker; bounded retries (max 3) |
| **Permanent** | 400 invalid schema, auth fail, policy deny, deleted conversation, account suspended | Non-retryable status, validation errors | Fail fast, no retry; surface clear error; fallback chain skips to next provider only if error is provider-specific |
| **Poison pill** | Repeated identical tool args looping, malformed tool JSON that always fails, prompt injection | Duplicate-call detector, max iterations / $ cap, moderation flags | Hard stop; `parallel_tool_calls: false`; quarantine conversation; alert security team |
| **Partial stream** | Disconnect before `completed` / `message_stop` | Missing terminal event, sequence gaps | Persist partial + `interrupted`; channel replay; Claude's 50-token threshold |
| **State drift** | Two devices fork branches, duplicate assistants | Version/leaf mismatch | Single-writer election per conversation or merge UI |
| **TPM despite cache** | 429 while bills look "cached-cheap" | Rate-limit headers | Cache hits do NOT reduce TPM -- plan on raw tokens |
| **Context overflow** | Truncation, lost constraints, model "forgets" system rules | Token counters, rising compaction frequency | Hybrid compaction (6 verbatim + summarize older + structured memory) |
| **Cache stampede** | Cost spike, TTFT spike | Cache hit-rate dashboards | Stable prefix ordering; ~15 RPM per prefix key |
| **Cascading timeouts** | Gateway waits on LLM waits on tool | Deadline propagation, bulkheads | Separate budgets: tools 2-5s, LLM stream idle timeout, client UX cancel |
| **Idempotency failure** | Network retry sends duplicate inference request | Request fingerprint collision | Redis distributed lock with fingerprint (SHA-256 of conv_id + turn + content) |
| **Output moderation gap** | Harmful tokens already rendered during streaming | Post-hoc flags after completion | Delay irreversible side effects until full moderation; token-window scrubber |
| **Hallucinated tool params** | Invalid JSON, wrong types, invented IDs | Schema validation (`strict`), parse-on-`content_block_stop` | Reject + retry-with-correction; disable fine-grained streaming if validity > latency |
| **Inference timeout** | Default 30-60s timeout aborts long generations | 100K+ token requests take 30-60s just for prefill | Configure 120-300s timeouts |

---

## 6. Architectural System Design Scenarios

### Scenario A: Enterprise AI Chat Assistant at Scale

**Problem statement.** A B2B SaaS company with 1M registered users (100K DAU) needs to build an AI chat assistant integrated into their product. Requirements: sub-500ms TTFT at p95, support for 150 peak requests/second, multi-turn conversations with context persistence, content moderation, cost optimization (blended target < $0.005 per turn), and 99.99% uptime across two regions.

**Architecture (three-tier separation):**

```
+--------------------------------------------------------------------------+
|                    ENTERPRISE CHAT ASSISTANT AT SCALE                      |
|                                                                          |
|  +--------------------------------------------------------------------+  |
|  |                    CONNECTION TIER                                   |  |
|  |                    (scales for persistent connectivity)              |  |
|  |                                                                     |  |
|  |  CDN Edge (Cloudflare / CloudFront)                                 |  |
|  |      |                                                              |  |
|  |  API Gateway (Kong)                                                 |  |
|  |      +-- JWT auth + RBAC (free/pro/enterprise)                      |  |
|  |      +-- Multi-dim rate limiter (Redis: RPM + TPM + concurrent)     |  |
|  |      +-- Request validation + W3C Trace Context                     |  |
|  |      +-- URL-based routing to backend services                      |  |
|  +------------------------------+--------------------------------------+  |
|                                  |                                        |
|  +------------------------------v--------------------------------------+  |
|  |                    ORCHESTRATION TIER                                |  |
|  |                    (stateless, horizontal autoscale)                 |  |
|  |                                                                     |  |
|  |  +-----------+  +-----------+  +-----------------------------------+|  |
|  |  | Context    |  | Moderation|  | LLM Router                       ||  |
|  |  | Assembler  |  | Pipeline  |  |                                   ||  |
|  |  |            |  |           |  | Complexity classifier:             ||  |
|  |  | Redis      |  | Pre: BERT |  |  Simple --> Haiku 4.5 ($0.25/M)   ||  |
|  |  | (session)  |  | classif.  |  |  Standard -> Sonnet 4.5 ($3/M)    ||  |
|  |  | Postgres   |  | Post:     |  |  Complex --> Opus 4.5 ($15/M)     ||  |
|  |  | (durable)  |  | output    |  |                                   ||  |
|  |  | Pinecone   |  | valid.    |  |  Fallback: Sonnet->Sol->Haiku     ||  |
|  |  | (embeds)   |  |           |  |   (circuit breaker per endpoint)   ||  |
|  |  |            |  |           |  |                                   ||  |
|  |  | Compaction:|  |           |  |                                   ||  |
|  |  | 6 verbatim |  |           |  |                                   ||  |
|  |  | +summarize |  |           |  |                                   ||  |
|  |  +-----------+  +-----------+  +-----------------------------------+|  |
|  +------------------------------+--------------------------------------+  |
|                                  |                                        |
|  +------------------------------v--------------------------------------+  |
|  |                    INFERENCE TIER                                    |  |
|  |                    (GPU clusters, scaled independently)              |  |
|  |                                                                     |  |
|  |  vLLM / TGI Inference Servers                                       |  |
|  |   Continuous batching for throughput                                 |  |
|  |   FP8 quantization (< 2% quality loss)                              |  |
|  |   KV-cache management + monitoring                                  |  |
|  |   ~30x H100 for 1M users (18K TPS target)                          |  |
|  |                                                                     |  |
|  |   Autoscaling:                                                      |  |
|  |    Predictive (pre-scale for US business hours)                     |  |
|  |    Reactive (HPA on GPU util + queue depth)                         |  |
|  |    Cold start: 30-90s model load; mitigate with pre-warmed standby  |  |
|  +--------------------------------------------------------------------+  |
|                                                                          |
|  +--------------------------------------------------------------------+  |
|  |                    PERSISTENCE & OBSERVABILITY                      |  |
|  |                                                                     |  |
|  |  PostgreSQL (conversations, messages, sharded by conv_id)           |  |
|  |  Redis (session cache 24h TTL, rate limit counters)                 |  |
|  |  Pinecone/pgvector (conversation embeddings, long-term memory)      |  |
|  |  Kafka --> Flink --> ClickHouse (structured logs, anomaly detect)   |  |
|  |  Prometheus + Grafana (TTFT p95/p99, TPS, GPU util, error, cost)   |  |
|  |  OpenTelemetry (browser -> gateway -> orchestrator -> inference)    |  |
|  |                                                                     |  |
|  |  Dashboard: Row 1: error rate | P95 latency | cost/hr              |  |
|  |             Row 2: eval scores | user feedback | retry rate         |  |
|  +--------------------------------------------------------------------+  |
+--------------------------------------------------------------------------+
```

**Trade-off matrix:**

| Decision | Option A | Option B | Chosen |
|----------|----------|----------|--------|
| **Inference hosting** | API-based (Anthropic, OpenAI): no GPU mgmt, no cold start, faster time-to-market; data sent externally, linear cost scaling | Self-hosted (vLLM on H100 cluster): data stays internal, amortized at high volume; 30-90s cold start, GPU ops team needed | API-based at launch. Migrate to self-hosted when >200K queries/month |
| **Streaming protocol** | SSE: HTTP/1.1 compatible, works through CDNs, auto-reconnection, simpler | WebSocket: bidirectional, real-time collab; proxy issues, manual reconnection, overkill for chat | SSE (industry consensus) |
| **Context management** | Sliding window (last 10-15 exchanges): simplest, fastest, lowest cost; old context lost, users notice gaps | Hybrid: 6 verbatim + summarize older + structured memory: continuity preserved, facts survive lossless; summarization overhead, potential hallucination in summary | Hybrid (users cannot tolerate losing context) |
| **Model routing** | Single model (Sonnet): simple to operate, consistent quality; expensive for simple queries | Three-tier router: 60% cost reduction, right model for task; complexity classifier needed, quality variance | Three-tier (economics dominate at 100K DAU) |

**Decision rationale.** The three-tier separation (connection, orchestration, inference) is the core architectural decision. Connection-heavy frontend scales independently from compute-heavy inference -- you can handle 10x more SSE connections without adding GPU capacity. Three-tier model routing is essential at 100K DAU because 60% cost reduction at that volume translates to tens of thousands of dollars per month.

---

### Scenario B: Multi-Device Personal Productivity Assistant

**Problem statement.** Design a single-tenant personal assistant for ~50K MAU: multi-turn streaming chat, light preference memory, optional calendar/email tools, and seamless phone-to-laptop handoff. Users abandon if a mid-answer is lost when switching devices. Budget target ~thousands of conversations/day; p50 TTFT under ~500ms.

**Architecture:**

```
  +---------+   +---------+
  | Mobile  |   |  Web    |
  +----+----+   +----+----+
       | OIDC        |
       v             v
  +----------------------------------------+
  |         API GATEWAY + QUOTAS           |
  +-------------------+--------------------+
                      |
           +----------+----------+
           v          v          v
  +---------+  +--------+  +----------------+
  | Conv.   |  |Context |  | Stream + PubSub|
  | Service |  |Builder |  | (SSE + channel)|
  |(Postgres|  |sys+K+  |  | sequence resume|
  | SoR)    |  |memory) |  +--------+-------+
  +----+----+  +---+----+          |
       |          v               |
       |    +-----------+         |
       |    | Model API |         |
       |    | stream=1  |---------+
       |    +-----+-----+
       |          v
       |    +-----------+
       +--->| MCP tools |-- vault --> Calendar / Email
            | RBAC+HITL |
            +-----------+
```

**Trade-off matrix:**

| Approach | Cost | Latency | Multi-device | Security |
|----------|------|---------|-------------|----------|
| **A1. Plain SSE only** (no channel) | Low | Good on one device | Poor -- no mid-stream resume | Medium |
| **A2. Server-canonical + pub/sub channel** (recommended) | Medium | Excellent resume; TTFT if unbuffered | Scales with channel + DB; devices catch up by sequence | Strong (you own ACLs) |
| **A3. Provider Conversations as sole SoR** | Medium API $ | Excellent | Easy cross-device | Medium (vendor retention/residency coupling) |

**Decision rationale.** Choose **A2**. Multi-device handoff is a hard product requirement; plain SSE (A1) cannot resume mid-stream on a new device. Provider Conversations (A3) simplify sync but couple retention/compliance and make GDPR deletes/export harder. Own the Postgres transcript + channel fan-out; use provider APIs for generation.

---

### Scenario C: Multi-Tenant AI Chat Platform

**Problem statement.** A platform company building a white-label AI chat product for enterprise customers. Each tenant has their own branding, system prompts, model preferences, and data isolation requirements. Requirements: strict tenant isolation (data, inference, rate limits), GDPR-compliant data residency per tenant geography, 7K+ concurrent sessions across tenants, per-tenant billing, defense against cross-tenant data leakage including KV-cache side-channel attacks.

**Architecture:**

```
+--------------------------------------------------------------------------+
|                    MULTI-TENANT AI CHAT PLATFORM                          |
|                                                                          |
|  +--------------------------------------------------------------------+  |
|  |                    GLOBAL CONTROL PLANE (no personal data)          |  |
|  |                                                                     |  |
|  |  +----------------+  +----------------+  +---------------------+   |  |
|  |  | Tenant Registry |  | Cell Router    |  | Billing Aggregator  |   |  |
|  |  | tenant_id->cell |  | Map request to |  | Per-tenant token    |   |  |
|  |  | tier, model     |  | home cell      |  | usage metering      |   |  |
|  |  | prefs, branding |  | BEFORE any     |  | Tiered pricing      |   |  |
|  |  | system prompt   |  | content is     |  |                     |   |  |
|  |  |                 |  | processed      |  |                     |   |  |
|  |  +----------------+  +----------------+  +---------------------+   |  |
|  +------------------------------+--------------------------------------+  |
|                                  |                                        |
|         +------------------------+------------------------+               |
|         |                        |                        |               |
|  +------v--------+  +------v--------+  +------v--------+               |
|  | EU CELL        |  | US CELL        |  | AP CELL        |               |
|  |                |  |                |  |                |               |
|  | API Gateway    |  | API Gateway    |  | API Gateway    |               |
|  | + Rate Limiter |  | + Rate Limiter |  | + Rate Limiter |               |
|  | (per-tenant)   |  | (per-tenant)   |  | (per-tenant)   |               |
|  |      |         |  |      |         |  |      |         |               |
|  | Orchestrator   |  | Orchestrator   |  | Orchestrator   |               |
|  | (namespace     |  | (namespace     |  | (namespace     |               |
|  |  isolated)     |  |  isolated)     |  |  isolated)     |               |
|  |      |         |  |      |         |  |      |         |               |
|  | GPU Inference  |  | GPU Inference  |  | GPU Inference  |               |
|  | (KV-cache      |  | (KV-cache      |  | (KV-cache      |               |
|  |  per-tenant)   |  |  per-tenant)   |  |  per-tenant)   |               |
|  |      |         |  |      |         |  |      |         |               |
|  | Storage:       |  | Storage:       |  | Storage:       |               |
|  | Postgres (RLS) |  | Postgres (RLS) |  | Postgres (RLS) |               |
|  | Redis          |  | Redis          |  | Redis          |               |
|  | Vector DB      |  | Vector DB      |  | Vector DB      |               |
|  | (per-tenant    |  | (per-tenant    |  | (per-tenant    |               |
|  |  namespace)    |  |  namespace)    |  |  namespace)    |               |
|  | Regional KMS   |  | Regional KMS   |  | Regional KMS   |               |
|  +----------------+  +----------------+  +----------------+               |
|                                                                          |
|  Execution Pipeline (per request):                                       |
|  Auth -> Tenant -> Budget -> Session -> Context -> Sandbox -> LLM        |
|    |       |         |         |          |          |         -> Persist |
|   JWT   tenant_id  token    state      prompt     gVisor                 |
|   verify resolve   quota   machine    assembly   isolation               |
|          route to  check                                                 |
|          home cell                                                       |
+--------------------------------------------------------------------------+
```

**Trade-off matrix:**

| Decision | Option A | Option B | Chosen |
|----------|----------|----------|--------|
| **Tenant isolation model** | Fully shared (tenant_id filtering): lowest cost, simplest ops; noisy neighbor risk, cross-tenant leakage via bugs | Hybrid namespace (shared infra, K8s namespace per tenant group): strong isolation, balanced cost, 2026 standard; more complex ops | Hybrid namespace. Fully siloed only for regulated industries. |
| **Database isolation** | Application-level filtering (WHERE tenant_id = ?): simpler; developer can omit, ORM can strip, injection can bypass | Row-Level Security (RLS): policy runs inside DB engine on every query; injection cannot override, developer cannot omit | RLS (defense-in-depth, non-negotiable) |
| **Vector DB isolation** | Shared index with tenant_id filter: simple; semantic search is approximate, CAN return wrong-tenant matches; compliance violation | Per-tenant namespaces + tenant_id stamped on every vector + required filter on every query: no cross-tenant leakage | Per-tenant namespaces (critical for correctness) |
| **KV-cache management** | Shared prefix cache: higher cache hit rate, lower GPU memory; TTFT timing side-channel attack (NDSS 2025, Llama2-13B/A100) | Per-tenant cache partitioning: no side-channel attack, prevents cross-tenant information leak; lower cache efficiency | Per-tenant partitioning (security over efficiency) |

**Decision rationale.** The cell-based architecture is the most consequential decision. RLS is non-negotiable because application-level tenant filtering fails silently in too many ways. Per-tenant vector DB namespaces are critical because semantic search is approximate by design. KV-cache partitioning per tenant prevents the TTFT timing side-channel attack demonstrated at NDSS 2025.

**Salesforce reference (production):** Multi-tenant AI agent platform handling 7K+ concurrent sessions. Session management persists state machine, action queue, and pending confirmations via SDK. 24-hour TTL policy preserves relevant context while auto-evicting stale data.

---

## Production Enterprise Code

### Combined Chat Turn Loop with Circuit Breaker, Fallback Chain, Idempotency, and Context Compaction

```python
"""
Production personal-chat turn loop demonstrating all key patterns:
- Client message_id idempotency (no duplicate inference)
- Server-canonical durable conversation store
- Retries with exponential backoff + deterministic jitter
- Three-state circuit breaker (CLOSED -> OPEN -> HALF_OPEN)
- Fallback model chain (primary -> fallback -> template)
- Context compaction (6 verbatim exchanges + summarize older)
- Multi-dimensional rate limiting (RPM + TPM + concurrent)
- PII detection stub
- Correlation IDs on every log line

Runs standalone with fake models (no API keys, no network).
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterator, List, Optional


# ---------------------------------------------------------------------------
# Deterministic clock + jitter (reproducible, no wall-clock)
# ---------------------------------------------------------------------------

class DetClock:
    """Monotonic clock for deterministic testing."""
    def __init__(self) -> None:
        self._ms = 0

    def now_ms(self) -> int:
        return self._ms

    def advance(self, ms: int) -> None:
        self._ms += ms


def det_jitter_ms(correlation_id: str, attempt: int, cap_ms: int = 50) -> int:
    """Bounded jitter from correlation_id + attempt -- reproducible."""
    h = hashlib.sha256(f"{correlation_id}:{attempt}".encode()).hexdigest()
    return int(h[:8], 16) % (cap_ms + 1)


# ---------------------------------------------------------------------------
# Circuit breaker (three-state)
# ---------------------------------------------------------------------------

class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """
    CLOSED: requests flow; count consecutive failures.
    OPEN: fail-fast to fallback chain for cool_down_ms.
    HALF_OPEN: one probe request; success -> CLOSED, failure -> OPEN.
    """
    failure_threshold: int = 2
    cool_down_ms: int = 100
    clock: DetClock = field(default_factory=DetClock)
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    opened_at_ms: Optional[int] = None

    def allow(self) -> bool:
        if self.state == BreakerState.CLOSED:
            return True
        if self.state == BreakerState.OPEN:
            assert self.opened_at_ms is not None
            if self.clock.now_ms() - self.opened_at_ms >= self.cool_down_ms:
                self.state = BreakerState.HALF_OPEN
                return True  # allow one probe
            return False
        return True  # HALF_OPEN: one probe

    def record_success(self) -> None:
        self.failures = 0
        self.state = BreakerState.CLOSED
        self.opened_at_ms = None

    def record_failure(self) -> None:
        self.failures += 1
        if (self.state == BreakerState.HALF_OPEN
                or self.failures >= self.failure_threshold):
            self.state = BreakerState.OPEN
            self.opened_at_ms = self.clock.now_ms()
            self.failures = 0


# ---------------------------------------------------------------------------
# Fake models (deterministic, no network)
# ---------------------------------------------------------------------------

class TransientProviderError(Exception):
    pass


@dataclass
class FakeModel:
    name: str
    fail_times: int = 0   # how many calls fail before succeeding
    _calls: int = 0

    def stream(self, prompt_tokens: int) -> Iterator[str]:
        self._calls += 1
        if self._calls <= self.fail_times:
            raise TransientProviderError(
                f"{self.name} transient failure #{self._calls}"
            )
        words = [f"tok{i}" for i in range(min(5, 1 + prompt_tokens // 1000))]
        for w in words:
            yield w + " "


# ---------------------------------------------------------------------------
# Idempotent conversation store (server-canonical)
# ---------------------------------------------------------------------------

@dataclass
class Turn:
    message_id: str
    role: str
    content: str
    model: str
    correlation_id: str
    status: str   # completed | interrupted

    @property
    def token_count(self) -> int:
        """Approximate token count (~1.3 tokens/word)."""
        return max(1, int(len(self.content.split()) * 1.3))


@dataclass
class ConversationStore:
    """Append-only, idempotent by message_id."""
    turns: Dict[str, Turn] = field(default_factory=dict)
    order: List[str] = field(default_factory=list)

    def get(self, message_id: str) -> Optional[Turn]:
        return self.turns.get(message_id)

    def append(self, turn: Turn) -> Turn:
        existing = self.turns.get(turn.message_id)
        if existing is not None:
            return existing   # idempotent: no duplicate append
        self.turns[turn.message_id] = turn
        self.order.append(turn.message_id)
        return turn

    def history(self) -> List[Turn]:
        return [self.turns[mid] for mid in self.order]


# ---------------------------------------------------------------------------
# Context compaction (hybrid: 6 verbatim + summarize older)
# ---------------------------------------------------------------------------

def compact_context(
    turns: List[Turn],
    verbatim_exchanges: int = 6,
    max_input_tokens: int = 128_000,
) -> dict:
    """
    Production hybrid compaction strategy:
    - Keep last `verbatim_exchanges` exchanges verbatim
    - Summarize everything older into a compact summary
    - Always include system prompt and structured memory

    Returns dict with compacted_turns, summary, tokens_saved,
    compaction_applied flag.
    """
    # Each exchange = 1 user + 1 assistant turn = 2 messages
    split_idx = max(0, len(turns) - (verbatim_exchanges * 2))
    older = turns[:split_idx]
    recent = turns[split_idx:]

    total_tokens = sum(t.token_count for t in turns)

    if total_tokens <= max_input_tokens or not older:
        return {
            "turns": turns,
            "summary": None,
            "tokens_saved": 0,
            "compaction_applied": False,
        }

    # Summarize older turns (in production, this calls a cheap LLM)
    summary_parts = []
    for t in older:
        summary_parts.append(f"{t.role}: {t.content[:80]}...")
    summary = "Prior context summary: " + " | ".join(summary_parts)

    older_tokens = sum(t.token_count for t in older)
    summary_tokens = max(1, int(len(summary.split()) * 1.3))
    tokens_saved = older_tokens - summary_tokens

    # Create a synthetic summary turn
    summary_turn = Turn(
        message_id="summary",
        role="system",
        content=summary,
        model="compaction",
        correlation_id="compact",
        status="completed",
    )
    compacted = [summary_turn] + list(recent)

    return {
        "turns": compacted,
        "summary": summary,
        "tokens_saved": tokens_saved,
        "compaction_applied": True,
    }


# ---------------------------------------------------------------------------
# Multi-dimensional rate limiter (in-memory for demo)
# ---------------------------------------------------------------------------

@dataclass
class RateLimits:
    rpm: int
    tpm: int
    max_concurrent: int


TIER_LIMITS = {
    "free":       RateLimits(rpm=10,  tpm=10_000,    max_concurrent=1),
    "pro":        RateLimits(rpm=60,  tpm=100_000,   max_concurrent=3),
    "enterprise": RateLimits(rpm=300, tpm=1_000_000, max_concurrent=10),
}


@dataclass
class RateLimiter:
    """Sliding-window rate limiter (in-memory for demo; Redis in prod)."""
    _request_counts: Dict[str, int] = field(default_factory=dict)
    _token_counts: Dict[str, int] = field(default_factory=dict)
    _concurrent: Dict[str, int] = field(default_factory=dict)

    def check(self, user_id: str, tier: str, est_tokens: int) -> dict:
        limits = TIER_LIMITS.get(tier, TIER_LIMITS["free"])
        rpm = self._request_counts.get(user_id, 0)
        tpm = self._token_counts.get(user_id, 0)
        conc = self._concurrent.get(user_id, 0)

        if rpm >= limits.rpm:
            return {"allowed": False, "reason": f"RPM {rpm}/{limits.rpm}"}
        if tpm + est_tokens > limits.tpm:
            return {"allowed": False, "reason": f"TPM exceeded"}
        if conc >= limits.max_concurrent:
            return {"allowed": False, "reason": f"Concurrent limit"}

        self._request_counts[user_id] = rpm + 1
        self._token_counts[user_id] = tpm + est_tokens
        self._concurrent[user_id] = conc + 1
        return {"allowed": True}

    def release(self, user_id: str) -> None:
        self._concurrent[user_id] = max(
            0, self._concurrent.get(user_id, 0) - 1
        )


# ---------------------------------------------------------------------------
# Chat turn loop (main orchestrator)
# ---------------------------------------------------------------------------

@dataclass
class ChatTurnLoop:
    store: ConversationStore
    primary: FakeModel
    fallbacks: List[FakeModel]
    breaker: CircuitBreaker
    clock: DetClock
    rate_limiter: RateLimiter = field(default_factory=RateLimiter)
    max_retries: int = 3
    base_backoff_ms: int = 10
    log: List[str] = field(default_factory=list)

    def _log(self, cid: str, msg: str) -> None:
        self.log.append(f"cid={cid} t={self.clock.now_ms()} {msg}")

    def _chain(self) -> List[FakeModel]:
        return [self.primary, *self.fallbacks]

    def run_user_turn(
        self,
        *,
        message_id: str,
        user_text: str,
        correlation_id: str,
        user_id: str = "user-1",
        tier: str = "pro",
        prompt_tokens: int = 5000,
    ) -> Turn:
        # --- Idempotency check ---
        existing = self.store.get(message_id)
        if existing is not None:
            self._log(correlation_id,
                      f"idempotent_hit message_id={message_id}")
            return existing

        # --- Rate limit check ---
        rl = self.rate_limiter.check(user_id, tier, prompt_tokens)
        if not rl["allowed"]:
            self._log(correlation_id,
                      f"rate_limited reason={rl['reason']}")
            return Turn(
                message_id=message_id, role="assistant",
                content=f"Rate limited: {rl['reason']}",
                model="rate_limiter", correlation_id=correlation_id,
                status="completed",
            )

        # --- Persist user turn BEFORE inference ---
        user_turn = Turn(
            message_id=f"user:{message_id}", role="user",
            content=user_text, model="n/a",
            correlation_id=correlation_id, status="completed",
        )
        self.store.append(user_turn)
        self._log(correlation_id,
                  f"persisted_user message_id={message_id}")

        # --- Context compaction ---
        history = self.store.history()
        compaction = compact_context(history)
        if compaction["compaction_applied"]:
            self._log(correlation_id,
                      f"compacted tokens_saved={compaction['tokens_saved']}")

        # --- Inference with circuit breaker + fallback chain ---
        try:
            last_err: Optional[Exception] = None
            for model in self._chain():
                is_primary = model.name == self.primary.name

                if is_primary and not self.breaker.allow():
                    self._log(correlation_id,
                              f"breaker_open skip model={model.name}")
                    continue

                for attempt in range(self.max_retries):
                    if is_primary and not self.breaker.allow():
                        self._log(correlation_id,
                                  f"breaker_open abort model={model.name}")
                        break
                    try:
                        self._log(
                            correlation_id,
                            f"attempt model={model.name} n={attempt} "
                            f"breaker={self.breaker.state.value}",
                        )
                        chunks: List[str] = []
                        for tok in model.stream(prompt_tokens):
                            chunks.append(tok)
                            self.clock.advance(5)
                        reply = "".join(chunks).rstrip()

                        if is_primary:
                            self.breaker.record_success()

                        assistant = Turn(
                            message_id=message_id, role="assistant",
                            content=reply, model=model.name,
                            correlation_id=correlation_id,
                            status="completed",
                        )
                        stored = self.store.append(assistant)
                        self._log(
                            correlation_id,
                            f"completed model={model.name} "
                            f"tokens_out={len(chunks)}",
                        )
                        return stored

                    except TransientProviderError as exc:
                        last_err = exc
                        if is_primary:
                            self.breaker.record_failure()
                        jitter = det_jitter_ms(correlation_id, attempt)
                        backoff = self.base_backoff_ms * (2**attempt) + jitter
                        self._log(
                            correlation_id,
                            f"retry model={model.name} err={exc} "
                            f"backoff_ms={backoff}",
                        )
                        self.clock.advance(backoff)

                self._log(correlation_id,
                          f"exhausted_retries model={model.name}")

            # All models exhausted -- deterministic template fallback
            self._log(correlation_id, "template_fallback")
            assistant = Turn(
                message_id=message_id, role="assistant",
                content="I'm temporarily unavailable. Please try again.",
                model="template", correlation_id=correlation_id,
                status="completed",
            )
            return self.store.append(assistant)

        finally:
            self.rate_limiter.release(user_id)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def demo() -> None:
    clock = DetClock()
    breaker = CircuitBreaker(
        failure_threshold=2, cool_down_ms=100, clock=clock
    )
    loop = ChatTurnLoop(
        store=ConversationStore(),
        primary=FakeModel(name="sonnet-4.5-fake", fail_times=2),
        fallbacks=[FakeModel(name="haiku-4.5-fake", fail_times=0)],
        breaker=breaker,
        clock=clock,
    )

    cid = "corr-001"
    # Turn 1: primary fails twice -> breaker opens -> fallback succeeds
    t1 = loop.run_user_turn(
        message_id="msg-aaa", user_text="Hello",
        correlation_id=cid, prompt_tokens=5000,
    )
    assert t1.model == "haiku-4.5-fake"  # fell through to fallback

    # Duplicate submit -- idempotent, no second inference
    t1b = loop.run_user_turn(
        message_id="msg-aaa", user_text="Hello",
        correlation_id="corr-002", prompt_tokens=5000,
    )
    assert t1.message_id == t1b.message_id == "msg-aaa"
    assert t1.content == t1b.content

    # After cool-down, half-open probe uses recovered primary
    clock.advance(100)
    loop.primary = FakeModel(name="sonnet-4.5-fake", fail_times=0)
    t2 = loop.run_user_turn(
        message_id="msg-bbb", user_text="Second question",
        correlation_id="corr-003", prompt_tokens=2000,
    )
    assert t2.model == "sonnet-4.5-fake"
    assert t2.status == "completed"

    print("Turns:")
    for mid in loop.store.order:
        t = loop.store.turns[mid]
        print(f"  {t.role:9s} | {t.message_id:15s} | "
              f"{t.model:20s} | {t.content[:40]}")
    print(f"\nLog ({len(loop.log)} lines):")
    for line in loop.log:
        print(f"  {line}")
    print("\nAll assertions passed.")


if __name__ == "__main__":
    demo()
```

**What this demonstrates:**

| Concern | Mechanism in Code |
|---------|-------------------|
| Idempotency | `ConversationStore.append` keyed by client `message_id` |
| Durable user turn | User message persisted BEFORE inference |
| Context compaction | `compact_context()`: 6 verbatim exchanges + summarize older |
| Retries + jitter | `base_backoff_ms * 2^attempt + det_jitter_ms(...)` |
| Circuit breaker | CLOSED --> OPEN --> HALF_OPEN with cool-down on DetClock |
| Fallback chain | primary --> fallbacks --> template string |
| Rate limiting | RPM + TPM + concurrent checks before inference |
| Correlation IDs | Every log line prefixed `cid=...` |

---

## Common Failure Modes (Quick Reference Table)

| # | Failure Mode | Symptom | Root Cause | Fix |
|---|-------------|---------|------------|-----|
| 1 | Context window overflow | Model "forgets" system rules, truncates | Context tax quadratic growth | Hybrid compaction: 6 verbatim + summarize older |
| 2 | Proxy buffering kills TTFT | User sees no tokens for seconds despite fast model | Reverse proxy buffers SSE chunks | Set `X-Accel-Buffering: no`; TransformStream tick flush |
| 3 | TPM exhausted despite caching | 429 errors while costs look low | Cache does NOT reduce TPM accounting | Plan capacity on raw tokens, not cached $; shed to mini |
| 4 | Silent quality degradation | No error, but output quality declines | GPU memory saturation (95% mem, 40% compute) | Monitor GPU mem vs compute divergence; alert on P99 > 5s |
| 5 | Mid-stream disconnect | Partial answer shown; other devices see nothing | Network instability, LB timeout | 50-token threshold; persist partial + interrupted; channel replay |
| 6 | Prompt injection | Policy bypass, tool misuse, data exfiltration | LLM cannot distinguish instructions from data | 6-layer defense-in-depth; reader vs doer separation |
| 7 | Cross-tenant data leak | Wrong-tenant results in multi-tenant RAG | Shared vector index returns approximate matches | Per-tenant namespaces; RLS; KV-cache partitioning |
| 8 | Infinite tool loops | Runaway cost, oscillating tool calls | Model re-invokes same tool with same args | Max iterations + $ cap; duplicate-call detector |
| 9 | Cache stampede | Cost + TTFT spike on cache miss storm | Prefix ordering changed or low RPM per prefix | Stable prefix ordering; >= 15 RPM per cache key |
| 10 | State drift (multi-device) | Forked branches, duplicate assistant messages | Two devices edit simultaneously | Optimistic concurrency on `active_leaf_id`; single-writer election |

---

## Key Takeaways for Interviews

1. **LLMs are stateless** -- the system must reconstruct full context on every request. This is the fundamental constraint that drives conversation state management, compaction, and cost engineering.

2. **SSE, not WebSocket** -- industry consensus for token streaming. SSE is unidirectional, HTTP/1.1 compatible, works through CDNs, has built-in browser reconnection. WebSocket adds unnecessary bidirectional complexity.

3. **Prompt caching is the single biggest cost lever** -- cached input at ~10% of full price. Keep system prompt + tool schemas as a stable prefix. But cache hits do NOT reduce TPM rate-limit accounting.

4. **The Context Tax is quadratic** -- a 5-turn conversation costs 6,500 input tokens, not 500. Hybrid compaction (6 verbatim + summarize older + structured memory) is the production answer.

5. **Three-tier model routing saves ~60%** -- route FAQ/simple to economy (Haiku/Luna), standard to mid-tier (Sonnet/Sol), complex reasoning to frontier (Opus/Astra). Essential at scale.

6. **Persist user turn BEFORE inference** -- if the stream crashes, the user's question is never lost. This is the chat equivalent of write-ahead logging.

7. **Multi-device sync requires server-canonical conversation + pub/sub** -- plain SSE cannot resume mid-stream on a new device. The conversation store + channel pattern with sequence-based catch-up is the production answer.

8. **Prompt injection is architecturally unsolved** -- no single defense works. Reader/Doer separation (Layer 1) is the most effective single defense. The Lethal Trifecta (private data + untrusted content + external comms) identifies the structural vulnerability.

---

## Interview Q&A

**Q1: Walk me through the request path when a user sends a message on mobile and then opens their laptop mid-stream. What is durable vs. connection-scoped?**

A: The user turn is persisted to PostgreSQL BEFORE inference starts -- that is durable. The SSE token stream is connection-scoped and dies when the mobile connection drops. The token stream also publishes to a conversation channel via pub/sub. When the laptop opens, it authenticates, pulls conversation history from the database via `cur_max_message_id` (durable), then subscribes to the live channel. If the stream is still active, the laptop picks up remaining tokens from the channel. If the stream finished, the completed assistant turn is already in the database. Plain SSE alone cannot do this -- you need server-canonical conversation + channel fan-out.

**Q2: Compute the cost per 1K turns for GPT-4o with and without cached input. What does caching NOT buy you on rate limits?**

A: With 5K input + 1.4K output tokens per turn: uncached = 5000 x $2.50/1M + 1400 x $10/1M = $0.0125 + $0.014 = $0.0265/turn = $26.50/1K turns. With 100% cached input: 5000 x $1.25/1M + 1400 x $10/1M = $0.02025/turn = $20.25/1K turns. Critical: cache hits do NOT reduce TPM rate-limit accounting. TPM is enforced before cache lookup. So you can be paying 50% less while still hitting the same rate-limit ceiling. Capacity planning must use raw token counts, not post-cache billable amounts.

**Q3: Draw the circuit-breaker state machine and place it relative to a fallback chain.**

A: CLOSED (normal, counting failures) --> OPEN (fail-fast, cool-down timer) --> HALF_OPEN (one probe, success resets). The circuit breaker wraps the primary model endpoint. When the breaker is OPEN, the orchestrator skips the primary and immediately tries the next model in the fallback chain: primary (Sonnet) --> secondary (Sol) --> economy (Haiku) --> deterministic template refusal. Each endpoint in the chain can have its own independent circuit breaker. The chain terminates with a hard-coded template so the user always gets a response.

**Q4: Why is client message_id idempotency required on flaky mobile networks?**

A: Mobile networks frequently drop connections after the request is sent but before the response arrives. The client retries, sending the same message again. Without idempotency, the server processes it as a new turn -- double inference cost, duplicate assistant message in the conversation, and potential inconsistency. The server unique-constrains (conversation_id, message_id): first insert processes the turn, duplicate returns the existing result. This is the chat analogue of payment idempotency keys.

**Q5: Compare long context vs. compaction vs. memory retrieval for a 6-month support thread.**

A: Long context: sends everything, costs scale quadratically, model attention degrades in the middle ("lost in the middle"), will exceed even 200K windows. Compaction: server-side opaque compaction item (OpenAI's compact_threshold ~200K tokens), keeps recent turns verbatim, summarizes older context, ~40-60% token reduction. Memory retrieval: structured facts (preferences, decisions) extracted into a separate store, semantic search via vector DB for relevance-based retrieval, bounded cost. For a 6-month thread, you need all three: structured memory for durable facts, compaction for session continuity, and RAG for on-demand historical retrieval. Realtime voice windows (32K total) make this even more acute.

**Q6: Where do you put the input/output guard boundary when tools can move money?**

A: Input classifiers (distilled BERT, sub-ms) run BEFORE inference at the gateway layer, outside agent orchestration -- injection cannot disable them. Output moderation runs after full generation. For money-moving tools specifically: delay the irreversible side effect (actual fund transfer) until AFTER full output moderation passes. Tool invocations go through per-invocation security scrutiny (a separate service analyzing intent and destination). Money-moving tools require HITL confirmation -- the assistant presents the intended action and waits for explicit user approval. This follows the Rule of Two: when untrusted input meets sensitive data meets state change, require human authorization.

**Q7: Design Zero-Trust MCP for calendar send-email: authz checks, schema validation, credential custody, HITL.**

A: Five layers: (1) Authenticate the calling user session -- never pass provider API keys into model context. (2) Authorize: check that user's role has `send-email` in their tool allowlist (Tool RBAC, default deny). (3) Validate: JSON schema with `strict: true` -- reject hallucinated recipient IDs or malformed fields. (4) Isolate credentials: email API keys live in a vault/tool proxy, model sees only tool name and sanitized results, never credentials. (5) HITL: email is an irreversible external communication -- present draft to user, await explicit confirmation before sending. The 5-step flows through the per-invocation security scrutiny service running outside agent orchestration.

**Q8: Providers publish no TTFT p99 SLA. What do you put in your SLO doc and how do you mitigate p99?**

A: I set internal design targets, not vendor SLAs: TTFT p50 < 400ms, p95 < 500ms, p99 < 1s. These are measured client-side on first non-empty text delta (not message_start). To mitigate p99: (1) Prompt caching on stable prefix reduces prefill work. (2) Never buffer the SSE proxy -- `X-Accel-Buffering: no` and TransformStream tick flush. (3) Model routing sends FAQ/simple queries to mini models (~120ms TTFT). (4) Capacity headroom prevents queueing (p99 is dominated by queueing, KV allocation, and routing, not decode FLOPs). (5) When p99 exceeds threshold, shed to smaller model via circuit breaker.

**Q9: Multi-tenant copilot: how do FGA filters on RAG prevent cross-tenant prompt injection via retrieved tickets?**

A: Three defenses layer. First, per-tenant vector DB namespaces ensure queries only search within the requesting tenant's embedding space -- semantic search is approximate, so shared indexes can return wrong-tenant matches. Second, every vector is stamped with `tenant_id` and every query requires a mandatory filter on `tenant_id` -- even if namespace isolation fails, the metadata filter catches it. Third, FGA (Fine-Grained Authorization) checks ACL on each retrieved document before it enters the prompt context -- a retrieved ticket from a different department/role is filtered out even within the same tenant. This prevents injected content in one tenant's tickets from being retrieved and executed in another tenant's context.

**Q10: SSE buffer at a reverse proxy improves throughput metrics but users complain about slow responses. How do you diagnose?**

A: Measure TTFT at the client, not at the server. The server-side TTFT may look healthy because the model generated the first token quickly. But if the reverse proxy (Nginx, Cloudflare) is buffering SSE chunks, the client does not receive the first byte until the buffer flushes -- potentially seconds later. Diagnosis: compare server-side TTFT (first token generated) with client-side TTFT (first token rendered). If there is a large gap, proxy buffering is the cause. Fix: set `X-Accel-Buffering: no` on the response headers, use TransformStream with tick flush, and configure heartbeat cadence so the connection stays active.

**Q11: How does Claude's 50-token recovery threshold work, and what trade-off does it represent?**

A: If output broke off under ~50 tokens: the client silently discards the partial message and re-issues the request from scratch. The user never sees the half-rendered output. If more than ~50 tokens were streamed: partial output is inserted into conversation history as an assistant turn with a system note ("the previous reply was interrupted, please continue from where you left off"), and the stream resumes. This reduced perceived disconnect rate from ~30% to under 10%. The trade-off is between RPO (data loss risk) and latency overhead. Synchronous persistence per token batch gives near-zero RPO but adds 5-15ms per batch. Asynchronous persistence with the 50-token threshold accepts some mid-stream RPO for lower latency. Most production systems choose the asynchronous approach.

**Q12: What is the KV-cache side-channel attack and how do you defend against it in multi-tenant deployments?**

A: Demonstrated at NDSS 2025 on Llama2-13B/A100: if two tenants share a KV-cache prefix pool, an attacker can measure TTFT differences to infer whether their prompt shares a cached prefix with another tenant's prompt -- revealing information about what the other tenant is asking. The faster TTFT (cache hit) vs. slower TTFT (cache miss) becomes a timing side-channel. Defense: partition KV caches by tenant identity. Each tenant gets its own prefix cache namespace. This lowers cache hit rates (no cross-tenant prefix sharing) but eliminates the information leak. Security over efficiency is the right trade-off for enterprise multi-tenant platforms.

---

## Key Numbers to Memorize

| Metric | Value | Context |
|--------|-------|---------|
| Tokens per word (English) | ~1.3-1.5 | BPE tokenization; code and non-English are denser |
| TTFT target (p95) | < 500ms | Interactive streaming chat; measure on first TEXT delta |
| ITL target | < 30ms | Smooth reading experience |
| TPS target | > 30 tok/s | Interactive chat minimum |
| Heartbeat timeout | 30 seconds | SSE connection drop detection |
| HTTP timeout for LLM | 120-300s | NOT default 30-60s; long contexts need 30-60s for prefill alone |
| Cost per turn (GPT-4o, 5K in / 1.4K out) | $0.0265 uncached | $0.02025 with 100% cached input |
| Context tax (5-turn conv) | 6,500 input tokens total | Not 500 -- accumulated re-send |
| Prompt cache min prefix (OpenAI) | 1,024 tokens | GPT-5.6+ |
| Prompt cache RPM heuristic | ~15 RPM | Per (prefix + cache_key) before load-balancing causes misses |
| Compaction threshold (OpenAI) | ~200,000 tokens | Triggers opaque compaction item |
| Realtime voice window | 32,768 tokens | Max 4,096 output; instructions+tools capped at 16,384 |
| Claude recovery threshold | ~50 tokens | Below: silent retry; above: persist partial + continue |
| GPU nodes for 1M users | ~30x H100 | 18K TPS target, 150 req/sec peak |
| INT4 quantization impact | 1-4% perplexity increase | Acceptable for chat; FP8/INT8 < 2% |
| Prompt injection defense deployment | 34.7% of orgs | As of 2026 |
| GDPR fines (2025) | EUR 2.1 billion | 4% of annual global revenue or EUR 20M |
| Salesforce concurrent sessions | 7K+ | Multi-tenant AI agent platform reference |
| Response retention (OpenAI) | 30 days | `previous_response_id` chain; `store: false` disables |
| Model routing cost savings | ~60% | Three-tier routing vs single frontier model |

---

## Quick Reference

```
ARCHITECTURE:  CDN Edge --> API Gateway --> Orchestration --> GPU Inference --> SSE Stream
STATE:         LLMs are stateless. Reconstruct context every request from DB.
STREAMING:     SSE (not WebSocket). Do NOT buffer the proxy. Heartbeat 30s. Timeout 120-300s.
COMPACTION:    6 verbatim exchanges + summarize older + structured memory (lossless facts)
COST:          Context tax is quadratic. Prompt caching = biggest lever. Cache != TPM relief.
ROUTING:       Simple->Haiku/Luna | Standard->Sonnet/Sol | Complex->Opus/Astra (~60% savings)
DURABILITY:    Persist user turn BEFORE inference. Append-only. Server is source of truth.
MULTI-DEVICE:  Server-canonical conversation + pub/sub channel. Plain SSE cannot resume.
RESILIENCE:    Circuit breaker per dependency. Fallback: frontier->mid->mini->template.
IDEMPOTENCY:   Client message_id, unique constraint on (conv_id, msg_id). No duplicate inference.
INJECTION:     6-layer defense. Reader vs Doer (#1 defense). Lethal Trifecta = structural vuln.
MULTI-TENANT:  Cell-per-geography. RLS (not app filtering). Per-tenant vector namespace + KV-cache.
RECOVERY:      <50 tokens streamed: silent retry. >50: persist partial + continue note.
RATE LIMIT:    RPM + TPM + concurrent. Cache does NOT reduce TPM. Plan on raw tokens.
```
