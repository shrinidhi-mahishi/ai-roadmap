# Topic 14: Observability

## What Is This?

**Observability** for AI agents means being able to see what the agent did, why it did it, and how long each step took. You can't debug what you can't see — and agents are especially hard to debug because they make autonomous decisions across multiple steps.

A **trace** is the core concept: it's a complete record of everything that happened during one agent run. Think of it like a flight recorder — it captures every LLM call (input prompt, output response, tokens used, latency), every tool call (which tool, what arguments, what result), and every decision point (why the agent chose action A over action B).

For traditional web apps, observability means metrics (request rate, error rate) and logs (what happened). For AI agents, you also need:
- **Token tracking**: How many tokens did each LLM call use? (This is your cost.)
- **Trajectory replay**: What path did the agent take through its tools? (Was it efficient or did it loop?)
- **Quality signals**: Was the output good? (Did the user thumbs-up or thumbs-down?)
- **Prompt/response pairs**: What exactly did the model see and say? (Essential for debugging wrong outputs.)

A simple example: Your customer support agent gives a wrong answer. Without observability, you have no idea why. With observability, you can pull up the trace and see: "Ah, the retrieval step returned an outdated FAQ document, so the model gave advice based on our old policy."

## Why It Matters

In production, things break in ways you don't expect. An agent that worked perfectly in testing might fail on real user queries. Observability is how you find and fix these problems — and how you prove to stakeholders that your AI system is working correctly.

---

## 2. Core Concepts

### 2.1 The Two-Plane Model

**Control plane:** Collectors, sampling decisions, RBAC policies, audit logs of who viewed which trace, spend caps, retention rules. Runs on the collector's clock and fails independently of your app. When overloaded, it returns 429 and expects SDK retry with exponential backoff.

**Data plane (telemetry):** Span trees, metrics, structured logs. Runs on user-facing SLO clocks like time-to-first-token and end-to-end latency. Storage is Tempo, Honeycomb, LangSmith, Phoenix, Datadog.

**Data plane (content blobs):** Prompts, completions, tool I/O, retrieved documents, screenshots. Independent TTL and IAM from the span metadata. Stored in object stores, eval datasets, or encrypted buckets with span pointers.

Teams that mix these planes—putting full user prompts on span attributes, then deriving Prometheus labels from `user.id`—simultaneously leak PII, explode cardinality, and sample away the only traces they later need for compliance or debugging.

### 2.2 Three Observability Surfaces

**Trajectory observability:** How the workflow progressed: steps, branches, retries, tool calls, resume points. Not just free-form messages but structured state transitions (ReAct loops, plan/execute boundaries, verifier/rewrite branches, LangGraph super-steps). Minimum durable unit: before and after each tool, verifier, or branch decision.

**Resource observability:** Tokens consumed, latency, cost, cache behavior. Frameworks expose this as first-class signals: `input_tokens`, `output_tokens`, `cached_tokens`, `reasoning_tokens`, `cache_write_tokens`, flow usage metrics.

**Evidence observability:** What tool outputs, retrieval results, references, or activity logs justified the run. For RAG/research agents, this means query rewrites, candidate sets, reranking decisions, citations. Strongest when the agent exposes the intermediate evidence path, not just the final answer.

### 2.3 Trace, Thread, Trajectory, Checkpoint

Four objects people conflate:

| Object | What it stores | Purpose |
|--------|---------------|---------|
| **Trace tree** | Nested spans/runs for one invocation | "Which child timed out?" |
| **Thread** | Sequence of traces sharing `thread_id` | Multi-turn session |
| **Trajectory** | Deduped ordered messages (human/AI/tool) plus state transitions | Scan the conversation; inspect reasoning loops |
| **Graph checkpoint** | Full state snapshot at each super-step | Time-travel, fork, resume; not the same as a span |

**Trajectory as structured state transitions:** ReAct exposes a `reason → act → observe` loop. Planner/executor systems expose plan and execution boundaries. Verifier/rewrite loops expose retry branches. LangGraph persists checkpoints at super-step boundaries. A trajectory can be monitored as steps, branches, retries, tool calls, and resume points—not only as free-form message transcripts.

### 2.4 Framework-Specific Observability Surfaces

Runtime topology determines the natural observability unit:

**LangGraph:** Graph nodes, super-steps, checkpoints, pending writes. Replay from `checkpoint_id` re-executes nodes after that point—LLM calls, tools, interrupts fire again and may differ. Checkpoint durability modes: `sync`, `async`, `exit` trade stronger persistence for more overhead.

**OpenAI Agents SDK:** Turns, tool spans, handoff spans, guardrail spans, session/run state.

**Google ADK:** Session events, State/Memory boundaries, compaction/artifact behavior, usage metadata.

**CrewAI:** Flow state, routed methods, human-feedback pauses, aggregated flow metrics.

### 2.5 Observable Run Cost Formula

```text
observable_run_cost
  ~= model_input_cost
   + cached_read_cost
   + cache_write_cost
   + output_cost
   + reasoning_token_cost (o1/o3)
   + tool_or_retrieval_surcharges
   + trace / checkpoint persistence overhead
   + eval LLM-as-judge cost (if online)
```

**Token overhead from observability:** Tool schemas, policy prefixes, approval prompts, tracing metadata, retrieval logs, and browser/computer tool declarations all consume context. Anthropic browser-tool declarations add roughly 6,610-6,670 input tokens and computer-tool declarations add roughly 4,520-4,590 input tokens before screenshots or task content.

### 2.6 Critical Path Latency Formula

```text
critical_path_latency
  ~= planning_llm_call
   + max(parallel_branch_durations)
   + verification_llm_call
   + approvals (human or HITL)
   + trace / checkpoint persistence
   + network hops (MCP, tools)
```

Measure with span trees, not summed durations. Parallel tool calls count as `max()`, not `sum()`.

## 3. How It Works

### 3.1 W3C Trace Context

W3C Trace Context (REC 2021-11-23) is the wire format every other layer rides on:

```
traceparent: 00-{32 hex trace-id}-{16 hex parent-id}-{2 hex flags}
example:     00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01
```

- Version `00`. Trace ID 16 bytes, parent/span ID 8 bytes. Flag `01` = sampled.
- `tracestate` is vendor-opaque key=value list; intermediaries MUST forward both headers.
- OTel tail sampling can write probability sampling fields (`rv`, `th`) into the `ot` section of `tracestate`.

**MCP exception to HTTP headers:** MCP SEP-414 documents carrying `traceparent` / `tracestate` / `baggage` in JSON-RPC `_meta` so stdio/SSE/HTTP all propagate the same context. Without this, the agent's `execute_tool` span and the MCP server's `tools/call` span are two traces. The MCP Python SDK's OTel middleware emits SERVER spans and asserts shared `trace_id` on client+server.

### 3.2 OpenTelemetry GenAI Semantic Conventions

GenAI SIG formed April 2024 under Semantic Conventions SIG. Scope grew from LLM client spans to six layers: client spans, agent/workflow spans, MCP, content capture, metrics, evaluation events.

**Authoritative home moved in 2026.** Core `semantic-conventions` v1.42.0 (2026-06-12) deprecated and moved all `gen_ai.*` content; v1.43.0 ships none. The dedicated repo is `open-telemetry/semantic-conventions-genai`. As of July 2026 **no GenAI-specific span/event/metric/attribute is Stable**—all Development. Shared core attrs (`error.type`, `server.address`) are Stable; `gen_ai.*` is not. Opt-in: `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`.

#### Key milestones

