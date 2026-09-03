# Module 05: Observability (LLM / Agent Telemetry)

**Study + interview prep.** Grounded in research dated 2026-09-02 (105 sources). Prices, ingest caps, retention, and semantic-convention names are vendor docs / OTel specs / named blogs as of that date. `$ per 1k traces/runs` figures that multiply a published SKU by a stated span shape are **[inferred]**, not a vendor SKU. Do not mix meters (LangSmith tree ≠ Datadog LLM-span ≠ Honeycomb event ≠ Grafana GB). Public pages do **not** publish production p50/p95/p99 of *your* telemetry pipeline or of a composed agent e2e; missing percentiles are marked and policy targets are architecture-derived **[inferred]**. Online scoring, LLM-as-judge cost, and dataset promotion belong in evals except where they **upgrade retention or sit on the SLO path**. Cache hit rates and TTFT physics belong in caching except as **telemetry fields**.

---

## What Is This?

An **agent trace is a PII store that happens to look like APM.** Nested spans of prompts, tool arguments, retrieved chunks, and screenshots sit in a system that was designed to hold `http.status_code` and `db.statement`. Treating that store as “just Datadog” is how teams leak PII, explode Prometheus cardinality, and sample away the only trees they later need.

Three **surfaces** interviewers collapse into “we have LangSmith”:

| Surface | What you actually need | Not the same as |
| --- | --- | --- |
| **Trajectory** | Steps, branches, retries, tool calls, resume points, LangGraph super-steps | A chat transcript |
| **Resource** | Tokens, latency, cost, cache splits — content-free histograms | A pretty trace UI |
| **Evidence / provenance** | Tool outputs, retrieval hits, citations, policy decisions that *justified* the run | The final answer |

Three **layers that must not share a sampling policy:**

| Layer | Sampling | Content | Purpose |
| --- | --- | --- | --- |
| Always-on **metrics** | **100%** | Content-free | SLOs, token burn, `$ / successful task` |
| Sampled / redacted **traces** | Tail (agent default); head only as last-ditch | Opt-in; hide in prod | Debug, trajectory UI, sampled online eval |
| Unsampled immutable **action audit** | **Never** sampled | Args **hash** (or redacted copy), not full prompt | Legal proof that a tool ran |

**Replay ≠ audit.** LangGraph replay from `checkpoint_id` **re-executes** nodes — LLM calls, tools, and interrupts fire again and may return different results. A checkpoint is a debugger. The audit tape is recorded span I/O + an unsampled action log keyed by `trace_id`. Trajectories are a *projection* over traces/threads, not a storage format.

## Why It Matters

Every production agent is an observability product whether you bought one or not. Interviews test whether you split **control plane vs telemetry data plane vs content blobs**, emit **OTLP once** (collector fan-out, not dual vendor SDKs), put **policy and $** on 100% metrics, put **forensics** on tail-sampled redacted traces, and put **legal proof** on a WORM action log. A Principal answer names W3C `traceparent`, OTel GenAI vs OpenInference as *mappings not protocols*, `decision_wait` vs agent e2e, PII **detect → redact → audit before export**, and “do not block the user on a Datadog timeout.”

---

### 1. System Topology & Data Flow

Three planes. Collapse them — full messages on span attributes, then Prometheus labels from `user.id` — and you leak, explode cardinality, and sample away the incident.

```
                         TELEMETRY / OBSERVABILITY SINKS
         ┌──────────────────────────────────────────────────────────────────┐
         │  L1 METRICS 100%     L2 TAIL-SAMPLED TRACES     L3 NEVER-SAMPLED │
         │  spanmetrics /       Tempo · Honeycomb ·        WORM action audit│
         │  ml_obs.* (15 mo)    LangSmith · Phoenix        (args hash)      │
         │  RED / tokens / $    Datadog LLM-obs · Langfuse platform who-viewed│
         │  content-free        redacted; blob URI on span SIEM / object-lock│
         └────────────▲─────────────────────▲──────────────────▲────────────┘
                      │ metrics connector   │ sampled trees    │ unsampled
                      │ (BEFORE tail drop)  │                  │ audit events
┌─────────────────────┴─────────────────────┴──────────────────┴────────────┐
│ CONTROL PLANE  (sampling, redaction, RBAC, spend, retention, who-viewed)  │
│                                                                           │
│  ┌────────────┐ ┌──────────────┐ ┌─────────────┐ ┌──────────┐ ┌────────┐ │
│  │ IdP / PEP  │ │ Sampling     │ │ PII pipeline│ │ Spend /  │ │ Audit  │ │
│  │ tenant HMAC│ │ policy       │ │ detect →    │ │ retention│ │ of obs │ │
│  │ NEVER from │ │ tail > head  │ │ redact →    │ │ caps     │ │ who    │ │
│  │ model JSON │ │ OTTL / ERROR │ │ audit BEFORE│ │ 14d≠400d │ │ viewed │ │
│  └─────┬──────┘ └──────┬───────┘ └──────┬──────┘ └────┬─────┘ └───┬────┘ │
└────────┼───────────────┼────────────────┼─────────────┼───────────┼──────┘
         │               │                │             │           │
         ▼               ▼                ▼             ▼           ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ DATA PLANE  (OTLP once — do not dual-instrument vendor SDKs)              │
│                                                                           │
│  APP SDK ──► BatchSpanProcessor ──► local/edge collector                  │
│              memory_limiter FIRST → k8sattributes → batch                 │
│              loadbalancing exporter routing_key=traceID  (NOT NGINX)      │
│                    │                                                      │
│                    ▼                                                      │
│              Kafka partition_traces_by_id=true  ──►  sampling StatefulSet │
│              tailsamplingprocessor (ALL spans of a trace, SAME instance)  │
│                    │                                                      │
│              ┌─────┴──────── fan-out ──────────────────────────────────┐  │
│              │ metrics 100% │ traces (kept trees) │ audit topic (all)  │  │
│              └──────────────┴─────────────────────┴────────────────────┘  │
│                                                                           │
│  ┌────────────── TOOL PROXIES (MCP / HTTP — least privilege) ──────────┐  │
│  │ execute_tool {allowlisted} │ mcp tools/call │ retrieve │ generate   │  │
│  │ inject W3C into MCP params._meta (UNPREFIXED traceparent)  SEP-414  │  │
│  │ Identity from verified token / RunContext — NEVER model-filled JSON │  │
│  │ Map MCP isError:true → span status ERROR (JSON-RPC 200 is a lie)    │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
└─────────┬───────────────┬─────────────────┬─────────────────┬─────────────┘
          │               │                 │                 │
          ▼               ▼                 ▼                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ PERSISTENCE LAYER  (three stores; independent TTL / IAM / failure domain) │
│                                                                           │
│  ┌──────────────┐ ┌──────────────────┐ ┌─────────────┐ ┌───────────────┐ │
│  │ Span trees   │ │ Content blobs    │ │ Checkpoints │ │ Action audit  │ │
│  │ Tempo live-  │ │ encrypted S3/GCS │ │ LangGraph   │ │ object-lock / │ │
│  │ store+block- │ │ gen_ai.*.ref URI │ │ PostgresSaver│ │ SIEM WORM     │ │
│  │ builder (3.0)│ │ short TTL, JIT   │ │ ≠ a span    │ │ never sampled │ │
│  └──────────────┘ └──────────────────┘ └─────────────┘ └───────────────┘ │
│  Collector sending_queue file_storage WAL · Kafka hop · NOT SDK memory  │
└───────────────────────────────────────────────────────────────────────────┘
```

**Planes (do not couple):**

| Plane | Owns | Clock | Typical store | Failure if mixed |
| --- | --- | --- | --- | --- |
| **Control** | Sampling policy, redaction, RBAC, spend caps, retention, *who viewed which trace* | Collector / ingest clock; 429 windows | Collector config, IdP, SIEM, LangSmith org settings | App code that “samples interesting traces” by inspecting prompts |
| **Data (telemetry)** | Span trees, metrics, structured logs | User SLO clock (TTFT / e2e) | Tempo, Honeycomb, LangSmith, Phoenix, Datadog, Langfuse | Content on attributes + metrics-generator labels = cardinality + leak |
| **Data (content blobs)** | Prompts, completions, tool I/O, retrieved docs, screenshots | Independent TTL / IAM | Object store, eval dataset, encrypted blob with span pointer | “We deleted the span” while S3 still has the prompt |

**Vendor ingest topology (interview traps):**

