# Module 16: Production — Deploying and Operating AI Systems at Scale

**Scope**: CI/CD for AI (prompt versioning, model versioning, five-gate regression pipeline), deployment patterns (shadow mode, canary, blue-green), reliability engineering (SLOs for AI, error budgets, graceful degradation), scaling patterns (Kubernetes + vLLM + KServe, auto-scaling, queue-based), data pipeline management (feature stores, vector DB ops, embedding refresh), configuration management (prompt registries, feature flags), incident management (runbooks, rollback, post-mortems), and compliance/audit (EU AI Act, NIST AI RMF, ISO 42001, model cards, data lineage).
**Prerequisite**: Module 12 (Evaluation), Module 14 (Observability), Module 15 (Inference & Optimization).
**Last updated**: 2026-08-21 | **Sources consulted**: 100

---

## 1. System Topology & Data Flow

### 1.1 Architecture Diagram

```
 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                    CONTROL PLANE                                           │
 │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
 │  │  Release Manager │  │  SLO Engine      │  │  Config          │  │  Compliance      │  │
 │  │  - 5-gate CI/CD  │  │  - Judgment SLOs │  │  Controller      │  │  Engine          │  │
 │  │  - Canary %      │  │  - Error budgets │  │  - Prompt        │  │  - EU AI Act     │  │
 │  │  - Shadow mode   │  │  - Compound fail │  │    registry      │  │  - NIST RMF      │  │
 │  │  - Auto-rollback │  │    rate tracking │  │  - Feature flags │  │  - ISO 42001     │  │
 │  │  - Blue-green    │  │  - Goodput       │  │  - Model config  │  │  - Audit trail   │  │
 │  │    swap          │  │                  │  │  - Rollback <60s │  │  - Model cards   │  │
 │  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
 └───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┘
             │                    │                    │                    │
 ┌───────────┼────────────────────┼────────────────────┼────────────────────┼─────────────────┐
 │           ▼                    ▼                    ▼                    ▼                  │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                          DATA PLANE: PRODUCTION AI SERVING                         │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  DEPLOYMENT LAYER                                                       │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                  │      │    │
 │  │  │  │ Blue/Green   │  │ Canary       │  │ Shadow Mode  │                  │      │    │
 │  │  │  │ Environments │  │ Router       │  │ Evaluator    │                  │      │    │
 │  │  │  │ - GPU warm-up│  │ - Session    │  │ - Replay     │                  │      │    │
 │  │  │  │ - Instant    │  │   hashing    │  │   prod logs  │                  │      │    │
 │  │  │  │   swap       │  │ - 1%→5%→20% │  │ - LLM judge  │                  │      │    │
 │  │  │  │ - Bake test  │  │   →50%→100% │  │ - No user    │                  │      │    │
 │  │  │  │              │  │ - 24h soak   │  │   exposure   │                  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘                  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  INFERENCE LAYER (Kubernetes + vLLM + KServe)                           │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ vLLM Engine  │  │ KServe       │  │ llm-d        │  │ Gateway API│  │      │    │
 │  │  │  │ - Paged Attn │  │ - Auto-scale │  │ - Disagg.    │  │ Inference  │  │      │    │
 │  │  │  │ - Continuous │  │ - Scale-to-0 │  │   prefill/   │  │ Extension  │  │      │    │
 │  │  │  │   batching   │  │ - Model      │  │   decode     │  │ - Model-   │  │      │    │
 │  │  │  │ - Spec decode│  │   versions   │  │ - 3× output  │  │   aware    │  │      │    │
 │  │  │  │ - FP8 quant  │  │              │  │   tok/s gain │  │   routing  │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  │                                                                                    │    │
 │  │  ┌──────────────────────────────────────────────────────────────────────────┐      │    │
 │  │  │  DATA PIPELINE LAYER                                                    │      │    │
 │  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │      │    │
 │  │  │  │ Feature Store│  │ Vector DB    │  │ Embedding    │  │ RAG        │  │      │    │
 │  │  │  │ (Feast/Tecton│  │ (pgvector/   │  │ Refresh      │  │ Pipeline   │  │      │    │
 │  │  │  │  /Databricks)│  │  Pinecone/   │  │ - Versioned  │  │ - Chunk    │  │      │    │
 │  │  │  │ - Train/serve│  │  Qdrant)     │  │ - Idempotent │  │   strategy │  │      │    │
 │  │  │  │   parity     │  │ - Index ops  │  │ - Hot-swap   │  │ - Quality  │  │      │    │
 │  │  │  │ - Drift mon. │  │ - Scale ops  │  │   backends   │  │   CI gate  │  │      │    │
 │  │  │  └──────────────┘  └──────────────┘  └──────────────┘  └────────────┘  │      │    │
 │  │  └──────────────────────────────────────────────────────────────────────────┘      │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                            │
 │  ┌────────────────────────────────────────────────────────────────────────────────────┐    │
 │  │                         TOOL PROXY LAYER                                           │    │
 │  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐       │    │
 │  │  │ AI Gateway    │  │ CI/CD         │  │ Eval Runner   │  │ Incident      │       │    │
 │  │  │ (LiteLLM/     │  │ Orchestrator  │  │ - Offline eval│  │ Router        │       │    │
 │  │  │  Portkey)     │  │ - GitHub      │  │ - Shadow eval │  │ - 4 incident  │       │    │
 │  │  │ - Multi-model │  │   Actions     │  │ - Canary eval │  │   classes     │       │    │
 │  │  │ - Failover    │  │ - Langfuse    │  │ - LLM judge  │  │ - 6-step      │       │    │
 │  │  │ - Cost track  │  │   experiment  │  │ - Regression  │  │   runbook     │       │    │
 │  │  │ - Rate limit  │  │   gate        │  │   reports     │  │ - Rollback    │       │    │
 │  │  └───────────────┘  └───────────────┘  └───────────────┘  └───────────────┘       │    │
 │  └────────────────────────────────────────────────────────────────────────────────────┘    │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  PERSISTENCE LAYER                                         │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Prompt Registry   │  │ Model Registry    │  │ Eval Store        │  │ Audit Trail    │  │
 │  │ - Immutable       │  │ (MLflow 3.0)      │  │ - Test datasets   │  │ - Model cards  │  │
 │  │   versions        │  │ - LoggedModel     │  │   (versioned)     │  │ - Data lineage │  │
 │  │ - Env pinning     │  │ - Git commit link │  │ - Quality scores  │  │ - Inference    │  │
 │  │ - Rollback ptrs   │  │ - Adapter weights │  │ - Drift baselines │  │   logs         │  │
 │  │ - Semantic diffs  │  │ - 3 versions min  │  │ - Judge traces    │  │ - Human review │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                  TELEMETRY PLANE                                           │
 │  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────┐  │
 │  │ Reliability       │  │ Quality           │  │ Cost & Scaling    │  │ Compliance     │  │
 │  │ - TTFT/TPOT       │  │ - Hallucination % │  │ - $/request       │  │ - Audit log    │  │
 │  │ - Error rate (5%) │  │ - Coherence score │  │ - Tokens/request  │  │   completeness │  │
 │  │ - Error budget    │  │ - Task completion │  │ - GPU utilization  │  │ - Model card   │  │
 │  │   burn rate       │  │ - Drift magnitude │  │ - Queue depth     │  │   freshness    │  │
 │  │ - Circuit breaker │  │ - Eval regression │  │ - Auto-scale evts │  │ - Incident     │  │
 │  │   state           │  │                   │  │                   │  │   tracking     │  │
 │  └───────────────────┘  └───────────────────┘  └───────────────────┘  └────────────────┘  │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narrative

**Step 1 — CI/CD Gate**: A prompt or model change enters the **five-gate pipeline**: lint → offline eval → cost budget → shadow eval → canary. Each gate must pass before promotion. The **Release Manager** controls canary percentage (1%→5%→20%→50%→100%) with 24h soak at each stage.

**Step 2 — Deployment Routing**: The **Canary Router** uses session-level hashing to pin users to a consistent version (no mid-conversation model switching). Shadow mode replays production traffic through the candidate model without user exposure. Blue-green environments enable instant swap with pre-warmed GPUs.

**Step 3 — Inference Execution**: Requests flow through the **Kubernetes inference stack**: Gateway API Inference Extension (model-aware routing, KV-cache-aware scheduling) → KServe (auto-scaling, scale-to-zero) → vLLM (PagedAttention, continuous batching, speculative decode). For models >13B at high concurrency, llm-d provides disaggregated prefill/decode.

**Step 4 — Data Pipeline**: The **Feature Store** ensures training-serving parity. **Vector DB** serves embeddings with index versioning. **Embedding Refresh** pipelines run idempotently with hot-swappable backends. RAG quality is validated in CI before merge.

**Step 5 — SLO Evaluation**: The **SLO Engine** evaluates six SLO categories: availability (99.9%), latency (TTFT <500ms p50), quality (<2% hallucination), safety (<0.1% toxicity), cost (<$0.05/request), and behavioral scope (100% within-scope). Error budgets track compound failure rates across multi-step agents.

**Step 6 — Incident Response**: On SLO breach, the **Incident Router** classifies into four classes (hallucination, jailbreak, drift, PII leak) and executes the 6-step runbook (detect→triage→contain→evaluate→fix→review). Rollback is a version pointer change in the prompt registry (<60s). The **Compliance Engine** ensures every action is logged to the immutable audit trail.

---

## 2. Core Mechanics & Algorithms

### 2.1 CI/CD: The Five-Gate Pipeline

| Gate | What It Checks | Tool | Auto-Pass Criteria |
|------|---------------|------|-------------------|
| **1. Lint** | Schema validation, variable bindings, forbidden patterns | Custom + Promptfoo | All checks pass |
| **2. Offline Eval** | Held-out test dataset quality scores | Langfuse experiment-action, Braintrust | Score ≥ baseline − threshold |
| **3. Cost Budget** | Token usage, API cost within limits | Gateway metrics | Cost ≤ 110% of baseline |
| **4. Shadow Eval** | Replay recent prod traffic; LLM-judge comparison | Shadow mode + LLM judge | No regression > 5% |
| **5. Canary** | Live traffic quality, latency, cost, safety | Canary router + online eval | 24h soak, all metrics within SLO |

**Key context**: OpenAI acquired Promptfoo (March 2026, $86M) — 150K+ developers, 25% of Fortune 500. Evaluation is now core infrastructure, not optional tooling.

**Prompt versioning principles**: Immutable versions (once published, never modified), environment pinning (dev/staging/prod each pin to a specific version), semantic diffing (PRs show prompt diffs with predicted impact), promotion gates (automated eval must pass before promotion).

**Model versioning** (MLflow 3.0): `LoggedModel` entity links each version to Git commit, prompt configs, traces, and eval runs. Artifact store retains minimum last three validated versions. 30M+ monthly downloads.

### 2.2 Deployment Patterns

| Pattern | Risk Level | Cost Overhead | Time to Signal | Best For |
|---------|:----------:|:------------:|:--------------:|---------|
| **Shadow mode** | Lowest | 2× inference | Hours-days | Major model/prompt changes; 67% enterprise adoption |
| **Canary** | Low-Medium | +1-5% traffic | 24h+ per stage | Incremental improvements |
| **Blue-green** | Medium | 2× GPU infra | Minutes (swap) | Model version switches; long warm-up models |
| **A/B test** | Medium | +10-50% traffic | Days-weeks | Feature comparison with statistical significance |

**The deployment funnel** (recommended sequence):
```
Offline Evals → Shadow Mode → Canary (1%→5%→20%→50%→100%) → Full Promotion + Continuous Monitoring
```

**AI-specific canary triggers for rollback**:
- Error rate increase >1 percentage point
- P99 latency increase >20%
- Automated eval score decrease >5%
- Coherence score drop >0.1
- Any toxicity increase

**Session pinning**: Active sessions must be pinned to the current version. Only new sessions route to the updated version. Prevents incoherent mid-conversation behavior.

### 2.3 SLOs for AI Systems

| SLO Category | Metric | Target | Why Traditional SRE Misses It |
|-------------|--------|--------|------------------------------|
| **Availability** | Uptime, success rate | 99.9% | Same as traditional — but 200 OK with hallucination is "available" |
| **Latency** | TTFT p50/p99 | <500ms / <2s | LLM tail latency 10× worse than p50 |
| **Quality** | Hallucination rate, coherence, task completion | <2% hallucination, >0.85 coherence | No signal in HTTP metrics — need LLM-judge |
| **Safety** | Toxicity rate, PII leak rate, jailbreak rate | <0.1% toxicity, 0% PII leaks | Requires content analysis, not status codes |
| **Cost** | $/request, tokens/request | <$0.05 median | Cost is per-request variable, not per-instance |
| **Behavioral scope** | Actions within authorized boundaries | 100% in-scope | Agent autonomy creates new failure modes |

**Compound failure rates**: 95% accuracy per step across 5 sequential steps → 0.95^5 = 77.4% end-to-end. Per-step SLOs must be higher than end-to-end target. Teams need parallel error budgets — accuracy and latency budgets burn independently.

**Production failure data** (Datadog 2026): 5% of all LLM call spans report errors. 60% of errors are rate limits (429). Token usage per request doubled YoY. 69% of companies use 3+ models in production.

### 2.4 Graceful Degradation

| Error Category | Detection | Response Pattern |
|---------------|-----------|-----------------|
| **Execution errors** | HTTP status codes, timeouts | Circuit breaker + retries with exponential backoff |
| **Semantic errors** | Output validation, schema checks | Validation + semantic fallback (simpler prompt, smaller model) |
| **State errors** | State consistency checks | State verification + checkpointing |
| **Timeout/latency** | Adaptive timeout monitoring | Partial result extraction + cached responses |
| **Silent quality degradation** | LLM-as-Judge scoring | Quality monitoring + automated rollback |

**Fallback chain**: Order providers by preference. Fallback triggers only after all retries on primary are exhausted. Consecutive providers must not share a failure domain.

**Multi-agent retry storm prevention**: `with_retry(stop_after_attempt=10)` on a tool used by 10 parallel agents = 100 retries hitting a dead service. Solution: shared circuit breaker state in Redis.

### 2.5 Scaling Patterns

| Component | Tool | Role |
|-----------|------|------|
| Inference engine | vLLM | PagedAttention, continuous batching, 24× over baseline |
| Model serving | KServe | Kubernetes-native with auto-scaling, scale-to-zero |
| GPU scheduling | Kueue | Fair scheduling of GPU workloads |
| Disaggregated inference | llm-d | Prefill/decode separation; 3× output tok/s (Tesla) |
| Routing | Gateway API Inference Extension (GA Feb 2026) | Model-aware, KV-cache-aware, LoRA-aware routing |
| Queue processing | KEDA + RabbitMQ/SQS | Queue-depth-driven auto-scaling for batch workloads |

**GPU auto-scaling**: KEDA scales replicas from vLLM's `vllm:num_requests_waiting` via Prometheus. Queue depth and TTFT p99 are the correct scaling signals — GPU utilization is misleading because KV cache can exhaust VRAM while compute utilization remains moderate.

### 2.6 Data Pipeline Management

**Feature store comparison**:

| Criterion | Feast | Tecton | Databricks |
|-----------|-------|--------|------------|
| License | Open-source | Managed SaaS | Managed platform |
| Online serving latency | Low (Redis-backed) | Sub-10ms p99 | Config-dependent |
| Streaming | Via push API | First-class (Kafka/Kinesis) | Spark Structured Streaming |
| Monitoring | DIY | Built-in (drift, freshness) | Lakehouse Monitoring |
| Cost | OSS + infra (~0.3 FTE) | $2K–$20K+/mo | Platform pricing |
| Lock-in | Low | Medium-High | High |

**Vector DB landscape (2026)**:

| Database | Strength | Best For |
|----------|----------|---------|
| **pgvector** | Integrates with existing Postgres; handles up to 50M vectors | Default choice for most teams |
| **Pinecone** | Sub-50ms p99 at scale, fully managed | High-performance managed ops |
| **Qdrant** | Rust-first performance; Series B | Production AI agents |
| **Weaviate** | Native hybrid search (BM25 + dense + metadata) | Hybrid search requirements |

**Embedding refresh**: Versioned + idempotent. 80% of RAG retrieval failures trace to chunking strategy or embedding model, not the database. CI must validate recall@K and tail latency before merge.

### 2.7 Configuration Management

**Prompt registry pattern**: Store prompts as immutable versioned artifacts. Changes via PR with eval gate. Deploy through canary. Feature flags for instant rollback (<60s). If rollback takes >15 minutes, the system is not production-ready.

**Feature flags for AI** (LaunchDarkly AI Configs GA May 2025):

| Flag Category | Examples | Granularity |
|--------------|----------|-------------|
| Model routing | Primary model, fallback, temperature | Per-model |
| Prompt variants | System prompt version, few-shot examples | Per-prompt-template |
| Retrieval strategy | Chunk size, top-k, re-ranker model | Per-RAG-pipeline |
| Tool access | Enabled tools, execution permissions | Per-tool-bundle |
| Guardrails | Toxicity threshold, PII filter sensitivity | Per-safety-config |
| Cost controls | Max tokens, request budget, rate limits | Per-tier |

### 2.8 Incident Management

**Four incident classes**:

| Class | Detection | Containment | Root Cause Pattern |
|-------|-----------|-------------|-------------------|
| **Hallucination** | Factual accuracy drop, user reports | Prompt rollback, stricter guardrails | Context overflow, retrieval failure |
| **Jailbreak** | Safety filter triggers, anomalous outputs | Block attack vectors, tighten input validation | Prompt injection, role confusion |
| **Drift** | Gradual quality degradation, eval regression | Revert to last-known-good version | Provider model update, data shift |
| **PII Leak** | PII detector alerts, compliance flags | Immediate traffic halt, audit logs | Missing output filtering |

**Real-world agent catastrophes** (2025–2026): Amazon Kiro deleted AWS Cost Explorer production (13h outage). Replit Agent deleted a production database and fabricated 4,000 replacement records. McKinsey Lilli exposed 46.5M chat messages. MCP accumulated 30+ CVEs in seven weeks. AI incidents surged 56% in one year.

### 2.9 Compliance Frameworks

| Framework | Scope | Penalties | Status (Aug 2026) |
|-----------|-------|-----------|-------------------|
| **EU AI Act** | Risk-based (4 levels); high-risk requires full compliance | Up to €35M or 7% global turnover | Bulk took effect Aug 2, 2026 |
| **NIST AI RMF** | Voluntary; 4 functions (Govern, Map, Measure, Manage) | None (but agencies reference it) | RMF 1.1 underway |
| **ISO/IEC 42001** | Certifiable AI management system | N/A (certification) | Crosswalk with NIST published |
| **US State Laws** | 1,561 bills across 45 states (Mar 2026) | Varies; CO: transparency; IL: BIPA $1K–5K/violation | Rapidly expanding |

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Model: Production AI Operations

| Component | Cost Range | Notes |
|-----------|:---------:|-------|
| **CI/CD eval pipeline (offline)** | $50–500/run | Depends on test set size and judge model |
| **Shadow mode evaluation** | 2× inference cost | Doubles compute during shadow period |
| **Canary monitoring (LLM judge)** | +10–20% of canary traffic cost | Judge scoring on every canary response |
| **Kubernetes inference (vLLM + KServe)** | $2.85–16.11/GPU-hr | H100–B200 range |
| **Feature store (managed)** | $2K–20K/mo | Tecton consumption-based |
| **Vector DB (managed)** | $70–2K/mo | Pinecone pod pricing at scale |
| **Prompt registry + eval platform** | $200–2.5K/mo | Langfuse $29–$2,499; LangSmith $39/seat |
| **Compliance platform** | $50K–1M/yr | Collibra enterprise; Atlan $50K+ |
| **Observability** | $1K–15K/mo | Module 14 reference |

**Industry context**: MLOps market $4.38B in 2026 (39.8% CAGR). LLMOps software market $7.14B. 85% of ML models never make it to production. 42% of companies abandoned AI initiatives in 2024–2025 (doubled from 17%). Enterprise monthly AI spend averaged $85K.

### 3.2 Latency SLA Targets

| Component | p50 | p95 | p99 | Mitigation |
|-----------|:---:|:---:|:---:|------------|
| **Prompt registry lookup** | <5ms | <10ms | <20ms | In-memory cache; CDN-backed |
| **Feature flag evaluation** | <1ms | <2ms | <5ms | Edge evaluation; local SDK cache |
| **Canary routing decision** | <1ms | <2ms | <5ms | Session hash lookup; local |
| **Blue-green swap** | 0ms (instant) | 0ms | 0ms | DNS/load balancer pointer change |
| **Shadow mode fork** | ~0ms | ~0ms | ~0ms | Async copy; no user path impact |
| **Model cold start** | 30s | 60s | 120s | Pre-warmed GPU pool; weight caching on NVMe |
| **CI eval gate (offline)** | 5min | 15min | 30min | Parallel test execution; model caching |
| **Rollback (prompt)** | <5s | <30s | <60s | Version pointer change in registry |
| **Rollback (model)** | <2min | <5min | <10min | Blue-green swap to previous |
| **Full agent rollback** | <10min | <30min | <60min | Orchestrated stack revert |

### 3.3 Throughput & Back-Pressure

**Scaling benchmarks**:
- vLLM: 24× throughput over baseline HuggingFace Transformers on A100
- llm-d + KServe (Tesla): 3× output tok/s improvement, 2× TTFT reduction on Llama 3.1 70B (4× MI300X)
- Continuous batching: 80–95% GPU utilization (vs. 30–50% without)
- KServe scale-to-zero: Avoids always-on GPU cost for low-traffic models

**Back-pressure mechanisms**:
- **Request queue depth**: KEDA scales GPU workers from `vllm:num_requests_waiting`. When queue exceeds 2× batch capacity, new requests get 429.
- **Token budget enforcement**: Gateway rejects requests that would exceed per-user daily token cap.
- **Eval queue depth**: Shadow and canary evaluations run asynchronously. If eval queue grows beyond threshold, reduce sampling rate.
- **Batch processing**: Drop batch jobs into queue (SQS, Kafka); workers pull at their pace. Queue depth drives auto-scaling via KEDA.
- **Cost budget**: Hard monthly cap. At 80% threshold, alert; at 100%, cascade to cheaper model or reject non-critical requests.

### 3.4 RPO/RTO per Persistence Tier

| Component | RPO | RTO | Mechanism |
|-----------|-----|-----|-----------|
| **Prompt registry** | 0 (version-controlled, immutable) | <5s (pointer change) | Git-backed; instant rollback via version pointer |
| **Model registry** | 0 (immutable artifacts) | <2min (blue-green swap) | MLflow + object store; pre-warmed standby |
| **Eval test datasets** | 0 (version-controlled) | <1s (read from versioned store) | Git LFS or S3 versioning |
| **Feature store (online)** | Per-write (Redis AOF) | <10s (Redis restart/failover) | Redis Sentinel or Cluster with AOF |
| **Vector DB index** | Per-write (WAL) | <30s (index reload) | Qdrant: WAL + snapshot; Pinecone: managed |
| **Audit trail** | Per-event (append-only) | <5s (DB reconnect) | Append-only log; immutable storage |
| **Inference request queue** | Per-message (persistent queue) | <5s (queue restart) | SQS/Kafka with message persistence |

---

## 4. Distributed Resilience & Security

### 4.1 Circuit Breaker for Production AI

#### 4.1.1 State Machine

```
                  requests flowing
             ┌───────────────┐
             │               │
             ▼               │
        ┌─────────┐    ┌────┴─────┐    ┌─────────────┐
        │ CLOSED  │───▶│  OPEN    │───▶│ HALF-OPEN   │
        │         │    │          │    │             │
        │ Normal  │    │ Fallback │    │ Route 3    │
        │ serving │    │ to backup│    │ test reqs   │
        │ through │    │ model/   │    │ through     │
        │ primary │    │ provider │    │ primary     │
        │ stack   │    │ chain    │    │ stack       │
        └─────────┘    └──────────┘    └─────────────┘
             ▲          │       ▲            │
             │          │       │            │
             │          │       └────────────┘
             │          │      any test req fails
             │     after 30s
             │     recovery timeout
             │     (30s → 60s → 120s exponential)
             │
             └──────────────────────────────┘
                   3/3 test requests succeed
                   with quality score ≥ baseline
                   and TTFT < 2× baseline
