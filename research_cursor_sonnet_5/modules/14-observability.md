# 14. Observability

**Sub-areas covered**: OTel GenAI semantic conventions (span/trace taxonomy) · agent-gateway two-tier Collector topology · head vs. tail sampling · LangGraph checkpointing & trajectory replay · durable trace storage (Tempo/ClickHouse) · circuit breakers for the telemetry backend itself · Zero-Trust MCP observability · PII redaction & audit chain-of-custody

---

## 1. System Topology & Data Flow

An agent observability stack has to solve a problem regular APM never faced: the artifact worth tracing (a multi-step, non-deterministic decision graph with tool calls, retries, and forks) is also the artifact most likely to contain the sensitive payload (full prompts/completions) and the one most likely to overwhelm naive volume-based sampling (a single agent task can legitimately emit thousands of spans). The topology below therefore separates a **control plane** that decides sampling/redaction/access policy *before* data is durably written from a **data plane** that emits and routes spans, with an explicit **tool-proxy** layer for MCP-specific telemetry and a **persistence layer** split between hot trace storage and long-lived trajectory/audit storage.

```
                                   ┌─────────────────────────────────────────────────────────────┐
                                   │                        CONTROL PLANE                          │
                                   │                                                                │
  ┌──────────┐   gen_ai.* spans    │  ┌────────────────┐   ┌─────────────────┐   ┌───────────────┐  │
  │  Agent   │────────────────────▶│  │ Semantic Conv.  │──▶│ Sampling Policy  │──▶│ Redaction /    │  │
  │  Runtime │                     │  │ Registry (pinned│   │ Engine (head %,  │   │ RBAC Policy    │  │
  │ (LLM loop│◀────────────────────│  │ gen_ai.* schema │   │ tail rules,      │   │ Store (allow-  │  │
  │  driver) │   OTEL_SEMCONV_     │  │ version; content│   │ decision_wait)   │   │ list, per-team │  │
  └──────────┘   STABILITY_OPT_IN  │  │ -capture mode)  │   │                  │   │ attribute ACL) │  │
                                   │  └────────┬────────┘   └────────┬─────────┘   └───────┬───────┘  │
                                   │           │                     │                     │          │
                                   │           ▼                     ▼                     ▼          │
                                   │  ┌───────────────────────────────────────────────────────────┐  │
                                   │  │   Circuit Breaker Registry — scoped per (exporter, backend) ;│  │
                                   │  │   CLOSED / OPEN / HALF_OPEN, fail_open=true default (§4.2)  │  │
                                   │  └──────────────────────────┬────────────────────────────────┘  │
                                   └─────────────────────────────┼───────────────────────────────────┘
                                                                  │
                                   ┌──────────────────────────────▼──────────────────────────────────┐
                                   │                            DATA PLANE                            │
                                   │                                                                   │
                                   │  ┌─────────────────────────────────────────────────────────────┐ │
                                   │  │ In-process SpanProcessor chain (§4.7): redact → batch → export │
                                   │  │ create_agent → invoke_agent → execute_tool → retrieval spans   │ │
                                   │  └───────┬───────────────────┬───────────────────┬──────────────┘ │
                                   │          ▼                   ▼                   ▼                │
                                   │  ┌───────────────┐  ┌────────────────┐  ┌──────────────────────┐  │
                                   │  │ MCP Gateway /  │  │ Tier-1 Collector│  │ LangGraph/framework   │  │
                                   │  │ Tool Proxy     │  │ (agent/sidecar):│  │ Checkpointer: state   │  │
                                   │  │ - manifest/    │  │ resource detect,│  │ snapshot per super-   │  │
                                   │  │   schema verify│  │ basic filter,   │  │ step, keyed by        │  │
                                   │  │   logged as    │  │ batch, forward  │  │ thread_id (§2.5)      │  │
                                   │  │   span event   │  │ via loadbalancing│  │                       │  │
                                   │  │ - capability-  │  │ exporter (hash  │  │                       │  │
                                   │  │   negotiation  │  │ on trace_id)    │  │                       │  │
                                   │  │   handshake log│  │                 │  │                       │  │
                                   │  │ - per-tool     │  │                 │  │                       │  │
                                   │  │   allow-list   │  │                 │  │                       │  │
                                   │  └───────┬───────┘  └────────┬────────┘  └──────────┬────────────┘ │
                                   └──────────┼───────────────────┼──────────────────────┼──────────────┘
                                              │                   │                      │
                                   ┌──────────▼───────────────────▼──────────────────────▼──────────────┐
                                   │                Tier-2 Collector (GATEWAY) — stateful                 │
                                   │  guaranteed-complete-trace routing (all spans of one trace_id land   │
                                   │  here); runs tail_sampling processor, redaction backstop, span-      │
                                   │  metrics/RED connector on FULL pre-sampling volume (parallel path)   │
                                   └───────────────────────────────┬────────────────────────────────────┘
                                                                   │
                                   ┌───────────────────────────────▼──────────────────────────────────┐
                                   │                         PERSISTENCE LAYER                          │
                                   │                                                                     │
                                   │  ┌────────────────┐  ┌────────────────┐  ┌──────────────────────┐  │
                                   │  │ Trace Store     │  │ Log Pipeline    │  │ Checkpoint/Trajectory│  │
                                   │  │ (Tempo/Click-   │  │ (structured     │  │ Store (Postgres/     │  │
                                   │  │ House): immutable│  │ JSON, trace_id/ │  │ Redis-backed saver;  │  │
                                   │  │ Parquet blocks   │  │ run_id/span_id  │  │ get_state_history for│  │
                                   │  │ in object storage│  │ correlation)    │  │ replay & fork, §2.5) │  │
                                   │  └────────┬────────┘  └────────┬────────┘  └──────────┬───────────┘  │
                                   │           │                     │                      │              │
                                   │           └──────────┬──────────┴──────────┬───────────┘              │
                                   │                       ▼                     ▼                          │
                                   │              ┌──────────────────┐  ┌────────────────────────┐         │
                                   │              │ Dead-Letter Queue │  │ Immutable Audit Log      │         │
                                   │              │ (failed exports,  │  │ (WORM: who queried what │         │
                                   │              │ poison-pill spans)│  │ trace, when, result)     │         │
                                   │              └──────────────────┘  └────────────────────────┘         │
                                   └─────────────────────────────────────────────────────────────────────┘
                                                                   │
                                   ┌───────────────────────────────▼──────────────────────────────────┐
                                   │                   TELEMETRY / OBSERVABILITY SINKS                  │
                                   │                                                                      │
                                   │  ┌───────────────┐  ┌────────────────┐  ┌─────────────────────────┐ │
                                   │  │ Trace-Query UI │  │ Trajectory      │  │ Behavioral-Baseline      │ │
                                   │  │ (Grafana/      │  │ Replay Engine   │  │ Anomaly Detector         │ │
                                   │  │ LangSmith/     │  │ (re-execute from│  │ (Watchdog-style; DQR/    │ │
                                   │  │ Langfuse) —    │  │ checkpoint_id,  │  │ TIE semantic SLIs vs.    │ │
                                   │  │ RBAC-gated     │  │ nodes-after     │  │ raw uptime, §4.4)        │ │
                                   │  │ query API      │  │ re-run, §2.5)   │  │                          │ │
                                   │  └───────────────┘  └────────────────┘  └─────────────────────────┘ │
                                   └──────────────────────────────────────────────────────────────────────┘
```

