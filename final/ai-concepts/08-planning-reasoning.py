"""
Planning and Reasoning: Complex tasks require decomposition, verification, and adaptation.
This file covers Chain-of-Thought prompting, Plan-and-Execute, Reflection/self-critique,
Tree-of-Thought search, and Replanning on failure -- the core patterns that separate
production agents from toy demos.
"""

from dataclasses import dataclass, field


# ═══ Chain-of-Thought Prompting ═══
# Adding "Let's think step by step" dramatically improves multi-step reasoning.
# PS+ (Plan-and-Solve+) extends this: first devise a plan, then carry it out.

def mock_llm(prompt: str) -> str:
    """Simulates an LLM response. In production, call Claude/GPT API."""
    # Simulate how CoT changes output quality
    if "step by step" in prompt.lower() or "devise a plan" in prompt.lower():
        return (
            "Step 1: The store has 5 apples.\n"
            "Step 2: 3 are sold, leaving 5 - 3 = 2.\n"
            "Step 3: 6 more arrive, so 2 + 6 = 8.\n"
            "Answer: 8 apples."
        )
    # Without CoT, models often jump to wrong answers on multi-step problems
    return "Answer: 11 apples."  # wrong -- added without subtracting


def demo_chain_of_thought():
    question = "A store has 5 apples. 3 are sold. 6 more arrive. How many apples?"

    # Direct prompting -- model jumps to answer, often wrong
    direct = mock_llm(f"Q: {question}\nA:")
    print(f"  Direct: {direct}")

    # CoT prompting -- "Let's think step by step" triggers structured reasoning
    cot = mock_llm(f"Q: {question}\nA: Let's think step by step.")
    print(f"  CoT: {cot}")

    # PS+ extends CoT: "devise a plan, extract variables, calculate intermediates"
    ps_plus = mock_llm(
        f"Q: {question}\nA: Let's first devise a plan to solve this, "
        f"extract the key numbers, and calculate step by step."
    )
    print(f"  PS+: {ps_plus}")


# ═══ Plan-and-Execute Pattern ═══
# Separate planning from execution: planner emits a structured plan upfront,
# executor runs each step, replanner adjusts. Amortizes expensive planning calls.

@dataclass
class PlanStep:
    task: str
    tool: str
    status: str = "pending"  # pending | done | failed
    result: str = ""


def mock_planner(objective: str) -> list[PlanStep]:
    """LLM generates a structured plan. Real: use structured output (Pydantic)."""
    return [
        PlanStep(task="Search for relevant documents", tool="retriever"),
        PlanStep(task="Analyze search results for key facts", tool="analyzer"),
        PlanStep(task="Generate answer from analyzed facts", tool="generator"),
    ]


def mock_execute(step: PlanStep) -> str:
    """Execute a single plan step using the specified tool."""
    tools = {
        "retriever": lambda: "Found 3 relevant documents about RAG pipelines",
        "analyzer": lambda: "Key facts: hybrid search improves recall by 49%",
        "generator": lambda: "RAG combines retrieval with generation for grounded answers",
    }
    executor = tools.get(step.tool, lambda: "Unknown tool")
    return executor()


def plan_and_execute(objective: str, max_replans: int = 2) -> str:
    """Plan-and-Execute: make a plan, run steps, replan if needed.
    This is the LangGraph canonical production pattern."""
    plan = mock_planner(objective)
    past_steps: list[tuple[str, str]] = []
    replan_count = 0

    for step in plan:
        result = mock_execute(step)
        step.result = result
        step.status = "done"
        past_steps.append((step.task, result))

    # Check if we need to replan (all steps done but answer may be insufficient)
    final_result = past_steps[-1][1] if past_steps else "No result"
    return final_result


def demo_plan_and_execute():
    result = plan_and_execute("How does hybrid search improve RAG retrieval?")
    print(f"  Final answer: {result}")