| Semconv version | What landed |
|----------------|-------------|
| v1.37 (Aug 2025) | `gen_ai.system` → `gen_ai.provider.name`; per-message events → aggregated `gen_ai.input.messages` / `output.messages` / `system_instructions` |
| v1.38 | Evaluation event; tool definitions; `invoke_agent` kind guidance |
| v1.39 | MCP semantic conventions |
| v1.40 (Feb 2026) | Retrieval spans; cache token attributes; `gen_ai.agent.version` |
| v1.41 (Apr 2026) | `execute_tool {tool.name}` naming; reasoning tokens; `invoke_workflow`; streaming metrics; `invoke_agent` CLIENT vs INTERNAL |

#### LLM / client spans

- **Kind:** CLIENT (MAY be INTERNAL for in-process models). **Name:** `{gen_ai.operation.name} {gen_ai.request.model}`.
- **`gen_ai.operation.name`:** `chat` | `text_completion` | `generate_content` | `embeddings` | `execute_tool` | `create_agent` | `invoke_agent` | `invoke_workflow` | `retrieval`
- **Required-class attrs:** `gen_ai.provider.name`, `gen_ai.operation.name`, `gen_ai.request.model`.
- **Usage:** `gen_ai.usage.input_tokens` (includes cached), `gen_ai.usage.output_tokens`, plus cache-read/cache-creation splits.
- **`gen_ai.response.model`** is what actually served.
- **`gen_ai.response.finish_reasons`:** `stop` | `tool_calls` | `length` | `content_filter`.
- **Content is off by default.** Only metadata (model, tokens, duration) unless opted in. Gate commonly `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`. Three recording modes: none; span attributes (size-limited, inherits trace ACL); external blob + URL on the span (recommended for production volume/PII).

#### Tool spans

Kind INTERNAL. From v1.41 name MUST be `execute_tool {gen_ai.tool.name}`. `gen_ai.tool.call.id` recommended; `gen_ai.tool.call.arguments` and `.result` are opt-in when privacy policy permits. Auto-instrumentors see the model's request for a tool; the function body is your code—wrap it or you get a chat span with `finish_reason=tool_calls` and a missing child.

#### Agent spans

`invoke_agent`: CLIENT for remote agent APIs, INTERNAL for in-process frameworks (LangGraph). `invoke_workflow` for predetermined DAGs. `create_agent` for hosted agent create. Honeycomb Timeline counts LLM calls as `operation.name in {chat, generate_content, text_completion}` and tools as `execute_tool`.

#### MCP layer

Client `tools/call` + server `tools/call`; attributes `mcp.method.name`, `mcp.session.id`, `rpc.system=mcp`. If outer GenAI already has `execute_tool`, MCP enriches rather than duplicating. Metrics: `mcp.client/server.operation.duration`, `mcp.client/server.session.duration`.

#### Metrics (always-on, content-free)

Client histograms in active use: `gen_ai.client.token.usage`, `gen_ai.client.operation.duration`. Datadog's equivalent is first-class: `ml_obs.*` metrics are computed from 100% of traffic, retained like ordinary Datadog metrics (15 months at full granularity), even when traces are sampled. That split—100% metrics, sampled traces—is the production default.

### 3.3 OpenInference

OpenInference is a semantic convention on top of OTel, not a competing protocol. Transport is OTLP. Required attribute: `openinference.span.kind` in ALL CAPS.

| Kind | Meaning |
|------|---------|
| `LLM` | Model API call: messages, params, token counts |
| `AGENT` | Reasoning step; may spawn LLM/TOOL/RETRIEVER children |
| `CHAIN` | Deterministic sequence (prompt format, post-process) |
| `TOOL` | Function/API the model asked to run |
| `RETRIEVER` | Vector/search query |
| `RERANKER` | Reorder candidates |
| `EMBEDDING` | Vector generation |
| `GUARDRAIL` | Input/output moderation |
| `EVALUATOR` | LLM-as-judge / code scorer |
| `PROMPT` | Named template invocation |
| `UNKNOWN` | Fallback |

Attributes are flattened (`llm.input_messages.0.message.role`) because OTel attributes are flat K/V. Common: `input.value` / `output.value`, `session.id`, `user.id`.

**Phoenix specifics:** Listens OTLP/gRPC 4317 and OTLP/HTTP on UI port 6006 (`/v1/traces`)—not generic 4318. Spans > 4 MB hit gRPC message limits (full docs, base64 images). Production: `TraceConfig(hide_inputs=True, ...)` or `OPENINFERENCE_HIDE_INPUTS=true`. Queue back-pressure: `PHOENIX_MAX_SPANS_QUEUE_SIZE` default 20,000.

**Mapping (do not treat as 1:1 identity):** OTel `gen_ai.operation.name=chat` approx OpenInference `LLM` approx Datadog `LLM` approx LangSmith run type `llm`. OTel `execute_tool` approx OpenInference `TOOL` approx Datadog `tool`. OTel `invoke_agent` approx OpenInference `AGENT` approx Datadog `agent`. OpenInference `CHAIN` approx Datadog `workflow` approx OTel `invoke_workflow`.

### 3.4 Vendor Topology Comparison

**LangSmith:** Runs (approx OTel spans) nest into a trace (one operation); threads group traces across turns; trajectory is a flat, ordered message list projected from the thread, with run nesting removed. Trajectory requires `thread_id` plus `ls_agent_type: "root"` on the turn's top run; `subagent` / `middleware` / `compaction` change what Messages view shows. Hard limit: 25,000 runs per trace.

**Enterprise hosting:** Cloud (vendor holds both), Hybrid (SaaS control plane + self-hosted data plane), Self-Hosted (your VPC).

**Datadog Agent Observability:** LLM / workflow / agent / tool / task / embedding / retrieval span kinds, with agent traces rooted on an agent span. SDK sampling decides on the root LLM-obs span and applies to all children including downstream APM via distributed tracing.

**Phoenix:** OTel-native. OTLP in, OpenInference span kinds for UI. Self-hosts the whole stack.

**Honeycomb Agent Timeline:** Binds conversations with `gen_ai.conversation.id`, swim-lanes by `gen_ai.agent.name`. Conversation metrics (duration, trace count, LLM calls, tool calls, failures, total tokens) + GenAI panel (provider, request/response model, tool type, call id). Failures depend on `error.type` and span status—if tools swallow exceptions, Timeline "Show Failures Only" is empty.

**Fan-out, don't dual-instrument:** LangSmith documents the production pattern: app emits OTLP once → OpenTelemetry Collector → LangSmith OTLP endpoint AND a second backend. Never instrument the same code path twice with vendor-specific SDKs.

### 3.5 Collector Topology

Head sampling (SDK `TraceIdRatioBased`) is cheap and wrong for agents: the interesting bit (tool error, 40-step loop, content_filter) is known only at the tail.

**Canonical two-tier OTel layout:**

```
┌─────────────┐
│  SDK/Agent  │
└──────┬──────┘
       │ OTLP
       v
┌─────────────────────┐
│ Edge Collector      │
│ - memory_limiter    │
│ - k8sattributes     │
│ - batch             │
│ - loadbalancing exp │
│   (traceID routing) │
└──────┬──────────────┘
       │
       v
┌──────────────────────┐
│   Kafka (optional)   │
│ partition by traceID │
└──────┬───────────────┘
       │
       v
┌─────────────────────────┐
│ Sampling Collector      │
│ - tailsamplingprocessor │
│   * status_code         │
│   * latency             │
│   * ottl_condition      │
│   * probabilistic       │
└──────┬──────────────────┘
       │
       v
┌────────────────────┐
│ Backend (Tempo/    │
│ LangSmith/Phoenix/ │
│ Honeycomb/Datadog) │
└────────────────────┘
```

1. **Edge / gateway collectors:** `memory_limiter` first, `k8sattributes`, batch; loadbalancing exporter with `routing_key: traceID` (DNS/k8s resolver to the sampling tier).
2. **Sampling tier:** `tailsamplingprocessor`. All spans of a trace MUST hit the same instance. Default `decision_wait=30s`, `num_traces=50000`. Policies: `status_code` (keep ERROR), `latency`, `ottl_condition` (e.g. `gen_ai.usage.input_tokens`), `probabilistic` for the rest, `composite` with per-policy rate allocation, `bytes_limiting` / `rate_limiting` token buckets for overload.

