# Module 11: Production Deployment

> **Audience**: Principal Data Scientist (12+ YOE) preparing for Director/VP-level AI roles.
> **Scope**: End-to-end production deployment for LLM-powered applications and agent systems -- containerization and GPU passthrough, Kubernetes orchestration with KEDA autoscaling, CI/CD with semantic evaluation gates, guardrail architectures (NeMo, LlamaGuard, Guardrails AI, defense-in-depth), cost optimization (prompt caching, semantic caching, model routing, batching), fallback chains with circuit breakers, checkpoint/resume for durable agents (LangGraph, Temporal), model serving infrastructure (vLLM, TGI, NIM, SGLang), queue-based architectures, enterprise security and compliance (SOC 2, HIPAA, GDPR, EU AI Act), and disaster recovery.
> **Pricing assumptions**: Claude Sonnet input $3/1M tokens, output $15/1M; Claude Opus input $15/1M, output $75/1M; Claude Haiku input $0.25/1M, output $1.25/1M; GPT-4o input $2.50/1M, output $10/1M; GPT-4o-mini input $0.15/1M, output $0.60/1M. H100 on-demand $2.99-$12.30/GPU-hr (specialist-to-hyperscaler). All as of Sep 2026.
> **Key references**: Cast.ai 2026 State of Kubernetes Optimization (5% avg GPU utilization), CNCF 2026 Annual Survey (66% GenAI on K8s), OWASP 2025 Top 10 for LLMs, arXiv 2601.06007 ("Don't Break the Cache"), LiteLLM supply chain attack (Mar 2026), DORA 2026 (1.5% throughput decrease with AI code gen), IBM 2025 breach report ($7.42M healthcare average), EU AI Act high-risk enforcement (Aug 2026).

---

## 1. System Topology & Data Flow

### 1.1 Full-Stack Production Deployment Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                              CONTROL PLANE                                           │
│                                                                                      │
│  ┌───────────────────────┐  ┌───────────────────────┐  ┌─────────────────────────┐  │
│  │  DEPLOYMENT           │  │  MODEL ROUTER          │  │  COST CONTROLLER        │  │
│  │  ORCHESTRATOR         │  │                        │  │                         │  │
│  │                       │  │  - Complexity classify  │  │  - Per-user hard caps   │  │
│  │  - ArgoCD GitOps      │  │    (simple/med/complex) │  │  - Per-feature soft     │  │
│  │  - Canary promotion   │  │  - RouteLLM / custom   │  │    alerts               │  │
│  │  - Blue-green switch  │  │  - Provider selection   │  │  - Budget thresholds    │  │
│  │  - Rollback triggers  │  │  - Batch API routing    │  │    (50%, 80%)           │  │
│  │  - Semantic eval gate │  │                        │  │  - Rolling baseline     │  │
│  └──────────┬────────────┘  └──────────┬────────────┘  └──────────┬──────────────┘  │
│             │                          │                           │                  │
│  ┌──────────┴────────────────────────────────────────────────────────────────────┐   │
│  │  GUARDRAIL ENGINE                                                             │   │
│  │                                                                               │   │
│  │  Layer 1: Input Validation (schema, length, encoding)                         │   │
│  │  Layer 2: Prompt Template Hardening (injection resistance)                    │   │
│  │  Layer 3: Security Gate (Llama Prompt Guard 2 86M, 20-50ms)                   │   │
│  │  Layer 4: Retrieval/RAG Rail (source filtering, relevance)                    │   │
│  │  Layer 5: Output Filtering (LlamaGuard 3 8B, PII redaction, hallucination)    │   │
│  │  Layer 6: Tool-Call Gating (allowlists, parameter validation)                 │   │
│  │  Layer 7: Audit & Compliance Logging (immutable, EU AI Act trace)             │   │
│  └───────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                      │
└──────────────────────────────────────────────────────────────────────────────────────┘
                                        │
┌───────────────────────────────────────┼──────────────────────────────────────────────┐
│                       DATA PLANE      v                                              │
│                                                                                      │
│  ┌───────────────────────┐  ┌───────────────────────┐  ┌─────────────────────────┐  │
│  │  MODEL SERVING        │  │  INFERENCE PIPELINE    │  │  CHECKPOINT MANAGER     │  │
│  │                       │  │                        │  │                         │  │
│  │  Self-hosted:         │  │  - Request enrichment  │  │  - Node-level           │  │
│  │  - vLLM (PagedAttn)   │  │  - Context assembly    │  │    (LangGraph per-step) │  │
│  │  - SGLang (RadixAttn) │  │  - Tool orchestration  │  │  - Activity-level       │  │
│  │  - NIM (TensorRT-LLM) │  │  - Response streaming  │  │    (Temporal replay)    │  │
│  │  Managed APIs:        │  │  - Post-processing     │  │  - Explicit commit pts  │  │
│  │  - Anthropic/OpenAI   │  │                        │  │  - PostgresSaver (prod) │  │
│  │  - Overflow routing   │  │                        │  │  - Cross-region sync    │  │
│  └──────────┬────────────┘  └──────────┬────────────┘  └──────────┬──────────────┘  │
│             │                          │                           │                  │
│  ┌──────────┴────────────┐  ┌──────────┴────────────┐             │                  │
│  │  FALLBACK CHAIN       │  │  AUTOSCALER           │             │                  │
│  │                       │  │                        │             │                  │
│  │  Primary (best-fit)   │  │  KEDA ScaledObject:    │             │                  │
│  │    ↓ circuit break     │  │  - Queue depth trigger │             │                  │
│  │  Secondary (cheaper)  │  │  - HTTP rate trigger   │             │                  │
│  │    ↓ circuit break     │  │  - GPU util trigger    │             │                  │
│  │  Tertiary (lightest)  │  │  - vllm:num_requests   │             │                  │
│  │    ↓                   │  │    _waiting scaler     │             │                  │
│  │  Deterministic rules  │  │  - Scale-to-zero       │             │                  │
│  │    ↓                   │  │    (non-prod)          │             │                  │
│  │  Human escalation     │  │  - Karpenter node      │             │                  │
│  └───────────────────────┘  │    provisioning        │             │                  │
│                              └───────────────────────┘             │                  │
│                                                                    │                  │
└────────────────────────────────────────────────────────────────────┼──────────────────┘
                                                                     │
┌────────────────────────────────────────────────────────────────────┼──────────────────┐
│                         PERSISTENCE LAYER                          v                  │
│                                                                                      │
│  ┌───────────────────────┐  ┌───────────────────────┐  ┌─────────────────────────┐  │
│  │  MODEL REGISTRY       │  │  STATE STORE           │  │  COST LEDGER            │  │
│  │                       │  │                        │  │                         │  │
│  │  - Container images   │  │  - PostgresSaver       │  │  - Per-request cost     │  │
│  │    (multi-stage,      │  │    (checkpoints)       │  │    attribution          │  │
│  │     vuln-scanned)     │  │  - Redis Streams       │  │  - User/feature/team    │  │
│  │  - Model weights      │  │    (tool exec queue)   │  │    tags                 │  │
│  │    (PVC-backed)       │  │  - Thread ID mapping   │  │  - Budget utilization   │  │
│  │  - Prompt versions    │  │  - Idempotency keys    │  │  - Provider invoices    │  │
│  │  - Config snapshots   │  │                        │  │  - GPU hours ledger     │  │
│  │  - ArgoCD GitOps      │  │                        │  │                         │  │
│  └───────────────────────┘  └───────────────────────┘  └─────────────────────────┘  │
│                                                                                      │
│  ┌──────────────────────────────────────────────────────────────────────────────┐    │
│  │  CONFIG STORE                                                                │    │
│  │                                                                              │    │
│  │  GitOps-managed: guardrail policies (Colang flows), routing rules,           │    │
│  │  fallback chain config, budget thresholds, KEDA ScaledObject specs,          │    │
│  │  canary promotion criteria, feature flags, model version mappings.           │    │
│  │  Versioned in Git. ArgoCD syncs desired state to cluster.                    │    │
│  └──────────────────────────────────────────────────────────────────────────────┘    │
│                                                                                      │
└──────────────────────────────────────────────────────────────────────────────────────┘
                                        │
┌───────────────────────────────────────┼──────────────────────────────────────────────┐
│                    TOOL PROXIES        v                                              │
│                                                                                      │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────────────┐  │
│  │ Cloud APIs │ │ GPU        │ │ Cache      │ │ MCP Tool   │ │ Secret Manager   │  │
│  │            │ │ Clusters   │ │ Layers     │ │ Servers    │ │                  │  │
│  │ Anthropic  │ │ vLLM on    │ │ Prompt     │ │ Postgres   │ │ Vault / AWS SM   │  │
│  │ OpenAI     │ │ Ray Serve  │ │  cache     │ │ Slack      │ │ Auto-rotation    │  │
│  │ Google     │ │ KEDA-      │ │  (provider)│ │ Search     │ │ Per-agent least  │  │
│  │ Portkey    │ │  scaled    │ │ Semantic   │ │ Filesystem │ │  privilege       │  │
│  │ Bifrost    │ │ Karpenter  │ │  cache     │ │ Docker Hub │ │ Scoped API keys  │  │
│  │ LiteLLM   │ │  provisioned│ │  (pgvector)│ │  pre-built │ │                  │  │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘ └──────────────────┘  │
│                                                                                      │
└──────────────────────────────────────────────────────────────────────────────────────┘
                                        │
┌───────────────────────────────────────┼──────────────────────────────────────────────┐
│              TELEMETRY / OBSERVABILITY SINKS                                         │
│                                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │ Prometheus   │  │ OpenTelemetry│  │ Grafana      │  │ LLM Quality Monitor    │  │
│  │ + Grafana    │  │ Collector    │  │ Dashboards   │  │                        │  │
│  │              │  │              │  │              │  │ - Hallucination rate   │  │
│  │ GPU util,    │  │ Traces,      │  │ Cost/req,    │  │ - Guardrail bypass     │  │
│  │ queue depth, │  │ spans, cost  │  │ latency,     │  │ - Semantic quality     │  │
│  │ pod count,   │  │ attribution, │  │ fallback     │  │   (LLM-as-judge)      │  │
│  │ KEDA metrics │  │ PII redacted │  │ rate, budget │  │ - Cost anomaly detect  │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └────────────────────────┘  │
│                                                                                      │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Request-Flow Narratives

