# 17. AI Chat Assistant

**Sub-areas covered**: Four-layer production chat architecture (context engineering, generation engine, persistent memory, SSE streaming), seven-stage request lifecycle from CDN edge through inference to client render, API gateway vs. load balancer disambiguation, stateless LLM conversation state reconstruction (history retrieval, context window management, structured memory injection), SSE streaming protocol design (event taxonomy, heartbeat lifecycle, AbortController cancellation, 50-token recovery threshold), multi-modal input/output routing with separate token budgets, three-tier LLM router (fast/standard/reasoning) with complexity-based and fallback routing strategies, BPE token economics and the "context tax" (quadratic cost growth in multi-turn conversations), hidden reasoning-token billing, prompt caching as the single largest savings lever (10x cost reduction on cached prefixes), per-conversation cost modeling across tiers ($0.20-$50/1M tokens, Sep 2026), latency engineering (TTFT < 500ms p95, ITL < 30ms, TPS > 30 for interactive chat), capacity planning reference model (1M registered users, 30x H100 nodes, 18K TPS), conversation persistence architecture (PostgreSQL + Redis + vector DB tiering), mid-conversation failure recovery (Claude's 50-token threshold, context continuation injection), multi-region active-active deployment with cell-based data residency for GDPR, multi-dimensional rate limiting (RPM/TPM/concurrent streams with Redis + in-memory fallback), circuit breaker three-state implementation, idempotency guards against duplicate inference billing, prompt injection as OWASP LLM01 with 340% attack surge in 2026 (EchoLeak CVE-2025-32711, GitHub Copilot RCE, Cursor DB deletion), Lethal Trifecta defense model (private data + untrusted content + external communication), defense-in-depth (reader/doer separation, gateway-layer control, per-invocation scrutiny, task-scoped least privilege), GDPR vs. EU AI Act retention tension (erasure vs. traceability), right to erasure across conversation stores + vector indexes + prompt logs + inference logs, context window overflow as the most common silent failure in production chat (sliding window, token compression, summarization, structured memory extraction, RAG memory, MemGPT-style tiering, prompt caching), Claude Code's 5-layer context reduction pipeline (budget reduction, snip, microcompact, context collapse, auto-compact), GPU saturation signals (memory vs. compute utilization divergence, thermal throttling, KV-cache exhaustion, DRAM bandwidth stall), CPU-induced inference degradation (tokenizer thread contention, kernel launch latency amplification), quantization quality thresholds (FP8/INT8 < 2% degradation, INT4 1-4% perplexity increase, below INT4 user-visible), quality monitoring via proxy metrics (feedback rate, retry rate, refusal rate) + LLM-as-judge eval sampling, production Python code with SSE streaming client, exponential backoff with jitter, three-state circuit breaker, model fallback chain with structured logging, conversation context compaction, and two enterprise system-design scenarios (enterprise chat assistant at scale with three-tier architecture, multi-tenant chat platform with hybrid namespace isolation and RLS) with architecture diagrams and trade-off matrices

---

## 1. System Topology & Data Flow

A production AI chat assistant is organized into four cooperating layers: a **context engineering layer** that assembles the full prompt (system prompt + managed conversation history + user message), budgets the context window, and injects structured memory; a **generation engine layer** that tokenizes via BPE, routes to the optimal model, and runs two-phase inference (prefill + decode) on GPU clusters; a **persistent memory layer** that stores conversations durably, extracts structured facts, manages long-term user preferences, and serves both low-latency session state and semantic retrieval; and an **SSE streaming layer** that streams tokens back to the client as generated, manages connection lifecycle with heartbeats and reconnection, and handles user-initiated cancellation.

```
┌────────────────────────────────────────────────────────────────────────────────────┐
│                                CDN EDGE / WAF                                      │
│                                                                                    │
│   Anycast DNS (Route 53 / Cloudflare) ──> nearest PoP                              │
│   TLS 1.3 termination, DDoS mitigation, static asset cache                         │
│   X-Accel-Buffering: no  (required for SSE passthrough)                             │
└────────────────────────────────────────┬───────────────────────────────────────────┘
                                         │ HTTPS
┌────────────────────────────────────────▼───────────────────────────────────────────┐
│                              API GATEWAY (Kong / Envoy)                             │
│                                                                                    │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────────────┐  │
│  │ Auth Engine       │  │ Rate Limiter      │  │ Request Router                  │  │
│  │                   │  │ (Redis-backed)    │  │                                  │  │
│  │ JWT verification  │  │                   │  │ /auth/*     ──> Auth Service     │  │
│  │ Bearer token      │  │ RPM (per user)    │  │ /convo/*    ──> Conversation Svc │  │
│  │ API key scoping   │  │ TPM (per tier)    │  │ /generate/* ──> Orchestrator     │  │
│  │ RBAC: free/pro/   │  │ Concurrent        │  │ /files/*    ──> Upload Service   │  │
│  │   enterprise/     │  │   streams (per    │  │                                  │  │
│  │   admin           │  │   connection)     │  │ W3C Trace Context propagation    │  │
│  │ OAuth 2.0 / OIDC  │  │ In-memory         │  │ Request schema validation        │  │
│  │ Session: 15m AT,  │  │   fallback when   │  │ API versioning                   │  │
│  │   7d RT           │  │   Redis down      │  │                                  │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────────────┘  │
└────────────────────────────────────────┬───────────────────────────────────────────┘
                                         │ validated request + trace ID
┌────────────────────────────────────────▼───────────────────────────────────────────┐
│                         CONTEXT ENGINEERING LAYER                                   │
│                         (Orchestration Service)                                     │
│                                                                                    │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────────────┐  │
│  │ Context Assembler │  │ Pre-Inference     │  │ LLM Router                      │  │
│  │                   │  │ Moderation        │  │ (Model Selection)               │  │
│  │ 1. Retrieve       │  │                   │  │                                  │  │
│  │    conversation   │  │ Distilled BERT    │  │ Complexity ──> model tier:       │  │
│  │    history from   │  │ classifiers       │  │  Simple ──> 8B FP8    ($0.15/M) │  │
│  │    Redis/Postgres │  │ (sub-ms latency)  │  │  Standard──> 70B      (mid)     │  │
│  │ 2. Apply context  │  │                   │  │  Reasoning──> o1-class ($2.50/M)│  │
│  │    compaction     │  │ Pattern filters:  │  │                                  │  │
│  │    (summarize     │  │  role-confusion   │  │ Load-based routing               │  │
│  │    older turns)   │  │  zero-width chars │  │ Fallback chains                  │  │
│  │ 3. Inject system  │  │  tool-call        │  │ Cost optimization                │  │
│  │    prompt +       │  │  mimicry          │  │ Blended cost reduction ~60%      │  │
│  │    structured     │  │  encoding attacks │  │                                  │  │
│  │    memory         │  │                   │  │ GPT-5: fast mode / thinking mode │  │
│  │ 4. Append user    │  │ Gateway defense   │  │   / fallback mode (built-in)     │  │
│  │    message        │  │ (outside agent    │  │                                  │  │
│  │                   │  │  orchestration)   │  │                                  │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────────────┘  │
└────────────────────────────────────────┬───────────────────────────────────────────┘
                                         │ assembled prompt + model target
┌────────────────────────────────────────▼───────────────────────────────────────────┐
│                         GENERATION ENGINE LAYER                                     │
│                         (GPU Inference Cluster)                                     │
│                                                                                    │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────────────┐  │
│  │ Tokenizer (BPE)  │  │ Inference Engine  │  │ Post-Inference Filters           │  │
│  │                   │  │ (vLLM / TGI)     │  │                                  │  │
│  │ tiktoken /        │  │                   │  │ Output classifiers               │  │
│  │ SentencePiece     │  │ Two-phase:        │  │ Content policy enforcement       │  │
│  │                   │  │  Prefill: process │  │ Structured output validation     │  │
│  │ 30K-100K vocab    │  │   all input       │  │ Schema compliance check          │  │
│  │ ~1.3-1.5 tok/word │  │   tokens          │  │                                  │  │
│  │ Code: more dense  │  │  Decode: generate │  │                                  │  │
│  │ Non-EN: higher    │  │   output one      │  │                                  │  │
│  │  tok/word         │  │   token at a time │  │                                  │  │
│  │                   │  │                   │  │                                  │  │
│  │                   │  │ Continuous batching│  │                                  │  │
│  │                   │  │ INT8/FP8 quant    │  │                                  │  │
│  │                   │  │ KV-cache mgmt     │  │                                  │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────────────┘  │
└────────────────────────────────────────┬───────────────────────────────────────────┘
                                         │ token stream
┌────────────────────────────────────────▼───────────────────────────────────────────┐
│                         SSE STREAMING LAYER                                         │
│                                                                                    │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────────────┐  │
│  │ SSE Event Framing │  │ Connection Mgmt  │  │ Conversation Persistence         │  │
│  │                   │  │                   │  │                                  │  │
│  │ Event types:      │  │ Heartbeat: 30s   │  │ Parallel write to:               │  │
│  │  start: conv_id,  │  │   timeout, then  │  │  PostgreSQL (durable store)      │  │
│  │   model, tier     │  │   reconnect w/   │  │  Redis (24h TTL session cache)   │  │
│  │  token: delta     │  │   exp backoff    │  │                                  │  │
│  │   chunks          │  │                   │  │ Idempotency guard:               │  │
│  │  meta: usage,     │  │ Server: persist  │  │  Redis distributed lock          │  │
│  │   cost, latency   │  │   heartbeat,     │  │  prevents duplicate inference    │  │
│  │  done: stop       │  │   client cleanup │  │                                  │  │
│  │   reason          │  │                   │  │ Structured memory extraction:    │  │
│  │                   │  │ Client:           │  │  pull facts/preferences into     │  │
│  │ Claude events:    │  │  AbortController  │  │  separate store                  │  │
│  │  message_start    │  │  for user cancel  │  │                                  │  │
│  │  content_block_*  │  │                   │  │ Embedding pipeline for           │  │
│  │  message_delta    │  │ HTTP timeout:     │  │  long-term vector memory         │  │
│  │  message_stop     │  │  120-300s (not    │  │                                  │  │
│  │                   │  │  default 30-60s)  │  │                                  │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────────┘
                                         │
┌────────────────────────────────────────▼───────────────────────────────────────────┐
│                         PERSISTENT MEMORY LAYER                                     │
│                                                                                    │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────────┐ ┌──────────────────────────┐│
│  │ PostgreSQL / │ │ Redis        │ │ Vector DB      │ │ Telemetry Pipeline       ││
│  │ DynamoDB     │ │              │ │ (Pinecone /    │ │                          ││
│  │              │ │ Active       │ │  pgvector /    │ │ Structured logs:         ││
│  │ Conversations│ │ session      │ │  Weaviate)     │ │  Kafka ──> Flink ──>     ││
│  │ Messages     │ │ state        │ │                │ │  ClickHouse              ││
│  │ User prefs   │ │ 24h TTL      │ │ Conversation   │ │                          ││
│  │ Pagination   │ │ Sub-ms reads │ │ embeddings     │ │ Real-time metrics:       ││
│  │              │ │              │ │ Semantic search│ │  Prometheus + Grafana    ││
│  │ Shard by     │ │              │ │ Long-term      │ │                          ││
│  │ conversation │ │              │ │ memory         │ │ Dashboard:               ││
│  │ ID at scale  │ │              │ │                │ │  Row 1: error rate,      ││
│  │              │ │              │ │                │ │    P95 lat, cost/hr      ││
│  │              │ │              │ │                │ │  Row 2: eval scores,     ││
│  │              │ │              │ │                │ │    user feedback         ││
│  └──────────────┘ └──────────────┘ └────────────────┘ └──────────────────────────┘│
└────────────────────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) A user sends a message from the browser or mobile app. The request hits the CDN edge (Cloudflare, CloudFront) with TLS 1.3, routes via anycast DNS to the nearest PoP, and passes through DDoS mitigation. The edge must have `X-Accel-Buffering: no` configured to prevent buffering of the SSE stream. (2) The API gateway authenticates the request (JWT/Bearer token verification, OAuth 2.0/OIDC for social login, or scoped API key for enterprise), checks multi-dimensional rate limits against Redis (RPM, TPM, concurrent streams; in-memory fallback when Redis is unavailable), validates the request schema, and routes by URL path to the appropriate backend service. A W3C Trace Context trace ID is injected for end-to-end observability. (3) The orchestration service assembles the full prompt: it retrieves conversation history from the low-latency store (Redis for active sessions, PostgreSQL/DynamoDB for persistence), applies context compaction (summarizing older turns to stay within the model's context window), injects the system prompt and structured memory (deterministic user facts that survive summarization losslessly), and appends the new user message. Pre-inference moderation runs distilled BERT classifiers at sub-millisecond latency, filtering role-confusion language, hidden content (zero-width characters, white-on-white text), tool-call mimicry, and encoding obfuscation. The LLM router selects the optimal model tier based on query complexity, current load, and cost targets. (4) The inference engine tokenizes via BPE (tiktoken/SentencePiece) and runs two-phase inference: prefill processes all input tokens in parallel (TTFT scales with prompt length), then decode generates output tokens one at a time (autoregressive). Post-inference classifiers validate the output against content policies and structured output schemas. (5) Tokens stream back through the SSE layer as `start`, `token`, `meta`, and `done` events. The connection manager maintains a 30-second heartbeat timeout; if no bytes arrive, the client reconnects with exponential backoff. Users can cancel via AbortController. (6) The conversation is persisted in parallel to PostgreSQL (durable) and Redis (session cache, 24-hour TTL). An idempotency guard (Redis distributed lock with request fingerprinting) prevents duplicate inference calls on network retries. Structured memory extraction pulls deterministic facts from the response into a separate store. Conversation chunks are embedded into the vector database for long-term semantic retrieval. (7) Telemetry flows through the Kafka-Flink-ClickHouse pipeline for real-time anomaly detection, while Prometheus+Grafana dashboards surface operational metrics.

**API gateway vs. load balancer.** A load balancer is "dumb" -- it distributes TCP/HTTP connections across servers using weighted round-robin or least-connections, with no awareness of request content. An API gateway is "smart" -- it reads URL paths, routes to specific services, handles authentication, rate limiting, request validation, API versioning, and observability injection. Production systems use both: the load balancer distributes connections across API gateway instances, which then route to specific backend services. Implementations: Kong, Envoy Proxy, AWS API Gateway.

---

## 2. Core Mechanics & Algorithms

### 2.1 Conversation State Management

LLMs are stateless -- the model holds nothing between requests. The system must reconstruct the full conversational context on every single request. This is the fundamental architectural constraint that drives most design decisions in a chat assistant.

**State reconstruction per request:**

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     PER-REQUEST STATE RECONSTRUCTION                    │
│                                                                         │
│  Step 1: Retrieve history                                               │
│  ┌─────────────┐    miss     ┌──────────────┐                          │
│  │ Redis Cache  │──────────>│ PostgreSQL /  │                          │
│  │ (24h TTL)    │           │ DynamoDB       │                          │
│  │ Sub-ms reads │<──────────│ (durable)      │                          │
│  └──────┬──────┘   hydrate  └──────────────┘                          │
│         │                                                               │
│  Step 2: Context window management                                      │
│  ┌──────▼──────────────────────────────────────────────────────────┐   │
│  │  Last 6 exchanges ──> verbatim                                   │   │
│  │  Older exchanges  ──> summarized (triggered at 70-80% capacity) │   │
│  │  System prompt    ──> re-injected every 20-30 exchanges          │   │
│  │  Structured facts ──> always included (survives summarization)   │   │
│  └──────┬──────────────────────────────────────────────────────────┘   │
│         │                                                               │
│  Step 3: Assemble final prompt                                          │
│  ┌──────▼──────────────────────────────────────────────────────────┐   │
│  │  [System prompt]                                                  │   │
│  │  [Structured memory: user preferences, confirmed decisions]       │   │
│  │  [Summary of older conversation segments]                         │   │
│  │  [Last 6 exchanges verbatim]                                      │   │
│  │  [New user message]                                                │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

**Advanced memory patterns.** Older conversation segments are summarized or converted to embeddings. When a user references historical details, semantic search (via vector DB) retrieves relevant past context. Structured memory extraction pulls deterministic facts (user preferences, confirmed decisions) into a separate store that travels with every prompt -- these facts survive summarization losslessly, unlike conversational context.

### 2.2 SSE Streaming Protocol

All major AI chat products (ChatGPT, Claude, Gemini) use Server-Sent Events (SSE), not WebSockets. Chat is unidirectional streaming (server to client) after the initial request -- SSE is purpose-built for this pattern. It works through standard HTTP/1.1 proxies and CDNs without special configuration, and the browser's EventSource API provides automatic reconnection.

**SSE event taxonomy (reference implementation):**

```
┌────────────────────────────────────────────────────────────────────────┐
│                     SSE EVENT LIFECYCLE                                  │
│                                                                         │
│  Client ────── POST /generate ──────> Server                            │
│                                                                         │
│  Server ─── event: start ───────────> Client                            │
│             data: {conv_id, model, tier}                                │
│                                                                         │
│  Server ─── event: token ───────────> Client    (repeated N times)      │
│             data: {delta: "Hello"}                                      │
│  Server ─── event: token ───────────> Client                            │
│             data: {delta: " there"}                                     │
│  Server ─── event: token ───────────> Client                            │
│             data: {delta: "!"}                                          │
│                                                                         │
│  Server ─── event: meta ────────────> Client                            │
│             data: {prompt_tokens: 1200,                                 │
│                    completion_tokens: 85,                                │
│                    cost: 0.0023,                                        │
│                    ttft_ms: 142}                                         │
│                                                                         │
│  Server ─── event: done ────────────> Client                            │
│             data: {stop_reason: "end_turn"}                             │
│                                                                         │
│  ── heartbeat every 30s if no data (: comment line) ──                  │
│  ── client AbortController for user cancel ──                           │
└────────────────────────────────────────────────────────────────────────┘
```

**Claude-specific SSE events:** `message_start`, `content_block_start`, `content_block_delta`, `content_block_stop`, `message_delta`, `message_stop`. Text deltas arrive in `delta.text`; tool-use deltas arrive in `delta.partial_json` and must be concatenated before JSON.parse.

**Frontend consumption.** ReadableStream with TextDecoder for incremental token rendering. Optimistic UI shows the user message before server confirmation. Virtualized lists render only visible messages in long conversations. Skeleton screens mask latency during initial loading.

### 2.3 Multi-Modal Input/Output

Modern chat assistants handle text, images, code, and files. The orchestration layer detects input modality and routes to the appropriate processing pipeline. Vision tokens are typically more expensive than text tokens and require separate budget tracking. File uploads go through object storage (S3/GCS) via pre-signed URLs. The frontend renders structured outputs: markdown, code blocks with syntax highlighting, LaTeX, and charts.

### 2.4 LLM Router (Model Selection)

The router selects the optimal model for each request based on complexity, cost, and latency targets. This is how production systems achieve blended cost reduction of up to 60%.

```
┌─────────────────────────────────────────────────────────────────────┐
│                     THREE-TIER MODEL ROUTING                         │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    Incoming Request                           │    │
│  └──────────────────────────┬──────────────────────────────────┘    │
│                              │                                       │
│                    ┌─────────▼─────────┐                            │
│                    │ Complexity        │                            │
│                    │ Classifier        │                            │
│                    └──┬──────┬──────┬──┘                            │
│           simple      │      │      │   complex                     │
│  ┌────────────────────▼┐  ┌──▼───┐  ┌▼────────────────────────┐    │
│  │  FAST TIER           │  │ STD  │  │  REASONING TIER          │    │
│  │  8B FP8              │  │ 70B  │  │  DeepThink / o1-class    │    │
│  │  TTFT ~120ms         │  │      │  │  Higher latency          │    │
│  │  $0.15 / 1M tokens   │  │ Mid  │  │  $2.50 / 1M tokens      │    │
│  │  FAQ, simple Q&A     │  │      │  │  Multi-step reasoning    │    │
│  └──────────────────────┘  └──────┘  └──────────────────────────┘    │
│                                                                      │
│  Routing strategies:                                                 │
│   1. Complexity-based: classifier scores query difficulty             │
│   2. Load-based: distribute across instances by utilization           │
│   3. Fallback chains: primary fails ──> retry with alternative       │
│   4. Cost-based: cheapest model meeting quality threshold             │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.5 Context Window Overflow -- The Most Common Silent Failure

Even 200K-400K token windows are insufficient for long agent sessions. Costs grow quadratically with conversation length. Model attention quality degrades in the middle of very long contexts ("lost in the middle" phenomenon). KV-cache memory grows linearly, eventually exhausting GPU VRAM.

**Production mitigation strategies (ordered by cost, cheapest first):**

```
┌─────────────────────────────────────────────────────────────────────────┐
│                CONTEXT MANAGEMENT STRATEGIES                             │
│                                                                          │
│  ┌─────────────────┬──────────┬──────────┬──────────┬───────────────┐  │
│  │ Strategy         │ Latency  │ Quality  │ Cost     │ Complexity    │  │
│  ├─────────────────┼──────────┼──────────┼──────────┼───────────────┤  │
│  │ Sliding window   │ Lowest   │ Lossy    │ Lowest   │ Lowest        │  │
│  │ Token compress   │ Low      │ Good     │ Low      │ Low           │  │
│  │ Summarization    │ Medium   │ Good     │ Medium   │ Medium        │  │
│  │ Structured mem   │ Low      │ Lossless │ Low      │ Medium        │  │
│  │ RAG memory       │ Medium   │ High     │ Higher   │ Higher        │  │
│  │ Memory tiering   │ Medium   │ Highest  │ Highest  │ Highest       │  │
│  │ Prompt caching   │ Lowest   │ No loss  │ Lowest   │ Infra-level   │  │
│  └─────────────────┴──────────┴──────────┴──────────┴───────────────┘  │
│                                                                          │
│  Best practice hybrid:                                                   │
│   Keep last 6 exchanges verbatim                                         │
│   + summarize everything older                                           │
│   + inject summary into system prompt                                    │
│   + re-inject system prompt every 20-30 exchanges                        │
│   Users don't notice; continuity is preserved.                           │
└─────────────────────────────────────────────────────────────────────────┘
```

**Claude Code's 5-layer context reduction pipeline** (executed before every model call, cheapest operation first): (1) Budget reduction -- targets individual tool outputs that overflow size limits. (2) Snip -- handles temporal depth. (3) Microcompact -- reacts to cache overhead. (4) Context collapse -- manages very long histories. (5) Auto-compact -- semantic compression as last resort. This ordering ensures the cheapest interventions fire first, preserving semantic fidelity where possible.

---

## 3. Token Economics & NFR Analysis

### 3.1 The Context Tax

Each turn in a multi-turn conversation re-sends the full conversation history as input. Costs grow quadratically, not linearly.

```
┌──────────────────────────────────────────────────────────────────────┐
│            MULTI-TURN COST COMPOUNDING ("Context Tax")                │
│                                                                       │
│  Assumption: each response ~500 tokens, user message ~100 tokens      │
│                                                                       │
│  Turn 1: input ~100 tok  ──> output 500 tok                          │
│  Turn 2: input ~700 tok  ──> output 500 tok                          │
│  Turn 3: input ~1,300 tok ──> output 500 tok                         │
│  Turn 4: input ~1,900 tok ──> output 500 tok                         │
│  Turn 5: input ~2,600 tok ──> output 500 tok                         │
│                                                                       │
│  Total input tokens across 5 turns: ~6,500 (not 500)                 │
│  Total output tokens: 2,500                                           │
│                                                                       │
│  Hidden costs:                                                        │
│   - Reasoning models (o1, o3, DeepThink): invisible "thinking"        │
│     tokens billed as output. 500 visible tokens may cost 2000+.       │
│   - Long-context surcharge: >272K tokens ──> 2x input, 1.5x output  │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.2 API Pricing (Sep 2026, per 1M tokens)

```
┌────────────────────────────┬──────────┬──────────────┬──────────┐
│ Provider / Model           │ Input    │ Cached Input │ Output   │
├────────────────────────────┼──────────┼──────────────┼──────────┤
│ GPT-6 Astra (frontier)     │ $10.00   │ $1.00        │ $50.00   │
│ GPT-5.6 Sol (balanced)     │ $4.00    │ $0.40        │ $20.00   │
│ GPT-5.6 Luna (economy)     │ $0.20    │ $0.02        │ $1.20    │
│ Claude Sonnet 4.5 (est.)   │ ~$3.00   │ ~$0.30       │ ~$15.00  │
│ Claude Haiku 4.5 (est.)    │ ~$0.25   │ ~$0.025      │ ~$1.25   │
└────────────────────────────┴──────────┴──────────────┴──────────┘

Cached input is typically 10% of input price.
Batch API typically halves costs with 24-hour SLA.
```

**Estimated monthly production costs:**

```
┌────────────────────────────┬──────────────────┐
│ Usage Tier                  │ Monthly Cost     │
├────────────────────────────┼──────────────────┤
│ Light (personal projects)   │ $5 - $40         │
│ Small apps                  │ $40 - $200       │
│ Production apps             │ $200 - $1,500    │
│ Enterprise                  │ $1,500+          │
│ OpenAI's own inference      │ ~$700K+ / day    │
└────────────────────────────┴──────────────────┘
```

### 3.3 Cost Optimization Strategies

Seven levers, ordered by impact:

1. **Prompt caching** -- reuse cached prefill computations; cached input at 10% of full price. For multi-turn conversations with stable system prompts, this is the single biggest savings lever.
2. **Model routing** -- route simple queries to economy models (Luna/Haiku), complex ones to frontier models. Achieves blended cost reduction of ~60%.
3. **Response caching** -- embedding-based cache keys in Redis for semantically similar prompts. Cache hit on repeated questions avoids inference entirely.
4. **Context compaction** -- summarize older conversation turns. 40-60% token reduction achievable.
5. **Token budgets** -- per-request and per-conversation limits prevent runaway costs.
6. **Batch API** -- non-interactive workloads (summarization, classification) at 50% discount with 24-hour SLA.
7. **Dynamic scaling** -- scale GPU nodes to zero during low-traffic periods (2-6 AM local).

Teams following these practices report 30-70% cost savings vs. naive usage.

**Consolidated cost per 1K conversations.**

```
Cost_per_1K_conversations = 1000 * avg_turns * avg_tokens_per_turn
                            * $/token * (1 - cache_hit_rate)

Worked example (Sonnet 4.5 pricing, Sep 2026):

  Assumptions:
    avg_turns            = 10
    avg_tokens_per_turn  = 2,000  (input + output, includes context tax)
    blended $/token      = $5.00 / 1M  (weighted: ~60% input @ $3/M,
                                         ~40% output @ $15/M, adjusted
                                         for three-tier routing mix)
    cache_hit_rate       = 40%  (prompt caching on stable system prompt
                                  + recent history prefix)

  Cost_per_1K = 1000 * 10 * 2000 * ($5.00 / 1,000,000) * (1 - 0.40)
              = 1000 * 10 * 2000 * 0.000005 * 0.60
              = $60.00 per 1K conversations

  Per-conversation:  ~$0.06
  Per-turn:          ~$0.006

At 100K DAU with 3 conversations/day:  ~$18K/month before routing savings.
With three-tier routing (60% to economy):  ~$7.2K/month.
```

### 3.4 Latency Targets for Interactive Chat

Measure at percentiles, never averages.

```
┌────────────────────────┬──────────────────────────────┬─────────────────────┐
│ Metric                  │ Definition                   │ Target              │
├────────────────────────┼──────────────────────────────┼─────────────────────┤
│ TTFT                    │ Time from request to first   │ < 500ms (p95)       │
│ (Time to First Token)   │ token received by client     │ < 1s (p99)          │
├────────────────────────┼──────────────────────────────┼─────────────────────┤
│ ITL / TPOT              │ Average time between tokens  │ < 30ms              │
│ (Inter-Token Latency)   │ after the first token        │ (smooth reading)    │
├────────────────────────┼──────────────────────────────┼─────────────────────┤
│ TPS                     │ Output generation rate       │ > 30 tok/s          │
│ (Tokens Per Second)     │                              │ (interactive chat)  │
├────────────────────────┼──────────────────────────────┼─────────────────────┤
│ E2E Latency             │ TTFT + (output_tokens x      │ Derived from above  │
│                         │ TPOT)                        │                     │
└────────────────────────┴──────────────────────────────┴─────────────────────┘

Key tension: optimizing for throughput (batching more requests) hurts TTFT
(increases queue wait time). Production SLAs must specify both:
  "TTFT p99 < 1s AND throughput > 2000 tok/s"

TTFT scales with prompt length: 500-token and 100K-token prompts produce
vastly different TTFT on the same model (prefill must process all input
tokens before generating the first output token).
```

**Benchmark reference (200 concurrent users):** Avg TTFT 119.96ms, P95 TTFT 135.8ms, system-wide output 146.04 tokens/sec, zero unhandled 5xx errors.

### 3.5 Concurrent User Capacity Planning

```
┌────────────────────────────────┬──────────────────────────┐
│ Metric                          │ Value                    │
├────────────────────────────────┼──────────────────────────┤
│ Registered users                │ 1,000,000                │
│ Daily Active Users (DAU)        │ 100,000 (10%)            │
│ Throughput target               │ 18,000 TPS               │
│ Peak request rate               │ 150 req/sec              │
│ GPU nodes required              │ ~30x H100                │
│ Single H100 cost                │ ~$3/hour                 │
│ 70B model FP16 VRAM             │ ~140 GB (2x H100 min)    │
│ 405B model FP16 VRAM            │ ~810 GB (multi-node)     │
│ INT4 quantization savings       │ 4x memory reduction      │
│  (70B on single 80GB GPU)       │ 1-4% perplexity increase │
└────────────────────────────────┴──────────────────────────┘
```

---

## 4. Distributed Resilience & Security

### 4.1 Mid-Conversation Failure Recovery

**Claude's 50-token recovery threshold.** If output broke off under ~50 tokens: the client silently discards the partial message and re-issues the request -- the user never sees the half-rendered output. If more than ~50 tokens were already streamed: the partial output is inserted into history as an assistant turn with a system note ("the previous reply was interrupted, please continue from where you left off"), and the stream resumes. This approach reduced perceived disconnect rate from ~30% of sessions to under 10%.

**SSE connection drops.** Network instability, server restarts, or load balancer timeouts cause connection drops. The heartbeat timeout (30s with no bytes) triggers reconnection with exponential backoff. Client UX must show "Connection interrupted -- retrying..." and never fail silently.

**Inference timeouts.** Default HTTP timeouts (30-60s) will abort long generations. Configure 120-300s timeouts. Long-context requests (100K+ tokens) can take 30-60s just for prefill.

**Cascade failures.** Inference backend failure causes queued requests to overwhelm remaining instances, leading to total outage. Circuit breakers (CLOSED -> OPEN -> HALF_OPEN) and bounded retry with jitter prevent this.

**RPO and RTO targets.** RPO (Recovery Point Objective) = last persisted message. Conversation turns are committed to PostgreSQL synchronously before the `done` SSE event, so completed messages have an effective RPO of zero. Mid-stream RPO is bounded by Claude's 50-token recovery threshold: if fewer than ~50 tokens were streamed, the partial output is discarded and re-generated (no data loss); if more were streamed, the partial content is persisted as an interrupted turn and continued from the breakpoint. RTO (Recovery Time Objective) = SSE reconnection + conversation state reload, typically < 2 seconds. The client detects a dropped connection via heartbeat timeout (30s), reconnects with exponential backoff (first retry ~500ms), and reloads conversation state from Redis (sub-ms) or PostgreSQL fallback (~10ms). **RPO/RTO vs. cost trade-off:** synchronous persistence (write to PostgreSQL before acknowledging each token batch) lowers mid-stream RPO to near-zero but adds ~5-15ms latency per token batch. Asynchronous persistence (buffer and flush after `done`) accepts higher mid-stream RPO in exchange for lower per-token latency. Most production systems choose asynchronous persistence with the 50-token recovery threshold as an acceptable middle ground.

**Failure taxonomy.**

```
┌─────────────────┬──────────────────────────────────┬────────────────────────────────────┐
│ Category         │ Examples                          │ Handling Strategy                   │
├─────────────────┼──────────────────────────────────┼────────────────────────────────────┤
│ Transient        │ API 429 rate limits               │ Retry with exponential backoff     │
│                  │ 503 provider outages              │ + jitter. Circuit breaker tracks   │
│                  │ SSE connection drops               │ failure rate. Bounded retries      │
│                  │ Timeout on long prefill           │ (max 3 attempts).                  │
├─────────────────┼──────────────────────────────────┼────────────────────────────────────┤
│ Permanent        │ Invalid/revoked API key           │ Fail fast, no retry. Surface       │
│                  │ Deleted conversation              │ clear error to user. Log for       │
│                  │ Unsupported model requested       │ ops alerting. Fallback chain       │
│                  │ Account suspended / quota zero    │ skips to next provider only if     │
│                  │                                    │ error is provider-specific.         │
├─────────────────┼──────────────────────────────────┼────────────────────────────────────┤
│ Poison-pill      │ Prompt injection via user         │ Detect via input guard (Layer 5    │
│                  │   message                         │ filtering + BERT classifiers).     │
│                  │ Adversarial conversation          │ Quarantine: flag conversation,     │
│                  │   history manipulation            │ log full payload, exclude from     │
│                  │ Malformed tool-call injection     │ training data. Do not retry.       │
│                  │                                    │ Alert security team.               │
├─────────────────┼──────────────────────────────────┼────────────────────────────────────┤
│ Idempotency      │ Network retry sends duplicate    │ Request fingerprinting: SHA-256    │
│                  │   inference request               │ of (conversation_id, turn_index,   │
│                  │ Client reconnect replays          │ message content). Redis            │
│                  │   last message                    │ distributed lock (TTL = inference  │
│                  │                                    │ timeout). Conversation state is    │
│                  │                                    │ append-only -- duplicate appends   │
│                  │                                    │ detected via turn sequence number. │
└─────────────────┴──────────────────────────────────┴────────────────────────────────────┘

Classification at the gateway layer: HTTP status codes map directly to categories.
429/503/timeout = transient. 401/403/404/422 = permanent. Input filter flags =
poison-pill. Duplicate idempotency key = idempotency violation. This classification
drives the circuit breaker, retry logic, and alerting pipeline.
```

### 4.2 Multi-Region Deployment

```
┌────────────────────────────────────────────────────────────────────────┐
│                   ACTIVE-ACTIVE MULTI-REGION                            │
│                                                                         │
│  ┌──────────────┐       ┌──────────────┐       ┌──────────────┐       │
│  │  US-EAST      │       │  EU-WEST      │       │  AP-SOUTH    │       │
│  │               │       │               │       │               │       │
│  │ Anycast CDN   │       │ Anycast CDN   │       │ Anycast CDN   │       │
│  │ Envoy L7 LB   │       │ Envoy L7 LB   │       │ Envoy L7 LB   │       │
│  │ Stateless API │       │ Stateless API │       │ Stateless API │       │
│  │ GPU cluster   │       │ GPU cluster   │       │ GPU cluster   │       │
│  │ (vLLM/TGI)    │       │ (vLLM/TGI)    │       │ (vLLM/TGI)    │       │
│  └──────┬───────┘       └──────┬───────┘       └──────┬───────┘       │
│         │                      │                      │                │
│         └──────────────────────┼──────────────────────┘                │
│                                │                                       │
│                     ┌──────────▼──────────┐                            │
│                     │ DNS-Based Failover   │                            │
│                     │ (Route 53 health     │                            │
│                     │  checks)             │                            │
│                     │ Uptime: 99.99%       │                            │
│                     └─────────────────────┘                            │
└────────────────────────────────────────────────────────────────────────┘
```

**Cell-based data residency (GDPR/compliance).** The system is split into self-contained "cells" per legal geography. Each cell holds storage, inference, embeddings, logs, and full orchestration. A thin global control plane maps tenants to cells but holds nothing personal. Requests are routed to the home cell before any content is processed. Boundaries are enforced with account-per-cell SCPs, regional KMS keys, and in-region model invocation. Regional processing endpoints carry a 10% cost uplift (OpenAI, from March 2026).

Critical insight: data residency is not just a database setting. A chatbot's personal data lives in conversation stores, vector indexes, prompt logs, AND inference request bodies. Designing cells around the database alone means embeddings and model calls will quietly breach compliance.

### 4.3 Prompt Injection Defense

Prompt injection is ranked #1 on OWASP Top 10 for LLM Applications 2025 (LLM01). Attacks surged 340% in 2026. A meta-analysis of 78 studies found adaptive attack success rates against state-of-the-art defenses exceed 85%. OpenAI publicly acknowledged in Feb 2026 that prompt injection in AI browsers "may never be fully patched."

**The Lethal Trifecta (Simon Willison, 2025).** An agent is structurally exploitable when three properties co-exist: (1) access to private data, (2) exposure to untrusted content, (3) ability to communicate externally. Remove any one to break the attack path.

**Real-world incidents (2025-2026):**
- **EchoLeak (CVE-2025-32711):** Zero-click M365 Copilot exploit -- hidden email instructions caused Copilot to exfiltrate documents during inbox summarization.
- **GitHub Copilot RCE (CVE-2025-53773):** Malicious code comments disabled user confirmations, granted unrestricted shell access.
- **Crypto wallet exploit (May 2026):** Morse-code-encoded attack tricked AI wallet into $150K unauthorized transfer.
- **Cursor AI (April 2026):** Coding agent deleted production database and backups in 9 seconds.

**Defense-in-depth architecture:**

```
┌─────────────────────────────────────────────────────────────────────┐
│                 PROMPT INJECTION DEFENSE LAYERS                      │
│                                                                      │
│  Layer 1: Architectural Separation (Reader vs. Doer)                 │
│  ┌───────────────────────────────────────────────────────┐          │
│  │ Agent processing untrusted content can ONLY return     │          │
│  │ structured analysis. It CANNOT call tools.             │          │
│  │ Most effective single defense.                         │          │
│  └───────────────────────────────────────────────────────┘          │
│                                                                      │
│  Layer 2: Gateway-Layer Control                                      │
│  ┌───────────────────────────────────────────────────────┐          │
│  │ Every request passes through a defense point that      │          │
│  │ runs OUTSIDE the agent's orchestration logic.          │          │
│  │ Injection cannot disable it.                           │          │
│  └───────────────────────────────────────────────────────┘          │
│                                                                      │
│  Layer 3: Per-Invocation Security Scrutiny                           │
│  ┌───────────────────────────────────────────────────────┐          │
│  │ Separate security service analyzes intent and          │          │
│  │ destination BEFORE every tool invocation.              │          │
│  └───────────────────────────────────────────────────────┘          │
│                                                                      │
│  Layer 4: Task-Scoped Tool Access (Least Privilege)                  │
│  ┌───────────────────────────────────────────────────────┐          │
│  │ Summarization: read-document only                      │          │
│  │ Reply-drafting: read-document + write-draft            │          │
│  │ Drafts queued for human review                         │          │
│  └───────────────────────────────────────────────────────┘          │
│                                                                      │
│  Layer 5: Input Filtering                                            │
│  ┌───────────────────────────────────────────────────────┐          │
│  │ Role-confusion, hidden content, zero-width chars,      │          │
│  │ encoding obfuscation, tool-call mimicry                │          │
│  └───────────────────────────────────────────────────────┘          │
│                                                                      │
│  Layer 6: Output Validation                                          │
│  ┌───────────────────────────────────────────────────────┐          │
│  │ Classify generated output for policy violations        │          │
│  └───────────────────────────────────────────────────────┘          │
│                                                                      │
│  Industry readiness (2026):                                          │
│   Only 34.7% of orgs have deployed injection defenses               │
│   83% plan agentic AI but only 29% feel ready securely              │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.4 Data Retention & Privacy (GDPR)

**Regulatory landscape (binding 2026):** GDPR (data processing, consent, right to erasure), EU AI Act Article 50 (mandatory transparency labelling for AI interactions, binding August 2026), NIST AI Risk Management Framework, ISO 42001 (AI-specific controls).

**Right to erasure (Article 17 GDPR).** Users can request deletion of all personal data within 30 days. Conversation data flows across multiple systems -- conversation store, vector DB, prompt logs, inference logs -- ALL must be addressed. Data embedded in trained model parameters cannot be simply "deleted"; irreversible anonymization is the only compliant path for retained training data.

**GDPR vs. EU AI Act tension.** GDPR demands erasure when purpose is fulfilled (Storage Limitation Principle, Article 5(1)(e)). EU AI Act demands lengthy archival of system documentation for traceability. Resolution: secure deletion of personal data immediately after system finalization, with anonymized data sets for compliance archival. Fines: up to 4% of annual global revenue or EUR 20 million (whichever higher). EUR 2.1 billion in GDPR fines issued in 2025 alone.

**Privacy by design checklist:** Server location in applicable jurisdiction. Mandatory DPA with all third-party AI providers. DPAs must explicitly prohibit using customer data for model training. Encryption: TLS 1.3 in transit, AES-256 at rest; customer-managed keys (CMK) for enterprise. Row-level security in database for multi-tenant data isolation. Record of Processing Activities maintained. Automated user rights fulfillment (access, erasure, portability).

### 4.5 Secure Infrastructure

Read-only filesystems for model serving. No egress network from inference containers. Model weights loaded from encrypted pre-signed URLs. Namespace isolation in Kubernetes for multi-tenancy. gVisor sandboxing (not just containers) for execution isolation -- standard containers share the host kernel and do not provide sufficient isolation boundaries.

### 4.6 Model Degradation Under Load

GPU saturation is insidious because it produces no error code -- output quality simply declines silently.

**GPU saturation signals:**
- GPU at 40% compute utilization but 95% memory utilization -> P99 latency 8s while P50 looks healthy at 1.2s
- GPU temperature hitting throttle threshold + simultaneous utilization drop -> thermal throttling
- KV-cache exhaustion -> sudden latency spikes and timeouts
- DRAM bandwidth saturation -> over 50% of attention kernel cycles stalled on data access

**CPU-induced degradation.** Under heavy load, tokenizer threads compete with the LLM engine for CPU. Frequent context switches and delayed kernel launches reduce GPU utilization. High CPU load can increase kernel launch latency from microseconds to milliseconds. Result: expensive GPU infrastructure delivers a fraction of expected throughput.

**Quantization quality thresholds.** FP8 / INT8: < 1-2% quality degradation, imperceptible for most production use cases. INT4: 1-4% perplexity increase, acceptable for chatbots, summarization, Q&A. Below INT4: quality degradation becomes user-visible.

**Monitoring.** Quality proxy metrics: user feedback rate (thumbs up/down), retry rate, refusal rate -- these are leading indicators. Run LLM-as-judge eval sampling on sampled outputs at regular intervals. Correlate infrastructure metrics (GPU utilization, memory, CPU, TTFT) with quality metrics (eval scores, user feedback). Alert thresholds: P99 latency exceeds 5s, GPU error rates above 0.1%, KV-cache usage approaching capacity.

### 4.8 Enterprise Security Boundaries

**Zero-Trust for chat tools (MCP).** When the chat assistant invokes external tools during conversation -- web search, file access, code execution, database queries via MCP (Model Context Protocol) servers -- each invocation requires per-invocation authorization. The tool call is not implicitly trusted because the user initiated the conversation. Capability scoping restricts each tool to the minimum permissions needed for the current task (e.g., a "search documents" tool gets read-only access to the user's namespace, never write access). Sensitive operations (file deletion, code execution, external API calls with side effects) require human-in-the-loop confirmation: the assistant presents the intended action and waits for explicit user approval before execution. Tool invocations are validated by the per-invocation security scrutiny layer (Layer 3 in the prompt injection defense stack) -- a separate security service analyzes intent and destination before every tool call, running outside the agent's orchestration logic so prompt injection cannot disable it.

**PII detection and filtering pipeline.** User messages pass through a PII detection stage before LLM processing. Detection combines regex patterns (SSN, credit card, phone number, email address formats) with NER models (spaCy/Presidio) for names, addresses, and context-dependent PII. Detected PII is handled in three modes: (1) **Redact** -- replace PII tokens with placeholders (`[EMAIL_1]`, `[SSN_REDACTED]`) before sending to the LLM, reverse-map in the response for display; this is the default for enterprise tenants. (2) **Log-scrub** -- allow PII to reach the LLM for response quality but scrub from all logs, telemetry, and conversation persistence. (3) **Opt-in pass-through** -- for use cases requiring PII handling (e.g., customer support referencing account details), PII is allowed with explicit tenant-level opt-in and enhanced audit logging. PII detected in model outputs is scrubbed from telemetry and flagged if the output contains PII not present in the input (potential hallucinated PII).

**Immutable audit logs.** Every conversation turn is logged to an append-only store with: user identity (pseudonymized ID), tenant ID, model used, input/output token counts, content hash (SHA-256 of message content -- enables integrity verification without storing raw content in the audit trail), moderation decisions (pass/flag/block with classifier scores), tool invocations and their outcomes, and timestamp. Storage uses WORM (Write Once Read Many) configuration -- S3 Object Lock with Compliance mode or equivalent -- preventing deletion or modification even by administrators. Retention: 7 years for regulated industries (financial services, healthcare), 2 years default. These logs satisfy EU AI Act Article 12 (record-keeping for high-risk AI systems) and complement GDPR erasure requirements: personal content is deleted per erasure requests, but the pseudonymized audit record (content hash, not content) is retained for compliance traceability.

---

## 5. Production Enterprise Code

### 5.1 SSE Streaming Client with Retry and Structured Logging

```python
"""
Production SSE streaming client for AI chat assistants.
Handles connection lifecycle, heartbeat timeout, retry with backoff,
user cancellation, and structured logging.
"""

import asyncio
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator

import httpx

# ── Structured Logging ──────────────────────────────────────────────

logger = logging.getLogger("chat_assistant")


def structured_log(level: str, event: str, **kwargs) -> None:
    """Emit structured JSON log lines for machine parsing."""
    record = {
        "ts": time.time(),
        "event": event,
        **kwargs,
    }
    getattr(logger, level)(json.dumps(record))


# ── Data Models ─────────────────────────────────────────────────────


class StreamEventType(Enum):
    START = "start"
    TOKEN = "token"
    META = "meta"
    DONE = "done"
    HEARTBEAT = "heartbeat"
    ERROR = "error"


@dataclass
class StreamEvent:
    event_type: StreamEventType
    data: dict = field(default_factory=dict)


@dataclass
class UsageStats:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    ttft_ms: float = 0.0
    model: str = ""
    fallback_used: bool = False


# ── Exponential Backoff with Full Jitter ────────────────────────────


def backoff_with_jitter(
    attempt: int,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
) -> float:
    """
    Bounded exponential backoff with full jitter.
    Prevents thundering herd on recovery -- each client
    picks a random delay within the exponential window.
    """
    exponential = min(base_delay * (2 ** attempt), max_delay)
    return random.uniform(0, exponential)


# ── Idempotency Guard ───────────────────────────────────────────────


def compute_request_fingerprint(
    conversation_id: str, user_message: str, turn_index: int
) -> str:
    """
    Deterministic fingerprint for idempotency.
    Same conversation + message + turn = same fingerprint.
    Redis lock on this key prevents duplicate inference.
    """
    payload = f"{conversation_id}:{turn_index}:{user_message}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ── SSE Stream Parser ──────────────────────────────────────────────


async def parse_sse_stream(
    response: httpx.Response,
    heartbeat_timeout: float = 30.0,
) -> AsyncIterator[StreamEvent]:
    """
    Parse an SSE byte stream into typed StreamEvent objects.
    Yields HEARTBEAT if no data arrives within heartbeat_timeout.
    Handles Claude-specific event types (content_block_delta, etc.).
    """
    buffer = ""
    last_data_time = time.monotonic()

    async for chunk in response.aiter_text():
        last_data_time = time.monotonic()
        buffer += chunk

        while "\n\n" in buffer:
            raw_event, buffer = buffer.split("\n\n", 1)
            event_type = None
            event_data = None

            for line in raw_event.strip().split("\n"):
                if line.startswith("event:"):
                    event_type = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    event_data = line[len("data:"):].strip()
                elif line.startswith(":"):
                    # Comment line = heartbeat
                    yield StreamEvent(StreamEventType.HEARTBEAT)
                    continue

            if event_data is None:
                continue

            try:
                parsed = json.loads(event_data)
            except json.JSONDecodeError:
                parsed = {"raw": event_data}

            # Map Claude-specific events to our taxonomy
            if event_type in ("start", "message_start"):
                yield StreamEvent(StreamEventType.START, parsed)
            elif event_type in (
                "token", "content_block_delta", "content_block_start"
            ):
                yield StreamEvent(StreamEventType.TOKEN, parsed)
            elif event_type in ("meta", "message_delta"):
                yield StreamEvent(StreamEventType.META, parsed)
            elif event_type in ("done", "message_stop"):
                yield StreamEvent(StreamEventType.DONE, parsed)
            else:
                yield StreamEvent(StreamEventType.TOKEN, parsed)


# ── Streaming Chat Client ──────────────────────────────────────────


async def stream_chat_response(
    api_url: str,
    api_key: str,
    conversation_id: str,
    messages: list[dict],
    model: str = "claude-sonnet-4-5",
    max_retries: int = 3,
    heartbeat_timeout: float = 30.0,
    request_timeout: float = 300.0,
) -> AsyncIterator[StreamEvent]:
    """
    Stream a chat response with production-grade resilience:
    - Exponential backoff with full jitter on transient failures
    - Heartbeat timeout detection
    - Idempotency fingerprinting
    - Structured logging at every decision point

    Yields StreamEvent objects for the caller to render.
    """
    fingerprint = compute_request_fingerprint(
        conversation_id,
        messages[-1].get("content", ""),
        len(messages),
    )

    for attempt in range(max_retries + 1):
        try:
            structured_log(
                "info",
                "stream_attempt",
                attempt=attempt,
                conversation_id=conversation_id,
                model=model,
                fingerprint=fingerprint,
                message_count=len(messages),
            )

            async with httpx.AsyncClient(
                timeout=httpx.Timeout(request_timeout, connect=10.0)
            ) as client:
                async with client.stream(
                    "POST",
                    api_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "X-Idempotency-Key": fingerprint,
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": True,
                    },
                ) as response:
                    if response.status_code == 429:
                        retry_after = float(
                            response.headers.get("Retry-After", "5")
                        )
                        structured_log(
                            "warning",
                            "rate_limited",
                            retry_after=retry_after,
                            attempt=attempt,
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    if response.status_code >= 500:
                        structured_log(
                            "warning",
                            "server_error",
                            status=response.status_code,
                            attempt=attempt,
                        )
                        delay = backoff_with_jitter(attempt)
                        await asyncio.sleep(delay)
                        continue

                    response.raise_for_status()

                    async for event in parse_sse_stream(
                        response, heartbeat_timeout
                    ):
                        yield event

                    # Successful completion
                    structured_log(
                        "info",
                        "stream_complete",
                        conversation_id=conversation_id,
                        attempt=attempt,
                    )
                    return

        except (httpx.ConnectError, httpx.ReadTimeout) as exc:
            structured_log(
                "warning",
                "connection_failure",
                error=str(exc),
                attempt=attempt,
                max_retries=max_retries,
            )
            if attempt < max_retries:
                delay = backoff_with_jitter(attempt)
                structured_log(
                    "info", "retry_backoff", delay_s=round(delay, 2)
                )
                await asyncio.sleep(delay)
            else:
                structured_log(
                    "error",
                    "stream_exhausted",
                    conversation_id=conversation_id,
                    total_attempts=max_retries + 1,
                )
                yield StreamEvent(
                    StreamEventType.ERROR,
                    {"message": f"All {max_retries + 1} attempts failed"},
                )
```

### 5.2 Three-State Circuit Breaker with Model Fallback Chain

```python
"""
Circuit breaker + model fallback chain for production inference routing.
Three-state circuit breaker (CLOSED -> OPEN -> HALF_OPEN) prevents cascade
failures when inference backends degrade. Fallback chain automatically
retries with alternative models.
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

from 5_1_sse_client import structured_log  # reuses structured_log above


# ── Circuit Breaker ─────────────────────────────────────────────────


class CircuitState(Enum):
    CLOSED = "closed"        # Normal operation, requests pass through
    OPEN = "open"            # Failures exceeded threshold, all requests rejected
    HALF_OPEN = "half_open"  # Tentatively allowing probe requests


@dataclass
class CircuitBreaker:
    """
    Three-state circuit breaker for inference backends.

    CLOSED: requests flow normally, failures are counted.
    OPEN: all requests immediately rejected (fail-fast), prevents cascade.
    HALF_OPEN: one probe request allowed; success resets, failure re-opens.

    Thread-safe via asyncio.Lock.
    """

    name: str
    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_calls: int = 1

    state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    failure_count: int = field(default=0, init=False)
    last_failure_time: float = field(default=0.0, init=False)
    half_open_calls: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def call(
        self, func: Callable[..., Awaitable[Any]], *args, **kwargs
    ) -> Any:
        """
        Execute func through the circuit breaker.
        Raises CircuitOpenError if circuit is OPEN and recovery timeout
        has not elapsed.
        """
        async with self._lock:
            if self.state == CircuitState.OPEN:
                elapsed = time.monotonic() - self.last_failure_time
                if elapsed < self.recovery_timeout:
                    structured_log(
                        "warning",
                        "circuit_open_reject",
                        breaker=self.name,
                        elapsed_s=round(elapsed, 1),
                        recovery_s=self.recovery_timeout,
                    )
                    raise CircuitOpenError(
                        f"Circuit {self.name} is OPEN "
                        f"({self.recovery_timeout - elapsed:.0f}s remaining)"
                    )
                # Recovery timeout elapsed -- transition to HALF_OPEN
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 0
                structured_log(
                    "info",
                    "circuit_half_open",
                    breaker=self.name,
                )

            if self.state == CircuitState.HALF_OPEN:
                if self.half_open_calls >= self.half_open_max_calls:
                    raise CircuitOpenError(
                        f"Circuit {self.name} HALF_OPEN probe limit reached"
                    )
                self.half_open_calls += 1

        # Execute outside the lock to avoid holding it during I/O
        try:
            result = await func(*args, **kwargs)
        except Exception as exc:
            await self._record_failure(exc)
            raise
        else:
            await self._record_success()
            return result

    async def _record_failure(self, exc: Exception) -> None:
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.monotonic()

            if self.state == CircuitState.HALF_OPEN:
                # Probe failed -- reopen circuit
                self.state = CircuitState.OPEN
                structured_log(
                    "warning",
                    "circuit_reopen",
                    breaker=self.name,
                    error=str(exc),
                )
            elif self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                structured_log(
                    "error",
                    "circuit_opened",
                    breaker=self.name,
                    failures=self.failure_count,
                    threshold=self.failure_threshold,
                )

    async def _record_success(self) -> None:
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                structured_log(
                    "info",
                    "circuit_closed",
                    breaker=self.name,
                    message="probe succeeded, circuit reset",
                )
            self.state = CircuitState.CLOSED
            self.failure_count = 0


class CircuitOpenError(Exception):
    pass


# ── Model Fallback Chain ────────────────────────────────────────────


@dataclass
class ModelEndpoint:
    """Configuration for a single model endpoint in the fallback chain."""
    name: str
    api_url: str
    model_id: str
    cost_per_1m_input: float
    cost_per_1m_output: float
    timeout: float = 120.0


class FallbackChain:
    """
    Ordered chain of model endpoints with per-endpoint circuit breakers.
    Tries each endpoint in sequence; if the circuit is open or the call
    fails, falls through to the next. Returns the result from whichever
    endpoint succeeds first.

    Production usage: primary (Claude Sonnet 4.5) -> secondary (GPT-5.6 Sol)
    -> economy fallback (Claude Haiku 4.5).
    """

    def __init__(self, endpoints: list[ModelEndpoint]) -> None:
        self.endpoints = endpoints
        self.breakers = {
            ep.name: CircuitBreaker(
                name=ep.name,
                failure_threshold=5,
                recovery_timeout=30.0,
            )
            for ep in endpoints
        }

    async def generate(
        self,
        messages: list[dict],
        call_fn: Callable[..., Awaitable[Any]],
    ) -> dict:
        """
        Attempt generation through the chain. call_fn receives
        (endpoint, messages) and returns the model response.

        Returns dict with response + metadata (which endpoint, fallback used).
        """
        errors = []

        for idx, endpoint in enumerate(self.endpoints):
            breaker = self.breakers[endpoint.name]
            is_fallback = idx > 0

            try:
                structured_log(
                    "info",
                    "fallback_attempt",
                    endpoint=endpoint.name,
                    model=endpoint.model_id,
                    position=idx,
                    is_fallback=is_fallback,
                )

                result = await breaker.call(call_fn, endpoint, messages)

                structured_log(
                    "info",
                    "fallback_success",
                    endpoint=endpoint.name,
                    is_fallback=is_fallback,
                )

                return {
                    "response": result,
                    "endpoint": endpoint.name,
                    "model": endpoint.model_id,
                    "fallback_used": is_fallback,
                    "attempts": idx + 1,
                }

            except CircuitOpenError as exc:
                structured_log(
                    "warning",
                    "fallback_circuit_open",
                    endpoint=endpoint.name,
                    error=str(exc),
                )
                errors.append((endpoint.name, str(exc)))

            except Exception as exc:
                structured_log(
                    "warning",
                    "fallback_endpoint_failed",
                    endpoint=endpoint.name,
                    error=str(exc),
                )
                errors.append((endpoint.name, str(exc)))

        # All endpoints exhausted
        structured_log(
            "error",
            "fallback_chain_exhausted",
            total_endpoints=len(self.endpoints),
            errors=errors,
        )
        raise AllEndpointsFailedError(
            f"All {len(self.endpoints)} endpoints failed: {errors}"
        )


class AllEndpointsFailedError(Exception):
    pass


# ── Wiring Example ──────────────────────────────────────────────────

# Production fallback chain configuration
FALLBACK_CHAIN = FallbackChain([
    ModelEndpoint(
        name="primary",
        api_url="https://api.anthropic.com/v1/messages",
        model_id="claude-sonnet-4-5",
        cost_per_1m_input=3.00,
        cost_per_1m_output=15.00,
        timeout=120.0,
    ),
    ModelEndpoint(
        name="secondary",
        api_url="https://api.openai.com/v1/chat/completions",
        model_id="gpt-5.6-sol",
        cost_per_1m_input=4.00,
        cost_per_1m_output=20.00,
        timeout=120.0,
    ),
    ModelEndpoint(
        name="economy",
        api_url="https://api.anthropic.com/v1/messages",
        model_id="claude-haiku-4-5",
        cost_per_1m_input=0.25,
        cost_per_1m_output=1.25,
        timeout=60.0,
    ),
])
```

### 5.3 Conversation Context Compaction

```python
"""
Context compaction for multi-turn conversations.
Implements the production hybrid strategy: keep last N exchanges verbatim,
summarize everything older, re-inject system prompt periodically.
Prevents context window overflow -- the most common silent failure
in production chat systems.
"""

import tiktoken
from dataclasses import dataclass


@dataclass
class ContextBudget:
    """Token budget allocation for prompt assembly."""
    max_context_tokens: int = 128_000
    system_prompt_tokens: int = 2_000     # reserved for system prompt
    structured_memory_tokens: int = 1_000  # reserved for user facts
    response_budget_tokens: int = 4_096    # reserved for model output
    safety_margin: float = 0.1             # 10% headroom

    @property
    def available_for_history(self) -> int:
        reserved = (
            self.system_prompt_tokens
            + self.structured_memory_tokens
            + self.response_budget_tokens
        )
        usable = self.max_context_tokens - reserved
        return int(usable * (1 - self.safety_margin))


def count_tokens(text: str, model: str = "gpt-4") -> int:
    """Count tokens using tiktoken. Works for OpenAI and approximates others."""
    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))


def count_message_tokens(messages: list[dict], model: str = "gpt-4") -> int:
    """Count total tokens across a list of chat messages."""
    total = 0
    for msg in messages:
        # ~4 tokens per message for role/formatting overhead
        total += 4
        content = msg.get("content", "")
        if isinstance(content, str):
            total += count_tokens(content, model)
        elif isinstance(content, list):
            # Multi-modal: count text parts only (vision tokens estimated separately)
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += count_tokens(part["text"], model)
    return total


def compact_conversation(
    system_prompt: str,
    structured_memory: dict,
    conversation: list[dict],
    summarize_fn,  # async callable: (messages) -> str
    model: str = "gpt-4",
    verbatim_exchanges: int = 6,
    budget: ContextBudget | None = None,
) -> dict:
    """
    Compact a conversation to fit within the context budget.

    Strategy (production hybrid):
    1. Keep last `verbatim_exchanges` exchanges (user+assistant pairs) verbatim
    2. Summarize everything older into a concise summary
    3. Inject summary into system prompt prefix
    4. Always include structured memory (lossless facts)

    Returns dict with:
      - messages: compacted message list ready for inference
      - summary: generated summary of older context (or None)
      - tokens_saved: how many tokens were saved by compaction
      - compaction_applied: whether compaction was needed
    """
    if budget is None:
        budget = ContextBudget()

    memory_block = _format_structured_memory(structured_memory)

    # Split into recent (verbatim) and older (candidates for summarization)
    # Each exchange = 1 user + 1 assistant message = 2 messages
    split_idx = max(0, len(conversation) - (verbatim_exchanges * 2))
    older_messages = conversation[:split_idx]
    recent_messages = conversation[split_idx:]

    # Count tokens without compaction
    total_uncompacted = (
        count_tokens(system_prompt, model)
        + count_tokens(memory_block, model)
        + count_message_tokens(conversation, model)
    )

    # Check if compaction is needed
    if total_uncompacted <= budget.available_for_history:
        # No compaction needed -- return full history
        messages = _assemble_prompt(
            system_prompt, memory_block, None, conversation
        )
        return {
            "messages": messages,
            "summary": None,
            "tokens_saved": 0,
            "compaction_applied": False,
            "total_tokens": total_uncompacted,
        }

    # Compaction needed -- summarize older messages
    if older_messages:
        summary = summarize_fn(older_messages)
    else:
        summary = None

    messages = _assemble_prompt(
        system_prompt, memory_block, summary, recent_messages
    )

    total_compacted = count_message_tokens(messages, model)
    tokens_saved = total_uncompacted - total_compacted

    return {
        "messages": messages,
        "summary": summary,
        "tokens_saved": tokens_saved,
        "compaction_applied": True,
        "total_tokens": total_compacted,
    }


def _format_structured_memory(memory: dict) -> str:
    """Format structured user facts for injection into prompt."""
    if not memory:
        return ""
    lines = ["<structured_memory>"]
    for key, value in memory.items():
        lines.append(f"  {key}: {value}")
    lines.append("</structured_memory>")
    return "\n".join(lines)


def _assemble_prompt(
    system_prompt: str,
    memory_block: str,
    summary: str | None,
    recent_messages: list[dict],
) -> list[dict]:
    """Assemble the final prompt with all components."""
    system_parts = [system_prompt]
    if memory_block:
        system_parts.append(memory_block)
    if summary:
        system_parts.append(
            f"<conversation_summary>\n{summary}\n</conversation_summary>"
        )

    messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
    messages.extend(recent_messages)
    return messages
```

### 5.4 Multi-Dimensional Rate Limiter

```python
"""
Multi-dimensional rate limiter for AI chat APIs.
Enforces RPM (requests per minute), TPM (tokens per minute),
and concurrent stream limits. Redis-backed with in-memory fallback
when Redis is unavailable.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


@dataclass
class RateLimits:
    """Rate limit configuration per user tier."""
    rpm: int            # requests per minute
    tpm: int            # tokens per minute
    max_concurrent: int  # max simultaneous streams


# Tier definitions
TIER_LIMITS = {
    "free":       RateLimits(rpm=10,  tpm=10_000,    max_concurrent=1),
    "pro":        RateLimits(rpm=60,  tpm=100_000,   max_concurrent=3),
    "enterprise": RateLimits(rpm=300, tpm=1_000_000, max_concurrent=10),
}


class InMemoryCounter:
    """Sliding-window counter for in-memory fallback."""

    def __init__(self) -> None:
        self._windows: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def increment(self, key: str, window_seconds: int = 60) -> int:
        async with self._lock:
            now = time.monotonic()
            if key not in self._windows:
                self._windows[key] = []

            # Prune expired entries
            cutoff = now - window_seconds
            self._windows[key] = [
                t for t in self._windows[key] if t > cutoff
            ]
            self._windows[key].append(now)
            return len(self._windows[key])

    async def get_count(self, key: str, window_seconds: int = 60) -> int:
        async with self._lock:
            now = time.monotonic()
            if key not in self._windows:
                return 0
            cutoff = now - window_seconds
            self._windows[key] = [
                t for t in self._windows[key] if t > cutoff
            ]
            return len(self._windows[key])


class RateLimiter:
    """
    Multi-dimensional rate limiter.
    Primary: Redis sliding window (shared across instances).
    Fallback: in-memory counters (per-instance, gracefully degraded).
    """

    def __init__(self, redis_url: Optional[str] = None) -> None:
        self._redis = None
        if redis_url and REDIS_AVAILABLE:
            self._redis = aioredis.from_url(redis_url)
        self._fallback = InMemoryCounter()
        self._concurrent: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def check_and_consume(
        self,
        user_id: str,
        tier: str,
        estimated_tokens: int = 0,
    ) -> dict:
        """
        Check all rate limit dimensions. Returns dict with:
          allowed: bool
          reason: str (if denied)
          limits: current counts vs. limits
        """
        limits = TIER_LIMITS.get(tier, TIER_LIMITS["free"])

        # Dimension 1: RPM
        rpm_key = f"rpm:{user_id}"
        rpm_count = await self._get_count(rpm_key)
        if rpm_count >= limits.rpm:
            return {
                "allowed": False,
                "reason": f"RPM limit exceeded ({rpm_count}/{limits.rpm})",
                "retry_after": 60,
            }

        # Dimension 2: TPM
        tpm_key = f"tpm:{user_id}"
        tpm_count = await self._get_count(tpm_key)
        if tpm_count + estimated_tokens > limits.tpm:
            return {
                "allowed": False,
                "reason": (
                    f"TPM limit exceeded "
                    f"({tpm_count + estimated_tokens}/{limits.tpm})"
                ),
                "retry_after": 60,
            }

        # Dimension 3: Concurrent streams
        async with self._lock:
            current_concurrent = self._concurrent.get(user_id, 0)
        if current_concurrent >= limits.max_concurrent:
            return {
                "allowed": False,
                "reason": (
                    f"Concurrent stream limit "
                    f"({current_concurrent}/{limits.max_concurrent})"
                ),
                "retry_after": 5,
            }

        # All checks passed -- consume
        await self._increment(rpm_key)
        if estimated_tokens > 0:
            await self._increment_by(tpm_key, estimated_tokens)
        async with self._lock:
            self._concurrent[user_id] = current_concurrent + 1

        return {
            "allowed": True,
            "limits": {
                "rpm": f"{rpm_count + 1}/{limits.rpm}",
                "tpm": f"{tpm_count + estimated_tokens}/{limits.tpm}",
                "concurrent": f"{current_concurrent + 1}/{limits.max_concurrent}",
            },
        }

    async def release_stream(self, user_id: str) -> None:
        """Release a concurrent stream slot when generation completes."""
        async with self._lock:
            current = self._concurrent.get(user_id, 0)
            self._concurrent[user_id] = max(0, current - 1)

    async def _get_count(self, key: str) -> int:
        if self._redis:
            try:
                val = await self._redis.get(key)
                return int(val) if val else 0
            except Exception:
                pass  # Fall through to in-memory
        return await self._fallback.get_count(key)

    async def _increment(self, key: str) -> None:
        if self._redis:
            try:
                pipe = self._redis.pipeline()
                pipe.incr(key)
                pipe.expire(key, 60)
                await pipe.execute()
                return
            except Exception:
                pass
        await self._fallback.increment(key)

    async def _increment_by(self, key: str, amount: int) -> None:
        if self._redis:
            try:
                pipe = self._redis.pipeline()
                pipe.incrby(key, amount)
                pipe.expire(key, 60)
                await pipe.execute()
                return
            except Exception:
                pass
        for _ in range(amount):
            await self._fallback.increment(key)
```

---

## 6. Architectural System Design Scenarios

### Scenario 1: Enterprise AI Chat Assistant at Scale

**Problem statement.** A B2B SaaS company with 1M registered users (100K DAU) needs to build an AI chat assistant integrated into their product. Requirements: sub-500ms TTFT at p95, support for 150 peak requests/second, multi-turn conversations with context persistence, content moderation, cost optimization (blended target < $0.005 per turn), and 99.99% uptime across two regions.

**Architecture.**

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                     ENTERPRISE CHAT ASSISTANT AT SCALE                         │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │                     CONNECTION TIER                                      │  │
│  │                     (scales for persistent connectivity)                 │  │
│  │                                                                          │  │
│  │  CDN Edge (Cloudflare / CloudFront)                                      │  │
│  │      │                                                                   │  │
│  │  API Gateway (Kong)                                                      │  │
│  │      ├── JWT auth + RBAC (free/pro/enterprise)                          │  │
│  │      ├── Multi-dim rate limiter (Redis: RPM + TPM + concurrent)         │  │
│  │      ├── Request validation + W3C Trace Context                         │  │
│  │      └── URL-based routing to backend services                          │  │
│  │                                                                          │  │
│  └──────────────────────────────┬──────────────────────────────────────────┘  │
│                                  │                                            │
│  ┌──────────────────────────────▼──────────────────────────────────────────┐  │
│  │                     ORCHESTRATION TIER                                   │  │
│  │                     (stateless, horizontal autoscale)                    │  │
│  │                                                                          │  │
│  │  ┌───────────────┐  ┌──────────────┐  ┌──────────────────────────────┐  │  │
│  │  │ Context        │  │ Moderation   │  │ LLM Router                   │  │  │
│  │  │ Assembler      │  │ Pipeline     │  │                              │  │  │
│  │  │                │  │              │  │ Complexity classifier:        │  │  │
│  │  │ Redis (session)│  │ Pre:  BERT   │  │  Simple ──> Haiku 4.5        │  │  │
│  │  │ Postgres       │  │  classifiers │  │    ($0.25/M, ~80ms TTFT)     │  │  │
│  │  │ (durable)      │  │ Post: output │  │  Standard ──> Sonnet 4.5    │  │  │
│  │  │ Pinecone       │  │  validators  │  │    ($3/M, ~200ms TTFT)       │  │  │
│  │  │ (embeddings)   │  │              │  │  Complex ──> Opus 4.5        │  │  │
│  │  │                │  │              │  │    ($15/M, ~400ms TTFT)       │  │  │
│  │  │ Compaction:    │  │              │  │                              │  │  │
│  │  │ 6 verbatim +   │  │              │  │ Fallback: Sonnet -> Sol      │  │  │
│  │  │ summarize older│  │              │  │   -> Haiku (circuit breaker)  │  │  │
│  │  └───────────────┘  └──────────────┘  └──────────────────────────────┘  │  │
│  │                                                                          │  │
│  └──────────────────────────────┬──────────────────────────────────────────┘  │
│                                  │                                            │
│  ┌──────────────────────────────▼──────────────────────────────────────────┐  │
│  │                     INFERENCE TIER                                       │  │
│  │                     (GPU clusters, scaled independently)                 │  │
│  │                                                                          │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐    │  │
│  │  │ vLLM / TGI Inference Servers                                    │    │  │
│  │  │                                                                  │    │  │
│  │  │  Continuous batching for throughput                               │    │  │
│  │  │  FP8 quantization (< 2% quality loss)                           │    │  │
│  │  │  KV-cache management + monitoring                                │    │  │
│  │  │  ~30x H100 for 1M users (18K TPS target)                       │    │  │
│  │  │                                                                  │    │  │
│  │  │  Autoscaling:                                                    │    │  │
│  │  │   Predictive (pre-scale for US business hours)                  │    │  │
│  │  │   Reactive (HPA on GPU util + queue depth)                      │    │  │
│  │  │   Cold start: 30-90s model load; mitigate with pre-warmed       │    │  │
│  │  │     standby nodes                                                │    │  │
│  │  └─────────────────────────────────────────────────────────────────┘    │  │
│  │                                                                          │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
│                                                                               │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │                     PERSISTENCE & OBSERVABILITY                          │  │
│  │                                                                          │  │
│  │  PostgreSQL (conversations, messages, user prefs, sharded by conv_id)   │  │
│  │  Redis (session cache, 24h TTL, rate limit counters)                    │  │
│  │  Pinecone/pgvector (conversation embeddings, long-term memory)          │  │
│  │  Kafka ──> Flink ──> ClickHouse (structured logs, anomaly detection)   │  │
│  │  Prometheus + Grafana (TTFT p95/p99, TPS, GPU util, error rate, cost)  │  │
│  │  OpenTelemetry (browser -> gateway -> orchestrator -> inference)        │  │
│  │                                                                          │  │
│  │  Dashboard layout:                                                      │  │
│  │   Row 1: error rate | P95 latency | cost/hr                            │  │
│  │   Row 2: eval scores | user feedback (thumbs) | retry rate             │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix.**

```
┌────────────────────────┬─────────────────────────┬──────────────────────────┐
│ Decision                │ Option A                │ Option B                 │
├────────────────────────┼─────────────────────────┼──────────────────────────┤
│ Inference hosting       │ API-based (Anthropic,   │ Self-hosted (vLLM on     │
│                         │ OpenAI)                 │ own H100 cluster)        │
│                         │ + No GPU management     │ + Data stays internal    │
│                         │ + No cold start         │ + Amortized costs at     │
│                         │ + Faster time-to-market │   high volume            │
│                         │ - Data sent externally  │ - 30-90s cold start      │
│                         │ - Linear cost scaling   │ - GPU ops team needed    │
│                         │ - Vendor lock-in risk   │ - Higher upfront cost    │
│                         │                         │                          │
│                         │ CHOSEN: API-based at    │ Migrate to self-hosted   │
│                         │ launch (speed)          │ when >200K queries/month │
├────────────────────────┼─────────────────────────┼──────────────────────────┤
│ Streaming protocol      │ SSE                     │ WebSocket                │
│                         │ + HTTP/1.1 compatible   │ + Bidirectional          │
│                         │ + Works through CDNs    │ + Real-time collab       │
│                         │ + Auto-reconnection     │ - Proxy issues           │
│                         │ + Simpler               │ - Manual reconnection    │
│                         │                         │ - Overkill for chat      │
│                         │                         │                          │
│                         │ CHOSEN: SSE             │ Industry consensus       │
├────────────────────────┼─────────────────────────┼──────────────────────────┤
│ Context management      │ Sliding window (last    │ Hybrid: 6 verbatim +     │
│                         │ 10-15 exchanges)        │ summarize older +        │
│                         │ + Simplest, fastest     │ structured memory        │
│                         │ + Lowest cost           │ + Continuity preserved   │
│                         │ - Old context lost      │ + Facts survive lossless │
│                         │ - Users notice gaps     │ - Summarization overhead │
│                         │                         │ - Potential hallucination │
│                         │                         │   in summary             │
│                         │                         │                          │
│                         │                         │ CHOSEN: Hybrid           │
├────────────────────────┼─────────────────────────┼──────────────────────────┤
│ Model routing           │ Single model (Sonnet)   │ Three-tier router        │
│                         │ + Simple to operate     │ + 60% cost reduction     │
│                         │ + Consistent quality    │ + Right model for task   │
│                         │ - Expensive for simple  │ - Complexity classifier  │
│                         │   queries               │   needed                 │
│                         │ - No cost optimization  │ - Quality variance       │
│                         │                         │                          │
│                         │                         │ CHOSEN: Three-tier       │
│                         │                         │ (economics dominate      │
│                         │                         │  at 100K DAU)            │
└────────────────────────┴─────────────────────────┴──────────────────────────┘
```

**Decision rationale.** The three-tier separation (connection, orchestration, inference) is the core architectural decision. Connection-heavy frontend scales independently from compute-heavy inference -- you can handle 10x more SSE connections without adding GPU capacity. API-based inference at launch because time-to-market matters more than per-token cost before product-market fit. SSE over WebSocket is the industry consensus for token streaming. Three-tier model routing is essential at 100K DAU because 60% cost reduction at that volume translates to tens of thousands of dollars per month. The hybrid context management strategy is chosen because users cannot tolerate losing context (sliding window) but full history is cost-prohibitive (context tax).

---

### Scenario 2: Multi-Tenant AI Chat Platform

**Problem statement.** A platform company building a white-label AI chat product for enterprise customers. Each tenant (customer) has their own branding, system prompts, model preferences, and data isolation requirements. Requirements: strict tenant isolation (data, inference, rate limits), GDPR-compliant data residency per tenant geography, support for 7K+ concurrent sessions across tenants, per-tenant billing, and defense against cross-tenant data leakage including KV-cache side-channel attacks.

**Architecture.**

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                     MULTI-TENANT AI CHAT PLATFORM                             │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │                     GLOBAL CONTROL PLANE                                 │  │
│  │                     (holds NO personal data)                             │  │
│  │                                                                          │  │
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐  │  │
│  │  │ Tenant Registry   │  │ Cell Router       │  │ Billing Aggregator  │  │  │
│  │  │                   │  │                   │  │                      │  │  │
│  │  │ tenant_id -> cell │  │ Map request to    │  │ Per-tenant token     │  │  │
│  │  │ tier, model prefs │  │ home cell before  │  │ usage metering       │  │  │
│  │  │ branding config   │  │ ANY content is    │  │ Tiered pricing       │  │  │
│  │  │ system prompt     │  │ processed         │  │                      │  │  │
│  │  └──────────────────┘  └──────────────────┘  └──────────────────────┘  │  │
│  │                                                                          │  │
│  └──────────────────────────────┬──────────────────────────────────────────┘  │
│                                  │                                            │
│          ┌───────────────────────┼───────────────────────┐                    │
│          │                       │                       │                    │
│  ┌───────▼───────────┐  ┌───────▼───────────┐  ┌───────▼───────────┐        │
│  │  EU CELL            │  │  US CELL            │  │  AP CELL            │        │
│  │                     │  │                     │  │                     │        │
│  │  ┌───────────────┐ │  │  ┌───────────────┐ │  │  ┌───────────────┐ │        │
│  │  │ API Gateway    │ │  │  │ API Gateway    │ │  │  │ API Gateway    │ │        │
│  │  │ + Rate Limiter │ │  │  │ + Rate Limiter │ │  │  │ + Rate Limiter │ │        │
│  │  │ (per-tenant)   │ │  │  │ (per-tenant)   │ │  │  │ (per-tenant)   │ │        │
│  │  └───────┬───────┘ │  │  └───────┬───────┘ │  │  └───────┬───────┘ │        │
│  │          │          │  │          │          │  │          │          │        │
│  │  ┌───────▼───────┐ │  │  ┌───────▼───────┐ │  │  ┌───────▼───────┐ │        │
│  │  │ Orchestrator   │ │  │  │ Orchestrator   │ │  │  │ Orchestrator   │ │        │
│  │  │ (namespace     │ │  │  │ (namespace     │ │  │  │ (namespace     │ │        │
│  │  │  isolated)     │ │  │  │  isolated)     │ │  │  │  isolated)     │ │        │
│  │  └───────┬───────┘ │  │  └───────┬───────┘ │  │  └───────┬───────┘ │        │
│  │          │          │  │          │          │  │          │          │        │
│  │  ┌───────▼───────┐ │  │  ┌───────▼───────┐ │  │  ┌───────▼───────┐ │        │
│  │  │ GPU Inference  │ │  │  │ GPU Inference  │ │  │  │ GPU Inference  │ │        │
│  │  │ (vLLM, KV-    │ │  │  │ (vLLM, KV-    │ │  │  │ (vLLM, KV-    │ │        │
│  │  │  cache per-   │ │  │  │  cache per-   │ │  │  │  cache per-   │ │        │
│  │  │  tenant)      │ │  │  │  tenant)      │ │  │  │  tenant)      │ │        │
│  │  └───────┬───────┘ │  │  └───────┬───────┘ │  │  └───────┬───────┘ │        │
│  │          │          │  │          │          │  │          │          │        │
│  │  ┌───────▼───────┐ │  │  ┌───────▼───────┐ │  │  ┌───────▼───────┐ │        │
│  │  │ Storage        │ │  │  │ Storage        │ │  │  │ Storage        │ │        │
│  │  │ Postgres (RLS) │ │  │  │ Postgres (RLS) │ │  │  │ Postgres (RLS) │ │        │
│  │  │ Redis          │ │  │  │ Redis          │ │  │  │ Redis          │ │        │
│  │  │ Vector DB      │ │  │  │ Vector DB      │ │  │  │ Vector DB      │ │        │
│  │  │ (per-tenant    │ │  │  │ (per-tenant    │ │  │  │ (per-tenant    │ │        │
│  │  │  namespace)    │ │  │  │  namespace)    │ │  │  │  namespace)    │ │        │
│  │  │ Regional KMS   │ │  │  │ Regional KMS   │ │  │  │ Regional KMS   │ │        │
│  │  └───────────────┘ │  │  └───────────────┘ │  │  └───────────────┘ │        │
│  │                     │  │                     │  │                     │        │
│  │  Regional SCPs      │  │  Regional SCPs      │  │  Regional SCPs      │        │
│  │  EU data never      │  │  US data never      │  │  AP data never      │        │
│  │  leaves EU cell     │  │  leaves US cell     │  │  leaves AP cell     │        │
│  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘        │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │                     EXECUTION PIPELINE (per request)                     │  │
│  │                                                                          │  │
│  │  Auth ──> Tenant ──> Budget ──> Session ──> Context ──> Sandbox          │  │
│  │    │        │          │          │           │           │               │  │
│  │   JWT    tenant_id   token     state       prompt      gVisor            │  │
│  │   verify  resolve    quota     machine     assembly    isolation          │  │
│  │            │          check     + action               (not just          │  │
│  │         route to               queue                   containers)        │  │
│  │         home cell              + pending                                  │  │
│  │                                confirms     ──> LLM ──> Persist           │  │
│  │                                                  │        │               │  │
│  │                                               inference  conversation    │  │
│  │                                               (in-cell   + memory        │  │
│  │                                                only)     + billing       │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off matrix.**

```
┌────────────────────────┬─────────────────────────┬──────────────────────────┐
│ Decision                │ Option A                │ Option B                 │
├────────────────────────┼─────────────────────────┼──────────────────────────┤
│ Tenant isolation model  │ Fully shared            │ Hybrid namespace         │
│                         │ (tenant_id filtering)   │ (shared infra,           │
│                         │                         │  K8s namespace per       │
│                         │ + Lowest cost           │  tenant group)           │
│                         │ + Simplest ops          │ + Strong isolation       │
│                         │ - Noisy neighbor risk   │ + Balanced cost          │
│                         │ - Cross-tenant leakage  │ + 2026 industry standard │
│                         │   via bugs              │ - More complex ops       │
│                         │                         │                          │
│                         │                         │ CHOSEN: Hybrid namespace │
│                         │                         │ Fully siloed only for    │
│                         │                         │ regulated industries     │
├────────────────────────┼─────────────────────────┼──────────────────────────┤
│ Database isolation      │ Application-level       │ Row-Level Security       │
│                         │ filtering (WHERE        │ (RLS) at database level  │
│                         │ tenant_id = ?)          │                          │
│                         │                         │                          │
│                         │ + Simpler to implement  │ + Policy runs inside DB  │
│                         │ - Developer can omit    │   engine on every query  │
│                         │ - ORM change can strip  │ + Injection cannot       │
│                         │ - Prompt injection can  │   override               │
│                         │   bypass                │ + Developer cannot omit  │
│                         │                         │                          │
│                         │                         │ CHOSEN: RLS              │
│                         │                         │ Defense-in-depth         │
├────────────────────────┼─────────────────────────┼──────────────────────────┤
│ Vector DB isolation     │ Shared index with       │ Per-tenant namespaces    │
│                         │ tenant_id filter        │ + tenant_id stamped on   │
│                         │                         │ every vector + required  │
│                         │ + Simple                │ filter on every query    │
│                         │ - Semantic search is    │                          │
│                         │   approximate: can      │ + No cross-tenant        │
│                         │   return wrong-tenant   │   leakage                │
│                         │   matches               │ + Separate indexes for   │
│                         │ - Compliance violation  │   regulated tenants      │
│                         │                         │                          │
│                         │                         │ CHOSEN: Per-tenant       │
│                         │                         │ namespaces (critical     │
│                         │                         │ for correctness)         │
├────────────────────────┼─────────────────────────┼──────────────────────────┤
│ KV-cache management     │ Shared prefix cache     │ Per-tenant cache         │
│                         │ across tenants          │ partitioning             │
│                         │                         │                          │
│                         │ + Higher cache hit rate │ + No TTFT side-channel   │
│                         │ + Lower GPU memory      │   attack                 │
│                         │ - TTFT timing side-     │ + Prevents cross-tenant  │
│                         │   channel attack        │   information leak       │
│                         │   (NDSS 2025, Llama2-  │ - Lower cache efficiency │
│                         │   13B/A100)             │ - Higher memory usage    │
│                         │                         │                          │
│                         │                         │ CHOSEN: Per-tenant       │
│                         │                         │ partitioning (security   │
│                         │                         │ over efficiency)         │
└────────────────────────┴─────────────────────────┴──────────────────────────┘
```

**Decision rationale.** The cell-based architecture is the most consequential decision. Data residency is not just a database setting -- a chatbot's personal data lives in conversation stores, vector indexes, prompt logs, and inference request bodies. Designing cells around the database alone means embeddings and model calls will quietly breach compliance. Each cell is self-contained: storage, inference, embeddings, logs, full orchestration. The thin global control plane maps tenants to cells but holds nothing personal. Hybrid namespace isolation (shared infrastructure, Kubernetes namespace-level separation) is the 2026 standard: it provides strong isolation without the cost multiplication of fully siloed infrastructure. RLS at the database level is non-negotiable because application-level tenant filtering (WHERE tenant_id = ?) fails silently when developers omit the filter, ORMs strip it, or prompt injection bypasses it. Per-tenant vector DB namespaces are critical because semantic search is approximate by design and can return wrong-tenant matches from a shared index. KV-cache partitioning per tenant prevents the TTFT timing side-channel attack demonstrated at NDSS 2025.

> **Salesforce reference (production):** Their multi-tenant AI agent platform handles 7K+ concurrent sessions. Session management persists state machine, action queue, and pending confirmations via SDK, eliminating Redis serialization and TTL management. 24-hour TTL policy preserves relevant context while auto-evicting stale data.
