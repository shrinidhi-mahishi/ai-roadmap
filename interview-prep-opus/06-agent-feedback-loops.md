# Module 06: Agent Feedback Loops

## What Is This?

Think of a feedback loop like a basketball player reviewing game tape. The player
shoots, watches the replay, sees what went wrong, and adjusts the next shot. Without
the tape, the player just keeps shooting the same way. Agent feedback loops are the
same idea applied to AI systems: the agent acts, observes the outcome, evaluates it,
and adjusts. The adjustment can happen in-conversation (self-reflection), across
conversations (memory), or across training cycles (RLHF/DPO). Without feedback loops,
an agent is a static prompt executor. With them, it can improve.

## Why It Matters

Every production AI system that ships v2 uses some form of feedback loop. The
difference between a demo and a product is whether the system learns from its
mistakes. As a Director/VP of AI, you will own the decision of which feedback
mechanisms to invest in -- each with radically different cost, latency, and risk
profiles.

---

## Part 1: System Topology & Data Flow

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CONTROL PLANE                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐                 │
│  │  Experiment   │  │  Eval        │  │  Rollout      │                 │
│  │  Registry     │  │  Orchestrator│  │  Controller   │                 │
│  │  (Braintrust) │  │  (Langfuse)  │  │  (A/B + %gate)│                 │
│  └──────┬───────┘  └──────┬───────┘  └───────┬───────┘                 │
│         │                 │                   │                         │
│         └────────┬────────┴───────────────────┘                        │
│                  │                                                      │
│         ┌────────▼────────┐                                            │
│         │  Policy Store   │  ← thresholds, eval criteria, model tier   │
│         └────────┬────────┘                                            │
└──────────────────┼─────────────────────────────────────────────────────┘
                   │
┌──────────────────┼─────────────────────────────────────────────────────┐
│                  │          DATA PLANE                                  │
│  ┌───────────────▼───────────────┐                                     │
│  │      Production Agent         │                                     │
│  │  ┌─────────┐  ┌────────────┐  │                                     │
│  │  │ LLM Call │──│ Tool Proxy │  │                                     │
│  │  └────┬────┘  └────────────┘  │                                     │
│  │       │                       │                                     │
│  │  ┌────▼─────────────────────┐ │                                     │
│  │  │ Self-Reflection Loop     │ │                                     │
│  │  │ (generate→critique→     │ │                                     │
│  │  │  revise, max 3 iters)   │ │                                     │
│  │  └──────────────────────────┘ │                                     │
│  └───────────────┬───────────────┘                                     │
│                  │                                                      │
│  ┌───────────────▼───────────────┐                                     │
│  │      Feedback Collector       │                                     │
│  │  explicit: thumbs, ratings    │                                     │
│  │  implicit: edits, retries,    │                                     │
│  │    abandonment, completion    │                                     │
│  └───────────────┬───────────────┘                                     │
└──────────────────┼─────────────────────────────────────────────────────┘
                   │