**Deployment pipeline flow (code commit to production):**

```
Developer pushes commit
    │
    v
┌────────────────┐  1. Trigger       ┌──────────────────────────┐
│  GitHub Actions │ ────────────────> │  Test Stage               │
│                │                   │                           │
│  - Lint, type  │                   │  - Unit tests             │
│    check       │                   │  - 30 behavioral tests    │
│                │                   │    (LLM-as-judge semantic │
└────────────────┘                   │     evaluation)           │
                                     │  - Guardrail bypass tests │
                                     └─────────────┬─────────────┘
                                                   │ pass
                                                   v
                                     ┌──────────────────────────┐
                                     │  Build Stage              │
                                     │                           │
                                     │  - Multi-stage Docker     │
                                     │    build (builder + slim  │
                                     │    runtime)               │
                                     │  - Vulnerability scan     │
                                     │    (Trivy, Snyk)          │
                                     │  - Signed image push to   │
                                     │    registry               │
                                     └─────────────┬─────────────┘
                                                   │
                                                   v
                                     ┌──────────────────────────┐
                                     │  Canary Deploy (ArgoCD)   │
                                     │                           │
                                     │  - 5% traffic to new ver  │
                                     │  - Observe 15-30 min:     │
                                     │    hallucination rate,     │
                                     │    guardrail bypass rate,  │
                                     │    cost/request, latency  │
                                     │  - Auto quality gate      │
                                     └─────────────┬─────────────┘
                                                   │ pass
                                                   v
                                     ┌──────────────────────────┐
                                     │  Progressive Rollout      │
                                     │                           │
                                     │  5% → 25% → 50% → 100%   │
                                     │  Each step: quality gate   │
                                     │  Old version warm for      │
                                     │  instant rollback          │
                                     └──────────────────────────┘
```

**Inference request with fallback flow:**

```
Incoming Request (user_id, feature, team metadata)
    │
    v
┌──────────────────┐
│  API Gateway     │── Auth, rate limit (token-aware), DDoS protection
│  (Kong/Apigee)   │── Centralized per-provider-key Redis limiter
└────────┬─────────┘
         │
         v
┌──────────────────┐
│  Cost Controller │── Check user daily budget → block if exceeded
│                  │── Tag request: user_id, feature, team
└────────┬─────────┘
         │
         v
┌──────────────────┐
│  Input Guardrails│── Layer 1: Schema/length validation (<5ms)
│                  │── Layer 2: Prompt hardening
│                  │── Layer 3: Llama Prompt Guard 2 (20-50ms)
│                  │── Reject or sanitize → return early if unsafe
└────────┬─────────┘
         │
         v
┌──────────────────┐
│  Model Router    │── Classify complexity (simple/med/complex)
│  (RouteLLM)      │── Check prompt cache (provider-side, 90% discount)
│                  │── Check semantic cache (pgvector, 25-35% hit rate)
│                  │── Route to cheapest capable model
└────────┬─────────┘
         │
         v
┌──────────────────┐     ┌──────────────────┐
│  PRIMARY MODEL   │     │  CIRCUIT BREAKER  │
│  (e.g., Claude   │────>│                   │
│   Sonnet 4)      │     │  State: CLOSED    │
│                  │     │  Failures: 0/5    │
└────────┬─────────┘     └──────────────────┘
         │
         ├── Success ──────────────────────────────────────────┐
         │                                                      │
         ├── Failure (5 consecutive) ──> Circuit OPEN           │
         │     │                                                │
         │     v                                                │
         │  ┌──────────────────┐                                │
         │  │  SECONDARY MODEL │  (different provider)          │
         │  │  (GPT-4o-mini)   │── Circuit: 5 fail / 60s cool  │
         │  └────────┬─────────┘                                │
         │           │                                          │
         │           ├── Success ──────────────────────────┐    │
         │           │                                      │    │
         │           ├── Failure ──>                         │    │
         │           │  ┌──────────────────┐                │    │
         │           │  │  TERTIARY MODEL  │                │    │
         │           │  │  (local/lighter) │                │    │
         │           │  └────────┬─────────┘                │    │
         │           │           │                          │    │
         │           │           ├── Failure ──>             │    │
         │           │           │  ┌──────────────────┐    │    │
         │           │           │  │  DETERMINISTIC   │    │    │
         │           │           │  │  RULES / HUMAN   │    │    │
         │           │           │  │  ESCALATION      │    │    │
         │           │           │  └────────┬─────────┘    │    │
         │           │           │           │              │    │
         v           v           v           v              v    v
┌──────────────────────────────────────────────────────────────────┐
│  Output Guardrails                                               │
│  - LlamaGuard 3 8B hazard classification (15-60ms at p50)        │
│  - PII redaction (Presidio)                                      │
│  - Structural validation (Guardrails AI / Pydantic)              │
│  - Hallucination check                                           │
└────────┬─────────────────────────────────────────────────────────┘
         │
         v
┌──────────────────┐
│  Cost Ledger     │── Log tokens used, model, cost, user_id, feature
│  + Telemetry     │── Emit OTel span with gen_ai semantic conventions
│                  │── Update budget utilization counters
└────────┬─────────┘
         │
         v
    Response to Client
```

**Checkpoint/resume flow (long-running agent):**

```
Agent Task Starts (thread_id = deterministic business key)
    │
    v
┌───────────────────────────────┐
│  LangGraph Orchestrator       │
│                               │
│  Step 1: Plan generation      │
│     │                         │
│     ├── Execute ──> Result    │
│     ├── Save state ──>        │──> PostgresSaver
│     │   checkpoint_1          │    (external, durable)
│     │                         │
│  Step 2: Tool call (API)      │
│     │                         │
│     ├── Execute ──> Result    │
│     ├── Save state ──>        │──> PostgresSaver
│     │   checkpoint_2          │    (idempotency key
│     │                         │     = thread_id + step_id)
│     │                         │
│  Step 3: LLM analysis         │
│     │                         │
│     ├── CRASH / TIMEOUT ──X   │
│     │                         │
└───────────────────────────────┘

    --- Process restarts ---

┌───────────────────────────────┐
│  Recovery                     │
│                               │
│  1. Load thread_id state      │
│     from PostgresSaver        │
│  2. Find last checkpoint:     │
│     checkpoint_2              │
│  3. Resume from Step 3        │
│     (skip Steps 1-2, use      │
│      recorded results)        │
│  4. Idempotency check:        │
│     if Step 3 partially       │
│     completed, skip or        │
│     re-execute safely         │
│                               │
└───────────────────────────────┘

Decision heuristic: checkpoint if resuming saves >5 min of compute
or >$2 in LLM token spend. Skip for sub-minute steps.
```

---

## 2. Core Mechanics & Algorithms

### 2.1 Containerization: Docker for AI Workloads

**Multi-stage builds** separate build-time dependencies (compilers, CUDA dev toolkits, pip build tools) from the runtime image. A single-stage Dockerfile for an LLM agent service balloons past 2GB because pip compiles C extensions, LLM SDKs pull CUDA libraries, and build tooling persists at runtime. Multi-stage builds produce images under 500MB with a reduced attack surface.

**GPU passthrough** requires the NVIDIA Container Toolkit. Docker cannot natively communicate with GPU drivers; the toolkit bridges the host GPU to the container runtime. Configuration uses `deploy.resources.reservations.devices` in Compose. For an 8B parameter model, 32GB memory limits provide comfortable headroom. The `NVIDIA_VISIBLE_DEVICES` environment variable controls which GPUs the container can access.

**Health checks must accommodate model loading**. LLMs take 1-5 minutes to load weights into GPU memory. The Docker `start_period` (e.g., 300 seconds) prevents premature container kills during loading. A readiness probe that fails keeps the pod running but removes it from the load balancer -- traffic never routes to a container still loading weights.

**MCP tool containers**: Docker Hub now hosts pre-built MCP (Model Context Protocol) servers -- PostgreSQL, Slack, Google Search, filesystem access -- that integrate as sidecar containers alongside agent services. The 2026 Docker AI stack includes Model Runner, MCP Gateway, Docker Offload, Agents, and Sandboxes.

**Right-sizing rule**: Most teams need Docker Compose plus a deploy script. Kubernetes is justified at 5+ services. Managed services (Cloud Run, Fargate) abstract complexity for small teams. The best architecture is one the team can actually operate.

### 2.2 Kubernetes Orchestration

**Scale of adoption**: The 2026 CNCF Annual Survey found 66% of organizations running generative AI inference use Kubernetes, driven by Dynamic Resource Allocation (DRA) and native gang scheduling.

**The HPA blind spot**: AI agents spend most compute time waiting for LLM API responses. CPU utilization stays low even when the system is saturated -- CPU reads 5% while GPU SM utilization spikes to 95%. HPA sees nothing alarming and does not scale. A system can be completely overloaded with pending requests at 30% CPU.

**KEDA solves the right signal problem**. KEDA scales inference pods to zero during inactivity and triggers scale-up based on queue depth, HTTP request rate, or custom event sources. KEDA's multi-trigger OR semantics (take the max across utilization and queue depth) express "scale on whichever signal fires first" without chaining HPAs that fight over one replica count. Two HPAs on one Deployment is a conflict; two triggers in one ScaledObject is the documented pattern.

**Cold start is the hard problem**: Every GPU pod restart means 3-10 minutes of image pulling, model weight loading, CUDA graph capture, and KV cache warming. Solve with PVC-backed model storage first (weights on a persistent volume, not baked into the image), then add autoscaling on top. Kubernetes 1.37 graduates HPA scale-to-zero to beta.

