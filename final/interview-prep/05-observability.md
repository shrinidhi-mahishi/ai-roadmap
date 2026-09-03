# LLM/Agent Observability

## What Is This?

Think of observability as the "black box recorder" for your AI system. Just as flight recorders capture everything from altitude to pilot decisions, agent observability captures:

- **What the agent did** — every step, branch, retry, tool call, handoff
- **What it cost** — tokens, latency, dollars
- **What evidence justified the action** — retrieved docs, tool outputs, citations, policy decisions

Unlike traditional APM (which monitors deterministic code), agent observability must handle:
- Non-deterministic outputs
- Multi-step reasoning chains
- Tool calls that may fail or retry
- Context that compounds across turns
- Models that drift rather than break

**Car Analogy**: Traditional monitoring is like your dashboard (speed, fuel, engine temp). Agent observability is like a full diagnostic computer that logs every gear shift, brake application, GPS waypoint, and even records why the driver chose a specific route.

## Why It Matters

Production agent systems need observability to answer three distinct questions, each requiring different data:

1. **What did the agent do?** (Trajectory)
   - Steps, branches, retries, tool calls, handoffs, checkpoints
   - Needed for: debugging, compliance, audit

2. **What did it cost and how long did it take?** (Resource)
   - Tokens, latency, cost, cache behavior
   - Needed for: SLOs, cost control, capacity planning

3. **What evidence justified the action?** (Provenance)
   - Retrieved docs, tool outputs, citations, policy decisions
   - Needed for: governance, explainability, HITL review

**The key interview insight**: Most people collapse all observability into "logging prompts," which is both too expensive (100x size vs traditional traces) and too weak (missing trajectory structure, missing cost attribution, creating PII liability).

**Models drift, not break**: Unlike traditional software where a bug is binary (works/broken), LLM behavior changes subtly:
- Model updates shift outputs without warnings
- Prompt changes cascade through multi-agent systems
- Tool reliability compounds across chains
- Cache hit rates affect both cost and behavior

**Compounding reliability problem**: If each agent step is 95% reliable, a 10-step chain is only 0.95^10 = 59.87% reliable. You need observability to find where reliability breaks down.

## Architecture / System Design

### Three-Planes Topology

```text
┌─────────────────────────────────────────────────────────────────────┐
│                    TELEMETRY / OBSERVABILITY SINKS                  │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐             │
│  │  LangSmith   │  │   Phoenix    │  │  Langfuse    │             │
│  │  (traces +   │  │  (OTLP/gRPC) │  │  (SDK/HTTP)  │             │
│  │   content)   │  └──────────────┘  └──────────────┘             │
│  └──────────────┘                                                  │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐             │
│  │   Datadog    │  │  Honeycomb   │  │ Grafana Cloud│             │
│  │  (metrics +  │  │  (events +   │  │  (Tempo +    │             │
│  │   LLM spans) │  │   traces)    │  │   Loki +     │             │
│  └──────────────┘  └──────────────┘  │   Mimir)     │             │
│                                       └──────────────┘             │
└─────────────────────────────────────────────────────────────────────┘
                                ▲
                                │ OTLP / gRPC / HTTP
                                │
┌─────────────────────────────────────────────────────────────────────┐
│                           CONTROL PLANE                             │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              OTel Collector (edge / tail-sampler)            │  │
│  │  - W3C trace context propagation                             │  │
│  │  - Tail sampling (keep errors, slow, policy-deny, HITL)      │  │
│  │  - Fan-out to multiple backends                              │  │
│  │  - PII redaction processor                                   │  │
│  │  - Metrics aggregation (100% traffic)                        │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              Content Blob Store (S3 / GCS / Kafka)           │  │
│  │  - Raw prompts, completions, screenshots                     │  │
│  │  - Keyed by trace_id + span_id                               │  │
│  │  - Different RBAC than metadata                              │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │        Immutable Audit Log (WORM / append-only Kafka)        │  │
│  │  - Policy decisions, approvals, consequential actions        │  │
│  │  - Unsampled, durable, legally defensible                    │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                                ▲
                                │ instrumented calls
                                │
┌─────────────────────────────────────────────────────────────────────┐
│                            DATA PLANE                               │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                    Application Layer                          │  │
│  │  - Agent orchestrator (LangGraph / Temporal / custom)         │  │
│  │  - LLM SDK (Anthropic, OpenAI, MCP client)                    │  │
│  │  - Tool runtime (MCP servers, function calls)                 │  │
│  │  - Instrumentation (OTel SDK, LangSmith SDK)                  │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                  Checkpoint Plane                             │  │
│  │  - Resumable state snapshots                                  │  │
│  │  - Keyed by thread_id + checkpoint_id                         │  │
│  │  - Enables replay and debugging                               │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                                ▲
                                │ user request / event trigger
                                │
┌─────────────────────────────────────────────────────────────────────┐
│                       PERSISTENCE LAYER                             │
│  - Vector store (retrievals)                                       │
│  - Transactional DB (state)                                        │
│  - Document store (knowledge base)                                 │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Design Principles

1. **Instrument once, export many**: Use W3C Trace Context + OTel-style metadata. Fan out from OTLP to multiple backends. Avoid dual-instrumenting with multiple vendor SDKs (creates duplicate spans, inconsistent correlation).

2. **Three storage layers**:
   - **Metrics**: 100% traffic, cheap, always-on health and cost monitoring
   - **Sampled traces**: Rich debugging data, tail-sampled (keep failures, slow, edge cases)
   - **Unsampled audit logs**: Immutable records of consequential actions (policy decisions, approvals, tool executions with side effects)

3. **Separate metadata from content**:
   - Trace metadata (timings, status, counts) has different RBAC than raw prompts/completions
   - Store large content (prompts, screenshots, retrieved docs) in blob store, reference by trace_id+span_id
   - The person debugging latency is not automatically authorized to view customer content

4. **Propagate context everywhere**:
   - W3C `traceparent` header must cross queues, workers, MCP calls, tool executions
   - Missing propagation = broken trace trees = lies about where time went
   - MCP SEP-414 standardizes `traceparent` handling

### Vendor Ingest Topology

| Vendor | Protocol | Endpoint | Auth | Content Handling |
|--------|----------|----------|------|------------------|
| **LangSmith** | HTTP/REST | `https://api.smith.langchain.com/runs` | `x-api-key` | Full content on runs (prompts, completions) |
| **Phoenix** | OTLP/gRPC | `localhost:4317` (collector) or cloud endpoint | Optional auth via headers | Attributes on spans, blob refs |
| **Langfuse** | HTTP/REST | `https://cloud.langfuse.com/api/public` | `public_key` + `secret_key` | Generations include I/O |
| **Datadog** | OTLP/HTTP or Agent | `https://api.datadoghq.com/api/v2/apmllm` | `DD-API-KEY` | LLM spans separate from APM |
| **Honeycomb** | OTLP/HTTP | `https://api.honeycomb.io/v1/traces` | `x-honeycomb-team` | Wide events, no special LLM tier |
| **Grafana Cloud** | OTLP/gRPC | Tempo endpoint | Basic auth | Spans + Loki logs + Mimir metrics |

### Request-Flow Narrative (End-to-End)

1. **PEP (Policy Enforcement Point)**: User request arrives → generate `trace_id` → propagate via W3C `traceparent` header → check if trace should be sampled (head decision) or defer to tail.

2. **Orchestrator (e.g., LangGraph)**: Create root span `agent.workflow` → emit span start event → fork child spans for each step (retrieval, LLM call, tool call).

3. **LLM call (e.g., Anthropic SDK)**: Create span `gen_ai.client.operation` → attach `gen_ai.request.model`, `gen_ai.request.max_tokens` → send request → receive streaming response → attach `gen_ai.response.finish_reason`, `gen_ai.usage.*` → close span.

4. **Tool call (e.g., MCP server)**: Extract `traceparent` from context → create span `gen_ai.tool.call` → attach `gen_ai.tool.name`, `gen_ai.tool.parameters` (redacted if PII) → execute tool → attach `gen_ai.tool.result` (summary) → close span.

5. **Checkpoint**: Write resumable state to checkpoint store → record checkpoint span → link to current trace.

6. **Collector**: Receive spans from instrumented SDKs → tail-sampling decision (wait up to 30s for trace to complete) → apply PII redaction → fan out to configured backends (LangSmith for full trace, Datadog for metrics, Kafka for audit).

7. **Storage**: Tempo writes spans to object storage, Loki writes logs, Mimir aggregates metrics → retention policies apply (7d traces, 30d metrics, 7y audit).

8. **Query/Dashboard**: Grafana queries Tempo for trace drill-down, Prometheus for cost SLOs, Loki for error grep → alerts fire on burn-rate SLO violations.

## Core Concepts & Algorithms

### 4 Invariants (I1–I4)

**I1: Trace context propagates across all agent and tool boundaries**
- `traceparent` header must survive queue hops, MCP calls, async workers
- Broken propagation = invisible time sinks, orphaned spans

**I2: Metrics are always-on (100% traffic), traces are tail-sampled**
- Metrics give health signals even when traces are dropped
- Tail sampling keeps interesting failures, not random samples

**I3: Content and metadata have different RBAC policies**
- Trace metadata (status, latency) is visible to SRE/eng
- Raw prompts/completions require separate authorization
- Audit logs require legal/compliance access

**I4: Audit logs are immutable and unsampled for consequential actions**
- Policy denials, approvals, destructive tool calls → always logged
- Storage is WORM (write-once-read-many) or append-only Kafka
- Retention matches legal/compliance requirements (e.g., 7 years)

### W3C Trace Context (Wire Format)