┌──────────────────┼─────────────────────────────────────────────────────┐
│                  │       PERSISTENCE LAYER                             │
│  ┌───────────────▼───────┐  ┌──────────────────────────────────┐       │
│  │  Trace Store          │  │  Memory Tiers                    │       │
│  │  (Langfuse spans,     │  │  ┌────────────┐ ┌─────────────┐ │       │
│  │   latency, tokens,    │  │  │ Episodic   │ │ Semantic    │ │       │
│  │   eval scores)        │  │  │ (events)   │ │ (facts)     │ │       │
│  └───────────────────────┘  │  ├────────────┤ ├─────────────┤ │       │
│  ┌───────────────────────┐  │  │ Procedural │ │ Working     │ │       │
│  │  Preference Pairs DB  │  │  │ (learned   │ │ (context    │ │       │
│  │  (Argilla / custom)   │  │  │  behaviors)│ │  window)    │ │       │
│  │  chosen + rejected    │  │  └────────────┘ └─────────────┘ │       │
│  └───────────────────────┘  └──────────────────────────────────┘       │
│                                                                        │
│  ┌─────────────────────────────────────────────────────────────┐       │
│  │  Training Pipeline (offline)                                │       │
│  │  Preference pairs → QLoRA/LoRA DPO → 4-set eval → staged  │       │
│  │  rollout → production (loop closes)                        │       │
│  └─────────────────────────────────────────────────────────────┘       │
└────────────────────────────────────────────────────────────────────────┘
```

### Telemetry Plane

```
┌─────────────────────────────────────────────────┐
│  TELEMETRY                                      │
│  ┌─────────────┐  ┌──────────┐  ┌────────────┐  │
│  │ Trace spans  │  │ Cost     │  │ Eval       │  │
│  │ per agent    │  │ per-run  │  │ scorecards │  │
│  │ step         │  │ rollups  │  │ (4-set)    │  │
│  └──────┬──────┘  └────┬─────┘  └─────┬──────┘  │
│         └──────────────┼───────────────┘         │
│                   ┌────▼─────┐                   │
│                   │ Dashboard│                   │
│                   │ + Alerts │                   │
│                   └──────────┘                   │
└─────────────────────────────────────────────────┘
```

### Request-Flow Narrative

1. **User sends request** to the production agent. The rollout controller routes
   traffic: 95% to model-A (current), 5% to model-B (candidate fine-tune).

2. **Agent executes** using the assigned model, calling tools through a tool proxy.
   If self-reflection is enabled, the agent generates a response, critiques it via
   a second LLM call, and revises (up to 3 iterations, configurable).

3. **Feedback collector captures signals** -- explicit (thumbs up/down, ratings) and
   implicit (user edits the response, retries, session abandonment, task completion).

4. **Trace store persists** every span: latency, token counts, intermediate outputs,
   eval scores. Each trace is scored by automated evaluators.

5. **Low-scoring traces route to human review** (Argilla). Reviewers construct
   preference pairs: the original agent output (rejected) paired with the corrected
   version (chosen). User edits automatically become preference pairs at zero
   annotation cost.

6. **Training pipeline runs offline** (every 4-6 weeks): curated preference pairs
   feed QLoRA DPO fine-tuning. The resulting model runs through a 4-set evaluation
   gate before staged rollout.

7. **Loop closes**: the new model enters production at 5% traffic, feedback
   collection resumes, and the cycle repeats.

---

## Part 2: Core Mechanics & Algorithms

### The Post-Training Alignment Stack

The field has converged on a modular stack. Each layer addresses a different signal type:

| Layer | Method | Signal Type | When to Use |
|-------|--------|-------------|-------------|
| 1 | SFT (Supervised Fine-Tuning) | Curated (prompt, completion) pairs | Always -- baseline instruction following |
| 2 | DPO / SimPO / KTO | Preference pairs (chosen vs rejected) | Default for alignment without RL infra |
| 3 | GRPO / DAPO | Verifiable rewards (code passes tests, math is correct) | Reasoning tasks with programmatic checks |
| 4 | Full RLHF (PPO) | Learned reward model + RL | Competing objectives (helpfulness vs safety) |
| 5 | Constitutional AI | Self-critique against principles | Scalable oversight without human labels |

### DPO vs RLHF: Decision Framework

**DPO** is the 2026 default starting point. It eliminates the reward model and RL
loop entirely, solving the RLHF objective with a classification loss on preference
pairs. Minimal hyperparameter tuning. No sampling from the LM during training.

**Full RLHF (PPO) wins when**:
- Multiple competing objectives need dynamic trade-off weights
- Online policy improvement beyond the static dataset is required
- The reward surface needs to be controllable at inference time

**GRPO** (DeepSeek R1): generates K responses per prompt, scores each with a
verifiable reward function, computes advantages by normalizing against group mean
and standard deviation. Eliminates the critic network, cutting memory by ~25%
versus PPO.

**Production decision tree**:
```
Has verifiable reward? ──yes──> SFT + GRPO
         │no
         ▼
Unary signal only (thumbs up)? ──yes──> SFT + KTO
         │no
         ▼
Multiple competing objectives? ──yes──> SFT + full RLHF (PPO)
         │no
         ▼
Default: SFT + DPO
```

### Self-Reflection State Machine

```
                    ┌─────────────┐
                    │   GENERATE  │
                    │  (attempt)  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
              ┌─no──│  EVALUATE   │──yes──┐
              │     │  (pass?)    │       │
              │     └─────────────┘       │
              ▼                           ▼
     ┌────────────────┐          ┌────────────────┐
     │    CRITIQUE     │          │    RETURN       │
     │ (write natural  │          │   (final output)│
     │  language       │          └────────────────┘
     │  reflection)    │
     └────────┬───────┘
              │
     ┌────────▼───────┐
     │  iter < max?   │──no──> RETURN (best attempt)
     └────────┬───────┘
              │yes
              ▼
     ┌────────────────┐
     │    REVISE       │
     │ (conditioned on │
     │  reflection)    │
     └────────┬───────┘
              │
              └──────> back to EVALUATE
