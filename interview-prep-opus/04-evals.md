# Module 04: LLM & Agent Evaluation

## What Is This?

Imagine you hire a new employee. You would not just trust them blindly -- you would review their work, test them on known problems, and compare their performance against your best people. LLM evaluation is exactly that, but for AI systems. You feed the model known inputs, score its outputs against criteria (using humans, rules, or another LLM as the "judge"), and decide whether it is good enough to ship.

The twist: unlike testing traditional software where 2 + 2 must equal 4, LLM outputs are probabilistic and open-ended. There is no single correct answer for "summarize this document." So the field has developed specialized paradigms -- pointwise scoring, pairwise comparison, reference-based checking -- and layered them into CI/CD pipelines that gate deployments on quality thresholds.

Agent evaluation adds another dimension: you are no longer scoring a single response but an entire multi-step workflow -- did the agent pick the right tools, call them correctly, recover from errors, and ultimately achieve the user's goal?

## Why It Matters

Quality regressions hit 40% of LLM deployments within 90 days. Without systematic evaluation, you discover failures from customer complaints, not dashboards. Evaluation is the mechanism that transforms an AI demo into a production system -- it is the difference between "it works on my laptop" and "it works at scale under adversarial conditions with audit trails."

---

## Part 1: System Topology & Data Flow

