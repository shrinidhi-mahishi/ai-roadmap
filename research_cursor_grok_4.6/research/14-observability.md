# Research: Observability
**Date researched**: 2026-08-21
**Sources consulted**: 63

Scope: **tracing** (OpenTelemetry GenAI SIG, OpenInference, LangSmith, Phoenix, W3C Trace Context, LLM/tool span types), **logging** (structured logs, correlation IDs, redaction, immutable audit), **monitoring** (SLOs, token burn, error budgets, dashboards, alerting, cost anomaly), **agent trajectories** (graph traces, step replay, decision provenance, debugging loops). Prices and ingest limits are **vendor-published** as of 2026-08-21. ⚠️ No unpublished production p50/p95/p99 ingest SLOs are invented. `$ per 1k traces` figures are either official SKUs or **[inferred]** from a named billing unit × a stated span/event shape — not a universal industry rate.

Invariant: **an agent trace is a PII store that happens to look like APM.** The control plane (collectors, sampling, RBAC, audit) is not the data plane (prompts, tool args, retrieved chunks). Collapsing those planes — putting full messages on span attributes, then deriving Prometheus labels from `user.id` — is how teams simultaneously leak PII, explode cardinality, and sample away the only traces they later need. Trajectories are a *projection* over traces/threads, not a storage format. Replay that re-calls the model is not the same as replay that reads a checkpoint.

---

## 1. System Topology & Mechanics

### 1.1 Two planes, three stores, one trace ID

| Plane | What it is | Clock | Typical store | Failure if mixed |
| --- | --- | --- | --- | --- |
| **Control** | Collectors, sampling policy, RBAC, audit of *who viewed which trace*, spend caps | Collector/ingest clock; 429 windows | Collector config, IdP, SIEM | App code that “samples interesting traces” by inspecting prompts |
| **Data (telemetry)** | Span trees, metrics, structured logs | User SLO clock (TTFT / e2e) | Tempo / Honeycomb / LangSmith / Phoenix / Datadog | Content on attributes + metrics-generator labels = cardinality + leak |
| **Data (content blobs)** | Prompts, completions, tool I/O, retrieved docs | Independent TTL / IAM | Object store, eval dataset, encrypted blob with span pointer | “We deleted the span” while S3 still has the prompt |