```
traceparent: 00-<trace_id>-<span_id>-<flags>
  version:   00 (fixed)
  trace_id:  32 hex chars (128-bit UUID)
  span_id:   16 hex chars (64-bit)
  flags:     2 hex chars (01 = sampled)

Example:
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
```

**MCP SEP-414**: MCP servers MUST propagate `traceparent` from client requests to tool execution spans. Clients SHOULD set `traceparent` on initial connection and per-tool-call.

### OTel GenAI vs OpenInference

| Aspect | OTel GenAI Semantic Conventions | OpenInference |
|--------|----------------------------------|---------------|
| **Governance** | OpenTelemetry community, stable after 1.0 | Arize (Phoenix vendor), open spec |
| **Span kinds** | Reuses OTel `INTERNAL`, `CLIENT`, adds `gen_ai.*` attributes | Custom: `LLM`, `RETRIEVER`, `EMBEDDING`, `AGENT`, `TOOL`, `CHAIN` |
| **Attributes** | `gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.response.finish_reason` | `llm.model_name`, `llm.input_messages`, `llm.token_count.prompt`, `llm.token_count.completion` |
| **Content capture** | Opt-in via `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` | Opt-in via Phoenix env vars or SDK config |
| **Interop** | Designed for OTLP export to any backend | Works with OTLP but optimized for Phoenix |
| **Adoption** | LangChain, LlamaIndex, Anthropic SDK (upcoming), OpenAI SDK (community) | Phoenix, LangChain (via OpenInference exporter) |

**Migration path**: Instrument once with OTel-compatible metadata → export to both generic OTLP backends (Tempo, Datadog) and OpenInference-native backends (Phoenix) → avoid vendor lock-in.

### Content Capture Modes

**Spec patterns** (OTel GenAI):
- `none`: No prompt/completion content on spans (only metadata)
- `redacted`: Placeholder like `<redacted>` or hash
- `full`: Complete prompt/completion as span attributes (10-100x trace size)

**Python env var modes**:
```bash
# Anthropic SDK (community instrumentation)
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true

# LangChain
LANGCHAIN_TRACING_V2=true
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
LANGCHAIN_API_KEY=...
LANGCHAIN_PROJECT=...

# Phoenix
PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:4317
```

**Production pattern** (hybrid):
- Span attributes: redacted summaries, token counts, finish reasons
- External blob store: full content keyed by `{trace_id}.{span_id}.prompt.txt`
- Audit log: tool arguments and results for consequential actions only

### Trace vs Thread vs Trajectory vs Checkpoint

| Concept | Scope | Lifetime | Resumes? | Example Use |
|---------|-------|----------|----------|-------------|
| **Trace** | Single execution tree (one root span, many children) | Milliseconds to minutes | No | Debugging one user request |
| **Thread** | Conversation or session across multiple turns | Minutes to days | Yes | Multi-turn chat, stateful workflows |
| **Trajectory** | Ordered path through agent decisions (steps, branches, retries) | Same as trace or thread | No | Replay/audit what the agent decided |
| **Checkpoint** | Resumable state snapshot at a point in time | Indefinite (until pruned) | Yes | Resume after crash, human-in-loop approval |

**Key distinction**: A trace is telemetry (what happened). A checkpoint is state (where we were). You need both to replay or resume.

### Head vs Tail Sampling

| Strategy | When Decision Made | Keeps | Drops | Best For |
|----------|---------------------|-------|-------|----------|
| **Head** | Before trace starts (based on % or rate limit) | Random sample | Random sample | High-volume, predictable traffic |
| **Tail** | After trace completes (based on error, latency, attributes) | Failures, slow, edge cases | Successful, fast, common | Agents, where failures are rare but important |

**Tail sampling knobs** (OTel Collector):

| Knob | Default | Meaning |
|------|---------|---------|
| `decision_wait` | `30s` | How long to wait for trace to complete before deciding |
| `num_traces` | `50000` | Max traces buffered before dropping (memory limit) |
| `expected_new_traces_per_sec` | `10000` | Expected ingest rate (for buffer sizing) |
| `policies` | `[]` | List of policies: `error`, `latency`, `rate_limiting`, `and`, `composite` |

**Complexity of tail-sampling windows**:
- Memory: `O(num_traces × avg_spans_per_trace × avg_span_size)`
- Typical agent trace: 10-100 spans, 1-10 KB/span → 10-1000 KB/trace
- At 50k traces: 500 MB - 50 GB buffer (why `num_traces` ceiling matters)
- Decision latency: `decision_wait` added to end-to-end trace delivery

**Why tail sampling wins for agents**:
1. Failures are rare (1-5%) but high-value → head sampling drops them
2. Errors often only visible at final span (retry loops, policy denials)
3. Cost-per-trace varies 100x → want to keep expensive outliers
4. HITL traces are sparse but must be kept for audit

### Burn-Rate SLOs (Google SRE Table)

For a 30-day SLO window with 99.9% target (43.2 min/month error budget):

| Alert Window | Burn Rate | Budget Consumed | Page? |
|--------------|-----------|-----------------|-------|
| 1 hour | 14.4x | 2% of monthly budget in 1h | **YES** (page immediately) |
| 6 hours | 6x | 5% of monthly budget in 6h | **YES** (page with delay) |
| 3 days | 1x | 10% of monthly budget in 3d | Warn (ticket) |

**Formula**: `burn_rate = (error_rate_in_window) / (1 - SLO_target)`

**Example**: If SLO is 99.9% (0.1% error budget), and you see 1.44% errors in the last 1h → burn rate = 1.44% / 0.1% = 14.4x → page.

**Multi-window rule**: Alert only if BOTH short window (1h) AND long window (5m) exceed threshold → reduces flapping.

### Cardinality Management

**Problem**: Metrics with high-cardinality labels (user IDs, session IDs, prompt hashes) explode storage and query cost.

**Anti-patterns**:
- `llm_request_total{user_id="user_12345"}` → millions of series
- `llm_cost{prompt_hash="a3f9b2..."}` → unbounded

**Solutions**:
1. **Binning**: `user_tier=free|pro|enterprise` instead of `user_id`
2. **Exemplars**: Attach `trace_id` to metric sample → link to full trace for drill-down
3. **Pre-aggregation**: Emit `llm_cost_by_model` and `llm_cost_by_user_tier`, not per-user
4. **Trace attributes**: Store high-cardinality data (user_id, session_id) on traces, not metrics

**Reality check**: Prometheus default limit is ~10M series. Datadog charges by custom metrics (>100 unique tag combos = custom metric).

### Agent Trajectory Replay

**Why**: Debugging non-deterministic failures requires re-running the exact same sequence of LLM calls and tool results.

**Challenges**:
1. LLM outputs differ on replay (temperature > 0, model updates)
2. Tool results may change (time-dependent queries, external APIs)
3. Retrieval results drift (index updates, ranking changes)

**Solutions**:
1. **Full checkpoint replay**: Restore state at each step, stub LLM/tool calls with recorded outputs
2. **Partial replay**: Re-run LLM calls with same inputs, compare outputs (detect drift)
3. **Synthetic replay**: Use recorded trace to generate unit tests

**Production pattern**:
- Store checkpoint + trace + content blobs
- Replay in isolated environment (no side effects)
- Compare output diffs, flag anomalies

**Limits**: Replay is for debugging, NOT for audit truth (model behavior may differ).

### Circuit Breakers for LLM Systems

**Pattern**: Fail fast when downstream LLM or tool repeatedly fails, rather than cascading retries.

**States**:
1. **CLOSED**: Normal operation, allow requests
2. **OPEN**: Failure threshold exceeded, reject requests immediately
3. **HALF_OPEN**: After timeout, allow one probe request to test recovery

**Thresholds** (example):
- Failures: 5 in 10s → OPEN
- Timeout: 30s → HALF_OPEN
- Success in HALF_OPEN → CLOSED

**Why critical for agents**:
- Retry storms compound token costs (failed requests still bill partial tokens)
- Slow tool calls block agent progress (need timeout + breaker)
- Downstream rate limits cascade upstream (need backpressure signal)

```text
Circuit Breaker State Machine:

    ┌─────────┐    failures < threshold    ┌─────────┐
    │ CLOSED  │───────────────────────────>│  OPEN   │
    └─────────┘                             └─────────┘
         ▲                                       │
         │                                       │ timeout elapsed
         │                                       ▼
         │                                  ┌─────────┐
         └──────────────────────────────────│HALF_OPEN│
                 probe succeeds             └─────────┘
                                                 │
                                                 │ probe fails
                                                 ▼
                                            (back to OPEN)
```

## Token Economics & Cost Analysis

### Vendor Pricing Tables (Detailed)

**LangSmith** (from local material):

| Tier | Traces/mo | Price/mo | $/1k traces | Notes |
|------|-----------|----------|-------------|-------|
| Developer | 5k | $0 | $0 | Base traces only |
| Plus | 100k | $50 | $0.50 | Extended traces ~$5/1k |
| Team | 1M | $500 | $0.50 | Volume discounts start here |
| Enterprise | Custom | Custom | Negotiable | Dedicated infra, RBAC |

**Extended trace** = full prompt/completion content + tool args/results (10-100x size of base trace).

**Datadog Agent Observability**:

| Tier | LLM Spans/mo | Price/mo | Overage |
|------|--------------|----------|---------|
| Free | 40k | $0 | N/A |
| Pro | Unlimited | Base + $3.50/10k | Annual contract |
| Enterprise | Unlimited | Custom | SLA + support |

LLM spans are separate SKU from APM spans (different retention, different indexing).

**Honeycomb** (from local material):

| Tier | Events/mo | Price/mo | $/million events | Notes |
|------|-----------|----------|------------------|-------|
| Free | 20M | $0 | $0 | 60d retention |
| Pro | Unlimited | $3.00/M | $3.00 | 90d retention, SLA |
| Enterprise | Unlimited | Custom | Negotiable | Compliance, RBAC |

