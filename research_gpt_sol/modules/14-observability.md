# 14 - Observability

**Scope:** Tracing, logging, monitoring, and agent trajectories for distributed, stateful agent systems.
**Study goal:** Build telemetry that explains causal execution, verifies outcomes, detects system and quality regressions, and proves when the telemetry itself is incomplete.

Agent observability answers five questions: what ran, why it ran, what state it observed and changed, whether the task and policy outcome were correct, and whether the evidence is complete. HTTP success is not task success. A model's final narrative is not proof of a side effect. A green dashboard is not evidence when the telemetry pipeline is silently dropping data.

## 1. System Topology & Data Flow

### Reference topology

```text
                                      CONTROL PLANE
┌─────────────────────────────────────────────────────────────────────────────┐
│ schema/semconv registry │ instrumentation policy │ sampling/cardinality    │
│ redaction/classification │ retention/residency │ SLOs/alerts │ access/RBAC │
│ collector config/signing │ grader versions │ release annotations/runbooks  │
└────────────┬─────────────────────┬───────────────────────┬──────────────────┘
             │ signed config       │ routing/retention     │ policy/version
             ▼                     ▼                       ▼
┌──────────────┐ objective  ┌───────────────────────────────────────────────┐
│ API/scheduler├───────────►│ AGENT DATA PLANE                              │
└──────────────┘ W3C context│ runtime -> model/retrieval/memory/tool/MCP    │
                            │        -> queue/subagent -> state/outcome     │
                            └──────────────┬────────────────────────────────┘
                                           │ traces/logs/metrics/trajectory
                                           │ content minimized + refs/hashes
                                           ▼
                            ┌───────────────────────────────────────────────┐
                            │ local/sidecar OTel Collector                  │
                            │ batch │ enrich │ redact │ bound queue/WAL     │
                            └──────────────┬────────────────────────────────┘
                                           │ OTLP/mTLS
                                           ▼
                            ┌───────────────────────────────────────────────┐
                            │ regional gateway Collectors                  │
                            │ trace-ID routing │ tail sample │ transform   │
                            │ priority/backpressure │ Kafka/WAL │ export   │
                            └───────┬────────┬─────────┬──────────┬─────────┘
                                    │        │         │          │
              ┌─────────────────────┘        │         │          └──────────┐
              ▼                              ▼         ▼                     ▼
       ┌────────────┐                 ┌──────────┐ ┌──────────┐      ┌────────────┐
       │ metrics    │                 │ trace DB │ │ log/SIEM │      │ trajectory │
       │ TSDB       │                 │ sampled  │ │ + audit  │      │ ledger +   │
       └──────┬─────┘                 └────┬─────┘ └────┬─────┘      │ artifacts  │
              └──────────────┬─────────────┴────────────┴────────────┴─────┬──────┘
                             ▼                                             ▼
                   ┌──────────────────┐                          ┌─────────────────┐
                   │ dashboards/pages│                          │ evals/incidents │
                   │ SLO/budget burn  │                          │ release gates   │
                   └──────────────────┘                          └─────────────────┘

 PERSISTENCE: workflow checkpoints │ event/outbox │ immutable artifacts/receipts
 TOOL PROXY: policy/credential/egress gateway emits proposal, decision and effect
 META-TELEMETRY: emitted/accepted/refused/dropped │ freshness │ canary end to end
```

The local collector prevents every application from owning exporter credentials and performs redaction before data crosses a trust boundary. Regional gateways centralize routing and stateful tail sampling. All spans for one trace must reach the same tail-sampler shard. Metrics, operational traces, security audit and content artifacts have different sensitivity and durability, so they do not share one undifferentiated sink.

### End-to-end request flow

1. Intake creates a globally unique trace ID and business `run_id`; it accepts a valid W3C `traceparent` only at a defined trust boundary. Authenticated identity is obtained from the security context, never from baggage.
2. The root `agent.run` span records stable bounded versions: service/deployment, workflow, agent, prompt, model route, tool registry, policy and semantic-convention mapping. User text is not a span name or metric label.
3. Context assembly, model inference, retrieval, memory, policy, tool, approval and outcome verification create child spans. Async tasks carry trace context explicitly; fan-out/fan-in uses span links and dispatch/join events.
4. Structured lifecycle logs share `trace_id`, `span_id`, `run_id`, event ID and schema version. Exceptions are logged once by the retry owner. Large or sensitive inputs/outputs become encrypted artifact references plus classifications and hashes.
5. Each trajectory step appends goal/plan version, evidence references, typed action, authorization, receipt, state-before/state-after hash, resource use and outcome assertion. Hidden chain-of-thought is neither required nor collected.
6. Low-cardinality counters, gauges and histograms cover traffic, errors, saturation, latency, quality, safety, progress and economics. Exemplars point from unusual histogram buckets to sampled traces.
7. The local collector batches, redacts, bounds memory and spools essential telemetry. The gateway honors backend backpressure and sheds optional debug/content before error, policy, effect and audit evidence.
8. Head sampling supplies an unbiased base sample; tail rules retain errors, policy/safety events, outliers and canaries. Unsampled request counters remain the population denominator.
9. Deterministic environment assertions and provider receipts set task/effect outcome. Asynchronous graders add versioned estimates, not ground truth.
10. Pipeline meta-metrics and a synthetic canary verify SDK -> collector -> backend -> query -> alert. Absence or freshness violation pages as telemetry blindness.

### Signal contract

| Signal | Unit and purpose | Must correlate with | Common misuse |
|---|---|---|---|
| Trace | causally related timed operations | trace/span links, run, versions | conversation as one never-ending trace; dynamic span names |
| Log | independently searchable discrete event/evidence | event ID, trace/span/run, schema | prose parsing, duplicate exception logs, raw prompt dumping |
| Metric | bounded aggregate counter/gauge/histogram | release dimensions and exemplars | user/run/prompt/URL labels; averages without tails |
| Trajectory | ordered goal/evidence/action/state/outcome steps | run/step, artifacts, receipts, checkpoints | calling model narration an outcome; requiring private reasoning |

## 2. Core Mechanics & Algorithms

### 2.1 Standards maturity and canonical model