**GPU utilization reality**: Cast.ai 2026 report found average GPU utilization across production clusters is 5%. An idle H100 on AWS p5 costs ~$12.30/GPU-hr on-demand. Fewer than 2% of GPU workloads ran on Spot in 2025. Karpenter provisions optimal node types per workload rather than scaling a fixed node group.

### 2.3 CI/CD for AI Applications

Standard deployments version code; AI deployments version three tightly coupled artifacts simultaneously: **code, data, and model weights**. A change to any one can break production silently -- the system keeps returning HTTP 200 while quality degrades.

**Semantic evaluation replaces exact-match assertions**. LLMs return varying text, making deterministic testing useless for quality. Teams use a separate LLM-as-judge to grade output quality during automated testing. The minimum viable CI/CD: version control for all artifacts (code, prompts, configs), 20-30 behavioral tests before every deployment, and one agent-specific metric in monitoring (response quality or hallucination rate).

**Canary deployments for AI**: Roll out the new model/prompt to 5% of traffic, observe semantic quality, safety signals, and cost per request. If the canary passes quality gates, gradually increase traffic share. Automatic rollback ties to specific quality metrics -- hallucination rate exceeding baseline by >2 standard deviations, guardrail bypass rate increasing, or cost-per-request spiking.

**Blue-green vs canary trade-off**: Blue-green means paying for two full LLM stacks during cutover. Canary runs mostly one stack plus a thin slice. Most teams canary the new version to 100%, then treat it as the new blue, keeping the old version warm for instant rollback.

**Dominant 2026 stack**: LangGraph + GitHub Actions + ArgoCD + OpenTelemetry -- composable, open-source, cloud-agnostic.

**DORA finding**: While AI code generation tools help teams write more code, delivery throughput is decreasing by 1.5% and stability is worsening by 7.5%. More code does not mean better outcomes.

### 2.4 Guardrails Architecture

**NeMo Guardrails (NVIDIA)**: Middleware between application and LLM. Every user message passes through an input rail pipeline before reaching the model; every response passes through an output rail pipeline before reaching the user. Five rail types: input, dialog, retrieval, execution, output. Built-in jailbreak heuristics, self-check moderation, Presidio PII detection, LlamaGuard integration. Colang 1.0/2.0 flow syntax. NVIDIA explicitly states the project is not recommended for production as-is (v0.17.0, Oct 2025). The 2026 engine reduces baseline overhead by 40% vs 2025.

**LlamaGuard (Meta)**: LLM-based classifier that categorizes prompts and responses as safe/unsafe against a harm taxonomy. NeMo is an orchestration framework; LlamaGuard is a classification model. They compose -- NeMo orchestrates LlamaGuard calls.

**Production stack**: NeMo Guardrails orchestrating Llama Prompt Guard 2 86M (fast first-pass gate, 20-50ms on H100 with FP8) and LlamaGuard 3 8B (detailed hazard classification). Co-located rail evaluation adds 15-60ms per request at p50. Keeping under 80ms p99 requires batched classifier inference with a 5-20ms accumulation window and FP8/INT4 quantized weights. For Llama 3.3 70B + LlamaGuard 3 8B, a 2x H100 SXM5 configuration works well.

**Guardrails AI**: Open-source Python framework enforcing quality constraints through composable validators. The Guard object orchestrates validation from 50+ pre-built validators (PII detection, toxicity, regex matching, competitor mentions). Pydantic integration for structured output. Configurable failure actions: fix, reask, exception, filter.

**Latency optimization**: Run independent guardrail checks in parallel. A 200ms serial pipeline becomes 70ms when parallelized. Phased rollout: Weeks 1-2 monitor mode, Weeks 3-4 soft enforcement, Month 2+ full enforcement.

**Six-layer defense-in-depth**: (1) Input validation, (2) Prompt template hardening, (3) Retrieval/RAG rail, (4) Output filtering (PII, content moderation, hallucination detection), (5) Tool-call gating (allowlists, parameter validation), (6) Audit and compliance logging. Each layer is independent -- a bypass of one does not compromise the others.

**Threat context**: OWASP 2025 Top 10 for LLMs: LLM01 Prompt Injection, LLM06 Excessive Agency. 77% of enterprises faced GenAI breaches in 2025 (IBM). EU AI Act high-risk obligations apply from August 2, 2026, with penalties up to 7% of global annual turnover.

### 2.5 Cost Optimization Stack

**Prompt caching** -- highest-leverage, lowest-risk cost reduction in 2026. Cuts input bill on repeated prefixes by up to 90% with no change to model output.

| Provider | Trigger | Cache Read Cost | Cache Write Cost | Retention | Min Tokens |
|----------|---------|----------------|-----------------|-----------|------------|
| OpenAI | Automatic | 0.1x input | 1.25x input (GPT-5.6+) | 30 min | 1,024 |
| Anthropic | Explicit `cache_control` | 0.1x input | 1.25x (5-min) / 2x (1-hr extended) | 5 min / 1 hr | varies |
| Google Gemini | Implicit (2.5+) | Automatic | Automatic | varies | 2,048 |

Academic validation: "Don't Break the Cache" (arXiv 2601.06007) tested 500+ agent sessions with 10K-token system prompts -- caching reduced costs 41-80% and improved TTFT 13-31%.

**Semantic caching**: Matches on meaning rather than exact token prefix using embedding similarity. 25-35% cache hit rates on chatbot workloads. Implementable with pgvector. Useful when users ask semantically identical questions with different phrasing.

**Model routing**: ~70% of production traffic is simple (intent classification, text rewriting) that a small model handles at full quality. RouteLLM reports >85% cost reduction on MT-Bench while retaining 95% of GPT-4 quality. Critical warning: build the eval first, then optimize -- otherwise you are cutting cost blind.

**Batching**: OpenAI Batch API gives 50% discount for requests that can wait up to 24 hours. Anthropic offers similar batch pricing. Batching and caching compound: cached prefixes get both the 50% batch discount and the 90% cache discount.

**Prompt compression**: LLMLingua reduces input tokens 2-5x with minimal quality degradation -- a technique almost no production team has adopted yet.

**Combined savings**: Production teams report 60-80% bill reduction when caching, batching, and routing all apply. Teams with high-volume async workloads that add batch processing reach 75-85%.

### 2.6 Fallback Chains

**Provider reliability reality**: Anthropic had 114 incidents in a 90-day window in early 2026. OpenAI's 99.76% uptime translates to ~16 hours/year downtime. Average API uptime across all providers fell from 99.66% to 99.46% between Q1 2024 and Q1 2025.

**Chain structure**: Primary (best-suited model) -> Secondary (cheaper/faster, different provider) -> Tertiary (lighter model, simplified capability) -> Rule-based deterministic fallback -> Human escalation. Each step delivers value with progressively less sophistication.

**Order by cost, not just availability**: If fallback chain goes Claude Sonnet -> GPT-4o, spending triples during Anthropic outages. Try cheaper before trying different. Community consensus: 5 failures to trip circuit breaker, 60-second cooldown before testing recovery.

**Fallback is distinct from retry**: Retry re-issues against the same model; fallback issues against a different one. Most production systems retry first (2-3 attempts with exponential backoff), then fall back. Agent runs with configured fallback chains saw 38% lower task-abandonment rates during simulated provider outages.

**Monitoring signal**: If the secondary model handles >5% of traffic, something is wrong with the primary setup. Track fallback rate, circuit breaker open rate, context compaction frequency, and tool failure rate by tool.

### 2.7 Checkpoint/Resume Patterns

For long-running agents (>4 hours), systems without state persistence have a 90% higher risk of total task failure due to API timeouts or infrastructure disruptions. LangChain's 2026 State of Agent Engineering report ties >60% of production incidents to state management.

**Three granularity models**:

1. **Node-level (LangGraph)**: Every graph node triggers a write. A 50-step workflow generates 50 persisted states. Supports memory, fault recovery, state history, time travel, and human-in-the-loop interrupts. PostgresSaver for production; never MemorySaver (lost on process death).

2. **Activity-level (Temporal)**: Each Activity is recorded in Event History. Workflow code replays against history on recovery, skipping completed Activities by using recorded results. Append-only, compacted -- more efficient than per-node storage for high-step-count workflows.

3. **Explicit commit points**: Developer manually inserts save calls at "safe" boundaries. Coarser granularity, easier to reason about, lowest overhead.

**Decision heuristic**: If resuming from a point saves more than 5 minutes of compute or more than $2 in LLM tokens, checkpoint there. For sub-minute steps, the checkpoint overhead exceeds the recovery benefit.

**Framework support**: Google ADK (automatic checkpoint per tool call, SQLite locally / managed cloud in production, no code changes), Mastra (compact JSON serialization, per-step records), AWS Lambda Durable Functions (Dec 2025: steps, waits, replays, long suspensions), Microsoft Durable Task for AI agents (Apr 2026: checkpointing and coordination).

**Key pitfalls**: Checkpointing too often (overhead dominates), ignoring idempotency (duplicate outputs on replay -- wrap all external calls as idempotent operations tied to workflow + step identity), storing state in-memory only (lost on process death).

### 2.8 Model Serving Infrastructure

| Framework | Throughput | TTFT p95 | Key Advantage | Status |
|-----------|-----------|----------|---------------|--------|
| **vLLM** | Highest (PagedAttention, <4% memory waste) | 0.89s | 24x higher throughput than TGI at high concurrency; 85-92% GPU util at 100+ users. #1 OSS project by contributors (Octoverse 2025) | Active, production standard |
| **TGI** | Moderate | 0.45s (lowest) | 13x speedup on 200K+ token prompts (TGI v3). Lowest time-to-first-token | **Maintenance mode** -- HuggingFace recommends vLLM or SGLang |
| **NVIDIA NIM** | ~15% edge over raw vLLM | Similar to vLLM | Pre-optimized TensorRT-LLM containers, zero-config deployment, enterprise security scanning | Active, licensed |
| **SGLang** | 29% above vLLM on shared-context | Similar to vLLM | RadixAttention for multi-turn/agentic workloads. Best for chatbots, RAG, agents | Active, rising |