- **LangSmith:** emit OTLP **once** → Collector → LangSmith OTLP **and** a second backend. `LANGSMITH_OTEL_ENABLED=true` is fan-out, not dual-SDK. Hybrid `tracing_mode="hybrid"` is **migration** (Python both from one replica; TypeScript: two replicas). Endpoint `https://api.smith.langchain.com/otel`; EU/APAC/AWS US hostnames exist. Cloud/Hybrid/Self-Hosted: if the data plane holds PII, every online evaluator and “Chat with traces” is a **subprocessor**. Hard cap **25,000 runs per trace**.
- **Phoenix:** OTLP/**gRPC 4317** and OTLP/**HTTP on UI port 6006** (`/v1/traces`) — **not** generic 4318. Spans > **4 MB** hit gRPC default max. `PHOENIX_MAX_SPANS_QUEUE_SIZE` default **20,000**. Hide via `TraceConfig` (code beats env).
- **Langfuse Cloud:** OTLP **HTTP only** (no gRPC) `/api/public/otel`. Header `x-langfuse-ingestion-version: 4` or ingested OTel can delay up to **10 minutes**. Scores are **not** OTLP spans (Scores API) and still count as billable units.
- **Datadog:** sample decision on the **root** LLM-obs span applies to all children including downstream APM (`DD_LLMOBS_SAMPLE_RATE` default **1.0** — head-on-root). Metrics `ml_obs.*` remain **100% of instrumented traffic**, 15-month metric retention. Gov sites: product **not supported**.
- **Honeycomb:** Agent Timeline binds on `gen_ai.conversation.id` on **every** span; swim-lanes by `gen_ai.agent.name`. A “GenAI span” is any span the agent triggered, including DB/HTTP. Refinery samples **before ingest** (dropped events do not count toward EPM).
- **Grafana Cloud Adaptive Traces:** managed tail sample. Root received → decide **2 s** after it arrives; no root → wait up to **30 s**. Policies **OR**. Volumetric policies GA **2026-07-22**, Cloud-only. Tempo **3.0** removed the **ingester**; write path is **live-store + block-builder**.

**Request-flow narrative (one user turn → three layers):**

1. **Control / PEP.** TLS terminates. Verified Entra/JWT expands tenant. HMAC `user.id` with a key that is **not** in the trace. Sampling policy, hide-content flags, and blob-upload IAM are **not** model-filled tool args.
2. **Data plane, instrument once.** The app creates one OTel tree: root `invoke_agent {name}` → `execute_tool {allowlisted}` / MCP `tools/call` → child `chat {model}`. W3C `traceparent` rides HTTP headers **and** MCP `params._meta` (SEP-414, **unprefixed** keys). `gen_ai.conversation.id` is copied onto every child including downstream DB/HTTP. Content capture default is **off** (`NO_CONTENT`). If prod must archive prompts, the upload hook writes an encrypted blob and the span stores `gen_ai.input.messages.ref` — the hook SHOULD run **independent of sampling**.
3. **SDK batch.** `BatchSpanProcessor` holds spans (`OTEL_BSP_SCHEDULE_DELAY` default **5,000 ms**, queue **2,048**, batch **512**, export timeout **30,000 ms**). Queue overflow **drops spans in the app process**. Export timeout must be **lower** than the processor timeout or retries stack on in-flight exports (Phoenix docs).
4. **Edge collector.** `memory_limiter` **first** (soft limit = `limit_mib − spike_limit_mib`, retryable refuse; pair `GOMEMLIMIT` ≈ **80%** container). `k8sattributes` **before** tail sampling (the tail processor reassembles batches and they **lose original context**). Collector `batch`: timeout default **200 ms**, `send_batch_size` **8,192**, set `send_batch_max_size` for Phoenix 4 MB / Honeycomb 1 MB/event.
5. **Sticky route.** Load-balancing exporter `routing_key: traceID` to a **headless** Service (ClusterIP returns one rotating VIP — documented collector-contrib **#27014**). Or Kafka `partition_traces_by_id: true` (default **false** — scatter = half-trees). Hash-by-`service.name` is for spanmetrics single-writer, **wrong** for agent+MCP.
6. **Fan-out — this is the three-layer split implemented as a collector graph.**
   - **Metrics 100%:** spanmetrics / connector on the **unsampled** pipe. Datadog computes `ml_obs.*` from 100% even when traces sample. Grafana metrics-generator discards spans whose **end time** is earlier than now−**30 s** slack.
   - **Traces:** `tailsamplingprocessor` (`decision_wait` default **30 s**, `num_traces` **50,000**). Keep ERROR / `content_filter` / HITL / high-latency roots; bytes/rate-limit under overload; probabilistic remainder. Export kept trees to Tempo / LangSmith / Honeycomb / Datadog / Phoenix / Langfuse.
   - **Audit:** every tool invocation (success **and** `isError`) appends to a WORM topic: principal, tool, args **hash**, policy decision, `trace_id`, `checkpoint_id`, model request/response ids. **Never** passes through the tail sampler.
7. **User path is already done.** The handler returned when the agent finished. Telemetry failure must not become a user 500 — circuit-break the exporter (see §4–§5).
8. **Checkpoint plane (other store).** LangGraph `PostgresSaver` snapshots state at super-step boundaries. Task writes inside a super-step are **fault tolerance**, not time-travel. Replay re-calls the model. That DB is not the audit tape and is not the span store.

---

### 2. Core Mechanics & Algorithms

#### 2.1 Invariants

**I1. Control ≠ telemetry ≠ content.** Sampling/redaction/RBAC are not OTLP export, and neither is the prompt blob. Metrics labels are a **closed low-cardinality set**. High-cardinality lives on traces.

**I2. Instrument once.** Dual LangSmith SDK + Datadog LLMObs SDK + OpenInference on the same LLM call produces duplicate trees, doubled token counts, and conflicting parent IDs. OpenInference and Datadog span kinds are **exporters/UI mappings** over OTel’s wire.

**I3. Replay is not audit truth.** Log the LLM/tool output as the authoritative fact. Reserve Replay for “what if.”

**I4. `traceparent` sampled flag `01` is a head-sampling hint**, not a tail decision. A `trace_flags` policy that samples if `01` was set on **any** span inverts intent when a chatty SDK sets `01` everywhere.

#### 2.2 W3C Trace Context is the wire

```
traceparent: 00-{32 hex trace-id}-{16 hex parent-id}-{2 hex flags}
example:     00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01
```

- Version `00`. Trace ID 16 bytes, parent/span ID 8 bytes. Flag `01` = sampled.
- Every tracing tool MUST set `traceparent` even when it only relies on vendor `tracestate`.
- `tracestate`: comma-separated vendor `key=value`, **max 32 members**. When a system updates `parent-id`, it MUST move its entry to the **left**. Intermediaries MUST forward both headers.
- OTel tail sampling can write probability fields (`rv`, `th`) into the `ot` section of `tracestate` when feature gate `processor.tailsamplingprocessor.usetracestate` is on (alpha, **off by default**). With the gate on, `probabilistic` consumes/rewrites those fields so adjusted counts stay unbiased; without them it falls back to FNV-1a of the trace ID + `hash_salt`.

W3C **Baggage** is a separate spec. Do not put PII in baggage: every hop that implements the propagator forwards it. `tracestate` values are opaque to other vendors — stuffing a tenant id there is not an ACL.

**MCP:** instrumentations SHOULD inject configured OTel propagators into MCP request `params._meta`. Despite MCP’s DNS-prefix convention, W3C keys MUST be **unprefixed**: `traceparent`, `tracestate`, `baggage` (SEP-414). DNS-prefixing (`io.modelcontextprotocol.traceparent`) **breaks** traces. MCP spec **2026-07-28** documents SEP-414; SEP-2028 is a related SEP for forwarding `_meta` onto HTTP headers — do **not** assume `_meta` automatically becomes the HTTP `traceparent` unless that client implements it. Same revision **deprecates** protocol Logging (`notifications/message`) in favor of OTel / stderr.

If inbound `_meta` has no context, MCP Python SDK server spans parent to **ambient** server context (health/stdio bootstrap) — still not the client’s tree. Server SHOULD **link** ambient context rather than using it as parent of `tools/call`.

#### 2.3 OTel GenAI vs OpenInference (not competing protocols)

OTel GenAI conventions are **Development, not Stable**. Official blog 2026-05-14: **by default, no prompt content or tool arguments**; metadata only (model names, token counts, durations). Independent 2026 write-ups: **0 of the GenAI-specific span/event/metric/attribute set is Stable**. Shared core attrs (`error.type`, `server.address`) are Stable. Newer `opentelemetry-util-genai` emits latest experimental **unconditionally**. Opt-in for older Python: `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`.

**OTel client / LLM spans.** Kind **CLIENT** (MAY be INTERNAL for in-process models). Name `{gen_ai.operation.name} {gen_ai.request.model}`. `gen_ai.operation.name`: `chat` | `text_completion` | `generate_content` | `embeddings` | `execute_tool` | `create_agent` | `invoke_agent` | `invoke_workflow` | `retrieval`. Required-class: `gen_ai.provider.name`, `gen_ai.operation.name`, `gen_ai.request.model`. Usage: `gen_ai.usage.input_tokens` (includes cached) + output + cache-read/creation splits. `gen_ai.response.model` is what actually served. `finish_reasons`: `stop` | `tool_calls` | `length` | `content_filter`.

**Tool spans.** Kind INTERNAL. Name SHOULD be `execute_tool {gen_ai.tool.name}`. `gen_ai.tool.call.arguments` / `.result` are **Opt-In**. Auto-instrumentors see the *model’s request* for a tool; wrap the **function body** or you get a chat span with `finish_reason=tool_calls` and a missing child.

**Agent spans.** `invoke_agent`: CLIENT for remote APIs, INTERNAL for in-process (LangGraph). Honeycomb Timeline counts LLM calls as `operation.name ∈ {chat, generate_content, text_completion}` and tools as `execute_tool`.

**Metrics (always-on, content-free).** Required: `gen_ai.client.token.usage`, `gen_ai.client.operation.duration`. Recommended streaming: `time_to_first_chunk` (client-observed TTFT **including network**; streaming only), `time_per_output_chunk` (inter-chunk). Server-side: `gen_ai.server.time_to_first_token`, `time_per_output_token`. Explicit duration/TTFC buckets (seconds):

`[0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92]`

Last bucket **81.92 s** — a **120 s** agent e2e **overflows**. Add an application histogram on the **root** `invoke_agent` with wider bounds. Do not reuse chat-operation buckets for product SLOs. Streaming span ends when the **stream completes**, not at first token; TTFT is a histogram or span event, not a second trace.

**Content capture — spec has three usage patterns; Python has four env-var modes.**

| Spec pattern | When |
| --- | --- |
| Default: do **not** record instructions/inputs/outputs | Production default |
| Record on span attrs (`gen_ai.system_instructions`, `gen_ai.input.messages`, `gen_ai.output.messages`) | Pre-prod, or volume-manageable store inside the compliance boundary |
| Store content **externally**, record `gen_ai.*.messages.ref` on the span | Production for volume + separate ACL |

Python `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`: `NO_CONTENT` (default) | `SPAN_ONLY` | `EVENT_ONLY` | `SPAN_AND_EVENT`. Legacy `true` is gone from that list. `OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK=upload` + `UPLOAD_BASE_PATH` writes fsspec (`s3://`, `gs://`, local). v1.37 collapsed per-message events into three aggregated fields that MAY sit on the span **or** on opt-in event `gen_ai.client.inference.operation.details`. Events are Development and **not yet available in some languages**. Spec-marked **sensitive**: input/output messages, system_instructions, tool arguments/result, `gen_ai.prompt.variable`. Turning capture on is a **data-classification decision**, not a debug flag.

Do **not** treat `gen_ai.evaluation.result` as an eval platform. It is a **carrier**. OTel does not run the judge.

**OpenInference** (Arize): conventions **on top of** OTel. Transport is OTLP. Required attribute `openinference.span.kind` in **ALL CAPS** — a different field from OTel `span_kind` (CLIENT/INTERNAL), because that is already taken. Kinds: `LLM`, `AGENT`, `CHAIN`, `TOOL`, `RETRIEVER`, `RERANKER`, `EMBEDDING`, `GUARDRAIL`, `EVALUATOR`, `PROMPT`, `UNKNOWN`. Attributes are **flattened** (`llm.input_messages.0.message.role`) — a 20-turn chat explodes key count. `llm.invocation_parameters` as a JSON **string** is a PII/secret magnet.

**Mapping (do not treat as 1:1 identity):**

| OTel `operation.name` | OpenInference kind | Datadog kind | Langfuse type |
| --- | --- | --- | --- |
| `chat` / `generate_content` | `LLM` | `LLM` (billable) | `generation` |
| `execute_tool` | `TOOL` | `tool` (**not** a valid Datadog root) | `tool` |
| `invoke_agent` | `AGENT` | `agent` (valid root) | `agent` |
| `invoke_workflow` | `CHAIN` | `workflow` (valid root) | `chain` |
| (eval event) | `EVALUATOR` | instrumented judge = LLM span | `evaluator` / Scores API |

Datadog: tool / embedding / retrieval / task are **not** valid roots. Billing = **LLM spans only**; other kinds free. Langfuse: `langfuse.observation.type` **always wins** over inferred type. Trace I/O is **deprecated** — put I/O on the root observation.

#### 2.4 Trace vs thread vs trajectory vs checkpoint

| Object | Stores | Purpose | Source of truth |
| --- | --- | --- | --- |
| **Trace tree** | Nested spans/runs for one invocation | “Which child timed out?” | OTel trace ID / LangSmith `trace_id` |
| **Thread / session** | Sequence of traces sharing `thread_id` / `session.id` / `gen_ai.conversation.id` | Multi-turn | Metadata key **you** must set |
| **Trajectory** | Deduped ordered messages + (in graphs) state transitions | Scan the conversation; inspect loops | *Projection*, not a store |
| **Graph checkpoint** | Full **state snapshot** at each super-step | Time-travel, fork, resume | Checkpointer DB — **not** a span |

LangSmith: **run** ≈ OTel span; **trace** = runs for one operation; **thread** = traces for a multi-turn session; **trajectory** = flat message list with nesting removed. Messages view (beta) needs `thread_id` plus `ls_agent_type: "root"` on the turn’s top run. `subagent` appears as a subagent action; `middleware` / `compaction` are **filtered out** of the transcript. `LS_MESSAGE_VIEW_EXCLUDE` (presence of the key, not truthiness — `{false}` still excludes) hides a run from Messages while leaving it in the tree and metrics.

Langfuse: **observations** nest under a **trace**; **sessions** group traces. v4: no separately ingested trace entity — the OTel trace ID *is* the grouping. Create+update of a generation is **one** OTel span assembled in memory, not two legacy ingestion events.

Honeycomb conversation metrics: duration, trace count, LLM calls, tool calls, failures, total tokens. “Show Failures Only” depends on `error.type` and span status — swallowed exceptions ⇒ empty filter.

#### 2.5 Head sampling vs tail sampling (agent default = tail)

**Head** (`TraceIdRatioBased`) decides at span **start**, before tools, `finish_reason`, or the 40-step loop. Cheap. **Wrong** as the primary policy for agents.

**Tail** waits until the tree is approximately complete, then keeps or drops the **whole** tree. Collector contrib `tailsamplingprocessor` is **beta**, stateful, and **must** receive every span of a trace on the **same instance**. It groups by `trace_id` itself (no `groupbytraceprocessor`). Place it **after** `k8sattributes`.

| Knob | Default | Meaning |
| --- | --- | --- |
| `decision_wait` | **30 s** | Under `trace-complete` (default), when the decision is made |
| `decision_wait_after_root_received` | **0 s** | Optional acceleration; `0` disables |
| `num_traces` | **50,000** | In-memory cap; excess → `trace_dropped_too_early` |
| `sampling_strategy` | `trace-complete` | `span-ingest` evaluates each batch; stateful policies rejected |
| `decision_cache.*_cache_size` | **0** (off) | LRU of keep/drop for **late spans**; size **≫ `num_traces`** |

Policies (required; no default): `always_sample`, `latency` (earliest start → latest end, **ignores gaps** — HITL idle inflates this), `numeric_attribute`, `probabilistic` (FNV-1a or `tracestate` `rv`/`th`), `status_code`, `string_attribute`, `trace_state`, `trace_flags`, `rate_limiting` / `bytes_limiting`, `span_count`, `boolean_attribute`, `ottl_condition`, `and` / `not` / `drop`, `composite` with per-policy rate allocation of `max_total_spans_per_second`.

Agent-shaped stack (order matters): keep ERROR / `finish_reason=content_filter` / policy-deny / HITL; keep high-latency roots; bytes/rate limit under overload; probabilistic remainder with `tracestate` rewrite; SDK head sample **only** if collectors are saturated.

Datadog SDK sampling is **head-on-the-root** at ingest (cost control, not a GenAI policy engine). Honeycomb Refinery is the productized tail proxy. Grafana Adaptive **2 s after root** is **not** Collector `decision_wait=30 s` — an `invoke_agent` that starts at t=0 and finishes at t=90 s is decided at **t=2 s**; later MCP children are late. Diversity fingerprints default to `service.name` / `http.route` / `http.response.status_code` every **15 minutes** — add `gen_ai.operation.name` / `error.type` or rare `content_filter` paths vanish.

Honeycomb published GenAI guidance: keep **100% of traces that carry `gen_ai.conversation.id`**, list that rule first (`RulesBasedSampler` matches if **any** span satisfies). True for a mixed APM estate; **false** when GenAI *is* the product (you pay 100% of EPM).

#### 2.6 Complexity of tail-sampling windows

Let \(\lambda\) = new traces/s, \(W\) = `decision_wait` (s), \(S\) = mean spans/trace, \(B\) = mean bytes/span, \(P\) = policies evaluated per trace.

| Quantity | Bound | Production implication |
| --- | --- | --- |
| In-flight traces | \(\Theta(\lambda W)\) | Must `num_traces` \(\ge \lambda W\) or `trace_dropped_too_early` |
| Default cap | \(50{,}000 / 30 \approx 1{,}667\) traces/s | Theoretical ceiling at defaults **[inferred from knobs]** |
| Memory | \(O(\lambda W \cdot S \cdot B)\) | Metadata-only \(S{=}25, B{=}2\,\mathrm{KB}\) → \(50\,\mathrm{k} \times 50\,\mathrm{KB} = 2.5\,\mathrm{GB}\). Content-on \(B{=}50\,\mathrm{KB}\) → \(62.5\,\mathrm{GB}\) — OOM first **[inferred from research storage shape]** |
| Decision work | \(O(S \cdot P)\) per trace at timer | OTTL regex / `string_attribute` regex dominates; `sample_on_first_match` cuts average |
| Late-span lookup | \(O(1)\) LRU | `decision_cache` size **≫** `num_traces` or a late MCP span becomes a one-span orphan |
| Routing | \(O(1)\) hash(`trace_id`) | Wrong key (`service`) or ClusterIP VIP ⇒ two incomplete trees, each looking “OK” |
| Elastic 2026 result | span-ingest + Pebble: memory **−65%**, CPU **~2×** | Lets you raise \(W\) / `num_traces` without OOM; **not** a portable SLO |

A 90 s tool + HITL with \(W=30\) s samples a **partial** tree as “OK”. Raise \(W\) to \(\ge\) product p99 e2e + slack, or wait for the root to **end**. `span-ingest` releases earlier but **cannot** run stateful whole-tree policies. `latency` policy **ignores gaps** — HITL idle looks like an SLO breach.

Canonical two-tier: edge collectors (`memory_limiter`, `k8sattributes`, batch, loadbalancing `traceID`) → sampling StatefulSet. Kafka between tiers with `partition_traces_by_id: true`. Consumer must propagate processor errors so the consumer **pauses** (true back-pressure) rather than OOM.

#### 2.7 Burn-rate SLOs (Google SRE → Datadog)

Google SRE Workbook Table 5-6 / 5-8 for a **30-day, 99.9%** SLO:

| Severity | Long window | Short window | Burn rate | Error budget consumed |
| --- | --- | --- | --- | --- |
| Page | 1 hour | 5 minutes | **14.4** | 2% |
| Page | 6 hours | 30 minutes | **6** | 5% |
| Ticket | 3 days | 6 hours | **1** | 10% |

Short window = **1/12** of long. Multiwindow AND: fire only if **both** windows exceed so the alert resets when burn stops. Apply to **availability and TTFT/e2e**, not token-count gauges.

Datadog: `burn_rate("slo_id").over("30d").long_window("1h").short_window("5m") > 14.4`. Max long window **48 hours** — cannot encode the SRE 3-day ticket window as one monitor. Same 14.4 / 6 table for 30-day; 7-day page at **16.8×** / 1 h. Honeycomb Pro includes **2 SLOs** (pick TTFT + availability; cost on a trigger). Enterprise starts at **100 SLOs**.

NFR targets to *set* (not industry SLOs): ingest 429 rate = 0; `trace_dropped_too_early` ≈ 0; `otelcol_exporter_queue_size / capacity` < 0.5; metrics-generator discarded-late-span rate ≈ 0; PII findings in traces = 0 after redaction QA. Alert on `otelcol_processor_refused_spans` and Phoenix `RESOURCE_EXHAUSTED` as **availability of the telemetry plane**, not as a user SLO.

---

### 3. Token Economics & NFR Analysis

Interview failure: quoting `$/1k traces` across Datadog, LangSmith, Honeycomb, and Langfuse as if they were the same object. **Do not mix SKUs.**

#### 3.1 `$ cost per 1k traces/runs` — meters are not interchangeable

| Vendor | Billable unit | Published list (2026-09-02) | Retention on that SKU |
| --- | --- | --- | --- |
| **LangSmith** | **Trace** (root + all child runs = 1) + extended-retention upgrade | Base **0.05¢/trace**, extended **10× = 0.50¢/trace** (upgrade **0.45¢**). Seats: Developer **$0** (1 seat, 5k base/mo), Plus **$39/seat** (10k included). Overlay: **1 LCU = $1.50**, **1 LSU = $1.00** | Base **14 days**; extended **400 days** (Ent customizable). Monitoring metadata **>30 days** after base deletion. Datasets: **indefinite** |
| **Datadog Agent Observability** | **LLM spans only**. Tool/workflow/agent/embedding/retrieval **free** | First **100k LLM spans/mo** **$160** annual / **$200** M2M / **$240** on-demand. Overage **$3.50 / $4.20 / $5 per 10k**. Retention add-on **$1.50 / $3 / $4 per 10k LLM spans** for 30 / 60 / 90-day traces. Free **40k**/mo, 15-day default | Default **15 days** traces |
| **Honeycomb** | **Event** = **one span** (SpanEvent and Link also count) | Free **20M events/mo** + **100M** metric datapoints. Pro starting **$150/mo** (50M events). From **2026-07-01** new Pro **$3.00 / million events** vs legacy **$1.30 / million**; grace through **2026-12-31**; tiers to **750M events/mo** | Events **60 days** (ingest date, not span timestamp). Metrics **13 months** default |
| **Langfuse Cloud** | **Unit** = trace + observation + score | Hobby **50k** units, 30d, 2 users. Core **$29/mo**, 100k, 90d. Pro **$199/mo**, 100k, **3 years**. Ent **$2,499/mo**. Overage **$8 / $7 / $6.50 / $6 per 100k** across 100k–1M / 1–10M / 10–50M / 50M+ | Plan-tier as left |
| **Phoenix / Tempo self-host** | Your disks + query compute | No SaaS trace SKU | You choose |
| **Grafana Cloud Traces** | **GB processed / written / retained** (not span count) | **$0.05/GB processed** (before Adaptive) + **$0.40/GB written** after **50 GB** allotment + **$0.10/GB** per extra **30-day** retention. Free: **50 GB**, **14-day**. Pro platform **$19/mo** includes 50 GB and **30-day** | 14d free / 30d paid default |

