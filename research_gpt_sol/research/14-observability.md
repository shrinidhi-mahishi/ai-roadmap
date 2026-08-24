# Research: Observability — Tracing, Logging, Monitoring, and Agent Trajectories

**Date researched**: 2026-08-21  
**Sources consulted**: 48

Agent observability is the ability to explain **what ran, why it ran, what state it observed and changed, how well it performed, and whether the telemetry itself is complete**. It extends conventional service observability because an agent's execution path is selected at runtime, may span many model and tool turns, and can succeed technically while failing the user's goal or policy.

The four named signals answer different questions:

- **Tracing:** which causally related operations occurred across services, queues, agents, models, retrieval, and tools?
- **Logging:** which discrete events and evidence must be searched, audited, or retained independently of a trace?
- **Monitoring:** is the aggregate system meeting availability, latency, quality, safety, and cost objectives now?
- **Agent trajectories:** how did goal, plan, evidence, decisions, actions, environment state, and outcome evolve step by step?

A production system needs all four. A trace without state deltas cannot prove that a tool changed the intended resource; raw logs without context cannot reconstruct causality; metrics reveal a regression but rarely explain one; and a trajectory viewer without telemetry-pipeline health can confidently display an incomplete history.

## 1. System Topology & Mechanics

### 1.1 Telemetry control plane and data plane

```text
 Users / API / scheduler
          |
          v
 Agent runtime ---- model / retrieval / memory / tools / subagents
    |  |  |  |                 |
    |  W3C trace context across HTTP, RPC, queues and workers
    |                           |
    +---- traces + logs + metrics + trajectory/state events
                                |
                        local/sidecar Collector
                                |
                    regional gateway Collectors
                   /       |          |          \
            metrics TSDB  trace DB   log store   evidence/object store
                   \       |          |          /
                    dashboards / alerting / search / graders
                                |
                   incidents / eval datasets / release gates

 Control plane: schemas, instrumentation policy, sampling, redaction,
 retention, access, routing, SLOs, alerts, collector config and audit.
```