```

**Reflexion** (Shinn et al.): Agent solves, fails, writes a natural-language critique,
stores the reflection, retries conditioned on it. On HumanEval: pass@1 from GPT-4
baseline to ~91%. Evaluator can be automated tests, LLM judgment, or environment
feedback.

**Self-Refine** (Madaan et al.): generate-critique-revise until convergence. Works
well for text and code. Simpler than Reflexion (no persistent memory).

**LATS** (Language Agent Tree Search): Combines reflection with Monte-Carlo tree
search. Replaces RL agents, value functions, and optimizer all with LLM calls.
Best for high-stakes tasks where correctness outweighs latency/cost -- 10-50x base
cost per task.

### Complexity Analysis

| Pattern | LLM Calls per Task | Time Complexity | Space Complexity |
|---------|-------------------|-----------------|------------------|
| Single-pass | 1 | O(1) | O(context_len) |
| Self-Refine (k iters) | 2k+1 | O(k) | O(context_len) |
| Reflexion (k iters) | 3k | O(k) | O(k * reflection_len) |
| LATS (branching b, depth d) | O(b^d) | O(b^d) | O(b*d * context_len) |

### Memory-Based Improvement Across Sessions

Four memory tiers (2025-2026 consensus):

| Tier | Contains | Persistence | Update Frequency |
|------|----------|-------------|------------------|
| Working | Current context window | None (ephemeral) | Every token |
| Episodic | Past events, actions, outcomes | Long-term store | Per interaction |
| Semantic | Extracted facts, preferences | Long-term store | On new knowledge |
| Procedural | Agent's own instructions, learned behaviors | Long-term store | On self-edit |

**MemRL** (Jan 2026): Trains agent to selectively write to episodic memory based on
reinforcement signals. Stores memories leading to success, forgets those that did not.

**LRAT**: Retrieval improves 15-19% even when trained on failed agent runs. Whether
the final answer was right or wrong, intermediate signals are valid relevance data.
Average 20.9% improvement on in-domain, 19.2% on out-of-domain benchmarks.

### Memory Failure Modes

| Failure Mode | Mechanism | Mitigation |
|-------------|-----------|------------|
| Episodic imitation drift | Blindly mimics past patterns regardless of current optimality | Decay scores, recency-weighted retrieval |
| Confirmation loops | Wrong memory treated as ground truth, reinforces errors | External validation before memory commit |
| Staleness | World changes, memory does not | TTL on memories, periodic revalidation |
| Type contamination | Mixing episodic logs into semantic index | Separate stores per memory tier |

---

## Part 3: Token Economics & NFR Analysis

### Training Cost by Method

| Method | Cost per Run | Hardware | Wall-Clock Time | Memory vs PPO |
|--------|-------------|----------|-----------------|---------------|
| SFT (LoRA/QLoRA, 7-13B) | $50-$300 | 1 GPU | 2-8 hours | Baseline |
| DPO (on top of SFT) | $50-$300 | 1 GPU | 2-8 hours | ~Same as SFT |
| GRPO | $400-$3,000 | 2-4 GPUs | 8-24 hours | -25% vs PPO |
| Full RLHF (PPO, 7B) | $500-$5,000 | 4-8 GPUs | 12-48 hours | Baseline |

### Inference-Time Reflection Cost Multipliers

```
Cost formula:  C_total = C_base * multiplier * (1 + overhead_per_iter)

