# Module 14 — Observability

**Audience**: personal study and enterprise interview prep.
**Grounded in**: `research_cursor_grok/research/14-observability.md` (researched 2026-08-21, 63 sources). Prices and ingest limits are vendor-published as of 2026-08-21. ⚠️ No unpublished production p50/p95/p99 ingest SLOs are invented. `$ per 1k traces` is either an official SKU or **[inferred]** from a named billing unit × a stated span/event shape.
**Mandatory topics**: Tracing · Logging · Monitoring · Agent trajectories.

The unit of production is not “we turned on LangSmith.” It is a **control plane** (collectors, sampling policy, RBAC, audit of *who viewed which trace*, spend caps) wrapping a **data plane** that is a PII store which happens to look like APM (prompts, tool args, retrieved chunks, completions). Collapsing those planes — full messages on span attributes, then Prometheus labels from `user.id` — is how teams leak PII, explode cardinality, and sample away the only traces they later need. Interview answers that skip this split fail when the follow-up is “where does the prompt live after you delete the span, and did replay re-call the model?”

**Invariant:** an agent trace is a PII store. Trajectories are a *projection* over traces/threads, not a storage format. Replay that re-calls the model is not replay that reads a checkpoint. Put **policy and $** on 100% metrics; **forensics** on sampled, redacted traces; **legal proof** on an unsampled immutable action log keyed by `trace_id`. One system for all three fails at least one of cost, privacy, or completeness.

---

## 1. System Topology & Data Flow

### 1.1 Topology

Control plane owns identity, sampling *policy* (not “inspect the prompt and keep the spicy ones”), collector config, IdP/RBAC, retention tier, spend/429 windows, and SIEM of *who viewed/exported a trace*. Data plane owns two clocks and two stores: the **user SLO clock** (TTFT / e2e on the span tree) in Tempo / Honeycomb / LangSmith / Phoenix / Datadog, and an **independent content clock** (prompts, completions, tool I/O, retrieved docs) in object storage with a span pointer, its own TTL and IAM. Persistence is therefore **three stores, one `trace_id`**: metadata traces, content blobs, graph checkpoints. Mixing them — “we deleted the span” while S3 still has the prompt, or content on attributes feeding a metrics-generator — is the production bug.

LangSmith’s published split is the cleanest *product* topology: **runs** (≈ OTel spans) nest into a **trace** (one operation); **threads** group traces across turns; a **trajectory** is a flat, ordered message list projected from the thread with run nesting removed. Datadog Agent Observability is isomorphic (LLM / workflow / agent / tool / task / embedding / retrieval; agent traces root on an **agent** span). Phoenix is the OTel-native twin (OTLP in, OpenInference kinds for UI). Honeycomb Agent Timeline binds conversations with `gen_ai.conversation.id` and swim-lanes by `gen_ai.agent.name`.

**Enterprise hosting.** LangSmith: Cloud (vendor holds both planes), **Hybrid** (SaaS control + self-hosted data), **Self-Hosted** (your VPC). Phoenix self-hosts the whole stack. Braintrust-class hybrid (UI/auth in vendor, traces in customer VPC) is the same pattern. If the data plane holds PII, every online evaluator, LLM-as-judge, and Engine job that reads traces is a **subprocessor**.

Fan-out, don’t dual-instrument: app emits OTLP once → Collector → LangSmith OTLP **and** a second backend. `LANGSMITH_OTEL_ENABLED` / `tracing_mode=hybrid` is a migration valve, not a long-term dual-SDK architecture.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CLIENTS  (chat UI / API / MCP client / HITL)                                    │
└────────────┬────────────────────────────────────────────────────────────────────┘
             │  TLS + W3C traceparent/tracestate + correlation-id
             ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE  (IdP · sampling policy · RBAC/ABAC · spend caps · SIEM)          │
│                                                                                 │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ API Gateway│─▶│ Policy       │─▶│ Orchestrator │─▶│ Sampling policy       │  │
│  │ auth, RPM  │  │ PII detect→  │  │ ReAct/graph  │  │ keep ERROR / HITL / $ │  │
│  │ TPM, 429   │  │ redact→audit │  │ thread_id    │  │ / content_filter; 1%  │  │
│  │ breaker    │  │ tool RBAC    │  │ ls_agent_    │  │ happy. NEVER sample   │  │
│  │            │  │ MCP allowlist│  │  type=root   │  │ the action-audit tape │  │
│  └────────────┘  └──────┬───────┘  └──────┬───────┘  └──────────┬────────────┘  │
└─────────────────────────┼─────────────────┼─────────────────────┼───────────────┘
                          │                 │                     │
         hosted complete()│                 │ tools/call          │ policy decision
                          ▼                 ▼                     │
┌─────────────────────────┼─────────────────┼─────────────────────┼───────────────┐
│ DATA PLANE              │  untrusted I/O  │                     │               │
│                         │                 │                     │               │
│  ┌──────────────────────┴──┐  ┌───────────┴──────────┐          │               │
│  │ Foundation model        │  │ TOOL / MCP PROXY     │          │               │
│  │ CLIENT span:            │  │ INTERNAL execute_tool│          │               │
│  │  {op} {request.model}   │  │ + MCP tools/call     │          │               │
│  │ tokens, finish_reason   │  │ _meta.traceparent    │          │               │
│  │ content OFF by default  │  │ (SEP-414) else TWO   │          │               │
│  └───────────┬─────────────┘  │ traces               │          │               │
│              │                └──────────┬───────────┘          │               │
│              │   gen_ai.usage.*          │  hashed args         │               │
│              ▼                           ▼                      │               │
│  ┌────────────────────────────────────────────────┐             │               │
│  │ In-process SDK  BatchSpanProcessor / queue     │             │               │
│  │ Phoenix analog: PHOENIX_MAX_SPANS_QUEUE_SIZE   │             │               │
│  │  = 20,000 then RESOURCE_EXHAUSTED              │             │               │
│  └──────────────────────┬─────────────────────────┘             │               │
└─────────────────────────┼───────────────────────────────────────┼───────────────┘
                          │ OTLP (gRPC 4317 / HTTP)               │
                          ▼                                       │
┌─────────────────────────────────────────────────────────────────┼───────────────┐
│ COLLECTOR TIER  (memory_limiter FIRST; GOMEMLIMIT ≈ 80%)        │               │
│                                                                 │               │
│  ┌───────────────┐  routing_key=traceID   ┌──────────────────┐  │               │
│  │ Edge/gateway  │───────────────────────▶│ Sampling tier    │  │               │
│  │ k8sattributes │  or Kafka              │ tailsampling     │  │               │
│  │ batch         │  partition_traces_     │ processor        │  │               │
│  │ loadbalancing │  by_id: true           │ decision_wait=30s│  │               │
│  │  exporter     │  (default FALSE)       │ num_traces=50k   │  │               │
│  └───────────────┘                        └────────┬─────────┘  │               │
│                                                    │ keep/drop  │               │
│  Honeycomb Refinery is this productized (tail      │            │               │
│  proxy; sampled-before-ingest events do not bill). │            │               │
└────────────────────────────────────────────────────┼────────────┼───────────────┘
                                                     │            │
              ┌──────────────────────────────────────┼────────────┘
              ▼                                      ▼
┌─────────────────────────────────┐    ┌──────────────────────────────────────────┐
│ TELEMETRY BACKENDS (sampled)    │    │ PERSISTENCE (independent failure domains)│
│  ┌────────────┐ ┌─────────────┐ │    │                                          │
│  │ Traces     │ │ Metrics 100%│ │    │  ┌──────────────┐  ┌──────────────────┐  │
│  │ Tempo/S3   │ │ RED + tokens│ │    │  │ App / graph  │  │ Content blobs    │  │
│  │ LangSmith  │ │ Datadog     │ │    │  │ checkpoints  │  │ encrypted bucket │  │
│  │ Phoenix    │ │  ml_obs.*   │ │    │  │ (resume; NOT │  │ span has URI     │  │
│  │ Honeycomb  │ │  15 mo full │ │    │  │  an audit    │  │ short TTL, JIT   │  │
│  │ Datadog    │ │ Grafana     │ │    │  │  tape)       │  │ access           │  │
│  │  LLM spans │ │  spanmetrics│ │    │  └──────────────┘  └──────────────────┘  │
│  │  billed    │ │  30s slack! │ │    │  ┌──────────────┐  ┌──────────────────┐  │
│  └────────────┘ └─────────────┘ │    │  │ WORM action  │  │ Platform audit   │  │
│  Content on span = size+PII.    │    │  │ audit UNSAMP.│  │ OCSF 1.7.0 / SIEM│  │
│  Prefer blob URL on the span.   │    │  │ keyed trace_ │  │ who viewed/exported││
│                                 │    │  │  id; 7y lock │  │ ≠ agent tool tape│  │
└─────────────────────────────────┘    │  └──────────────┘  └──────────────────┘  │
                                       └──────────────────────────────────────────┘
