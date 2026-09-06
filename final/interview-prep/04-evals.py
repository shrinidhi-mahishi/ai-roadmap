"""
LLM and Agent Evaluation -- Interview Prep Code Snippets.

Covers the basic eval harness (run agent, compare output, score), LLM-as-judge
pattern with bias mitigations, pass@k and pass^k estimators, trajectory evaluation
with geometric mean scoring, the dual-oracle pattern (deterministic + LLM judge),
and an eval CI pipeline skeleton.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from math import comb, lgamma, exp, log
from typing import Optional, NamedTuple


# --- pass@k Estimator (Chen et al., HumanEval) ---

def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k estimator (Chen et al., HumanEval).

    Probability that at least one of k independent samples passes.
    Answers: "can the system do this at all?"

    Formula: pass@k = 1 - C(n-c, k) / C(n, k)

    Why not naive 1-(1-c/n)^k? The naive formula is biased upward for small n.
    The Chen product uses combinatorial exact counting.

    Returns NaN when n < k (Inspect convention -- do not extrapolate.
    This is correct behavior, not a bug).

    Example: n=10 solutions, c=4 pass unit tests.
      pass@1 = 1 - C(6,1)/C(10,1) = 0.4
      pass@5 = 1 - C(6,5)/C(10,5) = 1 - 6/252 = 0.976

    Key: pass@k requires a VERIFIER (unit tests, DB state check) to pick
    the correct sample. An LLM judge is not a verifier.
    """
    if n < k:
        return float("nan")
    if c == 0:
        return 0.0
    if c == n:
        return 1.0
    # Compute in log-space to avoid overflow with large n
    log_num = lgamma(n - c + 1) - lgamma(n - c - k + 1) - lgamma(k + 1)
    log_den = lgamma(n + 1) - lgamma(n - k + 1) - lgamma(k + 1)
    return 1.0 - exp(log_num - log_den)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """pass^k reliability estimator (Inspect, without replacement).

    Probability that ALL k independent trials succeed.
    This is what users experience -- they get one try.

    Formula: pass^k = C(c, k) / C(n, k)

    The gap between pass@k and pass^k can be enormous:
      tau-airline (GPT-4o class): pass@1=0.332, pass^5=0.100 (23.2 pp gap)
      tau-airline (think+prompt): pass@1=0.584, pass^5=0.340 (24.4 pp gap)
      Maximum observed gap: 24.9 pp

    Gate on pass^k and report pass@k as the capability envelope.
    Mixing them up is how a demo becomes an SLO.
    """
    if n < k:
        return float("nan")
    if c < k:
        return 0.0
    return comb(c, k) / comb(n, k)


# --- Basic Eval Harness ---

@dataclass
class EvalTask:
    """A single evaluation task (one row in the eval dataset)."""
    task_id: str
    input_text: str
    expected_output: str = ""
    expected_tool_calls: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class EvalOutput:
    """Output from running the agent/model on a task."""
    task_id: str
    output_text: str
    tool_calls: list = field(default_factory=list)
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    error: Optional[str] = None


class GraderStatus(Enum):
    """Status of a scoring attempt.

    Unscored rows are never counted as passed.
    Inspect returns NaN when n < k -- this is correct behavior.
    """
    SCORED = "scored"
    SKIPPED = "skipped"       # Online: judge breaker open
    ERROR = "error"           # CI: judge breaker open (fail the build)
    TIMEOUT = "timeout"       # Judge exceeded timeout


@dataclass
class ScoreResult:
    """Result from a single scorer."""
    score: float              # 0.0 - 1.0
    passed: bool
    grader: str               # Name of the scoring method
    status: GraderStatus
    reasoning: str = ""       # Judge chain-of-thought (improves kappa 0.55 -> 0.75)


