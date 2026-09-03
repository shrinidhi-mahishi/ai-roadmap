"""
Agent Evaluation: measuring whether agents work and still work after changes.
Covers LLM-as-judge, pairwise comparison, trajectory scoring, tool call
accuracy, and end-to-end eval pipelines.
"""

from __future__ import annotations
import math, json, random
from dataclasses import dataclass, field
from typing import Any


# ═══ Section 1: LLM-as-Judge (Structured Rubric Evaluation) ═══

@dataclass
class RubricCriterion:
    """One criterion in a structured rubric. Modeled after HealthBench:
    48,562 criteria, median 11/example. Itemized + weighted beats a 1-5 vibe."""
    name: str
    description: str
    weight: float = 1.0
    positive_anchor: str = ""  # what a good answer does
    negative_anchor: str = ""  # what a bad answer does


def llm_as_judge(answer: str, rubric: list[RubricCriterion]) -> dict:
    """Score an answer using a structured rubric. In production, calls an LLM.
    Biases: position ~10-15pt, verbosity 15-30pt, self-enhancement 10-25%.
    Mitigate: swap order, length-normalize, cross-family judge, expert calibration."""
    scores = {}
    total_w, weighted_sum = 0.0, 0.0
    for c in rubric:
        # Simulated scoring (real: per-criterion LLM evaluation)
        score = random.uniform(0.6, 1.0) if c.name.lower() in answer.lower() or len(answer) > 50 \
            else random.uniform(0.1, 0.5)
        scores[c.name] = round(score, 2)
        weighted_sum += score * c.weight
        total_w += c.weight
    overall = round(weighted_sum / total_w, 3) if total_w else 0.0
    return {"overall_score": overall, "per_criterion": scores,
            "pass": overall >= 0.6}


def demo_judge():
    rubric = [RubricCriterion("accuracy", "Facts correct?", 2.0),
              RubricCriterion("completeness", "Covers all?", 1.5),
              RubricCriterion("clarity", "Clear?", 1.0),
              RubricCriterion("citations", "Sources?", 1.5)]
    answer = "Python was created by Guido van Rossum in 1991. [Source: Wikipedia]"
    result = llm_as_judge(answer, rubric)
    print(f"  Overall: {result['overall_score']:.1%} ({'PASS' if result['pass'] else 'FAIL'})")
    for name, score in result["per_criterion"].items():
        print(f"    {name}: {score:.0%}")


# ═══ Section 2: Pairwise Comparison ═══

def pairwise_compare(output_a: str, output_b: str, question: str) -> dict:
    """Compare two outputs. Must swap order to detect position bias (~10-15pt).
    GPT-4 judge vs humans: >80% agreement (MT-Bench)."""
    # Round 1: A first
    r1 = "A" if "source" in output_a.lower() else "B"
    # Round 2: swap order -- if judge picks differently, it is position bias
    r2_raw = r1  # simplified; real: re-run LLM with swapped order
    r2 = {"A": "B", "B": "A", "TIE": "TIE"}[r2_raw]
    if r1 != r2:
        return {"winner": "TIE (position bias)", "confident": False}
    return {"winner": r1, "confident": True}


def demo_pairwise():
    a = "Python is a language by Guido van Rossum. [Source: wiki]"
    b = "Python is a general-purpose language known for readability."
    result = pairwise_compare(a, b, "What is Python?")
    print(f"  Winner: {result['winner']} (confident: {result['confident']})")


# ═══ Section 3: Trajectory Evaluation ═══

@dataclass
class TrajectoryStep:
    """One step in an agent's trajectory. Trajectory eval asks 'were the steps
    legal and efficient?' while outcome eval asks 'did the world reach goal state?'"""
    step_id: int
    action: str
    args: dict
    was_successful: bool
    policy_violation: bool = False