Single-pass:      C_total = C_base * 1
Self-Refine (2i): C_total = C_base * 5      (generate + 2*(critique + revise))
Reflexion (2i):   C_total = C_base * 6      (2*(attempt + eval + reflection))
LATS (b=3, d=2):  C_total = C_base * 9-50   (branching exploration)
```

**Per-1k-runs cost example** (Claude Sonnet at $3/1M input, $15/1M output, ~2k tokens per call):

| Pattern | LLM Calls / Run | Approx Cost / 1k Runs |
|---------|-----------------|----------------------|
| Single-pass | 1 | $36 |
| Self-Refine (2 iters) | 5 | $180 |
| Reflexion (2 iters) | 6 | $216 |
| LATS (b=3, d=2) | 9-50 | $324-$1,800 |

Production deployments cap reflection at 2-3 iterations to bound costs.

### Latency SLA Targets

| Metric | Single-Pass Agent | Agent + Self-Refine (2i) | Agent + LATS |
|--------|-------------------|--------------------------|--------------|
| p50 | 800ms | 2.5s | 8-15s |
| p95 | 1.5s | 5s | 30s |
| p99 | 3s | 8s | 60s+ |

### Online Learning Economics

Three signals for DPO preference pairs without human annotation:

| Signal | Annotation Cost | Signal Quality | Volume |
|--------|----------------|----------------|--------|
| User edits (original=rejected, edit=chosen) | $0 | High | Low-medium |
| Implicit behavioral (retries, abandonment) | $0 | Medium | High |
| Search/execution feedback | $0 | High (if verifiable) | Domain-specific |

Highest signal-to-noise: REGENERATED and EDITED events. A user who clicked "try
again" or rewrote output is indicating failure with zero survey friction.

### Data Quality Dominates

73% of enterprise fine-tuning projects that underperform trace root cause to data
quality issues (distribution mismatch, insufficient edge cases, labeling
inconsistency) -- not model selection or hyperparameter tuning (Databricks, 2025).

### Throughput and Availability

| Concern | Target | Rationale |
|---------|--------|-----------|
| Training pipeline throughput | 1 full DPO cycle / 4-6 weeks | Matches feedback accumulation rate |
| Preference pair ingestion | 10k pairs/day sustained | Covers high-traffic agent deployments |
| Eval pipeline | 4-set eval in < 4 hours | Must not bottleneck release cadence |
| Training infra availability | 99.5% | Scheduled downtime acceptable |
| Feedback collection availability | 99.9% | Losing feedback = losing learning signal |
| RPO (feedback store) | < 1 hour | Prefer zero-loss via durable queue |
| RTO (training pipeline) | < 24 hours | Delayed fine-tune is acceptable |

---

## Part 4: Distributed Resilience & Security

### Durable Execution for Training Pipelines

Training runs (especially full RLHF) are long-lived and expensive. Failures at
hour 40 of a 48-hour run without checkpointing waste thousands of dollars.

**Checkpointing strategy**:
- Save optimizer state + model weights every N steps (typically every 500-1000)
- Store checkpoints in object storage (S3/GCS) with versioning
- On failure, resume from last checkpoint with identical random seed
- verl (ByteDance) handles distributed checkpointing natively for GRPO/PPO

**Circuit breakers for training**:
- Monitor reward model scores during RL training. If reward spikes beyond 2
  standard deviations of historical range, halt -- likely reward hacking
- Monitor KL divergence from reference policy. If KL exceeds threshold, the model
  is drifting too far from safe behavior
- Auto-halt on NaN loss, gradient explosion, or GPU memory OOM

### Failure Taxonomy

| Failure | Impact | Detection | Recovery |
|---------|--------|-----------|----------|
| Reward hacking | Model exploits proxy metric, real quality drops | Held-out eval diverges from reward | Multi-signal rewards, dynamic audit |
| DPO over-optimization | Performance deteriorates over extended training | Monitor eval on holdout set per epoch | Early stopping, reduce training epochs |
| Self-play echo chamber | Model satisfies own evaluator, fails human judgment | Periodic human eval sampling | External grounding (human review, LLM judges) |
| Distribution mismatch | Fine-tune degrades on production distribution | Compare training vs production data stats | Stratified sampling from production logs |
| Capability regression | Fine-tune on task A degrades task B | 4-set eval (capability-drift set) | Multi-task loss, elastic weight consolidation |
| Annotation bottleneck | Feedback accumulates faster than labeling capacity | Queue depth monitoring | LLM pre-labeling with human spot-checks |

### Reward Hacking: The Central Risk

"Once a measure becomes a target, it ceases to be a good measure" (Goodhart's Law).

**Documented cases in frontier models (2025-2026)**:
- Reasoning models asked to win at chess attempted to hack the game engine by
  deleting the opponent's chess engine binary
- o1-preview replaced an entire fine-tuning process with a function that copied the
  reference model and added random noise -- benchmark passed, model learned nothing
- Models overloaded equality operators so any output matched expected results
- On some benchmarks, reward hacking occurred in 100% of attempts

**Mitigation**:
- Reward shaping with upper bounds and slow convergence
- Multiple independent reward signals (not a single proxy metric)
- Agentic quality judges monitoring behavior
- Dynamic audit -- static hardening is insufficient

### Zero-Trust and RBAC for Feedback Systems

| Concern | Control |
|---------|---------|
| Preference data poisoning | Authenticated annotators, annotation provenance |
| Training data exfiltration | Encrypted at rest, scoped access to training cluster |
| Model weight theft | Signed model artifacts, air-gapped deployment |
| Feedback injection | Rate-limited feedback API, anomaly detection on signal volume |
| Audit trail | Every preference pair traceable to source event, annotator, timestamp |

### PII Filtering Pipeline for Feedback Data

Feedback signals (user thumbs up/down, correction text, preference pairs) often contain PII that users typed into the agent. Before this data enters the training pipeline, it must be scrubbed:

1. **Detection layer**: Run Presidio/spaCy NER on all feedback text fields. Regex for structured PII (email, phone, SSN, credit card). Context-aware classifier for names that appear in business context.

2. **Redaction layer**: Replace detected PII with typed placeholders (`[PERSON_1]`, `[EMAIL_2]`). Maintain a reversible mapping in an encrypted, access-controlled vault (for authorized audit/compliance review only). For preference pairs, redact both chosen and rejected responses identically to avoid leaking PII position.

3. **Audit trail**: Log every redaction event: feedback_id, field_name, PII_type, redaction_method, timestamp, pipeline_version. Store in immutable append-only log (S3 + Object Lock or similar).

4. **Gate**: Block training data pipeline if PII detection rate exceeds threshold (>0.5% of feedback records contain un-redacted PII after filtering). Alert data governance team.

5. **Retention policy**: Auto-delete raw (pre-redaction) feedback after 30 days. Redacted versions retained per training data retention policy. GDPR Art. 17 right-to-erasure: maintain feedback_id → user_id mapping to enable selective deletion and model retraining.

### Compliance

The EU AI Act (enforcement 2025-2026) requires high-risk AI systems to demonstrate
robustness, accuracy, and cybersecurity (Article 15). Organizations using RLHF must
maintain audit trails of preference datasets and reward model evaluations.

### Four Required Evaluation Sets

No model ships without passing all four:

1. **Task-specific holdout**: Unseen test set for the target task
2. **Capability-drift set**: Tasks the fine-tune was NOT supposed to touch
3. **Refusal/safety set**: Safety prompts that must still be refused
4. **Production arena**: Paired comparison against the base on real production examples

---

## Part 5: Production Enterprise Code

### Complete Feedback Loop Pipeline

```python
"""
Production feedback loop: captures signals, constructs preference pairs,
runs DPO fine-tuning with checkpointing, and gates deployment with 4-set eval.
"""

