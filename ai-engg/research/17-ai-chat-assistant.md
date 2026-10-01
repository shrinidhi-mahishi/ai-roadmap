# Research: Design Personal AI Chat Assistant
**Date researched**: 2026-09-29
**Sources consulted**: 28

---

## 1. System Topology & Mechanics

### Four-Layer Architecture

A production AI chat assistant flows through four primary layers:

1. **Context Engineering Layer** -- assembles the full prompt (system prompt + conversation history + user message), manages context window budget, injects structured memory
2. **Generation Engine Layer** -- tokenizes, routes to appropriate model, runs inference on GPU cluster
3. **Persistent Memory Layer** -- stores conversations, extracts structured facts, manages long-term user preferences
4. **SSE Streaming Layer** -- streams tokens back to client as generated, handles connection lifecycle

### Request Lifecycle (7 Stages)

1. User sends message from browser/app
2. Request hits CDN edge (Cloudflare, CloudFront), routes to nearest API gateway
3. **API Gateway** authenticates (JWT/Bearer token verification), checks rate limits (token-bucket or sliding-window), validates request schema, routes to appropriate backend service (`/auth/*` -> Auth Service, `/conversations/*` -> Conversation Service)
4. **Orchestration Service** assembles full prompt, runs pre-inference moderation (distilled BERT classifiers, sub-millisecond latency), dispatches to inference
5. **Inference Engine** tokenizes via BPE (tiktoken), runs two-phase inference (prefill + decode), streams tokens via SSE
6. **Post-inference filters** run content safety classifiers on output
7. Tokens flow back through stack for real-time rendering; conversation persisted to database

### API Gateway vs. Load Balancer

- **Load Balancer**: "dumb" -- distributes TCP/HTTP connections across servers (weighted round-robin or least-connections)
- **API Gateway**: "smart" -- reads URL path, routes to specific services, handles auth, rate limiting, request validation, API versioning, observability (W3C Trace Context trace IDs)
- Implementations: Kong, Envoy Proxy, AWS API Gateway

### Conversation State Management

LLMs are stateless -- the model holds nothing between requests. The system must inject conversation history into every new request.

**State reconstruction per request:**
1. Retrieve conversation history from low-latency store (PostgreSQL/DynamoDB for persistence, Redis for active session cache)
2. Apply context window management (truncation, summarization, or compaction)
3. Append system prompt + managed history + new user message
4. Send assembled prompt to inference

**Advanced memory patterns:**
- Older conversation segments are summarized or converted to embeddings
- Semantic search (via vector DB) retrieves relevant past context when user references historical details
- Structured memory extraction pulls deterministic facts (user preferences, confirmed decisions) into a separate store that travels with every prompt

### Streaming Response Architecture

All major AI chat products (ChatGPT, Claude, Gemini) use **Server-Sent Events (SSE)**, not WebSockets.

**Why SSE over WebSocket:**
- Chat is unidirectional streaming (server -> client) after the request -- SSE is purpose-built for this
- Simpler protocol: HTTP/1.1 compatible, works through proxies/CDNs without special configuration
- Automatic reconnection built into the browser EventSource API
- WebSocket is bidirectional but adds complexity (connection upgrade, heartbeat management, proxy issues) unnecessary for token streaming

**SSE event structure (reference implementation):**
1. `start` -- conversation ID, model, tier metadata
2. `token` -- individual token chunks (content_block_delta)
3. `meta` -- usage stats (prompt/completion/total tokens), cost, latency, fallback status
4. `done` -- stream termination with stop_reason

**Claude-specific SSE events:** `message_start`, `content_block_start`, `content_block_delta`, `content_block_stop`, `message_delta`, `message_stop`. Text deltas in `delta.text`; tool-use deltas in `delta.partial_json` (must concatenate before JSON.parse).

**Frontend streaming consumption:**
- `ReadableStream` with `TextDecoder` for incremental token rendering
- Optimistic UI -- show user message before server confirmation
- Virtualized lists for long conversations (render only visible messages)
- Skeleton screens during loading

### Multi-Modal Input/Output

Modern chat assistants handle text, images, code, and files. The orchestration layer must:
- Detect input modality and route to appropriate processing pipeline
- Manage separate token budgets for text vs. vision tokens (vision tokens are typically more expensive)
- Handle file uploads via object storage (S3/GCS) with pre-signed URLs
- Render structured outputs (markdown, code blocks, LaTeX, charts) in the frontend