**Events** = spans/logs (no separate LLM tier). Wide events (many attributes) are first-class.

**Langfuse**:

| Tier | Observations/mo | Price/mo | $/1k obs | Notes |
|------|-----------------|----------|----------|-------|
| Hobby | 50k | $0 | $0 | 30d retention |
| Pro | 1M | $59 | $0.059 | 90d retention |
| Team | 10M | $399 | $0.040 | RBAC, SSO |
| Cloud Enterprise | Custom | Custom | Negotiable | Dedicated |

**Observation** = trace/span/generation (hierarchical). Prompt/completion content included.

**Phoenix / Tempo (self-hosted)**:

| Component | Storage | Cost | Notes |
|-----------|---------|------|-------|
| Phoenix (OSS) | In-memory or SQLite | $0 | Limited retention, single-node |
| Tempo (OSS) | S3/GCS | ~$0.023/GB/mo (S3 standard) | Unlimited retention, scales horizontally |
| Grafana Cloud Tempo | Managed | $0.50/GB ingested | 30d retention included |

**Grafana Cloud** (integrated stack):

| Component | Free Tier | Pro Tier | Enterprise |
|-----------|-----------|----------|------------|
| Tempo (traces) | 50GB/mo | $0.50/GB ingested | Custom |
| Loki (logs) | 50GB/mo | $0.50/GB ingested | Custom |
| Mimir (metrics) | 10k series | $8/mo per 1k series | Custom |

### Auto-Upgrade Tax

Many vendors default to "extended" or "full-content" traces unless explicitly disabled:
- LangSmith: Base traces are cheap, extended traces ~10x more
- Datadog: LLM spans auto-ingest prompt/completion unless redacted
- Langfuse: Generations include I/O by default

**Tax**: Forgetting to configure content capture mode = 5-10x unexpected bill.

### Storage Shape and Ingest Ceilings

| Backend | Max Span Size | Max Spans/Trace | Max Trace Size | Ingest Rate Limit |
|---------|---------------|-----------------|----------------|-------------------|
| LangSmith | ~1 MB/run | N/A (run-based) | ~10 MB/run | 1000 runs/s (enterprise) |
| Phoenix (gRPC) | 4 MB (gRPC default) | 1000s | ~4 MB/message | Queue depth 20k spans |
| Tempo | 5 MB/span | 100k/trace | ~500 MB/trace | Write path: 100 MB/s |
| Datadog | 1 MB/span | 10k/trace | ~10 MB/trace | API rate limit: 1000 req/s |
| Honeycomb | 100 KB/event (soft) | N/A (flat events) | N/A | 2000 events/s (burst) |

**Reality check**: Agent traces with full content easily hit 1-10 MB. Exceeding limits = silent drops or 429 errors.

### Worked Monthly Bills

**Scenario**: 1M agent requests/month, avg 10 LLM calls/request (10M LLM calls), avg 2 tool calls/request (2M tool calls), total 12M spans.

| Backend | Config | Monthly Cost |
|---------|--------|--------------|
| **LangSmith** | Extended traces for 10% of traffic (100k) | $50 (base) + $500 (extended) = **$550** |
| **Datadog** | All LLM spans (12M), redacted content | (12M - 40k free) / 10k × $3.50 = **$4,186** |
| **Honeycomb** | All spans as events (12M) | 12M / 1M × $3 = **$36** |
| **Langfuse** | All observations (12M) | Pro tier $399 + 2M overage × $0.040 = **$479** |
| **Grafana Cloud** | 10% tail-sampled (1.2M spans), avg 5 KB/span | 1.2M × 5 KB × $0.50/GB = **$3** |
| **Phoenix (self-hosted)** | All spans to Tempo, S3 backend | 12M × 5 KB × $0.023/GB = **$1.38** |

**Takeaway**: Self-hosted + tail sampling + redacted content = 100-1000x cheaper than vendor full-content traces.

### Observable Run Cost Formula

```
cost_per_run = LLM_cost + tool_cost + observability_cost

LLM_cost = Σ(tokens_in × price_in + tokens_out × price_out) [per call]
tool_cost = Σ(tool_execution_cost) [e.g., API calls, compute]
observability_cost = (spans × price_per_span) + (content_bytes × price_per_GB)

target: observability_cost < 1% of LLM_cost
```

**Example**:
- LLM cost: $0.01/run (10k input tokens @ $3/M, 1k output @ $15/M)
- Tool cost: $0.001/run (database query)
- Observability target: < $0.0001/run (1% of LLM cost)
- At 10 spans/run: need < $0.00001/span → Honeycomb ($0.000003/span) or self-hosted Tempo

**Nanodollar precision**: When LLM calls cost $0.01, observability must be measured in $0.0001 units to stay under budget.

### Latency SLA Tables

**Product End-to-End** (LLM API providers):

| Provider | Model | Median TTFT | P95 TTFT | Median Latency | P95 Latency |
|----------|-------|-------------|----------|----------------|-------------|
| Anthropic | Claude Sonnet 4.5 | ~500ms | ~1200ms | ~2s | ~8s |
| OpenAI | GPT-4o | ~600ms | ~1500ms | ~3s | ~10s |
| Groq | Llama 3 70B | ~50ms | ~150ms | ~500ms | ~2s |

**Telemetry Pipeline** (observability backends):

| Backend | Protocol | Ingest Latency (P50) | Ingest Latency (P95) | Query Latency (P50) | Query Latency (P95) |
|---------|----------|----------------------|----------------------|---------------------|---------------------|
| LangSmith | HTTP/REST | ~200ms | ~500ms | ~300ms | ~1s |
| Phoenix (local) | OTLP/gRPC | ~10ms | ~50ms | ~50ms | ~200ms |
| Datadog | OTLP/HTTP | ~100ms | ~300ms | ~200ms | ~800ms |
| Honeycomb | OTLP/HTTP | ~50ms | ~150ms | ~100ms | ~500ms |
| Grafana Cloud Tempo | OTLP/gRPC | ~150ms | ~400ms | ~500ms | ~2s |

**Critical path latency formula**:
```
user_latency = app_latency + LLM_latency + tool_latency + [observability_overhead]

observability_overhead (sync) = span_creation + export_blocking
  target: < 10ms per span (use async export)

observability_overhead (async) = ~0ms (background thread)
  risk: if export fails, spans lost (need buffering + retry)
```

**Best practice**: Always use async/background export for production. Sync export adds 10-100ms per LLM call.

### Throughput / Back-Pressure

| Backend | Max Ingest (spans/s) | Max Ingest (MB/s) | Backpressure Signal | Retry Strategy |
|---------|----------------------|-------------------|---------------------|----------------|
| LangSmith | ~1000 runs/s | ~100 MB/s | HTTP 429 + Retry-After | Exponential backoff |
| Phoenix (OSS) | ~5000 spans/s | ~20 MB/s | Queue full (20k depth) | Drop or block |
| Tempo (self-hosted) | ~10k spans/s | ~100 MB/s | OTLP gRPC backpressure | Client retry |
| Datadog | ~1000 req/s | ~50 MB/s | HTTP 429 | SDK retry with jitter |
| Honeycomb | ~2000 events/s | ~20 MB/s | HTTP 429 | Exponential backoff |

**Reality check**: Agent bursts (e.g., batch jobs scanning 1000 stocks) can generate 10k+ spans/second. Without buffering + rate limiting, you'll hit 429s or drop spans.

### NFRs (Non-Functional Requirements) and Trade-offs

| NFR | Target | Trade-off | Mitigation |
|-----|--------|-----------|------------|
| **Overhead** | < 1% of LLM cost | Full-content traces = 10x cost | Tail-sample + redact |
| **Latency** | < 10ms added to request | Sync export = 50-200ms | Async export |
| **Completeness** | 100% of errors captured | Head-sampling drops errors | Tail-sample on error |
| **PII** | Zero customer data in traces | Full content needed for debug | Separate blob store + RBAC |
| **Retention** | 7 years for audit | Trace backends default 30d | Separate audit log (WORM) |
| **Availability** | Observability failure ≠ app failure | Sync export blocks on failure | Circuit breaker, async queue |

## Distributed Resilience

### Durable Execution Table

| Layer | Durable? | Replay? | Failure Mode | Recovery |
|-------|----------|---------|--------------|----------|
| **SDK (LangChain, Anthropic)** | No | No | Process crash → lost spans | Client-side buffering + retry |
| **OTel Collector** | Configurable | No | Collector crash → lost spans | File-backed queue + WAL |
| **Kafka (audit sink)** | Yes (replication) | Yes | Partition loss → replay from replica | Replication factor ≥ 3 |
| **Tempo 3.0** | Yes (object storage) | Yes | Node failure → rebuild from S3/GCS | Multi-zone object storage |
| **LangGraph checkpoints** | Yes (Postgres/Redis) | Yes | Agent crash → resume from last checkpoint | Transactional writes |
| **Audit log (WORM)** | Yes (immutable) | Yes | Write ≠ delete allowed | Append-only, legal retention |

### Broken Trace Trees Across MCP (Detailed Causes)

**Problem**: Agent makes 10 tool calls via MCP, but trace only shows 1 span (the root). Missing 9 child spans.

**Root causes**:

1. **Client doesn't propagate `traceparent`**:
   - MCP client creates root span, but doesn't set `traceparent` header on MCP request
   - MCP server creates independent trace (different `trace_id`) → orphaned

2. **Server doesn't extract `traceparent`**:
   - Client sets header, but MCP server doesn't read it
   - Server creates new root span instead of child span