```

### 1.2 End-to-end request flow

1. **Ingress.** Client opens SSE/HTTP. Gateway stamps `correlation-id` and W3C `traceparent` (`00-{32 hex trace-id}-{16 hex parent-id}-{2 hex flags}`; flag `01` = sampled). `tracestate` is forwarded opaquely. MCP hops put the same headers in JSON-RPC `_meta` (SEP-414); without that, `execute_tool` and the server’s `tools/call` are **two traces**.
2. **Policy (control).** PII detect → redact → audit **before** any span attribute or log line. Tool RBAC attaches the allowlist for this turn. Sampling *policy* is loaded here (keep ERROR / `content_filter` / HITL / high-$ / high-latency; probabilistic remainder). The policy does **not** read prompts.
3. **Orchestrator.** Graph/ReAct starts an `invoke_agent` span (CLIENT if remote agent API, INTERNAL if in-process LangGraph). Sets `thread_id` and LangSmith `ls_agent_type: "root"` on the turn’s top run so Messages-view trajectory can form. Checkpoint writer is a **different** store than the span exporter.
4. **Model call (data).** Child CLIENT span named `{gen_ai.operation.name} {gen_ai.request.model}` (`chat` / `generate_content` / …). Required-class attrs: `gen_ai.provider.name`, `operation.name`, `request.model`. Usage: `input_tokens` (includes cached) + `output_tokens` + cache-read/cache-creation splits. `response.model` is what actually served. `finish_reasons`: `stop` | `tool_calls` | `length` | `content_filter`. **Content is off by default** (`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` to opt in). Production recording mode: **external blob + URL on the span**.
5. **Tool proxy.** Kind INTERNAL; name from v1.41 MUST be `execute_tool {gen_ai.tool.name}`. Auto-instrumentors see the *model’s request* for a tool; wrap the *function body* or you get `finish_reason=tool_calls` and a missing child. `tool.call.arguments` / `.result` are opt-in and the usual PII leak (hide-prompt flags do **not** cover tool JSON unless you hide inputs/outputs globally or run a JSON-aware anonymizer).
6. **MCP enrichment.** Client + server `tools/call` with `mcp.method.name`, `mcp.session.id`, `rpc.system=mcp`. If the outer GenAI already has `execute_tool`, MCP **enriches** rather than duplicating. Stream the LLM span until the **stream completes**; TTFT is a span event or histogram, not a second trace.
7. **SDK export.** BatchSpanProcessor / LangSmith `auto_batch_tracing` holds spans for seconds. Phoenix in-process queue default **20,000** then `RESOURCE_EXHAUSTED`. Crashed workers lose the in-flight batch — telemetry does **not** resume with the checkpoint.
8. **Collectors.** `memory_limiter` is first (soft limit refuses with a **non-permanent** error so receivers retry; hard limit refuses + GC). Edge collectors `loadbalancing` export with `routing_key: traceID` (or Kafka `partition_traces_by_id: true`, default **false**). **All spans of a trace MUST hit the same sampling instance.** Tail sampler waits `decision_wait=30s` (default), `num_traces=50000`. Late spans after a drop need `decision_cache.non_sampled_cache_size ≫ num_traces` or you mint orphan one-span traces.
9. **Fan-out backends.** Sampled traces → Tempo / LangSmith / Phoenix / Honeycomb / Datadog. **Metrics are 100% and content-free** (`gen_ai.client.token.usage`, `gen_ai.client.operation.duration`; Datadog `ml_obs.*` computed from 100% of traffic, 15 months at full granularity, even when traces are sampled). Grafana Cloud metrics-generator **drops** spans whose end is older than now−**30s** — a 25s `decision_wait` + batch will zero RED metrics during the incident. Tempo query-frontend example `duration_slo: 5s` is a **read** SLO, not ingest.
10. **Persist two other tapes.** Graph checkpoint at super-step (resume, not proof). Unsampled WORM action audit: principal, agent id, tool, args **hash**, policy decision, `trace_id`, `checkpoint_id`. Platform audit (who changed sampling/retention/keys, who exported) is a third tape — LangSmith Enterprise OCSF 1.7.0 API Activity class 6003, API-only, ~70+ **write** operations, and **does not currently focus on reads**.

**Interview talking point:** “Instrument OTel GenAI + W3C once. OpenInference and Datadog span kinds are exporters/UI. The prompt is a blob with a pointer, not an attribute. The audit log is not the trace backend.”

---

## 2. Core Mechanics & Algorithms

### 2.1 Tracing — W3C, OTel GenAI, OpenInference

**W3C Trace Context (REC 2021-11-23)** is the wire format every other layer rides on. Version `00`; 16-byte trace id; 8-byte parent/span id; `01` = sampled. Intermediaries MUST forward `traceparent` **and** `tracestate`. OTel tail sampling can write probability fields (`rv`, `th`) into the `ot` section of `tracestate` when `processor.tailsamplingprocessor.usetracestate` is on — that is how dashboards stay unbiased after a keep/drop.

**GenAI SIG** (formed April 2024) grew to six layers: client spans, agent/workflow spans, MCP, content capture, metrics, evaluation events. Authoritative home moved in 2026: core `semantic-conventions` v1.42.0 deprecated and moved all `gen_ai.*`; v1.43.0 ships none. Dedicated repo: `open-telemetry/semantic-conventions-genai`. As of July 2026 **no GenAI-specific span/event/metric/attribute is Stable** — all Development. Shared core (`error.type`, `server.address`) is Stable. Opt-in: `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`.

| Semconv | What landed |
| --- | --- |
| v1.37 (Aug 2025) | `gen_ai.system` → `gen_ai.provider.name`; per-message events → aggregated `input.messages` / `output.messages` / `system_instructions` |
| v1.38 | Evaluation event; tool definitions; `invoke_agent` kind guidance |
| v1.39 | MCP semantic conventions |
| v1.40 (Feb 2026) | Retrieval spans; cache token attributes; `gen_ai.agent.version` |
| v1.41 (Apr 2026) | `execute_tool {tool.name}` naming; reasoning tokens; `invoke_workflow`; streaming metrics; `invoke_agent` CLIENT vs INTERNAL |

**Complexity.** A trace is a tree. Export is \(O(S)\) in span count \(S\). Tail sampling buffers the tree for \(W=\) `decision_wait` so memory is \(O(\texttt{num\_traces} \times \bar{S} \times \bar{B})\). LLM content makes \(\bar{B}\) **10–100×** APM (2–32k tokens ≈ 8–128 KB UTF-8 **per call**, plus tool JSON). Plus-plan headroom **[inferred]**: 5.0 GB/h ÷ 500k events = **10 KB/event**; a 50 KB create + 80 KB update = **130 KB** against the hourly payload window for one run. Content-on-by-default 429s you on **bytes** before span count. Caps: LangSmith **25,000 runs/trace** (further runs rejected); Phoenix/gRPC **4 MB**/message; Tempo example `max_bytes_per_trace: 5_000_000`. Split long agents into a **thread of traces**, not one mega-trace.

**OpenInference** is a convention **on** OTel, not a competing protocol. Transport OTLP. Required: `openinference.span.kind` in **ALL CAPS**. Phoenix listens OTLP/**gRPC 4317** and OTLP/**HTTP on UI port 6006** (`/v1/traces`) — **not** generic 4318. Production: `TraceConfig(hide_inputs=True, …)` or `OPENINFERENCE_HIDE_INPUTS=true`. Flattened attrs (`llm.input_messages.0.message.role`) because OTel attributes are flat K/V.

| OpenInference | OTel `operation.name` | Datadog | LangSmith run |
| --- | --- | --- | --- |
| `LLM` | `chat` / `generate_content` / `text_completion` | `LLM` (the **billable** kind) | `llm` |
| `TOOL` | `execute_tool` | `tool` (not a valid root) | tool run |
| `AGENT` | `invoke_agent` | `agent` (root) | top run + `ls_agent_type=root` |
| `CHAIN` | `invoke_workflow` | `workflow` | chain |
| `RETRIEVER` / `EMBEDDING` / `RERANKER` | `retrieval` / embeddings | retrieval / embedding | retriever |
| `GUARDRAIL` / `EVALUATOR` | evals as `gen_ai.evaluation.result` events | — | feedback / evaluator run |

Do not treat the mapping as 1:1 identity. Guardrail/evaluator kinds exist in OpenInference first.

**Head vs tail sampling.** SDK `TraceIdRatioBased` is cheap and **wrong for agents**: the interesting bit (tool error, 40-step loop, `content_filter`) is known only at the tail. Canonical two-tier: edge (`memory_limiter`, batch, loadbalancing by traceID) → sampling tier (`tailsamplingprocessor`). Policies, order: `status_code` ERROR → latency → `ottl_condition` (e.g. `gen_ai.usage.input_tokens`) → `probabilistic` remainder → `composite` rate allocation → `bytes_limiting` / `rate_limiting` token buckets. Honeycomb Refinery: dynamic / EMA dynamic / rules / throughput; use `root.` prefix or concatenated span values explode the sampler key. Datadog SDK samples on the **root** LLM-obs span and applies to all children including downstream APM; that is a **cost control**, not a GenAI policy engine.

**Invariant (sampling):** record `sample_rate` / adjusted count on the trace (`tracestate` probability, Honeycomb `SampleRate`). Dashboards that `count()` without \(1/p\) lie. Error-only keep overfits to failures and hides 2M-token **happy** 200s. Head-sample 1% of successes systematically deletes rare tool-failures and jailbreaks.

### 2.2 Logging — structured, correlated, not a second prompt dump

OTel log model fields that matter: `TraceId`, `SpanId`, `TraceFlags`, `Body`, `Attributes`, resource. SDKs inject IDs when a span is active. JSON-only enrichment for eBPF/OBI; plaintext is not correlated.

**Minimum structured event for an LLM call (control-safe):** `event`, `trace_id`, `span_id`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `input_tokens`, `output_tokens`, `cache_*_tokens`, `finish_reason`, `latency_ms`, `ttft_ms` if streaming, `cost_usd` computed **at emit time** from the published price table (never a nightly backfill as source of truth), `tenant_id` / hashed `user_id`, `feature` / `prompt.version`. **No raw user text** on the log line in default prod.

Redaction is **before write**, twice: SDK anonymizer + collector `redaction`/`transform` processor. LangSmith: `LANGSMITH_HIDE_INPUTS/OUTPUTS`, `create_anonymizer` regex/function, optional Presidio; anonymizer is **skipped** if hide-* is true. LLM Gateway PII/secrets redaction is a **control-plane** product, not an SDK afterthought. Phoenix: hide flags; `ReadableSpan` cannot `set_attribute` on `on_end` — redact too late is a published footgun.

Cardinality bomb: `span_name` like `GET /users/123` or `chat {user_prompt_hash}`; GenAI bombs are `user.id`, `session.id`, `gen_ai.conversation.id`, tool call ids, prompt text as **Prometheus labels**. Keep high-cardinality on **traces** (Honeycomb’s point) and low-cardinality on **metrics**. Tempo: `max_active_series`, per-label limits, span-name sanitization (DRAIN). Datadog LLM metric tags are a **fixed** set (`env`, `ml_app`, `model_name`, `model_provider`, …) — still explode if `ml_app` is per-customer.

### 2.3 Monitoring — SLOs that are not “the model is 99.9%”

User-facing SLIs are **request-shaped**, not token-shaped:

| SLI | Definition | Notes |
| --- | --- | --- |
| **Availability** | Root span OK **and** `finish_reason` not in `{length, content_filter}` unless that is a defined product outcome | 429 / overload is an error from the user’s seat |
| **Latency** | TTFT p95/p99 (stream) + e2e p95/p99 (agent may be 10–120 s) | Do not SLO the inner `chat` p99 as if it were the UX |
| **Correctness (online)** | Sampled eval / policy-violation rate — **off the request path** | Online judges are a second budget + often auto-extend retention |
| **Cost** | `$ / successful task` or tokens / task, not `$ / span` | Token burn is a **budget** unless finance says SLO |

Google SRE Workbook multi-window burn rates still apply. For a 30-day SLO: **page** at burn **14.4×** on **1h AND 5m** (2% of budget in 1h); page at **6×** on **6h AND 30m** (5% in 6h); ticket at **1×** on 3d. Short window = 1/12 of long. Datadog: `burn_rate("slo_id").over("30d").long_window("1h").short_window("5m")`.

Tempo metrics-generator turns spans into RED + exemplars; Grafana SLO app consumes `traces_spanmetrics_latency`. **Sanitize span names** (`chat`, `execute_tool {allowlisted_tool}`) or cardinality kills the SLO.

**Cost dashboards.** Datadog Cost Overview: estimated `$` from **public** provider prices × annotated tokens; `PARTIAL COST` / `UNAVAILABLE` when cache splits missing — then **standard input rate is applied to all `input_tokens`** (overestimate on cache hits). Alert on `ml_obs.span.llm.total.cost` by `ml_app` / `model_name`. Soft 80% / hard cap is a **gateway** concern (LangSmith LLM Gateway: cost controls + rate limits), not a dashboard. Honeycomb Pro SLO cap: **2 SLOs** — pick TTFT and availability; put cost on a trigger.

NFR targets to *set* (not claimed as industry): ingest 429 rate = 0; tail-sample `trace_dropped_too_early` ≈ 0; `otelcol_exporter_queue_size / capacity` < 0.5; metrics-generator discarded-late-span rate ≈ 0; PII findings in traces = 0 after redaction QA.

### 2.4 Agent trajectories — tree, thread, graph, replay

Four objects people conflate:

| Object | Stores | For |
| --- | --- | --- |
| **Trace tree** | Nested spans/runs for one invocation | “Which child timed out?” |
| **Thread** | Sequence of traces sharing `thread_id` | Multi-turn session |
| **Trajectory** | Deduped ordered messages (human/AI/tool) | Scan the conversation; LangSmith Messages view (**beta**) |
| **Graph checkpoint** | Full **state** at each super-step | Time-travel, fork, resume; **not** a span |

LangSmith trajectory requires `thread_id` plus `ls_agent_type: "root"` on the turn’s top run; `subagent` / `middleware` / `compaction` change what Messages view shows. Filtering middleware hides the guardrail that actually failed.

**LangGraph checkpointers** snapshot state at super-step boundaries; task writes inside a super-step are for **fault tolerance**, not time-travel. **Replay** from `checkpoint_id` **re-executes** nodes after that checkpoint — LLM calls, tools, interrupts fire again and **may differ**. Replay of the final checkpoint is a no-op. **Fork** = `update_state` then invoke; original history is not deleted. That is decision provenance plus a debugger, **not** an audit tape of what the model *would* say if sampled again.

**Forensics vs resume (complexity).** Forensic reconstruct is \(O(S)\) over recorded span I/O + checkpoint bytes; it is deterministic given the tape. Model-replay is a new sample (non-deterministic); using it to “fix” a flake that was sampling is a documented debugging-loop failure. Provenance that **survives sampling**: policy decision (allow/deny/HITL), tool name + call id, model request vs response id, checkpoint id, prompt version — low-cardinality span attrs **and** the unsampled audit log. Put the essay on a blob.

Honeycomb Timeline: conversation metrics (duration, trace count, LLM calls, tool calls, failures, total tokens) + GenAI panel (provider, request/response model, tool type `function|extension|datastore`, call id). Failures depend on `error.type` and span status — if tools swallow exceptions, “Show Failures Only” is empty.

### 2.5 State machines

**Tail sampler (per `trace_id`, sticky instance):**

```
  SPAN_IN ──▶ BUFFER (until decision_wait or root+idle)
                  │
                  ├── keep: ERROR | content_filter | HITL | latency | OTTL $
                  ├── bytes/rate token-bucket full ──▶ DROP (count)
                  └── probabilistic remainder ──▶ KEEP(p) | DROP(1-p)
                                                   write tracestate th/rv
  LATE_SPAN ──▶ decision_cache hit? ──▶ honor prior KEEP/DROP
                    miss + dropped-too-early ──▶ orphan one-span trace