### LLM Router (Model Selection)

A critical component that routes each request to the optimal model based on complexity, cost, and latency targets.

**Three-tier routing (reference implementation):**

| Tier | Model Size | Latency | Cost (per 1M tokens) |
|------|-----------|---------|------|
| Fast | 8B FP8 | ~120ms | $0.15 |
| Standard | 70B | ~280ms | Mid-range |
| Reasoning | DeepThink/o1-class | Higher | $2.50 |

**Routing strategies:**
- **Complexity-based**: Simple queries -> cheap/fast models; complex reasoning -> frontier models
- **Load-based**: Distribute across model instances based on current utilization
- **Fallback chains**: Automatically retry with alternative models when primary fails
- **Cost-based**: Route to cheapest model that meets quality threshold

GPT-5's built-in router shifts between "fast mode" (lower-latency model) and "thinking mode" (reasoning tokens for deeper logic), with a fallback mode to a smaller backup model. This blended routing reduces inference costs by up to 60%.

---

## 2. Token Economics & NFR Metrics

### Per-Conversation Cost Modeling

**Token fundamentals:**
- BPE vocabulary: 30,000-100,000 tokens
- English text: ~1.3-1.5 tokens per word (10 words ~ 13-15 tokens)
- Code is more token-dense than prose
- Non-English languages require more tokens per word due to English-biased BPE vocabularies

**Cost compounding in multi-turn conversations:**
Each turn re-sends the full conversation history as input. A 5-turn conversation where each response is 500 tokens:
- Turn 1 input: ~100 tokens, output: 500 tokens
- Turn 5 input: ~2,600 tokens (accumulated context), output: 500 tokens
- Total input tokens across all turns: ~6,500 (not 500)

This is the "context tax" -- costs grow quadratically with conversation length, not linearly.

**Reasoning model hidden costs:** Models with chain-of-thought (o1, o3, DeepThink) include invisible "thinking" tokens billed as output. A 500-token visible response can consume 2,000+ tokens total.

**Long-context surcharges:** Requests exceeding ~272K tokens bill input at 2x and output at 1.5x (OpenAI pricing, Sep 2026).

### Current API Pricing (Sep 2026, per 1M tokens)

| Provider/Model | Input | Cached Input | Output |
|---|---|---|---|
| GPT-6 Astra (frontier) | $10 | $1 | $50 |
| GPT-5.6 Sol (balanced) | $4 | $0.40 | $20 |
| GPT-5.6 Luna (economy) | $0.20 | $0.02 | $1.20 |
| Claude Sonnet 4.5 (inferred) | ~$3 | ~$0.30 | ~$15 |
| Claude Haiku 4.5 (inferred) | ~$0.25 | ~$0.025 | ~$1.25 |

Cached input is typically 10% of input price. Batch API typically halves costs with 24-hour SLA.

**Estimated production costs:**
- Light use (personal projects): $5-40/month
- Small apps: $40-200/month
- Production apps: $200-1,500/month
- Enterprise: $1,500+/month
- OpenAI's estimated inference spend: ~$700K+/day (inferred from infrastructure reports)

### Cost Optimization Strategies

1. **Prompt caching**: Reuse cached prefill computations; cached input at 10% of full price. For multi-turn conversations with stable system prompts, this is the single biggest savings lever.
2. **Model routing**: Route simple queries to economy models (Luna/Haiku), complex ones to frontier models. Achieves blended cost reduction of ~60%.
3. **Response caching**: Embedding-based cache keys in Redis for semantically similar prompts. Cache hit on repeated questions avoids inference entirely.
4. **Context compaction**: Summarize older conversation turns to reduce input token count. 40-60% token reduction achievable with compression techniques.
5. **Token budgets**: Per-request and per-conversation limits prevent runaway costs.
6. **Batch API**: Non-interactive workloads (summarization, classification) at 50% discount with 24-hour SLA.
7. **Dynamic scaling**: Scale GPU nodes to zero during low-traffic periods (2-6 AM local).

30-70% cost savings reported by teams following these practices vs. naive usage.

### Latency Targets for Interactive Chat

**Key metrics (percentile-based, never averages):**

| Metric | Definition | Target (interactive chat) |
|---|---|---|
| **TTFT** (Time to First Token) | Time from request to first token received | < 500ms (p95), < 1s (p99) |
| **ITL/TPOT** (Inter-Token Latency) | Average time between tokens after first | < 30ms for smooth reading |
| **TPS** (Tokens Per Second) | Output generation rate | > 30 tok/s for chat |
| **E2E Latency** | Total time from request to final token | TTFT + (output_tokens x TPOT) |