**LangSmith SKU caveat.** usage-and-billing still documents **0.05¢ / 0.50¢** per trace. The marketing page prints seats/LCU/LSU **without** a numeric per-trace rate. A June 2026 third-party guide claims **$2.50 / $5.00 per 1k**. **Do not mix those two numbers in one cost model.** Observability-concepts currently says SaaS retains **180 days**; usage-and-billing says **14 / 400**. Treat **14/400** as the billing contract.

**`$ per 1k` (named assumptions):**

- LangSmith base at documented 0.05¢: **$0.50 / 1k traces**. Extended: **$5.00 / 1k traces**. Official if the 0.05¢ line still invoices.
- Datadog: **not priced per trace**. **[inferred]** 1 agent request × 8 LLM calls = 8 billable spans. Annual overage **$3.50/10k LLM spans** = **$0.35 / 1k LLM spans** = **$2.80 / 1k such requests**. The $160 package is **$1.60 / 1k LLM spans** if you fill the 100k (and $0 inside free 40k).
- Honeycomb: **[inferred]** new Pro **$3.00 / 1M events** = **$0.003 / 1k events**. A 25-span agent turn = **$0.075 / 1k traces**. Refinery drops do not bill. Legacy $1.30/M = **$0.0013 / 1k events**.
- Langfuse: **[inferred]** not per trace. Official calculator: 1M units on Core = **$101/mo**. If a trace is 1 trace + 6 observations + 1 score = 8 units, 1k traces ≈ 8k units ≈ **$0.64** of overage at $8/100k **after** the included 100k. LLM-as-judge scores **count as units**.
- Grafana Cloud Traces: **not priced per trace**. **[inferred]** 100k turns × 25 spans × **2 KB** metadata-only ≈ **5 GB** written → inside 50 GB allotment (**$0** write after the $19 platform fee). Same shape at **50 KB**/span with content-on ≈ **125 GB** → **75 GB** billable write × $0.40 = **$30** write + 125 × $0.05 = **$6.25** process. Extra 30 days on 125 GB × $0.10 = **$12.50**. Content, not span count, is the Grafana meter.

**Auto-upgrade tax (LangSmith).** Online evaluators and automation rules **default to extending retention**. Matching **any run** upgrades the **entire trace**; a **thread-level** rule upgrades **every trace in the thread**. UI feedback/notes/annotation-queue adds do **not** upgrade. Experiments start at extended. This is how a 14-day debug project becomes a 400-day invoice.

**Engine LCU.** An Engine run can consume **~5–30 LCU**; 1 LCU = **$1.50** → **[inferred] ~$7.50–$45 per Engine run**. Engine is scheduled **once every 6 hours**. Four runs/day × 30 days × 15 LCU mid-range × $1.50 = **[inferred] ~$2,700/mo** on Plus, independent of the trace meter.

**Datadog SDS** (PII scan of traces) is a **separate SKU**: **$0.30 / $0.36 / $0.45 per scanned GB** annual / M2M / on-demand. “Unlimited context” does not include SDS.

**Worked monthly bills [inferred]** except the SKU itself. Shape: 100k **user turns**/month; 1 trace / 8 LLM calls / 12 tool spans / 25 total spans; 10% get an online-eval score; content captured.

| Stack | Arithmetic | Monthly (inferred) |
| --- | --- | --- |
| LangSmith Plus, base only (0.05¢ docs) | $39 seat + (100k − 10k) × $0.0005 | **~$84** + content 429 risk on 5 GB/h |
| LangSmith Plus, 10% auto-extended | $39 + 100k × $0.0005 + 10k × $0.0045 | **~$134** |
| LangSmith if third-party $2.50/1k were invoiced (**not** the 0.05¢ line) | $39 + 90k × $0.0025 | **~$264** — do not mix |
| Datadog annual, 800k LLM spans | $160 for first 100k + 70 × $3.50 | **~$405** traces; 15d; SDS extra |
| Datadog + 90d retention add-on | 80 × $4 | **+$320** retention |
| Honeycomb 2026 Pro, 2.5M spans (25×100k) | 2.5 × $3.00/M | **$7.50** event overage **if** already on a 50M tier ($150) |
| Honeycomb, GenAI-only 100M spans | 100 × $3 | **$300** events on a fitting tier; 60d |
| Langfuse Core, 100k traces × 8 units | 800k units: $29 + 7 × $8 | **~$85** |
| Grafana Cloud Pro, metadata-only 5 GB | $19 platform (50 GB included) | **$19** traces + 30d |
| Grafana Cloud Pro, content-on 125 GB | $19 + 75×$0.40 write + 125×$0.05 process | **~$55** traces (30d) |

Datadog favors **deep** trees (tools free). LangSmith bills the **tree as one**. Honeycomb bills **every span**. Langfuse bills **every observation and every score**. Grafana bills **GB**. Pick the meter that matches topology, then cap content.

Observable run cost (architecture, not a SKU):

```text
observable_run_cost
  ~= model_input_cost + cached_read_cost + cache_write_cost
   + output_cost + reasoning_token_cost
   + tool_or_retrieval_surcharges
   + trace / checkpoint persistence overhead
   + eval LLM-as-judge cost (if online)
```

Datadog Cost Overview: public provider prices × annotated tokens; unit **nanodollars**. `PARTIAL COST` / `COST UNAVAILABLE` when spans cannot be priced. Aggregate-only `input_tokens` ⇒ **standard input rate on all input** — overestimate when cache hits exist. Supports **800+** text-based models. Custom cost tags must be **bounded**.

#### 3.2 Storage shape and ingest ceilings (why LLM traces are 10–100× APM)

APM span: tens to hundreds of bytes. LLM span with content: 2–32k tokens ≈ **8–128 KB UTF-8 per call**, plus tool JSON.