# ═══ Reflection / Self-Critique Loop ═══
# Generate -> Critique -> Revise. The critic needs an ORACLE (tests, compiler)
# to be reliable. Without one, reflection can hurt performance (Reflexion ablation).

@dataclass
class ReflectionMemory:
    """Episodic hints from past failures. Capped to prevent prompt overflow."""
    hints: list[str] = field(default_factory=list)
    max_hints: int = 3

    def add(self, hint: str):
        self.hints.append(hint)
        if len(self.hints) > self.max_hints:
            self.hints = self.hints[-self.max_hints:]


def mock_generate_code(problem: str, reflections: list[str]) -> str:
    """Simulate code generation, improving with reflections."""
    if any("edge case" in r for r in reflections):
        return "def solve(n): return n if n >= 0 else 0  # handles negatives"
    if any("off-by-one" in r for r in reflections):
        return "def solve(n): return n + 1  # fixed boundary"
    return "def solve(n): return n"  # initial attempt -- may be wrong


def mock_run_tests(code: str) -> tuple[bool, str]:
    """Oracle verification: run tests, return pass/fail + output."""
    if "n >= 0" in code:
        return True, "All 5 tests passed"
    if "n + 1" in code:
        return False, "FAIL: solve(-1) expected 0, got 0 but solve(5) expected 5, got 6"
    return False, "FAIL: solve(-1) expected 0, got -1"


def reflexion_loop(problem: str, max_trials: int = 4) -> tuple[str, int]:
    """Reflexion: generate -> test (oracle) -> reflect -> retry.
    Key insight: the critic uses TEST OUTPUT, not self-eval."""
    memory = ReflectionMemory()

    for trial in range(1, max_trials + 1):
        code = mock_generate_code(problem, memory.hints)
        passed, output = mock_run_tests(code)

        if passed:
            return code, trial

        # Reflect using oracle feedback -- not LLM self-judgment
        if "-1" in output:
            memory.add("edge case: handle negative inputs by returning 0")
        elif "expected 5, got 6" in output:
            memory.add("off-by-one: don't add 1 to the input")

    return code, max_trials


def demo_reflection():
    code, trials = reflexion_loop("Write solve(n) that returns n for n>=0, else 0")
    print(f"  Solved in {trials} trial(s)")
    print(f"  Code: {code}")


# ═══ Tree-of-Thought (BFS Exploration) ═══
# Branch: generate multiple candidate next-steps.
# Evaluate: score each candidate (LLM or oracle).
# Prune: keep only the top-b candidates (beam width).
# ToT turns 4% (CoT) into 74% (ToT b=5) on Game of 24.

@dataclass
class ThoughtNode:
    state: str  # current partial solution
    score: float = 0.0
    children: list["ThoughtNode"] = field(default_factory=list)


def generate_thoughts(state: str, n: int = 3) -> list[str]:
    """Branch: generate n candidate next-steps from current state."""
    # Simulate branching for "arrange 4 numbers to make 24"
    depth = state.count("->")  # track depth for varied suggestions
    ops = [
        ("multiply 5*5=25", 0.7),
        ("add 1+5=6", 0.5),
        ("subtract 25-1=24", 0.9),  # winning path at depth 2
    ]
    return [f"{state} -> {ops[i % len(ops)][0]}" for i in range(depth, depth + n)]


def evaluate_thought(thought: str) -> float:
    """Score a candidate thought. Real: LLM self-eval or domain checker.
    Returns 0.0-1.0 where higher = more promising."""
    if "24" in thought:
        return 0.95  # found the answer
    if "multiply" in thought:
        return 0.7
    if "add" in thought:
        return 0.5
    return 0.3