**Benchmark data (reference implementation, 200 concurrent users):**
- Avg TTFT: 119.96ms
- P95 TTFT: 135.8ms
- Token output: 146.04 tokens/sec (system-wide)
- Zero unhandled 5xx errors

**TTFT vs. throughput trade-off:** Optimizing for throughput (batching more requests) hurts TTFT (increases queue wait time). Production SLAs should specify both: e.g., "TTFT p99 < 1s AND throughput > 2000 tok/s."

**TTFT scales with prompt length:** 500-token prompt and 100,000-token document produce very different TTFT on the same model. The prefill phase must process all input tokens before generating the first output token.

### Concurrent User Capacity Planning

**Reference sizing model (1M registered users):**

| Metric | Value |
|--------|-------|
| Daily Active Users | 100K (10% of registered) |
| Throughput target | 18K TPS |
| Peak request rate | 150 req/sec |
| GPU nodes required | ~30x H100 |

**Infrastructure cost drivers:**
- Single H100: ~$3/hour
- At scale, GPU costs reach millions per month
- 70B parameter model at FP16 requires ~140 GB VRAM (2x H100 minimum)
- 405B model at FP16 requires ~810 GB VRAM (multi-node required)
- INT4 quantization reduces GPU memory 4x -- allows 70B model on single 80GB GPU with 1-4% perplexity increase

---

## 3. Distributed Resilience & State

### Conversation Persistence & Recovery

**Storage architecture:**

| Data Type | Storage | Rationale |
|---|---|---|
| Conversations & messages | PostgreSQL / DynamoDB | Relational queries, user ownership, pagination |
| Active session state | Redis (24-hour TTL) | Low-latency retrieval for ongoing conversations |
| Embeddings / memory | Pinecone / pgvector / Weaviate | Similarity search for long-term memory |
| Model weights | S3 / GCS | Large versioned binary blobs |
| Logs & telemetry | ClickHouse / BigQuery | High-cardinality analytics |
| Real-time metrics | Prometheus + Grafana | Operational dashboards |

**Logging pipeline:** Structured logs flow through Kafka -> Flink -> ClickHouse for real-time anomaly detection and post-hoc analysis.

**Database sharding:** Shard by conversation ID once message volume reaches millions. Write-heavy workload (chat messages) benefits from Cassandra/DynamoDB over traditional SQL at scale.