**Request-flow narrative.** (1) The **Agent Runtime** emits GenAI-convention spans as it executes — `create_agent`/`invoke_agent` wrapping the whole task, `execute_tool` around each MCP or function call, `retrieval` around vector-store lookups — nested via a `startActiveSpan`-style API so the resulting span tree mirrors the agent's actual decision graph rather than a flat log. (2) Every span passes through an **in-process `SpanProcessor` chain** before it ever leaves the host process: this is the *only* layer that protects against a misconfigured or compromised downstream Collector, so redaction happens here first, batching second, export third (§4.7). (3) Tool calls specifically route through an **MCP Gateway/Tool Proxy**, which — beyond forwarding the call — logs the tool manifest/schema verification and capability-negotiation handshake as span events and enforces a per-tool allow-list before the call reaches a downstream service (§4.5); this is the MCP-specific extension of the generic tool-proxy pattern. (4) Spans flow to a **Tier-1 (agent) Collector** — lightweight, colocated, doing resource detection and coarse filtering — which forwards via a `loadbalancingexporter` that hashes on `trace_id`, a routing decision that exists for exactly one reason: the stateful **Tier-2 (gateway) Collector**'s `tail_sampling` processor needs *every* span of a given trace on the same instance to make a correct keep/drop decision, which naive round-robin load balancing cannot guarantee (§2.3, §2.6). (5) The gateway tier also runs the redaction backstop (second layer of defense, §4.7) and a `spanmetrics`/RED-metrics connector that must consume the **full, pre-sampling** span volume in a parallel pipeline — computing RED metrics from the post-sampling stream biases them toward errors/slow requests (§3.3). (6) Retained spans persist as immutable Parquet blocks in object storage (trace store), while a parallel structured-logging pipeline correlates the same `trace_id`/`span_id` plus a business-level `run_id`/`thread_id` for conversation-level filtering. (7) Independently, a **framework-level checkpointer** (e.g., LangGraph) persists full graph-state snapshots per `thread_id` at each super-step boundary — this is not the same data as the trace store; it exists specifically to support **replay** (re-execute from a saved state) and **fork** (branch into a new `checkpoint_id` for what-if exploration), functioning as both a debugger and an audit trail. (8) Every read against the trace store or checkpoint store is itself logged to a separate, independently-access-controlled **immutable audit log** keyed by trace ID (§4.8). (9) If any export leg fails after retries, the span is durably parked to a **dead-letter queue** rather than dropped silently or blocking the agent — telemetry-pipeline failures must never propagate back into the agent's control flow (§4.2's fail-open invariant). (10) At the sink layer, a behavioral-baseline anomaly detector watches semantic SLIs (decision-quality rate, tool-invocation efficiency) rather than only infra uptime, because an agent can be "up" by every HTTP metric while its actual decision quality silently degrades (§3.4, §5 of the failure-mode literature).

---

## 2. Core Mechanics & Algorithms

### 2.1 Span/trace hierarchy for agent loops — the six-layer GenAI taxonomy

OTel's GenAI semantic conventions (spun into a dedicated `semantic-conventions-genai` repo starting core v1.42.0, June 2026) define six span "operation layers," and the **critical invariant** for interview purposes is that these compose hierarchically, not as a flat list:

```
invoke_agent {agent.name}                       ← INTERNAL (in-process: LangGraph/CrewAI)
│                                                  or CLIENT (remote: Bedrock Agents, Assistants API)
├─ {operation.name} {request.model}              ← chat/inference span, per LLM call
├─ execute_tool {tool.name}                       ← INTERNAL, mandatory tool-name-in-span-name since v1.41
│  └─ (nested spans inside the tool's own service, if it propagates context)
├─ retrieval {gen_ai.system} {data_source.id}     ← RAG/vector-store lookup
└─ invoke_workflow                                ← predefined multi-step sub-workflow, if used
```

Every span in this tree carries a small fixed core attribute set — `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.agent.id` — which is what lets a query like "total tokens per agent per day" work as a simple aggregation over span attributes rather than a bespoke join. **Load-bearing caveat**: as of mid-2026 every `gen_ai.*` span, event, metric, and attribute remains in `Development` status (none are `Stable`); a dashboard built on these attributes can silently break across a minor OTel version bump, so pin the semantic-convention version explicitly (`OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`) rather than tracking "latest."

**Content capture is a three-mode state machine, not a boolean**, reflecting the debuggability/privacy trade-off directly in the spec: `NO_CONTENT` (default — structural spans only, no prompt/completion bodies) → `SPAN_ONLY` → `EVENT_ONLY` → `SPAN_AND_EVENT` (full content on both). An `upload` completion hook can offload full message bodies to external `fsspec`-compatible object storage and leave only a reference URI (`gen_ai.input.messages.ref`) on the span itself — this decouples the *size and sensitivity* of prompt/completion payloads from the trace backend's hot path, which matters directly for §3's cost model and §4.7's redaction architecture.

### 2.2 Multi-agent and cross-hop context propagation

Context propagates via standard **W3C Trace Context** (`traceparent`/`tracestate`) for synchronous HTTP-based inter-agent calls; for queue-based/async agent handoffs there is no automatic propagation — the calling code must manually serialize the trace context into the message envelope, or the child agent's spans become an orphaned, disconnected trace. LangSmith's equivalent (`langsmith-trace`/`baggage` headers) carries the same requirement plus an explicit protocol-level warning: **only accept these headers from trusted internal services**, since an external caller can otherwise forge/inject tracing context via `baggage` — a distributed-tracing-specific spoofing vector that a naive "just propagate whatever headers arrive" implementation is vulnerable to.

### 2.3 Sampling algorithms — head vs. tail, and why the choice is a state machine, not a config flag

**Head-based (deterministic) sampling**: the keep/drop decision is made at the root span *before* the trace completes, based only on information available at that instant (trace ID hash, a fixed probability). Complexity is `O(1)` per trace, memory is flat and predictable, but the algorithm is structurally blind to outcome — it cannot preferentially keep the 0.1% of traces that error or run slow, because at decision time the outcome hasn't happened yet.

**Tail-based sampling**: the decision is deferred until the trace completes or a `decision_wait` timeout elapses (commonly 30s), evaluating the full trace — status code, total latency, specific attribute values — against a policy set. This inverts the recall trade-off: tail sampling captures 90–99% of "interesting" (error/slow) traces vs. roughly 10% for pure probabilistic head sampling at an equivalent overall retention rate, at the cost of a **stateful** collector tier that must buffer every span of every in-flight trace until its decision fires.

**Capacity-planning invariant** (the formula every tail-sampling deployment must size against):

```
collector_memory ≈ TPS × decision_wait × avg_spans_per_trace × avg_span_size
```

This is why tail sampling requires the two-tier agent/gateway topology (§1, §2.6): the gateway's memory footprint is a direct function of `decision_wait`, and if spans of one trace are split across multiple gateway instances, no single instance ever observes a complete trace — the sampling decision becomes not merely lossy but silently *wrong* rather than an error state (§4.4's failure taxonomy classifies this as a permanent misconfiguration, not a transient failure).

**Hybrid (head pre-filter + tail refinement)** is the dominant production pattern: a low probabilistic head rate (1–20%) caps raw ingest volume into the gateway tier, then tail policies on that reduced stream guarantee 100% capture of errors, latency outliers, and business-critical routes, plus a small residual probabilistic baseline (3–10%) so aggregate traffic-volume signal isn't itself biased. **Critical trap**: if trace *count* is ever used as a proxy for request-volume metrics under tail sampling, the sample is heavily skewed toward errors/slow requests and produces a wrong p50/traffic dashboard — RED metrics must always be derived from a separate full-volume `spanmetrics`-style pipeline that runs before any sampling decision, never from counting what tail sampling happened to retain.

### 2.4 LangSmith run-tree reconstruction and its scalability ceiling

LangSmith models execution as a tree of **runs** (functionally OTel spans) typed by `run_type` (`llm`, `chain`, `tool`, `retriever`, `embedding`, `prompt`, `parser`). Chronological reconstruction under out-of-order arrival — a real condition when parallel tool calls or async branches complete in non-deterministic order — is solved via a `dotted_order` field, effectively a materialized-path string that lets the UI reconstruct correct nesting and sequence with an `O(n log n)` sort rather than relying on wall-clock arrival order. A **hard cap of 25,000 runs per trace** is a concrete, documented scalability ceiling: a sufficiently deep or long-running agentic loop (e.g., an autonomous research agent making thousands of tool calls) can exceed this and have additional runs rejected outright — an architectural constraint to design around (e.g., splitting into child traces per phase) rather than a soft degradation.

### 2.5 Trajectory checkpointing and replay — a three-operation state machine

LangGraph's checkpointer treats trajectory storage as a versioned state machine keyed by `thread_id`:

