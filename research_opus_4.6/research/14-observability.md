# Topic 14: Observability — LLM/AI Observability and Monitoring

## Table of Contents
1. [Overview and Market Context](#1-overview-and-market-context)
2. [OpenTelemetry GenAI Semantic Conventions](#2-opentelemetry-genai-semantic-conventions)
3. [Tracing Architectures](#3-tracing-architectures)
4. [Key LLM Inference Metrics](#4-key-llm-inference-metrics)
5. [Observability Platforms](#5-observability-platforms)
6. [Agent-Specific Observability](#6-agent-specific-observability)
7. [RAG Observability](#7-rag-observability)
8. [Cost Observability](#8-cost-observability)
9. [Production Monitoring Patterns](#9-production-monitoring-patterns)
10. [SLOs for AI Systems](#10-slos-for-ai-systems)
11. [Incident Response and On-Call for AI](#11-incident-response-and-on-call-for-ai)
12. [Dashboards, Alerting, and Infrastructure](#12-dashboards-alerting-and-infrastructure)
13. [The Observability-Evaluation Connection](#13-the-observability-evaluation-connection)
14. [Integration with Security (Module 13)](#14-integration-with-security-module-13)
15. [Interview Preparation](#15-interview-preparation)

---

## 1. Overview and Market Context

### What Is LLM Observability?

LLM observability is the practice of capturing, analyzing, and acting on the full execution trace of AI/LLM applications in production -- including model calls, tool invocations, retrieval steps, agent decisions, token usage, cost, latency, and output quality. It extends traditional application monitoring by adding semantic quality measurement to the operational health signals (uptime, latency, error rate) that infrastructure monitoring already covers.

Traditional monitoring tells you *something is wrong*; observability tells you *why*. For LLM systems, a model can return a syntactically valid, plausible-sounding response that is factually wrong -- no error is thrown, no latency spike occurs, and all infrastructure dashboards show green. LLM observability closes this gap by measuring whether the reasoning process and output quality were correct, not just whether the system executed without failure. ([OpenSourceForU, 2026](https://www.opensourceforu.com/2026/08/from-black-box-to-glass-box-observability-strategies-for-production-ai/))

### Why It Matters

- An LLM can return a 200 OK with normal latency while hallucinating, violating content policies, or producing harmful output. Standard APM catches none of this.
- AI agents are non-deterministic: the same input can trigger different tool sequences, retrieve different documents, and produce different outputs each run.
- A 2025 study reported that LLM hallucinations cost businesses over **$67.4 billion** in losses during 2024. ([OpenObserve, 2026](https://openobserve.ai/blog/llm-monitoring-best-practices/))
- Stanford AI Lab research indicates poorly evaluated RAG systems can produce hallucinations in up to **40% of responses** despite accessing correct information. ([Dextralabs, 2025](https://dextralabs.com/blog/production-rag-in-2025-evaluation-cicd-observability/))

### Market Size and Growth

The LLM observability platform market has grown rapidly:

| Year | Market Size | Source |
|------|------------|--------|
| 2024 | $510.5M -- $1.4B (varies by definition) | [Market.us](https://market.us/report/llm-observability-platform-market/), [TBRC](https://www.giiresearch.com/report/tbrc1981334-large-language-model-llm-observability-platform.html) |
| 2025 | $1.97B (TBRC) / $3.2B (DataIntelo) | [ResearchAndMarkets](https://www.researchandmarkets.com/reports/6215671/large-language-model-llm-observability), [DataIntelo](https://dataintelo.com/report/llm-observability-platform-market) |
| 2026 | $2.69B | [TBRC](https://www.giiresearch.com/report/tbrc1981334-large-language-model-llm-observability-platform.html) |
| 2030 | $9.26B (projected) | [TBRC](https://www.giiresearch.com/report/tbrc1981334-large-language-model-llm-observability-platform.html) |
| 2034 | $24.8B (projected) | [DataIntelo](https://dataintelo.com/report/llm-observability-platform-market) |

- **CAGR**: 36.2% (2025-2030) per TBRC; 25.4% (2025-2034) per DataIntelo; 31.8% per Market.us
- **VC investment**: $1.1 billion deployed across LLM observability and AI evaluation startups between January 2024 and April 2026. ([DataIntelo](https://dataintelo.com/report/llm-observability-platform-market))
- **Agentic AI Observability sub-market**: $0.55B in 2025, growing to $2.05B by 2030 at 30.1% CAGR. LLM/Agent Observability accounts for 40.1% share of the agentic AI monitoring market. ([Mordor Intelligence](https://www.mordorintelligence.com/industry-reports/agentic-artificial-intelligence-monitoring-analytics-and-observability-tools-market))

### Adoption Statistics

- **Gartner** predicts that by 2028, LLM observability investments will account for **50% of GenAI deployments**, up from only **15% in early 2026**. ([ConfidentAI, 2026](https://www.confident-ai.com/knowledge-base/compare/top-7-llm-observability-tools))
- ~65% of enterprises are transitioning from proprietary observability systems to open standards (primarily OpenTelemetry) as of 2025. ([OpenPR](https://www.openpr.com/news/4514790/emerging-growth-patterns-driving-the-expansion-of-the-large))
- Model API spending doubled from $3.5B to $8.4B between late 2024 and mid-2025. ([Braintrust](https://www.braintrust.dev/articles/how-to-track-llm-costs-2026))
- In 2025, enterprise monthly AI spend averaged **$85,521**, yet only **34% of companies** had mature cost management processes. **60% of AI projects** exceeded original cost estimates by 30-50%. ([Braintrust](https://www.braintrust.dev/articles/how-to-track-llm-costs-2026))

---

## 2. OpenTelemetry GenAI Semantic Conventions

### Background

OpenTelemetry (OTel) is the CNCF-backed standard for distributed tracing, metrics, and logs. In April 2024, the **GenAI Special Interest Group (GenAI SIG)** was formed to define semantic conventions specifically for AI/LLM workloads. The conventions solve the fragmentation problem: without a standard, every platform invents its own attribute names for model, tokens, tool calls, etc. ([OpenTelemetry Blog, 2025](https://opentelemetry.io/blog/2025/ai-agent-observability/))

As of June 2026 (v1.42.0), all `gen_ai.*` attributes and spans were moved from the main `open-telemetry/semantic-conventions` repo into a dedicated repository: **`open-telemetry/semantic-conventions-genai`**. This gives GenAI conventions their own release cadence. ([GitHub](https://github.com/open-telemetry/semantic-conventions-genai))

### Stability Status

The GenAI conventions remain **pre-stable and experimental** -- there is no 1.0 release, and names can still change between versions. However, core concepts have settled. The `OTEL_SEMCONV_STABILITY_OPT_IN` environment variable manages version transitions -- setting it to `gen_ai_latest_experimental` switches to the newest attribute format, and dual-emission mode maintains backward compatibility. v1.36 is the transition baseline. ([OpenTelemetry Blog, 2026](https://opentelemetry.io/blog/2026/genai-observability/))

### Three Signal Types

The GenAI conventions use all three OTel signal types:

**1. Traces (Spans)**
Trees of spans covering logical operations. The GenAI conventions define span shapes for:
- Model inference (`chat`, `text_completion`, `generate_content`)
- Embeddings
- Retrieval operations
- Memory operations
- Tool execution (`execute_tool`)
- Agent invocation (`invoke_agent`)
- Workflow invocation (`invoke_workflow`)
- Planning

**2. Metrics**
Pre-aggregated numeric series:
- `gen_ai.client.operation.duration` -- histogram of LLM call latencies (boundaries: powers-of-two up to ~82s)
- `gen_ai.client.token.usage` -- histogram of token consumption (boundaries: powers-of-four up to ~67M tokens)
- `gen_ai.client.time_per_output_token` -- streaming TPS metric
- Agent invocation duration, per-invocation call counts, tool execution duration

**3. Logs/Events**
Events are log records with well-known names and defined attribute sets, used for capturing prompt/completion content.

([OpenTelemetry Blog, 2026](https://opentelemetry.io/blog/2026/genai-observability/); [GitHub metrics spec](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md))

### Core Span Attributes

| Attribute | Description | Requirement |
|-----------|-------------|-------------|
| `gen_ai.operation.name` | Operation type (`chat`, `text_completion`, `generate_content`) | Required |
| `gen_ai.provider.name` | Provider identifier | Required |
| `gen_ai.request.model` | Model requested (e.g., `gpt-4o`) | Required |
| `gen_ai.response.model` | Model that actually generated the response | Recommended |
| `gen_ai.usage.input_tokens` | Input token count | Recommended |
| `gen_ai.usage.output_tokens` | Output token count | Recommended |
| `gen_ai.response.finish_reasons` | Why generation stopped (`stop`, `tool_calls`) | Recommended |
| `error.type` | Error class (if applicable) | Conditionally Required |
| `server.address` | Server address | Recommended |
| `server.port` | Server port | Conditionally Required |

([OpenTelemetry GenAI Spec](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md); [Greptime, 2026](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions))

### Content Capture (Opt-In)

Content capture (prompts, completions, tool arguments, retrieved chunks) is **opt-in by default** to protect PII and sensitive data. When enabled, attributes include:
- `gen_ai.system_instructions` -- system prompts
- `gen_ai.input.messages` -- user messages
- `gen_ai.output.messages` -- assistant responses

Teams must implement sampling, redaction, and retention policies before enabling content capture. The OTel Collector processor is a natural enforcement point for content redaction. ([OpenTelemetry Blog, 2026](https://opentelemetry.io/blog/2026/genai-observability/))

### Agent-Specific Span Types

The spec defines four operation types for agents:

| Span Name | Purpose |
|-----------|---------|
| `invoke_agent` | Top-level span for agent interactions (the root of the decision tree) |
| `chat` | Child span for each LLM call within an agent |
| `execute_tool` | Span wrapping each tool invocation |
| `invoke_workflow` | Span for orchestrators/graphs coordinating multiple agents or GenAI calls |

([GitHub agent spans spec](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md); [MortalApps](https://mortalapps.com/agents/production-engineering/opentelemetry-span-types-for-agents/))

### Token Usage Metric Rules

For `gen_ai.client.token.usage`:
- SHOULD be reported when token counts are readily available
- If streaming returns usage info, it SHOULD be used
- If instrumentation counts tokens independently, it SHOULD record the result
- If instrumentation cannot efficiently obtain counts, it MAY allow offline counting
- When systems report both *used* and *billable* tokens, instrumentation MUST report billable tokens
- `gen_ai.token.type` takes values `input` or `output`; reasoning and cached tokens are exposed as separate span attributes (`gen_ai.usage.reasoning.output_tokens`)

([GitHub metrics spec](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md))

### Instrumentation Libraries

**Official OTel Python Contrib (instrumentation-genai)**:
- First release: Python library instrumenting OpenAI client calls
- Captures spans, events, model inputs, response metadata, token usage
- Contributors: Amazon, Elastic, Google, IBM, Langtrace, Microsoft, OpenLIT, Scorecard, Traceloop
- ([OpenTelemetry Blog, 2024](https://opentelemetry.io/blog/2024/otel-generative-ai/))

**OpenLLMetry (by Traceloop)**:
- Open-source (Apache 2.0) set of OTel extensions for GenAI observability
- Covers LLM providers (OpenAI, Anthropic, Mistral, Cohere, Ollama, Vertex AI, HuggingFace Transformers) and Vector DBs (Weaviate, Pinecone, Chroma)
- Framework instrumentations: LangChain, Haystack, LlamaIndex
- Recent additions: Hub (LLM gateway with standardized OTel spans) and an MCP server bridging production telemetry into developer tooling
- ([GitHub](https://github.com/traceloop/openllmetry); [Traceloop Blog](https://www.traceloop.com/blog/visualizing-llm-performance-with-opentelemetry-tools-for-tracing-cost-and-latency))

**Framework-Native Instrumentation**:
- LangChain, CrewAI, AutoGen, AG2 emit OTel-compliant spans natively or via instrumentation packages
- VS Code Copilot emits traces, metrics, and events for every agent interaction
- OpenAI Codex exports structured log events and OTel metrics
- Claude Code exports metrics and log events via OTel, with trace support in beta

### Two Convention Tracks for Agents

1. **Agent Application Semantic Convention** -- finalized based on Google's AI agent white paper, covering generic agent tracing (issue #1732)
2. **Agent Framework Semantic Convention** -- active development (issue #1530) targeting IBM Bee Stack, CrewAI, AutoGen, LangGraph, Semantic Kernel, PydanticAI; allows framework-vendor-specific extensions while adhering to the common standard

([OpenTelemetry Blog, 2025](https://opentelemetry.io/blog/2025/ai-agent-observability/))

### Adoption

Major vendors including **Datadog, Honeycomb, New Relic, Google Cloud, AWS, Azure** already support these conventions. The industry is converging on OTel as the standard telemetry layer for AI agent systems. OpenInference (Arize's convention layer) was adopted by Microsoft as the shared trace contract for its 2026 "open trust stack" for AI agents. ([Greptime, 2026](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions); [Arize](https://arize.com/phoenix/))

---

## 3. Tracing Architectures

### The Span Tree Model

Every user request in a well-instrumented AI application produces a **trace** -- a tree of **spans**, where each span represents one logical operation (LLM call, tool execution, retrieval, etc.) with a start time, end time, status, and key/value attributes.

**Example trace structure for an agent request:**
```
invoke_agent (root span)
  |-- chat (LLM planning call)
  |     |-- gen_ai.request.model: gpt-4o
  |     |-- gen_ai.usage.input_tokens: 1200
  |     |-- gen_ai.usage.output_tokens: 85
  |-- execute_tool (search_database)
  |     |-- tool.name: search_database
  |     |-- tool.arguments: {"query": "customer order 12345"}
  |     |-- tool.result: {...}
  |     |-- duration: 340ms
  |-- chat (LLM reasoning with tool results)
  |     |-- gen_ai.request.model: gpt-4o
  |     |-- gen_ai.usage.input_tokens: 2100
  |     |-- gen_ai.usage.output_tokens: 250
  |-- execute_tool (send_email)
  |     |-- tool.name: send_email
  |     |-- duration: 120ms
  |-- chat (LLM final response)
        |-- gen_ai.response.finish_reasons: ["stop"]
```

### Two Categories of Tracing

**1. API-level / Gateway tracing** (Helicone, Portkey, LiteLLM):
- Captures each individual LLM API call by sitting as a proxy
- Logs prompt, completion, tokens, cost, latency per call
- Misses the orchestration graph -- cannot see agent reasoning, tool selection decisions, or multi-step flow
- Zero-instrumentation setup (change base URL)

**2. SDK / OTel tracing** (Langfuse, Phoenix, LangSmith, Braintrust):
- Traces the entire multi-step agent execution as a span tree
- Preserves parent-child relationships across agent handoffs, tool calls, retrieval
- Requires SDK instrumentation (decorators, wrappers, or framework auto-instrumentation)
- Shows agent reasoning flow and decision graph

**Key insight**: proxy/gateway tools log calls; SDK/OTel tools trace trajectories. Many production stacks use both -- a gateway for cost control and routing, plus an OTel-based platform for quality and debugging. ([Firecrawl, 2026](https://www.firecrawl.dev/blog/best-llm-observability-tools))

### Nested Spans for Complex Architectures

For multi-agent systems, spans nest to reflect the coordination graph:
```
invoke_workflow (orchestrator)
  |-- invoke_agent (research_agent)
  |     |-- chat (LLM call)
  |     |-- execute_tool (web_search)
  |     |-- chat (LLM synthesis)
  |-- invoke_agent (writing_agent)
        |-- chat (LLM call with research context)
        |-- execute_tool (save_document)
```

The `invoke_workflow` span SHOULD be reported for composable processes coordinating multiple agents. It SHOULD NOT be reported for standalone agent invocations or when the workflow invocation is an internal implementation detail. ([GitHub agent spans spec](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md))

### Instrumentation Approaches

**Option 1: Baked-in (Framework-native)**
- Advantages: zero-config, out-of-box observability
- Drawbacks: adds bloat, risk of OTel version lock-in
- Examples: CrewAI, LangChain native instrumentation

**Option 2: External instrumentation libraries**
- Advantages: decoupled from framework, community-maintained
- Drawbacks: fragmentation risk, slower OTel review queue
- Examples: OpenLLMetry, opentelemetry-instrumentation-genai

Best practice: provide configuration to enable/disable telemetry; plan for avoiding collision with external instrumentation. ([OpenTelemetry Blog, 2025](https://opentelemetry.io/blog/2025/ai-agent-observability/))

### Observability Overhead

| Approach | Overhead | Context |
|----------|----------|---------|
| Lightweight tracing (LangSmith, Laminar) | Near baseline | Multi-agent application |
| Langfuse step-level tracing | ~15% | Multi-agent, sync instrumentation |
| AgentOps | ~12% | Multi-agent application |
| Portkey gateway | 20-40ms | API gateway routing |
| Helicone proxy | 50-80ms average | Proxy-based, Cloudflare Workers |
| Voice agent tracing (general) | 1-5% | End-to-end voice pipeline |
| DMI-Lib model-internal | 0.4-6.8% | Offline batch inference |

Key finding: overhead is driven by instrumentation depth and whether tracing runs synchronously or asynchronously. Tools with deeper step-level tracing (Langfuse, AgentOps) exhibit higher overhead; lighter, async approaches stay near baseline. ([AIMultiple, 2026](https://aimultiple.com/agentic-monitoring))

---

## 4. Key LLM Inference Metrics

### Time to First Token (TTFT)

Time from query submission to first token received. Includes request queuing, prefill computation, and network latency. Longer prompts increase TTFT because attention computes over the full input sequence to create the KV cache.

- **Interactive target**: p95 TTFT under 500ms (MLCommons MLPerf 5.1 standard)
- **Glean production example**: TTFT dropped from 4.3s to 0.6s on cache-warm requests (7x improvement, no model changes)
- **LMCache**: demonstrated 6.7x faster TTFT (1.2s to 0.18s) alongside 80% higher throughput

([BentoML Inference Handbook](https://bentoml.com/llm/inference-optimization/llm-inference-metrics); [TianPan.co, 2026](https://tianpan.co/blog/2026-03-10-llm-latency-decomposition-ttft-vs-throughput))

### Time Per Output Token (TPOT) / Inter-Token Latency (ITL)

Average time between consecutive output tokens. Formula: `TPOT = (E2E_latency - TTFT) / (output_tokens - 1)`.

- **Human reading speed**: ~4-5 tokens/second, so ITL up to ~200ms is acceptable
- **MLCommons target**: TPOT <= 30ms (~33 tokens/second)
- Above 250ms, streaming feels choppy or broken
- TPOT gives average speed; ITL shows whether speed is steady or jittery -- a 200ms pause mid-response is noticed more than steady 20ms

([NVIDIA NIM Benchmarking](https://docs.nvidia.com/nim/benchmarking/llm/latest/metrics.html); [Anyscale](https://docs.anyscale.com/llm/serving/benchmarking/metrics))

### End-to-End (E2E) Latency

Total time from query submission to complete response received, including queuing, batching, prefill, decode, and network latency.

### Throughput (TPS / RPS)

Total output tokens per second across all concurrent requests. Larger batches improve aggregate TPS but may increase per-user TTFT and TPOT. Beyond the GPU saturation point, performance degrades. ([BentoML](https://bentoml.com/llm/inference-optimization/llm-inference-metrics))

### Goodput

Requests per second that meet **all** latency SLOs simultaneously (TTFT, TPOT, E2E). A system processing 500 RPS with 30% exceeding TTFT SLO has a goodput of only 350 RPS. Optimizing for goodput rather than raw throughput produces the right operational incentives. ([Rost Glukhov, 2026](https://www.glukhov.org/observability/monitoring-llm-inference-prometheus-grafana/))

### Critical Monitoring Insight

Never use averages for TTFT SLO management. A system with 200ms average TTFT can have a p99 of 3,000ms -- meaning 1% of users wait 15x longer than average. ([Rost Glukhov, 2026](https://www.glukhov.org/observability/monitoring-llm-inference-prometheus-grafana/))

### Serving Framework Metrics

| Framework | Endpoint | Key Metrics |
|-----------|----------|-------------|
| **vLLM** | `/metrics` (Prometheus, `vllm:` prefix) | `vllm:time_to_first_token_seconds`, `vllm:inter_token_latency_seconds`, `vllm:e2e_request_latency_seconds`, `vllm:request_prefill_time_seconds` |
| **TGI** | `/metrics` (Prometheus) | Queue size, request duration, queue duration, mean time per token |
| **llama.cpp** | `/metrics` (enabled via `--metrics` flag) | Request latency, tokens/sec |

([vLLM docs](https://docs.vllm.ai/en/stable/design/metrics/); [Rost Glukhov, 2026](https://www.glukhov.org/observability/monitoring-llm-inference-prometheus-grafana/))

---

## 5. Observability Platforms

### Platform Taxonomy

The ecosystem falls into four categories:

1. **AI-native observability** (Langfuse, LangSmith, Braintrust, Arize, Opik) -- treat the LLM trace as the primary object
2. **Open-source evaluation** (Arize Phoenix, DeepEval, MLflow, RAGAS) -- focus on scoring outputs
3. **AI gateways** (Helicone, Portkey, LiteLLM) -- proxy layer adding logging, caching, cost tracking
4. **APM extensions** (Datadog, New Relic, Dynatrace) -- bolt LLM tracing onto existing infrastructure monitoring

([MarkTechPost, 2026](https://www.marktechpost.com/2026/08/09/top-llm-observability-and-evaluation-platforms-in-2026-langfuse-langsmith-braintrust-arize-and-more-compared/))

### 5.1 Langfuse (Open-Source Leader)

**License**: MIT (fully open source, no feature gates)
**GitHub Stars**: 28,000+
**Self-hosting**: Docker Compose (Postgres + ClickHouse)
**Acquisition**: Acquired by ClickHouse Inc. on January 16, 2026 (same day as ClickHouse's $400M Series D at $15B valuation). Langfuse remains MIT-licensed with no pricing/licensing changes. ([ClickHouse Blog](https://clickhouse.com/blog/clickhouse-acquires-langfuse-open-source-llm-observability))

**Key Features**:
- Full tracing with multi-turn conversation support
- Prompt versioning with built-in playground
- Evaluation via LLM-as-judge, user feedback, or custom metrics
- Framework-agnostic via OpenTelemetry
- SOC 2 Type II, ISO 27001; HIPAA-ready region on Pro+
- Masking/redaction for sensitive fields before ingest

**Pricing (2026)**:

| Tier | Price | Included Units | Retention | Users |
|------|-------|---------------|-----------|-------|
| Hobby | Free | 50,000/month | 30 days | 2 |
| Core | $29/month | 100,000/month | 90 days | Unlimited |
| Pro | $199/month | Included | 3 years | Unlimited |
| Enterprise | $2,499/month | Custom | Custom | Unlimited |

Overage: $8 per 100K units. A "unit" = trace + observation + score, so one multi-step agent request can burn 20+ units. No per-seat charges at any tier. ([Langfuse Pricing](https://langfuse.com/blog/joining-clickhouse); [Coverge](https://coverge.ai/blog/langfuse-pricing))

**Best for**: Teams wanting full-featured, open-source, framework-agnostic observability with strict data residency control.

### 5.2 LangSmith

**License**: Proprietary (LangChain commercial platform)
**Best for**: Teams building with LangChain/LangGraph

**Key Features**:
- Richest tracing for LangChain/LangGraph applications with native agent graph visualization
- Annotation queues for structured human review
- Polly AI Assistant for natural-language trace debugging
- Topic clustering for automatic behavior categorization
- LangGraph Studio visual step-through debugging
- OpenTelemetry support (framework-agnostic via SDKs for Python, TypeScript, Go, Java)

**Pricing (2026)**:

| Tier | Price | Traces/month | Retention |
|------|-------|-------------|-----------|
| Developer | Free | 5,000 | 14 days |
| Plus | $39/seat/month | 10,000 base | 14 days (400-day: $5/1K) |
| Enterprise | Custom | Custom | Custom |

Overages: $2.50 per 1,000 base traces. Deployments: $0.005/run beyond free allotment. ([LangChain Pricing](https://www.langchain.com/pricing); [MetaCTO](https://www.metacto.com/blogs/the-true-cost-of-langsmith-a-comprehensive-pricing-integration-guide))

**Limitation**: Significant framework lock-in -- non-LangChain stacks lose most integration advantage.

### 5.3 Arize Phoenix (OSS)

**License**: Elastic License 2.0 (free, self-hostable, no feature gates)
**GitHub Stars**: 10,300+
**Downloads**: 2M+ monthly
**Notable**: Dynatrace announced definitive agreement to acquire Arize in August 2026.

**Key Features**:
- 50+ research-backed evaluation metrics (faithfulness, relevance, safety, toxicity, hallucination)
- Multi-step agent trajectory analysis
- Trace clustering, anomaly detection, retrieval relevancy visualization
- Embedding visualization (t-SNE, UMAP) for understanding LLM behavior
- Drift detection via Euclidean distance between embedding centroids across time windows
- Agent graph visualization for multi-agent debugging
- Built on OpenTelemetry + OpenInference
- Auto-instrumentors for LangGraph, AutoGen, CrewAI, OpenAI Agents SDK, Agno (35+ Python, 10+ JS packages)

**Pricing**: Phoenix self-hosted is free with no platform usage fee. Arize AX (commercial) adds drift detection, real-time alerting, Alyx debugging assistant, RBAC, compliance. AX Free: 25K spans/month; AX Pro: $50/month. ([Arize](https://arize.com/pricing/); [Voiceflow](https://www.voiceflow.com/blog/what-is-arize-ai))

**Best for**: Teams wanting eval rigor, drift detection, vendor-neutral OpenTelemetry tracing.

### 5.4 Datadog LLM Observability (now "Agent Observability")

**Type**: APM extension (enterprise)

**Key Features**:
- Estimated cost tracking for 800+ models (nanodollar precision)
- Custom LLM-as-a-judge evaluators (GA late 2025)
- Auto-instrumentation for LangChain, CrewAI, Pydantic AI, Strands Agents, AWS Bedrock, LiteLLM
- Execution flow charts for agent runs (announced DASH 2025)
- Sensitive data scanning and redaction
- Unified platform connecting AI with backend services and infrastructure
- Only LLM spans are billed -- tool, embedding, retrieval, and agent spans are free

**Pricing (May 2026)**:
- Free: 40,000 LLM spans/month
- Pro: $160/month for 100,000 spans
- Per-request: $8 per 10,000 monitored LLM requests
- Retention: 15 days default; add-ons for 30/60/90 days
- Warning: auto-activation risk -- Datadog can automatically activate a $120/day premium when detecting LLM spans

([Datadog Pricing](https://www.datadoghq.com/pricing/); [CubeAPM](https://cubeapm.com/faqs/datadog-llm-observability/); [Ecorpit](https://ecorpit.com/datadog-llm-observability-pricing-cap-costs-2026/))

**Best for**: Teams already on Datadog wanting unified AI + infrastructure observability.

### 5.5 Helicone

**License**: Apache 2.0
**Architecture**: Proxy/gateway (Rust-based, Cloudflare Workers + ClickHouse + Kafka)
**Scale**: 2B+ LLM interactions processed
**Status**: Acquired by Mintlify in March 2026; now in **maintenance mode** (security patches, new model support, but no new features). ([ChatForest](https://chatforest.com/reviews/helicone-llm-observability-gateway/))

**Key Features**:
- One-line integration (change base URL)
- Gateway: caching, rate limiting, API key management, threat detection, moderation
- Routing across 100+ providers with failover
- Prompt versioning and deployment via gateway
- SOC 2, HIPAA, GDPR compliance

**Performance**: P95 gateway overhead <5ms; throughput ~3,000 req/sec; average latency 50-80ms added.

**Pricing**: Free (10K requests/month, 7-day retention); Pro: $79/month. 50% off first year for startups <2 years old / <$5M funding. ([Helicone](https://www.helicone.ai/); [GitHub](https://github.com/helicone/helicone))

### 5.6 W&B Weave

**Type**: ML platform extension
**Architecture**: Python/TypeScript SDKs with `@weave.op` decorator

**Key Features**:
- Automatic tracking of all LLM calls (inputs, outputs, costs, latency, evaluation metrics) via decorator
- Trace trees organizing call stack with metrics aggregated at every level
- Built-in agents view for sessions, turns, LLM calls, tool calls
- Guardrails: pre-built scorers for toxicity, bias, PII, hallucinations, coherence, fluency, context relevance
- Evaluation framework with exact match, regex, model-graded, embedding similarity scorers
- OTel support exists (OTLP ingest) but documented as secondary path

**Best for**: Teams already in the W&B ecosystem wanting unified ML training + LLM production monitoring. ([W&B Weave Docs](https://docs.wandb.ai/weave); [W&B Site](https://wandb.ai/site/weave/))

### 5.7 MLflow 3

**License**: Apache 2.0 (fully open source, free)
**Architecture**: Open platform unifying tracking, evaluation, and observability for GenAI

**Key Features**:
- Fully compatible with OpenTelemetry; natively supports GenAI Semantic Conventions
- Tracing integrations with 20+ GenAI libraries (OpenAI, LangChain, LlamaIndex, DSPy, Pydantic AI)
- Human feedback and annotation tracking for HITL evaluation
- Versioning via LoggedModel entity linking app versions to Git commits, configs, traces, and eval runs
- Lightweight `mlflow-tracing` SDK: 95% smaller footprint than full `mlflow` package
- Async logging for zero-impact production tracing
- Databricks integration: traces stored in Unity Catalog as OTel Delta tables, queryable with SQL

**Cost**: Free, no SaaS fees. Trace data hosted on your own infrastructure. Managed MLflow on Databricks adds enterprise governance, scaling, and lakehouse integration. ([Databricks Blog](https://www.databricks.com/blog/mlflow-30-unified-ai-experimentation-observability-and-governance); [MLflow Tracing Docs](https://mlflow.org/docs/latest/genai/tracing/))

### 5.8 Braintrust

**Type**: Eval-first observability platform
**Funding**: $80M Series B (Feb 2026, led by ICONIQ), $800M valuation
**Customers**: Notion, Stripe, Vercel, Airtable, Instacart, Zapier, Ramp, Dropbox, Cloudflare

**Key Features**:
- Brainstore: purpose-built query engine for agent traces
- Active monitoring (Topics): surfaces patterns across task, issues, sentiment
- Online scoring catches regressions; quality gates block bad releases
- Loop: AI assistant analyzing traces and suggesting better prompts/scorers/datasets
- SDKs: Python, TypeScript, Go, Ruby, C#; wrappers for OpenAI Agents SDK, LangGraph, Mastra, Pydantic AI, LangChain, CrewAI, Vercel AI SDK (20+ frameworks)
- MCP server for IDE-driven observability
- SOC 2 Type II, GDPR, SSO, RBAC, HIPAA, hybrid deployment

**Pricing (2026)**:

| Tier | Price | Data | Scores | Retention |
|------|-------|------|--------|-----------|
| Starter | Free | 1 GB | 10,000 | 14 days |
| Pro | $249/month | 5 GB | 50,000 | 30 days |
| Enterprise | Custom | Custom | Custom | Custom |

No per-seat fees. Overages: $3/GB, $1.50/1K scores. AWS Marketplace tiers: $50K/$85K/$125K. ([Braintrust Pricing](https://www.braintrust.dev/pricing); [Cekura](https://www.cekura.ai/blogs/braintrust-pricing))

### 5.9 Opik (by Comet)

**License**: Apache 2.0 (full platform)
**GitHub Stars**: 20,000+
**Scale**: Handles 40M+ traces/day

**Key Features**:
- Complete tracing, evaluation, prompt optimization, guardrails, CI/CD integration
- Prompt optimization algorithms (automated variation testing)
- Cost Intelligence for coding agents (Claude Code, Codex)
- MCP server for IDE-driven workspace
- 50+ provider/framework integrations
- Self-hosting: full platform under Apache 2.0, no enterprise sales required

([Opik Docs](https://www.comet.com/docs/opik/); [GitHub](https://github.com/comet-ml/opik))

### 5.10 Portkey

**Type**: AI Gateway with observability
**Recognition**: Gartner Cool Vendor in LLM Observability (2025)

**Key Features**:
- Multi-provider routing, fallback, load balancing (OpenAI, Anthropic, Cohere, etc.)
- Unified logging across providers, users, workspaces
- Five observability pillars: Reliability, Quality, Safety, Cost, Governance
- OTel-based tracing
- Real-time monitoring and debugging

([Portkey](https://portkey.ai/features/observability); [Portkey Blog](https://portkey.ai/blog/the-complete-guide-to-llm-observability/))

### Platform Comparison Matrix

| Platform | License | Self-Host | OTel | Agent Tracing | Eval | Free Tier |
|----------|---------|-----------|------|--------------|------|-----------|
| Langfuse | MIT | Yes (Postgres+CH) | Yes | Yes | Yes | 50K units/mo |
| LangSmith | Proprietary | No | Yes | Yes (LangGraph native) | Yes | 5K traces/mo |
| Arize Phoenix | ELv2 | Yes (free) | Native | Yes | 50+ metrics | Unlimited self-hosted |
| Datadog | Proprietary | No | Yes | Yes | Custom judges | 40K spans/mo |
| Helicone | Apache 2.0 | Yes | Proxy | Sessions | No | 10K req/mo |
| W&B Weave | Open source* | Cloud | Secondary | Yes | Yes | Free tier |
| MLflow 3 | Apache 2.0 | Yes | Native | Yes | Yes | Free (unlimited) |
| Braintrust | Proprietary | No | Yes | Yes | Yes (eval-first) | 1GB data |
| Opik | Apache 2.0 | Yes | Yes | Yes | Yes | Free self-host |
| Portkey | Open source* | Yes | Yes | Via gateway | No | Free tier |

---

## 6. Agent-Specific Observability

### Why Agent Observability Is Different

Agents fail in ways that look like success -- well-formed but wrong outputs, redundant tool calls, semantically invalid actions. Traditional software fails loudly; agents fail quietly. Key failure modes: ([Braintrust, 2026](https://www.braintrust.dev/articles/agent-observability-complete-guide-2026))

- **Infinite loops**: Agent calls the same tool repeatedly with no progress
- **Context abandonment**: Agent forgets original user goal mid-task
- **Hallucinated tool arguments**: Agent invents parameters that don't exist
- **Silent retry loops**: Retries blend into normal traffic without detection
- **Wrong tool selection**: Agent picks a valid tool but not the right one for the context

### Four Pillars of Agent Telemetry

1. **Multi-step trace visualization**: Full span tree showing user input -> planning -> tool calls -> intermediate results -> final answer, with timing, token counts, and costs at each step
2. **Tool call tracking**: Each tool span records tool name, arguments, raw output, duration, retry count, error state
3. **Decision graph capture**: Which subagents, handoffs, or loop iterations ran, in what order, how often the agent looped
4. **Failure localization**: Pinpoint which step caused failure -- retrieval returned irrelevant docs, model hallucinated a parameter, or reasoning loop failed to converge

([LangChain Agent Observability](https://www.langchain.com/resources/agent-observability); [Groundcover](https://www.groundcover.com/learn/observability/ai-agent-observability))

### What Tool Spans Should Record

Each tool execution span must capture:
- Tool name and arguments
- Raw output
- Duration
- Retry count
- Error state
- Available tools list (what was offered to the model)
- Selected tool (which one it chose)

Without this data, hallucinated arguments and silent retry loops blend into normal traffic. ([Braintrust, 2026](https://www.braintrust.dev/articles/agent-observability-complete-guide-2026))

### Multi-Agent Tracing

For multi-agent systems, observability must:
- Track inter-agent handoffs with parent-child span relationships
- Map agent-to-agent and agent-to-tool flow as node-based graphs
- Detect recursive loops and repeated failures in trajectory
- Attribute cost and latency per sub-agent
- Arize Phoenix abstracts raw spans into agent graph visualizations for multi-agent debugging

### Cost and Latency Attribution per Agent Step

A critical use case: identifying that a specific sub-task consumes 80% of input/output tokens per trace or adds 3 seconds of tool call latency. Metrics like token usage, latency, and cost should be automatically aggregated at every level of the trace tree. ([W&B Weave](https://docs.wandb.ai/weave))

### Platform Support for Agent Observability

| Platform | Agent Graph Viz | Tool Call Analytics | Trajectory Analysis | Multi-Agent | Loop Detection |
|----------|----------------|--------------------|--------------------|-------------|----------------|
| LangSmith | Yes (LangGraph native) | Yes | Yes | Yes | Yes |
| Langfuse | Yes | Yes | Yes | Yes | Manual |
| Arize Phoenix | Yes (graph view) | Yes | Yes (trajectory mapping) | Yes | Yes |
| Datadog | Yes (execution flow chart, DASH 2025) | Yes | Yes | Yes | Yes |
| Braintrust | Yes | Yes | Yes | Yes | Via scorers |
| W&B Weave | Yes (trace trees) | Yes | Yes | Yes | Via signals |

---

## 7. RAG Observability

### What RAG Observability Covers

RAG observability captures traces and evaluations for the end-to-end RAG pipeline: query rewrite, embedding, vector search, reranking, generation, and grounding check. Each step is represented as a structured span with metadata: chunk IDs, similarity scores, document versions, latency. ([Coralogix](https://coralogix.com/guides/rag-observability/); [FutureAGI](https://futureagi.com/blog/what-is-rag-observability-2026))

### Why Standard Monitoring Misses RAG Failures

A hallucinated answer returns a healthy 200 status code with normal latency. RAG failures often start *before* generation:
- Irrelevant or stale chunks passed to the model
- Critical context that never reaches the model
- Model overweighting internal knowledge despite correct retrieved context

### Key RAG Metrics

**Retrieval Quality**:
- **Precision@K**: Fraction of retrieved documents that are relevant
- **Recall@K**: Fraction of all relevant documents that were retrieved
- **Hit Rate**: Whether at least one relevant document was retrieved
- **MRR** (Mean Reciprocal Rank): Position of first relevant result
- **Context Relevance**: LLM-as-a-judge scoring of each chunk's relevance to the query

**Generation Quality**:
- **Faithfulness / Groundedness**: Whether response only makes claims supported by retrieved documents (scores below 0.7 warrant alerting)
- **Answer Relevance**: Whether response actually addresses the query
- **Hallucination Rate**: Percentage of claims not grounded in context
- **Citation Correctness**: Whether citations point to supporting passages
- **Completeness**: Whether all key information from context was included

([Dextralabs, 2025](https://dextralabs.com/blog/production-rag-in-2025-evaluation-cicd-observability/); [Maxim AI](https://www.getmaxim.ai/articles/complete-guide-to-rag-evaluation-metrics-methods-and-best-practices-for-2025/))

### Embedding Drift

Embedding drift occurs when the semantic space of incoming documents shifts relative to the indexed corpus, causing retrieval degradation even when keywords remain similar.

**Detection Methods**:
- **Centroid Distance Monitoring**: Compute rolling centroid of new document embeddings (e.g., weekly batch) and measure cosine distance against historical corpus centroid. A sustained shift >0.05-0.10 in high-dimensional space signals distributional change.
- **Embedding Model Version Mismatch**: Partial re-indexing after model version changes leaves old and new vector representations mixed -- query latency and index size appear stable but retrieval quality degrades silently.
- **Arize Phoenix approach**: Computes Euclidean distance between embedding centroids across time windows -- deliberately simple, found more stable in production than complex alternatives.

([C# Corner](https://www.c-sharpcorner.com/article/detecting-embedding-drift-and-catalog-drift-in-financial-rag/); [Arize](https://arize.com/phoenix/))

### RAG Trace Structure

```
RAG Pipeline (root span)
  |-- query_rewrite
  |     |-- original_query: "What's our refund policy?"
  |     |-- rewritten_query: "company refund return policy terms"
  |-- embed_query
  |     |-- model: text-embedding-3-small
  |     |-- dimensions: 1536
  |-- vector_search
  |     |-- index: product_docs
  |     |-- top_k: 5
  |     |-- results: [{doc_id, score, chunk_text}...]
  |-- rerank
  |     |-- model: cohere-rerank-v3
  |     |-- top_k_after: 3
  |-- generate
  |     |-- model: gpt-4o
  |     |-- context_tokens: 2400
  |     |-- output_tokens: 180
  |-- grounding_check
        |-- faithfulness_score: 0.92
        |-- hallucination_detected: false
```

### Best Practices

1. Maintain a curated test set of **200+ Q&A pairs** with ground-truth answers; run nightly against the live index; track pass@5 and citation accuracy; trigger re-indexing on >3% drop.
2. Route low-faithfulness traces into annotation queues, label them, and feed back into eval datasets (trace-to-dataset feedback loop).
3. Track how chunking strategies affect latency, token usage, and hallucination rates.
4. Weekly quality reviews comparing current retrieval against baseline queries catch degradation before users notice.
5. Score faithfulness, context relevance, and citation correctness within minutes of trace ingestion to catch drift fast.

([Braintrust, 2026](https://www.braintrust.dev/articles/best-rag-evaluation-tools); [Dextralabs, 2025](https://dextralabs.com/blog/production-rag-in-2025-evaluation-cicd-observability/))

---

## 8. Cost Observability

### Why Per-Request Cost Attribution Matters

A provider invoice shows total spend but cannot identify whether a cost increase came from one customer's agent loop, a retry pattern during an upstream outage, or a prompt change adding tokens to every request. Without per-request breakdowns, product and engineering decisions are blind. ([Braintrust, 2026](https://www.braintrust.dev/articles/how-to-track-llm-costs-2026))

A single LLM request can range from **$0.0001 to $0.50** depending on model, input/output length, and reasoning tokens. Model API spending doubled from $3.5B to $8.4B between late 2024 and mid-2025.

### Cost Dimensions to Track

| Dimension | Why It Matters | Implementation |
|-----------|---------------|----------------|
| **Per user/customer** | Connects LLM usage to gross margin in B2B SaaS | `customer_id` on every span including sub-agent spans |
| **Per agent run** | Exposes runaway loops, excessive tool calls | Track median AND p99 cost by `agent_run_id` |
| **Per feature** | Identifies which features drive cost | Tag spans with `feature_name` |
| **Per model** | Tracks spending across model tiers | Automatic from `gen_ai.request.model` attribute |
| **Per environment** | Separates dev/staging/prod spend | `environment` tag |

**Example unit economics**: If average customer generates $12/month in LLM costs but pays $29/month, AI margin is thin. If one customer generates $200/month, the monthly invoice cannot surface this. ([Braintrust, 2026](https://www.braintrust.dev/articles/how-to-track-llm-costs-2026); [Traceloop](https://www.traceloop.com/blog/from-bills-to-budgets-how-to-track-llm-token-usage-and-cost-per-user))

### Budget Alerting Architecture

Three alert types:

1. **Hard caps per user**: Block or throttle requests when a single user crosses a configurable daily budget. Implement at the proxy/middleware layer -- blocking at the LLM call is too late (input tokens already billed).
2. **Soft alerts per feature**: Alert when cost per feature request moves above a rolling baseline. Catches prompt regressions, context bloat, retry patterns.
3. **Budget threshold alerts**: Alert when billing-period spend exceeds a fixed dollar amount. Set at 80% of expected monthly budget.

### Cost Tracking Platforms

| Platform | Approach | Coverage | Alerting |
|----------|----------|----------|----------|
| **LiteLLM** | Proxy, auto token-price mapping | 100+ providers | Max budget per key/user with enforcement |
| **Langfuse** | SDK, out-of-box cost tracking | All LLM calls in traces | Manual threshold setup |
| **Datadog** | APM extension | 800+ models, nanodollar precision | Native alerting integration |
| **Helicone** | Proxy gateway | Multi-provider | Dashboard-based |
| **Bifrost (Maxim)** | Open-source Go gateway | Core infrastructure | Gateway-level |
| **Traceloop** | OTel-based, attribute tagging | Custom attributes for user_id, feature | Via OTel backend |
| **Portkey** | AI gateway | Multi-provider | Real-time dashboard |
| **Opik** | SDK/platform | Cost Intelligence for coding agents | Real-time views |

([Maxim AI](https://www.getmaxim.ai/articles/best-llm-cost-tracking-tools-in-2026/); [LiteLLM Docs](https://docs.litellm.ai/docs/proxy/cost_tracking); [Langfuse](https://langfuse.com/docs/observability/features/token-and-cost-tracking))

### Best Practice: Gateway/Proxy Layer

The most effective approach is implementing cost tracking at the **gateway/proxy layer** where every LLM request passes through a single control point. This centralizes instrumentation and eliminates per-service tracking code.

### Organizational Framework

- One person owns the LLM cost budget and reports weekly
- Every LLM call tagged with team, feature, environment, user tier
- Monthly chargeback reports per team
- Spending limits enforced at the API layer -- not suggestions, but hard technical constraints returning errors or falling back to cheaper models

([Braintrust, 2026](https://www.braintrust.dev/articles/how-to-track-llm-costs-2026))

---

## 9. Production Monitoring Patterns

### The Three-Layer Monitoring Stack

Production AI monitoring requires three layers; no single tool covers everything: ([ValuestreamAI, 2026](https://valuestreamai.com/blog/ai-monitoring-in-production-guide-2026))

| Layer | What It Measures | Tools |
|-------|-----------------|-------|
| **Infrastructure** | CPU, GPU, memory, network, disk, KV cache utilization | Prometheus, Grafana, Datadog, CloudWatch |
| **LLM Telemetry** | Token usage, latency (TTFT/TPOT/E2E), cost, model versions, tool calls | Langfuse, LangSmith, Phoenix, Datadog LLM Obs |
| **Quality / Product** | Output correctness, faithfulness, user satisfaction, task completion | Online evals, user feedback, annotation queues |

**Key insight**: LLM-specific tools (Langfuse, LangSmith, Helicone) only cover the model layer. They cannot tell you what the infrastructure is doing or what users are doing in response. All three layers matter.

### LLM-Specific Monitoring Challenges

- **Non-deterministic latency**: Response time varies with prompt length, model version, provider load, and whether multi-step agent triggers sub-calls.
- **Token-based billing**: Unlike time/compute billing, cost attribution per team/feature is impossible without instrumentation.
- **Multi-step pipelines**: End-to-end latency is a composition of multiple spans -- retrieval, tools, safety filters, post-processing.
- **Concept drift**: Models trained through 2024 give less accurate answers about 2026 events; language and user expectations shift over time.
- **Silent failures**: Model can appear healthy on standard dashboards while consistently delivering bad answers.

### Production Monitoring Architecture

```
[User Request]
     |
[AI Gateway / Proxy]  -----> [Cost tracking, rate limiting]
     |
[Application Layer]
     |-- [Agent Orchestrator]  -----> [Traces to Langfuse/LangSmith]
     |     |-- [LLM Calls]    -----> [OTel spans with GenAI attributes]
     |     |-- [Tool Calls]   -----> [Tool execution spans]
     |     |-- [Retrieval]    -----> [Retrieval spans with scores]
     |
[Infrastructure]
     |-- [GPU/CPU]            -----> [Prometheus metrics]
     |-- [vLLM/TGI]          -----> [Inference metrics]
     |
[Quality Layer]
     |-- [Online Evals]       -----> [Sampled scoring on live traffic]
     |-- [User Feedback]      -----> [Thumbs up/down, ratings]
     |-- [Annotation Queues]  -----> [Human review of flagged traces]
```

### The Observability-as-Architecture Commitment

"It is an architectural commitment: to instrument pipelines before they go to production, to define quality SLOs alongside latency SLOs, to build feedback loops from production telemetry into evaluation pipelines, and to treat prompts as versioned artifacts with observable lifecycles." ([OpenSourceForU, 2026](https://www.opensourceforu.com/2026/08/from-black-box-to-glass-box-observability-strategies-for-production-ai/))

---

## 10. SLOs for AI Systems

### Why Traditional SLOs Are Insufficient

At minimum, a production LLM feature needs three classes of SLOs: **availability, latency, and quality**. Traditional SLOs only cover the first two.

An LLM application can meet 99.9% uptime and p99 < 2s latency while producing incorrect, harmful, or low-quality output. Quality SLOs must sit alongside operational SLOs. ([TechPreneur](https://techpreneurr.medium.com/from-prototype-to-24-7-a-deliverability-checklist-for-llm-features-slos-monitoring-drift-a23b078d5927))

### Six SLOs for AI Agents

Agents cannot be SLO'd with a single composite score. The recommended framework defines six independent metrics: ([FutureAGI, 2026](https://futureagi.com/blog/ai-agent-reliability-metrics-2026/))

| SLO | What It Measures | Why Single Score Hides It |
|-----|-----------------|--------------------------|
| **Task Completion Rate** | Fraction of tasks completed successfully | A composite score of 0.85 doesn't tell you completion vs. quality |
| **Tool-Call Success Rate** | Fraction of tool calls that succeed | 0.97 tool success can mask 0.62 argument extraction |
| **Recovery Rate** | Ability to recover from errors mid-task | Hidden by averaging with normal runs |
| **P99 Latency** | Tail latency for the worst 1% of requests | Averages hide the long tail |
| **Guardrail Trip Rate** | How often safety guardrails fire | Must be tracked independently from quality |
| **Trajectory Score** (4-D) | Trace-grounded quality across 4 dimensions | Needs trace-level evaluation, not aggregate |

**Example**: An aggregate `agent_score = 0.85` (dashboard green) hides that tool-call success is 0.97 but argument extraction is 0.62, and every refund over $1,000 goes out with the wrong tax line.

### Goodput as an SLO

Goodput measures requests per second meeting ALL SLOs simultaneously. A system processing 500 RPS with 30% exceeding TTFT SLO has goodput of only 350 RPS. This is the right operational metric because it aligns raw throughput with user-experienced quality.

### SLO Governance

Every SLO document needs: owner, technical reviewer, business approver, approval date, rationale, measurement definition, response policy, and next review date. Add model-risk, security, compliance, or DPO stakeholders. ([VDF.AI](https://vdf.ai/blog/on-prem-ai-agent-platform-slos/))

**Review cadence**:
- Monthly reviews until signal is trustworthy
- Tighten when users are harmed before the SLO triggers
- Relax when it creates pages without meaningful impact
- Move measurement closer to the user when component health disagrees with experienced outcome

### Practical SLO Examples

| SLO | Target | Measurement |
|-----|--------|-------------|
| Availability | 99.9% | HTTP 2xx / total requests |
| TTFT | p95 < 500ms | `gen_ai.client.operation.duration` first-token |
| E2E Latency | p95 < 5s | End-to-end request duration |
| Faithfulness | > 0.85 (mean, scored on 10% sample) | LLM-as-a-judge on production traces |
| Task Completion | > 90% | Agent-level success tracking |
| Cost per request | < $0.15 median | Token usage x model pricing |
| Guardrail trip rate | < 2% | Guardrail fire count / total requests |

---

## 11. Incident Response and On-Call for AI

### Why AI Incidents Need a New Playbook

AI incident management differs from traditional IT response because failures are **behavioral, not just infrastructural**. A model can drift, hallucinate, or misclassify while every infrastructure dashboard shows green. ([Glean, 2026](https://www.glean.com/perspectives/how-to-build-an-ai-incident-response-playbook-for-2026); [CSO Online](https://www.csoonline.com/article/4196303/ai-incidents-need-a-new-playbook-heres-how-to-build-one.html))

**Key statistics**:
- AI incidents surged **56.4%** from 2023 to 2024, reaching 233 documented cases
- Average AI incident detection time: **4.5 days**
- **67%** of AI incidents come from model errors, not adversarial attacks
- Teams improved at detecting incidents faster in H1 2026 vs. H2 2025 (hours vs. days), concentrated in teams with agent-specific observability

([Digital Applied, 2026](https://www.digitalapplied.com/blog/ai-incidents-h1-2026-retrospective-failure-modes-analysis))

### The LLM Incident Runbook: Six Steps, Four Classes

**Six steps**: Detect -> Triage -> Contain -> Evaluate -> Fix -> Review
**Four incident classes**: Hallucination, Jailbreak, Drift, PII Leak
**Loop closer**: Postmortem becomes a permanent test-set entry

The first call the on-call makes is **which class** the incident belongs to, because each class routes everything downstream differently. ([FutureAGI Substack](https://futureagi.substack.com/p/the-llm-incident-runbook-six-steps-f27))

### Per-Class Containment

| Class | Containment Action |
|-------|--------------------|
| **Hallucination** | Flip route to previous prompt version on previous model; tighten output-side groundedness checks |
| **Jailbreak** | Tighten inline input filters; add bypass template to adversarial test set |
| **Drift** | Flip to last known-good prompt-and-model pairing |
| **PII Leak** | Tighten output-side privacy checks to strict thresholds; enable per-tenant audit log; notify legal |

### Detection Signals

- **Rolling-mean rubric drift**: Sampled production traces scored against same rubrics that gate CI. A sustained 2-5 point drop over 15-60 minutes (per route, per prompt version) is the standard trigger.
- **For hallucination**: Groundedness score drops while retrieval rubrics hold steady -- context is correct but generator invents something. Usually mid-severity, escalating on regulated routes.
- **Token-spend anomaly detection**: Sudden spikes in per-request token usage
- **Trace-volume baselines**: Unusual patterns in agent step counts
- **Eval regression canaries**: Same eval suite running continuously

### Three Diagnostic Questions (In Order)

1. Did a version change on our side or the vendor's?
2. Did retrieval quality move?
3. Did the input distribution move?

Each answer points at a different owner and fix. Asking in a fixed order stops investigation from becoming debate. ([FutureAGI Substack](https://futureagi.substack.com/p/the-llm-incident-runbook-six-steps-f27))

### Pre-Positioned Response Capabilities

Maintain the ability to quickly:
- Adjust guardrail thresholds
- Swap model configurations
- Restrict tool access
- Roll back to previous model version without full deployment

These circuit-breaker capabilities enable rapid containment before root cause analysis is complete. ([AI Safety Directory](https://aisecurityandsafety.org/en/guides/ai-incident-response/))

### Relevant Standards

- **NIST SP 800-61r3** (April 2025): foundational incident response framework, now covering AI
- **MITRE ATLAS**: extends NIST to AI-specific threat vectors
- **OWASP GenAI Security Project**: open-source AIBOM generator (December 2025, CycloneDX format)

---

## 12. Dashboards, Alerting, and Infrastructure

### Grafana + Prometheus for LLM Inference

The most common open-source stack for LLM inference monitoring uses Prometheus for metric collection and Grafana for visualization: ([Grafana Blog](https://grafana.com/blog/ai-observability-llms-in-production/))

**Key metric taxonomy**:
- `llm_tokens_total{model, provider, direction}` -- token volume
- `llm_request_duration_seconds{model, endpoint}` -- latency
- `llm_cost_dollars{model, provider, endpoint}` -- cost attribution
- vLLM/TGI-specific metrics via `/metrics` endpoint

**Best practices**:
- Keep label cardinality low: model, endpoint, method (prefill/decode), status, instance
- Use SLOs with error budgets and burn-rate alerting
- Use Grafana Pyroscope for profiling CPU/memory hotspots and tail latency
- Use Grafana k6 for synthetic and load tests

### Grafana Cloud Agent Observability

Grafana Cloud provides:
- Built on OpenTelemetry for vendor-neutral instrumentation
- Managed OTLP gateway and Tempo trace backend
- Pre-built dashboards: GenAI observability, GenAI evaluations, vector database observability, MCP observability, GPU monitoring
- OpenLIT integration for instrumentation

([Grafana Docs](https://grafana.com/docs/grafana-cloud/machine-learning/agent-observability/))

### Architecture Pattern: OpenLIT + Grafana Cloud

```
[GenAI Service with OpenLIT instrumentation]
     |
     |-- (OTLP)
     v
[Grafana Cloud OTLP Gateway]
     |-- Tempo (traces)
     |-- Mimir (metrics)
     |-- Loki (logs)
     v
[Pre-built Grafana Dashboards]
```

### Purpose-Built LLM Dashboard Templates

StackPulsar offers production-ready Grafana dashboard templates specifically for LLM monitoring (importable in <30 minutes):
- Token throughput (input/output split)
- Latency breakdown (TTFT, TPOT, E2E)
- Cost attribution by model, provider, endpoint
- Quality signal panels

([StackPulsar](https://stackpulsar.com/blog/llm-monitoring-dashboard-templates/))

### Production Questions Your Dashboard Must Answer

1. How much is each model costing us?
2. Are we keeping latency within SLOs?
3. Are we returning hallucinations or toxic content?
4. Is the system vulnerable to prompt injection?
5. What saturates first -- GPU, KV cache, queue, or CPU tokenization?
6. Where is time spent -- queueing, batching, model execution, retrieval, safety filters?

### Alerting Patterns

| Alert | Trigger | Response |
|-------|---------|----------|
| TTFT SLO breach | p99 TTFT > threshold for 5min | Check queue depth, batch size, GPU utilization |
| Cost spike | Per-request cost > 2x baseline | Check prompt changes, retry loops, context bloat |
| Quality regression | Faithfulness score drop > 3% sustained | Run eval suite, check retrieval quality, model version |
| Token budget breach | 80% of monthly budget consumed | Throttle, switch to cheaper model, alert team lead |
| Error rate spike | Error rate > 5% for 3min | Check provider status, failover routing |
| Agent loop detection | Agent step count > threshold | Kill run, investigate trajectory |

---

## 13. The Observability-Evaluation Connection

### The Fundamental Gap

OpenTelemetry captures **what happened**. Evaluation measures **whether what happened was good**. This is the boundary between telemetry and evaluation -- and where most production AI architectures fall short. ([OpenTelemetry Blog, 2026](https://opentelemetry.io/blog/2026/genai-observability/))

### The Production-to-Eval Feedback Loop

The connection is bidirectional:

```
[Offline Experiments]  <----->  [Production Monitoring]
         |                            |
    Validates changes          Catches edge cases
    before deployment          dataset didn't cover
         |                            |
         v                            v
    [Evaluation Dataset] <---- [Failed Production Traces]
```

1. **Offline experiments** validate changes before deployment using curated datasets
2. **Online evaluation** (production scoring) catches edge cases the dataset didn't cover
3. Failed production traces are converted into eval cases
4. The eval suite grows from real user behavior
5. Future regressions are caught automatically
6. Same evaluation rubrics should be used in CI/CD gates AND production monitoring

([LangChain](https://www.langchain.com/articles/llm-monitoring-observability); [Langfuse, 2025](https://langfuse.com/blog/2025-11-12-evals))

### Online Evals on Production Traces

Online evals run on a **sampled subset** of live production traffic to provide real-time feedback on agent quality. Common scoring approaches:
- **LLM-as-a-judge**: Automated scoring of outputs for faithfulness, relevance, safety
- **Custom scorers**: Domain-specific Python functions
- **Rule-based assertions**: Regex, format checks, constraint validation
- **Human annotation**: Domain experts scoring traces via annotation queues

### Human-in-the-Loop

Many AI agent failures are judgment failures. Clinicians, lawyers, analysts, product managers, or support leads may be better positioned than engineers to judge output quality. Good observability gives reviewers a structured way to:
- Inspect examples in annotation queues
- Leave scored feedback against defined criteria
- Route judgment back into automated evaluators for calibration

### Wire CI and Production with Same Rubrics

"Wire TaskCompletion, LLMFunctionCalling, Groundedness, and 4-D TrajectoryScore into a pytest fixture via the ai-evaluation SDK, then attach the same templates as EvalTag scorers when production traces reveal issues the CI gate missed. Same rubric in both places is the diff between an offline pass that ships and a 3 AM page." ([FutureAGI, 2026](https://futureagi.com/blog/ai-agent-reliability-metrics-2026/))

### CHI 2025 Research

A CHI 2025 study on LLM observability design principles with 30 developers identified four pillars: **Awareness, Monitoring, Intervention, and Operability** -- all assuming evaluation depth, not just trace logging. ([Braintrust, 2026](https://www.braintrust.dev/articles/llm-observability-guide))

### Connection to Module 12 (Evaluation)

- Observability provides the raw data (traces, spans, metadata) that evaluation consumes
- Production traces become evaluation datasets through annotation
- Evaluation scores become observability metrics through online scoring
- Both share rubrics and scoring functions for consistency
- CI/CD evaluation gates prevent deployment; production evaluation catches post-deployment regressions
- The observability platform is where evaluation results are visualized, trended, and alerted on

---

## 14. Integration with Security (Module 13)

### Observability as Security Infrastructure

Observability and security share critical infrastructure:

1. **Prompt injection detection**: Observability traces capture input content that can be scored for injection patterns. Gateway-layer scanning (Helicone, Portkey) provides inline detection. ([Helicone](https://www.helicone.ai/))

2. **PII detection and redaction**: Content capture must be governed. OTel conventions treat content as opt-in. The Collector processor enforces that no content attribute leaves a boundary. Langfuse, Datadog, and W&B Weave all offer sensitive data scanning/redaction.

3. **Audit trails**: Every LLM interaction traced with full provenance (who, what, when, which model, what input, what output). SOC 2, HIPAA, GDPR compliance requires this level of traceability.

4. **Anomaly detection**: Token-spend anomalies, unusual trace patterns, and behavioral drift can indicate both quality issues and security threats (jailbreaks, data exfiltration attempts).

5. **Content filtering**: Gateway tools can moderate content inline before responses reach users.

### Security-Relevant Observability Signals

| Signal | Security Implication |
|--------|---------------------|
| Sudden prompt length increase | Possible injection attack |
| New tool call patterns | Possible privilege escalation |
| Output contains PII | Data leak risk |
| Faithfulness score drop | Possible model manipulation |
| Unusual token spending | Possible abuse or exfiltration |
| Guardrail trip rate spike | Active attack or policy violation |

### Compliance Framework Support

- **NIST SP 800-61r3** (April 2025): Incident response including AI-specific vectors
- **MITRE ATLAS**: AI threat taxonomy integrated with observability
- **OWASP GenAI Security Project**: AIBOM (AI Bill of Materials) generator
- **SOC 2 / HIPAA / GDPR**: All major platforms (Langfuse, Braintrust, Datadog, Helicone) offer compliance certifications

---

## 15. Interview Preparation

### Key Concepts to Articulate

1. **The three-layer stack**: Infrastructure (Prometheus/Grafana) + LLM Telemetry (Langfuse/LangSmith) + Quality (online evals/human feedback). No single tool covers everything.

2. **OTel GenAI conventions**: Vendor-neutral standard (`gen_ai.*` attributes, `invoke_agent`/`chat`/`execute_tool` span types, `gen_ai.client.operation.duration`/`gen_ai.client.token.usage` metrics). Pre-stable but core concepts settled. Make OTel support a hard buying requirement.

3. **Traces vs. logs vs. metrics**: Traces = span trees capturing full agent trajectory; Metrics = pre-aggregated numbers (latency, tokens, cost); Logs/Events = content capture (prompts, completions, tool args -- opt-in for privacy).

4. **Agent observability is different**: Non-deterministic, fails silently, needs trajectory analysis not just per-call logging. Six independent SLOs, not one composite score.

5. **The observability-eval loop**: Production traces feed evaluation datasets; eval scores become monitoring signals; same rubrics in CI and production.

6. **Cost attribution**: Per-user, per-feature, per-agent-run. Gateway/proxy layer is the best enforcement point. Hard caps, soft alerts, budget thresholds.

7. **RAG observability**: Retrieval quality (precision@K, MRR), embedding drift (centroid distance monitoring), faithfulness scoring on every response.

### System Design Scenarios

**"Design an observability stack for a production AI agent system"**:
- Gateway layer (Portkey/LiteLLM): cost tracking, rate limiting, routing
- OTel instrumentation: GenAI semantic conventions for traces
- Langfuse/Phoenix: trace storage, visualization, evaluation
- Prometheus/Grafana: infrastructure metrics, LLM inference metrics, dashboards
- Online evals: LLM-as-a-judge on sampled production traffic
- Annotation queues: human review of low-quality traces
- Alerting: SLO-based with burn-rate alerting via Alertmanager
- Incident runbook: Six steps, four classes, postmortem-to-test-set loop

**"How would you detect and respond to a hallucination increase in production?"**:
1. Detection: Rolling-mean faithfulness score drops while retrieval quality holds steady
2. Triage: Check if model version, prompt, or retrieval changed
3. Contain: Flip to previous prompt version; tighten groundedness checks
4. Evaluate: Run full eval suite against current vs. previous configuration
5. Fix: Identify root cause (prompt regression, context bloat, model drift)
6. Review: Add failing cases to eval dataset; postmortem

### Common Interview Questions

1. How do you monitor LLM quality in production? (Three-layer stack + online evals)
2. What's the difference between LLM monitoring and traditional APM? (Non-deterministic, semantic quality, token-based costs)
3. How do you track costs across teams/features? (Per-request attribution via span tagging + gateway enforcement)
4. What SLOs would you set for an AI agent? (Six independent metrics: completion, tool success, recovery, p99 latency, guardrail rate, trajectory score)
5. How does observability connect to evaluation? (Bidirectional feedback loop, same rubrics in CI and production)
6. What are the OpenTelemetry GenAI semantic conventions? (Vendor-neutral spans/metrics/events for LLM calls, tool execution, agent invocation)
7. How do you handle embedding drift? (Centroid distance monitoring, re-indexing triggers, nightly test set runs)
8. Design an incident response plan for AI systems. (Six steps, four classes, pre-positioned circuit breakers)

---

## Sources

### OpenTelemetry GenAI Conventions
1. [Inside the LLM Call: GenAI Observability with OpenTelemetry](https://opentelemetry.io/blog/2026/genai-observability/) -- OpenTelemetry Blog, 2026
2. [AI Agent Observability - Evolving Standards](https://opentelemetry.io/blog/2025/ai-agent-observability/) -- OpenTelemetry Blog, 2025
3. [OpenTelemetry GenAI Semantic Conventions (MLflow)](https://mlflow.org/docs/latest/genai/tracing/opentelemetry/genai-semconv/) -- MLflow Docs
4. [How OpenTelemetry Traces LLM Calls, Agent Reasoning, and MCP Tools](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions) -- Greptime, 2026
5. [OpenTelemetry GenAI Semantic Conventions Implementation Guide](https://hidekazu-konishi.com/entry/opentelemetry_genai_semantic_conventions_guide.html) -- Hidekazu Konishi
6. [OpenTelemetry GenAI Agent Spans Spec](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md) -- GitHub
7. [OpenTelemetry GenAI Metrics Spec](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md) -- GitHub
8. [OpenTelemetry for AI Agents (Zylos Research)](https://zylos.ai/research/2026-02-28-opentelemetry-ai-agent-observability/) -- 2026
9. [OTel GenAI SemConv Cheat Sheet](https://techbytes.app/posts/opentelemetry-genai-agent-semconv-cheat-sheet-2026/) -- TechBytes, 2026
10. [OpenTelemetry for AI Observability: What It Covers and Where It Stops](https://www.fiddler.ai/blog/opentelemetry-ai-observability-guide) -- Fiddler AI
11. [OpenTelemetry for GenAI and the OpenLLMetry project](https://horovits.medium.com/opentelemetry-for-genai-and-the-openllmetry-project-81b9cea6a771) -- Dotan Horovits, Medium
12. [OpenTelemetry for Generative AI](https://opentelemetry.io/blog/2024/otel-generative-ai/) -- OpenTelemetry Blog, 2024
13. [OpenTelemetry GenAI Span Types for Agents](https://mortalapps.com/agents/production-engineering/opentelemetry-span-types-for-agents/) -- MortalApps
14. [OpenTelemetry for LLMs: Complete SRE Guide for 2026](https://openobserve.ai/blog/opentelemetry-for-llms/) -- OpenObserve
15. [OpenTelemetry GenAI Semantic Conventions (DEV)](https://dev.to/x4nent/opentelemetry-genai-semantic-conventions-the-standard-for-llm-observability-1o2a) -- DEV Community

### Observability Platforms
16. [Top LLM Observability and Evaluation Platforms in 2026](https://www.marktechpost.com/2026/08/09/top-llm-observability-and-evaluation-platforms-in-2026-langfuse-langsmith-braintrust-arize-and-more-compared/) -- MarkTechPost
17. [Best LLM Observability Tools in 2026](https://www.firecrawl.dev/blog/best-llm-observability-tools) -- Firecrawl
18. [Top 5 LLM and Agent Observability Tools in 2026](https://mlflow.org/top-5-agent-observability-tools/) -- MLflow
19. [Langfuse vs Arize AI and Phoenix](https://langfuse.com/resources/engineering/best-phoenix-arize-alternatives) -- Langfuse
20. [ClickHouse welcomes Langfuse](https://clickhouse.com/blog/clickhouse-acquires-langfuse-open-source-llm-observability) -- ClickHouse Blog
21. [Langfuse joins ClickHouse](https://langfuse.com/blog/joining-clickhouse) -- Langfuse Blog
22. [Langfuse Pricing 2026](https://coverge.ai/blog/langfuse-pricing) -- Coverge
23. [LangSmith Pricing Guide 2026](https://www.metacto.com/blogs/the-true-cost-of-langsmith-a-comprehensive-pricing-integration-guide) -- MetaCTO
24. [LangSmith Plans and Pricing](https://www.langchain.com/pricing) -- LangChain
25. [Datadog LLM Observability Examples, Pricing](https://cubeapm.com/faqs/datadog-llm-observability/) -- CubeAPM
26. [Datadog LLM Observability Pricing 2026](https://ecorpit.com/datadog-llm-observability-pricing-cap-costs-2026/) -- Ecorpit
27. [Datadog Pricing](https://www.datadoghq.com/pricing/) -- Datadog
28. [Datadog Agent Observability](https://www.datadoghq.com/products/ai/agent-observability/) -- Datadog
29. [Helicone GitHub](https://github.com/helicone/helicone) -- GitHub
30. [Helicone](https://www.helicone.ai/) -- Helicone Website
31. [Helicone Review: Now in Maintenance Mode](https://chatforest.com/reviews/helicone-llm-observability-gateway/) -- ChatForest
32. [W&B Weave Documentation](https://docs.wandb.ai/weave) -- Weights & Biases
33. [W&B Weave for Production Agents](https://wandb.ai/site/weave/) -- W&B Site
34. [MLflow 3.0: Build, Evaluate, and Deploy GenAI](https://www.databricks.com/blog/mlflow-30-unified-ai-experimentation-observability-and-governance) -- Databricks
35. [MLflow Tracing for LLM Observability](https://mlflow.org/docs/latest/genai/tracing/) -- MLflow Docs
36. [Braintrust Pricing](https://www.braintrust.dev/pricing) -- Braintrust
37. [Braintrust Review 2026](https://aitoolsbakery.com/blog/braintrust-review/) -- AI Tools Bakery
38. [Arize Phoenix](https://arize.com/phoenix/) -- Arize AI
39. [What Is Arize AI? Pricing and Alternatives](https://www.voiceflow.com/blog/what-is-arize-ai) -- Voiceflow
40. [Opik by Comet](https://www.comet.com/site/products/opik/) -- Comet
41. [Opik GitHub](https://github.com/comet-ml/opik) -- GitHub
42. [Portkey Observability](https://portkey.ai/features/observability) -- Portkey
43. [OpenLLMetry GitHub](https://github.com/traceloop/openllmetry) -- Traceloop

### Agent Observability
44. [Agent Observability: Complete Guide for 2026](https://www.braintrust.dev/articles/agent-observability-complete-guide-2026) -- Braintrust
45. [AI Agent Observability (LangChain)](https://www.langchain.com/resources/agent-observability) -- LangChain
46. [AI Agent Observability Guide](https://www.groundcover.com/learn/observability/ai-agent-observability) -- Groundcover
47. [15 AI Agent Observability Tools](https://aimultiple.com/agentic-monitoring) -- AIMultiple

### RAG Observability
48. [RAG Observability: Trace Retrieval to Generation Quality](https://coralogix.com/guides/rag-observability/) -- Coralogix
49. [Production RAG in 2025: Evaluation, CI/CD, Observability](https://dextralabs.com/blog/production-rag-in-2025-evaluation-cicd-observability/) -- Dextralabs
50. [Complete Guide to RAG Evaluation](https://www.getmaxim.ai/articles/complete-guide-to-rag-evaluation-metrics-methods-and-best-practices-for-2025/) -- Maxim AI
51. [What Is RAG Observability? 2026](https://futureagi.com/blog/what-is-rag-observability-2026) -- FutureAGI
52. [Detecting Embedding Drift in Financial RAG](https://www.c-sharpcorner.com/article/detecting-embedding-drift-and-catalog-drift-in-financial-rag/) -- C# Corner

### Cost Tracking
53. [How to Track LLM Costs 2026](https://www.braintrust.dev/articles/how-to-track-llm-costs-2026) -- Braintrust
54. [From Bills to Budgets: LLM Token Usage and Cost](https://www.traceloop.com/blog/from-bills-to-budgets-how-to-track-llm-token-usage-and-cost-per-user) -- Traceloop
55. [Best LLM Cost Tracking Tools 2026](https://www.getmaxim.ai/articles/best-llm-cost-tracking-tools-in-2026/) -- Maxim AI
56. [LiteLLM Spend Tracking](https://docs.litellm.ai/docs/proxy/cost_tracking) -- LiteLLM Docs
57. [Langfuse Token and Cost Tracking](https://langfuse.com/docs/observability/features/token-and-cost-tracking) -- Langfuse Docs

### Production Monitoring and SLOs
58. [AI Monitoring in Production 2026](https://valuestreamai.com/blog/ai-monitoring-in-production-guide-2026) -- ValuestreamAI
59. [AI Agent Reliability Metrics 2026: Six SLOs](https://futureagi.com/blog/ai-agent-reliability-metrics-2026/) -- FutureAGI
60. [From Prototype to 24/7: Deliverability Checklist](https://techpreneurr.medium.com/from-prototype-to-24-7-a-deliverability-checklist-for-llm-features-slos-monitoring-drift-a23b078d5927) -- TechPreneur, Medium
61. [SLOs for On-Premises AI Agent Platforms](https://vdf.ai/blog/on-prem-ai-agent-platform-slos/) -- VDF.AI
62. [LLM Monitoring Best Practices 2026](https://openobserve.ai/blog/llm-monitoring-best-practices/) -- OpenObserve
63. [From Black Box to Glass Box: Observability Strategies](https://www.opensourceforu.com/2026/08/from-black-box-to-glass-box-observability-strategies-for-production-ai/) -- OpenSourceForU

### Incident Response
64. [The LLM Incident Response Runbook 2026](https://futureagi.substack.com/p/the-llm-incident-runbook-six-steps-f27) -- FutureAGI
65. [AI Incident Response Tools 2026](https://galileo.ai/blog/ai-incident-response-tools) -- Galileo
66. [AI Incidents H1 2026 Retrospective](https://www.digitalapplied.com/blog/ai-incidents-h1-2026-retrospective-failure-modes-analysis) -- Digital Applied
67. [How to Build an AI Incident Response Playbook](https://www.glean.com/perspectives/how-to-build-an-ai-incident-response-playbook-for-2026) -- Glean
68. [AI Incident Response Planning and Playbooks](https://aisecurityandsafety.org/en/guides/ai-incident-response/) -- AI Safety Directory
69. [AI Incidents Need a New Playbook](https://www.csoonline.com/article/4196303/ai-incidents-need-a-new-playbook-heres-how-to-build-one.html) -- CSO Online
70. [Hallucination Detection in Production AI Agents](https://noveum.ai/en/blog/hallucination-detection-production-ai-agents) -- Noveum

### Dashboards and Infrastructure
71. [Monitor LLMs in Production with Grafana Cloud, OpenLIT, and OTel](https://grafana.com/blog/ai-observability-llms-in-production/) -- Grafana Labs
72. [Grafana Agent Observability](https://grafana.com/docs/grafana-cloud/machine-learning/agent-observability/) -- Grafana Docs
73. [Monitor LLM Inference: Prometheus & Grafana for vLLM, TGI, llama.cpp](https://www.glukhov.org/observability/monitoring-llm-inference-prometheus-grafana/) -- Rost Glukhov
74. [LLM Monitoring Dashboard Templates: Grafana + Prometheus](https://stackpulsar.com/blog/llm-monitoring-dashboard-templates/) -- StackPulsar
75. [Monitoring LLM Systems: Metrics, Logging, Alerting, Dashboards](https://mbrenndoerfer.com/writing/monitoring-metrics-alerting-logging-dashboards-llm-production) -- Michael Brenndoerfer

### LLM Inference Metrics
76. [NVIDIA NIM Benchmarking Metrics](https://docs.nvidia.com/nim/benchmarking/llm/latest/metrics.html) -- NVIDIA
77. [Understand LLM Latency and Throughput Metrics](https://docs.anyscale.com/llm/serving/benchmarking/metrics) -- Anyscale
78. [Key Metrics for LLM Inference](https://bentoml.com/llm/inference-optimization/llm-inference-metrics) -- BentoML
79. [LLM Latency Decomposition: TTFT vs Throughput](https://tianpan.co/blog/2026-03-10-llm-latency-decomposition-ttft-vs-throughput) -- TianPan.co
80. [vLLM Metrics](https://docs.vllm.ai/en/stable/design/metrics/) -- vLLM Docs
81. [Benchmarking LLM Inference: Metrics That Matter](https://www.roeybc.com/blog/llm_inference_benchmark) -- Roey BC

### Market Data
82. [LLM Observability Platform Market Report 2026](https://www.researchandmarkets.com/reports/6215671/large-language-model-llm-observability) -- Research and Markets
83. [LLM Observability Platform Market Size (CAGR 31.8%)](https://market.us/report/llm-observability-platform-market/) -- Market.us
84. [LLM Observability Platform Market 2034](https://dataintelo.com/report/llm-observability-platform-market) -- DataIntelo
85. [Agentic AI Monitoring Market](https://www.mordorintelligence.com/industry-reports/agentic-artificial-intelligence-monitoring-analytics-and-observability-tools-market) -- Mordor Intelligence
86. [LLM Observability Platform Market to Grow at 36.3% CAGR](https://natlawreview.com/press-releases/large-language-model-llm-observability-platform-market-grow-363-cagr-2025) -- National Law Review

### Observability-Evaluation Connection
87. [Why LLM Observability Needs Evaluations](https://www.langchain.com/articles/llm-monitoring-observability) -- LangChain
88. [LLM Evaluation: Methods, Best Practices](https://langfuse.com/blog/2025-11-12-evals) -- Langfuse
89. [LLM Observability Guide](https://www.braintrust.dev/articles/llm-observability-guide) -- Braintrust
90. [LLM Observability Platform Guide](https://mastra.ai/articles/llm-observability-platform) -- Mastra