LangSmith hourly **data** caps (an “event” = run create **or** update; create+update in the same UTC hour = **2 events**; payload sums create+update sizes):

| Plan | Events / hour | Payload / hour |
| --- | --- | --- |
| Developer, no card | 50,000 | **500 MB** |
| Developer, card on file | 250,000 | **2.5 GB** |
| Startup/Plus | 500,000 | **5.0 GB** |
| Enterprise | Custom | Custom |

Plus ALB: **5,000** `POST|PATCH /runs*` per **minute** per key (SDK batches ≤ **100** runs/call). Generic `*`: **2,000**/min. `GET /runs/:id`: **30**/min. Developer no-card: **5,000 traces / calendar month** then 429.

**[inferred] payload math:** 5.0 GB/h ÷ 500k events = **10 KB/event** average headroom on Plus. A 50 KB prompt on create **and** 80 KB on update = **130 KB** for one run. Content-on-by-default **429s you before span-count does**.

Honeycomb event contract: max **2,000** distinct fields; entire event **< 1 MB** uncompressed JSON; each string field **≤ 64 KB**. Exceed → event **rejected** (does not count against EPM). OpenInference flattening of a 40-turn chat can blow **2,000 fields before 1 MB**. A ~32k-token prompt is ~128 KB UTF-8 and **does not fit** in one Honeycomb string field — blob + pointer. Burst: sending **>2× daily event target** does not count the excess against EPM, up to **3 times/month**. Daily target = EPM / **30.4**. Second consecutive overage month → 10-day warning → throttle accepts **1 of 10** until under target **72 hours**. Enterprise exempt from throttling. Retention from **ingest date**.

Langfuse Cloud ingest: Hobby **1,000 req/min**, Core **4,000**, Pro **20,000**, Ent custom.

Phoenix: 4 MB gRPC. Tempo example override `max_bytes_per_trace: 5_000_000` — **example configs, not Grafana Cloud’s unpublished tenant contract**.

#### 3.3 Latency SLA — two clocks, numeric ms

> ⚠️ Gap: **No vendor publishes a universal “trace ingest p99 = X ms” SLO, and none publishes composed agent e2e p50/p95/p99** you can copy into an architecture review. Bound the pipeline from published knobs. Product percentiles below are architecture-derived **[inferred] policy targets**, not vendor SLOs. Clock-split: (a) user-facing agent, (b) telemetry pipeline. Putting a judge on the user p99 path is a latency tax, not observability (see evals).

**Published delay sources (not SLOs):**

| Stage | Published delay | Effect |
| --- | --- | --- |
| SDK batch | `OTEL_BSP_SCHEDULE_DELAY` default **5,000 ms**; export timeout **30,000 ms** | Seconds of holdback; overflow drops in-process |
| Collector batch | timeout default **200 ms** | Flush trigger, not a hard cap unless `send_batch_max_size` set |
| Tail sample | Collector `decision_wait` default **30,000 ms**; Grafana Adaptive **2,000 ms after root** / **30,000 ms if no root**; Grafana metrics slack **30,000 ms** | Completeness vs freshness |
| Langfuse OTel without v4 header | up to **600,000 ms** (10 min) | Looks like “missing traces” |
| Tempo query_frontend example | `duration_slo: 5,000 ms` (search / by-id) | **Read** SLO, not ingest |
| Honeycomb trigger “event latency” | event timestamp vs arrival | Long agent traces inflate the chart even when spans arrive promptly |
| Exporter retry budget | `max_elapsed_time` default **300,000 ms** | Then drop and count |

**(a) Product e2e — unpublished. [inferred] policy targets** calibrated to OTel suggested histogram bounds (seconds → ms) plus research “agent e2e may be 10–120 s”. Do not SLO the inner `chat` p99 as if it were UX. Critical path (architecture):

```text
critical_path_latency
  ~= planning_llm_call
   + max(parallel_branch_durations)
   + verification_llm_call
   + approvals (HITL)
   + trace / checkpoint persistence
   + network hops (MCP, tools)
```

| Path | **p50** | **p95** | **p99** | Mitigation (one line) |
| --- | --- | --- | --- | --- |
| **Inner chat TTFT** (streaming; `time_to_first_chunk`; histogram 0.64 / 2.56 / 5.12 s buckets) **[inferred]** | **640 ms** | **2,560 ms** | **5,120 ms** | Stream; do not emit TTFT on non-stream; cache-read split on the span (physics in caching module) |
| **Inner chat e2e** (one `chat` span, stream-complete) **[inferred]** | **1,280 ms** | **10,240 ms** | **20,480 ms** | Timeout the provider independently of the exporter; `finish_reason=length` is an availability miss unless it is a defined outcome |
| **Agent e2e** (root `invoke_agent`; 10–120 s class; **do not** reuse chat buckets) **[inferred]** | **15,000 ms** | **60,000 ms** | **90,000 ms** | Parallel tools count as `max()` not `sum()`; HITL is a **gap** the latency policy ignores; 120,000 ms **overflows** the 81,920 ms last suggested bucket — use a wider root histogram |

Availability SLI: root span OK **and** `finish_reason` not in `{length, content_filter}` unless that is a defined product outcome. 429 / overload is an error from the user’s seat. `$ / successful task` is a **budget** unless finance says otherwise.

**(b) Telemetry pipeline itself — [inferred] from published knobs.** “Collector lag” = span **end** → backend **queryable** (metrics vs traces are different pipes).

| Path | **p50** | **p95** | **p99** | Grounding |
| --- | --- | --- | --- | --- |
| **SDK BatchSpanProcessor holdback** (default schedule) **[inferred]** | **2,500 ms** | **5,000 ms** | **5,000 ms** | Uniform over 0–5,000 ms schedule delay; p99 still capped by schedule unless the queue is full |
| **SDK export attempt ceiling** | — | — | **30,000 ms** | `OTEL_BSP_EXPORT_TIMEOUT`; processor retries while original export is in flight if exporter timeout ≥ this |
| **Collector batch** (default, low agent QPS) **[inferred]** | **100 ms** | **200 ms** | **200 ms** | timeout 200 ms; `send_batch_size` 8,192 rarely binds |
| **Metrics path (100%, pre-sample)** **[inferred]** | **2,600 ms** | **5,200 ms** | **8,000 ms** | SDK holdback + collector batch; p99 allows one retry/hitch. Grafana slack: spans ending **>30,000 ms** ago are **discarded from metrics** (rate → 0 during the incident if `decision_wait` + slack collide) |
| **Tail-sampled traces, Collector `trace-complete`** **[inferred]** | **32,600 ms** | **35,200 ms** | **38,000 ms** | `decision_wait` **30,000 ms** is a **timer**, not a distribution — every kept trace waits ~W, plus SDK/collector. Raise W to ≥ product p99 e2e or you sample partial trees |
| **Grafana Adaptive Traces (root present)** | **2,000 ms** after root | **2,000 ms** after root | **30,000 ms** (no-root path) | Published two-stage; child MCP spans after t=2 s are late |
| **Langfuse OTLP missing v4 header** | — | — | **600,000 ms** | Published up to 10 min — looks like missing traces |
| **Tempo by-id read (example config)** | — | — | **5,000 ms** | Example `duration_slo`; **read**, not ingest |

Mitigations mapped to percentiles:

- **p50 (user):** streaming TTFT; do not put Collector/Datadog on the handler critical path (async export).
- **p95 (user):** cap agent hops; parallelize tools; timeout MCP independently.
- **p99 (user):** HITL and long tools are the tail — measure with the **root** histogram; never wait on the exporter.
- **p50 (telemetry):** cut BSP schedule in the gateway collector path if UI freshness matters; metrics connector before tail wait.
- **p95 (telemetry):** size `num_traces` to \(\lambda W\); enable late-span decision cache.
- **p99 (telemetry):** persistent `file_storage` queue; alert on queue/capacity and `trace_dropped_too_early`; never combine 30 s tail wait with 30 s metrics slack without widening slack (support ticket — granularity coarsens).

#### 3.4 Throughput / back-pressure

| Ceiling | Number | Effect |
| --- | --- | --- |
| SDK BSP queue | **2,048** spans | Overflow **drops in the app** |
| Collector sending_queue | default **1,000** batches, **10** consumers | Full ⇒ **drop and count** (`otelcol_exporter_send_failed_spans`) |
| Retry budget | **300,000 ms** then drop | Disk-full and retry-timeout still drop |
| Phoenix in-process queue | **20,000** spans default | `RESOURCE_EXHAUSTED` under embedding-vector attributes |
| Tail in-memory | **50,000** traces | `trace_dropped_too_early` |
| LangSmith Plus | **500k events/h**, **5.0 GB/h**, **5k POST\|PATCH /runs\* / min** | Content-on 429s look like flaky tools |
| Honeycomb throttle | accept **1 of 10** after 2nd overage month | Head-random missing children |
| Grafana metrics slack | **30,000 ms** | Late spans vanish from RED |

**Back-pressure design:** (1) `memory_limiter` first — soft refuse is **retryable** so OTLP receivers apply upstream pressure (custom receivers that ignore this turn protection into silent loss); (2) Kafka between edge and sampling, consumer **pauses** on processor error; (3) persistent exporter queue (`sending_queue.storage: file_storage` — **no in-memory queue when storage is set**; crash without WAL **loses the batch**; auth context from the original request is **not** preserved across the WAL); (4) degrade: drop content → drop traces keep metrics → local disk buffer; (5) SDK head sample only as last-ditch — accept bias; (6) bulkhead **user serve** vs **exporter** — Datadog timeout must not become a user 500.

**[inferred] tail-sampler occupancy:** \(\lambda W \le\) `num_traces`. At \(W=30\) s and 50k cap, \(\lambda \le 1{,}667\) traces/s. Content-on inflates \(B\) until memory binds first.

#### 3.5 NFRs and explicit trade-offs

| NFR | Production stance | Competes with |
| --- | --- | --- |
| **Availability of the product vs of observability** | Product SLO is the user path. Telemetry is **best-effort with a budgeted drop**. Alert on collector refused/dropped as a *telemetry* SLO. Honeycomb 1-in-10 throttle and LangSmith 429 are **obs outages**, not user outages — unless you blocked the handler on export | Completeness of traces vs user p99 |
| **RPO of traces** | Sampled Tempo/LangSmith is **lossy by policy**. RPO for a dropped happy-path tree is “never stored.” Datadog 15 d vs 90 d add-on; LangSmith 14 d vs 400 d (10× $); Honeycomb 60 d from **ingest** | Debug window vs $ |
| **RPO of audit** | Unsampled WORM. RPO = last fsync / object-lock put. Must survive collector crash (WAL or Kafka) | Cost (you keep 100% of tool facts) vs trace sampling |
| **RTO of traces** | Re-point Grafana/LangSmith; you **cannot** reconstruct a tail-dropped tree. LangGraph replay re-executes — not RTO of the old tree | Debugger UX vs forensic truth |
| **RTO of audit** | Restore object-lock / SIEM. Independent failure domain from Tempo | Ops complexity |
| **Compliance** | Trace backend is a **subprocessor** if it holds prompts. Hybrid/self-host when the data plane is PII. GDPR “delete the user” ≠ delete billing aggregates. LangSmith purge: HTTP 200 = **queued**, jobs run **on the weekend**, no job-status API, **1,000 traces/request**. Public share = unauthenticated URL — disable org-wide on day one. Datadog Agent Obs **not** on gov sites. Langfuse HIPAA BAA on Pro+; audit logs **Enterprise only** | Time-to-debug (content-on SaaS) vs residency |
| **Correctness of dashboards** | Unbiased rates need `sample_rate` / `tracestate` probability / Honeycomb `SampleRate`. `count()` without `1/sample_rate` lies. Error-only keep overfits to failures and hides 2M-token happy 200s | Cheap 1% head sample vs rare jailbreak recall |

**RPO/RTO [inferred from architecture, not vendor RPO SKUs]:** RPO_audit = last WORM put (seconds if fsync; “empty” if SDK memory exporter). RTO_audit = SIEM search (seconds) vs “we only have sampled Tempo” (**cannot restore**). RPO_traces = last kept tree; a 99% drop is not a storage failure. RTO_traces after collector crash without `file_storage` = **lost in-flight batch**. Tempo 3.0 live-store crash still loses in-flight WAL unless object storage + Kafka is in front.

---

### 4. Distributed Resilience & Security

#### 4.1 Durable execution: traces vs checkpoints vs collector WAL