As of 2026-08-21, the OpenTelemetry core specification is `1.60.0`, OTLP is `1.11.0`, and core semantic conventions are `1.44.0`. The dedicated OpenTelemetry GenAI span and metric conventions remain **Development**, not Stable. OpenInference is a separate AI-focused semantic convention carried on valid OTel spans; it is not identical to OTel GenAI. No stable cross-framework standard completely models delegation, plan evolution, authorization, environment deltas, outcome evidence and evaluator annotations.

Therefore, define an internal versioned canonical schema, pin every producer's convention revision, map OTel GenAI/OpenInference/framework fields at ingestion, and contract-test upgrades. Do not present vendor UI compatibility as semantic equivalence.

```text
framework event ─► versioned adapter ─► canonical span/event/trajectory
OTel GenAI      ─► versioned adapter ─► canonical model
OpenInference   ─► versioned adapter ─► canonical model
                                      └─► backend-specific projection
```

Recommended trace hierarchy:

```text
agent.run
  input.guardrail
  agent.invoke
    context.assemble
      memory.search
      retrieval.query -> retrieval.rerank
    agent.plan
    model.inference
    tool.execute
      policy.authorize
      tool.transport
      environment.state_delta
    agent.handoff -> linked child agent.invoke
    approval.wait
  output.guardrail
  outcome.verify
```

Stable operation names keep trace indexes bounded. Attributes carry registered `tool.name`, model, operation, retry and normalized error class. Raw prompt, response, query, URL, document and arbitrary arguments live only in governed evidence storage when policy permits.

### 2.2 Correlation across synchronous and asynchronous work

A W3C `traceparent` has version, 16-byte nonzero trace ID, 8-byte nonzero parent ID and flags. Validate it; do not blindly reflect arbitrary headers. Inject into HTTP/RPC and queue metadata, extract in the worker, and create a new span. Use a link rather than false parentage when a consumer batch has several producers or when work is deliberately separated into another trace.

```text
business task: run-42 / correlation group G

root trace T0: dispatch span D
                    ├──link──► worker trace T1 / delegation d1
                    ├──link──► worker trace T2 / delegation d2
                    └──link──► worker trace T3 / delegation d3
root trace T0: join span J {expected:3, received:2, timed_out:1}
```

Correlation invariants:

- trace IDs are globally unique; conversation ID is only a bounded grouping attribute;
- every eligible async child has a valid parent or explicit link;
- authenticated tenant/user authority never comes from baggage;
- duplicate queue delivery reuses logical task/event identity but gets a new attempt span;
- a join accounts for expected, completed, duplicate, discarded and timed-out children;
- context propagation coverage is measured, not assumed.

Trace assembly is `O(S + L)` for `S` spans and `L` links when IDs are indexed. A naive UI that repeatedly scans parents is `O(S²)` and fails on fan-out. Store parent/link indexes and page large traces.

### 2.3 Structured logs and trajectory ledger

A machine event contains timestamp and observed timestamp, severity, stable event name, schema version, event/trace/span/run/step IDs, source version, bounded status/error, artifact/hash references and policy/outcome IDs. Encode JSON structurally; never concatenate untrusted content into a line.

The trajectory is an append-only event stream, not merely the span tree:

```text
ACCEPTED -> OBSERVED -> PLANNED -> PROPOSED -> AUTHORIZED
         -> EXECUTED -> STATE_VERIFIED -> CHECKPOINTED -> TERMINAL
```

For step `i`, retain `H_i = SHA256(H_(i-1) || canonical_event_i)`. This hash chain detects alteration and supplies chain of custody; external immutable storage, signatures and restricted deletion are still required because an agent that controls both data and hashes can rewrite both. Sequence insertion is `O(1)`; verification is `O(n)`. Partition by run, reject conflicting duplicate event IDs, and allow idempotent replay of the identical event.

Capture observable plan/action proposals and concise exposed decision summaries, not hidden chain-of-thought. A checkpoint is resumable runtime state; a trajectory is evidence about transitions. Append the checkpoint version and state hash after durable commit, preferably through an outbox/CDC event. Exact replay is usually impossible because models and external services are stochastic or mutable; promise explanation and controlled re-simulation instead.

### 2.4 Metrics, cardinality, and monitoring algorithms

Use counters for totals, gauges for current queue/concurrency, and histograms for latency, tokens, cost, turns and score distributions. A histogram supports tail estimation without a series per request. Preserve exemplars for trace drill-down.

Metric series grow approximately as the Cartesian product:

```text
series(metric) <= product(active values of each retained label)
```

`region(5) × workflow(20) × version(4) × status(6) × tool(50) = 120,000` possible series before replicas. Adding `tenant(10,000)` would make the theoretical space 1.2 billion. Maintain an allowlist, owner, expected cardinality and expiry for every label. Registry-backed tool/model names and normalized error types are acceptable; trace, run, conversation, user, tenant, document, prompt/hash, URL, arguments and error message are not metric labels.

OTel Metrics SDK's default cardinality limit is 2,000 attribute sets per stream when no other limit is configured; overflow is folded into `otel.metric.overflow=true`. That is a guardrail, not a capacity target. Alert on overflow and new-series rate. Prometheus guidance to keep most cardinality under ten and investigate metrics over 100 is guidance, not a backend-independent limit.

Monitor three SLO layers:

```text
product:   policy-compliant success, quality/safety, E2E latency, cost/success
component: model/tool/retrieval success/latency, loops, retries, token route
telemetry: coverage, joins, export completeness, freshness, query/alert latency

trace_completeness = observed_required_spans / expected_required_spans
context_join_rate  = valid_parent_or_link_spans / eligible_async_spans
telemetry_loss     = dropped_or_rejected / emitted
cost_per_success   = total agent + observability cost / compliant successes
```

Page user-visible multi-window SLO burn, unsafe effects, material cost/safety symptoms and telemetry blindness. Put non-actionable internal causes in dashboards. Track the golden signals plus quality, safety, progress and economics. Missing series must not mean zero: use heartbeat, expected-volume and end-to-end canary alerts.

### 2.5 Sampling, estimation, and quality monitoring

Head sampling is cheap and gives a known selection probability before outcome. Tail sampling sees status, latency, policy and score, but buffers open traces and biases retained data. Combine them:

1. retain 100% of security/policy events, errors, unsafe effects, outcome mismatch, explicit complaints and incident cohort;
2. retain high fractions for canaries, new release, rare tool/route, high cost/latency and looping;
3. deterministic trace-ID sample normal successes at known probability `p`;
4. decide content retention separately after classification/redaction;
5. keep unsampled low-cardinality counters for population rates.

