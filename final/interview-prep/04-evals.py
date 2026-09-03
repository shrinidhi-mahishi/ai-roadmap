"""
LLM & Agent Evaluation - Code Examples

Covers: Production eval pipelines, trajectory scoring (geometric mean),
online eval monitoring with drift detection, dual-oracle evaluation
(hard + soft), pass@k and pass^k metrics, LLM-as-judge with bias
mitigation, CI integration for agent evals, checkpointing, PII
redaction, RAG faithfulness evaluation, citation checking, and
interview Q&A examples.

Source: 04-evals.md
"""

# --- Shared imports across all code blocks ---
import json
import os
import random
import re
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from math import comb
from typing import List, Dict, Any, Optional, Callable

import numpy as np


# --- Section: Production-Grade Eval Pipeline (Layered Scoring) ---

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import numpy as np
from enum import Enum

class TrajectoryStep:
    """Single step in agent execution"""
    def __init__(self, tool: str, args: Dict[str, Any], output: Any,
                 duration_ms: int, error: Optional[str] = None):
        self.tool = tool
        self.args = args
        self.output = output
        self.duration_ms = duration_ms
        self.error = error

class ScoreMode(Enum):
    GEOMETRIC_MEAN = "geometric"
    ARITHMETIC_MEAN = "arithmetic"
    MINIMUM = "minimum"
    WEIGHTED = "weighted"

@dataclass
class EvalResult:
    task_id: str
    task_success: bool
    trajectory_score: float
    cost_usd: float
    latency_ms: int
    steps: List[TrajectoryStep]
    judge_feedback: Optional[str] = None

class EvalPipeline:
    """
    Multi-layer eval pipeline with hard + soft oracle.

    Layers:
      1. Task success (binary)
      2. Trajectory quality (geometric mean of step scores)
      3. Component-level (tool selection, args, recovery)
    """

    def __init__(self, judge_model: str = "claude-opus-5",
                 score_mode: ScoreMode = ScoreMode.GEOMETRIC_MEAN):
        self.judge_model = judge_model
        self.score_mode = score_mode
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            timeout_seconds=30,
            half_open_after=60
        )

    def evaluate_task(self, task_id: str, agent_output: Any,
                     expected_output: Any, trajectory: List[TrajectoryStep],
                     cost_usd: float, latency_ms: int) -> EvalResult:
        """
        Evaluate a single task across all layers.
        """
        # Layer 1: Task success (hard oracle)
        task_success = self._check_task_success(agent_output, expected_output)

        # Layer 2: Trajectory quality
        step_scores = [self._score_step(step) for step in trajectory]
        trajectory_score = self._aggregate_scores(step_scores)

        # Layer 3: LLM judge (soft oracle) - only if needed
        judge_feedback = None
        if not task_success and trajectory_score > 0.7:
            # High trajectory quality but wrong answer - get judge insight
            judge_feedback = self._invoke_judge(trajectory, agent_output, expected_output)

        return EvalResult(
            task_id=task_id,
            task_success=task_success,
            trajectory_score=trajectory_score,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            steps=trajectory,
            judge_feedback=judge_feedback
        )

    def _check_task_success(self, agent_output: Any, expected: Any) -> bool:
        """Hard oracle: exact match or semantic equivalence"""
        if isinstance(expected, str) and isinstance(agent_output, str):
            # Normalize whitespace, case
            return agent_output.strip().lower() == expected.strip().lower()
        return agent_output == expected

    def _score_step(self, step: TrajectoryStep) -> float:
        """
        Score individual step on [0, 1].

        Criteria:
          - Tool selection: 0.4
          - Argument correctness: 0.4
          - No error: 0.2
        """
        score = 0.0

        # Tool selection (mock - in practice, check against expected)
        if step.tool in ["search", "read", "write", "execute"]:
            score += 0.4

        # Argument correctness (mock - in practice, validate schema)
        if step.args and all(v is not None for v in step.args.values()):
            score += 0.4

        # No error
        if step.error is None:
            score += 0.2

        return min(score, 1.0)

    def _aggregate_scores(self, scores: List[float]) -> float:
        """Aggregate step scores into trajectory score"""
        if not scores:
            return 0.0

        if self.score_mode == ScoreMode.GEOMETRIC_MEAN:
            # Geometric mean: (s1 * s2 * ... * sn)^(1/n)
            product = np.prod(scores)
            return product ** (1.0 / len(scores))

        elif self.score_mode == ScoreMode.ARITHMETIC_MEAN:
            return np.mean(scores)

        elif self.score_mode == ScoreMode.MINIMUM:
            return min(scores)

        elif self.score_mode == ScoreMode.WEIGHTED:
            # Last step (final answer) gets 50% weight, rest split equally
            weights = [1.0] * len(scores)
            weights[-1] = len(scores)  # 50% to last step
            return np.average(scores, weights=weights)

        return 0.0

    def _invoke_judge(self, trajectory: List[TrajectoryStep],
                     agent_output: Any, expected: Any) -> str:
        """
        LLM-as-judge for qualitative feedback.

        Uses circuit breaker to prevent cascading failures.
        """
        if not self.circuit_breaker.allow_request():
            return "Judge unavailable (circuit breaker open)"

        try:
            prompt = self._build_judge_prompt(trajectory, agent_output, expected)
            # Mock API call - in practice, call Claude API
            response = self._call_judge_api(prompt)
            self.circuit_breaker.record_success()
            return response
        except Exception as e:
            self.circuit_breaker.record_failure()
            return f"Judge error: {str(e)}"

    def _build_judge_prompt(self, trajectory: List[TrajectoryStep],
                           agent_output: Any, expected: Any) -> str:
        """Build prompt for LLM judge"""
        trajectory_str = "\n".join([
            f"Step {i+1}: {step.tool}({step.args}) -> {step.output}"
            for i, step in enumerate(trajectory)
        ])

        return f"""Evaluate this agent trajectory.

Expected output: {expected}
Agent output: {agent_output}

Trajectory:
{trajectory_str}

Score on:
1. Tool selection accuracy (were the right tools chosen?)
2. Reasoning coherence (did steps follow logically?)
3. Error handling (were failures recovered gracefully?)

Provide a 1-paragraph assessment and a score 1-5."""

    def _call_judge_api(self, prompt: str) -> str:
        """Mock judge API call"""
        # In production: client.messages.create(model=self.judge_model, ...)
        return "Mock judge feedback: trajectory quality is high but final answer is off by one."


