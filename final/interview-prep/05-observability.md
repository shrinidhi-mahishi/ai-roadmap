# Module 05: Observability for LLM and Agent Systems

## What Is This?

Traditional monitoring is like having a speedometer and fuel gauge in your car -- you know how fast you are going and how much gas you have. LLM observability is like needing those gauges plus a dashcam recording every turn, a GPS log of the full route, a fuel-cost-per-mile tracker, and a judge scoring whether you actually reached the right destination. An LLM agent can return HTTP 200 in 500ms with a perfectly formatted response that is completely wrong -- every gauge reads green while the car is at the wrong address. That is the core problem: correctness cannot be inferred from status codes or latency. An agent trace is a PII store that happens to look like APM, and treating it as "just Datadog" is how teams leak personal data, explode Prometheus cardinality, and sample away the only trees they later need.

---

## Part 1: System Topology and Data Flow

### Three Observability Surfaces

Most people collapse observability into "we have LangSmith." Production systems need three distinct surfaces:

| Surface | What You Actually Need | Not the Same As |
|---------|----------------------|-----------------|
| **Trajectory** | Steps, branches, retries, tool calls, handoffs, resume points, LangGraph super-steps | A chat transcript |
| **Resource** | Tokens, latency, cost, cache splits -- content-free histograms | A pretty trace UI |
| **Evidence / Provenance** | Tool outputs, retrieval hits, citations, policy decisions that justified the run | The final answer |

### Three Layers That Must Not Share a Sampling Policy

| Layer | Sampling | Content | Purpose |
|-------|----------|---------|---------|
| Always-on **metrics** | **100%** | Content-free | SLOs, token burn, cost per successful task |
| Sampled/redacted **traces** | Tail (agent default); head only as last-ditch | Opt-in; hide in prod | Debug, trajectory UI, sampled online eval |
| Unsampled immutable **action audit** | **Never** sampled | Args hash (or redacted copy), not full prompt | Legal proof that a tool ran |

**Replay is not audit.** LangGraph replay from a `checkpoint_id` re-executes nodes -- LLM calls, tools, and interrupts fire again and may return different results. A checkpoint is a debugger. The audit tape is recorded span I/O plus an unsampled action log keyed by `trace_id`.

### End-to-End Architecture

```
                         TELEMETRY / OBSERVABILITY SINKS
         +------------------------------------------------------------------+
         |  L1 METRICS 100%     L2 TAIL-SAMPLED TRACES     L3 NEVER-SAMPLED |
         |  spanmetrics /       Tempo / Honeycomb /        WORM action audit|
         |  ml_obs.* (15 mo)    LangSmith / Phoenix        (args hash)      |
         |  RED / tokens / $    Datadog LLM-obs / Langfuse platform who-view|
         |  content-free        redacted; blob URI on span SIEM / obj-lock  |
         +----------^---------------------^------------------^--------------+
                    |                     |                  |
                    | metrics connector   | sampled trees    | unsampled
                    | (BEFORE tail drop)  |                  | audit events
+-------------------+---------------------+------------------+--------------+
| CONTROL PLANE  (sampling, redaction, RBAC, spend, retention)              |
|  IdP/PEP | Sampling policy | PII pipeline | Spend/retention | Audit obs  |
|  HMAC id | tail > head     | detect->     | caps            | who viewed |
|  NOT from| OTTL / ERROR    | redact->     | 14d != 400d     |            |
|  model   |                 | audit BEFORE |                 |            |
+-----------+-----------------+------+-------+---------+-------+-----+------+
            |                 |      |               |             |
            v                 v      v               v             v
+----------------------------------------------------------------------+
| DATA PLANE  (OTLP once -- do NOT dual-instrument vendor SDKs)        |
|                                                                      |
|  APP SDK --> BatchSpanProcessor --> local/edge collector              |
|  memory_limiter FIRST --> k8sattributes --> batch                    |
|  loadbalancing exporter routing_key=traceID (NOT NGINX)              |
|       |                                                              |
|       v                                                              |
|  Kafka partition_traces_by_id=true --> sampling StatefulSet           |
|  tailsamplingprocessor (ALL spans of a trace, SAME instance)         |
|       |                                                              |
|  +----+--- fan-out -------------------------------------------+      |
|  | metrics 100% | traces (kept trees) | audit topic (all)      |     |
|  +--------------+---------------------+------------------------+     |
|                                                                      |
|  TOOL PROXIES (MCP / HTTP -- least privilege)                        |
|  inject W3C traceparent into MCP params._meta (UNPREFIXED, SEP-414)  |
|  Identity from verified token -- NEVER model-filled JSON             |
|  Map MCP isError:true --> span status ERROR (JSON-RPC 200 is a lie)  |
+----------------------------------------------------------------------+
```

### Request-Flow Narrative (One User Turn to Three Layers)

1. **Control / PEP.** TLS terminates. Verified JWT expands tenant. HMAC `user.id` with a key that is not in the trace. Sampling policy, hide-content flags, and blob-upload IAM are not model-filled tool args.

2. **Data plane, instrument once.** The app creates one OTel tree: root `invoke_agent {name}` to `execute_tool {allowlisted}` / MCP `tools/call` to child `chat {model}`. W3C `traceparent` rides HTTP headers and MCP `params._meta` (SEP-414, unprefixed keys). Content capture default is off (`NO_CONTENT`). If prod must archive prompts, an upload hook writes an encrypted blob and the span stores `gen_ai.input.messages.ref`.

