# Module 14: Observability — Tracing, Metrics, Cost Attribution, and Production Monitoring for AI Systems

**Scope**: OpenTelemetry GenAI semantic conventions (traces, spans, metrics), observability platforms (Langfuse, Arize Phoenix, LangSmith, Datadog, Braintrust, MLflow 3), agent observability (multi-step traces, tool call tracking, trajectory analysis), RAG observability (retrieval quality, embedding drift), cost observability (per-request attribution, budget alerting), production monitoring (three-layer stack, SLOs for AI, incident response), and the observability-evaluation feedback loop.
**Prerequisite**: Module 12 (Evaluation), Module 13 (Security & Guardrails).
**Last updated**: 2026-08-21 | **Sources consulted**: 90

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                    CONTROL PLANE                                           │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
 │  │  SLO Manager     │  │  Alert Engine    │  │  Cost Budget     │  │  Incident        │  │
 │  │  - 6 agent SLOs  │  │  - Quality drop  │  │  Controller      │  │  Commander       │  │
 │  │  - Availability  │  │  - Cost spike    │  │  - Per-user cap  │  │  - 4 incident    │  │
 │  │  - Latency       │  │  - TTFT breach   │  │  - Per-feature   │  │    classes       │  │
 │  │  - Quality       │  │  - Drift detect  │  │  - 80% threshold │  │  - 6-step        │  │
 │  │  - Goodput       │  │  - Loop detect   │  │  - Hard enforce  │  │    runbook       │  │
 │  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
 └───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┘
             │                    │                    │                    │
 ┌───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┐
 │           ▼                    ▼                    ▼                    ▼                  │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │              DATA PLANE: THREE-LAYER OBSERVABILITY STACK                           │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  LAYER 1: INFRASTRUCTURE METRICS                                        │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │      │    │
 │  │  │  │ GPU/CPU/Mem  │  │ vLLM/TGI     │  │ Network /    │                  │      │    │
 │  │  │  │ Utilization  │  │ Metrics      │  │ Queue Depth  │                  │      │    │
 │  │  │  │ - KV cache   │  │ - TTFT hist. │  │ - Request    │                  │      │    │
 │  │  │  │ - Batch size │  │ - TPOT hist. │  │   queuing    │                  │      │    │
 │  │  │  │ - Saturation │  │ - E2E lat.   │  │ - Backpressure│                 │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘                  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  LAYER 2: LLM TELEMETRY (OpenTelemetry GenAI Conventions)               │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Traces       │  │ Metrics      │  │ Events/Logs  │  │ Cost       │  │      │    │
 │  │  │  │ - invoke_    │  │ - operation. │  │ - Prompt     │  │ Attribution│  │      │    │
 │  │  │  │   agent      │  │   duration   │  │   content    │  │ - Per-user │  │      │    │
 │  │  │  │ - chat       │  │ - token.     │  │ - Completion │  │ - Per-feat │  │      │    │
 │  │  │  │ - execute_   │  │   usage      │  │ - Tool args  │  │ - Per-model│  │      │    │
 │  │  │  │   tool       │  │ - time_per_  │  │ (opt-in,     │  │ - Per-run  │  │      │    │
 │  │  │  │ - invoke_    │  │   output_    │  │  PII-safe)   │  │            │  │      │    │
 │  │  │  │   workflow   │  │   token      │  │              │  │            │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  LAYER 3: QUALITY / PRODUCT METRICS                                     │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Online Evals │  │ RAG Quality  │  │ User Feedback│  │ Annotation │  │      │    │
 │  │  │  │ - LLM-judge  │  │ - Precision@K│  │ - Thumbs up/ │  │ Queues     │  │      │    │
 │  │  │  │   on sampled │  │ - Faithfuln. │  │   down       │  │ - Human    │  │      │    │
 │  │  │  │   traffic    │  │ - Embedding  │  │ - Ratings    │  │   review   │  │      │    │
 │  │  │  │ - Same rubric│  │   drift det. │  │ - Session    │  │ - Low-score│  │      │    │
 │  │  │  │   as CI gate │  │ - Chunk rel. │  │   abandon    │  │   triage   │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                         TOOL PROXY LAYER                                           │    │
 │  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐       │    │
 │  │  │ AI Gateway    │  │ OTel Collector│  │ Content       │  │ Eval Runner   │       │    │
 │  │  │ (LiteLLM/     │  │ - GenAI attrs │  │ Redaction     │  │ - Sampled     │       │    │
 │  │  │  Portkey)     │  │ - Span export │  │ Processor     │  │   online eval │       │    │
 │  │  │ - Cost track  │  │ - Metric agg  │  │ - PII strip   │  │ - Score ingest│       │    │
 │  │  │ - Rate limit  │  │ - Log filter  │  │ - Opt-in      │  │ - Threshold   │       │    │
 │  │  │ - Routing     │  │              │  │   content      │  │   alerting    │       │    │
 │  │  └───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘       │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  PERSISTENCE LAYER                                         │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Trace Store       │  │ Metrics Store     │  │ Eval Results      │  │ Cost Ledger    │  │
 │  │ (Tempo/ClickHouse)│  │ (Mimir/Prometheus)│  │ (Langfuse/Braintr)│  │ - Per-request  │  │
 │  │ - Span trees      │  │ - TTFT, TPOT hist │  │ - Quality scores  │  │ - Per-user     │  │
 │  │ - Tool call data  │  │ - Token counters  │  │ - Judge traces    │  │ - Per-feature  │  │
 │  │ - Agent traject.  │  │ - Goodput series  │  │ - Human labels    │  │ - Budget state │  │
 │  │ - Content (opt-in)│  │ - GPU utilization │  │ - Drift baselines │  │ - Model tier   │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  TELEMETRY PLANE                                           │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Operational       │  │ Quality           │  │ Cost & Token      │  │ Agent Health   │  │
 │  │ Dashboards        │  │ Dashboards        │  │ Dashboards        │  │ Dashboards     │  │
 │  │ - TTFT/TPOT/E2E  │  │ - Faithfulness    │  │ - $/request       │  │ - Task compl.  │  │
 │  │ - Error rates     │  │ - Relevance       │  │ - $/user          │  │ - Tool success │  │
 │  │ - Queue depth     │  │ - RAG precision   │  │ - $/feature       │  │ - Loop detect  │  │
 │  │ - GPU sat.        │  │ - Drift magnitude │  │ - Budget burn     │  │ - Trajectory   │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