class CircuitBreaker:
    """
    Circuit breaker for LLM judge calls.

    States:
      CLOSED: Normal operation
      OPEN: Too many failures, reject all requests
      HALF_OPEN: Test if service recovered
    """

    def __init__(self, failure_threshold: int, timeout_seconds: int,
                 half_open_after: int):
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.half_open_after = half_open_after
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = "CLOSED"

    def allow_request(self) -> bool:
        """Check if request should be allowed"""
        import time
        now = time.time()

        if self.state == "OPEN":
            if now - self.last_failure_time > self.half_open_after:
                self.state = "HALF_OPEN"
                return True
            return False

        return True

    def record_success(self):
        """Reset failure count on success"""
        self.failure_count = 0
        self.state = "CLOSED"

    def record_failure(self):
        """Increment failure count, open circuit if threshold exceeded"""
        import time
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self.state = "OPEN"


class LLMJudge:
    """
    LLM-as-judge with retry logic and fallback.

    Features:
      - Exponential backoff retry
      - Fallback to cheaper model if primary fails
      - Prompt caching for repeated rubrics
    """

    def __init__(self, primary_model: str = "claude-opus-5",
                 fallback_model: str = "claude-sonnet-4.5"):
        self.primary_model = primary_model
        self.fallback_model = fallback_model
        self.max_retries = 3

    def score(self, output: str, rubric: str, reference: Optional[str] = None) -> Dict[str, Any]:
        """
        Score output against rubric.

        Returns:
          {
            "score": float (0-1),
            "reasoning": str,
            "model_used": str
          }
        """
        # Try primary model with retries
        for attempt in range(self.max_retries):
            try:
                result = self._call_model(self.primary_model, output, rubric, reference)
                result["model_used"] = self.primary_model
                return result
            except Exception as e:
                if attempt == self.max_retries - 1:
                    # Fallback to cheaper model
                    try:
                        result = self._call_model(self.fallback_model, output, rubric, reference)
                        result["model_used"] = self.fallback_model
                        return result
                    except Exception as fallback_error:
                        return {
                            "score": 0.0,
                            "reasoning": f"Judge failed: {str(fallback_error)}",
                            "model_used": "none"
                        }
                # Exponential backoff
                time.sleep(2 ** attempt)

        return {"score": 0.0, "reasoning": "Max retries exceeded", "model_used": "none"}

    def _call_model(self, model: str, output: str, rubric: str,
                   reference: Optional[str]) -> Dict[str, Any]:
        """Mock model call - in production, use Anthropic SDK"""
        # Build prompt with caching
        system_prompt = f"""You are an expert evaluator. Score the output against this rubric:

{rubric}

Output a JSON object:
{{
  "score": <float 0-1>,
  "reasoning": "<1-2 sentence explanation>"
}}"""

        user_prompt = f"""Output to evaluate:
{output}
"""

        if reference:
            user_prompt += f"\nReference (expected output):\n{reference}\n"

        # Mock response
        return {
            "score": 0.85,
            "reasoning": "Output is clear and mostly correct, minor formatting issue."
        }