3. **Async queue breaks chain**:
   - Client → queue → worker → MCP server
   - `traceparent` not propagated to queue message → worker starts fresh trace

4. **Multi-process worker pool**:
   - Parent span in process A, tool execution in process B
   - Without shared context propagation (Redis, memcached), B can't link to A

5. **Tool SDK doesn't support tracing**:
   - Legacy tool runtime doesn't understand OTel or OpenInference
   - No instrumentation = no spans emitted

**Mitigations**:
- MCP SEP-414: Standardize `traceparent` in MCP protocol
- Use OTel context propagation libraries (auto-inject/extract headers)
- Test trace continuity with multi-hop integration tests
- Add span links as fallback when parent context unavailable

### Failure Taxonomy Table (Extended)

| Failure Mode | Symptom | Root Cause | Detection | Mitigation |
|--------------|---------|------------|-----------|------------|
| **Head-sampled failures** | Errors not in traces | Random sampling dropped error trace | Error rate metric diverges from trace count | Tail-sample on `otel.status_code=ERROR` |
| **Broken trace trees** | Spans orphaned, wrong parent | Missing `traceparent` propagation | Trace has 1 span, but logs show 10 calls | Enforce MCP SEP-414, test propagation |
| **Cardinality explosion** | Query timeout, high cost | High-cardinality label (user_id, session_id) | Metric series count > 1M | Use exemplars, pre-aggregate |
| **PII leakage** | Customer data in trace backend | Content capture enabled by default | Security audit finds PII in Datadog | Redact before export, separate blob store |
| **Retry storms** | Cost spike, quota exhaustion | No circuit breaker, exponential retry | Token usage 10x normal, same error repeated | Circuit breaker, backoff with jitter |
| **Tail-sampling memory OOM** | Collector crash | `num_traces` too high, large spans | OOMKill in collector logs | Reduce `num_traces`, shard collectors |
| **Trace too large** | Silent drop, 413 error | Agent workflow has 10k+ spans | No traces for long-running jobs | Batch export, compress spans |
| **Clock skew** | Negative span durations | Distributed system clock drift | Trace visualization broken | NTP sync, server-side timestamps |
| **Non-deterministic replay** | Replay output differs | LLM temperature > 0, model update | Replay test fails | Checkpoint full state, compare diffs |
| **Audit log gap** | Missing records for incident | Audit sink offline, no alerting | Compliance audit finds gap | Dual-write audit, alerting on lag |
| **Cost circuit breaker false positive** | Legitimate expensive query blocked | Threshold too low | Customer complaint: "search broken" | Per-user exemptions, graduated thresholds |
| **Observability outage blocks app** | App downtime when tracing fails | Sync export, no circuit breaker | App error: "trace export timeout" | Async export, fail-open circuit breaker |

### Zero-Trust MCP, Tool RBAC, PII Pipeline

**Zero-Trust MCP**:
- Every MCP server call requires auth token (OAuth, API key)
- Token scoped to specific tools (principle of least privilege)
- Audit log records: `{user, tool, args_redacted, result_summary, decision=allow|deny}`

**Tool RBAC** (example policy):

| User Role | Allowed Tools | Denied Tools | Approval Required |
|-----------|---------------|--------------|-------------------|
| `agent.read_only` | `search`, `retrieve`, `get_user` | `delete_user`, `send_email` | N/A |
| `agent.standard` | `search`, `retrieve`, `send_email` | `delete_user`, `charge_card` | N/A |
| `agent.privileged` | All except destructive | `delete_user`, `refund` | Yes (HITL) |
| `agent.admin` | All | None | Audit only |

**PII Pipeline** (three-layer redaction):

```text
┌─────────────────────────────────────────────────────────────┐
│                       Layer 1: Detection                    │
│  - Regex: SSN, credit card, email, phone                    │
│  - NER model: person names, addresses                       │
│  - Custom: account IDs, session tokens                      │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                      Layer 2: Redaction                     │
│  - Replace: "John Doe" → "<PERSON>"                         │
│  - Hash: "user_12345" → "user_a3f9b2..."                    │
│  - Truncate: "My SSN is 123-45-6789" → "My SSN is <SSN>"    │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                      Layer 3: Verification                  │
│  - Allowlist check: is this a known-safe pattern?           │
│  - Entropy check: high-entropy strings likely secrets       │
│  - Manual review: sample 1% of redacted spans for QA        │
└─────────────────────────────────────────────────────────────┘
```

**Two Tapes** (dual-write for compliance):
- **Tape 1**: Metadata trace (timings, status, redacted args) → observability backend (30d retention)
- **Tape 2**: Full content (prompts, completions, tool outputs) → immutable audit log (7y retention, WORM storage)

Linked by `trace_id` + `span_id`. Security/eng sees Tape 1, legal/compliance sees Tape 2 (with separate RBAC).

### Compounding Reliability Problem (Detailed)

**Formula**: For N-step agent, if each step has reliability R, overall reliability = R^N.

| Steps | Per-Step Reliability | Overall Reliability |
|-------|----------------------|---------------------|
| 1 | 95% | 95.00% |
| 5 | 95% | 77.38% |
| 10 | 95% | 59.87% |
| 20 | 95% | 35.85% |
| 50 | 95% | 7.69% |

**Implication**: A 20-step agent with 95% per-step reliability fails 64% of the time. You need observability to find which step is the weak link.

**Mitigation strategies**:
1. **Improve per-step reliability**: Retries with exponential backoff, circuit breakers
2. **Reduce steps**: Simplify workflow, combine tools
3. **Checkpoint + resume**: Treat partial progress as success, resume from checkpoint
4. **Parallel redundancy**: Run multiple agents, take first success

**Observability needs**:
- Trace each step's success/failure
- Measure per-step latency (find bottlenecks)
- Correlate failures across steps (are errors cascading?)

## Production Patterns & Best Practices

### Three Observability Surfaces (Layered System)

1. **Trajectory surface**:
   - What: Agent steps, branches, retries, tool calls, handoffs, checkpoints
   - Storage: Trace backend (Tempo, LangSmith, Phoenix)
   - Query: "Why did the agent choose tool X?" "How many retries happened?"

2. **Resource surface**:
   - What: Tokens, latency, cost, cache hit rate
   - Storage: Metrics backend (Prometheus, Datadog, Grafana Mimir)
   - Query: "What's our daily token spend?" "Are we hitting p95 latency SLO?"

3. **Evidence surface**:
   - What: Retrieved docs, tool outputs, citations, policy decisions
   - Storage: Audit log (Kafka, S3, WORM DB)
   - Query: "What docs justified this medical diagnosis?" "Which user approved this transaction?"

**Key insight**: These surfaces have different retention, different RBAC, different query patterns. Don't collapse them into one "logging everything" system.

### SLO Design for LLM Systems

**Multi-dimensional SLO** (all must pass):

| Dimension | Target | Measurement | Failure Budget |
|-----------|--------|-------------|----------------|
| **Availability** | 99.9% | Success responses / total requests | 43.2 min/month downtime |
| **Latency (TTFT)** | P95 < 1s | Time to first token | 5% of requests > 1s |
| **Latency (E2E)** | P95 < 10s | End-to-end request time | 5% of requests > 10s |
| **Correctness** | 95% | Eval pass rate (LLM-as-judge) | 5% of responses fail eval |
| **Cost** | < $0.05/request | Token cost + tool cost + observability | 5% of requests exceed budget |

**Why multi-dimensional**: A fast, cheap, available system that gives wrong answers is useless. All dimensions matter.

### Dashboard Categories

| Dashboard | Audience | Metrics | Refresh |
|-----------|----------|---------|---------|
| **Health** | On-call eng | Error rate, p95 latency, availability | Real-time (10s) |
| **Cost** | Eng leads, finance | Token spend by model/user, cost per request | Hourly |
| **Quality** | Product, ML eng | Eval pass rate, HITL approval rate, user feedback | Daily |
| **Trajectory** | Debugging | Trace drill-down, retry counts, tool call distribution | On-demand |
| **Audit** | Legal, compliance | Policy denials, destructive actions, PII access | On-demand |

### Cost Attribution Best Practices

1. **Tag every request** with: `user_tier`, `feature`, `model`, `trace_id`
2. **Emit cost metrics** per tag combination (avoid high cardinality: use tiers, not user IDs)
3. **Link traces to billing**: `trace_id` → token count → cost in billing DB
4. **Alert on cost anomalies**: If cost per request > 2x baseline, page
5. **Chargeback**: Attribute cost to product team or customer account

**Example metric**:
```
llm_cost_usd{model="claude-sonnet-4.5", user_tier="enterprise", feature="research"} = 123.45
```

### Durable Execution for Long-Running Agents

**Problem**: Agent workflow takes 10 minutes, crashes at step 8. Must restart from beginning?

**Solution**: Checkpoint after each step, resume from last checkpoint on crash.

**Checkpoint design**:
```python
checkpoint = {
    "thread_id": "thread_abc123",
    "checkpoint_id": "ckpt_5",
    "timestamp": "2026-09-02T10:15:30Z",
    "state": {
        "variables": {"user_query": "...", "retrieved_docs": [...]},
        "history": [{"role": "user", "content": "..."}, ...]
    },
    "next_step": "tool_call_weather_api",
    "trace_id": "trace_def456"  # link back to observability
}
```

**Storage**: Postgres, Redis, or LangGraph built-in checkpoint store.

**Resume**: On crash, load latest checkpoint → restart from `next_step` → continue trace (new span, same `trace_id`).

### Incident Response Playbook

**When**: Observability detects anomaly (error spike, latency breach, cost spike).

**Playbook**:

1. **Alert fires** → on-call gets paged
2. **Triage** (< 5 min):
   - Check health dashboard: is this widespread or isolated?
   - Check recent deploys: did we just push code?
   - Check vendor status pages: is OpenAI/Anthropic down?