| Store | What persists | Survives process kill? | Replay semantics |
| --- | --- | --- | --- |
| SDK in-memory exporter / BSP queue | Current batch | **No** | Lost spans |
| Collector `sending_queue` in-memory | Batches (`queue_size` 1000) | **No** | Lost |
| Collector `file_storage` WAL | Same batches on disk | Process yes; disk-full still drops | Export retry; **auth context not stored** |
| Kafka `partition_traces_by_id` | Whole trees keyed by hex trace ID | Broker retention | Consumer pause = back-pressure |
| Tempo 3.0 live-store WAL | In-flight blocks (`max_block_duration` **30 s**, `max_block_bytes` **50 MiB**, `complete_block_timeout` **20 m`) | Process: until flushed to object storage | Do not copy pre-3.0 `ingester.complete_block_timeout` |
| LangGraph checkpoint | Full state at super-step | If Postgres/S3 checkpointer does | Resume skips completed steps; **does not** restore dropped spans |
| Action audit (WORM) | Tool facts + hashes | If object-lock / SIEM does | Read-only proof |

**Broken trees across MCP.** Missing child = head sample on the MCP server, different collector without sticky `traceID`, `decision_wait` shorter than the tool, LangSmith 25k run cap, gRPC 4 MB fail, 429 hourly payload (create without completion), dual vendor SDKs, Phoenix HTTP on 4318, ClusterIP in front of the tail sampler, Honeycomb 1-in-10 throttle, or MCP `isError: true` on OK JSON-RPC (RED looks 100% healthy while the agent loops). Stdio MCP servers as child processes **without** `OTEL_EXPORTER_OTLP_ENDPOINT` is the #1 broken-tree cause — prefer a sidecar collector on the host. Do **not** emit duplicate `execute_tool` plus MCP `tools/call` as two siblings — nest or enrich.

Checkpointer vs trace divergence: checkpoint says tool X succeeded; span was dropped (queue full, 429, 25k cap). Resume skips the tool; audit cannot show args. Forked thread: set `thread_id` explicitly.

#### 4.2 Failure taxonomy

| Class | Examples | Detection | Handling |
| --- | --- | --- | --- |
| **Transient** | OTLP 429/5xx, Datadog timeout, Kafka lag, BSP export timeout, Phoenix `RESOURCE_EXHAUSTED`, Langfuse 10 min delay (missing v4 header) | `otelcol_exporter_send_failed_spans`; queue/capacity; UI “missing traces” with metrics still moving | Full-jitter retries on **idempotent** export; circuit-break backend; **do not** retry the user |
| **Permanent** | 4xx auth, LangSmith 25k cap (further runs rejected), Honeycomb 2,000-field / 64 KB string reject, Phoenix 4 MB gRPC, Tempo max_bytes_per_trace | Non-retryable; entire export fail | Truncate/blob-off; split mega-traces into a **thread** of traces; fix port 6006 vs 4318; **do not** retry a 4 MB payload expecting it to shrink |
| **Poison-pill high-cardinality labels** | `span_name = GET /users/123` or `chat {user_prompt_hash}`; `user.id` / `session.id` / `gen_ai.conversation.id` / tool call ids as Prometheus labels; `ml_app` per-customer | Tempo cardinality; HLL overflow `__cardinality_overflow__` (~3.25% SE); Datadog tag truncation | DRAIN `span_name_sanitization`; `max_cardinality_per_label`; keep high-card on **traces**, closed tag set on **metrics**; allowlisted `execute_tool {name}` |
| **Poison-pill content** | `Authorization` inside `llm.invocation_parameters`; screenshots; Gateway covers prompts but **not tool args / system / developer prompts**; Tempo MCP `get-trace` dumping prompts to an LLM | DLP findings; public-share hits | Hide/blob; treat Tempo MCP client as a **privileged identity**; disable public sharing |
| **Idempotency of exporters** | Dual BatchSpanProcessors of the **same** instrumentation are OK (Phoenix pattern); dual **SDKs** are not; WAL replay without idempotency keys double-counts tokens | Duplicate trees; doubled `token.usage` | One TracerProvider; exporter idempotency key = `(trace_id, span_id, export_epoch)`; Langfuse create+update = one span |
| **Sampling bias** | Head 1% of successful chats deletes rare tool-failures; `http.status_code` without `finish_reasons` under-keeps `content_filter`; error-only keep hides cost blowups | Jailbreak missing; cost dashboards green | Tail OTTL; record `sample_rate`; Honeycomb Usage mode is **unweighted** |

#### 4.3 Circuit breaker (closed → open → half-open)

Independent breakers: **trace backend** (Datadog/LangSmith/Tempo), **content blob store**, **metrics backend**. A Datadog timeout must **not** block the user (**bulkhead**). A blob 5xx must not block metadata traces.

```
        failures ≥ threshold or error-rate window
  ┌──────────┐  ─────────────────────────────────►  ┌──────────┐
  │  CLOSED  │                                       │   OPEN   │
  │ pass all │  success resets consecutive count     │ fail fast│
  └────┬─────┘                                       └────┬─────┘
       ▲                                                  │ cooldown elapsed
       │ trial success                                    ▼
       │                                            ┌──────────┐
       └──────────── trial OK ──────────────────────│ HALF-OPEN│
                    trial fail → OPEN               │ 1 probe  │
                                                    └──────────┘
```

**Thresholds [policy, not vendor SLO]:** trip trace-export on 5xx/timeout/429 sustained; trip blob-store independently; trip metrics only if the metrics pipe itself fails (rare — keep it boring). Cooldown tens of seconds. One probe in half-open.

**Fallback chain (cited policy):** **full content traces → redacted / pointer-only traces → metrics-only (drop traces) → local disk buffer.** Never the reverse on a privacy path (do not “fail open” into full prompts). Never fail-open Langfuse self-hosted EE masking (default callback is **fail-open** — set `FAIL_CLOSED=true` so a down callback **drops** rather than persisting plaintext). LangSmith Gateway scanner failure is **fail-close**.

#### 4.4 Zero-Trust MCP, tool RBAC, PII pipeline, two tapes

**Zero-Trust for observability** means the trace backend is **not** implicitly trusted with plaintext PII. CoSAI: log all agent/tool/prompt/model interactions; OTel for linkability; **immutable records of actions and authorizations**; do not pass user OAuth tokens through (token exchange); treat MCP returned content as untrusted. “Insufficient Observability” is **T12** of **12** core threat categories. Follow-on analysis: the whitepaper recommends immutable records, but **standardized audit logging across MCP does not yet exist** — sampled Tempo does not fill that gap.

Separate:

- **Metadata traces** (always): model, tokens, latency, tool name, policy decision, error class.
- **Content** (break-glass): encrypted blob, short TTL, just-in-time access, ticketed.
- **Audit of observability**: who exported / viewed a trace (not the agent’s tool audit).

**Tool-level RBAC (least privilege):**

| Tool / role | Who | Must not |
| --- | --- | --- |
| `execute_tool {allowlisted}` | Agent, identity from token | Omnibus `search(collection)` / model-filled `tenant_id` |
| MCP `tools/call` | Same, `_meta` from propagator | DNS-prefixed `traceparent`; protocol Logging as a third prompt dump |
| Tempo MCP (`/api/mcp`: `get-trace`, `traceql-search`, …) | Privileged assistant identity | Run as an unredacted LLM over prod traces (Grafana’s own warning) |
| LangSmith **Viewer** | SRE | See raw prompts |
| **Debugger** | On-call | Raise retention on a HIPAA project without a ticket (`projects:increase-trace-tier` is **independent** of `projects:update`) |
| **Privacy / legal** | Break-glass blob | Live in the same role as Viewer |
| `delete_runs` / dataset `hard_delete` | Compliance | Treat HTTP 200 as gone (it is **queued**) |

LangSmith workspace RBAC is **Enterprise**; Plus/Developer default all users to Admin. Operator can view audit logs, cannot manage billing/SSO/Org Admins. Langfuse: org RBAC on paid Cloud; **project-level RBAC** and Ent SSO on Pro **Teams add-on ($300/mo)** or Enterprise. Audit logs: **Enterprise only**.

**PII pipeline — detect → redact → audit — BEFORE export.** Token-level redaction recall is never 100%; architect as if regex will miss. Dual-write of full prompts to logs *and* spans doubles the PII store.

1. **Detection (regex + NER/classifier).** Scan span attrs that the spec marks sensitive (`gen_ai.input/output.messages`, `system_instructions`, `tool.call.arguments/result`, `prompt.variable`), OpenInference `input.value` / flattened messages / `llm.invocation_parameters`, retrieval documents, screenshots (`OPENINFERENCE_HIDE_INPUT_IMAGES`, `BASE64_IMAGE_MAX_LENGTH`), and eval datasets about to be promoted (indefinite on LangSmith). Regex: email, US SSN, US phones, PANs. NER: Presidio / Comprehend names, locations, NRP (LangSmith Gateway). LangSmith `create_anonymizer` is **skipped** if `LANGSMITH_HIDE_INPUTS/OUTPUTS` is true; `process_buffered_run_ops` batches expensive NER (`run_ops_buffer_size` counts operations, ~2 per traced call). Langfuse `mask` = legacy SDK attrs only; `mask_otel_spans` (2026-06-16) = export-stage including third-party OTel — **only this client’s exporter**; a second processor to Tempo still sees plaintext — mask at the **Collector** if you fan out. Keep the hook **fast** (batch worker thread); Langfuse EE callback docs: **< 100 ms**, timeout default **500 ms**, colocate sidecar. Datadog SDS scans **in Datadog’s backend** (extra SKU) — still redact at source. Gateway **does not cover** model responses (streaming redaction “in progress”), traces that **bypass** the gateway, system/developer prompts, or **tool-call arguments**. If the classifier is down: **fail closed on content export** (still serve the user; still emit metrics + redacted metadata).

2. **Redaction.** Replace with stable tokens (`[EMAIL_<hash12>]`, Gateway `[SAFE_TO_USE:<TYPE>_<8 char>]`). Put the essay on a blob; put policy decision, tool name + call id, model request vs response id, checkpoint id, prompt version on **low-cardinality span attrs and the action audit**. Cap string length; drop embedding vectors (`OPENINFERENCE_HIDE_EMBEDDING_VECTORS`); collector **allowlist** of `gen_ai.*` metadata attrs; HMAC identifiers. Application hide flags that run **after** `on_end` cannot mutate some SDK `ReadableSpan` implementations — redact at start or in the processor. Per-request LangSmith `tracing_context` replica `updates`: always set `project_name`; if it matches the active session project, `updates` **may be dropped** and unredacted I/O is sent. Zero-retention tenants: **disable tracing** for that request — masking still creates a run row. Langfuse EE server-side mask applies **only** to `/api/public/otel` (not legacy `/ingestion`); scrubbing is **async**: events land in **blob storage unmasked** before the worker callback; default `FAIL_CLOSED=false`.

3. **Audit trail (WORM).** Immutable log of detect/redact **decisions**, not values: `content_sha256` pre- and post-redact, entity **types** + counts, action (`tokenize`/`strip`/`block-from-export`/`none`), detector (`regex`|`presidio`|`comprehend`|`ner`), `trace_id`, `span_id`, `tenant_id`. Separate **agent action audit** (who/what-tool/args hash/policy/`trace_id`/`checkpoint_id`) from **platform audit** (who changed sampling, viewed/exported traces). LangSmith platform audit: Enterprise, OCSF **v1.7.0** API Activity class **6003**, retention **up to 400 days**, Org Admin or Operator. FAQ: primarily **write** ops; read ops (`query_run`, `query_trace`, …) introduced Helm `0.15.0-rc.1` — **verify** your build before claiming “who looked at this prompt.” Ship OCSF to SIEM; **do not** store platform audit in the same Postgres you would wipe for GDPR of traces.

**Two tapes:**

| Tape | Contents | Sampled? |
| --- | --- | --- |
| **Agent action audit** | Principal + agent id, tool, args **hash**, policy, `trace_id`, `checkpoint_id`, model req/resp ids | **Never** |
| **Platform audit** | Who changed sampling/retention/keys/SSO, viewed/exported traces | N/A — different object |

Sampled APM traces are **not** this tape. Tail sampling that keeps 1% of happy paths **cannot** prove a tool was never called. CoSAI immutable-audit is **not** satisfied by sampled Tempo alone.

---

### 5. Production Enterprise Code

Self-contained stdlib. Optional OTLP/Kafka wiring is commented. Run: `python observability_runtime.py`.

Wired: retries + full jitter, circuit breaker (closed → open → half-open) on the **trace backend**, fallback **full content → redacted → metrics-only → disk buffer**, PII detect→redact→audit **before** export, W3C `traceparent`, structured logs with correlation IDs, idempotent export keys, **user path never waits on Datadog**.

```python
#!/usr/bin/env python3
"""Telemetry-plane resilience: PII pipeline, export fallback, never block user.

Stdlib only. Swap Fake* ports for OTLP / Kafka / vendor HTTP.
# Optional: from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
"""
from __future__ import annotations

import hashlib, hmac, json, logging, random, re, tempfile, threading, time, uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for k, d in (("correlation_id", "-"), ("tenant_id", "-"),
                     ("traceparent", "-"), ("export_tier", "-")):
            setattr(record, k, getattr(record, k, d))
        return True

def configure_logging() -> logging.Logger:
    logger = logging.getLogger("obs")
    if logger.handlers:
        return logger
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter(
        '{"ts":"%(asctime)s","level":"%(levelname)s","cid":"%(correlation_id)s",'
        '"tenant":"%(tenant_id)s","traceparent":"%(traceparent)s",'
        '"tier":"%(export_tier)s","msg":"%(message)s"}'
    ))
    h.addFilter(CorrelationFilter())
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    return logger