LangSmith’s published split is the cleanest *product* topology: **runs** (≈ OTel spans) nest into a **trace** (one operation); **threads** group traces across turns; a **trajectory** is a *flat, ordered message list* projected from the thread, with run nesting removed ([LangSmith observability concepts](https://docs.langchain.com/langsmith/observability-concepts)). Datadog Agent Observability is isomorphic with different names: LLM / workflow / agent / tool / task / embedding / retrieval span kinds, with agent traces rooted on an **agent** span ([Datadog terms](https://docs.datadoghq.com/llm_observability/terms/)). Phoenix is the OTel-native twin: OTLP in, **OpenInference** span kinds for UI. Honeycomb Agent Timeline binds conversations with `gen_ai.conversation.id` and swim-lanes by `gen_ai.agent.name` ([Honeycomb Agent Timeline](https://docs.honeycomb.io/investigate/observe/agent-timeline); [instrumentation guide](https://www.honeycomb.io/blog/instrumenting-ai-agents-agent-timeline-opentelemetry-guide)).

**Control vs data plane (enterprise hosting).** LangSmith Enterprise: Cloud (vendor holds both), **Hybrid** (SaaS control plane + self-hosted data plane), **Self-Hosted** (your VPC) ([LangSmith pricing](https://www.langchain.com/pricing-langsmith)). Phoenix self-hosts the whole stack. Braintrust-class hybrid (UI/auth in vendor, traces in customer VPC) is the same pattern applied to eval+logs. Interview move: **trace backends are as sensitive as production databases** because they *are* the prompts. If the data plane holds PII, every online evaluator, LLM-as-judge, and “Engine” job that reads traces is a **subprocessor**.

**Fan-out, don’t dual-instrument.** LangSmith documents the production pattern: app emits OTLP once → OpenTelemetry Collector → LangSmith OTLP endpoint **and** a second backend ([trace with OpenTelemetry](https://docs.langchain.com/langsmith/trace-with-opentelemetry); [LangChain OTel blog](https://www.langchain.com/blog/opentelemetry-langsmith)). `LANGSMITH_OTEL_ENABLED` / `tracing_mode=hybrid` exists for migration, not as a long-term dual-SDK architecture.

### 1.2 Tracing: W3C context, then GenAI attributes, then vendor UI

**W3C Trace Context (REC 2021-11-23)** is the wire format every other layer rides on ([W3C Trace Context](https://www.w3.org/TR/trace-context/)):

```
traceparent: 00-{32 hex trace-id}-{16 hex parent-id}-{2 hex flags}
example:     00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01
```

- Version `00`. Trace ID 16 bytes, parent/span ID 8 bytes. Flag `01` = sampled.
- `tracestate` is vendor-opaque key=value list; intermediaries MUST forward both headers.
- OTel tail sampling can write probability sampling fields (`rv`, `th`) into the `ot` section of `tracestate` when `processor.tailsamplingprocessor.usetracestate` is on ([tailsamplingprocessor README](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/tailsamplingprocessor/README.md)).

**MCP exception to HTTP headers.** MCP SEP-414 documents carrying `traceparent` / `tracestate` / `baggage` in JSON-RPC `_meta` so stdio/SSE/HTTP all propagate the same context ([SEP-414](https://modelcontextprotocol.io/seps/414-request-meta)). Without this, the agent’s `execute_tool` span and the MCP server’s `tools/call` span are **two traces**. The MCP Python SDK’s OTel middleware emits SERVER spans and asserts shared `trace_id` on client+server ([PR #2854](https://github.com/modelcontextprotocol/python-sdk/pull/2854)).

### 1.3 OpenTelemetry GenAI SIG — six layers, still Development

GenAI SIG formed April 2024 under Semantic Conventions SIG. Scope grew from LLM client spans to **six layers**: client spans, agent/workflow spans, MCP, content capture, metrics, evaluation events ([Greptime walkthrough of v1.41, May 2026](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions); [OTel blog 2026](https://opentelemetry.io/blog/2026/genai-observability/)).

**Authoritative home moved in 2026.** Core `semantic-conventions` v1.42.0 (2026-06-12) deprecated and moved all `gen_ai.*` content; v1.43.0 ships none. The dedicated repo is [`open-telemetry/semantic-conventions-genai`](https://github.com/open-telemetry/semantic-conventions-genai). As of July 2026 **no GenAI-specific span/event/metric/attribute is Stable** — all Development ([John Hodge, 2026-07-17](https://john-hodge.com/blog/opentelemetry-genai-semantic-conventions/)). Shared core attrs (`error.type`, `server.address`) are Stable; `gen_ai.*` is not. Opt-in: `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`.

| Semconv version | What landed |
| --- | --- |
| v1.37 (Aug 2025) | `gen_ai.system` → `gen_ai.provider.name`; per-message events → aggregated `gen_ai.input.messages` / `output.messages` / `system_instructions` |
| v1.38 | Evaluation event; tool definitions; `invoke_agent` kind guidance |
| v1.39 | MCP semantic conventions |
| v1.40 (Feb 2026) | Retrieval spans; cache token attributes; `gen_ai.agent.version` |
| v1.41 (Apr 2026) | `execute_tool {tool.name}` naming; reasoning tokens; `invoke_workflow`; streaming metrics; `invoke_agent` CLIENT vs INTERNAL |

**LLM / client spans** ([gen-ai-spans.md](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)):

- Kind: **CLIENT** (MAY be INTERNAL for in-process models). Name: `{gen_ai.operation.name} {gen_ai.request.model}`.
- `gen_ai.operation.name`: `chat` | `text_completion` | `generate_content` | `embeddings` | `execute_tool` | `create_agent` | `invoke_agent` | `invoke_workflow` | `retrieval` ([attribute registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)).
- Required-class attrs: `gen_ai.provider.name`, `gen_ai.operation.name`, `gen_ai.request.model`. Usage: `gen_ai.usage.input_tokens` (includes cached), `gen_ai.usage.output_tokens`, plus cache-read/cache-creation splits. `gen_ai.response.model` is what actually served (dated snapshot / fine-tune). `gen_ai.response.finish_reasons`: `stop` | `tool_calls` | `length` | `content_filter`.
- **Content is off by default.** OTel blog: only metadata (model, tokens, duration) unless opted in. Gate commonly `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`. Three recording modes: none; span attributes (size-limited, inherits trace ACL); **external blob + URL on the span** (recommended for production volume/PII) ([Greptime layer 4](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions)).

**Tool spans.** Kind INTERNAL. From v1.41 name MUST be `execute_tool {gen_ai.tool.name}`. `gen_ai.tool.call.id` recommended; `gen_ai.tool.call.arguments` and `.result` are **opt-in** when privacy policy permits ([spans.yaml v1.41](https://github.com/open-telemetry/semantic-conventions/blob/v1.41.0/model/gen-ai/spans.yaml)). Auto-instrumentors see the *model’s request* for a tool; the *function body* is your code — wrap it or you get a chat span with `finish_reason=tool_calls` and a missing child.

**Agent spans.** `invoke_agent`: CLIENT for remote agent APIs, INTERNAL for in-process frameworks (LangGraph). `invoke_workflow` for predetermined DAGs. `create_agent` for hosted agent create. Honeycomb Timeline counts LLM calls as `operation.name ∈ {chat, generate_content, text_completion}` and tools as `execute_tool`.

**MCP layer.** Client `tools/call` + server `tools/call`; attributes `mcp.method.name`, `mcp.session.id`, `rpc.system=mcp`. If outer GenAI already has `execute_tool`, MCP **enriches** rather than duplicating. Metrics: `mcp.client/server.operation.duration`, `mcp.client/server.session.duration`.

**Metrics (always-on, content-free).** Client histograms in active use: `gen_ai.client.token.usage`, `gen_ai.client.operation.duration` ([OpenObserve practical guide](https://openobserve.ai/blog/opentelemetry-genai-semantic-conventions/)). Datadog’s equivalent is first-class: `ml_obs.*` metrics are computed from **100% of traffic**, retained like ordinary Datadog metrics (15 months at full granularity), even when traces are sampled ([Datadog LLM metrics](https://docs.datadoghq.com/llm_observability/monitoring/metrics/)). That split — **100% metrics, sampled traces** — is the production default.

### 1.4 OpenInference — AI span kinds on OTel’s wire

OpenInference is a semantic convention **on top of OTel**, not a competing protocol ([spec README](https://github.com/Arize-ai/openinference/blob/main/spec/README.md); [Phoenix docs](https://arize.com/docs/phoenix/tracing/concepts-tracing/otel-openinference/semantic-conventions)). Transport is OTLP. Required attribute: `openinference.span.kind` in **ALL CAPS**.

| Kind | Meaning |
| --- | --- |
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

Attributes are **flattened** (`llm.input_messages.0.message.role`) because OTel attributes are flat K/V. Common: `input.value` / `output.value`, `session.id`, `user.id`. Phoenix listens OTLP/**gRPC 4317** and OTLP/**HTTP on UI port 6006** (`/v1/traces`) — **not** generic 4318 ([exporter docs](https://arize.com/docs/phoenix/tracing/concepts-tracing/otel-openinference/exporter)). Spans > **4 MB** hit gRPC message limits (full docs, base64 images). Production: `TraceConfig(hide_inputs=True, …)` or `OPENINFERENCE_HIDE_INPUTS=true` (and hide outputs/messages/images) ([Phoenix production Python](https://github.com/Arize-ai/phoenix/blob/HEAD/.agents/skills/phoenix-tracing/references/production-python.md)). Queue back-pressure: `PHOENIX_MAX_SPANS_QUEUE_SIZE` default **20,000** ([Phoenix #10021](https://github.com/Arize-ai/phoenix/issues/10021)).

**Mapping (do not treat as 1:1 identity).** OTel `gen_ai.operation.name=chat` ≈ OpenInference `LLM` ≈ Datadog `LLM` ≈ LangSmith run type `llm`. OTel `execute_tool` ≈ OpenInference `TOOL` ≈ Datadog `tool` (not a valid Datadog root). OTel `invoke_agent` ≈ OpenInference `AGENT` ≈ Datadog `agent`. OpenInference `CHAIN` ≈ Datadog `workflow` ≈ OTel `invoke_workflow`. Guardrail/evaluator kinds exist in OpenInference first; OTel puts evals on `gen_ai.evaluation.result` events.

### 1.5 Collector topology: agents, gateways, tail sampling, Kafka

Head sampling (SDK `TraceIdRatioBased`) is cheap and **wrong for agents**: the interesting bit (tool error, 40-step loop, content_filter) is known only at the **tail**.

Canonical two-tier OTel layout ([OTel tail sampling blog](https://opentelemetry.io/blog/2022/tail-sampling/); [ADOT](https://aws-otel.github.io/docs/getting-started/advanced-sampling/); contrib README):

1. **Edge / gateway collectors** — `memory_limiter` first, `k8sattributes`, batch; **loadbalancing exporter** with `routing_key: traceID` (DNS/k8s resolver to the sampling tier).
2. **Sampling tier** — `tailsamplingprocessor`. **All spans of a trace MUST hit the same instance.** Default `decision_wait=30s`, `num_traces=50000`. Policies: `status_code` (keep ERROR), `latency`, `ottl_condition` (e.g. `gen_ai.usage.input_tokens`), `probabilistic` for the rest, `composite` with per-policy rate allocation, `bytes_limiting` / `rate_limiting` token buckets for overload.

**Kafka as the buffer between tiers.** `kafkaexporter` `partition_traces_by_id: true` (default **false**) sets the record key to the hex trace ID so a partition maps to one sampling collector ([kafkaexporter README](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/exporter/kafkaexporter/README.md)). Mutually exclusive with `message_key_from_metadata_key`. Jaeger encodings already key by trace ID.

**Honeycomb Refinery** is the same idea as a product: trace-aware **tail** proxy; dynamic / EMA dynamic / rules / throughput samplers; sampled-before-ingest events **do not** count toward EPM ([Refinery docs](https://docs.honeycomb.io/manage-data-volume/sample/honeycomb-refinery/); [sampling methods](https://docs.honeycomb.io/manage-data-volume/sample/honeycomb-refinery/sampling-methods)). Use `root.` prefix on field lists or concatenated span values explode the sampler key ([tuning blog](https://www.honeycomb.io/blog/tuning-refinery-dynamic-sampling)).

**Datadog SDK sampling** decides on the **root** LLM-obs span and applies to all children including downstream APM via distributed tracing; billing is span-volume based so this is a cost control, not a GenAI policy engine ([SDK](https://docs.datadoghq.com/llm_observability/instrumentation/sdk.md)).

### 1.6 Logging: structured, correlated, not a second prompt dump

OTel log model fields that matter: `TraceId`, `SpanId`, `TraceFlags`, `Body`, `Attributes`, resource ([OTel logs](https://opentelemetry.io/docs/concepts/signals/logs)). SDKs inject IDs when a span is active. JSON-only enrichment for eBPF/OBI; plaintext is not correlated.

**Minimum structured event for an LLM call (control-safe):** `event`, `trace_id`, `span_id`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `input_tokens`, `output_tokens`, `cache_*_tokens`, `finish_reason`, `latency_ms`, `ttft_ms` if streaming, `cost_usd` computed **at emit time** from the published price table (never a nightly backfill as the source of truth), `tenant_id` / hashed `user_id`, `feature` / `prompt.version`. **No raw user text** on the log line in default prod.

Redaction is **before write**, twice: SDK anonymizer + collector `redaction`/`transform` processor. LangSmith: `LANGSMITH_HIDE_INPUTS/OUTPUTS`, `create_anonymizer` regex/function, optional Presidio; anonymizer skipped if hide-* is true ([mask inputs/outputs](https://docs.langchain.com/langsmith/mask-inputs-outputs)). LLM Gateway on the pricing page: PII and secrets redaction as a **control-plane** product, not an SDK afterthought.

### 1.7 Monitoring: SLOs that are not “the model is 99.9%”

User-facing SLIs for agents are **request-shaped**, not token-shaped:

| SLI | Definition | Notes |
| --- | --- | --- |
| **Availability** | Root span OK **and** `finish_reason` not in `{length, content_filter}` unless that is a defined product outcome | 429 / overload is an error from the user’s seat |
| **Latency** | TTFT p95/p99 (stream) + e2e p95/p99 (agent may be 10–120s) | Do not SLO the inner `chat` p99 as if it were the UX |
| **Correctness (online)** | Sampled eval / policy-violation rate — **off the request path** | Online judges are a second budget |
| **Cost** | `$ / successful task` or tokens / task, not `$ / span` | Token burn is a **budget**, not an SLO unless finance says so |

Google SRE Workbook multi-window burn rates still apply ([Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/)): for a 30-day SLO, **page** at burn **14.4×** on **1h AND 5m** (2% of budget in 1h); page at **6×** on **6h AND 30m** (5% in 6h); ticket at **1×** on 3d. Datadog implements the same `burn_rate("slo_id").over("30d").long_window("1h").short_window("5m")` API ([Datadog burn rate](https://docs.datadoghq.com/service_management/service_level_objectives/burn_rate/)). Short window = 1/12 of long.

Tempo **metrics-generator** turns spans into RED metrics + exemplars; Grafana SLO app consumes `traces_spanmetrics_latency` ([Tempo app insights](https://grafana.com/docs/tempo/latest/solutions-with-traces/traces-app-insights/)). Grafana Cloud default **30s slack**: spans whose end time is older than now−30s are **dropped from metrics** (batch + `decision_wait=25s` will do this) ([Grafana Cloud metrics-generator](https://grafana.com/docs/grafana-cloud/send-data/traces/configure/metrics-generator/)). Query frontend example SLO knobs: `duration_slo: 5s` for search and get-by-id — that is **query**, not ingest.

**Cost dashboards.** Datadog Cost Overview: estimated `$` from **public** provider prices × annotated tokens; states `PARTIAL COST` / `UNAVAILABLE` when cache splits missing (then **standard input rate is applied to all `input_tokens`** — overestimate if you had cache hits) ([cost docs](https://docs.datadoghq.com/llm_observability/monitoring/cost/)). Alert on `ml_obs.span.llm.total.cost` by `ml_app` / `model_name`. Soft quota 80% / hard cap is a **gateway** concern (LangSmith LLM Gateway lists cost controls + rate limits), not a dashboard.

### 1.8 Agent trajectories: tree, thread, graph, replay

Four objects people conflate:

| Object | What it stores | What it is for |
| --- | --- | --- |
| **Trace tree** | Nested spans/runs for one invocation | “Which child timed out?” |
| **Thread** | Sequence of traces sharing `thread_id` | Multi-turn session |
| **Trajectory** | Deduped ordered messages (human/AI/tool) | Scan the conversation; LangSmith Messages view (**beta**) |
| **Graph checkpoint** | Full **state** at each super-step | Time-travel, fork, resume; **not** the same as a span |

LangSmith trajectory requires `thread_id` plus `ls_agent_type: "root"` on the turn’s top run; `subagent` / `middleware` / `compaction` change what Messages view shows ([messages view format](https://docs.langchain.com/langsmith/messages-view-trace-format)). Hard limit: **25,000 runs per trace**; further runs rejected ([usage and billing](https://docs.langchain.com/langsmith/usage-and-billing)).

**LangGraph checkpointers** snapshot state at super-step boundaries; task writes inside a super-step are for **fault tolerance**, not time-travel ([checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)). **Replay** from `checkpoint_id` **re-executes** nodes after that checkpoint — LLM calls, tools, interrupts fire again and **may differ**. Replay of the final checkpoint is a no-op. **Fork** = `update_state` then invoke; original history is not deleted ([time travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel)). That is **decision provenance plus a debugger**, not an audit tape of what the model *would* say if sampled again.

**Provenance that survives sampling:** policy decision (allow/deny/HITL), tool name + call id, model request vs response id, checkpoint id, prompt version. Put those on **low-cardinality span attrs and audit logs**. Put the essay on a blob.

Honeycomb Timeline: conversation metrics (duration, trace count, LLM calls, tool calls, failures, total tokens) + GenAI panel (provider, request/response model, tool type `function|extension|datastore`, call id). Failures depend on `error.type` and span status — if tools swallow exceptions, the Timeline’s “Show Failures Only” is empty.

---

## 2. Token Economics & NFR Metrics

### 2.1 What you are actually billed for

Billing units are **not interchangeable**. Interview failure: quoting `$/1k traces` across Datadog, LangSmith, and Honeycomb as if they were the same object.

| Vendor | Billable unit | Published list (2026-08-21) | Retention on that SKU |
| --- | --- | --- | --- |
| **LangSmith** | **Trace** (root + all child runs = 1) + **extended-retention upgrade** | Official docs: base **0.05¢/trace**, extended **10× = 0.50¢/trace** (upgrade **0.45¢**). Seats: Developer **$0** (1 seat, 5k base traces/mo), Plus **$39/seat** (10k included). Overlay: **1 LCU = $1.50**, **1 LSU = $1.00** for Engine/Fleet/deploy/sandbox ([usage-and-billing](https://docs.langchain.com/langsmith/usage-and-billing); [pricing](https://www.langchain.com/pricing-langsmith)) | Base **14 days**; extended **400 days** (Enterprise customizable). Monitoring graphs keep **metadata >30 days** after base deletion. Datasets: **indefinite**. |
| **Datadog Agent Observability** | **LLM spans only** (one provider call). Tool/workflow/agent/embedding/retrieval **free** | Free **40k LLM spans**/mo. Pro **$160**/mo **annual** for first **100k**; **$200** M2M; **$240** on-demand. Overage **$3.50 / 10k** annual, **$4.20** M2M, **$5** on-demand. Retention add-on **$1.50 / $3 / $4 per 10k LLM spans** for 30 / 60 / 90-day traces (and 6 / 9 / 12-month experiments) ([pricing list](https://www.datadoghq.com/pricing/list/); [product](https://www.datadoghq.com/products/ai/agent-observability/)) | Default **15 days** traces |
| **Honeycomb** | **Event** = **one span** (OTel SpanEvent/Link also count) | Free **20M events**/mo + **100M** metric datapoints. Pro **starting at $150**/mo, up to **750M** events (pricing page). Docs: from **2026-07-01** new Pro **$3.00 / million events** vs legacy **$1.30 / million** ([pricing](https://www.honeycomb.io/pricing); [Pro plan changes](https://docs.honeycomb.io/get-started/honeycomb/2026-pro-plan-changes/)) | Plan-default retention (not a 14d/400d SKU) |
| **Phoenix / Tempo self-host** | Your disks + query compute | No SaaS trace SKU. Constraint is **payload size** and **queue** | You choose |

**`$ per 1k traces` (named assumptions, not a vendor “trace SKU” unless stated):**

- LangSmith base: **$0.50 / 1k traces** (0.05¢ × 1000). Extended: **$5.00 / 1k traces**. Official.
- Datadog: **not priced per trace**. **[inferred]** 1 agent request × 8 LLM calls = 8 billable spans. At annual overage **$3.50/10k LLM spans** = **$0.35 / 1k LLM spans** = **$2.80 / 1k such requests**. The $160 package is **$1.60 / 1k LLM spans** if you fill the 100k (and $0 if you stay in free 40k).
- Honeycomb: **[inferred]** new Pro **$3.00 / 1M events** = **$0.003 / 1k events**. A 25-span agent turn = **$0.075 / 1k traces**. Refinery drops do not bill. Legacy $1.30/M = **$0.0013 / 1k events**.
- ⚠️ Third-party posts still quote LangSmith **$2.50 / 1k base traces**. That **does not match** `docs.langchain.com/langsmith/usage-and-billing` as of 2026-08-21 (0.05¢). Do not mix the two in a cost model.

**Auto-upgrade tax (LangSmith).** Online evaluators and automation rules **default to extending retention**. One matching run upgrades the **entire trace**; a **thread-level** rule upgrades **every trace in the thread**. UI feedback/notes/annotation-queue adds do **not** upgrade. Experiments start at extended. This is how a 14-day debug project becomes a 400-day invoice.

### 2.2 Storage shape: why LLM traces are 10–100× APM traces

APM span: tens to hundreds of bytes of attributes. LLM span with content: **full prompt + completion** (often 2–32k tokens ≈ 8–128 KB UTF-8 **per call**, plus tool JSON). LangSmith hourly **data** caps exist because of this, not because of span *count*:

| Plan | Events / hour | Payload / hour |
| --- | --- | --- |
| Developer, no card | 50,000 | **500 MB** |
| Developer, card on file | 250,000 | **2.5 GB** |
| Startup/Plus | 500,000 | **5.0 GB** |
| Enterprise | Custom | Custom |

Plus ALB: **5,000** `POST|PATCH /runs*` per **minute** per key (SDK batches ≤ **100** runs/call). Developer no-card: **5,000 traces / calendar month** then 429.

**[inferred] payload math:** 5.0 GB/h ÷ 500k events = **10 KB/event** average headroom on Plus. A single 50 KB prompt on create **and** 80 KB on update = **130 KB** against the window for one run. Content-on-by-default will 429 you before span-count does.

Phoenix: 4 MB gRPC; Tempo example override `max_bytes_per_trace: 5_000_000` (5 MB) and `ingestion_rate_limit_bytes: 15_000_000` (15 MB/s/tenant) in published config recipes — **example configs, not Grafana Cloud’s unpublished tenant contract**.

### 2.3 Ingest latency: knobs, not invented p50/p95/p99

⚠️ **No vendor publishes a universal “trace ingest p99 = X ms” SLO that you can copy into an architecture review.** Bound the pipeline instead:

| Stage | Published delay source | Effect |
| --- | --- | --- |
| SDK batch | LangSmith `auto_batch_tracing`; OTel `BatchSpanProcessor` timeout | Seconds of holdback |
| Tail sample | OTel default `decision_wait=30s`; Grafana Cloud metrics slack **30s** | Completeness vs freshness |
| Kafka | Partition lag | Back-pressure absorber; p99 is *your* cluster |
| Query | Tempo `query_frontend` `duration_slo: 5s` (search / by-id) | **Read** SLO |
| Alert | Honeycomb trigger **event latency** = event timestamp vs arrival; long agent traces inflate the chart even when spans arrive promptly ([Honeycomb support](https://support.honeycomb.io/articles/5136206977-why-didn-t-my-trigger-fire-triggers-and-event-latency)) | Mis-tuned duration misses delayed spans |

Datadog `ml_obs.*` metrics bypass trace retention: you can alert on token burn after the trace is gone. LangSmith monitoring tab similarly survives base-tier deletion via retained metadata.

### 2.4 Token burn vs observability burn

Two meters:

1. **Provider tokens** — `gen_ai.usage.*` including cache read/write. Cost anomaly = sudden mix shift to a larger model, cache miss storm, or agent loop (`finish_reason=tool_calls` oscillating).
2. **Observability tokens** — span bytes, eval LLM-as-judge tokens, LangSmith Engine **~5–30 LCU/run** (estimate on pricing FAQ; 1 LCU = $1.50 → **[inferred] ~$7.50–$45 per Engine run**, not a SLO).

NFR targets to *set* (not claimed as industry): ingest 429 rate = 0; tail-sample `trace_dropped_too_early` ≈ 0; exporter queue `otelcol_exporter_queue_size / capacity` < 0.5; metrics-generator discarded-late-span rate ≈ 0; PII findings in traces = 0 after redaction QA.

---

## 3. Distributed Resilience & State

### 3.1 Collector back-pressure is a protocol, not a hope

`memory_limiter` MUST be first in every pipeline ([memorylimiter README](https://github.com/open-telemetry/opentelemetry-collector/blob/main/processor/memorylimiterprocessor/README.md)). Soft limit = `limit_mib - spike_limit_mib`: refuse data with a **non-permanent** error so receivers retry and apply upstream back-pressure. Hard limit: refuse + force GC. Pair with `GOMEMLIMIT` ≈ **80%** of container memory. Forced GC that cannot free exporter-queue-held data **backs off exponentially**.

Exporters: `sending_queue` + `retry_on_failure`; optional `file_storage` so the queue survives restart. When the queue is full, **drop and count** — that is load shed. Watch `otelcol_processor_refused_spans` and queue size vs capacity.

OTLP receivers that ignore retryable errors turn limiter protection into **silent loss**. Custom receivers are the usual bug.

### 3.2 Sampling when overloaded

Policy stack under load (order matters):

1. **Keep** ERROR / `finish_reason=content_filter` / policy-deny / HITL traces (status + OTTL).
2. **Keep** high-latency roots (user SLO breach).
3. **Bytes_limiting / rate_limiting** token buckets on the tail sampler.
4. **Probabilistic** remainder; write `tracestate` so adjusted counts remain unbiased.
5. SDK head sample **only** as last-ditch if collectors are saturated — accept bias.

Honeycomb **throughput** samplers target spans/sec. OTel `composite` allocates a budget per policy (e.g. 50% of `max_total_spans_per_second` to errors). `drop_pending_traces_on_shutdown`: drop vs decide on partial data — pick explicitly; default partial decisions **look like** missing children.

**Late spans after a drop decision.** Enable `decision_cache.non_sampled_cache_size` **≫ `num_traces`** so a late MCP server span is dropped consistently instead of creating a one-span orphan trace. `trace_dropped_too_early` means `num_traces` is too small for `decision_wait` × arrival rate.

### 3.3 Kafka / disk as the state that APM pretended not to need

Agent traces are **wide** (many spans) and **slow** (tools, humans, MCP). In-memory grouping (`num_traces=50k`, 30s wait) OOMs first. Pattern: edge collector → Kafka (`partition_traces_by_id`) → sampling collectors → backends. Kafka consumer should propagate downstream processor errors so the consumer **pauses** (true back-pressure) rather than OOM the collector.

Phoenix in-process queue (20k spans) is the same class of bug without Kafka: `RESOURCE_EXHAUSTED` under embedding-vector attributes.

### 3.4 Cross-process state: don’t break the trace at MCP / queues

- HTTP tools: inject W3C headers.
- MCP: `_meta.traceparent` (SEP-414), not only HTTP.
- Queues: propagate context in message metadata; consumers `extract` before handling.
- Streaming LLM: span ends when the **stream completes**, not at first token; TTFT is a span event or histogram, not a second trace.

Missing child = head sample on the MCP server, different collector without traceID sticky routing, or `decision_wait` shorter than the tool.

### 3.5 LangGraph / checkpoint resilience vs telemetry resilience

Checkpoints let you **resume after crash** without redoing completed super-steps. Telemetry does **not** resume: crashed workers often lose the in-flight batch. If the product requirement is “we can prove what the agent did,” the audit store cannot be the SDK’s memory exporter. Use WAL (Tempo ingester WAL is a documented production requirement in operator guides) + checkpoint DB + SIEM, with **independent** failure domains.

---

## 4. Enterprise Security & Governance

### 4.1 Zero-Trust MCP and traces as a side channel

CoSAI: log all agent/tool/prompt/model interactions; OTel for linkability; **immutable** records of actions and authorizations; do not pass user OAuth tokens through (RFC 8693 token exchange); treat MCP returned content as untrusted ([CoSAI MCP security](https://www.coalitionforsecureai.org/securing-the-ai-agent-revolution-a-practical-guide-to-mcp-security/)). Groundcover notes MCP deprecating protocol-level custom logging in favor of OTel so the tool call continues into the **server’s** DB/HTTP spans ([MCP observability](https://www.groundcover.com/blog/mcp-spec-update)).

Zero-Trust for observability means: the **trace backend is not implicitly trusted with plaintext PII** just because SRE has access. Separate:

- **Metadata traces** (always): model, tokens, latency, tool name, policy decision, error class.
- **Content** (break-glass): encrypted blob, short TTL, just-in-time access, ticketed.
- **Audit of observability**: who exported / viewed a trace (this is *not* the agent’s tool audit).

### 4.2 PII in spans — the default is leak

OTel: content opt-in. OpenInference/Phoenix: hide flags. LangSmith: hide + anonymizer + Gateway redaction. Datadog: **Sensitive Data Scanner** integrated with Agent Observability ([terms](https://docs.datadoghq.com/llm_observability/terms/)).

Still leak:

- Tool arguments (`gen_ai.tool.call.arguments`, `tool.parameters`) with emails, account numbers, API keys in JSON the hide-prompt flag **does not** cover unless you hide outputs/inputs globally or run a JSON-aware anonymizer.
- Retrieval documents on `RETRIEVER` spans.
- `user.id` / `session.id` as **metrics labels** (legal + cardinality).
- Eval datasets promoted from prod traces (indefinite retention on LangSmith datasets).
- Screenshots / images (`OPENINFERENCE_HIDE_INPUT_IMAGES`, `BASE64_IMAGE_MAX_LENGTH`).

Defense in depth: allowlist attributes in the collector (`attributes` processor), hash identifiers (HMAC with a key that is **not** in the trace), cap string length, drop embedding vectors.

### 4.3 RBAC on traces

LangSmith: org vs workspace roles; **custom roles**; **ABAC** (GA cited Mar 2026 in hardening write-ups). Permissions include `projects:increase-trace-tier` / `decrease-trace-tier` **independent** of `projects:update` (metadata-only). Restricting a role hides or disables retention UI ([RBAC](https://docs.langchain.com/langsmith/rbac)). Plus: org roles User/Admin only; Enterprise: custom SSO, ABAC, RBAC.

Interview design: **Viewer** sees metadata; **Debugger** sees redacted content; **Privacy** / legal sees blobs; **no** engineer role that can raise retention on a HIPAA project without a ticket.

Honeycomb: SSO on Pro; Query Data API / PrivateLink / Private Cloud on Enterprise. Grafana: folder permissions + Tempo multi-tenant overrides.

### 4.4 Immutable audit — two tapes

| Tape | Contents | Format / sink |
| --- | --- | --- |
| **Agent action audit** | Who (user principal + agent id), what tool, args hash, policy decision, trace_id, checkpoint_id | Append-only log; WORM object lock; not sampling-eligible |
| **Platform audit** | Who changed sampling, retention, API keys, SSO, viewed/exported traces | LangSmith: Enterprise; self-hosted Helm **≥ 0.12.33**; **OCSF 1.7.0** API Activity (class 6003); `GET /api/v1/audit-logs` with `start_time`/`end_time`; org `organization:manage` only; **no UI**, API-only; ~70+ **write** operations ([audit logs](https://docs.langchain.com/langsmith/audit-logs); [API](https://docs.langchain.com/langsmith/smith-api/audit-logs/get-audit-logs)) |

LangSmith FAQ: audit logs **do not** currently focus on reads. If “who looked at this customer’s prompt” is a requirement, you need extra wrapping (IdP session + trace-ACL proxy) — ⚠️ gap.

Self-hosted enablement: `DEFAULT_ORG_FEATURE_CAN_USE_AUDIT_LOGS` / `AUDIT_LOGS_ENABLED`; existing orgs need a DB `can_use_audit_logs` flag. Ship OCSF to Splunk/Datadog SIEM; **do not** store platform audit in the same Postgres you would wipe for GDPR of traces.

GDPR/CCPA: LangSmith deletes user data on traces within a day after retention; **some metadata retained indefinitely for analytics/billing**. Plan that “delete the user” ≠ “delete billing aggregates.”

LangSmith ToS-facing FAQ: they **do not train** on your traces ([pricing FAQ](https://docs.langchain.com/langsmith/pricing-faq)). Still a **subprocessor** for DPA purposes.

---

## 5. Production Failure Modes

### 5.1 Missing spans (broken trees)

| Cause | Symptom | Fix |
| --- | --- | --- |
| Head sample on a downstream MCP/HTTP service | Tool span exists, server DB span missing | Tail sample; propagate context; sample MCP at 100% or inherit parent decision |
| Load balancer without traceID affinity | Random half-traces after tail sample | Two-tier + loadbalancing exporter or Kafka key |
| `decision_wait` < tool timeout / HITL | Partial trace sampled as “OK” | Wait ≥ p99 e2e + slack; or `decision_wait_after_root_received` |
| LangSmith 25k run cap | Later steps 4xx | Split long agents into thread of traces, not one mega-trace |
| Hide/redact processor dropping the span | “Tool never ran” | Redact fields, don’t drop TOOL spans |
| gRPC 4 MB / Tempo max_bytes_per_trace | Entire export fail | Truncate content; blob off-band |
| 429 hourly payload | Intermittent missing updates (create without completion) | Hide content; raise plan; sample |

### 5.2 Cardinality explosion

Tempo docs: `span_name` is the usual bomb (`GET /users/123`, `chat {user_prompt_hash}`) ([cardinality](https://grafana.com/docs/tempo/latest/metrics-from-traces/metrics-generator/cardinality/)). GenAI-specific bombs: `gen_ai.request.model` dated snapshots are OK; **`user.id`, `session.id`, `gen_ai.conversation.id`, tool call ids, prompt text** as Prometheus labels are not. Keep high-cardinality on **traces** (Honeycomb’s point) and low-cardinality on **metrics**. Tempo: `max_active_series`, per-label limits, span-name sanitization (DRAIN). Honeycomb Refinery: keep FieldList cardinality **low** (guide: prefer <100 keys); `root.` prefix.

Datadog LLM metrics tags are a **fixed** set (`env`, `ml_app`, `model_name`, `model_provider`, …) — safer than DIY spanmetrics. You still explode if `ml_app` is per-customer.

### 5.3 PII leak via traces

Classic: enable content capture in staging, copy the env to prod. Or log `Authorization` inside `llm.invocation_parameters` (Phoenix issues: `ReadableSpan` cannot `set_attribute` on `on_end` — redact too late). Or ship traces to a vendor before DPA. Or promote a leaking trace into a **dataset** (indefinite). Or screenshots in computer-use agents.

Treat a trace export as a **data subject access / breach** surface. Token-level redaction recall is never 100%; architect as if regex will miss.

### 5.4 Sampling bias

Head sampling 1% of “successful” chats **systematically deletes** rare tool-failures and jailbreak attempts. Dynamic sampling that keys on `http.status_code` but **not** `gen_ai.response.finish_reasons` will under-keep `content_filter`. Error-only keep will **overfit** dashboards to failures and hide cost blowups on happy 200s with 2M-token loops. Throughput samplers under a viral launch drop the new intent class first (unseen keys look “unique” and can invert — or, with a bad FieldList, everything looks unique and you keep 100% until the bill arrives).

Unbiased rates require **sample_rate recorded on the trace** (`tracestate` probability, Honeycomb `SampleRate`, OTel adjusted count). Dashboards that `count()` without `1/sample_rate` lie.

### 5.5 Cost / SLO coupling failures

- Online evals on 100% traffic: second LLM bill + retention upgrades.
- Metrics-generator 30s slack + 30s tail wait: **RED metrics go to zero** during the incident you care about.
- Datadog cost `PARTIAL` treating all input as uncached: finance pages you; you “optimize” a non-bug.
- Alerting on span ingest lag using agent **e2e duration** as event latency (Honeycomb): pages that never stop.

### 5.6 Debugging-loop failures

Replay that **re-calls** the model cannot reproduce a prod bug (non-determinism). Teams “fix” a flake that was sampling. LangGraph docs are explicit: replay re-triggers LLM/API/interrupts. For forensics use **recorded span I/O + checkpoint**, not a new sample. Messages-view trajectory that **filters middleware** hides the guardrail that actually failed.

---

## 6. Enterprise System Design Scenarios

### 6.1 Scenario A — Startup, one agent, SaaS OK

**Need:** Debug loops, cheap, ship this quarter.

**Design:** Phoenix or LangSmith Developer/Plus. OpenInference or LangSmith SDK. Content **on** in non-prod; hide in prod + anonymizer. No tail-sampling cluster yet; SDK sample 100% until 429.

**Trade-off:** Vendor holds prompts. Plus at $39/seat + $0.50/1k base traces is cheaper than a collector fleet until ~tens of k traces/day. Datadog free 40k LLM spans if you already have Datadog.

### 6.2 Scenario B — Bank: hybrid control, VPC data, WORM audit

**Need:** No prompts in SaaS; prove tool invocations 7 years; Zero-Trust MCP.

**Design:** OTel SDKs → gateway collectors in-cluster → Kafka (trace-id partition) → tail sample (keep errors, HITL, high $, 1% happy) → Tempo/S3 in VPC. Content: encrypted bucket, span has URI; IAM break-glass. MCP `_meta` context + server-side OTel. Action audit (not sampled) to object-lock bucket; platform audit to SIEM (OCSF). LangSmith **Hybrid** only if the data plane is **your** traces DB; else skip vendor data plane. RBAC: metadata vs content vs blob. Gateway: PII redact **before** any span attribute.

**Trade-off:** You own p99 ingest. You will not get LangSmith Messages-view polish unless you also export a redacted replica. Cost: S3 cheap; **query** and **engineer time** dominate. `$/1k traces` ≈ storage + Tempo compute **[inferred, env-specific]** — do not quote SaaS SKUs as TCO.

### 6.3 Scenario C — Multi-agent + MCP mesh, already on Honeycomb/Grafana

**Need:** Conversation-level UX + full-stack (DB/HTTP) in one product.

**Design:** Instrument **only** `gen_ai.*` + W3C. Honeycomb Agent Timeline + Refinery EMA dynamic on `root.http.status_code`, `gen_ai.provider.name`, `error` — **not** conversation id. Grafana: spanmetrics for SLOs with **sanitized** span names (`chat`, `execute_tool {allowlisted_tool}`); exemplars back to Tempo. Dual-export via collector.

**Trade-off:** Honeycomb bills **every span**. Wide traces (20–200 spans) make Refinery mandatory. Grafana dashboards will not look like LangSmith Messages until you build them. Pro SLO cap: **2 SLOs** — pick TTFT and availability, put cost on a trigger.

### 6.4 Scenario D — High QPS gateway (millions of LLM spans)

**Need:** Token economics + fallbacks; traces for 0.1% + all errors.

**Design:** Datadog or Grafana metrics **100%**; traces tail-sampled. Datadog billing on LLM spans favors **deep** agent trees (free tool spans). If the gateway is **one span per proxy call**, Datadog and LangSmith converge. Publish `error.type` enum: `RATE_LIMITED|OVERLOADED|TIMEOUT|CONTENT_FILTERED|…` low cardinality; raw provider code on the span only.

**Trade-off:** Datadog 15-day default; 90-day add-on is **$4 / 10k LLM spans/mo** — at 3M LLM spans, **[inferred]** 300 × $4 = **$1,200/mo** extra (matches independent bill-math writeups; still not a quote). LangSmith extended 400d is a different retention philosophy (compliance vs debug).

### 6.5 Scenario E — Eval-heavy platform team

**Need:** Online judges, datasets from prod, Engine-like clustering.

**Design:** Sample traces into an **eval project** at extended retention **explicitly**, not via default rule. Judges **async**. Never let eval spans block TTFT. Phoenix: spans → DataFrame → `run_evals` → annotations. LangSmith: opt **out** of retention upgrade on noisy evaluators.

**Trade-off:** Datasets are forever. PII in a golden set is a GDPR time bomb. Engine LCU is a **separate** meter from traces.

### 6.6 Trade-off matrix (interview board)

| Axis | SaaS LangSmith | Datadog Agent Obs | Honeycomb + OTel | Phoenix/Tempo self-host |
| --- | --- | --- | --- | --- |
| **Billing unit** | Trace (+ 10× extend) | LLM span | Event/span | Infra |
| **Agent UX** | Threads, Messages beta, LangGraph | Agent/workflow trees + APM | Agent Timeline + BubbleUp | OpenInference UI / DIY Grafana |
| **Content default** | On unless hidden | On; SDS redaction | You choose attrs | Hide flags |
| **Tail sample** | Vendor | SDK root rate | Refinery | You build |
| **Audit RBAC** | Ent. OCSF writes; Hybrid/SH | Datadog roles + SDS | Ent. features | You build |
| **Best for** | LangChain/Graph shops | Existing DD + Fortune APM | High-card debug | Data residency / cost at scale |
| **Fatal if** | Auto-extend + full content | 15d + cost add-on surprise | Span-count bill | No one owns collectors |

**Decision rule:** Instrument to **OTel GenAI + W3C** once. Treat OpenInference and Datadog span kinds as **exporters/UI**. Put **policy and $** on metrics (100%). Put **forensics** on traces (sampled, redacted). Put **legal proof** on an unsampled, immutable action log keyed by `trace_id`. If a design uses one system for all three, it will fail at least one of cost, privacy, or completeness.

---

## Sources

1. https://github.com/open-telemetry/semantic-conventions-genai
2. https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md
3. https://github.com/open-telemetry/semantic-conventions/blob/v1.41.0/model/gen-ai/spans.yaml
4. https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/
5. https://opentelemetry.io/blog/2026/genai-observability/
6. https://opentelemetry.io/blog/2022/tail-sampling/
7. https://opentelemetry.io/docs/concepts/signals/logs
8. https://john-hodge.com/blog/opentelemetry-genai-semantic-conventions/
9. https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions
10. https://openobserve.ai/blog/opentelemetry-genai-semantic-conventions/
11. https://www.w3.org/TR/trace-context/
12. https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/tailsamplingprocessor/README.md
13. https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/exporter/kafkaexporter/README.md
14. https://github.com/open-telemetry/opentelemetry-collector/blob/main/processor/memorylimiterprocessor/README.md
15. https://aws-otel.github.io/docs/getting-started/advanced-sampling/
16. https://github.com/Arize-ai/openinference/blob/main/spec/README.md
17. https://arize-ai.github.io/openinference/
18. https://arize.com/docs/phoenix/tracing/concepts-tracing/otel-openinference/semantic-conventions
19. https://arize.com/docs/phoenix/tracing/concepts-tracing/otel-openinference/span-kinds
20. https://arize.com/docs/phoenix/tracing/concepts-tracing/otel-openinference/exporter
21. https://arize.com/blog/llm-tracing-and-observability-with-arize-phoenix/
22. https://github.com/Arize-ai/phoenix/issues/10021
23. https://github.com/Arize-ai/phoenix/blob/HEAD/.agents/skills/phoenix-tracing/references/production-python.md
24. https://docs.langchain.com/langsmith/observability-concepts
25. https://docs.langchain.com/langsmith/view-traces
26. https://docs.langchain.com/langsmith/messages-view-trace-format
27. https://docs.langchain.com/langsmith/observability-quickstart
28. https://docs.langchain.com/langsmith/trace-with-langgraph
29. https://docs.langchain.com/langsmith/trace-with-opentelemetry
30. https://docs.langchain.com/langsmith/usage-and-billing
31. https://docs.langchain.com/langsmith/mask-inputs-outputs
32. https://docs.langchain.com/langsmith/rbac
33. https://docs.langchain.com/langsmith/audit-logs
34. https://docs.langchain.com/langsmith/smith-api/audit-logs/get-audit-logs
35. https://docs.langchain.com/langsmith/pricing-faq
36. https://www.langchain.com/pricing-langsmith
37. https://www.langchain.com/blog/opentelemetry-langsmith
38. https://docs.langchain.com/oss/python/langgraph/use-time-travel
39. https://docs.langchain.com/oss/python/langgraph/checkpointers
40. https://docs.datadoghq.com/llm_observability/terms/
41. https://docs.datadoghq.com/llm_observability/monitoring/cost/
42. https://docs.datadoghq.com/llm_observability/monitoring/metrics/
43. https://docs.datadoghq.com/llm_observability/instrumentation/sdk.md
44. https://docs.datadoghq.com/service_management/service_level_objectives/burn_rate/
45. https://www.datadoghq.com/pricing/list/
46. https://www.datadoghq.com/products/ai/agent-observability/
47. https://docs.honeycomb.io/investigate/observe/agent-timeline
48. https://www.honeycomb.io/blog/instrumenting-ai-agents-agent-timeline-opentelemetry-guide
49. https://www.honeycomb.io/pricing
50. https://docs.honeycomb.io/get-started/honeycomb/2026-pro-plan-changes/
51. https://docs.honeycomb.io/manage-data-volume/sample/honeycomb-refinery/
52. https://docs.honeycomb.io/manage-data-volume/sample/honeycomb-refinery/sampling-methods
53. https://www.honeycomb.io/blog/tuning-refinery-dynamic-sampling
54. https://github.com/honeycombio/refinery/
55. https://support.honeycomb.io/articles/5136206977-why-didn-t-my-trigger-fire-triggers-and-event-latency
56. https://grafana.com/docs/tempo/latest/metrics-from-traces/metrics-generator/cardinality/
57. https://grafana.com/docs/tempo/latest/solutions-with-traces/traces-app-insights/
58. https://grafana.com/docs/grafana-cloud/send-data/traces/configure/metrics-generator/
59. https://sre.google/workbook/alerting-on-slos/
60. https://modelcontextprotocol.io/seps/414-request-meta
61. https://github.com/modelcontextprotocol/python-sdk/pull/2854
62. https://www.coalitionforsecureai.org/securing-the-ai-agent-revolution-a-practical-guide-to-mcp-security/
63. https://www.groundcover.com/blog/mcp-spec-update