For independently probability-sampled item `i`, estimate a total with Horvitz-Thompson weighting `Σ(y_i / p_i)`. Tail rules are not automatically representative; store the rule and selection probability. Exact error bounds require the actual sampling design. Tail sampler memory is approximately:

```text
open_traces × mean_spans_per_open_trace × encoded_bytes_per_span
```

Use deterministic outcome checks first, rules second, a calibrated model grader third, and humans for consequential ambiguity. Expensive graders run asynchronously on stratified samples. Record grader model, prompt/rubric/version, cost, raw judgment and calibration cohort. A grader is telemetry, not truth; overlap versions and compare to human/deterministic labels before release.

Trajectory monitors include repeated state/action ratio, no-progress window, plan churn, retry amplification, unsupported actions, stale evidence, effect/state mismatch, premature termination, budget exhaustion and route distribution shift. Deterministic tool policy remains the enforcement boundary even if a sequence monitor flags cumulative risk.

### 2.6 Operational invariants

- Every accepted business run emits one terminal run event or a detectable completeness violation.
- Every effectful tool proposal links authorization, idempotency key, external receipt and verified state delta.
- Telemetry never supplies authorization identity; baggage is allowlisted and pseudonymous.
- Raw content is off by default, classified and redacted before external export.
- Metric labels are bounded by contract; high-cardinality detail uses trace/log/artifact references.
- Error, policy, audit and canary evidence outrank verbose content during overload.
- Sampling absence, export loss and producer absence remain distinguishable.
- Semantic-convention and grader versions are explicit and compatibility tested.
- Model final text never substitutes for deterministic environment outcome verification.

## 3. Token Economics & NFR Analysis

### 3.1 Explicit cost per 1,000 runs

These are transparent planning assumptions, not comparable vendor benchmarks. Per 1,000 runs, the agent uses 3M uncached input tokens, 5M cached-prefix reads, 0.02M cache writes and 1M output. Planning rates are `sol $5/$30`, `terra $2/$12`, `luna $0.20/$1.20` per million input/output tokens; cache reads cost 10% of input and cache writes 125%. Replace them with contracted rates.

| Agent tier | No-cache model cost | Cached model cost |
|---|---:|---:|
| `sol` | `8×$5 + 1×$30` = **$70.00** | `3×$5 + 5×$.50 + .02×$6.25 + 1×$30` = **$47.63** |
| `terra` | `8×$2 + 1×$12` = **$28.00** | `3×$2 + 5×$.20 + .02×$2.50 + 1×$12` = **$19.05** |
| `luna` | `8×$.20 + 1×$1.20` = **$2.80** | `3×$.20 + 5×$.02 + .02×$.25 + 1×$1.20` = **$1.91** |

Observability assumptions per 1,000 runs:

- 40 spans/run at 900 bytes = 36 MB emitted; 20% stored base sample = 7.2 MB;
- 12 structured logs/run at 600 bytes = 7.2 MB; 5% content artifacts at 20 KB = 1 MB;
- 15.4 MB (`0.0154 GB`) indexed/ingested at `$0.60/GB` = `$0.0092`;
- one-month hot storage at `$0.20/GB-month` = `$0.0031`; collector/network allocation = `$0.0100`;
- 100 `luna` graders use 0.1M input and 0.01M output = `$0.02 + $0.012 = $0.032`;
- five one-minute human reviews at `$60/hour` = `$5.00`.

Thus observability is **$5.0543/1K runs**, dominated by human calibration. Cached `terra` plus observability is **$24.1043/1K runs**. At 900 policy-compliant successes, cost per 1,000 compliant successes is `$24.1043×1000/900 = $26.78`. Excluded items include backend query seats, replicas, archive, legal hold and incident response; disclose them rather than hiding them in model cost.

```text
observability_cost = SDK/collector compute + network/queue
                   + trace/log/metric/evidence storage/index
                   + grader tokens + human calibration + query/alert
bytes_per_run = trace + logs + attributed metrics + evidence
```

Caching lowers agent cost, but telemetry must record uncached input, cache read, cache write, output and reasoning tokens separately. Cache hit ratio is a monitored outcome; do not infer it from nominal prompt similarity.

### 3.2 Latency SLOs

Internal starting targets, to be load-tested in the deployment:

| Path | p50 | p95 | p99 | Tail mitigation |
|---|---:|---:|---:|---|
| In-process span/event creation | 20 µs | 100 µs | 500 µs | content off, bounded attributes, batch processor |
| App -> local collector enqueue | 0.2 ms | 1 ms | 5 ms | nonblocking bounded queue, local socket |
| Collector export freshness | 2 s | 10 s | 30 s | batch tuning, WAL, reserved exporter capacity |
| Metrics dashboard freshness | 15 s | 30 s | 60 s | recording rules, bounded labels |
| Error trace searchable | 10 s | 30 s | 2 min | priority queue, trace-ID routed tail sampling |
| SLO alert delivery | 30 s | 2 min | 5 min | multi-window rules, redundant notification path |
| Async quality result | 30 s | 5 min | 20 min | stratified queue, cheaper fallback grader |
| Trace query, 24-hour scope | 0.5 s | 2 s | 8 s | partition/index by bounded fields, archive separately |

Measure with telemetry enabled and disabled. User-path overhead and telemetry freshness are distinct. Report successful, failed, timed-out and cancelled runs separately; end-to-end latency includes retries, queueing and approval.

### 3.3 Throughput, buffers, and backpressure

At 500 runs/s, 40 spans/run and 900 bytes/span:

```text
span rate              = 500×40 = 20,000 spans/s
raw trace ingress      = 20,000×900 = 18 MB/s
logs                   = 500×12×600 B = 3.6 MB/s
total before artifacts = 21.6 MB/s
10-minute outage buffer, 1.5× safety = 21.6×600×1.5 = 19.44 GB
tail sampler, 20k open traces×40×900 B = 720 MB + implementation overhead
```

If sustainable export is 15 MB/s during degradation and 10 GB remains, `time_to_full = 10,000 MB / (21.6-15) MB/s = 1,515 s`, about 25 minutes. Scale before 60-70% exporter queue occupancy after verifying the backend is not the bottleneck. Consistently route trace IDs for stateful tail sampling.