**Kafka as the buffer between tiers:** `kafkaexporter` `partition_traces_by_id: true` (default false) sets the record key to the hex trace ID so a partition maps to one sampling collector. Mutually exclusive with `message_key_from_metadata_key`.

**Honeycomb Refinery** is the same idea as a product: trace-aware tail proxy; dynamic / EMA dynamic / rules / throughput samplers; sampled-before-ingest events do not count toward EPM. Use `root.` prefix on field lists or concatenated span values explode the sampler key.

**Datadog SDK sampling** decides on the root LLM-obs span and applies to all children including downstream APM via distributed tracing; billing is span-volume based.

### 3.6 Logging: Structured, Correlated, Not a Second Prompt Dump

OTel log model fields that matter: `TraceId`, `SpanId`, `TraceFlags`, `Body`, `Attributes`, resource. SDKs inject IDs when a span is active. JSON-only enrichment; plaintext is not correlated.

**Minimum structured event for an LLM call (control-safe):**

```json
{
  "event": "llm_call_complete",
  "trace_id": "0af7651916cd43dd8448eb211c80319c",
  "span_id": "b7ad6b7169203331",
  "gen_ai.provider.name": "openai",
  "gen_ai.request.model": "gpt-4o-2024-11-20",
  "gen_ai.response.model": "gpt-4o-2024-11-20",
  "input_tokens": 1245,
  "output_tokens": 387,
  "cache_read_tokens": 823,
  "cache_creation_tokens": 0,
  "finish_reason": "stop",
  "latency_ms": 2340,
  "ttft_ms": 280,
  "cost_usd": 0.012,
  "tenant_id": "acme-corp",
  "user_id_hash": "sha256:...",
  "feature": "code_review",
  "prompt.version": "v3.2"
}
```

No raw user text on the log line in default prod.

Redaction is before write, twice: SDK anonymizer + collector `redaction`/`transform` processor. LangSmith: `LANGSMITH_HIDE_INPUTS/OUTPUTS`, `create_anonymizer` regex/function, optional Presidio. LLM Gateway: PII and secrets redaction as a control-plane product.

### 3.7 Monitoring: SLOs that are not "the model is 99.9%"

User-facing SLIs for agents are request-shaped, not token-shaped:

| SLI | Definition | Notes |
|-----|-----------|-------|
| **Availability** | Root span OK AND `finish_reason` not in `{length, content_filter}` unless defined product outcome | 429 / overload is an error from the user's seat |
| **Latency** | TTFT p95/p99 (stream) + e2e p95/p99 (agent may be 10-120s) | Do not SLO the inner `chat` p99 as if it were the UX |
| **Correctness (online)** | Sampled eval / policy-violation rate—off the request path | Online judges are a second budget |
| **Cost** | `$ / successful task` or tokens / task, not `$ / span` | Token burn is a budget, not an SLO unless finance says so |

Google SRE Workbook multi-window burn rates still apply: for a 30-day SLO, page at burn 14.4x on 1h AND 5m (2% of budget in 1h); page at 6x on 6h AND 30m (5% in 6h); ticket at 1x on 3d. Datadog implements the same `burn_rate("slo_id").over("30d").long_window("1h").short_window("5m")` API. Short window = 1/12 of long.

Tempo metrics-generator turns spans into RED metrics + exemplars; Grafana SLO app consumes `traces_spanmetrics_latency`. Grafana Cloud default 30s slack: spans whose end time is older than now-30s are dropped from metrics. Query frontend example SLO knobs: `duration_slo: 5s` for search and get-by-id—that is query, not ingest.

**Cost dashboards:** Datadog Cost Overview: estimated `$` from public provider prices × annotated tokens; states `PARTIAL COST` / `UNAVAILABLE` when cache splits missing (standard input rate applied to all `input_tokens`—overestimate if you had cache hits). Alert on `ml_obs.span.llm.total.cost` by `ml_app` / `model_name`. Soft quota 80% / hard cap is a gateway concern, not a dashboard.

### 3.8 Agent Trajectories: Tree, Thread, Graph, Replay

**LangGraph checkpointers** snapshot state at super-step boundaries; task writes inside a super-step are for fault tolerance, not time-travel. Replay from `checkpoint_id` re-executes nodes after that checkpoint—LLM calls, tools, interrupts fire again and may differ. Replay of the final checkpoint is a no-op. Fork = `update_state` then invoke; original history is not deleted. That is decision provenance plus a debugger, not an audit tape.

**Provenance that survives sampling:** Policy decision (allow/deny/HITL), tool name + call id, model request vs response id, checkpoint id, prompt version. Put those on low-cardinality span attrs and audit logs. Put the essay on a blob.

### 3.9 Token Economics & NFR Metrics

**Billing units are not interchangeable.** Interview failure: quoting `$/1k traces` across Datadog, LangSmith, and Honeycomb as if they were the same object.

| Vendor | Billable unit | Published list (2026-08-21) | Retention on that SKU |
|--------|--------------|----------------------------|---------------------|
| **LangSmith** | Trace (root + all child runs = 1) + extended-retention upgrade | base 0.05c/trace, extended 10x = 0.50c/trace (upgrade 0.45c). Seats: Developer $0 (1 seat, 5k base traces/mo), Plus $39/seat (10k included). Overlay: 1 LCU = $1.50, 1 LSU = $1.00 | Base 14 days; extended 400 days (Enterprise customizable). Monitoring graphs keep metadata >30 days after base deletion. Datasets: indefinite. |
| **Datadog Agent Observability** | LLM spans only (one provider call). Tool/workflow/agent/embedding/retrieval free | Free 40k LLM spans/mo. Pro $160/mo annual for first 100k; $200 M2M; $240 on-demand. Overage $3.50 / 10k annual, $4.20 M2M, $5 on-demand. Retention add-on $1.50 / $3 / $4 per 10k LLM spans for 30/60/90-day traces | Default 15 days traces |
| **Honeycomb** | Event = one span (SpanEvent/Link also count) | Free 20M events/mo + 100M metric datapoints. Pro starting at $150/mo, up to 750M events. From 2026-07-01 new Pro $3.00 / million events vs legacy $1.30 / million | Plan-default retention |
| **Phoenix / Tempo self-host** | Your disks + query compute | No SaaS trace SKU. Constraint is payload size and queue | You choose |

**$ per 1k traces (named assumptions):**
- LangSmith base: $0.50 / 1k traces. Extended: $5.00 / 1k traces. Official.
- Datadog: (inferred) 1 agent request × 8 LLM calls = 8 billable spans. At annual overage $3.50/10k LLM spans = $0.35 / 1k LLM spans = $2.80 / 1k such requests.
- Honeycomb: (inferred) new Pro $3.00 / 1M events = $0.003 / 1k events. A 25-span agent turn = $0.075 / 1k traces.

**Auto-upgrade tax (LangSmith):** Online evaluators and automation rules default to extending retention. One matching run upgrades the entire trace; a thread-level rule upgrades every trace in the thread. Experiments start at extended. This is how a 14-day debug project becomes a 400-day invoice.

**Storage shape:** APM span: tens to hundreds of bytes of attributes. LLM span with content: full prompt + completion (often 2-32k tokens approx 8-128 KB UTF-8 per call, plus tool JSON). LangSmith hourly data caps:

| Plan | Events / hour | Payload / hour |
|------|--------------|---------------|
| Developer, no card | 50,000 | 500 MB |
| Developer, card on file | 250,000 | 2.5 GB |
| Startup/Plus | 500,000 | 5.0 GB |
| Enterprise | Custom | Custom |