### Eval Pipeline Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        EVALUATION CONTROL PLANE                         │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌───────────┐ │
│  │   Dataset    │───>│   Runner    │───>│   Scorer    │───>│Aggregator │ │
│  │  (Golden +   │    │ (Batch exec │    │ (Layered:   │    │(Experiment│ │
│  │  Regression) │    │  against    │    │ deterministic│   │  tracker) │ │
│  │             │    │  candidate) │    │ -> heuristic │    │           │ │
│  └──────┬──────┘    └─────────────┘    │ -> LLM judge)│    └─────┬─────┘ │
│         │                              └─────────────┘          │       │
│         │                                                        │       │
│         │    ┌──────────────────────────────────────────┐        │       │
│         │    │              CI GATE                      │        │       │
│         │    │  avg >= 0.85 ? PASS : BLOCK MERGE        │<───────┘       │
│         │    └──────────────┬───────────────────────────┘                │
│         │                   │                                            │
│         │    ┌──────────────v───────────────────────────┐                │
│         └────┤         FEEDBACK LOOP                    │                │
│              │  Failed production traces auto-promote   │                │
│              │  into regression dataset                 │                │
│              └─────────────────────────────────────────┘                │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                         DATA PLANE (Three Tiers)                         │
├────────────────────┬────────────────────┬────────────────────────────────┤
│   OFFLINE EVAL     │   CI/CD GATE       │   ONLINE MONITORING            │
│   (Pre-merge)      │   (Deploy pipeline)│   (Post-deploy)                │
│                    │                    │                                │
│  Golden dataset    │  Automated pass/   │  5-10% live traffic sampled    │
│  + regression      │  fail on quality   │  Scored continuously           │
│  suite on every    │  threshold every   │  Alert on sustained drift      │
│  PR touching       │  deployment        │  Rolling 24h/7d windows        │
│  prompts/models    │                    │                                │
└────────────────────┴────────────────────┴────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                        PERSISTENCE LAYER                                 │
├──────────────────────────────────────────────────────────────────────────┤
│  Dataset Store         │  Experiment Store       │  Audit Log             │
│  (git-versioned,       │  (run config, model,    │  (immutable, tamper-   │
│   content-addressable) │   prompt hash, scores,  │   evident, exportable  │
│                        │   per-row results)      │   to SIEM)             │
└────────────────────────┴─────────────────────────┴───────────────────────┘
```

### Request-Flow Narrative

1. **Dataset**: A golden dataset of 50+ production failure cases, versioned in git alongside code. Every discovered bug auto-promotes into the regression suite. Content-addressable storage ensures a score change is attributable to a code change, not dataset drift.

2. **Runner**: On every PR touching prompts, RAG config, or model selection, the runner invokes the candidate system in batch against all dataset rows. Each row exercises the full pipeline (retrieval, context assembly, LLM). Concurrency limits and exponential backoff prevent judge-model rate limits from blocking CI.

3. **Scorer**: Layered evaluation -- cheapest checks first. Format/schema validation (deterministic, milliseconds) filters out gross failures. Heuristic scoring (length, keyword presence) catches structural issues. LLM-as-judge (faithfulness, coherence) runs last, only on outputs that survived cheaper checks. This ordering keeps cost proportional to suite growth.

4. **Aggregator**: Persists every eval run with full configuration (dataset version, model, prompt template hash), aggregate scores, and per-row results. Enables run-to-run comparison and attribution of score deltas to specific system changes.

5. **CI Gate**: Score posted back as PR comment/check. Score drop below threshold (commonly avg >= 0.85) blocks merge. The threshold is domain-specific -- set it from cost of failure in that flow, not from convention.

6. **Feedback Loop**: Failing online scorers auto-promote production traces into the offline regression dataset, closing the loop between monitoring and testing.

### Three Evaluation Paradigms

| Paradigm | Mechanism | Strength | Weakness | Best For |
|---|---|---|---|---|
| **Pointwise** | Absolute score (1-5) against rubric | Simple, threshold-ready | Susceptible to prompt variation, randomness | Quality gates, production monitoring |
| **Pairwise** | Judge picks better of two responses | More stable (relative easier than absolute) | 2x inference cost (both orderings required), no absolute threshold | A/B testing model versions, prompt variants |
| **Reference-Based** | Compare against gold-standard answer | Most objective | Requires curated reference datasets | Factual accuracy, structured extraction |

---

## Part 2: Core Mechanics & Algorithms

### Agent Evaluation: Three-Layer Stack

**Layer 1 -- End-to-End (Task Completion)**

Binary or graded assessment of goal achievement. Success Rate (SR) is the primary metric. Critical distinction: execution completion is not task success. An agent that runs all steps but produces wrong output has 100% execution completion and 0% task success. Keep these metrics separate.

**Layer 2 -- Trajectory Scoring**

Scores the sequence of (state, action) pairs across the agent's execution. The 2026 standard uses an LLM judge scoring each pair on a 1-5 scale; the trajectory score is the geometric mean (not arithmetic).

Why geometric mean? It punishes any single bad step. With arithmetic mean, one catastrophic step (score=1) among 19 fine steps (score=5) gives (19*5 + 1)/20 = 4.8 -- the bad step nearly vanishes. With geometric mean: (5^19 * 1)^(1/20) = 4.17 -- the single failure drags the score down meaningfully.

Trajectory evaluation modes:
- **Exact matching**: Fixed workflows where step order is prescribed
- **Set-based matching**: Order-flexible -- correct steps in any sequence
- **Partial-credit**: Fractional credit for partially correct actions
- **LLM judge**: When multiple valid paths exist (most production scenarios)

**Layer 3 -- Component-Level (Tool Call Accuracy)**

Decomposes into four sub-metrics:
- **Selection accuracy**: Did the agent pick the right tool? (target >= 95%)
- **Argument correctness**: Were arguments syntactically and semantically valid? (target >= 90%)
- **Repetition rate**: Fraction of calls duplicating a previous call's effect (target < 5%)
- **Error recovery**: Did the agent recover from failed tool calls?

### LLM-as-Judge Bias Taxonomy

Understanding judge biases is essential for building reliable eval systems. Each bias has a known mitigation.

**Position Bias** (Severity: High)

GPT-4 changed its preferred answer when order was swapped on roughly one-third of pairwise cases (MT-Bench). Training data associates "first" with "best." A study of 15 judges across ~150,000 evaluation instances confirmed bias varies significantly across judges and tasks.

Mitigation: Evaluate every pairwise comparison in both orders; only count consistent verdicts. Use neutral labels ("Response A/B") instead of ordinal labels ("Response 1/2"). Randomize criteria order within rubrics. Cost: 2x inference, but not optional.

**Length/Verbosity Bias** (Severity: Medium-High)

LLM judges systematically rate longer responses higher, independent of correctness. Detection: correlation analysis between response length and judge scores. Mitigation: explicit rubric instruction ("concise responses score equal to or better than verbose ones at equivalent correctness"), length-controlled win rate for pairwise, length-normalization in scoring functions.

**Self-Preference Bias** (Severity: Medium)

A judge rates answers matching its own writing style higher. Mitigation: use a different model family for judging than for generation. Cross-model evaluation panels.

**Rubric Position Bias** (2026 finding)

LLMs prefer score options at specific positions within rubric lists. Reordering criteria shifts scores. Mitigation: randomize rubric option ordering across eval runs.

**Compounding Biases (FairJudge, Feb 2026)**

Position, length, formatting, and model provenance all shape verdicts in ways unrelated to content quality. Frontier models exceeded 50% error rates on bias tests in production. FairJudge approach: SFT for base judge behavior, DPO targeting non-semantic biases, GRPO enforcing consistency across scoring modes.

### The Dual Oracle Problem

No reliable ground truth exists for evaluating open-ended LLM outputs. Using another LLM as judge introduces its own biases -- two uncertain "oracles." Pre-built eval libraries ship generic rubrics producing confidently wrong scores on specialized applications.

Mitigation: Calibrate judges against human expert evaluation on 100-200 representative outputs. Track Cohen's kappa between judge and human consensus. Combine LLM judge with human review. Calibration is not optional.

### Human Evaluation: Inter-Annotator Agreement

| Task Type | Metric | Target kappa |
|---|---|---|
| Objective (classification, entity extraction) | Cohen's/Fleiss' kappa | >= 0.90 |
| Moderately subjective (relevance, coherence) | Cohen's/Fleiss' kappa | 0.70 - 0.85 |
| Inherently subjective (creativity, style) | Krippendorff's alpha | 0.60 - 0.75 |

Forcing higher targets on subjective tasks destroys signal. Accepting lower targets on objective tasks introduces noise.

**Kappa prevalence paradox**: Severe class imbalance produces surprisingly low kappa despite high raw agreement. If 90% of outputs are "pass," a judge that always says "pass" hits 90% raw agreement but kappa near zero. Always report prevalence alongside kappa.

**Chain-of-thought improves judge agreement**: Asking the judge to write a one-paragraph rationale before emitting the grade lifts inter-judge kappa from ~0.55 to ~0.75 on retrieval relevance tasks.

### Calibration Protocol

1. Written guidelines with 10-20 worked examples
2. Calibration sessions: annotators independently rate gold examples, discuss disagreements
3. Agreement gate: kappa > 0.6 on calibration set before production annotation begins
4. Ongoing monitoring: 5-15% overlap, rolling IAA, quarterly recalibration

### Benchmark Reward Hacking (Critical Knowledge)

UC Berkeley RDI (April 2026) demonstrated that an automated scanning agent broke all eight major agent benchmarks (SWE-bench, WebArena, OSWorld, GAIA, Terminal-Bench, FieldWorkArena, CAR-bench) by reward hacking -- achieving near-perfect scores without solving a single task.

SWE-bench inflation: 19.78% of "solved" cases are semantically incorrect. Independent estimates: 5-15 points inflation from training-data leakage. OpenAI audit: 59.4% of hardest Verified tasks have tests that would not catch the intended bug. A 90% headline score is closer to 75-80% real capability.

Takeaway: Build internal private evals (50-200 representative tasks from your actual product). Weight independent evaluations heavily and lab marketing lightly.

---

## Part 3: Token Economics & NFR Analysis

### Cost of Evaluation

**Judge cost guardrail**: Keep judge cost under 10-15% of production LLM cost. Act (reduce sampling or downgrade judge model) if approaching 25%.

**Per-eval-run cost (Claude Opus 5 as judge, 500-row dataset)**:

```
Input:  500 rows x 2K tokens/eval x $5.00/MTok  = $5.00
Output: 500 rows x 500 tokens/eval x $25.00/MTok = $6.25
Total per run (no caching):                        $11.25
Total per run (cached rubric):                     $2-3
```

**Benchmark suite costs**:

| Suite | Tasks | Cost per model per run |
|---|---|---|
| SWE-bench Verified | 500 | $50 - $200 (depends on agent loop length) |
| GAIA | 450+ | $30 - $100 |
| Custom private eval | 200 | $5 - $20 (Sonnet-class judge) |

### Cost Optimization Strategies

| Strategy | Savings | Tradeoff |
|---|---|---|
| Layered scoring (deterministic first) | 60-80% fewer LLM judge calls | Requires building deterministic checks |
| Distilled judge model | 10x cheaper per judgment | Slight accuracy loss on edge cases |
| Batch API for offline evals | 50% cost reduction | Higher latency (async) |
| Cached judge rubric (prompt caching) | 90% input cost reduction | Requires stable rubric prefix |
| Sample-based online scoring (5%) | 95% fewer production judge calls | Statistical sampling error |

### Latency Impact

Eval in CI adds 3-15 minutes per PR (depending on dataset size and judge model). Mitigation: run deterministic checks first (seconds), only invoke LLM judge on surviving outputs. Use batch API for non-blocking eval runs at 50% cost.

### Eval Pipeline Latency SLAs

| Tier | p50 | p95 | p99 | Mitigation |
|------|-----|-----|-----|------------|
| Single LLM-as-judge call | 2s | 5s | 12s | Prompt caching (90% cost reduction), smaller judge model for non-critical evals |
| Pairwise comparison (2 calls) | 4s | 10s | 25s | Parallel execution, batch both orders in one API call |
| Full suite (500 rows) | 5min | 12min | 20min | Concurrent workers (10-20), progressive evaluation (fail-fast on critical metrics) |
| CI gate (PR-level) | 3min | 8min | 15min | Subset sampling (100 rows for PR, full suite nightly), cached reference outputs |

### Throughput & Back-Pressure

Eval pipeline throughput depends on LLM provider rate limits (typical: 60-500 RPM). Design for back-pressure: use a queue (SQS/Redis) between eval runner and scorer. Implement concurrency control (semaphore limiting parallel judge calls). Monitor queue depth -- alert if >1000 pending evals (indicates eval backlog).

### Capacity Planning

For a 500-row suite with pairwise comparison: 1000 LLM calls x avg 800 tokens = 800K tokens. At $3/MTok (Sonnet): $2.40/run. At 100 RPM rate limit: ~10 min wall clock. Scale: 10 PRs/day x $2.40 = $24/day eval cost.

### Framework Pricing

| Framework | Cost | Notes |
|---|---|---|
| DeepEval | Free (OSS); Confident AI hosted: $19.99/user/month | pytest-style, CI-native |
| RAGAS | Free (fully OSS) | RAG-specific metrics |
| Promptfoo | Free (OSS) | Matrix comparison, config-driven |
| LangSmith | Free tier; paid ~$39/seat/month | Deep LangChain integration |
| Braintrust | Free tier; paid tiers for production | Experiment comparison + CI |
| Arize Phoenix | Free (self-hosted OSS) | Observability + eval |

### NFR Targets

| Dimension | Target | Notes |
|---|---|---|
| CI gate latency | < 15 min for 500-row suite | Layered scoring reduces wall time |
| Judge availability | 99.5% (fallback judge configured) | Primary: Opus; Fallback: Sonnet |
| Test-retest reliability | kappa >= 0.80 for same judge on same inputs | Score same outputs twice, measure agreement |
| Online eval sampling | 5-10% of production traffic | Reservoir sampling for representative coverage |
| Alert SLA | < 5 min from score drop to alert | Sustained drops, not individual outliers |

---

## Part 4: Distributed Resilience & Security

### Eval Pipeline Reliability

**Flaky CI builds**: LLM non-determinism causes flaky eval results. Use tolerance bands instead of exact thresholds (e.g., pass if score >= 0.83 rather than >= 0.85). Pin the judge model version. Sample a stable golden test set. Temperature=0 does not guarantee determinism across API calls.

**Judge model availability**: If the judge model is rate-limited or down, the entire CI pipeline blocks. Configure fallback judge model. Implement retry with exponential backoff. Cache judge responses for identical inputs.

**Dataset versioning**: Eval datasets must be versioned alongside code. A score change can come from dataset drift, not model regression. Use content-addressable storage or git-tracked fixtures.

### Online Eval Resilience

**Sampling strategy**: 5-10% of production traffic scored continuously. Use reservoir sampling for representative coverage. Alert on sustained score drops (not individual outliers).

**Drift detection**: Monitor rolling average of scores over 24h/7d windows. A sustained decline in LLM-judge kappa across multiple eval cycles signals drift before it becomes a crisis.

**Judge consistency monitoring**: Track test-retest reliability -- score the same outputs twice and measure agreement. An uncalibrated judge is a liability. Uncalibrated judges can show perfect dashboards while diverging from expert review.

### Checkpointing

For long-running eval suites (500+ items), implement checkpointing: persist partial results so a crash at item 400 does not lose items 1-399. Braintrust and LangSmith both support incremental result uploads.

### Security & Governance

**Regulatory context**: EU AI Act high-risk obligations (effective August 2026) require: risk management systems, data governance, record-keeping/logging, transparency, and human oversight.

**RBAC for evaluation systems** (four-role model):
- **Operator**: Can run evals, view aggregate scores. No PII access.
- **Auditor**: PII unmasking on dual approval. Can inspect individual traces.
- **Compliance owner**: Retention policies, legal hold.
- **Security**: Audit-of-audit log access.

**PII in evaluation data**: PII redaction is a continuous pipeline spanning prompts, completions, logs, traces, and audit trails. Layered defense: detect PII before the model sees context, redact after response generation, write audit records proving which guardrail acted. Log data category ("customer_pii detected and redacted"), not raw content.

**Audit trail requirements**: Immutable, complete (who, what, when, which model, which data category), tamper-evident, exportable to SIEM. Must capture: eval dataset version, judge model ID, prompt template hash, scorer configuration, individual and aggregate scores, reviewer identity.

### Governance Platforms (2026)

| Platform | Strength |
|---|---|
| **Braintrust** | Eval scoring + production traces + human review + CI release gates |
| **Galileo** | Runtime protection blocking unsafe outputs with audit trails + policy versioning |
| **Credo AI** | Portfolio-level governance across many AI systems with registries + risk assessments |
| **Lakera** | AI-native runtime security: prompt injection defense, PII protection |
| **Bifrost** | Open-source AI gateway: governance, budgets, access control, audit logs |

---

## Part 5: Production Enterprise Code

### Layered Eval Pipeline with CI Gate

```python
"""
Production eval pipeline with layered scoring, retries, circuit breaker,
and CI gate. Requires: openai, tenacity, structlog.
"""