LOG = configure_logging()

def slog(level: int, msg: str, *, cid: str, tenant: str,
         traceparent: str = "-", tier: str = "-", **fields: object) -> None:
    extra = {"correlation_id": cid, "tenant_id": tenant,
             "traceparent": traceparent, "export_tier": tier}
    LOG.log(level, "%s %s", msg, json.dumps(fields, default=str), extra=extra)

class TransientError(Exception):
    """429, 5xx, timeout, circuit open — retry idempotent export."""

class PermanentError(Exception):
    """4xx auth, 25k cap, 4 MB gRPC, 2000-field reject — do not retry."""

def retry_with_jitter(
    fn: Callable[[], object], *, cid: str, tenant: str, traceparent: str,
    op: str, attempts: int = 4, base_s: float = 0.05, cap_s: float = 1.0,
) -> object:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except PermanentError:
            raise
        except TransientError as exc:
            last = exc
            if i == attempts - 1:
                break
            sleep = random.uniform(0, min(cap_s, base_s * (2**i)))
            slog(logging.WARNING, "retry", cid=cid, tenant=tenant,
                 traceparent=traceparent, op=op, attempt=i + 1,
                 sleep_s=round(sleep, 3), err=str(exc))
            time.sleep(sleep)
    assert last is not None
    raise last

class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitOpenError(TransientError):
    pass

@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    cooldown_s: float = 15.0
    half_open_probes: int = 1
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _probes_used: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._state is CircuitState.OPEN:
                if now - self._opened_at >= self.cooldown_s:
                    self._state = CircuitState.HALF_OPEN
                    self._probes_used = 0
                else:
                    raise CircuitOpenError(f"circuit_open:{self.name}")
            if self._state is CircuitState.HALF_OPEN:
                if self._probes_used >= self.half_open_probes:
                    raise CircuitOpenError(f"circuit_half_open_busy:{self.name}")
                self._probes_used += 1

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = CircuitState.CLOSED
            self._probes_used = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()

def new_traceparent(sampled: bool = False) -> tuple[str, str, str]:
    """W3C trace-context-1. Flag 01 is a HEAD hint — tail decides later."""
    tid, sid = uuid.uuid4().hex, uuid.uuid4().hex[:16]
    return f"00-{tid}-{sid}-{'01' if sampled else '00'}", tid, sid

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9.-]+")
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
PHONE_RE = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")

@dataclass
class RedactionResult:
    text: str
    types: dict[str, int]
    pre_sha: str
    post_sha: str
    @property
    def hit(self) -> bool:
        return bool(self.types)

class PiiPipeline:
    """Detect → redact → audit. Never logs raw values."""

    def __init__(self, audit: "AuditSink") -> None:
        self.audit = audit

    def redact(self, text: str) -> RedactionResult:
        pre = hashlib.sha256(text.encode()).hexdigest()
        types = {n: len(rx.findall(text)) for n, rx in
                 (("EMAIL", EMAIL_RE), ("SSN", SSN_RE), ("PHONE", PHONE_RE))}
        types = {k: v for k, v in types.items() if v}

        def tok(prefix: str, m: re.Match[str]) -> str:
            return f"[{prefix}_{hashlib.sha256(m.group(0).encode()).hexdigest()[:12]}]"

        out = EMAIL_RE.sub(lambda m: tok("EMAIL", m), text)
        out = SSN_RE.sub(lambda m: tok("SSN", m), out)
        out = PHONE_RE.sub(lambda m: tok("PHONE", m), out)
        return RedactionResult(out, types, pre, hashlib.sha256(out.encode()).hexdigest())

    def apply(self, text: str, **meta: str) -> RedactionResult:
        result = self.redact(text)
        self.audit.write({
            "type": "pii_decision", "ts": time.time(), **meta,
            "pre_sha": result.pre_sha, "post_sha": result.post_sha,
            "types": result.types, "action": "tokenize" if result.hit else "none",
            "detector": "regex",
        })
        return result

@dataclass
class SpanRec:
    span_id: str
    parent_id: str
    name: str
    kind: str
    status: str
    attrs: dict
    content: dict

@dataclass
class AgentTurn:
    tenant: str
    cid: str
    traceparent: str
    trace_id: str
    spans: list[SpanRec]
    tool_calls: list[dict]
    allow_content: bool
    finish_reason: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    ttft_ms: int
    cost_usd: float
    conversation_id: str
    checkpoint_id: str
    error_type: str | None = None

