# Module 03: LLMOps & CI/CD for AI

## What Is This?

Traditional software ships code. Traditional ML ships model binaries. LLMOps ships
**prompts, retrieval configs, model provider settings, and evaluation thresholds** --
artifacts where a single-word change in a system prompt can shift output quality more
than a full model retrain ever would.

Think of it this way: in classical DevOps, you build a binary, test it, deploy it.
In LLMOps, you are deploying *behavior* -- and behavior has no compiler, no type
checker, and no stack trace when it goes wrong. A prompt change that makes responses
20% more sycophantic produces zero errors and zero alerts. Your HTTP dashboards glow
green while Twitter becomes your production alerting system.

LLMOps is the discipline of making that chaos manageable: version prompts like code,
evaluate outputs like tests, deploy models like services, and monitor quality like SLOs.

## Why It Matters

- 85% of ML models never make it to production. LLMOps is the difference between a
  demo and a system.
- 30%+ of GenAI projects are abandoned after POC (Gartner 2025). Most die in the gap
  between "it works in my notebook" and "it works at 2 AM on a Saturday."
- 42% of companies abandoned AI initiatives in 2024-2025 due to governance gaps.
- The LLM observability market alone is $2.69B in 2026, projected $9.26B by 2030.
- Director/VP-level roles require you to articulate why a prompt registry matters more
  than a fancier model, and why a 45-minute eval suite is worse than no eval at all.

---

## Part 1: System Topology & Data Flow

### End-to-End LLMOps Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            CONTROL PLANE                                       │
│                                                                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ Prompt        │  │ Model        │  │ Eval Suite   │  │ Feature Flags /   │  │
│  │ Registry      │  │ Registry     │  │ (Golden DS + │  │ Experiment Config │  │
│  │ (versioned,   │  │ (MLflow /    │  │  LLM-Judge)  │  │ (GrowthBook /     │  │
│  │  tagged by    │  │  W&B /       │  │              │  │  LaunchDarkly)    │  │
│  │  environment) │  │  SageMaker)  │  │              │  │                   │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └────────┬──────────┘  │
│         │                 │                  │                   │              │
│         └─────────────────┼──────────────────┼───────────────────┘              │
│                           │                  │                                  │
│                    ┌──────┴──────┐     ┌──────┴──────┐                          │
│                    │ CI Pipeline │────>│ Quality     │                          │
│                    │ (PR Gate)   │     │ Gate        │                          │
│                    └──────┬──────┘     │ (pass/fail) │                          │
│                           │           └─────────────┘                          │
└───────────────────────────┼────────────────────────────────────────────────────┘
                            │
┌───────────────────────────┼────────────────────────────────────────────────────┐
│                      DATA PLANE                                                │
│                           │                                                    │
│              ┌────────────┴────────────┐                                       │
│              │    LLM Gateway /        │                                       │
│              │    Router (Portkey /     │                                       │
│              │    LiteLLM)             │                                       │
│              └──┬──────────┬──────┬───┘                                       │
│                 │          │      │                                            │
│          ┌──────┴──┐ ┌────┴────┐ ┌┴─────────┐                                │
│          │ Cheap   │ │ Mid-Tier│ │ Frontier  │                                │
│          │ Model   │ │ Model   │ │ Model     │                                │
│          │ (80%)   │ │ (15%)   │ │ (5%)      │                                │
│          └─────────┘ └─────────┘ └───────────┘                                │
│                                                                                │
│  ┌───────────┐  ┌──────────┐  ┌──────────────┐  ┌────────────────────────┐   │
│  │ Semantic   │  │ Vector   │  │ Knowledge    │  │ Guardrails             │   │
│  │ Cache      │  │ Store    │  │ Base / Docs  │  │ (input + output)       │   │
│  │ (Redis)    │  │          │  │              │  │                        │   │
│  └───────────┘  └──────────┘  └──────────────┘  └────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────────────┘
                            │