Backpressure policy: reject/strip optional raw content, then verbose success events, while preserving security/audit, effects, errors, terminal roots and canaries. Bound queues and retry duration, honor `Retry-After`, use full jitter, meter OTLP partial success, and never create unbounded application memory. Partition fairness by service/tenant plan/risk so one high-fan-out run cannot consume every open-trace slot.

### 3.4 NFR targets and trade-offs

| NFR | Target/evidence | Trade-off |
|---|---|---|
| Product telemetry coverage | 100% terminal count; >=99.9% required-span coverage | More instrumentation and contract tests |
| Export availability | 99.95% metadata pipeline; independently 99.99% security audit | Separate durable path costs more |
| Loss/freshness | <0.1% nonessential metadata loss/month; zero unreported loss | At-least-once duplicates and larger WAL |
| RPO | 0 for effect/policy/audit; <=5 min aggregate metrics; sampled traces by policy | Synchronous audit/outbox adds effect latency |
| RTO | <=15 min regional collector; <=60 min query backend; alerts remain via alternate path | Warm capacity and operational complexity |
| Privacy | zero prohibited fields; raw content off; audited access/deletion | Less immediate debugging context |
| Cardinality | no overflow on critical metrics; declared series budgets | Coarser aggregate slicing |
| Query | p95 2 s for 24-hour incident lookup | Index/storage cost |
| Compliance | field inventory, residency, retention, legal hold, deletion lineage | Multiple region/tier stores |
| Quality | versioned graders calibrated to human/deterministic sample | Evaluation cost and delayed signal |

## 4. Distributed Resilience & Security

### 4.1 Durable telemetry and workflow integration

```text
┌──────────────┐ state/effect ┌──────────────┐ outbox/CDC  ┌──────────────┐
│ Temporal     ├─────────────►│ DB/checkpoint├────────────►│ Kafka audit  │
│ workflow     │              │ + effect log │             │ partitions   │
└──────┬───────┘              └──────────────┘             └──────┬───────┘
       │ spans/logs nonblocking                               idempotent event
       ▼                                                            ▼
┌──────────────┐ batch/WAL   ┌──────────────┐ trace-ID key  ┌──────────────┐
│ local OTel   ├────────────►│ regional     ├──────────────►│ sampler/store│
│ collector    │             │ gateway      │               │ /DLQ/archive │
└──────────────┘             └──────────────┘               └──────────────┘
```

Temporal workflow replay must not re-emit a logical event as new truth. Derive a stable event ID from workflow/run/step/transition and deduplicate, while giving each physical retry its own attempt span. Persist checkpoint/effect and outbox atomically. Kafka partitions by run preserve per-run order; consumers remain idempotent because delivery is at least once. Poison schema/content events go to a dead-letter queue with reason and original hash; business denials are terminal facts, not poison retries.

Collector queues are bounded. A file-backed WAL survives restart; Kafka decouples tiers when the operational burden is justified. Essential audit uses an independent authenticated durable route. On backend outage, exponential full-jitter retry and closed/open/half-open breakers prevent exporter storms. Recovery drain is rate-limited so it does not starve current traffic.

Failure classes:

| Class | Examples | Handling |
|---|---|---|
| Transient | throttle, timeout, collector/backend unavailable | bounded retry/jitter, breaker, WAL |
| Permanent | invalid schema, unauthorized exporter, oversized prohibited field | reject/quarantine/DLQ; no blind retry |
| Partial success | backend rejects subset | count rejected, retry only when protocol permits |
| Duplicate | exporter retry after unknown acknowledgement | stable event ID, dedupe/at-least-once-aware query |
| Poison trajectory | invalid sequence, conflicting event ID/hash | quarantine run partition, alert owner |
| Silent absence | dead producer, broken query/alert route | expected-volume, heartbeat and end-to-end canary |

### 4.2 Telemetry integrity, privacy, and chain of custody

Telemetry is a high-value copy of prompts, source code, documents, identities, credentials and actions. Minimize before export:

```text
allowlist fields -> classify -> secret/PII detect -> redact/tokenize
                 -> hash + governed artifact reference -> route by residency
                 -> append-only audit -> retention/deletion/legal hold
```

Regex is insufficient for contextual PII; combine field allowlists, deterministic secret patterns, contextual detection and test fixtures. Secrets are never retained, even in diagnostic mode. Raw evidence stays encrypted in the governed system of record and is resolved through audited, tenant-scoped break-glass access. Hash chains prove alteration only relative to a trusted external anchor; use append-only/WORM storage, signatures, synchronized time and restoration tests.

Tool/model input and output capture is off by default. Never capture hidden reasoning. Record normalized proposal hash, policy decision, exact approval, tool call/idempotency ID, external receipt and before/after state reference. These reconstruct an effect without copying regulated payloads.

### 4.3 Zero Trust MCP and access control

An MCP server, its tool descriptions, results and errors are third-party data, not telemetry authority. The MCP host authenticates user and workload; the tool proxy enforces action/resource policy and emits a decision event. Do not pass incoming baggage as tenant identity or an inbound MCP token to a downstream telemetry exporter.

- mutual TLS/workload identity authenticates OTLP producers, collectors and exporters;
- tool-level RBAC establishes role baselines; ABAC narrows by tenant, task, resource, policy, destination and data class;
- SRE sees aggregate metrics/redacted traces; application owners see scoped metadata; evaluators see de-identified samples; security/privacy receives audited elevated access;
- agents and model/tool runtimes cannot alter or delete their audit stream;
- collectors run non-root with minimal components, constrained receivers/exporters, secret-manager credentials and egress allowlists;
- every raw-content view/export is watermarked, rate/volume controlled and immutably audited.

### 4.4 Sampling, retention, and incident safety

Sampling never applies to the authoritative effect/policy audit. Operational trace retention is separate from raw content, evaluator annotations, incident evidence and metrics. Define field-level purpose, TTL, region, encryption, legal hold and deletion propagation across trace DB, log index, object store, datasets, caches and backups. Production trajectories cannot silently become training/eval data.

An incident begins by establishing telemetry completeness. Preserve trace/log/artifact/checkpoint hashes, create an incident ID, and build a timeline from authenticated events and external receipts. Contain with route rollback, tool disable, budget/breaker, workload revocation and feature flags; reconcile side effects. Convert the trajectory into a privacy-reviewed regression case and verify the missing instrumentation or alert path.

### 4.5 Representative failures and controls