```
        ┌─────────────┐   super-step boundary    ┌─────────────┐
        │   RUNNING    │─────────────────────────▶│  CHECKPOINT  │
        │ (node exec)  │                          │  (state      │
        └──────▲───────┘                          │  snapshot    │
               │                                  │  persisted)  │
               │            get_state_history()   └──────┬───────┘
               │            (reverse-chronological        │
               │             list, debug + audit)         │
               │                                          ▼
     ┌─────────┴──────────┐                     ┌───────────────────┐
     │  REPLAY: invoke     │                     │  FORK: update_     │
     │  with prior         │◀────────────────────│  state(config,      │
     │  checkpoint_id;      │   new branch under   │  values) on a       │
     │  nodes BEFORE the    │   same thread_id,     │  historical         │
     │  checkpoint are      │   new checkpoint_id   │  checkpoint          │
     │  skipped (cached);   │                       │                     │
     │  nodes AFTER         │                       └───────────────────┘
     │  RE-EXECUTE fully    │
     └──────────────────────┘
```

The **invariant most commonly missed in practice**: replay is *not* deterministic playback. Nodes before the target checkpoint are skipped (their cached results are reused), but every node after it — including LLM calls and tool calls — **re-executes**, meaning replay of a trajectory that calls a nondeterministic LLM will not reproduce the exact prior output verbatim; it re-runs the same *logic* from the same *state*, not a recording. Fork calls `update_state()` on a historical checkpoint to create a new branch under the same `thread_id` but a distinct `checkpoint_id`, enabling "what-if" exploration without mutating the original run — this is the mechanism behind most agent trajectory-debugging tooling. A structural limitation: subgraphs checkpoint as a single atomic super-step at the parent level by default, so you cannot time-travel to a point *inside* a subgraph unless that subgraph has its own independent checkpointer.

### 2.6 OTel Collector two-tier pipeline — the algorithmic reason it's two tiers, not one

The reason a single flat pool of Collectors cannot implement tail sampling correctly is a direct consequence of §2.3's memory formula: tail sampling is inherently **stateful per-trace**, and correctness requires all spans of one trace to be visible to the same decision-making instance. A naive horizontally-scaled pool with round-robin or random load balancing scatters a single trace's spans across instances with probability approaching 1 as instance count grows, so **consistent hashing on `trace_id`** at a routing tier (Tier 1) is not an optimization — it's a correctness precondition for Tier 2's tail-sampling processor to function at all. This is the same class of problem as consistent-hash-ring sharding in any stateful distributed system, just applied to trace routing instead of data partitioning.

> ⚠️ Gap: no OTel spec formally proves an upper bound on trace-completeness probability under a given hash-collision/instance-count ratio; the "must route by trace_id" requirement is stated as a hard operational rule by the tail-sampling processor's own documentation rather than derived from a published probabilistic model.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost formulas — instrumentation overhead and trace storage

```
cost_per_1k_runs = (spans_per_run × avg_span_size_bytes × ingest_price_per_GB / 1e9 × 1000)
                  + (collector_compute_share_per_run × 1000)
                  + (storage_cost_per_run_after_retention × 1000)
                  + (query_compute_amortized_per_run × 1000)
```