**Mid-conversation failure recovery (Claude's approach):**
- If output broke off under ~50 tokens: client silently discards partial message, re-issues request. User never sees the half-rendered output.
- If more than ~50 tokens were already streamed: partial output is inserted into history as an assistant turn with a system note ("the previous reply was interrupted, please continue from where you left off"), and stream resumes.
- This approach reduced perceived disconnect rate from ~30% of sessions to under 10%.

### Multi-Region Deployment

**Active-active multi-region:**
- Anycast CDN/WAF at edge
- Envoy L7 load balancers per region
- Stateless auto-scaling API pods
- Dedicated GPU inference clusters per region (running vLLM or TGI)
- DNS-based failover (Route 53 health checks)
- Uptime target: 99.99%

**Data residency architecture (for GDPR/compliance):**
- Split system into self-contained "cells" per legal geography
- Each cell holds: storage, inference, embeddings, logs, full orchestration
- Thin global control plane maps tenants to cells, holds nothing personal
- Requests routed to home cell before any content is processed
- Boundaries enforced with account-per-cell SCPs, regional KMS keys, in-region model invocation
- Regional processing endpoints charged 10% uplift (OpenAI, from March 2026)

Critical insight: Data residency is not just a database setting. A chatbot's personal data lives in conversation stores, vector indexes, prompt logs, AND inference request bodies. Designing cells around the database alone means embeddings and model calls will quietly breach compliance.

### Connection Management

**SSE connection lifecycle:**
- Heartbeat timeout: if no bytes arrive for 30 seconds, assume connection dropped and reconnect with exponential backoff
- Client-side: AbortController for user-initiated cancellation
- Server-side: persistent heartbeat framing, client disconnect cleanup
- HTTP timeouts: configure 120s minimum, 300s for complex documents (default 30-60s will abort long generations)
- Edge relay requirements: `X-Accel-Buffering: no`, TransformStream tick flush, heartbeat cadence

**Rate limiting (multi-dimensional, Redis-backed):**
- RPM (Requests Per Minute)
- TPM (Tokens Per Minute)
- Active concurrent streams
- In-memory fallback when Redis unavailable
- Under load test: 86.4% of excess requests shed as 429 responses, protecting downstream inference

### Reliability Patterns

**Circuit breakers:** Three-state implementation (CLOSED -> OPEN -> HALF_OPEN). When inference backends exhibit high latency/errors, circuit opens to prevent cascade failures, then tentatively allows probe requests.

**Idempotency guard:** Two-phase distributed idempotency locks via Redis prevent duplicate LLM inference calls -- critical because each call carries real cost.

**Retry strategy:** Bounded exponential backoff with full jitter prevents thundering herd on recovery.

**Chaos testing:** Kill inference nodes, simulate network partitions, inject latency to validate failover paths.

### Autoscaling Strategies

- **Predictive**: Scale based on historical traffic patterns (pre-scale before US business hours)
- **Reactive**: Kubernetes HPA watches GPU utilization and inference queue depth
- **Spot instances**: Non-critical batch workloads at 60-70% discount
- **Reserved capacity**: Production inference on reserved instances for guaranteed availability
- **Cold start mitigation**: Loading 70B model into GPU memory takes 30-90 seconds; mitigate with pre-warming on standby nodes

---

## 4. Enterprise Security & Governance

### User Authentication & Session Management

- **JWT-based auth** with Bearer tokens verified on every request
- **OAuth 2.0 / OIDC**: Social login (Google, GitHub, Microsoft)
- **API keys**: Long-lived, scoped permissions for enterprise customers
- **RBAC roles**: `free`, `pro`, `enterprise`, `admin` -- unlock different models, rate limits, features
- **Session management**: Access tokens (15 min TTL), refresh tokens (7 days), HTTP-only cookies
- **Abuse prevention**: Behavioral analysis, CAPTCHAs, credential stuffing detection, bot detection

### Content Moderation & Safety Filters

**Pre-inference pipeline:**
- Input classifiers (lightweight distilled BERT variants) with sub-millisecond latency
- Pattern filtering: role-confusion language, hidden content (zero-width characters, white-on-white text), tool-call mimicry
- Modern AI gateways: Azure AI Content Safety, AWS Bedrock Guardrails, Anthropic constitutional AI filters

**Post-inference pipeline:**
- Output validation classifiers
- Content policy filter enforcement
- Structured output schema validation

### Prompt Injection Defense

Prompt injection is ranked #1 on OWASP Top 10 for LLM Applications 2025 (LLM01). Attacks surged 340% in 2026. A meta-analysis of 78 studies found adaptive attack success rates against state-of-the-art defenses exceed 85%.

**Why it's architecturally hard:** LLMs cannot distinguish between system-level instructions and user-supplied data. No single defense eliminates prompt injection. OpenAI publicly acknowledged in Feb 2026 that prompt injection in AI browsers "may never be fully patched."

**The "Lethal Trifecta" (Simon Willison, 2025):** An agent is structurally exploitable when three properties co-exist:
1. Access to private data
2. Exposure to untrusted content
3. Ability to communicate externally

Remove any one to break the attack path.

**Real-world incidents (2025-2026):**
- **EchoLeak (CVE-2025-32711):** Zero-click M365 Copilot exploit -- hidden email instructions caused Copilot to exfiltrate documents during inbox summarization
- **GitHub Copilot RCE (CVE-2025-53773):** Malicious code comments disabled user confirmations, granted unrestricted shell access
- **Crypto wallet exploit (May 2026):** Morse-code-encoded attack tricked AI wallet into $150K unauthorized transfer
- **Cursor AI (April 2026):** Coding agent deleted production database and backups in 9 seconds

**Defense architecture (defense-in-depth):**
1. **Architectural separation (Reader vs. Doer):** Agent processing untrusted content can only return structured analysis, cannot call tools. Most effective single defense.
2. **Gateway-layer control:** Every request passes through a defense point that runs outside the agent's orchestration logic (injection cannot disable it)
3. **Per-invocation security scrutiny:** Separate security service analyzes intent and destination before every tool invocation
4. **Task-scoped tool access (least privilege):** Summarization task gets read-document only; reply-drafting gets read-document + write-draft; drafts queued for human review
5. **Input filtering:** Detect role-confusion, hidden content, encoding obfuscation
6. **Output validation:** Classify generated output for policy violations

Industry readiness: Only 34.7% of organizations have deployed dedicated prompt injection defenses (Cisco State of AI Security 2026). 83% plan to deploy agentic AI but only 29% feel ready to do so securely.

### Data Retention & Privacy (GDPR)

**Regulatory landscape (binding 2026):**
- GDPR (data processing, consent, right to erasure)
- EU AI Act Article 50: mandatory transparency labelling for AI interactions (binding August 2026)
- NIST AI Risk Management Framework
- ISO 42001 (emerging standard for AI-specific controls)

**Right to erasure (Article 17 GDPR):**
- Users can request deletion of all personal data within 30 days
- Conversation data flows across multiple systems (conversation store, vector DB, prompt logs, inference logs) -- all must be addressed
- Deleted conversations may persist in backups for up to 30 days for abuse monitoring (OpenAI)
- Technical challenge: data embedded in trained model parameters cannot be simply "deleted" -- irreversible anonymization is the only compliant path for retained training data

**Data retention requirements:**
- Storing chat transcripts indefinitely violates GDPR by default
- Lead follow-up: 12-24 months after last meaningful contact is a defensible window
- Implement automated deletion -- don't rely on manual processes
- Document retention schedule in privacy policy

**GDPR vs. EU AI Act tension:** GDPR demands erasure when purpose is fulfilled (Storage Limitation Principle, Article 5(1)(e)). EU AI Act demands lengthy archival of system documentation for traceability. Resolution requires secure deletion of personal data immediately after system finalization, with anonymized data sets for compliance archival.

**Enforcement stakes:** Fines up to 4% of annual global revenue or EUR 20 million (whichever higher). EUR 2.1 billion in GDPR fines issued in 2025 alone.

**Privacy by design checklist:**
- Server location in applicable jurisdiction (EU for GDPR)
- Mandatory Data Processing Agreement (DPA) with all third-party AI providers
- DPAs must explicitly prohibit using customer data for model training
- Encryption: TLS 1.3 in transit, AES-256 at rest; customer-managed keys (CMK) for enterprise
- Row-level security in database for multi-tenant data isolation
- Record of Processing Activities maintained
- Automated user rights fulfillment (access, erasure, portability)

### Secure Infrastructure

- Read-only filesystems for model serving
- No egress network from inference containers
- Model weights loaded from encrypted pre-signed URLs
- Namespace isolation in Kubernetes for multi-tenancy
- gVisor sandboxing (not just containers) for execution isolation -- standard containers share host kernel

---

## 5. Production Failure Modes

### Mid-Conversation Failures & Recovery

**SSE connection drops:**
- Network instability, server restarts, or load balancer timeout
- Recovery: heartbeat timeout (30s no bytes -> reconnect with exponential backoff)
- Client UX: "Connection interrupted -- retrying..." (never fail silently)
- Claude's 50-token threshold: below = silent retry, above = inject context continuation note

**Inference timeouts:**
- Default HTTP timeouts (30-60s) will abort long generations
- Fix: configure 120-300s timeouts
- Long-context requests (100K+ tokens) can take 30-60s just for prefill

**Idempotency failures:**
- Network retry sends same request twice -> duplicate inference (wasted cost + potential inconsistency)
- Fix: Redis-based distributed idempotency locks with request fingerprinting

**Cascade failures:**
- Inference backend failure -> queued requests overwhelm remaining instances -> total outage
- Fix: Circuit breakers (CLOSED -> OPEN -> HALF_OPEN), bounded retry with jitter

### Context Window Overflow in Long Conversations

This is the most common silent failure in production chat systems.

**Why bigger windows don't solve it:**
- Even 200K-400K token windows are insufficient for long agent sessions
- Cost grows quadratically with conversation length
- Model attention quality degrades in the middle of very long contexts ("lost in the middle" phenomenon)
- KV-cache memory grows linearly, eventually exhausting GPU VRAM

**Production mitigation strategies (ordered by cost, cheapest first):**

1. **Sliding window**: Keep last 10-15 exchanges. Simple, fast, lossy.
2. **Token-level compression**: Remove filler words, redundant phrases. 40-60% reduction. Tools: LLMLingua.
3. **Conversation summarization**: Trigger at 70-80% context capacity. LLM summarizes older segments. Trade-off: computational overhead + potential hallucination in summary.
4. **Structured memory extraction**: Pull deterministic facts (preferences, decisions) into separate structured store. Survives summarization losslessly.
5. **RAG for conversation memory**: Embed conversation chunks, retrieve by semantic similarity at query time. Only relevant chunks loaded.
6. **Memory tiering (MemGPT-style)**: Working memory (active context) + short-term (session store) + long-term (vector DB). OS memory hierarchy applied to LLM context.
7. **Prompt caching at infrastructure level**: Cache KV computations for stable prefixes. Reduces both cost and TTFT.

**Best practice hybrid:** Keep last 6 exchanges verbatim + summarize everything older + inject summary into system prompt. Re-inject system prompt every 20-30 exchanges to maintain instruction following. Users don't notice the summarization; continuity is preserved.

**Claude Code's 5-layer context reduction pipeline (executed before every model call, cheapest first):**
1. Budget reduction -- targets individual tool outputs that overflow size limits
2. Snip -- handles temporal depth
3. Microcompact -- reacts to cache overhead
4. Context collapse -- manages very long histories
5. Auto-compact -- semantic compression as last resort

### Model Degradation Under Load

**Silent quality degradation:**
- No exception, no error code -- output quality simply declines
- Causes: GPU memory saturation, CPU bottlenecks (tokenizer threads competing with engine), thermal throttling, KV-cache exhaustion
- GPU at 40% compute utilization but 95% memory utilization -> P99 latency 8s while P50 looks healthy at 1.2s

**GPU saturation signals:**
- GPU utilization > 95% -> requests queuing, latency climbing
- GPU temperature hitting throttle threshold + simultaneous utilization drop -> thermal throttling
- KV-cache exhaustion -> sudden latency spikes and timeouts
- DRAM bandwidth saturation -> over 50% of attention kernel cycles stalled on data access

**CPU-induced degradation:**
- Under heavy load, tokenizer threads compete with LLM engine for CPU
- Frequent context switches, delayed kernel launches, reduced GPU utilization
- High CPU load can increase kernel launch latency from microseconds to milliseconds
- Result: expensive GPU infrastructure delivers fraction of expected throughput

**Quantization quality impact:**
- FP8 / INT8: < 1-2% quality degradation, imperceptible for most production use cases
- INT4: 1-4% perplexity increase, acceptable for chatbots, summarization, Q&A
- Below INT4: quality degradation becomes user-visible

**Monitoring for quality degradation:**
- Quality proxy metrics: user feedback rate (thumbs up/down), retry rate, refusal rate -- leading indicators
- Eval sampling: run LLM-as-judge on sampled outputs at regular intervals
- Correlate infrastructure metrics (GPU utilization, memory, CPU, TTFT) with quality metrics (eval scores, user feedback)
- Dashboard: top row = error rate + P95 latency + cost/hr; second row = eval scores + user feedback

**Alerting thresholds:**
- P99 latency exceeds 5s -> alert
- GPU error rates above 0.1% -> alert
- KV-cache usage approaching capacity -> proactive scaling trigger

---

## 6. Enterprise System Design Scenarios

### Enterprise Chat Assistant at Scale

**Build sequence (progressive complexity):**
1. Single API call to hosted LLM
2. SSE streaming implementation
3. System prompt configuration (persona, constraints)
4. JSON structured output support
5. Multi-turn conversation with context compaction
6. Prompt injection defense (input classifiers + output validation)
7. Persistent memory (user preferences across sessions)
8. Production hardening (rate limiting, circuit breakers, observability, multi-region)

**Three-tier scaling architecture:**
- **Connection tier**: CDN edge + API gateway, scales for persistent connectivity and low-latency feedback
- **Orchestration tier**: Stateless services for prompt assembly, moderation, routing
- **Inference tier**: GPU clusters running vLLM/TGI/Triton, scaled independently from connection tier

This separation allows scaling "connection-heavy" frontend independently from "compute-heavy" inference.

**Infrastructure stack:**
- Container orchestration: Kubernetes with NVIDIA device plugin for GPU-aware scheduling
- Service mesh: Istio or Linkerd for mTLS, traffic splitting, canary deployments
- Observability: OpenTelemetry (browser -> gateway -> orchestrator -> inference), Prometheus + Grafana dashboards
- Logging: Kafka -> Flink -> ClickHouse pipeline
- Model serving: vLLM or TGI with continuous batching, quantization (INT8/FP8)

### Multi-Tenant Chat Platform Design

**Execution pipeline (never skip steps):**
Auth -> Tenant -> Budget -> Session -> Context -> Sandbox -> LLM -> Persist

**Three isolation models:**

| Model | Description | Isolation | Cost |
|---|---|---|---|
| **Fully Siloed** | Separate infrastructure per tenant | Highest | Highest |
| **Fully Shared** | All tenants share resources, `tenant_id` filtering | Lowest (noisy neighbor risk) | Lowest |
| **Hybrid Namespace** | Shared infrastructure, namespace-level isolation | Strong | Balanced |

Hybrid namespace is the 2026 standard for modern platforms.

**Database isolation:**
- Row-Level Security (RLS) attaches access policies directly to tables
- Policy runs inside database engine on every query -- prompt injection cannot override, developer cannot accidentally omit, ORM change cannot silently strip
- Tenant + session identifiers embedded directly in storage keys

**Vector database isolation (for RAG/memory):**
- Never mix embeddings from multiple tenants in one index -- semantic search is approximate and can return wrong-tenant matches
- Use namespaces (one per tenant), stamp every vector with `tenant_id`, require filter on every query
- For regulated industries (finance, healthcare): separate indexes, not just filtering

**KV-cache side-channel attack:** Shared prefix caches can expose information through TTFT timing differences. Research at NDSS 2025 demonstrated this on Llama2-13B/A100. Partition KV caches by tenant identity.

**Performance isolation:** One tenant's burst must not starve another's throughput. Implement per-tenant rate limits, resource quotas, and queue prioritization.

**Salesforce reference (production):** Multi-tenant AI agent platform handling 7K+ concurrent sessions. Session management persists state machine, action queue, and pending confirmations via SDK, eliminating Redis serialization and TTL management. 24-hour TTL policy preserves relevant context while auto-evicting stale data.

### Trade-Off Matrices

**API-based vs. Self-hosted LLM:**

| Dimension | API-based | Self-hosted |
|---|---|---|
| GPU management | Provider handles | You manage |
| Privacy | Data sent externally | Data stays internal |
| Cost at low volume | Lower | Higher (infrastructure) |
| Cost at high volume | Scales linearly with tokens | Amortized fixed costs |
| Behavioral control | Limited by provider | Full control |
| Cold start | None (provider pre-warms) | 30-90s model loading |
| Migration path | Vendor lock-in risk | Full control |

**Self-hosting becomes economical** when token volume is high enough to amortize GPU infrastructure costs. Migration path from API: start with hosted API, move to self-hosted open-source models via vLLM/llama.cpp/SGLang when outgrowing APIs.

**SSE vs. WebSocket for token streaming:**

| Dimension | SSE | WebSocket |
|---|---|---|
| Direction | Server -> Client (unidirectional) | Bidirectional |
| Protocol | HTTP/1.1 | Upgrade from HTTP |
| Proxy/CDN compatibility | Works through standard proxies | Requires proxy configuration |
| Auto-reconnection | Built into browser EventSource API | Must implement manually |
| Complexity | Simple | More complex |
| Use case fit | Token streaming (predominant pattern) | Real-time collaboration |

Industry consensus: SSE for token streaming, WebSocket only if bidirectional real-time features are needed beyond chat.

**Context management strategies:**

| Strategy | Latency | Quality | Cost | Complexity |
|---|---|---|---|---|
| Sliding window | Lowest | Lossy (old context lost) | Lowest | Lowest |
| Summarization | Medium (LLM call) | Good (some information loss) | Medium | Medium |
| RAG memory | Medium (retrieval) | High (relevance-based) | Higher (vector DB) | Higher |
| Memory tiering | Medium | Highest | Highest | Highest |

---

## Sources

1. [System Design Newsletter - AI Chat Assistant](https://newsletter.systemdesign.one/p/ai-chat-assistant) -- Primary reference: four-layer architecture, build sequence, cost optimization, security
2. [Atharva Naik - ChatGPT System Design Architecture](https://atharvanaik.me/posts/chatgpt-system-design-architecture) -- 7-layer request flow, GPU infrastructure, database architecture, observability
3. [System Design Newsletter - ChatGPT System Design](https://newsletter.systemdesign.one/p/chatgpt-system-design) -- Conversation manager, LLM router, three-tier scaling
4. [GitHub - ai-system-design-chatgpt-1m](https://github.com/MadhavanAR/ai-system-design-chatgpt-1m) -- Reference implementation: Fastify SSE gateway, 3-tier model router, circuit breaker, capacity planning
5. [NeuralTrust - AI Gateway Architecture](https://neuraltrust.ai/blog/ai-gateway-architecture) -- LLM routing strategies, load balancing, fallback chains
6. [Claude Platform Docs - Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming) -- SSE event structure, streaming protocol
7. [How Claude Works - Architecture Deep Dive](https://traviteja.com/blog/how-claude-works-architecture/) -- Stateless architecture, KV-cache, context management
8. [Claude Code Internals - SSE Stream Processing](https://kotrotsos.medium.com/claude-code-internals-part-7-sse-stream-processing-c620ae9d64a1) -- SSE parsing, event types
9. [Arxiv - Dive into Claude Code](https://arxiv.org/html/2604.14228v1) -- Agent architecture, 5-layer context reduction pipeline, queryLoop generator
10. [Claude API Implementation Guide for Production Systems](https://zenvanriel.com/ai-engineer-blog/claude-api-implementation-guide/) -- 50-token recovery threshold, production connection recovery
11. [NVIDIA NIM LLM Benchmarking - Metrics](https://docs.nvidia.com/nim/benchmarking/llm/latest/metrics.html) -- TTFT, ITL, TPOT definitions
12. [BentoML - LLM Inference Metrics](https://bentoml.com/llm/llm-inference-basics/llm-inference-metrics) -- Prefill vs. decode phases, two-phase architecture
13. [ClickHouse - LLM Inference Latency](https://clickhouse.com/resources/engineering/llm-inference-latency) -- TTFT vs. TPS trade-offs, reporting best practices
14. [Getmaxim - Prompt Injection Defense 2026 Guide](https://www.getmaxim.ai/articles/prompt-injection-defense-for-production-ai-agents-a-complete-2026-guide/) -- Defense-in-depth architecture
15. [Zylos Research - Agentic AI Security 2026](https://zylos.ai/research/2026-05-16-agentic-ai-security-prompt-injection-defense-stack/) -- Lethal trifecta, reader vs. doer separation
16. [Microsoft Security Blog - Detecting Prompt Abuse](https://www.microsoft.com/en-us/security/blog/2026/03/12/detecting-analyzing-prompt-abuse-in-ai-tools/) -- Per-invocation security scrutiny
17. [Google Cloud - Multi-Tenant Agentic AI System](https://docs.google.com/architecture/multi-tenant-agentic-ai-system) -- Hub-and-spoke model, tenant isolation
18. [Salesforce Engineering - Multi-Tenant AI Agent Platform](https://engineering.salesforce.com/building-a-multi-tenant-ai-agent-platform-handling-7k-sessions-without-cross-team-interference/) -- 7K+ session management, state persistence
19. [CockroachDB - Multi-Tenant AI Data Isolation](https://www.cockroachlabs.com/blog/multi-tenant-ai-agents-why-data-isolation-starts-at-the-database/) -- Row-level security, database-level isolation
20. [Khaled's Blog - Multi-Region Data Residency for AI Chatbots](https://tuhidulhossain.com/blog/multi-region-data-residency-architecture-for-ai-chatbot-applications-on-aws-20260703/) -- Cell-based architecture, data residency
21. [Redis Blog - Context Window Overflow](https://redis.io/blog/context-window-overflow/) -- Production context management strategies
22. [JetBrains Research - Efficient Context Management](https://blog.jetbrains.com/research/2025/12/efficient-context-management/) -- Observation masking, hybrid approaches
23. [Agenta - Context Length Management Techniques](https://agenta.ai/blog/top-6-techniques-to-manage-context-length-in-llms) -- Summarization, compression, memory tiering
24. [OpenAI API Pricing](https://openai.com/api/pricing/) -- Current token pricing
25. [CloudZero - OpenAI API Pricing 2026](https://www.cloudzero.com/blog/openai-pricing/) -- Pricing tiers, historical trends
26. [TechGDPR - AI Data Retention Strategy](https://techgdpr.com/blog/reconciling-the-regulatory-clock/) -- GDPR vs. EU AI Act tension
27. [AWS Blog - Comprehensive LLM Inference Observability](https://aws.amazon.com/blogs/machine-learning/comprehensive-observability-for-amazon-sagemaker-ai-llm-inference-from-gpu-utilization-to-llm-quality/) -- GPU saturation, quality monitoring
28. [Conversational AI Institute - What Still Matters in 2026](https://www.conversationdesigninstitute.com/blog/how-to-build-a-conversational-ai-platform-that-scales) -- Production deployment practices, Klarna cautionary tale