3. **SDK batch.** `BatchSpanProcessor` holds spans (`OTEL_BSP_SCHEDULE_DELAY` default 5,000 ms, queue 2,048, batch 512, export timeout 30,000 ms). Queue overflow drops spans in the app process.

4. **Edge collector.** `memory_limiter` first (soft limit = `limit_mib - spike_limit_mib`; pair `GOMEMLIMIT` at roughly 80% container memory). `k8sattributes` before tail sampling. Collector batch timeout default 200 ms, `send_batch_size` 8,192.

5. **Sticky route.** Load-balancing exporter `routing_key: traceID` to a headless Service (ClusterIP returns one rotating VIP -- wrong for tail sampling). Or Kafka `partition_traces_by_id: true` (default false -- scatter produces half-trees).

6. **Fan-out -- the three-layer split.** Metrics at 100% from the unsampled pipe. Traces kept via `tailsamplingprocessor` (decision_wait default 30 s, num_traces 50,000). Audit: every tool invocation appends to a WORM topic with principal, tool, args hash, policy decision, trace_id, checkpoint_id. Never passes through the tail sampler.

7. **User path is already done.** The handler returned when the agent finished. Telemetry failure must not become a user 500 -- circuit-break the exporter.

### LLM Observability vs Agent Observability

These are distinct concepts:
- **LLM observability** tracks individual model calls: prompt performance, token usage, model outputs.
- **Agent observability** tracks autonomous workflows: goal achievement, tool execution patterns, multi-step reasoning chains. An agent makes multiple LLM calls across a single task. Agent observability connects these calls into a coherent narrative revealing whether the agent achieved its goal.

---

## Part 2: Core Mechanics and Algorithms

### Trace vs Thread vs Trajectory vs Checkpoint

| Object | Stores | Purpose | Source of Truth |
|--------|--------|---------|-----------------|
| **Trace tree** | Nested spans/runs for one invocation | "Which child timed out?" | OTel trace ID / LangSmith `trace_id` |
| **Thread / session** | Sequence of traces sharing `thread_id` / `session.id` / `gen_ai.conversation.id` | Multi-turn | Metadata key you must set |
| **Trajectory** | Deduped ordered messages plus state transitions | Scan the conversation; inspect loops | Projection, not a store |
| **Graph checkpoint** | Full state snapshot at each super-step | Time-travel, fork, resume | Checkpointer DB -- not a span |

**LangSmith specifics:** run = OTel span; trace = runs for one operation; thread = traces for a multi-turn session; trajectory = flat message list with nesting removed. Messages view (beta) needs `thread_id` plus `ls_agent_type: "root"` on the top run. `subagent` appears as a subagent action; `middleware` / `compaction` are filtered out of the transcript. `LS_MESSAGE_VIEW_EXCLUDE` (presence of the key, not truthiness) hides a run from Messages while leaving it in the tree and metrics.

**Langfuse:** observations nest under a trace; sessions group traces. v4: no separately ingested trace entity -- the OTel trace ID is the grouping.

**Honeycomb:** conversation metrics include duration, trace count, LLM calls, tool calls, failures, total tokens. "Show Failures Only" depends on `error.type` and span status -- swallowed exceptions produce an empty filter.

### W3C Trace Context -- The Wire Standard

```
traceparent: 00-{32 hex trace-id}-{16 hex parent-id}-{2 hex flags}
example:     00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01
```

- Version `00`. Trace ID 16 bytes, parent/span ID 8 bytes. Flag `01` = sampled (head hint, not a tail decision).
- `tracestate`: comma-separated vendor key=value, max 32 members. When a system updates `parent-id`, it must move its entry to the left.
- W3C Baggage is a separate spec -- do not put PII in baggage.
- **MCP:** inject configured OTel propagators into MCP request `params._meta`. Despite MCP's DNS-prefix convention, W3C keys must be unprefixed: `traceparent`, `tracestate`, `baggage` (SEP-414). DNS-prefixing breaks traces. MCP spec 2026-07-28 documents SEP-414 and deprecates protocol Logging in favor of OTel / stderr.

### OTel GenAI vs OpenInference (Complementary, Not Competing)

OTel GenAI conventions are Development, not Stable. By default, no prompt content or tool arguments; metadata only (model names, token counts, durations). Zero of the GenAI-specific span/event/metric/attribute set is Stable as of 2026.

**OTel GenAI key attributes:**
- Span kind: CLIENT (or INTERNAL for in-process). Name: `{gen_ai.operation.name} {gen_ai.request.model}`.
- Operations: `chat` | `text_completion` | `embeddings` | `execute_tool` | `invoke_agent` | `invoke_workflow` | `retrieval`.
- Required: `gen_ai.provider.name`, `gen_ai.operation.name`, `gen_ai.request.model`.
- Usage: `gen_ai.usage.input_tokens` (includes cached) + output + cache-read/creation splits.
- Tool spans: kind INTERNAL. Name: `execute_tool {gen_ai.tool.name}`. Arguments/results are opt-in.

**OpenInference** (Arize): conventions on top of OTel. Transport is OTLP. Required attribute `openinference.span.kind` in ALL CAPS -- different field from OTel `span_kind`. Kinds: `LLM`, `AGENT`, `CHAIN`, `TOOL`, `RETRIEVER`, `RERANKER`, `EMBEDDING`, `GUARDRAIL`, `EVALUATOR`. Attributes are flattened (`llm.input_messages.0.message.role`) -- a 20-turn chat explodes key count.

**Mapping (not 1:1 identity):**