| Failure | Detect | Contain/recover |
|---|---|---|
| Queue loses trace context | orphan worker/link coverage | contract-test inject/extract; link recovered task |
| Framework/manual duplicate spans | usage exceeds provider count | ownership map; suppress one instrumentor |
| GenAI convention upgrade breaks query | missing dimension at canary | pinned mapping, dual-write, compatibility test |
| Dynamic names/labels explode cardinality | series/name growth, overflow | stable names, bounded allowlist, exemplars |
| Tail sampler splits/overloads trace | partial roots, queue saturation | trace-ID routing, memory budget, fallback policy |
| Biased sample reported as population | estimate disagrees with counters | retain probability/rule, unsampled denominator, weighting |
| Raw prompt/secret exported | DLP/canary/access audit | pre-export redaction, quarantine, rotate secret |
| Agent tampers with evidence | chain gap/hash mismatch | out-of-process WORM audit and trusted anchors |
| Export retry storm | attempts/CPU/queue surge | jitter, breaker, WAL cap, one retry owner |
| Process exits before flush | accepted roots exceed exported roots | shutdown flush, local collector, completeness alert |
| Clock skew misorders steps | negative times/sequence gaps | monotonic duration, sequence IDs, causal links |
| Grader drift appears as product drift | release-cohort score discontinuity | overlap/calibrate/version; deterministic checks |
| Pipeline silently stops | missing volume/canary/freshness | independent alert route and regional failover |

The telemetry stack is part of the system under observation. Load-test normal, error, retry, fan-out, large-payload and backend-outage paths; measure application overhead, queue growth, drops, recovery drain, search latency and alert delivery.

## 5. Production Enterprise Code

This Python 3.11 standard-library program is a runnable telemetry edge. It validates/generates W3C-compatible trace identity, redacts before queueing, applies deterministic/error-aware sampling, enforces bounded metric labels, appends a trajectory hash chain, exports through primary then secondary sinks with exponential full jitter and closed/open/half-open breakers, and falls back to an fsynced JSONL WAL. A bounded queue drops optional data first and directly spools essential evidence during overload. Production adapters replace the in-memory transports and local WAL with authenticated OTLP, Kafka/object-lock audit and a managed key service while retaining the same contracts.