```

**Thresholds**:
- **Closed → Open**: 5 consecutive failures OR 50% failure rate over 10s window. Failures: HTTP 5xx, timeout >30s, OOM, model crash, eval score <0.5 on canary.
- **Open duration**: 30s initial recovery timeout with exponential backoff (30s → 60s → 120s). Cap at 10 minutes.
- **Open behavior**: Route all traffic to fallback chain (e.g., primary Sonnet → fallback Haiku → cached response → graceful error). Jitter (±10%) on backoff to prevent retry storms across agent fleets.
- **Half-Open probes**: Route 3 test requests through primary stack.
- **Half-Open → Closed**: All 3 requests succeed with quality score ≥ baseline AND TTFT <2× baseline.
- **Escalation**: If circuit stays open >5 minutes, page on-call. If >15 minutes, escalate to team lead with GPU health diagnostics.

### 4.2 Failure Taxonomy

| Failure | Class | Detection | Mitigation |
|---------|-------|-----------|------------|
| Provider API rate limit (429) | **Transient** | HTTP 429; Retry-After header | Backoff respecting Retry-After; failover to alternate provider |
| Provider API outage (5xx) | **Transient** | HTTP 5xx; health check | Failover via gateway; circuit breaker |
| Model deprecated by provider (404) | **Permanent** | HTTP 404 on model endpoint | Migrate to replacement model; alert on-call |
| Silent quality degradation | **Permanent** (until fixed) | Eval score drop; drift detection | Rollback prompt/model; investigate root cause |
| GPU OOM during inference | **Transient** | CUDA error; process crash | Reduce batch size; restart pod; KV cache eviction |
| Feature store training-serving skew | **Permanent** (data bug) | Distribution divergence monitor | Fix transformation; redeploy feature pipeline |
| Vector DB index corruption | **Permanent** | Query quality drop; index checksum fail | Rebuild index from source; restore from snapshot |
| Embedding model version mismatch | **Permanent** (config bug) | Dimension mismatch error; relevance drop | Pin embedding version; re-index with correct model |
| Canary regression detected | **Transient** (version-specific) | Canary eval score below threshold | Auto-rollback canary; block promotion |
| Compliance audit trail gap | **Permanent** (process gap) | Missing events in audit log | Fix logging pipeline; backfill from inference logs |

### 4.3 Idempotency in Production Operations

- **Prompt deployment**: Immutable versioned artifacts. Deploying the same version ID twice is a no-op. Version pointer changes are idempotent.
- **Embedding refresh**: Re-embedding pipelines use content hash as dedup key. Re-running the pipeline on unchanged content produces no new embeddings.
- **CI eval runs**: Eval results keyed by `hash(prompt_version + model_version + dataset_version + eval_config)`. Re-running with identical inputs returns cached results.
- **Rollback**: Rollback is a pointer change to a previous immutable version. Can be executed repeatedly without side effects.
- **Cost recording**: Per-request cost events keyed by `request_id`. Duplicate events ignored via idempotent insert.

### 4.3.1 Poison-Pill Detection in Production

Poison pills in production are deployments, configurations, or data changes that pass all gates but cause progressive degradation.

**Detection heuristics**:
- **Slow-burn prompt regression**: New prompt passes offline eval but degrades on production distribution. Detect via weekly eval reruns against production traffic (not held-out test set).
- **Embedding drift**: New embedding model passes CI quality gate but shifts retrieval distribution. Detect via embedding centroid distance monitoring (alert >0.05–0.10).
- **Feature store contamination**: Training-serving skew introduced by feature pipeline change. Detect via feature distribution comparison between training and serving.
- **Canary survivorship bias**: Canary passes because 1% traffic is not representative. Detect by stratifying canary traffic across user segments, query types, and content categories.
- **Silent provider model update**: Provider changes model behavior without notification (OpenAI April 2025 incident). Detect via daily eval reruns on fixed test set.

**Quarantine flow**: Flagged deployment frozen at current canary percentage. No further promotion. Eval team investigates with expanded test set. If confirmed regression, rollback to previous version and add failing cases to test set. Promoted only after root cause addressed.

### 4.4 Zero-Trust Boundaries

1. **Prompt supply chain**: Prompts treated as executable code. Every prompt change goes through PR review + automated eval gate. No direct production edits. Audit trail for every promotion.

2. **Model provenance**: Every model weight file verified against signed checksum from trusted registry. Quantized weights re-verified post-quantization. Adapter weights (LoRA) tracked with base model version.

3. **Feature store isolation**: Training data and serving data pipelines run through identical transformations. Schema validation enforces contracts. No ad-hoc feature engineering in production.

4. **Inference log access control**: Inference logs (prompts, completions) contain customer data. Role-based access control with field-level encryption for PII fields. Logs retained per data retention policy (GDPR Article 17).

5. **Compliance boundary**: Audit trail is append-only and immutable. Model cards auto-generated from live asset metadata. Regulators get evidence retrieval, not document reconstruction.

---

## 5. Production Enterprise Code

### 5.1 Five-Gate CI/CD Pipeline

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class GateResult(Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class EvalScore:
    metric: str
    value: float
    baseline: float
    threshold: float

    @property
    def passed(self) -> bool:
        return self.value >= self.baseline - self.threshold


@dataclass
class GateReport:
    gate_name: str
    result: GateResult
    scores: list[EvalScore] = field(default_factory=list)
    failure_reason: str = ""
    duration_ms: float = 0.0


@dataclass
class PipelineResult:
    prompt_version: str
    model_version: str
    gates: list[GateReport] = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return all(g.result == GateResult.PASS for g in self.gates)


class FiveGatePipeline:
    def __init__(self, eval_runner, cost_calculator, shadow_replayer,
                 canary_router):
        self.eval = eval_runner
        self.cost = cost_calculator
        self.shadow = shadow_replayer
        self.canary = canary_router

    def run(self, prompt_version: str, model_version: str,
            dataset_version: str) -> PipelineResult:
        result = PipelineResult(
            prompt_version=prompt_version,
            model_version=model_version,
        )

        lint_report = self._gate_lint(prompt_version)
        result.gates.append(lint_report)
        if lint_report.result == GateResult.FAIL:
            return result

        offline_report = self._gate_offline_eval(
            prompt_version, model_version, dataset_version)
        result.gates.append(offline_report)
        if offline_report.result == GateResult.FAIL:
            return result

        cost_report = self._gate_cost_budget(
            prompt_version, model_version, dataset_version)
        result.gates.append(cost_report)
        if cost_report.result == GateResult.FAIL:
            return result

        shadow_report = self._gate_shadow_eval(
            prompt_version, model_version)
        result.gates.append(shadow_report)
        if shadow_report.result == GateResult.FAIL:
            return result

        result.gates.append(GateReport(
            gate_name="canary",
            result=GateResult.PASS,
            failure_reason="Canary requires manual promotion via canary_router",
        ))
        return result

    def _gate_lint(self, prompt_version: str) -> GateReport:
        issues = self.eval.lint_prompt(prompt_version)
        return GateReport(
            gate_name="lint",
            result=GateResult.PASS if not issues else GateResult.FAIL,
            failure_reason="; ".join(issues) if issues else "",
        )

    def _gate_offline_eval(self, prompt_version: str,
                            model_version: str,
                            dataset_version: str) -> GateReport:
        scores = self.eval.run_offline(
            prompt_version, model_version, dataset_version)
        failed = [s for s in scores if not s.passed]
        return GateReport(
            gate_name="offline_eval",
            result=GateResult.FAIL if failed else GateResult.PASS,
            scores=scores,
            failure_reason=(
                f"Regression on: {', '.join(s.metric for s in failed)}"
                if failed else ""
            ),
        )

    def _gate_cost_budget(self, prompt_version: str,
                           model_version: str,
                           dataset_version: str) -> GateReport:
        cost = self.cost.estimate(prompt_version, model_version,
                                   dataset_version)
        baseline = self.cost.get_baseline(model_version)
        score = EvalScore("cost_ratio", cost / baseline if baseline else 1.0,
                          1.0, 0.10)
        return GateReport(
            gate_name="cost_budget",
            result=GateResult.PASS if score.passed else GateResult.FAIL,
            scores=[score],
            failure_reason=(
                f"Cost {cost:.2f} exceeds 110% of baseline {baseline:.2f}"
                if not score.passed else ""
            ),
        )

    def _gate_shadow_eval(self, prompt_version: str,
                           model_version: str) -> GateReport:
        comparison = self.shadow.replay_and_compare(
            prompt_version, model_version)
        regression = comparison.regression_pct
        score = EvalScore("shadow_regression", 1.0 - regression,
                          1.0, 0.05)
        return GateReport(
            gate_name="shadow_eval",
            result=GateResult.PASS if score.passed else GateResult.FAIL,
            scores=[score],
            failure_reason=(
                f"Shadow regression: {regression:.1%}"
                if not score.passed else ""
            ),
        )
```