def evaluate_trajectory(steps: list[TrajectoryStep], reference_steps: int = 0) -> dict:
    """Score trajectory quality. MAST taxonomy: step repetition is the #1 failure
    mode at 17.14%. Metrics: efficiency, backtrack rate, policy violations."""
    total = len(steps)
    violations = sum(1 for s in steps if s.policy_violation)
    # Detect backtracking (repeated action+args = agent stuck in a loop)
    seen, backtracks = set(), 0
    for s in steps:
        key = (s.action, json.dumps(s.args, sort_keys=True))
        if key in seen:
            backtracks += 1
        seen.add(key)
    efficiency = min(reference_steps / total, 1.0) if reference_steps and total else 0.0
    bt_rate = round(backtracks / total, 2) if total else 0.0
    return {"total_steps": total, "policy_violations": violations,
            "backtrack_rate": bt_rate, "path_efficiency": round(efficiency, 2),
            "grade": "GOOD" if violations == 0 and bt_rate < 0.2 else "NEEDS_REVIEW"}


def demo_trajectory():
    steps = [
        TrajectoryStep(1, "search", {"q": "Python history"}, True),
        TrajectoryStep(2, "read_page", {"url": "wiki.org"}, True),
        TrajectoryStep(3, "search", {"q": "Python history"}, True),  # backtrack!
        TrajectoryStep(4, "extract", {"sel": "p"}, True),
        TrajectoryStep(5, "synthesize", {}, True),
    ]
    r = evaluate_trajectory(steps, reference_steps=4)
    print(f"  Steps: {r['total_steps']}, Efficiency: {r['path_efficiency']:.0%}, "
          f"Backtracks: {r['backtrack_rate']:.0%}, Grade: {r['grade']}")


# ═══ Section 4: Tool Call Accuracy Metric ═══

def validate_tool_call(predicted: dict, expected: dict, schema: dict) -> dict:
    """BFCL-style AST matching. Do NOT use BLEU on JSON or LLM-judge parameters
    when a JSON schema exists. Hallucinated tool = abstention failure."""
    pred_args = set(predicted.get("args", {}).keys())
    exp_args = set(expected.get("args", {}).keys())
    required = set(schema.get("required", []))
    matches = {a: predicted["args"][a] == expected["args"][a]
               for a in pred_args & exp_args}
    return {
        "name_match": predicted.get("name") == expected.get("name"),
        "arg_matches": matches,
        "hallucinated": sorted(pred_args - exp_args),
        "missing_required": sorted(required - pred_args),
        "overall": (predicted.get("name") == expected.get("name")
                    and not (pred_args - exp_args) and not (required - pred_args)
                    and all(matches.values())),
    }