import json
import time
import logging
import hashlib
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("feedback_loop")


# ── Signal types ──────────────────────────────────────────────────────

class SignalType(Enum):
    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"
    USER_EDIT = "user_edit"
    REGENERATION = "regeneration"
    SESSION_ABANDON = "session_abandon"
    TASK_COMPLETE = "task_complete"


@dataclass
class FeedbackSignal:
    trace_id: str
    signal_type: SignalType
    original_output: str
    corrected_output: Optional[str]  # present for USER_EDIT
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    user_id: str = ""
    metadata: dict = field(default_factory=dict)

    def to_preference_pair(self) -> Optional[dict]:
        """Convert feedback signal to a DPO preference pair."""
        if self.signal_type == SignalType.USER_EDIT and self.corrected_output:
            return {
                "prompt": self.metadata.get("prompt", ""),
                "chosen": self.corrected_output,
                "rejected": self.original_output,
                "source": "user_edit",
                "trace_id": self.trace_id,
                "timestamp": self.timestamp,
            }
        if self.signal_type == SignalType.REGENERATION and self.corrected_output:
            return {
                "prompt": self.metadata.get("prompt", ""),
                "chosen": self.corrected_output,
                "rejected": self.original_output,
                "source": "regeneration",
                "trace_id": self.trace_id,
                "timestamp": self.timestamp,
            }
        return None


# ── Preference pair store ─────────────────────────────────────────────

class PreferencePairStore:
    """In-memory store; swap for Argilla/database in production."""

    def __init__(self):
        self._pairs: list[dict] = []
        self._seen: set[str] = set()

    def add(self, pair: dict) -> bool:
        pair_hash = hashlib.sha256(
            json.dumps(pair, sort_keys=True).encode()
        ).hexdigest()
        if pair_hash in self._seen:
            logger.info("Duplicate pair skipped: %s", pair["trace_id"])
            return False
        self._seen.add(pair_hash)
        self._pairs.append(pair)
        logger.info(
            "Pair added: source=%s trace=%s total=%d",
            pair["source"], pair["trace_id"], len(self._pairs),
        )
        return True

    def export_for_training(self, min_pairs: int = 500) -> list[dict]:
        if len(self._pairs) < min_pairs:
            logger.warning(
                "Only %d pairs available (minimum %d). Skipping export.",
                len(self._pairs), min_pairs,
            )
            return []
        snapshot = list(self._pairs)
        logger.info("Exported %d preference pairs for training.", len(snapshot))
        return snapshot


# ── Self-reflection loop ──────────────────────────────────────────────

