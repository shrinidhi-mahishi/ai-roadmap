# Module 05: Observability for LLM & Agent Systems

## What Is This?

Traditional monitoring is like having a speedometer and fuel gauge in your car -- you know how fast you are going and how much gas is left. LLM observability is like needing those gauges plus a dashcam that records every turn, a GPS log of the full route, a fuel-cost-per-mile tracker, and a judge who scores whether you actually drove to the right destination. The car (your agent) can reach the wrong address while every gauge reads green.

That is the core problem: an LLM application can return an HTTP 200 in 500ms with a perfectly formatted response that is completely wrong. Traditional APM (Application Performance Monitoring) tools like Datadog and New Relic were built for deterministic systems where a 200 status code means success. In LLM systems, correctness must be measured directly because it cannot be inferred from status codes or latency.

## Why It Matters

Models do not break -- they drift. Bias increases, hallucination frequency rises, precision degrades. These are probabilistic, not binary, and invisible to conventional monitoring. A 3-second request costing $0.002 looks identical to one costing $0.40 in latency dashboards. Only token-level observability reveals the difference. Organizations that treat observability as optional discover quality regressions from customer complaints, not dashboards.

---

## Part 1: System Topology & Data Flow

### End-to-End Observability Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         APPLICATION LAYER                                │
│                                                                          │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────┐                │
│  │  Agent       │───>│  LLM Client  │───>│  Tool Calls  │                │
│  │  Orchestrator│    │  (Anthropic/  │    │  (APIs, DBs, │                │
│  │             │<───│   OpenAI)    │<───│   search)    │                │
│  └──────┬──────┘    └──────┬───────┘    └──────┬───────┘                │
│         │                  │                    │                        │
│         │    OTel SDK instrumentation (spans + events + metrics)         │
│         v                  v                    v                        │
├──────────────────────────────────────────────────────────────────────────┤
│                     TELEMETRY PIPELINE                                   │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────┐        │
│  │                   PII REDACTION LAYER                        │        │
│  │  (SDK-level: pre-serialization, first line of defense)       │        │
│  └──────────────────────────┬───────────────────────────────────┘        │
│                              │                                           │
│  ┌──────────────────────────v───────────────────────────────────┐        │
│  │                  OTel COLLECTOR                               │        │
│  │                                                               │        │
│  │  ┌──────────┐  ┌──────────────┐  ┌────────────────────┐     │        │
│  │  │ Receiver │  │  Processors  │  │    Exporters       │     │        │
│  │  │ (OTLP)   │->│  - Redaction │->│  - Traces: Jaeger  │     │        │
│  │  │          │  │  - Tail      │  │  - Metrics: Prom   │     │        │
│  │  │          │  │    sampling  │  │  - Logs: Loki      │     │        │
│  │  │          │  │  - Attribute │  │  - LLM Obs: Langfuse│    │        │
│  │  │          │  │    transform │  │    / Arize / Datadog│     │        │
│  │  └──────────┘  └──────────────┘  └────────────────────┘     │        │
│  └──────────────────────────────────────────────────────────────┘        │
├──────────────────────────────────────────────────────────────────────────┤
│                      STORAGE & ANALYSIS                                  │
│                                                                          │
│  ┌────────────────┐  ┌────────────────┐  ┌──────────────────────┐       │
│  │  Trace Store   │  │  Metrics Store │  │  Content Store        │       │
│  │  (Jaeger v2 /  │  │  (Prometheus / │  │  (Access-controlled,  │       │
│  │   Tempo)       │  │   VictoriaM)  │  │   separate from       │       │
│  │               │  │               │  │   telemetry -- holds   │       │
│  │  Token counts, │  │  Aggregated   │  │   prompt/completion    │       │
│  │  latency,     │  │  time series  │  │   content for replay)  │       │
│  │  cost, spans  │  │               │  │                        │       │
│  └───────┬────────┘  └───────┬────────┘  └──────────┬─────────────┘     │
│          │                   │                       │                   │
│          v                   v                       v                   │
│  ┌──────────────────────────────────────────────────────────────┐        │
│  │                    DASHBOARDS & ALERTS                        │        │
│  │                                                               │        │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │        │
│  │  │Operational│  │ Economic │  │ Quality  │  │ Safety   │    │        │
│  │  │ (latency, │  │ (cost/   │  │ (eval    │  │(guardrail│    │        │
│  │  │  errors,  │  │  req,    │  │  pass %, │  │ triggers,│    │        │
│  │  │  TTFT)    │  │  cost/   │  │  halluc. │  │ PII      │    │        │
│  │  │          │  │  team)   │  │  rate)   │  │ events)  │    │        │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │        │
│  └──────────────────────────────────────────────────────────────┘        │
└──────────────────────────────────────────────────────────────────────────┘
```

### Request-Flow Narrative

1. **Application Layer**: A user request enters the agent orchestrator, which generates a top-level agent span (`gen_ai.agent.name`, task description). The agent calls an LLM (mid-level span: `gen_ai.request.model`, input/output tokens, finish reason). The LLM may invoke tools (leaf-level spans: tool name, arguments, results). Content capture (prompts, completions) is opt-in via `gen_ai.input.messages` / `gen_ai.output.messages`.

2. **PII Redaction**: Before telemetry leaves the SDK, PII is detected and redacted at the structured-logging layer. This is the correct primary control -- post-hoc redaction at the backend is incomplete because any consumer reading during the unmasked window sees raw data. Real-world incident: a customer's voice agent logged complete credit card numbers in OTel spans for three weeks because redaction existed only in the transcript display, not in the telemetry pipeline.

3. **OTel Collector**: Central processing. The Redaction Processor applies regex-based pattern matching as a second layer. Tail-based sampling retains error traces, high-latency traces, and traces with low eval scores at 100%, while sampling routine successes at 1-5%. Attribute transforms normalize model names (`gpt-4o-2024-11-20` -> `gpt-4o`).

4. **Storage**: Traces and metrics go to their respective stores. Critically, token counts and timestamps (low-risk) are stored in the observability backend; prompt content and completions (high-risk) are stored in a separate, access-controlled content store. Under GDPR Article 4(5), pseudonymized data remains personal data when linkable back to a person.

5. **Dashboards & Alerts**: Four dashboard categories: Operational (request rate, error rate, TTFT, token throughput), Economic (cost per request, cost per team/feature), Quality (eval pass rate, hallucination rate), Safety (guardrail triggers, PII exposure events).

### OpenTelemetry GenAI Semantic Conventions

The GenAI SIG (formed April 2024) defines trace structure for AI systems. As of mid-2026, most conventions remain experimental, with every release from v1.37 to v1.41 touching GenAI attributes.

**Trace hierarchy**:
- **Agent span**: `gen_ai.agent.name`, task description, goal
- **LLM span**: `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.response.finish_reasons`
- **Tool span**: tool name, arguments (redacted), results (redacted)

Two parallel standardization efforts: AI Agent Application Conventions (individual tasks, actions, memory) and AI Agent Framework Conventions (CrewAI, AutoGen, LangGraph instrumentation).

Migration path: `OTEL_SEMCONV_STABILITY_OPT_IN` enables dual-emission of legacy and new attribute names during version transitions.

### LLM Observability vs Agent Observability

These are distinct concepts:
- **LLM observability** tracks individual model calls: prompt performance, token usage, model outputs
- **Agent observability** tracks autonomous workflows: goal achievement, tool execution patterns, multi-step reasoning chains

An agent makes multiple LLM calls across a single task. Agent observability connects these calls into a coherent narrative that reveals whether the agent achieved its goal, not just whether each individual call succeeded.

---

## Part 2: Core Mechanics & Algorithms

### The Four Pillars

1. **Traces and Spans**: Execution flow with parent-child linkage. Agent span -> LLM span -> tool span. Without parent-child linkage across service boundaries, a multi-service agent workflow appears as disconnected fragments.

2. **Metrics**: Latency (TTFT, total), token counts, cost, error rates. These are aggregated time-series data for dashboards and alerts. Users perceive latency differently: a 10-second generation streaming after 300ms TTFT feels faster than a 4-second response with no streaming.

3. **Structured Logs**: Discrete JSON events with mandatory fields: `trace_id`, `span_id`, `parent_span_id`, `timestamp`, `event_type` (llm_call | tool_call | agent_step | eval_result), `model`, `token_count`, `latency_ms`, `status`. Optional enrichment: `eval_score`, `cost_usd`, `user_id`, `session_id`.

4. **Evals**: Systematic output quality scoring. This is what makes agent observability fundamentally different from traditional APM. Without evals, you can watch your agent work but cannot tell whether it worked. Per LangChain's State of Agent Engineering: 89% of organizations have observability but quality was the top blocker to production for a third of respondents.

### Sampling Strategies

| Strategy | Mechanism | Tradeoff |
|---|---|---|
| **Head-based** | Random % at request start | Simple but drops interesting requests with mundane ones. 10% head sampling discards 90% of problems. |
| **Tail-based** | Decision at trace completion based on outcome | Always captures errors, high-latency, low-eval-score traces. Biases store toward cases worth investigating. |
| **Evaluation sampling** | Score subset of requests | 1-5% general + 100% on negative user feedback. Balances cost vs coverage. |

The 2026 production pattern: 1-10% head sampling for ordinary traffic plus 100% capture for error and high-latency traces, implemented via tail sampling at the OTel Collector.

### Cardinality Management

Prometheus warns against label overuse; keep cardinality under ~100 per metric. LLM-specific cardinality traps to avoid:
- Prompt text as labels (unbounded)
- Response text as labels (unbounded)
- Conversation IDs or request IDs as labels (unbounded)
- Tool argument blobs as span attributes (unbounded)

Safe bounded dimensions: `model`, `model_family`, `endpoint`, `region`, `status_code`, `deployment`, `tenant` (only if bounded). Unbounded labelsets destabilize the monitoring stack itself -- the observability backend becomes a cost problem.

### Agent Trajectory Replay

Reconstructing the full execution path from stored traces requires:
1. Full content capture of prompts and completions
2. Tool call arguments and results
3. Memory state at each step
4. Temporal ordering with parent-child span linkage

This enables engineers to reproduce and debug agent behavior offline. LangSmith's LangGraph Studio v2 lets teams replay production traces locally.

### The Compounding Reliability Problem

A step that is 95% reliable gives 0.95^10 = ~59% reliability across a 10-step workflow. Per-step accuracy that sounds acceptable compounds into a coin flip. No individual span looks abnormal -- the failure is emergent.

This is why circuit breakers for LLM systems must fire on composite signals, not just individual errors: sustained eval score drops, cost per session exceeding budget by 2x, token consumption rate anomalies indicating infinite loops, tool call failure rate above threshold.

### Durable Execution for Long-Running Agents

When agent workflows span minutes or hours, state must survive process restarts. Durable execution frameworks (Temporal, Inngest) checkpoint agent state at each step, enabling replay from the last successful checkpoint after failure. The observability layer must capture checkpoint metadata alongside spans to enable accurate trajectory replay.

---

## Part 3: Token Economics & NFR Analysis

### Cost Calculation

```
input_cost  = input_token_count  x  cost_per_input_token
output_cost = output_token_count x  cost_per_output_token
total_cost  = input_cost + output_cost
```

This formula is deceptive. Production agent behavior introduces compounding factors:

| Factor | Impact | Example |
|---|---|---|
| Retry amplification | 1.2x+ cost if 20% malformed outputs | Retry includes full accumulated context |
| Multi-step reasoning | 5-7x base cost for 3-iteration agent | Each iteration sends system_prompt + accumulated context + new thought |
| Framework overhead | +200-500 tokens per call | LangChain/LlamaIndex inject system prompts, format instructions invisibly |
| Evaluation costs | 2x token spend | Judge LLM on every trace effectively doubles spend |
| Reasoning tokens | Hidden output cost | o1/o3 internal reasoning billed as output but not returned |

### Precision Engineering

Datadog stores estimated cost in nanodollars rather than USD to avoid floating-point precision loss at the per-call level. When aggregating millions of calls, rounding errors in USD accumulate into real accounting gaps.

### Cost Attribution Best Practices

Tag every span with bounded dimensions: `team`, `customer_tier`, `feature`, `prompt_version`, `model_name`, `model_provider`, `environment`. Without these, you see total spend but cannot answer "which team spent the most this week."

Keep a versioned pricing config file separate from application code. Normalize model names (`gpt-4o-2024-11-20` and `gpt-4o` may map to the same pricing tier).

### SLO Design for LLM Systems

Traditional availability SLOs (HTTP 200 rate) are necessary but insufficient. LLM systems require multi-dimensional SLOs:

| SLO Dimension | SLI | Typical Target |
|---|---|---|
| **Availability** | Non-5xx response rate | 99.9% |
| **Latency (TTFT)** | Time to first token, p95 | < 500ms interactive; < 2s batch |
| **Latency (total)** | End-to-end, p95 | < 5s interactive |
| **Quality** | Eval score / task completion rate | > 85% pass rate |
| **Cost** | USD per 1k successful completions | Budget-dependent |
| **Safety** | Guardrail false positive rate | < 2% |

Key insight: Availability != HTTP 200. A system returning 200 with hallucinated content is not "available." Quality SLOs measured through automated evaluators are the LLM-specific innovation. Optimize for a quality x latency x cost frontier, not the cheapest model call.

### Dashboard Categories

| Category | Key Metrics |
|---|---|
| **Operational** | Request rate, error rate, TTFT, total latency, token throughput (tokens/sec) |
| **Economic** | Input/output token counts, cost/request, cost/session, cost by feature/team |
| **Quality** | Eval pass rate, hallucination rate, task completion rate, user feedback scores |
| **Safety** | Guardrail trigger rate, prompt injection detection rate, PII exposure events |

### Vendor Cost Comparison

| Platform | Pricing Model | Notes |
|---|---|---|
| LangSmith | $39/user/month; 5k traces free | Deepest LangChain integration |
| Arize Phoenix | Self-hosted free; AX $30K-$500K/yr | OTel-native, strongest eval rigor |
| Datadog LLM Obs | ~$8/month per 10k LLM requests | Unified APM + LLM observability |
| Langfuse | Free tier (OSS, MIT) | Self-hostable, startup-friendly |
| Honeycomb | Usage-based | OTel-native, high-cardinality queries |

Market size: $2.69B in 2026, projected $9.26B by 2030 (36.2% CAGR).

### Observability System Latency SLAs

| Metric | p50 | p95 | p99 | Mitigation |
|--------|-----|-----|-----|------------|
| Span ingestion (SDK to collector) | 1ms | 5ms | 15ms | Async export, batch flushing (5s/512 spans) |
| Trace query (dashboard) | 200ms | 800ms | 2s | Tail sampling, TTL-based retention, indexed span attributes |
| Alert evaluation | 30s | 60s | 120s | Pre-aggregated metrics, recording rules |
| Cost report generation | 5s | 15s | 30s | Materialized views, hourly aggregation |

### Observability Cost Formula

$ per 1K agent runs: span ingestion ($0.30-$1.50 depending on vendor), log storage ($0.50-$2.00 at 50KB/run), metric points ($0.10-$0.50). Total: $0.90-$4.00 per 1K runs. With tail sampling (keep 10%): $0.15-$0.60 per 1K runs.

---

## Part 4: Distributed Resilience & Security

### PII in Traces: The Hidden Risk

User inputs and tool results routinely contain names, emails, account numbers, and internal identifiers. The trace backend becomes a PII repository unless redacted before storage.

**Real-world incident**: A customer's voice agent logged complete credit card numbers in production for three weeks -- not in the transcript display (that was redacted) but in OpenTelemetry spans used for latency debugging.

### Three-Layer PII Redaction Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  LAYER 1: SDK (Pre-serialization) -- PRIMARY CONTROL           │
│  Redact at instrumentation before payload hits the wire.       │
│  This is the correct default. Post-hoc redaction is            │
│  incomplete by construction -- any consumer reading during     │
│  the unmasked window sees raw data.                            │
├────────────────────────────────────────────────────────────────┤
│  LAYER 2: OTel Collector (Redaction Processor)                 │
│  Regex-based pattern matching on attribute keys and values.    │
│  Central policy enforcement for multi-service environments.    │
│  OpenObserve: 140+ built-in PII patterns, redact/hash/drop.   │
├────────────────────────────────────────────────────────────────┤
│  LAYER 3: Backend (Last Resort)                                │
│  Post-hoc scrubbing. Raw payload exists for some period        │
│  before the scrubber catches up. Fatal weakness: time window   │
│  where raw PII is readable.                                    │
└────────────────────────────────────────────────────────────────┘
```