Plus ALB: 5,000 `POST|PATCH /runs*` per minute per key (SDK batches <= 100 runs/call). Developer no-card: 5,000 traces / calendar month then 429.

(inferred) payload math: 5.0 GB/h / 500k events = 10 KB/event average headroom on Plus. A single 50 KB prompt on create and 80 KB on update = 130 KB against the window for one run. Content-on-by-default will 429 you before span-count does.

Phoenix: 4 MB gRPC; Tempo example override max_bytes_per_trace: 5,000,000 (5 MB) and ingestion_rate_limit_bytes: 15,000,000 (15 MB/s/tenant).

## 4. Key Patterns & Best Practices

### 4.1 Fan-Out, Don't Dual-Instrument

LangSmith documents the production pattern: app emits OTLP once → OpenTelemetry Collector → LangSmith OTLP endpoint AND a second backend. Never instrument the same code path twice with vendor-specific SDKs.

### 4.2 Sampling Policy Stack Under Load

Order matters:

1. **Keep** ERROR / `finish_reason=content_filter` / policy-deny / HITL traces (status + OTTL).
2. **Keep** high-latency roots (user SLO breach).
3. **Bytes_limiting / rate_limiting** token buckets on the tail sampler.
4. **Probabilistic** remainder; write `tracestate` so adjusted counts remain unbiased.
5. SDK head sample **only** as last-ditch if collectors are saturated—accept bias.

Honeycomb throughput samplers target spans/sec. OTel `composite` allocates a budget per policy. `drop_pending_traces_on_shutdown`: pick explicitly; default partial decisions look like missing children.

**Late spans after a drop decision:** Enable `decision_cache.non_sampled_cache_size` much greater than `num_traces` so a late MCP server span is dropped consistently instead of creating a one-span orphan trace. `trace_dropped_too_early` means `num_traces` is too small for `decision_wait` × arrival rate.

### 4.3 Collector Back-Pressure as a Protocol

`memory_limiter` MUST be first in every pipeline. Soft limit = `limit_mib - spike_limit_mib`: refuse data with a non-permanent error so receivers retry and apply upstream back-pressure. Hard limit: refuse + force GC. Pair with `GOMEMLIMIT` approx 80% of container memory. Forced GC that cannot free exporter-queue-held data backs off exponentially.

Exporters: `sending_queue` + `retry_on_failure`; optional `file_storage` so the queue survives restart. When the queue is full, drop and count—that is load shed. Watch `otelcol_processor_refused_spans` and queue size vs capacity.

OTLP receivers that ignore retryable errors turn limiter protection into silent loss. Custom receivers are the usual bug.

### 4.4 Kafka/Disk as the State APM Pretended Not to Need

Agent traces are wide (many spans) and slow (tools, humans, MCP). In-memory grouping (num_traces=50k, 30s wait) OOMs first. Pattern: edge collector → Kafka (partition_traces_by_id) → sampling collectors → backends. Kafka consumer should propagate downstream processor errors so the consumer pauses (true back-pressure) rather than OOM the collector.

Phoenix in-process queue (20k spans) is the same class of bug without Kafka: `RESOURCE_EXHAUSTED` under embedding-vector attributes.

### 4.5 Cross-Process State: Don't Break the Trace at MCP/Queues

- **HTTP tools:** inject W3C headers.
- **MCP:** `_meta.traceparent` (SEP-414), not only HTTP.
- **Queues:** propagate context in message metadata; consumers `extract` before handling.
- **Streaming LLM:** span ends when the stream completes, not at first token; TTFT is a span event or histogram, not a second trace.

Missing child = head sample on the MCP server, different collector without traceID sticky routing, or `decision_wait` shorter than the tool.

### 4.6 Content Capture: Metadata Always, Prompts Opt-In

Three recording modes:

1. **None (default prod):** Only model, tokens, latency, finish_reason, error.
2. **Span attributes:** Prompts/completions on the span. Size-limited (gRPC 4 MB, Tempo max_bytes_per_trace). Inherits trace ACL. Survives only as long as trace retention.
3. **External blob + URL on span (recommended):** Encrypted S3/GCS, short TTL, just-in-time IAM, ticketed break-glass. Span holds `content.uri` and hash. Blob retention independent of trace.

LangSmith: `LANGSMITH_HIDE_INPUTS/OUTPUTS=true` or per-run `include_run_outputs=False`. Phoenix: `OPENINFERENCE_HIDE_INPUTS=true`. OTel: `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false` (default).

### 4.7 PII Redaction Before Write, Twice

**SDK anonymizer:** Regex/function on the SDK side before network. LangSmith `create_anonymizer` with optional Presidio. OpenInference hide flags.

**Collector processor:** `redaction` or `transform` processor as second line. Allowlist attributes instead of blocklist. Cap string length. Drop embedding vectors. Hash identifiers (HMAC with a key that is not in the trace).

Still leak: tool arguments with emails/account numbers/API keys in JSON the hide-prompt flag does not cover unless you hide outputs/inputs globally or run a JSON-aware anonymizer. Retrieval documents on `RETRIEVER` spans. `user.id` / `session.id` as metrics labels (legal + cardinality). Eval datasets promoted from prod traces (indefinite retention on LangSmith datasets). Screenshots / images (`OPENINFERENCE_HIDE_INPUT_IMAGES`, `BASE64_IMAGE_MAX_LENGTH`).

Defense in depth: allowlist attributes in the collector (`attributes` processor), hash identifiers (HMAC with a key that is not in the trace), cap string length, drop embedding vectors.

### 4.8 Observability Pattern Matrix

| Pattern | Best fit | Strongest benefits | Main trade-offs |
|---------|---------|-------------------|----------------|
| Turn/run tracing | User-facing copilots, bounded tool workflows | Clean run-level visibility for tool calls, handoffs, approvals, usage | Can miss hidden branch inefficiency without finer checkpoint detail |
| Checkpointed trajectory tracing | Long-running graphs, verifier loops, resumable workflows | Reconstructs branch history, retry paths, and pending writes | More persistence overhead; resumed nodes may still replay |
| Evidence-linked retrieval logs | RAG, research, citation-heavy assistants | References plus activity logs make grounding failures diagnosable | More artifacts to retain and govern |
| Supervisor + worker traces | Multi-agent systems with narrow specialists | Preserves delegation lineage, routing decisions, worker accountability | Harder to unify across remote or nested workers |
| Protocol-aware audit layer | MCP/A2A-heavy enterprise platforms | Keeps auth, approval, timeout, external capability access visible at boundaries | Requires cross-system IDs and shared retention discipline |

## 5. System Design Considerations

### 5.1 Enterprise System Design Scenarios

**Scenario A — Startup, one agent, SaaS OK:**

Need: Debug loops, cheap, ship this quarter.

Design: Phoenix or LangSmith Developer/Plus. OpenInference or LangSmith SDK. Content on in non-prod; hide in prod + anonymizer. No tail-sampling cluster yet; SDK sample 100% until 429.

Trade-off: Vendor holds prompts. Plus at $39/seat + $0.50/1k base traces is cheaper than a collector fleet until ~tens of k traces/day. Datadog free 40k LLM spans if you already have Datadog.

**Scenario B — Bank: hybrid control, VPC data, WORM audit:**

Need: No prompts in SaaS; prove tool invocations 7 years; Zero-Trust MCP.

Design: OTel SDKs → gateway collectors in-cluster → Kafka (trace-id partition) → tail sample (keep errors, HITL, high $, 1% happy) → Tempo/S3 in VPC. Content: encrypted bucket, span has URI; IAM break-glass. MCP `_meta` context + server-side OTel. Action audit (not sampled) to object-lock bucket; platform audit to SIEM (OCSF). LangSmith Hybrid only if the data plane is your traces DB; else skip vendor data plane. RBAC: metadata vs content vs blob. Gateway: PII redact before any span attribute.