```python
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import random
import re
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Protocol, Sequence


class TransientFailure(RuntimeError):
    """A retryable transport failure."""


class PermanentFailure(RuntimeError):
    """A non-retryable schema or authorization failure."""


class CircuitOpen(TransientFailure):
    """The dependency is isolated until its recovery probe."""


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = {
            "timestamp": time.time(),
            "severity": record.levelname,
            "message": record.getMessage(),
        }
        for key in ("trace_id", "run_id", "event_name", "sink", "attempt",
                    "status", "queue_depth"):
            if hasattr(record, key):
                event[key] = getattr(record, key)
        return json.dumps(event, separators=(",", ":"), sort_keys=True)


logger = logging.getLogger("telemetry-edge")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


class Breaker:
    def __init__(self, threshold: int = 2, recovery_s: float = 2.0):
        self._threshold = threshold
        self._recovery_s = recovery_s
        self._failures = 0
        self._state = "closed"
        self._opened_at = 0.0
        self._probe_active = False
        self._lock = threading.Lock()

    def before(self) -> None:
        with self._lock:
            if self._state == "open":
                if time.monotonic() - self._opened_at < self._recovery_s:
                    raise CircuitOpen("circuit open")
                self._state = "half_open"
            if self._state == "half_open":
                if self._probe_active:
                    raise CircuitOpen("half-open probe already active")
                self._probe_active = True

    def success(self) -> None:
        with self._lock:
            self._state = "closed"
            self._failures = 0
            self._probe_active = False

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._probe_active = False
            if self._state == "half_open" or self._failures >= self._threshold:
                self._state = "open"
                self._opened_at = time.monotonic()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state


TRACEPARENT = re.compile(
    r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$"
)


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str
    sampled: bool

    @classmethod
    def start(cls, inbound: str | None = None) -> "TraceContext":
        if inbound:
            match = TRACEPARENT.fullmatch(inbound.strip().lower())
            if match and int(match.group(1), 16) and int(match.group(2), 16):
                return cls(match.group(1), uuid.uuid4().hex[:16],
                           bool(int(match.group(3), 16) & 1))
        return cls(uuid.uuid4().hex, uuid.uuid4().hex[:16], False)

    def child(self) -> "TraceContext":
        return TraceContext(self.trace_id, uuid.uuid4().hex[:16], self.sampled)

    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{'01' if self.sampled else '00'}"


EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
CARD = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
SECRET = re.compile(
    r"(?i)\b(api[_-]?key|password|token)\s*[:=]\s*[^\s,;]+"
)


def redact(value: object) -> object:
    if isinstance(value, str):
        value = EMAIL.sub("[EMAIL]", value)
        value = CARD.sub("[PAYMENT_DATA]", value)
        return SECRET.sub(lambda m: f"{m.group(1)}=[SECRET]", value)
    if isinstance(value, dict):
        return {str(key): redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[UNSUPPORTED_TYPE]"


EVENT_ATTRIBUTE_ALLOWLIST = {
    "workflow", "tool", "receipt", "diagnostic_sample", "model_tier",
    "policy_decision", "termination_reason",
}


def sanitize_attributes(attributes: dict[str, object]) -> dict[str, object]:
    return {key: redact(value) for key, value in attributes.items()
            if key in EVENT_ATTRIBUTE_ALLOWLIST}


@dataclass(frozen=True)
class TelemetryEvent:
    event_id: str
    timestamp: float
    observed_timestamp: float
    event_name: str
    schema_version: str
    trace_id: str
    span_id: str
    run_id: str
    step_id: int
    status: str
    essential: bool
    attributes: dict[str, object]
    artifact_ref: str | None = None
    previous_hash: str | None = None
    event_hash: str | None = None

    def canonical(self, include_hash: bool = False) -> bytes:
        value = asdict(self)
        if not include_hash:
            value.pop("event_hash", None)
        return json.dumps(value, separators=(",", ":"),
                          sort_keys=True).encode()


class DeterministicSampler:
    def __init__(self, base_rate: float):
        if not 0 <= base_rate <= 1:
            raise ValueError("base_rate must be within [0, 1]")
        self._threshold = int(base_rate * ((1 << 64) - 1))

    def retain(self, event: TelemetryEvent) -> bool:
        always = (event.essential or event.status in {"error", "denied"}
                  or event.event_name.startswith("security."))
        value = int(hashlib.sha256(event.trace_id.encode()).hexdigest()[:16], 16)
        return always or value <= self._threshold


class MetricGuard:
    ALLOWED = {
        "region": {"in-blr", "in-hyd"},
        "workflow": {"support", "payment"},
        "status": {"ok", "error", "denied", "degraded"},
        "model_tier": {"sol", "terra", "luna"},
    }

    def __init__(self):
        self._counters: dict[object, float] = {}
        self._lock = threading.Lock()

    def add(self, name: str, value: float, labels: dict[str, str]) -> None:
        if not name.startswith("agent."):
            raise PermanentFailure("metric name outside registered namespace")
        if any(key not in self.ALLOWED or item not in self.ALLOWED[key]
               for key, item in labels.items()):
            raise PermanentFailure("unbounded or unknown metric label")
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    def series_count(self) -> int:
        with self._lock:
            return len(self._counters)


class TrajectoryLedger:
    """Thread-safe per-run ordering and tamper-evident hash chaining."""

    def __init__(self):
        self._last: dict[str, tuple[int, str]] = {}
        self._events: dict[str, dict[str, TelemetryEvent]] = {}
        self._lock = threading.Lock()

    def append(self, event: TelemetryEvent) -> TelemetryEvent:
        event = replace(event, attributes=sanitize_attributes(event.attributes))
        with self._lock:
            existing = self._events.setdefault(event.run_id, {}).get(event.event_id)
            if existing:
                proposed = replace(event, previous_hash=existing.previous_hash,
                                   event_hash=existing.event_hash)
                if proposed.canonical(True) != existing.canonical(True):
                    raise PermanentFailure("conflicting duplicate event ID")
                return existing
            last_step, previous = self._last.get(event.run_id, (0, "0" * 64))
            if event.step_id != last_step + 1:
                raise PermanentFailure("trajectory step is not monotonic")
            staged = replace(event, previous_hash=previous)
            digest = hashlib.sha256(previous.encode() + staged.canonical()).hexdigest()
            committed = replace(staged, event_hash=digest)
            self._events[event.run_id][event.event_id] = committed
            self._last[event.run_id] = (event.step_id, digest)
            return committed

    def verify(self, run_id: str) -> bool:
        with self._lock:
            events = sorted(self._events.get(run_id, {}).values(),
                            key=lambda event: event.step_id)
        previous = "0" * 64
        for expected_step, event in enumerate(events, 1):
            if event.step_id != expected_step or event.previous_hash != previous:
                return False
            expected = hashlib.sha256(
                previous.encode() + replace(event, event_hash=None).canonical()
            ).hexdigest()
            if not event.event_hash or not hmac.compare_digest(expected,
                                                               event.event_hash):
                return False
            previous = event.event_hash
        return bool(events)


class Sink(Protocol):
    name: str

    def export(self, events: Sequence[TelemetryEvent]) -> None:
        """Export the batch or raise a classified failure."""


class MemorySink:
    def __init__(self, name: str, failures_before_success: int = 0):
        self.name = name
        self._failures = failures_before_success
        self.events: dict[str, TelemetryEvent] = {}

    def export(self, events: Sequence[TelemetryEvent]) -> None:
        if self._failures > 0:
            self._failures -= 1
            raise TransientFailure(f"{self.name} unavailable")
        for event in events:
            self.events[event.event_id] = event


class WalSink:
    name = "deterministic-wal"

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def export(self, events: Sequence[TelemetryEvent]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("ab", buffering=0) as stream:
            for event in events:
                stream.write(event.canonical(True) + b"\n")
            os.fsync(stream.fileno())

    def line_count(self) -> int:
        if not self.path.exists():
            return 0
        with self.path.open("rb") as stream:
            return sum(1 for _ in stream)


class ResilientExporter:
    def __init__(self, primary: Sink, secondary: Sink, wal: WalSink,
                 sampler: DeterministicSampler, max_queue: int = 100):
        if max_queue < 1:
            raise ValueError("max_queue must be positive")
        self._primary, self._secondary, self._wal = primary, secondary, wal
        self._sampler = sampler
        self._queue: deque[TelemetryEvent] = deque()
        self._max_queue = max_queue
        self._breakers = {primary.name: Breaker(), secondary.name: Breaker()}
        self._lock = threading.Lock()
        self.dropped_optional = 0

    def submit(self, event: TelemetryEvent) -> str:
        clean = replace(event, attributes=sanitize_attributes(event.attributes))
        if not self._sampler.retain(clean):
            return "sampled_out"
        with self._lock:
            if len(self._queue) < self._max_queue:
                self._queue.append(clean)
                return "queued"
            if not clean.essential:
                self.dropped_optional += 1
                return "dropped_optional"
        self._wal.export((clean,))
        return "spooled_essential"

    def flush(self, deadline_s: float = 1.0) -> str:
        with self._lock:
            batch = tuple(self._queue)
            self._queue.clear()
        if not batch:
            return "empty"
        deadline = time.monotonic() + deadline_s
        for sink in (self._primary, self._secondary):
            if self._try_sink(sink, batch, deadline):
                return sink.name
        self._wal.export(batch)
        logger.warning("export degraded to durable WAL", extra={
            "sink": self._wal.name, "status": "degraded",
            "queue_depth": len(batch), "trace_id": batch[0].trace_id,
            "run_id": batch[0].run_id,
            "event_name": batch[0].event_name,
        })
        return self._wal.name

    def _try_sink(self, sink: Sink, batch: Sequence[TelemetryEvent],
                  deadline: float) -> bool:
        breaker = self._breakers[sink.name]
        for attempt in range(1, 3):
            if time.monotonic() >= deadline:
                return False
            try:
                breaker.before()
                sink.export(batch)
                breaker.success()
                logger.info("export completed", extra={
                    "sink": sink.name, "attempt": attempt, "status": "ok",
                    "queue_depth": len(batch), "trace_id": batch[0].trace_id,
                    "run_id": batch[0].run_id,
                    "event_name": batch[0].event_name,
                })
                return True
            except CircuitOpen:
                return False
            except PermanentFailure:
                return False
            except (TransientFailure, TimeoutError) as exc:
                breaker.failure()
                logger.warning("export retryable failure", extra={
                    "sink": sink.name, "attempt": attempt,
                    "status": type(exc).__name__, "queue_depth": len(batch),
                    "trace_id": batch[0].trace_id,
                    "run_id": batch[0].run_id,
                    "event_name": batch[0].event_name,
                })
                if attempt < 2:
                    cap = min(0.02 * (2 ** (attempt - 1)),
                              max(0.0, deadline - time.monotonic()))
                    time.sleep(random.uniform(0.0, cap))
        return False


def new_event(context: TraceContext, run_id: str, step: int,
              name: str, status: str, essential: bool,
              attributes: dict[str, object]) -> TelemetryEvent:
    now = time.time()
    return TelemetryEvent(
        event_id=hashlib.sha256(
            f"{run_id}:{step}:{name}".encode()).hexdigest()[:24],
        timestamp=now, observed_timestamp=now,
        event_name=name, schema_version="agent-telemetry/1.0",
        trace_id=context.trace_id, span_id=context.span_id,
        run_id=run_id, step_id=step, status=status,
        essential=essential, attributes=attributes,
    )


def main() -> None:
    context, run_id = TraceContext.start(), "run-support-42"
    ledger, metrics = TrajectoryLedger(), MetricGuard()
    accepted = ledger.append(new_event(
        context, run_id, 1, "agent.run.accepted", "ok", True,
        {"workflow": "support", "diagnostic_sample":
         "Contact Ana@example.com; token=do-not-export"},
    ))
    completed = ledger.append(new_event(
        context.child(), run_id, 2, "agent.tool.completed", "ok", True,
        {"tool": "refund.lookup", "receipt": "refund-status-verified"},
    ))
    metrics.add("agent.runs", 1, {
        "region": "in-blr", "workflow": "support", "status": "ok",
        "model_tier": "terra",
    })

    with tempfile.TemporaryDirectory() as directory:
        wal = WalSink(Path(directory) / "telemetry.wal.jsonl")
        primary = MemorySink("primary-otlp", failures_before_success=3)
        secondary = MemorySink("secondary-otlp")
        exporter = ResilientExporter(
            primary, secondary, wal, DeterministicSampler(1.0), max_queue=8
        )
        exporter.submit(accepted)
        exporter.submit(completed)
        first_route = exporter.flush()

        terminal = ledger.append(new_event(
            context.child(), run_id, 3, "agent.run.completed", "ok", True,
            {"workflow": "support", "termination_reason": "verified"},
        ))
        outage = ResilientExporter(
            MemorySink("primary-down", 3), MemorySink("secondary-down", 3),
            wal, DeterministicSampler(1.0), max_queue=1,
        )
        outage.submit(terminal)
        second_route = outage.flush()
        stored = next(iter(secondary.events.values()))
        print(json.dumps({
            "firstRoute": first_route,
            "secondRoute": second_route,
            "secondaryEvents": len(secondary.events),
            "trajectoryValid": ledger.verify(run_id),
            "metricSeries": metrics.series_count(),
            "piiRedacted": "[EMAIL]" in str(stored.attributes)
                           and "[SECRET]" in str(stored.attributes),
            "walLines": wal.line_count(),
        }, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
```