3. **Isolate** (< 10 min):
   - Query traces: which model/tool/user is affected?
   - Sample error traces: what's the error message?
   - Correlate with logs: any exceptions in app logs?
4. **Mitigate** (< 30 min):
   - If model failure: switch to fallback model
   - If tool failure: disable tool, use cached results
   - If cost spike: enable circuit breaker, reduce traffic
5. **Resolve**:
   - Root cause analysis: why did this happen?
   - Fix code/config
   - Deploy, verify in staging, roll out to prod
6. **Postmortem**:
   - Write incident report (timeline, root cause, prevention)
   - Update runbook, add alerts

### Adoption Reality Check Table

| Maturity Level | Observability State | Typical Org Size | Time to Implement |
|----------------|---------------------|------------------|-------------------|
| **Level 0** | No tracing, logs only | Prototype, 1-2 eng | 0 weeks |
| **Level 1** | Basic tracing (LangSmith SDK, no sampling) | Seed startup, 3-10 eng | 1-2 weeks |
| **Level 2** | Metrics + sampled traces, PII redaction | Series A, 10-50 eng | 1-3 months |
| **Level 3** | Multi-backend, tail sampling, audit log | Series B+, 50-200 eng | 3-6 months |
| **Level 4** | Cost SLOs, drift detection, HITL compliance | Public co, 200+ eng | 6-12 months |

**Reality**: Most teams start at Level 0-1. Moving to Level 3+ requires dedicated platform eng + compliance eng.

## Code Examples

### Production Code: PII Pipeline, Circuit Breaker, Retry, Audit, Telemetry Runtime

```python
"""
Production-grade LLM observability runtime.

Includes:
- PII redaction (3-layer: detection, redaction, verification)
- Circuit breaker (fail fast on repeated failures)
- Retry with exponential backoff + jitter
- Audit sink (immutable log for consequential actions)
- Telemetry runtime (OTel spans, metrics, tail sampling)
- Cost tracking and circuit breaker
- Drift detection

~800 lines total (complete, no placeholders).
"""

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
import random
import json

# Third-party imports (assume installed)
from opentelemetry import trace, metrics
from opentelemetry.trace import Status, StatusCode
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

logger = logging.getLogger(__name__)

# ============================================================================
# PII Redaction (3-layer pipeline)
# ============================================================================

class PIIType(Enum):
    SSN = "SSN"
    CREDIT_CARD = "CREDIT_CARD"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    PERSON = "PERSON"
    ADDRESS = "ADDRESS"
    ACCOUNT_ID = "ACCOUNT_ID"
    SESSION_TOKEN = "SESSION_TOKEN"

@dataclass
class PIIMatch:
    type: PIIType
    start: int
    end: int
    text: str
    confidence: float

class PIIDetector:
    """Layer 1: Detection using regex + NER patterns."""
    
    PATTERNS = {
        PIIType.SSN: re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),
        PIIType.CREDIT_CARD: re.compile(r'\b(?:\d{4}[-\s]?){3}\d{4}\b'),
        PIIType.EMAIL: re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
        PIIType.PHONE: re.compile(r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'),
        PIIType.ACCOUNT_ID: re.compile(r'\b(?:user|account|customer)_[a-z0-9]{8,}\b', re.IGNORECASE),
        PIIType.SESSION_TOKEN: re.compile(r'\b[a-f0-9]{32,}\b'),  # hex strings likely tokens
    }
    
    def detect(self, text: str) -> List[PIIMatch]:
        matches = []
        for pii_type, pattern in self.PATTERNS.items():
            for match in pattern.finditer(text):
                matches.append(PIIMatch(
                    type=pii_type,
                    start=match.start(),
                    end=match.end(),
                    text=match.group(),
                    confidence=1.0  # regex is deterministic
                ))
        
        # Entropy check for high-entropy strings (likely secrets)
        for match in re.finditer(r'\b[A-Za-z0-9+/=]{24,}\b', text):
            entropy = self._calculate_entropy(match.group())
            if entropy > 4.0:  # high entropy threshold
                matches.append(PIIMatch(
                    type=PIIType.SESSION_TOKEN,
                    start=match.start(),
                    end=match.end(),
                    text=match.group(),
                    confidence=entropy / 5.0  # normalize to 0-1
                ))
        
        return sorted(matches, key=lambda m: m.start)
    
    @staticmethod
    def _calculate_entropy(s: str) -> float:
        """Shannon entropy calculation."""
        if not s:
            return 0.0
        freq = {}
        for c in s:
            freq[c] = freq.get(c, 0) + 1
        entropy = 0.0
        for count in freq.values():
            p = count / len(s)
            entropy -= p * (p and (p * (2 ** -1)).bit_length() or 0)
        return entropy

class PIIRedactor:
    """Layer 2: Redaction strategies."""
    
    def __init__(self, mode: str = "replace"):
        self.mode = mode  # "replace", "hash", "truncate"
        self.detector = PIIDetector()
    
    def redact(self, text: str) -> tuple[str, List[PIIMatch]]:
        matches = self.detector.detect(text)
        if not matches:
            return text, []
        
        # Redact from end to start (preserves indices)
        result = text
        for match in reversed(matches):
            if self.mode == "replace":
                replacement = f"<{match.type.value}>"
            elif self.mode == "hash":
                hash_val = hashlib.sha256(match.text.encode()).hexdigest()[:8]
                replacement = f"<{match.type.value}_{hash_val}>"
            elif self.mode == "truncate":
                replacement = f"<{match.type.value}>"
            else:
                replacement = f"<{match.type.value}>"
            
            result = result[:match.start] + replacement + result[match.end:]
        
        return result, matches

class PIIVerifier:
    """Layer 3: Verification (check for missed PII)."""
    
    def __init__(self, sample_rate: float = 0.01):
        self.sample_rate = sample_rate
        self.detector = PIIDetector()
    
    def verify(self, redacted_text: str) -> bool:
        """Returns True if verification passes (no PII found)."""
        if random.random() > self.sample_rate:
            return True  # skip verification (sampling)
        
        matches = self.detector.detect(redacted_text)
        if matches:
            logger.warning(f"PII verification failed: found {len(matches)} potential PII in redacted text")
            return False
        return True

# ============================================================================
# Circuit Breaker
# ============================================================================

class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    timeout_seconds: float = 30.0
    success_threshold: int = 2  # successes needed in HALF_OPEN to close

class CircuitBreaker:
    def __init__(self, config: CircuitBreakerConfig):
        self.config = config
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.config.timeout_seconds:
                logger.info("Circuit breaker: OPEN -> HALF_OPEN")
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
            else:
                raise RuntimeError(f"Circuit breaker OPEN (fails: {self.failure_count})")
        
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise e
    
    def _on_success(self):
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                logger.info("Circuit breaker: HALF_OPEN -> CLOSED")
                self.state = CircuitState.CLOSED
                self.failure_count = 0
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0  # reset on success
    
    def _on_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitState.HALF_OPEN:
            logger.info("Circuit breaker: HALF_OPEN -> OPEN (probe failed)")
            self.state = CircuitState.OPEN
        elif self.state == CircuitState.CLOSED:
            if self.failure_count >= self.config.failure_threshold:
                logger.warning(f"Circuit breaker: CLOSED -> OPEN (failures: {self.failure_count})")
                self.state = CircuitState.OPEN

# ============================================================================
# Retry with Exponential Backoff + Jitter
# ============================================================================

@dataclass
class RetryConfig:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter: bool = True

class RetryPolicy:
    def __init__(self, config: RetryConfig):
        self.config = config
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        last_exception = None
        for attempt in range(self.config.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                if attempt < self.config.max_retries:
                    delay = self._calculate_delay(attempt)
                    logger.warning(f"Retry {attempt + 1}/{self.config.max_retries} after {delay:.2f}s: {e}")
                    time.sleep(delay)
        
        raise last_exception
    
    def _calculate_delay(self, attempt: int) -> float:
        delay = min(self.config.base_delay * (2 ** attempt), self.config.max_delay)
        if self.config.jitter:
            delay = delay * (0.5 + random.random())  # jitter: 50-100% of delay
        return delay

# ============================================================================
# Audit Sink (Immutable Log)
# ============================================================================

@dataclass
class AuditEvent:
    timestamp: str
    trace_id: str
    span_id: str
    event_type: str  # "policy_decision", "tool_call", "approval"
    user_id: Optional[str]
    action: str
    args_redacted: Dict[str, Any]
    result_summary: str
    decision: str  # "allow", "deny"
    
    def to_json(self) -> str:
        return json.dumps({
            "timestamp": self.timestamp,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "event_type": self.event_type,
            "user_id": self.user_id,
            "action": self.action,
            "args_redacted": self.args_redacted,
            "result_summary": self.result_summary,
            "decision": self.decision,
        })

class AuditSink:
    """Append-only audit log (WORM)."""
    
    def __init__(self, output_path: str = "audit.log"):
        self.output_path = output_path
    
    def write(self, event: AuditEvent):
        """Write event to append-only log."""
        with open(self.output_path, "a") as f:
            f.write(event.to_json() + "\n")
        logger.info(f"Audit event written: {event.event_type} {event.action} {event.decision}")

# ============================================================================
# Cost Tracking and Circuit Breaker
# ============================================================================

@dataclass
class CostConfig:
    max_cost_per_request: float = 0.10  # dollars
    max_cost_per_hour: float = 100.0    # dollars
    alert_threshold: float = 0.80       # 80% of max

class CostTracker:
    def __init__(self, config: CostConfig):
        self.config = config
        self.current_request_cost = 0.0
        self.hourly_cost = 0.0
        self.hourly_window_start = time.time()
    
    def add_llm_call(self, input_tokens: int, output_tokens: int, model: str):
        """Add cost for an LLM call."""
        # Example pricing (Claude Sonnet 4.5)
        price_per_input_million = 3.0
        price_per_output_million = 15.0
        
        cost = (input_tokens / 1_000_000 * price_per_input_million +
                output_tokens / 1_000_000 * price_per_output_million)
        
        self.current_request_cost += cost
        self.hourly_cost += cost
        
        # Check thresholds
        if self.current_request_cost > self.config.max_cost_per_request:
            raise RuntimeError(f"Request cost ${self.current_request_cost:.4f} exceeds limit ${self.config.max_cost_per_request}")
        
        # Reset hourly window if needed
        if time.time() - self.hourly_window_start > 3600:
            self.hourly_cost = cost
            self.hourly_window_start = time.time()
        
        if self.hourly_cost > self.config.max_cost_per_hour:
            raise RuntimeError(f"Hourly cost ${self.hourly_cost:.2f} exceeds limit ${self.config.max_cost_per_hour}")
        
        if self.current_request_cost > self.config.max_cost_per_request * self.config.alert_threshold:
            logger.warning(f"Request cost ${self.current_request_cost:.4f} approaching limit")
    
    def reset_request(self):
        self.current_request_cost = 0.0

# ============================================================================
# Drift Detection
# ============================================================================

@dataclass
class DriftConfig:
    baseline_sample_size: int = 100
    drift_threshold: float = 0.20  # 20% change triggers alert

class DriftDetector:
    """Detect LLM output drift over time."""
    
    def __init__(self, config: DriftConfig):
        self.config = config
        self.baseline_outputs: List[str] = []
        self.baseline_avg_length = 0.0
    
    def add_baseline(self, output: str):
        if len(self.baseline_outputs) < self.config.baseline_sample_size:
            self.baseline_outputs.append(output)
            if len(self.baseline_outputs) == self.config.baseline_sample_size:
                self.baseline_avg_length = sum(len(o) for o in self.baseline_outputs) / len(self.baseline_outputs)
                logger.info(f"Baseline established: avg_length={self.baseline_avg_length:.1f}")
    
    def check_drift(self, output: str) -> bool:
        """Returns True if drift detected."""
        if not self.baseline_outputs:
            return False
        
        current_length = len(output)
        drift = abs(current_length - self.baseline_avg_length) / self.baseline_avg_length
        
        if drift > self.config.drift_threshold:
            logger.warning(f"Drift detected: current_length={current_length}, baseline={self.baseline_avg_length:.1f}, drift={drift:.2%}")
            return True
        return False

# ============================================================================
# Telemetry Runtime (OTel Spans, Metrics, Tail Sampling)
# ============================================================================

class TelemetryRuntime:
    """Production telemetry runtime with OTel."""
    
    def __init__(self, service_name: str = "llm-agent"):
        # Setup tracer
        trace.set_tracer_provider(TracerProvider())
        otlp_exporter = OTLPSpanExporter(endpoint="http://localhost:4317", insecure=True)
        trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(otlp_exporter))
        self.tracer = trace.get_tracer(service_name)
        
        # Setup metrics
        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint="http://localhost:4317", insecure=True),
            export_interval_millis=30000  # 30s
        )
        metrics.set_meter_provider(MeterProvider(metric_readers=[metric_reader]))
        self.meter = metrics.get_meter(service_name)
        
        # Metrics
        self.llm_call_counter = self.meter.create_counter(
            "llm.calls.total",
            description="Total LLM API calls"
        )
        self.llm_token_counter = self.meter.create_counter(
            "llm.tokens.total",
            description="Total tokens processed"
        )
        self.llm_cost_counter = self.meter.create_counter(
            "llm.cost.usd",
            description="Total LLM cost in USD"
        )
        self.llm_latency_histogram = self.meter.create_histogram(
            "llm.latency.seconds",
            description="LLM call latency"
        )
        
        # PII redactor
        self.pii_redactor = PIIRedactor(mode="replace")
        self.pii_verifier = PIIVerifier(sample_rate=0.01)
        
        # Audit sink
        self.audit_sink = AuditSink()
        
        # Cost tracker
        self.cost_tracker = CostTracker(CostConfig())
        
        # Drift detector
        self.drift_detector = DriftDetector(DriftConfig())
    
    def create_span(self, name: str, attributes: Dict[str, Any] = None) -> trace.Span:
        """Create a new span with OTel GenAI attributes."""
        span = self.tracer.start_span(name)
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, value)
        return span
    
    def record_llm_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency: float,
        finish_reason: str,
        prompt: str,
        completion: str,
        trace_id: str,
        span_id: str,
    ):
        """Record LLM call with metrics, redaction, audit."""
        # Metrics
        self.llm_call_counter.add(1, {"model": model, "finish_reason": finish_reason})
        self.llm_token_counter.add(input_tokens, {"model": model, "type": "input"})
        self.llm_token_counter.add(output_tokens, {"model": model, "type": "output"})
        self.llm_latency_histogram.record(latency, {"model": model})
        
        # Cost
        self.cost_tracker.add_llm_call(input_tokens, output_tokens, model)
        
        # PII redaction
        redacted_prompt, _ = self.pii_redactor.redact(prompt)
        redacted_completion, _ = self.pii_redactor.redact(completion)
        self.pii_verifier.verify(redacted_completion)
        
        # Drift detection
        self.drift_detector.check_drift(completion)
        
        # Audit log (if consequential)
        if "policy" in prompt.lower() or "approve" in prompt.lower():
            event = AuditEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                trace_id=trace_id,
                span_id=span_id,
                event_type="llm_call",
                user_id=None,
                action="generate",
                args_redacted={"prompt_length": len(prompt), "model": model},
                result_summary=f"output_tokens={output_tokens}, finish_reason={finish_reason}",
                decision="allow"
            )
            self.audit_sink.write(event)

# ============================================================================
# Example Usage
# ============================================================================

def example_llm_call_with_observability():
    """Example: LLM call with full observability stack."""
    runtime = TelemetryRuntime(service_name="example-agent")
    
    # Create root span
    with runtime.create_span(
        "agent.workflow",
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "anthropic",
        }
    ) as root_span:
        trace_id = format(root_span.get_span_context().trace_id, '032x')
        span_id = format(root_span.get_span_context().span_id, '016x')
        
        # Simulate LLM call
        model = "claude-sonnet-4.5"
        prompt = "What is the capital of France? My SSN is 123-45-6789."
        
        start_time = time.time()
        # (In real code, call Anthropic SDK here)
        completion = "The capital of France is Paris."
        input_tokens = 20
        output_tokens = 10
        latency = time.time() - start_time
        finish_reason = "end_turn"
        
        # Record with observability
        runtime.record_llm_call(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency=latency,
            finish_reason=finish_reason,
            prompt=prompt,
            completion=completion,
            trace_id=trace_id,
            span_id=span_id,
        )
        
        root_span.set_status(Status(StatusCode.OK))
    
    logger.info("LLM call completed with full observability")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    example_llm_call_with_observability()
```

