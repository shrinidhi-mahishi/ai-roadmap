"""
Agent Architecture: ReAct Loops, State Machines, Routers, and Budget Guards.

An agent is an LLM in a loop: think -> act -> observe -> repeat. The architecture
determines cost, latency, and failure modes. 88% of agent failures trace to
infrastructure gaps, not model quality (Arize, 2026).
"""

import time
import random
from dataclasses import dataclass, field
from enum import Enum


# ======================================================================
# 1. ReAct Loop (Reason + Act + Observe)
# ======================================================================

def mock_llm_reason(context):
    """Simulate LLM reasoning. Grounding via tools kills hallucination (0% vs
    CoT's 56%) but creates repetitive loops (47% of failures). Add a fuse.
    """
    step = context.get("step", 0)
    query = context.get("query", "")
    if step == 0:
        return {"thought": f"I need to search for: {query}",
                "action": {"tool": "search", "args": {"q": query}}}
    elif step == 1:
        return {"thought": "Let me verify with a calculation.",
                "action": {"tool": "calculate", "args": {"expr": "68 * 1.03"}}}
    return {"thought": "I have enough information.", "action": None,
            "answer": f"Answer to '{query}': approximately 70M."}


def execute_tool(name, args):
    tools = {
        "search": lambda q: f"Found: population is ~68 million",
        "calculate": lambda expr: f"{eval(expr):.2f}",
    }
    return tools.get(name, lambda **_: "Error: unknown tool")(**args)


def react_loop(query, max_turns=10):
    """The canonical ReAct loop -- every framework implements this."""
    ctx = {"query": query, "step": 0, "obs": []}
    print(f"  Query: {query}")
    for turn in range(max_turns):
        result = mock_llm_reason(ctx)
        print(f"  Turn {turn+1}: {result['thought']}")
        if result["action"] is None:
            print(f"  Answer: {result['answer']}")
            return result["answer"]
        a = result["action"]
        obs = execute_tool(a["tool"], a["args"])
        print(f"    {a['tool']}({a['args']}) -> {obs}")
        ctx["obs"].append(obs)
        ctx["step"] += 1
    print("  WARNING: max_turns hit -- fuse triggered")
    return None


# ======================================================================
# 2. State Machine Agent (PLAN -> EXECUTE -> EVALUATE -> DONE)
# ======================================================================

class Phase(Enum):
    PLAN = "PLAN"
    EXECUTE = "EXECUTE"
    EVALUATE = "EVALUATE"
    DONE = "DONE"


@dataclass
class StateMachineAgent:
    """Explicit state transitions -- predictable, auditable, checkpointable.

    CLEAR Framework: Plan-Execute costs $1.24/task vs $5.12 for Reflexion.
    """
    task: str
    phase: Phase = Phase.PLAN
    plan: list = field(default_factory=list)
    results: list = field(default_factory=list)
    step_idx: int = 0

    def run(self, max_iter=10):
        for i in range(max_iter):
            if self.phase == Phase.PLAN:
                self.plan = ["Fetch price", "Get earnings", "Calc PE"]
                print(f"  [PLAN] {self.plan}")
                self.phase = Phase.EXECUTE
            elif self.phase == Phase.EXECUTE:
                if self.step_idx >= len(self.plan):
                    self.phase = Phase.EVALUATE
                else:
                    self.results.append(f"data_{self.step_idx+1}")
                    print(f"  [EXEC] {self.plan[self.step_idx]} -> {self.results[-1]}")
                    self.step_idx += 1
            elif self.phase == Phase.EVALUATE:
                print(f"  [EVAL] OK ({len(self.results)} results)")
                self.phase = Phase.DONE
            elif self.phase == Phase.DONE:
                return self.results
        return self.results


# ======================================================================
# 3. Router Pattern (Classify -> Route to Specialist)
# ======================================================================

@dataclass
class Router:
    """Classify input -> route to specialist. Production: Haiku for triage,
    specialists with scoped tools (refund agent never gets delete_account).
    """
    handlers: dict = field(default_factory=dict)

    def register(self, intent, handler):
        self.handlers[intent] = handler

    def classify(self, msg):
        m = msg.lower()
        for intent, keywords in [("refund", ["refund", "return"]),
                                 ("billing", ["bill", "charge", "invoice"]),
                                 ("technical", ["bug", "error", "crash"])]:
            if any(w in m for w in keywords):
                return intent
        return "general"

    def route(self, msg):
        intent = self.classify(msg)
        handler = self.handlers.get(intent, self.handlers.get("general"))
        return intent, handler(msg) if handler else "No handler"