| OTel operation.name | OpenInference kind | Datadog kind | Langfuse type |
|---------------------|-------------------|-------------|---------------|
| `chat` / `generate_content` | `LLM` | `LLM` (billable) | `generation` |
| `execute_tool` | `TOOL` | `tool` (not a valid root) | `tool` |
| `invoke_agent` | `AGENT` | `agent` (valid root) | `agent` |
| `invoke_workflow` | `CHAIN` | `workflow` (valid root) | `chain` |

**Datadog billing:** LLM spans only are billable; tool/workflow/agent/embedding/retrieval are free.

**Content capture -- three spec patterns:**

| Pattern | When |
|---------|------|
| Default: do not record | Production default |
| Record on span attrs (`gen_ai.input.messages`, etc.) | Pre-prod |
| Store externally, record `gen_ai.*.messages.ref` on span | Production for volume + separate ACL |

Python env var `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`: `NO_CONTENT` (default) | `SPAN_ONLY` | `EVENT_ONLY` | `SPAN_AND_EVENT`.

### Head Sampling vs Tail Sampling (Agent Default = Tail)

**Head** (`TraceIdRatioBased`) decides at span start, before tools, `finish_reason`, or the 40-step loop. Cheap. Wrong as the primary policy for agents because the interesting information is only known at the tail.

**Tail** waits until the tree is approximately complete, then keeps or drops the whole tree. The collector contrib `tailsamplingprocessor` is beta, stateful, and must receive every span of a trace on the same instance.

| Knob | Default | Meaning |
|------|---------|---------|
| `decision_wait` | **30 s** | When the decision is made |
| `num_traces` | **50,000** | In-memory cap; excess triggers `trace_dropped_too_early` |
| `sampling_strategy` | `trace-complete` | `span-ingest` evaluates each batch |

**Agent-shaped policy stack (order matters):** keep ERROR / `content_filter` / policy-deny / HITL; keep high-latency roots; bytes/rate-limit under overload; probabilistic remainder; SDK head sample only if collectors are saturated.

**Platform-specific sampling behaviors:**
- **Datadog:** SDK sampling is head-on-the-root at ingest (cost control, not a GenAI policy engine). Metrics `ml_obs.*` remain 100% of instrumented traffic, 15-month retention.
- **Honeycomb Refinery:** samples before ingest (dropped events do not count toward EPM). GenAI guidance: keep 100% of traces carrying `gen_ai.conversation.id` -- true for mixed APM but false when GenAI is the product.
- **Grafana Adaptive Traces:** root received then decide 2 s after it arrives; no root then wait up to 30 s. Not the same as Collector `decision_wait=30 s`. An `invoke_agent` starting at t=0 finishing at t=90 s is decided at t=2 s; later MCP children are late.

### Tail-Sampling Memory Model

Let lambda = new traces/s, W = `decision_wait` (s), S = mean spans/trace, B = mean bytes/span.

| Quantity | Bound | Production Implication |
|----------|-------|-----------------------|
| In-flight traces | lambda * W | Must have `num_traces` >= lambda * W |
| Default cap throughput | 50,000 / 30 = ~1,667 traces/s | Theoretical ceiling at defaults |
| Memory (metadata-only) | S=25, B=2KB: 50k * 50KB = **2.5 GB** | Manageable |
| Memory (content-on) | B=50KB: **62.5 GB** -- OOM first | Content-off for sampling |

A 90 s tool with W=30 s samples a partial tree as "OK." Raise W to >= product p99 e2e + slack.

### Four Dashboard Categories

| Category | Key Metrics |
|----------|-------------|
| **Operational** | Request rate, error rate, TTFT, total latency, token throughput (tokens/sec) |
| **Economic** | Input/output token counts, cost/request, cost/session, cost by feature/team |
| **Quality** | Eval pass rate, hallucination rate, task completion rate, user feedback scores |
| **Safety** | Guardrail trigger rate, prompt injection detection rate, PII exposure events |

### The Compounding Reliability Problem

A step that is 95% reliable gives 0.95^10 = ~59% reliability across a 10-step workflow. No individual span looks abnormal -- the failure is emergent. This is why agent observability is fundamentally different from traditional APM.

### Burn-Rate SLOs (Google SRE to Datadog)

For a 30-day, 99.9% SLO:

| Severity | Long Window | Short Window | Burn Rate | Budget Consumed |
|----------|------------|-------------|-----------|-----------------|
| Page | 1 hour | 5 minutes | **14.4** | 2% |
| Page | 6 hours | 30 minutes | **6** | 5% |
| Ticket | 3 days | 6 hours | **1** | 10% |

Short window = 1/12 of long. Multiwindow AND: fire only if both windows exceed so the alert resets when burn stops. Apply to availability and TTFT/e2e, not token-count gauges.

**Datadog:** `burn_rate("slo_id").over("30d").long_window("1h").short_window("5m") > 14.4`. Max long window 48 hours. **Honeycomb Pro:** includes 2 SLOs.

---

## Part 3: Token Economics and NFR Analysis

### Vendor Cost Comparison -- Do Not Mix SKUs

The dominant interview failure is quoting `$/1k traces` across vendors as if they were the same object. Each vendor bills a different unit.