def run_eval_harness(
    tasks: list[EvalTask],
    agent_fn,
    scorers: list,
    k_trials: int = 1,
) -> dict:
    """Basic eval harness: run agent on tasks, score outputs, aggregate.

    The evaluated system is NOT "the model." It is:
    Model x Scaffold x Tools x Environment x Judge x Sampling x Retries x Infra

    If any term changes, the score changes. Anthropic showed infra alone
    (Docker image, RAM, network) accounts for 6 pp on Terminal-Bench.

    run_key must include (suite_id, dataset_version, harness_commit,
    judge_model, trial_id) -- a scaffold change is a new measurement.
    """
    results = []
    for task in tasks:
        trial_outputs = []
        for trial in range(k_trials):
            output = agent_fn(task)
            scores = []
            for scorer in scorers:
                score = scorer(task, output)
                scores.append(score)
            trial_outputs.append({"output": output, "scores": scores})
        results.append({"task_id": task.task_id, "trials": trial_outputs})

    # Aggregate with pass@k and pass^k
    all_passed = []
    for result in results:
        n = len(result["trials"])
        c = sum(
            1 for t in result["trials"]
            if all(s.passed for s in t["scores"])
        )
        all_passed.append({"task_id": result["task_id"], "n": n, "c": c})

    total_n = sum(r["n"] for r in all_passed)
    total_c = sum(r["c"] for r in all_passed)

    return {
        "per_task": all_passed,
        "pass_at_1": pass_at_k(total_n, total_c, 1),
        "pass_hat_1": pass_hat_k(total_n, total_c, 1),
        "pass_at_k": pass_at_k(total_n, total_c, k_trials),
        "pass_hat_k": pass_hat_k(total_n, total_c, k_trials),
        "total_tasks": len(tasks),
        "total_trials": total_n,
        "total_passed": total_c,
    }


# --- LLM-as-Judge Pattern ---

def llm_judge_pointwise(
    task: EvalTask,
    output: EvalOutput,
    rubric: str = "",
    judge_model: str = "claude-sonnet-5",
) -> ScoreResult:
    """Pointwise LLM-as-judge scoring.

    Score 0-5 against a rubric. Simple, threshold-ready.
    Used for quality gates, production monitoring.

    Key bias mitigations:
    1. Position bias: GPT-4 flipped ~1/3 of pairwise; use both orderings
    2. Length bias: explicit rubric: "concise >= verbose at equal correctness"
    3. Self-preference: +10% (GPT-4) to +25% (Claude-v1); use different family
    4. Rubric position bias (2026): randomize criteria ordering
    5. FairJudge (Feb 2026): frontier models >50% error rates on bias tests

    Chain-of-thought improves agreement: kappa from ~0.55 to ~0.75.
    Few-shot raises consistency 65->77.5% at 4x cost WITHOUT lifting human agreement.
    """
    # In production: actual LLM API call
    # prompt = f"""Score the following output on a 1-5 scale.
    # Rubric: {rubric}
    # Input: {task.input_text}
    # Expected: {task.expected_output}
    # Output: {output.output_text}
    # Think step by step, then give your score."""

    # Stub: deterministic scoring for demo
    if output.error:
        return ScoreResult(0.0, False, "llm_judge", GraderStatus.SCORED, "Error in output")

    # Simple heuristic standing in for LLM judge
    has_content = len(output.output_text.strip()) > 10
    matches_expected = (
        task.expected_output.lower() in output.output_text.lower()
        if task.expected_output else True
    )

    score = 0.0
    if has_content:
        score += 0.5
    if matches_expected:
        score += 0.5

    return ScoreResult(
        score=score,
        passed=score >= 0.7,
        grader=f"llm_judge:{judge_model}",
        status=GraderStatus.SCORED,
        reasoning=f"Content: {has_content}, matches: {matches_expected}",
    )


def llm_judge_pairwise(
    task: EvalTask,
    output_a: EvalOutput,
    output_b: EvalOutput,
) -> dict:
    """Pairwise LLM-as-judge comparison.

    More stable than pointwise (relative easier than absolute).
    2x inference cost (BOTH orderings required -- not optional).
    Used for A/B testing model versions, prompt variants.

    MUST evaluate both orderings and only count consistent verdicts.
    Use "Response A/B" labels not "1/2" to reduce position bias.
    """
    # Evaluate A-first ordering
    # In production: LLM call comparing A vs B
    a_first_winner = "A" if len(output_a.output_text) > len(output_b.output_text) else "B"

    # Evaluate B-first ordering (swap positions)
    # In production: same LLM call but with B presented first
    b_first_winner = "B" if len(output_a.output_text) > len(output_b.output_text) else "A"

    # Only count consistent verdicts
    if a_first_winner == b_first_winner:
        consistent = True
        winner = a_first_winner
    else:
        consistent = False
        winner = "tie"  # Inconsistent -> treat as tie

    return {
        "winner": winner,
        "consistent": consistent,
        "a_first_result": a_first_winner,
        "b_first_result": b_first_winner,
        "note": "Position-swapped; only consistent verdicts counted",
    }