```

**Exporter (per backend):**

```
  ENQUEUE ──▶ sending_queue + retry_on_failure
                │
                ├── ack ──▶ DONE
                ├── retryable ──▶ full-jitter retry (honor Retry-After)
                ├── queue full ──▶ DROP and count  (load shed)
                └── restart ──▶ file_storage WAL if configured; else lose batch
```

**Circuit breaker (per exporter / MCP / provider):**

```
           failure_rate ≥ threshold                probe success
  ┌────────┐  ─────────────────────▶  ┌──────┐  ───────────────▶  ┌────────┐
  │ CLOSED │                          │ OPEN │                    │CLOSED  │
  └───┬────┘                          └──┬───┘                    └────────┘
      │                                  │ timer elapsed
      │ success resets count             ▼
      │                             ┌──────────┐
      └─────────────────────────────│ HALF_OPEN│── probe fail ──▶ OPEN
                                    └──────────┘
```

**PII (must complete before write):**

```
  DETECT ──▶ REDACT (SDK) ──▶ REDACT (collector allowlist) ──▶ AUDIT (hash, never raw)
                │
                └── hide-* true ──▶ skip anonymizer (LangSmith) but still no content
```

**Trajectory / replay:**

```
  TRACE_TREE ──▶ project ──▶ TRAJECTORY (flat messages; nesting removed)
  CHECKPOINT ──▶ resume  ──▶ re-executes nodes (LLM/tools MAY differ)   ← not forensics
  SPAN_IO     ──▶ forensic replay ──▶ read tape; do not call the model