**Production pattern**: Ray Serve + vLLM workers for continuous batching and autoscaling. For 90% of teams, vLLM is the right choice -- NIM's premium rarely justifies the licensing cost. SGLang wins specifically for shared-context workloads (chatbots, RAG, agents). TGI is effectively deprecated.

**Infrastructure choice matrix**:

- **Serverless GPU** (Modal at ~$4.50/hr H100, RunPod at ~$2.50/hr): For bursty workloads under 30% GPU utilization, almost always cheaper than reserved instances. Limitation: 12-18% batching efficiency vs 65-80% on dedicated servers.
- **Containers** (Cloud Run): Hybrid model -- scale-to-zero, pay-per-request, real containers with NVIDIA L4 GPU support. No platform-imposed request timeout for streaming. Most natural fit for LLM streaming.
- **Dedicated GPU** (K8s + vLLM): For sustained high-throughput serving. Batching efficiency 65-80%. Required when latency or data residency constraints preclude serverless.
- **Edge** (Cloudflare Workers, Deno Deploy): Sub-millisecond cold starts, 300+ global locations, 70% cost savings. Constraint: 1-7B quantized models only, not frontier-class.

---

## 3. Token Economics & NFR Analysis

### 3.1 Cost Formulas

**Per-request cost attribution**:

```
Request_Cost = (Input_Tokens * Input_Rate) + (Output_Tokens * Output_Rate)
             - Cache_Discount - Batch_Discount
             + Guardrail_Cost + Embedding_Cost

where:
  Cache_Discount  = Cached_Input_Tokens * Input_Rate * 0.9    (90% savings on cache hits)
  Batch_Discount  = (Input_Cost + Output_Cost) * 0.5          (50% for batch API)
  Guardrail_Cost  = Guard_Model_Tokens * Guard_Model_Rate      (Prompt Guard + LlamaGuard)
  Embedding_Cost  = Chunks * Embedding_Rate                    (for semantic cache / RAG)
```

**Worked example -- single agentic request with 3 tool calls**:

```
Model: Claude Sonnet 4 ($3/1M input, $15/1M output)
System prompt: 4,000 tokens (cached after first request)
User query: 500 tokens
3 tool calls: 1,500 tokens each (input + output combined)
Final response: 800 tokens output

First request (no cache):
  Input:  4,000 + 500 + (3 * 750 avg input) = 6,750 tokens = $0.020
  Output: 800 + (3 * 750 avg output) = 3,050 tokens         = $0.046
  Guardrail: Prompt Guard ~100 tokens, LlamaGuard ~200 tokens = $0.001
  Total: ~$0.067

Subsequent requests (system prompt cached):
  Input:  4,000 * $0.3/1M (cached) + 2,750 * $3/1M          = $0.010
  Output: 3,050 * $15/1M                                     = $0.046
  Guardrail:                                                  = $0.001
  Total: ~$0.057  (15% savings from cache alone)

With batch API (async-tolerant):
  Total: ~$0.057 * 0.5 = ~$0.029  (57% savings from cache + batch)
```

**GPU infrastructure cost formulas**:

```
Self-Hosted_Monthly = GPU_Count * Hours_Per_Month * Rate_Per_GPU_Hr
                    + Cluster_Overhead (K8s nodes, networking, storage)

Example: 2x H100 for Llama 3.3 70B + LlamaGuard 3 8B
  On-demand (AWS):    2 * 730 * $12.30 = $17,958/month
  Spot (RunPod):      2 * 730 * $1.19  = $1,737/month   (90% savings, preemption risk)
  Reserved 1yr (AWS): 2 * 730 * $7.38  = $10,775/month  (40% discount)
  Specialist (Lambda): 2 * 730 * $3.99 = $5,825/month

Break-even vs API: Self-hosted wins when sustained inference exceeds
  ~$6K/month in API spend (for on-demand) or ~$2K/month (for spot).
```

**H100 cost by provider (Sep 2026)**:

| Provider | On-Demand (/GPU-hr) | Spot (/GPU-hr) | 1-Year Reserved |
|----------|-------------------|---------------|-----------------|
| AWS p5.48xlarge | $12.29 | N/A for H100 | ~$7.38 (40% off) |
| Azure ND H100 v5 | $12.25-$14.50 | Varies | Steepest discount curve |
| GCP a3-highgpu-8g | $9.00-$11.50 | ~$2.25 (60-91% off) | ~$6.00 (35% off) |
| RunPod | $2.99 | $1.19 | N/A |
| CoreWeave | $6.15 | N/A | Volume discounts |
| Lambda Labs | $3.99 | N/A | N/A |
| Spheron | $1.07 (A100 80GB) | $1.03 (H100 floor) | N/A |

**Key ratio**: H100 costs ~3x A100 and delivers ~3x training throughput, making cost-per-token roughly equivalent across GPU generations. The real savings come from software optimization (vLLM batching, quantization), not GPU generation.

### 3.2 Latency SLA Targets

| Metric | Target | Measured Reality | Mitigation |
|--------|--------|-----------------|------------|
| TTFT (time-to-first-token) | <1.0s p95 | vLLM: 0.89s p95; TGI: 0.45s p95 | SGLang RadixAttention for multi-turn; prompt caching reduces TTFT 13-31% |
| Guardrail overhead | <80ms p99 | 15-60ms p50 co-located | Batched classifier inference with 5-20ms accumulation window, FP8 quantization |
| Cold start (GPU pod) | <3 min | 3-10 min typical | PVC-backed model storage; provisioned concurrency; model weight pre-caching |
| Cold start (Lambda) | <500ms | 100ms-1s | Provisioned concurrency (charged since Aug 2025) |
| Cold start (edge) | <5ms | Sub-millisecond | Native advantage of edge functions; 1-7B models only |
| End-to-end agent turn | <5s p95 | 2-15s depending on tool count | Parallel tool calls; streaming; cached system prompts |
| Streaming latency | No timeout | Platform-dependent | Cloud Run: no streaming timeout. Lambda: 15-min hard timeout |

### 3.3 Throughput and Capacity Planning

**Scale signals** -- what to scale on vs what to ignore:

| Signal | Scale On? | Why |
|--------|----------|-----|
| CPU utilization | No | AI workloads are I/O-bound waiting for LLM responses. CPU reads 5% at full saturation |
| GPU SM utilization | Supplementary | Spikes to 95% under load but fluctuates rapidly |
| `vllm:num_requests_waiting` | Yes (primary) | Direct measure of inference queue pressure |
| Redis Stream pending entries | Yes (primary) | Leading indicator of consumer lag |
| Queue depth (SQS/Redis) | Yes (primary) | KEDA's best trigger for async workloads |
| HTTP request rate | Yes (supplementary) | Good for API-fronted services; KEDA HTTP scaler |

**Scaling dynamics**:
- Scale up fast: 1-3 minutes with cached model weights on PVC.
- Scale down slowly: stabilization windows, one pod at a time, to avoid thrashing.
- Never scale to zero in production unless cold start latency is tolerable for the use case.

**Queue-based capacity planning**: Put the LLM call and tool call on opposite sides of a Redis Streams queue so a slow/failing tool does not stall the agent loop for every user. Watch consumer lag (pending-entries count per consumer group) as the leading indicator.

**Batching efficiency gap**: Naive serverless LLM deployment achieves 12-18% batching efficiency vs 65-80% on dedicated inference servers with continuous batching (vLLM). High-throughput serving is almost always cheaper on dedicated GPU infrastructure than on per-request serverless.

### 3.4 Non-Functional Requirements

| Requirement | Target | Implementation |
|------------|--------|----------------|
| **Availability** | 99.9%+ effective | Multi-provider fallback chains (single providers deliver 99.46-99.76%). Active-passive multi-region with cross-region checkpoint sync |
| **RPO** (Recovery Point Objective) | <5 min for agent state | PostgresSaver with WAL replication; checkpoint at every graph node. Cross-region async replication |
| **RTO** (Recovery Time Objective) | <15 min for full recovery | Pre-provisioned warm standby; ArgoCD instant rollback to known-good version; PVC-backed models (no re-download) |
| **Cost governance** | <$X/month per feature | Per-request cost attribution tags. Hard caps per user. Soft alerts at 50%/80% of budget. Rolling baseline deviation alerts |
| **Compliance** | SOC 2 Type II, HIPAA (where applicable), GDPR, EU AI Act | Immutable audit trail per model call. Model version traceability. VPC isolation for PHI. Human-in-the-loop gates for high-risk. PII redaction in telemetry |
| **Security** | Zero trust, defense in depth | Per-agent least-privilege credentials. Signed container images. Dependency pinning. API gateway with DDoS protection. mTLS between services |

**Combined optimization savings (realistic enterprise)**:

```
Starting point: $25,000/month (all traffic to frontier model, no optimization)

Step 1: Model routing (70% to cheap model)        → $10,000  (60% reduction)
Step 2: Prompt caching (90% input on repeats)      → $7,000   (30% on remaining)
Step 3: Semantic caching (30% hit rate)             → $5,000   (29% further)
Step 4: Batch API for async (50% discount)          → $4,000   (20% further)
Step 5: Prompt compression on remaining             → $3,500   (14% further)

Total: ~85% reduction from naive baseline ($25K → $3.5-4K/month)
```

---

## 4. Distributed Resilience & Security

### 4.1 High Availability

**Multi-region architecture** -- three approaches:

| Approach | Resilience | Cost | Complexity | Best For |
|----------|-----------|------|------------|----------|
| Active-Active | Highest (instant failover) | 2x+ infrastructure | High (conflict resolution, state sync) | Mission-critical, low-latency global |
| Active-Passive | High (minutes RTO) | 1.3-1.5x infrastructure | Medium (one-way replication) | Most enterprise workloads |
| Hybrid | High (API layer active-active, GPU passive) | 1.5-1.8x | Medium-High | GPU-intensive with global API needs |

Recent reinforcement: regional cloud degradation in the Middle East and internet routing failures in late 2025 demonstrated that entire regions can become unreachable with little warning.

**Multi-provider as HA**: Using AI services across multiple providers is itself a deliberate HA strategy. You gain access to best-fit models and diversify failure domains. Front them with multi-region APIs and global routing for low latency. Portkey reports multi-provider adoption jumped from 23% to 40% of organizations in one year.

**Topology spread**: Use Kubernetes topology spread constraints or anti-affinity rules to spread replicas across Availability Zones and Fault Domains so a node/rack/zone loss does not take out capacity.

**Zero-downtime deployment**: Most outages are caused by changes, not hardware failure. HA architecture must include: rolling deployments (old and new versions overlap), blue-green releases (traffic switches between two complete environments), canary releases (small percentage to new version first), feature flags (disable risky behavior without redeploying). Old instances must finish in-flight work (graceful shutdown) before termination. Queues, sessions, and background jobs need special care during transitions.

### 4.2 Disaster Recovery

**AI-specific DR challenges**: AI deployments version code, data, and model weights simultaneously. DR must preserve and synchronize all three. The EU AI Act and SOC 2 Type II require tracing which model version served a specific response and why it was deployed.

**AI-driven proactive DR**: ML algorithms now monitor telemetry (disk I/O latency, network packet drops, CPU utilization anomalies) to predict failures before they trigger outages, shifting DR from reactive to proactive.

**Task queue failover**: SQS or Redis Streams with cross-region replicas, checkpoint stores with cross-region sync, ephemeral sandboxes via microVMs. Always set `MAXLEN` on Redis streams -- unbounded streams exhaust memory during worker outages.

**RPO/RTO for AI systems**: State is the critical dimension. Agent checkpoint data (PostgresSaver) requires WAL replication with <5-minute RPO. Model weights are immutable artifacts stored in registries -- RPO is effectively zero if the registry is geo-replicated. Prompts and configs are version-controlled in Git -- RPO depends on push frequency.

**2026 challenge**: Finding non-AI cloud capacity. Providers have invested billions in AI infrastructure while underinvesting in "standard" capacity, making multi-region DR harder to provision for non-GPU workloads.

### 4.3 Queue Architectures

**Redis Streams as backbone**: Recommended stack for AI agents -- FastAPI orchestrator + LLM API + Redis Streams (tool-execution queue) + API gateway (Kong) for auth and rate limiting. Put the LLM call and tool call on opposite sides of a queue so a slow/failing tool does not stall the agent loop for every user.

**Consumer lag as leading indicator**: Watch pending-entries count per consumer group, not CPU. A long-running LLM call occupies a consumer slot for its duration; pending entries climb fast if consumers cannot keep pace.

**Celery + Redis for GPU inference**: Decouple AI inference from the web tier. Redis or RabbitMQ as broker, prefork workers at `concurrency=1` on GPU nodes (one model per worker). Scale with KEDA or Kubernetes HPA driven by queue depth.

**Operational pitfalls**:
- Always set `MAXLEN` on Redis streams (unbounded streams exhaust memory during worker outages)
- Do not co-locate Redis on the same node pool as GPU-bound pods (resource contention)
- Do not run the result backend on the same Redis instance as the broker (backpressure propagation)
- Use tenant ID as Redis key prefix for multi-tenant rate limiting -- never share a single counter across tenants

**Token-aware rate limiting**: Traditional RPS rate limiting breaks for AI -- a single LLM call can consume thousands of tokens and occupy a GPU for seconds. Centralize rate limiting per provider key in Redis. Use Lua scripts for atomicity -- `WATCH`-based approaches cause frequent aborts under high concurrency. RL-based adaptive rate limiters report 30% fewer false positives and 25% fewer false negatives vs static rules.

### 4.4 Enterprise Security

**API security and identity management**: Only 21% of organizations maintain a real-time agent registry. Only 18% of security leaders believe their IAM can handle AI agent identities. 97% of breached organizations lacked proper access controls for their AI systems (IBM 2025).

Best practices: Per-agent least-privilege credentials, mandatory credential rotation, API key scoping to specific models and rate limits, centralized API gateway (Kong, Apigee) for auth and DDoS protection.

**Secret management**: Never bake secrets into Docker images. Store in `.env` files outside the image, mount at runtime. Use Pydantic Settings for configuration management across environments. Production: HashiCorp Vault, AWS Secrets Manager, or GCP Secret Manager with automatic rotation.

**Network security**: VPC-isolated deployment for regulated workloads -- production inference traffic, prompt content, and model responses remain within the customer's cloud environment. Private endpoints, mTLS between services. Enterprise tiers: SaaS with compliance attestations, VPC isolation, air-gapped deployment for maximum security.

**Compliance frameworks**:

| Framework | Key Requirements | Penalty | AI-Specific Considerations |
|-----------|-----------------|---------|---------------------------|
| **SOC 2 Type II** | 2026 updates explicitly address AI governance criteria. Now a procurement requirement, not a differentiator | Loss of enterprise deals | Audit trail on every model call. 40% of enterprise apps will integrate AI agents by end of 2026 (Gartner) |
| **HIPAA** | BAA required before transmitting PHI. Standard consumer APIs (OpenAI/Anthropic/Google) generally do not provide BAAs | $7.42M average breach cost (IBM 2025, highest industry for 14 consecutive years) | VPC-isolated or self-hosted deployment provides cleanest boundary. Without BAA, any PHI transmission is a violation |
| **GDPR** | EDPB clarified prompts containing personal data trigger full GDPR protections | Up to EUR 35M or 7% of global annual turnover | PII redaction in telemetry. Data residency. Right to erasure applies to training data |
| **EU AI Act** | High-risk requirements active August 2, 2026. Must trace model version per response and deployment rationale | Up to 7% of global annual turnover | Human-in-the-loop gates. OPA policies. Model versioning and traceability are non-negotiable |

**BCG 2026**: 73% of enterprise AI initiatives now name compliance posture as a top-three vendor selection criterion (up from 41% in 2024). Retrofitting governance after the audit notice typically costs 2-3x the original build.

**Supply chain security -- the LiteLLM incident**: In March 2026, backdoored versions of litellm (1.82.7, 1.82.8) were published to PyPI by a malicious actor (TeamPCP). The packages were live for ~40 minutes before quarantine. LiteLLM is downloaded ~3.4 million times per day. This attack demonstrated that even brief exposure windows in high-download-count packages create significant blast radius.

Mitigations: Container scanning in CI/CD pipelines, dependency pinning with hash verification, signed images, provenance verification. NIM containers include vulnerability scanning. For self-hosted stacks, enforce signed images and scan every layer.

**Content safety**: OWASP 2025 Top 10 for LLMs prioritizes Prompt Injection (LLM01), Excessive Agency (LLM06), Supply Chain (LLM03). 88% of organizations deploying AI agents reported at least one security incident in 2025. Guardrails are circumvented by indirect injection (through retrieved documents), multi-turn escalation, and encoding-based evasion. Defense requires all six layers of the defense-in-depth model.

---

## 5. Production Enterprise Code

### 5.1 Docker Multi-Stage Build for AI Application

```dockerfile
# Dockerfile -- Multi-stage build for LLM agent service
# Stage 1: Builder -- installs dependencies, compiles C extensions
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime -- slim image, no build tools
FROM python:3.12-slim AS runtime

# Security: non-root user
RUN groupadd -r agent && useradd -r -g agent -d /app -s /sbin/nologin agent

WORKDIR /app

# Copy only installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY --chown=agent:agent . .

# Health check with long start_period for model loading
HEALTHCHECK --interval=30s --timeout=10s --start-period=300s --retries=3 \
    CMD python -c "import requests; r = requests.get('http://localhost:8000/health'); exit(0 if r.status_code == 200 else 1)"

USER agent

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

```yaml
# docker-compose.yml -- Agent service with GPU passthrough
version: "3.8"

services:
  agent:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - REDIS_URL=redis://redis:6379
      - DATABASE_URL=postgresql://agent:${DB_PASSWORD}@postgres:5432/agent_state
    env_file:
      - .env  # secrets mounted at runtime, never baked into image
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
        limits:
          memory: 32G
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy

  redis:
    image: redis:7-alpine
    command: redis-server --maxmemory 512mb --maxmemory-policy allkeys-lru
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: agent_state
      POSTGRES_USER: agent
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U agent"]
      interval: 10s
      timeout: 5s
      retries: 3

volumes:
  pgdata:
```

### 5.2 KEDA-Based Autoscaling Configuration

```yaml
# keda-scaledobject.yaml -- Multi-trigger autoscaling for AI inference
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: inference-service-scaler
  namespace: ai-inference