| Vendor | Billable Unit | Published List (2026) | Retention |
|--------|--------------|----------------------|-----------|
| **LangSmith** | Trace (root + all child runs = 1) | Base 0.05c/trace ($0.50/1k), extended 0.50c/trace ($5.00/1k). Plus $39/seat | Base 14 days; extended 400 days |
| **Datadog Agent Obs** | LLM spans only (tools free) | First 100k LLM spans/mo $160 annual. Overage $3.50/10k | Default 15 days traces |
| **Honeycomb** | Event = one span | Pro from 2026-07-01: $3.00/million events (legacy $1.30/M) | Events 60 days (ingest date) |
| **Langfuse Cloud** | Unit = trace + observation + score | Core $29/mo, 100k units, 90d. Pro $199/mo, 3 years | Plan-tier dependent |
| **Grafana Cloud** | GB processed / written / retained | $0.05/GB processed + $0.40/GB written after 50 GB allotment | 14d free / 30d paid |

**Inferred per-1k costs (named assumptions, not vendor SKUs):**
- LangSmith base: **$0.50/1k traces**. Extended: **$5.00/1k traces**.
- Datadog: 1 agent request x 8 LLM calls = 8 billable spans. Annual overage: ~**$2.80/1k such requests**.
- Honeycomb: $3.00/1M events. A 25-span agent turn = ~**$0.075/1k traces**.
- Langfuse: 1 trace = ~8 units (1 trace + 6 observations + 1 score). ~**$0.64/1k** at $8/100k overage.
- Grafana: not priced per trace. 100k turns x 25 spans x 2KB = 5 GB, inside 50 GB allotment ($0 write after $19 platform).

**The meter determines architecture:** Datadog favors deep trees (tools free). LangSmith bills the tree as one. Honeycomb bills every span. Langfuse bills every observation and every score. Grafana bills GB. Pick the meter that matches your topology, then cap content.

### Storage Shape -- Why LLM Traces Are 10-100x APM

APM span: tens to hundreds of bytes. LLM span with content: 2-32k tokens = 8-128 KB UTF-8 per call, plus tool JSON.

**LangSmith hourly caps:**

| Plan | Events/hour | Payload/hour |
|------|-------------|-------------|
| Developer, no card | 50,000 | 500 MB |
| Developer, card on file | 250,000 | 2.5 GB |
| Plus | 500,000 | 5.0 GB |

At 5.0 GB/h / 500k events = ~10 KB/event average headroom. A 50 KB prompt on create and 80 KB on update = 130 KB for one run. Content-on-by-default 429s you before span-count does.

**Honeycomb event limits:** max 2,000 distinct fields; entire event < 1 MB uncompressed JSON; each string field <= 64 KB. OpenInference flattening of a 40-turn chat can blow 2,000 fields.

**Auto-upgrade tax (LangSmith).** Online evaluators and automation rules default to extending retention. Matching any run upgrades the entire trace; a thread-level rule upgrades every trace in the thread. This is how a 14-day debug project becomes a 400-day invoice.

### Latency SLA -- Two Clocks

No vendor publishes a universal "trace ingest p99 = X ms" SLO or composed agent e2e p50/p95/p99. Bound the pipeline from published knobs.

**(a) Product e2e -- inferred policy targets:**

| Path | p50 | p95 | p99 | Mitigation |
|------|-----|-----|-----|------------|
| Inner chat TTFT (streaming) | 640 ms | 2,560 ms | 5,120 ms | Stream; cache-read split |
| Inner chat e2e (one chat span) | 1,280 ms | 10,240 ms | 20,480 ms | Timeout provider independently of exporter |
| Agent e2e (root invoke_agent; 10-120 s class) | 15,000 ms | 60,000 ms | 90,000 ms | Parallel tools count as max() not sum(); HITL is a gap |

Agent e2e at 120 s overflows the last OTel suggested histogram bucket (81.92 s) -- add a wider root histogram.

**(b) Telemetry pipeline -- inferred from published knobs:**

| Path | p50 | p95 | p99 | Grounding |
|------|-----|-----|-----|-----------|
| SDK BatchSpanProcessor holdback | 2,500 ms | 5,000 ms | 5,000 ms | Uniform over 0-5,000 ms schedule delay |
| Metrics path (100%, pre-sample) | 2,600 ms | 5,200 ms | 8,000 ms | SDK holdback + collector batch |
| Tail-sampled traces (trace-complete) | 32,600 ms | 35,200 ms | 38,000 ms | decision_wait 30 s + SDK/collector |
| Langfuse OTLP without v4 header | -- | -- | 600,000 ms | Looks like "missing traces" |

### Observable Run Cost Formula

```
observable_run_cost
  ~= model_input_cost + cached_read_cost + cache_write_cost
   + output_cost + reasoning_token_cost
   + tool_or_retrieval_surcharges
   + trace / checkpoint persistence overhead
   + eval LLM-as-judge cost (if online)
```

### Throughput and Back-Pressure

| Ceiling | Number | Effect |
|---------|--------|--------|
| SDK BSP queue | 2,048 spans | Overflow drops in the app |
| Collector sending_queue | 1,000 batches, 10 consumers | Full then drop and count |
| Phoenix in-process queue | 20,000 spans default | RESOURCE_EXHAUSTED |
| Tail in-memory | 50,000 traces | trace_dropped_too_early |
| LangSmith Plus | 500k events/h, 5.0 GB/h, 5k POST/PATCH per min | Content-on 429s look like flaky tools |
| Honeycomb throttle | Accept 1 of 10 after 2nd overage month | Head-random missing children |
| Grafana metrics slack | 30,000 ms | Late spans vanish from RED |

**Back-pressure design:** (1) `memory_limiter` first -- soft refuse is retryable; (2) Kafka between edge and sampling; (3) persistent exporter queue (`file_storage` WAL); (4) degrade: drop content -> drop traces keep metrics -> local disk buffer; (5) SDK head sample only as last-ditch; (6) bulkhead user serve vs exporter -- Datadog timeout must not become a user 500.