```

### 2.6 Invariants worth stating in an interview

1. **Two planes, three stores, one `trace_id`.** Control (policy, RBAC, audit-of-view) ≠ telemetry tree ≠ content blobs. Delete is not transitive across stores.
2. **Content off by default.** Opt-in capture; production = blob URL, not attributes. Tool JSON is not covered by “hide the prompt” unless you hide globally or anonymize JSON.
3. **100% metrics, sampled traces, 0% sampling on the action audit.**
4. **Sticky traceID routing** at the tail sampler; `partition_traces_by_id` is off by default.
5. **`decision_wait` ≥ p99 e2e + slack**, or HITL/tool children are missing and the partial trace looks OK.
6. **Adjusted counts or you are lying.** `count() / p`.
7. **Replay ≠ sample.** Checkpoint resume re-fires the model. Forensics = recorded I/O + checkpoint.
8. **`gen_ai.*` is Development.** Pin a semconv commit; `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`.
9. **Redact before `on_end`.** You cannot fix attributes after the span ended (Phoenix).
10. **Unbiased cost requires cache splits.** Missing splits → Datadog applies full input price to all `input_tokens`.

---

## 3. Token Economics & NFR Analysis

Two meters: **provider tokens** (`gen_ai.usage.*` including cache read/write) and **observability tokens** (span bytes, eval LLM-as-judge, LangSmith Engine). Billing units are **not interchangeable**. Interview failure: quoting `$/1k traces` across Datadog, LangSmith, and Honeycomb as if they were the same object.

### 3.1 `$ per 1k runs` — trace storage + product

**Observability SKUs (2026-08-21):**

| Vendor | Billable unit | Published list | Retention on that SKU |
| --- | --- | --- | --- |
| **LangSmith** | **Trace** (root + all child runs = 1) + extended-retention upgrade | Base **0.05¢/trace**; extended **10× = 0.50¢** (upgrade **0.45¢**). Seats: Developer **$0** (1 seat, 5k base traces/mo), Plus **$39/seat** (10k included). Overlay: **1 LCU = $1.50**, **1 LSU = $1.00** | Base **14 days**; extended **400 days** (Enterprise customizable). Monitoring **metadata >30 days** after base deletion. Datasets: **indefinite** |
| **Datadog Agent Observability** | **LLM spans only**. Tool/workflow/agent/embedding/retrieval **free** | Free **40k LLM spans**/mo. Pro **$160**/mo **annual** for first **100k**; **$200** M2M; **$240** on-demand. Overage **$3.50 / 10k** annual, **$4.20** M2M, **$5** on-demand. Retention add-on **$1.50 / $3 / $4 per 10k LLM spans** for 30 / 60 / 90-day traces | Default **15 days** traces |
| **Honeycomb** | **Event** = **one span** (SpanEvent/Link also count) | Free **20M events**/mo + **100M** metric datapoints. Pro starting **$150**/mo, up to **750M** events. From **2026-07-01** new Pro **$3.00 / million events** vs legacy **$1.30 / million** | Plan-default (not a 14d/400d SKU) |
| **Phoenix / Tempo self-host** | Disks + query compute | No SaaS trace SKU. Constraint is **payload size** and **queue** | You choose |

**`$ per 1k traces` (named shape):**

- LangSmith base: **$0.50 / 1k traces** (official). Extended: **$5.00 / 1k traces** (official).
- Datadog: **not priced per trace**. **[inferred]** 1 agent request × **8 LLM calls** = 8 billable spans. Annual overage **$3.50/10k LLM spans** = **$0.35 / 1k LLM spans** = **$2.80 / 1k such requests**. The $160 package is **$1.60 / 1k LLM spans** if you fill the 100k (and $0 inside free 40k).
- Honeycomb: **[inferred]** new Pro **$3.00 / 1M events** = **$0.003 / 1k events**. A **25-span** agent turn = **$0.075 / 1k traces**. Refinery drops do not bill. Legacy $1.30/M = **$0.0013 / 1k events**.
- ⚠️ Third-party posts still quote LangSmith **$2.50 / 1k base traces**. That **does not match** `docs.langchain.com/langsmith/usage-and-billing` as of 2026-08-21 (0.05¢). Do not mix the two.

**Auto-upgrade tax (LangSmith).** Online evaluators and automation rules **default to extending retention**. One matching run upgrades the **entire trace**; a **thread-level** rule upgrades **every trace in the thread**. UI feedback/notes/annotation-queue adds do **not** upgrade. Experiments start at extended. This is how a 14-day debug project becomes a 400-day invoice.

**Engine** (pricing FAQ estimate): **~5–30 LCU/run** → **[inferred] ~$7.50–$45 per Engine run**, not per 1k. At 1k Engine runs that is **$7.5k–$45k** — a different product from tracing.

**Ingest caps that are cost (and 429) controls:**

| Plan | Events / hour | Payload / hour |
| --- | --- | --- |
| Developer, no card | 50,000 | **500 MB** |
| Developer, card on file | 250,000 | **2.5 GB** |
| Startup/Plus | 500,000 | **5.0 GB** |
| Enterprise | Custom | Custom |

Plus ALB: **5,000** `POST|PATCH /runs*` per **minute** per key (SDK batches ≤ **100** runs/call). Developer no-card: **5,000 traces / calendar month** then 429.

**Product meter (assumption, so TCO is not “trace SKU only”).** Datadog Cost Overview uses **public provider prices × annotated tokens**. This research file does not publish a model SKU; use a stated placeholder matching that method: **$3 / $15 per MTok** in/out, **8 chat calls / run**, **2,000 input + 400 output tokens / call**, no cache:

\[
C_{\mathrm{product,1k}} = 1000 \times 8 \times \frac{2000\cdot 3 + 400\cdot 15}{10^{6}} = \$96
\]

Cache-miss storm or missing cache splits: finance sees the full $96 even when hits existed (`PARTIAL COST`). Mix shift to a larger model, or `finish_reason=tool_calls` oscillating, is the token-burn anomaly; observability did not cause it.

| Workload (1k successful tasks) | Product **[assumed]** | Observability | TCO **[inferred]** |
| --- | --- | --- | --- |
| LangSmith base, hide content, eval opt-out | $96 | **$0.50** | **$96.50** |
| LangSmith extended (online eval default-on) | $96 | **$5.00** | **$101** |
| Datadog annual overage, 8 LLM spans/req | $96 | **$2.80** | **$98.80** |
| Datadog 90-day add-on at 3M LLM spans/mo | (live) | **[inferred] 300 × $4 = $1,200/mo** extra | retention philosophy ≠ LangSmith 400d |
| Honeycomb 25 spans/trace, new Pro | $96 | **$0.075** | **$96.08** (Refinery mandatory as width grows) |
| + 100% online judge + Engine | $96 + judge | traces **and** **$7.50–$45/run** Engine | Engine dominates |

Observability is **~0.5–5%** of LLM **until** you enable content, auto-extend, 100% judges, or Engine. Deep agent trees **favor Datadog** (non-LLM spans free) and **punish Honeycomb** (every span is an event). A gateway that is **one LLM span per proxy call** makes Datadog and LangSmith converge.

### 3.2 Latency — p50 / p95 / p99 (label **[inferred]**)

⚠️ **No vendor publishes a universal “trace ingest p99 = X ms” SLO.** Bound the pipeline from published knobs. User-facing p50/p95/p99 are the **agent request**, not the collector.

| Stage | Published delay source | Effect on tails |
| --- | --- | --- |
| SDK batch | LangSmith `auto_batch_tracing`; OTel `BatchSpanProcessor` timeout | Seconds of holdback before the collector even sees the span |
| Tail sample | Default `decision_wait=30s`; Grafana Cloud metrics slack **30s** | Completeness vs freshness; slack+wait **zeros RED metrics** |
| Kafka | Partition lag | Back-pressure absorber; p99 is *your* cluster |
| Query | Tempo `query_frontend` `duration_slo: 5s` (search / by-id) | **Read** SLO |
| Alert | Honeycomb trigger **event latency** = event timestamp vs arrival; long agent traces inflate the chart even when spans arrive promptly | Mis-tuned duration misses delayed spans |

Working envelopes (**[inferred]** — architecture mapping, not a vendor SLO):

| Percentile | User e2e (agent) | Trace completeness (ingest) | Query | Alert |
| --- | --- | --- | --- | --- |
| **p50** | **[inferred]** inner `chat` TTFT on cache-hit; not the UX | SDK batch holdback (seconds) | well under 5s if indexed | — |
| **p95** | **[inferred] 10–120 s** documented agent e2e band; SLO **this**, not inner chat | `decision_wait` 30s dominates if tools/HITL | Tempo 5s **query** SLO example | event latency chart lies if you use e2e duration |
| **p99** | **[inferred]** HITL / tool timeout / 25k-run reject; raise `decision_wait` to ≥ p99 e2e + slack | Kafka lag + late MCP spans; `trace_dropped_too_early` if `num_traces` too small | 5s search SLO miss = **read** incident, not ingest | pages that never stop if trigger uses agent duration as arrival lag |

| Tier | Mitigations |
| --- | --- |
| p50 | Hide content (bytes); 100% metrics so token-burn alerts do not wait for traces; blob off-band |
| p95 | Two-tier sticky traceID; `decision_wait_after_root_received` or wait ≥ p99 e2e; sanitize span names for spanmetrics; sample online judges **off** the request path |
| p99 | Kafka `partition_traces_by_id`; `non_sampled_cache_size ≫ num_traces`; WAL/`file_storage` on exporter; never put ingest lag on the user SLO; Honeycomb triggers on **arrival**, not e2e duration |

Datadog `ml_obs.*` and LangSmith monitoring metadata **survive** trace deletion (15 mo metrics; metadata >30 days after base deletion). Alert on token burn after the forensic tree is gone.

### 3.3 Throughput and back-pressure

Throughput is \(\min(\mathrm{SDK\ queue}, \mathrm{collector\ limiter}, \mathrm{Kafka\ produce}, \mathrm{sampler\ num\_traces}/W, \mathrm{backend\ 429s})\).

Published saturation points: Plus **500k events/h** and **5.0 GB/h**; ALB **5k /runs\*/min**; LangSmith **25k runs/trace**; Phoenix queue **20k**; gRPC **4 MB**; Tempo example `ingestion_rate_limit_bytes: 15_000_000` (15 MB/s/tenant) — **example config, not Grafana Cloud’s unpublished tenant contract**. Developer no-card **5k traces/calendar month**.

Back-pressure is a protocol:

1. `memory_limiter` first. Soft = `limit_mib - spike_limit_mib`: refuse with **non-permanent** error so receivers retry and apply **upstream** back-pressure. Hard: refuse + force GC. `GOMEMLIMIT` ≈ **80%** of container memory. Forced GC that cannot free exporter-queue-held data **backs off exponentially**.
2. Exporters: `sending_queue` + `retry_on_failure`; optional `file_storage` so the queue survives restart. Queue full → **drop and count**. Watch `otelcol_processor_refused_spans` and queue size vs capacity. OTLP receivers that ignore retryable errors turn limiter protection into **silent loss**.
3. Kafka between tiers: consumer **must propagate** downstream processor errors so the consumer **pauses** (true back-pressure) rather than OOM the collector. Agent traces are **wide** (many spans) and **slow** (tools, humans, MCP); in-memory `num_traces=50k`, 30s wait OOMs first.
4. Sampling under overload (order): keep ERROR / `content_filter` / policy-deny / HITL → keep high-latency roots → bytes/rate token buckets → probabilistic remainder with `tracestate` → SDK head sample **only** as last-ditch (accept bias). Honeycomb **throughput** samplers target spans/sec. OTel `composite` allocates e.g. 50% of `max_total_spans_per_second` to errors. `drop_pending_traces_on_shutdown`: drop vs decide on partial data — pick explicitly; default partial decisions **look like** missing children.
5. User path **fail-opens** on exporter loss (graceful degradation). Legal/audit path **fail-closes** (do not execute the tool if the WORM append failed). Those are different breakers.

Worked admission **[inferred]**: 100 traces/s × 25 spans × 10 KB = **25 MB/s** into Plus’s 5.0 GB/h (**~1.39 MB/s**) — you are 18× over the **payload** cap with content on, while 100 × 3600 = 360k events/h still fits 500k events. Content-on is a throughput bug, not a span-count bug.

### 3.4 Availability, RPO/RTO, compliance, explicit NFR trade-offs

⚠️ Research publishes no numeric RPO/RTO for LangSmith/Datadog/Phoenix ingest. Architecture mapping:

| NFR | Working target | Tension |
| --- | --- | --- |
| Availability | User SLO = root OK + allowed `finish_reason`. Collector 429 rate = 0. Exporter outage **must not** 500 the chat | Silent trace loss looks like quality stability; RED metrics go to zero if 30s slack + 30s wait |
| RPO | **Action audit: 0** (WORM, unsampled). Traces: 14d vs 400d vs 15d vs self-host WAL. Content blobs: independent TTL. SDK memory exporter RPO = the in-flight batch (unacceptable for proof) | “Delete the span” while S3 retains the prompt; GDPR wipe ≠ billing aggregates |
| RTO | Forensics = read tape + checkpoint. Resume = LangGraph `checkpoint_id` (re-executes). Metrics still alert after traces expire | Fast debug UI vs legal reconstruct |
| Consistency | Sticky traceID so keep/drop is total. Late-span cache. Adjusted counts | Head sample on MCP = missing child forever |
| Compliance | Trace backend = production database. Subprocessor if vendor holds prompts (LangSmith ToS-facing FAQ: they **do not train** on traces; still a DPA subprocessor). GDPR/CCPA: LangSmith deletes user data on traces within a day after retention; **some metadata retained indefinitely for analytics/billing**. PCI: do not put PAN in spans. Hybrid/self-host when prompts cannot leave the VPC | SaaS Messages-view polish vs residency |
| Cost vs completeness | 1% happy + 100% errors vs 100% content + auto-extend + Engine | Cheap dashboards that missed the jailbreak |
| Freshness vs completeness | `decision_wait` 5s vs 30s+ vs HITL minutes | Partial OK traces |

**Explicit trade-offs.**

| Dimension | Cheap / fast | Balanced | Strict / regulated |
| --- | --- | --- | --- |
| Traces | Phoenix/LangSmith Plus; 100% until 429; content on in non-prod | OTel once; tail sample errors+1%; hide content; blob URLs | VPC Tempo/S3; Hybrid only if data plane is yours |
| Metrics | Vendor UI | 100% `gen_ai.client.*` / `ml_obs.*`; sanitized spanmetrics | Same + exemplars; never high-card labels |
| Logs | stdout JSON | Control-safe LLM event; correlation ids | SIEM; no raw text |
| Trajectories | Messages view on SaaS | `thread_id` + root flag; forensic replay from I/O | Checkpoint DB + WORM; no model re-sample as proof |
| Retention | 14d / 15d | Extend **failures only**; opt out of eval auto-upgrade | 7y action audit object-lock; traces short |
| Audit | none | Platform OCSF writes | Two tapes; wrap reads (LangSmith gap); separate from trace Postgres |

---

## 4. Distributed Resilience & Security

### 4.1 Durable execution — Temporal / Kafka

Agent turns are **stateful workflows** that outlive a process: model stream, MCP call, HITL, checkpoint. Telemetry that lives in the SDK batch is **not** durable.

**Temporal (enterprise mapping of LangGraph checkpoint + independent WAL).** Workflow id = `tenant:agent:thread_id`. Activities = (policy/PII), (LLM call **emitting a span**), (MCP `tools/call`), (checkpoint write), (WORM action append). Workflow **replay reconstructs control state** from history; activities must be idempotent and **must not re-sample the model** inside a replay-unsafe closure. That is the same distinction LangGraph documents: resume from `checkpoint_id` re-executes nodes (LLM/tools **may differ**); forensic reconstruct reads the tape. Continue-As-New at history bounds — cousin to LangSmith’s **25k runs/trace**. Compensating action for a bad turn = new trace in the thread, not “overwrite the WORM row.” If the requirement is “we can prove what the agent did,” the audit store cannot be the SDK’s memory exporter: **WAL (Tempo ingester WAL is a documented production requirement) + checkpoint DB + SIEM**, independent failure domains.

**Kafka.** Edge collector → `kafkaexporter` with `partition_traces_by_id: true` (mutually exclusive with `message_key_from_metadata_key`; Jaeger encodings already key by trace ID) → sampling collectors → backends. One partition maps to one sampling instance — this **is** the sticky-routing invariant. Consumer pauses on downstream processor errors (true back-pressure). Poison (oversized gRPC 4 MB, 25k-run overflow, unparseable OTLP) → DLQ; do not block the partition. Online path: product request **does not** wait for Kafka ack of traces; it **does** wait for WORM ack if the tool is effectful.

> ⚠️ Gap: research has no Temporal replay-cost numbers for multi-MB agent traces and no Kafka lag SLO for OTLP buses. Treat Temporal here as the durable-execution mapping of checkpoint vs tape; treat Kafka as the published collector buffer.

**Resume keys.** `trace_id` (W3C). `thread_id` + `ls_agent_type=root`. LangGraph `checkpoint_id`. MCP `mcp.session.id` + `gen_ai.tool.call.id`. Blob URI on the span. None of these is a substitute for the others.

### 4.2 Failure taxonomy

| Class | Symptom | Handler |
| --- | --- | --- |
| Transient | Provider 429/5xx; LangSmith ALB 5k/min or hourly GB 429; collector limiter refuse (non-permanent); Kafka lag; Phoenix `RESOURCE_EXHAUSTED` | Full-jitter retry on **idempotent** export; honor `Retry-After`; pause Kafka consumer; user path fail-open |
| Permanent | Illegal span payload > 4 MB / `max_bytes_per_trace`; 25k-run cap; `gen_ai.*` attr rejected by a pin; hide processor **dropping** a TOOL span | Truncate/blob; split the thread; redact **fields**, never drop TOOL spans; do not retry |
| Poison pill | Head sample on MCP so the tree is permanently half; load balancer **without** traceID affinity; `decision_wait` < tool/HITL; content-on copied from staging env; promote a leaking trace into a **dataset** (indefinite) | Tail sample + sticky routing; wait ≥ p99 e2e; hide in prod; datasets are a GDPR time bomb — copy redacted |
| Semantic | Sampling bias (keep errors, miss 2M-token 200s; dynamic key on `http.status_code` but not `finish_reasons` under-keeps `content_filter`; throughput sampler drops the new intent class); `count()` without \(1/p\); tools swallowing exceptions (Timeline failures empty); metrics-generator 30s slack + 30s wait = RED=0 during the incident; Datadog `PARTIAL` treated as a real price bug; Honeycomb trigger on e2e duration | Policy stack in §2.1/§3.3; adjusted counts; re-raise tool errors onto span status; alert on `ml_obs.*` not trace freshness; do not “fix” a flake by re-sampling the model |

**Missing spans (broken trees)** — cause → fix: head sample on MCP → inherit parent / sample MCP at 100%; no traceID affinity → two-tier or Kafka key; `decision_wait` short → wait or `decision_wait_after_root_received`; 25k cap → thread of traces; redact dropping the span → redact fields; gRPC 4 MB → truncate/blob; 429 payload → hide content / raise plan / sample. Create-without-completion is the intermittent “run started, never finished” shape.

### 4.3 Circuit breaker and fallbacks

Per downstream (OTLP backend, Kafka, MCP server, model provider):

- **Closed:** export/call flows; consecutive failures or error-rate window trip to open.
- **Open:** fail fast; start recovery timer. **Traces:** WAL or drop+count; **user chat continues** (fail-open). **Action audit / effectful tool:** **fail-closed** (no side effect without a WORM row).
- **Half-open:** one probe (`half_open_max`). Success → closed; fail → open.

Published, not folklore:

1. Collector `memory_limiter` non-permanent refuse — this **is** a breaker that pushes back on receivers.
2. LangSmith tracing usage limits — 429 on monthly all-traces or extended-traces; extended cap also blocks retention-upgrading evals/rules.
3. LangSmith 429 classes — ALB 5000/min `/runs*`, hourly events/bytes, monthly unique traces.
4. Phoenix queue 20k → `RESOURCE_EXHAUSTED`.
5. Datadog SDK root sampling — cost breaker, not a GenAI policy engine.
6. Honeycomb Refinery throughput samplers — spans/sec target.

**Fallback chain (telemetry):** primary OTLP (Tempo / LangSmith / Phoenix) → secondary WAL/`file_storage` → **deterministic degrade** (drop + `dropped_spans` counter; metrics still incremented locally). Deterministic degrade must still emit the **control-safe log line** and, for tools, the **WORM row**. Do not fall back from WORM-fail to “execute anyway.” Do not fall back from hide-content to “dump the prompt so we can debug prod.” Do not fall back from tail sample to prompt-inspecting head sample.

⚠️ No vendor publishes “circuit breaker trips/hour” as an SLO. Design for **dropped traces ≠ dropped audit**.

### 4.4 Zero-Trust MCP, tool RBAC, PII detect→redact→audit, immutable logs

**Zero-Trust MCP and traces as a side channel.** CoSAI: log all agent/tool/prompt/model interactions; OTel for linkability; **immutable** records of actions and authorizations; do not pass user OAuth tokens through (RFC 8693 token exchange); treat MCP returned content as untrusted. Groundcover: MCP deprecates protocol-level custom logging in favor of OTel so the tool call continues into the **server’s** DB/HTTP spans. Zero-Trust for observability: the **trace backend is not implicitly trusted with plaintext PII** just because SRE has access.

| Plane | Always | Break-glass |
| --- | --- | --- |
| Metadata traces | model, tokens, latency, tool **name**, policy decision, `error.type` enum | — |
| Content | — | encrypted blob, short TTL, JIT access, ticketed |
| Audit of observability | who exported / viewed (not the agent’s tool audit) | IdP session + trace-ACL proxy — LangSmith **gap** on reads |

SEP-414 `_meta.traceparent` is a **security** control as well as a UX one: two traces means the MCP server’s DB span is unlinkable, which is how exfil looks like “the agent never called SQL.”

**Tool RBAC.** Viewer sees metadata; Debugger sees redacted content; Privacy/legal sees blobs; **no** engineer role that can `projects:increase-trace-tier` on a HIPAA project without a ticket (LangSmith: that permission is **independent** of `projects:update`). Plus: org roles User/Admin only. Enterprise: custom SSO, ABAC, RBAC (GA cited Mar 2026). Honeycomb: SSO on Pro; Query Data API / PrivateLink / Private Cloud on Enterprise. Grafana: folder permissions + Tempo multi-tenant overrides. Allowlist `execute_tool {name}` in spanmetrics; do not take tool names from the model’s free text.

**PII pipeline:** detect → redact **before write** (SDK + collector allowlist + HMAC identifiers with a key **not** in the trace + cap string length + drop embedding vectors) → audit placeholder (hash, never raw). Still leak: tool arguments, retrieval documents on `RETRIEVER` spans, `user.id` as metric labels, eval datasets promoted from prod, screenshots (`OPENINFERENCE_HIDE_INPUT_IMAGES`, `BASE64_IMAGE_MAX_LENGTH`). Token-level redaction recall is never 100%; architect as if regex will miss. Treat a trace export as a **DSAR / breach** surface. Datadog: Sensitive Data Scanner integrated with Agent Observability.

**Immutable audit — two tapes:**

| Tape | Contents | Format / sink |
| --- | --- | --- |
| **Agent action audit** | Who (user principal + agent id), what tool, args hash, policy decision, `trace_id`, `checkpoint_id` | Append-only; WORM object lock; **not sampling-eligible** |
| **Platform audit** | Who changed sampling, retention, API keys, SSO, viewed/exported traces | LangSmith Enterprise; self-hosted Helm **≥ 0.12.33**; **OCSF 1.7.0** API Activity (class 6003); `GET /api/v1/audit-logs`; org `organization:manage` only; **no UI**, API-only; ~70+ **write** ops |

Self-hosted enablement: `DEFAULT_ORG_FEATURE_CAN_USE_AUDIT_LOGS` / `AUDIT_LOGS_ENABLED`; existing orgs need DB `can_use_audit_logs`. Ship OCSF to Splunk/Datadog SIEM; **do not** store platform audit in the same Postgres you would wipe for GDPR of traces. If “who looked at this customer’s prompt” is a requirement, wrap the UI — ⚠️ gap.

---

## 5. Production Enterprise Code

Stdlib-only observability pipeline: W3C trace ids, GenAI/OpenInference spans, control-safe JSON logs with correlation ids, PII detect→redact→audit, tail-style keep/drop + adjusted counts, 100% metrics with a high-cardinality label guard, full-jitter retries, circuit breaker (closed → open → half-open), fallback primary OTLP → WAL → drop+count, unsampled hash-chained WORM, trajectory projection, forensic replay (no model call). User path fail-open; effectful tool fail-closed without WORM. Run: `python obs_pipeline.py`.

```python
#!/usr/bin/env python3
"""Agent observability pipeline (stdlib only). Run: python obs_pipeline.py"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

POLICY_VERSION = "obs-2026-08-21"
BREAKER_FAILURES = 3
BREAKER_RECOVERY_S = 0.05
LATENCY_KEEP_MS = 8_000
MAX_ATTR_CHARS = 2_048
WAL_MAX = 256
METRIC_LABEL_ALLOW = frozenset(
    {"provider", "model", "operation", "finish_reason", "error_type", "ml_app"}
)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
            "correlation_id": getattr(record, "correlation_id", None),
            "tenant": getattr(record, "tenant", None),
            "trace_id": getattr(record, "trace_id", None),
            "span_id": getattr(record, "span_id", None),
            "breaker": getattr(record, "breaker", None),
            "degraded": getattr(record, "degraded", None),
            "sample_rate": getattr(record, "sample_rate", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class CorrelationAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = dict(self.extra)
        extra.update(kwargs.pop("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs


def build_logger(correlation_id: str, tenant: str) -> CorrelationAdapter:
    base = logging.getLogger("obs.pipeline")
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    return CorrelationAdapter(
        base, {"correlation_id": correlation_id, "tenant": tenant}
    )


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class CircuitOpenError(Exception):
    pass


class WormClosed(PermanentError):
    pass


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, failures: int = BREAKER_FAILURES, recovery_s: float = BREAKER_RECOVERY_S):
        self.failures = failures
        self.recovery_s = recovery_s
        self._state = BreakerState.CLOSED
        self._n = 0
        self._opened_at = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> BreakerState:
        with self._lock:
            if self._state is BreakerState.OPEN and (time.monotonic() - self._opened_at) >= self.recovery_s:
                self._state = BreakerState.HALF_OPEN
            return self._state

    def allow(self) -> None:
        if self.state is BreakerState.OPEN:
            raise CircuitOpenError("circuit open")

    def record_success(self) -> None:
        with self._lock:
            self._n = 0
            self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._n += 1
            if self._state is BreakerState.HALF_OPEN or self._n >= self.failures:
                self._state = BreakerState.OPEN
                self._opened_at = time.monotonic()


def full_jitter_sleep(attempt: int, base: float = 0.01, cap: float = 0.05, rng: random.Random | None = None) -> float:
    r = rng or random
    return r.uniform(0.0, min(cap, base * (2 ** attempt)))


def retry_call(
    fn: Callable[[], Any],
    *,
    attempts: int = 4,
    breaker: CircuitBreaker,
    rng: random.Random,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    breaker.allow()
    last: Exception | None = None
    for i in range(attempts):
        try:
            out = fn()
            breaker.record_success()
            return out
        except CircuitOpenError:
            raise
        except PermanentError:
            breaker.record_failure()
            raise
        except TransientError as exc:
            last = exc
            breaker.record_failure()
            if i == attempts - 1 or breaker.state is BreakerState.OPEN:
                break
            sleep(full_jitter_sleep(i, rng=rng))
            try:
                breaker.allow()
            except CircuitOpenError:
                break
    if last:
        raise last
    raise TransientError("retry exhausted")


_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("KEY", re.compile(r"\b(?:sk|rk)-[A-Za-z0-9]{8,}\b")),
    ("BEARER", re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.I)),
    ("PHONE", re.compile(r"\b\+?1?\d{10,12}\b")),
)


def hmac_id(value: str, key: str = "obs-hmac-demo") -> str:
    return hashlib.sha256(f"{key}:{value}".encode()).hexdigest()[:16]


def detect_redact(text: str) -> tuple[str, list[str]]:
    found: list[str] = []
    out = text
    for label, pat in _PII_PATTERNS:
        if pat.search(out):
            found.append(label)
            out = pat.sub(f"[REDACTED:{label}]", out)
    if len(out) > MAX_ATTR_CHARS:
        out = out[:MAX_ATTR_CHARS] + "…[truncated]"
    return out, found


@dataclass
class WormLog:
    rows: list[dict[str, Any]] = field(default_factory=list)

    def append(self, record: dict[str, Any]) -> str:
        prev = self.rows[-1]["hash"] if self.rows else "genesis"
        body = json.dumps(record, sort_keys=True, default=str)
        digest = hashlib.sha256((prev + body).encode()).hexdigest()
        row = dict(record)
        row["prev"] = prev
        row["hash"] = digest
        self.rows.append(row)
        return digest


def new_trace_id(rng: random.Random) -> str:
    return rng.randbytes(16).hex()


def new_span_id(rng: random.Random) -> str:
    return rng.randbytes(8).hex()


def traceparent(trace_id: str, span_id: str, sampled: bool) -> str:
    flags = "01" if sampled else "00"
    return f"00-{trace_id}-{span_id}-{flags}"


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_span_id: str
    name: str
    kind: str
    oi_kind: str
    start_ms: float
    end_ms: float = 0.0
    status: str = "OK"
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    sampled: bool | None = None
    sample_rate: float = 1.0

    @property
    def latency_ms(self) -> float:
        return max(0.0, self.end_ms - self.start_ms)

    def to_otlp(self) -> dict[str, Any]:
        return {
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "parentSpanId": self.parent_span_id,
            "name": self.name,
            "kind": self.kind,
            "startTimeUnixNano": int(self.start_ms * 1e6),
            "endTimeUnixNano": int(self.end_ms * 1e6),
            "status": {"code": 2 if self.status == "ERROR" else 1},
            "attributes": [
                {"key": k, "value": v} for k, v in self.attributes.items()
            ],
        }


class Metrics:
    def __init__(self) -> None:
        self.counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self.rejected_labels = 0

    def add(self, name: str, value: float, labels: dict[str, str]) -> None:
        if any(k not in METRIC_LABEL_ALLOW for k in labels):
            self.rejected_labels += 1
            labels = {k: v for k, v in labels.items() if k in METRIC_LABEL_ALLOW}
        key = (name, tuple(sorted(labels.items())))
        self.counters[key] = self.counters.get(key, 0.0) + value


class TailSampler:
    def __init__(self, happy_p: float, rng: random.Random, latency_ms: float = LATENCY_KEEP_MS):
        self.happy_p = happy_p
        self.rng = rng
        self.latency_ms = latency_ms
        self.decision_cache: dict[str, tuple[bool, float]] = {}

    def decide(self, root: Span, children: list[Span]) -> tuple[bool, float]:
        cached = self.decision_cache.get(root.trace_id)
        if cached:
            return cached
        keep, p = False, 1.0
        finish = str(root.attributes.get("gen_ai.response.finish_reasons", ""))
        reasons = {finish} | {
            str(s.attributes.get("gen_ai.response.finish_reasons", "")) for s in children
        }
        if root.status == "ERROR" or any(s.status == "ERROR" for s in children):
            keep = True
        elif "content_filter" in reasons or root.attributes.get("policy.decision") == "deny":
            keep = True
        elif root.attributes.get("policy.decision") == "hitl":
            keep = True
        elif root.latency_ms >= self.latency_ms or any(s.latency_ms >= self.latency_ms for s in children):
            keep = True
        elif self.rng.random() < self.happy_p:
            keep, p = True, max(self.happy_p, 1e-9)
        else:
            keep, p = False, max(self.happy_p, 1e-9)
        self.decision_cache[root.trace_id] = (keep, p)
        return keep, p


class SpanExporter:
    def __init__(
        self,
        primary: Callable[[list[dict[str, Any]]], None],
        wal: list[list[dict[str, Any]]],
        breaker: CircuitBreaker,
        rng: random.Random,
        log: CorrelationAdapter,
    ) -> None:
        self.primary = primary
        self.wal = wal
        self.breaker = breaker
        self.rng = rng
        self.log = log
        self.dropped = 0
        self.exported = 0
        self.wal_writes = 0

    def export(self, spans: list[Span]) -> str:
        payload = [s.to_otlp() for s in spans]
        try:
            retry_call(lambda: self.primary(payload), breaker=self.breaker, rng=self.rng)
            self.exported += len(spans)
            self.log.info("otlp_export_ok", extra={"degraded": False})
            return "primary"
        except (TransientError, CircuitOpenError, PermanentError) as exc:
            self.log.warning(
                "otlp_export_fail",
                extra={"degraded": True, "breaker": self.breaker.state.value},
            )
            if len(self.wal) >= WAL_MAX:
                self.dropped += len(spans)
                self.log.error("wal_full_drop", extra={"degraded": True})
                return "dropped"
            self.wal.append(payload)
            self.wal_writes += 1
            self.log.warning("wal_fallback", extra={"degraded": True})
            _ = exc
            return "wal"


def redact_span(span: Span) -> list[str]:
    found: list[str] = []
    for key in list(span.attributes):
        val = span.attributes[key]
        if not isinstance(val, str):
            continue
        if key in {"gen_ai.input.messages", "gen_ai.output.messages",
                   "gen_ai.tool.call.arguments", "gen_ai.tool.call.result",
                   "input.value", "output.value"}:
            red, hits = detect_redact(val)
            span.attributes[key] = red
            found.extend(hits)
            span.attributes[f"{key}.sha256"] = hashlib.sha256(val.encode()).hexdigest()[:16]
    if "user.id" in span.attributes:
        span.attributes["user.id.hmac"] = hmac_id(str(span.attributes.pop("user.id")))
    return found


def project_trajectory(spans: list[Span]) -> list[dict[str, str]]:
    ordered = sorted(spans, key=lambda s: s.start_ms)
    out: list[dict[str, str]] = []
    for s in ordered:
        oi = s.oi_kind
        if oi == "LLM":
            out.append({"role": "ai", "name": s.name, "text": str(s.attributes.get("gen_ai.output.messages", ""))[:200]})
        elif oi == "TOOL":
            out.append({"role": "tool", "name": s.name, "text": str(s.attributes.get("gen_ai.tool.call.result", ""))[:200]})
        elif oi == "AGENT" and not s.parent_span_id:
            out.append({"role": "human", "name": s.name, "text": str(s.attributes.get("input.value", ""))[:200]})
    return out


def forensic_replay(spans: list[Span], checkpoint: dict[str, Any]) -> dict[str, Any]:
    """Read recorded I/O + checkpoint. Never call the model."""
    return {
        "mode": "forensic",
        "checkpoint_id": checkpoint.get("checkpoint_id"),
        "messages": project_trajectory(spans),
        "tool_calls": [
            s.attributes.get("gen_ai.tool.call.id")
            for s in spans if s.oi_kind == "TOOL"
        ],
        "model_invoked": False,
    }


def cost_sheet_per_1k(llm_calls: int = 8, spans: int = 25, in_tok: int = 2000, out_tok: int = 400) -> dict[str, float]:
    product = 1000 * llm_calls * (in_tok * 3 + out_tok * 15) / 1_000_000
    return {
        "product_assumed_usd": round(product, 2),
        "langsmith_base_usd": 0.50,
        "langsmith_extended_usd": 5.00,
        "datadog_overage_8_llm_usd": round(1000 * llm_calls * (3.50 / 10_000), 2),
        "honeycomb_25span_usd": round(1000 * spans * (3.00 / 1_000_000), 4),
        "engine_per_run_low_usd": 7.50,
        "engine_per_run_high_usd": 45.00,
    }


def _end(span: Span, now: float, **attrs: Any) -> None:
    span.end_ms = now
    span.attributes.update(attrs)


class AgentPipeline:
    def __init__(self, rng: random.Random, log: CorrelationAdapter, exporter: SpanExporter, worm: WormLog, metrics: Metrics, sampler: TailSampler) -> None:
        self.rng = rng
        self.log = log
        self.exporter = exporter
        self.worm = worm
        self.metrics = metrics
        self.sampler = sampler

    def _record_metrics(self, spans: list[Span]) -> None:
        for s in spans:
            labels = {
                "provider": str(s.attributes.get("gen_ai.provider.name", "none")),
                "model": str(s.attributes.get("gen_ai.request.model", "none")),
                "operation": str(s.attributes.get("gen_ai.operation.name", s.oi_kind.lower())),
                "finish_reason": str(s.attributes.get("gen_ai.response.finish_reasons", "none")),
                "error_type": str(s.attributes.get("error.type", "none")),
                "ml_app": "support_agent",
            }
            self.metrics.add("gen_ai.client.operation.duration", s.latency_ms, labels)
            self.metrics.add(
                "gen_ai.client.token.usage",
                float(s.attributes.get("gen_ai.usage.input_tokens", 0))
                + float(s.attributes.get("gen_ai.usage.output_tokens", 0)),
                labels,
            )

    def run_turn(
        self,
        *,
        prompt: str,
        tool_args: str,
        tool_ok: bool,
        latency_ms: float,
        finish: str,
        policy: str,
        execute_tool: bool,
    ) -> dict[str, Any]:
        t0 = time.time() * 1000.0
        trace_id = new_trace_id(self.rng)
        root_id = new_span_id(self.rng)
        log = CorrelationAdapter(self.log.logger, {**self.log.extra, "trace_id": trace_id, "span_id": root_id})
        root = Span(
            trace_id, root_id, "", "invoke_agent support", "INTERNAL", "AGENT", t0,
            attributes={
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.provider.name": "openai",
                "gen_ai.request.model": "gpt-4.1",
                "openinference.span.kind": "AGENT",
                "input.value": prompt,
                "user.id": "alice@bank.example",
                "thread_id": "thr-1",
                "ls_agent_type": "root",
                "policy.decision": policy,
                "prompt.version": "pv-12",
            },
        )
        chat_id = new_span_id(self.rng)
        chat = Span(
            trace_id, chat_id, root_id, "chat gpt-4.1", "CLIENT", "LLM", t0 + 1,
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "openai",
                "gen_ai.request.model": "gpt-4.1",
                "gen_ai.input.messages": prompt,
                "openinference.span.kind": "LLM",
            },
        )
        children = [chat]
        tool: Span | None = None
        mcp: Span | None = None
        if execute_tool:
            tool_id = new_span_id(self.rng)
            tool = Span(
                trace_id, tool_id, chat_id, "execute_tool crm.lookup", "INTERNAL", "TOOL", t0 + 2,
                attributes={
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": "crm.lookup",
                    "gen_ai.tool.call.id": "call-1",
                    "gen_ai.tool.call.arguments": tool_args,
                    "openinference.span.kind": "TOOL",
                    "mcp.method.name": "tools/call",
                    "rpc.system": "mcp",
                },
            )
            mcp_id = new_span_id(self.rng)
            mcp = Span(
                trace_id, mcp_id, tool_id, "tools/call crm.lookup", "SERVER", "TOOL", t0 + 3,
                attributes={
                    "mcp.method.name": "tools/call",
                    "mcp.session.id": "sess-1",
                    "rpc.system": "mcp",
                    "openinference.span.kind": "TOOL",
                },
            )
            mcp.attributes["_meta.traceparent"] = traceparent(trace_id, tool_id, True)
            children.extend([tool, mcp])

        now = t0 + latency_ms
        _end(chat, now - 5, **{
            "gen_ai.output.messages": "ok" if tool_ok else "tool error",
            "gen_ai.usage.input_tokens": 2000,
            "gen_ai.usage.output_tokens": 400,
            "gen_ai.response.model": "gpt-4.1-2026-04",
            "gen_ai.response.finish_reasons": finish,
        })
        if tool and mcp:
            if not tool_ok:
                tool.status = "ERROR"
                mcp.status = "ERROR"
                root.status = "ERROR"
                tool.attributes["error.type"] = "TIMEOUT"
                mcp.attributes["error.type"] = "TIMEOUT"
            _end(tool, now - 2, **{"gen_ai.tool.call.result": "row" if tool_ok else "timeout"})
            _end(mcp, now - 1)
            if execute_tool:
                try:
                    self.worm.append({
                        "tape": "agent_action",
                        "policy_version": POLICY_VERSION,
                        "trace_id": trace_id,
                        "checkpoint_id": "ckpt-1",
                        "tool": "crm.lookup",
                        "args_sha256": hashlib.sha256(tool_args.encode()).hexdigest()[:16],
                        "decision": policy,
                        "principal": hmac_id("alice@bank.example"),
                    })
                except Exception as exc:
                    raise WormClosed("audit append failed") from exc
        _end(root, now, **{"gen_ai.response.finish_reasons": finish})

        pii_hits: list[str] = []
        for s in [root, *children]:
            pii_hits.extend(redact_span(s))

        self._record_metrics([root, *children])

        keep, p = self.sampler.decide(root, children)
        root.sampled, root.sample_rate = keep, p
        for s in children:
            s.sampled, s.sample_rate = keep, p
        adjusted = (1.0 / p) if keep else 0.0
        log.info(
            "llm_call",
            extra={
                "sample_rate": p,
                "degraded": False,
            },
        )
        sink = "dropped_unsampled"
        if keep:
            sink = self.exporter.export([root, *children])
        else:
            self.metrics.add("trace.adjusted_drop", 1.0, {"ml_app": "support_agent", "operation": "invoke_agent", "provider": "openai", "model": "gpt-4.1", "finish_reason": finish, "error_type": "none"})

        return {
            "trace_id": trace_id,
            "keep": keep,
            "sample_rate": p,
            "adjusted_count": adjusted,
            "sink": sink,
            "pii_hits": sorted(set(pii_hits)),
            "trajectory": project_trajectory([root, *children]),
            "forensic": forensic_replay([root, *children], {"checkpoint_id": "ckpt-1"}),
            "traceparent": traceparent(trace_id, root_id, bool(keep)),
            "user_ok": True,
            "spans": [root, *children],
        }


class FlakyOTLP:
    def __init__(self, fail_first: int) -> None:
        self.fail_first = fail_first
        self.calls = 0
        self.received: list[list[dict[str, Any]]] = []

    def __call__(self, payload: list[dict[str, Any]]) -> None:
        self.calls += 1
        if self.calls <= self.fail_first:
            raise TransientError("backend 429")
        self.received.append(payload)


def main() -> None:
    rng = random.Random(42)
    cid = str(uuid.uuid4())
    log = build_logger(cid, "tenant-a")
    worm = WormLog()
    metrics = Metrics()
    sampler = TailSampler(happy_p=0.0, rng=rng)
    primary = FlakyOTLP(fail_first=3)
    breaker = CircuitBreaker()
    wal: list[list[dict[str, Any]]] = []
    exporter = SpanExporter(primary, wal, breaker, rng, log)
    pipe = AgentPipeline(rng, log, exporter, worm, metrics, sampler)

    error_turn = pipe.run_turn(
        prompt="Refund 123-45-6789 for alice@bank.example",
        tool_args='{"email":"alice@bank.example","ssn":"123-45-6789"}',
        tool_ok=False,
        latency_ms=900,
        finish="tool_calls",
        policy="allow",
        execute_tool=True,
    )
    happy_turn = pipe.run_turn(
        prompt="hello",
        tool_args="{}",
        tool_ok=True,
        latency_ms=400,
        finish="stop",
        policy="allow",
        execute_tool=False,
    )
    slow_turn = pipe.run_turn(
        prompt="long research",
        tool_args="{}",
        tool_ok=True,
        latency_ms=9_500,
        finish="stop",
        policy="allow",
        execute_tool=False,
    )

    breaker_open_before_probe = breaker.state.value
    time.sleep(BREAKER_RECOVERY_S + 0.02)
    recovered = pipe.run_turn(
        prompt="after outage",
        tool_args="{}",
        tool_ok=True,
        latency_ms=9_100,
        finish="stop",
        policy="hitl",
        execute_tool=False,
    )

    high_card = Metrics()
    high_card.add("bad", 1.0, {"user.id": "alice", "ml_app": "support_agent"})

    sheet = cost_sheet_per_1k()
    report = {
        "policy_version": POLICY_VERSION,
        "error_keep": error_turn["keep"],
        "error_sink": error_turn["sink"],
        "error_pii": error_turn["pii_hits"],
        "happy_keep": happy_turn["keep"],
        "slow_keep": slow_turn["keep"],
        "recovered_keep": recovered["keep"],
        "recovered_sink": recovered["sink"],
        "user_path_always_ok": all(t["user_ok"] for t in (error_turn, happy_turn, slow_turn, recovered)),
        "mcp_same_trace": error_turn["spans"][-1].trace_id == error_turn["trace_id"],
        "forensic_no_model": error_turn["forensic"]["model_invoked"] is False,
        "worm_rows": len(worm.rows),
        "worm_chain_ok": all(
            worm.rows[i]["prev"] == (worm.rows[i - 1]["hash"] if i else "genesis")
            for i in range(len(worm.rows))
        ),
        "wal_writes": exporter.wal_writes,
        "otlp_ok_batches": len(primary.received),
        "breaker_opened": breaker_open_before_probe,
        "high_card_rejected": high_card.rejected_labels == 1,
        "metric_series": len(metrics.counters),
        "content_redacted": "[REDACTED:EMAIL]" in str(error_turn["spans"][0].attributes.get("input.value")),
        "no_raw_user_id": "user.id" not in error_turn["spans"][0].attributes,
        "cost_per_1k": sheet,
        "trajectory_roles": [m["role"] for m in error_turn["trajectory"]],
    }
    assert report["error_keep"] is True
    assert report["happy_keep"] is False
    assert report["slow_keep"] is True
    assert report["user_path_always_ok"] is True
    assert report["mcp_same_trace"] is True
    assert report["forensic_no_model"] is True
    assert report["worm_rows"] == 1
    assert report["worm_chain_ok"] is True
    assert report["high_card_rejected"] is True
    assert report["content_redacted"] is True
    assert report["no_raw_user_id"] is True
    assert report["recovered_sink"] == "primary"
    assert report["otlp_ok_batches"] >= 1
    assert "EMAIL" in report["error_pii"] and "SSN" in report["error_pii"]
    assert sheet["langsmith_base_usd"] == 0.5
    assert sheet["datadog_overage_8_llm_usd"] == 2.8
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
```

**What the demo asserts.** ERROR and high-latency / HITL roots are kept; happy `finish_reason=stop` at `happy_p=0` is dropped; **metrics still increment** on the drop. PII in prompts and tool JSON is redacted **before** export; `user.id` is HMAC’d off the span. MCP child shares `trace_id`. Primary OTLP 429s trip the breaker; WAL absorbs; user path stays OK. After recovery the half-open probe exports. WORM has the tool invocation even if traces later drop. Forensic replay sets `model_invoked=False`. Cost sheet matches research SKUs: LangSmith **$0.50 / 1k**, Datadog **[inferred] $2.80 / 1k** at 8 LLM spans, product placeholder **$96 / 1k**. High-cardinality `user.id` metric labels are rejected.

**Interview talking point:** jittered retries and a WAL handle ingest 429s; they do not make a dropped trace into an audit hole. Three sinks (100% metrics, sampled traces, unsampled WORM) are three failure classes.

---

## 6. Architectural System Design Scenarios

Exactly two enterprise designs. Numbers are from the research file. Decision rule: **OTel GenAI + W3C once**; 100% metrics; sampled redacted traces; unsampled WORM keyed by `trace_id`. Do not dual-SDK. Do not put prompts on attributes. Do not prove production with a model re-sample.

### Scenario 1 — Bank: hybrid control, VPC data, WORM audit

**Problem statement.** Support/ops agent in a bank: prompts are customer PII; tool invocations must be provable for **7 years**; Zero-Trust MCP (no OAuth passthrough; `_meta.traceparent`). Leadership wants LangSmith Messages-view. Threats: SaaS holds plaintext prompts; online eval auto-extends every thread to 400 days; SRE role can raise retention; MCP `tools/call` breaks the trace so the SQL span is unlinkable; regex redaction misses tool JSON; replay “to reproduce the bug” re-calls the model and cannot match prod; platform audit does not record **reads**. NFR: action-audit RPO=0; traces may be 14–90 days; content blobs JIT. Cost: do **not** quote SaaS `$ / 1k traces` as TCO — S3 is cheap; **query + engineer time** dominate. Product **[assumed]** ~$96/1k tasks; LangSmith extended **$5/1k** is the wrong SKU if prompts cannot leave the VPC.

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Agent UI / │ SSE │ CONTROL PLANE (SaaS IdP OK; sampling policy; not prompts) │
│ MCP client │────▶│ Gateway: SSO, correlation-id, W3C + SEP-414 _meta         │
└────────────┘     │ Policy: PII detect→redact→audit BEFORE any attribute      │
                   │ RBAC: Viewer=metadata; Debugger=redacted; Privacy=blobs;  │
                   │  increase-trace-tier ticketed (≠ projects:update)         │
                   │ Orchestrator: Temporal wf=tenant:agent:thread; Kafka      │
                   │  outbox WORM before effectful tools; fail-closed tools    │
                   │  fail-open user span export                               │
                   └────┬──────────────────────────────┬───────────────────────┘
                        │ OTLP once                    │ MCP tools/call
                        ▼                              ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ COLLECTOR DATA   │        │ TOOL PROXIES                 │
                   │ PLANE (VPC)      │        │ audience-bound tokens; no    │
                   │ edge → Kafka     │        │ passthrough; wrap function   │
                   │  partition_      │        │ body (execute_tool child);   │
                   │  traces_by_id    │        │ untrusted MCP content        │
                   │ tail: ERROR/HITL │        │                              │
                   │  /$ / 1% happy   │        │                              │
                   └────────┬─────────┘        └──────────────┬───────────────┘
                            │                                 │
                            ▼                                 ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ PERSISTENCE (independent domains)                         │
                   │ Tempo/S3 metadata traces; encrypted content bucket (URI   │
                   │  on span); LangGraph checkpoints; WORM object-lock 7y;    │
                   │  OCSF platform audit to SIEM (not the trace Postgres);    │
                   │  wrap UI for read-audit (LangSmith gap)                   │
                   └───────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix.**

| Dimension | A. LangSmith Cloud, content on, online eval default-extend, dual SDK | B. Recommended: OTel → Kafka → tail sample → Tempo/S3 VPC; blob URIs; unsampled WORM; Hybrid only if data plane is yours | C. Phoenix in-process 100% content + head-sample 1% including errors |
| --- | --- | --- | --- |
| Cost | Base **$0.50/1k** becomes **$5/1k** on eval match; Engine **$7.50–$45/run**; vendor holds 400d PII | S3 cheap; query/eng dominate; **[inferred]** env-specific TCO — do not paste SaaS SKUs | Cheap until 20k queue `RESOURCE_EXHAUSTED` and a missed jailbreak |
| Latency | SaaS UI fast; ingest p99 unpublished | You own p99 (`decision_wait`, Kafka lag); user SLO ≠ ingest | SDK batch + no tail wait; missing children at p99 tools |
| Ops | Messages-view polish; auto-extend surprise | Collector fleet; no Messages-view unless redacted replica export | One binary; no sticky routing |
| Security | Prompts at vendor; subprocessor judges/Engine; read-audit gap | VPC; JIT blobs; two tapes; RBAC split; redact tool JSON | Head sample deletes the incident; content in the app disk |
| Scalability | 5 GB/h / 25k runs/trace / 5k /runs*/min | Horizontal collectors; Kafka pause = back-pressure | Single-process queue is the ceiling |

**Decision rationale.** **B** is research scenario B: no prompts in SaaS; prove tools 7 years on an **unsampled** object-lock tape; Zero-Trust MCP with `_meta` context; content encrypted with span URI; LangSmith **Hybrid** only when the data plane is **your** traces DB. A is the dual-SDK + auto-extend + plaintext-at-vendor failure. C is the “we have Phoenix” failure: queue 20k, head sample bias, no WORM. Interview close: “The trace backend is a production database. Replay reads the tape. The 7-year proof is not Tempo.”

### Scenario 2 — High-QPS LLM gateway (millions of LLM spans)

**Problem statement.** Shared gateway in front of many apps: millions of LLM spans/month, fallbacks across providers, token economics, traces only for **0.1% + all errors**. Threats: 100% content traces to LangSmith (hourly GB 429 + $5/1k extended if anyone attaches an eval); Honeycomb billed on **every** span of a 20–200 span tree; Datadog 15-day default, 90-day add-on **[inferred] $1,200/mo at 3M LLM spans** (`300 × $4`); head-sample 1% drops `content_filter`; spanmetrics labels `user.id`; Grafana 30s slack + 30s tail wait zeros RED during the incident; Datadog `PARTIAL COST` paged as a price bug; inner `chat` p99 used as the UX SLO. Need: 100% token/duration metrics, tail-sampled forensics, low-cardinality `error.type` enum (`RATE_LIMITED|OVERLOADED|TIMEOUT|CONTENT_FILTERED|…`).

**Proposed architecture.**

```
┌────────────┐     ┌───────────────────────────────────────────────────────────┐
│ Apps /     │     │ CONTROL PLANE (gateway)                                   │
│ agents     │────▶│ Gateway: API keys, TPM, breaker per provider, 80% soft /  │
│            │     │  hard cap (not a dashboard); correlation-id + W3C         │
└────────────┘     │ Policy: content OFF; blob only on kept traces; redact     │
                   │ Router: fallbacks; publish error.type enum (low card)     │
                   │ Sampling policy: keep ERROR/content_filter/high-$; 0.1%   │
                   │  happy; metrics 100% always                               │
                   └────┬──────────────────────────────┬───────────────────────┘
                        │ one OTLP stream              │ provider complete()
                        ▼                              ▼
                   ┌──────────────────┐        ┌──────────────────────────────┐
                   │ COLLECTORS       │        │ DATA PLANE (providers)       │
                   │ memory_limiter → │        │ CLIENT chat spans; cache_*   │
                   │ Kafka by traceID │        │  token splits REQUIRED or    │
                   │ Refinery or OTel │        │  cost dashboards PARTIAL     │
                   │  tail            │        │                              │
                   └────────┬─────────┘        └──────────────┬───────────────┘
                            │                                 │
                            ▼                                 ▼
                   ┌───────────────────────────────────────────────────────────┐
                   │ TELEMETRY                                                 │
                   │ 100% gen_ai.client.* / ml_obs.* (15 mo); traces sampled   │
                   │  to Datadog (LLM-span bill; tools free) and/or Tempo      │
                   │  spanmetrics with sanitized names + exemplars; 2 Honeycomb│
                   │  SLOs = TTFT + availability; cost on a trigger            │
                   └───────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix.**

| Dimension | A. 100% LangSmith content + online eval auto-extend + inner-chat SLO | B. Recommended: 100% metrics; tail 0.1%+errors; Datadog LLM-span bill **or** Grafana spanmetrics; cache splits; `error.type` enum | C. Honeycomb 100% spans, no Refinery; head-sample 1% at SDK |
| --- | --- | --- | --- |
| Cost | **$0.50→$5/1k** traces + judge tokens + 5 GB/h 429s | Datadog **[inferred] $2.80/1k req** at 8 LLM spans overage (tools free); 90d add-on **$1,200/mo @ 3M**; product **$96/1k** assumed | **$0.075/1k** at 25 spans; **×8** at 200-span trees; unique keys keep 100% until the bill |
| Latency | Batch + eval on path if mis-attached | User SLO = gateway e2e/TTFT; ingest `decision_wait` off the SLO; avoid 30s+30s metric drop | Head sample is fast and **wrong**; p99 tools missing |
| Ops | 25k-run mega-traces; ALB 5k/min | Two-tier collectors; composite sampler budget to errors; adjusted counts | Refinery FieldList cardinality; trigger on e2e duration false pages |
| Security | Prompts in SaaS; datasets indefinite | Hide + SDS/allowlist; no `user.id` labels | Content in every event; conversation id as sampler key explodes |
| Scalability | Payload cap first (10 KB/event headroom **[inferred]**) | Metrics scale with traffic; traces do not; deep trees favor Datadog | Span-count bill is the ceiling; Refinery becomes mandatory |

**Decision rationale.** **B** is research scenario D: Datadog or Grafana metrics **100%**; traces tail-sampled; Datadog billing on LLM spans favors **deep** agent trees; if the gateway is **one span per proxy call**, Datadog and LangSmith converge. Publish a low-cardinality `error.type` enum; raw provider codes stay on the span. A couples cost to completeness and SLOs the wrong span. C either overpays Honeycomb or head-samples away `content_filter`. Interview close: “Bill tokens on metrics. Debug on 0.1% + errors. The 90-day add-on is a retention philosophy, not LangSmith’s 400-day one. Never SLO ingest p99 you do not have.”

---

*End of module. Six sections. Four mandatory topics (tracing, logging, monitoring, agent trajectories). Token `$ / 1k` tables use official LangSmith 0.05¢/0.50¢ SKUs and **[inferred]** Datadog/Honeycomb unit math dated 2026-08-21. No unpublished ingest p50/p95/p99 SLOs — percentiles are labeled **[inferred]** or bound from documented knobs (`decision_wait=30s`, Grafana 30s slack, Tempo query `duration_slo: 5s`, Plus 500k evt/h and 5.0 GB/h, 25k runs/trace, 14d/400d vs 15d).*