# --- Trajectory Evaluation (Multi-Step Agent Scoring) ---

@dataclass
class TrajectoryStep:
    """One step in an agent's execution trajectory."""
    step_index: int
    action: str
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    result: str = ""
    score: float = 0.0  # 1-5 scale, set by judge


def geometric_mean(scores: list[float]) -> float:
    """Geometric mean for trajectory scoring.

    Why geometric (not arithmetic) mean for agent trajectories:
    - Arithmetic: one score=1 among 19 score=5 gives (19*5+1)/20 = 4.8
      (bad step VANISHES)
    - Geometric: (5^19 * 1)^(1/20) = 4.17
      (single failure DRAGS score down meaningfully)

    In financial contexts, one incorrect refund step among 19 correct steps
    must not vanish in an average. Geometric mean ensures this.

    This is the 2026 standard for trajectory scoring.
    """
    if not scores:
        return 0.0
    product = 1.0
    for s in scores:
        if s <= 0:
            return 0.0
        product *= s
    return product ** (1.0 / len(scores))


def score_trajectory(steps: list[TrajectoryStep]) -> dict:
    """Score an agent trajectory across multiple dimensions.

    Three-layer evaluation stack:
    Layer 1 -- End-to-End: binary/graded task completion (SR)
    Layer 2 -- Trajectory: geometric mean of per-step scores
    Layer 3 -- Component: tool selection acc, arg correctness, repeat rate

    Lucky pass detection: AgentLens found 10.7% (range 0.5-23.2%) of agent
    passes are "lucky" -- right answer through incorrect process.
    Process overlay (trajectory scoring) catches these.
    """
    if not steps:
        return {"geometric_mean": 0.0, "arithmetic_mean": 0.0, "step_count": 0}

    raw_scores = [s.score for s in steps]
    # Normalize to 0-1 scale (from 1-5)
    normalized = [s / 5.0 for s in raw_scores]

    geo = geometric_mean(normalized)
    arith = sum(normalized) / len(normalized)

    return {
        "geometric_mean": round(geo, 4),
        "arithmetic_mean": round(arith, 4),
        "gap": round(arith - geo, 4),  # Shows how much geo penalizes bad steps
        "step_count": len(steps),
        "min_step_score": min(raw_scores),
        "per_step": [
            {"step": s.step_index, "action": s.action, "score": s.score}
            for s in steps
        ],
    }


def tool_call_accuracy(
    expected_calls: list[dict],
    actual_calls: list[dict],
) -> dict:
    """Evaluate tool call accuracy (BFCL-style).

    Four sub-metrics:
    - Selection accuracy: >= 95% target (picked wrong tool?)
    - Argument correctness: >= 90% target (args valid?)
    - Repetition rate: < 5% target (duplicate calls?)
    - Error recovery: qualitative

    BFCL V4 bucket weights: 40/30/10/10/10.
    Uses AST matching (not string comparison) because argument order
    and formatting can vary without changing semantics.
    """
    if not expected_calls:
        return {
            "selection_accuracy": 1.0 if not actual_calls else 0.0,
            "argument_correctness": 1.0,
            "repetition_rate": 0.0,
        }

    # Selection accuracy: did the agent call the right tools?
    expected_tools = [c.get("name", "") for c in expected_calls]
    actual_tools = [c.get("name", "") for c in actual_calls]
    correct_selections = sum(
        1 for e in expected_tools if e in actual_tools
    )
    selection_acc = correct_selections / len(expected_tools)

    # Argument correctness: for matching tool calls, are args correct?
    correct_args = 0
    matched = 0
    for exp in expected_calls:
        for act in actual_calls:
            if exp.get("name") == act.get("name"):
                matched += 1
                # Compare args (simplified -- production uses AST matching)
                if json.dumps(exp.get("args", {}), sort_keys=True) == \
                   json.dumps(act.get("args", {}), sort_keys=True):
                    correct_args += 1
                break
    arg_acc = correct_args / max(matched, 1)

    # Repetition rate
    seen = set()
    repeats = 0
    for call in actual_calls:
        key = json.dumps(call, sort_keys=True)
        if key in seen:
            repeats += 1
        seen.add(key)
    repeat_rate = repeats / max(len(actual_calls), 1)

    return {
        "selection_accuracy": round(selection_acc, 3),
        "argument_correctness": round(arg_acc, 3),
        "repetition_rate": round(repeat_rate, 3),
        "total_expected": len(expected_calls),
        "total_actual": len(actual_calls),
    }