Trade-off: You own p99 ingest. You will not get LangSmith Messages-view polish unless you also export a redacted replica.

**Scenario C — Multi-agent + MCP mesh, already on Honeycomb/Grafana:**

Need: Conversation-level UX + full-stack (DB/HTTP) in one product.

Design: Instrument only `gen_ai.*` + W3C. Honeycomb Agent Timeline + Refinery EMA dynamic on `root.http.status_code`, `gen_ai.provider.name`, `error`—not conversation id. Grafana: spanmetrics for SLOs with sanitized span names; exemplars back to Tempo. Dual-export via collector.

Trade-off: Honeycomb bills every span. Wide traces (20-200 spans) make Refinery mandatory. Grafana dashboards will not look like LangSmith Messages until you build them.

**Scenario D — High QPS gateway (millions of LLM spans):**

Need: Token economics + fallbacks; traces for 0.1% + all errors.

Design: Datadog or Grafana metrics 100%; traces tail-sampled. Datadog billing on LLM spans favors deep agent trees (free tool spans).

Trade-off: Datadog 15-day default; 90-day add-on at 3M LLM spans (inferred) 300 × $4 = $1,200/mo extra.

**Scenario E — Eval-heavy platform team:**

Need: Online judges, datasets from prod, Engine-like clustering.

Design: Sample traces into an eval project at extended retention explicitly, not via default rule. Judges async. Never let eval spans block TTFT. Phoenix: spans → DataFrame → `run_evals` → annotations. LangSmith: opt out of retention upgrade on noisy evaluators.

Trade-off: Datasets are forever. PII in a golden set is a GDPR time bomb. Engine LCU is a separate meter from traces.

### 5.2 Vendor Trade-off Matrix

| Axis | SaaS LangSmith | Datadog Agent Obs | Honeycomb + OTel | Phoenix/Tempo self-host |
|------|---------------|------------------|-----------------|----------------------|
| **Billing unit** | Trace (+ 10x extend) | LLM span | Event/span | Infra |
| **Agent UX** | Threads, Messages beta, LangGraph | Agent/workflow trees + APM | Agent Timeline + BubbleUp | OpenInference UI / DIY Grafana |
| **Content default** | On unless hidden | On; SDS redaction | You choose attrs | Hide flags |
| **Tail sample** | Vendor | SDK root rate | Refinery | You build |
| **Audit RBAC** | Ent. OCSF writes; Hybrid/SH | Datadog roles + SDS | Ent. features | You build |
| **Best for** | LangChain/Graph shops | Existing DD + Fortune APM | High-card debug | Data residency / cost at scale |
| **Fatal if** | Auto-extend + full content | 15d + cost add-on surprise | Span-count bill | No one owns collectors |

**Decision rule:** Instrument to OTel GenAI + W3C once. Treat OpenInference and Datadog span kinds as exporters/UI. Put policy and $ on metrics (100%). Put forensics on traces (sampled, redacted). Put legal proof on an unsampled, immutable action log keyed by `trace_id`. If a design uses one system for all three, it will fail at least one of cost, privacy, or completeness.

### 5.3 Zero-Trust MCP and Traces as a Side Channel

CoSAI: log all agent/tool/prompt/model interactions; OTel for linkability; immutable records of actions and authorizations; do not pass user OAuth tokens through (RFC 8693 token exchange); treat MCP returned content as untrusted.

Zero-Trust for observability means: the trace backend is not implicitly trusted with plaintext PII just because SRE has access. Separate:
- **Metadata traces** (always): model, tokens, latency, tool name, policy decision, error class.
- **Content** (break-glass): encrypted blob, short TTL, just-in-time access, ticketed.
- **Audit of observability**: who exported / viewed a trace (this is not the agent's tool audit).

### 5.4 RBAC on Traces

LangSmith: org vs workspace roles; custom roles; ABAC (GA Mar 2026). Permissions include `projects:increase-trace-tier` / `decrease-trace-tier` independent of `projects:update`. Plus: org roles User/Admin only; Enterprise: custom SSO, ABAC, RBAC.

Interview design: **Viewer** sees metadata; **Debugger** sees redacted content; **Privacy** / legal sees blobs; no engineer role that can raise retention on a HIPAA project without a ticket.

Honeycomb: SSO on Pro; Query Data API / PrivateLink / Private Cloud on Enterprise. Grafana: folder permissions + Tempo multi-tenant overrides.

### 5.5 Immutable Audit — Two Tapes

| Tape | Contents | Format / sink |
|------|----------|--------------|
| **Agent action audit** | Who (user principal + agent id), what tool, args hash, policy decision, trace_id, checkpoint_id | Append-only log; WORM object lock; not sampling-eligible |
| **Platform audit** | Who changed sampling, retention, API keys, SSO, viewed/exported traces | LangSmith: Enterprise; self-hosted Helm >= 0.12.33; OCSF 1.7.0 API Activity (class 6003); `GET /api/v1/audit-logs` with `start_time`/`end_time`; org `organization:manage` only; no UI, API-only; ~70+ write operations |

LangSmith FAQ: audit logs do not currently focus on reads. If "who looked at this customer's prompt" is a requirement, you need extra wrapping (IdP session + trace-ACL proxy).

Self-hosted enablement: `DEFAULT_ORG_FEATURE_CAN_USE_AUDIT_LOGS` / `AUDIT_LOGS_ENABLED`; existing orgs need a DB `can_use_audit_logs` flag. Ship OCSF to Splunk/Datadog SIEM; do not store platform audit in the same Postgres you would wipe for GDPR of traces.

GDPR/CCPA: LangSmith deletes user data on traces within a day after retention; some metadata retained indefinitely for analytics/billing.

## 6. Code Examples

### 6.1 Minimal OTel GenAI Instrumentation (Python)

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
import os

# Enable experimental GenAI semconv
os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = "gen_ai_latest_experimental"
os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "false"  # prod default

provider = TracerProvider()
# Fan-out: single SDK -> collector -> multiple backends
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint="collector:4317")))
trace.set_tracer_provider(provider)

tracer = trace.get_tracer("my-agent", "1.0.0")
```

### 6.2 Agent Span with Tool Child

```python
with tracer.start_as_current_span(
    "invoke_agent my-assistant",
    kind=trace.SpanKind.INTERNAL,
    attributes={
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "my-assistant",
        "gen_ai.agent.version": "2.1",
    }
) as agent_span:

    # LLM call
    with tracer.start_as_current_span(
        "chat gpt-4o",
        kind=trace.SpanKind.CLIENT,
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.provider.name": "openai",
        }
    ) as llm_span:
        # ... call model ...
        llm_span.set_attribute("gen_ai.usage.input_tokens", 1500)
        llm_span.set_attribute("gen_ai.usage.output_tokens", 200)
        llm_span.set_attribute("gen_ai.response.finish_reasons", ["tool_calls"])

    # Tool execution
    with tracer.start_as_current_span(
        "execute_tool search_database",
        kind=trace.SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "search_database",
            "gen_ai.tool.call.id": "call_abc123",
            # arguments/result are OPT-IN -- only when privacy policy permits
        }
    ) as tool_span:
        # ... execute tool ...
        pass
```

### 6.3 Structured Log Event (Control-Safe)

```python
import json, time, hashlib

def emit_llm_log(trace_id, span_id, model, tokens_in, tokens_out,
                 cached_tokens, finish_reason, latency_ms, cost_usd,
                 tenant_id, user_id, prompt_version):
    """Emit a structured log line with NO raw user text."""
    log_entry = {
        "event": "llm.call",
        "trace_id": trace_id,
        "span_id": span_id,
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": model,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cache_read_tokens": cached_tokens,
        "finish_reason": finish_reason,
        "latency_ms": latency_ms,
        "cost_usd": cost_usd,  # computed at emit time from published price table
        "tenant_id": tenant_id,
        "user_id_hash": hashlib.sha256(user_id.encode()).hexdigest()[:16],
        "prompt.version": prompt_version,
        "ts": time.time(),
    }
    print(json.dumps(log_entry))  # -> structured log collector