---

## Part 4: Distributed Resilience and Security

### PII Pipeline -- Detect, Redact, Audit -- BEFORE Export

Token-level redaction recall is never 100%; architect as if regex will miss. Dual-write of full prompts to logs and spans doubles the PII store.

**Real-world incident:** A customer's voice agent logged complete credit card numbers in OpenTelemetry spans for three weeks -- not in the transcript display (that was redacted) but in telemetry used for latency debugging.

**Three-Layer PII Architecture:**

| Layer | Role | Note |
|-------|------|------|
| **Layer 1: SDK** (pre-serialization) | Primary control. Redact at instrumentation before payload hits the wire | Post-hoc redaction is incomplete by construction |
| **Layer 2: OTel Collector** (Redaction Processor) | Regex-based pattern matching on attribute keys and values. Central policy enforcement | Collector allowlist of `gen_ai.*` metadata attrs |
| **Layer 3: Backend** (last resort) | Post-hoc scrubbing. Raw payload exists for some period before the scrubber catches up | Fatal weakness: time window where raw PII is readable |

**Pipeline steps:**
1. **Detection.** Scan span attrs marked sensitive by the spec (`gen_ai.input/output.messages`, `system_instructions`, `tool.call.arguments/result`, `prompt.variable`), OpenInference fields, retrieval documents, screenshots, and eval datasets about to be promoted. Regex: email, US SSN, US phones, PANs. NER: Presidio / Comprehend for names, locations.
2. **Redaction.** Replace with stable tokens (`[EMAIL_<hash12>]`). Put the essay on a blob; put policy decision, tool name, call id, model request vs response id on low-cardinality span attrs and the action audit.
3. **Audit trail (WORM).** Immutable log of detect/redact decisions, not values: `content_sha256` pre- and post-redact, entity types + counts, action, detector, trace_id, span_id, tenant_id.

**If the classifier is down:** fail closed on content export (still serve the user; still emit metrics + redacted metadata).

### Tool RBAC for Observability

| Tool / Role | Who | Must Not |
|-------------|-----|----------|
| `execute_tool {allowlisted}` | Agent, identity from token | Omnibus `search(collection)` / model-filled `tenant_id` |
| Tempo MCP (`get-trace`, etc.) | Privileged assistant identity | Run as an unredacted LLM over prod traces |
| LangSmith Viewer | SRE | See raw prompts |
| Debugger | On-call | Raise retention on a HIPAA project without a ticket |
| Privacy / legal | Break-glass blob | Live in the same role as Viewer |

**LangSmith workspace RBAC:** Enterprise only; Plus/Developer default all users to Admin. **Langfuse:** org RBAC on paid Cloud; project-level RBAC on Pro Teams add-on ($300/mo) or Enterprise.

### Zero-Trust Observability

Separate three classes of data:
- **Metadata traces** (always): model, tokens, latency, tool name, policy decision, error class.
- **Content** (break-glass): encrypted blob, short TTL, just-in-time access, ticketed.
- **Audit of observability**: who exported / viewed a trace.

CoSAI: "Insufficient Observability" is T12 of 12 core threat categories. The whitepaper recommends immutable records, but standardized audit logging across MCP does not yet exist -- sampled Tempo does not fill that gap.

### Two Tapes

| Tape | Contents | Sampled? |
|------|----------|----------|
| **Agent action audit** | Principal + agent id, tool, args hash, policy, trace_id, checkpoint_id | **Never** |
| **Platform audit** | Who changed sampling/retention/keys/SSO, viewed/exported traces | N/A -- different object |

Sampled APM traces are not this tape. Tail sampling that keeps 1% of happy paths cannot prove a tool was never called.

### Circuit Breaker for Export (Closed to Open to Half-Open)

Independent breakers: trace backend (Datadog/LangSmith/Tempo), content blob store, metrics backend. A Datadog timeout must not block the user (bulkhead). A blob 5xx must not block metadata traces.

```
        failures >= threshold or error-rate window
  +----------+  ------------------------------------->  +----------+
  |  CLOSED  |                                         |   OPEN   |
  | pass all |  success resets consecutive count       | fail fast|
  +----+-----+                                         +----+-----+
       ^                                                    | cooldown
       |                                                    v
       |                                              +----------+
       +---------- trial OK --------------------------| HALF-OPEN|
                   trial fail -> OPEN                 | 1 probe  |
                                                      +----------+
```

**Fallback chain:** full content traces -> redacted/pointer-only traces -> metrics-only (drop traces) -> local disk buffer. Never the reverse on a privacy path. Never fail-open into full prompts.

### Broken Trees Across MCP -- Root Causes

Missing child spans result from: head sample on the MCP server, different collector without sticky traceID, `decision_wait` shorter than the tool, LangSmith 25k run cap, gRPC 4 MB fail, 429 hourly payload, dual vendor SDKs, Phoenix HTTP on wrong port (use 6006 not 4318), ClusterIP in front of the tail sampler, Honeycomb 1-in-10 throttle, or MCP `isError: true` on OK JSON-RPC (RED looks 100% healthy while the agent loops). Stdio MCP servers as child processes without `OTEL_EXPORTER_OTLP_ENDPOINT` is the number one broken-tree cause -- prefer a sidecar collector on the host.

---

## Part 5: Production Enterprise Code

Self-contained stdlib. Swap Fake* ports for OTLP / Kafka / vendor HTTP.