class AuditSink:
    """Layer 3: never sampled. Tool facts + PII decisions, not prompt bodies."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, rec: dict) -> None:
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            fh.flush()

    def tool_action(self, turn: AgentTurn, tool: dict) -> None:
        args = json.dumps(tool.get("arguments", {}), sort_keys=True)
        self.write({
            "type": "agent_action", "ts": time.time(), "tenant": turn.tenant,
            "principal": turn.cid, "trace_id": turn.trace_id,
            "checkpoint_id": turn.checkpoint_id, "tool": tool["name"],
            "call_id": tool.get("call_id", ""),
            "args_sha256": hashlib.sha256(args.encode()).hexdigest(),
            "policy": tool.get("policy", "allow"),
            "is_error": bool(tool.get("is_error")),
        })

class FakeTraceBackend:
    def __init__(self) -> None:
        self.trees: list[dict] = []
        self.fail_n: int = 0
        self.permanent: bool = False

    def export(self, tree: dict) -> None:
        if self.permanent:
            raise PermanentError("payload_rejected")
        if self.fail_n > 0:
            self.fail_n -= 1
            raise TransientError("datadog_timeout")
        self.trees.append(tree)

@dataclass
class ExportResult:
    user_ms: int
    tier: str
    exported: bool
    reason: str

class TelemetryRuntime:
    """OTLP-once: metrics 100%; traces fall back; audit always. User path does not wait."""

    def __init__(self, hmac_key: bytes, buffer_dir: Path, audit_path: Path) -> None:
        self.hmac_key = hmac_key
        self.metrics: list[dict] = []
        self.audit = AuditSink(audit_path)
        self.pii = PiiPipeline(self.audit)
        self.buffer_dir = buffer_dir
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        self.backend = FakeTraceBackend()
        self.breaker = CircuitBreaker("traces")
        self.seen: set[str] = set()

    def hash_user(self, user_id: str) -> str:
        return hmac.new(self.hmac_key, user_id.encode(), hashlib.sha256).hexdigest()[:16]

    def _idem(self, turn: AgentTurn, tier: str) -> str:
        return hashlib.sha256(f"{turn.trace_id}:{tier}".encode()).hexdigest()[:24]

    def _tree(self, turn: AgentTurn, spans: list[SpanRec], tier: str) -> dict:
        return {
            "trace_id": turn.trace_id, "traceparent": turn.traceparent,
            "conversation_id": turn.conversation_id, "tier": tier,
            "idem": self._idem(turn, tier),
            "spans": [{"span_id": s.span_id, "parent_id": s.parent_id, "name": s.name,
                       "kind": s.kind, "status": s.status, "attrs": s.attrs,
                       "content": s.content} for s in spans],
        }

    def _export_otlp(self, tree: dict, *, cid: str, tenant: str, tp: str) -> None:
        def _send() -> None:
            self.breaker.allow()
            try:
                self.backend.export(tree)
            except PermanentError:
                self.breaker.record_success()
                raise
            except TransientError:
                self.breaker.record_failure()
                raise
            self.breaker.record_success()
        retry_with_jitter(_send, cid=cid, tenant=tenant, traceparent=tp, op="otlp_export")

    def _redact_spans(self, turn: AgentTurn) -> list[SpanRec]:
        out: list[SpanRec] = []
        for s in turn.spans:
            content, attrs = {}, dict(s.attrs)
            for key, val in s.content.items():
                if not isinstance(val, str):
                    content[key] = val
                    continue
                red = self.pii.apply(
                    val, cid=turn.cid, tenant=turn.tenant,
                    trace_id=turn.trace_id, span_id=s.span_id,
                )
                if turn.allow_content and not red.hit:
                    content[key] = val
                else:
                    attrs[f"{key}.ref"] = red.post_sha
                    if red.hit:
                        attrs["pii_redacted"] = True
            out.append(SpanRec(s.span_id, s.parent_id, s.name, s.kind, s.status, attrs, content))
        return out

    def export_turn(self, turn: AgentTurn) -> ExportResult:
        t0 = time.monotonic()
        self.metrics.append({
            "tenant": turn.tenant, "finish_reason": turn.finish_reason,
            "tokens_in": turn.tokens_in, "tokens_out": turn.tokens_out,
            "latency_ms": turn.latency_ms, "ttft_ms": turn.ttft_ms,
            "cost_usd": turn.cost_usd, "error_type": turn.error_type or "",
            "ok": turn.error_type is None and turn.finish_reason not in {"length", "content_filter"},
        })
        for tool in turn.tool_calls:
            self.audit.tool_action(turn, tool)

        prepared = self._redact_spans(turn)
        pii_hit = any(s.attrs.get("pii_redacted") for s in prepared)
        candidates: list[tuple[str, list[SpanRec]]] = []
        if turn.allow_content and not pii_hit and any(s.content for s in prepared):
            candidates.append(("full", prepared))
        candidates.append(("redacted", [
            SpanRec(s.span_id, s.parent_id, s.name, s.kind, s.status, s.attrs, {})
            for s in prepared
        ]))

        last_err = "none"
        for tier, spans in candidates:
            idem = self._idem(turn, tier)
            if idem in self.seen:
                return ExportResult(int((time.monotonic() - t0) * 1000), tier, True, "idempotent_replay")
            tree = self._tree(turn, spans, tier)
            try:
                self._export_otlp(tree, cid=turn.cid, tenant=turn.tenant, tp=turn.traceparent)
                self.seen.add(idem)
                slog(logging.INFO, "export_ok", cid=turn.cid, tenant=turn.tenant,
                     traceparent=turn.traceparent, tier=tier, spans=len(spans))
                return ExportResult(int((time.monotonic() - t0) * 1000), tier, True, "ok")
            except PermanentError as exc:
                last_err = str(exc)
                slog(logging.ERROR, "export_permanent", cid=turn.cid, tenant=turn.tenant,
                     traceparent=turn.traceparent, tier=tier, err=last_err)
                break
            except TransientError as exc:
                last_err = str(exc)
                slog(logging.WARNING, "export_degraded", cid=turn.cid, tenant=turn.tenant,
                     traceparent=turn.traceparent, tier=tier, err=last_err)

        (self.buffer_dir / f"{self._idem(turn, 'metrics')}.json").write_text(
            json.dumps(self._tree(turn, [], "metrics"), sort_keys=True), encoding="utf-8"
        )
        slog(logging.ERROR, "metrics_only", cid=turn.cid, tenant=turn.tenant,
             traceparent=turn.traceparent, tier="metrics", err=last_err)
        return ExportResult(int((time.monotonic() - t0) * 1000), "metrics", False, last_err)

def demo_turn(rt: TelemetryRuntime, *, allow_content: bool, prompt: str) -> AgentTurn:
    tp, tid, rid = new_traceparent(sampled=False)
    llm_id, tool_id = uuid.uuid4().hex[:16], uuid.uuid4().hex[:16]
    return AgentTurn(
        tenant="acme", cid="corr-1", traceparent=tp, trace_id=tid,
        conversation_id="conv-9", checkpoint_id="ckpt-3",
        allow_content=allow_content, finish_reason="stop",
        tokens_in=800, tokens_out=120, latency_ms=2400, ttft_ms=640, cost_usd=0.004,
        tool_calls=[{"name": "refund_lookup", "call_id": "call-7",
                     "arguments": {"ticket_id": "T-9"},
                     "policy": "allow", "is_error": False}],
        spans=[
            SpanRec(rid, "", "invoke_agent support", "AGENT", "OK",
                    {"gen_ai.agent.name": "support", "gen_ai.conversation.id": "conv-9"}, {}),
            SpanRec(tool_id, rid, "execute_tool refund_lookup", "TOOL", "OK",
                    {"gen_ai.tool.name": "refund_lookup"},
                    {"arguments": json.dumps({"ticket_id": "T-9"})}),
            SpanRec(llm_id, rid, "chat demo-chat", "LLM", "OK",
                    {"gen_ai.operation.name": "chat", "gen_ai.provider.name": "demo",
                     "gen_ai.request.model": "demo-chat",
                     "user.hash": rt.hash_user("u-44")},
                    {"input": prompt, "output": "Refund window is 30 days."}),
        ],
    )

def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="obs_runtime_"))
    rt = TelemetryRuntime(b"server-secret-not-in-trace", root / "buf", root / "audit.ndjson")
    print("clean", rt.export_turn(demo_turn(rt, allow_content=True, prompt="What is the refund window?")))
    pii = demo_turn(rt, allow_content=True, prompt="Email ada@example.com SSN 123-45-6789")
    print("pii", rt.export_turn(pii))
    rt.backend.fail_n = 100
    rt.breaker.failure_threshold = 1
    down = demo_turn(rt, allow_content=False, prompt="status?")
    print("backend_down", rt.export_turn(down))
    print("metrics_rows", len(rt.metrics), "trees", len(rt.backend.trees),
          "audit_bytes", (root / "audit.ndjson").stat().st_size)
    print("user_path_never_blocked")

if __name__ == "__main__":
    main()
```

Graceful degradation contract the snippet enforces: metrics + WORM audit always; traces try full then redacted; Datadog-class timeout trips the breaker and falls to **metrics-only + disk buffer**; the function returns without raising to the user handler. HMAC `user.id` never becomes a metrics label. `traceparent` flag `00` until a **tail** policy would rewrite `tracestate` (not done in-process).

---

### 6. Architectural System Design Scenarios

#### Scenario A — Multi-tenant agent: content-off traces + WORM audit

**Problem.** SaaS agent serving many tenants. Tenant A must not see tenant B’s prompts. SRE needs metadata SLOs (TTFT, `$ / successful task`, error class). Legal needs **7-year** proof that tool X ran with policy Y. Online eval on raw prod prompts is a future ask, not a day-one requirement. Public LangSmith sharing must never be on.

**Proposed architecture:**

```
  ┌─────────────┐   ┌─────────────────────────────────────────────────────┐
  │ Tenant PEP  │──▶│ Agent runtime  OTel GenAI NO_CONTENT + W3C          │
  │ HMAC user   │   │ MCP _meta.traceparent (unprefixed)                  │
  └─────────────┘   └──────────────────────┬──────────────────────────────┘
                                           ▼
                    ┌──────────────────────────────────────────────────────┐
                    │ Edge collector  memory_limiter first                 │
                    │ transform: DROP gen_ai.input/output.messages,        │
                    │   tool.call.arguments UNLESS tenant.allow_content    │
                    │ allowlist gen_ai.* metadata; HMAC already applied    │
                    └───────────┬──────────────┬───────────────┬───────────┘
                                │              │               │
                    ┌───────────▼────────┐ ┌───▼─────┐ ┌───────▼──────────┐
                    │ Metrics 100%       │ │ Tail    │ │ WORM Kafka/S3    │
                    │ spanmetrics        │ │ sampled │ │ action audit     │
                    │ NO tenant_id label │ │ Tempo   │ │ object-lock 7y   │
                    │ if thousands of    │ │ VPC     │ │ args hash        │
                    │ tenants (or cap    │ │ blob URI│ │ never sampled    │
                    │ max_cardinality)   │ │ break-  │ │ keyed (tenant,   │
                    │                    │ │ glass   │ │  trace, tool,    │
                    └────────────────────┘ └─────────┘ │  call_id)        │
                                                       └──────────────────┘
                    RBAC: Viewer=metadata · Debugger=redacted · Privacy=blob
                    LangSmith/Langfuse: workspace/project = tenant boundary
                    Disable Public Sharing org-wide
```

**Technology choices:** OTel GenAI + W3C once. Content default off; encrypted bucket + `gen_ai.*.ref` for break-glass tenants (`tenant.allow_content=true`). Tail keep ERROR / HITL / high-$ / 1% happy. Tempo (or LangSmith Hybrid **only if the data plane is yours**). Metrics-generator **must not** include raw `tenant_id` at thousands of tenants — cap with `max_cardinality_per_label` or hash to a low-card enum. Judges, if added later, receive **already-redacted** text or you have exported PII to a second model vendor (evals module).

**Trade-off matrix:**

| Axis | **A1 Content-off traces + WORM audit + blob-by-pointer (recommended)** | **A2 SaaS LangSmith/Langfuse content-on + vendor retention** | **A3 Metrics-only, no traces, audit in app logs** |
| --- | --- | --- | --- |
| **Cost** | S3 cheap; Tempo query + engineer time dominate. `$/1k` is infra **[inferred, env-specific]** — do not quote SaaS SKUs as TCO | Plus **[inferred] ~$84–$134/mo** at 100k turns (0.05¢ line) + 5 GB/h 429 risk; Langfuse Core **~$85** at 8 units/trace | Cheapest ingest; you will re-instrument under the first incident |
| **Latency** | User p99 unchanged (async export). Tail UI freshness **~32,600 ms p50 [inferred]** at default `decision_wait` | Vendor UI seconds–minutes (Langfuse missing v4 header **600,000 ms**) | None |
| **Ops complexity** | Collector two-tier + Kafka sticky + PII processor + two tapes | Lowest until Hybrid/RBAC/purge (weekend batch, 1000/request) | Lowest until legal asks “prove the tool ran” |
| **Security posture** | Tenant isolation on HMAC + collector drop; WORM is the CoSAI tape; Viewer cannot raise retention | Vendor is a subprocessor; public share is unauthenticated; Gateway misses **tool args** | App logs become a second PII store; sampled nowhere, retention nowhere |
| **Scalability ceiling** | Tail memory binds on content-on; content-off stays in the 2.5 GB-class window **[inferred §2.6]** | Hourly 5 GB / 25k runs/trace / auto-extend | Metrics cardinality if you cheat labels |

**Decision.** **A1 wins** for multi-tenant + legal proof: content-off traces for SRE, WORM for counsel, blobs for break-glass, collector as the last choke point before any vendor disk. **A2 wins** only if residency allows a subprocessor and you hide/anonymize **and** disable public sharing **and** opt out of online-eval auto-extend. **A3 fails** the design review: logs-as-audit without WORM/hashing cannot prove a tool was never called, and you still have a PII store.

#### Scenario B — MCP-heavy fleet: tail sampling vs LangSmith vs self-hosted Phoenix

**Problem.** Host → MCP client → many servers (stdio + HTTP) → downstream SQL/SaaS. LangChain/Graph team wants Messages view. Security wants prompts in-VPC. Finance wants a cap. GenAI is **the** product (not a small fraction of an APM estate) — Honeycomb “keep 100% of `gen_ai.conversation.id`” would be 100% of EPM.

**Proposed architecture (recommended hybrid):**

```
  ┌─────────────────────────────────────────────────────────────────────────┐
  │ Host + sidecar collector (stdio MCP children inherit OTLP endpoint)     │
  │ SEP-414 _meta on every JSON-RPC · map isError → span status ERROR       │
  │ Nest MCP tools/call under execute_tool (enrich, do not duplicate)       │
  └───────────────────────────────┬─────────────────────────────────────────┘
                                  ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │ Kafka partition_traces_by_id=true                                       │
  │ Tail sampler  decision_wait ≥ max(MCP tool timeout, HITL, product p99)  │
  │ ottl keep mcp.method.name==tools/call AND status ERROR                  │
  │ Headless Service + loadbalancing routing_key=traceID                    │
  └─────────────┬─────────────────────────────┬─────────────────────────────┘
                │                             │
     ┌──────────▼──────────┐       ┌──────────▼──────────┐
     │ Tempo + Grafana     │       │ 1% redacted replica │
     │ spanmetrics SLOs    │       │ → LangSmith (base   │
     │ DRAIN span names    │       │   retention, hide   │
     │ 90-day ops          │       │   I/O) Messages UI  │
     └─────────────────────┘       │ Engine OFF          │
                                   └─────────────────────┘
     Phoenix self-host alternative: OpenInference hide flags,
     PHOENIX_MAX_SPANS_QUEUE_SIZE = peak spans/s × export RTT
```

**Technology choices:** `decision_wait` ≥ product p99 e2e (90 s class ⇒ **not** Adaptive’s 2 s-after-root). Do not run Grafana Adaptive as the only sampler for long MCP tools. LangSmith: default **base** retention, opt **out** of online-eval auto-upgrade, `LANGSMITH_HIDE_INPUTS/OUTPUTS` in prod, workspace spend limits, Engine off unless someone owns the LCU budget (**[inferred] ~$2,700/mo** mid-range). Phoenix: `TraceConfig` hide; you own authn/z, backups, PII. Pair Phoenix with Tempo if you also need classic APM.

**Trade-off matrix:**

| Axis | **B1 Collector tail + Tempo (100% metrics) + 1% redacted LangSmith replica (recommended)** | **B2 LangSmith Cloud content-on as system of record** | **B3 Self-hosted Phoenix only (OpenInference)** |
| --- | --- | --- | --- |
| **Cost** | Tempo GB + Plus seat + **1%** of $0.50/1k ≈ pennies on traces **[inferred]**; no auto-extend | 100k turns **[inferred] ~$84** base or **~$134** with 10% extend; 5 GB/h is the real cap | No per-trace SKU; ClickHouse/Postgres/disk + an engineer |
| **Latency** | User: 0 ms tax. Trace UI: **~32,600 ms p50 [inferred]** wait. LangSmith replica extra hop | Vendor ingest; Plus 429s look like tool flakes | In-process queue 20k; exporter timeout must be **<** BSP timeout |
| **Ops complexity** | Two-tier collectors + Kafka + hide flags + Engine discipline | Lowest until Hybrid, weekend purge, 25k cap, public-share | You build tail sample, RBAC, backups, ATO |
| **Security posture** | Prompts in VPC Tempo; LangSmith sees redacted 1%; WORM still required | Vendor holds prompts unless Hybrid; Gateway ≠ tool args; share-link is public | Residency yours; `ReadableSpan` on_end cannot always mutate — redact earlier |
| **Scalability ceiling** | Sticky `traceID` + `num_traces` ≥ \(\lambda W\); stdio sidecar | 25k runs/trace; split long agents into a **thread** of traces | gRPC 4 MB; HTTP **6006** not 4318; queue OOM if you raise it instead of hiding vectors |

**Decision.** **B1 wins** when MCP trees are wide/slow and GenAI is the estate: tail-sample in **your** collector (wait ≥ tool p99), keep metrics unbiased, give the LangChain team a **redacted** Messages replica at base retention, Engine off. **B2 wins** for a LangGraph shop shipping this quarter with no VPC mandate — hide in prod, disable public sharing, opt out of auto-extend, still emit OTLP once via Collector. **B3 wins** for residency/cost-at-scale when someone **owns** collectors; it is not “free LangSmith.” Honeycomb-only is a fourth cousin: cheap per event until wide traces × $3/M, Pro **2 SLO** cap, keep-100%-GenAI guidance **inverted** when GenAI is the product.

**Board-level decision rule:** Instrument to **OTel GenAI + W3C** once. Treat OpenInference and Datadog span kinds as exporters/UI mappings. Put **policy and $** on metrics (100%). Put **forensics** on traces (sampled, redacted). Put **legal proof** on an unsampled immutable action log keyed by `trace_id`. If a design uses one system for all three, it will fail at least one of cost, privacy, or completeness.

---

## Common Failure Modes

| Failure | Cause | Detection | Mitigation |
| --- | --- | --- | --- |
| **Broken tree / missing child** | Head sample on MCP; no sticky `traceID`; ClusterIP VIP; `decision_wait` < tool/HITL; 25k cap; 4 MB gRPC; dual SDKs; Phoenix on 4318; stdio MCP without OTLP endpoint | Partial UI; metrics still 100% | Two-tier + headless + Kafka by trace ID; wait ≥ p99 e2e; sidecar collector; fan-out once |
| **Partial tree sampled as OK** | `decision_wait=30 s` vs 90 s agent; Adaptive **2 s after root** | Root OK, MCP children absent | Wait for root **end**; late-span decision cache ≫ `num_traces` |
| **MCP isError lie** | JSON-RPC 200 + `isError: true` | RED 100% healthy; agent looping | Map `isError` → span status ERROR **before** tail sample |
| **Cardinality bomb** | `span_name` with ids; `user.id` as metric label; OpenInference flattening | Tempo HLL overflow; DD tag truncate | DRAIN sanitization; closed tag set; high-card on traces only |
| **PII in traces** | Content capture copied staging→prod; tool args; Gateway gap; public share; Tempo MCP to an LLM; dataset promote | DLP; unauthenticated share hits | Detect→redact→audit **before export**; disable public sharing; hide images; fail-closed on content |
| **Sampling bias** | Head 1% successes; error-only keep; FieldList too unique | Missing jailbreaks; cost dashboards lie | Tail OTTL; record `sample_rate`; do not `count()` unweighted |
| **RED metrics go to zero** | Metrics slack **30 s** + tail wait **30 s** | Dashboards empty during the incident | Metrics connector **before** sampler; widen slack knowing granularity coarsens |
| **429 as “flaky tools”** | LangSmith 5 GB/h content-on; Plus ALB 5k/min | Missing **updates** (create without completion) | Hide content; sample; raise plan |
| **Auto-extend invoice** | Online eval / thread rule upgrades entire traces | 14 d project → 400 d bill | Opt out per evaluator; base default; Engine off |
| **Replay “fixed” a flake** | LangGraph replay re-calls the model | New tree ≠ old tree | Recorded span I/O + checkpoint + WORM; Replay is “what if” |
| **Honeycomb 1-in-10** | 2nd overage month throttle | Random missing children | Treat as incident; sample at Refinery **before** ingest |
| **Messages view hid the guardrail** | `middleware` filtered; `LS_MESSAGE_VIEW_EXCLUDE` presence | Trajectory looks clean | Inspect the **tree**; set `ls_agent_type` correctly |

---

## Key Takeaways

- An agent trace is a **PII store that looks like APM**. Control (policy/RBAC/redaction) ≠ telemetry export ≠ content blobs.
- Three surfaces (trajectory / resource / evidence) and three layers (100% metrics / tail-sampled redacted traces / unsampled WORM audit). One product for all three fails cost, privacy, or completeness.
- **OTLP once**, Collector fan-out. Dual vendor SDKs duplicate trees. OpenInference is conventions on OTel’s wire, not a competing protocol. GenAI conventions are **Development**, content **off** by default.
- Agent default sampler is **tail**, sticky by `trace_id`, `decision_wait` ≥ product p99 e2e. Head sampling decides before the interesting bit exists. Adaptive’s **2 s after root** is not Collector **30 s**.
- **Replay ≠ audit.** Checkpoints resume; they re-execute. Legal proof is recorded I/O + never-sampled action hashes.
- Dollars follow the **meter**: LangSmith **tree**, Datadog **LLM span**, Honeycomb **event**, Langfuse **unit**, Grafana **GB**. Do not mix 0.05¢ with third-party $2.50/1k.
- PII is **detect → redact → audit before export**. Gateway does not cover tool args. Collector allowlist is the last chance before a vendor disk.
- Never block the user on a backend timeout: circuit-break export; fallback full → redacted → metrics-only → disk buffer.

---

## Interview Q&A

**Q1. Explain production agent observability to someone who only knows APM.**  
I treat the trace as a PII store that happens to look like APM. I split three surfaces — trajectory, resource usage, evidence — and three layers that do not share a sampling policy: 100% content-free metrics, tail-sampled redacted traces, and an unsampled WORM action audit. I never put prompts on Prometheus labels or sample the audit tape.

**Q2. Trace vs thread vs trajectory vs checkpoint — one sentence each.**  
Trace: nested spans for one invocation (who timed out). Thread: many traces sharing `conversation.id`. Trajectory: a projection — the flattened message/state path, not a store. Checkpoint: LangGraph state snapshot for resume; replay re-calls the model and is not audit truth.

**Q3. Why is head sampling wrong for agents?**  
Head decides at span start, before tools, `finish_reason`, or the 40-step loop. The interesting bit is only known at the tail. I wait `decision_wait` on one collector instance sticky-routed by `trace_id`, keep ERROR/`content_filter`/HITL/high latency, and use SDK head sample only if collectors are saturated.

**Q4. OTel GenAI vs OpenInference — competing?**  
No. OpenInference is span-kind conventions on OTel’s OTLP wire (`openinference.span.kind` ALL CAPS, flattened keys). OTel GenAI is Development, content off by default, four Python capture modes. I instrument once and treat Datadog/Langfuse kinds as mappings. Phoenix OTLP/HTTP is port **6006**, not 4318.

**Q5. Give me `$ per 1k` without mixing SKUs.**  
LangSmith documented 0.05¢ = **$0.50/1k traces** (extended **$5/1k**). Datadog is LLM-spans: **[inferred] $2.80/1k** eight-call requests at $3.50/10k overage. Honeycomb **[inferred] $0.075/1k** 25-span trees at $3/M events. Langfuse **[inferred] ~$0.64/1k** at 8 units and $8/100k overage. Grafana is GB, not traces. I will not mix the third-party $2.50/1k claim with the 0.05¢ invoice line.

**Q6. What p50/p95/p99 do you put in the contract?**  
Vendors do not publish agent e2e or ingest p99. I set **[inferred]** policy: chat TTFT **640 / 2,560 / 5,120 ms** on OTel histogram buckets; agent e2e **15,000 / 60,000 / 90,000 ms** with a wider root histogram (120 s overflows 81.92 s). Telemetry: metrics path **2,600 / 5,200 / 8,000 ms**; tail-complete traces **~32,600 / 35,200 / 38,000 ms** at default 30 s `decision_wait`. The user handler never waits on that.

**Q7. PII pipeline — walk detect → redact → audit.**  
Before export: regex + NER on spec-sensitive fields and tool args (Gateway misses those). Redact to tokens / blob pointers; HMAC user ids; collector allowlist. Audit WORM of **decisions** (pre/post hashes, entity types, counts) plus the agent action tape (tool + args hash). If NER is down I fail closed on **content**, not on the user. Fan-out masking at the Collector or Tempo still sees plaintext.

**Q8. MCP broke our trace tree. What did we miss?**  
Unprefixed `traceparent` in `params._meta` (SEP-414). Sidecar collector for stdio children. Sticky `traceID` through a **headless** Service, not ClusterIP, not NGINX. `decision_wait` ≥ tool timeout. Map `isError` to span ERROR. Do not DNS-prefix the W3C keys and do not duplicate `execute_tool` as a sibling of `tools/call`.

**Q9. On-call replayed the checkpoint and the bug vanished. Shipped?**  
No. Replay re-executes LLM/tools; temperature, provider routing, and non-idempotent POSTs diverge. I forensic off recorded span I/O + checkpoint bytes + the WORM tape. Messages view can hide `middleware` — I open the tree.

**Q10. Datadog is timing out. User p99 climbed. What did we do wrong?**  
We put the exporter on the request path. I circuit-break the trace backend (closed→open→half-open), fall back full→redacted→metrics-only→disk buffer, and keep `ml_obs.*` on the unsampled pipe. Product availability and observability availability are different SLOs.

**Q11. Why did RED dashboards go dark in the incident?**  
Grafana metrics-generator dropped spans older than the **30 s** slack while the tail sampler held the tree for **30 s**. I compute spanmetrics **before** sampling and I do not copy Adaptive’s 2 s-after-root into a 90 s MCP agent.

**Q12. Zero-Trust around trace tools — failure mode?**  
An omnibus `get-trace` / Tempo MCP / LangSmith public share that dumps prompts to an LLM or anyone with the link. I split Viewer vs Debugger vs Privacy, disable public sharing, ticket blob access, and I never take `tenant_id` from model JSON.

---

## Key Numbers to Memorize

### Wire / conventions / sampling
| Number | What |
| --- | --- |
| **32 members** | `tracestate` cap; move own entry left on parent-id change |
| **0 Stable** | GenAI-specific OTel span/event/metric/attr set (2026 write-ups); content **off** by default |
| **NO_CONTENT / SPAN_ONLY / EVENT_ONLY / SPAN_AND_EVENT** | Python capture modes; legacy `true` gone |
| **81.92 s** | Last suggested duration/TTFC histogram bucket; 120 s agent **overflows** |
| **30 s / 50,000 / 0 s** | `decision_wait` / `num_traces` / `decision_wait_after_root_received` defaults |
| **≈1,667 traces/s** | **[inferred]** default cap \(50\mathrm{k}/30\mathrm{s}\) |
| **2 s / 30 s** | Grafana Adaptive: after root / no root; volumetric GA **2026-07-22** |
| **5,000 / 2,048 / 512 / 30,000 ms** | BSP schedule / queue / batch / export timeout |
| **200 ms / 8,192** | Collector batch timeout / `send_batch_size` |
| **1,000 batches / 300 s** | Exporter sending_queue / retry `max_elapsed_time` |
| **80%** | `GOMEMLIMIT` vs container memory; `memory_limiter` **first** |
| **4 MB / 20,000 / 6006** | Phoenix gRPC max / queue default / OTLP HTTP port (**not** 4318) |
| **2,000 fields / 1 MB / 64 KB** | Honeycomb event caps |
| **25,000** | LangSmith max runs per trace |
| **14.4× / 6× / 1×** | 30 d 99.9% page 1 h / page 6 h / ticket 3 d burn rates; short = **1/12** long |
| **48 h / 2 SLOs** | Datadog max long window; Honeycomb Pro SLO cap |
| **−65% mem / ~2× CPU** | Elastic span-ingest + Pebble (their benches) |

### $ / meters / retention
| Number | What |
| --- | --- |
| **0.05¢ / 0.50¢** | LangSmith base / extended per **trace** (14 d / 400 d) |
| **$0.50 / $5.00 per 1k** | Same line, per 1k traces |
| **$39 / $1.50 / $1.00** | Plus seat; 1 LCU; 1 LSU |
| **[inferred] $7.50–$45; ~$2,700/mo** | Engine run 5–30 LCU; 4×/day × 30 d × 15 LCU |
| **$160 / 100k; $3.50/10k** | Datadog annual LLM-span package / overage; tools **free** |
| **$1.50 / $3 / $4 per 10k** | Datadog 30/60/90-day trace retention add-on |
| **$0.30/GB** | Datadog SDS annual |
| **[inferred] $0.35 / 1k LLM spans; $2.80 / 1k 8-call requests** | At $3.50/10k overage |
| **$3.00 / M events** | Honeycomb new Pro from 2026-07-01 (legacy $1.30; grace 2026-12-31) |
| **[inferred] $0.003 / 1k events; $0.075 / 1k 25-span traces** | At $3/M |
| **$8 / 100k units** | Langfuse first overage band; units = trace+observation+score |
| **$0.05 / $0.40 / $0.10 per GB** | Grafana process / write / extra 30 d retain; 50 GB allotment |
| **5 GB/h / 500k events/h** | LangSmith Plus ingest; **[inferred] 10 KB/event** headroom |
| **15 d / 60 d / 30–90 d / 3 y** | Datadog traces / Honeycomb events / Langfuse Core–Pro / Langfuse Pro data access |
| **$0.30 / $4** | SDS per GB vs 90 d add-on per 10k LLM spans; 3M spans **[inferred] $1,200/mo** extra |

### Latency / ingest / security (numeric ms)
| Number | What |
| --- | --- |
| **640 / 2,560 / 5,120 ms** | **[inferred]** inner chat TTFT p50/p95/p99 policy (OTel 0.64/2.56/5.12 s buckets) |
| **1,280 / 10,240 / 20,480 ms** | **[inferred]** inner chat e2e p50/p95/p99 policy |
| **15,000 / 60,000 / 90,000 ms** | **[inferred]** agent e2e p50/p95/p99 policy (10–120 s class) |
| **2,500 / 5,000 ms** | **[inferred]** BSP holdback p50/p95 at 5,000 ms schedule |
| **2,600 / 5,200 / 8,000 ms** | **[inferred]** metrics-path collector lag p50/p95/p99 |
| **32,600 / 35,200 / 38,000 ms** | **[inferred]** tail-complete trace freshness at default 30 s wait |
| **2,000 ms / 30,000 ms** | Adaptive after-root / no-root (and metrics slack) |
| **600,000 ms** | Langfuse OTel without v4 header (up to 10 min) |
| **5,000 ms** | Tempo example **read** `duration_slo` |
| **500 ms / <100 ms** | Langfuse EE mask callback timeout / recommended RTT |
| **1 of 10 / 72 h** | Honeycomb throttle accept rate / recovery under target |
| **1,000 traces/request; weekend** | LangSmith purge; HTTP 200 = queued |
| **T12 / 12** | CoSAI insufficient observability; no standardized MCP audit log yet |

---

*End of module. Practice the Q&A out loud; recode the breaker states from memory; recompute the $ per 1k mix on a whiteboard with the meter (tree vs LLM-span vs event vs GB) listed.*