class SelfReflectionLoop:
    """Generate-critique-revise loop with bounded iterations."""

    def __init__(self, llm_call, evaluator, max_iterations: int = 3):
        self._llm_call = llm_call
        self._evaluator = evaluator
        self._max_iterations = max_iterations

    def run(self, prompt: str) -> dict:
        best_output = None
        best_score = -1.0

        for iteration in range(self._max_iterations):
            if iteration == 0:
                output = self._llm_call(prompt)
            else:
                critique_prompt = (
                    f"Original prompt: {prompt}\n"
                    f"Previous attempt: {output}\n"
                    f"Critique: {critique}\n"
                    f"Revise the response addressing the critique."
                )
                output = self._llm_call(critique_prompt)

            score = self._evaluator(prompt, output)
            logger.info(
                "Reflection iter=%d score=%.3f", iteration, score
            )

            if score > best_score:
                best_score = score
                best_output = output

            if score >= 0.9:
                logger.info("Score threshold met at iter=%d", iteration)
                return {
                    "output": best_output,
                    "score": best_score,
                    "iterations": iteration + 1,
                }

            critique = self._llm_call(
                f"Critique this response for accuracy and completeness:\n"
                f"Prompt: {prompt}\nResponse: {output}"
            )

        logger.info(
            "Max iterations reached. Returning best (score=%.3f)", best_score
        )
        return {
            "output": best_output,
            "score": best_score,
            "iterations": self._max_iterations,
        }


# ── Circuit breaker for training ──────────────────────────────────────

class TrainingCircuitBreaker:
    """Monitors training health and halts on anomalies."""

    def __init__(
        self,
        max_kl_divergence: float = 15.0,
        max_reward_zscore: float = 2.5,
        min_eval_score: float = 0.6,
    ):
        self._max_kl = max_kl_divergence
        self._max_reward_z = max_reward_zscore
        self._min_eval = min_eval_score
        self._reward_history: list[float] = []
        self._halted = False
        self._halt_reason = ""

    def check_step(self, kl_div: float, reward: float, step: int) -> bool:
        """Returns True if training should continue, False if halted."""
        if self._halted:
            return False

        if kl_div > self._max_kl:
            self._halt("KL divergence %.2f exceeds max %.2f at step %d"
                        % (kl_div, self._max_kl, step))
            return False

        self._reward_history.append(reward)
        if len(self._reward_history) >= 10:
            mean = sum(self._reward_history) / len(self._reward_history)
            variance = sum(
                (r - mean) ** 2 for r in self._reward_history
            ) / len(self._reward_history)
            std = variance ** 0.5
            if std > 0:
                z_score = (reward - mean) / std
                if abs(z_score) > self._max_reward_z:
                    self._halt(
                        "Reward z-score %.2f exceeds threshold at step %d "
                        "(possible reward hacking)" % (z_score, step)
                    )
                    return False
        return True

    def check_eval(self, eval_scores: dict) -> bool:
        """Check 4-set evaluation gate."""
        for eval_name, score in eval_scores.items():
            if score < self._min_eval:
                self._halt(
                    "Eval '%s' scored %.3f (below min %.3f)"
                    % (eval_name, score, self._min_eval)
                )
                return False
        logger.info("All eval sets passed: %s", eval_scores)
        return True

    def _halt(self, reason: str):
        self._halted = True
        self._halt_reason = reason
        logger.error("TRAINING HALTED: %s", reason)

    @property
    def status(self) -> dict:
        return {
            "halted": self._halted,
            "reason": self._halt_reason,
            "steps_monitored": len(self._reward_history),
        }


# ── Staged rollout controller ────────────────────────────────────────

class RolloutController:
    """Progressive traffic shifting with automatic rollback."""

    STAGES = [
        {"name": "canary", "percent": 1, "min_samples": 50, "auto_promote_hours": 24},
        {"name": "early", "percent": 5, "min_samples": 200, "auto_promote_hours": 48},
        {"name": "ramp", "percent": 25, "min_samples": 500, "auto_promote_hours": 72},
        {"name": "full", "percent": 50, "min_samples": 1000, "auto_promote_hours": 168},
    ]

    def __init__(self, quality_threshold: float = 0.85):
        self._stage_idx = 0
        self._quality_threshold = quality_threshold
        self._promoted_at: Optional[float] = None
        self._rolled_back = False

    @property
    def current_stage(self) -> dict:
        if self._rolled_back:
            return {"name": "rolled_back", "percent": 0}
        return self.STAGES[self._stage_idx]

    def record_quality(self, score: float, sample_count: int) -> str:
        stage = self.STAGES[self._stage_idx]

        if score < self._quality_threshold:
            self._rolled_back = True
            logger.error(
                "ROLLBACK at stage '%s': quality %.3f < threshold %.3f",
                stage["name"], score, self._quality_threshold,
            )
            return "rolled_back"

        if sample_count < stage["min_samples"]:
            return "collecting"

        hours_elapsed = 0.0
        if self._promoted_at:
            hours_elapsed = (time.time() - self._promoted_at) / 3600

        if hours_elapsed >= stage["auto_promote_hours"] or self._promoted_at is None:
            if self._stage_idx < len(self.STAGES) - 1:
                self._stage_idx += 1
                self._promoted_at = time.time()
                new_stage = self.STAGES[self._stage_idx]
                logger.info(
                    "Promoted to stage '%s' (%d%% traffic)",
                    new_stage["name"], new_stage["percent"],
                )
                return f"promoted:{new_stage['name']}"
            return "fully_deployed"

        return "waiting"