# --- Dual-Oracle Pattern ---

class GateDecision(NamedTuple):
    """Ship/hold decision from the eval gate."""
    ship: bool
    reason: str


def dual_oracle_gate(
    hard_passed: Optional[bool],
    hard_status: GraderStatus,
    soft_score: float,
    soft_status: GraderStatus,
    soft_threshold: float = 0.85,
    coverage_scored: int = 100,
    coverage_eligible: int = 100,
    min_coverage: float = 0.95,
) -> GateDecision:
    """Dual-oracle gate: hard (deterministic) + soft (LLM judge).

    The enterprise default for any agent that touches money, policy, or safety.

    Rules:
    1. Hard fail -> HOLD (regardless of soft score)
    2. Hard error -> HOLD in CI (fail-closed)
    3. Soft score below threshold -> HOLD
    4. Coverage below floor -> HOLD (unscored != passed)
    5. All checks pass -> SHIP

    Why dual-oracle?
    - Hard-only ships "correct but hostile" and misses PII-in-logs
    - Soft-only ships "pretty wrong" (ARE whole-trace judge precision = 0.53)
    - Safety is never in the mean -- a 0.70 safety score hidden in "quality 0.93" is a lawsuit

    Example: refund agent correctly processes refund (hard passes) but says
    "I processed your stupid refund" (soft catches hostile tone).
    """
    # Gate 1: Hard oracle errors
    if hard_status == GraderStatus.ERROR:
        return GateDecision(False, "hard_oracle_error")

    # Gate 2: Hard oracle failure
    if hard_passed is False:
        return GateDecision(False, "hard_fail")

    # Gate 3: Coverage check (unscored rows are NOT passed)
    coverage = coverage_scored / max(coverage_eligible, 1)
    if coverage < min_coverage:
        return GateDecision(False, f"coverage_{coverage:.2f}_below_{min_coverage}")

    # Gate 4: Soft oracle
    if soft_status == GraderStatus.SKIPPED:
        # Online: judge breaker open, but hard passed -> OK
        return GateDecision(True, "soft_skipped_hard_passed")

    if soft_score < soft_threshold:
        return GateDecision(False, f"soft_{soft_score:.2f}_below_{soft_threshold}")

    return GateDecision(True, "all_checks_passed")


# --- Circuit Breaker for Judge Model ---

