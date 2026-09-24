# Topic 11: Production Deployment
> Consolidated from multi-model research (Grok + Opus) | Study & Interview Prep

---

## Introduction

### What This Topic Covers

Production deployment is where everything from Topics 1-10 comes together into a **running, reliable, cost-efficient, secure system**. This topic covers Docker containerization with GPU support, Kubernetes orchestration with KEDA autoscaling (because standard HPA is blind to AI workloads), CI/CD with semantic evaluation gates, guardrail architectures (NeMo Guardrails, LlamaGuard, Guardrails AI with defense-in-depth), cost optimization achieving 60-85% savings through caching/routing/batching, fallback chains with circuit breakers, checkpoint/resume for long-running agents, model serving infrastructure (vLLM, TGI, NIM, SGLang), queue-based architectures for async workloads, and enterprise compliance (SOC 2, HIPAA, GDPR, EU AI Act).

### Why Study This

- **The gap between demo and production**: Only 10% of enterprise AI agent pilots reach production (industry surveys). This topic covers the infrastructure, reliability, and governance patterns that bridge that gap.
- **Cost is the killer**: A misconfigured production system can burn $25K/month. The cost optimization stack in this topic (prompt caching + semantic caching + model routing + batch APIs + prompt compression) reduces that to $3.5-4K — an 85% savings. Interviewers at senior levels always ask about cost.
- **Compliance deadlines**: EU AI Act high-risk obligations take effect August 2, 2026 (7% global turnover penalties). HIPAA breaches cost $7.42M on average. SOC 2 2026 criteria now include AI governance. This is not future work — it's current work.
- **Incident awareness**: The LiteLLM supply chain attack, Anthropic quality degradation incident, AWS AI-agent outage, and multiple $50-500 runaway agent bills are all 2025-2026 events. Knowing these incidents and their mitigations shows operational maturity.

### What Details Are Included

- Docker multi-stage builds with GPU passthrough (Dockerfile + Compose)
- KEDA autoscaling with ScaledObject YAML + Karpenter NodePool
- CI/CD pipeline with semantic evaluation gates (6-step release flow)
- Guardrail architectures with 6-layer defense-in-depth and latency benchmarks
- Cost optimization: per-strategy savings breakdown ($25K → $3.5K walkthrough)
- GPU pricing comparison across 7 cloud providers
- Model serving comparison (vLLM 24x throughput over TGI, SGLang 29% gain on shared context)
- Fallback chains with LiteLLM/Portkey internals and circuit breaker integration
- Checkpoint/resume with LangGraph, Temporal, and Google ADK patterns
- Queue architectures with Redis Streams and Celery
- Compliance framework table with penalty amounts (SOC 2, HIPAA, GDPR, EU AI Act)
- Notable production incidents timeline (5 incidents with lessons learned)
- Three enterprise system design scenarios with trade-off matrices

### How to Approach This Document

> **First pass (2-3 hours)**: Sections 1-4. Study the deployment architecture diagram. Understand why KEDA beats HPA for AI workloads. Trace through the CI/CD pipeline and the guardrail layers.
>
> **Second pass (2-3 hours)**: Sections 5-7. Work through the cost optimization walkthrough ($25K → $3.5K). Study the model serving comparison table. Review the failure modes — especially cost runaway prevention and the 429 taxonomy.
>
> **Interview prep (1 hour)**: Section 10. Practice the "deploy an AI agent to production" answer: containerize, autoscale with KEDA, gate releases with semantic eval, layer guardrails, optimize cost with the 5-lever stack, monitor with OTel. Know the compliance frameworks by name.
>
> **Before an interview (30 min)**: Re-read section 10 only.

### How This Document Is Structured

This guide follows a **10-section progressive learning flow** — each section builds on the previous:

| # | Section | What It Covers | Study Approach |
|---|---------|---------------|----------------|
| 1 | Concept Overview | What and why | Read first for orientation |
| 2 | Core Concepts | Fundamental building blocks | Study deeply, take notes |
| 3 | Architecture & System Design | ASCII diagrams, topology, data flow | Draw diagrams from memory |
| 4 | Key Algorithms & Mechanics | Technical depth, complexity analysis | Understand the "why" |
| 5 | Token Economics & Cost Analysis | Pricing, cost formulas, optimization | Memorize key numbers |
| 6 | Production Patterns & Code | Runnable Python implementations | Run, modify, and break the code |
| 7 | Failure Modes & Mitigations | What goes wrong, how to handle it | Practice explaining failure scenarios |
| 8 | Security & Governance | Enterprise security considerations | Know compliance frameworks by name |
| 9 | System Design Scenarios | Real-world problems with trade-offs | Practice whiteboarding these |
| 10 | Interview Quick Reference | Key numbers, frameworks, talking points | Review 30 min before interviews |

---

**Scope**: End-to-end production deployment for LLM-powered applications and agent systems -- containerization, Kubernetes orchestration with KEDA, CI/CD with semantic evaluation gates, guardrail architectures, cost optimization (60-85% savings), fallback chains with circuit breakers, checkpoint/resume for durable agents, model serving infrastructure, queue architectures, enterprise security and compliance.

**Key references**: Cast.ai 2026 (5% avg GPU utilization), CNCF 2026 (66% GenAI on K8s), OWASP 2025 Top 10 for LLMs, arXiv 2601.06007 ("Don't Break the Cache"), LiteLLM supply chain attack (Mar 2026), DORA 2026 (1.5% throughput decrease with AI code gen), IBM 2025 breach report ($7.42M healthcare avg), EU AI Act high-risk enforcement (Aug 2026).

**Core principle**: **The model never deploys, never gates a canary, never opens a circuit, never meters spend, and never commits a checkpoint.** CI/GitOps own *what may run*. The serving data plane owns *tokens in flight*. The guardrail sidecar is a policy enforcement point (PEP). The checkpoint/workflow store owns *resume identity*. Collapsing these four planes is the dominant failure mode at the Principal Architect level.

---

## 1. Concept Overview

### What Is Production Deployment for AI Systems?

Production deployment for AI systems is fundamentally different from traditional software deployment because you are versioning **three coupled artifacts simultaneously**: code, prompts/data, and model weights. A change to any one can break production silently -- the system keeps returning HTTP 200 while quality degrades. The system keeps returning HTTP 200 while goldens regress.

A production ops/runtime system is **four independently clocked planes** sharing a correlation ID, plus tool proxies and telemetry that must not become a fifth source of truth:

| Plane | What it owns | Clock | Example failure if planes are fused |
|-------|-------------|-------|-------------------------------------|
| **CI / GitOps (control)** | Image digest, prompt SHA, model snapshot, eval gate, canary weights | Pipeline wall-clock; fail-closed | Shipping because `/health` is 200 while golden set regresses |
| **Serving (data)** | Tokens in flight, SSE streams, optional GPU | User SLO: TTFT / e2e | Treating a 12-minute decode as a 30s HTTP request; CPU HPA on idle-wait |
| **Guardrail PEP** | Block/mask/allow on input/output/tool args | Added to TTFT if inline; must be bounded or fail-open + audit | Synchronous frontier LLM self-check on every token; fail-open with no log |
| **Checkpoint / workflow** | Resume identity (thread_id + checkpoint_id / WorkflowId) | Super-step / Activity / offset | Replaying `payments.charge` because the node restarted |

**Real-world example**: A team ships a prompt update because their `/ok` healthcheck returns 200. But their golden evaluation set is failing 30% of schema tests. The control-plane clock (CI gate) was never consulted -- only the serving-plane clock (HTTP status) was checked. Result: silent quality regression in production for hours.

### Why It Matters -- The Money

On a stated skeleton **W** (one chat turn, 3,000 in + 800 out, no cache):

- **Mix M1** (standard SaaS): 92% primary, 6% secondary, 2% deterministic = **$19.20 / 1k requests** (inferred)
- **Mix M3** (retry storm anti-pattern): 1 primary + 2 retries + 1 secondary on 5% of traffic adds ~$3.98/1k = **~$23.2 / 1k** before the user saw one answer
- A 3-node support graph multiplies to **~$58.5 / 1k**
- An idle H100 costs **~$165/day** (inferred from ~$6.88/H100-hr aggregator pricing)

This is why deployment is not just DevOps -- it is cost engineering, reliability engineering, and compliance engineering fused into one role.

---

## 2. Core Concepts

### 2.1 Containerization: Two Images, Two Blast Radii

The **agent/Temporal worker** is a CPU process (Python, HTTP, checkpointer client). The **inference engine** (optional, self-hosted) is a GPU process (NIM/vLLM, HBM, CUDA userspace). Mixing them in one container couples image size, CVE lag, and SIGTERM semantics.

**CPU multi-stage build (distroless)**:

Builder stage (`python:3.13` / `uv sync --locked --no-dev --no-editable`) produces a venv. Runtime stage copies it onto `gcr.io/distroless/python3-debian12:nonroot` or `gcr.io/distroless/cc-debian12:nonroot`. Key constraints:
- Pin **by digest** (`@sha256:...`) -- distroless has no versioned tags beyond `:nonroot`/`:latest`/`:debug`
- `USER 65532`; pre-create writable dirs (distroless **cannot mkdir at start**)
- Google's `python3-debian12` is Python **3.11**, amd64/arm64, **no shell**, **no ctypes** on the default image
- `CMD` must be exec-form arrays (no shell to interpret)
- Distroless **lacks `curl`**: use Kubernetes HTTP probes against `/ok`, not `HEALTHCHECK CMD curl`

**GPU is three layers, not "CUDA + app"**:
1. **Host driver** (GPU Operator DaemonSets)
2. **Container Toolkit / CDI** injecting devices
3. **App image** (NIM/vLLM) -- must **not** ship a second driver

Distroless Python does **not** contain `libcuda`/cuDNN/NCCL. NVIDIA CUDA bases lag distro OpenSSL patches; inheriting `nvidia/cuda:*-ubuntu*` as runtime is a CVE-lag decision.

**Secrets never in layers**:
- `ARG`/`ENV` persist in image history even in discarded stages
- BuildKit `RUN --mount=type=secret,id=...` mounts for that RUN only
- Runtime: K8s Secret volumes / CSI / External Secrets
- **Never**: `COPY .env`, API keys in `langgraph.json`, tokens in checkpointed `state` (use `Runtime.context` / `UntrackedValue`)

```dockerfile
# Production multi-stage build for LLM agent service
# Stage 1: Builder -- installs dependencies, compiles C extensions
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime -- slim image, no build tools, non-root user
FROM python:3.12-slim AS runtime

RUN groupadd -r agent && useradd -r -g agent -d /app -s /sbin/nologin agent
WORKDIR /app

COPY --from=builder /install /usr/local
COPY --chown=agent:agent . .

# Health check with long start_period for model loading (300s)
HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD python -c "import requests; r = requests.get('http://localhost:8000/health'); exit(0 if r.status_code == 200 else 1)"

USER agent
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

**GPU passthrough** requires NVIDIA Container Toolkit. Docker cannot natively communicate with GPU drivers. For an 8B parameter model, 32GB memory limits provide comfortable headroom.

```yaml
# docker-compose.yml -- Agent service with GPU passthrough
services:
  agent:
    build: { context: ., dockerfile: Dockerfile }
    ports: ["8000:8000"]
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - REDIS_URL=redis://redis:6379
      - DATABASE_URL=postgresql://agent:${DB_PASSWORD}@postgres:5432/agent_state
    env_file: [.env]  # secrets mounted at runtime, never baked into image
    deploy:
      resources:
        reservations:
          devices: [{ driver: nvidia, count: 1, capabilities: [gpu] }]
        limits: { memory: 32G }
    depends_on:
      redis: { condition: service_healthy }
      postgres: { condition: service_healthy }
  redis:
    image: redis:7-alpine
    command: redis-server --maxmemory 512mb --maxmemory-policy allkeys-lru
  postgres:
    image: postgres:16-alpine
    volumes: [pgdata:/var/lib/postgresql/data]
volumes:
  pgdata:
```

**Right-sizing rule**: Most teams need Docker Compose plus a deploy script. Kubernetes is justified at 5+ services. Managed services (Cloud Run, Fargate) abstract complexity for small teams.

### 2.2 CI/CD: Eval-Gated Releases

Standard deployments version code. AI deployments version **three tightly coupled artifacts**: code digest, **prompt commit SHA**, and **model snapshot**. A green pytest on the worker image with `gpt-x-latest` in env is not a release.

**Three artifacts, one release**: code + prompt SHA + snapshot in the **same commit**. The CI gate is **fail-closed** on a pinned `(dataset_id, tag, split)` with code oracles + calibrated judges. The prod canary is **fail-open** on sampled live traffic with a **coverage % SLO**.

| Tool | What fails the job | Stochastic judges |
|------|-------------------|-------------------|
| **promptfoo** `promptfoo-action@v1` | `fail-on-threshold` 0-100; `jq .results.stats.failures > 0`; Node >=22.22, 24 LTS | Zero-failure is a **unit-test** gate (schema, PII regex) |
| **Braintrust** `eval-action@v2` | Default exit != quality (non-zero only if eval throws). Quality = `Reporter.reportRun -> bool`. `--sample 20` is non-final | Smoke on PR; full dataset on merge |
| **LangSmith** | pytest wrappers; pin dataset tag; `num_repetitions` | Experiment artifacts outlive 14d traces |

**Semantic evaluation replaces exact-match assertions**: LLMs return varying text, making deterministic testing useless for quality. The minimum viable CI/CD: version control for all artifacts, 20-30 behavioral tests before every deployment, and one agent-specific metric in monitoring.

**Release flow (control-plane clock)**:

1. **PR**: promptfoo schema/PII `FAILURES>0` (unit) + Braintrust `--sample 20` (non-final). No production MCP write tools.
2. **Merge**: Full golden tag + McNemar/bootstrap vs last `main` experiment; pin `prompt@SHA` and `model=gpt-5.4-2026-03-05` in the same commit.
3. **Build**: `langgraph build` / `docker buildx` with `--secret`; Syft SPDX; `cosign sign` + `cosign attest --type https://spdx.dev/Document` on the platform digest.
4. **Canary**: `setWeight: 10`. AnalysisJob runs online sample with filter `prompt.version=candidate`; coverage % is an NFR.
5. **Promote**: Weight 100% or flip active Service. Move Hub commit tag `prod` **after** pods that read the tag at start have rolled.
6. **Export**: AnalysisRun + `results.json` to WORM before default history limit 5 reaps them.