**Step 1 — Gateway Instrumentation**: Every request enters through the **AI Gateway** (LiteLLM, Portkey), which logs token usage, cost, and routing decisions. The gateway adds `gen_ai.request.model`, `customer_id`, and `feature_name` attributes to every span.

**Step 2 — OTel Trace Creation**: The **OTel Collector** receives GenAI-attributed spans: `invoke_agent` (root), `chat` (LLM calls), `execute_tool` (tool invocations), `invoke_workflow` (multi-agent coordination). Each span carries `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.response.finish_reasons`, and timing data.

**Step 3 — Content Redaction**: Before traces reach persistent storage, the **Content Redaction Processor** strips PII from opt-in content capture (prompts, completions, tool arguments). Content attributes are opt-in by default per OTel GenAI conventions.

**Step 4 — Three-Layer Monitoring**: Traces flow to **Layer 2** (LLM telemetry: Langfuse, LangSmith, Phoenix). Infrastructure metrics flow to **Layer 1** (Prometheus/Grafana: GPU, KV cache, queue depth). Quality scores flow to **Layer 3** (online evals on sampled traffic, user feedback, annotation queues).

**Step 5 — SLO Evaluation**: The **SLO Manager** evaluates six independent agent SLOs: task completion rate, tool-call success rate, recovery rate, p99 latency, guardrail trip rate, and trajectory score. Goodput (requests meeting ALL SLOs) is the composite operational metric.

**Step 6 — Alert & Response**: The **Alert Engine** fires on SLO breaches, cost spikes, quality regressions, and drift detection. The **Incident Commander** classifies incidents into 4 classes (hallucination, jailbreak, drift, PII leak) and executes the 6-step runbook. Failed production traces feed back into evaluation datasets.

---

## 2. Core Mechanics & Algorithms

### 2.1 OpenTelemetry GenAI Semantic Conventions

As of June 2026 (v1.42.0), all `gen_ai.*` attributes moved to a dedicated repository (`open-telemetry/semantic-conventions-genai`) with its own release cadence. Conventions remain pre-stable but core concepts have settled.

**Three signal types**:

| Signal | Key Conventions | Purpose |
|--------|----------------|---------|
| **Traces (Spans)** | `invoke_agent`, `chat`, `execute_tool`, `invoke_workflow` | Capture full agent execution as span tree |
| **Metrics** | `gen_ai.client.operation.duration`, `gen_ai.client.token.usage`, `gen_ai.client.time_per_output_token` | Pre-aggregated latency, token, and cost histograms |
| **Events/Logs** | `gen_ai.system_instructions`, `gen_ai.input.messages`, `gen_ai.output.messages` | Content capture (opt-in for PII safety) |

**Core span attributes**:

| Attribute | Description | Requirement |
|-----------|-------------|:-----------:|
| `gen_ai.operation.name` | `chat`, `text_completion`, `generate_content` | Required |
| `gen_ai.provider.name` | Provider identifier | Required |
| `gen_ai.request.model` | Model requested (e.g., `claude-sonnet-4`) | Required |
| `gen_ai.response.model` | Model that generated response | Recommended |
| `gen_ai.usage.input_tokens` | Input token count | Recommended |
| `gen_ai.usage.output_tokens` | Output token count | Recommended |
| `gen_ai.response.finish_reasons` | `stop`, `tool_calls`, `length` | Recommended |

**Token usage rule**: When systems report both *used* and *billable* tokens, instrumentation MUST report billable tokens.

**Adoption**: Datadog, Honeycomb, New Relic, Google Cloud, AWS, Azure support these conventions. ~65% of enterprises transitioning to OTel as of 2025.

### 2.2 LLM Inference Metrics

| Metric | Definition | Target | Critical Insight |
|--------|-----------|:------:|-----------------|
| **TTFT** (Time to First Token) | Time from query to first token | p95 < 500ms | Never use averages (200ms avg can have 3,000ms p99) |
| **TPOT** (Time Per Output Token) | `(E2E - TTFT) / (output_tokens - 1)` | ≤30ms (MLCommons) | >250ms makes streaming feel broken |
| **ITL** (Inter-Token Latency) | Time between consecutive tokens | Steady (low jitter) | A 200ms pause mid-response is noticed more than steady 20ms |
| **E2E Latency** | Full request-to-response time | p95 < 5s (interactive) | Composition of queue + prefill + decode + network |
| **Throughput** | Total output tokens/second across requests | Workload-dependent | Larger batches improve aggregate but hurt per-user latency |
| **Goodput** | Requests/second meeting ALL latency SLOs | 100% of throughput | 500 RPS with 30% exceeding TTFT SLO = 350 goodput |