# --- Section: Trajectory Step Scorer with Geometric Mean ---

from typing import List
import numpy as np

class TrajectoryStepScore:
    """Individual step evaluation"""
    def __init__(self, tool_correct: bool, args_correct: bool,
                 output_valid: bool, error_handled: bool):
        self.tool_correct = tool_correct
        self.args_correct = args_correct
        self.output_valid = output_valid
        self.error_handled = error_handled

    def compute(self) -> float:
        """
        Weighted score for this step.

        Weights:
          - Tool selection: 0.3
          - Argument correctness: 0.3
          - Output validity: 0.3
          - Error handling: 0.1
        """
        score = 0.0
        if self.tool_correct:
            score += 0.3
        if self.args_correct:
            score += 0.3
        if self.output_valid:
            score += 0.3
        if self.error_handled:
            score += 0.1
        return score

def score_trajectory_geometric(steps: List[TrajectoryStepScore]) -> float:
    """
    Score trajectory using geometric mean.

    Geometric mean ensures that a single failing step
    significantly impacts the overall score.

    Example:
      steps = [1.0, 1.0, 0.5, 1.0, 1.0]
      arithmetic mean = 0.9
      geometric mean = 0.87 (penalizes the 0.5 more)
    """
    if not steps:
        return 0.0

    scores = [step.compute() for step in steps]

    # Geometric mean: (s1 * s2 * ... * sn)^(1/n)
    product = np.prod(scores)
    return float(product ** (1.0 / len(scores)))

# Worked example
steps = [
    TrajectoryStepScore(tool_correct=True, args_correct=True, output_valid=True, error_handled=True),
    TrajectoryStepScore(tool_correct=True, args_correct=False, output_valid=True, error_handled=True),
    TrajectoryStepScore(tool_correct=True, args_correct=True, output_valid=True, error_handled=True),
    TrajectoryStepScore(tool_correct=True, args_correct=True, output_valid=True, error_handled=True),
    TrajectoryStepScore(tool_correct=True, args_correct=True, output_valid=True, error_handled=True),
]

# Step scores: [1.0, 0.7, 1.0, 1.0, 1.0]
# Geometric mean: (1.0 * 0.7 * 1.0 * 1.0 * 1.0)^(1/5) = 0.7^0.2 = 0.937
traj_score = score_trajectory_geometric(steps)
print(f"Trajectory score: {traj_score:.3f}")  # 0.937


# --- Section: Online Eval Monitor with Drift Detection ---

from collections import deque
from dataclasses import dataclass
import time
import numpy as np

@dataclass
class OnlineEvalSample:
    """Single production sample"""
    request_id: str
    timestamp: float
    task_success: bool
    trajectory_score: float
    cost_usd: float
    latency_ms: int