**Deployment strategy comparison**:

| Strategy | Traffic | LLM cost overhead | Rollback speed |
|----------|---------|-------------------|----------------|
| **Canary** | `setWeight` steps; stable RS kept at 100% | Thin extra slice + judge/sidecar | Weight to 0 |
| **Blue/green** | Atomic selector flip | **Two full stacks** until `scaleDownDelaySeconds` (default 30s -- too short for GPU) | Flip back |
| **Hub `prod` tag only** | 100% immediately | $0 extra infra | Workers that pulled at start will not see the move |

**DORA 2026 finding**: AI code generation tools increase code volume but decrease delivery throughput by 1.5% and worsen stability by 7.5%. More code does not mean better outcomes.

### 2.3 Guardrails Architecture

Guardrails are a **Policy Enforcement Point (PEP)**, not a library import in the model node. They run on their own clock -- added to TTFT if inline, so they must be bounded or fail-open with WORM audit.

**Defense-in-depth pipeline (cheap to expensive)**:

| Layer | Check | Latency | Purpose |
|-------|-------|---------|---------|
| 1 | Length / encoding / UTF-8 validation | <1ms | Catch obvious attacks cheaply |
| 2 | Prompt Guard 2 86M / jailbreak heuristics | 20-50ms (CPU or tiny BERT, 512-token window) | Fast safety gate |
| 3 | Presidio mask (regex + NER + checksum) | <5ms | PII anonymization before vendor |
| 4 | JSON Schema on tool arguments | <1ms | Structural validation |
| 5 | Llama Guard / content-safety NIM (8B/12B classifier) | 15-60ms p50 | Detailed hazard classification (S1-S14) |
| 6 | Optional LLM self-check | 100ms+ | Frontier-cost classification |

**Running Layer 6 first is how teams spend frontier tokens classifying `"hi"`.** Parallelize independent checks (a 200ms serial pipeline becomes ~70ms when parallelized). Do not parallelize mask-then-classify if the classifier must see the masked text.

**Key frameworks**:

- **NeMo Guardrails (NVIDIA)**: Middleware between app and LLM. Five rail types: input, dialog, retrieval, execution, output. **IORails** engine runs supported I/O/tool flows in parallel; `require_iorails=True` fails init if you silently drop to LLMRails. Speculative generation (start main LLM while input rails run) is IORails-only, non-streaming. NVIDIA explicitly states the project is not recommended for production as-is (v0.17.0).
- **LlamaGuard (Meta)**: LLM-based classifier emitting `safe`/`unsafe` + MLCommons categories S1-S13 (S14 Code Interpreter Abuse on 3 8B and 4 12B). Guard 3 Vision: one image + text. Guard 4 12B: text + multiple images.
- **Guardrails AI**: Open-source Python framework with 50+ pre-built validators (PII, toxicity, regex). Pydantic integration. Configurable failure actions: fix, reask, exception, filter.

**Presidio PII sandwich**: anonymize (email -> `{{EMAIL_1}}`) -> send to model -> deanonymize via session map after the model, before the user. Microsoft warns: **no guarantee of finding all PII** -- defense in depth, not a checkbox. NeMo `score_threshold` default **0.2** -- raise to cut false positives. Warm spaCy at process start or first-request p99 dies.