### 2.3 Observability Platform Comparison

| Platform | License | Self-Host | OTel | Free Tier | Pricing | Best For |
|----------|---------|:---------:|:----:|-----------|---------|---------|
| **Langfuse** | MIT | Yes | Yes | 50K units/mo | $29–$2,499/mo | Framework-agnostic; data residency |
| **LangSmith** | Proprietary | No | Yes | 5K traces/mo | $39/seat/mo | LangChain/LangGraph teams |
| **Arize Phoenix** | ELv2 | Yes (free) | Native | Unlimited self-host | AX Pro: $50/mo | Eval rigor; drift detection |
| **Datadog** | Proprietary | No | Yes | 40K spans/mo | $160/mo | Unified AI + infra APM |
| **Braintrust** | Proprietary | No | Yes | 1GB data | $249/mo | Eval-first observability |
| **MLflow 3** | Apache 2.0 | Yes | Native | Unlimited | Free | Full-featured OSS |
| **Opik** | Apache 2.0 | Yes | Yes | Free self-host | Free | Prompt optimization; coding agent cost |
| **Portkey** | Open source | Yes | Yes | Free tier | Gateway pricing | Multi-provider routing |

**Market**: $2.69B in 2026, projected $9.26B by 2030 (36.2% CAGR). $1.1B VC deployed across observability startups (2024–2026). Gartner: 50% of GenAI deployments will have observability by 2028, up from 15% in 2026.

### 2.4 Agent-Specific Observability

**Why agents fail silently**: A model can return a syntactically valid, plausible-sounding response that is factually wrong — no error is thrown, no latency spike occurs, all dashboards show green. LLM hallucinations cost businesses $67.4B in losses in 2024.