class OnlineEvalMonitor:
    """
    Sliding-window online evaluation for production traffic.

    Features:
      - Drift detection (compare current window vs baseline)
      - Alerting on regression
      - Sampling (don't eval every request)
    """

    def __init__(self, window_size: int = 1000, sample_rate: float = 0.1,
                 drift_threshold: float = 0.05):
        self.window_size = window_size
        self.sample_rate = sample_rate
        self.drift_threshold = drift_threshold

        # Sliding window of recent samples
        self.samples = deque(maxlen=window_size)

        # Baseline metrics (from initial eval)
        self.baseline_pass_rate = 0.0
        self.baseline_avg_score = 0.0

        # Alert state
        self.alert_active = False

    def set_baseline(self, pass_rate: float, avg_score: float):
        """Set baseline metrics from pre-deployment eval"""
        self.baseline_pass_rate = pass_rate
        self.baseline_avg_score = avg_score

    def should_sample(self) -> bool:
        """Decide whether to evaluate this request (sampling)"""
        import random
        return random.random() < self.sample_rate

    def record(self, sample: OnlineEvalSample):
        """Record a new evaluation sample"""
        self.samples.append(sample)

        # Check for drift if window is full
        if len(self.samples) >= self.window_size:
            self._check_drift()

    def _check_drift(self):
        """
        Detect drift: current window vs baseline.

        Alert if:
          - Pass rate drops > drift_threshold
          - Average trajectory score drops > drift_threshold
        """
        current_pass_rate = np.mean([s.task_success for s in self.samples])
        current_avg_score = np.mean([s.trajectory_score for s in self.samples])

        pass_rate_delta = self.baseline_pass_rate - current_pass_rate
        score_delta = self.baseline_avg_score - current_avg_score

        if pass_rate_delta > self.drift_threshold or score_delta > self.drift_threshold:
            if not self.alert_active:
                self._trigger_alert(current_pass_rate, current_avg_score)
                self.alert_active = True
        else:
            self.alert_active = False

    def _trigger_alert(self, current_pass_rate: float, current_avg_score: float):
        """Send alert (Slack, PagerDuty, etc.)"""
        message = f"""EVAL DRIFT ALERT

Baseline pass rate: {self.baseline_pass_rate:.2%}
Current pass rate: {current_pass_rate:.2%}
Delta: {self.baseline_pass_rate - current_pass_rate:.2%}

Baseline avg score: {self.baseline_avg_score:.3f}
Current avg score: {current_avg_score:.3f}
Delta: {self.baseline_avg_score - current_avg_score:.3f}

Window size: {len(self.samples)} samples
"""
        print(message)  # In production: send to alerting system

    def get_stats(self) -> Dict[str, Any]:
        """Get current window statistics"""
        if not self.samples:
            return {}

        return {
            "window_size": len(self.samples),
            "pass_rate": np.mean([s.task_success for s in self.samples]),
            "avg_trajectory_score": np.mean([s.trajectory_score for s in self.samples]),
            "p50_latency_ms": np.percentile([s.latency_ms for s in self.samples], 50),
            "p95_latency_ms": np.percentile([s.latency_ms for s in self.samples], 95),
            "p99_latency_ms": np.percentile([s.latency_ms for s in self.samples], 99),
            "avg_cost_usd": np.mean([s.cost_usd for s in self.samples]),
            "alert_active": self.alert_active
        }

# Usage
monitor = OnlineEvalMonitor(window_size=1000, sample_rate=0.1, drift_threshold=0.05)
monitor.set_baseline(pass_rate=0.85, avg_score=0.92)

# In production request handler
if monitor.should_sample():
    # Evaluate this request
    sample = OnlineEvalSample(
        request_id="req_123",
        timestamp=time.time(),
        task_success=True,
        trajectory_score=0.88,
        cost_usd=0.02,
        latency_ms=1500
    )
    monitor.record(sample)

# Periodic stats check
stats = monitor.get_stats()
print(f"Current window: {stats}")


# --- Section: Dual-Oracle Eval Runtime (Hard + Soft) ---

from typing import Callable, Any, Optional, List
from enum import Enum
import time

class OracleType(Enum):
    HARD = "hard"  # Exact match, deterministic
    SOFT = "soft"  # LLM judge, probabilistic

class EvalInvariant(Enum):
    """Eval design invariants"""
    I1 = "Orthogonal planes: harness _|_ production _|_ online"
    I2 = "Judge calls <15% of production LLM cost"
    I3 = "Eval suite passes on release candidate before deploy"
    I4 = "Every failure mode has a test case"