**Jailbreak heuristics (NeMo sidecar)**: Port 1337 `/heuristics`, perplexity via gpt2-large. Length/perplexity default 89.79; prefix/suffix default 1845.65 (49/50 GCG-style, 0.04% FPR). Prefix/suffix only on strings with >20 whitespace tokens. Heuristics are LLMRails-only for IORails (issue #2285).

**Streaming output rails**: Options are rolling-window (residual risk), buffer-then-release (kills TTFT), or tool-call-path-only (JSON is finite). Document the residual.

### 2.4 Cost Optimization Stack (60-85% Savings)

**Step-by-step cost reduction from a $25K/month naive baseline**:

| Step | Technique | Savings | Cumulative |
|------|-----------|---------|------------|
| 1 | **Model routing** -- 70% of traffic is simple (RouteLLM: >85% cost reduction on MT-Bench, 95% GPT-4 quality) | 60% | $10,000 |
| 2 | **Prompt caching** -- 90% input discount on repeated prefixes; no quality change | 30% of remaining | $7,000 |
| 3 | **Semantic caching** -- pgvector embedding similarity, 25-35% hit rate on chatbot workloads | 29% further | $5,000 |
| 4 | **Batch API** -- 50% discount for requests tolerating up to 24h (compounds with cache) | 20% further | $4,000 |
| 5 | **Prompt compression** -- LLMLingua reduces input 2-5x with minimal quality loss | 14% further | $3,500 |
| | **Total** | **~85% reduction** | **$3,500-4,000/month** |

**Prompt caching by provider (Sep 2026)**:

| Provider | Trigger | Cache Read Cost | Cache Write Cost | Retention | Min Tokens |
|----------|---------|----------------|-----------------|-----------|------------|
| OpenAI | Automatic | 0.1x input | 1.25x input (GPT-5.6+) | 30 min | 1,024 |
| Anthropic | Explicit `cache_control` | 0.1x input | 1.25x (5-min) / 2x (1-hr extended) | 5 min / 1 hr | varies |
| Google Gemini | Implicit (2.5+) | Automatic | Automatic | varies | 2,048 |

Academic validation: "Don't Break the Cache" (arXiv 2601.06007) tested 500+ agent sessions with 10K-token system prompts -- caching reduced costs 41-80% and improved TTFT 13-31%.

**Critical warning**: Build the eval first, then optimize. Otherwise you are cutting cost blind.

### 2.5 Fallback Chains

**Provider reliability reality**: Anthropic had 114 incidents in a 90-day window in early 2026. OpenAI's 99.76% uptime = ~16 hours/year downtime. Average API uptime fell from 99.66% to 99.46% between Q1 2024 and Q1 2025.

**Chain structure**: Primary (pinned snapshot) -> Secondary (different provider/failure domain) -> Tertiary (lighter model) -> Deterministic template / HITL queue. Each step delivers value with progressively less sophistication.

**Order by cost, not just availability**: If fallback goes Claude Sonnet -> GPT-4o, spending triples during Anthropic outages. Try cheaper before trying different.

**Fallback is distinct from retry**: Retry re-issues to the same model; fallback issues to a different one. Most production systems retry first (2-3 attempts with exponential backoff), then fall back. Agent runs with configured fallback chains saw 38% lower task-abandonment rates during simulated provider outages.

**LiteLLM specifics**: Fallbacks are in-order model groups; after `num_retries` (docs default 3; router pins provider `max_retries: 0` to avoid squaring retries), the next group runs. `max_fallbacks` default **5**. Three maps: `fallbacks` (generic), `context_window_fallbacks`, `content_policy_fallbacks`. Cooldown: `allowed_fails` (example 3/min) + `cooldown_time` (example 30s). Health-check routing removes deployments before users hit them; if **all** are unhealthy the filter is **bypassed**.

**Portkey specifics**: `strategy.mode: fallback` + `on_status_codes: [429, 503]`. Circuit breaker: `failure_threshold` + `minimum_requests`; `cooldown_interval` min 30s; default failure codes >500 -- you must explicitly list 429 if rate-limit should open the circuit. Retries: up to 5; exponential 1s-16s; 60s cumulative cap.

**The critical rule**: **Never fallback on spend-cap 429.** OpenAI `organization_spend_limit_exceeded` / `project_spend_limit_exceeded` / `credit_balance_exhausted` are NOT `slow_down`. Fail over on `slow_down`, 503, and regional 5xx. Deterministic last hop must not call a paid API. Mid-flight stream errors: no retry of the same stream (tokens already billed).

### 2.6 Checkpoint / Resume

For long-running agents (>4 hours), systems without state persistence have a 90% higher risk of total task failure. LangChain's 2026 State of Agent Engineering ties >60% of production incidents to state management.

**Three granularity models**:

| Model | Mechanism | Trade-off |
|-------|-----------|-----------|
| **Node-level (LangGraph)** | Every graph node triggers a write. PostgresSaver for production; **never MemorySaver** (lost on process death) | 50-step workflow = 50 persisted states. Supports time travel, HITL interrupts |
| **Activity-level (Temporal)** | Each Activity recorded in Event History. Replay skips completed Activities. Append-only, compacted | More efficient for high-step-count workflows |
| **Explicit commit points** | Developer inserts save calls at "safe" boundaries | Coarser granularity, lowest overhead |

**Decision heuristic**: If resuming saves >5 min of compute or >$2 in LLM tokens, checkpoint there.

**LangGraph details**: Checkpoint = `StateSnapshot` at a super-step. Durability modes: `"exit"` (only on success/error/HITL -- fastest, no mid-graph crash recovery), `"async"` (default -- persist while next step runs; small loss window), `"sync"` (persist before next step). Node bodies are **at-least-once** (retry, `interrupt()` resume, and time-travel restart the node from the top). PostgresSaver: `ConnectionPool`, `autocommit=True`, `row_factory=dict_row`; `prepare_threshold=None` behind PgBouncer. Agent Server: `DATABASE_URI` (not `POSTGRES_URI`). In-process two `invoke`s on one `thread_id` **race**; Agent Server leases at most 1 run/thread.

**Temporal details**: LangGraph plugin (public preview, `temporalio[langgraph]`): graph = Workflow; every node/task must set `execute_in: "activity"|"workflow"`; Activities get timeouts/retries/heartbeats; Workflow nodes must be deterministic. HITL `interrupt()` waits on a Temporal signal (zero CPU). Use **InMemorySaver** -- Temporal IS the durability; a second Postgres checkpointer is redundant and can diverge. Disable SDK-internal LLM retries so one retry policy owns backoff.

**Exactly-once is a lie you approximate**: Temporal Activities are **at-least-once** (worker completes, crashes before completion is recorded -> retry). At-most-once = `maximumAttempts: 1` (zero times possible). Idempotency key = **Workflow Run ID + Activity ID** passed to Stripe-style APIs. Saga: register a compensating Activity before each mutating step; on failure run compensations reversed; compensations must themselves be idempotent.

---

## 3. Architecture & System Design

### 3.1 Full-Stack Production Topology

```
+----------------------------------------------------------------------------------+
| CLIENTS / SURFACES                                                                |
|  GitHub PR / Argo CD | Chat / SSE widget | Approver console | GPU Operator        |
+----------+-----------+-------------------+------------------+---------------------+
           | TLS + session JWT (tenant/roles FROM TOKEN) + correlation-id
           | signed digest + Secret (not layers)
           v
+----------------------------------------------------------------------------------+
| CONTROL PLANE  (CI + GitOps + gateway policy)                                     |
|                                                                                   |
|  +------------+  +-------------+  +------------+  +-----------+  +-----------+   |
|  | Edge       |->| Input PII   |->| RELEASE    |->| GATEWAY   |->| BUDGET    |   |
|  | SSO, cid,  |  | redact      |  | Cosign +   |  | SKU alias,|  | tenant    |   |
|  | thread_id  |  | BEFORE      |  | SBOM, pin  |  | canary wt,|  | max_budget|   |
|  |            |  | vendor or   |  | prompt SHA |  | circuit,  |  | WITH DB   |   |
|  |            |  | checkpoint  |  | + snapshot |  | fallback  |  | 80% alert |   |
|  +------------+  +-------------+  +------------+  +-----------+  +-----------+   |
|                                                                                   |
|  RELEASE LEDGER: image@digest; prompt@SHA; model=gpt-5.4-2026-03-05              |
|  CI clock != serving clock != PEP clock != checkpoint clock                       |
|  Argo AnalysisRun: schema/PII/$/TTFT/guardrail FP -- not just 5xx               |
|                                                                                   |
|  +------------+  +-----------+                        +------------------+        |
|  | Circuit    |  | Fallback  |                        | SIGTERM / drain  |        |
|  | ONE per    |  | primary-> |                        | request_drain()  |        |
|  | VENDOR +   |  | secondary |                        | super-step; NIM  |        |
|  | one MCP    |  | vendor -> |                        | live!=ready; SSE |        |
|  | cluster    |  | determin. |                        | no retry mid-    |        |
|  |            |  | NEVER on  |                        | stream           |        |
|  |            |  | spend 429 |                        |                  |        |
|  +------------+  +-----------+                        +------------------+        |
+----------------------------------------------------------------------------------+
           |                               |
           v                               v
+---------------------------------+  +------------------------------------------+
| DATA PLANE  SERVING              |  | TOOL PROXIES (MCP egress PEP)             |
|                                  |  |                                           |
| Agent Server / worker            |  | Zero-Trust: OAuth 2.1, RFC 8707 aud      |
| LiteLLM / Portkey gateway        |  | Tool allowlists (compiled in image or    |
| Optional NIM/vLLM (separate img) |  |   signed ConfigMap)                       |
| Clock = TTFT / e2e               |  | CI: sim MCP, NO charge                   |
| GPU only if self-host            |  | PROD: gateway PEP, NetworkPolicy deny    |
+----------------------------------+  | gVisor for untrusted bash                |
           |                          +-------------------------------------------+
           v                               |
+---------------------------------+  +-----v-------------------------------------+
| PERSISTENCE (resume identity)    |  | TELEMETRY / OBSERVABILITY SINKS           |
|                                  |  |                                           |
| +-------------+ +-------------+  |  | +--------+ +--------+ +--------+ +-----+ |
| | CHECKPOINT  | | TEMPORAL    |  |  | | Audit  | | Metrics| | Traces | |Usage| |
| | Postgres /  | | event hist. |  |  | | (WORM) | | gen_ai | | OTel   | |auth | |
| | Mongo;      | | WorkflowId  |  |  | | cid,   | | .client| | gen_ai | |on   | |
| | Encrypted   | | Activity id |  |  | | tenant,| | .token | | .*     | |term | |
| | Serializer  | | InMemorySvr |  |  | | SHA,   | | .usage | | Coll.  | |evnt | |
| +-------------+ +-------------+  |  | | rail   | | 100%;  | | guard- | |$/1k | |
| +-------------+ +-------------+  |  | | decis. | | TTFT;  | | rail   | |mix  | |
| | REDIS       | | KAFKA / SQS |  |  | +--------+ +--------+ +--------+ +-----+ |
| | pub/sub +   | | tool bus +  |  |  +-------------------------------------------+
| | queue ONLY  | | DLQ; not    |  |
| |             | | chat tokens |  |
| +-------------+ +-------------+  |
| token vault NOT in checkpoints   |
+----------------------------------+

                 GUARDRAIL PEP (own clock -- sidecar / IGW VirtualModel)
                 +-------------------------------------------------------+
                 | Prompt Guard 2 / heuristics -> Presidio mask -> schema |
                 | -> Llama Guard / content-safety NIM -> opt self-check  |
                 | input BEFORE vendor; output BEFORE user; tool args JSON|
                 | IORails parallel hides wall-clock, NOT dollar cost     |
                 +-------------------------------------------------------+
```

### 3.2 Kubernetes Probe Configuration

**The HPA blind spot**: AI agents spend most compute time waiting for LLM API responses. CPU utilization stays low even when the system is saturated -- CPU reads 5% while GPU SM utilization spikes to 95%. HPA sees nothing alarming and does not scale.

| Workload | Liveness | Readiness | Startup |
|----------|----------|-----------|---------|
| Agent Server | `/ok` (process up) | `/ok` + Postgres reachable or queue worker listening | Short; image is small |
| NIM / vLLM | `/v1/health/live` (proxy up, no backend) | `/v1/health/ready` (weights in HBM) | Operator default failureThreshold 120 x period 10s = **20 min** |
| Guardrail classifier | process | model loaded | Llama Guard 4 12B != Prompt Guard 2 BERT |

**Inverting live/ready on NIM sends traffic to a loading GPU and then OOM-kills it.** Distroless often lacks `curl`: use a Kubernetes HTTP probe against `/ok`.

### 3.3 Model Serving Infrastructure

| Framework | Throughput | TTFT p95 | Key Advantage | Status |
|-----------|-----------|----------|---------------|--------|
| **vLLM** | Highest (PagedAttention, <4% memory waste) | 0.89s | 24x higher throughput than TGI at high concurrency; 85-92% GPU util at 100+ users. #1 OSS project by contributors (Octoverse 2025) | Active, production standard |
| **TGI** | Moderate | 0.45s (lowest) | 13x speedup on 200K+ token prompts (TGI v3). Lowest TTFT | **Maintenance mode** -- HuggingFace recommends vLLM or SGLang |
| **NVIDIA NIM** | ~15% edge over raw vLLM | Similar to vLLM | Pre-optimized TensorRT-LLM containers, zero-config, enterprise security scanning | Active, licensed |
| **SGLang** | 29% above vLLM on shared-context | Similar to vLLM | RadixAttention for multi-turn/agentic workloads. Best for chatbots, RAG, agents | Active, rising |

**Production pattern**: Ray Serve + vLLM workers for continuous batching and autoscaling. For 90% of teams, vLLM is the right choice. SGLang wins specifically for shared-context workloads. TGI is effectively deprecated.

**Infrastructure choice matrix**:

| Option | Cost | Batching Efficiency | Best For |
|--------|------|-------------------|----------|
| **Serverless GPU** (Modal ~$4.50/hr, RunPod ~$2.50/hr) | Per-use | 12-18% (naive) | Bursty workloads under 30% GPU utilization |
| **Containers** (Cloud Run) | Scale-to-zero | Variable | LLM streaming, hybrid (no platform timeout for streaming) |
| **Dedicated GPU** (K8s + vLLM) | Reserved | 65-80% | Sustained high-throughput, data residency constraints |
| **Edge** (Cloudflare Workers) | Per-request | N/A | Sub-ms cold starts, 300+ locations, 1-7B quantized only |

---

## 4. Key Algorithms & Mechanics

### 4.1 Autoscaling: KEDA Over HPA

**Why CPU HPA fails for AI**: Agents waiting on APIs show low CPU -- HPA no-ops while the queue is the real SLO signal. The 2026 CNCF survey found 66% of organizations running GenAI on K8s, driven by Dynamic Resource Allocation (DRA) and native gang scheduling.

**KEDA solves the right signal problem**: Multi-trigger OR semantics (take the max across all signals) express "scale on whichever signal fires first." Two HPAs on one Deployment is a conflict; two triggers in one ScaledObject is the documented pattern.

**Scale signals -- what to use vs ignore**:

| Signal | Scale On? | Why |
|--------|----------|-----|
| CPU utilization | **No** | I/O-bound waiting for LLM responses; reads 5% at full saturation |
| GPU SM utilization | Supplementary | Spikes to 95% but can sit ~100% while batching regardless of queue |
| `vllm:num_requests_waiting` | **Yes (primary)** | Direct measure of inference queue pressure |
| Queue depth (Redis Streams/SQS) | **Yes (primary)** | KEDA's best trigger for async workloads |
| HTTP request rate | **Yes (supplementary)** | Good for API-fronted services |
| EPP flow control queue | **Yes (primary)** | llm-d/EPP: `llm_d_epp_flow_control_queue_size` |

**Cold start is the hard problem**: Every GPU pod restart means 3-10 minutes of image pull, weight loading, CUDA graph capture, KV cache warming. Solve with PVC-backed model storage first.

```yaml
# KEDA ScaledObject -- multi-trigger autoscaling for AI inference
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: inference-service-scaler
  namespace: ai-inference
spec:
  scaleTargetRef:
    name: inference-service
  pollingInterval: 15
  cooldownPeriod: 300             # 5 min cooldown to avoid thrashing
  minReplicaCount: 1              # Never scale to zero in prod
  maxReplicaCount: 10
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleUp:
          stabilizationWindowSeconds: 60      # Scale up fast
          policies:
            - type: Pods
              value: 2                         # Add up to 2 pods per 60s
              periodSeconds: 60
        scaleDown:
          stabilizationWindowSeconds: 300     # Scale down slowly
          policies:
            - type: Pods
              value: 1                         # Remove 1 pod at a time
              periodSeconds: 120
  triggers:
    # Trigger 1: Redis Streams queue depth (primary signal)
    - type: redis-streams
      metadata:
        address: redis.ai-inference.svc.cluster.local:6379
        stream: inference-requests
        consumerGroup: inference-workers
        pendingEntriesCount: "10"             # Scale when >10 pending
        lagCount: "5"                          # or consumer lag >5
    # Trigger 2: vLLM waiting requests (Prometheus)
    - type: prometheus
      metadata:
        serverAddress: http://prometheus.monitoring.svc.cluster.local:9090
        query: sum(vllm_num_requests_waiting{namespace="ai-inference"})
        threshold: "5"
    # Trigger 3: HTTP request rate (supplementary)
    - type: prometheus
      metadata:
        serverAddress: http://prometheus.monitoring.svc.cluster.local:9090
        query: sum(rate(http_requests_total{service="inference-service"}[2m]))
        threshold: "50"
    # KEDA multi-trigger OR semantics: takes MAX across all triggers
---
# Karpenter NodePool for GPU workloads
apiVersion: karpenter.sh/v1
kind: NodePool
metadata:
  name: gpu-inference
spec:
  template:
    spec:
      requirements:
        - key: "node.kubernetes.io/instance-type"
          operator: In
          values: ["p5.48xlarge", "g5.xlarge", "g6.xlarge"]
        - key: "karpenter.sh/capacity-type"
          operator: In
          values: ["on-demand", "spot"]
  limits:
    cpu: 128
    memory: 512Gi
    nvidia.com/gpu: 8
  disruption:
    consolidationPolicy: WhenEmptyOrUnderutilized
    consolidateAfter: 10m
```

### 4.2 Circuit Breaker State Machine

**One breaker per vendor** (not the judge). Do not trip on 429-with-Retry-After (that is throttle, not failure). Do not open the serving breaker because the judge 429'd.

```
           5xx/529/timeout rate >= threshold           probe success
  +--------+  ------------------------------------->  +------+  ------> CLOSED
  | CLOSED |                                          | OPEN |
  +---+----+  429 slow_down = throttle                +--+---+
      |       (stay CLOSED; sleep Retry-After)           | timer (e.g. 30s)
      |       spend 429 = PermanentError (no trip        v
      |         as "rate"; fail closed to determin.)  +----------+
      | success resets window                         | HALF_OPEN|-- probe fail --> OPEN
      +-----------------------------------------------| 1 cheap  |
                                                      | /ok      |
                                                      +----------+
```

**Half-open**: Probe with a cheap 1-token classify / `/ok`, not a 12-minute decode. After N consecutive transport failures, trip that vendor for T seconds (Portkey cooldown min 30s). If **all** open, both LiteLLM health-filter and Portkey breaker **bypass** -- shed at the edge with `429 Retry-After`.

### 4.3 Model Routing

~70% of production traffic is simple (intent classification, text rewriting) that a small model handles at full quality. RouteLLM reports >85% cost reduction on MT-Bench while retaining 95% of GPT-4 quality.

```
Incoming request
    |
    v
+------------------+
| Complexity Router |
| (RouteLLM / custom classifier)
+------------------+
    |
    +-- Simple (70%)  --> Haiku / GPT-4o-mini  ($0.25-$0.15/1M input)
    +-- Medium (20%)  --> Sonnet / GPT-4o      ($3.00-$2.50/1M input)
    +-- Complex (10%) --> Opus / o3             ($15.00/1M input)
```

### 4.4 OTel Metering (Not the Invoice)

Semantic conventions are **Development** status (not Stable). Key attributes: `gen_ai.usage.input_tokens` (includes cache), `output_tokens`, `cache_creation.input_tokens` / `cache_read.input_tokens`, `reasoning.output_tokens`. When billed != consumed, **report billed**.

Metric `gen_ai.client.token.usage` histogram with `gen_ai.token.type=input|output` at **100%** even when traces sample. LiteLLM emits `gen_ai.usage.cost` (USD, computed) plus TTFT/TPOT. `litellm.call_id` joins traces to Spend Logs.

**Budgets are not OTel**: LiteLLM enforces against the **database**; `max_budget` **fails open** with no DB. Authoritative remaining: `litellm_remaining_team_budget_metric{team_id}`.

### 4.5 Complexity Analysis

Let L = prompt chars, T = tokens, G = fallback groups, R = HTTP retries, H = Temporal history events, N = golden items, Q = EPP queue depth.

| Operation | Complexity | Notes |
|-----------|-----------|-------|
| Distroless build | Theta(layers) | Runtime start = process + venv, not CUDA load. NIM Ready = weight load (20 min) |
| Eval gate | Theta(N x R_rep) | Target calls -- bill lives in evals, not this plane |
| PEP regex/Presidio | Theta(L) | Prompt Guard 2: Theta(ceil(T/512)) parallel BERT windows |
| OTel histogram update | O(1) per call | 100% metrics, sampled content |
| Fallback worst case | O(G x R) paid calls | This IS mix M3. Cap with `max_fallbacks` + retry budgets |
| LangGraph resume | Theta(state_size) | Temporal replay: Theta(H) Workflow decisions, 0 re-calls of completed Activities |
| HPA calculation | `ceil(current x current/desired)` every ~15s | Wrong metric (CPU) => O(1) no-op while Q is the SLO |

---

## 5. Token Economics & Cost Analysis

### 5.1 Per-Request Cost Attribution

```
Request_Cost = (Input_Tokens * Input_Rate) + (Output_Tokens * Output_Rate)
             - Cache_Discount - Batch_Discount
             + Guardrail_Cost + Embedding_Cost

where:
  Cache_Discount  = Cached_Input_Tokens * Input_Rate * 0.9    (90% savings)
  Batch_Discount  = (Input_Cost + Output_Cost) * 0.5          (50% for batch API)
  Guardrail_Cost  = Guard_Model_Tokens * Guard_Model_Rate     (Prompt Guard + LlamaGuard)
```

### 5.2 Model Pricing (Sep 2026)

| Model | Input / 1M tokens | Output / 1M tokens |
|-------|-------------------|---------------------|
| GPT-5.4 | $2.50 | $15.00 |
| Claude Sonnet 4.6 | $3.00 | $15.00 |
| Claude Opus 4 | $15.00 | $75.00 |
| Claude Haiku 4.5 | $1.00 | $5.00 |
| GPT-4o | $2.50 | $10.00 |
| GPT-4o-mini | $0.15 | $0.60 |

### 5.3 Mix Cost Analysis (Skeleton W: 3,000 in + 800 out, no cache)

| Hop | $/request (inferred) | $/1k (inferred) |
|-----|---------------------|-----------------|
| GPT-5.4 primary | (3000x2.50 + 800x15)/1M = $0.0195 | **$19.50** |
| Sonnet 4.6 secondary | (3000x3 + 800x15)/1M = $0.021 | **$21.00** |
| Haiku 4.5 cheap fallback | (3000x1 + 800x5)/1M = $0.007 | **$7.00** |
| Deterministic template | $0 | **$0** |

| Mix | Formula | $/1k (inferred) |
|-----|---------|-----------------|
| **M1** (92% primary, 6% secondary, 2% deterministic) | 0.92x19.50 + 0.06x21.00 + 0.02x0 | **$19.20** |
| **M2** (85% GPT-5.4, 10% Haiku, 5% refuse) | content-policy fallback | **$17.28** |
| **M3** (retry storm: 4 paid calls on 5% of traffic) | M1 + 0.05x(3x19.50+21) | **~$23.20** |
| 3-node agent graph (GPT-5.4) | M1 x ~3 | **~$58.50** |

**Worked example -- single agentic request with 3 tool calls (Claude Sonnet 4)**:

```
System prompt: 4,000 tokens (cached after first request)
User query: 500 tokens
3 tool calls: 1,500 tokens each (input + output combined)
Final response: 800 tokens output

First request (no cache):
  Input:  4,000 + 500 + (3 * 750 avg input) = 6,750 tokens  = $0.020
  Output: 800 + (3 * 750 avg output) = 3,050 tokens          = $0.046
  Guardrail: Prompt Guard ~100 tokens + LlamaGuard ~200       = $0.001
  Total: ~$0.067

Subsequent requests (system prompt cached):
  Input:  4,000 * $0.3/1M (cached) + 2,750 * $3/1M           = $0.010
  Output: 3,050 * $15/1M                                      = $0.046
  Total: ~$0.057  (15% savings from cache alone)

With batch API (async-tolerant):
  Total: ~$0.029  (57% savings from cache + batch combined)
```

### 5.4 GPU Infrastructure Costs

**H100 cost by provider (Sep 2026)**:

| Provider | On-Demand (/GPU-hr) | Spot (/GPU-hr) | 1-Year Reserved |
|----------|-------------------|---------------|-----------------|
| AWS p5.48xlarge | $12.29 | N/A for H100 | ~$7.38 (40% off) |
| Azure ND H100 v5 | $12.25-$14.50 | Varies | Steepest discount curve |
| GCP a3-highgpu-8g | $9.00-$11.50 | ~$2.25 (60-91% off) | ~$6.00 (35% off) |
| RunPod | $2.99 | $1.19 | N/A |
| CoreWeave | $6.15 | N/A | Volume discounts |
| Lambda Labs | $3.99 | N/A | N/A |

**Idle GPU reality**: Cast.ai 2026 reports fleet-average GPU utilization is **~5%**. An idle H100 costs **~$165/day** (inferred from ~$6.88/H100-hr aggregator pricing). Break-even vs API mix M1: ~8,600 requests/day on that GPU if it replaced GPT-5.4 and quality matched (it usually does not).

**Key ratio**: H100 costs ~3x A100 and delivers ~3x training throughput, making cost-per-token roughly equivalent across GPU generations. The real savings come from software optimization (vLLM batching, quantization), not GPU generation.

**Self-hosted vs API break-even**:

```
2x H100 for Llama 3.3 70B + LlamaGuard 3 8B:
  On-demand (AWS):     2 * 730 * $12.30 = $17,958/month
  Spot (RunPod):       2 * 730 * $1.19  = $1,737/month   (90% savings, preemption risk)
  Reserved 1yr (AWS):  2 * 730 * $7.38  = $10,775/month  (40% discount)

Break-even: Self-hosted wins when sustained inference exceeds
  ~$6K/month in API spend (on-demand) or ~$2K/month (spot).
```

### 5.5 Latency SLA Targets

| Metric | Target | Measured Reality | Mitigation |
|--------|--------|-----------------|------------|
| **TTFT** | <1.0s p95 | vLLM: 0.89s; TGI: 0.45s | SGLang RadixAttention; prompt caching reduces TTFT 13-31% |
| **Guardrail overhead** | <80ms p99 | 15-60ms p50 co-located | Batched classifier inference; 5-20ms accumulation window; FP8 quantization |
| **Cold start (GPU pod)** | <3 min | 3-10 min typical | PVC-backed model storage; provisioned concurrency; weight pre-caching |
| **e2e agent turn** | <5s p95 | 2-15s depending on tool count | Parallel tool calls; streaming; cached system prompts |
| **SSE idle timeout** | No premature close | nginx default 60s kills p99 | `proxy-read-timeout` > p99 silence (e.g. 3600s); `proxy-buffering: off`; keepalive comments every 20-30s (Cloudflare ~100s idle -> 524) |
| **NIM Ready** | Minutes | Operator budgets 20 min | `startupProbe` 20 min; prePromotion against preview that is already Ready |
| **Drain** | Finish current super-step | terminationGracePeriodSeconds >= p99 decode | Fail readiness on SIGTERM; do not retry the same stream |

### 5.6 Non-Functional Requirements

| NFR | Working Target | Implementation |
|-----|---------------|----------------|
| **Availability** | 99.9% on completed turns with `finish_reason in {stop, tool_calls}` (vendor spend-429 is intended shed, not an outage) | Multi-provider fallback chains (single providers deliver 99.46-99.76%). Serving must not depend on Hub/LangSmith UI |
| **RPO** | <5 min for agent state | PostgresSaver with WAL replication. Checkpoints: last durable super-step. Temporal: last completed Activity. In-flight SSE: gone |
| **RTO** | <15 min for full recovery | Pre-provisioned warm standby; ArgoCD instant rollback; PVC-backed models (no re-download). GPU: Ready <= 20 min. HITL days: Temporal signal |
| **Cost governance** | <$X/month per feature | Per-request cost attribution tags. Hard caps per user. Soft alerts at 50%/80%. Rolling baseline deviation alerts. DB required for budget enforcement |
| **Compliance** | SOC 2 Type II, HIPAA, GDPR, EU AI Act | Immutable audit trail per model call. Model version traceability. VPC isolation for PHI. HITL gates for high-risk. PII redaction in telemetry |
| **Consistency** | Lease <=1 run/thread; graph+checkpointer schema pinned in same image; Temporal Workflow deterministic | Canary RS must not write checkpoints the stable graph cannot read |

### 5.7 Back-Pressure Design

1. Admit the user iff the serving breaker is closed **and** tenant budget remaining > estimated turn.
2. Shed in order: drop online content sample -> keep 100% `gen_ai.*` + all errors -> pause soft rails (Llama Guard) -> keep schema/PII/allowlist -> **never** retry spend-cap 429 into another vendor.
3. Size in-flight streams (not just RPM -- OpenAI TPM counted when request completes) and EPP queue, not CPU.
4. Per-tenant concurrency at the gateway so one noisy neighbor cannot fill `N_JOBS_PER_WORKER` (default 10) across the fleet.
5. If all fallback targets OPEN: do **not** bypass into a dead primary -- edge 429 `Retry-After`.

**429 taxonomy (same status, four different meanings)**:

| Source / Code | Meaning | Retry? | Fallback? |
|---------------|---------|--------|-----------|
| OpenAI `slow_down` | RPM/TPM/burst | Yes, jitter + `Retry-After` | Optional other vendor |
| `organization_spend_limit_exceeded` / `project_spend_limit_exceeded` | Your hard cap | **No** -- page finance | Deterministic only |
| `organization_usage_limit_exceeded` | OpenAI-assigned tier cap | No | Do not dump onto Anthropic without a budget |
| `credit_balance_exhausted` | Prepaid empty | No | No |
| LiteLLM `Budget has been exceeded` (often 400 `auth_error`) | Team/key cap | No | No |
| Gateway overload 429 with `Retry-After` | You shed | Back off | Do not retry into the same pool |

---

## 6. Production Patterns & Code

### 6.1 Integrated Ops/Runtime System

This production code encodes all key patterns from Sections 1-5: spend-cap handling, guardrail sandwich, circuit breakers, fallback chains, checkpoint resume, and idempotent tools.

```python
#!/usr/bin/env python3
"""Ops/runtime plane: spend caps, guardrail sandwich, checkpoint resume.
   Offline self-test: no network, no LLM, no K8s, no Temporal cluster.
"""
from __future__ import annotations
import asyncio, hashlib, json, logging, random, re, time, uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

INITIAL_RETRY_DELAY, MAX_RETRY_DELAY, SDK_DEFAULT_MAX_RETRIES = 0.5, 8.0, 2
SOFT_BUDGET_FRACTION, MAX_FALLBACKS = 0.80, 5
USD_GPT54, USD_SONNET, USD_HAIKU = 0.0195, 0.021, 0.007  # skeleton W [inferred]
GATEWAY_AUD = "https://mcp.gateway.example"
PINNED_SKU = "gpt-5.4-2026-03-05"
GRAPH_HASH = "graph-v12-sha"
T = TypeVar("T")
WRITE_TOOLS = frozenset({"refund.execute", "payments.charge"})
ALLOWED_TOOLS = frozenset({"kb.search", "lookup_invoice", "refund.execute"})
SPEND_CODES = frozenset({
    "organization_spend_limit_exceeded", "project_spend_limit_exceeded",
    "organization_usage_limit_exceeded", "credit_balance_exhausted",
})
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


# --- Error hierarchy: PermanentError/SpendCapError are NEVER retried ---

class TransientError(Exception):
    def __init__(self, msg: str, retry_after: float | None = None,
                 status: int | None = None, code: str | None = None) -> None:
        super().__init__(msg)
        self.retry_after, self.status, self.code = retry_after, status, code

class PermanentError(Exception): pass

class SpendCapError(PermanentError):
    """Vendor or gateway hard cap. NEVER retry. NEVER paid-failover."""

class CircuitOpenError(TransientError): pass

def is_spend_cap(code: str | None, status: int | None) -> bool:
    if code in SPEND_CODES:
        return True
    return status == 400 and code == "auth_error"  # LiteLLM team budget


# --- Circuit breaker: one per VENDOR, not the judge ---

class BreakerState(Enum):
    CLOSED = "closed"; OPEN = "open"; HALF_OPEN = "half_open"

class BreakerStateMachine:
    """Do not trip on 429-with-Retry-After (that is throttle, not failure)."""
    def __init__(self, name: str, failure_threshold: int = 5,
                 recovery_seconds: float = 30.0, half_open_max: int = 1) -> None:
        self.name, self.failure_threshold = name, failure_threshold
        self.recovery_seconds, self.half_open_max = recovery_seconds, half_open_max
        self._state, self._failures, self._opened_at = BreakerState.CLOSED, 0, 0.0
        self._half_open_inflight, self._lock = 0, asyncio.Lock()

    async def allow(self) -> None:
        async with self._lock:
            if (self._state is BreakerState.OPEN and
                    (time.monotonic() - self._opened_at) >= self.recovery_seconds):
                self._state, self._half_open_inflight = BreakerState.HALF_OPEN, 0
            if self._state is BreakerState.OPEN:
                raise CircuitOpenError(f"circuit_open:{self.name}")
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_inflight >= self.half_open_max:
                    raise CircuitOpenError(f"circuit_half_open_busy:{self.name}")
                self._half_open_inflight += 1

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = self._half_open_inflight = 0
            self._state = BreakerState.CLOSED

    async def record_failure(self, *, trip: bool = True) -> None:
        async with self._lock:
            if not trip:
                return  # slow_down throttle -- stay closed
            self._failures += 1
            if (self._state is BreakerState.HALF_OPEN or
                    self._failures >= self.failure_threshold):
                self._state, self._opened_at = BreakerState.OPEN, time.monotonic()
                self._half_open_inflight = 0


# --- Retry with jitter: ONLY for transient HTTP errors ---

async def retry_with_jitter(
    fn: Callable[[], Awaitable[T]], *, log, attempts: int = 3,
    base: float = INITIAL_RETRY_DELAY, cap: float = MAX_RETRY_DELAY,
) -> T:
    """Full jitter. Never wraps PermanentError/SpendCapError/CircuitOpenError."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return await fn()
        except (PermanentError, CircuitOpenError):
            raise
        except TransientError as exc:
            if is_spend_cap(exc.code, exc.status):
                raise SpendCapError(f"spend_cap:{exc.code}") from exc
            last = exc
            if i == attempts - 1:
                break
            ra = exc.retry_after
            sleep_s = (ra if ra is not None and 0 < ra <= 60
                       else random.random() * min(cap, base * (2 ** i)))
            log.warning("http_retry attempt=%s sleep=%.3f err=%s", i + 1, sleep_s, exc)
            await asyncio.sleep(sleep_s)
    raise last


# --- Spend ledger: fail-closed admission, soft alert at 80% ---

class SpendLedger:
    """DB required in prod -- this is the in-process twin."""
    def __init__(self, tenant_caps: dict[str, float]) -> None:
        self.tenant_caps = dict(tenant_caps)
        self.spent: dict[str, float] = {k: 0.0 for k in tenant_caps}
        self.alerts: list[str] = []

    def remaining(self, tenant: str) -> float:
        return round(self.tenant_caps[tenant] - self.spent[tenant], 6)

    def admit(self, tenant: str, estimated: float) -> None:
        if tenant not in self.tenant_caps:
            raise PermanentError("unknown_tenant")
        if self.remaining(tenant) < estimated:
            raise SpendCapError(f"tenant_budget:{tenant}")

    def record(self, tenant: str, actual: float) -> None:
        self.spent[tenant] = round(self.spent[tenant] + actual, 6)
        cap = self.tenant_caps[tenant]
        if cap and self.spent[tenant] >= round(SOFT_BUDGET_FRACTION * cap, 6):
            self.alerts.append(tenant)


# --- Guardrail PEP: Presidio sandwich + allowlist + WORM audit ---

class GuardrailDecision(Enum):
    ALLOW = "allow"; BLOCK = "block"; MASK = "mask"

class PiiSandwich:
    """Presidio-shaped stub: replace emails with {{EMAIL_n}}, restore after model."""
    def __init__(self) -> None:
        self.session: dict[str, dict[str, str]] = {}

    def anonymize(self, cid: str, text: str) -> str:
        mapping = self.session.setdefault(cid, {})
        def repl(match: re.Match[str]) -> str:
            token = "{{EMAIL_%d}}" % (len(mapping) + 1)
            mapping[token] = match.group(0)
            return token
        return EMAIL_RE.sub(repl, text)

    def deanonymize(self, cid: str, text: str) -> str:
        out = text
        for token, orig in self.session.get(cid, {}).items():
            out = out.replace(token, orig)
        return out

@dataclass
class GuardrailPep:
    sandwich: PiiSandwich
    max_chars: int = 32_000
    worm: list[dict[str, Any]] = field(default_factory=list)

    def _audit(self, cid: str, stage: str, decision: GuardrailDecision, rail: str):
        self.worm.append({"correlation_id": cid, "stage": stage,
                          "decision": decision.value, "rail": rail, "hide_inputs": True})

    def check_input(self, cid: str, text: str) -> tuple[GuardrailDecision, str]:
        if len(text) > self.max_chars:
            self._audit(cid, "input", GuardrailDecision.BLOCK, "length")
            return GuardrailDecision.BLOCK, text
        if "ignore previous instructions" in text.casefold():
            self._audit(cid, "input", GuardrailDecision.BLOCK, "prompt_guard")
            return GuardrailDecision.BLOCK, text
        masked = self.sandwich.anonymize(cid, text)
        decision = GuardrailDecision.MASK if masked != text else GuardrailDecision.ALLOW
        self._audit(cid, "input", decision, "presidio_mask")
        return decision, masked

    def check_output(self, cid: str, text: str) -> tuple[GuardrailDecision, str]:
        restored = self.sandwich.deanonymize(cid, text)
        self._audit(cid, "output", GuardrailDecision.ALLOW, "deanonymize")
        return GuardrailDecision.ALLOW, restored

    def check_tool(self, name: str, args: dict[str, Any]) -> None:
        if name not in ALLOWED_TOOLS:
            raise PermanentError(f"tool_not_allowlisted:{name}")
        if name in WRITE_TOOLS and float(args.get("amount", 0) or 0) > 50:
            raise PermanentError("refund_cap")


# --- Checkpoint store: resume identity with graph hash validation ---

@dataclass(frozen=True)
class Checkpoint:
    thread_id: str; checkpoint_id: str; super_step: int
    next_nodes: tuple[str, ...]; channel_values: dict[str, Any]
    completed_activity_ids: tuple[str, ...]; graph_bytes_hash: str

class CheckpointStore:
    def __init__(self) -> None:
        self._rows: dict[str, Checkpoint] = {}
    def put(self, cp: Checkpoint) -> None:
        self._rows[cp.thread_id] = cp
    def latest(self, thread_id: str) -> Checkpoint | None:
        return self._rows.get(thread_id)
    def resume(self, thread_id: str, live_graph_hash: str) -> Checkpoint:
        cp = self.latest(thread_id)
        if cp is None: raise PermanentError("no_checkpoint")
        if cp.graph_bytes_hash != live_graph_hash: raise PermanentError("restore_mismatch")
        return cp


# --- Tool bus: at-least-once with idempotency key ---

class ToolBus:
    """Same idempotency key returns the receipt; does not re-charge."""
    def __init__(self) -> None:
        self.receipts: dict[str, dict[str, Any]] = {}
        self.attempts: dict[str, int] = {}
    def call(self, key: str, fn: Callable[..., dict[str, Any]], *args) -> dict[str, Any]:
        if key in self.receipts:
            return self.receipts[key]  # idempotent: return cached receipt
        self.attempts[key] = self.attempts.get(key, 0) + 1
        result = fn(*args)
        self.receipts[key] = result
        return result


# --- Serving runtime: ties all components together ---

@dataclass(frozen=True)
class ModelHop:
    name: str; usd: float; output: str = "ok"; fail: Exception | None = None

class ServingRuntime:
    def __init__(self, log, ledger: SpendLedger, pep: GuardrailPep,
                 store: CheckpointStore) -> None:
        self.log, self.ledger, self.pep, self.store = log, ledger, pep, store
        self.tools = ToolBus()
        self.breakers = {
            "primary": BreakerStateMachine("primary", failure_threshold=3, recovery_seconds=0.05),
            "secondary": BreakerStateMachine("secondary", failure_threshold=3, recovery_seconds=0.05),
        }
        self.degraded = False

    async def complete(self, tenant: str, hops: list[ModelHop]) -> tuple[str, float]:
        """Execute fallback chain: primary -> secondary -> deterministic."""
        paid = [h for h in hops if h.name != "deterministic"]
        if not paid:
            self.degraded = True
            return hops[0].output, 0.0
        self.ledger.admit(tenant, paid[0].usd)
        last: Exception | None = None
        used = 0
        for hop in hops:
            if used >= MAX_FALLBACKS: break
            if hop.name == "deterministic":
                self.degraded = True
                return hop.output, 0.0
            if isinstance(last, SpendCapError): break  # NEVER paid-failover on spend cap
            try:
                await self.breakers[hop.name].allow()
                async def call(h=hop):
                    if h.fail is not None: raise h.fail
                    return h.output
                out = await retry_with_jitter(call, log=self.log)
                await self.breakers[hop.name].record_success()
                self.ledger.record(tenant, hop.usd)
                return out, hop.usd
            except SpendCapError:
                break
            except CircuitOpenError:
                last = CircuitOpenError("open")
                continue
            except TransientError as exc:
                last = exc
                trip = not (exc.code == "slow_down" and exc.status == 429)
                await self.breakers[hop.name].record_failure(trip=trip)
                used += 1
                continue
        self.degraded = True
        return "try again", 0.0

    async def run_turn(self, cid, tenant, thread_id, user_text, hops):
        decision, masked = self.pep.check_input(cid, user_text)
        if decision is GuardrailDecision.BLOCK:
            return {"status": "blocked", "output": None, "usd": 0.0}
        # Execute LLM with fallback chain
        text, usd = await self.complete(tenant, hops)
        if text != "try again":
            model_out = text.replace("USER", masked)
            out_dec, visible = self.pep.check_output(cid, model_out)
            if out_dec is GuardrailDecision.BLOCK:
                return {"status": "blocked", "output": None, "usd": usd}
        else:
            visible = text
        # Checkpoint the turn
        cp = self.store.latest(thread_id)
        step = 0 if cp is None else cp.super_step + 1
        new_cp = Checkpoint(
            thread_id=thread_id, checkpoint_id=str(uuid.uuid4()),
            super_step=step, next_nodes=("end",),
            channel_values={"masked": masked, "visible": visible},
            completed_activity_ids=("llm",), graph_bytes_hash=GRAPH_HASH)
        self.store.put(new_cp)
        return {"status": "ok", "output": visible, "usd": usd, "step": step}
```

**Key behaviors encoded**:
- Full-jitter HTTP retries; `Retry-After` honored iff 0 < t <= 60; `PermanentError`/`SpendCapError` are never retried
- Spend-cap 429 codes raise `SpendCapError` before sleep -- no paid secondary hop
- Vendor breaker is distinct from judge; `slow_down` throttles (trip=False); 5xx trips OPEN
- Guardrail sandwich: email -> `{{EMAIL_1}}` -> model -> deanonymize; allowlist + refund cap; WORM `hide_inputs=true`
- Checkpoint resume: put after turn; hash mismatch is `restore_mismatch`; tool bus returns receipt on same key

### 6.2 Guardrail Pipeline with Parallel Execution

```python
"""Six-layer defense-in-depth with parallel execution for latency optimization."""
import asyncio, re, time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import httpx

class GuardrailAction(Enum):
    ALLOW = "allow"; BLOCK = "block"; SANITIZE = "sanitize"

@dataclass
class GuardrailResult:
    action: GuardrailAction; layer: str; reason: Optional[str] = None
    sanitized_text: Optional[str] = None; latency_ms: float = 0.0

# Layer 1: Input validation (deterministic, <1ms)
MAX_INPUT_LENGTH = 32_000
BLOCKED_ENCODINGS = re.compile(
    r"(\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}|&#x?[0-9a-fA-F]+;)", re.I)

def validate_input(text: str) -> GuardrailResult:
    start = time.perf_counter()
    if not text or not text.strip():
        return GuardrailResult(GuardrailAction.BLOCK, "input_validation",
                               "Empty input", latency_ms=(time.perf_counter()-start)*1000)
    if len(text) > MAX_INPUT_LENGTH:
        return GuardrailResult(GuardrailAction.BLOCK, "input_validation",
                               f"Exceeds {MAX_INPUT_LENGTH} chars",
                               latency_ms=(time.perf_counter()-start)*1000)
    if BLOCKED_ENCODINGS.search(text):
        cleaned = BLOCKED_ENCODINGS.sub("", text)
        return GuardrailResult(GuardrailAction.SANITIZE, "input_validation",
                               "Encoding evasion detected", cleaned,
                               (time.perf_counter()-start)*1000)
    return GuardrailResult(GuardrailAction.ALLOW, "input_validation",
                           latency_ms=(time.perf_counter()-start)*1000)

# Layer 2: Prompt injection detection (pattern-based, <1ms)
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts)", re.I),
    re.compile(r"you\s+are\s+now\s+(a|an)\s+", re.I),
    re.compile(r"<\|im_start\|>|<\|im_end\|>", re.I),
    re.compile(r"\[INST\]|\[/INST\]", re.I),
]

def detect_injection(text: str) -> GuardrailResult:
    start = time.perf_counter()
    for p in INJECTION_PATTERNS:
        if p.search(text):
            return GuardrailResult(GuardrailAction.BLOCK, "injection_detection",
                                   f"Injection pattern: {p.pattern[:50]}",
                                   latency_ms=(time.perf_counter()-start)*1000)
    return GuardrailResult(GuardrailAction.ALLOW, "injection_detection",
                           latency_ms=(time.perf_counter()-start)*1000)

# Layer 3: LLM safety gate (Llama Prompt Guard 2, 20-50ms)
async def llm_safety_gate(text: str, endpoint="http://localhost:8080/v1/classify"):
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(endpoint, json={"text": text, "model": "prompt-guard-2-86m"})
            r.raise_for_status()
            result = r.json()
            if result.get("label") == "unsafe" and result.get("score", 0) > 0.85:
                return GuardrailResult(GuardrailAction.BLOCK, "llm_safety_gate",
                    f"Unsafe (score={result['score']:.3f})",
                    latency_ms=(time.perf_counter()-start)*1000)
    except (httpx.HTTPError, httpx.TimeoutException):
        pass  # Fail-open: downstream output guardrails provide second defense
    return GuardrailResult(GuardrailAction.ALLOW, "llm_safety_gate",
                           latency_ms=(time.perf_counter()-start)*1000)

# Layer 4: PII redaction (output filter)
PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d{4}[- ]?){3}\d{4}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
}

def redact_pii(text: str) -> GuardrailResult:
    start = time.perf_counter()
    redacted, found = text, []
    for pii_type, pattern in PII_PATTERNS.items():
        if pattern.search(redacted):
            found.append(pii_type)
            redacted = pattern.sub(f"[REDACTED_{pii_type.upper()}]", redacted)
    if found:
        return GuardrailResult(GuardrailAction.SANITIZE, "pii_redaction",
                               f"PII redacted: {', '.join(found)}", redacted,
                               (time.perf_counter()-start)*1000)
    return GuardrailResult(GuardrailAction.ALLOW, "pii_redaction",
                           latency_ms=(time.perf_counter()-start)*1000)

# Layer 5: Tool-call gating
TOOL_ALLOWLIST = {
    "web_search": {"max_queries": 5},
    "database_query": {"max_queries": 3, "read_only": True},
    "file_read": {"allowed_paths": ["/data/", "/config/"]},
}

def validate_tool_call(tool_name: str, params: dict) -> GuardrailResult:
    start = time.perf_counter()
    if tool_name not in TOOL_ALLOWLIST:
        return GuardrailResult(GuardrailAction.BLOCK, "tool_gating",
                               f"Tool '{tool_name}' not allowlisted",
                               latency_ms=(time.perf_counter()-start)*1000)
    return GuardrailResult(GuardrailAction.ALLOW, "tool_gating",
                           latency_ms=(time.perf_counter()-start)*1000)

# Pipeline: parallel execution (200ms serial -> ~70ms parallel)
async def run_input_guardrails(text: str):
    validation = validate_input(text)
    if validation.action == GuardrailAction.BLOCK:
        return GuardrailAction.BLOCK, text, [validation]
    working = validation.sanitized_text or text
    # Layers 2 and 3 are independent -- run in parallel
    injection_task = asyncio.to_thread(detect_injection, working)
    safety_task = llm_safety_gate(working)
    injection_r, safety_r = await asyncio.gather(injection_task, safety_task)
    results = [validation, injection_r, safety_r]
    for r in results:
        if r.action == GuardrailAction.BLOCK:
            return GuardrailAction.BLOCK, text, results
    return GuardrailAction.ALLOW, working, results
```

### 6.3 Cost Tracking Middleware

```python
"""Per-request cost attribution with budget enforcement."""
import time
from dataclasses import dataclass
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

MODEL_PRICING = {
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-haiku-4": {"input": 0.25, "output": 1.25},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}

def calculate_request_cost(model: str, input_tokens: int, output_tokens: int,
                           cached_tokens: int = 0, is_batch: bool = False) -> float:
    pricing = MODEL_PRICING.get(model)
    if not pricing: raise ValueError(f"Unknown model: {model}")
    input_rate = pricing["input"] / 1_000_000
    output_rate = pricing["output"] / 1_000_000
    non_cached = max(0, input_tokens - cached_tokens)
    total = (non_cached * input_rate) + (cached_tokens * input_rate * 0.1) + (output_tokens * output_rate)
    if is_batch: total *= 0.5
    return round(total, 6)

class CostTracker:
    """Per-user, per-feature, per-team costs in Redis with budget enforcement."""
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def check_budget(self, user_id: str, daily_budget: float) -> dict:
        key = f"cost:daily:{user_id}:{time.strftime('%Y-%m-%d')}"
        current = float(await self.redis.get(key) or 0)
        util = current / daily_budget if daily_budget > 0 else 0
        status = ("exceeded" if util >= 1.0 else "critical" if util >= 0.80
                  else "warning" if util >= 0.50 else "ok")
        return {"status": status, "current_spend": current,
                "budget": daily_budget, "utilization_pct": round(util * 100, 1)}

    async def record_cost(self, user_id: str, feature: str, team: str,
                          model: str, cost: float) -> None:
        pipe = self.redis.pipeline()
        date = time.strftime("%Y-%m-%d")
        for key, ttl in [
            (f"cost:daily:{user_id}:{date}", 86400*7),
            (f"cost:feature:{feature}:{date}", 86400*30),
            (f"cost:team:{team}:{date}", 86400*30),
            (f"cost:model:{model}:{date}", 86400*30),
            (f"cost:global:{date}", 86400*90),
        ]:
            pipe.incrbyfloat(key, cost)
            pipe.expire(key, ttl)
        await pipe.execute()

class CostEnforcementMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, redis_url: str, default_budget: float = 10.0):
        super().__init__(app)
        self.tracker = CostTracker(redis.from_url(redis_url))
        self.default_budget = default_budget

    async def dispatch(self, request: Request, call_next) -> Response:
        user_id = request.headers.get("X-User-ID", "anonymous")
        status = await self.tracker.check_budget(user_id, self.default_budget)
        if status["status"] == "exceeded":
            raise HTTPException(429, detail={"error": "Daily budget exceeded",
                                             "spend": status["current_spend"],
                                             "budget": status["budget"]})
        request.state.cost_metadata = {"user_id": user_id, "budget_status": status}
        return await call_next(request)
```

### 6.4 Fallback Chain with Circuit Breakers

```python
"""
Multi-provider fallback chain with circuit breaker pattern.
Primary -> Secondary -> Tertiary -> Deterministic rules -> Human escalation.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"        # Normal operation
    OPEN = "open"            # Failing, reject immediately
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreaker:
    """
    Circuit breaker with configurable failure threshold and cooldown.
    Community consensus: 5 failures to trip, 60-second cooldown.
    """

    failure_threshold: int = 5
    cooldown_seconds: float = 60.0
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0

    def record_success(self) -> None:
        self.failure_count = 0
        self.state = CircuitState.CLOSED
        self.last_success_time = time.monotonic()

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker OPEN after %d failures", self.failure_count
            )

    def should_allow_request(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed >= self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker HALF_OPEN, testing recovery")
                return True
            return False
        # HALF_OPEN: allow one test request
        return True


@dataclass
class ModelProvider:
    name: str
    model: str
    api_base: str
    api_key_env: str  # environment variable name, not the key itself
    timeout_seconds: float = 30.0
    circuit_breaker: CircuitBreaker = field(default_factory=CircuitBreaker)


@dataclass
class FallbackResponse:
    content: str
    provider_used: str
    model_used: str
    fallback_depth: int       # 0 = primary, 1 = secondary, etc.
    latency_ms: float
    was_deterministic: bool = False


class FallbackChain:
    """
    Multi-provider fallback chain.
    Order by cost, not just availability -- try cheaper before different.
    """

    def __init__(self, providers: list[ModelProvider]):
        self.providers = providers
        self._call_count = 0
        self._fallback_count = 0

    @property
    def fallback_rate(self) -> float:
        """If secondary handles >5% of traffic, investigate the primary."""
        if self._call_count == 0:
            return 0.0
        return self._fallback_count / self._call_count

    async def call(
        self,
        messages: list[dict],
        max_retries_per_provider: int = 2,
    ) -> FallbackResponse:
        """
        Execute the fallback chain. Retry within provider first, then fall back.
        Fallback is distinct from retry: retry re-issues to the same model,
        fallback issues to a different one.
        """
        self._call_count += 1
        errors = []

        for depth, provider in enumerate(self.providers):
            if not provider.circuit_breaker.should_allow_request():
                logger.info(
                    "Skipping %s: circuit breaker OPEN", provider.name
                )
                continue

            for attempt in range(max_retries_per_provider):
                start = time.monotonic()
                try:
                    content = await self._call_provider(provider, messages)
                    provider.circuit_breaker.record_success()
                    latency = (time.monotonic() - start) * 1000

                    if depth > 0:
                        self._fallback_count += 1
                        logger.warning(
                            "Served by fallback provider %s (depth=%d)",
                            provider.name,
                            depth,
                        )

                    return FallbackResponse(
                        content=content,
                        provider_used=provider.name,
                        model_used=provider.model,
                        fallback_depth=depth,
                        latency_ms=latency,
                    )
                except Exception as e:
                    provider.circuit_breaker.record_failure()
                    errors.append(
                        f"{provider.name} attempt {attempt + 1}: {e}"
                    )
                    if attempt < max_retries_per_provider - 1:
                        await asyncio.sleep(2 ** attempt)  # exponential backoff

        # All providers failed -- deterministic fallback
        logger.error(
            "All providers failed. Errors: %s. Using deterministic fallback.",
            "; ".join(errors),
        )
        self._fallback_count += 1
        return FallbackResponse(
            content="I'm currently unable to process this request. "
            "Please try again shortly or contact support.",
            provider_used="deterministic_fallback",
            model_used="none",
            fallback_depth=len(self.providers),
            latency_ms=0.0,
            was_deterministic=True,
        )

    async def _call_provider(
        self, provider: ModelProvider, messages: list[dict]
    ) -> str:
        """Make an API call to a specific provider."""
        import os

        api_key = os.environ.get(provider.api_key_env)
        if not api_key:
            raise ValueError(f"Missing API key: {provider.api_key_env}")

        async with httpx.AsyncClient(timeout=provider.timeout_seconds) as client:
            response = await client.post(
                f"{provider.api_base}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": provider.model,
                    "messages": messages,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    def get_health_report(self) -> dict:
        """Report circuit breaker states and fallback rate for monitoring."""
        return {
            "fallback_rate": round(self.fallback_rate, 4),
            "fallback_rate_alert": self.fallback_rate > 0.05,
            "providers": [
                {
                    "name": p.name,
                    "circuit_state": p.circuit_breaker.state.value,
                    "failure_count": p.circuit_breaker.failure_count,
                }
                for p in self.providers
            ],
        }


# Usage -- order by cost (cheaper first in fallback)
def build_default_chain() -> FallbackChain:
    return FallbackChain(
        providers=[
            ModelProvider(
                name="anthropic",
                model="claude-sonnet-4-20250514",
                api_base="https://api.anthropic.com",
                api_key_env="ANTHROPIC_API_KEY",
            ),
            ModelProvider(
                name="openai",
                model="gpt-4o-mini",  # cheaper fallback, not GPT-4o
                api_base="https://api.openai.com",
                api_key_env="OPENAI_API_KEY",
            ),
            ModelProvider(
                name="google",
                model="gemini-2.5-flash",
                api_base="https://generativelanguage.googleapis.com",
                api_key_env="GOOGLE_API_KEY",
                timeout_seconds=15.0,
            ),
        ]
    )
```

### 6.5 Checkpoint/Resume for Long-Running Agents

```python
"""
Checkpoint/resume pattern for long-running agents using LangGraph.
Demonstrates node-level persistence with PostgresSaver.
"""

import logging
import uuid
from typing import Annotated, TypedDict

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    """
    Agent state persisted at every graph node.
    LangGraph saves state at each superstep, organized by thread_id.
    """
    messages: Annotated[list, add_messages]
    plan: list[str]
    current_step: int
    results: dict[str, str]
    total_cost_usd: float


async def plan_step(state: AgentState) -> dict:
    """Step 1: Generate execution plan. Checkpointed automatically."""
    logger.info("Executing plan_step (step %d)", state["current_step"])
    # In production, this calls an LLM to generate a plan
    plan = ["research_topic", "analyze_data", "generate_report"]
    return {"plan": plan, "current_step": 1}


async def execute_step(state: AgentState) -> dict:
    """
    Step 2: Execute current plan item.
    If the process crashes here, recovery resumes from the last checkpoint,
    skipping completed steps by using their recorded results.
    """
    step_idx = state["current_step"]
    plan = state["plan"]

    if step_idx >= len(plan):
        return state

    current_task = plan[step_idx]
    logger.info("Executing task: %s (step %d/%d)", current_task, step_idx + 1, len(plan))

    # Idempotency check: if this step already has a result (from a partial
    # replay), skip re-execution to avoid duplicate side effects
    if current_task in state["results"]:
        logger.info("Step '%s' already completed, skipping", current_task)
        return {"current_step": step_idx + 1}

    # Simulate work (in production: LLM calls, tool execution, API calls)
    result = f"Completed {current_task} with findings"

    updated_results = dict(state["results"])
    updated_results[current_task] = result

    return {
        "results": updated_results,
        "current_step": step_idx + 1,
        "total_cost_usd": state["total_cost_usd"] + 0.05,  # track cost per step
    }


def should_continue(state: AgentState) -> str:
    """Route: continue executing or finish."""
    if state["current_step"] >= len(state["plan"]):
        return "finish"
    return "execute"


async def finish_step(state: AgentState) -> dict:
    """Final step: compile results."""
    logger.info(
        "Agent complete. Total cost: $%.4f. Steps: %d",
        state["total_cost_usd"],
        len(state["results"]),
    )
    return state


def build_agent_graph() -> StateGraph:
    """Build the LangGraph agent with checkpoint-aware state transitions."""
    graph = StateGraph(AgentState)

    graph.add_node("plan", plan_step)
    graph.add_node("execute", execute_step)
    graph.add_node("finish", finish_step)

    graph.set_entry_point("plan")
    graph.add_edge("plan", "execute")
    graph.add_conditional_edges("execute", should_continue, {
        "execute": "execute",
        "finish": "finish",
    })
    graph.add_edge("finish", END)

    return graph


async def run_agent_with_checkpoints(
    task_description: str,
    thread_id: str | None = None,
    db_uri: str = "postgresql://agent:password@localhost:5432/agent_state",
) -> dict:
    """
    Run or resume an agent with durable checkpointing.

    Args:
        task_description: The task for the agent to perform.
        thread_id: Stable, deterministic ID tied to the business task.
                   Pass the same thread_id to resume a crashed run.
                   Use None to start a new run.
        db_uri: PostgreSQL connection string. Always use external storage,
                never MemorySaver (lost on process death).
    """
    # Use deterministic thread_id tied to business task, not random UUIDs
    if thread_id is None:
        thread_id = str(uuid.uuid4())

    config = {"configurable": {"thread_id": thread_id}}

    graph = build_agent_graph()

    async with AsyncPostgresSaver.from_conn_string(db_uri) as checkpointer:
        await checkpointer.setup()  # create tables if needed

        compiled = graph.compile(checkpointer=checkpointer)

        # Check for existing state (resume scenario)
        existing_state = await compiled.aget_state(config)
        if existing_state and existing_state.values:
            logger.info(
                "Resuming from checkpoint. Thread: %s, Step: %d",
                thread_id,
                existing_state.values.get("current_step", 0),
            )
        else:
            logger.info("Starting new agent run. Thread: %s", thread_id)

        # Run (or resume) the agent
        initial_state = {
            "messages": [{"role": "user", "content": task_description}],
            "plan": [],
            "current_step": 0,
            "results": {},
            "total_cost_usd": 0.0,
        }

        final_state = None
        async for event in compiled.astream(initial_state, config):
            for node_name, node_output in event.items():
                logger.info("Node '%s' completed", node_name)
                final_state = node_output

        return {
            "thread_id": thread_id,
            "status": "completed",
            "results": final_state.get("results", {}) if final_state else {},
            "total_cost_usd": (
                final_state.get("total_cost_usd", 0.0) if final_state else 0.0
            ),
        }
```

### 6.6 Health Check and Readiness Probes for AI Services

```python
"""
Health check and readiness probes for AI services.
Liveness: is the process alive?
Readiness: is the model loaded and ready to serve inference?
Startup: has the model finished loading? (prevents premature kills)
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum

from fastapi import FastAPI, Response

logger = logging.getLogger(__name__)


class ServiceStatus(Enum):
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class DependencyHealth:
    name: str
    healthy: bool
    latency_ms: float
    last_check: float = 0.0
    error: str | None = None


@dataclass
class ServiceHealth:
    status: ServiceStatus = ServiceStatus.STARTING
    model_loaded: bool = False
    model_load_start: float = field(default_factory=time.monotonic)
    uptime_seconds: float = 0.0
    requests_served: int = 0
    dependencies: dict[str, DependencyHealth] = field(default_factory=dict)
    last_successful_inference: float = 0.0
    inference_error_count: int = 0


health = ServiceHealth()
app = FastAPI()


async def check_redis(redis_url: str = "redis://localhost:6379") -> DependencyHealth:
    """Check Redis connectivity."""
    start = time.monotonic()
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(redis_url)
        await client.ping()
        await client.aclose()
        return DependencyHealth(
            name="redis",
            healthy=True,
            latency_ms=(time.monotonic() - start) * 1000,
        )
    except Exception as e:
        return DependencyHealth(
            name="redis",
            healthy=False,
            latency_ms=(time.monotonic() - start) * 1000,
            error=str(e),
        )


async def check_postgres(db_url: str = "postgresql://localhost:5432/agent_state") -> DependencyHealth:
    """Check PostgreSQL connectivity."""
    start = time.monotonic()
    try:
        import asyncpg
        conn = await asyncpg.connect(db_url)
        await conn.fetchval("SELECT 1")
        await conn.close()
        return DependencyHealth(
            name="postgres",
            healthy=True,
            latency_ms=(time.monotonic() - start) * 1000,
        )
    except Exception as e:
        return DependencyHealth(
            name="postgres",
            healthy=False,
            latency_ms=(time.monotonic() - start) * 1000,
            error=str(e),
        )


@app.get("/healthz")
async def liveness_probe():
    """
    Liveness probe: is the process alive?
    Returns 200 if the process is running, regardless of model state.
    If this fails, Kubernetes restarts the pod.
    """
    return {"status": "alive", "uptime_s": time.monotonic() - health.model_load_start}


@app.get("/readyz")
async def readiness_probe(response: Response):
    """
    Readiness probe: is the service ready to accept inference requests?
    Returns 503 if model is not loaded or critical dependencies are down.
    A pod that fails readiness remains running but is removed from
    the load balancer, preventing traffic to degraded instances.
    """
    # Check model
    if not health.model_loaded:
        response.status_code = 503
        return {
            "status": "not_ready",
            "reason": "Model not loaded",
            "loading_duration_s": time.monotonic() - health.model_load_start,
        }

    # Check critical dependencies in parallel
    redis_health, pg_health = await asyncio.gather(
        check_redis(), check_postgres()
    )
    health.dependencies["redis"] = redis_health
    health.dependencies["postgres"] = pg_health

    critical_deps_healthy = all(
        d.healthy for d in health.dependencies.values()
    )

    if not critical_deps_healthy:
        response.status_code = 503
        failed = [
            d.name for d in health.dependencies.values() if not d.healthy
        ]
        return {
            "status": "not_ready",
            "reason": f"Dependencies unhealthy: {', '.join(failed)}",
            "dependencies": {
                name: {"healthy": d.healthy, "error": d.error}
                for name, d in health.dependencies.items()
            },
        }

    # Check inference health (no successful inference in 5 minutes = degraded)
    if (
        health.requests_served > 0
        and time.monotonic() - health.last_successful_inference > 300
    ):
        response.status_code = 503
        return {
            "status": "degraded",
            "reason": "No successful inference in 5 minutes",
            "error_count": health.inference_error_count,
        }

    health.status = ServiceStatus.READY
    return {
        "status": "ready",
        "model_loaded": True,
        "requests_served": health.requests_served,
        "dependencies": {
            name: {"healthy": d.healthy, "latency_ms": round(d.latency_ms, 1)}
            for name, d in health.dependencies.items()
        },
    }


@app.get("/startupz")
async def startup_probe(response: Response):
    """
    Startup probe: has initial model loading completed?
    Kubernetes uses this to know when to start liveness/readiness checks.
    start_period equivalent: configure failureThreshold * periodSeconds
    to exceed maximum model load time (e.g., 60 * 10s = 600s for large models).
    """
    if not health.model_loaded:
        response.status_code = 503
        return {
            "status": "loading",
            "duration_s": round(time.monotonic() - health.model_load_start, 1),
        }
    return {"status": "started", "model_loaded": True}


def mark_model_loaded():
    """Call this after model weights are fully loaded into GPU memory."""
    health.model_loaded = True
    health.status = ServiceStatus.READY
    load_time = time.monotonic() - health.model_load_start
    logger.info("Model loaded in %.1f seconds", load_time)


def record_inference_success():
    """Call after each successful inference."""
    health.requests_served += 1
    health.last_successful_inference = time.monotonic()


def record_inference_error():
    """Call after each failed inference."""
    health.inference_error_count += 1
```

---

## 7. Failure Modes & Mitigations

### 7.1 Failure Taxonomy

| Class | Examples | Handler |
|-------|----------|---------|
| **Transient** | 408/`slow_down`/503/529, TLS reset, NIM NotReady, OTLP 429 | Full jitter; honor `Retry-After` iff 0 < t <= 60; same idempotency key; optional other vendor |
| **Permanent** | 400 schema, `ContextWindowExceeded`, 401/403, spend/usage/credit 429, jailbreak block | **No** retry. Spend -> deterministic + page finance. Schema -> dedicated fallbacks only if product says so |
| **Poison deploy** | Hub `latest`; canary on 5xx only; unsigned tag; arm64 into amd64; `fail-on-threshold` skipped | Pin digest+SHA+snapshot; AnalysisRun on schema/PII/$/TTFT/rail FP; Cosign+Kyverno admit |
| **Poison checkpoint** | Renamed node / reordered `@task`; `DeltaChannel` flip; missing `LANGGRAPH_AES_KEY`; canary RS writes unreadable checkpoints | Pin graph+version in image; migrate with new `thread_id`; Temporal replay tests |
| **Poison spend** | Treat spend 429 as `slow_down`; LiteLLM no-DB fail-open; fallback storm onto Anthropic without a budget | Error-code allowlist; require DB; 402/403 vs overload 429 |
| **Poison rails** | Silent `fix` mutates payment amount; fail-open no log; speculative prefill on unsafe; IORails surprise drop to LLMRails | Explicit failure actions; WORM decision log; `require_iorails=True` |
| **Poison secrets** | `ARG NPM_TOKEN`; Hub public prompt with attacker base URL; tokens in `state` | Secret mounts; `dangerously_pull_public_prompt` default false; `UntrackedValue` |
| **Silent quality drop** | Coverage 0%; spend cap pauses evaluator; all-open breaker bypass | Coverage SLO; edge shed; freeze prompt tags when error budget burned |

### 7.2 Interview Traps (Fail These, Fail the Round)

1. Shipping because `GET /ok` is 200 while the golden set regresses. **CI is not serving, and `/ok` is not quality.**
2. Retrying `organization_spend_limit_exceeded`. Same HTTP 429 as `slow_down`. **Never retry spend-cap 429.**
3. CPU HPA on an agent waiting on an API/GPU. Scale on **EPP queue / inflight tokens / task-queue depth**.
4. Secrets in layers: `ARG`/`ENV`, `COPY .env`, API keys in `langgraph.json`.
5. Copying a vLLM wheel onto distroless. Distroless has **no libcuda**. Two images, two blast radii.
6. Treating Temporal Activities as exactly-once. They are **at-least-once**. Key = `runId + activityId`.
7. Scale-to-zero standalone Agent Server. Docs say no serverless; `minReplicas >= 1`.
8. All-circuits-open bypass. Shed at the edge with `429 Retry-After`.
9. Blue/green `scaleDownDelaySeconds` default 30s on a GPU that needs minutes to load weights.
10. nginx `proxy-read-timeout` 60s + buffering on. LLM SSE needs buffering **off** and keepalive every 20-30s.
11. Synchronous Llama Guard on every token. Defense-in-depth is **cheap to expensive**.
12. LiteLLM `max_budget` fails open with no DB. Require Postgres.
13. K8s probes copied from Docker `HEALTHCHECK CMD curl` onto distroless (no shell).
14. Bearer tokens in graph `state` -- they land in checkpoints. Use `UntrackedValue`.

### 7.3 Notable Production Incidents (Interview Context)

| Incident | Date | Lesson |
|----------|------|--------|
| **Anthropic Claude Quality Degradation** | Aug-Sep 2025 | Context window routing error sent Sonnet 4 requests to 1M-token servers. Model versioning and routing traceability are essential |
| **AWS AI-Agent-Caused Outage** | Dec 2025 | First confirmed AI-agent-caused production outage. "Misconfigured access controls." Led to mandatory peer review for AI-initiated changes |
| **LiteLLM Supply Chain Attack** | Mar 2026 | Backdoored PyPI packages live for ~40 minutes. 3.4M daily downloads. Brief exposure windows in high-download packages create massive blast radius |
| **AWS us-east-1 Thermal Event** | May 2026 | Multiple chiller failures in Virginia. Legacy cooling not built for AI/HPC rack densities |
| **DORA 2026** | 2026 | AI code generation tools increase code volume but decrease delivery throughput by 1.5% and worsen stability by 7.5%. More code is not better outcomes |
| **GPU utilization** | Cast.ai 2026 | Average 5% GPU utilization across production K8s clusters. Fewer than 2% of GPU workloads on Spot |

### 7.4 System Invariants (Must Never Violate)

1. The model never deploys, gates, opens a circuit, meters spend, or commits a checkpoint.
2. Four clocks stay unfused. CI fail-closed != serving SLO != PEP bound != durable resume.
3. Never retry or paid-failover a spend-cap 429. Deterministic last hop only.
4. Tools are at-least-once. Idempotency key on every mutating call; saga compensations reversed and themselves idempotent.
5. Two images. Distroless worker; NIM/CUDA engine. No GPU request on the worker.
6. Secrets not in layers, not in `langgraph.json`, not in checkpointed `state`.
7. Budgets need a DB. LiteLLM `max_budget` fail-open without Postgres = unbounded $.
8. Redis is signaling. Checkpoints live in Postgres or Temporal history.
9. CI pin `(dataset, tag, split)` + `prompt@SHA` + snapshot in one commit.
10. PEP is cheap to expensive; sandwich masks before the vendor; deanonymize after.
11. Drain the super-step. Do not SIGKILL a Ready NIM because live still 200 during load.
12. All-open breakers bypass -- shed at the edge.

---

## 8. Security & Governance

### 8.1 Zero-Trust MCP Egress and RBAC

**North-south**: Gateway TLS; input rails before vendor; output rails before user.
**East-west**: Agent -> MCP only through a gateway PEP: OAuth 2.1, PRM RFC 9728, resource indicators RFC 8707. No shared PAT. Tool allowlists. RFC 8707 token minted for the gateway resource must not work on raw GitHub MCP (confused deputy).

**NetworkPolicy**: Default deny; allow gateway -> Agent Server -> Postgres/Redis; allow NIM health port. No worker egress to public internet except MCP gateway and pinned vendor CIDRs.

**Tool-level RBAC**: Allowlists compiled into worker image (or signed ConfigMap). Parameter allowlists: URL prefixes, max refund, forbidden SQL verbs. Tenant identity from the JWT, not from a prompt field or tool arg. Resume of a Temporal Workflow must re-RBAC before any write tool. Separate IdP clients for CI vs prod.

**Admission**: SBOM attested on the image digest: `cosign attest --type https://spdx.dev/Document`. Admit with Sigstore `ClusterImagePolicy` or Kyverno CEL. Pin platform digest. No `:latest`.

**Secret management**: Never bake into images. Production: HashiCorp Vault, AWS Secrets Manager, or GCP Secret Manager with automatic rotation. Per-agent least-privilege credentials.

### 8.2 Enterprise Security Statistics

- Only 21% of organizations maintain a real-time agent registry
- Only 18% of security leaders believe IAM can handle AI agent identities
- 97% of breached organizations lacked proper access controls for AI systems (IBM 2025)
- 88% of organizations deploying AI agents reported at least one security incident in 2025
- 77% of enterprises faced GenAI breaches in 2025 (IBM)

### 8.3 Compliance Frameworks

| Framework | Key Requirements | Penalty | AI-Specific Considerations |
|-----------|-----------------|---------|---------------------------|
| **SOC 2 Type II** | 2026 updates explicitly address AI governance. Now a procurement requirement | Loss of enterprise deals | Audit trail on every model call. 40% of enterprise apps will integrate AI agents by EOY 2026 (Gartner) |
| **HIPAA** | BAA required before transmitting PHI. Standard consumer APIs generally do not provide BAAs | $7.42M avg breach cost (IBM 2025, highest industry 14 consecutive years) | VPC-isolated or self-hosted deployment. Without BAA, any PHI transmission is a violation |
| **GDPR** | EDPB clarified prompts containing personal data trigger full GDPR protections | Up to EUR 35M or 7% of global annual turnover | PII redaction in telemetry. Data residency. Right to erasure applies to training data |
| **EU AI Act** | High-risk requirements active August 2, 2026. Must trace model version per response | Up to 7% of global annual turnover | HITL gates. OPA policies. Model versioning non-negotiable |

**BCG 2026**: 73% of enterprise AI initiatives name compliance posture as a top-three vendor selection criterion (up from 41% in 2024). Retrofitting governance after audit notice costs 2-3x the original build.

### 8.4 Supply Chain Security

The **LiteLLM incident** (March 2026) demonstrated the risk: backdoored versions published to PyPI, live for ~40 minutes, with 3.4M daily downloads. Mitigations:
- Container scanning in CI/CD pipelines
- Dependency pinning with hash verification
- Signed images (Cosign + Kyverno/Sigstore)
- NIM containers include vulnerability scanning
- `cosign attest` SBOM on platform digest (not multi-arch index)

### 8.5 PII and Immutable Audit

**WORM row format (append-only)**:

```
timestamp, correlation_id, tenant, user_hash, model_snapshot, prompt_commit,
tool_name, args_hash, gen_ai.usage.input_tokens, gen_ai.usage.output_tokens,
billed_usd, guardrail_decision, rail_name, finish_reason, thread_id,
checkpoint_id, spend_remaining, hide_inputs=true
```

Object-lock the audit bucket (S3 Object Lock / Azure immutable storage). OTel backends are NOT WORM unless you export to that bucket. Traces are PII stores -- use `hide_inputs` and note that datasets outlive 14d traces.

**OWASP Agentic Top 10 concentrated here**: ASI01 prompt injection past rails; ASI03 privilege abuse (worker with union MCP); ASI08 cascading failures (spend-cap fallback storm); ASI09 HITL exploitation (rubber-stamp approver queue).

---

## 9. System Design Scenarios

### Scenario 1: API-Only Multi-Tenant SaaS Agent (Kubernetes)

**Problem**: Multi-tenant chat + tools; no GPU; EU PII; 99.9% availability on completed turns; TTFT from vendor plus cheap rails. Mix M1 ~$19.20/1k. Eval success = spend-cap 429 does not fan out to secondary vendor; `/ok` green with golden regression cannot promote; PII never raw in checkpoint; SSE survives >60s decode; CI cannot `refund.execute`.

```
                    +---------------------------------------------------------+
                    | EDGE  SSO, cid, tenant FROM TOKEN                        |
                    | ingress-nginx buffering OFF; read timeout > p99 silence  |
                    | SSE keepalive 20-30s; MODEL never deploys / meters       |
                    +-----------------------------+---------------------------+
                                                  |
                    +-----------------------------v---------------------------+
                    | CONTROL  GITOPS + GATEWAY                                |
                    |  distroless python3-debian12@sha256; Cosign+SPDX         |
                    |  Helm Agent Server; DATABASE_URI + Redis; minReplicas>=2 |
                    |  LiteLLM/Portkey in-cluster; max_budget WITH DB          |
                    |  fallback: OpenAI -> Anthropic on slow_down/503 -> refuse|
                    |  NEVER fallback on spend 429. Soft alert 80% peak-minute |
                    |  CI: promptfoo FAILURES>0; Argo 10% + AnalysisRun       |
                    +------+------------------------------+-------------------+
                           |                              |
                           v                              v
                    +----------------+            +-----------------------+
                    | DATA serving   |            | TOOL PROXIES          |
                    | Agent Srv SSE  |            | CI: sim MCP           |
                    | PEP: Presidio +|            | PROD: gateway PEP     |
                    | Prompt Guard   |            | RFC 8707; allowlist    |
                    | Llama Guard    |            | NO stdio in web pod   |
                    | sampled/async  |            +-----------+-----------+
                    +--------+-------+                        |
                    +--------v-------+            +-----------v-----------+
                    | PERSIST Postgres|            | TELEMETRY WORM        |
                    | Saver pool;    |            | gen_ai.* 100%, M1     |
                    | durability=async|           | $19.20/1k, rail decis,|
                    | EncryptedSerde |            | remaining $           |
                    +----------------+            +-----------------------+
```

**Trade-off matrix**:

| Dimension | A: Direct SDK, python:3.13, Hub latest | B: Recommended (distroless, Helm, PEP, Argo) | C: Cloud Run scale-to-zero, sync Llama Guard |
|-----------|---------------------------------------|----------------------------------------------|----------------------------------------------|
| **Cost/1k** | M3 retry storm ~$23.2/1k; unmetered tenants | M1 $19.20/1k; idle GPU $0 | Sync Guard + 100% judge = +$9/1k |
| **Latency** | nginx 60s kills p99; SQLite races | Vendor + cheap rails; keepalive 20-30s | Guard on TTFT IS the p99; cold start IS the SLO miss |
| **Security** | Keys in layers; stdio MCP; PII in SQLite | Secret mounts; RFC 8707; Presidio sandwich; WORM | Scale-to-zero still holds PII in last checkpoint |
| **Scale** | CPU HPA no-ops; one tenant fills N_JOBS=10 | KEDA on task-queue; per-tenant concurrency | Loses `durability=exit` work; cannot HITL days |

**Decision**: B is the only design keeping four clocks apart: fail-closed CI, serving on vendor TTFT, CPU PEP, Postgres resume.

### Scenario 2: Hybrid GPU + Temporal Resume

**Problem**: Open-weight NIM for bulk; frontier API for hard turns; HITL that may wait days; tools that charge money. GPU ~$6.88/H100-hr. 10% API overflow at M1 rates on 100k NIM-local turns = $192. Eval success = worker crash does not double-charge Stripe; NIM 503 sheds to API with different idempotency namespace; Service flip never hits a loading GPU.

```
                    +---------------------------------------------------------+
                    | EDGE  SSO, cid; Envoy; streaming errors NOT retried      |
                    | TWO images: distroless worker + NIM GPU; NIMCache PVC    |
                    +-----------------------------+---------------------------+
                                                  |
                    +-----------------------------v---------------------------+
                    | CONTROL  TEMPORAL WORKFLOW = durable brain               |
                    |  execute_in=activity on LLM/tools; Workflow deterministic|
                    |  InMemorySaver -- Temporal IS durability                 |
                    |  Start-To-Close = p99 decode + load; heartbeats          |
                    |  Fallback: NIM 503 -> API Activity (new key namespace)   |
                    |  -> deterministic. NEVER spend-429 failover              |
                    |  KEDA on EPP queue; min>=1; startupProbe 20 min          |
                    |  Blue/green NIM with prePromotion on Ready               |
                    |  Saga: compensate reversed; key=runId:activityId         |
                    +------+------------------------------+-------------------+
                           |                              |
                           v                              v
                    +----------------+            +-----------------------+
                    | DATA NIM Ready |            | TOOL PROXIES          |
                    | + API overflow |            | Activity refund with  |
                    | PEP on both    |            | Stripe-style key; MCP |
                    | Presidio before|            | gateway; gVisor for   |
                    | ANY vendor     |            | untrusted bash        |
                    +--------+-------+            +-----------+-----------+
                    +--------v-------+            +-----------v-----------+
                    | PERSIST Temporal|            | TELEMETRY WORM        |
                    | history; Kafka |            | gen_ai.* 100% on API; |
                    | DLQ for tools; |            | GPU $ as CAPACITY     |
                    | NOT a 2nd PG   |            | not per-token         |
                    | checkpointer   |            |                       |
                    +----------------+            +-----------------------+
```

**Decision**: Two bills, two images, one durable brain (Temporal). InMemorySaver rule, NIM live/ready split, at-least-once Activities with saga compensations.

### Scenario 3: Cost-Optimized Internal AI Assistant

**Problem**: 5,000 employees, $2B revenue, $25K/month prototype -> <$10K/month target. SOC 2 + GDPR. 2 ML engineers, no GPU ops capacity.

**Decision**: Cloud Run + Managed APIs. With only 2 engineers, GPU ops (Approach B) would consume entire team bandwidth. Cloud Run provides scale-to-zero, L4 GPU for embedding model, no streaming timeout.

```
Cost optimization stack:
  Baseline:                              $25,000/month
  Model routing (70% to Haiku/Flash):    $10,000  (60% reduction)
  Prompt caching (stable system prompt):  $7,000  (30% on remaining)
  Semantic caching (30% hit rate):        $5,000  (29% further)
  Batch API (nightly summarization):      $4,000  (20% further)
  Total:                                 ~$4,000  (84% reduction)
```

GDPR mitigated by EU-based API endpoints + PII redaction before APIs + crypto-shredding for right-to-erasure. SOC 2 satisfied by immutable audit log (model version + user identity + timestamp per call).

---

## 10. Interview Quick Reference

### Key Numbers to Memorize

| Number | What |
|--------|------|
| **$19.20/1k** | Mix M1 tokens (92/6/2) skeleton W (inferred) |
| **$19.50 / $21 / $7 / $0** | GPT-5.4 / Sonnet 4.6 / Haiku 4.5 / deterministic per 1k |
| **~$23.2/1k** | Mix M3 retry storm (inferred) |
| **~$58.5/1k** | 3-node GPT-5.4 agent graph (inferred) |
| **~$165/day** | Idle H100 from ~$6.88/H100-hr aggregator |
| **~8,600 req/day** | Break-even vs M1 on one H100 |
| **60-85%** | Cost reduction from caching + routing + batching + compression |
| **5%** | Average GPU utilization across production K8s (Cast.ai 2026) |
| **66%** | Organizations running GenAI on K8s (CNCF 2026) |
| **60s / 5 min / 20-30s** | nginx read timeout / Envoy stream idle / SSE keepalive |
| **20 min** | NIM Operator startupProbe default |
| **30s** | Argo blue/green `scaleDownDelaySeconds` -- too short for GPU |
| **512** | Prompt Guard 2 token window |
| **80%** | Soft budget alert threshold |
| **99.9%** | Availability target on completed turns (not spend-shed) |
| **5 / 60s** | Circuit breaker: failures to trip / cooldown seconds |
| **38%** | Lower task-abandonment with configured fallback chains |
| **$7.42M** | Average healthcare breach cost (IBM 2025) |
| **7%** | EU AI Act max penalty as % of global annual turnover |

### Deployment Decision Matrix

| Factor | API-Only | Hybrid (API + Self-Hosted) | Fully Self-Hosted |
|--------|---------|---------------------------|-------------------|
| **When to use** | No GPU ops capacity; bursty; multi-provider HA | Data residency + frontier quality; GPU for bulk | Full control; sustained high throughput |
| **Monthly cost (from $25K)** | $4-7K (routing + caching + batch) | $8-12K (GPU infra + API overflow) | $12-18K (GPU cluster + ops) |
| **Ops burden** | Low | Medium | High |
| **HIPAA readiness** | Risky (need BAA from every provider) | Good (guardrails in VPC) | Best (all data stays in VPC) |
| **Scalability** | Highest (provider scales for you) | Elastic (API absorbs burst) | GPU procurement-limited |

### Interview Closing Statement

"Four clocks. The model is a stateless token API. I ship a signed distroless digest with `prompt@SHA` and a pinned snapshot, I meter `gen_ai.*` against a DB budget, I fail over on `slow_down` and never on spend-cap 429, I sandwich PII through a PEP, and I resume from Postgres or Temporal with at-least-once tools. Quote $19.20/1k M1, ~$165/day idle H100, nginx 60s as the SSE bug, NIM Ready 20 min, key = `runId:activityId`. Cost optimization stack gives 60-85% reduction through model routing, prompt caching, semantic caching, and batch APIs."