**Silent failure modes**:
- Infinite loops (same tool called repeatedly with no progress)
- Context abandonment (agent forgets original goal mid-task)
- Hallucinated tool arguments (invents parameters that don't exist)
- Wrong tool selection (valid tool, wrong context)
- Silent retry loops (retries blend into normal traffic)

**Four pillars of agent telemetry**:
1. **Multi-step trace visualization**: Full span tree with timing, tokens, and cost at each step
2. **Tool call tracking**: Name, arguments, raw output, duration, retry count, error state
3. **Decision graph capture**: Which subagents, handoffs, loop iterations ran
4. **Failure localization**: Pinpoint which step caused failure

### 2.5 RAG Observability Metrics

| Stage | Metric | What It Measures |
|-------|--------|-----------------|
| **Retrieval** | Precision@K | Fraction of retrieved chunks that are relevant |
| **Retrieval** | Recall@K | Fraction of relevant chunks that were retrieved |
| **Retrieval** | MRR | Position of first relevant result |
| **Retrieval** | Context Relevance | LLM-judge score of chunk relevance |
| **Generation** | Faithfulness | Claims supported by retrieved documents (alert below 0.7) |
| **Generation** | Hallucination Rate | % of claims not grounded in context |
| **Generation** | Citation Correctness | Citations point to supporting passages |
| **Drift** | Embedding Centroid Distance | Cosine distance between rolling and baseline centroids (alert >0.05–0.10) |

### 2.6 Six SLOs for AI Agents

| SLO | What It Measures | Why Single Score Hides It |
|-----|-----------------|--------------------------|
| **Task Completion Rate** | Fraction of tasks completed | Composite 0.85 doesn't tell completion vs. quality |
| **Tool-Call Success Rate** | Fraction of tool calls succeeding | 0.97 tool success masks 0.62 argument extraction |
| **Recovery Rate** | Ability to recover from mid-task errors | Hidden by averaging with normal runs |
| **P99 Latency** | Worst 1% tail latency | Averages hide the long tail |
| **Guardrail Trip Rate** | How often safety guardrails fire | Must track independently from quality |
| **Trajectory Score (4-D)** | Trace-grounded quality across 4 dimensions | Needs trace-level evaluation |

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Model: Observability Infrastructure

| Component | Cost | Notes |
|-----------|:----:|-------|
| **Langfuse Cloud (Core)** | $29/mo + $8/100K units | 1 multi-step trace ≈ 20+ units |
| **Langfuse Cloud (Pro)** | $199/mo | 3-year retention |
| **LangSmith Plus** | $39/seat/mo + $2.50/1K traces | Per-seat adds up at scale |
| **Datadog LLM Obs (Pro)** | $160/mo + $8/10K spans | Only LLM spans billed; tool/retrieval spans free |
| **Braintrust Pro** | $249/mo + $3/GB + $1.50/1K scores | Eval-first pricing |
| **Phoenix/MLflow (self-hosted)** | $0 (software) + infra | Infra: ~$500–2K/mo at scale |
| **Grafana + Prometheus (self-hosted)** | $0 (software) + infra | Standard SRE stack |
| **AI Gateway (LiteLLM/Portkey)** | $0–$200/mo | Self-host or cloud |

**Total observability cost at scale** (1M requests/month):

| Configuration | Monthly Cost | Notes |
|--------------|:-----------:|-------|
| Minimal (MLflow + Prometheus) | ~$1K | Self-hosted; engineer time not included |
| Standard (Langfuse Pro + Grafana) | ~$2K | Good coverage; self-host option |
| Enterprise (Datadog + Braintrust) | ~$5K+ | Full-featured; managed |
| Premium (Datadog + custom + human review) | ~$15K+ | Large teams with compliance needs |

**Industry context**: Enterprise monthly AI spend averaged $85,521 in 2025; only 34% had mature cost management. 60% of AI projects exceeded cost estimates by 30–50%. Allocate 5–10% of AI spend for observability.

### 3.2 Latency SLA Targets

| Component | p50 | p95 | p99 | Mitigation |
|-----------|-----|-----|-----|------------|
| Gateway overhead (Portkey) | 20ms | 40ms | 60ms | Edge deployment; connection pooling |
| OTel trace export (async) | ~0ms | ~0ms | ~0ms | Async export; non-blocking |
| Langfuse step-level tracing | ~15% overhead | — | — | Async instrumentation; batch export |
| Content redaction processor | 2ms | 5ms | 10ms | Pre-compiled regex; PII model cached |
| Online eval (LLM judge, sampled) | N/A | N/A | N/A | Async; not on hot path |
| Dashboard query (Grafana) | 200ms | 1s | 3s | Pre-aggregated metrics; query caching |
| TTFT SLO target | <200ms | <500ms | <1s | Queue management; prefill optimization |
| TPOT SLO target | <20ms | <30ms | <50ms | Decode optimization; batch management |
| E2E SLO target | <1s | <5s | <10s | Parallel tool calls; cache warm-up |

**Key rule**: Observability must not materially impact the system it observes. Async export, batch aggregation, and sampled scoring keep overhead below 15% in well-instrumented systems.

### 3.3 Throughput & Back-Pressure

**Trace ingestion throughput**: Langfuse v4 runs up to 165× faster than prior versions. Opik handles 40M+ traces/day. Helicone processes 2B+ LLM interactions. ClickHouse-backed stores scale to millions of spans per second.

**Back-pressure mechanisms**:
- **Trace volume spike**: Dynamic sampling rate (reduce from 100% to 10% under load). Always maintain 100% for error traces.
- **Eval queue depth**: If online eval queue exceeds threshold, reduce sampling percentage. Never score on the hot path.
- **Cost budget**: Hard per-user and per-session caps enforced at gateway. At 80% threshold, alert; at 100%, throttle or cascade to cheaper model.
- **Storage growth**: Retention policies (14-day for dev, 90-day for production, 3-year for compliance). Auto-archive old traces to cold storage.

### 3.4 RPO/RTO per Persistence Tier

| Component | RPO | RTO | Mechanism |
|-----------|-----|-----|-----------|
| **Trace store** | Per-batch (async export, 5-30s) | <30s (trace backend restart) | ClickHouse/Tempo with replication |
| **Metrics store** | Per-scrape (15-30s Prometheus) | <5s (Prometheus restart) | WAL-based recovery; Mimir replication |
| **Eval results** | Per-score (append-only) | <5s (DB reconnect) | Platform-managed (Langfuse/Braintrust) |
| **Cost ledger** | Per-request (transactional) | <2s (ledger restart) | Gateway-level atomic writes |
| **Dashboards** | 0 (version-controlled JSON) | <1s (reload from config) | Grafana dashboard-as-code |
| **Alert rules** | 0 (version-controlled) | <1s (reload from config) | Alertmanager config-as-code |

### 3.5 Cost Attribution Dimensions

| Dimension | Why It Matters | Implementation |
|-----------|---------------|----------------|
| **Per user/customer** | Connects LLM usage to gross margin | `customer_id` on every span |
| **Per agent run** | Exposes runaway loops | Track median AND p99 cost by `agent_run_id` |
| **Per feature** | Identifies cost drivers | Tag spans with `feature_name` |
| **Per model** | Tracks tier spending | From `gen_ai.request.model` attribute |
| **Per environment** | Separates dev/prod spend | `environment` tag |

---

## 4. Distributed Resilience & Security

### 4.1 Circuit Breaker for Observability Systems

#### 4.1.1 State Machine

```
                    traces flowing
              ┌───────────────┐
              │               │
              ▼               │
         ┌─────────┐    ┌────┴─────┐    ┌─────────────┐
         │ CLOSED  │───▶│  OPEN    │───▶│ HALF-OPEN   │
         │         │    │          │    │             │
         │ Normal  │    │ Degrade: │    │ Route 3    │
         │ telemetry│   │ buffer   │    │ test traces │
         │ pipeline │    │ locally; │    │ through full│
         │         │    │ metrics  │    │ pipeline    │
         │         │    │ only     │    │             │
         └─────────┘    └──────────┘    └─────────────┘
              ▲          │       ▲            │
              │          │       │            │
              │          │       └────────────┘
              │          │        test traces lost
              │     after 60s
              │     recovery timeout
              │     (60s → 120s → 240s exponential)
              │
              └──────────────────────────────┘
                    3/3 test traces ingested
                    and queryable within 30s
```

**Thresholds**:
- **Closed → Open**: 5 trace export failures (timeout >5s, backend 5xx, queue overflow) within 90s window. OR: trace store latency exceeds 10× baseline for 60s.
- **Open duration**: 60s initial recovery timeout with exponential backoff (60s → 120s → 240s).
- **Open behavior**: Buffer traces locally (ring buffer, 10K spans max). Continue exporting metrics (Prometheus, lightweight). Stop content capture. Log degradation event.
- **Half-Open probes**: 3 synthetic test traces routed through the full pipeline.
- **Half-Open → Closed**: All 3 test traces ingested and queryable within 30s.
- **Escalation**: If circuit stays open >15 minutes, alert on-call. Observability degradation does not block inference — traces are best-effort, not on the critical path.

### 4.2 Failure Taxonomy

| Failure | Class | Detection | Mitigation |
|---------|-------|-----------|------------|
| Trace backend down (ClickHouse/Tempo) | **Transient** | Health check; export failures | Local buffer; retry with backoff |
| OTel Collector crash | **Transient** | Process monitor; span drop counter | Auto-restart; sidecar pattern; queue persistence |
| Trace volume exceeds ingest capacity | **Transient** | Queue depth; backpressure signal | Dynamic sampling; reduce to error-only |
| Content capture exposes PII | **Permanent** (policy violation) | PII scan in redaction processor | Block content; audit trail; incident response |
| Dashboard query timeout | **Transient** | Query latency monitor | Pre-aggregated views; query optimization |
| Cost ledger desync | **Transient** | Reconciliation against provider invoices | Periodic reconciliation job; alert on >5% drift |
| Eval judge model outage | **Transient** | Judge response timeout | Fall back to deterministic scoring only |
| Embedding drift undetected | **Permanent** (quality decay) | Centroid distance monitoring; nightly test suite | Alert at >0.05; trigger re-indexing |
| Observability-induced latency | **Transient** (design flaw) | A/B latency comparison with/without instrumentation | Async export; reduce instrumentation depth |
| Stale SLO thresholds | **Permanent** (process gap) | Monthly SLO review; user harm incidents | Review cadence; tighten/relax based on impact |

### 4.3 Idempotency in Observability

Trace ingestion must handle duplicate spans gracefully — network retries, OTel Collector restarts, and async export can produce duplicates.

```
Span export:
  │
  ┌─────────────────────────────────┐
  │ Dedup Key:                      │
  │ hash(trace_id + span_id)        │
  └──────────────┬──────────────────┘
                 │
  ┌──────────────▼──────────────────┐
  │ IF key in recent_spans (TTL):   │
  │   SKIP (already ingested)       │
  │ ELSE:                           │
  │   ingest span; add key to set   │
  └─────────────────────────────────┘
```

**Metric idempotency**: Prometheus uses last-write-wins for gauges and monotonic counters — duplicate scrapes produce correct results by design. Histogram observations are additive — duplicates inflate counts. Use OTel delta temporality for histograms to avoid double-counting.

**Cost ledger idempotency**: Each cost event carries a unique `request_id`. Gateway ensures at-most-once cost recording per request ID.

### 4.3.1 Poison-Pill Detection in Observability

A poison pill in observability is a trace or metric that corrupts dashboards, misleads investigations, or triggers false alerts.

**Detection heuristics**:
- **Abnormal span duration**: Tool span reporting 0ms or >1 hour → likely instrumentation bug. Quarantine from aggregation.
- **Token count anomaly**: `gen_ai.usage.output_tokens > 100K` on a single span → likely instrumentation error or runaway generation. Flag for review.
- **Cost spike from single request**: One request costing >100× median → investigate before including in cost reports.
- **Synthetic traffic pollution**: Test/synthetic spans reaching production dashboards. Filter by `environment` tag; require `synthetic=true` flag.

**Quarantine**: Flagged spans moved to quarantine partition. Excluded from SLO calculations and dashboard aggregations. Available for forensic investigation. Auto-expire after 30 days.

### 4.4 Zero-Trust Observability Boundaries

1. **Content capture isolation**: Prompt/completion content is opt-in. The OTel Collector redaction processor enforces that no content attribute crosses the trust boundary without PII scanning. Redacted content stored separately from operational telemetry.

2. **Multi-tenant trace isolation**: In multi-tenant platforms, traces from different customers must be isolated. Langfuse and Braintrust support project-level isolation. Datadog supports org-level segregation. Self-hosted stores use row-level security.

3. **Credential exclusion**: Tool arguments may contain API keys, credentials, or tokens. Instrumentation must strip these before span export. Allowlist-based attribute capture (only known-safe attributes exported).

4. **Audit trail immutability**: All observability configuration changes (alert rules, SLO thresholds, dashboard edits) logged to immutable audit trail. Required for SOC 2 compliance.

5. **Eval result integrity**: Online eval scores stored append-only. No retroactive editing of quality scores. Provenance chain: score → judge model version → scoring prompt hash → input trace ID.

---

## 5. Production Enterprise Code

### 5.1 OpenTelemetry GenAI Instrumentation

```python
import time
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from typing import Optional


@dataclass
class SpanAttributes:
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    operation_name: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = ""
    start_time_ms: float = 0.0
    end_time_ms: float = 0.0
    status: str = "ok"
    error_type: str = ""
    custom_attrs: dict = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return self.end_time_ms - self.start_time_ms

    def to_otel_dict(self) -> dict:
        attrs = {
            "gen_ai.operation.name": self.operation_name,
            "gen_ai.provider.name": self.provider,
            "gen_ai.request.model": self.model,
            "gen_ai.usage.input_tokens": self.input_tokens,
            "gen_ai.usage.output_tokens": self.output_tokens,
            "gen_ai.response.finish_reasons": [self.finish_reason],
        }
        if self.error_type:
            attrs["error.type"] = self.error_type
        attrs.update(self.custom_attrs)
        return attrs


class AgentTracer:
    def __init__(self, exporter, cost_calculator):
        self.exporter = exporter
        self.cost = cost_calculator
        self._active_spans: dict[str, SpanAttributes] = {}

    @asynccontextmanager
    async def trace_agent(self, trace_id: str, agent_name: str,
                           customer_id: str = "", feature: str = ""):
        span = SpanAttributes(
            trace_id=trace_id,
            span_id=self._generate_id(),
            parent_span_id=None,
            operation_name="invoke_agent",
            provider="internal",
            model="",
            start_time_ms=time.time() * 1000,
            custom_attrs={
                "customer_id": customer_id,
                "feature_name": feature,
                "agent.name": agent_name,
            },
        )
        self._active_spans[span.span_id] = span
        try:
            yield span
        except Exception as e:
            span.status = "error"
            span.error_type = type(e).__name__
            raise
        finally:
            span.end_time_ms = time.time() * 1000
            await self.exporter.export(span)
            del self._active_spans[span.span_id]

    @asynccontextmanager
    async def trace_llm_call(self, parent_span_id: str, trace_id: str,
                              model: str, provider: str):
        span = SpanAttributes(
            trace_id=trace_id,
            span_id=self._generate_id(),
            parent_span_id=parent_span_id,
            operation_name="chat",
            provider=provider,
            model=model,
            start_time_ms=time.time() * 1000,
        )
        try:
            yield span
        finally:
            span.end_time_ms = time.time() * 1000
            span.custom_attrs["cost_usd"] = self.cost.calculate(
                model=model,
                input_tokens=span.input_tokens,
                output_tokens=span.output_tokens,
            )
            await self.exporter.export(span)

    @asynccontextmanager
    async def trace_tool(self, parent_span_id: str, trace_id: str,
                          tool_name: str):
        span = SpanAttributes(
            trace_id=trace_id,
            span_id=self._generate_id(),
            parent_span_id=parent_span_id,
            operation_name="execute_tool",
            provider="internal",
            model="",
            start_time_ms=time.time() * 1000,
            custom_attrs={"tool.name": tool_name},
        )
        try:
            yield span
        finally:
            span.end_time_ms = time.time() * 1000
            await self.exporter.export(span)

    def _generate_id(self) -> str:
        import uuid
        return uuid.uuid4().hex[:16]
```

### 5.2 Production Cost Tracker with Budget Enforcement

```python
from dataclasses import dataclass, field
from collections import defaultdict


MODEL_PRICING = {
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    "gpt-4o": {"input": 2.50, "output": 10.0},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
}


@dataclass
class CostEvent:
    request_id: str
    customer_id: str
    feature: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class BudgetAlert:
    alert_type: str
    entity_id: str
    current_spend: float
    threshold: float
    message: str


class CostTracker:
    def __init__(self, daily_per_user_cap: float = 50.0,
                 monthly_budget: float = 10_000.0):
        self.daily_cap = daily_per_user_cap
        self.monthly_budget = monthly_budget
        self._user_daily: dict[str, float] = defaultdict(float)
        self._feature_daily: dict[str, float] = defaultdict(float)
        self._total_monthly: float = 0.0
        self._seen_requests: set[str] = set()

    def calculate_cost(self, model: str, input_tokens: int,
                        output_tokens: int) -> float:
        pricing = MODEL_PRICING.get(model, {"input": 5.0, "output": 15.0})
        return (
            (input_tokens / 1_000_000) * pricing["input"]
            + (output_tokens / 1_000_000) * pricing["output"]
        )

    def record(self, event: CostEvent) -> list[BudgetAlert]:
        if event.request_id in self._seen_requests:
            return []
        self._seen_requests.add(event.request_id)

        alerts = []
        self._user_daily[event.customer_id] += event.cost_usd
        self._feature_daily[event.feature] += event.cost_usd
        self._total_monthly += event.cost_usd

        if self._user_daily[event.customer_id] > self.daily_cap:
            alerts.append(BudgetAlert(
                alert_type="hard_cap",
                entity_id=event.customer_id,
                current_spend=self._user_daily[event.customer_id],
                threshold=self.daily_cap,
                message=f"User {event.customer_id} exceeded daily cap "
                        f"(${self._user_daily[event.customer_id]:.2f} / "
                        f"${self.daily_cap:.2f})",
            ))

        if self._total_monthly > self.monthly_budget * 0.8:
            alerts.append(BudgetAlert(
                alert_type="budget_threshold",
                entity_id="organization",
                current_spend=self._total_monthly,
                threshold=self.monthly_budget * 0.8,
                message=f"Monthly spend at {self._total_monthly / self.monthly_budget * 100:.0f}% "
                        f"(${self._total_monthly:.2f} / ${self.monthly_budget:.2f})",
            ))

        return alerts

    def should_throttle(self, customer_id: str) -> bool:
        return self._user_daily.get(customer_id, 0) > self.daily_cap
```

### 5.3 Incident Classifier and Response Router

```python
from dataclasses import dataclass
from enum import Enum


class IncidentClass(Enum):
    HALLUCINATION = "hallucination"
    JAILBREAK = "jailbreak"
    DRIFT = "drift"
    PII_LEAK = "pii_leak"


@dataclass
class IncidentSignal:
    signal_type: str
    value: float
    threshold: float
    trace_ids: list[str]


@dataclass
class ContainmentAction:
    incident_class: IncidentClass
    actions: list[str]
    severity: str
    escalate_to: str


CONTAINMENT_PLAYBOOK = {
    IncidentClass.HALLUCINATION: ContainmentAction(
        incident_class=IncidentClass.HALLUCINATION,
        actions=[
            "Flip to previous prompt version",
            "Tighten output groundedness checks",
            "Run full eval suite on current vs. previous config",
        ],
        severity="medium",
        escalate_to="ai_team_lead",
    ),
    IncidentClass.JAILBREAK: ContainmentAction(
        incident_class=IncidentClass.JAILBREAK,
        actions=[
            "Tighten inline input filters to strict mode",
            "Add bypass pattern to adversarial test set",
            "Alert security team",
        ],
        severity="high",
        escalate_to="security_oncall",
    ),
    IncidentClass.DRIFT: ContainmentAction(
        incident_class=IncidentClass.DRIFT,
        actions=[
            "Flip to last known-good prompt + model pairing",
            "Check: version change? Retrieval shift? Input distribution?",
        ],
        severity="medium",
        escalate_to="ai_team_lead",
    ),
    IncidentClass.PII_LEAK: ContainmentAction(
        incident_class=IncidentClass.PII_LEAK,
        actions=[
            "Tighten output privacy checks to strict thresholds",
            "Enable per-tenant audit log",
            "Notify legal and DPO",
        ],
        severity="critical",
        escalate_to="security_oncall_and_legal",
    ),
}


class IncidentRouter:
    def classify(self, signals: list[IncidentSignal]) -> IncidentClass:
        for signal in signals:
            if signal.signal_type == "pii_detected_in_output":
                return IncidentClass.PII_LEAK
            if signal.signal_type == "guardrail_trip_rate_spike":
                return IncidentClass.JAILBREAK

        for signal in signals:
            if signal.signal_type == "faithfulness_drop":
                retrieval_ok = any(
                    s.signal_type == "retrieval_quality" and s.value > s.threshold
                    for s in signals
                )
                if retrieval_ok:
                    return IncidentClass.HALLUCINATION
                return IncidentClass.DRIFT

        return IncidentClass.DRIFT

    def get_containment(self, incident_class: IncidentClass) -> ContainmentAction:
        return CONTAINMENT_PLAYBOOK[incident_class]
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Observability Stack for a Multi-Product AI Company

**Business context**: A SaaS company runs 3 AI-powered products (customer support chatbot, internal knowledge assistant, code review agent) across 500K monthly active users. Each product has different latency and quality requirements. Current state: engineers eyeball logs after customer complaints — no systematic quality monitoring. Requirements: unified observability across all 3 products, per-product quality SLOs, per-customer cost attribution for the B2B chatbot, <15% observability overhead, and $3K/month budget.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                     MULTI-PRODUCT AI OBSERVABILITY                       │
 │                                                                          │
 │  Products ──▶ ┌──────────────┐ ──▶ ┌──────────────┐ ──▶ ┌───────────┐ │
 │               │ AI Gateway   │     │ OTel Collect.│     │ Langfuse  │ │
 │  - Chatbot    │ (LiteLLM)    │     │ + Redaction  │     │ (traces,  │ │
 │  - Knowledge  │ - Cost track │     │ - GenAI attrs│     │  evals,   │ │
 │  - Code review│ - Rate limit │     │ - PII strip  │     │  quality) │ │
 │               │ - Per-user   │     │ - Batch      │     │           │ │
 │               │   tagging    │     │   export     │     │           │ │
 │               └──────────────┘     └──────────────┘     └─────┬─────┘ │
 │                                                               │       │
 │  ┌─────────────────────────────────────────────────────────────▼────┐  │
 │  │  Prometheus + Grafana: Infra metrics, TTFT/TPOT, GPU, SLOs     │  │
 │  │  Online evals: LLM-judge on 5% sampled traffic (async)         │  │
 │  │  Alerting: SLO burn-rate via Alertmanager                      │  │
 │  └─────────────────────────────────────────────────────────────────┘  │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Full Commercial (Datadog) | B: OSS + Managed Platform (Recommended) | C: Fully Self-Built |
|-----------|------------------------------|------------------------------------------|-------------------|
| **Unified cross-product view** | ⬛⬛⬛ — Single pane for AI + infra | ⬛⬛⬛ — Langfuse (AI traces) + Grafana (infra) with shared dashboards | ⬛⬛⬜ — Must build unification |
| **Per-customer cost attribution** | ⬛⬛⬛ — Nanodollar precision | ⬛⬛⬛ — LiteLLM gateway + Langfuse cost tracking | ⬛⬛⬜ — Must implement |
| **Quality SLOs** | ⬛⬛⬛ — Custom LLM judges; built-in alerting | ⬛⬛⬛ — Langfuse evals + Alertmanager SLO burn-rate | ⬛⬛⬜ — Must build scoring + alerting |
| **Cost at 500K MAU** | ⬛⬛⬜ — $160/mo base + $8/10K spans ≈ $4K+/mo | ⬛⬛⬛ — Langfuse Pro $199/mo + LiteLLM $0 + infra $1K ≈ $1.5K/mo | ⬛⬛⬛ — Infra only ~$1K; high eng time |
| **Setup time** | ⬛⬛⬛ — Days (managed) | ⬛⬛⬜ — 1–2 weeks (LiteLLM + Langfuse + Grafana) | ⬛⬜⬜ — 2+ months |
| **Vendor lock-in risk** | ⬛⬛⬜ — Datadog-dependent | ⬛⬛⬛ — MIT/Apache; OTel standard; portable | ⬛⬛⬛ — No vendor |

**Recommended approach**: **B (OSS + Managed Platform)**.

**Decision rationale**: The $3K/month budget eliminates Option A at scale — Datadog's per-span pricing with 3 products and 500K MAU would exceed $4K/month quickly, plus auto-activation risks. Option C saves on tooling but the 2+ months of engineering time building custom observability costs more than the savings. Option B delivers full coverage at ~$1.5K/month: LiteLLM gateway (free, self-hosted) handles cost tracking and per-customer tagging; Langfuse Pro ($199/month) provides trace storage, quality evaluation, and multi-product project isolation; Prometheus + Grafana (self-hosted, ~$1K/month infra) covers infrastructure metrics, TTFT/TPOT SLOs, and SLO-based alerting via Alertmanager. OTel GenAI conventions ensure vendor portability — switching Langfuse for another backend requires changing the exporter, not re-instrumenting. Online evals run asynchronously on 5% sampled traffic with Flash-class judges, adding ~$200/month in judge tokens.

### 6.2 Scenario: Agent Observability for Autonomous Coding Agents at Scale

**Business context**: A developer tools company runs autonomous coding agents (similar to Devin/Cursor Background Agents) for 10,000 developers. Each agent session runs 5–50 steps, invoking tools (file read/write, bash, test runner, git). Agents occasionally enter infinite loops, hallucinate file paths, or produce code that passes tests but introduces subtle bugs. Requirements: detect runaway agents within 30 seconds, attribute cost per developer per project, visualize full agent trajectories for debugging, and handle 100K agent sessions/day.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                     CODING AGENT OBSERVABILITY                           │
 │                                                                          │
 │  Agent ──▶ ┌──────────────┐ ──▶ ┌──────────────┐ ──▶ ┌────────────┐   │
 │  Session   │ OTel Instrum.│     │ Trace Store  │     │ Analysis   │   │
 │            │ - invoke_    │     │ (ClickHouse) │     │ Engine     │   │
 │            │   agent root │     │ - 100K sess/ │     │            │   │
 │            │ - chat spans │     │   day        │     │ - Loop     │   │
 │            │ - tool spans │     │ - 30-day     │     │   detect   │   │
 │            │   (file,bash,│     │   retention  │     │ - Runaway  │   │
 │            │    test,git) │     │              │     │   alert    │   │
 │            │ - step count │     │              │     │ - Cost per │   │
 │            │ - token usage│     │              │     │   dev/proj │   │
 │            └──────────────┘     └──────────────┘     └──────┬─────┘   │
 │                                                             │         │
 │                                              ┌──────────────▼───────┐ │
 │                                              │ Real-Time Guards     │ │
 │                                              │ - Step count > 50    │ │
 │                                              │   → kill agent       │ │
 │                                              │ - Same tool 3x same  │ │
 │                                              │   args → halt + alert│ │
 │                                              │ - Token > 200K/sess  │ │
 │                                              │   → budget kill      │ │
 │                                              └──────────────────────┘ │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: General Observability (Langfuse/LangSmith) | B: Custom Agent Observability + Guards (Recommended) | C: Per-Agent VM Monitoring Only |
|-----------|-----------------------------------------------|------------------------------------------------------|-------------------------------|
| **Loop detection (<30s)** | ⬛⬛⬜ — Manual trace inspection; no real-time guards | ⬛⬛⬛ — Real-time step counter + duplicate tool call detection | ⬛⬜⬜ — VM-level only; no semantic insight |
| **Trajectory visualization** | ⬛⬛⬛ — Full span trees; agent graph view | ⬛⬛⬛ — Custom span trees + trajectory replay | ⬛⬜⬜ — No trajectory visibility |
| **Cost attribution per dev/project** | ⬛⬛⬜ — Generic cost tracking; needs custom tagging | ⬛⬛⬛ — Built-in per-developer, per-project attribution | ⬛⬜⬜ — VM cost only; no token attribution |
| **Scale (100K sessions/day)** | ⬛⬛⬜ — Platform may throttle; cost at volume | ⬛⬛⬛ — Self-hosted ClickHouse; horizontal scale | ⬛⬛⬛ — VM monitoring scales naturally |
| **Subtle bug detection** | ⬛⬛⬜ — No test-aware analysis | ⬛⬛⬛ — Trajectory quality scoring (tests pass but diff suspicious) | ⬛⬜⬜ — No code quality insight |
| **Build effort** | ⬛⬛⬛ — Minimal (platform) | ⬛⬛⬜ — 3–4 weeks (custom guards + analysis engine) | ⬛⬛⬛ — Standard infra monitoring |

**Recommended approach**: **B (Custom Agent Observability + Guards)**.

**Decision rationale**: The core requirement — detect runaway agents within 30 seconds — rules out Option A (general platforms lack real-time loop detection guards) and Option C (VM monitoring can't see semantic agent behavior). Option B builds three real-time guards into the agent runtime: (1) step counter kills agents exceeding 50 steps, (2) duplicate tool call detector (same tool + same args 3 consecutive times) halts and alerts, (3) per-session token budget (200K tokens) triggers hard kill. These guards run in-process with <1ms overhead. The analysis engine runs on a self-hosted ClickHouse store handling 100K sessions/day (each session: 5–50 spans × avg 10 attributes = 500K–5M rows/day — well within ClickHouse capacity). Per-developer, per-project cost attribution uses `developer_id` and `project_id` span attributes flowing through OTel. Trajectory visualization replays the full span tree for debugging. The 3–4 week build investment pays for itself by preventing the $47K-type runaway incidents seen in production coding agents. At 100K sessions/day, a managed platform at $2.50/1K traces would cost $250/day ($7.5K/month) — self-hosted ClickHouse costs ~$2K/month in infrastructure.

---

*Module 14 complete. Covers OpenTelemetry GenAI semantic conventions (spans, metrics, events), 8 observability platforms with pricing, three-layer monitoring stack, agent-specific observability (4 pillars, silent failure modes), RAG observability (retrieval + generation + embedding drift), cost attribution (5 dimensions, budget enforcement), 6 agent SLOs with goodput, incident response (6 steps, 4 classes), and the observability-evaluation feedback loop.*