Expected terminal result: the unavailable primary routes the batch to `secondary-otlp`; a total backend outage routes to `deterministic-wal`; the trajectory verifies; PII/secrets are redacted before either sink; and metric cardinality remains one bounded series. Logs expose correlation and retry state without payloads.

## 6. Architectural System Design Scenarios

### Scenario 1 - High-volume customer-support agent

**Problem statement.** Design observability for a multi-tenant support agent handling 30,000 requests/minute (500/s) across two regions. Each run averages 40 spans and 12 logs. Requirements are p99 user latency under 8 seconds, p95 error-trace searchability under 30 seconds, 99.95% metadata export availability, policy-compliant task success above 92%, zero raw payment data in external telemetry, and 10 minutes of regional backend outage tolerance.

**Proposed architecture.** Application SDKs emit stable OTel operations to node-local collectors. Prompts, tool payloads and customer messages remain in the support system; telemetry carries classified artifact references and hashes. Gateways use trace-ID routing, bounded transforms, pre-export DLP, unsampled RED/quality counters, 100% retention of errors/policy/outcome mismatch, and a 5% deterministic base sample of normal successes. A separate asynchronous evaluator samples by language, intent, release and escalation outcome. Security/audit events enter Kafka/WORM storage independently. End-to-end canaries assert backend query and page delivery.

```text
┌──────────────┐ OTel  ┌──────────────┐ OTLP  ┌───────────────────────────┐
│ API + agent  ├──────►│ node-local   ├──────►│ regional gateway          │
│ 500 runs/s   │       │ batch/redact │       │ trace-key/tail/WAL/DLP    │
└──────┬───────┘       └──────────────┘       └────┬─────┬─────┬─────────┘
       │ governed content refs                       │     │     │
       ▼                                             ▼     ▼     ▼
┌──────────────┐                              ┌────────┐┌──────┐┌──────────┐
│ support data │                              │TSDB/SLO││trace ││Kafka/WORM│
│ ACL/tenant   │                              │alerts  ││logs  ││security  │
└──────────────┘                              └────┬───┘└──┬───┘└────┬─────┘
                                                │       │         │
                                                └───────┴─────────┴──► incident/eval
```

Capacity uses the Section 3 workload: 20,000 spans/s, 21.6 MB/s pre-artifact ingress, and a 19.44 GB ten-minute regional buffer at 1.5x safety. Tail sampling needs at least 720 MB raw trace state plus runtime/index/headroom; provision and load-test substantially above the estimate. Fair queues prevent a large tenant or looping run from exhausting open-trace state.

| Approach | Cost | Latency | Operations | Security/privacy | Scalability ceiling |
|---|---|---|---|---|---|
| Direct SDK export of all content to one SaaS | High ingest | Low hop count until throttled | Low initially | Unacceptable raw-data concentration and loss coupling | Vendor/backend throttle |
| **Local collectors + regional gateways + references + hybrid sampling** | Medium | Bounded; p95 error freshness target | Medium-high routing/WAL/schema work | Redaction before boundary; independent audit | High with trace-key shards |
| Self-host every signal/content at 100% | Highest infrastructure | Query degrades with volume | Highest | Maximum placement control, larger sensitive copy | Storage/index operations |

