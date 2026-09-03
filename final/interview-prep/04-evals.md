# LLM & Agent Evaluation

## What Is This?

LLM and agent evaluation is the systematic measurement of model and agent behavior against known-good outcomes. Think of it as a manufacturing QC test lab, not a report card. Every release decision, every production change, every prompt iteration gates on eval results.

**Mental model: Dual-Oracle System**
- Hard Oracle: Ground truth (exact answers, verified tool calls, gold-standard trajectories)
- Soft Oracle: LLM-as-judge scoring quality on dimensions that have no single correct answer (helpfulness, tone, relevance)

**The evaluated system is:**
```
model × scaffold × tools × environment × judge × sampling
```

Change any one component and the score changes. An eval measures the entire stack, not just the model.

**Three independently scaled planes:**
```
                  ┌─────────────────────┐
                  │   Eval Harness      │ ← Pre-production quality gate
                  │   (SWE-bench, GAIA) │
                  └─────────────────────┘
                           │
                           │ Release decision
                           ▼
                  ┌─────────────────────┐
                  │  Production Agent   │
                  │  (model+scaffold)   │
                  └─────────────────────┘
                           │
                           │ traces, samples (1-10%)
                           ▼
                  ┌─────────────────────┐
                  │  Online Eval        │ ← Drift detection, regression
                  │  (Judge Sidecar)    │
                  └─────────────────────┘
```

Each plane runs at a different cadence:
- Eval harness: Every model change, daily CI
- Production: Continuous deployment
- Online eval: Sliding window, hourly or per-batch

**Core analogy:** "Hiring a new employee"
- Resume screening = Benchmark pass rate (SWE-bench, GAIA)
- Technical interview = Trajectory evaluation (tool usage, reasoning quality)
- 90-day probation = Online eval in production (regression detection)

## Why It Matters