```python
"""Telemetry-plane resilience: PII pipeline, export fallback, never block user.
Stdlib only. Run: python observability_runtime.py
"""
import hashlib, hmac, json, logging, random, re, time, uuid, threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

# --- Structured logging with correlation IDs ---
class CorrelationFilter(logging.Filter):
    def filter(self, record):
        for k, d in (("correlation_id", "-"), ("tenant_id", "-"),
                     ("traceparent", "-"), ("export_tier", "-")):
            setattr(record, k, getattr(record, k, d))
        return True

LOG = logging.getLogger("obs")
_h = logging.StreamHandler()
_h.setFormatter(logging.Formatter(
    '{"ts":"%(asctime)s","cid":"%(correlation_id)s",'
    '"tier":"%(export_tier)s","msg":"%(message)s"}'))
_h.addFilter(CorrelationFilter())
LOG.addHandler(_h); LOG.setLevel(logging.INFO)

# --- Retry with full jitter (AWS-style) ---
class TransientError(Exception): pass
class PermanentError(Exception): pass

def retry_with_jitter(fn: Callable, *, attempts=4, base=0.05, cap=1.0):
    last = None
    for i in range(attempts):
        try: return fn()
        except PermanentError: raise
        except TransientError as e:
            last = e
            if i < attempts - 1:
                time.sleep(random.uniform(0, min(cap, base * (2**i))))
    raise last

# --- Circuit breaker (fail-closed for export) ---
class CircuitState(str, Enum):
    CLOSED = "closed"; OPEN = "open"; HALF_OPEN = "half_open"

@dataclass
class CircuitBreaker:
    name: str
    threshold: int = 5
    cooldown_s: float = 15.0
    _state: CircuitState = CircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self):
        with self._lock:
            if self._state is CircuitState.OPEN:
                if time.monotonic() - self._opened_at >= self.cooldown_s:
                    self._state = CircuitState.HALF_OPEN
                else: raise TransientError(f"circuit_open:{self.name}")
            if self._state is CircuitState.HALF_OPEN:
                pass  # one probe allowed

    def record_success(self):
        with self._lock:
            self._failures = 0; self._state = CircuitState.CLOSED

    def record_failure(self):
        with self._lock:
            self._failures += 1
            if self._failures >= self.threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()

# --- PII detect -> redact -> audit (before export) ---
EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9.-]+")
SSN_RE   = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
PHONE_RE = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")

class PiiPipeline:
    """Detect -> redact -> audit. Never logs raw PII values."""
    def __init__(self, audit_log: list):
        self.audit = audit_log

    def apply(self, text: str, **meta) -> str:
        pre_sha = hashlib.sha256(text.encode()).hexdigest()[:16]
        types = {}
        for name, rx in [("EMAIL", EMAIL_RE), ("SSN", SSN_RE), ("PHONE", PHONE_RE)]:
            hits = len(rx.findall(text))
            if hits: types[name] = hits
        out = text
        for name, rx in [("EMAIL", EMAIL_RE), ("SSN", SSN_RE), ("PHONE", PHONE_RE)]:
            out = rx.sub(lambda m: f"[{name}_{hashlib.sha256(m.group().encode()).hexdigest()[:12]}]", out)
        post_sha = hashlib.sha256(out.encode()).hexdigest()[:16]
        # Audit: log decisions, not values
        self.audit.append({"type": "pii_decision", **meta,
                           "pre_sha": pre_sha, "post_sha": post_sha,
                           "types": types, "action": "tokenize" if types else "none"})
        return out

# --- W3C traceparent generation ---
def new_traceparent():
    tid, sid = uuid.uuid4().hex, uuid.uuid4().hex[:16]
    return f"00-{tid}-{sid}-00", tid, sid

# --- Export with fallback: full -> redacted -> metrics-only -> disk ---
class TelemetryRuntime:
    def __init__(self, hmac_key: bytes):
        self.hmac_key = hmac_key
        self.metrics = []       # Layer 1: always-on, content-free
        self.audit = []         # Layer 3: never-sampled WORM
        self.pii = PiiPipeline(self.audit)
        self.breaker = CircuitBreaker("traces")
        self.exported = []      # Simulated backend

    def hash_user(self, user_id: str) -> str:
        """HMAC user ID -- never as a metrics label."""
        return hmac.new(self.hmac_key, user_id.encode(), hashlib.sha256).hexdigest()[:16]

    def export_turn(self, *, tenant, trace_id, tokens_in, tokens_out,
                    latency_ms, cost_usd, tool_calls, prompt_text, allow_content):
        # L1: Metrics always (100%, content-free)
        self.metrics.append({"tenant": tenant, "tokens_in": tokens_in,
                             "tokens_out": tokens_out, "latency_ms": latency_ms,
                             "cost_usd": cost_usd})
        # L3: Audit always (never sampled)
        for tool in tool_calls:
            args_sha = hashlib.sha256(
                json.dumps(tool.get("args", {}), sort_keys=True).encode()
            ).hexdigest()[:16]
            self.audit.append({"type": "tool_action", "tenant": tenant,
                               "trace_id": trace_id, "tool": tool["name"],
                               "args_sha": args_sha, "policy": tool.get("policy", "allow")})
        # L2: Traces with fallback
        redacted = self.pii.apply(prompt_text, tenant=tenant, trace_id=trace_id)
        tiers = []
        if allow_content:
            tiers.append(("full", prompt_text))
        tiers.append(("redacted", redacted))

        for tier, content in tiers:
            try:
                self.breaker.allow()
                self.exported.append({"trace_id": trace_id, "tier": tier})
                self.breaker.record_success()
                return {"status": "ok", "tier": tier}
            except TransientError:
                self.breaker.record_failure()
        # Fallback: metrics-only, user path never blocked
        return {"status": "metrics_only", "tier": "metrics"}

# --- Demo ---
if __name__ == "__main__":
    rt = TelemetryRuntime(b"server-key-not-in-trace")
    tp, tid, sid = new_traceparent()
    result = rt.export_turn(
        tenant="acme", trace_id=tid, tokens_in=800, tokens_out=120,
        latency_ms=2400, cost_usd=0.004,
        tool_calls=[{"name": "refund_lookup", "args": {"ticket": "T-9"}}],
        prompt_text="Email ada@example.com about refund", allow_content=True)
    print(f"Export: {result}")
    print(f"Metrics: {len(rt.metrics)}, Audit: {len(rt.audit)}, Exported: {len(rt.exported)}")
    assert len(rt.metrics) == 1      # Metrics always
    assert len(rt.audit) >= 2        # PII decision + tool action
    print("User path never blocked.")
```