```

### 6.4 OTel Collector Config: Two-Tier with Tail Sampling

```yaml
# Tier 1: Edge/Gateway collector
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317

processors:
  memory_limiter:  # MUST be first
    check_interval: 1s
    limit_mib: 2048
    spike_limit_mib: 512
  batch:
    timeout: 5s
    send_batch_size: 1024

exporters:
  loadbalancing:
    routing_key: traceID      # all spans of a trace -> same sampler
    resolver:
      dns:
        hostname: sampling-tier.svc
    protocol:
      otlp:
        endpoint: sampling-tier.svc:4317

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [loadbalancing]
---
# Tier 2: Tail sampling collector
processors:
  tail_sampling:
    decision_wait: 30s
    num_traces: 50000
    policies:
      - name: keep-errors
        type: status_code
        status_code: { status_codes: [ERROR] }
      - name: keep-content-filter
        type: ottl_condition
        ottl_condition:
          span: ['attributes["gen_ai.response.finish_reasons"] == "content_filter"']
      - name: keep-high-latency
        type: latency
        latency: { threshold_ms: 30000 }
      - name: keep-hitl
        type: ottl_condition
        ottl_condition:
          span: ['attributes["policy.decision"] == "hitl"']
      - name: probabilistic-rest
        type: probabilistic
        probabilistic: { sampling_percentage: 5 }
```

### 6.5 LangSmith Redaction

```python
from langsmith import Client
import re

# Option 1: Hide all inputs/outputs
# LANGSMITH_HIDE_INPUTS=true
# LANGSMITH_HIDE_OUTPUTS=true

# Option 2: Regex anonymizer
def anonymizer(data):
    """Redact PII before it reaches LangSmith."""
    if isinstance(data, str):
        data = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '[SSN]', data)
        data = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]', data)
        data = re.sub(r'\b\d{16}\b', '[CARD]', data)
    return data