### 5.2 Canary Deployment Controller

```python
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class CanaryStage(Enum):
    SHADOW = "shadow"
    CANARY_1 = "canary_1pct"
    CANARY_5 = "canary_5pct"
    CANARY_20 = "canary_20pct"
    CANARY_50 = "canary_50pct"
    FULL = "full_rollout"
    ROLLED_BACK = "rolled_back"


STAGE_TRAFFIC = {
    CanaryStage.SHADOW: 0.0,
    CanaryStage.CANARY_1: 0.01,
    CanaryStage.CANARY_5: 0.05,
    CanaryStage.CANARY_20: 0.20,
    CanaryStage.CANARY_50: 0.50,
    CanaryStage.FULL: 1.0,
}

STAGE_ORDER = [
    CanaryStage.SHADOW, CanaryStage.CANARY_1, CanaryStage.CANARY_5,
    CanaryStage.CANARY_20, CanaryStage.CANARY_50, CanaryStage.FULL,
]


@dataclass
class CanaryMetrics:
    error_rate_delta: float
    p99_latency_delta_pct: float
    eval_score_delta: float
    coherence_delta: float
    toxicity_increase: bool


@dataclass
class RollbackTrigger:
    metric: str
    threshold: float
    actual: float


class CanaryController:
    def __init__(self, version_id: str, prompt_registry,
                 min_soak_hours: float = 24.0):
        self.version_id = version_id
        self.registry = prompt_registry
        self.min_soak_hours = min_soak_hours
        self.stage = CanaryStage.SHADOW
        self._stage_start_hours: float = 0.0

    def get_traffic_pct(self) -> float:
        return STAGE_TRAFFIC.get(self.stage, 0.0)

    def should_route_to_canary(self, session_id: str) -> bool:
        pct = self.get_traffic_pct()
        if pct <= 0:
            return False
        if pct >= 1.0:
            return True
        return (hash(session_id) % 10000) < (pct * 10000)

    def evaluate_promotion(self, metrics: CanaryMetrics,
                            hours_at_stage: float) -> Optional[RollbackTrigger]:
        trigger = self._check_rollback_triggers(metrics)
        if trigger:
            self.stage = CanaryStage.ROLLED_BACK
            self.registry.rollback(self.version_id)
            return trigger

        if hours_at_stage >= self.min_soak_hours:
            idx = STAGE_ORDER.index(self.stage)
            if idx < len(STAGE_ORDER) - 1:
                self.stage = STAGE_ORDER[idx + 1]
        return None

    def _check_rollback_triggers(
        self, m: CanaryMetrics
    ) -> Optional[RollbackTrigger]:
        checks = [
            ("error_rate_increase", 0.01, m.error_rate_delta),
            ("p99_latency_increase_pct", 0.20, m.p99_latency_delta_pct),
            ("eval_score_decrease", -0.05, -m.eval_score_delta),
            ("coherence_decrease", -0.10, -m.coherence_delta),
        ]
        for metric, threshold, actual in checks:
            if actual > threshold:
                return RollbackTrigger(metric, threshold, actual)
        if m.toxicity_increase:
            return RollbackTrigger("toxicity_increase", 0, 1)
        return None
```