┌───────────────────────────┼────────────────────────────────────────────────────┐
│                      TELEMETRY PLANE                                           │
│                           │                                                    │
│  ┌──────────────┐  ┌──────┴───────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Trace Store  │  │ Metrics      │  │ Eval Sampler │  │ Cost Tracker     │  │
│  │ (Langfuse /  │  │ (OTel GenAI  │  │ (5-10% of    │  │ (per-request,    │  │
│  │  Braintrust) │  │  Conventions)│  │  live traffic)│  │  per-user,       │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │  per-feature)    │  │
│                                                         └──────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │ Alerts: SLO burn-rate, quality regression, cost anomaly, latency spike  │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────┘
```

### Request Flow Narrative

1. A prompt change is committed to the **Prompt Registry** (versioned with semantic
   version, timestamp, and commit hash -- never "latest" in production).
2. The **CI Pipeline** triggers: runs the **Eval Suite** against a golden dataset.
   If quality thresholds are not met, the merge is blocked. Core gate suite stays
   under 10 minutes; the full suite runs overnight.
3. On merge, the **LLM Gateway** picks up the new prompt version via blue-green or
   canary deployment. Canary starts at 1-5% of traffic with a 24-72 hour soak.
4. At runtime, the Gateway routes requests through the **Semantic Cache** (cosine
   similarity > 0.95 bypasses the LLM entirely) and then to the appropriate model
   tier based on complexity classification.
5. Every request emits a trace (via OpenTelemetry GenAI Semantic Conventions) to
   the **Telemetry Plane**: token counts, latency, cost estimate, and span metadata.
6. The **Eval Sampler** scores 5-10% of live traffic with an automated evaluator.
   Alerts fire on SLO burn-rate violations, not threshold breaches -- burn-rate
   catches slow degradation that absolute thresholds miss.

---

## Part 2: Core Mechanics & Algorithms

### The Four Versioned Artifacts

In traditional CI/CD, you version code. In LLMOps, you version four distinct artifacts,
each of which can independently shift system behavior:

| Artifact | What Changes | Impact |
|---|---|---|
| **Prompt** | System instructions, few-shot examples | Output tone, accuracy, safety |
| **Retrieval Config** | Chunk size, overlap, reranker, top-K | Context quality, cost |
| **Model Provider** | Model version, provider, quantization | Latency, cost, capability |
| **Eval Thresholds** | Pass/fail criteria, golden dataset | What gets deployed |

### Model Registry Selection

| Need | Choose | Why |
|---|---|---|
| Cost-sensitive, regulated, air-gapped | **MLflow** (Apache 2.0) | Only viable on-prem option. 55%+ market share |
| Collaboration-first, budget available | **W&B** ($50/user/mo Teams) | Best visualizations, 70+ integrations |
| AWS-native enterprise | **SageMaker Registry** | Native IAM/VPC, managed MLflow |
| Unified ML + LLM, predictable pricing | **Comet ML** ($19/user/mo Pro) | Code diff tracking, Opik LLM eval |

### Prompt Versioning: The Git-Native Approach

Treat prompts as versioned files in the repo. On every PR that touches prompt files:
1. CI action (Braintrust or Promptfoo) runs eval against golden dataset
2. Results posted as PR comment with quality delta
3. Merge blocked if quality degrades below threshold
4. Webhook notifies downstream systems of new prompt version

Surviving platforms (2026): **PromptLayer** (visual registry, A/B routing),
**LangSmith Hub** (deep LangChain integration), **Langfuse** (MIT-licensed, self-host),
**Arize Phoenix** (ELv2, tracing + prompts unified), **Braintrust** (GitHub Action,
auto PR feedback).

### Model Serving Engines (2026)

| Engine | Status | Throughput (H100) | Best For |
|---|---|---|---|
| **vLLM** | De facto standard, 70% share | ~2,380 tok/s (Llama 70B FP8) | Default for new projects |
| **SGLang** | Strong contender | Competitive, wins on structured output | Agent workloads, tool calls |
| **TGI** | Maintenance mode (Dec 2025) | Lower GPU util (68-74%) | Legacy only. Migrate away |
| **Triton + TRT-LLM** | NVIDIA enterprise | 20-40% higher raw throughput | Fixed model, sustained concurrency |

All four expose OpenAI-compatible APIs. Switching servers = changing a base URL.

### Three-Tier Evaluation Architecture

**Tier 1 -- Offline (Pre-Deployment)**: Golden dataset evaluation before every release.
Component-level isolation (retrieval separate from generation). Question answered:
"Did we break something that worked before?"

**Tier 2 -- CI/CD Gates**: Automated eval on every PR touching prompts/models/retrieval.
Quality thresholds block merge. Keep core gate suite **under 10 minutes** (45-minute
suites get skipped by engineers). Full suite runs overnight.

**Tier 3 -- Production Monitoring**: Sample 5-10% of live traffic, score with automated
evaluator. Distribution shift detection. Alerts on quality degradation tied to SLO burn
rates. Question answered: "Is the system working correctly right now?"

### LLM-as-Judge Calibration

Before using an LLM judge as a CI gate, measure agreement against 20-30 human-labeled
examples specific to your task. A judge achieving < 80% agreement with human evaluators
is not reliable enough for automated blocking decisions. The evaluation dataset matters
more than the metric -- most teams spend 80% of effort on metrics and 20% on the
dataset. This ratio is backwards.

### Deployment Strategies

**Blue-Green**: Maintain parallel GPU environments. Green pre-loads and warms before the
switch. Session-aware load balancing is mandatory -- mid-conversation users cannot be
rerouted to a different model version.

**Canary**: Release to 1-5% of users. Monitor for 24-72 hours (not 2 hours). A canary
that runs for 2 hours on a Tuesday morning has not seen the weekend, the geography
spread, or the time-of-day distribution. LLM-specific canary metrics: output length
distribution, hallucination rate, coherence scores, cost per request. Standard HTTP
metrics are insufficient because HTTP 200 does not mean correct.

**Rollback**: A routing change, not a redeployment. Load balancer shifts 100% back to
stable version. Test rollbacks monthly with chaos engineering drills. ~60% of production
incidents end in rollback, ~40% in forward-fix.

---

## Part 3: Token Economics & NFR Analysis

### Cost Per 1K Runs

| Component | Unit Cost | Cost per 1K Runs | Notes |
|---|---|---|---|
| GPT-4o (avg 500 in / 800 out per call) | $2.50/1M in, $10/1M out | **$9.25** | Frontier reasoning |
| GPT-4o mini (avg 500 in / 800 out) | $0.15/1M in, $0.60/1M out | **$0.56** | 80% of routing tier |
| Claude Sonnet 4 (avg 500 in / 800 out) | $3/1M in, $15/1M out | **$13.50** | Complex multi-step |
| Self-hosted vLLM (H100 @ $2.50/hr) | ~$2.50/hr GPU | **$0.69** | At 1 req/s sustained |
| Semantic cache hit | ~$0.0001 (Redis lookup) | **$0.10** | Bypasses LLM entirely |
| Embedding (text-embedding-3-small) | $0.02/1M tokens | **$0.01** | 500 tokens avg |

**Multi-model routing impact**: Route 80% to GPT-4o mini, 20% to GPT-4o.
- Without routing: 1K runs at GPT-4o = $9.25
- With routing: 800 x $0.00056 + 200 x $0.00925 = $0.45 + $1.85 = **$2.30** (75% savings)
- Add 25% semantic cache hits: effective cost drops to **$1.73**

Organizations with tuned routing report 40-85% cost reductions without visible quality
drops.

### Latency SLA Targets

| Use Case | p50 | p95 | p99 |
|---|---|---|---|
| Customer-facing real-time chat | < 500ms | < 1.5s | < 3s |
| Internal tools | < 1s | < 3s | < 5s |
| Async batch (document processing) | < 5s | < 15s | < 30s |
| Real-time agentic loops (per step) | < 300ms | < 800ms | < 1.5s |
| Inline code completion | < 100ms | < 200ms | < 500ms |

### Throughput & Capacity Planning

- **vLLM**: 85-92% GPU utilization, ~100-150 concurrent requests per GPU before
  saturation. KV cache wastes < 4% of memory (previous systems wasted 60-80%).
- **Dynamic batching**: Hold requests for 5-15ms batch window. GPU utilization climbs
  from ~40% to 75-85%. Per-request p95 latency drops 25-35% under concurrency.
- **KV cache-aware routing** (llm-d): 87% cache hit rate, 88% faster TTFT for warm
  cache hits. Hash prompt prefix, store KV cache, reuse for requests with same prefix.
- **Queue-based autoscaling**: Scale on queue depth, not CPU/GPU alone. Separate
  high-priority queues (chat) from batch jobs. Model version routing: quantized for
  latency-critical, FP16 for batch.

### Availability & RPO/RTO

| Component | Availability Target | RPO | RTO |
|---|---|---|---|
| LLM Gateway | 99.95% | N/A (stateless) | < 30s (failover to standby) |
| Prompt Registry | 99.99% | 0 (Git-backed) | < 5 min (git clone) |
| Vector Store | 99.9% | < 1 min | < 15 min (rebuild from source) |
| Eval Dataset | 99.99% | 0 (version-controlled) | < 5 min |
| Telemetry / Traces | 99.5% | < 5 min | < 30 min |

Vector indexes are derived data -- they can be rebuilt from source documents. This
makes RPO less critical for vector stores than for primary data stores.

---

## Part 4: Distributed Resilience & Security

### Failure Taxonomy

| Failure Mode | Detection | Impact | Mitigation |
|---|---|---|---|
| **Silent quality regression** | Production eval sampling, user feedback | Users see degraded output, no alerts fire | SLO burn-rate alerts, A/B before rollout |
| **Provider-side silent model change** | Continuous monitoring, output distribution drift | Behavior shifts without your knowledge | Pin model versions, continuous eval |
| **Underpowered eval suite** | Statistical power analysis | Ship regressions you cannot detect | Minimum N per effect size (see below) |
| **Agentic cascade failure** | Trace analysis, step-level eval | Wrong first tool cascades into bad state | Per-step validation, circuit breakers |
| **Cost explosion** | Per-request cost tracking, daily anomaly alerts | Budget overrun from uncontrolled reasoning tokens | Token budgets, kill switch for agent loops |
| **Schema validation failure** | Structured output parsing errors | Tool calls break silently | Pydantic validation, retry with feedback |

### Sample Size Requirements for Eval

| Effect Size | Required Samples | Practical Implication |
|---|---|---|
| Large (10%+ change) | 50-100 | Quick sanity check |
| Moderate (5-10%) | 200-500 | Standard CI gate |
| Small (1-3%) | 500-2,000+ | Production A/B test |

Formula: n >= 16 * p * (1-p) / delta^2 at 80% power, 5% significance, plus 1.5x
buffer for real-traffic variance. A test with 30 examples that reports +0.03 delta
has resolved nothing.

### Circuit Breakers & Durable Execution

- **Multi-provider fallback**: If OpenAI is down, route to Anthropic or self-hosted.
  LLM gateways (Portkey, LiteLLM) provide this natively.
- **Circuit breaker pattern**: Trip on sustained error rate, route all traffic to
  fallback provider. Reset after health-check window.
- **Artifact storage**: LLMs range from GB to TB. Traditional CI/CD repos cannot
  handle this. Use cloud storage (S3/GCS) coupled with DVC or MLflow.
- **Rollback = routing change**: Load balancer shifts 100% to stable version.
  Canary stays deployed for debugging. Test rollbacks monthly.

### Zero-Trust for AI (2026)

Microsoft published its Zero Trust for AI reference architecture in March 2026. At
RSAC 2026, four major vendors independently concluded: Zero Trust must extend to every
AI workload, every agent identity, and every model interaction.

IBM 2025 Cost of a Data Breach: among organizations that experienced AI-related
breaches, **97% lacked proper AI access controls**.

### RBAC for LLM Infrastructure

| Role | Permissions | Example |
|---|---|---|
| Inference Consumer | Execute queries against approved models | Application service accounts |
| Prompt Engineer | Modify system prompts, retrieval configs | ML engineers, product managers |
| Model Administrator | Deploy, update, remove model weights | Platform team |
| Auditor | Read-only: inference logs, security events | Compliance, security |

For agentic systems: treat agents like workloads. Scope RBAC tightly, validate
entitlements continuously, add runtime enforcement for process behavior, file access,
and network actions.

### PII Filtering & Audit Trails

Log the data category ("customer_pii detected and redacted"), the guardrail event, the
model, the team, and the trace ID -- **never the raw prompt content or the PII itself**.
A redaction layer that logs the values it redacted has just created a second copy of the
data it was protecting.

### Regulatory Compliance

- **EU AI Act**: High-risk system obligations landing Aug 2026 (risk management, data
  governance, record-keeping, transparency, human oversight)
- **GDPR/CCPA**: PII detection and removal in both LLM inputs and outputs
- **Key frameworks**: NIST AI RMF, ISO/IEC 42001:2023, OWASP Top 10 for Agentic Apps
  (Dec 2025), CSA Agentic Trust Framework (Feb 2026)
- **Policy-as-code**: Embed executable governance rules (fairness, data lineage,
  versioning, compliance) into CI/CD pipelines

---

## Part 5: Production Enterprise Code

### LLMOps CI/CD Pipeline with Eval Gating

```python
"""
LLMOps CI/CD pipeline: prompt versioning, eval gating, canary deployment.
Requires: openai, pydantic, redis, numpy
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np
import redis
import openai


# ── Prompt Registry ──────────────────────────────────────────────────────────

class PromptVersion:
    """Immutable prompt version with semantic versioning and eval lineage."""

    def __init__(self, name: str, version: str, template: str,
                 model: str = "gpt-4o-mini", temperature: float = 0.3):
        self.name = name
        self.version = version
        self.template = template
        self.model = model
        self.temperature = temperature
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.commit_hash = self._compute_hash()
        self.eval_scores: dict[str, float] = {}

    def _compute_hash(self) -> str:
        content = f"{self.name}:{self.version}:{self.template}:{self.model}"
        return hashlib.sha256(content.encode()).hexdigest()[:12]

    def render(self, **kwargs: Any) -> str:
        return self.template.format(**kwargs)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "version": self.version,
            "template": self.template, "model": self.model,
            "temperature": self.temperature, "commit_hash": self.commit_hash,
            "created_at": self.created_at, "eval_scores": self.eval_scores,
        }


class PromptRegistry:
    """In-memory registry. Production: back with Redis or Postgres."""

    def __init__(self):
        self._store: dict[str, dict[str, PromptVersion]] = {}

    def register(self, prompt: PromptVersion) -> str:
        if prompt.name not in self._store:
            self._store[prompt.name] = {}
        self._store[prompt.name][prompt.version] = prompt
        return prompt.commit_hash

    def get(self, name: str, version: str) -> PromptVersion:
        return self._store[name][version]

    def get_production(self, name: str) -> PromptVersion:
        """Return highest version tagged with passing eval scores."""
        versions = self._store.get(name, {})
        candidates = [
            v for v in versions.values()
            if v.eval_scores.get("overall", 0) >= 0.80
        ]
        if not candidates:
            raise ValueError(f"No production-ready version for '{name}'")
        candidates.sort(key=lambda v: v.version, reverse=True)
        return candidates[0]


# ── Evaluation Engine ────────────────────────────────────────────────────────

@dataclass
class EvalCase:
    input_text: str
    expected_output: str
    category: str = "general"
    weight: float = 1.0


@dataclass
class EvalResult:
    prompt_version: str
    commit_hash: str
    scores: dict[str, float]
    passed: bool
    num_cases: int
    duration_seconds: float
    failures: list[dict] = field(default_factory=list)


class EvalEngine:
    """Three-tier eval: offline golden dataset, CI gate, production sampling."""

    PASS_THRESHOLDS = {
        "accuracy": 0.80,
        "faithfulness": 0.85,
        "relevance": 0.80,
        "overall": 0.80,
    }
    MAX_REGRESSION = 0.05  # max acceptable drop from baseline

    def __init__(self, client: openai.OpenAI, judge_model: str = "gpt-4o"):
        self.client = client
        self.judge_model = judge_model

    def score_single(self, response: str, expected: str, input_text: str) -> dict:
        """Use LLM-as-judge to score a single response. Returns 0-1 scores."""
        judge_prompt = (
            "You are an evaluation judge. Score the AI response on three dimensions.\n"
            "Return JSON with keys: accuracy, faithfulness, relevance (each 0.0-1.0).\n\n"
            f"User question: {input_text}\n"
            f"Expected answer: {expected}\n"
            f"Actual response: {response}\n\n"
            "Return only valid JSON, no explanation."
        )
        result = self.client.chat.completions.create(
            model=self.judge_model,
            messages=[{"role": "user", "content": judge_prompt}],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        return json.loads(result.choices[0].message.content)

    def run_eval_suite(self, prompt: PromptVersion,
                       golden_dataset: list[EvalCase],
                       baseline_scores: dict[str, float] | None = None
                       ) -> EvalResult:
        """Run full eval suite. Returns EvalResult with pass/fail decision."""
        start = time.time()
        all_scores: list[dict[str, float]] = []
        failures: list[dict] = []

        for case in golden_dataset:
            rendered = prompt.render(question=case.input_text)
            response = self.client.chat.completions.create(
                model=prompt.model,
                messages=[{"role": "user", "content": rendered}],
                temperature=prompt.temperature,
            )
            answer = response.choices[0].message.content
            scores = self.score_single(answer, case.expected_output, case.input_text)
            all_scores.append(scores)

            for metric, threshold in self.PASS_THRESHOLDS.items():
                if metric in scores and scores[metric] < threshold:
                    failures.append({
                        "input": case.input_text[:100],
                        "metric": metric,
                        "score": scores[metric],
                        "threshold": threshold,
                    })

        # Aggregate scores (weighted mean)
        aggregated = {}
        for metric in ["accuracy", "faithfulness", "relevance"]:
            values = [s.get(metric, 0) for s in all_scores]
            weights = [c.weight for c in golden_dataset]
            aggregated[metric] = float(np.average(values, weights=weights))
        aggregated["overall"] = float(np.mean(list(aggregated.values())))

        # Check absolute thresholds
        passed = all(
            aggregated.get(m, 0) >= t
            for m, t in self.PASS_THRESHOLDS.items()
        )
        # Check relative regression against baseline
        if passed and baseline_scores:
            for metric, score in aggregated.items():
                baseline = baseline_scores.get(metric, 0)
                if baseline - score > self.MAX_REGRESSION:
                    passed = False
                    failures.append({
                        "type": "regression",
                        "metric": metric,
                        "baseline": baseline,
                        "current": score,
                        "max_allowed_drop": self.MAX_REGRESSION,
                    })

        prompt.eval_scores = aggregated
        return EvalResult(
            prompt_version=prompt.version,
            commit_hash=prompt.commit_hash,
            scores=aggregated,
            passed=passed,
            num_cases=len(golden_dataset),
            duration_seconds=round(time.time() - start, 2),
            failures=failures,
        )


# ── Multi-Model Router ───────────────────────────────────────────────────────

class ModelTier(Enum):
    LIGHTWEIGHT = "lightweight"
    MID = "mid"
    FRONTIER = "frontier"


@dataclass
class RouteDecision:
    tier: ModelTier
    model: str
    reason: str
    estimated_cost: float  # dollars per request


class CostAwareRouter:
    """Route requests to cheapest model that can handle them."""

    TIER_CONFIG = {
        ModelTier.LIGHTWEIGHT: {
            "model": "gpt-4o-mini",
            "cost_per_1k_input": 0.00015,
            "cost_per_1k_output": 0.0006,
            "max_complexity": 0.4,
        },
        ModelTier.MID: {
            "model": "gpt-4o",
            "cost_per_1k_input": 0.0025,
            "cost_per_1k_output": 0.01,
            "max_complexity": 0.8,
        },
        ModelTier.FRONTIER: {
            "model": "claude-sonnet-4-20250514",
            "cost_per_1k_input": 0.003,
            "cost_per_1k_output": 0.015,
            "max_complexity": 1.0,
        },
    }

    def classify_complexity(self, query: str) -> float:
        """Simple heuristic complexity scorer. Production: use a classifier."""
        score = 0.2
        if len(query) > 500:
            score += 0.2
        multi_step_markers = ["step by step", "compare", "analyze", "explain why",
                              "trade-off", "pros and cons", "design"]
        for marker in multi_step_markers:
            if marker in query.lower():
                score += 0.15
        return min(score, 1.0)

    def route(self, query: str, estimated_tokens: int = 500) -> RouteDecision:
        complexity = self.classify_complexity(query)
        for tier in [ModelTier.LIGHTWEIGHT, ModelTier.MID, ModelTier.FRONTIER]:
            config = self.TIER_CONFIG[tier]
            if complexity <= config["max_complexity"]:
                cost = (
                    estimated_tokens * config["cost_per_1k_input"] / 1000
                    + estimated_tokens * 1.6 * config["cost_per_1k_output"] / 1000
                )
                return RouteDecision(
                    tier=tier, model=config["model"],
                    reason=f"complexity={complexity:.2f}, threshold={config['max_complexity']}",
                    estimated_cost=round(cost, 6),
                )
        frontier = self.TIER_CONFIG[ModelTier.FRONTIER]
        return RouteDecision(
            tier=ModelTier.FRONTIER, model=frontier["model"],
            reason="fallback to frontier", estimated_cost=0.01,
        )


# ── Semantic Cache ────────────────────────────────────────────────────────────

class SemanticCache:
    """Embedding-based cache. Cosine > threshold returns cached response."""

    def __init__(self, client: openai.OpenAI, redis_client: redis.Redis,
                 threshold: float = 0.95, embed_model: str = "text-embedding-3-small"):
        self.client = client
        self.redis = redis_client
        self.threshold = threshold
        self.embed_model = embed_model
        self._cache_key = "llmops:semantic_cache"

    def _embed(self, text: str) -> list[float]:
        resp = self.client.embeddings.create(model=self.embed_model, input=text)
        return resp.data[0].embedding

    def _cosine_sim(self, a: list[float], b: list[float]) -> float:
        a_arr, b_arr = np.array(a), np.array(b)
        return float(np.dot(a_arr, b_arr) / (np.linalg.norm(a_arr) * np.linalg.norm(b_arr)))

    def lookup(self, query: str) -> str | None:
        query_vec = self._embed(query)
        entries = self.redis.hgetall(self._cache_key)
        best_score, best_response = 0.0, None
        for key, val in entries.items():
            entry = json.loads(val)
            score = self._cosine_sim(query_vec, entry["embedding"])
            if score > best_score:
                best_score = score
                best_response = entry["response"]
        if best_score >= self.threshold:
            return best_response
        return None

    def store(self, query: str, response: str) -> None:
        vec = self._embed(query)
        entry = {"embedding": vec, "response": response,
                 "created_at": datetime.now(timezone.utc).isoformat()}
        cache_key = hashlib.md5(query.encode()).hexdigest()
        self.redis.hset(self._cache_key, cache_key, json.dumps(entry))


# ── Canary Deployment Controller ─────────────────────────────────────────────

@dataclass
class CanaryMetrics:
    error_rate: float
    p95_latency_ms: float
    avg_output_tokens: float
    eval_score: float
    cost_per_request: float


class CanaryController:
    """Manages canary rollout with automated rollback triggers."""

    ROLLBACK_THRESHOLDS = {
        "error_rate_increase": 0.02,     # 2% absolute increase
        "p95_latency_increase_ms": 500,  # 500ms increase
        "eval_score_decrease": 0.05,     # 5% decrease
        "cost_increase_pct": 0.30,       # 30% cost increase
    }

    def __init__(self, canary_pct: float = 0.05, soak_hours: int = 48):
        self.canary_pct = canary_pct
        self.soak_hours = soak_hours
        self.start_time: datetime | None = None
        self.baseline: CanaryMetrics | None = None

    def start_canary(self, baseline: CanaryMetrics) -> dict:
        self.baseline = baseline
        self.start_time = datetime.now(timezone.utc)
        return {
            "status": "canary_started",
            "canary_pct": self.canary_pct,
            "soak_hours": self.soak_hours,
            "rollback_thresholds": self.ROLLBACK_THRESHOLDS,
        }

    def evaluate(self, canary: CanaryMetrics) -> dict:
        """Check canary against baseline. Returns promote/rollback/continue."""
        if not self.baseline or not self.start_time:
            raise ValueError("Canary not started")

        violations = []
        b = self.baseline

        if canary.error_rate - b.error_rate > self.ROLLBACK_THRESHOLDS["error_rate_increase"]:
            violations.append(f"error_rate: {b.error_rate:.3f} -> {canary.error_rate:.3f}")

        if canary.p95_latency_ms - b.p95_latency_ms > self.ROLLBACK_THRESHOLDS["p95_latency_increase_ms"]:
            violations.append(f"p95_latency: {b.p95_latency_ms:.0f}ms -> {canary.p95_latency_ms:.0f}ms")

        if b.eval_score - canary.eval_score > self.ROLLBACK_THRESHOLDS["eval_score_decrease"]:
            violations.append(f"eval_score: {b.eval_score:.3f} -> {canary.eval_score:.3f}")

        cost_increase = (canary.cost_per_request - b.cost_per_request) / b.cost_per_request
        if cost_increase > self.ROLLBACK_THRESHOLDS["cost_increase_pct"]:
            violations.append(f"cost: ${b.cost_per_request:.4f} -> ${canary.cost_per_request:.4f}")

        if violations:
            return {"action": "rollback", "violations": violations}

        elapsed = (datetime.now(timezone.utc) - self.start_time).total_seconds() / 3600
        if elapsed >= self.soak_hours:
            return {"action": "promote", "soak_hours_completed": round(elapsed, 1)}

        return {"action": "continue", "hours_remaining": round(self.soak_hours - elapsed, 1)}


# ── Trace & Cost Attribution ─────────────────────────────────────────────────

@dataclass
class TraceSpan:
    """OpenTelemetry GenAI convention-compatible span."""
    trace_id: str
    span_id: str
    gen_ai_system: str        # e.g., "openai", "anthropic"
    gen_ai_request_model: str
    gen_ai_response_model: str
    gen_ai_usage_input_tokens: int
    gen_ai_usage_output_tokens: int
    latency_ms: float
    estimated_cost_usd: float
    user_id: str = ""
    feature: str = ""
    environment: str = "production"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_otel_attributes(self) -> dict:
        return {
            "gen_ai.system": self.gen_ai_system,
            "gen_ai.request.model": self.gen_ai_request_model,
            "gen_ai.response.model": self.gen_ai_response_model,
            "gen_ai.usage.input_tokens": self.gen_ai_usage_input_tokens,
            "gen_ai.usage.output_tokens": self.gen_ai_usage_output_tokens,
        }


# ── Usage Example ─────────────────────────────────────────────────────────────

def demo_pipeline():
    """Demonstrate the full CI/CD pipeline flow."""

    # 1. Register prompt versions
    registry = PromptRegistry()
    v1 = PromptVersion(
        name="support_bot", version="1.0.0",
        template="Answer the customer question using only the provided context.\n"
                 "Context: {{context}}\nQuestion: {question}\nAnswer:",
    )
    v2 = PromptVersion(
        name="support_bot", version="1.1.0",
        template="You are a helpful support agent. Answer using ONLY the provided context.\n"
                 "If the context does not contain the answer, say 'I don't have that information.'\n"
                 "Context: {{context}}\nQuestion: {question}\nAnswer:",
    )
    registry.register(v1)
    registry.register(v2)

    # 2. Define golden dataset
    golden_dataset = [
        EvalCase("How do I reset my password?",
                 "Go to Settings > Security > Reset Password.", "account"),
        EvalCase("What is the refund policy?",
                 "Full refund within 30 days of purchase.", "billing"),
        EvalCase("How do I export my data?",
                 "Navigate to Settings > Data > Export.", "account"),
    ]

    # 3. Route a sample query
    router = CostAwareRouter()
    simple_q = "What is the refund policy?"
    complex_q = "Compare the pros and cons of upgrading, step by step"
    print(f"Simple query -> {router.route(simple_q)}")
    print(f"Complex query -> {router.route(complex_q)}")

    # 4. Canary deployment
    canary = CanaryController(canary_pct=0.05, soak_hours=48)
    baseline = CanaryMetrics(
        error_rate=0.01, p95_latency_ms=800,
        avg_output_tokens=150, eval_score=0.88, cost_per_request=0.003,
    )
    print(f"\nCanary started: {canary.start_canary(baseline)}")

    # Simulate healthy canary
    canary_metrics = CanaryMetrics(
        error_rate=0.012, p95_latency_ms=820,
        avg_output_tokens=155, eval_score=0.87, cost_per_request=0.0032,
    )
    print(f"Canary check: {canary.evaluate(canary_metrics)}")

    # Simulate degraded canary triggering rollback
    bad_metrics = CanaryMetrics(
        error_rate=0.05, p95_latency_ms=1500,
        avg_output_tokens=300, eval_score=0.72, cost_per_request=0.008,
    )
    print(f"Bad canary check: {canary.evaluate(bad_metrics)}")


if __name__ == "__main__":
    demo_pipeline()
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: Building an Eval-Gated CI/CD Pipeline for a Multi-Team LLM Platform

**Problem Statement**: A fintech company has 8 product teams, each shipping LLM-powered
features (customer support chatbot, fraud explanation generator, compliance summarizer,
onboarding assistant). Each team changes prompts 5-10 times per week. There is no
quality gate -- prompt changes go live via feature flags, and regressions are caught
by customer complaints. The VP of Engineering wants a unified CI/CD pipeline where
no prompt change reaches production without passing automated evaluation.

**Architecture**:

```
┌─────────────────────────────────────────────────────────────────┐
│  Developer pushes PR touching prompt file                       │
│                                                                 │
│  ┌─────────┐    ┌──────────────┐    ┌────────────────────────┐ │
│  │ Git PR   │───>│ CI Runner    │───>│ Eval Orchestrator      │ │
│  │ (prompt  │    │ (GitHub      │    │ (Braintrust / DeepEval │ │
│  │  change) │    │  Action)     │    │  + LLM-as-Judge)       │ │
│  └─────────┘    └──────────────┘    └────────┬───────────────┘ │
│                                              │                  │
│                      ┌───────────────────────┼──────────┐      │
│                      │                       │          │      │
│                ┌─────┴─────┐  ┌──────────┐  ┌┴────────┐│      │
│                │ Team's    │  │ Shared   │  │ Power   ││      │
│                │ Golden    │  │ Safety   │  │ Analysis││      │
│                │ Dataset   │  │ Dataset  │  │ (N >=   ││      │
│                │ (50-200   │  │ (toxicity│  │  target) ││      │
│                │  cases)   │  │  PII,    │  │         ││      │
│                │           │  │  jailbrk)│  │         ││      │
│                └───────────┘  └──────────┘  └─────────┘│      │
│                                                         │      │
│                ┌────────────────────────────────────────┘      │
│                │                                               │
│         ┌──────┴──────┐                                       │
│         │ Quality Gate │                                       │
│         │ (absolute +  │                                       │
│         │  regression) │                                       │
│         └──────┬──────┘                                       │
│                │                                               │
│         PASS ──┤── FAIL                                       │
│         │      │                                               │
│     Merge  Block + PR comment with                            │
│         │      failure details                                 │
│         v                                                      │
│     Canary (5%, 48h soak)                                     │
│         │                                                      │
│     Promote / Rollback                                        │
└─────────────────────────────────────────────────────────────────┘
```

**Trade-off Matrix**:

| Decision | Option A | Option B | Chosen | Rationale |
|---|---|---|---|---|
| Eval tool | Braintrust (hosted) | DeepEval (self-hosted) | Braintrust | GitHub Action integration, PR comments, regression tracking out of the box |
| Judge model | GPT-4o (accurate) | GPT-4o-mini (cheap) | GPT-4o for gate, mini for nightly | Gate suite is small (< 200 cases), accuracy matters more than cost here |
| Gate suite size | 50 cases (fast) | 500 cases (thorough) | 50-100 per team + 50 shared safety | Keep under 10 minutes. Full suite runs overnight |
| Canary duration | 24 hours (fast) | 72 hours (safe) | 48 hours | Covers weekday traffic patterns; weekend covered by overnight suite |
| Rollback trigger | Manual (human decides) | Automated (threshold) | Automated with manual override | Speed matters more than precision; 60% of incidents end in rollback anyway |

**Decision Rationale**: The core insight is that the gate suite must be fast enough
that engineers do not skip it. A 45-minute suite is equivalent to no suite. Each team
owns their golden dataset (domain expertise) while sharing a cross-cutting safety
dataset (toxicity, PII leakage, jailbreak resistance). The shared safety dataset
catches the failures that domain teams do not think to test.

---

### Scenario 2: Multi-Model Cost Optimization for a High-Volume Customer Support Platform

**Problem Statement**: A SaaS company processes 500K customer support conversations per
day through a single GPT-4o pipeline. Monthly LLM spend is $180K and growing 15% MoM.
The CFO has mandated a 60% cost reduction without degrading customer satisfaction scores
(currently 4.2/5.0). The VP of AI must redesign the architecture.

**Architecture**:

```
┌─────────────────────────────────────────────────────────────────┐
│  Incoming Customer Message                                      │
│         │                                                       │
│         v                                                       │
│  ┌──────────────┐                                              │
│  │ Semantic Cache│──── HIT (25%) ────> Return cached response  │
│  │ (Redis, cos   │                                              │
│  │  > 0.95)      │                                              │
│  └──────┬───────┘                                              │
│         │ MISS                                                  │
│         v                                                       │
│  ┌──────────────┐                                              │
│  │ Intent +     │                                              │
│  │ Complexity   │                                              │
│  │ Classifier   │                                              │
│  └──┬─────┬────┬┘                                              │
│     │     │    │                                                │
│  Simple Complex Structured                                     │
│  (65%)  (15%)  (20%)                                           │
│     │     │    │                                                │
│     v     v    v                                                │
│  ┌──────┐┌──────┐┌──────────────┐                              │
│  │GPT-4o││GPT-4o││ API lookup   │                              │
│  │ mini ││      ││ (no LLM)     │                              │
│  └──────┘└──────┘└──────────────┘                              │
│     │     │    │                                                │
│     └─────┴────┘                                               │
│         │                                                       │
│         v                                                       │
│  ┌──────────────┐                                              │
│  │ Output       │                                              │
│  │ Guardrails   │                                              │
│  └──────────────┘                                              │
│         │                                                       │
│         v                                                       │
│  ┌──────────────┐                                              │
│  │ Async: trace,│                                              │
│  │ cost tag,    │                                              │
│  │ eval sample  │                                              │
│  └──────────────┘                                              │
└─────────────────────────────────────────────────────────────────┘
```

**Cost Breakdown (Before vs After)**:

| Component | Before (Monthly) | After (Monthly) | Savings |
|---|---|---|---|
| All traffic through GPT-4o | $180,000 | -- | -- |
| Semantic cache (25% hit rate) | -- | $0 (Redis: $200) | -- |
| GPT-4o mini (65% of remaining) | -- | $10,530 | -- |
| GPT-4o (15% of remaining) | -- | $34,690 | -- |
| API lookups, no LLM (20%) | -- | $500 (compute) | -- |
| Classifier overhead | -- | $2,000 | -- |
| **Total** | **$180,000** | **$47,920** | **73%** |

**Trade-off Matrix**:

| Decision | Option A | Option B | Chosen | Rationale |
|---|---|---|---|---|
| Cache threshold | 0.90 (higher hit rate) | 0.95 (higher precision) | 0.95 | False cache hits in support are worse than cache misses |
| Cheap model | GPT-4o mini | Self-hosted Llama 70B | GPT-4o mini | Operational simplicity; self-hosted adds $8K/mo infra + DevOps burden |
| Routing model | Trained classifier | Heuristic rules | Classifier | Rules break on edge cases; classifier accuracy improves with production data |
| Quality monitoring | Sample 5% | Sample 10% | 5% | At 500K/day, 5% = 25K scored samples. Statistically sufficient |
| Fallback on GPT-4o outage | Queue and retry | Route to Claude | Route to Claude | Support SLA requires < 5s response; queueing violates SLA |

**Decision Rationale**: The 73% cost reduction comes from three independent levers:
(1) semantic caching eliminates 25% of LLM calls entirely, (2) model routing sends
65% of remaining traffic to a model that costs 94% less per token, and (3) structured
queries (order status, account balance) bypass the LLM completely. The critical
constraint is that CSAT must not drop -- the classifier is trained on 3 months of
labeled support tickets, and the eval sampler continuously verifies that GPT-4o mini
responses score within 2% of GPT-4o on the golden dataset. If the gap widens, the
routing threshold automatically tightens, sending more traffic to the frontier model.

---

## Key Interview Signals

When discussing LLMOps in a Director/VP interview, anchor on these points:

1. **"HTTP 200 is not a test"** -- LLM quality regression is silent. If you cannot
   articulate how you detect behavioral degradation, you have not designed a production
   system.
2. **Cost is architecture, not optimization** -- Model routing, caching, and batching
   are not things you add later. They are load-bearing structural decisions.
3. **Eval gates must be fast** -- A 45-minute CI suite is equivalent to no suite. Keep
   the gate under 10 minutes; run the full suite overnight.
4. **Canary soak time matters** -- 2 hours on a Tuesday morning is not a canary. Hold
   for 24-72 hours to cover traffic distribution.
5. **The dataset beats the metric** -- A mediocre metric on a great dataset catches more
   real failures than a sophisticated metric on a poor dataset.