client = Client(anonymizer=anonymizer)
```

## 7. Common Pitfalls & Failure Modes

### 7.1 Missing Spans (Broken Trees)

| Cause | Symptom | Fix |
|-------|---------|-----|
| Head sample on downstream MCP/HTTP service | Tool span exists, server DB span missing | Tail sample; propagate context; sample MCP at 100% or inherit parent decision |
| Load balancer without traceID affinity | Random half-traces after tail sample | Two-tier + loadbalancing exporter or Kafka key |
| `decision_wait` < tool timeout / HITL | Partial trace sampled as "OK" | Wait >= p99 e2e + slack; or `decision_wait_after_root_received` |
| LangSmith 25k run cap | Later steps 4xx | Split long agents into thread of traces, not one mega-trace |
| Hide/redact processor dropping the span | "Tool never ran" | Redact fields, don't drop TOOL spans |
| gRPC 4 MB / Tempo max_bytes_per_trace | Entire export fail | Truncate content; blob off-band |
| 429 hourly payload | Intermittent missing updates (create without completion) | Hide content; raise plan; sample |

### 7.2 Cardinality Explosion

Tempo docs: `span_name` is the usual bomb. GenAI-specific bombs: `gen_ai.request.model` dated snapshots are OK; `user.id`, `session.id`, `gen_ai.conversation.id`, tool call ids, prompt text as Prometheus labels are not. Keep high-cardinality on traces and low-cardinality on metrics. Tempo: `max_active_series`, per-label limits, span-name sanitization (DRAIN). Honeycomb Refinery: keep FieldList cardinality low (guide: prefer <100 keys); `root.` prefix.

Datadog LLM metrics tags are a fixed set (`env`, `ml_app`, `model_name`, `model_provider`, ...) — safer than DIY spanmetrics.

### 7.3 PII Leak via Traces

Classic: enable content capture in staging, copy the env to prod. Or log `Authorization` inside `llm.invocation_parameters`. Or ship traces to a vendor before DPA. Or promote a leaking trace into a dataset (indefinite). Or screenshots in computer-use agents.

Treat a trace export as a data subject access / breach surface. Token-level redaction recall is never 100%; architect as if regex will miss.

### 7.4 Sampling Bias

Head sampling 1% of "successful" chats systematically deletes rare tool-failures and jailbreak attempts. Dynamic sampling that keys on `http.status_code` but not `gen_ai.response.finish_reasons` will under-keep `content_filter`. Error-only keep will overfit dashboards to failures and hide cost blowups on happy 200s with 2M-token loops. Throughput samplers under a viral launch drop the new intent class first.

Unbiased rates require sample_rate recorded on the trace (`tracestate` probability, Honeycomb `SampleRate`, OTel adjusted count). Dashboards that `count()` without `1/sample_rate` lie.

### 7.5 Cost / SLO Coupling Failures

- Online evals on 100% traffic: second LLM bill + retention upgrades.
- Metrics-generator 30s slack + 30s tail wait: RED metrics go to zero during the incident you care about.
- Datadog cost `PARTIAL` treating all input as uncached: finance pages you; you "optimize" a non-bug.
- Alerting on span ingest lag using agent e2e duration as event latency (Honeycomb): pages that never stop.

### 7.6 Final-Answer Observability with Hidden Trajectory Thrash

A correct final answer can hide repeated retries, rewrite loops, or unnecessary tool turns. If teams observe only "request succeeded," they miss operationally bad runs that consumed too many branches or too much time.

### 7.7 Replay Ambiguity After Resume or Retry

LangGraph re-executes from checkpoint boundaries. Resumed nodes can replay non-idempotent actions. Must record both `attempted action` and `confirmed external effect`. Replay that re-calls the model cannot reproduce a prod bug (non-determinism). Teams "fix" a flake that was sampling. LangGraph docs are explicit: replay re-triggers LLM/API/interrupts. For forensics use recorded span I/O + checkpoint, not a new sample. Messages-view trajectory that filters middleware hides the guardrail that actually failed.

### 7.8 Hidden or Fragmented State Across Specialists

Nested or tool-wrapped subagents can hide internal state from parent-level inspection. Remote delegation introduces additional coordinator and transport surfaces. A system can appear healthy at the top level while losing the trace needed to explain a worker failure.

### 7.9 Evidence Drift in Retrieval and Research Agents

Retrieval plans, query rewrites, or bounded reranking produce incomplete evidence sets while the final answer still looks fluent. Without references, candidate sets, or activity logs, team cannot tell whether failure was retrieval starvation, rewrite thrash, or answer synthesis.

### 7.10 Observation Drift in Browser or UI-Driven Loops

Visible environment can change between observation and action. Logs may show a valid planned action while the actual target on screen changed before execution.

### 7.11 Monitoring Blind Spots from Context and Cache Behavior

Context-window degradation, exact-prefix cache thrash, and semantic-cache false positives as distinct failure modes. If monitoring tracks only final cost or answer quality, teams miss the causal signal.

### 7.12 Governance Mismatch in Multi-Agent Systems

Coordinated groups add extra auth, timeout, and observability surfaces. A run-level trace that ignores delegation structure can understate real system risk.

## 8. Interview Questions & Answers

**Q1: An agent trace is not the same as an APM trace. What are the key differences?**

An APM span is tens to hundreds of bytes of attributes—an HTTP method, status code, a few tags. An LLM span with content capture includes the full prompt plus completion, often 2-32k tokens = 8-128 KB of UTF-8 per call, plus tool JSON. This means LLM traces are 10-100x the size of APM traces. Second, agent traces contain PII by default (user messages, tool arguments with account numbers, retrieved documents), making the trace backend as sensitive as a production database. Third, agent traces are structurally wider (many parallel tool calls) and slower (tools, humans, MCP round-trips), which breaks head sampling and in-memory tail-sampling assumptions designed for quick HTTP spans.

**Q2: Why is head sampling wrong for agents, and what should you use instead?**

Head sampling decides at the SDK before any work happens, using `TraceIdRatioBased`. The interesting bit for agents—a tool error, a 40-step loop, a `content_filter` finish reason, a policy deny—is only known at the tail. Head sampling 1% of "successful" chats systematically deletes rare tool-failures and jailbreak attempts. Use tail sampling with a two-tier collector topology: edge collectors with loadbalancing exporter (routing_key=traceID) fan to a sampling tier running `tailsamplingprocessor`. Policies: keep ERROR, keep `content_filter`, keep high-latency, keep HITL, probabilistic for the rest. Use Kafka between tiers if scale demands it, with `partition_traces_by_id: true`.

**Q3: Explain the three-layer observability architecture you would propose for a production agent system.**

Layer 1: Metrics (100% of traffic, content-free). `gen_ai.client.token.usage`, `gen_ai.client.operation.duration`, cost per task. These never get sampled and survive trace retention expiry. Layer 2: Traces (sampled, redacted). Span trees with metadata attributes but content stored as encrypted blobs with span pointers. Tail-sampled to keep errors, policy violations, high-cost runs. Layer 3: Immutable audit log (unsampled). Append-only records of tool invocations, policy decisions, checkpoint IDs, model request/response IDs. Keyed by `trace_id` but stored in WORM/object-lock storage, independent of trace retention. If you use one system for all three, you will fail at least one of cost, privacy, or completeness.

**Q4: How do you handle PII in agent traces?**

Defense in depth, five layers: (1) Content off by default—OTel `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false`. (2) SDK-level anonymizer—regex for SSN, email, card numbers before serialization. (3) Collector-level `redaction`/`transform` processor as a second pass. (4) Tool arguments require special treatment—`hide_inputs` flags do not cover JSON inside `gen_ai.tool.call.arguments` unless you hide globally or run a JSON-aware anonymizer. (5) Attribute allowlisting in the collector—hash identifiers with HMAC (key not in the trace), cap string length, drop embedding vectors. Additionally: never use `user.id` or `session.id` as metric labels (legal exposure + cardinality bomb). Treat trace export as a data breach surface.

**Q5: What are the billing models for the major observability vendors, and how do you compare costs?**

They are not interchangeable. LangSmith bills per trace (root + all child runs = 1 trace): base $0.50/1k traces with 14-day retention, extended $5.00/1k traces for 400-day retention. Datadog bills per LLM span only—tool/workflow/agent/embedding/retrieval spans are free. Free 40k LLM spans/mo, then $3.50/10k annual overage. Honeycomb bills per event (= one span; SpanEvents and Links also count): new Pro $3.00/million events. Phoenix/Tempo self-hosted: your disks + query compute. The interview trap is quoting "$/1k traces" across all vendors as if they mean the same thing. A 25-span agent turn is 1 trace on LangSmith, 8 LLM spans on Datadog (if 8 model calls), and 25 events on Honeycomb.

**Q6: How does the OTel GenAI SIG relate to OpenInference, and which should you use?**

They are complementary, not competing. OTel GenAI SIG defines `gen_ai.*` semantic conventions (currently all Development status, none Stable). OpenInference is a semantic convention layer on top of OTel's wire format (OTLP), adding span kinds like GUARDRAIL, EVALUATOR, RERANKER that OTel GenAI does not yet have. In practice: instrument with OTel GenAI attributes, export via OTLP, and let the backend (Phoenix, Datadog, Honeycomb) map to its own UI vocabulary. The mapping is approximate: OTel `chat` maps to OpenInference `LLM` maps to Datadog `LLM`. OTel `execute_tool` maps to OpenInference `TOOL`. OTel `invoke_agent` maps to OpenInference `AGENT`.

**Q7: Walk me through how you would set SLOs for an agent system.**

Agent SLOs are request-shaped, not token-shaped. Availability: root span OK AND `finish_reason` not in `{length, content_filter}` unless that is a defined product outcome—429/overload is an error from the user's seat. Latency: TTFT p95/p99 for streaming, plus e2e p95/p99 for the full agent run (which may be 10-120 seconds). Do not SLO the inner `chat` p99 as if it were the UX. Cost: `$ / successful task`, not `$ / span`. Use multi-window burn rates from the Google SRE Workbook: page at 14.4x burn on 1h AND 5m, page at 6x on 6h AND 30m, ticket at 1x on 3d. Correctness: sampled eval rate off the request path—this is a second budget, not a latency tax.

**Q8: How do LangGraph checkpoints relate to observability, and what is the replay trap?**

LangGraph checkpointers snapshot full state at super-step boundaries. This enables time-travel, fork, and resume. But replay from a `checkpoint_id` re-executes nodes—LLM calls, tools, and interrupts fire again and may differ due to non-determinism. Replay is not an audit tape of what actually happened; it is a debugger that may produce different results. For forensics, use recorded span I/O plus the checkpoint, not a new replay. For audit, the immutable record must be the span tree plus the action log, not the "replay it and see" approach. Messages-view trajectory that filters middleware can also hide the guardrail that actually failed.

**Q9: What is the auto-upgrade tax on LangSmith, and how do you avoid it?**

LangSmith online evaluators and automation rules default to extending trace retention from base (14 days, $0.50/1k) to extended (400 days, $5.00/1k). One matching run upgrades the entire trace; a thread-level rule upgrades every trace in the thread. Experiments start at extended. This is how a 14-day debug project becomes a 400-day invoice. UI feedback/notes/annotation-queue additions do not upgrade. To avoid: explicitly opt out of retention upgrade on noisy evaluators, restrict the `projects:increase-trace-tier` permission to specific roles, and sample traces into a separate eval project at extended retention rather than letting rules upgrade production traces.

**Q10: How do you propagate trace context through MCP calls?**

HTTP tools inject W3C `traceparent`/`tracestate` headers normally. MCP requires special handling: SEP-414 documents carrying `traceparent`/`tracestate`/`baggage` in JSON-RPC `_meta` because MCP uses stdio/SSE/HTTP transports that do not all support HTTP headers. Without this, the agent's `execute_tool` span and the MCP server's `tools/call` span become two unconnected traces. The MCP Python SDK's OTel middleware emits SERVER spans and asserts shared `trace_id`. For queues: propagate context in message metadata; consumers extract before handling. For streaming LLM: span ends when the stream completes, not at first token; TTFT is a span event or histogram, not a second trace.

**Q11: What is the difference between a trajectory and a trace, and why does it matter?**

A trace is a tree of nested spans representing one invocation—it answers "which child timed out?" A trajectory is a deduped, ordered list of messages (human/AI/tool) projected from a thread, with run nesting removed—it answers "what did the conversation look like?" In LangSmith, a thread groups multiple traces across turns, and the trajectory (Messages view) is a projection over the thread. A graph checkpoint is yet another thing: full state at each super-step for time-travel and resume. Teams conflate these and either lose the debugging power of traces (by only keeping trajectories) or lose the conversation-level view (by only keeping traces without thread grouping).

**Q12: Design an observability stack for a regulated bank running agents with MCP tools.**

No prompts in SaaS; prove tool invocations for 7 years; Zero-Trust MCP. Stack: OTel SDKs → gateway collectors in-cluster → Kafka (trace-id partition) → tail sample (keep errors, HITL, high cost, 1% happy) → Tempo/S3 in VPC. Content: encrypted bucket, span has URI; IAM break-glass with ticketed access. MCP: `_meta` context propagation + server-side OTel instrumentation. Two audit tapes: (1) Agent action audit (unsampled)—tool name, args hash, policy decision, trace_id, checkpoint_id—to object-lock bucket. (2) Platform audit—who changed sampling, retention, API keys, viewed traces—to SIEM in OCSF format. RBAC: Viewer sees metadata; Debugger sees redacted content; Privacy/legal sees blobs; no engineer role can raise retention on a HIPAA project without a ticket. LangSmith Hybrid only if the data plane is your traces DB.

**Q13: How do you prevent the metrics-generator from dropping spans during an incident?**

Grafana Cloud metrics-generator has a default 30s slack: spans whose end time is older than now-30s are dropped from metrics. If you combine this with OTel `BatchSpanProcessor` timeout plus tail sampling `decision_wait=25s`, your spans arrive past the slack window and RED metrics go to zero during the incident you care about. Fix: (1) tune batch timeout + decision_wait so total pipeline delay stays well under the slack window; (2) use a separate metrics path that does not go through tail sampling (Datadog's `ml_obs.*` metrics bypass trace retention entirely); (3) consider Tempo's metrics-generator configuration and adjust `metrics_ingestion_time_range_slack` if self-hosting.

**Q14: What are the three observability surfaces for AI agents, and why split them?**

Trajectory observability: how the workflow progressed through steps, branches, retries, tool calls, resume points—structured state transitions like ReAct loops, planner/executor boundaries, verifier/rewrite branches. Resource observability: tokens, latency, cost, cache behavior—the economics and performance. Evidence observability: what tool outputs, retrieval results, references, or activity logs justified the run—the provenance. Splitting them prevents collapsing forensic needs (trajectory) with compliance needs (evidence) with billing needs (resource). A correct final answer can hide trajectory thrash; metrics miss retrieval starvation; traces without evidence logs cannot diagnose grounding failures.

**Q15: How would you instrument a multi-agent system with delegation and handoffs?**

Supervisor + worker traces pattern. Supervisor emits `invoke_agent` CLIENT spans when calling remote workers or INTERNAL for in-process. Workers emit their own root spans linked via W3C context propagation. Handoffs between agents require explicit spans (`gen_ai.operation.name=handoff` or `agent.transition`). Preserve delegation lineage with attributes like `agent.parent`, `agent.role`, routing decisions. For evidence: each worker logs its tool calls, retrieval, and decisions with `agent.id` and `trace_id`. For audit: unsampled action log with `coordinator_trace_id`, `worker_trace_id`, `handoff_reason`. Failure mode: nested workers hide internal state—mandate OpenTelemetry at every agent boundary, not just the coordinator.

## 9. Key Numbers to Memorize

| Metric | Value | Context |
|--------|-------|---------|
| LLM span size vs APM span | 10-100x | Full prompt+completion = 8-128 KB vs tens of bytes |
| LangSmith base trace cost | $0.50 / 1k traces | 14-day retention; $5.00/1k for 400-day extended |
| LangSmith max runs per trace | 25,000 | Further runs rejected with 4xx |
| LangSmith Plus hourly payload cap | 5.0 GB | Content-on-by-default will hit this before span count |
| Datadog free LLM spans | 40,000 / month | Tool/workflow/agent spans are free |
| Datadog annual overage | $3.50 / 10k LLM spans | $5.00 on-demand |
| Honeycomb new Pro pricing | $3.00 / million events | From 2026-07-01; legacy $1.30/M |
| OTel tail sampling decision_wait | 30s default | `num_traces=50000` |
| Phoenix max spans queue | 20,000 default | `PHOENIX_MAX_SPANS_QUEUE_SIZE` |
| Phoenix/gRPC message limit | 4 MB | Truncate content or blob off-band |
| Grafana Cloud metrics slack | 30s | Spans older than now-30s dropped from metrics |
| SRE burn rate page threshold | 14.4x on 1h/5m | 2% of 30-day budget in 1 hour |
| SRE burn rate ticket threshold | 1x on 3d | Steady budget consumption |
| memory_limiter + GOMEMLIMIT | GOMEMLIMIT ~80% container RAM | Soft limit = limit_mib - spike_limit_mib |
| OTel GenAI semconv maturity | All Development | No GenAI-specific attr is Stable as of July 2026 |
| LangSmith ALB rate limit | 5,000 POST/PATCH per minute per key | SDK batches <= 100 runs/call |
| Browser-tool token floor | ~6,610-6,670 input tokens | Before screenshots or task content |
| Computer-tool token floor | ~4,520-4,590 input tokens | Before screenshots or task content |

## 10. Quick Reference

### One-Page Cheat Sheet

**Architecture rule:** Instrument OTel GenAI + W3C once. Export to N backends via collector fan-out. Never dual-instrument.

**Three-layer stack:**
1. Metrics (100%, content-free) — token usage, cost, latency histograms
2. Traces (sampled, redacted) — span trees with metadata attrs, content as encrypted blobs
3. Audit log (unsampled, immutable) — tool invocations, policy decisions, checkpoint IDs

**Tail sampling policy stack (order matters):**
1. Keep ERROR / content_filter / policy-deny / HITL
2. Keep high-latency roots (SLO breach)
3. bytes_limiting / rate_limiting token buckets
4. Probabilistic remainder (write tracestate for unbiased counts)
5. SDK head sample only as last-ditch

**SLOs for agents:**
- Availability: root span OK + valid finish_reason
- Latency: TTFT p95/p99 + e2e p95/p99
- Cost: $/successful task
- Correctness: sampled eval rate (off request path)

**PII defense in depth:**
1. Content off by default (OTel)
2. SDK anonymizer (regex)
3. Collector redaction processor
4. Attribute allowlist + HMAC identifiers
5. Separate content blobs from metadata traces

**MCP context propagation:** `_meta.traceparent` (SEP-414) for stdio/SSE; W3C headers for HTTP tools; message metadata for queues.

**Critical env vars:**
- `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`
- `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false` (prod default)
- `LANGSMITH_HIDE_INPUTS=true` / `LANGSMITH_HIDE_OUTPUTS=true`
- `OPENINFERENCE_HIDE_INPUTS=true`

**Billing comparison (per 1k agent turns, ~25 spans each, ~8 LLM calls):**
- LangSmith base: $0.50/1k traces (14d) | Extended: $5.00/1k (400d)
- Datadog: $2.80/1k turns (at $3.50/10k LLM spans overage)
- Honeycomb Pro: $0.075/1k turns (at $3.00/M events)
- Self-hosted: your infra cost

**Vendor span kind mapping:**
```
OTel chat         -> OpenInference LLM    -> Datadog LLM      -> LangSmith llm
OTel execute_tool -> OpenInference TOOL   -> Datadog tool     -> LangSmith tool
OTel invoke_agent -> OpenInference AGENT  -> Datadog agent    -> LangSmith chain
OTel invoke_workflow -> OpenInference CHAIN -> Datadog workflow
```

**Key collector config:**
- `memory_limiter` MUST be first processor
- `GOMEMLIMIT` ~ 80% container RAM
- `decision_cache.non_sampled_cache_size` >> `num_traces` (prevents orphan traces from late spans)
- Kafka: `partition_traces_by_id: true` for trace-id sticky routing

**Three observability surfaces:**
- Trajectory: steps, branches, retries, checkpoints (structured state transitions)
- Resource: tokens, latency, cost, cache behavior
- Evidence: tool outputs, retrieval results, references, activity logs

**Observable run cost formula:**
```text
model_input_cost + cached_read_cost + cache_write_cost + output_cost
+ reasoning_token_cost + tool_surcharges + trace_persistence + eval_cost
```

**Critical path latency formula:**
```text
planning + max(parallel_branches) + verification + approvals
+ trace_persistence + network_hops
```

**Production failure checklist:**
- Final-answer observability hiding trajectory thrash
- Replay ambiguity (re-execution vs forensics)
- Hidden state across specialists
- Evidence drift (retrieval starvation, rewrite thrash)
- Observation drift (browser/UI environment changes)
- Context/cache monitoring blind spots
- Governance mismatch in multi-agent delegation