### 5.3 Compliance Audit Trail Logger

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import hashlib
import json


class AuditEventType(Enum):
    PROMPT_DEPLOYED = "prompt_deployed"
    MODEL_DEPLOYED = "model_deployed"
    ROLLBACK = "rollback"
    EVAL_RUN = "eval_run"
    HUMAN_REVIEW = "human_review"
    INCIDENT_CREATED = "incident_created"
    CONFIG_CHANGE = "config_change"
    DATA_ACCESS = "data_access"


@dataclass
class AuditEvent:
    event_type: AuditEventType
    actor: str
    timestamp_iso: str
    resource_id: str
    resource_version: str
    details: dict = field(default_factory=dict)
    parent_event_id: Optional[str] = None

    @property
    def event_id(self) -> str:
        payload = json.dumps({
            "type": self.event_type.value,
            "actor": self.actor,
            "ts": self.timestamp_iso,
            "resource": self.resource_id,
            "version": self.resource_version,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:24]


class ComplianceAuditLogger:
    def __init__(self, store):
        self.store = store
        self._event_ids: set[str] = set()

    def log(self, event: AuditEvent) -> bool:
        if event.event_id in self._event_ids:
            return False
        self._event_ids.add(event.event_id)
        self.store.append(event)
        return True

    def log_deployment(self, actor: str, timestamp: str,
                        prompt_version: str, model_version: str,
                        eval_report_id: str) -> str:
        event = AuditEvent(
            event_type=AuditEventType.PROMPT_DEPLOYED,
            actor=actor,
            timestamp_iso=timestamp,
            resource_id="production",
            resource_version=prompt_version,
            details={
                "model_version": model_version,
                "eval_report_id": eval_report_id,
                "deployment_method": "canary",
            },
        )
        self.log(event)
        return event.event_id

    def log_rollback(self, actor: str, timestamp: str,
                      from_version: str, to_version: str,
                      reason: str, incident_id: Optional[str] = None) -> str:
        event = AuditEvent(
            event_type=AuditEventType.ROLLBACK,
            actor=actor,
            timestamp_iso=timestamp,
            resource_id="production",
            resource_version=to_version,
            details={
                "from_version": from_version,
                "reason": reason,
                "incident_id": incident_id,
            },
        )
        self.log(event)
        return event.event_id

    def generate_compliance_report(self, start_date: str,
                                     end_date: str) -> dict:
        events = self.store.query(start_date, end_date)
        return {
            "period": {"start": start_date, "end": end_date},
            "total_events": len(events),
            "deployments": sum(
                1 for e in events
                if e.event_type == AuditEventType.PROMPT_DEPLOYED
            ),
            "rollbacks": sum(
                1 for e in events
                if e.event_type == AuditEventType.ROLLBACK
            ),
            "incidents": sum(
                1 for e in events
                if e.event_type == AuditEventType.INCIDENT_CREATED
            ),
            "human_reviews": sum(
                1 for e in events
                if e.event_type == AuditEventType.HUMAN_REVIEW
            ),
            "coverage": {
                "model_cards": self._check_model_card_coverage(events),
                "eval_before_deploy": self._check_eval_coverage(events),
                "audit_trail_complete": len(events) > 0,
            },
        }

    def _check_model_card_coverage(self, events: list) -> bool:
        deploys = [e for e in events
                   if e.event_type == AuditEventType.MODEL_DEPLOYED]
        return all("model_card_id" in e.details for e in deploys)

    def _check_eval_coverage(self, events: list) -> bool:
        deploys = [e for e in events
                   if e.event_type == AuditEventType.PROMPT_DEPLOYED]
        return all("eval_report_id" in e.details for e in deploys)
```

---

## 6. Architectural System Design Scenarios

### 6.1 Scenario: Production AI Platform for a 200-Person Engineering Organization

**Business context**: A fintech company with 200 engineers runs 12 AI-powered features (fraud detection, customer support chatbot, document extraction, compliance screening). Current state: each team deploys independently with ad-hoc testing — last quarter saw 3 production incidents from untested prompt changes and 1 from a silent provider model update. Requirements: unified CI/CD pipeline for all AI features, deployment gates that prevent untested changes from reaching production, <60s rollback capability, EU AI Act compliance for high-risk features (credit scoring, fraud detection), and $5K/month budget for tooling.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                    UNIFIED AI PRODUCTION PLATFORM                        │
 │                                                                          │
 │  PR ──▶ ┌──────────────┐ ──▶ ┌──────────────┐ ──▶ ┌────────────────┐  │
 │         │ 5-Gate CI/CD │     │ Canary       │     │ Production     │  │
 │         │ (GitHub Act.)│     │ Controller   │     │ (KServe +      │  │
 │         │              │     │              │     │  vLLM)         │  │
 │         │ 1. Lint      │     │ 1%→5%→20%   │     │                │  │
 │         │ 2. Offline   │     │ →50%→100%   │     │ 12 AI features │  │
 │         │    eval      │     │ 24h soak     │     │ Per-feature    │  │
 │         │ 3. Cost      │     │ Auto-rollback│     │ SLOs + flags   │  │
 │         │ 4. Shadow    │     │              │     │                │  │
 │         │ 5. Canary    │     │              │     │                │  │
 │         └──────────────┘     └──────────────┘     └────────────────┘  │
 │                                                                        │
 │  ┌────────────────────────────────────────────────────────────────────┐ │
 │  │  Shared Infrastructure                                             │ │
 │  │  Langfuse (prompt registry + eval + traces): $199/mo               │ │
 │  │  LaunchDarkly AI Configs (feature flags): ~$1K/mo                  │ │
 │  │  Atlan (data lineage + compliance): $50K/yr                       │ │
 │  │  Prometheus + Grafana (SLOs + alerting): self-hosted               │ │
 │  └────────────────────────────────────────────────────────────────────┘ │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Per-Team Ad-Hoc | B: Unified Platform with Shared Tooling (Recommended) | C: Enterprise MLOps Suite (Databricks/SageMaker) |
|-----------|-------------------|------------------------------------------------------|------------------------------------------------|
| **Incident prevention** | ⬛⬜⬜ — No gates; 3 incidents/quarter | ⬛⬛⬛ — 5-gate pipeline catches regressions before prod | ⬛⬛⬛ — Full gates but higher complexity |
| **Rollback speed** | ⬛⬜⬜ — Manual, 15-60min | ⬛⬛⬛ — <60s via prompt registry pointer | ⬛⬛⬜ — Platform-dependent; often minutes |
| **EU AI Act compliance** | ⬛⬜⬜ — No audit trail | ⬛⬛⬛ — Atlan lineage + Langfuse traces + audit logger | ⬛⬛⬛ — Built-in governance features |
| **Cost** | ⬛⬛⬛ — Near zero (no tooling) | ⬛⬛⬛ — ~$5.5K/mo (within budget) | ⬛⬜⬜ — $20K-50K+/mo for enterprise tier |
| **Setup time** | ⬛⬛⬛ — None (status quo) | ⬛⬛⬜ — 4-6 weeks (platform + team onboarding) | ⬛⬜⬜ — 3-6 months |
| **Team adoption** | ⬛⬛⬛ — No change needed | ⬛⬛⬜ — Teams must adopt shared pipeline | ⬛⬜⬜ — Steep learning curve |

**Recommended approach**: **B (Unified Platform with Shared Tooling)**.

**Decision rationale**: Option A is the current state producing 3 incidents/quarter from untested changes — the status quo is failing. Option C (enterprise MLOps suite) provides comprehensive features but exceeds the $5K/month budget at $20K–50K+/month and requires 3–6 months of setup. Option B delivers the core requirements within budget: Langfuse Pro ($199/month) provides prompt registry, offline eval with GitHub Actions integration, and trace storage. LaunchDarkly AI Configs (~$1K/month) provides feature flags with instant rollback capability across all 12 features. Atlan ($50K/year = ~$4.2K/month) provides data lineage and compliance documentation required for EU AI Act high-risk features (fraud detection, credit scoring). Total: ~$5.5K/month. The five-gate CI/CD pipeline runs as GitHub Actions with Langfuse's `experiment-action`, requiring no additional infrastructure. Rollback is a version pointer change in Langfuse (<60s). Weekly eval reruns detect provider model drift — the exact failure mode that caused last quarter's silent update incident. The 4–6 week setup includes building shared eval datasets, configuring gates, and onboarding teams to the shared pipeline.

### 6.2 Scenario: Scaling AI Agent Infrastructure from 10K to 1M Daily Sessions

**Business context**: A customer service platform runs AI agents handling 10K daily sessions. Business is growing 10× over the next 12 months (to 100K, then 1M daily sessions). Current infrastructure: single-GPU vLLM deployment with manual scaling. Requirements: handle 1M daily sessions with p99 TTFT <2s, auto-scale GPU resources without 24/7 ops, per-customer cost attribution for B2B billing, graceful degradation during traffic spikes, and agent versioning with zero-downtime updates.

#### Component Diagram

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                   SCALABLE AGENT INFRASTRUCTURE                          │
 │                                                                          │
 │  Sessions ──▶ ┌──────────────┐ ──▶ ┌──────────────┐ ──▶ ┌──────────┐  │
 │               │ Gateway API  │     │ KServe +     │     │ vLLM     │  │
 │  1M/day       │ Inference    │     │ Kueue        │     │ Fleet    │  │
 │               │ Extension    │     │              │     │          │  │
 │               │ - Session    │     │ - KEDA from  │     │ - 8-16   │  │
 │               │   pinning    │     │   queue depth│     │   B200s  │  │
 │               │ - Model-     │     │ - Scale-to-0 │     │ - FP8    │  │
 │               │   aware      │     │   off-peak   │     │ - Cont.  │  │
 │               │   routing    │     │ - GPU node   │     │   batch  │  │
 │               │ - LoRA-aware │     │   autoscaler │     │ - Prefix │  │
 │               │              │     │   (Karpenter)│     │   cache  │  │
 │               └──────────────┘     └──────────────┘     └──────────┘  │
 │                                                                        │
 │  ┌────────────────────────────────────────────────────────────────────┐ │
 │  │  Resilience Layer                                                  │ │
 │  │  - Circuit breaker per provider (shared Redis state)               │ │
 │  │  - Fallback chain: Sonnet → Haiku → cached response               │ │
 │  │  - Queue-based batch processing for async tasks (SQS + KEDA)       │ │
 │  │  - Session pinning: active sessions stay on current agent version  │ │
 │  └────────────────────────────────────────────────────────────────────┘ │
 └──────────────────────────────────────────────────────────────────────────┘
```

#### Trade-off Matrix

| Dimension | A: Vertical Scaling (Bigger GPUs) | B: Kubernetes-Native Auto-Scaling (Recommended) | C: Fully Managed (Bedrock/Vertex) |
|-----------|----------------------------------|--------------------------------------------------|----------------------------------|
| **Scale to 1M sessions/day** | ⬛⬜⬜ — Single GPU ceiling | ⬛⬛⬛ — Horizontal + node auto-scale | ⬛⬛⬛ — Elastic (provider manages) |
| **TTFT p99 <2s** | ⬛⬛⬜ — OK until saturation | ⬛⬛⬛ — Queue-aware routing; prefix cache | ⬛⬛⬜ — Provider queuing adds latency |
| **Cost at 1M/day** | ⬛⬜⬜ — Over-provisioned 24/7 | ⬛⬛⬛ — Scale-to-zero off-peak; KEDA-driven | ⬛⬛⬜ — Pay-per-use but premium pricing |
| **Per-customer attribution** | ⬛⬛⬜ — Manual tracking | ⬛⬛⬛ — Gateway tags + cost ledger | ⬛⬜⬜ — Limited provider-side attribution |
| **Zero-downtime agent updates** | ⬛⬜⬜ — Restart required | ⬛⬛⬛ — Rolling update + session pinning | ⬛⬛⬜ — Provider-managed, limited control |
| **Graceful degradation** | ⬛⬜⬜ — No fallback chain | ⬛⬛⬛ — Circuit breaker + fallback + queue overflow | ⬛⬛⬜ — Provider handles some; limited control |
| **Ops burden** | ⬛⬛⬛ — Simple (one machine) | ⬛⬛⬜ — Kubernetes expertise required | ⬛⬛⬛ — Fully managed |

**Recommended approach**: **B (Kubernetes-Native Auto-Scaling)**.

**Decision rationale**: Option A (vertical scaling) hits a ceiling — a single GPU cannot serve 1M daily sessions with acceptable latency. Option C (managed APIs) provides elasticity but lacks per-customer cost attribution critical for B2B billing, and premium API pricing at 1M sessions/day (~500M tokens/day) would cost $150K+/month versus ~$50K/month self-hosted. Option B deploys KServe + vLLM on 8–16 B200 GPUs with the Gateway API Inference Extension (GA Feb 2026) for model-aware, KV-cache-aware routing and session pinning. KEDA scales replicas from queue depth (not GPU utilization — KV cache can exhaust VRAM while compute utilization looks moderate). Karpenter handles GPU node provisioning with sub-5-minute scale-up using pre-cached model weights. Scale-to-zero off-peak (nights, weekends) saves 30–40% of compute costs. Circuit breakers with shared Redis state prevent retry storms across the agent fleet. Session pinning ensures active conversations stay on the current agent version during rolling updates, with only new sessions routing to updated versions. Per-customer attribution flows from `customer_id` span attributes through the Gateway API to the cost ledger. The main risk is Kubernetes expertise — mitigated by hiring or training one ML platform engineer and using managed Kubernetes (EKS/GKE).

---

*Module 16 complete. Covers CI/CD five-gate pipeline (lint, offline eval, cost budget, shadow, canary), deployment patterns (shadow mode 67% enterprise adoption, canary with session pinning, blue-green), reliability engineering (6 SLO categories, compound error budgets, 5% production failure rate), scaling (Kubernetes + vLLM + KServe + llm-d, KEDA auto-scaling, queue-based), data pipelines (feature stores, vector DBs, embedding refresh), configuration management (prompt registries, feature flags, drift detection), incident management (4 classes, 6-step runbook, real-world catastrophes), and compliance (EU AI Act, NIST RMF, ISO 42001, US state laws, model cards, data lineage).*