def tree_of_thought_bfs(initial: str, depth: int = 3, beam_width: int = 2) -> list[str]:
    """BFS Tree-of-Thought: at each level, branch, evaluate, and prune.
    beam_width controls exploration breadth vs cost."""
    current_states = [initial]
    all_paths = []

    for level in range(depth):
        candidates = []
        for state in current_states:
            thoughts = generate_thoughts(state, n=3)
            for t in thoughts:
                score = evaluate_thought(t)
                candidates.append((t, score))

        # Prune: keep only top beam_width candidates
        candidates.sort(key=lambda x: x[1], reverse=True)
        current_states = [t for t, _ in candidates[:beam_width]]
        all_paths.append(current_states)

    return current_states  # best paths after full exploration


def demo_tree_of_thought():
    best = tree_of_thought_bfs("Game of 24: [1, 5, 5, 5]", depth=3, beam_width=2)
    print("  Best paths after BFS exploration:")
    for path in best:
        print(f"    {path[:80]}")


# ═══ Replanning on Failure ═══
# Detect failure (tool error, verifier fail, empty search), adjust plan, retry.
# MUST set max_replans -- the framework will not do it for you.

@dataclass
class ExecutionResult:
    success: bool
    output: str
    error: str = ""


def execute_with_failure(step: str, attempt: int) -> ExecutionResult:
    """Simulate tool execution that may fail on first attempt."""
    if "database" in step.lower() and attempt == 1:
        return ExecutionResult(success=False, output="", error="ConnectionTimeout")
    if "api" in step.lower() and attempt == 1:
        return ExecutionResult(success=False, output="", error="RateLimited")
    return ExecutionResult(success=True, output=f"Completed: {step}")


def replan_on_failure(steps: list[str], max_replans: int = 3) -> list[dict]:
    """Execute steps with failure detection and replanning.
    Circuit breakers: max_replans, same_action_k detection."""
    results = []
    replan_count = 0
    same_action_count: dict[str, int] = {}

    i = 0
    while i < len(steps):
        step = steps[i]

        # Circuit breaker: same action repeated too many times
        same_action_count[step] = same_action_count.get(step, 0) + 1
        if same_action_count[step] > 2:
            results.append({"step": step, "status": "skipped", "reason": "same_action_limit"})
            i += 1
            continue

        result = execute_with_failure(step, same_action_count[step])

        if result.success:
            results.append({"step": step, "status": "done", "output": result.output})
            i += 1
        else:
            # Replan: adjust the failed step
            replan_count += 1
            if replan_count > max_replans:
                results.append({"step": step, "status": "failed",
                                "reason": f"max_replans ({max_replans}) exhausted"})
                i += 1
                continue

            # Modify the approach -- e.g., retry with different params or fallback tool
            adjusted = f"{step} (retry with backoff)"
            results.append({"step": step, "status": "replanned",
                            "error": result.error, "new_step": adjusted})
            steps[i] = adjusted  # replace current step with adjusted version

    return results


def demo_replanning():
    steps = [
        "Query database for user records",
        "Call external API for enrichment",
        "Generate summary report",
    ]
    results = replan_on_failure(steps, max_replans=3)
    for r in results:
        status = r["status"]
        step = r["step"][:50]
        extra = r.get("error", r.get("output", r.get("reason", "")))
        print(f"  [{status:10s}] {step} | {extra}")


# ═══ Run All Demos ═══

if __name__ == "__main__":
    print("=" * 60)
    print("1. Chain-of-Thought Prompting")
    print("=" * 60)
    demo_chain_of_thought()

    print("\n" + "=" * 60)
    print("2. Plan-and-Execute Pattern")
    print("=" * 60)
    demo_plan_and_execute()

    print("\n" + "=" * 60)
    print("3. Reflection / Self-Critique Loop (Reflexion)")
    print("=" * 60)
    demo_reflection()

    print("\n" + "=" * 60)
    print("4. Tree-of-Thought (BFS Exploration)")
    print("=" * 60)
    demo_tree_of_thought()

    print("\n" + "=" * 60)
    print("5. Replanning on Failure (with Circuit Breakers)")
    print("=" * 60)
    demo_replanning()