### Audit Trails and Compliance

Separate token counts from content:
- **Token counts and timestamps** (low-risk): retain long-term in observability backend
- **Prompt content and completions** (high-risk): separate, access-controlled store with retention limits

Under GDPR Article 4(5), pseudonymized data remains personal data when it can be linked back to a person.

### Zero-Trust Observability

Assume-breach: encrypt traces at rest and in transit, mTLS between collectors and backends, signed trace exports, network segmentation (collectors in separate VPC from backends).

### RBAC for Observability

Define roles: developer (view own service traces, no prompt content), team-lead (view team traces, cost dashboards), security-auditor (view all traces including prompt content, PII audit logs), admin (configure sampling, retention, alerts). Implement at both vendor level (LangSmith workspace roles) and collector level (OTel attribute-based routing to access-controlled backends).

### Failure Taxonomy

| Failure Mode | Severity | Detection | Root Cause |
|---|---|---|---|
| **Silent failures** | Critical | Eval scoring (not APM) | Agent loops/hallucinates but returns 200 |
| **Cost spirals** | High | Token/cost anomaly detection | Agent retry loops, context accumulation |
| **Prompt drift** | High | Rolling eval score monitoring | Micro-adjustments compound into regression |
| **Missing spans** | Medium | Span completeness checks | Retry iterations not instrumented as separate spans |
| **Cardinality explosion** | Medium | OTel backend resource monitoring | Unbounded labels (prompt text, request IDs) |
| **Sampling bias** | Medium | Coverage analysis | Head sampling drops error traces |
| **Gradual degradation** | Medium | Trend analysis (24h/7d windows) | Model drift, data distribution shift |