**Decision rationale.** The hybrid design preserves population metrics and every consequential event while controlling content, cost and tail-sampler state. Local redaction meets the privacy boundary; trace-key sharding preserves complete traces; independent audit prevents normal telemetry shedding from erasing security evidence. Direct all-content export violates the data requirement, while full retention spends heavily without improving every diagnostic question.

### Scenario 2 - Regulated financial action agent

**Problem statement.** Design observability for an agent executing 100,000 invoice decisions/day across 150 regulated tenants. A run may last 24 hours across approval and reconciliation. Requirements are RPO zero for policy/approval/effect evidence, p99 machine authorization under 100 ms, complete tenant isolation, effect reconstruction within 15 minutes, seven-year audit retention, customer deletion for non-audit content, and no account numbers or identity documents in the operational trace.

**Proposed architecture.** Temporal owns the workflow and writes checkpoint, normalized effect hash, idempotency key and outbox event in the business transaction. The outbox feeds a tenant-keyed Kafka audit stream; consumers append signed/hash-chained records and processor receipts to regional object-lock storage. Operational OTel traces are segmented by resumable run phase and joined with a stable opaque business group ID. They contain versions, policy decision ID, approval digest, tool call ID and artifact references, never financial payloads. A privileged resolver maps opaque references to the system of record under dual-controlled, immutable access audit. SRE metrics remain tenant-free; regulated audit is never sampled.

```text
┌──────────────┐ checkpoint/effect ┌──────────────┐ outbox  ┌──────────────┐
│ Temporal     ├──────────────────►│ ledger DB    ├────────►│ Kafka audit  │
│ long workflow│                   │ RPO 0        │         │ tenant key   │
└──────┬───────┘                   └──────────────┘         └──────┬───────┘
       │ phase traces                                                  │
       ▼                                                               ▼
┌──────────────┐ OTLP  ┌──────────────┐                         ┌──────────────┐
│ local/redact ├──────►│ ops trace +  │                         │ object-lock  │
│ no raw data  │       │ TSDB/SIEM    │                         │ signed audit │
└──────────────┘       └──────┬───────┘                         └──────┬───────┘
                              │ opaque refs                            │
                              └──────────────┬─────────────────────────┘
                                             ▼
                                    ┌──────────────────┐
                                    │ audited resolver │──► system of record
                                    └──────────────────┘
```

At 100,000/day the mean is 1.16 runs/s, but design for a 20x close-of-month peak of 23.2 runs/s. At 120 spans/run and 1 KB/span, peak operational trace ingress is about 2.78 MB/s before logs. A 30-minute 2x-safety buffer is about 10 GB. Audit capacity follows effect events rather than trace samples; reconciliation monitors compare ledger, provider receipts and immutable audit counts by opaque tenant partition.

| Approach | Cost | Latency | Operations | Security/governance | Scalability ceiling |
|---|---|---|---|---|---|
| Operational trace database as legal audit | Low-medium | Simple queries | Low | Sampling/deletion/mutation cannot satisfy RPO 0 or evidence custody | Trace-store lifecycle conflict |
| **Transactional outbox + Kafka + object-lock audit; separate sampled ops traces** | Medium-high | Async audit; authorization remains local | High schema/key/retention operations | Strong custody, isolation, deletion separation | High via tenant partitions |
| Synchronous write to remote audit service before every action | High redundant service | Adds tail latency and outage coupling | Medium-high | Strong acknowledgement but availability risk | Remote audit bottleneck |

**Decision rationale.** The outbox binds evidence to committed business state without placing a regional audit round trip on the authorization latency path. Separating immutable audit from sampled operational telemetry resolves incompatible retention and deletion rules. Opaque references let operators restore service without viewing regulated documents, while dual-controlled resolution supports investigation. The workflow's phase traces explain execution; provider receipt and state delta prove the effect.

## Interview Review

1. **Trace versus trajectory?** A trace models causal timed operations; a trajectory adds ordered goal, evidence, proposal, policy, effect, state and outcome semantics.
2. **Why not put prompts in spans?** They are sensitive, large and high-cardinality. Store bounded metadata, hashes and governed references; selectively retain redacted content.
3. **Head or tail sampling?** Use a known-probability head sample for population estimation and tail rules for consequential/outlier traces; keep unsampled counters.
4. **How is trace completeness proved?** Required-path contracts, parent/link coverage, emitted/exported/rejected counts, provider/business reconciliation and an end-to-end canary.
5. **Can a run replay exactly?** Usually not. Preserve versions, inputs, state, artifacts and receipts for explanation/re-simulation; mutable services and stochastic models prevent a universal guarantee.
6. **What pages an operator?** User-visible SLO burn, unsafe effects, material cost/quality/security symptoms and telemetry blindness.
7. **How do you control cardinality?** Bounded label registry and budgets, stable operation names, normalized classes, exemplars and high-cardinality trace/log/artifact lookup.
8. **What is standards maturity?** Core OTel/OTLP are mature specifications; OTel GenAI conventions are Development, OpenInference is separate, and no complete stable trajectory schema spans frameworks.

## Primary References

- [OpenTelemetry specification](https://opentelemetry.io/docs/specs/otel/)
- [OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/)
- [OpenTelemetry GenAI span conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)
- [OpenTelemetry GenAI metric conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md)
- [OTLP specification](https://opentelemetry.io/docs/specs/otlp/)
- [W3C Trace Context](https://www.w3.org/TR/trace-context/)
- [OpenTelemetry logs data model](https://opentelemetry.io/docs/specs/otel/logs/)
- [OpenTelemetry baggage](https://opentelemetry.io/docs/concepts/signals/baggage/)
- [OpenTelemetry sampling](https://opentelemetry.io/docs/concepts/sampling/)
- [OpenTelemetry Collector resiliency](https://opentelemetry.io/docs/collector/resiliency/)
- [OpenTelemetry Collector scaling](https://opentelemetry.io/docs/collector/scaling/)
- [OpenTelemetry Collector security](https://opentelemetry.io/docs/security/config-best-practices/)
- [Prometheus instrumentation practices](https://prometheus.io/docs/practices/instrumentation/)
- [Google SRE monitoring](https://sre.google/sre-book/monitoring-distributed-systems/)
- [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
- [OpenInference specification](https://github.com/Arize-ai/openinference/tree/main/spec)
- [NIST SP 800-61r3](https://csrc.nist.gov/pubs/sp/800/61/r3/final)
- [Anthropic: Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
- [AgentTrace](https://arxiv.org/abs/2602.10133)