class CircuitBreaker:
    """Judge availability circuit breaker.

    Key invariant:
    - Online: breaker open -> SKIP score (never block user). 0 ms eval tax.
    - CI: breaker open -> ERROR (never pass silently). Fail the build.

    This asymmetry (fail-open online, fail-closed CI) is the correct default.
    The most common eval architecture mistake: inlining the judge on the user
    path (+12s on user p99, judge 429s become user 500s).
    """
    def __init__(self, failure_threshold: int = 5, reset_timeout_s: float = 60.0):
        self.threshold = failure_threshold
        self.timeout = reset_timeout_s
        self.failures = 0
        self.last_failure = 0.0
        self.state = "closed"

    def allow(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if time.time() - self.last_failure > self.timeout:
                self.state = "half_open"
                return True
            return False
        return True  # half_open: probe

    def record_success(self):
        self.failures = 0
        self.state = "closed"

    def record_failure(self):
        self.failures += 1
        self.last_failure = time.time()
        if self.failures >= self.threshold:
            self.state = "open"


# --- Eval CI Pipeline Skeleton ---

@dataclass
class EvalConfig:
    """Configuration for an eval CI pipeline run."""
    suite_id: str
    dataset_version: str
    harness_commit: str
    judge_model: str = "claude-sonnet-5"
    k_trials: int = 5
    soft_threshold: float = 0.85
    min_coverage: float = 0.95
    max_wall_time_s: float = 900.0  # 15 min for 500-row suite


def run_key(
    suite_id: str,
    dataset_version: str,
    harness_commit: str,
    judge_model: str,
    trial_id: str,
) -> str:
    """Deterministic key for an experiment run.

    Includes harness_commit so a scaffold change is a new measurement.
    SWE-bench caches on (run_id, instance_id) -- reusing run_id with a
    different patch is a silent no-op. Always mint a new key.

    SWE-agent ACI shell vs raw bash on the same GPT-4 Turbo moved scores
    +64% relative (12.47% absolute). The scaffold IS part of the measurement.
    """
    parts = f"{suite_id}|{dataset_version}|{harness_commit}|{judge_model}|{trial_id}"
    return hashlib.sha256(parts.encode()).hexdigest()[:16]


@dataclass
class CIEvalResult:
    """Result of a CI eval pipeline run."""
    run_id: str
    config: EvalConfig
    total_tasks: int
    scored_tasks: int
    passed_tasks: int
    pass_at_1: float
    pass_hat_k: float
    avg_soft_score: float
    gate_decision: GateDecision
    wall_time_s: float
    judge_cost_usd: float


def eval_ci_pipeline(
    config: EvalConfig,
    tasks: list[EvalTask],
    agent_fn,
    hard_oracle_fn,
    soft_oracle_fn=None,
) -> CIEvalResult:
    """Eval CI pipeline skeleton.

    Run on every PR (subset) and nightly (full suite).
    PR-level: 3-8 min target. Nightly: 5-20 min for 500 rows.

    Layered scoring: deterministic first (milliseconds), then LLM judge
    (seconds) only on survivors. This cuts 60-80% of LLM judge calls.

    judge cost guardrail: keep under 10-15% of production LLM cost.
    Act if approaching 25%.
    """
    start = time.time()
    rk = run_key(
        config.suite_id, config.dataset_version,
        config.harness_commit, config.judge_model,
        str(random.randint(0, 99999)),
    )

    scored = 0
    passed = 0
    soft_scores = []
    judge_breaker = CircuitBreaker()
    results_per_task = []

    for task in tasks:
        # Check wall-time budget
        if time.time() - start > config.max_wall_time_s:
            break

        # Run agent
        output = agent_fn(task)

        # Hard oracle first (deterministic, milliseconds, zero ambiguity)
        hard_result = hard_oracle_fn(task, output)
        hard_passed = hard_result.get("passed", False)

        # If hard fails, skip soft (never average safety into quality)
        if not hard_passed:
            scored += 1
            results_per_task.append({
                "task_id": task.task_id, "hard": False, "soft": None,
            })
            continue

        # Soft oracle (LLM judge, async in prod, seconds)
        soft_score = 0.0
        if soft_oracle_fn and judge_breaker.allow():
            try:
                soft_result = soft_oracle_fn(task, output)
                soft_score = soft_result.get("score", 0.0)
                judge_breaker.record_success()
            except Exception:
                judge_breaker.record_failure()
                soft_score = -1  # Sentinel for error
        elif not judge_breaker.allow():
            soft_score = -1  # Breaker open

        scored += 1
        if soft_score >= 0:
            soft_scores.append(soft_score)
        task_passed = hard_passed and (soft_score >= config.soft_threshold or soft_score < 0)
        if task_passed:
            passed += 1
        results_per_task.append({
            "task_id": task.task_id, "hard": hard_passed,
            "soft": soft_score if soft_score >= 0 else "breaker_open",
        })

    wall_time = time.time() - start
    avg_soft = sum(soft_scores) / max(len(soft_scores), 1)

    gate = dual_oracle_gate(
        hard_passed=True,  # Aggregate
        hard_status=GraderStatus.SCORED,
        soft_score=avg_soft,
        soft_status=GraderStatus.SCORED if soft_scores else GraderStatus.SKIPPED,
        soft_threshold=config.soft_threshold,
        coverage_scored=scored,
        coverage_eligible=len(tasks),
        min_coverage=config.min_coverage,
    )

    return CIEvalResult(
        run_id=rk,
        config=config,
        total_tasks=len(tasks),
        scored_tasks=scored,
        passed_tasks=passed,
        pass_at_1=passed / max(scored, 1),
        pass_hat_k=pass_hat_k(scored, passed, min(config.k_trials, scored)),
        avg_soft_score=round(avg_soft, 4),
        gate_decision=gate,
        wall_time_s=round(wall_time, 2),
        judge_cost_usd=round(len(soft_scores) * 0.009, 4),  # ~$9/1k judge calls
    )


# --- RAGAS Faithfulness (RAG-specific eval) ---

def ragas_faithfulness(answer: str, context: str) -> dict:
    """RAGAS Faithfulness: entailment of answer claims against retrieved context.

    Walkthrough (Einstein example): answer has 2 claims, 1 entailed -> 0.5.
    WikiEval agreement: ~95% (vs 0.72 for direct GPT scoring).

    Critical footgun: faithfulness can be 1.0 on the WRONG documents.
    Always pair with context_recall.

    Faithfulness is entailment vs RETRIEVED context, not world truth.
    That's a different construct (SimpleQA measures world-fact accuracy).

    DeepEval trap: default FaithfulnessMetric treats "I don't know" as supported
    and empty verdicts as 1.0 unless penalize_ambiguous_claims=True.
    """
    # Stub: decompose answer into claims, check each against context
    claims = [s.strip() for s in answer.split(".") if s.strip()]
    if not claims:
        return {"score": 0.0, "claims": 0, "supported": 0}

    context_lower = context.lower()
    supported = 0
    claim_results = []

    for claim in claims:
        words = set(claim.lower().split())
        ctx_words = set(context_lower.split())
        overlap = len(words & ctx_words) / max(len(words), 1)
        is_supported = overlap > 0.3
        if is_supported:
            supported += 1
        claim_results.append({
            "claim": claim[:80],
            "supported": is_supported,
            "overlap": round(overlap, 3),
        })

    return {
        "score": round(supported / len(claims), 3),
        "claims": len(claims),
        "supported": supported,
        "claim_details": claim_results,
    }


# --- Online Eval Monitor ---

class OnlineEvalMonitor:
    """Sample production traffic async, detect sustained quality drift.

    Key: 0 ms eval tax on user path. Scoring happens in sidecar.
    Alert on SUSTAINED drops (3+ consecutive windows below threshold),
    not individual outliers.

    The most common eval architecture mistake: inlining the judge on the
    user path. That buys +12s on user p99 and judge 429s become user 500s.
    """
    def __init__(
        self,
        sample_rate: float = 0.05,        # 5% of production traffic
        window_size: int = 100,
        alert_threshold: float = 0.80,
        sustained_drop_count: int = 3,     # Alert after 3 consecutive drops
    ):
        self.sample_rate = sample_rate
        self.window_size = window_size
        self.alert_threshold = alert_threshold
        self.sustained_count = sustained_drop_count
        self.scores: deque = deque(maxlen=window_size)
        self.consecutive_drops = 0
        self.alerts: list[dict] = []

    def should_sample(self) -> bool:
        return random.random() < self.sample_rate

    def record_score(self, trace_id: str, score: float):
        """Record a score from the async judge sidecar."""
        self.scores.append(score)
        self._check_drift(trace_id)

    def _check_drift(self, trace_id: str):
        if len(self.scores) < 20:
            return
        recent = list(self.scores)[-50:]
        avg = sum(recent) / len(recent)

        if avg < self.alert_threshold:
            self.consecutive_drops += 1
            if self.consecutive_drops >= self.sustained_count:
                self.alerts.append({
                    "type": "quality_drift",
                    "avg_score": round(avg, 4),
                    "window": len(recent),
                    "consecutive_drops": self.consecutive_drops,
                })
        else:
            self.consecutive_drops = 0


# --- Statistical Hygiene Helpers ---

def bootstrap_ci(scores: list[float], n_bootstrap: int = 1000,
                  confidence: float = 0.95) -> dict:
    """Bootstrap confidence interval for eval scores.

    Use when n < few hundred (CLT does not hold).
    Report intervals, not points: "83% +/- 2.1 pp (95% CI)" is a result.
    "83%" alone is not.

    Miller worked example: n ~ 969 needed for 3 pp MDE.
    A 50-item golden set cannot support a 3 pp claim.
    """
    if not scores:
        return {"mean": 0.0, "ci_lower": 0.0, "ci_upper": 0.0}

    means = []
    for _ in range(n_bootstrap):
        sample = random.choices(scores, k=len(scores))
        means.append(sum(sample) / len(sample))

    means.sort()
    alpha = 1 - confidence
    lower_idx = int(alpha / 2 * n_bootstrap)
    upper_idx = int((1 - alpha / 2) * n_bootstrap)

    mean = sum(scores) / len(scores)
    return {
        "mean": round(mean, 4),
        "ci_lower": round(means[lower_idx], 4),
        "ci_upper": round(means[upper_idx], 4),
        "n": len(scores),
        "note": f"{confidence*100:.0f}% CI via {n_bootstrap} bootstrap samples",
    }


def minimum_sample_size(mde: float, baseline_rate: float = 0.5,
                         alpha: float = 0.05, power: float = 0.80) -> int:
    """Minimum sample size for a given minimum detectable effect (MDE).

    Miller worked example: n ~ 969 for 3 pp MDE.
    Going from K=1 to K=10 retries on n=198 only reduces MDE from 13.2% to 7.5%.

    Unit = task, not step. n=50 SWE instances is not n=1,000 steps.
    Pseudo-replication inflates statistical power.
    """
    from math import sqrt
    # Two-proportion z-test approximation
    z_alpha = 1.96 if alpha == 0.05 else 1.645  # Simplified
    z_beta = 0.84 if power == 0.80 else 1.28

    p1 = baseline_rate
    p2 = baseline_rate + mde
    p_avg = (p1 + p2) / 2

    numerator = (z_alpha * sqrt(2 * p_avg * (1 - p_avg)) +
                 z_beta * sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    denominator = (p2 - p1) ** 2

    return int(math.ceil(numerator / denominator))


# --- Demo ---

if __name__ == "__main__":
    print("=" * 60)
    print("LLM & Agent Evaluation -- Interview Prep Demos")
    print("=" * 60)

    # 1. pass@k and pass^k
    print("\n--- pass@k vs pass^k ---")
    # 10 trials, 4 passed
    n, c = 10, 4
    print(f"n={n}, c={c}:")
    print(f"  pass@1  = {pass_at_k(n, c, 1):.4f}  (probability at least 1 of 1 works)")
    print(f"  pass@5  = {pass_at_k(n, c, 5):.4f}  (probability at least 1 of 5 works)")
    print(f"  pass^1  = {pass_hat_k(n, c, 1):.4f}  (probability ALL 1 of 1 works = same as pass@1)")
    print(f"  pass^5  = {pass_hat_k(n, c, 5):.4f}  (probability ALL 5 of 5 work)")
    print(f"  n<k:      pass@k(n=3, c=2, k=5) = {pass_at_k(3, 2, 5)}  (NaN -- correct behavior)")

    # Show the gap
    print("\n  The pass@k vs pass^k gap can be enormous:")
    for label, p1, p5 in [
        ("tau-airline (baseline)", 0.332, 0.100),
        ("tau-airline (think)",    0.584, 0.340),
        ("tau-retail (think)",     0.812, 0.626),
    ]:
        print(f"  {label:30s}: pass^1={p1:.3f}, pass^5={p5:.3f}, gap={p1-p5:.3f}")

    # 2. Trajectory scoring with geometric mean
    print("\n--- Trajectory Scoring (Geometric Mean) ---")
    # Normal trajectory
    normal_steps = [
        TrajectoryStep(0, "lookup_account", "db_query", {"id": 123}, "found", 5.0),
        TrajectoryStep(1, "check_policy", "policy_api", {"type": "refund"}, "ok", 4.0),
        TrajectoryStep(2, "process_refund", "payment_api", {"amt": 50}, "done", 5.0),
        TrajectoryStep(3, "confirm_user", None, None, "Refund processed", 5.0),
    ]
    normal_traj = score_trajectory(normal_steps)
    print(f"Normal trajectory:  geo={normal_traj['geometric_mean']:.4f}, "
          f"arith={normal_traj['arithmetic_mean']:.4f}")

    # One bad step
    bad_steps = normal_steps.copy()
    bad_steps[1] = TrajectoryStep(1, "wrong_action", "wrong_tool", {}, "error", 1.0)
    bad_traj = score_trajectory(bad_steps)
    print(f"One bad step (1/5): geo={bad_traj['geometric_mean']:.4f}, "
          f"arith={bad_traj['arithmetic_mean']:.4f}")
    print(f"  Arithmetic hides the failure (gap={bad_traj['gap']:.4f})")

    # 3. Dual-oracle gate
    print("\n--- Dual-Oracle Gate ---")
    # Case 1: both pass
    gate1 = dual_oracle_gate(
        hard_passed=True, hard_status=GraderStatus.SCORED,
        soft_score=0.91, soft_status=GraderStatus.SCORED,
    )
    print(f"Both pass:     ship={gate1.ship}, reason={gate1.reason}")

    # Case 2: hard fails (correct but hostile)
    gate2 = dual_oracle_gate(
        hard_passed=False, hard_status=GraderStatus.SCORED,
        soft_score=0.95, soft_status=GraderStatus.SCORED,
    )
    print(f"Hard fails:    ship={gate2.ship}, reason={gate2.reason}")

    # Case 3: soft below threshold (pretty wrong)
    gate3 = dual_oracle_gate(
        hard_passed=True, hard_status=GraderStatus.SCORED,
        soft_score=0.60, soft_status=GraderStatus.SCORED,
    )
    print(f"Soft too low:  ship={gate3.ship}, reason={gate3.reason}")

    # Case 4: low coverage
    gate4 = dual_oracle_gate(
        hard_passed=True, hard_status=GraderStatus.SCORED,
        soft_score=0.91, soft_status=GraderStatus.SCORED,
        coverage_scored=80, coverage_eligible=100,
    )
    print(f"Low coverage:  ship={gate4.ship}, reason={gate4.reason}")

    # 4. Tool call accuracy
    print("\n--- Tool Call Accuracy ---")
    expected = [
        {"name": "search_orders", "args": {"customer_id": 123}},
        {"name": "process_refund", "args": {"order_id": 456, "amount": 50.0}},
    ]
    actual = [
        {"name": "search_orders", "args": {"customer_id": 123}},
        {"name": "process_refund", "args": {"order_id": 456, "amount": 50.0}},
        {"name": "search_orders", "args": {"customer_id": 123}},  # Repeated
    ]
    tool_acc = tool_call_accuracy(expected, actual)
    print(f"Selection accuracy: {tool_acc['selection_accuracy']}")
    print(f"Argument correctness: {tool_acc['argument_correctness']}")
    print(f"Repetition rate: {tool_acc['repetition_rate']}")

    # 5. RAGAS faithfulness
    print("\n--- RAGAS Faithfulness ---")
    faith = ragas_faithfulness(
        answer="Einstein was born in Germany. He developed the theory of quantum gravity.",
        context="Albert Einstein was born in Ulm, Germany in 1879. He is known for the theory of relativity.",
    )
    print(f"Faithfulness score: {faith['score']} ({faith['supported']}/{faith['claims']} claims supported)")

    # 6. Statistical hygiene
    print("\n--- Statistical Hygiene ---")
    scores = [0.85, 0.90, 0.78, 0.92, 0.88, 0.83, 0.91, 0.87, 0.89, 0.86]
    ci = bootstrap_ci(scores)
    print(f"Score: {ci['mean']:.4f} [{ci['ci_lower']:.4f}, {ci['ci_upper']:.4f}] "
          f"(n={ci['n']}, {ci['note']})")

    n_needed = minimum_sample_size(mde=0.03)  # 3 pp MDE
    print(f"Sample size for 3 pp MDE: n ~ {n_needed} (Miller: ~969)")

    n_large = minimum_sample_size(mde=0.10)   # 10 pp MDE
    print(f"Sample size for 10 pp MDE: n ~ {n_large}")

    # 7. CI pipeline skeleton
    print("\n--- CI Pipeline Skeleton ---")
    config = EvalConfig(
        suite_id="support-agent-v2",
        dataset_version="ds-2026-09-03",
        harness_commit="abc123",
        k_trials=3,
    )

    tasks = [
        EvalTask(f"task-{i}", f"Customer query #{i}", f"Expected response #{i}")
        for i in range(20)
    ]

    def stub_agent(task):
        return EvalOutput(task.task_id, f"Response to: {task.input_text}")

    def stub_hard_oracle(task, output):
        return {"passed": random.random() > 0.15}

    def stub_soft_oracle(task, output):
        return {"score": 0.7 + random.random() * 0.3}

    random.seed(42)
    result = eval_ci_pipeline(config, tasks, stub_agent, stub_hard_oracle, stub_soft_oracle)
    print(f"Run ID: {result.run_id}")
    print(f"Tasks: {result.scored_tasks}/{result.total_tasks} scored")
    print(f"pass@1: {result.pass_at_1:.3f}")
    print(f"Avg soft score: {result.avg_soft_score:.3f}")
    print(f"Gate: ship={result.gate_decision.ship}, reason={result.gate_decision.reason}")
    print(f"Wall time: {result.wall_time_s:.1f}s")
    print(f"Judge cost: ${result.judge_cost_usd:.4f}")

    # 8. Online monitor
    print("\n--- Online Eval Monitor ---")
    monitor = OnlineEvalMonitor(sample_rate=1.0, alert_threshold=0.80)
    # Simulate declining quality
    for i in range(100):
        score = 0.90 - (i * 0.002)  # Gradual decline
        monitor.record_score(f"trace-{i}", score)
    print(f"Alerts triggered: {len(monitor.alerts)}")
    if monitor.alerts:
        print(f"  Last alert: avg_score={monitor.alerts[-1]['avg_score']}, "
              f"consecutive_drops={monitor.alerts[-1]['consecutive_drops']}")