spec:
  scaleTargetRef:
    name: inference-service
  pollingInterval: 15
  cooldownPeriod: 300           # 5 min cooldown to avoid thrashing
  minReplicaCount: 1            # Never scale to zero in prod
  maxReplicaCount: 10
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleUp:
          stabilizationWindowSeconds: 60    # Scale up fast
          policies:
            - type: Pods
              value: 2                       # Add up to 2 pods per 60s
              periodSeconds: 60
        scaleDown:
          stabilizationWindowSeconds: 300   # Scale down slowly
          policies:
            - type: Pods
              value: 1                       # Remove 1 pod at a time
              periodSeconds: 120
  triggers:
    # Trigger 1: Redis Streams queue depth (primary signal)
    - type: redis-streams
      metadata:
        address: redis.ai-inference.svc.cluster.local:6379
        stream: inference-requests
        consumerGroup: inference-workers
        pendingEntriesCount: "10"           # Scale when >10 pending
        lagCount: "5"                        # or consumer lag >5

    # Trigger 2: Prometheus custom metric (vLLM waiting requests)
    - type: prometheus
      metadata:
        serverAddress: http://prometheus.monitoring.svc.cluster.local:9090
        query: |
          sum(vllm_num_requests_waiting{namespace="ai-inference"})
        threshold: "5"                       # Scale when >5 requests waiting

    # Trigger 3: HTTP request rate (supplementary)
    - type: prometheus
      metadata:
        serverAddress: http://prometheus.monitoring.svc.cluster.local:9090
        query: |
          sum(rate(http_requests_total{service="inference-service"}[2m]))
        threshold: "50"                      # Scale at >50 req/s

    # KEDA multi-trigger OR semantics: takes MAX across all triggers.
    # "Scale on whichever signal fires first" without conflicting HPAs.
---
# Karpenter NodePool for GPU workloads -- provisions optimal node type
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
          values:
            - "p5.48xlarge"    # H100
            - "g5.xlarge"      # A10G (cheaper fallback)
            - "g6.xlarge"      # L4
        - key: "karpenter.sh/capacity-type"
          operator: In
          values:
            - "on-demand"
            - "spot"           # Enable spot for cost savings
      nodeClassRef:
        group: karpenter.k8s.aws
        kind: EC2NodeClass
        name: gpu-nodes
  limits:
    cpu: 128
    memory: 512Gi
    nvidia.com/gpu: 8
  disruption:
    consolidationPolicy: WhenEmptyOrUnderutilized
    consolidateAfter: 10m       # Wait 10 min before removing underutilized nodes
```

### 5.3 Guardrail Pipeline with Input/Output Filtering

```python
"""
Guardrail pipeline implementing the six-layer defense-in-depth model.
Runs independent checks in parallel for latency optimization.
"""

import asyncio
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import httpx


class GuardrailAction(Enum):
    ALLOW = "allow"
    BLOCK = "block"
    SANITIZE = "sanitize"


@dataclass
class GuardrailResult:
    action: GuardrailAction
    layer: str
    reason: Optional[str] = None
    sanitized_text: Optional[str] = None
    latency_ms: float = 0.0


@dataclass
class GuardrailMetrics:
    total_checks: int = 0
    blocks: int = 0
    sanitizations: int = 0
    total_latency_ms: float = 0.0
    checks_by_layer: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Layer 1: Input validation (deterministic, <1ms)
# ---------------------------------------------------------------------------
MAX_INPUT_LENGTH = 32_000  # characters
BLOCKED_ENCODINGS = re.compile(
    r"(\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}|&#x?[0-9a-fA-F]+;)", re.IGNORECASE
)


def validate_input(text: str) -> GuardrailResult:
    start = time.perf_counter()
    if not text or not text.strip():
        return GuardrailResult(
            action=GuardrailAction.BLOCK,
            layer="input_validation",
            reason="Empty input",
            latency_ms=(time.perf_counter() - start) * 1000,
        )
    if len(text) > MAX_INPUT_LENGTH:
        return GuardrailResult(
            action=GuardrailAction.BLOCK,
            layer="input_validation",
            reason=f"Input exceeds {MAX_INPUT_LENGTH} characters",
            latency_ms=(time.perf_counter() - start) * 1000,
        )
    if BLOCKED_ENCODINGS.search(text):
        cleaned = BLOCKED_ENCODINGS.sub("", text)
        return GuardrailResult(
            action=GuardrailAction.SANITIZE,
            layer="input_validation",
            reason="Encoding-based evasion attempt detected",
            sanitized_text=cleaned,
            latency_ms=(time.perf_counter() - start) * 1000,
        )
    return GuardrailResult(
        action=GuardrailAction.ALLOW,
        layer="input_validation",
        latency_ms=(time.perf_counter() - start) * 1000,
    )


# ---------------------------------------------------------------------------
# Layer 2: Prompt injection detection (pattern-based, <1ms)
# ---------------------------------------------------------------------------
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts)", re.I),
    re.compile(r"you\s+are\s+now\s+(a|an)\s+", re.I),
    re.compile(r"system\s*:\s*", re.I),
    re.compile(r"<\|im_start\|>|<\|im_end\|>", re.I),
    re.compile(r"\[INST\]|\[/INST\]", re.I),
    re.compile(r"forget\s+(everything|all|your)\s+(you|instructions|rules)", re.I),
]


def detect_injection(text: str) -> GuardrailResult:
    start = time.perf_counter()
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            return GuardrailResult(
                action=GuardrailAction.BLOCK,
                layer="injection_detection",
                reason=f"Prompt injection pattern detected: {pattern.pattern[:50]}",
                latency_ms=(time.perf_counter() - start) * 1000,
            )
    return GuardrailResult(
        action=GuardrailAction.ALLOW,
        layer="injection_detection",
        latency_ms=(time.perf_counter() - start) * 1000,
    )


# ---------------------------------------------------------------------------
# Layer 3: LLM-based safety gate (Llama Prompt Guard via API, 20-50ms)
# ---------------------------------------------------------------------------
async def llm_safety_gate(
    text: str, endpoint: str = "http://localhost:8080/v1/classify"
) -> GuardrailResult:
    """Call Llama Prompt Guard 2 86M for fast safety classification."""
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                endpoint,
                json={"text": text, "model": "prompt-guard-2-86m"},
            )
            response.raise_for_status()
            result = response.json()
            label = result.get("label", "safe")
            score = result.get("score", 0.0)

            if label == "unsafe" and score > 0.85:
                return GuardrailResult(
                    action=GuardrailAction.BLOCK,
                    layer="llm_safety_gate",
                    reason=f"Prompt Guard classified as unsafe (score={score:.3f})",
                    latency_ms=(time.perf_counter() - start) * 1000,
                )
    except (httpx.HTTPError, httpx.TimeoutException):
        # Safety gate failure: log and allow (fail-open for availability;
        # downstream output guardrails provide second line of defense)
        pass
    return GuardrailResult(
        action=GuardrailAction.ALLOW,
        layer="llm_safety_gate",
        latency_ms=(time.perf_counter() - start) * 1000,
    )


# ---------------------------------------------------------------------------
# Layer 4: PII detection and redaction (output filter)
# ---------------------------------------------------------------------------
PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d{4}[- ]?){3}\d{4}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "phone_us": re.compile(r"\b(?:\+1[-.]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
}


def redact_pii(text: str) -> GuardrailResult:
    start = time.perf_counter()
    redacted = text
    found_types = []
    for pii_type, pattern in PII_PATTERNS.items():
        if pattern.search(redacted):
            found_types.append(pii_type)
            redacted = pattern.sub(f"[REDACTED_{pii_type.upper()}]", redacted)
    if found_types:
        return GuardrailResult(
            action=GuardrailAction.SANITIZE,
            layer="pii_redaction",
            reason=f"PII detected and redacted: {', '.join(found_types)}",
            sanitized_text=redacted,
            latency_ms=(time.perf_counter() - start) * 1000,
        )
    return GuardrailResult(
        action=GuardrailAction.ALLOW,
        layer="pii_redaction",
        latency_ms=(time.perf_counter() - start) * 1000,
    )


# ---------------------------------------------------------------------------
# Layer 5: Tool-call gating
# ---------------------------------------------------------------------------
ALLOWED_TOOLS = {
    "web_search": {"max_queries": 5},
    "database_query": {"max_queries": 3, "read_only": True},
    "file_read": {"allowed_paths": ["/data/", "/config/"]},
}


def validate_tool_call(tool_name: str, params: dict) -> GuardrailResult:
    start = time.perf_counter()
    if tool_name not in ALLOWED_TOOLS:
        return GuardrailResult(
            action=GuardrailAction.BLOCK,
            layer="tool_gating",
            reason=f"Tool '{tool_name}' is not in the allowlist",
            latency_ms=(time.perf_counter() - start) * 1000,
        )
    tool_config = ALLOWED_TOOLS[tool_name]
    if tool_name == "file_read":
        path = params.get("path", "")
        if not any(path.startswith(p) for p in tool_config["allowed_paths"]):
            return GuardrailResult(
                action=GuardrailAction.BLOCK,
                layer="tool_gating",
                reason=f"File path '{path}' is outside allowed directories",
                latency_ms=(time.perf_counter() - start) * 1000,
            )
    return GuardrailResult(
        action=GuardrailAction.ALLOW,
        layer="tool_gating",
        latency_ms=(time.perf_counter() - start) * 1000,
    )


# ---------------------------------------------------------------------------
# Pipeline orchestrator: parallel execution for latency optimization
# ---------------------------------------------------------------------------
async def run_input_guardrails(text: str) -> tuple[GuardrailAction, str, list[GuardrailResult]]:
    """
    Run input guardrail layers. Independent checks run in parallel.
    A 200ms serial pipeline becomes ~70ms when parallelized.

    Returns: (action, possibly_sanitized_text, all_results)
    """
    # Layer 1 is fast and deterministic -- run first as a gate
    validation_result = validate_input(text)
    if validation_result.action == GuardrailAction.BLOCK:
        return GuardrailAction.BLOCK, text, [validation_result]

    working_text = validation_result.sanitized_text or text

    # Layers 2 and 3 are independent -- run in parallel
    injection_task = asyncio.to_thread(detect_injection, working_text)
    safety_task = llm_safety_gate(working_text)

    injection_result, safety_result = await asyncio.gather(injection_task, safety_task)

    all_results = [validation_result, injection_result, safety_result]

    # Any BLOCK stops the request
    for result in all_results:
        if result.action == GuardrailAction.BLOCK:
            return GuardrailAction.BLOCK, text, all_results

    return GuardrailAction.ALLOW, working_text, all_results