import json
import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from pathlib import Path

import structlog
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------

class ScorerTier(Enum):
    DETERMINISTIC = "deterministic"
    HEURISTIC = "heuristic"
    LLM_JUDGE = "llm_judge"


@dataclass
class EvalRow:
    row_id: str
    input_text: str
    expected_output: str
    candidate_output: str = ""
    scores: dict = field(default_factory=dict)
    passed_tiers: list = field(default_factory=list)
    final_score: float = 0.0


@dataclass
class EvalRunResult:
    run_id: str
    dataset_version: str
    model_name: str
    prompt_hash: str
    total_rows: int
    passed_rows: int
    avg_score: float
    per_row_results: list
    duration_seconds: float
    judge_cost_usd: float
    gate_passed: bool


# ---------------------------------------------------------------------------
# Circuit breaker for judge model
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """Simple circuit breaker for LLM judge calls."""

    def __init__(self, failure_threshold: int = 5, reset_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = "closed"  # closed | open | half_open

    def record_success(self):
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            logger.warning(
                "circuit_breaker_opened",
                failure_count=self.failure_count,
            )

    def allow_request(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if time.time() - self.last_failure_time > self.reset_timeout:
                self.state = "half_open"
                return True
            return False
        # half_open: allow one probe request
        return True


# ---------------------------------------------------------------------------
# Scorers (layered: deterministic -> heuristic -> LLM judge)
# ---------------------------------------------------------------------------

def score_deterministic(row: EvalRow) -> Optional[float]:
    """Tier 1: Format and schema validation. Returns 0.0 on failure, None on pass."""
    output = row.candidate_output.strip()
    if not output:
        row.scores["deterministic"] = 0.0
        return 0.0
    # Check JSON parsability if expected output is JSON
    if row.expected_output.strip().startswith("{"):
        try:
            json.loads(output)
        except json.JSONDecodeError:
            row.scores["deterministic"] = 0.0
            return 0.0
    row.scores["deterministic"] = 1.0
    row.passed_tiers.append(ScorerTier.DETERMINISTIC)
    return None  # None means "passed this tier, continue to next"


def score_heuristic(row: EvalRow) -> Optional[float]:
    """Tier 2: Length bounds and keyword checks."""
    output = row.candidate_output.strip()
    # Penalize extremely short or extremely long outputs
    if len(output) < 10:
        row.scores["heuristic"] = 0.2
        return 0.2
    if len(output) > 50_000:
        row.scores["heuristic"] = 0.3
        return 0.3
    row.scores["heuristic"] = 1.0
    row.passed_tiers.append(ScorerTier.HEURISTIC)
    return None


class LLMJudge:
    """Tier 3: LLM-as-judge with retry, fallback, and circuit breaker."""

    RUBRIC = (
        "You are an expert evaluator. Score the candidate response on a scale "
        "of 1 to 5 based on: faithfulness to the provided context, relevance "
        "to the question, coherence, and correctness. Concise responses that "
        "are correct score equal to or better than verbose ones. Output ONLY "
        "a JSON object: {\"score\": <int 1-5>, \"rationale\": \"<one paragraph>\"}."
    )

    def __init__(
        self,
        primary_model: str = "claude-sonnet-4-5-20250514",
        fallback_model: str = "claude-haiku-4-5-20250514",
        input_cost_per_mtok: float = 3.0,
        output_cost_per_mtok: float = 15.0,
    ):
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.input_cost_per_mtok = input_cost_per_mtok
        self.output_cost_per_mtok = output_cost_per_mtok
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, reset_timeout=60)
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    def _call_judge(self, model: str, prompt: str) -> dict:
        """Call judge model. In production, replace with actual API client."""
        # Placeholder: in production, use anthropic.Anthropic().messages.create(...)
        # This demonstrates the retry/circuit-breaker skeleton.
        raise NotImplementedError(
            "Replace with actual LLM API call: "
            "client.messages.create(model=model, messages=[...], max_tokens=300)"
        )

    def score(self, row: EvalRow) -> float:
        prompt = (
            f"{self.RUBRIC}\n\n"
            f"Question: {row.input_text}\n"
            f"Expected: {row.expected_output}\n"
            f"Candidate: {row.candidate_output}"
        )
        model = self.primary_model
        if not self.circuit_breaker.allow_request():
            model = self.fallback_model
            logger.info("judge_fallback_activated", fallback_model=model)

        try:
            result = self._call_judge(model, prompt)
            self.circuit_breaker.record_success()
            score = result.get("score", 3) / 5.0
            row.scores["llm_judge"] = score
            row.scores["llm_rationale"] = result.get("rationale", "")
            row.passed_tiers.append(ScorerTier.LLM_JUDGE)
            return score
        except NotImplementedError:
            # Demo mode: return neutral score
            row.scores["llm_judge"] = 0.7
            row.passed_tiers.append(ScorerTier.LLM_JUDGE)
            return 0.7
        except Exception as exc:
            self.circuit_breaker.record_failure()
            logger.error("judge_call_failed", error=str(exc), model=model)
            row.scores["llm_judge"] = 0.5  # Neutral fallback score
            return 0.5

    def estimated_cost_usd(self) -> float:
        input_cost = self.total_input_tokens * self.input_cost_per_mtok / 1_000_000
        output_cost = self.total_output_tokens * self.output_cost_per_mtok / 1_000_000
        return input_cost + output_cost


# ---------------------------------------------------------------------------
# Eval pipeline orchestrator
# ---------------------------------------------------------------------------

class EvalPipeline:
    """Orchestrates layered evaluation with checkpointing."""

    def __init__(
        self,
        judge: LLMJudge,
        gate_threshold: float = 0.85,
        checkpoint_dir: Optional[str] = None,
    ):
        self.judge = judge
        self.gate_threshold = gate_threshold
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else None
        self.completed_rows: list[dict] = []

    def _checkpoint(self, row_result: dict):
        """Persist partial results for crash recovery."""
        self.completed_rows.append(row_result)
        if self.checkpoint_dir:
            self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
            cp_path = self.checkpoint_dir / "checkpoint.jsonl"
            with open(cp_path, "a") as f:
                f.write(json.dumps(row_result) + "\n")

    def _load_checkpoint(self) -> set:
        """Load completed row IDs from checkpoint file."""
        completed_ids = set()
        if self.checkpoint_dir:
            cp_path = self.checkpoint_dir / "checkpoint.jsonl"
            if cp_path.exists():
                with open(cp_path) as f:
                    for line in f:
                        data = json.loads(line.strip())
                        completed_ids.add(data["row_id"])
                        self.completed_rows.append(data)
        return completed_ids

    def run(
        self,
        dataset: list[EvalRow],
        dataset_version: str,
        model_name: str,
        prompt_template: str,
    ) -> EvalRunResult:
        start_time = time.time()
        run_id = hashlib.sha256(
            f"{dataset_version}:{model_name}:{time.time()}".encode()
        ).hexdigest()[:12]
        prompt_hash = hashlib.sha256(prompt_template.encode()).hexdigest()[:12]

        completed_ids = self._load_checkpoint()
        logger.info(
            "eval_run_started",
            run_id=run_id,
            total_rows=len(dataset),
            resumed_rows=len(completed_ids),
        )

        for row in dataset:
            if row.row_id in completed_ids:
                continue

            # Tier 1: Deterministic
            result = score_deterministic(row)
            if result is not None:
                row.final_score = result
                self._checkpoint({"row_id": row.row_id, "score": result, "tier": "deterministic"})
                continue

            # Tier 2: Heuristic
            result = score_heuristic(row)
            if result is not None:
                row.final_score = result
                self._checkpoint({"row_id": row.row_id, "score": result, "tier": "heuristic"})
                continue

            # Tier 3: LLM Judge (only if survived cheaper tiers)
            score = self.judge.score(row)
            row.final_score = score
            self._checkpoint({"row_id": row.row_id, "score": score, "tier": "llm_judge"})

        duration = time.time() - start_time
        scores = [r["score"] for r in self.completed_rows]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        passed = sum(1 for s in scores if s >= self.gate_threshold)

        result = EvalRunResult(
            run_id=run_id,
            dataset_version=dataset_version,
            model_name=model_name,
            prompt_hash=prompt_hash,
            total_rows=len(dataset),
            passed_rows=passed,
            avg_score=round(avg_score, 4),
            per_row_results=self.completed_rows,
            duration_seconds=round(duration, 2),
            judge_cost_usd=round(self.judge.estimated_cost_usd(), 4),
            gate_passed=avg_score >= self.gate_threshold,
        )

        logger.info(
            "eval_run_completed",
            run_id=run_id,
            avg_score=result.avg_score,
            gate_passed=result.gate_passed,
            duration_s=result.duration_seconds,
        )
        return result


# ---------------------------------------------------------------------------
# Agent trajectory scorer (geometric mean)
# ---------------------------------------------------------------------------

import math

def geometric_mean(scores: list[float]) -> float:
    """Compute geometric mean. Punishes any single bad step."""
    if not scores:
        return 0.0
    product = 1.0
    for s in scores:
        if s <= 0:
            return 0.0
        product *= s
    return product ** (1.0 / len(scores))


@dataclass
class TrajectoryStep:
    step_index: int
    action: str
    tool_name: Optional[str]
    tool_args: Optional[dict]
    result: str
    score: float = 0.0  # 1-5 scale, set by judge


def score_trajectory(steps: list[TrajectoryStep]) -> dict:
    """Score an agent trajectory using geometric mean.

    Returns dict with geometric mean, arithmetic mean (for comparison),
    and per-step scores.
    """
    raw_scores = [s.score for s in steps]
    normalized = [s / 5.0 for s in raw_scores]  # Normalize to 0-1

    geo = geometric_mean(normalized)
    arith = sum(normalized) / len(normalized) if normalized else 0.0

    return {
        "geometric_mean": round(geo, 4),
        "arithmetic_mean": round(arith, 4),
        "step_count": len(steps),
        "min_step_score": min(raw_scores) if raw_scores else 0,
        "per_step": [
            {"step": s.step_index, "action": s.action, "score": s.score}
            for s in steps
        ],
    }


# ---------------------------------------------------------------------------
# Online eval sampler with drift detection
# ---------------------------------------------------------------------------

import random
from collections import deque
from datetime import datetime


class OnlineEvalMonitor:
    """Samples production traffic and detects quality drift."""

    def __init__(
        self,
        sample_rate: float = 0.05,
        window_size: int = 1000,
        alert_threshold: float = 0.80,
        sustained_drop_count: int = 3,
    ):
        self.sample_rate = sample_rate
        self.window_size = window_size
        self.alert_threshold = alert_threshold
        self.sustained_drop_count = sustained_drop_count
        self.score_window: deque = deque(maxlen=window_size)
        self.rolling_averages: list[dict] = []
        self.consecutive_drops = 0

    def should_sample(self) -> bool:
        return random.random() < self.sample_rate

    def record_score(self, trace_id: str, score: float, metadata: dict):
        self.score_window.append({
            "trace_id": trace_id,
            "score": score,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": metadata,
        })
        self._check_drift()

    def _check_drift(self):
        if len(self.score_window) < 50:
            return

        recent = list(self.score_window)[-100:]
        avg = sum(r["score"] for r in recent) / len(recent)

        self.rolling_averages.append({
            "timestamp": datetime.utcnow().isoformat(),
            "avg_score": round(avg, 4),
            "window_size": len(recent),
        })

        if avg < self.alert_threshold:
            self.consecutive_drops += 1
            if self.consecutive_drops >= self.sustained_drop_count:
                logger.error(
                    "quality_drift_alert",
                    avg_score=round(avg, 4),
                    threshold=self.alert_threshold,
                    consecutive_drops=self.consecutive_drops,
                )
        else:
            self.consecutive_drops = 0

    def get_status(self) -> dict:
        scores = [r["score"] for r in self.score_window]
        return {
            "window_size": len(self.score_window),
            "current_avg": round(sum(scores) / len(scores), 4) if scores else 0.0,
            "consecutive_drops": self.consecutive_drops,
            "alert_active": self.consecutive_drops >= self.sustained_drop_count,
        }
```


---

## Part 6: Architectural System Design Scenarios

### Scenario 1: Enterprise RAG Evaluation Pipeline

**Problem Statement**: A financial services company deploys a RAG-based assistant that answers questions about internal policy documents. After 60 days in production, customer support escalations increase 3x. Investigation reveals the assistant is hallucinating policy details that sound plausible but are wrong. There is no systematic evaluation -- quality was assessed by manual spot-checks during development.

**Architecture**:

```
┌─────────────────────────────────────────────────────────────────────┐
│                     OFFLINE EVALUATION                               │
│                                                                      │
│  ┌──────────────┐   ┌──────────────┐   ┌───────────────────────┐    │
│  │ Golden Dataset│──>│  RAG Pipeline │──>│  Layered Scorer       │    │
│  │ 500 rows,    │   │  (full e2e:  │   │  1. Format check      │    │
│  │ git-versioned│   │  retrieve +  │   │  2. RAGAS faithfulness│    │
│  │              │   │  generate)   │   │  3. RAGAS relevance   │    │
│  └──────────────┘   └──────────────┘   │  4. LLM judge coherence│   │
│                                         └──────────┬──────────────┘  │
│                                                     v                │
│                                         ┌───────────────────────┐    │
│                                         │  CI Gate: avg >= 0.85 │    │
│                                         │  Block PR if below    │    │
│                                         └───────────────────────┘    │
├──────────────────────────────────────────────────────────────────────┤
│                     ONLINE MONITORING                                │
│                                                                      │
│  Production ──5% sample──> Same scorer pipeline ──> Grafana dashboard│
│                                                                      │
│  Failing traces auto-promote into offline regression dataset         │
├──────────────────────────────────────────────────────────────────────┤
│                     HUMAN CALIBRATION                                │
│                                                                      │
│  Monthly: 100 samples x 3 domain experts                            │
│  Track Cohen's kappa (LLM judge vs human consensus)                 │
│  Re-calibrate when kappa < 0.60                                     │
└──────────────────────────────────────────────────────────────────────┘
```

**Trade-off Matrix**:

| Decision | Option A | Option B | Choice | Rationale |
|---|---|---|---|---|
| Judge model | Opus (highest accuracy) | Sonnet (10x cheaper) | Opus offline, Sonnet online | Cost control: Sonnet handles 95% volume; Opus ensures calibration accuracy |
| Scoring approach | Score every response | Sample 5% | Sample 5% online, 100% offline | Full scoring costs 20x more; sampling catches sustained drift |
| Dataset growth | Manual curation only | Auto-promote failures | Auto-promote + quarterly curation | Auto-promotion catches real regressions; manual curation ensures diversity |
| Gate threshold | 0.90 (strict) | 0.85 (standard) | 0.85 with tolerance band (0.83) | Financial domain is high-stakes but 0.90 causes excessive false blocks |

**Decision Rationale**: The layered scorer design (deterministic -> RAGAS -> LLM judge) keeps cost proportional to dataset growth. RAGAS metrics (context precision, context recall, faithfulness, answer relevance) map directly to RAG failure modes: wrong retrieval, hallucination, irrelevance. The auto-promotion feedback loop means every production failure strengthens the test suite. Monthly human calibration prevents judge drift from creating false confidence.

---

### Scenario 2: Agent Deployment Quality Gate

**Problem Statement**: A SaaS company builds an AI agent that handles customer billing inquiries -- checking balances, applying credits, processing refunds. Before deploying a new agent version (upgraded model, revised prompts, new tools), they need a quality gate that blocks unsafe releases. The agent has access to financial tools (apply_credit, process_refund) where errors have direct monetary impact.

**Architecture**:

```
┌────────────────────────────────────────────────────────────────────┐
│                    FOUR-DIMENSIONAL QUALITY GATE                   │
│                                                                    │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐          │
│  │  Dim 1:      │   │  Dim 2:      │   │  Dim 3:      │          │
│  │  OUTCOME     │   │  TRAJECTORY  │   │  TOOL USE    │          │
│  │              │   │              │   │              │          │
│  │  Task SR     │   │  Geo-mean    │   │  Selection   │          │
│  │  >= 85%      │   │  >= 3.5/5    │   │  acc >= 95%  │          │
│  │  on 200-task │   │  (LLM judge) │   │  Arg correct │          │
│  │  internal    │   │              │   │  >= 90%      │          │
│  │  eval        │   │              │   │  Repeat < 5% │          │
│  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘          │
│         │                  │                   │                  │
│         v                  v                   v                  │
│  ┌─────────────────────────────────────────────────────────┐      │
│  │                   GATE LOGIC                             │      │
│  │  Block deploy if ANY dimension regresses > 5%           │      │
│  │  from baseline                                          │      │
│  └────────────────────────┬────────────────────────────────┘      │
│                           │                                       │
│  ┌────────────────────────v────────────────────────────────┐      │
│  │  Dim 4: COST                                            │      │
│  │  Per-task $ within 2x of baseline                       │      │
│  │  p95 latency < 30s                                      │      │
│  └─────────────────────────────────────────────────────────┘      │
└────────────────────────────────────────────────────────────────────┘
```

**Trade-off Matrix**:

| Decision | Option A | Option B | Choice | Rationale |
|---|---|---|---|---|
| Eval dataset | Public benchmarks | Internal 200-task suite | Internal suite | Public benchmarks are gameable (reward hacking); internal tasks reflect actual product |
| Trajectory scoring | Arithmetic mean | Geometric mean | Geometric mean | One bad step in billing (wrong refund) must not be averaged away |
| Gate strictness | Block on any regression | Block on > 5% regression | > 5% regression | Zero-tolerance causes deployment paralysis from LLM non-determinism |
| Tool use verification | Log-based post-hoc | Real-time during eval | Real-time | Financial tools require immediate detection of incorrect arguments |

**Decision Rationale**: The four-dimensional gate ensures no single metric masks a failure. Outcome alone misses unsafe trajectories (right answer via wrong path). Trajectory alone misses tool misuse. Tool accuracy alone misses goal failure. Cost prevents runaway agent loops. The 5% regression tolerance absorbs LLM non-determinism while catching real regressions. Geometric mean for trajectory scoring is essential in financial contexts -- one incorrect refund step among 19 correct steps must drag the score down, not vanish in an average.