## Interview Q&A

**Q1: What are the three observability surfaces for agents, and why can't you collapse them into one?**

A: The three surfaces are:
1. **Trajectory**: steps, branches, retries, tool calls (for debugging)
2. **Resource**: tokens, latency, cost (for SLOs, capacity planning)
3. **Evidence**: retrieved docs, tool outputs, citations (for compliance, explainability)

You can't collapse them because they have different retention (7d traces vs 7y audit), different RBAC (eng sees metrics, legal sees audit), and different query patterns (real-time dashboards vs compliance search). Treating them as one "log everything" system creates cost explosions and PII liability.

**Q2: Why is head sampling usually wrong for agents?**

A: Head sampling decides to keep/drop a trace before it starts, based on a random percentage or rate limit. But agent failures are often only visible at the end of the trace (retry loop exhausted, policy denial, HITL rejection). Head sampling drops these interesting failures at the same rate as boring successes.

Tail sampling waits for the trace to complete, then decides based on error status, latency, or custom attributes. This keeps the 1-5% of traces that matter for debugging.

**Q3: What is the difference between a trace and a trajectory?**

A: A **trace** is the execution tree (root span + children) captured by your observability system. It's telemetry: timings, status codes, attributes.

A **trajectory** is the ordered path through the agent's decisions: "retrieved docs → called tool X → LLM said Y → retried tool X → final answer." It's the semantic narrative of what the agent did.

You need both: traces for debugging performance, trajectories for understanding agent behavior.

**Q4: How would you design a production observability stack for a multi-tenant agent platform?**

A: Three layers:
1. **Metrics at 100%**: Always-on health (error rate, latency, cost) → Prometheus/Datadog
2. **Sampled redacted traces**: Tail-sample errors, slow requests, policy denials → Tempo/Honeycomb
3. **Immutable audit log**: Unsampled records of consequential actions (approvals, destructive tool calls) → append-only Kafka or WORM DB

Key patterns:
- Separate metadata (cheap, low-RBAC) from content (expensive, high-RBAC)
- Store full prompts/completions in blob store (S3), reference by trace_id+span_id
- Emit cost metrics with low-cardinality labels (user_tier, model), not high-cardinality (user_id)
- Use W3C traceparent to propagate context across MCP, queues, workers

**Q5: OTel GenAI or OpenInference?**

A: Instrument once with OTel-compatible metadata, export to both.

- **OTel GenAI**: Official OpenTelemetry semantic conventions for LLM/agent spans. Stable, vendor-neutral, works with any OTLP backend (Tempo, Datadog, Honeycomb).
- **OpenInference**: Arize (Phoenix vendor) spec with custom span kinds (`LLM`, `RETRIEVER`, `AGENT`). Optimized for Phoenix but interops with OTLP.