def compute_tool_f1(predicted: list[dict], gold: list[dict]) -> dict:
    """Enterprise Tool F1 over a sequence of calls."""
    p_set = {(c["name"], json.dumps(c["args"], sort_keys=True)) for c in predicted}
    g_set = {(c["name"], json.dumps(c["args"], sort_keys=True)) for c in gold}
    correct = p_set & g_set
    prec = len(correct) / len(p_set) if p_set else 0.0
    rec = len(correct) / len(g_set) if g_set else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {"precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3)}


def demo_tool_accuracy():
    schema = {"required": ["city", "units"]}
    pred = {"name": "get_weather", "args": {"city": "Tokyo", "units": "celsius"}}
    exp = {"name": "get_weather", "args": {"city": "Tokyo", "units": "celsius"}}
    r = validate_tool_call(pred, exp, schema)
    print(f"  Single call: {'PASS' if r['overall'] else 'FAIL'} (name={r['name_match']}, args={r['arg_matches']})")

    f1 = compute_tool_f1(
        [{"name": "search", "args": {"q": "AI"}}, {"name": "email", "args": {"to": "x"}}],
        [{"name": "search", "args": {"q": "AI"}}, {"name": "read", "args": {"u": "y"}}],
    )
    print(f"  Sequence F1: P={f1['precision']}, R={f1['recall']}, F1={f1['f1']}")


# ═══ Section 5: Eval Pipeline (Dataset -> Run -> Score -> Aggregate -> Report) ═══

def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k (Chen et al., 2021). P(at least 1 of k succeeds).
    Naive 1-(1-c/n)^k is BIASED -- use combinatorial form."""
    if n - c < k: return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def pass_power_k(results: dict[str, list[bool]], k: int) -> float:
    """pass^k: P(ALL k trials succeed), averaged over tasks.
    The RELIABILITY metric (tau-bench). Gap vs pass@k = product risk."""
    probs = []
    for outcomes in results.values():
        n, c = len(outcomes), sum(outcomes)
        if n >= k:
            probs.append(math.comb(c, k) / math.comb(n, k) if c >= k else 0.0)
    return sum(probs) / len(probs) if probs else 0.0


def dual_oracle_gate(hard_pass: bool, soft_score: float, threshold: float = 0.6) -> bool:
    """Ship only if BOTH gates pass. Hard-only ships 'correct but hostile'.
    Soft-only ships 'pretty wrong'."""
    return hard_pass and soft_score >= threshold


def run_eval_pipeline(dataset: list[dict], agent_fn, scorers: list) -> dict:
    """Full pipeline: dataset -> run agent -> score -> dual oracle -> aggregate."""
    details = []
    for item in dataset:
        output = agent_fn(item["input"])
        scores = {s["name"]: s["fn"](output, item.get("expected", "")) for s in scorers}
        passed = dual_oracle_gate(scores.get("exact", 0) > 0.5, scores.get("quality", 0.5))
        details.append({"id": item["id"], "passed": passed, **scores})
    total, wins = len(details), sum(d["passed"] for d in details)
    return {"total": total, "passed": wins, "rate": round(wins / total, 3) if total else 0,
            "pass_at_1": round(pass_at_k(total, wins, 1), 3), "details": details}


def demo_pipeline():
    dataset = [{"id": "t1", "input": "2+2?", "expected": "4"},
               {"id": "t2", "input": "Capital of France?", "expected": "Paris"},
               {"id": "t3", "input": "Largest planet?", "expected": "Jupiter"}]
    answers = {"2+2?": "4", "Capital of France?": "Paris", "Largest planet?": "Saturn"}
    scorers = [{"name": "exact", "fn": lambda o, e: 1.0 if o == e else 0.0},
               {"name": "quality", "fn": lambda o, e: 0.8 if o else 0.0}]
    r = run_eval_pipeline(dataset, lambda q: answers.get(q, "?"), scorers)
    print(f"  Passed {r['passed']}/{r['total']} ({r['rate']:.0%}), pass@1={r['pass_at_1']}")
    for d in r["details"]:
        print(f"    {d['id']}: {'PASS' if d['passed'] else 'FAIL'} (exact={d['exact']})")


# ═══ Main ═══

if __name__ == "__main__":
    print("=" * 60)
    print("AGENT EVALUATION -- Interview Prep Demos")
    print("=" * 60)

    print("\n--- 1. LLM-as-Judge (Structured Rubric) ---")
    demo_judge()

    print("\n--- 2. Pairwise Comparison ---")
    demo_pairwise()

    print("\n--- 3. Trajectory Evaluation ---")
    demo_trajectory()

    print("\n--- 4. Tool Call Accuracy ---")
    demo_tool_accuracy()

    print("\n--- 5. Eval Pipeline (Dataset -> Score -> Report) ---")
    demo_pipeline()

    print("\n" + "=" * 60)
    print("Key takeaways:")
    print("  - Dual oracle: hard gate (tests/DB) + soft score (rubric)")
    print("  - pass@k = capability; pass^k = reliability; gap = risk")
    print("  - Judge biases: position 10-15pt, verbosity 15-30pt")
    print("  - Tool accuracy: AST match, not BLEU on JSON")
    print("  - EVAL = (model x scaffold x tools x oracle x sampling)")
