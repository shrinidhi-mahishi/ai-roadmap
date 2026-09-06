"""
Agent Feedback Loops -- Interview Prep Code Snippets

Covers the four separated roles (planner, executor, critic, verifier), the
self-correction loop (generate -> validate -> retry), reflection patterns,
tool-error retry with backoff, human feedback integration, structured output
validation, and feedback-driven prompt refinement.  All examples are
self-contained with stub oracles and LLMs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


# =============================================================================
# --- Section 1: Self-Correction Loop (Generate -> Validate -> Retry) --------
# =============================================================================
# The canonical feedback loop:
#   generate -> oracle/verifier -> pass? -> return
#                                  fail? -> critic -> revise -> loop
#
# Key invariant: attach a critic ONLY when an oracle exists (tests, compiler,
# DB predicate).  Huang (ICLR 2024): intrinsic self-correction (same model,
# no oracle) DROPS accuracy.  GSM8K 75.9 -> 74.7 after two rounds.

@dataclass
class Attempt:
    """One trial in a feedback loop."""
    output: str
    oracle_pass: bool
    oracle_logs: str = ""
    reflection: str = ""
    iteration: int = 0


class FakeOracle:
    """Deterministic verifier standing in for pytest / compiler / DB check.

    In production, rank stoppers (total-order):
      1. Deterministic env flag (AlfWorld done, HTTP 2xx, DB predicate)
      2. Hidden tests
      3. Replayable computation (interpreter, compiler, calculator)
      4. PRM -- rerank, not stop, when 1-3 exist
      5. LLM-as-judge / self-eval -- subjective quality only
    If 1-3 exist, 4-5 must NOT override.
    """
    def __init__(self, pass_on_attempt: int = 2):
        self.pass_on = pass_on_attempt

    def check(self, output: str, attempt_num: int) -> tuple[bool, str]:
        passed = attempt_num >= self.pass_on
        logs = f"test_result={'PASS' if passed else 'FAIL'}; output_len={len(output)}"
        return passed, logs


class FakeLLM:
    """Stub LLM for generating, critiquing, and revising."""

    def generate(self, prompt: str, context: str = "") -> str:
        return f"generated_answer(ctx={context[:20]})"

    def critique(self, output: str, oracle_logs: str) -> str:
        """Critic reads oracle logs, not the raw data that failed.

        This is the key pattern -- the critic verbalizes WHY a trial failed,
        based on structured test output, not by re-reading the input.
        """
        return f"hint: {oracle_logs}; try different approach"

    def revise(self, output: str, reflection: str) -> str:
        return f"revised({output[:20]}, reflection={reflection[:30]})"


def self_correction_loop(
    task: str,
    *,
    llm: FakeLLM,
    oracle: FakeOracle,
    max_iterations: int = 4,
) -> list[Attempt]:
    """Generate -> validate -> critique -> revise loop.

    This is the Reflexion pattern (Shinn et al., NeurIPS 2023):
      for trial in 1..T:
        y = Actor(task, memory)
        r = Env/Evaluator(y)
        if oracle_pass(r): return y
        z = Reflector(task, y, r)
        memory.append(z)

    HumanEval Python pass@1: 91.0% vs GPT-4 80.1% WITH tests.
    WITHOUT tests on hardest 50 HumanEval-Rust: 52% vs 60% -- harmful.
    """
    attempts: list[Attempt] = []
    context = ""

    for i in range(max_iterations):
        # Generate (or revise if we have a prior reflection)
        if i == 0:
            output = llm.generate(task)
        else:
            output = llm.revise(attempts[-1].output, attempts[-1].reflection)

        # Validate with oracle
        passed, logs = oracle.check(output, attempt_num=i + 1)

        attempt = Attempt(
            output=output,
            oracle_pass=passed,
            oracle_logs=logs,
            iteration=i + 1,
        )

        if passed:
            attempts.append(attempt)
            break

        # Critique: only fires on oracle fail
        # No oracle, no critic -- this is Invariant I3
        reflection = llm.critique(output, logs)
        attempt.reflection = reflection
        attempts.append(attempt)

    return attempts


# =============================================================================
# --- Section 2: Reflection Pattern (Agent Critiques Its Own Output) ---------
# =============================================================================
# Self-Refine (Madaan et al., NeurIPS 2023): same LLM as INIT / FEEDBACK /
# REFINE.  ~20% improvement over one-shot.  Best for style/fluency, NOT fact.
# CRITIC (Gou et al.): critique backed by tools (search, interpreter).
#   With tools:    ChatGPT HotpotQA F1 = 52.9
#   Without tools: 46.1 (below ReAct at 50.2)
# Critique without tools can be WORSE than no critique.

@dataclass
class ReflectionMemory:
    """Episodic memory for reflections.  Cap at last 3 (Reflexion paper).

    Memory is DATA, not instructions.  Store with origin=critic,
    untrusted=true.  Never auto-promote web observations to semantic memory.
    """
    hints: list[dict] = field(default_factory=list)
    max_size: int = 3

    def add(self, reflection: str, oracle_hash: str) -> None:
        self.hints.append({
            "text": reflection,
            "origin": "critic",          # who wrote it
            "oracle_hash": oracle_hash,  # ties reflection to specific test failure
            "untrusted": True,           # never treat as instructions
        })
        # Keep only the last N -- further reflections don't explore differently
        self.hints = self.hints[-self.max_size:]

    def get_context(self) -> str:
        if not self.hints:
            return ""
        return "\n".join(
            f"[Reflection {i+1}] {h['text']}" for i, h in enumerate(self.hints)
        )


class SelfRefineLoop:
    """Self-Refine: generate -> feedback -> refine, same model, no tools.

    Use ONLY for style/preference tasks.  For fact tasks, use CRITIC with
    tools or Reflexion with tests.

    Complexity: 2k+1 LLM calls for k iterations.
    Cost multiplier vs single-pass: ~5x for 2 iterations.
    """

    def __init__(self, llm: FakeLLM, max_k: int = 4):
        self.llm = llm
        self.max_k = max_k

    def run(self, task: str) -> dict:
        output = self.llm.generate(task)
        history = [{"stage": "init", "output": output}]

        for k in range(self.max_k):
            # Feedback: same model evaluates its own output
            feedback = self.llm.critique(output, "style_check")

            # Check if the model thinks it's good enough
            if "looks good" in feedback.lower() or k == self.max_k - 1:
                history.append({"stage": "final", "output": output, "k": k})
                break

            # Refine conditioned on feedback
            output = self.llm.revise(output, feedback)
            history.append({"stage": f"refine_{k+1}", "output": output})

        return {"final_output": output, "iterations": len(history), "history": history}


# =============================================================================
# --- Section 3: Tool Error Retry with Backoff -------------------------------
# =============================================================================
# Transient tool errors (429, 503, network timeout) need jitter-based retry.
# Permanent errors (4xx auth, schema mismatch) must NOT be retried.

class TransientError(Exception):
    """Retryable error (429, 503, timeout)."""
    pass


class PermanentError(Exception):
    """Non-retryable error (4xx auth, schema mismatch, Cedar deny)."""
    pass


def retry_with_jitter(
    fn: Callable,
    *,
    attempts: int = 4,
    base_s: float = 0.05,
    cap_s: float = 2.0,
) -> Any:
    """AWS-style full-jitter exponential backoff.

    sleep = random(0, min(cap, base * 2^attempt))

    Key points:
    - Only retry TransientError; PermanentError propagates immediately
    - Full jitter (not equal jitter) reduces thundering herd
    - Always have a max attempts cap -- unbounded retry is a cost amplifier
    """
    last_error = None
    for i in range(attempts):
        try:
            return fn()
        except PermanentError:
            raise  # Never retry auth failures or schema mismatches
        except TransientError as e:
            last_error = e
            if i < attempts - 1:
                sleep_time = random.uniform(0, min(cap_s, base_s * (2 ** i)))
                time.sleep(sleep_time)
    raise last_error  # type: ignore[misc]


class ToolExecutor:
    """Execute a tool call with retry and circuit breaker awareness.

    In production, map MCP isError:true to span status ERROR.
    JSON-RPC 200 with isError:true is a lie -- RED metrics show 100%
    healthy while the agent loops.
    """

    def __init__(self, fail_first_n: int = 2):
        self.call_count = 0
        self.fail_first_n = fail_first_n

    def execute(self, tool_name: str, args: dict) -> dict:
        self.call_count += 1
        if self.call_count <= self.fail_first_n:
            raise TransientError(f"{tool_name}: 429 rate limited")
        return {"result": f"{tool_name}_ok", "args": args}

    def execute_with_retry(self, tool_name: str, args: dict) -> dict:
        return retry_with_jitter(
            lambda: self.execute(tool_name, args),
            attempts=4,
            base_s=0.01,  # fast for demo
        )


# =============================================================================
# --- Section 4: Human Feedback Integration ----------------------------------
# =============================================================================
# Human feedback sources (zero-annotation signals):
#   - User edits (original=rejected, edit=chosen) -- highest signal
#   - Implicit behavioral (retries, abandonment)
#   - Thumbs up/down
#   - Search/execution feedback (if verifiable)
#
# 73% of enterprise fine-tuning underperformance traces to DATA QUALITY,
# not model selection or hyperparameters (Databricks 2025).

@dataclass
class FeedbackRecord:
    """A single piece of human feedback on an agent output."""
    request_id: str
    original_output: str
    feedback_type: str       # "edit" | "retry" | "thumbs_down" | "thumbs_up" | "abandon"
    corrected_output: str = ""
    timestamp: float = field(default_factory=time.time)


class FeedbackCollector:
    """Collect and route human feedback for downstream use.

    Best practice: the highest signal-to-noise is REGENERATED and EDITED
    events.  A user who clicked "try again" or rewrote the output is
    indicating failure with zero survey friction.
    """

    def __init__(self) -> None:
        self.records: list[FeedbackRecord] = []

    def record_edit(self, request_id: str, original: str, edited: str) -> FeedbackRecord:
        """User edited the output -- strongest signal.
        Creates an implicit preference pair: (rejected=original, chosen=edited).
        """
        rec = FeedbackRecord(
            request_id=request_id,
            original_output=original,
            feedback_type="edit",
            corrected_output=edited,
        )
        self.records.append(rec)
        return rec

    def record_retry(self, request_id: str, original: str) -> FeedbackRecord:
        """User clicked 'try again' -- implicit rejection."""
        rec = FeedbackRecord(
            request_id=request_id,
            original_output=original,
            feedback_type="retry",
        )
        self.records.append(rec)
        return rec

    def record_thumbs(self, request_id: str, output: str, up: bool) -> FeedbackRecord:
        """Explicit thumbs up/down -- lower friction but noisier signal."""
        rec = FeedbackRecord(
            request_id=request_id,
            original_output=output,
            feedback_type="thumbs_up" if up else "thumbs_down",
        )
        self.records.append(rec)
        return rec

    def to_preference_pairs(self) -> list[dict]:
        """Convert feedback into DPO-style preference pairs.

        DPO is the 2026 default for alignment:
          - Eliminates reward model and RL loop entirely
          - Solves RLHF objective with classification loss on preference pairs
          - Use GRPO when reward is verifiable (code passes tests)
          - Use KTO when only unary signal exists (thumbs up only)
        """
        pairs = []
        for rec in self.records:
            if rec.feedback_type == "edit" and rec.corrected_output:
                pairs.append({
                    "prompt": rec.request_id,  # in real usage, the actual prompt
                    "chosen": rec.corrected_output,
                    "rejected": rec.original_output,
                    "source": "user_edit",
                })
        return pairs


# =============================================================================
# --- Section 5: Output Validation (Structured Output Checks) ----------------
# =============================================================================
# Structured outputs from LLMs must be validated BEFORE use.
# Common failure: model returns valid JSON that violates business constraints.

@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


def validate_structured_output(
    output: dict,
    *,
    required_fields: list[str],
    field_types: dict[str, type] | None = None,
    custom_checks: list[Callable[[dict], str | None]] | None = None,
) -> ValidationResult:
    """Validate LLM-generated structured output against a schema.

    Three levels of validation:
      1. Schema: required fields present, correct types
      2. Semantic: values make sense (e.g., price > 0, date in future)
      3. Business: domain-specific rules (e.g., refund <= original amount)
    """
    errors: list[str] = []

    # Level 1: Required fields
    for f in required_fields:
        if f not in output:
            errors.append(f"missing required field: {f}")

    # Level 1: Type checks
    if field_types:
        for fname, ftype in field_types.items():
            if fname in output and not isinstance(output[fname], ftype):
                errors.append(
                    f"field '{fname}' expected {ftype.__name__}, "
                    f"got {type(output[fname]).__name__}"
                )

    # Level 2+3: Custom business rules
    if custom_checks:
        for check in custom_checks:
            error = check(output)
            if error:
                errors.append(error)

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_and_retry(
    generate_fn: Callable[[], dict],
    *,
    required_fields: list[str],
    field_types: dict[str, type] | None = None,
    custom_checks: list[Callable[[dict], str | None]] | None = None,
    max_retries: int = 3,
) -> tuple[dict | None, list[ValidationResult]]:
    """Generate structured output, validate, and retry on failure.

    This is the structured-output variant of the self-correction loop.
    On each failure, the validation errors are fed back as context for
    the next generation attempt.
    """
    results: list[ValidationResult] = []

    for attempt in range(max_retries):
        output = generate_fn()
        result = validate_structured_output(
            output,
            required_fields=required_fields,
            field_types=field_types,
            custom_checks=custom_checks,
        )
        results.append(result)

        if result.valid:
            return output, results

        # In production: feed result.errors back into the prompt for retry
        # e.g., "Previous output had errors: {result.errors}. Fix and retry."

    return None, results


# =============================================================================
# --- Section 6: Feedback-Driven Prompt Refinement ---------------------------
# =============================================================================
# Using collected feedback to improve prompts over time.
# This is the runtime equivalent of post-training alignment.

class PromptRefiner:
    """Refine prompts based on accumulated feedback patterns.

    This is NOT intrinsic self-correction (which drops accuracy).
    This uses external evidence (user edits, test results) to
    improve the system prompt across sessions.

    Production decision tree for post-training:
      Has verifiable reward? -> SFT + GRPO
      Unary signal only?     -> SFT + KTO
      Multiple objectives?   -> SFT + full RLHF (PPO)
      Default:               -> SFT + DPO
    """

    def __init__(self, base_prompt: str) -> None:
        self.base_prompt = base_prompt
        self.learned_rules: list[str] = []

    def learn_from_feedback(self, feedback_records: list[FeedbackRecord]) -> list[str]:
        """Extract patterns from feedback to add as prompt rules.

        In production, this would use an LLM to analyze feedback clusters
        and generate refined instructions.  Here we demonstrate the pattern.
        """
        new_rules: list[str] = []

        # Count failure types
        retries = sum(1 for r in feedback_records if r.feedback_type == "retry")
        edits = sum(1 for r in feedback_records if r.feedback_type == "edit")
        thumbs_down = sum(1 for r in feedback_records if r.feedback_type == "thumbs_down")

        total = len(feedback_records)
        if total == 0:
            return new_rules

        # If >30% retries, output format may be wrong
        if retries / total > 0.3:
            new_rules.append("Users frequently retry. Be more concise and direct.")

        # If edits show a consistent pattern, extract it
        edit_records = [r for r in feedback_records if r.feedback_type == "edit"]
        if len(edit_records) >= 3:
            new_rules.append(
                f"Users edited {len(edit_records)} outputs. "
                "Review common corrections and adjust style."
            )

        self.learned_rules.extend(new_rules)
        return new_rules

    def get_refined_prompt(self) -> str:
        """Compose the refined prompt with learned rules appended."""
        if not self.learned_rules:
            return self.base_prompt

        rules_section = "\n".join(f"- {r}" for r in self.learned_rules)
        return f"{self.base_prompt}\n\nLearned guidelines:\n{rules_section}"


# =============================================================================
# --- Section 7: Agent Loop with Budget Fuses --------------------------------
# =============================================================================
# The harness owns hop caps, NOT the model.  Key caps:
#   max_turns = 10 (OpenAI Agents SDK default)
#   max_replans = 2-3 (on state, not built-in)
#   same_action warn 3 / hard 5 (DeerFlow)
#   maxBudgetUsd (Claude -- no default, MUST set)

MAX_TURNS = 10
MAX_REPLANS = 2
SAME_ACTION_WARN = 3
SAME_ACTION_HARD = 5
MEMORY_CAP = 3


def action_hash(tool: str, args: str) -> str:
    """Hash a (tool, args) pair for same-action detection."""
    return hashlib.sha256(f"{tool}|{args}".encode()).hexdigest()[:16]


@dataclass
class LoopState:
    """State for the feedback loop harness."""
    goal: str
    allowlist: frozenset[str]
    plan: list[str] = field(default_factory=list)
    memory: ReflectionMemory = field(default_factory=ReflectionMemory)
    turns: int = 0
    replans: int = 0
    action_counts: dict[str, int] = field(default_factory=dict)
    status: str = "running"
    cost_usd: float = 0.0


class AgentLoop:
    """Production agent loop with four separated roles and budget fuses.

    Roles (never fused):
      Planner:   decompose objective into steps (structured output LLM)
      Executor:  run one ready node (tool runtime, sandboxed code)
      Critic:    verbalize why a trial failed (only on oracle fail)
      Verifier:  accept/reject (tests, compiler, DB predicate)

    The LLM is NOT the planner.  The planner is a function that emits
    a plan data structure.
    """

    def __init__(
        self,
        oracle: FakeOracle,
        llm: FakeLLM,
    ) -> None:
        self.oracle = oracle
        self.llm = llm

    def run(self, goal: str, allowlist: frozenset[str]) -> LoopState:
        state = LoopState(goal=goal, allowlist=allowlist, plan=["search", "analyze", "answer"])

        while state.status == "running":
            # --- Budget fuse: max_turns ---
            if state.turns >= MAX_TURNS:
                state.status = "refuse:max_turns"
                break

            state.turns += 1
            tool = state.plan[0] if state.plan else "search"

            # --- PEP: tool must be in allowlist ---
            if tool not in state.allowlist:
                state.status = "refuse:pep_deny"
                break

            # --- Same-action detection (DeerFlow pattern) ---
            key = action_hash(tool, f"goal={goal}")
            state.action_counts[key] = state.action_counts.get(key, 0) + 1
            if state.action_counts[key] >= SAME_ACTION_HARD:
                state.status = "refuse:same_action_hard"
                break
            if state.action_counts[key] >= SAME_ACTION_WARN:
                # Log warning but continue
                pass

            # --- Execute ---
            output = self.llm.generate(f"{tool}({goal})")

            # --- Verify (oracle first, always) ---
            passed, logs = self.oracle.check(output, state.turns)

            if passed:
                state.status = "pass"
                break

            # --- Critic (only on oracle fail) ---
            # No oracle, no critic -- Invariant I3
            reflection = self.llm.critique(output, logs)
            oracle_hash = hashlib.sha256(logs.encode()).hexdigest()[:16]
            state.memory.add(reflection, oracle_hash)

            # --- Replan ---
            state.replans += 1
            if state.replans > MAX_REPLANS:
                state.status = "refuse:max_replans"
                break

            # Rotate plan (simple strategy; production uses DAG replanning)
            if len(state.plan) > 1:
                state.plan = state.plan[1:] + state.plan[:1]

        return state


# =============================================================================
# --- Section 8: Circuit Breaker for Critic API ------------------------------
# =============================================================================
# Independent breakers needed for: critic API, tool fleet, same_action_k,
# max_replans, verifier disagreement.

class CriticCircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CriticCircuitBreaker:
    """Circuit breaker for the critic API.

    Fallback chain: oracle critic (Haiku + tool/logs) -> skip critic
    -> execute once -> deterministic refuse / HITL.
    Never: skip oracle and keep the critic.
    Never: verifier fail -> 'looks good, ship.'
    """
    threshold: int = 5
    cooldown_s: float = 15.0
    _state: CriticCircuitState = CriticCircuitState.CLOSED
    _failures: int = 0
    _opened_at: float = 0.0

    def allow(self) -> bool:
        if self._state is CriticCircuitState.CLOSED:
            return True
        if self._state is CriticCircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.cooldown_s:
                self._state = CriticCircuitState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN: one probe

    def record(self, success: bool) -> None:
        if success:
            self._failures = 0
            self._state = CriticCircuitState.CLOSED
        else:
            self._failures += 1
            if self._failures >= self.threshold:
                self._state = CriticCircuitState.OPEN
                self._opened_at = time.monotonic()


# =============================================================================
# --- Demo / Self-Test -------------------------------------------------------
# =============================================================================

if __name__ == "__main__":
    llm = FakeLLM()
    oracle = FakeOracle(pass_on_attempt=2)

    # 1. Self-correction loop
    attempts = self_correction_loop("write a sort function", llm=llm, oracle=oracle)
    assert attempts[-1].oracle_pass is True
    assert len(attempts) == 2  # fail on 1, pass on 2
    print(f"[Self-Correction] Passed on attempt {len(attempts)}, "
          f"reflections used: {sum(1 for a in attempts if a.reflection)}")

    # 2. Reflection memory
    memory = ReflectionMemory(max_size=3)
    for i in range(5):
        memory.add(f"reflection_{i}", f"hash_{i}")
    assert len(memory.hints) == 3  # capped at 3
    assert memory.hints[0]["text"] == "reflection_2"  # oldest kept
    print(f"[Memory] {len(memory.hints)} reflections (capped at {memory.max_size})")

    # 3. Self-Refine loop
    refiner = SelfRefineLoop(llm, max_k=3)
    result = refiner.run("write a product description")
    print(f"[Self-Refine] Iterations: {result['iterations']}")

    # 4. Tool error retry
    executor = ToolExecutor(fail_first_n=2)
    tool_result = executor.execute_with_retry("crm_lookup", {"id": 42})
    assert tool_result["result"] == "crm_lookup_ok"
    assert executor.call_count == 3  # failed 2, succeeded on 3
    print(f"[Retry] Tool succeeded after {executor.call_count} attempts")

    # 5. Human feedback collection and DPO pairs
    collector = FeedbackCollector()
    collector.record_edit("req-1", "verbose answer here", "concise answer")
    collector.record_retry("req-2", "wrong format output")
    collector.record_thumbs("req-3", "good answer", up=True)
    pairs = collector.to_preference_pairs()
    assert len(pairs) == 1  # only edits become preference pairs
    assert pairs[0]["chosen"] == "concise answer"
    print(f"[Feedback] {len(collector.records)} records, "
          f"{len(pairs)} DPO preference pairs")

    # 6. Structured output validation
    good_output = {"action": "refund", "amount": 25.0, "currency": "USD"}
    bad_output = {"action": "refund", "amount": -5.0}

    def check_positive_amount(o: dict) -> str | None:
        if o.get("amount", 0) <= 0:
            return "amount must be positive"
        return None

    good_result = validate_structured_output(
        good_output,
        required_fields=["action", "amount", "currency"],
        field_types={"amount": (int, float)},  # type: ignore
        custom_checks=[check_positive_amount],
    )
    assert good_result.valid

    bad_result = validate_structured_output(
        bad_output,
        required_fields=["action", "amount", "currency"],
        custom_checks=[check_positive_amount],
    )
    assert not bad_result.valid
    assert len(bad_result.errors) == 2  # missing currency + negative amount
    print(f"[Validation] Good: {good_result.valid}, Bad errors: {bad_result.errors}")

    # 7. Prompt refinement from feedback
    refiner_prompt = PromptRefiner("You are a helpful assistant.")
    collector2 = FeedbackCollector()
    for _ in range(5):
        collector2.record_retry(f"req-{_}", "bad output")
    for _ in range(3):
        collector2.record_edit(f"req-e{_}", "original", "edited")
    new_rules = refiner_prompt.learn_from_feedback(collector2.records)
    refined = refiner_prompt.get_refined_prompt()
    assert "Learned guidelines" in refined
    print(f"[Prompt Refine] Added {len(new_rules)} rules")

    # 8. Full agent loop with budget fuses
    loop = AgentLoop(oracle=FakeOracle(pass_on_attempt=2), llm=llm)
    state = loop.run("resolve ticket T-42", frozenset({"search", "analyze", "answer"}))
    assert state.status == "pass"
    assert state.turns == 2
    print(f"[Agent Loop] Status: {state.status}, turns: {state.turns}, "
          f"replans: {state.replans}")

    # Test refuse paths
    never_pass = AgentLoop(oracle=FakeOracle(pass_on_attempt=99), llm=llm)
    refused = never_pass.run("impossible task", frozenset({"search", "analyze", "answer"}))
    assert refused.status.startswith("refuse:")
    print(f"[Agent Loop] Refuse path: {refused.status}")

    # 9. Critic circuit breaker
    breaker = CriticCircuitBreaker(threshold=3)
    for _ in range(3):
        breaker.record(success=False)
    assert not breaker.allow()  # circuit is open
    print(f"[Circuit Breaker] State after 3 failures: {breaker._state.value}")

    print("\nAll checks passed.")