### Circuit Breakers for LLM Systems

Fire on composite signals:
1. Sustained eval score drops below threshold
2. Cost per session exceeding budget by 2x
3. Token consumption rate anomalies (infinite loop detection)
4. Tool call failure rate above threshold

### Incident Response Playbook

1. Identify the first time of regression
2. Correlate with deployments and configuration changes
3. Segment by model / prompt / agent / intent / language / region
4. Compare successful and failed traces
5. Determine failing stage (retrieval, model, tool, memory, policy)
6. Reproduce representative traces offline

### Adoption Reality Check

| Metric | Value |
|---|---|
| Current adoption (enterprise GenAI) | ~15% (up from ~5% one year prior) |
| Projected 2028 adoption | 50% |
| Organizations planning to enable | 85% |
| Finished rollout | 8% |
| Working on it | 36% |
| Plans but not started | 41% |

---

## Part 5: Production Enterprise Code

### Full Observability Stack: OTel Instrumentation with PII Redaction

```python
"""
Production LLM observability instrumentation with OTel GenAI semantic
conventions, PII redaction, cost tracking, tail-based sampling logic,
and circuit breakers. Requires: opentelemetry-api, opentelemetry-sdk, structlog.
"""

import re
import time
import hashlib
from dataclasses import dataclass, field
from typing import Optional
from collections import deque
from datetime import datetime, timezone

import structlog

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# PII Redaction (Layer 1: SDK-level, pre-serialization)
# ---------------------------------------------------------------------------

class PIIRedactor:
    """Redacts PII before telemetry leaves the application.

    This is the primary control. Post-hoc redaction at the collector
    or backend is incomplete -- any consumer reading during the
    unmasked window sees raw data.
    """

    PATTERNS = {
        "email": re.compile(
            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
        ),
        "phone_us": re.compile(
            r"\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
        ),
        "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "credit_card": re.compile(
            r"\b(?:\d{4}[-\s]?){3}\d{4}\b"
        ),
        "ip_address": re.compile(
            r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
        ),
        "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
        "pan_india": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    }

    def __init__(self, additional_patterns: Optional[dict] = None):
        if additional_patterns:
            self.PATTERNS.update(additional_patterns)
        self.redaction_counts: dict[str, int] = {}

    def redact(self, text: str) -> str:
        """Replace all PII patterns with category markers."""
        result = text
        for category, pattern in self.PATTERNS.items():
            matches = pattern.findall(result)
            if matches:
                self.redaction_counts[category] = (
                    self.redaction_counts.get(category, 0) + len(matches)
                )
                result = pattern.sub(f"[REDACTED_{category.upper()}]", result)
        return result

    def redact_dict(self, data: dict) -> dict:
        """Recursively redact PII from dict values."""
        redacted = {}
        for key, value in data.items():
            if isinstance(value, str):
                redacted[key] = self.redact(value)
            elif isinstance(value, dict):
                redacted[key] = self.redact_dict(value)
            elif isinstance(value, list):
                redacted[key] = [
                    self.redact(v) if isinstance(v, str) else v for v in value
                ]
            else:
                redacted[key] = value
        return redacted

    def get_audit_record(self) -> dict:
        """Return redaction counts for audit logging (no raw content)."""
        return {
            "redaction_counts": dict(self.redaction_counts),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# Cost Tracker (nanodollar precision)
# ---------------------------------------------------------------------------

@dataclass
class ModelPricing:
    """Pricing per million tokens."""
    input_per_mtok: float
    output_per_mtok: float


# Versioned pricing config -- keep separate from application code
MODEL_PRICING: dict[str, ModelPricing] = {
    "claude-opus-4-20250514": ModelPricing(15.0, 75.0),
    "claude-sonnet-4-5-20250514": ModelPricing(3.0, 15.0),
    "claude-haiku-4-5-20250514": ModelPricing(0.80, 4.0),
    "gpt-4o": ModelPricing(2.50, 10.0),
    "gpt-4o-mini": ModelPricing(0.15, 0.60),
}

# Model name normalization map
MODEL_ALIASES: dict[str, str] = {
    "gpt-4o-2024-11-20": "gpt-4o",
    "gpt-4o-2025-03-01": "gpt-4o",
    "claude-3-5-sonnet-20241022": "claude-sonnet-4-5-20250514",
}


class CostTracker:
    """Tracks LLM costs in nanodollars for precision at scale."""

    def __init__(self):
        self.total_nanodollars: int = 0
        self.by_dimension: dict[str, int] = {}

    def _resolve_model(self, model: str) -> str:
        return MODEL_ALIASES.get(model, model)

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        dimensions: Optional[dict] = None,
    ) -> dict:
        """Record cost for a single LLM call. Returns cost breakdown."""
        resolved = self._resolve_model(model)
        pricing = MODEL_PRICING.get(resolved)
        if not pricing:
            logger.warning("unknown_model_pricing", model=model, resolved=resolved)
            return {"cost_nanodollars": 0, "model": resolved, "warning": "unknown pricing"}

        input_nanodollars = int(input_tokens * pricing.input_per_mtok * 1000 / 1_000_000)
        output_nanodollars = int(output_tokens * pricing.output_per_mtok * 1000 / 1_000_000)
        total_nano = input_nanodollars + output_nanodollars

        self.total_nanodollars += total_nano

        # Attribution by dimension
        if dimensions:
            dim_key = "|".join(f"{k}={v}" for k, v in sorted(dimensions.items()))
            self.by_dimension[dim_key] = self.by_dimension.get(dim_key, 0) + total_nano

        return {
            "model": resolved,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_nanodollars": input_nanodollars,
            "output_nanodollars": output_nanodollars,
            "total_nanodollars": total_nano,
            "total_usd": total_nano / 1_000_000_000,
        }

    def get_total_usd(self) -> float:
        return self.total_nanodollars / 1_000_000_000

    def get_attribution_report(self) -> list[dict]:
        """Cost breakdown by dimension for team/feature attribution."""
        report = []
        for dim_key, nano in sorted(self.by_dimension.items(), key=lambda x: -x[1]):
            report.append({
                "dimensions": dim_key,
                "nanodollars": nano,
                "usd": nano / 1_000_000_000,
            })
        return report


# ---------------------------------------------------------------------------
# Span Builder (OTel GenAI Semantic Conventions)
# ---------------------------------------------------------------------------

@dataclass
class LLMSpan:
    """Represents an LLM call span following OTel GenAI conventions."""
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    timestamp: str
    event_type: str  # llm_call | tool_call | agent_step | eval_result
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    ttft_ms: float = 0.0
    status: str = "ok"
    finish_reason: str = ""
    cost_nanodollars: int = 0
    eval_score: Optional[float] = None
    team: str = ""
    feature: str = ""
    prompt_version: str = ""
    retry_count: int = 0
    content_hash: str = ""  # Hash of prompt content (not content itself)


class SpanBuilder:
    """Builds OTel-compatible spans with PII redaction and cost tracking."""

    def __init__(self, redactor: PIIRedactor, cost_tracker: CostTracker):
        self.redactor = redactor
        self.cost_tracker = cost_tracker
        self.spans: list[LLMSpan] = []

    def _generate_id(self) -> str:
        return hashlib.sha256(
            f"{time.time_ns()}".encode()
        ).hexdigest()[:16]

    def start_agent_span(
        self, trace_id: str, agent_name: str, task: str
    ) -> LLMSpan:
        span = LLMSpan(
            trace_id=trace_id,
            span_id=self._generate_id(),
            parent_span_id=None,
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type="agent_step",
            model="",
        )
        self.spans.append(span)
        logger.info(
            "agent_span_started",
            trace_id=trace_id,
            span_id=span.span_id,
            agent_name=agent_name,
            task=self.redactor.redact(task),
        )
        return span

    def record_llm_call(
        self,
        trace_id: str,
        parent_span_id: str,
        model: str,
        input_text: str,
        output_text: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        ttft_ms: float,
        finish_reason: str,
        retry_count: int = 0,
        dimensions: Optional[dict] = None,
    ) -> LLMSpan:
        """Record an LLM call with redaction and cost tracking."""
        # Redact content, hash for correlation (not storage of raw text)
        redacted_input = self.redactor.redact(input_text)
        content_hash = hashlib.sha256(input_text.encode()).hexdigest()[:12]

        cost = self.cost_tracker.record(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            dimensions=dimensions,
        )

        span = LLMSpan(
            trace_id=trace_id,
            span_id=self._generate_id(),
            parent_span_id=parent_span_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type="llm_call",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            status="ok",
            finish_reason=finish_reason,
            cost_nanodollars=cost["total_nanodollars"],
            retry_count=retry_count,
            content_hash=content_hash,
        )
        if dimensions:
            span.team = dimensions.get("team", "")
            span.feature = dimensions.get("feature", "")
            span.prompt_version = dimensions.get("prompt_version", "")

        self.spans.append(span)
        logger.info("llm_call_recorded", trace_id=trace_id, span_id=span.span_id,
                     model=model, tokens=input_tokens+output_tokens,
                     latency_ms=latency_ms, cost_usd=cost["total_usd"])
        return span

    def record_tool_call(self, trace_id: str, parent_span_id: str,
                         tool_name: str, tool_args: dict, tool_result: str,
                         latency_ms: float, status: str = "ok") -> LLMSpan:
        """Record a tool call with redacted arguments and results."""
        redacted_args = self.redactor.redact_dict(tool_args)
        span = LLMSpan(
            trace_id=trace_id, span_id=self._generate_id(),
            parent_span_id=parent_span_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type="tool_call", model="",
            latency_ms=latency_ms, status=status,
        )
        self.spans.append(span)
        logger.info("tool_call_recorded", trace_id=trace_id,
                     tool_name=tool_name, latency_ms=latency_ms, status=status)
        return span


# ---------------------------------------------------------------------------
# Tail-Based Sampling Decision Engine
# ---------------------------------------------------------------------------

class TailSampler:
    """Tail-based sampling: 100% errors/high-latency/low-eval, base_rate% routine."""

    def __init__(self, base_rate: float = 0.05, latency_threshold_ms: float = 10_000,
                 min_eval_score: float = 0.7):
        self.base_rate = base_rate
        self.latency_threshold_ms = latency_threshold_ms
        self.min_eval_score = min_eval_score
        self.retained = 0
        self.dropped = 0

    def should_retain(self, span: LLMSpan) -> bool:
        """Decide after trace completion based on outcome."""
        # Always retain: errors, high-latency, low-eval, retries
        always_retain = (
            span.status != "ok"
            or span.latency_ms > self.latency_threshold_ms
            or (span.eval_score is not None and span.eval_score < self.min_eval_score)
            or span.retry_count > 0
        )
        import random
        if always_retain or random.random() < self.base_rate:
            self.retained += 1
            return True
        self.dropped += 1
        return False


# ---------------------------------------------------------------------------
# Quality Drift Detector
# ---------------------------------------------------------------------------

class DriftDetector:
    """Monitors rolling eval scores for sustained quality degradation."""

    def __init__(self, alert_threshold: float = 0.80, sustained_minutes: int = 30):
        self.scores_24h: deque = deque(maxlen=1440)
        self.scores_7d: deque = deque(maxlen=10080)
        self.alert_threshold = alert_threshold
        self.sustained_minutes = sustained_minutes
        self.minutes_below = 0
        self.alert_active = False

    def record(self, score: float):
        entry = {"score": score, "ts": datetime.now(timezone.utc).isoformat()}
        self.scores_24h.append(entry)
        self.scores_7d.append(entry)
        if len(self.scores_24h) < 30:
            return
        avg = sum(e["score"] for e in list(self.scores_24h)[-60:]) / min(60, len(self.scores_24h))
        if avg < self.alert_threshold:
            self.minutes_below += 1
            if self.minutes_below >= self.sustained_minutes and not self.alert_active:
                self.alert_active = True
                logger.error("quality_drift_alert", avg_score=round(avg, 4),
                             sustained_minutes=self.minutes_below)
        else:
            self.minutes_below = 0
            if self.alert_active:
                self.alert_active = False
                logger.info("quality_drift_resolved", avg_score=round(avg, 4))


# ---------------------------------------------------------------------------
# Cost Circuit Breaker
# ---------------------------------------------------------------------------

class CostCircuitBreaker:
    """Trips when a session exceeds cost budget, preventing runaway agents.

    Inspired by the $4,200 weekend incident: an agent consumed $4,200 over
    a weekend due to an unexpected interaction loop.
    """

    def __init__(
        self,
        session_budget_usd: float = 5.0,
        hourly_budget_usd: float = 50.0,
    ):
        self.session_budget_nano = int(session_budget_usd * 1_000_000_000)
        self.hourly_budget_nano = int(hourly_budget_usd * 1_000_000_000)
        self.session_spend: dict[str, int] = {}
        self.hourly_spend: deque = deque()
        self.tripped_sessions: set = set()

    def check_and_record(
        self, session_id: str, cost_nanodollars: int
    ) -> dict:
        """Returns action: 'allow', 'warn', or 'block'."""
        now = time.time()

        # Track session spend
        self.session_spend[session_id] = (
            self.session_spend.get(session_id, 0) + cost_nanodollars
        )

        # Track hourly spend (sliding window)
        self.hourly_spend.append({"ts": now, "cost": cost_nanodollars})
        # Evict entries older than 1 hour
        cutoff = now - 3600
        while self.hourly_spend and self.hourly_spend[0]["ts"] < cutoff:
            self.hourly_spend.popleft()

        hourly_total = sum(e["cost"] for e in self.hourly_spend)
        session_total = self.session_spend[session_id]

        # Session budget exceeded
        if session_total > self.session_budget_nano:
            self.tripped_sessions.add(session_id)
            logger.error(
                "cost_circuit_breaker_tripped",
                session_id=session_id,
                session_spend_usd=session_total / 1_000_000_000,
                budget_usd=self.session_budget_nano / 1_000_000_000,
                reason="session_budget_exceeded",
            )
            return {"action": "block", "reason": "session_budget_exceeded"}

        # Hourly budget exceeded
        if hourly_total > self.hourly_budget_nano:
            logger.error(
                "cost_circuit_breaker_tripped",
                hourly_spend_usd=hourly_total / 1_000_000_000,
                budget_usd=self.hourly_budget_nano / 1_000_000_000,
                reason="hourly_budget_exceeded",
            )
            return {"action": "block", "reason": "hourly_budget_exceeded"}

        # Warning at 80% of budget
        if session_total > self.session_budget_nano * 0.8:
            return {"action": "warn", "reason": "session_approaching_budget"}

        return {"action": "allow", "reason": "within_budget"}
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: Instrumenting a Multi-Agent Customer Support Platform

**Problem Statement**: A B2B SaaS company runs a customer support platform with three specialized agents: a triage agent (classifies and routes tickets), a billing agent (queries accounts, applies credits), and a technical agent (searches knowledge base, creates Jira tickets). Each agent makes 2-5 LLM calls per task. The platform handles 10,000 tickets/day. After a model upgrade, resolution rates dropped 12% but APM dashboards showed all green -- latency improved, error rates unchanged. The company has no LLM-specific observability.

**Architecture**:

```
┌──────────────────────────────────────────────────────────────────────┐
│                     MULTI-AGENT OBSERVABILITY STACK                   │
│                                                                       │
│  ┌───────────┐   ┌───────────┐   ┌───────────┐                      │
│  │  Triage   │   │  Billing  │   │ Technical │                      │
│  │  Agent    │   │  Agent    │   │  Agent    │                      │
│  └─────┬─────┘   └─────┬─────┘   └─────┬─────┘                      │
│        │               │               │                             │
│        └───────────────┼───────────────┘                             │
│                        v                                             │
│  ┌───────────────────────────────────────────────────────────┐       │
│  │  INSTRUMENTATION LAYER (OTel SDK + PII Redaction)         │       │
│  │                                                           │       │
│  │  Agent span (parent) ──> LLM spans ──> Tool spans         │       │
│  │  Cross-agent: parent_span_id links triage -> billing      │       │
│  │  PII redact at SDK before wire serialization              │       │
│  └───────────────────────────┬───────────────────────────────┘       │
│                              v                                       │
│  ┌───────────────────────────────────────────────────────────┐       │
│  │  OTel COLLECTOR                                           │       │
│  │  Tail sampling: 100% errors + low-eval, 5% routine        │       │
│  │  Cardinality bounding: model, agent_type, team, status    │       │
│  │  Attribute transform: normalize model names               │       │
│  └──────────┬────────────────┬───────────────────────────────┘       │
│             v                v                                       │
│  ┌──────────────────┐  ┌──────────────────┐                         │
│  │  Langfuse (traces │  │  Prometheus      │                         │
│  │  + eval scores +  │  │  (metrics: cost, │                         │
│  │  trajectory replay│  │   latency, token │                         │
│  │  + content store) │  │   throughput)    │                         │
│  └────────┬─────────┘  └────────┬─────────┘                         │
│           v                     v                                    │
│  ┌──────────────────────────────────────────────────────────┐        │
│  │  GRAFANA DASHBOARDS                                      │        │
│  │  Operational | Economic | Quality | Safety               │        │
│  │                                                          │        │
│  │  Alert: sustained eval score < 0.80 over 30 min          │        │
│  │  Alert: session cost > $5 (cost circuit breaker)         │        │
│  │  Alert: agent routing accuracy drop > 5%                 │        │
│  └──────────────────────────────────────────────────────────┘        │
└──────────────────────────────────────────────────────────────────────┘
```

**Trade-off Matrix**:

| Decision | Option A | Option B | Choice | Rationale |
|---|---|---|---|---|
| Platform | Datadog LLM Obs (existing Datadog) | Langfuse (OSS, self-hosted) | Langfuse | Company is cost-sensitive; Langfuse is free self-hosted with MIT license, provides trajectory replay |
| Sampling | 100% of all traces | Tail-based (5% routine, 100% errors) | Tail-based | 10k tickets/day at 3-5 LLM calls each = 30-50k spans/day. Full capture increases storage cost 20x |
| Content storage | Same backend as metrics | Separate access-controlled store | Separate | PII compliance requires different retention and access policies for content vs telemetry |
| Eval approach | Score every response | 5% sample + 100% negative feedback | 5% + feedback | Judge cost at 100% would exceed production LLM cost |

**Decision Rationale**: The 12% resolution rate drop was invisible to APM because the failure was semantic, not structural. With LLM observability: quality SLOs (eval pass rate) would have caught the regression within hours. Tail-based sampling ensures the interesting traces (errors, low scores, high cost) are always retained. Cross-agent parent-child span linkage is critical -- without it, a triage agent misrouting a ticket appears as a billing agent failure. Cost circuit breakers prevent runaway agent loops (the $4,200 weekend pattern).

---

### Scenario 2: Migrating from APM-Only to Full LLM Observability

**Problem Statement**: An enterprise with 200+ AI engineers runs 15 LLM-powered features across 4 product lines. They use Datadog for traditional APM. Monthly LLM spend is $180K but cannot be attributed to teams or features. Prompt drift has caused 3 production incidents in 6 months, each discovered by customers, not monitoring. Leadership wants full LLM observability within 2 quarters without disrupting existing APM.

**Architecture (Phased Rollout)**:

```
┌──────────────────────────────────────────────────────────────────────┐
│  PHASE 1 (Q1): Foundation                                            │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────┐         │
│  │  Existing Datadog APM (untouched)                       │         │
│  └─────────────────────────────────────────────────────────┘         │
│                              +                                       │
│  ┌─────────────────────────────────────────────────────────┐         │
│  │  NEW: OTel SDK instrumentation for all LLM calls        │         │
│  │  - gen_ai.request.model, input/output tokens            │         │
│  │  - Cost tracking with team/feature attribution          │         │
│  │  - PII redaction at SDK layer                           │         │
│  │  - OTEL_SEMCONV_STABILITY_OPT_IN for dual emission      │         │
│  └────────────────────────┬────────────────────────────────┘         │
│                           v                                          │
│  ┌─────────────────────────────────────────────────────────┐         │
│  │  NEW: Datadog LLM Observability module                  │         │
│  │  (~$8/month per 10k LLM requests)                       │         │
│  │  Economic dashboards: cost by team, feature, model      │         │
│  └─────────────────────────────────────────────────────────┘         │
├──────────────────────────────────────────────────────────────────────┤
│  PHASE 2 (Q2): Quality Layer                                         │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────┐         │
│  │  NEW: Eval pipeline (5% production traffic sampling)    │         │
│  │  - Automated quality scoring (LLM-as-judge)             │         │
│  │  - Quality SLOs integrated into existing SLO framework  │         │
│  │  - Drift detection with 24h/7d rolling windows          │         │
│  │  - Alert on sustained score drops -> PagerDuty          │         │
│  └─────────────────────────────────────────────────────────┘         │
│                              +                                       │
│  ┌─────────────────────────────────────────────────────────┐         │
│  │  NEW: Tail-based sampling at OTel Collector              │         │
│  │  - 100% errors + high-latency + low-eval                │         │
│  │  - 5% routine successes                                 │         │
│  │  - Cardinality bounding enforced                        │         │
│  └─────────────────────────────────────────────────────────┘         │
└──────────────────────────────────────────────────────────────────────┘
```

**Trade-off Matrix**:

| Decision | Option A | Option B | Choice | Rationale |
|---|---|---|---|---|
| Platform | Replace Datadog with Langfuse | Add Datadog LLM module to existing | Add Datadog LLM module | 200+ engineers already use Datadog. Migration disrupts. LLM module extends existing investment. |
| Rollout | Big-bang (instrument everything Q1) | Phased (cost Q1, quality Q2) | Phased | Big-bang risks instrumentation bugs across 15 features simultaneously |
| Eval engine | Build custom | Use Braintrust/DeepEval | Braintrust for experiments + custom scorers | Custom scorers for domain-specific metrics; Braintrust for experiment tracking infrastructure |
| Cost attribution | Post-hoc from logs | Real-time span tagging | Real-time span tagging | Post-hoc requires log parsing, is delayed, and misses untagged calls |

**Decision Rationale**: Phase 1 delivers immediate value (cost attribution) without touching the quality signal chain. The $180K/month unattributed spend becomes attributable within weeks, creating executive buy-in for Phase 2. Phase 2 adds the quality layer that would have caught the 3 prompt-drift incidents. Using Datadog's LLM module avoids the organizational cost of introducing a new observability platform to 200+ engineers. The `OTEL_SEMCONV_STABILITY_OPT_IN` flag enables dual-emission during the transition, preventing breakage as GenAI semantic conventions stabilize.