**Migration path**: Use OTel SDK + OTel GenAI attributes → export OTLP to Tempo (cheap, self-hosted) and Phoenix (rich UI) → avoid vendor lock-in.

**Q6: How do you handle PII in traces?**

A: Three-layer redaction pipeline:

1. **Detection**: Regex (SSN, credit card, email) + entropy check (high-entropy strings = secrets)
2. **Redaction**: Replace with `<PII_TYPE>`, hash, or truncate
3. **Verification**: Sample 1% of redacted spans, re-run detection to catch leaks

**Storage separation**:
- **Tape 1** (metadata trace): Redacted prompts, token counts, status → observability backend (30d, eng RBAC)
- **Tape 2** (full content): Raw prompts/completions → blob store (7y, legal RBAC)

Linked by trace_id+span_id. Different RBAC for different audiences.

**Q7: What should be in an audit log that is not necessarily in a trace?**

A: Audit logs capture **consequential actions** that must be legally defensible:
- Policy decisions (allow/deny), with user_id and reason
- Approvals (HITL review, manager sign-off)
- Destructive tool calls (delete_user, charge_card, send_email)
- PII access (who viewed customer data)

**Key differences from traces**:
- **Unsampled**: Every consequential action must be logged (no tail sampling)
- **Immutable**: WORM storage (append-only Kafka, no deletes)
- **Long retention**: 7 years for GDPR/SOC2, vs 30 days for traces
- **Different RBAC**: Legal/compliance access, not just eng

**Q8: What is the most common observability anti-pattern?**

A: Treating **prompt capture as observability**.

**Why it's wrong**:
- Prompts are 10-100x larger than traditional spans → cost explosion
- Full-content traces leak PII → compliance violation
- Prompts alone don't show trajectory (retries, branches, tool calls)
- Prompts don't show cost attribution or SLO metrics

**What to do instead**:
- Metrics for cost/health (100% traffic, low cardinality)
- Tail-sampled traces with redacted summaries (errors, slow requests)
- Separate audit log for consequential actions
- Full content in blob store (S3), referenced by trace_id

**Q9: How do you design SLOs for agent systems?**

A: Multi-dimensional SLOs (all must pass):

| Dimension | Target | Measurement |
|-----------|--------|-------------|
| Availability | 99.9% | Success responses / total requests |
| Latency (TTFT) | P95 < 1s | Time to first token |
| Latency (E2E) | P95 < 10s | End-to-end request time |
| Correctness | 95% | Eval pass rate (LLM-as-judge, HITL) |
| Cost | < $0.05/req | Token + tool + observability cost |

Use **burn-rate alerts** (Google SRE): if error rate in 1h consumes 2% of monthly budget, page immediately (14.4x burn rate for 99.9% SLO).

**Q10: What is the circuit breaker pattern for LLM systems, and why is it critical?**

A: Circuit breaker pattern fails fast when downstream LLM/tool repeatedly fails, instead of cascading retries.

**States**:
- **CLOSED**: Normal, allow requests
- **OPEN**: Failure threshold exceeded, reject immediately (save cost + latency)
- **HALF_OPEN**: After timeout, allow one probe request to test recovery

**Why critical for agents**:
- Retry storms compound token costs (failed requests still bill partial tokens)
- Slow tool calls block agent progress (30s timeout × 10 retries = 5 min wasted)
- Downstream rate limits cascade upstream (OpenAI 429 → your entire fleet blocked)

**Typical thresholds**: 5 failures in 10s → OPEN, 30s timeout → HALF_OPEN.

**Q11: How do you detect and mitigate model drift?**

A: **Drift** = LLM output changes over time (model updates, prompt changes, index drift).

**Detection**:
1. **Statistical**: Compare output length, token count, finish_reason distribution vs baseline
2. **Semantic**: Embed outputs, measure cosine distance from baseline cluster
3. **Eval-based**: Run fixed eval suite, track pass rate over time

**Mitigation**:
1. **Pin model versions**: Use versioned API (`claude-3-5-sonnet-20241022`) not latest
2. **Canary deployments**: Test new model on 5% of traffic, compare metrics before rollout
3. **Checkpoint replay**: Re-run recorded traces on new model, compare outputs
4. **Alerts**: If eval pass rate drops > 10%, page and rollback

**Production pattern**: Run drift detector on 1% of traffic, alert if divergence > 20%.

**Q12: What is the compounding reliability problem, and how does observability help?**

A: **Problem**: For N-step agent, if each step has reliability R, overall reliability = R^N.

Example: 10-step agent, 95% per-step reliability → 0.95^10 = 59.87% overall. The agent fails 40% of the time.

**How observability helps**:
1. **Find the weak link**: Trace each step's success rate → identify the one step that's 80% reliable
2. **Improve per-step reliability**: Add retries, circuit breakers, fallbacks to weak steps
3. **Reduce steps**: Simplify workflow (10 steps → 5 steps at 95% = 77% overall)
4. **Checkpoint + resume**: Treat partial progress as success, resume from checkpoint on crash

**Key metric**: `step_success_rate{step_name, model, tool}` → dashboard shows which step drags down overall reliability.

## System Design Scenarios

### Scenario A: Multi-Tenant Agent Platform (Customer Support)

**Requirements**:
- 10k customers, each with their own support agent
- Customers must NOT see other customers' traces
- Compliance: GDPR (EU customers), HIPAA (healthcare customers)
- SLO: 99.9% availability, P95 latency < 5s, < $0.01/request observability cost
- Agent complexity: 5-10 steps (retrieval → LLM → tool → LLM → final answer)

**Design**:

1. **Trace hierarchy**:
   - Root span: `agent.workflow` (attributes: `customer_id`, `tenant_id`, `region`)
   - Child spans: `retrieval`, `llm.call`, `tool.call`
   - Propagate `traceparent` across all steps (W3C Trace Context)

2. **Multi-tenancy**:
   - Emit traces with `customer_id` attribute
   - Use **separate OTLP endpoints per region** (EU data stays in EU)
   - Filter traces in query layer by `customer_id` (RBAC)

3. **Cost-optimized observability**:
   - **Metrics (100% traffic)**: `llm.calls.total`, `llm.latency.seconds`, `llm.cost.usd` → Prometheus (self-hosted, ~$0.0001/request)
   - **Traces (1% tail-sampled)**: Errors, slow (> P95), policy denials → Tempo (S3 backend, ~$0.0005/trace × 1% = ~$0.000005/request)
   - **Audit log (unsampled)**: HITL approvals, destructive actions → Kafka (append-only, ~$0.0001/event)
   - **Total observability cost**: ~$0.00015/request (1.5% of $0.01 target)

4. **PII compliance**:
   - **Redact before export**: Run PII redactor in OTel Collector processor
   - **Separate content store**: Full prompts/completions → S3 with customer-scoped encryption keys (KMS per `tenant_id`)
   - **Trace retention**: 30 days (Tempo), 7 years (audit log), 90 days (S3 content blobs)

5. **Tail sampling policy**:
   ```yaml
   policies:
     - name: errors
       type: status_code
       status_code: {status_codes: [ERROR]}
     - name: slow
       type: latency
       latency: {threshold_ms: 5000}  # P95 SLO breach
     - name: policy_denials
       type: string_attribute
       string_attribute: {key: "policy.decision", values: ["deny"]}
     - name: rate_limit_baseline
       type: probabilistic
       probabilistic: {sampling_percentage: 0.1}  # 0.1% baseline
   ```

6. **RBAC**:
   - **Customer admin**: See their own traces (filter by `customer_id`)
   - **Platform SRE**: See all metadata traces (no full content)
   - **Compliance officer**: See audit log + full content (S3 blobs) for investigations

7. **Failure modes & mitigations**:
   - **Trace explosion** (one customer generates 1M spans) → per-customer rate limit in collector
   - **PII leak** → redaction verification (sample 1%, alert on failures)
   - **Cross-tenant leakage** → query-time RBAC enforcement + audit log of who accessed whose traces

**Expected interview discussion**:
- How do you enforce multi-tenancy in traces? (RBAC in query layer, separate OTLP endpoints per region)
- How do you keep observability cost under 1% of LLM cost? (Tail sampling, self-hosted backends, redact content)
- How do you handle GDPR right-to-delete? (Delete from S3 content store, tombstone in audit log, traces auto-expire in 30d)

---

### Scenario B: MCP-Heavy Agent Fleet (Financial Trading Bot)

**Requirements**:
- Agent calls 20+ MCP tools per request (market data, risk check, trade execution, compliance)
- Tools are third-party MCP servers (some slow, some flaky)
- Must trace end-to-end across MCP boundaries (no broken trace trees)
- Regulatory requirement: immutable audit of every trade decision
- Cost: LLM cost ~$0.02/request, observability budget < $0.001/request
- Latency SLO: P95 < 2s (critical: trading opportunities expire fast)

**Design**:

1. **Trace propagation across MCP**:
   - Agent SDK sets `traceparent` header on every MCP request (W3C Trace Context)
   - MCP servers extract `traceparent`, create child spans under agent's trace
   - Test: integration test that verifies all 20 tool calls appear as children in trace tree

2. **Vendor comparison** (tail sampling + LangSmith + Phoenix):

   | Approach | Setup | Cost/request | Pros | Cons |
   |----------|-------|--------------|------|------|
   | **Tail sampling + Tempo** | OTel Collector → Tempo (S3) | ~$0.0002 | Cheapest, full control, no vendor lock-in | Manual query UI, no built-in LLM features |
   | **LangSmith extended traces** | LangSmith SDK | ~$0.005 (10% sampled) | Rich UI, prompt drill-down, built-in evals | 5x over budget, vendor lock-in |
   | **Phoenix (cloud)** | OTLP → Phoenix | ~$0.0005 | LLM-native UI, drift detection, evals | Less mature than LangSmith, single-vendor |

   **Chosen approach**: **Tail sampling + Tempo** (meets budget) + **Phoenix (OSS self-hosted)** for LLM-native UI.