# ======================================================================
# 4. Checkpoint and Resume
# ======================================================================

@dataclass
class CheckpointAgent:
    """Save/restore agent state. Use Postgres or Temporal in prod (not MemorySaver)."""
    state: dict = field(default_factory=dict)
    _saved: list = field(default_factory=list)

    def save(self):
        self._saved.append(dict(self.state))

    def restore(self):
        if self._saved:
            self.state = dict(self._saved[-1])

    def run_steps(self, steps):
        self.state = {"step": 0, "results": []}
        for i, step in enumerate(steps):
            self.state["step"] = i
            self.state["results"].append(f"done: {step}")
            print(f"    Step {i+1}: {step}")
            self.save()
            if i == 2 and len(steps) > 3:
                print("    CRASH -> restoring last checkpoint...")
                self.restore()
                break
        return self.state["results"]


# ======================================================================
# 5. Max Iterations + Budget Guard
# ======================================================================

@dataclass
class BudgetGuard:
    """Prevent runaway loops. Without guards, agents can burn $47K/week."""
    max_turns: int = 10
    max_tokens: int = 100_000
    max_cost: float = 1.00
    turns: int = 0
    tokens: int = 0
    cost: float = 0.0
    _states: list = field(default_factory=list)

    def check(self, tokens_this_turn=500):
        """Returns (ok, reason). Enforce at orchestration layer, not prompt."""
        self.turns += 1
        self.tokens += tokens_this_turn
        self.cost += tokens_this_turn * 3.0 / 1_000_000
        if self.turns > self.max_turns:
            return False, f"Turns {self.turns}/{self.max_turns}"
        if self.tokens > self.max_tokens:
            return False, f"Tokens {self.tokens:,}/{self.max_tokens:,}"
        if self.cost > self.max_cost:
            return False, f"Cost ${self.cost:.4f}/${self.max_cost}"
        return True, "OK"

    def detect_loop(self, state_hash):
        self._states.append(state_hash)
        if len(self._states) > 5:
            self._states.pop(0)
        return len(self._states) >= 3 and len(set(self._states[-3:])) == 1


# ======================================================================
# Main: Demo All Snippets
# ======================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("1. ReAct LOOP")
    print("=" * 60)
    react_loop("What is the population of France?")
    print()

    print("=" * 60)
    print("2. STATE MACHINE AGENT")
    print("=" * 60)
    StateMachineAgent("Calculate AAPL forward PE").run()

    print("=" * 60)
    print("3. ROUTER PATTERN")
    print("=" * 60)
    router = Router()
    router.register("refund", lambda m: "Processing your refund in 3-5 days.")
    router.register("billing", lambda m: "Pulling up your latest invoice.")
    router.register("technical", lambda m: "Investigating the error.")
    router.register("general", lambda m: "How can I help?")
    for msg in ["I want a refund", "Strange charge on my bill",
                "App crashes on submit", "Tell me about products"]:
        intent, resp = router.route(msg)
        print(f"  '{msg}' -> [{intent}] {resp}")
    print()

    print("=" * 60)
    print("4. CHECKPOINT AND RESUME")
    print("=" * 60)
    agent = CheckpointAgent()
    results = agent.run_steps(["Fetch data", "Clean data", "Analyze", "Report", "Notify"])

    print("=" * 60)
    print("5. BUDGET GUARD")
    print("=" * 60)
    guard = BudgetGuard(max_turns=5, max_tokens=5000, max_cost=0.05)
    for i in range(8):
        ok, reason = guard.check(tokens_this_turn=800)
        loop = guard.detect_loop(f"s{i % 3}")
        if not ok:
            print(f"  Turn {i+1}: STOP -- {reason}"); break
        if loop:
            print(f"  Turn {i+1}: STOP -- loop detected"); break
        print(f"  Turn {i+1}: OK")
    print(f"  Final: {guard.turns} turns, {guard.tokens:,} tok, ${guard.cost:.4f}")
    print("  Key: SDK max_turns=10, LangGraph limit=25, 47% ReAct loops")