@dataclass
class OracleResult:
    """Result from an oracle (hard or soft)"""
    oracle_type: OracleType
    passed: bool
    score: Optional[float] = None  # For soft oracle
    reasoning: Optional[str] = None
    cost_usd: float = 0.0
    latency_ms: int = 0

class EvalRuntime:
    """
    Dual-oracle eval runtime.

    Enforces invariants:
      I1: Separate harness from production
      I2: Judge cost <15% of production cost
      I3: Release gate on pass rate
      I4: Coverage of known failure modes
    """

    def __init__(self,
                 hard_oracle: Callable[[Any, Any], bool],
                 soft_oracle: Optional[Callable[[Any, str], OracleResult]] = None,
                 pass_threshold: float = 0.80,
                 judge_cost_limit_pct: float = 0.15):
        self.hard_oracle = hard_oracle
        self.soft_oracle = soft_oracle
        self.pass_threshold = pass_threshold
        self.judge_cost_limit_pct = judge_cost_limit_pct

        # Metrics
        self.production_cost_usd = 0.0
        self.judge_cost_usd = 0.0

    def evaluate(self, agent_output: Any, expected: Any,
                 rubric: Optional[str] = None) -> OracleResult:
        """
        Run dual-oracle evaluation.

        Flow:
          1. Hard oracle (fast, deterministic)
          2. If hard oracle passes, return
          3. If hard oracle fails, invoke soft oracle (if available)
        """
        start = time.time()

        # Hard oracle
        hard_passed = self.hard_oracle(agent_output, expected)

        if hard_passed:
            return OracleResult(
                oracle_type=OracleType.HARD,
                passed=True,
                latency_ms=int((time.time() - start) * 1000)
            )

        # Soft oracle (if needed)
        if self.soft_oracle and rubric:
            soft_result = self.soft_oracle(agent_output, rubric)
            self.judge_cost_usd += soft_result.cost_usd

            # Check I2: judge cost limit
            if self.production_cost_usd > 0:
                judge_ratio = self.judge_cost_usd / self.production_cost_usd
                if judge_ratio > self.judge_cost_limit_pct:
                    raise Exception(f"Judge cost {judge_ratio:.1%} exceeds limit {self.judge_cost_limit_pct:.1%}")

            return soft_result

        # No soft oracle, hard oracle failed
        return OracleResult(
            oracle_type=OracleType.HARD,
            passed=False,
            latency_ms=int((time.time() - start) * 1000)
        )

    def evaluate_suite(self, test_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Run full eval suite and enforce I3 (release gate).

        Returns pass/fail decision + metrics.
        """
        results = []

        for case in test_cases:
            result = self.evaluate(
                agent_output=case["agent_output"],
                expected=case["expected"],
                rubric=case.get("rubric")
            )
            results.append(result)

        # Aggregate
        pass_count = sum(1 for r in results if r.passed)
        pass_rate = pass_count / len(results)

        # I3: Release gate
        release_approved = pass_rate >= self.pass_threshold

        return {
            "total": len(results),
            "passed": pass_count,
            "pass_rate": pass_rate,
            "release_approved": release_approved,
            "threshold": self.pass_threshold,
            "judge_cost_usd": self.judge_cost_usd,
            "production_cost_usd": self.production_cost_usd
        }

# Example: Hard oracle (exact match)
def exact_match_oracle(output: Any, expected: Any) -> bool:
    if isinstance(output, str) and isinstance(expected, str):
        return output.strip().lower() == expected.strip().lower()
    return output == expected

# Example: Soft oracle (LLM judge)
def llm_judge_oracle(output: Any, rubric: str) -> OracleResult:
    # Mock - in production, call LLM API
    return OracleResult(
        oracle_type=OracleType.SOFT,
        passed=True,
        score=0.88,
        reasoning="Output is mostly correct, minor style issue",
        cost_usd=0.01,
        latency_ms=800
    )

# Usage
runtime = EvalRuntime(
    hard_oracle=exact_match_oracle,
    soft_oracle=llm_judge_oracle,
    pass_threshold=0.80,
    judge_cost_limit_pct=0.15
)

test_cases = [
    {"agent_output": "Paris", "expected": "Paris"},
    {"agent_output": "paris", "expected": "Paris"},  # Case mismatch
    {"agent_output": "London", "expected": "Paris", "rubric": "Geographic accuracy"},
]

suite_result = runtime.evaluate_suite(test_cases)
print(f"Release approved: {suite_result['release_approved']}")
print(f"Pass rate: {suite_result['pass_rate']:.1%}")


# --- Section: pass@k and pass^k Implementation ---

from math import comb
import numpy as np

def pass_at_k(n: int, c: int, k: int) -> float:
    """
    Unbiased estimator for pass@k.

    Args:
        n: Total samples drawn per task
        c: Number of correct samples
        k: Target number of samples (k <= n)

    Returns:
        Probability that at least one of k samples is correct

    Formula:
        pass@k = 1 - C(n-c, k) / C(n, k)

    Example:
        n=10, c=3, k=5
        pass@5 = 1 - C(7,5)/C(10,5) = 1 - 21/252 = 0.917
    """
    if c >= k:
        return 1.0
    if c == 0:
        return 0.0
    if k > n:
        raise ValueError(f"k ({k}) cannot exceed n ({n})")

    return 1.0 - comb(n - c, k) / comb(n, k)

def pass_at_k_stable(n: int, c: int, k: int) -> float:
    """
    Numerically stable pass@k using product form.

    Avoids large intermediate values in binomial coefficients.
    """
    if c >= k:
        return 1.0
    if c == 0:
        return 0.0

    # Product form: 1 - prod(i=0 to k-1) [(n-c-i) / (n-i)]
    product = 1.0
    for i in range(k):
        product *= (n - c - i) / (n - i)

    return 1.0 - product

def pass_pow_k(n: int, c: int, k: int) -> float:
    """
    pass^k: Probability that all k samples are correct.

    Args:
        n: Total samples drawn
        c: Number of correct samples (must be >= k)
        k: Target number of samples

    Returns:
        Probability of k consecutive successes

    Formula:
        pass^k = C(c, k) / C(n, k)

    Example:
        n=10, c=8, k=3
        pass^3 = C(8,3)/C(10,3) = 56/120 = 0.467
    """
    if c < k:
        return 0.0
    if c == n:
        return 1.0

    return comb(c, k) / comb(n, k)

# Demonstration: HumanEval-style analysis
def analyze_pass_metrics(n: int, c: int, max_k: int = 10):
    """
    Compute pass@k and pass^k for k=1 to max_k.

    Shows how metrics diverge as k increases.
    """
    print(f"\nAnalysis for n={n}, c={c} correct samples\n")
    print("k | pass@k | pass^k | Gap")
    print("-" * 40)

    for k in range(1, min(max_k + 1, n + 1)):
        at_k = pass_at_k_stable(n, c, k)
        pow_k = pass_pow_k(n, c, k)
        gap = at_k - pow_k
        print(f"{k:2d} | {at_k:6.2%} | {pow_k:6.2%} | {gap:+6.2%}")

# Example: Model with 60% single-shot accuracy
analyze_pass_metrics(n=10, c=6, max_k=10)

# Example: Anthropic tau-airline replication
print("\nAnthropic tau-airline scenario (baseline):")
analyze_pass_metrics(n=10, c=3, max_k=5)  # ~33% pass@1


# --- Section: Back-Pressure Design (EvalQueue) ---

class EvalQueue:
    def __init__(self, max_queue_size: int = 10000):
        self.queue = []
        self.max_queue_size = max_queue_size
        self.dropped_count = 0

    def enqueue(self, trace):
        if len(self.queue) < self.max_queue_size:
            self.queue.append(trace)
        else:
            self.dropped_count += 1
            # Optionally: reservoir sampling to maintain random sample

    def drop_rate(self) -> float:
        total = len(self.queue) + self.dropped_count
        return self.dropped_count / total if total > 0 else 0.0


# --- Section: LLM-as-Judge Biases - Position Swap Debiasing ---

def pairwise_judge_debiased(response_a: str, response_b: str, rubric: str) -> str:
    """Run pairwise comparison twice with swapped order, aggregate"""
    # Forward
    score_forward = judge(f"A: {response_a}\nB: {response_b}\n{rubric}")
    # Reverse
    score_reverse = judge(f"A: {response_b}\nB: {response_a}\n{rubric}")

    # If both agree, return
    if score_forward == "A" and score_reverse == "B":
        return "A_wins"
    elif score_forward == "B" and score_reverse == "A":
        return "B_wins"
    else:
        return "Tie"  # Disagreement due to position bias


# --- Section: Chain-of-Thought for Judge Reliability ---

def judge_with_cot(output: str, rubric: str) -> Dict[str, Any]:
    prompt = f"""Evaluate this output against the rubric.

Output:
{output}

Rubric:
{rubric}

First, provide your reasoning (2-3 sentences). Then, provide a score 1-5.

Format:
Reasoning: <your reasoning>
Score: <1-5>
"""

    response = llm(prompt)
    # Parse reasoning and score
    reasoning = extract_reasoning(response)
    score = extract_score(response)

    return {"score": score, "reasoning": reasoning}


# --- Section: SimpleQA as Refusal-Aware Hard-Gate Pattern ---

def score_with_refusal(agent_output: str, expected: str, is_answerable: bool) -> float:
    if not is_answerable:
        # Should refuse
        if "I don't know" in agent_output or "Cannot answer" in agent_output:
            return 1.0
        else:
            return 0.0  # Hallucination
    else:
        # Should answer correctly
        if agent_output == expected:
            return 1.0
        elif "I don't know" in agent_output:
            return 0.0  # False refusal
        else:
            return 0.0  # Wrong answer


# --- Section: Complexity of k-Sample Evals (Stratified pass@k) ---

def stratified_pass_at_k(pass_at_1: float, k: int) -> float:
    """
    Estimate pass@k from pass@1 assuming independence.

    pass@k ~= 1 - (1 - pass@1)^k

    Caveat: Assumes samples are independent (often false for agents)
    """
    return 1 - (1 - pass_at_1) ** k

# Example: pass@1 = 0.3, estimate pass@10
# pass@10 ~= 1 - (1 - 0.3)^10 = 1 - 0.028 = 0.972


# --- Section: Flaky CI Mitigations ---

def ci_eval_task(task_id: str, agent: Agent, k: int = 3) -> bool:
    """
    CI eval: Pass if any of k runs succeeds.

    Tolerates flakiness while catching regressions.
    """
    successes = 0
    for i in range(k):
        result = agent.run(task_id, temperature=0, seed=i)
        if result.success:
            successes += 1

    # Pass if at least 1 success
    return successes >= 1


# --- Section: Online Eval Resilience - Sampling ---

import random

def should_evaluate() -> bool:
    return random.random() < 0.1  # 10% sample rate


# --- Section: Online Eval Resilience - Drift Detection ---

class DriftDetector:
    def __init__(self, window_size: int = 1000, threshold: float = 0.05):
        self.window = deque(maxlen=window_size)
        self.baseline_mean = 0.0
        self.threshold = threshold

    def record(self, score: float):
        self.window.append(score)
        if len(self.window) >= self.window_size:
            current_mean = np.mean(self.window)
            if abs(current_mean - self.baseline_mean) > self.threshold:
                alert("Drift detected!")


# --- Section: Online Eval Resilience - Judge Consistency Check ---

def judge_consistency_check(trace, rubric, n=3) -> float:
    scores = [judge(trace, rubric) for _ in range(n)]
    std = np.std(scores)
    return std  # Should be <0.1 for consistent judge


# --- Section: Checkpointing for Long-Running Suites ---

import json
import os

class CheckpointedEvalRunner:
    def __init__(self, checkpoint_file: str = "eval_checkpoint.json"):
        self.checkpoint_file = checkpoint_file
        self.completed_tasks = self._load_checkpoint()

    def _load_checkpoint(self) -> set:
        if os.path.exists(self.checkpoint_file):
            with open(self.checkpoint_file) as f:
                return set(json.load(f)["completed"])
        return set()

    def _save_checkpoint(self):
        with open(self.checkpoint_file, "w") as f:
            json.dump({"completed": list(self.completed_tasks)}, f)

    def run_suite(self, tasks: List[str]):
        for task_id in tasks:
            if task_id in self.completed_tasks:
                continue  # Skip already completed

            result = evaluate_task(task_id)
            self.completed_tasks.add(task_id)
            self._save_checkpoint()  # Checkpoint after each task


# --- Section: PII in Evaluation Data - Redaction ---

import re

def redact_pii(text: str) -> str:
    # Email
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '<EMAIL>', text)
    # SSN
    text = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '<SSN>', text)
    # Phone
    text = re.sub(r'\b\d{3}-\d{3}-\d{4}\b', '<PHONE>', text)
    return text


# --- Section: Scenario C - Dual-Oracle for Policy-Bound Support Agent (Hard Oracle) ---

def policy_oracle(response: str, context: Dict) -> bool:
    """Hard oracle: policy compliance"""
    # Extract agent actions
    refund_amount = extract_refund_amount(response)
    eligibility_checked = check_eligibility_verified(response, context)

    # Policy rules
    if refund_amount > context["order_total"]:
        return False  # Cannot refund more than order total

    if refund_amount > 0 and not eligibility_checked:
        return False  # Must check eligibility before refunding

    if context["account_age_days"] < 30 and not escalated(response):
        return False  # New accounts must be escalated

    return True


# --- Section: Scenario C - Dual-Oracle for Policy-Bound Support Agent (Soft Oracle) ---

def helpfulness_oracle(response: str) -> float:
    """Soft oracle: helpfulness score 0-1"""
    rubric = """Score the response on:
    1. Helpfulness (addresses user concern)
    2. Empathy (acknowledges frustration)
    3. Clarity (easy to understand)

    Return a score 0-1."""

    return llm_judge(response, rubric)


# --- Section: Scenario C - Dual-Oracle for Policy-Bound Support Agent (Aggregation) ---

def dual_oracle_decision(response: str, context: Dict) -> Dict[str, Any]:
    hard_pass = policy_oracle(response, context)
    soft_score = helpfulness_oracle(response)

    if not hard_pass:
        return {"decision": "BLOCK", "reason": "Policy violation"}

    if soft_score < 0.9:
        return {"decision": "WARN", "reason": f"Low helpfulness: {soft_score:.2f}"}

    return {"decision": "PASS"}


# --- Section: Scenario D - RAG Faithfulness CI + Citation Eval ---

def citation_oracle(answer: str, retrieved_docs: List[str]) -> float:
    """
    Check citation correctness.

    Returns:
      1.0 if all citations are correct
      0.0 if any citation is incorrect
    """
    citations = extract_citations(answer)  # e.g., [1], [2], [3]

    for citation_num in citations:
        if citation_num > len(retrieved_docs):
            return 0.0  # Citation out of range

        # Extract claim associated with citation
        claim = extract_claim_for_citation(answer, citation_num)

        # Check if claim is in cited document
        cited_doc = retrieved_docs[citation_num - 1]
        if claim not in cited_doc:
            return 0.0  # Claim not in source

    return 1.0  # All citations correct


# --- Section: Q5 - RAG Eval Strategy (Citation Oracle, Simplified) ---

def citation_oracle_simple(answer, retrieved_docs):
    citations = extract_citations(answer)
    for cite_num in citations:
        claim = extract_claim_for_citation(answer, cite_num)
        cited_doc = retrieved_docs[cite_num - 1]
        if claim not in cited_doc:
            return False  # Citation incorrect
    return True


# --- Section: Q6 - Trajectory Quality Scoring ---

def score_trajectory(steps):
    step_scores = [score_step(s) for s in steps]
    # Geometric mean: (s1 * s2 * ... * sn)^(1/n)
    return np.prod(step_scores) ** (1 / len(step_scores))


# --- Section: Q11 - LLM Judge Validation Dataset ---

validation_set = [
    {
        "output": "Paris is the capital of France.",
        "expected_score": 5,  # Perfect answer
        "category": "factual"
    },
    {
        "output": "Paris is a city.",
        "expected_score": 3,  # Correct but vague
        "category": "factual"
    },
    {
        "output": "London is the capital of France.",
        "expected_score": 1,  # Factually wrong
        "category": "factual"
    },
    # ... 100 examples across categories
]

# Run judge on validation set
for example in validation_set:
    judge_score = judge(example["output"], rubric)
    human_score = example["expected_score"]

    agreement = abs(judge_score - human_score) <= 1  # Allow 1-point tolerance
    # Aggregate: what % of examples have judge-human agreement?