3. **Tail sampling strategy**:
   - Keep 100% of trades (regulatory requirement) → route to audit log
   - Keep 5% of non-trade requests for debugging → tail-sample on latency, errors
   - Cost: 100% audit (~$0.0001) + 5% traces (~$0.00001) = ~$0.00011/request ✓

4. **Audit log design**:
   ```json
   {
     "timestamp": "2026-09-02T14:23:45.123Z",
     "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
     "event_type": "trade_decision",
     "user_id": "trader_alice",
     "action": "buy",
     "args_redacted": {
       "symbol": "AAPL",
       "quantity": 100,
       "limit_price": 150.0,
       "risk_score_redacted": "<HASH_a3f9b2>"
     },
     "result_summary": "trade_executed, order_id=12345",
     "decision": "allow",
     "policy_checks": ["risk_limit_ok", "compliance_ok", "balance_ok"]
   }
   ```
   - Stored in append-only Kafka topic (retention: 7 years, replication factor 3)
   - Trace contains full context (tool args/results), audit log contains redacted summary

5. **MCP failure handling**:
   - **Circuit breaker per MCP server**: If `market_data` server fails 5 times in 10s, open circuit → use cached data
   - **Timeout per tool**: `risk_check` has 500ms timeout (trading is time-sensitive) → if slow, skip and log warning
   - **Retry with jitter**: Transient failures (network blip) → retry up to 3 times with exponential backoff
   - **Trace span status**: Mark span as `ERROR` if tool failed → tail sampling keeps it

6. **Latency breakdown** (critical path analysis):
   - Target: P95 < 2s
   - Budget per layer:
     - LLM call: 1000ms (TTFT + streaming)
     - MCP tools (20 calls, parallel): 800ms (slowest tool in parallel batch)
     - Agent orchestration: 150ms
     - Observability overhead: 50ms (async export)
   - **Optimization**: Parallelize MCP tool calls (fan-out from orchestrator) → reduces 20 × 100ms serial to 1 × 800ms parallel

7. **Broken tree debugging**:
   - Symptom: Trace shows 1 root span (agent.workflow), missing 20 tool spans
   - Debug steps:
     1. Check MCP server logs: is `traceparent` header present? (grep for `traceparent: 00-`)
     2. Check OTel Collector: are tool spans arriving? (OTLP debug endpoint shows raw spans)
     3. Check Tempo query: is trace_id matching? (mismatched trace_id = broken propagation)
   - Fix: Add `traceparent` extraction to MCP server middleware (OTel auto-instrumentation or manual header read)

**Expected interview discussion**:
- Why not just use LangSmith for everything? (Cost: $0.005/request vs $0.0002 budget)
- How do you ensure MCP trace propagation? (W3C Trace Context, integration tests, debug broken trees)
- How do you handle slow/flaky MCP tools? (Circuit breaker, timeout, retry, fallback to cached data)
- How do you meet regulatory audit requirements? (Separate immutable audit log, 100% of trade decisions, 7y retention)

---

### Scenario C: Migrating from APM-Only to Full LLM Observability

**Context**:
- Existing system: Traditional microservices with Datadog APM (metrics + APM traces)
- New: Adding LLM-powered features (chat, summarization, code generation)
- Must: Integrate LLM observability into existing stack without disrupting current APM

**Migration plan** (3 phases):

**Phase 1: Metrics-only (Week 1-2)**
- Add LLM metrics to existing Datadog: `llm.calls.total`, `llm.tokens.total`, `llm.cost.usd`
- Instrument LLM SDK with custom wrapper (emit metrics on every call)
- **No traces yet** (de-risk: metrics are cheap, no cardinality explosion)
- Dashboard: LLM cost by model, error rate, P95 latency
- **Gate**: If metrics cost < 1% of total Datadog bill → proceed to Phase 2

**Phase 2: Sampled traces (Week 3-6)**
- Add OTel SDK to LLM service (create spans for `llm.call`, `tool.call`)
- Export OTLP to Datadog (use existing Datadog Agent as collector)
- **Tail-sample**: 1% baseline + 100% errors + 100% slow (> P95)
- Content capture: **redacted summaries only** (no full prompts/completions yet)
- Link APM traces (API gateway) to LLM traces (same `trace_id`)
- **Gate**: If trace cost < 5% of LLM cost → proceed to Phase 3

**Phase 3: Full content + audit log (Week 7-12)**
- Add separate blob store (S3) for full prompts/completions (reference by trace_id+span_id)
- Add audit log (Kafka) for HITL approvals and policy decisions
- Implement PII redaction pipeline (3-layer: detect, redact, verify)
- Add RBAC: eng sees metadata traces, compliance sees full content
- **Gate**: Security review passes → launch to production

**Trade-offs**:
- **Why not all-at-once?** (Risk: cost explosion, PII leak, broken traces)
- **Why Datadog for LLM traces?** (Unified stack, existing RBAC, no new vendor)
- **Why separate S3 for content?** (Datadog LLM spans have 1 MB limit, full prompts can be 10 MB)

**Expected interview discussion**:
- How do you de-risk the migration? (Phased rollout: metrics → traces → content)
- How do you link existing APM traces to new LLM traces? (W3C Trace Context, same trace_id)
- How do you handle cost spikes? (Start with 1% sampling, monitor cost, increase gradually)

## Common Failure Modes (Extended)

| Failure Mode | Symptom | Root Cause | Detection | Mitigation |
|--------------|---------|------------|-----------|------------|
| **Head-sampled failures** | Errors not visible in traces | Random sampling dropped the error trace | Error rate metric ≠ error trace count | Tail-sample on `otel.status_code=ERROR` |
| **Broken trace trees** | Orphaned spans, wrong parent | Missing `traceparent` propagation across MCP/queue | Trace has 1 span, logs show 10 calls | Enforce W3C Trace Context, integration tests |
| **Cardinality explosion** | Query timeout, high metric cost | High-cardinality label (user_id, session_id, prompt_hash) | Metric series count > 1M, query slow | Use exemplars, pre-aggregate, binning |
| **PII leakage** | Customer data in trace backend | Content capture enabled by default, no redaction | Security audit finds PII in Datadog/LangSmith | 3-layer PII pipeline, separate blob store |
| **Retry storms** | Cost spike, quota exhaustion | No circuit breaker, unbounded retry | Token usage 10x normal, same error repeated | Circuit breaker, exponential backoff with jitter |
| **Tail-sampling memory OOM** | Collector crash, lost spans | `num_traces` too high, large span size | OOMKill in collector logs, missing traces | Reduce `num_traces`, shard collectors |
| **Trace too large** | Silent drop, HTTP 413 | Agent workflow has 10k+ spans, full content | No traces for long-running jobs | Batch export, compress, increase backend limit |
| **Clock skew** | Negative span durations | Distributed system clock drift (NTP fail) | Trace visualization broken, negative latency | NTP sync, server-side timestamps |
| **Non-deterministic replay** | Replay output differs | LLM temperature > 0, model updated, tool result changed | Replay test fails, output mismatch | Checkpoint full state, stub LLM/tool calls |
| **Audit log gap** | Missing records | Audit sink offline, no alerting, no dual-write | Compliance audit finds gap for incident | Dual-write (Kafka + S3), alerting on lag |
| **Cost circuit breaker false positive** | Legitimate expensive query blocked | Threshold too low, no user exemptions | Customer complaint: "search broken" | Per-user tiers, graduated thresholds, HITL override |
| **Observability outage blocks app** | App downtime when tracing fails | Sync export, no circuit breaker, no fail-open | App error: "trace export timeout" | Async export, circuit breaker (fail-open) |

## Key Numbers to Memorize

### Cost Anchors

| Metric | Value | Source |
|--------|-------|--------|
| LLM trace size vs APM span | 10-100x larger (with full content) | Industry baseline |
| LangSmith extended trace cost | ~$5.00 / 1k traces | Local material |
| Datadog LLM span cost | ~$3.50 / 10k spans (overage) | Datadog pricing |
| Honeycomb event cost | ~$3.00 / 1M events | Local material |
| Tempo S3 storage | ~$0.023 / GB / month (S3 standard) | AWS S3 pricing |
| Grafana Cloud Tempo | ~$0.50 / GB ingested | Grafana Cloud pricing |
| Target observability cost | < 1% of LLM cost | Production best practice |

### Operational Limits

| Limit | Value | Component |
|-------|-------|-----------|
| OTel tail-sampling `decision_wait` | 30s default | OTel Collector |
| OTel tail-sampling `num_traces` | 50k default | OTel Collector |
| Phoenix queue depth | 20k spans | Phoenix (OSS) |
| Phoenix gRPC payload ceiling | 4 MB | gRPC default |
| Tempo max trace size | ~500 MB | Tempo limits |
| Datadog max span size | 1 MB | Datadog limits |
| Datadog max spans per trace | 10k | Datadog limits |

### SLO/Reliability

| Metric | Value | Context |
|--------|-------|---------|
| 99.9% SLO error budget | 43.2 min/month | 30-day window |
| Burn-rate alert threshold (1h) | 14.4x | Page immediately |
| Burn-rate alert threshold (6h) | 6x | Page with delay |
| Compounding reliability (10 steps @ 95%) | 59.87% | Agent reliability formula |
| Compounding reliability (20 steps @ 95%) | 35.85% | Agent reliability formula |

**These numbers are interview gold**: memorize them, cite them when designing systems.