async def run_output_guardrails(text: str) -> tuple[GuardrailAction, str, list[GuardrailResult]]:
    """
    Run output guardrail layers on model response.
    PII redaction always runs; additional checks can be added.
    """
    pii_result = redact_pii(text)
    output_text = pii_result.sanitized_text or text
    return pii_result.action, output_text, [pii_result]
```

### 5.4 Cost Tracking Middleware with Budget Enforcement

```python
"""
Cost tracking middleware for FastAPI.
Implements per-request cost attribution, budget enforcement, and alerting.
"""

import time
from dataclasses import dataclass, field
from typing import Optional

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# Model pricing table (input/output per 1M tokens, Sep 2026)
MODEL_PRICING = {
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-haiku-4": {"input": 0.25, "output": 1.25},
    "claude-opus-4": {"input": 15.00, "output": 75.00},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}

BUDGET_THRESHOLDS = {"warning": 0.50, "critical": 0.80, "hard_cap": 1.00}


@dataclass
class CostRecord:
    request_id: str
    user_id: str
    feature: str
    team: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_usd: float
    timestamp: float
    latency_ms: float


def calculate_request_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    is_batch: bool = False,
) -> float:
    """
    Calculate cost for a single request with cache and batch discounts.

    Request_Cost = (Input_Tokens * Input_Rate) + (Output_Tokens * Output_Rate)
                 - Cache_Discount - Batch_Discount
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        raise ValueError(f"Unknown model: {model}")

    input_rate = pricing["input"] / 1_000_000
    output_rate = pricing["output"] / 1_000_000

    # Non-cached input tokens at full rate
    non_cached_input = max(0, input_tokens - cached_tokens)
    input_cost = non_cached_input * input_rate

    # Cached tokens at 10% of input rate (90% discount)
    cache_cost = cached_tokens * input_rate * 0.1

    output_cost = output_tokens * output_rate

    total = input_cost + cache_cost + output_cost

    # Batch API: 50% discount on everything
    if is_batch:
        total *= 0.5

    return round(total, 6)


class CostTracker:
    """Tracks per-user and per-feature costs in Redis with budget enforcement."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def check_budget(self, user_id: str, daily_budget_usd: float) -> dict:
        """Check if user is within budget. Returns status and current spend."""
        key = f"cost:daily:{user_id}:{time.strftime('%Y-%m-%d')}"
        current_spend = float(await self.redis.get(key) or 0)
        utilization = current_spend / daily_budget_usd if daily_budget_usd > 0 else 0

        status = "ok"
        if utilization >= BUDGET_THRESHOLDS["hard_cap"]:
            status = "exceeded"
        elif utilization >= BUDGET_THRESHOLDS["critical"]:
            status = "critical"
        elif utilization >= BUDGET_THRESHOLDS["warning"]:
            status = "warning"

        return {
            "status": status,
            "current_spend_usd": current_spend,
            "daily_budget_usd": daily_budget_usd,
            "utilization_pct": round(utilization * 100, 1),
        }

    async def record_cost(self, record: CostRecord) -> None:
        """Record cost and update all attribution dimensions."""
        pipe = self.redis.pipeline()
        date_str = time.strftime("%Y-%m-%d")

        # Per-user daily spend
        user_key = f"cost:daily:{record.user_id}:{date_str}"
        pipe.incrbyfloat(user_key, record.cost_usd)
        pipe.expire(user_key, 86400 * 7)  # retain 7 days

        # Per-feature daily spend
        feature_key = f"cost:feature:{record.feature}:{date_str}"
        pipe.incrbyfloat(feature_key, record.cost_usd)
        pipe.expire(feature_key, 86400 * 30)

        # Per-team daily spend
        team_key = f"cost:team:{record.team}:{date_str}"
        pipe.incrbyfloat(team_key, record.cost_usd)
        pipe.expire(team_key, 86400 * 30)

        # Per-model daily spend (for routing optimization analysis)
        model_key = f"cost:model:{record.model}:{date_str}"
        pipe.incrbyfloat(model_key, record.cost_usd)
        pipe.expire(model_key, 86400 * 30)

        # Global daily spend
        global_key = f"cost:global:{date_str}"
        pipe.incrbyfloat(global_key, record.cost_usd)
        pipe.expire(global_key, 86400 * 90)

        await pipe.execute()

    async def get_feature_spend(self, feature: str, days: int = 7) -> list[dict]:
        """Get daily spend for a feature over the last N days."""
        results = []
        for i in range(days):
            date_str = time.strftime(
                "%Y-%m-%d",
                time.gmtime(time.time() - i * 86400),
            )
            key = f"cost:feature:{feature}:{date_str}"
            spend = float(await self.redis.get(key) or 0)
            results.append({"date": date_str, "spend_usd": spend})
        return results


class CostEnforcementMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware that enforces per-user budget caps.
    Blocks requests when daily budget is exceeded.
    """

    def __init__(self, app: FastAPI, redis_url: str, default_daily_budget: float = 10.0):
        super().__init__(app)
        self.redis_client = redis.from_url(redis_url)
        self.tracker = CostTracker(self.redis_client)
        self.default_daily_budget = default_daily_budget

    async def dispatch(self, request: Request, call_next) -> Response:
        user_id = request.headers.get("X-User-ID", "anonymous")
        feature = request.headers.get("X-Feature", "unknown")
        team = request.headers.get("X-Team", "unknown")

        # Check budget before processing
        budget_status = await self.tracker.check_budget(
            user_id, self.default_daily_budget
        )

        if budget_status["status"] == "exceeded":
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "Daily budget exceeded",
                    "current_spend": budget_status["current_spend_usd"],
                    "budget": budget_status["daily_budget_usd"],
                },
            )

        # Attach metadata to request state for downstream cost recording
        request.state.cost_metadata = {
            "user_id": user_id,
            "feature": feature,
            "team": team,
            "budget_status": budget_status,
        }

        response = await call_next(request)
        return response
```

### 5.5 Fallback Chain with Circuit Breaker

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

### 5.6 Checkpoint/Resume for Long-Running Agents

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

### 5.7 Health Check and Readiness Probes for AI Services

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

## 6. Architectural System Design Scenarios

### Scenario A: Production-Grade Multi-Tenant Agentic AI Platform

**Problem statement**: A B2B SaaS company needs to deploy a multi-tenant agentic AI platform serving 200+ enterprise customers. Each tenant runs autonomous agents that call tools, access tenant-specific data, and produce customer-facing outputs. Requirements include strict tenant isolation, SOC 2 Type II compliance, HIPAA readiness for healthcare customers, 99.9% effective availability, budget enforcement per tenant, and cost-effective operation at scale. Current monthly LLM spend is $45K with all traffic going to a frontier model; the target is <$15K/month without quality degradation.

**Proposed architecture**:

```
┌────────────────────────────────────────────────────────────────────────────┐
│                        INTERNET / CLIENT APPS                             │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │
┌───────────────────────────────v────────────────────────────────────────────┐
│  API GATEWAY (Kong)                                                        │
│  - mTLS termination, JWT auth per tenant                                   │
│  - Token-aware rate limiting (Redis Lua, per-tenant-key prefix)            │
│  - DDoS protection, request logging                                        │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │
┌───────────────────────────────v────────────────────────────────────────────┐
│  LLM GATEWAY (Bifrost / Portkey)                                           │
│                                                                            │
│  ┌──────────────┐  ┌────────────────┐  ┌───────────────────────────────┐  │
│  │ Model Router  │  │ Cost Enforcer   │  │ Prompt Cache Manager          │  │
│  │               │  │                 │  │                               │  │
│  │ Simple (70%)  │  │ Per-tenant hard │  │ Prefix ordering:              │  │
│  │  → Haiku/mini │  │ caps + per-     │  │ tools → system → docs →      │  │
│  │ Medium (20%)  │  │ feature soft    │  │ history → user query          │  │
│  │  → Sonnet/4o  │  │ alerts at       │  │                               │  │
│  │ Complex (10%) │  │ 50% and 80%     │  │ Semantic cache: pgvector,     │  │
│  │  → Opus/o3    │  │                 │  │ 30% hit rate                  │  │
│  └──────────────┘  └────────────────┘  └───────────────────────────────┘  │
│                                                                            │
│  Fallback chain: Claude Sonnet → GPT-4o-mini → Gemini Flash → rules       │
│  Circuit breaker: 5 failures / 60s cooldown per provider                   │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │
┌───────────────────────────────v────────────────────────────────────────────┐
│  GUARDRAIL LAYER (NeMo + LlamaGuard + Guardrails AI)                       │
│                                                                            │
│  Input:  Schema validation → Injection detection → Prompt Guard 2 (50ms)   │
│  Output: LlamaGuard 3 8B → PII redaction (Presidio) → Pydantic validators  │
│  Parallel execution: 70ms total vs 200ms serial                            │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │
┌───────────────────────────────v────────────────────────────────────────────┐
│  AGENT ORCHESTRATION (LangGraph on Kubernetes)                             │
│                                                                            │
│  ┌─────────────────────┐  ┌────────────────────┐  ┌───────────────────┐   │
│  │ Agent Runtime        │  │ Tool Queue          │  │ Checkpoint Store  │   │
│  │                      │  │                     │  │                   │   │
│  │ Per-tenant namespace │  │ Redis Streams       │  │ PostgresSaver     │   │
│  │ Max 15 tool calls    │  │ MAXLEN=10000        │  │ Per-node persist  │   │
│  │ Repetition detection │  │ Consumer groups     │  │ WAL replication   │   │
│  │ $50 task spend cap   │  │ per tenant          │  │ Cross-region sync │   │
│  └─────────────────────┘  └────────────────────┘  └───────────────────┘   │
│                                                                            │
│  Autoscaling: KEDA ScaledObject (queue depth + vllm:num_requests_waiting)  │
│  Nodes: Karpenter (H100 spot + A10G on-demand mix)                         │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │
┌───────────────────────────────v────────────────────────────────────────────┐
│  OBSERVABILITY                                                             │
│                                                                            │
│  OpenTelemetry (traces + cost attribution per span)                        │
│  Prometheus + Grafana (infra metrics, KEDA triggers, budget utilization)    │
│  LLM Quality Monitor (hallucination rate, guardrail bypass rate)           │
│  Braintrust / Maxim (per-tenant cost dashboards, budget enforcement)       │
│  Compliance: immutable audit trail, model version per response             │
└────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix**:

| Dimension | A: Full Self-Hosted (vLLM on K8s) | B: Hybrid (API + Self-Hosted Guardrails) | C: Fully Managed (API-Only) |
|-----------|----------------------------------|----------------------------------------|----------------------------|
| **Monthly cost (at $45K baseline)** | $12-18K (GPU infra + ops) | $8-12K (API with routing + caching) | $15-20K (API with routing only) |
| **Latency (p95 e2e)** | 1.5-3s (optimized batching) | 2-5s (network hop to API) | 2-5s (API latency) |
| **Ops complexity** | High (GPU cluster, vLLM, KEDA, model updates) | Medium (K8s for guardrails + orchestration, APIs for inference) | Low (no GPU ops, managed scaling) |
| **HIPAA readiness** | Best (all data stays in VPC) | Good (guardrails in VPC, PHI not sent to APIs without BAA) | Risky (requires BAA from every provider, most do not offer one) |
| **Scalability ceiling** | GPU procurement-limited (weeks for new H100s) | Elastic (API providers absorb burst) | Highest (provider scales for you) |
| **Vendor lock-in** | None (open source stack) | Low (LLM gateway enables provider switch) | Medium (prompt/tool format coupling) |

**Decision rationale**: Approach B (Hybrid) wins for this scenario. Multi-tenant SaaS with HIPAA needs means guardrails and PII redaction must run in-VPC, but self-hosting frontier model inference is operationally expensive and procurement-constrained for a 200-customer platform. The hybrid model runs NeMo Guardrails, LlamaGuard, and the LangGraph orchestrator on Kubernetes in-VPC while routing inference through managed APIs via the LLM gateway. Model routing (70% to cheap models) plus prompt caching plus semantic caching brings the $45K baseline to ~$10K/month. HIPAA customers get VPC-isolated agent orchestration with PII never leaving the tenant's environment; non-HIPAA tenants route through standard APIs. This avoids the GPU ops burden of Approach A while maintaining the data residency controls that Approach C cannot provide.

---

### Scenario B: Cost-Optimized Internal AI Assistant with Guardrails and Observability

**Problem statement**: An enterprise (5,000 employees, $2B revenue) wants to deploy an internal AI assistant for code review, document summarization, and Q&A over internal knowledge bases. Budget constraint: <$10K/month total LLM spend. The assistant must comply with SOC 2 for internal audit, redact PII from all telemetry, and support EU employees under GDPR. The team has 2 ML engineers and no dedicated GPU ops capacity. Current prototype runs on a single model and costs $25K/month with 1,000 daily active users.

**Proposed architecture**:

```
┌────────────────────────────────────────────────────────────────────────────┐
│  USERS (5,000 employees via SSO)                                           │
└───────────────────────────────────┬────────────────────────────────────────┘
                                    │
┌───────────────────────────────────v────────────────────────────────────────┐
│  REVERSE PROXY (Caddy / Nginx)                                             │
│  - SSO integration (OIDC → employee identity)                              │
│  - TLS termination                                                         │
│  - Request/response logging (PII-redacted)                                 │
└───────────────────────────────────┬────────────────────────────────────────┘
                                    │
┌───────────────────────────────────v────────────────────────────────────────┐
│  APPLICATION (FastAPI on Cloud Run)                                        │
│                                                                            │
│  ┌────────────────────┐  ┌────────────────────┐  ┌─────────────────────┐  │
│  │ Complexity Router   │  │ Budget Middleware   │  │ Guardrails          │  │
│  │                     │  │                     │  │                     │  │
│  │ Code review → Sonnet│  │ Per-user: $0.50/day │  │ Guardrails AI       │  │
│  │ Summarize → Haiku   │  │ Per-dept: $500/mo   │  │ - PII validators    │  │
│  │ Simple Q&A → Flash  │  │ Global: $10K/mo     │  │ - Output structure  │  │
│  │ Complex → Opus      │  │ Alerts: 50%, 80%    │  │ - Injection detect  │  │
│  │ (5% of traffic)     │  │                     │  │ Parallel: ~50ms     │  │
│  └────────────────────┘  └────────────────────┘  └─────────────────────┘  │
│                                                                            │
│  Prompt cache: stable system prompt prefix (tools, persona, instructions)  │
│  Semantic cache: pgvector on Cloud SQL, 30% hit rate on repeated Q&A       │
│  Batch API: nightly document summarization at 50% discount                 │
│                                                                            │
│  Cloud Run: scale-to-zero, L4 GPU for local embedding model,               │
│  no platform timeout for streaming, auto-scales on request count           │
└───────────────────────────────────┬────────────────────────────────────────┘
                                    │
┌───────────────────────────────────v────────────────────────────────────────┐
│  OBSERVABILITY (Open-Source Stack)                                          │
│                                                                            │
│  OpenTelemetry → OpenObserve (logs, metrics, SQL-native queries)           │
│  Per-request cost tags (user_id, department, task_type)                     │
│  PII redaction in OTel Collector processor (regex + NER before storage)     │
│  Budget dashboard: Grafana with daily/weekly/monthly cost breakdown         │
│  Quality: weekly LLM-as-judge eval on sampled responses (100 samples)      │
│                                                                            │
│  SOC 2: immutable audit log per model call (who, what model, when)         │
│  GDPR: data processed in EU region, PII never stored in telemetry,         │
│         right-to-erasure supported via user_id-keyed crypto-shredding      │
└────────────────────────────────────────────────────────────────────────────┘
```

**Trade-off evaluation matrix**:

| Dimension | A: Cloud Run + Managed APIs | B: Kubernetes + Self-Hosted vLLM | C: Serverless GPU (Modal/RunPod) |
|-----------|---------------------------|--------------------------------|--------------------------------|
| **Monthly cost** | $4-7K (routing + caching + batch) | $8-15K (GPU nodes + ops overhead) | $5-9K (pay-per-inference, no batch discount) |
| **Ops burden (2 ML engineers)** | Low (Cloud Run auto-scales, no GPU ops) | High (K8s cluster, vLLM tuning, GPU scheduling) | Medium (platform manages GPU, but custom setup needed) |
| **Cold start** | <1s (Cloud Run), API providers always warm | 3-10 min (GPU pod restart) | 10-60s (GPU container spin-up) |
| **Data residency (GDPR)** | Good (choose EU region for Cloud Run; API providers may process outside EU) | Best (all data stays in your EU cluster) | Varies (check provider's data residency) |
| **Scalability** | High (Cloud Run + API providers scale elastically) | GPU procurement-limited | High (platform absorbs burst) |
| **Maintenance** | Minimal (managed platform) | Significant (cluster upgrades, model updates, GPU drivers) | Low-Medium (platform handles infra) |

**Decision rationale**: Approach A (Cloud Run + Managed APIs) wins for this scenario. With only 2 ML engineers, GPU ops is not feasible -- Approach B requires cluster management, vLLM tuning, GPU driver updates, and scaling configuration that would consume the entire team's bandwidth. Cloud Run provides scale-to-zero (no cost when the assistant is idle overnight and weekends), L4 GPU support for the local embedding model, and no platform-imposed streaming timeout. The cost optimization stack reduces spend from $25K to $4-7K/month:

```
Baseline:                                          $25,000/month
After model routing (70% to Haiku/Flash):           $10,000  (60% reduction)
After prompt caching (stable system prompt):         $7,000  (30% on remaining)
After semantic caching (30% hit on repeated Q&A):    $5,000  (29% further)
After batch API (nightly summarization at 50% off):  $4,000  (20% further)
Total:                                              ~$4,000  (84% reduction)
```

The GDPR risk (API providers processing data outside EU) is mitigated by: (a) choosing EU-based API endpoints where available (Anthropic EU, Azure OpenAI in EU regions), (b) PII redaction before sending to APIs via Guardrails AI validators, and (c) crypto-shredding for right-to-erasure compliance. For the 5% of complex queries that route to Opus, the higher per-request cost is acceptable because the volume is low -- roughly 50 requests/day at ~$0.10 each = $150/month. SOC 2 compliance is satisfied by the immutable audit log in OpenObserve, covering model version, user identity, and timestamp for every call.

---

> **Notable production incidents for interview context**:
>
> - **Anthropic Claude Quality Degradation (Aug-Sep 2025)**: Three infrastructure bugs including a context window routing error sending Sonnet 4 requests to servers configured for 1M-token context. Demonstrates why model versioning and routing traceability are essential.
> - **AWS AI-Agent-Caused Outage (Dec 2025)**: First confirmed AI-agent-caused production outage in cloud history. Amazon attributed to "misconfigured access controls." Led to mandatory peer review for AI-initiated production changes.
> - **LiteLLM Supply Chain Attack (Mar 2026)**: Backdoored PyPI packages live for ~40 minutes. 3.4M daily downloads. Demonstrates blast radius of supply chain attacks in the LLM tooling ecosystem.
> - **AWS us-east-1 Thermal Event (May 2026)**: Multiple chiller failures in Northern Virginia triggered thermal-safety shutdown. Legacy cooling designs not built for AI/HPC rack densities. Demonstrates physical infrastructure risk for GPU-dense deployments.
> - **DORA 2026**: AI code generation tools increase code volume but decrease delivery throughput by 1.5% and worsen stability by 7.5%. More code is not better outcomes.
> - **GPU utilization**: Cast.ai 2026 reports average 5% GPU utilization across production K8s clusters. An idle H100 on AWS costs ~$12.30/hr. Fewer than 2% of GPU workloads ran on Spot. The optimization opportunity is enormous.