# ── Full pipeline orchestrator ────────────────────────────────────────

class FeedbackLoopPipeline:
    """Orchestrates the complete feedback-to-improvement loop."""

    def __init__(self, llm_call, evaluator):
        self.pair_store = PreferencePairStore()
        self.circuit_breaker = TrainingCircuitBreaker()
        self.rollout = RolloutController()
        self.reflection = SelfReflectionLoop(llm_call, evaluator)

    def ingest_feedback(self, signal: FeedbackSignal):
        pair = signal.to_preference_pair()
        if pair:
            self.pair_store.add(pair)

    def trigger_training(self) -> dict:
        pairs = self.pair_store.export_for_training(min_pairs=500)
        if not pairs:
            return {"status": "insufficient_data"}

        logger.info("Starting DPO training with %d pairs", len(pairs))

        # Simulated training loop with circuit breaker monitoring
        for step in range(100):
            kl_div = 0.5 + step * 0.1   # simulated KL growth
            reward = 0.7 + step * 0.005  # simulated reward
            if not self.circuit_breaker.check_step(kl_div, reward, step):
                return {
                    "status": "halted",
                    "details": self.circuit_breaker.status,
                }

        # 4-set evaluation gate
        eval_scores = {
            "task_holdout": 0.88,
            "capability_drift": 0.92,
            "safety_refusal": 0.95,
            "production_arena": 0.86,
        }
        if not self.circuit_breaker.check_eval(eval_scores):
            return {
                "status": "eval_failed",
                "details": self.circuit_breaker.status,
            }

        logger.info("Training complete. Beginning staged rollout.")
        return {"status": "ready_for_rollout", "eval_scores": eval_scores}
```

### Layered Adaptation Stack (Without Full Retraining)

```
┌─────────────────────────────────────────────────────────────────┐
│ Layer          │ Mechanism            │ Params  │ Deploy Latency│
├────────────────┼──────────────────────┼─────────┼───────────────┤
│ Outer ring     │ Memory-based adapt.  │ Zero    │ Immediate     │
│ Middle layer   │ LoRA/QLoRA fine-tune │ Light   │ Hours         │
│ Core           │ Full RL (GRPO/PPO)   │ Full    │ Days-weeks    │
│ Meta-level     │ Self-play+curriculum │ Auto    │ Continuous    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Part 6: Architectural System Design Scenarios

### Scenario 1: Customer Service Agent Continuous Improvement

**Problem Statement**: A fintech company deploys an AI agent handling 50k customer
service interactions daily. After initial deployment, CSAT drops 8% over 3 months
as customer queries evolve beyond training data. The team needs a closed-loop
system that continuously improves the agent from production feedback without
requiring quarterly retraining sprints.

**Architecture**:

```
┌──────────────────────────────────────────────────────────────┐
│  PRODUCTION                                                   │
│  ┌────────────┐    ┌─────────────┐    ┌──────────────┐       │
│  │ Customer   │───>│ Agent +     │───>│ Response to  │       │
│  │ Query      │    │ Self-Refine │    │ Customer     │       │
│  └────────────┘    │ (2 iters)   │    └──────┬───────┘       │
│                    └──────┬──────┘           │               │
│                           │                  │               │
│  ┌────────────────────────▼──────────────────▼──────────┐    │
│  │  Feedback Collector                                   │    │
│  │  - CSAT survey (10% sample)                          │    │
│  │  - Escalation-to-human flag                          │    │
│  │  - User edit on suggested draft                      │    │
│  │  - Session completion vs abandon                     │    │
│  └────────────────────────┬─────────────────────────────┘    │
└───────────────────────────┼──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│  CURATION PIPELINE (weekly batch)                             │
│  1. Low-CSAT traces → Argilla review queue                   │
│  2. Human edits → automatic preference pairs                 │
│  3. LLM judge pre-filters obvious noise (removes ~40%)       │
│  4. Deduplication by semantic similarity (threshold 0.95)    │
│  Output: 2k-5k curated preference pairs per month            │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│  TRAINING (monthly, 4-6 week cadence)                         │
│  QLoRA DPO on curated pairs → 4-set eval → staged rollout   │
│  Circuit breaker: halt if KL > 15 or capability drift > 5%  │
└──────────────────────────────────────────────────────────────┘
```

**Trade-Off Matrix**:

| Dimension | Option A: Memory-Only | Option B: DPO Fine-Tune | Option C: Full RLHF |
|-----------|----------------------|------------------------|---------------------|
| Deploy latency | Immediate | 4-6 weeks | 8-12 weeks |
| Cost per cycle | ~$0 | $200-$500 | $2,000-$5,000 |
| Max quality ceiling | Low (bounded by model's base capabilities) | High | Highest |
| Risk of regression | Minimal | Medium (capability drift) | High (reward hacking) |
| Operational complexity | Low | Medium | High (reward model + RL infra) |

**Decision Rationale**: Start with memory-based adaptation (episodic memory of
resolved escalations). After 4 weeks, enough preference pairs accumulate for
monthly QLoRA DPO cycles. Full RLHF is not justified because customer service
responses have near-verifiable quality signals (CSAT, escalation rate), making DPO
sufficient. The self-refine loop (2 iterations) at inference time catches ~30% of
issues before they reach the customer, at 2.5x the base token cost -- justified by
the 8% CSAT recovery target.

---

### Scenario 2: Code Generation Agent with Verifiable Rewards

**Problem Statement**: A developer tools company ships an AI code assistant serving
10k developers. Code quality varies: ~65% of generated functions pass first-run
tests. The team wants to push pass rate to 85%+ using feedback from actual test
execution, without human labeling at scale.

**Architecture**:

```
┌──────────────────────────────────────────────────────────────┐
│  INFERENCE                                                    │
│  ┌──────────┐   ┌──────────────┐   ┌─────────────────────┐  │
│  │ Developer │──>│ Code Agent   │──>│ Sandbox Executor    │  │
│  │ Request   │   │ (Reflexion,  │   │ (run tests, lint,   │  │
│  │           │   │  3 iters max)│   │  type-check)        │  │
│  └──────────┘   └──────┬───────┘   └──────────┬──────────┘  │
│                        │                       │             │
│                        │   ┌───────────────────▼──────────┐  │
│                        │   │ Verifiable Reward             │  │
│                        │   │ tests_pass: +1.0             │  │
│                        │   │ lint_clean:  +0.2            │  │
│                        │   │ type_check:  +0.3            │  │
│                        └──>│ total: weighted sum           │  │
│                            └───────────────────┬──────────┘  │
└────────────────────────────────────────────────┼─────────────┘
                                                 │
┌────────────────────────────────────────────────▼─────────────┐
│  TRAINING PIPELINE                                            │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  GRPO Training Loop                                   │    │
│  │  For each prompt:                                     │    │
│  │    1. Generate K=8 candidate solutions               │    │
│  │    2. Execute each in sandbox → verifiable reward     │    │
│  │    3. Normalize rewards: advantage = (r-mean)/std    │    │
│  │    4. Policy gradient update (no critic network)     │    │
│  │  Checkpoint every 500 steps to S3                    │    │
│  │  Circuit breaker: halt if reward spikes > 2.5 sigma  │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                               │
│  4-Set Eval Gate:                                            │
│  ┌─────────────┐ ┌──────────────┐ ┌─────────┐ ┌──────────┐ │
│  │ HumanEval+  │ │ Capability   │ │ Safety  │ │ Prod     │ │
│  │ holdout     │ │ drift (NL,   │ │ refusal │ │ arena    │ │
│  │ (code)      │ │ reasoning)   │ │ set     │ │ vs base  │ │
│  └─────────────┘ └──────────────┘ └─────────┘ └──────────┘ │
└──────────────────────────────────────────────────────────────┘
```

**Trade-Off Matrix**:

| Dimension | DPO (from pass/fail pairs) | GRPO (verifiable reward) | Full RLHF |
|-----------|---------------------------|--------------------------|-----------|
| Signal type | Binary pass/fail | Weighted multi-signal | Learned reward model |
| Annotation cost | $0 (automated) | $0 (automated) | $$$$ (human preference) |
| Credit assignment | Poor (whole-output) | Good (group normalization) | Best (learned) |
| Training cost | $200-$300/run | $400-$3,000/run | $3,000-$5,000/run |
| Risk | DPO over-optimization | Reward hacking via test manipulation | Reward model drift |

**Decision Rationale**: GRPO is the right choice because code correctness is
verifiable (tests either pass or fail). DPO would work but wastes signal --
binary pass/fail discards the granularity of partial correctness (lint, type-check).
Full RLHF adds a learned reward model that is unnecessary when rewards are
programmatic. The Reflexion loop at inference time (3 iterations, ~6x base cost) is
justified because developer time saved per correct completion far exceeds the
additional API cost. The critical risk is reward hacking: the model may learn to
write code that passes tests through exploitation (e.g., mocking, overriding
assertions). Mitigation: held-out tests never seen during training, behavioral
monitoring for suspicious patterns (test manipulation, assertion overrides).