---

## Common Failure Modes

| Failure | Cause | Detection | Mitigation |
|---------|-------|-----------|------------|
| **Broken tree / missing child** | Head sample on MCP; no sticky traceID; decision_wait < tool; 25k cap; dual SDKs | Partial UI; metrics still 100% | Two-tier + headless + Kafka by trace ID; wait >= p99 e2e |
| **Partial tree sampled as OK** | decision_wait=30 s vs 90 s agent; Adaptive 2 s after root | Root OK, MCP children absent | Wait for root end; late-span decision cache |
| **MCP isError lie** | JSON-RPC 200 + isError:true | RED 100% healthy; agent looping | Map isError to span status ERROR before tail sample |
| **Cardinality bomb** | span_name with ids; user.id as metric label; OpenInference flattening | Tempo HLL overflow; DD tag truncate | DRAIN sanitization; closed tag set; high-card on traces only |
| **PII in traces** | Content capture copied staging to prod; tool args; Gateway gap; public share | DLP findings; unauthenticated share hits | Detect-redact-audit before export; disable public sharing |
| **Sampling bias** | Head 1% successes; error-only keep | Missing jailbreaks; cost dashboards lie | Tail OTTL; record sample_rate; do not count() unweighted |
| **RED metrics go to zero** | Metrics slack 30 s + tail wait 30 s | Dashboards empty during incident | Metrics connector before sampler; widen slack |
| **429 as "flaky tools"** | LangSmith 5 GB/h content-on | Missing updates (create without completion) | Hide content; sample; raise plan |
| **Auto-extend invoice** | Online eval / thread rule upgrades entire traces | 14 d project becomes 400 d bill | Opt out per evaluator; base default; Engine off |
| **Replay "fixed" a flake** | LangGraph replay re-calls the model | New tree != old tree | Recorded span I/O + checkpoint + WORM |
| **Final-answer-only dashboards** | Only checking last output | Miss retry storms, dead-end tool loops | Trajectory-level monitoring |
| **Silent failures** | Agent loops/hallucinates but returns 200 | Eval scoring (not APM) | Quality SLOs via automated evaluators |

---

## Interview Q&A

**Q1. Explain production agent observability to someone who only knows APM.**
I treat the trace as a PII store that happens to look like APM. I split three surfaces -- trajectory, resource usage, evidence -- and three layers that do not share a sampling policy: 100% content-free metrics, tail-sampled redacted traces, and an unsampled WORM action audit. I never put prompts on Prometheus labels or sample the audit tape. An LLM returning HTTP 200 in 500ms can be completely wrong -- observability must include quality measurement, not just latency and status codes.

**Q2. Trace vs thread vs trajectory vs checkpoint -- one sentence each.**
Trace: nested spans for one invocation (who timed out). Thread: many traces sharing conversation.id across turns. Trajectory: a projection -- the flattened message/state path, not a store. Checkpoint: LangGraph state snapshot for resume; replay re-calls the model and is not audit truth.

**Q3. Why is head sampling wrong for agents?**
Head decides at span start, before tools, finish_reason, or the 40-step loop. The interesting bit is only known at the tail. I wait decision_wait on one collector instance sticky-routed by trace_id, keep ERROR/content_filter/HITL/high latency, and use SDK head sample only if collectors are saturated.

**Q4. OTel GenAI vs OpenInference -- are they competing?**
No. OpenInference is span-kind conventions on OTel's OTLP wire (openinference.span.kind ALL CAPS, flattened keys). OTel GenAI is Development, content off by default. I instrument once with OTel-compatible metadata and export where needed -- treat Datadog/Langfuse kinds as exporters and UI mappings. Phoenix OTLP/HTTP is port 6006, not 4318.

**Q5. Give me $ per 1k without mixing SKUs.**
LangSmith documented 0.05c = $0.50/1k traces (extended $5/1k). Datadog is LLM-spans: about $2.80/1k eight-call requests at $3.50/10k overage. Honeycomb about $0.075/1k 25-span trees at $3/M events. Langfuse about $0.64/1k at 8 units and $8/100k overage. Grafana is GB, not traces. I never mix the third-party $2.50/1k claim with the 0.05c invoice line.

**Q6. How would you design a production observability stack?**
Metrics at 100%, sampled redacted traces, and a separate immutable action audit keyed by trace ID. Instrument once with OTLP, fan out from the collector. Content off by default with encrypted blob plus pointer for break-glass. Tail-sample to keep error, high-latency, policy-deny, and HITL traces. Never block the user on a backend timeout.