**Advertised ingest price is not the true price.** Published per-GB SaaS rates run $0.10–$0.50/GB ingested (Google Cloud Trace: $0.20/million spans after a 2.5M/month free tier; Cloud Logging: $0.50/GiB after a 50GiB free tier). But a full cost-of-ownership breakdown at 10K spans/sec shows raw storage is only **~28% of total cost**: Collector compute ~$280/mo, SDK overhead across 200 pods ~$70/mo, cross-AZ network ~$130/mo, self-hosted backend (Tempo+S3) ~$1,156/mo, operational overhead (15% of one engineer's time) ~$2,500/mo → **~$4,136/mo total**, i.e. the true per-GB cost runs **30–60% above the advertised ingestion price** once compute, network, and query load are counted. *(Assumption: single-source modeled breakdown, order-of-magnitude not vendor-audited.)*

**$ per 1,000 agent runs — three representative profiles** (illustrative, August 2026 snapshot; assumes a moderate agentic workflow averaging 40 spans/run, 1.2KB/span uncompressed):

| Scenario | Assumptions | $/run | **$ per 1k runs** |
|---|---|---|---|
| Full tracing, SaaS-managed backend, no sampling | 40 spans × 1.2KB = 48KB/run raw; $0.30/GB blended ingest+storage+query rate | ~$0.0144 | **~$14.4 per 1k runs** |
| Hybrid sampling (10% head + tail on errors/slow), self-hosted Tempo+S3 | Same span volume, ~15% effective retention after tail policy; object storage ~$0.023/GB + compute amortized | ~$0.0025 | **~$2.5 per 1k runs** (~83% reduction vs. full tracing) |
| Full tracing + content capture (`SPAN_AND_EVENT`), regulated workload requiring complete audit trail | Same 40 spans/run but +8KB/run avg for prompt/completion bodies (56KB/run); no sampling permitted for compliance | ~$0.0168 | **~$16.8 per 1k runs**, plus a non-amortized compliance/redaction-pipeline engineering cost not captured in per-run math |

**Scale benchmark (single-source, methodology-disclosed, not independently reproduced)**: at 1M spans/sec sustained, self-hosted Jaeger 1.50 (8× c6g.4xlarge) ran ~$4.2k/month at 12ms p99 write latency; Honeycomb (managed) ran ~$14.7k/month at 8ms p99; Datadog APM (managed) ran ~$21.3k/month at 14ms p99 — self-hosted OSS is roughly **3.5–5x cheaper** at this throughput but requires ~3x the storage footprint and full operational ownership (staffing, on-call, upgrade cadence).

**Self-hosted vs. SaaS crossover** [inferred, order-of-magnitude]: roughly **50GB/day** of telemetry volume. Below that, SaaS convenience outweighs the engineering cost of standing up Collector fleets + a durable backend; above it, a modeled comparison at 570GB/day (200 hosts, 50 engineers with query access, 30-day retention) showed SaaS at ~$906,000/year vs. self-hosted at ~$66,157/year — a >13x gap dominated by a hypothetical enterprise SaaS license line rather than raw compute cost. Treat the specific dollar figures as illustrative rather than vendor-verified list prices.

### 3.2 Latency SLA targets — full P50/P95/P99 table

> ⚠️ No vendor publishes a contractual P95/P99 SLA specifically for tracing/logging *overhead* (as opposed to overall service latency). P50 figures below are drawn from published benchmarks; P95/P99 are architect-constructed design targets `[inferred]`, sized conservatively above the P50 evidence.

| Overhead source | P50 (reported) | P95 `[inferred]` | P99 `[inferred]` | Mitigation |
|---|---|---|---|---|
| Span creation (in-process, no export) | <10µs | ≤30µs | ≤50µs | Keep span-creation on the hot path allocation-free; avoid per-span dynamic attribute-map growth |
| SDK batch export (async, batched) | 1–5ms added to request | ≤8ms | ≤15ms | `BatchSpanProcessor` with tuned `maxQueueSize`/`scheduledDelayMillis`; never synchronous per-span export |
| Full instrumentation, moderate load (production rule-of-thumb) | ~1–5ms p99 absolute add | — | ≤5ms | Batched export + head sampling to bound exporter queue depth |
| Full instrumentation, intensive/serverless workload (academic benchmark) | 7–42% median latency increase | up to 80% throughput decrease | up to 175% latency spike (short serverless functions) | Manual instrumentation (~half the CPU overhead of auto-instrumentation); batching + head sampling measured to cut overhead to 3.6% CPU / 3.4% latency in the same study |
| Tier-1 (agent) Collector hop | 1–3ms | ≤6ms | ≤10ms | Keep Tier-1 stateless/lightweight (resource detection + batch only); no tail-sampling logic here |
| Tier-2 (gateway) Collector — tail-sampling `decision_wait` | 30s (by design, not overhead in the request path) | n/a — asynchronous to the request | n/a | Tail sampling is off the request's critical path; only affects time-to-queryable, not agent-perceived latency |
| Cross-AZ/cross-region export | 5–20ms | ≤50ms | ≤100ms | Regional Collector placement; avoid cross-region hops on the export hot path |
| Trace-query API (backend read path) | 50–300ms (ClickHouse/Tempo, indexed query) | ≤800ms | ≤2s | Columnar storage + block-level column pruning (Parquet); pre-aggregated span-metrics for dashboard-class queries instead of raw trace scans |
| Per-hop zero-trust token exchange (STS, adjacent to trace-context propagation) | consistently <40ms even at dozens of hops/task (Uber production figure) | ≤40ms (stated design requirement) | ≤40ms | Cached short-lived tokens; token-exchange colocated with the service mesh, not a remote call |

**Design target budgets** cited in production guidance: span creation <10µs (hard ceiling 50µs), memory <5KB/span (ceiling 10KB), CPU overhead <1% (ceiling 5%), network <100KB/s per service — these are the numbers to defend in an architecture review when a stakeholder asks "how much will observability slow us down."

### 3.3 Throughput and back-pressure design

- **Collector sizing heuristic**: ~1 vCPU + 2GB RAM per 10,000 spans/sec for a lightweight, trace-only pipeline (starting estimate; validate against actual processor/exporter chain, since PII redaction and tail-sampling processors are meaningfully more CPU-expensive per span than pass-through forwarding).
- **Tail-sampling memory is the binding constraint at scale**, governed by the §2.3 formula (`TPS × decision_wait × avg_spans_per_trace × avg_span_size`); capacity planning for the gateway tier must size against this before sizing against raw CPU.
- **Back-pressure failure mode is silent drop, not backpressure signaling.** OTel's `BatchSpanProcessor` has a bounded queue (`maxQueueSize`, historically defaulting to 2048); when the queue fills, new spans are dropped and only an unscraped internal metric (`otel_sdk_spans_dropped_total`-class counter) increments — there is no default alert, no exception, no log line. A documented 2026 postmortem: this queue overflowed within **170ms of peak traffic** on a 12K RPS payment service, silently dropping **18%** of spans and extending incident diagnosis by **2 hours** because on-call couldn't correlate an ingress rate-limit error with the observed latency spike using an incomplete trace set.
- **Mitigation**: size `maxQueueSize` to roughly 2x expected peak throughput, target an **8:1 ratio** of `maxQueueSize` to `maxExportBatchSize` as a starting point to absorb bursts, enable OTLP retry, and add a **CI-gated span-completeness check** — a load test that queries the trace backend for total ingested span count and fails the build if coverage drops below 99.9% of expected volume. This converts a silent, undetectable failure mode into a build-time gate.
- **Scaling economics**: infra cost scales roughly 8–10x (sub-linear, due to batching efficiency) going from 10K→100K spans/sec, while operational/engineering cost grows much more slowly since Collector-pipeline configuration doesn't multiply with volume — the dominant cost driver at high scale is infra, not headcount.

### 3.4 Non-functional requirements: availability, RPO/RTO, and explicit trade-offs

| NFR dimension | Target | Basis / trade-off |
|---|---|---|
| Trace ingestion path availability | **99.9%** (≈8.7h/year downtime budget) `[inferred design target]` | The ingestion path (SDK → Collector Tier-1) must stay decoupled from backend availability via local buffering, since backend downtime should degrade *query* availability, not *ingestion* availability |
| Trace backend (query/read) availability | **99.5%** `[inferred, looser than ingestion]` | Read-path outages are visible/annoying but not data-lossy if ingestion keeps buffering; deliberately budget a looser SLA here than ingestion to avoid over-engineering the less-critical path |
| RPO — completed, exported spans | **Near-zero** once a span is durably written past the Kafka/durable-queue layer (Tempo's Kafka-then-object-storage layering) | Kafka gives immediate write durability before an object-storage block flush completes; RPO for spans still in the pre-Kafka SDK batch buffer is bounded by `scheduledDelayMillis` (typically ≤5s of exposure) |
| RPO — trajectory checkpoints | Bounded by checkpoint cadence (per super-step) | A crash between super-steps loses only in-flight node execution, not the whole trajectory — this is *better* RPO than a naive periodic-snapshot design because checkpoints are triggered by logical boundaries, not a wall-clock timer |
| RTO — trace query availability after backend incident | **Minutes**, dominated by compaction-catchup and blocklist-poll refresh (default 5-min poll interval) | `compacted_block_retention` should be set to at least 2× `blocklist_poll` so queriers with a stale in-memory blocklist don't 404 against a just-deleted block during recovery |
| RTO — trajectory resume after crash | **Seconds** — `get_state_history()` + resume from last checkpoint, no re-execution of already-completed super-steps | Categorically faster than restarting a multi-step agent run from scratch; this is the primary operational argument for checkpointing beyond its debugging value |
| Compliance retention | 30–90 days hot, longer cold-archive for regulated workloads under EU AI Act activity-log requirements (fully applicable from August 2, 2026) | High-risk AI systems require activity logs, risk assessments, and human-oversight documentation — retention windows for agent trace data increasingly fall under the same obligations as application audit logs, not just an ops convenience window |

**Named trade-off #1 — sampling rate vs. cost/fidelity.** Head-based sampling at a low fixed rate (e.g., 5%) minimizes Collector memory and cost but statistically drops the vast majority of the traces that actually matter for debugging (errors, outliers) at the same rate as routine traffic (§2.3's ~10% interesting-trace recall figure). Tail-based sampling recovers 90–99% recall on interesting traces but roughly **triples Collector memory** and adds an entire stateful infra tier versus a stateless head-only pipeline — a documented production account of migrating head→tail described exactly this 2–3x infra cost bump at the Collector layer, offset by a *larger* drop in backend storage cost because retained data became far more targeted (net cost often falls even though Collector cost rises, but the two line items move in opposite directions and must be modeled separately, not netted informally).

**Named trade-off #2 — full tracing vs. privacy exposure.** `SPAN_AND_EVENT` content-capture mode (§2.1) makes the trace store a complete, queryable copy of every prompt and completion — maximally useful for debugging and compliance audit trails, but it converts the trace backend into a high-value PII target functionally equivalent to a chat-log database, with all the same GDPR/HIPAA/CCPA right-to-be-forgotten obligations now extending into observability infrastructure that was historically treated as "just ops data." The counter-position (`NO_CONTENT`, structural spans only) is far cheaper to secure and store but leaves engineers debugging agent misbehavior blind to what the model actually saw and said — the layered redaction architecture in §4.7 exists specifically to avoid choosing between these two extremes.

---

## 4. Distributed Resilience & Security

### 4.1 Durable trace storage and distributed correlation

Representative durable-storage pattern (Tempo): traces are written as **immutable Apache Parquet blocks** in object storage, with columnar layout meaning a query filtering on `span.http.status_code = 500` reads only that column rather than scanning full trace bodies. A block is invisible to queriers until its `meta.json` manifest exists — an atomic, all-or-nothing visibility mechanism that prevents partial/torn reads of an in-progress flush. **Kafka-based durability layering** (Tempo 3.0) gives immediate write durability the moment a span is produced to Kafka, with object storage providing long-term durability once a block-builder flushes; Kafka's own retention window bridges the gap if a block-builder falls behind, meaning the system degrades gracefully (temporary backlog) rather than losing data outright under a slow-consumer condition.

**Distributed correlation** across services/agents relies fundamentally on a `trace_id` generated at the edge and propagated via W3C Trace Context headers through every hop — including async/queue-based hops, where headers must be *manually* serialized into the message envelope since there's no transport-level auto-propagation for queues the way there is for HTTP. Structured-logging schemas converge on the same field set: `trace_id`, `span_id`, `parent_span_id`, plus a distinct business-level `run_id`/`conversation_id` — the separation matters operationally, since "filter by conversation" (business semantics) and "filter by trace" (technical execution path) answer different debugging questions and collapsing them into one field loses one of the two views.

### 4.2 Circuit breakers for the observability backend itself — fail-open by design

The single most important resilience principle in this domain, stated explicitly in OTel's own error-handling spec: **SDKs/APIs must not throw unhandled exceptions at runtime due to telemetry failures.** They may fail fast only at initialization (bad config caught early); once in steady-state operation, an export failure, a Collector outage, or a malformed span must never crash or degrade the *agent's own* control flow. This is the telemetry-specific instance of a general resilience principle: an ancillary system's failure must never cascade into the primary system it's observing.

The reference resilience pattern combines:

1. **Per-signal isolation** — separate bounded executor pools for logs/traces/metrics so a timeout storm in one signal type cannot starve the others.
2. **Bounded export calls** — every export call runs under a hard timeout (`future.result(timeout=...)`), never an unbounded blocking call.
3. **Circuit breaker, CLOSED → OPEN → HALF_OPEN**, tripping after a small number of consecutive timeouts/failures (3 is a commonly cited threshold), with a cooldown (30s is typical) before a half-open probe.
4. **`fail_open=true` as the default** — when the circuit is open, telemetry is silently dropped rather than raising into the calling application. A `fail_open=false` mode exists for teams that explicitly prefer a hard failure over silent data loss, but this must be an opt-in, not the default, precisely because the default behavior of an *observability* subsystem should never be "take down the thing it's observing."
5. **Graceful-degradation ladder** — `TracerProvider` with a live OTLP exporter → `TracerProvider` with a no-op exporter → no-op tracer objects entirely; the same ladder applies to `MeterProvider`. Structured logging is kept independently functional (console/JSON fallback) regardless of OTLP export health, since logs are frequently the last signal available when the rest of the telemetry pipeline is degraded.

At the Collector layer, the equivalent breaker tracks `rate(exporter_send_failed_spans) / rate(exporter_sent_spans)` and opens above a failure-rate threshold (e.g., >10% over 5 minutes), routing to a dead-letter/fallback path until the backend recovers.

### 4.3 Failure taxonomy: transient vs. permanent, poison-pill detection, idempotency

| Class | Examples | Handling |
|---|---|---|
| Transient | Backend 5xx/timeout, network blip, Collector restart, DNS resolution failure | Retry with exponential backoff + jitter (§5); circuit-break after repeated failures, not after the first |
| Permanent | Malformed OTLP payload, schema-version mismatch, auth failure to the backend, exceeding a hard cap (LangSmith's 25,000-runs-per-trace ceiling, §2.4) | Never retry — route directly to dead-letter; retrying a permanently-malformed payload wastes the retry budget without any chance of success |
| Poison-pill | A specific span/attribute combination that deterministically crashes the same exporter or redaction processor on every retry (e.g., a malformed `$ref` in an attribute value that trips a validator bug) | Detect via repeated-failure-on-identical-payload hashing (structurally identical to the tool-loop-detection pattern used elsewhere in agent systems); quarantine to dead-letter after N identical failures rather than retrying indefinitely |

**Idempotency keys** matter specifically for the *export* path when a retry follows an ambiguous-outcome timeout: if a batch export to the backend times out but may have actually succeeded server-side, a naive retry can double-write the batch. Tagging each export batch with a content-derived idempotency key (e.g., a hash of the batch's span IDs) lets the backend deduplicate on ingest rather than requiring the exporter to know definitively whether the prior attempt succeeded.

### 4.4 Checkpointing and dead-letter handling for trajectory data

Beyond §2.5's replay/fork mechanics, the resilience-specific concern is: what happens when a checkpoint write itself fails? The safe default is to treat checkpoint persistence as a **blocking, retried write** (unlike telemetry export, which fails open) — because unlike a dropped trace span, a dropped checkpoint means the agent's actual resumable state is lost, which is a correctness issue, not just an observability gap. Checkpoint writes should therefore sit outside the fail-open telemetry path entirely, backed by their own retry-with-backoff and, on repeated failure, an explicit halt-and-alert rather than silent continuation — the asymmetry (telemetry fails open, state fails closed) is a deliberate architectural choice, not an oversight.

### 4.5 Zero-Trust MCP applied to observability data — protocol-specific treatment

Treating "Zero-Trust MCP" as a generic access-control slogan misses what's actually specific to the MCP protocol in an observability context. Four concrete MCP-protocol touchpoints matter:

1. **Tool manifest/schema verification appearing in traces.** Every `execute_tool` span for an MCP-routed call should carry, as span attributes or a linked span event, the **manifest hash/version** of the tool schema that was actually presented to the model at call time — not just the tool name. Without this, a trace showing a tool call that "failed validation" is undiagnosable after the fact if the tool's schema has since changed upstream; the trace becomes a snapshot of an execution against a schema version that may no longer exist to inspect. Concretely: `mcp.tool.manifest_hash`, `mcp.tool.schema_version` as first-class span attributes, populated at the MCP Gateway/proxy layer (§1) before the call is dispatched.
2. **Capability-negotiation handshake logging.** MCP's initialization handshake negotiates which capabilities (tools, resources, prompts, sampling) a client and server support before any tool call happens. This handshake is a natural place for a downgrade or scope-creep attack to hide — a compromised or misconfigured MCP server could silently advertise broader capabilities than intended. The handshake itself (negotiated capability set, protocol version, server identity) must be logged as a distinct span, correlated to the session's subsequent `execute_tool` spans, so an auditor can answer "what capabilities did this session actually negotiate" independent of what any individual tool call later claimed.
3. **Resource scoping for trace data.** MCP's `resources/*` methods expose read access to server-side resources (files, database records, documents) alongside tool calls. Trace data *about* a resource access (which URI was read, at what scope) is itself sensitive — the trace becomes a map of what data an agent touched, which is exactly the kind of secondary-exposure surface that must be scoped by RBAC (§4.6) independent of whether the original resource access was authorized. A trace store granting broad read access to "all traces" effectively grants broad *retrospective* visibility into every resource any agent touched, which can exceed what any single team is authorized to know about another team's data access.
4. **Per-tool allow-listing enforced at the Gateway, logged at the trace layer.** The MCP Gateway (§1) is the single policy-enforcement point deciding which tools a given agent/session identity may invoke; every allow/deny decision — including denials — must be emitted as its own span or span event (`mcp.tool.rbac_decision = "denied"`, with reason code), not just silently swallowed. This mirrors the general "log denials, not just successes" audit principle (§4.8) but is specifically necessary for MCP because the protocol's dynamic tool-discovery model (`tools/list`) means the allow-listed set is not a static config an auditor can read once — it must be reconstructed from the trace/log stream itself.

Uber's production zero-trust architecture generalizes this at scale: a **per-hop token exchange (STS)** propagates the full actor chain (originating user → every intermediate agent → downstream tool) through every hop, so authorization *and* observability share the same instrumentation path — attribution is a byproduct of the security mechanism rather than a separately-bolted-on logging concern. An **MCP Gateway** serves as the single policy-enforcement point for all agent-to-service calls across Uber's 10,000+ internal services, adopted across thousands of internal agents with P99 token-exchange latency held under 40ms even for tasks involving dozens of tool/agent delegations — demonstrating that this level of per-hop verification is achievable without materially degrading agent latency at enterprise scale.

### 4.6 Tool-level RBAC for trace/log access

RBAC for observability data needs to operate at two layers simultaneously:

- **Backend query ACLs** — the conventional layer: a role can query traces/logs only within its authorized project/tenant scope.
- **Collector-level RBAC-as-code (defense-in-depth)** — per-team visible/hidden/hashed attribute lists compiled directly into Collector transform-processor statements, so a payments team's Collector strips `internal.debug.*` attributes while a platform team's Collector strips `payment.*`/`user.email` **before storage**, independent of whether the backend's own access-control logic is correctly configured. The load-bearing property: data a team isn't authorized to see is never *stored* in a form that team's queries could reach, rather than being stored-then-access-controlled — a strictly stronger guarantee since it doesn't depend on the backend ACL implementation being bug-free.

### 4.7 PII filtering — detect → redact → audit, as a layered architecture

The central tension: the payload most valuable for debugging (full prompt + full completion) is exactly the free-text field where PII and secrets concentrate. A trace store of raw LLM I/O is, functionally, an unsecured copy of every user conversation unless deliberately hardened. The converging recommended architecture is **layered**, not single-point:

1. **In-process `SpanProcessor` layer (before the span leaves the process)** — the *only* layer that protects against a misconfigured or compromised downstream Collector. Implemented as a single choke-point wrapper every span passes through (testable, auditable as one unit) rather than per-call-site scrubbing, which silently fails to cover a forgotten wrapper the next time someone adds a new instrumented call site.
2. **OTel Collector `redaction` processor as backstop** — a default-deny allow-list of attribute keys, regex-based masking (`blocked_values`) for structured PII (email, SSN, credit card patterns), and key-pattern masking (`blocked_key_patterns`, e.g. `.*token.*`, `.*api[_-]?key.*`) for secrets that might leak into arbitrary attribute names.
3. **Deterministic hashing (SHA-256/HMAC)** for fields needed for cross-trace correlation (e.g., user ID) without exposing the raw identifier — preserves joinability for debugging without storing the identifier in reversible form.

**Default-deny is the load-bearing rule**: any new attribute not explicitly allow-listed is dropped rather than passed through by default — the inverse policy (allow unless blocked) silently leaks the next new attribute a developer adds (e.g., `user.phone`) until the next manual audit catches it, which for a fast-moving codebase can be months. A **written, auditable redaction policy** — field, classification, action, retention, as four explicit columns — is the artifact a security team can actually sign off on; "we redact some stuff in a processor" is explicitly not an auditable control.

Regulatory backdrop: the EU AI Act (fully applicable from August 2, 2026) requires activity logs, risk assessments, and human-oversight documentation for high-risk AI systems — pushing PII governance for traces from a "good practice" into a compliance requirement for a growing set of deployments. *(Secondhand citation, not independently verified: one widely-cited industry breach report claims 63% of breached organizations lacked AI governance policies and 97% of AI-related breaches occurred at organizations without proper AI access controls, with average cost per incident ~$670,000 — treat these specific figures as directional, not audited.)*

### 4.8 Auditability — immutable logs and chain-of-custody

Effective audit-trail schema, converged across independent sources: **actor** (explicitly distinguishing "who initiated" from "what service executed on their behalf" — important when a service account queries a trace store on behalf of a human), **target resource** (which trace/log/checkpoint), **action/verb**, **timestamp**, and **context** (session ID, source IP, and — using the trace ID itself as an audit-correlation key — which trace the query touched). Every access **attempt**, not just successful ones, should be logged: a `403` against the trace-query API is itself security-relevant signal, and grouping denied-access events by actor is the standard pattern for detecting brute-force or privilege-escalation probing against the observability backend's own query API (directly analogous to Kubernetes audit-log guidance for its own API server).

**The audit log is itself a high-value target** — a complete record of who accessed what trace data doubles as a map of "potentially compromising actions" if it leaks. It must be independently access-controlled (a role authorized to *read* traces should not automatically be authorized to read the *audit log of who read traces*) and encrypted at rest, with write-once/append-only (WORM) storage so a compromised operator credential cannot retroactively cover its own tracks.

---

## 5. Production Enterprise Code

The module below implements a runnable agent-instrumentation resilience layer: retries with exponential backoff + jitter for the exporter, a circuit breaker (`CLOSED`→`OPEN`→`HALF_OPEN`) scoped to the observability backend with **fail-open** as the default so telemetry failures never propagate into the agent's control flow, a fallback export chain (primary OTLP-style backend → local disk dead-letter → in-memory no-op), an in-process PII-redaction span processor (default-deny allow-list), and structured JSON logging correlated by `trace_id`/`run_id`. Standard library only.

```python
"""
agent_observability.py

Production-grade agent instrumentation/telemetry resilience layer.

Implements: retry w/ backoff+jitter for span export, a circuit breaker
(CLOSED -> OPEN -> HALF_OPEN) scoped to the telemetry backend with
fail_open=True by default (Sec 4.2's core invariant: an observability
failure must never crash or block the agent it observes), a fallback
export chain (primary backend -> disk-backed dead-letter queue -> no-op),
a default-deny PII-redaction span processor (Sec 4.7), and structured
JSON logging correlated by trace_id/run_id (Sec 4.1/4.8).

All backend I/O is injected as callables so this module is fully
testable without a live OTLP collector or trace backend.
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
import uuid
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Deque, Optional

# --------------------------------------------------------------------------
# 1. Structured logging with trace_id/run_id correlation (Sec 4.1, 4.8)
# --------------------------------------------------------------------------

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")
_run_id: ContextVar[str] = ContextVar("run_id", default="")


class TraceContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id.get() or "-"
        record.run_id = _run_id.get() or "-"
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("agent_observability")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s",'
            '"trace_id":"%(trace_id)s","run_id":"%(run_id)s","msg":%(message)s}'
        )
    )
    handler.addFilter(TraceContextFilter())
    logger.handlers = [handler]
    logger.propagate = False
    return logger


log = configure_logging()


class trace_scope:
    """Binds trace_id (technical execution path) and run_id (business-level
    conversation/session identifier) for every log line emitted during one
    agent turn -- the two-ID separation from Sec 4.1 that lets an operator
    filter by either axis independently."""

    def __init__(self, trace_id: Optional[str] = None, run_id: Optional[str] = None):
        self.trace_id = trace_id or str(uuid.uuid4())
        self.run_id = run_id or str(uuid.uuid4())
        self._tok_trace = None
        self._tok_run = None

    def __enter__(self) -> "trace_scope":
        self._tok_trace = _trace_id.set(self.trace_id)
        self._tok_run = _run_id.set(self.run_id)
        return self

    def __exit__(self, *exc_info) -> None:
        _trace_id.reset(self._tok_trace)
        _run_id.reset(self._tok_run)


# --------------------------------------------------------------------------
# 2. Span model (minimal, GenAI-convention-inspired) (Sec 2.1)
# --------------------------------------------------------------------------

@dataclass
class Span:
    name: str                      # e.g. "execute_tool crm.create_ticket"
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    attributes: dict[str, Any] = field(default_factory=dict)
    start_ts: float = field(default_factory=time.time)
    end_ts: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name, "trace_id": self.trace_id, "span_id": self.span_id,
            "parent_span_id": self.parent_span_id, "attributes": self.attributes,
            "start_ts": self.start_ts, "end_ts": self.end_ts,
        }


# --------------------------------------------------------------------------
# 3. PII redaction span processor -- default-deny allow-list (Sec 4.7)
# --------------------------------------------------------------------------

class RedactingSpanProcessor:
    """The in-process choke-point every span passes through before export.
    Default-deny: any attribute key not explicitly allow-listed is dropped,
    not passed through -- Sec 4.7's load-bearing rule. Regex-based value
    masking catches structured PII that leaks through an allow-listed key
    (e.g. a free-text 'notes' field containing an email address)."""

    _EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
    _SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
    _SECRET_KEY_RE = re.compile(r".*(token|api[_-]?key|secret|password).*", re.IGNORECASE)

    def __init__(self, allowed_attribute_keys: set[str]):
        self.allowed_attribute_keys = allowed_attribute_keys

    def process(self, span: Span) -> Span:
        clean_attrs: dict[str, Any] = {}
        for key, value in span.attributes.items():
            if self._SECRET_KEY_RE.match(key):
                log.info(json.dumps({"event": "attribute_dropped_secret_key_pattern", "key": key}))
                continue
            if key not in self.allowed_attribute_keys:
                log.info(json.dumps({"event": "attribute_dropped_default_deny", "key": key}))
                continue
            clean_attrs[key] = self._mask_value(value)
        span.attributes = clean_attrs
        return span

    def _mask_value(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        value = self._EMAIL_RE.sub("[REDACTED_EMAIL]", value)
        value = self._SSN_RE.sub("[REDACTED_SSN]", value)
        return value


# --------------------------------------------------------------------------
# 4. Circuit breaker for the telemetry BACKEND -- fail-open by default
#    (Sec 4.2: an observability failure must never crash the agent)
# --------------------------------------------------------------------------

class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class TelemetryCircuitBreaker:
    name: str                       # e.g. "otlp_backend:primary"
    failure_threshold: int = 3      # consecutive failures to trip
    cooldown_s: float = 30.0
    half_open_max_probes: int = 1

    _state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    _consecutive_failures: int = field(default=0, init=False)
    _opened_at: float = field(default=0.0, init=False)
    _half_open_probes_used: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def allow_request(self) -> bool:
        with self._lock:
            if self._state == BreakerState.OPEN:
                if time.monotonic() - self._opened_at >= self.cooldown_s:
                    self._state = BreakerState.HALF_OPEN
                    self._half_open_probes_used = 0
                    log.info(json.dumps({"event": "breaker_half_open", "backend": self.name}))
                else:
                    return False
            if self._state == BreakerState.HALF_OPEN:
                if self._half_open_probes_used >= self.half_open_max_probes:
                    return False
                self._half_open_probes_used += 1
            return True

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            if self._state == BreakerState.HALF_OPEN:
                self._state = BreakerState.CLOSED
                log.info(json.dumps({"event": "breaker_closed", "backend": self.name}))

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._state == BreakerState.HALF_OPEN:
                self._trip("half_open_probe_failed")
                return
            if self._consecutive_failures >= self.failure_threshold:
                self._trip("consecutive_failure_threshold")

    def _trip(self, reason: str) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = time.monotonic()
        log.info(json.dumps({"event": "breaker_open", "backend": self.name, "reason": reason}))

    @property
    def state(self) -> BreakerState:
        return self._state


# --------------------------------------------------------------------------
# 5. Retry with full jitter, for exporter calls only (transient errors)
#    (Sec 4.3's failure taxonomy: never retry permanent/poison-pill errors)
# --------------------------------------------------------------------------

class TelemetryExportError(Exception):
    def __init__(self, message: str, transient: bool = True):
        super().__init__(message)
        self.transient = transient


def backoff_with_full_jitter(attempt: int, base_s: float = 0.1, cap_s: float = 4.0) -> float:
    return random.uniform(0, min(cap_s, base_s * (2 ** attempt)))


def export_with_retry(fn: Callable[[], Any], max_attempts: int = 3) -> Any:
    last_error: Optional[TelemetryExportError] = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except TelemetryExportError as exc:
            last_error = exc
            if not exc.transient:
                raise
            if attempt < max_attempts - 1:
                delay = backoff_with_full_jitter(attempt)
                log.info(json.dumps({"event": "export_retry_backoff", "attempt": attempt + 1,
                                      "delay_s": round(delay, 3)}))
                time.sleep(delay)
    assert last_error is not None
    raise last_error


# --------------------------------------------------------------------------
# 6. Dead-letter queue (disk-backed) for spans that exhaust every fallback
#    (Sec 4.3/4.4: durable parking, not silent loss)
# --------------------------------------------------------------------------

class DeadLetterQueue:
    def __init__(self, path: str = "./telemetry_dlq.jsonl"):
        self.path = Path(path)

    def park(self, span: Span, reason: str) -> None:
        record = {"reason": reason, "parked_at": time.time(), "span": span.to_dict()}
        with self.path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
        log.info(json.dumps({"event": "span_parked_dlq", "span_id": span.span_id, "reason": reason}))


# --------------------------------------------------------------------------
# 7. Instrumentation layer: redact -> export (breaker-guarded, retried) ->
#    dead-letter fallback -> no-op. Fail-open end to end (Sec 4.2, 4.7)
# --------------------------------------------------------------------------

@dataclass
class AgentInstrumentation:
    primary_export_fn: Callable[[Span], None]     # e.g. OTLP exporter call
    redactor: RedactingSpanProcessor
    breaker: TelemetryCircuitBreaker
    dlq: DeadLetterQueue
    fail_open: bool = True   # Sec 4.2: default MUST be True for production agents

    def emit_span(self, span: Span) -> str:
        """Returns the outcome tier: 'exported', 'dead_lettered', or
        'dropped_fail_open'. Never raises into the caller's control flow
        when fail_open=True -- the entire point of this method."""
        clean_span = self.redactor.process(span)

        if self.breaker.allow_request():
            try:
                export_with_retry(lambda: self.primary_export_fn(clean_span))
                self.breaker.record_success()
                return "exported"
            except TelemetryExportError as exc:
                self.breaker.record_failure()
                log.info(json.dumps({"event": "export_failed", "span_id": span.span_id,
                                      "reason": str(exc)}))
        else:
            log.info(json.dumps({"event": "export_skipped_breaker_open", "span_id": span.span_id}))

        # Fallback tier: durable disk dead-letter rather than silent loss
        try:
            self.dlq.park(clean_span, reason="primary_backend_unavailable")
            return "dead_lettered"
        except OSError as exc:
            # Disk itself is unavailable -- this is the true last resort.
            if not self.fail_open:
                raise
            log.info(json.dumps({"event": "span_dropped_fail_open", "span_id": span.span_id,
                                  "reason": str(exc)}))
            return "dropped_fail_open"


# --------------------------------------------------------------------------
# Example wiring: an agent tool call emits a span through the full chain
# --------------------------------------------------------------------------

if __name__ == "__main__":
    def flaky_otlp_backend(span: Span) -> None:
        if random.random() < 0.7:
            raise TelemetryExportError("backend 503", transient=True)

    instrumentation = AgentInstrumentation(
        primary_export_fn=flaky_otlp_backend,
        redactor=RedactingSpanProcessor(allowed_attribute_keys={
            "gen_ai.operation.name", "gen_ai.request.model", "gen_ai.agent.name",
            "gen_ai.tool.name", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
            "mcp.tool.manifest_hash", "mcp.tool.rbac_decision",
        }),
        breaker=TelemetryCircuitBreaker(name="otlp_backend:primary", failure_threshold=3, cooldown_s=5),
        dlq=DeadLetterQueue(path="./telemetry_dlq.jsonl"),
        fail_open=True,
    )

    with trace_scope() as ctx:
        log.info(json.dumps({"event": "agent_turn_start"}))
        span = Span(
            name="execute_tool crm.create_ticket",
            trace_id=ctx.trace_id,
            span_id=str(uuid.uuid4()),
            parent_span_id=None,
            attributes={
                "gen_ai.tool.name": "crm.create_ticket",
                "mcp.tool.manifest_hash": "sha256:9f2a...",
                "mcp.tool.rbac_decision": "allowed",
                "user.email": "customer@example.com",  # will be default-deny dropped
                "internal.debug.raw_prompt": "full prompt text...",  # dropped: not allow-listed
            },
        )
        outcome = instrumentation.emit_span(span)
        log.info(json.dumps({"event": "agent_turn_complete", "telemetry_outcome": outcome}))
```

This demonstrates the full chain end to end: the redactor strips `user.email` and `internal.debug.raw_prompt` because neither is on the allow-list (default-deny, §4.7), independent of whether the backend export even succeeds; the flaky backend (70% failure rate) exercises retry-with-jitter and trips the breaker within 3 consecutive failures, at which point subsequent spans skip the export attempt entirely rather than paying the timeout cost again; every span that can't reach the primary backend is durably parked to a disk-backed dead-letter queue instead of being dropped; and only if the disk itself is unavailable does the system fall through to `fail_open`-gated silent drop — the true last resort, logged as its own distinct event so "we are currently losing telemetry" remains a discoverable condition even in the degraded case.

---

## 6. Architectural System Design Scenarios

### Scenario A — Multi-agent enterprise platform choosing between self-hosted and SaaS observability at scale

**Problem statement.** A platform team runs ~200 internal agents across dozens of teams, generating an estimated 400–600GB/day of raw telemetry once tool-call spans, retrieval spans, and structured logs are combined. Leadership wants full visibility into agent trajectories for debugging and cost attribution, security wants PII exposure minimized, and finance wants predictable cost as agent adoption grows 3–5x over the next year. The team must choose an observability backend architecture and a sampling policy.

**Proposed architecture.**

```
200 internal agents (Agent FX-style standardized SDK, Sec 4.5)
                              │
              in-process RedactingSpanProcessor (Sec 4.7, default-deny)
                              │
                    Tier-1 Collector (per-cluster sidecar/DaemonSet)
                    loadbalancingexporter, hash on trace_id
                              │
              ┌───────────────┴────────────────┐
              ▼                                 ▼
   Tier-2 Gateway Collector             spanmetrics/RED connector
   (tail_sampling: 100% errors/        (FULL pre-sampling volume,
    P95+ latency, 5% baseline;         parallel pipeline -- Sec 3.3's
    redaction backstop, Sec 4.7)       "never derive RED from sampled
              │                        data" invariant)
              ▼
   Self-hosted Tempo + ClickHouse (Langfuse-pattern) on object storage,
   SharedMergeTree for storage/compute separation, CLICKHOUSE_READ_ONLY_URL
   isolating ingestion writes from analytical/dashboard read traffic
              │
              ▼
   RBAC-as-code at Collector layer (Sec 4.6) + WORM audit log (Sec 4.8)
   of every trace-store query, independent of the trace store itself
```

**Trade-off evaluation matrix.**

| Dimension | Full SaaS (Datadog/Honeycomb, no sampling) | Self-hosted, no sampling (Tempo+ClickHouse, 100% retention) | Self-hosted, hybrid sampling (proposed) |
|---|---|---|---|
| Cost / 1k runs | Highest — $14–21k/mo-class pricing scales linearly with raw volume at this scale (§3.1 benchmark) | Lower raw storage (~$0.023/GB object storage) but 100% retention means storage grows unbounded with agent adoption | Lowest sustained cost — hybrid sampling cuts retained volume by ~80–98% (§2.3) while still capturing all errors/outliers; storage growth decoupled from raw traffic growth |
| Latency P95 (query) | Low — managed, professionally tuned query engines | Medium — self-managed ClickHouse requires tuning (`SharedMergeTree`, read/write URL separation) to avoid ingestion write pressure degrading dashboard queries | Medium, same as above, but query volume is smaller since retained trace count is lower, partially offsetting self-hosting's query-tuning burden |
| Ops complexity | Lowest — vendor-managed | Highest — requires dedicated ClickHouse/Collector operational expertise, upgrade cadence ownership, on-call | High but bounded — same self-hosting burden, with the added (one-time) complexity of designing and validating tail-sampling policies correctly (§2.6's routing-tier requirement) |
| Security / data residency | Data leaves the VPC unless an expensive BYOC/enterprise self-hosted tier is purchased | Full control — required for strict data-sovereignty/VPC-only mandates | Full control, plus smaller retained-data footprint reduces the blast radius of any future trace-store breach |
| Scalability under 3–5x adoption growth | Cost scales linearly with volume — a 3–5x growth in agents means a roughly 3–5x SaaS bill | Storage/ops burden scales linearly with volume at 100% retention — same problem, self-hosted | Cost and storage scale sub-linearly, since sampling ratio can be tuned independent of agent count — this is the specific property that makes it viable under fast adoption growth |

**Decision rationale.** The proposed hybrid-sampling, self-hosted architecture is chosen because the 3–5x adoption growth constraint rules out any option whose cost scales linearly with raw agent-call volume — both the full-SaaS and full-retention self-hosted options fail this test even though they differ substantially in absolute cost today. Self-hosting is justified over SaaS specifically because of the data-sovereignty requirement (agent tool-call traces routinely contain customer data flowing through MCP tool calls, §4.5) combined with the platform's scale already sitting well above the ~50GB/day self-hosting crossover point (§3.1) — SaaS convenience is not worth its cost premium at this volume. Hybrid sampling (rather than 100% retention) is the one lever that decouples cost/ops growth from agent-count growth, and the parallel full-volume `spanmetrics` pipeline is non-negotiable specifically because leadership wants trustworthy RED/cost-attribution dashboards, which §3.3 shows become statistically biased if computed from the sampled stream alone.

### Scenario B — Regulated-industry (healthcare) agent platform requiring compliance-grade audit trails under strict PII exposure limits

**Problem statement.** A healthcare-adjacent company deploys clinical-intake agents that read and summarize patient-reported symptoms via MCP-connected EHR tool calls. Compliance requires a complete, auditable record of every tool call and every piece of data the agent accessed (for HIPAA accountability and EU AI Act high-risk-system activity-logging obligations), but the same data is maximally sensitive PHI — the worst possible content to have widely queryable in a shared trace store. The naive "just enable full tracing" approach is a direct compliance liability; the naive "sample and drop content" approach fails the audit-completeness requirement.

**Proposed architecture.**

```
Clinical-intake agent → execute_tool spans wrap every EHR MCP tool call,
tagged with mcp.tool.manifest_hash + capability-negotiation handshake
span (Sec 4.5) -- full audit completeness at the STRUCTURAL span level,
independent of content-capture mode
                              │
        Content-capture mode = NO_CONTENT for the default span/attribute
        stream (Sec 2.1) -- structural trace (who called what tool, when,
        with what manifest version) is retained at 100%, unsampled
                              │
        Full prompt/completion/PHI-bearing tool arguments routed via the
        `upload` completion hook (Sec 2.1) to a SEPARATE, PHI-scoped
        object-storage bucket with independent encryption keys and a
        MUCH stricter RBAC policy than the structural trace store --
        only a reference URI (gen_ai.input.messages.ref) lives on the span
                              │
              ┌───────────────┴────────────────┐
              ▼                                 ▼
   Structural trace store (Tempo)      PHI content store (separate bucket,
   -- broad internal query access       envelope-encrypted, access-logged
   for engineering/on-call debugging    per-object, HIPAA-minimum-necessary
   of tool-call flow and latency        RBAC -- only compliance/clinical-
                                        review roles can dereference a ref)
              │                                 │
              └───────────────┬─────────────────┘
                               ▼
              WORM audit log (Sec 4.8): every dereference of a PHI content
              ref is its own audited event, chain-of-custody complete even
              though the two data stores have independent access policies
```

**Trade-off evaluation matrix.**

| Dimension | Full tracing, single store, `SPAN_AND_EVENT` everywhere | Aggressive sampling to control PHI exposure | Structural/content split via upload-hook reference (proposed) |
|---|---|---|---|
| Cost / 1k runs | Highest (§3.1's `SPAN_AND_EVENT` profile, ~$16.8/1k runs) plus the compliance cost of securing one large mixed-sensitivity store | Lowest, but directly fails the audit-completeness requirement — compliance cannot accept "we have 90% of the audit trail" | Moderate — structural trace stays cheap and unsampled (100% coverage, `NO_CONTENT` mode is far smaller per span); PHI content store cost is isolated and scoped only to what compliance actually needs to review, not every span |
| Audit completeness | Complete, but at maximum exposure | Incomplete by construction — disqualifying for a HIPAA/EU AI Act activity-log requirement | Complete at the structural level (100%, unsampled, satisfies "who accessed what tool when"); PHI content is retained but access-gated rather than broadly queryable |
| PII/PHI exposure | Maximum — every engineer with trace-store access can read raw patient-reported symptoms in any debugging session | Reduced volume, but any retained trace with content capture still carries the same per-trace exposure risk | Minimized — the broadly-accessible structural store never carries PHI at all; PHI lives behind a second, independently-keyed access boundary that only compliance-authorized roles can dereference |
| Ops complexity | Lowest to build (one store, one policy) | Low to build, but requires a defensible, audit-survivable justification for *why* sampling doesn't violate the activity-logging requirement — a hard sell to a compliance/legal reviewer | Highest to build (two stores, a reference-resolution flow, two independent RBAC policies, dereference-level audit logging) but this is the direct, structural cost of satisfying both requirements simultaneously rather than trading one off against the other |
| Scalability / security posture | Poor on both fronts as volume grows — the single store's blast radius grows with data volume | Good cost/ops scalability but permanently poor compliance posture | Strong on both — structural trace scales cheaply since it never carries the sensitive payload; PHI store scales independently and its access pattern (rare, audited dereferences) is a much smaller and more defensible attack surface than "every debugging session touches raw PHI" |

**Decision rationale.** The structural/content split is selected because the other two options represent a false binary this specific requirement doesn't have to accept: full tracing conflates "audit completeness" with "content completeness," when in fact the compliance requirement (who accessed what tool, when, under what authorization) is satisfiable entirely at the **structural** span level with `NO_CONTENT` mode, while the PHI-bearing payload only needs to exist in a narrowly-scoped, independently-access-controlled store that a small compliance/clinical-review population can dereference. This directly applies §2.1's `upload` completion-hook mechanism — designed exactly for decoupling bulky/sensitive content from the trace backend — as a compliance control rather than merely a cost optimization. It also satisfies §4.5 and §4.8 simultaneously: MCP tool-call manifest/handshake logging gives complete structural audit coverage of every EHR interaction regardless of content-capture mode, and every PHI-reference dereference becomes its own independently-audited event, producing a chain-of-custody record that a HIPAA or EU AI Act auditor can inspect without ever needing broad read access to the PHI itself.