The instrumentation/data plane emits signals; the observability control plane defines their schema, content policy, sampling, access, retention, and alert rules `[inferred]`. OpenTelemetry (OTel) provides vendor-neutral APIs, SDKs, data models, semantic conventions, and OTLP transport [[1]](https://opentelemetry.io/docs/specs/otel/) [[6]](https://opentelemetry.io/docs/specs/otlp/). Its Collector can receive, process, and export traces, metrics, and logs to one or more backends [[15]](https://opentelemetry.io/docs/collector/).

Use a local Agent/DaemonSet or sidecar Collector for low-latency batching and resource enrichment, then regional gateway Collectors for central redaction, routing, tail sampling, and credentials `[inferred]`. OTel documents the gateway topology and, for stateful processors such as tail sampling, a two-tier pattern that routes all spans with one trace ID to the same downstream Collector [[16]](https://opentelemetry.io/docs/collector/deploy/gateway/).

### 1.2 Trace model

A **trace** represents one logical workflow; a **span** represents a timed operation; a span **event** represents an occurrence inside that operation; a **link** relates causally associated spans that do not fit a single parent-child tree `[inferred]`. W3C Trace Context standardizes `traceparent` and `tracestate` headers so trace identity survives vendor and service boundaries [[7]](https://www.w3.org/TR/trace-context/). OTel can inject trace and span IDs into logs, enabling precise trace-log correlation [[9]](https://opentelemetry.io/docs/concepts/context-propagation/) [[8]](https://opentelemetry.io/docs/specs/otel/logs/).

Recommended hierarchy `[inferred]`:

```text
agent.run                         root: one user/business task attempt
  input.guardrail
  agent.invoke                    current agent execution
    context.assemble
      memory.search
      retrieval.query
      retrieval.rerank
    agent.plan                    optional observable plan artifact
    model.inference               request through final chunk/error
    tool.execute                  logical call, including SDK retries
      policy.authorize
      tool.transport              HTTP/RPC/database span
      environment.state_delta
    agent.handoff                 delegation to child agent
      agent.invoke                child trace/span, linked across queue
    approval.wait
    output.guardrail
  outcome.verify
```

Name spans by stable operation, not user text, document title, URL, prompt, or generated tool arguments. Put bounded dimensions in attributes and large/high-cardinality content in an access-controlled evidence store. A root should carry `service.name`, deployment/environment, workflow name/version, run ID, tenant pseudonym, agent/harness/prompt/tool/policy versions, and release/experiment cohort. Child spans carry model/provider, retrieval collection, tool/action, retry, status, and token attributes `[inferred]`.

Async boundaries need explicit propagation. Inject context into queue/task metadata, extract it in the worker, and use links when one consumer processes a batch from several producers or one event fans out to several tasks. A conversation/thread ID groups multiple task traces but must not replace a globally unique trace ID `[inferred]`. LangSmith's distributed tracing propagates its run context across services with headers, while W3C Trace Context is the portable cross-vendor baseline [[38]](https://docs.langchain.com/langsmith/distributed-tracing) [[7]](https://www.w3.org/TR/trace-context/).

Do not trust incoming baggage as authorization or identity. OTel warns that baggage is automatically propagated in network headers, can leak to third parties, and has no built-in integrity check [[10]](https://opentelemetry.io/docs/concepts/signals/baggage/). Propagate only allowlisted pseudonymous routing/debug attributes; obtain authenticated tenant/user identity from the application security context.

### 1.3 OpenTelemetry GenAI semantics

As of the research date, core OTel semantic conventions are version 1.44.0, while GenAI conventions have moved to a dedicated repository and GenAI spans remain in **Development** status [[2]](https://opentelemetry.io/docs/specs/semconv/) [[3]](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md). Pin the semantic-convention revision used by each instrumentation library, record it as telemetry metadata, centralize translations, and test dashboards during upgrades `[inferred]`.

The current GenAI convention covers inference, embeddings, retrieval, response fetch, memory, and `execute_tool` spans. Known operation names include `invoke_agent`, `invoke_workflow`, `plan`, `retrieval`, memory CRUD/search, and `execute_tool`; tool execution is recommended as `execute_tool {gen_ai.tool.name}` [[3]](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md). The metric draft defines client token usage and operation duration, streaming time-to-first-chunk/time-per-output-chunk, server request/first-token/per-token duration, workflow/agent duration, inference/tool-call counts, and tool duration [[4]](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md).

Important GenAI attributes include provider, requested/response model, operation, conversation, response ID/status, input/output/cache/reasoning token counts, tool name/type/call ID, and error type [[5]](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/). Do not use raw user ID, prompt, response, query, document, or tool arguments as metric labels. Many content fields are explicitly sensitive.

OpenInference is a separate AI-focused semantic convention layered on valid OTel spans. It defines AI span kinds such as `LLM`, `AGENT`, `CHAIN`, `TOOL`, `RETRIEVER`, `RERANKER`, `GUARDRAIL`, and `EVALUATOR`, along with token, message, evaluation, and tool attributes [[43]](https://github.com/Arize-ai/openinference/tree/main/spec). Phoenix uses OpenTelemetry/OTLP with OpenInference instrumentation to visualize model, retrieval, tool, and custom application spans [[42]](https://arize.com/docs/phoenix/). OTel GenAI and OpenInference are not identical schemas; select a canonical internal model and map incoming conventions at the collector or ingestion layer `[inferred]`.

### 1.4 Structured logging

Logs should be structured events, not prose that must be parsed. OTel's log model can attach time, observed time, severity, body, attributes, resource, instrumentation scope, trace ID, span ID, and trace flags [[8]](https://opentelemetry.io/docs/specs/otel/logs/). Use a stable `event.name` and schema version; store mutable explanatory text separately from machine fields `[inferred]`.

Minimum event envelope `[inferred]`:

```json
{
  "timestamp": "2026-08-21T10:31:02.418Z",
  "observed_timestamp": "2026-08-21T10:31:02.441Z",
  "severity": "INFO",
  "event_name": "agent.tool.completed",
  "schema_version": "agent-telemetry/1.3",
  "trace_id": "...",
  "span_id": "...",
  "run_id": "...",
  "step_id": 17,
  "tenant_ref": "hmac:...",
  "agent": {"name": "support", "version": "git:8fd2..."},
  "tool": {"name": "refund.lookup", "schema": "v4", "call_id": "..."},
  "status": "ok",
  "duration_ms": 143,
  "input_ref": "evidence://...",
  "output_ref": "evidence://...",
  "input_hash": "sha256:...",
  "output_hash": "sha256:...",
  "state_before": "sha256:...",
  "state_after": "sha256:...",
  "policy_decision_id": "..."
}
```

Record lifecycle events for run accepted/started/completed/cancelled, model call/retry, retrieval, tool proposal/authorization/execution, handoff, approval, checkpoint, state mutation, guardrail/evaluator result, budget threshold, and outcome verification. Log exceptions once at the owner boundary with normalized `error.type`, retryability, attempt, dependency, and terminal status; avoid duplicating the same stack at every layer `[inferred]`.

Google ADK emits standard-library logs and structured GenAI events using OTel conventions; prompt content is elided by default, and its docs recommend INFO/WARNING rather than DEBUG in production because DEBUG may contain full prompts and detailed responses [[35]](https://adk.dev/observability/logging/). The OpenAI Agents SDK traces model generations, function tools, guardrails, handoffs, and custom events and allows sensitive model/tool input-output capture to be disabled [[31]](https://openai.github.io/openai-agents-python/tracing/).

### 1.5 Monitoring

Monitoring aggregates bounded signals into time series, SLOs, dashboards, and alerts. Start with Google's four golden signals—latency, traffic, errors, saturation—and extend them with agent-specific **quality, safety, progress, and economics** [[23]](https://sre.google/sre-book/monitoring-distributed-systems/). Dashboards diagnose; alerts should page on actionable user-visible symptoms rather than every internal anomaly [[26]](https://prometheus.io/docs/practices/alerting/).

| Plane | Core metrics `[inferred]` | Typical dimensions |
|---|---|---|
| Service | admitted/completed runs, errors, queue, concurrency, CPU/memory | service, region, environment, workflow version |
| Model | calls, tokens, cache read/write, duration, TTFT, rate limit, timeout | provider, requested/response model, operation, status |
| Agent | task success, progress, turns, loop/replan/handoff, termination reason | agent/workflow version, risk tier, outcome class |
| Retrieval | query latency, candidates, recall proxy, empty/low-score results | collection/version, retriever/reranker, status |
| Tool | proposed/allowed/executed/succeeded, validation and authorization errors | tool/action/version, status, dependency |
| Quality | evaluator score distribution, groundedness, user feedback, escalation | evaluator/rubric version, cohort, language/domain |
| Safety | guardrail blocks, policy denials, injection/DLP alerts, unsafe side effects | detector/policy version, severity, attack class |
| Economics | input/output/cache/reasoning tokens, model/tool/sandbox cost, cost/success | model, workflow, tenant plan, route |
| Telemetry | exported/dropped/refused items, queue fill, export failures, sampling rate | collector tier, signal, exporter, region |

Use counters for totals, gauges for current state/queues, and histograms for latency, tokens, cost, turns, and evaluator score distributions. Preserve exemplars so an anomalous histogram bucket links to a representative trace. OTel metrics SDK defines exemplars for trace-metric correlation and a default cardinality limit of 2,000 attribute sets per stream when no other limit is configured [[14]](https://opentelemetry.io/docs/specs/otel/metrics/sdk/). This default is a guardrail, not a target.

### 1.6 Agent trajectories

Anthropic defines a transcript/trace/trajectory as the complete record of one trial, including outputs, tool calls, intermediate results, reasoning, and interactions, and distinguishes it from the final environment outcome [[44]](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents). Operationally, a trajectory should be an append-only ordered event stream plus references to immutable artifacts and environment snapshots `[inferred]`.

Per step capture `[inferred]`:

- stable `run_id`, `step_id`, parent/delegation IDs, trace/span IDs, wall and monotonic time;
- current goal/constraints, plan version, agent/prompt/model/tool/policy versions;
- observation/evidence references with source, trust, timestamp, classification, and hash;
- proposed action, normalized arguments/hash, expected effect, authorization and approval;
- actual tool request/response/receipt, retry and idempotency information;
- state-before/state-after references or domain delta, checkpoint version, conflict result;
- token, cache, cost, latency and resource usage;
- guardrail/evaluator annotations with rubric/detector version and confidence;
- termination reason, outcome assertions, user feedback, and downstream incident/eval links.

Do not require hidden chain-of-thought. It may be unavailable, sensitive, misleading, or intentionally withheld. Capture observable messages, decision outputs, structured plan/action proposals, evidence provenance, policy decisions, state transitions, and concise model-provided summaries where available. Environment state and external receipts are stronger evidence than a model's narrative of why an action succeeded `[inferred]`.

OpenAI defines trace grading as attaching structured scores or labels to the end-to-end log of decisions, tool calls, and reasoning steps; trace evals apply graders across many examples to diagnose regressions [[33]](https://developers.openai.com/api/docs/guides/trace-grading). Google ADK separates trajectory/tool-use evaluation from final-response evaluation [[36]](https://adk.dev/evaluate/). Research taxonomies such as AgentOps argue for tracing artifacts across the agent lifecycle, but the field still lacks one stable, complete cross-framework trajectory schema [[45]](https://arxiv.org/abs/2411.05285).

## 2. Token Economics & NFR Metrics

### 2.1 SLO hierarchy

Define SLOs at three layers `[inferred]`:

1. **Product:** policy-compliant task success, quality, safety, end-to-end latency, cost per accepted outcome.
2. **Agent/component:** model/tool/retrieval success and latency, loop/retry rate, token use, route quality.
3. **Telemetry:** instrumentation coverage, context propagation, export completeness, freshness, query latency, and alert delivery.

Example estimands `[inferred]`:

```text
trace_completeness = observed_required_spans / expected_required_spans
context_join_rate = spans_with_valid_parent_or_link / eligible_spans
telemetry_loss_rate = dropped_or_rejected_items / emitted_items
trajectory_replayability = replayable_runs / sampled_completed_runs
policy_compliant_success = successful_and_policy_clean_runs / valid_runs
cost_per_success = total_agent_and_observability_cost / policy_compliant_successes
```

Do not promise "exactly once telemetry." Export retries can duplicate records, application crashes can lose buffered spans, and tail sampling intentionally drops traces. Make event IDs stable, deduplicate where required, expose loss, and distinguish sampled absence from pipeline loss `[inferred]`.

### 2.2 Latency, tokens, cost, and throughput

OpenTelemetry's GenAI draft uses histograms for token usage and operation duration and includes time-to-first-chunk, time-per-output-chunk, agent/workflow duration, tool duration, and model-server time-to-first-token/per-token metrics [[4]](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md). The OpenAI Agents SDK tracks request counts and input, output, cached, cache-write, and reasoning token details for a run [[32]](https://openai.github.io/openai-agents-python/usage/).

Measure p50/p90/p95/p99 for end-to-end task, queue/admission, context assembly, each model call, TTFT, inter-chunk gap, retrieval, tool, policy, approval, checkpoint, and telemetry export. Report successful, failed, timed-out, and cancelled runs separately. End-to-end time includes retries and approval wait; model-service latency alone does not represent user experience `[inferred]`.

```text
agent_execution_cost =
  model_input + model_cache_write + model_cache_read + model_output
  + retrieval/vector/DB + tool/API + sandbox/compute + human_review

observability_cost =
  SDK_CPU_memory + collector_compute_network_queue
  + trace_log_metric_storage_indexing
  + quality_evaluator_model_tokens + human_annotation
  + query_dashboard_alert_and_archive

observability_cost_per_1k_runs =
  1000 * total_observability_cost / admitted_runs

bytes_per_run = emitted_trace_bytes + emitted_log_bytes
              + attributed_metric_bytes + evidence_artifact_bytes
```

Model content dominates payload volume more often than span metadata. Record token counts, hashes, bounded metadata, and artifact references by default; retain raw content only for approved samples/incidents. Calculate daily ingestion as `runs/day * spans/run * bytes/span`, plus logs, metric series/samples, replication, indexes, and retention tiers `[inferred]`.

The OTel performance document specifies how SDKs should benchmark span throughput, CPU, and heap and suggests a 10,000-spans/second default workload and at least ten measurements; it supplies a methodology, not universal overhead results [[21]](https://opentelemetry.io/docs/specs/otel/performance-benchmark/). Dapper demonstrated that library-level instrumentation and sampling can support ubiquitous large-scale tracing, but its 2010 Google environment is historical evidence, not a 2026 agent-stack capacity number [[22]](https://research.google/pubs/dapper-a-large-scale-distributed-systems-tracing-infrastructure/).

> ⚠️ Limited public data available for this dimension. No neutral benchmark reports full production agent-observability overhead—SDK, GenAI content capture, collectors, redaction, tail sampling, storage, evaluators, and queries—across representative 2026 frameworks and backends. Benchmark in the target workload and publish p50/p95/p99 plus loss.

### 2.3 Cardinality discipline

Metric series grow approximately as the Cartesian product of label values. Never label metrics with run/trace/span/conversation/user/document IDs, prompt text/hash, arbitrary URL, tool arguments, error messages, or tenant IDs at high scale. Keep these on sampled traces/logs; put only bounded classes on metrics `[inferred]`.

OTel's current metrics guidance explains that each unique attribute combination consumes aggregation state, sets the SDK default cardinality limit at 2,000, and folds overflow into `otel.metric.overflow=true`; queries grouped by dropped attributes then undercount even though the total remains correct [[13]](https://opentelemetry.io/docs/concepts/signals/metrics/). Prometheus recommends keeping most metric cardinality below ten and reconsidering metrics that can exceed 100, while emphasizing that these are guidelines, not backend-independent hard limits [[25]](https://prometheus.io/docs/practices/instrumentation/).

Govern cardinality `[inferred]`:

- maintain an allowlist, owner, expected cardinality, and expiry for every metric label;
- use `tool_name` only from a registry; normalize `error.type` and termination reason;
- use coarse tenant plan/risk tier rather than tenant ID;
- aggregate model aliases only when doing so preserves operational meaning;
- set SDK/backend series budgets and alert on overflow/new series rate;
- use exemplars for trace drill-down rather than trace ID labels;
- precompute recording rules for expensive recurring queries.

### 2.4 Sampling and retention

Head sampling decides before the trace outcome is known; it is cheap and statistically tractable but cannot guarantee retaining errors. Tail sampling evaluates completed/mostly completed traces and can retain errors, long latency, specific attributes, or new releases, but it is stateful and operationally harder [[11]](https://opentelemetry.io/docs/concepts/sampling/).

Recommended policy `[inferred]`:

```text
retain 100%: critical policy violations, cross-tenant attempts, unsafe side effects,
             errors, explicit user complaints, outcome mismatch, incident cohort
retain high: canary/new release, rare tool/route, tail latency, cost outlier,
             evaluator failure, repeated replan/loop, sampled high-risk tenant tier
retain base: deterministic trace-ID probability sample of normal successful runs
retain content: separate, stricter opt-in sample after privacy/redaction policy
```

Keep unsampled low-cardinality metrics for every request. Store operational metadata longer than raw content; keep incident/legal-hold artifacts under a separately governed process. Weight sampled aggregates by known selection probabilities when estimating population rates; a tail sample enriched for failures is not representative without adjustment `[inferred]`. LangSmith supports probability sampling between 0 and 1 and conditional tracing for requests that must always or never be traced [[37]](https://docs.langchain.com/langsmith/sample-traces).

### 2.5 Quality and trajectory monitors

Online monitoring must add semantic signals that infrastructure metrics cannot infer: task/outcome success, groundedness, policy adherence, correct tool/arguments, progress, repetition, escalation quality, and user feedback. Use deterministic state checks first, rules second, calibrated model graders third, and humans for consequential ambiguity `[inferred]`.

Run cheap checks synchronously only if they enforce a decision. Run expensive quality graders asynchronously on stratified samples; record grader model/prompt/rubric/version, latency, cost, raw judgment, and human calibration. LangSmith supports filtered/sampled production evaluators and describes feeding failing production traces into offline datasets [[39]](https://docs.langchain.com/langsmith/evaluation) [[40]](https://docs.langchain.com/langsmith/online-evaluations-llm-as-judge). A grader score is another fallible telemetry signal, not ground truth.

Trajectory indicators `[inferred]`:

- steps/turns/model calls/tool calls/handoffs and depth;
- repeated action/state ratio, no-progress window, plan churn and backtracking;
- argument correction, tool error recovery, retry amplification, approval rate;
- evidence coverage/provenance, unsupported action and stale observation rate;
- state-delta/outcome mismatch, premature termination and budget exhaustion;
- action entropy or route distribution shift by release and task slice.

The 2026 AgentTrace paper proposes structured logging for agent cognitive trajectories but acknowledges constraints of observability and validation in sensitive settings [[46]](https://arxiv.org/abs/2602.10133). A 2026 provenance survey identifies unified schemas, claim-level provenance, realistic trace benchmarks, recovery evaluation, and privacy-aware audit infrastructure as open challenges [[47]](https://arxiv.org/abs/2606.04990).

## 3. Distributed Resilience & State

### 3.1 Durable export

Telemetry is a production dependency but should not usually block the user request. Use bounded in-process batch processors; export locally; shed optional content before essential security/audit events; and never let an exporter create unbounded application memory `[inferred]`.

OTel Collector exporters support in-memory sending queues and exponential-backoff retry; current resilience docs state common defaults of 1,000 batches and five minutes, after which queue overflow or retry expiry can lose data. A file-backed WAL survives Collector restart, while Kafka can decouple tiers at higher operational cost [[17]](https://opentelemetry.io/docs/collector/resiliency/). Treat those defaults as starting points: size for measured peak ingress, backend outage objective, item size, and disk/memory budget.

```text
required_buffer_bytes >= peak_ingest_bytes_per_second
                       * tolerated_backend_outage_seconds
                       * safety_factor

time_to_queue_full = free_queue_bytes / (ingress_Bps - sustainable_egress_Bps)
```

Alert on queue occupancy/growth, refused/enqueue/export failures, dropped items, retry age, WAL disk, backend throttle, and end-to-end telemetry freshness. OTel Collector exposes internal telemetry for its own metrics, logs, and traces [[19]](https://opentelemetry.io/docs/collector/internal-telemetry/). Send a periodic synthetic canary through SDK → collector → backend → query → alert notification to detect silent pipeline failure `[inferred]`.

### 3.2 Scaling and backpressure

Scale stateless receivers/processors horizontally. Tail samplers and span-to-metrics processors are stateful: spans for a trace/service must be consistently routed, or traces become partial and aggregations inaccurate. OTel recommends a load-balancing layer keyed by trace ID or service name; its current scaling guidance suggests considering scale-up around 60–70% exporter queue capacity while checking whether the backend, rather than Collector, is the bottleneck [[18]](https://opentelemetry.io/docs/collector/scaling/).

OTLP distinguishes retryable from non-retryable failures, supports partial-success counts, and specifies backpressure through retry information or HTTP `Retry-After` for eligible status codes [[6]](https://opentelemetry.io/docs/specs/otlp/). Honor backpressure with jitter, cap retries, meter drops, and avoid retry storms. Priority queues should protect security/audit and error signals from verbose debug/content traffic `[inferred]`.

### 3.3 Checkpoints, replay, and ordering

Agent checkpoint state and observability data are related but not interchangeable. The checkpoint is the runtime's resumable state; the trajectory is an evidentiary account of transitions. Write a checkpoint ID/version and state hash into the trajectory after durable commit. If possible, use an outbox/change-data-capture pattern so a committed business state change produces a corresponding durable event `[inferred]`.

Use per-run monotonically increasing step sequence plus event ID. Wall clocks order events across services only approximately; record monotonic duration locally and queue producer/consumer linkage. A replay harness needs versioned prompts, tools, policy, model identity/settings, initial environment snapshot, random seed where meaningful, external responses/artifacts, and recorded side effects. Even then, stochastic or retired models and mutable services make exact regeneration impossible. Support **explain/re-simulate**, not a false promise of deterministic replay `[inferred]`.

### 3.4 Multi-agent and workflow correlation

For supervisor-worker, handoff, DAG, or message-bus topologies `[inferred]`:

- one business task has a root trace or a stable correlation group over multiple traces;
- each agent invocation and delegation is a span or linked trace;
- task envelope carries W3C context, run/delegation IDs, budget, and schema version;
- fan-out children link to the dispatch span; join span records expected/received/missing children;
- worker identity, role, version, assigned task, capability and result artifact are explicit;
- cancellation/deadline propagates and is recorded at each boundary;
- duplicate/redelivered tasks reuse idempotency/event identity but create a new attempt span;
- final outcome links to all contributing trace/artifact IDs.

OpenAI's Agents SDK uses trace/group IDs and captures handoff spans, and its batch processor can be replaced or supplemented with custom processors [[31]](https://openai.github.io/openai-agents-python/tracing/). Google ADK exposes logging, metrics, and tracing integrations for agents [[34]](https://adk.dev/observability/). Framework instrumentation is a starting point; application-owned queue, state, authorization, and outcome spans still require manual instrumentation.

## 4. Enterprise Security & Governance

### 4.1 Telemetry is sensitive production data

Prompts, outputs, retrieved documents, memory, tool arguments/results, system instructions, identities, and traces can contain PII, secrets, regulated data, source code, or adversarial payloads. OTel GenAI says instructions/input/output are sensitive and often large, should not be captured by default, and may instead be stored externally with references under separate access controls [[3]](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md).

Apply data minimization before export `[inferred]`:

| Data | Default | Elevated diagnostic mode |
|---|---|---|
| Model/tool metadata, timings, tokens, status | capture with bounded fields | same |
| Prompt/response/query/tool payload | hash + classified artifact reference | sampled, redacted, encrypted artifact |
| System instructions/tool definitions | version/hash | authorized snapshot reference |
| User/tenant/session | keyed pseudonym or internal opaque reference | re-identification only through audited service |
| Secrets/tokens/passwords/keys | never capture | never capture; rotate if observed |
| Chain-of-thought/private reasoning | do not require/capture | structured decision summary if product exposes it |

OWASP recommends excluding or masking tokens, passwords, keys, sensitive PII, payment data, connection strings, and data above the log system's classification, and sanitizing event data to prevent log injection [[27]](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html). NIST's Privacy Framework treats data processing and privacy risk as enterprise risk-management concerns [[30]](https://www.nist.gov/privacy-framework).

### 4.2 Access, encryption, integrity, and tenancy

Use workload identity and mutual TLS for OTLP; authenticate receivers/exporters; restrict collector networks; run least privilege; store exporter credentials in a secret manager. OTel's Collector security guidance recommends encryption/authentication, minimal components, non-root execution, careful receiver/exporter configuration, resource limits, and redaction processors [[20]](https://opentelemetry.io/docs/security/config-best-practices/).

Separate roles `[inferred]`:

- SRE: aggregate metrics and redacted operational traces;
- application engineer: scoped trace metadata/content for owned services;
- evaluator/researcher: de-identified sampled trajectories;
- security/incident team: high-integrity security audit and approved raw evidence;
- privacy/legal: retention, deletion, legal hold, residency and access review;
- no model/agent: permission to alter its own immutable audit evidence.

Enforce tenant/workspace row-level access and region-specific routing. Encrypt transport, storage, indexes, queues, WAL, backups, and artifact references. Record every raw-content access/export and protect audit integrity with append-only/immutable storage, hashes/signatures, synchronized time, restricted deletion, and tested restoration `[inferred]`.

### 4.3 Retention and deletion

Create a field-level data inventory and retention schedule by purpose: high-resolution metrics, trace metadata, logs, raw content, evaluator annotations, incident evidence, and audit records need not share a TTL. Honor user/tenant deletion across trace DB, log index, object store, evaluation datasets, caches, and backups according to legal obligations `[inferred]`. LangSmith documents input/output hiding, metadata transformation, regex or PII anonymization, per-request masking, and conditional no-trace operation for zero-retention cases [[41]](https://docs.langchain.com/langsmith/mask-inputs-outputs).

Do not silently use production trajectories for training or evaluation beyond the declared purpose. Record consent/legal basis, dataset lineage, de-identification, access, and deletion propagation `[inferred]`.

### 4.4 Schema and instrumentation governance

Maintain an observability contract registry `[inferred]`:

- signal/event/span/metric name, owner, semantic version and description;
- field type/unit, allowed values/cardinality, sensitivity/classification;
- producer/consumer, source of truth, retention, redaction, sampling;
- availability/quality SLO, dashboards, alerts, runbook, deprecation date.

CI should validate metric names/units/buckets, required spans/events, parentage, schema compatibility, forbidden content, PII/secret test fixtures, cardinality budget, and exporter-offline behavior. Run trace-contract tests against representative success, retry, timeout, handoff, cancellation, and policy-denial paths. OpenTelemetry's base semantic conventions standardize naming across polyglot services, but GenAI Development status makes local compatibility tests essential [[2]](https://opentelemetry.io/docs/specs/semconv/).

### 4.5 Incident response and forensic workflow

NIST SP 800-61r3 integrates incident response across CSF 2.0 functions rather than treating it only as post-compromise handling [[28]](https://csrc.nist.gov/pubs/sp/800/61/r3/final). NIST's log-management planning guide defines logging as generating, transmitting, storing, accessing, and disposing of data for incident investigation and operations [[29]](https://csrc.nist.gov/pubs/sp/800/92/r1/ipd).

Incident workflow `[inferred]`:

1. Alert contains SLO impact, affected release/tenant/tool/model, trace exemplars, and runbook.
2. Incident system creates an ID propagated to queries, mitigations, and retained evidence.
3. Preserve relevant trace/log/artifact/checkpoint hashes under legal/retention policy.
4. Determine telemetry completeness before inferring absence of actions.
5. Build a timeline from authenticated events and external state receipts, not model narration alone.
6. Contain via feature flag, tool disable, route rollback, budget/circuit breaker, credential revoke.
7. Reconcile side effects and notify affected owners.
8. Convert the trajectory into an offline regression, update detection/runbook, and verify telemetry coverage.

Monitoring infrastructure must itself be monitored. Google SRE and Prometheus both emphasize simple actionable paging and meta-monitoring because a silent alert pipeline is worse than a visible component failure [[23]](https://sre.google/sre-book/monitoring-distributed-systems/) [[26]](https://prometheus.io/docs/practices/alerting/).

## 5. Production Failure Modes

| # | Failure | Detection | Mitigation `[inferred]` |
|---:|---|---|---|
| 1 | Root span closes before async children | orphan/late-span rate | explicit lifecycle; flush after background task completion |
| 2 | Queue drops trace context | parentless worker spans | inject/extract W3C context in task envelope; contract test |
| 3 | Fan-out represented as one false parent tree | impossible causal order | span links and explicit dispatch/join events |
| 4 | Conversation ID used as trace ID | huge never-ending trace | trace per task/turn; conversation as bounded group attribute |
| 5 | Retries create separate unrelated spans | attempt totals exceed logical calls | logical parent span plus attempt events/child transport spans |
| 6 | Tool reports success but state did not change | outcome/state assertion mismatch | record receipts and state before/after; verify outcome |
| 7 | Hidden SDK/framework call is uninstrumented | token invoice exceeds traced usage | compare provider billing/usage; manual hook or gateway span |
| 8 | Auto-instrumentation duplicates manual spans | paired identical spans/usage | single ownership map; suppress one instrumentation layer |
| 9 | Semantic-convention upgrade breaks queries | sudden missing dimensions | pin/map versions; dual-write/canary; schema tests |
| 10 | Dynamic span names explode index cardinality | unique-name growth | stable operation names; values in attributes/artifacts |
| 11 | User/run IDs become metric labels | series and memory surge | remove unbounded labels; exemplars/log lookup |
| 12 | OTel cardinality overflow hides error label | `otel.metric.overflow=true` | tighter views/labels; alert on overflow; retain aggregate totals |
| 13 | Histogram buckets miss agent-long latency | all tail values in top bucket | buckets aligned to measured task/tool SLOs; native histogram if supported |
| 14 | Average latency/quality hides bad tail/slice | user reports despite green mean | percentiles/distributions and critical slice dashboards |
| 15 | Head sampling drops rare incident trace | alert has no exemplar | tail retain errors/policy/outliers; separate audit stream |
| 16 | Tail sampler overloads or splits traces | partial traces, sampler queue pressure | trace-ID routing, capacity/fallback policy, self-monitoring |
| 17 | Sampling-biased failure rate treated as population | implausible aggregate | preserve selection probability; use unsampled counters/weighting |
| 18 | Collector/backend outage fills memory | queue growth/refused data | bounded queue, WAL/Kafka tier, backpressure, priority shedding |
| 19 | Retry storm worsens telemetry outage | exporter attempts/CPU surge | exponential backoff/jitter, circuit breaker, local buffer cap |
| 20 | Process/serverless exits before batch flush | run count exceeds exported roots | explicit flush on completion; local collector; shutdown hook |
| 21 | Clock skew creates negative/wrong ordering | timestamp anomalies | synchronized clock, monotonic duration, sequence IDs, causal links |
| 22 | Duplicate export double-counts logs/events | same event ID repeated | idempotent event IDs/dedupe or at-least-once-aware queries |
| 23 | Partial OTLP success is ignored | accepted count differs emitted | meter rejected items; handle partial success without blind retry |
| 24 | Debug mode logs prompts/secrets | DLP/secret canary hit | content off by default; pre-export redaction; rotate secrets |
| 25 | Redaction occurs after raw vendor export | raw payload visible in backend | redact in process/local collector before external boundary |
| 26 | Regex redaction misses contextual PII | audit sample finding | allowlist fields; NER/classifier plus tests; no-trace for prohibited data |
| 27 | Over-redaction destroys forensic value | incident cannot identify action | store hashed refs; separately controlled raw evidence; decision metadata |
| 28 | Trace context/baggage leaks tenant data externally | outbound-header audit | allowlist propagators at trust boundary; never put secrets in baggage |
| 29 | Attacker injects newlines/fields into logs | malformed event/schema failures | structured encoder, escaping/sanitization, never concatenate raw input |
| 30 | Agent can alter/delete its audit trail | gap aligned with suspicious action | out-of-process append-only sink and restricted credentials |
| 31 | LLM grader drift looks like product drift | score changes at grader release | version grader; overlap/calibrate on human-labeled set |
| 32 | Evaluator consumes unchecked production content | injection or data exfiltration | isolate evaluator, structured rubric, no action tools, redact inputs |
| 33 | Alert pages on every internal cause | duplicate pages and fatigue | page on user symptom/SLO; diagnostics in dashboard |
| 34 | Quality regression has no threshold alert | feedback declines unnoticed | calibrated online sample plus SLO/error-budget alert |
| 35 | Model alias changes without telemetry version | behavior shifts under same label | record requested and response model/snapshot/release event |
| 36 | Prompt/tool/policy version omitted | incident cannot reproduce cohort | immutable hashes/versions on root and action spans |
| 37 | Context compaction erases trajectory evidence | trace has summary but no source | retain artifact refs and compaction lineage/hashes |
| 38 | Long run exceeds retention before completion | early steps disappear | retention anchored after terminal state; archive long-run segments |
| 39 | Multi-agent child lacks user/task authority context | unexplained delegated action | delegation record with authenticated scope and parent trace link |
| 40 | Outcome inferred from final text | false success metric | deterministic environment/receipt verification |
| 41 | Monitoring pipeline is green only because no data arrives | missing series interpreted as zero | absence alerts, heartbeat/canary, expected-volume checks |
| 42 | Trace UI becomes production data-export path | unreviewed sensitive download | RBAC, watermark/export audit, tenant scope, rate/volume controls |

Long-horizon monitoring has an additional blind spot: individually benign actions may form a harmful sequence. A 2026 TRACE paper reports aggregate F1 0.713 and recall 0.844 on ten SHADE-Arena domains using cross-step evidence aggregation; these results are benchmark-specific and do not establish production detection rates [[48]](https://arxiv.org/abs/2606.07054). Use trajectory-level rules/monitors for cumulative spend, repeated access, scope expansion, evidence-to-action flow, and delayed exfiltration, while retaining deterministic tool policy as the enforcement boundary `[inferred]`.

## 6. Enterprise System Design Scenarios

### 6.1 High-volume customer-support agent

**Topology:** API and agent workers emit OTel to node-local Collectors, which batch to regional gateways. The gateway redacts, derives low-cardinality metrics, tail-samples all errors/policy denials/latency and quality outliers, and probability-samples normal successes. Metrics drive SLO alerts; sampled traces flow to a trace backend; raw conversation/tool content stays in the customer data store with ACL-controlled references `[inferred]`.

**Key dashboards:** task success/escalation, end-to-end p95/p99, model/tool error and rate limit, turns/replans, token/cost per success, evaluator/user feedback, tool side-effect mismatch, and telemetry loss/freshness. LangSmith's prebuilt dashboards illustrate trace, LLM, token/cost, tool, latency, and error groupings; its alerting supports run count, cost, error, feedback, and latency thresholds [[12]](https://docs.langchain.com/langsmith/dashboards) [[24]](https://docs.langchain.com/langsmith/alerts).

**Trade-off:** retaining all conversation content improves debugging but creates disproportionate privacy/storage risk. Retain metadata for all runs, raw content only under tenant policy and stratified sampling.

### 6.2 Long-running coding agent

**Topology:** one business task correlation group contains many resumable run-segment traces. Each segment records checkpoint ID, worktree/commit hash, plan version, model calls, shell/tool spans, file-delta manifest, test results, budgets, sandbox metrics, and handoff artifact. A durable trajectory ledger outlives individual trace retention. The collector prioritizes command, file-change, authorization, and external side-effect events over stdout/debug payload `[inferred]`.

**Detection:** no-progress steps, same failing command, repeated state hash, plan churn, dependency-install/network anomaly, token/disk/process budget, uncommitted work at segment end, and final test/outcome assertions. Exact replay may be impossible; preserve base image, repo commit, dependency lock, command/output hashes, and artifacts for controlled re-simulation.

**Trade-off:** per-command output is valuable but huge and secret-prone. Store bounded previews plus encrypted artifact references; never make raw terminal output a metric label/span name.

### 6.3 Multi-agent research platform

**Topology:** supervisor trace fans out linked worker traces through a queue. Delegation events record query/task, evidence scope, budget, worker version, and expected artifact. Worker retrieval/model/browser/tool spans retain evidence URLs/hashes and citation provenance. A join span records expected, completed, timed-out, duplicate, and discarded workers. Claim-level output links to evidence artifacts `[inferred]`.

**Monitoring:** task success, coverage/diversity, citation support, worker success/latency/cost, fan-out depth, duplicate search ratio, missing join results, cross-agent contradictions, and cost per supported claim. The provenance literature identifies claim-level and semantic provenance plus privacy-aware audit as unresolved system challenges [[47]](https://arxiv.org/abs/2606.04990).

**Trade-off:** one giant trace is easy to view but costly and fragile. Use a group/business-run ID over linked per-worker traces and maintain a separate trajectory index.

### 6.4 Regulated financial action agent

**Topology:** the operational trace excludes raw account/identity/content. It includes pseudonymous user/tenant refs, policy decision, bound approval, normalized transaction hash, tool call ID, idempotency key, sandbox/egress decision, processor receipt, and before/after state reference. Security/audit events go through a separate durable authenticated pipeline with stricter retention and access. Raw documents remain in the governed system of record `[inferred]`.

**Monitoring:** unauthorized proposal/deny, approval mismatch, recipient/amount drift, duplicate/unknown commit, reconciliation lag, cross-tenant attempt, DLP alert, policy/PDP latency, and audit-event completeness. Incident queries join via opaque IDs under audited break-glass access.

**Trade-off:** operational engineers see enough metadata to restore service without seeing regulated data; approved investigators can resolve references when necessary.

### 6.5 Capacity-planning worksheet

```text
runs_per_second_peak
* mean_spans_per_run
* mean_encoded_bytes_per_span
= trace_ingest_bytes_per_second

series ≈ sum_over_metrics(product_of_active_label_cardinalities)

collector_buffer = peak_ingest_Bps * outage_tolerance_seconds * safety_factor

tail_sampler_memory ≈ concurrent_open_traces
                      * spans_per_open_trace
                      * bytes_per_buffered_span

daily_storage = (trace_Bps + log_Bps + evidence_Bps) * 86400
                * replication_factor * index_overhead_factor
```

Measure distributions, not only means: p99 span count/content size and long-running open traces determine tail-sampler memory. Load-test success, error, retry, large-payload, fan-out, and backend-outage traffic. Validate application latency with telemetry enabled/disabled, queue saturation, drop priority, recovery drain rate, query latency, and alert timeliness `[inferred]`.

Google SRE notes that substantial monitoring systems require ongoing engineering ownership, not one-time dashboard creation [[23]](https://sre.google/sre-book/monitoring-distributed-systems/). Treat schema, pipeline, dashboards, alerts, graders, datasets, and runbooks as production code.

### 6.6 Production readiness checklist

- One documented trace/trajectory boundary exists for task, turn, conversation, and run segment.
- W3C context propagates across HTTP/RPC/queue/subagent boundaries; links model fan-out/fan-in.
- Required spans/events cover model, retrieval, memory, tools, policy, approval, state and outcome.
- Environment state/receipts verify effects; model final text is not the success source of truth.
- OTel/GenAI/OpenInference schema versions and mappings are pinned and compatibility-tested.
- Metric labels have bounded cardinality budgets; overflow and series growth are alerted.
- Sampling retains errors, policy/safety events, canaries and outliers; population estimates account for selection.
- Content capture is off by default; redaction happens before external export; raw access is audited.
- Queue/WAL/backend outage, partial success, throttling, process exit and recovery drain are load-tested.
- Collectors, exporters, backends, queries and alert delivery have meta-monitoring/canaries.
- Quality graders are versioned, sampled, costed, and calibrated against human/deterministic checks.
- Retention, deletion, residency, legal hold, incident preservation and dataset reuse are governed.

## Interview Preparation

1. **How is a trajectory different from a distributed trace?** A trace models causal timed operations; a trajectory adds ordered goal, evidence, action, state transition, policy, and outcome semantics. Store both through a shared identity model.
2. **Why not put prompts in every span?** They are large, sensitive, high-cost, and may exceed backend limits. Default to hashes/versions/references and selectively retain redacted content under separate access.
3. **Head or tail sampling?** Head sampling is cheap and statistically simple; tail sampling can retain errors, tail latency, and policy events but is stateful. Keep unsampled metrics and usually combine policies.
4. **How do you stop metric-cardinality explosions?** Allowlist bounded labels, never use request/user/prompt IDs, enforce budgets/views, alert on overflow/series growth, and use exemplars to reach traces.
5. **What should page an operator?** User-visible SLO burn, unsafe side effects, material cost/reliability/security symptoms, and telemetry blindness. Internal causes belong in diagnostic dashboards unless independently actionable.
6. **How do you know the trace is complete?** Define required spans/events by workflow path, instrument export/drop counters, compare business/provider counts, track parent/link coverage, and run end-to-end telemetry canaries.
7. **Can an agent run be replayed exactly?** Usually not. Preserve versions, inputs, external artifacts, state, settings, and side effects for explanation/re-simulation, but stochastic and mutable dependencies prevent a universal guarantee.
8. **How do you trace multi-agent fan-out?** Root business identity, dispatch span, linked child traces, delegation metadata, join accounting, and artifact lineage; do not force unrelated async work into misleading parentage.
9. **Why monitor quality online?** HTTP success does not mean task success. Sample production runs for deterministic outcome checks, calibrated graders, feedback, policy and trajectory anomalies, then feed failures into offline regression suites.
10. **What is the observability system's biggest hidden risk?** It can become a high-value copy of prompts, data, credentials, and actions. Minimize, redact before export, segment access, audit queries/exports, and monitor the pipeline itself.

## Sources

1. [OpenTelemetry Specification 1.60.0](https://opentelemetry.io/docs/specs/otel/)
2. [OpenTelemetry Semantic Conventions 1.44.0](https://opentelemetry.io/docs/specs/semconv/)
3. [OpenTelemetry GenAI span semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)
4. [OpenTelemetry GenAI metric semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md)
5. [OpenTelemetry GenAI attribute registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)
6. [OTLP Specification 1.11.0](https://opentelemetry.io/docs/specs/otlp/)
7. [W3C Trace Context Recommendation](https://www.w3.org/TR/trace-context/)
8. [OpenTelemetry Logging specification](https://opentelemetry.io/docs/specs/otel/logs/)
9. [OpenTelemetry Context Propagation](https://opentelemetry.io/docs/concepts/context-propagation/)
10. [OpenTelemetry Baggage](https://opentelemetry.io/docs/concepts/signals/baggage/)
11. [OpenTelemetry Sampling](https://opentelemetry.io/docs/concepts/sampling/)
12. [LangSmith: Monitoring dashboards](https://docs.langchain.com/langsmith/dashboards)
13. [OpenTelemetry Metrics and cardinality limits](https://opentelemetry.io/docs/concepts/signals/metrics/)
14. [OpenTelemetry Metrics SDK: cardinality and exemplars](https://opentelemetry.io/docs/specs/otel/metrics/sdk/)
15. [OpenTelemetry Collector](https://opentelemetry.io/docs/collector/)
16. [OpenTelemetry Collector gateway pattern](https://opentelemetry.io/docs/collector/deploy/gateway/)
17. [OpenTelemetry Collector resiliency](https://opentelemetry.io/docs/collector/resiliency/)
18. [OpenTelemetry Collector scaling](https://opentelemetry.io/docs/collector/scaling/)
19. [OpenTelemetry Collector internal telemetry](https://opentelemetry.io/docs/collector/internal-telemetry/)
20. [OpenTelemetry Collector configuration security](https://opentelemetry.io/docs/security/config-best-practices/)
21. [OpenTelemetry API performance benchmark methodology](https://opentelemetry.io/docs/specs/otel/performance-benchmark/)
22. [Google: Dapper, a Large-Scale Distributed Systems Tracing Infrastructure](https://research.google/pubs/dapper-a-large-scale-distributed-systems-tracing-infrastructure/)
23. [Google SRE: Monitoring Distributed Systems](https://sre.google/sre-book/monitoring-distributed-systems/)
24. [LangSmith: Alerts](https://docs.langchain.com/langsmith/alerts)
25. [Prometheus instrumentation best practices](https://prometheus.io/docs/practices/instrumentation/)
26. [Prometheus alerting best practices](https://prometheus.io/docs/practices/alerting/)
27. [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
28. [NIST SP 800-61r3: Incident Response Recommendations](https://csrc.nist.gov/pubs/sp/800/61/r3/final)
29. [NIST SP 800-92r1 IPD: Cybersecurity Log Management Planning Guide](https://csrc.nist.gov/pubs/sp/800/92/r1/ipd)
30. [NIST Privacy Framework](https://www.nist.gov/privacy-framework)
31. [OpenAI Agents SDK tracing](https://openai.github.io/openai-agents-python/tracing/)
32. [OpenAI Agents SDK usage tracking](https://openai.github.io/openai-agents-python/usage/)
33. [OpenAI API: Trace grading](https://developers.openai.com/api/docs/guides/trace-grading)
34. [Google Agent Development Kit: Observability](https://adk.dev/observability/)
35. [Google Agent Development Kit: Logging](https://adk.dev/observability/logging/)
36. [Google Agent Development Kit: Agent evaluation](https://adk.dev/evaluate/)
37. [LangSmith: Set a trace sampling rate](https://docs.langchain.com/langsmith/sample-traces)
38. [LangSmith: Distributed tracing](https://docs.langchain.com/langsmith/distributed-tracing)
39. [LangSmith: Evaluation](https://docs.langchain.com/langsmith/evaluation)
40. [LangSmith: Online LLM-as-judge evaluators](https://docs.langchain.com/langsmith/online-evaluations-llm-as-judge)
41. [LangSmith: Prevent sensitive data in traces](https://docs.langchain.com/langsmith/mask-inputs-outputs)
42. [Arize Phoenix documentation](https://arize.com/docs/phoenix/)
43. [OpenInference Specification](https://github.com/Arize-ai/openinference/tree/main/spec)
44. [Anthropic: Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
45. [Dong, Lu, and Zhu: AgentOps — Enabling Observability of LLM Agents](https://arxiv.org/abs/2411.05285)
46. [AgentTrace: A Structured Logging Framework for Agent System Observability](https://arxiv.org/abs/2602.10133)
47. [From Agent Traces to Trust: Evidence Tracing and Execution Provenance](https://arxiv.org/abs/2606.04990)
48. [TRACE: Trajectory Reasoning through Adaptive Cross-Step Evidence Aggregation](https://arxiv.org/abs/2606.07054)

## Research Gaps

> ⚠️ Limited public data available for this dimension. OpenTelemetry GenAI conventions remain in Development, and no stable cross-framework standard fully represents agent delegation, plan evolution, authorization, environment state deltas, outcome evidence, and evaluator annotations.

> ⚠️ Limited public data available for this dimension. Vendors publish features and isolated benchmark methods, but comparable p50/p95/p99 overhead, loss, storage amplification, query latency, and cost per 1,000 agent runs are not available across full production stacks.

> ⚠️ Limited public data available for this dimension. There is no representative public corpus of privacy-safe production trajectories with ground-truth incidents, state transitions, and long-horizon outcomes for calibrating anomaly or safety monitors.

> ⚠️ Limited public data available for this dimension. Online LLM-judge drift, false-alert rates, long-term human calibration cost, and behavior under adversarial trace content lack broad longitudinal studies.

> ⚠️ Limited public data available for this dimension. Exact telemetry retention, regional residency, deletion, legal-hold, and raw-content access requirements depend on jurisdiction, contract, data class, and organizational policy rather than one universal architecture.

> ⚠️ Limited public data available for this dimension. Production incident postmortems rarely disclose whether incomplete context propagation, sampling, redaction, collector loss, or missing state evidence materially delayed agent incident diagnosis and recovery.