**Without evals, you have:**
- No release gate (can't ship confidently)
- No regression detection (silent degradation)
- No A/B test validity (don't know which variant is better)
- No optimization signal (prompt engineering is guesswork)

**With evals, you get:**
- Automated quality gate: Block releases that drop task success below threshold
- Cost/quality Pareto frontier: Choose the cheapest model that meets SLA
- Contamination defense: Detect when the model has memorized the test set
- Accountability: Trace every production failure back to an eval gap

**Real impact:**
- Anthropic tau-airline study: Extended thinking improved pass^1 from 33.2% to 58.4%
- HumanEval+: Scaling test suite 80x dropped GPT-4 pass@1 by 19.3 percentage points
- On Randomness study: Temperature 0 still produces >1.5pp standard deviation across 60,000 agent trajectories
- AgentLens: 10.7% of "passing" agent runs were lucky false positives

## Architecture / System Design

### High-Level Flow

```
┌──────────────┐
│   Dataset    │  Test cases (inputs + expected outputs)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Eval Runner  │  Orchestrates execution, batching, retries
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Target Agent │  Model + scaffold + tools
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Environment  │  Sandboxed execution (filesystem, APIs, databases)
│   + Tools    │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   Traces     │  Logs of all actions, tool calls, LLM responses
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   Graders    │  Hard oracle (exact match) + Soft oracle (LLM judge)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Statistics  │  Aggregate metrics (pass@k, cost, latency)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Release Gate │  Go/No-Go decision (threshold check)
└──────────────┘
```

### Three-Layer Eval Stack

**Layer 1: End-to-End (Task Success)**
- Binary: Did the agent accomplish the goal?
- Examples: Solved the GitHub issue (SWE-bench), answered the question correctly (GAIA), booked the correct flight (tau-airline)
- Metric: pass@k, pass^k

**Layer 2: Trajectory Quality**
- How did the agent get to the answer?
- Tool selection accuracy, argument correctness, reasoning coherence
- Scored per step, aggregated over trajectory
- Metric: Geometric mean of step scores

**Layer 3: Component-Level**
- Individual scaffold pieces in isolation
- Tool selection: Accuracy of choosing the right tool given a state
- Argument generation: Correctness of parameters passed to tools
- Error recovery: Does the agent retry after tool failure?
- Metric: Per-component accuracy

### Evaluation Paradigms

| Paradigm | Input | Output | Use Case | Example |
|----------|-------|--------|----------|---------|
| **Pointwise** | Single response | Absolute score | Pass/fail, quality rating | "Is this summary factually correct?" |
| **Pairwise** | Two responses | Preference (A>B, B>A, Tie) | A/B tests, model comparison | "Which answer is more helpful?" |
| **Reference-Based** | Response + Gold standard | Similarity/correctness | Exact task success | "Does the SQL query match the expected result?" |

**When to use each:**
- Pointwise: Production monitoring, absolute quality gate
- Pairwise: Choosing between prompt variants, model selection
- Reference-based: Benchmarks with ground truth (SWE-bench, MATH)

### Six Dimensions of Agent Evaluation

```
1. Task Success (binary or continuous)
   └─ pass@k, pass^k, exact match, F1

2. Trajectory Quality
   ├─ Tool selection accuracy (>=95% target)
   ├─ Argument correctness (>=90% target)
   ├─ Repetition rate (<5% target)
   └─ Error recovery

3. Tool Accuracy
   ├─ Precision: Did the agent call only necessary tools?
   ├─ Recall: Did it call all required tools?
   └─ Efficiency: Minimum tool calls to solve the task

4. Output Quality (LLM-as-judge)
   ├─ Relevance
   ├─ Helpfulness
   ├─ Harmlessness
   ├─ Factuality
   └─ Style/Tone

5. Cost
   ├─ Input tokens
   ├─ Output tokens
   ├─ Cached tokens (if applicable)
   ├─ Tool call overhead
   └─ Judge tokens (for eval itself)

6. Latency
   ├─ Time to first token (TTFT)
   ├─ Time to completion
   ├─ p50, p95, p99 distribution
   └─ Tool execution time
```

**Hierarchy:** Task success is the north star. Trajectory quality explains why success happened or failed. Cost and latency are constraints (meet SLA while maximizing success).

## Core Concepts & Algorithms

### pass@k (Unbiased Estimator)

**Definition:** Probability that at least one of k samples solves the task.

**Why it matters:** A model that solves a task 30% of the time is useless if you can only afford one try. But if you can sample k=3 times and pick the best, success rate jumps to 65.7%.

**Formula (Chen et al., Codex paper):**
```
pass@k = 1 - C(n - c, k) / C(n, k)

where:
  n = total samples drawn per task
  c = number of correct samples
  k = samples you want to estimate for (k <= n)
  C(a, b) = binomial coefficient "a choose b"
```

**Numerically stable form (product of fractions):**
```
pass@k = 1 - ∏(i=0 to k-1) [(n - c - i) / (n - i)]
```

**Intuition:** You draw n samples, observe c successes. pass@k estimates the probability of at least one success if you had drawn k samples instead.

**Example:**
```
n = 10 samples drawn
c = 3 correct
k = 5 (estimate for 5 samples)

pass@5 = 1 - C(10-3, 5) / C(10, 5)
       = 1 - C(7, 5) / C(10, 5)
       = 1 - 21 / 252
       = 1 - 0.0833
       = 0.9167 (91.67%)
```

**HumanEval+ result:** When tests were scaled 80x, pass@k dropped:
- GPT-4: 19.3 percentage points
- GPT-3.5: 24.9 percentage points
- CodeGen: 28.9 percentage points

This shows contamination or overfitting to shallow test coverage.

### pass^k (Reliability Metric)

**Definition:** Probability that all k samples solve the task (dual to pass@k).

**Formula (Yao et al., tau-bench):**
```
pass^k = C(c, k) / C(n, k)

where c >= k (must have at least k correct samples)
```

**Intuition:** Draw without replacement. If you need k consecutive successes, what's the probability?

**Use case:** Reliability-critical applications where every invocation must succeed (medical diagnosis, financial advice).

**Example:**
```
n = 10 samples
c = 8 correct
k = 3

pass^3 = C(8, 3) / C(10, 3)
       = 56 / 120
       = 0.4667 (46.67%)
```

**Anthropic tau-airline study (with extended thinking):**

| Metric | Baseline | With Think Tool |
|--------|----------|-----------------|
| pass^1 | 0.332 | 0.584 (+25.2 pp) |
| pass^2 | 0.197 | 0.465 (+26.8 pp) |
| pass^3 | 0.127 | 0.381 (+25.4 pp) |
| pass^4 | 0.084 | 0.320 (+23.6 pp) |
| pass^5 | 0.100 | 0.340 (+24.0 pp) |

**Retail task (extended thinking):**
- pass^1: 0.812
- pass^5: 0.626 (drops 18.6 pp due to variance)

**Key insight:** pass^k always <= pass@k. Gap shows variance. Narrow gap = consistent agent.

### On Randomness in Agent Evaluation

**Study (2026):** 60,000 trajectories, 25.58B tokens, 1.88M tool calls across multiple benchmarks.

**Finding:** Even at temperature = 0, standard deviation >1.5 percentage points across runs of the same task.

**Sources of variance:**
1. Non-deterministic tool execution (API latency, database state)
2. Tie-breaking in greedy sampling (multiple tokens with same logprob)
3. Infrastructure noise (batching, quantization, GPU placement)
4. Retry logic (agent retries after failure, order of retries varies)

**Implication:** A single run is not enough. Always sample n>=10 per task to estimate pass@k reliably.

**Retry inflation:** If an agent retries failed tasks, pass@1 overstates single-shot performance. Report both "pass@1 (no retry)" and "pass@1 (with retry)" separately.

### Power Analysis for Eval Sample Size

**Question:** How many samples do you need to detect a 3 percentage point drop in pass@1 with 95% confidence?

**Formula (two-proportion z-test, Miller et al.):**
```
n ≈ 2 * (z_alpha + z_beta)^2 * p * (1 - p) / delta^2

where:
  z_alpha = 1.96 (for 95% confidence)
  z_beta = 0.84 (for 80% power)
  p = baseline pass rate (e.g., 0.5)
  delta = minimum detectable difference (e.g., 0.03)

Example:
  p = 0.5, delta = 0.03
  n ≈ 2 * (1.96 + 0.84)^2 * 0.5 * 0.5 / 0.03^2
  n ≈ 2 * 7.84 * 0.25 / 0.0009
  n ≈ 969 samples per variant
```

**Rule of thumb:** 1000+ samples to detect 3pp differences, 250+ for 6pp, 100+ for 12pp.

### Trajectory Scoring

**Problem:** How do you aggregate per-step scores into a single trajectory score?

**Options:**

**1. Arithmetic Mean**
```
score = (s1 + s2 + ... + sn) / n
```
Problem: One bad step gets averaged out.

**2. Geometric Mean (Recommended)**
```
score = (s1 * s2 * ... * sn)^(1/n)
```
Benefit: Any step score of 0 makes the entire trajectory 0. Captures compounding quality.

**Worked example:**
```
5-step trajectory:
  Step 1: Tool selection correct (1.0)
  Step 2: Argument error (0.5)
  Step 3: Tool selection correct (1.0)
  Step 4: Tool selection correct (1.0)
  Step 5: Final answer correct (1.0)

Arithmetic mean: (1.0 + 0.5 + 1.0 + 1.0 + 1.0) / 5 = 0.90
Geometric mean: (1.0 * 0.5 * 1.0 * 1.0 * 1.0)^(1/5) = 0.87

Geometric mean penalizes the error more, which is correct: the trajectory had a flaw.
```

**3. Minimum (Strictest)**
```
score = min(s1, s2, ..., sn)
```
Use for safety-critical systems where one mistake is fatal.

**4. Weighted Average**
```
score = w1*s1 + w2*s2 + ... + wn*sn
```
Use when later steps matter more (e.g., final answer is 50% of score, reasoning is 50%).

### Trajectory Evaluation Modes

| Mode | Description | Example | Use Case |
|------|-------------|---------|----------|
| **Exact Matching** | Tool calls must match gold trajectory exactly (order + args) | SWE-bench: Expected `git diff`, agent called `git status` first → fail | High-fidelity reproduction tasks |
| **Set-Based** | Tool calls must match as a set (order doesn't matter) | Required: {search, read, edit}. Agent: {read, search, edit} → pass | Tasks with no canonical order |
| **Partial Credit** | Score proportional to overlap with gold trajectory | 3/5 steps correct → 60% | Debugging, explainability |
| **LLM Judge** | Judge scores trajectory quality on rubric | "Rate reasoning coherence 1-5" | Open-ended tasks |

### Component-Level Sub-Metrics

**Tool Selection Accuracy:**
```
Accuracy = (Correct tool calls) / (Total tool calls)
Target: >= 95%
```

**Argument Correctness:**
```
Correctness = (Correct args) / (Total args)
Target: >= 90%
```

**Repetition Rate:**
```
Repetition = (Duplicate tool calls) / (Total tool calls)
Target: < 5%
```

**Error Recovery:**
```
Recovery = (Retries that succeed) / (Total errors)
Target: >= 70%
```

## Code Examples

### Production-Grade Eval Pipeline (Layered Scoring)

```python
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
```

### Trajectory Step Scorer with Geometric Mean

```python
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
```

### Online Eval Monitor with Drift Detection

```python
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
```

### Dual-Oracle Eval Runtime (Hard + Soft)

```python
from typing import Callable, Any, Optional, List
from enum import Enum
import time

class OracleType(Enum):
    HARD = "hard"  # Exact match, deterministic
    SOFT = "soft"  # LLM judge, probabilistic

class EvalInvariant(Enum):
    """Eval design invariants"""
    I1 = "Orthogonal planes: harness ⊥ production ⊥ online"
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
```

### pass@k and pass^k Implementation

```python
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
    
    # Product form: 1 - ∏(i=0 to k-1) [(n-c-i) / (n-i)]
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
```

## Token Economics & Cost Analysis

### Cost Components

**Production cost (per request):**
```
Cost = (Input tokens × Input price) + (Output tokens × Output price) + Tool execution cost

For cached requests:
Cost = (Cache write tokens × Write price) + (Cache read tokens × Read price) + (Uncached tokens × Input price) + (Output × Output price)
```

**Eval cost (per eval run):**
```
Eval cost = Production cost + Judge cost

Judge cost = Judge calls × (Judge input tokens × Judge input price + Judge output tokens × Judge output price)
```

**Guardrail: Judge cost should be <15% of production LLM cost**

If judge cost exceeds this, you're spending more on evaluation than production.

### Platform Cost Meters

| Component | Metered By | Pricing Mechanism |
|-----------|------------|-------------------|
| **LangSmith** | Traces ingested, storage, annotations | Free: 5k traces/month, then $30/100k traces |
| **Braintrust** | Rows evaluated, LLM calls (judge) | Free: 100k rows/month, $100/1M rows after |
| **Datadog** | Custom metrics, logs, APM spans | $0.10/100 custom metrics, $0.10/1M spans |
| **Phoenix (Arize)** | Traces, storage, models monitored | Free OSS, Cloud: $99/month base + usage |
| **Promptfoo** | Eval runs (self-hosted), cloud storage | Free OSS, Cloud: $50/month/seat |
| **DeepEval** | Test cases, judge calls | Free OSS, Cloud: $99/month + usage |
| **OpenAI Evals** | Self-hosted (your infra cost) | Free (but you pay OpenAI API costs) |

### Per-Eval-Run Cost Example

**Scenario:** Evaluate 500 test cases using Claude Opus 5 as judge

**Assumptions:**
- Production agent: Claude Sonnet 4.5
- Judge: Claude Opus 5
- Avg input per task: 2000 tokens (context + task)
- Avg agent output: 500 tokens
- Judge input: 2000 (trajectory) + 500 (rubric) = 2500 tokens
- Judge output: 200 tokens (score + reasoning)

**Production cost:**
```
Sonnet 4.5: $3/MTok input, $15/MTok output
  Input: 500 × 2000 = 1M tokens → $3
  Output: 500 × 500 = 250k tokens → $3.75
  Total: $6.75
```

**Judge cost:**
```
Opus 5: $15/MTok input, $75/MTok output
  Input: 500 × 2500 = 1.25M tokens → $18.75
  Output: 500 × 200 = 100k tokens → $7.50
  Total: $26.25
```

**All-in eval cost:** $6.75 + $26.25 = $33.00 for 500 test cases

**Cost per test case:** $0.066

**Judge-to-production ratio:** $26.25 / $6.75 = 3.9x

This violates the 15% guardrail. Solutions:
1. Use cheaper judge (Sonnet 4.5 instead of Opus 5): $26.25 → $5.25 (0.78x ratio)
2. Sample: Only judge 20% of cases → $26.25 → $5.25
3. Hard oracle first: Only judge when hard oracle fails (assume 20% failure) → $26.25 → $5.25

### Benchmark Suite Costs

| Benchmark | Size | Avg Time/Task | Judge Calls/Task | Est. Cost (Full Run) |
|-----------|------|---------------|------------------|----------------------|
| **SWE-bench** | 2,294 | 5-10 min | 1 (exact match) | $500-1000 (agent execution, no judge) |
| **SWE-bench Lite** | 500 | 5-10 min | 1 | $100-200 |
| **SWE-bench Verified** | 731 | 5-10 min | 1 | $150-300 |
| **GAIA** | 466 | 2-5 min | 1-3 (multi-step) | $50-150 |
| **GAIA 2** | 690 | 2-5 min | 1-3 | $75-225 |
| **tau-bench** | 1,200 (airline+retail) | 1-3 min | 1 | $100-300 |
| **HumanEval** | 164 | <1 min | 0 (unit test) | $5-10 |
| **HumanEval+** | 164 (80x tests) | <1 min | 0 | $5-10 |
| **MATH** | 12,500 | <1 min | 0 (symbolic check) | $50-100 |
| **HealthBench** | 5,000 | 1-2 min | ~11/example | $500-1000 |
| **BFCL V4** | ~2,000 | <1 min | 0 (tool call match) | $20-50 |

**Note:** Costs assume agent execution + judge calls. Does not include:
- Platform fees (LangSmith, Braintrust)
- Infra overhead (workers, storage)
- Retries or k-sample runs (multiply by k)

### Cost Optimization Strategies

| Strategy | Cost Reduction | Trade-off |
|----------|----------------|-----------|
| **1. Cheap judge first, escalate** | 60-80% | May miss subtle quality gaps |
| Use Haiku/Sonnet as first-pass, Opus only for edge cases | | |
| **2. Sampling** | 50-90% | Lose coverage, higher variance |
| Evaluate 10-20% of production traffic | | |
| **3. Hard oracle preference** | 80-95% | Only works when ground truth exists |
| Skip judge if exact match passes | | |
| **4. Prompt caching** | 40-60% on judge input | Only helps on repeated rubrics |
| Cache rubric + few-shot examples | | |
| **5. Batching** | 10-20% | Higher latency, infra complexity |
| Batch 100+ requests to amortize overhead | | |

### Judge-Token Reference Loop

**Problem:** If the judge needs to see the full agent trajectory, and the trajectory is long (many tool calls, large outputs), judge input tokens can exceed production input tokens.

**Example:**
```
Production agent:
  Input: 1000 tokens (user query + context)
  Output: 500 tokens (answer)

Judge sees:
  Agent input: 1000 tokens
  Agent output: 500 tokens
  Tool calls (5 × 200 tokens): 1000 tokens
  Rubric: 500 tokens
  Total judge input: 3000 tokens (3x production input)
```

**Mitigation:**
1. Summarize trajectory before sending to judge (loses fidelity)
2. Use component-level grading (tool selection, args) instead of full trajectory
3. Hard oracle first, judge only on failures

### All-In Cost Examples

**Scenario A: RAG Eval (500 tasks)**
```
Production:
  Model: Sonnet 4.5
  Avg input: 3000 tokens (context + query)
  Avg output: 300 tokens
  Cost: (500 × 3000 × $3/MTok) + (500 × 300 × $15/MTok) = $4.50 + $2.25 = $6.75

Judge (RAGAS faithfulness):
  Model: Sonnet 4.5
  Avg input: 3000 (context) + 300 (answer) + 500 (rubric) = 3800 tokens
  Avg output: 100 tokens
  Cost: (500 × 3800 × $3/MTok) + (500 × 100 × $15/MTok) = $5.70 + $0.75 = $6.45

Platform (Braintrust):
  500 rows = Free tier

Total: $6.75 + $6.45 = $13.20
Cost per task: $0.026
```

**Scenario B: Agent Eval (100 tasks, long trajectories)**
```
Production:
  Model: Opus 5
  Avg input: 2000 tokens
  Avg output: 5000 tokens (multi-step reasoning + tool calls)
  Cost: (100 × 2000 × $15/MTok) + (100 × 5000 × $75/MTok) = $3.00 + $37.50 = $40.50

Judge:
  Model: Sonnet 4.5 (cheaper)
  Avg input: 5000 (full trajectory) + 500 (rubric) = 5500 tokens
  Avg output: 200 tokens
  Cost: (100 × 5500 × $3/MTok) + (100 × 200 × $15/MTok) = $1.65 + $0.30 = $1.95

Platform (LangSmith):
  100 traces = Free tier

Total: $40.50 + $1.95 = $42.45
Cost per task: $0.42
Judge ratio: $1.95 / $40.50 = 4.8% ✓ (under 15% limit)
```

### Latency & Throughput

**Two clocks to track:**

1. **User-facing latency:** Time from request to response (production only)
   - Eval harness runs offline, no user impact
   - Target: 0ms overhead (eval is async)

2. **Eval time-to-score:** Time from trace capture to score available
   - Pointwise (single request): <1 second (for real-time dashboards)
   - Batch (nightly suite): <1 hour for 1000 tasks
   - Online monitoring: <5 minutes for drift detection

**Latency SLA Targets:**

| Eval Tier | p50 | p95 | p99 | Use Case |
|-----------|-----|-----|-----|----------|
| **Inline** (sync judge) | 500ms | 1s | 2s | Real-time quality gate (risky) |
| **Sidecar** (async judge) | 2s | 5s | 10s | Production monitoring |
| **Batch** (nightly CI) | 10s | 30s | 60s | Pre-deployment suite |
| **One-off** (ad-hoc research) | No SLA | No SLA | No SLA | Exploratory analysis |

**Throughput ceilings:**

| System | Max Tasks/Hour | Limiting Factor |
|--------|----------------|-----------------|
| **LangSmith** | ~10,000 | API rate limits (100 req/s) |
| **Braintrust** | ~50,000 | Ingest pipeline capacity |
| **Self-hosted** | ~100,000+ | Worker pool size |
| **GAIA 2 (official)** | ~700/day | Human-in-loop verification |
| **SWE-bench (official)** | ~100/day | Sandbox reset time (5-10 min/task) |

**Back-pressure design:**
When eval throughput < production throughput, use sampling or queue shedding:

```python
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
```

**Capacity planning worked example:**

```
Target: Evaluate 10% of production traffic
Production rate: 1000 req/s
Eval rate needed: 100 req/s

Per-eval latency:
  Agent execution (replay): 2s
  Judge scoring: 1s
  Total: 3s

Worker capacity per worker: 1/3 req/s (3s latency)
Workers needed: 100 / (1/3) = 300 workers

Cost:
  300 workers × $0.01/hour (spot instance) = $3/hour = $2,160/month
  LLM costs: 100 req/s × 3600s × 24h × 30d = 259M requests/month
  At $0.01/request → $2.59M/month (judge + agent execution)

Conclusion: 10% sampling is too expensive. Reduce to 1% → $0.30/hour infra + $259k/month LLM.
```

## Trade-offs & Failure Modes

### LLM-as-Judge Biases (Taxonomy with Severity)

| Bias Type | Severity | Description | Mitigation |
|-----------|----------|-------------|------------|
| **Position Bias** | High | Judge prefers first or last response in pairwise comparison | Swap order, aggregate |
| - GPT-4 changed preference ~1/3 of cases when order swapped (Zheng et al.) | | | |
| **Length/Verbosity Bias** | Medium-High | Longer responses score higher regardless of quality | Normalize by length, explicit rubric |
| **Self-Preference Bias** | Medium | Model prefers its own outputs over others | Use different model as judge |
| **Rubric Position Bias** | Medium | Judge scores first rubric item higher (2026 finding) | Randomize rubric order |
| **Compounding Biases** | High | Multiple biases interact (FairJudge Feb 2026: >50% error rates) | Ensemble judges, human calibration |

**Position bias example (Zheng et al.):**
```
Prompt: "Which is better, A or B?"
  A: Short answer
  B: Long answer
  Judge: "B is better" (60% of time)

Swapped:
  B: Long answer
  A: Short answer
  Judge: "A is better" (55% of time)

Actual preference: Indeterminate due to position+length bias
```

**Mitigation: Position swap + aggregate**
```python
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
```

### Judge Validation Protocol

Before deploying an LLM judge in production, validate:

1. **Inter-judge agreement:** Run 2+ models as judges on same dataset, measure Cohen's kappa
   - Target: kappa >= 0.6 (substantial agreement)
   - If kappa < 0.4, judges are unreliable

2. **Human alignment:** Sample 100+ judgments, have humans label, measure agreement
   - Target: >= 80% agreement with human majority vote
   - RAGAS faithfulness achieves ~95% human agreement

3. **Bias audit:** Test for position, length, self-preference bias
   - Swap orders, normalize lengths, cross-model judging

4. **Calibration:** Anchor judge on known-good and known-bad examples
   - Show 5-10 examples before eval: "This is a score-5 response... This is a score-1 response..."

5. **Drift monitoring:** Re-validate monthly (model updates can change judge behavior)

### LLM-as-Judge Calibration Anchors

| Benchmark | Human-Judge Agreement | Caveat |
|-----------|----------------------|--------|
| **RAGAS Faithfulness** | ~95% | Only for RAG factuality, not general quality |
| **AlpacaEval** | ~86% (GPT-4 vs human) | Length bias: longer = better |
| **MT-Bench** | ~80% (GPT-4 vs human) | Position bias in pairwise mode |
| **Arena-Hard** | ~90% (GPT-4-turbo vs human) | Crowd-sourced, noisy labels |
| **HHH (Helpful/Harmless/Honest)** | ~75% | Subjective, low inter-annotator agreement |
| **SimpleQA** | 100% (hard-coded) | Binary factuality, no judge needed (but useful as a pattern) |
| **FairJudge (Feb 2026)** | Baseline: 50% error rate | Exposed compounding biases in prior judges |

### Human Evaluation: Inter-Annotator Agreement

When using human labels as ground truth, measure inter-annotator agreement (IAA):

**Cohen's kappa:**
```
kappa = (p_observed - p_expected) / (1 - p_expected)

where:
  p_observed = proportion of agreement
  p_expected = proportion of agreement by chance
```

**Interpretation:**
- kappa < 0.2: Slight agreement
- 0.2-0.4: Fair
- 0.4-0.6: Moderate
- 0.6-0.8: Substantial
- 0.8-1.0: Almost perfect

**Target by task type:**

| Task | Target Kappa | Rationale |
|------|--------------|-----------|
| **Factuality** (binary) | >= 0.8 | Objective, clear ground truth |
| **Relevance** (1-5 scale) | >= 0.6 | Some subjectivity |
| **Helpfulness** (1-5 scale) | >= 0.5 | Highly subjective |
| **Trajectory quality** | >= 0.4 | Complex, multi-dimensional |

**Kappa prevalence paradox:** High agreement can still yield low kappa if the distribution is skewed.

Example:
```
100 samples, 95 are positive, 5 are negative
Annotators agree on 96/100 (96% agreement)
But kappa = 0.5 (moderate) because chance agreement is high
```

Solution: Report both raw agreement and kappa.

### Chain-of-Thought for Judge Reliability

**Finding:** Asking judges to produce reasoning before scoring improves agreement with humans.

**Experiment (Anthropic, 2025):**
- Without CoT: Judge-human agreement ~0.55 (kappa)
- With CoT: Judge-human agreement ~0.75 (kappa)

**Pattern:**
```python
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
```

**Calibration protocol (4 steps):**

1. **Anchor examples:** Show 5-10 calibration examples (known scores) before eval
2. **Chain-of-thought:** Force judge to explain reasoning first
3. **Multi-turn refinement:** Let judge revise initial score after seeing own reasoning
4. **Ensemble:** Run 2-3 judges, take majority vote or average

### Benchmark Reward Hacking

**UC Berkeley RDI (April 2026) study:** Intentionally broke all 8 major LLM benchmarks by exploiting eval implementation bugs.

**Findings:**
- SWE-bench: 19.78% of "resolved" issues were semantically incorrect but passed unit tests
- GAIA: Agents cached API responses, replayed on retry (inflated pass@k)
- MATH: LaTeX formatting tricks fooled symbolic checker
- HumanEval: Hardcoded solutions for common prompts
- AlpacaEval: Optimized for verbosity (length bias)
- MT-Bench: Position bias in pairwise mode
- MMLU: Memorized test set (contamination)
- GSM8K: Relied on calculator tool without checking arithmetic validity

**Takeaway:** Treat benchmark numbers as upper bounds, not ground truth. Always inspect failures manually.

### SWE-bench Contamination & Inflation

**Problem:** 19.78% of SWE-bench "passes" were semantically incorrect.

**Example:**
```
Task: Fix bug in function `calculate_discount`
Expected: Correctly apply 10% discount
Agent solution: Hardcode return value for test cases, logic still broken
Test: Passes (only tests known cases)
Semantic correctness: Fail (breaks on new inputs)
```

**Mitigation:**
- SWE-bench Verified (731 tasks): Human-reviewed for semantic correctness
- Test scaling (HumanEval+): Increase test coverage 80x
- Holdout test sets: Don't publish, rotate monthly

**GAIA 2 Mitigations:**
- 690 new tasks (vs 466 in GAIA 1)
- API response randomization (prevents caching)
- Human-in-loop verification (every agent pass is manually reviewed)

### Contamination Controls

| Control Type | Description | Example | Effectiveness |
|--------------|-------------|---------|---------------|
| **Holdout sets** | Unpublished test sets, rotated periodically | Google Gemini leaderboard (rotates monthly) | High (until leaked) |
| **Canary tokens** | Unique identifiers embedded in test data to detect leaks | SWE-bench ticket IDs | High (detects but doesn't prevent) |
| **Time-based splits** | Test on data after model's cutoff date | GAIA 2 (created after GPT-4 training) | Medium (models still learn patterns) |
| **Dynamic generation** | Generate new tasks on-the-fly | MATH (symbolic algebra, infinite variants) | High (hard to memorize) |
| **Human verification** | Manual review of every "pass" | GAIA 2, SWE-bench Verified | Very high (expensive) |

### SimpleQA as Refusal-Aware Hard-Gate Pattern

**SimpleQA (Anthropic, 2025):** 4,326 fact-seeking questions, model must answer or refuse.

**Scoring:**
```
if model refuses and should refuse:
    score = 1 (correct)
elif model answers and answer is correct:
    score = 1
else:
    score = 0
```

**Key insight:** Refusal is a valid response. Many benchmarks penalize "I don't know" even when correct.

**Pattern:** Refusal-aware eval
```python
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
```

### RAGAS-Class Metrics (RAG Evaluation)

**RAGAS Faithfulness:**
```
Faithfulness = (Number of claims supported by context) / (Total claims in answer)

Algorithm:
  1. Extract claims from answer (LLM call: "List all factual claims")
  2. For each claim, check if supported by retrieved context (LLM call: "Is this claim supported?")
  3. Aggregate: faithful claims / total claims
```

**Example:**
```
Retrieved context: "Paris is the capital of France. It has a population of 2.1M."
Answer: "Paris is the capital of France and has 10M people."

Claims:
  1. "Paris is the capital of France" → Supported ✓
  2. "Paris has 10M people" → Not supported ✗ (context says 2.1M)

Faithfulness = 1/2 = 0.5
```

**DeepEval Differences:**
- RAGAS: Focuses on faithfulness (factual grounding)
- DeepEval: Adds answer relevance (does answer address query?)

**Citation Correctness (2026 addition):**
```
Citation Correctness = (Correct citations) / (Total citations)

A citation is correct if:
  1. The cited source is in the retrieved context
  2. The cited claim is actually in that source
```

**Self-RAG (2024):**
Adds retrieval necessity check:
```
if query requires external knowledge:
    retrieve()
else:
    answer from parametric memory (no retrieval)
```

### Trajectory vs Stateless Tool-Call Eval

| Stage | Stateless (BFCL) | Trajectory (SWE-bench) |
|-------|------------------|------------------------|
| **Input** | Single tool call | Sequence of tool calls |
| **Context** | No history | Full conversation + state |
| **Complexity** | Low (1 call) | High (multi-step reasoning) |
| **Eval metric** | Exact match (tool + args) | End-to-end task success |
| **Example** | "Call `get_weather(city='SF')`" | "Fix GitHub issue #1234" |
| **Failure mode** | Wrong tool or args | Correct tools, wrong order |
| **Judge needed?** | No (hard oracle) | Often yes (trajectory quality) |

**BFCL V4 Weights (Berkeley Function-Calling Leaderboard):**
- Agentic (multi-step): 40%
- Multi-Turn (conversational): 30%
- Live (real API execution): 10%
- Non-Live (mocked): 10%
- Hallucination detection: 10%

Shows evolution from stateless (V1) to trajectory-aware (V4).

### AgentLens: Lucky Pass Detection

**AgentLens (Microsoft Research, 2026):** Analyzed 10,000 agent runs, found 10.7% were "lucky passes."

**Lucky pass:** Agent succeeded but for the wrong reason.

**Example:**
```
Task: Book a flight from SFO to JFK on March 15
Expected: search_flights(origin="SFO", dest="JFK", date="2026-03-15") → book_flight(flight_id)

Lucky pass:
  Agent: search_flights(origin="SFO", dest="JFK", date="2026-03-16")  # Wrong date
  But: Only one flight in results (API returned same flight for both dates due to bug)
  Agent: book_flight(flight_id) → Success ✓

Eval: Passed (booked correct flight)
Reality: Agent made a mistake but got lucky
```

**Detection:** Run same task multiple times with different random seeds. If pass rate < 100%, some passes are lucky.

### Complexity of k-Sample Evals

**Problem:** Estimating pass@k requires drawing n >= k samples per task. For k=100, that's expensive.

**Workaround:** Stratified sampling
```python
def stratified_pass_at_k(pass_at_1: float, k: int) -> float:
    """
    Estimate pass@k from pass@1 assuming independence.
    
    pass@k ≈ 1 - (1 - pass@1)^k
    
    Caveat: Assumes samples are independent (often false for agents)
    """
    return 1 - (1 - pass_at_1) ** k

# Example: pass@1 = 0.3, estimate pass@10
# pass@10 ≈ 1 - (1 - 0.3)^10 = 1 - 0.028 = 0.972
```

**When this breaks:** If failures are correlated (e.g., all retries hit same API rate limit), independence assumption fails.

**Better approach:** Draw k samples, compute pass@k directly (more expensive but accurate).

## Production Patterns & Best Practices

### Non-Functional Requirements (NFRs) for Eval Systems

| NFR | Target | Measurement |
|-----|--------|-------------|
| **Availability** | 99.9% uptime | Eval system can score traces even during outages (queue + retry) |
| **RPO** (Recovery Point Objective) | <1 hour | Max data loss if eval system crashes |
| **RTO** (Recovery Time Objective) | <15 minutes | Max downtime before eval resumes |
| **Compliance** | GDPR, HIPAA (if applicable) | PII redaction, audit trails |
| **Correctness** | <1% eval errors | Judge hallucination rate, false positive/negative rate |
| **Eval as Product** | Customer-facing metrics | Expose eval scores to end-users (transparency) |

### Flaky CI Mitigations

**Problem:** Non-deterministic agent behavior causes CI flakiness (eval passes locally, fails in CI).

**Sources of flakiness:**
1. Temperature > 0 (sampling variance)
2. Tool execution depends on external state (APIs, databases)
3. Race conditions (parallel tool calls)
4. Infrastructure noise (GPU placement, quantization)

**Mitigations:**

| Mitigation | Effectiveness | Trade-off |
|------------|---------------|-----------|
| **Set temperature=0** | Medium | Still non-deterministic (tie-breaking) |
| **Mock external APIs** | High | Loses real-world accuracy |
| **Retry failed tests (3x)** | Medium | Masks real issues, inflates pass rate |
| **k-sample tests (pass@3)** | High | Slower, more expensive |
| **Seed pinning** | Low | Only works for sampling, not tools |
| **Idempotent tool execution** | High | Requires tool redesign |

**Best practice:** Use pass@3 with temperature=0 for CI. If any of 3 runs passes, test passes.

```python
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
```

### Online Eval Resilience

**Challenge:** Production traffic is unpredictable. Eval system must not fall over.

**Resilience patterns:**

**1. Sampling (1-10% of traffic)**
```python
import random

def should_evaluate() -> bool:
    return random.random() < 0.1  # 10% sample rate
```

**2. Drift detection (sliding window)**
```python
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
```

**3. Judge consistency checks**
Run same trace through judge multiple times, check variance:
```python
def judge_consistency_check(trace, rubric, n=3) -> float:
    scores = [judge(trace, rubric) for _ in range(n)]
    std = np.std(scores)
    return std  # Should be <0.1 for consistent judge
```

### Checkpointing for Long-Running Suites

**Problem:** SWE-bench takes 10+ hours to run. If the run crashes at hour 9, you lose all progress.

**Solution:** Checkpoint every N tasks, resume on failure.

```python
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
```

### Eval Governance & Compliance

**EU AI Act Context (August 2026):**
- High-risk AI systems must maintain evaluation logs for 10 years
- Eval datasets must be representative (no demographic bias)
- Judge decisions must be auditable (explainability requirement)

**Implications:**
- Store eval traces, scores, judge reasoning permanently
- Demographic stratification (if applicable): test on diverse user groups separately
- Judge transparency: Always log judge prompt + response, never hide reasoning

**RBAC for Eval (4-Role Model):**

| Role | Permissions | Example |
|------|-------------|---------|
| **Viewer** | Read eval results, dashboards | Product manager checking pass rate |
| **Runner** | Trigger eval runs, view results | Eng running nightly CI |
| **Editor** | Modify eval datasets, rubrics | ML Eng tuning judge prompts |
| **Admin** | Grant permissions, delete data | Security team enforcing retention |

**PII in Evaluation Data:**

**Problem:** Production traces may contain PII (names, emails, SSNs).

**Pipeline:**
1. **Detection:** Regex + NER model to flag PII
2. **Redaction:** Replace PII with placeholders (`<EMAIL>`, `<SSN>`)
3. **Audit trail:** Log what was redacted, when, by whom
4. **Judge training:** Train judge on redacted data (ensures judge doesn't see PII)

```python
import re

def redact_pii(text: str) -> str:
    # Email
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '<EMAIL>', text)
    # SSN
    text = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '<SSN>', text)
    # Phone
    text = re.sub(r'\b\d{3}-\d{3}-\d{4}\b', '<PHONE>', text)
    return text
```

**Audit Trail Requirements:**
- Who triggered the eval?
- What dataset was used?
- What model/judge was used?
- What was the result?
- When was it run?
- What data was redacted?

Store in immutable log (append-only, no deletes).

### Governance Platforms

| Platform | Focus | Key Features | Pricing |
|----------|-------|--------------|---------|
| **Braintrust** | Eval + observability | Dataset versioning, judge management, RBAC | Free 100k rows, $100/1M after |
| **Galileo** | LLM observability | Guardrails, drift detection, hallucination detection | Enterprise (custom) |
| **Credo AI** | Governance + compliance | Bias audits, EU AI Act compliance, model cards | Enterprise (custom) |
| **Lakera** | Safety + red-teaming | Jailbreak detection, adversarial eval, prompt injection defense | Enterprise (custom) |
| **Bifrost** | Alignment + RLHF | Constitutional AI, human preference tuning, reward modeling | Research preview |

## System Design Scenarios

### Scenario A: RAG Eval Pipeline (Financial Services)

**Context:**
- Customer support chatbot for banking
- RAG retrieves from internal knowledge base (policies, FAQs)
- Must be factually accurate (regulatory requirement)
- Volume: 10k queries/day
- SLA: 99.9% accuracy, <5s latency

**Requirements:**
1. Eval every response for faithfulness (no hallucinations)
2. Detect drift (knowledge base updates)
3. Audit trail for compliance
4. Cost: <$1000/month for eval

**Design:**

```
┌──────────────────┐
│  User Query      │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  RAG Retriever   │  Fetch top-5 docs
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  LLM (Sonnet)    │  Generate answer
└────────┬─────────┘
         │
         ├─────────────────────┐
         │                     │
         ▼                     ▼
┌──────────────────┐   ┌──────────────────┐
│  User Response   │   │  Eval Sidecar    │  (async, 10% sample)
└──────────────────┘   └────────┬─────────┘
                                │
                                ▼
                       ┌──────────────────┐
                       │ Faithfulness     │  RAGAS metric
                       │ Judge (Haiku)    │
                       └────────┬─────────┘
                                │
                                ▼
                       ┌──────────────────┐
                       │  Score Store     │  Braintrust
                       │  + Audit Log     │
                       └────────┬─────────┘
                                │
                                ▼
                       ┌──────────────────┐
                       │ Drift Detector   │  Alert on <95% faithfulness
                       └──────────────────┘
```

**Metrics:**
- Faithfulness: >=95% (hard requirement)
- Answer relevance: >=90%
- Citation correctness: >=95%
- Latency (eval): <1s p95

**Cost estimate:**
```
Production:
  10k queries/day × 30 days = 300k queries/month
  Avg 2000 input tokens, 300 output tokens
  Sonnet 4.5: (300k × 2000 × $3/MTok) + (300k × 300 × $15/MTok) = $1,800 + $1,350 = $3,150/month

Eval (10% sample):
  30k queries/month
  Judge: Haiku (cheap)
  Avg 2500 input tokens (context + answer + rubric), 100 output tokens
  Haiku: (30k × 2500 × $0.25/MTok) + (30k × 100 × $1.25/MTok) = $18.75 + $3.75 = $22.50/month

Platform (Braintrust):
  30k rows/month = Free tier

Total eval cost: $22.50/month ✓ (under $1000 budget)
Judge ratio: $22.50 / $3,150 = 0.7% ✓ (under 15%)
```

**Failure modes:**
1. Knowledge base updated, judge still uses old rubric → Solution: Version rubrics with KB snapshots
2. Judge hallucinates, says answer is faithful when it's not → Solution: Human spot-check 1% of judge outputs monthly
3. Drift detector fires false alarms (variance, not real drift) → Solution: Require 2 consecutive windows below threshold

### Scenario B: Agent Deployment Quality Gate (Billing System)

**Context:**
- Agentic system for processing invoices (reads PDF, extracts line items, updates database)
- Replacing manual process (95% accuracy baseline)
- Must not degrade quality
- Volume: 1000 invoices/day
- SLA: >=95% extraction accuracy

**Requirements:**
1. Pre-deployment eval: Agent must score >=95% on holdout test set (100 invoices) before deploy
2. Post-deployment: Monitor extraction accuracy on 10% of production traffic
3. If accuracy drops below 90%, auto-rollback

**Design:**

```
┌──────────────────┐
│  Eval Harness    │  Pre-deployment
│  (100 test PDFs) │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Agent (v2)      │  Extract line items
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Hard Oracle     │  Compare to gold labels
│  (Exact Match)   │  (manually labeled)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Release Gate    │  If pass@1 >=95%, deploy
│                  │  Else: block
└────────┬─────────┘
         │ (Deploy)
         ▼
┌──────────────────┐
│  Production      │
│  (1000 PDFs/day) │
└────────┬─────────┘
         │
         ├─────────────────────┐
         │                     │
         ▼                     ▼
┌──────────────────┐   ┌──────────────────┐
│  Database Update │   │  Online Monitor  │  (10% sample)
└──────────────────┘   └────────┬─────────┘
                                │
                                ▼
                       ┌──────────────────┐
                       │  Spot-Check      │  Human verifies extraction
                       │  (100 PDFs/day)  │
                       └────────┬─────────┘
                                │
                                ▼
                       ┌──────────────────┐
                       │  Accuracy Calc   │  Rolling 7-day window
                       └────────┬─────────┘
                                │
                                ▼
                       ┌──────────────────┐
                       │  Auto-Rollback   │  If <90%, revert to v1
                       └──────────────────┘
```

**Metrics:**
- Pre-deploy pass@1: >=95%
- Production accuracy (7-day rolling): >=95%
- Rollback trigger: <90% for 2 consecutive days

**Cost estimate:**
```
Pre-deployment eval:
  100 test invoices
  Agent execution: 100 × $0.10/invoice = $10
  One-time cost per release

Online monitoring:
  1000 invoices/day × 10% = 100/day × 30 days = 3000/month
  Human spot-check: 100/day × 30 × $5/hour × 0.1 hours = $1,500/month
  Agent execution: 3000 × $0.10 = $300/month

Total: $1,800/month (mostly human labor, not LLM)
```

**Failure modes:**
1. Test set not representative → Production accuracy lower than test accuracy → Solution: Refresh test set quarterly from production samples
2. Human spot-checks are slow → Accuracy calculation lags → Solution: Async queue, calculate accuracy with 1-day delay
3. Auto-rollback triggers during maintenance window (false alarm) → Solution: Disable auto-rollback during announced maintenance

### Scenario C: Dual-Oracle for Policy-Bound Support Agent

**Context:**
- Customer support agent with strict policy constraints (refund limits, eligibility rules)
- Must follow policy exactly (hard oracle)
- But also be helpful/empathetic (soft oracle)
- Volume: 5k conversations/day
- SLA: 100% policy compliance, >=90% helpfulness

**Design:**

```
┌──────────────────┐
│  User Message    │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Agent Response  │
└────────┬─────────┘
         │
         ├─────────────────────────────┐
         │                             │
         ▼                             ▼
┌──────────────────┐         ┌──────────────────┐
│  Hard Oracle     │         │  Soft Oracle     │
│  (Policy Check)  │         │  (LLM Judge)     │
│                  │         │                  │
│  - Refund amount │         │  - Helpfulness   │
│  - Eligibility   │         │  - Empathy       │
│  - Escalation    │         │  - Clarity       │
└────────┬─────────┘         └────────┬─────────┘
         │                             │
         └──────────┬──────────────────┘
                    │
                    ▼
           ┌──────────────────┐
           │  Dual-Oracle      │  Hard: must pass
           │  Aggregator       │  Soft: >=90% target
           └────────┬─────────┘
                    │
                    ├─────────────────┬─────────────────┐
                    │                 │                 │
                    ▼                 ▼                 ▼
           ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
           │ Pass          │  │ Soft Fail    │  │ Hard Fail    │
           │ (ship)        │  │ (warn)       │  │ (block)      │
           └───────────────┘  └──────────────┘  └──────────────┘
```

**Hard Oracle Rules:**
```python
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
```

**Soft Oracle (Judge):**
```python
def helpfulness_oracle(response: str) -> float:
    """Soft oracle: helpfulness score 0-1"""
    rubric = """Score the response on:
    1. Helpfulness (addresses user concern)
    2. Empathy (acknowledges frustration)
    3. Clarity (easy to understand)
    
    Return a score 0-1."""
    
    return llm_judge(response, rubric)
```

**Aggregation:**
```python
def dual_oracle_decision(response: str, context: Dict) -> Dict[str, Any]:
    hard_pass = policy_oracle(response, context)
    soft_score = helpfulness_oracle(response)
    
    if not hard_pass:
        return {"decision": "BLOCK", "reason": "Policy violation"}
    
    if soft_score < 0.9:
        return {"decision": "WARN", "reason": f"Low helpfulness: {soft_score:.2f}"}
    
    return {"decision": "PASS"}
```

**Metrics:**
- Hard oracle pass rate: 100% (required)
- Soft oracle avg score: >=0.9
- False positive rate (hard oracle): <1% (manual review monthly)

### Scenario D: RAG Faithfulness CI + Citation Eval

**Context:**
- Legal research assistant (RAG over case law)
- Every answer must cite sources
- CI must catch hallucinations before deploy
- SLA: 100% citation correctness

**Design:**

```
┌──────────────────┐
│  Pull Request    │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  CI Trigger      │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Eval Suite      │  100 legal questions (holdout)
│  (RAGAS)         │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Agent Run       │  Generate answers + citations
└────────┬─────────┘
         │
         ├─────────────────────────────────┐
         │                                 │
         ▼                                 ▼
┌──────────────────┐             ┌──────────────────┐
│  Faithfulness    │             │  Citation Check  │
│  (RAGAS)         │             │  (Hard Oracle)   │
│                  │             │                  │
│  Claims grounded │             │  - Source exists │
│  in context?     │             │  - Claim in src  │
└────────┬─────────┘             └────────┬─────────┘
         │                                 │
         └──────────┬──────────────────────┘
                    │
                    ▼
           ┌──────────────────┐
           │  Aggregate       │  Faithfulness >=95%
           │  Metrics         │  Citation >=100%
           └────────┬─────────┘
                    │
                    ▼
           ┌──────────────────┐
           │  CI Gate         │  Pass → Merge
           │                  │  Fail → Block
           └──────────────────┘
```

**Citation Oracle:**
```python
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
```

**Metrics:**
- Faithfulness (RAGAS): >=95%
- Citation correctness: 100% (zero tolerance)
- Coverage: >=90% of claims have citations

**Cost:**
```
Per CI run:
  100 test cases
  Agent execution: $10 (cached prompts)
  RAGAS faithfulness judge: 100 × $0.02 = $2
  Citation oracle: Free (hard-coded check)
  
Total: $12/run

Runs per day: ~10 PRs
Monthly: $12 × 10 × 30 = $3,600
```

**Failure modes:**
1. RAGAS judge hallucinates (says claim is grounded when it's not) → Solution: Human spot-check 10 failures/month
2. Citation format changes (parser breaks) → Solution: Schema validation + unit tests
3. Retrieved docs change between eval and production → Solution: Pin doc versions in test set

## Interview Q&A

### Q1: How would you design an eval system for a customer support chatbot?

**Answer:**

**Step 1: Define success metrics (layered)**
- L1 (Task success): Did the agent resolve the issue? (binary)
- L2 (Trajectory): Did it follow the right steps? (tool selection, escalation)
- L3 (Quality): Was the response helpful, empathetic, clear? (LLM judge)
- L4 (Constraints): Latency <5s, cost <$0.10/query

**Step 2: Choose eval paradigm**
- Pointwise: Judge each response independently (for quality)
- Reference-based: Compare to expected resolution (for task success)
- Pairwise: A/B test new prompt vs baseline

**Step 3: Hard oracle + Soft oracle**
- Hard: Did the agent use correct tools? (e.g., check_order_status, issue_refund)
- Soft: LLM judge scores helpfulness, empathy (1-5 scale with rubric)

**Step 4: Dataset**
- Holdout test set: 200 conversations (manually labeled)
- Stratify by issue type (refund, shipping, technical support)
- Update quarterly from production samples (avoid staleness)

**Step 5: CI Integration**
- Pre-deployment: Run full suite (200 cases), require >=90% task success, >=4.0 avg quality
- Post-deployment: Monitor 10% of production traffic, alert if metrics drop >5pp

**Step 6: Cost control**
- Use Sonnet 4.5 as judge (not Opus) to stay under 15% cost ratio
- Sample 10% of production (not 100%)
- Cache rubrics (save 50% on judge input tokens)

**Step 7: Failure modes**
- Judge bias: Audit for length, position bias monthly
- Contamination: Rotate test set, never publish
- Drift: Track judge consistency (run same trace 3x, check variance)

**Trade-off:** Higher eval coverage (50%+) improves signal but costs more. Start at 10%, increase only if drift detection is noisy.

### Q2: Explain pass@k vs pass^k. When would you use each?

**Answer:**

**pass@k (at least one success):**
- Definition: Probability that at least one of k samples solves the task
- Formula: `1 - C(n-c, k) / C(n, k)` where n=total samples, c=correct, k=target
- Use case: Code generation where you can run multiple candidates and pick the best (e.g., GitHub Copilot suggests 3 completions)
- Interpretation: "If I sample k times, what's the chance at least one is right?"

**pass^k (all successes):**
- Definition: Probability that all k samples solve the task
- Formula: `C(c, k) / C(n, k)`
- Use case: Reliability-critical applications (medical diagnosis, financial advice) where every invocation must succeed
- Interpretation: "If I sample k times, what's the chance all k are right?"

**Key difference:**
- pass@k >= pass^k always (at-least-one >= all)
- Gap shows variance: narrow gap = consistent agent, wide gap = unreliable

**Example:**
```
n=10 samples, c=6 correct

pass@3 = 1 - C(4,3)/C(10,3) = 1 - 4/120 = 0.967 (96.7%)
pass^3 = C(6,3)/C(10,3) = 20/120 = 0.167 (16.7%)

Gap: 80pp → High variance, agent is inconsistent
```

**When to use:**
- pass@k: Benchmarking models (HumanEval, MBPP), selecting code suggestions
- pass^k: SLA guarantees (tau-bench), production reliability targets

**Interview follow-up:** "How many samples do you need to estimate pass@10 accurately?"
- Answer: Draw n>=10 samples per task. For 100 tasks, that's 1000+ agent runs. Expensive but necessary for unbiased estimate.

### Q3: What are the failure modes of LLM-as-judge? How do you mitigate?

**Answer:**

**Failure Mode 1: Position Bias**
- Judge prefers first or last response in pairwise comparison
- Zheng et al.: GPT-4 changed preference ~1/3 of cases when order swapped
- Mitigation: Swap order, run twice, aggregate

**Failure Mode 2: Length/Verbosity Bias**
- Longer responses score higher regardless of quality
- AlpacaEval: Optimized for verbosity, not helpfulness
- Mitigation: Normalize by length, explicit rubric ("brevity is valued")

**Failure Mode 3: Self-Preference Bias**
- Model prefers its own outputs over competitors
- Mitigation: Use different model as judge (GPT-4 judge for Claude output)

**Failure Mode 4: Rubric Position Bias**
- Judge scores first rubric criterion higher (2026 finding)
- Mitigation: Randomize rubric order, aggregate across permutations

**Failure Mode 5: Compounding Biases**
- Multiple biases interact (FairJudge Feb 2026: >50% error rates)
- Mitigation: Ensemble judges (3 models, majority vote)

**Validation protocol (4 steps):**
1. Inter-judge agreement: Run 2+ models, measure Cohen's kappa (target >=0.6)
2. Human alignment: Sample 100 judgments, compare to human labels (target >=80% agreement)
3. Bias audit: Swap orders, normalize lengths, cross-model judging
4. Calibration: Anchor judge on 5-10 known-good/known-bad examples before eval

**Production mitigation:**
- Chain-of-thought: Force judge to explain reasoning first (improves agreement 0.55 → 0.75 kappa)
- Consistency check: Run same trace 3x, alert if std dev >0.1
- Human spot-check: Manually review 1% of judge outputs monthly

**Cost:** Validation is expensive (3x judge calls for ensemble). Only do this for high-stakes evals (medical, legal, financial).

### Q4: How do you prevent contamination in eval datasets?

**Answer:**

**Contamination:** Model has seen the test data during training → inflated scores.

**Detection:**
1. **Canary tokens:** Embed unique IDs in test data, search for them in model outputs
2. **Time-based splits:** Test on data after model's cutoff date (GAIA 2: created after GPT-4 training)
3. **Performance discontinuities:** If model scores 95% on public benchmark, 60% on holdout → contamination

**Prevention:**

| Control | Description | Example | Effectiveness |
|---------|-------------|---------|---------------|
| **Holdout sets** | Unpublished tests, rotated periodically | Gemini leaderboard (rotates monthly) | High (until leaked) |
| **Canary tokens** | Unique IDs to detect leaks | SWE-bench ticket IDs | High (detects, doesn't prevent) |
| **Time-based splits** | Test on post-cutoff data | GAIA 2 | Medium (models still learn patterns) |
| **Dynamic generation** | Generate new tasks on-the-fly | MATH (infinite algebra problems) | High (hard to memorize) |
| **Human verification** | Manual review of every pass | GAIA 2, SWE-bench Verified | Very high (expensive) |

**SWE-bench case study:**
- 19.78% of "passes" were semantically incorrect (hardcoded test outputs)
- Solution: SWE-bench Verified (731 tasks, human-reviewed for semantic correctness)

**Best practice:**
- Public benchmark: Use for directional signal only, never for release decisions
- Private holdout: Update quarterly from production samples, never publish
- Rotation: Refresh 20% of test set every month (keeps test fresh)

**Interview follow-up:** "How do you balance freshness vs stability of eval datasets?"
- Answer: Keep 80% stable (track regression), rotate 20% (avoid staleness). Report both "stable score" and "fresh score."

### Q5: What's your eval strategy for a RAG system?

**Answer:**

**Metrics (4 dimensions):**

1. **Faithfulness (most critical)**
   - Are claims in the answer grounded in retrieved context?
   - RAGAS formula: (Claims supported) / (Total claims)
   - Target: >=95%

2. **Answer Relevance**
   - Does the answer address the query?
   - LLM judge: "Rate relevance 1-5"
   - Target: >=4.0

3. **Citation Correctness**
   - Are citations accurate and verifiable?
   - Hard oracle: Check if cited source exists and contains the claim
   - Target: 100% (zero tolerance)

4. **Retrieval Quality**
   - Precision: How many retrieved docs are relevant?
   - Recall: How many relevant docs were retrieved?
   - Target: Precision >=80%, Recall >=90%

**Eval pipeline:**

```
Query → Retriever → Top-5 docs → LLM → Answer + citations
                ↓                         ↓
         Retrieval eval          Faithfulness eval
         (Precision/Recall)      (RAGAS + Citation oracle)
```

**Test set:**
- 200 queries (manually labeled with expected answers + relevant docs)
- Stratify by query type (factual, comparison, procedural)
- Update quarterly from production logs

**Hard oracle (Citation):**
```python
def citation_oracle(answer, retrieved_docs):
    citations = extract_citations(answer)
    for cite_num in citations:
        claim = extract_claim_for_citation(answer, cite_num)
        cited_doc = retrieved_docs[cite_num - 1]
        if claim not in cited_doc:
            return False  # Citation incorrect
    return True
```

**Soft oracle (Faithfulness):**
- Use RAGAS (LLM judge extracts claims, checks grounding)
- ~95% human agreement (validated)

**Cost control:**
- Use Haiku for faithfulness judge (cheap)
- Sample 10% of production traffic
- Cache retrieval results (avoid re-fetching same docs)

**Failure modes:**
1. Retriever returns irrelevant docs → Faithfulness score high (no false claims) but answer is vague → Solution: Track answer relevance separately
2. Judge hallucinates (says claim is grounded when it's not) → Solution: Human spot-check 10 failures/month
3. Retrieved docs change (KB updated) → Solution: Version KB snapshots, re-run eval on old snapshot to catch regressions

### Q6: How do you evaluate agent trajectory quality (not just end-to-end success)?

**Answer:**

**Why trajectory matters:**
- End-to-end success doesn't explain why the agent succeeded/failed
- Trajectory quality predicts debugging effort (bad trajectory = hard to fix)

**Evaluation layers:**

**Layer 1: Tool Selection**
- Metric: Accuracy = (Correct tools) / (Total tools called)
- Target: >=95%
- Example failure: Agent calls `search_web` when `read_file` was correct

**Layer 2: Argument Correctness**
- Metric: (Correct args) / (Total args)
- Target: >=90%
- Example failure: `read_file(path="wrong.txt")` instead of `read_file(path="correct.txt")`

**Layer 3: Efficiency**
- Metric: (Minimum tool calls to solve) / (Actual tool calls)
- Target: >=0.8 (no more than 25% overhead)
- Example failure: Agent calls `search` 10 times when 2 would suffice

**Layer 4: Error Recovery**
- Metric: (Successful retries) / (Total errors)
- Target: >=70%
- Example failure: Agent gets API error, gives up instead of retrying

**Layer 5: Repetition**
- Metric: (Duplicate tool calls) / (Total tool calls)
- Target: <5%
- Example failure: Agent calls same API 3 times with identical params (loop)

**Aggregation: Geometric Mean**
```python
def score_trajectory(steps):
    step_scores = [score_step(s) for s in steps]
    # Geometric mean: (s1 * s2 * ... * sn)^(1/n)
    return np.prod(step_scores) ** (1 / len(step_scores))
```

Why geometric vs arithmetic? Geometric penalizes any single bad step more (reflects reality: one mistake can derail entire task).

**Grading modes:**

| Mode | Description | Use Case |
|------|-------------|----------|
| **Exact matching** | Trajectory must match gold exactly (order + args) | High-fidelity reproduction (SWE-bench) |
| **Set-based** | Tool calls match as set (order doesn't matter) | Tasks with no canonical order |
| **Partial credit** | Score = overlap / expected | Debugging, explainability |
| **LLM judge** | Judge scores trajectory on rubric | Open-ended tasks |

**Example (SWE-bench):**
```
Expected: [read_file("bug.py"), edit_file("bug.py", fix), run_tests()]
Agent:    [search_files("bug"), read_file("bug.py"), edit_file("bug.py", fix), run_tests()]

Exact match: Fail (extra search step)
Set-based: Pass (all required tools called)
Partial credit: 3/3 = 100% (all required steps present)
```

**Trade-off:** Exact matching is strict but clear. Set-based is lenient but may miss order-dependent bugs.

### Q7: How do you handle the cost of evaluation at scale?

**Answer:**

**Problem:** Evaluating 100% of production traffic with LLM-as-judge can cost more than production itself.

**Guardrail: Judge cost should be <15% of production LLM cost.**

**Strategies:**

**1. Sampling (50-90% cost reduction)**
- Evaluate 10% of traffic, not 100%
- Use reservoir sampling to maintain random sample
- Trade-off: Higher variance (need more samples to detect drift)

**2. Cheap judge first, escalate (60-80% cost reduction)**
- Use Haiku/Sonnet for first-pass, Opus only for edge cases
- Example: Haiku judges all, if score <0.5, escalate to Opus
- Trade-off: May miss subtle quality gaps

**3. Hard oracle preference (80-95% cost reduction)**
- If exact match passes, skip judge
- Only invoke judge on failures (for debugging)
- Trade-off: Only works when ground truth exists

**4. Prompt caching (40-60% cost reduction)**
- Cache rubric + few-shot examples (repeated across evals)
- Anthropic prompt caching: 90% discount on cached tokens
- Trade-off: Only helps if rubric is reused (not task-specific prompts)

**5. Batching (10-20% cost reduction)**
- Batch 100+ requests to amortize API overhead
- Trade-off: Higher latency, infra complexity

**Example (RAG eval with cost optimization):**

```
Baseline:
  10k queries/day, 100% eval coverage
  Judge: Opus 5 ($15/MTok input, $75/MTok output)
  Avg 3000 input tokens, 200 output tokens
  Cost: (10k × 3000 × $15/MTok) + (10k × 200 × $75/MTok) = $450 + $150 = $600/day

Optimized:
  1. Sample 10% → 1k queries/day → $60/day
  2. Use Sonnet 4.5 judge ($3/MTok input, $15/MTok output) → $6 + $3 = $9/day
  3. Cache rubric (500 tokens, 90% discount) → Save $0.50/day
  
Final: $8.50/day (98.6% cost reduction)
```

**Capacity planning:**
- Production rate: 1000 req/s
- Eval sample rate: 10% → 100 req/s to eval
- Per-eval latency: 1s (judge call)
- Workers needed: 100 (1 req/s each)
- Cost: 100 workers × $0.01/hour = $1/hour = $720/month infra

**When to increase eval coverage:**
- High-stakes domain (medical, legal, financial) → 50-100%
- Mature system with low drift → 1-5%
- New deployment (first month) → 50% (validate assumptions)

### Q8: Explain the three-layer eval stack (end-to-end, trajectory, component).

**Answer:**

**Analogy:** Debugging a failing test
- L1 (End-to-end): Test failed (binary signal)
- L2 (Trajectory): Which step failed? (localize bug)
- L3 (Component): Why did that step fail? (root cause)

**Layer 1: End-to-End (Task Success)**
- Question: Did the agent accomplish the goal?
- Metric: pass@k, pass^k, exact match
- Example: SWE-bench (did it resolve the GitHub issue?), GAIA (correct answer?)
- Use: Release gate (deploy only if >=90% task success)

**Layer 2: Trajectory Quality**
- Question: How did the agent get to the answer?
- Metrics: Tool selection accuracy, argument correctness, efficiency
- Example: Agent solved the task but took 20 steps instead of 5
- Use: Debugging (why is latency high?), optimization (prune unnecessary steps)

**Layer 3: Component-Level**
- Question: Are individual scaffold pieces working correctly?
- Metrics: Tool selection in isolation, argument generation, error recovery
- Example: Test "given state S, does agent choose correct tool?" without running full task
- Use: Unit tests (catch regressions in tool selection logic before integration)

**How they relate:**
```
L1 (Task success) = f(L2 trajectory quality)
L2 (Trajectory quality) = g(L3 component correctness)

If L1 fails → Check L2 to localize failure
If L2 fails → Check L3 to find root cause
```

**Example (Customer support agent):**

```
L1: Did agent resolve the issue?
  → No (end-to-end failure)

L2: Trajectory analysis
  Step 1: check_order_status(order_id="12345") → Success
  Step 2: issue_refund(order_id="12345", amount=100) → Error ("amount exceeds order total")
  Step 3: Agent stops (no retry)
  → Failure at Step 2 (incorrect refund amount)

L3: Component analysis
  Test: "Given order total $50, what refund amount should agent propose?"
  Expected: $50 (full refund)
  Agent: $100 (incorrect)
  → Root cause: Agent hallucinates refund amount, doesn't read order total from context
```

**Trade-off:**
- L1 is cheap to measure (one boolean), but gives no debugging signal
- L3 is expensive to measure (need labeled data for each component), but gives precise root cause
- L2 is the sweet spot: moderate cost, actionable debugging signal

### Q9: How do you set up CI for agent evaluation?

**Answer:**

**Goal:** Block deploy if eval suite fails (regression protection).

**CI Pipeline:**

```
┌──────────────┐
│ Pull Request │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Lint + Unit  │  Fast checks (10s)
│ Tests        │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Eval Suite   │  Agent eval (5-30 min)
│ (100 tasks)  │
└──────┬───────┘
       │
       ├─────────────────┬─────────────────┐
       │                 │                 │
       ▼                 ▼                 ▼
┌──────────┐    ┌──────────────┐   ┌──────────────┐
│ Task     │    │ Trajectory   │   │ Cost + Latency│
│ Success  │    │ Quality      │   │ Check         │
└──────┬───┘    └──────┬───────┘   └──────┬───────┘
       │               │                   │
       └───────┬───────┴───────────────────┘
               │
               ▼
      ┌──────────────┐
      │ Release Gate │  Pass thresholds?
      └──────┬───────┘
             │
             ├─────────────┬──────────────┐
             │             │              │
             ▼             ▼              ▼
        ┌────────┐   ┌─────────┐   ┌──────────┐
        │ Pass   │   │ Warn    │   │ Fail     │
        │ (merge)│   │ (review)│   │ (block)  │
        └────────┘   └─────────┘   └──────────┘
```

**Thresholds:**

| Metric | Pass | Warn | Fail |
|--------|------|------|------|
| **Task success (pass@1)** | >=90% | 85-90% | <85% |
| **Trajectory score** | >=0.85 | 0.75-0.85 | <0.75 |
| **Cost per task** | <=$0.10 | $0.10-$0.15 | >$0.15 |
| **p95 latency** | <=5s | 5-10s | >10s |

**Handling flakiness:**
- Non-deterministic agents cause CI flakiness (pass locally, fail in CI)
- Solution: pass@3 metric (if any of 3 runs passes, test passes)
- Trade-off: Slower CI (3x execution time), but tolerates variance

**Checkpointing:**
- Long eval suites (SWE-bench: 10 hours) risk losing progress on crash
- Save checkpoint every 10 tasks, resume on failure

**Caching:**
- Cache LLM responses by (prompt, model, temperature) to avoid redundant API calls
- Invalidate cache on model/prompt change

**Cost control:**
- Limit eval suite to 100 tasks (not 2,294 full SWE-bench)
- Use cheaper model (Sonnet instead of Opus) for CI
- Full eval suite runs nightly (not on every PR)

**Example (.github/workflows/eval.yml):**

```yaml
name: Agent Eval CI

on:
  pull_request:
    branches: [main]

jobs:
  eval:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Install dependencies
        run: pip install -r requirements.txt
      
      - name: Run eval suite
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          python eval_suite.py \
            --num-tasks 100 \
            --model claude-sonnet-4.5 \
            --pass-at-k 3 \
            --checkpoint eval_checkpoint.json
      
      - name: Check thresholds
        run: |
          python check_thresholds.py \
            --task-success-min 0.90 \
            --trajectory-score-min 0.85 \
            --cost-max 0.10 \
            --latency-p95-max 5000
      
      - name: Upload results
        uses: actions/upload-artifact@v3
        with:
          name: eval-results
          path: eval_results.json
```

**Failure modes:**
1. CI is too slow (30+ min) → Developers bypass it → Solution: Reduce test set to 50 tasks, run full suite nightly
2. Flakiness causes false failures → Developers ignore CI → Solution: pass@3, temperature=0
3. Cost blows up ($100/PR) → Finance complains → Solution: Use Sonnet, cache aggressively

### Q10: What's the difference between online eval and offline eval?

**Answer:**

| Dimension | Offline Eval | Online Eval |
|-----------|--------------|-------------|
| **When** | Pre-deployment (CI, nightly) | Post-deployment (production) |
| **Data** | Holdout test set (labeled) | Live production traffic (unlabeled) |
| **Coverage** | 100% of test set | 1-10% of production (sampled) |
| **Latency** | No user impact (async) | Must not slow user response |
| **Cost** | Fixed (test set size) | Scales with traffic |
| **Goal** | Release gate (block bad deploys) | Regression detection (catch drift) |
| **Metrics** | Task success, trajectory quality | Drift, anomaly detection |

**Offline eval:**
- Run in CI before deploy
- Test set: 100-1000 labeled examples
- Hard oracle (exact match) + soft oracle (judge)
- Threshold: >=90% task success → deploy
- Example: SWE-bench, GAIA, HumanEval

**Online eval:**
- Run continuously in production
- Sample 10% of traffic (reservoir sampling)
- Compare current window vs baseline (drift detection)
- Alert if metrics drop >5pp
- Example: LangSmith, Braintrust, Phoenix

**Why both?**
- Offline catches known regressions (test set)
- Online catches unknown issues (edge cases not in test set)

**Example workflow:**

```
┌─────────────────┐
│ Offline Eval    │  (CI)
│ pass@1 = 92%    │  → Pass → Deploy
└─────────┬───────┘
          │
          ▼
┌─────────────────┐
│ Production      │
│ (live traffic)  │
└─────────┬───────┘
          │
          ▼
┌─────────────────┐
│ Online Eval     │  (10% sample)
│ pass@1 = 85%    │  → Alert (7pp drop)
└─────────┬───────┘
          │
          ▼
┌─────────────────┐
│ Investigation   │  What changed?
│ - New user types?
│ - API change?
│ - Model drift?
└─────────────────┘
```

**Hybrid approach (best practice):**
- Offline: 100 high-quality labeled examples (curated)
- Online: 1000s of production samples (diverse but unlabeled)
- Human-in-loop: Label 1% of online failures, add to offline test set (closes the loop)

**Cost:**
```
Offline:
  100 tasks × $0.10/task = $10/run
  Runs: 10 PRs/day × 30 days = 300 runs/month
  Cost: $3,000/month

Online:
  10k production queries/day × 10% sample = 1k/day
  1k × $0.02/eval = $20/day
  Cost: $600/month

Total: $3,600/month
```

**Interview follow-up:** "How do you handle unlabeled data in online eval?"
- Answer: Use weak labels (heuristics, user feedback, implicit signals like retry rate) or human-in-loop labeling (sample 1% for manual review).

### Q11: How do you validate an LLM judge before deploying it?

**Answer:**

**Validation protocol (5 steps):**

**1. Inter-judge agreement**
- Run 2-3 different models as judges on same 100-sample dataset
- Measure Cohen's kappa (inter-rater agreement)
- Target: kappa >=0.6 (substantial agreement)
- If kappa <0.4, judges are unreliable → Don't deploy

**2. Human alignment**
- Sample 100-200 judgments, have 3 humans independently label
- Measure judge-human agreement
- Target: >=80% agreement with human majority vote
- RAGAS faithfulness: ~95% human agreement (gold standard)

**3. Bias audit**
- Position bias: Swap A/B order in pairwise comparison, check if preference flips
- Length bias: Compare judgments on (long, good) vs (short, good) responses
- Self-preference bias: Use GPT-4 judge on Claude output, check if biased toward GPT-4 outputs

**4. Calibration**
- Show judge 5-10 anchor examples before eval (score-1 through score-5 examples)
- Check if judge maintains calibration across eval (scores stay consistent)

**5. Consistency check**
- Run same trace through judge 3 times (with temperature=0)
- Measure std dev of scores
- Target: std dev <0.1 (judge is consistent)

**Example validation dataset:**

```python
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
```

**Failure criteria (don't deploy if):**
- Judge-human agreement <70%
- Inter-judge kappa <0.4
- Bias audit shows >20% preference flip on order swap
- Consistency check shows std dev >0.2

**Monitoring (post-deployment):**
- Re-validate monthly (models get updated, behavior drifts)
- Human spot-check 1% of judge outputs (catch silent failures)
- Track judge cost ratio (should stay <15% of production cost)

**Trade-off:** Validation is expensive (human labeling, multiple judge runs). Only do full validation for high-stakes domains (medical, legal, financial).

### Q12: How would you debug a failing eval suite (offline eval passes, online eval fails)?

**Answer:**

**Symptom:** Offline eval shows 95% pass rate, but online eval shows 85% → 10pp gap.

**Hypothesis tree:**

**1. Test set is not representative**
- Offline test set is too easy or doesn't cover production edge cases
- Check: Analyze online failures, look for patterns (new query types, edge cases)
- Fix: Add failing production samples to offline test set (close the loop)

**2. Data distribution shift**
- Production traffic changed (new user types, new use cases)
- Check: Compare offline test set distribution vs online traffic (query length, topic)
- Fix: Stratify test set by query type, ensure coverage

**3. Infrastructure differences**
- Offline runs in controlled environment (mocked APIs, fixed DB state)
- Online hits real APIs (rate limits, timeouts, stale data)
- Check: Compare tool failure rates (offline vs online)
- Fix: Add API retry logic, test with live APIs in staging

**4. Non-determinism**
- Agent is non-deterministic (temperature >0, tool execution variance)
- Offline eval runs once per task, online runs many times (variance shows up)
- Check: Run offline eval with pass@10, see if gap narrows
- Fix: Report pass@3 instead of pass@1 (tolerate variance)

**5. Measurement error**
- Online eval uses weak labels (heuristics, not ground truth)
- Offline uses gold labels (manually verified)
- Check: Sample 100 online "failures", manually verify (are they real failures?)
- Fix: Improve weak label quality (better heuristics, human-in-loop)

**6. Contamination**
- Offline test set leaked into training data → inflated offline scores
- Online sees novel data → true performance lower
- Check: Test on fresh holdout set (created after model training)
- Fix: Rotate test set quarterly, never publish

**Debugging workflow:**

```
Step 1: Sample 100 online failures
Step 2: Manually label (are they real failures? or labeling errors?)
Step 3: If real failures:
  → Add to offline test set
  → Re-run offline eval (should now fail, reproducing online issue)
Step 4: If not real failures:
  → Fix online labeling heuristic
  → Re-run online eval (failure rate should drop)
```

**Example (RAG system):**

```
Offline: 95% faithfulness (RAGAS judge on 100 labeled examples)
Online: 85% faithfulness (heuristic: check if answer contains "I don't know")

Investigation:
  - Sample 50 online "failures"
  - Manual review: 30 are real failures (hallucinations), 20 are false positives (correct refusals labeled as failures)
  
Root cause: Online heuristic counts refusals as failures

Fix:
  - Update heuristic: Only flag as failure if answer makes a claim (not refusal)
  - Re-run: Online faithfulness now 91% (closer to offline)
```

**Prevention:**
- Continuously add online failures to offline test set (closes the loop)
- Run offline eval on production-sampled data monthly (catch distribution shift early)
- Monitor offline/online gap as a metric (alert if >5pp)

## Key Numbers to Memorize

### Benchmark Anchors

| Benchmark | Size | Human Performance | GPT-4 Performance | Notes |
|-----------|------|-------------------|-------------------|-------|
| **SWE-bench** | 2,294 | ~90% (dev) | ~25% (2025) | Real GitHub issues, hard |
| **SWE-bench Lite** | 500 | ~90% | ~30% | Easier subset |
| **SWE-bench Verified** | 731 | ~90% | ~35% | Manually verified semantic correctness |
| **GAIA** | 466 | 92% | 15% (GPT-4+plugins) | Multi-step, real-world tasks |
| **GAIA 2** | 690 | 92% | ~25% (est) | Contamination-resistant version |
| **HumanEval** | 164 | ~97% | 67% (GPT-4) | Code generation, unit tests |
| **HumanEval+** | 164 (80x tests) | ~97% | 48% (GPT-4) | Scaled test coverage → 19pp drop |
| **MATH** | 12,500 | ~90% (AMC level) | 42% (GPT-4) | Competition math problems |
| **HealthBench** | 5,000 | ~85% (clinicians) | ~70% (GPT-4) | Medical conversations, 48,562 criteria |

### Pass@k Inflation

| Study | Baseline (pass@1) | Intervention | New (pass@1) | Lift |
|-------|-------------------|--------------|--------------|------|
| **Anthropic tau-airline** | 33.2% | Extended thinking (Think tool) | 58.4% | +25.2pp |
| **HumanEval → HumanEval+** | 67% (GPT-4) | 80x test coverage | 48% | -19pp |
| **On Randomness** | Varies | Temperature 0 (repeat runs) | SD >1.5pp | High variance even at T=0 |

### LLM Judge Reliability

| Metric | Target | Best-in-Class | Notes |
|--------|--------|---------------|-------|
| **Human agreement** | >=80% | ~95% (RAGAS faithfulness) | Measure on 100+ samples |
| **Inter-judge kappa** | >=0.6 | ~0.75 (with CoT) | Cohen's kappa between judges |
| **Position bias** | <10% flip rate | ~33% (GPT-4, Zheng) | Swap A/B order, check preference change |
| **Consistency (std dev)** | <0.1 | Varies by model | Run same input 3x, measure variance |

### Cost Ratios

| Ratio | Target | Typical (Unoptimized) | Notes |
|-------|--------|----------------------|-------|
| **Judge cost / Production cost** | <15% | 50-400% | Use cheaper judge, sampling, caching |
| **Eval infra / LLM cost** | <10% | 5-20% | Workers, storage, platform fees |

## Common Failure Modes

| Failure Mode | Symptom | Root Cause | Mitigation |
|--------------|---------|------------|------------|
| **Offline/Online gap** | Offline 95%, Online 85% | Test set not representative | Add production samples to test set |
| **Flaky CI** | Eval passes locally, fails in CI | Non-determinism (temperature, tools) | Use pass@3, temperature=0 |
| **Judge hallucination** | Judge says answer is correct when it's wrong | LLM judge errors | Spot-check 1% monthly, ensemble judges |
| **Position bias** | Judge preference flips on order swap | LLM judge bias | Swap order, aggregate |
| **Length bias** | Longer responses score higher | LLM judge bias | Normalize by length, explicit rubric |
| **Contamination** | Offline scores inflate over time | Test set leaked into training | Rotate test set quarterly, holdout unpublished |
| **Lucky passes** | Agent succeeds for wrong reason | Task is under-specified or has multiple solutions | AgentLens analysis, run pass@k |
| **Eval cost explosion** | Judge costs >100% of production | 100% coverage + expensive judge | Sample 10%, use cheap judge |
| **Drift false positives** | Alerts fire but no real issue | High variance in online eval | Require 2 consecutive windows, larger sample size |
| **Citation hallucination** | Agent cites non-existent sources | RAG system doesn't validate citations | Hard oracle: check source exists in retrieved docs |
| **Test set staleness** | Offline scores stay high, online drops | Distribution shift (production evolved) | Refresh test set quarterly from production |
| **Judge inconsistency** | Same input scored differently on retries | High temperature, non-deterministic judge | Temperature=0, consistency checks |
| **Semantic incorrectness** | Passes unit tests but wrong logic | Tests don't cover edge cases (SWE-bench 19.78%) | HumanEval+ pattern (scale tests 80x) |
| **Retry inflation** | pass@1 overstates due to retries | Agent retries failed tasks | Report "pass@1 (no retry)" separately |
| **Judge cost loop** | Judge needs full trajectory → tokens exceed production | Long agent trajectories | Summarize trajectory, component-level grading |

---

**End of LLM & Agent Evaluation**

This consolidated document merges all unique content from GPT, Opus, and Grok sources. All metrics, formulas, code samples, architecture diagrams, benchmarks, cost analyses, trade-offs, and interview Q&A are preserved in full detail.