**Q7. PII pipeline -- walk detect, redact, audit.**
Before export: regex plus NER on spec-sensitive fields and tool args (Gateway misses those). Redact to tokens / blob pointers; HMAC user ids; collector allowlist. Audit WORM of decisions -- pre/post hashes, entity types, counts -- plus the agent action tape (tool + args hash). If NER is down I fail closed on content, not on the user. Treat the trace backend as a subprocessor if it holds prompts.

**Q8. MCP broke our trace tree. What did we miss?**
Unprefixed traceparent in params._meta (SEP-414). Sidecar collector for stdio children. Sticky traceID through a headless Service, not ClusterIP. decision_wait >= tool timeout. Map isError to span ERROR. Do not DNS-prefix the W3C keys and do not duplicate execute_tool as a sibling of tools/call.

**Q9. What should fail closed vs fail open in observability?**
Authorization and content export should fail closed. The user path should never wait on an export timeout. Metrics export is best-effort but nearly always available. The fallback chain is: full content traces, then redacted traces, then metrics-only, then local disk buffer. Never the reverse on a privacy path.

**Q10. What is the most common observability anti-pattern?**
Treating prompt capture as observability. It is only one expensive and risky slice. A team that captures all prompts but has no quality SLOs, no cost attribution, and no tail sampling is both over-spending on storage and under-protected on privacy while still missing regressions.

**Q11. Datadog is timing out. User p99 climbed. What went wrong?**
The exporter is on the request path. I circuit-break the trace backend (closed -> open -> half-open), fall back full -> redacted -> metrics-only -> disk buffer, and keep ml_obs.* on the unsampled pipe. Product availability and observability availability are different SLOs.

**Q12. How do you handle SLO design for LLM systems?**
Traditional availability SLOs (HTTP 200 rate) are necessary but insufficient. I add multi-dimensional SLOs: availability (99.9%), latency TTFT p95 (< 500ms interactive), quality (eval pass rate > 85%), cost ($ per 1k successful completions), and safety (guardrail FPR < 2%). Availability != HTTP 200 -- a system returning 200 with hallucinated content is not "available." I use burn-rate alerts: page at 14.4x burn on 1h/5m windows for a 30-day 99.9% SLO.

---

## Key Numbers to Memorize

### Wire / Conventions / Sampling
| Number | What |
|--------|------|
| **32 members** | tracestate cap |
| **0 Stable** | GenAI-specific OTel span/event/metric/attr set (2026); content off by default |
| **81.92 s** | Last suggested duration histogram bucket; 120 s agent overflows |
| **30 s / 50,000** | decision_wait / num_traces defaults |
| **~1,667 traces/s** | Theoretical ceiling at defaults (50k/30s) |
| **2 s / 30 s** | Grafana Adaptive: after root / no root |
| **5,000 / 2,048 / 512 / 30,000 ms** | BSP schedule / queue / batch / export timeout |
| **200 ms / 8,192** | Collector batch timeout / send_batch_size |
| **80%** | GOMEMLIMIT vs container memory; memory_limiter first |
| **4 MB / 20,000 / 6006** | Phoenix gRPC max / queue default / OTLP HTTP port (not 4318) |
| **2,000 fields / 1 MB / 64 KB** | Honeycomb event caps |
| **25,000** | LangSmith max runs per trace |
| **14.4x / 6x / 1x** | 30d 99.9% page 1h / page 6h / ticket 3d burn rates |

### Cost / Meters / Retention
| Number | What |
|--------|------|
| **$0.50 / $5.00 per 1k** | LangSmith base / extended per 1k traces |
| **$160 / 100k; $3.50/10k** | Datadog annual LLM-span package / overage |
| **$3.00 / M events** | Honeycomb new Pro from 2026-07-01 |
| **$8 / 100k units** | Langfuse first overage band |
| **$0.40/GB written** | Grafana Cloud Traces after 50 GB allotment |
| **14d / 400d** | LangSmith base / extended retention |
| **15d / 60d / 90d** | Datadog / Honeycomb / Langfuse Core retention |

### Latency (Numeric ms)
| Number | What |
|--------|------|
| **640 / 2,560 / 5,120 ms** | Inner chat TTFT p50/p95/p99 (inferred policy) |
| **15,000 / 60,000 / 90,000 ms** | Agent e2e p50/p95/p99 (inferred, 10-120 s class) |
| **2,600 / 5,200 / 8,000 ms** | Metrics-path collector lag p50/p95/p99 |
| **32,600 / 35,200 / 38,000 ms** | Tail-complete trace freshness at default 30 s wait |
| **600,000 ms** | Langfuse OTel without v4 header (up to 10 min) |

---

## Quick Reference

**Instrument once, export many.** OTel GenAI + W3C traceparent. OpenInference and Datadog span kinds are UI mappings, not competing protocols.

**Three layers, three sampling policies.** Metrics 100%. Traces tail-sampled, redacted. Audit never sampled, WORM.

**Agent default sampler is tail.** Sticky by trace_id. decision_wait >= product p99 e2e. Head sampling decides before the interesting bit exists.

**Content off by default.** Blob + pointer for break-glass. Detect -> redact -> audit before export. Gateway does not cover tool args.

**Never block the user on a backend timeout.** Circuit-break export. Fallback: full -> redacted -> metrics-only -> disk buffer.

**Replay is not audit.** Checkpoints resume; they re-execute. Legal proof is recorded I/O + never-sampled action hashes.

**Dollars follow the meter.** LangSmith bills trees. Datadog bills LLM spans. Honeycomb bills events. Langfuse bills units. Grafana bills GB. Do not mix.
